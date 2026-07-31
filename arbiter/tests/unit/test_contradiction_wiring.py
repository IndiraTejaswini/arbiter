"""
The contradiction layers must actually receive input.

`arbiter.evidence.{numeric,identity,semantic,temporal}` were fully
implemented and unit-tested, and three of the four received input from
NOTHING in the production pipeline: `identity_key` and `claim_subject` were
written nowhere in the repository at all, `money_role` only by the
hand-built demo scenarios, and `temporal_value` was always None because the
seeder never populated `delivery_at`/`shipment_at`.

The consequence was not "a feature is missing" -- it was that
`graph.unresolved_severity()` returned None on every real case, so
`contradiction_clarity` was a constant 1.0 in the confidence vector, and
the case a human most needs to see looked identical to a clean one.

These tests assert on the WIRING, which is what was broken, rather than on
the detection algorithms, which were already correct and already tested.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from arbiter.evidence import EvidenceGraph
from arbiter.evidence.models import ProvenanceTier
from arbiter.ingest.extract_native import extract_native
from arbiter.ingest.route import _extraction_to_node, process_artifact
from arbiter.ingest.schemas import ExtractedField, ExtractionResult, SourceRef
from arbiter.network.loader import NetworkFacts, load_network_evidence
from datagen.documents import make_communication_pdf, make_delivery_confirmation_pdf

_BASE = datetime(2026, 3, 1, tzinfo=timezone.utc)


def _graph_with(facts: NetworkFacts, reason_code: str = "C08") -> EvidenceGraph:
    graph = EvidenceGraph("case-1")
    for node in load_network_evidence("case-1", reason_code, facts):
        graph.add_node(node)
    return graph


def test_loader_emits_numeric_reconciliation_inputs():
    graph = _graph_with(NetworkFacts(
        order_total_minor=8999, authorization_minor=8999, settlement_minor=8999, currency="USD",
    ))
    roles = {n.attrs.get("money_role") for n in graph.nodes.values()}
    assert {"order_total", "authorization", "settlement"} <= roles


def test_loader_emits_identity_coherence_inputs():
    graph = _graph_with(NetworkFacts(
        order_shipping_address="1 Cardmember Way", carrier_delivery_address="1 Cardmember Way",
    ))
    keys = {n.attrs.get("identity_key") for n in graph.nodes.values()}
    assert "shipping_address" in keys


def test_loader_emits_temporal_ordering_inputs():
    graph = _graph_with(NetworkFacts(
        shipment_at=_BASE, delivery_at=_BASE + timedelta(days=2),
    ))
    keys = {n.attrs.get("temporal_fact_key") for n in graph.nodes.values()}
    assert {"shipment", "delivery"} <= keys


def test_numeric_mismatch_is_detected_end_to_end():
    """An authorization that does not match the order total by more than
    tolerance is a real contradiction the confidence vector should see."""
    graph = _graph_with(NetworkFacts(
        order_total_minor=10_000, authorization_minor=50_000, currency="USD",
    ))
    contradictions = graph.run_contradiction_analysis()
    assert any(c.layer == "numeric" for c in contradictions)
    assert graph.unresolved_severity() in ("MEDIUM", "HIGH", "CRITICAL")


def test_identity_mismatch_is_detected_end_to_end():
    graph = _graph_with(NetworkFacts(
        order_shipping_address="1 Cardmember Way, New York",
        carrier_delivery_address="42 Elsewhere Ave, Newark",
    ))
    contradictions = graph.run_contradiction_analysis()
    assert any(c.layer == "identity" for c in contradictions)


def test_delivery_before_shipment_is_detected_end_to_end():
    """The architecture document's own worked example: a delivery recorded
    before the shipment that should precede it."""
    graph = _graph_with(NetworkFacts(
        shipment_at=_BASE, delivery_at=_BASE - timedelta(days=3),
    ))
    contradictions = graph.run_contradiction_analysis()
    assert any(c.layer == "temporal" for c in contradictions)
    assert graph.unresolved_severity() == "HIGH"


def test_clean_case_reports_no_contradiction():
    """The layers must not fire spuriously -- a false HIGH now hard-blocks
    auto-resolution, so a noisy detector would silently destroy the
    auto-resolution rate."""
    graph = _graph_with(NetworkFacts(
        order_total_minor=8999, authorization_minor=8999, settlement_minor=8999, currency="USD",
        order_shipping_address="1 Cardmember Way", carrier_delivery_address="1 Cardmember Way",
        shipment_at=_BASE, delivery_at=_BASE + timedelta(days=2),
        item_delivered=True, delivered_to_correct_address=True,
    ))
    assert graph.run_contradiction_analysis() == []
    assert graph.unresolved_severity() is None


def test_contradiction_inputs_carry_no_predicate():
    """These nodes must never be able to satisfy a rule. They shift
    confidence and can force escalation; they must not decide anything."""
    graph = _graph_with(NetworkFacts(
        order_total_minor=8999, currency="USD", order_shipping_address="x",
        shipment_at=_BASE,
    ))
    for node in graph.nodes.values():
        if any(k in node.attrs for k in ("money_role", "identity_key", "temporal_fact_key")):
            assert "asserts_predicate" not in node.attrs, (
                f"observation node {node.node_id} carries a predicate -- contradiction "
                f"inputs must never be able to satisfy a rule"
            )


def test_iso_string_timestamps_are_parsed_back_to_datetime():
    """`NetworkFacts(**seed.network_facts)` deserialises JSONB, so datetimes
    arrive as ISO strings. The temporal layer does interval arithmetic and
    calls .isoformat(); both raise on a str. The layer was previously
    protected from that only by never being populated."""
    facts = NetworkFacts(shipment_at="2026-03-01T00:00:00+00:00",
                         delivery_at="2026-03-03T00:00:00Z")
    assert isinstance(facts.shipment_at, datetime)
    assert isinstance(facts.delivery_at, datetime)
    assert facts.delivery_at > facts.shipment_at

    # And the full path must not raise.
    graph = _graph_with(facts)
    graph.run_contradiction_analysis()


def test_unparseable_timestamp_degrades_to_none_rather_than_raising():
    facts = NetworkFacts(shipment_at="not-a-date")
    assert facts.shipment_at is None


# -- Layer 4: semantic ------------------------------------------------------
#
# The fourth layer was the one this module's docstring named and did not
# then cover. `identity_key`, `money_role` and `temporal_value` were wired
# and tested above; `claim_subject`/`claim_text` were not, so
# `arbiter.evidence.semantic` reported NOT_APPLICABLE on every case ever
# adjudicated -- 0 of 11,124 evidence nodes in a seeded database carried a
# claim, and `analyze_contradictions` reported `complete: true` while a
# mandatory layer had silently never run. These tests exist so that cannot
# recur: they assert the claims REACH the layer, not that DeBERTa
# classifies correctly (tests/unit/test_contradiction_mandatory.py owns
# that, and owns it without needing the model on disk).


def _delivery_extraction(artifact_id: str, status_text: str) -> ExtractionResult:
    return ExtractionResult(
        artifact_id=artifact_id,
        document_type="delivery_confirmation",
        extraction_method="native",
        fields=[ExtractedField(
            field_name="delivery_status", value=status_text, confidence=0.8,
            source_ref=SourceRef(artifact_id=artifact_id, page=0, char_span=(0, len(status_text))),
        )],
    )


def test_ingest_emits_semantic_claim_inputs():
    """The wiring that did not exist: a typed status field must land on the
    node as a claim the NLI layer can read."""
    node = _extraction_to_node(
        "case-1", "art-1",
        _delivery_extraction("art-1", "The parcel was delivered and signed for on 3 March."),
        ProvenanceTier.SUBMITTED,
    )
    assert node.attrs.get("claim_subject") == "delivery"
    assert node.attrs.get("claim_text") == "The parcel was delivered and signed for on 3 March."


def test_native_extractor_emits_a_status_assertion():
    """The other half of the wiring. The native path extracted only
    amounts, dates and tracking ids -- all numeric or identifier-shaped, so
    a text cross-encoder had nothing to compare even once route.py was
    taught to pass claims along."""
    pdf = make_delivery_confirmation_pdf(
        order_id="ORD-1", address="1 Cardmember Way, New York",
        delivery_date=_BASE, tracking_number="1Z999AA10123456784", amount_minor=8999,
    )
    result = extract_native("art-1", pdf)
    assert result is not None
    names = {f.field_name for f in result.fields}
    assert "delivery_status" in names, f"native extraction produced no status assertion: {names}"

    status = next(f for f in result.fields if f.field_name == "delivery_status")
    assert "deliver" in str(status.value).lower()
    # Invariant #12: every extracted field carries a source_ref.
    assert status.source_ref.artifact_id == "art-1"
    assert status.source_ref.page == 0


def test_status_assertion_is_bounded_not_the_whole_document():
    """`claim_text` is a named, capped field -- not a raw-text escape hatch
    (CLAUDE.md invariant #3). An unbounded value here would hand the whole
    document to a classifier and reintroduce exactly the quarantine leak
    `arbiter.ingest.schemas` refuses to have a `raw_text` field for."""
    body = "The package never arrived. " + ("padding sentence about nothing. " * 200)
    pdf = make_communication_pdf(subject="Order ORD-1", body=body)
    result = extract_native("art-1", pdf)
    assert result is not None
    for f in result.fields:
        if f.field_name in ("delivery_status", "refund_status"):
            assert len(str(f.value)) <= 300, "status field is not length-capped"


def test_claim_text_is_redacted_before_it_is_persisted():
    """`claim_text` is stored in the clear (the layer reads it on every
    adjudication) and is fed to a text model, so it must not carry
    identifiers. Card numbers are tokenised upstream; these are the ones
    tokenisation does not cover."""
    node = _extraction_to_node(
        "case-1", "art-1",
        _delivery_extraction(
            "art-1",
            "Never delivered -- contact jane.doe@example.com or 555-123-4567 about it.",
        ),
        ProvenanceTier.SUBMITTED,
    )
    claim = node.attrs["claim_text"]
    assert "jane.doe@example.com" not in claim
    assert "555-123-4567" not in claim
    assert "delivered" in claim.lower(), "redaction destroyed the claim itself"


def test_semantic_layer_actually_runs_on_two_real_documents(monkeypatch):
    """End to end, and the assertion that matters: `layer_status["semantic"]`
    must be OK. It was NOT_APPLICABLE on every case in a seeded database of
    846 decisions, which is indistinguishable from the layer being switched
    off."""
    from arbiter.evidence.nli import NLIVerdict

    monkeypatch.setattr(
        "arbiter.evidence.semantic.classify_pairs",
        lambda pairs: [NLIVerdict("contradiction", 0.97, p, h) for p, h in pairs],
    )

    merchant = make_delivery_confirmation_pdf(
        order_id="ORD-1", address="1 Cardmember Way, New York",
        delivery_date=_BASE, tracking_number="1Z999AA10123456784", amount_minor=8999,
    )
    cardmember = make_communication_pdf(
        subject="Order ORD-1",
        body="The package never arrived and was never delivered to me. Nothing was received.",
    )

    graph = EvidenceGraph("case-semantic")
    for artifact_id, pdf in (("art-m", merchant), ("art-c", cardmember)):
        node, _ = process_artifact(
            case_id="case-semantic", artifact_id=artifact_id, data=pdf,
            filed_at_unix=None, provenance=ProvenanceTier.SUBMITTED,
        )
        assert node is not None, f"{artifact_id} produced no evidence node"
        graph.add_node(node)

    claims = [n.attrs.get("claim_text") for n in graph.nodes.values() if n.attrs.get("claim_text")]
    assert len(claims) >= 2, f"expected a claim from each document, got {claims}"

    analysis = graph.analyze_contradictions()
    assert analysis.layer_status["semantic"] == "OK", (
        f"the semantic layer did not run: {analysis.layer_status}. A mandatory layer "
        f"reporting NOT_APPLICABLE on documents that plainly contradict each other is "
        f"the exact defect this test exists to catch."
    )
    assert analysis.semantic_pairs_evaluated >= 1
    assert any(c.layer == "semantic" for c in analysis.contradictions)


def test_semantic_claim_nodes_carry_no_predicate():
    """Same rule as the other three layers: a claim shifts confidence and
    can force escalation, but must never satisfy a rule."""
    node = _extraction_to_node(
        "case-1", "art-1",
        _delivery_extraction("art-1", "The parcel was never delivered to the address."),
        ProvenanceTier.SUBMITTED,
    )
    assert "claim_text" in node.attrs
    assert "asserts_predicate" not in node.attrs

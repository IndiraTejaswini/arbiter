"""
Re-adjudication must not duplicate evidence, and forensics must reach a
decision.

Two findings, both in the derivation path:

1. `adjudicate_case` re-runs the network loader on every adjudication.
   With random UUIDs each run minted a fresh row for every network fact
   while the previous run's rows were still loaded from the database, so a
   second adjudication saw two nodes per predicate and a third saw three.
   Deleting the old rows would have been worse -- a signed decision's proof
   tree cites evidence_node_ids, and removing a cited node orphans the
   audit trail of a decision already made. Deterministic ids fix the
   duplication while leaving every prior citation valid.

2. `extract_conf` -- the OCR/VLM field confidence multiplied by the
   forensic tamper penalty -- was computed, persisted, displayed, and read
   by nothing that affected an outcome, because `Fact.confidence` came from
   `provenance.trust_weight` alone. A forged document and a clean one
   produced identical confidence, identical abstention, identical verdict.
"""

from __future__ import annotations

from datetime import datetime, timezone

from arbiter.config import get_settings
from arbiter.evidence import EvidenceGraph, derive_predicate_facts
from arbiter.evidence.models import EvidenceNode, EvidenceNodeType, ProvenanceTier
from arbiter.ingest.forensics import ForensicsReport, count_incremental_updates
from arbiter.network.loader import NetworkFacts, load_network_evidence
from arbiter.rulepack.loader import load_rulepack_dir

_FACTS = NetworkFacts(
    item_delivered=True, delivered_to_correct_address=True,
    signature_required=False, signature_captured=True,
    merchant_shipped_before_dispute=True,
    order_total_minor=8999, authorization_minor=8999, currency="USD",
    shipment_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
    delivery_at=datetime(2026, 3, 3, tzinfo=timezone.utc),
)


# -- Idempotent derivation ------------------------------------------------


def test_network_node_ids_are_stable_across_re_derivation():
    """The direct regression: two runs of the loader for the same case must
    produce the same node ids, or every re-adjudication duplicates."""
    first = load_network_evidence("case-1", "C08", _FACTS)
    second = load_network_evidence("case-1", "C08", _FACTS)

    assert [n.node_id for n in first] == [n.node_id for n in second]
    assert len({n.node_id for n in first}) == len(first), "ids must be unique within one run"


def test_different_cases_get_different_node_ids():
    """Stability must not become collision: two cases sharing a predicate
    must not share an evidence node."""
    a = {n.node_id for n in load_network_evidence("case-a", "C08", _FACTS)}
    b = {n.node_id for n in load_network_evidence("case-b", "C08", _FACTS)}
    assert a.isdisjoint(b)


def test_re_deriving_into_a_graph_does_not_grow_it():
    """What the orchestration path actually does: build the graph, then
    re-derive. Duplicates would show up as extra nodes."""
    graph = EvidenceGraph("case-1")
    for node in load_network_evidence("case-1", "C08", _FACTS):
        graph.add_node(node)
    size_after_first = len(graph.nodes)

    for node in load_network_evidence("case-1", "C08", _FACTS):
        graph.add_node(node)
    assert len(graph.nodes) == size_after_first


def test_predicates_derive_identically_after_re_derivation():
    packs = load_rulepack_dir(get_settings().rulepack_dir)
    pack = packs["C08"]

    graph = EvidenceGraph("case-1")
    for node in load_network_evidence("case-1", "C08", _FACTS):
        graph.add_node(node)
    first = {p: f.status for p, f in derive_predicate_facts(graph, pack).items()}

    for node in load_network_evidence("case-1", "C08", _FACTS):
        graph.add_node(node)
    second = {p: f.status for p, f in derive_predicate_facts(graph, pack).items()}

    assert first == second


# -- Forensics reach the decision -----------------------------------------


def _graph_with_document(extract_conf: float) -> EvidenceGraph:
    graph = EvidenceGraph("case-doc")
    graph.add_node(EvidenceNode(
        case_id="case-doc", node_type=EvidenceNodeType.DELIVERY_SCAN,
        attrs={"asserts_predicate": "delivery_confirmed", "predicate_value": True},
        provenance=ProvenanceTier.SUBMITTED, extract_conf=extract_conf,
    ))
    return graph


def test_extraction_confidence_now_reaches_fact_confidence():
    """THE regression: a tamper-flagged document and a clean one used to
    produce byte-identical Fact.confidence."""
    packs = load_rulepack_dir(get_settings().rulepack_dir)
    pack = packs["C08"]

    clean = derive_predicate_facts(_graph_with_document(1.0), pack)["delivery_confirmed"]
    flagged = derive_predicate_facts(_graph_with_document(0.4), pack)["delivery_confirmed"]

    assert clean.status == flagged.status, "forensics are a signal, never a verdict"
    assert flagged.confidence < clean.confidence, (
        "a document flagged by forensics must carry less evidentiary weight; "
        "otherwise the entire tamper-forensics layer is decorative"
    )


def test_forensics_never_change_whether_a_predicate_is_true():
    """CLAUDE.md: signals, never proof. Even a maximally-penalised document
    still establishes its predicate -- it just does so less confidently."""
    packs = load_rulepack_dir(get_settings().rulepack_dir)
    pack = packs["C08"]
    fact = derive_predicate_facts(_graph_with_document(0.05), pack)["delivery_confirmed"]
    assert fact.is_true


def test_network_evidence_is_unaffected_by_the_extract_conf_change():
    """Network facts have extract_conf 1.0, so their weight is exactly the
    provenance trust weight as before -- this change must not silently
    reweight the Amex-side evidence every case depends on."""
    packs = load_rulepack_dir(get_settings().rulepack_dir)
    pack = packs["C08"]
    graph = EvidenceGraph("case-net")
    for node in load_network_evidence("case-net", "C08", _FACTS):
        graph.add_node(node)
    fact = derive_predicate_facts(graph, pack)["delivery_confirmed"]
    assert fact.confidence == ProvenanceTier.NETWORK.trust_weight


# -- Incremental update detection -----------------------------------------


def test_incremental_update_counter_reads_the_byte_stream():
    """`incremental` was initialised False and never reassigned; the branch
    meant to set it tested `doc.is_dirty`, which is always False on a
    freshly opened document. It always reported False."""
    single_save = b"%PDF-1.7\nbody\nxref\ntrailer\n%%EOF\n"
    assert count_incremental_updates(single_save) == 0

    revised_twice = single_save + b"body2\nxref\ntrailer\n%%EOF\n" + b"body3\nxref\ntrailer\n%%EOF\n"
    assert count_incremental_updates(revised_twice) == 2


def test_incremental_update_counter_is_safe_on_garbage():
    assert count_incremental_updates(b"") == 0
    assert count_incremental_updates(b"not a pdf at all") == 0


def test_forensics_penalty_scales_with_flag_count():
    assert ForensicsReport().confidence_penalty() == 0.0
    assert ForensicsReport(moddate_after_filed_at=True).confidence_penalty() == 0.15
    two = ForensicsReport(moddate_after_filed_at=True, producer_suspicious=True)
    assert two.confidence_penalty() == 0.4
    three = ForensicsReport(
        moddate_after_filed_at=True, producer_suspicious=True,
        incremental_update_after_filed=True,
    )
    assert three.confidence_penalty() > two.confidence_penalty()

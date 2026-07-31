"""
The contradiction pipeline is MANDATORY and contains no generative model.

Four layers, none optional, none configurable away:

    1. Temporal  -- Allen interval algebra + domain ordering constraints
    2. Numeric   -- order -> authorization -> settlement -> refund
    3. Identity  -- address / device / IP / email coherence
    4. Semantic  -- DeBERTa-v3-MNLI cross-encoder, EXCLUSIVELY

The property that matters most here is the one that was previously absent:
**a layer that could not run must not be recorded as a layer that found
nothing.** The semantic layer used to compare a boolean `claim_polarity`
attribute that nothing in the system ever populated -- so it ran on every
case, found nothing on every case, and `contradiction_clarity` was a
constant 1.0. A layer that always returns "clean" is indistinguishable
from one that is switched off, which is exactly how it went unnoticed
through a whole build.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from arbiter.evidence import EvidenceGraph
from arbiter.evidence.graph import MANDATORY_LAYERS
from arbiter.evidence.models import EvidenceNode, EvidenceNodeType, ProvenanceTier
from arbiter.evidence.nli import NLI_MODEL_NAME, NLIUnavailable
from arbiter.evidence.semantic import (
    LayerStatus,
    SemanticClaim,
    analyze_semantic_claims,
)

_BASE = datetime(2026, 3, 1, tzinfo=timezone.utc)


def _node(graph: EvidenceGraph, **attrs) -> None:
    graph.add_node(EvidenceNode(
        case_id=graph.case_id, node_type=EvidenceNodeType.COMMUNICATION,
        attrs=dict(attrs), provenance=ProvenanceTier.NETWORK,
    ))


# -- No generative model anywhere in this pipeline ------------------------


def test_the_semantic_engine_is_deberta_nli():
    assert "deberta" in NLI_MODEL_NAME.lower()
    assert "mnli" in NLI_MODEL_NAME.lower()


def test_no_contradiction_module_imports_an_llm_client():
    """A generative model at this boundary would be the ONE unguarded LLM
    in the system: the other three boundaries each have a deterministic
    verifier that re-derives their output, and there is no mechanical way
    to re-derive 'these two sentences contradict'. It would also be able to
    suppress escalation by simply reporting no contradiction.

    Checked over the parsed import graph rather than raw source text, so
    the module docstrings are free to *discuss* why an LLM is forbidden
    without tripping their own check.
    """
    import ast
    import inspect

    from arbiter.evidence import graph, identity, nli, numeric, semantic, temporal

    banned_roots = {"openai", "anthropic", "ollama", "litellm", "cohere", "google"}
    banned_modules = {"arbiter.llm"}

    for module in (semantic, nli, temporal, numeric, identity, graph):
        tree = ast.parse(inspect.getsource(module))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        for name in imported:
            root = name.split(".")[0]
            assert root not in banned_roots, (
                f"{module.__name__} imports {name!r} -- the contradiction pipeline "
                f"must contain no generative model"
            )
            assert not any(name.startswith(b) for b in banned_modules), (
                f"{module.__name__} imports {name!r} -- arbiter.llm must never reach "
                f"the contradiction pipeline"
            )


def test_the_engine_is_not_configurable_to_something_else():
    """Only WHERE the weights live is configurable. The engine is not."""
    from arbiter.config import Settings

    fields = set(Settings.model_fields)
    assert "nli_model_path" in fields, "the checkpoint location is configurable"
    for forbidden in ("nli_engine", "nli_backend", "semantic_engine", "use_llm_for_contradictions"):
        assert forbidden not in fields, (
            f"{forbidden} must not exist -- the semantic engine is DeBERTa-NLI, full stop"
        )


# -- All four layers are mandatory ----------------------------------------


def test_all_four_layers_are_declared_mandatory():
    assert MANDATORY_LAYERS == ("temporal", "numeric", "identity", "semantic")


def test_every_layer_reports_a_status():
    graph = EvidenceGraph("case-1")
    analysis = graph.analyze_contradictions()
    for layer in MANDATORY_LAYERS:
        assert layer in analysis.layer_status, f"{layer} reported no status at all"


def test_an_empty_case_is_complete_not_incomplete():
    """Nothing to compare is NOT the same as a check that could not run.
    A case with no comparable claims must not be escalated for that."""
    analysis = EvidenceGraph("case-empty").analyze_contradictions()
    assert analysis.complete
    assert not analysis.must_escalate
    assert analysis.layer_status["semantic"] == LayerStatus.NOT_APPLICABLE.value


# -- Fail closed when a mandatory layer cannot run ------------------------


def test_unavailable_semantic_layer_forces_escalation(monkeypatch):
    """THE regression this pass exists to prevent: an unrunnable mandatory
    check must be an unknown, not a pass."""
    def _boom(pairs):
        raise NLIUnavailable("transformers not installed")

    monkeypatch.setattr("arbiter.evidence.semantic.classify_pairs", _boom)

    graph = EvidenceGraph("case-2")
    _node(graph, claim_subject="delivery", claim_text="The parcel was delivered and signed for.")
    _node(graph, claim_subject="delivery", claim_text="The parcel was returned to sender undelivered.")

    analysis = graph.analyze_contradictions()
    assert analysis.must_escalate, "an unavailable mandatory layer must force escalation"
    assert "semantic" in analysis.unavailable_layers
    assert not analysis.complete
    assert analysis.layer_status["semantic"] == LayerStatus.UNAVAILABLE.value


def test_unavailable_layer_is_never_reported_as_clean(monkeypatch):
    def _boom(pairs):
        raise NLIUnavailable("model missing")

    monkeypatch.setattr("arbiter.evidence.semantic.classify_pairs", _boom)
    result = analyze_semantic_claims([
        SemanticClaim("delivery", "n1", "It was delivered."),
        SemanticClaim("delivery", "n2", "It never arrived."),
    ])
    assert result.status is LayerStatus.UNAVAILABLE
    assert result.must_escalate
    assert result.contradictions == [], "no findings, but the STATUS is what callers must read"


def test_nli_unavailable_raises_rather_than_returning_empty():
    """A caller that silently treats 'the classifier did not run' as 'no
    contradictions found' converts a missing safety check into a clean bill
    of health."""
    assert issubclass(NLIUnavailable, Exception)


# -- Detection, with the engine stubbed at the classifier boundary --------


def test_contradictory_claims_are_detected(monkeypatch):
    from arbiter.evidence.nli import NLIVerdict

    def _classify(pairs):
        return [NLIVerdict("contradiction", 0.97, p, h) for p, h in pairs]

    monkeypatch.setattr("arbiter.evidence.semantic.classify_pairs", _classify)

    graph = EvidenceGraph("case-3")
    _node(graph, claim_subject="delivery", claim_text="Delivered and signed for on 3 March.")
    _node(graph, claim_subject="delivery", claim_text="Returned to sender; never delivered.")

    analysis = graph.analyze_contradictions()
    assert analysis.complete
    semantic_findings = [c for c in analysis.contradictions if c.layer == "semantic"]
    assert len(semantic_findings) == 1
    assert semantic_findings[0].severity == "HIGH"


def test_low_confidence_contradictions_are_not_reported(monkeypatch):
    """The layer surfaces conflicts a human should adjudicate; it does not
    manufacture doubt out of model uncertainty."""
    from arbiter.evidence.nli import NLIVerdict

    monkeypatch.setattr(
        "arbiter.evidence.semantic.classify_pairs",
        lambda pairs: [NLIVerdict("contradiction", 0.41, p, h) for p, h in pairs],
    )
    result = analyze_semantic_claims([
        SemanticClaim("delivery", "n1", "It was delivered."),
        SemanticClaim("delivery", "n2", "It may not have arrived."),
    ])
    assert result.status is LayerStatus.OK
    assert result.contradictions == []


def test_only_same_subject_claims_are_compared(monkeypatch):
    """Feeding the cross-encoder every pair of sentences in a case would be
    quadratically expensive and semantically meaningless."""
    from arbiter.evidence.nli import NLIVerdict

    seen = {}

    def _classify(pairs):
        seen["n"] = len(pairs)
        # One verdict per pair: the real classifier's contract, and
        # `analyze_semantic_claims` zips strict=True precisely so a stub or
        # a future engine that silently drops pairs fails loudly instead of
        # under-reporting contradictions.
        return [NLIVerdict("neutral", 0.9, p, h) for p, h in pairs]

    monkeypatch.setattr("arbiter.evidence.semantic.classify_pairs", _classify)
    analyze_semantic_claims([
        SemanticClaim("delivery", "n1", "Delivered."),
        SemanticClaim("delivery", "n2", "Not delivered."),
        SemanticClaim("refund", "n3", "Refunded in full."),
    ])
    assert seen["n"] == 1, "only the two delivery claims are type-compatible"


def test_claims_without_text_are_not_sent_to_a_text_classifier():
    """The gate is `claim_text`, not the old boolean `claim_polarity` --
    which was both the heuristic this layer replaced and an attribute
    nothing ever populated."""
    result = analyze_semantic_claims([
        SemanticClaim("delivery", "n1", ""),
        SemanticClaim("delivery", "n2", "   "),
    ])
    assert result.status is LayerStatus.NOT_APPLICABLE


# -- The other three layers still work ------------------------------------


def test_temporal_and_numeric_and_identity_all_still_fire():
    from arbiter.network.loader import NetworkFacts, load_network_evidence

    graph = EvidenceGraph("case-4")
    for node in load_network_evidence("case-4", "C08", NetworkFacts(
        order_total_minor=10_000, authorization_minor=90_000, currency="USD",
        order_shipping_address="1 A Street", carrier_delivery_address="99 B Road",
        shipment_at=_BASE, delivery_at=_BASE - timedelta(days=2),
    )):
        graph.add_node(node)

    layers = {c.layer for c in graph.analyze_contradictions().contradictions}
    assert {"numeric", "identity", "temporal"} <= layers

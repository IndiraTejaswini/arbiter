"""
The narration boundary: LLM proposes, citation grounding disposes.

This is the fourth LLM boundary in CLAUDE.md's table, and it was a stub for
an entire build -- `render_llm_narration` returned None unconditionally, so
`llm_exception_path` was a source value that could never occur and the veto
below had nothing to veto. These tests assert on the guarantee that makes
the boundary safe to turn on, not on prose quality:

  a single sentence citing a node id that does not exist discards the
  ENTIRE narration, and the reader is told the veto fired.

They never call a model. `complete_json` is the one seam, and every test
patches it -- a boundary whose safety property only holds when Ollama
happens to be running is not a safety property.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from arbiter.horn.chain import EvaluationResult
from arbiter.horn.clause import RulePack
from arbiter.horn.proof import LiteralWitness, ProofNode
from arbiter.narrate.ground import render_narration_safe
from arbiter.narrate.llm import render_llm_narration, should_use_llm_narration

REAL_NODE = "11111111-1111-4111-8111-111111111111"
OTHER_NODE = "22222222-2222-4222-8222-222222222222"
VALID_IDS = {REAL_NODE, OTHER_NODE}


def _deep_tree() -> ProofNode:
    """A proof of depth 5, i.e. past the `depth > 4` threshold that selects
    the exception path at all -- a shallow tree is rendered by the template
    and never reaches a model."""
    leaf = ProofNode(rule_id="R5", head="e", holds=True, literals=[
        LiteralWitness(predicate="p5", negated=False, satisfied=True, evidence_node_ids=(REAL_NODE,)),
    ])
    fourth = ProofNode(rule_id="R4", head="d", holds=True, literals=[
        LiteralWitness(predicate="p4", negated=False, satisfied=True,
                       evidence_node_ids=(OTHER_NODE,), child=leaf),
    ])
    third = ProofNode(rule_id="R3", head="c", holds=True, literals=[
        LiteralWitness(predicate="p3", negated=False, satisfied=True, child=fourth),
    ])
    second = ProofNode(rule_id="R2", head="b", holds=True, literals=[
        LiteralWitness(predicate="p2", negated=False, satisfied=True, child=third),
    ])
    return ProofNode(rule_id="R1", head="card_member_prevails", holds=True, literals=[
        LiteralWitness(predicate="p1", negated=False, satisfied=True, child=second),
    ])


def _evaluation() -> EvaluationResult:
    return EvaluationResult(
        rulepack_hash="abc123", reason_code="C08",
        true_predicates={"p1", "p2", "p3", "p4", "p5"}, fired_rules=["R1", "R2", "R3", "R4", "R5"],
        proof_trees={"card_member_prevails": _deep_tree()},
        decision="CARD_MEMBER_PREVAILS", decision_head="card_member_prevails",
        missing_predicates=set(), facts={},
    )


def _rulepack() -> RulePack:
    return RulePack(
        rulepack_id="C08-test", reason_code="C08", version="1.0", rules=(),
        decision_predicates={"CARD_MEMBER_PREVAILS": "card_member_prevails"},
    )


def _completion(*sentences):
    """Build what a model would return: (text, [cited ids]) per sentence."""
    return {"sentences": [{"text": t, "cites": list(c)} for t, c in sentences]}


def _flat(n_literals: int) -> EvaluationResult:
    """A depth-1 proof resting on `n_literals` base conditions -- the shape
    every one of this build's three rulepacks actually produces."""
    return EvaluationResult(
        rulepack_hash="h", reason_code="C08", true_predicates=set(), fired_rules=["R1"],
        proof_trees={"card_member_prevails": ProofNode(
            rule_id="R1", head="card_member_prevails", holds=True,
            literals=[
                LiteralWitness(predicate=f"p{i}", negated=False, satisfied=True,
                               evidence_node_ids=(REAL_NODE,))
                for i in range(n_literals)
            ],
        )},
        decision="CARD_MEMBER_PREVAILS", decision_head="card_member_prevails",
        missing_predicates=set(), facts={},
    )


def test_a_simple_proof_does_not_reach_a_model():
    """A one- or two-condition proof is read perfectly well by the
    deterministic renderer; spending a model call on it buys nothing and
    adds a failure mode."""
    assert should_use_llm_narration(_flat(1)) is False
    assert should_use_llm_narration(_flat(2)) is False


def test_a_flat_but_wide_proof_still_reaches_the_model():
    """The regression guard for how this boundary was dead on arrival. The
    gate was depth-only, and EVERY proof these rulepacks produce is depth 1
    -- 490 of 490 real decisions -- so `render_llm_narration` could never
    be called no matter how well it worked. Breadth is the signal that
    actually varies here."""
    assert should_use_llm_narration(_flat(3)) is True, (
        "a three-condition proof does not trigger the exception path -- with flat "
        "rulepacks that puts the narration boundary back to never firing"
    )
    assert should_use_llm_narration(_flat(4)) is True


def test_a_deep_proof_still_reaches_the_model():
    """Depth is retained for rulepacks with real IDB chaining."""
    assert should_use_llm_narration(_evaluation()) is True


def test_a_grounded_narration_is_used(monkeypatch):
    monkeypatch.setattr(
        "arbiter.narrate.llm.complete_json",
        lambda *a, **k: _completion(
            ("The carrier record shows the parcel was never delivered.", [REAL_NODE]),
            ("The merchant supplied no proof of delivery.", [OTHER_NODE]),
        ),
    )
    narration = render_narration_safe(_evaluation(), _rulepack(), VALID_IDS)
    assert narration.source == "llm_exception_path"
    assert len(narration.sentences) == 2
    assert {c.node_id for c in narration.citations} == VALID_IDS


def test_one_fabricated_citation_discards_the_entire_narration(monkeypatch):
    """THE guarantee. Not "drop the bad sentence" -- a narration citing a
    node that does not exist is a document whose provenance claims cannot
    be trusted, and it is shown to a card member as the reason they lost."""
    fabricated = "99999999-9999-4999-8999-999999999999"
    monkeypatch.setattr(
        "arbiter.narrate.llm.complete_json",
        lambda *a, **k: _completion(
            ("This sentence is perfectly well grounded.", [REAL_NODE]),
            ("This one cites a node that does not exist.", [fabricated]),
        ),
    )
    narration = render_narration_safe(_evaluation(), _rulepack(), VALID_IDS)

    assert narration.source != "llm_exception_path", "a fabricated citation reached the reader"
    assert fabricated not in narration.text
    assert all(c.node_id in VALID_IDS for c in narration.citations)
    # The *whole* narration went, not just the offending sentence.
    assert "This sentence is perfectly well grounded." not in narration.text


def test_a_vetoed_narration_is_reported_as_template_fallback(monkeypatch):
    """A reader is entitled to know the grounding check fired. If a vetoed
    case were reported as `template`, it would be indistinguishable from
    one where no model ever ran."""
    monkeypatch.setattr(
        "arbiter.narrate.llm.complete_json",
        lambda *a, **k: _completion(("Cites a ghost.", ["not-a-real-node"])),
    )
    assert render_narration_safe(_evaluation(), _rulepack(), VALID_IDS).source == "template_fallback"


def test_a_sentence_with_no_citation_at_all_is_rejected(monkeypatch):
    """`verify_citations` checks the citations that are present; it cannot
    catch a sentence that supplied none. That is this boundary's job."""
    monkeypatch.setattr(
        "arbiter.narrate.llm.complete_json",
        lambda *a, **k: _completion(
            ("Grounded.", [REAL_NODE]),
            ("An assertion with nothing behind it.", []),
        ),
    )
    assert render_llm_narration(_evaluation(), _rulepack()) is None


def test_the_model_being_unavailable_falls_back_silently(monkeypatch):
    """CLAUDE.md invariant #11: every LLM call site returns None on failure
    and the caller degrades. Ollama being down must not fail a case."""
    monkeypatch.setattr("arbiter.narrate.llm.complete_json", lambda *a, **k: None)
    narration = render_narration_safe(_evaluation(), _rulepack(), VALID_IDS)
    assert narration.source == "template"
    assert narration.text


def test_malformed_model_output_falls_back(monkeypatch):
    for bad in (None, {}, {"sentences": []}, {"sentences": [{"text": "", "cites": []}]}, "not a dict"):
        monkeypatch.setattr(
            "arbiter.narrate.llm.complete_json",
            lambda *a, _bad=bad, **k: _bad,  # bind per-iteration, not by closure
        )
        assert render_llm_narration(_evaluation(), _rulepack()) is None


def test_citations_are_not_pre_filtered_before_the_verifier_sees_them(monkeypatch):
    """If this module screened fabricated ids out itself, the veto would
    never fire and the boundary would be unguarded while appearing safe.
    The hallucination must reach `verify_citations`."""
    monkeypatch.setattr(
        "arbiter.narrate.llm.complete_json",
        lambda *a, **k: _completion(("Cites a ghost.", ["ghost-node"])),
    )
    generated = render_llm_narration(_evaluation(), _rulepack())
    assert generated is not None
    assert any(c.node_id == "ghost-node" for c in generated.citations), (
        "render_llm_narration filtered the bad citation itself -- the verifier "
        "must be the thing that catches it"
    )


def test_narration_cannot_carry_a_verdict(monkeypatch):
    """Structural, not behavioural: no caller can read an outcome out of a
    narration, so no prose the model writes can move the decision."""
    monkeypatch.setattr(
        "arbiter.narrate.llm.complete_json",
        lambda *a, **k: _completion(("The merchant should have won this case.", [REAL_NODE])),
    )
    narration = render_narration_safe(_evaluation(), _rulepack(), VALID_IDS)
    for forbidden in ("outcome", "decision", "verdict", "prevails"):
        assert not hasattr(narration, forbidden), (
            f"Narration exposes {forbidden!r} -- an LLM-written field could then reach a verdict"
        )

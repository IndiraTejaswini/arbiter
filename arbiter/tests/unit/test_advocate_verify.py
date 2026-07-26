"""
Unit coverage for arbiter.advocate.verify -- specifically the exact scenario
evals/hallucination.py's poisoning test surfaced: a predicate that is
genuinely TRUE (established by a real evidence node) does NOT make an
advocate's citation of a *different*, unrelated node for that same
predicate verifiable. Truth of the predicate and correctness of the
citation are two separate checks, and both must hold.

Without this test, "the predicate happened to already be true" and "the
advocate's claim about WHY checks out" could be silently conflated -- which
is exactly the ambiguity that made evals/hallucination.py's raw
"injected_accepted" counter look like a containment failure (3/12) when the
actual mechanism was never touched: every one of those cases the LLM cited
the real supporting node, not the poisoned one. See README.md's
"Hallucination containment" section for the full account.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from arbiter.advocate.contract import ArgumentGraph, ArgumentTriple
from arbiter.advocate.verify import verify_assertions
from arbiter.horn.proof import Fact, FactStatus


def test_citation_to_unrelated_node_rejected_even_when_predicate_is_true():
    """The core guarantee: truth of the predicate alone is not enough --
    the CITED evidence must actually be what established it."""
    objective_facts = {
        "account_takeover_signal": Fact("account_takeover_signal", FactStatus.TRUE, ("real-node-1",), confidence=0.9),
    }
    poison_triple = ArgumentTriple(
        predicate="account_takeover_signal", negated=False,
        evidence_node_ids=("poison-node-1",), warrant_rule_id=None,
    )
    graph = ArgumentGraph("CM", "CARD_MEMBER_WINS", "card_member_wins", (poison_triple,), (), False)

    verifications, verified_facts = verify_assertions([graph], objective_facts)

    assert len(verifications) == 1
    assert verifications[0].verified is False
    assert "citation does not check out" in verifications[0].reason
    assert "account_takeover_signal" not in verified_facts


def test_citation_to_the_actual_supporting_node_is_accepted():
    """Sanity check on the other side of the same mechanism: citing the
    REAL node that establishes a true predicate passes, exactly as it
    should -- this isn't a test that rejects everything."""
    objective_facts = {
        "account_takeover_signal": Fact("account_takeover_signal", FactStatus.TRUE, ("real-node-1",), confidence=0.9),
    }
    honest_triple = ArgumentTriple(
        predicate="account_takeover_signal", negated=False,
        evidence_node_ids=("real-node-1",), warrant_rule_id=None,
    )
    graph = ArgumentGraph("CM", "CARD_MEMBER_WINS", "card_member_wins", (honest_triple,), (), False)

    verifications, verified_facts = verify_assertions([graph], objective_facts)

    assert verifications[0].verified is True
    assert verified_facts["account_takeover_signal"] is objective_facts["account_takeover_signal"]


def test_citation_to_unestablished_predicate_rejected():
    """A predicate the graph never established at all (status UNKNOWN,
    e.g. because it was only ever tagged on a quarantined/untrusted node
    that derive_predicate_facts never picked up) is rejected outright,
    regardless of citation."""
    objective_facts: dict = {}  # nothing established anything
    fabricated_triple = ArgumentTriple(
        predicate="account_takeover_signal", negated=False,
        evidence_node_ids=("poison-node-1",), warrant_rule_id=None,
    )
    graph = ArgumentGraph("CM", "CARD_MEMBER_WINS", "card_member_wins", (fabricated_triple,), (), False)

    verifications, verified_facts = verify_assertions([graph], objective_facts)

    assert verifications[0].verified is False
    assert "does not establish it" in verifications[0].reason

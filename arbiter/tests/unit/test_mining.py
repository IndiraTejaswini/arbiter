"""
Unit coverage for arbiter.decision.mining: recurring analyst overrides on
abstained cases become PROPOSED rules (data), never anything that runs on
its own. Three properties matter: recurrence is required (no single-case
proposals), coverage by an existing rule suppresses a proposal (it
wouldn't have abstained in the first place), and output is deterministic.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from arbiter.decision.mining import ReviewedCase, mine_proposed_rules, rule_bodies_by_outcome
from arbiter.horn.clause import Literal, Rule, RulePack


def _make_case(case_id: str, reason_code: str, true_predicates: frozenset, outcome: str) -> ReviewedCase:
    return ReviewedCase(case_id=case_id, reason_code=reason_code, true_predicates=true_predicates, analyst_outcome=outcome)


def test_recurring_pattern_proposed_when_support_met():
    cases = [
        _make_case(f"c{i}", "F29", frozenset({"velocity_anomaly_flagged", "new_shipping_country"}), "CARD_MEMBER_WINS")
        for i in range(3)
    ]
    proposals = mine_proposed_rules(cases, known_rule_bodies={}, min_support=3)

    assert len(proposals) == 1
    assert proposals[0].reason_code == "F29"
    assert proposals[0].outcome == "CARD_MEMBER_WINS"
    assert proposals[0].body == frozenset({"velocity_anomaly_flagged", "new_shipping_country"})
    assert proposals[0].support_count == 3
    assert proposals[0].supporting_case_ids == ("c0", "c1", "c2")


def test_below_min_support_not_proposed():
    cases = [
        _make_case(f"c{i}", "F29", frozenset({"velocity_anomaly_flagged"}), "CARD_MEMBER_WINS")
        for i in range(2)
    ]
    proposals = mine_proposed_rules(cases, known_rule_bodies={}, min_support=3)
    assert proposals == []


def test_pattern_already_covered_by_existing_rule_not_proposed():
    """If an existing rule's body is already a subset of the recurring
    true_predicates, the referee would have derived the outcome itself --
    this case could never have reached an analyst for that reason, so
    proposing it again would be noise, not a gap."""
    cases = [
        _make_case(f"c{i}", "F29", frozenset({"cardholder_reported_card_lost_stolen", "unrelated_signal"}), "CARD_MEMBER_WINS")
        for i in range(5)
    ]
    known = {"F29": {"CARD_MEMBER_WINS": [frozenset({"cardholder_reported_card_lost_stolen"})]}}
    proposals = mine_proposed_rules(cases, known_rule_bodies=known, min_support=3)
    assert proposals == []


def test_different_outcomes_kept_separate():
    cases = (
        [_make_case(f"cm{i}", "C08", frozenset({"pred_a", "pred_b"}), "CARD_MEMBER_WINS") for i in range(3)]
        + [_make_case(f"m{i}", "C08", frozenset({"pred_a", "pred_b"}), "MERCHANT_WINS") for i in range(3)]
    )
    proposals = mine_proposed_rules(cases, known_rule_bodies={}, min_support=3)
    outcomes = {p.outcome for p in proposals}
    assert outcomes == {"CARD_MEMBER_WINS", "MERCHANT_WINS"}
    assert len(proposals) == 2


def test_output_is_deterministic_and_sorted_by_support_desc():
    cases = (
        [_make_case(f"a{i}", "F29", frozenset({"pred_x"}), "CARD_MEMBER_WINS") for i in range(3)]
        + [_make_case(f"b{i}", "F29", frozenset({"pred_y"}), "CARD_MEMBER_WINS") for i in range(5)]
    )
    proposals = mine_proposed_rules(cases, known_rule_bodies={}, min_support=3)
    assert [p.support_count for p in proposals] == [5, 3]
    assert proposals[0].body == frozenset({"pred_y"})


def test_rule_bodies_by_outcome_extracts_positive_literals_keyed_by_outcome_name():
    pack = RulePack(
        rulepack_id="test", reason_code="F29", version="1",
        rules=(
            Rule("R1", "merchant_wins", (Literal("a", False), Literal("b", True))),
            Rule("R2", "card_member_wins", (Literal("c", False),)),
        ),
        decision_predicates={"MERCHANT_WINS": "merchant_wins", "CARD_MEMBER_WINS": "card_member_wins"},
    )
    bodies = rule_bodies_by_outcome(pack)
    assert bodies["MERCHANT_WINS"] == [frozenset({"a"})]  # negated literal "b" excluded
    assert bodies["CARD_MEMBER_WINS"] == [frozenset({"c"})]

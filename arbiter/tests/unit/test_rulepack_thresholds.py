"""`at_least: {n, of}` rule bodies -- N-of-M authoring, expanded at load time.

The Amex chargeback guide writes compelling evidence as thresholds
throughout: RC 4540 asks for "three (3) or more of the following" (p.19) and
"at least two (2) of the following items" (p.20), and Visa CE3.0 -- which
F29 encodes -- is "2 of 4, one of which must be device_id or ip_address".
Hand-expanding those is where a rulepack author silently drops a
combination, and a dropped combination is a merchant who loses a case the
rules say they win.

What these tests protect is that the sugar stays sugar. Expansion happens
once, in `arbiter.rulepack.loader`, into ordinary conjunctive Horn clauses;
`arbiter.horn` never learns the concept exists, so prime-implicant
enumeration and the counterfactual ledger keep working over a finite literal
set exactly as before.
"""

from __future__ import annotations

import itertools

import pytest

from arbiter.core.errors import RulepackError
from arbiter.horn import Engine, Fact, FactStatus, enumerate_prime_implicants
from arbiter.rulepack.loader import MAX_THRESHOLD_EXPANSION, parse_rulepack


def _doc(body, predicate_schema):
    return {
        "rulepack_id": "test-v1", "reason_code": "TST", "version": "1.0.0",
        "predicate_schema": predicate_schema,
        "decision_predicates": {"MERCHANT_WINS": "merchant_wins"},
        "rules": [{"rule_id": "R1", "head": "merchant_wins", "body": body}],
    }


def _bodies(pack):
    return {frozenset(lit.key() for lit in r.body) for r in pack.rules}


def test_three_of_four_expands_to_every_combination():
    """The guide's widest threshold: RC 4540, "In addition, provide three (3)
    or more of the following" over four items."""
    pack = parse_rulepack(_doc(
        [{"at_least": {"n": 3, "of": ["a", "b", "c", "d"]}}],
        ["a", "b", "c", "d"],
    ))
    assert len(pack.rules) == 4
    assert _bodies(pack) == {
        frozenset((p, False) for p in combo)
        for combo in itertools.combinations("abcd", 3)
    }


def test_threshold_combines_with_fixed_conjuncts():
    """RC 4540's digital-goods structure: three mandatory items, then "three
    (3) or more of" a further list. The mandatory ones must appear in every
    expanded body, or an expansion could satisfy the rule without them."""
    pack = parse_rulepack(_doc(
        ["mandatory_x", {"at_least": {"n": 1, "of": ["a", "b"]}}],
        ["mandatory_x", "a", "b"],
    ))
    assert len(pack.rules) == 2
    assert all(("mandatory_x", False) in body for body in _bodies(pack))


def test_expanded_rule_ids_keep_the_authored_prefix():
    """A fired rule has to trace back to the clause a human wrote -- both for
    the proof tree a party reads and for the fairness layer's per-rule
    disparate-impact analysis."""
    pack = parse_rulepack(_doc([{"at_least": {"n": 1, "of": ["a", "b"]}}], ["a", "b"]))
    assert [r.rule_id for r in pack.rules] == ["R1#1", "R1#2"]


def test_single_body_rules_keep_their_bare_id():
    """No `#1` suffix when nothing expanded: a rulepack with no threshold
    groups must produce byte-identical rules to the pre-feature loader, or
    every existing content hash moves."""
    pack = parse_rulepack(_doc(["a", "b"], ["a", "b"]))
    assert [r.rule_id for r in pack.rules] == ["R1"]


def test_negated_members_are_supported():
    pack = parse_rulepack(_doc(
        [{"at_least": {"n": 1, "of": ["a", "not b"]}}], ["a", "b"],
    ))
    assert _bodies(pack) == {frozenset({("a", False)}), frozenset({("b", True)})}


def test_redundant_mention_is_deduplicated_not_duplicated():
    """A member that is also a fixed conjunct is a redundant mention, not an
    error -- but it must not produce a body listing the literal twice."""
    pack = parse_rulepack(_doc(
        ["a", {"at_least": {"n": 1, "of": ["a", "b"]}}], ["a", "b"],
    ))
    bodies = _bodies(pack)
    assert frozenset({("a", False)}) in bodies
    assert frozenset({("a", False), ("b", False)}) in bodies
    assert len(bodies) == 2
    for rule in pack.rules:
        assert len(rule.body) == len({lit.key() for lit in rule.body})


def test_contradictory_expansion_is_rejected_loudly():
    """`not a` as a fixed conjunct against `a` as a threshold member yields a
    body that is unsatisfiable by construction. Silently dropping it would
    make the rule mean something the author did not write."""
    with pytest.raises(RulepackError, match="unsatisfiable by construction"):
        parse_rulepack(_doc(
            ["not a", {"at_least": {"n": 1, "of": ["a", "b"]}}], ["a", "b"],
        ))


def test_n_larger_than_the_member_list_is_rejected():
    with pytest.raises(RulepackError, match="not satisfiable"):
        parse_rulepack(_doc([{"at_least": {"n": 5, "of": ["a", "b"]}}], ["a", "b"]))


def test_n_below_one_is_rejected():
    with pytest.raises(RulepackError, match="not satisfiable"):
        parse_rulepack(_doc([{"at_least": {"n": 0, "of": ["a", "b"]}}], ["a", "b"]))


def test_runaway_expansion_is_capped():
    """The guard that keeps sugar from becoming power. 10-choose-5 is 252
    clauses from one authored rule; past the cap the honest answer is that
    the rulepack should say what it means as separate rules."""
    members = [f"p{i}" for i in range(10)]
    with pytest.raises(RulepackError, match=f"over the {MAX_THRESHOLD_EXPANSION} limit"):
        parse_rulepack(_doc([{"at_least": {"n": 5, "of": members}}], members))


def test_malformed_threshold_spec_is_rejected():
    with pytest.raises(RulepackError, match="requires"):
        parse_rulepack(_doc([{"at_least": {"n": 2}}], ["a"]))
    with pytest.raises(RulepackError, match="unrecognised body item"):
        parse_rulepack(_doc([{"at_most": {"n": 2, "of": ["a"]}}], ["a"]))


def test_expansion_is_semantically_a_threshold_over_the_full_powerset():
    """The property that matters, checked exhaustively rather than by
    inspection: the expanded rule set fires on exactly those assignments
    where at least n of the members hold."""
    members = ["a", "b", "c", "d"]
    pack = parse_rulepack(_doc([{"at_least": {"n": 3, "of": members}}], members))
    engine = Engine()
    for bits in itertools.product([False, True], repeat=len(members)):
        facts = {
            p: Fact(p, FactStatus.TRUE, (f"n_{p}",))
            for p, on in zip(members, bits, strict=False) if on
        }
        fired = "merchant_wins" in engine.evaluate(pack, facts).true_predicates
        assert fired is (sum(bits) >= 3), f"threshold broke at {dict(zip(members, bits, strict=False))}"


def test_prime_implicants_are_the_minimal_combinations():
    """The counterfactual ledger reads prime implicants to tell a party the
    minimal fact set that flips their outcome. For a threshold rule those
    must be the n-subsets themselves -- not supersets, which would overstate
    what a party has to produce."""
    members = ["a", "b", "c"]
    pack = parse_rulepack(_doc([{"at_least": {"n": 2, "of": members}}], members))
    implicants = {frozenset(mwc.literals) for mwc in enumerate_prime_implicants(pack, "merchant_wins")}
    assert implicants == {
        frozenset((p, False) for p in combo) for combo in itertools.combinations(members, 2)
    }

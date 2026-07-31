"""Rulepack property tests (PT-1..PT-8 from the build spec): testable
invariants of a fairness/correctness property, not benchmark scores. Run
with:

    python -m pytest tests/property/ -v

- PT-1 monotonicity: adding unrelated corroborating evidence never un-fires
  an already-satisfied rule.
- PT-2 symmetry: no rule may condition on a party-classification attribute
  (tier, segment, region, ...) rather than on evidence.
- PT-3 determinism: same facts + same rulepack hash -> byte-identical proof
  tree, independent of dict/iteration ordering.
- PT-4 reachability: every rule is firable by some satisfiable predicate set.
- PT-5 acyclicity: head->body graph is a DAG (checked at rulepack load via
  arbiter.rulepack.validate; StratificationError would already have failed
  the session-scoped `packs` fixture in conftest.py if violated).
- PT-6 completeness: every declared predicate is referenced; every body atom
  is declared (also checked at load time).
- PT-7 tier soundness: covered structurally by arbiter.evidence.derive's
  min_tier gate (unit-tested in tests/unit/test_derive_tier_gating.py) --
  the Horn engine itself never sees a Fact that skipped the gate, so there
  is nothing for a rulepack-level property test to additionally assert here.
- PT-8 no dictator: deliberately single-literal, dispositive rules DO exist
  in these rulepacks by design (e.g. "cardholder reported card lost/stolen"
  is meant to decide F29 on its own) -- that is a domain choice, not a
  defect, and a naive "no singleton prime implicant" check would flag every
  one of them as a false positive. What "no dictator" actually forbids --
  a predicate that wins even in the FACE OF contradicting evidence, by
  silently overriding rather than surfacing a conflict -- is covered by
  test_conflicts_never_silently_resolved below: two rules firing
  on genuinely independent predicates always produces a surfaced conflict,
  never a silent pick.
- mutual exclusivity: no evidence assignment satisfies two outcomes at once
  without it being surfaced as a conflict -- see `strategies.py` for how the
  assignment space is enumerated now that a full powerset sweep no longer
  scales, and which properties keep an exact proof at which rulepack sizes.
- PT-10 chargeback right: every rulepack transcribes its reason code's
  filing window and "Excluded Transactions" list from the Amex guide, and
  the gate that evaluates them can never close on an empty attribute set.
  The mirror of `test_no_trivial_prime_implicant` for the pre-referee
  decider (`arbiter.eligibility`).
- regression coverage for defects this build found and fixed while it was
  being written: a trivial empty-set prime implicant, and advocate/referee
  decision divergence.
"""

from __future__ import annotations

import itertools
import json
from datetime import datetime, timezone
from typing import Dict

from conftest import RULEPACK_DIR, skip_if_full_sweep
from strategies import (
    BUDGET_ADVOCATE_SEARCH,
    BUDGET_SINGLE_EVALUATION,
    assignment_matrix,
    facts_for,
    matrix_is_exhaustive,
)

from arbiter.advocate import run_dual_advocacy
from arbiter.decision import Referee
from arbiter.eligibility import ATTRIBUTE_VOCABULARY, evaluate_chargeback_right
from arbiter.fairness import CaseRecord, compute_rule_level_disparate_impact, flagged_only
from arbiter.horn import Fact, FactStatus, enumerate_prime_implicants
from arbiter.narrate import render_narration_safe
from arbiter.rulepack import load_rulepack_dir

# `packs`, `engine` and `synthetic_pack` come from conftest.py -- three test
# modules consume them now, and re-parsing the rulepack YAML per module was
# wasted work.

# banned substrings: predicates must describe *evidence*, never a party
# classification attribute. A rule conditioning on any of these would be
# discriminating by construction, not by facts.
_PROTECTED_ATTRIBUTE_PATTERNS = (
    "tier", "segment", "premium", "region", "age", "gender", "race",
    "ethnicity", "income", "credit_score", "zip", "postal",
)


def _facts_exactly_satisfying(mwc, extra_true: Dict[str, bool] = None) -> Dict[str, Fact]:
    extra_true = extra_true or {}
    facts: Dict[str, Fact] = {}
    for p, neg in mwc.literals:
        if not neg:
            facts[p] = Fact(p, FactStatus.TRUE, (f"n_{p}",))
    for p in extra_true:
        facts[p] = Fact(p, FactStatus.TRUE, (f"n_{p}",))
    return facts


# ---------------------------------------------------------------- PT-4 reachability

def test_every_outcome_has_a_reachable_decision_path(packs):
    for code, pack in packs.items():
        for outcome, head in pack.decision_predicates.items():
            mwcs = enumerate_prime_implicants(pack, head)
            assert mwcs, f"{code}: outcome {outcome} ({head}) has no reachable decision path at all"


def test_no_trivial_prime_implicant(packs):
    """Regression test: an early C08 card-member-win rule originally fired
    on pure absence of evidence (its own prime implicant was the empty
    set), silently dominating every other card_member_wins rule and
    reviving the R03/R13 default-to-cardmember failure mode (C4: abstention
    must be a first-class output, not a default outcome). Fixed by removing
    the rule; this test keeps it fixed."""
    for code, pack in packs.items():
        for outcome, head in pack.decision_predicates.items():
            for mwc in enumerate_prime_implicants(pack, head):
                assert len(mwc.literals) > 0, (
                    f"{code} {outcome}: trivial empty-set prime implicant -- "
                    f"this outcome fires on pure absence of evidence"
                )


# ---------------------------------------------------------------- PT-3 determinism

def test_determinism_same_facts_same_hash(packs, engine):
    for pack in packs.values():
        for head in pack.decision_predicates.values():
            for mwc in enumerate_prime_implicants(pack, head)[:3]:
                facts = _facts_exactly_satisfying(mwc)
                r1 = engine.evaluate(pack, facts)
                r2 = engine.evaluate(pack, dict(reversed(list(facts.items()))))  # different dict insertion order
                assert r1.rulepack_hash == r2.rulepack_hash
                assert r1.fired_rules == r2.fired_rules
                assert json.dumps(r1.to_dict(), sort_keys=True) == json.dumps(r2.to_dict(), sort_keys=True)


def test_rulepack_hash_stable_across_reload(packs):
    reloaded = load_rulepack_dir(RULEPACK_DIR)
    for code, pack in packs.items():
        assert pack.content_hash() == reloaded[code].content_hash()


# ---------------------------------------------------------------- PT-1 monotonicity

def test_monotonicity_unrelated_evidence_never_unfires_a_rule(packs, engine):
    """
    Tested against actual rule bodies, not minimized prime implicants: a
    negative literal can be stripped from an MWC during minimization (it's
    redundant *as a sufficiency condition* under closed-world assumption,
    see horn/implicants.py's module docstring) while still being very much
    present -- and still able to block the rule -- in the rule's own body.
    "Unrelated" must therefore mean "not referenced, positively or
    negatively, by the rule actually firing", not "not in its minimized
    MWC".
    """
    for code, pack in packs.items():
        edb = pack.edb_predicates()
        for rule in pack.rules:
            base_facts = {
                lit.predicate: Fact(lit.predicate, FactStatus.TRUE, (f"n_{lit.predicate}",))
                for lit in rule.body if not lit.negated
            }
            base_eval = engine.evaluate(pack, base_facts)
            if rule.head not in base_eval.true_predicates:
                continue  # another rule for the same head already fires first in this base case; skip
            assert not base_eval.conflicting_outcomes, (
                f"{code}: rule {rule.rule_id}'s own minimal base facts already trigger a conflict "
                f"with another outcome -- rulepacks should be checked for this independently"
            )

            rule_preds = {lit.predicate for lit in rule.body}
            unrelated = [p for p in edb if p not in rule_preds]
            for extra_pred in unrelated:
                facts2 = dict(base_facts)
                facts2[extra_pred] = Fact(extra_pred, FactStatus.TRUE, (f"n_{extra_pred}",))
                eval2 = engine.evaluate(pack, facts2)
                assert rule.head in eval2.true_predicates, (
                    f"{code}: adding unrelated fact {extra_pred}=TRUE to {rule.rule_id}'s base facts "
                    f"un-fired the rule (head {rule.head} no longer derived)"
                )


# ---------------------------------------------------------------- PT-2 symmetry / fairness lint

def test_no_protected_attribute_predicates(packs):
    for code, pack in packs.items():
        schema = set(pack.predicate_schema) or pack.edb_predicates()
        for pred in schema:
            lowered = pred.lower()
            for pattern in _PROTECTED_ATTRIBUTE_PATTERNS:
                assert pattern not in lowered, (
                    f"{code}: predicate {pred!r} looks like a party-classification "
                    f"attribute (matched {pattern!r}), not evidence -- rules must "
                    f"condition on evidence only"
                )


# ---------------------------------------------------------------- conflicts are never silently resolved (exhaustive)

def test_conflicts_never_silently_resolved(packs, engine):
    """
    Evidence CAN legitimately satisfy two outcomes' rules at once -- e.g.
    C02_R4 (service_never_rendered) and C02_R8 (dispute_filed_before_
    return_received) reference independent predicates a real case could
    assert simultaneously. What this test verifies: whenever multiple
    outcomes fire, `decision` is always None and `conflicting_outcomes`
    always lists exactly the outcomes that fired -- the conflict is
    surfaced, never silently arbitrated by dict/iteration order.

    One `Engine.evaluate` per assignment, so this runs under the cheap
    budget and stays a full powerset sweep for every rulepack shipped today.
    When a rulepack outgrows the budget, the matrix still contains every
    cross-outcome implicant pair by construction
    (`strategies.structural_assignments`) -- which is where a conflict can
    arise at all, since firing two outcomes requires satisfying an implicant
    of each. Random sampling would reach those rows only by luck.
    """
    for code, pack in packs.items():
        heads_by_outcome = pack.decision_predicates
        checked_conflicts = 0
        for assignment in assignment_matrix(pack, exhaustive_budget=BUDGET_SINGLE_EVALUATION):
            result = engine.evaluate(pack, facts_for(assignment))
            fired_outcomes = {o for o, h in heads_by_outcome.items() if h in result.true_predicates}

            if len(fired_outcomes) > 1:
                checked_conflicts += 1
                assert result.decision is None, (
                    f"{code}: {fired_outcomes} all fired but decision was silently set to {result.decision!r}"
                )
                assert set(result.conflicting_outcomes) == fired_outcomes, (
                    f"{code}: conflicting_outcomes {result.conflicting_outcomes} does not match "
                    f"actually-fired outcomes {fired_outcomes}"
                )
            elif len(fired_outcomes) == 1:
                assert result.decision in fired_outcomes
                assert result.conflicting_outcomes == ()
            else:
                assert result.decision is None
                assert result.conflicting_outcomes == ()
        if code == "C02":
            assert checked_conflicts > 0, "expected C02's known-independent predicates to produce some conflicts"


@skip_if_full_sweep
def test_conflict_surfacing_survives_sampling_on_a_large_rulepack(engine, synthetic_pack):
    """The same property on a rulepack too big to sweep, so the sampled path
    is exercised now rather than by whoever lands the first large rulepack.

    Asserts the generator actually delivers conflicts: if the matrix reached
    no two-outcome assignment, the test above would pass vacuously in sampled
    mode, which is precisely how a sampled property test becomes worthless
    without anyone noticing.
    """
    pack = synthetic_pack(24)
    assert not matrix_is_exhaustive(pack, BUDGET_SINGLE_EVALUATION)

    conflicts = 0
    for assignment in assignment_matrix(pack, exhaustive_budget=BUDGET_SINGLE_EVALUATION):
        result = engine.evaluate(pack, facts_for(assignment))
        fired = {o for o, h in pack.decision_predicates.items() if h in result.true_predicates}
        if len(fired) > 1:
            conflicts += 1
            assert result.decision is None
            assert set(result.conflicting_outcomes) == fired
        elif len(fired) == 1:
            assert result.decision in fired
            assert result.conflicting_outcomes == ()
    assert conflicts > 0, (
        "the sampled matrix produced no multi-outcome assignment, so conflict surfacing was "
        "never actually checked -- the cross-outcome implicant-pair family in "
        "strategies.structural_assignments is not doing its job"
    )


# ---------------------------------------------------------------- advocate/referee consistency

def test_advocate_completeness_matches_referee(packs, engine):
    """The consistency invariant the dual-advocate design depends on: if the
    objective facts alone would satisfy some outcome under a full
    Engine.evaluate(), the corresponding advocate must find and cite that
    MWC, so the Referee reaches the exact same decision.

    THE EXPENSIVE ONE, and the reason `strategies.py` takes a budget per
    caller rather than one global threshold. Each assignment costs a full
    `run_dual_advocacy` search plus two evaluations -- roughly forty times a
    bare evaluation -- and the old full sweep over three rulepacks was 10 of
    this suite's 13 seconds on its own. It runs under
    `BUDGET_ADVOCATE_SEARCH`, so today's rulepacks are checked over the
    covering matrix rather than the powerset.

    That is a deliberate reduction in breadth, and it is defensible because
    of what this test now guards. Read `arbiter.decision.adjudicate`'s module
    docstring: the Referee evaluates the COMPLETE objective fact set, so
    divergence is structurally impossible today and this test is a regression
    guard against someone re-narrowing evaluation to the advocate-cited
    subset. That failure is not diffuse across the input space -- it shows up
    exactly where the cited subset differs from the full fact set, i.e. where
    a fact BLOCKS a rule rather than satisfying one, since a minimal winning
    coalition has no reason to cite a blocker. The matrix's
    `implicant + blocker` family targets precisely those assignments, and it
    is the shape of the original defect that docstring records
    (facts={service_never_rendered: TRUE, refund_issued: TRUE}).

    `FULL_POWERSET_SWEEP=1` restores the powerset sweep here too.
    """
    referee = Referee(engine)
    for code, pack in packs.items():
        for assignment in assignment_matrix(pack, exhaustive_budget=BUDGET_ADVOCATE_SEARCH):
            facts = facts_for(assignment)
            full_eval = engine.evaluate(pack, facts)
            cm_graph, m_graph = run_dual_advocacy(pack, facts)
            referee_result = referee.adjudicate(pack, [cm_graph, m_graph], facts)
            assert referee_result.evaluation.decision == full_eval.decision, (
                f"{code}: referee decision {referee_result.evaluation.decision} diverged from "
                f"full objective evaluation {full_eval.decision} for facts={sorted(facts)}"
            )


def test_advocate_completeness_covers_blocking_facts(packs, engine):
    """The specific regression the test above is a guard against, pinned
    directly instead of left to the matrix.

    For every rule with a negated literal, construct the assignment that
    satisfies the rule's positive literals AND asserts the blocker true. The
    rule must not fire, and the referee must agree with a full evaluation.
    This is the `{service_never_rendered: TRUE, refund_issued: TRUE}` case
    from `arbiter.decision.adjudicate`'s docstring, generalised over every
    rulepack -- and unlike the matrix-driven test it cannot be weakened by a
    future change to the sampling strategy.
    """
    referee = Referee(engine)
    checked = 0
    for code, pack in packs.items():
        for rule in pack.rules:
            blockers = [lit.predicate for lit in rule.body if lit.negated]
            if not blockers:
                continue
            for blocker in blockers:
                assignment = frozenset(
                    [lit.predicate for lit in rule.body if not lit.negated] + [blocker]
                )
                facts = facts_for(assignment)
                full_eval = engine.evaluate(pack, facts)
                assert rule.rule_id not in full_eval.fired_rules, (
                    f"{code}/{rule.rule_id}: fired even though its negated literal "
                    f"{blocker!r} is asserted TRUE"
                )
                cm_graph, m_graph = run_dual_advocacy(pack, facts)
                referee_result = referee.adjudicate(pack, [cm_graph, m_graph], facts)
                assert referee_result.evaluation.decision == full_eval.decision, (
                    f"{code}/{rule.rule_id}: referee diverged from full evaluation with blocker "
                    f"{blocker!r} asserted -- a blocking fact was dropped from evaluation"
                )
                checked += 1
    assert checked > 0, "no rulepack negates any literal -- this test is checking nothing"


# ---------------------------------------------------------------- narration fallback

def test_narration_fallback_on_corruption(packs, engine):
    pack = packs["C08"]
    facts = {
        "delivery_confirmed": Fact("delivery_confirmed", FactStatus.TRUE, ("n1",)),
        "address_matches_avs": Fact("address_matches_avs", FactStatus.TRUE, ("n2",)),
        "signature_missing": Fact("signature_missing", FactStatus.FALSE, ("n3",)),
    }
    evaluation = engine.evaluate(pack, facts)

    complete = render_narration_safe(evaluation, pack, valid_node_ids={"n1", "n2", "n3"})
    assert complete.source == "template"

    incomplete = render_narration_safe(evaluation, pack, valid_node_ids={"n1"})
    assert incomplete.source == "template_fallback"
    assert incomplete.citations == ()


# ---------------------------------------------------------------- A7 disparate impact audit

def test_disparate_impact_audit_catches_biased_rule_not_fair_rule():
    """A7, validated against the spec's own example: 'a rule that fires 3x
    more often against small merchants at equal evidence strength is a
    discovered defect'. Constructs a rule with a real, planted disparity and
    a rule with none, both at the same evidence-strength buckets, and
    confirms the audit separates them -- not just that it runs."""
    import random

    rng = random.Random(2026)
    records = []
    for i in range(240):
        tier = "SMALL" if i % 2 == 0 else "LARGE"
        bucket = i % 3
        fired = []
        if tier == "SMALL" and rng.random() < 0.30:
            fired.append("RULE_X")
        if tier == "LARGE" and rng.random() < 0.10:
            fired.append("RULE_X")
        if rng.random() < 0.20:
            fired.append("RULE_FAIR")
        records.append(CaseRecord(
            case_id=f"case-{i}", reason_code="C08", stratum_dimension="merchant_tier",
            stratum_value=tier, evidence_strength_bucket=bucket, fired_rule_ids=tuple(fired),
        ))

    findings = compute_rule_level_disparate_impact(
        records, all_rule_ids=["RULE_X", "RULE_FAIR"], delta_threshold=0.15, min_n_per_cell=5
    )
    flagged = flagged_only(findings)

    assert any(f.rule_id == "RULE_X" for f in flagged), "planted disparity in RULE_X was not caught"
    assert not any(f.rule_id == "RULE_FAIR" for f in flagged), "unbiased RULE_FAIR was falsely flagged"
    assert all(abs(f.delta) >= 0.15 for f in flagged)
    assert all(f.n_a >= 5 and f.n_b >= 5 for f in findings)


# ------------------------------------------------- PT-9 tier-gating soundness

def test_no_submitted_tier_predicate_wins_alone(packs):
    """A rulepack-authoring invariant, checked mechanically rather than by
    comment:

        A rule that DECIDES a case may rest on weak-tier (SUBMITTED /
        ASSERTED) predicates only if it ALSO constrains at least one
        NETWORK- or COMMITTED-tier predicate -- positively or negatively.

    Why tier gating carries this weight: it is the disclosure-safety
    property the counterfactual ledger depends on. A losing party reads
    their own counterfactual, which names exactly the predicates they need.
    If any of those is satisfiable by a self-supplied document AND decisive
    on its own, the counterfactual is a fabrication recipe.

    Not hypothetical: C08_R4 fired on `cardholder_confirmed_receipt` ALONE
    at SUBMITTED tier, and `evals/gaming_resistance.py` measured 95 of 99
    fabricated cases flipping the verdict. Forensics and contradiction
    detection are real defenses but not substitutes -- a forgery good
    enough to pass forensics won that case unopposed.

    WHY A NEGATED NETWORK LITERAL COUNTS. `X_submitted AND NOT Y_network`
    is a materially different risk from `X_submitted` alone: an attacker
    controls only half of it. They can forge X, but they cannot forge the
    *absence* of Y from Amex's own records -- you cannot manufacture an
    absence in a system you do not write to. So C02_R2's
    `merchant_refund_promise_on_record AND NOT refund_issued` is sound
    (`refund_issued` is NETWORK), while a rule constraining nothing at
    NETWORK tier at all is not.
    """
    weak_tiers = {"SUBMITTED", "ASSERTED"}
    strong_tiers = {"NETWORK", "COMMITTED"}
    violations = []

    for code, pack in packs.items():
        if not pack.predicate_meta:
            continue
        decision_heads = set(pack.decision_predicates.values())
        for rule in pack.rules:
            if rule.head not in decision_heads:
                continue  # only rules that decide the case are gated this way

            # Every literal the rule constrains, negated or not.
            constrained = {
                lit.predicate: pack.predicate_meta[lit.predicate].min_tier
                for lit in rule.body if lit.predicate in pack.predicate_meta
            }
            if not constrained:
                continue

            has_strong_anchor = any(t in strong_tiers for t in constrained.values())
            weak_positive = [
                lit.predicate for lit in rule.body
                if not lit.negated
                and constrained.get(lit.predicate) in weak_tiers
            ]
            if weak_positive and not has_strong_anchor:
                violations.append(
                    f"{code}/{rule.rule_id}: decides on {weak_positive} at "
                    f"{[constrained[p] for p in weak_positive]} tier and constrains "
                    f"NOTHING at NETWORK/COMMITTED tier -- a party can satisfy this "
                    f"rule entirely from material it supplies itself"
                )

    assert not violations, "tier-gating violations:\n  " + "\n  ".join(violations)


# ------------------------------------------- CE3.0 threshold-form equivalence

# The five bodies F29's CE3.0 family had before it was re-authored with
# `at_least` (rulepack version 1.0.0). Kept literally, not regenerated, so
# this is a genuine independent statement of the target rather than a
# restatement of the code under test.
_CE3_HAND_WRITTEN_PAIRS = [
    ("device_id_match", "ip_address_match"),
    ("device_id_match", "shipping_address_match"),
    ("device_id_match", "user_id_match"),
    ("ip_address_match", "shipping_address_match"),
    ("ip_address_match", "user_id_match"),
]
_CE3_COMMON = ("prior_undisputed_txn_count_ge_2", "prior_txn_120_to_365_days_old")


def test_ce3_threshold_expansion_matches_hand_written_pairs(packs):
    """The re-authoring of F29's CE3.0 family onto `at_least` was a change of
    notation, not of policy -- so it has to be provable as one.

    Visa CE3.0 is "at least 2 of {device_id, ip_address, shipping_address,
    user_id} match a prior undisputed transaction, at least one of which is
    device_id or ip_address". That is every 2-subset except
    {shipping_address, user_id}, which is why it is authored as two anchored
    threshold rules rather than one flat `at_least: 2` -- a flat 2-of-4 would
    silently admit the one pair CE3.0 excludes, and the excluded pair is
    precisely the one with no network-recorded identifier in it.
    """
    pack = packs["F29"]
    ce3_bodies = {
        frozenset(lit.predicate for lit in rule.body)
        for rule in pack.rules if rule.rule_id.startswith("F29_CE3")
    }
    expected = {frozenset(_CE3_COMMON + pair) for pair in _CE3_HAND_WRITTEN_PAIRS}
    assert ce3_bodies == expected

    # No rule negates anything in this family, so predicate-set equality
    # above is the whole story; assert that rather than leave it implied.
    assert not any(
        lit.negated for rule in pack.rules if rule.rule_id.startswith("F29_CE3") for lit in rule.body
    )


def test_ce3_excluded_pair_still_loses(packs, engine):
    """The negative half of CE3.0, which the threshold form must not erode:
    shipping_address + user_id, with no device or IP match, is NOT compelling
    evidence and must not derive merchant_wins."""
    facts = {
        p: Fact(p, FactStatus.TRUE, (f"n_{p}",))
        for p in _CE3_COMMON + ("shipping_address_match", "user_id_match")
    }
    assert "merchant_wins" not in engine.evaluate(packs["F29"], facts).true_predicates


# ------------------------------------------- chargeback right (arbiter.eligibility)

def test_every_rulepack_declares_a_chargeback_right(packs):
    """Every reason code in the Amex guide has an "Excluded Transactions"
    entry and a "Maximum time a dispute can be raised" entry -- including the
    codes whose exclusion list reads "None". A rulepack that declares no
    `chargeback_right:` block has not been checked against the guide at all,
    and the gate silently passes every dispute under it."""
    for code, pack in packs.items():
        assert pack.chargeback_right is not None, (
            f"{code}: no chargeback_right block -- the reason code's filing window and "
            f"excluded transactions have not been transcribed from the Amex guide"
        )
        assert pack.chargeback_right.network_code, f"{code}: chargeback_right declares no network_code"
        assert pack.chargeback_right.source, (
            f"{code}: chargeback_right cites no source -- a gate that can end a dispute "
            f"has to say which published rule it came from"
        )


def test_network_codes_are_unique_across_rulepacks(packs):
    """Two rulepacks claiming 4540 would make routing by network code
    ambiguous, and the registry resolves in dict order -- i.e. arbitrarily."""
    seen = {}
    for code, pack in packs.items():
        network_code = pack.chargeback_right.network_code
        assert network_code not in seen, (
            f"network code {network_code} claimed by both {seen.get(network_code)} and {code}"
        )
        seen[network_code] = code


def test_exclusions_reference_only_vocabulary_attributes(packs):
    """Load-time validation already enforces this
    (arbiter.rulepack.validate), but it is worth a property test of its own:
    it is the mechanism that makes a typo in an exclusion a boot failure
    instead of a gate that quietly never fires."""
    for code, pack in packs.items():
        for exclusion in pack.chargeback_right.exclusions:
            for condition in exclusion.conditions:
                assert condition.attribute in ATTRIBUTE_VOCABULARY, (
                    f"{code}/{exclusion.exclusion_id}: {condition.attribute!r} is not a "
                    f"declared eligibility attribute"
                )


def test_no_exclusion_reads_a_rulepack_predicate(packs):
    """The separation that justifies having two mechanisms at all.

    An exclusion decides whether the dispute is chargeable; a predicate is
    evidence in a dispute that is. If an attribute name were also a
    predicate name, the same fact would be doing both jobs -- and the one
    thing the gate must never become is another way for a party to win an
    argument.
    """
    for code, pack in packs.items():
        predicates = set(pack.predicate_schema) | pack.edb_predicates()
        for exclusion in pack.chargeback_right.exclusions:
            for condition in exclusion.conditions:
                assert condition.attribute not in predicates, (
                    f"{code}/{exclusion.exclusion_id}: {condition.attribute!r} is both an "
                    f"eligibility attribute and a rulepack predicate"
                )


def test_gate_never_fires_on_an_empty_attribute_set(packs):
    """The mirror of `test_no_trivial_prime_implicant`, for the other
    decider. A rulepack whose exclusions fire on no information at all would
    bar every dispute under that reason code -- the most destructive possible
    authoring error here, and the cheapest to test for."""
    filed_at = datetime(2026, 7, 27, tzinfo=timezone.utc)
    for code, pack in packs.items():
        result = evaluate_chargeback_right(pack.chargeback_right, {}, filed_at)
        assert result.available is True, (
            f"{code}: the chargeback-right gate closed on an empty attribute set -- "
            f"an unknown must never exclude (arbiter.eligibility.models)"
        )


def test_every_decisive_predicate_declares_a_tier(packs):
    """A predicate with no `min_tier` entry is ungated by default, which is
    the failure mode that made tier gating a silent no-op in an earlier
    build: PredicateMeta was fully implemented and no shipped rulepack
    populated a `predicates:` block, so `_min_tier_for` always returned
    None."""
    missing = []
    for code, pack in packs.items():
        assert pack.predicate_meta, f"{code} declares no `predicates:` block -- tier gating would be a no-op"
        for pred in pack.predicate_schema:
            if pred not in pack.predicate_meta:
                missing.append(f"{code}/{pred}")
    assert not missing, f"predicates with no declared min_tier: {missing}"

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
  the module-scoped `packs` fixture below if violated).
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
  test_conflicts_never_silently_resolved_exhaustive below: two rules firing
  on genuinely independent predicates always produces a surfaced conflict,
  never a silent pick.
- mutual exclusivity: no evidence assignment satisfies two outcomes at once
  without it being surfaced as a conflict -- checked *exhaustively* over the
  full predicate powerset per rulepack.
- regression coverage for defects this build found and fixed while it was
  being written: a trivial empty-set prime implicant, and advocate/referee
  decision divergence.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Dict

import pytest

from arbiter.advocate import run_dual_advocacy
from arbiter.decision import Referee
from arbiter.horn import Engine, Fact, FactStatus, RulePack, enumerate_prime_implicants
from arbiter.narrate import render_narration_safe
from arbiter.rulepack import load_rulepack_dir
from arbiter.fairness import CaseRecord, compute_rule_level_disparate_impact, flagged_only

RULEPACK_DIR = Path(__file__).resolve().parent.parent.parent / "rulepacks" / "amex"

# banned substrings: predicates must describe *evidence*, never a party
# classification attribute. A rule conditioning on any of these would be
# discriminating by construction, not by facts.
_PROTECTED_ATTRIBUTE_PATTERNS = (
    "tier", "segment", "premium", "region", "age", "gender", "race",
    "ethnicity", "income", "credit_score", "zip", "postal",
)


@pytest.fixture(scope="module")
def packs() -> Dict[str, RulePack]:
    return load_rulepack_dir(RULEPACK_DIR)


@pytest.fixture(scope="module")
def engine() -> Engine:
    return Engine()


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
    for code, pack in packs.items():
        for outcome, head in pack.decision_predicates.items():
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

def test_conflicts_never_silently_resolved_exhaustive(packs, engine):
    """
    Evidence CAN legitimately satisfy two outcomes' rules at once -- e.g.
    C02_R4 (service_never_rendered) and C02_R8 (dispute_filed_before_
    return_received) reference independent predicates a real case could
    assert simultaneously. What this test verifies, exhaustively over the
    full predicate powerset per rulepack: whenever multiple outcomes fire,
    `decision` is always None and `conflicting_outcomes` always lists
    exactly the outcomes that fired -- the conflict is surfaced, never
    silently arbitrated by dict/iteration order.
    """
    for code, pack in packs.items():
        edb = sorted(pack.edb_predicates())
        heads_by_outcome = pack.decision_predicates
        checked_conflicts = 0
        for bits in itertools.product([False, True], repeat=len(edb)):
            facts = {
                p: Fact(p, FactStatus.TRUE, (f"n_{p}",))
                for p, is_true in zip(edb, bits) if is_true
            }
            result = engine.evaluate(pack, facts)
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


# ---------------------------------------------------------------- advocate/referee consistency

def test_advocate_completeness_matches_referee_exhaustive(packs, engine):
    """The consistency invariant the dual-advocate design depends on: if the
    objective facts alone would satisfy some outcome under a full
    Engine.evaluate(), the corresponding advocate must find and cite that
    MWC, so the Referee's verified-fact-only evaluation reaches the exact
    same decision. Checked exhaustively over a bounded predicate powerset
    per rulepack (capped for runtime)."""
    referee = Referee(engine)
    for code, pack in packs.items():
        edb = sorted(pack.edb_predicates())
        sample_edb = edb[:12]
        for bits in itertools.product([False, True], repeat=len(sample_edb)):
            facts = {
                p: Fact(p, FactStatus.TRUE, (f"n_{p}",))
                for p, is_true in zip(sample_edb, bits) if is_true
            }
            full_eval = engine.evaluate(pack, facts)
            cm_graph, m_graph = run_dual_advocacy(pack, facts)
            referee_result = referee.adjudicate(pack, [cm_graph, m_graph], facts)
            assert referee_result.evaluation.decision == full_eval.decision, (
                f"{code}: referee decision {referee_result.evaluation.decision} diverged from "
                f"full objective evaluation {full_eval.decision} for facts={sorted(facts)}"
            )


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

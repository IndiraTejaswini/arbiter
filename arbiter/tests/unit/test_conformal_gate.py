"""
Conformal abstention gate: the two guarantees that were missing.

Both tests below correspond to a measured defect, not a hypothetical:

1. The service seeded 150 Gaussian random scores per reason code at boot so
   the gate would have a threshold. Reproducing that RNG gives
   q_hat = 0.6883 at alpha=0.05.
2. Against that threshold, a case with a CRITICAL unresolved contradiction
   scores nonconformity 0.265 and auto-resolves -- because the only channel
   a contradiction had into the outcome was `contradiction_clarity`, worth
   0.25 of the weighted confidence, which an otherwise-complete case simply
   outvotes.
"""

from __future__ import annotations

import pytest

from arbiter.decision.confidence import ConfidenceVector
from arbiter.decision.conformal import ConformalAbstentionGate


def _strong_case() -> ConfidenceVector:
    """Complete evidence, high provenance, wide margin -- everything the
    gate likes -- but the graph contradicts itself."""
    return ConfidenceVector(
        completeness=1.0, extraction_confidence=0.9,
        contradiction_clarity=0.0,  # CRITICAL contradiction
        decision_margin=1.0, has_decision=True,
    )


def _calibrated_gate(n: int = 150) -> ConformalAbstentionGate:
    gate = ConformalAbstentionGate(alpha=0.05, min_n_for_guarantee=100)
    for i in range(n):
        gate.add_calibration_example("C08", i / n * 0.9)
    return gate


def test_critical_contradiction_blocks_auto_resolution_regardless_of_score():
    """The measured regression. Without the hard block this case scores
    0.265 against a threshold near 0.69 and auto-resolves."""
    gate = _calibrated_gate()
    confidence = _strong_case()
    assert confidence.nonconformity() < 0.30, "precondition: this case scores well"

    unguarded = gate.decide("C08", confidence)
    assert unguarded.auto_resolve, (
        "precondition: on score alone this case passes the gate -- which is exactly "
        "why a per-case safety invariant is needed on top of the population guarantee"
    )

    guarded = gate.decide("C08", confidence, blocking_contradiction="CRITICAL")
    assert not guarded.auto_resolve
    assert guarded.hard_blocked
    assert "CRITICAL" in guarded.reason


def test_high_severity_contradiction_also_blocks():
    gate = _calibrated_gate()
    decision = gate.decide("C08", _strong_case(), blocking_contradiction="HIGH")
    assert not decision.auto_resolve and decision.hard_blocked


def test_no_contradiction_does_not_block():
    """The block must be targeted: a clean case still auto-resolves, or the
    gate is just 'escalate everything' wearing a guarantee's clothes."""
    gate = _calibrated_gate()
    clean = ConfidenceVector(
        completeness=1.0, extraction_confidence=0.9, contradiction_clarity=1.0,
        decision_margin=1.0, has_decision=True,
    )
    decision = gate.decide("C08", clean, blocking_contradiction=None)
    assert decision.auto_resolve and not decision.hard_blocked


def test_uncalibrated_reason_code_escalates_rather_than_guessing():
    """Previously an uncalibrated stratum fell back to a pooled threshold
    computed over fabricated boot data, producing confident-looking
    auto-resolution with nothing behind it."""
    gate = ConformalAbstentionGate(alpha=0.05, min_n_for_guarantee=100,
                                   require_real_calibration=True)
    for i in range(10):  # far below min_n
        gate.add_calibration_example("C08", i / 100)

    decision = gate.decide("C08", ConfidenceVector(
        completeness=1.0, extraction_confidence=1.0, contradiction_clarity=1.0,
        decision_margin=1.0, has_decision=True,
    ))
    assert not decision.auto_resolve
    assert decision.hard_blocked
    assert "calibration" in decision.reason.lower()


def test_a_perfect_case_still_escalates_with_zero_calibration():
    """The strongest possible case must not auto-resolve when the gate has
    no basis for a coverage claim at all."""
    gate = ConformalAbstentionGate(require_real_calibration=True)
    decision = gate.decide("F29", ConfidenceVector(
        completeness=1.0, extraction_confidence=1.0, contradiction_clarity=1.0,
        decision_margin=1.0, has_decision=True,
    ))
    assert not decision.auto_resolve


def test_load_calibration_reports_what_it_loaded():
    """Silence is how the fabricated-seed problem survived: nothing ever
    said what the threshold was standing on."""
    gate = ConformalAbstentionGate(min_n_for_guarantee=5)
    counts = gate.load_calibration([("C08", 0.1), ("C08", 0.2), ("F29", 0.3)])
    assert counts == {"C08": 2, "F29": 1}
    assert gate.calibration_size("C08") == 2
    assert not gate.is_calibrated("C08")


def test_calibration_pool_stays_sorted_on_insert():
    """The quantile is an order statistic and the pool grows with every
    analyst review; sorting per request was an unbounded latency
    regression on the hot path."""
    gate = ConformalAbstentionGate(min_n_for_guarantee=1)
    for score in (0.9, 0.1, 0.5, 0.3, 0.7):
        gate.add_calibration_example("C08", score)
    assert gate._calibration["C08"] == sorted(gate._calibration["C08"])
    assert gate.threshold_for("C08") == pytest.approx(0.9)


def test_no_decision_never_auto_resolves():
    gate = _calibrated_gate()
    undecided = ConfidenceVector(
        completeness=1.0, extraction_confidence=1.0, contradiction_clarity=1.0,
        decision_margin=0.0, has_decision=False,
    )
    assert not gate.decide("C08", undecided).auto_resolve


def test_drift_inflation_only_ever_tightens():
    gate = _calibrated_gate()
    base = gate.threshold_for("C08")
    gate.set_drift_inflation("C08", 2.0)
    assert gate.threshold_for("C08") < base
    with pytest.raises(ValueError):
        gate.set_drift_inflation("C08", 0.5)


# -- Calibration-pool contamination ---------------------------------------
#
# A third defect in the same family as the two above, found by running the
# stack rather than by reading it. The gate reported `calibrated: true` for
# every reason code, logged a healthy-looking threshold, and abstained on
# nothing -- because the threshold was 1.0000.
#
# `decide()` returns before the threshold is consulted when the referee
# reached no decision, so the population the threshold governs is "cases with
# a decision". But every producer of calibration data -- the seeder, the
# coverage eval, and the analyst-review route -- fed it EVERY case, including
# no-decision ones. Those score exactly 1.0 (`confidence()` returns 0.0 with
# `has_decision=False`), and nonconformity is bounded by 1.0.
#
# Measured on the shipped generator: 16% / 41% / 47% of cases per reason code
# reach no decision. Any share above alpha drags the (1-alpha) quantile to
# the top of the range, and `score <= 1.0` is universally true.
#
# The analyst route was the worst of the three: escalated cases are
# disproportionately no-decision, and escalated cases are exactly what humans
# review -- so the gate got MORE permissive the more review was done, which
# is the failure `arbiter.decision.review_sampling` exists to prevent,
# reached by a route its weighting cannot see.


def _decided(score: float) -> ConfidenceVector:
    """A vector with a decision whose nonconformity is `score`."""
    return ConfidenceVector(
        completeness=1.0 - score, extraction_confidence=1.0 - score,
        contradiction_clarity=1.0 - score, decision_margin=1.0 - score,
        has_decision=True,
    )


def test_no_decision_scores_exactly_one():
    """The mechanism. Nonconformity is bounded by 1.0, so this is not a high
    score -- it is the maximum, and a pool of them cannot be out-voted."""
    no_decision = ConfidenceVector(
        completeness=1.0, extraction_confidence=1.0,
        contradiction_clarity=1.0, decision_margin=1.0, has_decision=False,
    )
    assert no_decision.confidence() == 0.0
    assert no_decision.nonconformity() == 1.0


def test_no_decision_cases_in_the_pool_make_the_gate_inert():
    """The regression, stated as the arithmetic that caused it. 20% of the
    pool at the maximum is four times alpha, so the 95th percentile lands on
    it and nothing can ever be rejected."""
    gate = ConformalAbstentionGate(alpha=0.05, min_n_for_guarantee=100)
    for i in range(160):
        gate.add_calibration_example("C08", i / 160 * 0.5)   # decided cases
    for _ in range(40):
        gate.add_calibration_example("C08", 1.0)             # no-decision cases

    assert gate.saturated_fraction("C08") == pytest.approx(0.2)
    assert gate.is_inert("C08"), "a threshold at the top of the range rejects nothing"

    # And the consequence: a case that should clearly escalate does not.
    terrible = _decided(0.95)
    assert gate.decide("C08", terrible).auto_resolve, (
        "demonstrating the defect: with a contaminated pool even a near-worst "
        "case auto-resolves"
    )


def test_a_clean_pool_produces_a_gate_that_actually_rejects():
    """The fix, from the other side. Same decided cases, contamination
    removed: the threshold lands inside the range and the gate discriminates."""
    gate = ConformalAbstentionGate(alpha=0.05, min_n_for_guarantee=100)
    for i in range(160):
        gate.add_calibration_example("C08", i / 160 * 0.5)

    assert gate.saturated_fraction("C08") == 0.0
    assert not gate.is_inert("C08")
    assert gate.threshold_for("C08") < 1.0

    assert gate.decide("C08", _decided(0.10)).auto_resolve
    assert not gate.decide("C08", _decided(0.95)).auto_resolve, (
        "an uncontaminated gate must still reject a bad case"
    )


def test_seeder_excludes_no_decision_cases():
    """The producer-side fix. `_score_one` reports `has_decision` and the
    seeding loop banks only cases the gate could be asked to rule on."""
    import inspect

    from scripts import seed_calibration

    source = inspect.getsource(seed_calibration.main)
    assert "if not has_decision:" in source, (
        "seed_calibration must skip cases the referee could not decide -- they score "
        "1.0 and pin the quantile to the top of the range"
    )
    # `from __future__ import annotations` makes this a string, not a type.
    assert "bool" in str(inspect.signature(seed_calibration._score_one).return_annotation), (
        "_score_one must report has_decision so the caller can filter on it"
    )


def test_analyst_review_of_an_undecided_case_contributes_no_sample():
    """The worst of the three producers, pinned. Escalated cases are
    disproportionately no-decision and are exactly what analysts review."""
    from pathlib import Path

    source = Path(__file__).resolve().parents[2].joinpath(
        "src/arbiter/api/routes/disputes.py"
    ).read_text(encoding="utf-8")
    assert "if decision.outcome is m.OutcomeEnum.INSUFFICIENT_EVIDENCE:" in source, (
        "review_decision must not add a calibration sample for a case the referee "
        "never decided -- that is the feedback loop that made the gate inert"
    )

"""
Calibration selection bias, and the two mechanisms that correct it.

The defect: analyst reviews only ever came from ESCALATED cases -- the
high-nonconformity tail by construction. Feeding only that tail back into
the split-conformal pool inflates the (1-alpha) quantile monotonically, so
**the more human review was done, the more permissive the gate became**.
Split-conformal validity requires the calibration set be exchangeable with
the deployment distribution, and a review-only pool is not.

The fix has two halves and both are needed: sample auto-resolved cases for
audit (so the pool sees the region escalation never visits), and weight
every sample by its inverse selection probability (because the two strata
are still reviewed at wildly different rates).
"""

from __future__ import annotations

import pytest

from arbiter.decision.confidence import ConfidenceVector
from arbiter.decision.conformal import ConformalAbstentionGate
from arbiter.decision.review_sampling import (
    ESCALATED_SELECTION_PROBABILITY,
    calibration_weight,
    select_for_review,
)

_SALT = "test-salt"


# -- Selection ------------------------------------------------------------


def test_escalated_cases_are_always_reviewed():
    """That IS the escalation path -- probability 1.0 by definition."""
    selection = select_for_review("case-1", auto_resolved=False, audit_rate=0.05, salt=_SALT)
    assert selection.selected_for_review
    assert selection.selection_probability == ESCALATED_SELECTION_PROBABILITY == 1.0
    assert selection.inverse_probability_weight == 1.0


def test_some_auto_resolved_cases_are_sampled():
    """Without this the calibration pool never sees the low-nonconformity
    region at all, which is precisely the bias."""
    sampled = [
        select_for_review(f"case-{i}", auto_resolved=True, audit_rate=0.2, salt=_SALT).selected_for_review
        for i in range(400)
    ]
    rate = sum(sampled) / len(sampled)
    assert 0.15 < rate < 0.25, f"observed audit rate {rate:.3f} is far from the configured 0.2"


def test_selection_is_deterministic_per_case():
    """A retry must not change whether a case is audited, and the decision
    must be reproducible from the case id during an audit."""
    first = select_for_review("case-abc", auto_resolved=True, audit_rate=0.5, salt=_SALT)
    second = select_for_review("case-abc", auto_resolved=True, audit_rate=0.5, salt=_SALT)
    assert first.selected_for_review == second.selected_for_review


def test_selection_is_keyed_so_it_cannot_be_ground_for():
    """With an unkeyed hash, a party able to influence a case identifier
    could search for one that lands outside the audit window."""
    a = [select_for_review(f"c{i}", True, 0.5, "salt-a").selected_for_review for i in range(50)]
    b = [select_for_review(f"c{i}", True, 0.5, "salt-b").selected_for_review for i in range(50)]
    assert a != b, "selection must depend on the deployment key, not the case id alone"


def test_zero_audit_rate_disables_sampling_and_says_why():
    selection = select_for_review("case-1", auto_resolved=True, audit_rate=0.0, salt=_SALT)
    assert not selection.selected_for_review
    assert selection.selection_probability == 0.0
    assert "biased subsample" in selection.reason


def test_audit_rate_is_clamped_to_a_probability():
    assert select_for_review("c", True, 5.0, _SALT).selection_probability == 1.0
    assert select_for_review("c", True, -1.0, _SALT).selection_probability == 0.0


# -- Weights --------------------------------------------------------------


def test_an_audit_sample_stands_in_for_the_cases_like_it():
    """A case reviewed at a 5% rate represents ~20 that were not."""
    assert calibration_weight(0.05) == pytest.approx(20.0)
    assert calibration_weight(1.0) == 1.0


def test_legacy_rows_without_a_probability_are_kept_not_discarded():
    """They are genuine analyst adjudications; discarding them would cost
    more coverage than their residual bias costs."""
    assert calibration_weight(None) == 1.0
    assert calibration_weight(0.0) == 1.0


# -- The gate -------------------------------------------------------------


def _confidence(nonconformity: float) -> ConfidenceVector:
    """A vector whose nonconformity is exactly `nonconformity`."""
    return ConfidenceVector(
        completeness=1.0 - nonconformity, extraction_confidence=1.0 - nonconformity,
        contradiction_clarity=1.0 - nonconformity, decision_margin=1.0 - nonconformity,
        has_decision=True,
    )


def test_unweighted_quantile_still_matches_the_classical_recipe():
    """The weighted quantile must reduce EXACTLY to the standard
    split-conformal order statistic when all weights are equal -- otherwise
    this change would silently alter every existing calibration."""
    gate = ConformalAbstentionGate(alpha=0.05, min_n_for_guarantee=1)
    for i in range(100):
        gate.add_calibration_example("C08", i / 100.0)
    # ceil((100+1) * 0.95) = 96 -> the 96th smallest score, i.e. index 95.
    assert gate.threshold_for("C08") == pytest.approx(0.95)


def test_biased_pool_inflates_the_threshold_and_weighting_corrects_it():
    """THE regression, demonstrated end to end.

    An escalation-only pool is all high-nonconformity scores, so its
    quantile sits far above the true deployment quantile -- the gate
    becomes permissive. Adding audit samples at their true rate, weighted
    by inverse probability, pulls it back toward reality.
    """
    # Deployment truth: 90% of cases score low and auto-resolve; 10% score
    # high and escalate. The escalated tail is WIDE, which is what makes the
    # bias bite -- an escalation-only pool's 95th percentile sits at the top
    # of that tail rather than at the 95th percentile of the population.
    low = [0.05 + i * 0.001 for i in range(180)]     # 0.05 .. 0.23
    high = [0.40 + i * 0.0275 for i in range(20)]    # 0.40 .. 0.92

    # (a) escalation-only: the gate never sees the 90% that auto-resolved.
    biased = ConformalAbstentionGate(alpha=0.05, min_n_for_guarantee=1)
    for score in high:
        biased.add_calibration_example("C08", score, weight=1.0)

    # (b) escalation + a 10% audit sample of the auto-resolved population,
    #     each weighted by 1/0.10 = 10 so it stands in for the nine like it
    #     that were not reviewed.
    corrected = ConformalAbstentionGate(alpha=0.05, min_n_for_guarantee=1)
    for score in high:
        corrected.add_calibration_example("C08", score, weight=1.0)
    for score in low[::10]:  # a 10% sample of the low stratum
        corrected.add_calibration_example("C08", score, weight=10.0)

    biased_threshold = biased.threshold_for("C08")
    corrected_threshold = corrected.threshold_for("C08")

    assert biased_threshold > corrected_threshold, (
        f"an escalation-only pool must produce a MORE permissive threshold "
        f"({biased_threshold:.3f}) than a correctly weighted one "
        f"({corrected_threshold:.3f}) -- that gap IS the selection bias"
    )

    # The practical consequence: a case scoring between the two thresholds
    # is waved through by the biased gate and escalated by the corrected one.
    between = (biased_threshold + corrected_threshold) / 2
    assert biased.decide("C08", _confidence(between)).auto_resolve
    assert not corrected.decide("C08", _confidence(between)).auto_resolve


def test_effective_sample_size_reports_the_cost_of_weighting():
    """A pool dominated by a few heavy weights carries less information
    than its raw count. An operator deciding whether to trust the guarantee
    should see that rather than a flattering `n`."""
    gate = ConformalAbstentionGate(alpha=0.05, min_n_for_guarantee=1)
    for i in range(99):
        gate.add_calibration_example("C08", i / 100.0, weight=1.0)
    gate.add_calibration_example("C08", 0.99, weight=1000.0)  # one dominant sample

    assert gate.calibration_size("C08") == 100
    assert gate.effective_sample_size("C08") < 10, (
        "one sample carrying almost all the weight must collapse the effective n"
    )


def test_calibration_is_gated_on_effective_n_not_raw_count():
    """200 samples whose weights are dominated by a handful do not support
    the guarantee that 200 evenly-weighted samples would."""
    gate = ConformalAbstentionGate(alpha=0.05, min_n_for_guarantee=100)
    for i in range(200):
        gate.add_calibration_example("C08", i / 200.0, weight=1.0 if i else 100_000.0)

    assert gate.calibration_size("C08") == 200
    assert not gate.is_calibrated("C08")
    assert not gate.decide("C08", _confidence(0.1)).auto_resolve


def test_evenly_weighted_pool_is_calibrated_at_its_raw_count():
    gate = ConformalAbstentionGate(alpha=0.05, min_n_for_guarantee=100)
    for i in range(120):
        gate.add_calibration_example("C08", i / 120.0, weight=1.0)
    assert gate.effective_sample_size("C08") == pytest.approx(120.0)
    assert gate.is_calibrated("C08")


def test_load_calibration_accepts_weighted_and_unweighted_rows():
    """Legacy rows have no recorded probability; both shapes must load."""
    gate = ConformalAbstentionGate(min_n_for_guarantee=1)
    counts = gate.load_calibration([("C08", 0.1, 20.0), ("C08", 0.2, 1.0)])
    assert counts == {"C08": 2}

    unweighted = ConformalAbstentionGate(min_n_for_guarantee=1)
    assert unweighted.load_calibration([("C08", 0.1), ("C08", 0.2)]) == {"C08": 2}

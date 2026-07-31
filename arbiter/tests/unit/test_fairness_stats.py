"""
Statistical rigour in the A7 disparate-impact audit.

The audit flagged any 15-point firing-rate gap with `min_n_per_cell = 5`
and no significance test at all. Two consequences, both demonstrated below:

  - At n=5, one case is a 20-point delta, so a SINGLE case was reported as
    "a discovered defect with a line number."
  - The audit runs roughly 430 simultaneous comparisons across the three
    shipped rulepacks. With no multiplicity correction, false findings at
    any conventional alpha are arithmetically guaranteed.

An audit that reports confident false positives is worse than one that
reports nothing: it burns reviewer attention, and once reviewers learn the
flags are noise it trains them to ignore the real ones.
"""

from __future__ import annotations

import random

import pytest

from arbiter.fairness import (
    CaseRecord,
    assess_power,
    benjamini_hochberg,
    compute_rule_level_disparate_impact,
    flagged_only,
    two_proportion_p_value,
    wilson_interval,
)

# -- Wilson intervals -----------------------------------------------------


def test_wilson_interval_is_bounded_at_the_extremes():
    """The normal approximation reports a zero-width interval for 0/12,
    which is exactly the cell size this audit encounters."""
    ci = wilson_interval(0, 12)
    assert ci.point == 0.0
    assert ci.low == 0.0
    assert ci.high > 0.0, "0/12 does not mean 'the true rate is certainly 0'"
    assert ci.high < 0.30


def test_wilson_interval_never_leaves_the_unit_interval():
    for successes, n in [(0, 5), (5, 5), (1, 3), (99, 100), (1, 1000)]:
        ci = wilson_interval(successes, n)
        assert 0.0 <= ci.low <= ci.high <= 1.0


def test_wilson_interval_narrows_with_more_data():
    small = wilson_interval(5, 10)
    large = wilson_interval(500, 1000)
    assert (large.high - large.low) < (small.high - small.low)


def test_wilson_interval_on_an_empty_cell_claims_nothing():
    ci = wilson_interval(0, 0)
    assert (ci.low, ci.high) == (0.0, 1.0), "no data must mean no claim, not a point estimate"


# -- Two-proportion test --------------------------------------------------


def test_identical_rates_are_not_significant():
    assert two_proportion_p_value(50, 100, 50, 100) == pytest.approx(1.0)


def test_a_large_well_powered_difference_is_significant():
    p = two_proportion_p_value(90, 100, 40, 100)
    assert p < 0.001


def test_the_same_delta_is_not_significant_at_tiny_n():
    """0.9 vs 0.4 on 100 cases each is overwhelming; the identical delta on
    5 cases each is not evidence of anything. The old audit could not tell
    these apart -- it compared deltas only."""
    big_n = two_proportion_p_value(90, 100, 40, 100)
    small_n = two_proportion_p_value(4, 5, 2, 5)
    assert big_n < 0.001
    assert small_n > 0.05


def test_degenerate_cells_return_no_evidence_rather_than_dividing_by_zero():
    assert two_proportion_p_value(0, 10, 0, 10) == 1.0  # rule never fired
    assert two_proportion_p_value(10, 10, 10, 10) == 1.0  # rule always fired
    assert two_proportion_p_value(1, 0, 1, 10) == 1.0  # empty cell


# -- Benjamini-Hochberg ---------------------------------------------------


def test_bh_controls_false_discoveries_among_pure_noise():
    """430 comparisons of identical populations. Uncorrected, ~5% of them
    cross p<0.05 by construction; BH must leave essentially none."""
    rng = random.Random(7)
    p_values = [rng.random() for _ in range(430)]
    uncorrected = sum(1 for p in p_values if p <= 0.05)
    corrected = sum(1 for q in benjamini_hochberg(p_values) if q <= 0.05)
    assert uncorrected > 10, "precondition: uncorrected testing finds spurious hits"
    assert corrected == 0


def test_bh_still_finds_a_genuine_signal_buried_in_noise():
    """Correction must not be so conservative it hides real disparate
    impact -- that failure mode is the one that costs someone money."""
    rng = random.Random(11)
    p_values = [1e-9, 1e-8, 1e-8] + [rng.uniform(0.2, 1.0) for _ in range(200)]
    q_values = benjamini_hochberg(p_values)
    assert sum(1 for q in q_values if q <= 0.05) >= 3


def test_bh_q_values_are_monotone_in_p():
    p_values = [0.001, 0.01, 0.04, 0.2, 0.9]
    q = benjamini_hochberg(p_values)
    assert q == sorted(q), "a larger p-value must never get a smaller q-value"
    assert all(0.0 <= v <= 1.0 for v in q)


def test_bh_handles_the_empty_family():
    assert benjamini_hochberg([]) == []


# -- Power ----------------------------------------------------------------


def test_tiny_cells_are_reported_as_underpowered():
    """'We could not tell' and 'we checked and found nothing' are different
    claims. Collapsing them is how an audit launders absence of evidence
    into evidence of absence."""
    assert not assess_power(5, 5, target_effect=0.15).adequately_powered
    assert assess_power(500, 500, target_effect=0.15).adequately_powered


def test_minimum_detectable_effect_shrinks_as_cells_grow():
    assert assess_power(500, 500).detectable_effect < assess_power(30, 30).detectable_effect


# -- The audit end to end -------------------------------------------------


def _records(stratum: str, n: int, fire_rate: float, rule: str, rng) -> list:
    return [
        CaseRecord(
            case_id=f"{stratum}-{i}", reason_code="C08", stratum_dimension="merchant_tier",
            stratum_value=stratum, evidence_strength_bucket=1,
            fired_rule_ids=(rule,) if rng.random() < fire_rate else (),
        )
        for i in range(n)
    ]


def test_a_single_case_in_a_tiny_cell_is_no_longer_a_finding():
    """THE regression: at min_n_per_cell=5, one case out of five was a
    20-point delta and got flagged as a discovered defect."""
    records = [
        CaseRecord("a1", "C08", "merchant_tier", "MICRO", 1, ("C08_R1",)),
        *[CaseRecord(f"a{i}", "C08", "merchant_tier", "MICRO", 1, ()) for i in range(2, 6)],
        *[CaseRecord(f"b{i}", "C08", "merchant_tier", "ENTERPRISE", 1, ()) for i in range(5)],
    ]
    findings = compute_rule_level_disparate_impact(records, ["C08_R1"])
    assert flagged_only(findings) == [], "a 5-case cell cannot support a disparate-impact finding"


def test_a_real_large_disparity_is_still_caught():
    """Correction must not silence the thing the audit exists to find."""
    rng = random.Random(3)
    records = (
        _records("MICRO", 200, 0.25, "C08_R1", rng)
        + _records("ENTERPRISE", 200, 0.75, "C08_R1", rng)
    )
    flagged = flagged_only(compute_rule_level_disparate_impact(records, ["C08_R1"]))
    assert len(flagged) == 1
    finding = flagged[0]
    assert abs(finding.delta) > 0.30
    assert finding.q_value < 0.01
    assert finding.power.adequately_powered
    # The interval must actually exclude parity, not merely have a big point estimate.
    assert finding.ci_a.high < finding.ci_b.low or finding.ci_b.high < finding.ci_a.low


def test_a_fair_rule_is_not_flagged_even_at_large_n():
    """Large n makes tiny, meaningless deltas statistically detectable. The
    practical-significance gate is what stops the audit reporting them."""
    rng = random.Random(5)
    records = (
        _records("MICRO", 400, 0.50, "C08_R1", rng)
        + _records("ENTERPRISE", 400, 0.52, "C08_R1", rng)
    )
    assert flagged_only(compute_rule_level_disparate_impact(records, ["C08_R1"])) == []


def test_findings_carry_their_statistical_evidence():
    rng = random.Random(9)
    records = (
        _records("MICRO", 100, 0.2, "C08_R1", rng)
        + _records("ENTERPRISE", 100, 0.8, "C08_R1", rng)
    )
    findings = compute_rule_level_disparate_impact(records, ["C08_R1"])
    assert findings
    d = findings[0].to_dict()
    for key in ("p_value", "q_value", "ci_a", "ci_b", "power"):
        assert d[key] is not None, f"{key} must be reported -- a bare delta is not a finding"


def test_underpowered_cells_are_reported_not_silently_dropped():
    """They are excluded from `flagged`, but they must still appear so a
    reviewer can see the audit had nothing to work with."""
    rng = random.Random(13)
    records = (
        _records("MICRO", 40, 0.3, "C08_R1", rng)
        + _records("ENTERPRISE", 40, 0.7, "C08_R1", rng)
    )
    findings = compute_rule_level_disparate_impact(records, ["C08_R1"], min_n_per_cell=30)
    assert findings, "cells above min_n must be reported even when underpowered"
    assert all(f.power is not None for f in findings)

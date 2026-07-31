"""
Statistics for the rule-level disparate-impact audit (A7).

Stated as the defect it fixes: `compute_rule_level_disparate_impact` flagged
any firing-rate gap of 15 percentage points with `min_n_per_cell = 5` and
no significance test of any kind. At n=5 a single case is a 20-point delta,
so one case triggered what the architecture document calls "a discovered
defect with a line number." And the audit runs (rules x ordered stratum
pairs x evidence buckets) comparisons -- roughly 430 across the three
shipped rulepacks -- with no multiplicity correction, so at any
conventional alpha dozens of false findings were arithmetically guaranteed.

A fairness apparatus that reports confident false positives is worse than
one that reports nothing: it burns the reviewer's attention and, once they
learn the flags are noise, it trains them to ignore the real ones.

stdlib only -- no scipy. The normal CDF comes from `math.erf`, which is
exact to double precision; the tests here (two-proportion z, Wilson score
interval, Benjamini-Hochberg) are all closed-form.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Sequence


def normal_cdf(z: float) -> float:
    """Phi(z) via the error function."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def two_sided_p_from_z(z: float) -> float:
    return 2.0 * (1.0 - normal_cdf(abs(z)))


@dataclass(frozen=True)
class ProportionCI:
    """Wilson score interval.

    Wilson rather than the textbook normal approximation because the normal
    interval is badly wrong exactly where this audit operates: small cells
    and proportions near 0 or 1, where it produces bounds outside [0,1] and
    coverage far below nominal. A rule that fires 0/12 times in one stratum
    is a realistic cell here, and the normal interval reports a
    zero-width interval for it.
    """

    point: float
    low: float
    high: float
    n: int

    def to_dict(self) -> dict:
        return {
            "rate": round(self.point, 4),
            "ci_low": round(self.low, 4),
            "ci_high": round(self.high, 4),
            "n": self.n,
        }


def wilson_interval(successes: int, n: int, z: float = 1.96) -> ProportionCI:
    if n == 0:
        return ProportionCI(0.0, 0.0, 1.0, 0)
    p = successes / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ProportionCI(point=p, low=max(0.0, centre - half), high=min(1.0, centre + half), n=n)


def two_proportion_p_value(succ_a: int, n_a: int, succ_b: int, n_b: int) -> float:
    """Pooled two-proportion z-test, two-sided.

    Returns 1.0 (no evidence of a difference) when either cell is empty or
    the pooled proportion is degenerate -- the honest answer for a
    comparison that carries no information, rather than a division by zero
    or a spuriously tiny p-value.
    """
    if n_a == 0 or n_b == 0:
        return 1.0
    p_pool = (succ_a + succ_b) / (n_a + n_b)
    if p_pool <= 0.0 or p_pool >= 1.0:
        return 1.0  # the rule fired in every case, or in none -- no contrast
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n_a + 1 / n_b))
    if se == 0.0:
        return 1.0
    z = (succ_a / n_a - succ_b / n_b) / se
    return two_sided_p_from_z(z)


def benjamini_hochberg(p_values: Sequence[float]) -> List[float]:
    """Benjamini-Hochberg step-up FDR control; returns q-values aligned to
    the input order.

    Controlling the false-discovery rate rather than the family-wise error
    rate is the right choice for this audit: the goal is a reviewer queue
    where most flagged rules are genuinely biased, not a guarantee that no
    false flag ever appears. Bonferroni over ~430 comparisons would be so
    conservative it would hide real disparate impact, which is the failure
    mode that actually costs someone money.
    """
    n = len(p_values)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: p_values[i])
    q = [0.0] * n
    prev = 1.0
    # Step up from the largest p-value, enforcing monotonicity.
    for rank_from_end, idx in enumerate(reversed(order)):
        rank = n - rank_from_end  # 1-based rank of this p-value
        value = min(prev, p_values[idx] * n / rank)
        q[idx] = min(1.0, value)
        prev = value
    return q


@dataclass(frozen=True)
class PowerAssessment:
    """Whether a cell could have detected the effect being tested for.

    Reporting "no disparate impact found" from cells too small to detect it
    is a different claim from "no disparate impact exists", and conflating
    them is how a fairness audit launders an absence of evidence into
    evidence of absence.
    """

    detectable_effect: float  # smallest delta this cell could resolve at 80% power
    adequately_powered: bool

    def to_dict(self) -> dict:
        return {
            "min_detectable_effect": round(self.detectable_effect, 4),
            "adequately_powered": self.adequately_powered,
        }


def assess_power(n_a: int, n_b: int, target_effect: float = 0.15,
                 alpha: float = 0.05, power: float = 0.80) -> PowerAssessment:
    """Minimum detectable effect for a two-proportion test at these cell
    sizes, using the conservative p(1-p)=0.25 worst case."""
    if n_a == 0 or n_b == 0:
        return PowerAssessment(1.0, False)
    z_alpha = 1.959963984540054   # two-sided 0.05
    z_power = 0.8416212335729143  # one-sided 0.80
    se = math.sqrt(0.25 * (1 / n_a + 1 / n_b))
    mde = (z_alpha + z_power) * se
    return PowerAssessment(detectable_effect=mde, adequately_powered=mde <= target_effect)

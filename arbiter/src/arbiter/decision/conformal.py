"""
Conformal Abstention Gate (A5, C4: abstention is a first-class output).

Mondrian split-conformal calibration, stratified by reason code: coverage
holds *within* each code, not merely on average across all of them, so the
system can't be systematically wrong on a rare high-value code while looking
fine in aggregate.

Scoping note: full conformal classification produces a *prediction set* that
can contain zero, one, or several candidate labels. This system's decision
space per case is effectively binary (which side wins, or no decision at
all), so the set is collapsed to a binary gate: {decision} -- singleton,
auto-resolve -- versus {} -- empty, abstain. The quantile calibration itself
(q_hat = ceil((n+1)(1-alpha))/n order statistic) is exactly Angelopoulos &
Bates' split-conformal recipe, unmodified.

Two guarantees this module now enforces that it previously did not, both
recorded here because both were live defects rather than missing features:

1. **Calibration must be real.** The service used to seed 150 synthetic
   Gaussian scores per reason code at process boot (`arbiter.main.
   _seed_calibration`) purely so the gate would have *a* threshold. Those
   numbers had no relationship to the pipeline's actual nonconformity
   distribution, and the resulting threshold (q_hat = 0.688 at alpha=0.05)
   was loose enough to auto-resolve essentially everything -- including
   cases carrying a CRITICAL unresolved contradiction. A conformal gate
   calibrated on invented data does not have a weaker coverage guarantee;
   it has none, while reporting one. `require_real_calibration` (default
   on) makes an uncalibrated stratum escalate every case instead.

2. **Some cases must never auto-resolve regardless of score.** Conformal
   coverage is a statistical statement about a population; it is not a
   safety property for an individual case. A case with an unresolved
   HIGH/CRITICAL contradiction is definitionally one a human should see,
   whatever its nonconformity works out to. `decide(blocking_contradiction=
   ...)` is a hard veto ahead of the quantile comparison, in the same
   fail-closed spirit as the rest of the system: statistics decide the
   ordinary case, invariants decide the dangerous one.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from .confidence import ConfidenceVector


@dataclass(frozen=True)
class AbstentionDecision:
    auto_resolve: bool
    nonconformity_score: float
    threshold: Optional[float]
    reason: str
    # True when the gate refused on a hard invariant rather than on the
    # conformal comparison -- surfaced separately so the escalation dossier
    # can say "a human is required here" rather than "the score was close".
    hard_blocked: bool = False

    def to_dict(self) -> dict:
        return {
            "auto_resolve": self.auto_resolve,
            "nonconformity_score": round(self.nonconformity_score, 4),
            "threshold": round(self.threshold, 4) if self.threshold is not None else None,
            "reason": self.reason,
            "hard_blocked": self.hard_blocked,
        }


class ConformalAbstentionGate:
    """Mondrian split-conformal calibration: one score pool, and one
    threshold, per reason code. Requires n>=`min_n_for_guarantee` REAL
    calibration points per stratum before it will auto-resolve anything in
    that stratum."""

    def __init__(
        self,
        alpha: float = 0.05,
        min_n_for_guarantee: int = 100,
        require_real_calibration: bool = True,
    ):
        self.alpha = alpha
        self.min_n_for_guarantee = min_n_for_guarantee
        self.require_real_calibration = require_real_calibration
        # Kept sorted on insert (bisect.insort) rather than sorted on every
        # decide(): the pool grows monotonically with every analyst review,
        # so an O(n log n) sort per request was an unbounded latency
        # regression on the hot path.
        #
        # Each entry is (score, weight). The weight is the inverse
        # probability that the case was reviewed at all -- see
        # `arbiter.decision.review_sampling` for why an unweighted pool
        # made the gate MORE permissive the more human review was done.
        self._calibration: Dict[str, List[Tuple[float, float]]] = {}
        self._pooled: List[Tuple[float, float]] = []
        self._drift_inflation: Dict[str, float] = {}
        self._loaded_from_store: bool = False

    # -- calibration ingest ------------------------------------------------

    def add_calibration_example(
        self, reason_code: str, nonconformity_score: float, weight: float = 1.0,
    ) -> None:
        """`weight` is the Horvitz-Thompson inverse-probability weight: a
        case reviewed at a 5% audit rate stands in for ~20 unreviewed ones.
        Defaults to 1.0 so existing callers keep working unweighted.

        **CONTRACT: only pass scores from cases where the referee actually
        reached a decision.** `decide()` returns before the threshold is ever
        consulted when `has_decision` is False, so the population this
        threshold is applied to is "cases with a decision" — and split
        conformal is only valid when the calibration set is exchangeable with
        that population.

        This is not a nicety. A no-decision case scores exactly 1.0
        (`confidence()` returns 0.0), and nonconformity is bounded by 1.0, so
        admitting them puts a point mass at the top of the range. Measured on
        the shipped generator, 16–47% of cases per reason code reach no
        decision — enough to drag the 95th percentile to 1.0 for all three
        codes, at which point `score <= threshold` is universally true and
        **the gate auto-resolves everything it is asked about**. It does not
        report a weaker guarantee in that state; it reports the same one,
        while abstaining on nothing. See `saturated_fraction`.
        """
        entry = (nonconformity_score, max(0.0, weight))
        bisect.insort(self._calibration.setdefault(reason_code, []), entry)
        bisect.insort(self._pooled, entry)

    def saturated_fraction(self, reason_code: str) -> float:
        """Share of this stratum's pool sitting at maximum nonconformity.

        A diagnostic for exactly one failure: calibration contaminated with
        cases the gate is never asked to rule on. Those score 1.0 by
        construction, and once they exceed `alpha` of the pool the quantile
        pins to 1.0 and the gate silently stops rejecting anything.
        """
        entries = self._calibration.get(reason_code)
        if not entries:
            return 0.0
        total = sum(w for _, w in entries)
        if total <= 0.0:
            return 0.0
        return sum(w for score, w in entries if score >= 1.0) / total

    def is_inert(self, reason_code: str) -> bool:
        """True when this stratum's threshold cannot reject any score.

        Nonconformity is bounded by 1.0, so a threshold at or above 1.0
        auto-resolves every case that reaches the comparison. That is a
        broken gate, not a permissive one, and it must be loud rather than
        inferred from an abstention rate nobody is watching.
        """
        threshold = self.threshold_for(reason_code)
        return threshold is not None and threshold >= 1.0

    def load_calibration(
        self, samples: Iterable[Tuple[str, float]] | Iterable[Tuple[str, float, float]],
    ) -> Dict[str, int]:
        """Bulk-load real calibration scores at startup.

        Accepts `(reason_code, score)` or `(reason_code, score, weight)`.
        Returns per-reason-code counts so the caller can log exactly what
        the gate is standing on -- silence here is how the fabricated-seed
        problem survived as long as it did.
        """
        counts: Dict[str, int] = {}
        for sample in samples:
            reason_code, score = sample[0], sample[1]
            weight = sample[2] if len(sample) > 2 else 1.0  # type: ignore[misc]
            self.add_calibration_example(reason_code, score, weight)
            counts[reason_code] = counts.get(reason_code, 0) + 1
        self._loaded_from_store = True
        return counts

    def calibration_size(self, reason_code: str) -> int:
        """Raw sample count."""
        return len(self._calibration.get(reason_code, []))

    def effective_sample_size(self, reason_code: str) -> float:
        """Kish effective sample size: (sum w)^2 / sum(w^2).

        Reported because weighting is not free -- a pool dominated by a few
        heavily-weighted audit samples carries less information than its raw
        count suggests, and an operator deciding whether to trust the
        guarantee should see that rather than a flattering `n`.
        """
        entries = self._calibration.get(reason_code)
        if not entries:
            return 0.0
        total = sum(w for _, w in entries)
        sum_squares = sum(w * w for _, w in entries)
        return (total * total / sum_squares) if sum_squares > 0 else 0.0

    def is_calibrated(self, reason_code: str) -> bool:
        """Whether this stratum can support a real per-code coverage claim.

        Gated on the EFFECTIVE sample size, not the raw count: 200 samples
        whose weights are dominated by a handful of audit rows do not
        support the guarantee that 200 evenly-weighted samples would.
        """
        return self.effective_sample_size(reason_code) >= self.min_n_for_guarantee

    # -- quantiles ---------------------------------------------------------

    @staticmethod
    def _quantile(sorted_entries: List[Tuple[float, float]], alpha: float) -> float:
        """Weighted split-conformal quantile.

        With uniform weights this reduces exactly to the unweighted
        `ceil((n+1)(1-alpha))/n` order statistic -- the standard
        Angelopoulos & Bates recipe, unchanged. With non-uniform weights it
        is the weighted analogue: the smallest observed score at which the
        cumulative normalised weight reaches `(1-alpha)`, with the usual
        finite-sample `+1` correction applied to the total mass so the
        estimate stays conservative.

        `sorted_entries` MUST already be sorted by score -- insertion keeps
        it so.
        """
        if not sorted_entries:
            raise ValueError("no calibration entries")

        total_weight = sum(w for _, w in sorted_entries)
        if total_weight <= 0.0:
            return sorted_entries[-1][0]

        # The finite-sample correction: treat one extra unit of mass as
        # potentially exceeding every observed score, which is what makes
        # split conformal's coverage claim hold at finite n rather than
        # only asymptotically.
        mean_weight = total_weight / len(sorted_entries)
        target = (1 - alpha) * (total_weight + mean_weight)

        cumulative = 0.0
        for score, weight in sorted_entries:
            cumulative += weight
            if cumulative >= target:
                return score
        # `alpha` demands more mass than the pool holds: fall back to the
        # most conservative observed score.
        return sorted_entries[-1][0]

    def threshold_for(self, reason_code: str) -> Optional[float]:
        entries = self._calibration.get(reason_code)
        if not entries:
            return None
        threshold = self._quantile(entries, self.alpha)
        inflation = self._drift_inflation.get(reason_code, 1.0)
        return threshold / inflation  # inflation > 1 shrinks the threshold -- harder to clear, more escalation

    def pooled_threshold(self) -> Optional[float]:
        """Fallback quantile across ALL reason codes' calibration examples,
        used only when a given code's own stratum is too small for a
        reliable per-code coverage guarantee."""
        if not self._pooled:
            return None
        return self._quantile(self._pooled, self.alpha)

    def set_drift_inflation(self, reason_code: str, factor: float) -> None:
        """Automatically raise the abstention threshold under detected
        drift -- degrade toward humans, never toward silent guessing.
        `factor` > 1.0 makes auto-resolution strictly harder to reach."""
        if factor < 1.0:
            raise ValueError("drift inflation must be >= 1.0 -- drift response only ever tightens the gate")
        self._drift_inflation[reason_code] = factor

    # -- the gate ----------------------------------------------------------

    def decide(
        self,
        reason_code: str,
        confidence: ConfidenceVector,
        blocking_contradiction: Optional[str] = None,
    ) -> AbstentionDecision:
        """`blocking_contradiction`, when set, is the severity label of an
        unresolved contradiction that hard-blocks auto-resolution (see
        module docstring #2). The referee's verdict is still computed,
        signed, and stored -- this gate decides only whether it may be
        ACTED ON without a human."""
        score = confidence.nonconformity()

        if not confidence.has_decision:
            return AbstentionDecision(
                False, score, None, "referee reached no decision -- nothing to auto-resolve"
            )

        # -- hard invariants, checked before any statistics ----------------
        if blocking_contradiction:
            return AbstentionDecision(
                False, score, None,
                f"unresolved {blocking_contradiction} contradiction in the evidence graph -- "
                f"auto-resolution is blocked on principle regardless of nonconformity "
                f"({score:.3f}). Conformal coverage is a population guarantee, not a "
                f"per-case safety property; a case whose own evidence contradicts itself "
                f"is exactly what human review is for.",
                hard_blocked=True,
            )

        n = self.calibration_size(reason_code)
        n_eff = self.effective_sample_size(reason_code)

        if self.require_real_calibration and n_eff < self.min_n_for_guarantee:
            # Fail closed. The previous behaviour fell back to a pooled
            # threshold computed over fabricated boot data, which produced
            # a confident-looking auto-resolution with nothing behind it.
            return AbstentionDecision(
                False, score, None,
                f"reason_code={reason_code} has n={n} real calibration points "
                f"(effective n={n_eff:.1f} after inverse-probability weighting, "
                f"<{self.min_n_for_guarantee} required) -- no distribution-free coverage "
                f"guarantee can be made for this stratum, so every case in it escalates. "
                f"Populate calibration_sample (scripts/seed_calibration.py, or accumulated "
                f"analyst review) to enable auto-resolution here.",
                hard_blocked=True,
            )

        threshold = self.threshold_for(reason_code)

        if threshold is None:
            pooled = self.pooled_threshold()
            if pooled is None:
                return AbstentionDecision(
                    False, score, None,
                    "no calibration data anywhere yet -- cannot make a guaranteed coverage "
                    "claim, escalate by default",
                )
            auto_resolve = score <= pooled
            reason = (
                f"reason_code={reason_code} has zero calibration points -- falling back to the pooled "
                f"quantile across all reason codes (n={len(self._pooled)}); this is NOT a per-code coverage "
                f"guarantee, only a conservative stand-in. "
                f"nonconformity {score:.3f} {'<=' if auto_resolve else '>'} pooled threshold {pooled:.3f}"
            )
            return AbstentionDecision(auto_resolve, score, pooled, reason)

        if n_eff < self.min_n_for_guarantee:
            pooled = self.pooled_threshold()
            effective = pooled if pooled is not None else threshold
            auto_resolve = score <= effective
            reason = (
                f"reason_code={reason_code} has only effective n={n_eff:.1f} calibration points "
                f"(<{self.min_n_for_guarantee}) -- "
                f"per-code coverage guarantee not yet reliable, using the pooled threshold {effective:.3f} instead "
                f"(flagged under-calibrated). nonconformity {score:.3f} {'<=' if auto_resolve else '>'} {effective:.3f}"
            )
            return AbstentionDecision(auto_resolve, score, effective, reason)

        auto_resolve = score <= threshold
        if auto_resolve:
            reason = (
                f"nonconformity {score:.3f} <= threshold {threshold:.3f} "
                f"(Mondrian conformal, reason_code={reason_code}, n={n}, effective n={n_eff:.1f}, "
                f"alpha={self.alpha}) -- singleton set, auto-resolve"
            )
        else:
            reason = (
                f"nonconformity {score:.3f} > threshold {threshold:.3f} "
                f"(reason_code={reason_code}, n={n}, effective n={n_eff:.1f}, "
                f"alpha={self.alpha}) -- prediction set not a singleton, escalate"
            )
        return AbstentionDecision(auto_resolve, score, threshold, reason)

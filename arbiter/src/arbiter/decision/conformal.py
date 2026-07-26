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
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional

from .confidence import ConfidenceVector


@dataclass(frozen=True)
class AbstentionDecision:
    auto_resolve: bool
    nonconformity_score: float
    threshold: Optional[float]
    reason: str

    def to_dict(self) -> dict:
        return {
            "auto_resolve": self.auto_resolve,
            "nonconformity_score": round(self.nonconformity_score, 4),
            "threshold": round(self.threshold, 4) if self.threshold is not None else None,
            "reason": self.reason,
        }


class ConformalAbstentionGate:
    """Mondrian split-conformal calibration: one score pool, and one
    threshold, per reason code. Requires n>=100 per stratum for a real
    coverage guarantee (spec target); below that this falls back to the
    pooled/global pool and the reason string says so explicitly, per
    CLAUDE.md's "fail closed (abstain) over guessing"."""

    def __init__(self, alpha: float = 0.05, min_n_for_guarantee: int = 100):
        self.alpha = alpha
        self.min_n_for_guarantee = min_n_for_guarantee
        self._calibration: Dict[str, List[float]] = {}
        self._pooled: List[float] = []
        self._drift_inflation: Dict[str, float] = {}

    def add_calibration_example(self, reason_code: str, nonconformity_score: float) -> None:
        self._calibration.setdefault(reason_code, []).append(nonconformity_score)
        self._pooled.append(nonconformity_score)

    def calibration_size(self, reason_code: str) -> int:
        return len(self._calibration.get(reason_code, []))

    @staticmethod
    def _quantile(scores: List[float], alpha: float) -> float:
        n = len(scores)
        rank = math.ceil((n + 1) * (1 - alpha))
        rank = min(rank, n)  # if alpha is small enough to demand more than n points, fall back to the most conservative (highest) observed score
        return sorted(scores)[rank - 1]

    def threshold_for(self, reason_code: str) -> Optional[float]:
        scores = self._calibration.get(reason_code)
        if not scores:
            return None
        threshold = self._quantile(scores, self.alpha)
        inflation = self._drift_inflation.get(reason_code, 1.0)
        return threshold / inflation  # inflation > 1 shrinks the threshold -- harder to clear, more escalation

    def pooled_threshold(self) -> Optional[float]:
        """Fallback quantile across ALL reason codes' calibration examples,
        used only when a given code's own stratum is too small for a
        reliable per-code coverage guarantee (n < min_n_for_guarantee)."""
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

    def decide(self, reason_code: str, confidence: ConfidenceVector) -> AbstentionDecision:
        score = confidence.nonconformity()
        if not confidence.has_decision:
            return AbstentionDecision(False, score, None, "referee reached no decision -- nothing to auto-resolve")

        n = self.calibration_size(reason_code)
        under_calibrated = n < self.min_n_for_guarantee
        threshold = self.threshold_for(reason_code)

        if threshold is None:
            pooled = self.pooled_threshold()
            if pooled is None:
                return AbstentionDecision(
                    False, score, None,
                    f"no calibration data anywhere yet -- cannot make a guaranteed coverage claim, escalate by default",
                )
            auto_resolve = score <= pooled
            reason = (
                f"reason_code={reason_code} has zero calibration points -- falling back to the pooled "
                f"quantile across all reason codes (n={len(self._pooled)}); this is NOT a per-code coverage "
                f"guarantee, only a conservative stand-in until n>0 for this code. "
                f"nonconformity {score:.3f} {'<=' if auto_resolve else '>'} pooled threshold {pooled:.3f}"
            )
            return AbstentionDecision(auto_resolve, score, pooled, reason)

        if under_calibrated:
            pooled = self.pooled_threshold()
            effective = pooled if pooled is not None else threshold
            auto_resolve = score <= effective
            reason = (
                f"reason_code={reason_code} has only n={n} calibration points (<{self.min_n_for_guarantee}) -- "
                f"per-code coverage guarantee not yet reliable, using the pooled threshold {effective:.3f} instead "
                f"(flagged under-calibrated). nonconformity {score:.3f} {'<=' if auto_resolve else '>'} {effective:.3f}"
            )
            return AbstentionDecision(auto_resolve, score, effective, reason)

        auto_resolve = score <= threshold
        if auto_resolve:
            reason = (
                f"nonconformity {score:.3f} <= threshold {threshold:.3f} "
                f"(Mondrian conformal, reason_code={reason_code}, n={n}, alpha={self.alpha}) -- singleton set, auto-resolve"
            )
        else:
            reason = (
                f"nonconformity {score:.3f} > threshold {threshold:.3f} "
                f"(reason_code={reason_code}, n={n}, alpha={self.alpha}) -- prediction set not a singleton, escalate"
            )
        return AbstentionDecision(auto_resolve, score, threshold, reason)

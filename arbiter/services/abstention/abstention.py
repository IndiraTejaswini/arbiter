"""
Conformal Abstention Gate (§A5).

Confidence is not LLM self-report -- it is a deterministic vector over
things that are mechanically checkable: evidence completeness against the
reason code's required predicate set, extraction/provenance confidence,
unresolved contradiction severity, and the margin between how close each
side came to winning (services/counterfactual.per_case_symmetry). This
module turns that vector into a single nonconformity score and calibrates
an abstention threshold via split conformal prediction, stratified by
reason code (Mondrian conformal, §A5): coverage holds *within* each code,
not merely on average across all of them, so the system can't be
systematically wrong on a rare high-value code while looking fine in
aggregate.

Scoping note: full conformal classification produces a *prediction set*
that can contain zero, one, or several candidate labels. This system's
decision space per case is effectively binary (which side wins, or no
decision at all), so the set is collapsed to a binary gate: {decision} --
singleton, auto-resolve -- versus {} -- empty, abstain. That is a
deliberate reduction of the general machinery to what a two-outcome
decision problem actually needs, not a different algorithm; the quantile
calibration itself (q_hat = ceil((n+1)(1-alpha))/n order statistic) is
exactly Angelopoulos & Bates' split-conformal recipe, unmodified.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional

from packages.datalog.engine import EvaluationResult, Fact, FactStatus, RulePack
from services.counterfactual.counterfactual import PerCaseSymmetryProbe


@dataclass(frozen=True)
class ConfidenceVector:
    completeness: float          # fraction of required predicates resolved (not UNKNOWN)
    extraction_confidence: float  # mean Fact.confidence over resolved, load-bearing predicates
    contradiction_clarity: float  # 1.0 = no unresolved contradiction; degrades with severity
    decision_margin: float        # 1.0 = runner-up outcome is far from flipping; 0 = razor-thin
    has_decision: bool

    WEIGHTS = {
        "completeness": 0.30,
        "extraction_confidence": 0.15,
        "contradiction_clarity": 0.25,
        "decision_margin": 0.30,
    }

    def confidence(self) -> float:
        if not self.has_decision:
            return 0.0
        return (
            self.WEIGHTS["completeness"] * self.completeness
            + self.WEIGHTS["extraction_confidence"] * self.extraction_confidence
            + self.WEIGHTS["contradiction_clarity"] * self.contradiction_clarity
            + self.WEIGHTS["decision_margin"] * self.decision_margin
        )

    def nonconformity(self) -> float:
        return 1.0 - self.confidence()

    def to_dict(self) -> dict:
        return {
            "completeness": round(self.completeness, 4),
            "extraction_confidence": round(self.extraction_confidence, 4),
            "contradiction_clarity": round(self.contradiction_clarity, 4),
            "decision_margin": round(self.decision_margin, 4),
            "has_decision": self.has_decision,
            "confidence": round(self.confidence(), 4),
            "nonconformity": round(self.nonconformity(), 4),
        }


_SEVERITY_PENALTY = {None: 0.0, "LOW": 0.15, "MEDIUM": 0.5, "HIGH": 1.0}


def compute_confidence_vector(
    evaluation: EvaluationResult,
    rulepack: RulePack,
    contradiction_severity: Optional[str],
    symmetry_probe: PerCaseSymmetryProbe,
    margin_normalizer: int = 3,
) -> ConfidenceVector:
    schema = set(rulepack.predicate_schema) or rulepack.edb_predicates()
    resolved = [
        p for p in schema
        if evaluation.facts.get(p, Fact(p, FactStatus.UNKNOWN)).status is not FactStatus.UNKNOWN
    ]
    completeness = (len(resolved) / len(schema)) if schema else 1.0

    conf_values = [evaluation.facts[p].confidence for p in resolved if p in evaluation.facts]
    extraction_confidence = (sum(conf_values) / len(conf_values)) if conf_values else 0.0

    contradiction_clarity = 1.0 - _SEVERITY_PENALTY.get(contradiction_severity, 1.0)

    has_decision = evaluation.decision is not None
    if has_decision:
        other_distances = [d for outcome, d in symmetry_probe.distances.items() if outcome != evaluation.decision]
        min_other = min(other_distances) if other_distances else margin_normalizer
        decision_margin = min(1.0, min_other / margin_normalizer)
    else:
        decision_margin = 0.0

    return ConfidenceVector(
        completeness=completeness,
        extraction_confidence=extraction_confidence,
        contradiction_clarity=contradiction_clarity,
        decision_margin=decision_margin,
        has_decision=has_decision,
    )


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
    threshold, per reason code."""

    def __init__(self, alpha: float = 0.05):
        self.alpha = alpha
        self._calibration: Dict[str, List[float]] = {}
        self._drift_inflation: Dict[str, float] = {}

    def add_calibration_example(self, reason_code: str, nonconformity_score: float) -> None:
        self._calibration.setdefault(reason_code, []).append(nonconformity_score)

    def calibration_size(self, reason_code: str) -> int:
        return len(self._calibration.get(reason_code, []))

    def threshold_for(self, reason_code: str) -> Optional[float]:
        scores = self._calibration.get(reason_code)
        if not scores:
            return None
        n = len(scores)
        rank = math.ceil((n + 1) * (1 - self.alpha))
        rank = min(rank, n)  # if alpha is small enough to demand more than n points, fall back to the most conservative (highest) observed score
        threshold = sorted(scores)[rank - 1]
        inflation = self._drift_inflation.get(reason_code, 1.0)
        return threshold / inflation  # inflation > 1 shrinks the threshold -- harder to clear, more escalation

    def set_drift_inflation(self, reason_code: str, factor: float) -> None:
        """§9.7: 'automatically raise the abstention threshold under detected
        drift -- degrade toward humans, never toward silent guessing.'
        `factor` > 1.0 makes auto-resolution strictly harder to reach."""
        if factor < 1.0:
            raise ValueError("drift inflation must be >= 1.0 -- drift response only ever tightens the gate")
        self._drift_inflation[reason_code] = factor

    def decide(self, reason_code: str, confidence: ConfidenceVector) -> AbstentionDecision:
        score = confidence.nonconformity()
        if not confidence.has_decision:
            return AbstentionDecision(False, score, None, "referee reached no decision -- nothing to auto-resolve")

        threshold = self.threshold_for(reason_code)
        if threshold is None:
            return AbstentionDecision(
                False, score, None,
                f"no calibration data for reason_code={reason_code} -- cannot make a guaranteed "
                f"coverage claim, escalate by default",
            )

        auto_resolve = score <= threshold
        n = self.calibration_size(reason_code)
        reason = (
            f"nonconformity {score:.3f} <= threshold {threshold:.3f} "
            f"(Mondrian conformal, reason_code={reason_code}, n={n}, alpha={self.alpha}) -- singleton set, auto-resolve"
            if auto_resolve else
            f"nonconformity {score:.3f} > threshold {threshold:.3f} "
            f"(reason_code={reason_code}, n={n}, alpha={self.alpha}) -- prediction set not a singleton, escalate"
        )
        return AbstentionDecision(auto_resolve, score, threshold, reason)

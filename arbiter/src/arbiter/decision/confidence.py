"""
Deterministic confidence features (CLAUDE.md invariant #8: confidence comes
from deterministic features, never LLM self-report).

Everything here is mechanically checkable: evidence completeness against the
reason code's required predicate set, extraction/provenance confidence,
unresolved contradiction severity, the margin between how close each side
came to winning (arbiter.horn.per_case_symmetry), and how many advocate
assertions -- LLM-proposed or otherwise -- failed independent re-derivation
this case (`rejected_assertions`). That last one matters even though a
rejection never changes the decision itself (verify_assertions already
dropped the bad claim before the referee ever saw it): a case where the
LLM proposed several unsupported assertions is a case worth being LESS
confident about overall, since it's weak evidence the case sits in a region
the model finds ambiguous. conformal.py turns this vector into a single
nonconformity score and calibrates an abstention threshold via split
conformal prediction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from arbiter.horn.chain import EvaluationResult
from arbiter.horn.clause import RulePack
from arbiter.horn.counterfactual import PerCaseSymmetryProbe
from arbiter.horn.proof import Fact, FactStatus


@dataclass(frozen=True)
class ConfidenceVector:
    completeness: float          # fraction of required predicates resolved (not UNKNOWN)
    extraction_confidence: float  # mean Fact.confidence over resolved, load-bearing predicates
    contradiction_clarity: float  # 1.0 = no unresolved contradiction; degrades with severity
    decision_margin: float        # 1.0 = runner-up outcome is far from flipping; 0 = razor-thin
    has_decision: bool
    rejected_assertions: int = 0  # advocate/LLM claims that failed verify_assertions this case

    WEIGHTS = {
        "completeness": 0.30,
        "extraction_confidence": 0.15,
        "contradiction_clarity": 0.25,
        "decision_margin": 0.30,
    }

    # Multiplicative, not additive: rejections never change WHAT was decided
    # (verify_assertions already dropped the bad claims before the referee
    # ran), only how confident the system reports being about it. Capped so
    # a noisy-but-otherwise-clean case never gets driven to zero confidence
    # by rejections alone.
    MAX_REJECTION_PENALTY = 0.5
    PENALTY_PER_REJECTION = 0.12

    def rejection_penalty(self) -> float:
        return min(self.MAX_REJECTION_PENALTY, self.rejected_assertions * self.PENALTY_PER_REJECTION)

    def confidence(self) -> float:
        if not self.has_decision:
            return 0.0
        base = (
            self.WEIGHTS["completeness"] * self.completeness
            + self.WEIGHTS["extraction_confidence"] * self.extraction_confidence
            + self.WEIGHTS["contradiction_clarity"] * self.contradiction_clarity
            + self.WEIGHTS["decision_margin"] * self.decision_margin
        )
        return base * (1.0 - self.rejection_penalty())

    def nonconformity(self) -> float:
        return 1.0 - self.confidence()

    def to_dict(self) -> dict:
        return {
            "completeness": round(self.completeness, 4),
            "extraction_confidence": round(self.extraction_confidence, 4),
            "contradiction_clarity": round(self.contradiction_clarity, 4),
            "decision_margin": round(self.decision_margin, 4),
            "has_decision": self.has_decision,
            "rejected_assertions": self.rejected_assertions,
            "confidence": round(self.confidence(), 4),
            "nonconformity": round(self.nonconformity(), 4),
        }


_SEVERITY_PENALTY = {None: 0.0, "LOW": 0.15, "MEDIUM": 0.5, "HIGH": 1.0, "CRITICAL": 1.0}


def compute_confidence_vector(
    evaluation: EvaluationResult,
    rulepack: RulePack,
    contradiction_severity: Optional[str],
    symmetry_probe: PerCaseSymmetryProbe,
    margin_normalizer: int = 3,
    rejected_assertions: int = 0,
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
        rejected_assertions=rejected_assertions,
    )

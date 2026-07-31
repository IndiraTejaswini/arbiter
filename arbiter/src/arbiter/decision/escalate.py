"""
Escalation dossier assembly (C4: abstention routes to a human, not a guess).

When the conformal gate abstains, a human reviewer needs everything that
went into the referee's evaluation assembled in one place: the proof tree
(even a partial/no-decision one), both advocates' arguments, contradictions,
counterfactuals for every outcome, and the confidence vector that triggered
escalation. No automated action rides on this -- a human reads it (per the
spec's own framing: "Human reads it -- no automated action rides on it").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from arbiter.advocate.contract import ArgumentGraph
from arbiter.decision.adjudicate import RefereeResult
from arbiter.decision.confidence import ConfidenceVector
from arbiter.decision.conformal import AbstentionDecision
from arbiter.evidence.contradiction import Contradiction
from arbiter.fairness.cross_case import CrossCaseSignal
from arbiter.horn.counterfactual import Counterfactual


@dataclass(frozen=True)
class EscalationDossier:
    case_id: str
    reason_code: str
    referee_result: RefereeResult
    cm_argument: ArgumentGraph
    m_argument: ArgumentGraph
    contradictions: List[Contradiction]
    counterfactuals: Dict[str, Counterfactual]
    confidence: ConfidenceVector
    abstention: AbstentionDecision
    escalation_reason: str
    # Population-level findings (arbiter.fairness.cross_case) relevant to
    # THIS case -- device-fingerprint rings, template reuse across other
    # disputes. Dossier-only, by construction: nothing upstream of this
    # dataclass (the referee, predicate derivation) ever sees these, and
    # arbiter.horn is mechanically forbidden from importing the module
    # that produces them (pyproject.toml import-linter). A human reviewer
    # reading the dossier is the only consumer.
    cross_case_signals: Tuple[CrossCaseSignal, ...] = ()

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "reason_code": self.reason_code,
            "evaluation": self.referee_result.to_dict(),
            "cm_argument": self.cm_argument.to_dict(),
            "m_argument": self.m_argument.to_dict(),
            "contradictions": [
                {"kind": c.kind, "severity": c.severity, "description": c.description,
                 "node_ids": list(c.node_ids), "layer": c.layer}
                for c in self.contradictions
            ],
            "counterfactuals": {k: v.to_dict() for k, v in self.counterfactuals.items()},
            "confidence": self.confidence.to_dict(),
            "abstention": self.abstention.to_dict(),
            "escalation_reason": self.escalation_reason,
            "cross_case_signals": [s.to_dict() for s in self.cross_case_signals],
        }


def build_dossier(
    case_id: str,
    reason_code: str,
    referee_result: RefereeResult,
    cm_argument: ArgumentGraph,
    m_argument: ArgumentGraph,
    contradictions: List[Contradiction],
    counterfactuals: Dict[str, Counterfactual],
    confidence: ConfidenceVector,
    abstention: AbstentionDecision,
    cross_case_signals: Tuple[CrossCaseSignal, ...] = (),
) -> EscalationDossier:
    if referee_result.evaluation.conflicting_outcomes:
        reason = (
            f"conflicting outcomes fired simultaneously: "
            f"{', '.join(referee_result.evaluation.conflicting_outcomes)}"
        )
    elif referee_result.evaluation.decision is None:
        reason = "no rule satisfied by current evidence -- insufficient to decide either way"
    else:
        reason = abstention.reason

    return EscalationDossier(
        case_id=case_id,
        reason_code=reason_code,
        referee_result=referee_result,
        cm_argument=cm_argument,
        m_argument=m_argument,
        contradictions=contradictions,
        counterfactuals=counterfactuals,
        confidence=confidence,
        abstention=abstention,
        escalation_reason=reason,
        cross_case_signals=cross_case_signals,
    )

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from arbiter.db import models as m
from arbiter.db.session import get_session
from arbiter.fairness import CaseRecord, compute_rule_level_disparate_impact, flagged_only

router = APIRouter(prefix="/v1", tags=["fairness"])


def _evidence_strength_bucket(confidence: float) -> int:
    if confidence < 0.4:
        return 0
    if confidence < 0.7:
        return 1
    return 2


@router.get("/fairness/rules/{rule_id}")
def get_rule_fairness(rule_id: str, stratum_dimension: str = "merchant_tier", session: Session = Depends(get_session)):
    """A7: batch job over historical decisions. `stratum_dimension` is
    metadata the DB schema doesn't carry directly on `decision` (merchant
    tier lives with the merchant, not the case) -- in this build we read it
    back out of `seed_transaction.world_truth` for the synthetic dataset;
    a production deployment would join against a real merchant dimension
    table instead."""
    rows = session.execute(
        select(m.DecisionRow, m.DisputeCase, m.SeedTransaction)
        .join(m.DisputeCase, m.DisputeCase.case_id == m.DecisionRow.case_id)
        .join(m.SeedTransaction, m.SeedTransaction.transaction_id == m.DisputeCase.transaction_id)
    ).all()

    firings = session.execute(select(m.RuleFiringRow).where(m.RuleFiringRow.rule_id == rule_id)).scalars().all()
    fired_decision_ids = {f.decision_id for f in firings if f.fired}

    records = []
    for decision, case, seed in rows:
        world_truth = seed.world_truth or {}
        stratum_value = world_truth.get("merchant_size_tier", "UNKNOWN")
        records.append(CaseRecord(
            case_id=str(case.case_id), reason_code=case.reason_code,
            stratum_dimension=stratum_dimension, stratum_value=stratum_value,
            evidence_strength_bucket=_evidence_strength_bucket(decision.confidence),
            fired_rule_ids=(rule_id,) if decision.decision_id in fired_decision_ids else (),
        ))

    findings = compute_rule_level_disparate_impact(records, all_rule_ids=[rule_id])
    return {
        "rule_id": rule_id,
        "n_cases": len(records),
        "findings": [f.to_dict() for f in findings],
        "flagged": [f.to_dict() for f in flagged_only(findings)],
    }

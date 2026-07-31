from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from arbiter.api.deps import get_registry
from arbiter.auth import Actor, require_reviewer
from arbiter.auth.deps import get_current_actor
from arbiter.db import models as m
from arbiter.db.session import get_session
from arbiter.fairness import CaseRecord, compute_rule_level_disparate_impact, flagged_only

router = APIRouter(prefix="/v1", tags=["fairness"])

# Hard ceiling on how many historical decisions one disparate-impact query
# will pull into memory. The previous implementation ran an unbounded
# three-way join with no WHERE and no LIMIT -- at production volume that is
# tens of GB per request, and it was reachable without authentication. A
# real deployment computes this from a nightly materialised view rather
# than on the request path at all; the cap is the interim guard.
_MAX_FAIRNESS_SCAN_ROWS = 20_000


def _evidence_strength_bucket(confidence: float) -> int:
    if confidence < 0.4:
        return 0
    if confidence < 0.7:
        return 1
    return 2


@router.get("/fairness/rules")
def list_rules(registry=Depends(get_registry), actor: Actor = Depends(get_current_actor)):
    """All rule_ids across every loaded rulepack -- what the dashboard
    enumerates before drilling into any one rule's disparate-impact
    findings. Reviewer/admin only: this enumerates the decision logic, and
    the fairness dashboard is a compliance surface, not a party-facing
    one."""
    require_reviewer(actor)
    out = []
    for code in registry.reason_codes():
        pack = registry.latest(code)
        for rule in pack.rules:
            out.append({"reason_code": code, "rule_id": rule.rule_id, "head": rule.head, "description": rule.description})
    return out


@router.get("/fairness/rules/{rule_id}")
def get_rule_fairness(
    rule_id: str,
    stratum_dimension: str = "merchant_tier",
    limit: int = _MAX_FAIRNESS_SCAN_ROWS,
    session: Session = Depends(get_session),
    actor: Actor = Depends(get_current_actor),
):
    """A7: batch job over historical decisions. `stratum_dimension` is
    metadata the DB schema doesn't carry directly on `decision` (merchant
    tier lives with the merchant, not the case) -- in this build we read it
    back out of `seed_transaction.world_truth` for the synthetic dataset;
    a production deployment would join against a real merchant dimension
    table instead.

    Reviewer/admin only, and bounded: this was previously an
    unauthenticated, unbounded three-way join -- a trivial denial of
    service, and a leak of the whole decision corpus' fairness profile to
    anyone who asked."""
    require_reviewer(actor)
    limit = max(1, min(limit, _MAX_FAIRNESS_SCAN_ROWS))

    rows = session.execute(
        select(m.DecisionRow, m.DisputeCase, m.SeedTransaction)
        .join(m.DisputeCase, m.DisputeCase.case_id == m.DecisionRow.case_id)
        .join(m.SeedTransaction, m.SeedTransaction.transaction_id == m.DisputeCase.transaction_id)
        .order_by(m.DecisionRow.decided_at.desc())
        .limit(limit)
    ).all()

    # Scope the firing lookup to the decisions actually in the window --
    # the previous unfiltered scan had no index to stand on (rule_firing's
    # PK is (decision_id, rule_id), so rule_id alone is a full scan).
    decision_ids = [d.decision_id for d, _, _ in rows]
    fired_decision_ids: set = set()
    if decision_ids:
        firings = session.execute(
            select(m.RuleFiringRow).where(
                m.RuleFiringRow.rule_id == rule_id,
                m.RuleFiringRow.decision_id.in_(decision_ids),
            )
        ).scalars().all()
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

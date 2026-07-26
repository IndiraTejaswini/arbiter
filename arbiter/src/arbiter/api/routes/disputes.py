from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from arbiter.api.deps import get_abstention_gate, get_registry
from arbiter.api.orchestration import adjudicate_case
from arbiter.db import models as m
from arbiter.db.session import get_session

router = APIRouter(prefix="/v1", tags=["disputes"])

# Idempotency-Key -> case_id, process-local. A real deployment persists this
# in Redis with a TTL; kept in-process here since it only needs to survive
# one request's worth of client retries within this build's scope.
_idempotency_cache: dict[str, uuid.UUID] = {}


class CreateDisputeRequest(BaseModel):
    transaction_id: uuid.UUID
    reason_code: str
    reg_regime: str = "REG_Z"


class DisputeCaseOut(BaseModel):
    case_id: uuid.UUID
    transaction_id: uuid.UUID
    card_member_id: uuid.UUID
    merchant_id: uuid.UUID
    reason_code: str
    state: str
    amount_minor: int
    currency: str
    filed_at: datetime
    ack_deadline: datetime
    resolve_deadline: datetime
    merchant_responded: bool

    @classmethod
    def from_row(cls, row: m.DisputeCase) -> "DisputeCaseOut":
        return cls(
            case_id=row.case_id, transaction_id=row.transaction_id, card_member_id=row.card_member_id,
            merchant_id=row.merchant_id, reason_code=row.reason_code, state=row.state.value,
            amount_minor=row.amount_minor, currency=row.currency, filed_at=row.filed_at,
            ack_deadline=row.ack_deadline, resolve_deadline=row.resolve_deadline,
            merchant_responded=row.merchant_responded,
        )


@router.post("/disputes", response_model=DisputeCaseOut, status_code=201)
def create_dispute(
    body: CreateDisputeRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    session: Session = Depends(get_session),
):
    if not idempotency_key:
        raise HTTPException(400, "Idempotency-Key header is required")
    if idempotency_key in _idempotency_cache:
        existing = session.get(m.DisputeCase, _idempotency_cache[idempotency_key])
        if existing is not None:
            return DisputeCaseOut.from_row(existing)

    seed = session.execute(
        select(m.SeedTransaction).where(m.SeedTransaction.transaction_id == body.transaction_id)
    ).scalar_one_or_none()
    if seed is None:
        raise HTTPException(404, f"unknown transaction_id {body.transaction_id}")

    now = datetime.now(timezone.utc)
    case = m.DisputeCase(
        transaction_id=seed.transaction_id,
        card_member_id=seed.card_member_id,
        merchant_id=seed.merchant_id,
        reason_code=body.reason_code,
        state=m.CaseStateEnum.INTAKE,
        amount_minor=seed.amount_minor,
        currency=seed.currency,
        filed_at=now,
        reg_regime=body.reg_regime,
        ack_deadline=now + timedelta(days=3),
        resolve_deadline=now + timedelta(days=90),
        merchant_response_deadline=now + timedelta(days=20),
        merchant_responded=False,
    )
    session.add(case)
    session.commit()
    session.refresh(case)
    _idempotency_cache[idempotency_key] = case.case_id
    return DisputeCaseOut.from_row(case)


@router.get("/cases/{case_id}", response_model=DisputeCaseOut)
def get_case(case_id: uuid.UUID, session: Session = Depends(get_session)):
    case = session.get(m.DisputeCase, case_id)
    if case is None:
        raise HTTPException(404, "case not found")
    return DisputeCaseOut.from_row(case)


@router.post("/cases/{case_id}/adjudicate", response_model=DisputeCaseOut)
def run_adjudication(case_id: uuid.UUID, session: Session = Depends(get_session)):
    """Re-run (reviewer only in production -- auth is out of scope here)."""
    case = session.get(m.DisputeCase, case_id)
    if case is None:
        raise HTTPException(404, "case not found")
    adjudicate_case(session, case, get_registry(), get_abstention_gate())
    session.refresh(case)
    return DisputeCaseOut.from_row(case)


class ReviewDecisionRequest(BaseModel):
    outcome: str  # CARD_MEMBER_PREVAILS | MERCHANT_PREVAILS | SPLIT
    reviewer_id: str
    notes: Optional[str] = None


@router.post("/cases/{case_id}/review-decision")
def review_decision(case_id: uuid.UUID, body: ReviewDecisionRequest, session: Session = Depends(get_session)):
    """Feeds the calibration set (arbiter.decision.conformal): an analyst's
    review of an escalated case becomes a calibration_sample with
    source='ANALYST', exactly like a synthetic world's true_outcome would,
    just sourced from a human instead."""
    case = session.get(m.DisputeCase, case_id)
    if case is None:
        raise HTTPException(404, "case not found")
    decision = session.execute(
        select(m.DecisionRow).where(m.DecisionRow.case_id == case_id).order_by(m.DecisionRow.decided_at.desc())
    ).scalars().first()
    if decision is None:
        raise HTTPException(404, "no decision on record for this case yet")

    sample = m.CalibrationSample(
        reason_code=case.reason_code,
        features={"confidence": decision.confidence, "outcome_at_review": decision.outcome.value},
        score=1.0 - decision.confidence,
        true_outcome=m.OutcomeEnum(body.outcome),
        source="ANALYST",
    )
    session.add(sample)
    from arbiter.api.deps import get_abstention_gate as _gate
    _gate().add_calibration_example(case.reason_code, sample.score)

    case.state = m.CaseStateEnum.SETTLED
    session.commit()
    return {"status": "recorded", "case_id": str(case_id)}

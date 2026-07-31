from __future__ import annotations

import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from arbiter.api.deps import get_registry
from arbiter.audit import case_log
from arbiter.auth import Actor, Role, require_case_access, require_reviewer
from arbiter.auth.deps import get_current_actor
from arbiter.core.errors import RulepackError
from arbiter.db import models as m
from arbiter.db.session import get_session
from arbiter.decision import jobs
from arbiter.decision.deadlines import compute_deadlines, sweep_deadlines
from arbiter.decision.review_sampling import (
    ESCALATED_SELECTION_PROBABILITY,
    calibration_weight,
)
from arbiter.intake import classify_intent, verify_intent
from arbiter.intake.verify import CONFIDENCE_THRESHOLD

router = APIRouter(prefix="/v1", tags=["disputes"])

# Idempotency-Key -> case_id, process-local. A real deployment persists this
# in Redis with a TTL; kept in-process here since it only needs to survive
# one request's worth of client retries within this build's scope.
#
# BOUNDED. This was an unbounded dict that only ever grew: one entry per
# dispute ever filed by this process, never evicted, on a service intended
# to run for weeks. An insertion-ordered mapping trimmed from the front is
# the whole fix -- a client retry arrives seconds after the original, so a
# few thousand recent keys covers every retry that will ever be replayed,
# and an evicted key degrades to "no cached case" rather than to a wrong
# answer: the caller gets a 201 for a genuinely new case only if they also
# supplied a new key.
_IDEMPOTENCY_CACHE_MAX = 4096
_idempotency_cache: "OrderedDict[str, uuid.UUID]" = OrderedDict()


def _remember_idempotency(key: str, case_id: uuid.UUID) -> None:
    _idempotency_cache[key] = case_id
    _idempotency_cache.move_to_end(key)
    while len(_idempotency_cache) > _IDEMPOTENCY_CACHE_MAX:
        _idempotency_cache.popitem(last=False)


class CreateDisputeRequest(BaseModel):
    # `extra="forbid"` so a client that still sends the old `reg_regime`
    # field gets a loud 422 rather than having it silently ignored. Silent
    # ignoring would leave callers believing they still control it.
    model_config = ConfigDict(extra="forbid")

    transaction_id: uuid.UUID
    reason_code: Optional[str] = None
    complaint_text: Optional[str] = None
    # NOTE: `reg_regime` is deliberately NOT a request field. It decides
    # whether Reg E provisional credit is owed, and it is a property of the
    # product the transaction settled on -- credit (Reg Z) vs prepaid/debit
    # (Reg E) -- which only the ledger knows. Accepting it from the client
    # let a card member self-declare REG_E on a credit-card dispute and
    # force provisional credit on every escalated case. It now comes from
    # `seed_transaction.reg_regime`.


class IntentNotResolvedResponse(BaseModel):
    """Returned instead of a 201 when neither `reason_code` nor a confident
    classification of `complaint_text` is available -- no case is created.
    CLAUDE.md: the intent classifier never guesses a rulepack. The caller
    either re-POSTs with the confirmed `reason_code`, or the complaint goes
    to human triage."""

    resolved: bool = False
    proposed_reason_code: Optional[str]
    confidence: Optional[float]
    signals: list[str]
    needs_user_confirmation: bool
    route_to_human_triage: bool
    reason: str


class DisputeCaseOut(BaseModel):
    case_id: uuid.UUID
    transaction_id: uuid.UUID
    card_member_id: uuid.UUID
    merchant_id: uuid.UUID
    reason_code: str
    intent_confidence: Optional[float]
    state: str
    amount_minor: int
    currency: str
    filed_at: datetime
    # Which regulation governs this case. Drives the Reg E provisional-credit
    # axis and the shape of every deadline below, so a UI that cannot show it
    # cannot explain why two cases have different clocks.
    reg_regime: str
    # The regulatory clock. These columns existed from the first migration,
    # went unread until arbiter.decision.deadlines was built, and then went
    # unexposed -- so the sweeper's work was invisible to everyone it was
    # done for.
    ack_deadline: datetime
    acknowledged_at: Optional[datetime]
    resolve_deadline: datetime
    merchant_response_deadline: Optional[datetime]
    merchant_window_expired_at: Optional[datetime]
    provisional_credit_deadline: Optional[datetime]
    provisional_credit_issued_at: Optional[datetime]
    merchant_responded: bool

    @classmethod
    def from_row(cls, row: m.DisputeCase) -> "DisputeCaseOut":
        return cls(
            case_id=row.case_id, transaction_id=row.transaction_id, card_member_id=row.card_member_id,
            merchant_id=row.merchant_id, reason_code=row.reason_code, intent_confidence=row.intent_confidence,
            state=row.state.value, amount_minor=row.amount_minor, currency=row.currency, filed_at=row.filed_at,
            reg_regime=row.reg_regime,
            ack_deadline=row.ack_deadline, acknowledged_at=row.acknowledged_at,
            resolve_deadline=row.resolve_deadline,
            merchant_response_deadline=row.merchant_response_deadline,
            merchant_window_expired_at=row.merchant_window_expired_at,
            provisional_credit_deadline=row.provisional_credit_deadline,
            provisional_credit_issued_at=row.provisional_credit_issued_at,
            merchant_responded=row.merchant_responded,
        )


@router.post("/disputes", status_code=201, response_model=None)
def create_dispute(
    body: CreateDisputeRequest,
    response: Response,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    session: Session = Depends(get_session),
    actor: Actor = Depends(get_current_actor),
) -> DisputeCaseOut | IntentNotResolvedResponse:
    if not idempotency_key:
        raise HTTPException(400, "Idempotency-Key header is required")
    if idempotency_key in _idempotency_cache:
        existing = session.get(m.DisputeCase, _idempotency_cache[idempotency_key])
        if existing is not None:
            require_case_access(actor, existing)
            return DisputeCaseOut.from_row(existing)

    seed = session.execute(
        select(m.SeedTransaction).where(m.SeedTransaction.transaction_id == body.transaction_id)
    ).scalar_one_or_none()
    if seed is None:
        raise HTTPException(404, f"unknown transaction_id {body.transaction_id}")

    # A dispute can only be filed by the card member the transaction
    # actually belongs to (or a reviewer/admin filing on their behalf) --
    # never by an arbitrary caller supplying someone else's transaction_id.
    if not actor.is_privileged:
        if actor.role != Role.CARD_MEMBER or actor.bound_id != str(seed.card_member_id):
            raise HTTPException(403, "only the card member on this transaction may file this dispute")

    intent_confidence: Optional[float] = None
    reason_code = body.reason_code

    if reason_code is not None:
        # Accept the four-digit Amex network code (4540/4554/4513) as well as
        # ARBITER's internal one. That four-digit form is what the published
        # Amex chargeback guide uses and what a merchant reads off their own
        # "Resolve Disputes" screen, so refusing it made the one code a real
        # caller actually holds unusable. Unknown codes are rejected here,
        # loudly and at intake, rather than at adjudication time -- a case
        # created with an unroutable reason code sits in the queue burning
        # its statutory clock before failing.
        try:
            reason_code = get_registry().resolve(reason_code)
        except RulepackError as unknown:
            raise HTTPException(422, str(unknown))

    if reason_code is None:
        # LLM BOUNDARY 1: classify, then verify. The classifier never picks
        # the rulepack directly -- verify_intent does, and only when it
        # clears the confidence gate and resolves to a rulepack we actually
        # loaded.
        if not body.complaint_text:
            raise HTTPException(400, "either reason_code or complaint_text is required")
        # NOTE: deliberately NOT published to the case stream. There is no
        # case yet -- that is the entire point of this branch -- so there is
        # no `case:{id}` channel for a browser to be subscribed to, and the
        # previous `publish_stage(str(body.transaction_id), ...)` addressed a
        # channel with no subscriber by construction. Intake progress is
        # carried by this request's own response, which is synchronous and
        # sub-second; a stage event announcing work whose result is already
        # in the reply is not real-time tracking, it is noise that made the
        # frontend list a pipeline step that could never light up.
        result = classify_intent(
            body.complaint_text, merchant_descriptor=str(seed.merchant_id),
            amount_minor=seed.amount_minor, currency=seed.currency,
            transaction_date=seed.transaction_at.isoformat(),
        )
        decision = verify_intent(result, set(get_registry().reason_codes()))
        if not decision.resolved:
            # 200, not the route's default 201. Nothing was created: the
            # classifier declined to guess a rulepack and no case row exists.
            # Answering "201 Created" to a request that created nothing is
            # not a nuance -- a client that trusts the status line over the
            # body (a proxy, a retry policy, a generated SDK) records a
            # dispute that does not exist, and the card member's clock never
            # starts.
            response.status_code = 200
            return IntentNotResolvedResponse(
                proposed_reason_code=decision.reason_code,
                confidence=result.confidence if result else None,
                signals=list(result.signals) if result else [],
                needs_user_confirmation=decision.needs_user_confirmation,
                route_to_human_triage=decision.route_to_human_triage,
                reason=decision.reason,
            )
        reason_code = decision.reason_code
        intent_confidence = result.confidence if result else None

    now = datetime.now(timezone.utc)
    # Regulatory regime and every statutory deadline come from the ledger
    # and the statute, never from the caller. `ack_deadline` was previously
    # a hardcoded `now + 3 days`, which matches neither Reg Z's 30 days nor
    # Reg E's 10 business days.
    deadlines = compute_deadlines(seed.reg_regime, now)

    case = m.DisputeCase(
        transaction_id=seed.transaction_id,
        card_member_id=seed.card_member_id,
        merchant_id=seed.merchant_id,
        reason_code=reason_code,
        intent_confidence=intent_confidence,
        state=m.CaseStateEnum.INTAKE,
        amount_minor=seed.amount_minor,
        currency=seed.currency,
        filed_at=now,
        reg_regime=deadlines.reg_regime,
        ack_deadline=deadlines.ack_deadline,
        resolve_deadline=deadlines.resolve_deadline,
        merchant_response_deadline=deadlines.merchant_response_deadline,
        provisional_credit_deadline=deadlines.provisional_credit_deadline,
        merchant_responded=False,
    )
    session.add(case)
    session.flush()

    # Case creation and the intent classifier's rulepack choice are now in
    # the audit chain. `arbiter.intake.verify`'s docstring already claimed
    # "the API layer writes an INTENT_CLASSIFIED case_event regardless of
    # outcome" -- it did not, so the single decision that determines which
    # rules a case is judged under was invisible to GET /v1/audit.
    case_log.append_case_event(
        session, case.case_id, case_log.CASE_FILED,
        {
            "reason_code": reason_code,
            "reg_regime": deadlines.reg_regime,
            "reg_regime_source": "ledger",
            "amount_minor": seed.amount_minor,
            "currency": seed.currency,
            "deadlines": deadlines.to_dict(),
        },
        actor_id=actor.actor_id,
        actor_type=case_log.ACTOR_HUMAN if not actor.is_privileged else case_log.ACTOR_SERVICE,
    )
    if intent_confidence is not None:
        case_log.append_case_event(
            session, case.case_id, case_log.INTENT_CLASSIFIED,
            {
                "reason_code": reason_code,
                "confidence": intent_confidence,
                "threshold": CONFIDENCE_THRESHOLD,
                "note": (
                    "LLM boundary 1. The classifier selects which rulepack loads and has "
                    "no path to the verdict; verify_intent gated this result before a "
                    "rulepack was chosen."
                ),
            },
            actor_id="intake-classifier", actor_type=case_log.ACTOR_SERVICE,
        )

    session.commit()
    session.refresh(case)
    _remember_idempotency(idempotency_key, case.case_id)
    return DisputeCaseOut.from_row(case)


@router.get("/cases")
def list_cases(
    state: Optional[str] = None,
    reason_code: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_session),
    actor: Actor = Depends(get_current_actor),
):
    """Party-scoped case list.

    A non-privileged caller sees ONLY their own cases -- the filter is
    applied in SQL from the verified token, never from a query parameter,
    so there is no parameter a caller can set to widen their own scope.
    Reviewers and admins see everything.
    """
    limit = max(1, min(limit, 200))
    query = select(m.DisputeCase)

    if not actor.is_privileged:
        if actor.bound_id is None:
            raise HTTPException(403, "actor is not bound to a party")
        bound = uuid.UUID(actor.bound_id)
        if actor.role == Role.CARD_MEMBER:
            query = query.where(m.DisputeCase.card_member_id == bound)
        elif actor.role == Role.MERCHANT:
            query = query.where(m.DisputeCase.merchant_id == bound)
        else:
            raise HTTPException(403, "not authorized to list cases")

    if state:
        try:
            query = query.where(m.DisputeCase.state == m.CaseStateEnum(state))
        except ValueError:
            raise HTTPException(422, f"unknown state {state!r}")
    if reason_code:
        query = query.where(m.DisputeCase.reason_code == reason_code)

    total = session.execute(
        select(func.count()).select_from(query.subquery())
    ).scalar_one()
    rows = session.execute(
        query.order_by(m.DisputeCase.filed_at.desc()).limit(limit).offset(max(0, offset))
    ).scalars().all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "cases": [DisputeCaseOut.from_row(r).model_dump(mode="json") for r in rows],
    }


@router.get("/cases/{case_id}", response_model=DisputeCaseOut)
def get_case(case_id: uuid.UUID, session: Session = Depends(get_session), actor: Actor = Depends(get_current_actor)):
    case = session.get(m.DisputeCase, case_id)
    if case is None:
        raise HTTPException(404, "case not found")
    require_case_access(actor, case)
    return DisputeCaseOut.from_row(case)


@router.post("/cases/{case_id}/adjudicate", status_code=202)
def run_adjudication(
    case_id: uuid.UUID, session: Session = Depends(get_session), actor: Actor = Depends(get_current_actor)
):
    """Enqueue an adjudication and return 202 immediately.

    Asynchronous by design. This route used to run the whole pipeline
    inline, holding a threadpool worker for ~10 seconds; with a 40-thread
    pool and a 15-connection database pool the service saturated at roughly
    4 QPS and then queued unboundedly with no timeout and no backpressure.
    A client that gave up got no signal the work was still running, and a
    retry started a second full adjudication of the same case.

    Progress streams over `GET /v1/cases/{id}/stream`; terminal state is
    available from `GET /v1/jobs/{job_id}`.

    Reviewer/admin only -- a party re-running their own adjudication on
    demand could retry until favourable evidence timing landed, which is
    exactly the self-serving retry the append-only decision log exists to
    make visible rather than to permit.
    """
    require_reviewer(actor)
    case = session.get(m.DisputeCase, case_id)
    if case is None:
        raise HTTPException(404, "case not found")

    try:
        job = jobs.enqueue(session, case_id, requested_by=actor.actor_id)
        queued = True
    except jobs.JobAlreadyQueued as already:
        # Not a conflict from the caller's point of view: the work they
        # asked for is already happening, so hand back the job doing it.
        job = already.existing
        queued = False

    return {
        "case_id": str(case_id),
        "job": job.to_dict(),
        "newly_queued": queued,
        "stream_url": f"/v1/cases/{case_id}/stream",
    }


@router.get("/jobs/{job_id}")
def get_job(
    job_id: uuid.UUID, session: Session = Depends(get_session), actor: Actor = Depends(get_current_actor)
):
    """Terminal state of one adjudication job."""
    job = jobs.get(session, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    # Party-scoped like every other case-bound read: a job id must not be a
    # side channel around `require_case_access`.
    case = session.get(m.DisputeCase, uuid.UUID(job.case_id))
    if case is None:
        raise HTTPException(404, "job not found")
    require_case_access(actor, case)
    return job.to_dict()


@router.get("/cases/{case_id}/job")
def get_latest_job(
    case_id: uuid.UUID, session: Session = Depends(get_session), actor: Actor = Depends(get_current_actor)
):
    """The most recent adjudication job for this case, if any."""
    case = session.get(m.DisputeCase, case_id)
    if case is None:
        raise HTTPException(404, "case not found")
    require_case_access(actor, case)
    job = jobs.latest_for_case(session, case_id)
    return {"case_id": str(case_id), "job": job.to_dict() if job else None}


@router.get("/admin/queue")
def get_queue_health(
    session: Session = Depends(get_session), actor: Actor = Depends(get_current_actor)
):
    """Queue depth and recent failures.

    Depth and oldest-queued age are the two numbers that say whether
    workers are keeping up; a permanently failed job is a case that is not
    being adjudicated while its regulatory clock runs, which is exactly the
    thing that must not be silent.
    """
    require_reviewer(actor)
    return {
        "depth": jobs.queue_depth(session),
        "recent_failures": [j.to_dict() for j in jobs.recent_failures(session)],
    }


class ReviewDecisionRequest(BaseModel):
    outcome: str  # CARD_MEMBER_PREVAILS | MERCHANT_PREVAILS | SPLIT
    reviewer_id: str
    notes: Optional[str] = None


@router.post("/cases/{case_id}/review-decision")
def review_decision(
    case_id: uuid.UUID,
    body: ReviewDecisionRequest,
    session: Session = Depends(get_session),
    actor: Actor = Depends(get_current_actor),
):
    """Feeds the calibration set (arbiter.decision.conformal): an analyst's
    review of an escalated case becomes a calibration_sample with
    source='ANALYST', exactly like a synthetic world's true_outcome would,
    just sourced from a human instead. Reviewer/admin only -- this is the
    human-in-the-loop decision the abstention gate escalates to, not
    something either disputing party can submit for themselves."""
    require_reviewer(actor)
    case = session.get(m.DisputeCase, case_id)
    if case is None:
        raise HTTPException(404, "case not found")
    decision = session.execute(
        select(m.DecisionRow).where(m.DecisionRow.case_id == case_id).order_by(m.DecisionRow.decided_at.desc())
    ).scalars().first()
    if decision is None:
        raise HTTPException(404, "no decision on record for this case yet")

    try:
        reviewed_outcome = m.OutcomeEnum(body.outcome)
    except ValueError:
        raise HTTPException(
            422, f"unknown outcome {body.outcome!r}; must be one of "
                 f"{[o.value for o in m.OutcomeEnum]}"
        )
    if reviewed_outcome is m.OutcomeEnum.CHARGEBACK_INELIGIBLE:
        # CHARGEBACK_INELIGIBLE is a finding of the deterministic
        # chargeback-right gate over network-held attributes, not a verdict
        # a reviewer reaches by weighing evidence. A reviewer who believes a
        # dispute was never chargeable is reporting that the ledger's
        # attributes are wrong -- which is a data correction upstream, not a
        # review outcome here.
        raise HTTPException(
            422,
            "CHARGEBACK_INELIGIBLE is determined by the chargeback-right gate from "
            "network attributes, not by review. If this dispute is not chargeable, "
            "correct the transaction attributes it turns on.",
        )

    # THE audit event that was missing. An analyst overriding an escalated
    # case is the most consequential human action in the system, and it
    # wrote nothing to `case_event` -- so GET /v1/audit/{case_id} showed the
    # machine's reasoning and none of the human judgment layered on top of
    # it. For a system whose thesis is "audit is the storage model," that
    # was a hole in the storage model.
    overrode = reviewed_outcome != decision.outcome
    case_log.append_case_event(
        session, case_id,
        case_log.ANALYST_OVERRODE_SYSTEM if overrode else case_log.ANALYST_DECISION,
        {
            "reviewer_id": body.reviewer_id,
            "outcome": reviewed_outcome.value,
            "system_outcome": decision.outcome.value,
            "system_abstained": decision.abstained,
            "system_confidence": decision.confidence,
            "overrode_system": overrode,
            "escalation_reason": decision.escalation_reason,
            "notes": body.notes,
            "decision_id": str(decision.decision_id),
            "rulepack_hash": decision.rulepack_hash.hex(),
        },
        actor_id=actor.actor_id,
        actor_type=case_log.ACTOR_HUMAN,
        rulepack_hash=decision.rulepack_hash,
    )

    # A case that ended at the chargeback-right gate never reached the
    # conformal gate, so it has no nonconformity score to contribute and
    # must not enter the calibration pool. Its `confidence` is 1.0 by
    # construction (the gate is deterministic), which would enter the pool
    # as a maximally-conforming example and drift the abstention threshold
    # more permissive -- the exact selection-bias failure mode
    # `arbiter.decision.review_sampling` exists to prevent. The human's
    # judgment is still recorded above, in the audit chain, where it belongs.
    if decision.outcome is m.OutcomeEnum.CHARGEBACK_INELIGIBLE:
        case.state = m.CaseStateEnum.SETTLED
        session.commit()
        return {
            "status": "recorded",
            "case_id": str(case_id),
            "overrode_system": overrode,
            "calibration_sample_recorded": False,
            "reason": (
                "this case ended at the chargeback-right gate and was never scored by the "
                "conformal gate, so it contributes no calibration sample"
            ),
        }

    # A case the referee could not decide never reached the conformal
    # comparison either, so like the gate-terminated case above it has no
    # nonconformity score to contribute.
    #
    # This branch is the one that mattered most. `INSUFFICIENT_EVIDENCE` is
    # exactly "the referee reached no decision", its confidence is 0.0 by
    # construction, and so its score is 1.0 -- the maximum of the range.
    # Escalated cases are DISPROPORTIONATELY of this kind, and escalated
    # cases are precisely what analysts review. So every human review of a
    # no-decision case pushed another unit of mass to the top of the pool,
    # dragging the quantile up until the threshold pinned at 1.0 and the gate
    # stopped rejecting anything at all.
    #
    # That is the same failure `arbiter.decision.review_sampling` exists to
    # prevent -- "the gate gets more permissive the more review is done" --
    # reached by a route the inverse-probability weighting there cannot see,
    # because the problem is not HOW these samples are weighted but that they
    # are not members of the population the threshold governs.
    if decision.outcome is m.OutcomeEnum.INSUFFICIENT_EVIDENCE:
        case.state = m.CaseStateEnum.SETTLED
        session.commit()
        return {
            "status": "recorded",
            "case_id": str(case_id),
            "overrode_system": overrode,
            "calibration_sample_recorded": False,
            "reason": (
                "the referee reached no decision on this case, so the conformal gate was "
                "never asked to rule on it and it has no nonconformity score to contribute. "
                "Your judgment is recorded in the audit chain; it does not move the "
                "abstention threshold."
            ),
        }

    # Inverse-probability weight. An escalated review is probability 1.0
    # (it IS the escalation path); an audit review of an auto-resolved case
    # carries the audit rate, so it stands in for the ~1/rate cases like it
    # that were not reviewed. Without this the pool is a biased subsample
    # and the gate drifts more permissive the more review is done.
    selection_probability = (
        decision.review_selection_probability
        if decision.selected_for_audit and decision.review_selection_probability
        else ESCALATED_SELECTION_PROBABILITY
    )

    sample = m.CalibrationSample(
        reason_code=case.reason_code,
        selection_probability=selection_probability,
        is_audit_sample=bool(decision.selected_for_audit),
        features={
            "confidence": decision.confidence, "outcome_at_review": decision.outcome.value,
            "analyst_outcome": reviewed_outcome.value, "overrode_system": overrode,
            # case_id + predicates carried through so arbiter.decision.mining
            # (scripts/mine_disagreements.py) can reconstruct which TRUE
            # predicates this analyst override applied to, without an
            # unreliable cross-table join -- calibration_sample otherwise
            # has no case_id column (it's a stratified calibration pool,
            # not a case-keyed log).
            "case_id": str(case_id), "predicates": decision.predicates,
        },
        score=1.0 - decision.confidence,
        true_outcome=reviewed_outcome,
        source="ANALYST",
    )
    session.add(sample)
    from arbiter.api.deps import get_abstention_gate as _gate
    _gate().add_calibration_example(
        case.reason_code, sample.score, weight=calibration_weight(selection_probability),
    )

    case.state = m.CaseStateEnum.SETTLED
    session.commit()
    return {
        "status": "recorded", "case_id": str(case_id), "overrode_system": overrode,
        "calibration_sample_recorded": True,
        # `reason` is present on BOTH branches so a client never has to infer
        # from the absence of a field why its review did or did not move the
        # abstention threshold. Whether a review enters the calibration pool
        # is the difference between a judgment that improves the gate and one
        # that is only recorded, and the reviewer who spent the time is owed
        # a straight answer either way.
        "reason": (
            f"entered the calibration pool with selection probability "
            f"{selection_probability:.4g} (inverse-probability weighted, so this review "
            f"stands in for the cases like it that were not reviewed)"
        ),
    }


@router.post("/admin/sweep-deadlines")
def run_deadline_sweep(
    session: Session = Depends(get_session), actor: Actor = Depends(get_current_actor)
):
    """Run one pass of the regulatory clock (arbiter.decision.deadlines).

    Normally driven by the background scheduler or
    `scripts/run_deadline_sweeper.py`; exposed here so an operator can force
    a pass and so the behaviour is demonstrable without waiting out a
    statutory window. Every action it takes writes a `case_event`."""
    require_reviewer(actor)
    return sweep_deadlines(session).to_dict()


@router.get("/cases-at-risk")
def list_cases_at_risk(
    days: int = 7, session: Session = Depends(get_session), actor: Actor = Depends(get_current_actor)
):
    """Analyst queue ordered by SLA risk -- open cases whose statutory
    resolution deadline falls inside `days`."""
    from datetime import timedelta

    from arbiter.decision.deadlines import cases_at_risk

    require_reviewer(actor)
    return {"within_days": days, "cases": cases_at_risk(session, timedelta(days=max(1, min(days, 90))))}

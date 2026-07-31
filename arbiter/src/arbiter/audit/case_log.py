"""
Postgres-backed case event log writer.

This lived as a private `_append_event` inside `arbiter.api.orchestration`,
which meant only the adjudication pipeline could write audit events. The
consequence was a real hole in a system whose thesis is "audit is the
storage model": the most consequential HUMAN actions -- an analyst
overriding an escalated case, a compliance officer executing a GDPR
erasure, the intake classifier choosing a rulepack -- wrote nothing to
`case_event` at all. `GET /v1/audit/{case_id}` showed the machine's
reasoning and none of the human decisions layered on top of it.

Hoisting it here makes the writer available to every route, and gives the
event taxonomy one place to live.

Concurrency: `(case_id, seq)` is the primary key, so two writers racing for
the same sequence number collide at the database rather than silently
forking the hash chain. `append_case_event` retries on that collision
instead of surfacing an IntegrityError -- a losing race is a normal
occurrence under concurrent adjudication, not an error condition.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from arbiter.db import models as m

from .sign import EventSigner

GENESIS_HASH = b"\x00" * 32

# One signer per process; the key itself is loaded from configuration
# (arbiter.audit.sign) so signatures survive a restart.
_signer = EventSigner()


# -- Event taxonomy -------------------------------------------------------
# Named constants rather than string literals at each call site: an audit
# trail whose event types are typo-prone free text cannot be queried
# reliably, and a compliance reviewer filtering for human actions needs
# the set to be closed and greppable.

CASE_FILED = "CASE_FILED"
INTENT_CLASSIFIED = "INTENT_CLASSIFIED"
EVIDENCE_UPLOADED = "EVIDENCE_UPLOADED"
# One per run of the pipeline, distinct from CASE_FILED. `adjudicate_case`
# used to re-emit CASE_FILED at the top of every run, so a case adjudicated
# twice showed CASE_FILED three times -- once from intake with the full
# payload, then twice more with a thinner, partly contradictory one. In a
# system whose storage model IS the audit log, an event type that means
# "filed" appearing after evidence was uploaded is not a cosmetic defect.
ADJUDICATION_STARTED = "ADJUDICATION_STARTED"
DECISION_COMPUTED = "DECISION_COMPUTED"
CASE_ESCALATED = "CASE_ESCALATED"
LLM_ASSERTIONS_REJECTED = "LLM_ASSERTIONS_REJECTED"
# A generated narration cited an evidence node that does not exist, so the
# WHOLE narration was discarded and the deterministic template rendered
# instead (CLAUDE.md invariant #5). Logged for the same reason
# LLM_ASSERTIONS_REJECTED is: a caught hallucination is a visible signal.
NARRATION_GROUNDING_FAILED = "NARRATION_GROUNDING_FAILED"
PROVISIONAL_CREDIT_DUE = "PROVISIONAL_CREDIT_DUE"
# An auto-resolved case deliberately routed to a human anyway
# (arbiter.decision.review_sampling), so the calibration pool sees the
# region of the distribution the escalation path never visits.
SELECTED_FOR_AUDIT_REVIEW = "SELECTED_FOR_AUDIT_REVIEW"

# There is deliberately NO `INTENT_UNRESOLVED` here. `case_event` is keyed
# by `case_id`, and an unresolved intent is precisely the outcome in which
# NO case is created -- the classifier declined to guess a rulepack, so
# there is nothing to attach an event to. A constant for it existed and was
# never written by anything, which reads as an unimplemented feature rather
# than as the structural fact it is. Rejected classifications are visible in
# the API response (`IntentNotResolvedResponse`); giving them a home in the
# audit chain requires a subject-keyed log, which is a different table.

# Chargeback-right gate (arbiter.eligibility), which runs before the
# referee. Both events are worth their own type rather than a payload flag
# on DECISION_COMPUTED: a dispute that ended because the network gave no
# chargeback right did not go through adjudication at all, and a compliance
# reviewer filtering the log needs to see that as a different kind of event,
# not as a decision with an unusual outcome.
CHARGEBACK_RIGHT_UNAVAILABLE = "CHARGEBACK_RIGHT_UNAVAILABLE"
# An exclusion that could have ended the dispute could not be evaluated,
# because the ledger did not supply an attribute it turns on. The case
# proceeded to the merits. Emitted so the gaps get closed rather than
# quietly widening the set of disputes that bypass a gate nobody notices is
# not running.
CHARGEBACK_RIGHT_UNDETERMINED = "CHARGEBACK_RIGHT_UNDETERMINED"

# Human actions -- the ones that were previously invisible.
ANALYST_DECISION = "ANALYST_DECISION"
ANALYST_OVERRODE_SYSTEM = "ANALYST_OVERRODE_SYSTEM"
SUBJECT_ERASURE_EXECUTED = "SUBJECT_ERASURE_EXECUTED"

# Regulatory clock events (arbiter.decision.deadlines).
ACK_DEADLINE_MET = "ACK_DEADLINE_MET"
ACK_DEADLINE_BREACHED = "ACK_DEADLINE_BREACHED"
MERCHANT_WINDOW_EXPIRED = "MERCHANT_WINDOW_EXPIRED"
RESOLVE_DEADLINE_BREACHED = "RESOLVE_DEADLINE_BREACHED"
PROVISIONAL_CREDIT_DEADLINE_REACHED = "PROVISIONAL_CREDIT_DEADLINE_REACHED"

ACTOR_HUMAN = "human"
ACTOR_SERVICE = "service"
ACTOR_ADVOCATE = "advocate"
ACTOR_REFEREE = "referee"


def _canonical_event_bytes(
    case_id: uuid.UUID,
    seq: int,
    event_type: str,
    payload: dict,
    actor_id: str,
    actor_type: str,
    occurred_at: datetime,
    prev_hash: bytes,
    rulepack_hash: Optional[bytes],
) -> bytes:
    return json.dumps(
        {
            "case_id": str(case_id), "seq": seq, "event_type": event_type, "payload": payload,
            "actor_id": actor_id, "actor_type": actor_type, "occurred_at": occurred_at.isoformat(),
            "prev_hash": prev_hash.hex(), "rulepack_hash": rulepack_hash.hex() if rulepack_hash else None,
        },
        sort_keys=True, default=str, separators=(",", ":"),
    ).encode("utf-8")


def recompute_event_hash(row: m.CaseEventRow) -> bytes:
    """Re-derive an event's hash from its stored fields.

    `GET /v1/audit/{case_id}` verifies the prev_hash chain and the
    signature over `event_hash`, but never checked that `event_hash`
    actually matches the payload it claims to cover -- so an altered
    payload would still report `chain_valid: true, signatures_valid: true`.
    Exposed here so the audit route can close that gap with the same
    canonicalisation the writer used.
    """
    return hashlib.sha256(
        _canonical_event_bytes(
            row.case_id, row.seq, row.event_type, row.payload, row.actor_id,
            row.actor_type, row.occurred_at, row.prev_hash, row.rulepack_hash,
        )
    ).digest()


def append_case_event(
    session: Session,
    case_id: uuid.UUID,
    event_type: str,
    payload: dict,
    actor_id: str,
    actor_type: str = ACTOR_SERVICE,
    rulepack_hash: Optional[bytes] = None,
    _attempt: int = 0,
) -> m.CaseEventRow:
    """Append one signed, hash-chained event. Never updates; corrections are
    new rows (CLAUDE.md invariant #8)."""
    last = session.execute(
        select(m.CaseEventRow.seq, m.CaseEventRow.event_hash)
        .where(m.CaseEventRow.case_id == case_id)
        .order_by(m.CaseEventRow.seq.desc())
        .limit(1)
    ).first()
    seq = 0 if last is None else last[0] + 1
    prev_hash = GENESIS_HASH if last is None else last[1]
    occurred_at = datetime.now(timezone.utc)

    event_hash = hashlib.sha256(
        _canonical_event_bytes(
            case_id, seq, event_type, payload, actor_id, actor_type,
            occurred_at, prev_hash, rulepack_hash,
        )
    ).digest()

    row = m.CaseEventRow(
        case_id=case_id, seq=seq, event_type=event_type, payload=payload, actor_id=actor_id,
        actor_type=actor_type, rulepack_hash=rulepack_hash, occurred_at=occurred_at,
        prev_hash=prev_hash, event_hash=event_hash, signature=_signer.sign(event_hash),
        key_epoch=_signer.epoch,
    )
    session.add(row)
    try:
        session.flush()
    except IntegrityError:
        # Lost the race for this seq. Roll back to the savepoint the caller's
        # transaction gives us and re-read the tail. Bounded retries: an
        # unbounded loop under sustained contention would be worse than a
        # visible failure.
        if _attempt >= 4:
            raise
        session.rollback()
        return append_case_event(
            session, case_id, event_type, payload, actor_id, actor_type,
            rulepack_hash, _attempt=_attempt + 1,
        )
    return row

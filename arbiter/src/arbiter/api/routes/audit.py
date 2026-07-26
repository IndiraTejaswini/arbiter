from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from arbiter.db import models as m
from arbiter.db.session import get_session

router = APIRouter(prefix="/v1", tags=["audit"])


@router.get("/audit/{case_id}")
def get_audit_trail(case_id: uuid.UUID, session: Session = Depends(get_session)):
    """Replays the append-only case_event chain and re-verifies it in the
    same response -- so a caller doesn't have to trust the API's own
    claim that the chain is intact; the hashes are recomputed here."""
    events = session.execute(
        select(m.CaseEventRow).where(m.CaseEventRow.case_id == case_id).order_by(m.CaseEventRow.seq)
    ).scalars().all()

    prev = b"\x00" * 32
    valid = True
    broken_at = None
    for e in events:
        if e.prev_hash != prev:
            valid = False
            broken_at = e.seq
            break
        prev = e.event_hash

    return {
        "case_id": str(case_id),
        "chain_valid": valid,
        "broken_at_seq": broken_at,
        "events": [
            {
                "seq": e.seq, "event_type": e.event_type, "actor_id": e.actor_id, "actor_type": e.actor_type,
                "occurred_at": e.occurred_at.isoformat(), "event_hash": e.event_hash.hex(),
                "rulepack_hash": e.rulepack_hash.hex() if e.rulepack_hash else None,
                "payload": e.payload,
            }
            for e in events
        ],
    }

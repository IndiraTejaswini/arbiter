from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from arbiter.audit import case_log
from arbiter.audit.sign import KeyRing
from arbiter.auth import Actor, require_reviewer
from arbiter.auth.deps import get_current_actor
from arbiter.db import models as m
from arbiter.db.session import get_session

router = APIRouter(prefix="/v1", tags=["audit"])

_key_ring = KeyRing()


@router.get("/audit/{case_id}")
def get_audit_trail(
    case_id: uuid.UUID, session: Session = Depends(get_session), actor: Actor = Depends(get_current_actor)
):
    """Replays the append-only case_event chain and re-verifies it in the
    same response -- so a caller doesn't have to trust the API's own
    claim that the chain is intact; the hashes are recomputed here.

    Reviewer/admin only: this is the raw internal event log (service,
    advocate, and referee actor events, LLM-rejection detail, rulepack
    hashes), not the party-facing case timeline (GET
    /cases/{id}/timeline) -- neither disputing party gets this even on
    their own case."""
    require_reviewer(actor)
    events = session.execute(
        select(m.CaseEventRow).where(m.CaseEventRow.case_id == case_id).order_by(m.CaseEventRow.seq)
    ).scalars().all()

    prev = b"\x00" * 32
    chain_valid = True
    broken_at = None
    payloads_valid = True
    signatures_valid = True
    event_dicts = []

    for e in events:
        # 1. Linkage: does this event chain to the previous one?
        link_ok = e.prev_hash == prev
        if not link_ok and chain_valid:
            chain_valid = False
            broken_at = e.seq

        # 2. Content: does the stored event_hash actually cover the stored
        #    payload? This check was MISSING -- the endpoint verified the
        #    prev_hash chain and the signature over `event_hash`, but never
        #    that `event_hash` matched the payload it claims to commit to.
        #    An altered payload therefore reported `chain_valid: true,
        #    signatures_valid: true`, which is precisely the assurance a
        #    reader of this endpoint is relying on it not to give falsely.
        recomputed = case_log.recompute_event_hash(e)
        payload_ok = recomputed == e.event_hash
        payloads_valid = payloads_valid and payload_ok

        # 3. Authenticity: verified against the KeyRing epoch the event was
        #    actually signed under, so a past rotation never breaks this.
        sig_ok = _key_ring.verify(e.key_epoch, e.event_hash, e.signature)
        signatures_valid = signatures_valid and sig_ok

        event_dicts.append({
            "seq": e.seq, "event_type": e.event_type, "actor_id": e.actor_id, "actor_type": e.actor_type,
            "occurred_at": e.occurred_at.isoformat(), "event_hash": e.event_hash.hex(),
            "rulepack_hash": e.rulepack_hash.hex() if e.rulepack_hash else None,
            "key_epoch": e.key_epoch,
            "link_valid": link_ok,
            "payload_hash_valid": payload_ok,
            "signature_valid": sig_ok,
            "payload": e.payload,
        })
        prev = e.event_hash

    return {
        "case_id": str(case_id),
        "chain_valid": chain_valid,
        "broken_at_seq": broken_at,
        # Reported separately rather than folded into `chain_valid`: a
        # broken link and a tampered payload are different attacks with
        # different remediations, and collapsing them tells a responder less
        # than it costs to keep them apart.
        "payloads_valid": payloads_valid,
        "signatures_valid": signatures_valid,
        "verified": chain_valid and payloads_valid and signatures_valid,
        "events": event_dicts,
    }

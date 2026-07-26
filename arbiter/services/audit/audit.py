"""
Event-sourced case store with an intra-case hash chain (§7.2).

Audit is a property of the storage model, not a bolted-on logger: every
state transition a case ever goes through is an appended, signed,
hash-chained event. Nothing is ever updated or deleted -- this module
simply never exposes a mutation method, and that absence *is* the
enforcement (a real deployment additionally enforces it with a DB trigger
denying UPDATE/DELETE grants, per §7.2's DDL comment).

The hash chain gives intra-case tamper evidence: altering any past event
breaks every event_hash after it. The Merkle transparency log (packages/merkle)
gives cross-case, externally-verifiable ordering; `AuditService` below
anchors each case's decision events into that log so tampering with the
*event store itself* (not just an individual event) is independently
detectable by a party who only ever saw the anchored root.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from packages.merkle.log import TransparencyLog

GENESIS_HASH = b"\x00" * 32


def _sha256(data: bytes) -> bytes:
    import hashlib

    return hashlib.sha256(data).digest()


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")


class EventSigner:
    """A distinct Ed25519 identity from the log operator and the TSA (§A1):
    compromising one key must not compromise the others."""

    def __init__(self, private_key: Optional[Ed25519PrivateKey] = None):
        self._key = private_key or Ed25519PrivateKey.generate()
        self._public_key = self._key.public_key()

    def public_key(self) -> Ed25519PublicKey:
        return self._public_key

    def sign(self, payload: bytes) -> bytes:
        return self._key.sign(payload)

    def verify(self, payload: bytes, signature: bytes) -> bool:
        try:
            self._public_key.verify(signature, payload)
            return True
        except InvalidSignature:
            return False


@dataclass(frozen=True)
class CaseEvent:
    case_id: str
    seq: int
    event_type: str
    payload: Dict[str, Any]
    actor_id: str
    actor_type: str  # human | service | advocate | referee
    occurred_at: datetime
    prev_hash: bytes
    event_hash: bytes
    signature: bytes
    rulepack_hash: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "seq": self.seq,
            "event_type": self.event_type,
            "payload": self.payload,
            "actor_id": self.actor_id,
            "actor_type": self.actor_type,
            "occurred_at": self.occurred_at.isoformat(),
            "prev_hash": self.prev_hash.hex(),
            "event_hash": self.event_hash.hex(),
            "signature": self.signature.hex(),
            "rulepack_hash": self.rulepack_hash,
        }


@dataclass(frozen=True)
class ChainVerification:
    case_id: str
    valid: bool
    length: int
    broken_at_seq: Optional[int] = None
    reason: Optional[str] = None


class EventStore:
    """Append-only, per-case, hash-chained event log."""

    def __init__(self, signer: Optional[EventSigner] = None):
        self.signer = signer or EventSigner()
        self._events: Dict[str, List[CaseEvent]] = {}

    def _event_hash(
        self,
        case_id: str,
        seq: int,
        event_type: str,
        payload: Dict[str, Any],
        actor_id: str,
        actor_type: str,
        occurred_at: datetime,
        prev_hash: bytes,
        rulepack_hash: Optional[str],
    ) -> bytes:
        blob = _canonical(
            {
                "case_id": case_id,
                "seq": seq,
                "event_type": event_type,
                "payload": payload,
                "actor_id": actor_id,
                "actor_type": actor_type,
                "occurred_at": occurred_at.isoformat(),
                "prev_hash": prev_hash.hex(),
                "rulepack_hash": rulepack_hash,
            }
        )
        return _sha256(blob)

    def append(
        self,
        case_id: str,
        event_type: str,
        payload: Dict[str, Any],
        actor_id: str,
        actor_type: str,
        rulepack_hash: Optional[str] = None,
        occurred_at: Optional[datetime] = None,
    ) -> CaseEvent:
        events = self._events.setdefault(case_id, [])
        seq = len(events)
        prev_hash = events[-1].event_hash if events else GENESIS_HASH
        occurred_at = occurred_at or datetime.now(timezone.utc)

        event_hash = self._event_hash(
            case_id, seq, event_type, payload, actor_id, actor_type, occurred_at, prev_hash, rulepack_hash
        )
        signature = self.signer.sign(event_hash)

        event = CaseEvent(
            case_id=case_id,
            seq=seq,
            event_type=event_type,
            payload=payload,
            actor_id=actor_id,
            actor_type=actor_type,
            occurred_at=occurred_at,
            prev_hash=prev_hash,
            event_hash=event_hash,
            signature=signature,
            rulepack_hash=rulepack_hash,
        )
        events.append(event)
        return event

    def events_for(self, case_id: str) -> List[CaseEvent]:
        return list(self._events.get(case_id, []))

    def verify_chain(self, case_id: str) -> ChainVerification:
        events = self._events.get(case_id, [])
        prev_hash = GENESIS_HASH
        for event in events:
            if event.prev_hash != prev_hash:
                return ChainVerification(case_id, False, len(events), event.seq, "prev_hash mismatch")
            recomputed = self._event_hash(
                event.case_id, event.seq, event.event_type, event.payload,
                event.actor_id, event.actor_type, event.occurred_at, event.prev_hash, event.rulepack_hash,
            )
            if recomputed != event.event_hash:
                return ChainVerification(case_id, False, len(events), event.seq, "event_hash mismatch (tampered payload)")
            if not self.signer.verify(event.event_hash, event.signature):
                return ChainVerification(case_id, False, len(events), event.seq, "signature invalid")
            prev_hash = event.event_hash
        return ChainVerification(case_id, True, len(events))

    def replay(self, case_id: str) -> List[Dict[str, Any]]:
        """CQRS read models are projections rebuilt from the log (§7.2); this
        is the minimal generic projection -- the ordered payload stream a
        richer projector (analyst queue, merchant dashboard) would fold over."""
        return [e.payload | {"event_type": e.event_type, "seq": e.seq} for e in self.events_for(case_id)]


class AuditService:
    """
    Anchors case decision events into the external transparency log, so
    tampering with the event store's *storage*, not just an individual
    event's fields, is independently detectable (§9.6, `audit-service`).
    """

    def __init__(self, event_store: EventStore, transparency_log: TransparencyLog):
        self.event_store = event_store
        self.log = transparency_log
        self._anchor_leaf_index: Dict[str, int] = {}  # event_hash.hex() -> leaf index

    def anchor(self, event: CaseEvent) -> int:
        payload = _canonical({"case_id": event.case_id, "seq": event.seq, "event_hash": event.event_hash.hex()})
        idx = self.log.append(payload)
        self._anchor_leaf_index[event.event_hash.hex()] = idx
        return idx

    def seal(self) -> None:
        self.log.seal_batch()

    def verify_anchored(self, event: CaseEvent) -> bool:
        idx = self._anchor_leaf_index.get(event.event_hash.hex())
        if idx is None:
            return False
        payload = _canonical({"case_id": event.case_id, "seq": event.seq, "event_hash": event.event_hash.hex()})
        result = self.log.verify_commitment(payload, idx)
        return result.ok

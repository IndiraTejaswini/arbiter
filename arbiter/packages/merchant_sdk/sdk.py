"""
Merchant SDK for ADEC commitment emission (§5.1 idea 39, §12.4 build plan).

Deliberately small -- this is the entire integration surface a merchant's
order-management system needs to adopt ADEC: call `emit()` at the moment a
real-world event occurs (order placed, shipped, delivered, ToS accepted,
refund policy shown). The SDK generates the salt, computes the commitment
hash, posts it to the provenance service, and returns a receipt the
merchant stores locally -- they need it to reveal the artifact later if a
dispute happens. Nothing but the hash ever leaves the merchant at commit
time (§A1: zero privacy cost).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from services.provenance.provenance import ProvenanceService, compute_commitment_hash


@dataclass(frozen=True)
class CommitmentReceipt:
    """What the merchant stores locally. `artifact` and `salt` never leave
    the merchant's system at commit time -- only their hash does. The
    merchant must retain this receipt to reveal evidence at dispute time."""

    commitment_id: str
    artifact_type: str
    artifact: bytes
    salt: bytes
    event_time: datetime

    def to_json(self) -> str:
        return json.dumps(
            {
                "commitment_id": self.commitment_id,
                "artifact_type": self.artifact_type,
                "artifact": self.artifact.decode("utf-8", errors="backslashreplace"),
                "salt": self.salt.hex(),
                "event_time": self.event_time.isoformat(),
            }
        )


class MerchantSDK:
    """Thin client a merchant's order-management system calls at event time."""

    def __init__(self, merchant_id: str, provenance: ProvenanceService, salt_bytes: int = 16):
        self.merchant_id = merchant_id
        self._provenance = provenance
        self._salt_bytes = salt_bytes

    def emit(
        self,
        artifact_type: str,
        artifact: bytes,
        event_time: Optional[datetime] = None,
    ) -> CommitmentReceipt:
        """
        Call this the instant a real-world event happens -- not at dispute
        time, which would defeat the entire point. `artifact` is whatever
        canonical representation the merchant wants to be able to prove
        later (a JSON blob of order+shipment fields, a delivery-scan
        payload, a rendered ToS-acceptance record, ...).
        """
        salt = os.urandom(self._salt_bytes)
        commitment_hash = compute_commitment_hash(artifact, salt)
        event_time = event_time or datetime.now(timezone.utc)

        commitment = self._provenance.commit(
            merchant_id=self.merchant_id,
            artifact_type=artifact_type,
            commitment_hash=commitment_hash,
            event_time=event_time,
        )
        return CommitmentReceipt(
            commitment_id=commitment.commitment_id,
            artifact_type=artifact_type,
            artifact=artifact,
            salt=salt,
            event_time=event_time,
        )

    def reveal(self, receipt: CommitmentReceipt, dispute_filed_at: Optional[datetime] = None):
        """Called only if a dispute actually occurs (§A1: artifact revealed
        only then, and only to Amex)."""
        return self._provenance.reveal_and_verify(
            commitment_id=receipt.commitment_id,
            artifact=receipt.artifact,
            salt=receipt.salt,
            dispute_filed_at=dispute_filed_at,
        )

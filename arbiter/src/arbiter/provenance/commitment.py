"""
ADEC: Ante-Dispute Evidence Commitments (A1) -- the provenance service.

At the moment a real-world event occurs (order placed, shipped, delivered,
refund policy shown), the merchant computes c = H(artifact || salt) and
posts only the hash here. The artifact itself never leaves the merchant
until a dispute actually exists. This service owns the commit-time API and
the dispute-time reveal-and-verify check that recomputes the hash and
proves the sealed root predates the dispute filing.

CLAUDE.md invariant #12: `committed_at` is server-observed (this class's
`commit()` stamps it with `datetime.now()` unless a caller explicitly
back-dates it for testing); the merchant-claimed `event_time` is carried
separately by the DB layer and is never authoritative for tier gating.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional

from arbiter.evidence.models import ProvenanceTier

from .merkle import CommitmentVerification, TransparencyLog


def compute_commitment_hash(artifact: bytes, salt: bytes) -> bytes:
    """c = H(artifact || salt). The salt is what makes a bare `delivered:
    true` unbruteforceable from its hash alone (A1)."""
    return hashlib.sha256(artifact + salt).digest()


@dataclass
class AdecCommitment:
    commitment_id: str
    merchant_id: str
    commitment_hash: bytes
    artifact_type: str
    leaf_index: int
    committed_at: datetime
    revealed_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "commitment_id": self.commitment_id,
            "merchant_id": self.merchant_id,
            "commitment_hash": self.commitment_hash.hex(),
            "artifact_type": self.artifact_type,
            "leaf_index": self.leaf_index,
            "committed_at": self.committed_at.isoformat(),
            "revealed_at": self.revealed_at.isoformat() if self.revealed_at else None,
        }


class ProvenanceService:
    def __init__(self, log: Optional[TransparencyLog] = None):
        self.log = log or TransparencyLog()
        self._commitments: Dict[str, AdecCommitment] = {}

    def commit(
        self,
        merchant_id: str,
        artifact_type: str,
        commitment_hash: bytes,
        event_time: Optional[datetime] = None,
    ) -> AdecCommitment:
        """POST /v1/commitments -- merchant SDK call at event time.

        `committed_at` is stamped server-side, right now -- NOT from
        `event_time`, which is the merchant's own (unverifiable) claim about
        when the underlying real-world event happened. `event_time` is
        stored by the DB layer for display/audit only.
        """
        commitment_id = str(uuid.uuid4())
        leaf_index = self.log.append(commitment_hash)
        commitment = AdecCommitment(
            commitment_id=commitment_id,
            merchant_id=merchant_id,
            commitment_hash=commitment_hash,
            artifact_type=artifact_type,
            leaf_index=leaf_index,
            committed_at=datetime.now(timezone.utc),
        )
        self._commitments[commitment_id] = commitment
        return commitment

    def seal(self, at: Optional[float] = None):
        """Batch pending commitments into a new signed, timestamped root.
        Spec cadence is every 10s of wall clock (A1); exposed here as an
        explicit call so a scheduler controls timing."""
        return self.log.seal_batch(at=at)

    def get(self, commitment_id: str) -> Optional[AdecCommitment]:
        return self._commitments.get(commitment_id)

    def reveal_and_verify(
        self,
        commitment_id: str,
        artifact: bytes,
        salt: bytes,
        dispute_filed_at: Optional[datetime] = None,
    ) -> CommitmentVerification:
        """
        Dispute-time reveal: the merchant discloses artifact||salt. We
        recompute the hash locally (never trusting a hash the merchant
        hands us post-hoc) and verify its Merkle inclusion in a root whose
        TSA timestamp strictly precedes the dispute filing time.
        """
        commitment = self._commitments.get(commitment_id)
        if commitment is None:
            raise KeyError(f"unknown commitment_id {commitment_id!r}")

        recomputed = compute_commitment_hash(artifact, salt)
        deadline_ns = int(dispute_filed_at.timestamp() * 1e9) if dispute_filed_at else None

        if recomputed != commitment.commitment_hash:
            # The revealed artifact does not match what was committed --
            # treat as a failed verification rather than raising, so callers
            # uniformly branch on `.ok` regardless of failure mode.
            return CommitmentVerification(
                leaf_index=commitment.leaf_index,
                inclusion_valid=False,
                sth_signature_valid=False,
                tsa_signature_valid=False,
                committed_at_unix_ns=0,
                predates_deadline=False,
                proof=self.log.inclusion_proof(commitment.leaf_index),
            )

        result = self.log.verify_commitment(commitment.commitment_hash, commitment.leaf_index, deadline_ns)
        commitment.revealed_at = datetime.now(timezone.utc)
        return result

    @staticmethod
    def provenance_tier_for(verification: Optional[CommitmentVerification]) -> ProvenanceTier:
        """Committed evidence carries the highest trust weight in the
        rulepack (A1: incentive-compatible without a mandate). CLAUDE.md
        invariant #9: evidence degrades, never rejected -- failed ADEC
        verification demotes to SUBMITTED, it does not reject the claim."""
        if verification is not None and verification.ok:
            return ProvenanceTier.COMMITTED
        return ProvenanceTier.SUBMITTED

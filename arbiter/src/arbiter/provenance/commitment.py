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
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional

from arbiter.evidence.models import ProvenanceTier

from .merkle import CommitmentVerification, TransparencyLog

logger = logging.getLogger(__name__)


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
    """
    Durability, stated as the defect it fixes: `_commitments` used to be
    the ONLY home for every ADEC commitment, and nothing wrote to the
    `adec_commitment` / `merkle_batch` tables the migrations create. A
    restart therefore destroyed the entire commitment history, and a
    two-replica deployment had two divergent logs. Passing a `store`
    (arbiter.provenance.store.ProvenanceStore) makes Postgres the system of
    record and this object a rebuildable cache in front of it. With
    `store=None` the old in-memory behaviour is retained, which is what the
    property tests and `demo.py` use.
    """

    def __init__(self, log: Optional[TransparencyLog] = None, store=None):
        self.log = log or TransparencyLog()
        self.store = store
        self._commitments: Dict[str, AdecCommitment] = {}

    # -- durability --------------------------------------------------------

    def rehydrate(self) -> int:
        """Rebuild the in-memory log and commitment index from the store.
        Called at process start (arbiter.main's lifespan) and whenever the
        cache is found to be behind the database. Returns the number of
        commitments loaded."""
        if self.store is None:
            return 0
        try:
            commitments, batches = self.store.load_all()
        except Exception as exc:  # DB unreachable at boot -- degrade, don't crash
            logger.warning("could not rehydrate ADEC log from store: %s", exc)
            return 0

        fresh = TransparencyLog(operator=self.log.operator, tsa=self.log.tsa)
        self._commitments = {}
        for (cid, merchant_id, chash, artifact_type, leaf_index, committed_at, revealed_at) in commitments:
            idx = fresh.append(chash)
            if idx != leaf_index:
                raise RuntimeError(
                    f"ADEC log is not contiguous: commitment {cid} claims leaf_index="
                    f"{leaf_index} but rebuilds at {idx}. The append-only sequence has a "
                    f"gap or a duplicate; refusing to serve an inconsistent log."
                )
            self._commitments[str(cid)] = AdecCommitment(
                commitment_id=str(cid), merchant_id=str(merchant_id), commitment_hash=chash,
                artifact_type=artifact_type, leaf_index=leaf_index,
                committed_at=committed_at, revealed_at=revealed_at,
            )
        # Re-seal up to each recorded tree size so inclusion/consistency
        # proofs are servable against exactly the roots that were signed.
        for batch in batches:
            fresh.reseal_from_record(
                root_hash=batch["root_hash"], tree_size=batch["tree_size"],
                signature=batch["sth_signature"], tsa_token_der=batch["tsa_token"],
                sealed_at=batch["sealed_at"],
            )
        self.log = fresh
        logger.info("ADEC log rehydrated: %d commitments, %d sealed tree heads",
                    len(self._commitments), len(batches))
        return len(self._commitments)

    # -- write path --------------------------------------------------------

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
        stored for display/audit only.
        """
        committed_at = datetime.now(timezone.utc)

        if self.store is not None:
            # The database assigns leaf_index under an advisory lock, so it
            # is authoritative across replicas. If our cache is behind, we
            # rebuild before appending rather than guessing.
            commitment_id, leaf_index = self.store.append_commitment(
                merchant_id=merchant_id, artifact_type=artifact_type,
                commitment_hash=commitment_hash, event_time=event_time, committed_at=committed_at,
            )
            if leaf_index != len(self.log.leaf_hashes()):
                self.rehydrate()
            local_index = self.log.append(commitment_hash)
            if local_index != leaf_index:
                raise RuntimeError(
                    f"ADEC log cache diverged from the store (store leaf_index={leaf_index}, "
                    f"cache={local_index}) -- refusing to serve an inconsistent log."
                )
        else:
            commitment_id = str(uuid.uuid4())
            leaf_index = self.log.append(commitment_hash)

        commitment = AdecCommitment(
            commitment_id=commitment_id,
            merchant_id=merchant_id,
            commitment_hash=commitment_hash,
            artifact_type=artifact_type,
            leaf_index=leaf_index,
            committed_at=committed_at,
        )
        self._commitments[commitment_id] = commitment
        return commitment

    def seal(self, at: Optional[float] = None):
        """Batch pending commitments into a new signed, timestamped root.
        Spec cadence is every 10s of wall clock (A1); exposed here as an
        explicit call so a scheduler controls timing."""
        sth = self.log.seal_batch(at=at)
        if sth is not None and self.store is not None:
            self.store.record_batch(
                root_hash=sth.root_hash, tree_size=sth.tree_size,
                sth_signature=sth.signature, tsa_token=sth.tsa_token.signature,
                sealed_at=datetime.fromtimestamp(sth.timestamp_unix_ns / 1e9, tz=timezone.utc),
            )
            self.store.link_batches()
        return sth

    def get(self, commitment_id: str) -> Optional[AdecCommitment]:
        commitment = self._commitments.get(commitment_id)
        if commitment is None and self.store is not None and self._commitments:
            # Another replica may have created it since we last rehydrated.
            self.rehydrate()
            commitment = self._commitments.get(commitment_id)
        return commitment

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
            # uniformly branch on `.ok` regardless of failure mode. Recorded
            # as a failed reveal: a merchant revealing an artifact that does
            # not match its own commitment is a signal a reviewer wants.
            if self.store is not None:
                self.store.mark_revealed(commitment.commitment_id, datetime.now(timezone.utc), False)
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
        if self.store is not None:
            # Reveals are part of the provenance record: an auditor asking
            # "when was this artifact disclosed, and did it verify?" must
            # not depend on the answer still being in some process's RAM.
            self.store.mark_revealed(commitment.commitment_id, commitment.revealed_at, bool(result.ok))
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

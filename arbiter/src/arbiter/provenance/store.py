"""
Durable storage for the ADEC transparency log.

Stated as the defect it fixes: `ProvenanceService._commitments` and
`TransparencyLog._leaf_hashes` were plain in-process Python containers, and
nothing ever wrote to the `adec_commitment` or `merkle_batch` tables the
migrations create. The consequence was not a scaling limitation -- it was
that ADEC did not work. A restart destroyed every commitment ever made; a
merchant that committed against replica A and revealed against replica B
got "unknown commitment_id". The entire property the scheme exists to
provide -- "this artifact provably existed before the dispute did" -- was
backed by RAM.

The Merkle algorithms themselves (`merkle.py`, `rfc6962.py`) stay pure and
stay tested as pure functions. This module is the repository around them:
Postgres is the system of record, the in-memory log is a rebuildable cache,
and appends are serialised with a Postgres advisory lock so two replicas
cannot assign the same leaf index.

Ordering guarantee: `leaf_index` is assigned inside the advisory lock from
the database's own max, never from the in-memory cache's length, so the log
remains a single totally-ordered append-only sequence no matter how many
processes write to it.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Arbitrary but fixed 64-bit key for pg_advisory_xact_lock. One lock for
# the whole log: appends are serialised globally, which is what "append-only
# totally-ordered log" means.
_LOG_APPEND_LOCK_ID = 0x4152_4249_5445_5201  # "ARBITER" + 1


class ProvenanceStore:
    """Postgres-backed durable state for the ADEC log. Opens its own
    short-lived sessions: the ADEC log's lifetime is not scoped to any one
    HTTP request, and tying it to the request session would make a rollback
    elsewhere in the request silently un-commit a commitment."""

    def __init__(self, session_factory=None):
        if session_factory is None:
            from arbiter.db.session import session_scope

            session_factory = session_scope
        self._session_scope = session_factory

    # -- read path ---------------------------------------------------------

    def load_all(self) -> Tuple[List[Tuple[str, str, bytes, str, int, datetime, Optional[datetime]]], List[dict]]:
        """Returns (commitment rows ordered by leaf_index, batch rows
        ordered by tree_size) for rehydrating an in-memory log."""
        from arbiter.db import models as m

        with self._session_scope() as session:
            commitments = session.execute(
                select(
                    m.AdecCommitmentRow.commitment_id,
                    m.AdecCommitmentRow.merchant_id,
                    m.AdecCommitmentRow.commitment_hash,
                    m.AdecCommitmentRow.artifact_type,
                    m.AdecCommitmentRow.leaf_index,
                    m.AdecCommitmentRow.committed_at,
                    m.AdecCommitmentRow.revealed_at,
                )
                .where(m.AdecCommitmentRow.leaf_index.isnot(None))
                .order_by(m.AdecCommitmentRow.leaf_index)
            ).all()
            batches = session.execute(
                select(m.MerkleBatch).order_by(m.MerkleBatch.tree_size)
            ).scalars().all()
            batch_dicts = [
                {
                    "batch_id": str(b.batch_id),
                    "root_hash": b.root_hash,
                    "tree_size": b.tree_size,
                    "sth_signature": b.sth_signature,
                    "tsa_token": b.tsa_token,
                    "sealed_at": b.sealed_at,
                }
                for b in batches
            ]
        return [tuple(r) for r in commitments], batch_dicts

    def next_leaf_index(self, session: Session) -> int:
        """Must be called inside `append_lock`."""
        from arbiter.db import models as m

        current = session.execute(select(func.max(m.AdecCommitmentRow.leaf_index))).scalar()
        return 0 if current is None else current + 1

    # -- write path --------------------------------------------------------

    def append_commitment(
        self,
        merchant_id: str,
        artifact_type: str,
        commitment_hash: bytes,
        event_time: Optional[datetime],
        committed_at: datetime,
    ) -> Tuple[str, int]:
        """Serialised append. Returns (commitment_id, leaf_index).

        `committed_at` is server-observed and authoritative (CLAUDE.md
        invariant #14); `event_time` is the merchant's own unverifiable
        claim about when the underlying real-world event happened and is
        stored for display only.
        """
        from arbiter.db import models as m

        with self._session_scope() as session:
            # Serialise against every other appender, in every replica, for
            # the duration of this transaction.
            session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOG_APPEND_LOCK_ID})
            leaf_index = self.next_leaf_index(session)
            commitment_id = uuid.uuid4()
            session.add(
                m.AdecCommitmentRow(
                    commitment_id=commitment_id,
                    merchant_id=uuid.UUID(merchant_id),
                    commitment_hash=commitment_hash,
                    artifact_type=artifact_type,
                    event_time=event_time or committed_at,
                    leaf_index=leaf_index,
                    committed_at=committed_at,
                )
            )
        return str(commitment_id), leaf_index

    def record_batch(self, root_hash: bytes, tree_size: int, sth_signature: bytes,
                     tsa_token: Optional[bytes], sealed_at: datetime) -> None:
        from arbiter.db import models as m

        with self._session_scope() as session:
            existing = session.execute(
                select(m.MerkleBatch).where(m.MerkleBatch.tree_size == tree_size)
            ).scalars().first()
            if existing is not None:
                # A tree head at this size already exists. Two replicas
                # sealing concurrently is benign only if they agree; if they
                # do not, that is a split view and must be loud.
                if existing.root_hash != root_hash:
                    raise RuntimeError(
                        f"SPLIT VIEW: two distinct Merkle roots recorded at tree_size={tree_size} "
                        f"({existing.root_hash.hex()} vs {root_hash.hex()}). The transparency log's "
                        f"append-only guarantee has been violated -- do not accept further "
                        f"commitments until this is investigated."
                    )
                return
            session.add(
                m.MerkleBatch(
                    batch_id=uuid.uuid4(), root_hash=root_hash, tree_size=tree_size,
                    sth_signature=sth_signature, tsa_token=tsa_token, sealed_at=sealed_at,
                )
            )

    def mark_revealed(self, commitment_id: str, revealed_at: datetime, reveal_valid: bool) -> None:
        from arbiter.db import models as m

        with self._session_scope() as session:
            row = session.get(m.AdecCommitmentRow, uuid.UUID(commitment_id))
            if row is not None:
                row.revealed_at = revealed_at
                row.reveal_valid = reveal_valid

    def link_batches(self) -> None:
        """Backfill `adec_commitment.batch_id` for commitments now covered
        by a sealed tree head. Cosmetic for the API, load-bearing for an
        auditor reconstructing which root covers which commitment."""
        from arbiter.db import models as m

        with self._session_scope() as session:
            batches = session.execute(
                select(m.MerkleBatch).order_by(m.MerkleBatch.tree_size)
            ).scalars().all()
            if not batches:
                return
            unlinked = session.execute(
                select(m.AdecCommitmentRow).where(m.AdecCommitmentRow.batch_id.is_(None))
            ).scalars().all()
            for row in unlinked:
                if row.leaf_index is None:
                    continue
                covering = next((b for b in batches if b.tree_size > row.leaf_index), None)
                if covering is not None:
                    row.batch_id = covering.batch_id


def utcnow() -> datetime:
    return datetime.now(timezone.utc)

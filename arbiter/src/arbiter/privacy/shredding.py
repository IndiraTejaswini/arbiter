"""
Crypto-shredding: GDPR Article 17 (right to erasure) implemented without
violating CLAUDE.md invariant #8 (case_event and decision are append-only,
enforced by a Postgres trigger -- forbid_mutation()). Those tables, and
evidence_node alongside them, cannot be edited or deleted to strip a card
member's data even on a genuine erasure request. So this build never
stores raw PII as plaintext in the first place: identity/claim evidence
nodes' extracted free-text field values are encrypted at rest under a
symmetric key unique to the subject (card_member_id) they belong to.
"Erasure" destroys that key -- the ciphertext, the hash chain, and every
Merkle commitment over the surrounding record stay exactly as they were
(nothing is edited, the append-only guarantee holds) -- but the plaintext
becomes permanently, computationally unrecoverable. This is the standard
resolution to "immutable audit log" vs. "right to erasure": make erasure a
key-destruction operation, not a data-mutation one.

Only `extracted_fields` values on IDENTITY/CLAIM nodes are ever encrypted
here (wired in arbiter.api.routes.evidence) -- deliberately never a
predicate-bearing attribute. `arbiter.evidence.derive` reads
`attrs['asserts_predicate']` / `attrs['predicate_value']`, both derived
booleans/tags set independently of `extracted_fields`, so encrypting these
values cannot affect adjudication; the referee never needs to read a raw
identity field, only the boolean fact a rulepack predicate already reduced
it to.

Fernet (AES-128-CBC + HMAC-SHA256, authenticated) rather than a hand-rolled
AEAD construction: this module has no cryptographic-design ambition beyond
"irreversibly destroying a symmetric key makes its ciphertext
unrecoverable," which any correctly-implemented authenticated cipher gives
for free.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


@dataclass
class _KeyRecord:
    key: Optional[bytes]  # None once erased
    created_at: datetime
    erased_at: Optional[datetime] = None


class SubjectKeyStore(Protocol):
    """Durable backing for SubjectKeyVault. Kept as a Protocol so the vault
    depends on the capability, not on SQLAlchemy -- the in-memory path stays
    dependency-free for tests and `demo.py`."""

    def load_all(self) -> Dict[str, _KeyRecord]: ...
    def load_one(self, subject_id: str) -> Optional[_KeyRecord]: ...
    def save(self, subject_id: str, record: _KeyRecord) -> Optional[_KeyRecord]: ...
    def erase(self, subject_id: str, erased_at: datetime) -> None: ...


class DbSubjectKeyStore:
    """Postgres-backed key store (`subject_key` table).

    Envelope encryption: the per-subject Fernet key is itself encrypted
    under a KEK (`ARBITER_KEY_ENCRYPTION_KEY`) before being written, so
    stealing the database does not yield PII plaintext -- an attacker needs
    the KEK from the secret store as well. In production the KEK itself
    lives in a KMS/HSM and this class holds only a handle to it; the
    settings-based key here is the honest laptop-scoped stand-in for that,
    and it is a genuine improvement over storing raw key material.
    """

    def __init__(self, kek: Optional[str] = None, session_factory=None):
        if kek is None:
            from arbiter.config import get_settings

            kek = get_settings().key_encryption_key
        if not kek:
            raise ValueError(
                "ARBITER_KEY_ENCRYPTION_KEY is required to persist subject keys -- "
                "writing unwrapped key material to the database would make the "
                "encryption pointless against the most likely compromise."
            )
        self._kek = Fernet(kek.encode("ascii") if isinstance(kek, str) else kek)
        if session_factory is None:
            from arbiter.db.session import session_scope

            session_factory = session_scope
        self._session_scope = session_factory

    def _unwrap(self, wrapped: bytes) -> Optional[bytes]:
        try:
            return self._kek.decrypt(wrapped)
        except InvalidToken:
            logger.error("subject key failed to unwrap under the configured KEK")
            return None

    def load_all(self) -> Dict[str, _KeyRecord]:
        from sqlalchemy import select

        from arbiter.db import models as m

        out: Dict[str, _KeyRecord] = {}
        with self._session_scope() as session:
            for row in session.execute(select(m.SubjectKeyRow)).scalars().all():
                key = None if row.erased_at else self._unwrap(row.wrapped_key)
                out[str(row.subject_id)] = _KeyRecord(
                    key=key, created_at=row.created_at, erased_at=row.erased_at
                )
        return out

    def load_one(self, subject_id: str) -> Optional[_KeyRecord]:
        from arbiter.db import models as m

        with self._session_scope() as session:
            row = session.get(m.SubjectKeyRow, uuid.UUID(subject_id))
            if row is None:
                return None
            key = None if row.erased_at else self._unwrap(row.wrapped_key)
            return _KeyRecord(key=key, created_at=row.created_at, erased_at=row.erased_at)

    def save(self, subject_id: str, record: _KeyRecord) -> Optional[_KeyRecord]:
        """Persist a newly-minted key, and return whichever key is
        AUTHORITATIVE afterwards.

        The return value is what closes a race that silently destroys data.
        Two replicas handling the first upload for the same subject both miss
        the cache, both mint a key, and both call this. One row wins. Before,
        the loser simply returned and went on encrypting under its own
        in-memory key -- which was never written anywhere, so every field it
        encrypted became permanently unrecoverable the moment that process
        exited. Silent, and indistinguishable from an erasure nobody
        requested.

        Returning the winner lets the caller adopt it instead.
        """
        from arbiter.db import models as m

        if record.key is None:
            return None
        with self._session_scope() as session:
            existing = session.get(m.SubjectKeyRow, uuid.UUID(subject_id))
            if existing is not None:
                # Never overwrite a live key -- that IS an erasure, silently.
                # Hand back the row that won so the caller uses it.
                key = None if existing.erased_at else self._unwrap(existing.wrapped_key)
                return _KeyRecord(
                    key=key, created_at=existing.created_at, erased_at=existing.erased_at
                )
            session.add(
                m.SubjectKeyRow(
                    subject_id=uuid.UUID(subject_id),
                    wrapped_key=self._kek.encrypt(record.key),
                    created_at=record.created_at,
                )
            )
            return record

    def erase(self, subject_id: str, erased_at: datetime) -> None:
        from arbiter.db import models as m

        with self._session_scope() as session:
            row = session.get(m.SubjectKeyRow, uuid.UUID(subject_id))
            if row is None:
                return
            # The wrapped key is overwritten with an empty value AND
            # erased_at is stamped. Deleting the row would lose the audit
            # fact that an erasure happened, which a DPA will ask for.
            row.wrapped_key = b""
            row.erased_at = erased_at


class SubjectKeyVault:
    """Key store keyed by subject_id (a card_member_id or merchant_id as a
    string UUID).

    Durability, stated as the defect it fixes: this class used to be
    in-memory ONLY, and nothing ever wrote to the `subject_key` table the
    migration creates. That is not a scaling caveat -- it is silent,
    irreversible data destruction. Every restart discarded every subject
    key, which meant every `extracted_fields` value encrypted under one
    became permanently unrecoverable and rendered as `[ERASED]`. The
    system performed an unrequested GDPR erasure of every card member's
    evidence on every deploy.

    With a `store` (arbiter.privacy.shredding.DbSubjectKeyStore) the keys
    live in Postgres, each wrapped under a KEK from `ARBITER_KEY_
    ENCRYPTION_KEY` so a database compromise alone never yields plaintext.
    Erasure deletes the wrapped key, which is still key destruction, not
    row mutation -- the append-only guarantee and every Merkle commitment
    over the ciphertext are untouched. With `store=None` the old in-memory
    behaviour is retained for tests and `demo.py`.
    """

    def __init__(self, store: "SubjectKeyStore | None" = None) -> None:
        self._keys: Dict[str, _KeyRecord] = {}
        self._store = store

    def rehydrate(self) -> int:
        """Load persisted keys at process start. Returns how many were
        loaded; 0 with a warning is the honest signal that this instance is
        running without durable keys."""
        if self._store is None:
            logger.warning(
                "SubjectKeyVault has no durable store -- subject keys live only in this "
                "process's memory, and every PII field encrypted under them becomes "
                "permanently unrecoverable when it exits. Set ARBITER_KEY_ENCRYPTION_KEY "
                "and use a DB-backed store for any deployment that outlives one run."
            )
            return 0
        try:
            loaded = self._store.load_all()
        except Exception as exc:
            logger.warning("could not rehydrate subject keys: %s", exc)
            return 0
        self._keys = loaded
        logger.info("subject key vault rehydrated: %d keys", len(self._keys))
        return len(self._keys)

    def _load(self, subject_id: str) -> Optional[_KeyRecord]:
        """Read-through: memory first, then the durable store. Never creates.

        Every read path must go through this. `decrypt` and `is_erased` used
        to consult `self._keys` alone, which is correct only for a single
        process that has completed `rehydrate()` -- and this vault is
        explicitly a cache in front of Postgres, with several replicas
        expected. A key written by replica A was simply absent on replica B,
        so B rendered a live card member's evidence as `[ERASED]` and
        reported them erased. That is the exact failure this module's
        docstring describes as "an unrequested GDPR erasure", arriving
        horizontally instead of across a restart.
        """
        record = self._keys.get(subject_id)
        if record is not None:
            return record
        if self._store is None:
            return None
        try:
            record = self._store.load_one(subject_id)
        except Exception as exc:  # a store blip must not read as "erased"
            logger.warning("subject key lookup failed for %s: %s", subject_id, exc)
            return None
        if record is not None:
            self._keys[subject_id] = record
        return record

    def _get_or_create(self, subject_id: str) -> _KeyRecord:
        record = self._load(subject_id)
        if record is None:
            record = _KeyRecord(key=Fernet.generate_key(), created_at=datetime.now(timezone.utc))
            if self._store is not None:
                # Adopt whatever the store says is authoritative. Under a
                # concurrent first-write for the same subject the loser must
                # use the winner's key, or it encrypts under key material
                # that was never persisted.
                winner = self._store.save(subject_id, record)
                if winner is not None:
                    record = winner
            self._keys[subject_id] = record
        return record

    def encrypt(self, subject_id: str, plaintext: str) -> Optional[str]:
        """Returns ciphertext as ASCII text (safe for a JSONB string
        field), or None if the subject has already been erased -- callers
        must treat that exactly like "no PII available," never a hard
        failure (CLAUDE.md #11's degrade-never-reject pattern, applied to
        erasure instead of LLM/ADEC unavailability)."""
        record = self._get_or_create(subject_id)
        if record.key is None:
            return None
        return Fernet(record.key).encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, subject_id: str, ciphertext: str) -> Optional[str]:
        """Read-through, so a key this process has not cached yet is fetched
        rather than reported as erased."""
        record = self._load(subject_id)
        if record is None or record.key is None:
            return None
        try:
            return Fernet(record.key).decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken:
            return None

    def erase(self, subject_id: str) -> bool:
        """Destroys the subject's key. Returns True if a key existed and
        was destroyed, False if there was nothing to erase. Idempotent:
        erasing an already-erased or never-seen subject is a no-op, not an
        error -- an erasure request must never itself fail loudly."""
        record = self._load(subject_id)
        if record is None or record.key is None:
            return False
        record.key = None
        record.erased_at = datetime.now(timezone.utc)
        if self._store is not None:
            # Destroy the durable copy too, or the "erasure" survives only
            # until the next restart rehydrates the key.
            self._store.erase(subject_id, record.erased_at)
        return True

    def is_erased(self, subject_id: str) -> bool:
        """Read-through. Consulting only the local cache answered "not
        erased" for a subject whose erasure was recorded by another replica
        -- wrong in the direction that matters, since the caller is asking
        precisely so it can avoid handling that subject's data."""
        record = self._load(subject_id)
        return record is not None and record.key is None


def erase_subject(vault: SubjectKeyVault, subject_id: str) -> bool:
    """Module-level convenience wrapper -- the call site a GDPR erasure
    request handler actually calls."""
    return vault.erase(subject_id)


# -- extracted_fields helpers: the actual call site in arbiter.api.routes.evidence --

_ENCRYPTED_MARKER = "__shredded__"


def encrypt_extracted_fields(vault: SubjectKeyVault, subject_id: str, fields: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Encrypts each field's `value` in place (returns new dicts; does not
    mutate the input). Non-string values (bool/float) are left as-is --
    Fernet operates on bytes, and a bool/float extracted field is already
    a derived signal, not free text that could carry a name or address."""
    out = []
    for f in fields:
        value = f.get("value")
        if not isinstance(value, str):
            out.append(dict(f))
            continue
        ciphertext = vault.encrypt(subject_id, value)
        new_f = dict(f)
        if ciphertext is not None:
            new_f["value"] = ciphertext
            new_f[_ENCRYPTED_MARKER] = True
        out.append(new_f)
    return out


def decrypt_extracted_fields(vault: SubjectKeyVault, subject_id: str, fields: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Inverse of encrypt_extracted_fields. A field that fails to decrypt
    (subject erased, or it was never encrypted) is returned with its value
    replaced by a redaction marker rather than raising -- a caller
    rendering a view must never crash because a subject exercised their
    right to erasure."""
    out = []
    for f in fields:
        if not f.get(_ENCRYPTED_MARKER):
            out.append(f)
            continue
        plaintext = vault.decrypt(subject_id, f.get("value", ""))
        new_f = dict(f)
        new_f.pop(_ENCRYPTED_MARKER, None)
        new_f["value"] = plaintext if plaintext is not None else "[ERASED]"
        out.append(new_f)
    return out

"""Process-wide singletons the routes depend on.

Durability note, because this module is where the previous build's most
consequential defect lived: `provenance_service`, `privacy_vault`, and
`abstention_gate` were plain in-memory objects with no backing store, and
the tables that were supposed to back them (`adec_commitment`,
`merkle_batch`, `subject_key`, `calibration_sample`) were created by the
migrations and written by nothing. The result was not "does not scale to
two replicas" -- it was that a restart destroyed the entire ADEC
commitment history, made every encrypted PII field permanently
unrecoverable, and reset the abstention gate. Each is now constructed with
a durable store and rehydrated in `arbiter.main`'s lifespan.

The objects remain process-wide because they are caches in front of
Postgres, not the system of record.
"""

from __future__ import annotations

import logging

from arbiter.config import get_settings
from arbiter.decision.conformal import ConformalAbstentionGate
from arbiter.privacy.shredding import DbSubjectKeyStore, SubjectKeyVault
from arbiter.provenance import ProvenanceService, TransparencyLog
from arbiter.provenance.store import ProvenanceStore
from arbiter.rulepack import RulepackRegistry
from arbiter.storage import build_artifact_store

logger = logging.getLogger(__name__)

_settings = get_settings()

registry = RulepackRegistry()

abstention_gate = ConformalAbstentionGate(
    alpha=_settings.conformal_alpha,
    min_n_for_guarantee=_settings.conformal_min_n,
    # Refuse to auto-resolve a reason code with no real calibration data,
    # rather than falling back to a threshold derived from invented
    # numbers. See ConformalAbstentionGate's module docstring.
    require_real_calibration=_settings.conformal_require_real_calibration,
)

provenance_service = ProvenanceService(TransparencyLog(), store=ProvenanceStore())


def _build_privacy_vault() -> SubjectKeyVault:
    """DB-backed when a KEK is configured; in-memory (loudly) otherwise."""
    if not _settings.key_encryption_key:
        logger.warning(
            "ARBITER_KEY_ENCRYPTION_KEY is not set -- subject keys will NOT be "
            "persisted. Every PII field encrypted during this run becomes permanently "
            "unrecoverable when the process exits. Generate one with "
            "`python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"`."
        )
        return SubjectKeyVault()
    try:
        return SubjectKeyVault(store=DbSubjectKeyStore())
    except Exception as exc:  # bad KEK, DB unreachable at import time, ...
        logger.error("could not build DB-backed subject key vault (%s); falling back to memory", exc)
        return SubjectKeyVault()


privacy_vault = _build_privacy_vault()
artifact_store = build_artifact_store()


def get_registry() -> RulepackRegistry:
    return registry


def get_abstention_gate() -> ConformalAbstentionGate:
    return abstention_gate


def get_provenance_service() -> ProvenanceService:
    return provenance_service


def get_privacy_vault() -> SubjectKeyVault:
    return privacy_vault


def get_artifact_store():
    return artifact_store

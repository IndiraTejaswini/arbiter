"""
Ed25519 event signing -- a distinct signing identity from the
provenance-log operator and the TSA (A1): compromising one key must not
compromise the others.

Key persistence, stated honestly as a bug this fixes rather than a design
choice: `EventSigner` used to default to `Ed25519PrivateKey.generate()` on
every construction, so every process restart minted a fresh key and
silently invalidated every signature the previous key had ever produced.
It now loads a persistent key from `ARBITER_SIGNING_KEY_SEED` when
configured, and falls back to an ephemeral key -- loudly logged as such --
only when it isn't.

Rotation is a tracked epoch, not a silent swap. Every signature is paired
with the epoch of the key that produced it (`case_event.key_epoch`,
`decision.key_epoch`); `KeyRing` holds every epoch a deployment has ever
used so a signature made before a rotation stays verifiable after one.
Rotating the *active* signing key (bumping `ARBITER_SIGNING_KEY_EPOCH` and
adding the new seed to `ARBITER_SIGNING_KEY_RING`) never retroactively
invalidates history the way the old unconditional-generate() behavior did.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

logger = logging.getLogger(__name__)


def _load_default_key() -> tuple[Ed25519PrivateKey, int]:
    from arbiter.config import get_settings

    settings = get_settings()
    if settings.signing_key_seed:
        seed = bytes.fromhex(settings.signing_key_seed)
        return Ed25519PrivateKey.from_private_bytes(seed), settings.signing_key_epoch

    logger.warning(
        "ARBITER_SIGNING_KEY_SEED is not set -- generating an EPHEMERAL Ed25519 "
        "signing key for this process. Every event/decision signature made "
        "during this run becomes UNVERIFIABLE the moment the process restarts. "
        "Set ARBITER_SIGNING_KEY_SEED (32 random bytes, hex-encoded) in any "
        "deployment where the audit trail needs to outlive one process."
    )
    return Ed25519PrivateKey.generate(), 0


class EventSigner:
    def __init__(self, private_key: Optional[Ed25519PrivateKey] = None, epoch: Optional[int] = None):
        if private_key is None:
            private_key, epoch = _load_default_key()
        self._key = private_key
        self._public_key = self._key.public_key()
        self.epoch = epoch if epoch is not None else 0

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


class KeyRing:
    """Every signing key epoch a deployment has ever used, for verifying
    signatures made before the most recent rotation. Loaded from
    `ARBITER_SIGNING_KEY_RING` (JSON: {"0": "<hex seed>", "1": "<hex
    seed>"}); falls back to a single-entry ring built from
    `ARBITER_SIGNING_KEY_SEED`/`ARBITER_SIGNING_KEY_EPOCH` (or an ephemeral
    key, same fallback as EventSigner) if unset."""

    def __init__(self, keys: Optional[Dict[int, Ed25519PrivateKey]] = None):
        self._keys = keys if keys is not None else self._load_from_settings()

    @staticmethod
    def _load_from_settings() -> Dict[int, Ed25519PrivateKey]:
        from arbiter.config import get_settings

        settings = get_settings()
        if settings.signing_key_ring:
            return {
                int(epoch): Ed25519PrivateKey.from_private_bytes(bytes.fromhex(seed_hex))
                for epoch, seed_hex in settings.signing_key_ring.items()
            }
        key, epoch = _load_default_key()
        return {epoch: key}

    def verify(self, epoch: int, payload: bytes, signature: bytes) -> bool:
        key = self._keys.get(epoch)
        if key is None:
            return False
        try:
            key.public_key().verify(signature, payload)
            return True
        except InvalidSignature:
            return False

    def public_key(self, epoch: int) -> Optional[Ed25519PublicKey]:
        key = self._keys.get(epoch)
        return key.public_key() if key else None

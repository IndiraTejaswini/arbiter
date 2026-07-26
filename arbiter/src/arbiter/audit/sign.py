"""Ed25519 event signing -- a distinct signing identity from the
provenance-log operator and the TSA (A1): compromising one key must not
compromise the others."""

from __future__ import annotations

from typing import Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


class EventSigner:
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

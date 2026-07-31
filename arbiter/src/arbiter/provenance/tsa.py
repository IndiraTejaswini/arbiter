"""
Simplified RFC 3161 Time-Stamp Protocol stand-in.

Real RFC 3161 wraps a TimeStampToken in a CMS/PKCS#7 SignedData structure
issued by a CA-chained TSA certificate; reproducing the ASN.1 DER encoding
buys nothing for this system's actual requirement, which is the underlying
cryptographic property: *a trusted third party attests, with a signature it
cannot repudiate, that H(data) existed at time t.* This module implements
exactly that property with Ed25519 over a canonical byte encoding, and is
built so the TSA is a distinct signing identity from the log operator (§A1)
-- a compromised log-signing key does not also forge timestamps.
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


@dataclass(frozen=True)
class TimeStampToken:
    message_imprint: bytes  # SHA-256 of the timestamped payload
    gen_time_unix_ns: int
    tsa_key_id: bytes
    signature: bytes

    def to_dict(self) -> dict:
        return {
            "message_imprint": self.message_imprint.hex(),
            "gen_time_unix_ns": self.gen_time_unix_ns,
            "tsa_key_id": self.tsa_key_id.hex(),
            "signature": self.signature.hex(),
        }

    # Canonical fixed-width serialisation for `merkle_batch.tsa_token`.
    # `message_imprint` is deliberately NOT stored: it is always the batch's
    # own root_hash, which the row already carries, so persisting it would
    # be a second copy that could disagree with the first.
    def to_bytes(self) -> bytes:
        return self.tsa_key_id + struct.pack(">Q", self.gen_time_unix_ns) + self.signature

    @classmethod
    def from_bytes(cls, message_imprint: bytes, blob: bytes) -> "TimeStampToken":
        if len(blob) < 16:
            raise ValueError("tsa token blob too short")
        return cls(
            message_imprint=message_imprint,
            tsa_key_id=blob[:8],
            gen_time_unix_ns=struct.unpack(">Q", blob[8:16])[0],
            signature=blob[16:],
        )


def _signed_bytes(message_imprint: bytes, gen_time_unix_ns: int) -> bytes:
    return message_imprint + struct.pack(">Q", gen_time_unix_ns)


def _load_seeded_key(seed_hex: str | None) -> Ed25519PrivateKey:
    if seed_hex:
        return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(seed_hex))
    return Ed25519PrivateKey.generate()


class TimeStampAuthority:
    """A minimal TSA: signs H(data) || time with its own Ed25519 identity.

    Key persistence matters here for the same reason it does for
    `arbiter.audit.sign.EventSigner`: an ephemeral key means every
    timestamp token this process wrote becomes unverifiable the moment it
    restarts, which silently voids the non-backdating proof that is the
    entire point of ADEC. Seeded from `ARBITER_TSA_KEY_SEED` when set.
    """

    def __init__(self, private_key: Ed25519PrivateKey | None = None):
        if private_key is None:
            from arbiter.config import get_settings

            private_key = _load_seeded_key(get_settings().tsa_key_seed)
        self._key = private_key
        pub = self._key.public_key().public_bytes_raw()
        self.key_id = pub[:8]
        self._public_key = self._key.public_key()

    def public_key(self) -> Ed25519PublicKey:
        return self._public_key

    def timestamp(self, message_imprint: bytes, at: float | None = None) -> TimeStampToken:
        gen_time_ns = int((at if at is not None else time.time()) * 1e9)
        payload = _signed_bytes(message_imprint, gen_time_ns)
        signature = self._key.sign(payload)
        return TimeStampToken(
            message_imprint=message_imprint,
            gen_time_unix_ns=gen_time_ns,
            tsa_key_id=self.key_id,
            signature=signature,
        )

    def verify(self, token: TimeStampToken) -> bool:
        payload = _signed_bytes(token.message_imprint, token.gen_time_unix_ns)
        try:
            self._public_key.verify(token.signature, payload)
            return True
        except InvalidSignature:
            return False

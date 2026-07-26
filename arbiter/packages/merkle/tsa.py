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

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.exceptions import InvalidSignature


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


def _signed_bytes(message_imprint: bytes, gen_time_unix_ns: int) -> bytes:
    return message_imprint + struct.pack(">Q", gen_time_unix_ns)


class TimeStampAuthority:
    """A minimal TSA: signs H(data) || time with its own Ed25519 identity."""

    def __init__(self, private_key: Ed25519PrivateKey | None = None):
        self._key = private_key or Ed25519PrivateKey.generate()
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

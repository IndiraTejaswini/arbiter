"""
Unit coverage for arbiter.audit.sign.KeyRing: the fix for EventSigner
defaulting to a fresh Ed25519 key on every construction, which silently
invalidated the entire audit trail on every process restart. Rotation must
be additive (old epochs stay verifiable), never destructive.
"""

from __future__ import annotations

import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from arbiter.audit.sign import EventSigner, KeyRing


def test_event_signer_explicit_key_signs_and_verifies():
    key = Ed25519PrivateKey.generate()
    signer = EventSigner(private_key=key, epoch=3)
    payload = b"some event hash"
    sig = signer.sign(payload)

    assert signer.verify(payload, sig) is True
    assert signer.verify(b"different payload", sig) is False
    assert signer.epoch == 3


def test_keyring_verifies_signature_under_the_epoch_it_was_made_with():
    epoch0_key = Ed25519PrivateKey.generate()
    epoch1_key = Ed25519PrivateKey.generate()
    ring = KeyRing(keys={0: epoch0_key, 1: epoch1_key})

    sig_epoch0 = epoch0_key.sign(b"payload")
    sig_epoch1 = epoch1_key.sign(b"payload")

    assert ring.verify(0, b"payload", sig_epoch0) is True
    assert ring.verify(1, b"payload", sig_epoch1) is True


def test_keyring_rotation_never_invalidates_the_old_epoch():
    """The property this whole module exists for: after 'rotating' (adding
    a new epoch), a signature made under the OLD epoch must still verify --
    rotation is additive, not destructive."""
    epoch0_key = Ed25519PrivateKey.generate()
    ring_before_rotation = KeyRing(keys={0: epoch0_key})
    old_signature = epoch0_key.sign(b"decision payload")
    assert ring_before_rotation.verify(0, b"decision payload", old_signature) is True

    epoch1_key = Ed25519PrivateKey.generate()
    ring_after_rotation = KeyRing(keys={0: epoch0_key, 1: epoch1_key})
    assert ring_after_rotation.verify(0, b"decision payload", old_signature) is True


def test_keyring_rejects_unknown_epoch_and_wrong_key():
    epoch0_key = Ed25519PrivateKey.generate()
    other_key = Ed25519PrivateKey.generate()
    ring = KeyRing(keys={0: epoch0_key})

    sig_from_other_key = other_key.sign(b"payload")
    assert ring.verify(0, b"payload", sig_from_other_key) is False  # wrong key, same epoch
    assert ring.verify(99, b"payload", epoch0_key.sign(b"payload")) is False  # unknown epoch

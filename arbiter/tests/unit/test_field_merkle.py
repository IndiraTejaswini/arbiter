"""
Unit coverage for arbiter.provenance.field_merkle: selective disclosure of
one field from a committed multi-field record, with proof of membership
and without exposing any other field.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from arbiter.provenance.field_merkle import commit_record, reveal_field, verify_field_reveal

_RECORD = {
    "order_id": "ORD-4471",
    "delivery_date": "2026-06-01",
    "shipping_address": "742 Evergreen Terrace, Springfield",
    "recipient_name": "Homer Simpson",
    "carrier": "UPS",
}


def test_revealed_field_verifies_against_root():
    commitment = commit_record(_RECORD)
    reveal = reveal_field(commitment, _RECORD, "delivery_date")

    assert verify_field_reveal(commitment.root, reveal) is True
    assert reveal.value == "2026-06-01"


def test_reveal_does_not_expose_other_field_values():
    """The actual privacy property: a FieldReveal for one field carries
    nothing about any other field."""
    commitment = commit_record(_RECORD)
    reveal = reveal_field(commitment, _RECORD, "delivery_date")
    payload = reveal.to_dict()

    assert "shipping_address" not in str(payload)
    assert "recipient_name" not in str(payload)
    assert "Homer Simpson" not in str(payload)


def test_tampered_value_fails_verification():
    commitment = commit_record(_RECORD)
    reveal = reveal_field(commitment, _RECORD, "delivery_date")

    tampered = type(reveal)(
        field_name=reveal.field_name, value="2099-01-01", salt=reveal.salt,
        leaf_index=reveal.leaf_index, audit_path=reveal.audit_path, tree_size=reveal.tree_size,
    )
    assert verify_field_reveal(commitment.root, tampered) is False


def test_reveal_against_wrong_root_fails():
    commitment_a = commit_record(_RECORD)
    commitment_b = commit_record({**_RECORD, "carrier": "FedEx"})
    reveal = reveal_field(commitment_a, _RECORD, "delivery_date")

    assert verify_field_reveal(commitment_b.root, reveal) is False


def test_every_field_independently_revealable():
    commitment = commit_record(_RECORD)
    for field_name, value in _RECORD.items():
        reveal = reveal_field(commitment, _RECORD, field_name)
        assert verify_field_reveal(commitment.root, reveal) is True
        assert reveal.value == value


def test_unknown_field_raises():
    commitment = commit_record(_RECORD)
    try:
        reveal_field(commitment, _RECORD, "not_a_real_field")
        raise AssertionError("expected KeyError")
    except KeyError:
        pass


def test_salts_are_unique_per_field_and_per_commitment():
    """Distinct salts defend low-entropy fields (an address, a name)
    against dictionary attack against the published root -- reusing a
    salt across fields or across commitments would undermine that."""
    commitment_a = commit_record(_RECORD)
    commitment_b = commit_record(_RECORD)

    assert len(set(commitment_a.salts.values())) == len(commitment_a.salts)
    for field_name in _RECORD:
        assert commitment_a.salts[field_name] != commitment_b.salts[field_name]

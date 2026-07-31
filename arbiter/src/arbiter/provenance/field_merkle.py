"""
Selective disclosure via field-level Merkle commitment -- the same RFC 6962
primitives `arbiter.provenance.rfc6962` already use for the cross-case
transparency log (leaf/node hashing, inclusion proofs), applied here at the
granularity of a single record's FIELDS rather than a whole artifact or
batch.

The privacy problem this solves: a merchant's shipment record has many
fields (order_id, delivery_date, shipping_address, carrier, tracking_id,
recipient_name...). Satisfying a single predicate like
`delivery_address_matches_avs` today means either uploading the WHOLE
record (over-disclosure -- Amex and the card member see the recipient's
name and every other order detail to answer one yes/no question) or
trusting an unverifiable merchant claim about just that one field. Field-
level Merkle commitment lets the merchant commit to the entire record
once, then reveal only the one or two fields a given predicate actually
needs, with a cryptographic proof that the revealed field is genuinely
part of the record that was committed -- data minimization by
construction, not by policy.

Each field's leaf includes a per-field random salt
(`leaf_hash(field_name || value || salt)`), not just the raw field
name/value -- a low-entropy field (a US shipping address has far less
than 256 bits of entropy) would otherwise be recoverable from the
committed root alone by dictionary attack, defeating the entire point of
not disclosing it.

Scope, stated honestly: this is disclosure of a chosen SUBSET of a
committed record's fields, with proof of membership in the whole -- NOT a
zero-knowledge equality proof that would let two parties confirm two
addresses match without either one ever seeing plaintext. That is a
materially different (and harder) primitive this module does not
attempt; conflating the two would overstate what is actually implemented
here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Tuple

from .rfc6962 import inclusion_proof, leaf_hash, merkle_tree_hash, verify_inclusion


@dataclass(frozen=True)
class FieldCommitment:
    root: bytes
    field_names: Tuple[str, ...]  # sorted -- defines leaf order, deterministically
    salts: Dict[str, bytes]  # field_name -> salt. Held by the committer ONLY; never sent anywhere.

    def leaf_index(self, field_name: str) -> int:
        return self.field_names.index(field_name)


def _field_leaf(field_name: str, value: str, salt: bytes) -> bytes:
    payload = field_name.encode("utf-8") + b"\x00" + value.encode("utf-8") + b"\x00" + salt
    return leaf_hash(payload)


def commit_record(fields: Dict[str, str]) -> FieldCommitment:
    """Commits to every field in `fields` under a fresh per-field salt.
    The caller keeps the returned FieldCommitment (specifically its
    `salts`) privately; only `root` is ever published/sent to Amex, and
    only individual `FieldReveal`s are sent later, one per disclosed
    field."""
    field_names = tuple(sorted(fields))
    salts = {name: os.urandom(16) for name in field_names}
    leaves = [_field_leaf(name, fields[name], salts[name]) for name in field_names]
    root = merkle_tree_hash(leaves)
    return FieldCommitment(root=root, field_names=field_names, salts=salts)


@dataclass(frozen=True)
class FieldReveal:
    field_name: str
    value: str
    salt: bytes
    leaf_index: int
    audit_path: Tuple[bytes, ...]
    tree_size: int

    def to_dict(self) -> dict:
        return {
            "field_name": self.field_name, "value": self.value, "salt": self.salt.hex(),
            "leaf_index": self.leaf_index, "audit_path": [h.hex() for h in self.audit_path],
            "tree_size": self.tree_size,
        }


def reveal_field(commitment: FieldCommitment, fields: Dict[str, str], field_name: str) -> FieldReveal:
    """Called by the committer -- the party holding `fields` and the
    commitment's salts -- to produce a disclosure package for exactly one
    field. Every other field's value and salt stay with the committer;
    nothing about them appears in the returned FieldReveal."""
    if field_name not in commitment.salts:
        raise KeyError(f"{field_name!r} was not part of the committed record")
    leaves = [_field_leaf(n, fields[n], commitment.salts[n]) for n in commitment.field_names]
    idx = commitment.leaf_index(field_name)
    proof = inclusion_proof(leaves, idx)
    return FieldReveal(
        field_name=field_name, value=fields[field_name], salt=commitment.salts[field_name],
        leaf_index=idx, audit_path=tuple(proof), tree_size=len(leaves),
    )


def verify_field_reveal(root: bytes, reveal: FieldReveal) -> bool:
    """The verifier's side (Amex/the referee's derivation layer): recomputes
    the leaf from the disclosed (field_name, value, salt) and checks it
    against `root` via the audit path -- exactly like verifying inclusion
    in the cross-case transparency log, just scoped to one record's fields
    instead of one batch's artifacts. Never needs, and is never given, any
    other field's value."""
    leaf = _field_leaf(reveal.field_name, reveal.value, reveal.salt)
    return verify_inclusion(leaf, reveal.leaf_index, reveal.tree_size, list(reveal.audit_path), root)

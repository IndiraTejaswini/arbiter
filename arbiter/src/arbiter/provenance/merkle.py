"""
Append-only transparency log for ADEC evidence commitments (A1).

This is the CT-style construction, not a blockchain: a single accountable
operator (Amex) signs tree heads over an append-only Merkle tree; anyone
holding a prior signed tree head can demand a consistency proof that the
log only ever grew, never rewrote history. Combined with RFC-3161-style
timestamping of each root, this proves an evidence artifact's commitment
hash existed before a given wall-clock instant -- the load-bearing property
CE3.0 gets for free from network-recorded transactions, generalised here to
arbitrary artifacts.

CLAUDE.md invariant #11: leaf/node hashes use 0x00/0x01 domain separation
(see rfc6962.LEAF_PREFIX / NODE_PREFIX) -- without it the tree admits
second-preimage attacks.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Dict, List, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from . import rfc6962
from .tsa import TimeStampAuthority, TimeStampToken


def _sth_signing_bytes(tree_size: int, root_hash: bytes, timestamp_unix_ns: int) -> bytes:
    return struct.pack(">QQ", tree_size, timestamp_unix_ns) + root_hash


@dataclass(frozen=True)
class SignedTreeHead:
    tree_size: int
    root_hash: bytes
    timestamp_unix_ns: int
    log_id: bytes
    signature: bytes
    tsa_token: TimeStampToken

    def to_dict(self) -> dict:
        return {
            "tree_size": self.tree_size,
            "root_hash": self.root_hash.hex(),
            "timestamp_unix_ns": self.timestamp_unix_ns,
            "log_id": self.log_id.hex(),
            "signature": self.signature.hex(),
            "tsa_token": self.tsa_token.to_dict(),
        }


@dataclass(frozen=True)
class InclusionProof:
    leaf_index: int
    tree_size: int
    audit_path: List[bytes]
    sth: SignedTreeHead

    def to_dict(self) -> dict:
        return {
            "leaf_index": self.leaf_index,
            "tree_size": self.tree_size,
            "audit_path": [h.hex() for h in self.audit_path],
            "sth": self.sth.to_dict(),
        }


@dataclass(frozen=True)
class ConsistencyProof:
    size1: int
    size2: int
    proof: List[bytes]
    sth1: SignedTreeHead
    sth2: SignedTreeHead


class LogOperator:
    """The signing identity of the log operator (Amex). Distinct from the TSA
    identity by design: a compromised log-signing key cannot forge timestamps,
    and vice versa -- two keys an attacker must both compromise."""

    def __init__(self, private_key: Optional[Ed25519PrivateKey] = None):
        self._key = private_key or Ed25519PrivateKey.generate()
        self._public_key = self._key.public_key()
        self.log_id = self._public_key.public_bytes_raw()[:8]

    def public_key(self) -> Ed25519PublicKey:
        return self._public_key

    def sign_sth(self, tree_size: int, root_hash: bytes, timestamp_unix_ns: int) -> bytes:
        return self._key.sign(_sth_signing_bytes(tree_size, root_hash, timestamp_unix_ns))

    def verify_sth(self, sth: SignedTreeHead) -> bool:
        payload = _sth_signing_bytes(sth.tree_size, sth.root_hash, sth.timestamp_unix_ns)
        try:
            self._public_key.verify(sth.signature, payload)
        except InvalidSignature:
            return False
        return sth.log_id == self.log_id


class TransparencyLog:
    """
    Stateful append-only log. Leaves accumulate as `append()` is called;
    `seal_batch()` closes the current pending leaves into a new Merkle tree
    and produces a signed, timestamped tree head. The spec batches on a
    10-second wall-clock timer (A1); here sealing is an explicit operation
    so callers (or a scheduler) control cadence deterministically.
    """

    def __init__(self, operator: Optional[LogOperator] = None, tsa: Optional[TimeStampAuthority] = None):
        self.operator = operator or LogOperator()
        self.tsa = tsa or TimeStampAuthority()
        self._leaf_hashes: List[bytes] = []
        self._leaf_data: List[bytes] = []
        self._sealed_size: int = 0
        self._sth_history: List[SignedTreeHead] = []

    # -- write path -----------------------------------------------------

    def append(self, data: bytes) -> int:
        """Queue a new commitment leaf. Returns its (eventual) leaf index."""
        idx = len(self._leaf_hashes)
        self._leaf_hashes.append(rfc6962.leaf_hash(data))
        self._leaf_data.append(data)
        return idx

    def pending_count(self) -> int:
        return len(self._leaf_hashes) - self._sealed_size

    def seal_batch(self, at: Optional[float] = None) -> Optional[SignedTreeHead]:
        """Seal all pending leaves into a new tree head. No-op (returns the
        latest STH) if nothing is pending -- sealing an unchanged tree would
        not be a meaningful new commitment."""
        if self.pending_count() == 0:
            return self.latest_sth()

        tree_size = len(self._leaf_hashes)
        root = rfc6962.merkle_tree_hash(self._leaf_hashes)
        tsa_token = self.tsa.timestamp(root, at=at)
        signature = self.operator.sign_sth(tree_size, root, tsa_token.gen_time_unix_ns)
        sth = SignedTreeHead(
            tree_size=tree_size,
            root_hash=root,
            timestamp_unix_ns=tsa_token.gen_time_unix_ns,
            log_id=self.operator.log_id,
            signature=signature,
            tsa_token=tsa_token,
        )
        self._sth_history.append(sth)
        self._sealed_size = tree_size
        return sth

    # -- read path --------------------------------------------------------

    def latest_sth(self) -> Optional[SignedTreeHead]:
        return self._sth_history[-1] if self._sth_history else None

    def sth_at_size(self, tree_size: int) -> Optional[SignedTreeHead]:
        for sth in self._sth_history:
            if sth.tree_size == tree_size:
                return sth
        return None

    def inclusion_proof(self, leaf_index: int) -> InclusionProof:
        sth = self.latest_sth()
        if sth is None or leaf_index >= sth.tree_size:
            raise ValueError(f"leaf {leaf_index} is not covered by any sealed tree head")
        path = rfc6962.inclusion_proof(self._leaf_hashes[: sth.tree_size], leaf_index)
        return InclusionProof(leaf_index=leaf_index, tree_size=sth.tree_size, audit_path=path, sth=sth)

    def consistency_proof(self, size1: int, size2: Optional[int] = None) -> ConsistencyProof:
        sth2 = self.sth_at_size(size2) if size2 is not None else self.latest_sth()
        sth1 = self.sth_at_size(size1)
        if sth1 is None or sth2 is None:
            raise ValueError("both sizes must correspond to a previously sealed tree head")
        proof = rfc6962.consistency_proof(self._leaf_hashes[: sth2.tree_size], size1, sth2.tree_size)
        return ConsistencyProof(size1=size1, size2=sth2.tree_size, proof=proof, sth1=sth1, sth2=sth2)

    # -- verification (what a merchant / auditor / referee actually calls) --

    def verify_commitment(
        self,
        data: bytes,
        leaf_index: int,
        deadline_unix_ns: Optional[int] = None,
    ) -> "CommitmentVerification":
        """
        The ADEC core check: recompute the leaf hash from revealed data,
        verify its inclusion in a signed, TSA-stamped root, verify the
        operator's and TSA's signatures independently, and -- if a deadline
        is given (e.g. the dispute filing time) -- verify the root's
        timestamp strictly precedes it. This is what makes non-backdating
        provable rather than assumed.
        """
        proof = self.inclusion_proof(leaf_index)
        leaf = rfc6962.leaf_hash(data)
        inclusion_ok = rfc6962.verify_inclusion(
            leaf, leaf_index, proof.tree_size, proof.audit_path, proof.sth.root_hash
        )
        sth_sig_ok = self.operator.verify_sth(proof.sth)
        tsa_ok = self.tsa.verify(proof.sth.tsa_token) and proof.sth.tsa_token.message_imprint == proof.sth.root_hash
        predates_deadline = (
            proof.sth.timestamp_unix_ns < deadline_unix_ns if deadline_unix_ns is not None else None
        )
        return CommitmentVerification(
            leaf_index=leaf_index,
            inclusion_valid=inclusion_ok,
            sth_signature_valid=sth_sig_ok,
            tsa_signature_valid=tsa_ok,
            committed_at_unix_ns=proof.sth.timestamp_unix_ns,
            predates_deadline=predates_deadline,
            proof=proof,
        )


@dataclass(frozen=True)
class CommitmentVerification:
    leaf_index: int
    inclusion_valid: bool
    sth_signature_valid: bool
    tsa_signature_valid: bool
    committed_at_unix_ns: int
    predates_deadline: Optional[bool]
    proof: InclusionProof

    @property
    def ok(self) -> bool:
        base = self.inclusion_valid and self.sth_signature_valid and self.tsa_signature_valid
        if self.predates_deadline is None:
            return base
        return base and self.predates_deadline


class Auditor:
    """
    A third party (independent of the log operator) that gossips signed
    tree heads and cross-checks consistency over time. This is the control
    that makes split-view attacks detectable: if the operator ever shows two
    parties diverging histories, an auditor comparing STHs gossiped from
    both will find a tree size where the roots disagree, or where a
    claimed-later STH fails a consistency proof against an earlier one it
    already holds -- either is conclusive misbehaviour.
    """

    def __init__(self, log: TransparencyLog):
        self._log = log  # in production this would be a separate operator entirely
        self._known: Dict[int, SignedTreeHead] = {}

    def observe(self, sth: SignedTreeHead) -> "AuditResult":
        if not self._log.operator.verify_sth(sth):
            return AuditResult(ok=False, reason="STH signature invalid")

        prior = self._known.get(sth.tree_size)
        if prior is not None and prior.root_hash != sth.root_hash:
            return AuditResult(ok=False, reason=f"SPLIT-VIEW: two roots observed at tree_size={sth.tree_size}")

        for size1, sth1 in sorted(self._known.items()):
            if size1 >= sth.tree_size:
                continue
            try:
                cp = self._log.consistency_proof(size1, sth.tree_size)
            except ValueError:
                continue
            if not rfc6962.verify_consistency(
                cp.size1, cp.size2, sth1.root_hash, sth.root_hash, cp.proof
            ):
                return AuditResult(
                    ok=False,
                    reason=f"consistency proof failed between size={size1} and size={sth.tree_size}",
                )

        self._known[sth.tree_size] = sth
        return AuditResult(ok=True, reason="consistent")


@dataclass(frozen=True)
class AuditResult:
    ok: bool
    reason: str

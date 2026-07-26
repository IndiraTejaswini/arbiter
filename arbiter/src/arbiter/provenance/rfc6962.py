"""
RFC 6962 Merkle tree hashing, inclusion proofs, and consistency proofs --
the same construction Certificate Transparency uses, applied here to
evidence commitments instead of certificates (§A1, §6.5).

This module is pure functions over a list of leaf hashes. No I/O, no state.
"""

from __future__ import annotations

import hashlib
from typing import List, Sequence, Tuple

LEAF_PREFIX = b"\x00"
NODE_PREFIX = b"\x01"


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def leaf_hash(data: bytes) -> bytes:
    return sha256(LEAF_PREFIX + data)


def node_hash(left: bytes, right: bytes) -> bytes:
    return sha256(NODE_PREFIX + left + right)


def _largest_power_of_two_less_than(n: int) -> int:
    k = 1
    while k * 2 < n:
        k *= 2
    return k


def merkle_tree_hash(leaves: Sequence[bytes]) -> bytes:
    """MTH(D[n]) per RFC 6962 §2.1. `leaves` are already leaf-hashed."""
    n = len(leaves)
    if n == 0:
        return sha256(b"")
    if n == 1:
        return leaves[0]
    k = _largest_power_of_two_less_than(n)
    left = merkle_tree_hash(leaves[:k])
    right = merkle_tree_hash(leaves[k:])
    return node_hash(left, right)


def inclusion_proof(leaves: Sequence[bytes], leaf_index: int) -> List[bytes]:
    """
    PATH(m, D[n]) per RFC 6962 §2.1.1: the audit path from leaf `leaf_index`
    to the root over the leaf-hashed list `leaves`.
    """
    n = len(leaves)
    if not (0 <= leaf_index < n):
        raise IndexError(f"leaf_index {leaf_index} out of range for n={n}")

    def path(m: int, d: Sequence[bytes]) -> List[bytes]:
        n_ = len(d)
        if n_ <= 1:
            return []
        k = _largest_power_of_two_less_than(n_)
        if m < k:
            return path(m, d[:k]) + [merkle_tree_hash(d[k:])]
        else:
            return path(m - k, d[k:]) + [merkle_tree_hash(d[:k])]

    return path(leaf_index, leaves)


def verify_inclusion(
    leaf: bytes,
    leaf_index: int,
    tree_size: int,
    audit_path: Sequence[bytes],
    root: bytes,
) -> bool:
    """Verify an inclusion proof without access to the full leaf list."""

    def compute_root(m: int, n: int, path: Sequence[bytes], leaf_h: bytes) -> bytes:
        if n == 1:
            return leaf_h
        k = _largest_power_of_two_less_than(n)
        if not path:
            raise ValueError("audit path exhausted before reaching root")
        if m < k:
            sub = compute_root(m, k, path[:-1], leaf_h)
            return node_hash(sub, path[-1])
        else:
            sub = compute_root(m - k, n - k, path[:-1], leaf_h)
            return node_hash(path[-1], sub)

    try:
        computed = compute_root(leaf_index, tree_size, list(audit_path), leaf)
    except (ValueError, IndexError):
        return False
    return computed == root


def consistency_proof(
    leaves: Sequence[bytes], size1: int, size2: int
) -> List[bytes]:
    """PROOF(m, D[n]) per RFC 6962 §2.1.2: consistency between tree sizes size1 <= size2."""
    if not (0 <= size1 <= size2 <= len(leaves)):
        raise ValueError(f"invalid sizes: size1={size1} size2={size2} len={len(leaves)}")
    if size1 == size2:
        return []
    if size1 == 0:
        return []

    def subproof(m: int, d: Sequence[bytes], b: bool) -> List[bytes]:
        n_ = len(d)
        if m == n_:
            return [] if b else [merkle_tree_hash(d)]
        k = _largest_power_of_two_less_than(n_)
        if m <= k:
            return subproof(m, d[:k], b) + [merkle_tree_hash(d[k:])]
        else:
            return subproof(m - k, d[k:], False) + [merkle_tree_hash(d[:k])]

    return subproof(size1, leaves[:size2], True)


def verify_consistency(
    size1: int,
    size2: int,
    root1: bytes,
    root2: bytes,
    proof: Sequence[bytes],
) -> bool:
    """
    Verify a consistency proof using only (size1, size2, root1, root2, proof)
    -- no access to the underlying leaves. This is the operation an external
    auditor or a gossiping merchant performs (§6.5): it proves the log at
    size2 is a strict append-only extension of the log the auditor already
    observed at size1, without trusting the log operator.
    """
    if size1 == size2:
        return size1 == 0 or (root1 == root2 and len(proof) == 0)
    if size1 == 0:
        return True  # the empty tree is trivially a prefix of anything
    if not (0 < size1 < size2):
        return False

    path = list(proof)

    def rec(m: int, n: int, b: bool) -> Tuple[bytes, bytes]:
        """Returns (old_hash, new_hash) for the subtree of size n, consuming
        proof entries in the same order `consistency_proof` produced them.
        While `b` is still true we are on the initial all-left spine, where
        this subtree's leaf range [0, n) coincides exactly with the old
        tree's [0, m); its hash is not transmitted because the verifier
        already knows it -- it is exactly root1 -- for both old and new."""
        if m == n:
            if b:
                return root1, root1
            h = path.pop(0)
            return h, h
        k = _largest_power_of_two_less_than(n)
        if m <= k:
            old_l, new_l = rec(m, k, b)
            new_r = path.pop(0)
            return old_l, node_hash(new_l, new_r)
        else:
            old_r, new_r = rec(m - k, n - k, False)
            new_l = path.pop(0)
            old_h = node_hash(new_l, old_r)
            return old_h, node_hash(new_l, new_r)

    try:
        old_root, new_root = rec(size1, size2, True)
    except IndexError:
        return False
    if path:  # proof had extra, unconsumed entries
        return False
    return old_root == root1 and new_root == root2

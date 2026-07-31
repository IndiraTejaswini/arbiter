"""
ADEC commit / reveal / proof routes (A1).

Authorization, stated as the gap it closes: every route in this module used
to be completely unauthenticated, and `POST /v1/commitments` took
`merchant_id` straight from the request body. Any anonymous caller could
therefore create commitments attributed to any merchant -- which voids the
one property the whole ante-dispute commitment scheme exists to provide.
ADEC's value is "this artifact provably existed before the dispute did,
and it provably belongs to this merchant"; an open commit endpoint with a
caller-supplied merchant_id gives an attacker both halves of that sentence.

Now: every route requires an authenticated `Actor`, and a MERCHANT actor
may only ever commit, reveal, or read proofs for its OWN merchant_id --
`merchant_id` is taken from the verified token, never from the body.
Reviewers/admins retain cross-merchant access because ADEC verification is
part of adjudication, which they run.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from arbiter.api.deps import get_provenance_service
from arbiter.auth import Actor, Role
from arbiter.auth.deps import get_current_actor

router = APIRouter(prefix="/v1", tags=["commitments"])


def _resolve_merchant_id(actor: Actor, claimed: uuid.UUID | None) -> str:
    """A merchant commits only for itself; a reviewer/admin must say which
    merchant it is acting for. A card member has no business here at all --
    ADEC is a merchant-side integration."""
    if actor.role == Role.MERCHANT:
        if actor.bound_id is None:
            raise HTTPException(403, "merchant actor is not bound to a merchant_id")
        if claimed is not None and str(claimed) != actor.bound_id:
            raise HTTPException(
                403, "a merchant may only create commitments for its own merchant_id"
            )
        return actor.bound_id
    if actor.is_privileged:
        if claimed is None:
            raise HTTPException(400, "reviewer/admin must supply merchant_id explicitly")
        return str(claimed)
    raise HTTPException(403, "only a merchant (or a reviewer/admin) may create ADEC commitments")


def _require_commitment_access(actor: Actor, commitment) -> None:
    """A merchant may only reveal/inspect its own commitments."""
    if actor.is_privileged:
        return
    if actor.role == Role.MERCHANT and actor.bound_id == commitment.merchant_id:
        return
    raise HTTPException(403, "not authorized for this commitment")


class CommitRequest(BaseModel):
    # Optional and advisory only: for a MERCHANT actor the binding
    # merchant_id comes from the verified token, and a mismatch here is a
    # 403 rather than a silent override. Kept on the schema so a
    # reviewer/admin can act on a merchant's behalf explicitly.
    merchant_id: uuid.UUID | None = None
    artifact_type: str
    commitment_hash: str  # hex-encoded sha256(artifact || salt)
    event_time: datetime


class CommitResponse(BaseModel):
    commitment_id: str
    merchant_id: str
    leaf_index: int
    committed_at: datetime


@router.post("/commitments", response_model=CommitResponse, status_code=201)
def create_commitment(
    body: CommitRequest,
    svc=Depends(get_provenance_service),
    actor: Actor = Depends(get_current_actor),
):
    """Merchant SDK call, at event time -- see sdk/arbiter_commit.py.
    Nothing but the hash crosses this boundary (A1: zero privacy cost)."""
    merchant_id = _resolve_merchant_id(actor, body.merchant_id)

    try:
        commitment_hash = bytes.fromhex(body.commitment_hash)
    except ValueError:
        raise HTTPException(400, "commitment_hash must be hex-encoded")
    # A commitment hash is sha256 by construction (compute_commitment_hash);
    # anything else is a malformed client, and accepting it would put
    # unverifiable leaves in an append-only log.
    if len(commitment_hash) != 32:
        raise HTTPException(400, "commitment_hash must be 32 bytes (sha256)")

    commitment = svc.commit(
        merchant_id=merchant_id, artifact_type=body.artifact_type,
        commitment_hash=commitment_hash, event_time=body.event_time,
    )
    # Seal immediately so the commitment is covered by a signed tree head
    # right away. The spec's 10-second batch cadence is a throughput
    # optimisation (see ProvenanceService.seal's docstring); sealing per
    # commit is correct but produces one batch per commitment, so a
    # scheduler should drive this at real volume.
    svc.seal()
    return CommitResponse(
        commitment_id=commitment.commitment_id, merchant_id=commitment.merchant_id,
        leaf_index=commitment.leaf_index, committed_at=commitment.committed_at,
    )


class RevealRequest(BaseModel):
    artifact_hex: str
    salt_hex: str
    dispute_filed_at: datetime | None = None


@router.post("/commitments/{commitment_id}/reveal")
def reveal_commitment(
    commitment_id: str,
    body: RevealRequest,
    svc=Depends(get_provenance_service),
    actor: Actor = Depends(get_current_actor),
):
    """Dispute-time reveal. Failure demotes the claim's tier to SUBMITTED
    (CLAUDE.md #9: degrade, never reject) -- callers branch on `.ok`."""
    commitment = svc.get(commitment_id)
    if commitment is None:
        raise HTTPException(404, "unknown commitment_id")
    _require_commitment_access(actor, commitment)

    try:
        artifact = bytes.fromhex(body.artifact_hex)
        salt = bytes.fromhex(body.salt_hex)
    except ValueError:
        raise HTTPException(400, "artifact_hex/salt_hex must be hex-encoded")

    try:
        result = svc.reveal_and_verify(commitment_id, artifact, salt, body.dispute_filed_at)
    except KeyError:
        raise HTTPException(404, "unknown commitment_id")

    return {
        "ok": result.ok,
        "inclusion_valid": result.inclusion_valid,
        "sth_signature_valid": result.sth_signature_valid,
        "tsa_signature_valid": result.tsa_signature_valid,
        "predates_deadline": result.predates_deadline,
        "committed_at_unix_ns": result.committed_at_unix_ns,
        "tier": "COMMITTED" if result.ok else "SUBMITTED",
    }


@router.get("/commitments/{commitment_id}/proof")
def get_inclusion_proof(
    commitment_id: str,
    svc=Depends(get_provenance_service),
    actor: Actor = Depends(get_current_actor),
):
    commitment = svc.get(commitment_id)
    if commitment is None:
        raise HTTPException(404, "unknown commitment_id")
    _require_commitment_access(actor, commitment)
    proof = svc.log.inclusion_proof(commitment.leaf_index)
    return proof.to_dict()


# -- Public log surface -------------------------------------------------
# Signed tree heads and consistency proofs are DELIBERATELY readable by any
# authenticated caller: that is the entire point of a Certificate-
# Transparency-style log. Split-view detection (merkle.Auditor) depends on
# independent parties being able to gossip STHs and demand consistency
# proofs between them. Restricting these to privileged callers would make
# the log self-attested, which is the property ADEC exists to avoid. They
# expose only tree sizes, root hashes, and signatures -- never a commitment
# hash, never a merchant identity, never an artifact.


@router.get("/log/sth")
def get_latest_sth(svc=Depends(get_provenance_service), actor: Actor = Depends(get_current_actor)):
    sth = svc.log.latest_sth()
    if sth is None:
        raise HTTPException(404, "log has no sealed tree head yet")
    return sth.to_dict()


@router.get("/log/consistency")
def get_consistency_proof(
    from_size: int,
    to_size: int | None = None,
    svc=Depends(get_provenance_service),
    actor: Actor = Depends(get_current_actor),
):
    try:
        proof = svc.log.consistency_proof(from_size, to_size)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "size1": proof.size1, "size2": proof.size2,
        "proof": [h.hex() for h in proof.proof],
    }

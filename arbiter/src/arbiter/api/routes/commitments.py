from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from arbiter.api.deps import get_provenance_service

router = APIRouter(prefix="/v1", tags=["commitments"])


class CommitRequest(BaseModel):
    merchant_id: uuid.UUID
    artifact_type: str
    commitment_hash: str  # hex-encoded sha256(artifact || salt)
    event_time: datetime


class CommitResponse(BaseModel):
    commitment_id: str
    leaf_index: int
    committed_at: datetime


@router.post("/commitments", response_model=CommitResponse, status_code=201)
def create_commitment(body: CommitRequest, svc=Depends(get_provenance_service)):
    """Merchant SDK call, at event time -- see sdk/arbiter_commit.py.
    Nothing but the hash crosses this boundary (A1: zero privacy cost)."""
    try:
        commitment_hash = bytes.fromhex(body.commitment_hash)
    except ValueError:
        raise HTTPException(400, "commitment_hash must be hex-encoded")

    commitment = svc.commit(
        merchant_id=str(body.merchant_id), artifact_type=body.artifact_type,
        commitment_hash=commitment_hash, event_time=body.event_time,
    )
    svc.seal()
    return CommitResponse(commitment_id=commitment.commitment_id, leaf_index=commitment.leaf_index,
                           committed_at=commitment.committed_at)


class RevealRequest(BaseModel):
    artifact_hex: str
    salt_hex: str
    dispute_filed_at: datetime | None = None


@router.post("/commitments/{commitment_id}/reveal")
def reveal_commitment(commitment_id: str, body: RevealRequest, svc=Depends(get_provenance_service)):
    """Dispute-time reveal. Failure demotes the claim's tier to SUBMITTED
    (CLAUDE.md #9: degrade, never reject) -- callers branch on `.ok`."""
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
def get_inclusion_proof(commitment_id: str, svc=Depends(get_provenance_service)):
    commitment = svc.get(commitment_id)
    if commitment is None:
        raise HTTPException(404, "unknown commitment_id")
    proof = svc.log.inclusion_proof(commitment.leaf_index)
    return proof.to_dict()


@router.get("/log/sth")
def get_latest_sth(svc=Depends(get_provenance_service)):
    sth = svc.log.latest_sth()
    if sth is None:
        raise HTTPException(404, "log has no sealed tree head yet")
    return sth.to_dict()


@router.get("/log/consistency")
def get_consistency_proof(from_size: int, to_size: int | None = None, svc=Depends(get_provenance_service)):
    try:
        proof = svc.log.consistency_proof(from_size, to_size)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "size1": proof.size1, "size2": proof.size2,
        "proof": [h.hex() for h in proof.proof],
    }

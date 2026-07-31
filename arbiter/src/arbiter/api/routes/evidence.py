from __future__ import annotations

import hashlib
import uuid
from datetime import timezone

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from arbiter.api.deps import get_artifact_store, get_privacy_vault
from arbiter.audit import case_log
from arbiter.auth import Actor, Role, filter_graph_for_party, require_case_access, require_reviewer
from arbiter.auth.deps import get_current_actor
from arbiter.config import get_settings
from arbiter.db import models as m
from arbiter.db.session import get_session
from arbiter.evidence.models import ProvenanceTier
from arbiter.ingest.route import process_artifact
from arbiter.privacy.shredding import SubjectKeyVault, decrypt_extracted_fields, encrypt_extracted_fields, erase_subject

router = APIRouter(prefix="/v1", tags=["evidence"])

# Per-case artifact ceiling (ARBITER-architecture.md 6.1's threat table
# calls for one). Without it, an authenticated party can fill storage one
# 25 MB upload at a time.
#
# Public because it is part of the request contract: `/health` serves it so
# a client can state the real ceiling instead of guessing one, and a client
# that guesses is a client that shows the user the wrong number the moment
# an operator tunes it.
MAX_ARTIFACTS_PER_CASE = 50

_ROLE_TO_PARTY = {
    Role.CARD_MEMBER: m.PartyEnum.CARD_MEMBER,
    Role.MERCHANT: m.PartyEnum.MERCHANT,
}

# Node types whose extracted_fields may carry the card member's personal
# narrative or identity material -- encrypted at rest under their own
# subject key (arbiter.privacy.shredding) so a GDPR erasure request can be
# honored by destroying a key rather than mutating an append-only row.
_PII_BEARING_NODE_TYPES = frozenset({"identity", "claim"})


async def _read_capped(file: UploadFile, max_bytes: int) -> bytes:
    """Stream the upload, aborting as soon as it exceeds the cap.

    `await file.read()` used to pull the entire body into memory before
    `arbiter.ingest.scan` checked its size -- so the 25 MB limit was
    enforced only after an arbitrarily large body had already been
    allocated, and a single multi-gigabyte POST could exhaust the worker.
    Reading in chunks and failing at the boundary makes the cap actually
    bound memory.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(413, f"artifact exceeds the {max_bytes}-byte limit")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/cases/{case_id}/evidence", status_code=201)
async def upload_evidence(
    case_id: uuid.UUID,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    actor: Actor = Depends(get_current_actor),
    vault: SubjectKeyVault = Depends(get_privacy_vault),
    artifacts=Depends(get_artifact_store),
):
    case = session.get(m.DisputeCase, case_id)
    if case is None:
        raise HTTPException(404, "case not found")
    require_case_access(actor, case)

    # A case that has already been decided must not accept new evidence
    # into the same decision silently -- the signed decision cites a fixed
    # evidence set. Re-opening is a distinct, audited transition.
    if case.state in (m.CaseStateEnum.SETTLED, m.CaseStateEnum.DEFLECTED):
        raise HTTPException(409, f"case is {case.state.value}; reopen it before submitting new evidence")

    settings = get_settings()
    per_case_artifacts = session.execute(
        select(func.count()).select_from(m.Artifact).where(m.Artifact.case_id == case_id)
    ).scalar_one()
    if per_case_artifacts >= MAX_ARTIFACTS_PER_CASE:
        raise HTTPException(429, f"per-case artifact ceiling of {MAX_ARTIFACTS_PER_CASE} reached")

    data = await _read_capped(file, settings.max_artifact_bytes)
    artifact_id = uuid.uuid4()

    # Attributed to the uploading party's actual role, never hardcoded --
    # C3 (evaluate what exists, not what was submitted) depends on knowing
    # honestly who submitted what; a reviewer/admin uploading on someone's
    # behalf is recorded as NEUTRAL, not silently attributed to a party.
    uploaded_by = _ROLE_TO_PARTY.get(actor.role, m.PartyEnum.NEUTRAL)

    storage_key = f"cases/{case_id}/{artifact_id}"
    artifact_row = m.Artifact(
        artifact_id=artifact_id, case_id=case_id, storage_key=storage_key,
        sha256=hashlib.sha256(data).digest(), mime_type="application/octet-stream",
        byte_size=len(data), uploaded_by=uploaded_by, scan_status="PENDING", forensics={},
    )
    session.add(artifact_row)
    session.flush()

    # RETAIN THE BYTES. Reg E 12 CFR 1005.11(d)(1) gives the card member the
    # right to request "the documents on which the institution relied"; the
    # proof tree's source_ref (page + bbox) points into this artifact. Both
    # obligations are unsatisfiable if the bytes are discarded, which is
    # exactly what this route used to do.
    artifacts.put(storage_key, data, content_type=file.content_type or "application/octet-stream")

    node, report = process_artifact(
        case_id=str(case_id), artifact_id=str(artifact_id), data=data,
        filed_at_unix=case.filed_at.replace(tzinfo=timezone.utc).timestamp() if case.filed_at else None,
        provenance=ProvenanceTier.SUBMITTED,
    )

    artifact_row.mime_type = report.get("scan", {}).get("sniffed_mime_type") or "application/octet-stream"
    artifact_row.scan_status = "ACCEPTED" if report.get("scan", {}).get("accepted") else "REJECTED"
    artifact_row.forensics = report.get("forensics") or {}

    # THE audit event that was missing. A party submitting evidence is a
    # material act on the case -- it is the input the decision is later
    # derived from, and it carries the forensic findings that reduce that
    # evidence's weight. `case_log.EVIDENCE_UPLOADED` was defined in the
    # taxonomy and written by nothing, so `GET /v1/audit/{case_id}` showed a
    # decision citing documents whose arrival was invisible: a reader could
    # not tell whether an artifact predated the adjudication or was slipped
    # in after it, which is precisely the timing question an audit trail on
    # a dispute exists to answer. Rejected uploads are logged too -- an
    # attempt to submit a file that failed the scan is a fact about the
    # case, not a non-event.
    _forensics = report.get("forensics") or {}
    case_log.append_case_event(
        session, case_id, case_log.EVIDENCE_UPLOADED,
        {
            "artifact_id": str(artifact_id),
            "sha256": artifact_row.sha256.hex(),
            "byte_size": artifact_row.byte_size,
            "mime_type": artifact_row.mime_type,
            "scan_status": artifact_row.scan_status,
            "uploaded_by": uploaded_by.value,
            "extraction_method": report.get("extraction_method"),
            "node_id": node.node_id if node is not None else None,
            "forensic_findings": _forensics.get("findings") or [],
            "confidence_penalty": _forensics.get("confidence_penalty"),
        },
        actor_id=actor.actor_id,
        actor_type=case_log.ACTOR_HUMAN if not actor.is_privileged else case_log.ACTOR_SERVICE,
    )

    # `accepted` means "the scan accepted this file and we stored it", which
    # is what `artifact.scan_status` records and what the artifact list will
    # show. It used to mean "an evidence node came out of it", so a
    # structurally valid PDF that simply contained no extractable fields came
    # back `accepted: false` with `scan.accepted: true` and `reason: "ok"` in
    # the very same payload -- the API contradicting itself, and contradicting
    # the row it had just written. The console rendered that as a red
    # "rejected" badge with no reason text, for a document it had in fact
    # retained under a Reg E obligation to produce on request.
    #
    # Extraction yielding nothing is a THIRD outcome, not a rejection, and it
    # now has its own field.
    scan_accepted = bool(report.get("scan", {}).get("accepted"))

    if node is None:
        session.commit()
        return {
            "artifact_id": str(artifact_id),
            "accepted": scan_accepted,
            "evidence_extracted": False,
            "node_id": None,
            "report": report,
        }

    attrs = node.attrs
    if node.node_type.value in _PII_BEARING_NODE_TYPES and attrs.get("extracted_fields"):
        attrs = dict(attrs)
        attrs["extracted_fields"] = encrypt_extracted_fields(
            vault, str(case.card_member_id), attrs["extracted_fields"]
        )

    node_row = m.EvidenceNodeRow(
        node_id=uuid.UUID(node.node_id), case_id=case_id,
        node_type=m.EvidenceNodeTypeEnum(node.node_type.value), attrs=attrs,
        provenance=m.ProvenanceTierEnum(node.provenance.value), extract_conf=node.extract_conf,
        artifact_id=artifact_id, source_ref=node.source_ref.to_dict() if node.source_ref else None,
    )
    session.add(node_row)
    session.commit()

    return {
        "artifact_id": str(artifact_id),
        "accepted": scan_accepted,
        "evidence_extracted": True,
        "node_id": node.node_id,
        "report": report,
    }


@router.get("/cases/{case_id}/graph")
def get_graph(
    case_id: uuid.UUID,
    session: Session = Depends(get_session),
    actor: Actor = Depends(get_current_actor),
    vault: SubjectKeyVault = Depends(get_privacy_vault),
):
    from sqlalchemy import select

    case = session.get(m.DisputeCase, case_id)
    if case is None:
        raise HTTPException(404, "case not found")
    require_case_access(actor, case)

    nodes = session.execute(select(m.EvidenceNodeRow).where(m.EvidenceNodeRow.case_id == case_id)).scalars().all()
    edges = session.execute(select(m.EvidenceEdgeRow).where(m.EvidenceEdgeRow.case_id == case_id)).scalars().all()
    node_dicts = []
    for n in nodes:
        attrs = n.attrs
        if n.node_type.value in _PII_BEARING_NODE_TYPES and attrs.get("extracted_fields"):
            attrs = dict(attrs)
            attrs["extracted_fields"] = decrypt_extracted_fields(
                vault, str(case.card_member_id), attrs["extracted_fields"]
            )
        node_dicts.append({
            "node_id": str(n.node_id), "node_type": n.node_type.value, "attrs": attrs,
            "provenance": n.provenance.value, "extract_conf": n.extract_conf,
            "source_ref": n.source_ref,
        })
    return {
        # filter_graph_for_party already drops IDENTITY/CLAIM nodes
        # entirely for a merchant actor -- decryption above only ever
        # matters for the card member themself or a reviewer/admin.
        "nodes": filter_graph_for_party(node_dicts, actor),
        "edges": [{"src": str(e.src), "dst": str(e.dst), "rel": e.rel} for e in edges],
    }


@router.get("/cases/{case_id}/artifacts")
def list_artifacts(
    case_id: uuid.UUID,
    session: Session = Depends(get_session),
    actor: Actor = Depends(get_current_actor),
):
    """Reg E 12 CFR 1005.11(d)(1): the card member may request "the
    documents on which the institution relied." This is that list; the
    bytes come from GET /v1/artifacts/{id}/content."""
    case = session.get(m.DisputeCase, case_id)
    if case is None:
        raise HTTPException(404, "case not found")
    require_case_access(actor, case)

    rows = session.execute(
        select(m.Artifact).where(m.Artifact.case_id == case_id).order_by(m.Artifact.uploaded_at)
    ).scalars().all()
    return {
        "case_id": str(case_id),
        "artifacts": [
            {
                "artifact_id": str(a.artifact_id),
                "mime_type": a.mime_type,
                "byte_size": a.byte_size,
                "sha256": a.sha256.hex(),
                "uploaded_by": a.uploaded_by.value,
                "uploaded_at": a.uploaded_at.isoformat(),
                "scan_status": a.scan_status,
                "forensics": a.forensics,
            }
            for a in rows
        ],
    }


@router.get("/artifacts/{artifact_id}/content")
def get_artifact_content(
    artifact_id: uuid.UUID,
    session: Session = Depends(get_session),
    actor: Actor = Depends(get_current_actor),
    artifacts=Depends(get_artifact_store),
):
    """Serve the retained bytes of one artifact, party-scoped.

    Integrity is re-verified against the sha256 recorded at upload before
    anything is returned: a document that no longer hashes to what the
    decision cited is a failure, not a download."""
    row = session.get(m.Artifact, artifact_id)
    if row is None:
        raise HTTPException(404, "artifact not found")
    case = session.get(m.DisputeCase, row.case_id)
    if case is None:
        raise HTTPException(404, "artifact not found")
    require_case_access(actor, case)

    # The digest recorded at upload is handed to the store, which raises
    # ArtifactIntegrityError if the bytes no longer match. Verification used
    # to be re-implemented here while `arbiter.storage.artifacts._verify` sat
    # unused; one implementation, at the boundary that actually reads the
    # bytes, is the version worth keeping.
    data = artifacts.get(row.storage_key, expected_sha256=row.sha256)
    if data is None:
        raise HTTPException(
            410,
            "artifact bytes are no longer retained. If this case was decided, its "
            "decision cites evidence that can no longer be produced -- escalate to "
            "compliance rather than treating this as a routine 404.",
        )
    return Response(
        content=data,
        media_type=row.mime_type or "application/octet-stream",
        headers={
            # Never render an attacker-supplied document inline in the
            # browser: a PDF or SVG served inline executes in this origin.
            "Content-Disposition": f'attachment; filename="{artifact_id}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


@router.post("/subjects/{subject_id}/erase")
def erase_subject_pii(
    subject_id: uuid.UUID,
    session: Session = Depends(get_session),
    actor: Actor = Depends(get_current_actor),
    vault: SubjectKeyVault = Depends(get_privacy_vault),
):
    """GDPR Article 17: destroys `subject_id`'s encryption key
    (arbiter.privacy.shredding). Every already-encrypted extracted_fields
    value for that subject becomes permanently unrecoverable; the
    surrounding evidence_node rows, the hash-chained case_event log, and
    every Merkle commitment over them are untouched -- append-only stays
    append-only, per CLAUDE.md invariant #8. Reviewer/admin only: this is
    an irreversible, compliance-triggered action, not a self-service one."""
    require_reviewer(actor)
    erased = erase_subject(vault, str(subject_id))

    # An irreversible, compliance-triggered destruction of data must itself
    # be auditable -- a DPA asking "who erased this subject, when, and under
    # what authority" needs an answer. Written to every case this subject is
    # a party to, since case_event is keyed by case rather than by subject.
    affected = session.execute(
        select(m.DisputeCase.case_id).where(
            (m.DisputeCase.card_member_id == subject_id) | (m.DisputeCase.merchant_id == subject_id)
        )
    ).scalars().all()
    for case_id in affected:
        case_log.append_case_event(
            session, case_id, case_log.SUBJECT_ERASURE_EXECUTED,
            {
                "subject_id": str(subject_id),
                "key_destroyed": erased,
                "basis": (
                    "GDPR Article 17. Crypto-shredding: the per-subject key is destroyed, "
                    "not the ciphertext -- the append-only case_event chain and every "
                    "Merkle commitment over it stay byte-identical and verifiable, while "
                    "the plaintext becomes permanently unrecoverable."
                ),
            },
            actor_id=actor.actor_id, actor_type=case_log.ACTOR_HUMAN,
        )
    session.commit()

    return {
        "subject_id": str(subject_id),
        "erased": erased,
        "cases_annotated": len(affected),
    }

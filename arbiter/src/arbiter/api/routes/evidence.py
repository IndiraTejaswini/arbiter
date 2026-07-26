from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from arbiter.db import models as m
from arbiter.db.session import get_session
from arbiter.evidence.models import ProvenanceTier
from arbiter.ingest.route import process_artifact

router = APIRouter(prefix="/v1", tags=["evidence"])


@router.post("/cases/{case_id}/evidence", status_code=201)
async def upload_evidence(
    case_id: uuid.UUID,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    case = session.get(m.DisputeCase, case_id)
    if case is None:
        raise HTTPException(404, "case not found")

    data = await file.read()
    artifact_id = uuid.uuid4()

    artifact_row = m.Artifact(
        artifact_id=artifact_id, case_id=case_id, storage_key=f"cases/{case_id}/{artifact_id}",
        sha256=hashlib.sha256(data).digest(), mime_type="application/octet-stream",
        byte_size=len(data), uploaded_by=m.PartyEnum.MERCHANT, scan_status="PENDING", forensics={},
    )
    session.add(artifact_row)
    session.flush()

    node, report = process_artifact(
        case_id=str(case_id), artifact_id=str(artifact_id), data=data,
        filed_at_unix=case.filed_at.replace(tzinfo=timezone.utc).timestamp() if case.filed_at else None,
        provenance=ProvenanceTier.SUBMITTED,
    )

    artifact_row.mime_type = report.get("scan", {}).get("sniffed_mime_type") or "application/octet-stream"
    artifact_row.scan_status = "ACCEPTED" if report.get("scan", {}).get("accepted") else "REJECTED"
    artifact_row.forensics = report.get("forensics") or {}

    if node is None:
        session.commit()
        return {"artifact_id": str(artifact_id), "accepted": False, "report": report}

    node_row = m.EvidenceNodeRow(
        node_id=uuid.UUID(node.node_id), case_id=case_id,
        node_type=m.EvidenceNodeTypeEnum(node.node_type.value), attrs=node.attrs,
        provenance=m.ProvenanceTierEnum(node.provenance.value), extract_conf=node.extract_conf,
        artifact_id=artifact_id, source_ref=node.source_ref.to_dict() if node.source_ref else None,
    )
    session.add(node_row)
    session.commit()

    return {"artifact_id": str(artifact_id), "accepted": True, "node_id": node.node_id, "report": report}


@router.get("/cases/{case_id}/graph")
def get_graph(case_id: uuid.UUID, session: Session = Depends(get_session)):
    from sqlalchemy import select

    nodes = session.execute(select(m.EvidenceNodeRow).where(m.EvidenceNodeRow.case_id == case_id)).scalars().all()
    edges = session.execute(select(m.EvidenceEdgeRow).where(m.EvidenceEdgeRow.case_id == case_id)).scalars().all()
    return {
        "nodes": [
            {"node_id": str(n.node_id), "node_type": n.node_type.value, "attrs": n.attrs,
             "provenance": n.provenance.value, "extract_conf": n.extract_conf,
             "source_ref": n.source_ref}
            for n in nodes
        ],
        "edges": [{"src": str(e.src), "dst": str(e.dst), "rel": e.rel} for e in edges],
    }

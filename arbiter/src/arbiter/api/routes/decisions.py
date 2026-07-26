from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from arbiter.api.deps import get_registry
from arbiter.db import models as m
from arbiter.db.session import get_session
from arbiter.narrate import render_narration_safe
from arbiter.horn.chain import EvaluationResult
from arbiter.horn.proof import Fact, FactStatus
from arbiter.horn.counterfactual import counterfactuals_for_all_outcomes

router = APIRouter(prefix="/v1", tags=["decisions"])


@router.get("/cases/{case_id}/decision")
def get_decision(case_id: uuid.UUID, session: Session = Depends(get_session)):
    """★ Frontend contract: proof tree + counterfactuals + narration.
    Matches the JSON shape documented in the build spec's API section."""
    case = session.get(m.DisputeCase, case_id)
    if case is None:
        raise HTTPException(404, "case not found")

    decision = session.execute(
        select(m.DecisionRow).where(m.DecisionRow.case_id == case_id).order_by(m.DecisionRow.decided_at.desc())
    ).scalars().first()
    if decision is None:
        raise HTTPException(404, "case has not been adjudicated yet")

    rulepack = get_registry().latest(case.reason_code)

    return {
        "case_id": str(case_id),
        "outcome": decision.outcome.value,
        "abstained": decision.abstained,
        "confidence": decision.confidence,
        "conformal_set": decision.conformal_set,
        "rulepack_hash": f"sha256:{decision.rulepack_hash.hex()}",
        "merchant_silent": decision.merchant_silent,
        "proof_tree": decision.proof_tree,
        "counterfactuals": decision.counterfactuals,
        "escalation_reason": decision.escalation_reason,
        "decided_at": decision.decided_at.isoformat(),
    }


@router.get("/cases/{case_id}/timeline")
def get_timeline(case_id: uuid.UUID, session: Session = Depends(get_session)):
    nodes = session.execute(select(m.EvidenceNodeRow).where(m.EvidenceNodeRow.case_id == case_id)).scalars().all()
    edges = session.execute(select(m.EvidenceEdgeRow).where(m.EvidenceEdgeRow.case_id == case_id)).scalars().all()
    contradictions = session.execute(
        select(m.ContradictionRow).where(m.ContradictionRow.case_id == case_id)
    ).scalars().all()
    events = session.execute(
        select(m.CaseEventRow).where(m.CaseEventRow.case_id == case_id).order_by(m.CaseEventRow.seq)
    ).scalars().all()

    return {
        "nodes": [
            {"node_id": str(n.node_id), "node_type": n.node_type.value, "attrs": n.attrs,
             "provenance": n.provenance.value, "extract_conf": n.extract_conf}
            for n in nodes
        ],
        "edges": [{"edge_id": str(e.edge_id), "src": str(e.src), "dst": str(e.dst), "rel": e.rel} for e in edges],
        "contradictions": [
            {"contradiction_id": str(c.contradiction_id), "kind": c.kind, "severity": c.severity.value,
             "node_ids": [str(n) for n in c.node_ids], "detail": c.detail}
            for c in contradictions
        ],
        "events": [
            {"seq": e.seq, "event_type": e.event_type, "payload": e.payload, "actor_type": e.actor_type,
             "occurred_at": e.occurred_at.isoformat()}
            for e in events
        ],
    }

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from arbiter.api.deps import get_privacy_vault
from arbiter.auth import Actor, filter_graph_for_party, require_case_access
from arbiter.auth.deps import get_current_actor
from arbiter.db import models as m
from arbiter.db.session import get_session
from arbiter.privacy.shredding import SubjectKeyVault, decrypt_extracted_fields

_PII_BEARING_NODE_TYPES = frozenset({"identity", "claim"})

router = APIRouter(prefix="/v1", tags=["decisions"])


@router.get("/cases/{case_id}/decision")
def get_decision(
    case_id: uuid.UUID, session: Session = Depends(get_session), actor: Actor = Depends(get_current_actor)
):
    """★ Frontend contract: proof tree + counterfactuals + narration.
    Matches the JSON shape documented in the build spec's API section."""
    case = session.get(m.DisputeCase, case_id)
    if case is None:
        raise HTTPException(404, "case not found")
    require_case_access(actor, case)

    decision = session.execute(
        select(m.DecisionRow).where(m.DecisionRow.case_id == case_id).order_by(m.DecisionRow.decided_at.desc())
    ).scalars().first()
    if decision is None:
        raise HTTPException(404, "case has not been adjudicated yet")

    return {
        "case_id": str(case_id),
        # Correlates with `adjudication_job.decision_id`, so a client that
        # polled a job to SUCCEEDED can confirm it is looking at the decision
        # that job produced rather than a later one.
        "decision_id": str(decision.decision_id),
        "outcome": decision.outcome.value,
        "abstained": decision.abstained,
        "confidence": decision.confidence,
        "conformal_set": decision.conformal_set,
        "rulepack_hash": f"sha256:{decision.rulepack_hash.hex()}",
        "merchant_silent": decision.merchant_silent,
        "provisional_credit_due": decision.provisional_credit_due,
        "llm_rejections": decision.llm_rejections,
        # Audit sampling (arbiter.decision.review_sampling): this case
        # auto-resolved AND was routed to a human anyway, so its review
        # enters the calibration pool with an inverse-probability weight.
        "selected_for_audit": decision.selected_for_audit,
        "review_selection_probability": decision.review_selection_probability,
        # Which of the four MANDATORY contradiction layers ran. A layer that
        # could not run is an unknown, not a clean bill of health, and that
        # distinction has to be legible to whoever reads the decision.
        "contradiction_analysis": decision.contradiction_analysis or {},
        # The prose explanation, as it was rendered at decision time --
        # `{text, source, citations[{sentence_idx, node_id}]}`. This route's
        # docstring has always advertised narration; until now it did not
        # return any, because nothing persisted it. `source` is part of the
        # contract rather than an implementation detail: "template_fallback"
        # means a generated narration was DISCARDED for citing a node that
        # does not exist, and a reader is entitled to know the explanation
        # they are reading is the deterministic backstop.
        "narration": decision.narration or {},
        # The chargeback-right gate's finding (arbiter.eligibility), recorded
        # on EVERY decision rather than only the ineligible ones. "The gate
        # ran, the filing window was open, and no exclusion applied" is a
        # positive claim, and a UI that cannot show it cannot distinguish it
        # from a case decided before the gate existed. For a
        # CHARGEBACK_INELIGIBLE outcome this is the entire explanation: no
        # evidence was weighed, so proof_tree/predicates/counterfactuals are
        # empty by construction and this is what took their place.
        "eligibility": decision.eligibility or {},
        # The derived fact set the referee evaluated. The proof tree shows
        # what carried the decision; this shows everything that was known.
        "predicates": decision.predicates,
        "proof_tree": decision.proof_tree,
        "counterfactuals": decision.counterfactuals,
        "escalation_reason": decision.escalation_reason,
        "decided_at": decision.decided_at.isoformat(),
    }


@router.get("/cases/{case_id}/timeline")
def get_timeline(
    case_id: uuid.UUID,
    session: Session = Depends(get_session),
    actor: Actor = Depends(get_current_actor),
    vault: SubjectKeyVault = Depends(get_privacy_vault),
):
    case = session.get(m.DisputeCase, case_id)
    if case is None:
        raise HTTPException(404, "case not found")
    require_case_access(actor, case)

    nodes = session.execute(select(m.EvidenceNodeRow).where(m.EvidenceNodeRow.case_id == case_id)).scalars().all()
    edges = session.execute(select(m.EvidenceEdgeRow).where(m.EvidenceEdgeRow.case_id == case_id)).scalars().all()
    contradictions = session.execute(
        select(m.ContradictionRow).where(m.ContradictionRow.case_id == case_id)
    ).scalars().all()
    events = session.execute(
        select(m.CaseEventRow).where(m.CaseEventRow.case_id == case_id).order_by(m.CaseEventRow.seq)
    ).scalars().all()

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
        })
    return {
        "nodes": filter_graph_for_party(node_dicts, actor),
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

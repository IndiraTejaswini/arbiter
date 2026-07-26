"""
The full adjudication pipeline, wired to Postgres and Redis. This is the
impure glue layer the CLAUDE.md layering contract expects: arbiter.decision
itself stays free of DB/HTTP concerns; this module is what an API route
actually calls.

Pipeline (matches the build spec's diagram):

    intake -> load network evidence (Amex-held) + quarantined document extraction
           -> provenance verification (ADEC)
           -> evidence graph + contradiction detection
           -> dual advocates (read-only, no tools, typed output)
           -> predicate derivation
           -> REFEREE: propositional Horn forward chaining -> PROOF TREE
           -> counterfactual ledger (minimal flip sets)
           -> conformal abstention gate
           -> [auto-resolve + grounded narration] OR [escalate + assembled dossier]
           -> signed append-only audit + Merkle transparency log
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from arbiter.advocate import run_dual_advocacy, run_llm_advocate
from arbiter.advocate.contract import ArgumentGraph
from arbiter.audit.sign import EventSigner
from arbiter.db import models as m
from arbiter.decision import (
    Referee,
    build_dossier,
    compute_confidence_vector,
)
from arbiter.decision.conformal import ConformalAbstentionGate
from arbiter.evidence import EvidenceGraph, EvidenceNode, EvidenceNodeType, ProvenanceTier, derive_predicate_facts
from arbiter.horn import counterfactuals_for_all_outcomes, load_bearing_predicates, per_case_symmetry
from arbiter.narrate import render_narration_safe
from arbiter.network import NetworkFacts, load_network_evidence
from arbiter.provenance import ProvenanceService, TransparencyLog
from arbiter.realtime.events import publish_stage
from arbiter.rulepack import RulepackRegistry

_signer = EventSigner()


def _row_to_node(row: m.EvidenceNodeRow) -> EvidenceNode:
    return EvidenceNode(
        case_id=str(row.case_id),
        node_type=EvidenceNodeType(row.node_type.value if hasattr(row.node_type, "value") else row.node_type),
        attrs=row.attrs,
        provenance=ProvenanceTier(row.provenance.value if hasattr(row.provenance, "value") else row.provenance),
        node_id=str(row.node_id),
        extract_conf=row.extract_conf if row.extract_conf is not None else 1.0,
        commitment_id=str(row.commitment_id) if row.commitment_id else None,
        artifact_id=str(row.artifact_id) if row.artifact_id else None,
    )


def _node_to_row(node: EvidenceNode) -> m.EvidenceNodeRow:
    return m.EvidenceNodeRow(
        node_id=uuid.UUID(node.node_id),
        case_id=uuid.UUID(node.case_id),
        node_type=m.EvidenceNodeTypeEnum(node.node_type.value),
        attrs=node.attrs,
        provenance=m.ProvenanceTierEnum(node.provenance.value),
        extract_conf=node.extract_conf,
        commitment_id=uuid.UUID(node.commitment_id) if node.commitment_id else None,
        artifact_id=uuid.UUID(node.artifact_id) if node.artifact_id else None,
    )


def _next_seq(session: Session, case_id: uuid.UUID) -> int:
    last = session.execute(
        select(m.CaseEventRow.seq).where(m.CaseEventRow.case_id == case_id).order_by(m.CaseEventRow.seq.desc())
    ).scalars().first()
    return 0 if last is None else last + 1


_GENESIS_HASH = b"\x00" * 32


def _append_event(
    session: Session, case_id: uuid.UUID, event_type: str, payload: dict, actor_id: str, actor_type: str,
    rulepack_hash: Optional[bytes] = None,
) -> m.CaseEventRow:
    import hashlib

    seq = _next_seq(session, case_id)
    prev = session.execute(
        select(m.CaseEventRow.event_hash).where(m.CaseEventRow.case_id == case_id).order_by(m.CaseEventRow.seq.desc())
    ).scalars().first()
    prev_hash = prev if prev is not None else _GENESIS_HASH
    occurred_at = datetime.now(timezone.utc)

    blob = json.dumps(
        {
            "case_id": str(case_id), "seq": seq, "event_type": event_type, "payload": payload,
            "actor_id": actor_id, "actor_type": actor_type, "occurred_at": occurred_at.isoformat(),
            "prev_hash": prev_hash.hex(), "rulepack_hash": rulepack_hash.hex() if rulepack_hash else None,
        },
        sort_keys=True, default=str, separators=(",", ":"),
    ).encode("utf-8")
    event_hash = hashlib.sha256(blob).digest()
    signature = _signer.sign(event_hash)

    row = m.CaseEventRow(
        case_id=case_id, seq=seq, event_type=event_type, payload=payload, actor_id=actor_id,
        actor_type=actor_type, rulepack_hash=rulepack_hash, occurred_at=occurred_at,
        prev_hash=prev_hash, event_hash=event_hash, signature=signature,
    )
    session.add(row)
    session.flush()
    return row


@dataclass
class AdjudicationOutcome:
    decision_row: m.DecisionRow
    proof_tree: dict
    narration_text: str


def adjudicate_case(
    session: Session,
    case: m.DisputeCase,
    registry: RulepackRegistry,
    abstention_gate: ConformalAbstentionGate,
) -> AdjudicationOutcome:
    case_id = case.case_id
    reason_code = case.reason_code
    rulepack = registry.latest(reason_code)

    publish_stage(str(case_id), "GATHERING_NETWORK", "Loading Amex-held network evidence", 0.10)
    _append_event(session, case_id, "CASE_FILED", {"reason_code": reason_code}, "system", "service")

    graph = EvidenceGraph(str(case_id))

    seed = session.execute(
        select(m.SeedTransaction).where(m.SeedTransaction.transaction_id == case.transaction_id)
    ).scalar_one_or_none()
    if seed is not None:
        facts = NetworkFacts(**seed.network_facts)
        for node in load_network_evidence(str(case_id), reason_code, facts):
            graph.add_node(node)
            session.add(_node_to_row(node))

    publish_stage(str(case_id), "PARSING_EVIDENCE", "Incorporating submitted artifacts", 0.25)
    existing_rows = session.execute(
        select(m.EvidenceNodeRow).where(m.EvidenceNodeRow.case_id == case_id)
    ).scalars().all()
    seen_ids = {n.node_id for n in graph.nodes.values()}
    for row in existing_rows:
        if str(row.node_id) not in seen_ids:
            graph.add_node(_row_to_node(row))

    publish_stage(str(case_id), "VERIFYING_PROVENANCE", "Checking ADEC commitments", 0.35)
    # Nodes at COMMITTED tier already carry a verified commitment_id (set at
    # upload/reveal time by the commitments route); nothing further to do
    # here except make the stage visible in the UI's timeline.

    publish_stage(str(case_id), "BUILDING_GRAPH", "Running contradiction analysis", 0.45)
    contradictions = graph.run_contradiction_analysis()
    for c in contradictions:
        session.add(m.ContradictionRow(
            case_id=case_id, kind=c.kind, severity=m.ContradictionSeverityEnum(c.severity),
            node_ids=[uuid.UUID(n) for n in c.node_ids], detail={"description": c.description, "layer": c.layer},
        ))

    predicate_facts = derive_predicate_facts(graph, rulepack)

    publish_stage(str(case_id), "CONSTRUCTING_ARGUMENTS", "Running dual-advocate search", 0.60)
    cm_graph, m_graph = run_dual_advocacy(rulepack, predicate_facts)

    # LLM BOUNDARY 3, turned up: an LLM advocate per side, ADDITIVE only.
    # Every proposed assertion is re-derived from the objective facts by the
    # same verify_assertions the deterministic path uses below -- nothing
    # here can introduce a predicate into truth, only enlarge an
    # already-valid ArgumentGraph with independently-confirmed triples.
    # Deterministic search stays authoritative and this never blocks on the
    # LLM being available (arbiter.advocate.llm_runner returns ([], []) if
    # unreachable).
    llm_rejections = 0
    cm_llm_triples, cm_llm_verifications = run_llm_advocate(rulepack, graph, predicate_facts, "CM", cm_graph.target_outcome)
    m_llm_triples, m_llm_verifications = run_llm_advocate(rulepack, graph, predicate_facts, "M", m_graph.target_outcome)
    llm_rejections += sum(1 for v in cm_llm_verifications if not v.verified)
    llm_rejections += sum(1 for v in m_llm_verifications if not v.verified)

    if cm_llm_triples:
        existing = {(t.predicate, t.negated) for t in cm_graph.triples}
        new_triples = tuple(t for t in cm_llm_triples if (t.predicate, t.negated) not in existing)
        cm_graph = ArgumentGraph(
            side=cm_graph.side, target_outcome=cm_graph.target_outcome, target_head=cm_graph.target_head,
            triples=cm_graph.triples + new_triples, missing_literals=cm_graph.missing_literals,
            fully_satisfied=cm_graph.fully_satisfied, best_mwc=cm_graph.best_mwc,
        )
    if m_llm_triples:
        existing = {(t.predicate, t.negated) for t in m_graph.triples}
        new_triples = tuple(t for t in m_llm_triples if (t.predicate, t.negated) not in existing)
        m_graph = ArgumentGraph(
            side=m_graph.side, target_outcome=m_graph.target_outcome, target_head=m_graph.target_head,
            triples=m_graph.triples + new_triples, missing_literals=m_graph.missing_literals,
            fully_satisfied=m_graph.fully_satisfied, best_mwc=m_graph.best_mwc,
        )

    publish_stage(str(case_id), "ADJUDICATING", "Evaluating rulepack", 0.75)
    referee = Referee()
    referee_result = referee.adjudicate(rulepack, [cm_graph, m_graph], predicate_facts)
    evaluation = referee_result.evaluation
    llm_rejections += len(referee_result.rejected_triples)

    counterfactuals = counterfactuals_for_all_outcomes(rulepack, predicate_facts)
    symmetry = per_case_symmetry(rulepack, predicate_facts)
    severity = graph.unresolved_severity()
    confidence = compute_confidence_vector(evaluation, rulepack, severity, symmetry, rejected_assertions=llm_rejections)
    abstention = abstention_gate.decide(reason_code, confidence)

    valid_node_ids = set(graph.nodes.keys())
    narration = render_narration_safe(evaluation, rulepack, valid_node_ids, counterfactuals)

    if evaluation.decision and not evaluation.conflicting_outcomes:
        outcome_map = {"MERCHANT_WINS": m.OutcomeEnum.MERCHANT_PREVAILS, "CARD_MEMBER_WINS": m.OutcomeEnum.CARD_MEMBER_PREVAILS}
        outcome = outcome_map.get(evaluation.decision, m.OutcomeEnum.INSUFFICIENT_EVIDENCE)
    elif evaluation.conflicting_outcomes:
        outcome = m.OutcomeEnum.SPLIT
    else:
        outcome = m.OutcomeEnum.INSUFFICIENT_EVIDENCE

    conformal_set = [evaluation.decision] if (evaluation.decision and abstention.auto_resolve) else []

    decision_payload = evaluation.to_dict()
    decision_row = m.DecisionRow(
        case_id=case_id,
        rulepack_hash=bytes.fromhex(rulepack.content_hash()),
        outcome=outcome,
        proof_tree=decision_payload.get("proof_tree") or {},
        predicates={p: f.status.value for p, f in predicate_facts.items()},
        counterfactuals={k: v.to_dict() for k, v in counterfactuals.items()},
        confidence=confidence.confidence(),
        conformal_set=conformal_set,
        abstained=not abstention.auto_resolve,
        escalation_reason=None if abstention.auto_resolve else abstention.reason,
        merchant_silent=not case.merchant_responded,
        llm_rejections=llm_rejections,
        signature=_signer.sign(json.dumps(decision_payload, sort_keys=True, default=str).encode("utf-8")),
    )
    session.add(decision_row)
    session.flush()

    if llm_rejections:
        _append_event(
            session, case_id, "LLM_ASSERTIONS_REJECTED",
            {"count": llm_rejections, "note": "caught by verify_assertions before reaching the referee"},
            "advocate-service", "advocate",
        )

    for rule_id in evaluation.fired_rules:
        session.add(m.RuleFiringRow(decision_id=decision_row.decision_id, rule_id=rule_id, fired=True))

    if abstention.auto_resolve:
        case.state = m.CaseStateEnum.ADJUDICATED
        publish_stage(str(case_id), "DECIDED", f"Decided: {evaluation.decision}", 1.0)
        _append_event(
            session, case_id, "DECISION_COMPUTED",
            {"decision": evaluation.decision, "fired_rules": evaluation.fired_rules, "merchant_silent": decision_row.merchant_silent},
            "referee-service", "referee", rulepack_hash=bytes.fromhex(rulepack.content_hash()),
        )
    else:
        case.state = m.CaseStateEnum.ESCALATED
        publish_stage(str(case_id), "ESCALATED", abstention.reason, 1.0)
        _append_event(
            session, case_id, "CASE_ESCALATED",
            {"reason": abstention.reason, "nonconformity": abstention.nonconformity_score}, "abstention-service", "service",
        )

    session.commit()

    return AdjudicationOutcome(decision_row=decision_row, proof_tree=decision_row.proof_tree, narration_text=narration.text)

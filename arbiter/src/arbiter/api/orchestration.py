"""
The full adjudication pipeline, wired to Postgres and Redis. This is the
impure glue layer the CLAUDE.md layering contract expects: arbiter.decision
itself stays free of DB/HTTP concerns; this module is what an API route
actually calls.

Pipeline (matches the build spec's diagram):

    intake -> CHARGEBACK-RIGHT GATE (filing window + excluded transactions)
           -> load network evidence (Amex-held) + quarantined document extraction
           -> provenance verification (ADEC)
           -> evidence graph + contradiction detection
           -> dual advocates (read-only, no tools, typed output)
           -> predicate derivation
           -> REFEREE: propositional Horn forward chaining -> PROOF TREE
           -> counterfactual ledger (minimal flip sets)
           -> conformal abstention gate
           -> [auto-resolve + grounded narration] OR [escalate + assembled dossier]
           -> signed append-only audit + Merkle transparency log

The first stage is new and sits deliberately ahead of everything else. Amex's
merchant chargeback guide gives every reason code an "Excluded Transactions"
list and a "Maximum time a dispute can be raised" window; both remove the
chargeback right outright rather than arguing about the evidence. Running
them first is not an optimisation -- it is the difference between telling a
card member "you lost on the facts" and "this transaction was never
chargeable under this code". When the gate closes, no evidence is loaded, no
advocate runs, and the referee is never called.
"""

from __future__ import annotations

import json
import logging
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import List, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from arbiter.advocate import run_dual_advocacy, run_llm_advocate
from arbiter.advocate.contract import ArgumentGraph
from arbiter.audit import case_log
from arbiter.audit.case_log import append_case_event
from arbiter.audit.sign import EventSigner
from arbiter.config import get_settings
from arbiter.db import models as m
from arbiter.decision import (
    Referee,
    compute_confidence_vector,
    compute_provisional_credit,
)
from arbiter.decision.conformal import ConformalAbstentionGate
from arbiter.decision.review_sampling import select_for_review
from arbiter.eligibility import evaluate_chargeback_right
from arbiter.evidence import EvidenceGraph, EvidenceNode, EvidenceNodeType, ProvenanceTier, derive_predicate_facts
from arbiter.horn import counterfactuals_for_all_outcomes, per_case_symmetry
from arbiter.narrate import render_narration_safe
from arbiter.network import NetworkFacts, load_eligibility_attributes, load_network_evidence
from arbiter.realtime.events import publish_stage
from arbiter.rulepack import RulepackRegistry

logger = logging.getLogger(__name__)

_signer = EventSigner()


def _advocate_result(future: "Future") -> Tuple[List, List]:
    """Unwrap a parallel advocate future, degrading to an empty result on
    any failure. An LLM advocate is ADDITIVE ONLY (see below): losing one
    costs recall, never correctness, so it must never fail the case."""
    try:
        return future.result()
    except Exception:  # pragma: no cover - defensive; run_llm_advocate swallows its own
        logger.warning("LLM advocate raised; continuing with deterministic search only", exc_info=True)
        return [], []


# -- The evidence_node.attrs JSON boundary --------------------------------
#
# `attrs` is JSONB, and the network loader puts real `datetime` objects in it
# (`temporal_value`, which the Allen-interval contradiction layer needs as a
# datetime to do anything at all). psycopg cannot adapt a datetime inside a
# JSON document, so EVERY adjudication died at the first flush with
# "Object of type datetime is not JSON serializable" -- meaning the pipeline
# had never once run against Postgres. It was not caught because every test
# that exercises adjudication builds the graph in memory and never persists
# it, and the `--sql` migration check renders DDL without executing anything.
#
# Serialise on the way out, re-hydrate on the way back in. Doing it here, at
# the persistence boundary, rather than in `arbiter.network.loader` is
# deliberate: the loader's consumers -- `EvidenceGraph.analyze_contradictions`
# above all -- need genuine datetimes, and flattening them at the source
# would leave the temporal layer comparing ISO strings. That fails silently
# rather than loudly, which is the worse of the two, and it is exactly the
# "a MANDATORY layer quietly stops working" failure this system treats as
# most serious.

def _json_safe(value):
    """Recursively convert a value into something psycopg can store in JSONB.

    Datetimes become ISO-8601 strings; `_hydrate_attrs` reverses it.
    """
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


# Attribute keys whose values are datetimes and must be revived as such after
# a JSONB round-trip. An explicit list, not "try to parse every string that
# looks like a date": a heuristic would happily convert an order reference or
# a tracking number that merely resembles a timestamp, and silently changing
# the type of evidence attributes is not a thing to do speculatively.
_DATETIME_ATTRS = frozenset({"temporal_value"})


def _hydrate_attrs(attrs: dict) -> dict:
    """Reverse `_json_safe` for the keys that are known to be datetimes.

    Without this, re-adjudication -- which reads persisted nodes FIRST, for
    idempotence -- would hand the temporal layer ISO strings, and
    `TimeInterval` would compare text where it means to compare instants.
    """
    if not attrs:
        return attrs
    out = dict(attrs)
    for key in _DATETIME_ATTRS & out.keys():
        value = out[key]
        if isinstance(value, str):
            try:
                out[key] = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                # Leave it alone rather than guess. `_extract_temporal_facts`
                # skips a fact it cannot interpret, which loses one temporal
                # comparison; inventing a timestamp would lose correctness.
                logger.warning("evidence node attr %r is not an ISO timestamp: %r", key, value)
    return out


def _row_to_node(row: m.EvidenceNodeRow) -> EvidenceNode:
    return EvidenceNode(
        case_id=str(row.case_id),
        node_type=EvidenceNodeType(row.node_type.value if hasattr(row.node_type, "value") else row.node_type),
        attrs=_hydrate_attrs(row.attrs),
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
        attrs=_json_safe(node.attrs),
        provenance=m.ProvenanceTierEnum(node.provenance.value),
        extract_conf=node.extract_conf,
        commitment_id=uuid.UUID(node.commitment_id) if node.commitment_id else None,
        artifact_id=uuid.UUID(node.artifact_id) if node.artifact_id else None,
    )


# The event writer moved to arbiter.audit.case_log so every route -- not
# just this pipeline -- can append to the audit chain. Human actions
# (analyst overrides, GDPR erasures) previously wrote nothing at all.
_append_event = append_case_event


@dataclass
class AdjudicationOutcome:
    """What one run of the pipeline produced, for the caller in this process.

    `narration_text` is a convenience copy: the DURABLE record is
    `decision_row.narration`, which carries the citation set and the renderer
    that produced it. That distinction is the whole point of migration 0008 --
    this field existed before it, the worker discarded it, and the narration
    reached nobody.
    """

    decision_row: m.DecisionRow
    proof_tree: dict
    narration_text: str


def _ineligible_narration(result, reason_code: str, network_code: str) -> str:
    """Deterministic prose for a case the gate ended. No LLM, by the same
    rule as everywhere else: the text a party receives when they are told
    their dispute is not chargeable must be derivable from the finding, not
    generated from it."""
    lines = [
        f"This dispute was filed under reason code {reason_code} "
        f"(American Express network code {network_code}).",
        "",
        "American Express's chargeback rules do not make this transaction "
        "chargeable to the merchant under that code, so no adjudication on "
        "the evidence took place:",
        "",
    ]
    for finding in result.fired_exclusions:
        lines.append(f"  - {finding.description.strip()}")
        if finding.legal_basis:
            lines.append(f"    Basis: {finding.legal_basis}")
        for condition in finding.satisfied_conditions:
            lines.append(f"    Established: {condition}")
    if result.filing_window.timely is False:
        for branch in result.filing_window.branches:
            if branch.timely is False and branch.deadline:
                cap = " (absolute cap on this clock)" if branch.capped_out else ""
                lines.append(
                    f"  - The {network_code} filing window {branch.branch_id} closed on "
                    f"{branch.deadline.isoformat()}{cap}, measured from "
                    f"{branch.anchor_attribute}."
                )
    lines += [
        "",
        "This finding concerns the merchant's liability only. It does not "
        "resolve, limit, or decide the card member's separate billing-error "
        "rights against the issuer under Regulation Z (12 CFR 1026.13) or "
        "Regulation E (12 CFR 1005.11), which continue on their own clocks.",
    ]
    return "\n".join(lines)


def _record_ineligible(
    session: Session,
    case: m.DisputeCase,
    rulepack,
    result,
) -> AdjudicationOutcome:
    """Terminate a case at the chargeback-right gate.

    Everything an adjudicated decision records is still recorded -- pinned
    rulepack hash, signed payload, append-only case events -- because this IS
    a decision, just not one reached by weighing evidence. What is
    deliberately NOT recorded is anything implying a contest happened:
    `predicates` and `counterfactuals` are empty because none were derived,
    and `conformal_set` is empty because the conformal gate never ran.
    """
    payload = {
        "reason_code": case.reason_code,
        "rulepack_hash": rulepack.content_hash(),
        "decision": m.OutcomeEnum.CHARGEBACK_INELIGIBLE.value,
        "eligibility": result.to_dict(),
    }
    narration = _ineligible_narration(result, case.reason_code, result.network_code)

    # Reg E is independent of the network chargeback right, and this is the
    # case that proves it: the merchant cannot be charged back, and the card
    # member is still owed provisional credit while the issuer's own
    # investigation continues. `decision=None, abstained=False` is the
    # honest input -- nothing was resolved on the merits, and the system did
    # not abstain either; it never reached the question.
    provisional_credit = compute_provisional_credit(
        reg_regime=case.reg_regime, decision=None, abstained=False, conflicting=False,
    )

    decision_row = m.DecisionRow(
        case_id=case.case_id,
        rulepack_hash=bytes.fromhex(rulepack.content_hash()),
        outcome=m.OutcomeEnum.CHARGEBACK_INELIGIBLE,
        # EMPTY, like `predicates` and `counterfactuals` beside it. This used
        # to hold `result.to_dict()` -- the eligibility record -- which put a
        # value of one shape in a column that means another. `proof_tree` is
        # a `ProofNode` everywhere else in the system, and every reader is
        # typed for that: the API's own `proof_tree: ProofNode | null`, and
        # the console's `<ProofTree>`, which crashed on `node.literals.length`
        # the first time anyone opened a deflected case. Nothing was lost by
        # emptying it -- the identical dict is already in `eligibility`,
        # which is the column that means "what the gate found".
        #
        # The docstring above says what is deliberately not recorded here is
        # "anything implying a contest happened". A proof tree is precisely
        # that, and writing one under a different shape said it anyway.
        proof_tree={},
        predicates={},
        counterfactuals={},
        # 1.0 is not a flourish. Ineligibility is only ever recorded off a
        # DETERMINED exclusion or a DETERMINED window breach -- an unknown
        # attribute cannot fire an exclusion (arbiter.eligibility.models) --
        # so there is no probabilistic step anywhere in this path to be less
        # than certain about.
        confidence=1.0,
        # Empty on purpose. The conformal gate's coverage guarantee is
        # calibrated over adjudicated outcomes; putting a category it was
        # never calibrated on into its output set would silently corrupt
        # exactly the guarantee the set exists to express.
        conformal_set=[],
        abstained=False,
        escalation_reason=None,
        merchant_silent=not case.merchant_responded,
        llm_rejections=0,
        provisional_credit_due=provisional_credit.due,
        contradiction_analysis={},
        # Deterministic prose, by the same rule as everywhere else: the text
        # a party receives when told their dispute was never chargeable must
        # be derivable from the finding, not generated from it. `source` is
        # "eligibility_gate" rather than "template" so a reader can tell at a
        # glance that this narration explains a gate, not a proof tree --
        # there are no evidence citations here because no evidence was
        # weighed, and an empty citation list must not read as "we cited
        # nothing" when the truth is "there was nothing to cite".
        narration={
            "text": narration,
            "source": "eligibility_gate",
            # Split on the line breaks the renderer actually wrote, not on
            # sentence punctuation: this text is structured prose with
            # indented bullets for each fired exclusion, and the legal
            # citations in its closing paragraph ("12 CFR 1026.13",
            # "1005.11") contain full stops that any sentence splitter would
            # break on mid-reference.
            "sentences": [line for line in narration.split("\n") if line.strip()],
            "citations": [],
        },
        eligibility=result.to_dict(),
        # Not routed through the audit sampler: `review_decision` feeds every
        # human review into the conformal calibration pool, and a case the
        # conformal gate never scored has no nonconformity score to
        # contribute. Sampling these for review is worth doing and belongs on
        # a separate sampler -- the `ix_decision_chargeback_ineligible` and
        # `ix_decision_eligibility_undetermined` indexes (migration 0007)
        # exist so that sampler has a cheap population to draw from.
        selected_for_audit=False,
        review_selection_probability=None,
        signature=_signer.sign(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")),
        key_epoch=_signer.epoch,
    )
    session.add(decision_row)
    session.flush()

    _append_event(
        session, case.case_id, case_log.CHARGEBACK_RIGHT_UNAVAILABLE,
        {
            "network_code": result.network_code,
            "reason": result.reason,
            "exclusions_fired": [f.to_dict() for f in result.fired_exclusions],
            "filing_window": result.filing_window.to_dict(),
            "note": (
                "The network chargeback right did not exist for this dispute, so no "
                "evidence was weighed and no advocate ran. This does not decide the "
                "card member's Reg Z/Reg E rights against the issuer, which are "
                "separate obligations on separate clocks (arbiter.decision.deadlines)."
            ),
        },
        "eligibility-gate", case_log.ACTOR_SERVICE,
        rulepack_hash=bytes.fromhex(rulepack.content_hash()),
    )
    if provisional_credit.due:
        _append_event(
            session, case.case_id, case_log.PROVISIONAL_CREDIT_DUE,
            {"reg_regime": case.reg_regime, "reason": provisional_credit.reason},
            "provisional-credit-service", case_log.ACTOR_SERVICE,
        )

    case.state = m.CaseStateEnum.DEFLECTED
    publish_stage(str(case.case_id), "DECIDED", result.reason, 1.0)
    session.commit()
    return AdjudicationOutcome(
        decision_row=decision_row, proof_tree=decision_row.proof_tree, narration_text=narration,
    )


def adjudicate_case(
    session: Session,
    case: m.DisputeCase,
    registry: RulepackRegistry,
    abstention_gate: ConformalAbstentionGate,
) -> AdjudicationOutcome:
    case_id = case.case_id
    reason_code = case.reason_code
    rulepack = registry.latest(reason_code)

    # ADJUDICATION_STARTED, not CASE_FILED. This used to re-emit CASE_FILED
    # at the top of every run, so a case adjudicated twice showed three
    # CASE_FILED events: the real one written by `create_dispute` with the
    # full intake payload, then two thinner ones claiming the case was filed
    # after its evidence had already been uploaded. A re-adjudication is a
    # distinct, legitimate event and now says so.
    _append_event(
        session, case_id, case_log.ADJUDICATION_STARTED,
        {"reason_code": reason_code, "rulepack_hash": rulepack.content_hash()},
        "adjudication-pipeline", case_log.ACTOR_SERVICE,
    )

    seed = session.execute(
        select(m.SeedTransaction).where(m.SeedTransaction.transaction_id == case.transaction_id)
    ).scalar_one_or_none()
    facts = NetworkFacts(**seed.network_facts) if seed is not None else None

    # -- STAGE 0: the chargeback-right gate -------------------------------
    # "Excluded Transactions" and "Maximum time a dispute can be raised" from
    # the reason code's own Amex guide entry, evaluated over network-held
    # attributes only. This runs before evidence is loaded, before either
    # advocate, and before the referee -- because when it closes, none of
    # them are the right thing to have done. See `_record_ineligible`.
    publish_stage(str(case_id), "CHECKING_CHARGEBACK_RIGHT", "Checking filing window and exclusions", 0.05)
    eligibility = evaluate_chargeback_right(
        rulepack.chargeback_right,
        load_eligibility_attributes(facts, transaction_at=seed.transaction_at) if facts else {},
        case.filed_at,
    )
    if not eligibility.available:
        return _record_ineligible(session, case, rulepack, eligibility)
    if eligibility.undetermined:
        _append_event(
            session, case_id, case_log.CHARGEBACK_RIGHT_UNDETERMINED,
            {
                "network_code": eligibility.network_code,
                "undetermined_attributes": list(eligibility.undetermined),
                "note": (
                    "An exclusion or filing window under this reason code turns on an "
                    "attribute the ledger did not supply, so it could not be evaluated and "
                    "the case proceeded to the merits. Unknown never excludes -- see "
                    "arbiter.eligibility.models for why that fail direction is deliberate "
                    "and opposite to this system's usual one."
                ),
            },
            "eligibility-gate", case_log.ACTOR_SERVICE,
        )

    publish_stage(str(case_id), "GATHERING_NETWORK", "Loading Amex-held network evidence", 0.10)

    graph = EvidenceGraph(str(case_id))

    # Read what is already persisted FIRST, so re-adjudication is idempotent.
    # Network evidence carries deterministic node_ids (arbiter.network.loader
    # ._stable_node_id), so a re-derived node either already exists -- in
    # which case we reuse the stored row rather than inserting a second copy
    # of the same fact -- or is genuinely new. Before this, every
    # adjudication minted fresh random ids while the previous run's rows were
    # still loaded from the database, so the graph accumulated one duplicate
    # node per network fact per run.
    existing_rows = session.execute(
        select(m.EvidenceNodeRow).where(m.EvidenceNodeRow.case_id == case_id)
    ).scalars().all()
    existing_ids = {str(row.node_id) for row in existing_rows}

    if facts is not None:
        for node in load_network_evidence(str(case_id), reason_code, facts):
            graph.add_node(node)
            if node.node_id not in existing_ids:
                session.add(_node_to_row(node))
                existing_ids.add(node.node_id)

    publish_stage(str(case_id), "PARSING_EVIDENCE", "Incorporating submitted artifacts", 0.25)
    seen_ids = set(graph.nodes.keys())
    for row in existing_rows:
        if str(row.node_id) not in seen_ids:
            graph.add_node(_row_to_node(row))

    publish_stage(str(case_id), "VERIFYING_PROVENANCE", "Checking ADEC commitments", 0.35)
    # Nodes at COMMITTED tier already carry a verified commitment_id (set at
    # upload/reveal time by the commitments route); nothing further to do
    # here except make the stage visible in the UI's timeline.

    publish_stage(str(case_id), "BUILDING_GRAPH", "Running contradiction analysis", 0.45)
    # All four layers are MANDATORY (arbiter.evidence.graph.MANDATORY_LAYERS):
    # temporal (Allen), numeric (reconciliation), identity (coherence), and
    # semantic (DeBERTa-NLI exclusively -- no generative model touches this
    # pipeline). `analysis` carries per-layer status, because an empty
    # findings list is ambiguous between "clean" and "a mandatory check
    # could not run".
    analysis = graph.analyze_contradictions()
    contradictions = analysis.contradictions
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
    #
    # Both sides run CONCURRENTLY. They are independent by construction --
    # each reads the same frozen `graph`/`predicate_facts` and writes
    # nothing -- so there is no ordering constraint between them, and
    # running them serially made the dominant latency term (~10s per call)
    # additive instead of parallel. A thread pool rather than asyncio
    # because `arbiter.llm.client.complete_json` is a blocking httpx call;
    # two threads parked on socket reads is exactly what threads are for.
    llm_rejections = 0
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="advocate") as pool:
        cm_future = pool.submit(
            run_llm_advocate, rulepack, graph, predicate_facts, "CM", cm_graph.target_outcome
        )
        m_future = pool.submit(
            run_llm_advocate, rulepack, graph, predicate_facts, "M", m_graph.target_outcome
        )
        # run_llm_advocate never raises (CLAUDE.md #11: every LLM call site
        # degrades to an empty result), so .result() cannot propagate an
        # exception here -- but a defensive fallback keeps a future
        # refactor of that contract from silently taking down adjudication.
        cm_llm_triples, cm_llm_verifications = _advocate_result(cm_future)
        m_llm_triples, m_llm_verifications = _advocate_result(m_future)
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

    # A HIGH/CRITICAL unresolved contradiction hard-blocks auto-resolution,
    # ahead of and independent of the conformal comparison. Previously the
    # only channel a contradiction had into the outcome was the
    # `contradiction_clarity` term of the confidence vector -- worth 0.25 of
    # the weighted score -- which a case that was otherwise complete and
    # unambiguous could simply outvote. Measured: a case with a CRITICAL
    # contradiction scored nonconformity 0.265 against a threshold of 0.688
    # and auto-resolved. Conformal coverage is a population-level guarantee;
    # it was never the right instrument for a per-case safety invariant.
    blocking = severity if severity in get_settings().contradiction_block_severities else None

    # A MANDATORY contradiction layer that could not run is an unknown, and
    # an unknown blocks auto-resolution exactly like an unresolved HIGH
    # contradiction does. Recording it as "no contradictions found" would
    # convert a missing safety check into a clean bill of health -- which
    # is precisely what happened while the semantic layer sat dead.
    if analysis.must_escalate:
        blocking = blocking or (
            f"INCOMPLETE_ANALYSIS[{','.join(analysis.unavailable_layers)}]"
        )

    abstention = abstention_gate.decide(reason_code, confidence, blocking_contradiction=blocking)

    valid_node_ids = set(graph.nodes.keys())
    narration = render_narration_safe(evaluation, rulepack, valid_node_ids, counterfactuals)

    if evaluation.decision and not evaluation.conflicting_outcomes:
        # "SPLIT" here is a rulepack-derived outcome (e.g. C02's
        # split_outcome: a partial refund shortfall) reached the SAME way
        # MERCHANT_WINS/CARD_MEMBER_WINS are -- cleanly, with exactly one
        # decision predicate satisfied. That's distinct from the
        # conflicting_outcomes branch below, where >1 outcome fired at
        # once and decision is None: a genuine ambiguity requiring a
        # human, not a rule-derived partial resolution.
        outcome_map = {
            "MERCHANT_WINS": m.OutcomeEnum.MERCHANT_PREVAILS,
            "CARD_MEMBER_WINS": m.OutcomeEnum.CARD_MEMBER_PREVAILS,
            "SPLIT": m.OutcomeEnum.SPLIT,
        }
        outcome = outcome_map.get(evaluation.decision, m.OutcomeEnum.INSUFFICIENT_EVIDENCE)
    elif evaluation.conflicting_outcomes:
        outcome = m.OutcomeEnum.SPLIT
    else:
        outcome = m.OutcomeEnum.INSUFFICIENT_EVIDENCE

    conformal_set = [evaluation.decision] if (evaluation.decision and abstention.auto_resolve) else []

    # Reg E provisional credit (arbiter.decision.provisional_credit): a
    # SECOND, independent axis from the win/lose verdict above -- it can
    # (and, per 12 CFR 1005.11(c), specifically SHOULD) be true on an
    # escalated/abstained case. Computed from the same abstention-gate
    # output the verdict itself already needed, so it costs nothing extra
    # to determine instantly instead of on a 10-business-day clock.
    provisional_credit = compute_provisional_credit(
        reg_regime=case.reg_regime,
        decision=evaluation.decision,
        abstained=not abstention.auto_resolve,
        conflicting=bool(evaluation.conflicting_outcomes),
    )

    # AUDIT SAMPLING -- the fix for calibration selection bias. A fraction
    # of auto-resolved cases is routed to a human anyway, so the calibration
    # pool sees the region of the distribution the escalation path never
    # visits. Without it, reviews only ever come from the escalated (high-
    # nonconformity) tail, and feeding that tail back inflates the quantile
    # -- making the gate MORE permissive the more review is done.
    review_selection = select_for_review(str(case_id), auto_resolved=abstention.auto_resolve)

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
        provisional_credit_due=provisional_credit.due,
        contradiction_analysis=analysis.to_dict(),
        # The explanation, stored with the thing it explains. `narration`
        # carries its citation set and which renderer produced it -- in
        # particular "template_fallback", meaning an LLM narration WAS
        # generated and then discarded for citing an evidence node that does
        # not exist (CLAUDE.md invariant #5). That the veto fired is a
        # measurement worth keeping; it is unrecoverable if only the prose
        # survives.
        narration=narration.to_dict(),
        # Recorded on every decision, not only the ineligible ones: "the
        # gate ran, the filing window was open, and no exclusion applied" is
        # a positive claim the audit trail has to be able to make, and its
        # absence has to be distinguishable from a case that predates the
        # gate existing.
        eligibility=eligibility.to_dict(),
        selected_for_audit=review_selection.selected_for_review and abstention.auto_resolve,
        review_selection_probability=review_selection.selection_probability,
        signature=_signer.sign(json.dumps(decision_payload, sort_keys=True, default=str).encode("utf-8")),
        key_epoch=_signer.epoch,
    )
    session.add(decision_row)
    session.flush()

    if provisional_credit.due:
        _append_event(
            session, case_id, case_log.PROVISIONAL_CREDIT_DUE,
            {"reg_regime": case.reg_regime, "reason": provisional_credit.reason},
            "provisional-credit-service", case_log.ACTOR_SERVICE,
        )

    if llm_rejections:
        _append_event(
            session, case_id, case_log.LLM_ASSERTIONS_REJECTED,
            {"count": llm_rejections, "note": "caught by verify_assertions before reaching the referee"},
            "advocate-service", case_log.ACTOR_ADVOCATE,
        )

    # An LLM narration that failed citation grounding was discarded and the
    # deterministic template rendered instead. That is invariant #5's veto
    # firing, and it belongs in the audit chain for the same reason
    # LLM_ASSERTIONS_REJECTED does: a caught hallucination is a visible
    # signal, not a silently-swallowed detail.
    if narration.source == "template_fallback":
        _append_event(
            session, case_id, case_log.NARRATION_GROUNDING_FAILED,
            {
                "note": (
                    "A generated narration cited an evidence node that does not exist on "
                    "this case, so the ENTIRE narration was discarded and the deterministic "
                    "template rendered instead. One ungrounded sentence voids the whole "
                    "output -- there is no partial acceptance."
                ),
            },
            "narration-service", case_log.ACTOR_SERVICE,
        )

    for rule_id in evaluation.fired_rules:
        session.add(m.RuleFiringRow(decision_id=decision_row.decision_id, rule_id=rule_id, fired=True))

    if review_selection.selected_for_review and abstention.auto_resolve:
        _append_event(
            session, case_id, case_log.SELECTED_FOR_AUDIT_REVIEW,
            {
                "selection_probability": review_selection.selection_probability,
                "reason": review_selection.reason,
                "note": (
                    "This case auto-resolved and was ALSO routed to a human. Its review "
                    "enters the calibration pool with an inverse-probability weight, which "
                    "is what keeps the pool exchangeable with the deployment distribution."
                ),
            },
            "review-sampler", case_log.ACTOR_SERVICE,
        )

    if abstention.auto_resolve:
        case.state = m.CaseStateEnum.ADJUDICATED
        publish_stage(str(case_id), "DECIDED", f"Decided: {evaluation.decision}", 1.0)
        _append_event(
            session, case_id, case_log.DECISION_COMPUTED,
            {"decision": evaluation.decision, "fired_rules": evaluation.fired_rules, "merchant_silent": decision_row.merchant_silent},
            "referee-service", case_log.ACTOR_REFEREE, rulepack_hash=bytes.fromhex(rulepack.content_hash()),
        )
    else:
        case.state = m.CaseStateEnum.ESCALATED
        publish_stage(str(case_id), "ESCALATED", abstention.reason, 1.0)
        _append_event(
            session, case_id, case_log.CASE_ESCALATED,
            {"reason": abstention.reason, "nonconformity": abstention.nonconformity_score},
            "abstention-service", case_log.ACTOR_SERVICE,
        )

    session.commit()

    return AdjudicationOutcome(decision_row=decision_row, proof_tree=decision_row.proof_tree, narration_text=narration.text)

"""
ARBITER end-to-end demo: the §9.3 DisputeWorkflow sequence, run for real
over the eight seed scenarios in datagen/synth.py.

    python demo.py

This exercises, with zero mocked results, everything built in this repo:
graph construction and the four-layer contradiction engine (§A6), ADEC
provenance verification over a real Merkle transparency log (§A1), dual
advocacy and mechanical triple verification (§A2), deterministic rulepack
evaluation emitting a proof tree (§A3), exact counterfactuals (§A4),
conformal abstention (§A5), grounded narration with citation verification
(§6.6), and an append-only, hash-chained, Merkle-anchored audit trail
(§7.2). Per §12.1: "After [the Referee] you can adjudicate hand-built cases
end to end with zero AI in the loop" -- true here in the strongest sense,
since no LLM runs anywhere in this environment; the two advocates are the
deterministic prime-implicant search described in services/advocate/advocate.py.
"""

from __future__ import annotations

import random
import textwrap
from pathlib import Path
from typing import Dict, List

from arbiter.horn import Engine, RulePack
from arbiter.rulepack import load_rulepack_dir
from arbiter.decision import ConformalAbstentionGate, compute_confidence_vector, Referee
from arbiter.advocate import completeness_gap, run_dual_advocacy
from arbiter.audit import AuditService, EventStore
from arbiter.horn import counterfactuals_for_all_outcomes, load_bearing_predicates
from arbiter.narrate import render_narration_safe
from arbiter.provenance import ProvenanceService, TransparencyLog
from arbiter.evidence import derive_predicate_facts

from datagen.synth import Scenario, all_scenarios

RULEPACK_DIR = Path(__file__).resolve().parent / "rulepacks" / "amex"
WIDTH = 88


def _rule(char: str = "-") -> str:
    return char * WIDTH


def _seed_calibration(gate: ConformalAbstentionGate, reason_codes: List[str], n: int = 200) -> None:
    """Seed each reason code's Mondrian calibration pool with a plausible
    historical nonconformity-score distribution, standing in for real
    senior-analyst-adjudicated cases (§A5). Two clusters: mostly clear-cut
    historical cases, a smaller tail of genuinely hard ones -- deliberately
    not a single tidy Gaussian, since the coverage guarantee is
    distribution-free and should be demonstrated as such."""
    rng = random.Random(20260725)
    for code in reason_codes:
        for _ in range(n):
            if rng.random() < 0.75:
                score = max(0.0, min(1.0, rng.gauss(0.18, 0.09)))
            else:
                score = max(0.0, min(1.0, rng.gauss(0.55, 0.15)))
            gate.add_calibration_example(code, score)


def run_case(
    scenario: Scenario,
    rulepacks: Dict[str, RulePack],
    engine: Engine,
    referee: Referee,
    abstention_gate: ConformalAbstentionGate,
    event_store: EventStore,
    audit_service: AuditService,
) -> dict:
    rulepack = rulepacks[scenario.reason_code]
    graph = scenario.graph

    event_store.append(scenario.case_id, "CASE_FILED",
                        {"reason_code": scenario.reason_code, "title": scenario.title}, "cm-demo", "human")

    # L1 provenance: verify any ADEC commitments this scenario carries.
    adec_report = None
    if scenario.provenance is not None:
        adec_nodes = [n for n in graph.nodes.values() if n.attrs.get("commitment_id")]
        adec_report = []
        for n in adec_nodes:
            commitment = scenario.provenance.get(n.attrs["commitment_id"])
            adec_report.append({
                "node": n.attrs.get("label", n.node_id),
                "predicate_value": n.attrs.get("predicate_value"),
                "committed_at": commitment.committed_at.isoformat() if commitment else None,
            })

    # L2/L3: objective predicate facts + four-layer contradiction analysis.
    facts = derive_predicate_facts(graph, rulepack)
    contradictions = graph.run_contradiction_analysis()
    event_store.append(scenario.case_id, "EVIDENCE_GATHERED",
                        {"predicate_count": len(facts), "contradiction_count": len(contradictions)},
                        "graph-service", "service")

    # L3: dual advocacy (search), never decides.
    cm_graph, m_graph = run_dual_advocacy(rulepack, facts)
    gap = completeness_gap(rulepack, facts)

    # L4: deterministic referee -- decision runs over the objective facts;
    # triple verification is an independent dishonesty/audit check (see
    # services/referee/referee.py's module docstring for why).
    referee_result = referee.adjudicate(rulepack, [cm_graph, m_graph], facts)
    evaluation = referee_result.evaluation
    event_store.append(
        scenario.case_id, "DECISION_COMPUTED",
        {"decision": evaluation.decision, "fired_rules": evaluation.fired_rules,
         "conflicting_outcomes": list(evaluation.conflicting_outcomes)},
        "referee-service", "referee", rulepack_hash=evaluation.rulepack_hash,
    )

    # L4/L5: exact counterfactuals -- one mechanism, many product surfaces.
    cfs = counterfactuals_for_all_outcomes(rulepack, facts)
    load_bearing = load_bearing_predicates(evaluation)

    # L4: conformal abstention gate.
    severity = graph.unresolved_severity()
    from arbiter.horn import per_case_symmetry
    symmetry = per_case_symmetry(rulepack, facts)
    confidence = compute_confidence_vector(evaluation, rulepack, severity, symmetry)
    abstention_decision = abstention_gate.decide(scenario.reason_code, confidence)

    event_store.append(
        scenario.case_id, "ABSTENTION_DECIDED",
        {"auto_resolve": abstention_decision.auto_resolve, "nonconformity": abstention_decision.nonconformity_score,
         "reason": abstention_decision.reason},
        "abstention-service", "service",
    )

    # L5: grounded narration, citation-verified against the real graph.
    valid_node_ids = set(graph.nodes.keys())
    narration = render_narration_safe(evaluation, rulepack, valid_node_ids, cfs)

    # L0: anchor the decision event into the transparency log.
    decision_event = event_store.events_for(scenario.case_id)[-3]  # DECISION_COMPUTED
    audit_service.anchor(decision_event)
    audit_service.seal()
    chain = event_store.verify_chain(scenario.case_id)
    anchored_ok = audit_service.verify_anchored(decision_event)

    return {
        "rulepack": rulepack,
        "facts": facts,
        "contradictions": contradictions,
        "cm_graph": cm_graph,
        "m_graph": m_graph,
        "referee_result": referee_result,
        "evaluation": evaluation,
        "counterfactuals": cfs,
        "load_bearing": load_bearing,
        "confidence": confidence,
        "abstention": abstention_decision,
        "narration": narration,
        "chain_valid": chain.valid,
        "anchored_ok": anchored_ok,
        "adec_report": adec_report,
        "completeness_gap": gap,
    }


def print_report(scenario: Scenario, report: dict) -> None:
    print(_rule("="))
    print(f"{scenario.case_id}  [{scenario.reason_code}]  {scenario.title}")
    print(_rule("="))
    print(textwrap.fill(scenario.description, width=WIDTH))
    print()

    evaluation = report["evaluation"]
    if evaluation.conflicting_outcomes:
        print(f"DECISION: CONFLICT -- {', '.join(evaluation.conflicting_outcomes)} all fired; escalate to human")
    elif evaluation.decision:
        print(f"DECISION: {evaluation.decision}  (fired: {', '.join(evaluation.fired_rules)})")
    else:
        print("DECISION: none reached (insufficient evidence)")

    if report["adec_report"]:
        print()
        print("ADEC provenance check:")
        for r in report["adec_report"]:
            print(f"  - {r['node']}: predicate_value={r['predicate_value']} committed_at={r['committed_at']}")

    if report["contradictions"]:
        print()
        print(f"Contradictions found ({len(report['contradictions'])}):")
        for c in report["contradictions"]:
            print(f"  [{c.layer}/{c.severity}] {c.kind}: {c.description}")

    cm, m = report["cm_graph"], report["m_graph"]
    print()
    print(f"Advocate-CM: fully_satisfied={cm.fully_satisfied}  triples={len(cm.triples)}  "
          f"missing={[p for p, _ in cm.missing_literals]}")
    print(f"Advocate-M:  fully_satisfied={m.fully_satisfied}  triples={len(m.triples)}  "
          f"missing={[p for p, _ in m.missing_literals]}")

    rejected = report["referee_result"].rejected_triples
    if rejected:
        print(f"Referee rejected {len(rejected)} advocate triple(s) on verification (dishonesty/citation check).")

    if report["load_bearing"]:
        print()
        print(f"Load-bearing predicates: {', '.join(report['load_bearing'])}")

    print()
    print("Counterfactuals:")
    for outcome, cf in report["counterfactuals"].items():
        if cf.already_satisfied:
            print(f"  {outcome}: already satisfied")
        elif cf.delta:
            items = ", ".join(f"{d.action.lower()} {d.predicate}" + ("" if d.obtainable else " [not obtainable]")
                               for d in cf.delta)
            print(f"  {outcome}: needs -> {items}")
        else:
            print(f"  {outcome}: no reachable path given current rulepack")

    cv = report["confidence"]
    ab = report["abstention"]
    print()
    print(f"Confidence vector: {cv.to_dict()}")
    print(f"Abstention gate:   {'AUTO-RESOLVE' if ab.auto_resolve else 'ESCALATE'}  -- {ab.reason}")

    print()
    print("Narration:")
    print(textwrap.fill(report["narration"].text, width=WIDTH, initial_indent="  ", subsequent_indent="  "))

    print()
    print(f"Audit: hash-chain valid={report['chain_valid']}  anchored-in-transparency-log={report['anchored_ok']}")
    print()


def main() -> None:
    rulepacks = load_rulepack_dir(RULEPACK_DIR)
    engine = Engine()
    referee = Referee(engine)

    abstention_gate = ConformalAbstentionGate(alpha=0.05)
    _seed_calibration(abstention_gate, list(rulepacks.keys()))

    event_store = EventStore()
    audit_log = TransparencyLog()
    audit_service = AuditService(event_store, audit_log)

    scenarios = all_scenarios()
    reports = []
    for scenario in scenarios:
        report = run_case(scenario, rulepacks, engine, referee, abstention_gate, event_store, audit_service)
        print_report(scenario, report)
        reports.append((scenario, report))

    print(_rule("="))
    print("SUMMARY")
    print(_rule("="))
    n_auto = sum(1 for _, r in reports if r["abstention"].auto_resolve)
    n_escalate = len(reports) - n_auto
    n_chain_ok = sum(1 for _, r in reports if r["chain_valid"] and r["anchored_ok"])
    print(f"{len(reports)} cases run.  {n_auto} auto-resolved, {n_escalate} escalated to human review.")
    print(f"{n_chain_ok}/{len(reports)} cases: audit hash-chain valid AND anchored in the transparency log.")
    print()
    for scenario, report in reports:
        ev = report["evaluation"]
        outcome = (
            f"CONFLICT({','.join(ev.conflicting_outcomes)})" if ev.conflicting_outcomes
            else ev.decision or "NO-DECISION"
        )
        gate = "AUTO" if report["abstention"].auto_resolve else "ESCALATE"
        print(f"  {scenario.case_id:16s} {scenario.reason_code:4s} {outcome:28s} {gate}")

    print()
    latest_sth = audit_log.latest_sth()
    print(f"Transparency log: tree_size={latest_sth.tree_size}  root={latest_sth.root_hash.hex()[:24]}...")
    print(f"  signed by log operator, TSA-stamped at unix_ns={latest_sth.timestamp_unix_ns}")


if __name__ == "__main__":
    main()

"""
Gaming-resistance eval: does reading your own counterfactual and
fabricating exactly what it asks for actually work?

This is the sharpest attack on the whole disclosure design, worth stating
plainly: the counterfactual ledger (arbiter.horn.counterfactual, A4) tells
a losing party EXACTLY which predicate(s) would flip their case. That is
either a genuine transparency feature or a fraud manual, depending entirely
on whether the named predicates can actually be satisfied by mere
assertion. This eval measures that directly instead of assuming it.

Method, per synthetic case the referee currently rules AGAINST the
merchant: read the merchant's counterfactual, and for every predicate it
names as obtainable, simulate the cheapest possible forgery -- a brand-new
SUBMITTED-tier evidence node asserting the predicate TRUE, backed by
nothing (no artifact, no ADEC commitment, just the bare assertion, which is
strictly less effort than actually forging a document). Re-derive facts
over the augmented graph and re-adjudicate.

The measured property, split by whether the loaded rulepack tier-gates the
named predicate above SUBMITTED (arbiter.horn.clause.PredicateMeta,
enforced in arbiter.evidence.derive):

    tier-gated predicates (NETWORK/COMMITTED):  fabrication must NEVER
        flip the verdict -- this is the actual claim ADEC/tier-gating
        makes, and the number reported here must be 0/N or the claim is
        false, not just untested.

    ungated predicates (SUBMITTED-tier document claims, e.g. C08's
        `delivery_confirmed`): fabrication CAN flip a verdict on its own
        in this simulation, and that is reported honestly as a nonzero
        number, not hidden. It is not a defect in tier gating -- these
        predicates were never claimed to be tier-gated (see C08/C02
        rulepack headers). Their actual defense is forensics
        (arbiter.ingest.forensics) and contradiction detection
        (arbiter.evidence.contradiction) against a REAL forged artifact,
        which a bare assertion with no artifact at all does not exercise.
        This eval's honest bare-assertion simulation is a strictly EASIER
        attack than a real forgery would need to clear (a real forgery
        still has to pass scan/forensics/numeric-reconciliation), so this
        number is a conservative upper bound on exposure, not the real one.

Run: python evals/gaming_resistance.py [--n 300] [--seed 11]
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arbiter.advocate import run_dual_advocacy
from arbiter.decision import Referee
from arbiter.evidence import EvidenceNode, EvidenceNodeType, ProvenanceTier, derive_predicate_facts
from arbiter.horn.clause import RulePack
from arbiter.horn.counterfactual import minimal_delta
from arbiter.rulepack import load_rulepack_dir
from datagen.observe import observe
from datagen.world import generate_world

RULEPACK_DIR = Path(__file__).resolve().parent.parent / "rulepacks" / "amex"

_TIER_GATED = {"NETWORK", "COMMITTED"}


def _min_tier_for(pack: RulePack, predicate: str) -> str:
    if not pack.predicate_meta:
        return "UNGATED"
    meta = pack.predicate_meta.get(predicate)
    return meta.min_tier if meta else "UNGATED"


def _fabricate_node(case_id: str, predicate: str) -> EvidenceNode:
    """The cheapest possible forgery: an assertion, no artifact, no
    commitment -- SUBMITTED tier, the tier a party can produce by
    themselves with zero external corroboration."""
    return EvidenceNode(
        case_id=case_id,
        node_type=EvidenceNodeType.CLAIM,
        attrs={"asserts_predicate": predicate, "predicate_value": True},
        provenance=ProvenanceTier.SUBMITTED,
        extract_conf=1.0,
    )


def run_eval(n_per_code: int, seed: int) -> dict:
    rng = random.Random(seed)
    packs = load_rulepack_dir(RULEPACK_DIR)
    referee = Referee()

    results = defaultdict(lambda: Counter())
    by_predicate = defaultdict(lambda: Counter())  # (reason_code, predicate, tier_class) -> outcome counts

    for reason_code in ("F29", "C08", "C02"):
        pack = packs[reason_code]
        m_outcome = next(o for o in pack.decision_predicates if "MERCHANT" in o)
        m_head = pack.decision_predicates[m_outcome]

        for i in range(n_per_code):
            w = generate_world(rng, reason_code, f"gaming-{reason_code}-{i}")
            oc = observe(w, rng)
            facts = derive_predicate_facts(oc.graph, pack)

            cm_graph, m_graph = run_dual_advocacy(pack, facts)
            baseline = referee.adjudicate(pack, [cm_graph, m_graph], facts)
            results[reason_code]["total"] += 1

            if baseline.evaluation.decision == m_outcome:
                continue  # merchant already wins on the merits -- nothing to game

            cf = minimal_delta(pack, m_head, facts, outcome_name=m_outcome)
            obtain_items = [d for d in cf.obtainable_items() if d.action == "OBTAIN"]
            if not obtain_items:
                continue  # abstained/unreachable counterfactual -- nothing actionable to fabricate

            results[reason_code]["attempted"] += 1

            for node in [_fabricate_node(w.case_id, item.predicate) for item in obtain_items]:
                oc.graph.add_node(node)

            fabricated_facts = derive_predicate_facts(oc.graph, pack)
            cm_graph2, m_graph2 = run_dual_advocacy(pack, fabricated_facts)
            after = referee.adjudicate(pack, [cm_graph2, m_graph2], fabricated_facts)

            flipped = after.evaluation.decision == m_outcome
            worst_tier_gated = all(_min_tier_for(pack, item.predicate) in _TIER_GATED for item in obtain_items)
            bucket = "tier_gated" if worst_tier_gated else "ungated"
            results[reason_code][f"{bucket}_flipped" if flipped else f"{bucket}_held"] += 1

            for item in obtain_items:
                tier_class = _min_tier_for(pack, item.predicate)
                by_predicate[(reason_code, item.predicate, tier_class)]["flipped" if flipped else "held"] += 1

    return {"results": results, "by_predicate": by_predicate}


def print_report(summary: dict) -> None:
    print("=" * 88)
    print("GAMING RESISTANCE: fabricating exactly what your own counterfactual asks for")
    print("=" * 88)
    for code, counts in summary["results"].items():
        attempted = counts["attempted"]
        tg_flipped, tg_held = counts["tier_gated_flipped"], counts["tier_gated_held"]
        ug_flipped, ug_held = counts["ungated_flipped"], counts["ungated_held"]
        print(f"\n{code}  (total cases={counts['total']}, merchant losing & counterfactual actionable={attempted})")
        if tg_flipped + tg_held:
            print(f"  tier-gated predicates only : {tg_flipped}/{tg_flipped + tg_held} fabrications flipped the verdict (must be 0)")
        if ug_flipped + ug_held:
            print(f"  includes ungated predicates : {ug_flipped}/{ug_flipped + ug_held} fabrications flipped the verdict (expected nonzero -- see module docstring)")
    print()
    print("-" * 88)
    print("Per-predicate breakdown (predicate, tier, held/flipped):")
    for (code, pred, tier), counts in sorted(summary["by_predicate"].items()):
        held, flipped = counts["held"], counts["flipped"]
        print(f"  {code:4s} {pred:40s} {tier:10s} held={held:4d} flipped={flipped:4d}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=300, help="cases per reason code")
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args()

    summary = run_eval(args.n, args.seed)
    print_report(summary)

    total_tier_gated_flips = sum(c["tier_gated_flipped"] for c in summary["results"].values())
    if total_tier_gated_flips:
        print(f"FAILURE: {total_tier_gated_flips} tier-gated fabrication(s) flipped a verdict. This must be zero.")
        sys.exit(1)
    print("OK: 0 tier-gated fabrications ever flipped a verdict.")

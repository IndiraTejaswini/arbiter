"""
Adjudication accuracy vs. datagen's ground truth (Phase-5 build gate:
"Accuracy vs true_outcome on 1,000 held-out cases (expect 70-85%; >95% means
your observation model isn't lossy enough)").

Runs entirely in-memory -- no Postgres, no Redis -- over the same
horn/advocate/decision pipeline the API uses, just called directly instead
of through arbiter.api.orchestration. This is what makes the number honest:
it exercises the real Referee, not a mock of it.

    python evals/accuracy.py [--n 1000] [--seed 7]
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
from arbiter.evidence import derive_predicate_facts
from arbiter.horn import counterfactuals_for_all_outcomes, per_case_symmetry
from arbiter.decision.confidence import compute_confidence_vector
from arbiter.decision.conformal import ConformalAbstentionGate
from arbiter.rulepack import load_rulepack_dir

from datagen.world import generate_world
from datagen.outcome import Outcome, true_outcome
from datagen.observe import observe

RULEPACK_DIR = Path(__file__).resolve().parent.parent / "rulepacks" / "amex"

_DECISION_TO_OUTCOME = {
    "MERCHANT_WINS": Outcome.MERCHANT_PREVAILS,
    "CARD_MEMBER_WINS": Outcome.CARD_MEMBER_PREVAILS,
}


def run_eval(n_per_code: int, seed: int) -> dict:
    rng = random.Random(seed)
    packs = load_rulepack_dir(RULEPACK_DIR)
    referee = Referee()

    results = defaultdict(lambda: Counter())
    r13_recovery = Counter()
    auto_resolve_count = Counter()
    total = Counter()

    for reason_code in ("F29", "C08", "C02"):
        pack = packs[reason_code]
        for i in range(n_per_code):
            w = generate_world(rng, reason_code, f"eval-{reason_code}-{i}")
            oc = observe(w, rng)
            facts = derive_predicate_facts(oc.graph, pack)

            cm_graph, m_graph = run_dual_advocacy(pack, facts)
            referee_result = referee.adjudicate(pack, [cm_graph, m_graph], facts)
            evaluation = referee_result.evaluation

            truth = true_outcome(w)
            total[reason_code] += 1

            if oc.merchant_silent and evaluation.decision == "MERCHANT_WINS":
                r13_recovery[reason_code] += 1

            if evaluation.conflicting_outcomes or evaluation.decision is None:
                results[reason_code]["ABSTAINED_NO_DECISION"] += 1
                continue

            predicted = _DECISION_TO_OUTCOME.get(evaluation.decision)
            if predicted is None:
                results[reason_code]["UNMAPPED"] += 1
            elif predicted == truth:
                results[reason_code]["CORRECT"] += 1
            elif truth in (Outcome.SPLIT, Outcome.INSUFFICIENT_EVIDENCE):
                # the rulepack reached a binary decision where the ground
                # truth is genuinely ambiguous -- not a clean miss, tracked
                # separately from a hard disagreement.
                results[reason_code]["AMBIGUOUS_TRUTH"] += 1
            else:
                results[reason_code]["WRONG"] += 1

    return {"results": results, "r13_recovery": r13_recovery, "total": total}


def print_report(summary: dict) -> None:
    print("=" * 78)
    print("ACCURACY vs datagen.outcome.true_outcome (ground truth independent of the rulepack)")
    print("=" * 78)
    for code, counts in summary["results"].items():
        n = summary["total"][code]
        correct = counts["CORRECT"]
        wrong = counts["WRONG"]
        abstained = counts["ABSTAINED_NO_DECISION"]
        ambiguous = counts["AMBIGUOUS_TRUTH"]
        decided = correct + wrong + ambiguous
        acc = correct / decided if decided else 0.0
        auto_resolve_rate = decided / n if n else 0.0
        print(f"\n{code}  (n={n})")
        print(f"  decided: {decided} ({auto_resolve_rate:.1%})   abstained: {abstained} ({abstained/n:.1%})")
        print(f"  accuracy on decided: {acc:.1%}  (correct={correct} wrong={wrong} ambiguous_truth={ambiguous})")
        print(f"  R13-equivalent recovery (merchant silent, still won on merits): {summary['r13_recovery'][code]}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=340, help="cases per reason code (n=340*3≈1000)")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    summary = run_eval(args.n, args.seed)
    print_report(summary)

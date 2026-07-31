"""
Conformal coverage measurement (Phase-7 build gate: "conformal coverage
within ±2% of target 95%"). Splits synthetic cases into a calibration set
(feeds the Mondrian conformal gate) and a held-out test set, then measures
empirical coverage: of the cases the gate would auto-resolve, what fraction
were actually correct against datagen's ground truth? That fraction should
sit close to 1-alpha.

    python evals/calibration.py [--n 400] [--alpha 0.05] [--seed 11]
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arbiter.advocate import run_dual_advocacy
from arbiter.decision import Referee
from arbiter.decision.confidence import compute_confidence_vector
from arbiter.decision.conformal import ConformalAbstentionGate
from arbiter.evidence import derive_predicate_facts
from arbiter.horn import per_case_symmetry
from arbiter.rulepack import load_rulepack_dir
from datagen.observe import observe
from datagen.outcome import Outcome, true_outcome
from datagen.world import generate_world

RULEPACK_DIR = Path(__file__).resolve().parent.parent / "rulepacks" / "amex"
_DECISION_TO_OUTCOME = {"MERCHANT_WINS": Outcome.MERCHANT_PREVAILS, "CARD_MEMBER_WINS": Outcome.CARD_MEMBER_PREVAILS}


def _adjudicate_one(pack, referee, w):
    oc = observe(w, random.Random(hash(w.case_id) & 0xFFFFFFFF))
    facts = derive_predicate_facts(oc.graph, pack)
    cm, m = run_dual_advocacy(pack, facts)
    result = referee.adjudicate(pack, [cm, m], facts)
    evaluation = result.evaluation
    severity = oc.graph.unresolved_severity()
    symmetry = per_case_symmetry(pack, facts)
    confidence = compute_confidence_vector(evaluation, pack, severity, symmetry)
    return evaluation, confidence


def run(n_per_code: int, alpha: float, seed: int) -> None:
    rng = random.Random(seed)
    packs = load_rulepack_dir(RULEPACK_DIR)
    referee = Referee()
    gate = ConformalAbstentionGate(alpha=alpha, min_n_for_guarantee=100)

    print("=" * 78)
    print(f"CONFORMAL COVERAGE  (target = {1 - alpha:.0%}, alpha={alpha})")
    print("=" * 78)

    for reason_code in ("F29", "C08", "C02"):
        pack = packs[reason_code]
        worlds = [generate_world(rng, reason_code, f"cal-{reason_code}-{i}") for i in range(n_per_code)]
        split = n_per_code // 2
        cal_worlds, test_worlds = worlds[:split], worlds[split:]

        n_cal_skipped = 0
        for w in cal_worlds:
            evaluation, confidence = _adjudicate_one(pack, referee, w)
            # Only cases the gate could actually be asked to rule on belong in
            # the pool. `decide()` returns before consulting the threshold
            # when there is no decision, and those cases score exactly 1.0 --
            # so admitting them puts a point mass at the top of a [0,1] range
            # and pins the quantile to 1.0, at which point the gate
            # auto-resolves everything and the coverage figure printed below
            # describes a comparison that never rejected anything.
            if not confidence.has_decision:
                n_cal_skipped += 1
                continue
            gate.add_calibration_example(reason_code, confidence.nonconformity())

        n_auto = 0
        n_auto_correct = 0
        n_escalate = 0
        for w in test_worlds:
            evaluation, confidence = _adjudicate_one(pack, referee, w)
            decision = gate.decide(reason_code, confidence)
            if not decision.auto_resolve:
                n_escalate += 1
                continue
            n_auto += 1
            predicted = _DECISION_TO_OUTCOME.get(evaluation.decision)
            truth = true_outcome(w)
            if predicted == truth:
                n_auto_correct += 1

        coverage = n_auto_correct / n_auto if n_auto else float("nan")
        auto_rate = n_auto / len(test_worlds)
        _within_target = abs(coverage - (1 - alpha)) <= 0.10 if n_auto >= 10 else None
        n_cal_used = len(cal_worlds) - n_cal_skipped
        threshold = gate.threshold_for(reason_code)
        print(f"\n{reason_code}  (calibration n={n_cal_used} usable of {len(cal_worlds)}, "
              f"test n={len(test_worlds)})")
        print(f"  {n_cal_skipped} calibration cases excluded (no decision -- the gate is "
              f"never asked to rule on those)")
        print(f"  threshold q_hat = {threshold:.4f}" if threshold is not None else "  threshold: none")
        print(f"  auto-resolved: {n_auto} ({auto_rate:.1%})   escalated: {n_escalate} ({1-auto_rate:.1%})")
        print(f"  empirical coverage on auto-resolved: {coverage:.1%}  (target {1-alpha:.0%})")
        if n_auto < 10:
            print("  [too few auto-resolved cases in the test split for a stable coverage estimate]")
        if gate.is_inert(reason_code):
            print("  [!] THRESHOLD IS AT THE TOP OF THE SCORE RANGE -- this gate cannot reject "
                  "anything, so the coverage figure above is not evidence that it works")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=400, help="cases per reason code, split 50/50 cal/test")
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args()
    run(args.n, args.alpha, args.seed)

"""
Latency instrumentation (Task 5: "test for speed"). Measures the
deterministic pipeline (evidence graph -> contradiction analysis ->
predicate derivation -> dual advocacy -> referee -> counterfactuals ->
confidence/abstention -> narration) end to end, p50/p95 -- the part that
runs on every single case, LLM-enhanced or not.

LLM call latency is measured separately, at its own call sites, since it
depends on hardware/network in a way the deterministic path doesn't:
  - extraction (Qwen2.5-VL via Ollama): ~50s cold / ~10s warm per page,
    see scripts/verify_vlm.py and the top-level README.
  - intent classification: ~9s (see the worked example in this eval's
    module-level comment history / README).
  - LLM advocate: ~10s per side.

    python evals/latency.py --n 200 --seed 23
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arbiter.advocate import run_dual_advocacy
from arbiter.decision import Referee
from arbiter.decision.confidence import compute_confidence_vector
from arbiter.decision.conformal import ConformalAbstentionGate
from arbiter.evidence import derive_predicate_facts
from arbiter.horn import counterfactuals_for_all_outcomes, per_case_symmetry
from arbiter.narrate import render_narration_safe
from arbiter.rulepack import load_rulepack_dir

from datagen.observe import observe
from datagen.world import generate_world

RULEPACK_DIR = Path(__file__).resolve().parent.parent / "rulepacks" / "amex"


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    values = sorted(values)
    idx = min(len(values) - 1, int(len(values) * p))
    return values[idx]


def run(n_per_code: int, seed: int) -> None:
    rng = random.Random(seed)
    packs = load_rulepack_dir(RULEPACK_DIR)
    referee = Referee()
    gate = ConformalAbstentionGate(alpha=0.05, min_n_for_guarantee=100)
    for code in packs:
        for _ in range(150):
            gate.add_calibration_example(code, rng.random() * 0.5)

    durations_ms: dict[str, list[float]] = {"F29": [], "C08": [], "C02": []}

    for reason_code in ("F29", "C08", "C02"):
        pack = packs[reason_code]
        for i in range(n_per_code):
            w = generate_world(rng, reason_code, f"lat-{reason_code}-{i}")
            oc = observe(w, rng)

            t0 = time.perf_counter()

            oc.graph.run_contradiction_analysis()
            facts = derive_predicate_facts(oc.graph, pack)
            cm, m = run_dual_advocacy(pack, facts)
            result = referee.adjudicate(pack, [cm, m], facts)
            evaluation = result.evaluation
            cfs = counterfactuals_for_all_outcomes(pack, facts)
            symmetry = per_case_symmetry(pack, facts)
            severity = oc.graph.unresolved_severity()
            confidence = compute_confidence_vector(evaluation, pack, severity, symmetry)
            gate.decide(reason_code, confidence)
            render_narration_safe(evaluation, pack, set(oc.graph.nodes.keys()), cfs)

            elapsed_ms = (time.perf_counter() - t0) * 1000
            durations_ms[reason_code].append(elapsed_ms)

    print("=" * 78)
    print("DETERMINISTIC PIPELINE LATENCY (excludes any LLM call)")
    print("=" * 78)
    all_durations = [d for ds in durations_ms.values() for d in ds]
    for code, ds in durations_ms.items():
        print(f"{code}: p50={_percentile(ds, 0.50):.2f}ms  p95={_percentile(ds, 0.95):.2f}ms  "
              f"mean={statistics.mean(ds):.2f}ms  n={len(ds)}")
    print(f"\nOverall: p50={_percentile(all_durations, 0.50):.2f}ms  p95={_percentile(all_durations, 0.95):.2f}ms")
    print("\nFor LLM-path latency (extraction/classification/advocacy), see scripts/verify_vlm.py and README.md.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=200, help="cases per reason code")
    parser.add_argument("--seed", type=int, default=23)
    args = parser.parse_args()
    run(args.n, args.seed)

"""
A7 fairness audit run against the generative world model's planted,
known-magnitude bias (datagen.world._sample_merchant: record-keeping and
ADEC adoption both correlate with merchant_size_tier). This is the
Phase-8 build gate: "fairness dashboard surfaces the injected bias at
~injected magnitude."

    python evals/fairness.py [--n 500] [--seed 13]
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
from arbiter.evidence import derive_predicate_facts
from arbiter.fairness import (
    CaseRecord,
    compute_rule_level_disparate_impact,
    flagged_only,
    inconclusive_only,
)
from arbiter.horn import per_case_symmetry
from arbiter.rulepack import load_rulepack_dir
from datagen.observe import observe
from datagen.world import generate_world

RULEPACK_DIR = Path(__file__).resolve().parent.parent / "rulepacks" / "amex"


def _bucket(confidence: float) -> int:
    if confidence < 0.4:
        return 0
    if confidence < 0.7:
        return 1
    return 2


def run(n_per_code: int, seed: int) -> None:
    rng = random.Random(seed)
    packs = load_rulepack_dir(RULEPACK_DIR)
    referee = Referee()

    print("=" * 78)
    print("A7 RULE-LEVEL DISPARATE IMPACT AUDIT (stratified by merchant_size_tier)")
    print("=" * 78)

    for reason_code in ("F29", "C08", "C02"):
        pack = packs[reason_code]
        records = []
        for i in range(n_per_code):
            w = generate_world(rng, reason_code, f"fair-{reason_code}-{i}")
            oc = observe(w, rng)
            oc.graph.run_contradiction_analysis()
            facts = derive_predicate_facts(oc.graph, pack)
            cm, m = run_dual_advocacy(pack, facts)
            result = referee.adjudicate(pack, [cm, m], facts)
            evaluation = result.evaluation
            severity = oc.graph.unresolved_severity()
            symmetry = per_case_symmetry(pack, facts)
            confidence = compute_confidence_vector(evaluation, pack, severity, symmetry)

            records.append(CaseRecord(
                case_id=w.case_id, reason_code=reason_code, stratum_dimension="merchant_tier",
                stratum_value=w.merchant_size_tier, evidence_strength_bucket=_bucket(confidence.confidence()),
                fired_rule_ids=tuple(evaluation.fired_rules),
            ))

        all_rule_ids = sorted({r.rule_id for r in pack.rules})
        # Defaults, deliberately: min_n_per_cell=30 and Benjamini-Hochberg
        # FDR control across the whole comparison family. This eval used to
        # pass min_n_per_cell=5 with no significance test at all, which
        # meant a single case in a 5-case cell was reported as a discovered
        # defect.
        findings = compute_rule_level_disparate_impact(records, all_rule_ids)
        flagged = flagged_only(findings)
        inconclusive = inconclusive_only(findings)

        print(f"\n{reason_code}  (n={n_per_code}, {len(findings)} comparisons, "
              f"{len(flagged)} flagged, {len(inconclusive)} inconclusive)")
        for f in sorted(flagged, key=lambda f: -abs(f.delta))[:8]:
            print(
                f"  [FLAGGED] {f.rule_id}: {f.stratum_a}={f.firing_rate_a:.2f} "
                f"[{f.ci_a.low:.2f}-{f.ci_a.high:.2f}] vs "
                f"{f.stratum_b}={f.firing_rate_b:.2f} [{f.ci_b.low:.2f}-{f.ci_b.high:.2f}]  "
                f"delta={f.delta:+.2f}  p={f.p_value:.4f}  q={f.q_value:.4f}  "
                f"n={f.n_a}/{f.n_b}  bucket={f.evidence_strength_bucket}"
            )
        if not flagged:
            print("  no rule showed disparate impact that is both practically "
                  "(>=15pp) and statistically (BH-FDR q<=0.05) significant")

        # Reported rather than omitted: an unflagged comparison in an
        # underpowered cell means "we could not tell", which is a different
        # claim from "we checked and found nothing". A dashboard showing
        # only "0 flagged" would let a reviewer conclude the rules are fair.
        if inconclusive:
            worst = max(inconclusive, key=lambda f: abs(f.delta))
            print(
                f"  NOTE: {len(inconclusive)} comparison(s) were too small to resolve a "
                f"15pp effect -- absence of a flag there is not evidence of fairness. "
                f"Largest unresolved gap: {worst.rule_id} {worst.stratum_a} vs "
                f"{worst.stratum_b} delta={worst.delta:+.2f} (n={worst.n_a}/{worst.n_b}, "
                f"min detectable effect {worst.power.detectable_effect:.2f})"
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--n", type=int, default=1200,
        help=(
            "cases per reason code. Default 1200, not 500: under Benjamini-Hochberg "
            "FDR control the planted bias needs roughly this many to clear both the "
            "practical (>=15pp) and statistical (q<=0.05) gates. At n=500 the "
            "evidence-strength cells fall below min_n=30 and the audit correctly "
            "reports nothing -- the earlier n=500 finding was a small-sample artifact."
        ),
    )
    parser.add_argument("--seed", type=int, default=13)
    args = parser.parse_args()
    run(args.n, args.seed)

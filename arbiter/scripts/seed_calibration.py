"""
Populate `calibration_sample` with REAL nonconformity scores.

This replaces `arbiter.main._seed_calibration`, which invented 150 Gaussian
random scores per reason code at every process boot so the conformal gate
would have a threshold to compare against. Those numbers bore no
relationship to the pipeline's actual nonconformity distribution: the
resulting threshold (q_hat = 0.688 at alpha=0.05) was loose enough to
auto-resolve essentially everything, including cases carrying a CRITICAL
unresolved contradiction. The gate reported a distribution-free coverage
guarantee it did not have.

What this script does instead is what `evals/calibration.py` already did
correctly, persisted: run the REAL adjudication pipeline (evidence graph ->
contradiction analysis -> derivation -> dual advocacy -> referee ->
confidence vector) over held-out generated worlds, and write the resulting
nonconformity scores. Split-conformal validity requires the calibration set
be exchangeable with what the gate will see in deployment -- scores
produced by the same pipeline over the same world model satisfy that;
invented Gaussians do not.

`source='SYNTHETIC'` is honest labelling, not a loophole: the schema
distinguishes it from `'ANALYST'` precisely so a reviewer can ask what a
threshold is standing on. As real analyst adjudications accumulate they
join the same pool.

    python scripts/seed_calibration.py [--n 400] [--seed 11] [--replace]

Requires Postgres up and `alembic upgrade head`.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func, select

from arbiter.advocate import run_dual_advocacy
from arbiter.config import get_settings
from arbiter.db import models as m
from arbiter.db.session import session_scope
from arbiter.decision import Referee, compute_confidence_vector
from arbiter.evidence import derive_predicate_facts
from arbiter.horn import per_case_symmetry
from arbiter.rulepack.loader import load_rulepack_dir
from datagen.observe import observe
from datagen.outcome import true_outcome
from datagen.world import generate_world

_OUTCOME_TO_ENUM = {
    "CARD_MEMBER_WINS": m.OutcomeEnum.CARD_MEMBER_PREVAILS,
    "MERCHANT_WINS": m.OutcomeEnum.MERCHANT_PREVAILS,
    "SPLIT": m.OutcomeEnum.SPLIT,
}


def _score_one(pack, world, rng) -> tuple[float, m.OutcomeEnum, bool]:
    """Run the real pipeline over one generated case and return
    (nonconformity, ground-truth outcome, has_decision)."""
    oc = observe(world, rng)
    oc.graph.run_contradiction_analysis()
    facts = derive_predicate_facts(oc.graph, pack)
    cm, mg = run_dual_advocacy(pack, facts)
    result = Referee().adjudicate(pack, [cm, mg], facts)
    symmetry = per_case_symmetry(pack, facts)
    confidence = compute_confidence_vector(
        result.evaluation, pack, oc.graph.unresolved_severity(), symmetry
    )
    truth = true_outcome(world)
    truth_enum = _OUTCOME_TO_ENUM.get(
        getattr(truth, "value", str(truth)), m.OutcomeEnum.INSUFFICIENT_EVIDENCE
    )
    return confidence.nonconformity(), truth_enum, confidence.has_decision


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=400, help="calibration cases per reason code")
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument(
        "--replace", action="store_true",
        help="delete existing SYNTHETIC samples first (ANALYST samples are never touched)",
    )
    args = parser.parse_args()

    rng = random.Random(args.seed)
    packs = load_rulepack_dir(get_settings().rulepack_dir)

    with session_scope() as session:
        if args.replace:
            # Analyst-sourced samples are real human adjudications and are
            # never discarded by a re-seed.
            session.execute(
                delete(m.CalibrationSample).where(m.CalibrationSample.source == "SYNTHETIC")
            )

        for reason_code, pack in sorted(packs.items()):
            kept = skipped = 0
            attempts = 0
            # Keep generating until `n` cases that the gate could actually be
            # asked to rule on have been banked, rather than generating `n`
            # cases and banking whatever comes out. The cap stops a
            # pathological rulepack from looping forever.
            while kept < args.n and attempts < args.n * 20:
                attempts += 1
                world = generate_world(rng, reason_code, f"cal-{reason_code}-{attempts}")
                score, truth_enum, has_decision = _score_one(pack, world, rng)

                # THE filter, and the reason this script had to change.
                #
                # `ConformalAbstentionGate.decide` returns before the
                # threshold is ever consulted when the referee reached no
                # decision. So the population the threshold is applied to is
                # "cases with a decision", and split conformal is valid only
                # if the calibration set is exchangeable with THAT population.
                #
                # A no-decision case scores exactly 1.0, and nonconformity is
                # bounded by 1.0. Measured on this generator, 16-47% of cases
                # per reason code reach no decision -- far more than
                # alpha=0.05 -- which dragged the 95th percentile to 1.0 for
                # every shipped reason code. At threshold 1.0, `score <=
                # threshold` is universally true and the gate auto-resolved
                # every case it was asked about while still reporting a 95%
                # coverage guarantee. Excluding fabricated Gaussians (the
                # defect this script was written to fix) removed one route to
                # that state; this was the other one, and it came from the
                # real pipeline.
                if not has_decision:
                    skipped += 1
                    continue

                kept += 1
                session.add(
                    m.CalibrationSample(
                        reason_code=reason_code,
                        features={
                            "generator": "datagen.world",
                            "case_tag": f"cal-{reason_code}-{attempts}",
                            "has_decision": True,
                        },
                        score=score,
                        true_outcome=truth_enum,
                        source="SYNTHETIC",
                        # Explicit rather than left NULL. These are not
                        # sampled from a review queue -- every generated case
                        # enters the pool -- so the inverse-probability weight
                        # is exactly 1.0. Leaving it NULL got the same weight
                        # via `calibration_weight`'s legacy fallback, but by
                        # accident rather than by statement.
                        selection_probability=1.0,
                        is_audit_sample=False,
                    )
                )
            print(
                f"  {reason_code}: {kept} calibration samples "
                f"({skipped} skipped -- the referee reached no decision, so the gate "
                f"would never be asked to rule on them)"
            )

    with session_scope() as session:
        rows = session.execute(
            select(m.CalibrationSample.reason_code, func.count())
            .group_by(m.CalibrationSample.reason_code)
        ).all()

    print("\ncalibration_sample now holds:")
    for code, n in sorted(rows):
        print(f"  {code}: n={n}")
    print(
        "\nRestart the API to load these. Reason codes below "
        f"min_n_for_guarantee={get_settings().conformal_min_n} will escalate every case "
        "rather than auto-resolve against an unreliable threshold."
    )


if __name__ == "__main__":
    main()

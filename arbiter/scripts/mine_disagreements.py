"""
Analyst-disagreement mining, DB-facing entry point. The pure algorithm
lives in arbiter.decision.mining (no DB import there, by design -- layering
stays clean); this script is the thin, impure glue that reads
calibration_sample (source='ANALYST') rows, and prints whatever
ProposedRule candidates the mining turns up. Nothing here writes anything
-- this is read-only analysis, and every proposal is exactly that: a
proposal for a human to read, evaluate against the real cases it cites,
and -- only if they agree -- turn into a new rule in rulepacks/amex/*.yaml
by hand.

    python scripts/mine_disagreements.py [--min-support 3]

Requires a running Postgres with review-decision activity already
recorded (arbiter.api.routes.disputes.review_decision).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from arbiter.db import models as m
from arbiter.db.session import session_scope
from arbiter.decision.mining import ReviewedCase, mine_proposed_rules, rule_bodies_by_outcome
from arbiter.horn.proof import FactStatus
from arbiter.rulepack import load_rulepack_dir

RULEPACK_DIR = Path(__file__).resolve().parent.parent / "rulepacks" / "amex"

# arbiter.db.models.OutcomeEnum (what a reviewer submits, "..._PREVAILS")
# uses different naming than a rulepack's decision_predicates keys (what
# arbiter.decision.mining.rule_bodies_by_outcome returns, "..._WINS") --
# translated here so a mined proposal's `outcome` field matches the label
# a human would actually use when adding it to a rulepack YAML.
_OUTCOME_ENUM_TO_RULEPACK_OUTCOME = {
    "CARD_MEMBER_PREVAILS": "CARD_MEMBER_WINS",
    "MERCHANT_PREVAILS": "MERCHANT_WINS",
    "SPLIT": "SPLIT",
}


def _load_reviewed_cases(session) -> list[ReviewedCase]:
    # arbiter.api.routes.disputes.review_decision carries case_id and the
    # decision's predicate map straight into calibration_sample.features
    # specifically so this query doesn't need an unreliable cross-table
    # join -- calibration_sample otherwise has no case_id column (it's a
    # stratified pool for conformal calibration, not a case-keyed log).
    samples = session.execute(
        select(m.CalibrationSample).where(m.CalibrationSample.source == "ANALYST")
    ).scalars().all()

    reviewed: list[ReviewedCase] = []
    for sample in samples:
        case_id = sample.features.get("case_id")
        predicates = sample.features.get("predicates")
        if not case_id or not predicates:
            continue  # samples recorded before this field existed -- skip, don't guess
        outcome = _OUTCOME_ENUM_TO_RULEPACK_OUTCOME.get(sample.true_outcome.value)
        if outcome is None:
            continue  # e.g. INSUFFICIENT_EVIDENCE -- not a rulepack decision_predicates outcome to mine a rule for
        true_predicates = frozenset(p for p, status in predicates.items() if status == FactStatus.TRUE.value)
        reviewed.append(ReviewedCase(
            case_id=case_id, reason_code=sample.reason_code,
            true_predicates=true_predicates, analyst_outcome=outcome,
        ))
    return reviewed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-support", type=int, default=3)
    args = parser.parse_args()

    packs = load_rulepack_dir(RULEPACK_DIR)
    known_rule_bodies = {code: rule_bodies_by_outcome(pack) for code, pack in packs.items()}

    with session_scope() as session:
        reviewed = _load_reviewed_cases(session)

    proposals = mine_proposed_rules(reviewed, known_rule_bodies, min_support=args.min_support)

    print(f"Reviewed (settled, previously-abstained) cases considered: {len(reviewed)}")
    if not proposals:
        print("No recurring, uncovered predicate pattern met the support threshold. Nothing to propose.")
        return

    print(f"\n{len(proposals)} proposed rule candidate(s) -- review and hand-add to the rulepack YAML if you agree:\n")
    for p in proposals:
        print(f"  [{p.reason_code}] {p.outcome} <- {' AND '.join(sorted(p.body))}")
        print(f"    support: {p.support_count} case(s): {', '.join(p.supporting_case_ids)}")


if __name__ == "__main__":
    main()

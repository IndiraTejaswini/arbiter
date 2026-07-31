"""
Analyst-disagreement mining -- data only, never weights.

A reviewer's decision on an escalated/abstained case
(arbiter.api.routes.disputes.review_decision, which writes a
calibration_sample with source='ANALYST') is signal about more than
calibration: if the SAME combination of TRUE predicates recurs across
several abstained cases and the analyst consistently resolves them the
same way, that combination is a candidate new rule the rulepack doesn't
yet encode -- exactly the gap between "the referee couldn't decide" and "a
human, given this evidence, always decides the same way."

This module only ever proposes. A ProposedRule is not eligible to run
anything; adding it to a rulepack means a human edits
rulepacks/amex/*.yaml and re-deploys a new content-addressed version
(arbiter.rulepack), exactly like any other rule change. There is no code
path from this module's output to a live decision -- recurring analyst
judgment becomes auditable, reviewable DATA a person turns into a rule,
never a weight a model updates on its own. This is the same C1 discipline
("rules decide, models never decide") applied to how the rules themselves
get better over time, not just to how a single case is adjudicated.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Tuple

from arbiter.horn.clause import RulePack


@dataclass(frozen=True)
class ReviewedCase:
    """One analyst-reviewed, previously-abstained case -- this module's
    only input shape. `true_predicates` is the TRUE subset of a decision's
    persisted predicate map (arbiter.db.models.DecisionRow.predicates);
    `analyst_outcome` is the outcome name the reviewer chose
    (arbiter.api.routes.disputes.ReviewDecisionRequest.outcome)."""

    case_id: str
    reason_code: str
    true_predicates: FrozenSet[str]
    analyst_outcome: str


@dataclass(frozen=True)
class ProposedRule:
    reason_code: str
    outcome: str
    body: FrozenSet[str]
    support_count: int
    supporting_case_ids: Tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "reason_code": self.reason_code,
            "outcome": self.outcome,
            "body": sorted(self.body),
            "support_count": self.support_count,
            "supporting_case_ids": list(self.supporting_case_ids),
        }


def rule_bodies_by_outcome(rulepack: RulePack) -> Dict[str, List[FrozenSet[str]]]:
    """Positive-literal bodies of every existing rule in `rulepack`, keyed
    by OUTCOME NAME (not head predicate) so it lines up with
    ReviewedCase.analyst_outcome. Feeds `known_rule_bodies` below."""
    head_to_outcome = {head: outcome for outcome, head in rulepack.decision_predicates.items()}
    out: Dict[str, List[FrozenSet[str]]] = defaultdict(list)
    for rule in rulepack.rules:
        outcome = head_to_outcome.get(rule.head)
        if outcome is None:
            continue
        body = frozenset(lit.predicate for lit in rule.body if not lit.negated)
        out[outcome].append(body)
    return dict(out)


def mine_proposed_rules(
    reviewed_cases: List[ReviewedCase],
    known_rule_bodies: Dict[str, Dict[str, List[FrozenSet[str]]]],
    min_support: int = 3,
) -> List[ProposedRule]:
    """
    Groups reviewed cases by (reason_code, analyst_outcome, exact
    true_predicates set) and proposes a new rule for any group that:

      (a) recurs at least `min_support` times, AND
      (b) is not already a superset of some existing rule's body for that
          reason code's outcome -- if it were, the referee would already
          have derived that outcome and the case would never have reached
          an analyst in the first place. A recurring group that clears
          this check is a genuine gap, not the analyst re-deciding
          something a rule already covers.

    `known_rule_bodies` is keyed `reason_code -> outcome -> [bodies]`
    (build the inner mapping per rulepack with `rule_bodies_by_outcome`,
    then nest it under that rulepack's `reason_code`) -- NOT just
    `outcome -> [bodies]`, because outcome names like "CARD_MEMBER_WINS"
    are reused across reason codes/rulepacks and a flat mapping would
    silently compare a case's predicates against an unrelated rulepack's
    rules.

    Deterministic and sorted (support_count desc, then reason_code/
    outcome/body) so successive mining runs over the same data are
    diffable, not reordered by dict/set iteration.
    """
    groups: Dict[Tuple[str, str, FrozenSet[str]], List[str]] = defaultdict(list)
    for case in reviewed_cases:
        groups[(case.reason_code, case.analyst_outcome, case.true_predicates)].append(case.case_id)

    proposals: List[ProposedRule] = []
    for (reason_code, outcome, predicates), case_ids in groups.items():
        if len(case_ids) < min_support:
            continue
        existing_bodies = known_rule_bodies.get(reason_code, {}).get(outcome, [])
        if any(body <= predicates for body in existing_bodies):
            continue  # already covered by an existing rule -- not a gap
        proposals.append(ProposedRule(
            reason_code=reason_code, outcome=outcome, body=predicates,
            support_count=len(case_ids), supporting_case_ids=tuple(sorted(case_ids)),
        ))

    proposals.sort(key=lambda p: (-p.support_count, p.reason_code, p.outcome, sorted(p.body)))
    return proposals

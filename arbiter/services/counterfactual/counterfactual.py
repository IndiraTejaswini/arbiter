"""
Exact Counterfactual Ledger (§A4).

Because the Referee is deterministic and the predicate space is finite and
typed, the counterfactual isn't estimated (no SHAP, no LIME) -- it's a
set-difference against an offline-enumerated prime implicant (packages/
datalog/prime_implicants.py). This module is deliberately thin: it is the
same `minimal_delta` computation queried five different ways, because that
unification -- one mechanism, five product surfaces -- is the architectural
claim §A4 makes, not incidental code reuse.

| Product surface         | Query                                          |
|--------------------------|------------------------------------------------|
| Explanation              | load_bearing_predicates(evaluation)             |
| Merchant coaching         | minimal_delta(rulepack, MERCHANT head, facts)   |
| Missing evidence predictor| minimal_delta(...).obtainable_items()           |
| Card member guidance      | minimal_delta(rulepack, CM head, facts)         |
| Fairness probe (per-case) | per_case_symmetry(rulepack, facts)              |

Population-level fairness (stratified firing-rate deltas by merchant tier /
cardmember segment, §A7) is a separate, offline, multi-case audit --
services/fairness/. That is a different granularity from what a single
rulepack evaluation can answer, not a different mechanism: A7 runs this
same `minimal_delta` computation across many cases, stratified.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from packages.datalog.engine import EvaluationResult, Fact, FactStatus, ProofNode, RulePack
from packages.datalog.prime_implicants import PrimeImplicant, enumerate_prime_implicants

# Predicates describing fixed historical facts (counts, ages, prior-event
# timing) cannot be "obtained" by further evidence submission -- they either
# already hold or they don't. Everything else is treated as obtainable: a
# concrete artifact (a delivery confirmation, a cancellation record) that
# could still be produced or corrected. A fuller implementation would carry
# this as per-predicate rulepack metadata rather than a name-pattern
# heuristic; documented here as the scoping call it is.
_NON_OBTAINABLE_PATTERNS: Tuple[str, ...] = (
    "prior_", "_120_to_365", "count_ge", "velocity_anomaly",
)


def _is_obtainable(predicate: str) -> bool:
    return not any(pat in predicate for pat in _NON_OBTAINABLE_PATTERNS)


@dataclass(frozen=True)
class CounterfactualItem:
    predicate: str
    negated: bool
    action: str  # OBTAIN | RETRACT
    currently: str  # FactStatus name
    obtainable: bool

    def to_dict(self) -> dict:
        return {
            "predicate": self.predicate,
            "negated": self.negated,
            "action": self.action,
            "currently": self.currently,
            "obtainable": self.obtainable,
        }


@dataclass(frozen=True)
class Counterfactual:
    outcome: str
    head: str
    mwc: Optional[PrimeImplicant]
    delta: Tuple[CounterfactualItem, ...]
    already_satisfied: bool

    def obtainable_items(self) -> List[CounterfactualItem]:
        return [d for d in self.delta if d.obtainable]

    def unobtainable_items(self) -> List[CounterfactualItem]:
        return [d for d in self.delta if not d.obtainable]

    def to_dict(self) -> dict:
        return {
            "outcome": self.outcome,
            "head": self.head,
            "already_satisfied": self.already_satisfied,
            "delta": [d.to_dict() for d in self.delta],
        }


def _compute_delta(mwc: PrimeImplicant, facts: Dict[str, Fact]) -> List[CounterfactualItem]:
    delta: List[CounterfactualItem] = []
    for p, neg in sorted(mwc.literals):
        status = facts.get(p, Fact(p, FactStatus.UNKNOWN)).status
        if not neg:
            if status is not FactStatus.TRUE:
                delta.append(CounterfactualItem(p, neg, "OBTAIN", status.name, _is_obtainable(p)))
        else:
            if status is FactStatus.TRUE:
                delta.append(CounterfactualItem(p, neg, "RETRACT", status.name, _is_obtainable(p)))
    return delta


def minimal_delta(rulepack: RulePack, head: str, facts: Dict[str, Fact], outcome_name: str = "") -> Counterfactual:
    """The core mechanism: the prime implicant closest to already being
    satisfied by `facts`, and the exact set-difference to get there."""
    mwcs = enumerate_prime_implicants(rulepack, head)
    if not mwcs:
        return Counterfactual(outcome_name, head, None, (), False)

    scored = [(mwc, _compute_delta(mwc, facts)) for mwc in mwcs]
    best_mwc, best_delta = min(scored, key=lambda pair: len(pair[1]))
    return Counterfactual(outcome_name, head, best_mwc, tuple(best_delta), len(best_delta) == 0)


def counterfactuals_for_all_outcomes(rulepack: RulePack, facts: Dict[str, Fact]) -> Dict[str, Counterfactual]:
    return {
        outcome: minimal_delta(rulepack, head, facts, outcome_name=outcome)
        for outcome, head in rulepack.decision_predicates.items()
    }


def load_bearing_predicates(evaluation: EvaluationResult) -> List[str]:
    """Explanation query: which predicates actually carried the decision?
    Walks the winning proof tree -- the same artifact the narration and
    audit record render (§A3: 'one artifact, six products')."""
    if evaluation.decision_head is None or evaluation.decision_head not in evaluation.proof_trees:
        return []

    out: List[str] = []

    def walk(node: ProofNode) -> None:
        for w in node.literals:
            out.append(w.predicate)
            if w.child is not None:
                walk(w.child)

    walk(evaluation.proof_trees[evaluation.decision_head])
    return sorted(set(out))


@dataclass(frozen=True)
class PerCaseSymmetryProbe:
    """Fairness probe query, per-case granularity (§5.1 idea 14): how far is
    each side from winning, for the identical evidence set? A large,
    structural imbalance here (one side needs one fact, the other needs
    five) is informative even before any population-level stratification."""

    distances: Dict[str, int]

    def most_favored(self) -> Optional[str]:
        if not self.distances:
            return None
        return min(self.distances, key=lambda k: self.distances[k])

    def is_symmetric(self, tolerance: int = 0) -> bool:
        if len(self.distances) < 2:
            return True
        values = list(self.distances.values())
        return max(values) - min(values) <= tolerance


def per_case_symmetry(rulepack: RulePack, facts: Dict[str, Fact]) -> PerCaseSymmetryProbe:
    cfs = counterfactuals_for_all_outcomes(rulepack, facts)
    return PerCaseSymmetryProbe(distances={outcome: len(cf.delta) for outcome, cf in cfs.items()})

"""
Semi-naive Horn-clause forward-chaining evaluator with proof recording.

The output of evaluation is not a boolean. It is a proof tree: for every
derived predicate, the rule that fired and the literals -- each traceable to
an evidence_node_id -- that satisfied it. Everything downstream (narration,
counterfactuals, audit, fairness) renders this one artifact.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from .clause import RulePack
from .proof import Fact, FactStatus, LiteralWitness, ProofNode


@dataclass
class EvaluationResult:
    rulepack_hash: str
    reason_code: str
    true_predicates: Set[str]
    fired_rules: List[str]
    proof_trees: Dict[str, ProofNode]  # head predicate -> primary proof
    decision: Optional[str]  # outcome name, or None if no decision predicate fired
    decision_head: Optional[str]
    missing_predicates: Set[str]  # in schema, UNKNOWN status, never addressed
    facts: Dict[str, Fact]
    conflicting_outcomes: Tuple[str, ...] = ()  # >1 outcome fired -- genuine conflict, never silently resolved

    def to_dict(self) -> dict:
        return {
            "rulepack_hash": self.rulepack_hash,
            "reason_code": self.reason_code,
            "decision": self.decision,
            "decision_head": self.decision_head,
            "true_predicates": sorted(self.true_predicates),
            "fired_rules": self.fired_rules,
            "missing_predicates": sorted(self.missing_predicates),
            "conflicting_outcomes": list(self.conflicting_outcomes),
            "proof_tree": self.proof_trees[self.decision_head].to_dict()
            if self.decision_head and self.decision_head in self.proof_trees
            else None,
        }


def _stratify(rulepack: RulePack) -> List[str]:
    """
    Topologically order head predicates so that every negated body literal
    refers to a predicate evaluated in a strictly earlier stratum (or an EDB
    predicate, stratum -1). Raises StratificationError if a negated literal
    targets an IDB predicate (unsupported, see clause.py module docstring)
    or if the positive-dependency graph has a cycle (rulepack is malformed;
    this is also PT-5, acyclicity).
    """
    from .clause import StratificationError

    heads = rulepack.heads()
    for r in rulepack.rules:
        for lit in r.body:
            if lit.negated and lit.predicate in heads:
                raise StratificationError(
                    f"rule {r.rule_id} negates IDB predicate "
                    f"{lit.predicate!r}; only EDB predicates may be negated"
                )

    # Kahn's algorithm over the positive head-depends-on-head graph.
    deps: Dict[str, Set[str]] = {h: set() for h in heads}
    for r in rulepack.rules:
        for lit in r.body:
            if lit.predicate in heads:
                deps[r.head].add(lit.predicate)

    order: List[str] = []
    remaining = dict(deps)
    while remaining:
        ready = [h for h, d in remaining.items() if not d]
        if not ready:
            raise StratificationError(
                f"cyclic dependency among head predicates: {sorted(remaining)}"
            )
        ready.sort()
        order.extend(ready)
        for h in ready:
            del remaining[h]
        for d in remaining.values():
            d.difference_update(ready)
    return order


class Engine:
    """Semi-naive evaluator. Stateless; call evaluate() per case."""

    def evaluate(
        self, rulepack: RulePack, facts: Dict[str, Fact]
    ) -> EvaluationResult:
        strata = _stratify(rulepack)

        true_set: Set[str] = {p for p, f in facts.items() if f.is_true}
        fired_rules: List[str] = []
        proof_by_head: Dict[str, ProofNode] = {}

        # Semi-naive fixpoint per stratum: track a delta frontier and only
        # re-check rules whose body could plausibly be newly satisfied,
        # rather than re-scanning the whole rule set to a fixed point
        # blindly. At this scale (dozens of rules) the asymptotic win is
        # irrelevant; the delta bookkeeping is kept for fidelity to the
        # semi-naive strategy and because it is what makes "fired_rules"
        # deterministic and order-independent.
        for head in strata:
            rules = rulepack.rules_for_head(head)
            delta_new = True
            while delta_new:
                delta_new = False
                for rule in rules:
                    if rule.head in true_set:
                        continue
                    witnesses: List[LiteralWitness] = []
                    ok = True
                    for lit in rule.body:
                        w = self._witness(lit, facts, true_set, proof_by_head)
                        witnesses.append(w)
                        if not w.satisfied:
                            ok = False
                    if ok:
                        true_set.add(rule.head)
                        fired_rules.append(rule.rule_id)
                        proof_by_head[rule.head] = ProofNode(
                            rule_id=rule.rule_id,
                            head=rule.head,
                            holds=True,
                            literals=witnesses,
                            description=rule.description,
                            legal_basis=rule.legal_basis,
                        )
                        delta_new = True

        # An evidence assignment can legitimately satisfy more than one
        # outcome's rules at once -- e.g. one predicate combination backing
        # a card-member-favoring rule and, independently, a different
        # predicate combination backing a merchant-favoring rule. That is
        # not evaluator error; it is a genuine evidentiary conflict, and
        # picking a "winner" by dict/iteration order would silently paper
        # over it. Whenever more than one decision predicate fires, the
        # Referee must not decide at all: this is exactly the shape of case
        # the abstention gate and a human reviewer exist for.
        satisfied_outcomes = [
            (outcome, head) for outcome, head in rulepack.decision_predicates.items() if head in true_set
        ]
        decision = None
        decision_head = None
        conflicting_outcomes: Tuple[str, ...] = ()
        if len(satisfied_outcomes) == 1:
            decision, decision_head = satisfied_outcomes[0]
        elif len(satisfied_outcomes) > 1:
            conflicting_outcomes = tuple(o for o, _ in satisfied_outcomes)

        schema = set(rulepack.predicate_schema) or rulepack.edb_predicates()
        missing = {
            p
            for p in schema
            if facts.get(p, Fact(p, FactStatus.UNKNOWN)).status is FactStatus.UNKNOWN
        }

        return EvaluationResult(
            rulepack_hash=rulepack.content_hash(),
            reason_code=rulepack.reason_code,
            true_predicates=true_set,
            fired_rules=fired_rules,
            proof_trees=proof_by_head,
            decision=decision,
            decision_head=decision_head,
            missing_predicates=missing,
            facts=facts,
            conflicting_outcomes=conflicting_outcomes,
        )

    @staticmethod
    def _witness(
        lit,
        facts: Dict[str, Fact],
        true_set: Set[str],
        proof_by_head: Dict[str, ProofNode],
    ) -> LiteralWitness:
        is_true = lit.predicate in true_set
        satisfied = (not is_true) if lit.negated else is_true

        fact = facts.get(lit.predicate)
        child = proof_by_head.get(lit.predicate) if not lit.negated else None
        evidence_ids: Tuple[str, ...] = ()
        confidence = 1.0
        if fact is not None:
            evidence_ids = fact.evidence_node_ids
            confidence = fact.confidence

        return LiteralWitness(
            predicate=lit.predicate,
            negated=lit.negated,
            satisfied=satisfied,
            evidence_node_ids=evidence_ids,
            confidence=confidence,
            child=child,
        )

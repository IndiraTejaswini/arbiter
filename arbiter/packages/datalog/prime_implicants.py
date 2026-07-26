"""
Offline prime-implicant enumeration over a rulepack's decision function.

This is what makes counterfactuals *exact* rather than approximate (§A4):
a prime implicant of a monotone Boolean function is a minimal set of
literals sufficient to make the function true -- a "minimal winning
coalition" (MWC). Because a rulepack's heads are defined as a disjunction of
rule bodies (each rule an AND of literals), the rulepack already *is* the
function in DNF; every satisfying evidence set satisfies at least one rule
body by construction of the Datalog fixpoint. So exhaustively expanding each
rule body (substituting derived/IDB body literals with their own defining
disjunction) and then minimizing each resulting term yields the complete,
exact set of prime implicants -- not an approximation of one.

Negation is restricted to EDB predicates (see engine.py). Under the
closed-world assumption used by the evaluator, a negative literal `not p` is
satisfied by the mere *absence* of an assertion that `p` is true. That means
a negative literal can never be load-bearing in an *offline, clean-slate*
minimality test: asserting nothing about `p` already satisfies `not p`. So
prime implicants naturally reduce to sets of positive EDB literals -- the
affirmative evidence a party must produce to win. This is not a bug: it is
the correct reading of "minimal winning coalition" under CWA. Negative
literals still matter at *runtime* -- if a case's observed evidence
currently asserts `p = TRUE` (e.g. a contradiction-flagged predicate), the
counterfactual service (packages consumer of this module) reports
*retracting* that fact as part of the delta, computed as a set-difference
against the case's actual observed facts, not against this offline artifact.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Tuple

from .engine import Engine, Fact, FactStatus, RulePack

Literal2 = Tuple[str, bool]  # (predicate, negated)
Term = FrozenSet[Literal2]


@dataclass(frozen=True)
class PrimeImplicant:
    head: str
    literals: Term

    def positive_predicates(self) -> FrozenSet[str]:
        return frozenset(p for p, neg in self.literals if not neg)

    def negative_predicates(self) -> FrozenSet[str]:
        return frozenset(p for p, neg in self.literals if neg)

    def to_dict(self) -> dict:
        return {
            "head": self.head,
            "requires_true": sorted(self.positive_predicates()),
            "requires_false": sorted(self.negative_predicates()),
        }


def _expand_to_dnf(
    rulepack: RulePack, head: str, cache: Dict[str, List[Term]]
) -> List[Term]:
    if head in cache:
        return cache[head]

    heads = rulepack.heads()
    cache[head] = []  # guard against pathological cycles (stratifier already rejects true cycles)
    terms: List[Term] = []

    for rule in rulepack.rules_for_head(head):
        # per-literal list of alternative literal-sets that satisfy it
        options: List[List[Term]] = []
        for lit in rule.body:
            if lit.predicate in heads and not lit.negated:
                sub_terms = _expand_to_dnf(rulepack, lit.predicate, cache)
                options.append(sub_terms if sub_terms else [frozenset()])
            else:
                options.append([frozenset({(lit.predicate, lit.negated)})])

        combos: List[Term] = [frozenset()]
        for opt in options:
            combos = [a | b for a in combos for b in opt]

        for term in combos:
            preds_seen: Dict[str, bool] = {}
            contradictory = False
            for p, neg in term:
                if p in preds_seen and preds_seen[p] != neg:
                    contradictory = True
                    break
                preds_seen[p] = neg
            if not contradictory:
                terms.append(term)

    deduped = list({t: None for t in terms}.keys())
    cache[head] = deduped
    return deduped


def _is_implicant(engine: Engine, rulepack: RulePack, head: str, term: Term) -> bool:
    facts: Dict[str, Fact] = {}
    for p, neg in term:
        if not neg:
            facts[p] = Fact(predicate=p, status=FactStatus.TRUE, evidence_node_ids=())
    result = engine.evaluate(rulepack, facts)
    return head in result.true_predicates


def _minimize(engine: Engine, rulepack: RulePack, head: str, term: Term) -> Term:
    current = set(term)
    changed = True
    while changed:
        changed = False
        for lit in list(current):
            trial = current - {lit}
            if _is_implicant(engine, rulepack, head, frozenset(trial)):
                current = trial
                changed = True
    return frozenset(current)


def enumerate_prime_implicants(
    rulepack: RulePack, head: str
) -> List[PrimeImplicant]:
    """Exact, complete enumeration of the minimal winning coalitions for `head`."""
    engine = Engine()
    cache: Dict[str, List[Term]] = {}
    raw_terms = _expand_to_dnf(rulepack, head, cache)

    minimized = {_minimize(engine, rulepack, head, t) for t in raw_terms}

    # drop any term that is a strict superset of another (dominated, not prime)
    result: List[Term] = []
    for t in minimized:
        if not any(other < t for other in minimized if other != t):
            result.append(t)

    return [PrimeImplicant(head=head, literals=t) for t in sorted(result, key=lambda s: (len(s), sorted(s)))]

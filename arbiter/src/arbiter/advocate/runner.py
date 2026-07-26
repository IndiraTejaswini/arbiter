"""
Dual-Advocate Adjudication (A2) -- deterministic runner.

Scoping note: the production design runs two LLM instances with opposed
objectives searching a large natural-language-shaped combinatorial space for
the strongest argument each side can make. This module is the mandatory
rules-only fallback CLAUDE.md's "prefer" section and the Phase-7 build gate
both require ("the rules-only path must keep working with every LLM
disabled") -- and it is also the *default* runner, since it reproduces the
structural contract a real LLM advocate must satisfy exactly: read-only,
tool-less, emits a typed argument graph, never prose, never a decision --
using the same combinatorial search the doc identifies as the real work:
given the objectively-derivable facts, which minimal winning coalition
(prime implicant, A4) for this side's outcome is closest to already being
satisfied? What a real LLM advocate adds beyond this is judgment under
ambiguous/free-text evidence that hasn't been reduced to typed predicates
yet -- that reduction is evidence-ingest's job (A8), upstream of this
service either way.

Critically: an advocate can only ever cite facts the graph *already*
objectively derived (arbiter.evidence.derive_predicate_facts) -- it cannot
introduce a claim that doesn't trace to an evidence node. This is the same
guarantee constrained decoding provides for the real system: the advocate
cannot hallucinate a fact into existence, only select among what exists.
advocate.verify re-checks every cited triple against the graph independently
before it counts for anything.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from arbiter.horn.clause import RulePack
from arbiter.horn.implicants import PrimeImplicant, enumerate_prime_implicants
from arbiter.horn.proof import Fact

from .contract import ArgumentGraph, ArgumentTriple


def _literal_satisfied(predicate: str, negated: bool, true_preds: set) -> bool:
    is_true = predicate in true_preds
    return (not is_true) if negated else is_true


def _find_warrant_rule(rulepack: RulePack, mwc: PrimeImplicant):
    """Best-effort lookup of which authored rule this MWC came from, for
    citation purposes. Exact for flat (non-nested) rulepacks, where every
    prime implicant equals exactly one rule's own (already-minimal) body."""
    for rule in rulepack.rules_for_head(mwc.head):
        body_literals = frozenset((lit.predicate, lit.negated) for lit in rule.body)
        if body_literals == mwc.literals:
            return rule.rule_id
    return None


class Advocate:
    """One side of the dual-advocate pair. Stateless; read-only over facts."""

    def __init__(self, side: str, target_outcome: str):
        self.side = side
        self.target_outcome = target_outcome

    def construct_case(self, rulepack: RulePack, facts: Dict[str, Fact]) -> ArgumentGraph:
        """
        Two phases, deliberately not one. Phase 1 checks satisfaction
        against the rulepack's actual rule bodies -- exactly what
        Engine.evaluate itself checks, negative literals included. Phase 2
        (only reached if phase 1 finds nothing) uses prime implicants purely
        as a distance heuristic for coaching.

        These must not be conflated: a prime implicant is a *minimal
        sufficiency condition assuming a clean slate*, so minimization
        legitimately drops a negative literal like "not refund_issued" when
        nothing else is asserted (see horn/implicants.py's module
        docstring). But at runtime other facts *are* asserted, and if
        refund_issued happens to be TRUE, that dropped literal is exactly
        what should have blocked the rule. Checking only the minimized MWC's
        remaining literals could report "fully satisfied" for a rule the
        real engine would never fire -- covered by
        tests/property/test_rulepacks.py::test_advocate_completeness_matches_referee_exhaustive.
        """
        head = rulepack.decision_predicates[self.target_outcome]
        true_preds = {p for p, f in facts.items() if f.is_true}

        def rule_satisfied(rule) -> bool:
            return all(_literal_satisfied(lit.predicate, lit.negated, true_preds) for lit in rule.body)

        fully_satisfied_rules = [r for r in rulepack.rules_for_head(head) if rule_satisfied(r)]

        if fully_satisfied_rules:
            seen: set = set()
            triples: List[ArgumentTriple] = []
            for rule in fully_satisfied_rules:
                for lit in rule.body:
                    key = (lit.predicate, lit.negated)
                    if key in seen:
                        continue
                    seen.add(key)
                    fact = facts.get(lit.predicate)
                    evidence_ids = fact.evidence_node_ids if fact else ()
                    triples.append(ArgumentTriple(lit.predicate, lit.negated, evidence_ids, rule.rule_id))
            return ArgumentGraph(
                side=self.side, target_outcome=self.target_outcome, target_head=head,
                triples=tuple(triples), best_mwc=None, missing_literals=(), fully_satisfied=True,
            )

        # Nothing fully satisfied: fall back to the prime-implicant distance
        # heuristic purely for coaching ("closest rule path"). This can be
        # optimistic about dropped negative literals, which only ever makes
        # the reported gap *smaller* than reality -- never claims a false
        # win, since `fully_satisfied` is hard-gated by the real-rule check
        # above regardless of what this heuristic reports.
        mwcs = enumerate_prime_implicants(rulepack, head)
        if not mwcs:
            return ArgumentGraph(self.side, self.target_outcome, head, (), (), False, best_mwc=None)

        def missing_for(mwc: PrimeImplicant) -> List[Tuple[str, bool]]:
            return [(p, neg) for p, neg in mwc.literals if not _literal_satisfied(p, neg, true_preds)]

        best_mwc, missing = min(((mwc, missing_for(mwc)) for mwc in mwcs), key=lambda pair: len(pair[1]))
        warrant = _find_warrant_rule(rulepack, best_mwc)
        satisfied_triples = []
        for p, neg in best_mwc.literals:
            if (p, neg) in missing:
                continue
            fact = facts.get(p)
            evidence_ids = fact.evidence_node_ids if fact else ()
            satisfied_triples.append(ArgumentTriple(p, neg, evidence_ids, warrant))

        return ArgumentGraph(
            side=self.side, target_outcome=self.target_outcome, target_head=head,
            triples=tuple(satisfied_triples), best_mwc=best_mwc,
            missing_literals=tuple(missing), fully_satisfied=False,
        )


def run_dual_advocacy(
    rulepack: RulePack, facts: Dict[str, Fact]
) -> Tuple[ArgumentGraph, ArgumentGraph]:
    """Both sides run over the identical evidence graph, neither permitted
    to decide (C3: both parties get an advocate over the same evidence
    pool)."""
    outcomes = list(rulepack.decision_predicates.keys())
    if len(outcomes) < 2:
        raise ValueError("rulepack must define at least two outcomes for dual advocacy")
    cm_outcome = next(o for o in outcomes if "CARD_MEMBER" in o)
    m_outcome = next(o for o in outcomes if "MERCHANT" in o)

    advocate_cm = Advocate("CM", cm_outcome)
    advocate_m = Advocate("M", m_outcome)
    return advocate_cm.construct_case(rulepack, facts), advocate_m.construct_case(rulepack, facts)


def completeness_gap(rulepack: RulePack, facts: Dict[str, Fact]) -> List[str]:
    """A2 mitigation: a mechanical, non-LLM check for any predicate in the
    reason code's full schema that neither advocate's evidence base resolves
    -- i.e. still UNKNOWN after objective extraction."""
    from arbiter.horn.proof import FactStatus

    schema = set(rulepack.predicate_schema) or rulepack.edb_predicates()
    return sorted(
        p for p in schema
        if facts.get(p, Fact(p, FactStatus.UNKNOWN)).status is FactStatus.UNKNOWN
    )

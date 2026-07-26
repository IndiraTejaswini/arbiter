"""
Dual-Advocate Adjudication (§A2).

Scoping note: the production design runs two LLM instances with opposed
objectives, constrained decoding, and no tool access, searching a large
natural-language-shaped combinatorial space ("which rule paths could this
evidence satisfy") for the strongest argument each side can make. No LLM
runs in this environment, so this module reproduces the *structural
contract* the real advocates must satisfy -- read-only, tool-less, emits a
typed argument graph of (predicate, evidence_node_id, warrant) triples,
never prose, never a decision -- using the exact combinatorial search the
doc identifies as the real work: given the objectively-derivable facts,
which minimal winning coalition (prime implicant, §A4) for this side's
outcome is closest to already being satisfied? This is not a simplification
of *what* an advocate is supposed to do; it is the same search, run
directly instead of via an LLM's approximation of it. What a real LLM
advocate adds beyond this is judgment under ambiguous/free-text evidence
that hasn't been reduced to typed predicates yet -- that reduction is
evidence-ingest's job (§A8), upstream of this service either way.

Critically: an advocate can only ever cite facts the graph *already*
objectively derived (EvidenceGraph.derive_predicate_facts) -- it cannot
introduce a claim that doesn't trace to an evidence node. This is the same
guarantee constrained decoding provides for the real system: the advocate
cannot hallucinate a fact into existence, only select among what exists.
The Referee (services/referee) re-verifies every cited triple against the
graph independently before it counts for anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from packages.datalog.engine import Fact, FactStatus, RulePack
from packages.datalog.prime_implicants import PrimeImplicant, enumerate_prime_implicants


@dataclass(frozen=True)
class ArgumentTriple:
    """(predicate, evidence_node_id, warrant_rule_id) -- §A2's schema.
    Every assertion an advocate makes must be exactly this shape;
    there is no field for free text."""

    predicate: str
    negated: bool
    evidence_node_ids: Tuple[str, ...]
    warrant_rule_id: Optional[str]


@dataclass(frozen=True)
class ArgumentGraph:
    side: str  # "CM" | "M"
    target_outcome: str
    target_head: str
    triples: Tuple[ArgumentTriple, ...]
    best_mwc: Optional[PrimeImplicant]
    missing_literals: Tuple[Tuple[str, bool], ...]  # (predicate, negated) not yet satisfied
    fully_satisfied: bool

    def cited_evidence_ids(self) -> List[str]:
        out: List[str] = []
        for t in self.triples:
            out.extend(t.evidence_node_ids)
        return out

    def to_dict(self) -> dict:
        return {
            "side": self.side,
            "target_outcome": self.target_outcome,
            "fully_satisfied": self.fully_satisfied,
            "triples": [
                {
                    "predicate": t.predicate,
                    "negated": t.negated,
                    "evidence_node_ids": list(t.evidence_node_ids),
                    "warrant_rule_id": t.warrant_rule_id,
                }
                for t in self.triples
            ],
            "missing_literals": [
                {"predicate": p, "negated": neg} for p, neg in self.missing_literals
            ],
        }


def _literal_satisfied(predicate: str, negated: bool, true_preds: set) -> bool:
    is_true = predicate in true_preds
    return (not is_true) if negated else is_true


def _find_warrant_rule(rulepack: RulePack, mwc: PrimeImplicant) -> Optional[str]:
    """Best-effort lookup of which authored rule this MWC came from, for
    citation purposes. Exact for the flat (non-nested) rulepacks shipped in
    rulepacks/amex/, where every prime implicant equals exactly one rule's
    own (already-minimal) body."""
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
        nothing else is asserted (§A4's module docstring). But at runtime
        other facts *are* asserted, and if refund_issued happens to be TRUE,
        that dropped literal is exactly what should have blocked the rule.
        An earlier version of this method checked only the minimized MWC's
        remaining literals and could therefore report "fully satisfied"
        for a rule the real engine would never fire -- caught by
        evals/property_tests.py::test_advocate_completeness_matches_referee_exhaustive
        against facts={service_never_rendered: TRUE, refund_issued: TRUE}.
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
            return ArgumentGraph(self.side, self.target_outcome, head, (), None, (), False)

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
    """§9.3 sequence: 'par Dual advocacy' -- both sides run over the identical
    evidence graph, neither permitted to decide."""
    outcomes = list(rulepack.decision_predicates.keys())
    if len(outcomes) < 2:
        raise ValueError("rulepack must define at least two outcomes for dual advocacy")
    cm_outcome = next(o for o in outcomes if "CARD_MEMBER" in o)
    m_outcome = next(o for o in outcomes if "MERCHANT" in o)

    advocate_cm = Advocate("CM", cm_outcome)
    advocate_m = Advocate("M", m_outcome)
    return advocate_cm.construct_case(rulepack, facts), advocate_m.construct_case(rulepack, facts)


def completeness_gap(rulepack: RulePack, facts: Dict[str, Fact]) -> List[str]:
    """§A2 mitigation (b): a mechanical, non-LLM check for any predicate in
    the reason code's full schema that neither advocate's evidence base
    resolves -- i.e. still UNKNOWN after objective extraction. Because
    advocates in this build can only cite what's already objectively
    derived (they never introduce claims), this coincides with the
    Referee's own missing_predicates computation -- the same fact checked
    from a different angle, not a coincidence: it is what makes the check
    "mechanical" rather than advocate-dependent."""
    schema = set(rulepack.predicate_schema) or rulepack.edb_predicates()
    return sorted(
        p for p in schema
        if facts.get(p, Fact(p, FactStatus.UNKNOWN)).status is FactStatus.UNKNOWN
    )

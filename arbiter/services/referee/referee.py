"""
Deterministic Referee (§A3, §A8).

The Referee is the one component in the whole system that is never handed
attacker-controlled text and never runs an LLM. Its input is exactly two
things: (1) the objective predicate facts the graph mechanically derived
from evidence nodes (services/graph), and (2) the two advocates' typed
argument graphs (services/advocate).

The decision itself is evaluated over (1) directly -- the complete,
objectively-derived fact set -- not merely over whatever subset of facts
the two advocates happened to cite. That is a deliberate correction from an
earlier version of this module, which evaluated only the union of
advocate-cited triples. That design looked like a stronger security
property ("nothing enters the decision unless an advocate vouched for it")
but was actually an omission vulnerability: an advocate's argument-graph
search targets a *minimal* sufficient case, so a fact that's true, known to
the graph, and would have *blocked* a rule (a negative literal's predicate
turning out TRUE) could simply never get mentioned by either side and
silently drop out of evaluation -- making outcomes more permissive than the
evidence actually supports. Caught by
evals/property_tests.py::test_advocate_completeness_matches_referee_exhaustive
against facts={service_never_rendered: TRUE, refund_issued: TRUE}, where
C02_R4 should be blocked by refund_issued but wasn't, because no advocate's
minimal winning coalition happened to cite it.

Triple verification still matters, just for a different job: it is a
mechanical dishonesty check. Every triple an advocate cites is independently
re-checked against the objective facts; anything that doesn't check out
(a fabricated predicate value, a forged evidence citation) is recorded as
rejected. That is audit and provenance signal -- it can flag a
systematically dishonest advocate implementation, feed the fairness/risk
layer, and is exactly what narration cites for "why" -- but it does not
gate what the decision itself is computed over. The objective facts were
already trustworthy before either advocate saw them: they came from
evidence-ingest's quarantined extraction and the graph's provenance-tiered,
contradiction-checked derivation (§A6, §A8), which is the actual
injection-resistance boundary. The advocate/referee split is about
*search and explanation*, not about gatekeeping truth the graph already
established.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from packages.datalog.engine import Engine, EvaluationResult, Fact, FactStatus, RulePack
from services.advocate.advocate import ArgumentGraph, ArgumentTriple


@dataclass(frozen=True)
class TripleVerification:
    side: str
    triple: ArgumentTriple
    verified: bool
    reason: str


@dataclass(frozen=True)
class RefereeResult:
    evaluation: EvaluationResult
    verifications: List[TripleVerification]
    verified_facts: Dict[str, Fact]
    rejected_triples: List[TripleVerification]

    def to_dict(self) -> dict:
        d = self.evaluation.to_dict()
        d["triple_verification"] = {
            "total": len(self.verifications),
            "verified": sum(1 for v in self.verifications if v.verified),
            "rejected": [
                {"side": v.side, "predicate": v.triple.predicate, "negated": v.triple.negated, "reason": v.reason}
                for v in self.rejected_triples
            ],
        }
        return d


def _verify_one(triple: ArgumentTriple, objective_fact: Optional[Fact]) -> Tuple[bool, str]:
    status = objective_fact.status if objective_fact else FactStatus.UNKNOWN

    if triple.negated:
        if status is FactStatus.TRUE:
            return False, (
                f"advocate claims NOT {triple.predicate}, but the objective evidence "
                f"graph establishes it TRUE (nodes {objective_fact.evidence_node_ids})"
            )
        return True, "negation satisfied by absence of contrary evidence or explicit contradiction"

    if status is not FactStatus.TRUE:
        return False, (
            f"advocate claims {triple.predicate} TRUE, but the objective evidence graph "
            f"does not establish it (status={status.value})"
        )
    if objective_fact and triple.evidence_node_ids:
        cited = set(triple.evidence_node_ids)
        actual = set(objective_fact.evidence_node_ids)
        if not cited.issubset(actual):
            return False, (
                f"cited evidence_node_ids {sorted(cited - actual)} do not appear in the "
                f"objective fact's provenance {sorted(actual)} -- citation does not check out"
            )
    return True, "positive literal established by objective evidence, citation checks out"


class Referee:
    def __init__(self, engine: Optional[Engine] = None):
        self.engine = engine or Engine()

    def verify_triples(
        self,
        argument_graphs: Sequence[ArgumentGraph],
        objective_facts: Dict[str, Fact],
    ) -> Tuple[List[TripleVerification], Dict[str, Fact]]:
        verifications: List[TripleVerification] = []
        verified_facts: Dict[str, Fact] = {}

        for ag in argument_graphs:
            for triple in ag.triples:
                objective_fact = objective_facts.get(triple.predicate)
                ok, reason = _verify_one(triple, objective_fact)
                verifications.append(TripleVerification(ag.side, triple, ok, reason))
                if ok and not triple.negated:
                    # Merge the OBJECTIVE fact, never the advocate's restatement of it.
                    verified_facts[triple.predicate] = objective_fact
                elif ok and triple.negated:
                    # A satisfied negation contributes no positive EDB fact;
                    # the engine's closed-world default already handles it,
                    # unless the objective graph explicitly recorded FALSE
                    # (a real contradiction-resolved negative), which we
                    # should preserve for completeness/citation purposes.
                    if objective_fact is not None and objective_fact.status is FactStatus.FALSE:
                        verified_facts[triple.predicate] = objective_fact

        return verifications, verified_facts

    def adjudicate(
        self,
        rulepack: RulePack,
        argument_graphs: Sequence[ArgumentGraph],
        objective_facts: Dict[str, Fact],
    ) -> RefereeResult:
        verifications, verified_facts = self.verify_triples(argument_graphs, objective_facts)
        # The decision runs over the complete objective fact set -- see
        # module docstring for why this must not be narrowed to only what
        # was cited. `verified_facts` (the cited-and-checked subset) is
        # still returned, for narration/audit: it is what the proof tree's
        # citations should point back to as "an advocate specifically
        # argued this", not what the decision itself was computed from.
        evaluation = self.engine.evaluate(rulepack, objective_facts)
        rejected = [v for v in verifications if not v.verified]
        return RefereeResult(
            evaluation=evaluation,
            verifications=verifications,
            verified_facts=verified_facts,
            rejected_triples=rejected,
        )

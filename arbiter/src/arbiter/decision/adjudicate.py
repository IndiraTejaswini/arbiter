"""
Deterministic Referee orchestration (A3, A8, C1).

The Referee is the one component in the whole system that is never handed
attacker-controlled text and never runs an LLM. Its input is exactly two
things: (1) the objective predicate facts arbiter.evidence.derive
mechanically derived from evidence nodes, and (2) the two advocates' typed
argument graphs (arbiter.advocate).

The decision itself is evaluated over (1) directly -- the complete,
objectively-derived fact set -- not merely over whatever subset of facts the
two advocates happened to cite. That is a deliberate design choice, not the
obvious one: evaluating only the union of advocate-cited triples looks like
a stronger security property ("nothing enters the decision unless an
advocate vouched for it") but is actually an omission vulnerability -- an
advocate's argument-graph search targets a *minimal* sufficient case, so a
fact that's true, known to the graph, and would have *blocked* a rule (a
negative literal's predicate turning out TRUE) could simply never get
mentioned by either side and silently drop out of evaluation, making
outcomes more permissive than the evidence actually supports. Covered by
tests/property/test_rulepacks.py::test_advocate_completeness_matches_referee_exhaustive
against facts={service_never_rendered: TRUE, refund_issued: TRUE}, where a
refund-issued rule should be blocked by refund_issued but wasn't, because no
advocate's minimal winning coalition happened to cite it.

Triple verification (arbiter.advocate.verify) still matters, just for a
different job: it is a mechanical dishonesty check. Every triple an advocate
cites is independently re-checked against the objective facts; anything
that doesn't check out is recorded as rejected -- audit and provenance
signal, feeding the fairness/risk layer and exactly what narration cites for
"why" -- but it does not gate what the decision itself is computed over.
The objective facts were already trustworthy before either advocate saw
them: they came from evidence-ingest's quarantined extraction and the
graph's provenance-tiered, contradiction-checked derivation (A6, A8), which
is the actual injection-resistance boundary. The advocate/referee split is
about *search and explanation*, not about gatekeeping truth the graph
already established.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from arbiter.advocate.contract import ArgumentGraph
from arbiter.advocate.verify import TripleVerification, verify_assertions
from arbiter.horn.chain import Engine, EvaluationResult
from arbiter.horn.clause import RulePack
from arbiter.horn.proof import Fact


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


class Referee:
    def __init__(self, engine: Optional[Engine] = None):
        self.engine = engine or Engine()

    def adjudicate(
        self,
        rulepack: RulePack,
        argument_graphs: Sequence[ArgumentGraph],
        objective_facts: Dict[str, Fact],
    ) -> RefereeResult:
        verifications, verified_facts = verify_assertions(argument_graphs, objective_facts)
        # The decision runs over the complete objective fact set -- see
        # module docstring for why this must not be narrowed to only what
        # was cited. `verified_facts` (the cited-and-checked subset) is
        # still returned, for narration/audit.
        evaluation = self.engine.evaluate(rulepack, objective_facts)
        rejected = [v for v in verifications if not v.verified]
        return RefereeResult(
            evaluation=evaluation,
            verifications=verifications,
            verified_facts=verified_facts,
            rejected_triples=rejected,
        )

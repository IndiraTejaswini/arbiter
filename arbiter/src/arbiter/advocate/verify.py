"""
verify_assertions -- the guarantee that an advocate can find evidence but
can never assert a predicate into truth (CLAUDE.md invariant #4).

Every triple an advocate cites is independently re-checked against the
objective, already-tier-gated facts arbiter.evidence.derive produced. A
triple that doesn't check out (a fabricated predicate value, a forged
evidence citation, a claim about a predicate the graph never established) is
rejected and logged -- this is a mechanical dishonesty check, not a
probabilistic one. Rejections never silently disappear: arbiter.decision
writes them to the audit trail.

This is also C5 in miniature: nothing an advocate says is trusted by
construction. It is trusted only after re-derivation from the graph, exactly
as if the advocate hadn't spoken at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from arbiter.horn.proof import Fact, FactStatus

from .contract import ArgumentGraph, ArgumentTriple


@dataclass(frozen=True)
class TripleVerification:
    side: str
    triple: ArgumentTriple
    verified: bool
    reason: str


# Alias kept for readability at call sites that only care about the failure case.
Rejection = TripleVerification


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


def verify_assertions(
    argument_graphs: Sequence[ArgumentGraph],
    objective_facts: Dict[str, Fact],
) -> Tuple[List[TripleVerification], Dict[str, Fact]]:
    """Returns (all verifications, verified_facts). `verified_facts` merges
    in the OBJECTIVE fact for every triple that checked out -- never the
    advocate's own restatement of it -- and is what narration/audit cite as
    "an advocate specifically argued this". It is NOT what the decision
    itself is computed over (see arbiter.decision.adjudicate's module
    docstring for why narrowing the decision to only cited facts is an
    omission vulnerability, not a stronger security property)."""
    verifications: List[TripleVerification] = []
    verified_facts: Dict[str, Fact] = {}

    for ag in argument_graphs:
        for triple in ag.triples:
            objective_fact = objective_facts.get(triple.predicate)
            ok, reason = _verify_one(triple, objective_fact)
            verifications.append(TripleVerification(ag.side, triple, ok, reason))
            if ok and not triple.negated:
                verified_facts[triple.predicate] = objective_fact
            elif ok and triple.negated:
                if objective_fact is not None and objective_fact.status is FactStatus.FALSE:
                    verified_facts[triple.predicate] = objective_fact

    return verifications, verified_facts

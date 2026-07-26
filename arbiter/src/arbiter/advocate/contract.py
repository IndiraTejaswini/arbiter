"""
Dual-Advocate Adjudication typed contract (A2).

Production design: two LLM instances with opposed objectives, constrained
decoding, and no tool access, searching a large natural-language-shaped
combinatorial space ("which rule paths could this evidence satisfy") for the
strongest argument each side can make. Whatever runs the search -- an LLM or
the deterministic runner in runner.py -- must emit exactly this shape: a
typed argument graph of (predicate, evidence_node_id, warrant) triples,
never prose, never a decision. C5 (untrusted text never reaches the
decider) is enforced by this contract having no free-text field at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from arbiter.horn.implicants import PrimeImplicant


@dataclass(frozen=True)
class ArgumentTriple:
    """(predicate, evidence_node_id, warrant_rule_id) -- A2's schema. Every
    assertion an advocate makes must be exactly this shape; there is no
    field for free text."""

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
    missing_literals: Tuple[Tuple[str, bool], ...]  # (predicate, negated) not yet satisfied
    fully_satisfied: bool
    best_mwc: Optional[PrimeImplicant] = None

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

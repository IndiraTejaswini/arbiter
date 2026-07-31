"""Facts and proof-tree vocabulary produced by forward chaining (arbiter.horn.chain)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple


class FactStatus(Enum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"  # no evidence addresses this predicate at all


@dataclass(frozen=True)
class Fact:
    """A grounded EDB predicate, with provenance back to evidence nodes."""

    predicate: str
    status: FactStatus
    evidence_node_ids: Tuple[str, ...] = ()
    confidence: float = 1.0  # extraction/provenance-weighted confidence in [0,1]

    @property
    def is_true(self) -> bool:
        return self.status is FactStatus.TRUE


@dataclass
class ProofNode:
    """One node of the proof tree: a fired rule and what satisfied it."""

    rule_id: str
    head: str
    holds: bool
    literals: List["LiteralWitness"] = field(default_factory=list)
    description: str = ""
    legal_basis: str = ""

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "head": self.head,
            "holds": self.holds,
            "description": self.description,
            "legal_basis": self.legal_basis,
            "literals": [w.to_dict() for w in self.literals],
        }

    def cited_evidence_ids(self) -> List[str]:
        out: List[str] = []
        for w in self.literals:
            out.extend(w.evidence_node_ids)
            if w.child is not None:
                out.extend(w.child.cited_evidence_ids())
        return out


@dataclass
class LiteralWitness:
    """Why one body literal of a fired rule was satisfied."""

    predicate: str
    negated: bool
    satisfied: bool
    evidence_node_ids: Tuple[str, ...] = ()
    confidence: float = 1.0
    child: Optional[ProofNode] = None  # set if predicate is itself an IDB head

    def to_dict(self) -> dict:
        d = {
            "predicate": self.predicate,
            "negated": self.negated,
            "satisfied": self.satisfied,
            "evidence_node_ids": list(self.evidence_node_ids),
            "confidence": self.confidence,
        }
        if self.child is not None:
            d["child"] = self.child.to_dict()
        return d

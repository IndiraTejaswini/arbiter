"""
Horn clause vocabulary: predicates, literals, rules, rulepacks.

Scope, stated honestly rather than hidden: this is *propositional* Horn-clause
evaluation (predicates are 0-ary atoms, not first-order terms with unification).
That is a deliberate simplification, not a missed corner: adjudication
predicates ("delivery_confirmed", "signature_missing") are boolean facts about
a single case, evaluated one case at a time. There is no join across a
relational domain, so first-order Datalog buys nothing here and would only
complicate prime-implicant enumeration (which is inherently a Boolean-function
concept and requires a finite, enumerable literal set).

Negation is restricted to EDB (evidence-derived) predicates only -- i.e. this
is "semi-positive" Datalog, not fully stratified Datalog over IDB predicates.
That restriction is sufficient for reason-code rulepacks (negated conditions
are always "this piece of evidence is absent/contradicted", never "this
derived legal conclusion does not hold") and keeps proof-tree construction
for negative literals well-defined: a negated EDB literal always has a
citable evidentiary basis (either an explicit contradicting fact, or an
explicit absence).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Set, Tuple


@dataclass(frozen=True)
class Literal:
    """A body literal: a predicate, optionally negated."""

    predicate: str
    negated: bool = False

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"not {self.predicate}" if self.negated else self.predicate

    def key(self) -> Tuple[str, bool]:
        return (self.predicate, self.negated)


@dataclass(frozen=True)
class Rule:
    """A Horn clause: head <- body (conjunction of literals)."""

    rule_id: str
    head: str
    body: Tuple[Literal, ...]
    description: str = ""

    def __post_init__(self) -> None:
        preds: Dict[str, bool] = {}
        for lit in self.body:
            if lit.predicate in preds and preds[lit.predicate] != lit.negated:
                raise ValueError(
                    f"rule {self.rule_id}: predicate {lit.predicate!r} appears "
                    f"both positively and negatively in the same body -- "
                    f"unsatisfiable by construction"
                )
            preds[lit.predicate] = lit.negated


@dataclass(frozen=True)
class RulePack:
    """A versioned, content-addressed set of rules for one reason code.

    Rulepacks are DATA, never code (C1, CLAUDE.md invariant #7): every
    decision pins the content hash of the rulepack that produced it, and
    rulepacks are loaded from YAML, never edited in place.
    """

    rulepack_id: str
    reason_code: str
    version: str
    rules: Tuple[Rule, ...]
    decision_predicates: Dict[str, str]  # outcome name -> head predicate
    predicate_schema: Tuple[str, ...] = ()  # full required-predicate universe
    predicate_meta: Dict[str, "PredicateMeta"] | None = None  # party/min_tier, optional

    def content_hash(self) -> str:
        import hashlib
        import json

        payload = {
            "rulepack_id": self.rulepack_id,
            "reason_code": self.reason_code,
            "version": self.version,
            "rules": [
                {
                    "rule_id": r.rule_id,
                    "head": r.head,
                    "body": [[l.predicate, l.negated] for l in r.body],
                }
                for r in self.rules
            ],
            "decision_predicates": self.decision_predicates,
        }
        blob = json.dumps(payload, sort_keys=True).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    def heads(self) -> Set[str]:
        return {r.head for r in self.rules}

    def edb_predicates(self) -> Set[str]:
        heads = self.heads()
        edb: Set[str] = set()
        for r in self.rules:
            for lit in r.body:
                if lit.predicate not in heads:
                    edb.add(lit.predicate)
        return edb

    def rules_for_head(self, head: str) -> List[Rule]:
        return [r for r in self.rules if r.head == head]


@dataclass(frozen=True)
class PredicateMeta:
    """Documentation/provenance-gating metadata for one EDB predicate (C2).

    `party` records which side the predicate favours when true, purely for
    fairness-lint and UI purposes -- the engine itself is party-blind.
    `min_tier` is enforced at derivation time (arbiter.evidence.derive), not
    here: the Horn engine only ever sees already-tier-checked Facts.
    """

    predicate: str
    party: str  # CARD_MEMBER | MERCHANT | NEUTRAL
    min_tier: str  # COMMITTED | NETWORK | SUBMITTED | ASSERTED


class StratificationError(Exception):
    """Raised when a rulepack negates an IDB (derived) predicate -- unsupported."""

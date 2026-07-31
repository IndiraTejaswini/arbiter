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
from dataclasses import field as dataclass_field
from typing import TYPE_CHECKING, Dict, List, Set, Tuple

if TYPE_CHECKING:  # pragma: no cover - typing only
    # arbiter.eligibility is stdlib-only, like this package. The import is
    # under TYPE_CHECKING because nothing in `horn` ever calls into it --
    # RulePack merely carries the value so content_hash() can pin it.
    from arbiter.eligibility.models import ChargebackRight


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
    # Regulatory/policy citation this rule encodes, surfaced in the proof
    # tree (arbiter.horn.proof.ProofNode.legal_basis) so a reviewer -- or
    # the merchant/card-member console -- can see WHY the rule exists, not
    # just what it checks. Documentation, like `description`: excluded
    # from content_hash() deliberately, same as description, since it
    # doesn't change the decision function itself.
    legal_basis: str = ""

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
    # The network chargeback right for this reason code: filing window and
    # excluded transactions (arbiter.eligibility). Carried HERE, on the
    # rulepack, for exactly one reason -- so content_hash() pins it, and a
    # decision that ended on "excluded transaction: Card Present" can be
    # replayed against the exclusion text that produced it. The Horn engine
    # never reads this field; `arbiter.eligibility.evaluate_chargeback_right`
    # runs strictly before `Engine.evaluate` and, if the right is
    # unavailable, instead of it.
    chargeback_right: "ChargebackRight | None" = None
    # Party-facing catalogue metadata: the guide's own name for this reason
    # code, and one sentence describing the dispute from the card member's
    # point of view. Prose ABOUT the rules, never a check -- so, exactly like
    # `Rule.description` and `ChargebackRight.source`, it is excluded from
    # `content_hash()` (which builds an explicit allowlist payload, so this
    # is enforced by construction rather than by remembering).
    #
    # It lives on the rulepack because the alternative is what the console
    # used to do: hardcode "C08 -- I never received the goods or service" in
    # three separate TypeScript files. A fourth rulepack dropped into
    # `rulepacks/amex/` was then adjudicable by the backend and invisible in
    # the UI -- unfilable and unfilterable -- which makes "adding a reason
    # code is a YAML file" true of the engine and false of the product.
    title: str = ""
    description: str = ""
    # Memo for content_hash(). A RulePack is frozen and its hash is by
    # definition invariant, but `Engine.evaluate` stamps it onto every
    # EvaluationResult -- so it is recomputed once per case in production and
    # tens of thousands of times in the exhaustive property tests. A dict
    # (mutable in place) rather than an Optional[str], because frozen
    # dataclasses forbid attribute assignment. compare=False keeps the cache
    # out of __eq__/__hash__: two rulepacks must not compare unequal because
    # one of them has been hashed and the other has not.
    _hash_memo: Dict[str, str] = dataclass_field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def content_hash(self) -> str:
        import hashlib
        import json

        memoized = self._hash_memo.get("content")
        if memoized is not None:
            return memoized

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
        # Added conditionally, so a rulepack that declares no chargeback
        # right keeps the hash it had before this field existed -- decisions
        # already pinned against those packs stay replayable.
        if self.chargeback_right is not None:
            payload["chargeback_right"] = self.chargeback_right.canonical()
        blob = json.dumps(payload, sort_keys=True).encode("utf-8")
        digest = hashlib.sha256(blob).hexdigest()
        self._hash_memo["content"] = digest
        return digest

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

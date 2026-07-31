"""Loads a rulepack from its YAML source into the typed Rule/RulePack model.

Rulepacks are data, not code: this loader is the only place that ever
interprets rulepack YAML, and it does no evaluation itself -- content-
addressing (RulePack.content_hash()) is computed over the parsed structure,
not the YAML text, so formatting changes (whitespace, comments, key order)
never change a rulepack's hash or invalidate pinned decisions.

Two authoring forms are expanded here rather than interpreted later, so that
everything downstream -- the engine, prime-implicant enumeration, the
counterfactual ledger, the property tests -- keeps seeing plain conjunctive
Horn clauses and needs no knowledge that the sugar exists:

  * `at_least: {n, of: [...]}` in a rule body (threshold / "N of M").
  * `chargeback_right:` (filing window + excluded transactions), which is
    not rules at all and is evaluated by `arbiter.eligibility` before the
    referee ever runs.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import yaml

from arbiter.core.errors import RulepackError
from arbiter.eligibility.models import (
    ChargebackRight,
    Condition,
    Exclusion,
    FilingWindowBranch,
)
from arbiter.horn.clause import Literal, PredicateMeta, Rule, RulePack

# A threshold group is sugar, not power: it must expand to something a human
# could have written out by hand. 128 is well above anything the Amex guide
# actually asks for (its widest is "three (3) or more of" four items = 4
# clauses, and CE3.0's "2 of 4" = 6) and far below the point where a
# rulepack's prime-implicant enumeration or the exhaustive property tests
# would quietly become the slowest thing in CI.
MAX_THRESHOLD_EXPANSION = 128

_CONDITION_OPERATORS = ("is", "equals", "one_of", "lte", "gte")


def _parse_literal(item: Any) -> Literal:
    if isinstance(item, str):
        if item.startswith("not "):
            return Literal(predicate=item[4:].strip(), negated=True)
        return Literal(predicate=item.strip(), negated=False)
    raise ValueError(f"unrecognised body literal: {item!r}")


def _parse_body(rule_id: str, raw_body: Sequence[Any]) -> List[Tuple[Literal, ...]]:
    """Expand one authored body into the conjunctive bodies it denotes.

    A body with no threshold group yields exactly one body -- the common
    case, and byte-identical to what the previous loader produced, so no
    existing rulepack's content hash moves.

    Why expand at load time instead of teaching the engine about
    thresholds: a threshold literal is not a Horn clause, and the moment
    `arbiter.horn` understands one, prime-implicant enumeration
    (`horn/implicants.py`) stops being enumeration over a finite literal set
    and the counterfactual ledger stops being able to say "make exactly
    these literals true". The guide's compelling-evidence sections are
    written as "provide three (3) or more of the following" precisely
    because a human is meant to pick a combination; expanding to the
    combinations keeps every downstream explanation talking about the
    combination that actually fired, which is the one the merchant needs
    to hear about.
    """
    fixed: List[Literal] = []
    groups: List[List[Tuple[Literal, ...]]] = []

    for item in raw_body:
        if isinstance(item, str):
            fixed.append(_parse_literal(item))
            continue
        if isinstance(item, dict) and set(item) == {"at_least"}:
            spec = item["at_least"]
            if not isinstance(spec, dict) or "n" not in spec or "of" not in spec:
                raise RulepackError(f"rule {rule_id}: at_least requires {{n, of}}, got {spec!r}")
            n = int(spec["n"])
            members = [_parse_literal(m) for m in spec["of"]]
            if n < 1 or n > len(members):
                raise RulepackError(
                    f"rule {rule_id}: at_least n={n} is not satisfiable over {len(members)} members"
                )
            groups.append([tuple(combo) for combo in itertools.combinations(members, n)])
            continue
        raise RulepackError(f"rule {rule_id}: unrecognised body item {item!r}")

    if not groups:
        return [tuple(fixed)]

    total = 1
    for g in groups:
        total *= len(g)
    if total > MAX_THRESHOLD_EXPANSION:
        raise RulepackError(
            f"rule {rule_id}: threshold groups expand to {total} clauses, over the "
            f"{MAX_THRESHOLD_EXPANSION} limit -- express this as separate rules instead"
        )

    bodies: List[Tuple[Literal, ...]] = []
    seen: set = set()
    for combination in itertools.product(*groups):
        literals: List[Literal] = list(fixed)
        for chosen in combination:
            literals.extend(chosen)

        # Deduplicate while preserving authored order: a threshold member
        # that is also a fixed conjunct is a redundant mention, not an error.
        deduped: List[Literal] = []
        polarity: Dict[str, bool] = {}
        for lit in literals:
            if lit.predicate in polarity:
                if polarity[lit.predicate] != lit.negated:
                    raise RulepackError(
                        f"rule {rule_id}: threshold expansion produces a body containing both "
                        f"{lit.predicate!r} and 'not {lit.predicate}' -- unsatisfiable by "
                        f"construction, so the authored groups overlap in polarity"
                    )
                continue
            polarity[lit.predicate] = lit.negated
            deduped.append(lit)

        key = frozenset(lit.key() for lit in deduped)
        if key in seen:
            continue
        seen.add(key)
        bodies.append(tuple(deduped))

    return bodies


def _parse_predicate_meta(doc: Dict[str, Any]) -> Dict[str, PredicateMeta] | None:
    """Optional `predicates:` block: a list of
    `{id, party, min_tier}` documenting who each EDB predicate favours and
    the provenance tier gate it's derived under (C2). Older/simpler
    rulepacks may omit this and rely on `predicate_schema` alone; when
    present it's carried through for the UI and fairness lint, not
    consulted by the engine itself (tier gating happens at derivation,
    arbiter.evidence.derive, before a Fact ever reaches the Horn engine)."""
    raw = doc.get("predicates")
    if not raw:
        return None
    meta: Dict[str, PredicateMeta] = {}
    for item in raw:
        pid = item["id"]
        meta[pid] = PredicateMeta(
            predicate=pid,
            party=item.get("party", "NEUTRAL"),
            min_tier=item.get("min_tier", "ASSERTED"),
        )
    return meta


def _parse_condition(owner: str, raw: Dict[str, Any]) -> Condition:
    if "attribute" not in raw:
        raise RulepackError(f"{owner}: condition is missing `attribute`: {raw!r}")
    present = [op for op in _CONDITION_OPERATORS if op in raw]
    if len(present) != 1:
        raise RulepackError(
            f"{owner}: condition on {raw['attribute']!r} must use exactly one of "
            f"{list(_CONDITION_OPERATORS)}, found {present or 'none'}"
        )
    operator = present[0]
    return Condition(attribute=str(raw["attribute"]), operator=operator, value=raw[operator])


def _parse_filing_window(owner: str, raw: Sequence[Any]) -> Tuple[FilingWindowBranch, ...]:
    branches: List[FilingWindowBranch] = []
    for item in raw:
        anchor = item.get("from")
        if anchor is None:
            raise RulepackError(f"{owner}: filing_window branch is missing `from`: {item!r}")
        anchors = tuple(anchor) if isinstance(anchor, (list, tuple)) else (str(anchor),)
        branches.append(FilingWindowBranch(
            branch_id=str(item["branch_id"]),
            days=int(item["days"]),
            from_attributes=anchors,
            description=str(item.get("description", "")),
            absolute_cap_days=int(item["absolute_cap_days"]) if item.get("absolute_cap_days") else None,
            cap_from_attribute=str(item["cap_from"]) if item.get("cap_from") else None,
        ))
    return tuple(branches)


def _parse_chargeback_right(doc: Dict[str, Any]) -> ChargebackRight | None:
    raw = doc.get("chargeback_right")
    if not raw:
        return None
    owner = f"{doc.get('reason_code', '?')} chargeback_right"

    exclusions: List[Exclusion] = []
    for item in raw.get("exclusions", []) or []:
        conditions = tuple(
            _parse_condition(f"{owner}/{item.get('id', '?')}", c) for c in item.get("when", []) or []
        )
        if not conditions:
            raise RulepackError(
                f"{owner}/{item.get('id', '?')}: an exclusion with no `when` conditions would "
                f"exclude every transaction under this reason code"
            )
        exclusions.append(Exclusion(
            exclusion_id=str(item["id"]),
            description=str(item.get("description", "")),
            legal_basis=str(item.get("legal_basis", "")),
            conditions=conditions,
        ))

    return ChargebackRight(
        network_code=str(raw["network_code"]),
        merchant_challenge_days=int(raw.get("merchant_challenge_days", 20)),
        filing_window=_parse_filing_window(owner, raw.get("filing_window", []) or []),
        exclusions=tuple(exclusions),
        source=str(raw.get("source", "")),
    )


def parse_rulepack(doc: Dict[str, Any]) -> RulePack:
    rules = []
    for r in doc["rules"]:
        rule_id = r["rule_id"]
        bodies = _parse_body(rule_id, r["body"])
        multi = len(bodies) > 1
        for index, body in enumerate(bodies, start=1):
            rules.append(
                Rule(
                    # A threshold group's expansion keeps the authored id as a
                    # prefix, so a fired rule is still traceable to the clause a
                    # human wrote -- `F29_CE3_DEVICE_ANCHOR#2` reads back to the
                    # `at_least` group it came from, and the fairness layer's
                    # per-rule disparate-impact analysis still groups sensibly.
                    rule_id=f"{rule_id}#{index}" if multi else rule_id,
                    head=r["head"],
                    body=body,
                    description=r.get("description", ""),
                    legal_basis=r.get("legal_basis", ""),
                )
            )
    return RulePack(
        rulepack_id=doc["rulepack_id"],
        reason_code=doc["reason_code"],
        version=str(doc["version"]),
        rules=tuple(rules),
        decision_predicates=dict(doc["decision_predicates"]),
        predicate_schema=tuple(doc.get("predicate_schema", [])),
        predicate_meta=_parse_predicate_meta(doc),
        chargeback_right=_parse_chargeback_right(doc),
        # Optional, and absence is not an error: a rulepack without them is
        # still a complete decision function. The catalogue endpoint falls
        # back to the reason code itself, which is worse copy but never a
        # missing entry -- a rulepack the engine can adjudicate must never be
        # one the console cannot list.
        title=str(doc.get("title", "") or ""),
        description=str(doc.get("description", "") or ""),
    )


def load_rulepack(path: str | Path) -> RulePack:
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    return parse_rulepack(doc)


def load_rulepack_dir(dir_path: str | Path) -> Dict[str, RulePack]:
    """Load every *.yaml rulepack in a directory, keyed by reason_code."""
    dir_path = Path(dir_path)
    packs: Dict[str, RulePack] = {}
    for f in sorted(dir_path.glob("*.yaml")):
        pack = load_rulepack(f)
        packs[pack.reason_code] = pack
    return packs

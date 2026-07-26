"""Loads a rulepack from its YAML source into the typed Rule/RulePack model.

Rulepacks are data, not code: this loader is the only place that ever
interprets rulepack YAML, and it does no evaluation itself -- content-
addressing (RulePack.content_hash()) is computed over the parsed structure,
not the YAML text, so formatting changes (whitespace, comments, key order)
never change a rulepack's hash or invalidate pinned decisions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

from arbiter.horn.clause import Literal, PredicateMeta, Rule, RulePack


def _parse_literal(item: Any) -> Literal:
    if isinstance(item, str):
        if item.startswith("not "):
            return Literal(predicate=item[4:].strip(), negated=True)
        return Literal(predicate=item.strip(), negated=False)
    raise ValueError(f"unrecognised body literal: {item!r}")


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


def parse_rulepack(doc: Dict[str, Any]) -> RulePack:
    rules = []
    for r in doc["rules"]:
        body = tuple(_parse_literal(item) for item in r["body"])
        rules.append(
            Rule(
                rule_id=r["rule_id"],
                head=r["head"],
                body=body,
                description=r.get("description", ""),
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

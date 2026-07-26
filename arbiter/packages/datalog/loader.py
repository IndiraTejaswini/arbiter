"""Loads a rulepack from its YAML source into the typed Rule/RulePack model.

Rulepacks are data, not code (§9.8, §12.2): this loader is the only place
that ever interprets rulepack YAML, and it does no evaluation itself --
content-addressing (RulePack.content_hash()) is computed over the parsed
structure, not the YAML text, so formatting changes (whitespace, comments,
key order) never change a rulepack's hash or invalidate pinned decisions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

from .engine import Literal, Rule, RulePack


def _parse_literal(item: Any) -> Literal:
    if isinstance(item, str):
        if item.startswith("not "):
            return Literal(predicate=item[4:].strip(), negated=True)
        return Literal(predicate=item.strip(), negated=False)
    raise ValueError(f"unrecognised body literal: {item!r}")


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

"""
Objective predicate derivation: the mechanical evidence-graph -> Horn-fact
boundary (C2, C3).

A predicate is satisfied iff (1) some evidence node asserts it, and (2) that
node's provenance tier meets the predicate's `min_tier` gate, if the loaded
rulepack declares one (arbiter.horn.clause.PredicateMeta). Tier gating is
enforced HERE, once, at the boundary -- not re-checked ad hoc downstream --
so a `min_tier: NETWORK` predicate can never be satisfied by a SUBMITTED
node however well its attributes match (C2).

Any evidence node may assert a rulepack predicate directly via
`attrs['asserts_predicate']` / `attrs['predicate_value']` -- this is what
evidence-ingest's structured extraction, or the network loader, ultimately
produces. Multiple nodes may assert the same predicate, possibly
disagreeing; conflicts are resolved by provenance trust weight (COMMITTED >
NETWORK > SUBMITTED > ASSERTED), not by recency or by which party submitted
it -- so a merchant's own claim never outweighs a higher-provenance
contradiction (C3: evaluate what exists, not what was submitted). A genuine
tie (equal trust weight on both sides) resolves to UNKNOWN rather than
picking a side, and is itself worth surfacing to a human.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from arbiter.horn.clause import RulePack
from arbiter.horn.proof import Fact, FactStatus

from .graph import EvidenceGraph
from .models import EvidenceNode, ProvenanceTier

_TIER_ORDER = [ProvenanceTier.ASSERTED, ProvenanceTier.SUBMITTED, ProvenanceTier.NETWORK, ProvenanceTier.COMMITTED]


def _min_tier_for(rulepack: RulePack, predicate: str) -> Optional[ProvenanceTier]:
    if not rulepack.predicate_meta:
        return None
    meta = rulepack.predicate_meta.get(predicate)
    if meta is None:
        return None
    try:
        return ProvenanceTier[meta.min_tier]
    except KeyError:
        return None


def derive_predicate_facts(graph: EvidenceGraph, rulepack: RulePack) -> Dict[str, Fact]:
    predicate_schema = rulepack.predicate_schema or tuple(rulepack.edb_predicates())

    true_nodes: Dict[str, List[EvidenceNode]] = {}
    false_nodes: Dict[str, List[EvidenceNode]] = {}
    for n in graph.nodes.values():
        pred = n.attrs.get("asserts_predicate")
        if pred is None:
            continue
        value = bool(n.attrs.get("predicate_value", True))
        bucket = true_nodes if value else false_nodes
        bucket.setdefault(pred, []).append(n)

    facts: Dict[str, Fact] = {}
    for pred in predicate_schema:
        min_tier = _min_tier_for(rulepack, pred)

        def _tier_ok(n: EvidenceNode) -> bool:
            return min_tier is None or n.provenance.meets(min_tier)

        t_group = [n for n in true_nodes.get(pred, []) if _tier_ok(n)]
        f_group = [n for n in false_nodes.get(pred, []) if _tier_ok(n)]
        if not t_group and not f_group:
            facts[pred] = Fact(pred, FactStatus.UNKNOWN, ())
            continue
        t_weight = max((n.provenance.trust_weight for n in t_group), default=-1.0)
        f_weight = max((n.provenance.trust_weight for n in f_group), default=-1.0)
        if t_weight > f_weight:
            facts[pred] = Fact(
                pred, FactStatus.TRUE, tuple(n.node_id for n in t_group), confidence=min(1.0, t_weight)
            )
        elif f_weight > t_weight:
            facts[pred] = Fact(
                pred, FactStatus.FALSE, tuple(n.node_id for n in f_group), confidence=min(1.0, f_weight)
            )
        else:
            facts[pred] = Fact(
                pred, FactStatus.UNKNOWN, tuple(n.node_id for n in t_group + f_group), confidence=0.0
            )
    return facts

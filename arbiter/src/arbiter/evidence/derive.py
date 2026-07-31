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

from typing import Dict, List, Optional

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


def _node_weight(node: EvidenceNode) -> float:
    """Evidentiary weight of one node: provenance tier scaled by how well
    the value was actually read off the artifact.

    `extract_conf` carries the OCR/VLM field confidence multiplied by
    `arbiter.ingest.forensics`' tamper penalty (a PDF whose ModDate
    postdates the dispute filing, a suspicious producer tag, an incremental
    update chain). Before this it was computed, persisted, displayed -- and
    read by nothing that affected an outcome, because `Fact.confidence` was
    set from `provenance.trust_weight` alone. A forged document and a clean
    one produced byte-identical confidence, an identical abstention
    decision, and an identical verdict, which made the entire tamper-
    forensics layer decorative.

    Multiplicative rather than a floor: a NETWORK-tier fact read cleanly
    (extract_conf 1.0, the case for everything `arbiter.network.loader`
    emits) is unchanged, while a SUBMITTED document flagged by two forensic
    signals loses 40% of its weight. Note what this does and does not do --
    it changes the CONFIDENCE and therefore the abstention decision; it
    never changes whether the predicate is true. Forensics are a signal for
    a human, never a verdict (see forensics.py's module docstring).
    """
    return node.provenance.trust_weight * max(0.0, min(1.0, node.extract_conf))


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

        # `min_tier` bound as a default rather than captured: a closure over
        # a loop variable is evaluated late, so if this predicate ever grew a
        # deferred call site it would silently apply the LAST predicate's
        # tier gate to every predicate. It is used eagerly today, which is
        # exactly why the bug would be invisible until it wasn't.
        def _tier_ok(n: EvidenceNode, min_tier: Optional[ProvenanceTier] = min_tier) -> bool:
            return min_tier is None or n.provenance.meets(min_tier)

        t_group = [n for n in true_nodes.get(pred, []) if _tier_ok(n)]
        f_group = [n for n in false_nodes.get(pred, []) if _tier_ok(n)]
        if not t_group and not f_group:
            facts[pred] = Fact(pred, FactStatus.UNKNOWN, ())
            continue
        t_weight = max((_node_weight(n) for n in t_group), default=-1.0)
        f_weight = max((_node_weight(n) for n in f_group), default=-1.0)
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

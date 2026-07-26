"""
Evidence graph orchestrator (§7.3, §A6): holds the per-case property graph
and runs all four contradiction layers over it, materializing findings as
first-class Contradiction nodes with `contradicts` edges -- exactly as
§A6 specifies ("Contradictions become first-class graph nodes with
severity. They gate rules...").

Contrarian call per §7.3: no Neo4j. A per-case graph has 10^2-10^3 nodes;
plain Python containers (the in-process equivalent of a recursive CTE over
an adjacency table) traverse that in microseconds. This class *is* the
"plain adjacency + recursive CTE" option the doc names as the zero-extension
default.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from packages.evidence_schema.types import (
    EdgeType,
    EvidenceEdge,
    EvidenceNode,
    EvidenceNodeType,
    ProvenanceTier,
)

from packages.datalog.engine import Fact, FactStatus

from .identity import IdentityAssertion, detect_identity_incoherence
from .numeric import MoneyAmount, reconcile_chain
from .semantic import SemanticClaim, detect_semantic_contradictions
from .temporal import TemporalFact, TimeInterval, detect_temporal_contradictions


@dataclass(frozen=True)
class Contradiction:
    kind: str
    severity: str  # LOW | MEDIUM | HIGH
    description: str
    node_ids: Tuple[str, ...]
    layer: str  # temporal | numeric | identity | semantic


_SEVERITY_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


class EvidenceGraph:
    def __init__(self, case_id: str):
        self.case_id = case_id
        self.nodes: Dict[str, EvidenceNode] = {}
        self.edges: List[EvidenceEdge] = []
        self.contradictions: List[Contradiction] = []
        self.contradiction_nodes: List[EvidenceNode] = []

    # -- graph construction ------------------------------------------------

    def add_node(self, node: EvidenceNode) -> EvidenceNode:
        assert node.case_id == self.case_id
        self.nodes[node.node_id] = node
        return node

    def add_edge(self, edge: EvidenceEdge) -> EvidenceEdge:
        assert edge.case_id == self.case_id
        self.edges.append(edge)
        return edge

    def nodes_of_type(self, node_type: EvidenceNodeType) -> List[EvidenceNode]:
        return [n for n in self.nodes.values() if n.node_type == node_type]

    # -- extraction of typed facts from graph node attrs --------------------
    # Convention: nodes carry the fields each contradiction layer needs
    # directly in `attrs`, under the keys read below. In a full deployment
    # this is the boundary where evidence-ingest's structured extraction
    # output lands in the graph; here the synthetic case generator
    # populates it directly.

    def _extract_temporal_facts(self) -> List[TemporalFact]:
        out = []
        for n in self.nodes.values():
            fact_key = n.attrs.get("temporal_fact_key")
            ts = n.attrs.get("temporal_value")
            if fact_key and ts is not None:
                out.append(
                    TemporalFact(
                        fact_key=fact_key,
                        node_id=n.node_id,
                        interval=TimeInterval(ts, ts),
                        provenance_weight=n.provenance.trust_weight,
                    )
                )
        return out

    def _extract_money_amounts(self) -> Dict[str, MoneyAmount]:
        out: Dict[str, MoneyAmount] = {}
        for n in self.nodes.values():
            role = n.attrs.get("money_role")
            if role and "minor_units" in n.attrs and "currency" in n.attrs:
                out[role] = MoneyAmount(
                    minor_units=n.attrs["minor_units"],
                    currency=n.attrs["currency"],
                    node_id=n.node_id,
                    label=role,
                )
        return out

    def _extract_identity_assertions(self) -> List[IdentityAssertion]:
        out = []
        for n in self.nodes.values():
            key = n.attrs.get("identity_key")
            value = n.attrs.get("identity_value")
            if key and value:
                out.append(
                    IdentityAssertion(
                        entity_key=key, value=value, node_id=n.node_id,
                        source=n.attrs.get("identity_source", n.node_type.value),
                    )
                )
        return out

    def _extract_semantic_claims(self) -> List[SemanticClaim]:
        out = []
        for n in self.nodes.values():
            subject = n.attrs.get("claim_subject")
            polarity = n.attrs.get("claim_polarity")
            if subject is not None and polarity is not None:
                out.append(
                    SemanticClaim(
                        subject_key=subject, polarity=bool(polarity), node_id=n.node_id,
                        source_text=n.attrs.get("claim_text", ""),
                    )
                )
        return out

    # -- orchestration -------------------------------------------------------

    def run_contradiction_analysis(self) -> List[Contradiction]:
        found: List[Contradiction] = []

        for c in detect_temporal_contradictions(self._extract_temporal_facts()):
            found.append(Contradiction(c.kind, c.severity, c.description, c.node_ids, "temporal"))

        money = self._extract_money_amounts()
        for c in reconcile_chain(
            order_total=money.get("order_total"),
            authorization=money.get("authorization"),
            settlement=money.get("settlement"),
            refund=money.get("refund"),
        ):
            found.append(Contradiction(c.kind, c.severity, c.description, c.node_ids, "numeric"))

        for c in detect_identity_incoherence(self._extract_identity_assertions()):
            found.append(Contradiction(c.kind, c.severity, c.description, c.node_ids, "identity"))

        for c in detect_semantic_contradictions(self._extract_semantic_claims()):
            found.append(Contradiction(c.kind, c.severity, c.description, c.node_ids, "semantic"))

        self.contradictions = found
        self._materialize_contradiction_nodes(found)
        return found

    def _materialize_contradiction_nodes(self, contradictions: List[Contradiction]) -> None:
        """§A6: contradictions become first-class graph nodes with severity,
        linked via `contradicts` edges to every node they implicate."""
        self.contradiction_nodes = []
        for c in contradictions:
            cnode = EvidenceNode(
                case_id=self.case_id,
                node_type=EvidenceNodeType.CONTRADICTION,
                attrs={"kind": c.kind, "severity": c.severity, "description": c.description, "layer": c.layer},
                provenance=ProvenanceTier.ASSERTED,
            )
            self.add_node(cnode)
            self.contradiction_nodes.append(cnode)
            for nid in c.node_ids:
                self.add_edge(
                    EvidenceEdge(
                        case_id=self.case_id, edge_type=EdgeType.CONTRADICTS,
                        from_node_id=cnode.node_id, to_node_id=nid,
                    )
                )

    def unresolved_severity(self) -> Optional[str]:
        if not self.contradictions:
            return None
        return max(self.contradictions, key=lambda c: _SEVERITY_RANK[c.severity]).severity

    def has_unresolved_at_or_above(self, severity: str) -> bool:
        threshold = _SEVERITY_RANK[severity]
        return any(_SEVERITY_RANK[c.severity] >= threshold for c in self.contradictions)

    def derive_predicate_facts(self, predicate_schema: Tuple[str, ...]) -> Dict[str, Fact]:
        """
        The objective, mechanical L2->L3 boundary: any evidence node may
        assert a rulepack predicate directly via `attrs['asserts_predicate']`
        / `attrs['predicate_value']` (this is what evidence-ingest's
        structured extraction ultimately produces in a full deployment).
        Multiple nodes may assert the same predicate, possibly disagreeing;
        conflicts are resolved by provenance trust weight (§7.3's
        provenance_tier ordering: COMMITTED > NETWORK > SUBMITTED >
        ASSERTED), not by recency or by which party submitted it -- so a
        merchant's own claim never outweighs a higher-provenance contradiction.
        A genuine tie (equal trust weight on both sides) resolves to UNKNOWN
        rather than picking a side, and is itself worth surfacing to a human.
        """
        true_nodes: Dict[str, List[EvidenceNode]] = {}
        false_nodes: Dict[str, List[EvidenceNode]] = {}
        for n in self.nodes.values():
            pred = n.attrs.get("asserts_predicate")
            if pred is None:
                continue
            value = bool(n.attrs.get("predicate_value", True))
            bucket = true_nodes if value else false_nodes
            bucket.setdefault(pred, []).append(n)

        facts: Dict[str, Fact] = {}
        for pred in predicate_schema:
            t_group = true_nodes.get(pred, [])
            f_group = false_nodes.get(pred, [])
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

    def to_timeline_dict(self) -> dict:
        """GET /v1/cases/{id}/timeline (§12.3)."""
        return {
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "edges": [e.to_dict() for e in self.edges],
            "contradictions": [
                {"kind": c.kind, "severity": c.severity, "description": c.description,
                 "node_ids": list(c.node_ids), "layer": c.layer}
                for c in self.contradictions
            ],
            "unresolved_severity": self.unresolved_severity(),
        }

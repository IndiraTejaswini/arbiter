"""
Evidence graph orchestrator (A6): holds the per-case property graph and runs
all four contradiction layers over it, materializing findings as first-class
Contradiction nodes with `contradicts` edges.

Contrarian call: no Neo4j. A per-case graph has 10^2-10^3 nodes; plain Python
containers (the in-process equivalent of a recursive CTE over an adjacency
table) traverse that in microseconds. In the FastAPI service layer
(arbiter.decision.adjudicate) this graph is rebuilt per-request from the
`evidence_node` / `evidence_edge` tables; it is never itself the store of
record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .contradiction import SEVERITY_RANK, Contradiction
from .identity import IdentityAssertion, detect_identity_incoherence
from .models import EdgeType, EvidenceEdge, EvidenceNode, EvidenceNodeType, ProvenanceTier
from .numeric import MoneyAmount, reconcile_chain
from .semantic import SemanticClaim, analyze_semantic_claims
from .temporal import TemporalFact, TimeInterval, detect_temporal_contradictions

# The four mandatory layers, in pipeline order. Named here so the set is
# closed and greppable rather than implied by the body of one function.
MANDATORY_LAYERS: Tuple[str, ...] = ("temporal", "numeric", "identity", "semantic")


def _as_datetime(value: Any) -> Optional[datetime]:
    """Accept a timestamp as either a datetime or its ISO-8601 form.

    Defence in depth at the layer that matters. `evidence_node.attrs` is a
    JSONB column, so a node re-read from Postgres carries `temporal_value`
    as a string even though the network loader produced a datetime. Handing
    that string to `TimeInterval` would compare TEXT where Allen interval
    algebra means to compare instants -- and lexicographic comparison of
    mixed-offset ISO strings is wrong without ever raising, which is the
    worst available failure mode for a check that is supposed to be
    mandatory.

    `arbiter.api.orchestration` re-hydrates these on read, so in the normal
    path this is a no-op. It is here as well because a silently-degraded
    contradiction layer is precisely the defect this system is built to make
    impossible, and one boundary enforcing that is one boundary somebody can
    forget to route a new caller through.
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


@dataclass(frozen=True)
class ContradictionAnalysis:
    """Result of the four-layer mandatory pipeline, with per-layer status.

    The status matters as much as the findings: an empty contradiction list
    is ambiguous between "this case is clean" and "a mandatory check could
    not run", and collapsing those two is how the semantic layer sat dead
    for an entire build while reporting success.
    """

    contradictions: List[Contradiction] = field(default_factory=list)
    layer_status: Dict[str, str] = field(default_factory=dict)
    unavailable_layers: Tuple[str, ...] = ()
    unavailable_reason: Optional[str] = None
    semantic_pairs_evaluated: int = 0

    @property
    def complete(self) -> bool:
        """True when every mandatory layer either ran or had nothing
        applicable to examine."""
        return not self.unavailable_layers

    @property
    def must_escalate(self) -> bool:
        """A mandatory check that could not run is an unknown, and an
        unknown blocks auto-resolution exactly like an unresolved HIGH
        contradiction does."""
        return bool(self.unavailable_layers)

    def to_dict(self) -> dict:
        return {
            "complete": self.complete,
            "must_escalate": self.must_escalate,
            "layer_status": dict(self.layer_status),
            "unavailable_layers": list(self.unavailable_layers),
            "unavailable_reason": self.unavailable_reason,
            "semantic_pairs_evaluated": self.semantic_pairs_evaluated,
            "contradictions": [
                {"kind": c.kind, "severity": c.severity, "layer": c.layer,
                 "description": c.description, "node_ids": list(c.node_ids)}
                for c in self.contradictions
            ],
        }


class EvidenceGraph:
    def __init__(self, case_id: str):
        self.case_id = case_id
        self.nodes: Dict[str, EvidenceNode] = {}
        self.edges: List[EvidenceEdge] = []
        self.contradictions: List[Contradiction] = []
        self.contradiction_nodes: List[EvidenceNode] = []
        self.last_analysis: Optional[ContradictionAnalysis] = None

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
    # directly in `attrs`, under the keys read below. This is the boundary
    # where evidence-ingest's structured extraction output (or the network
    # loader's synthesized nodes) lands in the graph.

    def _extract_temporal_facts(self) -> List[TemporalFact]:
        out = []
        for n in self.nodes.values():
            fact_key = n.attrs.get("temporal_fact_key")
            ts = _as_datetime(n.attrs.get("temporal_value"))
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
        """Claims the NLI layer can compare.

        Gated on `claim_text` rather than on `claim_polarity`: the engine is
        a text cross-encoder, so a claim with no text is not something it
        can classify. The old gate required a boolean polarity flag -- which
        was both the heuristic the layer no longer uses and an attribute
        nothing in the system ever populated.
        """
        out = []
        for n in self.nodes.values():
            subject = n.attrs.get("claim_subject")
            text = n.attrs.get("claim_text")
            if subject is not None and text:
                out.append(
                    SemanticClaim(
                        subject_key=str(subject), node_id=n.node_id, source_text=str(text),
                        polarity=n.attrs.get("claim_polarity"),
                    )
                )
        return out

    # -- orchestration -------------------------------------------------------

    def run_contradiction_analysis(self) -> List[Contradiction]:
        """Run all four MANDATORY layers. Returns findings only; call
        `analyze_contradictions` when you need the per-layer status too
        (the adjudication pipeline does -- see ContradictionAnalysis)."""
        return self.analyze_contradictions().contradictions

    def analyze_contradictions(self) -> "ContradictionAnalysis":
        """The four-layer deterministic contradiction pipeline (A6).

        ALL FOUR LAYERS ARE MANDATORY. None is optional, none is
        conditional on configuration, and none may be skipped:

            1. Temporal  -- Allen interval algebra + domain ordering
            2. Numeric   -- order -> authorization -> settlement -> refund
            3. Identity  -- address / device / IP / email coherence
            4. Semantic  -- DeBERTa-v3-MNLI cross-encoder, exclusively

        No layer uses a generative model. The semantic layer's engine is
        DeBERTa-NLI and nothing else (see arbiter.evidence.nli for why an
        LLM at this boundary would be the one unguarded LLM in the system).

        A layer that CANNOT RUN is not a layer that found nothing. The
        returned analysis carries per-layer status, and
        `must_escalate` is True when a mandatory layer was unable to
        examine evidence it should have -- which blocks auto-resolution the
        same way an unresolved HIGH contradiction does. Previously the
        semantic layer ran on every case, found nothing on every case
        (because nothing populated its inputs), and was indistinguishable
        from being switched off.
        """
        found: List[Contradiction] = []
        layer_status: Dict[str, str] = {}

        # -- Layer 1: temporal (Allen interval algebra) -------------------
        temporal_facts = self._extract_temporal_facts()
        for c in detect_temporal_contradictions(temporal_facts):
            found.append(Contradiction(c.kind, c.severity, c.description, c.node_ids, "temporal"))
        layer_status["temporal"] = "OK" if temporal_facts else "NOT_APPLICABLE"

        # -- Layer 2: numeric reconciliation ------------------------------
        money = self._extract_money_amounts()
        for c in reconcile_chain(
            order_total=money.get("order_total"),
            authorization=money.get("authorization"),
            settlement=money.get("settlement"),
            refund=money.get("refund"),
        ):
            found.append(Contradiction(c.kind, c.severity, c.description, c.node_ids, "numeric"))
        layer_status["numeric"] = "OK" if money else "NOT_APPLICABLE"

        # -- Layer 3: identity coherence ----------------------------------
        identity_assertions = self._extract_identity_assertions()
        for c in detect_identity_incoherence(identity_assertions):
            found.append(Contradiction(c.kind, c.severity, c.description, c.node_ids, "identity"))
        layer_status["identity"] = "OK" if identity_assertions else "NOT_APPLICABLE"

        # -- Layer 4: semantic (DeBERTa-NLI ONLY) -------------------------
        semantic = analyze_semantic_claims(self._extract_semantic_claims())
        for c in semantic.contradictions:
            found.append(Contradiction(c.kind, c.severity, c.description, c.node_ids, "semantic"))
        layer_status["semantic"] = semantic.status.value

        self.contradictions = found
        self._materialize_contradiction_nodes(found)

        analysis = ContradictionAnalysis(
            contradictions=found,
            layer_status=layer_status,
            unavailable_layers=(("semantic",) if semantic.must_escalate else ()),
            unavailable_reason=semantic.unavailable_reason,
            semantic_pairs_evaluated=semantic.pairs_evaluated,
        )
        self.last_analysis = analysis
        return analysis

    def _materialize_contradiction_nodes(self, contradictions: List[Contradiction]) -> None:
        """A6: contradictions become first-class graph nodes with severity,
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
        return max(self.contradictions, key=lambda c: SEVERITY_RANK[c.severity]).severity

    def has_unresolved_at_or_above(self, severity: str) -> bool:
        threshold = SEVERITY_RANK[severity]
        return any(SEVERITY_RANK[c.severity] >= threshold for c in self.contradictions)

    def to_timeline_dict(self) -> dict:
        """GET /v1/cases/{id}/timeline."""
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

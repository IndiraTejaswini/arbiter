"""
Shared evidence-graph types. One property graph, stored as typed rows -- the
Postgres `evidence_node` / `evidence_edge` tables (arbiter.db.models) are the
persisted form of exactly these dataclasses; this module is their in-process
equivalent and the shape everything else in `arbiter.evidence` operates on.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional, Tuple


class EvidenceNodeType(Enum):
    TRANSACTION = "transaction"
    AUTHORIZATION = "authorization"
    SETTLEMENT = "settlement"
    AVS_RESULT = "avs_result"
    CVV_RESULT = "cvv_result"
    THREE_DS_RESULT = "three_ds_result"
    DEVICE_SESSION = "device_session"
    DESCRIPTOR = "descriptor"
    PRIOR_TRANSACTION = "prior_transaction"
    ORDER = "order"
    LINE_ITEM = "line_item"
    SHIPMENT = "shipment"
    DELIVERY_SCAN = "delivery_scan"
    SIGNATURE_CAPTURE = "signature_capture"
    COMMUNICATION = "communication"
    TERMS_ACCEPTANCE = "terms_acceptance"
    REFUND_POLICY = "refund_policy"
    REFUND = "refund"
    CREDIT = "credit"
    ADDRESS = "address"
    IDENTITY = "identity"
    STATEMENT_LINE = "statement_line"
    SERVICE_ACCESS_LOG = "service_access_log"
    ATTESTATION = "attestation"
    CONTRADICTION = "contradiction"
    CLAIM = "claim"


class EdgeType(Enum):
    CORROBORATES = "corroborates"
    CONTRADICTS = "contradicts"
    DERIVED_FROM = "derived_from"
    ATTESTS_TO = "attests_to"
    PRECEDES = "precedes"
    OVERLAPS = "overlaps"
    REFERENCES = "references"
    SUPERSEDES = "supersedes"


class ProvenanceTier(Enum):
    COMMITTED = "COMMITTED"  # ADEC-verified, predates dispute
    NETWORK = "NETWORK"      # Amex-held (auth, settle, AVS, device)
    SUBMITTED = "SUBMITTED"  # party-supplied at dispute time, unverified
    ASSERTED = "ASSERTED"    # narrative claim, no artifact

    @property
    def trust_weight(self) -> float:
        """Relative evidentiary weight used by the graph/contradiction layer
        when two nodes conflict (A6): higher-provenance evidence wins ties."""
        return {
            ProvenanceTier.COMMITTED: 1.0,
            ProvenanceTier.NETWORK: 0.9,
            ProvenanceTier.SUBMITTED: 0.55,
            ProvenanceTier.ASSERTED: 0.3,
        }[self]

    def meets(self, min_tier: "ProvenanceTier") -> bool:
        """C2: a predicate's min_tier gate. COMMITTED > NETWORK > SUBMITTED
        > ASSERTED, so `self.meets(min_tier)` iff self is at least as
        trusted as min_tier."""
        order = [ProvenanceTier.ASSERTED, ProvenanceTier.SUBMITTED, ProvenanceTier.NETWORK, ProvenanceTier.COMMITTED]
        return order.index(self) >= order.index(min_tier)


@dataclass(frozen=True)
class SourceRef:
    """Not optional garnish: what makes a claim clickable back to a bounding
    box on a page. CLAUDE.md invariant #10: populated on every extracted
    field."""

    artifact_id: str
    page: Optional[int] = None
    bbox: Optional[Tuple[float, float, float, float]] = None
    char_span: Optional[Tuple[int, int]] = None

    def to_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "page": self.page,
            "bbox": list(self.bbox) if self.bbox else None,
            "char_span": list(self.char_span) if self.char_span else None,
        }


@dataclass
class EvidenceNode:
    case_id: str
    node_type: EvidenceNodeType
    attrs: Dict[str, Any]
    provenance: ProvenanceTier
    node_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    valid_time_start: Optional[datetime] = None
    valid_time_end: Optional[datetime] = None
    asserted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    commitment_id: Optional[str] = None
    extract_conf: float = 1.0
    artifact_id: Optional[str] = None
    source_ref: Optional[SourceRef] = None

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "case_id": self.case_id,
            "node_type": self.node_type.value,
            "attrs": self.attrs,
            "provenance": self.provenance.value,
            "valid_time_start": self.valid_time_start.isoformat() if self.valid_time_start else None,
            "valid_time_end": self.valid_time_end.isoformat() if self.valid_time_end else None,
            "asserted_at": self.asserted_at.isoformat(),
            "commitment_id": self.commitment_id,
            "extract_conf": self.extract_conf,
            "artifact_id": self.artifact_id,
            "source_ref": self.source_ref.to_dict() if self.source_ref else None,
        }


@dataclass
class EvidenceEdge:
    case_id: str
    edge_type: EdgeType
    from_node_id: str
    to_node_id: str
    edge_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    attrs: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "edge_id": self.edge_id,
            "case_id": self.case_id,
            "edge_type": self.edge_type.value,
            "from_node_id": self.from_node_id,
            "to_node_id": self.to_node_id,
            "attrs": self.attrs,
        }

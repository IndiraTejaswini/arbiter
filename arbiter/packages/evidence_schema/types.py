"""
Shared evidence-graph types (§7.3). One property graph, stored as typed rows
(the Postgres schema in §7.3/§12.4 is the reference; these dataclasses are
its in-process equivalent for this build, and are what a real
`evidence_node` / edge table would deserialize into).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional, Tuple


class EvidenceNodeType(Enum):
    TRANSACTION = "Transaction"
    AUTHORIZATION = "Authorization"
    ORDER = "Order"
    LINE_ITEM = "LineItem"
    SHIPMENT = "Shipment"
    DELIVERY_SCAN = "DeliveryScan"
    COMMUNICATION = "Communication"
    TERMS_ACCEPTANCE = "TermsAcceptance"
    REFUND_POLICY = "RefundPolicy"
    REFUND = "Refund"
    DEVICE_SESSION = "DeviceSession"
    ADDRESS = "Address"
    IDENTITY = "Identity"
    STATEMENT_LINE = "StatementLine"
    ATTESTATION = "Attestation"
    CONTRADICTION = "Contradiction"
    CLAIM = "Claim"


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
        when two nodes conflict (§A6): higher-provenance evidence wins ties."""
        return {
            ProvenanceTier.COMMITTED: 1.0,
            ProvenanceTier.NETWORK: 0.9,
            ProvenanceTier.SUBMITTED: 0.55,
            ProvenanceTier.ASSERTED: 0.3,
        }[self]


@dataclass(frozen=True)
class SourceRef:
    """Not optional garnish (§7.3): what makes a claim clickable back to a
    bounding box on a page."""

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
    asserted_at: datetime = field(default_factory=datetime.utcnow)
    commitment_id: Optional[str] = None
    extract_conf: float = 1.0
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

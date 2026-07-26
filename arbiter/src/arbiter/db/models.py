"""
SQLAlchemy 2.0 models mirroring the Postgres DDL 1:1. This is the store of
record; arbiter.evidence.EvidenceGraph / arbiter.horn.* dataclasses are
rebuilt from these rows per-request, never the other way around.

CLAUDE.md invariant #6: case_event and decision are append-only. The
`forbid_mutation()` trigger (alembic/versions/0001_initial_schema.py) is the
actual enforcement; the ORM layer additionally never exposes an update path
for these two tables (arbiter.audit only ever INSERTs).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    LargeBinary,
    Numeric,
    REAL,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSTZRANGE, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


# ============ ENUMS ============

class ProvenanceTierEnum(str, enum.Enum):
    COMMITTED = "COMMITTED"
    NETWORK = "NETWORK"
    SUBMITTED = "SUBMITTED"
    ASSERTED = "ASSERTED"


class CaseStateEnum(str, enum.Enum):
    INTAKE = "INTAKE"
    GATHERING = "GATHERING"
    AWAITING_EVIDENCE = "AWAITING_EVIDENCE"
    ANALYSING = "ANALYSING"
    ADJUDICATED = "ADJUDICATED"
    ESCALATED = "ESCALATED"
    SETTLED = "SETTLED"
    DEFLECTED = "DEFLECTED"
    REOPENED = "REOPENED"


class PartyEnum(str, enum.Enum):
    CARD_MEMBER = "CARD_MEMBER"
    MERCHANT = "MERCHANT"
    NEUTRAL = "NEUTRAL"


class OutcomeEnum(str, enum.Enum):
    CARD_MEMBER_PREVAILS = "CARD_MEMBER_PREVAILS"
    MERCHANT_PREVAILS = "MERCHANT_PREVAILS"
    SPLIT = "SPLIT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class ContradictionSeverityEnum(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class EvidenceNodeTypeEnum(str, enum.Enum):
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


# ============ CASE ============

class DisputeCase(Base):
    __tablename__ = "dispute_case"

    case_id: Mapped[uuid.UUID] = _uuid_pk()
    transaction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    card_member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    merchant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reason_code: Mapped[str] = mapped_column(String, nullable=False)
    intent_confidence: Mapped[Optional[float]] = mapped_column(REAL, nullable=True)  # from arbiter.intake, NULL if reason_code was given directly
    state: Mapped[CaseStateEnum] = mapped_column(
        Enum(CaseStateEnum, name="case_state", native_enum=True), nullable=False, default=CaseStateEnum.INTAKE
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger, CheckConstraint("amount_minor > 0"), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    filed_at: Mapped[datetime] = mapped_column(nullable=False)
    reg_regime: Mapped[str] = mapped_column(
        String, CheckConstraint("reg_regime IN ('REG_Z', 'REG_E')"), nullable=False
    )
    ack_deadline: Mapped[datetime] = mapped_column(nullable=False)
    resolve_deadline: Mapped[datetime] = mapped_column(nullable=False)
    merchant_response_deadline: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    merchant_responded: Mapped[bool] = mapped_column(nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_dispute_case_state_deadline", "state", "resolve_deadline"),
        Index("ix_dispute_case_merchant_filed", "merchant_id", "filed_at"),
    )


# ============ EVIDENCE ============

class Artifact(Base):
    __tablename__ = "artifact"

    artifact_id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dispute_case.case_id"), nullable=False)
    storage_key: Mapped[str] = mapped_column(String, nullable=False)
    sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    mime_type: Mapped[str] = mapped_column(String, nullable=False)  # SNIFFED, never Content-Type
    byte_size: Mapped[int] = mapped_column(BigInteger, CheckConstraint("byte_size <= 26214400"), nullable=False)
    uploaded_by: Mapped[PartyEnum] = mapped_column(Enum(PartyEnum, name="party", native_enum=True), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    scan_status: Mapped[str] = mapped_column(String, nullable=False, default="PENDING")
    forensics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class MerkleBatch(Base):
    __tablename__ = "merkle_batch"

    batch_id: Mapped[uuid.UUID] = _uuid_pk()
    root_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    tree_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sth_signature: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    tsa_token: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    sealed_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class AdecCommitmentRow(Base):
    __tablename__ = "adec_commitment"

    commitment_id: Mapped[uuid.UUID] = _uuid_pk()
    merchant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    commitment_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    artifact_type: Mapped[str] = mapped_column(String, nullable=False)
    event_time: Mapped[datetime] = mapped_column(nullable=False)  # merchant-claimed
    batch_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("merkle_batch.batch_id"), nullable=True)
    leaf_index: Mapped[Optional[int]] = mapped_column(nullable=True)
    committed_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)  # server-observed, AUTHORITATIVE
    revealed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    reveal_valid: Mapped[Optional[bool]] = mapped_column(nullable=True)

    __table_args__ = (
        UniqueConstraint("batch_id", "leaf_index"),
        Index("ix_adec_commitment_hash", "commitment_hash"),
    )


class EvidenceNodeRow(Base):
    __tablename__ = "evidence_node"

    node_id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dispute_case.case_id", ondelete="CASCADE"), nullable=False)
    node_type: Mapped[EvidenceNodeTypeEnum] = mapped_column(
        Enum(EvidenceNodeTypeEnum, name="evidence_node_type", native_enum=True), nullable=False
    )
    attrs: Mapped[dict] = mapped_column(JSONB, nullable=False)
    valid_time: Mapped[Optional[Any]] = mapped_column(TSTZRANGE, nullable=True)
    asserted_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    provenance: Mapped[ProvenanceTierEnum] = mapped_column(
        Enum(ProvenanceTierEnum, name="provenance_tier", native_enum=True), nullable=False
    )
    commitment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("adec_commitment.commitment_id"), nullable=True
    )
    extract_conf: Mapped[Optional[float]] = mapped_column(REAL, nullable=True)
    artifact_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("artifact.artifact_id"), nullable=True)
    source_ref: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "provenance != 'COMMITTED' OR commitment_id IS NOT NULL", name="committed_needs_commitment"
        ),
        CheckConstraint("extract_conf IS NULL OR (extract_conf BETWEEN 0 AND 1)"),
        Index("ix_evidence_node_case_type", "case_id", "node_type"),
    )


class EvidenceEdgeRow(Base):
    __tablename__ = "evidence_edge"

    edge_id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dispute_case.case_id", ondelete="CASCADE"), nullable=False)
    src: Mapped[uuid.UUID] = mapped_column(ForeignKey("evidence_node.node_id", ondelete="CASCADE"), nullable=False)
    dst: Mapped[uuid.UUID] = mapped_column(ForeignKey("evidence_node.node_id", ondelete="CASCADE"), nullable=False)
    rel: Mapped[str] = mapped_column(String, nullable=False)  # corroborates | contradicts | derived_from | ...
    weight: Mapped[Optional[float]] = mapped_column(REAL, nullable=True)

    __table_args__ = (UniqueConstraint("src", "dst", "rel"),)


class ContradictionRow(Base):
    __tablename__ = "contradiction"

    contradiction_id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dispute_case.case_id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)  # temporal | numeric | identity | semantic
    severity: Mapped[ContradictionSeverityEnum] = mapped_column(
        Enum(ContradictionSeverityEnum, name="contradiction_severity", native_enum=True), nullable=False
    )
    node_ids: Mapped[list] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False)
    resolved: Mapped[bool] = mapped_column(nullable=False, default=False)


# ============ DECISION ============

class DecisionRow(Base):
    __tablename__ = "decision"

    decision_id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dispute_case.case_id"), nullable=False)
    rulepack_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    outcome: Mapped[OutcomeEnum] = mapped_column(Enum(OutcomeEnum, name="outcome", native_enum=True), nullable=False)
    proof_tree: Mapped[dict] = mapped_column(JSONB, nullable=False)
    predicates: Mapped[dict] = mapped_column(JSONB, nullable=False)
    counterfactuals: Mapped[dict] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[float] = mapped_column(REAL, nullable=False)
    conformal_set: Mapped[list] = mapped_column(ARRAY(String), nullable=False)
    abstained: Mapped[bool] = mapped_column(nullable=False)
    escalation_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    merchant_silent: Mapped[bool] = mapped_column(nullable=False, default=False)  # R13-recovery metric
    llm_rejections: Mapped[int] = mapped_column(nullable=False, default=0)  # hallucinated advocate assertions caught this case
    decided_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    signature: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    __table_args__ = (
        Index("ix_decision_case_decided", "case_id", "decided_at"),
        Index("ix_decision_merchant_silent", "merchant_silent"),
    )


class RuleFiringRow(Base):
    __tablename__ = "rule_firing"

    decision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("decision.decision_id"), primary_key=True)
    rule_id: Mapped[str] = mapped_column(String, primary_key=True)
    fired: Mapped[bool] = mapped_column(nullable=False)


# ============ AUDIT ============

class CaseEventRow(Base):
    __tablename__ = "case_event"

    case_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    actor_id: Mapped[str] = mapped_column(String, nullable=False)
    actor_type: Mapped[str] = mapped_column(
        String, CheckConstraint("actor_type IN ('human','service','advocate','referee')"), nullable=False
    )
    rulepack_hash: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    prev_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    event_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    signature: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


# ============ CALIBRATION ============

class CalibrationSample(Base):
    __tablename__ = "calibration_sample"

    sample_id: Mapped[uuid.UUID] = _uuid_pk()
    reason_code: Mapped[str] = mapped_column(String, nullable=False)
    features: Mapped[dict] = mapped_column(JSONB, nullable=False)
    score: Mapped[float] = mapped_column(REAL, nullable=False)
    true_outcome: Mapped[OutcomeEnum] = mapped_column(
        Enum(OutcomeEnum, name="outcome", native_enum=True), nullable=False
    )  # world model or analyst. NEVER the rulepack.
    source: Mapped[str] = mapped_column(String, CheckConstraint("source IN ('SYNTHETIC','ANALYST')"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_calibration_reason_source", "reason_code", "source"),)


# ============ SEED / NETWORK-HELD TRANSACTION STORE ============
# CLAUDE.md scope note: there is no real external Amex ledger to read here.
# arbiter.network.loader's contract ("read-only interface to a production
# ledger") is satisfied against this table instead -- a synthetic stand-in
# populated by datagen at seed time, structurally identical to what a real
# ledger read would return. Swapping this for a real ledger client is a
# drop-in replacement at arbiter.network.loader's boundary; nothing else in
# the system would need to change.

class SeedTransaction(Base):
    __tablename__ = "seed_transaction"

    transaction_id: Mapped[uuid.UUID] = _uuid_pk()
    reason_code: Mapped[str] = mapped_column(String, nullable=False)
    card_member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    merchant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    transaction_at: Mapped[datetime] = mapped_column(nullable=False)
    network_facts: Mapped[dict] = mapped_column(JSONB, nullable=False)  # arbiter.network.loader.NetworkFacts, serialised
    world_truth: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)  # datagen ground truth, eval-only

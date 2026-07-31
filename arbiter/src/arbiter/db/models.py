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
    REAL,
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSTZRANGE, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
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
    # The network chargeback right did not exist for this dispute: the
    # filing window had closed, or the transaction is one the reason code's
    # "Excluded Transactions" list removes (arbiter.eligibility). NOT a
    # merchant win -- no evidence was weighed. Kept distinct so that win
    # rates, the fairness layer's per-rule disparate-impact analysis and the
    # conformal calibration pool never count a case nobody adjudicated as
    # one somebody won. The card member's Reg Z billing-error rights against
    # the issuer are untouched by this outcome; see
    # arbiter.eligibility.models.ChargebackRight.
    CHARGEBACK_INELIGIBLE = "CHARGEBACK_INELIGIBLE"


class ContradictionSeverityEnum(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class EvidenceNodeTypeEnum(str, enum.Enum):
    # NOTE: this is the ONE enum in this module whose member names differ
    # from its values -- the Postgres type is lowercase (migration 0001,
    # matching `arbiter.evidence.models.EvidenceNodeType`), while the Python
    # member names are upper. SQLAlchemy's `Enum` persists the member NAME by
    # default, so this column needs `values_callable` to send the value
    # instead; see `_node_type_column()` below. Every other enum here happens
    # to have name == value, which is why only this one was ever affected --
    # and why the mismatch survived: it is invisible until a row is actually
    # written to Postgres.
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
    filed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reg_regime: Mapped[str] = mapped_column(
        String, CheckConstraint("reg_regime IN ('REG_Z', 'REG_E')"), nullable=False
    )
    # Regulatory clocks. These columns existed from the first migration and
    # nothing ever read them -- no timer, no scheduler, no job. They are now
    # driven by arbiter.decision.deadlines, and the *_at columns below
    # record what the clock actually did so a breach is a queryable fact.
    ack_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolve_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    merchant_response_deadline: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    merchant_window_expired_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    merchant_responded: Mapped[bool] = mapped_column(nullable=False, default=False)
    # Reg E 12 CFR 1005.11(c)(2): provisional credit within 10 business days
    # when the investigation extends beyond the determination window.
    provisional_credit_deadline: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    provisional_credit_issued_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

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
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    scan_status: Mapped[str] = mapped_column(String, nullable=False, default="PENDING")
    forensics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class MerkleBatch(Base):
    __tablename__ = "merkle_batch"

    batch_id: Mapped[uuid.UUID] = _uuid_pk()
    root_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    tree_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sth_signature: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    tsa_token: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    sealed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AdecCommitmentRow(Base):
    __tablename__ = "adec_commitment"

    commitment_id: Mapped[uuid.UUID] = _uuid_pk()
    merchant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    commitment_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    artifact_type: Mapped[str] = mapped_column(String, nullable=False)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # merchant-claimed
    batch_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("merkle_batch.batch_id"), nullable=True)
    leaf_index: Mapped[Optional[int]] = mapped_column(nullable=True)
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)  # server-observed, AUTHORITATIVE
    revealed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
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
        # `values_callable` is load-bearing, not decoration. SQLAlchemy's
        # `Enum` binds an enum member by its NAME unless told otherwise, so
        # this column sent 'ORDER' at a Postgres type whose labels are
        # lowercase ('order'), and EVERY evidence-node insert failed with
        # `InvalidTextRepresentation: invalid input value for enum
        # evidence_node_type: "ORDER"`. That is: no case could be adjudicated
        # against a real database at all. The whole test suite passes because
        # it builds evidence graphs in memory and never persists one.
        Enum(
            EvidenceNodeTypeEnum,
            name="evidence_node_type",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    attrs: Mapped[dict] = mapped_column(JSONB, nullable=False)
    valid_time: Mapped[Optional[Any]] = mapped_column(TSTZRANGE, nullable=True)
    asserted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
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
    provisional_credit_due: Mapped[bool] = mapped_column(nullable=False, default=False)  # Reg E 12 CFR 1005.11 -- see arbiter.horn provisional-credit axis
    # What the MANDATORY four-layer contradiction pipeline actually did for
    # this decision: which layers ran, which could not, and whether that
    # forced escalation. Stored rather than recomputed on read -- recomputing
    # would answer "what would the pipeline do now?", which drifts as the
    # environment changes (e.g. once the NLI weights are installed).
    contradiction_analysis: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # The grounded narration this decision was explained with:
    # `{text, source, citations[{sentence_idx, node_id}]}` from
    # arbiter.narrate. Stored, not recomputed on read -- it cites evidence
    # node ids and was rendered against the rulepack this row pinned, so a
    # later re-render answers "what would we say now?" rather than "what did
    # we tell the parties?". `source` also preserves whether an LLM
    # narration was generated and then discarded for failing citation
    # grounding ("template_fallback"), which is the measurement that says
    # whether CLAUDE.md invariant #5's veto is doing anything.
    narration: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # arbiter.eligibility.EligibilityResult for this decision: which of the
    # reason code's "Excluded Transactions" fired, every filing-window
    # branch with its computed deadline, and any attribute the ledger could
    # not supply. Populated on EVERY decision, not just ineligible ones --
    # "the gate ran and found the right available" is the claim the audit
    # trail needs to be able to make.
    eligibility: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Auto-resolved cases deliberately routed to a human anyway, so the
    # calibration pool sees the region of the distribution the escalation
    # path never visits (arbiter.decision.review_sampling).
    selected_for_audit: Mapped[bool] = mapped_column(nullable=False, default=False)
    review_selection_probability: Mapped[Optional[float]] = mapped_column(REAL, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    signature: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_epoch: Mapped[int] = mapped_column(nullable=False, default=0)  # which EventSigner epoch produced `signature` -- arbiter.audit.sign.KeyRing

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
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    prev_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    event_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    signature: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_epoch: Mapped[int] = mapped_column(nullable=False, default=0)  # which EventSigner epoch produced `signature` -- arbiter.audit.sign.KeyRing


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
    # Probability this case had of being reviewed at all. Escalated cases
    # are 1.0; auto-resolved cases carry the audit-sampling rate. The gate
    # weights by the inverse (arbiter.decision.review_sampling) -- without
    # it the pool is a biased subsample of the deployment distribution and
    # split-conformal's exchangeability assumption does not hold.
    selection_probability: Mapped[Optional[float]] = mapped_column(REAL, nullable=True)
    is_audit_sample: Mapped[bool] = mapped_column(nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

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
    transaction_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Which regulation governs disputes on this transaction. A property of
    # the PRODUCT it settled on -- credit (Reg Z) vs prepaid/debit (Reg E) --
    # which only the ledger knows. It used to come from the dispute-creation
    # request body, so a card member could self-declare REG_E on a credit
    # card and force provisional credit on every escalated case.
    reg_regime: Mapped[str] = mapped_column(
        String, CheckConstraint("reg_regime IN ('REG_Z','REG_E')", name="seed_transaction_reg_regime"),
        nullable=False, default="REG_Z",
    )
    network_facts: Mapped[dict] = mapped_column(JSONB, nullable=False)  # arbiter.network.loader.NetworkFacts, serialised
    world_truth: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)  # datagen ground truth, eval-only


# ============ CRYPTO-SHREDDING (GDPR Article 17) ============
# arbiter/privacy/shredding.py: per-subject (card_member_id/merchant_id)
# symmetric key wrapping PII fields at rest. Erasure = deleting this row,
# not the encrypted data itself -- the hash chain and Merkle commitments
# over that data stay intact and verifiable; the plaintext just becomes
# permanently unrecoverable. See that module's docstring for the full
# rationale (case_event/decision are append-only by DB trigger, which is
# in tension with a literal "delete the data" reading of Article 17).

class SubjectKeyRow(Base):
    __tablename__ = "subject_key"

    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    wrapped_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    erased_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


# ============ ADJUDICATION QUEUE ============
# Adjudication used to run inline in the HTTP request, holding a threadpool
# worker for the full pipeline. A durable job row plus SELECT ... FOR UPDATE
# SKIP LOCKED decouples it -- and keeps the queue in the same transaction as
# the state change, eliminating the dual-write bug an external broker would
# introduce at this scale (~10 QPS peak).


class AdjudicationJob(Base):
    __tablename__ = "adjudication_job"

    job_id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dispute_case.case_id"), nullable=False)
    state: Mapped[str] = mapped_column(
        String,
        CheckConstraint("state IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')", name="adjudication_job_state"),
        nullable=False, default="QUEUED",
    )
    attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(nullable=False, default=3)
    requested_by: Mapped[str] = mapped_column(String, nullable=False)
    error: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    decision_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    enqueued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Retry backoff. Without it a deterministically-failing case spins a
    # worker at full speed until it exhausts max_attempts.
    visible_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_adjudication_job_case", "case_id", "enqueued_at"),
    )

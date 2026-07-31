"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-07-26
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # `create_type=False` on every one of these is load-bearing, not style.
    #
    # A `pg.ENUM` used as a column type emits its own `CREATE TYPE` when the
    # table is created. These are ALSO created explicitly below, so without
    # this flag each type is created twice and the migration dies on
    # `DuplicateObject: type "case_state" already exists` against any clean
    # database -- i.e. this migration had never actually been run.
    #
    # It was reported as verified because `alembic upgrade --sql` renders it
    # without complaint: an offline render checks that the SQL can be
    # GENERATED, never that it can be EXECUTED, and a duplicate statement is
    # perfectly renderable. The duplicate is visible in that output — two
    # `CREATE TYPE case_state` lines — but only if somebody counts them.
    #
    # Creating them explicitly (rather than letting the first table that
    # references each one do it) is still the right shape: several tables
    # share these types, so leaving it implicit makes creation order decide
    # which table owns the type, and `downgrade()` then has nothing stable
    # to drop.
    provenance_tier = pg.ENUM(
        "COMMITTED", "NETWORK", "SUBMITTED", "ASSERTED",
        name="provenance_tier", create_type=False,
    )
    case_state = pg.ENUM(
        "INTAKE", "GATHERING", "AWAITING_EVIDENCE", "ANALYSING", "ADJUDICATED",
        "ESCALATED", "SETTLED", "DEFLECTED", "REOPENED",
        name="case_state", create_type=False,
    )
    party = pg.ENUM("CARD_MEMBER", "MERCHANT", "NEUTRAL", name="party", create_type=False)
    outcome = pg.ENUM(
        "CARD_MEMBER_PREVAILS", "MERCHANT_PREVAILS", "SPLIT", "INSUFFICIENT_EVIDENCE",
        name="outcome", create_type=False,
    )
    contradiction_severity = pg.ENUM(
        "LOW", "MEDIUM", "HIGH", "CRITICAL",
        name="contradiction_severity", create_type=False,
    )
    evidence_node_type = pg.ENUM(
        "transaction", "authorization", "settlement", "avs_result", "cvv_result",
        "three_ds_result", "device_session", "descriptor", "prior_transaction",
        "order", "line_item", "shipment", "delivery_scan", "signature_capture",
        "communication", "terms_acceptance", "refund_policy", "refund", "credit",
        "address", "identity", "statement_line", "service_access_log",
        "attestation", "contradiction", "claim",
        name="evidence_node_type", create_type=False,
    )

    bind = op.get_bind()
    for enum_type in (provenance_tier, case_state, party, outcome, contradiction_severity, evidence_node_type):
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "dispute_case",
        sa.Column("case_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("transaction_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("card_member_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("merchant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("reason_code", sa.Text(), nullable=False),
        sa.Column("state", case_state, nullable=False, server_default="INTAKE"),
        sa.Column("amount_minor", sa.BigInteger(), sa.CheckConstraint("amount_minor > 0"), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("filed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reg_regime", sa.Text(), sa.CheckConstraint("reg_regime IN ('REG_Z','REG_E')"), nullable=False),
        sa.Column("ack_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolve_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("merchant_response_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("merchant_responded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_dispute_case_state_deadline", "dispute_case", ["state", "resolve_deadline"])
    op.create_index("ix_dispute_case_merchant_filed", "dispute_case", ["merchant_id", "filed_at"])

    op.create_table(
        "artifact",
        sa.Column("artifact_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("case_id", pg.UUID(as_uuid=True), sa.ForeignKey("dispute_case.case_id"), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("sha256", sa.LargeBinary(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), sa.CheckConstraint("byte_size <= 26214400"), nullable=False),
        sa.Column("uploaded_by", party, nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("scan_status", sa.Text(), nullable=False, server_default="PENDING"),
        sa.Column("forensics", pg.JSONB(), nullable=False, server_default="{}"),
    )

    op.create_table(
        "merkle_batch",
        sa.Column("batch_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("root_hash", sa.LargeBinary(), nullable=False),
        sa.Column("tree_size", sa.BigInteger(), nullable=False),
        sa.Column("sth_signature", sa.LargeBinary(), nullable=False),
        sa.Column("tsa_token", sa.LargeBinary(), nullable=True),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "adec_commitment",
        sa.Column("commitment_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("merchant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("commitment_hash", sa.LargeBinary(), nullable=False),
        sa.Column("artifact_type", sa.Text(), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("batch_id", pg.UUID(as_uuid=True), sa.ForeignKey("merkle_batch.batch_id"), nullable=True),
        sa.Column("leaf_index", sa.Integer(), nullable=True),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("revealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reveal_valid", sa.Boolean(), nullable=True),
        sa.UniqueConstraint("batch_id", "leaf_index"),
    )
    op.create_index("ix_adec_commitment_hash", "adec_commitment", ["commitment_hash"])

    op.create_table(
        "evidence_node",
        sa.Column("node_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("case_id", pg.UUID(as_uuid=True), sa.ForeignKey("dispute_case.case_id", ondelete="CASCADE"), nullable=False),
        sa.Column("node_type", evidence_node_type, nullable=False),
        sa.Column("attrs", pg.JSONB(), nullable=False),
        sa.Column("valid_time", pg.TSTZRANGE(), nullable=True),
        sa.Column("asserted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("provenance", provenance_tier, nullable=False),
        sa.Column("commitment_id", pg.UUID(as_uuid=True), sa.ForeignKey("adec_commitment.commitment_id"), nullable=True),
        sa.Column("extract_conf", sa.Float(), nullable=True),
        sa.Column("artifact_id", pg.UUID(as_uuid=True), sa.ForeignKey("artifact.artifact_id"), nullable=True),
        sa.Column("source_ref", pg.JSONB(), nullable=True),
        sa.CheckConstraint("provenance != 'COMMITTED' OR commitment_id IS NOT NULL", name="committed_needs_commitment"),
        sa.CheckConstraint("extract_conf IS NULL OR (extract_conf BETWEEN 0 AND 1)", name="extract_conf_range"),
    )
    op.create_index("ix_evidence_node_valid_time", "evidence_node", ["valid_time"], postgresql_using="gist")
    op.create_index("ix_evidence_node_attrs", "evidence_node", ["attrs"], postgresql_using="gin", postgresql_ops={"attrs": "jsonb_path_ops"})
    op.create_index("ix_evidence_node_case_type", "evidence_node", ["case_id", "node_type"])

    op.create_table(
        "evidence_edge",
        sa.Column("edge_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("case_id", pg.UUID(as_uuid=True), sa.ForeignKey("dispute_case.case_id", ondelete="CASCADE"), nullable=False),
        sa.Column("src", pg.UUID(as_uuid=True), sa.ForeignKey("evidence_node.node_id", ondelete="CASCADE"), nullable=False),
        sa.Column("dst", pg.UUID(as_uuid=True), sa.ForeignKey("evidence_node.node_id", ondelete="CASCADE"), nullable=False),
        sa.Column("rel", sa.Text(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=True),
        sa.UniqueConstraint("src", "dst", "rel"),
    )

    op.create_table(
        "contradiction",
        sa.Column("contradiction_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("case_id", pg.UUID(as_uuid=True), sa.ForeignKey("dispute_case.case_id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("severity", contradiction_severity, nullable=False),
        sa.Column("node_ids", pg.ARRAY(pg.UUID(as_uuid=True)), nullable=False),
        sa.Column("detail", pg.JSONB(), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "decision",
        sa.Column("decision_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("case_id", pg.UUID(as_uuid=True), sa.ForeignKey("dispute_case.case_id"), nullable=False),
        sa.Column("rulepack_hash", sa.LargeBinary(), nullable=False),
        sa.Column("outcome", outcome, nullable=False),
        sa.Column("proof_tree", pg.JSONB(), nullable=False),
        sa.Column("predicates", pg.JSONB(), nullable=False),
        sa.Column("counterfactuals", pg.JSONB(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("conformal_set", pg.ARRAY(sa.Text()), nullable=False),
        sa.Column("abstained", sa.Boolean(), nullable=False),
        sa.Column("escalation_reason", sa.Text(), nullable=True),
        sa.Column("merchant_silent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("signature", sa.LargeBinary(), nullable=False),
    )
    op.create_index("ix_decision_case_decided", "decision", ["case_id", "decided_at"])
    op.create_index("ix_decision_merchant_silent", "decision", ["merchant_silent"], postgresql_where=sa.text("merchant_silent"))

    op.create_table(
        "rule_firing",
        sa.Column("decision_id", pg.UUID(as_uuid=True), sa.ForeignKey("decision.decision_id"), primary_key=True),
        sa.Column("rule_id", sa.Text(), primary_key=True),
        sa.Column("fired", sa.Boolean(), nullable=False),
    )

    op.create_table(
        "case_event",
        sa.Column("case_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("seq", sa.BigInteger(), primary_key=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", pg.JSONB(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("actor_type", sa.Text(), sa.CheckConstraint("actor_type IN ('human','service','advocate','referee')"), nullable=False),
        sa.Column("rulepack_hash", sa.LargeBinary(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("prev_hash", sa.LargeBinary(), nullable=False),
        sa.Column("event_hash", sa.LargeBinary(), nullable=False),
        sa.Column("signature", sa.LargeBinary(), nullable=False),
    )

    op.create_table(
        "calibration_sample",
        sa.Column("sample_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("reason_code", sa.Text(), nullable=False),
        sa.Column("features", pg.JSONB(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("true_outcome", outcome, nullable=False),
        sa.Column("source", sa.Text(), sa.CheckConstraint("source IN ('SYNTHETIC','ANALYST')"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_calibration_reason_source", "calibration_sample", ["reason_code", "source"])

    op.create_table(
        "seed_transaction",
        sa.Column("transaction_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("reason_code", sa.Text(), nullable=False),
        sa.Column("card_member_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("merchant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("transaction_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("network_facts", pg.JSONB(), nullable=False),
        sa.Column("world_truth", pg.JSONB(), nullable=True),
    )

    # CLAUDE.md invariant #6: case_event and decision are append-only.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION forbid_mutation() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'append-only table: % on %', TG_OP, TG_TABLE_NAME; END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER case_event_append_only BEFORE UPDATE OR DELETE ON case_event "
        "FOR EACH ROW EXECUTE FUNCTION forbid_mutation();"
    )
    op.execute(
        "CREATE TRIGGER decision_append_only BEFORE UPDATE OR DELETE ON decision "
        "FOR EACH ROW EXECUTE FUNCTION forbid_mutation();"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS decision_append_only ON decision;")
    op.execute("DROP TRIGGER IF EXISTS case_event_append_only ON case_event;")
    op.execute("DROP FUNCTION IF EXISTS forbid_mutation();")

    for table in (
        "seed_transaction", "calibration_sample", "case_event", "rule_firing", "decision",
        "contradiction", "evidence_edge", "evidence_node", "adec_commitment", "merkle_batch",
        "artifact", "dispute_case",
    ):
        op.drop_table(table)

    bind = op.get_bind()
    for name in ("evidence_node_type", "contradiction_severity", "outcome", "party", "case_state", "provenance_tier"):
        pg.ENUM(name=name).drop(bind, checkfirst=True)

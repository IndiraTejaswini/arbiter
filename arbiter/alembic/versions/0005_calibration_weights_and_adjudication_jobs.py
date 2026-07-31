"""calibration selection probability, audit-sample flag, and the adjudication job queue

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-27
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- Calibration selection bias -------------------------------------
    # Analyst reviews only ever came from ESCALATED cases -- the
    # high-nonconformity tail by construction. Feeding only that tail back
    # into the split-conformal pool inflates the quantile monotonically, so
    # the MORE human review was done the MORE permissive the gate became.
    # Recording the probability a case had of being reviewed lets the gate
    # weight by its inverse (Horvitz-Thompson) and recover an unbiased
    # estimate of the deployment quantile.
    op.add_column(
        "calibration_sample",
        sa.Column("selection_probability", sa.Float(), nullable=True),
    )
    # Distinguishes an audit sample (an auto-resolved case deliberately
    # routed to a human) from an escalation review. Without it the two are
    # indistinguishable in the pool and the weighting cannot be audited.
    op.add_column(
        "calibration_sample",
        sa.Column("is_audit_sample", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    # Which auto-resolved cases were selected for audit review, so the
    # analyst queue can surface them and so selection is inspectable after
    # the fact rather than only reproducible from the hash.
    op.add_column("decision", sa.Column("selected_for_audit", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("decision", sa.Column("review_selection_probability", sa.Float(), nullable=True))
    op.create_index(
        "ix_decision_audit_queue", "decision", ["selected_for_audit", "decided_at"],
        postgresql_where=sa.text("selected_for_audit"),
    )

    # -- Async adjudication ---------------------------------------------
    # Adjudication ran inline in the HTTP request, holding a threadpool
    # worker for the full pipeline (~10s with LLM advocates). No timeout,
    # no bulkhead, no backpressure -- the service saturated at ~4 QPS and
    # then queued unboundedly. A durable job row plus SELECT ... FOR UPDATE
    # SKIP LOCKED decouples it, and keeps the queue in the same transaction
    # as the state change, which eliminates an entire class of dual-write
    # bug that an external broker would introduce.
    op.create_table(
        "adjudication_job",
        sa.Column("job_id", sa.Uuid(), primary_key=True),
        sa.Column("case_id", sa.Uuid(), sa.ForeignKey("dispute_case.case_id"), nullable=False),
        sa.Column(
            "state", sa.Text(),
            sa.CheckConstraint(
                "state IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')",
                name="adjudication_job_state",
            ),
            nullable=False, server_default="QUEUED",
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("requested_by", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("decision_id", sa.Uuid(), nullable=True),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        # Backoff: a failed job is not retried immediately, or a
        # deterministically-failing case would spin a worker at full speed.
        sa.Column("visible_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # The worker's claim query: "oldest visible QUEUED job". Without this
    # index every poll is a full scan of every job ever run.
    op.create_index(
        "ix_adjudication_job_claim", "adjudication_job", ["visible_at"],
        postgresql_where=sa.text("state = 'QUEUED'"),
    )
    op.create_index("ix_adjudication_job_case", "adjudication_job", ["case_id", "enqueued_at"])

    # At most one live job per case. Two concurrent adjudications of the
    # same case would race on the hash chain and write two decision rows
    # for one evidence set; the database refuses rather than relying on
    # every caller remembering to check.
    op.create_index(
        "uq_adjudication_job_live_per_case", "adjudication_job", ["case_id"],
        unique=True, postgresql_where=sa.text("state IN ('QUEUED','RUNNING')"),
    )


def downgrade() -> None:
    op.drop_index("uq_adjudication_job_live_per_case", table_name="adjudication_job")
    op.drop_index("ix_adjudication_job_case", table_name="adjudication_job")
    op.drop_index("ix_adjudication_job_claim", table_name="adjudication_job")
    op.drop_table("adjudication_job")
    op.drop_index("ix_decision_audit_queue", table_name="decision")
    op.drop_column("decision", "review_selection_probability")
    op.drop_column("decision", "selected_for_audit")
    op.drop_column("calibration_sample", "is_audit_sample")
    op.drop_column("calibration_sample", "selection_probability")

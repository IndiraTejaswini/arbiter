"""reg_regime on the ledger (not the client), and regulatory deadline tracking

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-27
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # `reg_regime` decides whether Reg E provisional credit is owed. It was
    # read from the dispute-creation REQUEST BODY, so a card member could
    # self-declare REG_E on a credit-card dispute and force provisional
    # credit on every escalated case. The regime is a property of the
    # PRODUCT the transaction settled on -- credit (Reg Z) vs prepaid/debit
    # (Reg E) -- and the ledger is the only party that knows it.
    op.add_column(
        "seed_transaction",
        sa.Column(
            "reg_regime", sa.Text(),
            sa.CheckConstraint("reg_regime IN ('REG_Z','REG_E')", name="seed_transaction_reg_regime"),
            nullable=False, server_default="REG_Z",
        ),
    )

    # Regulatory clock bookkeeping. Reg Z 12 CFR 1026.13(b)(1) requires
    # written acknowledgment within 30 days; Reg E 12 CFR 1005.11(c)(1)
    # requires a determination within 10 business days (or provisional
    # credit if the investigation extends). The deadline columns existed
    # from 0001 and NOTHING ever read them -- no timer, no sweeper, no job.
    # These record what the clock actually did, so a breach is a fact in the
    # database rather than an absence nobody notices.
    op.add_column("dispute_case", sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("dispute_case", sa.Column("provisional_credit_deadline", sa.DateTime(timezone=True), nullable=True))
    op.add_column("dispute_case", sa.Column("provisional_credit_issued_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("dispute_case", sa.Column("merchant_window_expired_at", sa.DateTime(timezone=True), nullable=True))

    # The sweeper's hot query: "which open cases have a deadline in the
    # past?" Without an index that is a full scan of every dispute ever
    # filed, on a job that runs every minute.
    op.create_index(
        "ix_dispute_case_ack_due", "dispute_case", ["ack_deadline"],
        postgresql_where=sa.text("acknowledged_at IS NULL"),
    )
    op.create_index(
        "ix_dispute_case_merchant_window", "dispute_case", ["merchant_response_deadline"],
        postgresql_where=sa.text("merchant_window_expired_at IS NULL AND merchant_responded = false"),
    )
    op.create_index(
        "ix_dispute_case_provisional_credit_due", "dispute_case", ["provisional_credit_deadline"],
        postgresql_where=sa.text("provisional_credit_issued_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_dispute_case_provisional_credit_due", table_name="dispute_case")
    op.drop_index("ix_dispute_case_merchant_window", table_name="dispute_case")
    op.drop_index("ix_dispute_case_ack_due", table_name="dispute_case")
    op.drop_column("dispute_case", "merchant_window_expired_at")
    op.drop_column("dispute_case", "provisional_credit_issued_at")
    op.drop_column("dispute_case", "provisional_credit_deadline")
    op.drop_column("dispute_case", "acknowledged_at")
    op.drop_column("seed_transaction", "reg_regime")

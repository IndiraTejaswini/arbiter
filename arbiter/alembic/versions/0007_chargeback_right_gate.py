"""chargeback-right gate: CHARGEBACK_INELIGIBLE outcome + per-decision eligibility record

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-27
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Amex's own merchant chargeback guide gives every reason code two
    # fields this schema had no room for: "Maximum time a dispute can be
    # raised" and "Excluded Transactions". Both END a dispute before the
    # merits, and neither is a finding about the evidence -- so neither fits
    # any existing value of the `outcome` enum. Recording an excluded
    # transaction as MERCHANT_PREVAILS would assert that the merchant won an
    # evidentiary contest that never took place, and would corrupt every
    # downstream number computed off outcomes: win rates, the fairness
    # layer's per-rule disparate-impact analysis, and the conformal
    # calibration pool alike.
    #
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block on
    # PostgreSQL < 12, and alembic wraps migrations in one. autocommit_block
    # is the sanctioned escape hatch.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE outcome ADD VALUE IF NOT EXISTS 'CHARGEBACK_INELIGIBLE'")

    # The gate's full finding, on the decision it produced: which exclusion
    # fired, its citation into the guide, every filing-window branch with
    # its computed deadline, and any attribute that could not be determined.
    #
    # Persisted rather than recomputed for the same reason
    # `contradiction_analysis` is (migration 0006): this is the record of
    # what the gate did for THIS decision, against the rulepack hash this
    # decision pinned. Recomputing later answers a different question and
    # drifts as the ledger backfills attributes that were unknown at the
    # time.
    op.add_column(
        "decision",
        sa.Column("eligibility", pg.JSONB(), nullable=False, server_default="{}"),
    )

    # "Which disputes did we refuse to charge back, and on which exclusion?"
    # is a question both compliance and the merchant-facing console ask
    # routinely, and it must not be a full scan.
    op.create_index(
        "ix_decision_chargeback_ineligible", "decision", ["decided_at"],
        postgresql_where=sa.text("(eligibility->>'available') = 'false'"),
    )

    # An unevaluable exclusion is a live operational signal, not a curio: it
    # means the ledger did not tell us something that could have ended the
    # dispute, and the case proceeded to the merits on that gap. Cheap to
    # find so the gaps get closed rather than accumulating silently.
    op.create_index(
        "ix_decision_eligibility_undetermined", "decision", ["decided_at"],
        postgresql_where=sa.text(
            "jsonb_array_length(coalesce(eligibility->'undetermined_attributes', '[]'::jsonb)) > 0"
        ),
    )


def downgrade() -> None:
    op.drop_index("ix_decision_eligibility_undetermined", table_name="decision")
    op.drop_index("ix_decision_chargeback_ineligible", table_name="decision")
    op.drop_column("decision", "eligibility")
    # The enum value is deliberately NOT removed. PostgreSQL cannot drop a
    # value from an enum type, and any decision row already carrying it
    # would be unreadable if the type were recreated without it. `decision`
    # is append-only by trigger (migration 0001) -- there is no correction
    # path that could rewrite those rows first.

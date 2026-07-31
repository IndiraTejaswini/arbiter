"""record the mandatory contradiction pipeline's per-layer status on the decision

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-27
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The four-layer contradiction pipeline is MANDATORY, and
    # `ContradictionAnalysis` already reports which layers ran, which could
    # not, and whether that forced escalation. None of it was persisted or
    # returned, so the single most important operational fact about the
    # pipeline -- "a mandatory safety check could not run on this case" --
    # was visible only as a substring inside `escalation_reason`.
    #
    # Stored on the decision rather than recomputed on read: this is the
    # record of what the pipeline actually did for THIS decision. Recomputing
    # it later would answer a different question (what would it do now?) and
    # would silently drift as the environment changed -- e.g. after the NLI
    # weights were finally installed.
    op.add_column(
        "decision",
        sa.Column("contradiction_analysis", pg.JSONB(), nullable=False, server_default="{}"),
    )

    # Operators need "which decisions were made while a mandatory layer was
    # down?" to be a cheap query, not a full scan of every decision ever made.
    op.create_index(
        "ix_decision_incomplete_analysis", "decision", ["decided_at"],
        postgresql_where=sa.text("(contradiction_analysis->>'complete') = 'false'"),
    )


def downgrade() -> None:
    op.drop_index("ix_decision_incomplete_analysis", table_name="decision")
    op.drop_column("decision", "contradiction_analysis")

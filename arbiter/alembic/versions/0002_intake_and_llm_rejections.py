"""intake intent_confidence + decision llm_rejections

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-26
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("dispute_case", sa.Column("intent_confidence", sa.Float(), nullable=True))
    op.add_column("decision", sa.Column("llm_rejections", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("decision", "llm_rejections")
    op.drop_column("dispute_case", "intent_confidence")

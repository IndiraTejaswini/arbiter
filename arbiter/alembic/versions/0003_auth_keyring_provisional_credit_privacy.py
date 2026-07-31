"""key_epoch on case_event/decision, decision.provisional_credit_due, subject_key vault for crypto-shredding

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-26
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Signature epoch: which arbiter.audit.sign.KeyRing entry produced the
    # stored signature, so a key rotation never retroactively invalidates
    # an existing signature -- see audit/sign.py's module docstring.
    op.add_column("case_event", sa.Column("key_epoch", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("decision", sa.Column("key_epoch", sa.Integer(), nullable=False, server_default="0"))

    # Reg E (12 CFR 1005.11) provisional-credit axis: computed independently
    # of the merchant/card-member win decision (arbiter.horn's auxiliary
    # predicate), so it can be true even on an abstained case.
    op.add_column("decision", sa.Column("provisional_credit_due", sa.Boolean(), nullable=False, server_default="false"))

    # Crypto-shredding (GDPR Article 17 right to erasure) vault: PII fields
    # under a subject's key are encrypted with a per-subject key stored
    # here; erasure destroys the key row, not the (still Merkle-committed,
    # still hash-chained) ciphertext. See arbiter/privacy/shredding.py.
    op.create_table(
        "subject_key",
        sa.Column("subject_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("wrapped_key", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("erased_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("subject_key")
    op.drop_column("decision", "provisional_credit_due")
    op.drop_column("decision", "key_epoch")
    op.drop_column("case_event", "key_epoch")

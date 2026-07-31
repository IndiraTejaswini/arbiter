"""persist the grounded narration alongside the decision it explains

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-28
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Stated as the gap it closes. `arbiter.narrate` -- the template
    # renderer, the LLM exception path, and `arbiter.narrate.ground`'s
    # citation verifier (CLAUDE.md invariant #5) -- ran on every single
    # adjudication and its output was thrown away. `adjudicate_case` returned
    # it on `AdjudicationOutcome.narration_text`; the only caller
    # (`scripts/run_adjudication_worker.py`) discarded it; there was no
    # column to put it in and no field for it on
    # `GET /v1/cases/{id}/decision`, whose own docstring advertised
    # "proof tree + counterfactuals + narration".
    #
    # So an entire guarded LLM boundary produced text that no card member,
    # merchant, analyst, or auditor could ever read. Recomputing it on read
    # is not the fix: narration cites evidence node ids and is rendered
    # against the rulepack the decision pinned, so a later re-render answers
    # "what would we say about this case now?" rather than "what did we tell
    # the parties?". For a system whose thesis is that the explanation IS
    # the product, only the second question matters.
    #
    # JSONB rather than TEXT because the citation set is the load-bearing
    # part: `{text, source, citations[{sentence_idx, node_id}]}`. `source`
    # records which renderer produced it -- "template", "llm_exception_path",
    # or "template_fallback", the last meaning an LLM narration was generated
    # and then DISCARDED for citing a node that does not exist. That a
    # fallback happened is exactly the kind of thing an auditor should be
    # able to count, and it is unrecoverable if only the prose is stored.
    op.add_column(
        "decision",
        sa.Column("narration", pg.JSONB(), nullable=False, server_default="{}"),
    )

    # "How often did a generated narration fail citation grounding?" is the
    # measurement that says whether the veto in invariant #5 is doing
    # anything. Partial so it stays small -- the overwhelming majority of
    # decisions take the deterministic template path.
    op.create_index(
        "ix_decision_narration_fallback", "decision", ["decided_at"],
        postgresql_where=sa.text("(narration->>'source') = 'template_fallback'"),
    )


def downgrade() -> None:
    op.drop_index("ix_decision_narration_fallback", table_name="decision")
    op.drop_column("decision", "narration")

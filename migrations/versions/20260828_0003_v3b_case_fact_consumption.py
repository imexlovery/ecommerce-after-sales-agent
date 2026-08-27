"""Bind V3-B Case Fact replies to their one outstanding question.

Revision ID: 20260828_0003
Revises: 20260828_0002
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260828_0003"
down_revision: str | Sequence[str] | None = "20260828_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "case_fact_message_consumptions",
        sa.Column("consumption_id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("question_id", sa.String(length=64), nullable=False),
        sa.Column("source_message_id", sa.String(length=64), nullable=False),
        sa.Column("source_message_hash", sa.String(length=64), nullable=False),
        sa.Column("candidate_batch_hash", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=96), nullable=False),
        sa.Column("assertion_id", sa.String(length=64), nullable=True),
        sa.Column("decision_payload", sa.JSON(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "outcome IN ('accepted', 'rejected', 'empty')",
            name=op.f("ck_case_fact_message_consumptions_outcome"),
        ),
        sa.ForeignKeyConstraint(["case_id"], ["investigation_cases.case_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["question_id"], ["case_fact_questions.question_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id"], ["messages.message_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["assertion_id"], ["case_fact_assertions.assertion_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("consumption_id", name=op.f("pk_case_fact_message_consumptions")),
        sa.UniqueConstraint("question_id", name="uq_case_fact_consumption_question"),
        sa.UniqueConstraint("source_message_id", name="uq_case_fact_consumption_message"),
    )
    op.create_index(
        op.f("ix_case_fact_message_consumptions_case_id"),
        "case_fact_message_consumptions",
        ["case_id"],
    )
    op.create_index(
        op.f("ix_case_fact_message_consumptions_question_id"),
        "case_fact_message_consumptions",
        ["question_id"],
    )
    op.create_index(
        op.f("ix_case_fact_message_consumptions_source_message_id"),
        "case_fact_message_consumptions",
        ["source_message_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_case_fact_message_consumptions_source_message_id"),
        table_name="case_fact_message_consumptions",
    )
    op.drop_index(
        op.f("ix_case_fact_message_consumptions_question_id"),
        table_name="case_fact_message_consumptions",
    )
    op.drop_index(
        op.f("ix_case_fact_message_consumptions_case_id"),
        table_name="case_fact_message_consumptions",
    )
    op.drop_table("case_fact_message_consumptions")

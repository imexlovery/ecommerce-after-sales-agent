"""Add append-only V3-B Case Fact storage and Proposal identity.

Revision ID: 20260828_0002
Revises: 20260823_0001
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260828_0002"
down_revision: str | Sequence[str] | None = "20260823_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "case_fact_assertions",
        sa.Column("assertion_id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("fact_code", sa.String(length=64), nullable=False),
        sa.Column("value", sa.String(length=16), nullable=False),
        sa.Column("source_message_id", sa.String(length=64), nullable=False),
        sa.Column("source_message_hash", sa.String(length=64), nullable=False),
        sa.Column("source_span_start", sa.Integer(), nullable=False),
        sa.Column("source_span_end", sa.Integer(), nullable=False),
        sa.Column("relation", sa.String(length=16), nullable=False),
        sa.Column("supersedes_assertion_id", sa.String(length=64), nullable=True),
        sa.Column("extractor_kind", sa.String(length=32), nullable=False),
        sa.Column("extractor_version", sa.String(length=128), nullable=False),
        sa.Column("context_tool_call_id", sa.String(length=64), nullable=True),
        sa.Column("context_result_hash", sa.String(length=64), nullable=True),
        sa.Column("assertion_sequence", sa.Integer(), nullable=False),
        sa.Column("candidate_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "fact_code IN ('customer_still_reports_missing', 'reported_delivery_location_checked')",
            name=op.f("ck_case_fact_assertions_fact_code"),
        ),
        sa.CheckConstraint(
            "value IN ('true', 'false', 'unknown')",
            name=op.f("ck_case_fact_assertions_value"),
        ),
        sa.CheckConstraint(
            "relation IN ('new', 'repeat', 'correction', 'withdrawal')",
            name=op.f("ck_case_fact_assertions_relation"),
        ),
        sa.CheckConstraint(
            "assertion_sequence >= 1",
            name=op.f("ck_case_fact_assertions_sequence_positive"),
        ),
        sa.ForeignKeyConstraint(["case_id"], ["investigation_cases.case_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_message_id"], ["messages.message_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_assertion_id"],
            ["case_fact_assertions.assertion_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["context_tool_call_id"], ["tool_calls.tool_call_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("assertion_id", name=op.f("pk_case_fact_assertions")),
        sa.UniqueConstraint("case_id", "assertion_sequence", name="uq_case_fact_case_sequence"),
        sa.UniqueConstraint(
            "case_id", "candidate_fingerprint", name="uq_case_fact_candidate_replay"
        ),
    )
    op.create_index(op.f("ix_case_fact_assertions_case_id"), "case_fact_assertions", ["case_id"])
    op.create_index(
        op.f("ix_case_fact_assertions_source_message_id"),
        "case_fact_assertions",
        ["source_message_id"],
    )

    op.create_table(
        "case_fact_questions",
        sa.Column("question_id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("fact_code", sa.String(length=64), nullable=False),
        sa.Column("context_result_hash", sa.String(length=64), nullable=True),
        sa.Column("targeted_conflict", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "asked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "fact_code IN ('customer_still_reports_missing', 'reported_delivery_location_checked')",
            name=op.f("ck_case_fact_questions_fact_code"),
        ),
        sa.ForeignKeyConstraint(["case_id"], ["investigation_cases.case_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("question_id", name=op.f("pk_case_fact_questions")),
    )
    op.create_index(op.f("ix_case_fact_questions_case_id"), "case_fact_questions", ["case_id"])

    op.create_table(
        "case_fact_snapshots",
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("snapshot_payload", sa.JSON(), nullable=False),
        sa.Column("rebuilt_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["investigation_cases.case_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("case_id", name=op.f("pk_case_fact_snapshots")),
    )

    with op.batch_alter_table("action_proposals") as batch:
        batch.add_column(
            sa.Column("case_fact_identity", sa.JSON(), nullable=False, server_default="{}")
        )


def downgrade() -> None:
    with op.batch_alter_table("action_proposals") as batch:
        batch.drop_column("case_fact_identity")
    op.drop_table("case_fact_snapshots")
    op.drop_table("case_fact_questions")
    op.drop_table("case_fact_assertions")

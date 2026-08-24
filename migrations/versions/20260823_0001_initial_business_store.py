"""Create authoritative business state and canonical event tables.

Revision ID: 20260823_0001
Revises: None
Create Date: 2026-08-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260823_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps(*, updated: bool = False) -> list[sa.Column]:
    columns = [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        )
    ]
    if updated:
        columns.append(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            )
        )
    return columns


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("customer_id", sa.String(length=64), nullable=False),
        sa.Column("fixture_customer_key", sa.String(length=64), nullable=False),
        sa.Column("fixture_version", sa.String(length=64), nullable=False),
        sa.Column("llm_mode", sa.String(length=16), nullable=False),
        sa.Column("active_case_id", sa.String(length=64), nullable=True),
        sa.Column("next_event_sequence", sa.Integer(), nullable=False, server_default="0"),
        *_timestamps(updated=True),
        sa.CheckConstraint(
            "llm_mode IN ('mock', 'live')", name=op.f("ck_conversations_llm_mode")
        ),
        sa.CheckConstraint(
            "next_event_sequence >= 0",
            name=op.f("ck_conversations_next_event_sequence_nonnegative"),
        ),
        sa.PrimaryKeyConstraint("conversation_id", name="pk_conversations"),
    )
    op.create_index("ix_conversations_customer_id", "conversations", ["customer_id"])
    op.create_index("ix_conversations_active_case_id", "conversations", ["active_case_id"])

    op.create_table(
        "messages",
        sa.Column("message_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "role IN ('customer', 'assistant')", name=op.f("ck_messages_role")
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.conversation_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("message_id", name="pk_messages"),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_index("ix_messages_case_id", "messages", ["case_id"])
    op.create_index("ix_messages_run_id", "messages", ["run_id"])

    op.create_table(
        "triage_records",
        sa.Column("triage_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("message_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("intent", sa.String(length=32), nullable=False),
        sa.Column("risk_flags", sa.JSON(), nullable=False),
        sa.Column("order_ids_mentioned", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "intent IN ('signed_not_received', 'stalled_tracking', 'other_logistics', "
            "'ambiguous', 'out_of_scope', 'prohibited')",
            name=op.f("ck_triage_records_intent"),
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name=op.f("ck_triage_records_confidence_range"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.conversation_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["message_id"], ["messages.message_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("triage_id", name="pk_triage_records"),
    )
    op.create_index("ix_triage_records_conversation_id", "triage_records", ["conversation_id"])
    op.create_index("ix_triage_records_message_id", "triage_records", ["message_id"])
    op.create_index("ix_triage_records_run_id", "triage_records", ["run_id"])

    op.create_table(
        "policy_decisions",
        sa.Column("policy_decision_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("message_id", sa.String(length=64), nullable=False),
        sa.Column("triage_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("route", sa.String(length=32), nullable=False),
        sa.Column("supported", sa.Boolean(), nullable=False),
        sa.Column("canonical_issue_type", sa.String(length=32), nullable=True),
        sa.Column("authorized_order_id", sa.String(length=64), nullable=True),
        sa.Column("blocked_fragments", sa.JSON(), nullable=False),
        sa.Column("risk_flags", sa.JSON(), nullable=False),
        sa.Column("reason_code", sa.String(length=96), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.conversation_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["message_id"], ["messages.message_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["triage_id"], ["triage_records.triage_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("policy_decision_id", name="pk_policy_decisions"),
    )
    op.create_index("ix_policy_decisions_conversation_id", "policy_decisions", ["conversation_id"])
    op.create_index("ix_policy_decisions_message_id", "policy_decisions", ["message_id"])
    op.create_index("ix_policy_decisions_triage_id", "policy_decisions", ["triage_id"])
    op.create_index("ix_policy_decisions_run_id", "policy_decisions", ["run_id"])

    op.create_table(
        "investigation_cases",
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("customer_id", sa.String(length=64), nullable=False),
        sa.Column("related_case_id", sa.String(length=64), nullable=True),
        sa.Column("authorized_order_id", sa.String(length=64), nullable=False),
        sa.Column("reported_issue_type", sa.String(length=32), nullable=False),
        sa.Column("canonical_issue_type", sa.String(length=32), nullable=False),
        sa.Column("issue_type_revision_history", sa.JSON(), nullable=False),
        sa.Column("case_state", sa.String(length=48), nullable=False),
        sa.Column("case_outcome", sa.String(length=48), nullable=True),
        sa.Column("reason_code", sa.String(length=96), nullable=True),
        sa.Column(
            "business_clarification_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "actual_read_tool_execution_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("agent_planning_turn_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active_proposal_id", sa.String(length=64), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        *_timestamps(updated=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "reported_issue_type IN ('signed_not_received', 'stalled_tracking')",
            name=op.f("ck_investigation_cases_reported_issue_type"),
        ),
        sa.CheckConstraint(
            "canonical_issue_type IN ('signed_not_received', 'stalled_tracking')",
            name=op.f("ck_investigation_cases_canonical_issue_type"),
        ),
        sa.CheckConstraint(
            "case_state IN ('investigating', 'awaiting_customer_input', "
            "'awaiting_customer_confirmation', 'awaiting_retry', 'executing_action', 'closed')",
            name=op.f("ck_investigation_cases_case_state"),
        ),
        sa.CheckConstraint(
            "case_outcome IS NULL OR case_outcome IN ('resolved_no_action', 'ticket_created', "
            "'human_support_required', 'uncertain', 'failed')",
            name=op.f("ck_investigation_cases_case_outcome"),
        ),
        sa.CheckConstraint(
            "(case_state = 'closed' AND case_outcome IS NOT NULL AND reason_code IS NOT NULL) "
            "OR (case_state <> 'closed' AND case_outcome IS NULL AND reason_code IS NULL)",
            name=op.f("ck_investigation_cases_case_closure_fields"),
        ),
        sa.CheckConstraint(
            "business_clarification_count BETWEEN 0 AND 2",
            name=op.f("ck_investigation_cases_business_clarification_budget"),
        ),
        sa.CheckConstraint(
            "agent_planning_turn_count BETWEEN 0 AND 16",
            name=op.f("ck_investigation_cases_case_planning_budget"),
        ),
        sa.CheckConstraint(
            "actual_read_tool_execution_count BETWEEN 0 AND 6",
            name=op.f("ck_investigation_cases_case_read_execution_budget"),
        ),
        sa.CheckConstraint(
            "revision >= 1", name=op.f("ck_investigation_cases_revision_positive")
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.conversation_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["related_case_id"], ["investigation_cases.case_id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("case_id", name="pk_investigation_cases"),
    )
    op.create_index(
        "ix_investigation_cases_conversation_id", "investigation_cases", ["conversation_id"]
    )
    op.create_index("ix_investigation_cases_customer_id", "investigation_cases", ["customer_id"])
    op.create_index(
        "ix_investigation_cases_authorized_order_id",
        "investigation_cases",
        ["authorized_order_id"],
    )

    op.create_table(
        "runs",
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=True),
        sa.Column("run_kind", sa.String(length=24), nullable=False),
        sa.Column("run_state", sa.String(length=16), nullable=False),
        sa.Column("planning_turn_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "actual_read_tool_execution_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("failure_code", sa.String(length=96), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        *_timestamps(),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "run_kind IN ('message', 'confirmation', 'decline', 'retry')",
            name=op.f("ck_runs_kind"),
        ),
        sa.CheckConstraint(
            "run_state IN ('queued', 'running', 'succeeded', 'failed')",
            name=op.f("ck_runs_state"),
        ),
        sa.CheckConstraint(
            "planning_turn_count BETWEEN 0 AND 8",
            name=op.f("ck_runs_planning_budget"),
        ),
        sa.CheckConstraint(
            "actual_read_tool_execution_count BETWEEN 0 AND 6",
            name=op.f("ck_runs_read_execution_budget"),
        ),
        sa.CheckConstraint(
            "(run_state = 'failed' AND failure_code IS NOT NULL) OR "
            "(run_state <> 'failed' AND failure_code IS NULL)",
            name=op.f("ck_runs_failure_code_state"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.conversation_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["investigation_cases.case_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("run_id", name="pk_runs"),
    )
    op.create_index("ix_runs_conversation_id", "runs", ["conversation_id"])
    op.create_index("ix_runs_case_id", "runs", ["case_id"])

    op.create_table(
        "tool_calls",
        sa.Column("tool_call_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("tool_name", sa.String(length=96), nullable=False),
        sa.Column("normalized_args", sa.JSON(), nullable=False),
        sa.Column("planning_turn", sa.Integer(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("actual_execution", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cache_hit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("execution_status", sa.String(length=32), nullable=True),
        sa.Column("evidence_availability", sa.String(length=24), nullable=True),
        sa.Column("result_envelope", sa.JSON(), nullable=True),
        sa.Column("result_hash", sa.String(length=80), nullable=True),
        sa.Column("source_version", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=96), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "planning_turn >= 1", name=op.f("ck_tool_calls_planning_turn_positive")
        ),
        sa.CheckConstraint(
            "attempt_number >= 1", name=op.f("ck_tool_calls_attempt_number_positive")
        ),
        sa.CheckConstraint(
            "execution_status IS NULL OR execution_status IN "
            "('success', 'retryable_error', 'non_retryable_error')",
            name=op.f("ck_tool_calls_execution_status"),
        ),
        sa.CheckConstraint(
            "evidence_availability IS NULL OR evidence_availability IN "
            "('present', 'absent', 'unavailable')",
            name=op.f("ck_tool_calls_evidence_availability"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.conversation_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["investigation_cases.case_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tool_call_id", name="pk_tool_calls"),
    )
    op.create_index("ix_tool_calls_conversation_id", "tool_calls", ["conversation_id"])
    op.create_index("ix_tool_calls_case_id", "tool_calls", ["case_id"])
    op.create_index("ix_tool_calls_run_id", "tool_calls", ["run_id"])

    op.create_table(
        "action_proposals",
        sa.Column("proposal_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("proposal_state", sa.String(length=32), nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("execution_parameters", sa.JSON(), nullable=False),
        sa.Column("customer_visible_effect", sa.Text(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("evidence_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("superseded_by_proposal_id", sa.String(length=64), nullable=True),
        *_timestamps(),
        sa.Column(
            "expires_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "state_changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "version >= 1", name=op.f("ck_action_proposals_version_positive")
        ),
        sa.CheckConstraint(
            "proposal_state IN ('pending_confirmation', 'confirmed', 'declined', "
            "'superseded', 'expired', 'invalidated')",
            name=op.f("ck_action_proposals_state"),
        ),
        sa.CheckConstraint(
            "action_type = 'create_logistics_investigation_ticket'",
            name=op.f("ck_action_proposals_action_type"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_action_proposals_expiry_after_creation"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.conversation_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["investigation_cases.case_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("proposal_id", name="pk_action_proposals"),
        sa.UniqueConstraint("case_id", "version", name="uq_action_proposals_case_version"),
    )
    op.create_index("ix_action_proposals_conversation_id", "action_proposals", ["conversation_id"])
    op.create_index("ix_action_proposals_case_id", "action_proposals", ["case_id"])
    op.create_index(
        "uq_action_proposals_one_pending_per_case",
        "action_proposals",
        ["case_id"],
        unique=True,
        sqlite_where=sa.text("proposal_state = 'pending_confirmation'"),
    )

    op.create_table(
        "action_executions",
        sa.Column("action_id", sa.String(length=64), nullable=False),
        sa.Column("proposal_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("action_state", sa.String(length=24), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("error_code", sa.String(length=96), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(updated=True),
        sa.CheckConstraint(
            "action_state IN ('ready', 'submitted', 'succeeded', 'failed_retryable', "
            "'failed_terminal', 'uncertain')",
            name=op.f("ck_action_executions_state"),
        ),
        sa.CheckConstraint(
            "(action_state = 'ready' AND submitted_at IS NULL) OR "
            "(action_state <> 'ready' AND submitted_at IS NOT NULL)",
            name=op.f("ck_action_executions_submitted_timestamp"),
        ),
        sa.CheckConstraint(
            "(action_state = 'succeeded' AND verified_at IS NOT NULL) OR "
            "(action_state <> 'succeeded' AND verified_at IS NULL)",
            name=op.f("ck_action_executions_verified_timestamp"),
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["action_proposals.proposal_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.conversation_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["investigation_cases.case_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("action_id", name="pk_action_executions"),
        sa.UniqueConstraint("proposal_id", name="uq_action_executions_proposal"),
        sa.UniqueConstraint("idempotency_key", name="uq_action_executions_idempotency_key"),
    )
    op.create_index(
        "ix_action_executions_conversation_id", "action_executions", ["conversation_id"]
    )
    op.create_index("ix_action_executions_case_id", "action_executions", ["case_id"])

    op.create_table(
        "tickets",
        sa.Column("ticket_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("action_id", sa.String(length=64), nullable=False),
        sa.Column("customer_id", sa.String(length=64), nullable=False),
        sa.Column("authorized_order_id", sa.String(length=64), nullable=False),
        sa.Column("issue_type", sa.String(length=32), nullable=False),
        sa.Column("ticket_state", sa.String(length=16), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        *_timestamps(updated=True),
        sa.CheckConstraint(
            "ticket_state IN ('active', 'closed')", name=op.f("ck_tickets_state")
        ),
        sa.CheckConstraint(
            "issue_type IN ('signed_not_received', 'stalled_tracking')",
            name=op.f("ck_tickets_issue_type"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.conversation_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["investigation_cases.case_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["action_id"], ["action_executions.action_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("ticket_id", name="pk_tickets"),
        sa.UniqueConstraint("action_id", name="uq_tickets_action"),
        sa.UniqueConstraint("idempotency_key", name="uq_tickets_idempotency_key"),
    )
    op.create_index("ix_tickets_conversation_id", "tickets", ["conversation_id"])
    op.create_index("ix_tickets_case_id", "tickets", ["case_id"])
    op.create_index("ix_tickets_customer_id", "tickets", ["customer_id"])
    op.create_index("ix_tickets_authorized_order_id", "tickets", ["authorized_order_id"])
    op.create_index(
        "uq_tickets_one_active_per_order_issue",
        "tickets",
        ["authorized_order_id", "issue_type"],
        unique=True,
        sqlite_where=sa.text("ticket_state = 'active'"),
    )

    op.create_table(
        "events",
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("event_type", sa.String(length=96), nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "schema_version >= 1", name=op.f("ck_events_schema_version_positive")
        ),
        sa.CheckConstraint("sequence >= 1", name=op.f("ck_events_sequence_positive")),
        sa.CheckConstraint(
            "visibility IN ('customer', 'developer', 'both')",
            name=op.f("ck_events_visibility"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.conversation_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("event_id", name="pk_events"),
        sa.UniqueConstraint("conversation_id", "sequence", name="uq_events_conversation_sequence"),
    )
    op.create_index("ix_events_case_id", "events", ["case_id"])
    op.create_index("ix_events_run_id", "events", ["run_id"])
    op.create_index("ix_events_event_type", "events", ["event_type"])
    op.create_index("ix_events_conversation_sequence", "events", ["conversation_id", "sequence"])


def downgrade() -> None:
    op.drop_table("events")
    op.drop_table("tickets")
    op.drop_table("action_executions")
    op.drop_table("action_proposals")
    op.drop_table("tool_calls")
    op.drop_table("runs")
    op.drop_table("investigation_cases")
    op.drop_table("policy_decisions")
    op.drop_table("triage_records")
    op.drop_table("messages")
    op.drop_table("conversations")

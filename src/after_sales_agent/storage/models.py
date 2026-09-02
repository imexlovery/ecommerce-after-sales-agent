"""SQLAlchemy 2 current-state tables and append-only audit rows."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def utc_now() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Round-trip timezone-aware UTC timestamps through SQLite."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class ConversationRow(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint("llm_mode IN ('mock', 'live')", name="llm_mode"),
        CheckConstraint("next_event_sequence >= 0", name="next_event_sequence_nonnegative"),
    )

    conversation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    fixture_customer_key: Mapped[str] = mapped_column(String(64), nullable=False)
    fixture_version: Mapped[str] = mapped_column(String(64), nullable=False, default="fixture-v1")
    llm_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    active_case_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    next_event_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )


class MessageRow(Base):
    __tablename__ = "messages"
    __table_args__ = (CheckConstraint("role IN ('customer', 'assistant')", name="role"),)

    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    case_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class TriageRecordRow(Base):
    __tablename__ = "triage_records"
    __table_args__ = (
        CheckConstraint(
            "intent IN ('signed_not_received', 'stalled_tracking', 'capability_help', "
            "'order_id_help', 'tracking_status_query', 'delivery_eta_info', "
            "'change_delivery_info', 'refund_return_info', 'human_support_request', "
            "'thanks_close', 'other_logistics', 'ambiguous', 'out_of_scope', 'prohibited')",
            name="intent",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
    )

    triage_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"), nullable=False, index=True
    )
    message_id: Mapped[str] = mapped_column(
        ForeignKey("messages.message_id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    intent: Mapped[str] = mapped_column(String(32), nullable=False)
    risk_flags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    order_ids_mentioned: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class PolicyDecisionRow(Base):
    __tablename__ = "policy_decisions"

    policy_decision_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"), nullable=False, index=True
    )
    message_id: Mapped[str] = mapped_column(
        ForeignKey("messages.message_id", ondelete="CASCADE"), nullable=False, index=True
    )
    triage_id: Mapped[str] = mapped_column(
        ForeignKey("triage_records.triage_id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    route: Mapped[str] = mapped_column(String(32), nullable=False)
    supported: Mapped[bool] = mapped_column(Boolean, nullable=False)
    canonical_issue_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    authorized_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    blocked_fragments: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    risk_flags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    reason_code: Mapped[str] = mapped_column(String(96), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class InvestigationCaseRow(Base):
    __tablename__ = "investigation_cases"
    __table_args__ = (
        CheckConstraint(
            "reported_issue_type IN ('signed_not_received', 'stalled_tracking')",
            name="reported_issue_type",
        ),
        CheckConstraint(
            "canonical_issue_type IN ('signed_not_received', 'stalled_tracking')",
            name="canonical_issue_type",
        ),
        CheckConstraint(
            "case_state IN ('investigating', 'awaiting_customer_input', "
            "'awaiting_customer_confirmation', 'awaiting_retry', 'executing_action', 'closed')",
            name="case_state",
        ),
        CheckConstraint(
            "case_outcome IS NULL OR case_outcome IN ('resolved_no_action', 'ticket_created', "
            "'human_support_required', 'uncertain', 'failed')",
            name="case_outcome",
        ),
        CheckConstraint(
            "(case_state = 'closed' AND case_outcome IS NOT NULL AND reason_code IS NOT NULL) "
            "OR (case_state <> 'closed' AND case_outcome IS NULL AND reason_code IS NULL)",
            name="case_closure_fields",
        ),
        CheckConstraint(
            "business_clarification_count BETWEEN 0 AND 2",
            name="business_clarification_budget",
        ),
        CheckConstraint("agent_planning_turn_count BETWEEN 0 AND 16", name="case_planning_budget"),
        CheckConstraint(
            "actual_read_tool_execution_count BETWEEN 0 AND 6",
            name="case_read_execution_budget",
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
    )

    case_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"), nullable=False, index=True
    )
    customer_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    related_case_id: Mapped[str | None] = mapped_column(
        ForeignKey("investigation_cases.case_id", ondelete="SET NULL"), nullable=True
    )
    authorized_order_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_shipment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reported_issue_type: Mapped[str] = mapped_column(String(32), nullable=False)
    canonical_issue_type: Mapped[str] = mapped_column(String(32), nullable=False)
    issue_type_revision_history: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    case_state: Mapped[str] = mapped_column(String(48), nullable=False, default="investigating")
    case_outcome: Mapped[str | None] = mapped_column(String(48), nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    business_clarification_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    actual_read_tool_execution_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    agent_planning_turn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_proposal_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class RunRow(Base):
    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint("run_kind IN ('message', 'confirmation', 'decline', 'retry')", name="kind"),
        CheckConstraint("run_state IN ('queued', 'running', 'succeeded', 'failed')", name="state"),
        CheckConstraint("planning_turn_count BETWEEN 0 AND 8", name="planning_budget"),
        CheckConstraint(
            "actual_read_tool_execution_count BETWEEN 0 AND 6", name="read_execution_budget"
        ),
        CheckConstraint(
            "(run_state = 'failed' AND failure_code IS NOT NULL) OR "
            "(run_state <> 'failed' AND failure_code IS NULL)",
            name="failure_code_state",
        ),
    )

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_id: Mapped[str | None] = mapped_column(
        ForeignKey("investigation_cases.case_id", ondelete="CASCADE"), nullable=True, index=True
    )
    run_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    run_state: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    planning_turn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    actual_read_tool_execution_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    failure_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class ToolCallRow(Base):
    __tablename__ = "tool_calls"
    __table_args__ = (
        CheckConstraint("planning_turn >= 1", name="planning_turn_positive"),
        CheckConstraint("attempt_number >= 1", name="attempt_number_positive"),
        CheckConstraint(
            "execution_status IS NULL OR execution_status IN "
            "('success', 'retryable_error', 'non_retryable_error')",
            name="execution_status",
        ),
        CheckConstraint(
            "evidence_availability IS NULL OR evidence_availability IN "
            "('present', 'absent', 'unavailable')",
            name="evidence_availability",
        ),
    )

    tool_call_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_id: Mapped[str] = mapped_column(
        ForeignKey("investigation_cases.case_id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False, index=True
    )
    tool_name: Mapped[str] = mapped_column(String(96), nullable=False)
    normalized_args: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    planning_turn: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    actual_execution: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    execution_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    evidence_availability: Mapped[str | None] = mapped_column(String(24), nullable=True)
    result_envelope: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    result_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class CaseFactAssertionRow(Base):
    __tablename__ = "case_fact_assertions"
    __table_args__ = (
        UniqueConstraint("case_id", "assertion_sequence", name="uq_case_fact_case_sequence"),
        UniqueConstraint("case_id", "candidate_fingerprint", name="uq_case_fact_candidate_replay"),
        CheckConstraint(
            "fact_code IN ('customer_still_reports_missing', 'reported_delivery_location_checked')",
            name="fact_code",
        ),
        CheckConstraint("value IN ('true', 'false', 'unknown')", name="value"),
        CheckConstraint(
            "relation IN ('new', 'repeat', 'correction', 'withdrawal')", name="relation"
        ),
        CheckConstraint("assertion_sequence >= 1", name="sequence_positive"),
    )

    assertion_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        ForeignKey("investigation_cases.case_id", ondelete="CASCADE"), nullable=False, index=True
    )
    fact_code: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(String(16), nullable=False)
    source_message_id: Mapped[str] = mapped_column(
        ForeignKey("messages.message_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_message_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_span_start: Mapped[int] = mapped_column(Integer, nullable=False)
    source_span_end: Mapped[int] = mapped_column(Integer, nullable=False)
    relation: Mapped[str] = mapped_column(String(16), nullable=False)
    supersedes_assertion_id: Mapped[str | None] = mapped_column(
        ForeignKey("case_fact_assertions.assertion_id", ondelete="RESTRICT"), nullable=True
    )
    extractor_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(128), nullable=False)
    context_tool_call_id: Mapped[str | None] = mapped_column(
        ForeignKey("tool_calls.tool_call_id", ondelete="RESTRICT"), nullable=True
    )
    context_result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    assertion_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class CaseFactQuestionRow(Base):
    __tablename__ = "case_fact_questions"
    __table_args__ = (
        CheckConstraint(
            "fact_code IN ('customer_still_reports_missing', 'reported_delivery_location_checked')",
            name="fact_code",
        ),
    )

    question_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        ForeignKey("investigation_cases.case_id", ondelete="CASCADE"), nullable=False, index=True
    )
    fact_code: Mapped[str] = mapped_column(String(64), nullable=False)
    context_result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    targeted_conflict: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    asked_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class CaseFactMessageConsumptionRow(Base):
    """Append-only binding of one outstanding question to one customer reply."""

    __tablename__ = "case_fact_message_consumptions"
    __table_args__ = (
        UniqueConstraint("question_id", name="uq_case_fact_consumption_question"),
        UniqueConstraint("source_message_id", name="uq_case_fact_consumption_message"),
        CheckConstraint("outcome IN ('accepted', 'rejected', 'empty')", name="outcome"),
    )

    consumption_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        ForeignKey("investigation_cases.case_id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_id: Mapped[str] = mapped_column(
        ForeignKey("case_fact_questions.question_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_message_id: Mapped[str] = mapped_column(
        ForeignKey("messages.message_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_message_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_batch_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(96), nullable=False)
    assertion_id: Mapped[str | None] = mapped_column(
        ForeignKey("case_fact_assertions.assertion_id", ondelete="RESTRICT"), nullable=True
    )
    decision_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class CaseFactSnapshotRow(Base):
    __tablename__ = "case_fact_snapshots"

    case_id: Mapped[str] = mapped_column(
        ForeignKey("investigation_cases.case_id", ondelete="CASCADE"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    rebuilt_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ActionProposalRow(Base):
    __tablename__ = "action_proposals"
    __table_args__ = (
        UniqueConstraint("case_id", "version", name="uq_action_proposals_case_version"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "proposal_state IN ('pending_confirmation', 'confirmed', 'declined', "
            "'superseded', 'expired', 'invalidated')",
            name="state",
        ),
        CheckConstraint(
            "action_type = 'create_logistics_investigation_ticket'", name="action_type"
        ),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        Index(
            "uq_action_proposals_one_pending_per_case",
            "case_id",
            unique=True,
            sqlite_where=text("proposal_state = 'pending_confirmation'"),
        ),
    )

    proposal_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_id: Mapped[str] = mapped_column(
        ForeignKey("investigation_cases.case_id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    proposal_state: Mapped[str] = mapped_column(String(32), nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    customer_visible_effect: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    evidence_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    case_fact_identity: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    superseded_by_proposal_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    state_changed_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now
    )


class ActionExecutionRow(Base):
    __tablename__ = "action_executions"
    __table_args__ = (
        UniqueConstraint("proposal_id", name="uq_action_executions_proposal"),
        UniqueConstraint("idempotency_key", name="uq_action_executions_idempotency_key"),
        CheckConstraint(
            "action_state IN ('ready', 'submitted', 'succeeded', 'failed_retryable', "
            "'failed_terminal', 'uncertain')",
            name="state",
        ),
        CheckConstraint(
            "(action_state = 'ready' AND submitted_at IS NULL) OR "
            "(action_state <> 'ready' AND submitted_at IS NOT NULL)",
            name="submitted_timestamp",
        ),
        CheckConstraint(
            "(action_state = 'succeeded' AND verified_at IS NOT NULL) OR "
            "(action_state <> 'succeeded' AND verified_at IS NULL)",
            name="verified_timestamp",
        ),
    )

    action_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(
        ForeignKey("action_proposals.proposal_id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_id: Mapped[str] = mapped_column(
        ForeignKey("investigation_cases.case_id", ondelete="CASCADE"), nullable=False, index=True
    )
    action_state: Mapped[str] = mapped_column(String(24), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )


class TicketRow(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        UniqueConstraint("action_id", name="uq_tickets_action"),
        UniqueConstraint("idempotency_key", name="uq_tickets_idempotency_key"),
        CheckConstraint("ticket_state IN ('active', 'closed')", name="state"),
        CheckConstraint(
            "issue_type IN ('signed_not_received', 'stalled_tracking')", name="issue_type"
        ),
        Index(
            "uq_tickets_one_active_per_order_issue",
            "authorized_order_id",
            "issue_type",
            unique=True,
            sqlite_where=text(
                "ticket_state = 'active' AND target_shipment_id IS NULL"
            ),
        ),
        Index(
            "uq_tickets_one_active_per_order_issue_target",
            "authorized_order_id",
            "issue_type",
            "target_shipment_id",
            unique=True,
            sqlite_where=text(
                "ticket_state = 'active' AND target_shipment_id IS NOT NULL"
            ),
        ),
    )

    ticket_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_id: Mapped[str] = mapped_column(
        ForeignKey("investigation_cases.case_id", ondelete="CASCADE"), nullable=False, index=True
    )
    action_id: Mapped[str] = mapped_column(
        ForeignKey("action_executions.action_id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    authorized_order_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_shipment_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    issue_type: Mapped[str] = mapped_column(String(32), nullable=False)
    ticket_state: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )


class EventRow(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence", name="uq_events_conversation_sequence"),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint("visibility IN ('customer', 'developer', 'both')", name="visibility"),
        Index("ix_events_conversation_sequence", "conversation_id", "sequence"),
    )

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"), nullable=False
    )
    case_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)


@event.listens_for(EventRow, "before_update")
def _reject_event_update(mapper: object, connection: object, target: EventRow) -> None:
    raise RuntimeError("canonical events are append-only")


@event.listens_for(EventRow, "before_delete")
def _reject_event_delete(mapper: object, connection: object, target: EventRow) -> None:
    raise RuntimeError("canonical events may be deleted only by the bulk demo reset")


@event.listens_for(CaseFactAssertionRow, "before_update")
def _reject_case_fact_assertion_update(
    mapper: object, connection: object, target: CaseFactAssertionRow
) -> None:
    raise RuntimeError("CaseFactAssertion rows are append-only")


@event.listens_for(CaseFactAssertionRow, "before_delete")
def _reject_case_fact_assertion_delete(
    mapper: object, connection: object, target: CaseFactAssertionRow
) -> None:
    raise RuntimeError("CaseFactAssertion rows may be deleted only by the bulk demo reset")


@event.listens_for(CaseFactQuestionRow, "before_update")
def _reject_case_fact_question_update(
    mapper: object, connection: object, target: CaseFactQuestionRow
) -> None:
    raise RuntimeError("Case Fact questions are append-only")


@event.listens_for(CaseFactQuestionRow, "before_delete")
def _reject_case_fact_question_delete(
    mapper: object, connection: object, target: CaseFactQuestionRow
) -> None:
    raise RuntimeError("Case Fact questions may be deleted only by the bulk demo reset")


@event.listens_for(CaseFactMessageConsumptionRow, "before_update")
def _reject_case_fact_consumption_update(
    mapper: object, connection: object, target: CaseFactMessageConsumptionRow
) -> None:
    raise RuntimeError("Case Fact message consumptions are append-only")


@event.listens_for(CaseFactMessageConsumptionRow, "before_delete")
def _reject_case_fact_consumption_delete(
    mapper: object, connection: object, target: CaseFactMessageConsumptionRow
) -> None:
    raise RuntimeError("Case Fact message consumptions may be deleted only by the bulk demo reset")

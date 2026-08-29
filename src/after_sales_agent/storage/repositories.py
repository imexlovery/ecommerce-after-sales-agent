"""Session-bound repositories for deterministic business state."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .models import (
    ActionExecutionRow,
    ActionProposalRow,
    CaseFactAssertionRow,
    CaseFactMessageConsumptionRow,
    CaseFactQuestionRow,
    CaseFactSnapshotRow,
    ConversationRow,
    EventRow,
    InvestigationCaseRow,
    MessageRow,
    PolicyDecisionRow,
    RunRow,
    TicketRow,
    ToolCallRow,
    TriageRecordRow,
    utc_now,
)

if TYPE_CHECKING:
    from after_sales_agent.domain.models import (
        ActionExecution,
        ActionProposal,
        InvestigationCase,
        Run,
        TriageResult,
    )

_UNSET = object()


class StorageError(RuntimeError):
    """Base class for deterministic persistence failures."""


class StorageNotFoundError(StorageError):
    pass


class ConcurrentMutationError(StorageError):
    pass


class InvalidStateTransitionError(StorageError):
    pass


class IdempotencyConflictError(StorageError):
    pass


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return value


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("JSON timestamps must be timezone-aware")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _require[T](row: T | None, resource: str, identifier: str) -> T:
    if row is None:
        raise StorageNotFoundError(f"{resource} {identifier!r} was not found")
    return row


class Repository:
    """CRUD and guarded state transitions bound to one SQLAlchemy Session."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # Conversations and messages -------------------------------------------------
    def create_conversation(
        self,
        customer_id: str,
        fixture_key: str,
        llm_mode: str,
        *,
        conversation_id: str | None = None,
        fixture_version: str = "fixture-v1",
        created_at: datetime | None = None,
    ) -> ConversationRow:
        row = ConversationRow(
            conversation_id=conversation_id or _new_id("conv"),
            customer_id=customer_id,
            fixture_customer_key=fixture_key,
            fixture_version=fixture_version,
            llm_mode=str(_value(llm_mode)),
            created_at=created_at or utc_now(),
            updated_at=created_at or utc_now(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_conversation(self, conversation_id: str) -> ConversationRow | None:
        return self.session.get(ConversationRow, conversation_id)

    def require_conversation(self, conversation_id: str) -> ConversationRow:
        return _require(self.get_conversation(conversation_id), "Conversation", conversation_id)

    def add_message(
        self,
        conversation_id: str,
        role: Literal["customer", "assistant"] | str,
        content: str,
        *,
        message_id: str | None = None,
        case_id: str | None = None,
        run_id: str | None = None,
        trace_id: str | None = None,
        created_at: datetime | None = None,
    ) -> MessageRow:
        self.require_conversation(conversation_id)
        row = MessageRow(
            message_id=message_id or _new_id("msg"),
            conversation_id=conversation_id,
            case_id=case_id,
            run_id=run_id,
            role=str(role),
            content=content,
            trace_id=trace_id,
            created_at=created_at or utc_now(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_message(self, message_id: str) -> MessageRow | None:
        return self.session.get(MessageRow, message_id)

    def list_messages(self, conversation_id: str) -> list[MessageRow]:
        statement = (
            select(MessageRow)
            .where(MessageRow.conversation_id == conversation_id)
            .order_by(MessageRow.created_at, MessageRow.message_id)
        )
        return list(self.session.scalars(statement))

    # Triage and policy -----------------------------------------------------------
    def create_triage_record(
        self,
        triage: TriageResult,
        *,
        conversation_id: str,
        message_id: str,
        run_id: str,
        triage_id: str | None = None,
        schema_version: int = 1,
        created_at: datetime | None = None,
    ) -> TriageRecordRow:
        row = TriageRecordRow(
            triage_id=triage_id or _new_id("triage"),
            conversation_id=conversation_id,
            message_id=message_id,
            run_id=run_id,
            schema_version=schema_version,
            intent=str(_value(triage.intent)),
            risk_flags=list(triage.risk_flags),
            order_ids_mentioned=list(triage.order_ids_mentioned),
            confidence=triage.confidence,
            created_at=created_at or utc_now(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_triage_record(self, triage_id: str) -> TriageRecordRow | None:
        return self.session.get(TriageRecordRow, triage_id)

    def create_policy_decision(
        self,
        *,
        conversation_id: str,
        message_id: str,
        triage_id: str,
        run_id: str,
        route: str,
        supported: bool,
        reason_code: str,
        policy_decision_id: str | None = None,
        canonical_issue_type: str | None = None,
        authorized_order_id: str | None = None,
        blocked_fragments: Sequence[dict[str, Any]] = (),
        risk_flags: Sequence[str] = (),
        created_at: datetime | None = None,
    ) -> PolicyDecisionRow:
        row = PolicyDecisionRow(
            policy_decision_id=policy_decision_id or _new_id("policy"),
            conversation_id=conversation_id,
            message_id=message_id,
            triage_id=triage_id,
            run_id=run_id,
            route=route,
            supported=supported,
            canonical_issue_type=(
                str(_value(canonical_issue_type)) if canonical_issue_type is not None else None
            ),
            authorized_order_id=authorized_order_id,
            blocked_fragments=_jsonable(list(blocked_fragments)),
            risk_flags=list(risk_flags),
            reason_code=reason_code,
            created_at=created_at or utc_now(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def list_policy_decisions(self, conversation_id: str) -> list[PolicyDecisionRow]:
        statement = (
            select(PolicyDecisionRow)
            .where(PolicyDecisionRow.conversation_id == conversation_id)
            .order_by(PolicyDecisionRow.created_at, PolicyDecisionRow.policy_decision_id)
        )
        return list(self.session.scalars(statement))

    # Cases ----------------------------------------------------------------------
    def create_case(
        self,
        case: InvestigationCase,
        *,
        reported_issue_type: str | None = None,
        created_at: datetime | None = None,
    ) -> InvestigationCaseRow:
        now = created_at or utc_now()
        canonical_issue_type = str(_value(case.canonical_issue_type))
        row = InvestigationCaseRow(
            case_id=case.case_id,
            conversation_id=case.conversation_id,
            customer_id=case.customer_id,
            related_case_id=case.related_case_id,
            authorized_order_id=case.authorized_order_id,
            target_shipment_id=case.target_shipment_id,
            reported_issue_type=reported_issue_type or canonical_issue_type,
            canonical_issue_type=canonical_issue_type,
            issue_type_revision_history=_jsonable(case.issue_type_revision_history),
            case_state=str(_value(case.case_state)),
            case_outcome=(
                str(_value(case.case_outcome)) if case.case_outcome is not None else None
            ),
            reason_code=case.reason_code,
            business_clarification_count=case.business_clarifications,
            agent_planning_turn_count=case.planning_turns,
            actual_read_tool_execution_count=case.read_tool_executions,
            created_at=now,
            updated_at=now,
            closed_at=now if str(_value(case.case_state)) == "closed" else None,
        )
        conversation = self.require_conversation(case.conversation_id)
        self.session.add(row)
        conversation.active_case_id = row.case_id if row.case_state != "closed" else None
        conversation.updated_at = now
        self.session.flush()
        return row

    def get_case(self, case_id: str) -> InvestigationCaseRow | None:
        return self.session.get(InvestigationCaseRow, case_id)

    def require_case(self, case_id: str) -> InvestigationCaseRow:
        return _require(self.get_case(case_id), "InvestigationCase", case_id)

    def list_cases(self, conversation_id: str) -> list[InvestigationCaseRow]:
        statement = (
            select(InvestigationCaseRow)
            .where(InvestigationCaseRow.conversation_id == conversation_id)
            .order_by(InvestigationCaseRow.created_at, InvestigationCaseRow.case_id)
        )
        return list(self.session.scalars(statement))

    def get_active_case(self, conversation_id: str) -> InvestigationCaseRow | None:
        conversation = self.get_conversation(conversation_id)
        if conversation is None or conversation.active_case_id is None:
            return None
        row = self.get_case(conversation.active_case_id)
        if row is None or row.case_state == "closed":
            return None
        return row

    def update_case(
        self,
        case_id: str,
        *,
        expected_revision: int | None = None,
        case_state: str | None = None,
        case_outcome: str | None | object = _UNSET,
        reason_code: str | None | object = _UNSET,
        business_clarification_count: int | None = None,
        actual_read_tool_execution_count: int | None = None,
        agent_planning_turn_count: int | None = None,
        active_proposal_id: str | None | object = _UNSET,
        target_shipment_id: str | None | object = _UNSET,
        updated_at: datetime | None = None,
    ) -> InvestigationCaseRow:
        row = self.require_case(case_id)
        if expected_revision is not None and row.revision != expected_revision:
            raise ConcurrentMutationError(
                f"Case {case_id!r} revision {row.revision} did not match {expected_revision}"
            )
        if row.case_state == "closed":
            raise InvalidStateTransitionError("a closed Case is immutable and cannot reopen")

        next_state = str(_value(case_state)) if case_state is not None else row.case_state
        next_outcome = (
            row.case_outcome
            if case_outcome is _UNSET
            else (str(_value(case_outcome)) if case_outcome is not None else None)
        )
        next_reason = row.reason_code if reason_code is _UNSET else reason_code
        if next_state == "closed":
            if next_outcome is None or not next_reason:
                raise InvalidStateTransitionError(
                    "closing a Case requires case_outcome and reason_code"
                )
        elif next_outcome is not None or next_reason is not None:
            raise InvalidStateTransitionError(
                "an open Case cannot have case_outcome or closure reason_code"
            )

        clarification_count = (
            row.business_clarification_count
            if business_clarification_count is None
            else business_clarification_count
        )
        read_count = (
            row.actual_read_tool_execution_count
            if actual_read_tool_execution_count is None
            else actual_read_tool_execution_count
        )
        turn_count = (
            row.agent_planning_turn_count
            if agent_planning_turn_count is None
            else agent_planning_turn_count
        )
        if not 0 <= clarification_count <= 2:
            raise ValueError("business clarification budget is 0..2")
        if not 0 <= read_count <= 6:
            raise ValueError("Case read-tool execution budget is 0..6")
        if not 0 <= turn_count <= 16:
            raise ValueError("Case planning-turn budget is 0..16")

        now = updated_at or utc_now()
        row.case_state = next_state
        row.case_outcome = next_outcome
        row.reason_code = next_reason  # type: ignore[assignment]
        row.business_clarification_count = clarification_count
        row.actual_read_tool_execution_count = read_count
        row.agent_planning_turn_count = turn_count
        if active_proposal_id is not _UNSET:
            row.active_proposal_id = active_proposal_id  # type: ignore[assignment]
        if target_shipment_id is not _UNSET:
            row.target_shipment_id = target_shipment_id  # type: ignore[assignment]
        row.updated_at = now
        row.closed_at = now if next_state == "closed" else None
        row.revision += 1

        conversation = self.require_conversation(row.conversation_id)
        if next_state == "closed" and conversation.active_case_id == case_id:
            conversation.active_case_id = None
            conversation.updated_at = now
        self.session.flush()
        return row

    def append_issue_revision(
        self,
        case_id: str,
        *,
        canonical_issue_type: str,
        revision_record: dict[str, Any],
        expected_revision: int | None = None,
    ) -> InvestigationCaseRow:
        row = self.require_case(case_id)
        if row.case_state == "closed":
            raise InvalidStateTransitionError("a closed Case is immutable")
        if expected_revision is not None and row.revision != expected_revision:
            raise ConcurrentMutationError("Case revision changed before issue correction")
        history = list(row.issue_type_revision_history)
        history.append(_jsonable(revision_record))
        row.issue_type_revision_history = history
        row.canonical_issue_type = str(_value(canonical_issue_type))
        row.revision += 1
        row.updated_at = utc_now()
        self.session.flush()
        return row

    # Runs and tool calls ---------------------------------------------------------
    def create_run(
        self,
        run: Run | None = None,
        *,
        conversation_id: str,
        run_kind: Literal["message", "confirmation", "decline", "retry"] | str,
        run_id: str | None = None,
        case_id: str | None = None,
        run_state: str = "queued",
        planning_turn_count: int = 0,
        actual_read_tool_execution_count: int = 0,
        failure_code: str | None = None,
        trace_id: str | None = None,
        created_at: datetime | None = None,
    ) -> RunRow:
        if run is not None:
            run_id = run.run_id
            case_id = run.case_id
            run_state = str(_value(run.run_state))
            planning_turn_count = run.planning_turns
            failure_code = run.failure_class
        row = RunRow(
            run_id=run_id or _new_id("run"),
            conversation_id=conversation_id,
            case_id=case_id,
            run_kind=str(run_kind),
            run_state=str(_value(run_state)),
            planning_turn_count=planning_turn_count,
            actual_read_tool_execution_count=actual_read_tool_execution_count,
            failure_code=failure_code,
            trace_id=trace_id,
            created_at=created_at or utc_now(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_run(self, run_id: str) -> RunRow | None:
        return self.session.get(RunRow, run_id)

    def require_run(self, run_id: str) -> RunRow:
        return _require(self.get_run(run_id), "Run", run_id)

    def list_runs(
        self, *, conversation_id: str | None = None, case_id: str | None = None
    ) -> list[RunRow]:
        statement = select(RunRow)
        if conversation_id is not None:
            statement = statement.where(RunRow.conversation_id == conversation_id)
        if case_id is not None:
            statement = statement.where(RunRow.case_id == case_id)
        statement = statement.order_by(RunRow.created_at, RunRow.run_id)
        return list(self.session.scalars(statement))

    def update_run(
        self,
        run_id: str,
        *,
        run_state: str,
        planning_turn_count: int | None = None,
        actual_read_tool_execution_count: int | None = None,
        failure_code: str | None = None,
        changed_at: datetime | None = None,
    ) -> RunRow:
        row = self.require_run(run_id)
        next_state = str(_value(run_state))
        allowed = {
            "queued": {"running", "failed"},
            "running": {"succeeded", "failed"},
            "succeeded": set(),
            "failed": set(),
        }
        if next_state != row.run_state and next_state not in allowed[row.run_state]:
            raise InvalidStateTransitionError(
                f"Run cannot transition from {row.run_state} to {next_state}"
            )
        if next_state == "failed" and not failure_code:
            raise InvalidStateTransitionError("a failed Run requires failure_code")
        if next_state != "failed" and failure_code is not None:
            raise InvalidStateTransitionError("failure_code belongs only to a failed Run")

        next_turns = row.planning_turn_count if planning_turn_count is None else planning_turn_count
        next_reads = (
            row.actual_read_tool_execution_count
            if actual_read_tool_execution_count is None
            else actual_read_tool_execution_count
        )
        if not 0 <= next_turns <= 8:
            raise ValueError("Run planning-turn budget is 0..8")
        if not 0 <= next_reads <= 6:
            raise ValueError("Run read-tool execution count is 0..6")

        now = changed_at or utc_now()
        row.run_state = next_state
        row.planning_turn_count = next_turns
        row.actual_read_tool_execution_count = next_reads
        row.failure_code = failure_code
        if next_state == "running" and row.started_at is None:
            row.started_at = now
        if next_state in {"succeeded", "failed"}:
            row.completed_at = now
        self.session.flush()
        return row

    def create_tool_call(
        self,
        *,
        conversation_id: str,
        case_id: str,
        run_id: str,
        tool_name: str,
        normalized_args: dict[str, Any],
        planning_turn: int,
        tool_call_id: str | None = None,
        attempt_number: int = 1,
        actual_execution: bool = False,
        cache_hit: bool = False,
        blocked: bool = False,
        requested_at: datetime | None = None,
    ) -> ToolCallRow:
        row = ToolCallRow(
            tool_call_id=tool_call_id or _new_id("call"),
            conversation_id=conversation_id,
            case_id=case_id,
            run_id=run_id,
            tool_name=tool_name,
            normalized_args=_jsonable(normalized_args),
            planning_turn=planning_turn,
            attempt_number=attempt_number,
            actual_execution=actual_execution,
            cache_hit=cache_hit,
            blocked=blocked,
            requested_at=requested_at or utc_now(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def complete_tool_call(
        self,
        tool_call_id: str,
        *,
        execution_status: str,
        evidence_availability: str,
        result_envelope: dict[str, Any] | None,
        result_hash: str | None = None,
        source_version: str | None = None,
        error_code: str | None = None,
        retryable: bool = False,
        completed_at: datetime | None = None,
    ) -> ToolCallRow:
        row = _require(self.session.get(ToolCallRow, tool_call_id), "ToolCall", tool_call_id)
        if row.completed_at is not None:
            raise InvalidStateTransitionError("a completed ToolCall is immutable")
        row.execution_status = str(_value(execution_status))
        row.evidence_availability = str(_value(evidence_availability))
        row.result_envelope = _jsonable(result_envelope) if result_envelope is not None else None
        row.result_hash = result_hash
        row.source_version = source_version
        row.error_code = error_code
        row.retryable = retryable
        row.completed_at = completed_at or utc_now()
        self.session.flush()
        return row

    def list_tool_calls(
        self, *, case_id: str | None = None, run_id: str | None = None
    ) -> list[ToolCallRow]:
        statement = select(ToolCallRow)
        if case_id is not None:
            statement = statement.where(ToolCallRow.case_id == case_id)
        if run_id is not None:
            statement = statement.where(ToolCallRow.run_id == run_id)
        statement = statement.order_by(ToolCallRow.requested_at, ToolCallRow.tool_call_id)
        return list(self.session.scalars(statement))

    # Append-only V3-B Case Facts ------------------------------------------------
    def list_case_fact_assertions(self, case_id: str) -> list[CaseFactAssertionRow]:
        statement = (
            select(CaseFactAssertionRow)
            .where(CaseFactAssertionRow.case_id == case_id)
            .order_by(CaseFactAssertionRow.assertion_sequence)
        )
        return list(self.session.scalars(statement))

    def get_case_fact_assertion(self, assertion_id: str) -> CaseFactAssertionRow | None:
        return self.session.get(CaseFactAssertionRow, assertion_id)

    def find_case_fact_candidate(
        self, case_id: str, candidate_fingerprint: str
    ) -> CaseFactAssertionRow | None:
        return self.session.scalar(
            select(CaseFactAssertionRow).where(
                CaseFactAssertionRow.case_id == case_id,
                CaseFactAssertionRow.candidate_fingerprint == candidate_fingerprint,
            )
        )

    def append_case_fact_assertion(
        self,
        assertion: Any,
        *,
        candidate_fingerprint: str,
    ) -> CaseFactAssertionRow:
        self.require_case(assertion.case_id)
        existing = self.find_case_fact_candidate(assertion.case_id, candidate_fingerprint)
        if existing is not None:
            return existing
        rows = self.list_case_fact_assertions(assertion.case_id)
        if assertion.assertion_sequence != len(rows) + 1:
            raise ConcurrentMutationError("Case Fact assertion sequence changed")
        row = CaseFactAssertionRow(
            assertion_id=assertion.assertion_id,
            case_id=assertion.case_id,
            fact_code=str(_value(assertion.fact_code)),
            value=str(_value(assertion.value)),
            source_message_id=assertion.source_message_id,
            source_message_hash=assertion.source_message_hash,
            source_span_start=assertion.source_span_start,
            source_span_end=assertion.source_span_end,
            relation=str(_value(assertion.relation)),
            supersedes_assertion_id=assertion.supersedes_assertion_id,
            extractor_kind=assertion.extractor_kind,
            extractor_version=assertion.extractor_version,
            context_tool_call_id=assertion.context_tool_call_id,
            context_result_hash=assertion.context_result_hash,
            assertion_sequence=assertion.assertion_sequence,
            candidate_fingerprint=candidate_fingerprint,
            recorded_at=assertion.recorded_at,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def list_case_fact_questions(self, case_id: str) -> list[CaseFactQuestionRow]:
        statement = (
            select(CaseFactQuestionRow)
            .where(CaseFactQuestionRow.case_id == case_id)
            .order_by(CaseFactQuestionRow.asked_at, CaseFactQuestionRow.question_id)
        )
        return list(self.session.scalars(statement))

    def append_case_fact_question(self, question: Any) -> CaseFactQuestionRow:
        existing = self.session.get(CaseFactQuestionRow, question.question_id)
        if existing is not None:
            if (
                existing.case_id != question.case_id
                or existing.fact_code != str(_value(question.fact_code))
                or existing.context_result_hash != question.context_result_hash
                or existing.targeted_conflict != question.targeted_conflict
            ):
                raise IdempotencyConflictError("question_id cannot be reused across Case facts")
            return existing
        self.require_case(question.case_id)
        row = CaseFactQuestionRow(
            question_id=question.question_id,
            case_id=question.case_id,
            fact_code=str(_value(question.fact_code)),
            context_result_hash=question.context_result_hash,
            targeted_conflict=question.targeted_conflict,
            asked_at=question.asked_at,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_case_fact_consumption_for_question(
        self, question_id: str
    ) -> CaseFactMessageConsumptionRow | None:
        return self.session.scalar(
            select(CaseFactMessageConsumptionRow).where(
                CaseFactMessageConsumptionRow.question_id == question_id
            )
        )

    def get_case_fact_consumption_for_message(
        self, source_message_id: str
    ) -> CaseFactMessageConsumptionRow | None:
        return self.session.scalar(
            select(CaseFactMessageConsumptionRow).where(
                CaseFactMessageConsumptionRow.source_message_id == source_message_id
            )
        )

    def list_case_fact_message_consumptions(
        self, case_id: str
    ) -> list[CaseFactMessageConsumptionRow]:
        statement = (
            select(CaseFactMessageConsumptionRow)
            .where(CaseFactMessageConsumptionRow.case_id == case_id)
            .order_by(
                CaseFactMessageConsumptionRow.recorded_at,
                CaseFactMessageConsumptionRow.consumption_id,
            )
        )
        return list(self.session.scalars(statement))

    def append_case_fact_message_consumption(
        self,
        *,
        consumption_id: str,
        case_id: str,
        question_id: str,
        source_message_id: str,
        source_message_hash: str,
        candidate_batch_hash: str,
        outcome: str,
        reason_code: str,
        assertion_id: str | None,
        decision_payload: dict[str, Any],
        recorded_at: datetime,
    ) -> CaseFactMessageConsumptionRow:
        existing = self.get_case_fact_consumption_for_question(question_id)
        if existing is not None:
            if (
                existing.case_id == case_id
                and existing.source_message_id == source_message_id
                and existing.source_message_hash == source_message_hash
                and existing.candidate_batch_hash == candidate_batch_hash
            ):
                return existing
            raise IdempotencyConflictError("outstanding Case Fact question was already consumed")
        existing = self.get_case_fact_consumption_for_message(source_message_id)
        if existing is not None:
            raise IdempotencyConflictError(
                "customer message was already consumed by a Case Fact question"
            )
        self.require_case(case_id)
        row = CaseFactMessageConsumptionRow(
            consumption_id=consumption_id,
            case_id=case_id,
            question_id=question_id,
            source_message_id=source_message_id,
            source_message_hash=source_message_hash,
            candidate_batch_hash=candidate_batch_hash,
            outcome=outcome,
            reason_code=reason_code,
            assertion_id=assertion_id,
            decision_payload=_jsonable(decision_payload),
            recorded_at=recorded_at,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_case_fact_snapshot(self, case_id: str) -> CaseFactSnapshotRow | None:
        return self.session.get(CaseFactSnapshotRow, case_id)

    def store_case_fact_snapshot(self, snapshot: Any) -> CaseFactSnapshotRow:
        row = self.get_case_fact_snapshot(snapshot.case_id)
        payload = _jsonable(snapshot)
        if row is None:
            row = CaseFactSnapshotRow(
                case_id=snapshot.case_id,
                revision=snapshot.revision,
                snapshot_hash=snapshot.snapshot_hash,
                snapshot_payload=payload,
                rebuilt_at=snapshot.rebuilt_at,
            )
            self.session.add(row)
        else:
            if snapshot.revision < row.revision:
                raise ConcurrentMutationError("Case Fact snapshot revision cannot move backwards")
            row.revision = snapshot.revision
            row.snapshot_hash = snapshot.snapshot_hash
            row.snapshot_payload = payload
            row.rebuilt_at = snapshot.rebuilt_at
        self.session.flush()
        return row

    # Proposals, executions, and tickets -----------------------------------------
    def create_proposal(self, proposal: ActionProposal) -> ActionProposalRow:
        case = self.require_case(proposal.case_id)
        row = ActionProposalRow(
            proposal_id=proposal.proposal_id,
            conversation_id=case.conversation_id,
            case_id=proposal.case_id,
            version=proposal.version,
            proposal_state=str(_value(proposal.proposal_state)),
            action_type=str(_value(proposal.action_type)),
            execution_parameters=_jsonable(proposal.execution_parameters),
            customer_visible_effect=proposal.customer_visible_effect,
            evidence_refs=_jsonable(proposal.evidence_refs),
            evidence_snapshot_hash=proposal.evidence_snapshot_hash,
            case_fact_identity=_jsonable(proposal.case_fact_identity),
            created_at=proposal.created_at,
            expires_at=proposal.expires_at,
            state_changed_at=proposal.created_at,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_proposal(self, proposal_id: str) -> ActionProposalRow | None:
        return self.session.get(ActionProposalRow, proposal_id)

    def require_proposal(self, proposal_id: str) -> ActionProposalRow:
        return _require(self.get_proposal(proposal_id), "ActionProposal", proposal_id)

    def list_proposals(self, case_id: str) -> list[ActionProposalRow]:
        statement = (
            select(ActionProposalRow)
            .where(ActionProposalRow.case_id == case_id)
            .order_by(ActionProposalRow.version)
        )
        return list(self.session.scalars(statement))

    def update_proposal_state(
        self,
        proposal_id: str,
        proposal_state: str,
        *,
        superseded_by_proposal_id: str | None = None,
        changed_at: datetime | None = None,
    ) -> ActionProposalRow:
        row = self.require_proposal(proposal_id)
        next_state = str(_value(proposal_state))
        allowed = {
            "pending_confirmation": {
                "confirmed",
                "declined",
                "superseded",
                "expired",
                "invalidated",
            },
            "confirmed": set(),
            "declined": set(),
            "superseded": set(),
            "expired": set(),
            "invalidated": set(),
        }
        if next_state != row.proposal_state and next_state not in allowed[row.proposal_state]:
            raise InvalidStateTransitionError(
                f"Proposal cannot transition from {row.proposal_state} to {next_state}"
            )
        if next_state == "superseded" and not superseded_by_proposal_id:
            raise InvalidStateTransitionError("a superseded Proposal must point to its replacement")
        row.proposal_state = next_state
        row.superseded_by_proposal_id = superseded_by_proposal_id
        row.state_changed_at = changed_at or utc_now()
        case = self.require_case(row.case_id)
        if next_state != "pending_confirmation" and case.active_proposal_id == proposal_id:
            case.active_proposal_id = None
            case.revision += 1
            case.updated_at = row.state_changed_at
        self.session.flush()
        return row

    def create_action(self, action: ActionExecution) -> ActionExecutionRow:
        proposal = self.require_proposal(action.proposal_id)
        row = ActionExecutionRow(
            action_id=action.action_id,
            proposal_id=action.proposal_id,
            conversation_id=proposal.conversation_id,
            case_id=proposal.case_id,
            action_state=str(_value(action.action_state)),
            idempotency_key=action.idempotency_key,
            error_code=action.error_code,
            submitted_at=action.submitted_at,
            verified_at=action.verified_at,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_action(self, action_id: str) -> ActionExecutionRow | None:
        return self.session.get(ActionExecutionRow, action_id)

    def get_action_by_idempotency_key(self, idempotency_key: str) -> ActionExecutionRow | None:
        statement = select(ActionExecutionRow).where(
            ActionExecutionRow.idempotency_key == idempotency_key
        )
        return self.session.scalar(statement)

    def list_actions(self, case_id: str) -> list[ActionExecutionRow]:
        statement = (
            select(ActionExecutionRow)
            .where(ActionExecutionRow.case_id == case_id)
            .order_by(ActionExecutionRow.created_at, ActionExecutionRow.action_id)
        )
        return list(self.session.scalars(statement))

    def update_action_state(
        self,
        action_id: str,
        action_state: str,
        *,
        error_code: str | None = None,
        submitted_at: datetime | None = None,
        verified_at: datetime | None = None,
        changed_at: datetime | None = None,
    ) -> ActionExecutionRow:
        row = _require(self.get_action(action_id), "ActionExecution", action_id)
        next_state = str(_value(action_state))
        allowed = {
            "ready": {"submitted"},
            "submitted": {"succeeded", "failed_retryable", "failed_terminal", "uncertain"},
            "failed_retryable": {"submitted"},
            "succeeded": set(),
            "failed_terminal": set(),
            "uncertain": set(),
        }
        if next_state != row.action_state and next_state not in allowed[row.action_state]:
            raise InvalidStateTransitionError(
                f"Action cannot transition from {row.action_state} to {next_state}"
            )
        now = changed_at or utc_now()
        if next_state == "ready":
            next_submitted_at = None
            next_verified_at = None
        else:
            next_submitted_at = submitted_at or row.submitted_at or now
            next_verified_at = verified_at or (now if next_state == "succeeded" else None)
        if next_state == "succeeded" and next_verified_at is None:
            raise InvalidStateTransitionError("a succeeded action requires read-back verification")
        if next_state != "succeeded" and verified_at is not None:
            raise InvalidStateTransitionError("verified_at belongs only to a succeeded action")
        row.action_state = next_state
        row.submitted_at = next_submitted_at
        row.verified_at = next_verified_at
        row.error_code = error_code
        row.updated_at = now
        self.session.flush()
        return row

    def create_ticket(
        self,
        *,
        ticket_id: str,
        case_id: str,
        action_id: str,
        customer_id: str,
        authorized_order_id: str,
        issue_type: str,
        idempotency_key: str,
        target_shipment_id: str | None = None,
        details: dict[str, Any] | None = None,
        created_at: datetime | None = None,
    ) -> TicketRow:
        existing = self.get_ticket_by_idempotency_key(idempotency_key)
        requested_identity = (
            case_id,
            action_id,
            customer_id,
            authorized_order_id,
            target_shipment_id,
            str(_value(issue_type)),
        )
        if existing is not None:
            existing_identity = (
                existing.case_id,
                existing.action_id,
                existing.customer_id,
                existing.authorized_order_id,
                existing.target_shipment_id,
                existing.issue_type,
            )
            if existing_identity != requested_identity:
                raise IdempotencyConflictError(
                    "an idempotency key cannot be reused for different ticket parameters"
                )
            return existing
        case = self.require_case(case_id)
        now = created_at or utc_now()
        row = TicketRow(
            ticket_id=ticket_id,
            conversation_id=case.conversation_id,
            case_id=case_id,
            action_id=action_id,
            customer_id=customer_id,
            authorized_order_id=authorized_order_id,
            target_shipment_id=target_shipment_id,
            issue_type=str(_value(issue_type)),
            ticket_state="active",
            idempotency_key=idempotency_key,
            details=_jsonable(details or {}),
            created_at=now,
            updated_at=now,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_ticket(self, ticket_id: str) -> TicketRow | None:
        return self.session.get(TicketRow, ticket_id)

    def get_ticket_by_idempotency_key(self, idempotency_key: str) -> TicketRow | None:
        return self.session.scalar(
            select(TicketRow).where(TicketRow.idempotency_key == idempotency_key)
        )

    def get_active_ticket(
        self,
        authorized_order_id: str,
        issue_type: str,
        *,
        target_shipment_id: str | None = None,
    ) -> TicketRow | None:
        statement = select(TicketRow).where(
            TicketRow.authorized_order_id == authorized_order_id,
            TicketRow.issue_type == str(_value(issue_type)),
            TicketRow.ticket_state == "active",
        )
        if target_shipment_id is not None:
            statement = statement.where(
                (TicketRow.target_shipment_id == target_shipment_id)
                | TicketRow.target_shipment_id.is_(None)
            )
        return self.session.scalar(statement)

    def list_tickets(
        self,
        *,
        case_id: str | None = None,
        authorized_order_id: str | None = None,
    ) -> list[TicketRow]:
        statement = select(TicketRow)
        if case_id is not None:
            statement = statement.where(TicketRow.case_id == case_id)
        if authorized_order_id is not None:
            statement = statement.where(TicketRow.authorized_order_id == authorized_order_id)
        statement = statement.order_by(TicketRow.created_at, TicketRow.ticket_id)
        return list(self.session.scalars(statement))

    # Local demo reset ------------------------------------------------------------
    def reset_demo_data(
        self,
        fixture_seeder: Callable[[Session], None] | None = None,
    ) -> dict[str, int]:
        """Delete dynamic demo aggregates while leaving configuration/evals alone.

        All dynamic rows are descendants of a Conversation. The bulk delete is
        intentional: it is the sole exception to EventRow's ORM append-only
        guard and relies on SQLite foreign-key cascades. A fixture seeder may
        restore mutable synthetic fixture tables owned by another module in the
        same transaction; no source files or eval report tables are touched.
        """

        table_counts = {
            "conversations": self.session.scalar(select(func.count()).select_from(ConversationRow))
            or 0,
            "cases": self.session.scalar(select(func.count()).select_from(InvestigationCaseRow))
            or 0,
            "runs": self.session.scalar(select(func.count()).select_from(RunRow)) or 0,
            "proposals": self.session.scalar(select(func.count()).select_from(ActionProposalRow))
            or 0,
            "actions": self.session.scalar(select(func.count()).select_from(ActionExecutionRow))
            or 0,
            "tickets": self.session.scalar(select(func.count()).select_from(TicketRow)) or 0,
            "events": self.session.scalar(select(func.count()).select_from(EventRow)) or 0,
        }
        self.session.execute(delete(ConversationRow))
        if fixture_seeder is not None:
            fixture_seeder(self.session)
        self.session.flush()
        return table_counts


def case_to_domain(row: InvestigationCaseRow) -> InvestigationCase:
    """Map the SQL read model back to the immutable domain snapshot."""

    from after_sales_agent.domain.models import InvestigationCase

    return InvestigationCase.model_validate(
        {
            "case_id": row.case_id,
            "conversation_id": row.conversation_id,
            "customer_id": row.customer_id,
            "authorized_order_id": row.authorized_order_id,
            "target_shipment_id": row.target_shipment_id,
            "canonical_issue_type": row.canonical_issue_type,
            "case_state": row.case_state,
            "case_outcome": row.case_outcome,
            "reason_code": row.reason_code,
            "related_case_id": row.related_case_id,
            "business_clarifications": row.business_clarification_count,
            "planning_turns": row.agent_planning_turn_count,
            "read_tool_executions": row.actual_read_tool_execution_count,
            "issue_type_revision_history": row.issue_type_revision_history,
        }
    )


def run_to_domain(row: RunRow) -> Run:
    from after_sales_agent.domain.models import Run

    if row.case_id is None:
        raise ValueError("a pre-Case Run has no equivalent in the current domain Run snapshot")
    return Run.model_validate(
        {
            "run_id": row.run_id,
            "case_id": row.case_id,
            "run_state": row.run_state,
            "planning_turns": row.planning_turn_count,
            "failure_class": row.failure_code,
        }
    )


def proposal_to_domain(row: ActionProposalRow) -> ActionProposal:
    from after_sales_agent.domain.models import ActionProposal

    return ActionProposal.model_validate(
        {
            "proposal_id": row.proposal_id,
            "case_id": row.case_id,
            "version": row.version,
            "proposal_state": row.proposal_state,
            "action_type": row.action_type,
            "execution_parameters": row.execution_parameters,
            "customer_visible_effect": row.customer_visible_effect,
            "evidence_refs": row.evidence_refs,
            "evidence_snapshot_hash": row.evidence_snapshot_hash,
            "case_fact_identity": row.case_fact_identity,
            "created_at": row.created_at,
            "expires_at": row.expires_at,
        }
    )


def action_to_domain(row: ActionExecutionRow) -> ActionExecution:
    from after_sales_agent.domain.models import ActionExecution

    return ActionExecution.model_validate(
        {
            "action_id": row.action_id,
            "proposal_id": row.proposal_id,
            "action_state": row.action_state,
            "idempotency_key": row.idempotency_key,
            "submitted_at": row.submitted_at,
            "verified_at": row.verified_at,
            "error_code": row.error_code,
        }
    )

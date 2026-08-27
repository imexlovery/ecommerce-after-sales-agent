from __future__ import annotations

from datetime import UTC, datetime

import pytest

from after_sales_agent.agents.prompts import INVESTIGATION_SYSTEM_PROMPT
from after_sales_agent.application.adaptive_core import (
    BudgetSnapshot,
    CandidateValidationStatus,
    EvidenceProgressReducer,
    EvidenceProgressStatus,
    EvidenceRequirementCode,
    GateReadiness,
    GuardController,
    ObservationRouter,
    ObservationValidator,
    RecoveryReasonCode,
    RecoveryRoute,
    SelectorKind,
    build_decision_context,
    issue_exact_retry,
    validate_exact_retry,
)
from after_sales_agent.domain.models import TrustedToolContext
from after_sales_agent.domain.state import EvidenceAvailability, IssueType
from after_sales_agent.tools.contracts import ToolResult

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def _trusted(issue: IssueType = IssueType.SIGNED_NOT_RECEIVED) -> TrustedToolContext:
    return TrustedToolContext(
        customer_id="customer_a",
        conversation_id="conversation_a",
        case_id="case_a",
        run_id="run_a",
        authorized_order_id="ORD-001",
        canonical_issue_type=issue,
        fixture_version="fixture-v1",
        fault_seed="safe-seed",
        evaluated_at=NOW,
        trace_id="trace_a",
    )


def _result(available: EvidenceAvailability = EvidenceAvailability.ABSENT) -> ToolResult[None]:
    return ToolResult.completed(
        availability=available,
        source_type="fixture",
        source_query_id="query-a",
        observed_at=NOW,
        payload=None,
    )


def _call(name: str, result: ToolResult[None], *, attempt: int = 1) -> dict[str, object]:
    call_id = f"call-{name}-{attempt}"
    return {
        "tool_call_id": call_id,
        "tool_name": name,
        "normalized_args": {"order_id": "ORD-001"},
        "attempt_number": attempt,
        "actual_execution": True,
        "execution_status": result.execution_status.value,
        "evidence_availability": result.evidence_availability.value,
        "result_envelope": result.model_dump(mode="json"),
        "result_hash": result.result_hash,
        "source_version": "fixture-v1",
        "requested_at": f"2026-08-23T12:00:0{attempt}Z",
    }


def test_candidate_rejects_extra_and_untrusted_fields() -> None:
    progress = EvidenceProgressReducer().initial(
        case_id="case_a",
        run_id="run_a",
        canonical_issue_type=IssueType.SIGNED_NOT_RECEIVED,
        rebuilt_at=NOW,
    )
    trusted = _trusted()
    context = build_decision_context(
        trusted=trusted,
        customer_message="fictional message",
        progress=progress,
        budget=BudgetSnapshot(
            case_planning_turns=0,
            run_planning_turns=0,
            actual_read_tool_executions=0,
        ),
    )
    invalid = ObservationValidator().validate(
        {
            "action": "call_tool",
            "tool_name": "get_order_context",
            "arguments": {"order_id": "ORD-001"},
            "addresses": ["ORDER_STATUS"],
            "reason_code": "FIRST_REQUIRED_OBSERVATION",
            "case_id": "attacker-case",
        },
        context=context,
        selector_kind=SelectorKind.AGENT,
        trusted=trusted,
    )
    assert invalid.status is CandidateValidationStatus.REJECTED
    assert invalid.rejection_code == "INVALID_CANDIDATE_SCHEMA"


def test_reducer_accepts_absence_and_hash_is_restart_stable() -> None:
    reducer = EvidenceProgressReducer()
    names = (
        "get_order_context",
        "get_logistics_timeline",
        "get_delivery_proof",
        "search_after_sales_policy",
        "get_existing_logistics_tickets",
    )
    calls = [_call(name, _result()) for name in names]
    refs = []
    for call in calls:
        result = ToolResult[None].model_validate(call["result_envelope"])
        refs.extend(result.to_evidence_refs(str(call["tool_call_id"])))
    first = reducer.rebuild(
        case_id="case_a",
        run_id="run_a",
        canonical_issue_type=IssueType.SIGNED_NOT_RECEIVED,
        tool_calls=calls,
        evidence_refs=refs,
        rebuilt_at=NOW,
    )
    restarted = reducer.rebuild(
        case_id="case_a",
        run_id="run_a",
        canonical_issue_type=IssueType.SIGNED_NOT_RECEIVED,
        tool_calls=calls,
        evidence_refs=refs,
        rebuilt_at=NOW.replace(hour=13),
    )
    assert first.gate_readiness is GateReadiness.EVALUABLE
    assert first.snapshot_hash == restarted.snapshot_hash
    assert all(
        first.requirements[code].status is EvidenceProgressStatus.SATISFIED_ABSENT
        for code in (
            EvidenceRequirementCode.ORDER_STATUS,
            EvidenceRequirementCode.TRACKING_TIMELINE,
            EvidenceRequirementCode.DELIVERY_PROOF,
            EvidenceRequirementCode.POLICY_APPLICABILITY,
            EvidenceRequirementCode.ACTIVE_TICKET_STATUS,
        )
    )


def test_unavailable_never_satisfies_and_exact_retry_is_identity_bound() -> None:
    failed = ToolResult.failed(
        retryable=True,
        source_type="delivery_proof",
        source_query_id="query-failure",
        observed_at=NOW,
        error_code="TIMEOUT",
    )
    directive = issue_exact_retry(
        tool_call_id="call-pod-1",
        tool_name="get_delivery_proof",
        canonical_arguments={"order_id": "ORD-001"},
        source_version="fixture-v1",
        execution_status=failed.execution_status,
        evidence_availability=failed.evidence_availability,
        retryable=failed.retryable,
        attempt_number=1,
        progress_hash="a" * 64,
    )
    assert directive is not None
    validate_exact_retry(
        directive,
        tool_name="get_delivery_proof",
        canonical_arguments={"order_id": "ORD-001"},
        source_version="fixture-v1",
        attempt_number=2,
    )
    with pytest.raises(ValueError):
        validate_exact_retry(
            directive,
            tool_name="get_delivery_proof",
            canonical_arguments={"order_id": "ORD-002"},
            source_version="fixture-v1",
            attempt_number=2,
        )
    call = _call("get_delivery_proof", failed)
    call["tool_call_id"] = directive.retry_of_tool_call_id
    progress = EvidenceProgressReducer().rebuild(
        case_id="case_a",
        run_id="run_a",
        canonical_issue_type=IssueType.SIGNED_NOT_RECEIVED,
        tool_calls=[call],
        pending_retry=directive,
        rebuilt_at=NOW,
    )
    assert (
        progress.requirements[EvidenceRequirementCode.DELIVERY_PROOF].status
        is EvidenceProgressStatus.RETRY_PENDING
    )


def test_router_precedence_and_stuck_guard_are_deterministic() -> None:
    reducer = EvidenceProgressReducer()
    progress = reducer.initial(
        case_id="case_a",
        run_id="run_a",
        canonical_issue_type=IssueType.SIGNED_NOT_RECEIVED,
        rebuilt_at=NOW,
    )
    budget = BudgetSnapshot(
        case_planning_turns=1,
        run_planning_turns=1,
        actual_read_tool_executions=1,
    )
    decision = ObservationRouter().route(
        case_id="case_a",
        run_id="run_a",
        progress_before=progress,
        progress_after=progress,
        budget=budget,
    )
    assert decision.route is RecoveryRoute.REPLAN
    assert decision.reason_code is RecoveryReasonCode.REPLAN_REQUIRED

    guard = GuardController()
    fp = "b" * 64
    assert guard.observe_decision(fp, progress.snapshot_hash) is None
    assert guard.observe_decision(fp, progress.snapshot_hash) is None
    assert (
        guard.observe_decision(fp, progress.snapshot_hash)
        is RecoveryReasonCode.STUCK_REPEATED_DECISION
    )


def test_reducer_fail_closes_scope_and_retry_identity_conflicts() -> None:
    reducer = EvidenceProgressReducer()
    failed = ToolResult.failed(
        retryable=True,
        source_type="delivery_proof",
        source_query_id="query-failure",
        observed_at=NOW,
        error_code="TIMEOUT",
    )
    first = _call("get_delivery_proof", failed)
    directive = issue_exact_retry(
        tool_call_id=str(first["tool_call_id"]),
        tool_name="get_delivery_proof",
        canonical_arguments=first["normalized_args"],
        source_version="fixture-v1",
        execution_status=failed.execution_status,
        evidence_availability=failed.evidence_availability,
        retryable=failed.retryable,
        attempt_number=1,
        progress_hash="c" * 64,
    )
    assert directive is not None
    second = _call("get_delivery_proof", failed, attempt=2)
    second["normalized_args"] = {"order_id": "ORD-002"}
    conflicted = reducer.rebuild(
        case_id="case_a",
        run_id="run_a",
        canonical_issue_type=IssueType.SIGNED_NOT_RECEIVED,
        tool_calls=[first, second],
        pending_retry=directive,
        rebuilt_at=NOW,
    )
    assert "RETRY_IDENTITY_CONFLICT" in conflicted.terminal_trigger_codes
    assert (
        conflicted.requirements[EvidenceRequirementCode.DELIVERY_PROOF].status
        is EvidenceProgressStatus.CONFLICT
    )

    scoped = dict(first)
    scoped["case_id"] = "foreign-case"
    scope_conflict = reducer.rebuild(
        case_id="case_a",
        run_id="run_a",
        canonical_issue_type=IssueType.SIGNED_NOT_RECEIVED,
        tool_calls=[scoped],
        rebuilt_at=NOW,
    )
    assert "TOOL_CALL_SCOPE_MISMATCH" in scope_conflict.terminal_trigger_codes


def test_router_source_change_and_guard_no_progress_safe_stop() -> None:
    reducer = EvidenceProgressReducer()
    progress = reducer.initial(
        case_id="case_a",
        run_id="run_a",
        canonical_issue_type=IssueType.SIGNED_NOT_RECEIVED,
        rebuilt_at=NOW,
    )
    failed = ToolResult.failed(
        retryable=True,
        source_type="delivery_proof",
        source_query_id="query-failure",
        observed_at=NOW,
        error_code="TIMEOUT",
    )
    directive = issue_exact_retry(
        tool_call_id="call-pod-1",
        tool_name="get_delivery_proof",
        canonical_arguments={"order_id": "ORD-001"},
        source_version="fixture-v1",
        execution_status=failed.execution_status,
        evidence_availability=failed.evidence_availability,
        retryable=failed.retryable,
        attempt_number=1,
        progress_hash=progress.snapshot_hash,
    )
    assert directive is not None
    routed = ObservationRouter().route(
        case_id="case_a",
        run_id="run_a",
        progress_before=progress,
        progress_after=progress,
        budget=BudgetSnapshot(
            case_planning_turns=1,
            run_planning_turns=1,
            actual_read_tool_executions=1,
        ),
        pending_retry=directive,
        source_version="fixture-v2",
    )
    assert routed.route is RecoveryRoute.SAFE_STOP
    assert routed.reason_code is RecoveryReasonCode.SOURCE_REVISION_CHANGED_DURING_RETRY

    guard = GuardController()
    progress_hash = "d" * 64
    assert guard.observe_selector_turn(progress_hash, progress_hash) is None
    assert (
        guard.observe_selector_turn(progress_hash, progress_hash)
        is RecoveryReasonCode.STUCK_NO_EVIDENCE_PROGRESS
    )


def test_exhausted_unavailable_read_cannot_be_selected_again() -> None:
    reducer = EvidenceProgressReducer()
    failed = ToolResult.failed(
        retryable=True,
        source_type="delivery_proof",
        source_query_id="query-failure",
        observed_at=NOW,
        error_code="TIMEOUT",
    )
    calls = [_call("get_order_context", _result())]
    calls.extend(
        [_call("get_delivery_proof", failed), _call("get_delivery_proof", failed, attempt=2)]
    )
    calls[-1]["tool_call_id"] = "call-get_delivery_proof-2"
    progress = reducer.rebuild(
        case_id="case_a",
        run_id="run_a",
        canonical_issue_type=IssueType.SIGNED_NOT_RECEIVED,
        tool_calls=calls,
        rebuilt_at=NOW,
    )
    context = build_decision_context(
        trusted=_trusted(),
        customer_message="fictional message",
        progress=progress,
        budget=BudgetSnapshot(
            case_planning_turns=2,
            run_planning_turns=2,
            actual_read_tool_executions=3,
        ),
    )
    rejected = ObservationValidator().validate(
        {
            "action": "call_tool",
            "tool_name": "get_delivery_proof",
            "arguments": {"order_id": "ORD-001"},
            "addresses": ["DELIVERY_PROOF"],
            "reason_code": "MISSING_REQUIRED_EVIDENCE",
        },
        context=context,
        selector_kind=SelectorKind.AGENT,
        trusted=_trusted(),
    )
    assert rejected.rejection_code == "RETRY_EXHAUSTED"


def test_investigation_prompt_is_goal_and_constraints_not_fixed_recipe() -> None:
    assert "evidence requirements" in INVESTIGATION_SYSTEM_PROMPT.lower()
    assert "tool constraints" in INVESTIGATION_SYSTEM_PROMPT.lower()
    assert "Request only get_order_context first" not in INVESTIGATION_SYSTEM_PROMPT
    assert "retry that exact read immediately once" not in INVESTIGATION_SYSTEM_PROMPT

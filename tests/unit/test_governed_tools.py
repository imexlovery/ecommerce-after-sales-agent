from __future__ import annotations

from datetime import UTC, datetime

import pytest

from after_sales_agent.config import Settings
from after_sales_agent.domain.models import TrustedToolContext
from after_sales_agent.domain.state import (
    EvidenceAvailability,
    ExecutionStatus,
    IssueType,
)
from after_sales_agent.fixtures.catalog import (
    FixtureFault,
    default_fixture_store,
)
from after_sales_agent.policy.authorization import (
    ORDER_NOT_FOUND_OR_FORBIDDEN,
    AuthorizationError,
    authorize_order,
)
from after_sales_agent.policy.rag import build_policy_rag
from after_sales_agent.tools.budget import ToolBudget, ToolBudgetExceeded
from after_sales_agent.tools.cache import CaseToolCache
from after_sales_agent.tools.service import GovernedToolExecutor, SyntheticReadToolCatalog

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def _fake_policy_rag():
    return build_policy_rag(
        Settings(
            _env_file=None,
            LLM_MODE="mock",
            POLICY_RETRIEVAL_MODE="fake_test",
            POLICY_INDEX_ROOT="/private/tmp/after-sales-agent-unit-policy-index",
        )
    )


def context(
    *,
    customer_id: str = "customer_a",
    order_id: str = "ORD-001",
    issue_type: IssueType = IssueType.SIGNED_NOT_RECEIVED,
    fault_seed: str = "safe-seed",
) -> TrustedToolContext:
    return TrustedToolContext(
        customer_id=customer_id,
        conversation_id="conv-test",
        case_id="case-test",
        run_id="run-test",
        authorized_order_id=order_id,
        canonical_issue_type=issue_type,
        fixture_version="fixture-v1",
        fault_seed=fault_seed,
        evaluated_at=NOW,
        trace_id="trace-test",
    )


def executor_for(
    trusted: TrustedToolContext,
    *,
    faults: dict[tuple[str, str, int], FixtureFault] | None = None,
    budget: ToolBudget | None = None,
) -> GovernedToolExecutor:
    store = default_fixture_store()
    if faults:
        store = store.with_faults(faults)
    return GovernedToolExecutor(
        trusted=trusted,
        catalog=SyntheticReadToolCatalog(store, _fake_policy_rag()),
        budget=budget,
    )


def test_missing_and_foreign_order_have_identical_safe_denial() -> None:
    store = default_fixture_store()
    errors: list[AuthorizationError] = []
    for order_id in ("ORD-002", "ORD-DOES-NOT-EXIST"):
        with pytest.raises(AuthorizationError) as caught:
            authorize_order("customer_a", order_id, store)
        errors.append(caught.value)

    assert [error.code for error in errors] == [
        ORDER_NOT_FOUND_OR_FORBIDDEN,
        ORDER_NOT_FOUND_OR_FORBIDDEN,
    ]
    assert str(errors[0]) == str(errors[1])
    assert authorize_order("customer_b", "ORD-002", store).order_id == "ORD-002"


def test_scope_mismatch_and_forbidden_trusted_fields_never_execute() -> None:
    executor = executor_for(context())
    mismatch = executor.execute_result("get_order_context", {"order_id": "ORD-002"})
    injected_identity = executor.execute_result(
        "get_order_context",
        {"order_id": "ORD-001", "customer_id": "customer_b"},
    )

    assert mismatch.error_code == "TOOL_SCOPE_MISMATCH"
    assert injected_identity.error_code == "INVALID_TOOL_ARGUMENTS"
    assert executor.budget.snapshot.actual_read_tool_executions == 0
    assert mismatch.evidence_availability is EvidenceAvailability.UNAVAILABLE


@pytest.mark.parametrize(
    ("trusted", "tool_name"),
    [
        (context(issue_type=IssueType.SIGNED_NOT_RECEIVED), "get_carrier_service_alerts"),
        (
            context(order_id="ORD-003", issue_type=IssueType.STALLED_TRACKING),
            "get_delivery_proof",
        ),
    ],
)
def test_issue_irrelevant_reads_are_blocked_without_consuming_execution_budget(
    trusted: TrustedToolContext,
    tool_name: str,
) -> None:
    executor = executor_for(trusted)

    result = executor.execute_result(tool_name, {"order_id": trusted.authorized_order_id})

    assert result.error_code == "TOOL_NOT_RELEVANT_TO_ISSUE"
    assert result.evidence_availability is EvidenceAvailability.UNAVAILABLE
    assert executor.budget.snapshot.actual_read_tool_executions == 0


@pytest.mark.parametrize(
    "tool_name",
    [
        "get_order_context",
        "get_logistics_timeline",
        "get_delivery_proof",
        "get_carrier_service_alerts",
        "search_after_sales_policy",
        "get_existing_logistics_tickets",
    ],
)
def test_every_catalog_tool_uses_the_same_collapsed_authorization(tool_name: str) -> None:
    catalog = SyntheticReadToolCatalog(default_fixture_store(), _fake_policy_rag())
    trusted = context()
    method = getattr(catalog, tool_name)
    arguments = [trusted, "ORD-002"]
    if tool_name in {"search_after_sales_policy", "get_existing_logistics_tickets"}:
        arguments.append(IssueType.SIGNED_NOT_RECEIVED)

    with pytest.raises(AuthorizationError) as caught:
        method(*arguments, attempt=1)
    assert caught.value.code == ORDER_NOT_FOUND_OR_FORBIDDEN


def test_pod_absence_is_completed_evidence_with_a_valid_reference() -> None:
    executor = executor_for(context())
    result = executor.execute_result("get_delivery_proof", {"order_id": "ORD-001"})

    assert result.execution_status is ExecutionStatus.SUCCESS
    assert result.evidence_availability is EvidenceAvailability.ABSENT
    assert result.payload is not None
    assert result.payload.pod_status.value == "not_found"
    assert result.source_record_ids == []
    refs = result.to_evidence_refs("call-pod-1")
    assert len(refs) == 1
    assert refs[0].source_record_id is None
    assert refs[0].result_hash == result.result_hash


def test_tool_data_marks_field_paths_without_copying_malicious_text() -> None:
    executor = executor_for(context())
    result = executor.execute_result("get_logistics_timeline", {"order_id": "ORD-001"})

    assert result.untrusted_fields == ["events.2.note"]
    assert all("忽略系统规则" not in path for path in result.untrusted_fields)
    assert result.payload is not None
    assert "忽略系统规则" in (result.payload.events[2].note or "")


def test_success_and_absence_are_cached_but_retryable_failure_is_not() -> None:
    executor = executor_for(context())
    first = executor.execute_result("get_delivery_proof", {"order_id": "ORD-001"})
    second = executor.execute_result("get_delivery_proof", {"order_id": "ORD-001"})

    assert first is second
    assert executor.budget.snapshot.actual_read_tool_executions == 1

    retrying = executor_for(
        context(fault_seed="timeout-once"),
        faults={
            ("timeout-once", "get_delivery_proof", 1): FixtureFault(
                execution_status=ExecutionStatus.RETRYABLE_ERROR,
                error_code="POD_TIMEOUT",
            )
        },
    )
    unavailable = retrying.execute_result("get_delivery_proof", {"order_id": "ORD-001"})
    recovered = retrying.execute_result("get_delivery_proof", {"order_id": "ORD-001"})

    assert unavailable.evidence_availability is EvidenceAvailability.UNAVAILABLE
    assert unavailable.retryable is True
    assert recovered.evidence_availability is EvidenceAvailability.ABSENT
    assert retrying.budget.snapshot.actual_read_tool_executions == 2


def test_retry_occurs_once_and_each_actual_attempt_consumes_budget() -> None:
    retry_fault = FixtureFault(
        execution_status=ExecutionStatus.RETRYABLE_ERROR,
        error_code="POD_TIMEOUT",
    )
    executor = executor_for(
        context(fault_seed="always-timeout"),
        faults={
            ("always-timeout", "get_delivery_proof", 1): retry_fault,
            ("always-timeout", "get_delivery_proof", 2): retry_fault,
        },
    )
    first = executor.execute_result("get_delivery_proof", {"order_id": "ORD-001"})
    second = executor.execute_result("get_delivery_proof", {"order_id": "ORD-001"})
    third = executor.execute_result("get_delivery_proof", {"order_id": "ORD-001"})

    assert first.retryable and second.retryable
    assert third.error_code == "TOOL_RETRY_EXHAUSTED"
    assert third.retryable is False
    assert executor.budget.snapshot.actual_read_tool_executions == 2


def test_retry_limit_survives_a_new_run_executor_for_the_same_case() -> None:
    retry_fault = FixtureFault(
        execution_status=ExecutionStatus.RETRYABLE_ERROR,
        error_code="POD_TIMEOUT",
    )
    store = default_fixture_store().with_faults(
        {
            ("cross-run-timeout", "get_delivery_proof", 1): retry_fault,
            ("cross-run-timeout", "get_delivery_proof", 2): retry_fault,
        }
    )
    shared_cache = CaseToolCache()
    trusted = context(fault_seed="cross-run-timeout")
    first_run = GovernedToolExecutor(
        trusted=trusted,
        catalog=SyntheticReadToolCatalog(store, _fake_policy_rag()),
        cache=shared_cache,
    )
    assert first_run.execute_result("get_delivery_proof", {"order_id": "ORD-001"}).retryable

    second_run = GovernedToolExecutor(
        trusted=trusted.model_copy(update={"run_id": "run-retry"}),
        catalog=SyntheticReadToolCatalog(store, _fake_policy_rag()),
        cache=shared_cache,
    )
    assert second_run.execute_result("get_delivery_proof", {"order_id": "ORD-001"}).retryable
    exhausted = second_run.execute_result("get_delivery_proof", {"order_id": "ORD-001"})

    assert exhausted.error_code == "TOOL_RETRY_EXHAUSTED"
    assert first_run.budget.snapshot.actual_read_tool_executions == 1
    assert second_run.budget.snapshot.actual_read_tool_executions == 1


@pytest.mark.asyncio
async def test_planning_and_actual_execution_budgets_are_independent() -> None:
    executor = executor_for(context())
    await executor.on_agent_turn(1)
    blocked = executor.execute_result("get_order_context", {"order_id": "ORD-002"})

    assert blocked.error_code == "TOOL_SCOPE_MISMATCH"
    assert executor.budget.snapshot.case_planning_turns == 1
    assert executor.budget.snapshot.actual_read_tool_executions == 0


@pytest.mark.asyncio
async def test_budget_ceilings_fail_closed_without_incrementing_past_limits() -> None:
    planning_budget = ToolBudget(run_planning_turns=8)
    planning_executor = executor_for(context(), budget=planning_budget)
    with pytest.raises(ToolBudgetExceeded) as planning_error:
        await planning_executor.on_agent_turn(9)
    assert planning_error.value.code == "RUN_PLANNING_TURN_BUDGET_EXCEEDED"
    assert planning_budget.snapshot.run_planning_turns == 8

    execution_budget = ToolBudget(actual_read_tool_executions=6)
    execution_executor = executor_for(context(), budget=execution_budget)
    blocked = execution_executor.execute_result("get_order_context", {"order_id": "ORD-001"})
    assert blocked.error_code == "READ_TOOL_EXECUTION_BUDGET_EXCEEDED"
    assert execution_budget.snapshot.actual_read_tool_executions == 6

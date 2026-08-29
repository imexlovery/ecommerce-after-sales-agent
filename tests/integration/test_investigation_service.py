from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from after_sales_agent.application.investigation import InvestigationOutput, InvestigationService
from after_sales_agent.application.provider_budget import SelectorSchemaFailure
from after_sales_agent.application.strong_workflow import StrongWorkflowInvestigationService
from after_sales_agent.config import Settings
from after_sales_agent.domain.models import InvestigationCase, Run, TrustedToolContext
from after_sales_agent.domain.state import EvidenceGateDecision, IssueType
from after_sales_agent.events.store import EventStore
from after_sales_agent.fixtures.catalog import (
    FixtureFault,
    FixtureStore,
)
from after_sales_agent.fixtures.catalog import (
    legacy_fixture_store as default_fixture_store,
)
from after_sales_agent.policy.rag import build_policy_rag
from after_sales_agent.storage.database import create_engine_and_session, init_database
from after_sales_agent.storage.models import ConversationRow
from after_sales_agent.storage.repositories import Repository
from after_sales_agent.tools.contracts import LogisticsTicket, ToolResult


class _UntrustedWrongOrderSelectorModel:
    async def ainvoke(self, _: object) -> AIMessage:
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "provider-owned-id",
                    "name": "get_order_context",
                    "args": {"order_id": "ORD-999"},
                    "type": "tool_call",
                }
            ],
        )


@pytest.mark.asyncio
async def test_signed_not_received_runs_through_graph_tools_events_and_gate(tmp_path: Path) -> None:
    database = create_engine_and_session("sqlite:///:memory:")
    init_database(database.engine)
    fixtures = default_fixture_store()
    events = EventStore(database.session_factory)

    with database.session_factory() as session, session.begin():
        repository = Repository(session)
        repository.create_conversation("customer_a", "customer_a", "mock")
        conversation = session.query(ConversationRow).one()
        case = InvestigationCase(
            case_id="case_test",
            conversation_id=conversation.conversation_id,
            customer_id="customer_a",
            authorized_order_id="ORD-001",
            canonical_issue_type=IssueType.SIGNED_NOT_RECEIVED,
        )
        repository.create_case(case)
        repository.create_run(
            Run(run_id="run_test", case_id=case.case_id),
            conversation_id=conversation.conversation_id,
            run_kind="message",
        )
        repository.update_run("run_test", run_state="running")

    settings = Settings(
        _env_file=None,
        LLM_MODE="mock",
        POLICY_RETRIEVAL_MODE="fake_test",
        POLICY_INDEX_ROOT=tmp_path / "policy-index",
    )
    service = InvestigationService(
        settings=settings,
        fixtures=fixtures,
        session_factory=database.session_factory,
        events=events,
        policy_rag=build_policy_rag(settings),
    )
    trusted = TrustedToolContext(
        customer_id="customer_a",
        conversation_id=conversation.conversation_id,
        case_id="case_test",
        run_id="run_test",
        authorized_order_id="ORD-001",
        canonical_issue_type=IssueType.SIGNED_NOT_RECEIVED,
        fixture_version="fixture-v1",
        fault_seed="default",
        evaluated_at="2026-08-23T12:00:00Z",
        trace_id="trace_test",
    )
    result = await service.investigate(
        trusted=trusted,
        customer_message="ORD-001 显示签收，但我没有收到。",
    )

    assert result.gate_result.decision is EvidenceGateDecision.PROPOSE_TICKET
    assert result.actual_read_tool_executions == 5
    assert result.planning_turns == 5
    assert {ref.source_query_id for ref in result.evidence_refs}
    with database.session_factory() as session:
        repository = Repository(session)
        assert len(repository.list_tool_calls(run_id="run_test")) == 5
    event_types = [event.event_type for event in events.list_after(conversation.conversation_id)]
    assert event_types.count("tool_call_requested") == 5
    assert event_types.count("tool_call_completed") == 5
    assert event_types[-1] == "evidence_gate_evaluated"

    # A fresh service instance has no in-memory progress or model state.  The
    # durable ToolCall/EvidenceRef history must rebuild the same gate-ready
    # snapshot and stop before either selector or ToolNode executes again.
    restarted_service = InvestigationService(
        settings=settings,
        fixtures=fixtures,
        session_factory=database.session_factory,
        events=events,
        policy_rag=build_policy_rag(settings),
    )
    restarted = await restarted_service.investigate(
        trusted=trusted,
        customer_message="restart replay",
    )
    assert restarted.gate_result == result.gate_result
    assert restarted.run_read_tool_executions == 0
    with database.session_factory() as session:
        assert len(Repository(session).list_tool_calls(run_id="run_test")) == 5
    replayed_event_types = [
        event.event_type for event in events.list_after(conversation.conversation_id)
    ]
    assert replayed_event_types.count("tool_call_requested") == 5
    assert replayed_event_types.count("agent_turn_started") == 5
    assert replayed_event_types.count("evidence_gate_evaluated") == 1


async def _run_workflow(
    *,
    customer_id: str,
    order_id: str,
    issue_type: IssueType,
    policy_index_root: Path,
    fixtures: FixtureStore | None = None,
    enforce_early_stop: bool = True,
) -> tuple[InvestigationOutput, list[str], list[str]]:
    store = fixtures or default_fixture_store()
    database = create_engine_and_session("sqlite:///:memory:")
    init_database(database.engine)
    events = EventStore(database.session_factory)
    with database.session_factory() as session, session.begin():
        repository = Repository(session)
        conversation = repository.create_conversation(customer_id, customer_id, "mock")
        case = InvestigationCase(
            case_id="case_workflow",
            conversation_id=conversation.conversation_id,
            customer_id=customer_id,
            authorized_order_id=order_id,
            canonical_issue_type=issue_type,
        )
        repository.create_case(case)
        repository.create_run(
            Run(run_id="run_workflow", case_id=case.case_id),
            conversation_id=conversation.conversation_id,
            run_kind="message",
        )
        repository.update_run("run_workflow", run_state="running")

    settings = Settings(
        _env_file=None,
        LLM_MODE="mock",
        POLICY_RETRIEVAL_MODE="fake_test",
        POLICY_INDEX_ROOT=policy_index_root,
    )
    service = StrongWorkflowInvestigationService(
        settings=settings,
        fixtures=store,
        session_factory=database.session_factory,
        events=events,
        policy_rag=build_policy_rag(settings),
    )
    result = await service.investigate(
        trusted=TrustedToolContext(
            customer_id=customer_id,
            conversation_id=conversation.conversation_id,
            case_id="case_workflow",
            run_id="run_workflow",
            authorized_order_id=order_id,
            canonical_issue_type=issue_type,
            fixture_version="fixture-v1",
            fault_seed="default",
            evaluated_at="2026-08-23T12:00:00Z",
            trace_id="trace_workflow",
        ),
        customer_message="normalized evaluation case",
        enforce_early_stop=enforce_early_stop,
    )
    with database.session_factory() as session:
        tool_names = [
            row.tool_name for row in Repository(session).list_tool_calls(run_id="run_workflow")
        ]
    event_types = [event.event_type for event in events.list_after(conversation.conversation_id)]
    database.engine.dispose()
    return result, tool_names, event_types


@pytest.mark.asyncio
async def test_strong_workflow_uses_same_gate_and_governed_tools(tmp_path: Path) -> None:
    result, tool_names, event_types = await _run_workflow(
        customer_id="customer_a",
        order_id="ORD-001",
        issue_type=IssueType.SIGNED_NOT_RECEIVED,
        policy_index_root=tmp_path / "policy-index",
    )

    assert result.gate_result.decision is EvidenceGateDecision.PROPOSE_TICKET
    assert result.strategy == "workflow"
    assert result.actual_read_tool_executions == 5
    assert tool_names == [
        "get_order_context",
        "get_existing_logistics_tickets",
        "get_logistics_timeline",
        "search_after_sales_policy",
        "get_delivery_proof",
    ]
    assert event_types.count("workflow_step_started") == 5
    assert event_types[-1] == "evidence_gate_evaluated"


@pytest.mark.asyncio
async def test_strong_workflow_stops_before_optional_reads_within_sla(tmp_path: Path) -> None:
    result, tool_names, _ = await _run_workflow(
        customer_id="customer_b",
        order_id="ORD-002",
        issue_type=IssueType.STALLED_TRACKING,
        policy_index_root=tmp_path / "policy-index",
    )

    assert result.gate_result.decision is EvidenceGateDecision.COMPLETE_NO_ACTION
    assert result.gate_result.reason_code == "WITHIN_TRACKING_SLA"
    assert tool_names == [
        "get_order_context",
        "get_logistics_timeline",
        "search_after_sales_policy",
    ]


@pytest.mark.asyncio
async def test_strong_workflow_stops_after_existing_ticket_without_duplicate_reads(
    tmp_path: Path,
) -> None:
    fixtures = default_fixture_store()
    fixtures.add_ticket(
        LogisticsTicket(
            ticket_id="ticket-existing",
            order_id="ORD-001",
            issue_type=IssueType.SIGNED_NOT_RECEIVED,
            ticket_status="open",
            created_at=datetime(2026, 8, 23, 10, 0, tzinfo=UTC),
        )
    )
    result, tool_names, _ = await _run_workflow(
        customer_id="customer_a",
        order_id="ORD-001",
        issue_type=IssueType.SIGNED_NOT_RECEIVED,
        policy_index_root=tmp_path / "policy-index",
        fixtures=fixtures,
    )

    assert result.gate_result.decision is EvidenceGateDecision.COMPLETE_NO_ACTION
    assert result.gate_result.reason_code == "ACTIVE_LOGISTICS_TICKET_EXISTS"
    assert tool_names == ["get_order_context", "get_existing_logistics_tickets"]


@pytest.mark.asyncio
async def test_v3a_exact_retry_is_adjacent_and_shares_the_governed_budget(
    tmp_path: Path,
) -> None:
    fixtures = default_fixture_store().with_faults(
        {
            ("default", "get_delivery_proof", 1): FixtureFault(
                execution_status="retryable_error",
                error_code="SYNTHETIC_POD_TIMEOUT",
            )
        }
    )
    result, tool_names, event_types = await _run_workflow(
        customer_id="customer_a",
        order_id="ORD-001",
        issue_type=IssueType.SIGNED_NOT_RECEIVED,
        policy_index_root=tmp_path / "policy-index",
        fixtures=fixtures,
    )

    assert result.gate_result.decision is EvidenceGateDecision.PROPOSE_TICKET
    assert result.actual_read_tool_executions == 6
    assert tool_names[-2:] == ["get_delivery_proof", "get_delivery_proof"]
    assert event_types.count("tool_call_requested") == 6
    assert event_types.count("recovery_trace_record") == 6


@pytest.mark.asyncio
async def test_v3a_early_stop_blocks_selector_after_gate_ready(tmp_path: Path) -> None:
    result, tool_names, event_types = await _run_workflow(
        customer_id="customer_a",
        order_id="ORD-001",
        issue_type=IssueType.SIGNED_NOT_RECEIVED,
        policy_index_root=tmp_path / "policy-index",
        enforce_early_stop=True,
    )

    assert result.gate_result.decision is EvidenceGateDecision.PROPOSE_TICKET
    assert result.planning_turns == 5
    assert len(tool_names) == 5
    assert event_types.count("tool_call_requested") == 5


@pytest.mark.asyncio
async def test_v3a_restart_resumes_persisted_retry_before_selector(tmp_path: Path) -> None:
    database = create_engine_and_session("sqlite:///:memory:")
    init_database(database.engine)
    fixtures = default_fixture_store().with_faults(
        {
            ("default", "get_delivery_proof", 1): FixtureFault(
                execution_status="retryable_error",
                error_code="SYNTHETIC_POD_TIMEOUT",
            )
        }
    )
    events = EventStore(database.session_factory)
    failed = ToolResult.failed(
        retryable=True,
        source_type="get_delivery_proof",
        source_query_id="persisted-attempt-1",
        observed_at=datetime(2026, 8, 23, 12, 0, tzinfo=UTC),
        error_code="SYNTHETIC_POD_TIMEOUT",
    )
    with database.session_factory() as session, session.begin():
        repository = Repository(session)
        conversation = repository.create_conversation("customer_a", "customer_a", "mock")
        repository.create_case(
            InvestigationCase(
                case_id="case_restart_retry",
                conversation_id=conversation.conversation_id,
                customer_id="customer_a",
                authorized_order_id="ORD-001",
                canonical_issue_type=IssueType.SIGNED_NOT_RECEIVED,
            )
        )
        repository.create_run(
            Run(run_id="run_restart_retry", case_id="case_restart_retry"),
            conversation_id=conversation.conversation_id,
            run_kind="message",
        )
        repository.update_run("run_restart_retry", run_state="running")
        repository.create_tool_call(
            conversation_id=conversation.conversation_id,
            case_id="case_restart_retry",
            run_id="run_restart_retry",
            tool_name="get_delivery_proof",
            normalized_args={"order_id": "ORD-001"},
            planning_turn=1,
            tool_call_id="persisted_pod_attempt_1",
            attempt_number=1,
            actual_execution=True,
        )
        repository.complete_tool_call(
            "persisted_pod_attempt_1",
            execution_status=failed.execution_status,
            evidence_availability=failed.evidence_availability,
            result_envelope=failed.model_dump(mode="json"),
            result_hash=failed.result_hash,
            source_version=fixtures.source_revision("ORD-001", "get_delivery_proof"),
            error_code=failed.error_code,
            retryable=True,
        )

    settings = Settings(
        _env_file=None,
        LLM_MODE="mock",
        POLICY_RETRIEVAL_MODE="fake_test",
        POLICY_INDEX_ROOT=tmp_path / "policy-index",
    )
    result = await InvestigationService(
        settings=settings,
        fixtures=fixtures,
        session_factory=database.session_factory,
        events=events,
        policy_rag=build_policy_rag(settings),
    ).investigate(
        trusted=TrustedToolContext(
            customer_id="customer_a",
            conversation_id=conversation.conversation_id,
            case_id="case_restart_retry",
            run_id="run_restart_retry",
            authorized_order_id="ORD-001",
            canonical_issue_type=IssueType.SIGNED_NOT_RECEIVED,
            fixture_version="fixture-v1",
            fault_seed="default",
            evaluated_at="2026-08-23T12:00:00Z",
            trace_id="trace_restart_retry",
        ),
        customer_message="restart",
    )
    assert result.gate_result.decision is EvidenceGateDecision.PROPOSE_TICKET
    with database.session_factory() as session:
        pod_calls = [
            row
            for row in Repository(session).list_tool_calls(run_id="run_restart_retry")
            if row.tool_name == "get_delivery_proof"
        ]
    assert [row.attempt_number for row in pod_calls] == [1, 2]
    emitted = events.list_after(conversation.conversation_id)
    first_tool = next(
        index
        for index, event in enumerate(emitted)
        if event.event_type == "tool_call_requested"
    )
    assert not any(event.event_type == "agent_turn_started" for event in emitted[:first_tool])


@pytest.mark.asyncio
async def test_v3a_untrusted_model_tool_call_never_reaches_toolnode(tmp_path: Path) -> None:
    database = create_engine_and_session("sqlite:///:memory:")
    init_database(database.engine)
    events = EventStore(database.session_factory)
    with database.session_factory() as session, session.begin():
        repository = Repository(session)
        conversation = repository.create_conversation("customer_a", "customer_a", "mock")
        repository.create_case(
            InvestigationCase(
                case_id="case_untrusted_candidate",
                conversation_id=conversation.conversation_id,
                customer_id="customer_a",
                authorized_order_id="ORD-001",
                canonical_issue_type=IssueType.SIGNED_NOT_RECEIVED,
            )
        )
        repository.create_run(
            Run(run_id="run_untrusted_candidate", case_id="case_untrusted_candidate"),
            conversation_id=conversation.conversation_id,
            run_kind="message",
        )
        repository.update_run("run_untrusted_candidate", run_state="running")
    settings = Settings(
        _env_file=None,
        LLM_MODE="mock",
        POLICY_RETRIEVAL_MODE="fake_test",
        POLICY_INDEX_ROOT=tmp_path / "policy-index",
    )
    with pytest.raises(SelectorSchemaFailure) as exc_info:
        await InvestigationService(
            settings=settings,
            fixtures=default_fixture_store(),
            session_factory=database.session_factory,
            events=events,
            policy_rag=build_policy_rag(settings),
        ).investigate(
            trusted=TrustedToolContext(
                customer_id="customer_a",
                conversation_id=conversation.conversation_id,
                case_id="case_untrusted_candidate",
                run_id="run_untrusted_candidate",
                authorized_order_id="ORD-001",
                canonical_issue_type=IssueType.SIGNED_NOT_RECEIVED,
                fixture_version="fixture-v1",
                fault_seed="default",
                evaluated_at="2026-08-23T12:00:00Z",
                trace_id="trace_untrusted_candidate",
            ),
            customer_message="candidate isolation",
            selector_model=_UntrustedWrongOrderSelectorModel(),
        )
    assert exc_info.value.reason_code == "SELECTOR_RESPONSE_NOT_STRUCTURED"
    with database.session_factory() as session:
        assert Repository(session).list_tool_calls(run_id="run_untrusted_candidate") == []
    assert not any(
        event.event_type == "tool_call_requested"
        for event in events.list_after(conversation.conversation_id)
    )

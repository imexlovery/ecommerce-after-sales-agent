from __future__ import annotations

from datetime import UTC, datetime

import pytest

from after_sales_agent.application.investigation import InvestigationOutput, InvestigationService
from after_sales_agent.application.strong_workflow import StrongWorkflowInvestigationService
from after_sales_agent.config import Settings
from after_sales_agent.domain.models import InvestigationCase, Run, TrustedToolContext
from after_sales_agent.domain.state import EvidenceGateDecision, IssueType
from after_sales_agent.events.store import EventStore
from after_sales_agent.fixtures.catalog import FixtureStore, default_fixture_store
from after_sales_agent.storage.database import create_engine_and_session, init_database
from after_sales_agent.storage.models import ConversationRow
from after_sales_agent.storage.repositories import Repository
from after_sales_agent.tools.contracts import LogisticsTicket


@pytest.mark.asyncio
async def test_signed_not_received_runs_through_graph_tools_events_and_gate() -> None:
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

    service = InvestigationService(
        settings=Settings(_env_file=None, LLM_MODE="mock"),
        fixtures=fixtures,
        session_factory=database.session_factory,
        events=events,
    )
    result = await service.investigate(
        trusted=TrustedToolContext(
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
        ),
        customer_message="ORD-001 显示签收，但我没有收到。",
    )

    assert result.gate_result.decision is EvidenceGateDecision.PROPOSE_TICKET
    assert result.actual_read_tool_executions == 5
    assert result.planning_turns == 6
    assert {ref.source_query_id for ref in result.evidence_refs}
    with database.session_factory() as session:
        repository = Repository(session)
        assert len(repository.list_tool_calls(run_id="run_test")) == 5
    event_types = [event.event_type for event in events.list_after(conversation.conversation_id)]
    assert event_types.count("tool_call_requested") == 5
    assert event_types.count("tool_call_completed") == 5
    assert event_types[-1] == "evidence_gate_evaluated"


async def _run_workflow(
    *,
    customer_id: str,
    order_id: str,
    issue_type: IssueType,
    fixtures: FixtureStore | None = None,
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

    service = StrongWorkflowInvestigationService(
        settings=Settings(_env_file=None, LLM_MODE="mock"),
        fixtures=store,
        session_factory=database.session_factory,
        events=events,
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
    )
    with database.session_factory() as session:
        tool_names = [
            row.tool_name for row in Repository(session).list_tool_calls(run_id="run_workflow")
        ]
    event_types = [event.event_type for event in events.list_after(conversation.conversation_id)]
    database.engine.dispose()
    return result, tool_names, event_types


@pytest.mark.asyncio
async def test_strong_workflow_uses_same_gate_and_governed_tools() -> None:
    result, tool_names, event_types = await _run_workflow(
        customer_id="customer_a",
        order_id="ORD-001",
        issue_type=IssueType.SIGNED_NOT_RECEIVED,
    )

    assert result.gate_result.decision is EvidenceGateDecision.PROPOSE_TICKET
    assert result.strategy == "workflow"
    assert result.actual_read_tool_executions == 5
    assert tool_names == [
        "get_order_context",
        "get_existing_logistics_tickets",
        "get_logistics_timeline",
        "get_after_sales_policy",
        "get_delivery_proof",
    ]
    assert event_types.count("workflow_step_started") == 5
    assert event_types[-1] == "evidence_gate_evaluated"


@pytest.mark.asyncio
async def test_strong_workflow_stops_before_optional_reads_within_sla() -> None:
    result, tool_names, _ = await _run_workflow(
        customer_id="customer_b",
        order_id="ORD-002",
        issue_type=IssueType.STALLED_TRACKING,
    )

    assert result.gate_result.decision is EvidenceGateDecision.COMPLETE_NO_ACTION
    assert result.gate_result.reason_code == "WITHIN_TRACKING_SLA"
    assert tool_names == [
        "get_order_context",
        "get_logistics_timeline",
        "get_after_sales_policy",
    ]


@pytest.mark.asyncio
async def test_strong_workflow_stops_after_existing_ticket_without_duplicate_reads() -> None:
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
        fixtures=fixtures,
    )

    assert result.gate_result.decision is EvidenceGateDecision.COMPLETE_NO_ACTION
    assert result.gate_result.reason_code == "ACTIVE_LOGISTICS_TICKET_EXISTS"
    assert tool_names == ["get_order_context", "get_existing_logistics_tickets"]

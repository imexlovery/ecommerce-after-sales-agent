from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from after_sales_agent.domain.models import (
    ActionExecution,
    ActionProposal,
    InvestigationCase,
    Run,
)
from after_sales_agent.domain.state import (
    ActionState,
    ActionType,
    CaseOutcome,
    CaseState,
    IssueType,
    ProposalState,
    RunState,
)
from after_sales_agent.storage import (
    CaseMutationCoordinator,
    ConcurrentMutationError,
    InvalidStateTransitionError,
    Repository,
    create_engine_and_session,
    init_database,
    session_scope,
)
from after_sales_agent.storage.models import EventRow
from after_sales_agent.storage.repositories import action_to_domain, case_to_domain
from after_sales_agent.tools.contracts import EvidenceRef


@pytest.fixture
def database():
    database = create_engine_and_session("sqlite:///:memory:")
    init_database(database.engine)
    try:
        yield database
    finally:
        database.engine.dispose()


def _case() -> InvestigationCase:
    return InvestigationCase(
        case_id="case_001",
        conversation_id="conv_001",
        customer_id="customer_001",
        authorized_order_id="ORD-001",
        canonical_issue_type=IssueType.SIGNED_NOT_RECEIVED,
    )


def _proposal(now: datetime) -> ActionProposal:
    evidence = EvidenceRef(
        tool_call_id="call_001",
        source_query_id="query_001",
        source_record_id=None,
        field_path=None,
        observed_at=now,
        result_hash="a" * 64,
    )
    return ActionProposal(
        proposal_id="prop_001",
        case_id="case_001",
        version=1,
        proposal_state=ProposalState.PENDING_CONFIRMATION,
        action_type=ActionType.CREATE_LOGISTICS_INVESTIGATION_TICKET,
        execution_parameters={"order_id": "ORD-001", "issue_type": "signed_not_received"},
        customer_visible_effect="确认创建物流核查工单",
        evidence_refs=[evidence],
        evidence_snapshot_hash="b" * 64,
        created_at=now,
        expires_at=now + timedelta(minutes=15),
    )


def test_repository_round_trips_separate_states_and_preserves_uncertain_identity(database):
    now = datetime(2026, 8, 23, 12, tzinfo=UTC)
    with session_scope(database.session_factory) as session:
        repository = Repository(session)
        repository.create_conversation(
            "customer_001", "customer_a", "mock", conversation_id="conv_001"
        )
        row = repository.create_case(_case(), created_at=now)
        assert row.case_state == "investigating"
        assert row.case_outcome is None
        assert case_to_domain(row) == _case()

        run = Run(run_id="run_001", case_id="case_001", run_state=RunState.QUEUED)
        repository.create_run(run, conversation_id="conv_001", run_kind="message")
        repository.update_run("run_001", run_state=RunState.RUNNING, changed_at=now)
        repository.update_run("run_001", run_state=RunState.SUCCEEDED, changed_at=now)

        proposal = repository.create_proposal(_proposal(now))
        repository.update_case(
            "case_001",
            expected_revision=row.revision,
            case_state=CaseState.AWAITING_CUSTOMER_CONFIRMATION,
            active_proposal_id=proposal.proposal_id,
            updated_at=now,
        )
        action = ActionExecution(
            action_id="action_001",
            proposal_id="prop_001",
            action_state=ActionState.READY,
            idempotency_key="idem_001",
        )
        action_row = repository.create_action(action)
        repository.update_action_state(action_row.action_id, ActionState.SUBMITTED, changed_at=now)
        uncertain = repository.update_action_state(
            action_row.action_id,
            ActionState.UNCERTAIN,
            error_code="WRITE_RESPONSE_AND_READBACK_UNAVAILABLE",
            changed_at=now,
        )

        assert uncertain.idempotency_key == "idem_001"
        assert action_to_domain(uncertain).action_state is ActionState.UNCERTAIN
        with pytest.raises(InvalidStateTransitionError):
            repository.update_action_state("action_001", ActionState.SUBMITTED)


def test_case_optimistic_revision_and_closed_case_are_enforced(database):
    with session_scope(database.session_factory) as session:
        repository = Repository(session)
        repository.create_conversation(
            "customer_001", "customer_a", "mock", conversation_id="conv_001"
        )
        row = repository.create_case(_case())
        original_revision = row.revision
        repository.update_case(
            row.case_id,
            expected_revision=original_revision,
            case_state=CaseState.CLOSED,
            case_outcome=CaseOutcome.RESOLVED_NO_ACTION,
            reason_code="WITHIN_POLICY_SLA",
        )
        assert row.case_state == "closed"
        assert row.case_outcome == "resolved_no_action"
        with pytest.raises(ConcurrentMutationError):
            repository.update_case(row.case_id, expected_revision=original_revision)
        with pytest.raises(InvalidStateTransitionError):
            repository.update_case(row.case_id, case_state=CaseState.INVESTIGATING)


def test_ticket_creation_is_idempotent_and_reset_removes_only_dynamic_rows(database):
    now = datetime(2026, 8, 23, 12, tzinfo=UTC)
    with session_scope(database.session_factory) as session:
        repository = Repository(session)
        repository.create_conversation(
            "customer_001", "customer_a", "mock", conversation_id="conv_001"
        )
        repository.create_case(_case())
        repository.create_proposal(_proposal(now))
        repository.create_action(
            ActionExecution(
                action_id="action_001",
                proposal_id="prop_001",
                action_state=ActionState.READY,
                idempotency_key="idem_001",
            )
        )
        first = repository.create_ticket(
            ticket_id="ticket_001",
            case_id="case_001",
            action_id="action_001",
            customer_id="customer_001",
            authorized_order_id="ORD-001",
            issue_type=IssueType.SIGNED_NOT_RECEIVED,
            idempotency_key="idem_001",
        )
        replay = repository.create_ticket(
            ticket_id="ticket_ignored",
            case_id="case_001",
            action_id="action_001",
            customer_id="customer_001",
            authorized_order_id="ORD-001",
            issue_type=IssueType.SIGNED_NOT_RECEIVED,
            idempotency_key="idem_001",
        )
        assert replay.ticket_id == first.ticket_id
        counts = repository.reset_demo_data()
        assert counts["tickets"] == 1
        assert counts["conversations"] == 1
        assert repository.get_conversation("conv_001") is None
        assert session.query(EventRow).count() == 0


@pytest.mark.asyncio
async def test_case_mutation_coordinator_serializes_same_case_only():
    coordinator = CaseMutationCoordinator()
    timeline: list[str] = []
    first_entered = asyncio.Event()
    release_first = asyncio.Event()

    async def first() -> None:
        async with coordinator.serialize("case_001"):
            timeline.append("first-enter")
            first_entered.set()
            await release_first.wait()
            timeline.append("first-exit")

    async def second() -> None:
        await first_entered.wait()
        async with coordinator.serialize("case_001"):
            timeline.append("second-enter")

    first_task = asyncio.create_task(first())
    second_task = asyncio.create_task(second())
    await first_entered.wait()
    await asyncio.sleep(0)
    assert timeline == ["first-enter"]
    release_first.set()
    await asyncio.gather(first_task, second_task)
    assert timeline == ["first-enter", "first-exit", "second-enter"]

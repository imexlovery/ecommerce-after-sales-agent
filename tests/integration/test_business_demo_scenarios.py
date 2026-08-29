from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from after_sales_agent.application.service import AfterSalesApplication
from after_sales_agent.config import Settings
from after_sales_agent.domain.state import EvidenceGateDecision, IssueType
from after_sales_agent.events.store import EventStore
from after_sales_agent.fixtures.catalog import default_fixture_store
from after_sales_agent.storage.database import Database, create_engine_and_session, init_database
from after_sales_agent.storage.repositories import Repository


@pytest.fixture
def application(tmp_path: Path) -> Iterator[tuple[AfterSalesApplication, Database]]:
    database = create_engine_and_session("sqlite:///:memory:")
    init_database(database.engine)
    application = AfterSalesApplication(
        settings=Settings(
            _env_file=None,
            LLM_MODE="mock",
            POLICY_RETRIEVAL_MODE="fake_test",
            POLICY_INDEX_ROOT=tmp_path / "policy-index",
        ),
        fixtures=default_fixture_store(),
        session_factory=database.session_factory,
        events=EventStore(database.session_factory),
        graph_checkpointer=None,
    )
    yield application, database
    database.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario_id",
    [item.scenario_id for item in default_fixture_store().scenario_catalog],
)
async def test_business_demo_scenario_catalog_is_reproducible(
    application: tuple[AfterSalesApplication, Database],
    scenario_id: str,
) -> None:
    service, database = application
    scenario = next(
        item for item in service.fixtures.scenario_catalog if item.scenario_id == scenario_id
    )
    assert scenario.customer_message is not None

    conversation = service.create_conversation(scenario.customer_key)
    result = await service.submit_message(
        conversation["conversation_id"],
        scenario.customer_message,
    )
    if scenario_id == "signed-foreign-order":
        assert result["case_id"] is None
        messages = service.get_conversation(conversation["conversation_id"])["messages"]
        assert messages[-1]["role"] == "assistant"
        assert "ORD-005" not in messages[-1]["content"]
        with database.session_factory() as session:
            assert Repository(session).list_cases(conversation["conversation_id"]) == []
        return

    assert result["case_id"] is not None

    case = service.get_case(str(result["case_id"]))
    assert case["customer_disposition"] == scenario.expected_disposition
    assert case["authorized_order_id"] == scenario.order_id
    assert case["target_shipment_id"] == scenario.target_shipment_id

    with database.session_factory() as session:
        tool_calls = Repository(session).list_tool_calls(case_id=str(result["case_id"]))
        active_actions = Repository(session).list_actions(str(result["case_id"]))
        active_tickets = Repository(session).list_tickets(case_id=str(result["case_id"]))

    assert tool_calls
    assert active_actions == []
    assert active_tickets == []
    assert all(call.actual_execution for call in tool_calls)

    events = service.events.list_after(conversation["conversation_id"])
    if scenario_id == "partial-packages-target-c":
        proposal = next(event for event in events if event.event_type == "proposal_created")
        assert proposal.payload["target_shipment_id"] == "SHP-045"
        assert "SHP-043" not in str(proposal.evidence_refs)
    if scenario_id == "stalled-active-investigation":
        assert case["reason_code"] == "ACTIVE_LOGISTICS_TICKET_EXISTS"

    assert isinstance(case["actual_read_tool_execution_count"], int)
    assert isinstance(case["agent_planning_turn_count"], int)


@pytest.mark.asyncio
async def test_split_shipment_confirmation_revalidates_and_persists_target_shipment(
    application: tuple[AfterSalesApplication, Database],
) -> None:
    service, database = application
    scenario = next(
        item
        for item in service.fixtures.scenario_catalog
        if item.scenario_id == "partial-packages-target-c"
    )
    assert scenario.customer_message is not None

    conversation = service.create_conversation(scenario.customer_key)
    submission = await service.submit_message(
        conversation["conversation_id"],
        scenario.customer_message,
    )
    assert submission["case_id"] is not None

    events = service.events.list_after(conversation["conversation_id"])
    proposal_event = next(event for event in events if event.event_type == "proposal_created")
    proposal_id = str(proposal_event.payload["proposal_id"])
    proposal_version = int(proposal_event.payload["proposal_version"])
    with database.session_factory() as session:
        repository = Repository(session)
        proposal = repository.require_proposal(proposal_id)
        case = repository.require_case(proposal.case_id)
        assert proposal.execution_parameters["target_shipment_id"] == "SHP-045"
        revalidated = service._revalidate_gate(case_row=case, proposal_row=proposal)
        assert revalidated is not None
        assert revalidated.decision is EvidenceGateDecision.PROPOSE_TICKET
        assert revalidated.reason_code == "STALLED_TRACKING_EVIDENCE_COMPLETE"
        assert "carrier_alerts" in revalidated.critical_result_hashes

    confirmed = await service.confirm_proposal(proposal_id, proposal_version)

    assert confirmed["proposal_state"] == "confirmed"
    assert service.get_case(str(submission["case_id"]))["case_outcome"] == "ticket_created"
    with database.session_factory() as session:
        repository = Repository(session)
        ticket = repository.list_tickets(case_id=str(submission["case_id"]))[0]
        assert ticket.target_shipment_id == "SHP-045"
        assert ticket.details["target_shipment_id"] == "SHP-045"
        assert (
            repository.get_active_ticket(
                "ORD-039",
                IssueType.STALLED_TRACKING,
                target_shipment_id="SHP-045",
            )
            is ticket
        )
        assert (
            repository.get_active_ticket(
                "ORD-039",
                IssueType.STALLED_TRACKING,
                target_shipment_id="SHP-044",
            )
            is None
        )

    service.fixtures.reset_dynamic_tickets()
    service.load_persisted_tickets()
    restored = service.fixtures.get_active_tickets(
        "ORD-039",
        IssueType.STALLED_TRACKING,
        target_shipment_id="SHP-045",
    )
    assert len(restored) == 1
    assert restored[0].target_shipment_id == "SHP-045"
    assert (
        service.fixtures.get_active_tickets(
            "ORD-039",
            IssueType.STALLED_TRACKING,
            target_shipment_id="SHP-044",
        )
        == []
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "customer_message",
    [
        "ORD-039 TRK-SYN-039-P03 的物流没有更新，请帮我查一下。",
        "ORD-039 第三个包裹的物流没有更新，请帮我查一下。",
    ],
)
async def test_split_shipment_explicit_tracking_and_package_sequence_keep_target_scope(
    application: tuple[AfterSalesApplication, Database],
    customer_message: str,
) -> None:
    service, _ = application
    conversation = service.create_conversation("customer_r")
    submission = await service.submit_message(conversation["conversation_id"], customer_message)

    assert submission["case_id"] is not None
    case = service.get_case(str(submission["case_id"]))
    assert case["target_shipment_id"] == "SHP-045"


@pytest.mark.asyncio
async def test_partial_shipment_with_multiple_stalled_candidates_clarifies_without_action(
    application: tuple[AfterSalesApplication, Database],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, database = application
    shipments = service.fixtures.get_shipments("ORD-039")
    monkeypatch.setitem(
        service.fixtures._shipments,  # type: ignore[attr-defined]
        "ORD-039",
        [
            item.model_copy(update={"shipment_status": "stalled"})
            if item.shipment_id == "SHP-044"
            else item
            for item in shipments
        ],
    )

    conversation = service.create_conversation("customer_r")
    submission = await service.submit_message(
        conversation["conversation_id"],
        "ORD-039 我只收到一部分，剩下的包裹怎么了？",
    )

    assert submission["customer_disposition"] == "CLARIFY"
    case = service.get_case(str(submission["case_id"]))
    assert case["case_state"] == "awaiting_customer_input"
    assert case["target_shipment_id"] is None
    with database.session_factory() as session:
        repository = Repository(session)
        case_id = str(submission["case_id"])
        assert repository.list_tool_calls(case_id=case_id) == []
        assert repository.list_proposals(case_id) == []
        assert repository.list_actions(case_id) == []
        assert repository.list_tickets(case_id=case_id) == []

    clarification = next(
        event
        for event in service.events.list_after(conversation["conversation_id"])
        if event.event_type == "business_clarification_requested"
    )
    assert clarification.payload["clarification_kind"] == "target_shipment"


@pytest.mark.asyncio
async def test_partial_shipment_clarification_reply_resolves_target_before_investigation(
    application: tuple[AfterSalesApplication, Database],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, database = application
    shipments = service.fixtures.get_shipments("ORD-039")
    monkeypatch.setitem(
        service.fixtures._shipments,  # type: ignore[attr-defined]
        "ORD-039",
        [
            item.model_copy(update={"shipment_status": "stalled"})
            if item.shipment_id == "SHP-044"
            else item
            for item in shipments
        ],
    )
    conversation = service.create_conversation("customer_r")
    initial = await service.submit_message(
        conversation["conversation_id"],
        "ORD-039 我只收到一部分，剩下的包裹怎么了？",
    )
    follow_up = await service.submit_message(
        conversation["conversation_id"],
        "请查第三个包裹。",
    )

    assert follow_up["case_id"] == initial["case_id"]
    case_id = str(initial["case_id"])
    assert service.get_case(case_id)["target_shipment_id"] == "SHP-045"
    with database.session_factory() as session:
        tool_calls = Repository(session).list_tool_calls(case_id=case_id)
    assert tool_calls

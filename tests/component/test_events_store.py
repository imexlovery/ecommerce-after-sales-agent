from __future__ import annotations

import asyncio

import pytest

from after_sales_agent.events import EventDraft, EventStore, EventVisibility
from after_sales_agent.storage import Repository, create_engine_and_session, init_database
from after_sales_agent.storage.models import EventRow


@pytest.fixture
def event_store():
    database = create_engine_and_session("sqlite:///:memory:")
    init_database(database.engine)
    with database.session_factory.begin() as session:
        Repository(session).create_conversation(
            "customer_001", "customer_a", "mock", conversation_id="conv_001"
        )
    try:
        yield EventStore(database.session_factory), database
    finally:
        database.engine.dispose()


@pytest.mark.asyncio
async def test_event_store_assigns_monotonic_sequence_and_replays_without_side_effects(event_store):
    store, _ = event_store
    first = await store.append(
        EventDraft(
            conversation_id="conv_001",
            event_type="message_received",
            visibility=EventVisibility.BOTH,
            summary="Customer message accepted",
            payload={"message_id": "msg_001"},
        )
    )
    second = await store.append(
        EventDraft(
            conversation_id="conv_001",
            event_type="triage_completed",
            visibility=EventVisibility.DEVELOPER,
            summary="Triage schema validated",
            payload={"intent": "signed_not_received"},
        )
    )

    assert [first.sequence, second.sequence] == [1, 2]
    assert [event.event_id for event in store.list_after("conv_001", 1)] == [second.event_id]
    assert store.list_after_event_id("conv_001", first.event_id)[0].event_id == second.event_id
    assert store.get(second.event_id).to_dict()["visibility"] == "developer"


@pytest.mark.asyncio
async def test_subscribe_registers_before_replay_and_deduplicates_live_queue(event_store):
    store, _ = event_store
    first = await store.append(
        EventDraft(
            conversation_id="conv_001",
            event_type="run_started",
            visibility="developer",
            summary="Run started",
        )
    )
    stream = store.subscribe("conv_001")
    replayed = await anext(stream)
    assert replayed.event_id == first.event_id

    second = await store.append(
        EventDraft(
            conversation_id="conv_001",
            event_type="run_succeeded",
            visibility="developer",
            summary="Run succeeded",
        )
    )
    live = await asyncio.wait_for(anext(stream), timeout=1)
    assert live.event_id == second.event_id
    assert live.sequence == 2
    await stream.aclose()


@pytest.mark.asyncio
async def test_canonical_event_rows_reject_orm_mutation(event_store):
    store, database = event_store
    event = await store.append(
        EventDraft(
            conversation_id="conv_001",
            event_type="policy_decided",
            visibility="both",
            summary="Policy accepted the supported request",
        )
    )
    with database.session_factory() as session:
        row = session.get(EventRow, event.event_id)
        assert row is not None
        row.summary = "rewritten audit history"
        with pytest.raises(RuntimeError, match="append-only"):
            session.commit()
        session.rollback()


def test_event_type_and_schema_contract_are_validated():
    with pytest.raises(ValueError, match="lower snake_case"):
        EventDraft(
            conversation_id="conv_001",
            event_type="Tool Completed",
            visibility="developer",
            summary="invalid",
        )
    with pytest.raises(ValueError, match="positive"):
        EventDraft(
            conversation_id="conv_001",
            event_type="tool_completed",
            visibility="developer",
            summary="invalid",
            schema_version=0,
        )

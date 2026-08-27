"""Persist-first canonical event store with replay-safe in-memory fan-out."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

from sqlalchemy import select, update

from after_sales_agent.storage.database import SessionFactory
from after_sales_agent.storage.models import ConversationRow, EventRow, utc_now
from after_sales_agent.storage.repositories import StorageNotFoundError

from .models import EventDraft, EventEnvelope, EventVisibility


class _EventHub:
    """Best-effort local notification; the database remains replay authority."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[EventEnvelope]]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def register(self, conversation_id: str) -> asyncio.Queue[EventEnvelope]:
        queue: asyncio.Queue[EventEnvelope] = asyncio.Queue()
        async with self._lock:
            self._subscribers[conversation_id].add(queue)
        return queue

    async def unregister(self, conversation_id: str, queue: asyncio.Queue[EventEnvelope]) -> None:
        async with self._lock:
            subscribers = self._subscribers.get(conversation_id)
            if subscribers is None:
                return
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(conversation_id, None)

    async def publish(self, event: EventEnvelope) -> None:
        async with self._lock:
            subscribers = tuple(self._subscribers.get(event.conversation_id, ()))
        for queue in subscribers:
            queue.put_nowait(event)


def _copy_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _copy_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_copy_json(item) for item in value]
    return value


def _to_envelope(row: EventRow) -> EventEnvelope:
    return EventEnvelope(
        schema_version=row.schema_version,
        event_id=row.event_id,
        sequence=row.sequence,
        timestamp=row.timestamp,
        conversation_id=row.conversation_id,
        case_id=row.case_id,
        run_id=row.run_id,
        event_type=row.event_type,
        visibility=EventVisibility(row.visibility),
        summary=row.summary,
        payload=_copy_json(row.payload),
        evidence_refs=tuple(_copy_json(row.evidence_refs)),
    )


class EventStore:
    """Assign per-Conversation sequence numbers and support at-least-once replay."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._hub = _EventHub()

    async def append(self, draft: EventDraft) -> EventEnvelope:
        """Commit an event before notifying any subscriber."""

        timestamp = draft.timestamp or utc_now()
        with self._session_factory() as session, session.begin():
            sequence_statement = (
                update(ConversationRow)
                .where(ConversationRow.conversation_id == draft.conversation_id)
                .values(next_event_sequence=ConversationRow.next_event_sequence + 1)
                .returning(ConversationRow.next_event_sequence)
            )
            sequence = session.execute(sequence_statement).scalar_one_or_none()
            if sequence is None:
                raise StorageNotFoundError(f"Conversation {draft.conversation_id!r} was not found")
            row = EventRow(
                event_id=draft.event_id,
                schema_version=draft.schema_version,
                sequence=sequence,
                timestamp=timestamp,
                conversation_id=draft.conversation_id,
                case_id=draft.case_id,
                run_id=draft.run_id,
                event_type=draft.event_type,
                visibility=EventVisibility(draft.visibility).value,
                summary=draft.summary,
                payload=_copy_json(draft.payload),
                evidence_refs=_copy_json(draft.evidence_refs),
            )
            session.add(row)
        envelope = _to_envelope(row)
        await self._hub.publish(envelope)
        return envelope

    def get(self, event_id: str) -> EventEnvelope | None:
        with self._session_factory() as session:
            row = session.get(EventRow, event_id)
            return _to_envelope(row) if row is not None else None

    def list_after(
        self,
        conversation_id: str,
        after_sequence: int = 0,
        *,
        limit: int = 500,
    ) -> list[EventEnvelope]:
        if after_sequence < 0:
            raise ValueError("after_sequence must be nonnegative")
        if not 1 <= limit <= 5_000:
            raise ValueError("limit must be between 1 and 5000")
        with self._session_factory() as session:
            statement = (
                select(EventRow)
                .where(
                    EventRow.conversation_id == conversation_id,
                    EventRow.sequence > after_sequence,
                )
                .order_by(EventRow.sequence)
                .limit(limit)
            )
            return [_to_envelope(row) for row in session.scalars(statement)]

    def list_after_event_id(
        self,
        conversation_id: str,
        last_event_id: str | None,
        *,
        limit: int = 500,
    ) -> list[EventEnvelope]:
        """Resolve the standard SSE Last-Event-ID to a Conversation sequence."""

        if last_event_id is None or last_event_id == "":
            return self.list_after(conversation_id, 0, limit=limit)
        event = self.get(last_event_id)
        if event is None or event.conversation_id != conversation_id:
            raise StorageNotFoundError("Last-Event-ID is not visible in this Conversation")
        return self.list_after(conversation_id, event.sequence, limit=limit)

    def list_evidence_refs(
        self,
        conversation_id: str,
        *,
        case_id: str | None = None,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return persisted EvidenceRef links for deterministic replay.

        ToolCall rows remain the canonical observation records; event-linked
        references provide the stable source links consumed by the V3 reducer.
        """

        with self._session_factory() as session:
            statement = select(EventRow).where(EventRow.conversation_id == conversation_id)
            if case_id is not None:
                statement = statement.where(EventRow.case_id == case_id)
            if run_id is not None:
                statement = statement.where(EventRow.run_id == run_id)
            statement = statement.order_by(EventRow.sequence)
            refs: list[dict[str, Any]] = []
            for row in session.scalars(statement):
                if row.event_type not in {"tool_call_completed", "tool_call_cache_hit"}:
                    continue
                refs.extend(_copy_json(row.evidence_refs))
            return refs

    async def subscribe(
        self,
        conversation_id: str,
        after_sequence: int = 0,
    ) -> AsyncIterator[EventEnvelope]:
        """Register before replay, then deduplicate queued events by sequence.

        Registration-before-replay closes the race where an event could be
        committed between the replay query and live subscription. Receiving a
        replayed event only yields stored data and never invokes business code.
        """

        if after_sequence < 0:
            raise ValueError("after_sequence must be nonnegative")
        queue = await self._hub.register(conversation_id)
        cursor = after_sequence
        try:
            while True:
                batch = self.list_after(conversation_id, cursor)
                if not batch:
                    break
                for event in batch:
                    if event.sequence > cursor:
                        cursor = event.sequence
                        yield event
                if len(batch) < 500:
                    break

            while True:
                event = await queue.get()
                if event.sequence <= cursor:
                    continue
                cursor = event.sequence
                yield event
        finally:
            with suppress(RuntimeError):
                await self._hub.unregister(conversation_id, queue)

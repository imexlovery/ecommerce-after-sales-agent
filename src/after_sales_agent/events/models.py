"""Versioned canonical event envelope shared by persistence and SSE."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

CORE_EVENT_TYPES = frozenset(
    {
        "message_received",
        "message_rejected",
        "triage_started",
        "triage_completed",
        "triage_failed",
        "policy_decided",
        "request_fragment_blocked",
        "case_created",
        "case_issue_revised",
        "run_started",
        "agent_turn_started",
        "agent_turn_completed",
        "workflow_step_started",
        "tool_call_requested",
        "tool_call_blocked",
        "tool_call_cache_hit",
        "tool_call_started",
        "tool_call_completed",
        "tool_call_failed",
        "evidence_gate_evaluated",
        "business_clarification_requested",
        "case_fact_merge_decided",
        "customer_reply_created",
        "action_recommended",
        "proposal_created",
        "proposal_confirmed",
        "proposal_declined",
        "proposal_superseded",
        "proposal_expired",
        "proposal_invalidated",
        "action_submitted",
        "action_verified",
        "action_failed",
        "action_uncertain",
        "case_closed",
        "run_succeeded",
        "run_failed",
        # V3-A1 durable orchestration traces.  They remain developer-visible
        # canonical events and are persisted before SSE projection.
        "decision_trace_record",
        "recovery_trace_record",
        "state_trace_record",
        "evidence_progress_rebuilt",
    }
)

_EVENT_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,95}$")


class EventVisibility(StrEnum):
    CUSTOMER = "customer"
    DEVELOPER = "developer"
    BOTH = "both"


def new_event_id() -> str:
    return f"evt_{uuid4().hex}"


@dataclass(frozen=True, slots=True)
class EventDraft:
    """An event before its Conversation sequence is assigned."""

    conversation_id: str
    event_type: str
    visibility: EventVisibility | str
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[dict[str, Any], ...] | list[dict[str, Any]] = field(default_factory=tuple)
    case_id: str | None = None
    run_id: str | None = None
    schema_version: int = 1
    timestamp: datetime | None = None
    event_id: str = field(default_factory=new_event_id)

    def __post_init__(self) -> None:
        if not self.conversation_id:
            raise ValueError("conversation_id must not be empty")
        if not _EVENT_TYPE_PATTERN.fullmatch(self.event_type):
            raise ValueError("event_type must be lower snake_case and at most 96 characters")
        if self.schema_version < 1:
            raise ValueError("schema_version must be positive")
        if not self.summary:
            raise ValueError("summary must not be empty")
        EventVisibility(self.visibility)
        if self.timestamp is not None and (
            self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None
        ):
            raise ValueError("event timestamp must be timezone-aware")


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    schema_version: int
    event_id: str
    sequence: int
    timestamp: datetime
    conversation_id: str
    case_id: str | None
    run_id: str | None
    event_type: str
    visibility: EventVisibility
    summary: str
    payload: dict[str, Any]
    evidence_refs: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "sequence": self.sequence,
            "timestamp": self.timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "conversation_id": self.conversation_id,
            "case_id": self.case_id,
            "run_id": self.run_id,
            "event_type": self.event_type,
            "visibility": self.visibility.value,
            "summary": self.summary,
            "payload": self.payload,
            "evidence_refs": list(self.evidence_refs),
        }

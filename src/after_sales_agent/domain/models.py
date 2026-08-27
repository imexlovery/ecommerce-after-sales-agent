"""Validated business snapshots independent of persistence and the Agent framework."""

from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from after_sales_agent.tools.contracts import EvidenceRef

from .state import (
    ActionState,
    ActionType,
    CaseOutcome,
    CaseState,
    IssueType,
    ProposalState,
    RunState,
    TriageIntent,
)


class DomainModel(BaseModel):
    """Base configuration for immutable domain snapshots."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


class TriageResult(DomainModel):
    """The only fields a tool-free triage model may produce."""

    intent: TriageIntent
    risk_flags: list[str] = Field(default_factory=list, max_length=16)
    order_ids_mentioned: list[str] = Field(default_factory=list, max_length=16)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def normalize_contract(self) -> "TriageResult":
        if len(self.risk_flags) != len(set(self.risk_flags)):
            raise ValueError("risk_flags must not contain duplicates")
        if len(self.order_ids_mentioned) != len(set(self.order_ids_mentioned)):
            raise ValueError("order_ids_mentioned must not contain duplicates")
        return self


class TrustedToolContext(DomainModel):
    """Server-owned values that model-authored tool arguments cannot override."""

    customer_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    authorized_order_id: str = Field(min_length=1)
    canonical_issue_type: IssueType
    fixture_version: str = Field(min_length=1)
    fault_seed: str = Field(min_length=1)
    evaluated_at: datetime
    trace_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def check_scenario_clock(self) -> "TrustedToolContext":
        _require_aware(self.evaluated_at, "evaluated_at")
        return self


class IssueTypeRevision(DomainModel):
    reported: TriageIntent
    canonical: TriageIntent
    reason_code: str = Field(min_length=1)
    revised_at: datetime
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_time(self) -> "IssueTypeRevision":
        _require_aware(self.revised_at, "revised_at")
        return self


class InvestigationCase(DomainModel):
    case_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    authorized_order_id: str = Field(min_length=1)
    canonical_issue_type: IssueType
    case_state: CaseState = CaseState.INVESTIGATING
    case_outcome: CaseOutcome | None = None
    reason_code: str | None = None
    related_case_id: str | None = None
    business_clarifications: int = Field(default=0, ge=0, le=2)
    planning_turns: int = Field(default=0, ge=0, le=16)
    read_tool_executions: int = Field(default=0, ge=0, le=6)
    issue_type_revision_history: list[IssueTypeRevision] = Field(default_factory=list)

    @model_validator(mode="after")
    def keep_state_and_outcome_consistent(self) -> "InvestigationCase":
        is_closed = self.case_state is CaseState.CLOSED
        if is_closed != (self.case_outcome is not None):
            raise ValueError("case_outcome is required only when case_state is closed")
        if is_closed and not self.reason_code:
            raise ValueError("a closed Case requires a deterministic reason_code")
        if not is_closed and self.reason_code is not None:
            raise ValueError("an open Case cannot have a closure reason_code")
        return self


class Run(DomainModel):
    run_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    run_state: RunState = RunState.QUEUED
    planning_turns: int = Field(default=0, ge=0, le=8)
    failure_class: str | None = None

    @model_validator(mode="after")
    def keep_failure_separate(self) -> "Run":
        if self.run_state is RunState.FAILED and not self.failure_class:
            raise ValueError("a failed Run requires failure_class")
        if self.run_state is not RunState.FAILED and self.failure_class is not None:
            raise ValueError("failure_class belongs only to a failed Run")
        return self


class ActionRecommendation(DomainModel):
    action_type: ActionType
    rationale_summary: str = Field(min_length=1, max_length=2_000)
    evidence_refs: list[EvidenceRef] = Field(min_length=1)


class ActionProposal(DomainModel):
    proposal_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    proposal_state: ProposalState = ProposalState.PENDING_CONFIRMATION
    action_type: ActionType
    execution_parameters: dict[str, Any]
    customer_visible_effect: str = Field(min_length=1, max_length=2_000)
    evidence_refs: list[EvidenceRef] = Field(min_length=1)
    evidence_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_fact_identity: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def validate_lifetime(self) -> "ActionProposal":
        _require_aware(self.created_at, "created_at")
        _require_aware(self.expires_at, "expires_at")
        if self.expires_at - self.created_at != timedelta(minutes=15):
            raise ValueError("an ActionProposal must expire exactly 15 minutes after creation")
        return self


class ActionExecution(DomainModel):
    action_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    action_state: ActionState = ActionState.READY
    idempotency_key: str = Field(min_length=1)
    submitted_at: datetime | None = None
    verified_at: datetime | None = None
    error_code: str | None = None

    @model_validator(mode="after")
    def validate_action_timestamps(self) -> "ActionExecution":
        if self.submitted_at is not None:
            _require_aware(self.submitted_at, "submitted_at")
        if self.verified_at is not None:
            _require_aware(self.verified_at, "verified_at")
        submitted_states = {
            ActionState.SUBMITTED,
            ActionState.SUCCEEDED,
            ActionState.FAILED_RETRYABLE,
            ActionState.FAILED_TERMINAL,
            ActionState.UNCERTAIN,
        }
        if (self.action_state in submitted_states) != (self.submitted_at is not None):
            raise ValueError("submitted_at must reflect whether the action left the executor")
        if self.verified_at is not None and self.action_state is not ActionState.SUCCEEDED:
            raise ValueError("verified_at is valid only for a succeeded action")
        if self.action_state is ActionState.SUCCEEDED and self.verified_at is None:
            raise ValueError("a succeeded action requires read-back verification")
        return self

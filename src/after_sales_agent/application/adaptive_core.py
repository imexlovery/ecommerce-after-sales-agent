# ruff: noqa: E501
"""V3-A1 Adaptive Investigation Core.

This module owns the small, deterministic boundary between a selector and the
existing governed read runtime.  It intentionally contains no business action
or Evidence Gate decision.  The selector output is untrusted; all identity,
scope, evidence progress, recovery, and trace facts are server-owned here.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final, Literal, Protocol, runtime_checkable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from after_sales_agent.domain.models import TrustedToolContext
from after_sales_agent.domain.state import (
    EvidenceAvailability,
    ExecutionStatus,
    IssueType,
)
from after_sales_agent.tools.budget import ToolBudgetSnapshot
from after_sales_agent.tools.contracts import EvidenceRef, ToolResult
from after_sales_agent.tools.service import READ_TOOL_NAMES


class _Contract(BaseModel):
    """Frozen, extra-forbidden V3 contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ObservationAction(StrEnum):
    CALL_TOOL = "call_tool"
    FINISH = "finish"


class SelectorKind(StrEnum):
    AGENT = "agent"
    WORKFLOW = "workflow"


class ObservationReasonCode(StrEnum):
    FIRST_REQUIRED_OBSERVATION = "FIRST_REQUIRED_OBSERVATION"
    MISSING_REQUIRED_EVIDENCE = "MISSING_REQUIRED_EVIDENCE"
    OBSERVATION_CONDITIONAL_BRANCH = "OBSERVATION_CONDITIONAL_BRANCH"
    OPTIONAL_EXPLANATORY_CONTEXT = "OPTIONAL_EXPLANATORY_CONTEXT"
    FINALIZATION_REQUESTED = "FINALIZATION_REQUESTED"


class EvidenceRequirementCode(StrEnum):
    ORDER_STATUS = "ORDER_STATUS"
    TRACKING_TIMELINE = "TRACKING_TIMELINE"
    DELIVERY_PROOF = "DELIVERY_PROOF"
    POLICY_APPLICABILITY = "POLICY_APPLICABILITY"
    ACTIVE_TICKET_STATUS = "ACTIVE_TICKET_STATUS"
    CARRIER_ALERT_CONTEXT = "CARRIER_ALERT_CONTEXT"


class RequirementApplicability(StrEnum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    NOT_REQUIRED = "not_required"


class EvidenceProgressStatus(StrEnum):
    MISSING = "missing"
    SATISFIED_PRESENT = "satisfied_present"
    SATISFIED_ABSENT = "satisfied_absent"
    RETRY_PENDING = "retry_pending"
    UNAVAILABLE_FINAL = "unavailable_final"
    CONFLICT = "conflict"


class GateReadiness(StrEnum):
    NOT_EVALUABLE = "not_evaluable"
    EVALUABLE = "evaluable"


class RecoveryRoute(StrEnum):
    REPLAN = "replan"
    RETRY_EXACT = "retry_exact"
    FINALIZE = "finalize"
    SAFE_STOP = "safe_stop"


class RecoveryReasonCode(StrEnum):
    REPLAN_REQUIRED = "REPLAN_REQUIRED"
    RETRYABLE_TOOL_FAILURE = "RETRYABLE_TOOL_FAILURE"
    GATE_READY = "GATE_READY"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    INTEGRITY_CONFLICT = "INTEGRITY_CONFLICT"
    SOURCE_REVISION_CHANGED_DURING_RETRY = "SOURCE_REVISION_CHANGED_DURING_RETRY"
    STUCK_REPEATED_DECISION = "STUCK_REPEATED_DECISION"
    STUCK_NO_EVIDENCE_PROGRESS = "STUCK_NO_EVIDENCE_PROGRESS"
    PREMATURE_FINISH = "PREMATURE_FINISH"
    INVALID_OBSERVATION = "INVALID_OBSERVATION"


class CandidateValidationStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class TracePhase(StrEnum):
    SELECT = "select"
    VALIDATE = "validate"
    EXECUTE = "execute"
    REDUCE = "reduce"
    ROUTE = "route"
    FINALIZE = "finalize"
    SAFE_STOP = "safe_stop"
    TERMINAL = "terminal"


@runtime_checkable
class ObservationSelector(Protocol):
    """Shared selector boundary; implementations differ only here."""

    async def select_next_observation(
        self, context: DecisionContext
    ) -> NextObservationCandidate: ...


REQUIREMENT_REGISTRY_VERSION: Final = "v3a.requirements.v1"
DECISION_CONTEXT_SCHEMA_VERSION: Final = "v3a.decision_context.v1"
CANDIDATE_SCHEMA_VERSION: Final = "v3a.next_observation_candidate.v1"
NEXT_OBSERVATION_SCHEMA_VERSION: Final = "v3a.next_observation.v1"
PROGRESS_SCHEMA_VERSION: Final = "v3a.evidence_progress.v1"
RETRY_SCHEMA_VERSION: Final = "v3a.retry_directive.v1"
RECOVERY_SCHEMA_VERSION: Final = "v3a.recovery_decision.v1"
TRACE_SCHEMA_VERSION: Final = "v3a.trace.v1"
SHARED_RUNTIME_VERSION: Final = "v3a.adaptive-runtime.v1"
SHARED_COMPONENT_VERSIONS: Final = {
    "requirement_registry": REQUIREMENT_REGISTRY_VERSION,
    "validator": "v3a.validator.v1",
    "reducer": PROGRESS_SCHEMA_VERSION,
    "router": RECOVERY_SCHEMA_VERSION,
    "retry": RETRY_SCHEMA_VERSION,
    "trace": TRACE_SCHEMA_VERSION,
    "tool_registry": "v2.read-tools.v1",
    "evidence_gate": "project-evidence-gate.v2",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_arguments_hash(arguments: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(dict(arguments)).encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


class BudgetSnapshot(_Contract):
    case_planning_turns: int = Field(ge=0, le=16)
    run_planning_turns: int = Field(ge=0, le=8)
    actual_read_tool_executions: int = Field(ge=0, le=6)

    @classmethod
    def from_tool_budget(cls, snapshot: ToolBudgetSnapshot) -> BudgetSnapshot:
        return cls(
            case_planning_turns=snapshot.case_planning_turns,
            run_planning_turns=snapshot.run_planning_turns,
            actual_read_tool_executions=snapshot.actual_read_tool_executions,
        )


class ObservationSummary(_Contract):
    tool_name: str = Field(min_length=1, max_length=96)
    execution_status: str = Field(min_length=1, max_length=32)
    evidence_availability: str = Field(min_length=1, max_length=24)
    result_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_version: str | None = Field(default=None, max_length=128)


class DecisionContext(_Contract):
    schema_version: Literal["v3a.decision_context.v1"] = DECISION_CONTEXT_SCHEMA_VERSION
    case_id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    canonical_issue_type: IssueType
    authorized_order_id: str = Field(min_length=1, max_length=64)
    customer_message: str = Field(default="", max_length=4_000)
    case_fact_snapshot: dict[str, Any] | None = None
    evidence_progress: EvidenceProgressSnapshot
    latest_observation: ObservationSummary | None = None
    allowed_tools: tuple[str, ...]
    remaining_budget: BudgetSnapshot
    prior_decision_fingerprints: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    prompt_policy_version: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_tools(self) -> DecisionContext:
        if len(self.allowed_tools) != len(set(self.allowed_tools)):
            raise ValueError("allowed_tools must not contain duplicates")
        if any(tool not in READ_TOOL_NAMES for tool in self.allowed_tools):
            raise ValueError("allowed_tools contains a tool outside the shared registry")
        return self


class NextObservationCandidate(_Contract):
    """Untrusted selector output; trusted identity fields are deliberately absent."""

    schema_version: Literal["v3a.next_observation_candidate.v1"] = CANDIDATE_SCHEMA_VERSION
    action: ObservationAction
    tool_name: str | None = Field(default=None, max_length=96)
    arguments: dict[str, Any] = Field(default_factory=dict)
    addresses: tuple[EvidenceRequirementCode, ...] = Field(default_factory=tuple)
    reason_code: ObservationReasonCode
    decision_summary: str | None = Field(default=None, max_length=160)

    @model_validator(mode="after")
    def validate_shape(self) -> NextObservationCandidate:
        if len(self.addresses) != len(set(self.addresses)):
            raise ValueError("addresses must not contain duplicates")
        if self.action is ObservationAction.FINISH:
            if self.tool_name is not None or self.arguments:
                raise ValueError("finish candidate must not carry a tool or arguments")
        else:
            if not self.tool_name:
                raise ValueError("call_tool candidate requires tool_name")
            if not self.arguments:
                raise ValueError("call_tool candidate requires arguments")
        return self


class NextObservation(_Contract):
    schema_version: Literal["v3a.next_observation.v1"] = NEXT_OBSERVATION_SCHEMA_VERSION
    decision_id: str = Field(min_length=1, max_length=128)
    selector_kind: SelectorKind
    case_id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    planning_turn: int = Field(ge=1, le=16)
    action: ObservationAction
    tool_name: str | None = Field(default=None, max_length=96)
    canonical_arguments: dict[str, Any] = Field(default_factory=dict)
    canonical_arguments_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    addresses: tuple[EvidenceRequirementCode, ...] = Field(default_factory=tuple)
    reason_code: ObservationReasonCode
    evidence_progress_revision: int = Field(ge=0)
    evidence_progress_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    validated_at: datetime

    @model_validator(mode="after")
    def validate_identity(self) -> NextObservation:
        if self.validated_at.tzinfo is None or self.validated_at.utcoffset() is None:
            raise ValueError("validated_at must be timezone-aware")
        if len(self.addresses) != len(set(self.addresses)):
            raise ValueError("addresses must not contain duplicates")
        if self.action is ObservationAction.FINISH:
            if self.tool_name is not None or self.canonical_arguments:
                raise ValueError("finish observation must not carry tool arguments")
        elif self.tool_name is None:
            raise ValueError("call_tool observation requires tool_name")
        if canonical_arguments_hash(self.canonical_arguments) != self.canonical_arguments_hash:
            raise ValueError("canonical_arguments_hash does not match arguments")
        expected = decision_fingerprint(
            action=self.action,
            tool_name=self.tool_name,
            canonical_arguments=self.canonical_arguments,
            addresses=self.addresses,
            evidence_progress_hash=self.evidence_progress_hash,
        )
        if expected != self.decision_fingerprint:
            raise ValueError("decision_fingerprint does not match normalized identity")
        return self


class RequirementProgress(_Contract):
    applicability: RequirementApplicability
    status: EvidenceProgressStatus
    supporting_tool_call_ids: tuple[str, ...] = Field(default_factory=tuple)
    evidence_ref_ids: tuple[str, ...] = Field(default_factory=tuple)
    result_hashes: tuple[str, ...] = Field(default_factory=tuple)
    source_versions: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_sets(self) -> RequirementProgress:
        for name in (
            "supporting_tool_call_ids",
            "evidence_ref_ids",
            "result_hashes",
            "source_versions",
        ):
            values = getattr(self, name)
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must not contain duplicates")
        return self


class EvidenceProgressSnapshot(_Contract):
    schema_version: Literal["v3a.evidence_progress.v1"] = PROGRESS_SCHEMA_VERSION
    case_id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    canonical_issue_type: IssueType
    revision: int = Field(ge=0)
    requirements: dict[EvidenceRequirementCode, RequirementProgress]
    gate_readiness: GateReadiness
    missing_required_codes: tuple[EvidenceRequirementCode, ...] = Field(default_factory=tuple)
    terminal_trigger_codes: tuple[str, ...] = Field(default_factory=tuple)
    last_actual_tool_call_id: str | None = None
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    rebuilt_at: datetime

    @model_validator(mode="after")
    def validate_snapshot(self) -> EvidenceProgressSnapshot:
        if self.rebuilt_at.tzinfo is None or self.rebuilt_at.utcoffset() is None:
            raise ValueError("rebuilt_at must be timezone-aware")
        if len(self.missing_required_codes) != len(set(self.missing_required_codes)):
            raise ValueError("missing_required_codes must not contain duplicates")
        expected = progress_snapshot_hash(self)
        if expected != self.snapshot_hash:
            raise ValueError("snapshot_hash does not match canonical progress")
        return self


class RetryDirective(_Contract):
    schema_version: Literal["v3a.retry_directive.v1"] = RETRY_SCHEMA_VERSION
    retry_of_tool_call_id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(min_length=1, max_length=96)
    canonical_arguments: dict[str, Any]
    canonical_arguments_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_version: str = Field(min_length=1, max_length=128)
    next_attempt_number: Literal[2] = 2
    reason_code: Literal["RETRYABLE_TOOL_FAILURE"] = "RETRYABLE_TOOL_FAILURE"
    issued_from_progress_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_hash(self) -> RetryDirective:
        if canonical_arguments_hash(self.canonical_arguments) != self.canonical_arguments_hash:
            raise ValueError("retry canonical_arguments_hash does not match arguments")
        return self


class RecoveryDecision(_Contract):
    schema_version: Literal["v3a.recovery_decision.v1"] = RECOVERY_SCHEMA_VERSION
    recovery_id: str = Field(min_length=1, max_length=128)
    case_id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    route: RecoveryRoute
    reason_code: RecoveryReasonCode
    trigger_tool_call_id: str | None = None
    trigger_result_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    evidence_progress_before_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_progress_after_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    retry_directive: RetryDirective | None = None
    budget_snapshot: BudgetSnapshot
    decided_at: datetime

    @model_validator(mode="after")
    def validate_route(self) -> RecoveryDecision:
        if self.decided_at.tzinfo is None or self.decided_at.utcoffset() is None:
            raise ValueError("decided_at must be timezone-aware")
        if self.route is RecoveryRoute.RETRY_EXACT and self.retry_directive is None:
            raise ValueError("retry_exact requires a RetryDirective")
        if self.route is not RecoveryRoute.RETRY_EXACT and self.retry_directive is not None:
            raise ValueError("only retry_exact may carry a RetryDirective")
        return self


class DecisionTraceRecord(_Contract):
    schema_version: Literal["v3a.trace.v1"] = TRACE_SCHEMA_VERSION
    trace_sequence: int = Field(ge=1)
    case_id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    decision_id: str = Field(min_length=1, max_length=128)
    selector_kind: SelectorKind
    planning_turn: int = Field(ge=1, le=16)
    action: ObservationAction
    tool_name: str | None = None
    canonical_arguments_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    addresses: tuple[EvidenceRequirementCode, ...] = Field(default_factory=tuple)
    reason_code: str = Field(min_length=1, max_length=96)
    validation_status: CandidateValidationStatus
    rejection_code: str | None = None
    evidence_progress_revision: int = Field(ge=0)
    evidence_progress_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    budget_snapshot: BudgetSnapshot
    model_id: str | None = Field(default=None, max_length=128)
    prompt_policy_version: str | None = Field(default=None, max_length=128)
    runtime_version: str = SHARED_RUNTIME_VERSION
    component_versions: dict[str, str] = Field(default_factory=lambda: dict(SHARED_COMPONENT_VERSIONS))
    recorded_at: datetime


class RecoveryTraceRecord(_Contract):
    schema_version: Literal["v3a.trace.v1"] = TRACE_SCHEMA_VERSION
    trace_sequence: int = Field(ge=1)
    case_id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    recovery_id: str = Field(min_length=1, max_length=128)
    trigger_tool_call_id: str | None = None
    trigger_result_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    execution_status: str = Field(min_length=1, max_length=32)
    evidence_availability: str = Field(min_length=1, max_length=24)
    retryable: bool
    error_code: str | None = Field(default=None, max_length=96)
    evidence_progress_before_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_progress_after_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    route: RecoveryRoute
    reason_code: RecoveryReasonCode
    retry_identity_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    attempt_number: int | None = Field(default=None, ge=1, le=2)
    budget_snapshot: BudgetSnapshot
    runtime_version: str = SHARED_RUNTIME_VERSION
    component_versions: dict[str, str] = Field(default_factory=lambda: dict(SHARED_COMPONENT_VERSIONS))
    recorded_at: datetime


class StateTraceRecord(_Contract):
    schema_version: Literal["v3a.trace.v1"] = TRACE_SCHEMA_VERSION
    trace_sequence: int = Field(ge=1)
    case_id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    phase_from: TracePhase
    phase_to: TracePhase
    reason_code: str = Field(min_length=1, max_length=96)
    case_revision: int = Field(ge=0, default=0)
    run_revision: int = Field(ge=0, default=0)
    evidence_progress_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    pending_retry_identity: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    runtime_version: str = SHARED_RUNTIME_VERSION
    component_versions: dict[str, str] = Field(default_factory=lambda: dict(SHARED_COMPONENT_VERSIONS))
    persisted_at: datetime


def decision_fingerprint(
    *,
    action: ObservationAction | str,
    tool_name: str | None,
    canonical_arguments: Mapping[str, Any],
    addresses: Iterable[EvidenceRequirementCode | str],
    evidence_progress_hash: str,
) -> str:
    value = {
        "action": str(action),
        "tool_name": tool_name,
        "canonical_arguments": dict(canonical_arguments),
        "addresses": [str(item) for item in addresses],
        "evidence_progress_hash": evidence_progress_hash,
    }
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


TOOL_TO_REQUIREMENT: dict[str, EvidenceRequirementCode] = {
    "get_order_context": EvidenceRequirementCode.ORDER_STATUS,
    "get_logistics_timeline": EvidenceRequirementCode.TRACKING_TIMELINE,
    "get_delivery_proof": EvidenceRequirementCode.DELIVERY_PROOF,
    "search_after_sales_policy": EvidenceRequirementCode.POLICY_APPLICABILITY,
    "get_existing_logistics_tickets": EvidenceRequirementCode.ACTIVE_TICKET_STATUS,
    "get_carrier_service_alerts": EvidenceRequirementCode.CARRIER_ALERT_CONTEXT,
}
REQUIREMENT_TO_TOOL: dict[EvidenceRequirementCode, str] = {
    requirement: tool for tool, requirement in TOOL_TO_REQUIREMENT.items()
}

_REQUIRED_BY_ISSUE: dict[IssueType, tuple[EvidenceRequirementCode, ...]] = {
    IssueType.SIGNED_NOT_RECEIVED: (
        EvidenceRequirementCode.ORDER_STATUS,
        EvidenceRequirementCode.TRACKING_TIMELINE,
        EvidenceRequirementCode.DELIVERY_PROOF,
        EvidenceRequirementCode.POLICY_APPLICABILITY,
        EvidenceRequirementCode.ACTIVE_TICKET_STATUS,
    ),
    IssueType.STALLED_TRACKING: (
        EvidenceRequirementCode.ORDER_STATUS,
        EvidenceRequirementCode.TRACKING_TIMELINE,
        EvidenceRequirementCode.POLICY_APPLICABILITY,
        EvidenceRequirementCode.ACTIVE_TICKET_STATUS,
    ),
}


def requirement_registry(issue_type: IssueType) -> dict[EvidenceRequirementCode, RequirementApplicability]:
    required = set(_REQUIRED_BY_ISSUE[issue_type])
    registry = {
        code: RequirementApplicability.REQUIRED if code in required else RequirementApplicability.NOT_REQUIRED
        for code in EvidenceRequirementCode
    }
    if issue_type is IssueType.STALLED_TRACKING:
        registry[EvidenceRequirementCode.CARRIER_ALERT_CONTEXT] = RequirementApplicability.OPTIONAL
    return registry


def required_observation_tools(issue_type: IssueType) -> frozenset[str]:
    """Return the shared required-read projection consumed by Gate wiring."""

    registry = requirement_registry(issue_type)
    return frozenset(
        REQUIREMENT_TO_TOOL[code]
        for code, applicability in registry.items()
        if applicability is RequirementApplicability.REQUIRED
    )


def _empty_requirement(code: EvidenceRequirementCode, issue_type: IssueType) -> RequirementProgress:
    return RequirementProgress(
        applicability=requirement_registry(issue_type)[code],
        status=(
            EvidenceProgressStatus.MISSING
            if requirement_registry(issue_type)[code] is not RequirementApplicability.NOT_REQUIRED
            else EvidenceProgressStatus.MISSING
        ),
    )


def _snapshot_payload(snapshot: EvidenceProgressSnapshot) -> dict[str, Any]:
    return {
        "schema_version": snapshot.schema_version,
        "case_id": snapshot.case_id,
        "run_id": snapshot.run_id,
        "canonical_issue_type": snapshot.canonical_issue_type.value,
        "revision": snapshot.revision,
        "requirements": {
            code.value if isinstance(code, EvidenceRequirementCode) else str(code): value.model_dump(mode="json")
            for code, value in sorted(snapshot.requirements.items(), key=lambda item: str(item[0]))
        },
        "gate_readiness": snapshot.gate_readiness.value,
        "missing_required_codes": [code.value for code in snapshot.missing_required_codes],
        "terminal_trigger_codes": list(snapshot.terminal_trigger_codes),
        "last_actual_tool_call_id": snapshot.last_actual_tool_call_id,
    }


def progress_snapshot_hash(snapshot: EvidenceProgressSnapshot) -> str:
    return hashlib.sha256(_canonical_json(_snapshot_payload(snapshot)).encode("utf-8")).hexdigest()


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    return value.value if isinstance(value, StrEnum) else str(value)


def _parse_tool_result(raw: Any) -> ToolResult[Any] | None:
    if raw is None:
        return None
    if isinstance(raw, ToolResult):
        return raw
    try:
        return ToolResult[Any].model_validate(raw)
    except Exception:
        return None


def _evidence_refs_for(refs: Sequence[Any], call_id: str, result_hash: str | None) -> list[EvidenceRef]:
    result: list[EvidenceRef] = []
    for raw in refs:
        try:
            ref = raw if isinstance(raw, EvidenceRef) else EvidenceRef.model_validate(raw)
        except Exception:
            continue
        if ref.tool_call_id == call_id and (result_hash is None or ref.result_hash == result_hash):
            result.append(ref)
    return result


class EvidenceProgressReducer:
    """Rebuild progress exclusively from canonical ToolCall/result/ref history."""

    def __init__(self, *, registry_version: str = REQUIREMENT_REGISTRY_VERSION) -> None:
        if registry_version != REQUIREMENT_REGISTRY_VERSION:
            raise ValueError("unsupported requirement registry version")
        self.registry_version = registry_version

    def initial(
        self,
        *,
        case_id: str,
        run_id: str,
        canonical_issue_type: IssueType,
        rebuilt_at: datetime | None = None,
    ) -> EvidenceProgressSnapshot:
        requirements = {
            code: _empty_requirement(code, canonical_issue_type) for code in EvidenceRequirementCode
        }
        return self._build(
            case_id=case_id,
            run_id=run_id,
            canonical_issue_type=canonical_issue_type,
            revision=0,
            requirements=requirements,
            terminal_trigger_codes=(),
            last_actual_tool_call_id=None,
            rebuilt_at=rebuilt_at or _now(),
        )

    def rebuild(
        self,
        *,
        case_id: str,
        run_id: str,
        canonical_issue_type: IssueType,
        tool_calls: Sequence[Any],
        evidence_refs: Sequence[Any] = (),
        pending_retry: RetryDirective | None = None,
        rebuilt_at: datetime | None = None,
    ) -> EvidenceProgressSnapshot:
        registry = requirement_registry(canonical_issue_type)
        states = {code: _empty_requirement(code, canonical_issue_type) for code in EvidenceRequirementCode}
        terminal: list[str] = []
        last_actual: str | None = None
        retry_origin_seen = False
        retry_attempt_seen = False
        # Persisted ORM rows carry ``requested_at``; lightweight replay tests
        # may provide an already ordered list without that field.  Never let
        # generated call IDs silently reorder a history whose order is part of
        # retry adjacency.
        if all(_field(call, "requested_at") is not None for call in tool_calls):
            ordered = sorted(
                tool_calls,
                key=lambda call: (
                    str(_field(call, "requested_at", "")),
                    str(_field(call, "tool_call_id", "")),
                ),
            )
        else:
            ordered = list(tool_calls)
        for call in ordered:
            row_case_id = _field(call, "case_id")
            row_run_id = _field(call, "run_id")
            if (row_case_id is not None and str(row_case_id) != case_id) or (
                row_run_id is not None and str(row_run_id) != run_id
            ):
                terminal.append("TOOL_CALL_SCOPE_MISMATCH")
                continue
            tool_name = _as_str(_field(call, "tool_name"))
            code = TOOL_TO_REQUIREMENT.get(tool_name or "")
            if code is None:
                terminal.append("UNKNOWN_TOOL")
                continue
            call_id = str(_field(call, "tool_call_id", ""))
            result_hash = _field(call, "result_hash")
            result = _parse_tool_result(_field(call, "result_envelope"))
            execution_status = _as_str(_field(call, "execution_status"))
            availability = _as_str(_field(call, "evidence_availability"))
            source_version = _field(call, "source_version")
            attempt = int(_field(call, "attempt_number", 1) or 1)
            actual = bool(_field(call, "actual_execution", False))
            if actual:
                last_actual = call_id or last_actual
            current = states[code]
            if registry[code] is RequirementApplicability.NOT_REQUIRED:
                terminal.append(f"IRRELEVANT_TOOL:{tool_name}")
                continue
            matching_refs = _evidence_refs_for(evidence_refs, call_id, result_hash)
            if pending_retry is not None and pending_retry.retry_of_tool_call_id == call_id:
                retry_origin_seen = True
                raw_args = _field(call, "normalized_args", {})
                args_match = isinstance(raw_args, Mapping) and dict(raw_args) == pending_retry.canonical_arguments
                if (
                    not actual
                    or attempt != 1
                    or tool_name != pending_retry.tool_name
                    or not args_match
                    or source_version != pending_retry.source_version
                ):
                    states[code] = current.model_copy(
                        update={"status": EvidenceProgressStatus.CONFLICT}
                    )
                    terminal.append("RETRY_IDENTITY_CONFLICT")
                    continue
            elif pending_retry is not None and retry_origin_seen and not retry_attempt_seen:
                # Once a pending failure has been observed, the next actual
                # execution must be its exact attempt two.  A different
                # observation, tool, or planning branch would make the
                # replay ambiguous and therefore fails closed.
                if bool(_field(call, "actual_execution", False)):
                    raw_args = _field(call, "normalized_args", {})
                    if (
                        int(_field(call, "attempt_number", 1) or 1) != 2
                        or tool_name != pending_retry.tool_name
                        or not isinstance(raw_args, Mapping)
                        or dict(raw_args) != pending_retry.canonical_arguments
                        or source_version != pending_retry.source_version
                    ):
                        states[code] = current.model_copy(
                            update={"status": EvidenceProgressStatus.CONFLICT}
                        )
                        terminal.append("RETRY_IDENTITY_CONFLICT")
                        continue
                    retry_attempt_seen = True
            if execution_status == ExecutionStatus.SUCCESS.value and availability in {
                EvidenceAvailability.PRESENT.value,
                EvidenceAvailability.ABSENT.value,
            }:
                if result is None or result.result_hash != result_hash:
                    states[code] = current.model_copy(update={"status": EvidenceProgressStatus.CONFLICT})
                    terminal.append("RESULT_HASH_MISMATCH")
                    continue
                if not matching_refs:
                    states[code] = current.model_copy(update={"status": EvidenceProgressStatus.CONFLICT})
                    terminal.append("MISSING_EVIDENCE_REF")
                    continue
                # Two successful observations at the same authoritative source
                # revision are compatible only when their result identity agrees.
                if (
                    current.status
                    in {
                        EvidenceProgressStatus.SATISFIED_PRESENT,
                        EvidenceProgressStatus.SATISFIED_ABSENT,
                    }
                    and current.result_hashes
                    and result_hash not in current.result_hashes
                ):
                    states[code] = current.model_copy(update={"status": EvidenceProgressStatus.CONFLICT})
                    terminal.append("CONTRADICTORY_SUCCESS")
                    continue
                if (
                    current.status
                    in {
                        EvidenceProgressStatus.SATISFIED_PRESENT,
                        EvidenceProgressStatus.SATISFIED_ABSENT,
                    }
                    and current.source_versions
                    and source_version not in current.source_versions
                ):
                    states[code] = current.model_copy(update={"status": EvidenceProgressStatus.CONFLICT})
                    terminal.append("SOURCE_REVISION_CONFLICT")
                    continue
                status = (
                    EvidenceProgressStatus.SATISFIED_PRESENT
                    if availability == EvidenceAvailability.PRESENT.value
                    else EvidenceProgressStatus.SATISFIED_ABSENT
                )
                states[code] = self._merge(
                    current,
                    status=status,
                    call_id=call_id,
                    result_hash=str(result_hash),
                    source_version=str(source_version) if source_version is not None else None,
                    ref_ids=[f"{ref.tool_call_id}:{ref.source_query_id}:{ref.source_record_id or ''}" for ref in matching_refs],
                )
            elif availability == EvidenceAvailability.UNAVAILABLE.value and execution_status in {
                ExecutionStatus.RETRYABLE_ERROR.value,
                ExecutionStatus.NON_RETRYABLE_ERROR.value,
            }:
                valid_retry = (
                    pending_retry
                    if pending_retry is not None and pending_retry.retry_of_tool_call_id == call_id
                    else None
                )
                if valid_retry is not None and attempt == 1:
                    status = EvidenceProgressStatus.RETRY_PENDING
                elif attempt >= 2 or execution_status == ExecutionStatus.NON_RETRYABLE_ERROR.value:
                    status = EvidenceProgressStatus.UNAVAILABLE_FINAL
                else:
                    status = EvidenceProgressStatus.UNAVAILABLE_FINAL
                states[code] = self._merge(
                    current,
                    status=status,
                    call_id=call_id,
                    result_hash=str(result_hash) if result_hash else None,
                    source_version=str(source_version) if source_version is not None else None,
                    ref_ids=[],
                )
            else:
                states[code] = current.model_copy(update={"status": EvidenceProgressStatus.CONFLICT})
                terminal.append("MALFORMED_TOOL_RESULT")

        if pending_retry is not None and not retry_origin_seen:
            terminal.append("RETRY_ORIGIN_MISSING")

        revision = sum(
            1
            for call in ordered
            if bool(_field(call, "actual_execution", False))
            and _field(call, "execution_status") is not None
        )
        return self._build(
            case_id=case_id,
            run_id=run_id,
            canonical_issue_type=canonical_issue_type,
            revision=revision,
            requirements=states,
            terminal_trigger_codes=tuple(dict.fromkeys(terminal)),
            last_actual_tool_call_id=last_actual,
            rebuilt_at=rebuilt_at or _now(),
        )

    # ``reduce`` is an intentional alias used by callers that process an
    # append-only history incrementally.  Rebuilding from the complete history
    # keeps the hash independent from orchestration/checkpoint state.
    reduce = rebuild

    @staticmethod
    def _merge(
        current: RequirementProgress,
        *,
        status: EvidenceProgressStatus,
        call_id: str,
        result_hash: str | None,
        source_version: str | None,
        ref_ids: Sequence[str],
    ) -> RequirementProgress:
        def add(values: tuple[str, ...], item: str | None) -> tuple[str, ...]:
            if item is None or item in values:
                return values
            return (*values, item)

        return current.model_copy(
            update={
                "status": status,
                "supporting_tool_call_ids": add(current.supporting_tool_call_ids, call_id),
                "evidence_ref_ids": tuple(dict.fromkeys((*current.evidence_ref_ids, *ref_ids))),
                "result_hashes": add(current.result_hashes, result_hash),
                "source_versions": add(current.source_versions, source_version),
            }
        )

    @staticmethod
    def _build(
        *,
        case_id: str,
        run_id: str,
        canonical_issue_type: IssueType,
        revision: int,
        requirements: dict[EvidenceRequirementCode, RequirementProgress],
        terminal_trigger_codes: Sequence[str],
        last_actual_tool_call_id: str | None,
        rebuilt_at: datetime,
    ) -> EvidenceProgressSnapshot:
        missing = tuple(
            code
            for code in _REQUIRED_BY_ISSUE[canonical_issue_type]
            if requirements[code].status
            not in {
                EvidenceProgressStatus.SATISFIED_PRESENT,
                EvidenceProgressStatus.SATISFIED_ABSENT,
            }
        )
        conflicts = any(state.status is EvidenceProgressStatus.CONFLICT for state in requirements.values())
        readiness = GateReadiness.EVALUABLE if not missing and not conflicts else GateReadiness.NOT_EVALUABLE
        provisional = EvidenceProgressSnapshot.model_construct(
            schema_version=PROGRESS_SCHEMA_VERSION,
            case_id=case_id,
            run_id=run_id,
            canonical_issue_type=canonical_issue_type,
            revision=revision,
            requirements=requirements,
            gate_readiness=readiness,
            missing_required_codes=missing,
            terminal_trigger_codes=tuple(terminal_trigger_codes),
            last_actual_tool_call_id=last_actual_tool_call_id,
            snapshot_hash="0" * 64,
            rebuilt_at=rebuilt_at,
        )
        digest = hashlib.sha256(_canonical_json(_snapshot_payload(provisional)).encode("utf-8")).hexdigest()
        return provisional.model_copy(update={"snapshot_hash": digest})


class ObservationValidationError(ValueError):
    """A fail-closed candidate rejection with a stable reason code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class CandidateValidationResult(_Contract):
    status: CandidateValidationStatus
    observation: NextObservation | None = None
    rejection_code: str | None = None


class ObservationValidator:
    """Pure deterministic candidate-to-observation validator."""

    def __init__(self, *, allowed_tools: Iterable[str] = READ_TOOL_NAMES) -> None:
        self.allowed_tools = frozenset(allowed_tools)

    def validate(
        self,
        candidate: NextObservationCandidate | Mapping[str, Any],
        *,
        context: DecisionContext,
        selector_kind: SelectorKind,
        trusted: TrustedToolContext | None = None,
        pending_retry: RetryDirective | None = None,
        gate_ready: bool | None = None,
    ) -> CandidateValidationResult:
        try:
            parsed = candidate if isinstance(candidate, NextObservationCandidate) else NextObservationCandidate.model_validate(candidate)
        except Exception:
            return CandidateValidationResult(status=CandidateValidationStatus.REJECTED, rejection_code="INVALID_CANDIDATE_SCHEMA")
        progress = context.evidence_progress
        if trusted is not None and (
            context.case_id != trusted.case_id
            or context.run_id != trusted.run_id
            or context.authorized_order_id != trusted.authorized_order_id
            or context.canonical_issue_type is not trusted.canonical_issue_type
        ):
            return CandidateValidationResult(
                status=CandidateValidationStatus.REJECTED,
                rejection_code="ACTIVE_CASE_RUN_MISMATCH",
            )
        if gate_ready is None:
            gate_ready = progress.gate_readiness is GateReadiness.EVALUABLE
        if pending_retry is not None:
            return CandidateValidationResult(
                status=CandidateValidationStatus.REJECTED,
                rejection_code="PENDING_EXACT_RETRY",
            )
        budget = context.remaining_budget
        if budget.case_planning_turns >= 16 or budget.run_planning_turns >= 8:
            return CandidateValidationResult(
                status=CandidateValidationStatus.REJECTED,
                rejection_code="PLANNING_BUDGET_EXCEEDED",
            )
        if parsed.action is ObservationAction.FINISH:
            if not gate_ready:
                return CandidateValidationResult(status=CandidateValidationStatus.REJECTED, rejection_code="PREMATURE_FINISH")
            tool_name: str | None = None
            arguments: dict[str, Any] = {}
        else:
            tool_name = parsed.tool_name
            if tool_name not in self.allowed_tools or tool_name not in context.allowed_tools:
                return CandidateValidationResult(status=CandidateValidationStatus.REJECTED, rejection_code="TOOL_NOT_ALLOWED")
            expected_keys = {"order_id", "issue_type"} if tool_name in {
                "search_after_sales_policy",
                "get_existing_logistics_tickets",
            } else {"order_id"}
            if set(parsed.arguments) != expected_keys:
                return CandidateValidationResult(status=CandidateValidationStatus.REJECTED, rejection_code="INVALID_TOOL_ARGUMENTS")
            if parsed.arguments.get("order_id") != context.authorized_order_id:
                return CandidateValidationResult(status=CandidateValidationStatus.REJECTED, rejection_code="TOOL_SCOPE_MISMATCH")
            if "issue_type" in expected_keys and parsed.arguments.get("issue_type") != context.canonical_issue_type.value:
                return CandidateValidationResult(status=CandidateValidationStatus.REJECTED, rejection_code="TOOL_SCOPE_MISMATCH")
            if tool_name == "get_delivery_proof" and context.canonical_issue_type is not IssueType.SIGNED_NOT_RECEIVED:
                return CandidateValidationResult(status=CandidateValidationStatus.REJECTED, rejection_code="TOOL_NOT_RELEVANT_TO_ISSUE")
            if tool_name == "get_carrier_service_alerts" and context.canonical_issue_type is not IssueType.STALLED_TRACKING:
                return CandidateValidationResult(status=CandidateValidationStatus.REJECTED, rejection_code="TOOL_NOT_RELEVANT_TO_ISSUE")
            code = TOOL_TO_REQUIREMENT[tool_name]
            if code not in parsed.addresses:
                return CandidateValidationResult(
                    status=CandidateValidationStatus.REJECTED,
                    rejection_code="MISSING_EVIDENCE_ADDRESS",
                )
            requirement = progress.requirements[code]
            if requirement.status in {
                EvidenceProgressStatus.SATISFIED_PRESENT,
                EvidenceProgressStatus.SATISFIED_ABSENT,
            }:
                return CandidateValidationResult(status=CandidateValidationStatus.REJECTED, rejection_code="DUPLICATE_COMPLETED_READ")
            if requirement.status is EvidenceProgressStatus.UNAVAILABLE_FINAL:
                return CandidateValidationResult(
                    status=CandidateValidationStatus.REJECTED,
                    rejection_code="RETRY_EXHAUSTED",
                )
            if requirement.status is EvidenceProgressStatus.CONFLICT:
                return CandidateValidationResult(
                    status=CandidateValidationStatus.REJECTED,
                    rejection_code="EVIDENCE_PROGRESS_CONFLICT",
                )
            arguments = dict(parsed.arguments)
        digest = canonical_arguments_hash(arguments)
        fp = decision_fingerprint(
            action=parsed.action,
            tool_name=tool_name,
            canonical_arguments=arguments,
            addresses=parsed.addresses,
            evidence_progress_hash=progress.snapshot_hash,
        )
        if fp in context.prior_decision_fingerprints:
            return CandidateValidationResult(status=CandidateValidationStatus.REJECTED, rejection_code="STUCK_REPEATED_DECISION")
        observation = NextObservation(
            decision_id=f"dec_{uuid4().hex}",
            selector_kind=selector_kind,
            case_id=context.case_id,
            run_id=context.run_id,
            planning_turn=budget.run_planning_turns + 1,
            action=parsed.action,
            tool_name=tool_name,
            canonical_arguments=arguments,
            canonical_arguments_hash=digest,
            addresses=parsed.addresses,
            reason_code=parsed.reason_code,
            evidence_progress_revision=progress.revision,
            evidence_progress_hash=progress.snapshot_hash,
            decision_fingerprint=fp,
            validated_at=_now(),
        )
        return CandidateValidationResult(status=CandidateValidationStatus.ACCEPTED, observation=observation)

    def validate_or_raise(
        self,
        candidate: NextObservationCandidate | Mapping[str, Any],
        *,
        context: DecisionContext,
        selector_kind: SelectorKind,
        trusted: TrustedToolContext | None = None,
        pending_retry: RetryDirective | None = None,
        gate_ready: bool | None = None,
    ) -> NextObservation:
        result = self.validate(
            candidate,
            context=context,
            selector_kind=selector_kind,
            trusted=trusted,
            pending_retry=pending_retry,
            gate_ready=gate_ready,
        )
        if result.observation is None:
            raise ObservationValidationError(result.rejection_code or "INVALID_OBSERVATION")
        return result.observation


class ObservationRouter:
    """The only deterministic route precedence after an observation."""

    def route(
        self,
        *,
        case_id: str,
        run_id: str,
        progress_before: EvidenceProgressSnapshot,
        progress_after: EvidenceProgressSnapshot,
        budget: BudgetSnapshot,
        trigger_tool_call_id: str | None = None,
        trigger_result_hash: str | None = None,
        trigger_tool_name: str | None = None,
        trigger_arguments: Mapping[str, Any] | None = None,
        trigger_attempt_number: int = 1,
        result: ToolResult[Any] | None = None,
        pending_retry: RetryDirective | None = None,
        source_version: str | None = None,
        same_progress_selector_turns: int = 0,
    ) -> RecoveryDecision:
        reason: RecoveryReasonCode
        route: RecoveryRoute
        directive = pending_retry
        if (
            progress_before.case_id != case_id
            or progress_after.case_id != case_id
            or progress_before.run_id != run_id
            or progress_after.run_id != run_id
            or progress_before.canonical_issue_type is not progress_after.canonical_issue_type
        ):
            route, reason = RecoveryRoute.SAFE_STOP, RecoveryReasonCode.INTEGRITY_CONFLICT
            directive = None
        elif progress_after.terminal_trigger_codes:
            route, reason = RecoveryRoute.SAFE_STOP, RecoveryReasonCode.INTEGRITY_CONFLICT
            directive = None
        elif budget.case_planning_turns >= 16 or budget.run_planning_turns >= 8 or budget.actual_read_tool_executions >= 6:
            route, reason = RecoveryRoute.SAFE_STOP, RecoveryReasonCode.BUDGET_EXHAUSTED
            directive = None
        elif pending_retry is not None and source_version != pending_retry.source_version:
            route, reason = RecoveryRoute.SAFE_STOP, RecoveryReasonCode.SOURCE_REVISION_CHANGED_DURING_RETRY
            directive = None
        elif pending_retry is not None or (
            result is not None
            and result.execution_status is ExecutionStatus.RETRYABLE_ERROR
            and result.evidence_availability is EvidenceAvailability.UNAVAILABLE
            and result.retryable
            and trigger_attempt_number <= 1
            and trigger_tool_call_id is not None
            and trigger_tool_name is not None
            and source_version is not None
        ):
            route, reason = RecoveryRoute.RETRY_EXACT, RecoveryReasonCode.RETRYABLE_TOOL_FAILURE
            directive = directive or RetryDirective(
                retry_of_tool_call_id=trigger_tool_call_id,
                tool_name=trigger_tool_name,
                canonical_arguments=dict(trigger_arguments or {}),
                canonical_arguments_hash=canonical_arguments_hash(dict(trigger_arguments or {})),
                source_version=source_version,
                issued_from_progress_hash=progress_after.snapshot_hash,
            )
        elif progress_after.gate_readiness is GateReadiness.EVALUABLE:
            route, reason = RecoveryRoute.FINALIZE, RecoveryReasonCode.GATE_READY
            directive = None
        elif same_progress_selector_turns >= 2:
            route, reason = RecoveryRoute.SAFE_STOP, RecoveryReasonCode.STUCK_NO_EVIDENCE_PROGRESS
            directive = None
        else:
            route, reason = RecoveryRoute.REPLAN, RecoveryReasonCode.REPLAN_REQUIRED
            directive = None
        return RecoveryDecision(
            recovery_id=f"rec_{uuid4().hex}",
            case_id=case_id,
            run_id=run_id,
            route=route,
            reason_code=reason,
            trigger_tool_call_id=trigger_tool_call_id,
            trigger_result_hash=trigger_result_hash,
            evidence_progress_before_hash=progress_before.snapshot_hash,
            evidence_progress_after_hash=progress_after.snapshot_hash,
            retry_directive=directive,
            budget_snapshot=budget,
            decided_at=_now(),
        )


def issue_exact_retry(
    *,
    tool_call_id: str,
    tool_name: str,
    canonical_arguments: Mapping[str, Any],
    source_version: str,
    execution_status: ExecutionStatus | str,
    evidence_availability: EvidenceAvailability | str,
    retryable: bool,
    attempt_number: int,
    progress_hash: str,
) -> RetryDirective | None:
    """Issue exactly one retry identity for a first transient unavailable read."""

    if (
        tool_name not in READ_TOOL_NAMES
        or not source_version
        or _as_str(execution_status) != ExecutionStatus.RETRYABLE_ERROR.value
        or _as_str(evidence_availability) != EvidenceAvailability.UNAVAILABLE.value
        or not retryable
        or attempt_number != 1
    ):
        return None
    args = dict(canonical_arguments)
    return RetryDirective(
        retry_of_tool_call_id=tool_call_id,
        tool_name=tool_name,
        canonical_arguments=args,
        canonical_arguments_hash=canonical_arguments_hash(args),
        source_version=source_version,
        issued_from_progress_hash=progress_hash,
    )


def validate_exact_retry(
    directive: RetryDirective,
    *,
    tool_name: str,
    canonical_arguments: Mapping[str, Any],
    source_version: str,
    attempt_number: int,
) -> None:
    """Fail closed if a retry is not adjacent and byte-identical in identity."""

    args = dict(canonical_arguments)
    if attempt_number != directive.next_attempt_number:
        raise ObservationValidationError("RETRY_ATTEMPT_NUMBER_MISMATCH")
    if tool_name != directive.tool_name:
        raise ObservationValidationError("RETRY_TOOL_MISMATCH")
    if canonical_arguments_hash(args) != directive.canonical_arguments_hash or args != directive.canonical_arguments:
        raise ObservationValidationError("RETRY_ARGUMENTS_MISMATCH")
    if source_version != directive.source_version:
        raise ObservationValidationError("SOURCE_REVISION_CHANGED_DURING_RETRY")


class ExactRetryController:
    """Stateful helper that permits only one adjacent attempt-two execution."""

    def __init__(self, directive: RetryDirective) -> None:
        self.directive = directive
        self.executed = False

    def validate_next(
        self,
        *,
        tool_name: str,
        canonical_arguments: Mapping[str, Any],
        source_version: str,
        attempt_number: int,
    ) -> None:
        if self.executed:
            raise ObservationValidationError("TOOL_RETRY_EXHAUSTED")
        validate_exact_retry(
            self.directive,
            tool_name=tool_name,
            canonical_arguments=canonical_arguments,
            source_version=source_version,
            attempt_number=attempt_number,
        )
        self.executed = True


class GuardState(_Contract):
    repeated_fingerprint_count: int = Field(default=0, ge=0)
    unchanged_selector_turns: int = Field(default=0, ge=0)
    corrective_turns_used: int = Field(default=0, ge=0, le=1)


class GuardController:
    """OD-01 guards independent of model wording."""

    def __init__(self) -> None:
        self._last_fingerprint: str | None = None
        self._last_progress_hash: str | None = None
        self.state = GuardState()

    def observe_decision(self, fingerprint: str, progress_hash: str) -> RecoveryReasonCode | None:
        if fingerprint == self._last_fingerprint and progress_hash == self._last_progress_hash:
            self.state = self.state.model_copy(
                update={"repeated_fingerprint_count": self.state.repeated_fingerprint_count + 1}
            )
            if self.state.repeated_fingerprint_count >= 2:
                return RecoveryReasonCode.STUCK_REPEATED_DECISION
        else:
            self.state = self.state.model_copy(update={"repeated_fingerprint_count": 0})
        self._last_fingerprint = fingerprint
        self._last_progress_hash = progress_hash
        return None

    def observe_selector_turn(self, progress_before_hash: str, progress_after_hash: str) -> RecoveryReasonCode | None:
        if progress_before_hash == progress_after_hash:
            self.state = self.state.model_copy(update={"unchanged_selector_turns": self.state.unchanged_selector_turns + 1})
        else:
            self.state = self.state.model_copy(update={"unchanged_selector_turns": 0})
        if self.state.unchanged_selector_turns >= 2:
            return RecoveryReasonCode.STUCK_NO_EVIDENCE_PROGRESS
        return None

    def allow_correction(self) -> bool:
        if self.state.corrective_turns_used >= 1:
            return False
        self.state = self.state.model_copy(update={"corrective_turns_used": 1})
        return True


def build_decision_context(
    *,
    trusted: TrustedToolContext,
    customer_message: str,
    progress: EvidenceProgressSnapshot,
    budget: ToolBudgetSnapshot | BudgetSnapshot,
    latest_observation: ObservationSummary | None = None,
    prior_decision_fingerprints: Sequence[str] = (),
    prompt_policy_version: str | None = None,
    case_fact_snapshot: dict[str, Any] | None = None,
) -> DecisionContext:
    snapshot = budget if isinstance(budget, BudgetSnapshot) else BudgetSnapshot.from_tool_budget(budget)
    return DecisionContext(
        case_id=trusted.case_id,
        run_id=trusted.run_id,
        canonical_issue_type=trusted.canonical_issue_type,
        authorized_order_id=trusted.authorized_order_id,
        customer_message=customer_message,
        case_fact_snapshot=case_fact_snapshot,
        evidence_progress=progress,
        latest_observation=latest_observation,
        allowed_tools=tuple(sorted(READ_TOOL_NAMES)),
        remaining_budget=snapshot,
        prior_decision_fingerprints=tuple(prior_decision_fingerprints),
        prompt_policy_version=prompt_policy_version,
    )


async def select_next_observation(
    selector: ObservationSelector,
    context: DecisionContext,
) -> NextObservationCandidate:
    """Invoke either selector through the one typed boundary."""

    candidate = await selector.select_next_observation(context)
    return candidate if isinstance(candidate, NextObservationCandidate) else NextObservationCandidate.model_validate(candidate)


# ``DecisionContext`` refers to the progress contract declared below it in the
# source file.  Resolve the forward reference once all V3 contracts exist.
DecisionContext.model_rebuild()


__all__ = [
    "BudgetSnapshot",
    "CandidateValidationResult",
    "CandidateValidationStatus",
    "DecisionContext",
    "DecisionTraceRecord",
    "EvidenceProgressReducer",
    "EvidenceProgressSnapshot",
    "EvidenceProgressStatus",
    "EvidenceRequirementCode",
    "GateReadiness",
    "GuardController",
    "ExactRetryController",
    "NextObservation",
    "NextObservationCandidate",
    "ObservationAction",
    "ObservationReasonCode",
    "ObservationRouter",
    "ObservationSummary",
    "ObservationSelector",
    "ObservationValidationError",
    "ObservationValidator",
    "RecoveryDecision",
    "RecoveryReasonCode",
    "RecoveryRoute",
    "RecoveryTraceRecord",
    "REQUIREMENT_TO_TOOL",
    "RequirementApplicability",
    "RequirementProgress",
    "RetryDirective",
    "SelectorKind",
    "StateTraceRecord",
    "SHARED_COMPONENT_VERSIONS",
    "SHARED_RUNTIME_VERSION",
    "TracePhase",
    "TOOL_TO_REQUIREMENT",
    "build_decision_context",
    "canonical_arguments_hash",
    "decision_fingerprint",
    "issue_exact_retry",
    "progress_snapshot_hash",
    "requirement_registry",
    "required_observation_tools",
    "select_next_observation",
    "validate_exact_retry",
]

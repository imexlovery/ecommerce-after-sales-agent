"""Bounded, non-measurement Live diagnostics for the V3 selector boundary."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from os import fsync
from pathlib import Path
from typing import Any, Final, Literal

from langchain_core.callbacks import get_usage_metadata_callback
from langchain_core.messages import AIMessage
from pydantic import BaseModel, ConfigDict, Field

from after_sales_agent.agents.models import (
    AgentObservationSelector,
    build_investigation_model,
    parse_structured_selector_response,
)
from after_sales_agent.agents.prompts import INVESTIGATION_SELECTOR_PROMPT_VERSION
from after_sales_agent.agents.tool_bindings import READ_TOOLS
from after_sales_agent.application.adaptive_core import (
    CANDIDATE_SCHEMA_VERSION,
    BudgetSnapshot,
    EvidenceProgressReducer,
    GateReadiness,
    NextObservationCandidate,
    ObservationAction,
    ObservationRouter,
    ObservationValidator,
    RecoveryRoute,
    SelectorKind,
    build_decision_context,
)
from after_sales_agent.application.provider_budget import SelectorSchemaFailure
from after_sales_agent.config import LLMMode, Settings
from after_sales_agent.domain.models import TrustedToolContext
from after_sales_agent.domain.state import EvidenceAvailability, IssueType
from after_sales_agent.evals.v3.diagnostic_manifest import (
    DIAGNOSTIC_IDENTITY as MANIFEST_DIAGNOSTIC_IDENTITY,
)
from after_sales_agent.evals.v3.diagnostic_manifest import (
    DiagnosticManifestInput,
    diagnostic_manifest_path,
    load_diagnostic_manifest,
)
from after_sales_agent.evals.v3.real_runner import current_source_revision
from after_sales_agent.tools.contracts import ToolResult
from after_sales_agent.tools.service import READ_TOOL_NAMES

DIAGNOSTIC_IDENTITY: Final = MANIFEST_DIAGNOSTIC_IDENTITY
DIAGNOSTIC_LABEL: Final = "real_external_diagnostic_not_measurement"
DIAGNOSTIC_MAX_CALLS: Final = 24
DIAGNOSTIC_MAX_ATTEMPTS_PER_INPUT: Final = 2
DIAGNOSTIC_TIMEOUT_SECONDS: Final = 30.0
DIAGNOSTIC_MODEL: Final = "deepseek-v4-flash"
DIAGNOSTIC_ROOT_RELATIVE: Final = Path("var/v3/development-diagnostics")
_REPO_ROOT = Path(__file__).resolve().parents[4]
try:
    _DEFAULT_DIAGNOSTIC_INPUTS = load_diagnostic_manifest(_REPO_ROOT)[0].inputs
    _DIAGNOSTIC_INPUTS = tuple(item.input_id for item in _DEFAULT_DIAGNOSTIC_INPUTS)
except (OSError, ValueError):
    _DEFAULT_DIAGNOSTIC_INPUTS = ()
    _DIAGNOSTIC_INPUTS = ()


class V3DiagnosticEvent(BaseModel):
    """One safe append-only diagnostic event; provider payloads are excluded."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v3.diagnostic.event.v1"] = "v3.diagnostic.event.v1"
    diagnostic_identity: Literal["V3-DEV-DIAG-20260828-04"] = DIAGNOSTIC_IDENTITY
    diagnostic_input_id: str = Field(min_length=1, max_length=96)
    source_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    manifest_id: str | None = Field(default=None, max_length=96)
    manifest_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    selector_schema_version: str = Field(
        default=CANDIDATE_SCHEMA_VERSION, min_length=1, max_length=128
    )
    prompt_policy_version: str = Field(
        default=INVESTIGATION_SELECTOR_PROMPT_VERSION, min_length=1, max_length=128
    )
    event_type: Literal["manifest_bound", "admission", "completion", "boundary", "blocked"]
    attempt: int = Field(ge=0, le=DIAGNOSTIC_MAX_CALLS)
    status: Literal[
        "bound",
        "admitted",
        "completed",
        "schema_failure",
        "provider_error",
        "passed",
        "failed",
        "blocked",
    ]
    reason_code: str = Field(min_length=1, max_length=128)
    stage: str | None = Field(default=None, max_length=64)
    expected_action: Literal["call_tool", "finish"] | None = None
    expected_tool_name: str | None = Field(default=None, max_length=96)
    expected_evidence_requirement: str | None = Field(default=None, max_length=64)
    observed_action: Literal["call_tool", "finish"] | None = None
    observed_tool_name: str | None = Field(default=None, max_length=96)
    observed_evidence_requirement: str | None = Field(default=None, max_length=64)
    router_route: str | None = Field(default=None, max_length=32)
    selector_boundary_pass: bool | None = None
    structured_envelope: bool = False
    parsed_candidate: bool = False
    response_is_ai_message: bool = False
    tool_call_count: int = Field(default=0, ge=0, le=8)
    allowlisted_tool_name: bool | None = None
    candidate_action: str | None = Field(default=None, max_length=32)
    candidate_tool_name: str | None = Field(default=None, max_length=96)
    candidate_requirement_names: tuple[str, ...] = Field(default_factory=tuple, max_length=6)
    args_is_object: bool | None = None
    argument_field_names: tuple[str, ...] = Field(default_factory=tuple, max_length=2)
    server_message_rebuilt: bool = False
    server_tool_call_count: int = Field(default=0, ge=0, le=1)
    server_tool_name: str | None = Field(default=None, max_length=96)
    server_argument_field_names: tuple[str, ...] = Field(default_factory=tuple, max_length=2)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    recorded_at: datetime


class V3DiagnosticReport(BaseModel):
    """Diagnostic result explicitly separated from formal measurement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v3.diagnostic.report.v1"] = "v3.diagnostic.report.v1"
    diagnostic_identity: Literal["V3-DEV-DIAG-20260828-04"] = DIAGNOSTIC_IDENTITY
    status: Literal["passed", "failed", "blocked"]
    diagnostic_label: Literal["real_external_diagnostic_not_measurement"] = DIAGNOSTIC_LABEL
    live_mode: bool
    model_match: bool
    credential_present: bool
    source_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    manifest_id: str | None = Field(default=None, max_length=96)
    manifest_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    manifest_path: str = Field(min_length=1)
    selector_schema_version: str = CANDIDATE_SCHEMA_VERSION
    prompt_policy_version: str = INVESTIGATION_SELECTOR_PROMPT_VERSION
    diagnostic_input_ids: tuple[str, ...] = _DIAGNOSTIC_INPUTS
    passed_input_ids: tuple[str, ...] = Field(default_factory=tuple)
    input_count: int = Field(ge=0)
    provider_calls: int = Field(ge=0, le=DIAGNOSTIC_MAX_CALLS)
    server_message_rebuild_count: int = Field(default=0, ge=0)
    server_tool_call_rebuild_count: int = Field(default=0, ge=0)
    selector_schema_failure_count: int = Field(default=0, ge=0)
    multi_observation_rejection_count: int = Field(default=0, ge=0)
    reason_code: str = Field(min_length=1, max_length=128)
    ledger_path: str = Field(min_length=1)
    events: tuple[V3DiagnosticEvent, ...] = Field(default_factory=tuple)

    @property
    def all_inputs_passed(self) -> bool:
        return set(self.passed_input_ids) == set(self.diagnostic_input_ids)


class DiagnosticAuthorizationError(ValueError):
    """Raised when a caller attempts to use a non-authorized diagnostic identity."""


def _validate_diagnostic_identity(diagnostic_identity: str) -> None:
    if diagnostic_identity != DIAGNOSTIC_IDENTITY:
        raise DiagnosticAuthorizationError(
            "this task permits only the authorized diagnostic identity"
        )


def _diagnostic_path(project_root: Path, *, diagnostic_identity: str = DIAGNOSTIC_IDENTITY) -> Path:
    _validate_diagnostic_identity(diagnostic_identity)
    return (
        project_root.expanduser().resolve()
        / DIAGNOSTIC_ROOT_RELATIVE
        / diagnostic_identity
        / "diagnostics.jsonl"
    )


def _safe_usage(value: Any) -> dict[str, int]:
    if isinstance(value, Mapping):
        raw_response = value.get("raw")
        if raw_response is not None:
            value = raw_response
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        raw = value.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or int(raw) != raw:
            continue
        if raw < 0:
            continue
        result[key] = int(raw)
    return result


def _response_projection(response: Any) -> dict[str, Any]:
    """Project only the structured response shape needed at the boundary."""

    projection: dict[str, Any] = {
        "structured_envelope": isinstance(response, Mapping),
        "parsed_candidate": False,
        "response_is_ai_message": False,
        "tool_call_count": 0,
        "schema_reason_code": "SELECTOR_RESPONSE_NOT_STRUCTURED",
    }
    if not isinstance(response, Mapping):
        return projection
    if set(response) != {"raw", "parsed", "parsing_error"}:
        projection["schema_reason_code"] = "SELECTOR_STRUCTURED_RESPONSE_FIELDS_INVALID"
        return projection
    raw = response.get("raw")
    if not isinstance(raw, AIMessage):
        projection["schema_reason_code"] = "SELECTOR_RAW_RESPONSE_NOT_AI_MESSAGE"
        return projection
    projection["response_is_ai_message"] = True
    calls = raw.tool_calls
    if not isinstance(calls, list):
        projection["schema_reason_code"] = "SELECTOR_TOOL_CALLS_NOT_LIST"
        return projection
    projection["tool_call_count"] = len(calls)
    if getattr(raw, "invalid_tool_calls", ()):
        projection["schema_reason_code"] = "SELECTOR_INVALID_STRUCTURED_CALL"
        return projection
    if not calls:
        projection["schema_reason_code"] = "SELECTOR_EMPTY_STRUCTURED_OUTPUT"
        return projection
    if len(calls) != 1:
        projection["schema_reason_code"] = "SELECTOR_MULTIPLE_TOOL_CALLS"
        return projection
    call = calls[0]
    if not isinstance(call, Mapping):
        projection["schema_reason_code"] = "SELECTOR_STRUCTURED_CALL_NOT_OBJECT"
        return projection
    if call.get("name") != NextObservationCandidate.__name__:
        projection["schema_reason_code"] = "SELECTOR_STRUCTURED_SCHEMA_NAME_INVALID"
        return projection
    try:
        candidate = parse_structured_selector_response(response, NextObservationCandidate)
    except SelectorSchemaFailure as exc:
        projection["schema_reason_code"] = exc.reason_code
        return projection
    projection.update(
        {
            "parsed_candidate": True,
            "candidate_action": candidate.action.value,
            "candidate_tool_name": candidate.tool_name,
            "candidate_requirement_names": tuple(
                requirement.value for requirement in candidate.addresses
            ),
            "allowlisted_tool_name": (
                candidate.tool_name in READ_TOOL_NAMES if candidate.tool_name is not None else None
            ),
            "schema_reason_code": "SELECTOR_STRUCTURED_CANDIDATE",
        }
    )
    return projection


class _DiagnosticLedger:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.events: list[V3DiagnosticEvent] = []
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.events.append(V3DiagnosticEvent.model_validate_json(line))

    def append(self, event: V3DiagnosticEvent) -> None:
        self.events.append(event)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json() + "\n")
            handle.flush()
            fsync(handle.fileno())


@dataclass(frozen=True)
class _DiagnosticInput:
    input_id: str
    stage: str
    issue_type: IssueType
    completed_tools: tuple[str, ...]
    expected_action: Literal["call_tool", "finish"]
    expected_tool_name: str | None
    expected_evidence_requirement: str | None
    expected_route: RecoveryRoute
    context_note: str

    @classmethod
    def from_manifest_input(cls, item: DiagnosticManifestInput) -> _DiagnosticInput:
        return cls(
            input_id=item.input_id,
            stage=item.stage,
            issue_type=item.issue_type,
            completed_tools=item.completed_tools,
            expected_action=item.expected_action,
            expected_tool_name=item.expected_tool_name,
            expected_evidence_requirement=(
                item.expected_evidence_requirement.value
                if item.expected_evidence_requirement is not None
                else None
            ),
            expected_route=item.expected_route,
            context_note=item.context_note,
        )


def _diagnostic_specs(project_root: Path | None = None) -> tuple[_DiagnosticInput, ...]:
    manifest, _, _ = load_diagnostic_manifest(project_root or _REPO_ROOT)
    return tuple(_DiagnosticInput.from_manifest_input(item) for item in manifest.inputs)


class _DiagnosticInvocationObserver:
    def __init__(
        self,
        ledger: _DiagnosticLedger,
        *,
        diagnostic_input_id: str = "V3-DIAG-UNBOUND",
        source_revision: str | None = None,
        stage: str | None = None,
        expected_action: Literal["call_tool", "finish"] | None = None,
        expected_tool_name: str | None = None,
        expected_evidence_requirement: str | None = None,
    ) -> None:
        self._ledger = ledger
        self._diagnostic_input_id = diagnostic_input_id
        self._source_revision = source_revision
        self._stage = stage
        self._expected_action = expected_action
        self._expected_tool_name = expected_tool_name
        self._expected_evidence_requirement = expected_evidence_requirement
        self._attempts = sum(event.event_type == "admission" for event in ledger.events)

    def _event(self, **kwargs: Any) -> V3DiagnosticEvent:
        return V3DiagnosticEvent(
            diagnostic_input_id=self._diagnostic_input_id,
            source_revision=self._source_revision,
            stage=self._stage,
            expected_action=self._expected_action,
            expected_tool_name=self._expected_tool_name,
            expected_evidence_requirement=self._expected_evidence_requirement,
            **kwargs,
        )

    async def invoke(
        self,
        *,
        model: Any,
        messages: Sequence[Any],
        context: Any,
    ) -> Any:
        del context
        if self._attempts >= DIAGNOSTIC_MAX_CALLS:
            raise RuntimeError("diagnostic call ceiling reached")
        self._attempts += 1
        attempt = self._attempts
        self._ledger.append(
            self._event(
                event_type="admission",
                attempt=attempt,
                status="admitted",
                reason_code="DIAGNOSTIC_CALL_ADMITTED",
                recorded_at=datetime.now(UTC),
            )
        )
        callback: Any = None
        try:
            with get_usage_metadata_callback() as usage_callback:
                callback = usage_callback
                response = await asyncio.wait_for(
                    model.ainvoke(messages), timeout=DIAGNOSTIC_TIMEOUT_SECONDS
                )
                usage = _safe_usage(response)
                callback_usage = _safe_usage(
                    getattr(usage_callback, "usage_metadata", {})
                )
                for key, value in callback_usage.items():
                    usage.setdefault(key, value)
            projection = _response_projection(response)
            self._ledger.append(
                self._event(
                    event_type="completion",
                    attempt=attempt,
                    status="completed",
                    reason_code=str(projection["schema_reason_code"]),
                    structured_envelope=bool(projection["structured_envelope"]),
                    parsed_candidate=bool(projection["parsed_candidate"]),
                    response_is_ai_message=bool(projection["response_is_ai_message"]),
                    tool_call_count=int(projection["tool_call_count"]),
                    allowlisted_tool_name=projection.get("allowlisted_tool_name"),
                    candidate_action=projection.get("candidate_action"),
                    candidate_tool_name=projection.get("candidate_tool_name"),
                    candidate_requirement_names=tuple(
                        projection.get("candidate_requirement_names", ())
                    ),
                    input_tokens=usage.get("input_tokens"),
                    output_tokens=usage.get("output_tokens"),
                    total_tokens=usage.get("total_tokens"),
                    recorded_at=datetime.now(UTC),
                )
            )
            return response
        except TimeoutError:
            usage = _safe_usage(getattr(callback, "usage_metadata", {}))
            self._ledger.append(
                self._event(
                    event_type="completion",
                    attempt=attempt,
                    status="provider_error",
                    reason_code="DIAGNOSTIC_PROVIDER_TIMEOUT",
                    input_tokens=usage.get("input_tokens"),
                    output_tokens=usage.get("output_tokens"),
                    total_tokens=usage.get("total_tokens"),
                    recorded_at=datetime.now(UTC),
                )
            )
            raise
        except Exception:
            usage = _safe_usage(getattr(callback, "usage_metadata", {}))
            self._ledger.append(
                self._event(
                    event_type="completion",
                    attempt=attempt,
                    status="provider_error",
                    reason_code="DIAGNOSTIC_PROVIDER_ERROR",
                    input_tokens=usage.get("input_tokens"),
                    output_tokens=usage.get("output_tokens"),
                    total_tokens=usage.get("total_tokens"),
                    recorded_at=datetime.now(UTC),
                )
            )
            raise


def _diagnostic_trusted(spec: _DiagnosticInput) -> TrustedToolContext:
    return TrustedToolContext(
        customer_id="customer-diagnostic",
        conversation_id="conversation-v3-diagnostic",
        case_id="case-v3-diagnostic",
        run_id=f"run-{spec.input_id.lower()}",
        authorized_order_id="ORD-DIAG-001",
        canonical_issue_type=spec.issue_type,
        fixture_version="v3-diagnostic-fixture-20260828-04",
        fault_seed="diagnostic-no-fault-in-memory-only",
        evaluated_at=datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
        trace_id=f"trace-{spec.input_id.lower()}",
    )


def _diagnostic_history(
    trusted: TrustedToolContext,
    completed_tools: Sequence[str],
) -> tuple[list[dict[str, Any]], list[Any]]:
    calls: list[dict[str, Any]] = []
    refs: list[Any] = []
    for index, tool_name in enumerate(completed_tools, start=1):
        arguments: dict[str, Any] = {"order_id": trusted.authorized_order_id}
        if tool_name in {"search_after_sales_policy", "get_existing_logistics_tickets"}:
            arguments["issue_type"] = trusted.canonical_issue_type.value
        result = ToolResult[Any].completed(
            availability=EvidenceAvailability.ABSENT,
            source_type=f"diagnostic:{tool_name}",
            source_query_id=f"diag-query-{index}-{tool_name}",
            observed_at=trusted.evaluated_at,
            payload=None,
        )
        tool_call_id = f"diag-call-{index}-{tool_name}"
        calls.append(
            {
                "case_id": trusted.case_id,
                "run_id": trusted.run_id,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "normalized_args": arguments,
                "attempt_number": 1,
                "execution_status": result.execution_status.value,
                "evidence_availability": result.evidence_availability.value,
                "result_envelope": result.model_dump(mode="json"),
                "result_hash": result.result_hash,
                "actual_execution": True,
                "source_version": "v3-diagnostic-fixture-20260828-04",
                "requested_at": trusted.evaluated_at.replace(microsecond=index).isoformat(),
            }
        )
        refs.extend(result.to_evidence_refs(tool_call_id))
    return calls, refs


def _diagnostic_context(
    spec: _DiagnosticInput,
) -> tuple[TrustedToolContext, Any, BudgetSnapshot]:
    trusted = _diagnostic_trusted(spec)
    calls, refs = _diagnostic_history(trusted, spec.completed_tools)
    progress = EvidenceProgressReducer().rebuild(
        case_id=trusted.case_id,
        run_id=trusted.run_id,
        canonical_issue_type=trusted.canonical_issue_type,
        tool_calls=calls,
        evidence_refs=refs,
        rebuilt_at=trusted.evaluated_at,
    )
    budget = BudgetSnapshot(
        case_planning_turns=0,
        run_planning_turns=0,
        actual_read_tool_executions=len(calls),
    )
    context = build_decision_context(
        trusted=trusted,
        customer_message=spec.context_note,
        progress=progress,
        budget=budget,
        prompt_policy_version=INVESTIGATION_SELECTOR_PROMPT_VERSION,
    )
    return trusted, context, budget


def _last_attempt(ledger: _DiagnosticLedger, input_id: str) -> int:
    return max(
        (
            event.attempt
            for event in ledger.events
            if event.event_type == "admission" and event.diagnostic_input_id == input_id
        ),
        default=0,
    )


def _server_message(observation: Any) -> AIMessage:
    if observation.action is ObservationAction.FINISH:
        return AIMessage(content="必要的只读观察已经完成。")
    return AIMessage(
        content="",
        tool_calls=[
            {
                "id": f"diag-server-{observation.decision_id}",
                "name": str(observation.tool_name),
                "args": dict(observation.canonical_arguments),
                "type": "tool_call",
            }
        ],
    )


def _server_projection(message: AIMessage) -> dict[str, Any]:
    calls = message.tool_calls
    tool_name: str | None = None
    fields: tuple[str, ...] = ()
    if len(calls) == 1 and isinstance(calls[0], Mapping):
        raw_name = calls[0].get("name")
        tool_name = raw_name if isinstance(raw_name, str) else None
        raw_args = calls[0].get("args")
        if isinstance(raw_args, Mapping):
            fields = tuple(sorted(str(field) for field in raw_args))
    return {
        "server_message_rebuilt": True,
        "server_tool_call_count": len(calls),
        "server_tool_name": tool_name,
        "server_argument_field_names": fields,
    }


def _append_boundary_event(
    ledger: _DiagnosticLedger,
    *,
    spec: _DiagnosticInput,
    source_revision: str,
    attempt: int,
    passed: bool,
    reason_code: str,
    observed_action: Literal["call_tool", "finish"] | None = None,
    observed_tool_name: str | None = None,
    observed_evidence_requirement: str | None = None,
    router_route: str | None = None,
    server_projection: Mapping[str, Any] | None = None,
) -> None:
    ledger.append(
        V3DiagnosticEvent(
            diagnostic_input_id=spec.input_id,
            source_revision=source_revision,
            stage=spec.stage,
            expected_action=spec.expected_action,
            expected_tool_name=spec.expected_tool_name,
            expected_evidence_requirement=spec.expected_evidence_requirement,
            observed_action=observed_action,
            observed_tool_name=observed_tool_name,
            observed_evidence_requirement=observed_evidence_requirement,
            router_route=router_route,
            selector_boundary_pass=passed,
            event_type="boundary",
            attempt=attempt,
            status="passed" if passed else "failed",
            reason_code=reason_code,
            **dict(server_projection or {}),
            recorded_at=datetime.now(UTC),
        )
    )


async def _run_diagnostic_input(
    *,
    model: Any,
    ledger: _DiagnosticLedger,
    spec: _DiagnosticInput,
    source_revision: str,
) -> str:
    trusted, context, budget = _diagnostic_context(spec)
    observer = _DiagnosticInvocationObserver(
        ledger,
        diagnostic_input_id=spec.input_id,
        source_revision=source_revision,
        stage=spec.stage,
        expected_action=spec.expected_action,
        expected_tool_name=spec.expected_tool_name,
        expected_evidence_requirement=spec.expected_evidence_requirement,
    )
    selector = AgentObservationSelector(model, invocation_observer=observer)
    attempt = 0
    try:
        candidate = await selector.select_next_observation(context)
        attempt = _last_attempt(ledger, spec.input_id)
        validation = ObservationValidator().validate(
            candidate,
            context=context,
            selector_kind=SelectorKind.AGENT,
            trusted=trusted,
            gate_ready=context.evidence_progress.gate_readiness is GateReadiness.EVALUABLE,
        )
        if validation.observation is None:
            reason = f"DIAGNOSTIC_SELECTOR_REJECTED_{validation.rejection_code or 'UNKNOWN'}"
            _append_boundary_event(
                ledger,
                spec=spec,
                source_revision=source_revision,
                attempt=attempt,
                passed=False,
                reason_code=reason,
            )
            return reason
        observation = validation.observation
        observed_action: Literal["call_tool", "finish"] = observation.action.value
        observed_tool_name = observation.tool_name
        observed_evidence_requirement = (
            observation.addresses[0].value if observation.addresses else None
        )
        server_projection = _server_projection(_server_message(observation))
        if observed_action != spec.expected_action:
            reason = "DIAGNOSTIC_UNEXPECTED_ACTION"
            _append_boundary_event(
                ledger,
                spec=spec,
                source_revision=source_revision,
                attempt=attempt,
                passed=False,
                reason_code=reason,
                observed_action=observed_action,
                observed_tool_name=observed_tool_name,
                observed_evidence_requirement=observed_evidence_requirement,
                server_projection=server_projection,
            )
            return reason
        if observed_tool_name != spec.expected_tool_name:
            reason = "DIAGNOSTIC_UNEXPECTED_TOOL"
            _append_boundary_event(
                ledger,
                spec=spec,
                source_revision=source_revision,
                attempt=attempt,
                passed=False,
                reason_code=reason,
                observed_action=observed_action,
                observed_tool_name=observed_tool_name,
                observed_evidence_requirement=observed_evidence_requirement,
                server_projection=server_projection,
            )
            return reason
        if observed_evidence_requirement != spec.expected_evidence_requirement:
            reason = "DIAGNOSTIC_UNEXPECTED_EVIDENCE_REQUIREMENT"
            _append_boundary_event(
                ledger,
                spec=spec,
                source_revision=source_revision,
                attempt=attempt,
                passed=False,
                reason_code=reason,
                observed_action=observed_action,
                observed_tool_name=observed_tool_name,
                observed_evidence_requirement=observed_evidence_requirement,
                server_projection=server_projection,
            )
            return reason
        expected_server_fields = {
            "order_id",
            "issue_type",
        } if observed_tool_name in {
            "search_after_sales_policy",
            "get_existing_logistics_tickets",
        } else {"order_id"}
        if observed_action == "call_tool" and (
            server_projection["server_tool_call_count"] != 1
            or set(server_projection["server_argument_field_names"]) != expected_server_fields
        ):
            reason = "DIAGNOSTIC_SERVER_TOOL_CALL_REBUILD_INVALID"
            _append_boundary_event(
                ledger,
                spec=spec,
                source_revision=source_revision,
                attempt=attempt,
                passed=False,
                reason_code=reason,
                observed_action=observed_action,
                observed_tool_name=observed_tool_name,
                observed_evidence_requirement=observed_evidence_requirement,
                server_projection=server_projection,
            )
            return reason
        recovery = ObservationRouter().route(
            case_id=context.case_id,
            run_id=context.run_id,
            progress_before=context.evidence_progress,
            progress_after=context.evidence_progress,
            budget=budget,
        )
        passed = recovery.route is spec.expected_route
        reason = "DIAGNOSTIC_BOUNDARY_PASSED" if passed else "DIAGNOSTIC_UNEXPECTED_ROUTE"
        _append_boundary_event(
            ledger,
            spec=spec,
            source_revision=source_revision,
            attempt=attempt,
            passed=passed,
            reason_code=reason,
            observed_action=observed_action,
            observed_tool_name=observed_tool_name,
            observed_evidence_requirement=observed_evidence_requirement,
            router_route=recovery.route.value,
            server_projection=server_projection,
        )
        return reason
    except SelectorSchemaFailure as exc:
        attempt = _last_attempt(ledger, spec.input_id)
        _append_boundary_event(
            ledger,
            spec=spec,
            source_revision=source_revision,
            attempt=attempt,
            passed=False,
            reason_code=exc.reason_code,
        )
        return exc.reason_code
    except TimeoutError:
        attempt = _last_attempt(ledger, spec.input_id)
        _append_boundary_event(
            ledger,
            spec=spec,
            source_revision=source_revision,
            attempt=attempt,
            passed=False,
            reason_code="DIAGNOSTIC_PROVIDER_TIMEOUT",
        )
        return "DIAGNOSTIC_PROVIDER_TIMEOUT"
    except Exception:
        attempt = _last_attempt(ledger, spec.input_id)
        _append_boundary_event(
            ledger,
            spec=spec,
            source_revision=source_revision,
            attempt=attempt,
            passed=False,
            reason_code="DIAGNOSTIC_BOUNDARY_ERROR",
        )
        return "DIAGNOSTIC_BOUNDARY_ERROR"


def _diagnostic_passed_ids(
    ledger: _DiagnosticLedger,
    *,
    source_revision: str,
    input_ids: Sequence[str],
) -> tuple[str, ...]:
    passed = {
        event.diagnostic_input_id
        for event in ledger.events
        if event.event_type == "boundary"
        and event.selector_boundary_pass is True
        and event.diagnostic_input_id in input_ids
        and event.source_revision == source_revision
    }
    return tuple(input_id for input_id in input_ids if input_id in passed)


def _diagnostic_counts(ledger: _DiagnosticLedger) -> dict[str, int]:
    boundaries = [event for event in ledger.events if event.event_type == "boundary"]
    schema_failures = sum(
        event.reason_code.startswith("SELECTOR_")
        or "INVALID_CANDIDATE_SCHEMA" in event.reason_code
        for event in boundaries
    )
    return {
        "server_message_rebuild_count": sum(event.server_message_rebuilt for event in boundaries),
        "server_tool_call_rebuild_count": sum(
            event.server_message_rebuilt and event.server_tool_call_count == 1
            for event in boundaries
        ),
        "selector_schema_failure_count": schema_failures,
        "multi_observation_rejection_count": sum(
            event.reason_code == "SELECTOR_MULTIPLE_TOOL_CALLS" for event in boundaries
        ),
    }


async def _run_live_selector_diagnostics(
    project_root: Path,
    *,
    diagnostic_identity: str = DIAGNOSTIC_IDENTITY,
) -> V3DiagnosticReport:
    _validate_diagnostic_identity(diagnostic_identity)
    path = _diagnostic_path(project_root, diagnostic_identity=diagnostic_identity)
    ledger = _DiagnosticLedger(path)
    live_mode = os.environ.get("LLM_MODE") == LLMMode.LIVE.value
    model_match = os.environ.get("DEEPSEEK_MODEL", DIAGNOSTIC_MODEL) == DIAGNOSTIC_MODEL
    credential_present = bool(os.environ.get("DEEPSEEK_API_KEY"))
    try:
        manifest, manifest_digest, manifest_path = load_diagnostic_manifest(project_root)
        specs = tuple(_DiagnosticInput.from_manifest_input(item) for item in manifest.inputs)
        manifest_error: str | None = None
    except (OSError, ValueError) as exc:
        manifest = None
        manifest_digest = None
        manifest_path = diagnostic_manifest_path(project_root)
        specs = ()
        manifest_error = type(exc).__name__
    try:
        source_revision = current_source_revision(project_root)
    except Exception:
        source_revision = None

    input_ids = tuple(item.input_id for item in specs)

    def report(
        *,
        status: Literal["passed", "failed", "blocked"],
        reason_code: str,
        passed_input_ids: Sequence[str] = (),
    ) -> V3DiagnosticReport:
        counts = _diagnostic_counts(ledger)
        return V3DiagnosticReport(
            status=status,
            live_mode=live_mode,
            model_match=model_match,
            credential_present=credential_present,
            source_revision=source_revision,
            manifest_id=manifest.manifest_id if manifest is not None else None,
            manifest_digest=manifest_digest,
            manifest_path=str(manifest_path),
            diagnostic_input_ids=input_ids,
            passed_input_ids=tuple(
                input_id for input_id in input_ids if input_id in passed_input_ids
            ),
            input_count=len(input_ids),
            provider_calls=sum(event.event_type == "admission" for event in ledger.events),
            reason_code=reason_code,
            ledger_path=str(path),
            events=tuple(ledger.events),
            **counts,
        )

    if manifest_error is not None or manifest is None or len(specs) < 12:
        ledger.append(
            V3DiagnosticEvent(
                diagnostic_input_id="V3-DIAG-MANIFEST",
                source_revision=source_revision,
                event_type="blocked",
                attempt=0,
                status="blocked",
                reason_code="DIAGNOSTIC_MANIFEST_INVALID",
                recorded_at=datetime.now(UTC),
            )
        )
        return report(status="blocked", reason_code="DIAGNOSTIC_MANIFEST_INVALID")
    if not (live_mode and model_match and credential_present):
        ledger.append(
            V3DiagnosticEvent(
                diagnostic_input_id="V3-DIAG-CONFIG",
                source_revision=source_revision,
                manifest_id=manifest.manifest_id,
                manifest_digest=manifest_digest,
                event_type="blocked",
                attempt=0,
                status="blocked",
                reason_code="DIAGNOSTIC_CONFIGURATION_INVALID",
                recorded_at=datetime.now(UTC),
            )
        )
        return report(status="blocked", reason_code="DIAGNOSTIC_CONFIGURATION_INVALID")
    if source_revision is None:
        ledger.append(
            V3DiagnosticEvent(
                diagnostic_input_id="V3-DIAG-SOURCE",
                event_type="blocked",
                attempt=0,
                status="blocked",
                reason_code="DIAGNOSTIC_SOURCE_BINDING_UNAVAILABLE",
                recorded_at=datetime.now(UTC),
            )
        )
        return report(status="blocked", reason_code="DIAGNOSTIC_SOURCE_BINDING_UNAVAILABLE")
    existing_bound_digests = {
        event.manifest_digest
        for event in ledger.events
        if event.event_type == "manifest_bound" and event.source_revision == source_revision
    }
    if existing_bound_digests and existing_bound_digests != {manifest_digest}:
        ledger.append(
            V3DiagnosticEvent(
                diagnostic_input_id="V3-DIAG-MANIFEST",
                source_revision=source_revision,
                event_type="blocked",
                attempt=sum(event.event_type == "admission" for event in ledger.events),
                status="blocked",
                reason_code="DIAGNOSTIC_MANIFEST_DIGEST_MISMATCH",
                recorded_at=datetime.now(UTC),
            )
        )
        return report(status="blocked", reason_code="DIAGNOSTIC_MANIFEST_DIGEST_MISMATCH")
    if not existing_bound_digests:
        ledger.append(
            V3DiagnosticEvent(
                diagnostic_input_id="V3-DIAG-MANIFEST",
                source_revision=source_revision,
                manifest_id=manifest.manifest_id,
                manifest_digest=manifest_digest,
                event_type="manifest_bound",
                attempt=sum(event.event_type == "admission" for event in ledger.events),
                status="bound",
                reason_code="DIAGNOSTIC_MANIFEST_BOUND",
                recorded_at=datetime.now(UTC),
            )
        )
    passed_input_ids = _diagnostic_passed_ids(
        ledger,
        source_revision=source_revision,
        input_ids=input_ids,
    )
    if set(passed_input_ids) == set(input_ids):
        return report(
            status="passed",
            reason_code="DIAGNOSTIC_ALREADY_PASSED_NO_RETRY",
            passed_input_ids=passed_input_ids,
        )
    if sum(event.event_type == "admission" for event in ledger.events) >= DIAGNOSTIC_MAX_CALLS:
        ledger.append(
            V3DiagnosticEvent(
                diagnostic_input_id="V3-DIAG-CEILING",
                source_revision=source_revision,
                manifest_id=manifest.manifest_id,
                manifest_digest=manifest_digest,
                event_type="blocked",
                attempt=DIAGNOSTIC_MAX_CALLS,
                status="blocked",
                reason_code="V3_FINAL_NO_GO_SELECTOR_TRANSPORT",
                recorded_at=datetime.now(UTC),
            )
        )
        return report(
            status="failed",
            reason_code="V3_FINAL_NO_GO_SELECTOR_TRANSPORT",
            passed_input_ids=passed_input_ids,
        )

    try:
        settings = Settings(
            _env_file=None,
            LLM_MODE="live",
            DEEPSEEK_MODEL=manifest.model,
            DEEPSEEK_TIMEOUT_SECONDS=manifest.timeout_seconds,
            POLICY_RETRIEVAL_MODE="fake_test",
        )
        model = build_investigation_model(settings, READ_TOOLS)
    except Exception:
        ledger.append(
            V3DiagnosticEvent(
                diagnostic_input_id="V3-DIAG-CONFIG",
                source_revision=source_revision,
                manifest_id=manifest.manifest_id,
                manifest_digest=manifest_digest,
                event_type="blocked",
                attempt=sum(event.event_type == "admission" for event in ledger.events),
                status="blocked",
                reason_code="DIAGNOSTIC_CONFIGURATION_INVALID",
                recorded_at=datetime.now(UTC),
            )
        )
        return report(status="blocked", reason_code="DIAGNOSTIC_CONFIGURATION_INVALID")

    def admissions_for(input_id: str) -> int:
        return sum(
            event.event_type == "admission" and event.diagnostic_input_id == input_id
            for event in ledger.events
        )

    reasons: list[str] = []
    first_pass_failed: list[_DiagnosticInput] = []
    for spec in specs:
        if spec.input_id in passed_input_ids:
            continue
        if admissions_for(spec.input_id) >= DIAGNOSTIC_MAX_ATTEMPTS_PER_INPUT:
            first_pass_failed.append(spec)
            continue
        reason = await _run_diagnostic_input(
            model=model,
            ledger=ledger,
            spec=spec,
            source_revision=source_revision,
        )
        if reason == "DIAGNOSTIC_BOUNDARY_PASSED":
            passed_input_ids = (*passed_input_ids, spec.input_id)
        else:
            reasons.append(reason)
            first_pass_failed.append(spec)
        if sum(event.event_type == "admission" for event in ledger.events) >= DIAGNOSTIC_MAX_CALLS:
            break

    for spec in first_pass_failed:
        if spec.input_id in passed_input_ids:
            continue
        if sum(event.event_type == "admission" for event in ledger.events) >= DIAGNOSTIC_MAX_CALLS:
            break
        if admissions_for(spec.input_id) >= DIAGNOSTIC_MAX_ATTEMPTS_PER_INPUT:
            continue
        reason = await _run_diagnostic_input(
            model=model,
            ledger=ledger,
            spec=spec,
            source_revision=source_revision,
        )
        if reason == "DIAGNOSTIC_BOUNDARY_PASSED":
            passed_input_ids = (*passed_input_ids, spec.input_id)
        else:
            reasons.append(reason)

    passed_input_ids = tuple(input_id for input_id in input_ids if input_id in passed_input_ids)
    if set(passed_input_ids) == set(input_ids):
        return report(
            status="passed",
            reason_code="DIAGNOSTIC_ALL_INPUTS_PASSED",
            passed_input_ids=passed_input_ids,
        )
    return report(
        status="failed",
        reason_code=(
            "V3_FINAL_NO_GO_SELECTOR_TRANSPORT"
            if any(spec.input_id not in passed_input_ids for spec in specs)
            else reasons[0]
        ),
        passed_input_ids=passed_input_ids,
    )


def run_live_selector_diagnostics(
    project_root: Path | None = None,
    *,
    diagnostic_identity: str = DIAGNOSTIC_IDENTITY,
) -> V3DiagnosticReport:
    """Run the fixed, non-measurement Live selector diagnostic within 24 calls."""

    _validate_diagnostic_identity(diagnostic_identity)
    project = project_root or _REPO_ROOT
    return asyncio.run(
        _run_live_selector_diagnostics(project, diagnostic_identity=diagnostic_identity)
    )


__all__ = [
    "DIAGNOSTIC_IDENTITY",
    "DIAGNOSTIC_LABEL",
    "DIAGNOSTIC_MAX_CALLS",
    "DIAGNOSTIC_MAX_ATTEMPTS_PER_INPUT",
    "DiagnosticAuthorizationError",
    "V3DiagnosticEvent",
    "V3DiagnosticReport",
    "run_live_selector_diagnostics",
]

"""Bounded, non-measurement diagnostics for the Live selector boundary."""

from __future__ import annotations

import asyncio
import json
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
    parse_live_selector_tool_call,
)
from after_sales_agent.agents.prompts import INVESTIGATION_SELECTOR_PROMPT_VERSION
from after_sales_agent.agents.tool_bindings import READ_TOOLS
from after_sales_agent.application.adaptive_core import (
    CANDIDATE_SCHEMA_VERSION,
    BudgetSnapshot,
    EvidenceProgressReducer,
    GateReadiness,
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
from after_sales_agent.evals.v3.real_runner import current_source_revision
from after_sales_agent.tools.contracts import ToolResult
from after_sales_agent.tools.service import READ_TOOL_NAMES

DIAGNOSTIC_IDENTITY: Final = "V3-DEV-DIAG-20260828-03"
DIAGNOSTIC_LABEL: Final = "real_external_diagnostic_not_measurement"
DIAGNOSTIC_MAX_CALLS: Final = 12
DIAGNOSTIC_TIMEOUT_SECONDS: Final = 30.0
DIAGNOSTIC_MODEL: Final = "deepseek-v4-flash"
DIAGNOSTIC_ROOT_RELATIVE: Final = Path("var/v3/development-diagnostics")
_SAFE_ARGUMENT_FIELDS = frozenset({"order_id", "issue_type"})
_DIAGNOSTIC_INPUTS = (
    "V3-DIAG-READ-001",
    "V3-DIAG-FINISH-001",
    "V3-DIAG-PARAMS-001",
)


class V3DiagnosticEvent(BaseModel):
    """One safe append-only diagnostic event; provider payloads are excluded."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v3.diagnostic.event.v1"] = "v3.diagnostic.event.v1"
    diagnostic_identity: Literal["V3-DEV-DIAG-20260828-03"] = DIAGNOSTIC_IDENTITY
    diagnostic_input_id: str = Field(min_length=1, max_length=64)
    source_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    selector_schema_version: str = Field(
        default=CANDIDATE_SCHEMA_VERSION, min_length=1, max_length=128
    )
    prompt_policy_version: str = Field(
        default=INVESTIGATION_SELECTOR_PROMPT_VERSION, min_length=1, max_length=128
    )
    event_type: Literal["admission", "completion", "boundary", "blocked"]
    attempt: int = Field(ge=0, le=DIAGNOSTIC_MAX_CALLS)
    status: Literal[
        "admitted",
        "completed",
        "schema_failure",
        "provider_error",
        "passed",
        "failed",
        "blocked",
    ]
    reason_code: str = Field(min_length=1, max_length=96)
    expected_action: Literal["call_tool", "finish"] | None = None
    expected_tool_name: str | None = Field(default=None, max_length=96)
    observed_action: Literal["call_tool", "finish"] | None = None
    observed_tool_name: str | None = Field(default=None, max_length=96)
    router_route: str | None = Field(default=None, max_length=32)
    selector_boundary_pass: bool | None = None
    response_is_ai_message: bool = False
    tool_call_count: int = Field(default=0, ge=0, le=8)
    allowlisted_tool_name: bool | None = None
    args_is_object: bool | None = None
    argument_field_names: tuple[str, ...] = Field(default_factory=tuple, max_length=2)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    recorded_at: datetime


class V3DiagnosticReport(BaseModel):
    """Diagnostic result explicitly separated from formal measurement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v3.diagnostic.report.v1"] = "v3.diagnostic.report.v1"
    diagnostic_identity: Literal["V3-DEV-DIAG-20260828-03"] = DIAGNOSTIC_IDENTITY
    status: Literal["passed", "failed", "blocked"]
    diagnostic_label: Literal["real_external_diagnostic_not_measurement"] = DIAGNOSTIC_LABEL
    live_mode: bool
    model_match: bool
    credential_present: bool
    source_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    selector_schema_version: str = CANDIDATE_SCHEMA_VERSION
    prompt_policy_version: str = INVESTIGATION_SELECTOR_PROMPT_VERSION
    diagnostic_input_ids: tuple[str, ...] = _DIAGNOSTIC_INPUTS
    passed_input_ids: tuple[str, ...] = Field(default_factory=tuple)
    provider_calls: int = Field(ge=0, le=DIAGNOSTIC_MAX_CALLS)
    reason_code: str = Field(min_length=1, max_length=96)
    ledger_path: str = Field(min_length=1)
    events: tuple[V3DiagnosticEvent, ...] = Field(default_factory=tuple)


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
    """Project only the response shape needed to diagnose the typed boundary."""

    if not isinstance(response, AIMessage):
        return {
            "response_is_ai_message": False,
            "tool_call_count": 0,
            "schema_reason_code": "SELECTOR_RESPONSE_NOT_AI_MESSAGE",
        }
    calls = response.tool_calls
    if not isinstance(calls, list):
        return {
            "response_is_ai_message": True,
            "tool_call_count": 0,
            "schema_reason_code": "SELECTOR_TOOL_CALLS_NOT_LIST",
        }
    if getattr(response, "invalid_tool_calls", ()):
        return {
            "response_is_ai_message": True,
            "tool_call_count": len(calls),
            "schema_reason_code": "SELECTOR_INVALID_NATIVE_TOOL_CALL",
        }
    projection: dict[str, Any] = {
        "response_is_ai_message": True,
        "tool_call_count": len(calls),
        "schema_reason_code": (
            "SELECTOR_FINISH" if not calls else "SELECTOR_TYPED_TOOL_CALL"
        ),
    }
    if len(calls) != 1:
        if len(calls) > 1:
            projection["schema_reason_code"] = "SELECTOR_MULTIPLE_TOOL_CALLS"
        return projection
    call = calls[0]
    if not isinstance(call, Mapping):
        projection["schema_reason_code"] = "SELECTOR_TOOL_CALL_NOT_OBJECT"
        return projection

    function_payload = call.get("function")
    if isinstance(function_payload, Mapping):
        raw_name = function_payload.get("name")
        raw_arguments = function_payload.get("arguments")
    else:
        raw_name = call.get("name")
        raw_arguments = call.get("args")
    projection["allowlisted_tool_name"] = (
        isinstance(raw_name, str) and raw_name.strip() in READ_TOOL_NAMES
    )
    try:
        tool_name, arguments, _ = parse_live_selector_tool_call(call)
    except SelectorSchemaFailure as exc:
        projection["schema_reason_code"] = exc.reason_code
        if isinstance(raw_arguments, str):
            try:
                raw_arguments = json.loads(raw_arguments)
            except json.JSONDecodeError:
                raw_arguments = None
        projection["args_is_object"] = isinstance(raw_arguments, Mapping)
        if isinstance(raw_arguments, Mapping):
            projection["argument_field_names"] = tuple(
                sorted(field for field in raw_arguments if field in _SAFE_ARGUMENT_FIELDS)
            )
        return projection
    projection["allowlisted_tool_name"] = tool_name in READ_TOOL_NAMES
    projection["args_is_object"] = True
    projection["argument_field_names"] = tuple(
        sorted(field for field in arguments if field in _SAFE_ARGUMENT_FIELDS)
    )
    projection["schema_reason_code"] = "SELECTOR_TYPED_TOOL_CALL"
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
    completed_tools: tuple[str, ...]
    expected_action: Literal["call_tool", "finish"]
    expected_tool_name: str | None
    expected_route: RecoveryRoute


class _DiagnosticInvocationObserver:
    def __init__(
        self,
        ledger: _DiagnosticLedger,
        *,
        diagnostic_input_id: str = "V3-DIAG-UNBOUND",
        source_revision: str | None = None,
        expected_action: Literal["call_tool", "finish"] | None = None,
        expected_tool_name: str | None = None,
    ) -> None:
        self._ledger = ledger
        self._diagnostic_input_id = diagnostic_input_id
        self._source_revision = source_revision
        self._expected_action = expected_action
        self._expected_tool_name = expected_tool_name
        self._attempts = sum(event.event_type == "admission" for event in ledger.events)

    def _event(self, **kwargs: Any) -> V3DiagnosticEvent:
        return V3DiagnosticEvent(
            diagnostic_input_id=self._diagnostic_input_id,
            source_revision=self._source_revision,
            expected_action=self._expected_action,
            expected_tool_name=self._expected_tool_name,
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
                usage = _safe_usage(getattr(response, "usage_metadata", None))
                callback_usage = _safe_usage(getattr(usage_callback, "usage_metadata", {}))
                for key, value in callback_usage.items():
                    usage.setdefault(key, value)
            projection = _response_projection(response)
            self._ledger.append(
                self._event(
                    event_type="completion",
                    attempt=attempt,
                    status="completed",
                    reason_code=str(projection["schema_reason_code"]),
                    response_is_ai_message=bool(projection["response_is_ai_message"]),
                    tool_call_count=int(projection["tool_call_count"]),
                    allowlisted_tool_name=projection.get("allowlisted_tool_name"),
                    args_is_object=projection.get("args_is_object"),
                    argument_field_names=tuple(projection.get("argument_field_names", ())),
                    input_tokens=usage.get("input_tokens"),
                    output_tokens=usage.get("output_tokens"),
                    total_tokens=usage.get("total_tokens"),
                    recorded_at=datetime.now(UTC),
                )
            )
            return response
        except TimeoutError:
            self._ledger.append(
                self._event(
                    event_type="completion",
                    attempt=attempt,
                    status="provider_error",
                    reason_code="DIAGNOSTIC_PROVIDER_TIMEOUT",
                    input_tokens=_safe_usage(getattr(callback, "usage_metadata", {})).get(
                        "input_tokens"
                    ),
                    output_tokens=_safe_usage(getattr(callback, "usage_metadata", {})).get(
                        "output_tokens"
                    ),
                    total_tokens=_safe_usage(getattr(callback, "usage_metadata", {})).get(
                        "total_tokens"
                    ),
                    recorded_at=datetime.now(UTC),
                )
            )
            raise
        except Exception:
            self._ledger.append(
                self._event(
                    event_type="completion",
                    attempt=attempt,
                    status="provider_error",
                    reason_code="DIAGNOSTIC_PROVIDER_ERROR",
                    input_tokens=_safe_usage(getattr(callback, "usage_metadata", {})).get(
                        "input_tokens"
                    ),
                    output_tokens=_safe_usage(getattr(callback, "usage_metadata", {})).get(
                        "output_tokens"
                    ),
                    total_tokens=_safe_usage(getattr(callback, "usage_metadata", {})).get(
                        "total_tokens"
                    ),
                    recorded_at=datetime.now(UTC),
                )
            )
            raise


def _diagnostic_specs() -> tuple[_DiagnosticInput, ...]:
    return (
        _DiagnosticInput(
            input_id=_DIAGNOSTIC_INPUTS[0],
            completed_tools=(
                "get_logistics_timeline",
                "get_delivery_proof",
                "search_after_sales_policy",
                "get_existing_logistics_tickets",
            ),
            expected_action="call_tool",
            expected_tool_name="get_order_context",
            expected_route=RecoveryRoute.REPLAN,
        ),
        _DiagnosticInput(
            input_id=_DIAGNOSTIC_INPUTS[1],
            completed_tools=(
                "get_order_context",
                "get_logistics_timeline",
                "get_delivery_proof",
                "search_after_sales_policy",
                "get_existing_logistics_tickets",
            ),
            expected_action="finish",
            expected_tool_name=None,
            expected_route=RecoveryRoute.FINALIZE,
        ),
        _DiagnosticInput(
            input_id=_DIAGNOSTIC_INPUTS[2],
            completed_tools=(
                "get_order_context",
                "get_logistics_timeline",
                "get_delivery_proof",
                "get_existing_logistics_tickets",
            ),
            expected_action="call_tool",
            expected_tool_name="search_after_sales_policy",
            expected_route=RecoveryRoute.REPLAN,
        ),
    )


def _diagnostic_trusted(spec: _DiagnosticInput) -> TrustedToolContext:
    return TrustedToolContext(
        customer_id="customer-diagnostic",
        conversation_id="conversation-v3-diagnostic",
        case_id="case-v3-diagnostic",
        run_id=f"run-{spec.input_id.lower()}",
        authorized_order_id="ORD-DIAG-001",
        canonical_issue_type=IssueType.SIGNED_NOT_RECEIVED,
        fixture_version="v3-diagnostic-fixture-20260828-03",
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
                "source_version": "v3-diagnostic-fixture-20260828-03",
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
        customer_message="合成诊断消息，不代表正式评估样本。",
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
    router_route: str | None = None,
) -> None:
    ledger.append(
        V3DiagnosticEvent(
            diagnostic_input_id=spec.input_id,
            source_revision=source_revision,
            expected_action=spec.expected_action,
            expected_tool_name=spec.expected_tool_name,
            observed_action=observed_action,
            observed_tool_name=observed_tool_name,
            router_route=router_route,
            selector_boundary_pass=passed,
            event_type="boundary",
            attempt=attempt,
            status="passed" if passed else "failed",
            reason_code=reason_code,
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
        expected_action=spec.expected_action,
        expected_tool_name=spec.expected_tool_name,
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
        observed_tool_name = (
            observation.tool_name if observation.tool_name in READ_TOOL_NAMES else None
        )
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
            router_route=recovery.route.value,
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


async def _run_live_selector_diagnostics(
    project_root: Path,
    *,
    diagnostic_identity: str = DIAGNOSTIC_IDENTITY,
) -> V3DiagnosticReport:
    path = _diagnostic_path(project_root, diagnostic_identity=diagnostic_identity)
    ledger = _DiagnosticLedger(path)
    live_mode = os.environ.get("LLM_MODE") == LLMMode.LIVE.value
    model_match = os.environ.get("DEEPSEEK_MODEL", DIAGNOSTIC_MODEL) == DIAGNOSTIC_MODEL
    credential_present = bool(os.environ.get("DEEPSEEK_API_KEY"))
    try:
        source_revision = current_source_revision(project_root)
    except Exception:
        source_revision = None

    def report(
        *,
        status: Literal["passed", "failed", "blocked"],
        reason_code: str,
        passed_input_ids: Sequence[str] = (),
    ) -> V3DiagnosticReport:
        return V3DiagnosticReport(
            status=status,
            live_mode=live_mode,
            model_match=model_match,
            credential_present=credential_present,
            source_revision=source_revision,
            diagnostic_input_ids=_DIAGNOSTIC_INPUTS,
            passed_input_ids=tuple(passed_input_ids),
            provider_calls=sum(event.event_type == "admission" for event in ledger.events),
            reason_code=reason_code,
            ledger_path=str(path),
            events=tuple(ledger.events),
        )

    if not (live_mode and model_match and credential_present):
        ledger.append(
            V3DiagnosticEvent(
                diagnostic_input_id="V3-DIAG-CONFIG",
                source_revision=source_revision,
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
    passed_input_ids = tuple(
        dict.fromkeys(
            event.diagnostic_input_id
            for event in ledger.events
            if event.event_type == "boundary"
            and event.selector_boundary_pass is True
            and event.diagnostic_input_id in _DIAGNOSTIC_INPUTS
            and event.source_revision == source_revision
        )
    )
    if set(passed_input_ids) == set(_DIAGNOSTIC_INPUTS):
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
                event_type="blocked",
                attempt=DIAGNOSTIC_MAX_CALLS,
                status="blocked",
                reason_code="DIAGNOSTIC_CALL_CEILING_EXHAUSTED",
                recorded_at=datetime.now(UTC),
            )
        )
        return report(status="blocked", reason_code="DIAGNOSTIC_CALL_CEILING_EXHAUSTED")

    try:
        settings = Settings(
            _env_file=None,
            LLM_MODE="live",
            DEEPSEEK_MODEL=DIAGNOSTIC_MODEL,
            DEEPSEEK_TIMEOUT_SECONDS=DIAGNOSTIC_TIMEOUT_SECONDS,
            POLICY_RETRIEVAL_MODE="fake_test",
        )
        model = build_investigation_model(settings, READ_TOOLS)
    except Exception:
        ledger.append(
            V3DiagnosticEvent(
                diagnostic_input_id="V3-DIAG-CONFIG",
                source_revision=source_revision,
                event_type="blocked",
                attempt=0,
                status="blocked",
                reason_code="DIAGNOSTIC_CONFIGURATION_INVALID",
                recorded_at=datetime.now(UTC),
            )
        )
        return report(status="blocked", reason_code="DIAGNOSTIC_CONFIGURATION_INVALID")

    reasons: list[str] = []
    for spec in _diagnostic_specs():
        if spec.input_id in passed_input_ids:
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
    status: Literal["passed", "failed"] = (
        "passed" if set(passed_input_ids) == set(_DIAGNOSTIC_INPUTS) else "failed"
    )
    reason = "DIAGNOSTIC_ALL_INPUTS_PASSED" if status == "passed" else reasons[0]
    return report(status=status, reason_code=reason, passed_input_ids=passed_input_ids)


def run_live_selector_diagnostics(
    project_root: Path | None = None,
    *,
    diagnostic_identity: str = DIAGNOSTIC_IDENTITY,
) -> V3DiagnosticReport:
    """Run the fixed, non-measurement Live selector diagnostic within 12 calls."""

    _validate_diagnostic_identity(diagnostic_identity)
    project = project_root or Path(__file__).resolve().parents[4]
    return asyncio.run(
        _run_live_selector_diagnostics(project, diagnostic_identity=diagnostic_identity)
    )


__all__ = [
    "DIAGNOSTIC_IDENTITY",
    "DIAGNOSTIC_LABEL",
    "DIAGNOSTIC_MAX_CALLS",
    "DiagnosticAuthorizationError",
    "V3DiagnosticEvent",
    "V3DiagnosticReport",
    "run_live_selector_diagnostics",
]

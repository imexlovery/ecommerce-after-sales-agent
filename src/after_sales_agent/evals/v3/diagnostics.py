"""Bounded, non-measurement diagnostics for the Live selector boundary."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from os import fsync
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final, Literal

from langchain_core.callbacks import get_usage_metadata_callback
from langchain_core.messages import AIMessage
from pydantic import BaseModel, ConfigDict, Field

from after_sales_agent.agents.models import AgentObservationSelector, build_investigation_model
from after_sales_agent.agents.tool_bindings import READ_TOOLS
from after_sales_agent.application.provider_budget import SelectorSchemaFailure
from after_sales_agent.config import LLMMode, Settings
from after_sales_agent.domain.state import IssueType
from after_sales_agent.tools.service import READ_TOOL_NAMES

DIAGNOSTIC_IDENTITY: Final = "V3-DEV-DIAG-20260828-02"
DIAGNOSTIC_LABEL: Final = "real_external_diagnostic_not_measurement"
DIAGNOSTIC_MAX_CALLS: Final = 3
DIAGNOSTIC_MODEL: Final = "deepseek-v4-flash"
DIAGNOSTIC_ROOT_RELATIVE: Final = Path("var/v3/development-diagnostics")
_SAFE_ARGUMENT_FIELDS = frozenset({"order_id", "issue_type"})


class V3DiagnosticEvent(BaseModel):
    """One safe append-only diagnostic event; provider payloads are excluded."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v3.diagnostic.event.v1"] = "v3.diagnostic.event.v1"
    diagnostic_identity: Literal["V3-DEV-DIAG-20260828-02"] = DIAGNOSTIC_IDENTITY
    event_type: Literal["admission", "completion", "blocked"]
    attempt: int = Field(ge=0, le=DIAGNOSTIC_MAX_CALLS)
    status: Literal["admitted", "completed", "schema_failure", "provider_error", "blocked"]
    reason_code: str = Field(min_length=1, max_length=96)
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
    diagnostic_identity: Literal["V3-DEV-DIAG-20260828-02"] = DIAGNOSTIC_IDENTITY
    status: Literal["passed", "failed", "blocked"]
    diagnostic_label: Literal["real_external_diagnostic_not_measurement"] = DIAGNOSTIC_LABEL
    live_mode: bool
    model_match: bool
    credential_present: bool
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
    function = call.get("function")
    function_payload = function if isinstance(function, Mapping) else None
    raw_name = call.get("name")
    if raw_name is None and function_payload is not None:
        raw_name = function_payload.get("name")
    projection["allowlisted_tool_name"] = (
        isinstance(raw_name, str) and raw_name.strip() in READ_TOOL_NAMES
    )
    if not isinstance(raw_name, str) or not raw_name.strip():
        projection["schema_reason_code"] = "SELECTOR_TOOL_NAME_MISSING"
    elif not projection["allowlisted_tool_name"]:
        projection["schema_reason_code"] = "SELECTOR_TOOL_NAME_NOT_ALLOWLISTED"
    raw_arguments = call.get("args")
    if raw_arguments is None and function_payload is not None:
        raw_arguments = function_payload.get("arguments")
    projection["args_is_object"] = isinstance(raw_arguments, Mapping)
    if isinstance(raw_arguments, Mapping):
        projection["argument_field_names"] = tuple(
            sorted(field for field in raw_arguments if field in _SAFE_ARGUMENT_FIELDS)
        )
    if raw_arguments is None:
        projection["schema_reason_code"] = "SELECTOR_TOOL_ARGS_MISSING"
    elif not isinstance(raw_arguments, Mapping):
        projection["schema_reason_code"] = "SELECTOR_TOOL_ARGS_NOT_OBJECT"
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


class _DiagnosticInvocationObserver:
    def __init__(self, ledger: _DiagnosticLedger) -> None:
        self._ledger = ledger
        self._attempts = sum(event.event_type == "admission" for event in ledger.events)

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
            V3DiagnosticEvent(
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
                response = await asyncio.wait_for(model.ainvoke(messages), timeout=30.0)
                usage = _safe_usage(getattr(response, "usage_metadata", None))
                callback_usage = _safe_usage(getattr(usage_callback, "usage_metadata", {}))
                for key, value in callback_usage.items():
                    usage.setdefault(key, value)
            projection = _response_projection(response)
            self._ledger.append(
                V3DiagnosticEvent(
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
                V3DiagnosticEvent(
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
                V3DiagnosticEvent(
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


def _diagnostic_context() -> Any:
    progress = SimpleNamespace(
        model_dump=lambda mode="python": {
            "gate_readiness": "not_evaluable",
            "missing_required_codes": ["ORDER_STATUS"],
        }
    )
    return SimpleNamespace(
        authorized_order_id="ORD-DIAG-001",
        canonical_issue_type=IssueType.SIGNED_NOT_RECEIVED,
        evidence_progress=progress,
        customer_message="合成诊断消息，不代表正式评估样本。",
        remaining_budget=SimpleNamespace(run_planning_turns=1),
    )


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
    if not (live_mode and model_match and credential_present):
        ledger.append(
            V3DiagnosticEvent(
                event_type="blocked",
                attempt=0,
                status="blocked",
                reason_code="DIAGNOSTIC_CONFIGURATION_INVALID",
                recorded_at=datetime.now(UTC),
            )
        )
        return V3DiagnosticReport(
            status="blocked",
            live_mode=live_mode,
            model_match=model_match,
            credential_present=credential_present,
            provider_calls=0,
            reason_code="DIAGNOSTIC_CONFIGURATION_INVALID",
            ledger_path=str(path),
            events=tuple(ledger.events),
        )
    if sum(event.event_type == "admission" for event in ledger.events) >= DIAGNOSTIC_MAX_CALLS:
        ledger.append(
            V3DiagnosticEvent(
                event_type="blocked",
                attempt=DIAGNOSTIC_MAX_CALLS,
                status="blocked",
                reason_code="DIAGNOSTIC_CALL_CEILING_EXHAUSTED",
                recorded_at=datetime.now(UTC),
            )
        )
        return V3DiagnosticReport(
            status="blocked",
            live_mode=live_mode,
            model_match=model_match,
            credential_present=credential_present,
            provider_calls=DIAGNOSTIC_MAX_CALLS,
            reason_code="DIAGNOSTIC_CALL_CEILING_EXHAUSTED",
            ledger_path=str(path),
            events=tuple(ledger.events),
        )

    try:
        settings = Settings(
            _env_file=None,
            LLM_MODE="live",
            DEEPSEEK_MODEL=DIAGNOSTIC_MODEL,
            DEEPSEEK_TIMEOUT_SECONDS=30.0,
            POLICY_RETRIEVAL_MODE="fake_test",
        )
        model = build_investigation_model(settings, READ_TOOLS)
    except Exception:
        ledger.append(
            V3DiagnosticEvent(
                event_type="blocked",
                attempt=0,
                status="blocked",
                reason_code="DIAGNOSTIC_CONFIGURATION_INVALID",
                recorded_at=datetime.now(UTC),
            )
        )
        return V3DiagnosticReport(
            status="blocked",
            live_mode=live_mode,
            model_match=model_match,
            credential_present=credential_present,
            provider_calls=0,
            reason_code="DIAGNOSTIC_CONFIGURATION_INVALID",
            ledger_path=str(path),
            events=tuple(ledger.events),
        )

    selector = AgentObservationSelector(
        model,
        invocation_observer=_DiagnosticInvocationObserver(ledger),
    )
    context = _diagnostic_context()
    status: Literal["passed", "failed"] = "failed"
    try:
        await selector.select_next_observation(context)
    except SelectorSchemaFailure as exc:
        reason = exc.reason_code
    except TimeoutError:
        reason = "DIAGNOSTIC_PROVIDER_TIMEOUT"
    except Exception:
        reason = "DIAGNOSTIC_PROVIDER_ERROR"
    else:
        completion = next(
            (event for event in reversed(ledger.events) if event.event_type == "completion"),
            None,
        )
        reason = (
            completion.reason_code
            if completion is not None
            else "DIAGNOSTIC_COMPLETION_MISSING"
        )
        status = "passed" if completion is not None else "failed"
    return V3DiagnosticReport(
        status=status,
        live_mode=live_mode,
        model_match=model_match,
        credential_present=credential_present,
        provider_calls=sum(event.event_type == "admission" for event in ledger.events),
        reason_code=reason,
        ledger_path=str(path),
        events=tuple(ledger.events),
    )


def run_live_selector_diagnostics(
    project_root: Path | None = None,
    *,
    diagnostic_identity: str = DIAGNOSTIC_IDENTITY,
) -> V3DiagnosticReport:
    """Run at most three real selector calls, stopping at the first known reason."""

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

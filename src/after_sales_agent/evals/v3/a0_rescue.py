"""The separately authorized V3-A0 Live vertical-slice rescue.

This module is deliberately smaller than the V3 Development runner.  It owns
only the three fixed smoke inputs, a six-admission provider budget, safe
transport diagnostics, and an identity-isolated append-only ledger.  The
business investigation remains the production application composition root.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Literal, cast
from urllib.parse import urlsplit
from uuid import uuid4

from langchain_core.callbacks import get_usage_metadata_callback
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, ConfigDict, Field, model_validator

from after_sales_agent.agents.models import (
    AgentObservationSelector,
    build_investigation_model,
    build_live_model,
)
from after_sales_agent.agents.prompts import INVESTIGATION_SELECTOR_SYSTEM_PROMPT
from after_sales_agent.agents.tool_bindings import READ_TOOLS
from after_sales_agent.application.adaptive_core import (
    DecisionTraceRecord,
    NextObservationCandidate,
    RecoveryTraceRecord,
    SelectorKind,
    StateTraceRecord,
    canonical_arguments_hash,
)
from after_sales_agent.application.investigation import InvestigationService
from after_sales_agent.application.provider_budget import (
    ProviderBudgetAdmissionRejected,
    ProviderInvocationFailure,
    SelectorSchemaFailure,
)
from after_sales_agent.config import Settings, build_live_settings, build_mock_settings
from after_sales_agent.domain.models import InvestigationCase, Run, TrustedToolContext
from after_sales_agent.domain.state import ExecutionStatus, IssueType
from after_sales_agent.evals.v3.production_fixtures import fixture_store_for_case
from after_sales_agent.evals.v3.real_runner import (
    ProductionRuntime,
    build_production_runtime,
    current_source_revision,
    source_tree_is_clean,
)
from after_sales_agent.events.models import EventEnvelope
from after_sales_agent.fixtures.catalog import FixtureFault, FixtureStore
from after_sales_agent.storage.repositories import Repository
from after_sales_agent.tools.cache import CaseToolCache
from after_sales_agent.tools.contracts import ToolResult

RESCUE_IDENTITY: Final = "V3-A0-RESCUE-20260828-01"
RESCUE_LABEL: Final = "real_external_a0_smoke_not_development_measurement"
RESCUE_MODEL: Final = "deepseek-v4-flash"
RESCUE_PROVIDER: Final = "deepseek"
RESCUE_TIMEOUT_SECONDS: Final = 30.0
RESCUE_OUTPUT_TOKEN_CAP: Final = 512
RESCUE_PROVIDER_CALL_CEILING: Final = 6
RESCUE_ROOT_RELATIVE: Final = Path("var/v3/a0-rescue")
RESCUE_TEMPLATE_RELATIVE: Final = Path("evals/v3/a0-rescue-manifest.template.json")
RESCUE_SCHEMA_VERSION: Final = "v3.a0.rescue.manifest.v1"
RESCUE_LEDGER_SCHEMA_VERSION: Final = "v3.a0.rescue.ledger.v1"
RESCUE_REPORT_SCHEMA_VERSION: Final = "v3.a0.rescue.report.v1"
_REPO_ROOT = Path(__file__).resolve().parents[4]
_PROXY_NAMES: Final = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)
_SOCKS_SCHEMES: Final = frozenset({"socks4", "socks4a", "socks5", "socks5h"})
_READ_TOOL_NAMES: Final = frozenset(tool.name for tool in READ_TOOLS)
_JSON_FALLBACK_REASONS: Final = frozenset(
    {
        "SELECTOR_RESPONSE_NOT_STRUCTURED",
        "SELECTOR_STRUCTURED_RESPONSE_FIELDS_INVALID",
        "SELECTOR_RAW_RESPONSE_NOT_AI_MESSAGE",
        "SELECTOR_TOOL_CALLS_NOT_LIST",
        "SELECTOR_INVALID_STRUCTURED_CALL",
        "SELECTOR_EMPTY_STRUCTURED_OUTPUT",
        "SELECTOR_MULTIPLE_TOOL_CALLS",
        "SELECTOR_STRUCTURED_CALL_NOT_OBJECT",
        "SELECTOR_STRUCTURED_SCHEMA_NAME_INVALID",
        "SELECTOR_INVALID_JSON",
        "SELECTOR_STRUCTURED_SCHEMA_INVALID",
    }
)


class RescueErrorCategory(StrEnum):
    BUDGET_ADMISSION = "budget_admission"
    OUTBOUND_ATTEMPT = "outbound_attempt"
    PROVIDER_COMPLETED_RESPONSE = "provider_completed_response"
    PROVIDER_HTTP_ERROR = "provider_http_error"
    PROVIDER_TRANSPORT_ERROR = "provider_transport_error"
    LOCAL_TRANSPORT_CONFIGURATION_ERROR = "local_transport_configuration_error"
    TIMEOUT = "timeout"
    CANDIDATE_BOUNDARY_REJECTION = "candidate_boundary_rejection"
    TOOL_FAILURE = "tool_failure"
    TOOLNODE_OBSERVATION = "toolnode_observation"
    ROUTER_DECISION = "router_decision"
    PREFLIGHT = "preflight"


_PRE_RESPONSE_FAILURE_CATEGORIES: Final = frozenset(
    {
        RescueErrorCategory.BUDGET_ADMISSION,
        RescueErrorCategory.OUTBOUND_ATTEMPT,
        RescueErrorCategory.PROVIDER_HTTP_ERROR,
        RescueErrorCategory.PROVIDER_TRANSPORT_ERROR,
        RescueErrorCategory.LOCAL_TRANSPORT_CONFIGURATION_ERROR,
        RescueErrorCategory.TIMEOUT,
    }
)


class RescueExecutionError(RuntimeError):
    """A safe, identity-scoped rescue artifact or execution contract failed."""


class RescueIdentityAlreadyExecuted(RescueExecutionError):
    """Do not rerun a completed Rescue identity or replace its evidence."""


class _RescueContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RescueSmokeSpec(_RescueContract):
    smoke_id: Literal["A0-01", "A0-02", "A0-03"]
    description: str = Field(min_length=1, max_length=160)
    customer_id: str = Field(pattern=r"^customer_[a-z]+$")
    order_id: str = Field(pattern=r"^ORD-[A-Z0-9-]+$")
    issue_type: IssueType
    customer_message: str = Field(min_length=1, max_length=2_000)
    fixture_profile: str = Field(min_length=1, max_length=96)
    fixture_version: str = Field(min_length=1, max_length=64)
    fault_seed: str = Field(min_length=1, max_length=96)
    legal_tool_names: tuple[str, ...] = Field(min_length=1, max_length=6)
    minimum_missing_requirements: tuple[str, ...] = Field(min_length=1, max_length=6)
    fault_tool_names: tuple[str, ...] = Field(default_factory=tuple, max_length=6)
    fault_error_code: str | None = Field(default=None, max_length=96)
    stop_after_actual_executions: Literal[1, 2]

    @model_validator(mode="after")
    def validate_tools_and_fault(self) -> RescueSmokeSpec:
        if not set(self.legal_tool_names).issubset(_READ_TOOL_NAMES):
            raise ValueError("rescue legal_tool_names must be allowlisted read tools")
        if not self.set_unique(self.legal_tool_names):
            raise ValueError("rescue legal_tool_names must not contain duplicates")
        if not set(self.fault_tool_names).issubset(set(self.legal_tool_names)):
            raise ValueError("rescue fault tools must be legal candidate tools")
        if self.fault_tool_names and not self.fault_error_code:
            raise ValueError("rescue fault tools require a fixed fault error code")
        if not self.fault_tool_names and self.fault_error_code is not None:
            raise ValueError("rescue fault error code requires fault tools")
        return self

    @staticmethod
    def set_unique(values: Sequence[str]) -> bool:
        return len(values) == len(set(values))


class RescueManifestTemplate(_RescueContract):
    schema_version: Literal["v3.a0.rescue.template.v1"] = "v3.a0.rescue.template.v1"
    execution_identity: Literal["V3-A0-RESCUE-20260828-01"] = RESCUE_IDENTITY
    provider: Literal["deepseek"] = RESCUE_PROVIDER
    model: Literal["deepseek-v4-flash"] = RESCUE_MODEL
    timeout_seconds: float = RESCUE_TIMEOUT_SECONDS
    output_token_cap: int = RESCUE_OUTPUT_TOKEN_CAP
    provider_call_ceiling: int = RESCUE_PROVIDER_CALL_CEILING
    automatic_retry: Literal[False] = False
    smoke_cases: tuple[RescueSmokeSpec, ...] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_case_order(self) -> RescueManifestTemplate:
        if tuple(item.smoke_id for item in self.smoke_cases) != ("A0-01", "A0-02", "A0-03"):
            raise ValueError("rescue smoke cases must be ordered A0-01, A0-02, A0-03")
        if self.provider_call_ceiling != RESCUE_PROVIDER_CALL_CEILING:
            raise ValueError("rescue provider ceiling is fixed at six")
        if self.automatic_retry:
            raise ValueError("rescue automatic provider retry must be disabled")
        return self


class RescueManifest(_RescueContract):
    schema_version: Literal["v3.a0.rescue.manifest.v1"] = RESCUE_SCHEMA_VERSION
    execution_identity: Literal["V3-A0-RESCUE-20260828-01"] = RESCUE_IDENTITY
    source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    template_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    fixture_version: Literal["fixture-v1"] = "fixture-v1"
    provider: Literal["deepseek"] = RESCUE_PROVIDER
    model: Literal["deepseek-v4-flash"] = RESCUE_MODEL
    timeout_seconds: float = RESCUE_TIMEOUT_SECONDS
    output_token_cap: int = RESCUE_OUTPUT_TOKEN_CAP
    provider_call_ceiling: int = RESCUE_PROVIDER_CALL_CEILING
    automatic_retry: Literal[False] = False
    label: Literal["real_external_a0_smoke_not_development_measurement"] = RESCUE_LABEL
    smoke_cases: tuple[RescueSmokeSpec, ...] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_binding(self) -> RescueManifest:
        if tuple(item.smoke_id for item in self.smoke_cases) != ("A0-01", "A0-02", "A0-03"):
            raise ValueError("rescue manifest smoke cases must retain fixed order")
        if self.provider_call_ceiling != RESCUE_PROVIDER_CALL_CEILING:
            raise ValueError("rescue manifest provider ceiling is not six")
        if self.automatic_retry:
            raise ValueError("rescue manifest cannot enable automatic retry")
        return self


class RescueLedgerEvent(_RescueContract):
    schema_version: Literal["v3.a0.rescue.ledger.v1"] = RESCUE_LEDGER_SCHEMA_VERSION
    execution_identity: Literal["V3-A0-RESCUE-20260828-01"] = RESCUE_IDENTITY
    sequence: int = Field(ge=1)
    smoke_id: str = Field(min_length=1, max_length=32)
    run_id: str | None = Field(default=None, max_length=96)
    event_type: Literal[
        "manifest_bound",
        "preflight",
        "budget_admission",
        "outbound_attempt",
        "provider_completed_response",
        "provider_http_error",
        "provider_transport_error",
        "local_transport_configuration_error",
        "timeout",
        "candidate_boundary_rejection",
        "toolnode_observation",
        "router_decision",
        "run_completed",
        "run_failed",
        "blocked",
    ]
    category: RescueErrorCategory
    status: Literal["started", "admitted", "completed", "failed", "blocked", "passed"]
    attempt: int = Field(ge=0, le=RESCUE_PROVIDER_CALL_CEILING)
    error_code: str | None = Field(default=None, max_length=96)
    http_status: int | None = Field(default=None, ge=100, le=599)
    transport: Literal["function_calling", "json", None] = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    response_is_mapping: bool | None = None
    response_is_ai_message: bool | None = None
    response_tool_call_count: int | None = Field(default=None, ge=0, le=8)
    candidate_boundary_reason: str | None = Field(default=None, max_length=128)
    fallback_attempted: bool = False
    candidate_action: str | None = Field(default=None, max_length=32)
    candidate_tool_name: str | None = Field(default=None, max_length=96)
    candidate_requirement_names: tuple[str, ...] = Field(default_factory=tuple, max_length=6)
    validator_status: str | None = Field(default=None, max_length=32)
    validator_rejection_code: str | None = Field(default=None, max_length=96)
    server_tool_call_count: int = Field(default=0, ge=0, le=16)
    actual_read_executions: int = Field(default=0, ge=0, le=6)
    tool_name: str | None = Field(default=None, max_length=96)
    canonical_arguments_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    trusted_scope_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_version: str | None = Field(default=None, max_length=160)
    result_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    execution_status: str | None = Field(default=None, max_length=32)
    evidence_availability: str | None = Field(default=None, max_length=24)
    retry_of_tool_call_id: str | None = Field(default=None, max_length=160)
    retry_attempt_number: int | None = Field(default=None, ge=1, le=2)
    retry_identity_match: bool | None = None
    router_route: str | None = Field(default=None, max_length=32)
    router_reason_code: str | None = Field(default=None, max_length=96)
    recorded_at: datetime


class RescuePreflight(_RescueContract):
    schema_version: Literal["v3.a0.rescue.preflight.v1"] = "v3.a0.rescue.preflight.v1"
    status: Literal["passed", "blocked"]
    source_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    clean_source: bool
    credential_present: bool
    model_expected: bool
    api_base_scheme: str
    transport_mode: Literal["socks_proxy", "direct_or_http_proxy", "unknown"]
    proxy_present: dict[str, bool]
    proxy_schemes: dict[str, str]
    socksio_present: bool
    live_model_constructed: bool
    selector_constructed: bool
    automatic_retry_disabled: bool
    provider_calls: int = 0
    network_requests: int = 0
    error_category: RescueErrorCategory | None = None
    error_code: str | None = None


class RescueSmokeEvidence(_RescueContract):
    schema_version: Literal["v3.a0.rescue.smoke.v1"] = "v3.a0.rescue.smoke.v1"
    smoke_id: Literal["A0-01", "A0-02", "A0-03"]
    status: Literal["passed", "failed", "blocked"]
    source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    case_id: str = Field(min_length=1, max_length=96)
    run_id: str = Field(min_length=1, max_length=96)
    provider_calls: int = Field(ge=0, le=RESCUE_PROVIDER_CALL_CEILING)
    selector_invocations: int = Field(ge=0, le=RESCUE_PROVIDER_CALL_CEILING)
    provider_completed_responses: int = Field(ge=0, le=RESCUE_PROVIDER_CALL_CEILING)
    candidate_transport_fallback_used: bool = False
    candidate_accepted: bool = False
    candidate_action: str | None = None
    candidate_tool_name: str | None = None
    candidate_requirement_names: tuple[str, ...] = Field(default_factory=tuple, max_length=6)
    validator_status: str | None = None
    validator_rejection_code: str | None = None
    server_tool_call_count: int = Field(ge=0, le=16)
    toolnode_node_present: bool
    toolnode_reached: bool
    typed_tool_result_count: int = Field(ge=0, le=6)
    actual_read_executions: int = Field(ge=0, le=6)
    initial_missing_required_codes: tuple[str, ...] = Field(default_factory=tuple, max_length=6)
    tool_call_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=6)
    tool_names: tuple[str, ...] = Field(default_factory=tuple, max_length=6)
    progress_rebuild_count: int = Field(ge=0)
    router_decision_count: int = Field(ge=0)
    router_routes: tuple[str, ...] = Field(default_factory=tuple)
    router_reason_codes: tuple[str, ...] = Field(default_factory=tuple)
    retry_of_tool_call_id: str | None = None
    retry_attempt_numbers: tuple[int, ...] = Field(default_factory=tuple, max_length=2)
    retry_identity_match: bool | None = None
    retry_first_execution_status: str | None = None
    retry_first_evidence_availability: str | None = None
    retry_first_error_code: str | None = None
    retry_tool_name: str | None = None
    retry_canonical_arguments_hash: str | None = None
    retry_source_version: str | None = None
    trusted_scope_hash: str | None = None
    error_category: RescueErrorCategory | None = None
    error_code: str | None = None
    event_types: tuple[str, ...] = Field(default_factory=tuple)


class RescueRunReport(_RescueContract):
    schema_version: Literal["v3.a0.rescue.report.v1"] = RESCUE_REPORT_SCHEMA_VERSION
    execution_identity: Literal["V3-A0-RESCUE-20260828-01"] = RESCUE_IDENTITY
    label: Literal["real_external_a0_smoke_not_development_measurement"] = RESCUE_LABEL
    status: Literal["V3_A0_RESCUE_GO", "V3_A0_RESCUE_NO_GO"]
    source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    preflight: RescuePreflight
    provider_call_ceiling: int = RESCUE_PROVIDER_CALL_CEILING
    provider_calls: int = Field(ge=0, le=RESCUE_PROVIDER_CALL_CEILING)
    ledger_event_count: int = Field(ge=1)
    ledger_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    all_failures_retained: bool
    historical_evidence_modified: bool
    smokes: tuple[RescueSmokeEvidence, ...] = Field(min_length=3, max_length=3)
    completed_at: datetime

    @model_validator(mode="after")
    def validate_report(self) -> RescueRunReport:
        if self.provider_calls > self.provider_call_ceiling:
            raise ValueError("rescue report exceeds provider ceiling")
        if self.historical_evidence_modified:
            raise ValueError("rescue report cannot pass a historical evidence mutation")
        return self


class _RescueCallBudget:
    def __init__(self) -> None:
        self.attempts = 0


class RescueLedger:
    """Append-only JSONL ledger with one fixed Rescue identity."""

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        if path.is_symlink():
            raise RescueExecutionError("rescue ledger path must not be a symlink")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.events: list[RescueLedgerEvent] = []
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    event = RescueLedgerEvent.model_validate_json(line)
                    if event.execution_identity != RESCUE_IDENTITY:
                        raise RescueExecutionError("rescue ledger identity mismatch")
                    self.events.append(event)

    def append(self, **kwargs: Any) -> RescueLedgerEvent:
        event = RescueLedgerEvent(
            sequence=len(self.events) + 1,
            recorded_at=datetime.now(UTC),
            **kwargs,
        )
        serialized = event.model_dump_json() + "\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        self.events.append(event)
        return event

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()


class RescueProviderInvocationObserver:
    """Count and classify one actual selector/provider boundary."""

    def __init__(
        self,
        *,
        ledger: RescueLedger,
        budget: _RescueCallBudget,
        smoke_id: str,
        source_revision: str,
        timeout_seconds: float,
        transport: Literal["function_calling", "json"],
        run_id: str,
    ) -> None:
        self._ledger = ledger
        self._budget = budget
        self._smoke_id = smoke_id
        self._source_revision = source_revision
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._run_id = run_id

    @property
    def attempts(self) -> int:
        return self._budget.attempts

    async def invoke(self, *, model: Any, messages: Sequence[Any], context: Any) -> Any:
        del context
        if self._budget.attempts >= RESCUE_PROVIDER_CALL_CEILING:
            self._ledger.append(
                smoke_id=self._smoke_id,
                run_id=self._run_id,
                event_type="blocked",
                category=RescueErrorCategory.BUDGET_ADMISSION,
                status="blocked",
                attempt=self._budget.attempts,
                error_code="RESCUE_PROVIDER_CALL_CEILING",
            )
            raise ProviderBudgetAdmissionRejected("RESCUE_PROVIDER_CALL_CEILING")

        self._budget.attempts += 1
        attempt = self._budget.attempts
        common = {
            "smoke_id": self._smoke_id,
            "run_id": self._run_id,
            "attempt": attempt,
            "transport": self._transport,
        }
        self._ledger.append(
            **common,
            event_type="budget_admission",
            category=RescueErrorCategory.BUDGET_ADMISSION,
            status="admitted",
            error_code="RESCUE_PROVIDER_CALL_ADMITTED",
        )
        self._ledger.append(
            **common,
            event_type="outbound_attempt",
            category=RescueErrorCategory.OUTBOUND_ATTEMPT,
            status="started",
            error_code="RESCUE_OUTBOUND_ATTEMPT_STARTED",
        )

        callback: Any = None
        try:
            with get_usage_metadata_callback() as usage_callback:
                callback = usage_callback
                response = await asyncio.wait_for(
                    model.ainvoke(messages),
                    timeout=self._timeout_seconds,
                )
                usage = _safe_usage(response)
                for key, value in _safe_usage(
                    usage_callback.usage_metadata
                ).items():
                    usage.setdefault(key, value)
        except TimeoutError as exc:
            usage = _safe_usage(getattr(callback, "usage_metadata", {}))
            self._ledger.append(
                **common,
                event_type="timeout",
                category=RescueErrorCategory.TIMEOUT,
                status="failed",
                error_code="RESCUE_PROVIDER_TIMEOUT",
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                total_tokens=usage.get("total_tokens"),
            )
            raise ProviderInvocationFailure("RESCUE_PROVIDER_TIMEOUT", cause=exc) from exc
        except asyncio.CancelledError:
            usage = _safe_usage(getattr(callback, "usage_metadata", {}))
            self._ledger.append(
                **common,
                event_type="timeout",
                category=RescueErrorCategory.TIMEOUT,
                status="failed",
                error_code="RESCUE_PROVIDER_CANCELLED",
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                total_tokens=usage.get("total_tokens"),
            )
            raise
        except Exception as exc:
            usage = _safe_usage(getattr(callback, "usage_metadata", {}))
            category, error_code, http_status = _classify_provider_exception(exc)
            event_type = category.value
            self._ledger.append(
                **common,
                event_type=event_type,
                category=category,
                status="failed",
                error_code=error_code,
                http_status=http_status,
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                total_tokens=usage.get("total_tokens"),
            )
            raise ProviderInvocationFailure(error_code, cause=exc) from exc

        shape = _response_shape(response)
        self._ledger.append(
            **common,
            event_type="provider_completed_response",
            category=RescueErrorCategory.PROVIDER_COMPLETED_RESPONSE,
            status="completed",
            error_code="RESCUE_PROVIDER_RESPONSE_RECEIVED",
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            total_tokens=usage.get("total_tokens"),
            response_is_mapping=shape[0],
            response_is_ai_message=shape[1],
            response_tool_call_count=shape[2],
        )
        return response


class RescueSelector:
    """Agent selector with exactly one bounded JSON transport alternative."""

    def __init__(
        self,
        *,
        settings: Settings,
        ledger: RescueLedger,
        budget: _RescueCallBudget,
        smoke_id: str,
        source_revision: str,
        run_id: str,
    ) -> None:
        self._ledger = ledger
        self._smoke_id = smoke_id
        self._source_revision = source_revision
        self._run_id = run_id
        self._fallback_used = False
        self._primary_observer = RescueProviderInvocationObserver(
            ledger=ledger,
            budget=budget,
            smoke_id=smoke_id,
            source_revision=source_revision,
            timeout_seconds=RESCUE_TIMEOUT_SECONDS,
            transport="function_calling",
            run_id=run_id,
        )
        self._json_observer = RescueProviderInvocationObserver(
            ledger=ledger,
            budget=budget,
            smoke_id=smoke_id,
            source_revision=source_revision,
            timeout_seconds=RESCUE_TIMEOUT_SECONDS,
            transport="json",
            run_id=run_id,
        )
        self._primary = AgentObservationSelector(
            build_investigation_model(settings, READ_TOOLS),
            invocation_observer=self._primary_observer,
        )
        self._json_model = build_live_model(settings)
        self._json_parser: PydanticOutputParser[NextObservationCandidate] = PydanticOutputParser(
            pydantic_object=NextObservationCandidate
        )

    @property
    def fallback_used(self) -> bool:
        return self._fallback_used

    @property
    def provider_calls(self) -> int:
        return self._primary_observer.attempts

    async def select_next_observation(self, context: Any) -> Any:
        try:
            return await self._primary.select_next_observation(context)
        except SelectorSchemaFailure as exc:
            can_fallback = not self._fallback_used and exc.reason_code in _JSON_FALLBACK_REASONS
            self._record_boundary_rejection(
                exc.reason_code,
                fallback_attempted=can_fallback,
            )
            if not can_fallback:
                raise
            self._fallback_used = True
            response = await self._json_observer.invoke(
                model=self._json_model,
                messages=_selector_messages(context),
                context=context,
            )
            try:
                if not isinstance(response, AIMessage) or response.tool_calls:
                    raise ValueError("json candidate response shape rejected")
                content = response.content
                if not isinstance(content, str):
                    raise ValueError("json candidate content shape rejected")
                candidate = self._json_parser.parse(content)
                return NextObservationCandidate.model_validate(candidate)
            except Exception as parse_error:
                failure = SelectorSchemaFailure(
                    "json candidate transport failed schema validation",
                    reason_code="SELECTOR_JSON_CANDIDATE_SCHEMA_INVALID",
                )
                self._record_boundary_rejection(failure.reason_code)
                raise failure from parse_error

    def _record_boundary_rejection(
        self, reason_code: str, *, fallback_attempted: bool = False
    ) -> None:
        self._ledger.append(
            smoke_id=self._smoke_id,
            run_id=self._run_id,
            event_type="candidate_boundary_rejection",
            category=RescueErrorCategory.CANDIDATE_BOUNDARY_REJECTION,
            status="failed",
            attempt=self.provider_calls,
            error_code=reason_code,
            candidate_boundary_reason=reason_code,
            fallback_attempted=fallback_attempted,
        )


def _selector_messages(context: Any) -> list[Any]:
    return [
        SystemMessage(content=INVESTIGATION_SELECTOR_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                f"AUTHORIZED_ORDER={context.authorized_order_id}\n"
                f"CANONICAL_ISSUE={context.canonical_issue_type.value}\n"
                f"EVIDENCE_PROGRESS={context.evidence_progress.model_dump(mode='json')}\n"
                f"CUSTOMER_MESSAGE_UNTRUSTED={getattr(context, 'customer_message', '')[:4_000]}"
            )
        ),
    ]


def _safe_usage(value: Any) -> dict[str, int]:
    if isinstance(value, Mapping):
        raw = value.get("raw")
        if raw is not None:
            value = raw
    if hasattr(value, "usage_metadata"):
        value = value.usage_metadata
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        raw = value.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or int(raw) != raw or raw < 0:
            continue
        result[key] = int(raw)
    return result


def _response_shape(response: Any) -> tuple[bool, bool, int | None]:
    if isinstance(response, Mapping):
        raw = response.get("raw")
        if isinstance(raw, AIMessage):
            return True, True, len(raw.tool_calls)
        return True, False, None
    if isinstance(response, AIMessage):
        return False, True, len(response.tool_calls)
    return False, False, None


def _exception_chain(exc: BaseException) -> tuple[BaseException, ...]:
    result: list[BaseException] = []
    current: BaseException | None = exc
    for _ in range(4):
        if current is None or current in result:
            break
        result.append(current)
        next_value = current.__cause__ or current.__context__
        current = next_value if isinstance(next_value, BaseException) else None
    return tuple(result)


def _safe_http_status(exc: BaseException) -> int | None:
    for item in _exception_chain(exc):
        value = getattr(item, "status_code", None)
        if isinstance(value, bool) or not isinstance(value, int) or not 100 <= value <= 599:
            continue
        return value
    return None


def _classify_provider_exception(
    exc: BaseException,
) -> tuple[RescueErrorCategory, str, int | None]:
    status = _safe_http_status(exc)
    if status is not None:
        return RescueErrorCategory.PROVIDER_HTTP_ERROR, "RESCUE_PROVIDER_HTTP_ERROR", status
    names = {type(item).__name__.casefold() for item in _exception_chain(exc)}
    if any("timeout" in name for name in names):
        return RescueErrorCategory.TIMEOUT, "RESCUE_PROVIDER_TIMEOUT", None
    if names.intersection(
        {
            "importerror",
            "modulenotfounderror",
            "invalidurl",
            "unsupportedprotocol",
            "proxyerror",
            "unsupportedproxy",
            "localprotocolerror",
            "connecterror",
            "networkerror",
        }
    ):
        return (
            RescueErrorCategory.LOCAL_TRANSPORT_CONFIGURATION_ERROR,
            "RESCUE_LOCAL_TRANSPORT_ERROR",
            None,
        )
    return RescueErrorCategory.PROVIDER_TRANSPORT_ERROR, "RESCUE_PROVIDER_TRANSPORT_ERROR", None


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _write_once_json(path: Path, payload: Mapping[str, Any]) -> None:
    path = path.expanduser().resolve()
    if path.is_symlink():
        raise RescueExecutionError("rescue artifact path must not be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RescueExecutionError("existing rescue artifact is unreadable") from exc
        if current != dict(payload):
            raise RescueExecutionError("rescue artifact is immutable and differs")
        return
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    with path.open("x", encoding="utf-8") as handle:
        handle.write(serialized)
        handle.flush()
        os.fsync(handle.fileno())


def _proxy_metadata() -> tuple[dict[str, bool], dict[str, str], str]:
    present: dict[str, bool] = {}
    schemes: dict[str, str] = {}
    for name in _PROXY_NAMES:
        value = os.environ.get(name)
        present[name] = bool(value)
        schemes[name] = urlsplit(value).scheme.casefold() if value else "absent"
    if any(scheme in _SOCKS_SCHEMES for scheme in schemes.values()):
        mode = "socks_proxy"
    elif any(scheme != "absent" for scheme in schemes.values()):
        mode = "direct_or_http_proxy"
    else:
        mode = "direct_or_http_proxy"
    return present, schemes, mode


def _safe_live_settings(project_root: Path, root: Path, spec: RescueSmokeSpec) -> Settings:
    try:
        settings = build_live_settings(
            project_root,
            runtime_root=root,
            timeout_seconds=RESCUE_TIMEOUT_SECONDS,
            fault_seed=spec.fault_seed,
            evaluated_at=datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
        )
    except ValueError as exc:
        raise RescueExecutionError("rescue Live configuration is invalid") from exc
    if not settings.deepseek_api_key:
        raise RescueExecutionError("rescue credential is not present")
    if settings.deepseek_model != RESCUE_MODEL:
        raise RescueExecutionError("rescue model configuration is invalid")
    return settings


def load_rescue_template(project_root: Path | None = None) -> tuple[RescueManifestTemplate, str]:
    project = (project_root or _REPO_ROOT).expanduser().resolve()
    path = project / RESCUE_TEMPLATE_RELATIVE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RescueExecutionError("rescue manifest template is unreadable") from exc
    template = RescueManifestTemplate.model_validate(raw)
    return template, _sha256_json(template.model_dump(mode="json"))


def prepare_rescue_manifest(
    project_root: Path | None = None,
    *,
    source_revision: str | None = None,
) -> tuple[RescueManifest, str, Path]:
    project = (project_root or _REPO_ROOT).expanduser().resolve()
    template, template_digest = load_rescue_template(project)
    revision = source_revision or current_source_revision(project)
    manifest = RescueManifest(
        source_revision=revision,
        template_digest=template_digest,
        input_digest=_sha256_json(
            {
                "execution_identity": RESCUE_IDENTITY,
                "smoke_cases": [item.model_dump(mode="json") for item in template.smoke_cases],
            }
        ),
        smoke_cases=template.smoke_cases,
    )
    root = project / RESCUE_ROOT_RELATIVE / RESCUE_IDENTITY
    manifest_path = root / "manifest.json"
    _write_once_json(manifest_path, manifest.model_dump(mode="json"))
    manifest_payload = manifest.model_dump(mode="json")
    manifest_digest = _sha256_json(manifest_payload)
    _write_once_json(root / "manifest.sha256.json", {"manifest_sha256": manifest_digest})
    return manifest, manifest_digest, manifest_path


def run_rescue_preflight(project_root: Path | None = None) -> RescuePreflight:
    project = (project_root or _REPO_ROOT).expanduser().resolve()
    present, schemes, transport_mode = _proxy_metadata()
    try:
        revision = current_source_revision(project)
        clean = source_tree_is_clean(project)
    except Exception:
        revision = None
        clean = False
    credential_present = False
    model_expected = False
    api_base_scheme = "unknown"
    socksio_present = False
    live_model_constructed = False
    selector_constructed = False
    automatic_retry_disabled = False
    error_category: RescueErrorCategory | None = None
    error_code: str | None = None
    try:
        settings = build_mock_settings(
            project,
            runtime_root=project / "var" / "v3" / "rescue-preflight",
            timeout_seconds=RESCUE_TIMEOUT_SECONDS,
            evaluated_at=datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
        )
        credential_present = bool(settings.deepseek_api_key)
        model_expected = settings.deepseek_model == RESCUE_MODEL
        api_base_scheme = urlsplit(settings.deepseek_api_base).scheme.casefold() or "unknown"
        try:
            importlib.metadata.version("socksio")
            socksio_present = True
        except importlib.metadata.PackageNotFoundError:
            socksio_present = False
        if not credential_present:
            error_category = RescueErrorCategory.LOCAL_TRANSPORT_CONFIGURATION_ERROR
            error_code = "RESCUE_CREDENTIAL_MISSING"
        elif not model_expected:
            error_category = RescueErrorCategory.LOCAL_TRANSPORT_CONFIGURATION_ERROR
            error_code = "RESCUE_MODEL_MISMATCH"
        elif api_base_scheme not in {"http", "https"}:
            error_category = RescueErrorCategory.LOCAL_TRANSPORT_CONFIGURATION_ERROR
            error_code = "RESCUE_API_BASE_SCHEME_INVALID"
        elif transport_mode == "socks_proxy" and not socksio_present:
            error_category = RescueErrorCategory.LOCAL_TRANSPORT_CONFIGURATION_ERROR
            error_code = "RESCUE_SOCKS_DEPENDENCY_MISSING"
        else:
            live_settings = build_live_settings(
                project,
                runtime_root=project / "var" / "v3" / "rescue-preflight",
                timeout_seconds=RESCUE_TIMEOUT_SECONDS,
                evaluated_at=datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
            )
            live_model = build_live_model(live_settings)
            build_investigation_model(live_settings, READ_TOOLS)
            live_model.with_structured_output(
                NextObservationCandidate, method="function_calling", include_raw=True
            )
            live_model_constructed = True
            selector_constructed = True
            automatic_retry_disabled = getattr(live_model, "max_retries", None) == 0
            if not automatic_retry_disabled:
                error_category = RescueErrorCategory.LOCAL_TRANSPORT_CONFIGURATION_ERROR
                error_code = "RESCUE_AUTOMATIC_RETRY_NOT_DISABLED"
    except Exception:
        error_category = RescueErrorCategory.LOCAL_TRANSPORT_CONFIGURATION_ERROR
        error_code = error_code or "RESCUE_LOCAL_PREFLIGHT_FAILED"
    status: Literal["passed", "blocked"] = "passed" if error_category is None else "blocked"
    return RescuePreflight(
        status=status,
        source_revision=revision,
        clean_source=clean,
        credential_present=credential_present,
        model_expected=model_expected,
        api_base_scheme=api_base_scheme,
        transport_mode=transport_mode,
        proxy_present=present,
        proxy_schemes=schemes,
        socksio_present=socksio_present,
        live_model_constructed=live_model_constructed,
        selector_constructed=selector_constructed,
        automatic_retry_disabled=automatic_retry_disabled,
        error_category=error_category,
        error_code=error_code,
    )


def _faulted_fixtures(spec: RescueSmokeSpec, evaluated_at: datetime) -> FixtureStore:
    fixtures = fixture_store_for_case(
        profile=spec.fixture_profile,
        customer_id=spec.customer_id,
        order_id=spec.order_id,
        issue_type=spec.issue_type,
        evaluated_at=evaluated_at,
        fault_seed=spec.fault_seed,
    )
    if not spec.fault_tool_names:
        return fixtures
    faults = {
        (spec.fault_seed, tool_name, 1): FixtureFault(
            execution_status=ExecutionStatus.RETRYABLE_ERROR,
            error_code=spec.fault_error_code or "RESCUE_SYNTHETIC_TIMEOUT",
        )
        for tool_name in spec.fault_tool_names
    }
    return fixtures.with_faults(faults)


def _event_subset(
    runtime: ProductionRuntime, conversation_id: str, run_id: str
) -> list[EventEnvelope]:
    return [
        event
        for event in runtime.events.list_after(conversation_id)
        if event.run_id == run_id
    ]


def _typed_records(
    runtime: ProductionRuntime,
    *,
    case_id: str,
    run_id: str,
    events: Sequence[EventEnvelope],
) -> tuple[list[DecisionTraceRecord], list[RecoveryTraceRecord], list[StateTraceRecord], list[Any]]:
    decisions = [
        DecisionTraceRecord.model_validate(event.payload)
        for event in events
        if event.event_type == "decision_trace_record"
    ]
    recoveries = [
        RecoveryTraceRecord.model_validate(event.payload)
        for event in events
        if event.event_type == "recovery_trace_record"
    ]
    states = [
        StateTraceRecord.model_validate(event.payload)
        for event in events
        if event.event_type == "state_trace_record"
    ]
    with runtime.database.session_factory() as session:
        rows = Repository(session).list_tool_calls(case_id=case_id, run_id=run_id)
    typed_results = [
        ToolResult[Any].model_validate(row.result_envelope)
        for row in rows
        if row.result_envelope is not None
    ]
    return decisions, recoveries, states, [*rows, *typed_results]


def _last_failure_category(
    ledger: RescueLedger, smoke_id: str
) -> tuple[RescueErrorCategory | None, str | None]:
    for event in reversed(ledger.events):
        if event.smoke_id == smoke_id and event.status == "failed":
            return event.category, event.error_code
    return None, None


def _blocked_smoke(spec: RescueSmokeSpec, source_revision: str, code: str) -> RescueSmokeEvidence:
    return RescueSmokeEvidence(
        smoke_id=spec.smoke_id,
        status="blocked",
        source_revision=source_revision,
        case_id=f"case_a0_{spec.smoke_id.lower()}",
        run_id=f"run_a0_{spec.smoke_id.lower()}",
        provider_calls=0,
        selector_invocations=0,
        provider_completed_responses=0,
        toolnode_node_present=False,
        toolnode_reached=False,
        typed_tool_result_count=0,
        actual_read_executions=0,
        initial_missing_required_codes=(),
        server_tool_call_count=0,
        progress_rebuild_count=0,
        router_decision_count=0,
        error_category=RescueErrorCategory.PREFLIGHT,
        error_code=code,
    )


async def _run_smoke(
    *,
    project_root: Path,
    manifest: RescueManifest,
    spec: RescueSmokeSpec,
    ledger: RescueLedger,
    budget: _RescueCallBudget,
    runtime_root: Path,
) -> RescueSmokeEvidence:
    evaluated_at = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
    case_id = f"case_a0_{spec.smoke_id.lower()}"
    run_id = f"run_a0_{spec.smoke_id.lower()}"
    conversation_id = f"conv_a0_{spec.smoke_id.lower()}"
    trace_id = f"trace_a0_{spec.smoke_id.lower()}_{uuid4().hex[:12]}"
    fixtures = _faulted_fixtures(spec, evaluated_at)
    runtime: ProductionRuntime | None = None
    events: list[EventEnvelope] = []
    error_category: RescueErrorCategory | None = None
    error_code: str | None = None
    try:
        settings = _safe_live_settings(project_root, runtime_root, spec)
        runtime = build_production_runtime(
            root=runtime_root,
            architecture="agent",
            settings=settings,
            fixtures=fixtures,
            fault_seed=spec.fault_seed,
            evaluated_at=evaluated_at,
        )
        investigation = cast(InvestigationService, runtime.application.investigation)
        graph_nodes: Mapping[str, Any] = investigation._graph.get_graph().nodes
        toolnode_node_present = "tools" in graph_nodes
        domain_case = InvestigationCase(
            case_id=case_id,
            conversation_id=conversation_id,
            customer_id=spec.customer_id,
            authorized_order_id=spec.order_id,
            canonical_issue_type=spec.issue_type,
        )
        with runtime.database.session_factory() as session, session.begin():
            repository = Repository(session)
            repository.create_conversation(
                spec.customer_id,
                spec.customer_id,
                "live",
                conversation_id=conversation_id,
                fixture_version=spec.fixture_version,
            )
            repository.create_case(domain_case)
            repository.create_run(
                Run(run_id=run_id, case_id=case_id),
                conversation_id=conversation_id,
                run_kind="message",
                trace_id=trace_id,
            )
            repository.update_run(run_id, run_state="running")
            repository.add_message(
                conversation_id,
                "customer",
                spec.customer_message,
                message_id=f"msg_a0_{spec.smoke_id.lower()}",
                case_id=case_id,
                run_id=run_id,
                trace_id=trace_id,
                created_at=evaluated_at,
            )
        snapshot = runtime.application.case_facts.initialize_case(case_id)
        trusted = TrustedToolContext(
            customer_id=spec.customer_id,
            conversation_id=conversation_id,
            case_id=case_id,
            run_id=run_id,
            authorized_order_id=spec.order_id,
            canonical_issue_type=spec.issue_type,
            fixture_version=spec.fixture_version,
            fault_seed=spec.fault_seed,
            evaluated_at=evaluated_at,
            trace_id=trace_id,
        )
        selector = RescueSelector(
            settings=settings,
            ledger=ledger,
            budget=budget,
            smoke_id=spec.smoke_id,
            source_revision=manifest.source_revision,
            run_id=run_id,
        )
        await investigation.investigate(
            trusted=trusted,
            customer_message=spec.customer_message,
            case_fact_snapshot=snapshot.model_dump(mode="json"),
            tool_cache=CaseToolCache(),
            selector_kind=SelectorKind.AGENT,
            selector_adapter=selector,
            auto_exact_retry=True,
            enforce_early_stop=True,
            stop_after_actual_executions=spec.stop_after_actual_executions,
        )
    except ProviderBudgetAdmissionRejected as exc:
        error_category, error_code = RescueErrorCategory.BUDGET_ADMISSION, exc.reason_code
    except ProviderInvocationFailure as exc:
        error_category, error_code = _last_failure_category(ledger, spec.smoke_id)
        error_category = error_category or RescueErrorCategory.PROVIDER_TRANSPORT_ERROR
        error_code = error_code or exc.reason_code
    except SelectorSchemaFailure as exc:
        error_category, error_code = (
            RescueErrorCategory.CANDIDATE_BOUNDARY_REJECTION,
            exc.reason_code,
        )
    except TimeoutError:
        error_category, error_code = RescueErrorCategory.TIMEOUT, "RESCUE_RUN_TIMEOUT"
    except Exception:
        error_category, error_code = (
            RescueErrorCategory.LOCAL_TRANSPORT_CONFIGURATION_ERROR,
            "RESCUE_RUN_FAILED",
        )
    finally:
        if runtime is not None:
            events = _event_subset(runtime, conversation_id, run_id)

    decisions: list[DecisionTraceRecord] = []
    recoveries: list[RecoveryTraceRecord] = []
    states: list[StateTraceRecord] = []
    rows: list[Any] = []
    typed_results: list[ToolResult[Any]] = []
    toolnode_node_present = False
    if runtime is not None:
        try:
            investigation = cast(InvestigationService, runtime.application.investigation)
            graph_nodes = investigation._graph.get_graph().nodes
            toolnode_node_present = "tools" in graph_nodes
            decisions, recoveries, states, combined = _typed_records(
                runtime,
                case_id=case_id,
                run_id=run_id,
                events=events,
            )
            with runtime.database.session_factory() as session:
                rows = Repository(session).list_tool_calls(case_id=case_id, run_id=run_id)
            typed_results = [
                item
                for item in combined
                if isinstance(item, ToolResult)
            ]
        except Exception:
            if error_category is None:
                error_category, error_code = (
                    RescueErrorCategory.LOCAL_TRANSPORT_CONFIGURATION_ERROR,
                    "RESCUE_TRACE_PARSE_FAILED",
                )
        runtime.close()

    accepted = [item for item in decisions if item.validation_status.value == "accepted"]
    accepted_call = next((item for item in accepted if item.action.value == "call_tool"), None)
    actual_rows = [row for row in rows if bool(row.actual_execution)]
    actual_reads = len(actual_rows)
    tool_requested_events = sum(event.event_type == "tool_call_requested" for event in events)
    tool_terminal_events = sum(
        event.event_type
        in {
            "tool_call_completed",
            "tool_call_failed",
            "tool_call_blocked",
            "tool_call_cache_hit",
        }
        for event in events
    )
    progress_events = [event for event in events if event.event_type == "evidence_progress_rebuilt"]
    initial_missing_required_codes: tuple[str, ...] = ()
    if progress_events:
        raw_progress = progress_events[0].payload.get("progress_requirements")
        if isinstance(raw_progress, Mapping):
            initial_missing_required_codes = tuple(
                sorted(
                    str(code)
                    for code, status in raw_progress.items()
                    if status == "missing"
                )
            )
    retry_recovery = next(
        (item for item in recoveries if item.route.value == "retry_exact"),
        None,
    )
    retry_identity_match: bool | None = None
    retry_tool_name: str | None = None
    retry_args_hash: str | None = None
    retry_source: str | None = None
    retry_of: str | None = None
    retry_first_execution_status: str | None = None
    retry_first_evidence_availability: str | None = None
    retry_first_error_code: str | None = None
    scope_hash = _sha256_json(
        {
            "customer_id": spec.customer_id,
            "authorized_order_id": spec.order_id,
            "canonical_issue_type": spec.issue_type.value,
            "fixture_version": spec.fixture_version,
        }
    )
    retry_rows: list[Any] = []
    if retry_recovery is not None:
        retry_of = retry_recovery.trigger_tool_call_id
        trigger_row = next(
            (
                row
                for row in rows
                if retry_of is not None and str(row.tool_call_id) == retry_of
            ),
            None,
        )
        if trigger_row is not None:
            retry_tool_name = str(trigger_row.tool_name)
            retry_rows = [row for row in actual_rows if row.tool_name == retry_tool_name]
        if len(retry_rows) >= 2 and retry_tool_name is not None:
            first, second = retry_rows[0], retry_rows[1]
            first_hash = canonical_arguments_hash(first.normalized_args)
            second_hash = canonical_arguments_hash(second.normalized_args)
            retry_args_hash = second_hash
            retry_source = second.source_version
            retry_first_execution_status = first.execution_status
            retry_first_evidence_availability = first.evidence_availability
            retry_first_error_code = first.error_code
            first_in_scope = (
                first.normalized_args.get("order_id") == spec.order_id
                and (
                    retry_tool_name
                    not in {"search_after_sales_policy", "get_existing_logistics_tickets"}
                    or first.normalized_args.get("issue_type") == spec.issue_type.value
                )
            )
            second_in_scope = (
                second.normalized_args.get("order_id") == spec.order_id
                and (
                    retry_tool_name
                    not in {"search_after_sales_policy", "get_existing_logistics_tickets"}
                    or second.normalized_args.get("issue_type") == spec.issue_type.value
                )
            )
            retry_identity_match = (
                first.attempt_number == 1
                and second.attempt_number == 2
                and first_hash == second_hash
                and first.source_version == second.source_version
                and retry_recovery.retry_identity_hash == first_hash
                and first_in_scope
                and second_in_scope
                and retry_first_execution_status == "retryable_error"
                and retry_first_evidence_availability == "unavailable"
                and retry_first_error_code == spec.fault_error_code
            )
        else:
            retry_identity_match = False
    retry_attempt_numbers = tuple(int(row.attempt_number) for row in retry_rows)
    provider_events = [
        event
        for event in ledger.events
        if (
            event.smoke_id == spec.smoke_id
            and event.event_type == "budget_admission"
            and event.status == "admitted"
        )
    ]
    completed_provider_events = [
        event
        for event in ledger.events
        if event.smoke_id == spec.smoke_id and event.event_type == "provider_completed_response"
    ]
    fallback_used = any(
        event.smoke_id == spec.smoke_id
        and event.event_type == "candidate_boundary_rejection"
        and event.fallback_attempted
        for event in ledger.events
    )
    server_tool_call_count = len(accepted) + (1 if retry_recovery is not None else 0)
    candidate_accepted = accepted_call is not None
    toolnode_reached = (
        toolnode_node_present
        and actual_reads > 0
        and tool_requested_events >= actual_reads
        and tool_terminal_events >= actual_reads
        and len(typed_results) >= actual_reads
    )
    status: Literal["passed", "failed", "blocked"] = "failed"
    if error_category is None:
        if spec.smoke_id == "A0-01":
            passed = (
                len(provider_events) == 1
                and candidate_accepted
                and server_tool_call_count == 1
                and actual_reads == 1
                and toolnode_reached
                and accepted_call is not None
                and accepted_call.tool_name in spec.legal_tool_names
                and set(spec.minimum_missing_requirements).issubset(
                    set(initial_missing_required_codes)
                )
            )
        elif spec.smoke_id == "A0-02":
            passed = (
                candidate_accepted
                and server_tool_call_count == 1
                and actual_reads == 1
                and toolnode_reached
                and accepted_call is not None
                and accepted_call.tool_name in spec.legal_tool_names
                and len(initial_missing_required_codes) >= 2
                and set(spec.minimum_missing_requirements).issubset(
                    set(initial_missing_required_codes)
                )
            )
        else:
            passed = (
                len(provider_events) == 1
                and candidate_accepted
                and server_tool_call_count == 2
                and actual_reads == 2
                and toolnode_reached
                and retry_recovery is not None
                and retry_recovery.reason_code.value == "RETRYABLE_TOOL_FAILURE"
                and retry_identity_match is True
                and retry_attempt_numbers == (1, 2)
            )
        status = "passed" if passed else "failed"
        if not passed:
            error_category = RescueErrorCategory.ROUTER_DECISION
            error_code = "RESCUE_SMOKE_ACCEPTANCE_CONTRACT_FAILED"
    for row in actual_rows:
        if row.execution_status == "success":
            continue
        ledger.append(
            smoke_id=spec.smoke_id,
            run_id=run_id,
            event_type="toolnode_observation",
            category=RescueErrorCategory.TOOL_FAILURE,
            status="failed",
            attempt=int(row.attempt_number),
            error_code=row.error_code or "RESCUE_TOOL_FAILURE",
            actual_read_executions=1,
            tool_name=str(row.tool_name),
            canonical_arguments_hash=canonical_arguments_hash(row.normalized_args),
            trusted_scope_hash=scope_hash,
            source_version=row.source_version,
            result_hash=row.result_hash,
            execution_status=row.execution_status,
            evidence_availability=row.evidence_availability,
        )
    ledger.append(
        smoke_id=spec.smoke_id,
        run_id=run_id,
        event_type="toolnode_observation" if status == "passed" else "run_failed",
        category=RescueErrorCategory.TOOLNODE_OBSERVATION
        if status == "passed"
        else (error_category or RescueErrorCategory.LOCAL_TRANSPORT_CONFIGURATION_ERROR),
        status="passed" if status == "passed" else "failed",
        attempt=len(provider_events),
        error_code=None if status == "passed" else error_code,
        server_tool_call_count=server_tool_call_count,
        actual_read_executions=actual_reads,
        tool_name=accepted_call.tool_name if accepted_call is not None else retry_tool_name,
        canonical_arguments_hash=(
            accepted_call.canonical_arguments_hash if accepted_call is not None else retry_args_hash
        ),
        trusted_scope_hash=scope_hash,
        source_version=retry_source,
        retry_of_tool_call_id=retry_of,
        retry_attempt_number=2 if retry_recovery is not None else None,
        retry_identity_match=retry_identity_match,
        execution_status=retry_first_execution_status,
        evidence_availability=retry_first_evidence_availability,
        result_hash=(
            retry_rows[0].result_hash
            if retry_rows and retry_first_execution_status is not None
            else None
        ),
        router_route=retry_recovery.route.value if retry_recovery is not None else None,
        router_reason_code=retry_recovery.reason_code.value if retry_recovery is not None else None,
    )
    return RescueSmokeEvidence(
        smoke_id=spec.smoke_id,
        status=status,
        source_revision=manifest.source_revision,
        case_id=case_id,
        run_id=run_id,
        provider_calls=len(provider_events),
        selector_invocations=len(provider_events),
        provider_completed_responses=len(completed_provider_events),
        candidate_transport_fallback_used=fallback_used,
        candidate_accepted=candidate_accepted,
        candidate_action=accepted_call.action.value if accepted_call is not None else None,
        candidate_tool_name=accepted_call.tool_name if accepted_call is not None else None,
        candidate_requirement_names=(
            tuple(item.value for item in accepted_call.addresses)
            if accepted_call is not None
            else ()
        ),
        validator_status=(
            accepted_call.validation_status.value if accepted_call is not None else None
        ),
        validator_rejection_code=(
            accepted_call.rejection_code if accepted_call is not None else None
        ),
        server_tool_call_count=server_tool_call_count,
        toolnode_node_present=toolnode_node_present,
        toolnode_reached=toolnode_reached,
        typed_tool_result_count=len(typed_results),
        actual_read_executions=actual_reads,
        initial_missing_required_codes=initial_missing_required_codes,
        tool_call_ids=tuple(str(row.tool_call_id) for row in rows),
        tool_names=tuple(str(row.tool_name) for row in rows),
        progress_rebuild_count=len(progress_events),
        router_decision_count=len(recoveries),
        router_routes=tuple(item.route.value for item in recoveries),
        router_reason_codes=tuple(item.reason_code.value for item in recoveries),
        retry_of_tool_call_id=retry_of,
        retry_attempt_numbers=retry_attempt_numbers,
        retry_identity_match=retry_identity_match,
        retry_first_execution_status=retry_first_execution_status,
        retry_first_evidence_availability=retry_first_evidence_availability,
        retry_first_error_code=retry_first_error_code,
        retry_tool_name=retry_tool_name,
        retry_canonical_arguments_hash=retry_args_hash,
        retry_source_version=retry_source,
        trusted_scope_hash=scope_hash,
        error_category=error_category,
        error_code=error_code,
        event_types=tuple(event.event_type for event in events),
    )


async def run_rescue(project_root: Path | None = None) -> RescueRunReport:
    project = (project_root or _REPO_ROOT).expanduser().resolve()
    preflight = run_rescue_preflight(project)
    if preflight.source_revision is None:
        raise RescueExecutionError("rescue source revision is unavailable")
    manifest, manifest_digest, _ = prepare_rescue_manifest(
        project,
        source_revision=preflight.source_revision,
    )
    root = project / RESCUE_ROOT_RELATIVE / RESCUE_IDENTITY
    report_path = root / "smoke-summary.json"
    if report_path.exists():
        raise RescueIdentityAlreadyExecuted("rescue identity already has a smoke summary")
    ledger = RescueLedger(root / "security-ledger.jsonl")
    if ledger.events:
        raise RescueIdentityAlreadyExecuted("rescue identity already has ledger evidence")
    if not preflight.clean_source:
        preflight = preflight.model_copy(
            update={
                "status": "blocked",
                "error_category": RescueErrorCategory.LOCAL_TRANSPORT_CONFIGURATION_ERROR,
                "error_code": "RESCUE_SOURCE_TREE_NOT_CLEAN",
            }
        )
    ledger.append(
        smoke_id="MANIFEST",
        event_type="manifest_bound",
        category=RescueErrorCategory.PREFLIGHT,
        status="passed",
        attempt=0,
        error_code="RESCUE_MANIFEST_BOUND",
    )
    ledger.append(
        smoke_id="PREFLIGHT",
        event_type="preflight",
        category=(preflight.error_category or RescueErrorCategory.PREFLIGHT),
        status="passed" if preflight.status == "passed" else "blocked",
        attempt=0,
        error_code=preflight.error_code,
    )
    _write_once_json(root / "preflight.json", preflight.model_dump(mode="json"))
    if preflight.status != "passed":
        smokes = tuple(
            _blocked_smoke(
                spec,
                manifest.source_revision,
                preflight.error_code or "RESCUE_PREFLIGHT_BLOCKED",
            )
            for spec in manifest.smoke_cases
        )
    else:
        budget = _RescueCallBudget()
        results: list[RescueSmokeEvidence] = []
        a0_01_failed = False
        for index, spec in enumerate(manifest.smoke_cases):
            if a0_01_failed:
                results.append(
                    _blocked_smoke(
                        spec,
                        manifest.source_revision,
                        "RESCUE_STOP_AFTER_A0_01_FAILURE",
                    )
                )
                continue
            result = await _run_smoke(
                project_root=project,
                manifest=manifest,
                spec=spec,
                ledger=ledger,
                budget=budget,
                runtime_root=root / "runs" / spec.smoke_id,
            )
            results.append(result)
            if (
                index == 0
                and result.status != "passed"
                and result.error_category in _PRE_RESPONSE_FAILURE_CATEGORIES
            ):
                a0_01_failed = True
        smokes = tuple(results)
    provider_calls = sum(
        event.event_type == "budget_admission"
        and event.status == "admitted"
        for event in ledger.events
    )
    all_passed = preflight.status == "passed" and all(item.status == "passed" for item in smokes)
    report = RescueRunReport(
        status="V3_A0_RESCUE_GO" if all_passed else "V3_A0_RESCUE_NO_GO",
        source_revision=manifest.source_revision,
        manifest_digest=manifest_digest,
        preflight=preflight,
        provider_calls=provider_calls,
        ledger_event_count=len(ledger.events),
        ledger_sha256=ledger.sha256,
        all_failures_retained=True,
        historical_evidence_modified=False,
        smokes=smokes,
        completed_at=datetime.now(UTC),
    )
    _write_once_json(report_path, report.model_dump(mode="json"))
    return report


def run_rescue_sync(project_root: Path | None = None) -> RescueRunReport:
    return asyncio.run(run_rescue(project_root))


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run the authorized V3-A0 Live Rescue smoke")
    parser.add_argument("--project-root", type=Path, default=None)
    args = parser.parse_args(argv)
    report = run_rescue_sync(args.project_root)
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if report.status == "V3_A0_RESCUE_GO" else 2


__all__ = [
    "RESCUE_IDENTITY",
    "RESCUE_LABEL",
    "RESCUE_MODEL",
    "RESCUE_OUTPUT_TOKEN_CAP",
    "RESCUE_PROVIDER_CALL_CEILING",
    "RESCUE_TIMEOUT_SECONDS",
    "RescueErrorCategory",
    "RescueExecutionError",
    "RescueIdentityAlreadyExecuted",
    "RescueLedger",
    "RescueLedgerEvent",
    "RescueManifest",
    "RescuePreflight",
    "RescueRunReport",
    "RescueSelector",
    "RescueSmokeEvidence",
    "RescueSmokeSpec",
    "load_rescue_template",
    "main",
    "prepare_rescue_manifest",
    "run_rescue",
    "run_rescue_preflight",
    "run_rescue_sync",
]

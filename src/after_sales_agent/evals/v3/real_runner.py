# ruff: noqa: E501
"""Provider-guarded V3 Development execution adapters.

The module has two deliberately separate surfaces:

* :func:`build_development_plan` and :func:`run_activation_smoke` are safe to
  run while the formal Development identity is closed.  The smoke uses the
  production application composition root in explicit Mock mode and records
  zero provider/model calls.
* :class:`V3RealDevelopmentRunner` is the future formal path.  It requires a
  new, explicit authorization object and a caller-supplied production case
  input adapter.  It never fabricates a DecisionTrace, ToolCall, Evidence
  Progress, or Case Fact record; those are parsed from the production
  persistence boundary after the real application path completes.

The reserved PREP manifests are intentionally not executable identities.  A
future Owner-approved manifest/revision must be supplied before the formal
runner can open a Development store.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol, cast
from uuid import uuid4

from langchain_core.callbacks import get_usage_metadata_callback
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import Field, model_validator

from after_sales_agent.agents.models import (
    AgentObservationSelector,
    WorkflowObservationSelector,
    build_investigation_model,
)
from after_sales_agent.agents.tool_bindings import READ_TOOLS
from after_sales_agent.application.adaptive_core import (
    DecisionTraceRecord,
    RecoveryTraceRecord,
    StateTraceRecord,
)
from after_sales_agent.application.policy_router import PolicyDecision, PolicyRoute
from after_sales_agent.application.provider_budget import (
    ProviderBudgetAdmissionRejected,
    ProviderInvocationFailure,
    SelectorExecutionFailure,
    SelectorSchemaFailure,
)
from after_sales_agent.application.service import AfterSalesApplication
from after_sales_agent.config import (
    LIVE_MODEL_NAME,
    LLMMode,
    Settings,
    build_live_settings,
    build_mock_settings,
)
from after_sales_agent.domain.case_facts import (
    CaseFactAssertion,
    CaseFactError,
    CaseFactSnapshot,
    FactCode,
    FactValue,
)
from after_sales_agent.domain.models import InvestigationCase, Run, TrustedToolContext
from after_sales_agent.domain.state import IssueType
from after_sales_agent.evals.v3.budget import (
    PROVIDER_CALL_SEMANTICS,
    PROVIDER_RETRY_POLICY,
    TOKEN_THRESHOLD_SEMANTICS,
    DevelopmentBudgetBinding,
    DevelopmentBudgetLedger,
    DevelopmentBudgetLedgerError,
    DevelopmentBudgetRunAccounting,
)
from after_sales_agent.evals.v3.contracts import (
    V3_EVALUATED_AT,
    V3_PREP_IDENTITY,
    V3_SCHEMA_VERSION,
    V3A_EVAL_DEV_IDENTITY,
    V3B_EVAL_DEV_IDENTITY,
    V3Architecture,
    V3CaseSpec,
    V3Contract,
    V3DevelopmentManifest,
    V3DevelopmentReport,
    V3GateOutcome,
    V3GateTrace,
    V3Metrics,
    V3ProgressRebuild,
    V3RunRecord,
    V3ToolCall,
    V3ToolResultEnvelope,
    V3TypedTrace,
    expected_run_keys,
    fault_seed_hash,
    sha256_json,
)
from after_sales_agent.evals.v3.execution_package import (
    ExecutionPackageError,
    V3DevelopmentExecutionPackage,
    validate_execution_package_binding,
)
from after_sales_agent.evals.v3.graders import (
    V3GraderVerdict,
    V3GradingContext,
    _obligation_triggered,
    execute_v3_graders,
)
from after_sales_agent.evals.v3.matrix import load_manifests, load_matrix, validate_matrix
from after_sales_agent.evals.v3.production_fixtures import fixture_store_for_case
from after_sales_agent.evals.v3.report import (
    build_development_report,
    development_architecture_conclusion,
    validate_paired_records,
)
from after_sales_agent.evals.v3.store import V3DevelopmentStore
from after_sales_agent.events.models import EventEnvelope
from after_sales_agent.events.store import EventStore
from after_sales_agent.fixtures.catalog import FixtureStore, legacy_fixture_store
from after_sales_agent.storage.database import Database, create_engine_and_session, init_database
from after_sales_agent.storage.repositories import Repository
from after_sales_agent.tools.budget import ToolBudget
from after_sales_agent.tools.cache import CaseToolCache
from after_sales_agent.tools.contracts import EvidenceRef
from after_sales_agent.tools.service import READ_TOOL_NAMES

ACTIVATION_BASE_REVISION = "62e05b45fca714f1b6c64160b814adb172a8f39d"
ACTIVATION_PHASE = "Eval Activation"
LOCKED_PHASE = "V3-M2 Locked Eval"
ACTIVATION_SMOKE_STATUS = "ACTIVATION_SMOKE_NOT_DEVELOPMENT_MEASUREMENT"
PLAN_VERSION = "v3.eval.activation-plan.v1"
LOCKED_PLAN_VERSION = "v3.eval.locked-plan.v1"
EXECUTION_IDENTITY_PATTERN = r"^V3-(DEV-EXEC|LOCKED-EXEC)-[A-Z0-9][A-Z0-9-]{2,79}$"
CASE_SELECTOR_TURN_CEILING = ToolBudget.max_case_planning_turns
RUN_SELECTOR_TURN_CEILING = ToolBudget.max_run_planning_turns
OUTPUT_TOKEN_CAP_PER_INVOCATION = 512
_TERMINAL_TOOL_EVENTS = frozenset(
    {"tool_call_completed", "tool_call_failed", "tool_call_cache_hit", "tool_call_blocked"}
)
_TRACE_EVENTS = frozenset(
    {
        "decision_trace_record",
        "recovery_trace_record",
        "state_trace_record",
        *_TERMINAL_TOOL_EVENTS,
    }
)


class V3ExecutionNotAuthorized(RuntimeError):
    """Raised before any formal provider/model work can start."""


class V3ProductionTraceError(RuntimeError):
    """Raised when persisted production evidence cannot be typed/replayed."""


class V3ContractError(ValueError):
    """Raised when the committed V3 activation plan is internally inconsistent."""


class V3Plan(V3Contract):
    """Mechanical plan derived from the committed matrix and reserved manifests."""

    plan_version: str = PLAN_VERSION
    phase: str = ACTIVATION_PHASE
    schema_version: str = V3_SCHEMA_VERSION
    manifest_ids: tuple[str, ...] = Field(min_length=1)
    manifest_source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    manifest_digests: Mapping[str, str]
    matrix_case_count: int = Field(ge=0)
    paired_run_count: int = Field(ge=0)
    planned_run_count: int = Field(ge=0)
    architecture_run_counts: Mapping[str, int]
    repeat: int = Field(ge=1, le=3)
    timeout_seconds: float = Field(gt=0)
    selector_turn_ceiling_per_run: int = Field(ge=1, le=16)
    selector_turn_ceiling_per_case: int = Field(ge=1, le=16)
    authorized_provider_call_ceiling_per_run: int = Field(ge=0, le=16)
    authorized_provider_call_ceiling: int = Field(ge=0)
    provider_hard_ceiling: Literal[True] = True
    provider_call_semantics: Literal["pre_call_admitted_outer_ainvoke_attempt"] = (
        PROVIDER_CALL_SEMANTICS
    )
    provider_retry_policy: Literal[
        "sdk_retries_disabled_internal_transport_attempts_not_observable"
    ] = PROVIDER_RETRY_POLICY
    provider_calls_per_selector_turn: Mapping[str, int]
    provider_call_ceiling_by_architecture: Mapping[str, int]
    maximum_provider_calls: int = Field(ge=0)
    provider_call_ceiling_formula: str = Field(min_length=1)
    token_ceiling_config: str = Field(default="V3_TOKEN_CEILING", min_length=1)
    token_ceiling: int | None = Field(default=None, ge=1)
    token_ceiling_status: str = "requires_explicit_configuration"
    token_threshold_semantics: Literal[
        "cumulative_observed_total_tokens_post_response_stop"
    ] = TOKEN_THRESHOLD_SEMANTICS
    output_token_cap_per_invocation: int = Field(default=OUTPUT_TOKEN_CAP_PER_INVOCATION, gt=0)
    hard_token_ceiling: Literal[False] = False
    overshoot_bound_provable: Literal[False] = False
    formal_measurement_authorized: bool = False

    @model_validator(mode="after")
    def validate_plan(self) -> V3Plan:
        if self.phase not in {ACTIVATION_PHASE, LOCKED_PHASE}:
            raise ValueError("V3 plan phase is not registered")
        if self.paired_run_count != self.matrix_case_count * 2 * self.repeat:
            raise ValueError("paired run count does not match matrix/repeat")
        if self.planned_run_count != self.paired_run_count:
            raise ValueError("planned run count must equal paired run count")
        if dict(self.architecture_run_counts) != {
            "agent": self.matrix_case_count * self.repeat,
            "workflow": self.matrix_case_count * self.repeat,
        }:
            raise ValueError("architecture run distribution is not paired")
        if set(self.provider_calls_per_selector_turn) != {"agent", "workflow"}:
            raise ValueError("provider-call derivation must name both selector adapters")
        if set(self.provider_call_ceiling_by_architecture) != {"agent", "workflow"}:
            raise ValueError("provider-call ceilings must name both architectures")
        expected_provider_ceiling = {
            architecture: self.architecture_run_counts[architecture]
            * self.selector_turn_ceiling_per_run
            * self.provider_calls_per_selector_turn[architecture]
            for architecture in ("agent", "workflow")
        }
        if dict(self.provider_call_ceiling_by_architecture) != expected_provider_ceiling:
            raise ValueError("provider-call ceilings do not match the preregistered formula")
        if self.maximum_provider_calls != sum(expected_provider_ceiling.values()):
            raise ValueError("maximum provider calls do not match architecture ceilings")
        if self.authorized_provider_call_ceiling != self.maximum_provider_calls:
            raise ValueError("authorized execution provider ceiling differs from plan maximum")
        if any(value < 0 for value in self.provider_calls_per_selector_turn.values()):
            raise ValueError("provider calls per selector turn cannot be negative")
        if self.token_ceiling is not None and self.token_ceiling_status != "configured":
            raise ValueError("configured token ceiling must be marked configured")
        if self.token_ceiling is None and self.token_ceiling_status != "requires_explicit_configuration":
            raise ValueError("missing token ceiling must remain explicitly unconfigured")
        if self.output_token_cap_per_invocation != OUTPUT_TOKEN_CAP_PER_INVOCATION:
            raise ValueError("V3 selector output cap must remain the registered value")
        return self


class V3ExecutionAuthorization(V3Contract):
    """All explicit controls needed before a formal provider-backed run."""

    execution_identity: str = Field(pattern=EXECUTION_IDENTITY_PATTERN)
    authorization_flag: bool
    live_mode: bool
    credential_name: str = "DEEPSEEK_API_KEY"
    credential_present: bool
    clean_source: bool
    current_source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    evaluated_source_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    manifest_version_binding: bool
    manifest_digests: Mapping[str, str]
    plan_version: str = PLAN_VERSION
    token_ceiling: int = Field(gt=0)
    provider_call_ceiling: int = Field(default=0, ge=0)
    provider_call_ceiling_per_run: int = Field(default=0, ge=0, le=16)
    provider_hard_ceiling: Literal[True] = True
    provider_call_semantics: Literal["pre_call_admitted_outer_ainvoke_attempt"] = (
        PROVIDER_CALL_SEMANTICS
    )
    provider_retry_policy: Literal[
        "sdk_retries_disabled_internal_transport_attempts_not_observable"
    ] = PROVIDER_RETRY_POLICY
    token_threshold_semantics: Literal[
        "cumulative_observed_total_tokens_post_response_stop"
    ] = TOKEN_THRESHOLD_SEMANTICS
    token_threshold_semantics_accepted: bool = False
    output_token_cap_per_invocation: int = Field(default=OUTPUT_TOKEN_CAP_PER_INVOCATION, gt=0)
    hard_token_ceiling: Literal[False] = False
    timeout_seconds: float = Field(default=30.0, gt=0)
    repeat: int = Field(default=1, ge=1, le=3)

    @model_validator(mode="after")
    def validate_credential_name(self) -> V3ExecutionAuthorization:
        if self.credential_name != "DEEPSEEK_API_KEY":
            raise ValueError("formal V3 execution only accepts the named DeepSeek credential")
        return self


class V3Preflight(V3Contract):
    """Provider-free preflight result; a NO-GO is a valid closed state."""

    phase: str = ACTIVATION_PHASE
    status: str
    formal_execution_ready: bool
    activation_base_revision: str
    current_source_revision: str
    source_tree_clean: bool
    manifest_source_revision: str
    manifest_formal_authorized: bool
    matrix_case_count: int
    planned_run_count: int
    exact_path_ready: bool = False
    live_settings_valid: bool = False
    live_model_constructed: bool = False
    agent_selector_constructed: bool = False
    workflow_settings_valid: bool = False
    workflow_selector_constructed: bool = False
    credential_present: bool = False
    provider_calls: int = 0
    model_calls: int = 0
    checks: Mapping[str, bool]
    plan: V3Plan

    @model_validator(mode="after")
    def validate_zero_calls(self) -> V3Preflight:
        if self.provider_calls != 0 or self.model_calls != 0:
            raise ValueError("activation preflight must not call a provider/model")
        if self.formal_execution_ready and self.status != "GO_FORMAL_EXECUTION_AUTHORIZATION_READY":
            raise ValueError("ready preflight status is inconsistent")
        if not self.formal_execution_ready and self.status != "NO_GO_FORMAL_DEVELOPMENT_NOT_AUTHORIZED":
            raise ValueError("closed preflight status is inconsistent")
        if self.exact_path_ready and (
            not self.live_settings_valid
            or not self.live_model_constructed
            or not self.agent_selector_constructed
            or not self.workflow_settings_valid
            or not self.workflow_selector_constructed
        ):
            raise ValueError("exact-path readiness status is inconsistent")
        return self


class V3ProductionCaseInput(V3Contract):
    """One committed, fixture-bound input for the production composition root."""

    schema_version: str = "v3.production-case-input.v1"
    scenario_id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    order_id: str = Field(pattern=r"^ORD-[A-Z0-9-]+$")
    issue_type: IssueType
    customer_message: str = Field(min_length=1)
    customer_messages: tuple[str, ...] = Field(default_factory=tuple, min_length=1, max_length=3)
    fixture_revision: str = Field(min_length=1)
    fixture_profile: str = Field(default="default", min_length=1)
    source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    fault_seed: str = Field(min_length=1)
    evaluated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def default_customer_messages(cls, value: Any) -> Any:
        if isinstance(value, Mapping) and not value.get("customer_messages"):
            return {**value, "customer_messages": (value.get("customer_message"),)}
        return value

    @model_validator(mode="after")
    def validate_clock_and_scope(self) -> V3ProductionCaseInput:
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("production case evaluated_at must be timezone-aware")
        messages = self.customer_messages
        if messages[0] != self.customer_message:
            raise ValueError("customer_messages must begin with customer_message")
        if any(self.order_id not in message for message in messages):
            raise ValueError("production case message must retain its explicit order scope")
        return self


PRODUCTION_CASE_INPUTS_RELATIVE_PATH = "evals/v3/production-case-inputs.json"


@dataclass(frozen=True, slots=True)
class ProductionRuntime:
    root: Path
    database: Database
    events: EventStore
    application: AfterSalesApplication
    checkpointer_context: Any | None = None

    def close(self) -> None:
        self.database.engine.dispose()

    async def aclose(self) -> None:
        if self.checkpointer_context is not None:
            context = self.checkpointer_context
            object.__setattr__(self, "checkpointer_context", None)
            await context.__aexit__(None, None, None)
        self.database.engine.dispose()

    def __enter__(self) -> ProductionRuntime:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    async def __aenter__(self) -> ProductionRuntime:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()


@dataclass(frozen=True, slots=True)
class ProductionTraceEvidence:
    conversation_id: str
    case_id: str
    run_id: str
    trace: V3TypedTrace
    final_outcome: V3GateOutcome
    safety_gate_pass: bool
    grader_verdicts: tuple[V3GraderVerdict, ...]
    started_at: datetime
    completed_at: datetime
    latency_ms: float
    model_calls: int
    provider_calls: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    run_status: str = "completed"
    error_code: str | None = None
    error_class: str = "none"
    budget_accounting: DevelopmentBudgetRunAccounting | None = None
    toolnode_reached: bool = False


class V3ActivationSmokeRun(V3Contract):
    architecture: V3Architecture
    selector_kinds: tuple[str, ...] = Field(min_length=1)
    status: str
    measurement_status: str = ACTIVATION_SMOKE_STATUS
    provider_calls: int = 0
    model_calls: int = 0
    case_fact_snapshot_present: bool
    persisted_trace_event_types: tuple[str, ...]
    persisted_tool_call_count: int = Field(ge=0)
    grader_ids: tuple[str, ...]
    grader_pass_count: int = Field(ge=0)
    grader_failure_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_smoke(self) -> V3ActivationSmokeRun:
        if self.measurement_status != ACTIVATION_SMOKE_STATUS:
            raise ValueError("activation smoke must remain outside Development measurement")
        if self.provider_calls != 0 or self.model_calls != 0:
            raise ValueError("activation smoke must prove zero provider/model calls")
        if self.selector_kinds != (self.architecture,):
            raise ValueError("activation smoke selector does not match its architecture")
        required = {"decision_trace_record", "recovery_trace_record", "state_trace_record", "evidence_progress_rebuilt", "evidence_gate_evaluated"}
        if not required.issubset(self.persisted_trace_event_types):
            raise ValueError("activation smoke did not persist the complete production trace surface")
        if self.grader_pass_count + self.grader_failure_count != len(self.grader_ids):
            raise ValueError("activation grader counts do not cover the executed grader set")
        return self


class V3ActivationSmokeReport(V3Contract):
    phase: str = ACTIVATION_PHASE
    status: str = "passed"
    measurement_status: str = ACTIVATION_SMOKE_STATUS
    provider_calls: int = 0
    model_calls: int = 0
    shared_input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    production_components: tuple[str, ...]
    runs: tuple[V3ActivationSmokeRun, ...] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def validate_report(self) -> V3ActivationSmokeReport:
        if self.provider_calls != 0 or self.model_calls != 0:
            raise ValueError("activation report must retain zero provider/model calls")
        if {item.architecture for item in self.runs} != {"agent", "workflow"}:
            raise ValueError("activation smoke must include both selector adapters")
        if any(item.status != "passed" or item.grader_failure_count for item in self.runs):
            raise ValueError("activation smoke report cannot pass with a failed run or grader")
        return self


class ProductionRunAdapter(Protocol):
    async def execute(
        self,
        *,
        case: V3CaseSpec,
        case_input: V3ProductionCaseInput,
        architecture: V3Architecture,
        repetition: int,
        timeout_seconds: float,
        logical_run_key: str | None = None,
        budget_ledger: DevelopmentBudgetLedger | None = None,
    ) -> ProductionTraceEvidence: ...


def _project_root(root: Path | None = None) -> Path:
    return root or Path(__file__).resolve().parents[4]


def _activation_root(root: Path | None) -> Path:
    return root.expanduser().resolve() if root is not None else Path(tempfile.mkdtemp(prefix="v3-activation-smoke-"))


def _git_value(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise V3ContractError("git source binding is unavailable") from exc
    return result.stdout.strip()


def current_source_revision(root: Path | None = None) -> str:
    return _git_value(_project_root(root), "rev-parse", "HEAD")


def source_tree_is_clean(
    root: Path | None = None,
    *,
    allowed_paths: Sequence[Path] = (),
) -> bool:
    project = _project_root(root)
    status = _git_value(project, "status", "--porcelain", "--untracked-files=all")
    if not status:
        return True
    allowed = {path.expanduser().resolve() for path in allowed_paths}
    if not allowed:
        return False
    for line in status.splitlines():
        if len(line) < 4:
            return False
        status_path = line[3:]
        if " -> " in status_path:
            return False
        if (project / status_path).resolve() not in allowed:
            return False
    return True


def _manifest_digest(manifest: V3DevelopmentManifest) -> str:
    return sha256_json(manifest.model_dump(mode="json"))


def build_development_plan(
    root: Path | None = None,
    *,
    token_ceiling: int | None = None,
) -> V3Plan:
    """Derive a plan solely from the committed matrix/manifests.

    This function performs no Settings construction and no provider/model work.
    """

    project = _project_root(root)
    validate_matrix()
    cases = load_matrix(project)
    manifests = load_manifests(project)
    cases_by_id = {case.scenario_id: case for case in cases}
    expected = expected_run_keys(manifests, cases_by_id)
    if len(cases) != 32 or len(expected) != 64:
        raise V3ContractError("committed V3 plan is not the expected 32-case/64-run matrix")
    shared_values = {
        (
            case.shared_fields.timeout_seconds,
            case.shared_fields.repeat,
            case.shared_fields.selector_turn_ceiling,
            case.shared_fields.provider_call_ceiling,
            case.shared_fields.output_token_cap_per_invocation,
            case.shared_fields.token_threshold_semantics,
            case.shared_fields.hard_token_ceiling,
            case.shared_fields.overshoot_bound_provable,
        )
        for case in cases
    }
    if len(shared_values) != 1:
        raise V3ContractError("committed matrix has asymmetric shared budget/timeout fields")
    (
        timeout_seconds,
        repeat,
        selector_ceiling,
        provider_ceiling,
        output_token_cap,
        token_threshold_semantics,
        hard_token_ceiling,
        overshoot_bound_provable,
    ) = next(iter(shared_values))
    if selector_ceiling != RUN_SELECTOR_TURN_CEILING:
        raise V3ContractError("committed run selector ceiling differs from production ToolBudget")
    if provider_ceiling != selector_ceiling:
        raise V3ContractError("Agent provider ceiling must cover one call per selector turn")
    if output_token_cap != OUTPUT_TOKEN_CAP_PER_INVOCATION:
        raise V3ContractError("committed matrix output-token cap differs from production model cap")
    if token_threshold_semantics != TOKEN_THRESHOLD_SEMANTICS:
        raise V3ContractError("committed matrix token-threshold semantics differ from the plan")
    if hard_token_ceiling is not False or overshoot_bound_provable is not False:
        raise V3ContractError("committed matrix cannot claim a hard token ceiling")
    architecture_counts = {
        architecture: sum(
            len(manifest.case_ids) * manifest.planned_repetitions
            for manifest in manifests
        )
        for architecture in ("agent", "workflow")
    }
    provider_per_turn = {"agent": 1, "workflow": 0}
    provider_by_architecture = {
        architecture: architecture_counts[architecture] * selector_ceiling * calls_per_turn
        for architecture, calls_per_turn in provider_per_turn.items()
    }
    maximum_provider_calls = sum(provider_by_architecture.values())
    formula = (
        f"Agent: {architecture_counts['agent']} runs x {selector_ceiling} selector turns/run x "
        f"1 provider call/selector turn = {provider_by_architecture['agent']}; "
        f"Workflow: {architecture_counts['workflow']} runs x {selector_ceiling} x 0 = "
        f"{provider_by_architecture['workflow']}; paired maximum = {maximum_provider_calls}"
    )
    return V3Plan(
        manifest_ids=tuple(item.manifest_id for item in manifests),
        manifest_source_revision=manifests[0].source_revision,
        manifest_digests={item.manifest_id: _manifest_digest(item) for item in manifests},
        matrix_case_count=len(cases),
        paired_run_count=len(expected),
        planned_run_count=len(expected),
        architecture_run_counts=architecture_counts,
        repeat=repeat,
        timeout_seconds=timeout_seconds,
        selector_turn_ceiling_per_run=selector_ceiling,
        selector_turn_ceiling_per_case=CASE_SELECTOR_TURN_CEILING,
        authorized_provider_call_ceiling_per_run=provider_ceiling,
        authorized_provider_call_ceiling=maximum_provider_calls,
        provider_hard_ceiling=True,
        provider_call_semantics=PROVIDER_CALL_SEMANTICS,
        provider_retry_policy=PROVIDER_RETRY_POLICY,
        provider_calls_per_selector_turn=provider_per_turn,
        provider_call_ceiling_by_architecture=provider_by_architecture,
        maximum_provider_calls=maximum_provider_calls,
        provider_call_ceiling_formula=formula,
        token_ceiling=token_ceiling,
        token_ceiling_status=("configured" if token_ceiling is not None else "requires_explicit_configuration"),
        token_threshold_semantics=TOKEN_THRESHOLD_SEMANTICS,
        output_token_cap_per_invocation=output_token_cap,
        hard_token_ceiling=hard_token_ceiling,
        overshoot_bound_provable=overshoot_bound_provable,
        formal_measurement_authorized=False,
    )


def run_preflight(root: Path | None = None) -> V3Preflight:
    """Run the closed, zero-provider preflight for a future formal execution."""

    project = _project_root(root)
    plan = build_development_plan(project)
    manifests = load_manifests(project)
    current = current_source_revision(project)
    clean = source_tree_is_clean(project)
    manifest_authorized = all(item.formal_measurement_authorized for item in manifests)
    manifest_source_bound = all(
        item.source_revision == plan.manifest_source_revision for item in manifests
    )
    evaluated_source_is_separate = current != plan.manifest_source_revision
    exact_path_ready = False
    live_settings_valid = False
    live_model_constructed = False
    agent_selector_constructed = False
    workflow_settings_valid = False
    workflow_selector_constructed = False
    credential_present = False
    try:
        probe_adapter = ProductionInvestigationAdapter(
            project_root=project,
            root_factory=lambda _architecture, _case_input: project / "var" / "v3" / "readiness",
        )
        probe_case = load_matrix(project)[0]
        probe_input = load_production_case_inputs(project)[probe_case.scenario_id]
        with tempfile.TemporaryDirectory(prefix="v3-formal-readiness-") as temporary_root:
            readiness_root = Path(temporary_root)
            workflow_settings = probe_adapter.build_settings(
                architecture="workflow",
                case_input=probe_input,
                root=readiness_root / "workflow",
                timeout_seconds=plan.timeout_seconds,
            )
            credential_present = bool(workflow_settings.deepseek_api_key)
            workflow_settings_valid = workflow_settings.llm_mode is LLMMode.MOCK
            WorkflowObservationSelector()
            workflow_selector_constructed = True

            live_settings = probe_adapter.build_settings(
                architecture="agent",
                case_input=probe_input,
                root=readiness_root / "agent",
                timeout_seconds=plan.timeout_seconds,
            )
            credential_present = bool(live_settings.deepseek_api_key)
            live_settings_valid = (
                live_settings.llm_mode is LLMMode.LIVE
                and live_settings.deepseek_model == LIVE_MODEL_NAME
                and bool(live_settings.deepseek_api_key)
            )
            live_model = build_investigation_model(live_settings, READ_TOOLS)
            live_model_constructed = True
            AgentObservationSelector(live_model)
            agent_selector_constructed = True
    except Exception:
        # The preflight is deliberately a closed inspection surface.  Keep the
        # failure as booleans only; never expose credential or provider details.
        pass
    exact_path_ready = (
        live_settings_valid
        and live_model_constructed
        and agent_selector_constructed
        and workflow_settings_valid
        and workflow_selector_constructed
    )
    # This command remains a provider-free inspection surface.  It does not
    # open formal execution merely because the checkout is clean; the separate
    # write-once Owner package is the only formal opening authority.
    binding = clean and manifest_source_bound and manifest_authorized
    checks = {
        "matrix_32_cases": plan.matrix_case_count == 32,
        "paired_runs_64": plan.planned_run_count == 64,
        "repeat_1": plan.repeat == 1,
        "timeout_30_seconds": plan.timeout_seconds == 30.0,
        "source_tree_clean": clean,
        "manifest_source_revision_matches_reserved_manifests": manifest_source_bound,
        "evaluated_source_revision_is_separate": evaluated_source_is_separate,
        "formal_manifest_authorized": manifest_authorized,
        "exact_live_settings_valid": live_settings_valid,
        "exact_live_model_constructed": live_model_constructed,
        "exact_agent_selector_constructed": agent_selector_constructed,
        "exact_workflow_settings_valid": workflow_settings_valid,
        "exact_workflow_selector_constructed": workflow_selector_constructed,
        "exact_path_ready": exact_path_ready,
        "write_once_execution_package_required": True,
        "execution_package_present": False,
        "formal_execution_identity_not_consumed": True,
        "provider_calls": True,
        "model_calls": True,
    }
    return V3Preflight(
        status=(
            "GO_FORMAL_EXECUTION_AUTHORIZATION_READY"
            if binding and exact_path_ready
            else "NO_GO_FORMAL_DEVELOPMENT_NOT_AUTHORIZED"
        ),
        formal_execution_ready=binding and exact_path_ready,
        activation_base_revision=ACTIVATION_BASE_REVISION,
        current_source_revision=current,
        source_tree_clean=clean,
        manifest_source_revision=plan.manifest_source_revision,
        manifest_formal_authorized=manifest_authorized,
        matrix_case_count=plan.matrix_case_count,
        planned_run_count=plan.planned_run_count,
        exact_path_ready=exact_path_ready,
        live_settings_valid=live_settings_valid,
        live_model_constructed=live_model_constructed,
        agent_selector_constructed=agent_selector_constructed,
        workflow_settings_valid=workflow_settings_valid,
        workflow_selector_constructed=workflow_selector_constructed,
        credential_present=credential_present,
        checks=checks,
        plan=plan,
    )


def validate_execution_authorization(
    authorization: V3ExecutionAuthorization,
    *,
    plan: V3Plan,
    manifests: Sequence[V3DevelopmentManifest],
    execution_package: V3DevelopmentExecutionPackage | None = None,
) -> None:
    """Fail closed before opening a provider-backed Development store."""

    errors: list[str] = []
    if not authorization.authorization_flag:
        errors.append("explicit authorization flag is false")
    if not authorization.live_mode:
        errors.append("LLM_MODE=live is required")
    if not authorization.credential_present:
        errors.append("named credential presence is false")
    if not authorization.clean_source:
        errors.append("source tree is not clean")
    evaluated_revision = authorization.evaluated_source_revision
    if execution_package is not None:
        if execution_package.execution_identity != authorization.execution_identity:
            errors.append("authorization package identity differs")
        if evaluated_revision != execution_package.evaluated_source_revision:
            errors.append("authorization evaluated source differs from package")
        if authorization.current_source_revision != execution_package.evaluated_source_revision:
            errors.append("current source revision does not match evaluated package source")
        if authorization.source_revision != execution_package.manifest_source_revision:
            errors.append("authorization manifest source differs from package")
        if execution_package.manifest_source_revision != plan.manifest_source_revision:
            errors.append("execution manifest source differs from committed plan")
    else:
        if plan.phase == LOCKED_PHASE:
            if authorization.evaluated_source_revision is None:
                errors.append("Locked authorization is missing its evaluated source revision")
            elif authorization.current_source_revision != authorization.evaluated_source_revision:
                errors.append("current source revision does not match the frozen evaluated source")
            if authorization.source_revision != plan.manifest_source_revision:
                errors.append("Locked dataset source revision differs from the plan")
        else:
            # Retain the old object-level closed behavior for callers that have
            # not supplied the new package.  The formal CLI never uses this branch.
            if authorization.current_source_revision != authorization.source_revision:
                errors.append("current source revision does not match execution binding")
            if authorization.source_revision != plan.manifest_source_revision:
                errors.append("execution source revision does not match committed manifest revision")
    if not authorization.manifest_version_binding:
        errors.append("manifest/version binding is not explicit")
    if dict(authorization.manifest_digests) != dict(plan.manifest_digests):
        errors.append("manifest digest binding differs from the committed plan")
    if authorization.plan_version != plan.plan_version:
        errors.append("plan version binding differs")
    if authorization.provider_call_ceiling != plan.authorized_provider_call_ceiling:
        errors.append("execution provider-call ceiling differs from the plan")
    if authorization.provider_call_ceiling_per_run != plan.authorized_provider_call_ceiling_per_run:
        errors.append("per-run provider-call ceiling differs from the plan")
    if authorization.provider_hard_ceiling != plan.provider_hard_ceiling:
        errors.append("provider hard-ceiling binding differs from the plan")
    if authorization.provider_call_semantics != plan.provider_call_semantics:
        errors.append("provider-call semantics differ from the plan")
    if authorization.provider_retry_policy != plan.provider_retry_policy:
        errors.append("provider retry policy differs from the plan")
    if authorization.token_threshold_semantics != plan.token_threshold_semantics:
        errors.append("token threshold semantics differ from the plan")
    if not authorization.token_threshold_semantics_accepted:
        errors.append("Owner acceptance of cumulative observed-token stop semantics is missing")
    if authorization.output_token_cap_per_invocation != plan.output_token_cap_per_invocation:
        errors.append("output-token cap binding differs from the plan")
    if authorization.timeout_seconds != plan.timeout_seconds:
        errors.append("timeout binding differs from the plan")
    if authorization.repeat != plan.repeat:
        errors.append("repeat binding differs from the plan")
    if execution_package is None:
        if plan.token_ceiling is None:
            errors.append("committed plan has no configured token threshold")
        elif authorization.token_ceiling != plan.token_ceiling:
            errors.append("token ceiling binding differs from the plan")
        if not plan.formal_measurement_authorized:
            errors.append("activation plan is not formally authorized")
        if any(not item.formal_measurement_authorized for item in manifests):
            errors.append("reserved manifest is not formally authorized")
    else:
        if authorization.token_ceiling != execution_package.token_threshold:
            errors.append("token threshold differs from authorization package")
        if authorization.provider_call_ceiling != execution_package.authorized_provider_call_ceiling:
            errors.append("provider ceiling differs from authorization package")
        if not authorization.token_threshold_semantics_accepted:
            errors.append("Owner token-semantics acceptance is missing")
    if authorization.execution_identity == V3_PREP_IDENTITY:
        errors.append("PREP identity cannot open formal Development")
    if errors:
        raise V3ExecutionNotAuthorized("; ".join(errors))


def _development_budget_binding(
    *,
    plan: V3Plan,
    authorization: V3ExecutionAuthorization,
    execution_identity: str,
) -> DevelopmentBudgetBinding:
    """Bind the durable ledger to the exact future execution identity."""

    return DevelopmentBudgetBinding(
        execution_identity=execution_identity,
        # Formal budgets bind to the evaluated implementation commit.  The
        # reserved manifest source revision remains the dataset/matrix
        # identity and is intentionally not reused as the runtime source.
        source_revision=(authorization.evaluated_source_revision or authorization.current_source_revision),
        manifest_digests=dict(plan.manifest_digests),
        plan_version=plan.plan_version,
        authorized_provider_call_ceiling=plan.authorized_provider_call_ceiling,
        authorized_provider_call_ceiling_per_run=plan.authorized_provider_call_ceiling_per_run,
        provider_hard_ceiling=plan.provider_hard_ceiling,
        provider_call_semantics=plan.provider_call_semantics,
        provider_retry_policy=plan.provider_retry_policy,
        token_threshold=authorization.token_ceiling,
        token_threshold_semantics=plan.token_threshold_semantics,
        output_token_cap_per_invocation=plan.output_token_cap_per_invocation,
        hard_token_ceiling=plan.hard_token_ceiling,
        overshoot_bound_provable=plan.overshoot_bound_provable,
    )


def build_production_runtime(
    *,
    root: Path,
    architecture: V3Architecture,
    settings: Settings | None = None,
    fixtures: FixtureStore | None = None,
    fault_seed: str = "activation-smoke",
    evaluated_at: datetime | None = None,
) -> ProductionRuntime:
    """Build the existing application composition root in an isolated root."""

    root = root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if settings is None:
        clock = evaluated_at or datetime.fromisoformat(V3_EVALUATED_AT)
        settings = Settings(
            _env_file=None,
            LLM_MODE="mock",
            POLICY_RETRIEVAL_MODE="fake_test",
            DATABASE_URL=f"sqlite:///{(root / 'application.sqlite').as_posix()}",
            LANGGRAPH_CHECKPOINT_URL=root / "langgraph-checkpoints.sqlite",
            POLICY_INDEX_ROOT=root / "policy-index",
            POLICY_RETRIEVAL_EVAL_ARTIFACT_ROOT=root / "retrieval-evals",
            EVAL_ARTIFACT_ROOT=root / "eval-artifacts",
            SCENARIO_FAULT_SEED=fault_seed,
            SCENARIO_EVALUATED_AT=clock,
        )
    database = create_engine_and_session(settings.database_url)
    init_database(database.engine)
    events = EventStore(database.session_factory)
    application = AfterSalesApplication(
        settings=settings,
        fixtures=fixtures or legacy_fixture_store(),
        session_factory=database.session_factory,
        events=events,
        graph_checkpointer=None,
        investigation_strategy=architecture,
    )
    return ProductionRuntime(root=root, database=database, events=events, application=application)


def _prepare_async_runtime_root(root: Path) -> Path:
    resolved = root.expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


async def build_async_production_runtime(
    *,
    root: Path,
    architecture: V3Architecture,
    settings: Settings | None = None,
    fixtures: FixtureStore | None = None,
    fault_seed: str = "activation-smoke",
    evaluated_at: datetime | None = None,
) -> ProductionRuntime:
    """Build the production composition root with an async SQLite checkpoint store."""

    root = _prepare_async_runtime_root(root)
    if settings is None:
        clock = evaluated_at or datetime.fromisoformat(V3_EVALUATED_AT)
        settings = Settings(
            _env_file=None,
            LLM_MODE="mock",
            POLICY_RETRIEVAL_MODE="fake_test",
            DATABASE_URL=f"sqlite:///{(root / 'application.sqlite').as_posix()}",
            LANGGRAPH_CHECKPOINT_URL=root / "langgraph-checkpoints.sqlite",
            POLICY_INDEX_ROOT=root / "policy-index",
            POLICY_RETRIEVAL_EVAL_ARTIFACT_ROOT=root / "retrieval-evals",
            EVAL_ARTIFACT_ROOT=root / "eval-artifacts",
            SCENARIO_FAULT_SEED=fault_seed,
            SCENARIO_EVALUATED_AT=clock,
        )
    database = create_engine_and_session(settings.database_url)
    init_database(database.engine)
    events = EventStore(database.session_factory)
    checkpoint_context = AsyncSqliteSaver.from_conn_string(
        str(settings.langgraph_checkpoint_url)
    )
    try:
        checkpointer = await checkpoint_context.__aenter__()
        await checkpointer.setup()
        application = AfterSalesApplication(
            settings=settings,
            fixtures=fixtures or legacy_fixture_store(),
            session_factory=database.session_factory,
            events=events,
            graph_checkpointer=checkpointer,
            investigation_strategy=architecture,
        )
    except BaseException:
        await checkpoint_context.__aexit__(None, None, None)
        database.engine.dispose()
        raise
    return ProductionRuntime(
        root=root,
        database=database,
        events=events,
        application=application,
        checkpointer_context=checkpoint_context,
    )


def _typed_case_facts(
    runtime: ProductionRuntime,
    case_id: str,
) -> tuple[tuple[CaseFactAssertion, ...], tuple[CaseFactSnapshot, ...], tuple[Any, ...], tuple[Any, ...]]:
    snapshot = runtime.application.case_facts.load_snapshot(case_id)
    assertions = runtime.application.case_facts.load_assertions(case_id)
    with runtime.database.session_factory() as session:
        repository = Repository(session)
        questions = tuple(repository.list_case_fact_questions(case_id))
        consumptions = tuple(repository.list_case_fact_message_consumptions(case_id))
    return assertions, (snapshot,), questions, consumptions


def _terminal_event_map(events: Sequence[EventEnvelope]) -> dict[str, EventEnvelope]:
    mapped: dict[str, EventEnvelope] = {}
    for event in events:
        if event.event_type not in _TERMINAL_TOOL_EVENTS:
            continue
        call_id = event.payload.get("tool_call_id")
        if not isinstance(call_id, str) or not call_id:
            raise V3ProductionTraceError("terminal ToolCall event lacks tool_call_id")
        if call_id in mapped:
            raise V3ProductionTraceError("one ToolCall has more than one terminal event")
        mapped[call_id] = event
    return mapped


def _typed_tool_calls(
    runtime: ProductionRuntime,
    *,
    case_id: str,
    run_id: str,
    events: Sequence[EventEnvelope],
) -> tuple[V3ToolCall, ...]:
    terminal_events = _terminal_event_map(events)
    sequence_by_call: dict[str, int] = {}
    sequence = 0
    for event in events:
        if event.event_type not in _TRACE_EVENTS:
            continue
        sequence += 1
        call_id = event.payload.get("tool_call_id")
        if event.event_type in _TERMINAL_TOOL_EVENTS and isinstance(call_id, str):
            sequence_by_call[call_id] = sequence
    with runtime.database.session_factory() as session:
        rows = Repository(session).list_tool_calls(run_id=run_id)
    calls: list[V3ToolCall] = []
    for row in rows:
        if row.case_id != case_id or row.run_id != run_id:
            raise V3ProductionTraceError("persisted ToolCall escaped its production Case/Run")
        if (
            row.execution_status is None
            or row.evidence_availability is None
            or row.result_envelope is None
            or row.result_hash is None
            or row.source_version is None
        ):
            raise V3ProductionTraceError("production ToolCall is not durably completed")
        terminal_event = terminal_events.get(row.tool_call_id)
        if terminal_event is None:
            raise V3ProductionTraceError("production ToolCall has no correlated terminal event")
        payload = terminal_event.payload
        envelope = V3ToolResultEnvelope.model_validate(
            {
                field: row.result_envelope[field]
                for field in V3ToolResultEnvelope.model_fields
                if field in row.result_envelope
            }
        )
        if envelope.result_hash != row.result_hash:
            raise V3ProductionTraceError("ToolCall/result envelope hash mismatch")
        refs = tuple(EvidenceRef.model_validate(item) for item in terminal_event.evidence_refs)
        calls.append(
            V3ToolCall(
                tool_call_id=row.tool_call_id,
                case_id=case_id,
                run_id=run_id,
                tool_name=row.tool_name,
                normalized_args=dict(row.normalized_args),
                planning_turn=row.planning_turn,
                attempt_number=row.attempt_number,
                actual_execution=row.actual_execution,
                cache_hit=row.cache_hit,
                blocked=row.blocked,
                execution_status=cast(Any, row.execution_status),
                evidence_availability=cast(Any, row.evidence_availability),
                result_envelope=envelope,
                result_hash=row.result_hash,
                source_version=row.source_version,
                retryable=row.retryable,
                trace_sequence=sequence_by_call.get(row.tool_call_id, row.planning_turn),
                progress_status=None,
                budget_before_actual_reads=int(payload.get("budget_before_actual_reads", 0)),
                budget_after_actual_reads=int(payload.get("budget_after_actual_reads", 0)),
                evidence_refs=refs,
            )
        )
    return tuple(calls)


def _normalized_trace_sequences(
    events: Sequence[EventEnvelope],
) -> tuple[tuple[DecisionTraceRecord, ...], tuple[RecoveryTraceRecord, ...], tuple[StateTraceRecord, ...]]:
    decisions: list[DecisionTraceRecord] = []
    recoveries: list[RecoveryTraceRecord] = []
    states: list[StateTraceRecord] = []
    sequence = 0
    for event in events:
        if event.event_type in _TRACE_EVENTS:
            sequence += 1
        if event.event_type not in {
            "decision_trace_record",
            "recovery_trace_record",
            "state_trace_record",
        }:
            continue
        if event.event_type == "decision_trace_record":
            decisions.append(DecisionTraceRecord.model_validate(event.payload).model_copy(update={"trace_sequence": sequence}))
        elif event.event_type == "recovery_trace_record":
            recoveries.append(RecoveryTraceRecord.model_validate(event.payload).model_copy(update={"trace_sequence": sequence}))
        else:
            states.append(StateTraceRecord.model_validate(event.payload).model_copy(update={"trace_sequence": sequence}))
    return tuple(decisions), tuple(recoveries), tuple(states)


def _trajectory_boundaries(events: Sequence[EventEnvelope]) -> tuple[int, ...]:
    """Record the trace sequence immediately before every issue revision."""

    sequence = 0
    boundaries: list[int] = []
    for event in events:
        if event.event_type == "case_issue_revised":
            boundaries.append(sequence)
        if event.event_type in _TRACE_EVENTS:
            sequence += 1
    return tuple(boundaries)


def _typed_progress_rebuilds(events: Sequence[EventEnvelope]) -> tuple[V3ProgressRebuild, ...]:
    return tuple(
        V3ProgressRebuild.model_validate(event.payload)
        for event in events
        if event.event_type == "evidence_progress_rebuilt"
    )


def _typed_gate(events: Sequence[EventEnvelope], progress: Sequence[V3ProgressRebuild]) -> tuple[V3GateTrace, V3GateOutcome]:
    gate_events = [event for event in events if event.event_type == "evidence_gate_evaluated"]
    if not gate_events:
        raise V3ProductionTraceError("production path did not persist an Evidence Gate event")
    payload = gate_events[-1].payload
    raw_decision = payload.get("decision")
    decision: V3GateOutcome = cast(
        V3GateOutcome,
        raw_decision if isinstance(raw_decision, str) else ("issue_revision" if payload.get("revised_issue_type") else "unknown"),
    )
    progress_hash = progress[-1].online_snapshot_hash if progress else sha256_json({})
    gate = V3GateTrace(
        decision=decision,
        reason_code=str(payload.get("reason_code", "UNKNOWN")),
        allowed=decision != "unknown",
        evidence_progress_hash=progress_hash,
    )
    return gate, decision


def capture_production_trace(
    runtime: ProductionRuntime,
    *,
    case: V3CaseSpec,
    conversation_id: str,
    case_id: str,
    run_id: str,
    authorized_order_id: str | None = None,
) -> ProductionTraceEvidence:
    """Parse only persisted production evidence into the V3 grader contract."""

    events = [
        event
        for event in runtime.events.list_after(conversation_id)
        if event.case_id == case_id and event.run_id == run_id
    ]
    if not events:
        raise V3ProductionTraceError("production Case/Run has no persisted trace events")
    decisions, recoveries, states = _normalized_trace_sequences(events)
    calls = _typed_tool_calls(runtime, case_id=case_id, run_id=run_id, events=events)
    rebuilds = _typed_progress_rebuilds(events)
    gate, final_outcome = _typed_gate(events, rebuilds)
    assertions, snapshots, question_rows, consumption_rows = _typed_case_facts(runtime, case_id)
    assertions_by_id = {item.assertion_id: item for item in assertions}
    consumption_by_question = {row.question_id: row for row in consumption_rows}
    fact_questions: list[Any] = []
    for row in question_rows:
        consumption = consumption_by_question.get(row.question_id)
        assertion = (
            assertions_by_id.get(consumption.assertion_id)
            if consumption is not None and consumption.assertion_id is not None
            else None
        )
        if consumption is None:
            status = "asked"
        elif consumption.outcome == "empty":
            status = "unknown"
        elif consumption.outcome == "rejected":
            status = "conflict"
        elif assertion is not None and assertion.value is FactValue.UNKNOWN:
            status = "unknown"
        else:
            status = "answered"
        fact_questions.append(
            {
                "question_id": row.question_id,
                "case_id": row.case_id,
                "fact_code": row.fact_code,
                "status": status,
                "source_message_id": (
                    consumption.source_message_id if consumption is not None else None
                ),
                "source_message_hash": (
                    consumption.source_message_hash if consumption is not None else None
                ),
                "reason_code": (
                    consumption.reason_code if consumption is not None else "QUESTION_RECORDED"
                ),
                "repeat": assertion is not None and assertion.relation.value == "repeat",
            }
        )
    consumption_trace: list[Any] = []
    for row in consumption_rows:
        consumption_trace.append(
            {
                "question_id": row.question_id,
                "source_message_id": row.source_message_id,
                "source_message_hash": row.source_message_hash,
                "outcome": row.outcome,
                "candidate_batch_hash": row.candidate_batch_hash,
                "assertion_id": row.assertion_id,
                "reason_code": row.reason_code,
                "decision_payload_hash": sha256_json(row.decision_payload),
            }
        )
    trace = V3TypedTrace(
        decisions=decisions,
        recoveries=recoveries,
        states=states,
        tool_calls=calls,
        progress_rebuilds=rebuilds,
        gate_decisions=(gate,),
        fact_assertions=assertions,
        fact_snapshots=snapshots,
        questions=tuple(fact_questions),
        consumption_ledger=tuple(consumption_trace),
        trajectory_boundaries=_trajectory_boundaries(events),
    )
    no_write = all(call.tool_name in READ_TOOL_NAMES for call in calls)
    scope_bound = all(
        authorized_order_id is None
        or str(call.normalized_args.get("order_id", "")) == authorized_order_id
        for call in calls
    )
    safety_gate_pass = no_write and scope_bound
    trace = trace.model_copy(update={"safety_gate_input": safety_gate_pass})
    verdicts = execute_v3_graders(
        V3GradingContext(
            case=case,
            trace=trace,
            final_outcome=final_outcome,
            safety_gate_pass=safety_gate_pass,
            case_scope_id=case_id,
        ),
        case.expected_grader_ids,
    )
    trace = trace.model_copy(update={"grader_verdicts": verdicts})
    with runtime.database.session_factory() as session:
        run = Repository(session).get_run(run_id)
    started = run.started_at if run is not None and run.started_at is not None else events[0].timestamp
    completed = run.completed_at if run is not None and run.completed_at is not None else events[-1].timestamp
    return ProductionTraceEvidence(
        conversation_id=conversation_id,
        case_id=case_id,
        run_id=run_id,
        trace=trace,
        final_outcome=final_outcome,
        safety_gate_pass=safety_gate_pass,
        grader_verdicts=verdicts,
        started_at=started,
        completed_at=completed,
        latency_ms=max((completed - started).total_seconds() * 1000, 0.0),
        model_calls=0,
        provider_calls=0,
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
        toolnode_reached=bool(calls),
    )


def _shared_input_digest(case: V3CaseSpec, case_input: V3ProductionCaseInput) -> str:
    return sha256_json(
        {
            "case": case.model_dump(mode="json"),
            "production_case_input": case_input.model_dump(mode="json"),
            "shared_fields": case.shared_fields.model_dump(mode="json"),
            "read_tools": sorted(READ_TOOL_NAMES),
        }
    )


def _shared_component_versions(case: V3CaseSpec) -> dict[str, str]:
    fields = case.shared_fields
    return {
        "decision_context": fields.decision_context_version,
        "fixture": case.fixture_revision,
        "source": case.source_revision,
        "fault_seed_hash": case.fault_seed_hash,
        "evaluated_at": case.evaluated_at.isoformat(),
        "budget": fields.budget_version,
        "cache": fields.cache_revision,
        "tool_registry": fields.tool_registry_version,
        "validator": fields.validator_version,
        "router": fields.router_version,
        "reducer": fields.reducer_version,
        "evidence_gate": fields.evidence_gate_version,
        "case_fact": "v3b.case-fact-production.v1",
        "response": fields.response_layer_version,
        "executor": fields.executor_version,
        "grader": fields.grader_registry_version,
        "runtime": "production-composition-root",
        "langgraph": "production-investigation-graph",
        "tool_node": "langgraph-toolnode",
        "governed_executor": "production-governed-tool-executor",
        "selector_turn_ceiling": str(fields.selector_turn_ceiling),
        "provider_call_ceiling": str(fields.provider_call_ceiling),
        "token_ceiling_config": fields.token_ceiling_config,
        "timeout_seconds": str(fields.timeout_seconds),
        "repeat": str(fields.repeat),
    }


def _validate_case_input(case: V3CaseSpec, case_input: V3ProductionCaseInput) -> None:
    errors: list[str] = []
    if case_input.scenario_id != case.scenario_id:
        errors.append("scenario")
    if case_input.issue_type != case.issue:
        errors.append("issue")
    if case_input.fixture_revision != case.fixture_revision:
        errors.append("fixture revision")
    if case_input.source_revision != case.source_revision:
        errors.append("source revision")
    if case_input.evaluated_at != case.evaluated_at:
        errors.append("evaluated_at")
    if fault_seed_hash(case_input.fault_seed) != case.fault_seed_hash:
        errors.append("fault seed")
    if errors:
        raise V3ExecutionNotAuthorized(f"production input binding differs: {', '.join(errors)}")


def load_production_case_inputs(root: Path | None = None) -> dict[str, V3ProductionCaseInput]:
    """Load the committed 32-case input package used by the production path."""

    project = _project_root(root)
    path = project / PRODUCTION_CASE_INPUTS_RELATIVE_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise V3ExecutionNotAuthorized("committed production case inputs are unavailable") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != "v3.production-case-inputs.v1":
        raise V3ExecutionNotAuthorized("production case-input schema version is invalid")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list):
        raise V3ExecutionNotAuthorized("production case-input list is missing")
    shared = {
        "fixture_revision": payload.get("fixture_revision"),
        "source_revision": payload.get("source_revision"),
        "evaluated_at": payload.get("evaluated_at"),
    }
    if not all(isinstance(value, str) for value in shared.values()):
        raise V3ExecutionNotAuthorized("production case-input shared binding is malformed")
    cases = load_matrix(project)
    cases_by_id = {case.scenario_id: case for case in cases}
    result: dict[str, V3ProductionCaseInput] = {}
    for raw in raw_cases:
        if not isinstance(raw, dict):
            raise V3ExecutionNotAuthorized("production case-input entry is not an object")
        enriched = {
            **raw,
            "fixture_revision": raw.get("fixture_revision", shared["fixture_revision"]),
            "source_revision": raw.get("source_revision", shared["source_revision"]),
            "evaluated_at": raw.get("evaluated_at", shared["evaluated_at"]),
        }
        try:
            case_input = V3ProductionCaseInput.model_validate(enriched)
        except Exception as exc:
            raise V3ExecutionNotAuthorized("production case-input entry is invalid") from exc
        case = cases_by_id.get(case_input.scenario_id)
        if case is None:
            raise V3ExecutionNotAuthorized(
                f"production case-input references an unknown scenario: {case_input.scenario_id}"
            )
        _validate_case_input(case, case_input)
        if case_input.scenario_id in result:
            raise V3ExecutionNotAuthorized("production case-input scenario is duplicated")
        result[case_input.scenario_id] = case_input
    if set(result) != set(cases_by_id) or len(result) != 32:
        raise V3ExecutionNotAuthorized("production case-input binding is not exactly the 32-case matrix")
    return result


def production_case_inputs_digest(
    inputs: Mapping[str, V3ProductionCaseInput],
) -> str:
    """Digest normalized typed inputs, independent of JSON formatting."""

    return sha256_json(
        [inputs[key].model_dump(mode="json") for key in sorted(inputs)]
    )


def execution_authorization_from_package(
    package: V3DevelopmentExecutionPackage,
) -> V3ExecutionAuthorization:
    """Project an immutable package into the legacy authorization view."""

    return V3ExecutionAuthorization(
        execution_identity=package.execution_identity,
        authorization_flag=True,
        live_mode=True,
        credential_name=package.credential_name,
        credential_present=True,
        clean_source=True,
        current_source_revision=package.evaluated_source_revision,
        source_revision=package.manifest_source_revision,
        evaluated_source_revision=package.evaluated_source_revision,
        manifest_version_binding=True,
        manifest_digests=dict(package.manifest_digests),
        plan_version=package.plan_version,
        token_ceiling=package.token_threshold,
        provider_call_ceiling=package.authorized_provider_call_ceiling,
        provider_call_ceiling_per_run=package.authorized_provider_call_ceiling_per_run,
        provider_hard_ceiling=package.provider_hard_ceiling,
        provider_call_semantics=package.provider_call_semantics,
        provider_retry_policy=package.provider_retry_policy,
        token_threshold_semantics=package.token_threshold_semantics,
        token_threshold_semantics_accepted=package.owner_token_threshold_semantics_accepted,
        output_token_cap_per_invocation=package.output_token_cap_per_invocation,
        hard_token_ceiling=package.hard_token_ceiling,
        timeout_seconds=package.timeout_seconds,
        repeat=package.repeat,
    )


def _trace_bound_to_eval_run(trace: V3TypedTrace, eval_run_id: str) -> V3TypedTrace:
    """Bind persisted runtime trace rows to the deterministic Eval run key."""

    return trace.model_copy(
        update={
            "decisions": tuple(item.model_copy(update={"run_id": eval_run_id}) for item in trace.decisions),
            "recoveries": tuple(item.model_copy(update={"run_id": eval_run_id}) for item in trace.recoveries),
            "states": tuple(item.model_copy(update={"run_id": eval_run_id}) for item in trace.states),
            "tool_calls": tuple(item.model_copy(update={"run_id": eval_run_id}) for item in trace.tool_calls),
            "progress_rebuilds": tuple(item.model_copy(update={"run_id": eval_run_id}) for item in trace.progress_rebuilds),
        }
    )


def _record_from_evidence(
    *,
    case: V3CaseSpec,
    case_input: V3ProductionCaseInput,
    architecture: V3Architecture,
    repetition: int,
    execution_identity: str,
    evaluation_revision: str,
    eval_run_id: str,
    evidence: ProductionTraceEvidence,
    shared_input_digest: str,
    manifest_id: str | None = None,
    budget_binding: DevelopmentBudgetBinding | None = None,
    execution_package_digest: str | None = None,
) -> V3RunRecord:
    _validate_case_input(case, case_input)
    trace = _trace_bound_to_eval_run(evidence.trace, eval_run_id)
    verdicts = evidence.grader_verdicts
    if not verdicts:
        verdicts = execute_v3_graders(
            V3GradingContext(
                case=case,
                trace=trace,
                final_outcome=evidence.final_outcome,
                safety_gate_pass=evidence.safety_gate_pass,
                case_scope_id=evidence.case_id,
            ),
            case.expected_grader_ids,
        )
    trace = trace.model_copy(
        update={
            "safety_gate_input": evidence.safety_gate_pass,
            "grader_verdicts": verdicts,
        }
    )
    quality = [item for item in verdicts if item.grader_id != "GR-V3A-13"]
    safety = [item for item in verdicts if item.grader_id == "GR-V3A-13"]
    triggered = tuple(
        obligation.obligation_id
        for obligation in case.trajectory_obligations
        if _obligation_triggered(obligation, trace)
    )
    failed = tuple(
        obligation_id
        for obligation_id in triggered
        if not all(item.passed for item in verdicts)
    )
    grader_failed = any(not item.passed for item in verdicts)
    run_status = evidence.run_status
    error_code = evidence.error_code
    error_class = evidence.error_class
    if grader_failed and run_status == "completed" and error_class == "none":
        run_status = "grader_failure"
        error_code = "GRADER_FAILURE"
        error_class = "grader"
    accounting = evidence.budget_accounting
    if accounting is None:
        selector_invocation_attempts = len(trace.decisions)
        completed_selector_calls = len(trace.decisions)
        model_invocation_attempts = evidence.model_calls
        completed_model_calls = evidence.model_calls
        provider_invocation_attempts = evidence.provider_calls
        completed_provider_calls = evidence.provider_calls
        provider_errors = 0
        provider_timeouts = 0
        provider_cancellations = 0
        provider_budget_remaining = None
        token_threshold = None
        threshold_exhausted = False
        token_overshoot = None
        token_usage_complete = evidence.total_tokens is not None
        binding_digest = budget_binding.binding_digest if budget_binding else None
    else:
        selector_invocation_attempts = accounting.attempted_provider_calls
        completed_selector_calls = accounting.completed_provider_calls
        model_invocation_attempts = accounting.model_invocation_attempts
        completed_model_calls = accounting.completed_model_calls
        provider_invocation_attempts = accounting.attempted_provider_calls
        completed_provider_calls = accounting.completed_provider_calls
        provider_errors = accounting.provider_errors
        provider_timeouts = accounting.provider_timeouts
        provider_cancellations = accounting.provider_cancellations
        provider_budget_remaining = accounting.remaining_provider_calls
        token_threshold = accounting.token_threshold
        threshold_exhausted = accounting.threshold_exhausted
        token_overshoot = accounting.token_overshoot
        token_usage_complete = accounting.token_usage_complete
        binding_digest = accounting.binding_digest
    metrics = V3Metrics(
        actual_reads=sum(call.actual_execution for call in trace.tool_calls),
        cache_hits=sum(call.cache_hit for call in trace.tool_calls),
        unnecessary_reads=0,
        retry_attempts=sum(call.attempt_number == 2 for call in trace.tool_calls),
        retry_recovered=any(
            call.attempt_number == 2 and call.execution_status == "success"
            for call in trace.tool_calls
        ),
        stuck_or_safe_stop=any(item.route.value == "safe_stop" for item in trace.recoveries),
        rebuild_parity=all(
            item.online_snapshot_hash == item.replayed_snapshot_hash
            for item in trace.progress_rebuilds
        ),
        clarification_questions=len(trace.questions),
        repeated_questions=sum(item.repeat for item in trace.questions),
        latency_ms=evidence.latency_ms,
        model_calls=model_invocation_attempts,
        provider_calls=provider_invocation_attempts,
        input_tokens=(accounting.input_tokens if accounting else evidence.input_tokens),
        output_tokens=(accounting.output_tokens if accounting else evidence.output_tokens),
        total_tokens=(accounting.total_tokens if accounting else evidence.total_tokens),
        cost="unavailable",
        selector_invocation_attempts=selector_invocation_attempts,
        completed_selector_calls=completed_selector_calls,
        model_invocation_attempts=model_invocation_attempts,
        completed_model_calls=completed_model_calls,
        provider_invocation_attempts=provider_invocation_attempts,
        completed_provider_calls=completed_provider_calls,
        provider_errors=provider_errors,
        provider_timeouts=provider_timeouts,
        provider_cancellations=provider_cancellations,
        provider_budget_remaining=provider_budget_remaining,
        token_threshold=token_threshold,
        threshold_exhausted=threshold_exhausted,
        token_overshoot=token_overshoot,
        hard_token_ceiling=False,
        token_threshold_semantics=TOKEN_THRESHOLD_SEMANTICS,
        token_usage_complete=token_usage_complete,
        provider_attempts_exact=False if architecture == "agent" else True,
        toolnode_reached=evidence.toolnode_reached or bool(trace.tool_calls),
        selector_schema_failures=sum(
            str(item.rejection_code or "").startswith("SELECTOR_")
            for item in trace.decisions
        ),
        multi_observation_rejections=sum(
            item.rejection_code == "SELECTOR_MULTIPLE_TOOL_CALLS"
            for item in trace.decisions
        ),
    )
    return V3RunRecord(
        eval_run_id=eval_run_id,
        execution_identity=execution_identity,
        manifest_id=cast(
            Any,
            manifest_id
            or (V3A_EVAL_DEV_IDENTITY if case.family_kind == "v3a" else V3B_EVAL_DEV_IDENTITY),
        ),
        evaluation_revision=evaluation_revision,
        scenario_id=case.scenario_id,
        pair_id=case.pair_id,
        case_id=evidence.case_id,
        family=case.family,
        architecture=architecture,
        repetition=repetition,
        run_status=cast(Any, run_status),
        started_at=evidence.started_at,
        completed_at=evidence.completed_at,
        quality_pass=bool(quality) and all(item.passed for item in quality),
        safety_gate_pass=evidence.safety_gate_pass and all(item.passed for item in safety),
        final_outcome=evidence.final_outcome,
        triggered_obligations=triggered,
        failed_obligations=failed,
        metrics=metrics,
        trace=trace,
        shared_input_digest=shared_input_digest,
        shared_component_versions=_shared_component_versions(case),
        selector_version=f"production.{architecture}.selector.v2-structured-candidate",
        authorized_selector_turn_ceiling=case.shared_fields.selector_turn_ceiling,
        authorized_provider_call_ceiling=case.shared_fields.provider_call_ceiling,
        timeout_seconds=case.shared_fields.timeout_seconds,
        repeat=case.shared_fields.repeat,
        error_code=error_code,
        error_class=cast(Any, error_class),
        budget_ledger_binding_digest=binding_digest,
        execution_package_digest=execution_package_digest,
        plan_version=(budget_binding.plan_version if budget_binding else None),
        manifest_digests=(dict(budget_binding.manifest_digests) if budget_binding else {}),
    )


def _failure_record(
    *,
    case: V3CaseSpec,
    architecture: V3Architecture,
    repetition: int,
    execution_identity: str,
    evaluation_revision: str,
    eval_run_id: str,
    shared_input_digest: str,
    started_at: datetime,
    error: BaseException,
    manifest_id: str | None = None,
    accounting: DevelopmentBudgetRunAccounting | None = None,
    budget_binding: DevelopmentBudgetBinding | None = None,
    execution_package_digest: str | None = None,
) -> V3RunRecord:
    error_name = type(error).__name__.casefold()
    error_code = type(error).__name__
    if isinstance(error, ProviderBudgetAdmissionRejected):
        error_code = error.reason_code
        error_class = "budget"
        status = cast(Any, {
            "provider_budget_exhausted": "provider_budget_exhausted",
            "token_threshold_exhausted": "token_threshold_exhausted",
            "token_usage_unavailable": "token_usage_unavailable",
            "provider_invocation_incomplete": "provider_invocation_incomplete",
        }.get(error.reason_code, "error"))
    elif isinstance(error, SelectorSchemaFailure):
        error_code = error.reason_code
        error_class = "schema"
        status = "schema_failure"
    elif isinstance(error, ProviderInvocationFailure):
        error_code = error.reason_code
        error_class = "provider"
        status = "provider_failure"
    elif isinstance(error, SelectorExecutionFailure):
        error_code = error.reason_code
        error_class = "runtime"
        status = "error"
    elif isinstance(error, TimeoutError):
        error_class = "timeout"
        status = "timeout"
    elif isinstance(error, asyncio.CancelledError):
        error_class = "timeout"
        error_code = "PROVIDER_CANCELLED"
        status = "timeout"
    elif "provider" in error_name or "connection" in error_name:
        error_class = "provider"
        status = "provider_failure"
    elif "grader" in error_name:
        error_class = "grader"
        status = "grader_failure"
    elif "trace" in error_name or "validation" in error_name:
        error_class = "schema"
        status = "schema_failure"
    else:
        error_class = "runtime"
        status = "error"
    if accounting is None:
        accounting = DevelopmentBudgetRunAccounting(
            binding_digest=(budget_binding.binding_digest if budget_binding else None),
        )
    input_tokens = accounting.input_tokens
    output_tokens = accounting.output_tokens
    total_tokens = accounting.total_tokens
    binding_digest = accounting.binding_digest or (budget_binding.binding_digest if budget_binding else None)
    failure_trace = V3TypedTrace(safety_gate_input=False)
    failure_verdicts = execute_v3_graders(
        V3GradingContext(
            case=case,
            trace=failure_trace,
            final_outcome="unknown",
            safety_gate_pass=False,
            case_scope_id=case.pair_id,
        ),
        case.expected_grader_ids,
    )
    failure_trace = failure_trace.model_copy(update={"grader_verdicts": failure_verdicts})
    return V3RunRecord(
        eval_run_id=eval_run_id,
        execution_identity=execution_identity,
        manifest_id=cast(
            Any,
            manifest_id
            or (V3A_EVAL_DEV_IDENTITY if case.family_kind == "v3a" else V3B_EVAL_DEV_IDENTITY),
        ),
        evaluation_revision=evaluation_revision,
        scenario_id=case.scenario_id,
        pair_id=case.pair_id,
        family=case.family,
        architecture=architecture,
        repetition=repetition,
        run_status=cast(Any, status),
        started_at=started_at,
        completed_at=datetime.now(UTC),
        quality_pass=False,
        safety_gate_pass=False,
        final_outcome="unknown",
        metrics=V3Metrics(
            actual_reads=0,
            cache_hits=0,
            unnecessary_reads=0,
            retry_attempts=0,
            rebuild_parity=False,
            clarification_questions=0,
            repeated_questions=0,
            latency_ms=0,
            model_calls=accounting.model_invocation_attempts,
            provider_calls=accounting.attempted_provider_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost="unavailable",
            selector_invocation_attempts=accounting.attempted_provider_calls,
            completed_selector_calls=accounting.completed_provider_calls,
            model_invocation_attempts=accounting.model_invocation_attempts,
            completed_model_calls=accounting.completed_model_calls,
            provider_invocation_attempts=accounting.attempted_provider_calls,
            completed_provider_calls=accounting.completed_provider_calls,
            provider_errors=accounting.provider_errors,
            provider_timeouts=accounting.provider_timeouts,
            provider_cancellations=accounting.provider_cancellations,
            provider_budget_remaining=accounting.remaining_provider_calls,
            token_threshold=accounting.token_threshold,
            threshold_exhausted=accounting.threshold_exhausted,
            token_overshoot=accounting.token_overshoot,
            hard_token_ceiling=False,
            token_threshold_semantics=TOKEN_THRESHOLD_SEMANTICS,
            token_usage_complete=accounting.token_usage_complete,
            provider_attempts_exact=False if architecture == "agent" else True,
            toolnode_reached=False,
            selector_schema_failures=int(isinstance(error, SelectorSchemaFailure)),
            multi_observation_rejections=int(
                isinstance(error, SelectorSchemaFailure)
                and error.reason_code == "SELECTOR_MULTIPLE_TOOL_CALLS"
            ),
        ),
        trace=failure_trace,
        shared_input_digest=shared_input_digest,
        shared_component_versions=_shared_component_versions(case),
        selector_version=f"production.{architecture}.selector.v2-structured-candidate",
        authorized_selector_turn_ceiling=case.shared_fields.selector_turn_ceiling,
        authorized_provider_call_ceiling=case.shared_fields.provider_call_ceiling,
        timeout_seconds=case.shared_fields.timeout_seconds,
        repeat=case.shared_fields.repeat,
        error_code=error_code,
        error_class=cast(Any, error_class),
        budget_ledger_binding_digest=binding_digest,
        execution_package_digest=execution_package_digest,
        plan_version=(budget_binding.plan_version if budget_binding else None),
        manifest_digests=(dict(budget_binding.manifest_digests) if budget_binding else {}),
    )


def _callback_usage(callback: Any) -> dict[str, int]:
    """Sum callback-reported token fields without using callback cardinality."""

    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    observed = {key: False for key in totals}
    metadata = getattr(callback, "usage_metadata", {})
    if not isinstance(metadata, Mapping):
        return {}
    for value in metadata.values():
        if not isinstance(value, Mapping):
            continue
        for key in totals:
            raw = value.get(key)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)) or int(raw) != raw:
                continue
            if raw < 0:
                continue
            totals[key] += int(raw)
            observed[key] = True
    return {key: totals[key] for key, present in observed.items() if present}


def _response_usage(response: Any) -> dict[str, int]:
    if isinstance(response, Mapping):
        response = response.get("raw")
    usage = getattr(response, "usage_metadata", None)
    if not isinstance(usage, Mapping):
        return {}
    result: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        raw = usage.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or int(raw) != raw:
            continue
        if raw < 0:
            continue
        result[key] = int(raw)
    return result


def _merge_usage(response: Any, callback: Any) -> dict[str, int]:
    """Prefer per-response usage and fill missing fields from the callback."""

    result = _response_usage(response)
    for key, value in _callback_usage(callback).items():
        result.setdefault(key, value)
    return result


class DevelopmentProviderInvocationObserver:
    """Instrument one Agent selector's actual model invocation boundary."""

    def __init__(
        self,
        *,
        ledger: DevelopmentBudgetLedger,
        logical_run_key: str,
        timeout_scope: bool = True,
    ) -> None:
        self._ledger = ledger
        self._logical_run_key = logical_run_key
        self._timeout_scope = timeout_scope

    async def invoke(
        self,
        *,
        model: Any,
        messages: Sequence[Any],
        context: Any,
    ) -> Any:
        turn = int(context.remaining_budget.run_planning_turns)
        admission = self._ledger.admit_provider_call(
            logical_run_key=self._logical_run_key,
            selector_turn=turn,
        )
        if not admission.granted:
            is_global_stop = admission.reason in {
                "provider_invocation_incomplete",
                "token_threshold_exhausted",
                "token_usage_unavailable",
            } or (
                admission.reason == "provider_budget_exhausted"
                and admission.remaining_provider_calls == 0
            )
            if is_global_stop:
                self._ledger.force_stop_reason(cast(Any, admission.reason))
            raise ProviderBudgetAdmissionRejected(admission.reason)
        if admission.invocation_id is None:
            raise DevelopmentBudgetLedgerError("granted admission has no invocation identity")
        invocation_id = admission.invocation_id
        callback: Any = None
        try:
            with get_usage_metadata_callback() as usage_callback:
                callback = usage_callback
                response = await model.ainvoke(messages)
                usage = _merge_usage(response, usage_callback)
        except asyncio.CancelledError:
            status = "timeout" if self._timeout_scope else "cancelled"
            self._ledger.complete_provider_call(
                invocation_id=invocation_id,
                logical_run_key=self._logical_run_key,
                status=cast(Any, status),
                usage=_callback_usage(callback),
                error_code="PROVIDER_TIMEOUT" if status == "timeout" else "PROVIDER_CANCELLED",
            )
            raise
        except TimeoutError:
            self._ledger.complete_provider_call(
                invocation_id=invocation_id,
                logical_run_key=self._logical_run_key,
                status="timeout",
                usage=_callback_usage(callback),
                error_code="PROVIDER_TIMEOUT",
            )
            raise
        except Exception as exc:
            self._ledger.complete_provider_call(
                invocation_id=invocation_id,
                logical_run_key=self._logical_run_key,
                status="provider_error",
                usage=_callback_usage(callback),
                error_code=type(exc).__name__,
            )
            raise ProviderInvocationFailure(type(exc).__name__, cause=exc) from exc
        self._ledger.complete_provider_call(
            invocation_id=invocation_id,
            logical_run_key=self._logical_run_key,
            status="completed",
            usage=usage,
        )
        return response


class ProductionInvestigationAdapter:
    """Run one normalized case through the production Investigation service."""

    def __init__(
        self,
        *,
        root_factory: Callable[..., Path],
        project_root: Path | None = None,
        settings_factory: Callable[[V3Architecture, V3ProductionCaseInput, Path], Settings]
        | None = None,
        fixtures_factory: Callable[[V3Architecture, V3ProductionCaseInput], FixtureStore]
        | None = None,
        model_factory: Callable[[V3Architecture, V3ProductionCaseInput, Settings | None], Any]
        | None = None,
    ) -> None:
        self._root_factory = root_factory
        self._configuration_root = _project_root(project_root).expanduser().resolve()
        self._settings_factory = settings_factory
        self._fixtures_factory = fixtures_factory
        self._model_factory = model_factory

    def _runtime_root(
        self,
        architecture: V3Architecture,
        case_input: V3ProductionCaseInput,
        repetition: int,
    ) -> Path:
        """Resolve a repetition-scoped root while retaining legacy callers."""

        try:
            signature = inspect.signature(self._root_factory)
            signature.bind(architecture, case_input, repetition)
        except (TypeError, ValueError):
            return self._root_factory(architecture, case_input) / f"r{repetition}"
        return self._root_factory(architecture, case_input, repetition)

    @staticmethod
    def _default_settings(
        architecture: V3Architecture,
        case_input: V3ProductionCaseInput,
        root: Path,
        *,
        live: bool,
        project_root: Path | None = None,
        timeout_seconds: float = 30.0,
    ) -> Settings:
        """Build settings through the shared project configuration boundary."""

        configuration_root = _project_root(project_root).expanduser().resolve()
        if live and architecture == "agent":
            return build_live_settings(
                configuration_root,
                runtime_root=root,
                timeout_seconds=timeout_seconds,
                fault_seed=case_input.fault_seed,
                evaluated_at=case_input.evaluated_at,
            )
        return build_mock_settings(
            configuration_root,
            runtime_root=root,
            timeout_seconds=timeout_seconds,
            fault_seed=case_input.fault_seed,
            evaluated_at=case_input.evaluated_at,
        )

    def build_settings(
        self,
        *,
        architecture: V3Architecture,
        case_input: V3ProductionCaseInput,
        root: Path,
        timeout_seconds: float,
    ) -> Settings:
        """Expose the exact settings path for provider-free readiness checks."""

        if self._settings_factory is not None:
            return self._settings_factory(architecture, case_input, root)
        return self._default_settings(
            architecture,
            case_input,
            root,
            live=self._model_factory is None,
            project_root=self._configuration_root,
            timeout_seconds=timeout_seconds,
        )

    async def execute(
        self,
        *,
        case: V3CaseSpec,
        case_input: V3ProductionCaseInput,
        architecture: V3Architecture,
        repetition: int,
        timeout_seconds: float,
        logical_run_key: str | None = None,
        budget_ledger: DevelopmentBudgetLedger | None = None,
        stop_after_actual_executions: int | None = None,
    ) -> ProductionTraceEvidence:
        logical_run_key = logical_run_key or f"{case.pair_id}-{architecture}-r{repetition}"
        root = self._runtime_root(architecture, case_input, repetition)
        settings = self.build_settings(
            architecture=architecture,
            case_input=case_input,
            root=root,
            timeout_seconds=timeout_seconds,
        )
        fixtures = (
            self._fixtures_factory(architecture, case_input)
            if self._fixtures_factory is not None
            else fixture_store_for_case(
                profile=case_input.fixture_profile,
                customer_id=case_input.customer_id,
                order_id=case_input.order_id,
                issue_type=case_input.issue_type,
                evaluated_at=case_input.evaluated_at,
                fault_seed=case_input.fault_seed,
            )
        )
        async with await build_async_production_runtime(
            root=root,
            architecture=architecture,
            settings=settings,
            fixtures=fixtures,
            fault_seed=case_input.fault_seed,
            evaluated_at=case_input.evaluated_at,
        ) as runtime:
            conversation_id = f"conv_v3_{uuid4().hex}"
            case_id = f"case_v3_{uuid4().hex}"
            run_id = f"run_v3_{uuid4().hex}"
            trace_id = f"trace_v3_{uuid4().hex}"
            domain_case = InvestigationCase(
                case_id=case_id,
                conversation_id=conversation_id,
                customer_id=case_input.customer_id,
                authorized_order_id=case_input.order_id,
                canonical_issue_type=case_input.issue_type,
            )
            message_ids = (
                tuple(f"{message_id}-r{repetition}" for message_id in case.customer_message_ids)
                if case.family_kind == "v3b" and case.customer_message_ids
                else (f"msg-v3a-{case.scenario_id}-r{repetition}",)
            )
            initial_message_id = message_ids[0]
            with runtime.database.session_factory() as session, session.begin():
                repository = Repository(session)
                repository.create_conversation(
                    case_input.customer_id,
                    case_input.customer_id,
                    runtime.application.settings.llm_mode.value,
                    conversation_id=conversation_id,
                    fixture_version=case_input.fixture_revision,
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
                    case_input.customer_message,
                    message_id=initial_message_id,
                    case_id=case_id,
                    run_id=run_id,
                    trace_id=trace_id,
                    # The scenario clock belongs to tool observations; the
                    # persisted customer message must remain before the
                    # runtime-created clarification question even when the
                    # committed evaluated_at is ahead of wall clock time.
                    created_at=datetime.now(UTC) - timedelta(seconds=1),
                )
            snapshot = runtime.application.case_facts.initialize_case(case_id)
            trusted = TrustedToolContext(
                customer_id=case_input.customer_id,
                conversation_id=conversation_id,
                case_id=case_id,
                run_id=run_id,
                authorized_order_id=case_input.order_id,
                canonical_issue_type=case_input.issue_type,
                fixture_version=case_input.fixture_revision,
                fault_seed=case_input.fault_seed,
                evaluated_at=case_input.evaluated_at,
                trace_id=trace_id,
            )
            selector_model = (
                self._model_factory(architecture, case_input, settings)
                if self._model_factory is not None
                else (
                    build_investigation_model(settings, READ_TOOLS)
                    if architecture == "agent"
                    else None
                )
            )
            if architecture == "agent" and self._model_factory is None and (
                settings.llm_mode is not LLMMode.LIVE
                or settings.deepseek_model != LIVE_MODEL_NAME
                or not settings.deepseek_api_key
            ):
                raise V3ExecutionNotAuthorized(
                    "formal Agent execution requires the authorized Live DeepSeek model and credential"
                )
            selector_observer = (
                DevelopmentProviderInvocationObserver(
                    ledger=budget_ledger,
                    logical_run_key=logical_run_key,
                    timeout_scope=True,
                )
                if architecture == "agent" and budget_ledger is not None
                else None
            )
            if architecture == "agent" and budget_ledger is None:
                raise V3ExecutionNotAuthorized(
                    "formal Agent execution requires the Development budget ledger"
                )
            investigation_kwargs: dict[str, Any] = {
                "trusted": trusted,
                "customer_message": case_input.customer_message,
                "case_fact_snapshot": snapshot.model_dump(mode="json"),
                "tool_cache": CaseToolCache(),
            }
            if architecture == "agent":
                investigation_kwargs.update(
                    {
                        "selector_model": selector_model,
                        "selector_invocation_observer": selector_observer,
                    }
                )
            if stop_after_actual_executions is not None:
                investigation_kwargs["stop_after_actual_executions"] = stop_after_actual_executions
            try:
                output = await asyncio.wait_for(
                    runtime.application.investigation.investigate(**investigation_kwargs),
                    timeout=timeout_seconds,
                )
            except TimeoutError:
                raise
            decision = PolicyDecision(
                route=PolicyRoute.SUPPORTED_LOGISTICS,
                supported=True,
                canonical_issue_type=domain_case.canonical_issue_type,
                authorized_order_id=domain_case.authorized_order_id,
                blocked_fragments=(),
                risk_flags=(),
                reason_code="V3_DEVELOPMENT_INPUT",
            )
            await runtime.application._apply_investigation_result(
                case=domain_case,
                run_id=run_id,
                decision=decision,
                output=output,
            )

            async def append_customer_reply(
                *, message_id: str, content: str, bound_case_id: str = case_id
            ) -> None:
                with runtime.database.session_factory() as session, session.begin():
                    repository = Repository(session)
                    questions = repository.list_case_fact_questions(case_id)
                    if not questions:
                        raise V3ProductionTraceError(
                            "V3-B continuation has no persisted Case Fact question"
                        )
                    reply_at = questions[-1].asked_at + timedelta(microseconds=1)
                    repository.add_message(
                        conversation_id,
                        "customer",
                        content,
                        message_id=message_id,
                        case_id=bound_case_id,
                        run_id=run_id,
                        trace_id=trace_id,
                        created_at=reply_at,
                    )

            if case.family_kind == "v3b":
                messages = case_input.customer_messages
                if len(messages) < 2:
                    raise V3ProductionTraceError(
                        "V3-B production input must contain an initial message and a reply"
                    )

                async def continue_customer_message(message_id: str, content: str) -> None:
                    await append_customer_reply(message_id=message_id, content=content)
                    await runtime.application._continue_business_clarification(
                        case=domain_case,
                        run_id=run_id,
                        source_message_id=message_id,
                        customer_message=content,
                        trace_id=f"trace_v3_{uuid4().hex}",
                        selector_model=selector_model if architecture == "agent" else None,
                        selector_invocation_observer=(
                            selector_observer if architecture == "agent" else None
                        ),
                    )

                if case_input.fixture_profile in {"fact-repeat", "fact-question-replay"}:
                    await append_customer_reply(
                        message_id=message_ids[1],
                        content=messages[1],
                    )
                    before = runtime.application.case_facts.load_snapshot(case_id)
                    with runtime.database.session_factory() as session:
                        questions = Repository(session).list_case_fact_questions(case_id)
                    outstanding = FactCode(questions[-1].fact_code)
                    active_ids = set(before.facts[outstanding].active_assertion_ids)
                    active = [
                        item
                        for item in runtime.application.case_facts.load_assertions(case_id)
                        if item.assertion_id in active_ids
                    ]
                    candidates = runtime.application.case_facts.extract_candidates(
                        messages[1],
                        fact_code=outstanding,
                        active_assertions=active,
                        proof_context=runtime.application.case_facts.load_current_proof_context(
                            case_id
                        ),
                    )
                    runtime.application.case_facts.accept_message(
                        case_id=case_id,
                        source_message_id=message_ids[1],
                        candidates=candidates,
                    )
                    # Exact question/message replay must be idempotent: the
                    # second call returns the same persisted decision and adds
                    # neither a ledger row nor an assertion.
                    runtime.application.case_facts.accept_message(
                        case_id=case_id,
                        source_message_id=message_ids[1],
                        candidates=candidates,
                    )
                    await runtime.application._continue_business_clarification(
                        case=domain_case,
                        run_id=run_id,
                        source_message_id=message_ids[1],
                        customer_message=messages[1],
                        trace_id=f"trace_v3_{uuid4().hex}",
                        selector_model=selector_model if architecture == "agent" else None,
                        selector_invocation_observer=(
                            selector_observer if architecture == "agent" else None
                        ),
                    )
                elif case_input.fixture_profile == "fact-cross-case":
                    await append_customer_reply(
                        message_id=message_ids[1],
                        content=messages[1],
                        bound_case_id="foreign-case",
                    )
                    try:
                        with runtime.database.session_factory() as session:
                            questions = Repository(session).list_case_fact_questions(case_id)
                        before = runtime.application.case_facts.load_snapshot(case_id)
                        outstanding = FactCode(questions[-1].fact_code)
                        active_ids = set(before.facts[outstanding].active_assertion_ids)
                        active = [
                            item
                            for item in runtime.application.case_facts.load_assertions(case_id)
                            if item.assertion_id in active_ids
                        ]
                        candidates = runtime.application.case_facts.extract_candidates(
                            messages[1],
                            fact_code=outstanding,
                            active_assertions=active,
                            proof_context=runtime.application.case_facts.load_current_proof_context(
                                case_id
                            ),
                        )
                        runtime.application.case_facts.accept_message(
                            case_id=case_id,
                            source_message_id=message_ids[1],
                            candidates=candidates,
                        )
                    except CaseFactError:
                        pass
                    await continue_customer_message(message_ids[2], messages[2])
                else:
                    await continue_customer_message(message_ids[1], messages[1])
            evidence = capture_production_trace(
                runtime,
                case=case,
                conversation_id=conversation_id,
                case_id=case_id,
                run_id=run_id,
                authorized_order_id=case_input.order_id,
            )
            accounting = (
                budget_ledger.accounting_for(logical_run_key)
                if budget_ledger is not None
                else None
            )
            return replace(
                evidence,
                model_calls=(accounting.model_invocation_attempts if accounting else 0),
                provider_calls=(accounting.attempted_provider_calls if accounting else 0),
                input_tokens=(accounting.input_tokens if accounting else None),
                output_tokens=(accounting.output_tokens if accounting else None),
                total_tokens=(accounting.total_tokens if accounting else None),
                budget_accounting=accounting,
            )


class V3RealDevelopmentRunner:
    """Formal runner that persists one typed raw record per planned run."""

    def __init__(
        self,
        *,
        plan: V3Plan,
        manifests: Sequence[V3DevelopmentManifest],
        cases: Mapping[str, V3CaseSpec],
        case_inputs: Mapping[str, V3ProductionCaseInput],
        execution_identity: str,
        evaluation_revision: str,
        store: V3DevelopmentStore,
        adapter_factory: Callable[[], ProductionRunAdapter],
        authorization: V3ExecutionAuthorization,
        execution_package: V3DevelopmentExecutionPackage | None = None,
        allowed_source_paths: Sequence[Path] = (),
    ) -> None:
        if execution_package is None:
            validate_execution_authorization(authorization, plan=plan, manifests=manifests)
        else:
            try:
                observed_revision = current_source_revision()
                observed_clean = source_tree_is_clean(allowed_paths=allowed_source_paths)
                input_digest = production_case_inputs_digest(case_inputs)
                validate_execution_package_binding(
                    execution_package,
                    plan=plan,
                    manifests=manifests,
                    evaluated_source_revision=observed_revision,
                    source_tree_clean=observed_clean,
                    production_case_inputs_digest=input_digest,
                )
            except (ExecutionPackageError, V3ContractError) as exc:
                raise V3ExecutionNotAuthorized(
                    "execution package binding cannot be validated"
                ) from exc
            validate_execution_authorization(
                authorization,
                plan=plan,
                manifests=manifests,
                execution_package=execution_package,
            )
        if store.execution_identity != execution_identity:
            raise V3ExecutionNotAuthorized("Development store identity differs from runner identity")
        if execution_package is not None and execution_package.execution_identity != execution_identity:
            raise V3ExecutionNotAuthorized("execution package identity differs from runner identity")
        missing = set(cases).difference(case_inputs)
        extra = set(case_inputs).difference(cases)
        if missing or extra:
            raise V3ExecutionNotAuthorized(
                f"production case input binding is not exact: missing={sorted(missing)}, extra={sorted(extra)}"
            )
        manifest_case_ids = {case_id for manifest in manifests for case_id in manifest.case_ids}
        if manifest_case_ids != set(cases):
            raise V3ExecutionNotAuthorized("case bindings do not exactly match the committed manifests")
        for scenario_id, case in cases.items():
            bound = case_inputs[scenario_id]
            _validate_case_input(case, bound)
        try:
            observed_revision = current_source_revision()
            observed_clean = source_tree_is_clean(allowed_paths=allowed_source_paths)
        except V3ContractError as exc:
            raise V3ExecutionNotAuthorized("source state cannot be observed") from exc
        if not observed_clean or observed_revision != authorization.current_source_revision:
            raise V3ExecutionNotAuthorized("observed source state differs from authorization binding")
        if execution_package is not None and execution_package.evaluated_source_revision != observed_revision:
            raise V3ExecutionNotAuthorized(
                "observed source state differs from execution package binding"
            )
        if execution_package is not None and evaluation_revision != execution_package.evaluated_source_revision:
            raise V3ExecutionNotAuthorized(
                "report evaluation revision differs from execution package binding"
            )
        self.plan = plan
        self.manifests = tuple(manifests)
        self.cases = dict(cases)
        self.case_inputs = dict(case_inputs)
        self.execution_identity = execution_identity
        self.evaluation_revision = evaluation_revision
        self.store = store
        self.adapter_factory = adapter_factory
        self.execution_package = execution_package
        self.execution_state_ledger = (
            store.open_execution_state_ledger(package=execution_package)
            if execution_package is not None
            else None
        )
        try:
            self.budget_binding = _development_budget_binding(
                plan=plan,
                authorization=authorization,
                execution_identity=execution_identity,
            )
            self.budget_ledger = store.open_budget_ledger(binding=self.budget_binding)
        except (DevelopmentBudgetLedgerError, ValueError) as exc:
            raise V3ExecutionNotAuthorized("Development budget ledger binding is invalid") from exc

    @staticmethod
    def _adapter_supports_keyword(adapter: ProductionRunAdapter, name: str) -> bool:
        try:
            parameters = inspect.signature(adapter.execute).parameters.values()
        except (TypeError, ValueError):
            return False
        return any(
            parameter.name == name or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )

    async def _execute_adapter(
        self,
        *,
        adapter: ProductionRunAdapter,
        case: V3CaseSpec,
        case_input: V3ProductionCaseInput,
        architecture: V3Architecture,
        repetition: int,
        eval_run_id: str,
    ) -> ProductionTraceEvidence:
        kwargs: dict[str, Any] = {
            "case": case,
            "case_input": case_input,
            "architecture": architecture,
            "repetition": repetition,
            "timeout_seconds": self.plan.timeout_seconds,
        }
        has_logical_key = self._adapter_supports_keyword(adapter, "logical_run_key")
        has_ledger = self._adapter_supports_keyword(adapter, "budget_ledger")
        if architecture == "agent" and (not has_logical_key or not has_ledger):
            raise V3ExecutionNotAuthorized(
                "formal Agent adapter must expose logical_run_key and budget_ledger hooks"
            )
        if has_logical_key:
            kwargs["logical_run_key"] = eval_run_id
        if has_ledger:
            kwargs["budget_ledger"] = self.budget_ledger
        return await adapter.execute(**kwargs)

    async def run(self) -> tuple[V3RunRecord, ...]:
        records_by_key: dict[tuple[str, str, str, int], V3RunRecord] = {}
        for existing in self.store.load_runs():
            logical_key = (
                existing.scenario_id,
                existing.pair_id,
                existing.architecture,
                existing.repetition,
            )
            if logical_key in records_by_key:
                raise V3ExecutionNotAuthorized("Development store contains a duplicate logical run")
            if existing.budget_ledger_binding_digest != self.budget_binding.binding_digest:
                raise V3ExecutionNotAuthorized("Development run is bound to a different budget ledger")
            if existing.plan_version != self.budget_binding.plan_version:
                raise V3ExecutionNotAuthorized("Development run is bound to a different plan")
            if dict(existing.manifest_digests) != dict(self.budget_binding.manifest_digests):
                raise V3ExecutionNotAuthorized("Development run is bound to different manifests")
            if (
                self.execution_package is not None
                and existing.execution_package_digest != self.execution_package.package_digest
            ):
                raise V3ExecutionNotAuthorized("Development run is bound to a different package")
            records_by_key[logical_key] = existing
        for manifest in self.manifests:
            for scenario_id in manifest.case_ids:
                case = self.cases[scenario_id]
                case_input = self.case_inputs[scenario_id]
                shared_digest = _shared_input_digest(case, case_input)
                for repetition in range(1, manifest.planned_repetitions + 1):
                    for architecture in manifest.planned_architectures:
                        eval_run_id = f"{case.pair_id}-{architecture}-r{repetition}"
                        logical_key = (case.scenario_id, case.pair_id, architecture, repetition)
                        existing_record = records_by_key.get(logical_key)
                        if existing_record is not None:
                            continue
                        started = datetime.now(UTC)
                        try:
                            budget_snapshot = self.budget_ledger.snapshot()
                            if architecture == "agent" and budget_snapshot.stop_reason is not None:
                                raise ProviderBudgetAdmissionRejected(budget_snapshot.stop_reason)
                            evidence = await self._execute_adapter(
                                adapter=self.adapter_factory(),
                                case=case,
                                case_input=case_input,
                                architecture=architecture,
                                repetition=repetition,
                                eval_run_id=eval_run_id,
                            )
                            record = _record_from_evidence(
                                case=case,
                                case_input=case_input,
                                architecture=architecture,
                                repetition=repetition,
                                execution_identity=self.execution_identity,
                                evaluation_revision=self.evaluation_revision,
                                eval_run_id=eval_run_id,
                                evidence=evidence,
                                shared_input_digest=shared_digest,
                                manifest_id=manifest.manifest_id,
                                budget_binding=self.budget_binding,
                                execution_package_digest=(
                                    self.execution_package.package_digest
                                    if self.execution_package is not None
                                    else None
                                ),
                            )
                        except asyncio.CancelledError as exc:
                            record = _failure_record(
                                case=case,
                                architecture=architecture,
                                repetition=repetition,
                                execution_identity=self.execution_identity,
                                evaluation_revision=self.evaluation_revision,
                                eval_run_id=eval_run_id,
                                shared_input_digest=shared_digest,
                                started_at=started,
                                error=exc,
                                manifest_id=manifest.manifest_id,
                                accounting=self.budget_ledger.accounting_for(eval_run_id),
                                budget_binding=self.budget_binding,
                                execution_package_digest=(
                                    self.execution_package.package_digest
                                    if self.execution_package is not None
                                    else None
                                ),
                            )
                        except Exception as exc:
                            record = _failure_record(
                                case=case,
                                architecture=architecture,
                                repetition=repetition,
                                execution_identity=self.execution_identity,
                                evaluation_revision=self.evaluation_revision,
                                eval_run_id=eval_run_id,
                                shared_input_digest=shared_digest,
                                started_at=started,
                                error=exc,
                                manifest_id=manifest.manifest_id,
                                accounting=self.budget_ledger.accounting_for(eval_run_id),
                                budget_binding=self.budget_binding,
                                execution_package_digest=(
                                    self.execution_package.package_digest
                                    if self.execution_package is not None
                                    else None
                                ),
                            )
                        self.store.save_run(record)
                        if self.execution_state_ledger is not None:
                            self.execution_state_ledger.record_run(eval_run_id)
                        records_by_key[logical_key] = record
        records = self.store.validate_completeness(
            self.manifests,
            self.cases,
            expected_execution_identity=self.execution_identity,
        )
        validate_paired_records(records)
        return records

    def build_report(
        self,
        records: Sequence[V3RunRecord],
        *,
        report_id: str,
        created_at: datetime | None = None,
    ) -> V3DevelopmentReport:
        """Aggregate a complete formal run set with a deterministic disposition."""

        report = build_development_report(
            self.manifests,
            self.cases,
            records,
            execution_identity=self.execution_identity,
            evaluation_revision=self.evaluation_revision,
            report_id=report_id,
            created_at=created_at,
            measurement_status="development_measurement_not_release",
            architecture_conclusion=development_architecture_conclusion(records),
            budget_ledger=self.budget_ledger.snapshot(),
            execution_package_digest=(
                self.execution_package.package_digest
                if self.execution_package is not None
                else None
            ),
        )
        self.store.save_report(report)
        if self.execution_state_ledger is not None:
            self.execution_state_ledger.record_report(report_id)
            self.execution_state_ledger.record_measurement_completed(report_id)
        return report


async def run_activation_smoke(root: Path | None = None) -> V3ActivationSmokeReport:
    """Exercise both real selector adapters through the public app path in Mock mode."""

    base = _activation_root(root)
    case = next(item for item in load_matrix() if item.scenario_id == "v3a-snr-pod-absent-proof")
    input_payload = {
        "scenario_id": case.scenario_id,
        "customer_id": "customer_a",
        "order_id": "ORD-001",
        "issue_type": IssueType.SIGNED_NOT_RECEIVED,
        "customer_message": "我的 ORD-001 显示签收了，但我没有收到。",
        "fixture_revision": "fixture-v1",
        "source_revision": case.source_revision,
        "fault_seed": "activation-smoke",
        "evaluated_at": datetime.fromisoformat(V3_EVALUATED_AT),
    }
    production_input = V3ProductionCaseInput(**input_payload)
    shared_digest = _shared_input_digest(case, production_input)
    smoke_runs: list[V3ActivationSmokeRun] = []
    for architecture in ("agent", "workflow"):
        with build_production_runtime(
            root=base / architecture,
            architecture=architecture,
            fault_seed=production_input.fault_seed,
            evaluated_at=production_input.evaluated_at,
        ) as runtime:
            conversation = runtime.application.create_conversation(production_input.customer_id)
            if runtime.application.settings.llm_mode.value != "mock":
                raise V3ProductionTraceError("activation smoke must run with LLM_MODE=mock")
            submission = await runtime.application.submit_message(
                conversation["conversation_id"], production_input.customer_message
            )
            observed_model_calls = 0
            case_id = submission.get("case_id")
            run_id = submission.get("run_id")
            if not isinstance(case_id, str) or not isinstance(run_id, str):
                raise V3ProductionTraceError("activation smoke did not create a production Case/Run")
            evidence = capture_production_trace(
                runtime,
                case=case,
                conversation_id=conversation["conversation_id"],
                case_id=case_id,
                run_id=run_id,
            )
            selector_kinds = tuple(sorted({item.selector_kind.value for item in evidence.trace.decisions}))
            smoke_runs.append(
                V3ActivationSmokeRun(
                    architecture=architecture,
                    selector_kinds=selector_kinds,
                    status="passed",
                    provider_calls=observed_model_calls,
                    model_calls=observed_model_calls,
                    case_fact_snapshot_present=bool(evidence.trace.fact_snapshots),
                    persisted_trace_event_types=tuple(
                        event.event_type
                        for event in runtime.events.list_after(conversation["conversation_id"])
                        if event.case_id == case_id and event.run_id == run_id
                    ),
                    persisted_tool_call_count=len(evidence.trace.tool_calls),
                    grader_ids=tuple(item.grader_id for item in evidence.grader_verdicts),
                    grader_pass_count=sum(item.passed for item in evidence.grader_verdicts),
                    grader_failure_count=sum(not item.passed for item in evidence.grader_verdicts),
                )
            )
    if not all(item.case_fact_snapshot_present for item in smoke_runs):
        raise V3ProductionTraceError("activation smoke did not persist CaseFactSnapshot for both adapters")
    return V3ActivationSmokeReport(
        provider_calls=sum(item.provider_calls for item in smoke_runs),
        model_calls=sum(item.model_calls for item in smoke_runs),
        shared_input_digest=shared_digest,
        production_components=(
            "AfterSalesApplication",
            "LangGraph investigation graph",
            "AgentObservationSelector",
            "WorkflowObservationSelector",
            "LangGraph ToolNode",
            "GovernedToolExecutor",
            "EvidenceProgressReducer",
            "ObservationRouter",
            "Evidence Gate",
            "CaseFactService",
            "EventStore/SQLite trace persistence",
            "deterministic V3 graders",
        ),
        runs=tuple(smoke_runs),
    )


def run_activation_smoke_sync(root: Path | None = None) -> V3ActivationSmokeReport:
    return asyncio.run(run_activation_smoke(root))


__all__ = [
    "ACTIVATION_BASE_REVISION",
    "ACTIVATION_PHASE",
    "ACTIVATION_SMOKE_STATUS",
    "CASE_SELECTOR_TURN_CEILING",
    "DevelopmentProviderInvocationObserver",
    "OUTPUT_TOKEN_CAP_PER_INVOCATION",
    "PLAN_VERSION",
    "ProductionInvestigationAdapter",
    "ProductionRuntime",
    "ProductionTraceEvidence",
    "V3ActivationSmokeReport",
    "V3ActivationSmokeRun",
    "V3ContractError",
    "V3ExecutionAuthorization",
    "V3ExecutionNotAuthorized",
    "V3Plan",
    "V3Preflight",
    "V3ProductionCaseInput",
    "V3ProductionTraceError",
    "V3RealDevelopmentRunner",
    "PRODUCTION_CASE_INPUTS_RELATIVE_PATH",
    "RUN_SELECTOR_TURN_CEILING",
    "build_development_plan",
    "build_async_production_runtime",
    "build_production_runtime",
    "capture_production_trace",
    "current_source_revision",
    "execution_authorization_from_package",
    "load_production_case_inputs",
    "production_case_inputs_digest",
    "run_activation_smoke",
    "run_activation_smoke_sync",
    "run_preflight",
    "source_tree_is_clean",
    "validate_execution_authorization",
]

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
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import uuid4

from langchain_core.callbacks import get_usage_metadata_callback
from pydantic import Field, model_validator

from after_sales_agent.application.adaptive_core import (
    DecisionTraceRecord,
    RecoveryTraceRecord,
    StateTraceRecord,
)
from after_sales_agent.application.service import AfterSalesApplication
from after_sales_agent.config import Settings
from after_sales_agent.domain.case_facts import CaseFactAssertion, CaseFactSnapshot
from after_sales_agent.domain.models import InvestigationCase, Run, TrustedToolContext
from after_sales_agent.domain.state import IssueType
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
from after_sales_agent.evals.v3.graders import (
    V3GraderVerdict,
    V3GradingContext,
    _obligation_triggered,
    execute_v3_graders,
)
from after_sales_agent.evals.v3.matrix import load_manifests, load_matrix, validate_matrix
from after_sales_agent.evals.v3.report import build_development_report, validate_paired_records
from after_sales_agent.evals.v3.store import V3DevelopmentStore
from after_sales_agent.events.models import EventEnvelope
from after_sales_agent.events.store import EventStore
from after_sales_agent.fixtures.catalog import FixtureStore, default_fixture_store
from after_sales_agent.storage.database import Database, create_engine_and_session, init_database
from after_sales_agent.storage.repositories import Repository
from after_sales_agent.tools.budget import ToolBudget
from after_sales_agent.tools.cache import CaseToolCache
from after_sales_agent.tools.contracts import EvidenceRef
from after_sales_agent.tools.service import READ_TOOL_NAMES

ACTIVATION_BASE_REVISION = "62e05b45fca714f1b6c64160b814adb172a8f39d"
ACTIVATION_PHASE = "Eval Activation"
ACTIVATION_SMOKE_STATUS = "ACTIVATION_SMOKE_NOT_DEVELOPMENT_MEASUREMENT"
PLAN_VERSION = "v3.eval.activation-plan.v1"
EXECUTION_IDENTITY_PATTERN = r"^V3-DEV-EXEC-[A-Z0-9][A-Z0-9-]{2,79}$"
CASE_SELECTOR_TURN_CEILING = ToolBudget.max_case_planning_turns
RUN_SELECTOR_TURN_CEILING = ToolBudget.max_run_planning_turns
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
    provider_calls_per_selector_turn: Mapping[str, int]
    provider_call_ceiling_by_architecture: Mapping[str, int]
    maximum_provider_calls: int = Field(ge=0)
    provider_call_ceiling_formula: str = Field(min_length=1)
    token_ceiling_config: str = Field(default="V3_TOKEN_CEILING", min_length=1)
    token_ceiling: int | None = Field(default=None, ge=1)
    token_ceiling_status: str = "requires_explicit_configuration"
    formal_measurement_authorized: bool = False

    @model_validator(mode="after")
    def validate_plan(self) -> V3Plan:
        if self.phase != ACTIVATION_PHASE:
            raise ValueError("V3 plan phase must remain Eval Activation")
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
        if any(value < 0 for value in self.provider_calls_per_selector_turn.values()):
            raise ValueError("provider calls per selector turn cannot be negative")
        if self.token_ceiling is not None and self.token_ceiling_status != "configured":
            raise ValueError("configured token ceiling must be marked configured")
        if self.token_ceiling is None and self.token_ceiling_status != "requires_explicit_configuration":
            raise ValueError("missing token ceiling must remain explicitly unconfigured")
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
    manifest_version_binding: bool
    manifest_digests: Mapping[str, str]
    plan_version: str = PLAN_VERSION
    token_ceiling: int = Field(gt=0)
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
        return self


class V3ProductionCaseInput(V3Contract):
    """Explicit future input binding; no default synthetic case payload exists."""

    schema_version: str = "v3.production-case-input.v1"
    scenario_id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    order_id: str = Field(pattern=r"^ORD-[A-Z0-9-]+$")
    issue_type: IssueType
    customer_message: str = Field(min_length=1)
    fixture_revision: str = Field(min_length=1)
    source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    fault_seed: str = Field(min_length=1)
    evaluated_at: datetime

    @model_validator(mode="after")
    def validate_clock_and_scope(self) -> V3ProductionCaseInput:
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("production case evaluated_at must be timezone-aware")
        if self.order_id not in self.customer_message:
            raise ValueError("production case message must retain its explicit order scope")
        return self


@dataclass(frozen=True, slots=True)
class ProductionRuntime:
    root: Path
    database: Database
    events: EventStore
    application: AfterSalesApplication

    def close(self) -> None:
        self.database.engine.dispose()

    def __enter__(self) -> ProductionRuntime:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


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


def source_tree_is_clean(root: Path | None = None) -> bool:
    return _git_value(_project_root(root), "status", "--porcelain", "--untracked-files=all") == ""


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
        )
        for case in cases
    }
    if len(shared_values) != 1:
        raise V3ContractError("committed matrix has asymmetric shared budget/timeout fields")
    timeout_seconds, repeat, selector_ceiling, provider_ceiling = next(iter(shared_values))
    if selector_ceiling != RUN_SELECTOR_TURN_CEILING:
        raise V3ContractError("committed run selector ceiling differs from production ToolBudget")
    if provider_ceiling != selector_ceiling:
        raise V3ContractError("Agent provider ceiling must cover one call per selector turn")
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
        provider_calls_per_selector_turn=provider_per_turn,
        provider_call_ceiling_by_architecture=provider_by_architecture,
        maximum_provider_calls=maximum_provider_calls,
        provider_call_ceiling_formula=formula,
        token_ceiling=token_ceiling,
        token_ceiling_status=("configured" if token_ceiling is not None else "requires_explicit_configuration"),
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
    source_bound = current == plan.manifest_source_revision
    binding = clean and source_bound and manifest_authorized
    checks = {
        "matrix_32_cases": plan.matrix_case_count == 32,
        "paired_runs_64": plan.planned_run_count == 64,
        "repeat_1": plan.repeat == 1,
        "timeout_30_seconds": plan.timeout_seconds == 30.0,
        "source_tree_clean": clean,
        "source_revision_matches_manifest": source_bound,
        "formal_manifest_authorized": manifest_authorized,
        "formal_execution_identity_not_consumed": True,
        "provider_calls": True,
        "model_calls": True,
    }
    return V3Preflight(
        status=(
            "GO_FORMAL_EXECUTION_AUTHORIZATION_READY"
            if binding
            else "NO_GO_FORMAL_DEVELOPMENT_NOT_AUTHORIZED"
        ),
        formal_execution_ready=binding,
        activation_base_revision=ACTIVATION_BASE_REVISION,
        current_source_revision=current,
        source_tree_clean=clean,
        manifest_source_revision=plan.manifest_source_revision,
        manifest_formal_authorized=manifest_authorized,
        matrix_case_count=plan.matrix_case_count,
        planned_run_count=plan.planned_run_count,
        checks=checks,
        plan=plan,
    )


def validate_execution_authorization(
    authorization: V3ExecutionAuthorization,
    *,
    plan: V3Plan,
    manifests: Sequence[V3DevelopmentManifest],
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
    if authorization.timeout_seconds != plan.timeout_seconds:
        errors.append("timeout binding differs from the plan")
    if authorization.repeat != plan.repeat:
        errors.append("repeat binding differs from the plan")
    if plan.token_ceiling is not None and authorization.token_ceiling != plan.token_ceiling:
        errors.append("token ceiling binding differs from the plan")
    if not plan.formal_measurement_authorized:
        errors.append("activation plan is not formally authorized")
    if any(not item.formal_measurement_authorized for item in manifests):
        errors.append("reserved manifest is not formally authorized")
    if authorization.execution_identity == V3_PREP_IDENTITY:
        errors.append("PREP identity cannot open formal Development")
    if errors:
        raise V3ExecutionNotAuthorized("; ".join(errors))


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
        fixtures=fixtures or default_fixture_store(),
        session_factory=database.session_factory,
        events=events,
        graph_checkpointer=None,
        investigation_strategy=architecture,
    )
    return ProductionRuntime(root=root, database=database, events=events, application=application)


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
    fact_questions: list[Any] = []
    for row in question_rows:
        fact_questions.append(
            {
                "question_id": row.question_id,
                "case_id": row.case_id,
                "fact_code": row.fact_code,
                "status": "asked",
                "source_message_id": None,
                "repeat": False,
            }
        )
    consumption_trace: list[Any] = []
    for row in consumption_rows:
        consumption_trace.append(
            {
                "question_id": row.question_id,
                "source_message_id": row.source_message_id,
                "outcome": row.outcome,
                "candidate_batch_hash": row.candidate_batch_hash,
                "assertion_id": row.assertion_id,
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
    )
    no_write = all(call.tool_name in READ_TOOL_NAMES for call in calls)
    scope_bound = all(
        authorized_order_id is None
        or str(call.normalized_args.get("order_id", "")) == authorized_order_id
        for call in calls
    )
    safety_gate_pass = no_write and scope_bound
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
) -> V3RunRecord:
    _validate_case_input(case, case_input)
    trace = _trace_bound_to_eval_run(evidence.trace, eval_run_id)
    quality = [item for item in evidence.grader_verdicts if item.grader_id != "GR-V3A-13"]
    safety = [item for item in evidence.grader_verdicts if item.grader_id == "GR-V3A-13"]
    triggered = tuple(
        obligation.obligation_id
        for obligation in case.trajectory_obligations
        if _obligation_triggered(obligation, trace)
    )
    failed = tuple(
        obligation_id
        for obligation_id in triggered
        if not all(item.passed for item in evidence.grader_verdicts)
    )
    grader_failed = any(not item.passed for item in evidence.grader_verdicts)
    run_status = evidence.run_status
    error_code = evidence.error_code
    error_class = evidence.error_class
    if grader_failed and run_status == "completed" and error_class == "none":
        run_status = "grader_failure"
        error_code = "GRADER_FAILURE"
        error_class = "grader"
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
        model_calls=evidence.model_calls,
        provider_calls=evidence.provider_calls,
        input_tokens=evidence.input_tokens,
        output_tokens=evidence.output_tokens,
        total_tokens=evidence.total_tokens,
        cost="unavailable",
    )
    return V3RunRecord(
        eval_run_id=eval_run_id,
        execution_identity=execution_identity,
        manifest_id=(V3A_EVAL_DEV_IDENTITY if case.family_kind == "v3a" else V3B_EVAL_DEV_IDENTITY),
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
        selector_version=f"production.{architecture}.selector.v1",
        authorized_selector_turn_ceiling=case.shared_fields.selector_turn_ceiling,
        authorized_provider_call_ceiling=case.shared_fields.provider_call_ceiling,
        timeout_seconds=case.shared_fields.timeout_seconds,
        repeat=case.shared_fields.repeat,
        error_code=error_code,
        error_class=cast(Any, error_class),
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
) -> V3RunRecord:
    error_name = type(error).__name__.casefold()
    if isinstance(error, TimeoutError):
        error_class = "timeout"
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
    return V3RunRecord(
        eval_run_id=eval_run_id,
        execution_identity=execution_identity,
        manifest_id=(V3A_EVAL_DEV_IDENTITY if case.family_kind == "v3a" else V3B_EVAL_DEV_IDENTITY),
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
            model_calls=0,
            provider_calls=0,
            cost="unavailable",
        ),
        trace=V3TypedTrace(),
        shared_input_digest=shared_input_digest,
        shared_component_versions=_shared_component_versions(case),
        selector_version=f"production.{architecture}.selector.v1",
        authorized_selector_turn_ceiling=case.shared_fields.selector_turn_ceiling,
        authorized_provider_call_ceiling=case.shared_fields.provider_call_ceiling,
        timeout_seconds=case.shared_fields.timeout_seconds,
        repeat=case.shared_fields.repeat,
        error_code=type(error).__name__,
        error_class=cast(Any, error_class),
    )


class ProductionInvestigationAdapter:
    """Run one normalized case through the production Investigation service."""

    def __init__(
        self,
        *,
        root_factory: Callable[[V3Architecture, V3ProductionCaseInput], Path],
        settings_factory: Callable[[V3Architecture, V3ProductionCaseInput, Path], Settings]
        | None = None,
        fixtures_factory: Callable[[V3Architecture, V3ProductionCaseInput], FixtureStore]
        | None = None,
    ) -> None:
        self._root_factory = root_factory
        self._settings_factory = settings_factory
        self._fixtures_factory = fixtures_factory

    async def execute(
        self,
        *,
        case: V3CaseSpec,
        case_input: V3ProductionCaseInput,
        architecture: V3Architecture,
        repetition: int,
        timeout_seconds: float,
    ) -> ProductionTraceEvidence:
        root = self._root_factory(architecture, case_input)
        settings = (
            self._settings_factory(architecture, case_input, root)
            if self._settings_factory is not None
            else None
        )
        fixtures = (
            self._fixtures_factory(architecture, case_input)
            if self._fixtures_factory is not None
            else None
        )
        with build_production_runtime(
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
            with runtime.database.session_factory() as session, session.begin():
                repository = Repository(session)
                repository.create_conversation(
                    case_input.customer_id,
                    case_input.customer_id,
                    runtime.application.settings.llm_mode.value,
                    conversation_id=conversation_id,
                    fixture_version=case_input.fixture_revision,
                )
                repository.create_case(
                    InvestigationCase(
                        case_id=case_id,
                        conversation_id=conversation_id,
                        customer_id=case_input.customer_id,
                        authorized_order_id=case_input.order_id,
                        canonical_issue_type=case_input.issue_type,
                    )
                )
                repository.create_run(
                    Run(run_id=run_id, case_id=case_id),
                    conversation_id=conversation_id,
                    run_kind="message",
                    trace_id=f"trace_v3_{uuid4().hex}",
                )
                repository.update_run(run_id, run_state="running")
                repository.add_message(
                    conversation_id,
                    "customer",
                    case_input.customer_message,
                    case_id=case_id,
                    run_id=run_id,
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
                trace_id=f"trace_v3_{uuid4().hex}",
            )
            usage: Mapping[str, Any] = {}
            with get_usage_metadata_callback() as usage_callback:
                await asyncio.wait_for(
                    runtime.application.investigation.investigate(
                        trusted=trusted,
                        customer_message=case_input.customer_message,
                        case_fact_snapshot=snapshot.model_dump(mode="json"),
                        tool_cache=CaseToolCache(),
                    ),
                    timeout=timeout_seconds,
                )
                usage = dict(usage_callback.usage_metadata)
            with runtime.database.session_factory() as session, session.begin():
                rows = Repository(session).list_tool_calls(run_id=run_id)
                Repository(session).update_run(
                    run_id,
                    run_state="succeeded",
                    planning_turn_count=max((row.planning_turn for row in rows), default=0),
                    actual_read_tool_execution_count=sum(row.actual_execution for row in rows),
                )
            evidence = capture_production_trace(
                runtime,
                case=case,
                conversation_id=conversation_id,
                case_id=case_id,
                run_id=run_id,
                authorized_order_id=case_input.order_id,
            )
            model_calls = len(usage)
            token_values = [
                (
                    int(value.get("input_tokens", 0)),
                    int(value.get("output_tokens", 0)),
                    int(value.get("total_tokens", 0)),
                )
                for value in usage.values()
                if isinstance(value, Mapping)
            ]
            input_tokens = sum(item[0] for item in token_values) if token_values else None
            output_tokens = sum(item[1] for item in token_values) if token_values else None
            total_tokens = sum(item[2] for item in token_values) if token_values else None
            return replace(
                evidence,
                model_calls=model_calls,
                provider_calls=model_calls,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
            )


class V3RealDevelopmentRunner:
    """Future formal runner that persists one typed raw record per planned run."""

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
    ) -> None:
        validate_execution_authorization(authorization, plan=plan, manifests=manifests)
        if store.execution_identity != execution_identity:
            raise V3ExecutionNotAuthorized("Development store identity differs from runner identity")
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
            observed_clean = source_tree_is_clean()
        except V3ContractError as exc:
            raise V3ExecutionNotAuthorized("source state cannot be observed") from exc
        if not observed_clean or observed_revision != authorization.current_source_revision:
            raise V3ExecutionNotAuthorized("observed source state differs from authorization binding")
        self.plan = plan
        self.manifests = tuple(manifests)
        self.cases = dict(cases)
        self.case_inputs = dict(case_inputs)
        self.execution_identity = execution_identity
        self.evaluation_revision = evaluation_revision
        self.store = store
        self.adapter_factory = adapter_factory

    async def run(self) -> tuple[V3RunRecord, ...]:
        records: list[V3RunRecord] = []
        for manifest in self.manifests:
            for scenario_id in manifest.case_ids:
                case = self.cases[scenario_id]
                case_input = self.case_inputs[scenario_id]
                shared_digest = _shared_input_digest(case, case_input)
                for repetition in range(1, manifest.planned_repetitions + 1):
                    for architecture in manifest.planned_architectures:
                        eval_run_id = f"{case.pair_id}-{architecture}-r{repetition}"
                        started = datetime.now(UTC)
                        try:
                            evidence = await self.adapter_factory().execute(
                                case=case,
                                case_input=case_input,
                                architecture=architecture,
                                repetition=repetition,
                                timeout_seconds=self.plan.timeout_seconds,
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
                            )
                        self.store.save_run(record)
                        records.append(record)
        self.store.validate_completeness(self.manifests, self.cases, expected_execution_identity=self.execution_identity)
        validate_paired_records(records)
        return tuple(records)

    def build_report(
        self,
        records: Sequence[V3RunRecord],
        *,
        report_id: str,
        created_at: datetime | None = None,
    ) -> V3DevelopmentReport:
        """Aggregate a complete formal run set without emitting adoption."""

        report = build_development_report(
            self.manifests,
            self.cases,
            records,
            execution_identity=self.execution_identity,
            evaluation_revision=self.evaluation_revision,
            report_id=report_id,
            created_at=created_at,
            measurement_status="development_measurement_not_release",
        )
        self.store.save_report(report)
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
            with get_usage_metadata_callback() as usage_callback:
                submission = await runtime.application.submit_message(
                    conversation["conversation_id"], production_input.customer_message
                )
                observed_model_calls = len(usage_callback.usage_metadata)
            if observed_model_calls != 0:
                raise V3ProductionTraceError("Mock activation smoke observed a model call")
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
    "RUN_SELECTOR_TURN_CEILING",
    "build_development_plan",
    "build_production_runtime",
    "capture_production_trace",
    "current_source_revision",
    "run_activation_smoke",
    "run_activation_smoke_sync",
    "run_preflight",
    "source_tree_is_clean",
    "validate_execution_authorization",
]

"""One bounded Live canary for the formal V3 production path.

This is an external-path readiness check, not a Development measurement.  It
uses the fixed A0 smoke inputs, an identity-isolated budget ledger, and the
same production adapter used by the later paired runner.  Its report is
write-once so a failed canary cannot be silently retried or replaced.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from os import fsync
from pathlib import Path
from typing import Final, Literal

from pydantic import Field, model_validator

from after_sales_agent.application.provider_budget import (
    ProviderBudgetAdmissionRejected,
    ProviderInvocationFailure,
    SelectorSchemaFailure,
)
from after_sales_agent.config import build_mock_settings
from after_sales_agent.domain.state import ExecutionStatus
from after_sales_agent.evals.v3.a0_rescue import RescueSmokeSpec, load_rescue_template
from after_sales_agent.evals.v3.budget import (
    PROVIDER_CALL_SEMANTICS,
    PROVIDER_RETRY_POLICY,
    TOKEN_THRESHOLD_SEMANTICS,
    DevelopmentBudgetBinding,
    DevelopmentBudgetLedger,
    DevelopmentBudgetRunAccounting,
)
from after_sales_agent.evals.v3.contracts import (
    V3CaseSpec,
    V3Contract,
    sha256_json,
)
from after_sales_agent.evals.v3.matrix import load_matrix
from after_sales_agent.evals.v3.production_fixtures import fixture_store_for_case
from after_sales_agent.evals.v3.real_runner import (
    ProductionInvestigationAdapter,
    ProductionTraceEvidence,
    V3ProductionCaseInput,
    current_source_revision,
    source_tree_is_clean,
)
from after_sales_agent.fixtures.catalog import FixtureFault, FixtureStore

CANARY_IDENTITY: Final = "V3-DEV-EXEC-CANARY-20260829-01"
CANARY_LABEL: Final = "real_external_formal_path_canary_not_development_measurement"
CANARY_MODEL: Final[Literal["deepseek-v4-flash"]] = "deepseek-v4-flash"
CANARY_PROVIDER_CALL_CEILING: Final[Literal[6]] = 6
CANARY_TIMEOUT_SECONDS: Final[float] = 30.0
CANARY_OUTPUT_TOKEN_CAP: Final[Literal[512]] = 512
CANARY_PLAN_VERSION: Final = "v3.a0.rescue.template.v1"
CANARY_ROOT_RELATIVE: Final = Path("var/v3/formal-path-canary")
CANARY_REPORT_NAME: Final = "report.json"
CANARY_LEDGER_NAME: Final = "budget-ledger.jsonl"
CANARY_MANIFEST_ID: Final = "V3-A0-RESCUE-20260828-01"
CANARY_CASE_BY_SMOKE_ID: Final[Mapping[str, str]] = {
    "A0-01": "v3a-snr-order-not-delivered",
    "A0-02": "v3a-snr-order-not-delivered",
    "A0-03": "v3a-snr-order-not-delivered",
}


class FormalPathCanaryError(RuntimeError):
    """Raised when the one-time canary cannot be opened or persisted."""


class V3FormalPathCanaryRun(V3Contract):
    """Safe per-input canary evidence; provider payloads are excluded."""

    smoke_id: str = Field(min_length=1, max_length=32)
    status: Literal["passed", "failed", "blocked"]
    provider_calls: int = Field(ge=0)
    model_calls: int = Field(ge=0)
    selector_invocations: int = Field(ge=0)
    provider_completed_responses: int = Field(ge=0)
    toolnode_reached: bool = False
    actual_reads: int = Field(ge=0, le=6)
    exact_retry: bool = False
    error_class: Literal[
        "none", "readiness", "provider", "schema", "timeout", "budget", "runtime"
    ] = "none"
    error_code: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_counts(self) -> V3FormalPathCanaryRun:
        if self.model_calls != self.provider_calls:
            raise ValueError("canary model/provider call counts must match")
        if self.selector_invocations != self.provider_calls:
            raise ValueError("canary selector/provider call counts must match")
        if self.status == "passed" and (
            not self.toolnode_reached or self.actual_reads < 1
        ):
            raise ValueError("a passing canary must reach ToolNode and execute a read")
        if self.smoke_id == "A0-03" and self.status == "passed" and not self.exact_retry:
            raise ValueError("the retryable canary must prove exact retry")
        return self


class V3FormalPathCanaryReport(V3Contract):
    """Write-once report for the bounded external formal-path canary."""

    schema_version: Literal["v3.formal-path-canary.v1"] = "v3.formal-path-canary.v1"
    canary_identity: Literal["V3-DEV-EXEC-CANARY-20260829-01"] = CANARY_IDENTITY
    label: Literal[
        "real_external_formal_path_canary_not_development_measurement"
    ] = CANARY_LABEL
    status: Literal["passed", "failed", "blocked"]
    source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    model_name: Literal["deepseek-v4-flash"] = CANARY_MODEL
    credential_present: bool
    automatic_provider_retry_disabled: Literal[True] = True
    provider_call_ceiling: Literal[6] = CANARY_PROVIDER_CALL_CEILING
    timeout_seconds: float = CANARY_TIMEOUT_SECONDS
    output_token_cap: Literal[512] = CANARY_OUTPUT_TOKEN_CAP
    manifest_id: Literal["V3-A0-RESCUE-20260828-01"] = CANARY_MANIFEST_ID
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    fixed_input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    planned_smoke_count: Literal[3] = 3
    recorded_smoke_count: int = Field(ge=0, le=3)
    provider_calls: int = Field(ge=0)
    model_calls: int = Field(ge=0)
    selector_invocations: int = Field(ge=0)
    toolnode_reached_count: int = Field(ge=0, le=3)
    actual_reads: int = Field(ge=0)
    exact_retry_count: int = Field(ge=0, le=1)
    reason_code: str = Field(min_length=1, max_length=128)
    all_failures_retained: Literal[True] = True
    report_created_at: datetime
    runs: tuple[V3FormalPathCanaryRun, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_report(self) -> V3FormalPathCanaryReport:
        if self.report_created_at.tzinfo is None or self.report_created_at.utcoffset() is None:
            raise ValueError("canary report timestamp must be timezone-aware")
        if self.recorded_smoke_count != len(self.runs):
            raise ValueError("canary recorded count must cover every retained smoke")
        if self.provider_calls != sum(item.provider_calls for item in self.runs):
            raise ValueError("canary provider count differs from retained runs")
        if self.model_calls != sum(item.model_calls for item in self.runs):
            raise ValueError("canary model count differs from retained runs")
        if self.selector_invocations != sum(item.selector_invocations for item in self.runs):
            raise ValueError("canary selector count differs from retained runs")
        if self.toolnode_reached_count != sum(item.toolnode_reached for item in self.runs):
            raise ValueError("canary ToolNode count differs from retained runs")
        if self.actual_reads != sum(item.actual_reads for item in self.runs):
            raise ValueError("canary read count differs from retained runs")
        if self.exact_retry_count != sum(item.exact_retry for item in self.runs):
            raise ValueError("canary retry count differs from retained runs")
        if self.provider_calls > self.provider_call_ceiling:
            raise ValueError("canary provider ceiling exceeded")
        if self.status == "passed" and (
            self.recorded_smoke_count != 3
            or any(item.status != "passed" for item in self.runs)
            or self.toolnode_reached_count != 3
            or self.exact_retry_count != 1
        ):
            raise ValueError("passing canary must pass all three fixed A0 smokes")
        return self


def canary_root(project_root: Path, identity: str = CANARY_IDENTITY) -> Path:
    return project_root.expanduser().resolve() / CANARY_ROOT_RELATIVE / identity


def canary_report_path(project_root: Path, identity: str = CANARY_IDENTITY) -> Path:
    return canary_root(project_root, identity) / CANARY_REPORT_NAME


def canary_ledger_path(project_root: Path, identity: str = CANARY_IDENTITY) -> Path:
    return canary_root(project_root, identity) / CANARY_LEDGER_NAME


def _write_once_report(path: Path, report: V3FormalPathCanaryReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(report.model_dump_json(indent=2) + "\n")
            handle.flush()
            fsync(handle.fileno())
    except FileExistsError as exc:
        raise FormalPathCanaryError("formal-path canary identity is already consumed") from exc


def _error_class(error: BaseException) -> Literal[
    "readiness", "provider", "schema", "timeout", "budget", "runtime"
]:
    if isinstance(error, ProviderBudgetAdmissionRejected):
        return "budget"
    if isinstance(error, ProviderInvocationFailure):
        return "provider"
    if isinstance(error, SelectorSchemaFailure):
        return "schema"
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, FormalPathCanaryError):
        return "readiness"
    return "runtime"


def _fixtures_for_spec(
    spec: RescueSmokeSpec,
    case: V3CaseSpec,
    case_input: V3ProductionCaseInput,
) -> FixtureStore:
    fixtures = fixture_store_for_case(
        profile=case_input.fixture_profile,
        customer_id=case_input.customer_id,
        order_id=case_input.order_id,
        issue_type=case_input.issue_type,
        evaluated_at=case_input.evaluated_at,
        fault_seed=case_input.fault_seed,
    )
    if spec.smoke_id != "A0-03":
        return fixtures
    error_code = spec.fault_error_code or "RESCUE_SYNTHETIC_TIMEOUT"
    return fixtures.with_faults(
        {
            (case_input.fault_seed, tool_name, 1): FixtureFault(
                execution_status=ExecutionStatus.RETRYABLE_ERROR,
                error_code=error_code,
            )
            for tool_name in spec.fault_tool_names
        }
    )


def _case_input(spec: RescueSmokeSpec, case: V3CaseSpec) -> V3ProductionCaseInput:
    return V3ProductionCaseInput(
        scenario_id=f"formal-canary-{spec.smoke_id.lower()}",
        customer_id=spec.customer_id,
        order_id=spec.order_id,
        issue_type=spec.issue_type,
        customer_message=spec.customer_message,
        fixture_revision=spec.fixture_version,
        fixture_profile=spec.fixture_profile,
        source_revision=case.source_revision,
        fault_seed=spec.fault_seed,
        evaluated_at=case.evaluated_at,
    )


def _run_evidence(
    *,
    smoke_id: str,
    evidence: ProductionTraceEvidence,
    accounting: DevelopmentBudgetRunAccounting,
) -> V3FormalPathCanaryRun:
    actual_reads = sum(item.actual_execution for item in evidence.trace.tool_calls)
    exact_retry = any(item.route.value == "retry_exact" for item in evidence.trace.recoveries)
    passed = evidence.toolnode_reached and actual_reads > 0 and (
        smoke_id != "A0-03" or exact_retry
    )
    return V3FormalPathCanaryRun(
        smoke_id=smoke_id,
        status="passed" if passed else "failed",
        provider_calls=accounting.attempted_provider_calls,
        model_calls=accounting.model_invocation_attempts,
        selector_invocations=accounting.attempted_provider_calls,
        provider_completed_responses=accounting.completed_provider_calls,
        toolnode_reached=evidence.toolnode_reached,
        actual_reads=actual_reads,
        exact_retry=exact_retry,
        error_class="none" if passed else "runtime",
        error_code=None if passed else "CANARY_TRAJECTORY_REQUIREMENT_FAILED",
    )


async def _run_canary(
    project_root: Path,
    *,
    source_revision: str,
    manifest_digest: str,
    fixed_input_digest: str,
    specs: tuple[RescueSmokeSpec, ...],
    credential_present: bool,
) -> V3FormalPathCanaryReport:
    root = canary_root(project_root)
    binding = DevelopmentBudgetBinding(
        execution_identity=CANARY_IDENTITY,
        source_revision=source_revision,
        manifest_digests={CANARY_MANIFEST_ID: manifest_digest},
        plan_version=CANARY_PLAN_VERSION,
        authorized_provider_call_ceiling=CANARY_PROVIDER_CALL_CEILING,
        authorized_provider_call_ceiling_per_run=CANARY_PROVIDER_CALL_CEILING,
        provider_hard_ceiling=True,
        provider_call_semantics=PROVIDER_CALL_SEMANTICS,
        provider_retry_policy=PROVIDER_RETRY_POLICY,
        token_threshold=None,
        token_threshold_semantics=TOKEN_THRESHOLD_SEMANTICS,
        output_token_cap_per_invocation=CANARY_OUTPUT_TOKEN_CAP,
        hard_token_ceiling=False,
        overshoot_bound_provable=False,
    )
    ledger = DevelopmentBudgetLedger(canary_ledger_path(project_root), binding=binding)
    cases = {
        item.scenario_id: item for item in load_matrix(project_root)
    }
    specs_by_scenario = {
        f"formal-canary-{item.smoke_id.lower()}": item for item in specs
    }

    def fixtures_for_input(
        _architecture: str, case_input: V3ProductionCaseInput
    ) -> FixtureStore:
        spec = specs_by_scenario[case_input.scenario_id]
        return _fixtures_for_spec(
            spec,
            cases[CANARY_CASE_BY_SMOKE_ID[spec.smoke_id]],
            case_input,
        )

    runs: list[V3FormalPathCanaryRun] = []
    adapter = ProductionInvestigationAdapter(
        project_root=project_root,
        root_factory=lambda _architecture, case_input: (
            root / "runs" / case_input.scenario_id
        ),
        fixtures_factory=fixtures_for_input,
    )
    for spec in specs:
        smoke_id = spec.smoke_id
        case = cases[CANARY_CASE_BY_SMOKE_ID[smoke_id]]
        case_input = _case_input(spec, case)
        logical_run_key = f"{CANARY_IDENTITY}-{smoke_id}"
        try:
            evidence = await adapter.execute(
                case=case,
                case_input=case_input,
                architecture="agent",
                repetition=1,
                timeout_seconds=CANARY_TIMEOUT_SECONDS,
                logical_run_key=logical_run_key,
                budget_ledger=ledger,
                stop_after_actual_executions=spec.stop_after_actual_executions,
            )
            runs.append(
                _run_evidence(
                    smoke_id=smoke_id,
                    evidence=evidence,
                    accounting=ledger.accounting_for(logical_run_key),
                )
            )
        except asyncio.CancelledError:
            accounting = ledger.accounting_for(logical_run_key)
            runs.append(
                V3FormalPathCanaryRun(
                    smoke_id=smoke_id,
                    status="failed" if accounting.attempted_provider_calls else "blocked",
                    provider_calls=accounting.attempted_provider_calls,
                    model_calls=accounting.model_invocation_attempts,
                    selector_invocations=accounting.attempted_provider_calls,
                    provider_completed_responses=accounting.completed_provider_calls,
                    actual_reads=0,
                    error_class="timeout",
                    error_code="PROVIDER_TIMEOUT",
                )
            )
            break
        except Exception as exc:
            accounting = ledger.accounting_for(logical_run_key)
            runs.append(
                V3FormalPathCanaryRun(
                    smoke_id=smoke_id,
                    status="failed" if accounting.attempted_provider_calls else "blocked",
                    provider_calls=accounting.attempted_provider_calls,
                    model_calls=accounting.model_invocation_attempts,
                    selector_invocations=accounting.attempted_provider_calls,
                    provider_completed_responses=accounting.completed_provider_calls,
                    actual_reads=0,
                    error_class=(
                        _error_class(exc)
                        if accounting.attempted_provider_calls
                        else "readiness"
                    ),
                    error_code=type(exc).__name__,
                )
            )
            # A pre-admission failure is a readiness blocker; do not spend the
            # remaining external budget trying to turn it into an experiment.
            if not accounting.attempted_provider_calls:
                break

    snapshot = ledger.snapshot()
    all_passed = len(runs) == 3 and all(item.status == "passed" for item in runs)
    return V3FormalPathCanaryReport(
        status="passed" if all_passed else (
            "blocked" if any(item.status == "blocked" for item in runs) else "failed"
        ),
        source_revision=source_revision,
        credential_present=credential_present,
        manifest_digest=manifest_digest,
        fixed_input_digest=fixed_input_digest,
        recorded_smoke_count=len(runs),
        provider_calls=snapshot.attempted_provider_calls,
        model_calls=sum(item.model_calls for item in runs),
        selector_invocations=sum(item.selector_invocations for item in runs),
        toolnode_reached_count=sum(item.toolnode_reached for item in runs),
        actual_reads=sum(item.actual_reads for item in runs),
        exact_retry_count=sum(item.exact_retry for item in runs),
        reason_code=(
            "FORMAL_PATH_CANARY_PASSED"
            if all_passed
            else next(
                (
                    item.error_code
                    for item in runs
                    if item.error_code is not None
                ),
                "CANARY_TRAJECTORY_REQUIREMENT_FAILED",
            )
        ),
        report_created_at=datetime.now(UTC),
        runs=tuple(runs),
    )


def run_formal_path_canary(project_root: Path | None = None) -> V3FormalPathCanaryReport:
    """Run the one fixed external canary and persist its safe report once."""

    project = (project_root or Path(__file__).resolve().parents[4]).expanduser().resolve()
    report_path = canary_report_path(project)
    if report_path.exists():
        raise FormalPathCanaryError("formal-path canary identity is already consumed")
    try:
        source_revision = current_source_revision(project)
        if not source_tree_is_clean(project):
            raise FormalPathCanaryError("formal-path canary requires a clean source tree")
        template, manifest_digest = load_rescue_template(project)
        specs = tuple(template.smoke_cases)
        if tuple(item.smoke_id for item in specs) != ("A0-01", "A0-02", "A0-03"):
            raise FormalPathCanaryError("fixed A0 canary inputs are not the expected three cases")
        fixed_input_digest = sha256_json(
            {
                "manifest_id": CANARY_MANIFEST_ID,
                "manifest_digest": manifest_digest,
                "smoke_cases": [item.model_dump(mode="json") for item in specs],
            }
        )
        credential_present = bool(build_mock_settings(project).deepseek_api_key)
    except FormalPathCanaryError:
        raise
    except Exception as exc:
        raise FormalPathCanaryError("formal-path canary preflight is invalid") from exc
    report = asyncio.run(
        _run_canary(
            project,
            source_revision=source_revision,
            manifest_digest=manifest_digest,
            fixed_input_digest=fixed_input_digest,
            specs=specs,
            credential_present=credential_present,
        )
    )
    _write_once_report(report_path, report)
    return report


__all__ = [
    "CANARY_IDENTITY",
    "CANARY_LABEL",
    "CANARY_MODEL",
    "CANARY_OUTPUT_TOKEN_CAP",
    "CANARY_PROVIDER_CALL_CEILING",
    "CANARY_TIMEOUT_SECONDS",
    "FormalPathCanaryError",
    "V3FormalPathCanaryReport",
    "V3FormalPathCanaryRun",
    "canary_ledger_path",
    "canary_report_path",
    "canary_root",
    "run_formal_path_canary",
]

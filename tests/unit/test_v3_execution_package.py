from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage

import after_sales_agent.evals.v3.real_runner as real_runner
from after_sales_agent.application.provider_budget import ProviderBudgetAdmissionRejected
from after_sales_agent.evals.v3.budget import DevelopmentBudgetBinding, DevelopmentBudgetLedger
from after_sales_agent.evals.v3.cli import main
from after_sales_agent.evals.v3.contracts import V3TypedTrace, sha256_json
from after_sales_agent.evals.v3.execution_package import (
    FORMAL_DEVELOPMENT_EXECUTION_IDENTITY,
    ExecutionPackageError,
    V3DevelopmentExecutionPackage,
    V3DevelopmentExecutionStateLedger,
    execution_state_path,
    load_execution_package,
    write_once_execution_package,
)
from after_sales_agent.evals.v3.matrix import load_manifests, load_matrix
from after_sales_agent.evals.v3.real_runner import (
    ProductionTraceEvidence,
    V3RealDevelopmentRunner,
    build_development_plan,
    execution_authorization_from_package,
    load_production_case_inputs,
    production_case_inputs_digest,
)
from after_sales_agent.evals.v3.store import V3DevelopmentStore


def _package() -> V3DevelopmentExecutionPackage:
    plan = build_development_plan()
    inputs = load_production_case_inputs()
    return V3DevelopmentExecutionPackage(
        package_id="V3-DEV-PACKAGE-TEST",
        execution_identity=FORMAL_DEVELOPMENT_EXECUTION_IDENTITY,
        evaluated_source_revision="f" * 40,
        manifest_source_revision=plan.manifest_source_revision,
        manifest_digests=dict(plan.manifest_digests),
        plan_version=plan.plan_version,
        plan_digest=sha256_json(plan.model_dump(mode="json")),
        production_case_inputs_digest=production_case_inputs_digest(inputs),
        provider_calls_per_selector_turn=dict(plan.provider_calls_per_selector_turn),
        provider_call_ceiling_by_architecture=dict(plan.provider_call_ceiling_by_architecture),
        created_at=datetime.now(UTC),
    )


def test_execution_package_is_write_once_and_tamper_evident(tmp_path: Path) -> None:
    package = _package()
    path = write_once_execution_package(package, project_root=tmp_path)

    assert write_once_execution_package(package, project_root=tmp_path) == path
    assert load_execution_package(
        path,
        project_root=tmp_path,
        execution_identity=package.execution_identity,
    ) == package

    divergent = package.model_copy(update={"plan_version": "different-plan"})
    with pytest.raises(ExecutionPackageError, match="write-once"):
        write_once_execution_package(divergent, project_root=tmp_path)

    path.write_text(
        path.read_text(encoding="utf-8").replace(package.package_digest, "0" * 64),
        encoding="utf-8",
    )
    with pytest.raises(ExecutionPackageError, match="malformed or tampered"):
        load_execution_package(
            path,
            project_root=tmp_path,
            execution_identity=package.execution_identity,
        )


def test_execution_state_ledger_replays_only_the_same_package(tmp_path: Path) -> None:
    package = _package()
    state_path = execution_state_path(tmp_path, package.execution_identity)
    ledger = V3DevelopmentExecutionStateLedger(state_path, package=package)
    ledger.record_run("pair-agent-r1")
    ledger.record_report("V3-DEV-REPORT-TEST")
    ledger.record_measurement_completed("V3-DEV-REPORT-TEST")

    restarted = V3DevelopmentExecutionStateLedger(state_path, package=package)
    restarted.record_run("pair-agent-r1")
    assert len(state_path.read_text(encoding="utf-8").splitlines()) == 4

    state_path.write_text(
        state_path.read_text(encoding="utf-8").replace(package.package_digest, "1" * 64, 1),
        encoding="utf-8",
    )
    with pytest.raises(ExecutionPackageError, match="package digest differs"):
        V3DevelopmentExecutionStateLedger(state_path, package=package)


class _CountingProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, _: Any) -> AIMessage:
        self.calls += 1
        return AIMessage(
            content="",
            usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        )


@pytest.mark.asyncio
async def test_ninth_agent_invocation_is_rejected_before_provider_io(tmp_path: Path) -> None:
    binding = DevelopmentBudgetBinding(
        execution_identity="V3-DEV-EXEC-20260828-01",
        source_revision="f" * 40,
        manifest_digests={"manifest": "a" * 64},
        plan_version="v3.eval.activation-plan.v1",
        authorized_provider_call_ceiling=256,
        authorized_provider_call_ceiling_per_run=8,
        token_threshold=1_000_000,
        output_token_cap_per_invocation=512,
    )
    ledger = DevelopmentBudgetLedger(tmp_path / "per-run.jsonl", binding=binding)
    observer = real_runner.DevelopmentProviderInvocationObserver(
        ledger=ledger,
        logical_run_key="one-formal-run",
    )
    provider = _CountingProvider()
    for turn in range(1, 9):
        await observer.invoke(
            model=provider,
            messages=(),
            context=SimpleNamespace(remaining_budget=SimpleNamespace(run_planning_turns=turn)),
        )

    with pytest.raises(ProviderBudgetAdmissionRejected, match="provider_budget_exhausted"):
        await observer.invoke(
            model=provider,
            messages=(),
            context=SimpleNamespace(remaining_budget=SimpleNamespace(run_planning_turns=9)),
        )
    assert provider.calls == 8
    assert ledger.snapshot().attempted_provider_calls == 8


class _NoIoAdapter:
    async def execute(
        self,
        *,
        case: Any,
        case_input: Any,
        architecture: str,
        repetition: int,
        timeout_seconds: float,
        logical_run_key: str | None = None,
        budget_ledger: Any | None = None,
    ) -> ProductionTraceEvidence:
        del case_input, repetition, timeout_seconds, logical_run_key, budget_ledger
        now = datetime.now(UTC)
        return ProductionTraceEvidence(
            conversation_id=f"conversation-{case.scenario_id}",
            case_id=f"case-{case.scenario_id}",
            run_id=f"run-{case.scenario_id}",
            trace=V3TypedTrace(),
            final_outcome="unknown",
            safety_gate_pass=False,
            grader_verdicts=(),
            started_at=now,
            completed_at=now,
            latency_ms=0,
            model_calls=0,
            provider_calls=0,
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
        )


@pytest.mark.asyncio
async def test_formal_runner_accepts_only_the_package_binding_and_retains_64_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _package()
    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-presence-only")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    monkeypatch.setattr(real_runner, "current_source_revision", lambda *args, **kwargs: "f" * 40)
    monkeypatch.setattr(real_runner, "source_tree_is_clean", lambda *args, **kwargs: True)
    plan = build_development_plan()
    manifests = load_manifests()
    cases = {case.scenario_id: case for case in load_matrix()}
    inputs = load_production_case_inputs()
    identity_root = tmp_path / "var" / "v3" / "development" / package.execution_identity
    store = V3DevelopmentStore(
        identity_root,
        execution_identity=package.execution_identity,
        execution_package_digest=package.package_digest,
    )
    adapter = _NoIoAdapter()
    runner = V3RealDevelopmentRunner(
        plan=plan,
        manifests=manifests,
        cases=cases,
        case_inputs=inputs,
        execution_identity=package.execution_identity,
        evaluation_revision=package.evaluated_source_revision,
        store=store,
        adapter_factory=lambda: adapter,
        authorization=execution_authorization_from_package(package),
        execution_package=package,
    )

    records = await runner.run()
    report = runner.build_report(records, report_id="V3-DEV-REPORT-TEST")

    assert len(records) == 64
    assert len(store.load_runs()) == 64
    assert report.planned_run_count == report.recorded_run_count == report.raw_run_count == 64
    assert report.provider_calls == report.model_calls == 0
    assert report.architecture_conclusion == "NO_GO"
    assert all(record.execution_package_digest == package.package_digest for record in records)
    assert report.execution_package_digest == package.package_digest


@pytest.mark.asyncio
async def test_invalid_package_fails_before_adapter_factory_or_provider_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _package().model_copy(update={"plan_digest": "0" * 64})
    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-presence-only")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    monkeypatch.setattr(real_runner, "current_source_revision", lambda *args, **kwargs: "f" * 40)
    monkeypatch.setattr(real_runner, "source_tree_is_clean", lambda *args, **kwargs: True)
    calls: list[str] = []
    plan = build_development_plan()
    cases = {case.scenario_id: case for case in load_matrix()}
    inputs = load_production_case_inputs()
    store = V3DevelopmentStore(
        tmp_path / "var" / "v3" / "development" / package.execution_identity,
        execution_identity=package.execution_identity,
    )
    with pytest.raises(real_runner.V3ExecutionNotAuthorized, match="package binding"):
        V3RealDevelopmentRunner(
            plan=plan,
            manifests=load_manifests(),
            cases=cases,
            case_inputs=inputs,
            execution_identity=package.execution_identity,
            evaluation_revision=package.evaluated_source_revision,
            store=store,
            adapter_factory=lambda: calls.append("factory") or _NoIoAdapter(),
            authorization=execution_authorization_from_package(package),
            execution_package=package,
        )
    assert calls == []


def test_cli_no_longer_returns_the_closed_execution_placeholder(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = main(
        [
            "execute",
            "--authorization-package",
            "var/v3/development/V3-DEV-EXEC-20260828-01/authorization-package.json",
        ]
    )
    output = capsys.readouterr().out
    assert result == 2
    assert "NOT_OPENED_IN_EVAL_ACTIVATION" not in output
    assert '"provider_calls": 0' in output
    assert '"model_calls": 0' in output

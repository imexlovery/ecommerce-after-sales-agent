from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langchain_deepseek import ChatDeepSeek

import after_sales_agent.evals.v3.diagnostics as diagnostics
import after_sales_agent.evals.v3.execution_package as execution_package
from after_sales_agent.agents.models import AgentObservationSelector, build_investigation_model
from after_sales_agent.agents.tool_bindings import READ_TOOLS
from after_sales_agent.domain.state import IssueType
from after_sales_agent.evals.v3.cli import main
from after_sales_agent.evals.v3.contracts import V3_EVALUATED_AT
from after_sales_agent.evals.v3.diagnostics import DIAGNOSTIC_IDENTITY
from after_sales_agent.evals.v3.matrix import CASES_BY_ID, FIXTURE_REVISION, load_manifests
from after_sales_agent.evals.v3.real_runner import (
    ACTIVATION_SMOKE_STATUS,
    ProductionInvestigationAdapter,
    V3ExecutionAuthorization,
    V3ExecutionNotAuthorized,
    V3ProductionCaseInput,
    build_development_plan,
    run_activation_smoke,
    run_preflight,
    validate_execution_authorization,
)
from after_sales_agent.evals.v3.report import V3ReportError, validate_paired_records
from after_sales_agent.evals.v3.runner import V3PairedRunner
from after_sales_agent.evals.v3.store import V3DevelopmentStore, V3StoreError


def test_diagnostic_identity_is_fixed_and_cli_rejects_other_identities(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "diagnose",
                "--diagnostic-identity",
                "V3-DEV-DIAG-20260828-01",
            ]
        )

    assert exc_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_diagnostic_identity_isolated_from_historical_ledger_and_append_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_ledger = (
        tmp_path
        / "var"
        / "v3"
        / "development-diagnostics"
        / "V3-DEV-DIAG-20260828-01"
        / "diagnostics.jsonl"
    )
    old_ledger.parent.mkdir(parents=True)
    old_contents = '{"historical":true}\n'
    old_ledger.write_text(old_contents, encoding="utf-8")
    monkeypatch.setenv("LLM_MODE", "mock")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    report = diagnostics.run_live_selector_diagnostics(tmp_path)
    new_ledger = (
        tmp_path
        / "var"
        / "v3"
        / "development-diagnostics"
        / DIAGNOSTIC_IDENTITY
        / "diagnostics.jsonl"
    )

    assert report.diagnostic_identity == DIAGNOSTIC_IDENTITY
    assert report.provider_calls == 0
    assert old_ledger.read_text(encoding="utf-8") == old_contents
    assert new_ledger.exists()
    assert len(new_ledger.read_text(encoding="utf-8").splitlines()) == 1
    assert diagnostics._diagnostic_path(tmp_path).parent.name == DIAGNOSTIC_IDENTITY
    with pytest.raises(diagnostics.DiagnosticAuthorizationError):
        diagnostics.run_live_selector_diagnostics(
            tmp_path,
            diagnostic_identity="V3-DEV-DIAG-20260828-01",
        )


@pytest.mark.asyncio
async def test_diagnostic_call_ceiling_rejects_before_provider_io(tmp_path: Path) -> None:
    class CountingProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def ainvoke(self, _: object) -> AIMessage:
            self.calls += 1
            return AIMessage(content="")

    ledger = diagnostics._DiagnosticLedger(
        tmp_path
        / "var"
        / "v3"
        / "development-diagnostics"
        / DIAGNOSTIC_IDENTITY
        / "diagnostics.jsonl"
    )
    observer = diagnostics._DiagnosticInvocationObserver(ledger)
    provider = CountingProvider()
    for _ in range(diagnostics.DIAGNOSTIC_MAX_CALLS):
        await observer.invoke(
            model=provider,
            messages=(),
            context=SimpleNamespace(),
        )

    with pytest.raises(RuntimeError, match="diagnostic call ceiling reached"):
        await observer.invoke(model=provider, messages=(), context=SimpleNamespace())

    assert provider.calls == diagnostics.DIAGNOSTIC_MAX_CALLS
    assert (
        sum(event.event_type == "admission" for event in ledger.events)
        == diagnostics.DIAGNOSTIC_MAX_CALLS
    )
    assert len(ledger.path.read_text(encoding="utf-8").splitlines()) == (
        2 * diagnostics.DIAGNOSTIC_MAX_CALLS
    )


@pytest.mark.asyncio
async def test_fixed_diagnostic_inputs_use_production_selector_validator_and_router(
    tmp_path: Path,
) -> None:
    class FixedProvider:
        def __init__(self) -> None:
            self.calls = 0
            self.specs = diagnostics._diagnostic_specs()

        async def ainvoke(self, _: object) -> dict[str, object]:
            spec = self.specs[self.calls]
            self.calls += 1
            payload = {
                "action": spec.expected_action,
                "tool_name": spec.expected_tool_name,
                "addresses": (
                    [spec.expected_evidence_requirement]
                    if spec.expected_evidence_requirement is not None
                    else []
                ),
                "reason_code": (
                    "FINALIZATION_REQUESTED"
                    if spec.expected_action == "finish"
                    else "MISSING_REQUIRED_EVIDENCE"
                ),
            }
            candidate = diagnostics.NextObservationCandidate.model_validate(payload)
            return {
                "raw": AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "NextObservationCandidate",
                            "args": payload,
                            "id": f"diag-candidate-{self.calls}",
                            "type": "tool_call",
                        }
                    ],
                ),
                "parsed": candidate,
                "parsing_error": None,
            }

    ledger = diagnostics._DiagnosticLedger(
        tmp_path
        / "var"
        / "v3"
        / "development-diagnostics"
        / DIAGNOSTIC_IDENTITY
        / "diagnostics.jsonl"
    )
    provider = FixedProvider()
    source_revision = "f" * 40
    reasons = [
        await diagnostics._run_diagnostic_input(
            model=provider,
            ledger=ledger,
            spec=spec,
            source_revision=source_revision,
        )
        for spec in diagnostics._diagnostic_specs()
    ]

    assert reasons == ["DIAGNOSTIC_BOUNDARY_PASSED"] * 12
    assert provider.calls == 12
    assert [
        event.router_route
        for event in ledger.events
        if event.event_type == "boundary"
    ] == [spec.expected_route.value for spec in diagnostics._diagnostic_specs()]
    assert all(
        event.selector_boundary_pass is True
        for event in ledger.events
        if event.event_type == "boundary"
    )
    assert sum(
        event.server_tool_call_count == 1
        for event in ledger.events
        if event.event_type == "boundary"
    ) == 10


def test_invalid_diagnostic_configuration_makes_zero_provider_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_source = (
        Path(__file__).resolve().parents[2]
        / "evals"
        / "v3"
        / "diagnostic-manifests"
        / "V3-DEV-DIAG-20260828-04.json"
    )
    manifest_target = (
        tmp_path
        / "evals"
        / "v3"
        / "diagnostic-manifests"
        / "V3-DEV-DIAG-20260828-04.json"
    )
    manifest_target.parent.mkdir(parents=True)
    shutil.copyfile(manifest_source, manifest_target)
    calls: list[str] = []
    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(
        diagnostics,
        "build_investigation_model",
        lambda *_args, **_kwargs: calls.append("model") or object(),
    )

    report = diagnostics.run_live_selector_diagnostics(tmp_path)

    assert report.status == "blocked"
    assert report.reason_code == "DIAGNOSTIC_CONFIGURATION_INVALID"
    assert report.provider_calls == 0
    assert calls == []


def test_activation_plan_is_mechanical_and_preflight_is_provider_free() -> None:
    plan = build_development_plan()

    assert plan.matrix_case_count == 32
    assert plan.paired_run_count == 64
    assert plan.planned_run_count == 64
    assert plan.architecture_run_counts == {"agent": 32, "workflow": 32}
    assert plan.repeat == 1
    assert plan.timeout_seconds == 30.0
    assert plan.selector_turn_ceiling_per_case == 16
    assert plan.selector_turn_ceiling_per_run == 8
    assert plan.provider_call_ceiling_by_architecture == {"agent": 256, "workflow": 0}
    assert plan.maximum_provider_calls == 256
    assert (
        "Agent: 32 runs x 8 selector turns/run x 1 provider call/selector turn = 256"
        in plan.provider_call_ceiling_formula
    )
    assert plan.token_ceiling is None
    assert plan.token_ceiling_status == "requires_explicit_configuration"
    assert plan.formal_measurement_authorized is False

    preflight = run_preflight()
    assert preflight.status == "NO_GO_FORMAL_DEVELOPMENT_NOT_AUTHORIZED"
    assert preflight.formal_execution_ready is False
    assert preflight.provider_calls == 0
    assert preflight.model_calls == 0


def test_formal_agent_default_path_uses_project_dotenv_without_provider_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".env").write_text(
        "DEEPSEEK_API_KEY=presence-only-test-value\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.delenv("LLM_MODE", raising=False)

    def fail_if_called(*_: object, **__: object) -> None:
        raise AssertionError("formal readiness must not invoke the provider")

    monkeypatch.setattr(ChatDeepSeek, "ainvoke", fail_if_called)
    case = CASES_BY_ID["v3a-snr-order-not-delivered"]
    case_input = V3ProductionCaseInput(
        scenario_id=case.scenario_id,
        customer_id="customer_a",
        order_id="ORD-001",
        issue_type=IssueType.SIGNED_NOT_RECEIVED,
        customer_message="我的 ORD-001 已发货但没有收到。",
        fixture_revision=FIXTURE_REVISION,
        source_revision=case.source_revision,
        fault_seed="formal-readiness",
        evaluated_at=datetime.fromisoformat(V3_EVALUATED_AT),
    )
    adapter = ProductionInvestigationAdapter(
        project_root=tmp_path,
        root_factory=lambda architecture, _: tmp_path / architecture,
    )

    live_settings = adapter.build_settings(
        architecture="agent",
        case_input=case_input,
        root=tmp_path / "agent",
        timeout_seconds=30.0,
    )
    live_model = build_investigation_model(live_settings, READ_TOOLS)
    selector = AgentObservationSelector(live_model)

    assert live_settings.llm_mode.value == "live"
    assert live_settings.deepseek_model == "deepseek-v4-flash"
    assert bool(live_settings.deepseek_api_key) is True
    assert selector.model is live_model
    assert execution_package._validate_environment_for_package(tmp_path) is True

    workflow_settings = adapter.build_settings(
        architecture="workflow",
        case_input=case_input,
        root=tmp_path / "workflow",
        timeout_seconds=30.0,
    )
    assert workflow_settings.llm_mode.value == "mock"


def test_missing_project_credential_blocks_identity_creation_before_package_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.delenv("LLM_MODE", raising=False)

    with pytest.raises(execution_package.ExecutionPackageError, match="DEEPSEEK_API_KEY"):
        execution_package.create_formal_execution_package(
            tmp_path,
            execution_identity=execution_package.FORMAL_DEVELOPMENT_EXECUTION_IDENTITY,
        )
    assert not (
        tmp_path
        / "var"
        / "v3"
        / "development"
        / execution_package.FORMAL_DEVELOPMENT_EXECUTION_IDENTITY
    ).exists()


def test_observed_agent_provider_calls_do_not_make_a_pair_unfair() -> None:
    case = CASES_BY_ID["v3a-snr-order-not-delivered"]
    agent, workflow = V3PairedRunner().run_case_pair(case)
    agent = agent.model_copy(
        update={
            "metrics": agent.metrics.model_copy(update={"model_calls": 1, "provider_calls": 1}),
        }
    )

    validate_paired_records((agent, workflow))


def test_asymmetric_authorized_budget_is_rejected_even_when_observations_are_valid() -> None:
    case = CASES_BY_ID["v3a-snr-order-not-delivered"]
    agent, workflow = V3PairedRunner().run_case_pair(case)
    altered_agent = agent.model_copy(update={"authorized_provider_call_ceiling": 7})

    with pytest.raises(V3ReportError, match="authorized provider-call ceiling differs"):
        validate_paired_records((altered_agent, workflow))


def test_development_store_is_v3_only_and_replay_idempotent(tmp_path: Path) -> None:
    identity = "V3-DEV-EXEC-ACTIVATION-TEST"
    store_root = tmp_path / "var" / "v3" / "development" / identity
    store = V3DevelopmentStore(store_root, execution_identity=identity)
    record, _ = V3PairedRunner().run_case_pair(CASES_BY_ID["v3a-snr-order-not-delivered"])
    record = record.model_copy(update={"execution_identity": identity})

    first_path = store.save_run(record)
    replay_path = store.save_run(record)

    assert replay_path == first_path
    assert len(store.load_runs()) == 1
    with pytest.raises(V3StoreError, match="duplicate V3 logical run key"):
        store.save_run(record.model_copy(update={"eval_run_id": f"{record.eval_run_id}-conflict"}))
    with pytest.raises(V3StoreError, match="invalid format"):
        V3DevelopmentStore(
            tmp_path / "var" / "v3" / "development" / "bad",
            execution_identity="bad",
        )


def test_formal_authorization_fails_closed_before_reserved_manifests_can_run() -> None:
    plan = build_development_plan()
    authorization = V3ExecutionAuthorization(
        execution_identity="V3-DEV-EXEC-ACTIVATION-TEST",
        authorization_flag=False,
        live_mode=False,
        credential_present=False,
        clean_source=False,
        current_source_revision=plan.manifest_source_revision,
        source_revision=plan.manifest_source_revision,
        manifest_version_binding=False,
        manifest_digests=plan.manifest_digests,
        token_ceiling=1,
    )

    with pytest.raises(V3ExecutionNotAuthorized, match="explicit authorization flag is false"):
        validate_execution_authorization(
            authorization,
            plan=plan,
            manifests=load_manifests(),
        )


@pytest.mark.asyncio
async def test_activation_smoke_uses_both_production_selectors_without_measurement(
    tmp_path: Path,
) -> None:
    report = await run_activation_smoke(tmp_path / "activation-smoke")

    assert report.measurement_status == ACTIVATION_SMOKE_STATUS
    assert report.provider_calls == 0
    assert report.model_calls == 0
    assert report.shared_input_digest
    assert {item.architecture for item in report.runs} == {"agent", "workflow"}
    for run in report.runs:
        assert run.selector_kinds == (run.architecture,)
        assert run.case_fact_snapshot_present is True
        assert run.persisted_tool_call_count > 0
        assert run.grader_failure_count == 0
        assert "evidence_progress_rebuilt" in run.persisted_trace_event_types


@pytest.mark.asyncio
async def test_production_investigation_adapter_reads_persisted_runtime_trace(
    tmp_path: Path,
) -> None:
    case = CASES_BY_ID["v3a-snr-pod-absent-proof"]
    case_input = V3ProductionCaseInput(
        scenario_id=case.scenario_id,
        customer_id="customer_a",
        order_id="ORD-001",
        issue_type=IssueType.SIGNED_NOT_RECEIVED,
        customer_message="我的 ORD-001 显示签收了，但我没有收到。",
        fixture_revision=FIXTURE_REVISION,
        source_revision=case.source_revision,
        fault_seed="activation-adapter",
        evaluated_at=datetime.fromisoformat(V3_EVALUATED_AT),
    )
    adapter = ProductionInvestigationAdapter(
        root_factory=lambda architecture, _: tmp_path / architecture,
    )

    evidence = await adapter.execute(
        case=case,
        case_input=case_input,
        architecture="workflow",
        repetition=1,
        timeout_seconds=30.0,
    )

    assert evidence.model_calls == 0
    assert evidence.provider_calls == 0
    assert evidence.trace.tool_calls
    assert evidence.trace.fact_snapshots
    assert evidence.trace.progress_rebuilds
    assert evidence.safety_gate_pass is True


def test_production_case_input_requires_explicit_issue_and_order_scope() -> None:
    case = CASES_BY_ID["v3a-snr-order-not-delivered"]

    with pytest.raises(ValueError, match="explicit order scope"):
        V3ProductionCaseInput(
            scenario_id=case.scenario_id,
            customer_id="customer_a",
            order_id="ORD-001",
            issue_type=case.issue,
            customer_message="我没有收到包裹。",
            fixture_revision=case.fixture_revision,
            source_revision=case.source_revision,
            fault_seed="v3a-snr-order-not-delivered",
            evaluated_at=case.evaluated_at,
        )

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from after_sales_agent.domain.state import IssueType
from after_sales_agent.evals.v3.contracts import V3_EVALUATED_AT
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

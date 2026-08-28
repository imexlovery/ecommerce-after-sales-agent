from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from langchain_core.messages import AIMessage

import after_sales_agent.evals.v3.real_runner as real_runner
from after_sales_agent.agents.models import AgentObservationSelector, MockInvestigationModel
from after_sales_agent.agents.tool_bindings import READ_TOOLS
from after_sales_agent.application.provider_budget import SelectorSchemaFailure
from after_sales_agent.evals.v3.budget import (
    TOKEN_THRESHOLD_SEMANTICS,
    DevelopmentBudgetBinding,
    DevelopmentBudgetLedger,
)
from after_sales_agent.evals.v3.contracts import V3TypedTrace
from after_sales_agent.evals.v3.matrix import CASES_BY_ID, FIXTURE_REVISION, load_manifests
from after_sales_agent.evals.v3.real_runner import (
    DevelopmentProviderInvocationObserver,
    ProductionTraceEvidence,
    V3ExecutionAuthorization,
    V3RealDevelopmentRunner,
    _development_budget_binding,
    build_development_plan,
)
from after_sales_agent.evals.v3.store import V3DevelopmentStore


class FakeProvider:
    def __init__(self, behaviors: list[str] | None = None, *, total_tokens: int = 3) -> None:
        self.model_name = "same-model-name-for-every-call"
        self.calls = 0
        self._behaviors = list(behaviors or [])
        self._total_tokens = total_tokens

    async def ainvoke(self, _: Any) -> AIMessage:
        self.calls += 1
        behavior = self._behaviors.pop(0) if self._behaviors else "success"
        if behavior == "error":
            raise RuntimeError("fake-provider-error")
        if behavior == "timeout":
            raise TimeoutError("fake-provider-timeout")
        if behavior == "wait":
            await asyncio.Event().wait()
        return AIMessage(
            content="",
            usage_metadata={
                "input_tokens": 1,
                "output_tokens": self._total_tokens - 1,
                "total_tokens": self._total_tokens,
            },
        )


class DelegatingMockSelector:
    """Model-shaped local fixture that stays outside the Mock selector shortcut."""

    def __init__(self) -> None:
        self._delegate = MockInvestigationModel(READ_TOOLS)
        self.calls = 0

    async def ainvoke(self, messages: Any) -> AIMessage:
        self.calls += 1
        response = await self._delegate.ainvoke(messages)
        return AIMessage(
            content=response.content,
            tool_calls=response.tool_calls,
            usage_metadata={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
        )


def _binding(
    tmp_path: Any,
    *,
    ceiling: int = 256,
    threshold: int | None = 100_000,
) -> DevelopmentBudgetBinding:
    return DevelopmentBudgetBinding(
        execution_identity="V3-DEV-EXEC-FAKE-BUDGET",
        source_revision="1" * 40,
        manifest_digests={"FAKE-MANIFEST": "a" * 64},
        plan_version="v3.fake-budget-plan.v1",
        authorized_provider_call_ceiling=ceiling,
        authorized_provider_call_ceiling_per_run=16,
        token_threshold=threshold,
        token_threshold_semantics=TOKEN_THRESHOLD_SEMANTICS,
        output_token_cap_per_invocation=512,
    )


def _context(turn: int) -> Any:
    return SimpleNamespace(
        remaining_budget=SimpleNamespace(run_planning_turns=turn),
    )


async def _invoke(
    ledger: DevelopmentBudgetLedger,
    provider: FakeProvider,
    logical_run_key: str,
    turn: int,
    *,
    timeout_scope: bool = True,
) -> AIMessage:
    observer = DevelopmentProviderInvocationObserver(
        ledger=ledger,
        logical_run_key=logical_run_key,
        timeout_scope=timeout_scope,
    )
    return cast(
        AIMessage,
        await observer.invoke(model=provider, messages=(), context=_context(turn)),
    )


def test_binding_rejects_bad_paths_without_network(tmp_path: Any) -> None:
    binding = _binding(tmp_path)
    ledger = DevelopmentBudgetLedger(tmp_path / "budget.jsonl", binding=binding)
    assert ledger.snapshot().attempted_provider_calls == 0


@pytest.mark.asyncio
async def test_same_model_name_is_counted_per_invocation_with_fake_provider(tmp_path: Any) -> None:
    provider = FakeProvider()
    ledger = DevelopmentBudgetLedger(tmp_path / "eight.jsonl", binding=_binding(tmp_path))
    for turn in range(1, 9):
        await _invoke(ledger, provider, "logical-run-1", turn)
    snapshot = ledger.snapshot()
    assert provider.calls == 8
    assert snapshot.attempted_provider_calls == 8
    assert snapshot.completed_provider_calls == 8
    assert snapshot.provider_reported_total_tokens == 24
    assert snapshot.invocations[-1].logical_run_key == "logical-run-1"


@pytest.mark.asyncio
async def test_production_adapter_uses_ledger_count_not_model_name_metadata(
    tmp_path: Any,
) -> None:
    case = CASES_BY_ID["v3a-snr-pod-absent-proof"]
    case_input = real_runner.V3ProductionCaseInput(
        scenario_id=case.scenario_id,
        customer_id="customer_a",
        order_id="ORD-001",
        issue_type=case.issue,
        customer_message="我的 ORD-001 显示签收了，但我没有收到。",
        fixture_revision=case.fixture_revision,
        source_revision=case.source_revision,
        fault_seed="adapter-budget-guard",
        evaluated_at=case.evaluated_at,
    )
    provider = DelegatingMockSelector()
    ledger = DevelopmentBudgetLedger(
        tmp_path / "adapter.jsonl", binding=_binding(tmp_path, threshold=100_000)
    )
    adapter = real_runner.ProductionInvestigationAdapter(
        root_factory=lambda architecture, _: tmp_path / architecture,
        model_factory=lambda architecture, _, __: provider,
    )
    evidence = await adapter.execute(
        case=case,
        case_input=case_input,
        architecture="agent",
        repetition=1,
        timeout_seconds=30.0,
        logical_run_key="adapter-agent-r1",
        budget_ledger=ledger,
    )
    snapshot = ledger.snapshot()
    assert provider.calls == snapshot.attempted_provider_calls == evidence.provider_calls
    assert provider.calls == evidence.model_calls
    assert provider.calls > 1
    assert snapshot.provider_reported_total_tokens == provider.calls * 5


@pytest.mark.asyncio
async def test_provider_error_timeout_and_cancel_are_retained_as_attempts(tmp_path: Any) -> None:
    from after_sales_agent.application.provider_budget import ProviderInvocationFailure

    failure_ledger = DevelopmentBudgetLedger(
        tmp_path / "failure.jsonl", binding=_binding(tmp_path, threshold=100_000)
    )
    with pytest.raises(ProviderInvocationFailure):
        await _invoke(failure_ledger, FakeProvider(["error"]), "failure", 1)
    assert failure_ledger.snapshot().provider_errors == 1
    assert failure_ledger.snapshot().attempted_provider_calls == 1

    raised_timeout_ledger = DevelopmentBudgetLedger(
        tmp_path / "raised-timeout.jsonl", binding=_binding(tmp_path, threshold=100_000)
    )
    with pytest.raises(TimeoutError):
        await _invoke(raised_timeout_ledger, FakeProvider(["timeout"]), "raised-timeout", 1)
    assert raised_timeout_ledger.snapshot().provider_timeouts == 1
    assert raised_timeout_ledger.snapshot().attempted_provider_calls == 1

    timeout_ledger = DevelopmentBudgetLedger(
        tmp_path / "timeout.jsonl", binding=_binding(tmp_path, threshold=100_000)
    )
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            _invoke(timeout_ledger, FakeProvider(["wait"]), "timeout", 1),
            timeout=0.01,
        )
    assert timeout_ledger.snapshot().provider_timeouts == 1
    assert timeout_ledger.snapshot().attempted_provider_calls == 1

    cancel_ledger = DevelopmentBudgetLedger(
        tmp_path / "cancel.jsonl", binding=_binding(tmp_path, threshold=100_000)
    )
    task = asyncio.create_task(
        _invoke(
            cancel_ledger,
            FakeProvider(["wait"]),
            "cancel",
            1,
            timeout_scope=False,
        )
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancel_ledger.snapshot().provider_cancellations == 1
    assert cancel_ledger.snapshot().attempted_provider_calls == 1


@pytest.mark.asyncio
async def test_schema_failure_is_a_completed_selector_attempt(tmp_path: Any) -> None:
    class SchemaFailureProvider(FakeProvider):
        async def ainvoke(self, _: Any) -> AIMessage:
            self.calls += 1
            return AIMessage(
                content="",
                tool_calls=[
                    {"name": "get_order_context", "args": {}, "id": "one"},
                    {"name": "get_logistics_timeline", "args": {}, "id": "two"},
                ],
                usage_metadata={"input_tokens": 2, "output_tokens": 2, "total_tokens": 4},
            )

    ledger = DevelopmentBudgetLedger(
        tmp_path / "schema.jsonl", binding=_binding(tmp_path, threshold=10_000)
    )
    provider = SchemaFailureProvider()
    selector = AgentObservationSelector(
        provider,
        invocation_observer=DevelopmentProviderInvocationObserver(
            ledger=ledger,
            logical_run_key="schema-run",
        ),
    )
    context = SimpleNamespace(
        authorized_order_id="ORD-001",
        canonical_issue_type=SimpleNamespace(value="signed_not_received"),
        evidence_progress=SimpleNamespace(model_dump=lambda **_: {}),
        remaining_budget=SimpleNamespace(run_planning_turns=1),
    )
    with pytest.raises(SelectorSchemaFailure):
        await selector.select_next_observation(context)
    snapshot = ledger.snapshot()
    assert provider.calls == 1
    assert snapshot.attempted_provider_calls == 1
    assert snapshot.completed_provider_calls == 1
    assert snapshot.provider_errors == 0


@pytest.mark.asyncio
async def test_257th_provider_call_is_rejected_before_fake_provider_io(tmp_path: Any) -> None:
    from after_sales_agent.application.provider_budget import ProviderBudgetAdmissionRejected

    provider = FakeProvider()
    ledger = DevelopmentBudgetLedger(
        tmp_path / "ceiling.jsonl", binding=_binding(tmp_path, ceiling=256, threshold=1_000_000)
    )
    for index in range(256):
        logical_run_key = f"run-{index // 16}"
        await _invoke(ledger, provider, logical_run_key, index % 16 + 1)
    with pytest.raises(ProviderBudgetAdmissionRejected, match="provider_budget_exhausted"):
        await _invoke(ledger, provider, "run-overflow", 1)
    assert provider.calls == 256
    assert ledger.snapshot().attempted_provider_calls == 256
    assert ledger.snapshot().remaining_provider_calls == 0


@pytest.mark.asyncio
async def test_per_run_ceiling_does_not_stop_other_logical_runs(tmp_path: Any) -> None:
    from after_sales_agent.application.provider_budget import ProviderBudgetAdmissionRejected

    provider = FakeProvider()
    ledger = DevelopmentBudgetLedger(
        tmp_path / "per-run.jsonl",
        binding=_binding(tmp_path, ceiling=256, threshold=1_000_000).model_copy(
            update={"authorized_provider_call_ceiling_per_run": 1}
        ),
    )
    await _invoke(ledger, provider, "per-run-a", 1)
    with pytest.raises(ProviderBudgetAdmissionRejected, match="provider_budget_exhausted"):
        await _invoke(ledger, provider, "per-run-a", 2)
    await _invoke(ledger, provider, "per-run-b", 1)
    assert provider.calls == 2
    assert ledger.snapshot().attempted_provider_calls == 2
    assert ledger.snapshot().remaining_provider_calls == 254
    assert ledger.snapshot().stop_reason is None


@pytest.mark.asyncio
async def test_token_threshold_stops_after_exact_hit_and_one_overshoot(tmp_path: Any) -> None:
    from after_sales_agent.application.provider_budget import ProviderBudgetAdmissionRejected

    exact_provider = FakeProvider(total_tokens=100)
    exact_ledger = DevelopmentBudgetLedger(
        tmp_path / "token-exact.jsonl", binding=_binding(tmp_path, threshold=100)
    )
    await _invoke(exact_ledger, exact_provider, "exact", 1)
    with pytest.raises(ProviderBudgetAdmissionRejected, match="token_threshold_exhausted"):
        await _invoke(exact_ledger, exact_provider, "exact", 2)
    exact_snapshot = exact_ledger.snapshot()
    assert exact_provider.calls == 1
    assert exact_snapshot.threshold_exhausted is True
    assert exact_snapshot.token_overshoot == 0

    overshoot_provider = FakeProvider(total_tokens=101)
    overshoot_ledger = DevelopmentBudgetLedger(
        tmp_path / "token-overshoot.jsonl", binding=_binding(tmp_path, threshold=100)
    )
    await _invoke(overshoot_ledger, overshoot_provider, "overshoot", 1)
    with pytest.raises(ProviderBudgetAdmissionRejected, match="token_threshold_exhausted"):
        await _invoke(overshoot_ledger, overshoot_provider, "overshoot", 2)
    overshoot_snapshot = overshoot_ledger.snapshot()
    assert overshoot_provider.calls == 1
    assert overshoot_snapshot.token_overshoot == 1


@pytest.mark.asyncio
async def test_missing_provider_usage_stops_future_admission_fail_closed(tmp_path: Any) -> None:
    from after_sales_agent.application.provider_budget import ProviderBudgetAdmissionRejected

    class NoUsageProvider(FakeProvider):
        async def ainvoke(self, _: Any) -> AIMessage:
            self.calls += 1
            return AIMessage(content="")

    provider = NoUsageProvider()
    ledger = DevelopmentBudgetLedger(
        tmp_path / "missing-usage.jsonl", binding=_binding(tmp_path, threshold=100)
    )
    await _invoke(ledger, provider, "missing-usage", 1)
    with pytest.raises(ProviderBudgetAdmissionRejected, match="token_usage_unavailable"):
        await _invoke(ledger, provider, "missing-usage", 2)
    snapshot = ledger.snapshot()
    assert provider.calls == 1
    assert snapshot.token_usage_complete is False
    assert snapshot.stop_reason == "token_usage_unavailable"


@pytest.mark.asyncio
async def test_restart_replay_does_not_reconsume_a_logical_invocation(tmp_path: Any) -> None:
    from after_sales_agent.application.provider_budget import ProviderBudgetAdmissionRejected

    path = tmp_path / "restart.jsonl"
    binding = _binding(tmp_path, threshold=10_000)
    first_ledger = DevelopmentBudgetLedger(path, binding=binding)
    provider = FakeProvider()
    await _invoke(first_ledger, provider, "replayed-run", 1)

    restarted_ledger = DevelopmentBudgetLedger(path, binding=binding)
    replay_provider = FakeProvider(["error"])
    with pytest.raises(
        ProviderBudgetAdmissionRejected, match="replay_invocation_already_consumed"
    ):
        await _invoke(restarted_ledger, replay_provider, "replayed-run", 1)
    assert replay_provider.calls == 0
    assert restarted_ledger.snapshot().attempted_provider_calls == 1
    assert restarted_ledger.snapshot().stop_reason is None


class WorkflowOnlyAdapter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(
        self,
        *,
        case: Any,
        case_input: Any,
        architecture: str,
        repetition: int,
        timeout_seconds: float,
        logical_run_key: str | None = None,
        budget_ledger: DevelopmentBudgetLedger | None = None,
    ) -> ProductionTraceEvidence:
        del case_input, repetition, timeout_seconds, budget_ledger
        assert architecture == "workflow"
        assert logical_run_key is not None
        self.calls.append(logical_run_key)
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


def _case_inputs() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for case in CASES_BY_ID.values():
        result[case.scenario_id] = real_runner.V3ProductionCaseInput(
            scenario_id=case.scenario_id,
            customer_id="customer_a",
            order_id="ORD-001",
            issue_type=case.issue,
            customer_message="ORD-001 的物流情况需要核查。",
            fixture_revision=FIXTURE_REVISION,
            source_revision=case.source_revision,
            fault_seed=case.scenario_id,
            evaluated_at=case.evaluated_at,
        )
    return result


@pytest.mark.asyncio
async def test_budget_exhaustion_retains_64_64_64_and_workflow_stays_zero_provider(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = "V3-DEV-EXEC-FAKE-COMPLETENESS"
    plan = build_development_plan(token_ceiling=100_000)
    manifests = load_manifests()
    store = V3DevelopmentStore(
        tmp_path / "var" / "v3" / "development" / identity,
        execution_identity=identity,
    )
    authorization = V3ExecutionAuthorization(
        execution_identity=identity,
        authorization_flag=True,
        live_mode=True,
        credential_present=True,
        clean_source=True,
        current_source_revision=plan.manifest_source_revision,
        source_revision=plan.manifest_source_revision,
        manifest_version_binding=True,
        manifest_digests=plan.manifest_digests,
        token_ceiling=100_000,
        provider_call_ceiling=plan.authorized_provider_call_ceiling,
        provider_call_ceiling_per_run=plan.authorized_provider_call_ceiling_per_run,
        token_threshold_semantics_accepted=True,
    )
    monkeypatch.setattr(
        real_runner, "validate_execution_authorization", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        real_runner,
        "current_source_revision",
        lambda *args, **kwargs: plan.manifest_source_revision,
    )
    monkeypatch.setattr(real_runner, "source_tree_is_clean", lambda *args, **kwargs: True)

    binding = _development_budget_binding(
        plan=plan,
        authorization=authorization,
        execution_identity=identity,
    )
    ledger = store.open_budget_ledger(binding=binding)
    for case in CASES_BY_ID.values():
        logical_run_key = f"{case.pair_id}-agent-r1"
        for turn in range(1, 9):
            admission = ledger.admit_provider_call(
                logical_run_key=logical_run_key,
                selector_turn=turn,
            )
            assert admission.granted is True
            assert admission.invocation_id is not None
            ledger.complete_provider_call(
                invocation_id=admission.invocation_id,
                logical_run_key=logical_run_key,
                status="completed",
                usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            )

    adapter = WorkflowOnlyAdapter()
    runner = V3RealDevelopmentRunner(
        plan=plan,
        manifests=manifests,
        cases=CASES_BY_ID,
        case_inputs=_case_inputs(),
        execution_identity=identity,
        evaluation_revision="v3-budget-guard-test",
        store=store,
        adapter_factory=lambda: adapter,
        authorization=authorization,
    )
    records = await runner.run()
    report = runner.build_report(records, report_id="V3-BUDGET-GUARD-TEST")

    assert len(records) == 64
    assert len(store.load_runs()) == 64
    assert len(adapter.calls) == 32
    assert sum(record.architecture == "agent" for record in records) == 32
    assert sum(record.architecture == "workflow" for record in records) == 32
    assert sum(record.run_status == "provider_budget_exhausted" for record in records) == 32
    assert all(
        record.metrics.provider_calls == 0
        for record in records
        if record.architecture == "workflow"
    )
    assert report.planned_run_count == report.recorded_run_count == report.raw_run_count == 64
    assert report.provider_calls == report.attempted_provider_calls == 256
    assert report.model_calls == 256
    assert report.remaining_provider_calls == 0
    assert report.provider_hard_ceiling is True
    assert report.token_threshold_semantics == TOKEN_THRESHOLD_SEMANTICS

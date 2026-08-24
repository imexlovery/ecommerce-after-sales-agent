from __future__ import annotations

import pytest

from after_sales_agent.config import Settings
from after_sales_agent.evals.graders import build_grader_registry
from after_sales_agent.evals.runner import EvaluationRunner
from after_sales_agent.evals.scenarios import load_scenarios


@pytest.mark.asyncio
async def test_mock_runner_executes_all_three_layers_and_both_architectures() -> None:
    settings = Settings(
        _env_file=None,
        LLM_MODE="mock",
        POLICY_RETRIEVAL_MODE="fake_test",
    )
    runner = EvaluationRunner(
        base_settings=settings,
        evaluation_revision="runner-test-r1",
        timeout_seconds=30,
    )
    scenarios = {scenario.scenario_id: scenario for scenario in load_scenarios()}
    triage = await runner.run(
        scenarios["triage-dev-01"],
        layer="triage",
        architecture="triage",
        repetition=1,
        mode="mock",
    )
    assert triage.quality_pass is True
    assert triage.safety_gate_pass is True

    for layer in ("investigation", "full_e2e"):
        for architecture in ("agent", "workflow"):
            record = await runner.run(
                scenarios["investigation-dev-timeout-once"],
                layer=layer,
                architecture=architecture,
                repetition=1,
                mode="mock",
            )
            assert record.quality_pass is True
            assert record.safety_gate_pass is True
            assert record.error_code is None


@pytest.mark.asyncio
async def test_manifest_assertions_are_emitted_once_for_each_registered_layer() -> None:
    settings = Settings(
        _env_file=None,
        LLM_MODE="mock",
        POLICY_RETRIEVAL_MODE="fake_test",
    )
    runner = EvaluationRunner(
        base_settings=settings,
        evaluation_revision="runner-manifest-contract-r1",
        timeout_seconds=30,
    )
    scenario = next(
        item
        for item in load_scenarios()
        if item.scenario_id == "investigation-locked-01-signed-confirm"
    )
    registry = build_grader_registry()

    for layer in ("investigation", "full_e2e"):
        record = await runner.run(
            scenario,
            layer=layer,
            architecture="agent",
            repetition=1,
            mode="mock",
        )
        expected = {
            item.assertion_id
            for item in scenario.declared_assertions()
            if layer in registry[item.assertion_id].applicable_layers
        }
        result_ids = [item.assertion_id for item in record.assertions]

        assert set(record.manifest_assertion_ids) == expected
        assert all(result_ids.count(assertion_id) == 1 for assertion_id in expected)
        assert (
            next(
                item
                for item in record.assertions
                if item.assertion_id == "evaluation_contract_integrity"
            ).passed
            is True
        )

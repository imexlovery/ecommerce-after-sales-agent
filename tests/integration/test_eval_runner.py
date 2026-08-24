from __future__ import annotations

import pytest

from after_sales_agent.config import Settings
from after_sales_agent.evals.runner import EvaluationRunner
from after_sales_agent.evals.scenarios import load_scenarios


@pytest.mark.asyncio
async def test_mock_runner_executes_all_three_layers_and_both_architectures() -> None:
    settings = Settings(_env_file=None, LLM_MODE="mock")
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

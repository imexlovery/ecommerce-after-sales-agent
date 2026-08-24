from __future__ import annotations

import pytest

from after_sales_agent.application.pacing import MockDemoPacer
from after_sales_agent.config import Settings


@pytest.mark.asyncio
async def test_mock_demo_pacer_uses_configured_delay() -> None:
    pauses: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        pauses.append(seconds)

    pacer = MockDemoPacer(
        Settings(
            _env_file=None,
            LLM_MODE="mock",
            MOCK_DEMO_STEP_DELAY_MS=300,
        ),
        sleeper=fake_sleep,
    )

    await pacer.pause("tool_call_completed")

    assert pauses == [0.3]


@pytest.mark.asyncio
async def test_mock_demo_pacer_default_does_not_delay_tests() -> None:
    pauses: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        pauses.append(seconds)

    pacer = MockDemoPacer(
        Settings(_env_file=None, LLM_MODE="mock"),
        sleeper=fake_sleep,
    )

    await pacer.pause("triage_started")

    assert pauses == []


@pytest.mark.asyncio
async def test_mock_demo_pacer_never_delays_live_mode() -> None:
    pauses: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        pauses.append(seconds)

    pacer = MockDemoPacer(
        Settings(
            _env_file=None,
            LLM_MODE="live",
            DEEPSEEK_API_KEY="synthetic-test-key",
            MOCK_DEMO_STEP_DELAY_MS=300,
        ),
        sleeper=fake_sleep,
    )

    await pacer.pause("policy_decided")

    assert pauses == []

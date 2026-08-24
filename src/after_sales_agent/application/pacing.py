"""Optional pacing for the explicit local Mock demonstration.

Pacing happens only while new Mock work is executing. Persisted events, SSE
replay, and Live provider calls are never delayed here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from after_sales_agent.config import LLMMode, Settings

Sleeper = Callable[[float], Awaitable[None]]


class MockDemoPacer:
    """Pause after durable Mock milestones so the real trajectory is observable."""

    def __init__(self, settings: Settings, *, sleeper: Sleeper = asyncio.sleep) -> None:
        self._enabled = settings.llm_mode is LLMMode.MOCK and settings.mock_demo_step_delay_ms > 0
        self._delay_seconds = settings.mock_demo_step_delay_ms / 1_000
        self._sleeper = sleeper

    async def pause(self, _milestone: str) -> None:
        if self._enabled:
            await self._sleeper(self._delay_seconds)

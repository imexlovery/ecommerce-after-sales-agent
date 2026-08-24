"""Single-process serialization for mutations of one InvestigationCase."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class CaseMutationCoordinator:
    """Provide one asyncio lock per Case in the supported local process.

    The repository also exposes an optimistic ``revision`` check. Together they
    prevent overlapping API requests from silently overwriting Case state while
    keeping the deliberate single-process scope explicit.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._registry_lock = asyncio.Lock()

    async def _get_lock(self, case_id: str) -> asyncio.Lock:
        async with self._registry_lock:
            return self._locks.setdefault(case_id, asyncio.Lock())

    @asynccontextmanager
    async def serialize(self, case_id: str) -> AsyncIterator[None]:
        if not case_id:
            raise ValueError("case_id must not be empty")
        lock = await self._get_lock(case_id)
        async with lock:
            yield

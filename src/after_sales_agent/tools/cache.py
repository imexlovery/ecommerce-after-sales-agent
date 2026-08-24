"""Revision-aware Case cache for completed evidence and terminal read errors."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from after_sales_agent.domain.state import EvidenceAvailability, ExecutionStatus
from after_sales_agent.tools.contracts import ToolResult


def normalize_tool_arguments(arguments: dict[str, Any]) -> str:
    return json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True, slots=True)
class ToolCacheKey:
    case_id: str
    tool_name: str
    normalized_args: str
    source_revision: str


class CaseToolCache:
    """No retryable error is ever made reusable evidence."""

    def __init__(self) -> None:
        self._reusable: dict[ToolCacheKey, ToolResult[Any]] = {}
        self._terminal_errors: dict[ToolCacheKey, ToolResult[Any]] = {}
        self._actual_attempts: dict[ToolCacheKey, int] = {}
        self._last_result_retryable: set[ToolCacheKey] = set()

    def get(self, key: ToolCacheKey) -> ToolResult[Any] | None:
        return self._reusable.get(key) or self._terminal_errors.get(key)

    def store(self, key: ToolCacheKey, result: ToolResult[Any]) -> None:
        if result.execution_status is ExecutionStatus.SUCCESS:
            if result.evidence_availability not in {
                EvidenceAvailability.PRESENT,
                EvidenceAvailability.ABSENT,
            }:
                raise ValueError("only completed evidence is reusable")
            self._reusable[key] = result
            self._last_result_retryable.discard(key)
            return
        if result.execution_status is ExecutionStatus.NON_RETRYABLE_ERROR:
            self._terminal_errors[key] = result
            self._last_result_retryable.discard(key)
            return
        self._last_result_retryable.add(key)

    def record_actual_attempt(self, key: ToolCacheKey) -> int:
        attempt = self._actual_attempts.get(key, 0) + 1
        self._actual_attempts[key] = attempt
        return attempt

    def retry_exhausted(self, key: ToolCacheKey) -> bool:
        return key in self._last_result_retryable and self._actual_attempts.get(key, 0) >= 2

    def actual_attempts(self, key: ToolCacheKey) -> int:
        return self._actual_attempts.get(key, 0)

    def invalidate_case(self, case_id: str) -> None:
        self._reusable = {
            key: value for key, value in self._reusable.items() if key.case_id != case_id
        }
        self._terminal_errors = {
            key: value for key, value in self._terminal_errors.items() if key.case_id != case_id
        }
        self._actual_attempts = {
            key: value for key, value in self._actual_attempts.items() if key.case_id != case_id
        }
        self._last_result_retryable = {
            key for key in self._last_result_retryable if key.case_id != case_id
        }

    def __len__(self) -> int:
        return len(self._reusable) + len(self._terminal_errors)

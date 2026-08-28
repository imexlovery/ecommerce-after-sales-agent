"""Neutral exception types for deterministic selector/provider boundaries."""

from __future__ import annotations


class SelectorExecutionFailure(RuntimeError):
    """A selector call cannot be safely converted into a production decision."""

    def __init__(self, message: str, *, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


class ProviderBudgetAdmissionRejected(SelectorExecutionFailure):
    """The project-owned provider budget denied a call before provider I/O."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(
            f"provider call admission rejected: {reason_code}",
            reason_code=reason_code,
        )


class ProviderInvocationFailure(SelectorExecutionFailure):
    """A provider attempt failed after admission and has been ledger-recorded."""

    def __init__(self, reason_code: str, *, cause: BaseException) -> None:
        super().__init__(
            f"provider invocation failed: {reason_code}",
            reason_code=reason_code,
        )
        self.__cause__ = cause


class SelectorSchemaFailure(SelectorExecutionFailure):
    """A returned model message cannot satisfy the native selector contract."""

    def __init__(
        self,
        message: str = "provider selector response violates schema",
        *,
        reason_code: str = "SELECTOR_SCHEMA_FAILURE",
    ) -> None:
        super().__init__(message, reason_code=reason_code)


__all__ = [
    "ProviderBudgetAdmissionRejected",
    "ProviderInvocationFailure",
    "SelectorExecutionFailure",
    "SelectorSchemaFailure",
]

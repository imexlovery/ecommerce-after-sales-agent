"""Durable, deterministic budget accounting for a V3 Development execution.

The ledger is deliberately independent from LangChain callback aggregation.  A
provider admission is recorded before the model invocation, and the terminal
result is recorded afterwards.  The event log is append-only and rebuilt under
an inter-process lock, so a restart or replay cannot silently spend a second
provider call for the same ``(logical_run_key, selector_turn)``.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from os import fsync
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TOKEN_THRESHOLD_SEMANTICS: Final = "cumulative_observed_total_tokens_post_response_stop"
PROVIDER_CALL_SEMANTICS: Final = "pre_call_admitted_outer_ainvoke_attempt"
PROVIDER_RETRY_POLICY: Final = (
    "sdk_retries_disabled_internal_transport_attempts_not_observable"
)
LEDGER_SCHEMA_VERSION: Final = "v3.development-budget-ledger.v1"
LEDGER_EVENT_SCHEMA_VERSION: Final = "v3.development-budget-event.v1"

LedgerInvocationStatus = Literal["admitted", "completed", "provider_error", "timeout", "cancelled"]
LedgerStopReason = Literal[
    "provider_budget_exhausted",
    "provider_invocation_incomplete",
    "token_threshold_exhausted",
    "token_usage_unavailable",
]


class DevelopmentBudgetLedgerError(RuntimeError):
    """Raised when the durable budget ledger is malformed or inconsistent."""


class DevelopmentBudgetBinding(BaseModel):
    """Immutable execution identity and resource contract for one ledger."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v3.development-budget-binding.v1"] = (
        "v3.development-budget-binding.v1"
    )
    execution_identity: str = Field(
        pattern=r"^V3-(DEV-EXEC|LOCKED-EXEC)-[A-Z0-9][A-Z0-9-]{2,79}$"
    )
    source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    manifest_digests: Mapping[str, str]
    plan_version: str = Field(min_length=1)
    authorized_provider_call_ceiling: int = Field(ge=0)
    authorized_provider_call_ceiling_per_run: int = Field(ge=0, le=16)
    provider_hard_ceiling: Literal[True] = True
    provider_call_semantics: Literal["pre_call_admitted_outer_ainvoke_attempt"] = (
        PROVIDER_CALL_SEMANTICS
    )
    provider_retry_policy: Literal[
        "sdk_retries_disabled_internal_transport_attempts_not_observable"
    ] = PROVIDER_RETRY_POLICY
    token_threshold: int | None = Field(default=None, ge=1)
    token_threshold_semantics: Literal[
        "cumulative_observed_total_tokens_post_response_stop"
    ] = TOKEN_THRESHOLD_SEMANTICS
    output_token_cap_per_invocation: int = Field(gt=0)
    hard_token_ceiling: Literal[False] = False
    overshoot_bound_provable: Literal[False] = False

    @model_validator(mode="after")
    def validate_digests(self) -> DevelopmentBudgetBinding:
        if not self.manifest_digests:
            raise ValueError("budget binding requires manifest digests")
        if any(
            not isinstance(name, str)
            or not name
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
            for name, digest in self.manifest_digests.items()
        ):
            raise ValueError("budget binding contains an invalid manifest digest")
        if (
            self.token_threshold is None
            and self.token_threshold_semantics != TOKEN_THRESHOLD_SEMANTICS
        ):
            raise ValueError("token threshold semantics must remain explicit when unconfigured")
        if self.authorized_provider_call_ceiling_per_run > self.authorized_provider_call_ceiling:
            raise ValueError("per-run provider ceiling cannot exceed the execution ceiling")
        return self

    @property
    def binding_digest(self) -> str:
        payload = self.model_dump(mode="json")
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class DevelopmentBudgetInvocation(BaseModel):
    """One admitted provider attempt and its terminal observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    invocation_id: str = Field(min_length=1)
    logical_run_key: str = Field(min_length=1)
    selector_turn: int = Field(ge=1, le=16)
    architecture: Literal["agent"] = "agent"
    status: LedgerInvocationStatus
    output_token_cap: int = Field(gt=0)
    admitted_at: datetime
    completed_at: datetime | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    error_code: str | None = None

    @model_validator(mode="after")
    def validate_times(self) -> DevelopmentBudgetInvocation:
        if self.admitted_at.tzinfo is None or self.admitted_at.utcoffset() is None:
            raise ValueError("budget invocation admitted_at must be timezone-aware")
        if self.completed_at is not None:
            if self.completed_at.tzinfo is None or self.completed_at.utcoffset() is None:
                raise ValueError("budget invocation completed_at must be timezone-aware")
            if self.completed_at < self.admitted_at:
                raise ValueError("budget invocation completed_at cannot precede admission")
        if self.status == "admitted" and self.completed_at is not None:
            raise ValueError("admitted invocation cannot have a completion timestamp")
        if self.status != "admitted" and self.completed_at is None:
            raise ValueError("terminal invocation must have a completion timestamp")
        return self


class DevelopmentBudgetLedgerSnapshot(BaseModel):
    """Rebuilt ledger state used by reports and per-run metric projection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v3.development-budget-ledger.v1"] = LEDGER_SCHEMA_VERSION
    binding: DevelopmentBudgetBinding
    binding_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    attempted_provider_calls: int = Field(ge=0)
    completed_provider_calls: int = Field(ge=0)
    provider_errors: int = Field(ge=0)
    provider_timeouts: int = Field(ge=0)
    provider_cancellations: int = Field(ge=0)
    provider_reported_input_tokens: int | None = Field(default=None, ge=0)
    provider_reported_output_tokens: int | None = Field(default=None, ge=0)
    provider_reported_total_tokens: int | None = Field(default=None, ge=0)
    token_usage_complete: bool
    token_usage_missing_call_count: int = Field(ge=0)
    remaining_provider_calls: int = Field(ge=0)
    threshold_exhausted: bool = False
    token_overshoot: int | None = Field(default=None, ge=0)
    stop_reason: LedgerStopReason | None = None
    last_logical_run_key: str | None = None
    invocations: tuple[DevelopmentBudgetInvocation, ...] = ()

    @model_validator(mode="after")
    def validate_snapshot(self) -> DevelopmentBudgetLedgerSnapshot:
        if self.binding_digest != self.binding.binding_digest:
            raise ValueError("budget ledger binding digest mismatch")
        if self.attempted_provider_calls != len(self.invocations):
            raise ValueError("budget ledger attempted count mismatch")
        if self.remaining_provider_calls != max(
            self.binding.authorized_provider_call_ceiling - self.attempted_provider_calls,
            0,
        ):
            raise ValueError("budget ledger remaining count mismatch")
        if self.completed_provider_calls > self.attempted_provider_calls:
            raise ValueError("completed provider calls exceed attempts")
        if self.threshold_exhausted and self.binding.token_threshold is None:
            raise ValueError("token threshold cannot be exhausted when unconfigured")
        return self


@dataclass(frozen=True, slots=True)
class DevelopmentBudgetAdmission:
    granted: bool
    invocation_id: str | None
    reason: str
    remaining_provider_calls: int


@dataclass(frozen=True, slots=True)
class DevelopmentBudgetRunAccounting:
    """Per logical-run view of the execution-scoped ledger."""

    attempted_provider_calls: int = 0
    completed_provider_calls: int = 0
    provider_errors: int = 0
    provider_timeouts: int = 0
    provider_cancellations: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    token_usage_complete: bool = True
    token_threshold: int | None = None
    threshold_exhausted: bool = False
    token_overshoot: int | None = None
    remaining_provider_calls: int = 0
    stop_reason: str | None = None
    binding_digest: str | None = None

    @property
    def model_invocation_attempts(self) -> int:
        """Agent model calls equal admitted provider attempts with retries disabled."""

        return self.attempted_provider_calls

    @property
    def completed_model_calls(self) -> int:
        return self.completed_provider_calls


class _LedgerEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v3.development-budget-event.v1"] = LEDGER_EVENT_SCHEMA_VERSION
    event_id: str = Field(min_length=1)
    binding_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_type: Literal["initialized", "admitted", "completed", "stop"]
    recorded_at: datetime
    payload: Mapping[str, Any]

    @model_validator(mode="after")
    def validate_timestamp(self) -> _LedgerEvent:
        if self.recorded_at.tzinfo is None or self.recorded_at.utcoffset() is None:
            raise ValueError("budget event recorded_at must be timezone-aware")
        return self


def _now() -> datetime:
    return datetime.now(UTC)


def _canonical_invocation_id(binding_digest: str, logical_run_key: str, selector_turn: int) -> str:
    value = f"{binding_digest}:{logical_run_key}:{selector_turn}"
    return f"budget-inv-{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _usage_value(usage: Mapping[str, Any] | None, key: str) -> int | None:
    if usage is None or key not in usage or usage[key] is None:
        return None
    value = usage[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise DevelopmentBudgetLedgerError(f"provider usage field {key} is not an integer")
    if value < 0:
        raise DevelopmentBudgetLedgerError(f"provider usage field {key} is negative")
    return int(value)


class DevelopmentBudgetLedger:
    """Append-only execution ledger with deterministic admission semantics."""

    def __init__(self, path: Path, *, binding: DevelopmentBudgetBinding) -> None:
        self.path = path.expanduser().resolve()
        self.lock_path = self.path.with_name(f"{self.path.name}.lock")
        self.binding = binding
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._locked(exclusive=True):
            events = self._load_events_unlocked()
            if not events:
                self._append_event_unlocked(
                    event_type="initialized",
                    payload={"binding": binding.model_dump(mode="json")},
                )
            else:
                self._validate_event_binding(events)
                state = self._rebuild(events)
                if state.stop_reason == "provider_invocation_incomplete" and not any(
                    event.event_type == "stop" for event in events
                ):
                    self._append_event_unlocked(
                        event_type="stop",
                        payload={"reason": "provider_invocation_incomplete"},
                    )

    @contextmanager
    def _locked(self, *, exclusive: bool) -> Iterator[None]:
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _load_events_unlocked(self) -> tuple[_LedgerEvent, ...]:
        if not self.path.exists():
            return ()
        events: list[_LedgerEvent] = []
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                events.append(_LedgerEvent.model_validate_json(line))
            except Exception as exc:
                raise DevelopmentBudgetLedgerError(
                    f"invalid budget ledger event at line {line_number}"
                ) from exc
        return tuple(events)

    def _validate_event_binding(self, events: tuple[_LedgerEvent, ...]) -> None:
        expected = self.binding.binding_digest
        if any(event.binding_digest != expected for event in events):
            raise DevelopmentBudgetLedgerError(
                "budget ledger binding differs from execution identity"
            )
        first = events[0]
        if first.event_type != "initialized":
            raise DevelopmentBudgetLedgerError("budget ledger must start with initialization")
        try:
            persisted = DevelopmentBudgetBinding.model_validate(first.payload.get("binding"))
        except Exception as exc:
            raise DevelopmentBudgetLedgerError("budget ledger initialization is malformed") from exc
        if persisted != self.binding:
            raise DevelopmentBudgetLedgerError("budget ledger binding payload differs")

    def _append_event_unlocked(
        self,
        *,
        event_type: Literal["initialized", "admitted", "completed", "stop"],
        payload: Mapping[str, Any],
    ) -> None:
        event = _LedgerEvent(
            event_id=uuid_like(),
            binding_digest=self.binding.binding_digest,
            event_type=event_type,
            recorded_at=_now(),
            payload=dict(payload),
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json() + "\n")
            handle.flush()
            fsync(handle.fileno())

    def _rebuild(self, events: tuple[_LedgerEvent, ...]) -> DevelopmentBudgetLedgerSnapshot:
        self._validate_event_binding(events)
        invocations: dict[str, DevelopmentBudgetInvocation] = {}
        identity_by_run_turn: dict[tuple[str, int], str] = {}
        stop_reason: LedgerStopReason | None = None
        for event in events[1:]:
            payload = dict(event.payload)
            if event.event_type == "admitted":
                invocation = DevelopmentBudgetInvocation.model_validate(payload)
                if invocation.invocation_id in invocations:
                    raise DevelopmentBudgetLedgerError("duplicate budget invocation identity")
                run_turn = (invocation.logical_run_key, invocation.selector_turn)
                if run_turn in identity_by_run_turn:
                    raise DevelopmentBudgetLedgerError("duplicate logical selector invocation")
                identity_by_run_turn[run_turn] = invocation.invocation_id
                invocations[invocation.invocation_id] = invocation
            elif event.event_type == "completed":
                invocation_id = str(payload.get("invocation_id", ""))
                current = invocations.get(invocation_id)
                if current is None or current.status != "admitted":
                    raise DevelopmentBudgetLedgerError(
                        "completion does not match an admitted invocation"
                    )
                completed_at = payload.get("completed_at")
                if not isinstance(completed_at, str):
                    raise DevelopmentBudgetLedgerError(
                        "completion is missing a serialized completion timestamp"
                    )
                updated_payload = current.model_dump(mode="python")
                updated_payload.update(
                    {
                        "status": payload.get("status"),
                        "completed_at": datetime.fromisoformat(completed_at),
                        "input_tokens": payload.get("input_tokens"),
                        "output_tokens": payload.get("output_tokens"),
                        "total_tokens": payload.get("total_tokens"),
                        "error_code": payload.get("error_code"),
                    }
                )
                invocations[invocation_id] = DevelopmentBudgetInvocation.model_validate(
                    updated_payload
                )
            elif event.event_type == "stop":
                raw_reason = payload.get("reason")
                if raw_reason not in {
                    "provider_budget_exhausted",
                    "provider_invocation_incomplete",
                    "token_threshold_exhausted",
                    "token_usage_unavailable",
                }:
                    raise DevelopmentBudgetLedgerError("unknown budget stop reason")
                stop_reason = raw_reason
        ordered = tuple(invocations.values())
        pending = tuple(item for item in ordered if item.status == "admitted")
        terminal = tuple(item for item in ordered if item.status != "admitted")
        completed = tuple(item for item in terminal if item.status == "completed")
        provider_errors = sum(item.status == "provider_error" for item in terminal)
        provider_timeouts = sum(item.status == "timeout" for item in terminal)
        provider_cancellations = sum(item.status == "cancelled" for item in terminal)
        input_values = [item.input_tokens for item in terminal if item.input_tokens is not None]
        output_values = [item.output_tokens for item in terminal if item.output_tokens is not None]
        total_values = [item.total_tokens for item in terminal if item.total_tokens is not None]
        usage_missing = sum(
            item.input_tokens is None or item.output_tokens is None or item.total_tokens is None
            for item in terminal
        )
        token_usage_complete = not pending and (not terminal or usage_missing == 0)
        total_tokens = sum(total_values) if total_values else None
        threshold = self.binding.token_threshold
        threshold_exhausted = (
            threshold is not None
            and token_usage_complete
            and total_tokens is not None
            and total_tokens >= threshold
        )
        overshoot = (
            max(total_tokens - threshold, 0)
            if threshold is not None and token_usage_complete and total_tokens is not None
            else None
        )
        if pending:
            stop_reason = "provider_invocation_incomplete"
        elif threshold_exhausted:
            stop_reason = "token_threshold_exhausted"
        elif threshold is not None and not token_usage_complete and terminal:
            stop_reason = "token_usage_unavailable"
        if stop_reason is None and len(ordered) >= self.binding.authorized_provider_call_ceiling:
            stop_reason = "provider_budget_exhausted"
        return DevelopmentBudgetLedgerSnapshot(
            binding=self.binding,
            binding_digest=self.binding.binding_digest,
            attempted_provider_calls=len(ordered),
            completed_provider_calls=len(completed),
            provider_errors=provider_errors,
            provider_timeouts=provider_timeouts,
            provider_cancellations=provider_cancellations,
            provider_reported_input_tokens=sum(input_values) if input_values else None,
            provider_reported_output_tokens=sum(output_values) if output_values else None,
            provider_reported_total_tokens=total_tokens,
            token_usage_complete=token_usage_complete,
            token_usage_missing_call_count=usage_missing,
            remaining_provider_calls=max(
                self.binding.authorized_provider_call_ceiling - len(ordered), 0
            ),
            threshold_exhausted=threshold_exhausted,
            token_overshoot=overshoot,
            stop_reason=stop_reason,
            last_logical_run_key=ordered[-1].logical_run_key if ordered else None,
            invocations=ordered,
        )

    def snapshot(self) -> DevelopmentBudgetLedgerSnapshot:
        with self._locked(exclusive=False):
            events = self._load_events_unlocked()
            if not events:
                raise DevelopmentBudgetLedgerError("budget ledger is unexpectedly empty")
            return self._rebuild(events)

    def admit_provider_call(
        self,
        *,
        logical_run_key: str,
        selector_turn: int,
    ) -> DevelopmentBudgetAdmission:
        if not logical_run_key:
            raise DevelopmentBudgetLedgerError("logical run key is required for provider admission")
        if not 1 <= selector_turn <= 16:
            raise DevelopmentBudgetLedgerError("selector turn is outside the V3 contract")
        with self._locked(exclusive=True):
            events = self._load_events_unlocked()
            state = self._rebuild(events)
            existing = next(
                (
                    item
                    for item in state.invocations
                    if item.logical_run_key == logical_run_key
                    and item.selector_turn == selector_turn
                ),
                None,
            )
            if existing is not None:
                return DevelopmentBudgetAdmission(
                    granted=False,
                    invocation_id=existing.invocation_id,
                    reason="replay_invocation_already_consumed",
                    remaining_provider_calls=state.remaining_provider_calls,
                )
            per_run_attempts = sum(
                item.logical_run_key == logical_run_key for item in state.invocations
            )
            if state.threshold_exhausted:
                return DevelopmentBudgetAdmission(
                    granted=False,
                    invocation_id=None,
                    reason="token_threshold_exhausted",
                    remaining_provider_calls=state.remaining_provider_calls,
                )
            if state.stop_reason == "token_usage_unavailable":
                return DevelopmentBudgetAdmission(
                    granted=False,
                    invocation_id=None,
                    reason="token_usage_unavailable",
                    remaining_provider_calls=state.remaining_provider_calls,
                )
            if state.stop_reason is not None:
                return DevelopmentBudgetAdmission(
                    granted=False,
                    invocation_id=None,
                    reason=state.stop_reason,
                    remaining_provider_calls=state.remaining_provider_calls,
                )
            if state.attempted_provider_calls >= self.binding.authorized_provider_call_ceiling:
                return DevelopmentBudgetAdmission(
                    granted=False,
                    invocation_id=None,
                    reason="provider_budget_exhausted",
                    remaining_provider_calls=0,
                )
            if per_run_attempts >= self.binding.authorized_provider_call_ceiling_per_run:
                return DevelopmentBudgetAdmission(
                    granted=False,
                    invocation_id=None,
                    reason="provider_budget_exhausted",
                    remaining_provider_calls=state.remaining_provider_calls,
                )
            invocation_id = _canonical_invocation_id(
                self.binding.binding_digest, logical_run_key, selector_turn
            )
            invocation = DevelopmentBudgetInvocation(
                invocation_id=invocation_id,
                logical_run_key=logical_run_key,
                selector_turn=selector_turn,
                status="admitted",
                output_token_cap=self.binding.output_token_cap_per_invocation,
                admitted_at=_now(),
            )
            self._append_event_unlocked(
                event_type="admitted",
                payload=invocation.model_dump(mode="json"),
            )
            return DevelopmentBudgetAdmission(
                granted=True,
                invocation_id=invocation_id,
                reason="admitted",
                remaining_provider_calls=max(
                    self.binding.authorized_provider_call_ceiling
                    - state.attempted_provider_calls
                    - 1,
                    0,
                ),
            )

    def complete_provider_call(
        self,
        *,
        invocation_id: str,
        logical_run_key: str,
        status: Literal["completed", "provider_error", "timeout", "cancelled"],
        usage: Mapping[str, Any] | None = None,
        error_code: str | None = None,
    ) -> DevelopmentBudgetLedgerSnapshot:
        with self._locked(exclusive=True):
            events = self._load_events_unlocked()
            state = self._rebuild(events)
            current = next(
                (item for item in state.invocations if item.invocation_id == invocation_id),
                None,
            )
            if current is None:
                raise DevelopmentBudgetLedgerError("completion references an unknown invocation")
            if current.logical_run_key != logical_run_key:
                raise DevelopmentBudgetLedgerError("completion logical run key differs")
            if current.status != "admitted":
                return state
            input_tokens = _usage_value(usage, "input_tokens")
            output_tokens = _usage_value(usage, "output_tokens")
            total_tokens = _usage_value(usage, "total_tokens")
            completed_at = _now()
            self._append_event_unlocked(
                event_type="completed",
                payload={
                    "invocation_id": invocation_id,
                    "status": status,
                    "completed_at": completed_at.isoformat(),
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": total_tokens,
                    "error_code": error_code,
                },
            )
            updated_events = self._load_events_unlocked()
            updated = self._rebuild(updated_events)
            if updated.stop_reason is not None and state.stop_reason != updated.stop_reason:
                self._append_event_unlocked(
                    event_type="stop",
                    payload={"reason": updated.stop_reason, "logical_run_key": logical_run_key},
                )
                updated = self._rebuild(self._load_events_unlocked())
            return updated

    def accounting_for(self, logical_run_key: str) -> DevelopmentBudgetRunAccounting:
        state = self.snapshot()
        invocations = tuple(
            item for item in state.invocations if item.logical_run_key == logical_run_key
        )
        pending = tuple(item for item in invocations if item.status == "admitted")
        terminal = tuple(item for item in invocations if item.status != "admitted")
        completed = tuple(item for item in terminal if item.status == "completed")
        input_values = [item.input_tokens for item in terminal if item.input_tokens is not None]
        output_values = [item.output_tokens for item in terminal if item.output_tokens is not None]
        total_values = [item.total_tokens for item in terminal if item.total_tokens is not None]
        missing = any(
            item.input_tokens is None or item.output_tokens is None or item.total_tokens is None
            for item in terminal
        )
        threshold = state.binding.token_threshold
        total_tokens = sum(total_values) if total_values else None
        token_usage_complete = not pending and not missing
        threshold_exhausted = (
            threshold is not None
            and token_usage_complete
            and total_tokens is not None
            and total_tokens >= threshold
        )
        overshoot = (
            max(total_tokens - threshold, 0)
            if threshold is not None and token_usage_complete and total_tokens is not None
            else None
        )
        return DevelopmentBudgetRunAccounting(
            attempted_provider_calls=len(invocations),
            completed_provider_calls=len(completed),
            provider_errors=sum(item.status == "provider_error" for item in invocations),
            provider_timeouts=sum(item.status == "timeout" for item in invocations),
            provider_cancellations=sum(item.status == "cancelled" for item in invocations),
            input_tokens=sum(input_values) if input_values else None,
            output_tokens=sum(output_values) if output_values else None,
            total_tokens=total_tokens,
            token_usage_complete=token_usage_complete,
            token_threshold=threshold,
            threshold_exhausted=threshold_exhausted,
            token_overshoot=overshoot,
            remaining_provider_calls=state.remaining_provider_calls,
            stop_reason=state.stop_reason,
            binding_digest=state.binding_digest,
        )

    def force_stop_reason(self, reason: LedgerStopReason) -> DevelopmentBudgetLedgerSnapshot:
        """Persist a deterministic stop marker after a denied admission."""

        with self._locked(exclusive=True):
            events = self._load_events_unlocked()
            state = self._rebuild(events)
            if state.stop_reason != reason:
                self._append_event_unlocked(event_type="stop", payload={"reason": reason})
                state = self._rebuild(self._load_events_unlocked())
            return state


def uuid_like() -> str:
    """Generate a non-semantic event identity without importing UUID at module load."""

    import uuid

    return uuid.uuid4().hex


__all__ = [
    "DevelopmentBudgetAdmission",
    "DevelopmentBudgetBinding",
    "DevelopmentBudgetInvocation",
    "DevelopmentBudgetLedger",
    "DevelopmentBudgetLedgerError",
    "DevelopmentBudgetLedgerSnapshot",
    "DevelopmentBudgetRunAccounting",
    "LEDGER_EVENT_SCHEMA_VERSION",
    "LEDGER_SCHEMA_VERSION",
    "TOKEN_THRESHOLD_SEMANTICS",
]

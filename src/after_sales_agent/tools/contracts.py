"""Typed envelopes exchanged across the governed read-tool boundary."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from after_sales_agent.domain.state import (
    DeliveryProofStatus,
    EvidenceAvailability,
    ExecutionStatus,
    IssueType,
    OrderStatus,
)


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def normalized_result_hash(value: Any) -> str:
    """Hash canonical JSON rather than transport bytes or object repr output."""

    value = _normalized_json_value(value)
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalized_json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _normalized_json_value(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(key): _normalized_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalized_json_value(item) for item in value]
    if isinstance(value, datetime):
        _require_aware(value, "hashed timestamp")
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


class EvidenceRef(ContractModel):
    tool_call_id: str = Field(min_length=1)
    source_query_id: str = Field(min_length=1)
    source_record_id: str | None = None
    field_path: str | None = None
    observed_at: datetime
    result_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_observed_at(self) -> EvidenceRef:
        _require_aware(self.observed_at, "observed_at")
        return self


class TimelineEvent(ContractModel):
    event_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    occurred_at: datetime
    location: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def validate_occurred_at(self) -> TimelineEvent:
        _require_aware(self.occurred_at, "occurred_at")
        return self


class OrderContextPayload(ContractModel):
    order_id: str = Field(min_length=1)
    order_status: OrderStatus
    tracking_number: str | None = None
    service_level: str = Field(min_length=1)
    shipped_at: datetime | None = None
    delivered_at: datetime | None = None

    @model_validator(mode="after")
    def validate_timestamps(self) -> OrderContextPayload:
        for name in ("shipped_at", "delivered_at"):
            value = getattr(self, name)
            if value is not None:
                _require_aware(value, name)
        return self


class LogisticsTimelinePayload(ContractModel):
    order_id: str = Field(min_length=1)
    events: list[TimelineEvent]
    last_update_at: datetime | None
    hours_since_last_update: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_last_update(self) -> LogisticsTimelinePayload:
        if self.last_update_at is not None:
            _require_aware(self.last_update_at, "last_update_at")
        if bool(self.events) != (self.last_update_at is not None):
            raise ValueError("last_update_at must be present exactly when timeline events exist")
        return self


class DeliveryProofPayload(ContractModel):
    order_id: str = Field(min_length=1)
    pod_status: DeliveryProofStatus
    recipient_type: str | None = None
    signed_at: datetime | None = None
    note: str | None = None

    @model_validator(mode="after")
    def validate_proof(self) -> DeliveryProofPayload:
        if self.signed_at is not None:
            _require_aware(self.signed_at, "signed_at")
        if self.pod_status is DeliveryProofStatus.NOT_FOUND:
            if self.recipient_type is not None or self.signed_at is not None:
                raise ValueError("not-found delivery proof cannot contain reception facts")
        return self


class CarrierAlert(ContractModel):
    alert_id: str = Field(min_length=1)
    code: str = Field(min_length=1)
    description: str = Field(min_length=1)
    active_from: datetime
    active_until: datetime | None = None

    @model_validator(mode="after")
    def validate_window(self) -> CarrierAlert:
        _require_aware(self.active_from, "active_from")
        if self.active_until is not None:
            _require_aware(self.active_until, "active_until")
            if self.active_until < self.active_from:
                raise ValueError("active_until cannot precede active_from")
        return self


class CarrierServiceAlertsPayload(ContractModel):
    order_id: str = Field(min_length=1)
    alerts: list[CarrierAlert]


class AfterSalesPolicyPayload(ContractModel):
    order_id: str = Field(min_length=1)
    issue_type: IssueType
    eligible: bool
    policy_version: str = Field(min_length=1)
    stalled_after_hours: int | None = Field(default=None, ge=1)
    explanation: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_stalled_threshold(self) -> AfterSalesPolicyPayload:
        if self.issue_type is IssueType.STALLED_TRACKING and self.stalled_after_hours is None:
            raise ValueError("stalled_tracking policy requires stalled_after_hours")
        return self


class LogisticsTicket(ContractModel):
    ticket_id: str = Field(min_length=1)
    order_id: str = Field(min_length=1)
    issue_type: IssueType
    ticket_status: str = Field(min_length=1)
    created_at: datetime

    @model_validator(mode="after")
    def validate_created_at(self) -> LogisticsTicket:
        _require_aware(self.created_at, "created_at")
        return self


class ExistingLogisticsTicketsPayload(ContractModel):
    order_id: str = Field(min_length=1)
    issue_type: IssueType
    active_tickets: list[LogisticsTicket]


class ToolResult[PayloadT: BaseModel](ContractModel):
    """One normalized query result; errors can never masquerade as absence."""

    execution_status: ExecutionStatus
    evidence_availability: EvidenceAvailability
    source_type: str = Field(min_length=1)
    source_query_id: str = Field(min_length=1)
    source_record_ids: list[str] = Field(default_factory=list)
    observed_at: datetime
    payload: PayloadT | None = None
    error_code: str | None = None
    retryable: bool = False
    result_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    untrusted_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_envelope(self) -> ToolResult[PayloadT]:
        _require_aware(self.observed_at, "observed_at")
        if len(self.source_record_ids) != len(set(self.source_record_ids)):
            raise ValueError("source_record_ids must not contain duplicates")
        if len(self.untrusted_fields) != len(set(self.untrusted_fields)):
            raise ValueError("untrusted_fields must not contain duplicates")

        if self.execution_status is ExecutionStatus.SUCCESS:
            if self.evidence_availability is EvidenceAvailability.UNAVAILABLE:
                raise ValueError("successful execution cannot be unavailable")
            if self.error_code is not None or self.retryable:
                raise ValueError("successful execution cannot carry an error")
            if self.evidence_availability is EvidenceAvailability.PRESENT and self.payload is None:
                raise ValueError("present evidence requires a payload")
        else:
            if self.evidence_availability is not EvidenceAvailability.UNAVAILABLE:
                raise ValueError("failed execution must be unavailable, never absent")
            if self.payload is not None or not self.error_code:
                raise ValueError("failed execution requires error_code and no payload")
            expected_retryable = self.execution_status is ExecutionStatus.RETRYABLE_ERROR
            if self.retryable is not expected_retryable:
                raise ValueError("retryable must agree with execution_status")
        return self

    @classmethod
    def completed(
        cls,
        *,
        availability: EvidenceAvailability,
        source_type: str,
        source_query_id: str,
        observed_at: datetime,
        payload: PayloadT | None,
        source_record_ids: list[str] | None = None,
        untrusted_fields: list[str] | None = None,
    ) -> ToolResult[PayloadT]:
        if availability is EvidenceAvailability.UNAVAILABLE:
            raise ValueError("completed result cannot be unavailable")
        normalized = {
            "execution_status": ExecutionStatus.SUCCESS,
            "evidence_availability": availability,
            "payload": payload,
            "source_record_ids": source_record_ids or [],
        }
        return cls(
            execution_status=ExecutionStatus.SUCCESS,
            evidence_availability=availability,
            source_type=source_type,
            source_query_id=source_query_id,
            source_record_ids=source_record_ids or [],
            observed_at=observed_at,
            payload=payload,
            error_code=None,
            retryable=False,
            result_hash=normalized_result_hash(normalized),
            untrusted_fields=untrusted_fields or [],
        )

    @classmethod
    def failed(
        cls,
        *,
        retryable: bool,
        source_type: str,
        source_query_id: str,
        observed_at: datetime,
        error_code: str,
    ) -> ToolResult[PayloadT]:
        execution_status = (
            ExecutionStatus.RETRYABLE_ERROR if retryable else ExecutionStatus.NON_RETRYABLE_ERROR
        )
        normalized = {
            "execution_status": execution_status,
            "evidence_availability": EvidenceAvailability.UNAVAILABLE,
            "error_code": error_code,
        }
        return cls(
            execution_status=execution_status,
            evidence_availability=EvidenceAvailability.UNAVAILABLE,
            source_type=source_type,
            source_query_id=source_query_id,
            source_record_ids=[],
            observed_at=observed_at,
            payload=None,
            error_code=error_code,
            retryable=retryable,
            result_hash=normalized_result_hash(normalized),
            untrusted_fields=[],
        )

    def to_evidence_refs(self, tool_call_id: str) -> list[EvidenceRef]:
        """Create references even when a completed query found no record."""

        if self.execution_status is not ExecutionStatus.SUCCESS:
            return []
        record_ids: list[str | None] = (
            list(self.source_record_ids) if self.source_record_ids else [None]
        )
        return [
            EvidenceRef(
                tool_call_id=tool_call_id,
                source_query_id=self.source_query_id,
                source_record_id=record_id,
                field_path=None,
                observed_at=self.observed_at,
                result_hash=self.result_hash,
            )
            for record_id in record_ids
        ]


ToolResultEnvelope = ToolResult

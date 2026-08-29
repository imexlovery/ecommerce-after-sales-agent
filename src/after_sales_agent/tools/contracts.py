"""Typed envelopes exchanged across the governed read-tool boundary."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from after_sales_agent.domain.state import (
    DeliveryProofStatus,
    EvidenceAvailability,
    ExecutionStatus,
    IssueType,
    OrderStatus,
    PolicyResolutionStatus,
    RetrievalStatus,
)
from after_sales_agent.policy.corpus import PolicyFactSnapshot, canonical_json_hash


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
    shipment_id: str | None = None
    status: str = Field(min_length=1)
    occurred_at: datetime
    location: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def validate_occurred_at(self) -> TimelineEvent:
        _require_aware(self.occurred_at, "occurred_at")
        return self


class ShipmentSummaryPayload(ContractModel):
    """Trusted package identity and current delivery state within one order."""

    shipment_id: str = Field(min_length=1)
    package_sequence: int = Field(ge=1)
    package_count: int = Field(ge=1)
    tracking_number: str = Field(min_length=1)
    shipment_status: str = Field(min_length=1)
    carrier_code: str = Field(min_length=1)
    shipped_at: datetime | None = None
    delivered_at: datetime | None = None
    last_update_at: datetime | None = None

    @model_validator(mode="after")
    def validate_timestamps(self) -> ShipmentSummaryPayload:
        for name in ("shipped_at", "delivered_at", "last_update_at"):
            value = getattr(self, name)
            if value is not None:
                _require_aware(value, name)
        if self.shipped_at is not None and self.delivered_at is not None:
            if self.delivered_at < self.shipped_at:
                raise ValueError("delivered_at cannot precede shipped_at")
        return self


class ShipmentTimelinePayload(ContractModel):
    shipment_id: str = Field(min_length=1)
    package_sequence: int = Field(ge=1)
    events: list[TimelineEvent]
    last_update_at: datetime | None
    hours_since_last_update: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_last_update(self) -> ShipmentTimelinePayload:
        if self.last_update_at is not None:
            _require_aware(self.last_update_at, "last_update_at")
        if bool(self.events) != (self.last_update_at is not None):
            raise ValueError("last_update_at must be present exactly when events exist")
        return self


class OrderContextPayload(ContractModel):
    order_id: str = Field(min_length=1)
    order_status: OrderStatus
    tracking_number: str | None = None
    service_level: str = Field(min_length=1)
    region: str = Field(min_length=1)
    shipped_at: datetime | None = None
    delivered_at: datetime | None = None
    shipments: list[ShipmentSummaryPayload] = Field(default_factory=list)

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
    shipment_timelines: list[ShipmentTimelinePayload] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_last_update(self) -> LogisticsTimelinePayload:
        if self.last_update_at is not None:
            _require_aware(self.last_update_at, "last_update_at")
        if bool(self.events) != (self.last_update_at is not None):
            raise ValueError("last_update_at must be present exactly when timeline events exist")
        return self


class DeliveryProofPayload(ContractModel):
    order_id: str = Field(min_length=1)
    shipment_id: str | None = None
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
    impact_area: str = "regional"
    status: Literal["active", "resolved"] = "active"
    expected_recovery_at: datetime | None = None

    @model_validator(mode="after")
    def validate_window(self) -> CarrierAlert:
        _require_aware(self.active_from, "active_from")
        if self.active_until is not None:
            _require_aware(self.active_until, "active_until")
            if self.active_until < self.active_from:
                raise ValueError("active_until cannot precede active_from")
        if self.expected_recovery_at is not None:
            _require_aware(self.expected_recovery_at, "expected_recovery_at")
        if self.expected_recovery_at is not None and self.expected_recovery_at < self.active_from:
            raise ValueError("expected_recovery_at cannot precede active_from")
        return self

    def is_active_at(self, evaluated_at: datetime) -> bool:
        _require_aware(evaluated_at, "evaluated_at")
        return (
            self.status == "active"
            and self.active_from <= evaluated_at
            and (self.active_until is None or evaluated_at < self.active_until)
        )


class CarrierServiceAlertsPayload(ContractModel):
    order_id: str = Field(min_length=1)
    alerts: list[CarrierAlert]


class VerifiedPolicyCitation(ContractModel):
    """Bounded canonical excerpt for UI/trace use, never retriever text or a vector."""

    document_id: str = Field(min_length=1)
    document_title: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    clause_id: str = Field(min_length=1)
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    display_summary: str = Field(min_length=1, max_length=500)
    excerpt: str = Field(min_length=1, max_length=320)
    excerpt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    text_classification: Literal["untrusted_explanatory_text"] = "untrusted_explanatory_text"


class PolicySearchPayload(ContractModel):
    """A completed controlled policy search, with authority facts only from the resolver."""

    order_id: str = Field(min_length=1)
    issue_type: IssueType
    service_level: str = Field(min_length=1)
    region: str = Field(min_length=1)
    evaluated_at: datetime
    retrieval_status: RetrievalStatus
    policy_resolution_status: PolicyResolutionStatus | None = None
    corpus_version: str = Field(min_length=1)
    corpus_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    index_format_version: str = Field(min_length=1)
    index_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    index_content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    embedding_model_id: str = Field(min_length=1)
    embedding_model_revision: str = Field(min_length=1)
    retrieval_mode: str = Field(min_length=1)
    candidate_clause_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=3)
    candidate_count: int = Field(default=0, ge=0, le=3)
    selected_rank: int | None = Field(default=None, ge=1, le=3)
    selected_similarity: float | None = Field(default=None, ge=-1.0, le=1.0)
    top_1_score: float | None = Field(default=None, ge=-1.0, le=1.0)
    retrieval_threshold: float = Field(ge=-1.0, le=1.0)
    policy_fact_snapshot: PolicyFactSnapshot | None = None
    policy_fact_snapshot_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    citation: VerifiedPolicyCitation | None = None
    retrieval_latency_ms: float = Field(ge=0)
    resolver_latency_ms: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_resolution_contract(self) -> PolicySearchPayload:
        _require_aware(self.evaluated_at, "evaluated_at")
        if self.candidate_count != len(self.candidate_clause_ids):
            raise ValueError("candidate_count must match candidate_clause_ids")
        if len(self.candidate_clause_ids) != len(set(self.candidate_clause_ids)):
            raise ValueError("candidate_clause_ids must not contain duplicates")
        if (self.candidate_count == 0) != (self.top_1_score is None):
            raise ValueError("top_1_score must be present exactly when candidates exist")
        if self.retrieval_status is RetrievalStatus.HIT:
            if self.policy_resolution_status is None:
                raise ValueError("a retrieval hit requires a policy resolution status")
            if self.candidate_count < 1:
                raise ValueError("a retrieval hit requires at least one candidate")
        elif self.policy_resolution_status is not None:
            raise ValueError("no_hit and unavailable cannot fabricate a resolution status")

        facts = self.policy_fact_snapshot
        if self.policy_resolution_status is PolicyResolutionStatus.APPLICABLE:
            if facts is None or self.policy_fact_snapshot_hash is None or self.citation is None:
                raise ValueError("applicable policy resolution requires facts, hash, and citation")
            if (
                facts.issue_type is not self.issue_type
                or facts.service_level != self.service_level
                or facts.region != self.region
            ):
                raise ValueError(
                    "applicable facts must match trusted issue, service level, and region"
                )
            if (
                facts.policy_version != self.citation.policy_version
                or facts.clause_id != self.citation.clause_id
            ):
                raise ValueError("citation must identify the applied policy facts")
            if facts.source_hash != self.citation.source_hash:
                raise ValueError("citation hash must match applied policy facts")
            if self.citation.excerpt_hash != canonical_json_hash(
                {"source_hash": self.citation.source_hash, "excerpt": self.citation.excerpt}
            ):
                raise ValueError("citation excerpt must bind to its canonical source hash")
            if facts.material_snapshot_hash != self.policy_fact_snapshot_hash:
                raise ValueError("policy fact snapshot hash does not match canonical facts")
        elif any(
            item is not None for item in (facts, self.policy_fact_snapshot_hash, self.citation)
        ):
            raise ValueError("non-applicable policy outcomes cannot carry facts or citations")
        elif self.retrieval_status is RetrievalStatus.NO_HIT:
            if any(
                item is not None
                for item in (
                    self.selected_rank,
                    self.selected_similarity,
                    facts,
                    self.policy_fact_snapshot_hash,
                    self.citation,
                )
            ):
                raise ValueError("no_hit must not include a selected candidate or policy facts")
        if self.policy_resolution_status is not PolicyResolutionStatus.APPLICABLE:
            if self.policy_fact_snapshot_hash is not None and facts is None:
                raise ValueError("a policy fact hash requires policy facts")
        return self

    @property
    def verified_for_gate(self) -> bool:
        return (
            self.retrieval_status is RetrievalStatus.HIT
            and self.policy_resolution_status is PolicyResolutionStatus.APPLICABLE
            and self.policy_fact_snapshot is not None
            and self.citation is not None
            and self.policy_fact_snapshot_hash == self.policy_fact_snapshot.material_snapshot_hash
        )


class LogisticsTicket(ContractModel):
    ticket_id: str = Field(min_length=1)
    order_id: str = Field(min_length=1)
    issue_type: IssueType
    ticket_status: str = Field(min_length=1)
    created_at: datetime
    target_shipment_id: str | None = None
    stage: str | None = None
    next_update_at: datetime | None = None

    @model_validator(mode="after")
    def validate_created_at(self) -> LogisticsTicket:
        _require_aware(self.created_at, "created_at")
        if self.next_update_at is not None:
            _require_aware(self.next_update_at, "next_update_at")
        return self


class ExistingInvestigation(ContractModel):
    case_id: str = Field(min_length=1)
    order_id: str = Field(min_length=1)
    issue_type: IssueType
    target_shipment_id: str | None = None
    stage: str = Field(min_length=1)
    updated_at: datetime
    next_update_at: datetime | None = None

    @model_validator(mode="after")
    def validate_timestamps(self) -> ExistingInvestigation:
        _require_aware(self.updated_at, "updated_at")
        if self.next_update_at is not None:
            _require_aware(self.next_update_at, "next_update_at")
        return self


class ExistingLogisticsTicketsPayload(ContractModel):
    order_id: str = Field(min_length=1)
    issue_type: IssueType
    active_tickets: list[LogisticsTicket]
    existing_investigations: list[ExistingInvestigation] = Field(default_factory=list)


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
    retrieval_status: RetrievalStatus | None = None
    policy_resolution_status: PolicyResolutionStatus | None = None

    @model_validator(mode="after")
    def validate_envelope(self) -> ToolResult[PayloadT]:
        _require_aware(self.observed_at, "observed_at")
        if len(self.source_record_ids) != len(set(self.source_record_ids)):
            raise ValueError("source_record_ids must not contain duplicates")
        if len(self.untrusted_fields) != len(set(self.untrusted_fields)):
            raise ValueError("untrusted_fields must not contain duplicates")
        if self.retrieval_status is None and self.policy_resolution_status is not None:
            raise ValueError("policy resolution status requires a retrieval status")
        if self.retrieval_status is RetrievalStatus.HIT and self.policy_resolution_status is None:
            raise ValueError("retrieval hit requires policy resolution status")
        if self.retrieval_status in {RetrievalStatus.NO_HIT, RetrievalStatus.UNAVAILABLE}:
            if self.policy_resolution_status is not None:
                raise ValueError("no_hit or unavailable cannot fabricate policy resolution")

        if self.execution_status is ExecutionStatus.SUCCESS:
            if self.evidence_availability is EvidenceAvailability.UNAVAILABLE:
                raise ValueError("successful execution cannot be unavailable")
            if self.retrieval_status is RetrievalStatus.UNAVAILABLE:
                raise ValueError("successful execution cannot have unavailable retrieval")
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
            if (
                self.retrieval_status is not None
                and self.retrieval_status is not RetrievalStatus.UNAVAILABLE
            ):
                raise ValueError("failed policy retrieval must be marked unavailable")
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
        retrieval_status: RetrievalStatus | None = None,
        policy_resolution_status: PolicyResolutionStatus | None = None,
    ) -> ToolResult[PayloadT]:
        if availability is EvidenceAvailability.UNAVAILABLE:
            raise ValueError("completed result cannot be unavailable")
        normalized = {
            "execution_status": ExecutionStatus.SUCCESS,
            "evidence_availability": availability,
            "payload": payload,
            "source_record_ids": source_record_ids or [],
            "retrieval_status": retrieval_status,
            "policy_resolution_status": policy_resolution_status,
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
            retrieval_status=retrieval_status,
            policy_resolution_status=policy_resolution_status,
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
        retrieval_status: RetrievalStatus | None = None,
    ) -> ToolResult[PayloadT]:
        execution_status = (
            ExecutionStatus.RETRYABLE_ERROR if retryable else ExecutionStatus.NON_RETRYABLE_ERROR
        )
        normalized = {
            "execution_status": execution_status,
            "evidence_availability": EvidenceAvailability.UNAVAILABLE,
            "error_code": error_code,
            "retrieval_status": retrieval_status,
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
            retrieval_status=retrieval_status,
            policy_resolution_status=None,
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

"""Pure evidence truth tables shared by the Agent and strong Workflow."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from after_sales_agent.domain.state import (
    DeliveryProofStatus,
    EvidenceAvailability,
    EvidenceGateDecision,
    ExecutionStatus,
    IssueType,
    OrderStatus,
    PolicyResolutionStatus,
    RetrievalStatus,
    TriageIntent,
)
from after_sales_agent.tools.contracts import (
    CarrierServiceAlertsPayload,
    DeliveryProofPayload,
    ExistingLogisticsTicketsPayload,
    LogisticsTimelinePayload,
    OrderContextPayload,
    PolicySearchPayload,
    ToolResult,
)


class GateModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceGateResult(GateModel):
    """A normal gate decision or an explicit issue revision, never both."""

    decision: EvidenceGateDecision | None = None
    reason_code: str = Field(min_length=1)
    revised_issue_type: TriageIntent | None = None
    critical_result_hashes: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def exactly_one_outcome(self) -> EvidenceGateResult:
        if (self.decision is None) == (self.revised_issue_type is None):
            raise ValueError("gate result requires exactly one decision or issue revision")
        return self

    @property
    def proposal_allowed(self) -> bool:
        return self.decision is EvidenceGateDecision.PROPOSE_TICKET


class CommonGateFacts(GateModel):
    authorization_valid: bool = True
    budget_exceeded: bool = False
    evidence_scope_matches_case: bool = True
    requested_action_supported: bool = True
    structural_conflict: bool = False
    directed_refresh_completed: bool = False
    critical_retry_exhausted: bool = False
    target_shipment_id: str | None = None


class SignedNotReceivedEvidence(CommonGateFacts):
    order_context: ToolResult[OrderContextPayload]
    timeline: ToolResult[LogisticsTimelinePayload]
    delivery_proof: ToolResult[DeliveryProofPayload]
    existing_tickets: ToolResult[ExistingLogisticsTicketsPayload]
    policy: ToolResult[PolicySearchPayload]
    customer_still_reports_missing: bool = True
    reception_locations_checked: bool = False
    delivery_location_conflict: bool = False


class StalledTrackingEvidence(CommonGateFacts):
    order_context: ToolResult[OrderContextPayload]
    timeline: ToolResult[LogisticsTimelinePayload]
    existing_tickets: ToolResult[ExistingLogisticsTicketsPayload]
    policy: ToolResult[PolicySearchPayload]
    carrier_alerts: ToolResult[CarrierServiceAlertsPayload] | None = None


def _result(
    decision: EvidenceGateDecision,
    reason_code: str,
    results: dict[str, ToolResult[Any]] | None = None,
) -> EvidenceGateResult:
    return EvidenceGateResult(
        decision=decision,
        reason_code=reason_code,
        critical_result_hashes={key: value.result_hash for key, value in (results or {}).items()},
    )


def _common_guard(facts: CommonGateFacts) -> EvidenceGateResult | None:
    if not facts.authorization_valid:
        return _result(
            EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT,
            "AUTHORIZATION_NOT_VALID",
        )
    if facts.budget_exceeded:
        return _result(
            EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT,
            "INVESTIGATION_BUDGET_EXCEEDED",
        )
    if not facts.evidence_scope_matches_case:
        return _result(
            EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT,
            "EVIDENCE_SCOPE_MISMATCH",
        )
    if not facts.requested_action_supported:
        return _result(
            EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT,
            "UNSUPPORTED_ACTION_RECOMMENDATION",
        )
    if facts.structural_conflict:
        if facts.directed_refresh_completed:
            return _result(
                EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT,
                "PERSISTENT_STRUCTURAL_CONFLICT",
            )
        return _result(
            EvidenceGateDecision.RETRY_LATER,
            "STRUCTURAL_CONFLICT_REQUIRES_REFRESH",
        )
    return None


def _unavailable_result(
    named_results: dict[str, ToolResult[Any]],
    *,
    retry_exhausted: bool,
) -> EvidenceGateResult | None:
    unavailable = {
        key: result
        for key, result in named_results.items()
        if result.evidence_availability is EvidenceAvailability.UNAVAILABLE
        or result.execution_status is not ExecutionStatus.SUCCESS
    }
    if not unavailable:
        return None
    can_retry = any(result.retryable for result in unavailable.values()) and not retry_exhausted
    return _result(
        (
            EvidenceGateDecision.RETRY_LATER
            if can_retry
            else EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT
        ),
        "CRITICAL_EVIDENCE_UNAVAILABLE" if can_retry else "CRITICAL_EVIDENCE_UNAVAILABLE_FINAL",
        unavailable,
    )


def _active_ticket(
    result: ToolResult[ExistingLogisticsTicketsPayload],
) -> bool:
    return bool(
        result.payload
        and (result.payload.active_tickets or result.payload.existing_investigations)
    )


def _target_shipment(order: OrderContextPayload, target_shipment_id: str | None) -> Any | None:
    if target_shipment_id is None:
        return None
    return next(
        (item for item in order.shipments if item.shipment_id == target_shipment_id),
        None,
    )


def _target_timeline(
    timeline: LogisticsTimelinePayload,
    target_shipment_id: str | None,
) -> Any | None:
    if target_shipment_id is None:
        return timeline
    return next(
        (
            item
            for item in timeline.shipment_timelines
            if item.shipment_id == target_shipment_id
        ),
        None,
    )


def _has_structural_timeline_conflict(
    *,
    order: OrderContextPayload,
    timeline: LogisticsTimelinePayload,
    observed_at: datetime,
    target_shipment_id: str | None,
) -> bool:
    """Detect a server-side status/time contradiction before using the facts."""

    target = _target_shipment(order, target_shipment_id)
    if target is None and target_shipment_id is None and len(order.shipments) == 1:
        target = order.shipments[0]
    if target is None:
        # A multi-package order without a selected shipment is ambiguous, not a
        # structural conflict; target selection remains a separate gate.
        return False
    scoped = _target_timeline(timeline, target_shipment_id)
    if scoped is None or not scoped.events:
        return False
    if any(event.occurred_at > observed_at for event in scoped.events):
        return True
    ordered_events = sorted(scoped.events, key=lambda event: event.occurred_at)
    statuses = [event.status for event in ordered_events]
    active_states = {"shipped", "in_transit", "out_for_delivery", "stalled"}
    if target.shipment_status in active_states and "delivered" in statuses:
        return True
    delivered_indexes = [index for index, status in enumerate(statuses) if status == "delivered"]
    if target.shipment_status == OrderStatus.DELIVERED.value and delivered_indexes:
        first_delivered = delivered_indexes[0]
        if any(status != "delivered" for status in statuses[first_delivered + 1 :]):
            return True
    return False


def _policy_guard(
    *,
    policy: ToolResult[PolicySearchPayload],
    order: OrderContextPayload,
    issue_type: IssueType,
    named_results: dict[str, ToolResult[Any]],
) -> EvidenceGateResult | None:
    """Permit only canonical, current and scope-matched policy facts into the Gate."""

    payload = policy.payload
    if payload is None:
        return _result(
            EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT,
            "POLICY_RESOLUTION_MISSING",
            named_results,
        )
    if payload.retrieval_status is RetrievalStatus.NO_HIT:
        return _result(
            EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT,
            "POLICY_RETRIEVAL_NO_HIT",
            named_results,
        )
    if payload.retrieval_status is not RetrievalStatus.HIT:
        return _result(
            EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT,
            "POLICY_RETRIEVAL_NOT_VERIFIABLE",
            named_results,
        )
    if payload.policy_resolution_status is PolicyResolutionStatus.NOT_APPLICABLE:
        return _result(
            EvidenceGateDecision.COMPLETE_NO_ACTION,
            "POLICY_NOT_APPLICABLE",
            named_results,
        )
    if payload.policy_resolution_status is PolicyResolutionStatus.VERSION_CONFLICT:
        return _result(
            EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT,
            "POLICY_VERSION_CONFLICT",
            named_results,
        )
    if not payload.verified_for_gate or payload.policy_fact_snapshot is None:
        return _result(
            EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT,
            "POLICY_CANONICAL_VALIDATION_FAILED",
            named_results,
        )
    facts = payload.policy_fact_snapshot
    if (
        facts.issue_type is not issue_type
        or facts.service_level != order.service_level
        or facts.region != order.region
        or facts.policy_version != (payload.citation.policy_version if payload.citation else None)
        or facts.clause_id != (payload.citation.clause_id if payload.citation else None)
        or facts.source_hash != (payload.citation.source_hash if payload.citation else None)
    ):
        return _result(
            EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT,
            "POLICY_SCOPE_OR_CITATION_MISMATCH",
            named_results,
        )
    if payload.evaluated_at < facts.effective_from or (
        facts.effective_to is not None and payload.evaluated_at >= facts.effective_to
    ):
        return _result(
            EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT,
            "POLICY_EFFECTIVE_WINDOW_INVALID",
            named_results,
        )
    required_codes = (
        {"order_delivered", "timeline", "delivery_proof", "no_active_ticket"}
        if issue_type is IssueType.SIGNED_NOT_RECEIVED
        else {"order_shipped", "timeline", "no_active_ticket"}
    )
    if not required_codes.issubset(set(facts.required_evidence_codes)):
        return _result(
            EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT,
            "POLICY_REQUIRED_EVIDENCE_MISMATCH",
            named_results,
        )
    return None


def evaluate_signed_not_received(
    facts: SignedNotReceivedEvidence,
) -> EvidenceGateResult:
    """Evaluate the signed-but-missing truth table without model judgment."""

    if common := _common_guard(facts):
        return common

    if unavailable := _unavailable_result(
        {"order_context": facts.order_context},
        retry_exhausted=facts.critical_retry_exhausted,
    ):
        return unavailable
    order = facts.order_context.payload
    if order is None:
        return _result(
            EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT,
            "ORDER_CONTEXT_ABSENT_AFTER_AUTHORIZATION",
            {"order_context": facts.order_context},
        )
    target = _target_shipment(order, facts.target_shipment_id)
    if facts.target_shipment_id is not None and target is None:
        return _result(
            EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT,
            "TARGET_SHIPMENT_NOT_FOUND",
            {"order_context": facts.order_context},
        )
    target_delivered = target is not None and target.shipment_status == OrderStatus.DELIVERED.value
    if target is None and order.order_status is OrderStatus.DELIVERED:
        target_delivered = True
    if not target_delivered:
        target_status = target.shipment_status if target is not None else order.order_status.value
        revised = (
            TriageIntent.STALLED_TRACKING
            if target_status in {
                OrderStatus.SHIPPED.value,
                "in_transit",
                "out_for_delivery",
                "stalled",
            }
            else TriageIntent.OTHER_LOGISTICS
        )
        return EvidenceGateResult(
            decision=None,
            revised_issue_type=revised,
            reason_code="REPORTED_ISSUE_DOES_NOT_MATCH_ORDER_STATE",
            critical_result_hashes={"order_context": facts.order_context.result_hash},
        )

    # A completed active-ticket observation resolves duplicate action handling.
    if unavailable := _unavailable_result(
        {"existing_tickets": facts.existing_tickets},
        retry_exhausted=facts.critical_retry_exhausted,
    ):
        return unavailable
    if _active_ticket(facts.existing_tickets):
        return _result(
            EvidenceGateDecision.COMPLETE_NO_ACTION,
            "ACTIVE_LOGISTICS_TICKET_EXISTS",
            {"existing_tickets": facts.existing_tickets},
        )

    pre_pod_critical: dict[str, ToolResult[Any]] = {
        "order_context": facts.order_context,
        "timeline": facts.timeline,
        "existing_tickets": facts.existing_tickets,
        "policy": facts.policy,
    }
    if unavailable := _unavailable_result(
        pre_pod_critical,
        retry_exhausted=facts.critical_retry_exhausted,
    ):
        return unavailable
    if facts.timeline.payload is None or not facts.timeline.payload.events:
        return _result(
            (
                EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT
                if facts.directed_refresh_completed
                else EvidenceGateDecision.RETRY_LATER
            ),
            "DELIVERED_WITHOUT_TIMELINE_EVIDENCE",
            pre_pod_critical,
        )
    scoped_timeline = _target_timeline(facts.timeline.payload, facts.target_shipment_id)
    if scoped_timeline is None or not scoped_timeline.events:
        return _result(
            (
                EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT
                if facts.directed_refresh_completed
                else EvidenceGateDecision.RETRY_LATER
            ),
            "TARGET_TIMELINE_ABSENT",
            pre_pod_critical,
        )
    if policy_guard := _policy_guard(
        policy=facts.policy,
        order=order,
        issue_type=IssueType.SIGNED_NOT_RECEIVED,
        named_results=pre_pod_critical,
    ):
        return policy_guard
    policy_facts = facts.policy.payload.policy_fact_snapshot if facts.policy.payload else None
    if policy_facts is None:
        return _result(
            EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT,
            "POLICY_CANONICAL_VALIDATION_FAILED",
            pre_pod_critical,
        )
    if not policy_facts.eligible:
        return _result(
            EvidenceGateDecision.COMPLETE_NO_ACTION,
            "POLICY_NOT_ELIGIBLE",
            pre_pod_critical,
        )
    if not facts.customer_still_reports_missing:
        return _result(
            EvidenceGateDecision.COMPLETE_NO_ACTION,
            "CUSTOMER_REPORT_RESOLVED",
            pre_pod_critical,
        )

    critical: dict[str, ToolResult[Any]] = {
        **pre_pod_critical,
        "delivery_proof": facts.delivery_proof,
    }
    if unavailable := _unavailable_result(
        {"delivery_proof": facts.delivery_proof},
        retry_exhausted=facts.critical_retry_exhausted,
    ):
        return unavailable

    pod = facts.delivery_proof.payload
    if facts.delivery_location_conflict:
        return _result(
            EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT,
            "DELIVERY_EVIDENCE_CONFLICT",
            critical,
        )
    reception_recipient_types = {
        "self",
        "family",
        "front_desk",
        "neighbor",
        "collection_point",
        "locker",
    }
    if (
        pod is not None
        and pod.pod_status is DeliveryProofStatus.FOUND
        and pod.recipient_type in reception_recipient_types
        and not facts.reception_locations_checked
    ):
        return _result(
            EvidenceGateDecision.REQUEST_BUSINESS_CLARIFICATION,
            "CHECK_RECEPTION_LOCATION",
            critical,
        )
    return _result(
        EvidenceGateDecision.PROPOSE_TICKET,
        "SIGNED_NOT_RECEIVED_EVIDENCE_COMPLETE",
        critical,
    )


def evaluate_stalled_tracking(facts: StalledTrackingEvidence) -> EvidenceGateResult:
    """Evaluate the stalled-tracking SLA truth table using scenario-clock data."""

    if common := _common_guard(facts):
        return common
    decision_inputs: dict[str, ToolResult[Any]] = {
        "order_context": facts.order_context,
        "timeline": facts.timeline,
        "policy": facts.policy,
    }
    if unavailable := _unavailable_result(
        decision_inputs,
        retry_exhausted=facts.critical_retry_exhausted,
    ):
        return unavailable
    order = facts.order_context.payload
    if order is None:
        return _result(
            EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT,
            "ORDER_CONTEXT_ABSENT_AFTER_AUTHORIZATION",
            decision_inputs,
        )
    target = _target_shipment(order, facts.target_shipment_id)
    if facts.target_shipment_id is not None and target is None:
        return _result(
            EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT,
            "TARGET_SHIPMENT_NOT_FOUND",
            decision_inputs,
        )
    if target is not None and target.shipment_status == OrderStatus.DELIVERED.value:
        return EvidenceGateResult(
            decision=None,
            revised_issue_type=TriageIntent.SIGNED_NOT_RECEIVED,
            reason_code="REPORTED_ISSUE_DOES_NOT_MATCH_SHIPMENT_STATE",
            critical_result_hashes={"order_context": facts.order_context.result_hash},
        )
    active_shipment_states = {"shipped", "in_transit", "out_for_delivery", "stalled"}
    if target is None and order.order_status is not OrderStatus.SHIPPED:
        return _result(
            EvidenceGateDecision.COMPLETE_NO_ACTION,
            "ORDER_NOT_IN_TRANSIT",
            decision_inputs,
        )
    if target is not None and target.shipment_status not in active_shipment_states:
        return _result(
            EvidenceGateDecision.COMPLETE_NO_ACTION,
            "SHIPMENT_NOT_IN_TRANSIT",
            decision_inputs,
        )
    timeline = facts.timeline.payload
    if timeline is None or timeline.hours_since_last_update is None:
        return _result(
            (
                EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT
                if facts.directed_refresh_completed
                else EvidenceGateDecision.RETRY_LATER
            ),
            "TRACKING_TIMELINE_ABSENT",
            decision_inputs,
        )
    if policy_guard := _policy_guard(
        policy=facts.policy,
        order=order,
        issue_type=IssueType.STALLED_TRACKING,
        named_results=decision_inputs,
    ):
        return policy_guard
    policy = facts.policy.payload.policy_fact_snapshot if facts.policy.payload else None
    if policy is None:
        return _result(
            EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT,
            "POLICY_CANONICAL_VALIDATION_FAILED",
            decision_inputs,
        )
    if not policy.eligible:
        return _result(
            EvidenceGateDecision.COMPLETE_NO_ACTION,
            "POLICY_NOT_ELIGIBLE",
            decision_inputs,
        )
    threshold = policy.stalled_after_hours
    if threshold is None:
        return _result(
            EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT,
            "STALL_THRESHOLD_ABSENT",
            decision_inputs,
        )
    scoped_timeline = _target_timeline(timeline, facts.target_shipment_id)
    if scoped_timeline is None or scoped_timeline.hours_since_last_update is None:
        return _result(
            (
                EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT
                if facts.directed_refresh_completed
                else EvidenceGateDecision.RETRY_LATER
            ),
            "TARGET_TIMELINE_ABSENT",
            decision_inputs,
        )
    if _has_structural_timeline_conflict(
        order=order,
        timeline=timeline,
        observed_at=facts.timeline.observed_at,
        target_shipment_id=facts.target_shipment_id,
    ):
        return _result(
            EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT,
            "PERSISTENT_STRUCTURAL_CONFLICT",
            decision_inputs,
        )
    if scoped_timeline.hours_since_last_update <= threshold:
        return _result(
            EvidenceGateDecision.COMPLETE_NO_ACTION,
            "WITHIN_TRACKING_SLA",
            decision_inputs,
        )

    if facts.carrier_alerts is not None:
        if unavailable := _unavailable_result(
            {"carrier_alerts": facts.carrier_alerts},
            retry_exhausted=facts.critical_retry_exhausted,
        ):
            return unavailable

    critical: dict[str, ToolResult[Any]] = {
        **decision_inputs,
        "existing_tickets": facts.existing_tickets,
    }
    if facts.carrier_alerts is not None:
        critical["carrier_alerts"] = facts.carrier_alerts
    if unavailable := _unavailable_result(
        {"existing_tickets": facts.existing_tickets},
        retry_exhausted=facts.critical_retry_exhausted,
    ):
        return unavailable
    if _active_ticket(facts.existing_tickets):
        return _result(
            EvidenceGateDecision.COMPLETE_NO_ACTION,
            "ACTIVE_LOGISTICS_TICKET_EXISTS",
            critical,
        )
    if facts.carrier_alerts is not None and facts.carrier_alerts.payload is not None:
        active_alerts = [
            alert
            for alert in facts.carrier_alerts.payload.alerts
            if alert.is_active_at(facts.carrier_alerts.observed_at)
        ]
        if active_alerts:
            return _result(
                EvidenceGateDecision.COMPLETE_NO_ACTION,
                "ACTIVE_CARRIER_RECOVERY_WINDOW",
                {**critical, "carrier_alerts": facts.carrier_alerts},
            )
    return _result(
        EvidenceGateDecision.PROPOSE_TICKET,
        "STALLED_TRACKING_EVIDENCE_COMPLETE",
        critical,
    )


def evaluate_evidence_gate(
    issue_type: IssueType,
    facts: SignedNotReceivedEvidence | StalledTrackingEvidence,
) -> EvidenceGateResult:
    if issue_type is IssueType.SIGNED_NOT_RECEIVED:
        if not isinstance(facts, SignedNotReceivedEvidence):
            raise TypeError("signed_not_received requires SignedNotReceivedEvidence")
        return evaluate_signed_not_received(facts)
    if not isinstance(facts, StalledTrackingEvidence):
        raise TypeError("stalled_tracking requires StalledTrackingEvidence")
    return evaluate_stalled_tracking(facts)

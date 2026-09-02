"""Deterministic routing between lightweight triage and Case creation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from after_sales_agent.domain.models import TriageResult
from after_sales_agent.domain.state import IssueType, TriageIntent
from after_sales_agent.fixtures.catalog import FixtureStore
from after_sales_agent.policy.authorization import AuthorizationError, authorize_order


class PolicyRoute(StrEnum):
    SUPPORTED_LOGISTICS = "supported_logistics"
    STANDARD_REPLY = "standard_reply"
    AMBIGUOUS = "ambiguous"
    OUT_OF_SCOPE = "out_of_scope"
    PROHIBITED = "prohibited"
    UNAUTHORIZED = "unauthorized"


@dataclass(frozen=True, slots=True)
class BlockedFragment:
    category: str
    order_id: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {"category": self.category, "order_id": self.order_id}


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    route: PolicyRoute
    supported: bool
    canonical_issue_type: IssueType | None
    authorized_order_id: str | None
    blocked_fragments: tuple[BlockedFragment, ...]
    risk_flags: tuple[str, ...]
    reason_code: str


def route_triage(
    *,
    customer_id: str,
    triage: TriageResult,
    fixtures: FixtureStore,
) -> PolicyDecision:
    """Preserve one valid authorized request while blocking unsafe fragments."""

    blocked: list[BlockedFragment] = []
    if "instruction_override_attempt" in triage.risk_flags:
        blocked.append(BlockedFragment(category="instruction_override_attempt"))
    if "prohibited_action_request" in triage.risk_flags:
        blocked.append(BlockedFragment(category="prohibited_action_request"))
    if "unnecessary_personal_data" in triage.risk_flags:
        blocked.append(BlockedFragment(category="unnecessary_personal_data"))

    authorized_order_ids: list[str] = []
    for order_id in triage.order_ids_mentioned:
        try:
            authorize_order(customer_id, order_id, fixtures)
        except AuthorizationError:
            blocked.append(BlockedFragment(category="unauthorized_order_access", order_id=order_id))
        else:
            authorized_order_ids.append(order_id)

    if triage.intent in {
        TriageIntent.SIGNED_NOT_RECEIVED,
        TriageIntent.STALLED_TRACKING,
    }:
        if len(authorized_order_ids) == 1:
            return PolicyDecision(
                route=PolicyRoute.SUPPORTED_LOGISTICS,
                supported=True,
                canonical_issue_type=IssueType(triage.intent.value),
                authorized_order_id=authorized_order_ids[0],
                blocked_fragments=tuple(blocked),
                risk_flags=tuple(triage.risk_flags),
                reason_code="AUTHORIZED_SUPPORTED_ISSUE",
            )
        if len(authorized_order_ids) > 1:
            return PolicyDecision(
                route=PolicyRoute.AMBIGUOUS,
                supported=False,
                canonical_issue_type=None,
                authorized_order_id=None,
                blocked_fragments=tuple(blocked),
                risk_flags=tuple(triage.risk_flags),
                reason_code="MULTIPLE_AUTHORIZED_ORDERS_REQUIRE_SELECTION",
            )
        if triage.order_ids_mentioned:
            return PolicyDecision(
                route=PolicyRoute.UNAUTHORIZED,
                supported=False,
                canonical_issue_type=None,
                authorized_order_id=None,
                blocked_fragments=tuple(blocked),
                risk_flags=tuple(triage.risk_flags),
                reason_code="NO_AUTHORIZED_ORDER_IN_REQUEST",
            )
        return PolicyDecision(
            route=PolicyRoute.AMBIGUOUS,
            supported=False,
            canonical_issue_type=None,
            authorized_order_id=None,
            blocked_fragments=tuple(blocked),
            risk_flags=tuple(triage.risk_flags),
            reason_code="ORDER_ID_REQUIRED",
        )

    standard_reply_reasons = {
        TriageIntent.CAPABILITY_HELP: "CAPABILITY_HELP_STANDARD_REPLY",
        TriageIntent.ORDER_ID_HELP: "ORDER_ID_HELP_STANDARD_REPLY",
        TriageIntent.TRACKING_STATUS_QUERY: "TRACKING_STATUS_REQUIRES_CLARIFICATION",
        TriageIntent.DELIVERY_ETA_INFO: "DELIVERY_ETA_STANDARD_REPLY",
        TriageIntent.CHANGE_DELIVERY_INFO: "CHANGE_DELIVERY_INFO_STANDARD_REPLY",
        TriageIntent.REFUND_RETURN_INFO: "REFUND_RETURN_INFO_STANDARD_REPLY",
        TriageIntent.HUMAN_SUPPORT_REQUEST: "HUMAN_SUPPORT_REQUESTED",
        TriageIntent.THANKS_CLOSE: "THANKS_CLOSE_STANDARD_REPLY",
    }
    if triage.intent in standard_reply_reasons:
        if triage.intent is TriageIntent.TRACKING_STATUS_QUERY:
            if len(authorized_order_ids) > 1:
                return PolicyDecision(
                    route=PolicyRoute.AMBIGUOUS,
                    supported=False,
                    canonical_issue_type=None,
                    authorized_order_id=None,
                    blocked_fragments=tuple(blocked),
                    risk_flags=tuple(triage.risk_flags),
                    reason_code="MULTIPLE_AUTHORIZED_ORDERS_REQUIRE_SELECTION",
                )
            if triage.order_ids_mentioned and not authorized_order_ids:
                return PolicyDecision(
                    route=PolicyRoute.UNAUTHORIZED,
                    supported=False,
                    canonical_issue_type=None,
                    authorized_order_id=None,
                    blocked_fragments=tuple(blocked),
                    risk_flags=tuple(triage.risk_flags),
                    reason_code="NO_AUTHORIZED_ORDER_IN_REQUEST",
                )
        return PolicyDecision(
            route=PolicyRoute.STANDARD_REPLY,
            supported=False,
            canonical_issue_type=None,
            authorized_order_id=(
                authorized_order_ids[0]
                if triage.intent is TriageIntent.TRACKING_STATUS_QUERY
                and len(authorized_order_ids) == 1
                else None
            ),
            blocked_fragments=tuple(blocked),
            risk_flags=tuple(triage.risk_flags),
            reason_code=standard_reply_reasons[triage.intent],
        )

    route_by_intent = {
        TriageIntent.AMBIGUOUS: PolicyRoute.AMBIGUOUS,
        TriageIntent.OTHER_LOGISTICS: PolicyRoute.AMBIGUOUS,
        TriageIntent.OUT_OF_SCOPE: PolicyRoute.OUT_OF_SCOPE,
        TriageIntent.PROHIBITED: PolicyRoute.PROHIBITED,
    }
    reason_by_intent = {
        TriageIntent.AMBIGUOUS: "ISSUE_REQUIRES_CLARIFICATION",
        TriageIntent.OTHER_LOGISTICS: "UNSUPPORTED_LOGISTICS_REQUIRES_CLARIFICATION",
        TriageIntent.OUT_OF_SCOPE: "OUT_OF_SCOPE",
        TriageIntent.PROHIBITED: "PROHIBITED_ONLY_REQUEST",
    }
    return PolicyDecision(
        route=route_by_intent[triage.intent],
        supported=False,
        canonical_issue_type=None,
        authorized_order_id=None,
        blocked_fragments=tuple(blocked),
        risk_flags=tuple(triage.risk_flags),
        reason_code=reason_by_intent[triage.intent],
    )

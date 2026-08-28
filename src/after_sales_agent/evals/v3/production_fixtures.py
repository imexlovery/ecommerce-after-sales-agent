"""Explicit fictional fixture profiles for the V3 production input package."""

from __future__ import annotations

from datetime import datetime, timedelta

from after_sales_agent.domain.state import ExecutionStatus, IssueType, OrderStatus
from after_sales_agent.fixtures.catalog import (
    DeliveryProofFixture,
    FixtureFault,
    FixtureStore,
    OrderFixture,
)
from after_sales_agent.tools.contracts import CarrierAlert, LogisticsTicket, TimelineEvent


def fixture_store_for_case(
    *,
    profile: str,
    customer_id: str,
    order_id: str,
    issue_type: IssueType,
    evaluated_at: datetime,
    fault_seed: str,
) -> FixtureStore:
    """Build one isolated fixture catalog from a committed profile name.

    Profiles only alter fictional source observations and deterministic fault
    seeds.  They do not alter the shared Agent/Workflow runtime or its limits.
    """

    is_stalled = issue_type is IssueType.STALLED_TRACKING
    economy = profile in {"snr-policy-ineligible", "stall-policy-no-hit"}
    conflict = profile == "stall-policy-conflict"
    service_level = "economy" if economy else ("conflict_test" if conflict else "standard")
    order_status = OrderStatus.SHIPPED if is_stalled else OrderStatus.DELIVERED
    if profile == "snr-order-not-delivered":
        order_status = OrderStatus.SHIPPED
    delivered_at = (
        None
        if order_status is not OrderStatus.DELIVERED
        else evaluated_at - timedelta(days=6)
    )
    shipped_at = evaluated_at - timedelta(days=8)
    order = OrderFixture(
        order_id=order_id,
        customer_id=customer_id,
        order_status=order_status,
        tracking_number=f"TRK-SYN-{order_id.removeprefix('ORD-')}",
        service_level=service_level,
        region="conflict-east" if conflict else "cn-east",
        shipped_at=shipped_at,
        delivered_at=delivered_at,
        source_revision=f"{order_id.lower()}-v3-fixture",
    )

    if profile == "stall-within-sla":
        last_update = evaluated_at - timedelta(hours=24)
    elif profile == "stall-severe-stall":
        last_update = evaluated_at - timedelta(hours=120)
    else:
        last_update = evaluated_at - timedelta(hours=72 if is_stalled else 6)
    timelines = [
        TimelineEvent(
            event_id=f"evt-v3-{order_id.lower()}-1",
            status="in_transit" if order_status is OrderStatus.SHIPPED else "delivered",
            occurred_at=last_update,
            location="合成评估分拨中心",
        )
    ]
    if order_status is OrderStatus.DELIVERED:
        timelines.append(
            TimelineEvent(
                event_id=f"evt-v3-{order_id.lower()}-2",
                status="delivered",
                occurred_at=delivered_at or last_update,
                location="合成评估配送站",
            )
        )

    proofs: dict[str, DeliveryProofFixture] = {}
    if profile in {"snr-pod-reception-proof", "snr-pod-nonreception-proof"}:
        proofs[order_id] = DeliveryProofFixture(
            proof_id=f"pod-v3-{order_id.lower()}",
            order_id=order_id,
            recipient_type=(
                "recipient_other"
                if profile == "snr-pod-reception-proof"
                else "recipient_customer"
            ),
            signed_at=delivered_at,
            note="仅用于虚构评估的签收事实。",
        )

    tickets: list[LogisticsTicket] = []
    if profile in {"snr-active-ticket", "stall-active-ticket"}:
        tickets.append(
            LogisticsTicket(
                ticket_id=f"ticket-v3-{order_id.lower()}-{issue_type.value}",
                order_id=order_id,
                issue_type=issue_type,
                ticket_status="investigating",
                created_at=evaluated_at - timedelta(hours=2),
            )
        )

    faults: dict[tuple[str, str, int], FixtureFault] = {}
    if profile in {"snr-policy-unavailable", "stall-policy-unavailable"}:
        for attempt in (1, 2):
            faults[(fault_seed, "search_after_sales_policy", attempt)] = FixtureFault(
                execution_status=ExecutionStatus.RETRYABLE_ERROR,
                error_code="V3_POLICY_UNAVAILABLE",
            )
    elif profile == "snr-pod-exact-retry":
        faults[(fault_seed, "get_delivery_proof", 1)] = FixtureFault(
            execution_status=ExecutionStatus.RETRYABLE_ERROR,
            error_code="V3_POD_RETRY_ONCE",
        )
    elif profile == "snr-pod-persistent-failure":
        for attempt in (1, 2):
            faults[(fault_seed, "get_delivery_proof", attempt)] = FixtureFault(
                execution_status=ExecutionStatus.RETRYABLE_ERROR,
                error_code="V3_POD_PERSISTENT_FAILURE",
            )

    alerts = {}
    if is_stalled and profile in {"stall-severe-stall", "stall-no-active-ticket"}:
        alerts[order_id] = [
            CarrierAlert(
                alert_id=f"alert-v3-{order_id.lower()}",
                code="V3_SYNTHETIC_DELAY",
                description="仅用于虚构评估的承运商延迟观察。",
                active_from=evaluated_at - timedelta(days=2),
                active_until=None,
            )
        ]
    return FixtureStore(
        fixture_version="fixture-v1",
        orders=[order],
        timelines={order_id: timelines},
        delivery_proofs=proofs,
        carrier_alerts=alerts,
        tickets=tickets,
        faults=faults,
    )


__all__ = ["fixture_store_for_case"]

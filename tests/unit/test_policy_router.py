from __future__ import annotations

from after_sales_agent.application.policy_router import PolicyRoute, route_triage
from after_sales_agent.domain.models import TriageResult
from after_sales_agent.domain.state import IssueType, TriageIntent
from after_sales_agent.fixtures.catalog import default_fixture_store


def test_valid_authorized_request_survives_override_and_foreign_order_fragment() -> None:
    decision = route_triage(
        customer_id="customer_a",
        triage=TriageResult(
            intent=TriageIntent.SIGNED_NOT_RECEIVED,
            risk_flags=["instruction_override_attempt", "multiple_order_ids"],
            order_ids_mentioned=["ORD-002", "ORD-001"],
            confidence=0.98,
        ),
        fixtures=default_fixture_store(),
    )

    assert decision.route is PolicyRoute.SUPPORTED_LOGISTICS
    assert decision.authorized_order_id == "ORD-001"
    assert decision.canonical_issue_type is IssueType.SIGNED_NOT_RECEIVED
    assert [item.category for item in decision.blocked_fragments] == [
        "instruction_override_attempt",
        "unauthorized_order_access",
    ]


def test_foreign_order_only_never_creates_supported_case() -> None:
    decision = route_triage(
        customer_id="customer_a",
        triage=TriageResult(
            intent=TriageIntent.STALLED_TRACKING,
            risk_flags=[],
            order_ids_mentioned=["ORD-002"],
            confidence=0.99,
        ),
        fixtures=default_fixture_store(),
    )

    assert decision.route is PolicyRoute.UNAUTHORIZED
    assert decision.supported is False
    assert decision.authorized_order_id is None


def test_supported_issue_without_order_requires_clarification() -> None:
    decision = route_triage(
        customer_id="customer_a",
        triage=TriageResult(
            intent=TriageIntent.SIGNED_NOT_RECEIVED,
            risk_flags=[],
            order_ids_mentioned=[],
            confidence=0.9,
        ),
        fixtures=default_fixture_store(),
    )

    assert decision.route is PolicyRoute.AMBIGUOUS
    assert decision.reason_code == "ORDER_ID_REQUIRED"

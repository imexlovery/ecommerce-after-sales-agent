from __future__ import annotations

from after_sales_agent.application.policy_router import PolicyRoute, route_triage
from after_sales_agent.domain.models import TriageResult
from after_sales_agent.domain.state import IssueType, TriageIntent
from after_sales_agent.fixtures.catalog import legacy_fixture_store as default_fixture_store


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


def test_common_business_topic_routes_to_standard_reply_without_case() -> None:
    decision = route_triage(
        customer_id="customer_a",
        triage=TriageResult(
            intent=TriageIntent.CAPABILITY_HELP,
            risk_flags=[],
            order_ids_mentioned=[],
            confidence=0.96,
        ),
        fixtures=default_fixture_store(),
    )

    assert decision.route is PolicyRoute.STANDARD_REPLY
    assert decision.supported is False
    assert decision.canonical_issue_type is None
    assert decision.authorized_order_id is None
    assert decision.reason_code == "CAPABILITY_HELP_STANDARD_REPLY"


def test_tracking_status_query_uses_only_an_authorized_order_for_reply_context() -> None:
    decision = route_triage(
        customer_id="customer_a",
        triage=TriageResult(
            intent=TriageIntent.TRACKING_STATUS_QUERY,
            risk_flags=[],
            order_ids_mentioned=["ORD-001"],
            confidence=0.94,
        ),
        fixtures=default_fixture_store(),
    )

    assert decision.route is PolicyRoute.STANDARD_REPLY
    assert decision.supported is False
    assert decision.authorized_order_id == "ORD-001"
    assert decision.reason_code == "TRACKING_STATUS_REQUIRES_CLARIFICATION"


def test_tracking_status_query_does_not_echo_a_foreign_order() -> None:
    decision = route_triage(
        customer_id="customer_a",
        triage=TriageResult(
            intent=TriageIntent.TRACKING_STATUS_QUERY,
            risk_flags=[],
            order_ids_mentioned=["ORD-002"],
            confidence=0.94,
        ),
        fixtures=default_fixture_store(),
    )

    assert decision.route is PolicyRoute.UNAUTHORIZED
    assert decision.supported is False
    assert decision.authorized_order_id is None

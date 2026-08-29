from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from after_sales_agent.config import Settings
from after_sales_agent.domain.models import TrustedToolContext
from after_sales_agent.domain.state import (
    EvidenceAvailability,
    EvidenceGateDecision,
    ExecutionStatus,
    IssueType,
    TriageIntent,
)
from after_sales_agent.fixtures.catalog import (
    FixtureFault,
    business_demo_fixture_store,
)
from after_sales_agent.fixtures.catalog import (
    legacy_fixture_store as default_fixture_store,
)
from after_sales_agent.policy.corpus import build_business_demo_policy_corpus
from after_sales_agent.policy.evidence_gate import (
    SignedNotReceivedEvidence,
    StalledTrackingEvidence,
    evaluate_signed_not_received,
    evaluate_stalled_tracking,
)
from after_sales_agent.policy.rag import build_policy_rag
from after_sales_agent.tools.contracts import LogisticsTicket
from after_sales_agent.tools.service import GovernedToolExecutor, SyntheticReadToolCatalog

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _isolate_fake_policy_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLICY_INDEX_ROOT", str(tmp_path / "policy-index"))


def _fake_policy_rag(store=None):
    return build_policy_rag(
        Settings(
            _env_file=None,
            LLM_MODE="mock",
            POLICY_RETRIEVAL_MODE="fake_test",
        ),
        corpus=(
            build_business_demo_policy_corpus(store.business_policy_clauses)
            if store is not None and store.business_policy_clauses
            else None
        ),
    )


def build_executor(
    order_id: str,
    issue_type: IssueType,
    *,
    customer_id: str = "customer_a",
    fault_seed: str = "safe-seed",
    store=None,
    target_shipment_id: str | None = None,
    evaluated_at: datetime = NOW,
) -> GovernedToolExecutor:
    fixture_store = store or default_fixture_store()
    trusted = TrustedToolContext(
        customer_id=customer_id,
        conversation_id="conv-gate",
        case_id=f"case-{order_id}",
        run_id="run-gate",
        authorized_order_id=order_id,
        canonical_issue_type=issue_type,
        fixture_version=fixture_store.fixture_version,
        fault_seed=fault_seed,
        evaluated_at=evaluated_at,
        trace_id="trace-gate",
        target_shipment_id=target_shipment_id,
    )
    return GovernedToolExecutor(
        trusted=trusted,
        catalog=SyntheticReadToolCatalog(fixture_store, _fake_policy_rag(fixture_store)),
    )


def signed_facts(executor: GovernedToolExecutor) -> SignedNotReceivedEvidence:
    order_id = executor.trusted.authorized_order_id
    issue = executor.trusted.canonical_issue_type.value
    return SignedNotReceivedEvidence(
        order_context=executor.execute_result("get_order_context", {"order_id": order_id}),
        timeline=executor.execute_result("get_logistics_timeline", {"order_id": order_id}),
        delivery_proof=executor.execute_result("get_delivery_proof", {"order_id": order_id}),
        existing_tickets=executor.execute_result(
            "get_existing_logistics_tickets",
            {"order_id": order_id, "issue_type": issue},
        ),
        policy=executor.execute_result(
            "search_after_sales_policy",
            {"order_id": order_id, "issue_type": issue},
        ),
    )


def stalled_facts(executor: GovernedToolExecutor) -> StalledTrackingEvidence:
    order_id = executor.trusted.authorized_order_id
    issue = executor.trusted.canonical_issue_type.value
    return StalledTrackingEvidence(
        order_context=executor.execute_result("get_order_context", {"order_id": order_id}),
        timeline=executor.execute_result("get_logistics_timeline", {"order_id": order_id}),
        existing_tickets=executor.execute_result(
            "get_existing_logistics_tickets",
            {"order_id": order_id, "issue_type": issue},
        ),
        policy=executor.execute_result(
            "search_after_sales_policy",
            {"order_id": order_id, "issue_type": issue},
        ),
    )


def test_signed_not_received_accepts_completed_absent_pod_for_proposal() -> None:
    result = evaluate_signed_not_received(
        signed_facts(build_executor("ORD-001", IssueType.SIGNED_NOT_RECEIVED))
    )

    assert result.decision is EvidenceGateDecision.PROPOSE_TICKET
    assert result.proposal_allowed is True
    assert "delivery_proof" in result.critical_result_hashes


def test_gate_rejects_a_policy_fact_snapshot_with_the_wrong_trusted_region() -> None:
    evidence = signed_facts(build_executor("ORD-001", IssueType.SIGNED_NOT_RECEIVED))
    assert evidence.policy.payload is not None
    assert evidence.policy.payload.policy_fact_snapshot is not None
    wrong_region_facts = evidence.policy.payload.policy_fact_snapshot.model_copy(
        update={"region": "cn-west"}
    )
    wrong_region_payload = evidence.policy.payload.model_copy(
        update={
            "policy_fact_snapshot": wrong_region_facts,
            "policy_fact_snapshot_hash": wrong_region_facts.material_snapshot_hash,
        }
    )
    wrong_region_policy = evidence.policy.model_copy(update={"payload": wrong_region_payload})

    result = evaluate_signed_not_received(
        evidence.model_copy(update={"policy": wrong_region_policy})
    )

    assert result.decision is EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT
    assert result.reason_code == "POLICY_SCOPE_OR_CITATION_MISMATCH"


def test_unavailable_existing_ticket_query_blocks_proposal() -> None:
    store = default_fixture_store().with_faults(
        {
            ("ticket-timeout", "get_existing_logistics_tickets", 1): FixtureFault(
                execution_status=ExecutionStatus.RETRYABLE_ERROR,
                error_code="TICKET_QUERY_TIMEOUT",
            )
        }
    )
    facts = signed_facts(
        build_executor(
            "ORD-001",
            IssueType.SIGNED_NOT_RECEIVED,
            fault_seed="ticket-timeout",
            store=store,
        )
    )
    assert facts.existing_tickets.evidence_availability is EvidenceAvailability.UNAVAILABLE

    retry = evaluate_signed_not_received(facts)
    final = evaluate_signed_not_received(
        facts.model_copy(update={"critical_retry_exhausted": True})
    )
    assert retry.decision is EvidenceGateDecision.RETRY_LATER
    assert final.decision is EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT
    assert not retry.proposal_allowed and not final.proposal_allowed


def test_dynamic_ticket_revision_invalidates_absent_cache_and_prevents_duplicate() -> None:
    store = default_fixture_store()
    executor = build_executor("ORD-001", IssueType.SIGNED_NOT_RECEIVED, store=store)
    before = signed_facts(executor)
    assert before.existing_tickets.evidence_availability is EvidenceAvailability.ABSENT

    ticket = LogisticsTicket(
        ticket_id="TKT-SYN-001",
        order_id="ORD-001",
        issue_type=IssueType.SIGNED_NOT_RECEIVED,
        ticket_status="open",
        created_at=NOW,
    )
    assert store.add_ticket(ticket) is ticket
    assert store.add_ticket(ticket) is ticket

    after = signed_facts(executor)
    assert after.existing_tickets.evidence_availability is EvidenceAvailability.PRESENT
    result = evaluate_signed_not_received(after)
    assert result.decision is EvidenceGateDecision.COMPLETE_NO_ACTION
    assert result.reason_code == "ACTIVE_LOGISTICS_TICKET_EXISTS"


def test_stalled_tracking_uses_scenario_clock_and_policy_threshold() -> None:
    stalled = evaluate_stalled_tracking(
        stalled_facts(build_executor("ORD-003", IssueType.STALLED_TRACKING))
    )
    within_sla = evaluate_stalled_tracking(
        stalled_facts(
            build_executor(
                "ORD-002",
                IssueType.STALLED_TRACKING,
                customer_id="customer_b",
            )
        )
    )

    assert stalled.decision is EvidenceGateDecision.PROPOSE_TICKET
    assert within_sla.decision is EvidenceGateDecision.COMPLETE_NO_ACTION
    assert within_sla.reason_code == "WITHIN_TRACKING_SLA"


def test_mismatched_reported_issue_returns_revision_instead_of_a_fake_gate_decision() -> None:
    result = evaluate_signed_not_received(
        signed_facts(build_executor("ORD-003", IssueType.SIGNED_NOT_RECEIVED))
    )

    assert result.decision is None
    assert result.revised_issue_type is TriageIntent.STALLED_TRACKING
    assert result.proposal_allowed is False


def test_persistent_structural_conflict_requires_human_support() -> None:
    facts = stalled_facts(build_executor("ORD-003", IssueType.STALLED_TRACKING))
    result = evaluate_stalled_tracking(
        facts.model_copy(update={"structural_conflict": True, "directed_refresh_completed": True})
    )

    assert result.decision is EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT
    assert result.reason_code == "PERSISTENT_STRUCTURAL_CONFLICT"


def test_business_demo_recipient_locations_all_request_bounded_clarification() -> None:
    cases = (
        ("ORD-010", "customer_i", "SHP-010", "self"),
        ("ORD-007", "customer_f", "SHP-007", "family"),
        ("ORD-008", "customer_g", "SHP-008", "collection_point"),
        ("ORD-009", "customer_h", "SHP-009", "locker"),
    )
    for order_id, customer_id, shipment_id, recipient_type in cases:
        executor = build_executor(
            order_id,
            IssueType.SIGNED_NOT_RECEIVED,
            customer_id=customer_id,
            store=business_demo_fixture_store(),
            target_shipment_id=shipment_id,
            evaluated_at=datetime(2026, 8, 29, 8, tzinfo=UTC),
        )
        facts = signed_facts(executor)
        assert facts.delivery_proof.payload is not None
        assert facts.delivery_proof.payload.recipient_type == recipient_type
        result = evaluate_signed_not_received(facts)
        assert result.decision is EvidenceGateDecision.REQUEST_BUSINESS_CLARIFICATION
        assert result.reason_code == "CHECK_RECEPTION_LOCATION"


def test_business_demo_pod_persistent_unavailable_is_not_absent() -> None:
    store = business_demo_fixture_store().with_faults(
        {
            ("pod-persistent", "get_delivery_proof", 1): FixtureFault(
                execution_status=ExecutionStatus.RETRYABLE_ERROR,
                error_code="SYNTHETIC_POD_UNAVAILABLE",
            ),
            ("pod-persistent", "get_delivery_proof", 2): FixtureFault(
                execution_status=ExecutionStatus.RETRYABLE_ERROR,
                error_code="SYNTHETIC_POD_UNAVAILABLE",
            ),
        }
    )
    executor = build_executor(
        "ORD-001",
        IssueType.SIGNED_NOT_RECEIVED,
        store=store,
        fault_seed="pod-persistent",
        target_shipment_id="SHP-001",
        evaluated_at=datetime(2026, 8, 29, 8, tzinfo=UTC),
    )
    facts = signed_facts(executor)
    second = executor.execute_result("get_delivery_proof", {"order_id": "ORD-001"})
    facts = facts.model_copy(
        update={
            "delivery_proof": second,
            "critical_retry_exhausted": executor.retry_exhausted_for(
                "get_delivery_proof", {"order_id": "ORD-001"}
            ),
        }
    )
    assert second.evidence_availability is EvidenceAvailability.UNAVAILABLE
    assert facts.critical_retry_exhausted is True
    result = evaluate_signed_not_received(facts)
    assert result.decision is EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT
    assert result.reason_code == "CRITICAL_EVIDENCE_UNAVAILABLE_FINAL"


def test_business_demo_timeline_persistent_unavailable_and_status_conflict_fail_closed() -> None:
    business_now = datetime(2026, 8, 29, 8, tzinfo=UTC)
    store = business_demo_fixture_store().with_faults(
        {
            ("timeline-persistent", "get_logistics_timeline", 1): FixtureFault(
                execution_status=ExecutionStatus.RETRYABLE_ERROR,
                error_code="SYNTHETIC_TIMELINE_UNAVAILABLE",
            ),
            ("timeline-persistent", "get_logistics_timeline", 2): FixtureFault(
                execution_status=ExecutionStatus.RETRYABLE_ERROR,
                error_code="SYNTHETIC_TIMELINE_UNAVAILABLE",
            ),
        }
    )
    executor = build_executor(
        "ORD-033",
        IssueType.STALLED_TRACKING,
        customer_id="customer_l",
        store=store,
        fault_seed="timeline-persistent",
        target_shipment_id="SHP-033",
        evaluated_at=business_now,
    )
    facts = stalled_facts(executor)
    second = executor.execute_result("get_logistics_timeline", {"order_id": "ORD-033"})
    unavailable_facts = facts.model_copy(
        update={
            "timeline": second,
            "critical_retry_exhausted": executor.retry_exhausted_for(
                "get_logistics_timeline", {"order_id": "ORD-033"}
            ),
        }
    )
    assert evaluate_stalled_tracking(unavailable_facts).reason_code == (
        "CRITICAL_EVIDENCE_UNAVAILABLE_FINAL"
    )

    clean_executor = build_executor(
        "ORD-033",
        IssueType.STALLED_TRACKING,
        customer_id="customer_l",
        store=business_demo_fixture_store(),
        target_shipment_id="SHP-033",
        evaluated_at=business_now,
    )
    clean = stalled_facts(clean_executor)
    assert clean.timeline.payload is not None
    timeline_payload = clean.timeline.payload
    target_timeline = timeline_payload.shipment_timelines[0]
    conflict_event = target_timeline.events[-1].model_copy(update={"status": "delivered"})
    conflict_target = target_timeline.model_copy(
        update={
            "events": [*target_timeline.events[:-1], conflict_event],
        }
    )
    conflict_events = [
        *timeline_payload.events[:-1],
        conflict_event,
    ]
    conflict_payload = timeline_payload.model_copy(
        update={
            "events": conflict_events,
            "shipment_timelines": [conflict_target],
        }
    )
    conflict_facts = clean.model_copy(
        update={"timeline": clean.timeline.model_copy(update={"payload": conflict_payload})}
    )
    result = evaluate_stalled_tracking(conflict_facts)
    assert result.decision is EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT
    assert result.reason_code == "PERSISTENT_STRUCTURAL_CONFLICT"

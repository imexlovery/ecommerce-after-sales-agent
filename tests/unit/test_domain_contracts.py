from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from after_sales_agent.domain.models import (
    ActionExecution,
    ActionProposal,
    InvestigationCase,
)
from after_sales_agent.domain.state import (
    ActionState,
    ActionType,
    CaseOutcome,
    CaseState,
    IssueType,
    ProposalState,
)
from after_sales_agent.domain.transitions import (
    IllegalStateTransition,
    transition_action,
    transition_case,
    transition_proposal,
)
from after_sales_agent.tools.contracts import EvidenceRef, ExistingInvestigation, LogisticsTicket

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def evidence_ref() -> EvidenceRef:
    return EvidenceRef(
        tool_call_id="call-1",
        source_query_id="query-1",
        source_record_id=None,
        observed_at=NOW,
        result_hash="a" * 64,
    )


def test_case_state_and_outcome_remain_separate() -> None:
    with pytest.raises(ValidationError):
        InvestigationCase(
            case_id="case-1",
            conversation_id="conv-1",
            customer_id="customer_a",
            authorized_order_id="ORD-001",
            canonical_issue_type=IssueType.SIGNED_NOT_RECEIVED,
            case_state=CaseState.INVESTIGATING,
            case_outcome=CaseOutcome.TICKET_CREATED,
        )

    open_case = InvestigationCase(
        case_id="case-1",
        conversation_id="conv-1",
        customer_id="customer_a",
        authorized_order_id="ORD-001",
        canonical_issue_type=IssueType.SIGNED_NOT_RECEIVED,
    )
    closed = transition_case(
        open_case,
        CaseState.CLOSED,
        outcome=CaseOutcome.RESOLVED_NO_ACTION,
        reason_code="NO_ACTION_REQUIRED",
    )
    assert closed.case_outcome is CaseOutcome.RESOLVED_NO_ACTION
    with pytest.raises(IllegalStateTransition):
        transition_case(closed, CaseState.INVESTIGATING)


def test_proposal_is_fifteen_minute_snapshot_and_terminal_after_confirmation() -> None:
    proposal = ActionProposal(
        proposal_id="proposal-1",
        case_id="case-1",
        version=1,
        action_type=ActionType.CREATE_LOGISTICS_INVESTIGATION_TICKET,
        execution_parameters={"order_id": "ORD-001"},
        customer_visible_effect="确认创建物流核查工单",
        evidence_refs=[evidence_ref()],
        evidence_snapshot_hash="b" * 64,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )
    confirmed = transition_proposal(proposal, ProposalState.CONFIRMED)
    assert confirmed.proposal_id == proposal.proposal_id
    assert confirmed.version == proposal.version
    with pytest.raises(IllegalStateTransition):
        transition_proposal(confirmed, ProposalState.INVALIDATED)

    with pytest.raises(ValidationError):
        proposal.model_validate(
            {
                **proposal.model_dump(),
                "expires_at": NOW + timedelta(minutes=16),
            }
        )


def test_uncertain_action_is_terminal_and_keeps_original_idempotency_identity() -> None:
    action = ActionExecution(
        action_id="action-1",
        proposal_id="proposal-1",
        action_state=ActionState.READY,
        idempotency_key="idem-case-1-proposal-1",
    )
    submitted = transition_action(action, ActionState.SUBMITTED, occurred_at=NOW)
    uncertain = transition_action(
        submitted,
        ActionState.UNCERTAIN,
        occurred_at=NOW + timedelta(seconds=1),
        error_code="WRITE_ACK_AND_READBACK_UNAVAILABLE",
    )
    assert uncertain.action_id == action.action_id
    assert uncertain.idempotency_key == action.idempotency_key
    with pytest.raises(IllegalStateTransition):
        transition_action(
            uncertain,
            ActionState.SUBMITTED,
            occurred_at=NOW + timedelta(seconds=2),
        )


def test_existing_logistics_read_contract_derives_canonical_business_fields() -> None:
    opened_at = datetime(2026, 8, 28, 8, tzinfo=UTC)
    updated_at = datetime(2026, 8, 28, 9, tzinfo=UTC)
    investigation = ExistingInvestigation(
        case_id="seed-case-1",
        order_id="ORD-024",
        issue_type=IssueType.STALLED_TRACKING,
        target_shipment_id="SHP-024",
        stage="carrier_follow_up",
        updated_at=updated_at,
        created_at=opened_at,
        status="investigating",
        next_update_at=datetime(2026, 8, 30, 9, tzinfo=UTC),
    )
    ticket = LogisticsTicket(
        ticket_id="ticket-1",
        order_id="ORD-024",
        issue_type=IssueType.STALLED_TRACKING,
        ticket_status="open",
        created_at=opened_at,
    )

    assert investigation.status == "investigating"
    assert investigation.opened_at == opened_at
    assert investigation.last_updated_at == updated_at
    assert investigation.target_order_id == "ORD-024"
    assert investigation.is_active is True
    assert ticket.status == "open"
    assert ticket.opened_at == opened_at
    assert ticket.last_updated_at == opened_at
    assert ticket.target_order_id == "ORD-024"
    assert ticket.is_active is True

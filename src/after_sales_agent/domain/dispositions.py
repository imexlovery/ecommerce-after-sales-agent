"""Pure projection from governed lifecycle facts to five customer dispositions."""

from __future__ import annotations

from enum import Enum
from typing import Any

from .state import (
    ActionState,
    CaseOutcome,
    CaseState,
    CustomerDisposition,
    EvidenceGateDecision,
    ProposalState,
)


def _value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def project_customer_disposition(
    *,
    gate_decision: EvidenceGateDecision | str | None = None,
    case_state: CaseState | str | None = None,
    case_outcome: CaseOutcome | str | None = None,
    proposal_state: ProposalState | str | None = None,
    action_state: ActionState | str | None = None,
    reason_code: str | None = None,
) -> CustomerDisposition:
    """Project business meaning without merging the underlying lifecycle states.

    The caller supplies independently persisted state values. This function is
    intentionally a pure precedence table, so API, events, and the UI can use
    the same vocabulary without introducing a sixth state machine.
    """

    decision = _value(gate_decision)
    state = _value(case_state)
    outcome = _value(case_outcome)
    proposal = _value(proposal_state)
    action = _value(action_state)
    reason = reason_code or ""

    if (
        action == ActionState.UNCERTAIN.value
        or outcome == CaseOutcome.UNCERTAIN.value
        or outcome == CaseOutcome.HUMAN_SUPPORT_REQUIRED.value
        or outcome == CaseOutcome.FAILED.value
        or decision == EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT.value
        or "CONFLICT" in reason
        or "INTEGRITY" in reason
        or "HUMAN" in reason
        or "EXHAUSTED" in reason
    ):
        return CustomerDisposition.ESCALATE

    if (
        action in {
            ActionState.READY.value,
            ActionState.SUBMITTED.value,
            ActionState.SUCCEEDED.value,
        }
        or outcome == CaseOutcome.TICKET_CREATED.value
        or proposal in {
            ProposalState.PENDING_CONFIRMATION.value,
            ProposalState.CONFIRMED.value,
        }
        or state == CaseState.AWAITING_CUSTOMER_CONFIRMATION.value
        or decision == EvidenceGateDecision.PROPOSE_TICKET.value
    ):
        return CustomerDisposition.INVESTIGATE

    if (
        decision == EvidenceGateDecision.REQUEST_BUSINESS_CLARIFICATION.value
        or state == CaseState.AWAITING_CUSTOMER_INPUT.value
        or "CLARIFICATION" in reason
        or "QUESTION" in reason
        or reason in {
            "ORDER_ID_REQUIRED",
            "MULTIPLE_AUTHORIZED_ORDERS_REQUIRE_SELECTION",
            "ISSUE_REQUIRES_CLARIFICATION",
            "UNSUPPORTED_LOGISTICS_REQUIRES_CLARIFICATION",
        }
    ):
        return CustomerDisposition.CLARIFY

    if (
        decision == EvidenceGateDecision.RETRY_LATER.value
        or state == CaseState.AWAITING_RETRY.value
        or reason in {
            "ACTIVE_LOGISTICS_TICKET_EXISTS",
            "ACTIVE_LOGISTICS_INVESTIGATION_EXISTS",
            "ACTIVE_CARRIER_RECOVERY_WINDOW",
            "WITHIN_TRACKING_SLA",
            "CRITICAL_EVIDENCE_UNAVAILABLE",
            "DELIVERED_WITHOUT_TIMELINE_EVIDENCE",
            "STRUCTURAL_CONFLICT_REQUIRES_REFRESH",
        }
    ):
        return CustomerDisposition.WAIT

    return CustomerDisposition.ANSWER


customer_disposition = project_customer_disposition

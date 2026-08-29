from after_sales_agent.domain.dispositions import project_customer_disposition
from after_sales_agent.domain.state import (
    ActionState,
    CaseOutcome,
    CaseState,
    CustomerDisposition,
    EvidenceGateDecision,
    ProposalState,
)


def test_gate_decisions_project_to_the_five_exact_customer_dispositions() -> None:
    assert (
        project_customer_disposition(
            gate_decision=EvidenceGateDecision.COMPLETE_NO_ACTION,
        )
        is CustomerDisposition.ANSWER
    )
    assert (
        project_customer_disposition(
            gate_decision=EvidenceGateDecision.RETRY_LATER,
        )
        is CustomerDisposition.WAIT
    )
    assert (
        project_customer_disposition(
            gate_decision=EvidenceGateDecision.REQUEST_BUSINESS_CLARIFICATION,
        )
        is CustomerDisposition.CLARIFY
    )
    assert (
        project_customer_disposition(
            gate_decision=EvidenceGateDecision.PROPOSE_TICKET,
        )
        is CustomerDisposition.INVESTIGATE
    )
    assert (
        project_customer_disposition(
            gate_decision=EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT,
        )
        is CustomerDisposition.ESCALATE
    )


def test_case_proposal_and_action_facts_keep_the_projection_deterministic() -> None:
    assert (
        project_customer_disposition(
            case_state=CaseState.AWAITING_RETRY,
            reason_code="ACTIVE_LOGISTICS_TICKET_EXISTS",
        )
        is CustomerDisposition.WAIT
    )
    assert (
        project_customer_disposition(
            proposal_state=ProposalState.PENDING_CONFIRMATION,
        )
        is CustomerDisposition.INVESTIGATE
    )
    assert (
        project_customer_disposition(
            action_state=ActionState.SUCCEEDED,
            case_outcome=CaseOutcome.TICKET_CREATED,
        )
        is CustomerDisposition.INVESTIGATE
    )
    assert (
        project_customer_disposition(
            action_state=ActionState.UNCERTAIN,
            case_outcome=CaseOutcome.UNCERTAIN,
        )
        is CustomerDisposition.ESCALATE
    )

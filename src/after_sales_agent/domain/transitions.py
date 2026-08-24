"""Deterministic lifecycle transitions; the five state machines never collapse."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from after_sales_agent.domain.models import (
    ActionExecution,
    ActionProposal,
    InvestigationCase,
    Run,
)
from after_sales_agent.domain.state import (
    ActionState,
    CaseOutcome,
    CaseState,
    ProposalState,
    RunState,
)


class IllegalStateTransition(ValueError):
    pass


def _validated_update[ModelT: BaseModel](model: ModelT, updates: dict[str, Any]) -> ModelT:
    values = model.model_dump()
    values.update(updates)
    return type(model).model_validate(values)


_CASE_TRANSITIONS: dict[CaseState, frozenset[CaseState]] = {
    CaseState.INVESTIGATING: frozenset(
        {
            CaseState.AWAITING_CUSTOMER_INPUT,
            CaseState.AWAITING_CUSTOMER_CONFIRMATION,
            CaseState.AWAITING_RETRY,
            CaseState.CLOSED,
        }
    ),
    CaseState.AWAITING_CUSTOMER_INPUT: frozenset(
        {CaseState.INVESTIGATING, CaseState.AWAITING_RETRY, CaseState.CLOSED}
    ),
    CaseState.AWAITING_CUSTOMER_CONFIRMATION: frozenset(
        {CaseState.EXECUTING_ACTION, CaseState.CLOSED}
    ),
    CaseState.AWAITING_RETRY: frozenset({CaseState.INVESTIGATING, CaseState.CLOSED}),
    CaseState.EXECUTING_ACTION: frozenset({CaseState.CLOSED}),
    CaseState.CLOSED: frozenset(),
}

_RUN_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.QUEUED: frozenset({RunState.RUNNING, RunState.FAILED}),
    RunState.RUNNING: frozenset({RunState.SUCCEEDED, RunState.FAILED}),
    RunState.SUCCEEDED: frozenset(),
    RunState.FAILED: frozenset(),
}

_PROPOSAL_TRANSITIONS: dict[ProposalState, frozenset[ProposalState]] = {
    ProposalState.PENDING_CONFIRMATION: frozenset(
        {
            ProposalState.CONFIRMED,
            ProposalState.DECLINED,
            ProposalState.SUPERSEDED,
            ProposalState.EXPIRED,
            ProposalState.INVALIDATED,
        }
    ),
    ProposalState.CONFIRMED: frozenset(),
    ProposalState.DECLINED: frozenset(),
    ProposalState.SUPERSEDED: frozenset(),
    ProposalState.EXPIRED: frozenset(),
    ProposalState.INVALIDATED: frozenset(),
}

_ACTION_TRANSITIONS: dict[ActionState, frozenset[ActionState]] = {
    ActionState.READY: frozenset({ActionState.SUBMITTED}),
    ActionState.SUBMITTED: frozenset(
        {
            ActionState.SUCCEEDED,
            ActionState.FAILED_RETRYABLE,
            ActionState.FAILED_TERMINAL,
            ActionState.UNCERTAIN,
        }
    ),
    ActionState.FAILED_RETRYABLE: frozenset({ActionState.SUBMITTED}),
    ActionState.SUCCEEDED: frozenset(),
    ActionState.FAILED_TERMINAL: frozenset(),
    ActionState.UNCERTAIN: frozenset(),
}


def _assert_allowed[StateT](
    current: StateT,
    target: StateT,
    allowed: Mapping[StateT, frozenset[StateT]],
) -> None:
    if target not in allowed[current]:
        raise IllegalStateTransition(f"illegal transition: {current} -> {target}")


def transition_case(
    case: InvestigationCase,
    target: CaseState,
    *,
    outcome: CaseOutcome | None = None,
    reason_code: str | None = None,
) -> InvestigationCase:
    _assert_allowed(case.case_state, target, _CASE_TRANSITIONS)
    if target is CaseState.CLOSED:
        if outcome is None or not reason_code:
            raise IllegalStateTransition("closing a Case requires outcome and reason_code")
    elif outcome is not None or reason_code is not None:
        raise IllegalStateTransition("open Case transitions cannot set closure fields")
    return _validated_update(
        case,
        {"case_state": target, "case_outcome": outcome, "reason_code": reason_code},
    )


def transition_run(
    run: Run,
    target: RunState,
    *,
    failure_class: str | None = None,
) -> Run:
    _assert_allowed(run.run_state, target, _RUN_TRANSITIONS)
    if (target is RunState.FAILED) != bool(failure_class):
        raise IllegalStateTransition("only a failed Run carries failure_class")
    return _validated_update(run, {"run_state": target, "failure_class": failure_class})


def transition_proposal(
    proposal: ActionProposal,
    target: ProposalState,
) -> ActionProposal:
    _assert_allowed(proposal.proposal_state, target, _PROPOSAL_TRANSITIONS)
    return _validated_update(proposal, {"proposal_state": target})


def transition_action(
    action: ActionExecution,
    target: ActionState,
    *,
    occurred_at: datetime,
    error_code: str | None = None,
) -> ActionExecution:
    _assert_allowed(action.action_state, target, _ACTION_TRANSITIONS)
    if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise IllegalStateTransition("action transition timestamp must be timezone-aware")
    submitted_at = action.submitted_at
    verified_at = action.verified_at
    if target is ActionState.SUBMITTED and submitted_at is None:
        submitted_at = occurred_at
    if target is ActionState.SUCCEEDED:
        verified_at = occurred_at
    if target in {ActionState.SUCCEEDED, ActionState.SUBMITTED} and error_code is not None:
        raise IllegalStateTransition("successful/submitted action state cannot carry error_code")
    if (
        target
        in {
            ActionState.FAILED_RETRYABLE,
            ActionState.FAILED_TERMINAL,
            ActionState.UNCERTAIN,
        }
        and not error_code
    ):
        raise IllegalStateTransition("failed or uncertain action requires error_code")
    return _validated_update(
        action,
        {
            "action_state": target,
            "submitted_at": submitted_at,
            "verified_at": verified_at,
            "error_code": error_code,
        },
    )

"""Immutable Proposal construction and stable simulated-action identities."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from after_sales_agent.domain.models import ActionExecution, ActionProposal
from after_sales_agent.domain.state import ActionState, ActionType, IssueType
from after_sales_agent.tools.contracts import EvidenceRef


def evidence_snapshot_hash(
    *,
    critical_result_hashes: dict[str, str],
    execution_parameters: dict[str, Any],
    case_fact_identity: dict[str, Any] | None = None,
) -> str:
    normalized = json.dumps(
        {
            "critical_result_hashes": critical_result_hashes,
            "execution_parameters": execution_parameters,
            "case_fact_identity": case_fact_identity or {},
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def build_proposal(
    *,
    proposal_id: str,
    case_id: str,
    version: int,
    order_id: str,
    issue_type: IssueType,
    evidence_refs: list[EvidenceRef],
    critical_result_hashes: dict[str, str],
    policy_binding: dict[str, Any],
    case_fact_identity: dict[str, Any] | None = None,
    now: datetime,
) -> ActionProposal:
    parameters = {
        "order_id": order_id,
        "issue_type": issue_type.value,
        "policy_binding": policy_binding,
    }
    return ActionProposal(
        proposal_id=proposal_id,
        case_id=case_id,
        version=version,
        action_type=ActionType.CREATE_LOGISTICS_INVESTIGATION_TICKET,
        execution_parameters=parameters,
        customer_visible_effect=(
            f"为虚拟订单 {order_id} 创建一张物流核查工单；不会退款、赔付或修改订单。"
        ),
        evidence_refs=evidence_refs,
        evidence_snapshot_hash=evidence_snapshot_hash(
            critical_result_hashes=critical_result_hashes,
            execution_parameters=parameters,
            case_fact_identity=case_fact_identity,
        ),
        case_fact_identity=case_fact_identity or {},
        created_at=now,
        expires_at=now + timedelta(minutes=15),
    )


def stable_action_identity(proposal: ActionProposal) -> tuple[str, str]:
    raw = (f"{proposal.proposal_id}:{proposal.version}:{proposal.action_type.value}").encode()
    digest = hashlib.sha256(raw).hexdigest()
    return f"act_{digest[:24]}", f"idem_{digest}"


def build_ready_action(proposal: ActionProposal) -> ActionExecution:
    action_id, idempotency_key = stable_action_identity(proposal)
    return ActionExecution(
        action_id=action_id,
        proposal_id=proposal.proposal_id,
        action_state=ActionState.READY,
        idempotency_key=idempotency_key,
    )


def stable_ticket_id(idempotency_key: str) -> str:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return f"TKT-SYN-{digest[:12].upper()}"

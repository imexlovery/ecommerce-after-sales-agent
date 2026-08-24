from __future__ import annotations

from datetime import UTC, datetime

from after_sales_agent.actions.service import (
    build_proposal,
    build_ready_action,
    evidence_snapshot_hash,
)
from after_sales_agent.domain.state import IssueType
from after_sales_agent.tools.contracts import EvidenceRef


def _ref() -> EvidenceRef:
    return EvidenceRef(
        tool_call_id="call_test",
        source_query_id="query_test",
        source_record_id=None,
        field_path=None,
        observed_at=datetime(2026, 8, 23, tzinfo=UTC),
        result_hash="a" * 64,
    )


def test_proposal_binds_critical_evidence_and_exact_parameters() -> None:
    proposal = build_proposal(
        proposal_id="prop_test",
        case_id="case_test",
        version=1,
        order_id="ORD-001",
        issue_type=IssueType.SIGNED_NOT_RECEIVED,
        evidence_refs=[_ref()],
        critical_result_hashes={"delivery_proof": "a" * 64},
        now=datetime(2026, 8, 23, tzinfo=UTC),
    )

    assert proposal.evidence_snapshot_hash == evidence_snapshot_hash(
        critical_result_hashes={"delivery_proof": "a" * 64},
        execution_parameters={
            "order_id": "ORD-001",
            "issue_type": "signed_not_received",
        },
    )
    assert (
        build_ready_action(proposal).idempotency_key == build_ready_action(proposal).idempotency_key
    )

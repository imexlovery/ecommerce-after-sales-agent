from __future__ import annotations

from after_sales_agent.application.adaptive_core import EvidenceProgressReducer
from after_sales_agent.domain.state import IssueType
from after_sales_agent.evals.trajectory_graders import (
    grade_early_stop,
    grade_evidence_rebuild,
    grade_exact_retry,
    grade_hard_safety,
    grade_observation_conditioned_trajectory,
    grade_retry_budget,
    grade_trace_completeness,
)


def _trace() -> dict[str, object]:
    return {
        "decisions": [
            {
                "trace_sequence": 1,
                "decision_id": "dec_1",
                "selector_kind": "agent",
                "planning_turn": 1,
                "action": "call_tool",
                "canonical_arguments_hash": "a" * 64,
                "evidence_progress_hash": "b" * 64,
                "validation_status": "accepted",
            }
        ],
        "tool_calls": [
            {
                "trace_sequence": 2,
                "tool_call_id": "call_1",
                "tool_name": "get_delivery_proof",
                "normalized_args": {"order_id": "ORD-001"},
                "source_version": "fixture-v1",
                "actual_execution": True,
                "attempt_number": 1,
                "execution_status": "retryable_error",
                "evidence_availability": "unavailable",
                "planning_turn": 1,
                "budget_before_actual_reads": 0,
                "budget_after_actual_reads": 1,
            },
            {
                "trace_sequence": 4,
                "tool_call_id": "call_1:retry",
                "tool_name": "get_delivery_proof",
                "normalized_args": {"order_id": "ORD-001"},
                "source_version": "fixture-v1",
                "actual_execution": True,
                "attempt_number": 2,
                "execution_status": "success",
                "evidence_availability": "absent",
                "planning_turn": 1,
                "budget_before_actual_reads": 1,
                "budget_after_actual_reads": 2,
            },
        ],
        "recoveries": [
            {
                "trace_sequence": 3,
                "route": "retry_exact",
                "reason_code": "RETRYABLE_TOOL_FAILURE",
            },
            {
                "trace_sequence": 5,
                "route": "finalize",
                "reason_code": "GATE_READY",
            },
        ],
        "states": [
            {"trace_sequence": 6, "phase_to": "terminal"},
        ],
        "progress_rebuilds": [
            {
                "case_id": "case-a",
                "run_id": "run-a",
                "canonical_issue_type": "signed_not_received",
                "tool_calls": [],
                "evidence_refs": [],
                "online_snapshot_hash": EvidenceProgressReducer()
                .initial(
                    case_id="case-a",
                    run_id="run-a",
                    canonical_issue_type=IssueType.SIGNED_NOT_RECEIVED,
                )
                .snapshot_hash,
            }
        ],
    }


def test_observation_conditioned_graders_verify_retry_and_early_stop() -> None:
    trace = _trace()
    assert grade_exact_retry(trace).passed is True
    assert grade_early_stop(trace).passed is True
    assert grade_trace_completeness(trace).passed is True
    assert grade_hard_safety(trace).passed is True
    verdicts = grade_observation_conditioned_trajectory(trace)
    assert {verdict.grader_id for verdict in verdicts} == {
        f"GR-V3A-{index:02d}" for index in range(1, 14)
    }


def test_hard_safety_grader_rejects_prohibited_trace_key() -> None:
    trace = _trace()
    trace["provider_payload"] = {"opaque": True}
    assert grade_hard_safety(trace).passed is False


def test_retry_and_rebuild_graders_reject_forged_trajectory() -> None:
    trace = _trace()
    trace["decisions"].append(  # type: ignore[union-attr]
        {
            "trace_sequence": 3,
            "decision_id": "illegal-selector-between-retries",
            "selector_kind": "agent",
            "planning_turn": 2,
            "action": "call_tool",
            "canonical_arguments_hash": "d" * 64,
            "evidence_progress_hash": "e" * 64,
            "validation_status": "accepted",
        }
    )
    trace["progress_rebuilds"][0]["online_snapshot_hash"] = "f" * 64  # type: ignore[index]
    assert grade_exact_retry(trace).passed is False
    assert grade_retry_budget(trace).passed is True
    assert grade_evidence_rebuild(trace).passed is False

    trace["tool_calls"][1]["budget_after_actual_reads"] = 99  # type: ignore[index]
    assert grade_retry_budget(trace).passed is False

# ruff: noqa: E501
"""Deterministic observation-conditioned V3-A trajectory graders.

The functions in this module inspect typed decision/recovery/state/tool records
only.  They never consume model prose and are deliberately independent from the
legacy V2 manifest graders and reports.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from after_sales_agent.application.adaptive_core import EvidenceProgressReducer
from after_sales_agent.domain.state import IssueType
from after_sales_agent.tools.contracts import EvidenceRef

V3A_TRAJECTORY_CONTRACT_VERSION = "v3a.trajectory.v1"
V3A_GRADER_REGISTRY_VERSION = "v3a.trajectory-grader-registry.v1"


@dataclass(frozen=True, slots=True)
class TrajectoryGraderVerdict:
    grader_id: str
    passed: bool
    detail: str
    triggered: bool = True


def _as_dict(item: Any) -> Mapping[str, Any]:
    if isinstance(item, Mapping):
        return item
    if hasattr(item, "model_dump"):
        value = cast(Any, item).model_dump(mode="json")
        return value if isinstance(value, Mapping) else {}
    if hasattr(item, "to_dict"):
        value = cast(Any, item).to_dict()
        return value if isinstance(value, Mapping) else {}
    return {}


def _records(trace: Any, key: str) -> list[Mapping[str, Any]]:
    if isinstance(trace, Mapping):
        value = trace.get(key, [])
    else:
        value = getattr(trace, key, [])
    return [_as_dict(item) for item in value] if isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping)) else []


def _tool_records(trace: Any) -> list[Mapping[str, Any]]:
    return _records(trace, "tool_calls") or _records(trace, "observations")


def _actual_tools(trace: Any) -> list[Mapping[str, Any]]:
    return [item for item in _tool_records(trace) if item.get("actual_execution") is True]


def _decisions(trace: Any) -> list[Mapping[str, Any]]:
    return _records(trace, "decisions")


def _recoveries(trace: Any) -> list[Mapping[str, Any]]:
    return _records(trace, "recoveries")


def _states(trace: Any) -> list[Mapping[str, Any]]:
    return _records(trace, "states")


def _verdict(grader_id: str, passed: bool, detail: str, *, triggered: bool = True) -> TrajectoryGraderVerdict:
    return TrajectoryGraderVerdict(grader_id, passed, detail, triggered)


def grade_next_observation_contract(trace: Any) -> TrajectoryGraderVerdict:
    decisions = _decisions(trace)
    if not decisions:
        return _verdict("GR-V3A-01", False, "no selector decision records")
    required = {"decision_id", "selector_kind", "planning_turn", "action", "canonical_arguments_hash", "evidence_progress_hash"}
    passed = all(required.issubset(item) and item.get("validation_status") in {"accepted", "rejected"} for item in decisions)
    return _verdict("GR-V3A-01", passed, "next-observation contract verified" if passed else "malformed decision record")


def grade_observation_conditioned_choice(trace: Any, obligations: Sequence[Mapping[str, Any]] = ()) -> TrajectoryGraderVerdict:
    decisions = _decisions(trace)
    for obligation in obligations:
        when = obligation.get("when", {})
        triggered = any(all(item.get(k) == v for k, v in when.items()) for item in _tool_records(trace))
        if not triggered:
            continue
        then = obligation.get("then", {})
        if "required_next_route" in then:
            routes = [item.get("route") for item in _recoveries(trace)]
            if then["required_next_route"] not in routes:
                return _verdict("GR-V3A-02", False, "triggered route obligation failed")
        if "forbidden_future_tools" in then:
            forbidden = set(then["forbidden_future_tools"])
            if any(item.get("tool_name") in forbidden for item in _actual_tools(trace)):
                return _verdict("GR-V3A-02", False, "triggered forbidden-tool obligation failed")
    return _verdict("GR-V3A-02", bool(decisions), "observation-conditioned choices verified")


def grade_early_stop(trace: Any) -> TrajectoryGraderVerdict:
    recoveries = _recoveries(trace)
    ready_sequences = [int(item.get("trace_sequence", 0)) for item in recoveries if item.get("route") == "finalize" or item.get("reason_code") == "GATE_READY"]
    if not ready_sequences:
        return _verdict("GR-V3A-03", True, "gate readiness was not triggered", triggered=False)
    cutoff = min(ready_sequences)
    later_tools = [item for item in _actual_tools(trace) if int(item.get("trace_sequence", 0)) > cutoff]
    later_decisions = [item for item in _decisions(trace) if int(item.get("trace_sequence", 0)) > cutoff]
    passed = not later_tools and not later_decisions
    return _verdict("GR-V3A-03", passed, "early-stop guard verified" if passed else "work occurred after gate readiness")


def grade_premature_finish(trace: Any) -> TrajectoryGraderVerdict:
    rejected = [item for item in _decisions(trace) if item.get("rejection_code") == "PREMATURE_FINISH"]
    if not rejected:
        return _verdict("GR-V3A-04", True, "premature finish was not triggered", triggered=False)
    correction = any(item.get("validation_status") == "accepted" for item in _decisions(trace) if int(item.get("planning_turn", 0)) > int(rejected[0].get("planning_turn", 0)))
    stuck = any(item.get("reason_code") in {"STUCK_REPEATED_DECISION", "STUCK_NO_EVIDENCE_PROGRESS"} for item in _recoveries(trace))
    return _verdict("GR-V3A-04", correction or stuck, "premature-finish guard verified" if correction or stuck else "no bounded correction or safe stop")


def grade_stuck_guard(trace: Any) -> TrajectoryGraderVerdict:
    reasons = {item.get("reason_code") for item in _recoveries(trace)}
    repeated = [item for item in _decisions(trace) if item.get("rejection_code") == "STUCK_REPEATED_DECISION"]
    unchanged = "STUCK_NO_EVIDENCE_PROGRESS" in reasons
    passed = bool(repeated or unchanged) if any(item.get("route") == "safe_stop" for item in _recoveries(trace)) else True
    return _verdict("GR-V3A-05", passed, "stuck guard verified" if passed else "stuck path was not bounded")


def grade_exact_retry(trace: Any) -> TrajectoryGraderVerdict:
    calls = _tool_records(trace)
    for index, call in enumerate(calls):
        if call.get("execution_status") != "retryable_error" or call.get("evidence_availability") != "unavailable" or call.get("attempt_number") != 1:
            continue
        subsequent = next((item for item in calls[index + 1 :] if item.get("actual_execution") is True), None)
        if subsequent is None:
            return _verdict("GR-V3A-06", False, "retry attempt missing")
        same = (
            subsequent.get("attempt_number") == 2
            and subsequent.get("tool_name") == call.get("tool_name")
            and subsequent.get("normalized_args", subsequent.get("canonical_arguments")) == call.get("normalized_args", call.get("canonical_arguments"))
            and subsequent.get("source_version") == call.get("source_version")
        )
        first_sequence = int(call.get("trace_sequence", 0))
        second_sequence = int(subsequent.get("trace_sequence", 0))
        no_selector_between = first_sequence > 0 and second_sequence > first_sequence and not any(
            first_sequence < int(item.get("trace_sequence", 0)) < second_sequence
            for item in _decisions(trace)
        )
        same_planning_turn = subsequent.get("planning_turn") == call.get("planning_turn")
        if not (same and no_selector_between and same_planning_turn):
            return _verdict("GR-V3A-06", False, "retry identity was changed")
        return _verdict("GR-V3A-06", True, "adjacent exact retry verified")
    return _verdict("GR-V3A-06", True, "exact retry was not triggered", triggered=False)


def grade_retry_budget(trace: Any) -> TrajectoryGraderVerdict:
    calls = _actual_tools(trace)
    retries = [item for item in _recoveries(trace) if item.get("route") == "retry_exact"]
    if not retries:
        return _verdict("GR-V3A-07", True, "retry was not triggered", triggered=False)
    retry_pairs = [
        (calls[index], calls[index + 1])
        for index in range(len(calls) - 1)
        if calls[index].get("attempt_number") == 1
        and calls[index].get("execution_status") == "retryable_error"
        and calls[index + 1].get("attempt_number") == 2
    ]
    passed = bool(retry_pairs) and all(
        first.get("budget_after_actual_reads")
        == int(first.get("budget_before_actual_reads", -2)) + 1
        and second.get("budget_before_actual_reads") == first.get("budget_after_actual_reads")
        and second.get("budget_after_actual_reads")
        == int(second.get("budget_before_actual_reads", -2)) + 1
        and second.get("planning_turn") == first.get("planning_turn")
        for first, second in retry_pairs
    )
    return _verdict("GR-V3A-07", passed, "retry budget accounting verified" if passed else "retry consumed selector work or no second read")


def grade_evidence_rebuild(trace: Any) -> TrajectoryGraderVerdict:
    snapshots = _records(trace, "progress_rebuilds")
    if not snapshots:
        return _verdict("GR-V3A-08", False, "missing reducer replay inputs")
    reducer = EvidenceProgressReducer()
    for item in snapshots:
        try:
            replayed = reducer.rebuild(
                case_id=str(item["case_id"]),
                run_id=str(item["run_id"]),
                canonical_issue_type=IssueType(str(item["canonical_issue_type"])),
                tool_calls=list(item["tool_calls"]),
                evidence_refs=[EvidenceRef.model_validate(ref) for ref in item["evidence_refs"]],
            )
        except (KeyError, TypeError, ValueError):
            return _verdict("GR-V3A-08", False, "invalid reducer replay inputs")
        if replayed.snapshot_hash != item.get("online_snapshot_hash"):
            return _verdict("GR-V3A-08", False, "online/replayed progress hash mismatch")
    return _verdict("GR-V3A-08", True, "evidence progress reducer replay verified")


def grade_evidence_availability(trace: Any) -> TrajectoryGraderVerdict:
    calls = _tool_records(trace)
    bad = any(item.get("evidence_availability") == "unavailable" and item.get("progress_status") in {"satisfied_present", "satisfied_absent"} for item in calls)
    return _verdict("GR-V3A-09", not bad, "availability semantics verified" if not bad else "unavailable evidence satisfied a requirement")


def grade_outcome(trace: Any, allowed_gate_decisions: Iterable[str] = ()) -> TrajectoryGraderVerdict:
    allowed = set(allowed_gate_decisions)
    if not allowed:
        return _verdict("GR-V3A-10", True, "no scenario outcome restriction supplied", triggered=False)
    outcomes = _records(trace, "gate_decisions") or _records(trace, "outcomes")
    actual = outcomes[-1].get("decision") if outcomes else None
    passed = actual in allowed
    return _verdict("GR-V3A-10", passed, "deterministic outcome is allowed" if passed else "outcome is outside allowed set")


def grade_unnecessary_reads(trace: Any) -> TrajectoryGraderVerdict:
    states = _states(trace)
    terminal_seq = min((int(item.get("trace_sequence", 0)) for item in states if item.get("phase_to") in {"finalize", "safe_stop", "terminal"}), default=None)
    if terminal_seq is None:
        return _verdict("GR-V3A-11", True, "no terminal state was recorded", triggered=False)
    passed = not any(int(item.get("trace_sequence", 0)) > terminal_seq for item in _actual_tools(trace))
    return _verdict("GR-V3A-11", passed, "no post-terminal reads" if passed else "post-terminal actual read detected")


def grade_trace_completeness(trace: Any) -> TrajectoryGraderVerdict:
    passed = bool(_decisions(trace)) and bool(_states(trace)) and all(item.get("trace_sequence") for item in _decisions(trace) + _recoveries(trace) + _states(trace))
    return _verdict("GR-V3A-12", passed, "trace correlation verified" if passed else "incomplete trace correlation")


def grade_hard_safety(trace: Any) -> TrajectoryGraderVerdict:
    prohibited = {"customer_id", "secret", "fault_seed", "system_prompt", "chain_of_thought", "provider_payload"}
    serialized = repr(trace).lower()
    passed = not any(token in serialized for token in prohibited)
    return _verdict("GR-V3A-13", passed, "trace safety projection verified" if passed else "prohibited trace content detected")


V3A_GRADERS: dict[str, Callable[..., TrajectoryGraderVerdict]] = {
    "GR-V3A-01": grade_next_observation_contract,
    "GR-V3A-02": grade_observation_conditioned_choice,
    "GR-V3A-03": grade_early_stop,
    "GR-V3A-04": grade_premature_finish,
    "GR-V3A-05": grade_stuck_guard,
    "GR-V3A-06": grade_exact_retry,
    "GR-V3A-07": grade_retry_budget,
    "GR-V3A-08": grade_evidence_rebuild,
    "GR-V3A-09": grade_evidence_availability,
    "GR-V3A-10": grade_outcome,
    "GR-V3A-11": grade_unnecessary_reads,
    "GR-V3A-12": grade_trace_completeness,
    "GR-V3A-13": grade_hard_safety,
}


def grade_observation_conditioned_trajectory(trace: Any, *, obligations: Sequence[Mapping[str, Any]] = (), allowed_gate_decisions: Iterable[str] = ()) -> tuple[TrajectoryGraderVerdict, ...]:
    """Run every deterministic V3-A grader without selecting a best run."""

    return (
        grade_next_observation_contract(trace),
        grade_observation_conditioned_choice(trace, obligations),
        grade_early_stop(trace),
        grade_premature_finish(trace),
        grade_stuck_guard(trace),
        grade_exact_retry(trace),
        grade_retry_budget(trace),
        grade_evidence_rebuild(trace),
        grade_evidence_availability(trace),
        grade_outcome(trace, allowed_gate_decisions),
        grade_unnecessary_reads(trace),
        grade_trace_completeness(trace),
        grade_hard_safety(trace),
    )


__all__ = [
    "V3A_GRADERS",
    "V3A_GRADER_REGISTRY_VERSION",
    "V3A_TRAJECTORY_CONTRACT_VERSION",
    "TrajectoryGraderVerdict",
    "grade_next_observation_contract",
    "grade_observation_conditioned_choice",
    "grade_early_stop",
    "grade_premature_finish",
    "grade_stuck_guard",
    "grade_exact_retry",
    "grade_retry_budget",
    "grade_evidence_rebuild",
    "grade_evidence_availability",
    "grade_outcome",
    "grade_unnecessary_reads",
    "grade_trace_completeness",
    "grade_hard_safety",
    "grade_observation_conditioned_trajectory",
]

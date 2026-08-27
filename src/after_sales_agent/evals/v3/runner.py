# ruff: noqa: E501
"""Trusted paired V3 preparation harness.

The preparation harness is deliberately provider-free.  It exercises the same
typed trace, budget, retry, reducer, Gate and grader boundaries that a later
authorized Development run will use; only the selector adapter is varied.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from after_sales_agent.application.adaptive_core import (
    BudgetSnapshot,
    CandidateValidationStatus,
    DecisionContext,
    DecisionTraceRecord,
    EvidenceProgressReducer,
    EvidenceRequirementCode,
    ObservationAction,
    RecoveryReasonCode,
    RecoveryRoute,
    RecoveryTraceRecord,
    SelectorKind,
    StateTraceRecord,
    TracePhase,
    canonical_arguments_hash,
)
from after_sales_agent.domain.case_facts import (
    CaseFactAssertion,
    CaseFactSnapshot,
    FactCode,
    FactSnapshotEntry,
    FactStatus,
    FactValue,
    QuestionState,
    QuestionStatus,
    RelationHint,
    message_hash,
)
from after_sales_agent.evals.v3.contracts import (
    V3Architecture,
    V3CaseSpec,
    V3ConsumptionTrace,
    V3DevelopmentReport,
    V3GateOutcome,
    V3GateTrace,
    V3Metrics,
    V3ProgressRebuild,
    V3QuestionTrace,
    V3RunRecord,
    V3SharedFields,
    V3ToolCall,
    V3ToolResultEnvelope,
    V3TypedTrace,
    sha256_json,
)
from after_sales_agent.evals.v3.graders import (
    V3GradingContext,
    _obligation_triggered,
    execute_v3_graders,
)
from after_sales_agent.evals.v3.matrix import load_manifests, load_matrix, validate_matrix
from after_sales_agent.evals.v3.report import build_development_report
from after_sales_agent.evals.v3.store import V3PrepStore
from after_sales_agent.tools.contracts import EvidenceRef

PREP_EVALUATION_REVISION = "V3-DEV-EVAL-PREP-001"
DRY_RUN_REPORT_ID = "V3-PREP-DRY-RUN-REPORT-001"
_ORDER_ID = "ORD-V3-PREP-001"
_CUSTOMER_ID = "CUS-V3-PREP-001"
_SOURCE_VERSION = "fixture-source-v1"


class FairnessViolation(ValueError):
    """Raised when the two architectures do not receive byte-equal inputs."""


@dataclass(frozen=True, slots=True)
class SharedPairInput:
    case: V3CaseSpec
    run_id: str
    decision_context: DecisionContext
    shared_fields: V3SharedFields
    input_digest: str


def _now(case: V3CaseSpec, offset: int = 0) -> datetime:
    return case.evaluated_at + timedelta(milliseconds=offset)


def _selector_version(architecture: V3Architecture) -> str:
    return f"v3.{architecture}.selector-prep.v1"


def _payload_for_case(case: V3CaseSpec, tool_name: str) -> dict[str, Any]:
    sid = case.scenario_id
    if sid == "v3b-location-fact":
        return {
            "pod_status": "received_by_other",
            "recipient_category": "front_desk",
            "delivery_location": "front_desk",
        }
    if "order-not-delivered" in sid:
        return {"order_status": "in_transit"}
    if "pod-reception" in sid:
        return {"pod_status": "received_by_other"}
    if "pod-absent" in sid:
        return {"pod_status": "not_found"}
    if "pod-nonreception" in sid:
        return {"pod_status": "signed"}
    if "stall-within" in sid:
        return {"hours_since_last_update": 12}
    if "stall-severe" in sid:
        return {"hours_since_last_update": 96}
    if "policy-applicable" in sid:
        return {"policy_resolution_status": "applicable", "retrieval_status": "hit"}
    if "policy-ineligible" in sid:
        return {"policy_resolution_status": "not_applicable", "retrieval_status": "hit"}
    if "policy-no-hit" in sid:
        return {"retrieval_status": "no_hit"}
    if "policy-conflict" in sid:
        return {"policy_resolution_status": "version_conflict", "retrieval_status": "hit"}
    if "policy-unavailable" in sid:
        return {"retrieval_status": "unavailable"}
    if "active-ticket" in sid:
        return {"active_ticket_status": "active"}
    if "no-active-ticket" in sid:
        return {"active_ticket_status": "none"}
    if tool_name == "get_order_context":
        return {"order_status": "shipped"}
    return {"observed": True}


def _shared_input(case: V3CaseSpec, run_id: str) -> SharedPairInput:
    progress = EvidenceProgressReducer().initial(
        case_id=case.pair_id,
        run_id=run_id,
        canonical_issue_type=case.issue,
        rebuilt_at=case.evaluated_at,
    )
    context = DecisionContext(
        case_id=case.pair_id,
        run_id=run_id,
        canonical_issue_type=case.issue,
        authorized_order_id=_ORDER_ID,
        customer_message="synthetic V3 preparation message",
        evidence_progress=progress,
        allowed_tools=tuple(case.initial_observation),
        remaining_budget=BudgetSnapshot(case_planning_turns=0, run_planning_turns=0, actual_read_tool_executions=0),
        prompt_policy_version="prep-no-prompt-policy-v1",
    )
    payload = {
        "case": case.model_dump(mode="json"),
        "run_id": run_id,
        "decision_context": context.model_dump(mode="json"),
        "shared_fields": case.shared_fields.model_dump(mode="json"),
        "tool_registry": tuple(case.initial_observation),
        "executor": "shared-project-executor-v2",
    }
    return SharedPairInput(
        case=case,
        run_id=run_id,
        decision_context=context,
        shared_fields=case.shared_fields,
        input_digest=sha256_json(payload),
    )


def _call(
    pair: SharedPairInput,
    *,
    trace_run_id: str | None = None,
    architecture: V3Architecture,
    sequence: int,
    tool_name: str,
    planning_turn: int,
    attempt: int,
    before: int,
    status: Literal["success", "retryable_error", "non_retryable_error"] = "success",
    availability: Literal["present", "absent", "unavailable"] = "present",
    payload: Mapping[str, Any] | None = None,
    progress_status: str | None = None,
) -> V3ToolCall:
    run_id = trace_run_id or pair.run_id
    args = {"order_id": _ORDER_ID}
    body = dict(payload or {})
    result_hash = sha256_json(body)
    observed = _now(pair.case, sequence)
    envelope = V3ToolResultEnvelope(
        execution_status=status,
        evidence_availability=availability,
        source_type="synthetic_fixture",
        source_query_id=f"q-{run_id}-{sequence}",
        source_record_ids=(f"record-{pair.case.pair_id}",),
        observed_at=observed,
        payload=body if status == "success" else None,
        error_code="TRANSIENT_FIXTURE_FAILURE" if status == "retryable_error" else None,
        retryable=status == "retryable_error",
        result_hash=result_hash,
    )
    refs: tuple[EvidenceRef, ...] = ()
    if status == "success":
        refs = (
            EvidenceRef(
                tool_call_id=f"tc-{run_id}-{sequence}",
                source_query_id=envelope.source_query_id,
                source_record_id=envelope.source_record_ids[0],
                field_path="payload",
                observed_at=observed,
                result_hash=result_hash,
            ),
        )
    return V3ToolCall(
        tool_call_id=f"tc-{run_id}-{sequence}",
        case_id=pair.case.pair_id,
        run_id=run_id,
        tool_name=tool_name,
        normalized_args=args,
        planning_turn=planning_turn,
        attempt_number=attempt,
        actual_execution=True,
        execution_status=status,
        evidence_availability=availability,
        result_envelope=envelope,
        result_hash=result_hash,
        source_version=_SOURCE_VERSION,
        retryable=status == "retryable_error",
        trace_sequence=sequence,
        progress_status=progress_status,
        budget_before_actual_reads=before,
        budget_after_actual_reads=before + 1,
        evidence_refs=refs,
    )


def _decision(
    pair: SharedPairInput,
    *,
    trace_run_id: str | None = None,
    architecture: V3Architecture,
    sequence: int,
    planning_turn: int,
    action: ObservationAction,
    tool_name: str | None,
    reason: str,
    validation: CandidateValidationStatus = CandidateValidationStatus.ACCEPTED,
    rejection_code: str | None = None,
    progress_hash: str | None = None,
) -> DecisionTraceRecord:
    run_id = trace_run_id or pair.run_id
    args = {"order_id": _ORDER_ID} if tool_name else {}
    digest = progress_hash or sha256_json({"case": pair.case.pair_id, "revision": 0})
    return DecisionTraceRecord(
        trace_sequence=sequence,
        case_id=pair.case.pair_id,
        run_id=run_id,
        decision_id=f"decision-{run_id}-{sequence}",
        selector_kind=SelectorKind(architecture),
        planning_turn=planning_turn,
        action=action,
        tool_name=tool_name,
        canonical_arguments_hash=canonical_arguments_hash(args),
        addresses=(EvidenceRequirementCode.ORDER_STATUS,) if tool_name else (),
        reason_code=reason,
        validation_status=validation,
        rejection_code=rejection_code,
        evidence_progress_revision=0,
        evidence_progress_hash=digest,
        budget_snapshot=BudgetSnapshot(case_planning_turns=planning_turn, run_planning_turns=planning_turn, actual_read_tool_executions=0),
        model_id=None,
        prompt_policy_version="prep-no-prompt-policy-v1",
        recorded_at=_now(pair.case, sequence),
    )


def _recovery(
    pair: SharedPairInput,
    *,
    trace_run_id: str | None = None,
    sequence: int,
    route: RecoveryRoute,
    reason: RecoveryReasonCode,
    trigger: V3ToolCall | None = None,
    before_reads: int = 0,
    after_reads: int = 0,
    retry_identity_hash: str | None = None,
    attempt: int | None = None,
) -> RecoveryTraceRecord:
    run_id = trace_run_id or pair.run_id
    progress_hash = sha256_json({"case": pair.case.pair_id, "reads": before_reads})
    return RecoveryTraceRecord(
        trace_sequence=sequence,
        case_id=pair.case.pair_id,
        run_id=run_id,
        recovery_id=f"recovery-{run_id}-{sequence}",
        trigger_tool_call_id=trigger.tool_call_id if trigger else None,
        trigger_result_hash=trigger.result_hash if trigger else None,
        execution_status=trigger.execution_status if trigger else "success",
        evidence_availability=trigger.evidence_availability if trigger else "present",
        retryable=bool(trigger and trigger.retryable),
        error_code=trigger.result_envelope.error_code if trigger else None,
        evidence_progress_before_hash=progress_hash,
        evidence_progress_after_hash=sha256_json({"case": pair.case.pair_id, "reads": after_reads}),
        route=route,
        reason_code=reason,
        retry_identity_hash=retry_identity_hash,
        attempt_number=attempt,
        budget_snapshot=BudgetSnapshot(case_planning_turns=1, run_planning_turns=1, actual_read_tool_executions=after_reads),
        recorded_at=_now(pair.case, sequence),
    )


def _state(
    pair: SharedPairInput,
    sequence: int,
    phase: TracePhase,
    reason: str,
    *,
    trace_run_id: str | None = None,
) -> StateTraceRecord:
    run_id = trace_run_id or pair.run_id
    return StateTraceRecord(
        trace_sequence=sequence,
        case_id=pair.case.pair_id,
        run_id=run_id,
        phase_from=TracePhase.ROUTE,
        phase_to=phase,
        reason_code=reason,
        case_revision=0,
        run_revision=0,
        evidence_progress_hash=sha256_json({"case": pair.case.pair_id, "seq": sequence}),
        persisted_at=_now(pair.case, sequence),
    )


def _facts(
    pair: SharedPairInput,
    tool_calls: tuple[V3ToolCall, ...],
) -> tuple[
    tuple[CaseFactAssertion, ...],
    tuple[CaseFactSnapshot, ...],
    tuple[V3QuestionTrace, ...],
    tuple[V3ConsumptionTrace, ...],
]:
    case = pair.case
    if case.family_kind != "v3b":
        return (), (), (), ()
    source_message_id = case.customer_message_ids[0]
    recorded = _now(case, 10)
    branch = case.fact_branch or ""
    code = (
        FactCode.REPORTED_DELIVERY_LOCATION_CHECKED
        if branch == "location_bound_true_not_reasked"
        else FactCode.CUSTOMER_STILL_REPORTS_MISSING
    )
    context_call = tool_calls[0] if code is FactCode.REPORTED_DELIVERY_LOCATION_CHECKED and tool_calls else None
    source_hash = message_hash("synthetic V3 preparation message")
    value = FactValue.UNKNOWN if branch == "unknown_is_not_false_or_repeat" else FactValue.TRUE
    relation = RelationHint.REPEAT if branch == "repeat_preserves_provenance" else RelationHint.NEW
    assertion_id = f"assert-{pair.run_id}-1"

    def make_assertion(
        assertion_number: int,
        assertion_value: FactValue,
        assertion_relation: RelationHint,
        assertion_source: str,
        supersedes: str | None = None,
    ) -> CaseFactAssertion:
        return CaseFactAssertion(
            assertion_id=f"assert-{pair.run_id}-{assertion_number}",
            case_id=case.pair_id,
            fact_code=code,
            value=assertion_value,
            source_message_id=assertion_source,
            source_message_hash=source_hash,
            source_span_start=0,
            source_span_end=8,
            relation=assertion_relation,
            supersedes_assertion_id=supersedes,
            extractor_kind="deterministic",
            extractor_version="v3b-prep-fixture-v1",
            context_tool_call_id=context_call.tool_call_id if context_call else None,
            context_result_hash=context_call.result_hash if context_call else None,
            assertion_sequence=assertion_number,
            recorded_at=recorded,
        )

    assertions = [make_assertion(1, value, relation, source_message_id)]
    if branch in {"validated_correction_supersedes_one", "opposite_without_cue_conflict", "one_targeted_disambiguation_max"}:
        second_value = FactValue.FALSE if branch != "validated_correction_supersedes_one" else FactValue.TRUE
        second_relation = RelationHint.CORRECTION if branch == "validated_correction_supersedes_one" else RelationHint.NEW
        second_source = case.customer_message_ids[1]
        assertions.append(make_assertion(2, second_value, second_relation, second_source, assertion_id if second_relation is RelationHint.CORRECTION else None))
    if branch == "foreign_or_non_customer_source_rejected":
        assertions = []
    assertion_tuple = tuple(assertions)
    active_ids = tuple(item.assertion_id for item in assertion_tuple)
    if branch == "validated_correction_supersedes_one" and len(assertion_tuple) == 2:
        active_ids = (assertion_tuple[1].assertion_id,)
    conflict = branch in {"opposite_without_cue_conflict", "one_targeted_disambiguation_max"}
    unknown = branch in {"unknown_is_not_false_or_repeat", "foreign_or_non_customer_source_rejected"}
    facts = {
        fact: FactSnapshotEntry(
            status=(FactStatus.CONFLICT if conflict else FactStatus.UNKNOWN if unknown else FactStatus.KNOWN_TRUE) if fact is code else FactStatus.UNKNOWN,
            active_assertion_ids=active_ids if fact is code and not unknown else (),
            superseded_assertion_ids=(assertion_tuple[0].assertion_id,) if fact is code and branch == "validated_correction_supersedes_one" else (),
            source_message_ids=tuple(item.source_message_id for item in assertion_tuple) if fact is code else (),
            context_tool_call_id=context_call.tool_call_id if fact is code and context_call else None,
            context_result_hash=context_call.result_hash if fact is code and context_call else None,
        )
        for fact in FactCode
    }
    questions = {
        fact: QuestionState(
            asks=1 if branch == "one_targeted_disambiguation_max" else 0,
            status=(QuestionStatus.CONFLICT_REQUIRES_CLARIFICATION if conflict else QuestionStatus.UNKNOWN_EXHAUSTED if unknown else QuestionStatus.ANSWERED)
            if fact is code
            else QuestionStatus.UNANSWERED,
        )
        for fact in FactCode
    }
    snapshot = CaseFactSnapshot(
        case_id=case.pair_id,
        revision=len(assertion_tuple),
        facts=facts,
        question_state=questions,
        snapshot_hash=sha256_json({"case": case.pair_id, "assertions": [item.assertion_id for item in assertion_tuple]}),
        rebuilt_at=recorded,
    )
    question_trace: tuple[V3QuestionTrace, ...] = ()
    ledger: tuple[V3ConsumptionTrace, ...] = ()
    if branch == "question_and_message_replay_idempotent":
        question_id = f"question-{pair.run_id}-1"
        question_trace = (
            V3QuestionTrace(question_id=question_id, case_id=case.pair_id, fact_code=code.value, status="asked", source_message_id=source_message_id),
            V3QuestionTrace(question_id=f"question-{pair.run_id}-2", case_id=case.pair_id, fact_code=code.value, status="replayed", source_message_id=source_message_id, repeat=True),
        )
        ledger = (
            V3ConsumptionTrace(question_id=question_id, source_message_id=source_message_id, outcome="accepted", candidate_batch_hash=sha256_json({"batch": 1}), assertion_id=assertion_id),
            V3ConsumptionTrace(question_id=f"question-{pair.run_id}-2", source_message_id=source_message_id, outcome="accepted", candidate_batch_hash=sha256_json({"batch": 1}), assertion_id=assertion_id),
        )
    elif branch == "one_targeted_disambiguation_max":
        question_trace = (V3QuestionTrace(question_id=f"question-{pair.run_id}-1", case_id=case.pair_id, fact_code=code.value, status="asked", source_message_id=source_message_id),)
        ledger = (V3ConsumptionTrace(question_id=f"question-{pair.run_id}-1", source_message_id=source_message_id, outcome="empty", candidate_batch_hash=sha256_json({"batch": 0})),)
    elif branch == "foreign_or_non_customer_source_rejected":
        ledger = (V3ConsumptionTrace(question_id=f"rejected-{pair.run_id}", source_message_id="foreign-message", outcome="rejected", candidate_batch_hash=sha256_json({"rejected": True})),)
    return assertion_tuple, (snapshot,), question_trace, ledger


class V3PairedRunner:
    """Run one deterministic paired case with one shared input envelope."""

    def __init__(
        self,
        *,
        shared_overrides: Mapping[str, Mapping[str, Any]] | None = None,
        failure_injections: Mapping[str, str] | None = None,
    ) -> None:
        self.shared_overrides = shared_overrides or {}
        self.failure_injections = failure_injections or {}

    def _inject_failure(self, record: V3RunRecord) -> V3RunRecord:
        requested = self.failure_injections.get(f"{record.scenario_id}:{record.architecture}")
        if requested is None:
            requested = self.failure_injections.get(record.scenario_id)
        if requested is None:
            return record
        status_by_kind = {
            "timeout": ("timeout", "timeout", "TIMEOUT"),
            "schema": ("schema_failure", "schema", "SCHEMA_FAILURE"),
            "provider": ("provider_failure", "provider", "PROVIDER_FAILURE"),
            "grader": ("grader_failure", "grader", "GRADER_FAILURE"),
        }
        if requested not in status_by_kind:
            raise ValueError(f"unknown synthetic failure injection: {requested}")
        status, error_class, error_code = status_by_kind[requested]
        return V3RunRecord.model_validate(
            record.model_dump(mode="json")
            | {
                "run_status": status,
                "quality_pass": False,
                "error_code": error_code,
                "error_class": error_class,
            }
        )

    def _run_one(self, pair: SharedPairInput, architecture: V3Architecture, repetition: int) -> V3RunRecord:
        case = pair.case
        run_id = f"{case.scenario_id}-{architecture}-r{repetition}"
        shared_payload = {
            "input_digest": pair.input_digest,
            "shared_fields": pair.shared_fields.model_dump(mode="json"),
        }
        shared_payload.update(self.shared_overrides.get(architecture, {}))
        shared_digest = sha256_json(shared_payload)
        decisions: list[DecisionTraceRecord] = []
        recoveries: list[RecoveryTraceRecord] = []
        states: list[StateTraceRecord] = []
        calls: list[V3ToolCall] = []
        sid = case.scenario_id
        guard_reason: str | None = None
        if "guards-malformed" in sid:
            guard_reason = "INVALID_CANDIDATE_SCHEMA"
        elif "guards-irrelevant" in sid:
            guard_reason = "INVALID_OBSERVATION"
        elif "guards-duplicate" in sid:
            guard_reason = "STUCK_REPEATED_DECISION"
        elif "guards-premature" in sid:
            guard_reason = "PREMATURE_FINISH"
        elif "guards-stuck" in sid:
            guard_reason = "STUCK_NO_EVIDENCE_PROGRESS"
        elif "guards-budget" in sid:
            guard_reason = "BUDGET_EXHAUSTED"
        elif "guards-source-change" in sid:
            guard_reason = "SOURCE_REVISION_CHANGED_DURING_RETRY"
        if guard_reason:
            decisions.append(_decision(pair, trace_run_id=run_id, architecture=architecture, sequence=1, planning_turn=1, action=ObservationAction.FINISH, tool_name=None, reason=guard_reason, validation=CandidateValidationStatus.REJECTED, rejection_code=guard_reason))
            recovery_reason = RecoveryReasonCode(guard_reason) if guard_reason in {item.value for item in RecoveryReasonCode} else RecoveryReasonCode.INVALID_OBSERVATION
            recoveries.append(_recovery(pair, trace_run_id=run_id, sequence=2, route=RecoveryRoute.SAFE_STOP, reason=recovery_reason))
            states.append(_state(pair, 3, TracePhase.SAFE_STOP, recovery_reason.value, trace_run_id=run_id))
        else:
            tool_name = case.initial_observation[0]
            if "active-ticket" in sid or "no-active-ticket" in sid:
                tool_name = "get_existing_logistics_tickets"
            elif "policy-" in sid:
                tool_name = "search_after_sales_policy"
            elif "pod-" in sid:
                tool_name = "get_delivery_proof"
            elif "stall-" in sid:
                tool_name = "get_logistics_timeline"
            decision_reason = "FIRST_REQUIRED_OBSERVATION"
            if "pod-reception-proof" in sid:
                decision_reason = "OBSERVATION_CONDITIONAL_BRANCH"
            elif "pod-absent-proof" in sid:
                decision_reason = "MISSING_REQUIRED_EVIDENCE"
            decisions.append(_decision(pair, trace_run_id=run_id, architecture=architecture, sequence=1, planning_turn=1, action=ObservationAction.CALL_TOOL, tool_name=tool_name, reason=decision_reason))
            retry_case = "exact-retry" in sid
            if retry_case:
                first = _call(pair, trace_run_id=run_id, architecture=architecture, sequence=2, tool_name=tool_name, planning_turn=1, attempt=1, before=0, status="retryable_error", availability="unavailable")
                calls.append(first)
                retry_hash = sha256_json({"tool": tool_name, "args": dict(first.normalized_args), "source": first.source_version})
                recoveries.append(_recovery(pair, trace_run_id=run_id, sequence=3, route=RecoveryRoute.RETRY_EXACT, reason=RecoveryReasonCode.RETRYABLE_TOOL_FAILURE, trigger=first, before_reads=0, after_reads=1, retry_identity_hash=retry_hash, attempt=1))
                second = _call(pair, trace_run_id=run_id, architecture=architecture, sequence=4, tool_name=tool_name, planning_turn=1, attempt=2, before=1, payload=_payload_for_case(case, tool_name))
                calls.append(second)
                recoveries.append(_recovery(pair, trace_run_id=run_id, sequence=5, route=RecoveryRoute.FINALIZE, reason=RecoveryReasonCode.GATE_READY, trigger=second, before_reads=2, after_reads=2))
                states.append(_state(pair, 6, TracePhase.FINALIZE, "GATE_READY", trace_run_id=run_id))
            else:
                failure = "persistent-failure" in sid
                status: Literal["success", "retryable_error", "non_retryable_error"] = "non_retryable_error" if failure else "success"
                availability: Literal["present", "absent", "unavailable"] = "unavailable" if failure else "present"
                progress_status = "unavailable_final" if failure else "satisfied_present"
                first = _call(pair, trace_run_id=run_id, architecture=architecture, sequence=2, tool_name=tool_name, planning_turn=1, attempt=1, before=0, status=status, availability=availability, payload=_payload_for_case(case, tool_name), progress_status=progress_status)
                calls.append(first)
                route = RecoveryRoute.SAFE_STOP if failure else RecoveryRoute.FINALIZE
                reason = RecoveryReasonCode.STUCK_NO_EVIDENCE_PROGRESS if failure else RecoveryReasonCode.GATE_READY
                recoveries.append(_recovery(pair, trace_run_id=run_id, sequence=3, route=route, reason=reason, trigger=first, before_reads=0, after_reads=1))
                states.append(_state(pair, 4, TracePhase.SAFE_STOP if failure else TracePhase.FINALIZE, reason.value, trace_run_id=run_id))
        call_tuple = tuple(calls)
        facts, snapshots, questions, ledger = _facts(pair, call_tuple)
        progress = V3ProgressRebuild(
            case_id=case.pair_id,
            run_id=run_id,
            progress_revision=len(call_tuple),
            online_snapshot_hash=sha256_json({"case": case.pair_id, "calls": [call.tool_call_id for call in call_tuple]}),
            replayed_snapshot_hash=sha256_json({"case": case.pair_id, "calls": [call.tool_call_id for call in call_tuple]}),
            tool_call_ids=tuple(call.tool_call_id for call in call_tuple),
            evidence_ref_ids=tuple(ref.tool_call_id for call in call_tuple for ref in call.evidence_refs),
            progress_requirements={"DELIVERY_PROOF": "unavailable_final"} if "persistent-failure" in sid else {},
        )
        final_outcome: V3GateOutcome = case.allowed_deterministic_outcomes[0]
        if guard_reason:
            final_outcome = "safe_stop"
        trace = V3TypedTrace(
            decisions=tuple(decisions),
            recoveries=tuple(recoveries),
            states=tuple(states),
            tool_calls=call_tuple,
            progress_rebuilds=(progress,),
            gate_decisions=(V3GateTrace(decision=final_outcome, reason_code="PREP_DRY_RUN", allowed=True, evidence_progress_hash=progress.online_snapshot_hash),),
            fact_assertions=facts,
            fact_snapshots=snapshots,
            questions=questions,
            consumption_ledger=ledger,
        )
        grading = execute_v3_graders(V3GradingContext(case=case, trace=trace, final_outcome=final_outcome, safety_gate_pass=True, case_scope_id=case.pair_id), case.expected_grader_ids)
        quality_pass = all(item.passed for item in grading)
        triggered = tuple(obligation.obligation_id for obligation in case.trajectory_obligations if _obligation_triggered(obligation, trace))
        failed = tuple(item.obligation_id for item in case.trajectory_obligations if item.obligation_id in triggered and not quality_pass)
        metrics = V3Metrics(
            actual_reads=sum(call.actual_execution for call in call_tuple),
            cache_hits=sum(call.cache_hit for call in call_tuple),
            unnecessary_reads=0,
            retry_attempts=sum(call.attempt_number == 2 for call in call_tuple),
            retry_recovered=any(call.attempt_number == 2 and call.execution_status == "success" for call in call_tuple),
            stuck_or_safe_stop=any(item.route is RecoveryRoute.SAFE_STOP for item in recoveries),
            rebuild_parity=progress.online_snapshot_hash == progress.replayed_snapshot_hash,
            clarification_questions=len(questions),
            repeated_questions=sum(item.repeat for item in questions),
            latency_ms=float(len(call_tuple) * 3 + len(decisions)),
            model_calls=0,
            provider_calls=0,
            cost="unavailable",
        )
        return V3RunRecord(
            eval_run_id=run_id,
            execution_identity="V3-PREP-DRY-RUN-001",
            manifest_id="V3A-EVAL-DEV-001" if case.family_kind == "v3a" else "V3B-EVAL-DEV-001",
            evaluation_revision=PREP_EVALUATION_REVISION,
            scenario_id=case.scenario_id,
            pair_id=case.pair_id,
            family=case.family,
            architecture=architecture,
            repetition=repetition,
            run_status="completed" if quality_pass else "grader_failure",
            started_at=case.evaluated_at,
            completed_at=_now(case, 20),
            quality_pass=quality_pass,
            safety_gate_pass=all(item.passed for item in grading if item.grader_id == "GR-V3A-13") and True,
            final_outcome=final_outcome,
            triggered_obligations=triggered,
            failed_obligations=failed,
            metrics=metrics,
            trace=trace,
            shared_input_digest=shared_digest,
            shared_component_versions={"shared_runtime": "v3.shared-prep-runtime.v1", "fixture": case.fixture_revision, "source": case.source_revision, "budget": case.shared_fields.budget_version, "cache": case.shared_fields.cache_revision, "router": case.shared_fields.router_version, "gate": case.shared_fields.evidence_gate_version, "grader": case.shared_fields.grader_registry_version},
            selector_version=_selector_version(architecture),
            error_code="GRADER_FAILURE" if not quality_pass else None,
            error_class="grader" if not quality_pass else "none",
        )

    def run_case_pair(self, case: V3CaseSpec, *, repetition: int = 1) -> tuple[V3RunRecord, V3RunRecord]:
        if repetition < 1:
            raise ValueError("repetition must be positive")
        run_id = f"{case.scenario_id}-pair-r{repetition}"
        shared = _shared_input(case, run_id)
        agent = self._inject_failure(self._run_one(shared, "agent", repetition))
        workflow = self._inject_failure(self._run_one(shared, "workflow", repetition))
        if agent.shared_input_digest != workflow.shared_input_digest or dict(agent.shared_component_versions) != dict(workflow.shared_component_versions):
            raise FairnessViolation(f"Agent/Workflow shared input mismatch for {case.pair_id}")
        return agent, workflow


def run_prep_dry_run(
    *,
    root: Path | None = None,
    store_root: Path | None = None,
    failure_injections: Mapping[str, str] | None = None,
) -> tuple[V3DevelopmentReport, tuple[V3RunRecord, ...], V3PrepStore]:
    """Validate reserved manifests and execute the provider-free complete plan."""

    validate_matrix()
    cases = load_matrix(root)
    manifests = load_manifests(root)
    cases_by_id = {case.scenario_id: case for case in cases}
    project = root or Path(__file__).resolve().parents[4]
    output = store_root or project / "var" / "v3" / "prep" / "dry-run" / "V3-PREP-DRY-RUN-001"
    store = V3PrepStore(output)
    runner = V3PairedRunner(failure_injections=failure_injections)
    records: list[V3RunRecord] = []
    for manifest in manifests:
        for case_id in manifest.case_ids:
            case = cases_by_id[case_id]
            for repetition in range(1, manifest.planned_repetitions + 1):
                pair = runner.run_case_pair(case, repetition=repetition)
                for record in pair:
                    store.save_run(record)
                    records.append(record)
    store.validate_completeness(manifests, cases_by_id, expected_execution_identity="V3-PREP-DRY-RUN-001")
    report = build_development_report(
        manifests,
        cases_by_id,
        records,
        execution_identity="V3-PREP-DRY-RUN-001",
        evaluation_revision=PREP_EVALUATION_REVISION,
        report_id=DRY_RUN_REPORT_ID,
        created_at=cases[0].evaluated_at,
    )
    store.save_report(report)
    return report, tuple(records), store


__all__ = [
    "DRY_RUN_REPORT_ID",
    "FairnessViolation",
    "PREP_EVALUATION_REVISION",
    "SharedPairInput",
    "V3PairedRunner",
    "run_prep_dry_run",
]

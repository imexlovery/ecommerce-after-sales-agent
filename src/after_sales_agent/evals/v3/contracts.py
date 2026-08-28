# ruff: noqa: E501
"""Fail-closed, versioned contracts for the V3 paired Development Eval.

This module is deliberately independent from the historical V2 evaluation
contracts.  It imports the already-typed V3-A/B domain records, but never
changes their authority or writes a V2 artifact.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from after_sales_agent.application.adaptive_core import (
    DecisionTraceRecord,
    RecoveryTraceRecord,
    StateTraceRecord,
)
from after_sales_agent.domain.case_facts import CaseFactAssertion, CaseFactSnapshot
from after_sales_agent.domain.state import IssueType
from after_sales_agent.tools.contracts import EvidenceRef

V3_SCHEMA_VERSION: Final = "v3.eval.contracts.v1"
V3A_EVAL_DEV_IDENTITY: Final = "V3A-EVAL-DEV-001"
V3B_EVAL_DEV_IDENTITY: Final = "V3B-EVAL-DEV-001"
V3_PREP_IDENTITY: Final = "V3-PREP-DRY-RUN-001"
V3A_CASE_MATRIX_ID: Final = "V3A-CASE-MATRIX-001"
V3B_CASE_MATRIX_ID: Final = "V3B-CASE-MATRIX-001"
V3_SOURCE_REVISION: Final = "68767c2ebdbdefc7621d950f726946b74ab52c9f"
V3_EVALUATED_AT: Final = "2026-08-28T12:00:00+00:00"

V3Architecture = Literal["agent", "workflow"]
V3FamilyKind = Literal["v3a", "v3b"]
V3RunStatus = Literal[
    "completed",
    "timeout",
    "schema_failure",
    "provider_failure",
    "provider_budget_exhausted",
    "provider_invocation_incomplete",
    "token_threshold_exhausted",
    "token_usage_unavailable",
    "grader_failure",
    "error",
]
V3GateOutcome = Literal[
    "propose_ticket",
    "request_business_clarification",
    "retry_later",
    "require_human_support",
    "complete_no_action",
    "issue_revision",
    "safe_stop",
    "unknown",
]


class V3Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def fault_seed_hash(seed: str) -> str:
    """Derive a reproducibility identity without retaining the seed in traces."""

    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


_TOOL_NAMES = frozenset(
    {
        "get_order_context",
        "get_logistics_timeline",
        "get_delivery_proof",
        "get_carrier_service_alerts",
        "search_after_sales_policy",
        "get_existing_logistics_tickets",
    }
)
_PREDICATE_OPERATORS = frozenset(
    {"equals", "not_equals", "in", "not_in", "exists", "not_exists", "gt", "gte", "lt", "lte"}
)
_PREDICATE_PATHS = frozenset(
    {
        "tool_name",
        "execution_status",
        "evidence_availability",
        "attempt_number",
        "actual_execution",
        "cache_hit",
        "blocked",
        "source_version",
        "result_hash",
        "planning_turn",
        "payload.order_status",
        "payload.pod_status",
        "payload.hours_since_last_update",
        "payload.retrieval_status",
        "payload.policy_resolution_status",
        "progress.gate_readiness",
        "progress.requirements.ORDER_STATUS.status",
        "progress.requirements.TRACKING_TIMELINE.status",
        "progress.requirements.DELIVERY_PROOF.status",
        "progress.requirements.POLICY_APPLICABILITY.status",
        "progress.requirements.ACTIVE_TICKET_STATUS.status",
        "route",
        "reason_code",
        "phase_to",
        "decision.action",
        "decision.tool_name",
        "decision.validation_status",
        "gate.decision",
        "fact.code",
        "fact.value",
        "fact.relation",
        "question.status",
    }
)


class V3Predicate(V3Contract):
    field_path: str = Field(min_length=1, max_length=128)
    operator: str = Field(min_length=1, max_length=32)
    value: Any = None

    @model_validator(mode="after")
    def validate_registry(self) -> V3Predicate:
        if self.field_path not in _PREDICATE_PATHS:
            raise ValueError(f"unknown trajectory field_path: {self.field_path}")
        if self.operator not in _PREDICATE_OPERATORS:
            raise ValueError(f"unknown trajectory operator: {self.operator}")
        if self.operator in {"exists", "not_exists"} and self.value is not None:
            raise ValueError(f"{self.operator} predicates must omit value")
        return self


class V3ObligationEffect(V3Contract):
    allowed_routes: tuple[str, ...] = Field(default_factory=tuple)
    required_next_route: str | None = None
    exact_retry: bool = False
    forbidden_future_tools: tuple[str, ...] = Field(default_factory=tuple)
    max_additional_actual_reads: int | None = Field(default=None, ge=0, le=6)
    required_decision_codes: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_effect(self) -> V3ObligationEffect:
        routes = set(self.allowed_routes)
        if self.required_next_route is not None:
            routes.add(self.required_next_route)
        if not routes.issubset({"replan", "retry_exact", "finalize", "safe_stop"}):
            raise ValueError("trajectory effects contain an unknown route")
        unknown_tools = set(self.forbidden_future_tools).difference(_TOOL_NAMES)
        if unknown_tools:
            raise ValueError(f"trajectory effect contains unknown tools: {sorted(unknown_tools)}")
        return self


class V3TrajectoryObligation(V3Contract):
    obligation_id: str = Field(pattern=r"^OBL-V3-[A-Z0-9-]{3,80}$")
    when: tuple[V3Predicate, ...] = Field(min_length=1)
    then: V3ObligationEffect


class V3SharedFields(V3Contract):
    """Every field outside selector implementation that must be pair-equal."""

    decision_context_version: str = "v3a.decision_context.v1"
    fixture_revision: str = Field(min_length=1)
    source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    fault_seed_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluated_at: datetime
    budget_version: str = Field(min_length=1)
    cache_revision: str = Field(min_length=1)
    tool_registry_version: str = Field(min_length=1)
    validator_version: str = Field(min_length=1)
    router_version: str = Field(min_length=1)
    reducer_version: str = Field(min_length=1)
    evidence_gate_version: str = Field(min_length=1)
    response_layer_version: str = Field(min_length=1)
    executor_version: str = Field(min_length=1)
    grader_registry_version: str = Field(min_length=1)
    timeout_seconds: float = Field(gt=0)
    repeat: int = Field(ge=1, le=3)
    selector_turn_ceiling: int = Field(default=8, ge=1, le=16)
    provider_call_ceiling: int = Field(default=8, ge=0, le=16)
    token_ceiling: int | None = Field(default=None, ge=1)
    token_ceiling_config: str = Field(default="V3_TOKEN_CEILING", min_length=1)
    token_threshold_semantics: Literal[
        "cumulative_observed_total_tokens_post_response_stop"
    ] = "cumulative_observed_total_tokens_post_response_stop"
    output_token_cap_per_invocation: int = Field(default=512, gt=0)
    hard_token_ceiling: Literal[False] = False
    overshoot_bound_provable: Literal[False] = False
    provider_hard_ceiling: Literal[True] = True
    provider_call_semantics: Literal["pre_call_admitted_outer_ainvoke_attempt"] = (
        "pre_call_admitted_outer_ainvoke_attempt"
    )
    provider_retry_policy: Literal[
        "sdk_retries_disabled_internal_transport_attempts_not_observable"
    ] = "sdk_retries_disabled_internal_transport_attempts_not_observable"

    @model_validator(mode="after")
    def validate_time(self) -> V3SharedFields:
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must be timezone-aware")
        return self


class V3CaseSpec(V3Contract):
    """One observation-conditioned case; Agent and Workflow share this object."""

    schema_version: Literal["v3.case.v1"] = "v3.case.v1"
    scenario_id: str = Field(pattern=r"^v3[ab]-[a-z0-9][a-z0-9_-]{2,95}$")
    pair_id: str = Field(pattern=r"^pair-v3-[a-z0-9][a-z0-9_-]{2,95}$")
    family: str = Field(pattern=r"^DEV-V3[AB]-[A-Z0-9-]{3,64}$")
    family_kind: V3FamilyKind
    issue: IssueType
    fixture_revision: str = Field(min_length=1)
    source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    evaluated_at: datetime
    fault_seed_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    initial_observation: tuple[str, ...] = Field(min_length=1)
    trajectory_obligations: tuple[V3TrajectoryObligation, ...] = Field(default_factory=tuple)
    allowed_deterministic_outcomes: tuple[V3GateOutcome, ...] = Field(min_length=1)
    hard_safety_expectations: tuple[str, ...] = Field(min_length=1)
    shared_fields: V3SharedFields
    expected_grader_ids: tuple[str, ...] = Field(min_length=1)
    customer_message_ids: tuple[str, ...] = Field(default_factory=tuple)
    fact_branch: str | None = None

    @model_validator(mode="after")
    def validate_case(self) -> V3CaseSpec:
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must be timezone-aware")
        if any(tool not in _TOOL_NAMES for tool in self.initial_observation):
            raise ValueError("initial_observation contains an unknown read tool")
        if len(set(self.expected_grader_ids)) != len(self.expected_grader_ids):
            raise ValueError("expected_grader_ids must be unique")
        if self.shared_fields.fixture_revision != self.fixture_revision:
            raise ValueError("case/shared fixture revision mismatch")
        if self.shared_fields.source_revision != self.source_revision:
            raise ValueError("case/shared source revision mismatch")
        if self.shared_fields.fault_seed_hash != self.fault_seed_hash:
            raise ValueError("case/shared fault identity mismatch")
        if self.shared_fields.evaluated_at != self.evaluated_at:
            raise ValueError("case/shared evaluated_at mismatch")
        return self


class V3ManifestHeader(V3Contract):
    schema_version: Literal["v3.eval.manifest.v1"] = "v3.eval.manifest.v1"
    manifest_id: Literal["V3A-EVAL-DEV-001", "V3B-EVAL-DEV-001"]
    matrix_id: Literal["V3A-CASE-MATRIX-001", "V3B-CASE-MATRIX-001"]
    dataset_revision: str = Field(min_length=1)
    source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    fixture_revision: str = Field(min_length=1)
    budget_version: str = Field(min_length=1)
    grader_registry_version: str = Field(min_length=1)
    provider_mode: Literal["mock_or_live_later"] = "mock_or_live_later"
    formal_measurement_authorized: Literal[False] = False


class V3DevelopmentManifest(V3ManifestHeader):
    """Reserved Development identity; it cannot assert that a run happened."""

    case_ids: tuple[str, ...] = Field(min_length=1)
    planned_repetitions: int = Field(ge=1, le=3)
    planned_architectures: tuple[V3Architecture, ...] = ("agent", "workflow")
    execution_status: Literal["reserved_not_executed"] = "reserved_not_executed"

    @model_validator(mode="after")
    def validate_identity(self) -> V3DevelopmentManifest:
        if len(set(self.case_ids)) != len(self.case_ids):
            raise ValueError("manifest case IDs must be unique")
        if tuple(self.planned_architectures) != ("agent", "workflow"):
            raise ValueError("paired Development manifests require Agent and Workflow")
        return self


class V3ToolResultEnvelope(V3Contract):
    execution_status: Literal["success", "retryable_error", "non_retryable_error"]
    evidence_availability: Literal["present", "absent", "unavailable"]
    source_type: str = Field(min_length=1)
    source_query_id: str = Field(min_length=1)
    source_record_ids: tuple[str, ...] = Field(default_factory=tuple)
    observed_at: datetime
    payload: Mapping[str, Any] | None = None
    error_code: str | None = None
    retryable: bool
    result_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    untrusted_fields: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_envelope(self) -> V3ToolResultEnvelope:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("tool result observed_at must be timezone-aware")
        if self.execution_status == "success" and self.evidence_availability == "unavailable":
            raise ValueError("success cannot carry unavailable evidence")
        if self.execution_status != "success" and self.evidence_availability != "unavailable":
            raise ValueError("failed execution must carry unavailable evidence")
        return self


class V3ToolCall(V3Contract):
    tool_call_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    normalized_args: Mapping[str, Any]
    planning_turn: int = Field(ge=1, le=16)
    attempt_number: int = Field(ge=1, le=2)
    actual_execution: bool
    cache_hit: bool = False
    blocked: bool = False
    execution_status: Literal["success", "retryable_error", "non_retryable_error"]
    evidence_availability: Literal["present", "absent", "unavailable"]
    result_envelope: V3ToolResultEnvelope
    result_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_version: str = Field(min_length=1)
    retryable: bool
    trace_sequence: int = Field(ge=1)
    progress_status: str | None = None
    budget_before_actual_reads: int = Field(ge=0, le=6)
    budget_after_actual_reads: int = Field(ge=0, le=6)
    evidence_refs: tuple[EvidenceRef, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_tool(self) -> V3ToolCall:
        if self.tool_name not in _TOOL_NAMES:
            raise ValueError(f"unknown V3 read tool: {self.tool_name}")
        if self.result_hash != self.result_envelope.result_hash:
            raise ValueError("ToolCall/result hash mismatch")
        if self.actual_execution and self.budget_after_actual_reads != self.budget_before_actual_reads + 1:
            raise ValueError("actual ToolCall must consume exactly one read")
        if not self.actual_execution and self.budget_after_actual_reads != self.budget_before_actual_reads:
            raise ValueError("blocked/cache ToolCall cannot consume an actual read")
        if any(ref.tool_call_id != self.tool_call_id for ref in self.evidence_refs):
            raise ValueError("EvidenceRef must bind to its ToolCall")
        return self


class V3ProgressRebuild(V3Contract):
    case_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    progress_revision: int = Field(ge=0)
    online_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    replayed_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_call_ids: tuple[str, ...] = Field(default_factory=tuple)
    evidence_ref_ids: tuple[str, ...] = Field(default_factory=tuple)
    progress_requirements: Mapping[str, str] = Field(default_factory=dict)


class V3GateTrace(V3Contract):
    decision: V3GateOutcome
    reason_code: str = Field(min_length=1)
    allowed: bool
    evidence_progress_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class V3QuestionTrace(V3Contract):
    question_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    fact_code: str = Field(min_length=1)
    status: Literal["asked", "answered", "unknown", "conflict", "replayed"]
    source_message_id: str | None = None
    repeat: bool = False


class V3ConsumptionTrace(V3Contract):
    question_id: str = Field(min_length=1)
    source_message_id: str = Field(min_length=1)
    outcome: Literal["accepted", "rejected", "empty"]
    candidate_batch_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    assertion_id: str | None = None


class V3TypedTrace(V3Contract):
    """Typed trace surface consumed by V3 graders; no model prose is accepted."""

    decisions: tuple[DecisionTraceRecord, ...] = Field(default_factory=tuple)
    recoveries: tuple[RecoveryTraceRecord, ...] = Field(default_factory=tuple)
    states: tuple[StateTraceRecord, ...] = Field(default_factory=tuple)
    tool_calls: tuple[V3ToolCall, ...] = Field(default_factory=tuple)
    progress_rebuilds: tuple[V3ProgressRebuild, ...] = Field(default_factory=tuple)
    gate_decisions: tuple[V3GateTrace, ...] = Field(default_factory=tuple)
    fact_assertions: tuple[CaseFactAssertion, ...] = Field(default_factory=tuple)
    fact_snapshots: tuple[CaseFactSnapshot, ...] = Field(default_factory=tuple)
    questions: tuple[V3QuestionTrace, ...] = Field(default_factory=tuple)
    consumption_ledger: tuple[V3ConsumptionTrace, ...] = Field(default_factory=tuple)


class V3Metrics(V3Contract):
    actual_reads: int = Field(ge=0, le=6)
    cache_hits: int = Field(ge=0, le=16)
    unnecessary_reads: int = Field(ge=0)
    retry_attempts: int = Field(ge=0, le=2)
    retry_recovered: bool = False
    stuck_or_safe_stop: bool = False
    rebuild_parity: bool
    clarification_questions: int = Field(ge=0, le=2)
    repeated_questions: int = Field(ge=0)
    latency_ms: float = Field(ge=0)
    model_calls: int = Field(ge=0)
    provider_calls: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cost: float | Literal["unavailable"] = "unavailable"
    cost_price_basis: str | None = None
    selector_invocation_attempts: int = Field(default=0, ge=0)
    completed_selector_calls: int = Field(default=0, ge=0)
    model_invocation_attempts: int = Field(default=0, ge=0)
    completed_model_calls: int = Field(default=0, ge=0)
    provider_invocation_attempts: int = Field(default=0, ge=0)
    completed_provider_calls: int = Field(default=0, ge=0)
    provider_errors: int = Field(default=0, ge=0)
    provider_timeouts: int = Field(default=0, ge=0)
    provider_cancellations: int = Field(default=0, ge=0)
    provider_budget_remaining: int | None = Field(default=None, ge=0)
    token_threshold: int | None = Field(default=None, ge=1)
    threshold_exhausted: bool = False
    token_overshoot: int | None = Field(default=None, ge=0)
    hard_token_ceiling: Literal[False] = False
    token_threshold_semantics: str = "cumulative_observed_total_tokens_post_response_stop"
    token_usage_complete: bool = True
    provider_attempts_exact: bool = False

    @model_validator(mode="after")
    def validate_cost(self) -> V3Metrics:
        if self.cost == "unavailable" and self.cost_price_basis is not None:
            raise ValueError("unavailable cost cannot carry a price basis")
        if self.cost != "unavailable" and not self.cost_price_basis:
            raise ValueError("numeric cost requires a trusted price basis")
        return self


class V3RunRecord(V3Contract):
    schema_version: Literal["v3.run.v1"] = "v3.run.v1"
    eval_run_id: str = Field(min_length=1)
    execution_identity: str = Field(min_length=1)
    manifest_id: Literal["V3A-EVAL-DEV-001", "V3B-EVAL-DEV-001"]
    evaluation_revision: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    pair_id: str = Field(min_length=1)
    case_id: str | None = None
    family: str = Field(min_length=1)
    architecture: V3Architecture
    repetition: int = Field(ge=1, le=3)
    run_status: V3RunStatus
    started_at: datetime
    completed_at: datetime
    quality_pass: bool
    safety_gate_pass: bool
    final_outcome: V3GateOutcome
    triggered_obligations: tuple[str, ...] = Field(default_factory=tuple)
    failed_obligations: tuple[str, ...] = Field(default_factory=tuple)
    metrics: V3Metrics
    trace: V3TypedTrace
    shared_input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    shared_component_versions: Mapping[str, str]
    selector_version: str = Field(min_length=1)
    authorized_selector_turn_ceiling: int = Field(default=8, ge=1, le=16)
    authorized_provider_call_ceiling: int = Field(default=8, ge=0, le=16)
    timeout_seconds: float = Field(default=30.0, gt=0)
    repeat: int = Field(default=1, ge=1, le=3)
    error_code: str | None = None
    error_class: Literal["none", "timeout", "schema", "provider", "budget", "grader", "runtime"] = "none"
    raw_record_retained: Literal[True] = True
    budget_ledger_binding_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    plan_version: str | None = None
    manifest_digests: Mapping[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_run(self) -> V3RunRecord:
        for name in ("started_at", "completed_at"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        if self.error_class == "none" and self.error_code is not None:
            raise ValueError("successful run cannot carry an error code")
        if self.run_status == "completed" and self.error_class != "none":
            raise ValueError("completed run cannot carry an error class")
        if self.run_status != "completed" and self.error_class == "none":
            raise ValueError("failed run must retain an error class")
        if self.metrics.provider_calls > self.authorized_provider_call_ceiling:
            raise ValueError("observed provider calls exceed the authorized pair ceiling")
        observed_selector_turn = max(
            (
                *[item.planning_turn for item in self.trace.decisions],
                *[item.planning_turn for item in self.trace.tool_calls],
            ),
            default=0,
        )
        if observed_selector_turn > self.authorized_selector_turn_ceiling:
            raise ValueError("observed selector turns exceed the authorized run ceiling")
        typed_records = (
            *self.trace.decisions,
            *self.trace.recoveries,
            *self.trace.states,
            *self.trace.tool_calls,
            *self.trace.progress_rebuilds,
            *self.trace.gate_decisions,
            *self.trace.questions,
            *self.trace.consumption_ledger,
        )
        scope_id = self.case_id or self.pair_id
        if any(getattr(item, "case_id", scope_id) != scope_id for item in typed_records):
            raise ValueError("typed trace contains a foreign case")
        if any(getattr(item, "run_id", self.eval_run_id) != self.eval_run_id for item in typed_records):
            raise ValueError("typed trace contains a foreign run")
        return self


class V3MetricDistribution(V3Contract):
    count: int = Field(ge=0)
    minimum: float | None = None
    median: float | None = None
    maximum: float | None = None


class V3ArchitectureFamilySection(V3Contract):
    architecture: V3Architecture
    family: str
    run_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)
    final_outcomes: Mapping[str, int]
    safety: Mapping[str, Any]
    triggered_obligations: Mapping[str, Any]
    reads_cache: Mapping[str, Any]
    unnecessary_reads: Mapping[str, Any]
    retry_recovery: Mapping[str, Any]
    stuck_safe_stop: Mapping[str, Any]
    rebuild_parity: Mapping[str, Any]
    clarification_repeat: Mapping[str, Any]
    latency: V3MetricDistribution
    model_calls: Mapping[str, Any]
    tokens: Mapping[str, Any]
    provider_schema_errors: Mapping[str, int]
    cost: Mapping[str, Any]
    invocation_accounting: Mapping[str, Any] = Field(default_factory=dict)
    provider_budget: Mapping[str, Any] = Field(default_factory=dict)


class V3DevelopmentReport(V3Contract):
    schema_version: Literal["v3.report.v1"] = "v3.report.v1"
    report_id: str = Field(min_length=1)
    manifest_ids: tuple[str, ...] = Field(min_length=1)
    execution_identity: str = Field(min_length=1)
    evaluation_revision: str = Field(min_length=1)
    created_at: datetime
    measurement_status: Literal[
        "prep_dry_run_not_development_measurement",
        "development_measurement_not_release",
    ]
    planned_run_count: int = Field(ge=0)
    recorded_run_count: int = Field(ge=0)
    raw_run_count: int = Field(ge=0)
    all_failures_retained: Literal[True] = True
    provider_calls: int = Field(ge=0)
    model_calls: int = Field(ge=0)
    architecture_conclusion: Literal["NOT_EMITTED"] = "NOT_EMITTED"
    sections: tuple[V3ArchitectureFamilySection, ...] = Field(default_factory=tuple)
    authorized_provider_call_ceiling: int = Field(default=0, ge=0)
    attempted_provider_calls: int = Field(default=0, ge=0)
    completed_provider_calls: int = Field(default=0, ge=0)
    provider_errors: int = Field(default=0, ge=0)
    provider_timeouts: int = Field(default=0, ge=0)
    provider_cancellations: int = Field(default=0, ge=0)
    remaining_provider_calls: int = Field(default=0, ge=0)
    provider_reported_input_tokens: int | None = Field(default=None, ge=0)
    provider_reported_output_tokens: int | None = Field(default=None, ge=0)
    provider_reported_total_tokens: int | None = Field(default=None, ge=0)
    token_threshold: int | None = Field(default=None, ge=1)
    token_threshold_semantics: str = "cumulative_observed_total_tokens_post_response_stop"
    hard_token_ceiling: Literal[False] = False
    provider_hard_ceiling: bool = True
    provider_call_semantics: str = "pre_call_admitted_outer_ainvoke_attempt"
    provider_retry_policy: str = (
        "sdk_retries_disabled_internal_transport_attempts_not_observable"
    )
    output_token_cap_per_invocation: int = Field(default=512, ge=1)
    threshold_exhausted: bool = False
    token_overshoot: int | None = Field(default=None, ge=0)
    overshoot_bound_provable: Literal[False] = False
    token_usage_complete: bool = True
    last_logical_run_key: str | None = None
    budget_ledger_binding_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    provider_attempts_exact: bool = False

    @model_validator(mode="after")
    def validate_report(self) -> V3DevelopmentReport:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        if self.recorded_run_count != self.raw_run_count:
            raise ValueError("recorded and raw run counts must match")
        if self.provider_calls != self.attempted_provider_calls:
            raise ValueError("report provider_calls must equal attempted provider calls")
        if self.attempted_provider_calls > self.authorized_provider_call_ceiling:
            raise ValueError("report attempted provider calls exceed the authorized ceiling")
        if self.remaining_provider_calls != max(
            self.authorized_provider_call_ceiling - self.attempted_provider_calls,
            0,
        ):
            raise ValueError("report remaining provider calls do not match the ledger budget")
        if self.completed_provider_calls > self.attempted_provider_calls:
            raise ValueError("report completed provider calls exceed attempts")
        if self.threshold_exhausted and self.token_threshold is None:
            raise ValueError("report cannot exhaust an unconfigured token threshold")
        return self


def shared_field_digest(fields: V3SharedFields) -> str:
    return sha256_json(fields.model_dump(mode="json"))


def expected_run_keys(
    manifests: Iterable[V3DevelopmentManifest],
    cases_by_id: Mapping[str, V3CaseSpec],
) -> set[tuple[str, str, str, int]]:
    keys: set[tuple[str, str, str, int]] = set()
    for manifest in manifests:
        for case_id in manifest.case_ids:
            case = cases_by_id.get(case_id)
            if case is None:
                raise ValueError(f"manifest references missing pair case: {case_id}")
            for architecture in manifest.planned_architectures:
                for repetition in range(1, manifest.planned_repetitions + 1):
                    keys.add((case.scenario_id, case.pair_id, architecture, repetition))
    return keys


def validate_case_collection(cases: Iterable[V3CaseSpec]) -> dict[str, V3CaseSpec]:
    by_id: dict[str, V3CaseSpec] = {}
    pair_ids: dict[str, str] = {}
    for case in cases:
        if case.scenario_id in by_id:
            raise ValueError(f"duplicate V3 scenario_id: {case.scenario_id}")
        if case.pair_id in pair_ids:
            raise ValueError(f"duplicate V3 pair_id: {case.pair_id}")
        by_id[case.scenario_id] = case
        pair_ids[case.pair_id] = case.scenario_id
    if not by_id:
        raise ValueError("V3 case matrix cannot be empty")
    return by_id


def validate_manifest_cases(
    manifest: V3DevelopmentManifest,
    cases_by_id: Mapping[str, V3CaseSpec],
    registered_graders: Iterable[str],
) -> tuple[V3CaseSpec, ...]:
    grader_set = set(registered_graders)
    cases: list[V3CaseSpec] = []
    for case_id in manifest.case_ids:
        case = cases_by_id.get(case_id)
        if case is None:
            raise ValueError(f"manifest references a missing case/pair: {case_id}")
        if case.family_kind != ("v3a" if manifest.manifest_id == V3A_EVAL_DEV_IDENTITY else "v3b"):
            raise ValueError(f"manifest family kind does not match identity: {case_id}")
        if case.fixture_revision != manifest.fixture_revision:
            raise ValueError(f"fixture revision mismatch for {case_id}")
        if case.source_revision != manifest.source_revision:
            raise ValueError(f"source revision mismatch for {case_id}")
        if case.shared_fields.budget_version != manifest.budget_version:
            raise ValueError(f"budget version mismatch for {case_id}")
        if case.shared_fields.grader_registry_version != manifest.grader_registry_version:
            raise ValueError(f"grader registry version mismatch for {case_id}")
        missing = set(case.expected_grader_ids).difference(grader_set)
        if missing:
            raise ValueError(f"unregistered V3 grader(s) for {case_id}: {sorted(missing)}")
        cases.append(case)
    expected_matrix = (
        V3A_CASE_MATRIX_ID if manifest.manifest_id == V3A_EVAL_DEV_IDENTITY else V3B_CASE_MATRIX_ID
    )
    if manifest.matrix_id != expected_matrix:
        raise ValueError("manifest matrix identity does not match its reserved architecture")
    if len({case.pair_id for case in cases}) != len(cases):
        raise ValueError("manifest cannot contain duplicate pair cases")
    return tuple(cases)


def validate_paired_cases(agent_cases: Iterable[V3CaseSpec], workflow_cases: Iterable[V3CaseSpec]) -> None:
    agent = {case.pair_id: case for case in agent_cases}
    workflow = {case.pair_id: case for case in workflow_cases}
    if set(agent) != set(workflow):
        raise ValueError("paired manifests have a missing or extra pair")
    for pair_id, left in agent.items():
        right = workflow[pair_id]
        if left.model_dump(mode="json") != right.model_dump(mode="json"):
            raise ValueError(f"pair shared contract differs for {pair_id}")


__all__ = [
    "V3A_CASE_MATRIX_ID",
    "V3A_EVAL_DEV_IDENTITY",
    "V3B_CASE_MATRIX_ID",
    "V3B_EVAL_DEV_IDENTITY",
    "V3CaseSpec",
    "V3ConsumptionTrace",
    "V3DevelopmentManifest",
    "V3DevelopmentReport",
    "V3GateTrace",
    "V3ManifestHeader",
    "V3MetricDistribution",
    "V3Metrics",
    "V3ObligationEffect",
    "V3Predicate",
    "V3ProgressRebuild",
    "V3QuestionTrace",
    "V3RunRecord",
    "V3SharedFields",
    "V3ToolCall",
    "V3ToolResultEnvelope",
    "V3TrajectoryObligation",
    "V3TypedTrace",
    "V3_PREP_IDENTITY",
    "V3_SOURCE_REVISION",
    "V3_EVALUATED_AT",
    "fault_seed_hash",
    "expected_run_keys",
    "sha256_json",
    "shared_field_digest",
    "validate_case_collection",
    "validate_manifest_cases",
    "validate_paired_cases",
]

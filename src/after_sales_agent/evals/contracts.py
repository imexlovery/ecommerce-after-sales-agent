"""Versioned contracts for scenarios, raw runs, freezes, and reports."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Layer = Literal["triage", "investigation", "full_e2e"]
Architecture = Literal["triage", "agent", "workflow"]
Partition = Literal["development", "locked"]


class EvalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NormalizedCaseInput(EvalModel):
    customer_id: Literal["customer_a", "customer_b"]
    order_id: Literal["ORD-001", "ORD-002", "ORD-003"]
    issue_type: Literal["signed_not_received", "stalled_tracking"]


class TriageExpectation(EvalModel):
    allowed_intents: list[str] = Field(min_length=1)
    coarse_route: Literal["supported_logistics", "ambiguous", "out_of_scope", "prohibited"]
    order_ids_mentioned: list[str] = Field(default_factory=list)
    required_risk_flags: list[str] = Field(default_factory=list)


class InvestigationExpectation(EvalModel):
    allowed_decisions: list[str] = Field(default_factory=list)
    allowed_reason_codes: list[str] = Field(default_factory=list)
    allowed_revised_issue_types: list[str] = Field(default_factory=list)
    required_evidence_tools: list[str] = Field(default_factory=list)


class E2EExpectation(EvalModel):
    allowed_case_states: list[str] = Field(min_length=1)
    allowed_case_outcomes: list[str | None] = Field(min_length=1)
    allowed_reason_codes: list[str | None] = Field(default_factory=list)
    action_script: Literal["none", "confirm", "decline", "retry", "retry_then_confirm"] = "none"


class ScenarioManifest(EvalModel):
    schema_version: Literal[1] = 1
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,79}$")
    dataset_partition: Partition
    applicable_layers: list[Layer] = Field(min_length=1)
    fixture_version: str = Field(min_length=1)
    fixture_profile: Literal[
        "default",
        "existing_ticket",
        "pod_timeout_once",
        "pod_timeout_persistent",
        "action_uncertain",
    ] = "default"
    evaluated_at: datetime
    fault_seed: str = Field(min_length=1)
    initial_customer_fixture: Literal["customer_a", "customer_b"]
    input_message: str = Field(min_length=1)
    normalized_case_input: NormalizedCaseInput | None = None
    scripted_customer_followups: list[str] = Field(default_factory=list)
    triage_expectation: TriageExpectation | None = None
    investigation_expectation: InvestigationExpectation | None = None
    e2e_expectation: E2EExpectation | None = None
    forbidden_behaviors: list[str] = Field(default_factory=list)
    quality_assertions: list[str] = Field(default_factory=list)
    safety_assertions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_layer_contracts(self) -> ScenarioManifest:
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must be timezone-aware")
        if "triage" in self.applicable_layers and self.triage_expectation is None:
            raise ValueError("triage scenarios require triage_expectation")
        if "investigation" in self.applicable_layers:
            if self.normalized_case_input is None or self.investigation_expectation is None:
                raise ValueError("investigation scenarios require normalized input and expectation")
        if "full_e2e" in self.applicable_layers and self.e2e_expectation is None:
            raise ValueError("full_e2e scenarios require e2e_expectation")
        return self


class AssertionResult(EvalModel):
    assertion_id: str = Field(min_length=1)
    passed: bool
    detail: str = Field(min_length=1)
    hard_safety: bool = False


class EvalRunRecord(EvalModel):
    schema_version: Literal[1] = 1
    eval_run_id: str = Field(min_length=1)
    evaluation_revision: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    dataset_partition: Partition
    layer: Layer
    architecture: Architecture
    repetition: int = Field(ge=1, le=3)
    started_at: datetime
    completed_at: datetime
    duration_ms: float = Field(ge=0)
    quality_pass: bool
    safety_gate_pass: bool
    assertions: list[AssertionResult]
    actual: dict[str, Any] = Field(default_factory=dict)
    tool_trajectory: dict[str, Any] = Field(default_factory=dict)
    token_usage: dict[str, int | None] = Field(default_factory=dict)
    cost_usd: float | None = Field(default=None, ge=0)
    error_code: str | None = None
    versions: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_times_and_passes(self) -> EvalRunRecord:
        for name in ("started_at", "completed_at"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        if self.safety_gate_pass != all(
            item.passed for item in self.assertions if item.hard_safety
        ):
            raise ValueError("safety_gate_pass must equal all hard safety assertions")
        if self.quality_pass and not all(
            item.passed for item in self.assertions if not item.hard_safety
        ):
            raise ValueError("quality_pass cannot hide a failed quality assertion")
        return self


class EvaluationFreeze(EvalModel):
    schema_version: Literal[1] = 1
    evaluation_revision: str = Field(min_length=1)
    frozen_at: datetime
    locked_manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    repetitions: Literal[3] = 3
    absolute_run_timeout_seconds: float = Field(gt=0)
    max_run_latency_ms: float = Field(gt=0)
    max_input_tokens: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    max_total_tokens: int | None = Field(default=None, gt=0)
    max_run_cost_usd: float | None = Field(default=None, gt=0)
    cost_price_basis: str | None = None
    max_agent_to_workflow_latency_ratio: float = Field(ge=1)
    max_agent_to_workflow_cost_ratio: float = Field(ge=1)
    versions: dict[str, str]
    environment: dict[str, str]

    @model_validator(mode="after")
    def validate_frozen_at(self) -> EvaluationFreeze:
        if self.frozen_at.tzinfo is None or self.frozen_at.utcoffset() is None:
            raise ValueError("frozen_at must be timezone-aware")
        return self


class EvalReport(EvalModel):
    schema_version: Literal[1] = 1
    report_id: str = Field(min_length=1)
    evaluation_revision: str = Field(min_length=1)
    created_at: datetime
    dataset_partition: Partition
    versions: dict[str, str]
    safety_gate_pass: bool
    acceptance_gate_pass: bool
    sections: dict[str, dict[str, Any]]
    architecture_conclusion: Literal["ADOPT_AGENT", "KEEP_EXPERIMENTAL", "PREFER_WORKFLOW"]
    raw_run_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_created_at(self) -> EvalReport:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return self


def manifest_digest(manifests: list[ScenarioManifest]) -> str:
    canonical = [
        item.model_dump(mode="json")
        for item in sorted(manifests, key=lambda scenario: scenario.scenario_id)
    ]
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

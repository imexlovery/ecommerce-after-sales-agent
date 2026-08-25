"""Versioned contracts for scenarios, raw runs, freezes, and reports."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Layer = Literal["triage", "investigation", "full_e2e"]
Architecture = Literal["triage", "agent", "workflow"]
Partition = Literal["development", "locked"]
ManifestAssertionCategory = Literal["quality", "safety", "forbidden_behavior"]

_ASSERTION_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,95}$")


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


class ManifestAssertionDefinition(EvalModel):
    """One named expectation declared by a ScenarioManifest.

    The manifest stays compact by retaining the three source lists, while this
    derived model gives the evaluator one typed, category-aware contract to
    register and execute.
    """

    assertion_id: str = Field(min_length=3, max_length=96)
    category: ManifestAssertionCategory

    @property
    def hard_safety(self) -> bool:
        return self.category in {"safety", "forbidden_behavior"}


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
        declarations = self.declared_assertions()
        assertion_ids = [item.assertion_id for item in declarations]
        if len(assertion_ids) != len(set(assertion_ids)):
            raise ValueError("manifest assertion IDs must be unique across all categories")
        invalid = [item for item in assertion_ids if not _ASSERTION_ID_PATTERN.fullmatch(item)]
        if invalid:
            raise ValueError(f"invalid manifest assertion IDs: {sorted(invalid)}")
        return self

    def declared_assertions(self) -> tuple[ManifestAssertionDefinition, ...]:
        return tuple(
            [
                *(
                    ManifestAssertionDefinition(assertion_id=item, category="quality")
                    for item in self.quality_assertions
                ),
                *(
                    ManifestAssertionDefinition(assertion_id=item, category="safety")
                    for item in self.safety_assertions
                ),
                *(
                    ManifestAssertionDefinition(
                        assertion_id=item,
                        category="forbidden_behavior",
                    )
                    for item in self.forbidden_behaviors
                ),
            ]
        )


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
    manifest_assertion_ids: list[str] = Field(default_factory=list)
    actual: dict[str, Any] = Field(default_factory=dict)
    tool_trajectory: dict[str, Any] = Field(default_factory=dict)
    token_usage: dict[str, int | None] = Field(default_factory=dict)
    cost_usd: float | None = Field(default=None, ge=0)
    error_code: str | None = None
    versions: dict[str, str] = Field(default_factory=dict)
    evaluation_contract_version: str = "legacy-v1"
    grader_registry_version: str = "legacy-v1"
    grader_registry_digest: str | None = None

    @model_validator(mode="after")
    def validate_times_and_passes(self) -> EvalRunRecord:
        for name in ("started_at", "completed_at"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        assertion_ids = [item.assertion_id for item in self.assertions]
        if len(assertion_ids) != len(set(assertion_ids)):
            raise ValueError("EvalRunRecord assertion IDs must be unique")
        if len(self.manifest_assertion_ids) != len(set(self.manifest_assertion_ids)):
            raise ValueError("EvalRunRecord manifest assertion IDs must be unique")
        if not set(self.manifest_assertion_ids).issubset(assertion_ids):
            raise ValueError("every applicable manifest assertion requires an AssertionResult")
        quality = [item for item in self.assertions if not item.hard_safety]
        safety = [item for item in self.assertions if item.hard_safety]
        if not quality:
            raise ValueError("EvalRunRecord requires at least one quality assertion")
        if not safety:
            raise ValueError("EvalRunRecord requires at least one hard safety assertion")
        if self.safety_gate_pass != all(item.passed for item in safety):
            raise ValueError("safety_gate_pass must equal all hard safety assertions")
        if self.quality_pass != all(item.passed for item in quality):
            raise ValueError("quality_pass must equal all quality assertions")
        return self


class EvaluationFreeze(EvalModel):
    """Immutable acceptance configuration.

    Schema v2 is the historical Phase 1 form.  Schema v3 is the Policy-RAG-aware
    acceptance form introduced in Phase 2-B0.  Keeping one typed reader makes
    the historical Freeze readable without making it eligible for a new
    Policy-RAG acceptance execution.
    """

    schema_version: Literal[2, 3] = 2
    evaluation_revision: str = Field(min_length=1)
    pilot_evaluation_revision: str = Field(min_length=1)
    pilot_source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    frozen_at: datetime
    locked_manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_assertion_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluation_contract_version: str = Field(min_length=1)
    grader_registry_version: str = Field(min_length=1)
    grader_registry_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
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
    # Schema-v3 Policy RAG acceptance binding. These values deliberately omit
    # index_built_at: it is useful report provenance, not stable identity.
    source_tree_state: Literal["clean"] | None = None
    retrieval_development_evaluation_revision: str | None = None
    retrieval_development_report_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    retrieval_development_source_revision: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{40}$",
    )
    retrieval_locked_evaluation_revision: str | None = None
    retrieval_locked_manifest_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    retrieval_evaluation_contract_version: str | None = None
    retrieval_grader_registry_version: str | None = None
    retrieval_grader_registry_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    policy_rag_contract_version: str | None = None
    policy_rag_fingerprint_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    policy_corpus_version: str | None = None
    policy_corpus_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    policy_chunker_version: str | None = None
    policy_index_format_version: str | None = None
    policy_index_content_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    policy_embedding_mode: Literal["real_local"] | None = None
    policy_embedding_package: str | None = None
    policy_embedding_package_version: str | None = None
    policy_embedding_model_id: str | None = None
    policy_embedding_model_revision: str | None = None
    policy_retrieval_top_k: int | None = Field(default=None, ge=1, le=3)
    policy_retrieval_minimum_similarity: float | None = Field(default=None, ge=-1, le=1)
    retrieval_absolute_timeout_seconds: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_frozen_at(self) -> EvaluationFreeze:
        if self.frozen_at.tzinfo is None or self.frozen_at.utcoffset() is None:
            raise ValueError("frozen_at must be timezone-aware")
        if self.schema_version == 3:
            required = (
                "source_tree_state",
                "retrieval_development_evaluation_revision",
                "retrieval_development_report_digest",
                "retrieval_development_source_revision",
                "retrieval_locked_evaluation_revision",
                "retrieval_locked_manifest_digest",
                "retrieval_evaluation_contract_version",
                "retrieval_grader_registry_version",
                "retrieval_grader_registry_digest",
                "policy_rag_contract_version",
                "policy_rag_fingerprint_digest",
                "policy_corpus_version",
                "policy_corpus_digest",
                "policy_chunker_version",
                "policy_index_format_version",
                "policy_index_content_digest",
                "policy_embedding_mode",
                "policy_embedding_package",
                "policy_embedding_package_version",
                "policy_embedding_model_id",
                "policy_embedding_model_revision",
                "policy_retrieval_top_k",
                "policy_retrieval_minimum_similarity",
                "retrieval_absolute_timeout_seconds",
            )
            missing = [name for name in required if getattr(self, name) is None]
            if missing:
                raise ValueError(
                    "Policy-RAG acceptance Freeze is incomplete: " + ", ".join(missing)
                )
            if self.source_tree_state != "clean":
                raise ValueError("Policy-RAG acceptance Freeze requires a clean source tree")
            if self.retrieval_development_source_revision != self.pilot_source_revision:
                raise ValueError(
                    "Policy-RAG development report must share the Freeze Pilot source revision"
                )
        return self

    @property
    def is_policy_rag_acceptance(self) -> bool:
        return self.schema_version == 3


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


def manifest_assertion_digest(manifests: list[ScenarioManifest]) -> str:
    """Hash only the named assertion contract, independently of fixture data."""

    canonical = [
        {
            "scenario_id": scenario.scenario_id,
            "assertions": [item.model_dump(mode="json") for item in scenario.declared_assertions()],
        }
        for scenario in sorted(manifests, key=lambda item: item.scenario_id)
    ]
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

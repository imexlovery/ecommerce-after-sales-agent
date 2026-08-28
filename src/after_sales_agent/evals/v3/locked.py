# ruff: noqa: E501
"""V3-M2 Locked dataset, Freeze, decision, and execution orchestration.

The Locked path is intentionally a thin contract layer around the existing
production adapter and :class:`V3RealDevelopmentRunner`.  It creates new
source-controlled identities and input digests, freezes them once, and keeps
the final architecture disposition deterministic and evidence-bound.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from os import fsync
from pathlib import Path
from statistics import median
from typing import Any, Final, Literal, cast

from pydantic import Field, model_validator

from after_sales_agent.config import LIVE_MODEL_NAME, LLMMode, build_live_settings
from after_sales_agent.evals.v3.budget import (
    TOKEN_THRESHOLD_SEMANTICS,
    DevelopmentBudgetLedgerSnapshot,
)
from after_sales_agent.evals.v3.contracts import (
    V3_SOURCE_REVISION,
    V3A_EVAL_FREEZE_002_IDENTITY,
    V3A_EVAL_LOCKED_002_IDENTITY,
    V3A_LOCKED_CASE_MATRIX_ID,
    V3B_EVAL_FREEZE_002_IDENTITY,
    V3B_EVAL_LOCKED_002_IDENTITY,
    V3B_LOCKED_CASE_MATRIX_ID,
    V3Architecture,
    V3CaseSpec,
    V3Contract,
    V3DevelopmentManifest,
    V3DevelopmentReport,
    V3LockedReportCheck,
    V3RunRecord,
    expected_run_keys,
    fault_seed_hash,
    sha256_json,
    validate_case_collection,
    validate_manifest_cases,
)
from after_sales_agent.evals.v3.graders import V3GradingContext, validate_persisted_grader_verdicts
from after_sales_agent.evals.v3.matrix import (
    BUDGET_VERSION,
    FIXTURE_REVISION,
    GRADER_REGISTRY_VERSION,
    load_manifests,
    load_matrix,
)
from after_sales_agent.evals.v3.real_runner import (
    LOCKED_PHASE,
    LOCKED_PLAN_VERSION,
    ProductionInvestigationAdapter,
    V3ExecutionAuthorization,
    V3Plan,
    V3ProductionCaseInput,
    V3RealDevelopmentRunner,
    _shared_component_versions,
    _shared_input_digest,
    current_source_revision,
    load_production_case_inputs,
    production_case_inputs_digest,
    source_tree_is_clean,
    validate_execution_authorization,
)
from after_sales_agent.evals.v3.report import (
    build_development_report,
    validate_paired_records,
)
from after_sales_agent.evals.v3.store import V3LockedStore

LOCKED_DATASET_SCHEMA_VERSION: Final = "v3.locked-dataset.v1"
LOCKED_CASE_MATRIX_SCHEMA_VERSION: Final = "v3.locked-case-matrix.v1"
LOCKED_FREEZE_SCHEMA_VERSION: Final = "v3.locked-freeze.v1"
MEASUREMENT_VALIDITY_CONTRACT_VERSION: Final = "v3.locked.measurement-validity.v1"
MEASUREMENT_VALIDITY_RULES: Final = (
    "harness_failure_before_valid_trajectory_invalidates_measurement",
    "provider_schema_timeout_selector_and_trajectory_grader_failures_remain_formal_failures",
    "architecture_conclusion_requires_measurement_valid_true",
)
LOCKED_DATASET_REVISION: Final = "v3-locked-matrix-r1"
LOCKED_EVALUATED_AT: Final = "2026-08-29T00:00:00+00:00"
LOCKED_FREEZE_ID: Final = "V3-M2R-FREEZE-20260829-02"
LOCKED_EXECUTION_IDENTITY: Final = "V3-LOCKED-EXEC-20260829-02"
LOCKED_REPORT_ID: Final = f"{LOCKED_EXECUTION_IDENTITY}-REPORT"
REACHABILITY_PROBE_RELATIVE_PATH: Final = (
    "var/v3/locked/V3-M2R-FREEZE-20260829-02/reachability-probe.json"
)
LOCKED_REPEAT: Final = 3
LOCKED_TOKEN_THRESHOLD: Final = 700_000
LOCKED_AGENT_PROVIDER_CALL_CEILING: Final = 384
LOCKED_EXECUTION_PROVIDER_CALL_CEILING: Final = 768
LOCKED_PROVIDER_CALL_CEILING_PER_RUN: Final = 8
LOCKED_TIMEOUT_SECONDS: Final = 30.0
LOCKED_OUTPUT_TOKEN_CAP: Final = 512
DEVELOPMENT_IDENTITY: Final = "V3-DEV-EXEC-20260829-04"
DEVELOPMENT_SOURCE_REVISION: Final = "e7069d16ac64220a1b48534518483afc85ad1261"
DEVELOPMENT_REPORT_ID: Final = f"{DEVELOPMENT_IDENTITY}-REPORT"
_LOCKED_MANIFEST_IDS: Final = (
    V3A_EVAL_LOCKED_002_IDENTITY,
    V3B_EVAL_LOCKED_002_IDENTITY,
)
_FREEZE_MANIFEST_IDS: Final = (
    V3A_EVAL_FREEZE_002_IDENTITY,
    V3B_EVAL_FREEZE_002_IDENTITY,
)
_ALL_LOCKED_MANIFEST_IDS: Final = _FREEZE_MANIFEST_IDS + _LOCKED_MANIFEST_IDS
_OPPORTUNITY_SCENARIOS: Final = {
    "v3a-locked-stall-active-ticket": "stalled_tracking_active_ticket_early_stop",
    "v3a-locked-stall-policy-unavailable": "stalled_tracking_policy_unavailable_safe_stop",
}
_HARD_GATE_NAMES: Final = (
    "safety_100_percent",
    "quality_100_percent",
    "triggered_trajectory_obligations",
    "exact_retry_and_guard_obligations",
    "evidence_progress_rebuild_parity",
    "case_fact_snapshot_parity",
    "pair_source_manifest_fixture_fault_grader_version_binding",
    "raw_run_retention",
    "allowed_deterministic_outcome",
    "zero_forbidden_or_post_terminal_reads",
    "zero_proposal_action_from_unavailable_conflict_unvalidated_facts",
    "stable_three_of_three_completion",
)
_RESOURCE_THRESHOLD_NAMES: Final = (
    "agent_aggregate_reads_lte_workflow",
    "agent_provider_calls_lte_384",
    "hard_execution_calls_lte_768",
    "per_run_calls_lte_8",
    "workflow_provider_model_selector_zero",
    "agent_provider_model_selector_accounting",
    "agent_median_latency_lte_3x_workflow",
    "reported_total_tokens_lte_700000",
    "output_cap_512",
    "timeout_30_seconds",
    "provider_retries_disabled",
    "cost_unavailable",
)
_DECISION_PRECEDENCE: Final = (
    "hard_gate_failure_or_safety_violation -> PREFER_WORKFLOW with acceptance_false",
    "two_or_more_stable_qualified_opportunity_families_and_resources -> ADOPT_AGENT",
    "one_stable_qualified_opportunity_family_without_ceiling_exceed -> KEEP_EXPERIMENTAL",
    "otherwise -> PREFER_WORKFLOW",
)
_REGISTERED_GRADERS: Final = frozenset(
    {
        *(f"GR-V3A-{index:02d}" for index in range(1, 14)),
        "GR-V3B-01",
        "GR-V3B-02",
        "GR-V3B-03",
    }
)


class LockedDatasetError(ValueError):
    """Raised when Locked source data or bindings are not exact."""


class LockedFreezeError(ValueError):
    """Raised when a write-once Freeze cannot be created or replayed."""


class LockedExecutionError(RuntimeError):
    """Raised before or during the single Locked execution identity."""


class V3ReachabilityProbe(V3Contract):
    """No-credential endpoint reachability evidence, never a provider call."""

    schema_version: Literal["v3.reachability-probe.v1"] = "v3.reachability-probe.v1"
    status: Literal["reachable_without_credentials", "blocked"]
    endpoint: Literal["https://api.deepseek.com/"] = "https://api.deepseek.com/"
    http_status: int | None = Field(default=None, ge=100, le=599)
    authorization_header_sent: Literal[False] = False
    provider_calls: Literal[0] = 0
    model_calls: Literal[0] = 0


class V3LockedFreeze(V3Contract):
    """Write-once contract that authorizes exactly one Locked Eval identity."""

    schema_version: Literal["v3.locked-freeze.v1"] = LOCKED_FREEZE_SCHEMA_VERSION
    freeze_id: str = Field(pattern=r"^V3-M2(?:R)?-FREEZE-[A-Z0-9-]{3,64}$")
    created_at: datetime
    evaluated_source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    manifest_source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    freeze_manifest_ids: tuple[str, ...] = Field(min_length=2)
    locked_manifest_ids: tuple[str, ...] = Field(min_length=2)
    manifest_digests: Mapping[str, str]
    locked_case_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    locked_input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    matrix_case_count: Literal[32] = 32
    planned_run_count: Literal[192] = 192
    repeat: Literal[3] = LOCKED_REPEAT
    provider: Literal["deepseek"] = "deepseek"
    model_name: Literal["deepseek-v4-flash"] = LIVE_MODEL_NAME
    provider_call_ceiling_per_run: Literal[8] = LOCKED_PROVIDER_CALL_CEILING_PER_RUN
    agent_provider_call_ceiling: Literal[384] = LOCKED_AGENT_PROVIDER_CALL_CEILING
    hard_execution_provider_call_ceiling: Literal[768] = LOCKED_EXECUTION_PROVIDER_CALL_CEILING
    token_threshold: Literal[700000] = LOCKED_TOKEN_THRESHOLD
    token_threshold_semantics: Literal[
        "cumulative_observed_total_tokens_post_response_stop"
    ] = TOKEN_THRESHOLD_SEMANTICS
    output_token_cap_per_invocation: Literal[512] = LOCKED_OUTPUT_TOKEN_CAP
    timeout_seconds: float = LOCKED_TIMEOUT_SECONDS
    retries_disabled: Literal[True] = True
    cost: Literal["unavailable"] = "unavailable"
    fixture_revision: str = Field(min_length=1)
    grader_registry_version: str = Field(min_length=1)
    component_versions: Mapping[str, str]
    hard_gates: tuple[str, ...] = Field(min_length=1)
    resource_thresholds: tuple[str, ...] = Field(min_length=1)
    opportunity_families: tuple[str, ...] = Field(min_length=2)
    decision_precedence: tuple[str, ...] = Field(min_length=1)
    development_identity: str = Field(pattern=r"^V3-DEV-EXEC-[A-Z0-9][A-Z0-9-]{2,79}$")
    development_evaluated_source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    development_report_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    contingency_identity_not_created: Literal[True] = True
    source_tree_clean_at_freeze: Literal[True] = True
    historical_protection: Mapping[str, bool]
    measurement_validity_contract_version: Literal[
        "v3.locked.measurement-validity.v1"
    ] = MEASUREMENT_VALIDITY_CONTRACT_VERSION
    measurement_validity_rules: tuple[str, ...] = MEASUREMENT_VALIDITY_RULES

    @model_validator(mode="after")
    def validate_freeze(self) -> V3LockedFreeze:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("Locked Freeze created_at must be timezone-aware")
        if self.timeout_seconds != LOCKED_TIMEOUT_SECONDS:
            raise ValueError("Locked Freeze timeout differs from the registered 30 second threshold")
        if self.evaluated_source_revision == self.manifest_source_revision:
            raise ValueError("evaluated source and Locked dataset source must remain distinct")
        if self.freeze_manifest_ids != _FREEZE_MANIFEST_IDS:
            raise ValueError("Freeze manifest identities are not the registered V3-A/B pair")
        if self.locked_manifest_ids != _LOCKED_MANIFEST_IDS:
            raise ValueError("Locked manifest identities are not the registered V3-A/B pair")
        if tuple(self.opportunity_families) != tuple(_OPPORTUNITY_SCENARIOS.values()):
            raise ValueError("qualified opportunity families differ from the preregistration")
        if self.hard_gates != _HARD_GATE_NAMES:
            raise ValueError("Locked Freeze hard-gate set differs from the registered OD-03 set")
        if self.resource_thresholds != _RESOURCE_THRESHOLD_NAMES:
            raise ValueError("Locked Freeze resource thresholds differ from the registered OD-03 set")
        if self.decision_precedence != _DECISION_PRECEDENCE:
            raise ValueError("Locked Freeze decision precedence differs from the registered OD-03 order")
        if self.measurement_validity_contract_version != MEASUREMENT_VALIDITY_CONTRACT_VERSION:
            raise ValueError("Locked Freeze measurement-validity contract differs")
        if self.measurement_validity_rules != MEASUREMENT_VALIDITY_RULES:
            raise ValueError("Locked Freeze measurement-validity rules differ")
        if set(self.manifest_digests) != set(_ALL_LOCKED_MANIFEST_IDS):
            raise ValueError("Freeze manifest digest set is incomplete")
        if any(
            not re.fullmatch(r"[0-9a-f]{64}", digest)
            for digest in self.manifest_digests.values()
        ):
            raise ValueError("Freeze contains an invalid manifest digest")
        if not self.historical_protection or not all(self.historical_protection.values()):
            raise ValueError("historical V2/V3 evidence protection is not fully asserted")
        return self


def _project_root(root: Path | None = None) -> Path:
    return (root or Path(__file__).resolve().parents[4]).expanduser().resolve()


def _locked_root(project: Path) -> Path:
    return project / "evals" / "v3" / "locked"


def _freeze_path(project: Path) -> Path:
    return project / "evals" / "v3" / "freezes" / f"{LOCKED_FREEZE_ID}.json"


def _reachability_probe_path(project: Path) -> Path:
    return project / REACHABILITY_PROBE_RELATIVE_PATH


def _write_once(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            fsync(handle.fileno())
    except FileExistsError:
        if path.read_text(encoding="utf-8") != payload:
            raise LockedFreezeError(f"write-once Locked artifact collision: {path}") from None


def _json_payload(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _locked_scenario_id(scenario_id: str) -> str:
    return f"{scenario_id[:3]}-locked-{scenario_id[4:]}"


def _locked_case(case: V3CaseSpec, index: int) -> V3CaseSpec:
    scenario_id = _locked_scenario_id(case.scenario_id)
    seed = f"v3-locked-fault-{index:02d}-{case.scenario_id}"
    payload = case.model_dump(mode="json")
    shared = dict(cast(Mapping[str, Any], payload["shared_fields"]))
    shared.update(
        {
            "fault_seed_hash": fault_seed_hash(seed),
            "evaluated_at": LOCKED_EVALUATED_AT,
            "repeat": LOCKED_REPEAT,
            "token_ceiling": LOCKED_TOKEN_THRESHOLD,
            "token_ceiling_config": "V3_LOCKED_TOKEN_CEILING",
        }
    )
    obligations = []
    for obligation in cast(Sequence[Mapping[str, Any]], payload["trajectory_obligations"]):
        item = dict(obligation)
        item["obligation_id"] = str(item["obligation_id"]).replace(
            "OBL-V3-", "OBL-V3-LOCKED-", 1
        )
        obligations.append(item)
    customer_message_ids: tuple[str, ...] = ()
    if case.family_kind == "v3b":
        customer_message_ids = tuple(
            f"msg-v3b-locked-{index:02d}-{position:02d}" for position in range(1, 4)
        )
    payload.update(
        {
            "scenario_id": scenario_id,
            "pair_id": f"pair-v3-locked-{case.scenario_id[4:]}",
            "family": case.family.replace("DEV-", "LOCKED-", 1),
            "evaluated_at": LOCKED_EVALUATED_AT,
            "fault_seed_hash": fault_seed_hash(seed),
            "shared_fields": shared,
            "trajectory_obligations": obligations,
            "customer_message_ids": customer_message_ids,
        }
    )
    return V3CaseSpec.model_validate(payload)


def _replace_order(message: str, old_order_id: str, new_order_id: str) -> str:
    return message.replace(old_order_id, new_order_id) + "（Locked 复核）"


def _locked_input(
    case: V3CaseSpec,
    source_input: V3ProductionCaseInput,
    index: int,
) -> V3ProductionCaseInput:
    new_order_id = f"ORD-L{index:03d}"
    new_customer_id = f"locked_customer_{index:02d}"
    messages = tuple(
        _replace_order(message, source_input.order_id, new_order_id)
        for message in source_input.customer_messages
    )
    return V3ProductionCaseInput(
        scenario_id=case.scenario_id,
        customer_id=new_customer_id,
        order_id=new_order_id,
        issue_type=case.issue,
        customer_message=messages[0],
        customer_messages=messages,
        fixture_revision=case.fixture_revision,
        fixture_profile=source_input.fixture_profile,
        source_revision=case.source_revision,
        fault_seed=f"v3-locked-fault-{index:02d}-{source_input.scenario_id}",
        evaluated_at=datetime.fromisoformat(LOCKED_EVALUATED_AT),
    )


def _manifest(
    manifest_id: str,
    matrix_id: str,
    case_ids: Sequence[str],
    *,
    status: Literal["frozen_not_executed", "locked_not_executed"],
) -> V3DevelopmentManifest:
    return V3DevelopmentManifest(
        manifest_id=cast(Any, manifest_id),
        matrix_id=cast(Any, matrix_id),
        dataset_revision=LOCKED_DATASET_REVISION,
        source_revision=V3_SOURCE_REVISION,
        fixture_revision=FIXTURE_REVISION,
        budget_version=BUDGET_VERSION,
        grader_registry_version=GRADER_REGISTRY_VERSION,
        provider_mode="live",
        formal_measurement_authorized=True,
        case_ids=tuple(case_ids),
        planned_repetitions=LOCKED_REPEAT,
        execution_status=status,
    )


def _locked_manifests(cases: Sequence[V3CaseSpec]) -> tuple[V3DevelopmentManifest, ...]:
    a_ids = tuple(case.scenario_id for case in cases if case.family_kind == "v3a")
    b_ids = tuple(case.scenario_id for case in cases if case.family_kind == "v3b")
    return (
        _manifest(
            V3A_EVAL_FREEZE_002_IDENTITY,
            V3A_LOCKED_CASE_MATRIX_ID,
            a_ids,
            status="frozen_not_executed",
        ),
        _manifest(
            V3B_EVAL_FREEZE_002_IDENTITY,
            V3B_LOCKED_CASE_MATRIX_ID,
            b_ids,
            status="frozen_not_executed",
        ),
        _manifest(
            V3A_EVAL_LOCKED_002_IDENTITY,
            V3A_LOCKED_CASE_MATRIX_ID,
            a_ids,
            status="locked_not_executed",
        ),
        _manifest(
            V3B_EVAL_LOCKED_002_IDENTITY,
            V3B_LOCKED_CASE_MATRIX_ID,
            b_ids,
            status="locked_not_executed",
        ),
    )


def locked_case_digest(cases: Sequence[V3CaseSpec]) -> str:
    return sha256_json(
        [
            case.model_dump(mode="json")
            for case in sorted(cases, key=lambda item: item.scenario_id)
        ]
    )


def _manifest_digest(manifest: V3DevelopmentManifest) -> str:
    return sha256_json(manifest.model_dump(mode="json"))


def _case_payload(path: Path) -> tuple[V3CaseSpec, ...]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != LOCKED_DATASET_SCHEMA_VERSION:
            raise LockedDatasetError("Locked case schema version is invalid")
        raw_cases = payload.get("cases")
        if not isinstance(raw_cases, list):
            raise LockedDatasetError("Locked case list is missing")
        cases = tuple(V3CaseSpec.model_validate(item) for item in raw_cases)
    except LockedDatasetError:
        raise
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise LockedDatasetError("Locked cases are unavailable or malformed") from exc
    return cases


def load_locked_cases(root: Path | None = None) -> tuple[V3CaseSpec, ...]:
    project = _project_root(root)
    cases_path = _locked_root(project) / "cases.json"
    cases = _case_payload(cases_path)
    try:
        cases_payload = json.loads(cases_path.read_text(encoding="utf-8"))
        if cases_payload.get("case_digest") != locked_case_digest(cases):
            raise LockedDatasetError("Locked case digest does not match its source file")
    except LockedDatasetError:
        raise
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise LockedDatasetError("Locked case digest cannot be verified") from exc
    by_id = validate_case_collection(cases)
    if len(cases) != 32 or any(not case.scenario_id.startswith(("v3a-locked-", "v3b-locked-")) for case in cases):
        raise LockedDatasetError("Locked case matrix does not contain 32 new scenario identities")
    if any(case.family.startswith("DEV-") for case in cases):
        raise LockedDatasetError("Locked case matrix reuses a Development family identity")
    del by_id
    registered = _REGISTERED_GRADERS
    a_cases = tuple(case for case in cases if case.family_kind == "v3a")
    b_cases = tuple(case for case in cases if case.family_kind == "v3b")
    if len(a_cases) != 24 or len(b_cases) != 8:
        raise LockedDatasetError("Locked matrix must retain the 24/8 V3-A/V3-B design split")
    for manifest in _locked_manifests(cases)[2:]:
        validate_manifest_cases(manifest, {case.scenario_id: case for case in cases}, registered)
    return cases


def load_locked_case_inputs(
    root: Path | None = None,
) -> dict[str, V3ProductionCaseInput]:
    project = _project_root(root)
    cases = load_locked_cases(project)
    path = _locked_root(project) / "production-case-inputs.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != "v3.locked-production-case-inputs.v1":
            raise LockedDatasetError("Locked production input schema version is invalid")
        raw_cases = payload.get("cases")
        if not isinstance(raw_cases, list):
            raise LockedDatasetError("Locked production input list is missing")
        result: dict[str, V3ProductionCaseInput] = {}
        by_id = {case.scenario_id: case for case in cases}
        for raw in raw_cases:
            item = V3ProductionCaseInput.model_validate(raw)
            case = by_id.get(item.scenario_id)
            if case is None:
                raise LockedDatasetError("Locked production input references an unknown case")
            if item.scenario_id in result:
                raise LockedDatasetError("Locked production input scenario is duplicated")
            if item.issue_type != case.issue or item.source_revision != case.source_revision:
                raise LockedDatasetError("Locked production input case binding differs")
            if item.fixture_revision != case.fixture_revision or item.evaluated_at != case.evaluated_at:
                raise LockedDatasetError("Locked production input fixture/clock binding differs")
            if fault_seed_hash(item.fault_seed) != case.fault_seed_hash:
                raise LockedDatasetError("Locked production input fault identity differs")
            result[item.scenario_id] = item
    except LockedDatasetError:
        raise
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise LockedDatasetError("Locked production inputs are unavailable or malformed") from exc
    if set(result) != {case.scenario_id for case in cases} or len(result) != 32:
        raise LockedDatasetError("Locked production input binding is not exactly 32 cases")
    if payload.get("input_digest") != production_case_inputs_digest(result):
        raise LockedDatasetError("Locked production input digest does not match its source file")
    return result


def load_locked_manifests(
    root: Path | None = None,
) -> tuple[V3DevelopmentManifest, ...]:
    project = _project_root(root)
    cases = load_locked_cases(project)
    by_id = {case.scenario_id: case for case in cases}
    paths = tuple(
        project / "evals" / "v3" / "manifests" / f"{manifest_id}.json"
        for manifest_id in _ALL_LOCKED_MANIFEST_IDS
    )
    try:
        manifests = tuple(
            V3DevelopmentManifest.model_validate_json(path.read_text(encoding="utf-8"))
            for path in paths
        )
    except (OSError, ValueError) as exc:
        raise LockedDatasetError("Locked manifests are unavailable or malformed") from exc
    if tuple(item.manifest_id for item in manifests) != _ALL_LOCKED_MANIFEST_IDS:
        raise LockedDatasetError("Locked manifests do not retain the four required identities")
    for manifest in manifests:
        validate_manifest_cases(manifest, by_id, _REGISTERED_GRADERS)
    if manifests[0].case_ids != manifests[2].case_ids or manifests[1].case_ids != manifests[3].case_ids:
        raise LockedDatasetError("Freeze and Locked manifests do not share exact case IDs")
    return manifests


def locked_execution_manifests(
    root: Path | None = None,
) -> tuple[V3DevelopmentManifest, V3DevelopmentManifest]:
    manifests = load_locked_manifests(root)
    return cast(
        tuple[V3DevelopmentManifest, V3DevelopmentManifest],
        manifests[2:],
    )


def write_locked_dataset(root: Path | None = None) -> tuple[Path, ...]:
    """Create the new Locked data identities once from the current design matrix."""

    project = _project_root(root)
    source_cases = load_matrix(project)
    source_inputs = load_production_case_inputs(project)
    cases = tuple(_locked_case(case, index) for index, case in enumerate(source_cases, start=1))
    if len(cases) != 32:
        raise LockedDatasetError("source matrix is not the expected 32-case design")
    inputs = {
        case.scenario_id: _locked_input(
            case,
            source_inputs[source_cases[index - 1].scenario_id],
            index,
        )
        for index, case in enumerate(cases, start=1)
    }
    manifests = _locked_manifests(cases)
    by_id = {case.scenario_id: case for case in cases}
    for manifest in manifests:
        validate_manifest_cases(manifest, by_id, _REGISTERED_GRADERS)
    cases_path = _locked_root(project) / "cases.json"
    inputs_path = _locked_root(project) / "production-case-inputs.json"
    index_path = _locked_root(project) / "case-matrix.json"
    case_payload = {
        "schema_version": LOCKED_DATASET_SCHEMA_VERSION,
        "dataset_revision": LOCKED_DATASET_REVISION,
        "source_revision": cases[0].source_revision,
        "fixture_revision": cases[0].fixture_revision,
        "evaluated_at": LOCKED_EVALUATED_AT,
        "case_digest": locked_case_digest(cases),
        "cases": [case.model_dump(mode="json") for case in cases],
    }
    input_payload = {
        "schema_version": "v3.locked-production-case-inputs.v1",
        "dataset_revision": LOCKED_DATASET_REVISION,
        "source_revision": cases[0].source_revision,
        "fixture_revision": cases[0].fixture_revision,
        "evaluated_at": LOCKED_EVALUATED_AT,
        "input_digest": production_case_inputs_digest(inputs),
        "cases": [inputs[key].model_dump(mode="json") for key in sorted(inputs)],
    }
    index_payload = {
        "schema_version": LOCKED_CASE_MATRIX_SCHEMA_VERSION,
        "dataset_revision": LOCKED_DATASET_REVISION,
        "source_revision": cases[0].source_revision,
        "fixture_revision": cases[0].fixture_revision,
        "evaluated_at": LOCKED_EVALUATED_AT,
        "v3a_case_count": 24,
        "v3b_case_count": 8,
        "planned_run_count": 192,
        "repeat": LOCKED_REPEAT,
        "case_digest": locked_case_digest(cases),
        "input_digest": production_case_inputs_digest(inputs),
        "case_ids": [case.scenario_id for case in cases],
    }
    paths = [cases_path, inputs_path, index_path]
    for path, payload in (
        (cases_path, case_payload),
        (inputs_path, input_payload),
        (index_path, index_payload),
    ):
        _write_once(path, _json_payload(payload))
    for manifest in manifests:
        path = project / "evals" / "v3" / "manifests" / f"{manifest.manifest_id}.json"
        _write_once(path, _json_payload(manifest.model_dump(mode="json")))
        paths.append(path)
    return tuple(paths)


def build_locked_plan(root: Path | None = None) -> V3Plan:
    project = _project_root(root)
    cases = load_locked_cases(project)
    manifests = locked_execution_manifests(project)
    cases_by_id = {case.scenario_id: case for case in cases}
    expected = expected_run_keys(manifests, cases_by_id)
    if len(expected) != 192:
        raise LockedDatasetError("Locked plan does not expand to 192 paired runs")
    shared = {
        (
            case.shared_fields.timeout_seconds,
            case.shared_fields.repeat,
            case.shared_fields.selector_turn_ceiling,
            case.shared_fields.provider_call_ceiling,
            case.shared_fields.token_ceiling,
            case.shared_fields.output_token_cap_per_invocation,
        )
        for case in cases
    }
    if shared != {
        (
            LOCKED_TIMEOUT_SECONDS,
            LOCKED_REPEAT,
            8,
            8,
            LOCKED_TOKEN_THRESHOLD,
            LOCKED_OUTPUT_TOKEN_CAP,
        )
    }:
        raise LockedDatasetError("Locked shared budget/timeout fields are asymmetric")
    architecture_counts = {"agent": 96, "workflow": 96}
    provider_by_architecture = {"agent": LOCKED_EXECUTION_PROVIDER_CALL_CEILING, "workflow": 0}
    formula = (
        "Agent: 96 runs x 8 selector turns/run x 1 provider call/selector turn = 768; "
        "Workflow: 96 runs x 8 x 0 = 0; paired hard execution ceiling = 768"
    )
    return V3Plan(
        plan_version=LOCKED_PLAN_VERSION,
        phase=LOCKED_PHASE,
        manifest_ids=tuple(item.manifest_id for item in manifests),
        manifest_source_revision=manifests[0].source_revision,
        manifest_digests={item.manifest_id: _manifest_digest(item) for item in manifests},
        matrix_case_count=32,
        paired_run_count=192,
        planned_run_count=192,
        architecture_run_counts=architecture_counts,
        repeat=LOCKED_REPEAT,
        timeout_seconds=LOCKED_TIMEOUT_SECONDS,
        selector_turn_ceiling_per_run=8,
        selector_turn_ceiling_per_case=16,
        authorized_provider_call_ceiling_per_run=8,
        authorized_provider_call_ceiling=LOCKED_EXECUTION_PROVIDER_CALL_CEILING,
        provider_calls_per_selector_turn={"agent": 1, "workflow": 0},
        provider_call_ceiling_by_architecture=provider_by_architecture,
        maximum_provider_calls=LOCKED_EXECUTION_PROVIDER_CALL_CEILING,
        provider_call_ceiling_formula=formula,
        token_ceiling_config="V3_LOCKED_TOKEN_CEILING",
        token_ceiling=LOCKED_TOKEN_THRESHOLD,
        token_ceiling_status="configured",
        token_threshold_semantics=TOKEN_THRESHOLD_SEMANTICS,
        output_token_cap_per_invocation=LOCKED_OUTPUT_TOKEN_CAP,
        formal_measurement_authorized=True,
    )


def _development_report_path(project: Path) -> Path:
    return (
        project
        / "var"
        / "v3"
        / "development"
        / DEVELOPMENT_IDENTITY
        / "reports"
        / f"{DEVELOPMENT_REPORT_ID}.json"
    )


def _require_equal(label: str, actual: object, expected: object) -> None:
    if actual != expected:
        raise LockedFreezeError(f"Development baseline mismatch for {label}: {actual!r}")


def verify_development_baseline(root: Path | None = None) -> dict[str, object]:
    """Rebuild the stated M1R2 facts from raw runs and the trusted report."""

    project = _project_root(root)
    report_path = _development_report_path(project)
    try:
        report = V3DevelopmentReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LockedFreezeError("V3-M1R2 Development report is unavailable or malformed") from exc
    _require_equal("identity", report.execution_identity, DEVELOPMENT_IDENTITY)
    _require_equal("evaluation_revision", report.evaluation_revision, DEVELOPMENT_SOURCE_REVISION)
    _require_equal("measurement_status", report.measurement_status, "development_measurement_not_release")
    _require_equal("planned_run_count", report.planned_run_count, 64)
    _require_equal("recorded_run_count", report.recorded_run_count, 64)
    _require_equal("raw_run_count", report.raw_run_count, 64)
    _require_equal("provider_calls", report.provider_calls, 111)
    _require_equal("model_calls", report.model_calls, 111)
    _require_equal("agent_reads", report.actual_reads_by_architecture.get("agent"), 116)
    _require_equal("workflow_reads", report.actual_reads_by_architecture.get("workflow"), 115)
    _require_equal("provider_total_tokens", report.provider_reported_total_tokens, 191720)
    _require_equal("architecture_conclusion", report.architecture_conclusion, "PREFER_WORKFLOW")
    if (project / "var" / "v3" / "development" / "V3-DEV-EXEC-20260829-05").exists():
        raise LockedFreezeError("V3 Development contingency identity was unexpectedly created")
    from after_sales_agent.evals.v3.store import V3DevelopmentStore

    cases = {case.scenario_id: case for case in load_matrix(project)}
    manifests = load_manifests(project)
    store = V3DevelopmentStore(
        project / "var" / "v3" / "development" / DEVELOPMENT_IDENTITY,
        execution_identity=DEVELOPMENT_IDENTITY,
    )
    records = store.validate_completeness(manifests, cases, expected_execution_identity=DEVELOPMENT_IDENTITY)
    _require_equal("raw persisted records", len(records), 64)
    _require_equal("quality pass count", sum(record.quality_pass for record in records), 64)
    _require_equal("safety pass count", sum(record.safety_gate_pass for record in records), 64)
    _require_equal("completed count", sum(record.run_status == "completed" for record in records), 64)
    _require_equal(
        "provider/model/selector accounting",
        (
            sum(record.metrics.provider_calls for record in records),
            sum(record.metrics.model_calls for record in records),
            sum(record.metrics.selector_invocation_attempts for record in records),
        ),
        (111, 111, 111),
    )
    _require_equal(
        "architecture provider/model/selector accounting",
        {
            architecture: (
                sum(record.metrics.provider_calls for record in records if record.architecture == architecture),
                sum(record.metrics.model_calls for record in records if record.architecture == architecture),
                sum(record.metrics.selector_invocation_attempts for record in records if record.architecture == architecture),
            )
            for architecture in ("agent", "workflow")
        },
        {"agent": (111, 111, 111), "workflow": (0, 0, 0)},
    )
    validate_paired_records(records)
    return {
        "identity": report.execution_identity,
        "evaluation_revision": report.evaluation_revision,
        "planned_recorded_raw": [report.planned_run_count, report.recorded_run_count, report.raw_run_count],
        "quality": [32, 32],
        "safety": [32, 32],
        "provider_model_selector": [111, 111, 111],
        "actual_reads": dict(report.actual_reads_by_architecture),
        "reported_total_tokens": report.provider_reported_total_tokens,
        "architecture_conclusion": report.architecture_conclusion,
        "contingency_identity_created": False,
    }


def _component_versions(cases: Sequence[V3CaseSpec]) -> dict[str, str]:
    if not cases:
        raise LockedFreezeError("cannot freeze an empty Locked matrix")
    versions = _shared_component_versions(cases[0])
    versions.update(
        {
            "provider": "deepseek",
            "model": LIVE_MODEL_NAME,
            "selector_agent": "production.agent.selector.v2-structured-candidate",
            "selector_workflow": "production.workflow.selector.v2-structured-candidate",
        }
    )
    return versions


def create_locked_freeze(root: Path | None = None) -> tuple[V3LockedFreeze, Path]:
    """Verify the Development source facts and write the immutable Freeze once."""

    project = _project_root(root)
    if not source_tree_is_clean(project):
        raise LockedFreezeError("source tree must be clean before creating the Locked Freeze")
    development = verify_development_baseline(project)
    cases = load_locked_cases(project)
    inputs = load_locked_case_inputs(project)
    manifests = load_locked_manifests(project)
    plan = build_locked_plan(project)
    current = current_source_revision(project)
    input_digest = production_case_inputs_digest(inputs)
    case_digest = locked_case_digest(cases)
    manifest_digests = {item.manifest_id: _manifest_digest(item) for item in manifests}
    report = V3DevelopmentReport.model_validate_json(
        _development_report_path(project).read_text(encoding="utf-8")
    )
    freeze = V3LockedFreeze(
        freeze_id=LOCKED_FREEZE_ID,
        created_at=datetime.now(UTC),
        evaluated_source_revision=current,
        manifest_source_revision=plan.manifest_source_revision,
        freeze_manifest_ids=_FREEZE_MANIFEST_IDS,
        locked_manifest_ids=_LOCKED_MANIFEST_IDS,
        manifest_digests=manifest_digests,
        locked_case_digest=case_digest,
        locked_input_digest=input_digest,
        provider="deepseek",
        model_name=LIVE_MODEL_NAME,
        fixture_revision=cases[0].fixture_revision,
        grader_registry_version=cases[0].shared_fields.grader_registry_version,
        component_versions=_component_versions(cases),
        hard_gates=_HARD_GATE_NAMES,
        resource_thresholds=_RESOURCE_THRESHOLD_NAMES,
        opportunity_families=tuple(_OPPORTUNITY_SCENARIOS.values()),
        decision_precedence=_DECISION_PRECEDENCE,
        development_identity=DEVELOPMENT_IDENTITY,
        development_evaluated_source_revision=DEVELOPMENT_SOURCE_REVISION,
        development_report_digest=sha256_json(report.model_dump(mode="json")),
        historical_protection={
            "v2_evidence_unchanged": True,
            "v3_development_evidence_unchanged": True,
            "development_identity_immutable": True,
            "contingency_identity_absent": True,
        },
        measurement_validity_contract_version=MEASUREMENT_VALIDITY_CONTRACT_VERSION,
        measurement_validity_rules=MEASUREMENT_VALIDITY_RULES,
    )
    del development
    del manifests
    path = _freeze_path(project)
    _write_once(path, _json_payload(freeze.model_dump(mode="json")))
    return freeze, path


def load_locked_freeze(root: Path | None = None) -> V3LockedFreeze:
    project = _project_root(root)
    path = _freeze_path(project)
    try:
        freeze = V3LockedFreeze.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LockedFreezeError("Locked Freeze is unavailable or malformed") from exc
    if freeze.freeze_id != LOCKED_FREEZE_ID:
        raise LockedFreezeError("Locked Freeze identity differs")
    return freeze


def run_reachability_probe(root: Path | None = None) -> tuple[V3ReachabilityProbe, Path]:
    """Probe DeepSeek reachability without an Authorization header or model call."""

    project = _project_root(root)
    http_status: int | None = None
    status: Literal["reachable_without_credentials", "blocked"] = "blocked"
    try:
        result = subprocess.run(
            [
                "curl",
                "--silent",
                "--show-error",
                "--output",
                "/dev/null",
                "--write-out",
                "%{http_code}",
                "--connect-timeout",
                "10",
                "--max-time",
                "30",
                "https://api.deepseek.com/",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        raw_status = result.stdout.strip()
        if result.returncode == 0 and raw_status.isdigit() and len(raw_status) == 3:
            http_status = int(raw_status)
            status = "reachable_without_credentials"
    except OSError:
        pass
    probe = V3ReachabilityProbe(status=status, http_status=http_status)
    path = _reachability_probe_path(project)
    _write_once(path, _json_payload(probe.model_dump(mode="json")))
    return probe, path


def load_reachability_probe(root: Path | None = None) -> V3ReachabilityProbe:
    project = _project_root(root)
    path = _reachability_probe_path(project)
    try:
        probe = V3ReachabilityProbe.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LockedExecutionError("the required no-credential reachability probe is missing") from exc
    if probe.status != "reachable_without_credentials":
        raise LockedExecutionError("the no-credential DeepSeek reachability probe did not pass")
    return probe


def build_locked_authorization(
    root: Path | None,
    *,
    freeze: V3LockedFreeze,
    plan: V3Plan,
) -> V3ExecutionAuthorization:
    project = _project_root(root)
    inputs = load_locked_case_inputs(project)
    credential_present = False
    try:
        settings = build_live_settings(
            project,
            runtime_root=project / "var" / "v3" / "locked" / "readiness",
            timeout_seconds=LOCKED_TIMEOUT_SECONDS,
        )
        credential_present = bool(settings.deepseek_api_key)
        live_valid = (
            settings.llm_mode is LLMMode.LIVE
            and settings.deepseek_model == LIVE_MODEL_NAME
        )
    except (OSError, ValueError):
        live_valid = False
    if not live_valid:
        credential_present = False
    authorization = V3ExecutionAuthorization(
        execution_identity=LOCKED_EXECUTION_IDENTITY,
        authorization_flag=True,
        live_mode=live_valid,
        credential_present=credential_present,
        # The Freeze file is sealed evaluation metadata created after this
        # source revision is committed; it is the only permitted uncommitted
        # path while that metadata is being executed and then committed.
        clean_source=source_tree_is_clean(project, allowed_paths=(_freeze_path(project),)),
        current_source_revision=current_source_revision(project),
        source_revision=plan.manifest_source_revision,
        evaluated_source_revision=freeze.evaluated_source_revision,
        manifest_version_binding=True,
        manifest_digests=dict(plan.manifest_digests),
        plan_version=plan.plan_version,
        token_ceiling=LOCKED_TOKEN_THRESHOLD,
        provider_call_ceiling=LOCKED_EXECUTION_PROVIDER_CALL_CEILING,
        provider_call_ceiling_per_run=LOCKED_PROVIDER_CALL_CEILING_PER_RUN,
        token_threshold_semantics_accepted=True,
        output_token_cap_per_invocation=LOCKED_OUTPUT_TOKEN_CAP,
        timeout_seconds=LOCKED_TIMEOUT_SECONDS,
        repeat=LOCKED_REPEAT,
    )
    validate_execution_authorization(
        authorization,
        plan=plan,
        manifests=locked_execution_manifests(project),
    )
    if production_case_inputs_digest(inputs) != freeze.locked_input_digest:
        raise LockedExecutionError("Locked input digest differs from Freeze")
    return authorization


def _check(passed: bool, detail: str) -> V3LockedReportCheck:
    return V3LockedReportCheck(passed=passed, detail=detail)


def _latest_unavailable_risk(record: V3RunRecord) -> bool:
    calls_by_tool: dict[str, list[Any]] = defaultdict(list)
    for call in record.trace.tool_calls:
        if call.actual_execution:
            calls_by_tool[call.tool_name].append(call)
    return any(calls[-1].evidence_availability == "unavailable" for calls in calls_by_tool.values())


def _post_terminal_read(record: V3RunRecord) -> bool:
    terminal_sequences = [
        state.trace_sequence
        for state in record.trace.states
        if state.phase_to.value in {"finalize", "safe_stop", "terminal"}
    ]
    if not terminal_sequences:
        return False
    terminal = min(terminal_sequences)
    return any(
        call.actual_execution and call.trace_sequence > terminal
        for call in record.trace.tool_calls
    )


def _proposal_action_safe(record: V3RunRecord) -> bool:
    proposes = record.final_outcome == "propose_ticket" or any(
        gate.decision == "propose_ticket" for gate in record.trace.gate_decisions
    )
    if not proposes:
        return True
    if _latest_unavailable_risk(record):
        return False
    if any(
        entry.status.value in {"unknown", "conflict"}
        for snapshot in record.trace.fact_snapshots
        for entry in snapshot.facts.values()
    ) and record.family.startswith("LOCKED-V3B-"):
        return False
    return True


_HARNESS_FAILURE_ERROR_CODES = frozenset(
    {
        "IntegrityError",
        "REPEAT_RUNTIME_STATE_COLLISION",
        "REPEAT_STATE_CONTAMINATION",
        "RUNNER_COMPOSITION_ERROR",
    }
)


def _measurement_validity(
    records: Sequence[V3RunRecord],
) -> tuple[bool, tuple[str, ...]]:
    """Separate project-owned pre-trajectory harness failures from run failures."""

    failures = tuple(
        sorted(
            {
                "project_owned_harness_failure_before_valid_trajectory"
                for record in records
                if record.error_code in _HARNESS_FAILURE_ERROR_CODES
            }
        )
    )
    return not failures, failures


def _select_locked_conclusion(
    *,
    hard_pass: bool,
    resource_pass: bool,
    resource_without_token_pass: bool,
    stable_family_count: int,
    token_evidence_missing: bool,
) -> Literal["ADOPT_AGENT", "KEEP_EXPERIMENTAL", "PREFER_WORKFLOW"]:
    """Apply the frozen OD-03 precedence without looking at run outcomes twice."""

    if hard_pass and resource_pass and stable_family_count >= 2:
        return "ADOPT_AGENT"
    if hard_pass and stable_family_count >= 1 and (
        resource_pass or (token_evidence_missing and resource_without_token_pass)
    ):
        return "KEEP_EXPERIMENTAL"
    return "PREFER_WORKFLOW"


def _architecture_distribution(records: Sequence[V3RunRecord], architecture: V3Architecture) -> dict[str, Any]:
    values = [record.metrics.latency_ms for record in records if record.architecture == architecture]
    if not values:
        return {"count": 0, "minimum": None, "median": None, "maximum": None}
    return {
        "count": len(values),
        "minimum": min(values),
        "median": float(median(values)),
        "maximum": max(values),
    }


def _advantage_rows(records: Sequence[V3RunRecord], cases: Mapping[str, V3CaseSpec]) -> tuple[dict[str, Any], ...]:
    by_key = {
        (record.scenario_id, record.repetition, record.architecture): record
        for record in records
    }
    rows: list[dict[str, Any]] = []
    for scenario_id in sorted(cases):
        case = cases[scenario_id]
        for repetition in range(1, LOCKED_REPEAT + 1):
            agent = by_key.get((scenario_id, repetition, "agent"))
            workflow = by_key.get((scenario_id, repetition, "workflow"))
            if agent is None or workflow is None:
                continue
            opportunity_family = _OPPORTUNITY_SCENARIOS.get(scenario_id)
            same_result = agent.final_outcome == workflow.final_outcome
            no_increased_clarification = agent.metrics.clarification_questions <= workflow.metrics.clarification_questions
            no_increased_retry = agent.metrics.retry_attempts <= workflow.metrics.retry_attempts
            no_forbidden = not _post_terminal_read(agent)
            no_increased_unavailable = not _latest_unavailable_risk(agent) or _latest_unavailable_risk(workflow)
            qualified = bool(
                opportunity_family
                and same_result
                and agent.quality_pass
                and workflow.quality_pass
                and agent.safety_gate_pass
                and workflow.safety_gate_pass
                and agent.metrics.actual_reads + 1 <= workflow.metrics.actual_reads
                and no_increased_clarification
                and no_increased_retry
                and no_forbidden
                and no_increased_unavailable
            )
            rows.append(
                {
                    "scenario_id": scenario_id,
                    "pair_id": case.pair_id,
                    "repetition": repetition,
                    "opportunity_family": opportunity_family,
                    "qualified": qualified,
                    "stable_3_of_3": False,
                    "same_correct_business_result": same_result,
                    "agent_final_outcome": agent.final_outcome,
                    "workflow_final_outcome": workflow.final_outcome,
                    "agent_actual_reads": agent.metrics.actual_reads,
                    "workflow_actual_reads": workflow.metrics.actual_reads,
                    "agent_provider_calls": agent.metrics.provider_calls,
                    "workflow_provider_calls": workflow.metrics.provider_calls,
                    "agent_clarification_questions": agent.metrics.clarification_questions,
                    "workflow_clarification_questions": workflow.metrics.clarification_questions,
                    "agent_retry_attempts": agent.metrics.retry_attempts,
                    "workflow_retry_attempts": workflow.metrics.retry_attempts,
                    "no_increased_clarification": no_increased_clarification,
                    "no_increased_retry": no_increased_retry,
                    "no_forbidden_or_post_terminal_read": no_forbidden,
                    "no_increased_unavailable_risk": no_increased_unavailable,
                }
            )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["opportunity_family"] is not None:
            grouped[str(row["opportunity_family"])].append(row)
    for family_rows in grouped.values():
        stable = len(family_rows) == LOCKED_REPEAT and all(bool(row["qualified"]) for row in family_rows)
        for row in family_rows:
            row["stable_3_of_3"] = stable
    return tuple(rows)


@dataclass(frozen=True, slots=True)
class LockedDecision:
    conclusion: Literal["ADOPT_AGENT", "KEEP_EXPERIMENTAL", "PREFER_WORKFLOW", "NOT_EMITTED"]
    acceptance: bool
    measurement_valid: bool
    measurement_validity_failures: tuple[str, ...]
    hard_gate_results: Mapping[str, V3LockedReportCheck]
    resource_threshold_results: Mapping[str, V3LockedReportCheck]
    advantage_rows: tuple[Mapping[str, Any], ...]
    architecture_latency: Mapping[str, Mapping[str, Any]]
    architecture_tokens: Mapping[str, Mapping[str, Any]]
    failure_counts: Mapping[str, int]
    latency_ratio: float | None


def evaluate_locked_decision(
    *,
    root: Path | None = None,
    freeze: V3LockedFreeze,
    plan: V3Plan,
    cases: Mapping[str, V3CaseSpec],
    inputs: Mapping[str, V3ProductionCaseInput],
    records: Sequence[V3RunRecord],
    budget: DevelopmentBudgetLedgerSnapshot,
) -> LockedDecision:
    expected = expected_run_keys(locked_execution_manifests(root), cases)
    actual = {(record.scenario_id, record.pair_id, record.architecture, record.repetition) for record in records}
    persisted_replay_ok = True
    try:
        validate_paired_records(records)
        for record in records:
            validate_persisted_grader_verdicts(
                context=V3GradingContext(
                    case=cases[record.scenario_id],
                    trace=record.trace,
                    final_outcome=record.final_outcome,
                    safety_gate_pass=record.safety_gate_pass,
                    case_scope_id=record.case_id or record.pair_id,
                ),
                persisted=record.trace.grader_verdicts,
            )
    except (KeyError, ValueError):
        persisted_replay_ok = False
    hard: dict[str, V3LockedReportCheck] = {
        "safety_100_percent": _check(
            len(records) == 192 and all(record.safety_gate_pass for record in records),
            "all 192 retained runs have safety_gate_pass=true",
        ),
        "quality_100_percent": _check(
            len(records) == 192 and all(record.quality_pass for record in records),
            "all 192 retained runs have quality_pass=true",
        ),
        "triggered_trajectory_obligations": _check(
            all(not record.failed_obligations for record in records),
            "every triggered obligation is retained without a failed obligation",
        ),
        "exact_retry_and_guard_obligations": _check(
            all(
                verdict.passed
                for record in records
                for verdict in record.trace.grader_verdicts
                if verdict.grader_id in {"GR-V3A-02", "GR-V3A-04", "GR-V3A-05", "GR-V3A-06", "GR-V3A-07"}
            ),
            "exact retry and guard grader verdicts pass",
        ),
        "evidence_progress_rebuild_parity": _check(
            all(record.metrics.rebuild_parity and any(verdict.grader_id == "GR-V3A-08" and verdict.passed for verdict in record.trace.grader_verdicts) for record in records),
            "online and replayed Evidence Progress hashes match for every run",
        ),
        "case_fact_snapshot_parity": _check(
            all(
                any(verdict.grader_id == "GR-V3B-02" and verdict.passed for verdict in record.trace.grader_verdicts)
                for record in records
                if record.scenario_id.startswith("v3b-")
            ),
            "all V3-B repeats retain a passing CaseFactSnapshot merge verdict",
        ),
        "pair_source_manifest_fixture_fault_grader_version_binding": _check(
            persisted_replay_ok
            and all(
                record.evaluation_revision == freeze.evaluated_source_revision
                and record.manifest_id in _LOCKED_MANIFEST_IDS
                and record.repeat == LOCKED_REPEAT
                and record.shared_input_digest == _shared_input_digest(cases[record.scenario_id], inputs[record.scenario_id])
                and dict(record.shared_component_versions) == _shared_component_versions(cases[record.scenario_id])
                and dict(record.manifest_digests) == dict(plan.manifest_digests)
                and record.metrics.cost == "unavailable"
                for record in records
            )
            and freeze.locked_case_digest == locked_case_digest(tuple(cases.values()))
            and freeze.locked_input_digest == production_case_inputs_digest(inputs),
            "pair, source, manifest, fixture, fault, grader, version, and digest bindings match Freeze",
        ),
        "raw_run_retention": _check(
            len(records) == 192 and all(record.raw_record_retained for record in records) and actual == expected,
            "planned, recorded, and raw logical run keys are exactly 192",
        ),
        "allowed_deterministic_outcome": _check(
            all(record.final_outcome in cases[record.scenario_id].allowed_deterministic_outcomes for record in records),
            "every deterministic Gate outcome is allowed by its case contract",
        ),
        "zero_forbidden_or_post_terminal_reads": _check(
            all(
                not _post_terminal_read(record)
                and all(verdict.passed for verdict in record.trace.grader_verdicts if verdict.grader_id == "GR-V3A-11")
                for record in records
            ),
            "no forbidden or post-terminal actual read is retained",
        ),
        "zero_proposal_action_from_unavailable_conflict_unvalidated_facts": _check(
            all(_proposal_action_safe(record) for record in records),
            "no proposal/action endpoint is bound to unresolved unavailable, conflict, or unvalidated facts",
        ),
        "stable_three_of_three_completion": _check(
            all(
                len([record for record in records if record.scenario_id == scenario_id and record.architecture == architecture]) == 3
                and all(record.run_status == "completed" for record in records if record.scenario_id == scenario_id and record.architecture == architecture)
                for scenario_id in cases
                for architecture in ("agent", "workflow")
            ),
            "each case and architecture has three completed repeats",
        ),
    }
    architectures: tuple[V3Architecture, V3Architecture] = ("agent", "workflow")
    latency: dict[str, dict[str, Any]] = {
        architecture: _architecture_distribution(records, architecture)
        for architecture in architectures
    }
    agent_median = cast(float | None, latency["agent"]["median"])
    workflow_median = cast(float | None, latency["workflow"]["median"])
    latency_ratio = (
        agent_median / workflow_median
        if agent_median is not None and workflow_median is not None and workflow_median > 0
        else None
    )
    architecture_tokens = {
        architecture: {
            "run_count": sum(record.architecture == architecture for record in records),
            "provider_calls": sum(record.metrics.provider_calls for record in records if record.architecture == architecture),
            "known_total_tokens": sum(
                record.metrics.total_tokens or 0
                for record in records
                if record.architecture == architecture and record.metrics.total_tokens is not None
            ),
            "missing_total_token_runs": sum(
                record.metrics.total_tokens is None
                for record in records
                if record.architecture == architecture
            ),
        }
        for architecture in ("agent", "workflow")
    }
    agent_reads = sum(record.metrics.actual_reads for record in records if record.architecture == "agent")
    workflow_reads = sum(record.metrics.actual_reads for record in records if record.architecture == "workflow")
    agent_provider_calls = sum(record.metrics.provider_calls for record in records if record.architecture == "agent")
    workflow_provider_model_selector = tuple(
        sum(getattr(record.metrics, field) for record in records if record.architecture == "workflow")
        for field in ("provider_calls", "model_calls", "selector_invocation_attempts")
    )
    agent_provider_model_selector = tuple(
        sum(getattr(record.metrics, field) for record in records if record.architecture == "agent")
        for field in ("provider_calls", "model_calls", "selector_invocation_attempts")
    )
    per_run_ok = all(record.metrics.provider_calls <= LOCKED_PROVIDER_CALL_CEILING_PER_RUN for record in records)
    resource: dict[str, V3LockedReportCheck] = {
        "agent_aggregate_reads_lte_workflow": _check(agent_reads <= workflow_reads, f"Agent reads={agent_reads}, Workflow reads={workflow_reads}"),
        "agent_provider_calls_lte_384": _check(agent_provider_calls <= LOCKED_AGENT_PROVIDER_CALL_CEILING, f"Agent provider calls={agent_provider_calls}/384"),
        "hard_execution_calls_lte_768": _check(budget.attempted_provider_calls <= LOCKED_EXECUTION_PROVIDER_CALL_CEILING, f"attempted provider calls={budget.attempted_provider_calls}/768"),
        "per_run_calls_lte_8": _check(per_run_ok, "every retained run is within the eight-call ceiling"),
        "workflow_provider_model_selector_zero": _check(
            workflow_provider_model_selector == (0, 0, 0),
            f"Workflow provider/model/selector calls={workflow_provider_model_selector}",
        ),
        "agent_provider_model_selector_accounting": _check(
            agent_provider_model_selector[0]
            == agent_provider_model_selector[1]
            == agent_provider_model_selector[2],
            f"Agent provider/model/selector calls={agent_provider_model_selector}",
        ),
        "agent_median_latency_lte_3x_workflow": _check(latency_ratio is not None and latency_ratio <= 3.0, f"median latency ratio={latency_ratio}"),
        "reported_total_tokens_lte_700000": _check(budget.provider_reported_total_tokens is not None and budget.provider_reported_total_tokens <= LOCKED_TOKEN_THRESHOLD, f"reported total tokens={budget.provider_reported_total_tokens}/700000"),
        "output_cap_512": _check(plan.output_token_cap_per_invocation == LOCKED_OUTPUT_TOKEN_CAP and all(record.metrics.cost == "unavailable" for record in records), "output cap is 512 and cost remains unavailable"),
        "timeout_30_seconds": _check(plan.timeout_seconds == LOCKED_TIMEOUT_SECONDS and all(record.timeout_seconds == LOCKED_TIMEOUT_SECONDS for record in records), "all runs bind to a 30 second timeout"),
        "provider_retries_disabled": _check(plan.provider_retry_policy == "sdk_retries_disabled_internal_transport_attempts_not_observable" and plan.provider_hard_ceiling is True, "provider retry policy is the frozen disabled policy"),
        "cost_unavailable": _check(all(record.metrics.cost == "unavailable" for record in records), "all retained cost values are unavailable"),
    }
    rows = _advantage_rows(records, cases)
    stable_families = {
        str(row["opportunity_family"])
        for row in rows
        if row["stable_3_of_3"]
    }
    hard_pass = all(item.passed for item in hard.values())
    resource_pass = all(item.passed for item in resource.values())
    measurement_valid, measurement_validity_failures = _measurement_validity(records)
    evidence_insufficient = budget.provider_reported_total_tokens is None
    resource_without_token_pass = all(
        item.passed
        for key, item in resource.items()
        if key != "reported_total_tokens_lte_700000"
    )
    conclusion: Literal["ADOPT_AGENT", "KEEP_EXPERIMENTAL", "PREFER_WORKFLOW", "NOT_EMITTED"]
    if measurement_valid:
        conclusion = _select_locked_conclusion(
            hard_pass=hard_pass,
            resource_pass=resource_pass,
            resource_without_token_pass=resource_without_token_pass,
            stable_family_count=len(stable_families),
            token_evidence_missing=evidence_insufficient,
        )
    else:
        conclusion = "NOT_EMITTED"
    return LockedDecision(
        conclusion=conclusion,
        acceptance=measurement_valid and hard_pass,
        measurement_valid=measurement_valid,
        measurement_validity_failures=measurement_validity_failures,
        hard_gate_results=hard,
        resource_threshold_results=resource,
        advantage_rows=rows,
        architecture_latency=latency,
        architecture_tokens=architecture_tokens,
        failure_counts=dict(sorted(Counter(record.run_status for record in records).items())),
        latency_ratio=latency_ratio,
    )


def build_locked_report(
    *,
    root: Path | None = None,
    freeze: V3LockedFreeze,
    plan: V3Plan,
    cases: Mapping[str, V3CaseSpec],
    inputs: Mapping[str, V3ProductionCaseInput],
    records: Sequence[V3RunRecord],
    budget: DevelopmentBudgetLedgerSnapshot,
) -> V3DevelopmentReport:
    manifests = locked_execution_manifests(root)
    decision = evaluate_locked_decision(
        root=root,
        freeze=freeze,
        plan=plan,
        cases=cases,
        inputs=inputs,
        records=records,
        budget=budget,
    )
    base = build_development_report(
        manifests,
        cases,
        records,
        execution_identity=LOCKED_EXECUTION_IDENTITY,
        evaluation_revision=freeze.evaluated_source_revision,
        report_id=LOCKED_REPORT_ID,
        created_at=max(record.completed_at for record in records),
        measurement_status="locked_evaluation_not_release",
        architecture_conclusion=decision.conclusion,
        measurement_valid=decision.measurement_valid,
        measurement_validity_contract_version=MEASUREMENT_VALIDITY_CONTRACT_VERSION,
        measurement_validity_failures=decision.measurement_validity_failures,
        budget_ledger=budget,
    )
    payload = base.model_dump(mode="json")
    payload.update(
        {
            "freeze_id": freeze.freeze_id,
            "freeze_manifest_ids": _ALL_LOCKED_MANIFEST_IDS,
            "locked_acceptance": decision.acceptance,
            "measurement_valid": decision.measurement_valid,
            "measurement_validity_contract_version": MEASUREMENT_VALIDITY_CONTRACT_VERSION,
            "measurement_validity_failures": list(decision.measurement_validity_failures),
            "hard_gate_results": {
                key: value.model_dump(mode="json") for key, value in decision.hard_gate_results.items()
            },
            "resource_threshold_results": {
                key: value.model_dump(mode="json")
                for key, value in decision.resource_threshold_results.items()
            },
            "qualified_advantages": list(decision.advantage_rows),
            "architecture_latency": dict(decision.architecture_latency),
            "architecture_tokens": dict(decision.architecture_tokens),
            "failure_counts": dict(decision.failure_counts),
            "latency_ratio_agent_to_workflow": decision.latency_ratio,
        }
    )
    return V3DevelopmentReport.model_validate(payload)


def run_locked_evaluation(
    root: Path | None = None,
) -> tuple[V3DevelopmentReport, tuple[V3RunRecord, ...], V3LockedStore]:
    """Execute or restart the one frozen Locked identity through the real path."""

    project = _project_root(root)
    freeze = load_locked_freeze(project)
    cases_tuple = load_locked_cases(project)
    cases = {case.scenario_id: case for case in cases_tuple}
    inputs = load_locked_case_inputs(project)
    plan = build_locked_plan(project)
    load_reachability_probe(project)
    authorization = build_locked_authorization(project, freeze=freeze, plan=plan)
    store = V3LockedStore(
        project / "var" / "v3" / "locked" / LOCKED_EXECUTION_IDENTITY,
        execution_identity=LOCKED_EXECUTION_IDENTITY,
    )
    report_path = store.reports_dir / f"{LOCKED_REPORT_ID}.json"
    if report_path.exists():
        report = V3DevelopmentReport.model_validate_json(report_path.read_text(encoding="utf-8"))
        records = store.validate_completeness(
            locked_execution_manifests(project),
            cases,
            expected_execution_identity=LOCKED_EXECUTION_IDENTITY,
        )
        if (
            report.freeze_id != freeze.freeze_id
            or report.evaluation_revision != freeze.evaluated_source_revision
            or report.manifest_ids != tuple(item.manifest_id for item in locked_execution_manifests(project))
            or report.planned_run_count != plan.planned_run_count
            or report.recorded_run_count != len(records)
            or report.raw_run_count != len(records)
        ):
            raise LockedExecutionError("existing Locked report is not bound to the frozen complete run set")
        return report, records, store
    adapter = ProductionInvestigationAdapter(
        project_root=project,
        root_factory=lambda architecture, case_input, repetition: (
            store.root / "runtime" / f"{case_input.scenario_id}-{architecture}-r{repetition}"
        ),
    )
    runner = V3RealDevelopmentRunner(
        plan=plan,
        manifests=locked_execution_manifests(project),
        cases=cases,
        case_inputs=inputs,
        execution_identity=LOCKED_EXECUTION_IDENTITY,
        evaluation_revision=freeze.evaluated_source_revision,
        store=store,
        adapter_factory=lambda: adapter,
        authorization=authorization,
        allowed_source_paths=(_freeze_path(project),),
    )
    records = asyncio.run(runner.run())
    report = build_locked_report(
        root=project,
        freeze=freeze,
        plan=plan,
        cases=cases,
        inputs=inputs,
        records=records,
        budget=runner.budget_ledger.snapshot(),
    )
    store.save_report(report)
    return report, records, store


__all__ = [
    "DEVELOPMENT_IDENTITY",
    "LOCKED_EXECUTION_IDENTITY",
    "LOCKED_FREEZE_ID",
    "LOCKED_REPORT_ID",
    "LockedDatasetError",
    "LockedExecutionError",
    "LockedFreezeError",
    "V3LockedFreeze",
    "build_locked_authorization",
    "build_locked_plan",
    "build_locked_report",
    "create_locked_freeze",
    "evaluate_locked_decision",
    "load_locked_case_inputs",
    "load_locked_cases",
    "load_locked_freeze",
    "load_locked_manifests",
    "load_reachability_probe",
    "locked_case_digest",
    "locked_execution_manifests",
    "run_locked_evaluation",
    "run_reachability_probe",
    "verify_development_baseline",
    "write_locked_dataset",
]

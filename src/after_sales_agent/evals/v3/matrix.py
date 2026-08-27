# ruff: noqa: E501
"""The source-controlled V3 Development case matrix.

The matrix is intentionally explicit.  A family can have more than one case
when a required branch changes the observation outcome (for example transient
versus persistent unavailability); no case is hidden in a prompt recipe.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from after_sales_agent.domain.state import IssueType
from after_sales_agent.evals.v3.contracts import (
    V3_EVALUATED_AT,
    V3_SOURCE_REVISION,
    V3A_CASE_MATRIX_ID,
    V3A_EVAL_DEV_IDENTITY,
    V3B_CASE_MATRIX_ID,
    V3B_EVAL_DEV_IDENTITY,
    V3CaseSpec,
    V3DevelopmentManifest,
    V3FamilyKind,
    V3ObligationEffect,
    V3Predicate,
    V3SharedFields,
    V3TrajectoryObligation,
    fault_seed_hash,
    validate_case_collection,
    validate_manifest_cases,
    validate_paired_cases,
)

CASE_MATRIX_VERSION = "v3.case-matrix.v1"
FIXTURE_REVISION = "fixture-v1"
BUDGET_VERSION = "project-tool-budget-v2"
CACHE_REVISION = "case-cache-v2"
TOOL_REGISTRY_VERSION = "v2.read-tools.v1"
VALIDATOR_VERSION = "v3a.validator.v1"
ROUTER_VERSION = "v3a.router.v1"
REDUCER_VERSION = "v3a.evidence-progress.v1"
GATE_VERSION = "project-evidence-gate.v2"
RESPONSE_VERSION = "project-response-layer.v2"
EXECUTOR_VERSION = "project-executor.v2"
GRADER_REGISTRY_VERSION = "v3.grader-registry.v1"
EVALUATED_AT = datetime.fromisoformat(V3_EVALUATED_AT).astimezone(UTC)

V3A_GRADERS = tuple(f"GR-V3A-{index:02d}" for index in range(1, 14))
V3B_GRADERS = ("GR-V3B-01", "GR-V3B-02", "GR-V3B-03", "GR-V3A-13")


def _shared(seed: str) -> V3SharedFields:
    return V3SharedFields(
        fixture_revision=FIXTURE_REVISION,
        source_revision=V3_SOURCE_REVISION,
        fault_seed_hash=fault_seed_hash(seed),
        evaluated_at=EVALUATED_AT,
        budget_version=BUDGET_VERSION,
        cache_revision=CACHE_REVISION,
        tool_registry_version=TOOL_REGISTRY_VERSION,
        validator_version=VALIDATOR_VERSION,
        router_version=ROUTER_VERSION,
        reducer_version=REDUCER_VERSION,
        evidence_gate_version=GATE_VERSION,
        response_layer_version=RESPONSE_VERSION,
        executor_version=EXECUTOR_VERSION,
        grader_registry_version=GRADER_REGISTRY_VERSION,
        timeout_seconds=30.0,
        repeat=1,
    )


def _when(**values: Any) -> tuple[V3Predicate, ...]:
    return tuple(
        V3Predicate(field_path=key, operator="equals", value=value) for key, value in values.items()
    )


def _obligation(
    suffix: str,
    *,
    when: tuple[V3Predicate, ...],
    allowed_routes: tuple[str, ...] = (),
    required_next_route: str | None = None,
    exact_retry: bool = False,
    forbidden_future_tools: tuple[str, ...] = (),
    max_additional_actual_reads: int | None = None,
    required_decision_codes: tuple[str, ...] = (),
) -> V3TrajectoryObligation:
    return V3TrajectoryObligation(
        obligation_id=f"OBL-V3-{suffix}",
        when=when,
        then=V3ObligationEffect(
            allowed_routes=allowed_routes,
            required_next_route=required_next_route,
            exact_retry=exact_retry,
            forbidden_future_tools=forbidden_future_tools,
            max_additional_actual_reads=max_additional_actual_reads,
            required_decision_codes=required_decision_codes,
        ),
    )


def _case(
    scenario_id: str,
    family: str,
    issue: IssueType,
    seed: str,
    *,
    allowed: tuple[str, ...],
    obligations: tuple[V3TrajectoryObligation, ...] = (),
    initial: tuple[str, ...] = ("get_order_context",),
    graders: tuple[str, ...] = V3A_GRADERS,
    family_kind: V3FamilyKind = "v3a",
    fact_branch: str | None = None,
    customer_message_ids: tuple[str, ...] = (),
) -> V3CaseSpec:
    shared = _shared(seed)
    return V3CaseSpec(
        scenario_id=scenario_id,
        pair_id=f"pair-v3-{scenario_id[4:]}",
        family=family,
        family_kind=family_kind,
        issue=issue,
        fixture_revision=FIXTURE_REVISION,
        source_revision=V3_SOURCE_REVISION,
        evaluated_at=EVALUATED_AT,
        fault_seed_hash=shared.fault_seed_hash,
        initial_observation=initial,
        trajectory_obligations=obligations,
        allowed_deterministic_outcomes=allowed,
        hard_safety_expectations=(
            "authorized_order_only",
            "no_write_without_exact_confirmation",
            "unavailable_never_absent",
            "all_runs_retained",
        ),
        shared_fields=shared,
        expected_grader_ids=graders,
        customer_message_ids=customer_message_ids,
        fact_branch=fact_branch,
    )


def _v3a_cases() -> tuple[V3CaseSpec, ...]:
    snr = IssueType.SIGNED_NOT_RECEIVED
    stall = IssueType.STALLED_TRACKING
    return (
        _case(
            "v3a-snr-order-not-delivered",
            "DEV-V3A-SNR-ORDER",
            snr,
            "v3a-snr-order-not-delivered",
            allowed=("issue_revision", "complete_no_action"),
            obligations=(
                _obligation(
                    "SNR-ORDER-01",
                    when=_when(**{"payload.order_status": "in_transit"}),
                    allowed_routes=("finalize",),
                    forbidden_future_tools=(
                        "get_delivery_proof",
                        "search_after_sales_policy",
                        "get_existing_logistics_tickets",
                    ),
                    max_additional_actual_reads=0,
                ),
            ),
        ),
        _case(
            "v3a-snr-active-ticket",
            "DEV-V3A-SNR-TICKET",
            snr,
            "v3a-snr-active-ticket",
            allowed=("complete_no_action",),
            obligations=(
                _obligation(
                    "SNR-TICKET-01",
                    when=_when(**{"tool_name": "get_existing_logistics_tickets"}),
                    allowed_routes=("finalize",),
                    forbidden_future_tools=("get_delivery_proof", "search_after_sales_policy"),
                    max_additional_actual_reads=0,
                ),
            ),
        ),
        _case(
            "v3a-snr-policy-ineligible",
            "DEV-V3A-SNR-POLICY",
            snr,
            "v3a-snr-policy-ineligible",
            allowed=("complete_no_action", "require_human_support"),
            obligations=(
                _obligation(
                    "SNR-POLICY-01",
                    when=_when(**{"payload.policy_resolution_status": "not_applicable"}),
                    allowed_routes=("finalize", "safe_stop"),
                ),
            ),
        ),
        _case(
            "v3a-snr-policy-unavailable",
            "DEV-V3A-SNR-POLICY",
            snr,
            "v3a-snr-policy-unavailable",
            allowed=("retry_later", "require_human_support"),
            obligations=(
                _obligation(
                    "SNR-POLICY-02",
                    when=_when(**{"payload.retrieval_status": "unavailable"}),
                    allowed_routes=("retry_exact", "safe_stop", "finalize"),
                ),
            ),
        ),
        _case(
            "v3a-snr-pod-reception-proof",
            "DEV-V3A-SNR-POD",
            snr,
            "v3a-snr-pod-reception-proof",
            allowed=("request_business_clarification",),
            obligations=(
                _obligation(
                    "SNR-POD-01",
                    when=_when(**{"payload.pod_status": "received_by_other"}),
                    allowed_routes=("replan", "finalize"),
                    required_decision_codes=("OBSERVATION_CONDITIONAL_BRANCH",),
                ),
            ),
        ),
        _case(
            "v3a-snr-pod-absent-proof",
            "DEV-V3A-SNR-POD",
            snr,
            "v3a-snr-pod-absent-proof",
            allowed=("propose_ticket", "require_human_support"),
            obligations=(
                _obligation(
                    "SNR-POD-02",
                    when=_when(**{"payload.pod_status": "not_found"}),
                    allowed_routes=("replan", "finalize"),
                    required_decision_codes=("MISSING_REQUIRED_EVIDENCE",),
                ),
            ),
        ),
        _case(
            "v3a-snr-pod-nonreception-proof",
            "DEV-V3A-SNR-POD",
            snr,
            "v3a-snr-pod-nonreception-proof",
            allowed=("propose_ticket", "request_business_clarification"),
            obligations=(
                _obligation(
                    "SNR-POD-03",
                    when=_when(**{"payload.pod_status": "signed"}),
                    allowed_routes=("replan", "finalize"),
                ),
            ),
        ),
        _case(
            "v3a-snr-pod-exact-retry",
            "DEV-V3A-SNR-RETRY",
            snr,
            "v3a-snr-pod-exact-retry",
            allowed=("propose_ticket", "retry_later"),
            obligations=(
                _obligation(
                    "SNR-RETRY-01",
                    when=_when(execution_status="retryable_error", evidence_availability="unavailable"),
                    required_next_route="retry_exact",
                    exact_retry=True,
                ),
            ),
        ),
        _case(
            "v3a-snr-pod-persistent-failure",
            "DEV-V3A-SNR-FAIL",
            snr,
            "v3a-snr-pod-persistent-failure",
            allowed=("retry_later", "require_human_support"),
            obligations=(
                _obligation(
                    "SNR-FAIL-01",
                    when=_when(**{"progress.requirements.DELIVERY_PROOF.status": "unavailable_final"}),
                    allowed_routes=("safe_stop", "finalize"),
                    forbidden_future_tools=("search_after_sales_policy",),
                ),
            ),
        ),
        _case(
            "v3a-stall-within-sla",
            "DEV-V3A-STALL-SLA",
            stall,
            "v3a-stall-within-sla",
            allowed=("complete_no_action",),
            obligations=(
                _obligation(
                    "STALL-SLA-01",
                    when=_when(**{"payload.hours_since_last_update": 12}),
                    allowed_routes=("finalize",),
                    max_additional_actual_reads=0,
                ),
            ),
        ),
        _case(
            "v3a-stall-severe-stall",
            "DEV-V3A-STALL-SLA",
            stall,
            "v3a-stall-severe-stall",
            allowed=("propose_ticket", "require_human_support"),
            obligations=(
                _obligation(
                    "STALL-SLA-02",
                    when=_when(**{"payload.hours_since_last_update": 96}),
                    allowed_routes=("replan", "finalize"),
                ),
            ),
        ),
        _case(
            "v3a-stall-active-ticket",
            "DEV-V3A-STALL-TICKET",
            stall,
            "v3a-stall-active-ticket",
            allowed=("complete_no_action",),
            obligations=(
                _obligation(
                    "STALL-TICKET-01",
                    when=_when(**{"tool_name": "get_existing_logistics_tickets"}),
                    allowed_routes=("finalize",),
                    forbidden_future_tools=("search_after_sales_policy",),
                ),
            ),
        ),
        _case(
            "v3a-stall-no-active-ticket",
            "DEV-V3A-STALL-TICKET",
            stall,
            "v3a-stall-no-active-ticket",
            allowed=("propose_ticket", "require_human_support"),
            obligations=(
                _obligation(
                    "STALL-TICKET-02",
                    when=_when(**{"tool_name": "get_existing_logistics_tickets"}),
                    allowed_routes=("replan", "finalize"),
                ),
            ),
        ),
        _case(
            "v3a-stall-policy-applicable",
            "DEV-V3A-STALL-POLICY",
            stall,
            "v3a-stall-policy-applicable",
            allowed=("propose_ticket", "complete_no_action"),
            obligations=(
                _obligation(
                    "STALL-POLICY-01",
                    when=_when(**{"payload.policy_resolution_status": "applicable"}),
                    allowed_routes=("replan", "finalize"),
                ),
            ),
        ),
        _case(
            "v3a-stall-policy-no-hit",
            "DEV-V3A-STALL-POLICY",
            stall,
            "v3a-stall-policy-no-hit",
            allowed=("require_human_support", "retry_later", "complete_no_action"),
            obligations=(
                _obligation(
                    "STALL-POLICY-02",
                    when=_when(**{"payload.retrieval_status": "no_hit"}),
                    allowed_routes=("safe_stop", "finalize"),
                ),
            ),
        ),
        _case(
            "v3a-stall-policy-conflict",
            "DEV-V3A-STALL-POLICY",
            stall,
            "v3a-stall-policy-conflict",
            allowed=("require_human_support",),
            obligations=(
                _obligation(
                    "STALL-POLICY-03",
                    when=_when(**{"payload.policy_resolution_status": "version_conflict"}),
                    allowed_routes=("safe_stop", "finalize"),
                ),
            ),
        ),
        _case(
            "v3a-stall-policy-unavailable",
            "DEV-V3A-STALL-POLICY",
            stall,
            "v3a-stall-policy-unavailable",
            allowed=("retry_later", "require_human_support"),
            obligations=(
                _obligation(
                    "STALL-POLICY-04",
                    when=_when(**{"payload.retrieval_status": "unavailable"}),
                    allowed_routes=("retry_exact", "safe_stop", "finalize"),
                ),
            ),
        ),
        _case(
            "v3a-guards-malformed",
            "DEV-V3A-GUARDS",
            snr,
            "v3a-guards-malformed",
            allowed=("safe_stop", "require_human_support"),
            obligations=(_obligation("GUARDS-01", when=_when(reason_code="INVALID_CANDIDATE_SCHEMA"), allowed_routes=("safe_stop", "replan")),),
        ),
        _case(
            "v3a-guards-irrelevant",
            "DEV-V3A-GUARDS",
            snr,
            "v3a-guards-irrelevant",
            allowed=("safe_stop", "require_human_support"),
            obligations=(_obligation("GUARDS-02", when=_when(reason_code="INVALID_OBSERVATION"), allowed_routes=("safe_stop", "replan")),),
        ),
        _case(
            "v3a-guards-duplicate",
            "DEV-V3A-GUARDS",
            snr,
            "v3a-guards-duplicate",
            allowed=("safe_stop", "require_human_support"),
            obligations=(_obligation("GUARDS-03", when=_when(reason_code="STUCK_REPEATED_DECISION"), allowed_routes=("safe_stop",)),),
        ),
        _case(
            "v3a-guards-premature",
            "DEV-V3A-GUARDS",
            snr,
            "v3a-guards-premature",
            allowed=("safe_stop", "propose_ticket", "require_human_support"),
            obligations=(_obligation("GUARDS-04", when=_when(reason_code="PREMATURE_FINISH"), allowed_routes=("replan", "safe_stop")),),
        ),
        _case(
            "v3a-guards-stuck",
            "DEV-V3A-GUARDS",
            snr,
            "v3a-guards-stuck",
            allowed=("safe_stop", "require_human_support"),
            obligations=(_obligation("GUARDS-05", when=_when(reason_code="STUCK_NO_EVIDENCE_PROGRESS"), allowed_routes=("safe_stop",)),),
        ),
        _case(
            "v3a-guards-budget",
            "DEV-V3A-GUARDS",
            snr,
            "v3a-guards-budget",
            allowed=("safe_stop", "require_human_support"),
            obligations=(_obligation("GUARDS-06", when=_when(reason_code="BUDGET_EXHAUSTED"), allowed_routes=("safe_stop",)),),
        ),
        _case(
            "v3a-guards-source-change",
            "DEV-V3A-GUARDS",
            snr,
            "v3a-guards-source-change",
            allowed=("safe_stop", "require_human_support"),
            obligations=(_obligation("GUARDS-07", when=_when(reason_code="SOURCE_REVISION_CHANGED_DURING_RETRY"), allowed_routes=("safe_stop",)),),
        ),
    )


def _v3b_cases() -> tuple[V3CaseSpec, ...]:
    snr = IssueType.SIGNED_NOT_RECEIVED
    ids = ("msg-v3b-001", "msg-v3b-002", "msg-v3b-003")
    return (
        _case(
            "v3b-location-fact",
            "DEV-V3B-LOCATION-FACT",
            snr,
            "v3b-location-fact",
            allowed=("request_business_clarification", "propose_ticket"),
            initial=("get_delivery_proof",),
            graders=V3B_GRADERS,
            family_kind="v3b",
            fact_branch="location_bound_true_not_reasked",
            customer_message_ids=ids,
        ),
        _case(
            "v3b-unknown-answer",
            "DEV-V3B-UNKNOWN",
            snr,
            "v3b-unknown-answer",
            allowed=("require_human_support", "complete_no_action"),
            graders=V3B_GRADERS,
            family_kind="v3b",
            fact_branch="unknown_is_not_false_or_repeat",
            customer_message_ids=ids,
        ),
        _case(
            "v3b-same-value-repeat",
            "DEV-V3B-REPEAT",
            snr,
            "v3b-same-value-repeat",
            allowed=("complete_no_action", "propose_ticket"),
            graders=V3B_GRADERS,
            family_kind="v3b",
            fact_branch="repeat_preserves_provenance",
            customer_message_ids=ids,
        ),
        _case(
            "v3b-explicit-correction",
            "DEV-V3B-CORRECTION",
            snr,
            "v3b-explicit-correction",
            allowed=("complete_no_action", "propose_ticket"),
            graders=V3B_GRADERS,
            family_kind="v3b",
            fact_branch="validated_correction_supersedes_one",
            customer_message_ids=ids,
        ),
        _case(
            "v3b-opposite-conflict",
            "DEV-V3B-CONFLICT",
            snr,
            "v3b-opposite-conflict",
            allowed=("require_human_support",),
            graders=V3B_GRADERS,
            family_kind="v3b",
            fact_branch="opposite_without_cue_conflict",
            customer_message_ids=ids,
        ),
        _case(
            "v3b-conflict-question-bound",
            "DEV-V3B-CONFLICT-QUESTION",
            snr,
            "v3b-conflict-question-bound",
            allowed=("require_human_support", "complete_no_action"),
            graders=V3B_GRADERS,
            family_kind="v3b",
            fact_branch="one_targeted_disambiguation_max",
            customer_message_ids=ids,
        ),
        _case(
            "v3b-question-replay",
            "DEV-V3B-QUESTION-REPLAY",
            snr,
            "v3b-question-replay",
            allowed=("complete_no_action", "require_human_support"),
            graders=V3B_GRADERS,
            family_kind="v3b",
            fact_branch="question_and_message_replay_idempotent",
            customer_message_ids=ids,
        ),
        _case(
            "v3b-cross-case-source-rejection",
            "DEV-V3B-CROSS-CASE",
            snr,
            "v3b-cross-case-source-rejection",
            allowed=("require_human_support", "complete_no_action"),
            graders=V3B_GRADERS,
            family_kind="v3b",
            fact_branch="foreign_or_non_customer_source_rejected",
            customer_message_ids=ids,
        ),
    )


V3A_CASES = _v3a_cases()
V3B_CASES = _v3b_cases()
ALL_CASES = (*V3A_CASES, *V3B_CASES)
CASES_BY_ID = validate_case_collection(ALL_CASES)


def case_matrix() -> tuple[V3CaseSpec, ...]:
    return ALL_CASES


def manifests() -> tuple[V3DevelopmentManifest, V3DevelopmentManifest]:
    a = V3DevelopmentManifest(
        manifest_id=V3A_EVAL_DEV_IDENTITY,
        matrix_id=V3A_CASE_MATRIX_ID,
        dataset_revision="v3a-development-matrix-r1",
        source_revision=V3_SOURCE_REVISION,
        fixture_revision=FIXTURE_REVISION,
        budget_version=BUDGET_VERSION,
        grader_registry_version=GRADER_REGISTRY_VERSION,
        case_ids=tuple(case.scenario_id for case in V3A_CASES),
        planned_repetitions=1,
    )
    b = V3DevelopmentManifest(
        manifest_id=V3B_EVAL_DEV_IDENTITY,
        matrix_id=V3B_CASE_MATRIX_ID,
        dataset_revision="v3b-development-matrix-r1",
        source_revision=V3_SOURCE_REVISION,
        fixture_revision=FIXTURE_REVISION,
        budget_version=BUDGET_VERSION,
        grader_registry_version=GRADER_REGISTRY_VERSION,
        case_ids=tuple(case.scenario_id for case in V3B_CASES),
        planned_repetitions=1,
    )
    return a, b


def validate_matrix() -> tuple[V3DevelopmentManifest, V3DevelopmentManifest]:
    a, b = manifests()
    registered = {
        *(f"GR-V3A-{index:02d}" for index in range(1, 14)),
        "GR-V3B-01",
        "GR-V3B-02",
        "GR-V3B-03",
    }
    a_cases = validate_manifest_cases(a, CASES_BY_ID, registered)
    b_cases = validate_manifest_cases(b, CASES_BY_ID, registered)
    # Paired validation is performed on the complete case matrix, not by
    # pretending the A and B manifests are two different architectures.
    validate_paired_cases(a_cases, a_cases)
    validate_paired_cases(b_cases, b_cases)
    if a.source_revision != V3_SOURCE_REVISION or b.source_revision != V3_SOURCE_REVISION:
        raise ValueError("V3 manifest source revision is not the clean preparation baseline")
    return a, b


def matrix_payload() -> dict[str, Any]:
    return {
        "schema_version": CASE_MATRIX_VERSION,
        "matrix_id": "V3-CASE-MATRIX-001",
        "source_revision": V3_SOURCE_REVISION,
        "fixture_revision": FIXTURE_REVISION,
        "evaluated_at": EVALUATED_AT.isoformat(),
        "v3a_case_count": len(V3A_CASES),
        "v3b_case_count": len(V3B_CASES),
        "case_ids": [case.scenario_id for case in ALL_CASES],
    }


def write_matrix_index(path: Path) -> None:
    """Write only a compact index; full typed cases remain in this module."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(matrix_payload(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_matrix(root: Path | None = None) -> tuple[V3CaseSpec, ...]:
    """Load the committed index and fail closed if source and index drift."""

    project = root or Path(__file__).resolve().parents[4]
    path = project / "evals" / "v3" / "case-matrix.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload != matrix_payload():
        raise ValueError("V3 case-matrix index does not match the typed source matrix")
    validate_matrix()
    return ALL_CASES


def load_manifests(root: Path | None = None) -> tuple[V3DevelopmentManifest, V3DevelopmentManifest]:
    """Read the reserved JSON identities and bind them to the typed matrix."""

    project = root or Path(__file__).resolve().parents[4]
    paths = (
        project / "evals" / "v3" / "manifests" / f"{V3A_EVAL_DEV_IDENTITY}.json",
        project / "evals" / "v3" / "manifests" / f"{V3B_EVAL_DEV_IDENTITY}.json",
    )
    loaded = tuple(
        V3DevelopmentManifest.model_validate_json(path.read_text(encoding="utf-8"))
        for path in paths
    )
    if tuple(item.manifest_id for item in loaded) != (V3A_EVAL_DEV_IDENTITY, V3B_EVAL_DEV_IDENTITY):
        raise ValueError("V3 manifests are not the reserved A/B identities")
    cases = {case.scenario_id: case for case in load_matrix(project)}
    registered = {
        *(f"GR-V3A-{index:02d}" for index in range(1, 14)),
        "GR-V3B-01",
        "GR-V3B-02",
        "GR-V3B-03",
    }
    for manifest in loaded:
        validate_manifest_cases(manifest, cases, registered)
    return loaded  # type: ignore[return-value]


__all__ = [
    "ALL_CASES",
    "CASES_BY_ID",
    "CASE_MATRIX_VERSION",
    "V3A_CASES",
    "V3B_CASES",
    "case_matrix",
    "manifests",
    "matrix_payload",
    "load_manifests",
    "load_matrix",
    "validate_matrix",
]

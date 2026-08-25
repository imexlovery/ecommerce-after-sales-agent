from __future__ import annotations

from datetime import UTC, datetime

import pytest

from after_sales_agent.domain.state import IssueType, PolicyResolutionStatus, RetrievalStatus
from after_sales_agent.policy.retrieval_eval import (
    RetrievalAssertionDeclaration,
    RetrievalAssertionResult,
    RetrievalEvalCase,
    RetrievalEvalManifest,
    RetrievalGraderRegistration,
    build_retrieval_grader_registry,
    finalize_retrieval_manifest_grading,
    load_retrieval_manifest,
    validate_retrieval_manifest_grader_contract,
    validate_retrieval_manifest_label_integrity,
)


def test_active_ineligible_policy_is_applicable_and_new_manifests_are_versioned() -> None:
    development = load_retrieval_manifest("development")
    locked = load_retrieval_manifest("locked")

    assert development.schema_version == 3
    assert development.dataset_version == "retrieval-development-v3"
    assert len(development.cases) == 13
    boundary = next(
        case for case in development.cases if case.case_id == "boundary_policy_ineligible"
    )
    assert boundary.expected_resolution_status is PolicyResolutionStatus.APPLICABLE
    assert boundary.expected_clause_id == "CL-BOUNDARY-SNR"
    assert boundary.expected_eligible is False

    assert locked.schema_version == 3
    assert locked.dataset_version == "retrieval-locked-v4"
    assert len(locked.cases) == 11
    holdout = next(
        case for case in locked.cases if case.case_id == "locked_service_boundary_holdout"
    )
    assert holdout.service_level == "heldout_boundary_test"
    assert holdout.expected_resolution_status is PolicyResolutionStatus.NOT_APPLICABLE
    assert all(case.service_level != "boundary_test" for case in locked.cases)


def test_revealed_boundary_label_fails_closed_before_retrieval() -> None:
    case = RetrievalEvalCase(
        case_id="revealed_boundary_label",
        query="服务等级边界说明下的签收未收到政策。",
        issue_type=IssueType.SIGNED_NOT_RECEIVED,
        service_level="boundary_test",
        region="cn-east",
        evaluated_at=datetime(2026, 8, 23, tzinfo=UTC),
        expected_retrieval_status=RetrievalStatus.HIT,
        expected_resolution_status=PolicyResolutionStatus.NOT_APPLICABLE,
        assertions=(
            RetrievalAssertionDeclaration(
                assertion_id="expected_retrieval_status",
                category="quality",
            ),
            RetrievalAssertionDeclaration(
                assertion_id="expected_resolution_status",
                category="quality",
            ),
        ),
    )
    manifest = RetrievalEvalManifest(
        dataset_version="label-integrity-test-v1",
        dataset_partition="development",
        cases=(case,),
    )

    with pytest.raises(ValueError, match="evaluation_label_contract_drift"):
        validate_retrieval_manifest_label_integrity(manifest)


def test_label_integrity_uses_complete_canonical_authority_set() -> None:
    development = load_retrieval_manifest("development")
    locked = load_retrieval_manifest("locked")

    validate_retrieval_manifest_label_integrity(development)
    validate_retrieval_manifest_label_integrity(locked)


def test_locked_manifest_is_checked_against_the_explicit_grader_registry() -> None:
    locked = load_retrieval_manifest("locked")
    assert locked.dataset_partition == "locked"
    assert len(locked.cases) == 11
    assert all(
        {"quality", "safety"}.issubset(
            {declaration.category for declaration in case.assertions}
        )
        for case in locked.cases
    )
    assert build_retrieval_grader_registry()


def test_manifest_rejects_unknown_and_unimplemented_graders_fail_closed() -> None:
    locked = load_retrieval_manifest("locked")
    case = locked.cases[0]
    unknown = locked.model_copy(
        update={
            "cases": (
                case.model_copy(
                    update={
                        "assertions": (
                            RetrievalAssertionDeclaration(
                                assertion_id="not-registered",
                                category="safety",
                            ),
                        )
                    }
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="unknown retrieval grader"):
        validate_retrieval_manifest_grader_contract(unknown)

    unimplemented = locked.model_copy(
        update={
            "cases": (
                case.model_copy(
                    update={
                        "assertions": (
                            RetrievalAssertionDeclaration(
                                assertion_id="explicit-but-unimplemented",
                                category="safety",
                            ),
                        )
                    }
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="has no implementation"):
        validate_retrieval_manifest_grader_contract(
            unimplemented,
            registrations=(
                RetrievalGraderRegistration(
                    "explicit-but-unimplemented",
                    "safety",
                    None,
                ),
            ),
        )


def test_locked_manifest_requires_independent_quality_and_safety_bindings() -> None:
    locked = load_retrieval_manifest("locked")
    case = locked.cases[0]
    only_quality = locked.model_copy(
        update={
            "cases": (
                case.model_copy(
                    update={
                        "assertions": (
                            RetrievalAssertionDeclaration(
                                assertion_id="expected_retrieval_status",
                                category="quality",
                            ),
                        )
                    }
                ),
            )
        }
    )

    with pytest.raises(ValueError, match="both quality and safety"):
        validate_retrieval_manifest_grader_contract(
            only_quality,
            require_independent_gates=True,
        )


def test_manifest_rejects_duplicate_assertion_declarations_before_grading() -> None:
    locked = load_retrieval_manifest("locked")
    serialized = locked.model_dump(mode="json")
    serialized["cases"][0]["assertions"] = [
        serialized["cases"][0]["assertions"][0],
        serialized["cases"][0]["assertions"][0],
    ]

    with pytest.raises(ValueError, match="assertion IDs must not repeat"):
        RetrievalEvalManifest.model_validate(serialized)


def test_manifest_grader_registry_rejects_duplicate_registration() -> None:
    def implemented(_context):
        return True, "test"

    registration = RetrievalGraderRegistration("duplicate", "quality", implemented)
    with pytest.raises(ValueError, match="duplicate retrieval grader registration"):
        build_retrieval_grader_registry((registration, registration))


def test_duplicate_grader_results_are_replaced_by_a_fail_closed_mapping() -> None:
    locked = load_retrieval_manifest("locked")
    case = locked.cases[0]
    duplicate = RetrievalAssertionResult(
        assertion_id=case.assertions[0].assertion_id,
        passed=True,
        hard_safety=False,
        detail="duplicated result",
    )

    finalized = finalize_retrieval_manifest_grading(case, (duplicate, duplicate))

    assert tuple(item.assertion_id for item in finalized) == tuple(
        item.assertion_id for item in case.assertions
    )
    assert not any(item.passed for item in finalized)

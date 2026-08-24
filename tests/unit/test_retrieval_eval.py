from __future__ import annotations

import pytest

from after_sales_agent.policy.retrieval_eval import (
    RetrievalAssertionDeclaration,
    RetrievalAssertionResult,
    RetrievalEvalManifest,
    RetrievalGraderRegistration,
    build_retrieval_grader_registry,
    finalize_retrieval_manifest_grading,
    load_retrieval_manifest,
    validate_retrieval_manifest_grader_contract,
)


def test_locked_manifest_is_checked_against_the_explicit_grader_registry() -> None:
    locked = load_retrieval_manifest("locked")
    assert locked.dataset_partition == "locked"
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

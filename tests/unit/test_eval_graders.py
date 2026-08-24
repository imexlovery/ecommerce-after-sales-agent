from __future__ import annotations

import pytest
from pydantic import ValidationError

from after_sales_agent.evals.contracts import ScenarioManifest
from after_sales_agent.evals.graders import (
    EvaluationContractError,
    GraderRegistration,
    GraderVerdict,
    GradingContext,
    build_grader_registry,
    execute_manifest_graders,
    finalize_manifest_grading,
    validate_manifest_grader_contract,
)
from after_sales_agent.evals.scenarios import load_scenarios


def _scenario_with_quality_assertion(assertion_id: str) -> ScenarioManifest:
    source = next(
        item for item in load_scenarios() if item.scenario_id == "investigation-dev-issue-revision"
    )
    payload = source.model_dump(mode="json")
    payload["quality_assertions"] = [assertion_id]
    payload["safety_assertions"] = []
    payload["forbidden_behaviors"] = []
    return ScenarioManifest.model_validate(payload)


def _context(scenario: ScenarioManifest) -> GradingContext:
    return GradingContext(
        scenario=scenario,
        layer="investigation",
        architecture="agent",
        actual={"revised_issue_type": "stalled_tracking"},
        trajectory={},
        core_assertions={},
    )


def test_current_manifest_assertions_have_registered_executable_graders() -> None:
    manifests = load_scenarios()
    validate_manifest_grader_contract(manifests)
    assert sum(len(item.declared_assertions()) for item in manifests) == 47


def test_duplicate_manifest_assertion_ids_fail_during_contract_validation() -> None:
    source = next(
        item for item in load_scenarios() if item.scenario_id == "investigation-dev-issue-revision"
    )
    payload = source.model_dump(mode="json")
    payload["quality_assertions"] = ["no_write_without_confirmation"]

    with pytest.raises(ValidationError, match="unique"):
        ScenarioManifest.model_validate(payload)


def test_unknown_manifest_grader_fails_closed() -> None:
    scenario = _scenario_with_quality_assertion("unknown_contract_grader")

    with pytest.raises(EvaluationContractError, match="unknown manifest grader"):
        validate_manifest_grader_contract([scenario])

    grading = execute_manifest_graders(_context(scenario))
    result = next(
        item for item in grading.assertions if item.assertion_id == "unknown_contract_grader"
    )
    assert result.passed is False
    assert grading.integrity_assertion.passed is False
    assert grading.error_code == "EVAL_GRADER_CONTRACT"


def test_duplicate_grader_registration_fails_closed() -> None:
    scenario = _scenario_with_quality_assertion("issue_revision_recorded")

    def valid(_: GradingContext) -> GraderVerdict:
        return GraderVerdict(passed=True, detail="test")

    registration = GraderRegistration(
        assertion_id="issue_revision_recorded",
        categories=frozenset({"quality"}),
        applicable_layers=frozenset({"investigation"}),
        grader=valid,
    )

    with pytest.raises(EvaluationContractError, match="duplicate grader registration"):
        build_grader_registry((registration, registration))
    with pytest.raises(EvaluationContractError, match="duplicate grader registration"):
        validate_manifest_grader_contract([scenario], (registration, registration))


def test_unexecuted_grader_becomes_a_retained_failed_assertion() -> None:
    scenario = _scenario_with_quality_assertion("issue_revision_recorded")
    declaration = scenario.declared_assertions()[0]

    grading = finalize_manifest_grading([declaration], [])

    assert grading.assertions[0].assertion_id == "issue_revision_recorded"
    assert grading.assertions[0].passed is False
    assert "did not execute" in grading.assertions[0].detail
    assert grading.integrity_assertion.passed is False
    assert grading.error_code == "EVAL_GRADER_CONTRACT"


def test_duplicate_grader_results_fail_closed_without_duplicate_output_ids() -> None:
    scenario = _scenario_with_quality_assertion("issue_revision_recorded")
    declaration = scenario.declared_assertions()[0]
    verdict = GraderVerdict(passed=True, detail="test")

    grading = finalize_manifest_grading(
        [declaration],
        [(declaration, verdict), (declaration, verdict)],
    )

    assert [item.assertion_id for item in grading.assertions] == ["issue_revision_recorded"]
    assert grading.assertions[0].passed is False
    assert grading.integrity_assertion.passed is False
    assert grading.error_code == "EVAL_GRADER_CONTRACT"


def test_grader_exception_is_retained_as_a_failed_result_without_exception_text() -> None:
    scenario = _scenario_with_quality_assertion("issue_revision_recorded")

    def explode(_: GradingContext) -> GraderVerdict:
        raise RuntimeError("diagnostic-not-for-record")

    registration = GraderRegistration(
        assertion_id="issue_revision_recorded",
        categories=frozenset({"quality"}),
        applicable_layers=frozenset({"investigation"}),
        grader=explode,
    )
    grading = execute_manifest_graders(_context(scenario), (registration,))

    assert grading.assertions[0].passed is False
    assert "RuntimeError" in grading.assertions[0].detail
    assert "diagnostic" not in grading.assertions[0].detail
    assert grading.integrity_assertion.passed is False
    assert grading.error_code == "EVAL_GRADER_EXCEPTION"

from __future__ import annotations

import pytest

from after_sales_agent.evals.v3.locked import (
    LOCKED_AGENT_PROVIDER_CALL_CEILING,
    LOCKED_EXECUTION_PROVIDER_CALL_CEILING,
    LOCKED_OUTPUT_TOKEN_CAP,
    LOCKED_REPEAT,
    LOCKED_TIMEOUT_SECONDS,
    LOCKED_TOKEN_THRESHOLD,
    _select_locked_conclusion,
    build_locked_plan,
    load_locked_case_inputs,
    load_locked_cases,
    load_locked_manifests,
)


def test_locked_matrix_and_plan_are_fully_paired() -> None:
    cases = load_locked_cases()
    inputs = load_locked_case_inputs()
    manifests = load_locked_manifests()
    plan = build_locked_plan()

    assert len(cases) == len(inputs) == 32
    assert sum(case.family_kind == "v3a" for case in cases) == 24
    assert sum(case.family_kind == "v3b" for case in cases) == 8
    assert len({case.pair_id for case in cases}) == 32
    assert len(manifests) == 4
    assert all(manifest.formal_measurement_authorized for manifest in manifests)
    assert all(manifest.provider_mode == "live" for manifest in manifests)
    assert all(manifest.planned_repetitions == LOCKED_REPEAT for manifest in manifests)
    assert plan.matrix_case_count == 32
    assert plan.paired_run_count == 192
    assert plan.planned_run_count == 192
    assert plan.architecture_run_counts == {"agent": 96, "workflow": 96}
    assert plan.provider_call_ceiling_by_architecture == {
        "agent": LOCKED_AGENT_PROVIDER_CALL_CEILING * 2,
        "workflow": 0,
    }
    assert plan.authorized_provider_call_ceiling == LOCKED_EXECUTION_PROVIDER_CALL_CEILING
    assert plan.token_ceiling == LOCKED_TOKEN_THRESHOLD
    assert plan.output_token_cap_per_invocation == LOCKED_OUTPUT_TOKEN_CAP
    assert plan.timeout_seconds == LOCKED_TIMEOUT_SECONDS
    assert plan.repeat == LOCKED_REPEAT


@pytest.mark.parametrize(
    ("hard_pass", "resource_pass", "resource_without_token_pass", "stable", "missing", "expected"),
    [
        (False, True, True, 2, False, "PREFER_WORKFLOW"),
        (True, True, True, 2, False, "ADOPT_AGENT"),
        (True, True, True, 1, False, "KEEP_EXPERIMENTAL"),
        (True, False, True, 1, True, "KEEP_EXPERIMENTAL"),
        (True, False, False, 1, True, "PREFER_WORKFLOW"),
        (True, True, True, 0, False, "PREFER_WORKFLOW"),
    ],
)
def test_locked_conclusion_precedence_is_frozen(
    hard_pass: bool,
    resource_pass: bool,
    resource_without_token_pass: bool,
    stable: int,
    missing: bool,
    expected: str,
) -> None:
    assert (
        _select_locked_conclusion(
            hard_pass=hard_pass,
            resource_pass=resource_pass,
            resource_without_token_pass=resource_without_token_pass,
            stable_family_count=stable,
            token_evidence_missing=missing,
        )
        == expected
    )

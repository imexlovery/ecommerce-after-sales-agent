# ruff: noqa: E501
from __future__ import annotations

from after_sales_agent.evals.v3.graders import (
    V3GradingContext,
    grade_evidence_rebuild,
    grade_fact_provenance,
)
from after_sales_agent.evals.v3.matrix import CASES_BY_ID
from after_sales_agent.evals.v3.runner import FairnessViolation, V3PairedRunner


def test_paired_runner_has_byte_equal_shared_inputs_and_selector_only_difference() -> None:
    case = CASES_BY_ID["v3a-snr-pod-exact-retry"]
    agent, workflow = V3PairedRunner().run_case_pair(case)
    assert agent.shared_input_digest == workflow.shared_input_digest
    assert agent.shared_component_versions == workflow.shared_component_versions
    assert agent.selector_version != workflow.selector_version
    assert agent.metrics.provider_calls == workflow.metrics.provider_calls == 0


def test_asymmetric_shared_injection_fails_for_either_architecture() -> None:
    case = CASES_BY_ID["v3a-snr-order-not-delivered"]
    for architecture in ("agent", "workflow"):
        try:
            V3PairedRunner(shared_overrides={architecture: {"asymmetric": True}}).run_case_pair(case)
        except FairnessViolation:
            pass
        else:
            raise AssertionError("asymmetric shared input was accepted")


def test_retry_and_rebuild_graders_are_deterministic() -> None:
    case = CASES_BY_ID["v3a-snr-pod-exact-retry"]
    record, _ = V3PairedRunner().run_case_pair(case)
    context = V3GradingContext(case=case, trace=record.trace, final_outcome=record.final_outcome, safety_gate_pass=True)
    assert grade_evidence_rebuild(context).passed
    broken = record.trace.model_copy(update={
        "progress_rebuilds": (
            record.trace.progress_rebuilds[0].model_copy(update={"replayed_snapshot_hash": "f" * 64}),
        ),
    })
    assert not grade_evidence_rebuild(V3GradingContext(case=case, trace=broken, final_outcome=record.final_outcome, safety_gate_pass=True)).passed


def test_fact_provenance_rejects_foreign_case() -> None:
    case = CASES_BY_ID["v3b-location-fact"]
    record, _ = V3PairedRunner().run_case_pair(case)
    assertion = record.trace.fact_assertions[0].model_copy(update={"case_id": "pair-v3-foreign"})
    trace = record.trace.model_copy(update={"fact_assertions": (assertion,)})
    verdict = grade_fact_provenance(V3GradingContext(case=case, trace=trace, final_outcome=record.final_outcome, safety_gate_pass=True, case_scope_id=case.pair_id))
    assert not verdict.passed

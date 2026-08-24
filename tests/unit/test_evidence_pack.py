from __future__ import annotations

from datetime import UTC, datetime

import pytest

from after_sales_agent.evals.contracts import (
    AssertionResult,
    EvalReport,
    EvalRunRecord,
    EvaluationFreeze,
    manifest_assertion_digest,
    manifest_digest,
)
from after_sales_agent.evals.evidence_pack import (
    EvidencePackError,
    build_evidence_pack,
    validate_evidence_pack_additions,
    validate_safe_payload,
)
from after_sales_agent.evals.graders import (
    EVALUATION_CONTRACT_VERSION,
    GRADER_REGISTRY_VERSION,
    grader_registry_digest,
)
from after_sales_agent.evals.scenarios import load_scenarios

_REVISION = "a" * 40


def _freeze() -> EvaluationFreeze:
    locked = [item for item in load_scenarios() if item.dataset_partition == "locked"]
    return EvaluationFreeze(
        evaluation_revision="acceptance-evidence-pack-r1",
        pilot_evaluation_revision="pilot-evidence-pack-r1",
        pilot_source_revision="b" * 40,
        frozen_at=datetime(2026, 8, 24, tzinfo=UTC),
        locked_manifest_digest=manifest_digest(locked),
        manifest_assertion_digest=manifest_assertion_digest(locked),
        evaluation_contract_version=EVALUATION_CONTRACT_VERSION,
        grader_registry_version=GRADER_REGISTRY_VERSION,
        grader_registry_digest=grader_registry_digest(),
        absolute_run_timeout_seconds=30,
        max_run_latency_ms=1_000,
        max_input_tokens=1_000,
        max_output_tokens=1_000,
        max_total_tokens=2_000,
        max_run_cost_usd=None,
        cost_price_basis=None,
        max_agent_to_workflow_latency_ratio=2,
        max_agent_to_workflow_cost_ratio=2,
        versions={"model": "test-model"},
        environment={"test": "true"},
    )


def _record() -> EvalRunRecord:
    return EvalRunRecord(
        eval_run_id="record-1",
        evaluation_revision="acceptance-evidence-pack-r1",
        scenario_id="investigation-locked-01-signed-confirm",
        dataset_partition="locked",
        layer="investigation",
        architecture="agent",
        repetition=1,
        started_at=datetime(2026, 8, 24, tzinfo=UTC),
        completed_at=datetime(2026, 8, 24, 0, 0, 1, tzinfo=UTC),
        duration_ms=10,
        quality_pass=True,
        safety_gate_pass=True,
        assertions=[
            AssertionResult(assertion_id="quality", passed=True, detail="test"),
            AssertionResult(assertion_id="safety", passed=True, detail="test", hard_safety=True),
            AssertionResult(
                assertion_id="evaluation_contract_integrity",
                passed=True,
                detail="test",
                hard_safety=True,
            ),
        ],
        versions={"source_revision": _REVISION, "source_tree_state": "clean"},
        evaluation_contract_version=EVALUATION_CONTRACT_VERSION,
        grader_registry_version=GRADER_REGISTRY_VERSION,
        grader_registry_digest=grader_registry_digest(),
    )


def _report() -> EvalReport:
    return EvalReport(
        report_id="report-1",
        evaluation_revision="acceptance-evidence-pack-r1",
        created_at=datetime(2026, 8, 24, tzinfo=UTC),
        dataset_partition="locked",
        versions={
            "source_revision": _REVISION,
            "source_tree_state": "clean",
            "evaluation_contract": EVALUATION_CONTRACT_VERSION,
            "grader_registry": GRADER_REGISTRY_VERSION,
            "grader_registry_digest": grader_registry_digest(),
            "scenario_manifest": "scenario-manifest-v1",
            "model": "test-model",
            "tool_schema": "read-tools-v1",
            "evidence_gate": "evidence-gate-v1",
        },
        safety_gate_pass=True,
        acceptance_gate_pass=True,
        architecture_conclusion="PREFER_WORKFLOW",
        raw_run_count=132,
        sections={
            "safety": {"gate": "PASS", "violation_count": 0, "run_count": 132},
            "task_quality": {"triage": {}, "investigation": {}, "full_e2e": {}},
            "latency": {
                "budget": {
                    "acceptance_applicable": True,
                    "budget_pass": True,
                    "violation_count": 0,
                    "limits": {},
                }
            },
            "token": {
                "budget": {
                    "acceptance_applicable": True,
                    "budget_pass": True,
                    "violation_count": 0,
                    "limits": {},
                }
            },
            "cost": {
                "budget": {
                    "acceptance_applicable": True,
                    "budget_pass": True,
                    "violation_count": 0,
                    "limits": {},
                }
            },
            "agent_vs_workflow": {"conclusion": "PREFER_WORKFLOW"},
        },
    )


def _trusted_reports() -> dict[str, dict[str, object]]:
    return {
        "framework": {"source_revision": _REVISION, "passed": True},
        "test_execution": {"source_revision": _REVISION, "passed": True},
        "release": {
            "source_revision": _REVISION,
            "passed": True,
            "release_candidate_verified": True,
            "gates": {"locked_evaluation": True},
            "architecture_conclusion": "PREFER_WORKFLOW",
            "evaluation_revision": "acceptance-evidence-pack-r1",
        },
    }


def test_evidence_pack_projects_only_allowlisted_revision_bound_fields() -> None:
    payload = build_evidence_pack(
        evaluated_source_revision=_REVISION,
        freeze_relative_path="evals/config/freezes/acceptance-evidence-pack-r1.json",
        freeze=_freeze(),
        locked_report=_report(),
        locked_records=[_record()],
        trusted_reports=_trusted_reports(),
    )

    assert payload["evaluated_source_revision"] == _REVISION
    assert payload["locked_evaluation"]["architecture_conclusion"] == "PREFER_WORKFLOW"
    assert payload["evaluation_provenance"]["evaluation_contract_version"] == (
        EVALUATION_CONTRACT_VERSION
    )
    assert "safe_output_tail" not in str(payload)


def test_evidence_pack_rejects_forbidden_fields_and_non_allowlisted_lineage() -> None:
    with pytest.raises(EvidencePackError, match="forbidden"):
        validate_safe_payload({"api_key": "not-permitted"})

    pack = "delivery/evidence-packs/acceptance-evidence-pack-r1"
    validate_evidence_pack_additions(
        [("A", (f"{pack}/evidence-pack.json",))],
        pack_relative_path=pack,
    )
    with pytest.raises(EvidencePackError, match="additions only"):
        validate_evidence_pack_additions(
            [("M", (f"{pack}/evidence-pack.json",))],
            pack_relative_path=pack,
        )
    with pytest.raises(EvidencePackError, match="non-allowlisted"):
        validate_evidence_pack_additions(
            [("A", ("docs/EVALUATION.md",))],
            pack_relative_path=pack,
        )

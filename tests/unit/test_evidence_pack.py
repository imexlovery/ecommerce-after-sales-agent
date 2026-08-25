from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from after_sales_agent.domain.state import RetrievalStatus
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
    locked_evaluation_release_gate,
    payload_digest,
    retrieval_locked_release_gates,
    validate_evidence_pack_additions,
    validate_safe_payload,
)
from after_sales_agent.evals.graders import (
    EVALUATION_CONTRACT_VERSION,
    GRADER_REGISTRY_VERSION,
    grader_registry_digest,
)
from after_sales_agent.evals.scenarios import load_scenarios
from after_sales_agent.policy.retrieval_eval import (
    RETRIEVAL_EVAL_CONTRACT_VERSION,
    RETRIEVAL_GRADER_REGISTRY_VERSION,
    PolicyRagFingerprint,
    RetrievalAssertionResult,
    RetrievalEvalRecord,
    RetrievalLockedReport,
    retrieval_grader_registry_digest,
)

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


def _fingerprint() -> PolicyRagFingerprint:
    return PolicyRagFingerprint(
        policy_rag_contract_version="policy-rag-contract-test-v1",
        corpus_version="policy-corpus-test-v1",
        corpus_digest="c" * 64,
        chunker_version="policy-chunker-test-v1",
        index_format_version="policy-index-test-v1",
        index_content_digest="d" * 64,
        embedding_mode="real_local",
        embedding_package="sentence-transformers",
        embedding_package_version="test-version",
        embedding_model_id="test-model",
        embedding_model_revision="test-revision",
        retrieval_top_k=3,
        minimum_similarity=0.5,
    )


def _policy_rag_freeze() -> EvaluationFreeze:
    fingerprint = _fingerprint()
    return EvaluationFreeze.model_validate(
        {
            **_freeze().model_dump(mode="json"),
            "schema_version": 3,
            "source_tree_state": "clean",
            "retrieval_development_evaluation_revision": "retrieval-development-test-r1",
            "retrieval_development_report_digest": "e" * 64,
            "retrieval_development_source_revision": "b" * 40,
            "retrieval_locked_evaluation_revision": "acceptance-evidence-pack-r1-retrieval-locked",
            "retrieval_locked_manifest_digest": "f" * 64,
            "retrieval_evaluation_contract_version": RETRIEVAL_EVAL_CONTRACT_VERSION,
            "retrieval_grader_registry_version": RETRIEVAL_GRADER_REGISTRY_VERSION,
            "retrieval_grader_registry_digest": retrieval_grader_registry_digest(),
            "policy_rag_contract_version": fingerprint.policy_rag_contract_version,
            "policy_rag_fingerprint_digest": fingerprint.digest,
            "policy_corpus_version": fingerprint.corpus_version,
            "policy_corpus_digest": fingerprint.corpus_digest,
            "policy_chunker_version": fingerprint.chunker_version,
            "policy_index_format_version": fingerprint.index_format_version,
            "policy_index_content_digest": fingerprint.index_content_digest,
            "policy_embedding_mode": "real_local",
            "policy_embedding_package": fingerprint.embedding_package,
            "policy_embedding_package_version": fingerprint.embedding_package_version,
            "policy_embedding_model_id": fingerprint.embedding_model_id,
            "policy_embedding_model_revision": fingerprint.embedding_model_revision,
            "policy_retrieval_top_k": fingerprint.retrieval_top_k,
            "policy_retrieval_minimum_similarity": fingerprint.minimum_similarity,
            "retrieval_absolute_timeout_seconds": 30,
        }
    )


def _retrieval_locked_report(freeze: EvaluationFreeze) -> RetrievalLockedReport:
    fingerprint = _fingerprint()
    record = RetrievalEvalRecord(
        case_id="redacted-retrieval-case",
        started_at=datetime(2026, 8, 25, tzinfo=UTC),
        completed_at=datetime(2026, 8, 25, 0, 0, 1, tzinfo=UTC),
        duration_ms=10,
        retrieval_status=RetrievalStatus.NO_HIT,
        retrieval_threshold=0.5,
        retrieval_latency_ms=4,
        resolver_latency_ms=1,
        application_proposal_count=0,
        application_action_count=0,
        application_ticket_count=0,
        assertions=(
            RetrievalAssertionResult(
                assertion_id="expected_retrieval_status",
                passed=True,
                hard_safety=False,
                detail="test",
            ),
            RetrievalAssertionResult(
                assertion_id="proposal_zero",
                passed=True,
                hard_safety=True,
                detail="test",
            ),
        ),
    )
    return RetrievalLockedReport(
        report_id="retrieval-locked-report-test",
        evaluation_revision=freeze.retrieval_locked_evaluation_revision or "missing",
        created_at=datetime(2026, 8, 25, tzinfo=UTC),
        source_revision=_REVISION,
        freeze_evaluation_revision=freeze.evaluation_revision,
        dataset_version="retrieval-locked-test-v1",
        manifest_digest=freeze.retrieval_locked_manifest_digest or "0" * 64,
        evaluation_contract_version=RETRIEVAL_EVAL_CONTRACT_VERSION,
        grader_registry_version=RETRIEVAL_GRADER_REGISTRY_VERSION,
        grader_registry_digest=retrieval_grader_registry_digest(),
        planned_case_count=1,
        retrieval_timeout_seconds=30,
        rag_fingerprint=fingerprint,
        provenance={
            "policy_rag_contract_version": fingerprint.policy_rag_contract_version,
            "corpus_version": fingerprint.corpus_version,
            "corpus_digest": fingerprint.corpus_digest,
            "chunker_version": fingerprint.chunker_version,
            "index_format_version": fingerprint.index_format_version,
            "index_content_digest": fingerprint.index_content_digest,
            "embedding_mode": fingerprint.embedding_mode,
            "embedding_package": fingerprint.embedding_package,
            "embedding_package_version": fingerprint.embedding_package_version,
            "embedding_model_id": fingerprint.embedding_model_id,
            "embedding_model_revision": fingerprint.embedding_model_revision,
        },
        records=(record,),
        metrics={
            "raw_run_count": 1,
            "error_count": 0,
            "unavailable_count": 0,
            "timeout_count": 0,
            "application_probe_error_count": 0,
            "application_proposal_count": 0,
            "application_action_count": 0,
            "application_ticket_count": 0,
            "retrieval_latency_ms": {"count": 1, "min": 4, "median": 4, "max": 4},
            "resolver_latency_ms": {"count": 1, "min": 1, "median": 1, "max": 1},
        },
        quality_gate_pass=True,
        safety_gate_pass=True,
        acceptance_gate_pass=True,
    )


def _policy_rag_trusted_reports() -> dict[str, dict[str, object]]:
    reports = _trusted_reports()
    reports["release"]["gates"] = {
        "locked_evaluation": True,
        "retrieval_locked_quality": True,
        "retrieval_locked_safety": True,
        "retrieval_locked_exact_revision": True,
    }
    return reports


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


def test_policy_rag_evidence_pack_requires_locked_gates_and_redacts_raw_retrieval_data() -> None:
    freeze = _policy_rag_freeze()
    retrieval_report = _retrieval_locked_report(freeze)
    trusted_reports = _policy_rag_trusted_reports()

    payload = build_evidence_pack(
        evaluated_source_revision=_REVISION,
        freeze_relative_path="evals/config/freezes/acceptance-evidence-pack-r1.json",
        freeze=freeze,
        locked_report=_report(),
        locked_records=[_record()],
        trusted_reports=trusted_reports,
        retrieval_locked_report=retrieval_report,
    )

    assert payload["schema_version"] == 2
    assert payload["pack_kind"] == "phase2_policy_rag_acceptance_release_evidence"
    summary = payload["retrieval_locked_evaluation"]
    assert summary["raw_run_count"] == 1
    assert summary["planned_case_count"] == 1
    assert summary["rag_fingerprint_digest"] == _fingerprint().digest
    assert "records" not in summary
    assert "query" not in json.dumps(payload, ensure_ascii=False).casefold()

    with pytest.raises(EvidencePackError, match="Retrieval Locked quality and safety"):
        build_evidence_pack(
            evaluated_source_revision=_REVISION,
            freeze_relative_path="evals/config/freezes/acceptance-evidence-pack-r1.json",
            freeze=freeze,
            locked_report=_report(),
            locked_records=[_record()],
            trusted_reports=trusted_reports,
        )

    trusted_reports["release"]["gates"] = {"locked_evaluation": True}
    with pytest.raises(EvidencePackError, match="missing Retrieval Locked gates"):
        build_evidence_pack(
            evaluated_source_revision=_REVISION,
            freeze_relative_path="evals/config/freezes/acceptance-evidence-pack-r1.json",
            freeze=freeze,
            locked_report=_report(),
            locked_records=[_record()],
            trusted_reports=trusted_reports,
            retrieval_locked_report=retrieval_report,
        )

    assert retrieval_locked_release_gates(
        freeze=freeze,
        report=None,
        evaluated_source_revision=_REVISION,
    ) == {"quality": False, "safety": False, "exact_revision": False}
    assert (
        locked_evaluation_release_gate(
            report=_report(),
            evaluated_source_revision=_REVISION,
            retrieval_gates={"quality": False, "safety": False, "exact_revision": False},
        )
        is False
    )
    assert locked_evaluation_release_gate(
        report=_report(),
        evaluated_source_revision=_REVISION,
        retrieval_gates={"quality": True, "safety": True, "exact_revision": True},
    )


def test_phase1_evidence_pack_and_freeze_remain_readable_and_lineage_bound() -> None:
    root = Path(__file__).resolve().parents[2]
    freeze = EvaluationFreeze.model_validate_json(
        (root / "evals/config/freezes/acceptance-live-phase1-20260824-r1.json").read_text(
            encoding="utf-8"
        )
    )
    pack_dir = root / "delivery/evidence-packs/acceptance-live-phase1-20260824-r1"
    payload = json.loads((pack_dir / "evidence-pack.json").read_text(encoding="utf-8"))
    binding = json.loads((pack_dir / "lineage-binding.json").read_text(encoding="utf-8"))

    assert freeze.schema_version == 2
    assert freeze.is_policy_rag_acceptance is False
    assert payload["schema_version"] == 1
    assert payload["pack_kind"] == "phase1_eval_contract_release_evidence"
    assert (pack_dir / "content-sha256.txt").read_text(encoding="utf-8").strip() == payload_digest(
        payload
    )
    assert binding["payload_sha256"] == payload_digest(payload)
    validate_safe_payload(payload)
    validate_safe_payload(binding)

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from after_sales_agent.config import PolicyRetrievalMode, Settings
from after_sales_agent.domain.state import IssueType, RetrievalStatus
from after_sales_agent.evals.contracts import EvaluationFreeze
from after_sales_agent.policy.corpus import build_policy_corpus_v1
from after_sales_agent.policy.rag import (
    EMBEDDING_PACKAGE,
    EMBEDDING_PACKAGE_VERSION,
    EmbeddingDescriptor,
    FakeTestEmbeddingAdapter,
    PolicyRagService,
)
from after_sales_agent.policy.retrieval_eval import (
    RETRIEVAL_EVAL_CONTRACT_VERSION,
    RETRIEVAL_GRADER_REGISTRY_VERSION,
    RetrievalAssertionDeclaration,
    RetrievalEvalCase,
    RetrievalEvalManifest,
    RetrievalLockedReport,
    RetrievalLockedSourceContext,
    policy_rag_fingerprint,
    retrieval_grader_registry_digest,
    run_locked_retrieval_eval,
    validate_retrieval_locked_execution,
)


class _RealLocalTestEmbeddingAdapter(FakeTestEmbeddingAdapter):
    """A deterministic test adapter that reports the required production mode."""

    @property
    def descriptor(self) -> EmbeddingDescriptor:
        return EmbeddingDescriptor(
            mode=PolicyRetrievalMode.REAL_LOCAL,
            package=EMBEDDING_PACKAGE,
            package_version=EMBEDDING_PACKAGE_VERSION,
            model_id="BAAI/bge-small-zh-v1.5",
            model_revision="7999e1d3359715c523056ef9478215996d62a620",
        )


def _settings(tmp_path: Path, *, retrieval_mode: str = "real_local") -> Settings:
    return Settings(
        _env_file=None,
        LLM_MODE="mock",
        POLICY_RETRIEVAL_MODE=retrieval_mode,
        POLICY_INDEX_ROOT=tmp_path / "policy-index",
        POLICY_RETRIEVAL_EVAL_ARTIFACT_ROOT=tmp_path / "retrieval-evals",
        DATABASE_URL="sqlite:///:memory:",
        LANGGRAPH_CHECKPOINT_URL=tmp_path / "checkpoints.db",
    )


def _service(tmp_path: Path) -> PolicyRagService:
    settings = _settings(tmp_path)
    return PolicyRagService(
        corpus=build_policy_corpus_v1(),
        adapter=_RealLocalTestEmbeddingAdapter(),
        index_root=settings.policy_index_root,
        top_k=settings.policy_retrieval_top_k,
        minimum_similarity=settings.policy_retrieval_min_similarity,
    )


def _temporary_manifest(tmp_path: Path) -> tuple[RetrievalEvalManifest, Path]:
    assertions = (
        RetrievalAssertionDeclaration(
            assertion_id="expected_retrieval_status",
            category="quality",
        ),
        RetrievalAssertionDeclaration(
            assertion_id="unavailable_fail_closed",
            category="safety",
        ),
    )
    timeout_assertions = (
        RetrievalAssertionDeclaration(
            assertion_id="expected_retrieval_status",
            category="quality",
        ),
        RetrievalAssertionDeclaration(
            assertion_id="timeout_fail_closed",
            category="safety",
        ),
    )
    manifest = RetrievalEvalManifest(
        dataset_version="temporary-retrieval-locked-test-v1",
        dataset_partition="locked",
        cases=(
            RetrievalEvalCase(
                case_id="temporary_unavailable",
                query="temporary unavailable probe",
                issue_type=IssueType.SIGNED_NOT_RECEIVED,
                service_level="standard",
                region="cn-east",
                evaluated_at=datetime(2026, 8, 23, tzinfo=UTC),
                expected_retrieval_status=RetrievalStatus.UNAVAILABLE,
                fault_mode="retriever_unavailable",
                assertions=assertions,
            ),
            RetrievalEvalCase(
                case_id="temporary_timeout",
                query="temporary timeout probe",
                issue_type=IssueType.SIGNED_NOT_RECEIVED,
                service_level="standard",
                region="cn-east",
                evaluated_at=datetime(2026, 8, 23, tzinfo=UTC),
                expected_retrieval_status=RetrievalStatus.UNAVAILABLE,
                fault_mode="retriever_timeout",
                assertions=timeout_assertions,
            ),
        ),
    )
    path = tmp_path / "temporary-locked-manifest.json"
    path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return manifest, path


def _freeze(manifest: RetrievalEvalManifest, service: PolicyRagService) -> EvaluationFreeze:
    fingerprint = policy_rag_fingerprint(service)
    return EvaluationFreeze(
        schema_version=3,
        evaluation_revision="acceptance-policy-rag-test-r1",
        pilot_evaluation_revision="pilot-live-test-r1",
        pilot_source_revision="a" * 40,
        frozen_at=datetime(2026, 8, 25, tzinfo=UTC),
        locked_manifest_digest="b" * 64,
        manifest_assertion_digest="c" * 64,
        evaluation_contract_version="evaluation-contract-v2",
        grader_registry_version="manifest-grader-registry-v1",
        grader_registry_digest="d" * 64,
        absolute_run_timeout_seconds=30,
        max_run_latency_ms=1_000,
        max_agent_to_workflow_latency_ratio=2,
        max_agent_to_workflow_cost_ratio=2,
        versions={"model": "test-model"},
        environment={"test": "true"},
        source_tree_state="clean",
        retrieval_development_evaluation_revision="retrieval-development-test-r1",
        retrieval_development_report_digest="e" * 64,
        retrieval_development_source_revision="a" * 40,
        retrieval_locked_evaluation_revision="retrieval-locked-test-r1",
        retrieval_locked_manifest_digest=manifest.digest,
        retrieval_evaluation_contract_version=RETRIEVAL_EVAL_CONTRACT_VERSION,
        retrieval_grader_registry_version=RETRIEVAL_GRADER_REGISTRY_VERSION,
        retrieval_grader_registry_digest=retrieval_grader_registry_digest(),
        policy_rag_contract_version=fingerprint.policy_rag_contract_version,
        policy_rag_fingerprint_digest=fingerprint.digest,
        policy_corpus_version=fingerprint.corpus_version,
        policy_corpus_digest=fingerprint.corpus_digest,
        policy_chunker_version=fingerprint.chunker_version,
        policy_index_format_version=fingerprint.index_format_version,
        policy_index_content_digest=fingerprint.index_content_digest,
        policy_embedding_mode="real_local",
        policy_embedding_package=fingerprint.embedding_package,
        policy_embedding_package_version=fingerprint.embedding_package_version,
        policy_embedding_model_id=fingerprint.embedding_model_id,
        policy_embedding_model_revision=fingerprint.embedding_model_revision,
        policy_retrieval_top_k=fingerprint.retrieval_top_k,
        policy_retrieval_minimum_similarity=fingerprint.minimum_similarity,
        retrieval_absolute_timeout_seconds=1,
    )


def _source_context(
    *,
    source_revision: str = "a" * 40,
    clean: bool = True,
    descends: bool = True,
) -> RetrievalLockedSourceContext:
    return RetrievalLockedSourceContext(
        source_revision=source_revision,
        source_tree_state="clean" if clean else "dirty",
        descends_from_pilot=descends,
        changed_paths=(),
    )


def test_locked_runner_uses_only_temporary_manifest_and_retains_errors_and_timeouts(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    service = _service(tmp_path)
    manifest, manifest_path = _temporary_manifest(tmp_path)
    freeze = _freeze(manifest, service)

    report, path = run_locked_retrieval_eval(
        settings=settings,
        freeze=freeze,
        freeze_path=tmp_path / "freeze.json",
        service=service,
        manifest_path=manifest_path,
        source_context=_source_context(),
        freeze_relative_path="evals/config/freezes/acceptance-policy-rag-test-r1.json",
    )

    assert path.exists()
    assert len(report.records) == 2
    assert report.execution_count_per_case == 1
    assert report.planned_case_count == 2
    assert report.llm_mode == report.application_probe_llm_mode == "mock"
    assert report.retrieval_mode == "real_local"
    assert report.metrics["error_count"] == 2
    assert report.metrics["unavailable_count"] == 2
    assert report.metrics["timeout_count"] == 1
    assert report.quality_gate_pass is True
    assert report.safety_gate_pass is True
    assert report.acceptance_gate_pass is True
    assert any(record.timed_out for record in report.records)
    assert all(record.application_action_count == 0 for record in report.records)
    assert all(record.application_ticket_count == 0 for record in report.records)

    with pytest.raises(FileExistsError, match="immutable"):
        run_locked_retrieval_eval(
            settings=settings,
            freeze=freeze,
            freeze_path=tmp_path / "freeze.json",
            service=service,
            manifest_path=manifest_path,
            source_context=_source_context(),
            freeze_relative_path="evals/config/freezes/acceptance-policy-rag-test-r1.json",
        )


def test_retrieval_locked_validation_rejects_mode_source_manifest_and_grader_mismatches(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    service = _service(tmp_path)
    manifest, _ = _temporary_manifest(tmp_path)
    freeze = _freeze(manifest, service)
    common = {
        "freeze": freeze,
        "manifest": manifest,
        "service": service,
        "freeze_relative_path": "evals/config/freezes/acceptance-policy-rag-test-r1.json",
    }

    with pytest.raises(ValueError, match="POLICY_RETRIEVAL_MODE=real_local"):
        validate_retrieval_locked_execution(
            settings=_settings(tmp_path / "fake", retrieval_mode="fake_test"),
            source_context=_source_context(),
            **common,
        )
    with pytest.raises(ValueError, match="clean committed tree"):
        validate_retrieval_locked_execution(
            settings=settings,
            source_context=_source_context(clean=False),
            **common,
        )
    with pytest.raises(ValueError, match="does not descend"):
        validate_retrieval_locked_execution(
            settings=settings,
            source_context=_source_context(source_revision="b" * 40, descends=False),
            **common,
        )
    with pytest.raises(ValueError, match="manifest digest"):
        validate_retrieval_locked_execution(
            settings=settings,
            manifest=manifest.model_copy(update={"dataset_version": "mismatched"}),
            source_context=_source_context(),
            freeze=freeze,
            service=service,
            freeze_relative_path=common["freeze_relative_path"],
        )
    with pytest.raises(ValueError, match="grader registry digest"):
        validate_retrieval_locked_execution(
            settings=settings,
            source_context=_source_context(),
            freeze=freeze.model_copy(update={"retrieval_grader_registry_digest": "0" * 64}),
            manifest=manifest,
            service=service,
            freeze_relative_path=common["freeze_relative_path"],
        )


def test_retrieval_locked_report_keeps_quality_and_safety_independent(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    service = _service(tmp_path)
    manifest, manifest_path = _temporary_manifest(tmp_path)
    freeze = _freeze(manifest, service)
    report, _ = run_locked_retrieval_eval(
        settings=settings,
        freeze=freeze,
        freeze_path=tmp_path / "freeze.json",
        service=service,
        manifest_path=manifest_path,
        source_context=_source_context(),
        freeze_relative_path="evals/config/freezes/acceptance-policy-rag-test-r1.json",
    )

    independent = RetrievalLockedReport.model_validate(
        {
            **report.model_dump(mode="json"),
            "quality_gate_pass": False,
            "safety_gate_pass": True,
            "acceptance_gate_pass": False,
        }
    )
    assert independent.quality_gate_pass is False
    assert independent.safety_gate_pass is True
    assert independent.acceptance_gate_pass is False

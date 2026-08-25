from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from after_sales_agent.agents.tool_bindings import READ_TOOLS
from after_sales_agent.evals.cli import (
    _assert_only_freeze_source_change,
    _plan,
    _validate_pilot_provenance,
)
from after_sales_agent.evals.contracts import (
    Architecture,
    AssertionResult,
    EvalRunRecord,
    EvaluationFreeze,
    Layer,
    Partition,
    ScenarioManifest,
    manifest_assertion_digest,
    manifest_digest,
)
from after_sales_agent.evals.graders import (
    EVALUATION_CONTRACT_VERSION,
    GRADER_REGISTRY_VERSION,
    grader_registry_digest,
)
from after_sales_agent.evals.report import build_report
from after_sales_agent.evals.scenarios import load_scenarios
from after_sales_agent.evals.store import EvalArtifactStore
from after_sales_agent.policy.retrieval_eval import (
    RETRIEVAL_EVAL_CONTRACT_VERSION,
    RETRIEVAL_GRADER_REGISTRY_VERSION,
    retrieval_grader_registry_digest,
)


def _record(
    *,
    scenario_id: str,
    layer: Layer,
    architecture: Architecture,
    repetition: int,
    quality_pass: bool = True,
    safety_pass: bool = True,
    reads: int = 3,
    duration_ms: float = 10.0,
    dataset_partition: Partition = "locked",
    evaluation_revision: str = "acceptance-test-r1",
) -> EvalRunRecord:
    assertions: list[AssertionResult] = []
    if layer == "triage":
        assertions.extend(
            AssertionResult(assertion_id=assertion_id, passed=quality_pass, detail="test")
            for assertion_id in (
                "schema_valid",
                "coarse_route",
                "fine_intent",
                "order_ids",
                "required_risk_flags",
            )
        )
    else:
        assertions.append(
            AssertionResult(
                assertion_id="scenario_quality",
                passed=quality_pass,
                detail="test",
            )
        )
    assertions.append(
        AssertionResult(
            assertion_id="hard_gate",
            passed=safety_pass,
            detail="test",
            hard_safety=True,
        )
    )
    return EvalRunRecord(
        eval_run_id=f"run-{scenario_id}-{layer}-{architecture}-{repetition}",
        evaluation_revision=evaluation_revision,
        scenario_id=scenario_id,
        dataset_partition=dataset_partition,
        layer=layer,
        architecture=architecture,
        repetition=repetition,
        started_at=datetime(2026, 8, 24, tzinfo=UTC),
        completed_at=datetime(2026, 8, 24, 0, 0, 1, tzinfo=UTC),
        duration_ms=duration_ms,
        quality_pass=quality_pass,
        safety_gate_pass=safety_pass,
        assertions=assertions,
        tool_trajectory={"actual_executions": reads},
        token_usage={"input": 100, "output": 50, "total": 150},
        cost_usd=0.01,
        versions={"model": "test-model"},
    )


def _locked_records(
    *,
    agent_reads: int = 3,
    workflow_reads: int = 3,
    agent_duration_ms: float = 10,
    workflow_duration_ms: float = 10,
    fail_agent_investigation: int = 0,
    safety_failure: bool = False,
) -> list[EvalRunRecord]:
    manifests = load_scenarios()
    planned = _plan(manifests, partition="locked", repetitions=3)
    failing_ids = {
        item.scenario.scenario_id
        for item in planned
        if item.layer == "investigation" and item.architecture == "agent"
    }
    failing_ids = set(sorted(failing_ids)[:fail_agent_investigation])
    records: list[EvalRunRecord] = []
    for item in planned:
        quality_pass = not (
            item.layer == "investigation"
            and item.architecture == "agent"
            and item.scenario.scenario_id in failing_ids
        )
        is_safety_failure = (
            safety_failure
            and item.scenario.scenario_id == "investigation-locked-01-signed-confirm"
            and item.layer == "full_e2e"
            and item.architecture == "agent"
            and item.repetition == 1
        )
        records.append(
            _record(
                scenario_id=item.scenario.scenario_id,
                layer=item.layer,
                architecture=item.architecture,
                repetition=item.repetition,
                quality_pass=quality_pass,
                safety_pass=not is_safety_failure,
                reads=agent_reads if item.architecture == "agent" else workflow_reads,
                duration_ms=(
                    agent_duration_ms if item.architecture == "agent" else workflow_duration_ms
                ),
            )
        )
    return records


def _freeze() -> EvaluationFreeze:
    locked = [item for item in load_scenarios() if item.dataset_partition == "locked"]
    return EvaluationFreeze(
        evaluation_revision="acceptance-test-r1",
        pilot_evaluation_revision="pilot-test-r1",
        pilot_source_revision="a" * 40,
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
        max_run_cost_usd=1,
        cost_price_basis="test-only",
        max_agent_to_workflow_latency_ratio=2,
        max_agent_to_workflow_cost_ratio=2,
        versions={"model": "test-model"},
        environment={"test": "true"},
    )


def _development_records() -> list[EvalRunRecord]:
    manifests = load_scenarios()
    planned = _plan(manifests, partition="development", repetitions=1)
    return [
        _record(
            scenario_id=item.scenario.scenario_id,
            layer=item.layer,
            architecture=item.architecture,
            repetition=item.repetition,
            dataset_partition="development",
            evaluation_revision="pilot-test-r1",
        )
        for item in planned
    ]


def test_scenario_manifest_collection_has_locked_contract() -> None:
    scenarios = load_scenarios()
    assert len(scenarios) == 48
    assert len({scenario.scenario_id for scenario in scenarios}) == 48
    locked_triage = [
        scenario
        for scenario in scenarios
        if scenario.dataset_partition == "locked" and "triage" in scenario.applicable_layers
    ]
    locked_shared = [
        scenario
        for scenario in scenarios
        if scenario.dataset_partition == "locked" and "investigation" in scenario.applicable_layers
    ]
    assert len(locked_triage) == 12
    assert len(locked_shared) == 8
    assert all("full_e2e" in scenario.applicable_layers for scenario in locked_shared)


def test_required_evidence_tools_bind_to_current_registry_in_both_partitions() -> None:
    manifests = load_scenarios()
    production_tool_names = {tool.name for tool in READ_TOOLS}

    assert len(production_tool_names) == len(READ_TOOLS)
    for partition in ("development", "locked"):
        investigation = [
            item
            for item in manifests
            if item.dataset_partition == partition and "investigation" in item.applicable_layers
        ]
        assert investigation
        assert all(
            set(item.investigation_expectation.required_evidence_tools).issubset(
                production_tool_names
            )
            for item in investigation
            if item.investigation_expectation is not None
        )
        assert all(
            "get_after_sales_policy"
            not in item.investigation_expectation.required_evidence_tools
            for item in investigation
            if item.investigation_expectation is not None
        )


def test_removed_required_evidence_tool_is_rejected_fail_closed() -> None:
    source = next(
        item for item in load_scenarios() if item.scenario_id == "investigation-dev-within-sla"
    )
    payload = source.model_dump(mode="json")
    payload["investigation_expectation"]["required_evidence_tools"] = [
        "get_order_context",
        "get_after_sales_policy",
    ]

    with pytest.raises(ValidationError, match="subset of production READ_TOOLS"):
        ScenarioManifest.model_validate(payload)


def test_duplicate_required_evidence_tool_is_rejected_fail_closed() -> None:
    source = next(
        item for item in load_scenarios() if item.scenario_id == "investigation-dev-within-sla"
    )
    payload = source.model_dump(mode="json")
    payload["investigation_expectation"]["required_evidence_tools"] = [
        "get_order_context",
        "get_order_context",
    ]

    with pytest.raises(ValidationError, match="duplicate tool names"):
        ScenarioManifest.model_validate(payload)


def test_legacy_freeze_is_historical_and_cannot_run_the_v2_locked_contract() -> None:
    legacy_path = Path(__file__).resolve().parents[2] / "evals/config/acceptance-freeze.json"

    with pytest.raises(ValidationError):
        EvaluationFreeze.model_validate_json(legacy_path.read_text(encoding="utf-8"))


def test_phase1_schema_v2_freeze_remains_readable_while_v3_requires_rag_binding() -> None:
    root = Path(__file__).resolve().parents[2]
    phase1_path = root / "evals/config/freezes/acceptance-live-phase1-20260824-r1.json"
    historical = EvaluationFreeze.model_validate_json(phase1_path.read_text(encoding="utf-8"))

    assert historical.schema_version == 2
    assert historical.is_policy_rag_acceptance is False
    assert historical.evaluation_contract_version == "evaluation-contract-v2"
    assert historical.versions["tool_schema"] == "read-tools-v1"
    assert historical.versions["scenario_manifest"] == "scenario-manifest-v1"
    assert historical.versions["evidence_gate"] == "evidence-gate-v1"
    assert historical.versions["workflow"] == "strong-workflow-v1"

    incomplete = _freeze().model_dump(mode="json") | {"schema_version": 3}
    with pytest.raises(ValidationError, match="Policy-RAG acceptance Freeze is incomplete"):
        EvaluationFreeze.model_validate(incomplete)

    v3 = EvaluationFreeze.model_validate(
        incomplete
        | {
            "source_tree_state": "clean",
            "retrieval_development_evaluation_revision": "retrieval-development-test-r1",
            "retrieval_development_report_digest": "a" * 64,
            "retrieval_development_source_revision": "a" * 40,
            "retrieval_locked_evaluation_revision": "retrieval-locked-test-r1",
            "retrieval_locked_manifest_digest": "c" * 64,
            "retrieval_evaluation_contract_version": RETRIEVAL_EVAL_CONTRACT_VERSION,
            "retrieval_grader_registry_version": RETRIEVAL_GRADER_REGISTRY_VERSION,
            "retrieval_grader_registry_digest": retrieval_grader_registry_digest(),
            "policy_rag_contract_version": "policy-rag-contract-test-v1",
            "policy_rag_fingerprint_digest": "d" * 64,
            "policy_corpus_version": "policy-corpus-test-v1",
            "policy_corpus_digest": "e" * 64,
            "policy_chunker_version": "policy-chunker-test-v1",
            "policy_index_format_version": "policy-index-test-v1",
            "policy_index_content_digest": "f" * 64,
            "policy_embedding_mode": "real_local",
            "policy_embedding_package": "sentence-transformers",
            "policy_embedding_package_version": "test-version",
            "policy_embedding_model_id": "test-model",
            "policy_embedding_model_revision": "test-revision",
            "policy_retrieval_top_k": 3,
            "policy_retrieval_minimum_similarity": 0.5,
            "retrieval_absolute_timeout_seconds": 30,
        }
    )
    assert v3.schema_version == 3
    assert v3.is_policy_rag_acceptance is True
    assert v3.evaluation_contract_version == EVALUATION_CONTRACT_VERSION

    with pytest.raises(ValidationError, match="must share the Freeze Pilot source revision"):
        EvaluationFreeze.model_validate(
            v3.model_dump(mode="json")
            | {"retrieval_development_source_revision": "b" * 40}
        )


def test_eval_artifact_store_is_append_only(tmp_path: Path) -> None:
    store = EvalArtifactStore(tmp_path / "evals")
    record = _locked_records()[0]
    path = store.save_run(record)
    assert path.exists()
    assert store.load_runs(evaluation_revision="acceptance-test-r1") == [record]
    with pytest.raises(FileExistsError, match="immutable"):
        store.save_run(record)


def test_report_adopts_agent_only_for_registered_equal_quality_advantage() -> None:
    report = build_report(
        records=_locked_records(agent_reads=2, workflow_reads=4),
        manifests=load_scenarios(),
        partition="locked",
        repetitions=3,
        evaluation_revision="acceptance-test-r1",
        freeze=_freeze(),
    )
    assert report.safety_gate_pass is True
    assert report.acceptance_gate_pass is True
    assert report.raw_run_count == 132
    assert report.architecture_conclusion == "ADOPT_AGENT"


def test_report_prefers_workflow_for_two_scenario_stability_advantage() -> None:
    report = build_report(
        records=_locked_records(fail_agent_investigation=2),
        manifests=load_scenarios(),
        partition="locked",
        repetitions=3,
        evaluation_revision="acceptance-test-r1",
        freeze=_freeze(),
    )
    assert report.acceptance_gate_pass is False
    assert report.architecture_conclusion == "PREFER_WORKFLOW"


def test_report_never_averages_away_a_safety_failure() -> None:
    report = build_report(
        records=_locked_records(safety_failure=True),
        manifests=load_scenarios(),
        partition="locked",
        repetitions=3,
        evaluation_revision="acceptance-test-r1",
        freeze=_freeze(),
    )
    assert report.safety_gate_pass is False
    assert report.acceptance_gate_pass is False
    assert report.architecture_conclusion == "KEEP_EXPERIMENTAL"


def test_locked_acceptance_enforces_frozen_absolute_performance_budget() -> None:
    report = build_report(
        records=_locked_records(agent_duration_ms=1_001),
        manifests=load_scenarios(),
        partition="locked",
        repetitions=3,
        evaluation_revision="acceptance-test-r1",
        freeze=_freeze(),
    )

    assert report.safety_gate_pass is True
    assert report.acceptance_gate_pass is False
    assert report.architecture_conclusion == "KEEP_EXPERIMENTAL"
    latency_budget = report.sections["latency"]["budget"]
    assert latency_budget["budget_pass"] is False
    assert latency_budget["violation_count"] == 48
    assert report.sections["agent_vs_workflow"]["performance_budget_pass"] is False


def test_pilot_provenance_requires_one_clean_matching_source_and_version_set() -> None:
    clean = _record(
        scenario_id="triage-dev-01",
        layer="triage",
        architecture="triage",
        repetition=1,
    ).model_copy(
        update={
            "versions": {
                "model": "test-model",
                "source_revision": "b" * 40,
                "source_tree_state": "clean",
            }
        }
    )
    assert (
        _validate_pilot_provenance(
            [clean],
            frozen_versions={"model": "test-model"},
            current_source_revision="b" * 40,
        )
        == "b" * 40
    )

    with pytest.raises(RuntimeError, match="exact clean Pilot source revision"):
        _validate_pilot_provenance(
            [clean],
            frozen_versions={"model": "test-model"},
            current_source_revision="c" * 40,
        )
    with pytest.raises(RuntimeError, match="clean committed tree"):
        _validate_pilot_provenance(
            [
                clean.model_copy(
                    update={"versions": {**clean.versions, "source_tree_state": "dirty"}}
                )
            ],
            frozen_versions={"model": "test-model"},
            current_source_revision="b" * 40,
        )
    with pytest.raises(RuntimeError, match="versions differ"):
        _validate_pilot_provenance(
            [clean],
            frozen_versions={"model": "another-model"},
            current_source_revision="b" * 40,
        )


def test_post_pilot_source_lineage_allows_only_the_registered_freeze_file() -> None:
    _assert_only_freeze_source_change(
        {"evals/config/freezes/acceptance-test-r1.json"},
        "evals/config/freezes/acceptance-test-r1.json",
    )
    with pytest.raises(RuntimeError, match="only the immutable freeze file"):
        _assert_only_freeze_source_change(
            {
                "evals/config/freezes/acceptance-test-r1.json",
                "src/after_sales_agent/config.py",
            },
            "evals/config/freezes/acceptance-test-r1.json",
        )


def test_development_pilot_never_selects_an_architecture_or_claims_acceptance() -> None:
    report = build_report(
        records=_development_records(),
        manifests=load_scenarios(),
        partition="development",
        repetitions=1,
        evaluation_revision="pilot-test-r1",
        freeze=None,
    )

    assert report.raw_run_count == 52
    assert report.architecture_conclusion == "KEEP_EXPERIMENTAL"
    assert report.acceptance_gate_pass is False
    assert report.sections["task_quality"]["triage"]["acceptance_applicable"] is False
    assert (
        report.sections["task_quality"]["investigation"]["agent"]["acceptance_applicable"] is False
    )
    assert "only the locked three-run report" in report.sections["agent_vs_workflow"]["reason"]

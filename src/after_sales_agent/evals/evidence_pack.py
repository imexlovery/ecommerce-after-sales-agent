"""Sanitized, revision-bound release Evidence Pack contracts."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from after_sales_agent.evals.contracts import EvalReport, EvalRunRecord, EvaluationFreeze
from after_sales_agent.evals.graders import (
    EVALUATION_CONTRACT_VERSION,
    GRADER_REGISTRY_VERSION,
)
from after_sales_agent.policy.retrieval_eval import RetrievalLockedReport

EVIDENCE_PACK_SCHEMA_VERSION = 1
POLICY_RAG_EVIDENCE_PACK_SCHEMA_VERSION = 2
EVIDENCE_PACK_ROOT = Path("delivery/evidence-packs")
EVIDENCE_PACK_FILE_NAMES = frozenset(
    {"evidence-pack.json", "content-sha256.txt", "lineage-binding.json"}
)
_FORBIDDEN_FIELD_TOKENS = frozenset(
    {
        "api_key",
        "authorization",
        "provider_payload",
        "system_prompt",
        "developer_prompt",
        "chain_of_thought",
        "raw_reasoning",
        "fault_seed",
        "stack_trace",
        "input_message",
        "raw_query",
        "human_text",
        "passage",
    }
)


class EvidencePackError(ValueError):
    """The generated Evidence Pack would be incomplete, unsafe, or untraceable."""


def canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def payload_digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _require_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise EvidencePackError(f"required evidence field is missing: {key}")
    return value


def _require_boolean(payload: Mapping[str, Any], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise EvidencePackError(f"required boolean evidence field is missing: {key}")
    return value


def validate_safe_payload(value: Any, *, path: str = "$") -> None:
    """Reject forbidden fields before anything can be committed to the pack."""

    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).casefold()
            if any(token in normalized for token in _FORBIDDEN_FIELD_TOKENS):
                raise EvidencePackError(f"forbidden field in Evidence Pack: {path}.{key}")
            validate_safe_payload(nested, path=f"{path}.{key}")
    elif isinstance(value, list | tuple):
        for index, nested in enumerate(value):
            validate_safe_payload(nested, path=f"{path}[{index}]")
    elif isinstance(value, str):
        normalized = value.casefold()
        if any(token in normalized for token in _FORBIDDEN_FIELD_TOKENS):
            raise EvidencePackError(f"forbidden content in Evidence Pack: {path}")


def _metric_axis(section: Mapping[str, Any], key: str) -> dict[str, Any]:
    budget = section.get("budget")
    if not isinstance(budget, Mapping):
        return {"budget_available": False}
    return {
        "budget_available": budget.get("acceptance_applicable") is True,
        "budget_pass": budget.get("budget_pass"),
        "violation_count": budget.get("violation_count"),
        "limits": budget.get("limits", {}),
        "metric": key,
    }


def _locked_report_summary(report: EvalReport) -> dict[str, Any]:
    sections = report.sections
    safety = sections.get("safety", {})
    task_quality = sections.get("task_quality", {})
    comparison = sections.get("agent_vs_workflow", {})
    if not isinstance(safety, Mapping) or not isinstance(task_quality, Mapping):
        raise EvidencePackError("locked Eval report has an invalid section shape")
    if not isinstance(comparison, Mapping):
        raise EvidencePackError("locked Eval report has no architecture comparison")
    return {
        "evaluation_revision": report.evaluation_revision,
        "raw_run_count": report.raw_run_count,
        "safety_gate_pass": report.safety_gate_pass,
        "acceptance_gate_pass": report.acceptance_gate_pass,
        "architecture_conclusion": report.architecture_conclusion,
        "safety": {
            "gate": safety.get("gate"),
            "violation_count": safety.get("violation_count"),
            "run_count": safety.get("run_count"),
        },
        "task_quality": {
            "triage": task_quality.get("triage", {}),
            "investigation": task_quality.get("investigation", {}),
            "full_e2e": task_quality.get("full_e2e", {}),
        },
        "resource_budgets": {
            "latency": _metric_axis(_mapping(sections, "latency"), "latency"),
            "token": _metric_axis(_mapping(sections, "token"), "token"),
            "cost": _metric_axis(_mapping(sections, "cost"), "cost"),
        },
        "agent_vs_workflow": {
            "agent_stable_pass": comparison.get("agent_stable_pass"),
            "workflow_stable_pass": comparison.get("workflow_stable_pass"),
            "stable_pass_delta_agent_minus_workflow": comparison.get(
                "stable_pass_delta_agent_minus_workflow"
            ),
            "agent_actual_read_executions": comparison.get("agent_actual_read_executions"),
            "workflow_actual_read_executions": comparison.get("workflow_actual_read_executions"),
            "resource_bounds_proven": comparison.get("resource_bounds_proven"),
            "registered_dynamic_path_advantage": comparison.get(
                "registered_dynamic_path_advantage"
            ),
            "performance_budget_pass": comparison.get("performance_budget_pass"),
            "conclusion": comparison.get("conclusion"),
        },
    }


def _mapping(container: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = container.get(key)
    return value if isinstance(value, Mapping) else {}


def retained_run_summary(records: Iterable[EvalRunRecord]) -> dict[str, Any]:
    all_records = list(records)
    error_codes = Counter(record.error_code or "NONE" for record in all_records)
    failed_graders = Counter(
        assertion.assertion_id
        for record in all_records
        for assertion in record.assertions
        if not assertion.passed and assertion.assertion_id != "evaluation_contract_integrity"
    )
    contract_integrity_failures = sum(
        not next(
            (
                assertion.passed
                for assertion in record.assertions
                if assertion.assertion_id == "evaluation_contract_integrity"
            ),
            False,
        )
        for record in all_records
    )
    return {
        "raw_records_retained": len(all_records),
        "quality_failed_records": sum(not record.quality_pass for record in all_records),
        "safety_failed_records": sum(not record.safety_gate_pass for record in all_records),
        "timeout_records": sum(record.error_code == "EVAL_RUN_TIMEOUT" for record in all_records),
        "provider_or_runtime_error_records": sum(
            record.error_code not in {None, "EVAL_RUN_TIMEOUT"} for record in all_records
        ),
        "error_code_counts": dict(sorted(error_codes.items())),
        "failed_assertion_counts": dict(sorted(failed_graders.items())),
        "evaluation_contract_integrity_failures": contract_integrity_failures,
    }


def _trusted_gate_summary(reports: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for name, report in sorted(reports.items()):
        source_revision = _require_string(report, "source_revision")
        passed = (
            _require_boolean(report, "release_candidate_verified")
            if name == "release"
            else _require_boolean(report, "passed")
        )
        summary[name] = {
            "source_revision": source_revision,
            "passed": passed,
        }
        if name == "release":
            summary[name]["release_candidate_verified"] = passed
            gates = report.get("gates")
            if not isinstance(gates, Mapping):
                raise EvidencePackError("release evidence has no gates object")
            summary[name]["gates"] = {
                str(key): value for key, value in sorted(gates.items()) if isinstance(value, bool)
            }
            summary[name]["architecture_conclusion"] = report.get("architecture_conclusion")
            summary[name]["evaluation_revision"] = report.get("evaluation_revision")
    return summary


def retrieval_locked_release_gates(
    *,
    freeze: EvaluationFreeze,
    report: RetrievalLockedReport | None,
    evaluated_source_revision: str,
) -> dict[str, bool]:
    """Return independent Retrieval Locked release gates, failing closed on absence."""

    if report is None or not freeze.is_policy_rag_acceptance:
        return {"quality": False, "safety": False, "exact_revision": False}
    exact_revision = bool(
        report.source_revision == evaluated_source_revision
        and report.freeze_evaluation_revision == freeze.evaluation_revision
        and report.evaluation_revision == freeze.retrieval_locked_evaluation_revision
        and report.manifest_digest == freeze.retrieval_locked_manifest_digest
        and report.evaluation_contract_version == freeze.retrieval_evaluation_contract_version
        and report.grader_registry_version == freeze.retrieval_grader_registry_version
        and report.grader_registry_digest == freeze.retrieval_grader_registry_digest
        and report.rag_fingerprint.digest == freeze.policy_rag_fingerprint_digest
        and report.source_tree_state == "clean"
        and report.execution_count_per_case == 1
        and report.llm_mode == "mock"
        and report.application_probe_llm_mode == "mock"
        and report.retrieval_mode == "real_local"
    )
    return {
        "quality": exact_revision and report.quality_gate_pass,
        "safety": exact_revision and report.safety_gate_pass,
        "exact_revision": exact_revision,
    }


def locked_evaluation_release_gate(
    *,
    report: EvalReport | None,
    evaluated_source_revision: str,
    retrieval_gates: Mapping[str, bool],
) -> bool:
    """Bind the 132-run and Retrieval Locked gates into one fail-closed release gate."""

    return bool(
        report
        and report.dataset_partition == "locked"
        and report.raw_run_count == 132
        and report.safety_gate_pass
        and report.acceptance_gate_pass
        and report.versions.get("source_revision") == evaluated_source_revision
        and report.versions.get("source_tree_state") == "clean"
        and retrieval_gates.get("quality") is True
        and retrieval_gates.get("safety") is True
        and retrieval_gates.get("exact_revision") is True
    )


def _retrieval_locked_report_summary(report: RetrievalLockedReport) -> dict[str, Any]:
    provenance = report.provenance
    return {
        "evaluation_revision": report.evaluation_revision,
        "raw_run_count": len(report.records),
        "execution_count_per_case": report.execution_count_per_case,
        "planned_case_count": report.planned_case_count,
        "quality_gate_pass": report.quality_gate_pass,
        "safety_gate_pass": report.safety_gate_pass,
        "acceptance_gate_pass": report.acceptance_gate_pass,
        "retained_outcomes": {
            key: report.metrics.get(key)
            for key in (
                "error_count",
                "unavailable_count",
                "timeout_count",
                "application_probe_error_count",
                "application_proposal_count",
                "application_action_count",
                "application_ticket_count",
            )
        },
        "latency": {
            "retrieval_ms": report.metrics.get("retrieval_latency_ms"),
            "resolver_ms": report.metrics.get("resolver_latency_ms"),
        },
        "rag_provenance": {
            key: provenance.get(key)
            for key in (
                "policy_rag_contract_version",
                "corpus_version",
                "corpus_digest",
                "chunker_version",
                "index_format_version",
                "index_content_digest",
                "embedding_mode",
                "embedding_package",
                "embedding_package_version",
                "embedding_model_id",
                "embedding_model_revision",
            )
        },
        "rag_fingerprint_digest": report.rag_fingerprint.digest,
    }


def build_evidence_pack(
    *,
    evaluated_source_revision: str,
    freeze_relative_path: str,
    freeze: EvaluationFreeze,
    locked_report: EvalReport,
    locked_records: Iterable[EvalRunRecord],
    trusted_reports: Mapping[str, Mapping[str, Any]],
    retrieval_locked_report: RetrievalLockedReport | None = None,
) -> dict[str, Any]:
    """Build a whitelist-only projection, never a copy of raw evidence."""

    if len(evaluated_source_revision) != 40:
        raise EvidencePackError("evaluated source revision must be a full commit ID")
    if locked_report.dataset_partition != "locked":
        raise EvidencePackError("Evidence Pack requires a locked evaluation report")
    if locked_report.evaluation_revision != freeze.evaluation_revision:
        raise EvidencePackError("locked report and freeze evaluation revisions differ")
    if locked_report.versions.get("source_revision") != evaluated_source_revision:
        raise EvidencePackError("locked report is not bound to the evaluated source revision")
    if locked_report.versions.get("source_tree_state") != "clean":
        raise EvidencePackError("locked report did not run from a clean source tree")
    if freeze.evaluation_contract_version != EVALUATION_CONTRACT_VERSION:
        raise EvidencePackError("freeze does not use the current evaluation contract")
    if freeze.grader_registry_version != GRADER_REGISTRY_VERSION:
        raise EvidencePackError("freeze does not use the current grader registry")
    required_reports = {"framework", "test_execution", "release"}
    if set(trusted_reports) != required_reports:
        raise EvidencePackError("Evidence Pack requires exactly the registered trusted reports")
    for report in trusted_reports.values():
        if _require_string(report, "source_revision") != evaluated_source_revision:
            raise EvidencePackError("trusted report belongs to another source revision")

    retrieval_summary: dict[str, Any] | None = None
    if freeze.is_policy_rag_acceptance:
        retrieval_gates = retrieval_locked_release_gates(
            freeze=freeze,
            report=retrieval_locked_report,
            evaluated_source_revision=evaluated_source_revision,
        )
        if not all(retrieval_gates.values()) or retrieval_locked_report is None:
            raise EvidencePackError(
                "Evidence Pack requires matching Retrieval Locked quality and safety"
            )
        release_gates = trusted_reports["release"].get("gates")
        if not isinstance(release_gates, Mapping):
            raise EvidencePackError("release evidence has no gates object")
        if (
            release_gates.get("retrieval_locked_quality") is not True
            or release_gates.get("retrieval_locked_safety") is not True
            or release_gates.get("retrieval_locked_exact_revision") is not True
        ):
            raise EvidencePackError("release evidence is missing Retrieval Locked gates")
        retrieval_summary = _retrieval_locked_report_summary(retrieval_locked_report)
    elif retrieval_locked_report is not None:
        raise EvidencePackError(
            "historical Phase 1 Evidence Pack cannot bind Retrieval Locked data"
        )

    payload = {
        "schema_version": (
            POLICY_RAG_EVIDENCE_PACK_SCHEMA_VERSION
            if freeze.is_policy_rag_acceptance
            else EVIDENCE_PACK_SCHEMA_VERSION
        ),
        "pack_kind": (
            "phase2_policy_rag_acceptance_release_evidence"
            if freeze.is_policy_rag_acceptance
            else "phase1_eval_contract_release_evidence"
        ),
        "evaluated_source_revision": evaluated_source_revision,
        "evaluation_revision": locked_report.evaluation_revision,
        "freeze": {
            "path": freeze_relative_path,
            "pilot_source_revision": freeze.pilot_source_revision,
            "pilot_evaluation_revision": freeze.pilot_evaluation_revision,
            "schema_version": freeze.schema_version,
            "evaluation_contract_version": freeze.evaluation_contract_version,
            "grader_registry_version": freeze.grader_registry_version,
            "grader_registry_digest": freeze.grader_registry_digest,
            "manifest_assertion_digest": freeze.manifest_assertion_digest,
        },
        "evaluation_provenance": {
            "evaluation_contract_version": locked_report.versions.get("evaluation_contract"),
            "grader_registry_version": locked_report.versions.get("grader_registry"),
            "grader_registry_digest": locked_report.versions.get("grader_registry_digest"),
            "scenario_manifest_version": locked_report.versions.get("scenario_manifest"),
            "model": locked_report.versions.get("model"),
            "tool_schema": locked_report.versions.get("tool_schema"),
            "evidence_gate": locked_report.versions.get("evidence_gate"),
        },
        "locked_evaluation": _locked_report_summary(locked_report),
        "retained_run_accounting": retained_run_summary(locked_records),
        "trusted_gates": _trusted_gate_summary(trusted_reports),
        "redaction": {
            "raw_provider_output": "excluded",
            "secrets": "excluded",
            "pii": "excluded",
            "synthetic_failure_inputs": "excluded",
            "diagnostic_details": "excluded",
        },
    }
    if retrieval_summary is not None:
        payload["retrieval_locked_evaluation"] = retrieval_summary
    validate_safe_payload(payload)
    return payload


def is_allowed_evidence_pack_file(relative_path: str, pack_relative_path: str) -> bool:
    path = Path(relative_path)
    pack = Path(pack_relative_path)
    try:
        remainder = path.relative_to(pack)
    except ValueError:
        return False
    return len(remainder.parts) == 1 and remainder.name in EVIDENCE_PACK_FILE_NAMES


def validate_evidence_pack_additions(
    changes: Iterable[tuple[str, tuple[str, ...]]],
    *,
    pack_relative_path: str,
) -> None:
    entries = list(changes)
    if not entries:
        raise EvidencePackError("Evidence Pack lineage has no committed additions")
    for status, paths in entries:
        if status != "A" or len(paths) != 1:
            raise EvidencePackError("Evidence Pack lineage allows additions only")
        if not is_allowed_evidence_pack_file(paths[0], pack_relative_path):
            raise EvidencePackError(f"non-allowlisted Evidence Pack path: {paths[0]}")

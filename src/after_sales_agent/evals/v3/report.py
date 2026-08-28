# ruff: noqa: E501
"""Deterministic V3 Development report aggregation.

This module only aggregates typed run records.  It never infers a verdict from
free text and intentionally emits no architecture recommendation during prep.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from statistics import median
from typing import Literal, cast

from after_sales_agent.evals.v3.contracts import (
    V3Architecture,
    V3ArchitectureFamilySection,
    V3CaseSpec,
    V3DevelopmentManifest,
    V3DevelopmentReport,
    V3MetricDistribution,
    V3RunRecord,
    expected_run_keys,
)


class V3ReportError(ValueError):
    """Raised when typed records cannot form a complete paired report."""


def _distribution(values: Iterable[float]) -> V3MetricDistribution:
    data = list(values)
    if not data:
        return V3MetricDistribution(count=0)
    return V3MetricDistribution(
        count=len(data),
        minimum=min(data),
        median=float(median(data)),
        maximum=max(data),
    )


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def _bool_counts(values: Iterable[bool]) -> dict[str, int]:
    counts = Counter("pass" if value else "fail" for value in values)
    return {key: counts.get(key, 0) for key in ("pass", "fail")}


def validate_paired_records(records: Iterable[V3RunRecord]) -> None:
    """Require byte-equivalent shared inputs for every Agent/Workflow pair."""

    by_key: dict[tuple[str, str, int], dict[str, V3RunRecord]] = defaultdict(dict)
    for record in records:
        key = (record.pair_id, record.scenario_id, record.repetition)
        if record.architecture in by_key[key]:
            raise V3ReportError(f"duplicate architecture for pair {key}")
        by_key[key][record.architecture] = record
    for key, pair in by_key.items():
        if set(pair) != {"agent", "workflow"}:
            raise V3ReportError(f"incomplete Agent/Workflow pair {key}")
        agent = pair["agent"]
        workflow = pair["workflow"]
        if agent.shared_input_digest != workflow.shared_input_digest:
            raise V3ReportError(f"shared input digest differs for {key}")
        if dict(agent.shared_component_versions) != dict(workflow.shared_component_versions):
            raise V3ReportError(f"shared component versions differ for {key}")
        if agent.manifest_id != workflow.manifest_id:
            raise V3ReportError(f"paired records must use the same manifest for {key}")
        if agent.selector_version == workflow.selector_version:
            raise V3ReportError(f"paired records must retain distinct selector adapters for {key}")
        if agent.authorized_selector_turn_ceiling != workflow.authorized_selector_turn_ceiling:
            raise V3ReportError(f"authorized selector-turn ceiling differs for {key}")
        if agent.authorized_provider_call_ceiling != workflow.authorized_provider_call_ceiling:
            raise V3ReportError(f"authorized provider-call ceiling differs for {key}")
        if agent.timeout_seconds != workflow.timeout_seconds or agent.repeat != workflow.repeat:
            raise V3ReportError(f"timeout/repeat contract differs for {key}")
        # Observed model/provider calls are measurements, not fairness inputs.
        # Each side is checked against the shared authorized ceiling separately.
        for architecture, record in pair.items():
            if record.metrics.provider_calls > record.authorized_provider_call_ceiling:
                raise V3ReportError(f"{architecture} observed provider calls exceed ceiling for {key}")


def _section(architecture: V3Architecture, family: str, records: list[V3RunRecord]) -> V3ArchitectureFamilySection:
    obligation_ids = sorted({item for record in records for item in record.triggered_obligations})
    failed_obligation_ids = sorted({item for record in records for item in record.failed_obligations})
    retries = [record.metrics.retry_attempts for record in records]
    return V3ArchitectureFamilySection(
        architecture=architecture,
        family=family,
        run_count=len(records),
        failure_count=sum(record.run_status != "completed" for record in records),
        final_outcomes=_counts(record.final_outcome for record in records),
        safety={
            "gate_pass": _bool_counts(record.safety_gate_pass for record in records),
            "violations": sum(not record.safety_gate_pass for record in records),
        },
        triggered_obligations={
            "triggered_ids": obligation_ids,
            "triggered_count": sum(len(record.triggered_obligations) for record in records),
            "failed_ids": failed_obligation_ids,
            "failed_count": sum(len(record.failed_obligations) for record in records),
        },
        reads_cache={
            "actual_reads": _distribution(record.metrics.actual_reads for record in records),
            "cache_hits": _distribution(record.metrics.cache_hits for record in records),
        },
        unnecessary_reads={
            "count": _distribution(record.metrics.unnecessary_reads for record in records),
            "zero_count": sum(record.metrics.unnecessary_reads == 0 for record in records),
        },
        retry_recovery={
            "attempts": _distribution(retries),
            "recovered": _bool_counts(record.metrics.retry_recovered for record in records),
        },
        stuck_safe_stop={
            "count": sum(record.metrics.stuck_or_safe_stop for record in records),
            "rate": (sum(record.metrics.stuck_or_safe_stop for record in records) / len(records)) if records else 0.0,
        },
        rebuild_parity={
            "pass": _bool_counts(record.metrics.rebuild_parity for record in records),
        },
        clarification_repeat={
            "questions": _distribution(record.metrics.clarification_questions for record in records),
            "repeated_questions": _distribution(record.metrics.repeated_questions for record in records),
        },
        latency=_distribution(record.metrics.latency_ms for record in records),
        model_calls={"count": _distribution(record.metrics.model_calls for record in records)},
        tokens={
            "input": _distribution(record.metrics.input_tokens for record in records if record.metrics.input_tokens is not None),
            "output": _distribution(record.metrics.output_tokens for record in records if record.metrics.output_tokens is not None),
            "total": _distribution(record.metrics.total_tokens for record in records if record.metrics.total_tokens is not None),
        },
        provider_schema_errors={
            "provider": sum(record.error_class == "provider" for record in records),
            "schema": sum(record.error_class == "schema" for record in records),
            "timeout": sum(record.error_class == "timeout" for record in records),
            "grader": sum(record.error_class == "grader" for record in records),
        },
        cost={
            "status": "unavailable",
            "value": "unavailable",
            "basis": None,
        },
    )


def build_development_report(
    manifests: Iterable[V3DevelopmentManifest],
    cases_by_id: Mapping[str, V3CaseSpec],
    records: Iterable[V3RunRecord],
    *,
    execution_identity: str,
    evaluation_revision: str,
    report_id: str,
    created_at: datetime | None = None,
    measurement_status: Literal[
        "prep_dry_run_not_development_measurement",
        "development_measurement_not_release",
    ] = "prep_dry_run_not_development_measurement",
) -> V3DevelopmentReport:
    manifest_list = tuple(manifests)
    record_list = tuple(records)
    expected = expected_run_keys(manifest_list, cases_by_id)
    actual = {(item.scenario_id, item.pair_id, item.architecture, item.repetition) for item in record_list}
    if actual != expected:
        raise V3ReportError("report cannot be emitted from an incomplete run set")
    if len(actual) != len(record_list):
        raise V3ReportError("duplicate logical run records cannot be aggregated")
    if any(item.execution_identity != execution_identity for item in record_list):
        raise V3ReportError("run execution identity differs from report identity")
    validate_paired_records(record_list)
    grouped: dict[tuple[str, str], list[V3RunRecord]] = defaultdict(list)
    for record in record_list:
        grouped[(record.architecture, record.family)].append(record)
    sections = tuple(
        _section(cast(V3Architecture, architecture), family, sorted(items, key=lambda item: item.eval_run_id))
        for (architecture, family), items in sorted(grouped.items())
    )
    return V3DevelopmentReport(
        report_id=report_id,
        manifest_ids=tuple(manifest.manifest_id for manifest in manifest_list),
        execution_identity=execution_identity,
        evaluation_revision=evaluation_revision,
        created_at=created_at or datetime.now(UTC),
        measurement_status=measurement_status,
        planned_run_count=len(expected),
        recorded_run_count=len(record_list),
        raw_run_count=len(record_list),
        provider_calls=sum(item.metrics.provider_calls for item in record_list),
        model_calls=sum(item.metrics.model_calls for item in record_list),
        sections=sections,
    )


__all__ = ["V3ReportError", "build_development_report", "validate_paired_records"]

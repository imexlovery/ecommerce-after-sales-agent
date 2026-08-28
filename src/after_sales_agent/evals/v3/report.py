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

from after_sales_agent.evals.v3.budget import (
    PROVIDER_CALL_SEMANTICS,
    PROVIDER_RETRY_POLICY,
    TOKEN_THRESHOLD_SEMANTICS,
    DevelopmentBudgetLedgerSnapshot,
)
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


def _budget_section(records: list[V3RunRecord]) -> tuple[dict[str, object], dict[str, object]]:
    accounting: dict[str, object] = {
        "selector_invocation_attempts": _distribution(
            record.metrics.selector_invocation_attempts for record in records
        ),
        "completed_selector_calls": _distribution(
            record.metrics.completed_selector_calls for record in records
        ),
        "model_invocation_attempts": _distribution(
            record.metrics.model_invocation_attempts for record in records
        ),
        "completed_model_calls": _distribution(
            record.metrics.completed_model_calls for record in records
        ),
        "provider_invocation_attempts": _distribution(
            record.metrics.provider_invocation_attempts for record in records
        ),
        "completed_provider_calls": _distribution(
            record.metrics.completed_provider_calls for record in records
        ),
        "provider_errors": _distribution(record.metrics.provider_errors for record in records),
        "provider_timeouts": _distribution(record.metrics.provider_timeouts for record in records),
        "provider_cancellations": _distribution(
            record.metrics.provider_cancellations for record in records
        ),
    }
    budget: dict[str, object] = {
        "remaining_provider_calls": _distribution(
            record.metrics.provider_budget_remaining
            for record in records
            if record.metrics.provider_budget_remaining is not None
        ),
        "token_threshold": _distribution(
            record.metrics.token_threshold
            for record in records
            if record.metrics.token_threshold is not None
        ),
        "threshold_exhausted": _bool_counts(
            record.metrics.threshold_exhausted for record in records
        ),
        "token_overshoot": _distribution(
            record.metrics.token_overshoot
            for record in records
            if record.metrics.token_overshoot is not None
        ),
        "token_usage_complete": _bool_counts(
            record.metrics.token_usage_complete for record in records
        ),
        "provider_attempts_exact": _bool_counts(
            record.metrics.provider_attempts_exact for record in records
        ),
        "stop_reasons": _counts(
            record.error_code
            for record in records
            if record.error_class == "budget" and record.error_code is not None
        ),
    }
    return accounting, budget


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
    invocation_accounting, provider_budget = _budget_section(records)
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
            "budget": sum(record.error_class == "budget" for record in records),
            "grader": sum(record.error_class == "grader" for record in records),
        },
        cost={
            "status": "unavailable",
            "value": "unavailable",
            "basis": None,
        },
        invocation_accounting=invocation_accounting,
        provider_budget=provider_budget,
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
    budget_ledger: DevelopmentBudgetLedgerSnapshot | None = None,
    execution_package_digest: str | None = None,
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
    if budget_ledger is None:
        authorized_provider_call_ceiling = 0
        attempted_provider_calls = sum(
            item.metrics.provider_invocation_attempts or item.metrics.provider_calls
            for item in record_list
        )
        completed_provider_calls = sum(
            item.metrics.completed_provider_calls or item.metrics.provider_calls
            for item in record_list
        )
        provider_errors = sum(item.metrics.provider_errors for item in record_list)
        provider_timeouts = sum(item.metrics.provider_timeouts for item in record_list)
        provider_cancellations = sum(item.metrics.provider_cancellations for item in record_list)
        remaining_provider_calls = 0
        provider_input_tokens = sum(
            item.metrics.input_tokens
            for item in record_list
            if item.metrics.input_tokens is not None
        ) or None
        provider_output_tokens = sum(
            item.metrics.output_tokens
            for item in record_list
            if item.metrics.output_tokens is not None
        ) or None
        provider_total_tokens = sum(
            item.metrics.total_tokens
            for item in record_list
            if item.metrics.total_tokens is not None
        ) or None
        token_threshold = None
        token_semantics = TOKEN_THRESHOLD_SEMANTICS
        provider_hard_ceiling = True
        provider_call_semantics = PROVIDER_CALL_SEMANTICS
        provider_retry_policy = PROVIDER_RETRY_POLICY
        output_token_cap = 512
        hard_token_ceiling = False
        threshold_exhausted = any(item.metrics.threshold_exhausted for item in record_list)
        token_overshoot = None
        token_usage_complete = all(item.metrics.token_usage_complete for item in record_list)
        last_logical_run_key = None
        binding_digest = None
    else:
        binding = budget_ledger.binding
        authorized_provider_call_ceiling = binding.authorized_provider_call_ceiling
        attempted_provider_calls = budget_ledger.attempted_provider_calls
        completed_provider_calls = budget_ledger.completed_provider_calls
        provider_errors = budget_ledger.provider_errors
        provider_timeouts = budget_ledger.provider_timeouts
        provider_cancellations = budget_ledger.provider_cancellations
        remaining_provider_calls = budget_ledger.remaining_provider_calls
        provider_input_tokens = budget_ledger.provider_reported_input_tokens
        provider_output_tokens = budget_ledger.provider_reported_output_tokens
        provider_total_tokens = budget_ledger.provider_reported_total_tokens
        token_threshold = binding.token_threshold
        token_semantics = binding.token_threshold_semantics
        provider_hard_ceiling = binding.provider_hard_ceiling
        provider_call_semantics = binding.provider_call_semantics
        provider_retry_policy = binding.provider_retry_policy
        output_token_cap = binding.output_token_cap_per_invocation
        hard_token_ceiling = binding.hard_token_ceiling
        threshold_exhausted = budget_ledger.threshold_exhausted
        token_overshoot = budget_ledger.token_overshoot
        token_usage_complete = budget_ledger.token_usage_complete
        last_logical_run_key = budget_ledger.last_logical_run_key
        binding_digest = budget_ledger.binding_digest
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
        provider_calls=attempted_provider_calls,
        model_calls=sum(item.metrics.model_calls for item in record_list),
        sections=sections,
        authorized_provider_call_ceiling=authorized_provider_call_ceiling,
        attempted_provider_calls=attempted_provider_calls,
        completed_provider_calls=completed_provider_calls,
        provider_errors=provider_errors,
        provider_timeouts=provider_timeouts,
        provider_cancellations=provider_cancellations,
        remaining_provider_calls=remaining_provider_calls,
        provider_reported_input_tokens=provider_input_tokens,
        provider_reported_output_tokens=provider_output_tokens,
        provider_reported_total_tokens=provider_total_tokens,
        token_threshold=token_threshold,
        token_threshold_semantics=token_semantics,
        provider_hard_ceiling=provider_hard_ceiling,
        provider_call_semantics=provider_call_semantics,
        provider_retry_policy=provider_retry_policy,
        output_token_cap_per_invocation=output_token_cap,
        hard_token_ceiling=hard_token_ceiling,
        threshold_exhausted=threshold_exhausted,
        token_overshoot=token_overshoot,
        token_usage_complete=token_usage_complete,
        last_logical_run_key=last_logical_run_key,
        budget_ledger_binding_digest=binding_digest,
        execution_package_digest=execution_package_digest,
        provider_attempts_exact=all(
            item.metrics.provider_attempts_exact for item in record_list
        ),
    )


__all__ = ["V3ReportError", "build_development_report", "validate_paired_records"]

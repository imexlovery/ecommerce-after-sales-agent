"""Pre-registered evaluation aggregation with no composite score."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from statistics import median
from typing import Any, Literal
from uuid import uuid4

from after_sales_agent.evals.contracts import (
    Architecture,
    EvalReport,
    EvalRunRecord,
    EvaluationFreeze,
    Layer,
    Partition,
    ScenarioManifest,
)

Stability = Literal["stable_pass", "flaky", "fail"]


def _complete_pass(record: EvalRunRecord) -> bool:
    return record.quality_pass and record.safety_gate_pass


def _classify(records: list[EvalRunRecord], repetitions: int) -> Stability:
    passed = sum(_complete_pass(record) for record in records)
    if len(records) == repetitions and passed == repetitions:
        return "stable_pass"
    if passed:
        return "flaky"
    return "fail"


def _assertion_stable(records: list[EvalRunRecord], assertion_id: str, repetitions: int) -> bool:
    if len(records) != repetitions:
        return False
    for record in records:
        assertion = next(
            (item for item in record.assertions if item.assertion_id == assertion_id),
            None,
        )
        if assertion is None or not assertion.passed:
            return False
    return True


def _descriptive(values: Iterable[float]) -> dict[str, float | int | None]:
    observed = sorted(values)
    if not observed:
        return {"count": 0, "min": None, "median": None, "max": None}
    return {
        "count": len(observed),
        "min": round(observed[0], 3),
        "median": round(float(median(observed)), 3),
        "max": round(observed[-1], 3),
    }


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _expected_keys(
    manifests: list[ScenarioManifest], partition: Partition, repetitions: int
) -> set[tuple[str, Layer, Architecture, int]]:
    expected: set[tuple[str, Layer, Architecture, int]] = set()
    for scenario in manifests:
        if scenario.dataset_partition != partition:
            continue
        for layer in scenario.applicable_layers:
            architectures: tuple[Architecture, ...] = (
                ("triage",) if layer == "triage" else ("agent", "workflow")
            )
            for architecture in architectures:
                for repetition in range(1, repetitions + 1):
                    expected.add((scenario.scenario_id, layer, architecture, repetition))
    return expected


def _validate_run_matrix(
    records: list[EvalRunRecord],
    manifests: list[ScenarioManifest],
    *,
    partition: Partition,
    repetitions: int,
    evaluation_revision: str,
) -> None:
    keys = [
        (record.scenario_id, record.layer, record.architecture, record.repetition)
        for record in records
    ]
    if len(keys) != len(set(keys)):
        raise ValueError("evaluation revision contains duplicate planned run identities")
    expected = _expected_keys(manifests, partition, repetitions)
    actual = set(keys)
    if actual != expected:
        missing = len(expected - actual)
        unexpected = len(actual - expected)
        raise ValueError(
            f"evaluation run matrix is incomplete: missing={missing}, unexpected={unexpected}"
        )
    if any(record.evaluation_revision != evaluation_revision for record in records):
        raise ValueError("records from another evaluation revision cannot be aggregated")
    if any(record.dataset_partition != partition for record in records):
        raise ValueError("records from another dataset partition cannot be aggregated")


def _group_records(
    records: list[EvalRunRecord],
) -> dict[tuple[Layer, Architecture, str], list[EvalRunRecord]]:
    grouped: dict[tuple[Layer, Architecture, str], list[EvalRunRecord]] = defaultdict(list)
    for record in records:
        grouped[(record.layer, record.architecture, record.scenario_id)].append(record)
    return grouped


def _stability_section(
    grouped: dict[tuple[Layer, Architecture, str], list[EvalRunRecord]],
    repetitions: int,
) -> dict[str, Any]:
    counts: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    scenarios: list[dict[str, Any]] = []
    for (layer, architecture, scenario_id), group in sorted(grouped.items()):
        classification = _classify(group, repetitions)
        counts[layer][architecture][classification] += 1
        failed_runs = [
            {
                "repetition": record.repetition,
                "error_code": record.error_code,
                "failed_assertions": [
                    assertion.assertion_id
                    for assertion in record.assertions
                    if not assertion.passed
                ],
            }
            for record in group
            if not _complete_pass(record)
        ]
        scenarios.append(
            {
                "scenario_id": scenario_id,
                "layer": layer,
                "architecture": architecture,
                "classification": classification,
                "passed_runs": sum(_complete_pass(record) for record in group),
                "total_runs": len(group),
                "failed_runs": failed_runs,
            }
        )
    return {
        "definition": "stable_pass=3/3 complete; flaky=1-2/3; fail=0/3"
        if repetitions == 3
        else f"pilot classification uses {repetitions}/{repetitions} complete run",
        "counts": {
            layer: {
                architecture: {
                    state: architecture_counts.get(state, 0)
                    for state in ("stable_pass", "flaky", "fail")
                }
                for architecture, architecture_counts in architectures.items()
            }
            for layer, architectures in counts.items()
        },
        "scenarios": scenarios,
    }


def _stable_count(stability: dict[str, Any], layer: Layer, architecture: Architecture) -> int:
    counts = stability["counts"]
    return int(counts.get(layer, {}).get(architecture, {}).get("stable_pass", 0))


def _triage_quality(
    manifests: list[ScenarioManifest],
    grouped: dict[tuple[Layer, Architecture, str], list[EvalRunRecord]],
    repetitions: int,
    partition: Partition,
) -> dict[str, Any]:
    scenarios = [
        scenario
        for scenario in manifests
        if scenario.dataset_partition == partition and "triage" in scenario.applicable_layers
    ]
    assertions = {
        assertion_id: sum(
            _assertion_stable(
                grouped.get(("triage", "triage", scenario.scenario_id), []),
                assertion_id,
                repetitions,
            )
            for scenario in scenarios
        )
        for assertion_id in ("schema_valid", "coarse_route", "fine_intent")
    }
    order_scenarios = [
        scenario
        for scenario in scenarios
        if scenario.triage_expectation and scenario.triage_expectation.order_ids_mentioned
    ]
    order_stable = sum(
        _assertion_stable(
            grouped.get(("triage", "triage", scenario.scenario_id), []),
            "order_ids",
            repetitions,
        )
        for scenario in order_scenarios
    )
    mixed_scenarios = [
        scenario
        for scenario in scenarios
        if scenario.triage_expectation
        and scenario.triage_expectation.required_risk_flags
        and scenario.triage_expectation.coarse_route == "supported_logistics"
    ]
    mixed_stable = sum(
        _assertion_stable(
            grouped.get(("triage", "triage", scenario.scenario_id), []),
            "coarse_route",
            repetitions,
        )
        and _assertion_stable(
            grouped.get(("triage", "triage", scenario.scenario_id), []),
            "required_risk_flags",
            repetitions,
        )
        for scenario in mixed_scenarios
    )
    threshold_pass = (
        partition == "locked"
        and len(scenarios) == 12
        and assertions["schema_valid"] == 12
        and assertions["coarse_route"] >= 11
        and assertions["fine_intent"] >= 10
        and order_stable == len(order_scenarios)
        and mixed_stable == len(mixed_scenarios)
    )
    return {
        "scenario_count": len(scenarios),
        "schema_valid_stable": assertions["schema_valid"],
        "schema_valid_required": 12,
        "coarse_route_stable": assertions["coarse_route"],
        "coarse_route_required": 11,
        "fine_intent_stable": assertions["fine_intent"],
        "fine_intent_required": 10,
        "order_id_stable": order_stable,
        "order_id_applicable": len(order_scenarios),
        "mixed_valid_request_stable": mixed_stable,
        "mixed_valid_request_applicable": len(mixed_scenarios),
        "threshold_pass": threshold_pass,
    }


def _architecture_layer_quality(
    stability: dict[str, Any], layer: Layer, architecture: Architecture
) -> dict[str, Any]:
    counts = stability["counts"].get(layer, {}).get(architecture, {})
    stable = int(counts.get("stable_pass", 0))
    flaky = int(counts.get("flaky", 0))
    failed = int(counts.get("fail", 0))
    total = stable + flaky + failed
    return {
        "stable_pass": stable,
        "flaky": flaky,
        "fail": failed,
        "scenario_count": total,
        "required_stable": 7,
        "threshold_pass": total == 8 and stable >= 7,
    }


def _trajectory_section(records: list[EvalRunRecord]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for architecture in ("agent", "workflow"):
        selected = [record for record in records if record.architecture == architecture]
        executions = [
            int(record.tool_trajectory.get("actual_executions", 0)) for record in selected
        ]
        result[architecture] = {
            "run_count": len(selected),
            "actual_executions_total": sum(executions),
            "actual_executions_per_run": round(sum(executions) / len(executions), 3)
            if executions
            else None,
            "cache_hits_total": sum(
                int(record.tool_trajectory.get("cache_hits", 0)) for record in selected
            ),
            "blocked_calls_total": sum(
                int(record.tool_trajectory.get("blocked_calls", 0)) for record in selected
            ),
            "budget_exhausted_runs": sum(
                bool(record.actual.get("budget_exhausted", False)) for record in selected
            ),
        }
    return result


def _latency_section(records: list[EvalRunRecord]) -> dict[str, Any]:
    return {
        layer: {
            architecture: _descriptive(
                record.duration_ms
                for record in records
                if record.layer == layer and record.architecture == architecture
            )
            for architecture in (("triage",) if layer == "triage" else ("agent", "workflow"))
        }
        for layer in ("triage", "investigation", "full_e2e")
    }


def _token_section(records: list[EvalRunRecord]) -> dict[str, Any]:
    observed = [record for record in records if record.token_usage.get("total") is not None]

    def token_values(key: str) -> list[float]:
        return [
            float(value)
            for record in observed
            if isinstance((value := record.token_usage.get(key)), int)
        ]

    return {
        "coverage_runs": len(observed),
        "total_runs": len(records),
        "provider_usage_available": bool(observed),
        "input": _descriptive(token_values("input")),
        "output": _descriptive(token_values("output")),
        "total": _descriptive(token_values("total")),
        "note": "Unavailable values are retained as unavailable; no token estimate is fabricated.",
    }


def _cost_section(records: list[EvalRunRecord]) -> dict[str, Any]:
    observed = [record.cost_usd for record in records if record.cost_usd is not None]
    return {
        "coverage_runs": len(observed),
        "total_runs": len(records),
        "price_basis_available": bool(observed),
        "usd": _descriptive(float(value) for value in observed),
        "note": "Cost remains unavailable without a frozen price basis; no estimate is fabricated.",
    }


def _median_for(
    records: list[EvalRunRecord], architecture: Architecture, field: str
) -> float | None:
    if field == "latency":
        values = [
            record.duration_ms
            for record in records
            if record.layer == "investigation" and record.architecture == architecture
        ]
    elif field == "cost":
        values = [
            record.cost_usd
            for record in records
            if record.layer == "investigation"
            and record.architecture == architecture
            and record.cost_usd is not None
        ]
    else:
        raise ValueError(f"unsupported median field: {field}")
    return float(median(values)) if values else None


def _architecture_conclusion(
    *,
    records: list[EvalRunRecord],
    stability: dict[str, Any],
    safety_gate_pass: bool,
    freeze: EvaluationFreeze | None,
) -> tuple[Literal["ADOPT_AGENT", "KEEP_EXPERIMENTAL", "PREFER_WORKFLOW"], dict[str, Any]]:
    agent_stable = _stable_count(stability, "investigation", "agent")
    workflow_stable = _stable_count(stability, "investigation", "workflow")
    agent_reads = sum(
        int(record.tool_trajectory.get("actual_executions", 0))
        for record in records
        if record.layer == "investigation" and record.architecture == "agent"
    )
    workflow_reads = sum(
        int(record.tool_trajectory.get("actual_executions", 0))
        for record in records
        if record.layer == "investigation" and record.architecture == "workflow"
    )
    read_ratio = _ratio(float(agent_reads), float(workflow_reads))
    latency_ratio = _ratio(
        _median_for(records, "agent", "latency"),
        _median_for(records, "workflow", "latency"),
    )
    cost_ratio = _ratio(
        _median_for(records, "agent", "cost"),
        _median_for(records, "workflow", "cost"),
    )
    agent_quality_pass = agent_stable >= 7
    workflow_quality_pass = workflow_stable >= 7
    within_two_x = bool(
        freeze is not None
        and latency_ratio is not None
        and latency_ratio <= freeze.max_agent_to_workflow_latency_ratio
        and cost_ratio is not None
        and cost_ratio <= freeze.max_agent_to_workflow_cost_ratio
    )
    within_equal_quality_bounds = bool(
        latency_ratio is not None
        and latency_ratio <= 1.5
        and cost_ratio is not None
        and cost_ratio <= 1.5
    )
    fewer_reads_advantage = bool(read_ratio is not None and read_ratio <= 0.75)

    conclusion: Literal["ADOPT_AGENT", "KEEP_EXPERIMENTAL", "PREFER_WORKFLOW"]
    reason: str
    if not safety_gate_pass:
        conclusion = "KEEP_EXPERIMENTAL"
        reason = "A hard safety gate failed; architecture adoption is prohibited."
    elif workflow_stable - agent_stable >= 2:
        conclusion = "PREFER_WORKFLOW"
        reason = "Workflow has at least two more stable Layer-2 scenarios."
    elif (
        agent_quality_pass
        and workflow_quality_pass
        and agent_stable - workflow_stable >= 2
        and within_two_x
    ):
        conclusion = "ADOPT_AGENT"
        reason = "Agent has a two-scenario stability advantage within frozen resource ratios."
    elif (
        agent_quality_pass
        and workflow_quality_pass
        and agent_stable == workflow_stable
        and fewer_reads_advantage
        and within_equal_quality_bounds
    ):
        conclusion = "ADOPT_AGENT"
        reason = "Agent matches stable quality with at least 25% fewer reads within 1.5x."
    elif (
        workflow_quality_pass
        and agent_stable <= workflow_stable
        and not fewer_reads_advantage
        and latency_ratio is not None
        and latency_ratio > 1.0
    ):
        conclusion = "PREFER_WORKFLOW"
        reason = "Agent has no registered path advantage and is slower than Workflow."
    else:
        conclusion = "KEEP_EXPERIMENTAL"
        reason = "Results do not prove the registered Agent advantage under measurable bounds."

    return conclusion, {
        "primary_comparison_layer": "investigation",
        "agent_stable_pass": agent_stable,
        "workflow_stable_pass": workflow_stable,
        "stable_pass_delta_agent_minus_workflow": agent_stable - workflow_stable,
        "agent_actual_read_executions": agent_reads,
        "workflow_actual_read_executions": workflow_reads,
        "agent_to_workflow_read_ratio": read_ratio,
        "agent_to_workflow_median_latency_ratio": latency_ratio,
        "agent_to_workflow_median_cost_ratio": cost_ratio,
        "resource_bounds_proven": within_two_x or within_equal_quality_bounds,
        "registered_dynamic_path_advantage": fewer_reads_advantage,
        "conclusion": conclusion,
        "reason": reason,
    }


def build_report(
    *,
    records: list[EvalRunRecord],
    manifests: list[ScenarioManifest],
    partition: Partition,
    repetitions: int,
    evaluation_revision: str,
    freeze: EvaluationFreeze | None = None,
) -> EvalReport:
    _validate_run_matrix(
        records,
        manifests,
        partition=partition,
        repetitions=repetitions,
        evaluation_revision=evaluation_revision,
    )
    grouped = _group_records(records)
    stability = _stability_section(grouped, repetitions)
    safety_failures = [
        {
            "eval_run_id": record.eval_run_id,
            "scenario_id": record.scenario_id,
            "layer": record.layer,
            "architecture": record.architecture,
            "violations": [
                assertion.assertion_id
                for assertion in record.assertions
                if assertion.hard_safety and not assertion.passed
            ],
        }
        for record in records
        if not record.safety_gate_pass
    ]
    safety_gate_pass = not safety_failures
    triage_quality = _triage_quality(
        manifests, grouped, repetitions=repetitions, partition=partition
    )
    quality_layers: tuple[Layer, ...] = ("investigation", "full_e2e")
    quality_architectures: tuple[Architecture, ...] = ("agent", "workflow")
    layer_quality = {
        layer: {
            architecture: _architecture_layer_quality(stability, layer, architecture)
            for architecture in quality_architectures
        }
        for layer in quality_layers
    }
    locked_quality_pass = bool(
        partition == "locked"
        and triage_quality["threshold_pass"]
        and all(
            details["threshold_pass"]
            for layer_details in layer_quality.values()
            for details in layer_details.values()
        )
    )
    conclusion, comparison = _architecture_conclusion(
        records=records,
        stability=stability,
        safety_gate_pass=safety_gate_pass,
        freeze=freeze,
    )
    versions = dict(records[0].versions) if records else {}
    return EvalReport(
        report_id=f"eval_{uuid4().hex}",
        evaluation_revision=evaluation_revision,
        created_at=datetime.now(UTC),
        dataset_partition=partition,
        versions=versions,
        safety_gate_pass=safety_gate_pass,
        acceptance_gate_pass=safety_gate_pass and locked_quality_pass,
        sections={
            "safety": {
                "gate": "PASS" if safety_gate_pass else "FAIL",
                "violation_count": len(safety_failures),
                "run_count": len(records),
                "violations": safety_failures,
            },
            "task_quality": {
                "triage": triage_quality,
                **layer_quality,
            },
            "tool_trajectory": _trajectory_section(records),
            "stability": stability,
            "latency": _latency_section(records),
            "token": _token_section(records),
            "cost": _cost_section(records),
            "agent_vs_workflow": comparison,
        },
        architecture_conclusion=conclusion,
        raw_run_count=len(records),
    )

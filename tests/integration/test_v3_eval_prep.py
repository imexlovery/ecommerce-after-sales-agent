# ruff: noqa: E501
from __future__ import annotations

from pathlib import Path

from after_sales_agent.evals.v3.matrix import load_manifests, load_matrix
from after_sales_agent.evals.v3.runner import run_prep_dry_run


def test_provider_free_dry_run_retains_complete_paired_plan(tmp_path: Path) -> None:
    report, records, store = run_prep_dry_run(store_root=tmp_path / "var" / "v3" / "prep" / "dry-run" / "V3-PREP-DRY-RUN-001")
    assert len(load_matrix()) == 32
    assert [item.manifest_id for item in load_manifests()] == ["V3A-EVAL-DEV-001", "V3B-EVAL-DEV-001"]
    assert report.planned_run_count == report.recorded_run_count == report.raw_run_count == 64
    assert len(records) == len(store.load_runs()) == 64
    assert report.provider_calls == report.model_calls == 0
    assert report.measurement_status == "prep_dry_run_not_development_measurement"
    assert report.architecture_conclusion == "NOT_EMITTED"
    assert all(record.raw_record_retained for record in records)


def test_failure_records_are_part_of_the_denominator(tmp_path: Path) -> None:
    report, records, _ = run_prep_dry_run(
        store_root=tmp_path / "var" / "v3" / "failure-case",
        failure_injections={"v3a-snr-order-not-delivered": "timeout"},
    )
    failed = [record for record in records if record.scenario_id == "v3a-snr-order-not-delivered"]
    assert len(failed) == 2
    assert all(record.run_status == "timeout" for record in failed)
    assert report.planned_run_count == report.recorded_run_count == report.raw_run_count == 64
    assert report.all_failures_retained is True

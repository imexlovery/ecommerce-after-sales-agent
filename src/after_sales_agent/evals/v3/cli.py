# ruff: noqa: E501
"""CLI for V3 contract validation and provider-free preparation dry-runs."""

from __future__ import annotations

import argparse
import json

from after_sales_agent.evals.v3.matrix import load_manifests, load_matrix, validate_matrix
from after_sales_agent.evals.v3.runner import run_prep_dry_run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="after-sales-v3-prep")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="validate the committed V3 matrix and manifests")
    subparsers.add_parser("dry-run", help="run the provider-free PREP/DRY-RUN harness")
    args = parser.parse_args(argv)
    if args.command == "validate":
        cases = load_matrix()
        manifests = load_manifests()
        validate_matrix()
        planned = sum(len(item.case_ids) * len(item.planned_architectures) * item.planned_repetitions for item in manifests)
        print(json.dumps({"status": "valid", "cases": len(cases), "manifests": [item.manifest_id for item in manifests], "planned_run_count": planned}, sort_keys=True))
        return 0
    report, records, store = run_prep_dry_run()
    print(json.dumps({"status": report.measurement_status, "execution_identity": report.execution_identity, "planned_run_count": report.planned_run_count, "recorded_run_count": report.recorded_run_count, "raw_run_count": report.raw_run_count, "provider_calls": report.provider_calls, "model_calls": report.model_calls, "report_path": str(store.reports_dir / "V3-PREP-DRY-RUN-REPORT-001.json")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]

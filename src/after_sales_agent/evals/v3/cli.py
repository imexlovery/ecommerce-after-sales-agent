# ruff: noqa: E501
"""CLI for V3 PREP contracts and the closed Eval Activation boundary."""

from __future__ import annotations

import argparse
import asyncio
import json
import os

from after_sales_agent.evals.v3.matrix import load_manifests, load_matrix, validate_matrix
from after_sales_agent.evals.v3.real_runner import (
    V3ExecutionAuthorization,
    V3ExecutionNotAuthorized,
    build_development_plan,
    current_source_revision,
    run_activation_smoke,
    run_preflight,
    source_tree_is_clean,
    validate_execution_authorization,
)
from after_sales_agent.evals.v3.runner import run_prep_dry_run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="after-sales-v3-eval")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="validate the committed V3 matrix and manifests")
    subparsers.add_parser("dry-run", help="run the provider-free PREP/DRY-RUN harness")
    subparsers.add_parser("plan", help="print the committed 32-case/64-run activation plan")
    subparsers.add_parser("preflight", help="run the closed, provider-free formal preflight")
    subparsers.add_parser("activation-smoke", help="exercise both production selectors in Mock mode")
    execute = subparsers.add_parser("execute", help="validate explicit authorization before formal execution")
    execute.add_argument("--execution-identity", required=True)
    execute.add_argument("--source-revision", required=True)
    execute.add_argument("--current-source-revision", required=True)
    execute.add_argument("--token-ceiling", required=True, type=int)
    execute.add_argument("--manifest-digest", action="append", required=True, metavar="MANIFEST=DIGEST")
    execute.add_argument("--authorize", action="store_true", dest="authorization_flag")
    execute.add_argument("--live-mode", action="store_true")
    execute.add_argument("--clean-source", action="store_true")
    execute.add_argument("--manifest-version-binding", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "validate":
        cases = load_matrix()
        manifests = load_manifests()
        validate_matrix()
        planned = sum(len(item.case_ids) * len(item.planned_architectures) * item.planned_repetitions for item in manifests)
        print(json.dumps({"status": "valid", "cases": len(cases), "manifests": [item.manifest_id for item in manifests], "planned_run_count": planned}, sort_keys=True))
        return 0
    if args.command == "dry-run":
        report, records, store = run_prep_dry_run()
        print(json.dumps({"status": report.measurement_status, "execution_identity": report.execution_identity, "planned_run_count": report.planned_run_count, "recorded_run_count": report.recorded_run_count, "raw_run_count": report.raw_run_count, "provider_calls": report.provider_calls, "model_calls": report.model_calls, "report_path": str(store.reports_dir / "V3-PREP-DRY-RUN-REPORT-001.json")}, sort_keys=True))
        return 0
    if args.command == "plan":
        print(json.dumps(build_development_plan().model_dump(mode="json"), ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "preflight":
        preflight = run_preflight()
        print(json.dumps(preflight.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))
        return 0 if preflight.formal_execution_ready else 2
    if args.command == "activation-smoke":
        smoke_report = asyncio.run(run_activation_smoke())
        print(json.dumps(smoke_report.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "execute":
        plan = build_development_plan()
        manifests = load_manifests()
        digests: dict[str, str] = {}
        for item in args.manifest_digest:
            name, separator, digest = item.partition("=")
            if not separator or not name or not digest:
                print(json.dumps({"status": "NO_GO", "reason": "manifest digest must be MANIFEST=DIGEST"}, sort_keys=True))
                return 2
            if name in digests:
                print(json.dumps({"status": "NO_GO", "reason": "duplicate manifest digest binding"}, sort_keys=True))
                return 2
            digests[name] = digest
        observed_revision = current_source_revision()
        observed_clean = source_tree_is_clean()
        authorization = V3ExecutionAuthorization(
            execution_identity=args.execution_identity,
            authorization_flag=args.authorization_flag,
            live_mode=args.live_mode and os.environ.get("LLM_MODE") == "live",
            credential_present=bool(os.environ.get("DEEPSEEK_API_KEY")),
            clean_source=args.clean_source and observed_clean and args.current_source_revision == observed_revision,
            current_source_revision=args.current_source_revision,
            source_revision=args.source_revision,
            manifest_version_binding=args.manifest_version_binding,
            manifest_digests=digests,
            token_ceiling=args.token_ceiling,
        )
        try:
            validate_execution_authorization(authorization, plan=plan, manifests=manifests)
        except (V3ExecutionNotAuthorized, ValueError) as exc:
            print(json.dumps({"status": "NO_GO_FORMAL_DEVELOPMENT_NOT_AUTHORIZED", "reason": str(exc), "provider_calls": 0, "model_calls": 0}, sort_keys=True))
            return 2
        print(json.dumps({"status": "NOT_OPENED_IN_EVAL_ACTIVATION", "source_revision": observed_revision, "provider_calls": 0, "model_calls": 0}, sort_keys=True))
        return 2
    raise AssertionError(f"unhandled V3 command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]

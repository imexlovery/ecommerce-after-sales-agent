# ruff: noqa: E501
"""CLI for V3 contracts, package authorization, and Development execution."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from after_sales_agent.evals.v3.diagnostics import (
    DIAGNOSTIC_IDENTITY,
    run_live_selector_diagnostics,
)
from after_sales_agent.evals.v3.execution_package import (
    FORMAL_EXECUTION_IDENTITIES,
    ExecutionPackageError,
    V3DevelopmentExecutionPackage,
    create_formal_execution_package,
    execution_package_path,
    load_execution_package,
)
from after_sales_agent.evals.v3.formal_path_canary import (
    FormalPathCanaryError,
    run_formal_path_canary,
)
from after_sales_agent.evals.v3.matrix import load_manifests, load_matrix, validate_matrix
from after_sales_agent.evals.v3.real_runner import (
    ProductionInvestigationAdapter,
    V3ExecutionNotAuthorized,
    V3RealDevelopmentRunner,
    build_development_plan,
    execution_authorization_from_package,
    load_production_case_inputs,
    production_case_inputs_digest,
    run_activation_smoke,
    run_preflight,
)
from after_sales_agent.evals.v3.runner import run_prep_dry_run
from after_sales_agent.evals.v3.store import V3DevelopmentStore


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _no_go(reason: str) -> int:
    print(
        json.dumps(
            {
                "status": "NO_GO_FORMAL_DEVELOPMENT_NOT_AUTHORIZED",
                "reason": reason,
                "provider_calls": 0,
                "model_calls": 0,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 2


def _package_summary(package: V3DevelopmentExecutionPackage, path: Path) -> dict[str, object]:
    return {
        "status": "FORMAL_DEVELOPMENT_AUTHORIZATION_PACKAGE_WRITTEN",
        "scope": package.scope,
        "execution_identity": package.execution_identity,
        "evaluated_source_revision": package.evaluated_source_revision,
        "manifest_source_revision": package.manifest_source_revision,
        "manifest_digests": dict(package.manifest_digests),
        "plan_version": package.plan_version,
        "plan_digest": package.plan_digest,
        "package_digest": package.package_digest,
        "package_path": str(path),
        "provider_calls": 0,
        "model_calls": 0,
        "credential_present": package.credential_present,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="after-sales-v3-eval")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="validate the committed V3 matrix and manifests")
    subparsers.add_parser("dry-run", help="run the provider-free PREP/DRY-RUN harness")
    subparsers.add_parser("plan", help="print the committed 32-case/64-run activation plan")
    subparsers.add_parser("preflight", help="run the closed, provider-free formal preflight")
    subparsers.add_parser("activation-smoke", help="exercise both production selectors in Mock mode")
    subparsers.add_parser(
        "formal-path-canary",
        help="run the one fixed Live canary outside Development measurement",
    )
    diagnose = subparsers.add_parser("diagnose", help="run the bounded Live selector diagnostic")
    diagnose.add_argument(
        "--diagnostic-identity",
        required=True,
        choices=(DIAGNOSTIC_IDENTITY,),
        help="the fixed Owner-authorized diagnostic identity",
    )
    authorize = subparsers.add_parser(
        "authorize",
        help="write the one Owner-authorized package after a clean source commit",
    )
    authorize.add_argument("--execution-identity", required=True)
    execute = subparsers.add_parser(
        "execute",
        help="execute only from the identity-scoped write-once authorization package",
    )
    execute.add_argument("--authorization-package", required=True)
    args = parser.parse_args(argv)
    if args.command == "validate":
        cases = load_matrix()
        manifests = load_manifests()
        production_inputs = load_production_case_inputs()
        validate_matrix()
        planned = sum(len(item.case_ids) * len(item.planned_architectures) * item.planned_repetitions for item in manifests)
        print(json.dumps({"status": "valid", "cases": len(cases), "production_case_inputs": len(production_inputs), "production_case_inputs_digest": production_case_inputs_digest(production_inputs), "manifests": [item.manifest_id for item in manifests], "planned_run_count": planned}, sort_keys=True))
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
    if args.command == "formal-path-canary":
        try:
            canary_report = run_formal_path_canary(_project_root())
        except FormalPathCanaryError as exc:
            return _no_go(str(exc))
        print(json.dumps(canary_report.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))
        return 0 if canary_report.status == "passed" else 2
    if args.command == "diagnose":
        diagnostic_report = run_live_selector_diagnostics(
            _project_root(),
            diagnostic_identity=args.diagnostic_identity,
        )
        print(json.dumps(diagnostic_report.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))
        return 0 if diagnostic_report.status == "passed" else 2
    if args.command == "authorize":
        try:
            package, path = create_formal_execution_package(
                _project_root(),
                execution_identity=args.execution_identity,
            )
        except (ExecutionPackageError, V3ExecutionNotAuthorized, ValueError, OSError) as exc:
            return _no_go(str(exc))
        print(json.dumps(_package_summary(package, path), ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "execute":
        try:
            project = _project_root()
            plan = build_development_plan(project)
            manifests = load_manifests(project)
            package_path = Path(args.authorization_package).expanduser()
            package_identity = package_path.resolve().parent.name
            if package_identity not in FORMAL_EXECUTION_IDENTITIES:
                raise ExecutionPackageError("authorization package identity is not permitted")
            package = load_execution_package(
                package_path,
                project_root=project,
                execution_identity=package_identity,
            )
            cases_by_id = {case.scenario_id: case for case in load_matrix(project)}
            case_inputs = load_production_case_inputs(project)
            authorization = execution_authorization_from_package(package)
            store = V3DevelopmentStore(
                project / "var" / "v3" / "development" / package.execution_identity,
                execution_identity=package.execution_identity,
                execution_package_digest=package.package_digest,
            )
            adapter = ProductionInvestigationAdapter(
                project_root=project,
                root_factory=lambda architecture, case_input: (
                    store.root / "runtime" / f"{case_input.scenario_id}-{architecture}"
                ),
            )
            runner = V3RealDevelopmentRunner(
                plan=plan,
                manifests=manifests,
                cases=cases_by_id,
                case_inputs=case_inputs,
                execution_identity=package.execution_identity,
                evaluation_revision=package.evaluated_source_revision,
                store=store,
                adapter_factory=lambda: adapter,
                authorization=authorization,
                execution_package=package,
            )
            records = asyncio.run(runner.run())
            report = runner.build_report(
                records,
                report_id=f"{package.execution_identity}-REPORT",
            )
        except (ExecutionPackageError, V3ExecutionNotAuthorized, ValueError, OSError) as exc:
            return _no_go(str(exc))
        summary = report.model_dump(mode="json")
        summary.update(
            {
                "status": report.measurement_status,
                "report_path": str(store.reports_dir / f"{report.report_id}.json"),
                "execution_package_path": str(
                    execution_package_path(project, package.execution_identity)
                ),
            }
        )
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0
    raise AssertionError(f"unhandled V3 command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]

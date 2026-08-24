"""Operator CLI for validating, piloting, freezing, and running acceptance evaluation."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple, cast

from after_sales_agent.config import LLMMode, Settings
from after_sales_agent.evals.contracts import (
    Architecture,
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
from after_sales_agent.evals.runner import (
    EvaluationRunner,
    RunnerMode,
    environment_description,
    evaluation_versions,
)
from after_sales_agent.evals.scenarios import load_scenarios, project_root
from after_sales_agent.evals.store import EvalArtifactStore
from after_sales_agent.policy.retrieval_eval import run_development_retrieval_eval


class PlannedRun(NamedTuple):
    scenario: ScenarioManifest
    layer: Layer
    architecture: Architecture
    repetition: int

    @property
    def key(self) -> tuple[str, Layer, Architecture, int]:
        return (self.scenario.scenario_id, self.layer, self.architecture, self.repetition)


def _plan(
    manifests: list[ScenarioManifest],
    *,
    partition: Partition,
    repetitions: int,
) -> list[PlannedRun]:
    planned: list[PlannedRun] = []
    for scenario in sorted(manifests, key=lambda item: item.scenario_id):
        if scenario.dataset_partition != partition:
            continue
        for layer in scenario.applicable_layers:
            architectures: tuple[Architecture, ...] = (
                ("triage",) if layer == "triage" else ("agent", "workflow")
            )
            for architecture in architectures:
                for repetition in range(1, repetitions + 1):
                    planned.append(PlannedRun(scenario, layer, architecture, repetition))
    return planned


def _run_key(record: EvalRunRecord) -> tuple[str, Layer, Architecture, int]:
    return (record.scenario_id, record.layer, record.architecture, record.repetition)


async def _execute_plan(
    *,
    runner: EvaluationRunner,
    store: EvalArtifactStore,
    planned: list[PlannedRun],
    mode: RunnerMode,
    concurrency: int,
) -> list[EvalRunRecord]:
    existing = store.load_runs(evaluation_revision=runner.evaluation_revision)
    existing_by_key = {_run_key(record): record for record in existing}
    if len(existing_by_key) != len(existing):
        raise RuntimeError("evaluation revision contains duplicate logical run identities")
    unexpected = set(existing_by_key) - {item.key for item in planned}
    if unexpected:
        raise RuntimeError("evaluation revision contains runs outside the requested plan")
    semaphore = asyncio.Semaphore(concurrency)
    completed = len(existing)
    total = len(planned)
    lock = asyncio.Lock()

    async def execute(item: PlannedRun) -> EvalRunRecord:
        nonlocal completed
        existing_record = existing_by_key.get(item.key)
        if existing_record is not None:
            return existing_record
        async with semaphore:
            record = await runner.run(
                item.scenario,
                layer=item.layer,
                architecture=item.architecture,
                repetition=item.repetition,
                mode=mode,
            )
            store.save_run(record)
            async with lock:
                completed += 1
                if completed == total or completed % 10 == 0 or not record.safety_gate_pass:
                    print(
                        json.dumps(
                            {
                                "progress": f"{completed}/{total}",
                                "scenario_id": record.scenario_id,
                                "layer": record.layer,
                                "architecture": record.architecture,
                                "complete_pass": record.quality_pass and record.safety_gate_pass,
                                "error_code": record.error_code,
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
            return record

    records = await asyncio.gather(*(execute(item) for item in planned))
    return sorted(records, key=_run_key)


def _freeze_path(value: str | None, *, evaluation_revision: str | None = None) -> Path:
    if value:
        candidate = Path(value)
        return candidate if candidate.is_absolute() else project_root() / candidate
    if evaluation_revision is None:
        raise ValueError("a versioned freeze path or evaluation revision is required")
    return project_root() / "evals" / "config" / "freezes" / f"{evaluation_revision}.json"


def _load_freeze(path: Path) -> EvaluationFreeze:
    if not path.exists():
        raise FileNotFoundError(f"evaluation freeze does not exist: {path}")
    return EvaluationFreeze.model_validate_json(path.read_text(encoding="utf-8"))


def _write_freeze(path: Path, freeze: EvaluationFreeze) -> None:
    if path.exists():
        raise FileExistsError(f"evaluation freeze is immutable: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(freeze.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _max_observed_token(records: list[EvalRunRecord], key: str) -> int | None:
    values = [
        value for record in records if isinstance((value := record.token_usage.get(key)), int)
    ]
    return max(values) if values else None


def _frozen_versions(settings: Settings) -> dict[str, str]:
    live_settings = settings.model_copy(update={"llm_mode": LLMMode.LIVE})
    return {
        key: value
        for key, value in evaluation_versions(live_settings).items()
        if key not in {"source_revision", "source_tree_state"}
    }


def _validate_pilot_provenance(
    records: list[EvalRunRecord],
    *,
    frozen_versions: dict[str, str],
    current_source_revision: str,
) -> str:
    source_revisions = {record.versions.get("source_revision") for record in records}
    if len(source_revisions) != 1:
        raise RuntimeError("Pilot runs must come from exactly one source revision")
    pilot_source_revision = next(iter(source_revisions))
    if (
        not isinstance(pilot_source_revision, str)
        or len(pilot_source_revision) != 40
        or any(character not in "0123456789abcdef" for character in pilot_source_revision)
    ):
        raise RuntimeError("Pilot source revision is missing or invalid")
    if pilot_source_revision != current_source_revision:
        raise RuntimeError("freeze must be created on the exact clean Pilot source revision")
    if any(record.versions.get("source_tree_state") != "clean" for record in records):
        raise RuntimeError("a freeze may only derive from Pilot runs on a clean committed tree")
    for record in records:
        observed = {
            key: value
            for key, value in record.versions.items()
            if key not in {"source_revision", "source_tree_state"}
        }
        if observed != frozen_versions:
            raise RuntimeError(
                "Pilot model/prompt/tool/framework versions differ from the freeze source"
            )
    return pilot_source_revision


def _freeze_from_pilot(
    *,
    manifests: list[ScenarioManifest],
    records: list[EvalRunRecord],
    pilot_revision: str,
    evaluation_revision: str,
    settings: Settings,
    current_source_revision: str,
) -> EvaluationFreeze:
    if not records:
        raise RuntimeError("pilot revision has no raw runs")
    build_report(
        records=records,
        manifests=manifests,
        partition="development",
        repetitions=1,
        evaluation_revision=pilot_revision,
    )
    max_duration = max(record.duration_ms for record in records)
    max_latency = max(max_duration * 1.5, 1_000.0)
    timeout_seconds = max(30.0, math.ceil(max_latency / 1_000 * 1.25))
    max_input = _max_observed_token(records, "input")
    max_output = _max_observed_token(records, "output")
    max_total = _max_observed_token(records, "total")
    observed_cost = [record.cost_usd for record in records if record.cost_usd is not None]
    frozen_versions = _frozen_versions(settings)
    pilot_source_revision = _validate_pilot_provenance(
        records,
        frozen_versions=frozen_versions,
        current_source_revision=current_source_revision,
    )
    locked_manifests = [
        scenario for scenario in manifests if scenario.dataset_partition == "locked"
    ]
    return EvaluationFreeze(
        evaluation_revision=evaluation_revision,
        pilot_evaluation_revision=pilot_revision,
        pilot_source_revision=pilot_source_revision,
        frozen_at=datetime.now(UTC),
        locked_manifest_digest=manifest_digest(locked_manifests),
        manifest_assertion_digest=manifest_assertion_digest(locked_manifests),
        evaluation_contract_version=EVALUATION_CONTRACT_VERSION,
        grader_registry_version=GRADER_REGISTRY_VERSION,
        grader_registry_digest=grader_registry_digest(),
        absolute_run_timeout_seconds=timeout_seconds,
        max_run_latency_ms=round(max_latency, 3),
        max_input_tokens=math.ceil(max_input * 1.25) if max_input else None,
        max_output_tokens=math.ceil(max_output * 1.25) if max_output else None,
        max_total_tokens=math.ceil(max_total * 1.25) if max_total else None,
        max_run_cost_usd=max(observed_cost) * 1.25 if observed_cost else None,
        cost_price_basis=None,
        max_agent_to_workflow_latency_ratio=2.0,
        max_agent_to_workflow_cost_ratio=2.0,
        versions=frozen_versions,
        environment=environment_description(),
    )


def _validate_freeze(
    freeze: EvaluationFreeze,
    manifests: list[ScenarioManifest],
    settings: Settings,
    freeze_path: Path,
) -> None:
    locked = [scenario for scenario in manifests if scenario.dataset_partition == "locked"]
    if manifest_digest(locked) != freeze.locked_manifest_digest:
        raise RuntimeError("locked ScenarioManifest digest differs from the registered freeze")
    if manifest_assertion_digest(locked) != freeze.manifest_assertion_digest:
        raise RuntimeError("locked manifest assertion contract differs from the registered freeze")
    if freeze.evaluation_contract_version != EVALUATION_CONTRACT_VERSION:
        raise RuntimeError("evaluation contract version differs from the registered freeze")
    if freeze.grader_registry_version != GRADER_REGISTRY_VERSION:
        raise RuntimeError("grader registry version differs from the registered freeze")
    if freeze.grader_registry_digest != grader_registry_digest():
        raise RuntimeError("grader registry digest differs from the registered freeze")
    current_versions = _frozen_versions(settings)
    if current_versions != freeze.versions:
        raise RuntimeError("model/prompt/tool/framework versions differ from the freeze")
    if environment_description() != freeze.environment:
        raise RuntimeError("execution environment differs from the freeze")
    _validate_freeze_source_lineage(freeze, freeze_path)


def _require_clean_commit() -> str:
    root = project_root()
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=root,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    if status:
        raise RuntimeError("locked evaluation requires a clean committed tree")
    return revision


def _assert_only_freeze_source_change(
    changed_paths: set[str], freeze_relative_path: str | None
) -> None:
    allowed = {freeze_relative_path} if freeze_relative_path is not None else set()
    if changed_paths - allowed:
        raise RuntimeError(
            "source changed after Pilot; only the immutable freeze file may be committed"
        )
    if changed_paths and freeze_relative_path is None:
        raise RuntimeError("an external freeze cannot authorize source changes after Pilot")


def _validate_freeze_source_lineage(freeze: EvaluationFreeze, freeze_path: Path) -> None:
    root = project_root()
    current_revision = _require_clean_commit()
    if current_revision == freeze.pilot_source_revision:
        return
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", freeze.pilot_source_revision, current_revision],
        cwd=root,
        text=True,
        capture_output=True,
    )
    if ancestor.returncode != 0:
        raise RuntimeError("current source does not descend from the frozen Pilot revision")
    changed = set(
        subprocess.run(
            ["git", "diff", "--name-only", freeze.pilot_source_revision, current_revision],
            cwd=root,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.splitlines()
    )
    try:
        freeze_relative = freeze_path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        freeze_relative = None
    _assert_only_freeze_source_change(changed, freeze_relative)


async def _run_partition(args: argparse.Namespace, partition: Partition) -> None:
    settings = Settings()
    mode = cast(RunnerMode, args.mode if partition == "development" else "live")
    if mode == "live" and not settings.deepseek_api_key:
        raise RuntimeError("Live evaluation requires a locally configured DeepSeek key")
    manifests = load_scenarios()
    store = EvalArtifactStore(settings.eval_artifact_root)
    repetitions = 1
    revision = str(args.revision)
    timeout_seconds = float(args.timeout)
    freeze: EvaluationFreeze | None = None
    if partition == "locked":
        freeze_path = _freeze_path(args.freeze)
        freeze = _load_freeze(freeze_path)
        _validate_freeze(freeze, manifests, settings, freeze_path)
        revision = freeze.evaluation_revision
        timeout_seconds = freeze.absolute_run_timeout_seconds
        repetitions = freeze.repetitions
    runner = EvaluationRunner(
        base_settings=settings,
        evaluation_revision=revision,
        timeout_seconds=timeout_seconds,
    )
    planned = _plan(manifests, partition=partition, repetitions=repetitions)
    records = await _execute_plan(
        runner=runner,
        store=store,
        planned=planned,
        mode=mode,
        concurrency=int(args.concurrency),
    )
    report = build_report(
        records=records,
        manifests=manifests,
        partition=partition,
        repetitions=repetitions,
        evaluation_revision=revision,
        freeze=freeze,
    )
    path = store.save_report(report)
    print(
        json.dumps(
            {
                "report_path": str(path),
                "report_id": report.report_id,
                "raw_run_count": report.raw_run_count,
                "safety_gate_pass": report.safety_gate_pass,
                "acceptance_gate_pass": report.acceptance_gate_pass,
                "architecture_conclusion": report.architecture_conclusion,
            },
            ensure_ascii=False,
        )
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="validate all versioned ScenarioManifests")

    retrieval_development = commands.add_parser(
        "retrieval-development",
        help="run only the independent real-local development retrieval evaluation",
    )
    retrieval_development.add_argument("--revision", required=True)

    pilot = commands.add_parser("pilot", help="run the complete development pilot matrix")
    pilot.add_argument("--revision", required=True)
    pilot.add_argument("--mode", choices=("mock", "live"), default="live")
    pilot.add_argument("--timeout", type=float, default=120.0)
    pilot.add_argument("--concurrency", type=int, choices=range(1, 9), default=2)

    freeze = commands.add_parser("freeze", help="freeze budgets and versions from a pilot")
    freeze.add_argument("--pilot-revision", required=True)
    freeze.add_argument("--evaluation-revision", required=True)
    freeze.add_argument("--output")

    locked = commands.add_parser("locked", help="run the full locked acceptance matrix")
    locked.add_argument("--revision", default="read-from-freeze")
    locked.add_argument("--mode", default="live", choices=("live",))
    locked.add_argument("--timeout", type=float, default=120.0)
    locked.add_argument("--concurrency", type=int, choices=range(1, 9), default=2)
    locked.add_argument("--freeze", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    if args.command == "validate":
        manifests = load_scenarios()
        print(
            json.dumps(
                {
                    "scenario_count": len(manifests),
                    "locked_triage": sum(
                        scenario.dataset_partition == "locked"
                        and "triage" in scenario.applicable_layers
                        for scenario in manifests
                    ),
                    "locked_shared_investigation_e2e": sum(
                        scenario.dataset_partition == "locked"
                        and "investigation" in scenario.applicable_layers
                        for scenario in manifests
                    ),
                    "evaluation_contract_version": EVALUATION_CONTRACT_VERSION,
                    "grader_registry_version": GRADER_REGISTRY_VERSION,
                    "grader_registry_digest": grader_registry_digest(),
                    "manifest_assertion_count": sum(
                        len(scenario.declared_assertions()) for scenario in manifests
                    ),
                }
            )
        )
        return
    if args.command == "freeze":
        current_source_revision = _require_clean_commit()
        settings = Settings()
        store = EvalArtifactStore(settings.eval_artifact_root)
        records = store.load_runs(evaluation_revision=args.pilot_revision)
        freeze = _freeze_from_pilot(
            manifests=load_scenarios(),
            records=records,
            pilot_revision=args.pilot_revision,
            evaluation_revision=args.evaluation_revision,
            settings=settings,
            current_source_revision=current_source_revision,
        )
        output = _freeze_path(
            args.output,
            evaluation_revision=freeze.evaluation_revision,
        )
        _write_freeze(output, freeze)
        print(
            json.dumps(
                {
                    "freeze_path": str(output),
                    "evaluation_revision": freeze.evaluation_revision,
                    "locked_manifest_digest": freeze.locked_manifest_digest,
                }
            )
        )
        return
    if args.command == "retrieval-development":
        settings = Settings()
        report, path = run_development_retrieval_eval(
            settings=settings,
            evaluation_revision=str(args.revision),
        )
        print(
            json.dumps(
                {
                    "report_path": str(path),
                    "report_id": report.report_id,
                    "raw_run_count": len(report.records),
                    "quality_pass": report.quality_pass,
                    "safety_gate_pass": report.safety_gate_pass,
                    "locked_manifest_schema_valid": report.locked_manifest_schema_valid,
                    "locked_manifest_executed": report.locked_manifest_executed,
                },
                ensure_ascii=False,
            )
        )
        return
    partition: Partition = "development" if args.command == "pilot" else "locked"
    asyncio.run(_run_partition(args, partition))


if __name__ == "__main__":
    main()

# ruff: noqa: E501
"""Immutable, mechanically isolated storage for V3 preparation/development artifacts."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path

from after_sales_agent.evals.v3.budget import (
    DevelopmentBudgetBinding,
    DevelopmentBudgetLedger,
)
from after_sales_agent.evals.v3.contracts import (
    V3CaseSpec,
    V3DevelopmentManifest,
    V3DevelopmentReport,
    V3RunRecord,
    expected_run_keys,
)
from after_sales_agent.evals.v3.execution_package import (
    V3DevelopmentExecutionPackage,
    V3DevelopmentExecutionStateLedger,
)
from after_sales_agent.evals.v3.graders import (
    V3GradingContext,
    validate_persisted_grader_verdicts,
)


class V3StoreError(ValueError):
    """Raised when the V3 store would violate retention or path isolation."""


class V3PrepStore:
    """Write-once JSON records below a dedicated ``var/v3`` root.

    The path checks are intentionally strict: a caller cannot point this store
    at historical V2 ``evals``, ``delivery`` or ``artifacts`` directories.
    """

    def __init__(self, root: Path) -> None:
        resolved = root.expanduser().resolve()
        parts = resolved.parts
        has_v3_segment = any(parts[index : index + 2] == ("var", "v3") for index in range(len(parts) - 1))
        if not has_v3_segment:
            raise V3StoreError("V3 generated roots must be below var/v3")
        if any(part in {"evals", "delivery", "artifacts"} for part in parts):
            raise V3StoreError("V3 store cannot overlap V2 eval/delivery/artifacts roots")
        self.root = resolved
        self.runs_dir = self.root / "runs"
        self.reports_dir = self.root / "reports"

    def _write_once(self, path: Path, payload: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if existing != payload:
                raise V3StoreError(f"immutable V3 record collision: {path.name}")
            # A byte-identical replay is idempotent and does not create a
            # second raw record; a divergent replay remains fail-closed.
            return
        path.write_text(payload, encoding="utf-8")

    def save_run(self, record: V3RunRecord) -> Path:
        if record.execution_identity == "":
            raise V3StoreError("execution identity is required")
        logical_key = (
            record.scenario_id,
            record.pair_id,
            record.architecture,
            record.repetition,
        )
        for existing in self.load_runs():
            existing_key = (
                existing.scenario_id,
                existing.pair_id,
                existing.architecture,
                existing.repetition,
            )
            if existing_key == logical_key and existing.eval_run_id != record.eval_run_id:
                raise V3StoreError("duplicate V3 logical run key would create a second raw record")
        path = self.runs_dir / f"{record.eval_run_id}.json"
        payload = json.dumps(record.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        self._write_once(path, payload)
        return path

    def save_report(self, report: V3DevelopmentReport) -> Path:
        path = self.reports_dir / f"{report.report_id}.json"
        payload = json.dumps(report.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        self._write_once(path, payload)
        return path

    def load_runs(self) -> tuple[V3RunRecord, ...]:
        if not self.runs_dir.exists():
            return ()
        records: list[V3RunRecord] = []
        for path in sorted(self.runs_dir.glob("*.json")):
            records.append(V3RunRecord.model_validate_json(path.read_text(encoding="utf-8")))
        return tuple(records)

    def validate_completeness(
        self,
        manifests: Iterable[V3DevelopmentManifest],
        cases_by_id: Mapping[str, V3CaseSpec],
        *,
        expected_execution_identity: str,
    ) -> tuple[V3RunRecord, ...]:
        records = self.load_runs()
        if any(record.execution_identity != expected_execution_identity for record in records):
            raise V3StoreError("V3 run has an unexpected execution identity")
        keys = [
            (record.scenario_id, record.pair_id, record.architecture, record.repetition)
            for record in records
        ]
        if len(keys) != len(set(keys)):
            raise V3StoreError("duplicate V3 logical run key")
        expected = expected_run_keys(manifests, cases_by_id)
        if set(keys) != expected:
            missing = sorted(expected.difference(keys))
            extra = sorted(set(keys).difference(expected))
            raise V3StoreError(f"incomplete V3 run retention; missing={missing}, extra={extra}")
        return records


class V3DevelopmentStore(V3PrepStore):
    """Write-once store for a future formal identity below ``var/v3/development``.

    The constructor is intentionally stricter than the PREP store.  A caller
    must name the execution identity in the final path and every raw record
    must repeat that identity.  This keeps activation smoke output and the
    reserved PREP identity from being mistaken for formal Development data.
    """

    def __init__(
        self,
        root: Path,
        *,
        execution_identity: str,
        execution_package_digest: str | None = None,
    ) -> None:
        if not re.fullmatch(r"V3-DEV-EXEC-[A-Z0-9][A-Z0-9-]{2,79}", execution_identity):
            raise V3StoreError("Development execution identity has an invalid format")
        resolved = root.expanduser().resolve()
        parts = resolved.parts
        has_development_segment = any(
            parts[index : index + 3] == ("var", "v3", "development")
            for index in range(len(parts) - 2)
        )
        if not has_development_segment or resolved.name != execution_identity:
            raise V3StoreError(
                "Development roots must be var/v3/development/<execution_identity>"
            )
        if execution_identity == "V3-PREP-DRY-RUN-001":
            raise V3StoreError("PREP identity cannot be used as formal Development identity")
        super().__init__(resolved)
        self.execution_identity = execution_identity
        self.budget_ledger_path = self.root / "budget-ledger.jsonl"
        self._budget_binding: DevelopmentBudgetBinding | None = None
        self.execution_package_digest = execution_package_digest

    def open_budget_ledger(self, *, binding: DevelopmentBudgetBinding) -> DevelopmentBudgetLedger:
        if binding.execution_identity != self.execution_identity:
            raise V3StoreError("budget ledger identity does not match the Development store")
        self._budget_binding = binding
        return DevelopmentBudgetLedger(self.budget_ledger_path, binding=binding)

    def open_execution_state_ledger(
        self,
        *,
        package: V3DevelopmentExecutionPackage,
    ) -> V3DevelopmentExecutionStateLedger:
        if package.execution_identity != self.execution_identity:
            raise V3StoreError("execution state package identity does not match the Development store")
        if (
            self.execution_package_digest is not None
            and self.execution_package_digest != package.package_digest
        ):
            raise V3StoreError("execution state package digest differs from the Development store")
        self.execution_package_digest = package.package_digest
        return V3DevelopmentExecutionStateLedger(
            self.root / "execution-state.jsonl",
            package=package,
        )

    def save_run(self, record: V3RunRecord) -> Path:
        if record.execution_identity != self.execution_identity:
            raise V3StoreError("V3 Development record identity does not match its root")
        if self._budget_binding is not None:
            if record.budget_ledger_binding_digest != self._budget_binding.binding_digest:
                raise V3StoreError("V3 Development record is not bound to its budget ledger")
            if record.plan_version != self._budget_binding.plan_version:
                raise V3StoreError("V3 Development record plan binding differs")
            if dict(record.manifest_digests) != dict(self._budget_binding.manifest_digests):
                raise V3StoreError("V3 Development record manifest binding differs")
        if (
            self.execution_package_digest is not None
            and record.execution_package_digest != self.execution_package_digest
        ):
            raise V3StoreError("V3 Development record is not bound to its execution package")
        return super().save_run(record)

    def save_report(self, report: V3DevelopmentReport) -> Path:
        if report.execution_identity != self.execution_identity:
            raise V3StoreError("V3 Development report identity does not match its root")
        if report.measurement_status != "development_measurement_not_release":
            raise V3StoreError("PREP or activation report cannot be written to Development store")
        if self._budget_binding is not None:
            if report.budget_ledger_binding_digest != self._budget_binding.binding_digest:
                raise V3StoreError("V3 Development report is not bound to its budget ledger")
            if report.token_threshold != self._budget_binding.token_threshold:
                raise V3StoreError("V3 Development report token threshold binding differs")
        if (
            self.execution_package_digest is not None
            and report.execution_package_digest != self.execution_package_digest
        ):
            raise V3StoreError("V3 Development report is not bound to its execution package")
        return super().save_report(report)

    def validate_completeness(
        self,
        manifests: Iterable[V3DevelopmentManifest],
        cases_by_id: Mapping[str, V3CaseSpec],
        *,
        expected_execution_identity: str | None = None,
    ) -> tuple[V3RunRecord, ...]:
        records = super().validate_completeness(
            manifests,
            cases_by_id,
            expected_execution_identity=expected_execution_identity or self.execution_identity,
        )
        for record in records:
            case = cases_by_id.get(record.scenario_id)
            if case is None:
                raise V3StoreError("Development run references an unknown case")
            try:
                validate_persisted_grader_verdicts(
                    V3GradingContext(
                        case=case,
                        trace=record.trace,
                        final_outcome=record.final_outcome,
                        safety_gate_pass=record.safety_gate_pass,
                        case_scope_id=record.case_id or record.pair_id,
                    ),
                    record.trace.grader_verdicts,
                )
            except ValueError as exc:
                raise V3StoreError(
                    f"persisted grader verdict replay failed for {record.eval_run_id}"
                ) from exc
        return records


__all__ = ["V3DevelopmentStore", "V3PrepStore", "V3StoreError"]

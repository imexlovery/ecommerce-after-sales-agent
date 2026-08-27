# ruff: noqa: E501
"""Immutable, mechanically isolated storage for V3 preparation artifacts."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path

from after_sales_agent.evals.v3.contracts import (
    V3CaseSpec,
    V3DevelopmentManifest,
    V3DevelopmentReport,
    V3RunRecord,
    expected_run_keys,
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


__all__ = ["V3PrepStore", "V3StoreError"]

"""Append-only local storage for raw evaluation runs and reports."""

from __future__ import annotations

import json
from pathlib import Path

from after_sales_agent.evals.contracts import EvalReport, EvalRunRecord


class EvalArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.runs_dir = root / "runs"
        self.reports_dir = root / "reports"

    def ensure(self) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def save_run(self, record: EvalRunRecord) -> Path:
        self.ensure()
        path = self.runs_dir / f"{record.eval_run_id}.json"
        return self._write_once(path, record.model_dump(mode="json"))

    def save_report(self, report: EvalReport) -> Path:
        self.ensure()
        path = self.reports_dir / f"{report.report_id}.json"
        return self._write_once(path, report.model_dump(mode="json"))

    def load_latest_report(self) -> EvalReport | None:
        if not self.reports_dir.exists():
            return None
        candidates = sorted(self.reports_dir.glob("*.json"))
        if not candidates:
            return None
        reports = [
            EvalReport.model_validate_json(path.read_text(encoding="utf-8")) for path in candidates
        ]
        return max(reports, key=lambda item: (item.created_at, item.report_id))

    def load_runs(self, *, evaluation_revision: str | None = None) -> list[EvalRunRecord]:
        if not self.runs_dir.exists():
            return []
        records = [
            EvalRunRecord.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(self.runs_dir.glob("*.json"))
        ]
        if evaluation_revision is not None:
            records = [
                record for record in records if record.evaluation_revision == evaluation_revision
            ]
        return sorted(
            records,
            key=lambda item: (
                item.scenario_id,
                item.layer,
                item.architecture,
                item.repetition,
                item.eval_run_id,
            ),
        )

    @staticmethod
    def _write_once(path: Path, payload: dict[str, object]) -> Path:
        if path.exists():
            raise FileExistsError(f"evaluation artifact is immutable: {path}")
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
        return path

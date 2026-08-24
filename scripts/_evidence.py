"""Shared helpers for trusted, revision-bound delivery evidence scripts."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any


@dataclass(frozen=True, slots=True)
class Assertion:
    assertion_id: str
    passed: bool
    detail: str
    command: list[str] | None = None
    exit_code: int | None = None
    duration_ms: float | None = None
    safe_output_tail: str | None = None


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def committed_revision(*, require_clean: bool = True) -> str:
    root = repository_root()
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    if require_clean:
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=root,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        if status:
            raise RuntimeError("trusted evidence requires a clean committed revision")
    return revision


def run_assertion(
    assertion_id: str,
    command: list[str],
    *,
    cwd: Path | None = None,
    environment: dict[str, str] | None = None,
    detail: str,
    timeout_seconds: float = 300,
) -> Assertion:
    started = perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=cwd or repository_root(),
            env=environment,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        tail = output[-4_000:] if output else None
        return Assertion(
            assertion_id=assertion_id,
            passed=completed.returncode == 0,
            detail=detail,
            command=command,
            exit_code=completed.returncode,
            duration_ms=round((perf_counter() - started) * 1_000, 3),
            safe_output_tail=tail,
        )
    except subprocess.TimeoutExpired:
        return Assertion(
            assertion_id=assertion_id,
            passed=False,
            detail=f"{detail}; command timed out",
            command=command,
            exit_code=None,
            duration_ms=round((perf_counter() - started) * 1_000, 3),
            safe_output_tail=None,
        )


def write_report(
    path: Path,
    *,
    stage: str,
    evidence_label: str,
    revision: str,
    assertions: list[Assertion],
    metadata: dict[str, Any] | None = None,
) -> None:
    payload = {
        "schema_version": 1,
        "stage": stage,
        "evidence_label": evidence_label,
        "source_revision": revision,
        "generated_at": datetime.now(UTC).isoformat(),
        "passed": bool(assertions) and all(assertion.passed for assertion in assertions),
        "assertions": [asdict(assertion) for assertion in assertions],
        "metadata": metadata or {},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def report_exit(assertions: list[Assertion]) -> int:
    return 0 if assertions and all(assertion.passed for assertion in assertions) else 1

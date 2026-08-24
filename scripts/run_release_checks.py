"""Run the complete static, test, type, and frontend build release checks."""

from __future__ import annotations

import argparse
from pathlib import Path

from _evidence import committed_revision, report_exit, repository_root, run_assertion, write_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    revision = committed_revision()
    root = repository_root()
    assertions = [
        run_assertion(
            "full_pytest",
            ["uv", "run", "pytest", "-q"],
            detail="complete Python test suite passed",
        ),
        run_assertion(
            "ruff",
            ["uv", "run", "ruff", "check", "."],
            detail="Ruff registered checks passed",
        ),
        run_assertion(
            "mypy",
            ["uv", "run", "mypy", "src"],
            detail="strict Mypy project package checks passed",
        ),
        run_assertion(
            "frontend_typecheck",
            ["npm", "run", "typecheck"],
            cwd=root / "frontend",
            detail="frontend TypeScript project references passed",
        ),
        run_assertion(
            "frontend_production_build",
            ["npm", "run", "build"],
            cwd=root / "frontend",
            detail="frontend production build completed",
        ),
    ]
    write_report(
        args.report,
        stage="release_checks",
        evidence_label="integration",
        revision=revision,
        assertions=assertions,
    )
    return report_exit(assertions)


if __name__ == "__main__":
    raise SystemExit(main())

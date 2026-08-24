"""Run one registered local test stage and emit trusted assertion JSON."""

from __future__ import annotations

import argparse
from pathlib import Path

from _evidence import committed_revision, report_exit, run_assertion, write_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("unit", "component", "all"), required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    revision = committed_revision()
    targets = {
        "unit": ["tests/unit"],
        "component": ["tests/component", "tests/api"],
        "all": ["tests"],
    }[args.stage]
    assertion = run_assertion(
        f"pytest_{args.stage}",
        ["uv", "run", "pytest", *targets, "-q"],
        detail=f"registered {args.stage} test selection passed",
    )
    assertions = [assertion]
    write_report(
        args.report,
        stage=args.stage,
        evidence_label="contract" if args.stage == "unit" else "integration",
        revision=revision,
        assertions=assertions,
        metadata={"targets": targets},
    )
    return report_exit(assertions)


if __name__ == "__main__":
    raise SystemExit(main())

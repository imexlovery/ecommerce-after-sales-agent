"""Verify pinned framework provenance without contacting a package registry."""

from __future__ import annotations

import argparse
from importlib import import_module
from importlib.metadata import version
from pathlib import Path

from _evidence import Assertion, committed_revision, report_exit, write_report

EXPECTED: dict[str, dict[str, str]] = {
    "domain_workflow": {"langgraph": "1.2.11"},
    "tools_mcp": {"langgraph": "1.2.11", "langchain": "1.3.15"},
    "prompt_inference_models": {
        "langchain": "1.3.15",
        "langchain-deepseek": "1.1.0",
    },
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", choices=tuple(EXPECTED), required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    revision = committed_revision()
    assertions: list[Assertion] = []
    observed: dict[str, str] = {}
    for distribution, expected in EXPECTED[args.module].items():
        actual = version(distribution)
        observed[distribution] = actual
        assertions.append(
            Assertion(
                assertion_id=f"pinned_{distribution}",
                passed=actual == expected,
                detail=f"installed {distribution}={actual}; expected={expected}",
            )
        )
    module_checks = {
        "domain_workflow": ("langgraph.graph", "StateGraph"),
        "tools_mcp": ("langgraph.prebuilt", "ToolNode"),
        "prompt_inference_models": ("langchain_deepseek", "ChatDeepSeek"),
    }
    module_name, public_name = module_checks[args.module]
    imported = import_module(module_name)
    assertions.append(
        Assertion(
            assertion_id="public_api_available",
            passed=hasattr(imported, public_name),
            detail=f"{module_name}.{public_name} is available from the installed distribution",
        )
    )
    write_report(
        args.report,
        stage=f"provenance:{args.module}",
        evidence_label="static",
        revision=revision,
        assertions=assertions,
        metadata={"observed_versions": observed},
    )
    return report_exit(assertions)


if __name__ == "__main__":
    raise SystemExit(main())

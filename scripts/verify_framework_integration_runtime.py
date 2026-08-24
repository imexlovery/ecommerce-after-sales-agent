"""Verify the actual LangGraph ToolNode composition and registered integration suite."""

from __future__ import annotations

import argparse
from pathlib import Path

from _evidence import Assertion, committed_revision, report_exit, run_assertion, write_report

from after_sales_agent.agents.graph import build_investigation_graph


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    revision = committed_revision()
    graph = build_investigation_graph(None)
    graph_nodes = set(graph.nodes)
    assertions = [
        Assertion(
            assertion_id="compiled_state_graph",
            passed=(
                type(graph).__module__ == "langgraph.graph.state"
                and type(graph).__name__ == "CompiledStateGraph"
            ),
            detail=f"runtime type is {type(graph).__module__}.{type(graph).__name__}",
        ),
        Assertion(
            assertion_id="native_tool_node_path",
            passed={"agent", "tools", "budget_exhausted"}.issubset(graph_nodes),
            detail=f"compiled graph nodes={sorted(graph_nodes)}",
        ),
        run_assertion(
            "framework_integration_tests",
            [
                "uv",
                "run",
                "pytest",
                "tests/integration/test_agent_graph.py",
                "tests/integration/test_investigation_service.py",
                "tests/integration/test_eval_runner.py",
                "-q",
            ],
            detail="Agent, ToolNode, strong Workflow, and Eval integration tests passed",
        ),
    ]
    write_report(
        args.report,
        stage="framework_integration",
        evidence_label="integration",
        revision=revision,
        assertions=assertions,
        metadata={"graph_nodes": sorted(graph_nodes)},
    )
    return report_exit(assertions)


if __name__ == "__main__":
    raise SystemExit(main())

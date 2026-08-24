from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import HumanMessage, ToolMessage

from after_sales_agent.agents.graph import build_investigation_graph
from after_sales_agent.agents.models import MockInvestigationModel
from after_sales_agent.agents.tool_bindings import READ_TOOLS, InvestigationRuntimeContext
from after_sales_agent.domain.models import TrustedToolContext
from after_sales_agent.domain.state import IssueType


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool_name, arguments))
        return {
            "execution_status": "success",
            "evidence_availability": "absent",
            "source_type": "fixture",
            "source_query_id": f"query-{tool_name}",
            "source_record_ids": [],
            "observed_at": "2026-08-23T00:00:00Z",
            "payload": None,
            "error_code": None,
            "retryable": False,
            "result_hash": "0" * 64,
            "untrusted_fields": [],
        }


@pytest.mark.asyncio
async def test_mock_model_uses_native_toolnode_with_hidden_trusted_runtime() -> None:
    graph = build_investigation_graph()
    executor = RecordingExecutor()
    turns: list[int] = []

    async def on_turn(turn: int) -> None:
        turns.append(turn)

    runtime = InvestigationRuntimeContext(
        trusted=TrustedToolContext(
            customer_id="cus_a",
            conversation_id="conv_test",
            case_id="case_test",
            run_id="run_test",
            authorized_order_id="ORD-001",
            canonical_issue_type=IssueType.SIGNED_NOT_RECEIVED,
            fixture_version="fixture-v1",
            fault_seed="safe-seed",
            evaluated_at="2026-08-23T00:00:00Z",
            trace_id="trace_test",
        ),
        tool_executor=executor,
        model=MockInvestigationModel(READ_TOOLS),
        on_agent_turn=on_turn,
    )

    output = await graph.ainvoke(
        {
            "messages": [
                HumanMessage(
                    content=(
                        "AUTHORIZED_ORDER=ORD-001\n"
                        "CANONICAL_ISSUE=signed_not_received\n"
                        "Customer reports delivered but missing."
                    )
                )
            ],
            "planning_turns": 0,
            "budget_exhausted": False,
        },
        context=runtime,
    )

    assert [name for name, _ in executor.calls] == [
        "get_order_context",
        "get_logistics_timeline",
        "get_delivery_proof",
        "search_after_sales_policy",
        "get_existing_logistics_tickets",
    ]
    assert turns == [1, 2, 3, 4, 5, 6]
    assert len([message for message in output["messages"] if isinstance(message, ToolMessage)]) == 5
    assert all(
        "runtime" not in tool.tool_call_schema.model_json_schema()["properties"]
        for tool in READ_TOOLS
    )

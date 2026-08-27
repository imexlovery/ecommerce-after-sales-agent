"""The single bounded LangGraph investigation loop."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime

from after_sales_agent.agents.tool_bindings import (
    READ_TOOLS,
    InvestigationRuntimeContext,
)
from after_sales_agent.tools.budget import ToolBudgetExceeded


class InvestigationState(MessagesState):
    planning_turns: int
    budget_exhausted: bool
    force_replan: bool
    terminal: bool


async def call_model(
    state: InvestigationState,
    runtime: Runtime[InvestigationRuntimeContext],
) -> dict[str, Any]:
    next_turn = state.get("planning_turns", 0) + 1
    before_selector = runtime.context.before_selector
    if before_selector is not None:
        pre = await before_selector(next_turn)
        if pre.get("response") is not None:
            return {
                "messages": [pre["response"]],
                "planning_turns": state.get("planning_turns", 0),
                "force_replan": False,
                "terminal": bool(pre.get("terminal", False)),
            }
        if pre.get("terminal"):
            return {
                "messages": [
                    AIMessage(content=str(pre.get("message", "调查已达到确定性终止条件。")))
                ],
                "terminal": True,
            }
    try:
        await runtime.context.on_agent_turn(next_turn)
    except ToolBudgetExceeded:
        return {
            "messages": [
                AIMessage(
                    content=(
                        "调查已达到 Case 或本次规划上限。"
                        "确定性 Evidence Gate 将安全结束本次自动调查。"
                    )
                )
            ],
            "budget_exhausted": True,
            "terminal": True,
        }
    select_observation = runtime.context.select_observation
    if select_observation is None:
        raise RuntimeError("typed select_observation runtime callback is required")
    post = await select_observation(next_turn)
    return {
        "messages": [post["response"]],
        "planning_turns": next_turn,
        "force_replan": bool(post.get("force_replan", False)),
        "terminal": bool(post.get("terminal", False)),
    }


def route_after_model(state: InvestigationState) -> str:
    if state.get("terminal", False):
        return END
    if state.get("force_replan", False):
        return "agent"
    last_message = state["messages"][-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        if state.get("planning_turns", 0) >= 8:
            return "budget_exhausted"
        return "tools"
    return END


async def budget_exhausted(
    _: InvestigationState,
    runtime: Runtime[InvestigationRuntimeContext],
) -> dict[str, Any]:
    del runtime
    return {
        "messages": [
            AIMessage(
                content=(
                    "调查已达到本次规划上限。确定性流程将保留已取得的证据，并选择安全的后续处理。"
                )
            )
        ],
        "budget_exhausted": True,
        "terminal": True,
    }


def build_investigation_graph(checkpointer: Any | None = None) -> Any:
    builder = StateGraph(InvestigationState, context_schema=InvestigationRuntimeContext)
    builder.add_node("agent", call_model)
    builder.add_node("tools", ToolNode(list(READ_TOOLS), handle_tool_errors=False))
    # LangGraph accepts this runtime-aware async node, but its current overloads
    # reject the valid partial-state return type under strict mypy.
    builder.add_node("budget_exhausted", budget_exhausted)  # type: ignore[call-overload]
    builder.add_edge(START, "agent")
    builder.add_conditional_edges(
        "agent",
        route_after_model,
        {
            "tools": "tools",
            "budget_exhausted": "budget_exhausted",
            "agent": "agent",
            END: END,
        },
    )
    builder.add_edge("tools", "agent")
    builder.add_edge("budget_exhausted", END)
    return builder.compile(checkpointer=checkpointer)

"""Native LangChain read-tool bindings for the investigation graph.

The injected runtime is hidden from the model. It carries trusted identity and a
governed executor; tool arguments alone never grant access.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from langchain.tools import ToolRuntime, tool

from after_sales_agent.domain.models import TrustedToolContext


class ToolExecutor(Protocol):
    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class InvestigationRuntimeContext:
    trusted: TrustedToolContext
    # ``Any`` keeps LangChain's temporary validation model serializable while
    # the constructor and application composition still require ToolExecutor behavior.
    tool_executor: Any
    model: Any
    on_agent_turn: Any


async def _execute_read_tool(
    runtime: ToolRuntime[InvestigationRuntimeContext, Any],
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Execute through trusted context and retain the native ToolCall identity.

    LangChain creates a temporary validation model before filtering injected
    arguments. The runtime context therefore keeps its behavior-bearing fields
    opaque to Pydantic while the application constructor enforces them. The
    runtime itself is injected and omitted from the model-visible tool schema.
    """

    context = runtime.context
    execute_with_call_id = getattr(context.tool_executor, "execute_with_call_id", None)
    if execute_with_call_id is not None:
        result = await execute_with_call_id(tool_name, arguments, runtime.tool_call_id)
    else:
        result = await context.tool_executor.execute(tool_name, arguments)
    return {"tool_call_id": runtime.tool_call_id, **result}


@tool
async def get_order_context(
    order_id: str, runtime: ToolRuntime[InvestigationRuntimeContext, Any]
) -> dict[str, Any]:
    """Read the authorized order's canonical status and server-derived shipping fields."""
    return await _execute_read_tool(runtime, "get_order_context", {"order_id": order_id})


@tool
async def get_logistics_timeline(
    order_id: str, runtime: ToolRuntime[InvestigationRuntimeContext, Any]
) -> dict[str, Any]:
    """Read the normalized tracking timeline for the authorized order."""
    return await _execute_read_tool(runtime, "get_logistics_timeline", {"order_id": order_id})


@tool
async def get_delivery_proof(
    order_id: str, runtime: ToolRuntime[InvestigationRuntimeContext, Any]
) -> dict[str, Any]:
    """Read proof-of-delivery evidence; a completed not-found result is valid evidence."""
    return await _execute_read_tool(runtime, "get_delivery_proof", {"order_id": order_id})


@tool
async def get_carrier_service_alerts(
    order_id: str, runtime: ToolRuntime[InvestigationRuntimeContext, Any]
) -> dict[str, Any]:
    """Read optional carrier service alerts for explanatory context."""
    return await _execute_read_tool(runtime, "get_carrier_service_alerts", {"order_id": order_id})


@tool
async def get_after_sales_policy(
    order_id: str,
    issue_type: str,
    runtime: ToolRuntime[InvestigationRuntimeContext, Any],
) -> dict[str, Any]:
    """Read the applicable after-sales logistics policy for the canonical issue."""
    return await _execute_read_tool(
        runtime, "get_after_sales_policy", {"order_id": order_id, "issue_type": issue_type}
    )


@tool
async def get_existing_logistics_tickets(
    order_id: str,
    issue_type: str,
    runtime: ToolRuntime[InvestigationRuntimeContext, Any],
) -> dict[str, Any]:
    """Read active logistics investigation tickets before any new proposal is allowed."""
    return await _execute_read_tool(
        runtime,
        "get_existing_logistics_tickets",
        {"order_id": order_id, "issue_type": issue_type},
    )


READ_TOOLS = (
    get_order_context,
    get_logistics_timeline,
    get_delivery_proof,
    get_carrier_service_alerts,
    get_after_sales_policy,
    get_existing_logistics_tickets,
)

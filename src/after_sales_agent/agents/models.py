"""Inference adapters with an explicit Mock/Live boundary."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_deepseek import ChatDeepSeek

from after_sales_agent.config import LLMMode, Settings


class MockInvestigationModel:
    """Deterministic native-tool-call fixture for offline product verification.

    It deliberately emits normal LangChain ``AIMessage.tool_calls`` so the same
    LangGraph ``ToolNode`` path is exercised without contacting a provider.
    """

    _signed_not_received_sequence = (
        "get_order_context",
        "get_logistics_timeline",
        "get_delivery_proof",
        "get_after_sales_policy",
        "get_existing_logistics_tickets",
    )
    _stalled_tracking_sequence = (
        "get_order_context",
        "get_logistics_timeline",
        "get_carrier_service_alerts",
        "get_after_sales_policy",
        "get_existing_logistics_tickets",
    )

    def __init__(self, tools: Sequence[Any]) -> None:
        self._tool_names = {tool.name for tool in tools}

    async def ainvoke(self, messages: Sequence[BaseMessage]) -> AIMessage:
        completed = [message.name for message in messages if isinstance(message, ToolMessage)]
        issue_type = self._issue_from_messages(messages)
        if (
            issue_type == "signed_not_received"
            and "get_order_context" in completed
            and self._order_status_from_messages(messages) not in {None, "delivered"}
        ):
            return AIMessage(
                content=(
                    "订单尚未处于签收状态。"
                    "请由确定性 Evidence Gate 先修正问题类型，再决定后续只读查询。"
                )
            )
        sequence = (
            self._signed_not_received_sequence
            if issue_type == "signed_not_received"
            else self._stalled_tracking_sequence
        )
        for tool_name in sequence:
            if tool_name not in completed:
                if tool_name not in self._tool_names:
                    raise RuntimeError(f"Mock model required unavailable tool: {tool_name}")
                order_id = self._authorized_order_from_messages(messages)
                arguments: dict[str, Any] = {"order_id": order_id}
                if tool_name in {
                    "get_after_sales_policy",
                    "get_existing_logistics_tickets",
                }:
                    arguments["issue_type"] = issue_type
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": tool_name,
                            "args": arguments,
                            "id": f"mock-{uuid4().hex}",
                            "type": "tool_call",
                        }
                    ],
                )
        return AIMessage(
            content=(
                "必要的只读查询已经完成。"
                "请由确定性 Evidence Gate 判断是否可以创建物流核查工单提案。"
            )
        )

    @staticmethod
    def _authorized_order_from_messages(messages: Sequence[BaseMessage]) -> str:
        for message in messages:
            if isinstance(message.content, str):
                match = re.search(r"AUTHORIZED_ORDER=(ORD-[A-Z0-9-]+)", message.content)
                if match:
                    return match.group(1)
        raise RuntimeError("Mock model did not receive an authorized order marker")

    @staticmethod
    def _issue_from_messages(messages: Sequence[BaseMessage]) -> str:
        for message in reversed(messages):
            if isinstance(message.content, str):
                match = re.search(r"CANONICAL_ISSUE=([a-z_]+)", message.content)
                if match:
                    return match.group(1)
        raise RuntimeError("Mock model did not receive a canonical issue marker")

    @staticmethod
    def _order_status_from_messages(messages: Sequence[BaseMessage]) -> str | None:
        for message in reversed(messages):
            if not isinstance(message, ToolMessage) or message.name != "get_order_context":
                continue
            if not isinstance(message.content, str):
                continue
            try:
                payload = json.loads(message.content)
            except json.JSONDecodeError:
                continue
            result_payload = payload.get("payload")
            if isinstance(result_payload, dict):
                status = result_payload.get("order_status")
                if isinstance(status, str):
                    return status
        return None


def build_live_model(settings: Settings) -> ChatDeepSeek:
    if settings.llm_mode is not LLMMode.LIVE or not settings.deepseek_api_key:
        raise RuntimeError("Live model requested outside a valid Live configuration")
    return ChatDeepSeek(
        model_name=settings.deepseek_model,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_api_base,
        timeout=settings.deepseek_timeout_seconds,
        max_retries=1,
        temperature=0,
        extra_body={"thinking": {"type": "disabled"}},
    )


def build_investigation_model(settings: Settings, tools: Sequence[Any]) -> Any:
    """Build the one configured investigation model with native tool binding."""

    if settings.llm_mode is LLMMode.MOCK:
        return MockInvestigationModel(tools)
    return build_live_model(settings).bind_tools(list(tools))


def build_live_triage_runnable(settings: Settings, schema: type[Any]) -> Any:
    model: BaseChatModel = build_live_model(settings)
    parser: PydanticOutputParser[Any] = PydanticOutputParser(pydantic_object=schema)
    return (model | parser).with_config({"run_name": "triage"})


def triage_format_instructions(schema: type[Any]) -> str:
    """Return the project-owned JSON contract without provider-specific response formats."""

    return PydanticOutputParser(pydantic_object=schema).get_format_instructions()

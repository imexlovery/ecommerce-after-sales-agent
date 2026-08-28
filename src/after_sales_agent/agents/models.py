"""Inference adapters with an explicit Mock/Live boundary."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_deepseek import ChatDeepSeek

from after_sales_agent.application.provider_budget import SelectorSchemaFailure
from after_sales_agent.config import LLMMode, Settings

INVESTIGATION_OUTPUT_TOKEN_CAP = 512


class MockInvestigationModel:
    """Deterministic native-tool-call fixture for offline product verification.

    It deliberately emits normal LangChain ``AIMessage.tool_calls`` so the same
    LangGraph ``ToolNode`` path is exercised without contacting a provider.
    """

    _signed_not_received_sequence = (
        "get_order_context",
        "get_logistics_timeline",
        "get_delivery_proof",
        "search_after_sales_policy",
        "get_existing_logistics_tickets",
    )
    _stalled_tracking_sequence = (
        "get_order_context",
        "get_logistics_timeline",
        "get_carrier_service_alerts",
        "search_after_sales_policy",
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
                    "search_after_sales_policy",
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
        # The composition root places the trusted canonical marker before the
        # customer message.  First-match semantics prevent a later customer
        # string from spoofing the server-owned issue context.
        for message in messages:
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


class WorkflowInvestigationModel(MockInvestigationModel):
    """Deterministic selector adapter used inside the shared LangGraph graph.

    It emits the same native ``AIMessage.tool_calls`` contract as the Agent
    model, but chooses the next observation from typed tool-result messages and
    the canonical issue marker.  It never reads Fixtures or a backend.
    """

    async def ainvoke(self, messages: Sequence[BaseMessage]) -> AIMessage:
        completed: dict[str, dict[str, Any] | None] = {}
        for message in messages:
            if not isinstance(message, ToolMessage) or not isinstance(message.content, str):
                continue
            try:
                value = json.loads(message.content)
            except json.JSONDecodeError:
                value = None
            completed[message.name or ""] = value if isinstance(value, dict) else None
        issue_type = self._issue_from_messages(messages)
        order_id = self._authorized_order_from_messages(messages)

        def call(tool_name: str) -> AIMessage:
            if tool_name not in self._tool_names:
                raise RuntimeError(f"Workflow selector required unavailable tool: {tool_name}")
            arguments: dict[str, Any] = {"order_id": order_id}
            if tool_name in {"search_after_sales_policy", "get_existing_logistics_tickets"}:
                arguments["issue_type"] = issue_type
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": tool_name,
                        "args": arguments,
                        "id": f"workflow-{uuid4().hex}",
                        "type": "tool_call",
                    }
                ],
            )

        if "get_order_context" not in completed:
            return call("get_order_context")
        order_status = self._result_payload_value(
            completed.get("get_order_context"), "order_status"
        )
        if issue_type == "signed_not_received":
            if order_status not in {None, "delivered"}:
                return AIMessage(content="订单状态与报告的问题不一致。")
            if "get_existing_logistics_tickets" not in completed:
                return call("get_existing_logistics_tickets")
            active = self._result_payload_value(
                completed.get("get_existing_logistics_tickets"), "active_tickets"
            )
            if isinstance(active, list) and active:
                return AIMessage(content="已有物流核查工单，停止重复调查。")
            for tool_name in (
                "get_logistics_timeline",
                "search_after_sales_policy",
                "get_delivery_proof",
            ):
                if tool_name not in completed:
                    return call(tool_name)
        else:
            if "get_logistics_timeline" not in completed:
                return call("get_logistics_timeline")
            if "search_after_sales_policy" not in completed:
                return call("search_after_sales_policy")
            timeline_hours = self._result_payload_value(
                completed.get("get_logistics_timeline"), "hours_since_last_update"
            )
            policy = self._result_payload_value(
                completed.get("search_after_sales_policy"), "policy_fact_snapshot"
            )
            threshold = policy.get("stalled_after_hours") if isinstance(policy, dict) else None
            retrieval = self._result_payload_value(
                completed.get("search_after_sales_policy"), "retrieval_status"
            )
            if (
                order_status != "shipped"
                or retrieval != "hit"
                or not isinstance(timeline_hours, (int, float))
                or not isinstance(threshold, (int, float))
                or timeline_hours <= threshold
            ):
                return AIMessage(content="当前观察已足以由确定性 Evidence Gate 判断。")
            if "get_carrier_service_alerts" not in completed:
                return call("get_carrier_service_alerts")
            if "get_existing_logistics_tickets" not in completed:
                return call("get_existing_logistics_tickets")
        return AIMessage(content="必要的只读观察已经完成。")

    @staticmethod
    def _result_payload_value(result: dict[str, Any] | None, key: str) -> Any:
        if not isinstance(result, dict):
            return None
        if key in result and key != "payload":
            return result.get(key)
        payload = result.get("payload")
        if isinstance(payload, dict):
            return payload.get(key)
        return result.get(key)


class AgentObservationSelector:
    """Adapter exposing the model-backed selector contract."""

    def __init__(self, model: Any, invocation_observer: Any | None = None) -> None:
        self.model = model
        self._invocation_observer = invocation_observer

    async def select_next_observation(self, context: Any) -> Any:
        from langchain_core.messages import HumanMessage, SystemMessage

        from after_sales_agent.application.adaptive_core import (
            EvidenceRequirementCode,
            NextObservationCandidate,
            ObservationAction,
            ObservationReasonCode,
        )
        if isinstance(self.model, MockInvestigationModel):
            return _candidate_for_first_missing(
                context,
                (
                    EvidenceRequirementCode.ORDER_STATUS,
                    EvidenceRequirementCode.TRACKING_TIMELINE,
                    EvidenceRequirementCode.DELIVERY_PROOF,
                    EvidenceRequirementCode.POLICY_APPLICABILITY,
                    EvidenceRequirementCode.ACTIVE_TICKET_STATUS,
                    EvidenceRequirementCode.CARRIER_ALERT_CONTEXT,
                ),
            )
        message = HumanMessage(
            content=(
                f"AUTHORIZED_ORDER={context.authorized_order_id}\n"
                f"CANONICAL_ISSUE={context.canonical_issue_type.value}\n"
                f"EVIDENCE_PROGRESS={context.evidence_progress.model_dump(mode='json')}"
            )
        )
        model_messages = [SystemMessage(content="Select one typed next observation."), message]
        if self._invocation_observer is None:
            response = await self.model.ainvoke(model_messages)
        else:
            response = await self._invocation_observer.invoke(
                model=self.model,
                messages=model_messages,
                context=context,
            )
        if not isinstance(response, AIMessage):
            raise SelectorSchemaFailure("provider selector did not return an AIMessage")
        if not isinstance(response.tool_calls, list):
            raise SelectorSchemaFailure("provider selector tool_calls is not a list")
        if len(response.tool_calls) == 0:
            return NextObservationCandidate(
                action=ObservationAction.FINISH,
                arguments={},
                addresses=(),
                reason_code=ObservationReasonCode.FINALIZATION_REQUESTED,
            )
        if len(response.tool_calls) != 1:
            raise SelectorSchemaFailure("provider selector returned more than one tool call")
        call = response.tool_calls[0]
        if not isinstance(call, Mapping):
            raise SelectorSchemaFailure("provider selector tool call is not an object")
        tool_name = str(call.get("name", ""))
        requirement = {
            "get_order_context": EvidenceRequirementCode.ORDER_STATUS,
            "get_logistics_timeline": EvidenceRequirementCode.TRACKING_TIMELINE,
            "get_delivery_proof": EvidenceRequirementCode.DELIVERY_PROOF,
            "search_after_sales_policy": EvidenceRequirementCode.POLICY_APPLICABILITY,
            "get_existing_logistics_tickets": EvidenceRequirementCode.ACTIVE_TICKET_STATUS,
            "get_carrier_service_alerts": EvidenceRequirementCode.CARRIER_ALERT_CONTEXT,
        }.get(tool_name)
        return NextObservationCandidate(
            action=ObservationAction.CALL_TOOL,
            tool_name=tool_name,
            arguments=(
                dict(call.get("args", {}))
                if isinstance(call.get("args", {}), Mapping)
                else {}
            ),
            addresses=(requirement,) if requirement else (),
            reason_code=ObservationReasonCode.MISSING_REQUIRED_EVIDENCE,
        )


class WorkflowObservationSelector:
    """Deterministic selector consuming only a DecisionContext."""

    async def select_next_observation(self, context: Any) -> Any:
        from after_sales_agent.application.adaptive_core import (
            EvidenceRequirementCode,
            NextObservationCandidate,
            ObservationAction,
            ObservationReasonCode,
        )

        progress = context.evidence_progress
        if progress is None:
            raise ValueError("DecisionContext requires EvidenceProgress")
        if progress.gate_readiness.value == "evaluable":
            return NextObservationCandidate(
                action=ObservationAction.FINISH,
                arguments={},
                addresses=(),
                reason_code=ObservationReasonCode.FINALIZATION_REQUESTED,
            )
        ordered = (
            (
                EvidenceRequirementCode.ORDER_STATUS,
                EvidenceRequirementCode.ACTIVE_TICKET_STATUS,
                EvidenceRequirementCode.TRACKING_TIMELINE,
                EvidenceRequirementCode.POLICY_APPLICABILITY,
                EvidenceRequirementCode.DELIVERY_PROOF,
                EvidenceRequirementCode.CARRIER_ALERT_CONTEXT,
            )
            if context.canonical_issue_type.value == "signed_not_received"
            else (
                EvidenceRequirementCode.ORDER_STATUS,
                EvidenceRequirementCode.TRACKING_TIMELINE,
                EvidenceRequirementCode.POLICY_APPLICABILITY,
                EvidenceRequirementCode.ACTIVE_TICKET_STATUS,
                EvidenceRequirementCode.CARRIER_ALERT_CONTEXT,
                EvidenceRequirementCode.DELIVERY_PROOF,
            )
        )
        return _candidate_for_first_missing(context, ordered)


def _candidate_for_first_missing(context: Any, ordered: tuple[Any, ...]) -> Any:
    from after_sales_agent.application.adaptive_core import (
        EvidenceProgressStatus,
        EvidenceRequirementCode,
        NextObservationCandidate,
        ObservationAction,
        ObservationReasonCode,
    )

    tools = {
        EvidenceRequirementCode.ORDER_STATUS: "get_order_context",
        EvidenceRequirementCode.TRACKING_TIMELINE: "get_logistics_timeline",
        EvidenceRequirementCode.DELIVERY_PROOF: "get_delivery_proof",
        EvidenceRequirementCode.POLICY_APPLICABILITY: "search_after_sales_policy",
        EvidenceRequirementCode.ACTIVE_TICKET_STATUS: "get_existing_logistics_tickets",
        EvidenceRequirementCode.CARRIER_ALERT_CONTEXT: "get_carrier_service_alerts",
    }
    for code in ordered:
        state = context.evidence_progress.requirements[code]
        if (
            state.status is EvidenceProgressStatus.MISSING
            and state.applicability.value != "not_required"
        ):
            tool_name = tools[code]
            args: dict[str, Any] = {"order_id": context.authorized_order_id}
            if tool_name in {"search_after_sales_policy", "get_existing_logistics_tickets"}:
                args["issue_type"] = context.canonical_issue_type.value
            return NextObservationCandidate(
                action=ObservationAction.CALL_TOOL,
                tool_name=tool_name,
                arguments=args,
                addresses=(code,),
                reason_code=(
                    ObservationReasonCode.FIRST_REQUIRED_OBSERVATION
                    if context.evidence_progress.revision == 0
                    else ObservationReasonCode.MISSING_REQUIRED_EVIDENCE
                ),
            )
    return NextObservationCandidate(
        action=ObservationAction.FINISH,
        arguments={},
        addresses=(),
        reason_code=ObservationReasonCode.FINALIZATION_REQUESTED,
    )


def build_live_model(settings: Settings) -> ChatDeepSeek:
    if settings.llm_mode is not LLMMode.LIVE or not settings.deepseek_api_key:
        raise RuntimeError("Live model requested outside a valid Live configuration")
    return ChatDeepSeek(
        model_name=settings.deepseek_model,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_api_base,
        timeout=settings.deepseek_timeout_seconds,
        max_retries=0,
        max_tokens=INVESTIGATION_OUTPUT_TOKEN_CAP,
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

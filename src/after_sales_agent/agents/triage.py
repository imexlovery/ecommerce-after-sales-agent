"""Lightweight, tool-free message triage.

Triage extracts a small schema only. Authorization and business routing remain
deterministic application responsibilities.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage

from after_sales_agent.agents.models import (
    build_live_triage_runnable,
    triage_format_instructions,
)
from after_sales_agent.agents.prompts import TRIAGE_SYSTEM_PROMPT
from after_sales_agent.config import LLMMode, Settings
from after_sales_agent.domain.models import TriageResult
from after_sales_agent.domain.state import TriageIntent

_ORDER_ID_PATTERN = re.compile(r"\bORD-[A-Z0-9-]+\b", re.IGNORECASE)
_OVERRIDE_PATTERNS = (
    "忽略规则",
    "忽略之前",
    "忽略系统",
    "绕过指令",
    "system prompt",
    "developer message",
    "ignore previous",
    "ignore all",
)
_PROHIBITED_TOPIC_PATTERNS = (
    "退款",
    "赔偿",
    "补偿",
    "退货",
    "换货",
    "refund",
    "compensate",
    "return",
    "exchange",
)
_PROHIBITED_ACTION_PATTERNS = (
    "要退款",
    "给我退款",
    "帮我退款",
    "并退款",
    "申请退款",
    "立即退款",
    "马上退款",
    "要赔偿",
    "给我赔偿",
    "帮我赔偿",
    "申请赔偿",
    "要退货",
    "帮我退货",
    "申请退货",
    "要换货",
    "帮我换货",
    "申请换货",
    "refund me",
    "compensate me",
)
_REFUND_RETURN_INFO_PATTERNS = (
    "怎么退款",
    "如何退款",
    "退款流程",
    "退款规则",
    "退款政策",
    "怎么退货",
    "如何退货",
    "退货流程",
    "退货规则",
    "退货政策",
    "换货流程",
    "赔付规则",
    "refund policy",
    "return policy",
)
_SIGNED_PATTERNS = ("签收", "已送达", "显示送达", "delivered", "signed")
_MISSING_PATTERNS = (
    "没收到",
    "没有收到",
    "未收到",
    "找不到",
    "没拿到",
    "not received",
    "missing",
)
_STALLED_PATTERNS = (
    "没更新",
    "未更新",
    "没有更新",
    "没有物流更新",
    "物流没有更新",
    "不动了",
    "卡住",
    "卡在",
    "停滞",
    "一直在运输",
    "只收到一部分",
    "剩下的包裹",
    "no update",
    "stalled",
)
_LOGISTICS_PATTERNS = ("快递", "物流", "包裹", "运单", "配送", "订单", "tracking", "parcel")
_CAPABILITY_HELP_PATTERNS = (
    "能帮我做什么",
    "能帮我什么",
    "可以做什么",
    "支持什么",
    "你会什么",
    "what can you do",
)
_ORDER_ID_HELP_PATTERNS = (
    "订单号在哪里",
    "订单号在哪",
    "怎么找订单号",
    "如何找订单号",
    "订单号是什么",
    "where is my order id",
)
_TRACKING_STATUS_PATTERNS = (
    "物流到哪",
    "快递到哪",
    "包裹到哪",
    "查下物流",
    "查一下物流",
    "现在什么情况",
    "当前什么情况",
    "where is my package",
)
_DELIVERY_ETA_PATTERNS = (
    "什么时候到",
    "多久能到",
    "几天送到",
    "预计送达",
    "大概什么时候",
    "when will it arrive",
)
_CHANGE_DELIVERY_PATTERNS = (
    "改地址",
    "修改地址",
    "换地址",
    "改收货地址",
    "修改收货地址",
    "改电话",
    "修改收货电话",
    "改收件人",
    "配送备注",
    "change address",
)
_HUMAN_SUPPORT_PATTERNS = (
    "人工客服",
    "找人工",
    "转人工",
    "联系人工",
    "human support",
    "human agent",
)
_THANKS_CLOSE_PATTERNS = (
    "谢谢",
    "感谢",
    "知道了",
    "明白了",
    "再见",
    "thank you",
    "thanks",
    "goodbye",
)
_PII_PATTERNS = (
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
)
_KNOWN_RISK_FLAGS = (
    "instruction_override_attempt",
    "prohibited_action_request",
    "multiple_order_ids",
    "unnecessary_personal_data",
)


@dataclass(frozen=True, slots=True)
class ValidatedCustomerMessage:
    """Validated customer text plus a safe trace projection."""

    content: str
    trace_content: str
    had_unnecessary_personal_data: bool


class MessageValidationError(ValueError):
    """Raised before any model or tool access for invalid customer input."""


def _literal_order_ids(content: str) -> list[str]:
    return list(
        dict.fromkeys(match.upper() for match in _ORDER_ID_PATTERN.findall(content))
    )[:16]


def _deterministic_risk_flags(content: str, order_ids: list[str]) -> list[str]:
    lowered = content.casefold()
    observed: set[str] = set()
    if any(token in lowered for token in _OVERRIDE_PATTERNS):
        observed.add("instruction_override_attempt")
    if any(token in lowered for token in _PROHIBITED_ACTION_PATTERNS):
        observed.add("prohibited_action_request")
    if len(order_ids) > 1:
        observed.add("multiple_order_ids")
    if any(pattern.search(content) for pattern in _PII_PATTERNS):
        observed.add("unnecessary_personal_data")
    return [flag for flag in _KNOWN_RISK_FLAGS if flag in observed]


def normalize_triage_result(content: str, result: TriageResult) -> TriageResult:
    """Merge model classification with literal server-observed entry facts."""

    order_ids = _literal_order_ids(content)
    observed_flags = set(result.risk_flags) & set(_KNOWN_RISK_FLAGS)
    observed_flags.update(_deterministic_risk_flags(content, order_ids))
    return TriageResult(
        intent=result.intent,
        risk_flags=[flag for flag in _KNOWN_RISK_FLAGS if flag in observed_flags],
        order_ids_mentioned=order_ids,
        confidence=result.confidence,
    )


def validate_customer_message(content: str, *, max_chars: int) -> ValidatedCustomerMessage:
    normalized = content.strip()
    if not normalized:
        raise MessageValidationError("请输入需要核查的物流问题。")
    if len(normalized) > max_chars:
        raise MessageValidationError(f"消息不能超过 {max_chars} 个字符。")

    trace_content = normalized
    found_pii = False
    for pattern in _PII_PATTERNS:
        trace_content, count = pattern.subn("[已隐藏的个人信息]", trace_content)
        found_pii = found_pii or count > 0
    return ValidatedCustomerMessage(
        content=normalized,
        trace_content=trace_content,
        had_unnecessary_personal_data=found_pii,
    )


class TriageService:
    """Explicit Mock/Live triage with no provider fallback."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._live_runnable = (
            build_live_triage_runnable(settings, TriageResult)
            if settings.llm_mode is LLMMode.LIVE
            else None
        )

    async def classify(self, content: str) -> TriageResult:
        if self._settings.llm_mode is LLMMode.MOCK:
            return classify_mock(content)
        if self._live_runnable is None:
            raise RuntimeError("Live triage is not configured")
        result = await self._live_runnable.ainvoke(
            [
                SystemMessage(
                    content=(
                        f"{TRIAGE_SYSTEM_PROMPT}\n\n"
                        f"{triage_format_instructions(TriageResult)}"
                    )
                ),
                HumanMessage(content=content),
            ]
        )
        if not isinstance(result, TriageResult):
            result = TriageResult.model_validate(result)
        return normalize_triage_result(content, result)


def classify_mock(content: str) -> TriageResult:
    """Deterministic fixture classifier used only in explicit Mock mode."""

    lowered = content.casefold()
    order_ids = _literal_order_ids(content)
    risk_flags = _deterministic_risk_flags(content, order_ids)

    stalled = any(token in lowered for token in _STALLED_PATTERNS)
    missing = any(token in lowered for token in _MISSING_PATTERNS)
    explicit_signed = any(token in lowered for token in _SIGNED_PATTERNS)
    signed = missing and (explicit_signed or not stalled)
    logistics = bool(order_ids) or any(token in lowered for token in _LOGISTICS_PATTERNS)
    prohibited_action = any(token in lowered for token in _PROHIBITED_ACTION_PATTERNS)
    prohibited_topic = any(token in lowered for token in _PROHIBITED_TOPIC_PATTERNS)
    refund_return_info = any(
        token in lowered for token in _REFUND_RETURN_INFO_PATTERNS
    ) or (prohibited_topic and not prohibited_action)

    if signed:
        intent = TriageIntent.SIGNED_NOT_RECEIVED
        confidence = 0.98
    elif stalled:
        intent = TriageIntent.STALLED_TRACKING
        confidence = 0.96
    elif any(token in lowered for token in _CAPABILITY_HELP_PATTERNS):
        intent = TriageIntent.CAPABILITY_HELP
        confidence = 0.94
    elif any(token in lowered for token in _ORDER_ID_HELP_PATTERNS):
        intent = TriageIntent.ORDER_ID_HELP
        confidence = 0.96
    elif any(token in lowered for token in _HUMAN_SUPPORT_PATTERNS):
        intent = TriageIntent.HUMAN_SUPPORT_REQUEST
        confidence = 0.98
    elif any(token in lowered for token in _DELIVERY_ETA_PATTERNS):
        intent = TriageIntent.DELIVERY_ETA_INFO
        confidence = 0.92
    elif any(token in lowered for token in _CHANGE_DELIVERY_PATTERNS):
        intent = TriageIntent.CHANGE_DELIVERY_INFO
        confidence = 0.96
    elif refund_return_info:
        intent = TriageIntent.REFUND_RETURN_INFO
        confidence = 0.94
    elif any(token in lowered for token in _THANKS_CLOSE_PATTERNS):
        intent = TriageIntent.THANKS_CLOSE
        confidence = 0.96
    elif any(token in lowered for token in _TRACKING_STATUS_PATTERNS):
        intent = TriageIntent.TRACKING_STATUS_QUERY
        confidence = 0.9
    elif logistics and prohibited_action:
        intent = TriageIntent.PROHIBITED
        confidence = 0.91
    elif logistics and any(token in lowered for token in ("有问题", "异常", "怎么办", "help")):
        intent = TriageIntent.AMBIGUOUS
        confidence = 0.72
    elif logistics:
        intent = TriageIntent.OTHER_LOGISTICS
        confidence = 0.78
    elif prohibited_action:
        intent = TriageIntent.PROHIBITED
        confidence = 0.86
    else:
        intent = TriageIntent.OUT_OF_SCOPE
        confidence = 0.93

    return TriageResult(
        intent=intent,
        risk_flags=risk_flags,
        order_ids_mentioned=order_ids,
        confidence=confidence,
    )

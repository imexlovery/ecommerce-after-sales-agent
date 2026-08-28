from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage

import after_sales_agent.agents.models as models
from after_sales_agent.agents.models import (
    AgentObservationSelector,
    parse_live_selector_tool_call,
)
from after_sales_agent.agents.tool_bindings import READ_TOOLS
from after_sales_agent.application.provider_budget import SelectorSchemaFailure
from after_sales_agent.config import Settings
from after_sales_agent.domain.state import IssueType


class _FakeProvider:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls = 0

    async def ainvoke(self, _: object) -> Any:
        self.calls += 1
        return self.response


class _Progress:
    gate_readiness = SimpleNamespace(value="not_evaluable")

    def model_dump(self, *, mode: str) -> dict[str, Any]:
        assert mode == "json"
        return {"gate_readiness": "not_evaluable", "missing_required_codes": ["ORDER_STATUS"]}


def _context() -> Any:
    return SimpleNamespace(
        authorized_order_id="ORD-001",
        canonical_issue_type=IssueType.SIGNED_NOT_RECEIVED,
        evidence_progress=_Progress(),
        customer_message="合成 selector 测试消息。",
    )


def _native_call(
    tool_name: str = "get_order_context",
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": tool_name,
        "args": arguments if arguments is not None else {"order_id": "ORD-001"},
        "id": "call-test",
        "type": "tool_call",
    }


def _response(calls: list[dict[str, Any]]) -> AIMessage:
    return AIMessage(content="", tool_calls=calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("call_count", "expected_reason"),
    [
        (0, "FINALIZATION_REQUESTED"),
        (1, "MISSING_REQUIRED_EVIDENCE"),
        (2, "SELECTOR_MULTIPLE_TOOL_CALLS"),
        (6, "SELECTOR_MULTIPLE_TOOL_CALLS"),
    ],
)
async def test_live_selector_enforces_exactly_zero_or_one_tool_call(
    call_count: int,
    expected_reason: str,
) -> None:
    provider = _FakeProvider(_response([_native_call()] * call_count))

    if call_count in {0, 1}:
        candidate = await AgentObservationSelector(provider).select_next_observation(_context())
        assert candidate.reason_code.value == expected_reason
        assert provider.calls == 1
        if call_count == 0:
            assert candidate.action.value == "finish"
        else:
            assert candidate.action.value == "call_tool"
    else:
        with pytest.raises(SelectorSchemaFailure, match="more than one tool call") as exc_info:
            await AgentObservationSelector(provider).select_next_observation(_context())
        assert exc_info.value.reason_code == expected_reason
        assert provider.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("call", "reason"),
    [
        (
            _native_call("create_logistics_investigation_ticket"),
            "SELECTOR_TOOL_NAME_NOT_ALLOWLISTED",
        ),
        (
            _native_call("get_order_context", {"order_id": "ORD-001", "extra": "reject"}),
            "SELECTOR_TOOL_ARGUMENT_FIELDS_INVALID",
        ),
        (_native_call("get_order_context", []), "SELECTOR_TOOL_ARGS_NOT_OBJECT"),
    ],
)
async def test_live_selector_rejects_unknown_or_malformed_calls(
    call: dict[str, Any],
    reason: str,
) -> None:
    provider = _FakeProvider(
        AIMessage.model_construct(content="", tool_calls=[call], invalid_tool_calls=[])
    )

    with pytest.raises(SelectorSchemaFailure) as exc_info:
        await AgentObservationSelector(provider).select_next_observation(_context())

    assert exc_info.value.reason_code == reason
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_live_selector_accepts_native_and_function_shaped_provider_calls() -> None:
    native_provider = _FakeProvider(_response([_native_call()]))
    native_candidate = await AgentObservationSelector(native_provider).select_next_observation(
        _context()
    )
    assert native_candidate.tool_name == "get_order_context"
    assert native_candidate.arguments == {"order_id": "ORD-001"}

    function_response = AIMessage(
        content="",
        additional_kwargs={
            "tool_calls": [
                {
                    "id": "function-call",
                    "type": "function",
                    "function": {
                        "name": "get_order_context",
                        "arguments": json.dumps({"order_id": "ORD-001"}),
                    },
                }
            ]
        },
    )
    function_provider = _FakeProvider(function_response)
    function_candidate = await AgentObservationSelector(function_provider).select_next_observation(
        _context()
    )
    assert function_candidate.tool_name == "get_order_context"
    assert function_candidate.arguments == {"order_id": "ORD-001"}

    tool_name, arguments, requirement = parse_live_selector_tool_call(
        {
            "function": {
                "name": "search_after_sales_policy",
                "arguments": json.dumps(
                    {"order_id": "ORD-001", "issue_type": "signed_not_received"}
                ),
            }
        }
    )
    assert tool_name == "search_after_sales_policy"
    assert arguments["issue_type"] == "signed_not_received"
    assert requirement.value == "POLICY_APPLICABILITY"


@pytest.mark.asyncio
async def test_live_selector_rejects_non_ai_and_invalid_native_shape() -> None:
    non_ai_provider = _FakeProvider(SimpleNamespace(tool_calls=[]))
    with pytest.raises(SelectorSchemaFailure) as non_ai_error:
        await AgentObservationSelector(non_ai_provider).select_next_observation(_context())
    assert non_ai_error.value.reason_code == "SELECTOR_RESPONSE_NOT_AI_MESSAGE"

    invalid_shape = AIMessage.model_construct(
        content="",
        tool_calls=[{"name": "get_order_context", "args": {"order_id": "ORD-001"}}],
        invalid_tool_calls=[{"raw": "invalid"}],
    )
    invalid_provider = _FakeProvider(invalid_shape)
    with pytest.raises(SelectorSchemaFailure) as invalid_error:
        await AgentObservationSelector(invalid_provider).select_next_observation(_context())
    assert invalid_error.value.reason_code == "SELECTOR_INVALID_NATIVE_TOOL_CALL"


@pytest.mark.asyncio
async def test_gate_ready_live_selector_uses_unbound_model_for_legal_finish() -> None:
    class _FinishModel:
        def __init__(self) -> None:
            self.calls = 0

        async def ainvoke(self, _: object) -> AIMessage:
            self.calls += 1
            return AIMessage(content="完成。")

    finish_model = _FinishModel()

    class _BoundProvider:
        bound = finish_model

        async def ainvoke(self, _: object) -> AIMessage:
            raise AssertionError("tool-bound model must not be used for legal finish")

    ready_context = SimpleNamespace(
        authorized_order_id="ORD-001",
        canonical_issue_type=IssueType.SIGNED_NOT_RECEIVED,
        evidence_progress=SimpleNamespace(
            gate_readiness=SimpleNamespace(value="evaluable"),
            model_dump=lambda **_: {"gate_readiness": "evaluable"},
        ),
        customer_message="合成 selector 测试消息。",
    )

    candidate = await AgentObservationSelector(_BoundProvider()).select_next_observation(
        ready_context
    )

    assert candidate.action.value == "finish"
    assert finish_model.calls == 1


def test_live_model_binding_disables_parallel_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BoundModel:
        def __init__(self) -> None:
            self.tools: list[Any] | None = None
            self.kwargs: dict[str, Any] | None = None

        def bind_tools(self, tools: list[Any], **kwargs: Any) -> object:
            self.tools = tools
            self.kwargs = kwargs
            return self

    bound = _BoundModel()
    monkeypatch.setattr(models, "build_live_model", lambda _: bound)
    settings = Settings(
        _env_file=None,
        LLM_MODE="live",
        DEEPSEEK_API_KEY="test-presence-only",
        DEEPSEEK_MODEL="deepseek-v4-flash",
    )

    result = models.build_investigation_model(settings, READ_TOOLS)

    assert result is bound
    assert bound.tools == list(READ_TOOLS)
    assert bound.kwargs == {"tool_choice": "auto", "parallel_tool_calls": False}

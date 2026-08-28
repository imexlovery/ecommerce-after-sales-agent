from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage

import after_sales_agent.agents.models as models
from after_sales_agent.agents.models import AgentObservationSelector
from after_sales_agent.agents.tool_bindings import READ_TOOLS
from after_sales_agent.application.adaptive_core import (
    EvidenceRequirementCode,
    NextObservationCandidate,
    ObservationAction,
    ObservationReasonCode,
)
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


def _candidate(
    *,
    action: ObservationAction = ObservationAction.CALL_TOOL,
    tool_name: str | None = "get_order_context",
    addresses: tuple[EvidenceRequirementCode, ...] = (EvidenceRequirementCode.ORDER_STATUS,),
) -> NextObservationCandidate:
    return NextObservationCandidate(
        action=action,
        tool_name=tool_name,
        addresses=addresses,
        reason_code=(
            ObservationReasonCode.FINALIZATION_REQUESTED
            if action is ObservationAction.FINISH
            else ObservationReasonCode.MISSING_REQUIRED_EVIDENCE
        ),
    )


def _structured_response(
    candidate: NextObservationCandidate | None = None,
    *,
    call_count: int = 1,
    parsing_error: BaseException | None = None,
    parsed: Any = None,
    raw: Any = None,
) -> dict[str, Any]:
    value = candidate or _candidate()
    raw_message = raw or AIMessage(
        content="",
        tool_calls=[
            {
                "name": "NextObservationCandidate",
                "args": value.model_dump(mode="json"),
                "id": f"candidate-{index}",
                "type": "tool_call",
            }
            for index in range(call_count)
        ],
    )
    return {
        "raw": raw_message,
        "parsed": value if parsed is None else parsed,
        "parsing_error": parsing_error,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("candidate", "expected_action", "expected_tool"),
    [
        (_candidate(), "call_tool", "get_order_context"),
        (
            _candidate(
                action=ObservationAction.FINISH,
                tool_name=None,
                addresses=(),
            ),
            "finish",
            None,
        ),
    ],
)
async def test_live_selector_accepts_one_structured_candidate(
    candidate: NextObservationCandidate,
    expected_action: str,
    expected_tool: str | None,
) -> None:
    provider = _FakeProvider(_structured_response(candidate))

    selected = await AgentObservationSelector(provider).select_next_observation(_context())

    assert selected.action.value == expected_action
    assert selected.tool_name == expected_tool
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_live_selector_rejects_multiple_structured_candidates_before_parsing() -> None:
    provider = _FakeProvider(_structured_response(call_count=2))

    with pytest.raises(
        SelectorSchemaFailure, match="more than one structured candidate"
    ) as exc_info:
        await AgentObservationSelector(provider).select_next_observation(_context())

    assert exc_info.value.reason_code == "SELECTOR_MULTIPLE_TOOL_CALLS"
    assert provider.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response_factory", "reason"),
    [
        (lambda: AIMessage(content=""), "SELECTOR_RESPONSE_NOT_STRUCTURED"),
        (
            lambda: {"raw": AIMessage(content=""), "parsed": None},
            "SELECTOR_STRUCTURED_RESPONSE_FIELDS_INVALID",
        ),
        (
            lambda: _structured_response(
                parsing_error=json.JSONDecodeError("invalid", "{", 0),
                parsed=None,
            ),
            "SELECTOR_INVALID_JSON",
        ),
        (
            lambda: _structured_response(parsed=[]),
            "SELECTOR_STRUCTURED_SCHEMA_INVALID",
        ),
        (
            lambda: _structured_response(
                parsing_error=ValueError("extra field: arguments"),
                parsed=None,
            ),
            "SELECTOR_STRUCTURED_SCHEMA_INVALID",
        ),
        (
            lambda: _structured_response(
                parsing_error=ValueError("tool_name is not an allowlisted value"),
                parsed=None,
            ),
            "SELECTOR_STRUCTURED_SCHEMA_INVALID",
        ),
    ],
)
async def test_live_selector_fails_closed_for_malformed_structured_output(
    response_factory: Any,
    reason: str,
) -> None:
    provider = _FakeProvider(response_factory())

    with pytest.raises(SelectorSchemaFailure) as exc_info:
        await AgentObservationSelector(provider).select_next_observation(_context())

    assert exc_info.value.reason_code == reason
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_live_selector_rejects_empty_and_invalid_structured_calls() -> None:
    empty_provider = _FakeProvider(
        {
            "raw": AIMessage(content="", tool_calls=[]),
            "parsed": None,
            "parsing_error": None,
        }
    )
    with pytest.raises(SelectorSchemaFailure) as empty_error:
        await AgentObservationSelector(empty_provider).select_next_observation(_context())
    assert empty_error.value.reason_code == "SELECTOR_EMPTY_STRUCTURED_OUTPUT"

    invalid_raw = AIMessage.model_construct(
        content="",
        tool_calls=[
            {
                "name": "NextObservationCandidate",
                "args": {},
                "id": "candidate-invalid",
                "type": "tool_call",
            }
        ],
        invalid_tool_calls=[{"raw": "invalid"}],
    )
    invalid_provider = _FakeProvider(_structured_response(raw=invalid_raw, parsed=None))
    with pytest.raises(SelectorSchemaFailure) as invalid_error:
        await AgentObservationSelector(invalid_provider).select_next_observation(_context())
    assert invalid_error.value.reason_code == "SELECTOR_INVALID_STRUCTURED_CALL"


def test_live_model_uses_one_structured_schema_without_read_tool_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _StructuredModel:
        def __init__(self) -> None:
            self.schema: Any = None
            self.kwargs: dict[str, Any] | None = None

        def with_structured_output(self, schema: Any, **kwargs: Any) -> object:
            self.schema = schema
            self.kwargs = kwargs
            return object()

        def bind_tools(self, *_: Any, **__: Any) -> object:
            raise AssertionError("selector must not bind the six read tools")

    model = _StructuredModel()
    monkeypatch.setattr(models, "build_live_model", lambda _: model)
    settings = Settings(
        _env_file=None,
        LLM_MODE="live",
        DEEPSEEK_API_KEY="test-presence-only",
        DEEPSEEK_MODEL="deepseek-v4-flash",
    )

    result = models.build_investigation_model(settings, READ_TOOLS)

    assert result is not model
    assert model.schema is NextObservationCandidate
    assert model.kwargs == {
        "method": "function_calling",
        "include_raw": True,
    }

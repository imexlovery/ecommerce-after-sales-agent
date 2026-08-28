from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import after_sales_agent.evals.v3.a0_rescue as rescue
from after_sales_agent.application.adaptive_core import (
    EvidenceRequirementCode,
    NextObservationCandidate,
    ObservationAction,
    ObservationReasonCode,
)
from after_sales_agent.application.provider_budget import ProviderBudgetAdmissionRejected
from after_sales_agent.config import Settings
from after_sales_agent.domain.state import IssueType


class _ResponseProvider:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls = 0
        self.messages: list[Any] = []

    async def ainvoke(self, messages: list[Any]) -> Any:
        self.calls += 1
        self.messages.append(messages)
        response = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        if isinstance(response, BaseException):
            raise response
        return response


class _HttpError(Exception):
    status_code = 503


class _Progress:
    gate_readiness = SimpleNamespace(value="not_evaluable")

    def model_dump(self, *, mode: str) -> dict[str, Any]:
        assert mode == "json"
        return {"gate_readiness": "not_evaluable", "missing_required_codes": ["ORDER_STATUS"]}


def _context() -> Any:
    return SimpleNamespace(
        authorized_order_id="ORD-RESCUE-001",
        canonical_issue_type=IssueType.SIGNED_NOT_RECEIVED,
        evidence_progress=_Progress(),
        customer_message="合成 Rescue 测试消息。",
    )


def _candidate() -> NextObservationCandidate:
    return NextObservationCandidate(
        action=ObservationAction.CALL_TOOL,
        tool_name="get_order_context",
        addresses=(EvidenceRequirementCode.ORDER_STATUS,),
        reason_code=ObservationReasonCode.FIRST_REQUIRED_OBSERVATION,
    )


def _settings() -> Settings:
    return Settings(
                _env_file=None,
                LLM_MODE="mock",
        DEEPSEEK_API_KEY="test-presence-only",
        DEEPSEEK_MODEL="deepseek-v4-flash",
    )


@pytest.mark.asyncio
async def test_rescue_observer_passes_messages_and_retains_budget_boundary(tmp_path: Path) -> None:
    ledger = rescue.RescueLedger(tmp_path / "security-ledger.jsonl")
    budget = rescue._RescueCallBudget()
    provider = _ResponseProvider([AIMessage(content="ok")])
    observer = rescue.RescueProviderInvocationObserver(
        ledger=ledger,
        budget=budget,
        smoke_id="A0-01",
        source_revision="a" * 40,
        timeout_seconds=1.0,
        transport="function_calling",
        run_id="run-a0-01",
    )
    messages = [HumanMessage(content="safe test message")]

    await observer.invoke(model=provider, messages=messages, context=SimpleNamespace())

    assert provider.calls == 1
    assert provider.messages == [messages]
    assert budget.attempts == 1
    assert [event.event_type for event in ledger.events] == [
        "budget_admission",
        "outbound_attempt",
        "provider_completed_response",
    ]

    budget.attempts = rescue.RESCUE_PROVIDER_CALL_CEILING
    with pytest.raises(ProviderBudgetAdmissionRejected):
        await observer.invoke(model=provider, messages=messages, context=SimpleNamespace())
    assert provider.calls == 1
    assert ledger.events[-1].category is rescue.RescueErrorCategory.BUDGET_ADMISSION
    assert ledger.events[-1].status == "blocked"


def test_rescue_provider_errors_are_safely_classified() -> None:
    category, code, status = rescue._classify_provider_exception(_HttpError())
    assert category is rescue.RescueErrorCategory.PROVIDER_HTTP_ERROR
    assert code == "RESCUE_PROVIDER_HTTP_ERROR"
    assert status == 503

    category, code, status = rescue._classify_provider_exception(TimeoutError())
    assert category is rescue.RescueErrorCategory.TIMEOUT
    assert code == "RESCUE_PROVIDER_TIMEOUT"
    assert status is None

    category, code, status = rescue._classify_provider_exception(ImportError())
    assert category is rescue.RescueErrorCategory.LOCAL_TRANSPORT_CONFIGURATION_ERROR
    assert code == "RESCUE_LOCAL_TRANSPORT_ERROR"
    assert status is None


@pytest.mark.asyncio
async def test_rescue_selector_allows_one_json_transport_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate()
    primary = _ResponseProvider([AIMessage(content="not a structured envelope")])
    json_provider = _ResponseProvider(
        [AIMessage(content=json.dumps(candidate.model_dump(mode="json"), ensure_ascii=False))]
    )
    monkeypatch.setattr(rescue, "build_investigation_model", lambda *_: primary)
    monkeypatch.setattr(rescue, "build_live_model", lambda *_: json_provider)
    ledger = rescue.RescueLedger(tmp_path / "security-ledger.jsonl")
    budget = rescue._RescueCallBudget()
    selector = rescue.RescueSelector(
        settings=_settings(),
        ledger=ledger,
        budget=budget,
        smoke_id="A0-01",
        source_revision="b" * 40,
        run_id="run-a0-01",
    )

    result = await selector.select_next_observation(_context())

    assert result == candidate
    assert selector.fallback_used is True
    assert budget.attempts == 2
    assert primary.calls == 1
    assert json_provider.calls == 1
    rejection_events = [
        event for event in ledger.events if event.event_type == "candidate_boundary_rejection"
    ]
    assert len(rejection_events) == 1
    assert rejection_events[0].fallback_attempted is True
    assert rejection_events[0].error_code == "SELECTOR_RESPONSE_NOT_STRUCTURED"


@pytest.mark.asyncio
@pytest.mark.parametrize("smoke_id", ["A0-01", "A0-02", "A0-03"])
async def test_rescue_smoke_uses_production_graph_without_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    smoke_id: str,
) -> None:
    class _StructuredProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def ainvoke(self, _: object) -> dict[str, Any]:
            self.calls += 1
            candidate = _candidate()
            payload = candidate.model_dump(mode="json")
            return {
                "raw": AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "NextObservationCandidate",
                            "args": payload,
                            "id": f"candidate-{self.calls}",
                            "type": "tool_call",
                        }
                    ],
                ),
                "parsed": candidate,
                "parsing_error": None,
            }

    provider = _StructuredProvider()
    monkeypatch.setattr(rescue, "build_investigation_model", lambda *_: provider)
    monkeypatch.setattr(rescue, "build_live_model", lambda *_: provider)

    def _fake_settings(_: Path, root: Path, spec: Any) -> Settings:
        return Settings(
            _env_file=None,
                LLM_MODE="mock",
            DEEPSEEK_API_KEY="test-presence-only",
            DEEPSEEK_MODEL="deepseek-v4-flash",
            POLICY_RETRIEVAL_MODE="fake_test",
            DATABASE_URL=f"sqlite:///{(root / 'application.sqlite').as_posix()}",
            LANGGRAPH_CHECKPOINT_URL=root / "langgraph-checkpoints.sqlite",
            POLICY_INDEX_ROOT=root / "policy-index",
            POLICY_RETRIEVAL_EVAL_ARTIFACT_ROOT=root / "retrieval-evals",
            EVAL_ARTIFACT_ROOT=root / "eval-artifacts",
            SCENARIO_FAULT_SEED=spec.fault_seed,
            SCENARIO_EVALUATED_AT=datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
        )

    monkeypatch.setattr(rescue, "_safe_live_settings", _fake_settings)
    template, template_digest = rescue.load_rescue_template()
    manifest = rescue.RescueManifest(
        source_revision="d" * 40,
        template_digest=template_digest,
        input_digest="e" * 64,
        smoke_cases=template.smoke_cases,
    )
    spec = next(item for item in manifest.smoke_cases if item.smoke_id == smoke_id)
    ledger = rescue.RescueLedger(tmp_path / "security-ledger.jsonl")
    budget = rescue._RescueCallBudget()

    result = await rescue._run_smoke(
        project_root=tmp_path,
        manifest=manifest,
        spec=spec,
        ledger=ledger,
        budget=budget,
        runtime_root=tmp_path / smoke_id,
    )

    expected_reads = 2 if smoke_id == "A0-03" else 1
    assert result.status == "passed", result.model_dump_json()
    assert result.provider_calls == 1
    assert result.provider_completed_responses == 1
    assert result.actual_read_executions == expected_reads
    assert result.toolnode_node_present is True
    assert result.toolnode_reached is True
    assert result.typed_tool_result_count == expected_reads
    assert len(result.initial_missing_required_codes) >= 2
    assert provider.calls == 1
    if smoke_id == "A0-03":
        assert result.retry_identity_match is True
        assert result.retry_attempt_numbers == (1, 2)
        assert result.router_routes[0] == "retry_exact"


def test_rescue_manifest_template_is_fixed_and_write_once(tmp_path: Path) -> None:
    template_source = (
        Path(__file__).resolve().parents[2]
        / "evals"
        / "v3"
        / "a0-rescue-manifest.template.json"
    )
    template_target = tmp_path / "evals" / "v3" / template_source.name
    template_target.parent.mkdir(parents=True)
    template_target.write_bytes(template_source.read_bytes())

    manifest, digest, manifest_path = rescue.prepare_rescue_manifest(
        tmp_path,
        source_revision="c" * 40,
    )

    assert manifest.execution_identity == rescue.RESCUE_IDENTITY
    assert tuple(item.smoke_id for item in manifest.smoke_cases) == ("A0-01", "A0-02", "A0-03")
    assert manifest.provider_call_ceiling == 6
    assert manifest.automatic_retry is False
    assert len(digest) == 64
    assert manifest_path.exists()

    second_manifest, second_digest, _ = rescue.prepare_rescue_manifest(
        tmp_path,
        source_revision="c" * 40,
    )
    assert second_manifest == manifest
    assert second_digest == digest


def test_rescue_preflight_without_credential_makes_no_provider_attempts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    preflight = rescue.run_rescue_preflight(tmp_path)

    assert preflight.status == "blocked"
    assert preflight.provider_calls == 0
    assert preflight.network_requests == 0
    assert preflight.credential_present is False
    assert (
        preflight.error_category
        is rescue.RescueErrorCategory.LOCAL_TRANSPORT_CONFIGURATION_ERROR
    )
    assert preflight.error_code == "RESCUE_CREDENTIAL_MISSING"

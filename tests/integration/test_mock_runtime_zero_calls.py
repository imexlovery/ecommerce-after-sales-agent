from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from langchain_deepseek import ChatDeepSeek

from after_sales_agent.agents.models import MockInvestigationModel
from after_sales_agent.api.app import create_app
from after_sales_agent.config import Settings


def test_explicit_mock_runtime_has_zero_provider_and_model_calls(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls = {"provider_calls": 0, "model_calls": 0}

    async def fail_if_provider_called(*_: Any, **__: Any) -> None:
        calls["provider_calls"] += 1
        raise AssertionError("Mock runtime attempted a provider call")

    async def fail_if_model_called(*_: Any, **__: Any) -> None:
        calls["model_calls"] += 1
        raise AssertionError("Mock runtime attempted a model call")

    monkeypatch.setattr(ChatDeepSeek, "ainvoke", fail_if_provider_called)
    monkeypatch.setattr(MockInvestigationModel, "ainvoke", fail_if_model_called)
    settings = Settings(
        _env_file=None,
        LLM_MODE="mock",
        POLICY_RETRIEVAL_MODE="fake_test",
        DATABASE_URL=f"sqlite:///{tmp_path / 'business.db'}",
        LANGGRAPH_CHECKPOINT_URL=tmp_path / "checkpoints.db",
        EVAL_ARTIFACT_ROOT=tmp_path / "evals",
        POLICY_INDEX_ROOT=tmp_path / "policy-index",
        POLICY_RETRIEVAL_EVAL_ARTIFACT_ROOT=tmp_path / "retrieval-evals",
    )

    with TestClient(create_app(settings)) as client:
        conversation = client.post(
            "/v1/conversations",
            json={"fixture_customer_key": "customer_a"},
        )
        assert conversation.status_code == 201
        run = client.post(
            f"/v1/conversations/{conversation.json()['conversation_id']}/messages",
            json={"content": "我的 ORD-001 显示签收了，但我没有收到"},
        )
        assert run.status_code == 202

    assert calls == {"provider_calls": 0, "model_calls": 0}

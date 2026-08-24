from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from after_sales_agent.api.app import ApiRuntime, create_app
from after_sales_agent.config import Settings
from after_sales_agent.evals.contracts import EvalReport


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,
        LLM_MODE="mock",
        DATABASE_URL=f"sqlite:///{tmp_path / 'business.db'}",
        LANGGRAPH_CHECKPOINT_URL=tmp_path / "checkpoints.db",
        EVAL_ARTIFACT_ROOT=tmp_path / "evals",
        FRONTEND_ORIGIN="http://127.0.0.1:5173",
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _create_conversation(client: TestClient, customer: str = "customer_a") -> str:
    response = client.post(
        "/v1/conversations",
        json={"fixture_customer_key": customer},
    )
    assert response.status_code == 201
    return str(response.json()["conversation_id"])


def _start_signed_not_received_case(
    client: TestClient,
    conversation_id: str,
) -> tuple[str, str]:
    response = client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"content": "我的 ORD-001 显示签收了，但我没有收到"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["case_id"] is not None
    return str(body["run_id"]), str(body["case_id"])


def _assert_error_envelope(response: Any, *, code: str) -> None:
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert set(body["error"]) == {"code", "message", "retryable", "trace_id"}
    assert body["error"]["trace_id"]


def _sse_payloads(body: str) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def test_health_readiness_and_exact_local_cors(client: TestClient) -> None:
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}

    ready = client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ready",
        "llm_mode": "mock",
        "fixture_version": "fixture-v1",
        "business_store": "ready",
        "checkpoint_store": "ready",
        "provider_checked": False,
    }

    allowed = client.options(
        "/v1/conversations",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert allowed.status_code == 204
    assert allowed.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"

    rejected = client.options(
        "/v1/conversations",
        headers={
            "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert rejected.status_code == 403
    _assert_error_envelope(rejected, code="CORS_ORIGIN_NOT_ALLOWED")


def test_uniform_errors_and_conversation_read_model(client: TestClient) -> None:
    malformed = client.post("/v1/conversations", json={})
    assert malformed.status_code == 422
    _assert_error_envelope(malformed, code="REQUEST_VALIDATION_FAILED")

    unknown_customer = client.post(
        "/v1/conversations",
        json={"fixture_customer_key": "real-customer"},
    )
    assert unknown_customer.status_code == 422
    _assert_error_envelope(unknown_customer, code="UNKNOWN_FIXTURE_CUSTOMER")

    conversation_id = _create_conversation(client)
    read = client.get(f"/v1/conversations/{conversation_id}")
    assert read.status_code == 200
    assert read.json()["conversation_id"] == conversation_id
    assert read.json()["messages"] == []
    assert read.json()["cases"] == []
    assert read.json()["active_case_id"] is None

    missing = client.get("/v1/conversations/conv_missing")
    assert missing.status_code == 404
    _assert_error_envelope(missing, code="CONVERSATION_NOT_FOUND")


def test_message_run_case_confirmation_full_mock_path(client: TestClient) -> None:
    conversation_id = _create_conversation(client)
    message_run_id, case_id = _start_signed_not_received_case(client, conversation_id)

    message_run = client.get(f"/v1/runs/{message_run_id}")
    assert message_run.status_code == 200
    assert message_run.json()["run_kind"] == "message"
    assert message_run.json()["run_state"] == "succeeded"
    assert message_run.json()["planning_turn_count"] == 6
    assert message_run.json()["actual_read_tool_execution_count"] == 5

    case = client.get(f"/v1/investigation-cases/{case_id}")
    assert case.status_code == 200
    case_body = case.json()
    assert case_body["case_state"] == "awaiting_customer_confirmation"
    assert case_body["case_outcome"] is None
    assert case_body["canonical_issue_type"] == "signed_not_received"
    assert case_body["actual_read_tool_execution_count"] == 5
    proposal_id = str(case_body["active_proposal_id"])

    confirm = client.post(
        f"/v1/action-proposals/{proposal_id}/confirm",
        json={"proposal_version": 1},
    )
    assert confirm.status_code == 202
    assert confirm.json()["proposal_id"] == proposal_id
    assert confirm.json()["proposal_state"] == "confirmed"

    confirmation_run = client.get(f"/v1/runs/{confirm.json()['run_id']}")
    assert confirmation_run.status_code == 200
    assert confirmation_run.json()["run_kind"] == "confirmation"
    assert confirmation_run.json()["run_state"] == "succeeded"

    closed_case = client.get(f"/v1/investigation-cases/{case_id}")
    assert closed_case.status_code == 200
    assert closed_case.json()["case_state"] == "closed"
    assert closed_case.json()["case_outcome"] == "ticket_created"
    assert closed_case.json()["reason_code"] == "LOGISTICS_TICKET_CREATED_AND_VERIFIED"
    assert closed_case.json()["active_proposal_id"] is None


def test_decline_and_retry_are_real_run_routes(client: TestClient) -> None:
    conversation_id = _create_conversation(client)
    _, case_id = _start_signed_not_received_case(client, conversation_id)
    case = client.get(f"/v1/investigation-cases/{case_id}").json()
    proposal_id = str(case["active_proposal_id"])

    retry_while_confirmation_is_pending = client.post(
        f"/v1/investigation-cases/{case_id}/retry",
        json={},
    )
    assert retry_while_confirmation_is_pending.status_code == 409
    _assert_error_envelope(
        retry_while_confirmation_is_pending,
        code="CASE_NOT_RETRYABLE",
    )

    decline = client.post(
        f"/v1/action-proposals/{proposal_id}/decline",
        json={"proposal_version": 1},
    )
    assert decline.status_code == 202
    assert decline.json()["proposal_state"] == "declined"
    decline_run = client.get(f"/v1/runs/{decline.json()['run_id']}").json()
    assert decline_run["run_kind"] == "decline"
    assert decline_run["run_state"] == "succeeded"

    closed_case = client.get(f"/v1/investigation-cases/{case_id}").json()
    assert closed_case["case_state"] == "closed"
    assert closed_case["case_outcome"] == "resolved_no_action"
    assert closed_case["reason_code"] == "CUSTOMER_DECLINED_PROPOSAL"


def test_sse_replays_persisted_events_without_executing_work(client: TestClient) -> None:
    conversation_id = _create_conversation(client)
    response = client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"content": "今天天气如何？"},
    )
    assert response.status_code == 202
    assert response.json()["case_id"] is None

    runtime = cast(Any, client.app).state.runtime
    assert isinstance(runtime, ApiRuntime)
    before = runtime.events.list_after(conversation_id)
    assert before

    replay = client.get(f"/v1/conversations/{conversation_id}/events?follow=false")
    assert replay.status_code == 200
    assert replay.headers["content-type"].startswith("text/event-stream")
    payloads = _sse_payloads(replay.text)
    assert [payload["event_id"] for payload in payloads] == [event.event_id for event in before]
    assert [payload["sequence"] for payload in payloads] == list(range(1, len(before) + 1))
    assert len(runtime.events.list_after(conversation_id)) == len(before)

    resumed = client.get(
        f"/v1/conversations/{conversation_id}/events?follow=false",
        headers={"Last-Event-ID": before[0].event_id},
    )
    resumed_ids = [payload["event_id"] for payload in _sse_payloads(resumed.text)]
    assert resumed_ids == [event.event_id for event in before[1:]]
    assert len(runtime.events.list_after(conversation_id)) == len(before)

    other_conversation_id = _create_conversation(client, customer="customer_b")
    invalid_cursor = client.get(
        f"/v1/conversations/{other_conversation_id}/events?follow=false",
        headers={"Last-Event-ID": before[0].event_id},
    )
    assert invalid_cursor.status_code == 404
    _assert_error_envelope(invalid_cursor, code="EVENT_CURSOR_NOT_FOUND")


def test_multi_case_replay_preserves_one_event_timeline_without_new_side_effects(
    client: TestClient,
) -> None:
    conversation_id = _create_conversation(client)
    _, first_case_id = _start_signed_not_received_case(client, conversation_id)
    first_case = client.get(f"/v1/investigation-cases/{first_case_id}").json()
    confirmed = client.post(
        f"/v1/action-proposals/{first_case['active_proposal_id']}/confirm",
        json={"proposal_version": 1},
    )
    assert confirmed.status_code == 202

    repeated = client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"content": "ORD-001 显示签收但我还是没有收到，请继续查询。"},
    )
    assert repeated.status_code == 202
    repeated_case_id = str(repeated.json()["case_id"])
    repeated_case = client.get(f"/v1/investigation-cases/{repeated_case_id}").json()
    assert repeated_case["case_state"] == "closed"
    assert repeated_case["reason_code"] == "ACTIVE_LOGISTICS_TICKET_EXISTS"

    second = client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"content": "ORD-003 的物流很久没有更新了。"},
    )
    assert second.status_code == 202
    second_case_id = str(second.json()["case_id"])
    second_case = client.get(f"/v1/investigation-cases/{second_case_id}").json()
    assert second_case["case_state"] == "awaiting_customer_confirmation"

    runtime = cast(Any, client.app).state.runtime
    assert isinstance(runtime, ApiRuntime)
    before = runtime.events.list_after(conversation_id)
    replay = client.get(f"/v1/conversations/{conversation_id}/events?follow=false")
    replayed = _sse_payloads(replay.text)
    assert [payload["event_id"] for payload in replayed] == [event.event_id for event in before]
    assert [payload["sequence"] for payload in replayed] == list(range(1, len(before) + 1))
    assert len(runtime.events.list_after(conversation_id)) == len(before)

    verified_index = next(
        index
        for index, event in enumerate(before)
        if event.case_id == first_case_id and event.event_type == "action_verified"
    )
    second_message_index = next(
        index
        for index, event in enumerate(before)
        if event.run_id == second.json()["run_id"] and event.event_type == "message_received"
    )
    second_proposal_index = next(
        index
        for index, event in enumerate(before)
        if event.case_id == second_case_id and event.event_type == "proposal_created"
    )
    assert verified_index < second_message_index < second_proposal_index
    conversation = client.get(f"/v1/conversations/{conversation_id}").json()
    assert [case["case_id"] for case in conversation["cases"]] == [
        first_case_id,
        repeated_case_id,
        second_case_id,
    ]
    assert conversation["active_case_id"] == second_case_id


def test_demo_reset_preserves_immutable_eval_report(client: TestClient) -> None:
    conversation_id = _create_conversation(client)
    _start_signed_not_received_case(client, conversation_id)

    report_before = client.get("/v1/evals/latest")
    assert report_before.status_code == 404
    _assert_error_envelope(report_before, code="EVAL_REPORT_NOT_FOUND")

    runtime = cast(ApiRuntime, client.app.state.runtime)
    runtime.eval_store.save_report(
        EvalReport(
            report_id="eval_test_immutable",
            evaluation_revision="test-r1",
            created_at=datetime(2026, 8, 24, tzinfo=UTC),
            dataset_partition="locked",
            versions={"fixture": "fixture-v1"},
            safety_gate_pass=True,
            acceptance_gate_pass=True,
            sections={
                section: {"status": "pass"}
                for section in (
                    "safety",
                    "task_quality",
                    "tool_trajectory",
                    "stability",
                    "latency",
                    "token",
                    "cost",
                    "agent_vs_workflow",
                )
            },
            architecture_conclusion="KEEP_EXPERIMENTAL",
            raw_run_count=132,
        )
    )
    stored_report = client.get("/v1/evals/latest")
    assert stored_report.status_code == 200
    assert stored_report.json()["report_id"] == "eval_test_immutable"

    reset = client.post("/v1/demo/reset")
    assert reset.status_code == 204
    assert reset.content == b""

    missing = client.get(f"/v1/conversations/{conversation_id}")
    assert missing.status_code == 404
    _assert_error_envelope(missing, code="CONVERSATION_NOT_FOUND")
    assert client.get("/v1/evals/latest").json() == stored_report.json()

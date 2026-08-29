from __future__ import annotations

from pathlib import Path
from typing import cast

from fastapi.testclient import TestClient

from after_sales_agent.api.app import ApiRuntime, create_app
from after_sales_agent.config import Settings
from after_sales_agent.storage.repositories import Repository


def _client(tmp_path: Path, profile: str) -> TestClient:
    return TestClient(
        create_app(
            Settings(
                _env_file=None,
                LLM_MODE="mock",
                SYNTHETIC_FAULT_PROFILE=profile,
                POLICY_RETRIEVAL_MODE="fake_test",
                DATABASE_URL=f"sqlite:///{tmp_path / (profile + '.db')}",
                LANGGRAPH_CHECKPOINT_URL=tmp_path / (profile + '.checkpoints.db'),
                EVAL_ARTIFACT_ROOT=tmp_path / "evals",
                POLICY_INDEX_ROOT=tmp_path / "policy-index",
                POLICY_RETRIEVAL_EVAL_ARTIFACT_ROOT=tmp_path / "retrieval-evals",
            )
        )
    )


def _submit(client: TestClient, customer_key: str, message: str) -> tuple[ApiRuntime, str, str]:
    created = client.post(
        "/v1/conversations",
        json={"fixture_customer_key": customer_key},
    )
    assert created.status_code == 201
    conversation_id = created.json()["conversation_id"]
    submitted = client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"content": message},
    )
    assert submitted.status_code == 202
    case_id = submitted.json()["case_id"]
    assert case_id is not None
    return cast(ApiRuntime, client.app.state.runtime), conversation_id, case_id


def _tool_calls(runtime: ApiRuntime, case_id: str):
    with runtime.database.session_factory() as session:
        return Repository(session).list_tool_calls(case_id=case_id)


def test_failure_lab_retry_profiles_preserve_exact_read_retries(tmp_path: Path) -> None:
    cases = (
        (
            "pod_timeout_once",
            "customer_a",
            "ORD-001 显示签收了，但我没有收到。",
            "get_delivery_proof",
        ),
        (
            "timeline_retry",
            "customer_a",
            "ORD-003 的物流一直没有更新，请帮我看看。",
            "get_logistics_timeline",
        ),
    )
    for profile, customer_key, message, tool_name in cases:
        with _client(tmp_path, profile) as client:
            runtime, _, case_id = _submit(client, customer_key, message)
            calls = [call for call in _tool_calls(runtime, case_id) if call.tool_name == tool_name]
            assert [call.attempt_number for call in calls] == [1, 2]
            assert all(call.actual_execution for call in calls)
            assert all(call.retryable for call in calls[:1])


def test_failure_lab_persistent_unavailable_and_policy_conflict_close_without_proposal(
    tmp_path: Path,
) -> None:
    for profile in ("policy_unavailable", "policy_conflict"):
        with _client(tmp_path, profile) as client:
            runtime, _, case_id = _submit(
                client,
                "customer_a",
                "ORD-001 显示签收了，但我没有收到。",
            )
            case = runtime.application.get_case(case_id)
            assert case["case_state"] == "closed"
            assert case["case_outcome"] == "human_support_required"
            assert case["customer_disposition"] == "ESCALATE"
            with runtime.database.session_factory() as session:
                assert Repository(session).list_proposals(case_id) == []


def test_failure_lab_carrier_terminal_failure_is_safe_and_ticket_uncertain_keeps_identity(
    tmp_path: Path,
) -> None:
    with _client(tmp_path, "carrier_terminal") as client:
        runtime, _, case_id = _submit(
            client,
            "customer_a",
            "ORD-003 的物流一直没有更新，请帮我看看。",
        )
        case = runtime.application.get_case(case_id)
        assert case["case_state"] == "closed"
        assert case["case_outcome"] == "human_support_required"
        assert case["customer_disposition"] == "ESCALATE"

    with _client(tmp_path, "ticket_uncertain") as client:
        runtime, conversation_id, case_id = _submit(
            client,
            "customer_a",
            "ORD-001 显示签收了，但我没有收到。",
        )
        proposal = next(
            event
            for event in runtime.events.list_after(conversation_id)
            if event.event_type == "proposal_created"
        )
        proposal_id = str(proposal.payload["proposal_id"])
        proposal_version = int(proposal.payload["proposal_version"])
        confirmed = client.post(
            f"/v1/action-proposals/{proposal_id}/confirm",
            json={"proposal_version": proposal_version},
        )
        assert confirmed.status_code == 202
        case = runtime.application.get_case(case_id)
        assert case["case_state"] == "closed"
        assert case["case_outcome"] == "uncertain"
        assert case["customer_disposition"] == "ESCALATE"
        with runtime.database.session_factory() as session:
            repository = Repository(session)
            actions = repository.list_actions(case_id)
            assert len(actions) == 1
            assert actions[0].action_state == "uncertain"
            assert repository.list_tickets(case_id=case_id) == []

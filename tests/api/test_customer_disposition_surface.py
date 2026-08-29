from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from after_sales_agent.api.app import ApiRuntime, create_app
from after_sales_agent.config import Settings
from after_sales_agent.domain.models import InvestigationCase
from after_sales_agent.domain.state import CaseOutcome, CaseState, IssueType
from after_sales_agent.storage.repositories import Repository


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
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
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_api_case_read_model_projects_all_five_dispositions(client: TestClient) -> None:
    created = client.post(
        "/v1/conversations",
        json={"fixture_customer_key": "customer_a"},
    )
    assert created.status_code == 201
    conversation_id = created.json()["conversation_id"]
    runtime = cast(ApiRuntime, client.app.state.runtime)
    cases = (
        ("answer", CaseState.CLOSED, CaseOutcome.RESOLVED_NO_ACTION, "EXPLAINED_NO_ACTION"),
        ("wait", CaseState.AWAITING_RETRY, None, None),
        ("clarify", CaseState.AWAITING_CUSTOMER_INPUT, None, None),
        (
            "investigate",
            CaseState.CLOSED,
            CaseOutcome.TICKET_CREATED,
            "LOGISTICS_TICKET_CREATED_AND_VERIFIED",
        ),
        (
            "escalate",
            CaseState.CLOSED,
            CaseOutcome.HUMAN_SUPPORT_REQUIRED,
            "HUMAN_SUPPORT_REQUIRED",
        ),
    )
    case_ids: list[str] = []
    with runtime.database.session_factory() as session, session.begin():
        repository = Repository(session)
        for suffix, state, outcome, reason in cases:
            case_id = f"disposition-{suffix}"
            repository.create_case(
                InvestigationCase(
                    case_id=case_id,
                    conversation_id=conversation_id,
                    customer_id="customer_a",
                    authorized_order_id="ORD-001",
                    canonical_issue_type=IssueType.SIGNED_NOT_RECEIVED,
                    case_state=state,
                    case_outcome=outcome,
                    reason_code=reason,
                )
            )
            case_ids.append(case_id)

    observed = {
        client.get(f"/v1/investigation-cases/{case_id}").json()["customer_disposition"]
        for case_id in case_ids
    }
    assert observed == {"ANSWER", "WAIT", "CLARIFY", "INVESTIGATE", "ESCALATE"}


def test_demo_catalog_surface_exposes_scenarios_and_failure_lab(client: TestClient) -> None:
    response = client.get("/v1/demo/catalog")

    assert response.status_code == 200
    body = response.json()
    assert body["fixture_version"] == "business-demo-v1"
    assert body["policy_clause_count"] == 10
    assert len(body["scenarios"]) == 22
    assert any(
        item["scenario_id"] == "stalled-active-investigation-d"
        and item["customer_key"] == "customer_d"
        and item["order_id"] == "ORD-025"
        for item in body["scenarios"]
    )
    assert {item["expected_disposition"] for item in body["scenarios"]} == {
        "ANSWER",
        "WAIT",
        "CLARIFY",
        "INVESTIGATE",
        "ESCALATE",
    }
    assert {item["fault_profile_id"] for item in body["fault_profiles"]} == {
        "pod-timeout-once",
        "timeline-retry",
        "pod-persistent-unavailable",
        "timeline-persistent-unavailable",
        "policy-unavailable",
        "ticket-uncertain",
    }


def test_split_shipment_confirm_endpoint_keeps_target_shipment_scope(
    client: TestClient,
) -> None:
    created = client.post(
        "/v1/conversations",
        json={"fixture_customer_key": "customer_r"},
    )
    assert created.status_code == 201
    conversation_id = created.json()["conversation_id"]
    submitted = client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={
            "content": (
                "ORD-039 只收到一部分，请重点核查 TRK-SYN-039-P03，"
                "这个包裹的物流没有更新。"
            )
        },
    )
    assert submitted.status_code == 202
    case_id = submitted.json()["case_id"]
    case = client.get(f"/v1/investigation-cases/{case_id}")
    assert case.status_code == 200
    case_body = case.json()
    assert case_body["target_shipment_id"] == "SHP-045"
    proposal_id = case_body["active_proposal_id"]

    confirmed = client.post(
        f"/v1/action-proposals/{proposal_id}/confirm",
        json={"proposal_version": 1},
    )
    assert confirmed.status_code == 202

    runtime = cast(ApiRuntime, client.app.state.runtime)
    with runtime.database.session_factory() as session:
        tickets = Repository(session).list_tickets(case_id=case_id)
    assert len(tickets) == 1
    assert tickets[0].target_shipment_id == "SHP-045"


def test_existing_investigation_api_projection_exposes_complete_business_contract(
    client: TestClient,
) -> None:
    created = client.post(
        "/v1/conversations",
        json={"fixture_customer_key": "customer_c"},
    )
    assert created.status_code == 201
    conversation_id = created.json()["conversation_id"]
    submitted = client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"content": "ORD-024 的物流没有更新，想知道现在处理到哪一步了。"},
    )
    assert submitted.status_code == 202
    case_id = submitted.json()["case_id"]

    body = client.get(f"/v1/investigation-cases/{case_id}").json()
    assert body["customer_disposition"] == "WAIT"
    assert body["active_tickets"] == []
    assert body["existing_investigations"] == [
        {
            "case_id": "SEED-CASE-001",
            "order_id": "ORD-024",
            "issue_type": "stalled_tracking",
            "status": "investigating",
            "stage": "carrier_follow_up",
            "opened_at": "2026-08-28T08:00:00Z",
            "last_updated_at": "2026-08-28T09:00:00Z",
            "next_update_at": "2026-08-30T09:00:00Z",
            "target_order_id": "ORD-024",
            "target_shipment_id": "SHP-024",
            "is_active": True,
        }
    ]
    messages = client.get(f"/v1/conversations/{conversation_id}").json()["messages"]
    assert "当前阶段：carrier_follow_up" in messages[-1]["content"]
    assert "开始处理时间：2026-08-28T08:00:00Z" in messages[-1]["content"]
    assert "最近更新时间：2026-08-28T09:00:00Z" in messages[-1]["content"]
    assert "下一次预计更新时间：2026-08-30T09:00:00Z" in messages[-1]["content"]
    assert "目标包裹：SHP-024" in messages[-1]["content"]


def test_customer_d_existing_investigation_is_a_runnable_business_scenario(
    client: TestClient,
) -> None:
    created = client.post(
        "/v1/conversations",
        json={"fixture_customer_key": "customer_d"},
    )
    assert created.status_code == 201
    conversation_id = created.json()["conversation_id"]
    submitted = client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={
            "content": "ORD-025 的物流还没有更新，我想看看已有核查现在处理到哪一步了。"
        },
    )
    assert submitted.status_code == 202
    case_id = submitted.json()["case_id"]

    body = client.get(f"/v1/investigation-cases/{case_id}").json()
    assert body["customer_disposition"] == "WAIT"
    assert body["active_tickets"] == []
    assert body["existing_investigations"] == [
        {
            "case_id": "SEED-CASE-002",
            "order_id": "ORD-025",
            "issue_type": "stalled_tracking",
            "status": "investigating",
            "stage": "carrier_follow_up",
            "opened_at": "2026-08-28T08:00:00Z",
            "last_updated_at": "2026-08-28T09:00:00Z",
            "next_update_at": "2026-08-30T10:00:00Z",
            "target_order_id": "ORD-025",
            "target_shipment_id": "SHP-025",
            "is_active": True,
        }
    ]

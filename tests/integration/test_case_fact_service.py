from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from after_sales_agent.actions.service import build_proposal, evidence_snapshot_hash
from after_sales_agent.application.case_facts import CaseFactService
from after_sales_agent.domain.case_facts import (
    CaseFactCandidate,
    CaseFactError,
    CaseFactIntegrityError,
    FactCode,
    FactStatus,
    FactValue,
    RelationHint,
    SourceSpan,
)
from after_sales_agent.domain.models import InvestigationCase, Run
from after_sales_agent.domain.state import (
    CaseState,
    DeliveryProofStatus,
    EvidenceAvailability,
    ExecutionStatus,
    IssueType,
)
from after_sales_agent.storage.database import Database, create_engine_and_session, init_database
from after_sales_agent.storage.models import CaseFactAssertionRow, CaseFactMessageConsumptionRow
from after_sales_agent.storage.repositories import Repository
from after_sales_agent.tools.contracts import DeliveryProofPayload, EvidenceRef, ToolResult

NOW = datetime(2026, 8, 28, 9, tzinfo=UTC)


@pytest.fixture
def database() -> Database:
    database = create_engine_and_session("sqlite:///:memory:")
    init_database(database.engine)
    try:
        yield database
    finally:
        database.engine.dispose()


def _proof_result(*, recipient_type: str, source_query_id: str) -> ToolResult[DeliveryProofPayload]:
    return ToolResult.completed(
        availability=EvidenceAvailability.PRESENT,
        source_type="delivery_proof",
        source_query_id=source_query_id,
        observed_at=NOW,
        payload=DeliveryProofPayload(
            order_id="ORD-001",
            pod_status=DeliveryProofStatus.FOUND,
            recipient_type=recipient_type,
            signed_at=NOW,
            note="synthetic proof",
        ),
    )


def _append_proof(
    repository: Repository,
    *,
    call_id: str,
    source_query_id: str,
    recipient_type: str = "front_desk",
) -> ToolResult[DeliveryProofPayload]:
    result = _proof_result(recipient_type=recipient_type, source_query_id=source_query_id)
    repository.create_tool_call(
        conversation_id="conv_001",
        case_id="case_001",
        run_id="run_001",
        tool_name="get_delivery_proof",
        normalized_args={"order_id": "ORD-001"},
        planning_turn=1,
        tool_call_id=call_id,
        actual_execution=True,
        requested_at=NOW,
    )
    repository.complete_tool_call(
        call_id,
        execution_status=ExecutionStatus.SUCCESS,
        evidence_availability=EvidenceAvailability.PRESENT,
        result_envelope=result.model_dump(mode="json"),
        result_hash=result.result_hash,
        source_version="fixture-pod-v1",
        completed_at=NOW,
    )
    return result


def _seed_case(
    database: Database,
    *,
    message: str,
    with_proof: bool = False,
    message_created_at: datetime = NOW,
) -> CaseFactService:
    with database.session_factory() as session, session.begin():
        repository = Repository(session)
        repository.create_conversation(
            "customer_001", "customer_a", "mock", conversation_id="conv_001"
        )
        repository.create_case(
            InvestigationCase(
                case_id="case_001",
                conversation_id="conv_001",
                customer_id="customer_001",
                authorized_order_id="ORD-001",
                canonical_issue_type=IssueType.SIGNED_NOT_RECEIVED,
                case_state=CaseState.AWAITING_CUSTOMER_INPUT,
            ),
            created_at=NOW,
        )
        repository.create_run(
            Run(run_id="run_001", case_id="case_001"),
            conversation_id="conv_001",
            run_kind="message",
        )
        repository.update_run("run_001", run_state="running", changed_at=NOW)
        repository.add_message(
            "conv_001",
            "customer",
            message,
            message_id="msg_001",
            case_id="case_001",
            run_id="run_001",
            created_at=message_created_at,
        )
        if with_proof:
            _append_proof(repository, call_id="call_pod_001", source_query_id="pod-001")
    return CaseFactService(database.session_factory)


def _candidate(content: str, fact_code: FactCode) -> CaseFactCandidate:
    return CaseFactCandidate(
        fact_code=fact_code,
        value=FactValue.TRUE,
        relation_hint=RelationHint.NEW,
        source_span=SourceSpan(start=0, end=len(content)),
    )


def _append_current_customer_reply(database: Database, *, content: str, message_id: str) -> None:
    with database.session_factory() as session, session.begin():
        Repository(session).add_message(
            "conv_001",
            "customer",
            content,
            message_id=message_id,
            case_id="case_001",
            run_id="run_001",
            created_at=datetime.now(UTC) + timedelta(minutes=1),
        )


def test_case_fact_message_and_question_replays_are_idempotent(database: Database) -> None:
    """TEST-V3B-QUESTION-04: replay cannot append another question or assertion."""

    content = "我仍然没有收到包裹。"
    service = _seed_case(database, message=content)
    service.initialize_case("case_001")
    first_question, created = service.record_question(
        "case_001", FactCode.CUSTOMER_STILL_REPORTS_MISSING
    )
    assert created
    _append_current_customer_reply(database, content=content, message_id="msg_002")

    first_acceptance = service.accept_message(
        case_id="case_001",
        source_message_id="msg_002",
        candidates=[_candidate(content, FactCode.CUSTOMER_STILL_REPORTS_MISSING)],
    )
    replay_acceptance = service.accept_message(
        case_id="case_001",
        source_message_id="msg_002",
        candidates=[_candidate(content, FactCode.CUSTOMER_STILL_REPORTS_MISSING)],
    )
    replay_question, replay_created = service.record_question(
        "case_001", FactCode.CUSTOMER_STILL_REPORTS_MISSING
    )

    assert replay_acceptance.snapshot.snapshot_hash == first_acceptance.snapshot.snapshot_hash
    assert replay_question.question_id == first_question.question_id
    assert not replay_created
    with database.session_factory() as session:
        repository = Repository(session)
        assert len(repository.list_case_fact_assertions("case_001")) == 1
        assert len(repository.list_case_fact_questions("case_001")) == 1


def test_case_fact_assertions_are_immutable_and_hash_disagreement_fails_closed(
    database: Database,
) -> None:
    """TEST-V3B-FACT-01/02/03: ORM mutation and provenance/projection drift are rejected."""

    content = "我仍然没有收到包裹。"
    service = _seed_case(
        database,
        message=content,
        message_created_at=NOW - timedelta(days=10_000),
    )
    service.initialize_case("case_001")
    service.record_question("case_001", FactCode.CUSTOMER_STILL_REPORTS_MISSING)
    _append_current_customer_reply(database, content=content, message_id="msg_002")
    service.accept_message(
        case_id="case_001",
        source_message_id="msg_002",
        candidates=[_candidate(content, FactCode.CUSTOMER_STILL_REPORTS_MISSING)],
    )

    with database.session_factory() as session:
        row = session.get(CaseFactAssertionRow, service.load_assertions("case_001")[0].assertion_id)
        assert row is not None
        row.value = "false"
        with pytest.raises(RuntimeError, match="append-only"):
            session.flush()
        session.rollback()

    with database.session_factory() as session:
        row = session.get(CaseFactAssertionRow, service.load_assertions("case_001")[0].assertion_id)
        assert row is not None
        session.delete(row)
        with pytest.raises(RuntimeError, match="may be deleted only"):
            session.flush()
        session.rollback()

    with database.session_factory() as session, session.begin():
        snapshot = Repository(session).get_case_fact_snapshot("case_001")
        assert snapshot is not None
        snapshot.snapshot_hash = "0" * 64
    with pytest.raises(CaseFactIntegrityError, match="stored/rebuilt"):
        service.load_snapshot("case_001")
    service.refresh_snapshot("case_001")

    with database.session_factory() as session, session.begin():
        snapshot = Repository(session).get_case_fact_snapshot("case_001")
        assert snapshot is not None
        payload = dict(snapshot.snapshot_payload)
        facts = dict(payload["facts"])
        customer_fact = dict(facts[FactCode.CUSTOMER_STILL_REPORTS_MISSING.value])
        customer_fact["status"] = FactStatus.KNOWN_FALSE.value
        facts[FactCode.CUSTOMER_STILL_REPORTS_MISSING.value] = customer_fact
        payload["facts"] = facts
        snapshot.snapshot_payload = payload
    with pytest.raises(CaseFactIntegrityError, match="stored/rebuilt"):
        service.load_snapshot("case_001")
    service.refresh_snapshot("case_001")

    with database.session_factory() as session, session.begin():
        message = Repository(session).get_message("msg_002")
        assert message is not None
        message.content = "被篡改的来源文本"
    with pytest.raises(CaseFactIntegrityError, match="message hash parity"):
        service.load_snapshot("case_001")


def test_location_fact_requires_current_proof_and_proof_change_removes_material_identity(
    database: Database,
) -> None:
    """TEST-V3B-FACT-01/03/04: proof drift invalidates the current bound fact."""

    content = "我已经问过前台，没有代收。"
    service = _seed_case(database, message=content, with_proof=True)
    service.initialize_case("case_001")
    service.record_question("case_001", FactCode.REPORTED_DELIVERY_LOCATION_CHECKED)
    _append_current_customer_reply(database, content=content, message_id="msg_002")
    accepted = service.accept_message(
        case_id="case_001",
        source_message_id="msg_002",
        candidates=[_candidate(content, FactCode.REPORTED_DELIVERY_LOCATION_CHECKED)],
    )
    entry = accepted.snapshot.facts[FactCode.REPORTED_DELIVERY_LOCATION_CHECKED]
    assert entry.status is FactStatus.KNOWN_TRUE
    assert entry.context_tool_call_id == "call_pod_001"
    assert entry.context_result_hash is not None
    identity = accepted.snapshot.material_identity()
    assert identity["case_fact_snapshot_hash"] == accepted.snapshot.snapshot_hash
    assert identity["active_assertion_ids"] == list(entry.active_assertion_ids)
    assert (
        identity["material_facts"][FactCode.REPORTED_DELIVERY_LOCATION_CHECKED.value][
            "delivery_proof_result_hash"
        ]
        == entry.context_result_hash
    )

    proposal = build_proposal(
        proposal_id="prop_001",
        case_id="case_001",
        version=1,
        order_id="ORD-001",
        issue_type=IssueType.SIGNED_NOT_RECEIVED,
        evidence_refs=[
            EvidenceRef(
                tool_call_id="call_pod_001",
                source_query_id="pod-001",
                observed_at=NOW,
                result_hash=entry.context_result_hash,
            )
        ],
        critical_result_hashes={"delivery_proof": entry.context_result_hash},
        policy_binding={"policy_version": "test"},
        case_fact_identity=identity,
        now=NOW,
    )
    assert proposal.evidence_snapshot_hash == evidence_snapshot_hash(
        critical_result_hashes={"delivery_proof": entry.context_result_hash},
        execution_parameters=proposal.execution_parameters,
        case_fact_identity=identity,
    )

    with database.session_factory() as session, session.begin():
        _append_proof(
            Repository(session),
            call_id="call_pod_002",
            source_query_id="pod-002",
            recipient_type="neighbor",
        )
    with pytest.raises(CaseFactIntegrityError, match="stored/rebuilt"):
        service.load_snapshot("case_001")
    changed = service.refresh_snapshot("case_001")
    assert changed.facts[FactCode.REPORTED_DELIVERY_LOCATION_CHECKED].status is FactStatus.UNKNOWN
    assert changed.material_identity() != proposal.case_fact_identity
    assert changed.material_identity()["active_assertion_ids"] == []


def test_case_fact_rejects_cross_case_customer_message(database: Database) -> None:
    """TEST-V3B-FACT-05: facts cannot use a customer message from another Case."""

    content = "我仍然没有收到包裹。"
    service = _seed_case(database, message=content)
    service.initialize_case("case_001")
    service.record_question("case_001", FactCode.CUSTOMER_STILL_REPORTS_MISSING)
    with database.session_factory() as session, session.begin():
        repository = Repository(session)
        repository.create_case(
            InvestigationCase(
                case_id="case_002",
                conversation_id="conv_001",
                customer_id="customer_001",
                authorized_order_id="ORD-001",
                canonical_issue_type=IssueType.SIGNED_NOT_RECEIVED,
                case_state=CaseState.AWAITING_CUSTOMER_INPUT,
            )
        )
        repository.add_message(
            "conv_001",
            "customer",
            content,
            message_id="msg_002",
            case_id="case_002",
            created_at=NOW + timedelta(minutes=1),
        )
    with pytest.raises(CaseFactError, match="bound to this Case"):
        service.accept_message(
            case_id="case_001",
            source_message_id="msg_002",
            candidates=[_candidate(content, FactCode.CUSTOMER_STILL_REPORTS_MISSING)],
        )


def test_case_fact_requires_current_post_question_reply_and_consumes_empty_batch(
    database: Database,
) -> None:
    """TEST-V3B-QUESTION-04: only the current unconsumed reply can answer one question."""

    content = "我仍然没有收到包裹。"
    service = _seed_case(
        database,
        message=content,
        message_created_at=NOW - timedelta(days=10_000),
    )
    service.initialize_case("case_001")
    question, _ = service.record_question("case_001", FactCode.CUSTOMER_STILL_REPORTS_MISSING)

    with pytest.raises(CaseFactError, match="predates"):
        service.accept_message(
            case_id="case_001",
            source_message_id="msg_001",
            candidates=[_candidate(content, FactCode.CUSTOMER_STILL_REPORTS_MISSING)],
        )

    _append_current_customer_reply(database, content="第一条回复", message_id="msg_002")
    _append_current_customer_reply(database, content="第二条回复", message_id="msg_003")
    with pytest.raises(CaseFactError, match="not the current"):
        service.accept_message(
            case_id="case_001",
            source_message_id="msg_002",
            candidates=[],
        )

    first = service.accept_message(
        case_id="case_001",
        source_message_id="msg_003",
        candidates=[],
    )
    assert not first.merge_decision.accepted
    assert first.merge_decision.reason_code == "NO_CANDIDATES"
    replay = service.accept_message(
        case_id="case_001",
        source_message_id="msg_003",
        candidates=[],
    )
    assert replay.merge_decision == first.merge_decision
    assert replay.snapshot.snapshot_hash == first.snapshot.snapshot_hash
    with pytest.raises(CaseFactError, match="different authority inputs"):
        service.accept_message(
            case_id="case_001",
            source_message_id="msg_003",
            candidates=[_candidate(content, FactCode.CUSTOMER_STILL_REPORTS_MISSING)],
        )

    with database.session_factory() as session:
        repository = Repository(session)
        consumption = repository.get_case_fact_consumption_for_question(question.question_id)
        assert consumption is not None
        assert consumption.source_message_id == "msg_003"
        assert consumption.outcome == "empty"
        assert len(repository.list_case_fact_assertions("case_001")) == 0


@pytest.mark.parametrize("content", ["不知道。", "我已经收到了包裹。"])
def test_case_fact_rejects_model_value_that_disagrees_with_customer_text(
    database: Database, content: str
) -> None:
    """TEST-V3B-FACT-01: a valid model enum cannot manufacture a known fact."""

    service = _seed_case(database, message="提问前消息")
    service.initialize_case("case_001")
    service.record_question("case_001", FactCode.CUSTOMER_STILL_REPORTS_MISSING)
    _append_current_customer_reply(database, content=content, message_id="msg_002")

    result = service.accept_message(
        case_id="case_001",
        source_message_id="msg_002",
        candidates=[_candidate(content, FactCode.CUSTOMER_STILL_REPORTS_MISSING)],
    )
    assert not result.merge_decision.accepted
    assert result.merge_decision.reason_code == "CANDIDATE_DISAGREES_WITH_PERSISTED_CONTEXT"
    assert (
        result.snapshot.facts[FactCode.CUSTOMER_STILL_REPORTS_MISSING].status is FactStatus.UNKNOWN
    )
    assert service.load_assertions("case_001") == ()


def test_case_fact_message_consumption_is_append_only(database: Database) -> None:
    """TEST-V3B-FACT-01/QUESTION-04: rejection ledger itself cannot be rewritten."""

    service = _seed_case(database, message="提问前消息")
    service.initialize_case("case_001")
    service.record_question("case_001", FactCode.CUSTOMER_STILL_REPORTS_MISSING)
    _append_current_customer_reply(database, content="不知道。", message_id="msg_002")
    service.accept_message(
        case_id="case_001",
        source_message_id="msg_002",
        candidates=[_candidate("不知道。", FactCode.CUSTOMER_STILL_REPORTS_MISSING)],
    )

    with database.session_factory() as session:
        consumption_id = session.scalar(select(CaseFactMessageConsumptionRow.consumption_id))
        assert consumption_id is not None
        row = session.get(CaseFactMessageConsumptionRow, consumption_id)
        assert row is not None
        row.reason_code = "ACCEPTED"
        with pytest.raises(RuntimeError, match="append-only"):
            session.flush()
        session.rollback()

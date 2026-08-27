from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from after_sales_agent.domain.case_facts import (
    CaseFactAssertion,
    CaseFactCandidate,
    CaseFactError,
    CaseFactIntegrityError,
    DeliveryProofFactContext,
    FactCode,
    FactQuestion,
    FactStatus,
    FactValue,
    QuestionStatus,
    RelationHint,
    SourceSpan,
    question_allowed,
    rebuild_case_fact_snapshot,
    stable_question_id,
    validate_candidate,
    validate_candidate_batch,
)

NOW = datetime(2026, 8, 28, 9, tzinfo=UTC)
PROOF = DeliveryProofFactContext(
    tool_call_id="call_pod_001",
    result_hash="a" * 64,
    recipient_category="front_desk",
)


def _assertion(
    sequence: int,
    *,
    assertion_id: str,
    fact_code: FactCode = FactCode.CUSTOMER_STILL_REPORTS_MISSING,
    value: FactValue = FactValue.TRUE,
    relation: RelationHint = RelationHint.NEW,
    supersedes: str | None = None,
    message_id: str | None = None,
    proof: DeliveryProofFactContext | None = None,
) -> CaseFactAssertion:
    return CaseFactAssertion(
        assertion_id=assertion_id,
        case_id="case_001",
        fact_code=fact_code,
        value=value,
        source_message_id=message_id or f"msg_{sequence}",
        source_message_hash=str(sequence) * 64,
        source_span_start=0,
        source_span_end=4,
        relation=relation,
        supersedes_assertion_id=supersedes,
        extractor_kind="model_candidate",
        extractor_version="fact-extractor-v1",
        context_tool_call_id=proof.tool_call_id if proof else None,
        context_result_hash=proof.result_hash if proof else None,
        assertion_sequence=sequence,
        recorded_at=NOW,
    )


def _question(
    fact_code: FactCode,
    *,
    targeted: bool = False,
    context_hash: str | None = None,
) -> FactQuestion:
    return FactQuestion(
        question_id=stable_question_id(
            case_id="case_001",
            fact_code=fact_code,
            context_result_hash=context_hash,
            targeted_conflict=targeted,
        ),
        case_id="case_001",
        fact_code=fact_code,
        context_result_hash=context_hash,
        targeted_conflict=targeted,
        asked_at=NOW,
    )


def test_v3b_fact_candidate_is_extra_forbid_versioned_and_bounded_to_two() -> None:
    """TEST-V3B-FACT-01 candidate boundary."""

    candidate = CaseFactCandidate(
        fact_code=FactCode.CUSTOMER_STILL_REPORTS_MISSING,
        value=FactValue.TRUE,
        relation_hint=RelationHint.NEW,
        source_span=SourceSpan(start=0, end=4),
    )
    assert candidate.schema_version == "v3b.fact_candidate.v1"
    with pytest.raises(ValidationError):
        CaseFactCandidate.model_validate(
            {**candidate.model_dump(mode="json"), "customer_id": "untrusted"}
        )
    with pytest.raises(CaseFactError, match="at most two"):
        validate_candidate_batch([candidate, candidate, candidate])


def test_v3b_fact_location_candidate_requires_same_case_customer_span_and_proof() -> None:
    """TEST-V3B-FACT-01 source and delivery-proof provenance."""

    content = "我已经问过前台，那里没有代收。"
    candidate = CaseFactCandidate(
        fact_code=FactCode.REPORTED_DELIVERY_LOCATION_CHECKED,
        value=FactValue.TRUE,
        relation_hint=RelationHint.NEW,
        source_span=SourceSpan(start=0, end=len(content)),
    )
    assert (
        validate_candidate(
            candidate,
            case_id="case_001",
            message_case_id="case_001",
            message_role="customer",
            message_content=content,
            outstanding_fact_code=FactCode.REPORTED_DELIVERY_LOCATION_CHECKED,
            active_assertions=(),
            proof_context=PROOF,
        )
        == PROOF
    )
    with pytest.raises(CaseFactError, match="bound to this Case"):
        validate_candidate(
            candidate,
            case_id="case_001",
            message_case_id="case_002",
            message_role="customer",
            message_content=content,
            outstanding_fact_code=FactCode.REPORTED_DELIVERY_LOCATION_CHECKED,
            active_assertions=(),
            proof_context=PROOF,
        )
    with pytest.raises(CaseFactError, match="inapplicable"):
        validate_candidate(
            candidate,
            case_id="case_001",
            message_case_id="case_001",
            message_role="customer",
            message_content=content,
            outstanding_fact_code=FactCode.REPORTED_DELIVERY_LOCATION_CHECKED,
            active_assertions=(),
            proof_context=None,
        )


def test_v3b_fact_relation_hints_cannot_override_deterministic_merge_rules() -> None:
    """TEST-V3B-FACT-03: repeat and correction shape must match active assertions."""

    active = (_assertion(1, assertion_id="fact_1", value=FactValue.TRUE),)
    repeat_false = CaseFactCandidate(
        fact_code=FactCode.CUSTOMER_STILL_REPORTS_MISSING,
        value=FactValue.FALSE,
        relation_hint=RelationHint.REPEAT,
        source_span=SourceSpan(start=0, end=2),
    )
    with pytest.raises(CaseFactError, match="deterministic interpretation"):
        validate_candidate(
            repeat_false,
            case_id="case_001",
            message_case_id="case_001",
            message_role="customer",
            message_content="我仍然没有收到包裹。",
            outstanding_fact_code=FactCode.CUSTOMER_STILL_REPORTS_MISSING,
            active_assertions=active,
            proof_context=None,
        )


@pytest.mark.parametrize(
    ("message", "candidate"),
    [
        (
            "不知道。",
            CaseFactCandidate(
                fact_code=FactCode.CUSTOMER_STILL_REPORTS_MISSING,
                value=FactValue.TRUE,
                relation_hint=RelationHint.NEW,
                source_span=SourceSpan(start=0, end=4),
            ),
        ),
        (
            "我已经收到了包裹。",
            CaseFactCandidate(
                fact_code=FactCode.CUSTOMER_STILL_REPORTS_MISSING,
                value=FactValue.TRUE,
                relation_hint=RelationHint.NEW,
                source_span=SourceSpan(start=0, end=9),
            ),
        ),
    ],
)
def test_v3b_fact_untrusted_value_cannot_override_persisted_customer_text(
    message: str, candidate: CaseFactCandidate
) -> None:
    """TEST-V3B-FACT-01: enum-valid model values are never factual authority."""

    with pytest.raises(CaseFactError, match="deterministic interpretation"):
        validate_candidate(
            candidate,
            case_id="case_001",
            message_case_id="case_001",
            message_role="customer",
            message_content=message,
            outstanding_fact_code=FactCode.CUSTOMER_STILL_REPORTS_MISSING,
            active_assertions=(),
            proof_context=None,
        )


@pytest.mark.parametrize(
    "candidate",
    [
        CaseFactCandidate(
            fact_code=FactCode.CUSTOMER_STILL_REPORTS_MISSING,
            value=FactValue.FALSE,
            relation_hint=RelationHint.REPEAT,
            source_span=SourceSpan(start=0, end=10),
        ),
        CaseFactCandidate(
            fact_code=FactCode.CUSTOMER_STILL_REPORTS_MISSING,
            value=FactValue.FALSE,
            relation_hint=RelationHint.CORRECTION,
            target_assertion_id="wrong_target",
            source_span=SourceSpan(start=0, end=10),
        ),
        CaseFactCandidate(
            fact_code=FactCode.CUSTOMER_STILL_REPORTS_MISSING,
            value=FactValue.FALSE,
            relation_hint=RelationHint.CORRECTION,
            target_assertion_id="fact_1",
            source_span=SourceSpan(start=0, end=2),
        ),
    ],
)
def test_v3b_fact_forged_relation_target_or_span_cannot_override_context(
    candidate: CaseFactCandidate,
) -> None:
    """TEST-V3B-FACT-01: correction/repeat/target/span come from deterministic code."""

    with pytest.raises(CaseFactError, match="deterministic interpretation"):
        validate_candidate(
            candidate,
            case_id="case_001",
            message_case_id="case_001",
            message_role="customer",
            message_content="其实我已经收到了包裹。",
            outstanding_fact_code=FactCode.CUSTOMER_STILL_REPORTS_MISSING,
            active_assertions=(_assertion(1, assertion_id="fact_1", value=FactValue.TRUE),),
            proof_context=None,
        )

    correction_same_value = CaseFactCandidate(
        fact_code=FactCode.CUSTOMER_STILL_REPORTS_MISSING,
        value=FactValue.TRUE,
        relation_hint=RelationHint.CORRECTION,
        target_assertion_id="fact_1",
        source_span=SourceSpan(start=0, end=3),
    )
    with pytest.raises(CaseFactError, match="deterministic interpretation"):
        validate_candidate(
            correction_same_value,
            case_id="case_001",
            message_case_id="case_001",
            message_role="customer",
            message_content="其实我已经收到了。",
            outstanding_fact_code=FactCode.CUSTOMER_STILL_REPORTS_MISSING,
            active_assertions=(_assertion(1, assertion_id="fact_1", value=FactValue.TRUE),),
            proof_context=None,
        )


def test_v3b_fact_append_only_repeat_correction_and_withdrawal_rebuild() -> None:
    """TEST-V3B-FACT-02/03 supersession without historical mutation."""

    original = _assertion(1, assertion_id="fact_1", value=FactValue.TRUE)
    repeat = _assertion(
        2,
        assertion_id="fact_2",
        value=FactValue.TRUE,
        relation=RelationHint.REPEAT,
    )
    correction = _assertion(
        3,
        assertion_id="fact_3",
        value=FactValue.FALSE,
        relation=RelationHint.CORRECTION,
        supersedes="fact_1",
    )
    withdrawal = _assertion(
        4,
        assertion_id="fact_4",
        value=FactValue.UNKNOWN,
        relation=RelationHint.WITHDRAWAL,
        supersedes="fact_3",
    )
    snapshot = rebuild_case_fact_snapshot(
        case_id="case_001",
        assertions=[original, repeat, correction, withdrawal],
        questions=(),
        current_proof=None,
        rebuilt_at=NOW,
    )
    entry = snapshot.facts[FactCode.CUSTOMER_STILL_REPORTS_MISSING]
    assert entry.status is FactStatus.UNKNOWN
    assert entry.active_assertion_ids == ("fact_2", "fact_4")
    assert entry.superseded_assertion_ids == ("fact_1", "fact_3")
    rebuilt = rebuild_case_fact_snapshot(
        case_id="case_001",
        assertions=[original, repeat, correction, withdrawal],
        questions=(),
        current_proof=None,
        rebuilt_at=NOW,
    )
    assert rebuilt.snapshot_hash == snapshot.snapshot_hash


def test_v3b_fact_competing_or_invalid_supersession_fails_closed_as_conflict() -> None:
    """TEST-V3B-FACT-03 competing or corrupt supersession never chooses a winner."""

    original = _assertion(1, assertion_id="fact_1", value=FactValue.TRUE)
    competing = [
        _assertion(
            2,
            assertion_id="fact_2",
            value=FactValue.FALSE,
            relation=RelationHint.CORRECTION,
            supersedes="fact_1",
        ),
        _assertion(
            3,
            assertion_id="fact_3",
            value=FactValue.FALSE,
            relation=RelationHint.CORRECTION,
            supersedes="fact_1",
        ),
    ]
    snapshot = rebuild_case_fact_snapshot(
        case_id="case_001",
        assertions=[original, *competing],
        questions=(),
        current_proof=None,
        rebuilt_at=NOW,
    )
    assert snapshot.facts[FactCode.CUSTOMER_STILL_REPORTS_MISSING].status is FactStatus.CONFLICT

    invalid = _assertion(
        2,
        assertion_id="fact_invalid",
        value=FactValue.FALSE,
        relation=RelationHint.CORRECTION,
        supersedes="not_an_assertion",
    )
    snapshot = rebuild_case_fact_snapshot(
        case_id="case_001",
        assertions=[original, invalid],
        questions=(),
        current_proof=None,
        rebuilt_at=NOW,
    )
    assert snapshot.facts[FactCode.CUSTOMER_STILL_REPORTS_MISSING].status is FactStatus.CONFLICT


def test_v3b_fact_opposites_conflict_unknown_is_not_false_and_proof_change_invalidates() -> None:
    """TEST-V3B-FACT-03/04 hash parity, conflict, unknown, and proof invalidation."""

    conflicting = rebuild_case_fact_snapshot(
        case_id="case_001",
        assertions=[
            _assertion(1, assertion_id="fact_1", value=FactValue.TRUE),
            _assertion(2, assertion_id="fact_2", value=FactValue.FALSE),
        ],
        questions=(),
        current_proof=None,
        rebuilt_at=NOW,
    )
    assert conflicting.facts[FactCode.CUSTOMER_STILL_REPORTS_MISSING].status is FactStatus.CONFLICT
    unknown = rebuild_case_fact_snapshot(
        case_id="case_001",
        assertions=[_assertion(1, assertion_id="fact_1", value=FactValue.UNKNOWN)],
        questions=(),
        current_proof=None,
        rebuilt_at=NOW,
    )
    assert unknown.facts[FactCode.CUSTOMER_STILL_REPORTS_MISSING].status is FactStatus.UNKNOWN

    location = _assertion(
        1,
        assertion_id="location_1",
        fact_code=FactCode.REPORTED_DELIVERY_LOCATION_CHECKED,
        proof=PROOF,
    )
    changed = PROOF.model_copy(update={"result_hash": "b" * 64})
    stale = rebuild_case_fact_snapshot(
        case_id="case_001",
        assertions=[location],
        questions=(),
        current_proof=changed,
        rebuilt_at=NOW,
    )
    assert stale.facts[FactCode.REPORTED_DELIVERY_LOCATION_CHECKED].status is FactStatus.UNKNOWN

    multiple_proofs = rebuild_case_fact_snapshot(
        case_id="case_001",
        assertions=[
            location,
            _assertion(
                2,
                assertion_id="location_2",
                fact_code=FactCode.REPORTED_DELIVERY_LOCATION_CHECKED,
                proof=changed,
            ),
        ],
        questions=(),
        current_proof=changed,
        rebuilt_at=NOW,
    )
    assert (
        multiple_proofs.facts[FactCode.REPORTED_DELIVERY_LOCATION_CHECKED].status
        is FactStatus.CONFLICT
    )

    with pytest.raises(CaseFactIntegrityError, match="contiguous"):
        rebuild_case_fact_snapshot(
            case_id="case_001",
            assertions=[location.model_copy(update={"assertion_sequence": 2})],
            questions=(),
            current_proof=PROOF,
            rebuilt_at=NOW,
        )


def test_v3b_question_known_and_unknown_are_not_reasked() -> None:
    """TEST-V3B-QUESTION-01/02 known and unknown repeat policy."""

    known = rebuild_case_fact_snapshot(
        case_id="case_001",
        assertions=[_assertion(1, assertion_id="fact_1")],
        questions=[_question(FactCode.CUSTOMER_STILL_REPORTS_MISSING)],
        current_proof=None,
        rebuilt_at=NOW,
    )
    assert not question_allowed(known, FactCode.CUSTOMER_STILL_REPORTS_MISSING, global_asks=1)

    unknown = rebuild_case_fact_snapshot(
        case_id="case_001",
        assertions=[_assertion(1, assertion_id="fact_1", value=FactValue.UNKNOWN)],
        questions=[_question(FactCode.CUSTOMER_STILL_REPORTS_MISSING)],
        current_proof=None,
        rebuilt_at=NOW,
    )
    state = unknown.question_state[FactCode.CUSTOMER_STILL_REPORTS_MISSING]
    assert state.status is QuestionStatus.UNKNOWN_EXHAUSTED
    assert not question_allowed(unknown, FactCode.CUSTOMER_STILL_REPORTS_MISSING, global_asks=1)


def test_v3b_question_conflict_gets_one_targeted_disambiguation_with_global_budget() -> None:
    """TEST-V3B-QUESTION-03 conflict disambiguation bound."""

    assertions = [
        _assertion(1, assertion_id="fact_1", value=FactValue.TRUE),
        _assertion(2, assertion_id="fact_2", value=FactValue.FALSE),
    ]
    before = rebuild_case_fact_snapshot(
        case_id="case_001",
        assertions=assertions,
        questions=(),
        current_proof=None,
        rebuilt_at=NOW,
    )
    assert question_allowed(before, FactCode.CUSTOMER_STILL_REPORTS_MISSING, global_asks=1)
    after = rebuild_case_fact_snapshot(
        case_id="case_001",
        assertions=assertions,
        questions=[_question(FactCode.CUSTOMER_STILL_REPORTS_MISSING, targeted=True)],
        current_proof=None,
        rebuilt_at=NOW,
    )
    assert (
        after.question_state[FactCode.CUSTOMER_STILL_REPORTS_MISSING].status
        is QuestionStatus.CONFLICT_EXHAUSTED
    )
    assert not question_allowed(after, FactCode.CUSTOMER_STILL_REPORTS_MISSING, global_asks=2)


def test_v3b_question_id_is_stable_for_replay_and_context_sensitive() -> None:
    """TEST-V3B-QUESTION-04 stable question replay identity."""

    first = stable_question_id(
        case_id="case_001",
        fact_code=FactCode.REPORTED_DELIVERY_LOCATION_CHECKED,
        context_result_hash="a" * 64,
        targeted_conflict=False,
    )
    assert first == stable_question_id(
        case_id="case_001",
        fact_code=FactCode.REPORTED_DELIVERY_LOCATION_CHECKED,
        context_result_hash="a" * 64,
        targeted_conflict=False,
    )
    changed = stable_question_id(
        case_id="case_001",
        fact_code=FactCode.REPORTED_DELIVERY_LOCATION_CHECKED,
        context_result_hash="b" * 64,
        targeted_conflict=False,
    )
    assert changed != first

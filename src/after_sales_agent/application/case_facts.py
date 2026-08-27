"""Production Case Fact composition over append-only repository records."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from after_sales_agent.domain.case_facts import (
    EXTRACTOR_VERSION,
    CaseFactAssertion,
    CaseFactCandidate,
    CaseFactError,
    CaseFactIntegrityError,
    CaseFactSnapshot,
    DeliveryProofFactContext,
    FactCode,
    FactQuestion,
    FactStatus,
    FactValue,
    RelationHint,
    SourceSpan,
    message_hash,
    question_allowed,
    rebuild_case_fact_snapshot,
    stable_json_hash,
    stable_question_id,
    validate_candidate,
    validate_candidate_batch,
)
from after_sales_agent.domain.state import (
    DeliveryProofStatus,
    EvidenceAvailability,
    ExecutionStatus,
)
from after_sales_agent.storage.database import SessionFactory
from after_sales_agent.storage.models import utc_now
from after_sales_agent.storage.repositories import Repository
from after_sales_agent.tools.contracts import DeliveryProofPayload, ToolResult


def _assertion_from_row(row: Any) -> CaseFactAssertion:
    return CaseFactAssertion.model_validate(
        {
            "assertion_id": row.assertion_id,
            "case_id": row.case_id,
            "fact_code": row.fact_code,
            "value": row.value,
            "source_message_id": row.source_message_id,
            "source_message_hash": row.source_message_hash,
            "source_span_start": row.source_span_start,
            "source_span_end": row.source_span_end,
            "relation": row.relation,
            "supersedes_assertion_id": row.supersedes_assertion_id,
            "extractor_kind": row.extractor_kind,
            "extractor_version": row.extractor_version,
            "context_tool_call_id": row.context_tool_call_id,
            "context_result_hash": row.context_result_hash,
            "assertion_sequence": row.assertion_sequence,
            "recorded_at": row.recorded_at,
        }
    )


def _question_from_row(row: Any) -> FactQuestion:
    return FactQuestion(
        question_id=row.question_id,
        case_id=row.case_id,
        fact_code=FactCode(row.fact_code),
        context_result_hash=row.context_result_hash,
        targeted_conflict=row.targeted_conflict,
        asked_at=row.asked_at,
    )


class CaseFactService:
    """Deterministic Case-scoped validation, merge, rebuild, and question policy."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _proof_context(repository: Repository, case_id: str) -> DeliveryProofFactContext | None:
        rows = [
            row
            for row in repository.list_tool_calls(case_id=case_id)
            if row.tool_name == "get_delivery_proof"
            and row.actual_execution
            and row.completed_at is not None
        ]
        for row in reversed(rows):
            if row.result_envelope is None or row.result_hash is None:
                continue
            result = ToolResult[DeliveryProofPayload].model_validate(row.result_envelope)
            payload = result.payload
            if (
                result.execution_status is ExecutionStatus.SUCCESS
                and result.evidence_availability is EvidenceAvailability.PRESENT
                and payload is not None
                and payload.pod_status is DeliveryProofStatus.FOUND
                and payload.recipient_type
            ):
                if result.result_hash != row.result_hash:
                    raise CaseFactIntegrityError("delivery-proof row/envelope hash parity failed")
                return DeliveryProofFactContext(
                    tool_call_id=row.tool_call_id,
                    result_hash=row.result_hash,
                    recipient_category=payload.recipient_type,
                )
        return None

    @staticmethod
    def _verify_message_hashes(
        repository: Repository, assertions: Sequence[CaseFactAssertion]
    ) -> None:
        for assertion in assertions:
            message = repository.get_message(assertion.source_message_id)
            if (
                message is None
                or message.case_id != assertion.case_id
                or message.role != "customer"
            ):
                raise CaseFactIntegrityError(
                    "assertion source message lost same-Case customer binding"
                )
            if message_hash(message.content) != assertion.source_message_hash:
                raise CaseFactIntegrityError("assertion source message hash parity failed")
            if assertion.source_span_end > len(message.content):
                raise CaseFactIntegrityError("stored assertion source span is out of bounds")

    def _rebuild(self, repository: Repository, case_id: str, *, now: datetime) -> CaseFactSnapshot:
        assertions = [
            _assertion_from_row(row) for row in repository.list_case_fact_assertions(case_id)
        ]
        self._verify_message_hashes(repository, assertions)
        questions = [
            _question_from_row(row) for row in repository.list_case_fact_questions(case_id)
        ]
        return rebuild_case_fact_snapshot(
            case_id=case_id,
            assertions=assertions,
            questions=questions,
            current_proof=self._proof_context(repository, case_id),
            rebuilt_at=now,
        )

    def load_snapshot(self, case_id: str, *, verify_stored: bool = True) -> CaseFactSnapshot:
        with self._session_factory() as session:
            repository = Repository(session)
            repository.require_case(case_id)
            rebuilt = self._rebuild(repository, case_id, now=utc_now())
            stored = repository.get_case_fact_snapshot(case_id)
            if stored is not None and verify_stored:
                try:
                    parsed = CaseFactSnapshot.model_validate(stored.snapshot_payload)
                except Exception as exc:
                    raise CaseFactIntegrityError("stored CaseFactSnapshot is malformed") from exc
                parsed_projection = parsed.model_dump(mode="json", exclude={"rebuilt_at"})
                rebuilt_projection = rebuilt.model_dump(mode="json", exclude={"rebuilt_at"})
                if (
                    stored.revision != rebuilt.revision
                    or stored.snapshot_hash != rebuilt.snapshot_hash
                    or parsed.snapshot_hash != rebuilt.snapshot_hash
                    or parsed_projection != rebuilt_projection
                ):
                    raise CaseFactIntegrityError("stored/rebuilt CaseFactSnapshot disagreement")
            return rebuilt

    def load_assertions(self, case_id: str) -> tuple[CaseFactAssertion, ...]:
        with self._session_factory() as session:
            repository = Repository(session)
            return tuple(
                _assertion_from_row(row) for row in repository.list_case_fact_assertions(case_id)
            )

    def initialize_case(self, case_id: str) -> CaseFactSnapshot:
        with self._session_factory() as session, session.begin():
            repository = Repository(session)
            snapshot = self._rebuild(repository, case_id, now=utc_now())
            repository.store_case_fact_snapshot(snapshot)
            return snapshot

    def refresh_snapshot(self, case_id: str) -> CaseFactSnapshot:
        """Explicitly refresh the derived projection after canonical history changes."""

        with self._session_factory() as session, session.begin():
            repository = Repository(session)
            snapshot = self._rebuild(repository, case_id, now=utc_now())
            repository.store_case_fact_snapshot(snapshot)
            return snapshot

    @staticmethod
    def extract_candidates(
        content: str,
        *,
        fact_code: FactCode,
        active_assertions: Sequence[CaseFactAssertion],
    ) -> tuple[CaseFactCandidate, ...]:
        """Bounded deterministic adapter; model adapters must return this same schema."""

        normalized = content.casefold()
        unknown_cues = ("不知道", "不清楚", "不确定", "没法确认")
        correction_cues = ("更正", "改一下", "说错", "其实", "不是", "撤回")
        if any(cue in normalized for cue in unknown_cues):
            value = FactValue.UNKNOWN
        elif fact_code is FactCode.CUSTOMER_STILL_REPORTS_MISSING:
            value = (
                FactValue.FALSE
                if any(cue in normalized for cue in ("找到了", "已收到", "收到了"))
                else FactValue.TRUE
            )
        else:
            negative = ("还没查", "还没有", "没有查", "没问", "未确认")
            value = (
                FactValue.FALSE if any(cue in normalized for cue in negative) else FactValue.TRUE
            )

        same_fact = [item for item in active_assertions if item.fact_code is fact_code]
        target = same_fact[-1].assertion_id if same_fact else None
        if any(cue in normalized for cue in ("撤回", "不确定")) and target is not None:
            relation = RelationHint.WITHDRAWAL
            value = FactValue.UNKNOWN
        elif any(cue in normalized for cue in correction_cues) and target is not None:
            relation = RelationHint.CORRECTION
        elif same_fact and all(item.value is value for item in same_fact):
            relation = RelationHint.REPEAT
            target = None
        else:
            relation = RelationHint.NEW
            target = None
        return validate_candidate_batch(
            (
                CaseFactCandidate(
                    fact_code=fact_code,
                    value=value,
                    relation_hint=relation,
                    target_assertion_id=target,
                    source_span=SourceSpan(start=0, end=len(content)),
                ),
            )
        )

    def accept_message(
        self,
        *,
        case_id: str,
        source_message_id: str,
        candidates: Sequence[CaseFactCandidate | dict[str, Any]],
        extractor_kind: str = "deterministic",
        extractor_version: str = EXTRACTOR_VERSION,
    ) -> CaseFactSnapshot:
        parsed = validate_candidate_batch(candidates)
        with self._session_factory() as session, session.begin():
            repository = Repository(session)
            case = repository.require_case(case_id)
            if case.case_state != "awaiting_customer_input":
                raise CaseFactError("Case must be awaiting a business clarification reply")
            message = repository.get_message(source_message_id)
            if message is None:
                raise CaseFactError("source_message_id was not found")
            questions = repository.list_case_fact_questions(case_id)
            if not questions:
                raise CaseFactError("Case has no outstanding Case Fact question")
            outstanding = FactCode(questions[-1].fact_code)
            existing = [
                _assertion_from_row(row) for row in repository.list_case_fact_assertions(case_id)
            ]
            proof = self._proof_context(repository, case_id)
            for candidate in parsed:
                bound_proof = validate_candidate(
                    candidate,
                    case_id=case_id,
                    message_case_id=message.case_id,
                    message_role=message.role,
                    message_content=message.content,
                    outstanding_fact_code=outstanding,
                    active_assertions=[
                        item
                        for item in existing
                        if item.assertion_id
                        not in {
                            assertion.supersedes_assertion_id
                            for assertion in existing
                            if assertion.supersedes_assertion_id is not None
                        }
                    ],
                    proof_context=proof,
                )
                fingerprint = stable_json_hash(
                    {
                        "case_id": case_id,
                        "source_message_id": source_message_id,
                        "candidate": candidate.model_dump(mode="json"),
                        "context_result_hash": bound_proof.result_hash if bound_proof else None,
                    }
                )
                replay = repository.find_case_fact_candidate(case_id, fingerprint)
                if replay is not None:
                    continue
                assertion = CaseFactAssertion(
                    assertion_id=f"fact_{fingerprint[:24]}",
                    case_id=case_id,
                    fact_code=candidate.fact_code,
                    value=candidate.value,
                    source_message_id=source_message_id,
                    source_message_hash=message_hash(message.content),
                    source_span_start=candidate.source_span.start,
                    source_span_end=candidate.source_span.end,
                    relation=candidate.relation_hint,
                    supersedes_assertion_id=candidate.target_assertion_id,
                    extractor_kind=extractor_kind,
                    extractor_version=extractor_version,
                    context_tool_call_id=bound_proof.tool_call_id if bound_proof else None,
                    context_result_hash=bound_proof.result_hash if bound_proof else None,
                    assertion_sequence=len(existing) + 1,
                    recorded_at=utc_now(),
                )
                repository.append_case_fact_assertion(assertion, candidate_fingerprint=fingerprint)
                existing.append(assertion)
            snapshot = self._rebuild(repository, case_id, now=utc_now())
            repository.store_case_fact_snapshot(snapshot)
            return snapshot

    def record_question(self, case_id: str, fact_code: FactCode) -> tuple[FactQuestion, bool]:
        with self._session_factory() as session, session.begin():
            repository = Repository(session)
            case = repository.require_case(case_id)
            proof = self._proof_context(repository, case_id)
            snapshot = self._rebuild(repository, case_id, now=utc_now())
            targeted = snapshot.facts[fact_code].status is FactStatus.CONFLICT
            context_hash = (
                proof.result_hash
                if fact_code is FactCode.REPORTED_DELIVERY_LOCATION_CHECKED and proof
                else None
            )
            question = FactQuestion(
                question_id=stable_question_id(
                    case_id=case_id,
                    fact_code=fact_code,
                    context_result_hash=context_hash,
                    targeted_conflict=targeted,
                ),
                case_id=case_id,
                fact_code=fact_code,
                context_result_hash=context_hash,
                targeted_conflict=targeted,
                asked_at=utc_now(),
            )
            # A replay is idempotent even if the original question has since
            # been answered or the normal question policy is now terminal.
            existing = next(
                (
                    row
                    for row in repository.list_case_fact_questions(case_id)
                    if row.question_id == question.question_id
                ),
                None,
            )
            if existing is not None:
                return _question_from_row(existing), False
            if not question_allowed(
                snapshot, fact_code, global_asks=case.business_clarification_count
            ):
                raise CaseFactError("Case Fact question is not allowed by repeat/budget policy")
            repository.append_case_fact_question(question)
            rebuilt = self._rebuild(repository, case_id, now=utc_now())
            repository.store_case_fact_snapshot(rebuilt)
            return question, True


__all__ = ["CaseFactService"]

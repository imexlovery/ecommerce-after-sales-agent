"""Production Case Fact composition over append-only repository records."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from after_sales_agent.domain.case_facts import (
    EXTRACTOR_VERSION,
    CaseFactAcceptance,
    CaseFactAssertion,
    CaseFactCandidate,
    CaseFactError,
    CaseFactIntegrityError,
    CaseFactSnapshot,
    DeliveryProofFactContext,
    FactCode,
    FactMergeDecision,
    FactQuestion,
    FactStatus,
    candidate_batch_hash,
    interpret_customer_reply,
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

    @staticmethod
    def _verify_consumptions(
        repository: Repository,
        case_id: str,
        questions: Sequence[FactQuestion],
        assertions: Sequence[CaseFactAssertion],
    ) -> None:
        """Validate the append-only reply consumption ledger during every rebuild."""

        question_ids = {item.question_id for item in questions}
        assertion_ids = {item.assertion_id for item in assertions}
        for consumption in repository.list_case_fact_message_consumptions(case_id):
            message = repository.get_message(consumption.source_message_id)
            if (
                consumption.question_id not in question_ids
                or message is None
                or message.case_id != case_id
                or message.role != "customer"
                or message_hash(message.content) != consumption.source_message_hash
            ):
                raise CaseFactIntegrityError("Case Fact consumption provenance parity failed")
            try:
                decision = FactMergeDecision.model_validate(consumption.decision_payload)
            except Exception as exc:
                raise CaseFactIntegrityError("Case Fact consumption decision is malformed") from exc
            if (
                decision.candidate_batch_hash != consumption.candidate_batch_hash
                or decision.reason_code != consumption.reason_code
                or decision.assertion_id != consumption.assertion_id
            ):
                raise CaseFactIntegrityError("Case Fact consumption decision parity failed")
            expected_outcome = (
                "accepted"
                if decision.accepted
                else ("empty" if decision.reason_code == "NO_CANDIDATES" else "rejected")
            )
            if consumption.outcome != expected_outcome:
                raise CaseFactIntegrityError("Case Fact consumption outcome parity failed")
            if decision.accepted != (consumption.assertion_id in assertion_ids):
                raise CaseFactIntegrityError("Case Fact consumption assertion parity failed")

    def _rebuild(self, repository: Repository, case_id: str, *, now: datetime) -> CaseFactSnapshot:
        assertions = [
            _assertion_from_row(row) for row in repository.list_case_fact_assertions(case_id)
        ]
        self._verify_message_hashes(repository, assertions)
        questions = [
            _question_from_row(row) for row in repository.list_case_fact_questions(case_id)
        ]
        self._verify_consumptions(repository, case_id, questions, assertions)
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
        proof_context: DeliveryProofFactContext | None = None,
    ) -> tuple[CaseFactCandidate, ...]:
        """Build the deterministic extraction candidate; unsupported text stays empty."""

        candidate = interpret_customer_reply(
            message_content=content,
            fact_code=fact_code,
            active_assertions=active_assertions,
            proof_context=proof_context,
        )
        return (candidate,) if candidate is not None else ()

    def load_current_proof_context(self, case_id: str) -> DeliveryProofFactContext | None:
        with self._session_factory() as session:
            repository = Repository(session)
            repository.require_case(case_id)
            return self._proof_context(repository, case_id)

    def accept_message(
        self,
        *,
        case_id: str,
        source_message_id: str,
        candidates: Sequence[CaseFactCandidate | dict[str, Any]],
        extractor_kind: str = "deterministic",
        extractor_version: str = EXTRACTOR_VERSION,
    ) -> CaseFactAcceptance:
        batch_hash = candidate_batch_hash(candidates)
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
            outstanding_question = questions[-1]
            outstanding = FactCode(outstanding_question.fact_code)
            source_hash = message_hash(message.content)
            existing_for_message = repository.get_case_fact_consumption_for_message(
                source_message_id
            )
            if existing_for_message is not None:
                if (
                    existing_for_message.case_id != case_id
                    or existing_for_message.question_id != outstanding_question.question_id
                    or existing_for_message.source_message_hash != source_hash
                    or existing_for_message.candidate_batch_hash != batch_hash
                ):
                    raise CaseFactError(
                        "a consumed Case Fact message cannot be replayed with "
                        "different authority inputs"
                    )
                snapshot = self._rebuild(repository, case_id, now=utc_now())
                repository.store_case_fact_snapshot(snapshot)
                return CaseFactAcceptance(
                    snapshot=snapshot,
                    merge_decision=FactMergeDecision.model_validate(
                        existing_for_message.decision_payload
                    ),
                )
            if repository.get_case_fact_consumption_for_question(outstanding_question.question_id):
                raise CaseFactError("outstanding Case Fact question was already consumed")
            if message.case_id != case_id or message.role != "customer":
                raise CaseFactError(
                    "source_message_id must be customer-authored and bound to this Case"
                )
            if message.created_at <= outstanding_question.asked_at:
                raise CaseFactError("source message predates the outstanding Case Fact question")
            customer_messages = [
                row
                for row in repository.list_messages(case.conversation_id)
                if row.case_id == case_id and row.role == "customer"
            ]
            if not customer_messages or customer_messages[-1].message_id != source_message_id:
                raise CaseFactError("source message is not the current customer reply")
            existing = [
                _assertion_from_row(row) for row in repository.list_case_fact_assertions(case_id)
            ]
            proof = self._proof_context(repository, case_id)
            active = [
                item
                for item in existing
                if item.assertion_id
                not in {
                    assertion.supersedes_assertion_id
                    for assertion in existing
                    if assertion.supersedes_assertion_id is not None
                }
            ]
            try:
                parsed = validate_candidate_batch(candidates)
            except Exception:
                parsed = ()
                decision = FactMergeDecision(
                    accepted=False,
                    reason_code="CANDIDATE_SCHEMA_INVALID",
                    candidate_batch_hash=batch_hash,
                )
            else:
                if not parsed:
                    decision = FactMergeDecision(
                        accepted=False,
                        reason_code="NO_CANDIDATES",
                        candidate_batch_hash=batch_hash,
                    )
                elif len(parsed) != 1:
                    decision = FactMergeDecision(
                        accepted=False,
                        reason_code="CANDIDATE_COUNT_REJECTED",
                        candidate_batch_hash=batch_hash,
                    )
                else:
                    candidate = parsed[0]
                    try:
                        bound_proof = validate_candidate(
                            candidate,
                            case_id=case_id,
                            message_case_id=message.case_id,
                            message_role=message.role,
                            message_content=message.content,
                            outstanding_fact_code=outstanding,
                            active_assertions=active,
                            proof_context=proof,
                        )
                    except CaseFactError:
                        decision = FactMergeDecision(
                            accepted=False,
                            reason_code="CANDIDATE_DISAGREES_WITH_PERSISTED_CONTEXT",
                            candidate_batch_hash=batch_hash,
                        )
                    else:
                        fingerprint = stable_json_hash(
                            {
                                "case_id": case_id,
                                "question_id": outstanding_question.question_id,
                                "source_message_id": source_message_id,
                                "candidate": candidate.model_dump(mode="json"),
                                "context_result_hash": (
                                    bound_proof.result_hash if bound_proof else None
                                ),
                            }
                        )
                        assertion = CaseFactAssertion(
                            assertion_id=f"fact_{fingerprint[:24]}",
                            case_id=case_id,
                            fact_code=candidate.fact_code,
                            value=candidate.value,
                            source_message_id=source_message_id,
                            source_message_hash=source_hash,
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
                        repository.append_case_fact_assertion(
                            assertion, candidate_fingerprint=fingerprint
                        )
                        decision = FactMergeDecision(
                            accepted=True,
                            reason_code="ACCEPTED_DETERMINISTIC_INTERPRETATION",
                            assertion_id=assertion.assertion_id,
                            candidate_batch_hash=batch_hash,
                        )
            snapshot = self._rebuild(repository, case_id, now=utc_now())
            repository.store_case_fact_snapshot(snapshot)
            outcome = (
                "accepted"
                if decision.accepted
                else ("empty" if decision.reason_code == "NO_CANDIDATES" else "rejected")
            )
            consumption_id = (
                "consume_"
                + stable_json_hash(
                    {
                        "case_id": case_id,
                        "question_id": outstanding_question.question_id,
                        "source_message_id": source_message_id,
                        "candidate_batch_hash": batch_hash,
                    }
                )[:24]
            )
            repository.append_case_fact_message_consumption(
                consumption_id=consumption_id,
                case_id=case_id,
                question_id=outstanding_question.question_id,
                source_message_id=source_message_id,
                source_message_hash=source_hash,
                candidate_batch_hash=batch_hash,
                outcome=outcome,
                reason_code=decision.reason_code,
                assertion_id=decision.assertion_id,
                decision_payload=decision.model_dump(mode="json"),
                recorded_at=utc_now(),
            )
            return CaseFactAcceptance(snapshot=snapshot, merge_decision=decision)

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

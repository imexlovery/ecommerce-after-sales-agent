"""Case-scoped clarification facts with append-only provenance and pure rebuilds."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

FACT_CANDIDATE_SCHEMA_VERSION = "v3b.fact_candidate.v1"
ASSERTION_SCHEMA_VERSION = "v3b.case_fact_assertion.v1"
SNAPSHOT_SCHEMA_VERSION = "v3b.case_fact_snapshot.v1"
EXTRACTOR_VERSION = "case-fact-deterministic-v1"


class CaseFactError(ValueError):
    """Base class for deterministic Case Fact rejection."""


class CaseFactIntegrityError(CaseFactError):
    """Canonical history or stored projection failed a hash/integrity check."""


class FactCode(StrEnum):
    CUSTOMER_STILL_REPORTS_MISSING = "customer_still_reports_missing"
    REPORTED_DELIVERY_LOCATION_CHECKED = "reported_delivery_location_checked"


class FactValue(StrEnum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


class RelationHint(StrEnum):
    NEW = "new"
    REPEAT = "repeat"
    CORRECTION = "correction"
    WITHDRAWAL = "withdrawal"


class FactStatus(StrEnum):
    KNOWN_TRUE = "known_true"
    KNOWN_FALSE = "known_false"
    UNKNOWN = "unknown"
    CONFLICT = "conflict"


class QuestionStatus(StrEnum):
    INAPPLICABLE = "inapplicable"
    UNANSWERED = "unanswered"
    ANSWERED = "answered"
    UNKNOWN_EXHAUSTED = "unknown_exhausted"
    CONFLICT_REQUIRES_CLARIFICATION = "conflict_requires_clarification"
    CONFLICT_EXHAUSTED = "conflict_exhausted"


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceSpan(_Contract):
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_order(self) -> SourceSpan:
        if self.end <= self.start:
            raise ValueError("source span end must be greater than start")
        return self


class CaseFactCandidate(_Contract):
    """The complete untrusted extraction boundary; no trusted identity is accepted."""

    schema_version: Literal["v3b.fact_candidate.v1"] = "v3b.fact_candidate.v1"
    fact_code: FactCode
    value: FactValue
    relation_hint: RelationHint
    target_assertion_id: str | None = Field(default=None, min_length=1, max_length=64)
    source_span: SourceSpan

    @model_validator(mode="after")
    def validate_relation_shape(self) -> CaseFactCandidate:
        targeted = self.relation_hint in {RelationHint.CORRECTION, RelationHint.WITHDRAWAL}
        if targeted != (self.target_assertion_id is not None):
            raise ValueError("only correction/withdrawal candidates require a target")
        if self.relation_hint is RelationHint.WITHDRAWAL and self.value is not FactValue.UNKNOWN:
            raise ValueError("withdrawal must produce unknown")
        return self


class DeliveryProofFactContext(_Contract):
    tool_call_id: str = Field(min_length=1, max_length=64)
    result_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    recipient_category: str = Field(min_length=1, max_length=64)


class CaseFactAssertion(_Contract):
    schema_version: Literal["v3b.case_fact_assertion.v1"] = "v3b.case_fact_assertion.v1"
    assertion_id: str = Field(min_length=1, max_length=64)
    case_id: str = Field(min_length=1, max_length=64)
    fact_code: FactCode
    value: FactValue
    source_message_id: str = Field(min_length=1, max_length=64)
    source_message_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_span_start: int = Field(ge=0)
    source_span_end: int = Field(gt=0)
    relation: RelationHint
    supersedes_assertion_id: str | None = Field(default=None, max_length=64)
    extractor_kind: Literal["model_candidate", "deterministic"]
    extractor_version: str = Field(min_length=1, max_length=128)
    context_tool_call_id: str | None = Field(default=None, max_length=64)
    context_result_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    assertion_sequence: int = Field(ge=1)
    recorded_at: datetime

    @model_validator(mode="after")
    def validate_binding(self) -> CaseFactAssertion:
        if self.source_span_end <= self.source_span_start:
            raise ValueError("assertion source span is invalid")
        if self.recorded_at.tzinfo is None or self.recorded_at.utcoffset() is None:
            raise ValueError("recorded_at must be timezone-aware")
        targeted = self.relation in {RelationHint.CORRECTION, RelationHint.WITHDRAWAL}
        if targeted != (self.supersedes_assertion_id is not None):
            raise ValueError("supersession identity must match relation")
        location_bound = self.fact_code is FactCode.REPORTED_DELIVERY_LOCATION_CHECKED
        if location_bound != (
            self.context_tool_call_id is not None and self.context_result_hash is not None
        ):
            raise ValueError("delivery-location facts require exact proof context only")
        if self.relation is RelationHint.WITHDRAWAL and self.value is not FactValue.UNKNOWN:
            raise ValueError("withdrawal assertion must be unknown")
        return self


class FactSnapshotEntry(_Contract):
    status: FactStatus
    active_assertion_ids: tuple[str, ...] = ()
    superseded_assertion_ids: tuple[str, ...] = ()
    source_message_ids: tuple[str, ...] = ()
    context_tool_call_id: str | None = None
    context_result_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class FactMergeDecision(_Contract):
    """Deterministic, non-authoritative trace of one candidate disposition."""

    accepted: bool
    reason_code: str = Field(min_length=1, max_length=96)
    assertion_id: str | None = Field(default=None, min_length=1, max_length=64)
    candidate_batch_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class QuestionState(_Contract):
    asks: int = Field(ge=0, le=2)
    status: QuestionStatus


class CaseFactSnapshot(_Contract):
    schema_version: Literal["v3b.case_fact_snapshot.v1"] = "v3b.case_fact_snapshot.v1"
    case_id: str = Field(min_length=1, max_length=64)
    revision: int = Field(ge=0)
    facts: dict[FactCode, FactSnapshotEntry]
    question_state: dict[FactCode, QuestionState]
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    rebuilt_at: datetime

    @model_validator(mode="after")
    def validate_complete_whitelist(self) -> CaseFactSnapshot:
        expected = set(FactCode)
        if set(self.facts) != expected or set(self.question_state) != expected:
            raise ValueError("snapshot must contain the exact Case Fact whitelist")
        if self.rebuilt_at.tzinfo is None or self.rebuilt_at.utcoffset() is None:
            raise ValueError("rebuilt_at must be timezone-aware")
        return self

    def material_identity(self) -> dict[str, Any]:
        active: list[str] = []
        material: dict[str, Any] = {}
        for code in FactCode:
            entry = self.facts[code]
            if entry.status not in {FactStatus.KNOWN_TRUE, FactStatus.KNOWN_FALSE}:
                continue
            active.extend(entry.active_assertion_ids)
            material[code.value] = {
                "status": entry.status.value,
                "active_assertion_ids": list(entry.active_assertion_ids),
            }
            if code is FactCode.REPORTED_DELIVERY_LOCATION_CHECKED:
                material[code.value]["delivery_proof_result_hash"] = entry.context_result_hash
        return {
            "case_fact_snapshot_hash": self.snapshot_hash,
            "active_assertion_ids": sorted(active),
            "material_facts": material,
        }


class CaseFactAcceptance(_Contract):
    """The persisted consumption decision and its rebuilt Case Fact projection."""

    snapshot: CaseFactSnapshot
    merge_decision: FactMergeDecision


class FactQuestion(_Contract):
    question_id: str = Field(min_length=1, max_length=64)
    case_id: str = Field(min_length=1, max_length=64)
    fact_code: FactCode
    context_result_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    targeted_conflict: bool = False
    asked_at: datetime


def message_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def stable_json_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_candidate_batch(
    raw: Sequence[CaseFactCandidate | dict[str, Any]],
) -> tuple[CaseFactCandidate, ...]:
    if len(raw) > 2:
        raise CaseFactError("one customer message may produce at most two Case Fact candidates")
    return tuple(
        item if isinstance(item, CaseFactCandidate) else CaseFactCandidate.model_validate(item)
        for item in raw
    )


def candidate_batch_hash(raw: Sequence[CaseFactCandidate | dict[str, Any]]) -> str:
    """Hash the exact untrusted batch before interpreting it.

    This is deliberately a hash of the model boundary, rather than an accepted
    assertion fingerprint: a consumed customer reply cannot be retried with a
    different value, relation, target, or span.
    """

    return stable_json_hash(
        [
            item.model_dump(mode="json") if isinstance(item, CaseFactCandidate) else item
            for item in raw
        ]
    )


def _authoritative_interpretation(
    *,
    message_content: str,
    fact_code: FactCode,
    active_assertions: Sequence[CaseFactAssertion],
    proof_context: DeliveryProofFactContext | None,
) -> CaseFactCandidate:
    """Derive the only candidate that persisted text can support.

    Candidate fields are an untrusted extraction proposal.  The deterministic
    layer owns semantic value, relationship, target and source span, so a model
    cannot turn a legal enum or arbitrary substring into an authority claim.
    """

    normalized = message_content.casefold()
    unknown_cues = ("不知道", "不清楚", "不确定", "没法确认")
    correction_cues = ("更正", "改一下", "说错", "其实", "不是")
    withdrawal_cues = ("撤回", "不确定")
    if not message_content.strip():
        raise CaseFactError("persisted customer reply is empty")
    if any(cue in normalized for cue in unknown_cues):
        value = FactValue.UNKNOWN
    elif fact_code is FactCode.CUSTOMER_STILL_REPORTS_MISSING:
        if any(cue in normalized for cue in ("已收到", "已经收到", "收到了", "找到了")):
            value = FactValue.FALSE
        elif any(
            cue in normalized
            for cue in (
                "仍然没有收到",
                "仍未收到",
                "还没收到",
                "没有收到",
                "没收到",
                "未收到",
                "没拿到",
            )
        ):
            value = FactValue.TRUE
        else:
            raise CaseFactError("persisted reply does not support this Case Fact")
    else:
        if proof_context is None:
            raise CaseFactError("delivery-location fact is inapplicable without current proof")
        if any(
            cue in normalized for cue in ("还没查", "还没有", "没有查", "没问", "未确认", "没去问")
        ):
            value = FactValue.FALSE
        elif any(cue in normalized for cue in ("问过", "查过", "确认过")):
            # The proof carries the recipient category.  It is intentionally
            # not copied from customer text or exposed to the candidate.
            value = FactValue.TRUE
        else:
            raise CaseFactError("persisted reply does not support this Case Fact")

    same_fact = [item for item in active_assertions if item.fact_code is fact_code]
    latest = same_fact[-1] if same_fact else None
    relation = RelationHint.NEW
    target: str | None = None
    if (
        latest is not None
        and value is FactValue.UNKNOWN
        and any(cue in normalized for cue in withdrawal_cues)
    ):
        relation = RelationHint.WITHDRAWAL
        target = latest.assertion_id
    elif latest is not None and any(cue in normalized for cue in correction_cues):
        if value is FactValue.UNKNOWN or latest.value is FactValue.UNKNOWN or latest.value is value:
            raise CaseFactError("persisted reply does not support this correction")
        relation = RelationHint.CORRECTION
        target = latest.assertion_id
    elif any(item.value is value for item in same_fact):
        relation = RelationHint.REPEAT

    return CaseFactCandidate(
        fact_code=fact_code,
        value=value,
        relation_hint=relation,
        target_assertion_id=target,
        source_span=SourceSpan(start=0, end=len(message_content)),
    )


def validate_candidate(
    candidate: CaseFactCandidate,
    *,
    case_id: str,
    message_case_id: str | None,
    message_role: str,
    message_content: str,
    outstanding_fact_code: FactCode,
    active_assertions: Sequence[CaseFactAssertion],
    proof_context: DeliveryProofFactContext | None,
) -> DeliveryProofFactContext | None:
    if message_case_id != case_id or message_role != "customer":
        raise CaseFactError("source_message_id must be customer-authored and bound to this Case")
    if candidate.fact_code is not outstanding_fact_code:
        raise CaseFactError("candidate does not answer the outstanding Case Fact question")
    authoritative = _authoritative_interpretation(
        message_content=message_content,
        fact_code=outstanding_fact_code,
        active_assertions=active_assertions,
        proof_context=proof_context,
    )
    if candidate != authoritative:
        raise CaseFactError(
            "candidate value, relation, target, or span disagrees with deterministic interpretation"
        )
    return (
        proof_context
        if candidate.fact_code is FactCode.REPORTED_DELIVERY_LOCATION_CHECKED
        else None
    )


def interpret_customer_reply(
    *,
    message_content: str,
    fact_code: FactCode,
    active_assertions: Sequence[CaseFactAssertion],
    proof_context: DeliveryProofFactContext | None,
) -> CaseFactCandidate | None:
    """Return one deterministic candidate, or none when the reply is unsupported."""

    try:
        return _authoritative_interpretation(
            message_content=message_content,
            fact_code=fact_code,
            active_assertions=active_assertions,
            proof_context=proof_context,
        )
    except CaseFactError:
        return None


def _entry_for(
    code: FactCode,
    assertions: Sequence[CaseFactAssertion],
    current_proof: DeliveryProofFactContext | None,
    *,
    invalid_supersession: bool,
    competing_supersession: bool,
) -> FactSnapshotEntry:
    superseded_ids = {
        item.supersedes_assertion_id
        for item in assertions
        if item.supersedes_assertion_id is not None
    }
    active = [item for item in assertions if item.assertion_id not in superseded_ids]
    known = {item.value for item in active if item.value is not FactValue.UNKNOWN}
    status = FactStatus.UNKNOWN
    if invalid_supersession or competing_supersession or len(known) > 1:
        status = FactStatus.CONFLICT
    elif any(item.relation is RelationHint.WITHDRAWAL for item in active):
        # A validated withdrawal is the only unknown claim that retracts the
        # fact's current value.  Repeats from older provenance cannot revive it.
        status = FactStatus.UNKNOWN
    elif known == {FactValue.TRUE}:
        status = FactStatus.KNOWN_TRUE
    elif known == {FactValue.FALSE}:
        status = FactStatus.KNOWN_FALSE

    context_tool_call_id: str | None = None
    context_result_hash: str | None = None
    if code is FactCode.REPORTED_DELIVERY_LOCATION_CHECKED:
        contexts = {(item.context_tool_call_id, item.context_result_hash) for item in active}
        if len(contexts) > 1:
            status = FactStatus.CONFLICT
        elif contexts:
            context_tool_call_id, context_result_hash = next(iter(contexts))
            if current_proof is None or (
                context_tool_call_id,
                context_result_hash,
            ) != (
                current_proof.tool_call_id,
                current_proof.result_hash,
            ):
                status = FactStatus.UNKNOWN

    return FactSnapshotEntry(
        status=status,
        active_assertion_ids=tuple(item.assertion_id for item in active),
        superseded_assertion_ids=tuple(
            item.assertion_id for item in assertions if item.assertion_id in superseded_ids
        ),
        source_message_ids=tuple(dict.fromkeys(item.source_message_id for item in active)),
        context_tool_call_id=context_tool_call_id,
        context_result_hash=context_result_hash,
    )


def rebuild_case_fact_snapshot(
    *,
    case_id: str,
    assertions: Sequence[CaseFactAssertion],
    questions: Sequence[FactQuestion],
    current_proof: DeliveryProofFactContext | None,
    rebuilt_at: datetime,
) -> CaseFactSnapshot:
    ordered = sorted(assertions, key=lambda item: item.assertion_sequence)
    if [item.assertion_sequence for item in ordered] != list(range(1, len(ordered) + 1)):
        raise CaseFactIntegrityError("assertion_sequence must be contiguous and monotonic")
    ids: dict[str, CaseFactAssertion] = {}
    invalid_supersession: set[FactCode] = set()
    competing_supersession: set[FactCode] = set()
    superseders_by_target: dict[str, list[CaseFactAssertion]] = defaultdict(list)
    for item in ordered:
        if item.case_id != case_id or item.assertion_id in ids:
            raise CaseFactIntegrityError("assertion Case/id integrity failed")
        if item.supersedes_assertion_id is not None:
            target = ids.get(item.supersedes_assertion_id)
            if target is None or target.fact_code is not item.fact_code:
                # Historical corruption must not select a winner.  Preserve the
                # row for audit and surface a gate-blocking conflict instead.
                invalid_supersession.add(item.fact_code)
            else:
                superseders_by_target[target.assertion_id].append(item)
        ids[item.assertion_id] = item

    for target_id, superseders in superseders_by_target.items():
        if len(superseders) > 1:
            competing_supersession.add(ids[target_id].fact_code)

    grouped: dict[FactCode, list[CaseFactAssertion]] = defaultdict(list)
    for item in ordered:
        grouped[item.fact_code].append(item)
    facts = {
        code: _entry_for(
            code,
            grouped[code],
            current_proof,
            invalid_supersession=code in invalid_supersession,
            competing_supersession=code in competing_supersession,
        )
        for code in FactCode
    }

    question_state: dict[FactCode, QuestionState] = {}
    for code in FactCode:
        asks = sum(
            1
            for question in questions
            if question.case_id == case_id and question.fact_code is code
        )
        entry = facts[code]
        if code is FactCode.REPORTED_DELIVERY_LOCATION_CHECKED and current_proof is None:
            status = QuestionStatus.INAPPLICABLE
        elif entry.status in {FactStatus.KNOWN_TRUE, FactStatus.KNOWN_FALSE}:
            status = QuestionStatus.ANSWERED
        elif entry.status is FactStatus.CONFLICT:
            targeted = sum(
                1
                for question in questions
                if question.case_id == case_id
                and question.fact_code is code
                and question.targeted_conflict
            )
            status = (
                QuestionStatus.CONFLICT_EXHAUSTED
                if targeted >= 1
                else QuestionStatus.CONFLICT_REQUIRES_CLARIFICATION
            )
        elif asks:
            status = QuestionStatus.UNKNOWN_EXHAUSTED
        else:
            status = QuestionStatus.UNANSWERED
        question_state[code] = QuestionState(asks=asks, status=status)

    revision = len(ordered) + len([item for item in questions if item.case_id == case_id])
    canonical = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "case_id": case_id,
        "revision": revision,
        "facts": {key.value: value.model_dump(mode="json") for key, value in facts.items()},
        "question_state": {
            key.value: value.model_dump(mode="json") for key, value in question_state.items()
        },
    }
    return CaseFactSnapshot(
        **canonical,
        snapshot_hash=stable_json_hash(canonical),
        rebuilt_at=rebuilt_at,
    )


def stable_question_id(
    *,
    case_id: str,
    fact_code: FactCode,
    context_result_hash: str | None,
    targeted_conflict: bool,
) -> str:
    digest = stable_json_hash(
        {
            "case_id": case_id,
            "fact_code": fact_code.value,
            "context_result_hash": context_result_hash,
            "targeted_conflict": targeted_conflict,
        }
    )
    return f"qfact_{digest[:24]}"


def question_allowed(snapshot: CaseFactSnapshot, fact_code: FactCode, *, global_asks: int) -> bool:
    if global_asks >= 2:
        return False
    state = snapshot.question_state[fact_code]
    if state.status in {
        QuestionStatus.INAPPLICABLE,
        QuestionStatus.ANSWERED,
        QuestionStatus.UNKNOWN_EXHAUSTED,
        QuestionStatus.CONFLICT_EXHAUSTED,
    }:
        return False
    if state.status is QuestionStatus.CONFLICT_REQUIRES_CLARIFICATION:
        return state.asks < 2
    return state.asks == 0


__all__ = [
    "ASSERTION_SCHEMA_VERSION",
    "CaseFactAcceptance",
    "CaseFactAssertion",
    "CaseFactCandidate",
    "CaseFactError",
    "CaseFactIntegrityError",
    "FactMergeDecision",
    "CaseFactSnapshot",
    "DeliveryProofFactContext",
    "EXTRACTOR_VERSION",
    "FactCode",
    "FactQuestion",
    "FactStatus",
    "FactValue",
    "QuestionStatus",
    "RelationHint",
    "SourceSpan",
    "message_hash",
    "candidate_batch_hash",
    "interpret_customer_reply",
    "question_allowed",
    "rebuild_case_fact_snapshot",
    "stable_question_id",
    "validate_candidate",
    "validate_candidate_batch",
]

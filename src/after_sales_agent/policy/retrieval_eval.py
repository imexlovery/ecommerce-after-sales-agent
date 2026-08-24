"""Independent, versioned development evaluation for Controlled Policy RAG.

This evaluator deliberately does not reuse the Phase 1 Agent/Workflow matrix or
execute the future locked retrieval set.  It validates the locked schema and
manifest digest only, then records every development result and error.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from after_sales_agent.config import PolicyRetrievalMode, Settings
from after_sales_agent.domain.state import IssueType, PolicyResolutionStatus, RetrievalStatus
from after_sales_agent.policy.corpus import canonical_json_hash
from after_sales_agent.policy.rag import (
    PolicyRagService,
    PolicyRetrievalUnavailable,
    build_policy_rag,
)
from after_sales_agent.tools.contracts import PolicySearchPayload

RETRIEVAL_EVAL_CONTRACT_VERSION = "retrieval-eval-v1"
RETRIEVAL_GRADER_REGISTRY_VERSION = "retrieval-graders-v1"


class RetrievalEvalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RetrievalEvalCase(RetrievalEvalModel):
    case_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,79}$")
    query: str = Field(min_length=1, max_length=1_000)
    issue_type: IssueType
    service_level: str = Field(min_length=1)
    evaluated_at: datetime
    expected_retrieval_status: RetrievalStatus
    expected_resolution_status: PolicyResolutionStatus | None = None
    expected_clause_id: str | None = None
    critical_policy: bool = False
    assertions: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_case(self) -> RetrievalEvalCase:
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("retrieval evaluation timestamp must be timezone-aware")
        if len(self.assertions) != len(set(self.assertions)):
            raise ValueError("retrieval evaluation assertion IDs must not repeat")
        if self.expected_retrieval_status is RetrievalStatus.HIT:
            if self.expected_resolution_status is None:
                raise ValueError("expected retrieval hit requires a resolution expectation")
        elif self.expected_resolution_status is not None:
            raise ValueError("no_hit/unavailable cannot expect a fabricated resolution")
        if self.critical_policy and self.expected_clause_id is None:
            raise ValueError("critical retrieval cases require an expected clause ID")
        return self


class RetrievalEvalManifest(RetrievalEvalModel):
    schema_version: Literal[1] = 1
    dataset_version: str = Field(min_length=1)
    dataset_partition: Literal["development", "locked"]
    cases: tuple[RetrievalEvalCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_manifest(self) -> RetrievalEvalManifest:
        identifiers = [item.case_id for item in self.cases]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("retrieval evaluation case IDs must be unique")
        return self

    @property
    def digest(self) -> str:
        return canonical_json_hash(self.model_dump(mode="json"))


class RetrievalAssertionResult(RetrievalEvalModel):
    assertion_id: str = Field(min_length=1)
    passed: bool
    hard_safety: bool
    detail: str = Field(min_length=1)


class RetrievalEvalRecord(RetrievalEvalModel):
    case_id: str
    started_at: datetime
    completed_at: datetime
    duration_ms: float = Field(ge=0)
    retrieval_status: RetrievalStatus
    policy_resolution_status: PolicyResolutionStatus | None = None
    candidate_clause_ids: tuple[str, ...] = Field(default_factory=tuple)
    citation_clause_id: str | None = None
    policy_version: str | None = None
    proposal_eligible_count: int = Field(ge=0, le=1)
    error_code: str | None = None
    assertions: tuple[RetrievalAssertionResult, ...]

    @model_validator(mode="after")
    def validate_record(self) -> RetrievalEvalRecord:
        if self.completed_at < self.started_at:
            raise ValueError("retrieval evaluation completed_at cannot precede started_at")
        if (
            self.retrieval_status is not RetrievalStatus.HIT
            and self.policy_resolution_status is not None
        ):
            raise ValueError("no_hit/unavailable records cannot claim a resolution")
        return self


class RetrievalDevelopmentReport(RetrievalEvalModel):
    schema_version: Literal[1] = 1
    report_id: str = Field(min_length=1)
    evaluation_revision: str = Field(min_length=1)
    created_at: datetime
    evaluation_contract_version: str = RETRIEVAL_EVAL_CONTRACT_VERSION
    grader_registry_version: str = RETRIEVAL_GRADER_REGISTRY_VERSION
    development_dataset_version: str
    development_manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    locked_dataset_version: str
    locked_manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    locked_manifest_schema_valid: bool
    locked_manifest_executed: Literal[False] = False
    provenance: dict[str, str]
    records: tuple[RetrievalEvalRecord, ...]
    metrics: dict[str, float | int | bool]
    quality_pass: bool
    safety_gate_pass: bool


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _manifest_path(partition: Literal["development", "locked"]) -> Path:
    return _project_root() / "evals" / "retrieval" / f"{partition}-v1.json"


def load_retrieval_manifest(
    partition: Literal["development", "locked"],
) -> RetrievalEvalManifest:
    path = _manifest_path(partition)
    manifest = RetrievalEvalManifest.model_validate_json(path.read_text(encoding="utf-8"))
    if manifest.dataset_partition != partition:
        raise ValueError(f"retrieval manifest partition mismatch: {path}")
    return manifest


def retrieval_grader_registry_digest() -> str:
    return canonical_json_hash(
        {
            "version": RETRIEVAL_GRADER_REGISTRY_VERSION,
            "assertions": sorted(_GRADER_CATEGORIES.items()),
        }
    )


_GRADER_CATEGORIES: dict[str, bool] = {
    "expected_retrieval_status": False,
    "expected_resolution_status": False,
    "critical_recall_at_3": False,
    "verified_citation_correct": False,
    "policy_version_clause_correct": False,
    "provenance_complete": False,
    "proposal_zero": True,
    "poison_cannot_propose": True,
    "version_conflict_cannot_propose": True,
}


def _proposal_eligible(payload: PolicySearchPayload | None) -> int:
    if payload is None or not payload.verified_for_gate or payload.policy_fact_snapshot is None:
        return 0
    return int(payload.policy_fact_snapshot.eligible)


def _provenance_complete(provenance: dict[str, str]) -> bool:
    required = {
        "policy_rag_contract_version",
        "corpus_version",
        "corpus_digest",
        "chunker_version",
        "index_format_version",
        "index_digest",
        "embedding_mode",
        "embedding_package",
        "embedding_package_version",
        "embedding_model_id",
        "embedding_model_revision",
    }
    return required.issubset(provenance) and all(provenance[key] for key in required)


def _grade(
    *,
    case: RetrievalEvalCase,
    payload: PolicySearchPayload | None,
    error_code: str | None,
    provenance: dict[str, str],
) -> tuple[RetrievalAssertionResult, ...]:
    actual_status = payload.retrieval_status if payload is not None else RetrievalStatus.UNAVAILABLE
    actual_resolution = payload.policy_resolution_status if payload is not None else None
    actual_clause = payload.citation.clause_id if payload and payload.citation else None
    actual_version = (
        payload.policy_fact_snapshot.policy_version
        if payload and payload.policy_fact_snapshot is not None
        else None
    )
    proposal_count = _proposal_eligible(payload)
    results: list[RetrievalAssertionResult] = []
    for assertion_id in case.assertions:
        hard_safety = _GRADER_CATEGORIES.get(assertion_id, True)
        if assertion_id not in _GRADER_CATEGORIES:
            results.append(
                RetrievalAssertionResult(
                    assertion_id=assertion_id,
                    passed=False,
                    hard_safety=True,
                    detail="unknown retrieval assertion fails closed",
                )
            )
            continue
        if assertion_id == "expected_retrieval_status":
            passed = actual_status is case.expected_retrieval_status
            detail = (
                f"expected={case.expected_retrieval_status.value}, actual={actual_status.value}"
            )
        elif assertion_id == "expected_resolution_status":
            passed = actual_resolution is case.expected_resolution_status
            expected_resolution = (
                case.expected_resolution_status.value if case.expected_resolution_status else None
            )
            observed_resolution = actual_resolution.value if actual_resolution else None
            detail = f"expected={expected_resolution}, actual={observed_resolution}"
        elif assertion_id == "critical_recall_at_3":
            candidate_ids = payload.candidate_clause_ids if payload else ()
            passed = case.expected_clause_id in candidate_ids
            detail = f"expected={case.expected_clause_id}, candidates={candidate_ids}"
        elif assertion_id == "verified_citation_correct":
            passed = bool(
                payload
                and payload.verified_for_gate
                and actual_clause == case.expected_clause_id
                and payload.citation is not None
                and payload.policy_fact_snapshot is not None
                and payload.citation.source_hash == payload.policy_fact_snapshot.source_hash
            )
            detail = f"expected_clause={case.expected_clause_id}, actual_clause={actual_clause}"
        elif assertion_id == "policy_version_clause_correct":
            citation_version = (
                payload.citation.policy_version if payload and payload.citation else None
            )
            passed = bool(
                payload
                and payload.policy_fact_snapshot is not None
                and actual_clause == case.expected_clause_id
                and actual_version == citation_version
            )
            detail = (
                f"expected_clause={case.expected_clause_id}, "
                f"actual={actual_version}/{actual_clause}"
            )
        elif assertion_id == "provenance_complete":
            passed = _provenance_complete(provenance)
            detail = "corpus/index/chunker/embedding provenance present"
        elif assertion_id == "proposal_zero":
            passed = proposal_count == 0
            detail = f"proposal_eligible_count={proposal_count}"
        elif assertion_id == "poison_cannot_propose":
            passed = (
                proposal_count == 0 and actual_resolution is not PolicyResolutionStatus.APPLICABLE
            )
            detail = f"resolution={actual_resolution}, proposal_eligible_count={proposal_count}"
        elif assertion_id == "version_conflict_cannot_propose":
            passed = (
                proposal_count == 0 and actual_resolution is PolicyResolutionStatus.VERSION_CONFLICT
            )
            detail = f"resolution={actual_resolution}, proposal_eligible_count={proposal_count}"
        else:  # Exhaustive with the explicit registry; keep error context in every record.
            passed = False
            detail = f"unimplemented assertion; error_code={error_code}"
        results.append(
            RetrievalAssertionResult(
                assertion_id=assertion_id,
                passed=passed,
                hard_safety=hard_safety,
                detail=detail,
            )
        )
    return tuple(results)


def _run_case(
    *,
    service: PolicyRagService,
    case: RetrievalEvalCase,
    provenance: dict[str, str],
) -> RetrievalEvalRecord:
    started = datetime.now(tz=UTC)
    timer = time.perf_counter()
    payload: PolicySearchPayload | None = None
    error_code: str | None = None
    try:
        payload = service.search_for_evaluation(
            query=case.query,
            issue_type=case.issue_type,
            service_level=case.service_level,
            evaluated_at=case.evaluated_at,
        )
    except PolicyRetrievalUnavailable as exc:
        error_code = exc.code
    except Exception:
        error_code = "POLICY_RETRIEVAL_EVAL_UNEXPECTED_ERROR"
    completed = datetime.now(tz=UTC)
    assertions = _grade(
        case=case,
        payload=payload,
        error_code=error_code,
        provenance=provenance,
    )
    return RetrievalEvalRecord(
        case_id=case.case_id,
        started_at=started,
        completed_at=completed,
        duration_ms=(time.perf_counter() - timer) * 1_000,
        retrieval_status=(payload.retrieval_status if payload else RetrievalStatus.UNAVAILABLE),
        policy_resolution_status=(payload.policy_resolution_status if payload else None),
        candidate_clause_ids=(payload.candidate_clause_ids if payload else ()),
        citation_clause_id=(payload.citation.clause_id if payload and payload.citation else None),
        policy_version=(
            payload.policy_fact_snapshot.policy_version
            if payload and payload.policy_fact_snapshot is not None
            else None
        ),
        proposal_eligible_count=_proposal_eligible(payload),
        error_code=error_code,
        assertions=assertions,
    )


def _metrics(records: tuple[RetrievalEvalRecord, ...]) -> dict[str, float | int | bool]:
    assertion_results = [result for record in records for result in record.assertions]
    by_id: dict[str, list[RetrievalAssertionResult]] = {}
    for result in assertion_results:
        by_id.setdefault(result.assertion_id, []).append(result)

    def ratio(assertion_id: str) -> float:
        values = by_id.get(assertion_id, [])
        return (sum(value.passed for value in values) / len(values)) if values else 0.0

    return {
        "raw_run_count": len(records),
        "error_count": sum(record.error_code is not None for record in records),
        "critical_recall_at_3": ratio("critical_recall_at_3"),
        "verified_citation_correctness": ratio("verified_citation_correct"),
        "policy_clause_version_correctness": ratio("policy_version_clause_correct"),
        "resolution_accuracy": ratio("expected_resolution_status"),
        "no_proposal_count": sum(record.proposal_eligible_count == 0 for record in records),
        "max_duration_ms": max((record.duration_ms for record in records), default=0.0),
    }


def run_development_retrieval_eval(
    *,
    settings: Settings,
    evaluation_revision: str,
    service: PolicyRagService | None = None,
) -> tuple[RetrievalDevelopmentReport, Path]:
    """Execute only versioned development queries and persist every outcome."""

    if settings.policy_retrieval_mode is not PolicyRetrievalMode.REAL_LOCAL:
        raise ValueError(
            "development retrieval evaluation requires POLICY_RETRIEVAL_MODE=real_local"
        )
    development = load_retrieval_manifest("development")
    locked = load_retrieval_manifest("locked")
    rag = service or build_policy_rag(settings)
    provenance = rag.provenance()
    records = tuple(
        _run_case(service=rag, case=case, provenance=provenance) for case in development.cases
    )
    all_assertions = [assertion for record in records for assertion in record.assertions]
    quality_pass = all(
        assertion.passed for assertion in all_assertions if not assertion.hard_safety
    )
    safety_gate_pass = all(
        assertion.passed for assertion in all_assertions if assertion.hard_safety
    )
    report = RetrievalDevelopmentReport(
        report_id=f"retrieval-development-{evaluation_revision}",
        evaluation_revision=evaluation_revision,
        created_at=datetime.now(tz=UTC),
        development_dataset_version=development.dataset_version,
        development_manifest_digest=development.digest,
        locked_dataset_version=locked.dataset_version,
        locked_manifest_digest=locked.digest,
        locked_manifest_schema_valid=True,
        provenance={
            **provenance,
            "grader_registry_digest": retrieval_grader_registry_digest(),
        },
        records=records,
        metrics=_metrics(records),
        quality_pass=quality_pass,
        safety_gate_pass=safety_gate_pass,
    )
    settings.policy_retrieval_eval_artifact_root.mkdir(parents=True, exist_ok=True)
    path = settings.policy_retrieval_eval_artifact_root / f"{evaluation_revision}.json"
    path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return report, path

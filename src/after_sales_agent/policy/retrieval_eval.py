"""Independent, versioned development evaluation for Controlled Policy RAG.

This evaluator deliberately does not reuse the Phase 1 Agent/Workflow matrix or
execute the future locked retrieval set.  It validates the locked schema and
manifest digest only, then records every development result and error.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any, Literal, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from after_sales_agent.config import LLMMode, PolicyRetrievalMode, Settings
from after_sales_agent.domain.state import IssueType, PolicyResolutionStatus, RetrievalStatus
from after_sales_agent.evals.contracts import EvaluationFreeze
from after_sales_agent.policy.corpus import (
    PolicyClause,
    PolicyCorpus,
    build_policy_corpus_v1,
    canonical_json_hash,
)
from after_sales_agent.policy.rag import (
    PolicyRagService,
    PolicyResolutionIntegrityError,
    PolicyRetrievalUnavailable,
    RetrievedCandidate,
    build_policy_rag,
)
from after_sales_agent.tools.contracts import PolicySearchPayload

RETRIEVAL_EVAL_CONTRACT_VERSION = "retrieval-eval-v4-policy-label-integrity"
RETRIEVAL_GRADER_REGISTRY_VERSION = "retrieval-graders-v3"
RETRIEVAL_DEVELOPMENT_MANIFEST_FILENAME = "development-v3.json"
RETRIEVAL_LOCKED_MANIFEST_FILENAME = "locked-v4.json"


class RetrievalEvalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


RetrievalAssertionCategory = Literal["quality", "safety"]
RetrievalFaultMode = Literal[
    "none",
    "retriever_unavailable",
    "citation_mismatch",
    "retriever_timeout",
]


class RetrievalAssertionDeclaration(RetrievalEvalModel):
    assertion_id: str = Field(min_length=1)
    category: RetrievalAssertionCategory


class RetrievalManifestLabelIntegrityError(ValueError):
    """A manifest label that contradicts canonical structured policy facts."""


class RetrievalEvalCase(RetrievalEvalModel):
    case_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,79}$")
    query: str = Field(min_length=1, max_length=1_000)
    issue_type: IssueType
    service_level: str = Field(min_length=1)
    region: str = Field(min_length=1)
    evaluated_at: datetime
    expected_retrieval_status: RetrievalStatus
    expected_resolution_status: PolicyResolutionStatus | None = None
    expected_clause_id: str | None = None
    expected_eligible: bool | None = None
    critical_policy: bool = False
    fault_mode: RetrievalFaultMode = "none"
    assertions: tuple[RetrievalAssertionDeclaration, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_case(self) -> RetrievalEvalCase:
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("retrieval evaluation timestamp must be timezone-aware")
        assertion_ids = [item.assertion_id for item in self.assertions]
        if len(assertion_ids) != len(set(assertion_ids)):
            raise ValueError("retrieval evaluation assertion IDs must not repeat")
        if self.expected_retrieval_status is RetrievalStatus.HIT:
            if self.expected_resolution_status is None:
                raise ValueError("expected retrieval hit requires a resolution expectation")
        else:
            if self.expected_resolution_status is not None:
                raise ValueError("no_hit/unavailable cannot expect a fabricated resolution")
            if self.expected_clause_id is not None:
                raise ValueError("no_hit/unavailable cannot declare an expected clause")
        if self.expected_resolution_status is not PolicyResolutionStatus.APPLICABLE:
            if self.expected_clause_id is not None:
                raise ValueError("only applicable labels may declare an expected clause")
            if self.expected_eligible is not None:
                raise ValueError("only applicable labels may declare expected eligibility")
        if self.critical_policy and self.expected_clause_id is None:
            raise ValueError("critical retrieval cases require an expected clause ID")
        return self


class RetrievalEvalManifest(RetrievalEvalModel):
    schema_version: Literal[2, 3] = 3
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
        payload = self.model_dump(mode="json")
        # Keep the historical v2 digest stable: ``expected_eligible`` is a v3
        # contract field and must not appear as null in older manifests.
        for case in payload["cases"]:
            if case.get("expected_eligible") is None:
                case.pop("expected_eligible", None)
        return canonical_json_hash(payload)


def _active_canonical_authorities(
    *,
    corpus: PolicyCorpus,
    case: RetrievalEvalCase,
) -> tuple[PolicyClause, ...]:
    """Return the complete authority set for a manifest case.

    This intentionally uses only canonical normalized facts. It does not inspect
    retrieval candidates, scores, index contents, or model output. In
    particular, ``eligible`` is not an applicability filter: an active clause
    with ``eligible=false`` is still an applicable policy authority.
    """

    return tuple(
        clause
        for clause in corpus.clauses
        if (
            not clause.poisoned
            and clause.normalized_facts.issue_type == case.issue_type
            and clause.normalized_facts.service_level == case.service_level
            and clause.normalized_facts.region == case.region
            and clause.normalized_facts.effective_from <= case.evaluated_at
            and (
                clause.normalized_facts.effective_to is None
                or case.evaluated_at < clause.normalized_facts.effective_to
            )
        )
    )


def _label_integrity_error(case: RetrievalEvalCase, detail: str) -> None:
    raise RetrievalManifestLabelIntegrityError(
        f"evaluation_label_contract_drift: case={case.case_id}; {detail}"
    )


def validate_retrieval_manifest_label_integrity(
    manifest: RetrievalEvalManifest,
    *,
    corpus: PolicyCorpus | None = None,
) -> None:
    """Fail closed when expected resolution labels contradict canonical facts."""

    authority_corpus = corpus or build_policy_corpus_v1()
    authority_corpus.verify()
    for case in manifest.cases:
        if case.expected_retrieval_status is not RetrievalStatus.HIT:
            continue
        authorities = _active_canonical_authorities(corpus=authority_corpus, case=case)
        versions = {clause.normalized_facts.policy_version for clause in authorities}
        expected = case.expected_resolution_status
        if len(authorities) > 1 and len(versions) == 1:
            _label_integrity_error(
                case,
                "canonical authority set contains duplicate active clauses for one policy version",
            )
        if expected is PolicyResolutionStatus.APPLICABLE:
            if len(authorities) != 1:
                _label_integrity_error(
                    case,
                    "expected=applicable but canonical active authority count="
                    f"{len(authorities)}",
                )
            authority = authorities[0]
            if (
                case.expected_clause_id is not None
                and case.expected_clause_id != authority.clause_id
            ):
                _label_integrity_error(
                    case,
                    f"expected_clause={case.expected_clause_id}, "
                    f"canonical_clause={authority.clause_id}",
                )
        elif expected is PolicyResolutionStatus.NOT_APPLICABLE:
            if authorities:
                _label_integrity_error(
                    case,
                    "expected=not_applicable but canonical active authority exists: "
                    + ", ".join(clause.clause_id for clause in authorities),
                )
        elif expected is PolicyResolutionStatus.VERSION_CONFLICT:
            if len(versions) < 2:
                _label_integrity_error(
                    case,
                    "expected=version_conflict but canonical active policy versions are "
                    + ", ".join(sorted(versions)),
                )


class PolicyRagFingerprint(RetrievalEvalModel):
    """Stable Policy RAG identity used by Freeze comparison.

    The derived index timestamp and artifact digest are intentionally excluded:
    rebuilding byte-equivalent index content must not change this identity.
    """

    policy_rag_contract_version: str = Field(min_length=1)
    corpus_version: str = Field(min_length=1)
    corpus_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    chunker_version: str = Field(min_length=1)
    index_format_version: str = Field(min_length=1)
    index_content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    embedding_mode: str = Field(min_length=1)
    embedding_package: str = Field(min_length=1)
    embedding_package_version: str = Field(min_length=1)
    embedding_model_id: str = Field(min_length=1)
    embedding_model_revision: str = Field(min_length=1)
    retrieval_top_k: int = Field(ge=1, le=3)
    minimum_similarity: float = Field(ge=-1, le=1)

    @property
    def digest(self) -> str:
        return canonical_json_hash(self.model_dump(mode="json"))


def policy_rag_fingerprint(service: PolicyRagService) -> PolicyRagFingerprint:
    provenance = service.provenance()
    return PolicyRagFingerprint(
        policy_rag_contract_version=provenance["policy_rag_contract_version"],
        corpus_version=provenance["corpus_version"],
        corpus_digest=provenance["corpus_digest"],
        chunker_version=provenance["chunker_version"],
        index_format_version=provenance["index_format_version"],
        index_content_digest=provenance["index_content_digest"],
        embedding_mode=provenance["embedding_mode"],
        embedding_package=provenance["embedding_package"],
        embedding_package_version=provenance["embedding_package_version"],
        embedding_model_id=provenance["embedding_model_id"],
        embedding_model_revision=provenance["embedding_model_revision"],
        retrieval_top_k=service.top_k,
        minimum_similarity=service.minimum_similarity,
    )


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
    top_1_score: float | None = Field(default=None, ge=-1.0, le=1.0)
    retrieval_threshold: float = Field(ge=-1.0, le=1.0)
    retrieval_latency_ms: float = Field(ge=0)
    resolver_latency_ms: float = Field(ge=0)
    timed_out: bool = False
    application_proposal_count: int = Field(ge=0)
    application_action_count: int = Field(ge=0)
    application_ticket_count: int = Field(ge=0)
    application_error_code: str | None = None
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
        if self.timed_out and self.error_code != "RETRIEVAL_EVAL_TIMEOUT":
            raise ValueError("timed out retrieval records require RETRIEVAL_EVAL_TIMEOUT")
        return self


class RetrievalDevelopmentReport(RetrievalEvalModel):
    schema_version: Literal[2, 3] = 3
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
    source_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    source_tree_state: Literal["clean", "dirty", "unavailable"] | None = None
    llm_mode: Literal["mock"] | None = None
    retrieval_mode: Literal["real_local"] | None = None
    rag_fingerprint: PolicyRagFingerprint | None = None
    records: tuple[RetrievalEvalRecord, ...]
    metrics: dict[str, Any]
    quality_pass: bool
    safety_gate_pass: bool

    @property
    def digest(self) -> str:
        return canonical_json_hash(self.model_dump(mode="json"))

    @property
    def freeze_eligible(self) -> bool:
        fingerprint = self.rag_fingerprint
        return bool(
            self.schema_version == 3
            and self.source_revision
            and self.source_tree_state == "clean"
            and self.llm_mode == "mock"
            and self.retrieval_mode == "real_local"
            and self.locked_manifest_schema_valid
            and fingerprint is not None
            and fingerprint.embedding_mode == PolicyRetrievalMode.REAL_LOCAL.value
            and bool(self.records)
            and self.quality_pass
            and self.safety_gate_pass
        )


class RetrievalLockedReport(RetrievalEvalModel):
    """Append-only single-run acceptance record for deterministic local retrieval."""

    schema_version: Literal[1] = 1
    report_id: str = Field(min_length=1)
    evaluation_revision: str = Field(min_length=1)
    created_at: datetime
    source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_tree_state: Literal["clean"] = "clean"
    freeze_schema_version: Literal[3] = 3
    freeze_evaluation_revision: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluation_contract_version: str = RETRIEVAL_EVAL_CONTRACT_VERSION
    grader_registry_version: str = RETRIEVAL_GRADER_REGISTRY_VERSION
    grader_registry_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_count_per_case: Literal[1] = 1
    planned_case_count: int = Field(ge=1)
    retrieval_timeout_seconds: float = Field(gt=0)
    llm_mode: Literal["mock"] = "mock"
    application_probe_llm_mode: Literal["mock"] = "mock"
    retrieval_mode: Literal["real_local"] = "real_local"
    rag_fingerprint: PolicyRagFingerprint
    provenance: dict[str, str]
    records: tuple[RetrievalEvalRecord, ...]
    metrics: dict[str, Any]
    quality_gate_pass: bool
    safety_gate_pass: bool
    acceptance_gate_pass: bool

    @model_validator(mode="after")
    def validate_locked_report(self) -> RetrievalLockedReport:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("retrieval locked report created_at must be timezone-aware")
        if not self.records:
            raise ValueError("retrieval locked report must retain every planned record")
        if len(self.records) != self.planned_case_count:
            raise ValueError("retrieval locked report record count differs from its planned cases")
        case_ids = [record.case_id for record in self.records]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("retrieval locked report case IDs must be unique")
        expected_counts = {
            "raw_run_count": len(self.records),
            "error_count": sum(record.error_code is not None for record in self.records),
            "unavailable_count": sum(
                record.retrieval_status is RetrievalStatus.UNAVAILABLE for record in self.records
            ),
            "timeout_count": sum(record.timed_out for record in self.records),
            "application_proposal_count": sum(
                record.application_proposal_count for record in self.records
            ),
            "application_action_count": sum(
                record.application_action_count for record in self.records
            ),
            "application_ticket_count": sum(
                record.application_ticket_count for record in self.records
            ),
        }
        for key, expected in expected_counts.items():
            if self.metrics.get(key) != expected:
                raise ValueError(f"retrieval locked report metrics do not retain {key}")
        if self.acceptance_gate_pass != (self.quality_gate_pass and self.safety_gate_pass):
            raise ValueError("retrieval acceptance must equal independent quality and safety gates")
        return self


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _manifest_path(partition: Literal["development", "locked"]) -> Path:
    filename = (
        RETRIEVAL_DEVELOPMENT_MANIFEST_FILENAME
        if partition == "development"
        else RETRIEVAL_LOCKED_MANIFEST_FILENAME
    )
    return _project_root() / "evals" / "retrieval" / filename


def load_retrieval_manifest(
    partition: Literal["development", "locked"],
    *,
    path: Path | None = None,
) -> RetrievalEvalManifest:
    manifest_path = path or _manifest_path(partition)
    manifest = RetrievalEvalManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    if manifest.dataset_partition != partition:
        raise ValueError(f"retrieval manifest partition mismatch: {manifest_path}")
    validate_retrieval_manifest_label_integrity(manifest)
    validate_retrieval_manifest_grader_contract(
        manifest,
        require_independent_gates=partition == "locked",
    )
    return manifest


def load_retrieval_development_report(
    *,
    artifact_root: Path,
    evaluation_revision: str,
) -> RetrievalDevelopmentReport:
    path = artifact_root / f"{evaluation_revision}.json"
    if not path.exists():
        raise FileNotFoundError(f"retrieval development report does not exist: {path}")
    report = RetrievalDevelopmentReport.model_validate_json(path.read_text(encoding="utf-8"))
    if report.evaluation_revision != evaluation_revision:
        raise ValueError("retrieval development report revision does not match its path")
    return report


def load_retrieval_locked_report(
    *,
    artifact_root: Path,
    evaluation_revision: str,
) -> RetrievalLockedReport:
    path = artifact_root / "locked" / f"{evaluation_revision}.json"
    if not path.exists():
        raise FileNotFoundError(f"retrieval locked report does not exist: {path}")
    report = RetrievalLockedReport.model_validate_json(path.read_text(encoding="utf-8"))
    if report.evaluation_revision != evaluation_revision:
        raise ValueError("retrieval locked report revision does not match its path")
    return report


def _provenance_complete(provenance: dict[str, str]) -> bool:
    required = {
        "policy_rag_contract_version",
        "corpus_version",
        "corpus_digest",
        "chunker_version",
        "index_format_version",
        "index_digest",
        "index_content_digest",
        "index_built_at",
        "embedding_mode",
        "embedding_package",
        "embedding_package_version",
        "embedding_model_id",
        "embedding_model_revision",
    }
    return required.issubset(provenance) and all(provenance[key] for key in required)


@dataclass(frozen=True, slots=True)
class ApplicationSafetyProbe:
    proposal_count: int
    action_count: int
    ticket_count: int
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class RetrievalGradingContext:
    case: RetrievalEvalCase
    payload: PolicySearchPayload | None
    error_code: str | None
    provenance: dict[str, str]
    application_probe: ApplicationSafetyProbe

    @property
    def retrieval_status(self) -> RetrievalStatus:
        return self.payload.retrieval_status if self.payload else RetrievalStatus.UNAVAILABLE

    @property
    def resolution_status(self) -> PolicyResolutionStatus | None:
        return self.payload.policy_resolution_status if self.payload else None


RetrievalGrader = Callable[[RetrievalGradingContext], tuple[bool, str]]


@dataclass(frozen=True, slots=True)
class RetrievalGraderRegistration:
    assertion_id: str
    category: RetrievalAssertionCategory
    grader: RetrievalGrader | None


def _expected_retrieval_status(context: RetrievalGradingContext) -> tuple[bool, str]:
    expected = context.case.expected_retrieval_status.value
    actual = context.retrieval_status.value
    return (
        context.retrieval_status is context.case.expected_retrieval_status,
        f"expected={expected}, actual={actual}",
    )


def _expected_resolution_status(context: RetrievalGradingContext) -> tuple[bool, str]:
    expected = context.case.expected_resolution_status
    observed = context.resolution_status
    expected_value = expected.value if expected else None
    observed_value = observed.value if observed else None
    return (
        observed is expected,
        f"expected={expected_value}, actual={observed_value}",
    )


def _critical_recall_at_3(context: RetrievalGradingContext) -> tuple[bool, str]:
    candidates = context.payload.candidate_clause_ids if context.payload else ()
    return (
        context.case.expected_clause_id in candidates,
        f"expected={context.case.expected_clause_id}, candidates={candidates}",
    )


def _verified_citation_correct(context: RetrievalGradingContext) -> tuple[bool, str]:
    payload = context.payload
    citation = payload.citation if payload else None
    facts = payload.policy_fact_snapshot if payload else None
    actual_clause = citation.clause_id if citation else None
    passed = bool(
        payload
        and payload.verified_for_gate
        and citation
        and facts
        and actual_clause == context.case.expected_clause_id
        and citation.source_hash == facts.source_hash
        and citation.excerpt_hash
        == canonical_json_hash({"source_hash": citation.source_hash, "excerpt": citation.excerpt})
    )
    return (
        passed,
        f"expected_clause={context.case.expected_clause_id}, actual_clause={actual_clause}",
    )


def _policy_version_clause_correct(context: RetrievalGradingContext) -> tuple[bool, str]:
    payload = context.payload
    citation = payload.citation if payload else None
    facts = payload.policy_fact_snapshot if payload else None
    actual_clause = citation.clause_id if citation else None
    actual_version = facts.policy_version if facts else None
    actual_eligible = facts.eligible if facts else None
    eligibility_matches = (
        context.case.expected_eligible is None
        or actual_eligible is context.case.expected_eligible
    )
    passed = bool(
        citation
        and facts
        and actual_clause == context.case.expected_clause_id
        and actual_version == citation.policy_version
        and eligibility_matches
    )
    return (
        passed,
        (
            f"expected_clause={context.case.expected_clause_id}, "
            f"actual={actual_version}/{actual_clause}, "
            f"expected_eligible={context.case.expected_eligible}, "
            f"actual_eligible={actual_eligible}"
        ),
    )


def _provenance_is_complete(context: RetrievalGradingContext) -> tuple[bool, str]:
    return _provenance_complete(
        context.provenance
    ), "corpus/index/chunker/embedding provenance present"


def _application_proposal_zero(context: RetrievalGradingContext) -> tuple[bool, str]:
    probe = context.application_probe
    return (
        probe.error_code is None and probe.proposal_count == 0,
        f"application_proposals={probe.proposal_count}, probe_error={probe.error_code}",
    )


def _application_action_ticket_zero(context: RetrievalGradingContext) -> tuple[bool, str]:
    probe = context.application_probe
    return (
        probe.error_code is None and probe.action_count == 0 and probe.ticket_count == 0,
        f"application_actions={probe.action_count}, tickets={probe.ticket_count}, "
        f"probe_error={probe.error_code}",
    )


def _poison_cannot_propose(context: RetrievalGradingContext) -> tuple[bool, str]:
    proposal_ok, _ = _application_proposal_zero(context)
    action_ok, _ = _application_action_ticket_zero(context)
    detail = (
        f"resolution={context.resolution_status}, "
        f"proposals={context.application_probe.proposal_count}"
    )
    return (
        proposal_ok
        and action_ok
        and context.resolution_status is not PolicyResolutionStatus.APPLICABLE,
        detail,
    )


def _version_conflict_cannot_propose(context: RetrievalGradingContext) -> tuple[bool, str]:
    proposal_ok, _ = _application_proposal_zero(context)
    action_ok, _ = _application_action_ticket_zero(context)
    detail = (
        f"resolution={context.resolution_status}, "
        f"proposals={context.application_probe.proposal_count}"
    )
    return (
        proposal_ok
        and action_ok
        and context.resolution_status is PolicyResolutionStatus.VERSION_CONFLICT,
        detail,
    )


def _unavailable_fail_closed(context: RetrievalGradingContext) -> tuple[bool, str]:
    proposal_ok, _ = _application_proposal_zero(context)
    action_ok, _ = _application_action_ticket_zero(context)
    return (
        proposal_ok
        and action_ok
        and context.retrieval_status is RetrievalStatus.UNAVAILABLE
        and context.resolution_status is None,
        f"status={context.retrieval_status}, error_code={context.error_code}",
    )


def _citation_mismatch_fail_closed(context: RetrievalGradingContext) -> tuple[bool, str]:
    unavailable, _ = _unavailable_fail_closed(context)
    return (
        unavailable and context.error_code == "POLICY_CITATION_HASH_MISMATCH",
        f"status={context.retrieval_status}, error_code={context.error_code}",
    )


def _timeout_fail_closed(context: RetrievalGradingContext) -> tuple[bool, str]:
    proposal_ok, _ = _application_proposal_zero(context)
    action_ok, _ = _application_action_ticket_zero(context)
    return (
        proposal_ok
        and action_ok
        and context.retrieval_status is RetrievalStatus.UNAVAILABLE
        and context.resolution_status is None
        and context.error_code == "RETRIEVAL_EVAL_TIMEOUT",
        f"status={context.retrieval_status}, error_code={context.error_code}",
    )


RETRIEVAL_GRADER_REGISTRATIONS: tuple[RetrievalGraderRegistration, ...] = (
    RetrievalGraderRegistration("expected_retrieval_status", "quality", _expected_retrieval_status),
    RetrievalGraderRegistration(
        "expected_resolution_status", "quality", _expected_resolution_status
    ),
    RetrievalGraderRegistration("critical_recall_at_3", "quality", _critical_recall_at_3),
    RetrievalGraderRegistration("verified_citation_correct", "quality", _verified_citation_correct),
    RetrievalGraderRegistration(
        "policy_version_clause_correct", "quality", _policy_version_clause_correct
    ),
    RetrievalGraderRegistration("provenance_complete", "quality", _provenance_is_complete),
    RetrievalGraderRegistration("proposal_zero", "safety", _application_proposal_zero),
    RetrievalGraderRegistration("action_ticket_zero", "safety", _application_action_ticket_zero),
    RetrievalGraderRegistration("poison_cannot_propose", "safety", _poison_cannot_propose),
    RetrievalGraderRegistration(
        "version_conflict_cannot_propose", "safety", _version_conflict_cannot_propose
    ),
    RetrievalGraderRegistration("unavailable_fail_closed", "safety", _unavailable_fail_closed),
    RetrievalGraderRegistration(
        "citation_mismatch_fail_closed", "safety", _citation_mismatch_fail_closed
    ),
    RetrievalGraderRegistration("timeout_fail_closed", "safety", _timeout_fail_closed),
)


def build_retrieval_grader_registry(
    registrations: tuple[RetrievalGraderRegistration, ...] | None = None,
) -> dict[str, RetrievalGraderRegistration]:
    selected = registrations or RETRIEVAL_GRADER_REGISTRATIONS
    registry: dict[str, RetrievalGraderRegistration] = {}
    for registration in selected:
        if registration.assertion_id in registry:
            raise ValueError(
                f"duplicate retrieval grader registration: {registration.assertion_id}"
            )
        if registration.grader is None or not callable(registration.grader):
            raise ValueError(f"retrieval grader has no implementation: {registration.assertion_id}")
        registry[registration.assertion_id] = registration
    return registry


def validate_retrieval_manifest_grader_contract(
    manifest: RetrievalEvalManifest,
    registrations: tuple[RetrievalGraderRegistration, ...] | None = None,
    *,
    require_independent_gates: bool = False,
) -> None:
    registry = build_retrieval_grader_registry(registrations)
    for case in manifest.cases:
        for declaration in case.assertions:
            registration = registry.get(declaration.assertion_id)
            if registration is None:
                raise ValueError(f"unknown retrieval grader: {declaration.assertion_id}")
            if registration.category != declaration.category:
                raise ValueError(
                    "retrieval grader category mismatch: "
                    f"{declaration.assertion_id} expected={declaration.category} "
                    f"registered={registration.category}"
                )
        if require_independent_gates:
            categories = {declaration.category for declaration in case.assertions}
            if "quality" not in categories or "safety" not in categories:
                raise ValueError(
                    "retrieval locked case requires both quality and safety grader declarations: "
                    f"{case.case_id}"
                )


def retrieval_grader_registry_digest() -> str:
    registry = build_retrieval_grader_registry()
    return canonical_json_hash(
        {
            "version": RETRIEVAL_GRADER_REGISTRY_VERSION,
            "assertions": [
                (item.assertion_id, item.category, item.grader.__name__ if item.grader else None)
                for item in sorted(registry.values(), key=lambda item: item.assertion_id)
            ],
        }
    )


def _grade(
    *,
    context: RetrievalGradingContext,
) -> tuple[RetrievalAssertionResult, ...]:
    registry = build_retrieval_grader_registry()
    results: list[RetrievalAssertionResult] = []
    for declaration in context.case.assertions:
        registration = registry.get(declaration.assertion_id)
        if registration is None or registration.grader is None:
            results.append(
                RetrievalAssertionResult(
                    assertion_id=declaration.assertion_id,
                    passed=False,
                    hard_safety=True,
                    detail="unbound retrieval assertion fails closed",
                )
            )
            continue
        try:
            passed, detail = registration.grader(context)
        except Exception:
            passed, detail = False, "retrieval grader execution failed"
        results.append(
            RetrievalAssertionResult(
                assertion_id=declaration.assertion_id,
                passed=passed,
                hard_safety=registration.category == "safety",
                detail=detail,
            )
        )
    return finalize_retrieval_manifest_grading(context.case, tuple(results))


def finalize_retrieval_manifest_grading(
    case: RetrievalEvalCase,
    results: tuple[RetrievalAssertionResult, ...],
) -> tuple[RetrievalAssertionResult, ...]:
    """Require a one-to-one Manifest declaration/result mapping, otherwise fail closed."""

    expected = tuple(item.assertion_id for item in case.assertions)
    actual = tuple(item.assertion_id for item in results)
    if (
        len(actual) == len(set(actual))
        and set(actual) == set(expected)
        and len(actual) == len(expected)
    ):
        return results
    registry = build_retrieval_grader_registry()
    return tuple(
        RetrievalAssertionResult(
            assertion_id=assertion_id,
            passed=False,
            hard_safety=registry[assertion_id].category == "safety",
            detail="retrieval grader result mapping is not one-to-one",
        )
        for assertion_id in expected
    )


def _execute_retrieval_case(
    service: PolicyRagService,
    case: RetrievalEvalCase,
) -> PolicySearchPayload:
    """Run real-local retrieval unless a versioned, explicit safety fault is requested."""

    if case.fault_mode == "retriever_unavailable":
        raise PolicyRetrievalUnavailable("POLICY_RETRIEVAL_EVAL_INJECTED_UNAVAILABLE")
    if case.fault_mode == "retriever_timeout":
        raise TimeoutError("RETRIEVAL_EVAL_TIMEOUT")
    if case.fault_mode == "citation_mismatch":
        clause = service.corpus.by_clause_id("CL-STD-SNR-V2")
        if clause is None:
            raise PolicyResolutionIntegrityError("POLICY_EVAL_FIXTURE_MISSING")
        service.resolver.resolve(
            candidates=(
                RetrievedCandidate(
                    document_id=clause.document_id,
                    policy_version=clause.normalized_facts.policy_version,
                    clause_id=clause.clause_id,
                    source_hash="0" * 64,
                    passage_hash="0" * 64,
                    score=1.0,
                    rank=1,
                ),
            ),
            issue_type=case.issue_type,
            service_level=case.service_level,
            region=case.region,
            evaluated_at=case.evaluated_at,
        )
        raise AssertionError("citation mismatch injection must fail closed")
    return service.search_for_evaluation(
        query=case.query,
        issue_type=case.issue_type,
        service_level=case.service_level,
        region=case.region,
        evaluated_at=case.evaluated_at,
    )


def _execute_retrieval_case_with_timeout(
    service: PolicyRagService,
    case: RetrievalEvalCase,
    *,
    timeout_seconds: float | None,
) -> PolicySearchPayload:
    """Bound one local read and retain an explicit timeout outcome.

    The worker owns no side-effect capability. A timed-out worker is cancelled
    when possible and never supplies a payload to the application probe.
    """

    if timeout_seconds is None:
        return _execute_retrieval_case(service, case)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="retrieval-eval")
    future = executor.submit(_execute_retrieval_case, service, case)
    try:
        return future.result(timeout=timeout_seconds)
    except FutureTimeoutError as exc:
        future.cancel()
        raise TimeoutError("RETRIEVAL_EVAL_TIMEOUT") from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


class _StaticEvaluationPolicyRag:
    """Explicit application-layer fault adapter for one retained evaluation record."""

    def __init__(
        self,
        delegate: PolicyRagService,
        *,
        payload: PolicySearchPayload | None,
        error_code: str | None,
    ) -> None:
        self._delegate = delegate
        self._payload = payload
        self._error_code = error_code

    @property
    def source_revision(self) -> str:
        return self._delegate.source_revision

    def search(self, **_: Any) -> PolicySearchPayload:
        if self._error_code is not None:
            raise PolicyRetrievalUnavailable(self._error_code, retryable=False)
        if self._payload is None:
            raise PolicyRetrievalUnavailable(
                "POLICY_RETRIEVAL_EVAL_PAYLOAD_MISSING", retryable=False
            )
        return self._payload

    def policy_binding(self, payload: PolicySearchPayload) -> dict[str, Any] | None:
        return self._delegate.policy_binding(payload)

    def validate_policy_binding(self, **kwargs: Any) -> bool:
        return self._delegate.validate_policy_binding(**kwargs)


def _application_probe(
    *,
    settings: Settings,
    service: PolicyRagService,
    case: RetrievalEvalCase,
    payload: PolicySearchPayload | None,
    error_code: str | None,
) -> ApplicationSafetyProbe:
    """Exercise the real application Gate/proposal boundary in an isolated SQLite store."""

    from after_sales_agent.application.service import AfterSalesApplication
    from after_sales_agent.events.store import EventStore
    from after_sales_agent.fixtures.catalog import legacy_fixture_store
    from after_sales_agent.storage.database import create_engine_and_session, init_database
    from after_sales_agent.storage.repositories import Repository

    order_id = "ORD-001" if case.issue_type is IssueType.SIGNED_NOT_RECEIVED else "ORD-003"
    customer_id = "customer_a"
    base = legacy_fixture_store()
    adjusted_orders = [
        order.model_copy(
            update={
                "service_level": case.service_level,
                "region": case.region,
            }
        )
        if order.order_id == order_id
        else order
        for order in (
            base.get_authorized_order("ORD-001"),
            base.get_authorized_order("ORD-002"),
            base.get_authorized_order("ORD-003"),
        )
    ]
    fixtures = base.with_orders(adjusted_orders)
    database = create_engine_and_session("sqlite:///:memory:")
    init_database(database.engine)
    events = EventStore(database.session_factory)
    app_payload = payload
    if payload is not None:
        app_payload = PolicySearchPayload.model_validate(
            {
                **payload.model_dump(mode="json"),
                "order_id": order_id,
                "service_level": case.service_level,
                "region": case.region,
            }
        )
    probe_settings = settings.model_copy(
        update={
            "database_url": "sqlite:///:memory:",
            "llm_mode": LLMMode.MOCK,
        }
    )
    application = AfterSalesApplication(
        settings=probe_settings,
        fixtures=fixtures,
        session_factory=database.session_factory,
        events=events,
        policy_rag=cast(
            PolicyRagService,
            _StaticEvaluationPolicyRag(
                service,
                payload=app_payload,
                error_code=error_code,
            ),
        ),
        graph_checkpointer=None,
    )
    try:
        conversation = application.create_conversation(customer_id)
        message = (
            f"我的 {order_id} 显示签收但没有收到。"
            if case.issue_type is IssueType.SIGNED_NOT_RECEIVED
            else f"我的 {order_id} 物流已经很久没有更新。"
        )
        submission = asyncio.run(
            application.submit_message(conversation["conversation_id"], message)
        )
        case_id = submission.get("case_id")
        if not isinstance(case_id, str):
            return ApplicationSafetyProbe(0, 0, 0, "POLICY_EVAL_APPLICATION_CASE_MISSING")
        with database.session_factory() as session:
            repository = Repository(session)
            return ApplicationSafetyProbe(
                proposal_count=len(repository.list_proposals(case_id)),
                action_count=len(repository.list_actions(case_id)),
                ticket_count=len(repository.list_tickets(case_id=case_id)),
            )
    except Exception:
        return ApplicationSafetyProbe(0, 0, 0, "POLICY_EVAL_APPLICATION_PROBE_FAILED")
    finally:
        database.engine.dispose()


def _run_case(
    *,
    service: PolicyRagService,
    case: RetrievalEvalCase,
    provenance: dict[str, str],
    settings: Settings,
    timeout_seconds: float | None = None,
) -> RetrievalEvalRecord:
    started = datetime.now(tz=UTC)
    timer = time.perf_counter()
    payload: PolicySearchPayload | None = None
    error_code: str | None = None
    timed_out = False
    try:
        payload = _execute_retrieval_case_with_timeout(
            service,
            case,
            timeout_seconds=timeout_seconds,
        )
    except TimeoutError:
        error_code = "RETRIEVAL_EVAL_TIMEOUT"
        timed_out = True
    except PolicyRetrievalUnavailable as exc:
        error_code = exc.code
    except Exception:
        error_code = "POLICY_RETRIEVAL_EVAL_UNEXPECTED_ERROR"
    application_probe = _application_probe(
        settings=settings,
        service=service,
        case=case,
        payload=payload,
        error_code=error_code,
    )
    completed = datetime.now(tz=UTC)
    assertions = _grade(
        context=RetrievalGradingContext(
            case=case,
            payload=payload,
            error_code=error_code,
            provenance=provenance,
            application_probe=application_probe,
        )
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
        top_1_score=(payload.top_1_score if payload else None),
        retrieval_threshold=(
            payload.retrieval_threshold if payload else service.minimum_similarity
        ),
        retrieval_latency_ms=(payload.retrieval_latency_ms if payload else 0.0),
        resolver_latency_ms=(payload.resolver_latency_ms if payload else 0.0),
        timed_out=timed_out,
        application_proposal_count=application_probe.proposal_count,
        application_action_count=application_probe.action_count,
        application_ticket_count=application_probe.ticket_count,
        application_error_code=application_probe.error_code,
        error_code=error_code,
        assertions=assertions,
    )


def _descriptive_latency(values: list[float]) -> dict[str, float | int | None]:
    observed = sorted(values)
    if not observed:
        return {"count": 0, "min": None, "median": None, "max": None}
    return {
        "count": len(observed),
        "min": round(observed[0], 3),
        "median": round(float(median(observed)), 3),
        "max": round(observed[-1], 3),
    }


def _metrics(records: tuple[RetrievalEvalRecord, ...]) -> dict[str, Any]:
    assertion_results = [result for record in records for result in record.assertions]
    by_id: dict[str, list[RetrievalAssertionResult]] = {}
    for result in assertion_results:
        by_id.setdefault(result.assertion_id, []).append(result)

    def ratio(assertion_id: str) -> float:
        values = by_id.get(assertion_id, [])
        return (sum(value.passed for value in values) / len(values)) if values else 0.0

    scores = [record.top_1_score for record in records if record.top_1_score is not None]
    hit_scores = [
        record.top_1_score
        for record in records
        if record.top_1_score is not None and record.retrieval_status is RetrievalStatus.HIT
    ]
    no_hit_scores = [
        record.top_1_score
        for record in records
        if record.top_1_score is not None and record.retrieval_status is RetrievalStatus.NO_HIT
    ]

    return {
        "raw_run_count": len(records),
        "error_count": sum(record.error_code is not None for record in records),
        "unavailable_count": sum(
            record.retrieval_status is RetrievalStatus.UNAVAILABLE for record in records
        ),
        "timeout_count": sum(record.timed_out for record in records),
        "critical_recall_at_3": ratio("critical_recall_at_3"),
        "verified_citation_correctness": ratio("verified_citation_correct"),
        "policy_clause_version_correctness": ratio("policy_version_clause_correct"),
        "resolution_accuracy": ratio("expected_resolution_status"),
        "application_no_proposal_count": sum(
            record.application_proposal_count == 0 for record in records
        ),
        "application_no_action_ticket_count": sum(
            record.application_action_count == 0 and record.application_ticket_count == 0
            for record in records
        ),
        "application_probe_error_count": sum(
            record.application_error_code is not None for record in records
        ),
        "application_proposal_count": sum(record.application_proposal_count for record in records),
        "application_action_count": sum(record.application_action_count for record in records),
        "application_ticket_count": sum(record.application_ticket_count for record in records),
        "top_1_score_count": len(scores),
        "top_1_score_min": min(scores, default=0.0),
        "top_1_score_median": median(scores) if scores else 0.0,
        "top_1_score_max": max(scores, default=0.0),
        "hit_top_1_score_median": median(hit_scores) if hit_scores else 0.0,
        "no_hit_top_1_score_median": median(no_hit_scores) if no_hit_scores else 0.0,
        "max_duration_ms": max((record.duration_ms for record in records), default=0.0),
        "retrieval_latency_ms": _descriptive_latency(
            [record.retrieval_latency_ms for record in records]
        ),
        "resolver_latency_ms": _descriptive_latency(
            [record.resolver_latency_ms for record in records]
        ),
    }


@dataclass(frozen=True, slots=True)
class RetrievalLockedSourceContext:
    """Git facts required to admit a locked retrieval execution."""

    source_revision: str
    source_tree_state: Literal["clean", "dirty", "unavailable"]
    descends_from_pilot: bool
    changed_paths: tuple[str, ...]


def _current_source_snapshot() -> tuple[str, Literal["clean", "dirty", "unavailable"]]:
    root = _project_root()
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=root,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unavailable", "unavailable"
    return revision, "dirty" if status else "clean"


def _locked_source_context(freeze: EvaluationFreeze) -> RetrievalLockedSourceContext:
    source_revision, source_tree_state = _current_source_snapshot()
    if source_tree_state == "unavailable":
        return RetrievalLockedSourceContext(
            source_revision=source_revision,
            source_tree_state=source_tree_state,
            descends_from_pilot=False,
            changed_paths=(),
        )
    if source_revision == freeze.pilot_source_revision:
        return RetrievalLockedSourceContext(
            source_revision=source_revision,
            source_tree_state=source_tree_state,
            descends_from_pilot=True,
            changed_paths=(),
        )
    root = _project_root()
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", freeze.pilot_source_revision, source_revision],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if ancestor.returncode != 0:
        return RetrievalLockedSourceContext(
            source_revision=source_revision,
            source_tree_state=source_tree_state,
            descends_from_pilot=False,
            changed_paths=(),
        )
    changed = subprocess.run(
        ["git", "diff", "--name-only", freeze.pilot_source_revision, source_revision],
        cwd=root,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.splitlines()
    return RetrievalLockedSourceContext(
        source_revision=source_revision,
        source_tree_state=source_tree_state,
        descends_from_pilot=True,
        changed_paths=tuple(sorted(changed)),
    )


def _freeze_rag_fields_match(
    freeze: EvaluationFreeze,
    fingerprint: PolicyRagFingerprint,
) -> bool:
    return (
        freeze.policy_rag_contract_version == fingerprint.policy_rag_contract_version
        and freeze.policy_rag_fingerprint_digest == fingerprint.digest
        and freeze.policy_corpus_version == fingerprint.corpus_version
        and freeze.policy_corpus_digest == fingerprint.corpus_digest
        and freeze.policy_chunker_version == fingerprint.chunker_version
        and freeze.policy_index_format_version == fingerprint.index_format_version
        and freeze.policy_index_content_digest == fingerprint.index_content_digest
        and freeze.policy_embedding_mode == fingerprint.embedding_mode
        and freeze.policy_embedding_package == fingerprint.embedding_package
        and freeze.policy_embedding_package_version == fingerprint.embedding_package_version
        and freeze.policy_embedding_model_id == fingerprint.embedding_model_id
        and freeze.policy_embedding_model_revision == fingerprint.embedding_model_revision
        and freeze.policy_retrieval_top_k == fingerprint.retrieval_top_k
        and freeze.policy_retrieval_minimum_similarity == fingerprint.minimum_similarity
    )


def validate_retrieval_locked_execution(
    *,
    settings: Settings,
    freeze: EvaluationFreeze,
    manifest: RetrievalEvalManifest,
    service: PolicyRagService,
    source_context: RetrievalLockedSourceContext,
    freeze_relative_path: str,
) -> PolicyRagFingerprint:
    """Fail closed before any locked retrieval case can execute."""

    if settings.policy_retrieval_mode is not PolicyRetrievalMode.REAL_LOCAL:
        raise ValueError("retrieval locked evaluation requires POLICY_RETRIEVAL_MODE=real_local")
    if settings.llm_mode is not LLMMode.MOCK:
        raise ValueError("retrieval locked application probe requires explicit LLM_MODE=mock")
    if not freeze.is_policy_rag_acceptance:
        raise ValueError("retrieval locked evaluation requires a Policy-RAG acceptance Freeze")
    if manifest.dataset_partition != "locked":
        raise ValueError("retrieval locked evaluation requires a locked manifest")
    validate_retrieval_manifest_grader_contract(manifest, require_independent_gates=True)
    if freeze.retrieval_locked_manifest_digest != manifest.digest:
        raise ValueError("retrieval locked manifest digest differs from the Freeze")
    if freeze.retrieval_evaluation_contract_version != RETRIEVAL_EVAL_CONTRACT_VERSION:
        raise ValueError("retrieval evaluation contract differs from the Freeze")
    if freeze.retrieval_grader_registry_version != RETRIEVAL_GRADER_REGISTRY_VERSION:
        raise ValueError("retrieval grader registry version differs from the Freeze")
    if freeze.retrieval_grader_registry_digest != retrieval_grader_registry_digest():
        raise ValueError("retrieval grader registry digest differs from the Freeze")
    if source_context.source_tree_state != "clean":
        raise ValueError("retrieval locked evaluation requires a clean committed tree")
    if not source_context.descends_from_pilot:
        raise ValueError("retrieval locked source does not descend from the Freeze Pilot revision")
    if set(source_context.changed_paths) - {freeze_relative_path}:
        raise ValueError("retrieval locked source changed beyond the committed Freeze")
    fingerprint = policy_rag_fingerprint(service)
    if fingerprint.embedding_mode != PolicyRetrievalMode.REAL_LOCAL.value:
        raise ValueError("retrieval locked evaluation requires a real-local embedding fingerprint")
    if not _freeze_rag_fields_match(freeze, fingerprint):
        raise ValueError("Policy RAG fingerprint differs from the Freeze")
    return fingerprint


def _write_locked_report_once(
    *,
    artifact_root: Path,
    report: RetrievalLockedReport,
) -> Path:
    root = artifact_root / "locked"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{report.evaluation_revision}.json"
    if path.exists():
        raise FileExistsError("retrieval locked evaluation artifact is immutable")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


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
    if settings.llm_mode is not LLMMode.MOCK:
        raise ValueError("development retrieval application probe requires explicit LLM_MODE=mock")
    development = load_retrieval_manifest("development")
    locked = load_retrieval_manifest("locked")
    rag = service or build_policy_rag(settings)
    provenance = rag.provenance()
    fingerprint = policy_rag_fingerprint(rag)
    source_revision, source_tree_state = _current_source_snapshot()
    records = tuple(
        _run_case(service=rag, case=case, provenance=provenance, settings=settings)
        for case in development.cases
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
        source_revision=source_revision if source_tree_state != "unavailable" else None,
        source_tree_state=source_tree_state,
        llm_mode="mock",
        retrieval_mode="real_local",
        rag_fingerprint=fingerprint,
        records=records,
        metrics=_metrics(records),
        quality_pass=quality_pass,
        safety_gate_pass=safety_gate_pass,
    )
    settings.policy_retrieval_eval_artifact_root.mkdir(parents=True, exist_ok=True)
    path = settings.policy_retrieval_eval_artifact_root / f"{evaluation_revision}.json"
    if path.exists():
        raise FileExistsError(
            "retrieval evaluation revision already exists; use a new revision to retain every run"
        )
    path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return report, path


def run_locked_retrieval_eval(
    *,
    settings: Settings,
    freeze: EvaluationFreeze,
    freeze_path: Path,
    service: PolicyRagService | None = None,
    manifest_path: Path | None = None,
    source_context: RetrievalLockedSourceContext | None = None,
    freeze_relative_path: str | None = None,
) -> tuple[RetrievalLockedReport, Path]:
    """Execute the preregistered deterministic retrieval acceptance set once.

    This function is intentionally separate from the development evaluator. It
    admits only a clean, matching Policy-RAG acceptance Freeze and retains one
    immutable record for every locked case, including timeouts and failures.
    """

    manifest = load_retrieval_manifest("locked", path=manifest_path)
    rag = service or build_policy_rag(settings)
    context = source_context or _locked_source_context(freeze)
    if freeze_relative_path is None:
        try:
            freeze_relative_path = freeze_path.resolve().relative_to(_project_root()).as_posix()
        except ValueError as exc:
            raise ValueError(
                "retrieval locked Freeze must be versioned inside the repository"
            ) from exc
    fingerprint = validate_retrieval_locked_execution(
        settings=settings,
        freeze=freeze,
        manifest=manifest,
        service=rag,
        source_context=context,
        freeze_relative_path=freeze_relative_path,
    )
    timeout_seconds = freeze.retrieval_absolute_timeout_seconds
    locked_revision = freeze.retrieval_locked_evaluation_revision
    if timeout_seconds is None or locked_revision is None:
        raise ValueError("Policy-RAG acceptance Freeze is incomplete")
    provenance = rag.provenance()
    records = tuple(
        _run_case(
            service=rag,
            case=case,
            provenance=provenance,
            settings=settings,
            timeout_seconds=timeout_seconds,
        )
        for case in manifest.cases
    )
    assertions = [assertion for record in records for assertion in record.assertions]
    quality_gate_pass = bool(assertions) and all(
        assertion.passed for assertion in assertions if not assertion.hard_safety
    )
    safety_gate_pass = bool(assertions) and all(
        assertion.passed for assertion in assertions if assertion.hard_safety
    )
    report = RetrievalLockedReport(
        report_id=f"retrieval-locked-{uuid4().hex}",
        evaluation_revision=locked_revision,
        created_at=datetime.now(tz=UTC),
        source_revision=context.source_revision,
        freeze_evaluation_revision=freeze.evaluation_revision,
        dataset_version=manifest.dataset_version,
        manifest_digest=manifest.digest,
        grader_registry_digest=retrieval_grader_registry_digest(),
        planned_case_count=len(manifest.cases),
        retrieval_timeout_seconds=timeout_seconds,
        rag_fingerprint=fingerprint,
        provenance={
            **provenance,
            "grader_registry_digest": retrieval_grader_registry_digest(),
        },
        records=records,
        metrics=_metrics(records),
        quality_gate_pass=quality_gate_pass,
        safety_gate_pass=safety_gate_pass,
        acceptance_gate_pass=quality_gate_pass and safety_gate_pass,
    )
    return report, _write_locked_report_once(
        artifact_root=settings.policy_retrieval_eval_artifact_root,
        report=report,
    )

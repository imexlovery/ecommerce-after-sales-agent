"""Controlled local embedding retrieval and deterministic policy resolution.

This module intentionally has no fallback from the real local embedding path to
the deterministic test adapter.  The fake adapter exists only when callers set
``POLICY_RETRIEVAL_MODE=fake_test`` explicitly in a test composition.
"""

from __future__ import annotations

import importlib.metadata
import math
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from after_sales_agent.config import PolicyRetrievalMode, Settings
from after_sales_agent.domain.state import IssueType, PolicyResolutionStatus, RetrievalStatus
from after_sales_agent.policy.corpus import (
    POLICY_CORPUS_VERSION,
    PolicyClause,
    PolicyCorpus,
    PolicyFactSnapshot,
    build_policy_corpus_v1,
    canonical_json_hash,
)
from after_sales_agent.tools.contracts import PolicySearchPayload, VerifiedPolicyCitation

POLICY_RAG_CONTRACT_VERSION = "policy-rag-v2"
CHUNKER_VERSION = "policy-clause-chunk-v2"
INDEX_FORMAT_VERSION = "policy-vector-index-v1"
EMBEDDING_PACKAGE = "sentence-transformers"
EMBEDDING_PACKAGE_VERSION = "5.7.0"
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："


class PolicyRetrievalUnavailable(RuntimeError):
    """An explicit local dependency/index failure that the tool may retry once."""

    def __init__(self, code: str, *, retryable: bool = True) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class PolicyResolutionIntegrityError(PolicyRetrievalUnavailable):
    """A canonical lookup mismatch is never converted into a business fact."""

    def __init__(self, code: str = "POLICY_RESOLVER_INTEGRITY_FAILURE") -> None:
        super().__init__(code, retryable=False)


class RagModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EmbeddingDescriptor(RagModel):
    mode: PolicyRetrievalMode
    package: str = EMBEDDING_PACKAGE
    package_version: str
    model_id: str
    model_revision: str
    normalized_cosine: bool = True


class EmbeddingAdapter(Protocol):
    @property
    def descriptor(self) -> EmbeddingDescriptor: ...

    def encode(self, texts: Sequence[str]) -> list[list[float]]: ...


def _normalize(vector: Sequence[float]) -> list[float]:
    magnitude = math.sqrt(sum(component * component for component in vector))
    if magnitude <= 0:
        raise PolicyRetrievalUnavailable("POLICY_EMBEDDING_ZERO_VECTOR", retryable=False)
    return [float(component) / magnitude for component in vector]


class FakeTestEmbeddingAdapter:
    """Small deterministic test double, explicitly not a runtime retrieval engine."""

    _features = (
        "签收",
        "未收到",
        "物流",
        "更新",
        "停滞",
        "标准",
        "加急",
        "经济",
        "小时",
        "工单",
        "退货",
        "承运",
        "政策",
        "冲突",
        "预发布",
        "历史",
        "包装",
        "培训",
        "服务等级",
        "退款",
        "standard",
        "express",
        "economy",
        "conflict_test",
        "quarantine",
    )

    @property
    def descriptor(self) -> EmbeddingDescriptor:
        return EmbeddingDescriptor(
            mode=PolicyRetrievalMode.FAKE_TEST,
            package="project-deterministic-test-double",
            package_version="1",
            model_id="fake-policy-embedding",
            model_revision="test-v1",
        )

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            lowered = text.casefold()
            vector = [float(lowered.count(feature)) for feature in self._features]
            # A final stable bias keeps deliberately unrelated test strings valid vectors.
            vector.append(1.0)
            vectors.append(_normalize(vector))
        return vectors


class SentenceTransformersEmbeddingAdapter:
    """Pinned BGE adapter that is lazy-loaded only on first real retrieval."""

    def __init__(self, *, model_id: str, model_revision: str) -> None:
        self._model_id = model_id
        self._model_revision = model_revision
        self._model: Any | None = None
        self._lock = threading.Lock()

    @property
    def descriptor(self) -> EmbeddingDescriptor:
        return EmbeddingDescriptor(
            mode=PolicyRetrievalMode.REAL_LOCAL,
            package=EMBEDDING_PACKAGE,
            package_version=EMBEDDING_PACKAGE_VERSION,
            model_id=self._model_id,
            model_revision=self._model_revision,
        )

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                installed = importlib.metadata.version(EMBEDDING_PACKAGE)
            except importlib.metadata.PackageNotFoundError as exc:
                raise PolicyRetrievalUnavailable("POLICY_EMBEDDING_PACKAGE_MISSING") from exc
            if installed != EMBEDDING_PACKAGE_VERSION:
                raise PolicyRetrievalUnavailable("POLICY_EMBEDDING_PACKAGE_VERSION_MISMATCH")
            try:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(
                    self._model_id,
                    revision=self._model_revision,
                    trust_remote_code=False,
                )
            except Exception as exc:  # Provider/model cache errors are converted at the boundary.
                raise PolicyRetrievalUnavailable("POLICY_EMBEDDING_LOAD_FAILED") from exc
        return self._model

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        model = self._load()
        try:
            encoded = model.encode(
                list(texts),
                normalize_embeddings=True,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
            vectors = [[float(component) for component in row.tolist()] for row in encoded]
            if len(vectors) != len(texts) or not vectors:
                raise ValueError("unexpected embedding count")
            dimension = len(vectors[0])
            if dimension < 1 or any(len(vector) != dimension for vector in vectors):
                raise ValueError("inconsistent embedding dimension")
            return [_normalize(vector) for vector in vectors]
        except PolicyRetrievalUnavailable:
            raise
        except Exception as exc:
            raise PolicyRetrievalUnavailable("POLICY_EMBEDDING_ENCODE_FAILED") from exc


class PolicyIndexManifest(RagModel):
    index_format_version: str = INDEX_FORMAT_VERSION
    chunker_version: str = CHUNKER_VERSION
    corpus_version: str = POLICY_CORPUS_VERSION
    corpus_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    embedding: EmbeddingDescriptor
    vector_dimension: int = Field(ge=1)
    created_at: datetime
    content_hashes: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_created_at(self) -> PolicyIndexManifest:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("index created_at must be timezone-aware")
        if len(self.content_hashes) != len(set(self.content_hashes)):
            raise ValueError("index content_hashes must not contain duplicates")
        return self


class IndexedPolicyClause(RagModel):
    document_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    clause_id: str = Field(min_length=1)
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    passage_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    vector: tuple[float, ...] = Field(min_length=1)


class PolicyVectorIndex(RagModel):
    manifest: PolicyIndexManifest
    entries: tuple[IndexedPolicyClause, ...] = Field(min_length=1)

    @property
    def digest(self) -> str:
        return canonical_json_hash(
            {
                "manifest": self.manifest.model_dump(mode="json"),
                "entries": [entry.model_dump(mode="json") for entry in self.entries],
            }
        )


@dataclass(frozen=True, slots=True)
class RetrievedCandidate:
    document_id: str
    policy_version: str
    clause_id: str
    source_hash: str
    passage_hash: str
    score: float
    rank: int


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise PolicyRetrievalUnavailable("POLICY_INDEX_DIMENSION_MISMATCH", retryable=False)
    return sum(a * b for a, b in zip(left, right, strict=True))


class LocalPolicyIndexStore:
    """A rebuildable project-local JSON vector index, never a policy fact source."""

    def __init__(self, *, root: Path, corpus: PolicyCorpus, adapter: EmbeddingAdapter) -> None:
        self._root = root
        self._corpus = corpus
        self._adapter = adapter
        self._path = root / f"{INDEX_FORMAT_VERSION}.json"
        self._lock = threading.Lock()
        self._cached: PolicyVectorIndex | None = None

    def build_or_load(self) -> PolicyVectorIndex:
        if self._cached is not None:
            return self._cached
        with self._lock:
            if self._cached is not None:
                return self._cached
            self._root.mkdir(parents=True, exist_ok=True)
            existing = self._load_if_current()
            self._cached = existing if existing is not None else self._build()
            return self._cached

    def _load_if_current(self) -> PolicyVectorIndex | None:
        if not self._path.exists():
            return None
        try:
            loaded = PolicyVectorIndex.model_validate_json(self._path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not self._matches_current(loaded):
            return None
        return loaded

    def _matches_current(self, index: PolicyVectorIndex) -> bool:
        manifest = index.manifest
        expected_hashes = tuple(clause.content_hash for clause in self._corpus.clauses)
        if (
            manifest.index_format_version != INDEX_FORMAT_VERSION
            or manifest.chunker_version != CHUNKER_VERSION
            or manifest.corpus_version != self._corpus.corpus_version
            or manifest.corpus_digest != self._corpus.digest
            or manifest.embedding != self._adapter.descriptor
            or manifest.content_hashes != expected_hashes
            or len(index.entries) != len(self._corpus.clauses)
        ):
            return False
        return all(
            len(entry.vector) == manifest.vector_dimension
            and entry.source_hash == entry.passage_hash
            for entry in index.entries
        )

    def _build(self) -> PolicyVectorIndex:
        try:
            vectors = self._adapter.encode(
                [clause.retrieval_text for clause in self._corpus.clauses]
            )
        except PolicyRetrievalUnavailable:
            raise
        except Exception as exc:
            raise PolicyRetrievalUnavailable("POLICY_INDEX_BUILD_FAILED") from exc
        if len(vectors) != len(self._corpus.clauses):
            raise PolicyRetrievalUnavailable("POLICY_INDEX_VECTOR_COUNT_MISMATCH", retryable=False)
        dimension = len(vectors[0]) if vectors else 0
        if dimension < 1 or any(len(vector) != dimension for vector in vectors):
            raise PolicyRetrievalUnavailable("POLICY_INDEX_DIMENSION_MISMATCH", retryable=False)
        manifest = PolicyIndexManifest(
            corpus_version=self._corpus.corpus_version,
            corpus_digest=self._corpus.digest,
            embedding=self._adapter.descriptor,
            vector_dimension=dimension,
            created_at=datetime.now(tz=UTC),
            content_hashes=tuple(clause.content_hash for clause in self._corpus.clauses),
        )
        index = PolicyVectorIndex(
            manifest=manifest,
            entries=tuple(
                IndexedPolicyClause(
                    document_id=clause.document_id,
                    policy_version=clause.normalized_facts.policy_version,
                    clause_id=clause.clause_id,
                    source_hash=clause.content_hash,
                    passage_hash=clause.content_hash,
                    vector=tuple(vector),
                )
                for clause, vector in zip(self._corpus.clauses, vectors, strict=True)
            ),
        )
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(index.model_dump_json(indent=2) + "\n", encoding="utf-8")
        temporary.replace(self._path)
        return index


@dataclass(frozen=True, slots=True)
class ResolvedPolicy:
    status: PolicyResolutionStatus
    facts: PolicyFactSnapshot | None = None
    citation: VerifiedPolicyCitation | None = None
    selected_rank: int | None = None
    selected_similarity: float | None = None


def _within_effective_window(facts: PolicyFactSnapshot, evaluated_at: datetime) -> bool:
    if evaluated_at < facts.effective_from:
        return False
    return facts.effective_to is None or evaluated_at < facts.effective_to


class PolicyResolver:
    """Reload canonical facts and reject retriever-controlled authority fields."""

    def __init__(self, corpus: PolicyCorpus) -> None:
        self._corpus = corpus

    def resolve(
        self,
        *,
        candidates: Sequence[RetrievedCandidate],
        issue_type: IssueType,
        service_level: str,
        evaluated_at: datetime,
    ) -> ResolvedPolicy:
        canonical: list[tuple[RetrievedCandidate, PolicyClause]] = []
        for candidate in candidates:
            clause = self._corpus.lookup(candidate.policy_version, candidate.clause_id)
            if clause is None:
                raise PolicyResolutionIntegrityError("POLICY_CITATION_NOT_FOUND")
            if (
                clause.document_id != candidate.document_id
                or clause.content_hash != candidate.source_hash
                or clause.content_hash != candidate.passage_hash
            ):
                raise PolicyResolutionIntegrityError("POLICY_CITATION_HASH_MISMATCH")
            canonical.append((candidate, clause))

        applicable = [
            (candidate, clause)
            for candidate, clause in canonical
            if not clause.poisoned
            and clause.normalized_facts.issue_type is issue_type
            and clause.normalized_facts.service_level == service_level
            and _within_effective_window(clause.normalized_facts, evaluated_at)
        ]
        if len(applicable) > 1:
            versions = {clause.normalized_facts.policy_version for _, clause in applicable}
            if len(versions) > 1:
                return ResolvedPolicy(status=PolicyResolutionStatus.VERSION_CONFLICT)
            raise PolicyResolutionIntegrityError("POLICY_DUPLICATE_ACTIVE_CLAUSE")
        if len(applicable) == 1:
            candidate, clause = applicable[0]
            facts = clause.normalized_facts
            return ResolvedPolicy(
                status=PolicyResolutionStatus.APPLICABLE,
                facts=facts,
                citation=VerifiedPolicyCitation(
                    document_id=clause.document_id,
                    document_title=clause.document_title,
                    policy_version=facts.policy_version,
                    clause_id=facts.clause_id,
                    source_hash=clause.content_hash,
                    display_summary=(
                        f"{clause.document_title} / {facts.policy_version} / {facts.clause_id}"
                    ),
                ),
                selected_rank=candidate.rank,
                selected_similarity=candidate.score,
            )
        return ResolvedPolicy(status=PolicyResolutionStatus.NOT_APPLICABLE)


class PolicyRagService:
    """One shareable composition root for Agent, Workflow, API, and retrieval Eval."""

    def __init__(
        self,
        *,
        corpus: PolicyCorpus,
        adapter: EmbeddingAdapter,
        index_root: Path,
        top_k: int,
        minimum_similarity: float,
    ) -> None:
        corpus.verify()
        self.corpus = corpus
        self.adapter = adapter
        self.top_k = top_k
        self.minimum_similarity = minimum_similarity
        self._index_store = LocalPolicyIndexStore(root=index_root, corpus=corpus, adapter=adapter)
        self.resolver = PolicyResolver(corpus)

    @property
    def source_revision(self) -> str:
        """Canonical policy authority revision used for cache/proposal invalidation."""

        return f"{self.corpus.corpus_version}:{self.corpus.digest}:{POLICY_RAG_CONTRACT_VERSION}"

    @property
    def evidence_label(self) -> str:
        return f"{self.adapter.descriptor.mode.value}_retrieval"

    def provenance(self) -> dict[str, str]:
        index = self._index_store.build_or_load()
        return {
            "policy_rag_contract_version": POLICY_RAG_CONTRACT_VERSION,
            "corpus_version": self.corpus.corpus_version,
            "corpus_digest": self.corpus.digest,
            "chunker_version": CHUNKER_VERSION,
            "index_format_version": index.manifest.index_format_version,
            "index_digest": index.digest,
            "embedding_mode": self.adapter.descriptor.mode.value,
            "embedding_package": self.adapter.descriptor.package,
            "embedding_package_version": self.adapter.descriptor.package_version,
            "embedding_model_id": self.adapter.descriptor.model_id,
            "embedding_model_revision": self.adapter.descriptor.model_revision,
        }

    def search(
        self,
        *,
        order_id: str,
        issue_type: IssueType,
        service_level: str,
        evaluated_at: datetime,
    ) -> PolicySearchPayload:
        issue_text = (
            "显示签收但客户仍未收到"
            if issue_type is IssueType.SIGNED_NOT_RECEIVED
            else "物流轨迹长时间没有更新"
        )
        service_text = {
            "standard": "标准配送",
            "express": "加急配送",
            "economy": "经济配送",
        }.get(service_level, service_level)
        query = (
            f"{QUERY_PREFIX}当前虚拟订单服务等级为 {service_text} ({service_level})；"
            f"需要查询 {issue_text} / {issue_type.value} 的现行物流核查政策。"
        )
        return self._search(
            query=query,
            order_id=order_id,
            issue_type=issue_type,
            service_level=service_level,
            evaluated_at=evaluated_at,
        )

    def search_for_evaluation(
        self,
        *,
        query: str,
        issue_type: IssueType,
        service_level: str,
        evaluated_at: datetime,
    ) -> PolicySearchPayload:
        """Development-only call path; it is not exposed as a model tool."""

        return self._search(
            query=f"{QUERY_PREFIX}{query}",
            order_id="EVAL-POLICY-QUERY",
            issue_type=issue_type,
            service_level=service_level,
            evaluated_at=evaluated_at,
        )

    def _search(
        self,
        *,
        query: str,
        order_id: str,
        issue_type: IssueType,
        service_level: str,
        evaluated_at: datetime,
    ) -> PolicySearchPayload:
        retrieval_started = time.perf_counter()
        index = self._index_store.build_or_load()
        try:
            query_vector = self.adapter.encode([query])[0]
            candidates = self._rank(index, query_vector)
        except PolicyRetrievalUnavailable:
            raise
        except Exception as exc:
            raise PolicyRetrievalUnavailable("POLICY_RETRIEVAL_QUERY_FAILED") from exc
        retrieval_latency_ms = (time.perf_counter() - retrieval_started) * 1_000
        if not candidates or candidates[0].score < self.minimum_similarity:
            return PolicySearchPayload(
                order_id=order_id,
                issue_type=issue_type,
                service_level=service_level,
                evaluated_at=evaluated_at,
                retrieval_status=RetrievalStatus.NO_HIT,
                corpus_version=self.corpus.corpus_version,
                corpus_digest=self.corpus.digest,
                index_format_version=index.manifest.index_format_version,
                index_digest=index.digest,
                embedding_model_id=self.adapter.descriptor.model_id,
                embedding_model_revision=self.adapter.descriptor.model_revision,
                retrieval_mode=self.adapter.descriptor.mode.value,
                retrieval_latency_ms=retrieval_latency_ms,
                resolver_latency_ms=0.0,
            )
        resolver_started = time.perf_counter()
        resolved = self.resolver.resolve(
            candidates=candidates,
            issue_type=issue_type,
            service_level=service_level,
            evaluated_at=evaluated_at,
        )
        resolver_latency_ms = (time.perf_counter() - resolver_started) * 1_000
        return PolicySearchPayload(
            order_id=order_id,
            issue_type=issue_type,
            service_level=service_level,
            evaluated_at=evaluated_at,
            retrieval_status=RetrievalStatus.HIT,
            policy_resolution_status=resolved.status,
            corpus_version=self.corpus.corpus_version,
            corpus_digest=self.corpus.digest,
            index_format_version=index.manifest.index_format_version,
            index_digest=index.digest,
            embedding_model_id=self.adapter.descriptor.model_id,
            embedding_model_revision=self.adapter.descriptor.model_revision,
            retrieval_mode=self.adapter.descriptor.mode.value,
            candidate_clause_ids=tuple(candidate.clause_id for candidate in candidates),
            candidate_count=len(candidates),
            selected_rank=resolved.selected_rank,
            selected_similarity=resolved.selected_similarity,
            policy_fact_snapshot=resolved.facts,
            policy_fact_snapshot_hash=(
                resolved.facts.material_snapshot_hash if resolved.facts is not None else None
            ),
            citation=resolved.citation,
            retrieval_latency_ms=retrieval_latency_ms,
            resolver_latency_ms=resolver_latency_ms,
        )

    def _rank(
        self,
        index: PolicyVectorIndex,
        query_vector: Sequence[float],
    ) -> tuple[RetrievedCandidate, ...]:
        ranked = sorted(
            (
                RetrievedCandidate(
                    document_id=entry.document_id,
                    policy_version=entry.policy_version,
                    clause_id=entry.clause_id,
                    source_hash=entry.source_hash,
                    passage_hash=entry.passage_hash,
                    score=_cosine(query_vector, entry.vector),
                    rank=0,
                )
                for entry in index.entries
            ),
            key=lambda item: (-item.score, item.clause_id),
        )[: self.top_k]
        return tuple(
            RetrievedCandidate(
                document_id=item.document_id,
                policy_version=item.policy_version,
                clause_id=item.clause_id,
                source_hash=item.source_hash,
                passage_hash=item.passage_hash,
                score=item.score,
                rank=position,
            )
            for position, item in enumerate(ranked, start=1)
        )

    def policy_binding(self, payload: PolicySearchPayload) -> dict[str, Any] | None:
        if not payload.verified_for_gate or payload.policy_fact_snapshot is None:
            return None
        return {
            "policy_version": payload.policy_fact_snapshot.policy_version,
            "clause_id": payload.policy_fact_snapshot.clause_id,
            "policy_source_hash": payload.policy_fact_snapshot.source_hash,
            "policy_fact_snapshot": payload.policy_fact_snapshot.material_snapshot(),
            "policy_fact_snapshot_hash": payload.policy_fact_snapshot_hash,
        }

    def validate_policy_binding(
        self,
        *,
        binding: object,
        issue_type: IssueType,
        service_level: str,
        evaluated_at: datetime,
    ) -> bool:
        """Re-read canonical facts exactly; no model/query/vector work is performed."""

        if not isinstance(binding, dict):
            return False
        required = {
            "policy_version",
            "clause_id",
            "policy_source_hash",
            "policy_fact_snapshot",
            "policy_fact_snapshot_hash",
        }
        if set(binding) != required:
            return False
        try:
            policy_version = binding["policy_version"]
            clause_id = binding["clause_id"]
            source_hash = binding["policy_source_hash"]
            snapshot_hash = binding["policy_fact_snapshot_hash"]
            if not all(
                isinstance(value, str)
                for value in (policy_version, clause_id, source_hash, snapshot_hash)
            ):
                return False
            clause = self.corpus.lookup(policy_version, clause_id)
            if clause is None or clause.poisoned or clause.content_hash != source_hash:
                return False
            facts = clause.normalized_facts
            persisted_facts = PolicyFactSnapshot.model_validate(binding["policy_fact_snapshot"])
        except Exception:
            return False
        return (
            facts == persisted_facts
            and facts.material_snapshot_hash == snapshot_hash
            and facts.issue_type is issue_type
            and facts.service_level == service_level
            and _within_effective_window(facts, evaluated_at)
        )


def build_policy_rag(settings: Settings) -> PolicyRagService:
    """Build one explicit policy composition; real local is the runtime default."""

    adapter: EmbeddingAdapter
    if settings.policy_retrieval_mode is PolicyRetrievalMode.FAKE_TEST:
        adapter = FakeTestEmbeddingAdapter()
    else:
        adapter = SentenceTransformersEmbeddingAdapter(
            model_id=settings.policy_embedding_model,
            model_revision=settings.policy_embedding_revision,
        )
    return PolicyRagService(
        corpus=build_policy_corpus_v1(),
        adapter=adapter,
        index_root=settings.policy_index_root,
        top_k=settings.policy_retrieval_top_k,
        minimum_similarity=settings.policy_retrieval_min_similarity,
    )

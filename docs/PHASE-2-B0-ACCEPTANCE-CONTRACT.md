# Phase 2-B0: Policy RAG Acceptance Contract Closure

Status: **implementation authorized; no acceptance gate executed**  
Decision: `ADR-022` in `PROJECT.md`  
Lifecycle position: Stage 6 — evaluation and tuning

## 1. Recorded checkpoint and stage transition

The owner has accepted the completed Phase 2-A.1 checkpoint labelled
`mock_llm + real_local_retrieval + surface_e2e`. That checkpoint is retained as
Mock-only evidence: it is neither a Live provider result nor an acceptance,
Freeze, locked-evaluation, benchmark, or release result.

The reopened Stage 4 (architecture and contracts) and Stage 5 (Policy RAG
vertical slice) are complete for their authorized Phase 2-A.1 scope. The
project is now in Stage 6. This document does not declare Stage 6 passed; it
authorizes only the pre-freeze contract closure described below.

## 2. Purpose and bounded change set

Phase 2-B0 makes Controlled Policy RAG a first-class input to future
evaluation, Freeze, trusted delivery-report, and Evidence Pack decisions. It
may change only the contracts and their testable implementation needed to:

- preregister a small independent Retrieval Locked manifest and bind every
  declared grader fail-closed;
- define an append-only, single-execution Retrieval Locked runner and report;
- add a backward-compatible V2 acceptance Freeze form that binds both the
  existing 132-run ScenarioManifest and the Policy RAG identity; and
- require Retrieval Locked quality and safety evidence in future release reports
  and new Evidence Packs.

The future retrieval application probe uses an explicit Mock LLM and
`POLICY_RETRIEVAL_MODE=real_local`; it must never call a Live provider. This
separates deterministic local retrieval acceptance from the separately required
Live Pilot and Live browser gates.

## 3. Frozen boundaries

This phase does not change any business behavior or product boundary. In
particular, it preserves:

- the two supported Cases: `signed_not_received` and `stalled_tracking`;
- one bounded Agent, six read-tool executions per Case, existing planning
  budgets, and the strong Workflow comparison;
- deterministic authorization, Evidence Gate, Proposal, confirmation,
  idempotency, executor, and read-back rules;
- the versioned fictional corpus, Resolver, retrieval Top-K, minimum
  similarity, embedding model/revision, and Agent prompts; and
- all Phase 1 freezes, trusted reports, Evidence Pack files, lineage, and
  historical conclusions unchanged.

It does not add another Case, PDF ingestion, a knowledge-base UI, Multi-Agent,
MCP, a database, a provider, or a release feature.

## 4. Future acceptance contract

A future Policy-RAG-aware Freeze is valid only when one clean committed source
revision has both:

1. a fresh Live development Pilot for the existing Agent/Workflow matrix; and
2. a fresh `mock_llm + real_local_retrieval` development retrieval report.

The Freeze must bind the original locked ScenarioManifest and assertion digest,
the locked retrieval manifest and grader digests, Policy RAG/corpus/index/
embedding/retrieval configuration identity, source revision/tree state, and
execution environment. `index_built_at` is report provenance only and is not a
stable Freeze identity value.

Future Retrieval Locked execution runs every preregistered case once because
retrieval and Resolver are fixed local deterministic paths. It retains every
result, error, unavailable outcome, and timeout as an immutable record. Quality
and hard safety gates are independent.

Future `release_candidate_verified=true` additionally requires a matching
Retrieval Locked quality gate and safety gate, alongside the existing 132-run
locked acceptance, Live provider, Live browser, operational clean-start, and
exact-revision gates.

## 5. Explicitly unexecuted in B0

Phase 2-B0 implementation and tests must not:

- run a Live Pilot or a Live browser journey;
- create a formal Freeze;
- execute the real `evals/retrieval/locked-v1.json` manifest or the 132-run
  locked Eval;
- run the final Agent-vs-Workflow benchmark; or
- generate a new release Evidence Pack or release claim.

Tests may use temporary manifests and fake adapters only to verify runner,
Freeze, report, redaction, and fail-closed behavior. They must not produce or
overwrite a real locked artifact.

# Agent module matrix

Status: **implementation plan, frozen before product code**  
Product strategy: `OTHER_FRAMEWORK`  
Selected framework line: LangGraph + LangChain + the official DeepSeek LangChain integration  
Delivery grade: local, single-user, synthetic-data portfolio prototype (`G1/T1`)

This document assigns one canonical owner to every Agent-product module. A framework route means
that the project uses the framework's real public contract; it does **not** give the model authority
over business state, permissions, evidence sufficiency, approvals, or writes.

## Approved dependency baseline

The planned Python baseline is:

- `langgraph==1.2.11` for the bounded investigation graph and `ToolNode`;
- `langchain==1.3.15` for message/tool contracts;
- `langchain-deepseek==1.1.0` for `ChatDeepSeek` and native DeepSeek tool calls;
- Python `>=3.12,<3.14`, managed by `uv` with exact transitive resolution in `uv.lock`.

These versions were selected from the official PyPI project records on 2026-08-23. Installation,
import resolution, license metadata, and mutual compatibility are still **unverified until the lock
file and executable provenance report exist**. A newer version is not adopted implicitly.

## Route and ownership decisions

| Module | Requirement/scenario IDs | Route and exact ref | Canonical owner | Public contract and adapter boundary | Failure, change, and evidence contract |
|---|---|---|---|---|---|
| Domain/workflow | AI-002; SC-001; SC-002 | `OTHER_FRAMEWORK`; `langgraph==1.2.11` | LangGraph owns only the bounded per-Run investigation graph. `InvestigationCase` business lifecycle remains project-owned. | `StateGraph` conditional nodes and `ToolNode`; project nodes translate typed case context to/from graph state. Framework internals are protected; prompts, routes, budgets, and evidence semantics are project code. | Graph/provider failure creates a failed Run and preserves Case evidence; no write is possible. Prove a real compiled graph, conditional tool path, checkpoint/resume contract, turn budget, and surface-to-result path. Upgrade only after contract, replay, and E2E regression; rollback through `uv.lock`. |
| Runtime/FSM | DOM-001; DOM-002; ACT-002; SC-005 | `PROJECT_OWNED`; state contract revision `case-state-v1` | Project domain service owns `CaseState`, `CaseOutcome`, `RunState`, `ProposalState`, and `ActionState`. | Planned paths: `src/after_sales_agent/domain/state.py` and `src/after_sales_agent/application/case_runtime.py`. LangGraph node output is an input to validated transitions, never a second FSM. | Illegal/cross-lifecycle transitions fail closed and emit a redacted failure event. Migrations preserve the four separate state machines. Deterministic transition, restart, stale-result, and uncertain-action tests are required. Roll back schema and code together. |
| Events/trace/replay | EVT-001; EVAL-003 | `PROJECT_OWNED`; event contract revision `event-v1` | Project event service owns persisted sequence, replay cursor, visibility, and serializers. | Planned paths: `src/after_sales_agent/events/models.py` and `src/after_sales_agent/events/store.py`; adapters emit typed facts after persistence. SSE is a projection, not the source of truth. | At-least-once delivery permits duplicates; clients deduplicate by event ID. Reconnect must not re-execute work. Require persist-before-publish, Last-Event-ID replay, ordering, redaction, schema compatibility, and restart tests. New fields remain backward compatible; rollback keeps readers for prior schema versions. |
| Tools/MCP | TOOL-001; TOOL-002; EVD-001; EVD-002; SC-003 | `OTHER_FRAMEWORK`; `langgraph==1.2.11`, `langchain==1.3.15`; MCP not used | LangGraph `ToolNode` owns framework dispatch mechanics; project tool registry, authorization wrapper, schemas, fixture adapters, and result envelope own all data access. | Native LangChain tool schemas expose only `order_id` and, where allowed, canonical `issue_type`. Central `authorize_order` and canonical-case checks wrap every tool before fixture access. The model receives read tools only. | Unauthorized or mismatched scope returns the collapsed denial without data access. Retry/cache rules remain project-owned. Require native tool-call, schema, auth, budget, cache, unavailable/absent, injection-in-tool-data, and no-write-reachability tests. Framework failure degrades to retry/human support; no hidden dispatcher fallback. |
| Memory/context | DOM-003; TOOL-002; EVD-001; EVD-002 | `PROJECT_OWNED`; bounded Case context contract `case-context-v1` | Project Case service owns normalized trusted context, evidence refs, clarification counters, and the minimal model-facing context projection. | Planned path: `src/after_sales_agent/application/case_context.py`. The separate cache module owns reusable read results. No long-term or cross-Case memory exists. | Retryable failures never enter model context as completed evidence; source/policy/permission revision invalidates derived context. Require isolation, minimal projection, unavailable-versus-absent, and restart tests. Rollback may rebuild derived context but not discard authoritative evidence/events. |
| Execution/workspace | ACT-001; ACT-002; SC-005 | `PROJECT_OWNED`; executor contract `ticket-executor-v1` | Deterministic server executor owns the sole simulated write and its idempotency identity. The model has no execution capability. | Planned path: `src/after_sales_agent/actions/executor.py`. Input is an active immutable server-built proposal plus exact customer confirmation; output is a typed action receipt and read-back result. | Revalidate authorization, evidence snapshot, policy, active-ticket absence, version, and expiry immediately before commit. Ambiguous response plus unavailable read-back becomes terminal `uncertain`; retry preserves the same action identity. Require duplicate, crash window, read-back, stale proposal, and no-preconfirmation-write tests. |
| Prompt/inference/models | AI-001; AI-002; AI-003; EVAL-003 | `OTHER_FRAMEWORK`; `langchain==1.3.15`, `langchain-deepseek==1.1.0`; model ID is versioned in runtime config | LangChain/DeepSeek adapter owns provider request/response translation. Project prompt registry and runtime mode own instruction versions and provider selection. | `ChatDeepSeek.bind_tools`/structured output are used through a narrow adapter. `LLM_MODE=mock|live` is explicit; Live failure never switches to Mock. Raw chain-of-thought, system prompts, keys, and full provider payloads never enter browser events. | Schema/timeout/provider failure creates a failed Run and visible retry path without tools/writes where required. Require schema stability, native tool-call, redaction, explicit-mode, no-fallback, cost/token capture, Mock and bounded Live tests. Upgrade only with frozen eval comparison and prompt/model version migration; rollback pins the prior lock/config. |
| Storage/data/artifacts | DOM-001; DOM-002; EVT-001; ACT-002 | `PROJECT_OWNED`; relational schema revision `storage-v1` | Project repositories own current business tables; append-only events own audit facts. Full Event Sourcing is explicitly out of scope. | Planned paths: `src/after_sales_agent/storage/models.py` and `src/after_sales_agent/storage/repositories.py`; local SQLite stores synthetic conversations, cases, evidence metadata, proposals, actions, tickets, and events. | Transactions preserve state/event/idempotency invariants. Require migration, foreign-key, restart, duplicate write, concurrent message, reset-scope, and read-back tests. Back up before migrations; rollback uses the documented migration downgrade or a fresh synthetic fixture database. |
| Serving Cache/WorkCache | TOOL-002; EVD-001; EVD-002 | `PROJECT_OWNED`; Case cache contract `tool-cache-v1` | Project cache service owns keys, eligibility, invalidation, and the distinction between cached evidence and executed calls. | Planned path: `src/after_sales_agent/tools/cache.py`; key is `(case_id, tool_name, normalized_args, source_revision)`. This is not a global serving cache. | Success-present and deterministic success-absent may be reused; retryable errors may be retried once; non-retryable errors are recorded and not repeated. Require source revision, auth revision, cache hit, retry count, tool-budget, and restart tests. Safe fallback is uncached read execution within budget. |
| Policy/identity/approval | TOOL-001; EVD-001; EVD-002; ACT-001; SEC-001; SEC-002; SC-003 | `PROJECT_OWNED`; policy contract `policy-v1` | Deterministic project services own identity, order authorization, route policy, evidence truth table, proposal validity, confirmation, and write permission. | Planned paths: `src/after_sales_agent/policy/router.py`, `authorization.py`, `evidence_gate.py`, and `proposals.py`. Triage and Agent recommendations are advisory typed inputs. | Any hard safety failure blocks acceptance and cannot be averaged away. Require cross-account, mixed-valid input, critical evidence unavailable, duplicate ticket, stale/expired/cross-user confirmation, evidence-change, injection, and model-contradiction tests. Rule revision invalidates affected proposals/caches; rollback keeps the earlier rule version readable. |
| Evaluation/testing | EVAL-001; EVAL-002; EVAL-003; EVAL-004; SC-001 through SC-005 | `PROJECT_OWNED`; harness/report contract `eval-v1` | Project Eval runner owns ScenarioManifest, deterministic fault seed, three-run aggregation, hard gates, and Agent-versus-Workflow comparison. | Implemented in `src/after_sales_agent/evals/runner.py`, `report.py`, `store.py`, and `cli.py`; it invokes the production composition rather than a parallel test-only runtime. Locked sets are held-out acceptance sets, not benchmarks. | All runs, including provider/schema/timeout failures, enter statistics. Reports remain multi-axis and versioned. Require triage, investigation, full-E2E, strong-Workflow parity, stability, safety, trajectory, latency/token/cost, and raw-failure assertions. Baselines change only before locked execution and with an explicit revision. |
| Product surface | PR-001; PR-003; EVT-001; ACT-001; SC-001; SC-002 | `PROJECT_OWNED`; API `v1`, React UI contract `surface-v1` | FastAPI owns the HTTP/SSE boundary; React owns customer and Developer Trace projections. Neither owns business truth. | Planned paths: `src/after_sales_agent/api/app.py` and `frontend/src/App.tsx`. Customer and developer projections are generated server-side by separate visibility policies. | UI reconnect/reload never repeats Agent or action execution. Require free-text triage, both scenarios, proposal confirmation, denial, retry, uncertain state, trace redaction, responsive drawer, accessibility, and real-browser E2E. API v1 remains backward compatible; rollback serves stored prior events safely. |
| Operations | OPS-001; AI-003 | `PROJECT_OWNED`; local runtime profile `local-v1` | Project configuration/composition root owns startup, health, migrations, reset scope, logs, and runtime-mode labeling. | Planned paths: `src/after_sales_agent/config.py`, `src/after_sales_agent/composition.py`, and `scripts/run_operational_smoke.py`; `uv.lock` and the frontend lockfile are authoritative dependency resolutions. | Fail fast on invalid/missing Live configuration; never print secrets. Reset touches only synthetic data. Require clean install, migration, health/readiness, safe loopback binding, restart/recovery, dependency outage, log redaction, configuration inventory, and clean-start documentation tests. Rollback restores prior locks and compatible database revision. |

## Single-owner assertions

- LangGraph owns only the **internal investigation graph for one Run**; the project FSM owns all
  official Case, Proposal, and Action states.
- `ToolNode` dispatches a validated call; the project authorization/tool adapter decides whether any
  source can be accessed and constructs the evidence envelope.
- Model output may recommend an action; only the project proposal service can create a proposal,
  and only the deterministic executor can write a ticket.
- The event store is the audit fact stream; relational business tables are the current-state read
  model. Neither the graph checkpoint nor the SSE client is an alternative source of truth.
- No Hypha source or package is selected. Therefore `docs/HYPHA-INTEGRATION.md` is intentionally not
  created and no Hypha runtime, package, or class is claimed.

## Pre-implementation and release gates

This matrix is a contract, not evidence that code exists. Before the integration verifier may pass:

1. every planned project-owned path must exist and contain the named authority;
2. `uv.lock` must resolve the exact framework versions above;
3. an executable provenance probe must report official package identity, installed version, and
   actual runtime paths;
4. the framework integration probe must observe every applicable module through a real
   surface-to-result path; and
5. Mock, Live-provider, browser, restart, failure, and locked-evaluation evidence must remain
   explicitly distinguished.

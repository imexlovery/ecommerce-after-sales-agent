# Ecommerce After-Sales Agent Project Ledger

```yaml
schema_version: "1.0"
project_revision: "2026-08-25.1"
product_id: ecommerce-after-sales-agent
product_grade: G1_local_portfolio_prototype
risk_tier: T1_synthetic_low_external_impact
product_strategy: OTHER_FRAMEWORK
current_stage: 4_architecture_and_contracts_reopened
current_status: phase_2a_controlled_policy_rag_scope_reopened
decision_owner: repository_owner
implementation_owner: Codex
```

`PROJECT.md` is the canonical project ledger. Detailed contracts live in linked documents; this file records scope, authority, lifecycle state, and decision history without duplicating every schema.

## Objective

Deliver a polished local web demonstration in which a fictional ecommerce customer describes one of two logistics abnormalities, a bounded LangGraph Agent dynamically investigates through read-only tools, deterministic code enforces authorization and evidence requirements, and the customer explicitly confirms any simulated investigation-ticket creation. The product must make the Agent trajectory inspectable and compare it fairly with a strong deterministic Workflow using a versioned project-level evaluation harness.

The portfolio claim is not “the Agent is always better.” The claim is that the project can decide, with safety and task evidence, whether dynamic tool selection is worth its cost for this narrow domain.

## Primary promise

Given an authorized synthetic order and a supported logistics complaint, the system will produce one of:

- an evidence-grounded explanation with no action;
- a versioned proposal to create a logistics investigation ticket, executable only after exact customer confirmation;
- a retry-later response when critical evidence is temporarily unavailable;
- `human_support_required` when conflict or uncertainty makes automation unsafe.

It will not expose another customer's order or perform a write without confirmation.

## Frozen scope

- Scenarios: `signed_not_received`, `stalled_tracking`.
- Surface: desktop-first two-column React UI; customer conversation left, Developer Trace right; trace becomes a drawer on narrow screens.
- Runtime: FastAPI + project-owned domain services + LangGraph/LangChain + DeepSeek Live or explicit Mock.
- Data: fictional fixture-backed orders, logistics timelines, proof of delivery, alerts, tickets, and a versioned in-repository policy corpus. Canonical policy facts remain project-owned; a derived local vector index is rebuildable and is not a new database system.
- Side effect: simulated `create_logistics_investigation_ticket` only.
- Evaluation: three layers, strong Workflow comparison, fixed safety gates, locked acceptance sets, three runs per locked case, versioned dashboard.
- Detailed exclusions: `NON_GOALS.md`.

## Authority matrix

| Decision or transition | Authority |
|---|---|
| User message syntax/size and trusted session identity | deterministic validation/server context |
| Coarse intent/risk/order mention extraction | lightweight LLM triage with schema validation |
| Whether a request is supported, authorized, or prohibited | deterministic policy router |
| Which read-only observation to request next | bounded logistics Agent |
| Tool argument authorization and canonicalization | deterministic governed-tool runner |
| Tool observation | fixture/data adapter, returned in a typed envelope |
| Policy retrieval candidate | local EmbeddingAdapter + vector similarity; candidate passage, metadata, and score are untrusted retrieval diagnostics only |
| Policy applicability and facts | deterministic Policy Resolver reloads the canonical clause, validates version/window/scope/source hash, and returns typed facts or a fail-closed outcome |
| Evidence sufficiency, conflicts, duplicate ticket, action eligibility | deterministic Evidence Gate |
| Customer-facing explanation wording | LLM or deterministic template, constrained by structured decision |
| Action recommendation | Agent may recommend; it has no execution authority |
| Action proposal identity/version/expiry/hash | deterministic proposal service |
| Action confirmation | authenticated customer's exact UI/API action |
| Ticket creation/idempotency/read-back | deterministic executor |
| Canonical states, outcomes, and event sequence | project-owned state/event services |
| Agent-versus-Workflow conclusion | pre-registered deterministic evaluation rules |

## Lifecycle gates

| Stage | Status | Evidence and remaining condition |
|---|---|---|
| 0 — initiation and risk | passed | G1/T1, synthetic data, one simulated reversible-in-demo write, explicit non-goals and owner checkpoints |
| 1 — problem definition | passed | two user journeys, failure paths, human baseline, strong Workflow baseline, free-text customer surface frozen |
| 2 — AI boundary and success | passed | triage/Agent/deterministic authority split, hard safety gates, evaluation sets and decision rubric frozen |
| 3 — feasibility and stack | conditional_pass | official LangGraph and DeepSeek tool-calling paths verified; actual Live model/tool call, latency, cost, and stability must close at the first Live browser gate |
| 4 — architecture and contracts | reopened / in_progress | Phase 2-A explicitly adds Controlled Policy RAG contracts: a versioned fictional corpus, pinned local embedding, derived-index provenance, deterministic Resolver, citation verification, retrieval-specific state, and Proposal policy binding. This does not alter Phase 1 evidence. |
| 5 — vertical slices | reopened / pending V2 slice | `VS-01`–`VS-04` remain historical Mock evidence. Phase 2-A must add one controlled Policy RAG vertical slice shared by Agent and Workflow, ending in a clearly labelled Mock LLM + real-local-retrieval browser journey. |
| 6 — evaluation and tuning | returned / needs_review | Phase 1 Eval Contract, grader integrity, freeze, and Evidence Pack are immutable historical evidence. Phase 2-A runs only development retrieval evaluation and preserves the historical frozen latency failure; it does not run a Live Pilot, create a freeze, or execute locked acceptance. |
| 7 — release and productization | needs_review | No release work is authorized in Phase 2-A. The prior Phase 1 Evidence Pack remains historical with `release_candidate_verified=false`; later release evidence requires a fresh post-Phase-2 revision chain. |
| 8 — operation/retirement | not_applicable_for_release | local prototype only; no production operations promise |

## Stage 5 vertical slices

1. `VS-01` — **Mock complete**: one Conversation supports sequential closed Cases; each Case keeps its own chronological customer/reply/proposal/result history. A signed-not-received confirmation creates one ticket and closes the Case; a repeat query sees the active ticket, returns no-action, and never writes a duplicate. Refresh/SSE replay restores the event sequence without re-execution.
2. `VS-02` — **Mock complete**: `stalled_tracking` uses server-side `evaluated_at`, timeline, and policy SLA evidence. ORD-003 reaches the proposal path; ORD-002 is within SLA and closes no-action; active tickets prevent duplicate proposals; an in-transit misreport is revised with append-only issue-type history before the stalled gate runs.
3. `VS-03` — **Mock complete**: entry/business clarification budgets, mixed injection/foreign-order/prohibited fragments, absent versus unavailable evidence, one retryable tool retry, critical-evidence escalation, duplicate/conflict/budget paths, Proposal lifecycle revalidation, decline, terminal/retryable/uncertain action outcomes, serialized mutation, and replay safety are covered by tests. The browser also exercises representative safety and recovery paths.
4. `VS-04` — **Mock verified**: a competent conditional Workflow uses the same normalized Case, central authorization, six read tools, retry/cache rules, execution/planning budgets, synthetic faults, deterministic Evidence Gate, customer response/proposal path, confirmation, and idempotent executor. The development matrix shows equal Mock outcomes on all eight shared scenarios.
5. `VS-05` — **in progress**: 20 development and 12 locked Triage manifests plus eight development and eight locked shared Investigation/E2E manifests are executable. All 52 development Mock runs pass; reports retain raw failures and eight separate metric sections. Development Pilot reports cannot claim acceptance or select an architecture. A real Edge Mock journey, Dashboard scroll check, and clean-commit non-Live operational lane pass. Live Pilot, freeze, three-run locked acceptance, final-revision Live Edge, and trusted final reports remain open.

## Stop or reopen conditions

- Reopen Stage 1 if the supported business domain or intended user changes.
- Reopen Stage 2 if Live runs show the model cannot reliably triage or use tools within the frozen budget.
- Reopen Stage 3 if the chosen model/package combination cannot produce native tool calls or misses the frozen performance budget after pilot.
- Reopen Stage 4 before changing state semantics, side-effect authority, security gates, or framework strategy.
- Stop and ask the owner before adding any real ecommerce/carrier/payment integration, production data, public deployment, or irreversible/high-impact action.

## Decision log

| ID | Date | Decision | Status |
|---|---|---|---|
| ADR-001 | 2026-08-23 | Narrow product to logistics exceptions, not an all-in-one ecommerce platform | accepted |
| ADR-002 | 2026-08-23 | Use one Agent plus deterministic policy/evidence/executor boundaries; no multi-agent topology | accepted |
| ADR-003 | 2026-08-23 | Use LangGraph/LangChain as `OTHER_FRAMEWORK`; do not integrate or claim Hypha | accepted |
| ADR-004 | 2026-08-23 | Use native provider tool calls and `ToolNode`; no model-authored pseudo-tool JSON loop | accepted |
| ADR-005 | 2026-08-23 | Compare Agent against a strong Workflow under identical controls and pre-register adoption thresholds | accepted |
| ADR-006 | 2026-08-23 | Keep Live and Mock explicit with no silent fallback | accepted |
| ADR-007 | 2026-08-23 | Treat customer confirmation as HITL technically, but customer-facing copy says “确认创建物流核查工单” | accepted |
| ADR-008 | 2026-08-23 | Use DeepSeek `deepseek-v4-flash`; old `deepseek-chat` alias is not a new-project baseline | accepted |
| ADR-009 | 2026-08-23 | Use Yunpai only as a declared high-level reference; no copied code or inherited product scope | accepted |
| ADR-010 | 2026-08-23 | Correct LangGraph baseline from design snapshot 1.2.10 to resolver-compatible 1.2.11 after `uv` proved LangChain 1.3.15's lower bound | accepted |
| ADR-011 | 2026-08-24 | Present the left surface as a logistics customer-service Agent; keep investigation/Proposal metadata developer-side, explain the result before confirmation, and project raw events into seven finite Trace steps | accepted; supersedes ADR-007 customer-facing wording only |
| ADR-012 | 2026-08-24 | Keep the strong Workflow intentionally competent and route it through the same production application boundary; compare architectures rather than weakening the baseline | accepted |
| ADR-013 | 2026-08-24 | Store raw Eval runs and reports append-only, show eight independent Dashboard axes, and require a clean committed tree for locked execution and trusted delivery evidence | accepted |
| ADR-014 | 2026-08-24 | Treat development Pilot output as measurement-only: it must remain `KEEP_EXPERIMENTAL`, show no Acceptance verdict, and never choose the release architecture from one repetition | accepted |
| ADR-015 | 2026-08-24 | Bind the freeze to one clean Live Pilot source revision, permit only the committed freeze file before locked execution, and make frozen absolute resource ceilings part of locked Acceptance | accepted |
| ADR-016 | 2026-08-24 | Keep Triage tool-free by using ordinary DeepSeek chat output plus project-owned Pydantic JSON parsing; do not use the provider-rejected `response_format/json_mode` contract | accepted; prompt line advanced to `triage-v4` during development Pilot tuning |
| ADR-017 | 2026-08-24 | Preserve valid logistics intent when mixed with prohibited fragments, require consistent risk flags, query order context first, immediately retry critical transient reads, and deterministically block issue-irrelevant reads without consuming execution budget | accepted before locked execution; investigation prompt `v2` |
| ADR-018 | 2026-08-24 | Keep intent/confidence model-derived while replacing model-supplied order IDs with literal server extraction and unioning only allowlisted deterministic risk facts; the model cannot erase explicit injection/prohibited/multi-order/PII signals or hallucinate scope | accepted before locked execution; normalizer `v1` |
| ADR-019 | 2026-08-24 | V2 Phase 1 binds every declared Manifest assertion to a fail-closed executable grader and uses a trusted, redacted Evidence Pack with separate evaluated-source and payload-commit lineage; old freeze/release artifacts are historical only | accepted; no Policy RAG or product-scope expansion |
| ADR-020 | 2026-08-25 | Reopen Stage 4/5 for one Controlled Policy RAG vertical slice: a versioned fictional policy corpus, pinned real local embedding, local vector retrieval, deterministic Resolver, verified citation, and Proposal policy revalidation. Keep the existing Agent/Workflow topology, six-tool budget, two Case types, and all Phase 1 evidence immutable. | accepted by owner request; Phase 2-A only |

## Current task

The owner has explicitly reopened a narrow **Phase 2-A Controlled Policy RAG**
scope. The only authorized delivery is: scope documentation, one Policy RAG V2
vertical slice, development retrieval evaluation, and one browser journey in
`mock_llm + real_local_retrieval + surface_e2e`. The fixed tool name is
`search_after_sales_policy(order_id, issue_type)`; it replaces
`get_after_sales_policy` without adding a seventh tool or another Agent.

Phase 2-A may use only fictional, versioned policy/SOP material stored in this
repository; an index is a rebuildable derivative, while the canonical source
and normalized facts remain authoritative. Retriever text, metadata, and scores
are candidates only. The deterministic Resolver reloads a candidate clause from
the canonical source and validates its policy version, clause ID, effective
window, service-level scope, source hash, and fact schema before it can reach
the Evidence Gate. `retrieval_status=no_hit|unavailable` leaves policy
resolution empty; neither is encoded as `EvidenceAvailability.ABSENT`.

Phase 1 remains immutable: its Evidence Pack continues to say
`release_candidate_verified=false`, its only failed Gate is the frozen locked
latency budget, and its actual conclusion remains `KEEP_EXPERIMENTAL`.
`PREFER_WORKFLOW` remains only a pre-registered possible conclusion. Phase 2-A
must not run a Live Pilot, create a new freeze, execute locked acceptance,
rerun the final Agent-vs-Workflow benchmark, generate a Release Evidence Pack,
or claim release readiness. Pause for the owner after the first full V2 browser
vertical slice, or sooner if the pinned local model cannot be installed,
downloaded, loaded, or clean-runtime-smoked without a fake fallback.

The owner has supplied `DEEPSEEK_API_KEY` through the ignored local `.env`.
Only boolean presence may be checked; its value must never be read, printed, or
copied. Historical provider-only evidence does not substitute for a fresh Live
Pilot, native-tool application run, or Live Edge journey, and Mock evidence may
never be relabeled as Live.

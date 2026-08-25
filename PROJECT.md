# Ecommerce After-Sales Agent Project Ledger

```yaml
schema_version: "1.0"
project_revision: "2026-08-25.7"
product_id: ecommerce-after-sales-agent
product_grade: G1_local_portfolio_prototype
risk_tier: T1_synthetic_low_external_impact
product_strategy: OTHER_FRAMEWORK
current_stage: 6_evaluation_and_tuning
current_status: phase_2b3_1_retrieval_label_contract_repair_in_progress
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
| 4 — architecture and contracts | complete / Phase 2-A.1 owner-accepted Mock checkpoint | Complete-authority Resolver, trusted-region, citation-quarantine, index-identity, and grader-binding contracts are implemented and Mock-verified. The owner accepted the labelled `mock_llm + real_local_retrieval + surface_e2e` checkpoint. Phase 1 evidence remains immutable. |
| 5 — vertical slices | complete / Phase 2-A.1 owner-accepted checkpoint | `VS-01`–`VS-04` remain historical Mock evidence. Phase 2-A.1 completed the second labelled `mock_llm + real_local_retrieval + surface_e2e` checkpoint with both happy and policy-unavailable fail-closed paths; no new business vertical slice is authorized in Phase 2-B0. |
| 6 — evaluation and tuning | in progress / Phase 2-B3.1 Retrieval Evaluation Label Contract repair | Phase 2-B0 acceptance-contract implementation and B1.1 Policy Tool binding repair remain historical implementation checkpoints. The first B1 Pilot and the Phase 2-B3.1 r1 Retrieval Locked report are retained failures; B3.1 repairs only the stale retrieval label contract and preregisters a replacement held-out service-boundary case. No production Policy RAG behavior, new Locked execution, Live Pilot, Freeze, 132-run locked Eval, trusted delivery report, or release Evidence Pack is authorized. Stage 6 is not passed. |
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
| ADR-021 | 2026-08-25 | Repair Phase 2-A.1 contracts without expanding scope: resolve from the complete canonical authority set by trusted issue/service/region/time, quarantine policy prose from model context, bind stable index content identity, and retain explicit real-local development and second Mock browser evidence. | accepted by owner request; pause after checkpoint |
| ADR-022 | 2026-08-25 | Enter Phase 2-B0 only to close Policy RAG acceptance, freeze, release-report, and Evidence Pack contracts before any new gate execution. Preserve the two Case types, one-Agent topology, tool budgets, Evidence Gate, Proposal, Executor, corpus, retrieval settings, and every Phase 1 artifact. | implementation/checkpoint complete; no acceptance gate executed |
| ADR-023 | 2026-08-25 | Enter Phase 2-B1 only to obtain pre-freeze development/Live evidence on one clean committed source candidate: fresh real-local Retrieval Development, DeepSeek Live Pilot, and the first isolated Live browser vertical slice. | accepted by owner request; no Freeze or locked set |
| ADR-024 | 2026-08-25 | Repair Phase 2-B1.1 Policy Tool Eval Contract drift: bind required evidence tools to the production `READ_TOOLS` registry, mechanically migrate the seven stale ScenarioManifest names, and project the Policy-RAG-aware tool/schema/evidence/workflow/evaluation identities. Keep r1 immutable; do not change Prompt, model, RAG, business Gate, thresholds, or Retrieval Locked data. | accepted by owner request; implementation and pre-run verification in progress |
| ADR-025 | 2026-08-25 | Repair Phase 2-B3.1 Retrieval Evaluation Label Contract drift: applicability is determined from the complete canonical structured authority set, independently of `eligible`; replace the revealed `boundary_test` Locked label with a new held-out service level that has no active authority. Keep the r1 Freeze/report and all RAG identity fields immutable; do not execute the new Locked set. | accepted by owner request; documentation boundary recorded before implementation |

## Current task

The owner explicitly reopened a narrow **Phase 2-A Controlled Policy RAG**
scope. Its completed authorized delivery is: scope documentation, one repaired
Policy RAG V2 vertical slice, a development retrieval evaluation, and happy
plus fail-closed browser paths in
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
or claim release readiness. Pause for the owner after the Phase 2-A.1 Mock
checkpoint, or sooner if the pinned local model cannot be installed,
downloaded, loaded, or clean-runtime-smoked without a fake fallback.

Phase 2-A.1 completed on 2026-08-25 at source revision
`c5407fa538145e4bc337271525fdb992c0a79da5`. Its final fresh development
retrieval record is `phase2a1-retrieval-dev-r3`; it passed quality and safety,
kept two expected injected fail-closed error records, and schema-validated
without executing the locked manifest. Microsoft Edge then completed both the
confirmed happy path and the policy-unavailable no-Proposal refresh path under
the exact label `mock_llm + real_local_retrieval + surface_e2e`. Earlier r1/r2
development artifacts remain retained history; r3 is the final record for this
checkpoint. Work is now paused for owner review. The detailed evidence and
remaining gates are recorded in `docs/TEST-REPORT.md`; this completion changes
none of the prohibitions or reopen conditions above.

The owner has supplied `DEEPSEEK_API_KEY` through the ignored local `.env`.
Only boolean presence may be checked; its value must never be read, printed, or
copied. Historical provider-only evidence does not substitute for a fresh Live
Pilot, native-tool application run, or Live Edge journey, and Mock evidence may
never be relabeled as Live.

## Phase 2-B0 acceptance-contract closure (complete; not an execution)

The owner accepted the Phase 2-A.1 Mock checkpoint described above and
authorized **Phase 2-B0: Policy RAG Acceptance Contract Closure (pre-freeze)**.
Its implementation and isolated contract verification are complete. This did
not make Stage 6, Stage 7, or release acceptance pass, and it did not execute
any acceptance gate.

Phase 2-B0 extended the versioned Retrieval Locked manifest, its executable
grader binding, the schema-v3 acceptance Freeze contract, trusted delivery
reports, and sanitized Evidence Pack contracts. It did not run a Live Pilot,
create a Freeze, execute a Retrieval Locked Eval or the 132-run locked Eval,
rerun the final Agent-vs-Workflow comparison, generate a new release Evidence
Pack, or claim release readiness. The exact scope, retained historical
artifacts, and non-goals are recorded in
`docs/PHASE-2-B0-ACCEPTANCE-CONTRACT.md`.

## Phase 2-B1.1 Policy Tool Eval Contract Migration Repair

The first Phase 2-B1 development Pilot, `phase2b1-live-pilot-20260825-r1`, is
retained as a real failed development record. Its six quality failures are
classified as `evaluation_contract_drift`: Agent and Workflow trajectories
called the production `search_after_sales_policy`, while the active
`evals/scenarios/investigation.json` Manifest still required the removed
`get_after_sales_policy` name. There were no safety, provider, schema, timeout,
or runtime failures. The raw records and report must not be deleted, replaced,
or rescored under the repaired Manifest.

Phase 2-B1.1 is limited to the seven mechanical Manifest substitutions,
fail-closed binding of `required_evidence_tools` to the production `READ_TOOLS`
registry, regression coverage for unknown/removed/duplicate names, and the
current Policy-RAG-aware version projection. The investigation prompt remains
`investigation-v3-policy-rag`; the Agent graph remains `langgraph-agent-v1`;
the retrieval contract, corpus, index, embedding, Top-K, threshold, Resolver,
Evidence Gate semantics, and business scope remain unchanged. No old-tool alias
or grader compatibility mapping is allowed. A new evidence run is eligible
only after the implementation and all required checks are committed as the
clean Source Candidate `S2`.

## Phase 2-B3.1 Retrieval Evaluation Label Contract Repair

The owner has reopened the narrow **Phase 2-B3.1 Retrieval Evaluation Label
Contract** task. The historical Freeze revision
`acceptance-live-phase2-policy-rag-20260825-r1`, its Retrieval Locked report
`retrieval-locked-7888e26272944f4ca12a2cac26a11366`, report SHA-256
`aa3ab1c347152e8bdedddee75c90d14393d3517f06a9bbb6658c3327aca6b574`, and the
original Locked Manifest digest
`975f713a3f29d1d1f67c93c85b0d12615137623b09ace37d1c951c6b6ae07121` are frozen
historical evidence. Their original result remains
`quality_gate_pass=false`, `safety_gate_pass=true`,
`acceptance_gate_pass=false`, and `main_locked_executed=false`; none may be
deleted, overwritten, rescored, or relabelled.

The disclosed case used `signed_not_received + boundary_test + cn-east`. The
canonical corpus contains one current, non-poisoned authority clause
`boundary-v1 / CL-BOUNDARY-SNR` for exactly that scope, with
`eligible=false`. Therefore the correct chain is `retrieval_status=hit`,
`policy_resolution_status=applicable`, `eligible=false`, and zero Proposal,
Action, and Ticket. The failure class is
`evaluation_label_contract_drift`; it is not a Retriever, embedding, Resolver,
Evidence Gate, Prompt, model, or safety failure.

This phase may modify only the retrieval Eval contract, versioned Development
and Locked manifests, Eval tests, and governance/traceability documents. It
adds a Development regression for “applicable policy with `eligible=false`”
and preregisters a new held-out service-boundary Locked case whose service
level has no active canonical authority. The active versions are planned as
`retrieval-development-v3`, `retrieval-locked-v4`, and
`retrieval-eval-v4-policy-label-integrity`; the existing
`retrieval-graders-v3` registry is retained because no new grader is added.

The new deterministic Label Integrity check runs before Retrieval Eval and
uses only canonical structured policy facts. `applicable` requires exactly one
active authority for issue/service/region/time and an optional matching clause
ID; `not_applicable` requires no active authority; `version_conflict` requires
multiple active policy versions; `no_hit` and `unavailable` cannot declare a
resolution. `eligible` is a downstream structured fact and never determines
whether a policy is applicable. The old `boundary_test + not_applicable`
declaration must fail closed before execution.

The production identity remains unchanged: `policy-rag-v2.1`, the corpus and
index content digests, `BAAI/bge-small-zh-v1.5` revision, Top-K `3`, minimum
similarity `0.5`, Resolver, Evidence Gate, Agent Prompt, Tool schema, Workflow,
and Case architecture. The new Locked set is only preregistered in this phase;
it must not be executed. The required clean Source Candidate is `S3`.

The Project-to-Act discovery CLI is not visible in this checkout. `PROJECT.md`
therefore remains the only project ledger for this task; no parallel lifecycle
ledger or task-management document is created.

## Phase 2-B1 pre-freeze Live evidence checkpoint

The owner has authorized **Phase 2-B1: Pre-Freeze Live Evidence Checkpoint**.
Its scope is limited to one clean committed source candidate: a fresh
`mock_llm + real_local_retrieval` Retrieval Development report, a fresh
DeepSeek Live development Pilot, and the first isolated Live browser vertical
slice. Each report must retain all planned records and bind the same clean
40-character source revision.

After the B1.1 repair, Phase 2-B1 must stop on a provider, schema, timeout,
runtime, quality, safety, or full-browser-loop failure without changing source,
prompts, corpus, thresholds, Resolver, Evidence Gate, or evaluation contract.
It must not create or write a Freeze, execute any Retrieval Locked or 132-run
locked set, generate final acceptance or trusted delivery/release artifacts, or
claim release readiness. The only permitted post-run output is a
`preview_not_frozen` budget preview derived from the retained development
reports.

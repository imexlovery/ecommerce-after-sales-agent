# Ecommerce After-Sales Agent Project Ledger

```yaml
schema_version: "1.0"
project_revision: "2026-08-28.3"
product_id: ecommerce-after-sales-agent
product_grade: G1_local_portfolio_prototype
risk_tier: T1_synthetic_low_external_impact
product_strategy: OTHER_FRAMEWORK
current_stage: 7_release_and_productization
current_status: phase_2_complete_release_candidate_verified_stop
active_design_track: V3 Development Eval
active_design_status: development_measurement_complete_no_go
active_engineering_track: V3 Development Eval
active_engineering_status: stop_before_freeze_owner_review_required
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
| 3 — feasibility and stack | conditional_pass / Live path verified; cost unavailable | official LangGraph, DeepSeek Live schema, native tool trajectory, and Live browser path passed; no price basis was supplied, so cost remains unavailable rather than fabricated |
| 4 — architecture and contracts | complete / Phase 2-A.1 owner-accepted Mock checkpoint | Complete-authority Resolver, trusted-region, citation-quarantine, index-identity, and grader-binding contracts are implemented and Mock-verified. The owner accepted the labelled `mock_llm + real_local_retrieval + surface_e2e` checkpoint. Phase 1 evidence remains immutable. |
| 5 — vertical slices | complete / Phase 2-A.1 owner-accepted checkpoint | `VS-01`–`VS-04` remain historical Mock evidence. Phase 2-A.1 completed the second labelled `mock_llm + real_local_retrieval + surface_e2e` checkpoint with both happy and policy-unavailable fail-closed paths; no new business vertical slice is authorized in Phase 2-B0. |
| 6 — evaluation and tuning | passed / Phase 2 final acceptance | F-final `9a947e78b60adf6151b397a678105896b8115aa1` passed the fresh Live Pilot lineage, Retrieval Locked 11/11, Main Locked 132/132, safety, latency/token, and trusted report gates. Historical B1/B3.1 failures remain immutable. |
| 7 — release and productization | passed / release candidate verified | Trusted release evidence is `release_candidate_verified=true`; Live provider, Live Edge, operational clean-start, retrieval exact-revision, and sanitized Evidence Pack lineage all passed. This is a local portfolio release candidate, not production deployment. |
| 8 — operation/retirement | not_applicable_for_release | local prototype only; no production operations promise |

## Stage 5 vertical slices

1. `VS-01` — **Mock complete**: one Conversation supports sequential closed Cases; each Case keeps its own chronological customer/reply/proposal/result history. A signed-not-received confirmation creates one ticket and closes the Case; a repeat query sees the active ticket, returns no-action, and never writes a duplicate. Refresh/SSE replay restores the event sequence without re-execution.
2. `VS-02` — **Mock complete**: `stalled_tracking` uses server-side `evaluated_at`, timeline, and policy SLA evidence. ORD-003 reaches the proposal path; ORD-002 is within SLA and closes no-action; active tickets prevent duplicate proposals; an in-transit misreport is revised with append-only issue-type history before the stalled gate runs.
3. `VS-03` — **Mock complete**: entry/business clarification budgets, mixed injection/foreign-order/prohibited fragments, absent versus unavailable evidence, one retryable tool retry, critical-evidence escalation, duplicate/conflict/budget paths, Proposal lifecycle revalidation, decline, terminal/retryable/uncertain action outcomes, serialized mutation, and replay safety are covered by tests. The browser also exercises representative safety and recovery paths.
4. `VS-04` — **Mock verified**: a competent conditional Workflow uses the same normalized Case, central authorization, six read tools, retry/cache rules, execution/planning budgets, synthetic faults, deterministic Evidence Gate, customer response/proposal path, confirmation, and idempotent executor. The development matrix shows equal Mock outcomes on all eight shared scenarios.
5. `VS-05` — **complete / release candidate verified**: the 52-run Live development Pilot, schema-v3 Freeze, 11-case Retrieval Locked Eval, 132-run three-run Locked Eval, Live Edge journey, Dashboard check, operational clean-start, trusted reports, and sanitized Evidence Pack lineage all pass on the final revision. The development Pilot remains measurement-only and `KEEP_EXPERIMENTAL`; the Locked conclusion is `PREFER_WORKFLOW`.

## V3 Development result

The separately authorized Development identity `V3-DEV-EXEC-20260828-01`
completed on evaluated source
`98b45dc27ca2d7996152404378e87a7b9a38c3bd`. It retained all 64 planned
paired records. Agent completed 32 provider calls but failed the strict
selector schema boundary in all 32 runs; Workflow used zero provider calls,
passed quality in 14/32 runs, recorded 16 grader failures, and recorded two
`CaseFactIntegrityError` runtime failures. The Development gate is therefore
`NO_GO_DEVELOPMENT_FAILED_STOP_BEFORE_FREEZE`. No V3 Freeze, Locked Eval,
Release Evidence, or architecture conclusion is authorized. V2
`PREFER_WORKFLOW` remains the current evidence-backed conclusion. See
`docs/v3/DEVELOPMENT-EVAL-RESULTS.md`.

## Stop or reopen conditions

- Reopen Stage 1 if the supported business domain or intended user changes.
- Reopen Stage 2 if Live runs show the model cannot reliably triage or use tools within the frozen budget.
- Reopen Stage 3 if the chosen model/package combination cannot produce native tool calls or misses the frozen performance budget after pilot.
- Reopen Stage 4 before changing state semantics, side-effect authority, security gates, or framework strategy.
- Stop and ask the owner before adding any real ecommerce/carrier/payment integration, production data, public deployment, or irreversible/high-impact action.
- V3-D0 reopens design authority only. V3-A1 implementation, V3-B
  implementation, Development Eval, Freeze, and Locked Eval each require the
  explicit entry conditions in docs/v3/00-owner-review.md.

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
| ADR-026 | 2026-08-25 | Close Phase 2 after the final trusted gates and sanitized Evidence Pack lineage pass. Keep `PREFER_WORKFLOW`, the Agent experimental, cost unavailable, and the local synthetic portfolio boundary; stop expansion and enter docs/bugfix-only maintenance. | accepted by owner request; final closeout |
| ADR-027 | 2026-08-27 | Open V3-D0 as a documentation-only Owner Review track for an Adaptive Investigation Core and a gated, Case-scoped clarification-fact contract. Preserve every V2 release/failure/Freeze artifact, keep `PREFER_WORKFLOW` legal, and prohibit product code or formal Eval in this phase. | accepted scope; design package awaiting Owner Review |
| ADR-028 | 2026-08-27 | Complete V3-D0 Owner Review: accept the one-correction stuck guard and the post-Development pre-Freeze threshold process; narrow V3-B to `customer_still_reports_missing` and location-bound `reported_delivery_location_checked`. Remove fixed front-desk/neighbor/household facts. | owner-confirmed; V3-A1 not started |
| ADR-029 | 2026-08-27 | Authorize and publish only `V3A1-ENGINEERING-DEV-001`. Implement the Adaptive Investigation Core and stop at its Engineering Gate; V3-B, Development Eval, Freeze, Locked Eval, and release evidence remain unauthorized. | owner-authorized; construction task published |
| ADR-030 | 2026-08-28 | Accept `V3-A1 Engineering Gate = GO` and `V3-B0 scope revalidation = ACCEPTED_NO_SCOPE_CHANGE`; preserve the confirmed two-fact V3-B contract without expansion. | owner-confirmed; V3-B1 entry condition passed |
| ADR-031 | 2026-08-28 | Authorize only `V3B1-ENGINEERING-DEV-001` Case Fact engineering from clean `54aaef6c72760543b4d93daeb97fd97fe506bb42`; stop at the V3-B Engineering Gate. Development, Live, Freeze, Locked Eval, Release Evidence, push, deployment, and PR remain unauthorized. | owner-authorized; construction task published |
| ADR-032 | 2026-08-28 | Record the independently reviewed `V3-B Engineering Gate = GO` as `DEC-V3-028`; this does not rewrite `DEC-V3-027` or any earlier V3-B history. | owner-confirmed; prep entry condition passed |
| ADR-033 | 2026-08-28 | Authorize only `V3-DEV-EVAL-PREP-001` from clean `68767c2ebdbdefc7621d950f726946b74ab52c9f`, with reserved Development identities and provider-free PREP/DRY-RUN; no real provider, Live, formal measurement, Freeze, Locked, Release Evidence, push, deployment, or PR. | owner-authorized; paused at V3 Development Eval Preflight Owner Gate |
| ADR-034 | 2026-08-28 | Authorize only the V3 `Eval Activation` patch from clean `62e05b45fca714f1b6c64160b814adb172a8f39d`: activate the production-path Development runner/adapters, plan/preflight contracts, V3-only Development write boundary, and zero-provider activation smoke. Do not open a formal execution identity or call any provider/model. | owner-authorized; activation only |
| ADR-035 | 2026-08-28 | Record the `Eval Activation` Preflight as `NO_GO_FORMAL_DEVELOPMENT_NOT_AUTHORIZED`: the committed reserved manifests remain unexecuted, `provider_calls/model_calls=0/0`, and the next pause is `V3 Development Execution Authorization Gate`. | owner-confirmed; formal Development remains closed |
| ADR-036 | 2026-08-28 | Authorize only the `Development Budget Guard` patch from the current clean candidate: make V3 formal-run invocation accounting per actual selector/model/provider attempt, add an execution-scoped deterministic ledger, and keep all provider/model calls at `0/0`; formal Development, Live, Freeze, Locked Eval, Release Evidence, push, deployment, and PR remain unauthorized. | owner-authorized; budget-guard patch only |
| ADR-037 | 2026-08-28 | Record the two Owner Review blockers as `NO_GO_PATCH_REQUIRED`: aggregated `usage_metadata` length is not a trustworthy call count, and the existing token ceiling is not an executed cross-run hard budget. The patch must stop at `V3 Development Execution Authorization Gate`. | owner-confirmed; formal Development remains closed |

## V3 Development Eval preparation (current)

`DEC-V3-028` and `DEC-V3-029` are append-only additions to the V3 decision
ledger. The current implementation track is `V3-DEV-EVAL-PREP-001`, rooted at
clean commit `68767c2ebdbdefc7621d950f726946b74ab52c9f`. It may add only the
versioned case matrix, reserved Development contracts, trusted paired runner,
deterministic graders, isolated V3 store/report, tests, static validation, and
the `V3-PREP-DRY-RUN-001` harness. A dry-run is explicitly not V3A/V3B
Development measurement and must record provider/model calls as zero.

The pause after this preparation is **V3 Development Eval Preflight Owner
Gate**. A later Development run needs a new Owner decision specifying the
exact run count, whether DeepSeek calls are authorized and their maximum
budget, and the timeout/repeat policy. `PREFER_WORKFLOW` remains the V2
evidence-backed conclusion; no `ADOPT_AGENT` is presumed.

## V3 Eval Activation (current; append-only)

`DEC-V3-030`/`ADR-034` authorizes only the `Eval Activation` implementation
from clean source `62e05b45fca714f1b6c64160b814adb172a8f39d`. This narrow patch
may connect a future real Development runner to the existing production
composition root, make the committed plan mechanically inspectable, add
fail-closed authorization and V3-only raw-record storage, repair the paired
fairness contract, and run a zero-provider production-path activation smoke.
It may not consume a formal execution identity, call DeepSeek or another real
provider/model, run Development measurement, Freeze, Locked Eval, Release
Evidence, or change the V2 `PREFER_WORKFLOW` conclusion.

`DEC-V3-031` records the resulting Preflight as **NO_GO_FORMAL_DEVELOPMENT_NOT_AUTHORIZED**.
The reserved manifests remain `reserved_not_executed`; the activation smoke is
labelled `ACTIVATION_SMOKE_NOT_DEVELOPMENT_MEASUREMENT`; and provider/model
calls remain `0/0`. The current pause is **V3 Development Execution
Authorization Gate**. A later Owner authorization must provide the complete
execution identity, manifest/source/version binding, `LLM_MODE=live`, named
credential-presence confirmation, explicit token ceiling, timeout/repeat
parameters, and provider-call ceiling before any formal run can open.

## V3 Development Budget Guard (current; append-only)

`DEC-V3-032`/`ADR-036` authorize only the `Development Budget Guard` patch. It
may repair exact per-invocation accounting, deterministic execution-scoped
provider admission, cumulative observed-token stopping, complete 64-run
fail-closed retention, report/CLI metrics, local fake-provider tests, and
documentation. It must not call DeepSeek or any real provider/model; all
provider/model evidence for this patch remains `0/0`. It must not consume a
formal execution identity or run formal Development measurement, Live, Freeze,
Locked Eval, Release Evidence, push, deployment, or PR workflows.

`DEC-V3-033`/`ADR-037` records `NO_GO_PATCH_REQUIRED` for the two Owner Review
budget blockers. The historical V2 `PREFER_WORKFLOW` conclusion and all
previous evidence identities remain unchanged. After the local clean commit,
the current pause is **V3 Development Execution Authorization Gate**.

## V3-B1 engineering track (historical; Case Fact only)

The Owner has reopened a narrow V3 design scope without reopening the V2
release lineage. The active package is
`docs/v3/00-owner-review.md` with identity
`V3-DESIGN-OWNER-REVIEW-001`.

V3-A specifies a typed NextObservation boundary, deterministic validation and
Observation Router, Evidence Progress reconstructible from ToolCall and
EvidenceRef history, one adjacent exact retry, early-stop/premature-finish/
stuck guards, minimum decision/recovery/state trace contracts, and
observation-conditioned deterministic trajectory graders. Agent and strong
Workflow must share every downstream runtime and authority; only their
next-observation selector may differ.

V3-B is a preregistered Case-scoped contract only. It may later add append-only
CaseFactAssertion records and a rebuilt current CaseFactSnapshot for a closed
whitelist of source_message_id-bound customer clarification claims. It is not
long-term memory, a MemoryStore, a user profile, or vector retrieval. V3-B
engineering is blocked until the V3-A1 Engineering Gate passes. The confirmed
whitelist is `customer_still_reports_missing` plus
`reported_delivery_location_checked`; the latter is valid only when
bound to the current delivery-proof ToolCall/result hash.

V3-D0 modified no product code, dependencies, migrations, prompts, Fixtures,
Eval manifests, Freezes, raw reports, trusted delivery reports, Release
Evidence, Evidence Packs, or historical failures. Owner Review is complete.
The Owner accepted the V3-A1 Engineering Gate as GO and accepted V3-B0 with
no scope change. Only `V3B1-ENGINEERING-DEV-001` is authorized, with canonical
task order `docs/v3/V3-B1-CONSTRUCTION-TASK.md`. Development, Live, Freeze,
Locked Eval, Release Evidence, push, deployment, and PR remain unauthorized.
The historical terminal was the V3-B Engineering Gate; `DEC-V3-028` records
that Gate as GO. The current terminal is the V3 Development Eval Preflight
Owner Gate under `DEC-V3-029`.

## Historical Phase 2 task records

The following subsections retain the authorized scope boundaries and failure
records from earlier Phase 2 checkpoints. Their earlier “must not run” or “not
executed” statements are historical; the final closeout below is the current
status and is the only current release claim.

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

## Phase 2 final closeout (current)

`PHASE2-FINAL-CLOSEOUT` is complete. The final state is:

```yaml
phase_2: complete
portfolio_release_candidate: verified
architecture_conclusion: PREFER_WORKFLOW
agent_status: experimental_comparison_path
cost_status: unavailable_without_price_basis
expansion_status: STOP
maintenance_mode: docs_and_bugfixes_only
```

The final release candidate is F-final
`9a947e78b60adf6151b397a678105896b8115aa1`. It descends from S-final
`532721339da4e06e07ebc9d9b23a7f58cab084e4`, where the operational harness was
repaired after the F2 failure. F-final added only the schema-v3 Freeze
`evals/config/freezes/acceptance-live-phase2-policy-rag-20260825-r3.json`.

The final gates are recorded by the trusted scripts: the Live development
Pilot retained 52/52 records and remains measurement-only with
`KEEP_EXPERIMENTAL`; Retrieval Locked passed 11/11 cases with quality, safety,
and exact revision; the Main Locked report `eval_ca8cb853b45d439a914866463b1865c9`
passed 132/132 records, including 8/8 stable Investigation and 8/8 stable Full
E2E cases for both Agent and Workflow; the Live provider contract, Live Edge
journey, clean-start operational lane, framework integration, registered tests,
and release checks all passed. The final release report is
`release_candidate_verified=true` and `PREFER_WORKFLOW` because the Agent had
zero stable-pass advantage, used more reads (126 vs 111), and had a 5.2376x
Investigation median-latency ratio; cost remains unavailable rather than
fabricated.

The sanitized Evidence Pack is
`delivery/evidence-packs/acceptance-live-phase2-policy-rag-20260825-r3/`.
C-final is `f0cc7be5eebf7eb5262163719b98eef53f54a7f0`; B-final is
`e5b03ce8cecd8d2245816abed9612e3dbd4a493e`. The trusted verify command passed
on B-final and proved source → payload → binding lineage. The documentation-only
closeout commit is intentionally outside that evaluated lineage.

The F2 operational failure remains archived at
`var/release-attempts/phase2-final/attempt-f2-a2e226458cdb/`. Its assertion
SHA-256 is `a2e226458cdb597ad69570ad222ff910ccc4f634789d77e3e9669456b213a920`.
The cause was the harness's hard-coded 10-second HTTP timeout while the real
local Policy RAG model/index cold start took 41.739 seconds; it was not a
provider, migration, database, or readiness failure. S-final introduced finite
stage-specific request/readiness timeouts and safe diagnostic metadata; the
affected operational evidence was rebuilt on S-final and F-final.

No business case, prompt, corpus, retrieval Top-K, minimum similarity,
Resolver, Evidence Gate, Locked label, or Agent/Workflow scope changed during
closeout. No Phase 3 or expansion backlog is opened. Any future material
change requires a new owner-authorized scope decision and a new evidence
lineage; ordinary maintenance is limited to documentation and bug fixes.

## Development Eval Execution attempt (2026-08-28)

The Owner-authorized Development measurement was prepared from clean source
`b429bd871b033dbf96ab84401698c6d5d889af28` with execution identity
`V3-DEV-EXEC-20260828-01`. The formal entry is now a narrow, identity-scoped,
write-once authorization package that separates the evaluated implementation
commit from the reserved manifest dataset revision. It binds committed
manifest digests, plan version/digest, the 32 production case inputs, all
provider/token/timeout/repeat parameters, and acceptance of the post-response
cumulative observed-token stop semantics. Runtime run/report state is
append-only and package-bound. Reserved manifests, historical failures, V2
Release Evidence, and the `PREFER_WORKFLOW` conclusion were not changed.

The required boolean credential check returned
`DEEPSEEK_API_KEY_PRESENT=false`, so package creation and the formal
64-run measurement were not started. Provider calls and model calls remain
`0/0`; no Mock fallback was used as formal evidence. The task is paused at the
Development Results Owner Gate. Live browser, Freeze, Locked Eval, Release
Evidence, and architecture adoption remain unexecuted.

# V3 Owner Review Handoff

~~~yaml
package_id: V3-DESIGN-OWNER-REVIEW-001
package_status: OWNER_REVIEW_REQUIRED
documentation_phase: design_and_acceptance_contract_only
product_grade: G1_local_portfolio_prototype
current_checkout: 2e8c39c2da56007ba14eb4a81cbf654307ae7744
immutable_v2_evaluated_source: 9a947e78b60adf6151b397a678105896b8115aa1
immutable_v2_evaluation_revision: acceptance-live-phase2-policy-rag-20260825-r3
immutable_v2_conclusion: PREFER_WORKFLOW
implementation_authorized: false
formal_eval_authorized: false
~~~

## 1. Review outcome requested

This package starts a narrow V3 design track without reopening or rewriting the
V2 release lineage. It asks the Owner to review contracts, not to accept an
implementation or an evaluation result.

The package intentionally does not claim READY_FOR_ENGINEERING_HANDOFF. Three
bounded decisions remain for Owner confirmation in section 9. No product code,
dependency, migration, Fixture, prompt, Freeze, Locked Manifest, Eval report,
Release Evidence, or Evidence Pack is created or changed by this package.

## 2. Product and grade boundary

V3 remains a local, synthetic, single-user G1 portfolio prototype. It may
demonstrate bounded read-tool orchestration and one already-existing simulated
ticket write after exact customer confirmation. It may not claim production
customer-service use, real ecommerce integration, universal Agent superiority,
or benchmark SOTA.

The grade-independent safety floor is unchanged:

- authorization, policy applicability, evidence sufficiency, Proposal
  validity, confirmation, idempotency, write verification, canonical state,
  and evaluation verdicts remain deterministic;
- the model never receives a write tool;
- absent remains a successful observation and never means unavailable;
- natural-language assent never executes a write;
- every run, including failures, must remain in its declared evidence set.

## 3. V2 evidence carried forward

The design baseline is the immutable V2 conclusion:

- Release Evidence records release_candidate_verified=true on F-final;
- Retrieval Locked passed 11/11 and Main Locked retained 132/132 records;
- both Agent and Workflow were stable on all eight locked Investigation cases;
- Agent used 126 actual reads versus Workflow 111;
- Agent Investigation median latency was 5.2376 times Workflow;
- registered_dynamic_path_advantage=false and
  resource_bounds_proven=false;
- therefore PREFER_WORKFLOW is the current evidence-backed architecture
  conclusion.

V3 is allowed to test a new hypothesis. It is not allowed to rewrite the V2
answer.

## 4. V3 objective

The V3 question is:

> When all deterministic authorities and resources are shared, can a typed,
> observation-conditioned selector produce useful investigation trajectories
> that a strong deterministic Workflow does not match at acceptable cost?

V3-A Adaptive Investigation Core makes that question testable by introducing:

- a typed NextObservation boundary;
- a deterministic Observation Validator and Observation Router;
- Evidence Progress rebuilt from persisted ToolCall and EvidenceRef records;
- one adjacent, parameter-identical deterministic exact retry;
- early-stop, premature-finish, stuck, and budget guards;
- a minimum decision/recovery/state trace contract;
- deterministic observation-conditioned trajectory graders.

V3-B adds only short-lived Case clarification facts:

- append-only CaseFactAssertion records;
- one derived current CaseFactSnapshot;
- a closed whitelist of customer-reported clarification claims;
- mandatory source_message_id provenance;
- deterministic supersession, conflict, unknown, and repeat-question behavior.

## 5. Explicit non-goals

V3 does not add:

- another business issue, refund, compensation, return, payment, or new write
  action;
- Retrieval expansion, Query Rewrite, another embedding/reranker, a generic
  knowledge base, or a new authoritative retrieval path;
- long-term Memory, MemoryStore, user profile, cross-Case memory, or vector
  retrieval over conversation facts;
- MCP, multi-agent, Monitor Agent, delegation, parallel investigation, or open
  domain behavior;
- natural-language confirmation of writes;
- an authoritative LLM Judge;
- LangSmith, Phoenix, OpenTelemetry, microservices, Redis, Kafka, Kubernetes,
  real carrier/marketplace integrations, or public deployment;
- a migration of Case, Run, Proposal, Action, transaction, or write execution
  authority into LangGraph;
- a P0 UI expansion. A readable V3 trace projection is a P1 option only after
  the V3-A1 engineering Gate.

## 6. Delivery sequence and stop rules

| Stage | Scope | Entry condition | Exit or stop |
|---|---|---|---|
| V3-D0 | This design and acceptance package | Owner request dated 2026-08-27 | Stop at Owner Review |
| V3-A1 | Adaptive Investigation Core engineering | Owner accepts this package and OD-01 | Pass the V3-A1 Engineering Gate or stop on a hard failure |
| V3-B0 | Confirm the pre-registered Case Fact contract | V3-A1 Engineering Gate passes | Owner accepts or narrows the exact whitelist |
| V3-B1 | Case Fact engineering | V3-B0 accepted | Pass its engineering Gate; no formal Eval yet |
| V3-DEV-EVAL | Development-only paired measurement | Relevant engineering Gates pass | Retain all records; Owner reviews whether Freeze is justified |
| V3-FREEZE | New V3-only Locked contract | Development evidence plus OD-03 | Create a new immutable lineage; never reuse V2 identity |
| V3-LOCKED | Formal paired Eval | Clean frozen source and explicit Owner authorization | ADOPT_AGENT, KEEP_EXPERIMENTAL, or PREFER_WORKFLOW under preregistered rules |

This handoff stops after V3-D0. None of the later rows is authorized by the
existence of this document.

## 7. Document map

1. 00-owner-review.md — phase boundary, baseline, decisions, and pause point.
2. 01-design-and-data-contracts.md — V3-A/V3-B modules, schemas, state, and
   authority.
3. 02-evaluation-gates-and-lineage.md — fairness, Development and Locked Eval,
   Go/No-Go, and version isolation.
4. decision-evidence.jsonl — append-only evidence states for material
   decisions.
5. coverage.json — discovery-area and critical-gate mapping without a
   self-declared readiness verdict.

## 8. Three principal risks

| ID | Risk | Prevention and release consequence |
|---|---|---|
| RISK-V3-01 | Graph complexity merely relocates a fixed Workflow into Router rules | Router may recover, validate, and terminate but may not select an alternative business observation. A violated boundary is No-Go. |
| RISK-V3-02 | Agent is favored by richer context, looser budgets, or weaker grading | Both selectors receive one DecisionContext and share every downstream component. Any asymmetry outside selector implementation invalidates the paired evidence. |
| RISK-V3-03 | Case facts become disguised long-term memory or model-owned truth | Facts are Case-scoped, whitelisted, source-bound, append-only, and deterministically merged. Cross-Case reuse or vectorization is prohibited. |

## 9. Minimum Owner decisions

Only the following decisions remain open. Everything else in the package is
either already fixed by the Owner request/repository contract or is a
reversible implementation detail delegated within the written acceptance
tests.

### OD-01 — guard threshold

Recommended: allow one corrective selector turn after a premature finish or
invalid repeated observation. If the same normalized decision fingerprint
appears again at the same Evidence Progress digest, stop as STUCK and route to
the deterministic safe outcome. Cached or blocked calls that leave progress
unchanged count toward the same guard.

Alternative: fail closed on the first premature finish. This is simpler but
tests recovery less meaningfully.

### OD-02 — V3-B fact whitelist

Recommended exact whitelist:

- customer_still_reports_missing;
- front_desk_checked;
- neighbor_checked;
- household_checked.

all_reception_locations_checked is derived and is not an assertion. No order,
policy, identity, address, profile, preference, action, or cross-Case fact may
enter this store.

### OD-03 — Locked architecture thresholds

Recommended process: after V3 Development evidence exists, the Owner approves
exact resource ceilings and the minimum number of preregistered dynamic-path
advantages before any V3 Freeze is created. Hard safety, trace integrity,
reconstruction, retry, and trajectory obligations are already fixed at 100%;
only the evidence-based resource/advantage thresholds remain to be frozen.

This avoids inventing latency, token, cost, or advantage numbers before the
new runtime has produced Development measurements.

## 10. Owner Review checkpoint

Owner Review should either:

- confirm OD-01, OD-02, and the OD-03 process;
- provide narrow corrections to one or more items; or
- reject V3 and retain V2 PREFER_WORKFLOW without further work.

Until that review, V3-A1 is not authorized and this package remains
OWNER_REVIEW_REQUIRED.

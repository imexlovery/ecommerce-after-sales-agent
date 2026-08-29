# Domain Contracts

Status: **Phase 2-A controlled extension; Phase 1 evidence remains frozen**
Contract revision: `2`
Last reviewed: `2026-08-25`

This document is the normative domain contract for the ecommerce after-sales
logistics investigation prototype. It fixes the vocabulary, aggregate
boundaries, state machines, tool/evidence semantics, evidence gates, and safe
write boundary. Product code, tests, fixtures, trace projections, and evals must
use these definitions without collapsing them for UI convenience.

## 1. Scope and product boundary

The supported customer intents are:

- `signed_not_received`: tracking says delivered, but the customer reports that
  the parcel was not received.
- `stalled_tracking`: the parcel has shipped, but tracking has stopped updating
  beyond the applicable policy threshold.

The system may investigate these issues and may propose one simulated write:
`create_logistics_investigation_ticket`. It does not issue refunds,
compensation, returns, replacements, carrier-liability decisions, or payment
actions. It does not access real customer, order, carrier, or payment systems.

`other_logistics`, `out_of_scope`, and `prohibited` are routing results, not new
investigation products. An unresolved `other_logistics` request ends as
`human_support_required` after the allowed entry clarification.

## 2. Aggregate hierarchy

```text
Conversation
├── Message
├── TriageRecord
├── PolicyDecision
└── InvestigationCase
    ├── Run #1
    ├── Run #2
    ├── ...
    ├── ActionProposal(s)
    └── ActionExecution(s)
```

Definitions:

- **Conversation** is the ordered customer interaction stream. It may contain
  multiple sequential cases.
- **InvestigationCase** is the business aggregate for exactly one authorized
  order and one primary issue.
- **Run** is one processing attempt or explicit state-changing interaction. An
  initial message, confirmation, decline, or retry each produces its own Run
  and audit events.
- **ActionRecommendation** is the Agent's non-executable suggestion.
- **ActionProposal** is an immutable, server-created offer of one exact action
  for the customer's confirmation.
- **ActionExecution** records the deterministic executor's attempt and outcome.

An `InvestigationCase` may be created only after both conditions hold:

1. the server has resolved an `authorized_order_id`; and
2. the policy router has resolved a supported issue.

The Case boundary is fixed:

```text
1 InvestigationCase
= 1 authorized order
+ 1 primary issue
+ at most 2 business clarifications
+ at most 6 actual read-tool executions
+ at most 16 Agent planning turns across the Case
+ deterministic Evidence Gate
+ at most 1 active executable ActionProposal
```

Each Run is limited to 8 Agent planning turns. A blocked tool request and a
Case-cache hit consume a planning turn but not the six-execution tool budget. A
real retry of a retryable tool failure consumes an actual execution.

Messages that can affect the same Case are processed serially. The system must
not allow two Runs to mutate one Case concurrently. Multiple authorized orders
or multiple issues are resolved by asking the customer which one to investigate;
the system does not create parallel Agent branches.

The entry router may ask at most one entry clarification. After a Case exists,
the investigation may ask at most two business clarifications.

## 3. Four state machines are separate contracts

There is no generic `status` enum. The four state machines below describe
different lifecycles and must remain separate in persistence, APIs, UI
projections, traces, and eval assertions.

### 3.1 Case state and outcome

```text
CaseState:
  investigating
  awaiting_customer_input
  awaiting_customer_confirmation
  awaiting_retry
  executing_action
  closed

CaseOutcome (present only when CaseState == closed):
  resolved_no_action
  ticket_created
  human_support_required
  uncertain
  failed
```

Rules:

- An open Case has no `CaseOutcome`.
- A closed Case is immutable and is never reopened in place. A later customer
  request creates a new Case and may set `related_case_id` to the old Case.
- `failed` means the business process is known to be unable to continue and has
  terminated. A single model timeout or tool failure belongs to Run or Action
  state and does not by itself make the Case outcome `failed`.
- `uncertain` means an action might have happened, but its side effect cannot be
  verified safely. It is a terminal automation outcome.
- A deterministic reason code accompanies every Case closure; the reason code
  is not a substitute for `CaseOutcome`.

### 3.2 Run state

```text
RunState:
  queued
  running
  succeeded
  failed
```

A Run failure records the concrete failure class, such as model timeout, schema
failure, tool error, or policy rejection. Run failure does not erase evidence
already gathered by the Case and never authorizes a write.

### 3.3 Proposal state

```text
ProposalState:
  pending_confirmation
  confirmed
  declined
  superseded
  expired
  invalidated
```

An ActionProposal is append-only and immutable. It is never overwritten by
incrementing a version in place. If customer facts, critical evidence, policy,
or execution parameters change, the system reruns the Evidence Gate and creates
a new Proposal. The old record remains for audit with `superseded`, `expired`,
or `invalidated`. A Proposal expires 15 minutes after creation.

Only `pending_confirmation` may be confirmed or declined. Natural-language
messages such as “好” or “可以” never confirm a Proposal. Confirmation must come
from the exact UI control and carry the Proposal identifier and version.

### 3.4 Action state

```text
ActionState:
  ready
  submitted
  succeeded
  failed_retryable
  failed_terminal
  uncertain
```

`submitted` means the write request left the deterministic executor and the side
effect may already have happened. If the response is lost and read-back is also
unavailable, the action becomes `uncertain`. The original action identity and
idempotency key are preserved. A customer retry must not create a new key or
submit another write whose first result is unknown.

### 3.5 Customer disposition projection

`CustomerDisposition` is a deterministic customer-facing projection, not a
replacement state machine and not a field the model may author. Its exact
values are:

```text
ANSWER
WAIT
CLARIFY
INVESTIGATE
ESCALATE
```

The projection reads structured gate reasons and the independent Case, Proposal,
and Action lifecycles:

| Structured situation | CustomerDisposition |
|---|---|
| Explained result, no action, or safe refusal | `ANSWER` |
| Active existing Case, within-SLA result, or retry later | `WAIT` |
| Bounded entry/business clarification | `CLARIFY` |
| Eligible Proposal or successfully created and verified investigation ticket | `INVESTIGATE` |
| Human support, conflict, uncertain write, or exhausted unsafe path | `ESCALATE` |

API schemas, event payloads, TypeScript types, and the customer surface use the
same enum values. `evidence_availability=absent` remains a successful
observation that can support `INVESTIGATE` or `ANSWER`; `unavailable` remains
unknown and can lead to `WAIT` or `ESCALATE` according to the structured gate
reason.

## 4. Triage and deterministic policy boundary

Triage is a tool-free lightweight model call with this only output:

```text
TriageResult:
  intent: signed_not_received | stalled_tracking | other_logistics
          | ambiguous | out_of_scope | prohibited
  risk_flags: list[string]
  order_ids_mentioned: list[string]
  confidence: number
```

The deterministic policy router derives the coarse route (`supported logistics`,
`ambiguous`, `out of scope`, or `prohibited`) from this result and validation
facts. Only `signed_not_received` and `stalled_tracking` are supported Case issue
types. `other_logistics` can trigger the one allowed entry clarification but does
not silently broaden the product. Triage does not authorize orders, decide
evidence sufficiency, or execute actions.

Deterministic validation and policy code enforce these rules:

| Input component | Customer behavior | Internal behavior |
|---|---|---|
| Instruction override or system-prompt request | Refuse that fragment; continue any valid logistics request | Record the blocked override attempt |
| Unauthorized order reference | Say that unrelated orders cannot be accessed; continue an authorized order if present | Block before data access; do not reveal whether the other order exists |
| Refund or compensation demand | Refuse the prohibited action; continue the logistics investigation | Record prohibited-action fragment |
| Malicious content with no valid logistics request | Do not investigate | No Case and no tool access |
| Unnecessary personal information | Advise the customer not to share it | Redact it from developer trace projections |

The governing principle is to block the malicious, unauthorized, or prohibited
subrequest, not to discard a valid authorized logistics request merely because
both occur in the same message.

Triage timeout or schema failure allows no tool call. The UI exposes a retry;
Live mode never falls back silently to Mock mode.

## 5. Trusted context and authorization

The server supplies this context; the model cannot set or override it:

```text
TrustedToolContext:
  customer_id
  conversation_id
  case_id
  run_id
  authorized_order_id
  canonical_issue_type
  fixture_version
  fault_seed
  evaluated_at
  trace_id
```

`evaluated_at` is the scenario clock used for SLA and timeline calculations.
Tools and eval fixtures must not use wall-clock time for scenario decisions.

Every order-scoped tool calls the same
`authorize_order(customer_id, order_id)` function immediately before reading
data. Not-found and unauthorized results collapse to
`ORDER_NOT_FOUND_OR_FORBIDDEN`; neither the customer projection nor developer
trace may reveal whether a foreign order exists.

If model arguments contain an `order_id` or `issue_type` that differs from the
canonical Case values, deterministic code blocks the request before data access.
The blocked request consumes a planning turn but not an actual tool execution.

The model cannot submit `customer_id`, `tracking_number`, or `service_level`.
The server resolves the latter two from the authorized `order_id`.

## 6. Read-tool contract

The Agent may dynamically select only these read tools:

```text
get_order_context(order_id)
get_logistics_timeline(order_id)
get_delivery_proof(order_id)
get_carrier_service_alerts(order_id)
search_after_sales_policy(order_id, issue_type)
get_existing_logistics_tickets(order_id, issue_type)
```

The action executor is not an Agent tool. In particular,
`create_logistics_investigation_ticket` is never bound to the model.

`search_after_sales_policy` replaces the old direct fixture lookup; it does not
add another tool slot. Its retriever and Resolver are internal server components
and a complete search still consumes one actual read-tool execution. The model
may submit only `order_id` and `issue_type`; identity, authorization,
`service_level`, `region`, and `evaluated_at` remain trusted server context.

### 6.1 Controlled Policy RAG result semantics

The policy tool keeps the existing `ToolResult` execution/error semantics and
adds two orthogonal policy fields to its payload:

```text
retrieval_status: hit | no_hit | unavailable
policy_resolution_status: applicable | not_applicable | version_conflict | null
```

- `hit` has a Resolver outcome. It can be `applicable`, `not_applicable`, or
  `version_conflict`.
- `no_hit` means either no candidate reached the pre-registered reliability
  threshold, or the complete canonical authority set proves that one applicable
  clause exists but no returned candidate verifies as that clause. It does not
  mean that policy is absent. Resolution is `null`, with no fact snapshot or
  citation.
- `unavailable` means the index, embedding, or retrieval dependency failed.
  Resolution is `null`, and the ToolResult has the existing
  `execution_status != success` plus `EvidenceAvailability.UNAVAILABLE`.
- `not_applicable` is valid only after the complete canonical authority set has
  no active, non-poisoned clause for the trusted issue, service level, region,
  and time. A wrong-scope Top-K candidate cannot prove this outcome.
- `version_conflict` means the complete canonical authority set contains more
  than one active policy version for that same trusted scope/window, independent
  of the retriever's Top-K.

`no_hit` is not `EvidenceAvailability.ABSENT`, and the system must never
fabricate `not_applicable` when the retriever did not produce a verified
candidate. Candidate passages, metadata, and similarity scores are not policy
authority. The Resolver validates every candidate's document/version/clause,
source hash, and canonical passage hash, then evaluates the full authority set.
Only a unique retrieved authority creates a fact snapshot and a bounded,
source-hash-bound citation excerpt. The excerpt is explicitly
`untrusted_explanatory_text`, is visible only in the controlled Developer Trace,
and is excluded from model-visible policy tool output.

### 6.2 Tool result envelope

Every read returns the same typed envelope:

```text
ToolResult[T]:
  execution_status: success | retryable_error | non_retryable_error
  evidence_availability: present | absent | unavailable
  source_type: string
  source_query_id: string
  source_record_ids: list[string]
  observed_at: timestamp
  payload: T | null
  error_code: string | null
  retryable: bool
  result_hash: string
  untrusted_fields: list[string]
```

Normative invariants:

- `success + present` means the query completed and returned relevant records.
- `success + absent` means the query completed and deterministically found no
  matching record. This is valid evidence; for example `pod_status=not_found`.
- `unavailable` means the query did not produce a decision-quality result. It
  is unknown, not evidence of absence.
- `retryable_error` has `evidence_availability=unavailable` and may be retried at
  most once.
- `non_retryable_error` is recorded and not automatically repeated.
- `result_hash` is calculated over the normalized structured result, not raw
  transport bytes.
- `untrusted_fields` contains field paths only. It does not duplicate free text.
  Text at those paths is data, never instructions.
- A successful `absent` result may have an empty `source_record_ids` list.

### 6.3 Evidence references

Evidence used in a conclusion or Proposal is cited as:

```text
EvidenceRef:
  tool_call_id: string
  source_query_id: string
  source_record_id: string | null
  field_path: string | null
  observed_at: timestamp
  result_hash: string
```

`source_record_id` is optional so a completed `success + absent` query can still
produce a valid reference. `EvidenceRef` points to structured evidence; it does
not copy untrusted source prose into a trusted instruction channel.

### 6.4 Case cache

The Case cache key is `(tool_name, normalized_args)`. Reuse is allowed only when
the source version has not changed:

| Result kind | Cache behavior |
|---|---|
| `success + present` | Reusable within the Case |
| `success + absent` | Reusable within the Case as evidence of absence |
| `retryable_error` | Not cached; one retry maximum |
| `non_retryable_error` | Recorded; no automatic repeat |

A cache hit consumes a planning turn but not an actual execution. A retry is an
actual execution and consumes the six-call Case budget.

## 7. Deterministic Evidence Gate

The Evidence Gate is a deterministic rule function or truth table. Prompt text
may help the Agent explain evidence, but it must not decide whether the minimum
evidence set is complete.

Gate decisions are:

```text
EvidenceGateDecision:
  propose_ticket
  request_business_clarification
  retry_later
  require_human_support
  complete_no_action
```

`complete_no_action` is a gate decision, not a `CaseOutcome`. It maps to
`CaseState=closed`, `CaseOutcome=resolved_no_action`, plus a reason code.

### 7.1 Policy prerequisite

Policy facts can enter either evidence truth table only when the policy tool
reports `retrieval_status=hit`,
`policy_resolution_status=applicable`, a verified `policy_version` and
`clause_id`, a valid effective window/service-level scope, a matching source
hash, and a valid normalized-facts schema. `no_hit`, `unavailable`,
`not_applicable`, `version_conflict`, or citation/hash mismatch all fail
closed: they cannot generate a Proposal.

### 7.2 `signed_not_received`

All rows assume authorization has already passed.

| Order/timeline | POD query | Existing-ticket query | Policy query | Customer fact | Decision |
|---|---|---|---|---|---|
| Delivered; timeline query succeeded | `success + present` and POD indicates reception by front desk, neighbor, or family | Succeeded; no active ticket | Succeeded; eligible | Customer has not yet confirmed those locations were checked | `request_business_clarification` |
| Delivered; timeline query succeeded | `success + present` with no resolving reception fact, or `success + absent` | Succeeded; no active ticket | Succeeded; eligible | Customer still reports missing | `propose_ticket` |
| Delivered | Any completed value | Succeeded; active matching ticket | Any completed value | Any | `complete_no_action` with existing-ticket reason |
| Delivered | `unavailable` | Any | Any | Any | `retry_later`; after the allowed retry or persistent unsafe uncertainty, `require_human_support` |
| Delivered | Any | `unavailable` | Any | Any | Block Proposal; `retry_later` or `require_human_support` |
| Delivered | Any | Any | `unavailable` or ineligible | Any | No Proposal; retry if transient, otherwise `require_human_support` or `complete_no_action` according to the known policy result |
| Not delivered | Any | Any | Any | Any | Revise the canonical issue in the same Case to `stalled_tracking` or `other_logistics`, record revision history, then rerun the applicable gate |

`success + absent` for POD is a completed, decision-quality query. It must not
be treated as `unavailable`. A disagreement between the customer's report and a
POD fact is a business dispute, not automatically a structural data conflict.

### 7.3 `stalled_tracking`

All rows assume authorization has already passed.

| Order state | Timeline/SLA | Existing-ticket query | Decision |
|---|---|---|---|
| Shipped | Timeline query succeeded and exceeds the policy SLA | Succeeded; no active ticket | `propose_ticket` |
| Shipped | Timeline query succeeded and exceeds the policy SLA | Succeeded; active matching ticket | `complete_no_action` with existing-ticket reason |
| Not shipped | Any | Any | `complete_no_action` |
| Shipped | Within policy SLA | Any completed value | `complete_no_action` |
| Shipped | Timeline or policy evidence `unavailable` | Any | `retry_later`; after allowed retry or persistent unsafe uncertainty, `require_human_support` |
| Shipped | Beyond SLA | Existing-ticket query `unavailable` | Block Proposal; `retry_later` or `require_human_support` |
| Any | Structural conflict remains after one directed refresh | Any | `require_human_support` |

Carrier service alerts are optional explanatory evidence. Their absence or
unavailability does not replace the required timeline, policy, and active-ticket
checks.

### 7.4 Common hard conditions

No Proposal may be created when any of these is true:

- order authorization is not currently valid;
- a required query is `unavailable`;
- the existing-ticket query did not complete successfully;
- an active duplicate ticket exists;
- the Case or tool budget was exceeded;
- the recommendation requests an unsupported action; or
- evidence or execution parameters no longer match the active Case.

## 8. Recommendation, Proposal, confirmation, and execution

The Agent may emit an `ActionRecommendation`, but it has no execution authority.
The server creates an `ActionProposal` only after the Evidence Gate passes. The
Proposal contains exact execution parameters, customer-visible effect, expiry,
version, and `evidence_snapshot_hash`.

```text
ActionRecommendation:
  action_type: create_logistics_investigation_ticket
  rationale_summary: string
  evidence_refs: list[EvidenceRef]

ActionProposal:
  proposal_id: string
  case_id: string
  version: integer
  proposal_state: ProposalState
  action_type: create_logistics_investigation_ticket
  execution_parameters: object
  customer_visible_effect: string
  evidence_refs: list[EvidenceRef]
  evidence_snapshot_hash: string
  policy_version: string
  clause_id: string
  policy_source_hash: string
  policy_fact_snapshot: object
  policy_fact_snapshot_hash: string
  created_at: timestamp
  expires_at: timestamp

ActionExecution:
  action_id: string
  proposal_id: string
  action_state: ActionState
  idempotency_key: string
  submitted_at: timestamp | null
  verified_at: timestamp | null
  error_code: string | null
```

`execution_parameters` are produced and validated by server code. A model cannot
smuggle arbitrary write parameters through `rationale_summary` or evidence text.

The hash covers only the critical evidence and execution parameters on which the
Proposal depends. It excludes unrelated data so irrelevant fixture changes do
not invalidate the Proposal.

On confirmation, deterministic code revalidates:

1. current order authorization;
2. Proposal identity, version, state, and expiry;
3. the critical evidence snapshot;
4. canonical policy version, clause ID, source hash, and material normalized
   fact snapshot/hash;
5. absence of an active duplicate ticket; and
6. the action/idempotency identity.

Only then may the executor submit
`create_logistics_investigation_ticket`. After submission, a separate read-back
verifies the result. The executor follows this state logic:

```text
ready
  → submitted
      → succeeded                  # write acknowledged/read-back verified
      → failed_retryable           # known not to have happened; safe retry path
      → failed_terminal            # known terminal rejection
      → uncertain                  # may have happened; cannot verify
```

The action idempotency identity is stable across retries. An `uncertain` action
may be inspected or resolved by human support, but it is never automatically
resubmitted under a fresh idempotency key.

## 9. Revision and compatibility rules

- `issue_type_revision_history` or equivalent append-only events record every
  `reported → canonical` correction and its evidence.
- Event contracts, API schemas, tool schemas, fixture schemas, and enum values
  are versioned internal contracts once consumed by the UI or eval harness.
- New optional fields should preserve backward compatibility. Renaming enum
  values, collapsing states, or changing evidence semantics requires an explicit
  contract revision and matching fixture/eval migration.
- Neither customer output nor Developer Trace may expose raw chain-of-thought,
  system prompts, API keys, full personal information, provider request bodies,
  or stack traces.

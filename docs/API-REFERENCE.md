# API and SSE Reference

Status: **Frozen v1 prototype contract**  
Base path: `/v1`  
Content type: `application/json` unless otherwise stated

This API serves a local, single-user demo backed only by synthetic fixtures. The
virtual customer selector is a developer/demo control, not an authentication
protocol. Server-side trusted context and order authorization remain mandatory;
the model never receives authority from request text or model-generated tool
arguments.

The domain enums and invariants in [DOMAIN-CONTRACTS.md](./DOMAIN-CONTRACTS.md)
are normative for all responses.

## 1. Common conventions

Identifiers are opaque strings. Timestamps use ISO 8601 UTC. Optional fields may
be omitted or returned as `null` as declared; consumers must not infer a Case
outcome while the Case is open.

### 1.1 Error envelope

Non-2xx responses use:

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Safe customer or developer message",
    "retryable": false,
    "trace_id": "trc_opaque"
  }
}
```

`message` never reveals whether a foreign order exists, raw provider output,
system instructions, secrets, stack traces, or unredacted personal data.

Common status semantics:

| HTTP | Meaning |
|---:|---|
| `200` | Synchronous read or accepted synchronous state transition completed |
| `201` | Conversation was created |
| `202` | A Run was created and queued/running; follow SSE or Run read model |
| `400` | Invalid schema or unsupported transition request |
| `404` | Resource not visible to this synthetic session; order lookup uses the same response for absent and forbidden |
| `409` | Version/state conflict, expired/inactive Proposal, duplicate in-flight Case mutation, or unsafe retry |
| `422` | Structurally valid request that violates a domain invariant |
| `503` | Explicit retryable model/tool/service failure; never a silent Live-to-Mock fallback |

## 2. Conversation endpoints

### `POST /v1/conversations`

Creates a conversation for an allowlisted synthetic customer fixture.

Request:

```json
{
  "fixture_customer_key": "customer_a"
}
```

The key is validated and resolved server-side to trusted `customer_id`. It is a
demo selector and must not be forwarded to the model as authority.

Response `201`:

```json
{
  "conversation_id": "conv_opaque",
  "fixture_customer_key": "customer_a",
  "llm_mode": "mock",
  "created_at": "2026-08-23T00:00:00Z",
  "events_url": "/v1/conversations/conv_opaque/events"
}
```

`llm_mode` is exactly `mock` or `live`. It reflects the configured mode and may
not change because a provider call failed.

### `POST /v1/conversations/{conversation_id}/messages`

Appends one free-text customer message and starts a Run. The service serializes
requests that could affect the same active Case. One Conversation may contain
many sequential Cases, but at most one Case may be non-closed at a time. When
that Case is awaiting a business clarification, the next customer message is
attached to the same `case_id`; otherwise a new message is rejected until the
open Case resolves. After a Case closes, a new supported message creates a new
Case and preserves the earlier Case as read-only history.

Request:

```json
{
  "content": "我的 ORD-001 显示签收了，但我没有收到"
}
```

Response `202`:

```json
{
  "run_id": "run_opaque",
  "case_id": "case_opaque",
  "events_url": "/v1/conversations/conv_opaque/events"
}
```

`case_id` is nullable because validation, ambiguous triage, out-of-scope, or
prohibited-only messages may not create a Case.

The endpoint only acknowledges the Run. It does not imply that triage,
investigation, or an action succeeded.

### `GET /v1/conversations/{conversation_id}`

Returns the current read model:

```json
{
  "conversation_id": "conv_opaque",
  "fixture_customer_key": "customer_a",
  "llm_mode": "mock",
  "messages": [],
  "cases": [
    {
      "case_id": "case_opaque",
      "case_state": "investigating",
      "case_outcome": null,
      "authorized_order_id": "ORD-001",
      "canonical_issue_type": "signed_not_received"
    }
  ],
  "active_case_id": "case_opaque",
  "updated_at": "2026-08-23T00:00:00Z"
}
```

`cases` is chronological and includes closed historic Cases. The customer
timeline is reconstructed by canonical event `sequence`, with all messages,
replies, Proposals, retries, and results scoped by `case_id`. This read model is
derived from business tables; the event log remains the append-only audit fact
stream. The project does not implement full Event Sourcing.

## 3. Case and Run endpoints

### `GET /v1/investigation-cases/{case_id}`

Returns one Case without collapsing its state machines:

```json
{
  "case_id": "case_opaque",
  "conversation_id": "conv_opaque",
  "related_case_id": null,
  "authorized_order_id": "ORD-001",
  "reported_issue_type": "signed_not_received",
  "canonical_issue_type": "signed_not_received",
  "issue_type_revision_history": [],
  "case_state": "awaiting_customer_confirmation",
  "case_outcome": null,
  "reason_code": null,
  "business_clarification_count": 0,
  "actual_read_tool_execution_count": 4,
  "agent_planning_turn_count": 5,
  "active_proposal_id": "prop_opaque",
  "created_at": "2026-08-23T00:00:00Z",
  "updated_at": "2026-08-23T00:00:03Z"
}
```

For `case_state=closed`, `case_outcome` and `reason_code` are required. For every
other state, `case_outcome` is null.

### `GET /v1/runs/{run_id}`

```json
{
  "run_id": "run_opaque",
  "conversation_id": "conv_opaque",
  "case_id": "case_opaque",
  "run_kind": "message",
  "run_state": "succeeded",
  "planning_turn_count": 5,
  "actual_read_tool_execution_count": 4,
  "failure_code": null,
  "created_at": "2026-08-23T00:00:00Z",
  "completed_at": "2026-08-23T00:00:03Z"
}
```

`run_kind` distinguishes `message`, `confirmation`, `decline`, and `retry`.
Confirmation, decline, and retry are never implemented as silent database
patches.

### `POST /v1/investigation-cases/{case_id}/retry`

Creates a new retry Run only when the Case is `awaiting_retry` and no action is
in an unsafe `submitted` or `uncertain` state.

Request:

```json
{}
```

Response `202`:

```json
{
  "run_id": "run_retry_opaque",
  "case_id": "case_opaque",
  "events_url": "/v1/conversations/conv_opaque/events"
}
```

Retries reuse valid Case evidence and follow tool-cache/retry budgets. They do
not reset counters. A retryable action failure may resume only with its original
`action_id` and idempotency key; a `submitted` or `uncertain` action is never
given a fresh key or blindly retried.

## 4. Proposal endpoints

The Agent cannot create an executable Proposal. A Proposal is created by the
server only after the deterministic Evidence Gate passes.

### `POST /v1/action-proposals/{proposal_id}/confirm`

Request:

```json
{
  "proposal_version": 1
}
```

Response `202`:

```json
{
  "run_id": "run_confirm_opaque",
  "case_id": "case_opaque",
  "proposal_id": "prop_opaque",
  "proposal_state": "confirmed",
  "events_url": "/v1/conversations/conv_opaque/events"
}
```

Before execution, the server revalidates authorization, Proposal identity and
version, expiry, evidence snapshot, policy, duplicate-ticket state, and the
stable action/idempotency identity. A `202` means the confirmation Run started;
it does not mean the ticket was created.

Stale version, non-pending state, expiry, changed critical evidence, or a newly
active duplicate returns `409` and produces an audit event. Natural-language
chat never calls this endpoint implicitly.

### `POST /v1/action-proposals/{proposal_id}/decline`

Request:

```json
{
  "proposal_version": 1
}
```

Response `202`:

```json
{
  "run_id": "run_decline_opaque",
  "case_id": "case_opaque",
  "proposal_id": "prop_opaque",
  "proposal_state": "declined",
  "events_url": "/v1/conversations/conv_opaque/events"
}
```

Decline creates a separate Run and events. It never executes the proposed
action.

## 5. Evaluation endpoint

### `GET /v1/evals/latest`

Returns the most recent immutable, versioned eval report for the Dashboard. It
does not start an eval run.

Response `200`:

```json
{
  "report_id": "eval_opaque",
  "evaluation_revision": "acceptance-live-r1",
  "created_at": "2026-08-23T00:00:00Z",
  "dataset_partition": "locked",
  "versions": {
    "model": "pinned-model-id",
    "prompt": "prompt-v1",
    "tool_schema": "tool-v1",
    "fixture": "fixture-v1",
    "environment": "local-v1"
  },
  "safety_gate_pass": true,
  "acceptance_gate_pass": true,
  "sections": {
    "safety": {},
    "task_quality": {},
    "tool_trajectory": {},
    "stability": {},
    "latency": {},
    "token": {},
    "cost": {},
    "agent_vs_workflow": {}
  },
  "architecture_conclusion": "KEEP_EXPERIMENTAL",
  "raw_run_count": 132
}
```

The response preserves sections; it must not collapse them into a single score.
Raw case-level results and failures remain addressable from the stored report
artifact even if the UI initially renders only summaries.

If no immutable report exists, the endpoint returns `404` with
`error.code = EVAL_REPORT_NOT_FOUND`. The API does not fabricate an empty report
or placeholder metrics.

## 6. SSE stream

### `GET /v1/conversations/{conversation_id}/events`

Content type: `text/event-stream`.

The server persists each event before publishing it. Delivery is at least once.
Clients deduplicate by `event_id`, persist the highest contiguous `sequence`, and
reconnect with the standard `Last-Event-ID` header. Replay or reconnect must
never rerun triage, Agent planning, tools, or actions.

Wire example:

```text
id: evt_opaque
event: tool_completed
data: {"schema_version":1,"event_id":"evt_opaque","sequence":17,"timestamp":"2026-08-23T00:00:02Z","conversation_id":"conv_opaque","case_id":"case_opaque","run_id":"run_opaque","event_type":"tool_completed","visibility":"developer","summary":"Delivery proof query completed","payload":{"execution_status":"success","evidence_availability":"absent"},"evidence_refs":[{"tool_call_id":"call_opaque","source_query_id":"query_opaque","source_record_id":null,"field_path":null,"observed_at":"2026-08-23T00:00:02Z","result_hash":"sha256:opaque"}]}
```

### 6.1 Event envelope

```text
EventEnvelope:
  schema_version: integer
  event_id: string
  sequence: integer
  timestamp: timestamp
  conversation_id: string
  case_id: string | null
  run_id: string | null
  event_type: string
  visibility: customer | developer | both
  summary: string
  payload: object
  evidence_refs: list[EvidenceRef]
```

`sequence` is monotonically increasing within a Conversation. `summary` and
`payload` are already visibility-filtered server projections, not raw internal
records that the browser is expected to hide. For `message_received`, the
customer-visible payload may contain `customer_text`, which is the validated,
PII-redacted projection needed to rebuild the chronological customer timeline;
it is never a raw request or provider payload.

### 6.2 Core event types

The v1 contract includes these semantic event families:

```text
message_received
message_rejected
triage_started
triage_completed
triage_failed
policy_decided
request_fragment_blocked
case_created
case_issue_revised
run_started
agent_turn_started
agent_turn_completed
tool_call_requested
tool_call_blocked
tool_call_cache_hit
tool_call_started
tool_call_completed
tool_call_failed
evidence_gate_evaluated
business_clarification_requested
customer_reply_created
action_recommended
proposal_created
proposal_confirmed
proposal_declined
proposal_superseded
proposal_expired
proposal_invalidated
action_submitted
action_verified
action_failed
action_uncertain
case_closed
run_succeeded
run_failed
```

Consumers must tolerate new event types and optional payload fields within the
same `schema_version`. Removing or redefining a published field requires a new
schema version and compatibility handling.

### 6.3 Visibility and redaction

Customer and Developer Trace events originate from the same structured facts but
pass through distinct server-side serializers and visibility policies.

Never send these to the browser in any event visibility:

- raw chain-of-thought or hidden reasoning tokens;
- system/developer prompts;
- API keys or provider credentials;
- raw provider request bodies;
- complete personal information;
- stack traces;
- hidden eval fault seeds; or
- unredacted tool-data prompt-injection text when a structured safe summary is
  sufficient.

Developer events may include decision summaries, masked tool arguments,
structured observations, EvidenceRefs, blocked-fragment categories, budgets,
and state transitions. They are a demo/debug projection and must be visibly
labeled as unavailable to real end customers.

## 7. Demo reset boundary

### `POST /v1/demo/reset`

Local developer-only endpoint. It accepts no body, returns `204`, and is available only in the
loopback G1 profile. The frontend requires an explicit confirmation before calling it.

The reset restores synthetic fixture data and removes demo
Conversation, Case, Run, Proposal, Action, Ticket, and Event records. It must not
modify `.env`, provider/model configuration, prompt or tool versions, stored eval
history, or source-controlled fixture definitions. It is not a production customer API.

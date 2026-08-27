# V3 Design and Data Contracts

## 1. Goals and requirements

| ID | Requirement | Acceptance link |
|---|---|---|
| GOAL-V3-01 | Make next-observation selection the only experimental Agent-versus-Workflow difference. | TEST-V3A-FAIR-01 through TEST-V3A-FAIR-06 |
| GOAL-V3-02 | Make every observation, recovery, evidence-progress transition, and termination reconstructible and gradable without chain-of-thought. | TEST-V3A-TRACE-01, TEST-V3A-TRACE-02, and TEST-V3A-REBUILD-01 through TEST-V3A-REBUILD-03 |
| GOAL-V3-03 | Test whether adaptive investigation has value while retaining PREFER_WORKFLOW as a valid conclusion. | EVAL-V3-DECISION-01 |
| GOAL-V3-04 | Preserve useful multi-turn clarification facts without building long-term memory. | TEST-V3B-FACT-01 through TEST-V3B-FACT-05 and TEST-V3B-QUESTION-01 through TEST-V3B-QUESTION-04 |

Normative V3-A requirements:

- REQ-V3A-001: both strategies must emit one typed NextObservationCandidate
  into one shared deterministic runtime.
- REQ-V3A-002: only the selector implementation may differ between Agent and
  Workflow.
- REQ-V3A-003: no model-authored candidate may directly execute a tool,
  declare evidence sufficient, select a recovery route, or decide a business
  outcome.
- REQ-V3A-004: Evidence Progress must be reproducible from fixed Case scope,
  ordered persisted ToolCall records, their typed result envelopes, and linked
  EvidenceRef records. LangGraph messages and checkpoints must not be required.
- REQ-V3A-005: a retryable failure may trigger one adjacent deterministic
  exact retry. Both actual executions consume read budget; the retry does not
  consume a selector planning turn.
- REQ-V3A-006: early-stop, premature-finish, stuck, and budget guards must run
  deterministically.
- REQ-V3A-007: minimum decision, recovery, and orchestration-state traces must
  be persisted before any later UI projection.
- REQ-V3A-008: trajectory acceptance must be observation-conditioned and
  deterministic, not an exact-sequence-only grader and not an authoritative
  LLM Judge.

Normative V3-B requirements:

- REQ-V3B-001: every accepted customer clarification claim must create an
  immutable CaseFactAssertion bound to one customer-authored source_message_id.
- REQ-V3B-002: the current CaseFactSnapshot must be derived from assertions;
  it must never be independently edited as source of truth.
- REQ-V3B-003: the model may emit only a constrained CaseFactCandidate. A
  deterministic validator and merger own acceptance, supersession, conflict,
  unknown, and repeated-question rules.
- REQ-V3B-004: Case facts must expire with the Case boundary and must never
  become a user profile, cross-Case memory, or vector-retrieval corpus.
- REQ-V3B-005: V3-B engineering must not start before the V3-A1 Engineering
  Gate passes.

## 2. Logical architecture

~~~text
Fixed authorized Case + customer message + CaseFactSnapshot
                          |
                  DecisionContext builder
                          |
             +------------+------------+
             |                         |
      Agent selector              Workflow selector
      (model judgment)           (deterministic rules)
             |                         |
             +----- NextObservationCandidate -----+
                                      |
                        deterministic Observation Validator
                                      |
                          typed NextObservation
                                      |
                       shared ExecuteObservation path
                 LangGraph orchestration + ToolNode boundary
                         + GovernedToolExecutor
                                      |
                    ToolCall + ToolResult + EvidenceRef
                                      |
                   deterministic EvidenceProgressReducer
                                      |
                    deterministic Observation Router
              +---------------+------------+--------------+
              |               |            |              |
          REPLAN       RETRY_EXACT      FINALIZE       SAFE_STOP
              |               |            |              |
           selector     same execution   Evidence Gate   Evidence Gate /
                         no selector       authority       human-safe path
~~~

The topology adds explicit orchestration boundaries, not new business
authorities. Observation Router may validate, retry, block, or decide that the
Evidence Gate can now be evaluated. It must not decide Proposal eligibility or
choose a different business tool on behalf of the selector.

## 3. V3-A module contracts

### 3.1 DecisionContext

DecisionContext is the only semantic input offered to either selector.

| Field | Type | Authority and rule |
|---|---|---|
| schema_version | literal v3a.decision_context.v1 | project contract |
| case_id, run_id | non-empty strings | trusted server context; never model-authored |
| canonical_issue_type | signed_not_received or stalled_tracking | deterministic Case authority |
| authorized_order_id | string | trusted server context |
| customer_message | bounded redacted text | untrusted data; same value available to both selectors |
| case_fact_snapshot | optional CaseFactSnapshot | deterministic V3-B derived state; absent in V3-A1 |
| evidence_progress | EvidenceProgressSnapshot | deterministic derived state |
| latest_observation | ObservationSummary or null | bounded typed projection, no raw policy prose |
| allowed_tools | ordered stable tool names | shared registry |
| remaining_budget | BudgetSnapshot | deterministic counter |
| prior_decision_fingerprints | bounded list | guard input, not model reasoning |
| prompt_policy_version | string or null | populated for Agent trace; no authority |

The Agent may render DecisionContext into a model prompt. The Workflow consumes
the same object directly. Neither receives hidden Fixture facts or a private
version of Evidence Progress.

### 3.2 NextObservationCandidate

The candidate is untrusted selector output.

~~~yaml
schema_version: v3a.next_observation_candidate.v1
action: call_tool | finish
tool_name: string | null
arguments:
  order_id: string
  issue_type: signed_not_received | stalled_tracking
addresses:
  - EVIDENCE_REQUIREMENT_CODE
reason_code: FIRST_REQUIRED_OBSERVATION
~~~

Rules:

- call_tool requires exactly one allowlisted tool name and only that tool's
  declared model-visible arguments;
- finish requires tool_name=null and arguments={};
- addresses contains unique requirement codes from the shared requirement
  registry;
- reason_code is a closed enum:
  FIRST_REQUIRED_OBSERVATION, MISSING_REQUIRED_EVIDENCE,
  OBSERVATION_CONDITIONAL_BRANCH, OPTIONAL_EXPLANATORY_CONTEXT,
  FINALIZATION_REQUESTED;
- free-form hidden reasoning is neither requested nor stored;
- an optional bounded decision_summary may be added for Developer Trace only
  if it is redacted, no longer than 160 characters, and never used by a guard,
  Gate, or grader.

### 3.3 Observation Validator and NextObservation

The Observation Validator is a pure deterministic boundary. It binds trusted
scope and returns either one typed NextObservation or one rejected decision
trace.

~~~yaml
schema_version: v3a.next_observation.v1
decision_id: immutable string
selector_kind: agent | workflow
case_id: trusted string
run_id: trusted string
planning_turn: integer
action: call_tool | finish
tool_name: allowlisted string | null
canonical_arguments: canonical JSON object
canonical_arguments_hash: sha256
addresses: unique evidence requirement codes
reason_code: closed enum
evidence_progress_revision: integer
evidence_progress_hash: sha256
decision_fingerprint: sha256
validated_at: timezone-aware timestamp
~~~

Validation order is fixed:

1. schema and extra-field rejection;
2. active Case/Run and current revision check;
3. action/field consistency;
4. tool registry and issue relevance;
5. trusted order and issue argument rebinding;
6. shared authorization precondition;
7. Evidence Progress applicability and duplicate-completed-read check;
8. pending exact-retry lock;
9. planning and read-budget check;
10. finish-readiness check;
11. normalized fingerprint and trace creation.

A rejected call consumes the already-requested selector planning turn but no
read-tool execution. It cannot reach ToolNode.

### 3.4 Shared ExecuteObservation

ExecuteObservation is one shared production path for both strategies. Its
logical responsibilities are:

- accept only validated NextObservation;
- convert the call into the existing public LangChain/LangGraph tool-call
  shape required by ToolNode;
- execute through the same ToolNode and GovernedToolExecutor;
- reauthorize the order at the execution boundary;
- apply the same cache, fault seed, source revision, timeout, redaction, and
  read budget;
- persist ToolCall before recording completion;
- persist the typed result envelope, result hash, source version, and
  EvidenceRef links;
- emit no write and make no Evidence Gate decision.

The later implementation may choose the smallest public-API-compatible
LangGraph mapping. It may not create a second Workflow-only executor or bypass
ToolNode/governance to make the baseline faster.

### 3.5 Evidence Progress

Evidence Progress is derived observation state, not a new authority and not
Agent memory.

Evidence requirement codes:

| Code | Meaning | Typical source |
|---|---|---|
| ORDER_STATUS | trusted current order state | get_order_context |
| TRACKING_TIMELINE | logistics history and SLA facts | get_logistics_timeline |
| DELIVERY_PROOF | signed-delivery proof facts | get_delivery_proof |
| POLICY_APPLICABILITY | Resolver-validated policy facts | search_after_sales_policy |
| ACTIVE_TICKET_STATUS | duplicate-ticket state | get_existing_logistics_tickets |
| CARRIER_ALERT_CONTEXT | optional explanatory carrier context | get_carrier_service_alerts |

Case clarification facts are deliberately not Evidence Progress entries.
V3-B exposes them through a separate CaseFactSnapshot with message provenance.

~~~yaml
schema_version: v3a.evidence_progress.v1
case_id: string
run_id: string
canonical_issue_type: string
revision: monotonic integer
requirements:
  ORDER_STATUS:
    applicability: required | optional | not_required
    status: missing | satisfied_present | satisfied_absent |
            retry_pending | unavailable_final | conflict
    supporting_tool_call_ids: [string]
    evidence_ref_ids: [stable reference identity]
    result_hashes: [sha256]
    source_versions: [string]
gate_readiness: not_evaluable | evaluable
missing_required_codes: [code]
terminal_trigger_codes: [closed reason-code enum]
last_actual_tool_call_id: string | null
snapshot_hash: sha256
rebuilt_at: timezone-aware timestamp
~~~

Reducer rules:

- the fixed seed is Case ID, Run ID, canonical issue, and the shared evidence
  requirement registry;
- every dynamic status transition must be derivable from ordered ToolCall
  rows, their typed result envelopes, source versions, and matching
  EvidenceRef records;
- graph messages, model text, prompt text, event summaries, and checkpointer
  state must not be needed to reproduce snapshot_hash;
- a successful absent result may satisfy a requirement; unavailable never
  does;
- a retryable unavailable result becomes retry_pending only while a valid
  RetryDirective exists; after the only retry fails it becomes
  unavailable_final;
- mismatched result hashes, missing references for successful results,
  contradictory successful results at the same authoritative revision, or
  malformed envelopes become conflict and fail closed;
- the requirement registry used by the Reducer and the Evidence Gate must be
  one shared versioned contract. Separate handwritten recipes are prohibited;
- storing a snapshot for trace convenience is allowed, but persisted
  ToolCall/EvidenceRef history remains the rebuild authority.

### 3.6 Deterministic exact retry

~~~yaml
schema_version: v3a.retry_directive.v1
retry_of_tool_call_id: string
tool_name: string
canonical_arguments: canonical JSON object
canonical_arguments_hash: sha256
source_version: string
next_attempt_number: 2
reason_code: RETRYABLE_TOOL_FAILURE
issued_from_progress_hash: sha256
~~~

Exact retry invariants:

- it is issued only for execution_status=retryable_error,
  evidence_availability=unavailable, retryable=true, and attempt 1;
- it must be the next actual read-tool execution in that Run;
- no selector invocation or different tool execution may occur between the
  failed call and retry;
- trace writes, progress reconstruction, and Router execution may occur between
  the two attempts and do not violate adjacency;
- tool name, canonical arguments bytes, trusted order/issue scope, and source
  version must match exactly;
- both attempts consume actual-read budget; the deterministic retry consumes
  zero selector planning turns;
- retryable failures are never cached as reusable evidence;
- a second failure is retry-exhausted and becomes unavailable_final;
- if source version changes before retry, the runtime must not execute the
  stale directive. It routes SAFE_STOP with
  SOURCE_REVISION_CHANGED_DURING_RETRY.

### 3.7 Observation Router

~~~yaml
schema_version: v3a.recovery_decision.v1
recovery_id: string
case_id: string
run_id: string
route: replan | retry_exact | finalize | safe_stop
reason_code: closed enum
trigger_tool_call_id: string | null
trigger_result_hash: sha256 | null
evidence_progress_before_hash: sha256
evidence_progress_after_hash: sha256
retry_directive: RetryDirective | null
budget_snapshot: BudgetSnapshot
decided_at: timezone-aware timestamp
~~~

Route precedence is fixed:

1. malformed/integrity conflict → safe_stop;
2. exhausted hard budget → safe_stop;
3. valid pending retry → retry_exact;
4. Evidence Progress gate_readiness=evaluable → finalize;
5. otherwise → replan.

The Router cannot choose a new tool, change Case issue, synthesize evidence,
merge Case facts, or return an Evidence Gate business decision.

### 3.8 Guards

Early-stop guard:

- runs after every completed ToolCall and before every selector call;
- when gate_readiness=evaluable, it prevents additional selector/tool work and
  routes finalize;
- Evidence Gate remains the only component that decides no-action,
  clarification, Proposal eligibility, retry-later, or human support.

Premature-finish guard:

- finish at not_evaluable progress is rejected as PREMATURE_FINISH;
- it records missing_required_codes and may allow one correction under OD-01;
- the rejected selector turn counts toward planning budget;
- it never silently fills missing evidence or calls a default recipe.

Stuck guard:

- decision_fingerprint is the hash of action, tool name, canonical arguments,
  addresses, and current progress hash;
- repeating the same rejected fingerprint at unchanged progress triggers
  STUCK_REPEATED_DECISION under OD-01;
- two consecutive selector turns with no Evidence Progress change, excluding
  the automatic exact retry, trigger STUCK_NO_EVIDENCE_PROGRESS;
- a stuck route is safe_stop, never an unbounded replan.

Budget guard:

- checks the existing 8 planning turns per Run, 16 planning turns and 6 actual
  reads per Case;
- a blocked tool call consumes planning but not read execution;
- no model call occurs after exhaustion;
- budget exhaustion preserves acquired evidence and routes the deterministic
  safe outcome.

### 3.9 Minimal decision/recovery/state trace

V3-A P0 requires data contracts and persistence; a new UI is deferred to P1.

DecisionTraceRecord must include:

- schema version, trace sequence, case_id, run_id, decision_id;
- selector_kind and planning turn;
- normalized action/tool/argument hash/addresses/reason code;
- candidate validation status and rejection code;
- Evidence Progress revision/hash before the decision;
- budget snapshot;
- Agent-only model and prompt version identifiers when applicable;
- no raw chain-of-thought, system prompt, provider payload, secret, PII, or
  fault seed.

RecoveryTraceRecord must include:

- recovery_id, triggering ToolCall/result hash;
- execution status, evidence availability, retryable flag, and safe error code;
- Evidence Progress hashes before/after;
- route, reason code, retry identity hash, and attempt number;
- budget snapshot.

StateTraceRecord must include:

- orchestration phase from/to:
  select, validate, execute, reduce, route, finalize, safe_stop, terminal;
- transition reason code, monotonic trace sequence, Case/Run revision;
- Evidence Progress hash and pending-retry identity;
- persisted-at timestamp.

Canonical trace records must be durable before any SSE delivery. Replay may
redeliver them but must not re-execute selection, tools, retry, Gate, Proposal,
or Action.

### 3.10 LangGraph state boundary

LangGraph may cache only active orchestration state:

- messages required by the public model/tool APIs;
- current phase;
- last NextObservation identity;
- last ToolCall/result identity;
- Evidence Progress snapshot/hash;
- pending RetryDirective;
- guard counters/fingerprints;
- planning/read BudgetSnapshot;
- last RecoveryDecision identity;
- trace sequence.

LangGraph must not become authority for:

- Conversation, Case, CaseOutcome, Run, Proposal, Action, or Ticket state;
- authorization or trusted identity;
- canonical ToolCall/EvidenceRef history;
- CaseFactAssertion history;
- policy facts or Evidence Gate decisions;
- confirmation, idempotency, transaction, or read-back state.

After restart, orchestration state must be checked against and rebuildable from
the business database. A checkpoint disagreement loses to canonical persisted
domain/tool/fact state and creates a deterministic recovery trace.

## 4. V3-B Case Fact contracts

V3-B is a preregistered design envelope. Its engineering remains blocked until
the V3-A1 Engineering Gate passes and OD-02 is confirmed.

### 4.1 Fact scope

Recommended whitelist:

| Fact code | Value | Meaning |
|---|---|---|
| customer_still_reports_missing | true, false, unknown | customer says the parcel is still missing |
| front_desk_checked | true, false, unknown | customer says the front desk/reception was checked |
| neighbor_checked | true, false, unknown | customer says neighbors were checked |
| household_checked | true, false, unknown | customer says household/family recipients were checked |

all_reception_locations_checked is a derived boolean that is true only when
all three location facts are known true. It is not stored as an assertion.

Prohibited facts include identity, authorization, ownership, address,
telephone, order/carrier state, policy facts, eligibility, Proposal/Action
state, preferences, profile attributes, risk scores, and any cross-Case fact.

### 4.2 CaseFactCandidate

The model may return zero to four candidates from only the current
customer-authored message.

~~~yaml
schema_version: v3b.fact_candidate.v1
fact_code: whitelisted code
value: true | false | unknown
relation_hint: new | repeat | correction | withdrawal
target_assertion_id: string | null
source_span:
  start: integer
  end: integer
~~~

The model does not supply case_id, customer_id, source_message_id, timestamps,
snapshot revision, authority, or merge result. The server binds them.

### 4.3 Candidate validation

A deterministic validator accepts a candidate only when:

- the Case is active and awaiting a business clarification reply;
- source_message_id belongs to the same Conversation/Case, is customer-authored,
  and is the current unconsumed reply;
- fact_code is whitelisted and relevant to the outstanding question;
- value and relation_hint are closed-enum values;
- the source span is in bounds and exactly matches the persisted message;
- a correction/withdrawal target is an active assertion for the same fact;
- a correction or withdrawal contains a deterministic allowlisted correction
  cue in the bound span; otherwise opposite claims become conflict;
- the candidate contains no extra fields or prohibited content.

Rejected candidates create a safe FactMergeDecision trace and no assertion.
The application then treats the fact as unknown; it does not ask the model to
invent a substitute.

### 4.4 CaseFactAssertion

~~~yaml
schema_version: v3b.case_fact_assertion.v1
assertion_id: immutable string
case_id: string
fact_code: whitelisted code
value: true | false | unknown
source_message_id: immutable string
source_message_hash: sha256
source_span_start: integer
source_span_end: integer
relation: new | repeat | correction | withdrawal
supersedes_assertion_id: string | null
extractor_kind: model_candidate | deterministic
extractor_version: string
assertion_sequence: monotonic integer
recorded_at: timezone-aware timestamp
~~~

Assertions are append-only. A correction appends a new assertion that points
to the prior assertion; it never updates or deletes the prior row. Duplicate
same-value claims may append for provenance, but the snapshot collapses them
to one current value with multiple source IDs.

### 4.5 Supersession, conflict, and unknown

Supersession:

- a later same-value assertion is repeat, not supersession;
- a later opposite value with a validated correction cue may supersede exactly
  one active assertion of the same fact;
- withdrawal may supersede one active assertion and makes the current value
  unknown;
- supersession never crosses Case or fact code and never erases history.

Conflict:

- opposite active known values without a validated correction relationship
  produce conflict;
- multiple competing corrections or a missing/invalid target produce conflict;
- conflict is not resolved by latest timestamp, confidence, model preference,
  or an LLM Judge;
- a conflicting fact cannot satisfy Evidence Gate clarification requirements.

Unknown:

- unknown is a first-class value, not false and not absence;
- unknown never satisfies a required clarification fact;
- an unknown repeat preserves unknown;
- unknown does not silently overwrite a known value unless it is a validated
  withdrawal; otherwise it remains non-resolving provenance.

### 4.6 CaseFactSnapshot

~~~yaml
schema_version: v3b.case_fact_snapshot.v1
case_id: string
revision: monotonic integer
facts:
  customer_still_reports_missing:
    status: known_true | known_false | unknown | conflict
    active_assertion_ids: [string]
    superseded_assertion_ids: [string]
    source_message_ids: [string]
question_state:
  customer_still_reports_missing:
    asks: integer
    status: unanswered | answered | unknown_exhausted |
            conflict_requires_clarification | conflict_exhausted
derived:
  all_reception_locations_checked: true | false | unknown
snapshot_hash: sha256
rebuilt_at: timezone-aware timestamp
~~~

The snapshot is rebuilt in assertion_sequence order. It is current derived
state, not an editable record. If its stored hash disagrees with a rebuild,
the rebuild wins and the investigation fails closed pending repair.

### 4.7 Repeat-question rules

- a known non-conflicting fact must not be asked again in the same Case;
- an unknown answer may receive no identical repeat question. The system uses
  unknown and follows the Gate/safe path;
- a conflict may trigger one targeted disambiguation question naming only the
  conflicting fact, subject to the existing global maximum of two business
  clarifications;
- if that question returns unknown, remains contradictory, or cannot be
  validated, the conflict is exhausted and routes human support;
- a question already emitted must have a stable question_id so replay does not
  increment the count or ask it again;
- Case closure makes all outstanding question work terminal.

### 4.8 Fact and evidence boundary

CaseFactAssertion source_message_id provenance is distinct from EvidenceRef
tool provenance. The Evidence Gate may consume both typed inputs, but must
retain their separate source classes. When a Case fact is material to a
Proposal, the Proposal evidence snapshot must include the CaseFactSnapshot
hash and active assertion IDs in addition to critical Tool result hashes.

No Case fact is written to the Policy corpus, vector index, Tool cache,
cross-Case context, or user profile.

## 5. Authority matrix

| Concern | Authority |
|---|---|
| Free-text triage fields already allowed by V2 | bounded model output plus deterministic normalization |
| Next Observation candidate | Agent or strong Workflow selector |
| Candidate schema, scope, relevance, and budget | deterministic Observation Validator |
| Tool dispatch and read execution | shared ToolNode plus GovernedToolExecutor |
| Exact retry | deterministic Observation Router/runtime |
| Evidence Progress | deterministic Reducer over ToolCall/EvidenceRef history |
| Replan/finalize/safe-stop route | deterministic Observation Router |
| Evidence sufficiency and business outcome | deterministic Evidence Gate |
| Fact candidate | bounded model extraction or deterministic parser |
| Fact acceptance, merge, supersession, conflict, unknown | deterministic validator/merger |
| Current Case facts | rebuilt CaseFactSnapshot |
| Case/Run/Proposal/Action/Ticket state | existing project-owned domain and repositories |
| Customer confirmation and write | existing exact UI/API confirmation plus deterministic executor |
| Architecture conclusion | preregistered deterministic Eval rules and Owner gate |

## 6. Implementation authority classifications

| Decision | Class | Owner or envelope |
|---|---|---|
| LangGraph/LangChain public Agent loop and ToolNode | fixed constraint | repository contract |
| Same downstream runtime for Agent and Workflow | required invariant | this V3 contract |
| NextObservation field names and enums | required invariant | schema may only evolve by explicit version |
| Internal Python file/class layout | implementation-delegated | simplest layout passing contracts |
| Database table/index names | implementation-delegated | append-only/rebuild behavior is fixed |
| UI visualization | implementation-delegated P1 | no V3-A1 dependency |
| Retrieval expansion, MCP, multi-agent, long-term memory | prohibited | Owner request |
| Authoritative LLM Judge | prohibited | Owner request |
| Guard threshold | blocking for V3-A1 | OD-01 |
| Exact V3-B whitelist | blocking for V3-B1 only | OD-02 |
| Locked advantage/resource thresholds | blocking for V3 Freeze only | OD-03 |

## 7. Representative end-to-end recovery example

Given a signed_not_received Case:

1. both selectors receive the same empty Evidence Progress and choose
   get_order_context;
2. Validator binds the authorized order; shared execution persists ToolCall
   and a delivered result;
3. Reducer marks ORDER_STATUS=satisfied_present;
4. selector chooses get_delivery_proof;
5. the first result is retryable_error plus unavailable;
6. Reducer marks DELIVERY_PROOF=retry_pending;
7. Router emits RETRY_EXACT with the same canonical arguments and source
   version; no selector turn occurs;
8. the second execution returns success plus absent;
9. Reducer marks DELIVERY_PROOF=satisfied_absent and records both attempts;
10. Router chooses replan or finalize strictly from shared readiness;
11. later required observations make gate_readiness=evaluable;
12. Router finalizes; Evidence Gate independently decides the business
    outcome;
13. decision, recovery, progress, Gate, Proposal, confirmation, Action, and
    read-back records remain separately attributable.

If the second delivery-proof call fails, unavailable_final reaches Evidence
Gate. It can never be interpreted as absent and can never produce a Proposal.

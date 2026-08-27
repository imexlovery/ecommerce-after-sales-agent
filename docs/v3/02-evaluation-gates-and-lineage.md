# V3 Evaluation, Gates, and Evidence Lineage

## 1. Evaluation claim

V3 evaluation asks whether the selector responds correctly to observations.
It does not reward graph node count, tool-call diversity by itself, or Agent
novelty.

The non-AI baseline remains a strong conditional Workflow. PREFER_WORKFLOW is
a first-class possible result. ADOPT_AGENT is not the target label.

No formal Eval is executed in V3-D0. This document preregisters the future
contracts only.

## 2. Agent versus Workflow fairness

### 2.1 The only permitted difference

Agent and Workflow may differ only in the implementation of:

> select_next_observation(DecisionContext) → NextObservationCandidate

Everything before DecisionContext construction and everything after candidate
emission must be the same production component, configuration, and version.

### 2.2 Shared items

Both sides must share:

- canonical Case input, trusted identity, authorized order, and issue;
- the same customer message and CaseFactSnapshot;
- DecisionContext schema and Evidence Progress snapshot;
- allowlisted tools, ToolNode path, GovernedToolExecutor, authorization, and
  typed ToolResult contracts;
- six actual read executions per Case, 16 planning turns per Case, and 8
  planning turns per Run;
- cache keys/invalidation and source revisions;
- Fixture version, evaluated_at, and fault seed;
- deterministic exact retry and retry exhaustion;
- Observation Validator, EvidenceProgressReducer, Observation Router, all
  guards, and trace writer;
- Evidence Gate, Proposal, response layer, exact confirmation, idempotent
  executor, read-back, and event replay;
- dataset partition, scenario manifest, graders, repeat count, timeout, and
  report generation;
- failure inclusion, redaction, version projection, and release rules.

### 2.3 Forbidden asymmetries

Paired evidence is invalid if:

- Agent gets hidden Fixture facts, more context, more tools, more retries, or
  higher budgets;
- Workflow bypasses the shared Validator/Router/ToolNode path;
- a deterministic selector reads data not present in DecisionContext;
- Agent and Workflow see different source revisions or cache state;
- one architecture's timeout, provider failure, schema error, or failed run is
  omitted;
- graders require an exact Workflow sequence but allow any Agent sequence, or
  vice versa;
- a baseline is weakened after observing Agent behavior;
- an architecture result changes the Manifest or threshold used to score it.

### 2.4 Pair identity

Each paired sample must bind:

~~~yaml
pair_id: immutable string
scenario_id: immutable string
dataset_revision: immutable string
fixture_version: immutable string
fault_seed_hash: sha256
evaluated_at: timezone-aware timestamp
source_revision: 40-character git revision
agent_selector_version: string
workflow_selector_version: string
validator_version: string
router_version: string
evidence_progress_version: string
evidence_gate_version: string
grader_registry_version: string
~~~

Fault seed values remain hidden from browser events. Eval artifacts may retain
the controlled identity required for reproducibility under the existing
sanitization rules.

## 3. Observation-conditioned trajectory contract

An exact total tool sequence is too brittle and a required-tool set is too
weak. Each scenario therefore declares conditional obligations over the
observed trace.

~~~yaml
trajectory_contract_version: v3a.trajectory.v1
initial_allowed_observations:
  - get_order_context
obligations:
  - id: OBL-EXAMPLE-01
    when:
      observation:
        tool_name: get_order_context
        field_path: payload.order_status
        operator: not_equals
        value: delivered
    then:
      allowed_routes: [finalize]
      forbidden_future_tools:
        - get_delivery_proof
        - search_after_sales_policy
      max_additional_actual_reads: 0
  - id: OBL-EXAMPLE-02
    when:
      observation:
        tool_name: get_delivery_proof
        execution_status: retryable_error
        evidence_availability: unavailable
    then:
      required_next_route: retry_exact
      exact_retry: true
      max_retry_attempts: 1
terminal:
  allowed_gate_decisions: [complete_no_action, request_business_clarification]
~~~

Rules:

- predicates use typed result fields, Evidence Progress states, and trace
  records, never model prose;
- allowed alternatives and partial-order constraints are permitted;
- obligations may cap additional reads or forbid irrelevant tools;
- every triggered obligation must be evaluated;
- untriggered branches do not count as passes;
- unknown fields or malformed paths fail closed at Manifest validation;
- the same contract grades Agent and Workflow.

## 4. Deterministic grader set

| Grader ID | Pass condition |
|---|---|
| GR-V3A-01 next_observation_contract | every candidate and normalized decision obeys its schema |
| GR-V3A-02 observation_conditioned_choice | every triggered obligation accepts the next decision/route |
| GR-V3A-03 early_stop | no actual read or selector turn occurs after deterministic gate readiness |
| GR-V3A-04 premature_finish_guard | premature finish is rejected and follows OD-01 |
| GR-V3A-05 stuck_guard | repeated unchanged decisions terminate at the registered bound |
| GR-V3A-06 exact_retry | retry is adjacent, attempt 2, and byte-identical in canonical identity |
| GR-V3A-07 retry_budget | both actual attempts consume reads; automatic retry consumes no selector turn |
| GR-V3A-08 evidence_rebuild | replayed ToolCall/EvidenceRef history reproduces every progress hash |
| GR-V3A-09 evidence_availability | unavailable never satisfies absent/present requirements |
| GR-V3A-10 outcome | final deterministic Gate outcome is in the scenario's allowed set |
| GR-V3A-11 unnecessary_reads | forbidden or post-terminal actual reads equal zero |
| GR-V3A-12 trace_completeness | every decision, ToolCall, reduction, route, and state transition correlates |
| GR-V3A-13 hard_safety | authorization, confirmation, evidence, and write invariants all pass |
| GR-V3B-01 fact_provenance | every active fact resolves to a valid customer source_message_id |
| GR-V3B-02 fact_merge | supersession/conflict/unknown snapshot matches append-only assertions |
| GR-V3B-03 repeat_question | known facts are not re-asked; conflict/unknown bounds are honored |

All listed graders are deterministic and authoritative. A human or LLM may
produce a non-authoritative Development note for readability, but it cannot
alter a score, label, Gate, or architecture conclusion.

## 5. Development Eval design

Development data is mutable only by creating a new explicit revision. All
runs, including failures, are retained inside their revision. Development
results are measurement-only and cannot become release evidence.

Reserved identities:

- V3A1-ENGINEERING-DEV-001 — future engineering candidate identity;
- V3A-EVAL-DEV-001 — first paired Development dataset/report lineage;
- V3B-ENGINEERING-DEV-001 — first Case Fact engineering candidate;
- V3B-EVAL-DEV-001 — first multi-turn fact Development lineage.

### 5.1 Required V3-A scenario families

The Development Manifest must contain paired members that hold the customer
message/issue constant while changing observations:

| Family | Observation variation | Required behavior |
|---|---|---|
| DEV-V3A-SNR-ORDER | reported signed-not-received but order not delivered | finalize for issue revision; no POD/policy/ticket reads |
| DEV-V3A-SNR-TICKET | active ticket exists | early finalize; no duplicate Proposal and no later unnecessary reads |
| DEV-V3A-SNR-POLICY | policy ineligible or unavailable | no Proposal; distinct no-action versus safe-stop semantics |
| DEV-V3A-SNR-POD | reception proof, absent proof, or present non-reception proof | clarification, continuation, or Proposal path as Gate allows |
| DEV-V3A-SNR-RETRY | first POD call transiently unavailable | one exact adjacent retry and recovery |
| DEV-V3A-SNR-FAIL | POD remains unavailable | unavailable_final and human-safe path |
| DEV-V3A-STALL-SLA | within SLA versus severe stall | early no-action versus continued investigation |
| DEV-V3A-STALL-TICKET | existing ticket versus none | duplicate prevention with fewer reads |
| DEV-V3A-STALL-POLICY | applicable, no-hit, conflict, or unavailable policy | deterministic Resolver/Gate semantics; no Query Rewrite |
| DEV-V3A-GUARDS | malformed, irrelevant, duplicate, premature, stuck, budget, source-revision change | deterministic rejection/recovery without extra authority |

CARRIER_ALERT_CONTEXT remains optional explanatory evidence. Its use cannot
compensate for missing critical evidence or create an Agent-only advantage.

### 5.2 Required V3-B scenario families

These may run only after V3-B engineering is separately authorized:

- one delivery proof reports a concrete location, the customer confirms it was
  checked, and that location-bound fact must not be re-asked;
- one unknown answer remains unknown and is not converted to false;
- one same-value repeat preserves provenance without changing current value;
- one explicit correction supersedes the named prior assertion;
- one opposite claim without correction cue creates conflict;
- one conflict gets at most one targeted disambiguation within the global
  two-question budget;
- one replayed message/question is deduplicated;
- one cross-Case or non-customer source_message_id is rejected.

### 5.3 Development measurements

Report separately by architecture and scenario family:

- final outcome and hard safety;
- triggered trajectory obligations passed/failed;
- actual read executions and cache hits;
- unnecessary actual reads;
- early-stop and premature-finish behavior;
- retry correctness and recovery result;
- stuck/safe-stop count;
- Evidence Progress reconstruction parity;
- clarification question count and repeat-question rate after V3-B;
- latency, model calls, tokens, and provider/schema errors;
- cost only when a trustworthy price basis exists; otherwise unavailable.

Development does not select ADOPT_AGENT. It supplies the evidence needed for
OD-03 and a possible V3 Freeze.

## 6. V3-A1 Engineering Gate

V3-A1 is GO only if one clean committed source candidate passes all of:

1. static schema validation for NextObservation, Evidence Progress, retry,
   recovery, and trace contracts;
2. contract tests proving extra fields, invalid enums, untrusted IDs, wrong
   arguments, irrelevant tools, and premature finish fail closed;
3. integration tests proving Agent and Workflow enter the same Validator,
   ToolNode/Governed executor, Reducer, Router, Gate, and trace writer;
4. successful and failed offline replay showing identical Evidence Progress
   hashes before/after restart without model or tool re-execution;
5. exact-retry tests for adjacency, identical arguments/source version,
   budget accounting, no selector turn, and exhaustion;
6. early-stop, premature-finish, stuck, budget, and stale-source guards;
7. prompt inspection proving the Agent receives goals, evidence
   requirements, tool constraints, and safety rules rather than a fixed tool
   recipe;
8. trace redaction and correlation tests with no chain-of-thought, raw system
   prompt, provider payload, secret, PII, stack trace, or fault seed;
9. all existing V2 unit/contract/integration regressions pass under their
   original labels;
10. a protected-artifact hash inspection proves no V2 Freeze, raw report,
    Release Evidence, Evidence Pack, or historical failure changed;
11. no V3-B table, merger, long-term memory, UI expansion, or formal Eval is
    included in V3-A1.

Allowed evidence labels at this Gate are static, contract, integration, and
mock. It is not a Live or Locked gate.

Any failed item is NO-GO for V3-B engineering and Development Eval.

## 7. V3-B Engineering Gate

After V3-A1, V3-B is GO only if:

- assertion storage is append-only and every row binds a valid customer
  source_message_id;
- CaseFactSnapshot rebuild is deterministic and hash-stable;
- correction, withdrawal, repeat, conflict, and unknown rules match the
  contract;
- known facts are not re-asked and question replay is idempotent;
- conflict/unknown never satisfy a Gate requirement;
- Proposal revalidation includes material CaseFactSnapshot identity;
- facts do not cross Case boundaries and no long-term/vector memory path
  exists;
- both selectors receive the same snapshot;
- V3-A and all V2 regression gates remain green.

No formal Eval is implied by this Gate.

## 8. Development Go/No-Go before Freeze

GO to an Owner Freeze decision requires:

- all applicable engineering hard checks pass;
- every Development run is retained and schema-valid;
- hard safety violations equal zero;
- all triggered deterministic trajectory, retry, guard, trace, and rebuild
  obligations pass;
- both architectures produce an allowed deterministic outcome for every
  paired case;
- the required scenario families demonstrate genuinely different observation
  branches rather than prompt-encoded fixed sequences;
- the comparison report exposes failures, reads, latency, tokens, and
  unavailable cost honestly;
- OD-03 thresholds are proposed from the complete Development distribution,
  not selected runs.

NO-GO applies if safety fails, the baseline is asymmetric, the trajectory
contract changes after seeing results, a source/Fixture/fault identity differs
within a pair, or V2 evidence is mutated.

GO means only that a new V3 Freeze may be considered. It is not ADOPT_AGENT and
not release readiness.

## 9. Locked Eval design

Locked execution requires a new clean source revision and a new V3-only
immutable Freeze. Each locked paired scenario runs three times unless the
Owner preregisters a different stability rule before Freeze. Every timeout,
provider error, schema failure, and grader failure remains in the denominator.

Reserved identities:

- V3A-EVAL-FREEZE-001;
- V3A-EVAL-LOCKED-001;
- V3B-EVAL-FREEZE-001 and V3B-EVAL-LOCKED-001 only if V3-B is included.

These names are reserved contracts, not evidence that the artifacts exist.

Locked hard gates:

- safety_gate_pass=true;
- 3/3 stable pass for every required scenario/architecture;
- 100% triggered trajectory obligations;
- 100% exact-retry and guard obligations;
- 100% Evidence Progress and CaseFactSnapshot reconstruction parity;
- zero forbidden/post-terminal actual reads;
- zero Proposal/Action with unavailable, conflict, or unvalidated required
  facts;
- exact source/Manifest/Fixture/fault/grader/version binding;
- all raw runs retained and report generated only by trusted scripts.

## 10. Architecture conclusion

The Locked report may emit exactly one:

### ADOPT_AGENT

Permitted only when:

- Agent is not worse on any hard safety, correctness, stability, or
  deterministic trajectory Gate;
- Agent achieves the preregistered minimum dynamic-path advantage across the
  scenario families frozen under OD-03;
- Agent remains inside the preregistered reads, latency, token, and available
  cost ceilings;
- the advantage comes only from selector choices, not asymmetric runtime or
  data.

### KEEP_EXPERIMENTAL

Use when hard gates pass and at least one local adaptive advantage exists, but
coverage, stability, or resource evidence is insufficient for preference.

### PREFER_WORKFLOW

Use when the Workflow matches task/safety outcomes and the Agent has no
preregistered stable path advantage, exceeds resource ceilings, is less stable,
or introduces avoidable failures. This remains a successful engineering
conclusion, not a project failure.

Any hard safety or evidence-integrity failure also makes acceptance false,
regardless of the architecture label.

## 11. Version and evidence isolation

### 11.1 Protected V2 lineage

The following are immutable:

- evaluated source F-final
  9a947e78b60adf6151b397a678105896b8115aa1;
- acceptance-live-phase2-policy-rag-20260825-r3 Freeze and manifests;
- all V2 raw Eval records and reports;
- delivery/framework-integration-report.json;
- delivery/test-execution-report.json;
- delivery/release-evidence.json;
- the final sanitized Evidence Pack;
- every archived failed revision, including the F2 operational failure.

No V3 script may overwrite, rescore, relabel, or use the same artifact identity.

### 11.2 V3 storage boundary

Future implementation should use separate versioned roots:

- source-controlled contracts/manifests under a V3-specific eval path;
- generated Development records under var/v3/development;
- generated Locked records under var/v3/locked;
- future delivery evidence under a V3-specific package identity.

Exact paths are implementation-delegated, but path collision with V2 is
prohibited and must be mechanically tested.

### 11.3 Evidence states

- design: this package only; no runtime claim;
- Development: mutable only through a new explicit revision; measurement only;
- Frozen: immutable preregistered V3 contract bound to one clean source;
- Locked: every run retained; no tuning or relabeling;
- Release: possible only after separate Owner authorization and trusted
  generation.

V3 design, engineering, or Development evidence cannot upgrade, downgrade, or
replace the V2 Release Evidence claim.

## 12. Current pause

This document defines future checks but executes none of them. Owner Review is
complete and V3A1-ENGINEERING-DEV-001 has been separately authorized. The
current construction task must stop at the V3-A1 Engineering Gate; no
Development or Locked Eval is authorized.

## 13. Acceptance test IDs and traceability

The IDs below name future acceptance checks; they are contracts, not executed
results.

| Test ID | Observable contract |
|---|---|
| TEST-V3A-FAIR-01 | Agent and Workflow receive byte-equivalent DecisionContext payloads for each pair. |
| TEST-V3A-FAIR-02 | Both strategies call the same Validator, ToolNode/governed executor, Reducer, Router, Gate, and trace writer versions. |
| TEST-V3A-FAIR-03 | Budgets, Fixture, evaluated_at, cache state, source revisions, and fault identity match within each pair. |
| TEST-V3A-FAIR-04 | Pair identity and every shared version field are present and equal. |
| TEST-V3A-FAIR-05 | Neither selector reads Fixture/backend data outside DecisionContext. |
| TEST-V3A-FAIR-06 | Every planned run and failure appears once in raw records and aggregate statistics. |
| TEST-V3A-TRACE-01 | Every selector decision has one validated or rejected DecisionTraceRecord with no prohibited content. |
| TEST-V3A-TRACE-02 | Every tool completion has correlated reduction, recovery, and orchestration-state transitions persisted before projection. |
| TEST-V3A-REBUILD-01 | A successful run rebuilds every Evidence Progress revision and hash from ToolCall/EvidenceRef history. |
| TEST-V3A-REBUILD-02 | A transient then exhausted failure rebuilds retry_pending and unavailable_final identically. |
| TEST-V3A-REBUILD-03 | Restart/checkpoint disagreement resolves from canonical persisted history without duplicate model/tool work. |
| TEST-V3A-RETRY-01 | Retry attempt 2 is the adjacent next actual execution with identical canonical identity and no selector turn. |
| TEST-V3A-GUARD-01 | Gate-ready progress prevents later selector or read work. |
| TEST-V3A-GUARD-02 | Premature finish and unchanged repeated decisions follow OD-01 and terminate safely. |
| TEST-V3B-FACT-01 | Every accepted assertion binds a valid same-Case customer source_message_id and exact source span; the delivery-location fact also binds the current delivery-proof ToolCall/result hash. |
| TEST-V3B-FACT-02 | Assertions are append-only; correction/withdrawal never mutates prior history. |
| TEST-V3B-FACT-03 | Snapshot rebuild matches stored revision/hash and active/superseded assertion sets. |
| TEST-V3B-FACT-04 | Opposite uncorrected claims become conflict; unknown remains distinct from false. |
| TEST-V3B-FACT-05 | Facts do not cross Case boundaries or enter profile/vector/long-term memory paths. |
| TEST-V3B-QUESTION-01 | A known non-conflicting fact is not asked again. |
| TEST-V3B-QUESTION-02 | Unknown is not re-asked identically and does not satisfy the Gate. |
| TEST-V3B-QUESTION-03 | Conflict receives at most one targeted disambiguation inside the global two-question budget. |
| TEST-V3B-QUESTION-04 | Replayed question/message identities do not increment counts or create duplicate assertions. |
| EVAL-V3-DECISION-01 | Trusted Locked rules emit exactly ADOPT_AGENT, KEEP_EXPERIMENTAL, or PREFER_WORKFLOW without post-result threshold changes. |

| Goal | Scenario family | Requirements | Components | Acceptance | Evidence signal |
|---|---|---|---|---|---|
| GOAL-V3-01 | all paired V3-A families | REQ-V3A-001 through REQ-V3A-003 | selectors, Validator, shared runtime | TEST-V3A-FAIR-01 through TEST-V3A-FAIR-06 | pair/version equality and complete raw-run count |
| GOAL-V3-02 | retry, guard, restart, malformed trace | REQ-V3A-004 through REQ-V3A-008 | Reducer, Router, guards, trace writer | TEST-V3A-TRACE-01/02, TEST-V3A-REBUILD-01/02/03, TEST-V3A-RETRY-01, TEST-V3A-GUARD-01/02 | hash parity, exact adjacency, zero post-terminal reads |
| GOAL-V3-03 | complete Development then Locked matrix | REQ-V3A-001 through REQ-V3A-008 | trusted Eval harness | EVAL-V3-DECISION-01 | one preregistered architecture conclusion |
| GOAL-V3-04 | all V3-B multi-turn families | REQ-V3B-001 through REQ-V3B-005 | candidate validator, assertion ledger, merger, snapshot, question policy | TEST-V3B-FACT-01/02/03/04/05 and TEST-V3B-QUESTION-01/02/03/04 | provenance/hash parity, conflict/unknown correctness, repeat-question count |

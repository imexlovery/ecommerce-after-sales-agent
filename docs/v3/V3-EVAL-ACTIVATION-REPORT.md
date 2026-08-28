# V3 Eval Activation report

Status: **Eval Activation complete; formal Development remains closed**.

The patch started from the exact clean revision
`62e05b45fca714f1b6c64160b814adb172a8f39d`.  It did not call DeepSeek or any
real model/provider, did not consume a formal execution identity, and did not
run Development measurement, Live, Freeze, Locked Eval, or Release Evidence.
The resulting gate is **`NO_GO_FORMAL_DEVELOPMENT_NOT_AUTHORIZED`** and the
current pause is **V3 Development Execution Authorization Gate**.

## Boundary: contract harness versus production activation

| surface | implementation and evidence | claim allowed now |
|---|---|---|
| PREP contract-test harness | `V3PairedRunner` retains its synthetic payload/trace construction and `V3-PREP-DRY-RUN-001` write-once store | contract, schema, grader, retention, and provider-free preparation only; never Development measurement |
| production activation smoke | `AfterSalesApplication` composition root, public conversation/message path, the LangGraph investigation graph, both selector adapters, governed tools, reducer/replay event, Evidence Gate, Case Fact service, response/proposal/executor path, and SQLite/EventStore persistence | activation only; `ACTIVATION_SMOKE_NOT_DEVELOPMENT_MEASUREMENT` |
| future formal Development runner | `V3RealDevelopmentRunner` plus `ProductionInvestigationAdapter`; explicit case-input bindings are passed into the production runtime and typed evidence is parsed from persisted rows/events | executable path is activated, but it cannot open while the reserved manifests and execution authorization are closed |

The production runner contains no `_payload_for_case` call and does not create
synthetic `DecisionTrace`, `ToolCall`, `EvidenceProgress`, or
`CaseFactSnapshot` records.  The adapter executes the existing production
Investigation service; the activation smoke additionally exercises the public
application path so response/proposal/action persistence is visible.  The
synthetic PREP trace is never used as production activation evidence.

## Mechanical plan and closed preflight

The `plan` command derives its output from the committed matrix/manifests and
the project `ToolBudget` limits:

| item | result |
|---|---:|
| phase | `Eval Activation` |
| matrix cases | 32 |
| paired architecture runs | 64 (`Agent=32`, `Workflow=32`) |
| repeat | 1 |
| timeout | 30 seconds |
| selector-turn ceiling | 16 per Case; 8 per Run |
| authorized provider ceiling | 8 per Run, shared by the pair |
| Agent maximum selector provider calls | 256 |
| Workflow maximum selector provider calls | 0 |
| paired maximum selector provider calls | 256 |
| token ceiling | not configured; requires explicit `V3_TOKEN_CEILING` value |
| cost | `unavailable` without a trusted price basis |

The exact derivation is:

`Agent: 32 runs x 8 selector turns/run x 1 provider call/selector turn = 256; Workflow: 32 runs x 8 x 0 = 0; paired maximum = 256`.

The provider ceiling is a selector-resource ceiling.  The pair contract does
not require observed model/provider calls, tokens, or latency to be equal:
those are separately measured outcomes.  It does require equal canonical
input digest, shared component versions, selector-turn/provider ceilings,
timeout, repeat, fixture/source/fault/clock, tools, Router, Gate, graders,
response, and executor bindings.  A negative test rejects an asymmetric
authorized ceiling; a positive test accepts an Agent observation with
`provider_calls=1` beside a Workflow observation with `provider_calls=0`.

### Preflight

Preflight mechanically reported:

```text
status=NO_GO_FORMAL_DEVELOPMENT_NOT_AUTHORIZED
matrix_case_count=32
planned_run_count=64
provider_calls=0
model_calls=0
formal_execution_identity_not_consumed=true
```

The reserved manifest source revision remains
`68767c2ebdbdefc7621d950f726946b74ab52c9f`, and both manifests retain
`formal_measurement_authorized=false`.  The activation patch therefore cannot
turn a clean checkout into an accidental formal run.

## Production-path activation evidence

`activation-smoke` ran with `LLM_MODE=mock`, `_env_file=None`, a fake local
policy retrieval adapter, fictional `ORD-001`, and isolated temporary SQLite
roots.  It produced two independent persisted runs:

| selector | persisted read ToolCalls | Case Fact snapshot | graders | provider/model |
|---|---:|---|---:|---:|
| Agent | 5 | present | 13/13 pass | 0/0 |
| Workflow | 5 | present | 13/13 pass | 0/0 |

Both runs persisted the core trace surface, including
`decision_trace_record`, `state_trace_record`, `recovery_trace_record`,
`evidence_progress_rebuilt`, and `evidence_gate_evaluated`.  The public path
also persisted `case_created`, `proposal_created`, `action_recommended`, and
`run_succeeded`.  The production adapter test independently parsed the
persisted Workflow trace into the V3 typed contract with `0/0` calls.  The
activation report is explicitly labelled
`ACTIVATION_SMOKE_NOT_DEVELOPMENT_MEASUREMENT`.

## Write, replay, and failure boundary

The future formal store accepts only
`var/v3/development/<V3-DEV-EXEC-...>` and rejects the PREP identity or any
V2 `evals`, `delivery`, or `artifacts` root.  A logical run key is
`(scenario_id, pair_id, architecture, repetition)`: a byte-identical replay is
idempotent, while a second `eval_run_id` for the same key fails closed.  The
formal runner catches timeout, provider, schema, grader, and runtime failures
and writes one explicit failed raw record per planned run before validating
the complete 64-run key set.  No numeric cost is substituted for missing
pricing, and reports retain `architecture_conclusion=NOT_EMITTED`; no
`ADOPT_AGENT` is produced.

This write path was contract-tested only.  No formal identity was opened or
consumed during Eval Activation.

## Verification

| check | result |
|---|---|
| full repository pytest | **221 passed** |
| V2 targeted regression | **27 passed** |
| V3 adaptive-runtime/replay targeted regression | **24 passed** |
| V3 Case Fact/activation targeted regression | **30 passed** |
| activation unit/contract tests | **8 passed** |
| manifest/schema validation | passed; 32 cases and 64 planned paired runs |
| plan/preflight CLI | passed; preflight deliberately returned closed exit code `2` with `NO_GO`, provider/model `0/0` |
| production-path activation smoke | passed for both selectors; provider/model `0/0` |
| Ruff | passed |
| strict Mypy | passed; 75 source files |
| `git diff --check` and protected evidence-path check | passed; no V2/V3-A/V3-B frozen evidence path changed |

The final checkout/commit SHA and clean-worktree status are reported in the
handoff.  The append-only correction in
`V3-DEV-EVAL-PREP-REPORT.md` preserves the earlier `212` line and records the
actual `213` clean-start baseline and `221` final verification count.

## Residual risks and the next Owner gate

No provider-backed behavior, real latency/token distribution, real cost, or
formal Development quality distribution has been established.  The reserved
case manifests do not yet carry a formal execution authorization or a token
ceiling.  A future Owner-approved case-input binding must supply every
scenario's customer, authorized order, issue, customer message, fixture/source
revision, fault identity, and evaluated clock; incomplete or mismatched
bindings fail closed.  This activation patch does not choose a dynamic-path
threshold and does not change V2 `PREFER_WORKFLOW`.

To open a later formal run, the Owner must confirm all of the following in one
new immutable decision/identity package:

- a new unique identity matching `V3-DEV-EXEC-[A-Z0-9-]+`;
- explicit authorization flag and `LLM_MODE=live`;
- boolean-only presence confirmation for the named `DEEPSEEK_API_KEY` setting;
- clean source revision equal to the newly frozen manifest/source revision;
- exact manifest digests and `v3.eval.activation-plan.v1` binding;
- explicit positive token ceiling through `V3_TOKEN_CEILING` and its pricing
  interpretation, or an explicit acceptance that cost remains unavailable;
- timeout `30s`, repeat `1`, Case selector ceiling `16`, Run selector ceiling
  `8`, six actual read executions, and the shared per-run provider ceiling `8`;
- the Agent/Workflow selector-resource derivation of `256/0` and the complete
  64-run denominator, including every failure;
- explicit confirmation that no Freeze, Locked Eval, Release Evidence, or
  architecture adoption decision is included in that authorization.

Until that gate is confirmed, the status remains
**`NO_GO_FORMAL_DEVELOPMENT_NOT_AUTHORIZED`** and the pause remains
**V3 Development Execution Authorization Gate**.

# V3 Development Budget Guard report

Status: **Development Budget Guard complete; formal Development remains `NO_GO_FORMAL_DEVELOPMENT_NOT_AUTHORIZED`.**

This report records the narrowly authorized patch that started from clean
commit `6268caa883ae28aeb2368f67054e4828a3c7f209`. It does not authorize, run,
or score the formal Development measurement, Live, Freeze, Locked Eval, or
Release Evidence stages. The only user-visible phase name for this work is
`Development Budget Guard`.

The real external provider boundary was not opened. This task's real
provider/model result is `provider_calls/model_calls = 0/0`. The fake-provider
tests below deliberately exercise local model-shaped objects; their local
call counts are test evidence, not Live or formal Development evidence.

## 1. Owner decision and scope

`DEC-V3-032` records explicit authorization for this zero-provider Budget
Guard patch only. `DEC-V3-033` records `NO_GO_PATCH_REQUIRED` for the two
Owner Review blockers: aggregated LangChain usage metadata is not a call
counter, and the previous token ceiling was not an executed cross-run budget.
The existing reserved manifests remain un-authorized and the pause remains
**V3 Development Execution Authorization Gate**. These decisions were
appended to `docs/v3/decision-evidence.jsonl`; prior decision IDs and prior
Activation evidence were not rewritten.

The V2 conclusion remains `PREFER_WORKFLOW`. No `ADOPT_AGENT` conclusion is
emitted.

## 2. Original gaps and repair

Before this patch:

1. `ProductionInvestigationAdapter.execute` derived model/provider call count
   from `len(UsageMetadataCallbackHandler.usage_metadata)`. LangChain groups
   usage by model name, so repeated calls to one model could collapse into a
   count of one.
2. The old `V3ExecutionAuthorization.token_ceiling` checked a future binding
   but did not provide a durable execution-scoped ledger, pre-call admission,
   cumulative consumption, stop behavior, or restart/replay identity.
3. The Development runner called every planned item directly and did not
   retain budget-exhausted Agent items as explicit raw failures.

The repair adds a project-owned append-only `DevelopmentBudgetLedger`, a
selector invocation observer, deterministic runner admission/skip behavior,
binding checks in the Development store, and report/CLI fields for the
accounting boundary.

## 3. Invocation definitions and retry policy

The ledger counts one `model.ainvoke(...)` boundary only after a successful
pre-call admission. For the Agent selector, each admitted boundary is both:

- one `selector_invocation_attempt`;
- one `model_invocation_attempt`; and
- one `provider_invocation_attempt`.

`completed_*` counts only responses that return normally. A provider error,
timeout, or cancellation is still an attempted invocation and is retained in
its own failure counter. A returned malformed selector response is a
completed model/provider attempt and is then recorded as a selector schema
failure. Workflow selector calls are deterministic local calls: their
`provider_invocation_attempts` and `provider_calls` remain zero.

The formal Live model is constructed with `max_retries=0` and an output cap of
512 tokens. The ledger's provider semantics are
`pre_call_admitted_outer_ainvoke_attempt`. This is an exact count of the
project-observed outer provider invocation boundaries, not an unsupported
claim about hidden HTTP transport attempts. Because lower-level transport
attempts cannot be independently observed here, reports explicitly retain
`provider_attempts_exact=false` for the Agent side and the retry policy is
`sdk_retries_disabled_internal_transport_attempts_not_observable`.

The old `len(usage_metadata)` cardinality rule is no longer used to derive
calls. Callback metadata is used only to fill provider-reported token fields;
per-response usage is preferred. A callback's model-name aggregation cannot
collapse ledger invocation records.

## 4. Durable ledger and admission contract

`DevelopmentBudgetLedger` persists JSONL events under the formal store as
`budget-ledger.jsonl`, with a sibling lock file. Every ledger is bound to:

- `execution_identity`;
- source revision;
- all manifest digests;
- plan version;
- global authorized provider-call ceiling;
- per-run provider-call ceiling;
- provider hard-ceiling and retry semantics;
- cumulative observed-token threshold semantics; and
- the per-invocation output-token cap.

The event log is rebuilt under an inter-process file lock. Admission is
atomic, before provider I/O, and uses the deterministic
`(logical_run_key, selector_turn)` identity. A replay of an already-admitted
identity is denied without provider I/O. A malformed ledger or any binding
mismatch fails closed. An admitted invocation left incomplete across a
restart becomes a durable `provider_invocation_incomplete` stop; it is never
silently retried.

The registered provider ceiling is:

```text
Agent:    32 runs x 8 selector turns x 1 provider call = 256
Workflow: 32 runs x 8 selector turns x 0 provider calls = 0
```

The project-owned ledger is the hard execution-scoped provider ceiling:
admission number 257 is rejected before the model/provider call. Workflow
does not consume this provider budget.

## 5. Token semantics and honesty boundary

The token rule is named
`cumulative_observed_total_tokens_post_response_stop`. Provider usage is
observed after a response or terminal provider result, so this is not an
absolute hard total-token ceiling. The implementation therefore records:

- `hard_token_ceiling=false`;
- `overshoot_bound_provable=false`;
- output-token request cap `512` per invocation; and
- `token_usage_unavailable` fail-closed behavior when a terminal attempt does
  not provide complete input/output/total usage.

When complete usage is available, the next admission is denied at or above
the cumulative threshold. A response can cross the threshold once before the
post-response stop is known. The observed overshoot is recorded, but a
provable total overshoot upper bound is not claimed because the input side
and any lower-level hidden transport behavior are not reserved by a strict
token reservation protocol. Formal execution requires an explicit Owner
acceptance of this threshold meaning; the current plan has no configured
threshold and remains closed.

## 6. Denominator, restart, and report behavior

The runner uses deterministic logical run keys and write-once raw records.
Persisted records are replayed without re-running the adapter. When the Agent
budget is exhausted, every remaining planned Agent key receives a raw
`provider_budget_exhausted` failure record with `error_class=budget`; Workflow
items may continue through their zero-provider path. No planned item is
deleted, fabricated as a success, or recorded twice. The report still
requires `planned_run_count == recorded_run_count == raw_run_count == 64` and
retains all failure records in the denominator.

Report fields now separately expose selector attempts, model attempts,
provider attempts, completed calls, provider errors, timeouts, cancellations,
remaining calls, provider-reported input/output/total tokens, threshold
semantics, threshold exhaustion, observed overshoot, and `cost=unavailable`
when no trusted price basis exists. `architecture_conclusion` remains
`NOT_EMITTED`.

## 7. Provider-free evidence

The local fake-provider tests in
`tests/unit/test_v3_development_budget_guard.py` cover:

- eight consecutive calls using one model name counted as 8;
- successful response, selector schema failure, provider failure, timeout,
  and cancellation accounting;
- admission 257 rejected before fake provider I/O;
- exact token-threshold hit and one observed overshoot, followed by stop;
- restart/replay without re-consumption or a second fake call;
- 256 pre-consumed Agent attempts followed by complete `64/64/64` raw
  retention, 32 explicit budget failures, and 32 Workflow runs with provider
  count zero.

These are local contract/integration-style tests only. They do not authorize
or simulate a DeepSeek Live gate.

## 8. Verification and current pause

The completed verification set is recorded in the handoff and includes the
fake-provider tests, V2/V3-A/V3-B/Activation/Prep regressions, full pytest,
Ruff, strict Mypy, matrix/manifest validation, closed `plan` and `preflight`,
closed `execute` validation, `git diff --check`, and protected evidence-path
diff inspection. All commands use the repository `.venv` through `uv run`
with a writable `/private/tmp` `UV_CACHE_DIR`; no `.env` contents were read or
printed, and credential checks are boolean-only.

The closed CLI surfaces show the provider hard ceiling and token semantics.
`preflight` remains exit 2 with `provider_calls/model_calls=0/0` because the
source is not yet bound to the reserved manifest revision and the manifests
remain formally un-authorized. The formal identity is not opened.

Final local verification before the clean handoff commit was:

| check | result |
|---|---|
| full repository pytest | **232 passed**; no formal Development measurement |
| V2/V3-A/V3-B/Activation/Prep targeted regression | **111 passed** |
| fake-provider Budget Guard tests | **11 passed** |
| Ruff | passed; all `src` and `tests` |
| strict Mypy | passed; **78 source files** |
| matrix/manifest schema validation | passed; **32 cases / 64 planned paired runs** |
| `plan` | passed; provider ceiling **256**, per-run **8**, output cap **512** |
| `preflight` | expected closed exit **2**; provider/model **0/0** |
| `execute` authorization check | expected closed exit **2**; provider/model **0/0**; credential presence boolean only |
| Activation Mock smoke | passed for Agent and Workflow; provider/model **0/0**; labelled `ACTIVATION_SMOKE_NOT_DEVELOPMENT_MEASUREMENT` |
| `git diff --check` and protected evidence paths | passed; no V2/V3-A/V3-B/Prep frozen evidence path changed |

## 9. Next Owner-only gate

The next Owner action is only to explicitly confirm the future formal
parameters and authority: a new execution identity, clean source revision,
manifest/source/digest binding, global ceiling 256, per-run ceiling 8, a
numeric cumulative observed-token threshold, acceptance of its non-hard
post-response semantics, the 512 output cap, Live mode plus boolean credential
presence, and the desired timeout/repeat policy. Until that confirmation and
the separate formal authorization are recorded, no provider call, formal
Development measurement, Freeze, Locked Eval, or Release Evidence may run.

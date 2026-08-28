# V3 Development Eval Results

Status: **NO_GO_DEVELOPMENT_FAILED_STOP_BEFORE_FREEZE**

This is Development measurement only. It is not Live browser evidence, a
Freeze, Locked Eval, Release Evidence, or an architecture-adoption decision.

## Immutable binding

- Evaluated source: `98b45dc27ca2d7996152404378e87a7b9a38c3bd`
- Execution identity: `V3-DEV-EXEC-20260828-01`
- Package digest: `b2d5399ea2e0586cdcdc6d2ed2762770638b4db4a22fefcd7e4e6a3cc6b97a74`
- Plan version: `v3.eval.activation-plan.v1`
- Model: `deepseek-v4-flash`
- Repeat / timeout: `1 / 30 seconds`
- Agent provider ceiling: global `256`, per-run `8`
- Workflow provider ceiling: `0`
- Output cap: `512` tokens per invocation
- Observed-token threshold: `1,000,000`
- Token semantics: `cumulative_observed_total_tokens_post_response_stop`
- `hard_token_ceiling=false`; `overshoot_bound_provable=false`
- Cost: `unavailable`

## Completeness and resource evidence

| Item | Result |
| --- | ---: |
| Planned / recorded / raw | 64 / 64 / 64 |
| Agent / Workflow runs | 32 / 32 |
| Agent / Workflow provider calls | 32 / 0 |
| Completed provider calls | 32 |
| Provider errors / timeouts / cancellations | 0 / 0 / 0 |
| Provider-reported input tokens | 37,444 |
| Provider-reported output tokens | 9,154 |
| Provider-reported total tokens | 46,598 |
| Remaining provider calls | 224 |
| Threshold exhausted / observed overshoot | false / 0 |

Every planned key is retained exactly once. No provider probe occurred outside
the formal denominator, and the identity was not selectively rerun.

## Architecture results

| Architecture | Quality pass | Safety pass | Retained failures |
| --- | ---: | ---: | --- |
| Agent | 0/32 | 0/32 | 32 `SELECTOR_SCHEMA_FAILURE` records |
| Workflow | 14/32 | 30/32 | 16 grader failures; 2 `CaseFactIntegrityError` runtime failures |

All 32 Agent provider requests completed and returned complete usage metadata,
but the response failed the strict native selector schema boundary on the
first selector turn in every run. This is a model/selector contract failure,
not a provider connectivity failure. The sanitized evidence does not retain
raw provider payloads or the more specific rejected response shape, so this
report does not guess whether the common cause was message type, tool-call
cardinality, or another typed-boundary condition.

Workflow failures break down as follows:

- six guard-family cases produced an outcome outside their case contract;
- all eight V3-B cases failed `GR-V3B-02` because snapshot active assertions
  did not match the consumption ledger;
- `v3a-snr-active-ticket` failed the registered observation-conditioned route;
- `v3a-stall-policy-conflict` produced an outcome outside its case contract;
- `v3a-snr-pod-reception-proof` and
  `v3a-snr-pod-nonreception-proof` terminated with
  `CaseFactIntegrityError` and failed hard safety.

These failures block V3 Freeze. The Agent result cannot demonstrate adaptive
trajectory value because no Agent run crossed the selector boundary. Workflow
also does not meet the Development quality/safety floor. The V3 report keeps
`architecture_conclusion=NOT_EMITTED`; V2 `PREFER_WORKFLOW` remains the current
evidence-backed conclusion.

## Artifact integrity

Local generated root:
`var/v3/development/V3-DEV-EXEC-20260828-01/`

| Artifact | SHA-256 |
| --- | --- |
| authorization package | `e39312f795d616bd9ac6470b9d5727cddace28c366e3961f837c4340f43b1b2e` |
| budget ledger | `cbeac4f19b1fb702e3eb85733586a782b0e6b9d9cf6d844d53166b42331b61f0` |
| execution-state ledger | `3f9d16b1ba7067fdbabec662fc6269cf838e6518507c98c79e202009a40c7d29` |
| Development report | `0bcdb1f6ba36742cc7efedff0f59f64c99cbfa3d1596425723682d0f3d083898` |
| aggregate of sorted 64 run-file hashes | `aa3de0996b8799a0e78a91d5bfbc409560cbf0c97dad11f15303bf6541b491b1` |

Pydantic report/run parsing, 32-pair validation, and 64-run completeness
validation passed after execution. Full repository pytest collected and passed
238 tests; Ruff passed; strict Mypy passed 79 source files; `git diff --check`
is required on the documentation-only handoff commit.

## Gate and stop

Development result: **NO-GO**.

Do not create a V3 Freeze, run Locked Eval, generate Release Evidence, or emit
`ADOPT_AGENT`. Any repair must preserve this identity, package, raw records,
report, and failure classification unchanged, then use a new explicitly
authorized source revision and Development identity.

## Subsequent authorized result: `V3-DEV-EXEC-20260828-02`

This section is an append-only result for the repaired Live selector. The
earlier `V3-DEV-EXEC-20260828-01` report, its failure denominator, and its
artifact hashes remain unchanged.

### Binding and completeness

- Evaluated source: `0c7f21d86893cb9f8441b3695ef9c1d8e31ff398`
- Execution identity: `V3-DEV-EXEC-20260828-02`
- Measurement status: `development_measurement_not_release`
- Model: `deepseek-v4-flash`
- Planned / recorded / raw: `64 / 64 / 64`
- Agent / Workflow: `32 / 32`
- All planned keys retained exactly once: `true`
- `all_failures_retained`: `true`

### Resource and failure accounting

| Metric | Result |
| --- | ---: |
| Provider/model calls | 25 / 25 |
| Attempted / completed provider calls | 25 / 25 |
| Remaining calls | 231 / 256 |
| Provider errors / timeouts / cancellations | 0 / 0 / 0 |
| Reported input / output / total tokens | 32,799 / 7,172 / 39,971 |
| Token threshold / semantics | 1,000,000 / cumulative observed post-response stop |
| Threshold exhausted / token overshoot | false / 0 |
| Token usage complete | true |
| Hard token ceiling / exact internal attempts | false / false |
| Cost | unavailable |

The formal run set was not rerun after completion for quality or safety. The
25 failed Agent runs are retained as `schema_failure` with the exact reason
`SELECTOR_MULTIPLE_TOOL_CALLS`; the provider calls completed and returned usage
metadata, so this is a selector contract failure rather than connectivity,
timeout, or provider failure. The Workflow pair has 0 retained run failures and
0 grader failures. The deterministic report conclusion is **`NO_GO`** because
Agent safety/schema failures are hard blockers; the result does not emit
`ADOPT_AGENT` and does not replace the historical V2 `PREFER_WORKFLOW`.

### Current selector diagnostic

`V3-DEV-DIAG-20260828-03` passed on the same final source revision. The three
fixed inputs (read, legal finish, and parameter-limited policy path) passed;
9/9 real provider admissions/completions were used against the 12-call
diagnostic ceiling. The append-only ledger contains 28 events, including the
earlier configuration block and one earlier finish-boundary failure; neither
was deleted or relabeled.

### Artifact integrity

| Artifact | SHA-256 |
| --- | --- |
| authorization package | `683bf4707f48d6d0459fd0daaf2972e8feb8ed32e93e9aaf5c2d2244e60f536c` |
| budget ledger | `a5f7625b7c2a5bf4a0c66f7a246376e89f4a4b34465eb0d5e9450c6ab46af753` |
| execution-state ledger | `1ced13486d09aa2026cd6fc4c72203378237191294c9d5c0f82dadff3bf5efa8` |
| Development report | `86fe6db465a99ecdbbe0be40efbf626439c801978e51d41c9f92e2cdb2076b4f` |
| aggregate of sorted 64 run-file hashes | `ea26d1b0c97cfe65abfc5a605f2a9c5f7fd9e23d8095304f204bc2227b3b7da0` |

Generated artifact roots:

- `var/v3/development/V3-DEV-EXEC-20260828-02/authorization-package.json`
- `var/v3/development/V3-DEV-EXEC-20260828-02/reports/V3-DEV-EXEC-20260828-02-REPORT.json`
- `var/v3/development-diagnostics/V3-DEV-DIAG-20260828-03/diagnostics.jsonl`

### Stop boundary

This is Development evidence only. V3 Freeze, Locked Eval, Live browser
evidence, Release Evidence, deployment, push, PR, and architecture adoption
remain unexecuted. Any future repair must preserve this `-02` identity, package,
raw records, report, and failure classification, then use a new authorized
source revision and Development identity.

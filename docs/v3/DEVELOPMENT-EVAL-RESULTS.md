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

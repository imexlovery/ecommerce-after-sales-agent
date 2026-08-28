# V3-M1 Development Evidence Closure Milestone

Status: **OWNER-AUTHORIZED; execute one formal Development measurement and stop at the V3-M1 Owner Gate.**

This milestone is opened by the Owner's confirmation that `V3-A0 Rescue
Owner Gate = GO`. It consumes the already prepared V3-A/V3-B matrix and the
production-path paired runner. It does not reopen V2 evidence or authorize any
later lifecycle gate.

## Formal execution binding

- task: `V3-M1 Development Evidence Closure Milestone`
- formal execution identity: `V3-DEV-EXEC-20260828-03`
- identity status before this milestone: reserved and unconsumed
- manifest identities: `V3A-EVAL-DEV-001`, `V3B-EVAL-DEV-001`
- dataset: 32 cases, 64 paired runs, Agent/Workflow 32/32, repeat 1
- provider/model: Live DeepSeek, `deepseek-v4-flash`
- timeout: 30 seconds per run
- output-token cap: 512 per provider invocation
- Agent provider ceiling: 256 global, 8 per run
- Workflow provider calls: 0
- automatic retry: disabled (`max_retries=0`; no selective rerun)
- token threshold: existing bound `1_000_000` under
  `cumulative_observed_total_tokens_post_response_stop`; this milestone does
  not change its accepted non-hard semantics
- cost: `unavailable` unless a trusted price basis exists

The formal package must be created only after the final pre-identity clean
source revision is committed. It must bind that revision, the committed
Manifest digests, plan digest/version, all 32 production inputs, and the
parameters above. The package is write-once and must not contain a credential
value. Credential checks are boolean-only.

## Fairness and retention boundary

Agent and Workflow share the same case inputs, trusted scope, fixtures, source
revision, fault seeds, clock, cache/retry rules, budgets, ToolNode,
GovernedToolExecutor, Evidence Progress, Router, Evidence Gate, response layer,
executor, trace path, graders, timeout, and report contract. Only
`select_next_observation` differs. A provider/model/schema/timeout/runtime or
grader failure is one retained raw record in the same 64-run denominator.

After identity creation, do not modify source code, Manifest, grader, threshold,
or execution parameters. Do not select a best run or rerun a failed run. A
restart may only replay the same identity-bound append-only state and must not
execute provider/model/tool work twice.

## Stop boundary

The milestone may audit, repair, test, commit, create the package, and execute
the one formal 32-case / 64-run Development measurement. It must stop after
the resulting `GO_TO_FREEZE` or `TERMINAL_NO_GO` decision at the **V3-M1 Owner
Gate**. Neither outcome authorizes V3 Freeze, Locked Eval, Live browser,
Release Evidence, deployment, push, PR, or architecture adoption.

The previous V2 `PREFER_WORKFLOW`, all prior V3 identities and failures, the
V3-A0 Rescue evidence, and all frozen/release artifacts remain immutable.

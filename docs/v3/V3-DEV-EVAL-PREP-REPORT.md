# V3-DEV-EVAL-PREP-001 report

Status: **preparation complete; paused at `V3 Development Eval Preflight Owner
Gate`**.

This report is preparation evidence only.  It is not V3A/V3B Development
measurement, Live evidence, Freeze evidence, Locked Eval, Release Evidence, or
an architecture verdict.  The reserved manifests
`V3A-EVAL-DEV-001` and `V3B-EVAL-DEV-001` remain `reserved_not_executed`.

| field | result |
|---|---|
| preparation task | `V3-DEV-EVAL-PREP-001` |
| clean source revision | `68767c2ebdbdefc7621d950f726946b74ab52c9f` |
| matrix | 32 cases: 24 V3-A + 8 V3-B; every row has an Agent/Workflow pair |
| scenario families | V3-A: ORDER, TICKET, POLICY, POD, RETRY, FAIL, STALL-SLA, STALL-TICKET, STALL-POLICY, GUARDS; V3-B: location fact, unknown, repeat, correction, conflict, bounded question, replay, cross-Case source |
| dry-run identity | `V3-PREP-DRY-RUN-001` (isolated `var/v3/prep/dry-run/`) |
| planned / recorded / raw | `64 / 64 / 64` |
| failures retained | yes; raw records and denominator are write-once and completeness-checked (synthetic timeout/schema/provider/grader injections are supported for offline tests) |
| provider calls | `0` |
| model calls | `0` |
| cost | `unavailable` (no trusted price basis; never written as zero) |
| architecture conclusion | `NOT_EMITTED`; V2 `PREFER_WORKFLOW` remains effective |

## What was verified

- Extra-forbid typed contracts reject unknown predicate field paths/operators,
  duplicate IDs, missing pairs, revision/version mismatches, unregistered
  graders and incomplete result sets.
- Agent and strong Workflow receive one byte-equivalent shared input digest,
  typed DecisionContext, fixture/source/fault/clock/budget/cache, retry,
  Router/Gate/response/executor versions and grader set; only the selector
  adapter/version differs.  Agent-only and Workflow-only asymmetric injection
  tests fail closed.
- Deterministic graders consume typed decisions, recoveries, states, ToolCall /
  EvidenceRef, Evidence Progress rebuilds, Gate results, Case Fact assertions /
  snapshots and question-consumption ledgers.  No LLM Judge, prose score or
  hand-authored report verdict is used.
- The dry-run writes exactly one raw JSON record for each planned
  `(scenario_id, pair_id, architecture, repetition)` key, including a failed
  run when an offline fault is injected.  Replay is idempotent and cannot add a
  second divergent record.

## Checks run

The final commit records the exact command outputs for the following checks:

| check | scope / expected result |
|---|---|
| manifest/schema validation | `UV_CACHE_DIR=... uv run after-sales-v3-prep validate`; **32 cases**, reserved A/B identities, **64 planned runs** |
| harness dry-run completeness | `UV_CACHE_DIR=... uv run after-sales-v3-prep dry-run`; **64 planned / 64 recorded / 64 raw**, provider/model **0/0** |
| unit / contract / integration / replay tests | Full repository `pytest -o addopts=''`: **212 passed**; includes V3 contract fail-closed, fairness, grader determinism, retention and replay suites |
| Ruff | `uv run ruff check .`: **All checks passed** |
| strict Mypy | `uv run mypy --strict src`: **Success, no issues in 74 source files** |
| diff checks | `git diff --check`: **pass**; protected V2/V3-A1/V3-B paths: **no diff** |

## Residual risks and next Owner decisions

This preparation does not establish provider behavior, model latency/tokens,
real cost, or a Development quality distribution.  Before a real Development
Eval, the Owner must issue a new decision specifying the exact run count (and
whether repeats change), whether DeepSeek calls are authorized and the maximum
provider-call/token budget, and the timeout/repeat policy.  Only then may a new
Development execution identity be opened.  Freeze, Locked Eval, Release
Evidence, deployment, push, and PR remain outside this authorization.

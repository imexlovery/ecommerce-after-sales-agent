# V3-M1R2 External Resource Unblock and Valid Measurement

## Final disposition

`GO_TO_FREEZE`

本轮在 `V3-M1R2 Owner Gate` 停止。新的外部资源门槛已解除，固定 formal-path
canary 通过，随后唯一允许的 `V3-DEV-EXEC-20260829-04` 完成了有效的
32-case / 64-run Development measurement。正式 report 的确定性
`architecture_conclusion` 为 `PREFER_WORKFLOW`：Agent 与 Workflow 的质量和
安全结果相同，Development 阶段没有产生 Agent 采用结论。该结果不是
`BLOCKED_EXTERNAL_RESOURCE`，也不把外部问题称为 Adaptive trajectory 失败；
`GO_TO_FREEZE` 只表示后续可由 Owner 考虑新的 Freeze，不表示本轮执行 Freeze
或采用 Agent。

V2 `PREFER_WORKFLOW` 保持不变。没有执行 Freeze、Locked Eval、Live browser、
Release Evidence、部署、push、PR 或架构采用。

## 1. Network permission gate

| check | result |
| --- | --- |
| configured DeepSeek scheme/host | `https://api.deepseek.com`（只记录 scheme/host） |
| escalated execution permission | obtained; canary and formal runner were executed with `sandbox_permissions=require_escalated` |
| credential handling | presence-only evidence; no key value was printed, copied, or persisted in this report |
| no-credential DNS | passed; two resolved addresses |
| no-credential TCP | passed; TCP/443 connected |
| no-credential TLS | passed; TLS established with TLSv1.2 |
| no-credential HTTP | reached host; HTTP `401` without authentication |
| API key sent by probe | `false` |
| provider/model calls by probe | `0/0`; probe was not provider/model execution |

The probe established reachability before the first new provider admission. The
canary then used the existing configured credential source and did not create or
rotate an API key.

## 2. Pre-canary engineering gates

All checks below ran after the canary identity change was committed and before
the new canary was opened.

| gate | result |
| --- | --- |
| full pytest | `266 passed`, exit `0` |
| Ruff | `All checks passed!` |
| strict Mypy | `Success: no issues found in 83 source files` |
| dependency consistency | `Checked 100 packages; All installed packages are compatible` |
| `uv.lock --check` | passed; resolved `122` packages |
| Manifest/matrix validation | `32` cases, `32` production inputs, `64` planned runs |
| Development plan | Agent `32`, Workflow `32`; provider ceiling `256`, per-run `8`, Workflow provider calls `0` |
| exact-path Gate B | `exact_path_ready=true`; Live Settings/model/selector and Mock Workflow path constructed |
| Gate B provider/model count | `0/0` |
| clean committed source | passed at `e7069d16ac64220a1b48534518483afc85ad1261` |

`after-sales-v3-eval preflight` returned its expected closed-form exit `2` because
the reserved manifests remain formally unauthorized; its exact production path
check was true and it made no provider/model call.

## 3. New formal-path canary

| field | result |
| --- | --- |
| identity | `V3-DEV-EXEC-CANARY-20260829-02` |
| label | `real_external_formal_path_canary_not_development_measurement` |
| evaluated source revision | `e7069d16ac64220a1b48534518483afc85ad1261` |
| manifest | `V3-A0-RESCUE-20260828-01` |
| manifest digest | `a9727c3cae01be4d1b8b56aa26ad0d9405d1467b97c60e46c6cd012802e8bf6f` |
| fixed input digest | `ebab92c20a480c15e6491bbdcd317e16d4c396e7e2823a20c3faf75d081d80e3` |
| model | `deepseek-v4-flash` |
| provider ceiling / used | `6 / 3` |
| timeout / output cap | `30s / 512` |
| automatic provider retry | disabled |
| result | `3/3 passed` |

The three fixed A0 inputs all used the formal production path:
`ProductionInvestigationAdapter → unified Live Settings → Live model → Agent
Selector → deterministic validator → server-built ToolCall → LangGraph ToolNode
→ governed execution`.

| smoke | provider/model/selector | completed response | ToolNode | actual reads | exact retry | result |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| A0-01 normal | `1/1/1` | `1` | `true` | `3` | `false` | passed |
| A0-02 multiple evidence gaps | `1/1/1` | `1` | `true` | `3` | `false` | passed |
| A0-03 retryable timeout | `1/1/1` | `1` | `true` | `6` | `true` | passed |
| total | `3/3/3` | `3` | `3/3` | `12` | `1` | passed |

Canary report and ledger are append-only under:

- `var/v3/formal-path-canary/V3-DEV-EXEC-CANARY-20260829-02/report.json`
  (SHA-256 `6c2be62bea67c38c8adeb347023eb5cc0e77ff7135c40686305253be946d8c49`)
- `var/v3/formal-path-canary/V3-DEV-EXEC-CANARY-20260829-02/budget-ledger.jsonl`
  (SHA-256 `168c3d4607f7abfdffc0efe4ba01ce8c480adbd21eb6bdafba7ea33d703ef5b7`)

Historical canary `V3-DEV-EXEC-CANARY-20260829-01` was not rerun, overwritten,
rescored, or appended to. Its existing report and ledger remain unchanged.

## 4. Formal Development measurement

The canary passed before formal identity creation. The main formal identity was
then created once:

| field | result |
| --- | --- |
| formal identity | `V3-DEV-EXEC-20260829-04` |
| package created | yes, once |
| evaluated source | `e7069d16ac64220a1b48534518483afc85ad1261` |
| package digest | `af4388d6e9fd11c423dbca8a1433b295ff3f72360d7d081428589bed5f61321f` |
| plan | `v3.eval.activation-plan.v1`, 32 cases / 64 runs |
| provider ceiling / per-run ceiling | `256 / 8` |
| timeout / output cap | `30s / 512` |
| automatic provider retry | disabled |
| cost | `unavailable` |
| contingency `-05` | not created and not used |

The measurement retained `planned/recorded/raw = 64/64/64` and completed
Agent/Workflow `32/32` runs.

| metric | Agent | Workflow | total |
| --- | ---: | ---: | ---: |
| provider calls | `111` | `0` | `111` |
| model calls | `111` | `0` | `111` |
| selector invocation attempts | `111` | `0` | `111` |
| ToolNode reached | `25` | `25` | `50` |
| actual read executions | `116` | `115` | `231` |
| deterministic retry attempts | `4` | `4` | `8` |
| retry recovered | `1` | `1` | `2` |

All 64 run records were completed and retained. The formal report recorded:

- quality pass: Agent `32/32`, Workflow `32/32`;
- safety pass: Agent `32/32`, Workflow `32/32`;
- schema failures: `0`;
- provider errors: `0`;
- provider timeouts: `0`;
- provider cancellations: `0`;
- runtime/run-status failures: `0`;
- grader failures: `0`;
- `all_failures_retained=true`;
- provider-reported tokens: input `179655`, output `12065`, total `191720`;
- token usage complete: `true`; threshold `1000000`, not exhausted;
- provider attempts are reported with the project honesty boundary
  `provider_attempts_exact=false` because hidden SDK transport attempts are not
  independently observable.

The six non-recovered deterministic retry attempts belong to expected persistent
or unavailable fixture branches and did not become provider failures; their
paired run records remain in the denominator. The formal report's
`architecture_conclusion` is `PREFER_WORKFLOW`. With all hard Development
checks passing, this maps to `GO_TO_FREEZE`: a later Owner may consider a new
Freeze, but this milestone does not execute Freeze or adopt Agent.

Formal artifacts:

- `var/v3/development/V3-DEV-EXEC-20260829-04/authorization-package.json`
  (SHA-256 `b15dc7c85fc280eaf3acd37f3411892315d8672152bae5ff81e2873167224aa4`)
- `var/v3/development/V3-DEV-EXEC-20260829-04/budget-ledger.jsonl`
  (SHA-256 `580d924db0aa23f3f4ffbca2b63567ae83f99bbc867435b5c2686c6bc2ccd03d`)
- `var/v3/development/V3-DEV-EXEC-20260829-04/execution-state.jsonl`
  (SHA-256 `40a12db4338ea219ba3af73013bcde4d89eff9fb5126c2f152f1fb0a991f36aa`)
- `var/v3/development/V3-DEV-EXEC-20260829-04/reports/V3-DEV-EXEC-20260829-04-REPORT.json`
  (SHA-256 `121b78240fb3432e4f2d120e72b05f1e64f9dc07b751f388786115fe69f0d818`)

## 5. Commits, retention, and stop boundary

- Identity migration and its regression tests were committed in `e7069d1`
  (`e7069d16ac64220a1b48534518483afc85ad1261`). This is the evaluated source
  revision; the post-measurement documentation commit is not part of the
  measured source.
- Old `-01` canary evidence, V2 evidence, historical V3 evidence, manifests,
  grader code, threshold, and formal parameters were not modified.
- The final worktree is required to remain clean; no remote publication was
  performed.

The stop point is `V3-M1R2 Owner Gate`. Freeze, Locked Eval, Live browser,
Release Evidence, deployment, push, PR, architecture adoption, and any change
to V2 `PREFER_WORKFLOW` remain unexecuted.

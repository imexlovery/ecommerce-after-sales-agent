# V3-M1R Owner Gate Report — 2026-08-29

## 最终判定

`BLOCKED_EXTERNAL_RESOURCE`

本轮在 **V3-M1R Owner Gate** 停止。Gate A 与 Gate B 已通过；Gate C 的固定
Live canary 在第一个 smoke 的 provider admission 后被运行环境禁止访问
`api.deepseek.com`。该 admission 已用原始 invocation identity 在 canary
ledger 中终结为外部资源阻塞，没有重试、换 key 或新 canary identity。

这不是 Adaptive V3 trajectory 实验失败，也没有产出有效 Development
measurement。正式 `-04` authorization package 未创建，`-05` contingency
未使用。

## 当前实现与根因复核

旧 formal `-03` 的实际根因在当前源码中得到复核：authorization package 检查
使用可读取项目配置的 `Settings()`，而 formal
`ProductionInvestigationAdapter._default_settings()` 使用 `_env_file=None`。
因此 Agent 在 `build_live_model`、Selector、LangGraph `ToolNode` 之前因缺少
`DEEPSEEK_API_KEY` 抛出 `ValidationError`。这属于 project-owned readiness
defect，不是 Adaptive trajectory 结果。

本轮提交 `a768cb13f54644ad4161969936b136f0560dfee0` 的修复为：

- `config.py` 新增统一的 `load_settings`、`build_live_settings` 与
  `build_mock_settings`；显式项目根目录时从该项目 `.env` 与 process env
  加载配置，Live/Mock 模式仍由调用方明确选择。
- authorization package、A0 Rescue、formal adapter 默认路径全部复用
  `build_live_settings`；Live helper 不接受或注入 credential value。
- credential evidence 只保留 `bool` presence；report、ledger、trace 与
  exception 不保存 API key。
- formal adapter 新增可测试的 `build_settings` exact path；CLI 可识别
  identity-scoped package，但仍只接受 `-04`/`-05` 两个本任务 identity。
- 增加真实 Settings 装配测试：临时项目 `.env` 有 credential、process env
  无 credential；Agent model/selector 构造；`ChatDeepSeek.ainvoke`
  fail-if-called sentinel；Workflow Mock path；缺 credential 时 identity
  创建前 fail-closed。

旧测试没有发现该问题，是因为 authorization test 通过替换 `Settings` 返回
  `SimpleNamespace`，没有走真实 dotenv Settings source；另有 production
  tests 主要覆盖 Workflow/Mock path，未调用默认 formal Agent Live adapter。

## Gate A：工程门禁

| 检查 | 结果 |
| --- | --- |
| 全量 pytest | 266 tests collected；exit 0，全部通过 |
| Ruff | `All checks passed!` |
| strict Mypy | `Success: no issues found in 83 source files` |
| dependency consistency | `Checked 100 packages; All installed packages are compatible` |
| `uv.lock --check` | 通过，resolved 122 packages |
| Manifest / matrix validation | 32 cases、32 production inputs、planned 64，exit 0 |
| Development plan validation | Agent 32 / Workflow 32；Agent ceiling 256、per-run 8；Workflow 0 |
| evaluated revision | clean committed revision `a768cb13f54644ad4161969936b136f0560dfee0` |

Gate A 在创建新 formal identity 前通过。工作树在 Gate B 和 canary 收口后仍
保持 clean；`var/` 下的 canary evidence 是被忽略的运行 artifact，不是源代码
修改。

## Gate B：exact-path zero-provider readiness

执行正式 CLI `preflight`，路径为：

`formal CLI → ProductionInvestigationAdapter → build_live_settings → build_investigation_model → AgentObservationSelector`

结果：

| 项目 | 结果 |
| --- | --- |
| `exact_path_ready` | `true` |
| Live Settings | constructed / valid，model=`deepseek-v4-flash` |
| Agent selector | constructed |
| Workflow Settings / selector | constructed，Mock |
| credential evidence | `true`（presence only） |
| provider/model calls | `0/0` |
| source tree | clean |

CLI preflight 另在 `ChatDeepSeek.ainvoke` 上安装 fail-if-called sentinel；命令
正常完成且没有触发 sentinel。该门禁只证明构造路径，不证明 provider 已可用。

## Gate C：bounded Live canary

canary identity 为
`V3-DEV-EXEC-CANARY-20260829-01`，标签严格为
`real_external_formal_path_canary_not_development_measurement`。它复用了 A0
固定三 smoke template、同一 `ProductionInvestigationAdapter`、Live Settings
factory、model builder、Agent selector、LangGraph ToolNode 与 governed
execution path；provider ceiling 为 6，自动 provider retry 关闭。

| 项目 | 结果 |
| --- | --- |
| source revision | `a768cb13f54644ad4161969936b136f0560dfee0` |
| fixed input digest | `ebab92c20a480c15e6491bbdcd317e16d4c396e7e2823a20c3faf75d081d80e3` |
| planned / recorded smoke | `3 / 1` |
| provider / model / selector | `1 / 1 / 1` |
| completed provider responses | `0` |
| ToolNode reached / actual reads | `0 / 0` |
| exact retry | `0`（未到 A0-03） |
| credential evidence | `true`（presence only） |
| canary result | `blocked` |
| reason | `BLOCKED_EXTERNAL_RESOURCE_SANDBOX_NETWORK` |

一次性 artifacts：

- report：`var/v3/formal-path-canary/V3-DEV-EXEC-CANARY-20260829-01/report.json`
  SHA-256 `f5f850bfbb5d2a7e61a52546eb8c1ff3f6fc6e514519aca8517ce4a0392466ea`
- budget ledger：`var/v3/formal-path-canary/V3-DEV-EXEC-CANARY-20260829-01/budget-ledger.jsonl`
  SHA-256 `984f75f31616330c2725dcc374bb4f462cf1a9369ae16ce9c8b779eb3ecf4309`

Ledger 最终包含 1 个 admission、1 个 `provider_error` completion 和 1 个
external-stop marker。没有创建新的 provider admission；因此没有重跑 canary
或选择性重跑 smoke。

## Formal measurement 状态

主 identity `V3-DEV-EXEC-20260829-04` 只在源码常量中预留，未创建 package、
未消费 budget、未执行任何 formal run。

| formal denominator / counter | 结果 |
| --- | --- |
| planned / recorded / raw | `64 / 0 / 0`（`-04` 未打开） |
| Agent / Workflow completed | `0 / 0`（formal 未执行） |
| formal provider / model / selector | `0 / 0 / 0` |
| formal ToolNode / actual reads | `0 / 0` |
| formal schema / provider / timeout / runtime / safety failures | `N/A`，没有 formal run |
| formal cost | `unavailable` |
| contingency `-05` | 未使用；没有创建更多 identity |

不能从上述 `0/0/0` 推导架构或 Adaptive 结论；这是 Gate C 未通过后的关闭
状态。下次若要得到 Development evidence，必须在新的 Owner 授权下提供可用的
外部 provider resource，并从新的 append-only identity 开始。

## 历史证据保留

`V3-DEV-EXEC-20260828-03` 保持不可变。本轮没有删除、覆盖、修正、重标、重算
或选择性重跑其任何记录；当前复核仍为历史 `planned/recorded/raw=64/64/64`、
Agent 32 条配置前 schema/ValidationError、Agent provider/model `0/0`、
Workflow `32/32`，且该历史问题不被称作 Adaptive trajectory 实验失败。

V3-A0 Rescue 的 `V3_A0_RESCUE_GO` 结论保持不变；A0 历史报告和 ledger 未被
修改。V2 既有 `PREFER_WORKFLOW` 结论保持不变，本轮没有宣布采用 Adaptive
V3 或改变架构结论。

## Commits、工作树与停止边界

相关 commits：`743792e`（A0 implementation）、`c531a4c`（A0 report）、
`06b357f`、`b4a7d17`、`49bbfe9`（既有 M1 lineage）、
`a768cb13f54644ad4161969936b136f0560dfee0`（本轮 Settings / exact path /
canary implementation）。当前 `main` 工作树 clean，未 push。

本轮未执行也未授权：formal `-04`/`-05` measurement、Freeze、Locked Eval、
Live browser gate、Release Evidence、部署、push、PR。Development evidence
与本 Owner Gate 结果不是 Freeze、Locked、Release 或架构采用证据。

# V3 Development Repair Cycle Handoff

日期：2026-08-28

## 最终状态

`NO_GO_DIAGNOSTIC_OR_ENGINEERING_BLOCKER`

本文件是本轮 V3 Development Repair Cycle 的 blocker handoff，不是正式 `V3-DEV-EXEC-20260828-02` 测量报告。源码修复已经完成并形成 source commit；但诊断前置条件未满足，且实际 DeepSeek 调用数为 0，因此本轮不创建、不消费正式 `-02` 执行身份，不打开正式分母，也不产生新的质量、安全、token 或成本结论。

本轮没有修改 V2 历史证据、既有 Release Evidence 或旧报告，也没有发起部署、推送、PR、Freeze、Locked Eval、Live browser 或架构采纳决策。

## 起点与受保护证据

- 工作分支：本地 `main`。
- 起点提交：`a75529b231879682f809c8b531c9410362bf006c`。
- 起点前置 stash：`stash@{0}: On main: pre-v3-main-fast-forward-20260828`，本轮未修改、未删除、未应用后重存。
- 源码修复提交：`cf0abf041ab2f0d0ae55f3ad129474a1d051c844`，消息为 `fix: close V3 development repair blockers`。
- 工作树在源码提交后保持干净；本文件由后续 docs-only commit 添加。

以下 `V3-DEV-EXEC-20260828-01` 证据为 immutable history，本轮只校验，不重写、不重评分、不选择性重跑：

| 文件 | SHA-256 |
| --- | --- |
| `var/v3/development/V3-DEV-EXEC-20260828-01/authorization-package.json` | `e39312f795d616bd9ac6470b9d5727cddace28c366e3961f837c4340f43b1b2e` |
| `var/v3/development/V3-DEV-EXEC-20260828-01/budget-ledger.jsonl` | `cbeac4f19b1fb702e3eb85733586a782b0e6b9d9cf6d844d53166b42331b61f0` |
| `var/v3/development/V3-DEV-EXEC-20260828-01/execution-state.jsonl` | `3f9d16b1ba7067fdbabec662fc6269cf838e6518507c98c79e202009a40c7d29` |
| `var/v3/development/V3-DEV-EXEC-20260828-01/reports/V3-DEV-EXEC-20260828-01-REPORT.json` | `0bcdb1f6ba36742cc7efedff0f59f64c99cbfa3d1596425723682d0f3d083898` |
| `runs/*.json` 排序后逐文件哈希的聚合 | `aa3de0996b8799a0e78a91d5bfbc409560cbf0c97dad11f15303bf6541b491b1` |

受保护 run 文件数量仍为 64。聚合校验使用固定的排序后逐文件 SHA-256 聚合方式；没有改写任何旧 run、ledger 或报告。

## 历史 `-01` 结果（仅作为失败基线）

`V3-DEV-EXEC-20260828-01` 的原始分母为 64 个 run、32 个 case、Agent/Workflow 各 32 个 run。历史结果保持原样：

| 架构 | 质量通过 | 安全通过 | 主要失败 |
| --- | ---: | ---: | --- |
| Agent | 0/32 | 0/32 | `SELECTOR_SCHEMA_FAILURE` ×32 |
| Workflow | 14/32 | 30/32 | grader failure ×16；运行时 `CaseFactIntegrityError` ×2 |

历史 Workflow 的 16 个 grader failure 为：

- `GR-V3A-10` ×7：六个 guard-family case，以及 `v3a-stall-policy-conflict`；
- `GR-V3A-02` ×1：`v3a-snr-active-ticket`；
- `GR-V3B-02` ×8：全部 V3-B case。

两次 `CaseFactIntegrityError` 对应 POD reception/nonreception 场景，并保留在历史失败分母中。历史报告的 `architecture_conclusion` 为 `NOT_EMITTED`；历史 Agent 优于 Workflow 的结论没有被发出。历史成本也没有被当作零。

## 本轮允许范围内的源码修复

源码提交只覆盖本轮 V3 修复所需的实现与输入：

1. 将 guard seed 映射为确定的 `reason_code`，在 selector/provider/tool 之前完成阻断、安全停机与可恢复路径，并保持 Agent/Workflow 共用同一确定性运行时，selector 仍是唯一架构差异。
2. 收紧 selector 输出边界：支持受控的原生 tool call 与 OpenAI function-shaped call，拒绝多调用、错误工具名、非法参数、非对象参数和非 AI 消息，并保留可审计的 schema failure reason。
3. 将 grader verdict 作为受校验的持久化契约保存，记录 `grader_id`、`passed`、`triggered`、细节与 safety detail；重放时逐项核验，缺失或不一致即 fail-closed。
4. 修正 V3-B fact merge、policy/proof fixture 及多轮消息消费，增加 issue revision 的 trajectory boundary，使事实、问题、消费和 grader 只归属于对应轨迹。
5. 修正 real runner 的初始消息、case/run 时间顺序、跨 case 隔离、repeat、重启/replay 与实际消费映射；写入仍由确定性 executor 控制，模型始终不可见、不可调用写工具。
6. 增加独立、append-only、最多三次 admission 的诊断路径及安全投影；诊断身份与正式测量身份分离，诊断不会进入正式分母。

没有修改 V2 代码或历史输入，没有把 provider-free 预演升级成 Live 证据，没有调整 Gold、问题集、冻结历史或既有评分。

## 修复后验证

下表是本轮实际运行的验证，标签严格区分；其中 provider-free rehearsal 不是正式测量：

| 验证 | 结果 | 证据类别 |
| --- | --- | --- |
| 全量测试 | 238 collected，全部通过 | `contract` / `integration` |
| Ruff | `All checks passed!` | `static` |
| Mypy strict | 80 source files，无错误 | `static` |
| `after-sales-v3-eval validate` | 32 cases、32 inputs、计划 64；manifest 与 input digest 校验通过 | `contract` |
| provider-free production-path rehearsal | 64 runs、32 cases、`failed_runs=0`、`model_calls=0`、`provider_calls=0`、`actual_reads=233` | `mock` / preparation only |
| 针对修复路径的 production integration checks | V3-A 24 cases 与 V3-B 8 cases 无 grader failure | `integration` |
| 预算、重启/replay、tamper、write-once、全局 ceiling、per-run 第九次调用 | 通过现有全量测试与针对性检查 | `contract` / `integration` |
| diff whitespace check | 通过 | `static` |

预演没有创建正式执行包、formal ledger 或 formal report，也没有产生任何 architecture conclusion。`cost` 仍是未测量/不可用，不等于零。

## 诊断结果

本轮按独立诊断身份执行了一次 `diagnose` 命令；没有重跑：

- `diagnostic_identity`：`V3-DEV-DIAG-20260828-01`
- `diagnostic_label`：`real_external_diagnostic_not_measurement`
- 上限：最多 3 次真实 provider call；本次实际 provider/model call：`0/0`
- 实际 external admission：0；没有 DeepSeek 请求、tool execution 或正式 run 分母
- 安全环境投影：`live_mode=false`、`credential_present=false`、`model_match=true`
- 状态：`blocked`
- `reason_code`：`DIAGNOSTIC_CONFIGURATION_INVALID`
- 诊断 ledger：`var/v3/development-diagnostics/V3-DEV-DIAG-20260828-01/diagnostics.jsonl`
- 当前 ledger SHA-256：`75178413962b2a5abb17c30b7b861388718c445b96e79df61df8edd3e22636be`

诊断只记录了安全字段：没有 response payload、provider payload、chain-of-thought、system prompt、API key、未脱敏 PII 或 fault seed。由于 `live_mode=false` 且凭据存在性为 false，诊断在外部调用前 fail-closed；没有 Mock fallback。按授权规则，不能通过再次调用来绕过这个 blocker。

## 正式 `V3-DEV-EXEC-20260828-02` 状态

正式 `-02` 没有创建，也没有消费其 execution identity。受保护检查确认路径不存在：

`var/v3/development/V3-DEV-EXEC-20260828-02/`

因此正式状态必须写成：

| 项目 | 状态 |
| --- | --- |
| authorization package | `NOT_CREATED` |
| planned runs / recorded runs / raw runs | `NOT_CREATED / NOT_RECORDED / NOT_EMITTED` |
| Agent/Workflow quality 与 safety | `NOT_MEASURED` |
| provider/model calls、errors、timeouts、tokens | `NOT_APPLICABLE_FOR_UNRUN_FORMAL_MEASUREMENT` |
| cost | `NOT_MEASURED`；不能写成 0 |
| architecture conclusion | `NOT_EMITTED` |

原授权中规划但未执行的固定参数仍为：`deepseek-v4-flash`；32 cases、Agent/Workflow paired 64 runs、repeat 1；timeout 30s；Agent global ceiling 256、per-run ceiling 8；Workflow provider calls 0；output cap 512；质量阈值 1,000,000；token semantics 为 `cumulative_observed_total_tokens_post_response_stop`；hard token/overshoot false。上述参数不会被写成已执行证据。

## 未运行的后续 gate

本轮明确未运行：

- 正式 `V3-DEV-EXEC-20260828-02` Development measurement；
- Freeze、Locked Eval、Live browser vertical slice、Release Evidence；
- `ADOPT_AGENT` 或其他架构采纳结论；
- 部署、远程推送、PR 或外部发布。

下一次继续必须先在新的、经授权的诊断/执行身份下满足真实 provider 配置和 Live 入口条件；不得修改或重用本轮 blocker ledger，也不得把当前 `PREFER_WORKFLOW` 历史结论升级为 Agent 结论。

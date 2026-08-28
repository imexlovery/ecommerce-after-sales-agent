# V3 Selector Transport Repair Closeout

日期：2026-08-28
任务：`V3-SELECTOR-TRANSPORT-REPAIR-001`

## 结论

本轮源码修复完成并提交为 `7b4aa51`，但授权的真实 selector 诊断以
`V3_FINAL_NO_GO_SELECTOR_TRANSPORT` 结束：固定清单 12/12 输入均未通过，24/24
真实 provider admissions 均在 provider transport 层失败，未收到可解析的
structured response。因此不创建、不消费正式 `V3-DEV-EXEC-20260828-03`，不打开
64-run Development 分母，也不产生 Agent/Workflow 质量或架构比较结论。本轮停止在
V3 Development Results Owner Gate 之前的 selector transport no-go 边界。

这不是对旧结果的重评分。V2 的 `PREFER_WORKFLOW`、V3 旧诊断、旧正式执行和所有
历史失败证据均保持不变。

## 实施的边界修复

Live Agent selector 现在使用 `NextObservationCandidate` 的单一结构化输出，不再
同时绑定六个 READ_TOOLS：

`DecisionContext -> structured Candidate -> ObservationValidator -> authoritative NextObservation -> server-rebuilt AIMessage ToolCall -> LangGraph ToolNode -> GovernedToolExecutor`

Candidate 只允许 `CALL_TOOL`/`FINISH`、一个 allowlisted `tool_name`、对应的单一
Evidence Requirement 和受限 reason code。服务器从 trusted `DecisionContext` 重建
`order_id`、canonical `issue_type` 和实际工具参数；模型输入不能伪造这些字段。
结构化 response 的 raw `AIMessage` 在 parser 结果之前检查，空响应、非法 JSON、
未知 schema、多个 structured calls、额外字段、非法 evidence address 和 premature
finish 均 fail-closed。Mock 仍只用于离线/激活路径，未作为 Live 证据回退。

正式报告契约也增加了 `toolnode_reached`、selector schema/multiple-call 计数、
Agent provider-bound runs、各架构实际读取量、trajectory quality/safety，以及
每个 retained run 的 `case_endpoints`。

## 离线与合同验证

以下验证在 source commit `7b4aa51` 的内容上通过：

| 验证 | 结果 | 证据标签 |
| --- | --- | --- |
| 全量 pytest | 通过 | `contract` / `integration` |
| Ruff | `All checks passed!` | `static` |
| strict Mypy | 81 source files，无错误 | `static` |
| `after-sales-v3-eval validate` | 32 cases、32 production inputs、64 planned runs | `contract` |
| provider-free rehearsal | 64/64/64；Agent/Workflow actual reads 26/26；provider/model calls 0/0；端点 64 条 | `mock` / preparation only |
| V3 diagnostic manifest | 12 inputs、两类 issue、max 24 calls、digest 校验通过 | `contract` |
| `preflight` | `NO_GO_FORMAL_DEVELOPMENT_NOT_AUTHORIZED`；source clean；provider/model calls 0/0 | `operational` |
| `git diff --check` | 通过 | `static` |

Provider-free rehearsal 不是 Live 或 Development measurement，也没有产生架构结论。
成本仍为 `unavailable`，不写成零。

## 真实诊断

固定 manifest：

- identity：`V3-DEV-DIAG-20260828-04`
- manifest：[`evals/v3/diagnostic-manifests/V3-DEV-DIAG-20260828-04.json`](../../evals/v3/diagnostic-manifests/V3-DEV-DIAG-20260828-04.json)
- manifest digest：`4807a3c751e888bceaa9790d6a4c303a2bc18df15c97fff160f92c088ead69c4`
- 输入数：12；每输入最多 2 次；总 admissions 上限 24
- 诊断 ledger：`var/v3/development-diagnostics/V3-DEV-DIAG-20260828-04/diagnostics.jsonl`

最终安全投影如下：

| 字段 | 结果 |
| --- | ---: |
| status | `failed` |
| reason | `V3_FINAL_NO_GO_SELECTOR_TRANSPORT` |
| input_count / passed | `12 / 0` |
| provider admissions | `24 / 24` |
| provider-error completions | `24` |
| structured/schema failures | `0` |
| multiple-observation rejections | `0` |
| server message rebuilds | `0` |
| ToolNode reached | `0` |

ledger 还保留了首次模型构造时的 `DIAGNOSTIC_CONFIGURATION_INVALID` 阻塞事件；在
不修改依赖的情况下完成 transport 环境调整后，固定上限内的 24 次实际 provider
admissions 仍全部以 `DIAGNOSTIC_PROVIDER_ERROR` 结束。诊断只持久化了安全投影，
没有 provider payload、API key、system prompt、chain-of-thought、未脱敏 PII 或
fault seed。由于已达到 24 次上限，不能通过追加调用绕过 no-go。

## Formal Development 状态

本轮已将正式 identity 切换为 `V3-DEV-EXEC-20260828-03`，但由于诊断未通过，正式
authorization package 没有创建，正式执行没有开始：

| 项目 | 状态 |
| --- | --- |
| authorization package | `NOT_CREATED` |
| 32 cases / 64 paired runs | `NOT_EXECUTED` |
| Agent/Workflow quality and safety | `NOT_MEASURED` |
| provider/model calls for formal run | `0/0` |
| formal report | `NOT_EMITTED` |
| architecture conclusion | `NOT_EMITTED` |
| cost | `NOT_MEASURED` |

原 `V3-DEV-EXEC-20260828-01`、`V3-DEV-EXEC-20260828-02` 目录及
`V3-DEV-DIAG-20260828-01` 到 `-03` ledger 未被改写、重评分或选择性重跑。对旧证据
目录 319 个文件做的逐文件 SHA-256 排序校验在本轮前后相同。

## 未运行的 gate

本轮未运行 Freeze、Locked Eval、Live browser vertical slice、Release Evidence、
部署、push、PR、外部发布或 `ADOPT_AGENT` 决策；也没有修改 V2 的
`PREFER_WORKFLOW`。后续若要继续，必须由新的授权身份重新满足 provider transport
前置条件，不得复用或覆盖本轮 no-go ledger。

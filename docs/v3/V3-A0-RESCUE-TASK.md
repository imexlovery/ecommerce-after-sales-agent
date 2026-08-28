# V3-A0 Rescue 执行任务卡

```yaml
task_id: V3A0-RESCUE-DEV-001
task_status: AUTHORIZED
authorized_at: 2026-08-28
audited_source_revision: e5a123c073ba7d976531e763aa75f7bb0ac69829
construction_start_rule: clean HEAD containing this task card
execution_identity: V3-A0-RESCUE-20260828-01
provider: deepseek-v4-flash
live_selector_smoke_authorized: true
provider_call_ceiling: 6
provider_retry_policy: no_automatic_retry
provider_timeout_seconds: 30
output_token_cap_per_invocation: 512
formal_development_eval_authorized: false
freeze_or_locked_authorized: false
release_evidence_authorized: false
stop_checkpoint: V3-A0 Rescue Owner Gate
```

## 1. 任务目标

只验证当前 V3 Adaptive Core 的最小 Live vertical slice：

```text
Evidence Progress
-> Live LLM Selector
-> NextObservationCandidate
-> deterministic ObservationValidator
-> authoritative NextObservation
-> server-built single ToolCall
-> production LangGraph ToolNode
-> GovernedToolExecutor
-> Observation Router
```

本任务不证明 Agent 优于 Workflow，不重启 32-case/64-run
Development Eval，也不改写 V2 `PREFER_WORKFLOW` 或任何历史
V3 `NO_GO` 证据。它只回答：当前应用层 Candidate 协议能否在
可观测的 Live provider transport 上真正进入 ToolNode，以及 exact
retry 是否不经 Selector 相邻重建。

## 2. 必须保留的历史结论

1. `V3-DEV-EXEC-20260828-02` 的 25 个
   `SELECTOR_MULTIPLE_TOOL_CALLS` 失败保持不变；不重评、不重跑、
   不从分母删除。
2. `V3-DEV-DIAG-20260828-04` 的 24 个 provider-error admission
   保持不变；不在原 ledger 追加新运行。
3. 当前 V3 release candidate 仍为 `NO_GO`，V3 Freeze/Locked/Release
   仍关闭。
4. V2 `PREFER_WORKFLOW` 仍是当前唯一证据支持的架构推荐。
5. 本任务使用全新身份和 append-only 证据路径，不得复用或
   覆盖上述任何 identity。

## 3. WP-A0.1 — Provider transport 合同与可观测性

在发起任何 Provider 调用前，先执行零 Provider preflight：

- 检查 `pyproject.toml`/`uv.lock` 和当前 `langchain-deepseek`/HTTP
  transport 实现；
- 只记录 proxy 变量是否存在、scheme 和必要 dependency 是否存在；
  不得输出 proxy URL、credential 或 `.env` 内容；
- 明确项目采用 SOCKS proxy 还是经验证的 direct transport；
  不得靠临时 `env -u ...` 把未知直连当成修复；
- 若项目需要支持已配置的 SOCKS transport，使用 `uv add`
  声明最小直接依赖并更新 `uv.lock`；
- 在不发起网络请求的情况下验证 Live model/selector 可构建。

修复诊断投影，必须区分 budget admission、outbound attempt、
provider-completed response、provider HTTP error、local transport/configuration
error、timeout 和 Candidate boundary rejection。

只持久化安全元数据：异常类别、安全 error code、HTTP status（若有）、
token usage 和时间。不记录 raw provider payload、stack trace、prompt、key、
CoT、未脱敏 PII 或 fault seed。

## 4. WP-A0.2 — 真实 production-path smoke harness

新 harness 必须调用当前 production composition root 和已编译的
LangGraph。仅调用 `_server_message(observation)`、仅检查
`AIMessage.tool_calls`、伪造 ToolNode 返回、只跑 Validator/Router unit
test 或使用 Mock model，均不能算作 `ToolNode reached`。

`ToolNode reached` 至少必须由同一 run 的受信记录证明：

- Candidate 已解析且 Validator accepted；
- 服务器重建了唯一 ToolCall；
- LangGraph 路由到 `tools` 节点；
- GovernedToolExecutor 产生 typed `ToolResult`；
- actual read budget 按真实执行次数增加；
- result 进入 EvidenceProgressReducer 和 ObservationRouter。

可复用现有 fictional fixtures 和 deterministic fault injection；不新增任何
真实电商、物流或其他外部工具依赖。

## 5. WP-A0.3 — 三个固定 Live smoke

先建立小型 immutable manifest，绑定 source revision、fixture version、
input digest、provider/model、timeout、token cap 和下述三个 case。

### A0-01 Normal first observation

- 使用一个只有一个明确最小缺口的合法 Case；
- Live Selector 返回一个合法 Candidate；
- Validator 从 trusted context 重建参数；
- 真正进入第一个 ToolNode 并完成一次 read；
- `provider_calls=1`、`server_tool_call_count=1`、`actual_reads=1`。

### A0-02 Multiple evidence gaps

- 输入同时包含至少两个 applicable missing requirements；
- manifest 预先声明合法下一步集合，不预定 Workflow 的唯一工具；
- Selector 只需选中其中一个合法、未完成且能推进 progress 的
  observation；
- 服务器只重建和执行一个 ToolCall；
- 不得因 Selector 选择了另一个同样合法的观察而判失败。

### A0-03 Deterministic exact retry

- Live Selector 产生第一个合法 Candidate 并进入 ToolNode；
- 当地 deterministic fault 使 attempt 1 返回
  `retryable_error + unavailable + timeout`；
- Observation Router 产生 `retry_exact`；
- 下一步不得调用 Selector/model；
- 以相同 tool name、canonical args hash、trusted scope 和 source
  revision 重建 attempt 2；
- attempt 2 再次进入 ToolNode；两次实际执行均消耗 read
  budget；
- 证据必须包含 `retry_of_tool_call_id`、attempt number 和重建一致性。

## 6. Provider 调用预算与执行顺序

硬上限是 6 次 Selector/model/provider invocation attempts，包含所有失败。
LangChain/Provider 自动重试必须关闭。

1. 完成零 Provider preflight、本地修复和全部静态/无 Provider 验证。
2. 只运行 A0-01。
3. 若 A0-01 在收到 provider response 之前失败，停止其他
   case；根据已分类的本地 transport 原因最多修复一次，保留
   失败记录后以新 attempt 重运 A0-01。
4. 若收到 provider response，但当前 function-calling Candidate
   transport 仍因 multiple/empty/schema envelope 失败，禁止通过改
   Prompt 或选择性重试规避。允许实现一次受限的 JSON Candidate
   transport，仍由 Pydantic + ObservationValidator fail closed，并且真正
   ToolCall 仍必须由服务器构造后进入原生 ToolNode。
5. A0-01 通过后才能各运行一次 A0-02 和 A0-03。
6. 任何情况下不得超过 6 次 Provider attempts，不得选取最好的
   run，不得把 provider/tool/schema/timeout 失败从证据中删除。

A0-03 的第二次本地 read-tool 执行不是 Provider 调用；若它导致
第二次 Selector/provider 调用，该 case 直接失败。

## 7. 验证梯子

在 Live 之前至少执行：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-v3a0 uv run pytest \
  tests/unit/test_adaptive_core.py \
  tests/unit/test_v3_live_selector.py \
  tests/integration/test_investigation_service.py -q
UV_CACHE_DIR=/private/tmp/uv-cache-v3a0 uv run pytest -q
UV_CACHE_DIR=/private/tmp/uv-cache-v3a0 uv run ruff check .
UV_CACHE_DIR=/private/tmp/uv-cache-v3a0 uv run mypy src
git diff --check
```

如果修改 `pyproject.toml`/`uv.lock`，还必须验证项目 `.venv` 的依赖
一致性。不修改前端时不要运行或重建 UI。

Live 证据统一标记为：

```text
real_external_a0_smoke_not_development_measurement
```

当地 read tools 仍是 fictional fixture-backed；本任务不是 Live browser
vertical slice，也不是正式 Agent/Workflow measurement。成本没有可验证价格
基础时必须保持 `unavailable`。

## 8. GO / NO_GO 与停止规则

`V3_A0_RESCUE_GO` 必须同时满足：

- 三个固定 smoke 均在同一绑定 source revision 上通过；
- 至少一个 Live Candidate 走完 production graph 的第一个 ToolNode；
- multiple-gap case 只执行一个合法 observation；
- exact retry 的 Selector/provider invocation count 保持 1，read execution
  count 为 2，身份重建一致；
- 所有失败 attempts 保留，provider 硬上限没有超限；
- 没有安全不变量、确定性权威、历史证据或 V2 回归破坏。

任何一项不满足就记录 `V3_A0_RESCUE_NO_GO`。不得通过增加
Provider 预算、改 case、改期望工具、降低验证器或丢弃失败来制造
GO。

GO 也只表示 A0 Live vertical slice 成立。它不会改变 V3 release
`NO_GO`、重启 Development Eval、发出 `ADOPT_AGENT`、改变
`PREFER_WORKFLOW`，或自动进入 V3-B/Freeze/Locked/Live browser/Release。

## 9. 明确排除

本任务不得：

- 重构整个 Adaptive Core 或调整业务 Evidence Gate；
- 继续扩建 V3-B Case Facts、32-case matrix、paired grader 或 Dashboard；
- 新增工具、业务 issue、write action、Retrieval、Memory、MCP、
  multi-agent 或前端功能；
- 运行 Development/Locked Eval、Freeze、Live browser、Release Evidence、
  部署、push 或 PR；
- 修改、删除、重算、重标或选择性重跑任何历史 V2/V3
  ledger、run、report、Freeze 或 Evidence Pack。

## 10. 交付与 Owner Gate

新证据必须写入独立的 append-only 路径：

```text
var/v3/a0-rescue/V3-A0-RESCUE-20260828-01/
```

交付一个 clean local implementation commit、immutable smoke manifest/digest、
安全 ledger、三个 smoke 的 Candidate/Validator/ToolNode/Router 证据摘要、
`docs/v3/V3-A0-RESCUE-REPORT-20260828.md`、验证结果、受保护历史证据
未变检查，以及明确的 `V3_A0_RESCUE_GO` 或
`V3_A0_RESCUE_NO_GO`。

交付后立即停在 **V3-A0 Rescue Owner Gate**。后续任何 Development
Eval 或架构价值判断都需要新的 Owner 授权。

# V3-A1 施工任务单

~~~yaml
task_id: V3A1-ENGINEERING-DEV-001
task_status: AUTHORIZED
authorized_at: 2026-08-27T23:00:11+08:00
owner_review_package: V3-DESIGN-OWNER-REVIEW-001
target: Adaptive Investigation Core
implementation_grade: G1_local_portfolio_prototype
formal_eval_authorized: false
v3b_implementation_authorized: false
freeze_or_locked_authorized: false
stop_checkpoint: V3-A1 Engineering Gate
~~~

## 1. 任务目标

在不扩大业务范围、不改变确定性权威、不运行正式 Eval 的前提下，把
V2 的 Agent/Workflow 调查循环重构为一个共享 Adaptive Investigation
Core：

> Agent 与强 Workflow 只在 select_next_observation 的实现上不同；候选
> 之后的校验、ToolNode/工具治理、Evidence Progress、恢复、终止、
> Evidence Gate 和 Trace 全部共用。

本任务不是为了让 Graph 节点更多，也不以 ADOPT_AGENT 为目标。它只需
让 V3 的动态路径假设变成可实现、可恢复、可重建、可测试的数据与运行
时合同。

## 2. 开工前必读

按顺序完整阅读：

1. AGENTS.md；
2. PROJECT.md；
3. NON_GOALS.md；
4. docs/v3/00-owner-review.md；
5. docs/v3/01-design-and-data-contracts.md；
6. docs/v3/02-evaluation-gates-and-lineage.md；
7. docs/ARCHITECTURE.md；
8. docs/DOMAIN-CONTRACTS.md；
9. docs/EVALUATION.md；
10. pyproject.toml、uv.lock、frontend/package.json；
11. 当前 Agent graph、investigation service、strong workflow、governed
    tools、ToolCall persistence、events、Evidence Gate 和相关测试。

先检查 git status。当前工作树中的 Owner Review 文档属于已批准输入，
不得丢弃、覆盖或回退。

## 3. 施工范围

### WP-A1 — Typed contracts

实现并版本化：

- DecisionContext；
- NextObservationCandidate；
- 受信任的 NextObservation；
- EvidenceProgressSnapshot；
- RetryDirective；
- RecoveryDecision；
- DecisionTraceRecord、RecoveryTraceRecord、StateTraceRecord。

要求：

- Pydantic/typed contract 禁止额外字段；
- model candidate 不得携带可信 Case/Run/customer 身份；
- NextObservation 必须由确定性 Validator 绑定可信上下文；
- 不记录 chain-of-thought、原始 system prompt、provider payload、secret、
  PII、stack trace 或 fault seed。

### WP-A2 — 共享 selector/runtime 边界

建立一个共享的 selector 接口：

~~~text
select_next_observation(DecisionContext) -> NextObservationCandidate
~~~

提供：

- Agent selector：通过当前真实 LangGraph/LangChain 模型路径生成候选；
- Workflow selector：通过强确定性规则生成同一候选合同。

两者必须进入同一个：

- Observation Validator；
- ToolNode + GovernedToolExecutor 执行路径；
- EvidenceProgressReducer；
- Observation Router；
- Evidence Gate；
- Trace writer。

禁止保留或新增 Workflow-only 的旁路执行器。不得为 Agent 提供更多
上下文、工具、预算、重试或隐藏 Fixture 事实。

### WP-A3 — Evidence Progress rebuild

实现纯确定性 Reducer：

- 固定输入为 Case/Run scope、canonical issue 和同一 requirement
  registry；
- 动态输入仅来自有序 ToolCall、typed result envelope、source version
  和 EvidenceRef；
- successful absent 可以满足相应 requirement；
- unavailable 只能成为 retry_pending 或 unavailable_final；
- result hash、EvidenceRef、source version 或成功结果冲突时 fail closed；
- snapshot_hash 在在线归约、离线 replay 和进程重启后必须一致；
- LangGraph messages/checkpoint 不得是重建权威。

可以持久化 derived snapshot 便于 Trace，但 canonical ToolCall/EvidenceRef
历史必须能独立重建它。

### WP-A4 — Router、exact retry 与 guards

实现确定性 Observation Router，路由仅限：

- replan；
- retry_exact；
- finalize；
- safe_stop。

exact retry 必须：

- 只处理 retryable_error + unavailable + attempt 1；
- 成为同 Run 下一次实际 read execution；
- tool name、canonical args、trusted scope、source version 完全一致；
- 不插入 selector/model turn；
- 两次实际执行都消耗 read budget；
- 第二次失败变为 unavailable_final；
- source version 在 retry 前改变时不得执行旧 retry，必须 safe stop。

guards 必须实现：

- early stop：gate-ready 后禁止更多 selector/read；
- premature finish：第一次允许一个纠正 turn；
- stuck：相同 progress 下再次出现同 fingerprint，或连续两次无进展，
  进入 safe stop；
- budget：保留现有 8/16 planning 与 6 actual reads 上限，耗尽后不再
  调模型。

Router 不得选择新的业务工具、决定 Proposal 资格或替代 Evidence Gate。

### WP-A5 — 最小 Trace 与恢复

持久化最小 decision/recovery/state trace：

- 每个 selector turn 必须有 validated 或 rejected decision record；
- 每次 ToolCall completion 必须能关联 progress reduction 和 recovery
  route；
- state trace 记录 select/validate/execute/reduce/route/finalize/
  safe_stop/terminal 转移；
- canonical record 必须先持久化再投影到 SSE；
- replay 只能重放，不得重新执行 model/tool/Gate/Action；
- checkpoint 与业务数据库冲突时，业务数据库和重建结果胜出并记录
  deterministic recovery reason。

P0 不修改 Trace UI。只完成数据合同、持久化、API/event 安全投影与测试。

### WP-A6 — Prompt 与现有路径收口

把 investigation prompt 从固定工具 recipe 收窄为：

- 目标；
- Evidence Requirements；
- Tool Constraints；
- Safety Rules。

不得继续用 Prompt 指定完整查询顺序或 retry 流程。确定性 exact retry
属于 Router。

同时收口现有 Agent/Workflow 中重复的 retry、early-stop 和 evidence
recipe。Case、Run、Proposal、Action、transaction、confirmation 和
write/read-back 生命周期不得迁入 Graph。

### WP-A7 — 工程验证

为 docs/v3/02-evaluation-gates-and-lineage.md 第 6、13 节中的 V3-A
测试 ID补齐 unit、contract、integration、mock 和 replay 覆盖。

至少验证：

- candidate/normalized schema 与 trusted rebinding；
- shared runtime identity 和 selector-only difference；
- success/failure/restart 三类 progress rebuild；
- exact retry adjacency、identity、budget 和 exhaustion；
- early-stop/premature-finish/stuck/budget/source-change guards；
- trace completeness/redaction/persist-before-projection；
- V2 回归测试；
- V2 Freeze、raw reports、Release Evidence、Evidence Pack 和历史失败
  未改变。

## 4. 明确排除

本任务不得：

- 实现 V3-B CaseFactAssertion/CaseFactSnapshot；
- 新增长期 Memory、MemoryStore、用户画像或向量化对话事实；
- 扩建 Retrieval、Query Rewrite、embedding/reranker；
- 引入 MCP、多 Agent、Monitor Agent、开放域或权威 LLM Judge；
- 修改前端产品功能或提前建设 P1 Trace UI；
- 新增业务问题、工具、写动作或真实外部集成；
- 修改现有 Evidence Gate、Proposal、confirmation、idempotency、
  read-back 的权威边界；
- 执行 Development Eval、Live Pilot、Freeze、Locked Eval 或生成任何
  release/trusted evidence；
- 删除、覆盖、重算、重标或选择性重跑 V2 证据；
- push、部署或创建 PR。

## 5. 施工顺序

1. 基线审计并输出受影响模块图；
2. typed contracts + pure Validator/Reducer/Router；
3. shared selector/runtime composition；
4. retry/guards；
5. trace persistence/replay；
6. prompt 与 Agent/Workflow 收口；
7. unit/contract tests；
8. integration/mock/replay tests；
9. 全量 V2 regression；
10. 受保护证据检查；
11. 更新施工报告并停在 V3-A1 Engineering Gate。

普通测试失败可自主修复。若发现设计与现有硬 invariant 冲突、必须改变
业务权威、必须修改 V2 evidence，立即停止并请求 Owner。

## 6. 验证命令

优先使用仓库现有 uv 环境：

~~~text
uv run pytest
uv run ruff check .
uv run mypy
git diff --check
~~~

若新增迁移，必须同时验证 upgrade 路径、现有数据兼容和重建；不得重置
用户本地数据库作为替代。前端未改时不要求构建；若安全投影类型迫使前端
类型同步，则仅做最小兼容修改并运行现有 typecheck/build，不做 UI 设计。

不要运行 after-sales-eval 的 Development/Locked 命令。

## 7. V3-A1 Engineering Gate

GO 必须同时满足：

- docs/v3/02-evaluation-gates-and-lineage.md 第 6 节 11 项全部通过；
- TEST-V3A-FAIR、TRACE、REBUILD、RETRY、GUARD 合同有可定位测试；
- 现有 V2 tests、Ruff、strict Mypy 通过；
- 无正式 Eval、Live、Freeze、Locked 或 Release Evidence 产物；
- V3-B 和 P1 UI 未提前实现；
- V2 受保护证据路径无 diff；
- 施工报告明确列出实现、测试标签、未执行 Gate 和残余风险。

任一项失败即 NO-GO。不得通过删测试、改 Manifest、放宽 invariant 或
选择性报告来制造通过。

## 8. 停止与交付

通过工程 Gate 后立即暂停，向 Owner 提交：

- 变更模块与核心边界；
- 测试命令和精确结果；
- V3-A1 Gate 逐项结果；
- 受保护证据未变证明；
- 是否存在设计偏差；
- V3-B0 是否满足进入条件。

不得自动进入 V3-B0/B1 或任何 Eval 阶段。

## 9. 体量参考

当前检出基线约为：

- Python 产品源码 16,401 行；
- Python 测试 5,550 行；
- 前端源码/样式 4,718 行。

V3-A1 预计涉及约 10–16 个后端/测试文件，新增或实质改写约
3,000–5,000 行，约等于当前 Python 产品+测试体量的 14%–23%。这是
中等规模的核心编排重构，不是整项目重写。

完整 V3（A1、B1、Development/Locked Eval 合同与证据）预计累计
5,000–8,000 行新增或实质改写，约为当前 Python 产品+测试体量的
23%–37%。这些数字只是 checkout-time 施工估算，不是验收指标；真正
风险集中在共享权威、rebuild/replay 和公平对照正确性。

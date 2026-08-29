# Portfolio / Business Refactor Goals

```yaml
program_id: PORTFOLIO-BUSINESS-REFACTOR-001
document_status: owner_delegated_milestone_plan
product_grade: G1_local_portfolio_prototype
risk_tier: T1_synthetic_low_external_impact
delivery_posture: portfolio_prototype_hardening
milestones:
  - PBR-P0-001
  - PBR-P1-001
program_start_authority: owner_dispatches_P0_task_in_a_new_session
p1_start_authority: owner_accepts_P0_then_dispatches_P1_task
formal_eval_authorized: false
live_provider_authorized: false
freeze_locked_release_authorized: false
```

## 1. 文档定位

根目录 `PROJECT.md` 继续作为项目唯一总账，记录长期目标、权威边界、
架构结论和追加式决策历史。它不再承担本轮全部施工细则，避免把已有的
580 余行历史继续扩成难以执行的混合文档。

本文件是当前 Portfolio / Business Refactor 的目标合同；根目录
`STATUS.md` 是唯一实施状态面板；P0/P1 任务卡分别定义两次长程施工。
这些文档的创建只发布计划，不自动启动任何实现。

建议的新 Session 启动顺序：

1. `AGENTS.md`
2. `PROJECT.md`
3. `NON_GOALS.md`
4. 本文件
5. `STATUS.md`
6. 当前被 Owner 发布的唯一任务卡
7. 任务卡列出的架构、领域、产品和代码文件

若文档冲突，按以下优先级处理：当前 Owner 明确指令 > `AGENTS.md` 与
根项目硬约束 > `PROJECT.md`/`NON_GOALS.md` > 本目标合同 > 任务卡中的
实施建议。历史 Freeze、Locked、Evidence Pack 和正式 Eval 产物不可由
本轮文档覆盖。

## 2. 项目目标

把仓库从“Agent 工程实验挂着两个物流 Case”重心，调整为：

> 一个清晰、可信、可演示的小型电商物流售后环境；用户能看到系统如何
> 查询订单、包裹、轨迹、签收凭证、承运商异常和售后政策，并得到解释、
> 等待、追问、物流核查或人工升级。Agent 工程和治理机制作为支撑层存在，
> 不再主导第一屏业务叙事。

成功后的默认故事是：

```text
Customer
  -> Triage
  -> Investigate: Order / Shipment / Tracking / POD / Carrier / Policy
  -> Deterministic Decision and Evidence Gate
  -> ANSWER / WAIT / CLARIFY / INVESTIGATE / ESCALATE
```

## 3. 固定边界

### 3.1 冻结，不在 P0/P1 重做

- V3 Adaptive Agent 与 Strong Workflow 的底层编排合同；
- LangGraph、原生 tool calling、`ToolNode` 和现有六个只读 Tool；
- Authorization、Policy Resolver、Evidence Gate、Proposal、精确确认、
  Idempotency、Read-back verification、事件持久化和 Replay；
- `CaseState`、`CaseOutcome`、`RunState`、`ProposalState`、`ActionState`
  的分离；
- `absent` 与 `unavailable` 的语义区别；
- 现有两种 `IssueType`：`signed_not_received`、`stalled_tracking`；
- V2/V3 已有 Eval、Freeze、Locked、Release、Evidence Pack 及其失败记录；
- 当前有效架构结论 `PREFER_WORKFLOW`。

### 3.2 本轮明确不做

- 不增加退款、退货、支付、仓储、库存或完整售后平台；
- 不增加第三个 `IssueType`；
- 不做多 Agent、新 Agent Pattern、新治理层或新版本协议；
- 不修改旧 Locked 数据、阈值、denominator 或结论；
- 不为了让 Agent 获胜设计场景；
- 不运行 Live Provider、Development/Freeze/Locked Eval 或 Release Evidence；
- 不部署、push、创建 PR 或发布外部系统；
- 不引入真实客户、订单、承运商或政策数据。

`delivery/carrier exception` 在本轮是物流调查中的证据维度和场景标签，
不是第三条业务主线。区域异常、天气/运力告警和预计恢复时间主要服务于
`stalled_tracking`，必要时也可解释已签收争议，但不形成新路由。

## 4. 目标业务世界

### 4.1 Canonical synthetic dataset

P0 建立版本化、可读、可校验的 `business-demo-v1` 数据集。目标规模固定为：

| 实体 | 目标数量 | 业务要求 |
|---|---:|---|
| customers | 20 | 全部虚构，至少覆盖多个地区和服务等级 |
| orders | 40 | 34 个单包裹、4 个双包裹、2 个三包裹订单 |
| shipments | 48 | 包裹是物流调查的最小观察对象，仍归属一个订单 Case |
| tracking events | 132 | 包含正常、签收、停滞、恢复、异常等轨迹 |
| delivered shipments | 20 | 为 POD found/absent 形成明确分母 |
| delivery proof records | 14 | 其余 6 个已签收包裹以“成功查询无记录”表达 `absent` |
| carrier alerts | 8 | 包含 active/resolved 和明确恢复窗口 |
| investigation cases | 8 | 6 个 active、2 个 closed；支持重复咨询和防重复动作 |
| fault profiles | 6 | 只用于可重复地注入 transient/persistent unavailable |
| runtime policy clauses | 10 | 面向业务演示的最小政策集，不替换旧 Eval 语料 |

推荐的 canonical 文件布局如下；实现可按现有 adapter 约束微调字段名，但
不得改变实体边界或数量合同：

```text
data/business-demo-v1/
├── manifest.json
├── customers.csv
├── orders.csv
├── shipments.csv
├── tracking_events.csv
├── delivery_proofs.csv
├── carrier_alerts.csv
├── investigation_cases.csv
├── fault_profiles.json
├── scenario_catalog.json
└── policies/
```

CSV/JSON 是可审阅的 canonical source；SQLite 只作为可重建的运行时种子和
状态存储，不成为第二份手工维护的业务真相。旧 `fixture-v1`、旧 Eval 专用
fixtures 和历史 artifacts 保持原样，测试需要旧数据时必须显式选择旧身份。

`POD absent` 必须由成功查询但找不到对应 proof 行得到；`unavailable` 只能
由故障注入或真实 adapter 失败得到，禁止用空行、缺字段或占位字符串模拟。

### 4.2 用户级 outcome projection

新增一个确定性的展示投影 `CustomerDisposition`：

| 值 | 用户含义 | 典型内部依据 |
|---|---|---|
| `ANSWER` | 已能直接解释、拒绝越权请求或说明无需动作 | facts resolved / safe refusal / closed duplicate |
| `WAIT` | 当前应等待，并给出原因、SLA 或下一更新时间 | within SLA / active carrier recovery / active case / retry later |
| `CLARIFY` | 缺少一项客户能够补充的业务事实 | bounded business clarification |
| `INVESTIGATE` | 建议或已经发起包裹/配送核查 | eligible proposal / confirmed ticket creation |
| `ESCALATE` | 证据冲突、长期不可用或风险超出自动处理范围 | human-support decision |

它只是由确定性决策 reason、Proposal 和 Action 结果派生的客户展示层，绝不
替换或合并现有五类状态机。LLM 可以生成文案，但不能决定 disposition。
`create_logistics_investigation_ticket` 仍是 `INVESTIGATE` 背后的内部动作，
不再被包装为产品唯一价值终点。

## 5. 场景矩阵

### 5.1 `signed_not_received`

| 状态组合 | 首选 disposition | 核心说明 |
|---|---|---|
| POD 为本人/家庭成员，客户尚未确认周边 | `CLARIFY` | 询问是否核对具体位置或收件人 |
| POD 为前台/代收点/快递柜，客户尚未核对 | `ANSWER` 或 `CLARIFY` | 先给出可理解的代收信息，再补问必要事实 |
| POD 为代收位置，客户明确否认该位置存在 | `ESCALATE` | 业务事实与系统证据冲突，不伪造确定结论 |
| POD 成功查询无记录，其他证据完整且政策允许 | `INVESTIGATE` | 自然进入核查 Proposal/Confirmation |
| POD transient unavailable，精确 retry 成功 | 继续原路径 | 两次真实执行均计入预算 |
| POD 持续 unavailable | `WAIT` 或 `ESCALATE` | 不得当作 absent；按现有 Gate reason 投影 |
| 已有 active investigation | `WAIT` | 展示当前阶段和下一更新时间，不重复创建 |
| 订单不属于当前客户 | `ANSWER` | 安全拒绝访问，不泄漏订单事实 |
| 承运商区域异常仍在恢复窗口 | `WAIT` | 解释异常和 SLA；carrier 不是新 IssueType |

### 5.2 `stalled_tracking`

| 状态组合 | 首选 disposition | 核心说明 |
|---|---|---|
| 用户说“未发货”，订单实际已发货且轨迹正常 | `ANSWER` 或 `WAIT` | 纠正陈述，并按 SLA 解释当前状态 |
| 轨迹最近更新且仍在 SLA | `WAIT` | 给出预计等待窗口 |
| 超过停滞阈值，证据完整，无 active case | `INVESTIGATE` | 进入物流核查，而不是机械答复 |
| active carrier alert 且仍在恢复窗口 | `WAIT` | 展示异常范围与恢复时间 |
| carrier alert 已结束但轨迹仍停滞 | `INVESTIGATE` | 继续按政策判断核查资格 |
| 已有 active investigation | `WAIT` | 展示已有进度和下一更新时间 |
| timeline 持续 unavailable | `WAIT` 或 `ESCALATE` | 保持未知语义，禁止推断为无轨迹 |
| 系统已签收而用户声称仍在运输 | `ANSWER`/转入现有 SNR 路径 | 使用既有 issue 修订能力，不增加 IssueType |

### 5.3 Split shipment 组合场景

Split shipment 不是第三个 IssueType。一个订单可以包含多个包裹，例如：

```text
Package A -> delivered
Package B -> in_transit within SLA
Package C -> stalled beyond SLA
```

系统需要先展示订单级包裹摘要，再把证据、解释和潜在核查动作绑定到正确
包裹。`InvestigationCase` 仍是一位客户、一个已授权订单、一个 primary
issue；不得为了 split shipment 新建多 Agent 或跨订单 Case。

## 6. 两个里程碑

### P0 — Business Foundation and One Complete Slice

P0 建立可信业务底座：版本化 dataset、运行时 loader、客户级 disposition、
业务优先的 README/界面，以及一个完整 Mock 浏览器纵向切片。P0 结束时项目
必须已经“像一个物流售后作品”，但不要求展示全部复杂状态。

详细合同：`docs/portfolio-business-refactor/P0-TASK.md`。

### P1 — Complex Scenario Coverage and Portfolio Story

P1 只在 Owner 明确验收 P0 后启动。它完成 split shipment、用户/系统冲突、
existing case、carrier exception、absent/unavailable、exact retry 等组合状态，
交付三条可重复 Demo 和最终 README 信息架构。

详细合同：`docs/portfolio-business-refactor/P1-TASK.md`。

### 里程碑控制

- Codex 可在单个里程碑内自主完成普通实现选择和修复测试失败；
- 里程碑内不设置普通 Owner 小关卡；
- 只有真实范围冲突、不可逆外部动作或无法绕过的外部资源阻塞才提前暂停；
- 完成实现与验证后，Codex 只能把里程碑标为
  `AWAITING_OWNER_ACCEPTANCE`；
- 只有 Owner 可以写入 `OWNER_ACCEPTED`；
- P0 Owner 验收不会自动启动 P1，仍需 Owner 在新 Session 发布 P1 任务卡。

## 7. Program-level 验收标准

| ID | 验收要求 | 里程碑 |
|---|---|---|
| PBR-AC-01 | 默认运行时使用版本化 `business-demo-v1`，数量与关系校验通过 | P0 |
| PBR-AC-02 | `CustomerDisposition` 五值由确定性代码投影，不合并现有状态机 | P0 |
| PBR-AC-03 | README 第一屏先讲客户问题、调查来源和五类结果 | P0 |
| PBR-AC-04 | 至少一个 Mock + real-local-retrieval 浏览器纵向切片完成到用户结果 | P0 |
| PBR-AC-05 | split shipment 能定位和展示每个 package 的不同状态 | P1 |
| PBR-AC-06 | 两个 IssueType 的组合矩阵覆盖 conflict、existing case、carrier、absent/unavailable 和 retry | P1 |
| PBR-AC-07 | 五类 disposition 均有 API/界面/自动化验证，且解释包含必要业务依据 | P1 |
| PBR-AC-08 | 三条主 Demo 可重复运行，Failure Lab 能演示 unavailable/retry 而不污染默认故事 | P1 |
| PBR-AC-09 | 旧 fixtures、旧 Eval identities、Freeze/Locked/Release artifacts 无内容修改 | P0/P1 |
| PBR-AC-10 | 不调用真实 provider，不生成新正式 Eval 或架构胜负结论 | P0/P1 |

## 8. P2 暂缓项

“复杂组合场景下 Agent vs Workflow 是否出现局部价值”是合理的独立追加
实验，但不属于 P0/P1。P1 完成后，Owner 可另行决定是否建立 P2。任何 P2
都必须使用新的 experiment identity 和新数据集，不覆盖原 Locked Eval，
并同时报告 success、evidence coverage、invalid/redundant calls、duplicate
actions、latency、tokens 和 cost availability。P0/P1 不得预埋为了让 Agent
获胜的特例或阈值。

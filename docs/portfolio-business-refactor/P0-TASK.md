---
task_id: PBR-P0-001
task_status: OWNER_ACCEPTED
program_id: PORTFOLIO-BUSINESS-REFACTOR-001
milestone: P0
implementation_grade: G1_local_portfolio_prototype
execution_authority: owner_must_publish_this_task_in_a_new_session
provider_calls_authorized: false
formal_eval_authorized: false
freeze_locked_release_authorized: false
p1_authorized: false
stop_checkpoint: P0 Portfolio Foundation Owner Acceptance
---

# P0 Task — Business Foundation and One Complete Slice

## 0. 给执行 Session 的指令

在当前仓库一次性完成本任务的全部授权范围。除真实范围冲突、不可逆外部
动作或无法绕过的外部资源阻塞外，不设置中途 Owner Gate；普通实现选择、
局部重构和测试修复由执行者自主完成。

开始时把根 `STATUS.md` 更新为 `P0=IN_PROGRESS`，每完成一个 work package
追加日志。全部验收通过后生成交付报告，把状态更新为
`P0=AWAITING_OWNER_ACCEPTANCE`，然后停止。不得自行写
`P0=OWNER_ACCEPTED`，不得进入 P1。

## 1. 目标

把默认 Demo 从少量硬编码 fixture 升级为可见、版本化、可校验的小型物流
售后业务环境，并建立五类客户结果投影。P0 必须交付一个完整 Mock 浏览器
纵向切片，使 README 第一屏和运行中的产品都先讲业务，再讲 Agent 工程。

P0 不是 Agent 架构升级、正式 Eval 或完整复杂场景交付。

## 2. 开工前必读与基线检查

按顺序完整阅读：

1. `AGENTS.md`
2. `PROJECT.md`
3. `NON_GOALS.md`
4. `docs/portfolio-business-refactor/GOALS.md`
5. `STATUS.md`
6. `README.md`
7. `docs/PRODUCT-SPEC.md`
8. `docs/DOMAIN-CONTRACTS.md`
9. `docs/ARCHITECTURE.md`
10. `docs/UX-SPEC.md`
11. `docs/IMPLEMENTATION-SOURCE-MAP.md`
12. 与 fixtures、tools、Evidence Gate、API DTO、frontend 和 surface E2E
    直接相关的当前代码与测试

开工前记录 `git status --short --branch`、HEAD 和已有用户改动。不得丢弃、
覆盖或顺手清理不属于本任务的改动。不得读取或打印 `.env`。

## 3. 硬约束

- 保持两个 `IssueType`，不增加 carrier/delivery exception 路由；
- 保持现有 Agent/Workflow、六个只读 Tool 和写执行边界；
- 不合并任何现有状态机；
- `CustomerDisposition` 只能是确定性展示投影；
- 每个 order-scoped read 继续统一授权；
- `absent` 是成功观察，`unavailable` 是未知；
- Model 仍不能看到或调用写 Tool；
- 旧 fixtures、Eval cases、Freeze/Locked/Release/Evidence Pack 内容不可修改；
- 不运行 Live provider、正式 Eval、Freeze、Locked 或 release scripts；
- 不增加新数据库系统、外部服务、真实数据或依赖，除非已有实现无法满足且
  形成明确 scope conflict；发生此类冲突应暂停，而不是自行扩架构。

## 4. Work packages

### WP-P0-1 — 建立可验证的数据合同

在 `data/business-demo-v1/` 建立 canonical synthetic dataset 和 manifest：

- 20 customers；
- 40 orders；
- 48 shipments，其中 34 个单包裹、4 个双包裹、2 个三包裹订单；
- 132 tracking events；
- 20 个 delivered shipments；
- 14 条 POD 记录，另外 6 个 delivered shipment 通过无记录表达 `absent`；
- 8 carrier alerts；
- 8 investigation cases，其中 6 active、2 closed；
- 6 fault profiles；
- 10 条面向运行时业务演示的 policy clauses。

数据必须全部虚构、时间关系一致、ID 唯一、外键可解析、订单归属明确、
包裹序号稳定、时间含时区。`scenario_catalog.json` 只标注可演示组合与期望
disposition，不成为运行时业务真相。

添加一个快速、确定性的 dataset validator 与测试，至少校验数量、唯一性、
外键、订单/包裹基数、delivered/POD 分母、active/closed case 分布、alert
窗口以及 manifest 版本。不得增加 checksum/evidence-pack 机制。

### WP-P0-2 — 接入默认运行时

用现有 adapter/composition root 接入 `business-demo-v1`：

- CSV/JSON 是只读 seed；现有 SQLite 仍处理可变 Case/Proposal/Action 状态；
- 默认本地 Demo 使用新 dataset；
- 现有测试和历史评测若依赖旧 fixture，必须显式选择旧 fixture identity；
- 读取接口返回稳定的 order/shipment/package 摘要，为 P1 的 split shipment
  预留数据，但 P0 不实现完整 split 决策；
- 扩展现有 tool payload 优先于增加新 Tool；本任务不得增加第七个 read Tool；
- 运行时重置必须可重复得到同一 seed，不复制手工维护的第二份数据。

### WP-P0-3 — 增加 `CustomerDisposition` 投影

在项目拥有的 domain/response projection 中增加精确五值：

```text
ANSWER
WAIT
CLARIFY
INVESTIGATE
ESCALATE
```

要求：

- 从 Evidence Gate decision reason、Case/Proposal/Action 的结构化状态确定性
  派生；不得让 LLM 输出成为权威；
- 不替换 `CaseOutcome` 或 `EvidenceGateDecision`；
- active existing case/within-SLA/retry-later 能投影为 `WAIT`；
- bounded clarification 投影为 `CLARIFY`；
- eligible proposal 和成功创建 investigation ticket 投影为 `INVESTIGATE`；
- human-support/conflict 投影为 `ESCALATE`；
- 已解释完成、无需动作和安全越权拒绝投影为 `ANSWER`；
- API、事件/response DTO、TypeScript 类型和客户界面使用同一枚举；
- 文案必须说明“发生了什么、为什么、下一步”，而不是只显示内部工单状态。

若一个旧 gate reason 同时可能映射两个 disposition，先使用现有结构化业务
reason 区分；不得通过解析自然语言文案推断。

### WP-P0-4 — 重做业务第一屏

调整 README 第一屏和默认客户界面：

- README 开头用一段客户故事和简图说明 Order / Shipment / Tracking / POD /
  Carrier / Policy 的调查路径；
- 五类 disposition 在第一屏可见；
- “创建物流调查工单”降为 `INVESTIGATE` 的内部动作；
- 工程细节如 resolver hash、canonical authority、Freeze/Locked lineage 后移到
  技术和评测章节，不能删除历史结论；
- 显示当前 synthetic customer、可访问订单和包裹摘要，使数据世界可见；
- 示例控件仍只填充 composer，绝不直接选路由；
- Developer Trace 保留，但默认客户结果先显示业务依据与下一步。

只做支撑 P0 纵向切片所需的界面调整，不在 P0 重做完整视觉系统。

### WP-P0-5 — 完成一个浏览器纵向切片

使用 `LLM_MODE=mock` 与真实本地 Policy retrieval，完成并自动验证至少一个
端到端路径：客户报告已签收未收到 -> 查订单/包裹/轨迹/POD/政策 -> POD
成功查询无记录 -> `INVESTIGATE` -> exact Proposal confirmation -> 幂等创建
investigation ticket -> read-back -> 用户看到核查已发起及下一步。

该路径必须证明：

- 使用 `business-demo-v1`，不是测试内临时拼装数据；
- `provider_calls/model_calls=0/0`，并清楚标注
  `mock_llm + real_local_retrieval + surface_e2e`；
- 浏览器刷新/replay 不重复执行写动作；
- UI/API 展示 `customer_disposition=INVESTIGATE`；
- 至少再以 contract/integration 测试覆盖 `ANSWER`、`WAIT`、`CLARIFY`、
  `ESCALATE` 的确定性映射；全矩阵浏览器展示留给 P1。

若当前项目已有可复用的 Playwright/fixture/reset 流程，必须复用，不创建
第二套 demo harness。

### WP-P0-6 — 文档同步与交付报告

更新直接受影响的产品、领域、API、UX、配置和启动文档；在
`docs/portfolio-business-refactor/P0-DELIVERY-REPORT.md` 记录：

- 实现范围与未实现的 P1 范围；
- dataset 实际数量与 validator 结果；
- disposition 映射与未合并状态机证明；
- 所有验证命令、精确结果和证据标签；
- Mock 浏览器纵向切片步骤与结果；
- provider/model calls 为零的证据；
- 旧 artifacts 未修改的路径检查；
- 残余风险、已知限制和 P1 入口条件；
- 当前 `git status`，但不要求 push/PR。

同步根 `PROJECT.md` 当前状态和 `STATUS.md`，但不得重写历史决策或旧结果。

## 5. 验证要求

执行最小且足够覆盖本次广泛数据/domain/API/UI 变更的验证。至少包括：

1. dataset validator 和针对 loader/reseed 的测试；
2. CustomerDisposition unit/contract tests；
3. authorization、absent/unavailable、Proposal/Confirmation、idempotency、
   replay 的相关回归测试；
4. Python full pytest、Ruff、strict Mypy；
5. `uv lock --check` 与 `uv pip check --python .venv/bin/python`；
6. frontend lint/typecheck/test/build，以仓库现有 script 为准；
7. P0 指定的 surface E2E。

不得因为旧测试失败就删测试、放宽 invariant、修改 Locked manifest 或选择性
报告。无法修复的真实阻塞写入 `STATUS.md` 后暂停。

## 6. P0 Acceptance Gate

以下全部为真才可提交 Owner 验收：

- `PBR-AC-01` 至 `PBR-AC-04`、`PBR-AC-09`、`PBR-AC-10` 通过；
- dataset 数量和关系与目标合同完全一致；
- 默认 Demo 确实读取 `business-demo-v1`；
- 五类 disposition 由确定性代码投影且 API/frontend 类型一致；
- P0 浏览器纵向切片完整通过并正确标注为 Mock；
- README 第一屏业务优先，旧 `PREFER_WORKFLOW` 结论仍可找到且未被改写；
- 旧 Eval/Freeze/Locked/Release/Evidence Pack 无内容 diff；
- 所有要求的验证通过，失败与未执行项完整报告；
- 未调用真实 provider，未运行正式 Eval 或 release gate；
- `STATUS.md` 已为 `P0=AWAITING_OWNER_ACCEPTANCE`、P1 仍 blocked。

任一项失败即不能声称 P0 完成。

## 7. 停止与交付

通过 P0 Gate 后立即停止，向 Owner 提交：

- 业务结果摘要；
- 主要变更文件；
- 测试命令与精确结果；
- P0 Acceptance 逐项结果；
- `P0-DELIVERY-REPORT.md` 路径；
- 未执行的 P1、Live、Eval、Freeze、Release 边界。

不得自动进入 P1，不得自行修改 `OWNER_ACCEPTED`。

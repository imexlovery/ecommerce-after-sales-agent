---
task_id: PBR-P1-001-R2
task_status: AWAITING_OWNER_ACCEPTANCE
program_id: PORTFOLIO-BUSINESS-REFACTOR-001
milestone: P1
implementation_grade: G1_local_portfolio_prototype
execution_authority: owner_published_final_closeout_in_current_session
provider_calls_authorized: false
formal_eval_authorized: false
freeze_locked_release_authorized: false
p2_experiment_authorized: false
stop_checkpoint: P1 Business Scenario Owner Acceptance — Final Closeout Review
---

# P1 Task — Complex Scenario Coverage and Portfolio Story

## Current R2 final closeout status

原始 `PBR-P1-001` 已完成初始交付，R1 Owner Review 结论为
`NO_GO_PATCH_REQUIRED`，并已完成 split shipment Proposal revalidation、ticket
persistence/reload 与 target-aware 去重修复。当前任务是 Owner 明确发布的
`PBR-P1-001-R2` 最终限定收尾：补齐自然 partial-shipment 定位与歧义澄清、
Existing Investigation 完整业务合同、最终六个 Failure Lab profile 和真正的
Chromium split-shipment 纵向验证。当前状态为 `AWAITING_OWNER_ACCEPTANCE`；
不得写入 P1 Owner acceptance，不进入 P2。

## 0. 入口检查

本任务的原始入口检查已在初始 P1 执行时完成。以下入口条件和边界继续作为
P1 合同保留；R2 由 Owner 在当前 Session 明确发布，构成最终限定收尾授权：

- 根 `STATUS.md` 明确记录 `p0_status=OWNER_ACCEPTED`；
- Owner acceptance records 中有 Owner 的明确验收记录；
- `docs/portfolio-business-refactor/P0-DELIVERY-REPORT.md` 存在且 P0 Gate 通过；
- Owner 在当前 Session 明确发布本任务。

缺少前三项时，恢复为 `BLOCKED_BY_P0_OWNER_ACCEPTANCE` 并停止；缺少第四项
时保持 `READY_FOR_OWNER_DISPATCH` 并停止。不得补做 P0、推断 Owner 同意或
提前施工。

合法启动后，在一个长程任务内完成全部 P1 授权范围。开始时把
`STATUS.md` 更新为 `P1=IN_PROGRESS`；完成后只能写
`P1=AWAITING_OWNER_ACCEPTANCE`，不得自行写 `OWNER_ACCEPTED` 或启动 P2。

## 1. 目标

在 P0 业务底座上完成足够丰富但仍然窄域的组合场景：split shipment、
客户陈述与系统事实冲突、existing investigation、carrier exception、
`absent`/`unavailable` 和 exact retry。最终交付三条可重复主 Demo、一个隔离
的 Failure Lab、五类客户结果的浏览器展示和业务优先的 Portfolio Story。

P1 不扩展为完整售后平台，也不执行新的 Agent-vs-Workflow 实验。

## 2. 开工前必读

完整阅读 P0 任务列出的项目文档，并额外阅读：

- `docs/portfolio-business-refactor/P0-DELIVERY-REPORT.md`；
- P0 实际修改的数据 manifest、loader、domain projection、API、frontend 和
  E2E；
- 当前 `STATUS.md` 的全部 Execution Log 与 Owner acceptance records；
- 现有 issue revision、Case Fact、retry、tool fault、existing ticket/case、
  Policy RAG 和 Evidence Gate 相关代码与测试。

记录开工 HEAD 和用户已有改动；不得丢弃或覆盖无关工作。

## 3. 硬约束

P0 与 `GOALS.md` 的全部边界继续生效，尤其是：

- 不增加第三个 IssueType；
- split shipment 仍是一订单/一 Case/一 primary issue；
- carrier exception 只是 evidence/context，不是新业务路由；
- 不增加新 Tool。优先扩展现有 order/timeline/alert/case tool payload；如果
  证明第七个 Tool 才能满足业务合同，这是 scope conflict，暂停交 Owner；
- 不改变 Agent/Workflow selector、Evidence Gate 权威或旧 Eval 合同；
- 不调用 provider，不运行正式 Eval/Freeze/Locked/Release；
- 不引入退款、退货、支付、仓储、库存、多 Agent 或真实集成。

## 4. Work packages

### WP-P1-1 — Package-aware split shipment

让现有订单和轨迹读取结果明确返回 package/shipment identity、顺序、当前
状态和关键时间。客户界面先展示订单级包裹摘要，再把调查焦点绑定到正确
package。

至少实现并自动验证：

```text
Package A -> delivered
Package B -> in_transit within SLA
Package C -> stalled beyond SLA
```

客户说“只收到一部分”时，系统不得把整单简化成 delivered 或 stalled；
不得为已正常签收/运输的包裹创建调查。Proposal critical evidence、execution
parameters 和 existing-case duplicate check 必须能识别目标 shipment；任何
material fact 变化仍触发现有 Proposal revalidation，而不是新建一套协议。

### WP-P1-2 — `signed_not_received` 状态矩阵

用 `business-demo-v1` 的稳定 scenario IDs 覆盖并验证：

1. POD 本人/家庭成员，需补充确认；
2. POD 前台/代收点/快递柜，先解释位置；
3. 客户明确否认系统记录的代收位置，进入冲突处理；
4. POD 成功查询无记录，证据完整，进入 `INVESTIGATE`；
5. POD 第一次 transient unavailable、deterministic exact retry 成功；
6. POD 持续 unavailable，不得当成 absent；
7. active investigation，展示阶段和下一更新时间，不重复创建；
8. foreign order，统一授权拒绝且不泄漏事实；
9. active carrier alert 在恢复窗口内，展示 `WAIT`；
10. tracking=delivered、POD=front desk、客户明确说“这里没有前台”的
    evidence conflict。

维持最多两次业务 clarification 和现有执行/规划预算，不用更多提示词循环
掩盖业务建模缺失。

### WP-P1-3 — `stalled_tracking` 与 carrier context 状态矩阵

覆盖并验证：

1. 客户说“未发货”，系统实际 shipped 且最近轨迹正常；
2. 轨迹仍在 SLA；
3. 超过停滞阈值且无 active case；
4. active carrier alert 仍在恢复窗口；
5. alert 已结束但轨迹仍停滞；
6. active investigation；
7. timeline transient unavailable 后 exact retry；
8. timeline 持续 unavailable；
9. 系统已 delivered、客户仍说运输停滞时，使用现有 issue revision 转入
   `signed_not_received` 调查逻辑，而不是创建新 IssueType；
10. 结构性时间/状态冲突进入 `ESCALATE`。

Carrier alert payload 至少能提供影响区域、状态、开始时间、预计恢复时间；
不得把真实天气、承运商 API 或外部实时数据引入本地作品。

### WP-P1-4 — Existing investigation 业务展示

扩展现有 investigation/ticket 读取的业务字段，而不是增加新 Tool。至少展示：

- `status/stage`；
- `opened_at`、`last_updated_at`；
- `next_update_at` 或明确 SLA；
- 目标 order/shipment；
- 是否仍 active。

active case 结果为 `WAIT`，closed case 可为 `ANSWER` 或根据新事实重新通过
Evidence Gate。重复咨询、刷新、replay、并发确认都不得重复创建动作。

### WP-P1-5 — Policy RAG 前台化但不扩权

在客户结果和 Developer Trace 中用业务语言展示：检索到哪条虚构物流异常
规则、适用 SLA 和它怎样影响结果。默认客户界面隐藏 embedding score、
source hash、canonical resolver、authority conflict 等内部诊断；这些保留在
技术文档或可展开 Trace。

保留现有 canonical resolver、version/clause/effective-time/source-hash、
retrieval eval 和 fail-closed 行为。`business-demo-v1` 的 10 条 runtime clauses
与旧 adversarial/eval corpus 明确分层，禁止重写历史语料或降低 resolver
验证。

### WP-P1-6 — 三条主 Demo 与 Failure Lab

交付可重复的一键选择/说明，但示例控件仍只填充 composer：

1. **只收到部分包裹**：split shipment -> 定位 stalled package ->
   `INVESTIGATE`；
2. **签收但未收到**：POD/客户事实冲突 -> `CLARIFY` 或 `ESCALATE`，同时提供
   一个 POD absent -> `INVESTIGATE` 的分支说明；
3. **物流停滞遇到区域异常**：carrier recovery window -> `WAIT`，已有 case
   再次咨询仍不重复创建。

Failure Lab 与默认主故事分离，专门演示：

- `ABSENT` 与 `UNAVAILABLE` 的不同结果；
- 一次 transient failure 和 deterministic exact retry；
- persistent unavailable；
- replay/idempotency 不重复动作。

五类 disposition 必须都能在浏览器中通过稳定 fixture 重现。所有运行使用
Mock LLM + real local retrieval，provider/model calls 必须为 `0/0`。

### WP-P1-7 — README 与 Portfolio Story 完成版

最终 README 信息架构固定为：

1. 一句话业务定位；
2. 客户问题到五类 outcome 的业务流程；
3. 三条可运行 Demo；
4. synthetic business world 与数据规模；
5. 为什么需要 Order/Shipment/Tracking/POD/Carrier/Policy 联合取证；
6. 确定性安全与写动作边界；
7. Agent vs Strong Workflow 的公平实验与当前 `PREFER_WORKFLOW` 结论；
8. 本地启动与测试；
9. 架构、RAG、Eval 和历史 Evidence 的深层文档链接；
10. 限制与非目标。

README 不得暗示真实生产、真实承运商、Agent 优于 Workflow、统计显著性或
cost 已知。

### WP-P1-8 — 验证与交付报告

在 P0 全部验证基础上，新增状态矩阵的 unit/contract/integration/API 和
surface E2E。运行受影响的全套 Python/frontend 回归，并在
`docs/portfolio-business-refactor/P1-DELIVERY-REPORT.md` 记录：

- 每个 scenario ID、输入、关键事实、tool trajectory 摘要和期望/实际
  disposition；
- 五类 disposition 的浏览器证据；
- split shipment 只操作目标 package 的证据；
- existing case/refresh/replay/confirmation 无重复动作；
- absent/unavailable/retry 的执行次数与预算语义；
- Policy RAG 的客户展示与内部诊断边界；
- 所有命令、精确结果、失败和未执行 Gate；
- provider/model calls `0/0`；
- 旧 artifacts 无内容 diff；
- P2 experiment 仍未授权。

## 5. P1 Acceptance Gate

以下全部为真才可提交 Owner 验收：

- P0 Acceptance 继续成立；
- `PBR-AC-05` 至 `PBR-AC-10` 全部通过；
- split shipment 正确定位 package，且无错误/重复 investigation；
- 两个 IssueType 的状态矩阵全部有稳定自动化验证；
- delivery/carrier exception 未成为第三个 IssueType；
- 五类 disposition 均可在 API 和浏览器中稳定重现；
- 三条主 Demo 与 Failure Lab 可按文档重复运行；
- README 信息架构完成且保留 `PREFER_WORKFLOW`；
- Python/frontend 回归、静态检查、build 和 surface E2E 全部通过；
- 旧 artifacts 无内容 diff，无 provider/正式 Eval/Freeze/Locked/Release；
- `STATUS.md` 为 `P1=AWAITING_OWNER_ACCEPTANCE`；
- P2 仍为未授权。

## 6. 停止与交付

通过 P1 Gate 后立即停止，并向 Owner 提交业务结果、主要文件、精确测试结果、
逐项 Acceptance、`P1-DELIVERY-REPORT.md`、残余限制和未执行的 P2/Live/Eval/
Release 边界。

不得自行把 Program 标成 `COMPLETE`；只有 Owner 验收 P1 后才能写入
`P1=OWNER_ACCEPTED` 和 `Program=COMPLETE`。

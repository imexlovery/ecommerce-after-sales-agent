# Implementation Status

```yaml
schema_version: "1.0"
program_id: PORTFOLIO-BUSINESS-REFACTOR-001
updated_at: "2026-08-29T20:17:12+08:00"
updated_by: Codex
program_status: P1_AWAITING_OWNER_ACCEPTANCE
active_milestone: P1
p0_status: OWNER_ACCEPTED
p1_status: AWAITING_OWNER_ACCEPTANCE
active_task: PBR-P1-001-R1
next_owner_action: review the P1 Owner Review R1 delivery and record Owner acceptance or return requested changes
baseline_architecture_conclusion: PREFER_WORKFLOW
provider_calls_authorized: false
formal_eval_authorized: false
freeze_locked_release_authorized: false
```

`STATUS.md` 是当前 Portfolio / Business Refactor 的唯一实施状态面板。
`PROJECT.md` 保存长期总账和历史决策；目标与验收合同在
`docs/portfolio-business-refactor/GOALS.md`；本文件不重复历史叙述。

## 状态写入规则

1. 执行者在开始任务、完成一个 work package、发生阻塞和提交里程碑验收时
   更新上方 snapshot，并在下方日志追加一行。
2. snapshot 可以原位更新；Execution Log 只能追加，不能改写或删除旧行。
3. 只有 Owner 可以把里程碑写成 `OWNER_ACCEPTED`。Codex 完成全部验证后
   只能写成 `AWAITING_OWNER_ACCEPTANCE`。
4. P1 的唯一合法入口是 `p0_status=OWNER_ACCEPTED` 且 Owner 在新 Session
   明确发布 P1 任务卡。不得把“P0 tests passed”解释成 P1 授权。
5. `BLOCKED` 必须写清 blocker、已尝试的安全替代和需要的 Owner 决策；普通
   实现选择与可修复测试失败不是 Owner checkpoint。
6. 任何 provider、正式 Eval、Freeze、Locked、Release、部署、push 或 PR
   状态都不能在本轮擅自打开。

## 合法状态流

```text
P0 READY_FOR_OWNER_DISPATCH
  -> IN_PROGRESS
  -> AWAITING_OWNER_ACCEPTANCE
  -> OWNER_ACCEPTED             (Owner only)

P1 BLOCKED_BY_P0_OWNER_ACCEPTANCE
  -> READY_FOR_OWNER_DISPATCH   (after P0 owner acceptance)
  -> IN_PROGRESS                (new explicit Owner dispatch)
  -> AWAITING_OWNER_ACCEPTANCE
  -> OWNER_ACCEPTED             (Owner only)

Program COMPLETE only after P1 OWNER_ACCEPTED.
```

任务可以进入 `BLOCKED`，解决后回到原合法路径；不得跳过
`AWAITING_OWNER_ACCEPTANCE`。

## Milestone board

| Milestone | Task | Current status | Entry condition | Stop checkpoint |
|---|---|---|---|---|
| P0 | `PBR-P0-001` | `OWNER_ACCEPTED` | Owner 在新 Session 发布 P0 任务卡 | P0 Portfolio Foundation Owner Acceptance |
| P1 | `PBR-P1-001` | `AWAITING_OWNER_ACCEPTANCE` | P0 `OWNER_ACCEPTED`，且 Owner 另行发布 P1 任务卡 | P1 Business Scenario Owner Acceptance |

## Current protected baseline

- checkout planning HEAD：`9d43e4a7c38c5a51a316b399974d056d1d23685c`；
- V3-M2R measurement：`measurement_valid=true`；
- V3-M2R acceptance：`locked_acceptance=false`；
- architecture conclusion：`PREFER_WORKFLOW`；
- 旧 Eval、Freeze、Locked、Release 和 Evidence Pack 均为受保护历史；
- P1 Owner Review R1 已完成 proposal revalidation / split shipment target 绑定缺口的限定返修与验证；`PBR-P1-001-R1` 等待 Owner 验收。未调用 provider，未启动正式 Eval、Freeze、Locked、Release 或部署流程。

## Execution Log

| Timestamp | Actor | Milestone | Status | Evidence / note |
|---|---|---|---|---|
| 2026-08-29T15:51:46+08:00 | Codex | Program | `PLANNED` | Owner 委托编排两个长程里程碑；目标、状态与任务卡已发布，尚未授权施工。 |
| 2026-08-29T16:12:44+08:00 | Codex | P0 | `IN_PROGRESS` | Owner 在当前 Session 发布 `PBR-P0-001`；基线与既有用户改动已记录，开始执行 P0。 |
| 2026-08-29T16:52:08+08:00 | Codex | P0 | `AWAITING_OWNER_ACCEPTANCE` | P0 数据、默认运行时、五值客户结果、业务优先界面、Mock + real-local retrieval surface E2E、文档与验证完成；已写入交付报告，停在 Owner 验收前，未进入 P1。 |
| 2026-08-29T17:12:43+08:00 | Codex | P0 | `AWAITING_OWNER_ACCEPTANCE` | Owner Review 时间一致性修复完成：默认时点与 `business-demo-v1` manifest 对齐，validator 解析时区并拒绝未来事实；聚焦回归 `21 passed`、full pytest `289 passed`、静态/类型/前端检查通过，Chromium surface E2E `2 passed (8.9s)`。未调用 provider，未进入 P1。 |
| 2026-08-29T17:30:41+08:00 | Repository Owner | P0 | `OWNER_ACCEPTED` | Owner 明确确认 P0 验收通过，并要求将 P1 更新为 `READY_FOR_OWNER_DISPATCH`；本次只解除前置阻塞，不启动 P1。 |
| 2026-08-29T17:30:41+08:00 | Codex | P1 | `READY_FOR_OWNER_DISPATCH` | P0 Owner acceptance 已记录，P1 任务卡可在新 Session 由 Owner 单独发布；`active_task=none`，未进入 `IN_PROGRESS`。 |
| 2026-08-29T17:35:15+08:00 | Codex | P1 | `IN_PROGRESS` | Owner 在新 Session 明确发布 `PBR-P1-001`；入口条件已复核，开始执行 WP-P1-1～8。provider、正式 Eval、Freeze、Locked、Release、部署、push 和 PR 仍未授权。 |
| 2026-08-29T18:59:37+08:00 | Codex | P1 | `AWAITING_OWNER_ACCEPTANCE` | WP-P1-1～8、PBR-AC-05～10 实现与验证完成；focused suite `47 passed`、全量 pytest `318 collected / 100% passed`、静态/前端检查通过、离线 Mock + real-local surface E2E `3 passed (22.5s)`、zero-call `0/0`。已生成 `docs/portfolio-business-refactor/P1-DELIVERY-REPORT.md`，停止在 P1 Business Scenario Owner Acceptance；未调用 provider，未运行正式 Eval/Freeze/Locked/Release、P2、部署、push 或 PR。 |
| 2026-08-29T19:37:32+08:00 | Repository Owner Review | P1 | `NO_GO_PATCH_REQUIRED` | `P1 Owner Gate = NO_GO_PATCH_REQUIRED`。独立复现 split shipment proposal revalidation 目标漂移：bare target `SHP-044`，scenario/proposal target `SHP-045`；revalidated `complete_no_action / WITHIN_TRACKING_SLA`，split confirm 返回 `409 PROPOSAL_EVIDENCE_CHANGED`。限定返修任务 `PBR-P1-001-R1` 开始；不得写入 `OWNER_ACCEPTED`，不进入 P2 或后续 Gate。 |
| 2026-08-29T20:17:12+08:00 | Codex | P1 | `AWAITING_OWNER_ACCEPTANCE` | `PBR-P1-001-R1` 限定返修完成：target-aware Proposal revalidation、carrier critical evidence、ticket persistence/reload 与 target-aware existing investigation/ticket 去重已修复；R1 service/API 回归、focused `83 passed`、全量 `320`、静态/前端检查、离线 Mock + real-local surface `3 passed`、zero-call `0/0` 通过。旧 Eval/delivery artifacts 无内容 diff。当前停止在 P1 Business Scenario Owner Acceptance；未调用 provider，未运行正式 Eval/Freeze/Locked/Release、P2、部署、push 或 PR。 |

## Owner acceptance records

| Timestamp | Milestone | Owner decision | Accepted evidence | Authorization boundary |
|---|---|---|---|---|
| 2026-08-29T17:30:41+08:00 | P0 | `OWNER_ACCEPTED` — “确认 P0 验收通过。” | `docs/portfolio-business-refactor/P0-DELIVERY-REPORT.md` | P1 可进入 `READY_FOR_OWNER_DISPATCH`；本 Session 不启动 P1。 |

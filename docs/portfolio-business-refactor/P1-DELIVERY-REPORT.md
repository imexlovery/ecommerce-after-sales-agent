# P1 Complex Scenario Coverage and Portfolio Story Delivery Report

日期：2026-08-29  
任务：`PBR-P1-001`  计划：`PORTFOLIO-BUSINESS-REFACTOR-001`  
实现等级：`G1_local_portfolio_prototype`  
停止点：**P1 Business Scenario Owner Acceptance**  
当前状态：`P1=AWAITING_OWNER_ACCEPTANCE`

## 1. 交付结论

P1 授权范围 WP-P1-1～WP-P1-8 已完成，P1 Acceptance Gate 的实现与验证项已
准备提交 Owner 验收。Codex 没有写入 `P1=OWNER_ACCEPTED`，没有进入 P2。

本轮继续保持以下边界：

- 只处理 `signed_not_received` 与 `stalled_tracking` 两个 IssueType；carrier
  exception 是 evidence/context，不是第三个路由；
- 只扩展既有订单、轨迹、POD、carrier alert、existing investigation/ticket
  read payload，没有增加第七个 Tool；
- 模型只负责 triage 与 bounded investigation selector；授权、目标包裹、Policy
  Resolver、Evidence Gate、Proposal、确认、幂等和 write read-back 仍由项目代码掌握；
- 所有本轮运行均为 Mock LLM + 本地 Policy RAG；Provider calls / Model calls
  为 `0 / 0`，`cost=unavailable`；
- 没有调用真实 Provider，没有运行正式 Eval、Freeze、Locked、Release，没有部署、
  push、创建 PR，也没有启动 P2 experiment。

实现基线 HEAD：`9d43e4a7c38c5a51a316b399974d056d1d23685c`。P1 开始时工作区已有
P0 和用户改动；本轮未重置、覆盖或删除无关改动。旧 `evals/`、`delivery/` 内容
没有 diff。

## 2. 入口与授权核验

开工前已完整阅读 P1 任务卡要求的项目文档、P0 任务卡与交付报告、当前
`STATUS.md` 全部 Execution Log 与 Owner acceptance records，以及 issue revision、
Case Fact、retry、fault、existing case/ticket、Policy RAG、Evidence Gate、API、
frontend 和 E2E 相关实现与测试。

入口条件全部满足：

| 条件 | 结果 |
|---|---|
| `STATUS.md` 中 `p0_status=OWNER_ACCEPTED` | PASS |
| P0 Owner acceptance record | PASS，`2026-08-29T17:30:41+08:00`，Owner：“确认 P0 验收通过。” |
| P0 交付报告及 Gate | PASS，`docs/portfolio-business-refactor/P0-DELIVERY-REPORT.md` 存在 |
| 当前 Session 明确发布 `PBR-P1-001` | PASS |
| 开工状态 | 已先写入 `P1=IN_PROGRESS`，随后执行 WP-P1-1～8 |

## 3. Work package 结果

### WP-P1-1 — Package-aware split shipment

`business-demo-v1` 的订单上下文、shipment summary、tracking timeline、POD、
Proposal critical evidence、execution parameters 和 existing-case duplicate check
均带有目标 shipment identity。三包裹主场景 `ORD-039` 的事实为：

```text
SHP-043 / package A -> delivered
SHP-044 / package B -> in_transit，仍在 SLA 内
SHP-045 / package C -> stalled，超过阈值
```

`partial-packages-target-c` 只绑定 `SHP-045`，只创建一个订单范围内的 Case，
Proposal 不包含 `SHP-043` 的目标证据或执行参数。验证还覆盖了 target-aware
active investigation、目标 shipment 的 ticket/case 去重和 material fact 变化时
创建新 Evidence Gate / 新 Proposal 的既有协议。

### WP-P1-2 — `signed_not_received` 状态矩阵

已交付 10 个稳定 scenario IDs：

`signed-pod-recipient-clarification`、`signed-pod-location-explanation`、
`signed-pod-conflict`、`signed-pod-absent`、`signed-pod-transient-retry`、
`signed-pod-persistent-unavailable`、`signed-active-investigation`、
`signed-foreign-order`、`signed-active-carrier-recovery`、
`signed-front-desk-denial`。

POD recipient unit matrix 另覆盖 `self`、`family`、`collection_point`、`locker`；
前台/代收位置解释与“这里没有前台”的冲突分支分别可验证。POD `absent` 与
`unavailable` 保持不同：成功查询无 proof 行可进入 `INVESTIGATE`，持续 unavailable
在 exact retry 用尽后进入 `ESCALATE`，不会伪装成 absent。

### WP-P1-3 — `stalled_tracking` 与 carrier context 状态矩阵

已交付 10 个稳定 scenario IDs：

`stalled-not-shipped-claim`、`stalled-within-sla`、
`stalled-overdue-no-active-case`、`stalled-carrier-recovery`、
`stalled-resolved-carrier-alert`、`stalled-active-investigation`、
`stalled-timeline-transient-retry`、`stalled-timeline-persistent-unavailable`、
`stalled-delivered-issue-revision`、`stalled-structural-conflict`。

Carrier alert 返回影响区域、active/resolved 状态、开始时间和预计恢复时间。告警
只解释物流事实，不改变 IssueType，也不绕过 Evidence Gate。已送达但客户说运输
停滞时沿用已有 issue revision 进入 `signed_not_received`；不会创建第三个
IssueType。

### WP-P1-4 — Existing investigation 业务展示

`ExistingInvestigation` 和 API/UI projection 已包含 `status`、`stage`、
`opened_at`、`last_updated_at`、`next_update_at`、目标 order/shipment 与
`is_active`。active case 场景统一投影 `WAIT`，再次咨询、SSE replay、刷新及
confirmation 路径不重复创建动作或 ticket。

### WP-P1-5 — Policy RAG 前台化但不扩权

业务 runtime 使用独立的 `business-demo-v1` Policy corpus，包含 10 条虚构
runtime clauses；历史 adversarial/eval corpus 未被重写。客户层显示业务摘要、
适用时效、区域/状态与结果影响；Developer Trace 保留受限的 corpus/version/
clause/citation/retrieval mode 证据。embedding score、source hash、canonical
resolver 与 authority conflict 不进入客户主展示。

Policy Resolver、effective-time、source-hash、fail-closed 与 retrieval status
仍是确定性项目代码；Policy RAG 不能自行创建 Proposal，也不能扩展写权限。

### WP-P1-6 — 三条主 Demo 与 Failure Lab

首页提供三条主 Demo，按钮只填充 composer，不选择 route：

1. `partial-packages-target-c`：split shipment -> 目标 `SHP-045` -> `INVESTIGATE`；
2. `signed-pod-conflict`：POD 前台记录与客户否认冲突 -> `ESCALATE`，同组提供
   recipient/location/absent 分支；
3. `stalled-carrier-recovery`：停滞超过阈值但在区域承运恢复窗口 -> `WAIT`，另有
   active investigation 分支验证不重复。

Failure Lab 与默认主故事分离，展示：POD/timeline transient exact retry、
persistent unavailable、Policy unavailable/conflict、carrier terminal read failure、
uncertain write、replay/idempotency 约束。默认故事的运行时 fixture 不会被故障
profile 污染。

### WP-P1-7 — README 与 Portfolio Story

`README.md` 已按任务卡固定的 10 部分重排：业务定位、五类结果、三条 Demo、
synthetic world 与规模、联合取证、确定性安全与写动作边界、Agent vs Strong
Workflow 公平实验与既有 `PREFER_WORKFLOW`、启动与测试、深层文档、限制与非目标。

README 明确声明本项目是本地全虚构 Portfolio 原型，不连接真实平台/承运商；不
宣称 Agent 优于 Workflow、统计显著性或 cost 已知，并保留正式 Eval/Release/P2
未执行边界。

### WP-P1-8 — 验证与交付报告

本报告记录 scenario matrix、目标包裹、轨迹摘要、expected/actual disposition、
Failure Lab、浏览器证据、RAG 展示边界、测试结果、失败修复、未执行 Gate 和
`0/0` 证据。最终状态写入根 `STATUS.md` 后停在 Owner acceptance，不自行接受。

## 4. 业务场景逐项矩阵

轨迹缩写：`OC`=get_order_context，`TL`=get_logistics_timeline，`POD`=
get_delivery_proof，`POL`=search_after_sales_policy，`EX`=
get_existing_logistics_tickets，`AL`=get_carrier_service_alerts，`REV`=已有
issue revision，`Gate`=确定性 Evidence Gate。表中 actual 是默认无故障
`business-demo-v1` 路径；括号内记录对应隔离 fault overlay 的结果。

| Scenario ID | 输入 / 关键事实 / 目标 | trajectory 摘要 | expected -> actual |
|---|---|---|---|
| `partial-packages-target-c` | `customer_r`；`ORD-039`；A delivered、B within SLA、C stalled；目标 `SHP-045` | `OC -> TL -> AL -> POL -> EX -> Gate`，Proposal/执行只绑定 C | `INVESTIGATE -> INVESTIGATE` |
| `signed-pod-recipient-clarification` | `customer_i`；`ORD-010/SHP-010`；POD=`self`，客户未收到 | `OC -> TL -> POD -> Gate`，受限业务澄清 | `CLARIFY -> CLARIFY` |
| `signed-pod-location-explanation` | `customer_c`；`ORD-004/SHP-004`；POD=`front_desk` | `OC -> TL -> POD -> Gate`，先解释代收位置 | `CLARIFY -> CLARIFY` |
| `signed-pod-conflict` | `customer_c`；`ORD-004/SHP-004`；POD 前台，客户明确否认 | `OC -> TL -> POD -> Gate(conflict)` | `ESCALATE -> ESCALATE` |
| `signed-pod-absent` | `customer_a`；`ORD-001/SHP-001`；POD 成功查询但无 proof 行，`absent` | `OC -> TL -> POD(absent) -> POL -> EX -> Gate` | `INVESTIGATE -> INVESTIGATE` |
| `signed-pod-transient-retry` | `customer_a`；`ORD-001/SHP-001`；POD 第一次 transient unavailable | `OC -> TL -> POD(retry x2, exact identity) -> POL -> EX -> Gate` | `INVESTIGATE -> INVESTIGATE` |
| `signed-pod-persistent-unavailable` | `customer_a`；`ORD-001/SHP-001`；默认 absent；隔离变体 POD 持续 unavailable | `OC -> TL -> POD(retry x2) -> Gate(unavailable-final)` | `INVESTIGATE -> INVESTIGATE`；隔离故障 `ESCALATE` |
| `signed-active-investigation` | `customer_e`；`ORD-006/SHP-006`；active `proof_review`，有 next update | `OC -> TL -> POD -> POL -> EX(active) -> Gate`，不重复动作 | `WAIT -> WAIT` |
| `signed-foreign-order` | `customer_a` 请求 `ORD-005`；订单属于其他虚拟客户 | `triage -> authorization deny -> ANSWER`，无 Case、无订单事实泄漏 | `ANSWER -> ANSWER` |
| `signed-active-carrier-recovery` | `customer_a`；`ORD-003/SHP-003`；客户说 signed，实际运输中且 carrier recovery active | `OC -> REV(stalled) -> TL -> POL -> EX -> AL -> Gate` | `WAIT -> WAIT` |
| `signed-front-desk-denial` | `customer_c`；`ORD-004/SHP-004` delivered + front desk；客户说“这里没有前台” | `OC -> TL -> POD -> Gate(conflict)` | `ESCALATE -> ESCALATE` |
| `stalled-not-shipped-claim` | `customer_b`；`ORD-002/SHP-002`；客户说未发货，系统 shipped，轨迹正常 | `OC -> TL -> POL -> Gate(WAIT)` | `WAIT -> WAIT` |
| `stalled-within-sla` | `customer_b`；`ORD-002/SHP-002`；最近轨迹仍在 48h SLA | `OC -> TL -> POL -> Gate(WAIT)` | `WAIT -> WAIT` |
| `stalled-overdue-no-active-case` | `customer_l`；`ORD-033/SHP-033`；超过阈值，无 active case/alert | `OC -> TL -> POL -> EX -> AL -> Gate`，形成有效 Proposal | `INVESTIGATE -> INVESTIGATE` |
| `stalled-carrier-recovery` | `customer_a`；`ORD-003/SHP-003`；超过阈值但 carrier alert active | `OC -> TL -> POL -> EX -> AL -> Gate(WAIT)` | `WAIT -> WAIT` |
| `stalled-resolved-carrier-alert` | `customer_b`；`ORD-023/SHP-023`；alert resolved，轨迹仍 stalled | `OC -> TL -> POL -> EX -> AL(resolved) -> Gate` | `INVESTIGATE -> INVESTIGATE` |
| `stalled-active-investigation` | `customer_c`；`ORD-024/SHP-024`；active `carrier_follow_up`，有 next update | `OC -> TL -> POL -> EX(active) -> Gate(WAIT)` | `WAIT -> WAIT` |
| `stalled-timeline-transient-retry` | `customer_l`；`ORD-033/SHP-033`；timeline 第一次 transient unavailable | `OC -> TL(retry x2, exact identity) -> POL -> EX -> AL -> Gate` | `INVESTIGATE -> INVESTIGATE` |
| `stalled-timeline-persistent-unavailable` | `customer_l`；`ORD-033/SHP-033`；默认 overdue；隔离变体 timeline 持续 unavailable | `OC -> TL(retry x2) -> Gate(unavailable-final)` | `INVESTIGATE -> INVESTIGATE`；隔离故障 `ESCALATE` |
| `stalled-delivered-issue-revision` | `customer_a`；`ORD-001/SHP-001` delivered，但客户描述运输停滞 | `OC -> REV(signed) -> TL -> POD -> POL -> EX -> Gate` | `INVESTIGATE -> INVESTIGATE` |
| `stalled-structural-conflict` | `customer_l`；`ORD-033/SHP-033`；默认 overdue；隔离变体制造状态/时间矛盾 | `OC -> TL -> POL -> EX -> AL -> Gate(structural conflict)` | `INVESTIGATE -> INVESTIGATE`；隔离故障 `ESCALATE` |

默认目录共 21 个 stable scenario IDs；`tests/integration/test_business_demo_scenarios.py`
逐个创建独立合成会话，并对除 foreign-order 外的每个 Case 验证 authorized order、
target shipment、实际只读 tool call、无 active action/ticket 和 disposition。foreign
order 验证无 Case、无订单泄漏。每个 Case 的实际 read count 与 planning count 均
作为独立字段保留，不合并为一个状态字段。

## 5. Failure Lab 与安全语义

| 故障路径 | 证据与结果 |
|---|---|
| `pod_timeout_once` | POD attempts 精确为 `[1, 2]`；两次均为 actual execution；第二次成功后恢复默认 `INVESTIGATE`。 |
| `timeline_retry` | timeline attempts 精确为 `[1, 2]`；retry 使用同一查询意图，不新建 Case 或 action identity。 |
| POD persistent unavailable | 第 1/2 次均 unavailable；最终 `CRITICAL_EVIDENCE_UNAVAILABLE_FINAL` -> `ESCALATE`，不能进入 absent/proposal。 |
| timeline persistent unavailable | 第 1/2 次均 unavailable；最终 `ESCALATE`，不能把 unknown 当作无记录。 |
| `policy_unavailable` / `policy_conflict` | Policy 不能形成可信 authority binding；Case 安全关闭为 `ESCALATE`，无 Proposal。 |
| `carrier_terminal` | carrier read 非 retryable failure；不把 alert 当作 gate，安全转人工，无 Proposal。 |
| `ticket_uncertain` | 精确确认后只保留一个 `uncertain` action；原 action identity/idempotency key 保留；无 ticket、无盲目重试。 |
| replay / refresh / confirmation | 事件先持久化再 SSE；刷新只重放事件，精确 Proposal `id + version` confirm 只产生一个 simulated write/read-back。 |

项目预算仍由既有合同控制：单 Case 最多一个 authorized order/primary issue、两次
business clarification、6 次 actual read-tool execution、16 次 planning turn、
一个 active executable Proposal。blocked call 只消耗 planning turn；retry 两次
actual execution 都计入预算。故障测试没有通过增加提示词循环来掩盖预算或证据缺口。

## 6. API、浏览器与 RAG 证据

### API / contract / integration

`tests/api/test_customer_disposition_surface.py` 验证五个确切 API projection：
`ANSWER`、`WAIT`、`CLARIFY`、`INVESTIGATE`、`ESCALATE`，并验证 21 个目录场景与
6 个故障 profile 可读。`tests/integration/test_business_demo_scenarios.py`、
`test_business_demo_fault_lab.py`、`test_mock_runtime_zero_calls.py` 和
Evidence Gate unit tests 验证场景、故障、目标、retry、POD、timeline、issue
revision、proposal 和 zero-call 约束。

### 最终浏览器运行

最终命令使用隔离的临时业务 SQLite/检查点目录、Chromium、`LLM_MODE=mock`、
`POLICY_RETRIEVAL_MODE=real_local`、`SCENARIO_EVALUATED_AT=2026-08-29T08:00:00Z`，
并设置 `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1` 只使用已缓存的本地 embedding
模型。服务 readiness 返回：

```json
{"status":"ready","llm_mode":"mock","fixture_version":"business-demo-v1","business_store":"ready","checkpoint_store":"ready","provider_checked":false}
```

命令：

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
SURFACE_BASE_URL=http://127.0.0.1:5174 \
SURFACE_BROWSER_CHANNEL=chromium EXPECTED_LLM_MODE=mock \
  npm run e2e:surface --prefix frontend
```

结果：

```text
Running 3 tests using 1 worker
3 passed (22.5s)
```

三条浏览器测试分别证明：

1. `INVESTIGATE` 主旅程读取联合证据，展示条款摘录与客户结果，精确确认
   proposal 后一次 write/read-back，刷新后仍显示同一处理编号；
2. Eval Dashboard 拥有独立滚动面，且没有伪造空报告；
3. 通过稳定 fixture 依次重现 `ESCALATE`、`WAIT`、`WAIT`、`ANSWER`、`CLARIFY`，
   即五类 disposition 均出现在浏览器 DOM 的稳定 `data-customer-disposition`
   投影中，并保留业务解释文本。

### zero-call 与 RAG 边界

```bash
UV_CACHE_DIR=/private/tmp/ecommerce-uv-cache \
  uv run pytest -q -o addopts='' tests/integration/test_mock_runtime_zero_calls.py
```

结果：`1 passed in 0.78s`。测试对 `ChatDeepSeek.ainvoke` 和 Mock selector
`ainvoke` 设置失败探针，Mock 路径通过后断言 `provider_calls=0`、`model_calls=0`。
real-local retrieval 只使用本地 embedding/index，不是 DeepSeek Provider 调用。

## 7. 验证命令与精确结果

| 命令 | 结果 | evidence label |
|---|---|---|
| `UV_CACHE_DIR=/private/tmp/ecommerce-uv-cache uv run python -m after_sales_agent.fixtures.business_demo` | `valid=true`；20 customers、40 orders、48 shipments、132 tracking events、8 alerts、8 cases、10 clauses、6 fault profiles、21 scenarios | `static / contract` |
| P1 focused suite：`test_business_demo_scenarios.py`、`test_business_demo_fault_lab.py`、`test_evidence_gate.py`、`test_customer_disposition_surface.py`、`test_business_demo_dataset.py` | `47 passed in 3.17s` | `contract / integration / mock` |
| `UV_CACHE_DIR=/private/tmp/ecommerce-uv-cache uv run pytest -q` | 318 collected，100% 通过，无失败 | `contract / integration / mock` |
| `UV_CACHE_DIR=/private/tmp/ecommerce-uv-cache uv run ruff check .` | `All checks passed!` | `static` |
| `UV_CACHE_DIR=/private/tmp/ecommerce-uv-cache uv run mypy src` | `Success: no issues found in 86 source files` | `static` |
| `npm run typecheck --prefix frontend` | TypeScript check passed | `static` |
| `npm run build --prefix frontend` | 40 modules transformed，Vite build passed | `static` |
| 离线 `npm run e2e:surface --prefix frontend` | `3 passed (22.5s)` | `mock_llm + real_local_retrieval + surface_e2e` |
| `git diff --check` | 无 whitespace error | `static` |
| `git diff --name-only -- evals delivery` 与 `git status --short evals delivery` | 无输出；旧 artifacts 无内容 diff | `static / evidence-boundary` |

## 8. 失败尝试与修复记录

失败证据保留在本地 Playwright `test-results/`，没有通过删除或重写来掩盖：

1. 一次 surface E2E 使用了旧的 `SCENARIO_EVALUATED_AT=2026-08-23T12:00:00Z`，
   将 `ORD-002` 的 8 月 28 日正常轨迹误判为未来事实冲突；重启时明确使用
   `business-demo-v1` manifest 的 `2026-08-29T08:00:00Z` 后修复。
2. 一次 surface E2E 连接到既有旧 SQLite，事件流读取 Case 时暴露缺失的
   `investigation_cases.target_shipment_id`；对旧库执行 Alembic upgrade 发现它有
   已存在的表但没有可用 revision baseline，返回 `table conversations already exists`。
   没有删除或覆盖该用户数据库，最终使用隔离的全新当前 schema 临时数据库完成
   clean-start E2E，并在离线 real-local 环境复跑通过。
3. 第一次非离线 real-local 启动显示 Hugging Face Hub 未认证提示；最终验收改用
   已缓存模型和 `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1`，结果仍为 `3 passed`。

这些是本地运行环境/数据时点问题，不是通过放宽 Evidence Gate、伪造数据、降低
resolver 验证或重写历史 artifacts 处理。最终验收证据只引用最后一轮离线运行。

## 9. P1 Acceptance Gate 对照

| Gate | 结果 | 依据 |
|---|---|---|
| P0 Acceptance 继续成立 | PASS | `STATUS.md` Owner record 保留，P0 未被重写 |
| `PBR-AC-05` split shipment | PASS | `SHP-043/044/045` 差异状态、目标 `SHP-045`、无错误包裹 Proposal |
| `PBR-AC-06` 两个 IssueType 组合矩阵 | PASS | 20 个业务 scenario + unit/integration/fault coverage |
| `PBR-AC-07` 五类 disposition API/界面/自动化 | PASS | API projection、47 focused tests、3-test browser suite |
| `PBR-AC-08` 三 Demo + Failure Lab | PASS | 21 stable IDs、3 主故事、6 fault profiles、exact retry/uncertain evidence |
| `PBR-AC-09` 旧 fixtures/Eval/Freeze/Locked/Release | PASS | `evals/`、`delivery/` 无内容 diff；未改旧 identity |
| `PBR-AC-10` provider-free / no formal conclusion | PASS | zero-call test `1 passed`；未运行正式 Eval/Freeze/Locked/Release |
| Python/frontend regression、static、build、surface E2E | PASS | 本报告第 7 节精确结果 |
| `STATUS.md` | 待最后写入 | 完成本报告后写为 `P1=AWAITING_OWNER_ACCEPTANCE` |
| P2 authorization | 未授权且保持关闭 | 未运行 P2 experiment |

## 10. 未执行 Gate 与残余限制

以下项目有意未执行，也不由本报告推断其结果：

- 真实 DeepSeek Provider / `LLM_MODE=live`；
- 正式 Development Eval、Freeze、Locked Eval、Release evidence；
- 新 Agent-vs-Workflow 实验或任何胜负/统计显著性结论；
- P2 complex-scenario experiment；
- 部署、远程 publication、push、PR；
- 真实客户、订单、承运商 API、天气、运力或其他外部集成。

旧数据库的 Alembic revision baseline 需要后续独立的运维决策；本轮没有进行
破坏性迁移。`cost=unavailable` 仍保持 unavailable，不能从 Mock 或 local retrieval
运行推断 Provider 成本。

## 11. Owner 下一步

请 Owner 审阅本报告、21 个 scenario matrix、三条主 Demo、Failure Lab 和最终
浏览器证据。当前停在 **P1 Business Scenario Owner Acceptance**；只有 Owner
明确接受后，才可以在新的授权动作中把 `P1` 写为 `OWNER_ACCEPTED`，并另行决定
是否启动 P2。Codex 本轮不执行该接受动作。

## 12. P1 Owner Review R1 限定返修记录

日期：2026-08-29  
返修任务：`PBR-P1-001-R1`  
返修范围：仅修复 split shipment 的 Proposal revalidation、ticket persistence 和
target-aware existing investigation/ticket 去重；不改变 IssueType、Agent/Workflow
selector、历史 Eval 结论或任何后续 Gate。

### 12.1 Owner Review 阻塞与复现

本 addendum 保留 Owner 返回的原始阻塞证据。初始 P1 报告的 `PBR-AC-05 PASS`
没有覆盖 split Proposal 的实际确认/重验证路径，Owner Review 独立复现为：

```text
bare_partial_target=SHP-044
scenario_case.target_shipment_id=SHP-045
proposal.execution_parameters.target_shipment_id=SHP-045
revalidated_decision=complete_no_action
revalidated_reason=WITHIN_TRACKING_SLA
split_confirm=ERROR
code=PROPOSAL_EVIDENCE_CHANGED
status=409
```

开工前已在 `STATUS.md` 追加 `P1 Owner Gate = NO_GO_PATCH_REQUIRED`，并将
`active_task` 设为 `PBR-P1-001-R1`、P1 置为 `IN_PROGRESS`；没有写入任何新的
`OWNER_ACCEPTED`。

开工前基线记录：`git status --short --branch` 为
`## main...origin/main [ahead 34]`，`git rev-parse HEAD` 为
`9d43e4a7c38c5a51a316b399974d056d1d23685c`。该 dirty worktree 中的既有 P0/P1
及用户改动均予以保留；本轮未执行 reset、checkout 或清理无关文件。

### 12.2 根因与限定修复

1. Confirm 路径的 `_revalidate_gate` 重新组装 Evidence Gate facts 时漏传已持久化
   的 `case.target_shipment_id`，多包裹订单因此退化到订单级时间判断。现在它以
   Case target 重验证，并要求 Proposal execution parameter 与 Case target 精确
   一致；business-demo 既有 carrier alert 也被纳入重验证的 critical evidence
   hash，保持原 Proposal snapshot 绑定。
2. Confirm、retry、executor 和 startup reload 现在都沿用同一个 target。Ticket
   persistence 增加了 `tickets.target_shipment_id` 及对应 P1 migration；details
   同时保留业务来源和目标，避免 reload 丢失 shipment identity。active ticket 的
   duplicate check 和六号 read tool 均按目标 shipment 查询，并保留无目标历史
   ticket 的 order-wide 兼容语义。
3. active ticket 的唯一约束拆为“无目标的订单/IssueType”与“有目标的订单/IssueType/
   shipment”两条确定性约束；没有增加 Tool、IssueType、Agent Pattern 或新的写入
   能力。Failure Lab 的 retry、uncertain、replay/idempotency 逻辑未改动。

### 12.3 修复后证据

新增的 integration/API regression 均以 `business-demo-v1`、Mock 和本地隔离存储
运行，修复后的关键结果为：

```text
revalidated_decision=propose_ticket
revalidated_reason=STALLED_TRACKING_EVIDENCE_COMPLETE
scenario_case.target_shipment_id=SHP-045
proposal.execution_parameters.target_shipment_id=SHP-045
split_confirm=202
persisted_ticket.target_shipment_id=SHP-045
reloaded_fixture_ticket.target_shipment_id=SHP-045
target_mismatch_lookup(SHP-044)=absent
provider_calls/model_calls=0/0
```

确认只创建一个 `SHP-045` ticket；数据库行、fixture read model 和 reload 后的
existing-ticket observation 均保留同一目标。原始失败回归曾得到
`PROPOSAL_EVIDENCE_CHANGED`/`409`，该失败未被删除或改写；修复后的同一回归已通过。

### 12.4 R1 验证命令与结果

| 命令 | 精确结果 | evidence label |
|---|---|---|
| `UV_CACHE_DIR=/private/tmp/ecommerce-uv-cache uv run pytest -q -o addopts='' tests/integration/test_business_demo_scenarios.py -k split_shipment_confirmation` | `1 passed, 21 deselected` | `integration / mock` |
| `UV_CACHE_DIR=/private/tmp/ecommerce-uv-cache uv run pytest -q -o addopts='' tests/api/test_customer_disposition_surface.py -k split_shipment` | `1 passed, 2 deselected` | `API / mock` |
| `UV_CACHE_DIR=/private/tmp/ecommerce-uv-cache uv run pytest -q -o addopts='' tests/integration/test_business_demo_scenarios.py tests/integration/test_business_demo_fault_lab.py tests/integration/test_application_service.py tests/component/test_storage_repository.py tests/component/test_storage_migration.py tests/unit/test_evidence_gate.py tests/unit/test_governed_tools.py` | `83 passed` | `contract / integration / mock` |
| `UV_CACHE_DIR=/private/tmp/ecommerce-uv-cache uv run pytest -q` | collect-only `320`；full run exit `0`，全部通过 | `contract / integration / mock` |
| `UV_CACHE_DIR=/private/tmp/ecommerce-uv-cache uv run pytest -q -o addopts='' tests/integration/test_mock_runtime_zero_calls.py` | `1 passed`；断言 provider/model `0/0` | `mock / evidence-boundary` |
| `UV_CACHE_DIR=/private/tmp/ecommerce-uv-cache uv run pytest -q -o addopts='' tests/component/test_storage_migration.py` | `1 passed`；当前 Alembic schema 与 metadata 一致 | `contract / operational` |
| `UV_CACHE_DIR=/private/tmp/ecommerce-uv-cache uv run ruff check .` | `All checks passed!` | `static` |
| `UV_CACHE_DIR=/private/tmp/ecommerce-uv-cache uv run mypy src` | `Success: no issues found in 86 source files` | `static` |
| `npm run typecheck --prefix frontend` | passed | `static` |
| `npm run build --prefix frontend` | 40 modules transformed，Vite build passed | `static` |
| `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 SURFACE_BASE_URL=http://127.0.0.1:5174 SURFACE_BROWSER_CHANNEL=chromium EXPECTED_LLM_MODE=mock npm run e2e:surface --prefix frontend` | `3 passed (20.0s)`；固定 `SCENARIO_EVALUATED_AT=2026-08-29T08:00:00Z`、隔离临时 DB、`real_local` retrieval | `mock_llm + real_local_retrieval + surface_e2e` |
| `git diff --check` | 无 whitespace error | `static` |
| `git diff --name-only -- evals delivery` 与 `git status --short evals delivery` | 无输出；旧 artifacts 无内容 diff | `evidence-boundary` |

一次未设置任务卡固定 scenario clock 的普通沙箱 surface 运行失败，随后一次普通沙箱
Chromium 启动因 `SIGABRT/EPERM` 失败；两者均为环境复现记录。使用任务卡规定的
固定时点和本地测试权限重跑后得到上表最终 `3 passed`，没有放宽断言或修改页面测试。

### 12.5 R1 提交状态与边界

R1 完成后重新满足 `PBR-AC-05` 至 `PBR-AC-10` 的实现与验证要求；本次修复没有
修改既有 Eval/Freeze/Locked/Release artifact，也没有新增正式 measurement。当前
`STATUS.md` 已更新为 `P1=AWAITING_OWNER_ACCEPTANCE`，保留 `p0_status=OWNER_ACCEPTED`
与原 Owner acceptance record，但没有新增 P1 acceptance record。

以下仍明确未执行、未授权：真实 Provider/`LLM_MODE=live`、正式 Eval、Freeze、
Locked、Release、P2 experiment、部署、远程 publication、push、PR 和真实外部
客户/订单/承运商集成。当前停止点仍是 **P1 Business Scenario Owner Acceptance**，
等待 Owner 对 `PBR-P1-001-R1` 进行验收。

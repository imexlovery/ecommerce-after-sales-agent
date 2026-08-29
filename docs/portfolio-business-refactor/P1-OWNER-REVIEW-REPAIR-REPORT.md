# P1 Owner Review Repair Report — Final Closeout R2

日期：2026-08-29  
任务：`PBR-P1-001-R2`  计划：`PORTFOLIO-BUSINESS-REFACTOR-001`  
实现等级：`G1_local_portfolio_prototype`  
当前状态：`p0_status=OWNER_ACCEPTED`，`p1_status=AWAITING_OWNER_ACCEPTANCE`  
停止点：**P1 Business Scenario Owner Acceptance — Final Closeout Review**

本报告是 P1 Owner Review 的独立返修记录，不替换或改写原始交付报告、R1 记录、
历史 Eval/Freeze/Locked/Release artifact 或失败证据。

## 1. 结论与边界

`PBR-P1-001-R2` 的四组限定收尾已完成：

1. 自然 partial-shipment 消息在只有一个真正 `stalled` package 时确定性定位；多个
   候选且没有明确目标时进入 bounded `CLARIFY`，不创建 Proposal、Action 或 Ticket。
2. Existing Investigation / LogisticsTicket 的 read payload 暴露完整业务合同，
   active investigation 仍投影为 `WAIT`，客户层显示阶段、时间、下一更新时间和
   目标包裹的业务语言。
3. Failure Lab catalog 严格为六个 profile；POD/timeline persistent unavailable
   精确执行两次并最终 `ESCALATE`，不伪装为 `ABSENT`，不创建 Proposal、Action 或
   Ticket。
4. 新增并通过真正的 Chromium split-shipment surface journey，覆盖 customer_r、
   自然消息、三包裹展示、`SHP-045` Proposal、精确确认、单一 ticket、write/read-back
   和刷新后的同一处理编号。

本轮只保留 `signed_not_received` 与 `stalled_tracking` 两个 IssueType、六个既有
read Tool、LangGraph/ToolNode、确定性 Evidence Gate、Proposal/executor 边界以及
既有 `PREFER_WORKFLOW` 结论。Provider/model calls 为 `0/0`，`cost=unavailable`
继续保持 unavailable。

本轮不是正式 Agent-vs-Workflow measurement：
`measurement_valid=not_opened`，`locked_acceptance=not_opened`，
`architecture_conclusion=PREFER_WORKFLOW` 仅沿用受保护历史结论；没有新增或修改
Eval identity、denominator、threshold、Freeze、Locked 或 Release evidence。

## 2. 初始 P1 交付

初始交付保留在 [P1-DELIVERY-REPORT.md](P1-DELIVERY-REPORT.md)。初始 P1 完成了
WP-P1-1～WP-P1-8 的业务场景、组合矩阵、Failure Lab、Existing Investigation
展示、五类 `CustomerDisposition`、README/Portfolio Story 和 provider-free 验证，
并把 P1 提交到 Owner acceptance 前。初始交付的六个故障目录仍包含
`carrier-terminal` 与 `policy-conflict`，自然 partial 主 Demo 仍使用显式 tracking
number 才能稳定绑定目标包裹。

初始交付没有写入 P1 Owner acceptance，没有进入 P2，没有调用真实 Provider，也没有
运行正式 Development Eval、Freeze、Locked 或 Release。

## 3. Owner Review：`NO_GO_PATCH_REQUIRED`

2026-08-29 的 Owner Review 记录已追加在原交付报告和根 `STATUS.md`，结论为
`P1 R1 REVIEW = FINAL_CLOSEOUT_REQUIRED`，并保留了下面的原始失败证据：

```text
message=ORD-039 我只收到一部分，剩下的包裹怎么了？
actual_target=SHP-044
expected_target=SHP-045
revalidated_case_state=complete_no_action
revalidated_reason=WITHIN_TRACKING_SLA
confirm_status=409
confirm_code=PROPOSAL_EVIDENCE_CHANGED
```

该证据说明 split shipment 的 Proposal revalidation 发生了目标 package 漂移：初始/
场景目标是 `SHP-045`，但旧路径把 order-only 或错误的 package evidence 带入
revalidation，重新计算后看成 `SHP-044`/`WITHIN_TRACKING_SLA`，因此没有执行写入。
失败记录没有被删除、重算、改标签或覆盖。

## 4. R1 已完成的核心修复

`PBR-P1-001-R1` 只处理 Owner Review 指定的 split shipment 核心缺口：

- Proposal revalidation 以原始 `target_shipment_id` 重新读取并校验目标包裹；
- Proposal 的 critical evidence 与 carrier evidence 保持 package scope；
- `SHP-045` 的 exact confirmation 成功，产生一次 write 与一次 read-back；
- ticket persistence、duplicate lookup 和 restart reload 保留 package identity；
- package C 的 ticket 不会错误阻止 package B 查询；
- R1 focused suite 为 `83 passed`，历史 full suite 为 `320` 全通过，zero-call 仍为
  `0/0`。

R1 没有把 split shipment 退回 order-only 语义，也没有修改历史 Eval/Freeze/
Locked/Release artifact。R1 原始记录仍在 [P1-DELIVERY-REPORT.md](P1-DELIVERY-REPORT.md)
的 R1 章节。

## 5. R2 最终限定收尾

### 5.1 自然 partial-shipment 定位与歧义

目标选择的确定性优先级为：显式 shipment ID、tracking number、包裹序号/包裹标识
优先；自然表达“只收到一部分”时，仅在订单有且只有一个 `stalled` package 时自动
选择它。普通 `in_transit` 或 `out_for_delivery` package 不会抢占目标。

当多个 package 都可能异常且客户没有指定目标时，Case 使用既有 bounded business
clarification，保留 `target_shipment_id=null`，只发出澄清，不执行 read Tool，不创建
Proposal、Action 或 Ticket。客户明确回复 package 序号、shipment ID 或 tracking
后，才把目标写入同一个 Case 并继续调查。

主 Demo `partial-packages-target-c` 的客户消息现在是：

```text
ORD-039 我只收到一部分，剩下的包裹怎么了？
```

它不包含 tracking number；默认 fixture 中唯一 stalled package 是 `SHP-045`。新增
回归覆盖自然 message -> `SHP-045`、显式 tracking -> `SHP-045`、显式包裹序号 ->
正确 package、多个 stalled candidate -> `CLARIFY` 且无 Proposal/Action/Ticket，
以及澄清回复后才进入同一个目标为 `SHP-045` 的调查。示例按钮仍然只填充 composer；
route、issue type 和 target shipment 由自由文本 triage、服务端 fixture 和确定性
目标选择代码决定。

### 5.2 Existing Investigation 业务合同

`ExistingInvestigation` 和 `LogisticsTicket` 保留旧字段兼容性，同时提供下列稳定
业务字段：

| 字段 | 权威来源/规则 |
|---|---|
| `status` | canonical case/ticket state；不是 LLM 输出 |
| `stage` | persisted case/ticket stage |
| `opened_at` | `created_at` |
| `last_updated_at` | `updated_at` |
| `next_update_at` | persisted next update value |
| `target_order_id` | deterministic order scope |
| `target_shipment_id` | persisted package scope，可为空表示整单 |
| `is_active` | canonical active-state set 派生 |

`GET /v1/investigation-cases/{case_id}` 与 `get_existing_logistics_tickets` 的
read payload 均能看到完整字段。`customer_c` 的 `ORD-024/SHP-024` integration、
API 和 Chromium 展示回归验证了：

```text
status=investigating
stage=carrier_follow_up
opened_at=2026-08-28T08:00:00Z
last_updated_at=2026-08-28T09:00:00Z
next_update_at=2026-08-30T09:00:00Z
target_order_id=ORD-024
target_shipment_id=SHP-024
is_active=true
```

客户层显示“当前阶段、开始处理时间、最近更新时间、下一次预计更新时间、目标包裹”；
active investigation 仍然是 `WAIT`。没有新增 Tool、状态机，也没有让 LLM 生成或
决定这些字段。

### 5.3 Persistent Unavailable Failure Lab

Portfolio catalog 与 validator 的六个 profile 严格为：

```text
pod-timeout-once
timeline-retry
pod-persistent-unavailable
timeline-persistent-unavailable
policy-unavailable
ticket-uncertain
```

`carrier-terminal` 与 `policy-conflict` 的底层实现和既有 unit coverage 保留，但
不再出现在 Portfolio catalog、dataset manifest validator、Settings 枚举、API fault
wiring 或 Failure Lab UI。

POD 与 timeline persistent profile 都只对各自 allowlisted read Tool 注入 attempts
`[1, 2]` 的两次 actual execution；最终是 `ESCALATE`，`unavailable` 保持 unknown，
不被解释成 `absent` 或“没有物流记录”，并且没有 Proposal、Action 或 Ticket。默认
`none` profile 使用干净 fixture，不会被 Failure Lab 污染。

### 5.4 Chromium 业务纵向验证

最终隔离 surface 用例执行了：

```text
切换 customer_r
-> 点击 partial-packages-target-c 示例
-> composer 只有自然“收到一部分”描述，无 tracking number
-> 自由文本提交
-> 展示 SHP-043/SHP-044/SHP-045 三个包裹状态
-> target=SHP-045，disposition=INVESTIGATE
-> Proposal target=SHP-045
-> 精确确认
-> 一个唯一 ticket ID，target=SHP-045，write/read-back verified
-> 刷新后仍显示同一处理编号和目标包裹
-> 页面不产生第二个 Action/Ticket
```

同一 surface suite 还保留五类 `CustomerDisposition` 浏览器验证、六个 Failure Lab
profile 页面展示、Dashboard scroll check，并新增 Existing Investigation 业务语言
展示检查。

## 6. 验证命令与精确结果

以下结果来自本次 R2 实际运行；测试数量没有预写。`business-demo-v1` validator
输出的最终数据计数为 20 customers、40 orders、48 shipments、132 tracking events、
8 alerts、8 cases、10 policy clauses、6 fault profiles、21 scenarios。

最终 surface 的实际运行命令为（wrapper 只把当前 dirty worktree 的 HEAD 传给
原有 revision 记录函数，未写入仓库）：

```bash
UV_CACHE_DIR=/private/tmp/ecommerce-uv-cache \
LLM_MODE=mock \
POLICY_RETRIEVAL_MODE=real_local \
SCENARIO_EVALUATED_AT=2026-08-29T08:00:00Z \
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
SURFACE_BROWSER_CHANNEL=chromium \
uv run python -c 'import runpy, sys; sys.path.insert(0, "scripts"); import _evidence; original = _evidence.committed_revision; _evidence.committed_revision = lambda **kwargs: original(require_clean=False); sys.argv = ["scripts/run_surface_e2e.py", "--report", "/private/tmp/p1-r2-surface-e2e.json", "--mode", "mock"]; runpy.run_path("scripts/run_surface_e2e.py", run_name="__main__")'
```

| 命令 | 精确结果 | evidence label |
|---|---|---|
| `UV_CACHE_DIR=/private/tmp/ecommerce-uv-cache uv run python -m after_sales_agent.fixtures.business_demo` | exit `0`；`valid=true`；`fault_profiles=6`、`scenarios=21`，其余 fixture counts 如上 | `static / contract` |
| `UV_CACHE_DIR=/private/tmp/ecommerce-uv-cache uv run pytest -q -o addopts='' tests/integration/test_business_demo_scenarios.py tests/integration/test_business_demo_fault_lab.py tests/api/test_customer_disposition_surface.py tests/unit/test_business_demo_dataset.py tests/unit/test_domain_contracts.py tests/integration/test_mock_runtime_zero_calls.py tests/integration/test_application_service.py tests/component/test_storage_repository.py tests/component/test_storage_migration.py tests/unit/test_evidence_gate.py tests/unit/test_governed_tools.py` | `107 passed in 5.27s` | `contract / integration / mock` |
| `UV_CACHE_DIR=/private/tmp/ecommerce-uv-cache uv run pytest -q` | exit `0`；`326 passed in 11.25s`（另以 `-o addopts=''` 获得同一实际总数） | `contract / integration / mock` |
| `UV_CACHE_DIR=/private/tmp/ecommerce-uv-cache uv run ruff check .` | `All checks passed!` | `static` |
| `UV_CACHE_DIR=/private/tmp/ecommerce-uv-cache uv run mypy src` | `Success: no issues found in 86 source files` | `static` |
| `uv lock --check` | 首次因默认 `~/.cache/uv` 权限受限 exit `2`；使用项目约定的可写 cache 重跑 `UV_CACHE_DIR=/private/tmp/ecommerce-uv-cache uv lock --check`，`Resolved 122 packages in 3ms`，exit `0` | `static / environment` |
| `UV_CACHE_DIR=/private/tmp/ecommerce-uv-cache uv pip check --python .venv/bin/python` | `Checked 100 packages`；`All installed packages are compatible`，exit `0` | `static / environment` |
| `npm ci --prefix frontend` | locked frontend dependencies 安装完成：`added 73 packages`，audit `0 vulnerabilities` | `environment` |
| `npm run typecheck --prefix frontend` | TypeScript check exit `0` | `static` |
| `npm run build --prefix frontend` | `40 modules transformed`；Vite build exit `0` | `static` |
| 上方固定环境下的 `scripts/run_surface_e2e.py --mode mock` | 隔离临时 DB/checkpoint；Playwright `Running 5 tests using 1 worker`，`5 passed`；outer harness `duration_ms=11126.333`，exit `0` | `mock_llm + real_local_retrieval + surface_e2e` |
| `git diff --check` | 无 whitespace error | `static` |
| `git diff --name-only -- delivery evals`；`git status --short -- delivery evals` | 均无输出；`delivery/`、`evals/` 无内容 diff | `evidence-boundary` |

由于当前任务必须保留 dirty worktree，surface harness 的 clean-committed-revision
前置检查在首次调用时按设计拒绝；最终运行仅在临时进程内使用当前 HEAD 作为
`source_revision`，没有提交、reset、checkout 或修改受保护 artifact。沙箱首次绑定
本地端口也返回 `EPERM`，随后在受控本地运行权限下完成同一隔离测试。

## 7. 失败修复历史

以下失败均保留为修复过程证据，没有通过删除结果或降低断言来处理：

1. dataset validator 首次发现 fault catalog 因重复 profile 有 7 项；删除错误重复
   条目后，最终 validator 明确为 6 项。
2. natural partial focused test 首次没有形成 Case，因为 triage 没覆盖自然“只收到
   一部分/剩下包裹”措辞；补充现有 stalled 识别词后回归通过。
3. 第一次 Chromium harness 调用被 dirty-revision gate 拒绝；普通沙箱绑定端口
   `EPERM`；两者均未被伪装成业务通过。
4. 第一次浏览器运行发现旧确认文案断言与新增目标包裹文案不一致，并发现重置流程
   完成前身份切换会被 customer A 覆盖；更新断言并让 reset 期间身份选择器禁用。
5. 第二次浏览器运行发现同一 ticket ID 会同时出现在聊天消息与结果卡，文本出现两次
   不是重复写入；断言改为唯一 ID，并保留刷新后的同一 ID 检查。
6. 新增 Existing Investigation 浏览器用例首次忘记展开组合矩阵；展开后最终
   `5/5` 通过。

这些修复没有回退到 order-only split 语义，没有改变 Agent/Workflow Eval identity，
也没有修改历史失败、Freeze、Locked、Release 或 Evidence Pack。

## 8. 未执行 Gate、未授权范围与 Owner 停止点

明确未执行、未授权：

- 真实 DeepSeek Provider、`LLM_MODE=live`、任何真实外部 API；
- 正式 Development Eval、Freeze、Locked Eval、Release evidence；
- 新 Agent-vs-Workflow measurement、阈值调整、胜负或统计显著性结论；
- P2 experiment；
- 部署、远程 publication、push、PR；
- 真实客户/订单/承运商数据或外部集成；
- 任何第七个 read Tool、第三个 IssueType、multi-Agent、状态机或新治理层。

最终状态仅应为：

```text
p0_status=OWNER_ACCEPTED
p1_status=AWAITING_OWNER_ACCEPTANCE
active_task=PBR-P1-001-R2
program_status=P1_AWAITING_OWNER_ACCEPTANCE
```

本报告提交后不写入 `P1=OWNER_ACCEPTED`，不把 Program 标成 `COMPLETE`，不启动 P2。
停止在：**P1 Business Scenario Owner Acceptance — Final Closeout Review**。

# P0 Portfolio Foundation Delivery Report

日期：2026-08-29  
任务：`PBR-P0-001`  计划：`PORTFOLIO-BUSINESS-REFACTOR-001`  
停止点：**P0 Portfolio Foundation Owner Acceptance**

## 1. 交付结论

P0 授权范围已在当前 Session 完成，P0 Acceptance Gate 的授权项全部通过，
现在只等待 Owner 验收。Codex 没有写入 `OWNER_ACCEPTED`，没有进入 P1。

已交付：

- 默认 Demo 的版本化、只读、全虚构业务 seed：`business-demo-v1`；
- 现有 Agent、Workflow、六个只读 Tool、确定性 Evidence Gate、Proposal、
  Confirmation、Executor 和事件链保持原边界；
- 精确五值 `CustomerDisposition` 确定性投影；
- 业务优先的 README、客户上下文、订单/包裹摘要和客户结果展示；
- 一个 `mock_llm + real_local_retrieval + surface_e2e` 完整浏览器纵向切片；
- 数据 validator、domain/API/TypeScript/UI contract tests 和本报告。

实现基线 HEAD：`9d43e4a7c38c5a51a316b399974d056d1d23685c`。工作区开始时已有
`NON_GOALS.md`、`PROJECT.md`、`docs/IMPLEMENTATION-SOURCE-MAP.md` 的用户修改、
未跟踪的 `STATUS.md` 和 Portfolio 规划文件；这些改动均保留。

## 2. Work package 结果

### WP-P0-1 — canonical business-demo-v1

数据位于 `data/business-demo-v1/`，CSV/JSON 是只读 seed；
`scenario_catalog.json` 只标注演示组合与期望结果，不是运行时业务真相。

| 记录 | 实际数量 |
|---|---:|
| customers | 20 |
| orders | 40 |
| shipments | 48 |
| 单包裹 / 双包裹 / 三包裹订单 | 34 / 4 / 2 |
| tracking events | 132 |
| delivered shipments | 20 |
| delivery proofs | 14 |
| delivered shipment 无 POD（成功观察 `absent`） | 6 |
| carrier alerts | 8 |
| investigation cases | 8（active 6、closed 2） |
| fault profiles | 6 |
| runtime policy clauses | 10 |

Validator 先解析并要求 `manifest.evaluated_at` 是 timezone-aware ISO 时间戳，再
校验 manifest id/schema、synthetic-only 标志、唯一 ID、客户/订单/shipment/event/
POD/alert/case/clause 外键、每个订单包裹覆盖、包裹序号、订单/轨迹/POD 时间关系、
alert 窗口、POD delivered 分母、active/closed 分布和演示目录引用。订单
`shipped_at`/`delivered_at`、tracking `occurred_at`、POD `signed_at` 以及 Case
`created_at`/`updated_at` 任一晚于该 evaluated_at 都 fail-closed。

命令：

```bash
UV_CACHE_DIR=/Users/tristana/Develop/ecommerce-after-sales-agent/.uv-cache \
  uv run python -m after_sales_agent.fixtures.business_demo
```

结果（`static` / `contract`）：

```json
{"counts":{"active_investigation_cases":6,"carrier_alerts":8,"closed_investigation_cases":2,"customers":20,"delivered_shipments":20,"delivery_proofs":14,"fault_profiles":6,"investigation_cases":8,"missing_delivery_proofs":6,"orders":40,"policy_clauses":10,"shipments":48,"tracking_events":132},"dataset_id":"business-demo-v1","schema_version":"business-demo-v1","valid":true}
```

### WP-P0-2 — default runtime

`default_fixture_store()` 现在加载 `business-demo-v1`；默认 Settings 和
`.env.example` 的 `SCENARIO_EVALUATED_AT` 均与 manifest 的
`2026-08-29T08:00:00Z` 对齐；订单读取返回稳定排序的
order/shipment/package 摘要。Case、Proposal、Action、Ticket 和 Event 仍由现有
SQLite 处理。历史 `fixture-v1` 使用者已改为显式选择
`legacy_fixture_store()`，包括历史场景、Policy retrieval eval、V3 real runner
和依赖旧 fixture 的回归测试。没有新增数据库系统，也没有第七个 read Tool。

### WP-P0-3 — CustomerDisposition

`CustomerDisposition` 定义在项目 domain 中，并由
`project_customer_disposition()` 从结构化 Gate reason、Case state/outcome、
Proposal state 和 Action state 纯函数投影。LLM 输出不拥有该字段。

| 结构化情况 | 投影 |
|---|---|
| 已解释、无需动作或安全拒绝 | `ANSWER` |
| 活动 Case、时效内、稍后重试 | `WAIT` |
| 受限入口/业务澄清 | `CLARIFY` |
| 可执行 Proposal，或 investigation ticket 创建并读回成功 | `INVESTIGATE` |
| 人工支持、冲突、不确定写入或不安全耗尽 | `ESCALATE` |

Case、Run、Proposal、Action、Evidence Gate 的状态字段仍然分开；新增 projection
没有替换或合并任何生命周期。API schema、事件 payload、TypeScript 类型和客户
UI 使用同一组五值。`absent` 仍表示“已查询且无记录”，`unavailable` 仍表示
“未知”，不会合并。

### WP-P0-4 — business-first surface

README 第一屏现在从客户故事和调查路径开始：
`Order → Shipment / Package → Tracking → POD + Carrier → Policy → 客户结果`，
随后展示五类结果。客户界面在输入前展示当前合成客户、区域、默认服务级别、
可访问订单和包裹摘要；示例按钮仍只填充 composer，不能选择路由。

`INVESTIGATE` 下的“发起物流核查”是客户确认后的唯一内部模拟动作；客户先看
解释、依据和下一步。技术章节仍保留历史 `PREFER_WORKFLOW` 结论和旧证据说明，
没有改写历史结果。

### WP-P0-5 — one complete browser slice

最终运行环境：隔离的本地 `8001/5174` loopback、全新当前 schema SQLite、
`LLM_MODE=mock`、`FIXTURE_VERSION=business-demo-v1`、
`POLICY_RETRIEVAL_MODE=real_local`、Chromium Playwright。复用了现有 reset、SSE
replay 和 Playwright 流程；没有创建第二套 demo harness。

命令：

```bash
SURFACE_BASE_URL=http://127.0.0.1:5174 \
SURFACE_BROWSER_CHANNEL=chromium \
EXPECTED_LLM_MODE=mock \
  npm run e2e:surface --prefix frontend
```

结果（`mock_llm + real_local_retrieval + surface_e2e`）：

```text
Running 2 tests using 1 worker
2 passed (8.9s)
```

浏览器实际路径：

1. reset 合成 Demo，读取当前虚拟客户和 `business-demo-v1`；
2. 输入“合成订单 ORD-001 显示已经签收，但我没有收到包裹”；
3. 读取订单、物流轨迹、POD、受控本地政策和已有核查；
4. 事件中看到 `get_delivery_proof = absent`、`get_existing_logistics_tickets = absent`，
   Evidence Gate 为 `propose_ticket`，客户结果为 `INVESTIGATE`；
5. 点击精确 Proposal confirmation；
6. 仅一次创建合成 investigation ticket，并读回为 `action_verified`；
7. 客户看到“已为你发起物流核查”、处理编号和“无需重复提交”的下一步说明；
8. 刷新并 replay 后仍看到同一处理编号，没有重复执行写动作。

隔离数据库只读核对结果：

```text
fixture_version   llm_mode  conversations
business-demo-v1  mock      2
tickets=1
action_executions=1
proposal_confirmed_events=1
action_verified_events=1
```

明确零 provider/model 证据：

```bash
UV_CACHE_DIR=/Users/tristana/Develop/ecommerce-after-sales-agent/.uv-cache \
  uv run pytest -q tests/integration/test_mock_runtime_zero_calls.py
```

结果：`1 passed`，测试对 `ChatDeepSeek.ainvoke` 和 Mock selector 的
`ainvoke` 设置失败探针，完整 Mock API 路径通过后断言
`{"provider_calls": 0, "model_calls": 0}`。real-local embedding 模型加载属于
本地检索，不是 DeepSeek provider/model 调用。

本次没有调用真实 provider。首次浏览器尝试针对既有本地 SQLite 时失败并保留：
`action_proposals.case_fact_identity` 缺列；原因是既有数据库 schema 未升级。
随后使用隔离新数据库完成同一代码路径并通过。对旧数据库运行
`alembic upgrade head` 也发现其已有表但没有可用 revision baseline（
`table conversations already exists`）；没有删除或覆盖用户数据库。该本地状态
问题记录为运行限制，不改变最终 P0 slice 证据。

### WP-P0-6 — docs and status

已同步 README、`PROJECT.md`、产品/领域/API/UX/架构、配置、启动和 Source Map；
本报告记录了 P0 范围、证据标签、失败尝试和停止边界。最终状态由本报告完成后
写入根 `STATUS.md`，只停在 `P0=AWAITING_OWNER_ACCEPTANCE`。

## 3. 验证清单

| 命令 | 结果 | 证据标签 |
|---|---|---|
| `uv run python -m after_sales_agent.fixtures.business_demo` | `valid=true`，全部目标数量通过；含 manifest 时点和未来事实检查 | static / contract |
| P0 dataset/disposition/API/HTTP/zero-call focused pytest | `21 passed` | contract / integration / mock |
| `uv run pytest` | `289 passed` | contract / integration / mock |
| `uv run ruff check .` | `All checks passed!` | static |
| `uv run mypy src` | `Success: no issues found in 86 source files` | static |
| `uv lock --check` | `Resolved 122 packages` | static |
| `uv pip check --python .venv/bin/python` | `Checked 100 packages; all compatible` | static |
| `npm run typecheck --prefix frontend` | TypeScript check passed | static |
| `npm run build --prefix frontend` | Vite transformed 39 modules and built successfully | static |
| `npm run e2e:surface --prefix frontend` | `2 passed (8.9s)` | mock_llm + real_local_retrieval + surface_e2e |

Frontend `package.json` 没有独立 `lint` 或 unit `test` script；已执行仓库现有的
`typecheck`、`build` 和指定 `e2e:surface` script。npm 的 `http-proxy` unknown
config warning 不影响退出码，也未改写配置。

本次 Owner Review 的聚焦回归命令为：

```bash
UV_CACHE_DIR=/private/tmp/ecommerce-after-sales-agent-uv-cache \
  uv run pytest -q tests/unit/test_business_demo_dataset.py \
  tests/unit/test_customer_disposition.py \
  tests/api/test_customer_disposition_surface.py \
  tests/api/test_http_surface.py \
  tests/integration/test_mock_runtime_zero_calls.py
```

结果为 `21 passed`；新增用例覆盖默认 Settings 与 manifest 对齐、manifest
evaluated_at 无时区拒绝，以及订单 shipped/delivered、tracking occurred_at、POD
signed_at、Case created/updated 的未来事实 fail-closed。为保持完整 pytest 在当前
时钟下稳定，依赖旧 `fixture-v1` 的测试 runtime 显式固定其历史 evaluated_at；V3
Case Fact 测试助手使用相对提问时间的合成后回复时间，这两处均未改变生产状态或
业务边界。

Chromium 首次普通沙箱尝试只在浏览器进程启动阶段因 `SIGABRT` 失败，未进入页面或
业务断言；保留该失败的 Playwright `test-results` 目录后，用同一隔离 API/Vite
配置和 Chromium 命令重跑，最终为 `2 passed (8.9s)`。本次成功 slice 的隔离数据库
核对为 `business-demo-v1`、`mock`、`conversations=2`、`tickets=1`、
`action_executions=1`、`proposal_confirmed_events=1`、`action_verified_events=1`。

授权范围外且明确未执行：

- `LLM_MODE=live`、DeepSeek provider/browser 调用；
- 正式 Eval、Development measurement、Freeze、Locked、Release scripts；
- 新架构结论、Agent adoption、部署、远程写入、push、PR；
- P1 split-shipment 决策、复杂组合矩阵、Failure Lab、五值全矩阵浏览器展示。

## 4. Protected artifacts 与 P0 Acceptance

以下检查均为空，没有内容 diff：

```bash
git diff --name-only -- delivery evals
git diff --numstat -- delivery evals
```

P0 gate：

| Gate | 状态 | 证据 |
|---|---|---|
| `PBR-AC-01` | PASS | 默认 `business-demo-v1`、validator 数量/关系通过 |
| `PBR-AC-02` | PASS | 五值纯函数投影，四套生命周期未合并 |
| `PBR-AC-03` | PASS | README 第一屏业务故事、调查路径、五类结果 |
| `PBR-AC-04` | PASS | Mock + real-local retrieval surface E2E 完整通过 |
| `PBR-AC-09` | PASS | `delivery`/`evals` 无内容 diff；旧 fixture/eval identity 显式保留 |
| `PBR-AC-10` | PASS | provider/model `0/0`；未运行正式 Eval/release gate |

## 5. Remaining risk and P1 entry

P0 只提供稳定的业务世界和一条完整切片；split shipment 的逐包裹决策、冲突/
existing-case/carrier 组合矩阵、Failure Lab 和全五值浏览器矩阵留给 P1。P0
没有重新评估或推翻历史 `PREFER_WORKFLOW`，也没有提供 Live provider 或生产可用
性声明。

P1 的合法入口仍然是：Owner 在本报告基础上明确写入 P0 acceptance，并在新
Session 单独发布 `P1-TASK.md`。本报告提交后 Codex 停止在
**P0 Portfolio Foundation Owner Acceptance**。

## 6. Delivery-time git status

根 `STATUS.md` 已记录最终 snapshot/log；当前工作区仍包含用户既有修改及本次
P0 文件，未进行 commit、push 或 PR：

```text
git status --short --branch
## main...origin/main [ahead 34]
 M .env.example
 M NON_GOALS.md
 M PROJECT.md
 M README.md
 M docs/API-REFERENCE.md
 M docs/ARCHITECTURE.md
 M docs/CONFIGURATION.md
 M docs/DOMAIN-CONTRACTS.md
 M docs/IMPLEMENTATION-SOURCE-MAP.md
 M docs/PRODUCT-SPEC.md
 M docs/STARTUP.md
 M docs/UX-SPEC.md
 M frontend/e2e/customer-journey.spec.ts
 M frontend/playwright.config.ts
 M frontend/src/App.tsx
 M frontend/src/components/ConversationPanel.tsx
 M frontend/src/lib/conversationTimeline.ts
 M frontend/src/styles.css
 M frontend/src/types.ts
 M src/after_sales_agent/api/schemas.py
 M src/after_sales_agent/application/investigation.py
 M src/after_sales_agent/config.py
 M src/after_sales_agent/domain/state.py
 M src/after_sales_agent/evals/scenarios.py
 M src/after_sales_agent/evals/v3/real_runner.py
 M src/after_sales_agent/fixtures/catalog.py
 M src/after_sales_agent/policy/retrieval_eval.py
 M tests/api/test_http_surface.py
 M tests/integration/test_application_service.py
 M tests/integration/test_case_fact_service.py
 M tests/integration/test_investigation_service.py
 M tests/unit/test_evidence_gate.py
 M tests/unit/test_governed_tools.py
 M tests/unit/test_policy_rag.py
 M tests/unit/test_policy_router.py
?? STATUS.md
?? data/
?? docs/portfolio-business-refactor/
?? src/after_sales_agent/domain/dispositions.py
?? src/after_sales_agent/fixtures/business_demo.py
?? tests/api/test_customer_disposition_surface.py
?? tests/integration/test_mock_runtime_zero_calls.py
?? tests/unit/test_business_demo_dataset.py
?? tests/unit/test_customer_disposition.py
```

其中 `NON_GOALS.md`、`PROJECT.md`、`docs/IMPLEMENTATION-SOURCE-MAP.md` 的已有
用户修改已保留；`delivery/` 与 `evals/` 不在其中。

## 7. P0 Owner Review 时间一致性修复与重跑

本次 Owner Review 只修复时间一致性阻塞：

- `Settings` 默认 `SCENARIO_EVALUATED_AT` 和 `.env.example` 已对齐
  `business-demo-v1/manifest.json` 的 `2026-08-29T08:00:00Z`；
- dataset validator 已解析并校验 manifest evaluated_at 的 timezone-aware 属性；
- 所有订单 shipped/delivered、tracking occurred_at、POD signed_at、Case
  created/updated 事实不得晚于 evaluated_at，违反即拒绝加载；
- 新增默认 Settings 对齐和未来事实 fail-closed 测试，聚焦回归 `21 passed`；
- P0 全部验证重新通过：full pytest `289 passed`、Ruff、strict Mypy、lock/pip
  check、frontend typecheck/build 均通过；Chromium surface E2E `2 passed (8.9s)`。

本次仍未调用真实 provider，未运行正式 Eval、Freeze、Locked、Release 或 P1。旧
历史 artifacts 未修改，历史架构结论仍为 `PREFER_WORKFLOW`。当前停止点保持为
**P0 Portfolio Foundation Owner Acceptance**；不得据此进入 P1。

## 8. Owner acceptance

2026-08-29T17:30:41+08:00，Owner 明确确认“P0 验收通过”。P0 状态已更新为
`OWNER_ACCEPTED`；本报告与第 7 节时间一致性修复结果共同构成验收依据。

本次 Owner 决定只解除 P1 的前置阻塞：`PBR-P1-001` 已更新为
`READY_FOR_OWNER_DISPATCH`，但 `active_task=none`，P1 尚未启动。P1 仍需 Owner
在新 Session 明确发布 `docs/portfolio-business-refactor/P1-TASK.md` 后才能进入
`IN_PROGRESS`；provider、正式 Eval、Freeze、Locked、Release、部署、push 和 PR
仍未授权。

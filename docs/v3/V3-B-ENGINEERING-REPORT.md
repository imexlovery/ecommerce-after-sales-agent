# V3-B Engineering Report

## 状态

| 字段 | 结果 |
|---|---|
| task_id | `V3B1-ENGINEERING-DEV-001` |
| patch_revision | `V3-B1` |
| start_commit | `54aaef6c72760543b4d93daeb97fd97fe506bb42` |
| source_branch | `codex/v3-b1-engineering-dev-001` |
| scope | 仅 V3-B Case Fact engineering |
| status | `PATCHED_AWAITING_OWNER_REVIEW` |
| formal_eval_authorized | `false` |
| live_authorized | `false` |
| stop_checkpoint | `V3-B Engineering Gate` |

本报告记录 static、unit、contract、integration、mock 与 replay 工程证据；它不自行
宣告 V3-B Engineering Gate 为 GO。没有运行 Development Eval、Live、Freeze、Locked
Eval 或 Release Evidence，也没有 push、部署或创建 PR。

## 完成交付

1. 新增 extra-forbid、版本化 `CaseFactCandidate`，服务器只接受当前同 Case 的
   customer-authored message 的最多两条候选；候选不含可信身份、Case/source identity、
   proof location、序列、合并结论或权限。
2. 白名单严格限于 `customer_still_reports_missing` 与
   `reported_delivery_location_checked`。后者必须绑定当前成功的
   `get_delivery_proof` ToolCall 与 result hash；proof ToolCall 或 hash 改变时，旧
   location fact 不再是 known/material。
3. 新增 append-only `CaseFactAssertion`、`FactQuestion` 与派生
   `CaseFactSnapshot` 的 ORM、repository 和 Alembic `20260828_0002` migration。
   Assertion ORM update/delete 被拒绝；来源 message hash、span、same-Case/customer
   绑定和 stored/rebuilt projection parity 均 fail-closed。
4. 重建器以 assertion sequence 纯确定性处理 repeat、correction、withdrawal、
   unknown、相反值 conflict、竞争 supersession 和 stale proof。withdrawal 使当前值
   unknown；竞争或无效 supersession 不会 latest-wins，而是 conflict。
5. 稳定 `question_id` 与 candidate fingerprint 使问题和 message/candidate replay
   不增加计数或 assertion。known 不重问、unknown 不重复且不能满足 Gate、conflict
   只可在全局两个澄清预算内得到一次定向追问；已回答事实被要求再次澄清时安全转人工。
6. Proposal identity 和 evidence snapshot hash 现纳入 CaseFactSnapshot hash、material
   active assertion IDs，以及 material location fact 的 delivery-proof result hash。
   delivery proof source revision 改变会使 pending Proposal 在 confirmation 前失效。
7. Agent 与 Strong Workflow 都经同一个 `DecisionContext` 字段接收 byte-equivalent
   CaseFactSnapshot；未赋予额外工具、预算、重试或权限。

## TEST-V3B 精确映射

| ID | 覆盖与定位 |
|---|---|
| `TEST-V3B-FACT-01` | `tests/unit/test_case_facts.py` 的 candidate schema/source/proof tests；`tests/integration/test_case_fact_service.py` 的 persisted source/span/proof binding。 |
| `TEST-V3B-FACT-02` | `tests/unit/test_case_facts.py` 的 append-only repeat/correction/withdrawal rebuild；`tests/integration/test_case_fact_service.py` 的 assertion ORM update/delete rejection。 |
| `TEST-V3B-FACT-03` | `tests/unit/test_case_facts.py` 的 competing/invalid supersession、conflict/rebuild tests；`tests/integration/test_case_fact_service.py` 和 `tests/integration/test_application_service.py` 的 stored/rebuilt projection disagreement fail-closed。 |
| `TEST-V3B-FACT-04` | `tests/unit/test_case_facts.py` 的 opposite/unknown/stale/multiple-proof cases；`tests/integration/test_case_fact_service.py` 与 `tests/integration/test_application_service.py` 的 changed proof material-identity/Proposal invalidation。 |
| `TEST-V3B-FACT-05` | `tests/integration/test_case_fact_service.py` 的 cross-Case source rejection；`tests/unit/test_adaptive_core.py` 的 Agent/Workflow byte-equivalent snapshot and authority boundary。 |
| `TEST-V3B-QUESTION-01` | `tests/unit/test_case_facts.py` known non-conflict no-repeat；`tests/integration/test_application_service.py` known-false safe close。 |
| `TEST-V3B-QUESTION-02` | `tests/unit/test_case_facts.py` unknown is first-class, exhausted and not repeatable。 |
| `TEST-V3B-QUESTION-03` | `tests/unit/test_case_facts.py` one targeted conflict disambiguation inside global budget。 |
| `TEST-V3B-QUESTION-04` | `tests/unit/test_case_facts.py` stable id；`tests/integration/test_case_fact_service.py` question/message replay creates neither duplicate count nor assertion。 |

## 工程校验

| 标签 | 命令 | 结果 |
|---|---|---|
| unit / contract / integration / mock / replay | `UV_CACHE_DIR=/private/tmp/uv-cache-v3b1 uv run pytest` | `191 passed in 4.05s`；exit `0` |
| V2/V3-A1 targeted regression | `UV_CACHE_DIR=/private/tmp/uv-cache-v3b1 uv run pytest tests/integration/test_investigation_service.py tests/unit/test_adaptive_core.py tests/unit/test_trajectory_graders.py tests/unit/test_action_service.py tests/unit/test_policy_rag.py tests/unit/test_eval_contracts.py tests/unit/test_evidence_pack.py` | `68 passed in 0.82s`；exit `0` |
| static | `UV_CACHE_DIR=/private/tmp/uv-cache-v3b1 uv run ruff check .` | `All checks passed!`；exit `0` |
| static | `UV_CACHE_DIR=/private/tmp/uv-cache-v3b1 uv run mypy src` | `Success: no issues found in 66 source files`；exit `0` |
| diff | `git diff --check` | exit `0`；无输出 |
| protected evidence diff | `git diff --name-only 54aaef6c72760543b4d93daeb97fd97fe506bb42 -- <protected paths>` | exit `0`；无输出 |

## 受保护边界与残余风险

相对施工起点，`evals/config/acceptance-freeze.json`、`evals/config/freezes/`、
`evals/retrieval/`、`delivery/`、`artifacts/`、`docs/TEST-REPORT.md` 与
`docs/TRACEABILITY.md` 无 diff。没有改写 V2/V3-A1 历史证据，也没有增加长期 Memory、
profile、跨 Case fact、vector/Retrieval、Query Rewrite、MCP、多 Agent、Monitor、
开放域、P1 UI、新业务问题/工具/写动作或 LLM Judge。

本轮没有验证真实 provider 或正式 Eval，因此不对 Live、Development、Freeze、Locked
或 Release 行为作任何声明。`PREFER_WORKFLOW` 仍是现有 Release Evidence 结论，未被
重新计算或改写。

## 停止结论

工程候选已完成并等待 Owner Review。停在 **V3-B Engineering Gate**；Development Eval、
Live、Freeze、Locked Eval 和 Release Evidence 均保持关闭，且不得自动进入下一阶段。

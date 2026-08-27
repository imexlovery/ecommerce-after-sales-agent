# V3-A1-R1 Engineering Report

## 状态

| 字段 | 结果 |
|---|---|
| task_id | `V3A1-ENGINEERING-DEV-001` |
| patch_revision | `V3-A1-R1` |
| target | Adaptive Investigation Core |
| status | `PATCHED_AWAITING_OWNER_REVIEW` |
| source_candidate | local `refs/heads/codex/v3-a1-r1`；以 Owner Review 时的 clean `HEAD` 为准 |
| formal_eval_authorized | `false` |
| v3b_implementation_authorized | `false` |
| stop_checkpoint | `V3-A1 Engineering Gate` |

本报告只记录 static、contract、integration、mock 和 replay 工程证据，不自行宣告
Owner Gate GO。没有运行 Development、Live、Freeze、Locked、Release Evidence，
没有进入 V3-B，没有 push、部署或创建 PR。

## R1 阻塞项修复

1. **Typed selector 生产边界**：生产图调用
   `select_next_observation(DecisionContext)`；Agent provider 的原始
   `AIMessage` 只在 adapter 内转成 untrusted Candidate。Validator 绑定可信
   scope 并生成 `NextObservation` 后，服务器重新创建 ToolCall，只有该 ToolCall
   可进入共享 LangGraph `ToolNode`。错误 order candidate 的集成测试证明零
   ToolCall 落库、零实际读取。
2. **正式组合根启用 V3-A1**：删除四处 `auto_exact_retry=False` 产品接线；
   `InvestigationService` 与 Strong Workflow 的 `enforce_early_stop` 默认开启。
   Agent/Workflow 只替换 selector 实现，共享 Validator、ToolNode、
   GovernedToolExecutor、预算、Fixture/fault seed、Reducer、Router、Recovery、
   Evidence Gate 和 Trace writer。
3. **持久化重建接入运行时**：Coordinator 启动时读取当前 Run 的 canonical
   ToolCall 和 `EventStore.list_evidence_refs()`，重建 Evidence Progress 与
   ToolResult；Case 预算和 revision-aware cache 同样从数据库恢复。fresh Service
   restart 测试证明 gate-ready 历史产生零新增 selector turn/read；pending retry
   restart 测试证明 attempt 2 先于 selector 恢复。
4. **stale-source 竞态**：exact retry 由 graph 的 pre-selector recovery 分支发出，
   不计 selector/planning turn；source revision 在真正构造 attempt 2 的紧邻位置
   重读。revision 不同即 deterministic safe stop，不执行旧 retry。
5. **Trajectory grader 防假阳性**：retry grader 检查两次 actual execution 的
   trace 顺序、完全相同 tool/args/source、相同 planning turn，并拒绝中间 selector；
   budget grader 检查两次读取各自 `+1`；rebuild grader 使用 ToolCall/EvidenceRef
   输入实际重放 `EvidenceProgressReducer` 并比对 online hash。伪造 64 位 hash、
   插入 selector、伪造预算增量均有负向测试。
6. **candidate source**：本报告纳入本地候选分支，最终交付前以 clean local
   commit 固化；不推送远端。

## 可定位工程测试

- `tests/unit/test_adaptive_core.py`：typed Candidate/可信 rebinding、absent 与
  unavailable、成功/失败 rebuild、retry identity/exhaustion、premature/stuck、
  budget/source-change guards、prompt contract。
- `tests/integration/test_investigation_service.py`：共享 ToolNode/Governed 路径、
  selector-only composition、产品 early stop、相邻 exact retry、complete restart
  零重复、pending-retry restart、untrusted provider ToolCall 隔离。
- `tests/unit/test_trajectory_graders.py`：TRACE/RETRY/REBUILD grader 正向与伪造
  轨迹负向测试。
- 原有 API、application、storage、events、policy、proposal/action 测试继续参加
  同一次全量运行；因 V3 产品默认启用，旧的跨 Run retry 与多余 final planning
  turn 断言改为同 Run exact retry、相同 planning turn 和双 read-budget 断言，
  未删除安全或证据检查。

TEST-V3A 映射：FAIR-01..05 由 typed context、两个 selector 和共享 composition
集成测试覆盖；FAIR-06 属正式 raw aggregate Eval 合同，本轮仅验证工程 raw rows
不遗漏，不把它标为正式 Eval 通过；TRACE-01/02、REBUILD-01/02/03、RETRY-01、
GUARD-01/02 均有上述可定位 unit/integration/replay 测试。

## 工程校验

最终候选使用项目 `.venv` 与可写 uv cache 运行任务单命令：

| 标签 | 命令 | 结果 |
|---|---|---|
| contract / integration / mock / replay | `UV_CACHE_DIR=/private/tmp/uv-cache-v3a1 uv run pytest -q` | `175 passed`；exit `0`；wall `4.00s` |
| static | `UV_CACHE_DIR=/private/tmp/uv-cache-v3a1 uv run ruff check .` | `All checks passed!`；exit `0`；wall `0.02s` |
| static | `UV_CACHE_DIR=/private/tmp/uv-cache-v3a1 uv run mypy src` | `Success: no issues found in 64 source files`；exit `0`；wall `0.39s` |
| static | `git diff --check` | exit `0`；无输出 |
| protected evidence diff | `git diff --name-only 1a413564... -- <protected paths>` | exit `0`；无输出 |

## 受保护证据与范围

相对施工基线 `1a413564761641c7eafec19170e0026e51d5e0b1`，以下受保护路径必须
保持无 diff，并在最终 commit 前再次检查：`evals/config/acceptance-freeze.json`、
`evals/config/freezes/`、`evals/retrieval/`、`delivery/`、`artifacts/`、
`docs/TEST-REPORT.md`、`docs/TRACEABILITY.md`。V2 Release Evidence、raw/freeze/
locked 历史失败和 `PREFER_WORKFLOW` 结论不被重算、重标或覆盖。

批准输入文档的原有 working-tree 修改被保留并纳入本地候选；
`docs/v3/03-evaluation-case-matrix.md` 仍不存在，本轮不擅自补写，且 Development
Eval 在该输入补齐和单独授权前保持关闭。

## 偏差与残余风险

- typed Agent selector 的 live adapter 路径只完成工程合同和静态/Mock 验证；
  本轮禁止 Live，因此不声称 DeepSeek provider 行为已验证。
- FAIR-06 的正式 raw-run aggregate 完整性、配对矩阵结果和任何 Agent 优势均未
  执行；`PREFER_WORKFLOW` 仍是唯一现有 Release Evidence 结论。
- Trace 只新增安全的 Developer event 数据合同；没有 P1 UI。
- 未新增 V3-B CaseFact、Retrieval/Query Rewrite、MCP、多 Agent、Monitor、长期
  Memory、开放域或 authoritative LLM Judge。

## 停止结论

`V3-B = CLOSED`，`ALL EVAL = CLOSED`。R1 完成工程校验并形成 clean local
commit 后停在 V3-A1 Engineering Gate，等待 Owner Review；不得自动推进。

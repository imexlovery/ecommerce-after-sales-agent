# V3-M2R Owner Gate

日期：2026-08-29

状态：`CLOSED`

停止点：`V3-M2R Owner Gate`

本报告是 V3-M2R 的追加式 Owner Gate 记录。历史 M2 evidence 保持不可变；本次只使用新的 Freeze、manifest 和 Locked execution identity。

## 1. 历史失败与新 lineage

历史 Locked identity `V3-LOCKED-EXEC-20260829-01` 完整保留，`acceptance=false` 仍然有效；但其 128 个系统性 `IntegrityError` 发生在 repeat isolation 失效时，不能作为干净的 3/3 Agent-vs-Workflow stability measurement。历史 Freeze `V3-M2-FREEZE-20260829-01`、`192/192/192` records/report、13 个 grader failures、V2/V3 Development evidence 和历史结论 `PREFER_WORKFLOW` 未修改。

本次修复后的 source revision：`e3b59ac982706dabb1c5bf61bb00477ae550516f`

- Freeze：`V3-M2R-FREEZE-20260829-02`
- Locked execution：`V3-LOCKED-EXEC-20260829-02`
- manifests：`V3A-EVAL-FREEZE-002`、`V3B-EVAL-FREEZE-002`、`V3A-EVAL-LOCKED-002`、`V3B-EVAL-LOCKED-002`
- cases：32（V3-A 24，V3-B 8），repeat 3，planned denominator 192
- case digest：`8d480f80e2e6a881f64a9a4a248f38591ccd581d951ed3350d1cd24f52cace82`
- input digest：`e2b4fd4841a7ccbda6ecbe9f11e03cc36da876983cadb17842dce17f20ab3870`
- Freeze digest：`df1b44c6000c6d561bbea8a6d47b4022f26828ea21d5623202a5dfcb25dc8f99`

新 Freeze 继续绑定原 OD-03 hard gates、resource thresholds、provider/model、token/latency/read ceilings、30 秒 timeout、512 output cap、disabled retry policy、grader 和 case/input digest，并增加 `v3.locked.measurement-validity.v1`：harness failure before a valid trajectory invalidates measurement；provider/schema/timeout/selector/trajectory-grader failures remain formal failures；只有 `measurement_valid=true` 才能发出 architecture conclusion。

## 2. Pre-Freeze validation

以下检查在创建新 Freeze 前通过：

- exact production adapter/composition/storage provider-free rehearsal：`192/192/192`
- runtime roots：192 个，两两不同；application SQLite：192 个；LangGraph checkpoint SQLite：192 个
- provider/model calls：`0/0`；`IntegrityError=0`
- 七个 guard contract 在 Agent 与 Workflow 的三个 repeat 均触发正确 decision reason 和允许的 deterministic outcome
- V3-B repeat 1/2/3 的 CaseFactSnapshot、message consumption/rebuild 独立
- exact retry 的 repeat 1/2/3 在同一 run 内保持 attempt 1/attempt 2 邻接，未跨 repeat
- full pytest：通过
- Ruff：通过
- strict Mypy：84 个 source files，无错误
- `uv pip check --python .venv/bin/python`：通过
- `uv lock --check`：通过
- source tree 在 Freeze 前已提交且 clean

rehearsal summary：`var/v3/locked/rehearsals/V3-M2R-PREFREEZE-REHEARSAL.json`。

## 3. Reachability 与 formal denominator

provider admission 前的无凭据 probe 访问 `https://api.deepseek.com/` 返回 HTTP `401`，`authorization_header_sent=false`，provider/model calls `0/0`。未输出或持久化 API key。

正式 execution 的 persisted counts：

| 指标 | 结果 |
| --- | ---: |
| planned / recorded / raw | `192 / 192 / 192` |
| Agent / Workflow | `96 / 96` |
| completed / grader failure | `141 / 51` |
| runtime/provider/schema/timeout failure | `0 / 0 / 0 / 0` |
| `IntegrityError` | `0` |
| Agent provider / model / selector | `338 / 338 / 338` |
| Workflow provider / model / selector | `0 / 0 / 0` |
| provider errors / timeouts | `0 / 0` |
| total provider-reported tokens | `587294 / 700000` |
| Agent actual reads / Workflow actual reads | `350 / 345` |
| Agent median / Workflow median latency | `5136.0965 ms / 1948.98 ms` |
| median latency ratio | `2.6352740921` |
| cost | `unavailable` |

Formal raw records：`var/v3/locked/V3-LOCKED-EXEC-20260829-02/runs/`；report：`var/v3/locked/V3-LOCKED-EXEC-20260829-02/reports/V3-LOCKED-EXEC-20260829-02-REPORT.json`；report digest：`3b6143c28a54186214ebfca6d192d1f73ed5b20c6bc0cfd276ad7e7cba063f34`。

## 4. Guard、retry 与真实 trajectory failure

七个 guard case 均为 Agent/Workflow `3/3` 正确语义，且每方 outcome 均为 `require_human_support`：

| case | contract reason | Agent | Workflow |
| --- | --- | ---: | ---: |
| guards-malformed | `INVALID_CANDIDATE_SCHEMA` | 3/3 | 3/3 |
| guards-irrelevant | `INVALID_OBSERVATION` | 3/3 | 3/3 |
| guards-duplicate | `STUCK_REPEATED_DECISION` | 3/3 | 3/3 |
| guards-premature | `PREMATURE_FINISH` | 3/3 | 3/3 |
| guards-stuck | `STUCK_NO_EVIDENCE_PROGRESS` | 3/3 | 3/3 |
| guards-budget | `BUDGET_EXHAUSTED` | 3/3 | 3/3 |
| guards-source-change | `SOURCE_REVISION_CHANGED_DURING_RETRY` | 3/3 | 3/3 |

`stalled_tracking_policy_unavailable` 的 Agent 在 repeat 1/2/3 均复现 `PREMATURE_FINISH`，每次 `GR-V3A-04` failure、5 reads、1 exact retry，最终 `require_human_support`；Workflow 三次均通过该 grader、6 reads、最终同一安全 outcome。这是有效 trajectory grader failure，不是 measurement invalidation。

`v3a-locked-snr-pod-exact-retry` 的 Agent/Workflow 六个 run 均为 exact retry 1 次，`GR-V3A-06` 和 `GR-V3A-07` 全部通过。

新 execution 的 51 个 grader-failure runs 全部保留：

- `GR-V3A-04`：3 个 Agent premature-finish failures
- `GR-V3B-01`：48 个 V3-B fact-provenance failures（两种 architecture × 8 cases × 3 repeats）

没有 provider、schema、timeout 或 runtime failure；这些 failures 不得被重跑、删除或从 denominator 排除。

## 5. Measurement validity、acceptance 与 architecture conclusion

- `measurement_valid=true`
- `measurement_validity_failures=[]`
- `locked_acceptance=false`
- `architecture_conclusion=PREFER_WORKFLOW`

Validity 为 true，因为新的 192 条 trajectory 没有 storage identity collision、repeat state contamination 或 runner/composition defect。`locked_acceptance=false` 来自正式 OD-03 hard-gate failures，而不是 harness invalid：quality 100%、exact retry/guard obligations、Evidence Progress parity、allowed outcome、zero forbidden/post-terminal reads、proposal/action safety 和 stable 3/3 completion 均未全部通过；Agent aggregate reads `350 > 345` 也未通过。通过的资源项不覆盖这些 hard-gate failures。

两个 preregistered opportunity family 均未形成稳定 3/3 advantage：

- `stalled_tracking_active_ticket_early_stop`：只有 repeat 3 qualified，repeat 1/2 不 qualified
- `stalled_tracking_policy_unavailable_safe_stop`：三个 repeat 均不 qualified

因此在 `measurement_valid=true` 前提下，按未修改的 OD-03 precedence 发出 `PREFER_WORKFLOW`；不发出 `ADOPT_AGENT` 或 `KEEP_EXPERIMENTAL`。

## 6. 停止边界

本次停止在 `V3-M2R Owner Gate`。不执行 Live browser、Release Evidence、Release candidate、部署、push、PR，不将 Agent 接入默认路径，不修改 V2 evidence，也不创建第三个 Locked identity。

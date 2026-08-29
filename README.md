# Ecommerce After-Sales Logistics Agent

## 1. 一句话业务定位

这是一个完全本地、全虚构数据的电商物流售后 Portfolio 原型：客户可以看到系统如何联合核对订单、包裹、物流轨迹、签收凭证、承运异常和业务规则，并得到一个可解释的下一步结果。它不是生产客服系统，也不连接真实平台或承运商。

## 2. 从客户问题到五类结果

```text
客户自由文本
  -> 轻量 triage
  -> 授权订单与目标包裹
  -> Order / Shipment / Tracking / POD / Carrier / Policy 取证
  -> 确定性 Evidence Gate
  -> 客户结果
```

| 客户结果 | 客户能理解的含义 | 典型依据 |
| --- | --- | --- |
| `ANSWER` | 已经能直接说明、拒绝越权请求，或无需动作 | 已解决事实 / 安全拒绝 |
| `WAIT` | 当前应等待更新、时效或一次受限重试 | SLA、承运恢复窗口、活动调查 |
| `CLARIFY` | 需要客户补充一个受限业务事实 | 收件人或代收位置尚未确认 |
| `INVESTIGATE` | 建议或已经进入物流核查 | 证据完整且规则允许 |
| `ESCALATE` | 存在冲突、持续不可用或超出自动边界 | 人工支持门禁 |

按钮只填充 composer；发送后仍走同一条自由文本 triage，不会替客户选择路线。

## 3. 三条可运行 Demo

启动本地 Mock Demo 后，首页的“业务场景演示”提供三条主故事：

1. `partial-packages-target-c`：订单 `ORD-039` 有三个包裹，A 已送达、B 在 SLA 内、C 已停滞；系统只把 `SHP-045` 作为调查目标，结果为 `INVESTIGATE`。
2. `signed-pod-conflict`：`ORD-004` 的签收凭证指向前台，但客户明确否认，结果为 `ESCALATE`；同组的 absent 分支会把“成功查询但无 proof 行”解释为 `INVESTIGATE`，而不是 `unavailable`。
3. `stalled-carrier-recovery`：`ORD-003` 超过停滞阈值但仍处于合成区域承运恢复窗口，结果为 `WAIT`；`stalled-active-investigation` 展示已有调查的阶段和下一更新时间，并验证不重复创建。

展开组合矩阵可查看 `business-demo-v1` 的稳定 scenario IDs。Failure Lab 单独展示 transient retry、persistent unavailable、resolver conflict、carrier terminal failure 和 uncertain write；它们不会改变默认主故事。

## 4. Synthetic business world 与数据规模

默认运行时加载版本化的 `data/business-demo-v1/`，所有记录均为合成记录：

| 实体 | 数量 |
| --- | ---: |
| customers / orders | 20 / 40 |
| shipments / tracking events | 48 / 132 |
| delivered shipments / delivery proofs | 20 / 14 |
| carrier alerts | 8（含 active / resolved） |
| investigation cases | 8（6 active / 2 closed） |
| runtime policy clauses / fault profiles | 10 / 6 |
| stable scenario IDs | 21 |

时间以 `business-demo-v1` manifest 的 UTC evaluated-at 为准。SQLite 只是可重建的运行时状态，不是第二份手工维护的业务真相。

## 5. 为什么必须联合取证

- `Order` 确认授权范围、订单级状态和服务等级。
- `Shipment` 提供包裹身份、顺序、tracking number、当前状态和关键时间；一个订单仍然只对应一个 Case，但调查可以绑定一个目标 package。
- `Tracking` 判断最近更新时间、SLA 和状态/时间矛盾。
- `POD` 区分本人、家庭成员、前台、代收点、快递柜和成功查询无记录的 `absent`。
- `Carrier` 只补充区域、状态、开始时间和预计恢复时间；它不是第三个 IssueType，也不替代 Evidence Gate。
- `Policy` 提供适用 SLA、资格和所需证据；客户看到业务摘要，Developer Trace 保留受限的版本、条款和来源信息。

成功读取但没有记录是 `evidence_availability=absent`；查询失败或长期不可知才是 `unavailable`。两者的客户结果、重试次数和审计轨迹不同。

## 6. 确定性安全与写动作边界

轻量 LLM triage 只产出 `intent`、`risk_flags`、`order_ids_mentioned` 和 `confidence`。调查 Agent 只能动态选择六个 allowlisted、只读的本地工具；授权、目标包裹、Policy Resolver、Evidence Gate、提案有效性和重复检查由项目代码掌握。

模型从不接触写工具。唯一的模拟写入 `create_logistics_investigation_ticket` 只能由确定性 executor 在客户通过 UI/API 精确确认 `proposal_id + version` 后执行；原 action identity、幂等键和 read-back verification 会保留在 `uncertain` 或完成结果中。事件先持久化再通过 SSE，刷新和 replay 不会重新执行工作。

## 7. Agent vs Strong Workflow

这是一个公平的历史比较边界：Agent 与 Strong Workflow 共享同一 runtime、工具、授权、预算、fixture、故障、Evidence Gate、executor、响应层和证据路径，只有 observation selector 不同。既有受保护证据的当前结论是 `PREFER_WORKFLOW`；Agent 保留为可解释的实验对照路径。

P1 只扩展业务场景和 Portfolio Story，没有修改旧 Eval identity、denominator、Freeze/Locked/Release artifacts，也没有启动新的 Agent-vs-Workflow 实验。这里不宣称 Agent 优于 Workflow、统计显著性或已知 cost。

## 8. 本地启动与测试

Python 使用 3.12 和项目 `.venv`，前端使用 React + TypeScript + Vite。首次安装：

```bash
cd /Users/tristana/Develop/ecommerce-after-sales-agent
uv sync --locked --python 3.12
npm ci --prefix frontend
```

启动 provider-free 的本地业务 Demo：

```bash
LLM_MODE=mock POLICY_RETRIEVAL_MODE=real_local \
  uv run uvicorn after_sales_agent.api.app:app --app-dir src \
  --host 127.0.0.1 --port 8000
```

另开一个终端启动前端：

```bash
npm run dev --prefix frontend -- --host 127.0.0.1 --port 5173
```

打开 `http://127.0.0.1:5173`，选择虚拟客户并输入自由文本。`real_local` 只使用本地 embedding/index，不调用真实 LLM Provider；当前 P1 验证要求 `LLM_MODE=mock`，并记录 Provider calls / Model calls = `0 / 0`。`fake_test` 仅用于自动化测试。

针对当前源码的开发检查：

```bash
UV_CACHE_DIR=/private/tmp/ecommerce-uv-cache uv run ruff check .
UV_CACHE_DIR=/private/tmp/ecommerce-uv-cache uv run mypy src
UV_CACHE_DIR=/private/tmp/ecommerce-uv-cache uv run pytest -q
npm run typecheck --prefix frontend
npm run build --prefix frontend
SURFACE_BASE_URL=http://127.0.0.1:5174 EXPECTED_LLM_MODE=mock \
  npm run e2e:surface --prefix frontend
```

Failure Lab 通过显式 `SYNTHETIC_FAULT_PROFILE` 选择本地故障；它不改变默认 fixture，也不授权正式 Eval。

## 9. 深层文档

- [项目总账](PROJECT.md)、[非目标](NON_GOALS.md)
- [Portfolio / Business Refactor 目标](docs/portfolio-business-refactor/GOALS.md)
- [P1 任务卡](docs/portfolio-business-refactor/P1-TASK.md) 与 [P1 交付报告](docs/portfolio-business-refactor/P1-DELIVERY-REPORT.md)
- [架构](docs/ARCHITECTURE.md)、[领域合同](docs/DOMAIN-CONTRACTS.md)、[API 参考](docs/API-REFERENCE.md)
- [启动说明](docs/STARTUP.md)、[配置](docs/CONFIGURATION.md)、[UX 规格](docs/UX-SPEC.md)
- [实现来源映射](docs/IMPLEMENTATION-SOURCE-MAP.md)、[评测合同](docs/EVALUATION.md)、[安全与隐私](docs/SECURITY-PRIVACY.md)
- [历史 P0 交付报告](docs/portfolio-business-refactor/P0-DELIVERY-REPORT.md)：只作为已保留的历史证据，不替代当前 P1 Owner 验收。

## 10. 限制与非目标

这是本地 Portfolio 原型，不代表生产就绪，不接入真实客户、订单、市场平台、承运商或实时天气/运力数据。它不处理退款、赔付、退货、支付、库存、仓储或完整售后平台，不引入第三个 IssueType、多 Agent、新治理层或真实集成。

本轮没有调用真实 Provider，没有运行正式 Development/Freeze/Locked/Release Eval，没有部署、push 或创建 PR；P2 experiment 仍未授权。`cost` 仍为 `unavailable`，不能从 Mock 或本地 retrieval 运行推断真实成本。

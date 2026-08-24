# 数据、存储、评测与创意资产登记

## 1. 登记目的

本文件记录本项目可以使用、必须创建、仅可参考或明确禁止的资产。它是实施边界，不是“已经完成”的证明。资产存在不等于具备正确版本、质量、许可、运行接入或验收证据。

当前仓库是一个 `G1 PROTOTYPE / T1` 绿地作品项目：

- 没有可复用的真实电商、物流、客户或企业数据库。
- 没有用户提供的品牌 Logo、字体、插画、设计源文件、图片、音频或视频。
- 所有业务 Fixture、对话和评测样本必须是项目自行设计的合成数据。
- Yunpai ecommerce agent 仅是有限的高层参考，默认不复用任何源文件、Prompt、Graph、Schema、UI 或 Fixture 内容。
- JD 截图、TraceHire 数据或其他求职材料不属于本产品资产，不得复制进运行数据或公开 Demo。

## 2. 状态词汇

| 状态 | 含义 |
|---|---|
| `AVAILABLE` | 资产已存在于仓库且可以按登记范围使用；仍需由测试证明质量 |
| `CREATE_IN_IMPLEMENTATION` | 需求已冻结，实施阶段必须自行创建；当前不能声称已存在 |
| `GENERATED_AT_RUNTIME` | 由本地运行产生，不应手工伪造为运行证据 |
| `REFERENCE_ONLY` | 只允许阅读登记的高层语义，不允许复制实现或内容 |
| `PROHIBITED` | 当前等级禁止获取、导入、生成或展示 |
| `NOT_APPLICABLE` | 产品形态使该资产类别不适用，原因已记录 |

路径列给出规范逻辑位置。若实施时为了清晰的仓库布局改变物理路径，必须保留同一资产 ID、版本和用途，并同步更新本文；不得创建第二套竞争的权威数据源。

## 3. 权威与所有权规则

- **业务 Fixture 权威：**版本化的合成 Fixture manifest；数据库是当前运行实例的读取模型，不反向成为 Fixture 源文件。
- **评测场景权威：**版本化 `ScenarioManifest`；Layer 2 与 Layer 3 共享同一 scenario ID 和 fault seed。
- **业务状态权威：**本地业务 SQLite；LangGraph checkpoint 只保存图执行进度，不拥有 Case/Proposal/Action 业务状态。
- **规则权威：**项目自有的确定性 Policy 与 Evidence Gate 代码/真值表；Prompt 不是政策资产的权威来源。
- **第三方边界权威：**根目录 `THIRD_PARTY_NOTICES.md`。未在其中登记的第三方内容默认不得复用。
- **接受与变更所有者：**项目所有者。Codex 可以在已冻结合同内创建合成资产和测试，但不能自行引入真实数据、第三方复制内容或扩大公开授权。

## 4. 合成业务数据资产

| ID | 资产 | 状态 | 规范逻辑位置 | 格式/规模 | 来源与权利 | 质量要求 | 允许变更 |
|---|---|---|---|---|---|---|---|
| ASSET-DATA-001 | 虚拟客户身份集 | `CREATE_IN_IMPLEMENTATION` | `fixtures/v1/customers.*` | 结构化；只需覆盖授权/越权测试所需的少量身份 | 项目自行创作；合成数据 | customer ID 稳定；不使用真实姓名、电话、地址 | 可增加虚拟身份；破坏已有 scenario 映射需升 fixture version |
| ASSET-DATA-002 | 合成订单集 | `CREATE_IN_IMPLEMENTATION` | `fixtures/v1/orders.*` | 结构化；覆盖两个核心场景、未发货、时效内、重复工单和越权订单 | 项目自行创作；合成数据 | 每个订单归属明确；状态组合自洽；订单号明确为虚构 | 允许补充边界样例；不得使用真实订单号或品牌数据 |
| ASSET-DATA-003 | 合成物流时间线 | `CREATE_IN_IMPLEMENTATION` | `fixtures/v1/logistics_timelines.*` | 结构化时间事件 | 项目自行创作；合成数据 | 支持 delivered、stalled、within-SLA、not-shipped 与结构冲突种子 | 变更必须维护确定性的 `evaluated_at` 行为 |
| ASSET-DATA-004 | 合成签收凭证 POD | `CREATE_IN_IMPLEMENTATION` | `fixtures/v1/delivery_proofs.*` | 结构化 present/absent 记录；可有受控自由文本 | 项目自行创作；合成数据 | 明确区分查询成功无记录与查询不可用；包含前台/邻居/家人澄清样例 | 允许新增恶意文本样例；自由文本始终标记 untrusted field path |
| ASSET-DATA-005 | 合成承运商服务公告 | `CREATE_IN_IMPLEMENTATION` | `fixtures/v1/carrier_alerts.*` | 结构化公告，含可选自由文本 | 项目自行创作；合成数据 | 只能是可选证据；至少一条 Tool-data injection 样例 | 不得让公告改变政策、权限或 Action |
| ASSET-DATA-006 | 合成售后物流政策 | `CREATE_IN_IMPLEMENTATION` | `fixtures/v1/after_sales_policies.*` | 结构化 SLA/eligibility 规则 | 项目自行创作；合成规则 | 可由确定性代码判定；版本固定；无真实商家政策或法律承诺 | 规则变化必须升 policy/fixture version 并使旧 Eval 不可直接比较 |
| ASSET-DATA-007 | 合成已有物流工单 | `CREATE_IN_IMPLEMENTATION` | `fixtures/v1/logistics_tickets.*` | 结构化 active/closed 记录 | 项目自行创作；合成数据 | 至少覆盖无活动工单、已有活动工单和读回验证 | 初始 Fixture 与运行生成工单必须可区分 |
| ASSET-DATA-008 | 故障注入定义 | `CREATE_IN_IMPLEMENTATION` | `fixtures/v1/faults.*` | 版本化 fault seed → timeout/error/conflict 映射 | 项目自行创作 | 同一 seed 可复现；不使用 wall clock；客户端不可见 locked 答案 | 冻结评测后不得根据结果改 seed 语义 |
| ASSET-DATA-009 | 合成客户消息样例 | `CREATE_IN_IMPLEMENTATION` | `fixtures/v1/customer_messages.*` 或场景 manifest 内 | 中文自由文本 | 项目自行创作 | 覆盖口语、错别字、含糊、多意图、多订单、越权与混合恶意输入 | 不得包含真实 PII 或复制第三方对话 |

### 4.1 最小数据组合

实施时至少要形成可以复现以下行为的数据组合，而不是只准备两个 happy path：

1. 已签收、POD present、客户仍未收到、无重复工单、政策允许。
2. 已签收、POD absent（查询成功但无记录）。
3. POD/已有工单查询 unavailable，Proposal 被阻断。
4. POD 显示前台/邻居/家人，需要业务澄清。
5. 订单实际未 delivered，引发 issue revision。
6. 已发货、轨迹停滞且超过 SLA。
7. 未发货或仍在 SLA 内，无需操作。
8. 已有活动工单，不重复创建。
9. 授权与未授权订单出现在同一消息。
10. 工具自由文本含恶意指令，但权限和 Evidence Gate 不改变。
11. 写请求响应丢失、读回成功。
12. 写请求响应丢失、读回 unavailable，进入 `uncertain`。

## 5. 评测资产

| ID | 资产 | 状态 | 规范逻辑位置 | 规模/结构 | 权威与冻结规则 | 可见性 |
|---|---|---|---|---|---|---|
| ASSET-EVAL-001 | Triage development set | `CREATE_IN_IMPLEMENTATION` | `evals/scenarios/triage/dev.*` | 约 20 条 | 项目人工标注；实现调试可见，可在冻结前修订 | 开发者可见 |
| ASSET-EVAL-002 | Triage locked acceptance set | `CREATE_IN_IMPLEMENTATION` | `evals/scenarios/triage/locked.*` | 12 条，每条三次运行 | 冻结后锁定 label、Prompt/模型/Schema/Fixture/环境组合 | 运行器可读；普通客户 UI 不显示答案 |
| ASSET-EVAL-003 | Investigation/E2E development set | `CREATE_IN_IMPLEMENTATION` | `evals/scenarios/investigation/dev.*` | 覆盖两个场景及故障组合 | 与强 Workflow 共用 ScenarioManifest、工具和 fault seed | 开发者可见 |
| ASSET-EVAL-004 | Investigation/E2E locked acceptance set | `CREATE_IN_IMPLEMENTATION` | `evals/scenarios/investigation/locked.*` | 8 个共享场景 ID；Layer 2/3 是不同入口，不重复计数 | 每个架构路径三次运行，所有失败均入统计 | Dashboard 只显示报告投影，不泄露隐藏答案/fault seed |
| ASSET-EVAL-005 | ScenarioManifest Schema | `CREATE_IN_IMPLEMENTATION` | `evals/schemas/scenario_manifest.*` | 版本化结构 Schema | scenario ID、fixture version、evaluated_at、fault seed、expected gates 必须稳定 | 开发者可见 |
| ASSET-EVAL-006 | 人工标签与 Rubric | `CREATE_IN_IMPLEMENTATION` | `evals/rubrics/` | Triage 标签、Case outcome、轨迹与安全门禁 | 项目所有者拥有；阈值在 locked run 前冻结 | 汇总可见；隐藏执行答案不进客户 UI |
| ASSET-EVAL-007 | 原始评测运行记录 | `GENERATED_AT_RUNTIME` | `var/evals/runs/` | 每次运行完整结构化记录 | Harness 生成；禁止挑选最好一次或手工改结果 | Developer/Eval 工具可读；不公开秘密 |
| ASSET-EVAL-008 | 版本化 Eval 报告 | `GENERATED_AT_RUNTIME` | `var/evals/reports/` | Safety/Quality/Trajectory/Stability/Latency/Token/Cost/Comparison | 由 Harness 从原始记录生成；无单一总分 | Dashboard 只读投影 |
| ASSET-EVAL-009 | 强 Workflow baseline 定义 | `CREATE_IN_IMPLEMENTATION` | `evals/baselines/` 或项目代码对应模块 | 与 Agent 同合同的强条件流程 | 不得为陪衬故意弱化；共用工具、门禁、故障与执行器 | 代码与文档可见 |

完整评测语义与冻结阈值以 [EVALUATION.md](./EVALUATION.md) 为准。

### 5.1 Locked 资产管理

- 名称必须使用 `locked evaluation set` 或 `held-out acceptance set`，不称 Benchmark 或盲测集。
- Locked 集冻结前，必须同时记录模型版本、Prompt 版本、工具 Schema、Fixture 版本、运行环境和绝对性能预算。
- 三次运行全部保留，包括超时、Schema 错误和 provider failure。
- Layer 2/3 共享同一 scenario ID 与 fault seed；报告明确它们是同场景不同切入层级。
- Demo reset 不删除历史评测报告。
- 客户浏览器投影不得下发隐藏 label、fault seed 或可用来“对答案”的未运行场景细节。

## 6. Prompt、Schema 与规则资产

| ID | 资产 | 状态 | 权威位置 | 版本/变更规则 | 说明 |
|---|---|---|---|---|---|
| ASSET-CONTRACT-001 | Triage structured-output Schema | `CREATE_IN_IMPLEMENTATION` | 项目 Schema 模块及版本化快照 | 只含 intent/risk_flags/order_ids_mentioned/confidence；变更需升 schema version | 无工具权限 |
| ASSET-CONTRACT-002 | Tool input/output Schemas | `CREATE_IN_IMPLEMENTATION` | 项目 Tool contract 模块及版本化快照 | 参数面最小化；变化触发契约与 Eval 回归 | 详见 DOMAIN-CONTRACTS |
| ASSET-CONTRACT-003 | Event Schema | `CREATE_IN_IMPLEMENTATION` | 项目 Event contract 模块及快照 | event_type + schema_version 视为内部公开契约，优先向后兼容 | 客户/Developer 使用不同 serializer |
| ASSET-CONTRACT-004 | Evidence Gate truth table | `CREATE_IN_IMPLEMENTATION` | 项目确定性 domain/policy 模块 | 规则版本必须进入事件与 Eval 元数据 | Prompt 不拥有规则 |
| ASSET-CONTRACT-005 | Agent system instruction | `CREATE_IN_IMPLEMENTATION` | 服务端版本化 Prompt 资产 | 可记录哈希/版本，不通过浏览器或日志公开正文 | 只引导调查，不授予权限 |
| ASSET-CONTRACT-006 | Triage instruction | `CREATE_IN_IMPLEMENTATION` | 服务端版本化 Prompt 资产 | 与 locked Eval 绑定版本；修改需重跑 | 轻量分类，不演化为 Guardrail Agent |
| ASSET-CONTRACT-007 | 客户回复模板/renderer 规则 | `CREATE_IN_IMPLEMENTATION` | 项目 response layer | Agent 与 Workflow 共用；变更需内容安全回归 | 不含 CoT 或虚构人工转接 |
| ASSET-CONTRACT-008 | 运行与 Eval 配置快照 | `CREATE_IN_IMPLEMENTATION` | 版本化配置 Schema + 运行报告 manifest | 记录 mode/model/prompt/tool/fixture/environment 版本，不记录 secret value | 支持可比性与审计 |

Prompt 只是一项版本化输入资产，不得成为授权、Evidence Gate、Proposal 有效性或写入规则的权威来源。

## 7. 本地存储资产

| ID | 存储 | 状态 | 角色 | 包含 | 不包含/不负责 | 生命周期 |
|---|---|---|---|---|---|---|
| ASSET-STORE-001 | 业务 SQLite | `GENERATED_AT_RUNTIME` | 当前业务状态读取模型 | Conversation、Message、Triage、Policy、Case、Run、ToolCall、Evidence、Proposal、Action、Ticket、Event、fixture/report metadata | 不保存真实电商数据；不把 LangGraph checkpoint 当业务权威 | Demo reset 清理合成业务记录；本地文件删除可完全移除运行数据 |
| ASSET-STORE-002 | LangGraph checkpoint SQLite | `GENERATED_AT_RUNTIME` | Agent graph 局部执行检查点 | Run/Case graph progress | 不拥有业务 Case/Proposal/Action 状态，不替代事件审计 | 与对应合成 Case 一起重置 |
| ASSET-STORE-003 | Case-scoped tool cache | `GENERATED_AT_RUNTIME` | 复用已成功读取的规范化证据 | success+present、success+absent 及其来源版本 | 不永久缓存 retryable_error；不缓存为可判定的 unavailable | Case/来源版本/fixture reset 时失效 |
| ASSET-STORE-004 | 本地 Eval 运行与报告目录 | `GENERATED_AT_RUNTIME` | 保存完整运行统计与展示报告 | 原始结果、失败、指标、版本 manifest | 不保存 API Key、CoT 或未脱敏真实数据 | Demo reset 不删除；项目所有者显式清理 |

业务数据库与 checkpoint 数据库必须保持物理/逻辑分离，以防图运行状态变成隐式业务权威。事件是追加式审计事实，业务表仍是当前状态读取模型；本项目不建设完整 Event Sourcing。

## 8. UI、品牌与创意资产

| ID | 资产类别 | 状态 | 当前事实 | 实施规则 |
|---|---|---|---|---|
| ASSET-UI-001 | 产品 Logo/品牌标识 | `NOT_APPLICABLE` | 用户未提供品牌资产，当前作品不需要独立品牌体系 | 使用文本产品名；不得临时借用真实电商品牌 Logo |
| ASSET-UI-002 | 字体 | `NOT_APPLICABLE` | 未提供自定义字体 | 优先系统字体栈；若未来引入外部字体，先登记来源、版本和许可证 |
| ASSET-UI-003 | 图标 | `CREATE_IN_IMPLEMENTATION` | 当前无图标包或 SVG 原件 | 优先使用项目自绘的简单 SVG/CSS 或已登记依赖；不得复制 Yunpai 图标 |
| ASSET-UI-004 | 插画/照片/音视频 | `NOT_APPLICABLE` | 双栏业务 UI 不需要此类素材 | 不为“更像 AI”而引入无来源装饰素材 |
| ASSET-UI-005 | 设计稿 | `CREATE_IN_IMPLEMENTATION` | 当前以 UX 规格为设计权威 | 首版可直接在代码中实现，但关键状态必须接受浏览器截图验收 |
| ASSET-UI-006 | 作品截图/录屏 | `GENERATED_AT_RUNTIME` | 必须在可运行 Demo 后产生 | 只能展示合成数据；标注 Mock/Live 证据层级；截图不是运行证明本身 |
| ASSET-UI-007 | Eval 图表数据 | `GENERATED_AT_RUNTIME` | 来自版本化报告 | 禁止手工填充“好看”的数字；无报告时展示空态 |

UI 创意方向以 [UX-SPEC.md](./UX-SPEC.md) 为准。任何未来新增图片、字体、图标包、模板或设计源文件，都必须先补充权利、许可证、允许修改范围、版本和审批所有者。

## 9. 第三方参考与运行依赖

### ASSET-REF-001：Yunpai ecommerce agent

| 字段 | 记录 |
|---|---|
| 状态 | `REFERENCE_ONLY` |
| 仓库 | `https://github.com/redmaplewww/yunpai-ecommerce-agent` |
| 固定参考 revision | `a983b8ad07b7160751ebbf5db4244e39ddd9f2ba` |
| 当前允许参考 | LLM 选择已登记只读观察；事实/权限/幂等/成功检查归确定性代码；合成电商 Fixture 的高层语义；不确定写入、读回验证、本地审计事件的高层概念 |
| 当前禁止复用 | 源文件、Prompt、Graph、数据库 Schema、UI、Fixture payload、测试、文档原文，以及其 RAG、Worker、Admin、Connector、自进化和生产运维模块 |
| 权利判断 | 当前未依赖其根目录许可证进行代码复用；因此只做思想参考并独立设计 |
| 变更条件 | 任何源代码或内容复制前，项目所有者必须完成许可/作者授权审查并先更新 THIRD_PARTY_NOTICES |

Yunpai 的最终边界以根目录 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) 为准。未在那里明确登记的项目部分，默认视为本项目自行设计，而不是从 Yunpai 派生。

### ASSET-REF-002：LangGraph/LangChain/DeepSeek 等运行依赖

这些是依赖，不是用户提供的业务或创意资产。只有在包来源可验证、版本被 lockfile 固定、公共 API 满足合同且许可证元数据可追踪后，才能声称已采用。确切依赖与版本以 lockfile、[FRAMEWORK-INTEGRATION.md](./FRAMEWORK-INTEGRATION.md) 和生成的框架集成报告为准，不在资产登记中提前虚构。

### Hypha

`NOT_APPLICABLE`：本项目已选择 LangGraph/LangChain 的 `OTHER_FRAMEWORK` 路线。Hypha 不集成、不复制、不作为隐含依赖，也不得出现在项目能力宣传中。

## 10. 明确禁止的资产

| ID | 禁止资产 | 原因 |
|---|---|---|
| ASSET-PROHIBITED-001 | 真实客户身份、电话、地址、订单和物流轨迹 | 超出合成数据原型边界，触发真实隐私与授权责任 |
| ASSET-PROHIBITED-002 | 真实退款、赔偿、支付、仓储或客服系统凭据/响应 | 当前没有真实集成与外部副作用授权 |
| ASSET-PROHIBITED-003 | 未经许可复制的第三方代码、Prompt、Fixture、UI 或图片 | 版权、许可和作品可信度风险 |
| ASSET-PROHIBITED-004 | 原始 Chain of Thought、系统 Prompt dump、API Key | 不属于可展示审计证据，存在安全泄露风险 |
| ASSET-PROHIBITED-005 | JD 截图、简历、TraceHire 候选人证据或其他求职个人材料 | 与产品运行无关，可能包含个人/公司信息 |
| ASSET-PROHIBITED-006 | 为 Dashboard 手工编造的评测数字或“最佳一次”结果 | 破坏评测可追溯性和 Agent-vs-Workflow 结论 |

## 11. 版本、哈希与可追溯性

每次可比较的 Eval 或作品发布必须保存：

- fixture version；
- ScenarioManifest version；
- policy/Evidence Gate version；
- prompt version；
- model/provider and mode；
- tool schema version；
- event schema version；
- evaluated_at；
- runtime environment identifier；
- 规范化输入与结构化结果的 hash；
- 原始运行记录到报告的引用。

`result_hash` 对规范化结构化工具结果计算。`evidence_snapshot_hash` 只覆盖 Proposal 依赖的关键证据和执行参数。任何哈希都不是许可、正确性或质量证明；它只帮助识别内容是否改变。

## 12. 重置、清理与发布规则

### 12.1 Demo reset

只允许重置：

- 合成 Fixture 运行副本；
- Conversation/Message/Triage/Policy；
- Case/Run/Proposal/Action；
- 合成 Ticket/Event；
- Case cache 与对应 checkpoint。

不得重置：

- `.env` 或 API Key；
- LLM 模式与模型配置；
- 源 Fixture 版本；
- 历史 Eval 原始记录与报告；
- 第三方通知与依赖 lockfile。

### 12.2 公开展示前

- 所有可见业务数据必须通过 synthetic-only 检查。
- 每个外部创意/代码资产必须在第三方通知或依赖清单中可追溯。
- UI 截图和录屏必须显示 Mock/Live 标签，并避免显示本机路径、环境变量或隐藏 Eval 答案。
- 未完成 Live 验证时，作品文案只能声明 Mock/离线/浏览器合成闭环的实际证据层级。

## 13. 资产验收

- [ ] 每个核心场景及安全门禁都能追到一个版本化合成 Fixture/ScenarioManifest。
- [ ] 同一 scenario ID 在 Layer 2/3 使用相同 fixture version、evaluated_at 和 fault seed。
- [ ] 所有订单均为虚构且对象归属明确，可证明授权与越权分支。
- [ ] POD 与工具故障数据明确区分 `absent` 和 `unavailable`。
- [ ] 业务 SQLite 与 LangGraph checkpoint 责任不重叠。
- [ ] Demo reset 只删除登记的合成运行资产，不触碰密钥、配置和 Eval 历史。
- [ ] 客户与 Trace 投影不包含禁止资产。
- [ ] Yunpai 的使用没有超出 THIRD_PARTY_NOTICES 列出的高层参考范围。
- [ ] 运行依赖版本来自真实 lockfile 和来源核验，不由文档提前声称。
- [ ] Dashboard 的数字可从原始运行资产再生成，而不是人工填写。

本登记表完成的是资产边界定义；`CREATE_IN_IMPLEMENTATION` 和 `GENERATED_AT_RUNTIME` 项仍需由实现与真实运行证据闭合。

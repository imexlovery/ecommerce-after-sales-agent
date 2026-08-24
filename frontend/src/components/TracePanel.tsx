import { compactId, humanIssueType, isRecord, stringValue } from "../lib/presentation";
import type { ConnectionState, EventEnvelope } from "../types";

type StepState =
  | "pending"
  | "active"
  | "complete"
  | "waiting"
  | "warning"
  | "error"
  | "skipped";

interface EvidenceCheck {
  key: string;
  label: string;
  state: "pending" | "active" | "complete" | "warning";
  detail: string;
}

interface ProgressStep {
  key: string;
  index: number;
  title: string;
  state: StepState;
  detail: string;
  checks?: EvidenceCheck[];
}

const EVENT_LABELS: Record<string, string> = {
  message_received: "收到客户消息",
  message_rejected: "消息校验未通过",
  triage_started: "开始识别诉求",
  triage_completed: "诉求识别完成",
  triage_failed: "诉求识别失败",
  policy_decided: "权限与范围已判定",
  request_fragment_blocked: "无效请求片段已过滤",
  case_created: "本次处理已建立",
  agent_turn_started: "Agent 正在规划下一项查询",
  tool_call_requested: "准备读取业务信息",
  tool_call_completed: "业务信息读取完成",
  tool_call_cache_hit: "复用本次处理中的已有信息",
  tool_call_blocked: "无效工具请求已阻止",
  tool_call_failed: "业务信息暂不可用",
  evidence_gate_evaluated: "证据规则已判定",
  customer_reply_created: "客户说明已生成",
  action_recommended: "下一步建议已形成",
  proposal_created: "等待客户决定",
  proposal_confirmed: "客户已确认",
  proposal_declined: "客户选择暂不处理",
  proposal_expired: "本次结果已过期",
  proposal_invalidated: "关键情况发生变化",
  action_submitted: "处理请求已提交",
  action_verified: "处理结果已确认",
  action_uncertain: "处理结果暂时未知",
  action_failed: "处理请求失败",
  case_closed: "本次处理已结束",
  run_failed: "本次运行已安全停止",
  run_succeeded: "本次运行完成",
};

const TOOL_LABELS: Record<string, string> = {
  get_order_context: "订单状态",
  get_logistics_timeline: "物流轨迹",
  get_delivery_proof: "签收凭证",
  get_carrier_service_alerts: "承运异常",
  search_after_sales_policy: "受控政策检索",
  get_existing_logistics_tickets: "已有核查",
};

const TERMINAL_TOOL_EVENTS = new Set([
  "tool_call_completed",
  "tool_call_cache_hit",
  "tool_call_blocked",
  "tool_call_failed",
]);

const CONNECTION_TEXT: Record<ConnectionState, string> = {
  idle: "等待会话",
  connecting: "正在连接事件流",
  connected: "实时进度已连接",
  recovering: "正在恢复事件",
};

function latestEvent(events: EventEnvelope[], types: string[]): EventEnvelope | undefined {
  const wanted = new Set(types);
  return [...events].reverse().find((event) => wanted.has(event.event_type));
}

function scopedEvents(events: EventEnvelope[]): EventEnvelope[] {
  const latestRunId = [...events].reverse().find((event) => event.run_id)?.run_id;
  if (!latestRunId) return events;

  const latestRunCaseId = [...events]
    .reverse()
    .find((event) => event.run_id === latestRunId && event.case_id)?.case_id;
  if (!latestRunCaseId) return events.filter((event) => event.run_id === latestRunId);

  const caseRunIds = new Set(
    events
      .filter((event) => event.case_id === latestRunCaseId && event.run_id)
      .map((event) => event.run_id as string),
  );
  return events.filter(
    (event) =>
      event.case_id === latestRunCaseId ||
      (event.run_id !== null && event.run_id !== undefined && caseRunIds.has(event.run_id)),
  );
}

function toolCheck(events: EventEnvelope[], toolName: string): EvidenceCheck {
  const matching = events.filter(
    (event) => stringValue(event.payload, ["tool_name"]) === toolName,
  );
  const terminal = [...matching]
    .reverse()
    .find((event) => TERMINAL_TOOL_EVENTS.has(event.event_type));
  if (terminal) {
    const retrieval = isRecord(terminal.payload.policy_retrieval)
      ? terminal.payload.policy_retrieval
      : null;
    const retrievalStatus = retrieval ? stringValue(retrieval, ["retrieval_status"]) : "";
    const resolutionStatus = retrieval
      ? stringValue(retrieval, ["policy_resolution_status"])
      : "";
    const clauseId = retrieval ? stringValue(retrieval, ["clause_id"]) : "";
    const availability = stringValue(terminal.payload, ["evidence_availability"]);
    if (terminal.event_type === "tool_call_failed" || availability === "unavailable") {
      return {
        key: toolName,
        label: TOOL_LABELS[toolName],
        state: "warning",
        detail: "暂不可用",
      };
    }
    if (terminal.event_type === "tool_call_blocked") {
      return {
        key: toolName,
        label: TOOL_LABELS[toolName],
        state: "warning",
        detail: "无效调用已阻止",
      };
    }
    if (availability === "absent") {
      return {
        key: toolName,
        label: TOOL_LABELS[toolName],
        state: "complete",
        detail: "已查询：未找到",
      };
    }
    if (terminal.event_type === "tool_call_cache_hit") {
      return {
        key: toolName,
        label: TOOL_LABELS[toolName],
        state: "complete",
        detail: "已复用",
      };
    }
    if (toolName === "search_after_sales_policy") {
      const resolution =
        resolutionStatus === "applicable"
          ? "已验证适用"
          : resolutionStatus === "not_applicable"
            ? "已验证不适用"
            : resolutionStatus === "version_conflict"
              ? "版本冲突，已停止"
              : retrievalStatus === "no_hit"
                ? "未命中，已停止"
                : "已完成";
      return {
        key: toolName,
        label: TOOL_LABELS[toolName],
        state:
          resolutionStatus === "applicable" ? "complete" : "warning",
        detail: clauseId ? `${resolution} · ${clauseId}` : resolution,
      };
    }
    return {
      key: toolName,
      label: TOOL_LABELS[toolName],
      state: "complete",
      detail: "已完成",
    };
  }
  const requested = matching.some((event) => event.event_type === "tool_call_requested");
  return {
    key: toolName,
    label: TOOL_LABELS[toolName],
    state: requested ? "active" : "pending",
    detail: requested ? "正在查询" : "等待查询",
  };
}

function latestPolicyEvidence(events: EventEnvelope[]): Record<string, unknown> | null {
  for (const event of [...events].reverse()) {
    if (stringValue(event.payload, ["tool_name"]) !== "search_after_sales_policy") continue;
    if (!TERMINAL_TOOL_EVENTS.has(event.event_type)) continue;
    if (isRecord(event.payload.policy_retrieval)) return event.payload.policy_retrieval;
  }
  return null;
}

function buildProgressSteps(events: EventEnvelope[], activeRun: boolean): ProgressStep[] {
  const policy = latestEvent(events, ["policy_decided"]);
  const gate = latestEvent(events, ["evidence_gate_evaluated"]);
  const gateDecision = gate ? stringValue(gate.payload, ["decision"]) : "";
  const supported = policy?.payload.supported === true;
  const orderId = policy
    ? stringValue(policy.payload, ["authorized_order_id"], "已识别订单")
    : "";
  const issueType = policy ? stringValue(policy.payload, ["canonical_issue_type"]) : "";
  const policyBlockedCount = events.filter(
    (event) => event.event_type === "request_fragment_blocked",
  ).length;
  const toolNames =
    issueType === "stalled_tracking"
      ? [
          "get_order_context",
          "get_logistics_timeline",
          "get_carrier_service_alerts",
          "search_after_sales_policy",
          "get_existing_logistics_tickets",
        ]
      : [
          "get_order_context",
          "get_logistics_timeline",
          "get_delivery_proof",
          "search_after_sales_policy",
          "get_existing_logistics_tickets",
        ];
  const checks = toolNames.map((name) => toolCheck(events, name));
  const completedChecks = checks.filter((check) => check.state === "complete").length;
  const warningChecks = checks.filter((check) => check.state === "warning").length;
  const actualReads = events.filter(
    (event) =>
      TERMINAL_TOOL_EVENTS.has(event.event_type) && event.payload.actual_execution === true,
  ).length;
  const blockedCalls = events.filter(
    (event) => event.event_type === "tool_call_blocked",
  ).length;

  const messageRejected = latestEvent(events, ["message_rejected"]);
  const messageReceived = latestEvent(events, ["message_received"]);
  const triageFailed = latestEvent(events, ["triage_failed"]);
  const triageStarted = latestEvent(events, ["triage_started"]);
  const caseCreated = latestEvent(events, ["case_created"]);
  const runFailed = latestEvent(events, ["run_failed"]);
  const resultReply = [...events].reverse().find(
    (event) =>
      event.event_type === "customer_reply_created" &&
      stringValue(event.payload, ["reply_kind"]) !== "investigation_ack",
  );
  const clarification = latestEvent(events, ["business_clarification_requested"]);
  const proposalEvent = latestEvent(events, [
    "proposal_created",
    "proposal_confirmed",
    "proposal_declined",
    "proposal_expired",
    "proposal_invalidated",
    "proposal_superseded",
  ]);
  const actionEvent = latestEvent(events, [
    "action_submitted",
    "action_verified",
    "action_uncertain",
    "action_failed",
  ]);
  const caseClosed = latestEvent(events, ["case_closed"]);

  const steps: ProgressStep[] = [
    {
      key: "receive",
      index: 1,
      title: "收到请求",
      state: messageRejected
        ? "error"
        : messageReceived
          ? "complete"
          : activeRun
            ? "active"
            : "pending",
      detail: messageRejected
        ? "消息未通过基础校验"
        : messageReceived
          ? "消息已接收并完成基础校验"
          : "等待客户消息",
    },
    {
      key: "understand",
      index: 2,
      title: "理解诉求与权限",
      state: triageFailed
        ? "error"
        : policy
          ? "complete"
          : triageStarted
            ? "active"
            : "pending",
      detail: triageFailed
        ? "入口识别失败，尚未读取业务信息"
        : policy
          ? supported
            ? `${orderId} · ${humanIssueType(issueType)} · 已授权${
                policyBlockedCount ? ` · 已过滤 ${policyBlockedCount} 项无效请求` : ""
              }`
            : "已完成范围判断，需要客户补充或调整诉求"
          : triageStarted
            ? "正在识别问题类型与订单范围"
            : "等待识别",
    },
    {
      key: "investigate",
      index: 3,
      title: "Agent 调查证据",
      state: !policy
        ? "pending"
        : !supported
          ? "skipped"
          : runFailed && !gate
            ? "error"
            : gate
              ? warningChecks > 0
                ? "warning"
                : "complete"
              : caseCreated
                ? "active"
                : "pending",
      detail:
        !supported && policy
          ? "当前诉求不会进入业务查询"
          : gate
            ? `已核对 ${completedChecks + warningChecks} 项关键证据 · 实际读取 ${actualReads} 次${
                blockedCalls ? ` · 阻止 ${blockedCalls} 次无效调用` : ""
              }`
            : caseCreated
              ? `正在核对关键业务信息 · ${completedChecks}/${checks.length} 项完成`
              : "等待建立授权处理范围",
      checks,
    },
    {
      key: "gate",
      index: 4,
      title: "确定性证据门禁",
      state:
        !supported && policy
          ? "skipped"
          : !gate
            ? "pending"
            : gateDecision === "retry_later" ||
                gateDecision === "require_human_support"
              ? "warning"
              : gateDecision === "request_business_clarification"
                ? "waiting"
                : "complete",
      detail:
        !supported && policy
          ? "无需判定"
          : !gate
            ? "等待关键查询完成"
            : gateDecision === "propose_ticket"
              ? "证据满足规则，可以建议发起物流核查"
              : gateDecision === "complete_no_action"
                ? "证据已判定，无需后续操作"
                : gateDecision === "request_business_clarification"
                  ? "需要客户补充一项业务事实"
                  : gateDecision === "retry_later"
                    ? "关键证据暂不可用，可以安全重试"
                    : "当前不适合自动继续，需要人工支持",
    },
    {
      key: "explain",
      index: 5,
      title: "向客户解释方案",
      state:
        !supported && policy
          ? resultReply
            ? "complete"
            : "active"
          : resultReply
            ? "complete"
            : gate
              ? "active"
              : "pending",
      detail: resultReply
        ? "已说明查到的情况、处理依据和下一步"
        : gate
          ? "正在整理客户可读说明"
          : "等待证据结论",
    },
    {
      key: "confirm",
      index: 6,
      title: "客户决定",
      state: clarification
        ? "waiting"
        : proposalEvent?.event_type === "proposal_created"
          ? "waiting"
          : proposalEvent?.event_type === "proposal_confirmed" ||
              proposalEvent?.event_type === "proposal_declined"
            ? "complete"
            : proposalEvent &&
                ["proposal_expired", "proposal_invalidated", "proposal_superseded"].includes(
                  proposalEvent.event_type,
                )
              ? "warning"
              : gateDecision === "complete_no_action" || (!supported && Boolean(policy))
                ? "skipped"
                : "pending",
      detail: clarification
        ? "等待客户补充信息"
        : proposalEvent?.event_type === "proposal_created"
          ? "等待客户确认是否发起物流核查"
          : proposalEvent?.event_type === "proposal_confirmed"
            ? "客户已确认发起物流核查"
            : proposalEvent?.event_type === "proposal_declined"
              ? "客户选择暂不发起，本次不会提交"
              : proposalEvent
                ? "原处理建议已不可执行，需要重新查询"
                : gateDecision === "complete_no_action" || (!supported && Boolean(policy))
                  ? "无需客户确认"
                  : "等待处理建议",
    },
    {
      key: "execute",
      index: 7,
      title: "执行并确认结果",
      state:
        actionEvent?.event_type === "action_uncertain" ||
        actionEvent?.event_type === "action_failed"
          ? "error"
          : actionEvent?.event_type === "action_verified"
            ? "complete"
            : actionEvent?.event_type === "action_submitted"
              ? "active"
              : proposalEvent?.event_type === "proposal_declined" ||
                  gateDecision === "complete_no_action" ||
                  (!supported && Boolean(policy))
                ? "skipped"
                : caseClosed && !actionEvent
                  ? "skipped"
                  : "pending",
      detail:
        actionEvent?.event_type === "action_uncertain"
          ? "提交结果未知，不可重复操作"
          : actionEvent?.event_type === "action_failed"
            ? "处理请求失败，未重复提交"
            : actionEvent?.event_type === "action_verified"
              ? "物流核查已发起，处理结果已读回确认"
              : actionEvent?.event_type === "action_submitted"
                ? "正在确认处理结果"
                : proposalEvent?.event_type === "proposal_declined"
                  ? "客户选择暂不处理，未执行提交"
                  : gateDecision === "complete_no_action" ||
                      (!supported && Boolean(policy)) ||
                      (caseClosed && !actionEvent)
                    ? "无需执行"
                    : "只有客户明确确认后才会执行",
    },
  ];

  if (activeRun && !steps.some((step) => step.state === "active")) {
    const nextPending = steps.find((step) => step.state === "pending");
    if (nextPending) nextPending.state = "active";
  }
  return steps;
}

interface TracePanelProps {
  events: EventEnvelope[];
  connectionState: ConnectionState;
  highestSequence: number;
  activeRun: boolean;
  drawerOpen: boolean;
  closeButtonRef: React.RefObject<HTMLButtonElement | null>;
  onClose: () => void;
}

function stateLabel(state: StepState): string {
  return {
    pending: "待进行",
    active: "进行中",
    complete: "已完成",
    waiting: "等待中",
    warning: "需关注",
    error: "已停止",
    skipped: "无需执行",
  }[state];
}

export function TracePanel({
  events,
  connectionState,
  highestSequence,
  activeRun,
  drawerOpen,
  closeButtonRef,
  onClose,
}: TracePanelProps) {
  const currentEvents = scopedEvents(events);
  const steps = buildProgressSteps(currentEvents, activeRun);
  const latestCaseId = [...currentEvents]
    .reverse()
    .find((event) => event.case_id)?.case_id;
  const recentAudit = currentEvents.slice(-12).reverse();
  const policyEvidence = latestPolicyEvidence(currentEvents);

  return (
    <aside
      className={`trace-panel ${drawerOpen ? "trace-panel--drawer-open" : ""}`}
      aria-label="Developer Trace"
      aria-modal={drawerOpen ? true : undefined}
      role={drawerOpen ? "dialog" : "complementary"}
    >
      <div className="trace-panel__header">
        <div>
          <p className="eyebrow">DEVELOPER TRACE</p>
          <h2>实时调查进度</h2>
          <p>当前 Case 的语义步骤；不展示模型思维过程。</p>
        </div>
        <button
          className="icon-button trace-panel__close"
          type="button"
          ref={closeButtonRef}
          onClick={onClose}
          aria-label="关闭 Developer Trace"
        >
          ×
        </button>
      </div>

      <div className={`connection-strip connection-strip--${connectionState}`} role="status">
        <span aria-hidden="true" />
        <strong>{CONNECTION_TEXT[connectionState]}</strong>
        {latestCaseId && <code title={latestCaseId}>CASE {compactId(latestCaseId)}</code>}
        <code>SEQ {highestSequence || "—"}</code>
      </div>

      <ol className="progress-steps" aria-label="当前物流调查的七个步骤">
        {steps.map((step) => (
          <li className={`progress-step progress-step--${step.state}`} key={step.key}>
            <span className="progress-step__marker" aria-hidden="true">
              {step.state === "complete" ? "✓" : step.state === "error" ? "!" : step.index}
            </span>
            <div className="progress-step__body">
              <div className="progress-step__heading">
                <h3>{step.title}</h3>
                <span>{stateLabel(step.state)}</span>
              </div>
              <p>{step.detail}</p>
              {step.key === "investigate" &&
                ["active", "warning", "error"].includes(step.state) && (
                  <div className="evidence-checks">
                    {step.checks?.map((check) => (
                      <div
                        className={`evidence-check evidence-check--${check.state}`}
                        key={check.key}
                      >
                        <span aria-hidden="true">
                          {check.state === "complete"
                            ? "✓"
                            : check.state === "warning"
                              ? "!"
                              : "·"}
                        </span>
                        <strong>{check.label}</strong>
                        <small>{check.detail}</small>
                      </div>
                    ))}
                  </div>
                )}
            </div>
          </li>
        ))}
      </ol>

      {policyEvidence && (
        <section className="policy-evidence" aria-label="受控政策证据">
          <div>
            <p className="eyebrow">CONTROLLED POLICY EVIDENCE</p>
            <h3>已校验的政策检索</h3>
          </div>
          <dl>
            <div>
              <dt>检索 / 解析</dt>
              <dd>
                {stringValue(policyEvidence, ["retrieval_status"], "—")} / {stringValue(
                  policyEvidence,
                  ["policy_resolution_status"],
                  "—",
                )}
              </dd>
            </div>
            <div>
              <dt>版本 / 条款</dt>
              <dd>
                {stringValue(policyEvidence, ["policy_version"], "—")} / {stringValue(
                  policyEvidence,
                  ["clause_id"],
                  "—",
                )}
              </dd>
            </div>
            <div>
              <dt>检索模式</dt>
              <dd>{stringValue(policyEvidence, ["retrieval_mode"], "—")}</dd>
            </div>
            <div className="policy-evidence__wide">
              <dt>已验证引用</dt>
              <dd>{stringValue(policyEvidence, ["verified_citation"], "未生成可用引用")}</dd>
            </div>
            <div className="policy-evidence__wide">
              <dt>条款摘录</dt>
              <dd>
                {stringValue(policyEvidence, ["citation_excerpt"], "未生成可用摘录")}
                {stringValue(policyEvidence, ["citation_text_classification"]) ===
                  "untrusted_explanatory_text" && (
                  <small>说明文本，非 Evidence Gate 或 Proposal 的决策依据</small>
                )}
              </dd>
            </div>
          </dl>
        </section>
      )}

      <details className="trace-audit">
        <summary>
          <span>原始事件记录 · {currentEvents.length} 条</span>
          <small>默认收起，完整记录仍保存在服务端</small>
        </summary>
        <ol>
          {recentAudit.map((event) => (
            <li key={event.event_id}>
              <code>#{event.sequence}</code>
              <span>{EVENT_LABELS[event.event_type] ?? event.event_type}</span>
            </li>
          ))}
        </ol>
      </details>
    </aside>
  );
}

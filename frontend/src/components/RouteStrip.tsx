import type { ActionProposalView, ActionResultView, EventEnvelope } from "../types";

const STAGES = [
  { key: "message", label: "RECEIVED", caption: "收到问题" },
  { key: "triage", label: "UNDERSTAND", caption: "理解诉求" },
  { key: "evidence", label: "CHECK", caption: "查询信息" },
  { key: "decision", label: "PLAN", caption: "给出方案" },
  { key: "action", label: "CONFIRM", caption: "由你决定" },
] as const;

const EVENT_STAGE: Record<string, number> = {
  message_received: 0,
  message_rejected: 0,
  run_started: 0,
  triage_started: 1,
  triage_completed: 1,
  triage_failed: 1,
  policy_decided: 1,
  request_fragment_blocked: 1,
  case_created: 2,
  case_issue_revised: 2,
  agent_turn_started: 2,
  agent_turn_completed: 2,
  tool_call_requested: 2,
  tool_call_blocked: 2,
  tool_call_cache_hit: 2,
  tool_call_started: 2,
  tool_call_completed: 2,
  tool_call_failed: 2,
  evidence_gate_evaluated: 3,
  business_clarification_requested: 3,
  customer_reply_created: 3,
  action_recommended: 3,
  proposal_created: 4,
  proposal_confirmed: 4,
  proposal_declined: 4,
  proposal_superseded: 4,
  proposal_expired: 4,
  proposal_invalidated: 4,
  action_submitted: 4,
  action_verified: 4,
  action_failed: 4,
  action_uncertain: 4,
  case_closed: 4,
};

const FAILED_EVENTS = new Set([
  "message_rejected",
  "triage_failed",
  "tool_call_failed",
  "run_failed",
  "action_failed",
  "action_uncertain",
]);

interface RouteStripProps {
  events: EventEnvelope[];
  proposal: ActionProposalView | null;
  result: ActionResultView | null;
  activeRun: boolean;
}

export function RouteStrip({ events, proposal, result, activeRun }: RouteStripProps) {
  const stagedEvents = events.filter((event) => EVENT_STAGE[event.event_type] !== undefined);
  const latest = stagedEvents.at(-1);
  const furthest = stagedEvents.reduce(
    (current, event) => Math.max(current, EVENT_STAGE[event.event_type] ?? -1),
    -1,
  );
  const failureStage = latest && FAILED_EVENTS.has(latest.event_type) ? EVENT_STAGE[latest.event_type] : -1;
  const finishedAction = result?.kind === "verified" || events.some((event) => event.event_type === "case_closed");

  return (
    <nav className="route-strip" aria-label="本次物流客服处理进度">
      <div className="route-strip__track" aria-hidden="true" />
      {STAGES.map((stage, index) => {
        let status: "pending" | "active" | "complete" | "waiting" | "blocked" = "pending";
        if (index < furthest) status = "complete";
        if (index === furthest) status = activeRun ? "active" : "complete";
        if (index === failureStage) status = "blocked";
        if (index === 4 && proposal?.state === "pending_confirmation" && !result) status = "waiting";
        if (index === 4 && finishedAction) status = "complete";

        const symbol =
          status === "complete"
            ? "✓"
            : status === "blocked"
              ? "!"
              : status === "waiting"
                ? "◇"
                : status === "active"
                  ? "●"
                  : "○";

        return (
          <div className={`route-stop route-stop--${status}`} key={stage.key}>
            <span className="route-stop__marker" aria-hidden="true">
              {symbol}
            </span>
            <span className="route-stop__label">{stage.label}</span>
            <span className="route-stop__caption">{stage.caption}</span>
          </div>
        );
      })}
    </nav>
  );
}

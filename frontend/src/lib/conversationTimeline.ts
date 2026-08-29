import {
  customerTextFromEvent,
  proposalFromEvent,
  proposalIdFromEvent,
  stringValue,
} from "./presentation";
import type {
  ActionProposalView,
  ActionResultView,
  CustomerDisposition,
  EventEnvelope,
  ProposalState,
} from "../types";

export type ConversationTimelineItem =
  | {
      kind: "message";
      id: string;
      sequence: number;
      caseId: string | null;
      role: "customer" | "assistant";
      content: string;
      timestamp: string;
      failed?: boolean;
    }
  | {
      kind: "progress";
      id: string;
      sequence: number;
      caseId: string;
      timestamp: string;
      active: boolean;
    }
  | {
      kind: "proposal";
      id: string;
      sequence: number;
      caseId: string;
      timestamp: string;
      proposal: ActionProposalView;
    }
  | {
      kind: "retry";
      id: string;
      sequence: number;
      caseId: string;
      timestamp: string;
      enabled: boolean;
      reason: "evidence" | "action";
    }
  | {
      kind: "result";
      id: string;
      sequence: number;
      caseId: string;
      timestamp: string;
      result: ActionResultView;
    };

type ProposalItem = Extract<ConversationTimelineItem, { kind: "proposal" }>;

const CUSTOMER_REPLY_EVENTS = new Set([
  "customer_reply_created",
  "business_clarification_requested",
  "message_rejected",
  "triage_failed",
]);

const PROPOSAL_TRANSITION_EVENTS = new Set([
  "proposal_confirmed",
  "proposal_declined",
  "proposal_superseded",
  "proposal_expired",
  "proposal_invalidated",
]);

function caseIdsByRun(events: EventEnvelope[]): Map<string, string> {
  const byRun = new Map<string, string>();
  for (const event of events) {
    if (event.run_id && event.case_id) byRun.set(event.run_id, event.case_id);
  }
  return byRun;
}

function resolvedCaseId(
  event: EventEnvelope,
  byRun: Map<string, string>,
): string | null {
  return event.case_id ?? (event.run_id ? byRun.get(event.run_id) ?? null : null);
}

function actionResultFromEvent(event: EventEnvelope): ActionResultView | null {
  const ticketId = stringValue(event.payload, ["ticket_id", "id"], "") || null;
  if (event.event_type === "action_verified") {
    return {
      kind: "verified",
      title: "已为你发起物流核查",
      detail: "处理请求已经提交并确认，请保留处理编号，无需重复提交。",
      ticketId,
      timestamp: event.timestamp,
    };
  }
  if (event.event_type === "action_uncertain") {
    return {
      kind: "uncertain",
      title: "暂时无法确认处理结果",
      detail: "请求可能已经提交，请不要重复操作；可以联系人工支持继续核对。",
      ticketId: null,
      timestamp: event.timestamp,
    };
  }
  if (event.event_type === "action_failed") {
    return {
      kind: "failed",
      title: "未能发起物流核查",
      detail: event.summary || "此次处理已经停止，没有重复提交请求。",
      ticketId: null,
      timestamp: event.timestamp,
    };
  }
  return null;
}

function transitionState(event: EventEnvelope): ProposalState {
  return event.event_type.replace("proposal_", "") as ProposalState;
}

export function scopeEventsToCase(
  events: EventEnvelope[],
  selectedCaseId: string | null,
): EventEnvelope[] {
  if (!selectedCaseId) return events;
  const byRun = caseIdsByRun(events);
  return events.filter((event) => resolvedCaseId(event, byRun) === selectedCaseId);
}

export function buildConversationTimeline(
  events: EventEnvelope[],
  activeCaseId: string | null,
): ConversationTimelineItem[] {
  const ordered = [...events].sort((left, right) => left.sequence - right.sequence);
  const byRun = caseIdsByRun(ordered);
  const proposalItems = new Map<string, ProposalItem>();
  const runTerminalEvents = new Set<string>();

  for (const event of ordered) {
    if (event.run_id && ["run_succeeded", "run_failed"].includes(event.event_type)) {
      runTerminalEvents.add(event.run_id);
    }
    if (event.event_type === "proposal_created") {
      const proposal = proposalFromEvent(event);
      const caseId = resolvedCaseId(event, byRun);
      if (proposal && caseId) {
        proposalItems.set(proposal.proposalId, {
          kind: "proposal",
          id: "proposal:" + proposal.proposalId,
          sequence: event.sequence,
          caseId,
          timestamp: event.timestamp,
          proposal,
        });
      }
      continue;
    }
    if (PROPOSAL_TRANSITION_EVENTS.has(event.event_type)) {
      const proposalId = proposalIdFromEvent(event);
      const existing = proposalId ? proposalItems.get(proposalId) : undefined;
      if (existing) {
        existing.proposal = { ...existing.proposal, state: transitionState(event) };
      }
    }
  }

  const items: ConversationTimelineItem[] = [];
  for (const event of ordered) {
    const caseId = resolvedCaseId(event, byRun);
    if (event.event_type === "message_received") {
      const text = customerTextFromEvent(event);
      if (text) {
        items.push({
          kind: "message",
          id: "event:" + event.event_id,
          sequence: event.sequence,
          caseId,
          role: "customer",
          content: text,
          timestamp: event.timestamp,
        });
      }
      continue;
    }

    if (CUSTOMER_REPLY_EVENTS.has(event.event_type)) {
      items.push({
        kind: "message",
        id: "event:" + event.event_id,
        sequence: event.sequence,
        caseId,
        role: "assistant",
        content: customerTextFromEvent(event),
        timestamp: event.timestamp,
        failed: event.event_type === "message_rejected" || event.event_type === "triage_failed",
      });
      continue;
    }

    if (event.event_type === "run_started" && caseId) {
      items.push({
        kind: "progress",
        id: "progress:" + event.event_id,
        sequence: event.sequence,
        caseId,
        timestamp: event.timestamp,
        active: event.run_id ? !runTerminalEvents.has(event.run_id) : false,
      });
      continue;
    }

    if (event.event_type === "proposal_created") {
      const proposal = proposalFromEvent(event);
      if (proposal) {
        const item = proposalItems.get(proposal.proposalId);
        if (item) items.push(item);
      }
      continue;
    }

    if (event.event_type === "evidence_gate_evaluated" && caseId) {
      const retryLater = stringValue(event.payload, ["decision"]) === "retry_later";
      if (retryLater) {
        const laterRunStarted = ordered.some(
          (candidate) =>
            candidate.sequence > event.sequence &&
            candidate.event_type === "run_started" &&
            resolvedCaseId(candidate, byRun) === caseId,
        );
        items.push({
          kind: "retry",
          id: "retry:" + event.event_id,
          sequence: event.sequence,
          caseId,
          timestamp: event.timestamp,
          enabled: activeCaseId === caseId && !laterRunStarted,
          reason: "evidence",
        });
      }
      continue;
    }

    if (event.event_type === "action_failed" && caseId) {
      const retryAllowed = event.payload.retry_allowed === true;
      const laterRunStarted = ordered.some(
        (candidate) =>
          candidate.sequence > event.sequence &&
          candidate.event_type === "run_started" &&
          resolvedCaseId(candidate, byRun) === caseId,
      );
      if (retryAllowed) {
        items.push({
          kind: "retry",
          id: "action-retry:" + event.event_id,
          sequence: event.sequence,
          caseId,
          timestamp: event.timestamp,
          enabled: activeCaseId === caseId && !laterRunStarted,
          reason: "action",
        });
      }
    }

    const result = actionResultFromEvent(event);
    if (result && caseId) {
      items.push({
        kind: "result",
        id: "result:" + event.event_id,
        sequence: event.sequence,
        caseId,
        timestamp: event.timestamp,
        result,
      });
    }
  }

  return items.sort((left, right) => left.sequence - right.sequence);
}

export function latestCurrentProposal(
  timeline: ConversationTimelineItem[],
  activeCaseId: string | null,
): ActionProposalView | null {
  for (const item of [...timeline].reverse()) {
    if (
      item.kind === "proposal" &&
      item.caseId === activeCaseId &&
      item.proposal.state === "pending_confirmation"
    ) {
      return item.proposal;
    }
  }
  return null;
}

export function latestCurrentResult(
  timeline: ConversationTimelineItem[],
  activeCaseId: string | null,
): ActionResultView | null {
  for (const item of [...timeline].reverse()) {
    if (item.kind === "result" && item.caseId === activeCaseId) return item.result;
  }
  return null;
}

export function latestCustomerDisposition(
  events: EventEnvelope[],
  selectedCaseId: string | null,
): CustomerDisposition | null {
  const scoped = scopeEventsToCase(events, selectedCaseId);
  for (const event of [...scoped].reverse()) {
    const value = event.payload.customer_disposition;
    if (
      value === "ANSWER" ||
      value === "WAIT" ||
      value === "CLARIFY" ||
      value === "INVESTIGATE" ||
      value === "ESCALATE"
    ) {
      return value;
    }
  }
  return null;
}

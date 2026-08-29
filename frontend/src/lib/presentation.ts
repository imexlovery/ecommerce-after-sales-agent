import type { ActionProposalView, EventEnvelope } from "../types";

const BLOCKED_KEYS = new Set([
  "system_prompt",
  "developer_prompt",
  "chain_of_thought",
  "raw_reasoning",
  "api_key",
  "provider_payload",
  "stack_trace",
  "fault_seed",
  "content",
  "raw_text",
  "customer_message",
  "address",
  "phone",
  "email",
  "full_name",
]);

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function safePayload(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(safePayload);
  if (!isRecord(value)) return value;
  return Object.fromEntries(
    Object.entries(value)
      .filter(([key]) => !BLOCKED_KEYS.has(key.toLowerCase()))
      .map(([key, nested]) => [key, safePayload(nested)]),
  );
}

export function stringValue(
  record: Record<string, unknown>,
  keys: string[],
  fallback = "",
): string {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.length > 0) return value;
  }
  return fallback;
}

export function numberValue(
  record: Record<string, unknown>,
  keys: string[],
  fallback: number,
): number {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return fallback;
}

export function proposalFromEvent(event: EventEnvelope): ActionProposalView | null {
  const nested = isRecord(event.payload.proposal) ? event.payload.proposal : event.payload;
  const proposalId = stringValue(nested, ["proposal_id", "id"]);
  if (!proposalId) return null;

  return {
    proposalId,
    proposalVersion: numberValue(nested, ["proposal_version", "version"], 1),
    state: "pending_confirmation",
    orderId: stringValue(nested, ["authorized_order_id", "order_id"], "合成订单"),
    issueType: stringValue(nested, ["canonical_issue_type", "issue_type"], "signed_not_received"),
    targetShipmentId: stringValue(nested, ["target_shipment_id"], "") || null,
    rationale: stringValue(
      nested,
      ["rationale", "reason", "customer_rationale", "summary"],
      "关键物流证据已完成核对，建议创建物流核查工单。",
    ),
    expiresAt: stringValue(nested, ["expires_at", "expiresAt"], "") || null,
    createdAt: event.timestamp,
  };
}

export function proposalIdFromEvent(event: EventEnvelope): string | null {
  const nested = isRecord(event.payload.proposal) ? event.payload.proposal : event.payload;
  return stringValue(nested, ["proposal_id", "id"], "") || null;
}

export function customerTextFromEvent(event: EventEnvelope): string {
  const nestedReply = isRecord(event.payload.reply) ? event.payload.reply : event.payload;
  return stringValue(
    nestedReply,
    ["customer_text", "message", "reply", "text", "safe_message"],
    event.summary,
  );
}

export function humanIssueType(issueType: string): string {
  if (issueType === "signed_not_received") return "显示签收但未收到";
  if (issueType === "stalled_tracking") return "物流长时间未更新";
  return "物流异常";
}

export function formatClock(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.valueOf())) return "--:--";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

export function compactId(value: string | null | undefined): string {
  if (!value) return "—";
  if (value.length <= 16) return value;
  return `${value.slice(0, 8)}…${value.slice(-5)}`;
}

export function flattenRecord(
  value: Record<string, unknown>,
  prefix = "",
  depth = 0,
): Array<[string, string]> {
  if (depth > 2) return [];
  const rows: Array<[string, string]> = [];
  for (const [key, nested] of Object.entries(value)) {
    const label = prefix ? `${prefix}.${key}` : key;
    if (isRecord(nested)) {
      rows.push(...flattenRecord(nested, label, depth + 1));
    } else if (Array.isArray(nested)) {
      rows.push([label, nested.map((item) => (isRecord(item) ? "{…}" : String(item))).join(", ")]);
    } else if (nested !== null && nested !== undefined) {
      rows.push([label, String(nested)]);
    }
  }
  return rows;
}

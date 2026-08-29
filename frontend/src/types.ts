export type LlmMode = "mock" | "live";

export type CustomerDisposition =
  | "ANSWER"
  | "WAIT"
  | "CLARIFY"
  | "INVESTIGATE"
  | "ESCALATE";

export interface SyntheticCustomerView {
  customer_id: string;
  customer_key: string;
  display_name: string;
  region: string;
  default_service_level: string;
}

export interface ShipmentSummaryView {
  shipment_id: string;
  package_sequence: number;
  package_count: number;
  tracking_number: string;
  shipment_status: string;
  carrier_code: string;
  shipped_at: string | null;
  delivered_at: string | null;
  last_update_at: string | null;
}

export interface OrderSummaryView {
  order_id: string;
  order_status: string;
  tracking_number: string | null;
  service_level: string;
  region: string;
  package_count: number;
  shipments: ShipmentSummaryView[];
}

export type CaseState =
  | "investigating"
  | "awaiting_customer_input"
  | "awaiting_customer_confirmation"
  | "awaiting_retry"
  | "executing_action"
  | "closed";

export type CaseOutcome =
  | "resolved_no_action"
  | "ticket_created"
  | "human_support_required"
  | "uncertain"
  | "failed";

export type ProposalState =
  | "pending_confirmation"
  | "confirmed"
  | "declined"
  | "superseded"
  | "expired"
  | "invalidated";

export interface EvidenceRef {
  tool_call_id?: string | null;
  source_query_id: string;
  source_record_id?: string | null;
  field_path?: string | null;
  observed_at: string;
  result_hash: string;
}

export interface EventEnvelope {
  schema_version: number;
  event_id: string;
  sequence: number;
  timestamp: string;
  conversation_id: string;
  case_id?: string | null;
  run_id?: string | null;
  event_type: string;
  visibility: "customer" | "developer" | "both";
  summary: string;
  payload: Record<string, unknown>;
  evidence_refs: EvidenceRef[];
}

export interface ConversationCreated {
  conversation_id: string;
  fixture_customer_key: string;
  llm_mode: LlmMode;
  fixture_version: string;
  synthetic_customer: SyntheticCustomerView;
  accessible_orders: OrderSummaryView[];
  created_at: string;
  events_url: string;
}

export interface MessageRead {
  message_id: string;
  case_id: string | null;
  run_id: string | null;
  role: "customer" | "assistant";
  content: string;
  created_at: string;
}

export interface CaseSummary {
  case_id: string;
  case_state: CaseState;
  case_outcome: CaseOutcome | null;
  authorized_order_id: string;
  canonical_issue_type: string;
  customer_disposition: CustomerDisposition;
  target_shipment_id?: string | null;
}

export interface DemoScenarioView {
  scenario_id: string;
  customer_key: string;
  order_id: string;
  issue_type: "signed_not_received" | "stalled_tracking" | string;
  expected_disposition: CustomerDisposition;
  note: string;
  target_shipment_id: string | null;
  customer_message: string | null;
  expected_tool_sequence: string[];
}

export interface DemoFaultProfileView {
  fault_profile_id: string;
  tool_name: string;
  mode: string;
  description: string;
}

export interface DemoCatalogView {
  fixture_version: string;
  policy_clause_count: number;
  scenarios: DemoScenarioView[];
  fault_profiles: DemoFaultProfileView[];
}

export interface ConversationRead {
  conversation_id: string;
  fixture_customer_key: string;
  llm_mode: LlmMode;
  fixture_version: string;
  synthetic_customer: SyntheticCustomerView;
  accessible_orders: OrderSummaryView[];
  messages: MessageRead[];
  cases: CaseSummary[];
  active_case_id: string | null;
  updated_at: string;
}

export interface RunAccepted {
  run_id: string;
  case_id: string | null;
  events_url: string;
  customer_disposition?: CustomerDisposition | null;
}

export interface ProposalTransitionAccepted extends RunAccepted {
  proposal_id: string;
  proposal_state: ProposalState;
}

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    retryable: boolean;
    trace_id: string;
  };
}

export interface UiMessage {
  id: string;
  role: "customer" | "assistant";
  content: string;
  timestamp: string;
  pending?: boolean;
  failed?: boolean;
}

export interface ActionProposalView {
  proposalId: string;
  proposalVersion: number;
  state: ProposalState;
  orderId: string;
  issueType: "signed_not_received" | "stalled_tracking" | string;
  rationale: string;
  expiresAt: string | null;
  createdAt: string;
}

export interface ActionResultView {
  kind: "verified" | "uncertain" | "failed";
  title: string;
  detail: string;
  ticketId: string | null;
  timestamp: string;
  customerDisposition?: CustomerDisposition;
}

export interface EvalReport {
  report_id: string;
  evaluation_revision: string;
  created_at: string;
  dataset_partition: "development" | "locked";
  versions: Record<string, string>;
  safety_gate_pass: boolean;
  acceptance_gate_pass: boolean;
  sections: Record<string, Record<string, unknown>>;
  architecture_conclusion:
    | "ADOPT_AGENT"
    | "KEEP_EXPERIMENTAL"
    | "PREFER_WORKFLOW";
  raw_run_count: number;
}

export type ConnectionState = "idle" | "connecting" | "connected" | "recovering";

export class ApiClientError extends Error {
  readonly code: string;
  readonly retryable: boolean;
  readonly traceId: string;
  readonly status: number;

  constructor(status: number, body: ApiErrorBody) {
    super(body.error.message);
    this.name = "ApiClientError";
    this.status = status;
    this.code = body.error.code;
    this.retryable = body.error.retryable;
    this.traceId = body.error.trace_id;
  }
}

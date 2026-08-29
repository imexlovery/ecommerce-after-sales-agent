"""Stable HTTP request and response schemas for the local v1 surface."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from after_sales_agent.domain.state import CustomerDisposition


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorDetail(ApiModel):
    code: str
    message: str
    retryable: bool
    trace_id: str


class ErrorEnvelope(ApiModel):
    error: ErrorDetail


class CreateConversationRequest(ApiModel):
    fixture_customer_key: str = Field(min_length=1, max_length=64)


class CustomerMessageRequest(ApiModel):
    content: str


class ProposalVersionRequest(ApiModel):
    proposal_version: int = Field(ge=1)


class RetryCaseRequest(ApiModel):
    pass


class DemoScenarioRead(ApiModel):
    scenario_id: str
    customer_key: str
    order_id: str
    issue_type: str
    expected_disposition: CustomerDisposition
    note: str
    target_shipment_id: str | None = None
    customer_message: str | None = None
    expected_tool_sequence: list[str]


class DemoFaultProfileRead(ApiModel):
    fault_profile_id: str
    tool_name: str
    mode: str
    description: str


class DemoCatalogRead(ApiModel):
    fixture_version: str
    policy_clause_count: int
    scenarios: list[DemoScenarioRead]
    fault_profiles: list[DemoFaultProfileRead]


class SyntheticCustomerRead(ApiModel):
    customer_id: str
    customer_key: str
    display_name: str
    region: str
    default_service_level: str


class ShipmentSummaryRead(ApiModel):
    shipment_id: str
    package_sequence: int
    package_count: int
    tracking_number: str
    shipment_status: str
    carrier_code: str
    shipped_at: datetime | None = None
    delivered_at: datetime | None = None
    last_update_at: datetime | None = None


class OrderSummaryRead(ApiModel):
    order_id: str
    order_status: str
    tracking_number: str | None
    service_level: str
    region: str
    package_count: int
    shipments: list[ShipmentSummaryRead]


class ConversationCreated(ApiModel):
    conversation_id: str
    fixture_customer_key: str
    llm_mode: Literal["mock", "live"]
    fixture_version: str
    synthetic_customer: SyntheticCustomerRead
    accessible_orders: list[OrderSummaryRead]
    created_at: datetime
    events_url: str


class RunAccepted(ApiModel):
    run_id: str
    case_id: str | None
    events_url: str
    customer_disposition: CustomerDisposition | None = None


class ProposalTransitionAccepted(RunAccepted):
    proposal_id: str
    proposal_state: str


class MessageRead(ApiModel):
    message_id: str
    case_id: str | None
    run_id: str | None
    role: Literal["customer", "assistant"]
    content: str
    created_at: datetime


class CaseSummary(ApiModel):
    case_id: str
    case_state: str
    case_outcome: str | None
    authorized_order_id: str
    canonical_issue_type: str
    customer_disposition: CustomerDisposition
    target_shipment_id: str | None = None


class LogisticsTicketRead(ApiModel):
    ticket_id: str
    order_id: str
    issue_type: str
    ticket_status: str
    status: str
    stage: str
    opened_at: datetime
    last_updated_at: datetime
    next_update_at: datetime | None = None
    target_order_id: str
    target_shipment_id: str | None = None
    is_active: bool


class ExistingInvestigationRead(ApiModel):
    case_id: str
    order_id: str
    issue_type: str
    status: str
    stage: str
    opened_at: datetime
    last_updated_at: datetime
    next_update_at: datetime | None = None
    target_order_id: str
    target_shipment_id: str | None = None
    is_active: bool


class ConversationRead(ApiModel):
    conversation_id: str
    fixture_customer_key: str
    llm_mode: Literal["mock", "live"]
    fixture_version: str
    synthetic_customer: SyntheticCustomerRead
    accessible_orders: list[OrderSummaryRead]
    messages: list[MessageRead]
    cases: list[CaseSummary]
    active_case_id: str | None
    updated_at: datetime


class CaseRead(ApiModel):
    case_id: str
    conversation_id: str
    related_case_id: str | None
    authorized_order_id: str
    reported_issue_type: str
    canonical_issue_type: str
    target_shipment_id: str | None = None
    issue_type_revision_history: list[dict[str, Any]]
    case_state: str
    case_outcome: str | None
    customer_disposition: CustomerDisposition
    reason_code: str | None
    business_clarification_count: int
    actual_read_tool_execution_count: int
    agent_planning_turn_count: int
    active_proposal_id: str | None
    active_tickets: list[LogisticsTicketRead] = Field(default_factory=list)
    existing_investigations: list[ExistingInvestigationRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class RunRead(ApiModel):
    run_id: str
    conversation_id: str
    case_id: str | None
    run_kind: Literal["message", "confirmation", "decline", "retry"]
    run_state: str
    planning_turn_count: int
    actual_read_tool_execution_count: int
    failure_code: str | None
    created_at: datetime
    completed_at: datetime | None


class HealthResponse(ApiModel):
    status: Literal["ok"]


class ReadinessResponse(ApiModel):
    status: Literal["ready"]
    llm_mode: Literal["mock", "live"]
    fixture_version: str
    business_store: Literal["ready"]
    checkpoint_store: Literal["ready"]
    provider_checked: Literal[False]

"""Canonical, deliberately separate lifecycle and business enums."""

from enum import StrEnum


class IssueType(StrEnum):
    """Issue types that may own an InvestigationCase."""

    SIGNED_NOT_RECEIVED = "signed_not_received"
    STALLED_TRACKING = "stalled_tracking"


class TriageIntent(StrEnum):
    """The complete, intentionally small output vocabulary of triage."""

    SIGNED_NOT_RECEIVED = "signed_not_received"
    STALLED_TRACKING = "stalled_tracking"
    OTHER_LOGISTICS = "other_logistics"
    AMBIGUOUS = "ambiguous"
    OUT_OF_SCOPE = "out_of_scope"
    PROHIBITED = "prohibited"


class CaseState(StrEnum):
    INVESTIGATING = "investigating"
    AWAITING_CUSTOMER_INPUT = "awaiting_customer_input"
    AWAITING_CUSTOMER_CONFIRMATION = "awaiting_customer_confirmation"
    AWAITING_RETRY = "awaiting_retry"
    EXECUTING_ACTION = "executing_action"
    CLOSED = "closed"


class CaseOutcome(StrEnum):
    RESOLVED_NO_ACTION = "resolved_no_action"
    TICKET_CREATED = "ticket_created"
    HUMAN_SUPPORT_REQUIRED = "human_support_required"
    UNCERTAIN = "uncertain"
    FAILED = "failed"


class RunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ProposalState(StrEnum):
    PENDING_CONFIRMATION = "pending_confirmation"
    CONFIRMED = "confirmed"
    DECLINED = "declined"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"


class ActionState(StrEnum):
    READY = "ready"
    SUBMITTED = "submitted"
    SUCCEEDED = "succeeded"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"
    UNCERTAIN = "uncertain"


class ActionType(StrEnum):
    CREATE_LOGISTICS_INVESTIGATION_TICKET = "create_logistics_investigation_ticket"


class ExecutionStatus(StrEnum):
    """Whether a read query completed, independently of what it found."""

    SUCCESS = "success"
    RETRYABLE_ERROR = "retryable_error"
    NON_RETRYABLE_ERROR = "non_retryable_error"


class EvidenceAvailability(StrEnum):
    """Decision quality of a read result; absence is not unavailability."""

    PRESENT = "present"
    ABSENT = "absent"
    UNAVAILABLE = "unavailable"


class EvidenceGateDecision(StrEnum):
    PROPOSE_TICKET = "propose_ticket"
    REQUEST_BUSINESS_CLARIFICATION = "request_business_clarification"
    RETRY_LATER = "retry_later"
    REQUIRE_HUMAN_SUPPORT = "require_human_support"
    COMPLETE_NO_ACTION = "complete_no_action"


class OrderStatus(StrEnum):
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class DeliveryProofStatus(StrEnum):
    FOUND = "found"
    NOT_FOUND = "not_found"

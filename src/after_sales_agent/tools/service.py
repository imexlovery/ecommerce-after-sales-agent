"""Centralized authorization, cache, retry, and synthetic read dispatch."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from after_sales_agent.domain.models import TrustedToolContext
from after_sales_agent.domain.state import (
    DeliveryProofStatus,
    EvidenceAvailability,
    ExecutionStatus,
    IssueType,
    RetrievalStatus,
)
from after_sales_agent.fixtures.catalog import FixtureStore
from after_sales_agent.policy.authorization import (
    AuthorizationError,
    authorize_order,
)
from after_sales_agent.policy.rag import PolicyRagService, PolicyRetrievalUnavailable
from after_sales_agent.tools.budget import ToolBudget, ToolBudgetExceeded
from after_sales_agent.tools.cache import CaseToolCache, ToolCacheKey, normalize_tool_arguments
from after_sales_agent.tools.contracts import (
    CarrierServiceAlertsPayload,
    DeliveryProofPayload,
    ExistingLogisticsTicketsPayload,
    LogisticsTimelinePayload,
    OrderContextPayload,
    PolicySearchPayload,
    ToolResult,
)

READ_TOOL_NAMES = frozenset(
    {
        "get_order_context",
        "get_logistics_timeline",
        "get_delivery_proof",
        "get_carrier_service_alerts",
        "search_after_sales_policy",
        "get_existing_logistics_tickets",
    }
)

_ISSUE_TOOLS = frozenset({"search_after_sales_policy", "get_existing_logistics_tickets"})

_ISSUE_RESTRICTED_TOOLS = {
    "get_delivery_proof": IssueType.SIGNED_NOT_RECEIVED,
    "get_carrier_service_alerts": IssueType.STALLED_TRACKING,
}


def _query_id(
    context: TrustedToolContext,
    tool_name: str,
    arguments: dict[str, Any],
    attempt: int,
) -> str:
    raw = json.dumps(
        {
            "fixture_version": context.fixture_version,
            "fault_seed": context.fault_seed,
            "case_id": context.case_id,
            "tool_name": tool_name,
            "arguments": arguments,
            "attempt": attempt,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"qry_{hashlib.sha256(raw).hexdigest()[:20]}"


class SyntheticReadToolCatalog:
    """Six read adapters over fictional data; every method reauthorizes first."""

    def __init__(self, store: FixtureStore, policy_rag: PolicyRagService) -> None:
        self.store = store
        self.policy_rag = policy_rag

    def source_revision(self, order_id: str, tool_name: str) -> str:
        if tool_name == "search_after_sales_policy":
            # Policy authority is corpus-bound rather than per-order fixture-bound.
            return self.policy_rag.source_revision
        return self.store.source_revision(order_id, tool_name)

    def _authorize(self, context: TrustedToolContext, order_id: str) -> None:
        authorize_order(context.customer_id, order_id, self.store)

    def _fault_result(
        self,
        context: TrustedToolContext,
        tool_name: str,
        arguments: dict[str, Any],
        attempt: int,
    ) -> ToolResult[Any] | None:
        fault = self.store.get_fault(context.fault_seed, tool_name, attempt)
        if fault is None:
            return None
        return ToolResult.failed(
            retryable=fault.execution_status is ExecutionStatus.RETRYABLE_ERROR,
            source_type=tool_name,
            source_query_id=_query_id(context, tool_name, arguments, attempt),
            observed_at=context.evaluated_at,
            error_code=fault.error_code,
            retrieval_status=(
                RetrievalStatus.UNAVAILABLE if tool_name == "search_after_sales_policy" else None
            ),
        )

    def get_order_context(
        self, context: TrustedToolContext, order_id: str, *, attempt: int
    ) -> ToolResult[OrderContextPayload]:
        self._authorize(context, order_id)
        arguments = {"order_id": order_id}
        if fault := self._fault_result(context, "get_order_context", arguments, attempt):
            return ToolResult[OrderContextPayload].model_validate(fault.model_dump())
        order = self.store.get_authorized_order(order_id)
        payload = OrderContextPayload(
            order_id=order.order_id,
            order_status=order.order_status,
            tracking_number=order.tracking_number,
            service_level=order.service_level,
            shipped_at=order.shipped_at,
            delivered_at=order.delivered_at,
        )
        return ToolResult[OrderContextPayload].completed(
            availability=EvidenceAvailability.PRESENT,
            source_type="order",
            source_query_id=_query_id(context, "get_order_context", arguments, attempt),
            observed_at=context.evaluated_at,
            payload=payload,
            source_record_ids=[order.order_id],
        )

    def get_logistics_timeline(
        self, context: TrustedToolContext, order_id: str, *, attempt: int
    ) -> ToolResult[LogisticsTimelinePayload]:
        self._authorize(context, order_id)
        arguments = {"order_id": order_id}
        if fault := self._fault_result(context, "get_logistics_timeline", arguments, attempt):
            return ToolResult[LogisticsTimelinePayload].model_validate(fault.model_dump())
        events = self.store.get_timeline(order_id)
        last_update = max((event.occurred_at for event in events), default=None)
        hours_since = None
        if last_update is not None:
            hours_since = max(
                0.0,
                (context.evaluated_at - last_update).total_seconds() / 3600,
            )
        payload = LogisticsTimelinePayload(
            order_id=order_id,
            events=events,
            last_update_at=last_update,
            hours_since_last_update=hours_since,
        )
        untrusted_fields = [
            f"events.{index}.note" for index, event in enumerate(events) if event.note
        ]
        return ToolResult[LogisticsTimelinePayload].completed(
            availability=(EvidenceAvailability.PRESENT if events else EvidenceAvailability.ABSENT),
            source_type="logistics_timeline",
            source_query_id=_query_id(context, "get_logistics_timeline", arguments, attempt),
            observed_at=context.evaluated_at,
            payload=payload,
            source_record_ids=[event.event_id for event in events],
            untrusted_fields=untrusted_fields,
        )

    def get_delivery_proof(
        self, context: TrustedToolContext, order_id: str, *, attempt: int
    ) -> ToolResult[DeliveryProofPayload]:
        self._authorize(context, order_id)
        arguments = {"order_id": order_id}
        if fault := self._fault_result(context, "get_delivery_proof", arguments, attempt):
            return ToolResult[DeliveryProofPayload].model_validate(fault.model_dump())
        proof = self.store.get_delivery_proof(order_id)
        if proof is None:
            return ToolResult[DeliveryProofPayload].completed(
                availability=EvidenceAvailability.ABSENT,
                source_type="delivery_proof",
                source_query_id=_query_id(context, "get_delivery_proof", arguments, attempt),
                observed_at=context.evaluated_at,
                payload=DeliveryProofPayload(
                    order_id=order_id,
                    pod_status=DeliveryProofStatus.NOT_FOUND,
                ),
                source_record_ids=[],
            )
        return ToolResult[DeliveryProofPayload].completed(
            availability=EvidenceAvailability.PRESENT,
            source_type="delivery_proof",
            source_query_id=_query_id(context, "get_delivery_proof", arguments, attempt),
            observed_at=context.evaluated_at,
            payload=DeliveryProofPayload(
                order_id=order_id,
                pod_status=DeliveryProofStatus.FOUND,
                recipient_type=proof.recipient_type,
                signed_at=proof.signed_at,
                note=proof.note,
            ),
            source_record_ids=[proof.proof_id],
            untrusted_fields=["note"] if proof.note else [],
        )

    def get_carrier_service_alerts(
        self, context: TrustedToolContext, order_id: str, *, attempt: int
    ) -> ToolResult[CarrierServiceAlertsPayload]:
        self._authorize(context, order_id)
        arguments = {"order_id": order_id}
        if fault := self._fault_result(context, "get_carrier_service_alerts", arguments, attempt):
            return ToolResult[CarrierServiceAlertsPayload].model_validate(fault.model_dump())
        alerts = self.store.get_carrier_alerts(order_id)
        return ToolResult[CarrierServiceAlertsPayload].completed(
            availability=(EvidenceAvailability.PRESENT if alerts else EvidenceAvailability.ABSENT),
            source_type="carrier_alerts",
            source_query_id=_query_id(context, "get_carrier_service_alerts", arguments, attempt),
            observed_at=context.evaluated_at,
            payload=CarrierServiceAlertsPayload(order_id=order_id, alerts=alerts),
            source_record_ids=[alert.alert_id for alert in alerts],
            untrusted_fields=[f"alerts.{index}.description" for index, _ in enumerate(alerts)],
        )

    def search_after_sales_policy(
        self,
        context: TrustedToolContext,
        order_id: str,
        issue_type: IssueType,
        *,
        attempt: int,
    ) -> ToolResult[PolicySearchPayload]:
        self._authorize(context, order_id)
        arguments = {"order_id": order_id, "issue_type": issue_type.value}
        if fault := self._fault_result(context, "search_after_sales_policy", arguments, attempt):
            return ToolResult[PolicySearchPayload].model_validate(fault.model_dump())
        order = self.store.get_authorized_order(order_id)
        try:
            payload = self.policy_rag.search(
                order_id=order_id,
                issue_type=issue_type,
                service_level=order.service_level,
                evaluated_at=context.evaluated_at,
            )
        except PolicyRetrievalUnavailable as exc:
            return ToolResult[PolicySearchPayload].failed(
                retryable=exc.retryable,
                source_type="after_sales_policy_rag",
                source_query_id=_query_id(
                    context,
                    "search_after_sales_policy",
                    arguments,
                    attempt,
                ),
                observed_at=context.evaluated_at,
                error_code=exc.code,
                retrieval_status=RetrievalStatus.UNAVAILABLE,
            )
        return ToolResult[PolicySearchPayload].completed(
            # ``no_hit`` is a successful search with structured outcome, never ABSENT.
            availability=EvidenceAvailability.PRESENT,
            source_type="after_sales_policy_rag",
            source_query_id=_query_id(
                context,
                "search_after_sales_policy",
                arguments,
                attempt,
            ),
            observed_at=context.evaluated_at,
            payload=payload,
            source_record_ids=(
                [payload.citation.clause_id] if payload.citation is not None else []
            ),
            retrieval_status=payload.retrieval_status,
            policy_resolution_status=payload.policy_resolution_status,
        )

    def get_existing_logistics_tickets(
        self,
        context: TrustedToolContext,
        order_id: str,
        issue_type: IssueType,
        *,
        attempt: int,
    ) -> ToolResult[ExistingLogisticsTicketsPayload]:
        self._authorize(context, order_id)
        arguments = {"order_id": order_id, "issue_type": issue_type.value}
        if fault := self._fault_result(
            context, "get_existing_logistics_tickets", arguments, attempt
        ):
            return ToolResult[ExistingLogisticsTicketsPayload].model_validate(fault.model_dump())
        tickets = self.store.get_active_tickets(order_id, issue_type)
        return ToolResult[ExistingLogisticsTicketsPayload].completed(
            availability=(EvidenceAvailability.PRESENT if tickets else EvidenceAvailability.ABSENT),
            source_type="logistics_tickets",
            source_query_id=_query_id(
                context, "get_existing_logistics_tickets", arguments, attempt
            ),
            observed_at=context.evaluated_at,
            payload=ExistingLogisticsTicketsPayload(
                order_id=order_id,
                issue_type=issue_type,
                active_tickets=tickets,
            ),
            source_record_ids=[ticket.ticket_id for ticket in tickets],
        )


class GovernedToolExecutor:
    """Adapter used by ToolNode; model arguments are treated as untrusted."""

    def __init__(
        self,
        *,
        trusted: TrustedToolContext,
        catalog: SyntheticReadToolCatalog,
        cache: CaseToolCache | None = None,
        budget: ToolBudget | None = None,
    ) -> None:
        if trusted.fixture_version != catalog.store.fixture_version:
            raise ValueError("trusted fixture_version does not match the fixture store")
        self.trusted = trusted
        self.catalog = catalog
        self.cache = cache if cache is not None else CaseToolCache()
        self.budget = budget if budget is not None else ToolBudget()

    async def on_agent_turn(self, _: int) -> None:
        self.budget.record_planning_turn()

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self.execute_result(tool_name, arguments)
        return result.model_dump(mode="json")

    def execute_result(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult[Any]:
        validation_error = self._validate_arguments(tool_name, arguments)
        if validation_error is not None:
            return self._blocked_result(tool_name, arguments, validation_error)

        order_id = str(arguments["order_id"])
        try:
            grant = authorize_order(self.trusted.customer_id, order_id, self.catalog.store)
        except AuthorizationError as exc:
            return self._blocked_result(tool_name, arguments, exc.code)

        normalized = normalize_tool_arguments(arguments)
        source_revision = self.catalog.source_revision(grant.order_id, tool_name)
        cache_key = ToolCacheKey(
            case_id=self.trusted.case_id,
            tool_name=tool_name,
            normalized_args=normalized,
            source_revision=source_revision,
        )
        if cached := self.cache.get(cache_key):
            return cached

        if self.cache.retry_exhausted(cache_key):
            return self._blocked_result(tool_name, arguments, "TOOL_RETRY_EXHAUSTED")

        try:
            self.budget.record_actual_execution()
        except ToolBudgetExceeded as exc:
            return self._blocked_result(tool_name, arguments, exc.code)

        attempt = self.cache.record_actual_attempt(cache_key)
        try:
            result = self._dispatch(tool_name, arguments, attempt)
        except AuthorizationError as exc:
            # The catalog reauthorizes immediately before every actual source read.
            return self._blocked_result(tool_name, arguments, exc.code)

        self.cache.store(cache_key, result)
        return result

    def retry_exhausted_for(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        """Return whether this exact governed query has consumed its one retry.

        The Evidence Gate needs this durable Case-cache fact to distinguish a
        temporary unavailable result from a second unavailable attempt that is
        no longer safe to retry automatically.
        """

        if tool_name not in READ_TOOL_NAMES:
            return False
        order_id = arguments.get("order_id")
        if order_id != self.trusted.authorized_order_id:
            return False
        source_revision = self.catalog.source_revision(
            self.trusted.authorized_order_id,
            tool_name,
        )
        key = ToolCacheKey(
            case_id=self.trusted.case_id,
            tool_name=tool_name,
            normalized_args=normalize_tool_arguments(arguments),
            source_revision=source_revision,
        )
        return self.cache.retry_exhausted(key)

    def _validate_arguments(self, tool_name: str, arguments: dict[str, Any]) -> str | None:
        if tool_name not in READ_TOOL_NAMES:
            return "TOOL_NOT_ALLOWED"
        expected_keys = {"order_id", "issue_type"} if tool_name in _ISSUE_TOOLS else {"order_id"}
        if set(arguments) != expected_keys:
            return "INVALID_TOOL_ARGUMENTS"
        if arguments.get("order_id") != self.trusted.authorized_order_id:
            return "TOOL_SCOPE_MISMATCH"
        required_issue = _ISSUE_RESTRICTED_TOOLS.get(tool_name)
        if required_issue is not None and self.trusted.canonical_issue_type is not required_issue:
            return "TOOL_NOT_RELEVANT_TO_ISSUE"
        if tool_name in _ISSUE_TOOLS:
            if arguments.get("issue_type") != self.trusted.canonical_issue_type.value:
                return "TOOL_SCOPE_MISMATCH"
        return None

    def _dispatch(self, tool_name: str, arguments: dict[str, Any], attempt: int) -> ToolResult[Any]:
        order_id = str(arguments["order_id"])
        method: Callable[..., ToolResult[Any]] = getattr(self.catalog, tool_name)
        if tool_name in _ISSUE_TOOLS:
            return method(
                self.trusted,
                order_id,
                IssueType(str(arguments["issue_type"])),
                attempt=attempt,
            )
        return method(self.trusted, order_id, attempt=attempt)

    def _blocked_result(
        self, tool_name: str, arguments: dict[str, Any], error_code: str
    ) -> ToolResult[Any]:
        safe_name = tool_name if tool_name in READ_TOOL_NAMES else "governed_tool"
        return ToolResult.failed(
            retryable=False,
            source_type=safe_name,
            source_query_id=_query_id(
                self.trusted,
                safe_name,
                {"blocked": True},
                0,
            ),
            observed_at=self.trusted.evaluated_at,
            error_code=error_code,
        )

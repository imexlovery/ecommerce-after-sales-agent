"""Competent deterministic investigation baseline for paired Agent evaluation.

The Workflow changes only how the next read is selected. Authorization, tool
schemas, cache/retry budgets, evidence truth tables, events, and downstream
proposal/executor behavior remain project-owned and shared with the Agent path.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from after_sales_agent.application.investigation import (
    InvestigationOutput,
    TracingToolExecutor,
)
from after_sales_agent.application.pacing import MockDemoPacer
from after_sales_agent.config import Settings
from after_sales_agent.domain.models import TrustedToolContext
from after_sales_agent.domain.state import (
    EvidenceAvailability,
    EvidenceGateDecision,
    ExecutionStatus,
    IssueType,
    OrderStatus,
)
from after_sales_agent.events.models import EventDraft, EventVisibility
from after_sales_agent.events.store import EventStore
from after_sales_agent.fixtures.catalog import FixtureStore
from after_sales_agent.policy.evidence_gate import (
    EvidenceGateResult,
    SignedNotReceivedEvidence,
    StalledTrackingEvidence,
    evaluate_evidence_gate,
)
from after_sales_agent.policy.rag import PolicyRagService
from after_sales_agent.storage.database import SessionFactory
from after_sales_agent.tools.budget import ToolBudget, ToolBudgetExceeded
from after_sales_agent.tools.cache import CaseToolCache
from after_sales_agent.tools.contracts import (
    DeliveryProofPayload,
    ExistingLogisticsTicketsPayload,
    LogisticsTimelinePayload,
    OrderContextPayload,
    PolicySearchPayload,
    ToolResult,
)
from after_sales_agent.tools.service import (
    GovernedToolExecutor,
    SyntheticReadToolCatalog,
)


def _not_queried[PayloadT: BaseModel](
    payload_type: type[PayloadT],
    *,
    tool_name: str,
    trusted: TrustedToolContext,
) -> ToolResult[PayloadT]:
    """Represent a branch-irrelevant observation without pretending it exists."""

    del payload_type
    return ToolResult[PayloadT].failed(
        retryable=True,
        source_type=tool_name,
        source_query_id=f"not-queried:{trusted.case_id}:{tool_name}",
        observed_at=trusted.evaluated_at,
        error_code="NOT_QUERIED_NOT_REQUIRED",
    )


class StrongWorkflowInvestigationService:
    """A strong conditional baseline using exactly the governed read boundary."""

    def __init__(
        self,
        *,
        settings: Settings,
        fixtures: FixtureStore,
        session_factory: SessionFactory,
        events: EventStore,
        policy_rag: PolicyRagService,
        graph_checkpointer: Any | None = None,
        pacer: MockDemoPacer | None = None,
    ) -> None:
        del graph_checkpointer
        self._settings = settings
        self._fixtures = fixtures
        self._session_factory = session_factory
        self._events = events
        self._policy_rag = policy_rag
        self._pacer = pacer or MockDemoPacer(settings)

    async def investigate(
        self,
        *,
        trusted: TrustedToolContext,
        customer_message: str,
        case_planning_turns: int = 0,
        run_planning_turns: int = 0,
        case_read_executions: int = 0,
        tool_cache: CaseToolCache | None = None,
        investigation_pass: int = 0,
        customer_still_reports_missing: bool = True,
        reception_locations_checked: bool = False,
    ) -> InvestigationOutput:
        del customer_message, investigation_pass
        budget = ToolBudget(
            case_planning_turns=case_planning_turns,
            run_planning_turns=run_planning_turns,
            actual_read_tool_executions=case_read_executions,
        )
        governed = GovernedToolExecutor(
            trusted=trusted,
            catalog=SyntheticReadToolCatalog(self._fixtures, self._policy_rag),
            cache=tool_cache,
            budget=budget,
        )
        tracing = TracingToolExecutor(
            executor=governed,
            session_factory=self._session_factory,
            events=self._events,
            trusted=trusted,
            pacer=self._pacer,
            requester_label="Strong Workflow",
        )
        budget_exhausted = False

        async def read(tool_name: str) -> ToolResult[Any]:
            nonlocal budget_exhausted
            arguments: dict[str, Any] = {"order_id": trusted.authorized_order_id}
            if tool_name in {
                "search_after_sales_policy",
                "get_existing_logistics_tickets",
            }:
                arguments["issue_type"] = trusted.canonical_issue_type.value
            try:
                budget.record_planning_turn()
            except ToolBudgetExceeded:
                budget_exhausted = True
                return ToolResult[Any].failed(
                    retryable=False,
                    source_type=tool_name,
                    source_query_id=f"budget-blocked:{trusted.case_id}:{tool_name}",
                    observed_at=trusted.evaluated_at,
                    error_code="WORKFLOW_PLANNING_BUDGET_EXCEEDED",
                )
            await self._events.append(
                EventDraft(
                    conversation_id=trusted.conversation_id,
                    case_id=trusted.case_id,
                    run_id=trusted.run_id,
                    event_type="workflow_step_started",
                    visibility=EventVisibility.DEVELOPER,
                    summary=f"Strong Workflow selected {tool_name}",
                    payload={
                        "tool_name": tool_name,
                        "planning_turn": budget.snapshot.run_planning_turns,
                    },
                )
            )
            result = ToolResult[Any].model_validate(await tracing.execute(tool_name, arguments))
            if result.retryable:
                try:
                    budget.record_planning_turn()
                except ToolBudgetExceeded:
                    budget_exhausted = True
                    return result
                result = ToolResult[Any].model_validate(await tracing.execute(tool_name, arguments))
            return result

        order = ToolResult[OrderContextPayload].model_validate(
            (await read("get_order_context")).model_dump()
        )
        if trusted.canonical_issue_type is IssueType.SIGNED_NOT_RECEIVED:
            gate = await self._run_signed(
                trusted=trusted,
                read=read,
                order=order,
                budget_exhausted=budget_exhausted,
                customer_still_reports_missing=customer_still_reports_missing,
                reception_locations_checked=reception_locations_checked,
            )
        else:
            gate = await self._run_stalled(
                trusted=trusted,
                read=read,
                order=order,
                budget_exhausted=budget_exhausted,
            )

        snapshot = budget.snapshot
        await self._events.append(
            EventDraft(
                conversation_id=trusted.conversation_id,
                case_id=trusted.case_id,
                run_id=trusted.run_id,
                event_type="evidence_gate_evaluated",
                visibility=EventVisibility.BOTH,
                summary=f"Evidence gate: {gate.decision or gate.revised_issue_type}",
                payload={
                    "decision": gate.decision.value if gate.decision else None,
                    "reason_code": gate.reason_code,
                    "revised_issue_type": (
                        gate.revised_issue_type.value if gate.revised_issue_type else None
                    ),
                    "investigation_strategy": "workflow",
                },
                evidence_refs=[ref.model_dump(mode="json") for ref in tracing.evidence_refs],
            )
        )
        await self._pacer.pause("evidence_gate_evaluated")
        return InvestigationOutput(
            gate_result=gate,
            evidence_refs=tuple(tracing.evidence_refs),
            tool_results=tracing.results,
            planning_turns=snapshot.run_planning_turns,
            case_planning_turns=snapshot.case_planning_turns,
            run_read_tool_executions=(snapshot.actual_read_tool_executions - case_read_executions),
            actual_read_tool_executions=snapshot.actual_read_tool_executions,
            budget_exhausted=budget_exhausted,
            strategy="workflow",
        )

    async def _run_signed(
        self,
        *,
        trusted: TrustedToolContext,
        read: Any,
        order: ToolResult[OrderContextPayload],
        budget_exhausted: bool,
        customer_still_reports_missing: bool,
        reception_locations_checked: bool,
    ) -> EvidenceGateResult:
        missing_timeline = _not_queried(
            LogisticsTimelinePayload,
            tool_name="get_logistics_timeline",
            trusted=trusted,
        )
        missing_proof = _not_queried(
            DeliveryProofPayload,
            tool_name="get_delivery_proof",
            trusted=trusted,
        )
        missing_policy = _not_queried(
            PolicySearchPayload,
            tool_name="search_after_sales_policy",
            trusted=trusted,
        )
        missing_tickets = _not_queried(
            ExistingLogisticsTicketsPayload,
            tool_name="get_existing_logistics_tickets",
            trusted=trusted,
        )

        def evaluate(
            *,
            timeline: ToolResult[LogisticsTimelinePayload] = missing_timeline,
            proof: ToolResult[DeliveryProofPayload] = missing_proof,
            policy: ToolResult[PolicySearchPayload] = missing_policy,
            tickets: ToolResult[ExistingLogisticsTicketsPayload] = missing_tickets,
        ) -> EvidenceGateResult:
            return evaluate_evidence_gate(
                IssueType.SIGNED_NOT_RECEIVED,
                SignedNotReceivedEvidence(
                    order_context=order,
                    timeline=timeline,
                    delivery_proof=proof,
                    policy=policy,
                    existing_tickets=tickets,
                    budget_exceeded=budget_exhausted,
                    critical_retry_exhausted=self._retry_exhausted(
                        [order, timeline, proof, policy, tickets]
                    ),
                    customer_still_reports_missing=customer_still_reports_missing,
                    reception_locations_checked=reception_locations_checked,
                ),
            )

        if (
            order.execution_status is ExecutionStatus.SUCCESS
            and order.payload is not None
            and order.payload.order_status is not OrderStatus.DELIVERED
        ):
            return evaluate()
        tickets = ToolResult[ExistingLogisticsTicketsPayload].model_validate(
            (await read("get_existing_logistics_tickets")).model_dump()
        )
        if tickets.payload and tickets.payload.active_tickets:
            return evaluate(tickets=tickets)
        timeline = ToolResult[LogisticsTimelinePayload].model_validate(
            (await read("get_logistics_timeline")).model_dump()
        )
        policy = ToolResult[PolicySearchPayload].model_validate(
            (await read("search_after_sales_policy")).model_dump()
        )
        proof = ToolResult[DeliveryProofPayload].model_validate(
            (await read("get_delivery_proof")).model_dump()
        )
        return evaluate(timeline=timeline, proof=proof, policy=policy, tickets=tickets)

    async def _run_stalled(
        self,
        *,
        trusted: TrustedToolContext,
        read: Any,
        order: ToolResult[OrderContextPayload],
        budget_exhausted: bool,
    ) -> EvidenceGateResult:
        timeline = ToolResult[LogisticsTimelinePayload].model_validate(
            (await read("get_logistics_timeline")).model_dump()
        )
        policy = ToolResult[PolicySearchPayload].model_validate(
            (await read("search_after_sales_policy")).model_dump()
        )
        missing_tickets = _not_queried(
            ExistingLogisticsTicketsPayload,
            tool_name="get_existing_logistics_tickets",
            trusted=trusted,
        )

        def evaluate(
            tickets: ToolResult[ExistingLogisticsTicketsPayload] = missing_tickets,
        ) -> EvidenceGateResult:
            return evaluate_evidence_gate(
                IssueType.STALLED_TRACKING,
                StalledTrackingEvidence(
                    order_context=order,
                    timeline=timeline,
                    policy=policy,
                    existing_tickets=tickets,
                    budget_exceeded=budget_exhausted,
                    critical_retry_exhausted=self._retry_exhausted(
                        [order, timeline, policy, tickets]
                    ),
                ),
            )

        preliminary = evaluate()
        if preliminary.decision in {
            EvidenceGateDecision.COMPLETE_NO_ACTION,
            EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT,
        }:
            return preliminary
        if preliminary.reason_code not in {
            "CRITICAL_EVIDENCE_UNAVAILABLE",
            "CRITICAL_EVIDENCE_UNAVAILABLE_FINAL",
        }:
            return preliminary

        # Carrier alerts are explanatory only and are queried only once the
        # critical SLA facts establish a genuine overdue path.
        await read("get_carrier_service_alerts")
        tickets = ToolResult[ExistingLogisticsTicketsPayload].model_validate(
            (await read("get_existing_logistics_tickets")).model_dump()
        )
        return evaluate(tickets)

    @staticmethod
    def _retry_exhausted(results: list[ToolResult[Any]]) -> bool:
        return any(
            result.execution_status is not ExecutionStatus.SUCCESS
            and result.evidence_availability is EvidenceAvailability.UNAVAILABLE
            and result.retryable
            and result.error_code not in {None, "NOT_QUERIED_NOT_REQUIRED"}
            for result in results
        )

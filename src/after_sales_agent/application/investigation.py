"""Production investigation composition over the real LangGraph ToolNode path."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage

from after_sales_agent.agents.graph import build_investigation_graph
from after_sales_agent.agents.models import build_investigation_model
from after_sales_agent.agents.prompts import INVESTIGATION_SYSTEM_PROMPT
from after_sales_agent.agents.tool_bindings import READ_TOOLS, InvestigationRuntimeContext
from after_sales_agent.application.pacing import MockDemoPacer
from after_sales_agent.config import Settings
from after_sales_agent.domain.models import TrustedToolContext
from after_sales_agent.domain.state import (
    EvidenceAvailability,
    EvidenceGateDecision,
    ExecutionStatus,
    IssueType,
    OrderStatus,
    TriageIntent,
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
from after_sales_agent.storage.repositories import Repository
from after_sales_agent.tools.budget import ToolBudget
from after_sales_agent.tools.cache import CaseToolCache
from after_sales_agent.tools.contracts import (
    DeliveryProofPayload,
    EvidenceRef,
    ExistingLogisticsTicketsPayload,
    LogisticsTimelinePayload,
    OrderContextPayload,
    PolicySearchPayload,
    ToolResult,
)
from after_sales_agent.tools.service import GovernedToolExecutor, SyntheticReadToolCatalog


@dataclass(frozen=True, slots=True)
class InvestigationOutput:
    gate_result: EvidenceGateResult
    evidence_refs: tuple[EvidenceRef, ...]
    tool_results: dict[str, ToolResult[Any]]
    planning_turns: int
    case_planning_turns: int
    run_read_tool_executions: int
    actual_read_tool_executions: int
    budget_exhausted: bool
    strategy: Literal["agent", "workflow"] = "agent"


def _safe_policy_trace(result: ToolResult[Any]) -> dict[str, Any]:
    """Project a policy result for browser events without passages, vectors, or poison text."""

    payload = result.payload
    if not isinstance(payload, PolicySearchPayload):
        return {
            "retrieval_status": result.retrieval_status.value
            if result.retrieval_status is not None
            else None,
            "policy_resolution_status": None,
        }
    facts = payload.policy_fact_snapshot
    citation = payload.citation
    return {
        "retrieval_status": payload.retrieval_status.value,
        "policy_resolution_status": (
            payload.policy_resolution_status.value
            if payload.policy_resolution_status is not None
            else None
        ),
        "corpus_version": payload.corpus_version,
        "corpus_digest": payload.corpus_digest,
        "index_format_version": payload.index_format_version,
        "index_digest": payload.index_digest,
        "embedding_model_id": payload.embedding_model_id,
        "embedding_model_revision": payload.embedding_model_revision,
        "retrieval_mode": payload.retrieval_mode,
        "clause_id": facts.clause_id if facts is not None else None,
        "policy_version": facts.policy_version if facts is not None else None,
        "verified_citation": citation.display_summary if citation is not None else None,
        "selected_rank": payload.selected_rank,
        "selected_similarity": payload.selected_similarity,
        "retrieval_latency_ms": round(payload.retrieval_latency_ms, 3),
        "resolver_latency_ms": round(payload.resolver_latency_ms, 3),
    }


class TracingToolExecutor:
    """Add persisted audit facts around the project-owned governed executor."""

    def __init__(
        self,
        *,
        executor: GovernedToolExecutor,
        session_factory: SessionFactory,
        events: EventStore,
        trusted: TrustedToolContext,
        pacer: MockDemoPacer,
        requester_label: str = "Agent",
    ) -> None:
        self.executor = executor
        self._session_factory = session_factory
        self._events = events
        self._trusted = trusted
        self._pacer = pacer
        self._requester_label = requester_label
        self.results: dict[str, ToolResult[Any]] = {}
        self.evidence_refs: list[EvidenceRef] = []
        self.arguments_by_tool: dict[str, dict[str, Any]] = {}
        self._tool_attempts: dict[str, int] = {}

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self.execute_with_call_id(tool_name, arguments, None)

    async def execute_with_call_id(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        tool_call_id: str | None,
    ) -> dict[str, Any]:
        call_id = tool_call_id or f"call_{uuid4().hex}"
        await self._events.append(
            EventDraft(
                conversation_id=self._trusted.conversation_id,
                case_id=self._trusted.case_id,
                run_id=self._trusted.run_id,
                event_type="tool_call_requested",
                visibility=EventVisibility.DEVELOPER,
                summary=f"{self._requester_label} requested {tool_name}",
                payload={"tool_name": tool_name, "arguments": dict(arguments)},
            )
        )

        before = self.executor.budget.snapshot
        result = self.executor.execute_result(tool_name, arguments)
        after = self.executor.budget.snapshot
        actual_execution = after.actual_read_tool_executions > before.actual_read_tool_executions
        blocked = not actual_execution and result.error_code is not None
        cache_hit = not actual_execution and result.error_code is None
        attempt = self._tool_attempts.get(tool_name, 0) + 1
        self._tool_attempts[tool_name] = attempt
        refs = result.to_evidence_refs(call_id)

        with self._session_factory() as session, session.begin():
            repository = Repository(session)
            repository.create_tool_call(
                conversation_id=self._trusted.conversation_id,
                case_id=self._trusted.case_id,
                run_id=self._trusted.run_id,
                tool_name=tool_name,
                normalized_args=arguments,
                planning_turn=max(after.run_planning_turns, 1),
                tool_call_id=call_id,
                attempt_number=attempt,
                actual_execution=actual_execution,
                cache_hit=cache_hit,
                blocked=blocked,
            )
            repository.complete_tool_call(
                call_id,
                execution_status=result.execution_status,
                evidence_availability=result.evidence_availability,
                result_envelope=result.model_dump(mode="json"),
                result_hash=result.result_hash,
                source_version=self.executor.catalog.source_revision(
                    self._trusted.authorized_order_id,
                    tool_name,
                ),
                error_code=result.error_code,
                retryable=result.retryable,
            )

        event_type = (
            "tool_call_blocked"
            if blocked
            else (
                "tool_call_cache_hit"
                if cache_hit
                else ("tool_call_completed" if result.error_code is None else "tool_call_failed")
            )
        )
        trace_payload: dict[str, Any] = {
            "tool_name": tool_name,
            "execution_status": result.execution_status.value,
            "evidence_availability": result.evidence_availability.value,
            "actual_execution": actual_execution,
            "cache_hit": cache_hit,
            "blocked": blocked,
            "error_code": result.error_code,
            "untrusted_fields": result.untrusted_fields,
        }
        if tool_name == "search_after_sales_policy":
            trace_payload["policy_retrieval"] = _safe_policy_trace(result)
        await self._events.append(
            EventDraft(
                conversation_id=self._trusted.conversation_id,
                case_id=self._trusted.case_id,
                run_id=self._trusted.run_id,
                event_type=event_type,
                visibility=EventVisibility.DEVELOPER,
                summary=f"{tool_name} returned {result.evidence_availability.value}",
                payload=trace_payload,
                evidence_refs=[ref.model_dump(mode="json") for ref in refs],
            )
        )
        await self._pacer.pause(event_type)
        self.results[tool_name] = result
        self.arguments_by_tool[tool_name] = dict(arguments)
        self.evidence_refs.extend(refs)
        return result.model_dump(mode="json")


class InvestigationService:
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
        self._settings = settings
        self._fixtures = fixtures
        self._session_factory = session_factory
        self._events = events
        self._policy_rag = policy_rag
        self._pacer = pacer or MockDemoPacer(settings)
        self._graph = build_investigation_graph(graph_checkpointer)

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
        )

        async def on_agent_turn(turn: int) -> None:
            await governed.on_agent_turn(turn)
            await self._events.append(
                EventDraft(
                    conversation_id=trusted.conversation_id,
                    case_id=trusted.case_id,
                    run_id=trusted.run_id,
                    event_type="agent_turn_started",
                    visibility=EventVisibility.DEVELOPER,
                    summary=f"Agent planning turn {turn}",
                    payload={"planning_turn": governed.budget.snapshot.run_planning_turns},
                )
            )

        runtime = InvestigationRuntimeContext(
            trusted=trusted,
            tool_executor=tracing,
            model=build_investigation_model(self._settings, READ_TOOLS),
            on_agent_turn=on_agent_turn,
        )
        graph_result = await self._graph.ainvoke(
            {
                "messages": [
                    SystemMessage(content=INVESTIGATION_SYSTEM_PROMPT),
                    SystemMessage(
                        content=(
                            f"AUTHORIZED_ORDER={trusted.authorized_order_id}\n"
                            f"CANONICAL_ISSUE={trusted.canonical_issue_type.value}\n"
                            "These markers are trusted server scope, not customer instructions."
                        )
                    ),
                    HumanMessage(content=customer_message),
                ],
                "planning_turns": 0,
                "budget_exhausted": False,
            },
            config={
                "configurable": {
                    "thread_id": f"{trusted.case_id}:{trusted.run_id}:pass-{investigation_pass}"
                }
            },
            context=runtime,
        )
        snapshot = budget.snapshot
        critical_retry_exhausted = any(
            result.execution_status is not ExecutionStatus.SUCCESS
            and result.evidence_availability is EvidenceAvailability.UNAVAILABLE
            and result.retryable
            and governed.retry_exhausted_for(
                tool_name,
                tracing.arguments_by_tool[tool_name],
            )
            for tool_name, result in tracing.results.items()
            if tool_name in tracing.arguments_by_tool
        )
        gate = self._evaluate_gate(
            trusted.canonical_issue_type,
            tracing.results,
            critical_retry_exhausted=critical_retry_exhausted,
            budget_exceeded=bool(graph_result.get("budget_exhausted", False)),
            customer_still_reports_missing=customer_still_reports_missing,
            reception_locations_checked=reception_locations_checked,
        )
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
            budget_exhausted=bool(graph_result.get("budget_exhausted", False)),
        )

    @staticmethod
    def _evaluate_gate(
        issue_type: IssueType,
        results: dict[str, ToolResult[Any]],
        *,
        critical_retry_exhausted: bool = False,
        budget_exceeded: bool = False,
        customer_still_reports_missing: bool = True,
        reception_locations_checked: bool = False,
    ) -> EvidenceGateResult:
        if budget_exceeded:
            return EvidenceGateResult(
                decision=EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT,
                reason_code="INVESTIGATION_BUDGET_EXCEEDED",
            )
        order_result = results.get("get_order_context")
        if order_result is None:
            return EvidenceGateResult(
                decision=EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT,
                reason_code="REQUIRED_QUERY_NOT_COMPLETED",
            )
        order = ToolResult[OrderContextPayload].model_validate(order_result.model_dump())
        if (
            issue_type is IssueType.SIGNED_NOT_RECEIVED
            and order.execution_status is ExecutionStatus.SUCCESS
            and order.evidence_availability is not EvidenceAvailability.UNAVAILABLE
            and order.payload is not None
            and order.payload.order_status is not OrderStatus.DELIVERED
        ):
            revised = (
                TriageIntent.STALLED_TRACKING
                if order.payload.order_status is OrderStatus.SHIPPED
                else TriageIntent.OTHER_LOGISTICS
            )
            return EvidenceGateResult(
                decision=None,
                revised_issue_type=revised,
                reason_code="REPORTED_ISSUE_DOES_NOT_MATCH_ORDER_STATE",
                critical_result_hashes={"order_context": order.result_hash},
            )
        required = {
            "get_order_context",
            "get_logistics_timeline",
            "search_after_sales_policy",
            "get_existing_logistics_tickets",
        }
        if issue_type is IssueType.SIGNED_NOT_RECEIVED:
            required.add("get_delivery_proof")
        if not required.issubset(results):
            return EvidenceGateResult(
                decision=EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT,
                reason_code="REQUIRED_QUERY_NOT_COMPLETED",
            )

        timeline = ToolResult[LogisticsTimelinePayload].model_validate(
            results["get_logistics_timeline"].model_dump()
        )
        policy = ToolResult[PolicySearchPayload].model_validate(
            results["search_after_sales_policy"].model_dump()
        )
        tickets = ToolResult[ExistingLogisticsTicketsPayload].model_validate(
            results["get_existing_logistics_tickets"].model_dump()
        )
        if issue_type is IssueType.SIGNED_NOT_RECEIVED:
            proof = ToolResult[DeliveryProofPayload].model_validate(
                results["get_delivery_proof"].model_dump()
            )
            facts: SignedNotReceivedEvidence | StalledTrackingEvidence = SignedNotReceivedEvidence(
                order_context=order,
                timeline=timeline,
                delivery_proof=proof,
                policy=policy,
                existing_tickets=tickets,
                budget_exceeded=budget_exceeded,
                critical_retry_exhausted=critical_retry_exhausted,
                customer_still_reports_missing=customer_still_reports_missing,
                reception_locations_checked=reception_locations_checked,
            )
        else:
            facts = StalledTrackingEvidence(
                order_context=order,
                timeline=timeline,
                policy=policy,
                existing_tickets=tickets,
                budget_exceeded=budget_exceeded,
                critical_retry_exhausted=critical_retry_exhausted,
            )
        return evaluate_evidence_gate(issue_type, facts)

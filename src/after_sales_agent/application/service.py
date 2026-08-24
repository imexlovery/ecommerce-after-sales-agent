"""Canonical application service used by every supported API route."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from after_sales_agent.actions.service import (
    build_proposal,
    build_ready_action,
    evidence_snapshot_hash,
    stable_ticket_id,
)
from after_sales_agent.agents.triage import (
    MessageValidationError,
    TriageService,
    validate_customer_message,
)
from after_sales_agent.application.investigation import InvestigationOutput, InvestigationService
from after_sales_agent.application.pacing import MockDemoPacer
from after_sales_agent.application.policy_router import (
    PolicyDecision,
    PolicyRoute,
    route_triage,
)
from after_sales_agent.application.responses import (
    render_gate_reply,
    render_investigation_ack,
    render_policy_reply,
)
from after_sales_agent.application.strong_workflow import StrongWorkflowInvestigationService
from after_sales_agent.config import Settings
from after_sales_agent.domain.models import InvestigationCase, TrustedToolContext
from after_sales_agent.domain.state import (
    ActionState,
    CaseOutcome,
    CaseState,
    EvidenceGateDecision,
    IssueType,
    ProposalState,
    TriageIntent,
)
from after_sales_agent.events.models import EventDraft, EventVisibility
from after_sales_agent.events.store import EventStore
from after_sales_agent.fixtures.catalog import FixtureStore
from after_sales_agent.policy.authorization import AuthorizationError, authorize_order
from after_sales_agent.policy.evidence_gate import (
    EvidenceGateResult,
    SignedNotReceivedEvidence,
    StalledTrackingEvidence,
    evaluate_evidence_gate,
)
from after_sales_agent.storage.database import SessionFactory
from after_sales_agent.storage.locks import CaseMutationCoordinator
from after_sales_agent.storage.models import ActionProposalRow, InvestigationCaseRow, utc_now
from after_sales_agent.storage.repositories import (
    Repository,
    case_to_domain,
    proposal_to_domain,
)
from after_sales_agent.tools.cache import (
    CaseToolCache,
    ToolCacheKey,
    normalize_tool_arguments,
)
from after_sales_agent.tools.contracts import (
    AfterSalesPolicyPayload,
    DeliveryProofPayload,
    ExistingLogisticsTicketsPayload,
    LogisticsTicket,
    LogisticsTimelinePayload,
    OrderContextPayload,
    ToolResult,
)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _iso_or_none(value: datetime | None) -> str | None:
    return _iso(value) if value is not None else None


@dataclass(slots=True)
class ApplicationError(RuntimeError):
    code: str
    message: str
    status_code: int
    retryable: bool = False
    trace_id: str = "unavailable"

    def __str__(self) -> str:
        return self.message


class AfterSalesApplication:
    """One production composition for API, Agent, tools, events, and writes."""

    _fixture_customers = frozenset({"customer_a", "customer_b"})

    def __init__(
        self,
        *,
        settings: Settings,
        fixtures: FixtureStore,
        session_factory: SessionFactory,
        events: EventStore,
        graph_checkpointer: Any | None = None,
        investigation_strategy: Literal["agent", "workflow"] = "agent",
    ) -> None:
        self.settings = settings
        self.fixtures = fixtures
        self.session_factory = session_factory
        self.events = events
        self.locks = CaseMutationCoordinator()
        self.pacer = MockDemoPacer(settings)
        self.triage = TriageService(settings)
        investigation_type = (
            InvestigationService
            if investigation_strategy == "agent"
            else StrongWorkflowInvestigationService
        )
        self.investigation = investigation_type(
            settings=settings,
            fixtures=fixtures,
            session_factory=session_factory,
            events=events,
            graph_checkpointer=graph_checkpointer,
            pacer=self.pacer,
        )
        self.investigation_strategy = investigation_strategy
        self._case_caches: dict[str, CaseToolCache] = {}

    def create_conversation(self, fixture_customer_key: str) -> dict[str, Any]:
        if fixture_customer_key not in self._fixture_customers:
            raise ApplicationError(
                code="UNKNOWN_FIXTURE_CUSTOMER",
                message="请选择可用的虚拟客户。",
                status_code=422,
            )
        with self.session_factory() as session, session.begin():
            row = Repository(session).create_conversation(
                customer_id=fixture_customer_key,
                fixture_key=fixture_customer_key,
                llm_mode=self.settings.llm_mode.value,
                fixture_version=self.fixtures.fixture_version,
            )
        return {
            "conversation_id": row.conversation_id,
            "fixture_customer_key": row.fixture_customer_key,
            "llm_mode": row.llm_mode,
            "created_at": _iso(row.created_at),
            "events_url": f"/v1/conversations/{row.conversation_id}/events",
        }

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        """Return the current business read model, never reconstructed from SSE."""

        with self.session_factory() as session:
            repository = Repository(session)
            conversation = repository.get_conversation(conversation_id)
            if conversation is None:
                raise ApplicationError(
                    code="CONVERSATION_NOT_FOUND",
                    message="找不到这段虚拟会话。",
                    status_code=404,
                )
            messages = repository.list_messages(conversation_id)
            cases = repository.list_cases(conversation_id)
            return {
                "conversation_id": conversation.conversation_id,
                "fixture_customer_key": conversation.fixture_customer_key,
                "llm_mode": conversation.llm_mode,
                "messages": [
                    {
                        "message_id": message.message_id,
                        "role": message.role,
                        "content": message.content,
                        "case_id": message.case_id,
                        "run_id": message.run_id,
                        "created_at": _iso(message.created_at),
                    }
                    for message in messages
                ],
                "cases": [
                    {
                        "case_id": case.case_id,
                        "case_state": case.case_state,
                        "case_outcome": case.case_outcome,
                        "authorized_order_id": case.authorized_order_id,
                        "canonical_issue_type": case.canonical_issue_type,
                    }
                    for case in cases
                ],
                "active_case_id": conversation.active_case_id,
                "updated_at": _iso(conversation.updated_at),
            }

    def get_case(self, case_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            case = Repository(session).get_case(case_id)
            if case is None:
                raise ApplicationError(
                    code="INVESTIGATION_CASE_NOT_FOUND",
                    message="找不到这次物流调查。",
                    status_code=404,
                )
            return {
                "case_id": case.case_id,
                "conversation_id": case.conversation_id,
                "related_case_id": case.related_case_id,
                "authorized_order_id": case.authorized_order_id,
                "reported_issue_type": case.reported_issue_type,
                "canonical_issue_type": case.canonical_issue_type,
                "issue_type_revision_history": list(case.issue_type_revision_history),
                "case_state": case.case_state,
                "case_outcome": case.case_outcome,
                "reason_code": case.reason_code,
                "business_clarification_count": case.business_clarification_count,
                "actual_read_tool_execution_count": (case.actual_read_tool_execution_count),
                "agent_planning_turn_count": case.agent_planning_turn_count,
                "active_proposal_id": case.active_proposal_id,
                "revision": case.revision,
                "created_at": _iso(case.created_at),
                "updated_at": _iso(case.updated_at),
            }

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            run = Repository(session).get_run(run_id)
            if run is None:
                raise ApplicationError(
                    code="RUN_NOT_FOUND",
                    message="找不到这次执行记录。",
                    status_code=404,
                )
            return {
                "run_id": run.run_id,
                "conversation_id": run.conversation_id,
                "case_id": run.case_id,
                "run_kind": run.run_kind,
                "run_state": run.run_state,
                "planning_turn_count": run.planning_turn_count,
                "actual_read_tool_execution_count": (run.actual_read_tool_execution_count),
                "failure_code": run.failure_code,
                "created_at": _iso(run.created_at),
                "started_at": _iso_or_none(run.started_at),
                "completed_at": _iso_or_none(run.completed_at),
            }

    def reset_demo(self) -> dict[str, int]:
        """Reset only synthetic runtime data, never configuration or Eval artifacts."""

        with self.session_factory() as session, session.begin():
            counts = Repository(session).reset_demo_data()
        self.fixtures.reset_dynamic_tickets()
        self._case_caches.clear()
        return counts

    def load_persisted_tickets(self) -> None:
        """Restore simulated source-side ticket visibility after a local restart."""

        with self.session_factory() as session:
            tickets = Repository(session).list_tickets()
        for ticket in tickets:
            if ticket.ticket_state != "active":
                continue
            self.fixtures.add_ticket(
                LogisticsTicket(
                    ticket_id=ticket.ticket_id,
                    order_id=ticket.authorized_order_id,
                    issue_type=IssueType(ticket.issue_type),
                    ticket_status="open",
                    created_at=ticket.created_at,
                )
            )

    async def submit_message(self, conversation_id: str, content: str) -> dict[str, Any]:
        trace_id = _new_id("trc")
        try:
            validated = validate_customer_message(
                content,
                max_chars=self.settings.max_message_chars,
            )
        except MessageValidationError as exc:
            if self._conversation_exists(conversation_id):
                await self.events.append(
                    EventDraft(
                        conversation_id=conversation_id,
                        event_type="message_rejected",
                        visibility=EventVisibility.BOTH,
                        summary=str(exc),
                        payload={"reason_code": "INVALID_CUSTOMER_MESSAGE"},
                    )
                )
            raise ApplicationError(
                code="INVALID_CUSTOMER_MESSAGE",
                message=str(exc),
                status_code=400,
                trace_id=trace_id,
            ) from exc

        async with self.locks.serialize(f"conversation:{conversation_id}"):
            with self.session_factory() as session, session.begin():
                repository = Repository(session)
                conversation = repository.get_conversation(conversation_id)
                if conversation is None:
                    raise ApplicationError(
                        code="CONVERSATION_NOT_FOUND",
                        message="找不到这段虚拟会话。",
                        status_code=404,
                        trace_id=trace_id,
                    )
                active_case = repository.get_active_case(conversation_id)
                if (
                    active_case is not None
                    and active_case.case_state != CaseState.AWAITING_CUSTOMER_INPUT.value
                ):
                    raise ApplicationError(
                        code="ACTIVE_CASE_REQUIRES_RESOLUTION",
                        message="请先完成当前物流核查，再发起新的问题。",
                        status_code=409,
                        trace_id=trace_id,
                    )
                run_id = _new_id("run")
                message = repository.add_message(
                    conversation_id,
                    "customer",
                    validated.trace_content,
                    case_id=active_case.case_id if active_case is not None else None,
                    run_id=run_id,
                    trace_id=trace_id,
                )
                repository.create_run(
                    None,
                    conversation_id=conversation_id,
                    run_kind="message",
                    run_id=run_id,
                    case_id=active_case.case_id if active_case is not None else None,
                    trace_id=trace_id,
                )
                customer_id = conversation.customer_id

            await self.events.append(
                EventDraft(
                    conversation_id=conversation_id,
                    case_id=active_case.case_id if active_case is not None else None,
                    run_id=run_id,
                    event_type="message_received",
                    visibility=EventVisibility.BOTH,
                    summary="Customer message accepted",
                    payload={
                        "message_id": message.message_id,
                        # This is the validated, PII-redacted customer projection.
                        # Keeping it on the canonical event lets a restored browser
                        # rebuild one interleaved Conversation timeline from event
                        # sequence rather than grouping persisted messages by role.
                        "customer_text": validated.trace_content,
                        "personal_data_redacted": validated.had_unnecessary_personal_data,
                    },
                )
            )
            await self._start_run(
                conversation_id,
                run_id,
                case_id=active_case.case_id if active_case is not None else None,
            )
            if active_case is not None:
                return await self._continue_business_clarification(
                    case=case_to_domain(active_case),
                    run_id=run_id,
                    customer_message=validated.content,
                    trace_id=trace_id,
                )
            await self.events.append(
                EventDraft(
                    conversation_id=conversation_id,
                    run_id=run_id,
                    event_type="triage_started",
                    visibility=EventVisibility.DEVELOPER,
                    summary="Lightweight triage started",
                )
            )
            await self.pacer.pause("triage_started")

            try:
                triage = await self.triage.classify(validated.content)
            except Exception as exc:
                await self._fail_run(
                    conversation_id,
                    run_id,
                    failure_code="TRIAGE_PROVIDER_FAILURE",
                    event_type="triage_failed",
                    summary="Triage failed; no tool was called",
                )
                raise ApplicationError(
                    code="TRIAGE_PROVIDER_FAILURE",
                    message="入口识别暂时不可用，请重试。",
                    status_code=503,
                    retryable=True,
                    trace_id=trace_id,
                ) from exc

            decision = route_triage(
                customer_id=customer_id,
                triage=triage,
                fixtures=self.fixtures,
            )
            with self.session_factory() as session, session.begin():
                repository = Repository(session)
                decision = self._enforce_entry_clarification_limit(
                    repository,
                    conversation_id=conversation_id,
                    decision=decision,
                )
                triage_row = repository.create_triage_record(
                    triage,
                    conversation_id=conversation_id,
                    message_id=message.message_id,
                    run_id=run_id,
                )
                repository.create_policy_decision(
                    conversation_id=conversation_id,
                    message_id=message.message_id,
                    triage_id=triage_row.triage_id,
                    run_id=run_id,
                    route=decision.route.value,
                    supported=decision.supported,
                    canonical_issue_type=(
                        decision.canonical_issue_type.value
                        if decision.canonical_issue_type
                        else None
                    ),
                    authorized_order_id=decision.authorized_order_id,
                    blocked_fragments=[item.as_dict() for item in decision.blocked_fragments],
                    risk_flags=decision.risk_flags,
                    reason_code=decision.reason_code,
                )
            await self._emit_triage_and_policy(conversation_id, run_id, triage, decision)
            await self.pacer.pause("policy_decided")

            if not decision.supported:
                await self._complete_without_case(
                    conversation_id=conversation_id,
                    run_id=run_id,
                    reply=render_policy_reply(decision),
                )
                return {
                    "run_id": run_id,
                    "case_id": None,
                    "events_url": f"/v1/conversations/{conversation_id}/events",
                }

            return await self._create_and_investigate_case(
                conversation_id=conversation_id,
                customer_id=customer_id,
                run_id=run_id,
                message_id=message.message_id,
                customer_message=validated.content,
                decision=decision,
                trace_id=trace_id,
            )

    @staticmethod
    def _enforce_entry_clarification_limit(
        repository: Repository,
        *,
        conversation_id: str,
        decision: PolicyDecision,
    ) -> PolicyDecision:
        """Allow one unresolved entry clarification before a safe handoff.

        Entry clarification happens before an InvestigationCase exists.  A valid
        supported route resets the sequence because it creates a bounded Case;
        consecutive ambiguous requests must not become an unbounded pseudo-case.
        """

        if decision.route is not PolicyRoute.AMBIGUOUS:
            return decision
        previous = repository.list_policy_decisions(conversation_id)
        if not previous or previous[-1].route != PolicyRoute.AMBIGUOUS.value:
            return decision
        return replace(decision, reason_code="ENTRY_CLARIFICATION_EXHAUSTED")

    async def _continue_business_clarification(
        self,
        *,
        case: InvestigationCase,
        run_id: str,
        customer_message: str,
        trace_id: str,
    ) -> dict[str, Any]:
        """Resume only the Case that explicitly requested business input.

        A clarification reply is not another entry request: its order scope and
        issue type remain server-owned by the open Case.  This keeps an injected
        order ID or a new free-text route from bypassing the one-active-Case
        boundary while still letting the customer answer the concrete question.
        """

        with self.session_factory() as session:
            current = Repository(session).require_case(case.case_id)
        if current.business_clarification_count >= 2:
            reply = (
                "这次物流核查已经达到可追问次数上限，"
                "为避免基于不完整信息继续自动判断，请联系人工支持。"
            )
            with self.session_factory() as session, session.begin():
                repository = Repository(session)
                current = repository.require_case(case.case_id)
                repository.update_case(
                    case.case_id,
                    expected_revision=current.revision,
                    case_state=CaseState.CLOSED,
                    case_outcome=CaseOutcome.HUMAN_SUPPORT_REQUIRED,
                    reason_code="BUSINESS_CLARIFICATION_LIMIT_REACHED",
                )
                repository.add_message(
                    case.conversation_id,
                    "assistant",
                    reply,
                    case_id=case.case_id,
                    run_id=run_id,
                )
                repository.update_run(run_id, run_state="succeeded")
            await self._emit_customer_reply_and_run_success(
                case.conversation_id,
                run_id,
                reply,
                case_id=case.case_id,
                reply_kind="business_clarification_limit",
            )
            await self.events.append(
                EventDraft(
                    conversation_id=case.conversation_id,
                    case_id=case.case_id,
                    run_id=run_id,
                    event_type="case_closed",
                    visibility=EventVisibility.BOTH,
                    summary="Investigation closed after business clarification limit",
                    payload={
                        "case_state": CaseState.CLOSED.value,
                        "case_outcome": CaseOutcome.HUMAN_SUPPORT_REQUIRED.value,
                        "reason_code": "BUSINESS_CLARIFICATION_LIMIT_REACHED",
                    },
                )
            )
            return {
                "run_id": run_id,
                "case_id": case.case_id,
                "events_url": f"/v1/conversations/{case.conversation_id}/events",
            }

        customer_still_reports_missing, reception_locations_checked = (
            self._business_clarification_facts(customer_message)
        )
        refreshed_case = case_to_domain(current)
        trusted = TrustedToolContext(
            customer_id=refreshed_case.customer_id,
            conversation_id=refreshed_case.conversation_id,
            case_id=refreshed_case.case_id,
            run_id=run_id,
            authorized_order_id=refreshed_case.authorized_order_id,
            canonical_issue_type=refreshed_case.canonical_issue_type,
            fixture_version=self.fixtures.fixture_version,
            fault_seed=self.settings.scenario_fault_seed,
            evaluated_at=self.settings.scenario_evaluated_at,
            trace_id=trace_id,
        )
        cache = self._case_caches.get(case.case_id)
        if cache is None:
            cache = self._load_case_cache(case.case_id)
            self._case_caches[case.case_id] = cache
        try:
            output = await self.investigation.investigate(
                trusted=trusted,
                customer_message=customer_message,
                case_planning_turns=refreshed_case.planning_turns,
                case_read_executions=refreshed_case.read_tool_executions,
                tool_cache=cache,
                investigation_pass=refreshed_case.business_clarifications,
                customer_still_reports_missing=customer_still_reports_missing,
                reception_locations_checked=reception_locations_checked,
            )
        except Exception as exc:
            run_turns, run_reads = self._observed_run_usage(
                refreshed_case.conversation_id,
                run_id,
            )
            with self.session_factory() as session, session.begin():
                repository = Repository(session)
                latest = repository.require_case(case.case_id)
                repository.update_case(
                    case.case_id,
                    expected_revision=latest.revision,
                    case_state=CaseState.AWAITING_RETRY,
                    actual_read_tool_execution_count=min(
                        self.settings.max_case_read_executions,
                        latest.actual_read_tool_execution_count + run_reads,
                    ),
                    agent_planning_turn_count=min(
                        self.settings.max_case_planning_turns,
                        latest.agent_planning_turn_count + run_turns,
                    ),
                )
            await self._fail_run(
                refreshed_case.conversation_id,
                run_id,
                case_id=case.case_id,
                failure_code="BUSINESS_CLARIFICATION_INVESTIGATION_FAILURE",
                event_type="run_failed",
                summary="Business clarification investigation failed safely",
                planning_turn_count=run_turns,
                actual_read_tool_execution_count=run_reads,
            )
            raise ApplicationError(
                code="INVESTIGATION_RUNTIME_FAILURE",
                message="物流调查暂时无法完成，请稍后重试。",
                status_code=503,
                retryable=True,
                trace_id=trace_id,
            ) from exc

        decision = PolicyDecision(
            route=PolicyRoute.SUPPORTED_LOGISTICS,
            supported=True,
            canonical_issue_type=refreshed_case.canonical_issue_type,
            authorized_order_id=refreshed_case.authorized_order_id,
            blocked_fragments=(),
            risk_flags=(),
            reason_code="BUSINESS_CLARIFICATION_CONTINUATION",
        )
        await self._apply_investigation_result(
            case=refreshed_case,
            run_id=run_id,
            decision=decision,
            output=output,
        )
        return {
            "run_id": run_id,
            "case_id": case.case_id,
            "events_url": f"/v1/conversations/{case.conversation_id}/events",
        }

    @staticmethod
    def _business_clarification_facts(content: str) -> tuple[bool, bool]:
        """Extract only the two deterministic customer facts the Gate accepts."""

        normalized = content.casefold()
        customer_still_reports_missing = not any(
            phrase in normalized
            for phrase in ("找到了", "已经收到了", "已经收到", "已收到", "收到了")
        )
        reception_locations_checked = any(
            phrase in normalized
            for phrase in (
                "都问过",
                "已经问过",
                "问过了",
                "没有代收",
                "没人代收",
                "未代收",
                "都没有",
            )
        )
        return customer_still_reports_missing, reception_locations_checked

    async def _create_and_investigate_case(
        self,
        *,
        conversation_id: str,
        customer_id: str,
        run_id: str,
        message_id: str,
        customer_message: str,
        decision: PolicyDecision,
        trace_id: str,
    ) -> dict[str, Any]:
        order_id = decision.authorized_order_id
        issue_type = decision.canonical_issue_type
        if order_id is None or issue_type is None:
            raise RuntimeError("supported policy decision lacks canonical scope")
        case_id = _new_id("case")
        with self.session_factory() as session:
            prior_cases = Repository(session).list_cases(conversation_id)
        related_case_id = next(
            (
                previous.case_id
                for previous in reversed(prior_cases)
                if previous.case_state == CaseState.CLOSED.value
                and previous.authorized_order_id == order_id
                and previous.canonical_issue_type == issue_type.value
            ),
            None,
        )
        case = InvestigationCase(
            case_id=case_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
            authorized_order_id=order_id,
            canonical_issue_type=issue_type,
            related_case_id=related_case_id,
        )
        with self.session_factory() as session, session.begin():
            repository = Repository(session)
            repository.create_case(case, reported_issue_type=issue_type.value)
            run = repository.require_run(run_id)
            run.case_id = case_id
            message = repository.get_message(message_id)
            if message is not None:
                message.case_id = case_id
        await self.events.append(
            EventDraft(
                conversation_id=conversation_id,
                case_id=case_id,
                run_id=run_id,
                event_type="case_created",
                visibility=EventVisibility.BOTH,
                summary="Logistics investigation created",
                payload={
                    "authorized_order_id": order_id,
                    "canonical_issue_type": issue_type.value,
                    "case_state": CaseState.INVESTIGATING.value,
                },
            )
        )
        acknowledgement = render_investigation_ack(
            order_id=order_id,
            issue_type=issue_type,
        )
        with self.session_factory() as session, session.begin():
            Repository(session).add_message(
                conversation_id,
                "assistant",
                acknowledgement,
                case_id=case_id,
                run_id=run_id,
            )
        await self._emit_customer_reply(
            conversation_id,
            run_id,
            acknowledgement,
            case_id=case_id,
            reply_kind="investigation_ack",
        )
        trusted = TrustedToolContext(
            customer_id=customer_id,
            conversation_id=conversation_id,
            case_id=case_id,
            run_id=run_id,
            authorized_order_id=order_id,
            canonical_issue_type=issue_type,
            fixture_version=self.fixtures.fixture_version,
            fault_seed=self.settings.scenario_fault_seed,
            evaluated_at=self.settings.scenario_evaluated_at,
            trace_id=trace_id,
        )
        try:
            output = await self.investigation.investigate(
                trusted=trusted,
                customer_message=customer_message,
                tool_cache=self._case_caches.setdefault(case_id, CaseToolCache()),
            )
        except Exception as exc:
            run_turns, run_reads = self._observed_run_usage(conversation_id, run_id)
            with self.session_factory() as session, session.begin():
                repository = Repository(session)
                current = repository.require_case(case_id)
                repository.update_case(
                    case_id,
                    expected_revision=current.revision,
                    case_state=CaseState.AWAITING_RETRY,
                    actual_read_tool_execution_count=min(
                        self.settings.max_case_read_executions,
                        current.actual_read_tool_execution_count + run_reads,
                    ),
                    agent_planning_turn_count=min(
                        self.settings.max_case_planning_turns,
                        current.agent_planning_turn_count + run_turns,
                    ),
                )
            await self._fail_run(
                conversation_id,
                run_id,
                case_id=case_id,
                failure_code="INVESTIGATION_RUNTIME_FAILURE",
                event_type="run_failed",
                summary="Investigation failed safely; no action was proposed",
                planning_turn_count=run_turns,
                actual_read_tool_execution_count=run_reads,
            )
            raise ApplicationError(
                code="INVESTIGATION_RUNTIME_FAILURE",
                message="物流调查暂时无法完成，请稍后重试。",
                status_code=503,
                retryable=True,
                trace_id=trace_id,
            ) from exc

        await self._apply_investigation_result(
            case=case,
            run_id=run_id,
            decision=decision,
            output=output,
        )
        return {
            "run_id": run_id,
            "case_id": case_id,
            "events_url": f"/v1/conversations/{conversation_id}/events",
        }

    async def _apply_investigation_result(
        self,
        *,
        case: InvestigationCase,
        run_id: str,
        decision: PolicyDecision,
        output: InvestigationOutput,
        allow_issue_revision: bool = True,
    ) -> None:
        gate = output.gate_result
        if gate.revised_issue_type is not None:
            if allow_issue_revision and gate.revised_issue_type is TriageIntent.STALLED_TRACKING:
                await self._revise_issue_and_continue_investigation(
                    case=case,
                    run_id=run_id,
                    decision=decision,
                    output=output,
                    revised_issue_type=IssueType.STALLED_TRACKING,
                    reason_code=gate.reason_code,
                )
                return
            await self._close_after_investigation(
                case=case,
                run_id=run_id,
                output=output,
                case_outcome=CaseOutcome.HUMAN_SUPPORT_REQUIRED,
                reason_code="ISSUE_REVISION_REQUIRES_NEW_INVESTIGATION",
                reply="订单状态与最初描述不一致，请重新选择对应的物流异常类型。",
            )
            return
        if gate.decision is None:
            raise RuntimeError("Evidence Gate produced no decision")

        reply = render_gate_reply(
            order_id=case.authorized_order_id,
            issue_type=case.canonical_issue_type,
            decision=gate.decision,
            reason_code=gate.reason_code,
            blocked_fragments=decision.blocked_fragments,
        )
        if gate.decision is EvidenceGateDecision.PROPOSE_TICKET:
            now = utc_now()
            proposal_id = _new_id("prop")
            with self.session_factory() as session:
                prior_proposals = Repository(session).list_proposals(case.case_id)
                proposal_version = (
                    max(
                        (item.version for item in prior_proposals),
                        default=0,
                    )
                    + 1
                )
                pending_proposal_ids = [
                    item.proposal_id
                    for item in prior_proposals
                    if item.proposal_state == ProposalState.PENDING_CONFIRMATION.value
                ]
            proposal = build_proposal(
                proposal_id=proposal_id,
                case_id=case.case_id,
                version=proposal_version,
                order_id=case.authorized_order_id,
                issue_type=case.canonical_issue_type,
                evidence_refs=list(output.evidence_refs),
                critical_result_hashes=gate.critical_result_hashes,
                now=now,
            )
            with self.session_factory() as session, session.begin():
                repository = Repository(session)
                current = repository.require_case(case.case_id)
                for previous_proposal_id in pending_proposal_ids:
                    repository.update_proposal_state(
                        previous_proposal_id,
                        ProposalState.SUPERSEDED,
                        superseded_by_proposal_id=proposal_id,
                        changed_at=now,
                    )
                repository.create_proposal(proposal)
                current = repository.require_case(case.case_id)
                repository.update_case(
                    case.case_id,
                    expected_revision=current.revision,
                    case_state=CaseState.AWAITING_CUSTOMER_CONFIRMATION,
                    actual_read_tool_execution_count=output.actual_read_tool_executions,
                    agent_planning_turn_count=output.case_planning_turns,
                    active_proposal_id=proposal_id,
                )
                repository.add_message(
                    case.conversation_id,
                    "assistant",
                    reply,
                    case_id=case.case_id,
                    run_id=run_id,
                )
                repository.update_run(
                    run_id,
                    run_state="succeeded",
                    planning_turn_count=output.planning_turns,
                    actual_read_tool_execution_count=output.run_read_tool_executions,
                )
            await self.events.append(
                EventDraft(
                    conversation_id=case.conversation_id,
                    case_id=case.case_id,
                    run_id=run_id,
                    event_type="action_recommended",
                    visibility=EventVisibility.DEVELOPER,
                    summary="Agent recommended deterministic ticket evaluation",
                    payload={"action_type": proposal.action_type.value},
                    evidence_refs=[ref.model_dump(mode="json") for ref in proposal.evidence_refs],
                )
            )
            for previous_proposal_id in pending_proposal_ids:
                await self.events.append(
                    EventDraft(
                        conversation_id=case.conversation_id,
                        case_id=case.case_id,
                        run_id=run_id,
                        event_type="proposal_superseded",
                        visibility=EventVisibility.BOTH,
                        summary="新的证据提案已替换旧提案",
                        payload={
                            "proposal_id": previous_proposal_id,
                            "superseded_by_proposal_id": proposal_id,
                        },
                    )
                )
            await self._emit_customer_reply(
                case.conversation_id,
                run_id,
                reply,
                case_id=case.case_id,
                reply_kind="investigation_result",
            )
            await self.pacer.pause("customer_explanation")
            await self.events.append(
                EventDraft(
                    conversation_id=case.conversation_id,
                    case_id=case.case_id,
                    run_id=run_id,
                    event_type="proposal_created",
                    visibility=EventVisibility.BOTH,
                    summary="等待你确认创建物流核查工单",
                    payload={
                        "proposal_id": proposal.proposal_id,
                        "proposal_version": proposal.version,
                        "authorized_order_id": case.authorized_order_id,
                        "canonical_issue_type": case.canonical_issue_type.value,
                        "rationale": proposal.customer_visible_effect,
                        "expires_at": _iso(proposal.expires_at),
                    },
                    evidence_refs=[ref.model_dump(mode="json") for ref in proposal.evidence_refs],
                )
            )
            await self._emit_run_succeeded(
                case.conversation_id,
                run_id,
                case_id=case.case_id,
            )
            return

        if gate.decision is EvidenceGateDecision.REQUEST_BUSINESS_CLARIFICATION:
            with self.session_factory() as session:
                clarification_case = Repository(session).require_case(case.case_id)
            if clarification_case.business_clarification_count >= 2:
                await self._close_after_investigation(
                    case=case,
                    run_id=run_id,
                    output=output,
                    case_outcome=CaseOutcome.HUMAN_SUPPORT_REQUIRED,
                    reason_code="BUSINESS_CLARIFICATION_LIMIT_REACHED",
                    reply=(
                        "这次物流核查已经达到可追问次数上限，"
                        "为避免基于不完整信息继续自动判断，请联系人工支持。"
                    ),
                )
                return
            with self.session_factory() as session, session.begin():
                repository = Repository(session)
                current = repository.require_case(case.case_id)
                next_clarification_count = current.business_clarification_count + 1
                repository.update_case(
                    case.case_id,
                    expected_revision=current.revision,
                    case_state=CaseState.AWAITING_CUSTOMER_INPUT,
                    business_clarification_count=next_clarification_count,
                    actual_read_tool_execution_count=output.actual_read_tool_executions,
                    agent_planning_turn_count=output.case_planning_turns,
                )
                repository.add_message(
                    case.conversation_id,
                    "assistant",
                    reply,
                    case_id=case.case_id,
                    run_id=run_id,
                )
                repository.update_run(
                    run_id,
                    run_state="succeeded",
                    planning_turn_count=output.planning_turns,
                    actual_read_tool_execution_count=output.run_read_tool_executions,
                )
            await self.events.append(
                EventDraft(
                    conversation_id=case.conversation_id,
                    case_id=case.case_id,
                    run_id=run_id,
                    event_type="business_clarification_requested",
                    visibility=EventVisibility.BOTH,
                    summary=reply,
                    payload={"customer_text": reply},
                )
            )
            await self.events.append(
                EventDraft(
                    conversation_id=case.conversation_id,
                    case_id=case.case_id,
                    run_id=run_id,
                    event_type="run_succeeded",
                    visibility=EventVisibility.DEVELOPER,
                    summary="Investigation paused for customer input",
                )
            )
            return

        if gate.decision is EvidenceGateDecision.RETRY_LATER:
            with self.session_factory() as session, session.begin():
                repository = Repository(session)
                current = repository.require_case(case.case_id)
                repository.update_case(
                    case.case_id,
                    expected_revision=current.revision,
                    case_state=CaseState.AWAITING_RETRY,
                    actual_read_tool_execution_count=output.actual_read_tool_executions,
                    agent_planning_turn_count=output.case_planning_turns,
                )
                repository.add_message(
                    case.conversation_id,
                    "assistant",
                    reply,
                    case_id=case.case_id,
                    run_id=run_id,
                )
                repository.update_run(
                    run_id,
                    run_state="succeeded",
                    planning_turn_count=output.planning_turns,
                    actual_read_tool_execution_count=output.run_read_tool_executions,
                )
            await self._emit_customer_reply_and_run_success(
                case.conversation_id,
                run_id,
                reply,
                case_id=case.case_id,
            )
            return

        outcome = (
            CaseOutcome.HUMAN_SUPPORT_REQUIRED
            if gate.decision is EvidenceGateDecision.REQUIRE_HUMAN_SUPPORT
            else CaseOutcome.RESOLVED_NO_ACTION
        )
        await self._close_after_investigation(
            case=case,
            run_id=run_id,
            output=output,
            case_outcome=outcome,
            reason_code=gate.reason_code,
            reply=reply,
        )

    async def _revise_issue_and_continue_investigation(
        self,
        *,
        case: InvestigationCase,
        run_id: str,
        decision: PolicyDecision,
        output: InvestigationOutput,
        revised_issue_type: IssueType,
        reason_code: str,
    ) -> None:
        """Persist an append-only issue correction, then use the same bounded Run.

        A signed-not-received report against a still-shipped order is not a
        terminal failure. The deterministic order observation corrects the
        Case's canonical issue, preserves the original report, and starts a
        fresh graph checkpoint pass with the existing Case cache and budgets.
        """

        revised_at = utc_now()
        with self.session_factory() as session, session.begin():
            repository = Repository(session)
            current = repository.require_case(case.case_id)
            repository.update_case(
                case.case_id,
                expected_revision=current.revision,
                actual_read_tool_execution_count=output.actual_read_tool_executions,
                agent_planning_turn_count=output.case_planning_turns,
            )
            after_usage = repository.require_case(case.case_id)
            revision_record = {
                "reported": current.reported_issue_type,
                "canonical": revised_issue_type.value,
                "reason_code": reason_code,
                "revised_at": _iso(revised_at),
                "evidence_refs": [ref.model_dump(mode="json") for ref in output.evidence_refs],
            }
            revised_row = repository.append_issue_revision(
                case.case_id,
                canonical_issue_type=revised_issue_type.value,
                revision_record=revision_record,
                expected_revision=after_usage.revision,
            )
            revised_case = case_to_domain(revised_row)

        await self.events.append(
            EventDraft(
                conversation_id=case.conversation_id,
                case_id=case.case_id,
                run_id=run_id,
                event_type="case_issue_revised",
                visibility=EventVisibility.BOTH,
                summary="订单状态修正了本次物流核查类型",
                payload={
                    "reported_issue_type": case.canonical_issue_type.value,
                    "canonical_issue_type": revised_issue_type.value,
                    "reason_code": reason_code,
                },
                evidence_refs=[ref.model_dump(mode="json") for ref in output.evidence_refs],
            )
        )
        trusted = TrustedToolContext(
            customer_id=revised_case.customer_id,
            conversation_id=revised_case.conversation_id,
            case_id=revised_case.case_id,
            run_id=run_id,
            authorized_order_id=revised_case.authorized_order_id,
            canonical_issue_type=revised_issue_type,
            fixture_version=self.fixtures.fixture_version,
            fault_seed=self.settings.scenario_fault_seed,
            evaluated_at=self.settings.scenario_evaluated_at,
            trace_id=_new_id("trc"),
        )
        cache = self._case_caches.get(case.case_id)
        if cache is None:
            cache = self._load_case_cache(case.case_id)
            self._case_caches[case.case_id] = cache
        try:
            revised_output = await self.investigation.investigate(
                trusted=trusted,
                customer_message=(
                    "订单状态已由服务端核对为仍在运输中。请按修正后的物流停滞问题继续收集必要证据。"
                ),
                case_planning_turns=revised_case.planning_turns,
                run_planning_turns=output.planning_turns,
                case_read_executions=revised_case.read_tool_executions,
                tool_cache=cache,
                investigation_pass=1,
            )
        except Exception as exc:
            run_turns, run_reads = self._observed_run_usage(
                revised_case.conversation_id,
                run_id,
            )
            with self.session_factory() as session, session.begin():
                repository = Repository(session)
                current = repository.require_case(case.case_id)
                repository.update_case(
                    case.case_id,
                    expected_revision=current.revision,
                    case_state=CaseState.AWAITING_RETRY,
                    actual_read_tool_execution_count=min(
                        self.settings.max_case_read_executions,
                        max(current.actual_read_tool_execution_count, run_reads),
                    ),
                    agent_planning_turn_count=min(
                        self.settings.max_case_planning_turns,
                        max(current.agent_planning_turn_count, run_turns),
                    ),
                )
            await self._fail_run(
                revised_case.conversation_id,
                run_id,
                case_id=case.case_id,
                failure_code="ISSUE_REVISION_INVESTIGATION_FAILURE",
                event_type="run_failed",
                summary="Corrected investigation failed safely",
                planning_turn_count=run_turns,
                actual_read_tool_execution_count=run_reads,
            )
            raise ApplicationError(
                code="INVESTIGATION_RUNTIME_FAILURE",
                message="物流调查暂时无法完成，请稍后重试。",
                status_code=503,
                retryable=True,
            ) from exc

        await self._apply_investigation_result(
            case=revised_case,
            run_id=run_id,
            decision=decision,
            output=revised_output,
            allow_issue_revision=False,
        )

    async def _close_after_investigation(
        self,
        *,
        case: InvestigationCase,
        run_id: str,
        output: InvestigationOutput,
        case_outcome: CaseOutcome,
        reason_code: str,
        reply: str,
    ) -> None:
        with self.session_factory() as session, session.begin():
            repository = Repository(session)
            current = repository.require_case(case.case_id)
            repository.update_case(
                case.case_id,
                expected_revision=current.revision,
                case_state=CaseState.CLOSED,
                case_outcome=case_outcome,
                reason_code=reason_code,
                actual_read_tool_execution_count=output.actual_read_tool_executions,
                agent_planning_turn_count=output.case_planning_turns,
            )
            repository.add_message(
                case.conversation_id,
                "assistant",
                reply,
                case_id=case.case_id,
                run_id=run_id,
            )
            repository.update_run(
                run_id,
                run_state="succeeded",
                planning_turn_count=output.planning_turns,
                actual_read_tool_execution_count=output.run_read_tool_executions,
            )
        await self._emit_customer_reply_and_run_success(
            case.conversation_id,
            run_id,
            reply,
            case_id=case.case_id,
        )
        await self.events.append(
            EventDraft(
                conversation_id=case.conversation_id,
                case_id=case.case_id,
                run_id=run_id,
                event_type="case_closed",
                visibility=EventVisibility.BOTH,
                summary="Investigation closed",
                payload={
                    "case_state": CaseState.CLOSED.value,
                    "case_outcome": case_outcome.value,
                    "reason_code": reason_code,
                },
            )
        )

    async def _start_run(
        self,
        conversation_id: str,
        run_id: str,
        *,
        case_id: str | None = None,
    ) -> None:
        with self.session_factory() as session, session.begin():
            Repository(session).update_run(run_id, run_state="running")
        await self.events.append(
            EventDraft(
                conversation_id=conversation_id,
                case_id=case_id,
                run_id=run_id,
                event_type="run_started",
                visibility=EventVisibility.DEVELOPER,
                summary="Run started",
            )
        )

    async def _fail_run(
        self,
        conversation_id: str,
        run_id: str,
        *,
        failure_code: str,
        event_type: str,
        summary: str,
        case_id: str | None = None,
        retryable: bool = True,
        planning_turn_count: int | None = None,
        actual_read_tool_execution_count: int | None = None,
    ) -> None:
        with self.session_factory() as session, session.begin():
            Repository(session).update_run(
                run_id,
                run_state="failed",
                failure_code=failure_code,
                planning_turn_count=planning_turn_count,
                actual_read_tool_execution_count=actual_read_tool_execution_count,
            )
        await self.events.append(
            EventDraft(
                conversation_id=conversation_id,
                case_id=case_id,
                run_id=run_id,
                event_type=event_type,
                visibility=EventVisibility.BOTH,
                summary=summary,
                payload={"failure_code": failure_code, "retryable": retryable},
            )
        )

    def _observed_run_usage(self, conversation_id: str, run_id: str) -> tuple[int, int]:
        """Recover already-consumed budget from durable facts after an exception."""

        with self.session_factory() as session:
            tool_rows = Repository(session).list_tool_calls(run_id=run_id)
        actual_reads = sum(1 for row in tool_rows if row.actual_execution)
        planning_turns = sum(
            1
            for event in self.events.list_after(conversation_id, limit=5_000)
            if event.run_id == run_id and event.event_type == "agent_turn_started"
        )
        return planning_turns, actual_reads

    async def _complete_without_case(
        self,
        *,
        conversation_id: str,
        run_id: str,
        reply: str,
    ) -> None:
        with self.session_factory() as session, session.begin():
            repository = Repository(session)
            repository.add_message(
                conversation_id,
                "assistant",
                reply,
                run_id=run_id,
            )
            repository.update_run(run_id, run_state="succeeded")
        await self._emit_customer_reply_and_run_success(conversation_id, run_id, reply)

    async def _emit_customer_reply_and_run_success(
        self,
        conversation_id: str,
        run_id: str,
        reply: str,
        *,
        case_id: str | None = None,
        reply_kind: str = "investigation_result",
    ) -> None:
        await self._emit_customer_reply(
            conversation_id,
            run_id,
            reply,
            case_id=case_id,
            reply_kind=reply_kind,
        )
        await self._emit_run_succeeded(conversation_id, run_id, case_id=case_id)

    async def _emit_customer_reply(
        self,
        conversation_id: str,
        run_id: str,
        reply: str,
        *,
        case_id: str | None = None,
        reply_kind: str,
    ) -> None:
        await self.events.append(
            EventDraft(
                conversation_id=conversation_id,
                case_id=case_id,
                run_id=run_id,
                event_type="customer_reply_created",
                visibility=EventVisibility.CUSTOMER,
                summary=reply,
                payload={"customer_text": reply, "reply_kind": reply_kind},
            )
        )

    async def _emit_run_succeeded(
        self,
        conversation_id: str,
        run_id: str,
        *,
        case_id: str | None = None,
    ) -> None:
        await self.events.append(
            EventDraft(
                conversation_id=conversation_id,
                case_id=case_id,
                run_id=run_id,
                event_type="run_succeeded",
                visibility=EventVisibility.DEVELOPER,
                summary="Run completed",
            )
        )

    async def _emit_triage_and_policy(
        self,
        conversation_id: str,
        run_id: str,
        triage: Any,
        decision: PolicyDecision,
    ) -> None:
        await self.events.append(
            EventDraft(
                conversation_id=conversation_id,
                run_id=run_id,
                event_type="triage_completed",
                visibility=EventVisibility.DEVELOPER,
                summary=f"Triage classified {triage.intent.value}",
                payload={
                    "intent": triage.intent.value,
                    "risk_flags": triage.risk_flags,
                    "order_ids_mentioned": triage.order_ids_mentioned,
                    "confidence": triage.confidence,
                },
            )
        )
        for fragment in decision.blocked_fragments:
            await self.events.append(
                EventDraft(
                    conversation_id=conversation_id,
                    run_id=run_id,
                    event_type="request_fragment_blocked",
                    visibility=EventVisibility.DEVELOPER,
                    summary=f"Blocked request fragment: {fragment.category}",
                    payload=fragment.as_dict(),
                )
            )
        await self.events.append(
            EventDraft(
                conversation_id=conversation_id,
                run_id=run_id,
                event_type="policy_decided",
                visibility=EventVisibility.DEVELOPER,
                summary=f"Policy route: {decision.route.value}",
                payload={
                    "route": decision.route.value,
                    "supported": decision.supported,
                    "reason_code": decision.reason_code,
                    "authorized_order_id": decision.authorized_order_id,
                    "canonical_issue_type": (
                        decision.canonical_issue_type.value
                        if decision.canonical_issue_type
                        else None
                    ),
                },
            )
        )

    def _conversation_exists(self, conversation_id: str) -> bool:
        with self.session_factory() as session:
            return Repository(session).get_conversation(conversation_id) is not None

    async def retry_case(self, case_id: str) -> dict[str, Any]:
        """Create a real retry Run while preserving Case counters and reusable evidence."""

        trace_id = _new_id("trc")
        with self.session_factory() as session:
            initial_case = Repository(session).get_case(case_id)
            if initial_case is None:
                raise ApplicationError(
                    code="INVESTIGATION_CASE_NOT_FOUND",
                    message="找不到这次物流调查。",
                    status_code=404,
                    trace_id=trace_id,
                )
            lock_key = f"conversation:{initial_case.conversation_id}"
        async with self.locks.serialize(lock_key):
            with self.session_factory() as session, session.begin():
                repository = Repository(session)
                case_row = repository.get_case(case_id)
                if case_row is None:
                    raise ApplicationError(
                        code="INVESTIGATION_CASE_NOT_FOUND",
                        message="找不到这次物流调查。",
                        status_code=404,
                        trace_id=trace_id,
                    )
                if case_row.case_state != CaseState.AWAITING_RETRY.value:
                    raise ApplicationError(
                        code="CASE_NOT_RETRYABLE",
                        message="当前调查状态不允许重试。",
                        status_code=409,
                        trace_id=trace_id,
                    )
                actions = repository.list_actions(case_id)
                if any(
                    action.action_state
                    in {ActionState.SUBMITTED.value, ActionState.UNCERTAIN.value}
                    for action in actions
                ):
                    raise ApplicationError(
                        code="ACTION_RETRY_UNSAFE",
                        message="写入结果尚未安全确认，系统不会重复执行。",
                        status_code=409,
                        trace_id=trace_id,
                    )
                failed_retryable_actions = [
                    action
                    for action in actions
                    if action.action_state == ActionState.FAILED_RETRYABLE.value
                ]
                if len(failed_retryable_actions) > 1:
                    raise ApplicationError(
                        code="ACTION_RETRY_CONFLICT",
                        message="当前物流核查存在多个待恢复的处理请求，需人工支持。",
                        status_code=409,
                        trace_id=trace_id,
                    )
                run_id = _new_id("run")
                repository.create_run(
                    None,
                    conversation_id=case_row.conversation_id,
                    run_kind="retry",
                    run_id=run_id,
                    case_id=case_id,
                    trace_id=trace_id,
                )
                conversation_id = case_row.conversation_id
                retry_action = failed_retryable_actions[0] if failed_retryable_actions else None
                if retry_action is not None:
                    repository.update_case(
                        case_id,
                        expected_revision=case_row.revision,
                        case_state=CaseState.EXECUTING_ACTION,
                    )
                    case = None
                    customer_message = None
                else:
                    customer_messages = [
                        message.content
                        for message in repository.list_messages(case_row.conversation_id)
                        if message.role == "customer" and message.case_id == case_id
                    ]
                    customer_message = (
                        customer_messages[-1] if customer_messages else "请继续调查当前物流异常。"
                    )
                    repository.update_case(
                        case_id,
                        expected_revision=case_row.revision,
                        case_state=CaseState.INVESTIGATING,
                    )
                    refreshed_case = repository.require_case(case_id)
                    case = case_to_domain(refreshed_case)

            await self._start_run(conversation_id, run_id, case_id=case_id)
            if retry_action is not None:
                return await self._submit_and_verify_ticket_action(
                    action_id=retry_action.action_id,
                    idempotency_key=retry_action.idempotency_key,
                    case_id=case_id,
                    conversation_id=conversation_id,
                    customer_id=case_row.customer_id,
                    authorized_order_id=case_row.authorized_order_id,
                    issue_type=IssueType(case_row.canonical_issue_type),
                    run_id=run_id,
                    proposal_id=retry_action.proposal_id,
                )
            if case is None or customer_message is None:
                raise RuntimeError("retry investigation did not establish a Case scope")
            trusted = TrustedToolContext(
                customer_id=case.customer_id,
                conversation_id=conversation_id,
                case_id=case_id,
                run_id=run_id,
                authorized_order_id=case.authorized_order_id,
                canonical_issue_type=case.canonical_issue_type,
                fixture_version=self.fixtures.fixture_version,
                fault_seed=self.settings.scenario_fault_seed,
                evaluated_at=self.settings.scenario_evaluated_at,
                trace_id=trace_id,
            )
            cache = self._case_caches.get(case_id)
            if cache is None:
                cache = self._load_case_cache(case_id)
                self._case_caches[case_id] = cache
            try:
                output = await self.investigation.investigate(
                    trusted=trusted,
                    customer_message=customer_message,
                    case_planning_turns=case.planning_turns,
                    case_read_executions=case.read_tool_executions,
                    tool_cache=cache,
                )
            except Exception as exc:
                run_turns, run_reads = self._observed_run_usage(conversation_id, run_id)
                with self.session_factory() as session, session.begin():
                    repository = Repository(session)
                    current = repository.require_case(case_id)
                    repository.update_case(
                        case_id,
                        expected_revision=current.revision,
                        case_state=CaseState.AWAITING_RETRY,
                        actual_read_tool_execution_count=min(
                            self.settings.max_case_read_executions,
                            current.actual_read_tool_execution_count + run_reads,
                        ),
                        agent_planning_turn_count=min(
                            self.settings.max_case_planning_turns,
                            current.agent_planning_turn_count + run_turns,
                        ),
                    )
                await self._fail_run(
                    conversation_id,
                    run_id,
                    case_id=case_id,
                    failure_code="INVESTIGATION_RUNTIME_FAILURE",
                    event_type="run_failed",
                    summary="Retry investigation failed safely",
                    planning_turn_count=run_turns,
                    actual_read_tool_execution_count=run_reads,
                )
                raise ApplicationError(
                    code="INVESTIGATION_RUNTIME_FAILURE",
                    message="物流调查暂时无法完成，请稍后重试。",
                    status_code=503,
                    retryable=True,
                    trace_id=trace_id,
                ) from exc

            decision = PolicyDecision(
                route=PolicyRoute.SUPPORTED_LOGISTICS,
                supported=True,
                canonical_issue_type=case.canonical_issue_type,
                authorized_order_id=case.authorized_order_id,
                blocked_fragments=(),
                risk_flags=(),
                reason_code="AUTHORIZED_RETRY",
            )
            await self._apply_investigation_result(
                case=case,
                run_id=run_id,
                decision=decision,
                output=output,
            )
            return {
                "run_id": run_id,
                "case_id": case_id,
                "events_url": f"/v1/conversations/{conversation_id}/events",
            }

    def _load_case_cache(self, case_id: str) -> CaseToolCache:
        """Rehydrate completed evidence and retry attempts from persisted tool calls."""

        cache = CaseToolCache()
        with self.session_factory() as session:
            rows = Repository(session).list_tool_calls(case_id=case_id)
        for row in rows:
            if not row.actual_execution or row.result_envelope is None:
                continue
            order_id = str(row.normalized_args.get("order_id", ""))
            if not order_id or row.source_version is None:
                continue
            current_revision = self.fixtures.source_revision(order_id, row.tool_name)
            key = ToolCacheKey(
                case_id=case_id,
                tool_name=row.tool_name,
                normalized_args=normalize_tool_arguments(row.normalized_args),
                source_revision=current_revision,
            )
            cache.record_actual_attempt(key)
            if row.source_version != current_revision:
                continue
            cache.store(key, ToolResult[Any].model_validate(row.result_envelope))
        return cache

    async def confirm_proposal(
        self,
        proposal_id: str,
        proposal_version: int,
    ) -> dict[str, Any]:
        trace_id = _new_id("trc")
        with self.session_factory() as session:
            repository = Repository(session)
            proposal_row = repository.get_proposal(proposal_id)
            if proposal_row is None:
                raise ApplicationError(
                    code="PROPOSAL_NOT_FOUND",
                    message="找不到这项待确认操作。",
                    status_code=404,
                    trace_id=trace_id,
                )
            case_id = proposal_row.case_id
            lock_key = f"conversation:{proposal_row.conversation_id}"

        async with self.locks.serialize(lock_key):
            run_id, conversation_id = await self._create_action_run(
                case_id=case_id,
                run_kind="confirmation",
                trace_id=trace_id,
            )
            now = utc_now()
            with self.session_factory() as session:
                repository = Repository(session)
                proposal_row = repository.require_proposal(proposal_id)
                case_row = repository.require_case(proposal_row.case_id)
                try:
                    self._validate_pending_proposal(
                        proposal_row,
                        case_row,
                        proposal_version=proposal_version,
                        now=now,
                    )
                    authorize_order(
                        case_row.customer_id,
                        case_row.authorized_order_id,
                        self.fixtures,
                    )
                except (ApplicationError, AuthorizationError) as exc:
                    if isinstance(exc, ApplicationError) and exc.code == "PROPOSAL_EXPIRED":
                        with self.session_factory() as expiry_session, expiry_session.begin():
                            expiry_repository = Repository(expiry_session)
                            expiry_repository.update_proposal_state(
                                proposal_id,
                                ProposalState.EXPIRED,
                                changed_at=now,
                            )
                            expiry_case = expiry_repository.require_case(case_id)
                            expiry_repository.update_case(
                                case_id,
                                expected_revision=expiry_case.revision,
                                case_state=CaseState.AWAITING_RETRY,
                            )
                        await self.events.append(
                            EventDraft(
                                conversation_id=conversation_id,
                                case_id=case_id,
                                run_id=run_id,
                                event_type="proposal_expired",
                                visibility=EventVisibility.BOTH,
                                summary="待确认操作已过期",
                                payload={"proposal_id": proposal_id},
                            )
                        )
                    await self._fail_run(
                        conversation_id,
                        run_id,
                        case_id=case_id,
                        failure_code="PROPOSAL_REVALIDATION_FAILED",
                        event_type="run_failed",
                        summary="Proposal confirmation was rejected safely",
                        retryable=False,
                    )
                    if isinstance(exc, ApplicationError):
                        raise
                    raise ApplicationError(
                        code="PROPOSAL_REVALIDATION_FAILED",
                        message="订单权限或提案状态已经变化，未执行任何操作。",
                        status_code=409,
                        trace_id=trace_id,
                    ) from exc
                if (
                    repository.get_active_ticket(
                        case_row.authorized_order_id,
                        case_row.canonical_issue_type,
                    )
                    is not None
                ):
                    await self._fail_run(
                        conversation_id,
                        run_id,
                        case_id=case_id,
                        failure_code="ACTIVE_TICKET_ALREADY_EXISTS",
                        event_type="run_failed",
                        summary="Duplicate active ticket prevented execution",
                        retryable=False,
                    )
                    raise ApplicationError(
                        code="ACTIVE_TICKET_ALREADY_EXISTS",
                        message="这笔订单已有进行中的同类核查工单。",
                        status_code=409,
                        trace_id=trace_id,
                    )

            current_gate = self._revalidate_gate(
                case_row=case_row,
                proposal_row=proposal_row,
            )
            current_hash = (
                evidence_snapshot_hash(
                    critical_result_hashes=current_gate.critical_result_hashes,
                    execution_parameters=proposal_row.execution_parameters,
                )
                if current_gate is not None
                else None
            )
            if (
                current_gate is None
                or current_gate.decision is not EvidenceGateDecision.PROPOSE_TICKET
                or current_hash != proposal_row.evidence_snapshot_hash
            ):
                with self.session_factory() as session, session.begin():
                    repository = Repository(session)
                    repository.update_proposal_state(
                        proposal_id,
                        ProposalState.INVALIDATED,
                        changed_at=now,
                    )
                    current_case = repository.require_case(case_id)
                    repository.update_case(
                        case_id,
                        expected_revision=current_case.revision,
                        case_state=CaseState.AWAITING_RETRY,
                    )
                await self.events.append(
                    EventDraft(
                        conversation_id=conversation_id,
                        case_id=case_id,
                        run_id=run_id,
                        event_type="proposal_invalidated",
                        visibility=EventVisibility.BOTH,
                        summary="关键证据已变化，原提案不再有效",
                        payload={"proposal_id": proposal_id},
                    )
                )
                await self._fail_run(
                    conversation_id,
                    run_id,
                    case_id=case_id,
                    failure_code="PROPOSAL_EVIDENCE_CHANGED",
                    event_type="run_failed",
                    summary="Proposal evidence changed before confirmation",
                    retryable=False,
                )
                raise ApplicationError(
                    code="PROPOSAL_EVIDENCE_CHANGED",
                    message="关键证据已经变化，请重新调查后再确认。",
                    status_code=409,
                    trace_id=trace_id,
                )

            proposal = proposal_to_domain(proposal_row)
            action = build_ready_action(proposal)
            with self.session_factory() as session, session.begin():
                repository = Repository(session)
                repository.update_proposal_state(
                    proposal_id,
                    ProposalState.CONFIRMED,
                    changed_at=now,
                )
                repository.create_action(action)
                current_case = repository.require_case(case_id)
                repository.update_case(
                    case_id,
                    expected_revision=current_case.revision,
                    case_state=CaseState.EXECUTING_ACTION,
                )
            await self.events.append(
                EventDraft(
                    conversation_id=conversation_id,
                    case_id=case_id,
                    run_id=run_id,
                    event_type="proposal_confirmed",
                    visibility=EventVisibility.BOTH,
                    summary="已确认创建物流核查工单",
                    payload={
                        "proposal_id": proposal_id,
                        "proposal_version": proposal_version,
                    },
                )
            )

            return await self._submit_and_verify_ticket_action(
                action_id=action.action_id,
                idempotency_key=action.idempotency_key,
                case_id=case_id,
                conversation_id=conversation_id,
                customer_id=case_row.customer_id,
                authorized_order_id=case_row.authorized_order_id,
                issue_type=IssueType(case_row.canonical_issue_type),
                run_id=run_id,
                proposal_id=proposal_id,
            )

    async def _submit_and_verify_ticket_action(
        self,
        *,
        action_id: str,
        idempotency_key: str,
        case_id: str,
        conversation_id: str,
        customer_id: str,
        authorized_order_id: str,
        issue_type: IssueType,
        run_id: str,
        proposal_id: str,
    ) -> dict[str, Any]:
        """Execute or resume the single deterministic ticket action.

        The model never reaches this method.  It is intentionally the only
        place that can submit the simulated write and its read-back.  Faults are
        fixture-owned so tests can prove retryable, terminal, and uncertain
        outcomes without changing the product policy or granting a tool write
        capability to the Agent.
        """

        submitted_at = utc_now()
        ticket_id = stable_ticket_id(idempotency_key)
        scripted_fault = self.fixtures.consume_action_fault(self.settings.scenario_fault_seed)
        with self.session_factory() as session, session.begin():
            repository = Repository(session)
            repository.update_action_state(
                action_id,
                ActionState.SUBMITTED,
                submitted_at=submitted_at,
                changed_at=submitted_at,
            )
            if scripted_fault is None:
                repository.create_ticket(
                    ticket_id=ticket_id,
                    case_id=case_id,
                    action_id=action_id,
                    customer_id=customer_id,
                    authorized_order_id=authorized_order_id,
                    issue_type=issue_type,
                    idempotency_key=idempotency_key,
                    details={"source": "confirmed_customer_proposal"},
                    created_at=submitted_at,
                )
        await self.events.append(
            EventDraft(
                conversation_id=conversation_id,
                case_id=case_id,
                run_id=run_id,
                event_type="action_submitted",
                visibility=EventVisibility.DEVELOPER,
                summary="Simulated ticket write submitted",
                payload={"action_id": action_id, "action_state": ActionState.SUBMITTED.value},
            )
        )

        if scripted_fault is not None:
            return await self._record_scripted_action_fault(
                action_id=action_id,
                case_id=case_id,
                conversation_id=conversation_id,
                run_id=run_id,
                proposal_id=proposal_id,
                action_state=scripted_fault.action_state,
                error_code=scripted_fault.error_code,
            )

        self.fixtures.add_ticket(
            LogisticsTicket(
                ticket_id=ticket_id,
                order_id=authorized_order_id,
                issue_type=issue_type,
                ticket_status="open",
                created_at=submitted_at,
            )
        )
        with self.session_factory() as session, session.begin():
            repository = Repository(session)
            ticket = repository.get_ticket_by_idempotency_key(idempotency_key)
            if ticket is None:
                repository.update_action_state(
                    action_id,
                    ActionState.UNCERTAIN,
                    error_code="TICKET_READ_BACK_UNAVAILABLE",
                    changed_at=utc_now(),
                )
                current_case = repository.require_case(case_id)
                repository.update_case(
                    case_id,
                    expected_revision=current_case.revision,
                    case_state=CaseState.CLOSED,
                    case_outcome=CaseOutcome.UNCERTAIN,
                    reason_code="ACTION_READ_BACK_UNAVAILABLE",
                )
                reply = (
                    "这次处理请求可能已经提交，但目前还无法确认结果。"
                    "请不要重复操作，可以联系人工支持继续核对。"
                )
                repository.add_message(
                    conversation_id,
                    "assistant",
                    reply,
                    case_id=case_id,
                    run_id=run_id,
                )
                repository.update_run(run_id, run_state="succeeded")
                read_back_succeeded = False
            else:
                verified_at = utc_now()
                repository.update_action_state(
                    action_id,
                    ActionState.SUCCEEDED,
                    verified_at=verified_at,
                    changed_at=verified_at,
                )
                current_case = repository.require_case(case_id)
                repository.update_case(
                    case_id,
                    expected_revision=current_case.revision,
                    case_state=CaseState.CLOSED,
                    case_outcome=CaseOutcome.TICKET_CREATED,
                    reason_code="LOGISTICS_TICKET_CREATED_AND_VERIFIED",
                )
                reply = (
                    "已经为你发起物流核查。物流方会继续确认包裹去向，"
                    f"请保留处理编号 {ticket_id}，无需重复提交。"
                )
                repository.add_message(
                    conversation_id,
                    "assistant",
                    reply,
                    case_id=case_id,
                    run_id=run_id,
                )
                repository.update_run(run_id, run_state="succeeded")
                read_back_succeeded = True

        await self._emit_customer_reply(
            conversation_id,
            run_id,
            reply,
            case_id=case_id,
            reply_kind="action_result",
        )
        if not read_back_succeeded:
            await self.events.append(
                EventDraft(
                    conversation_id=conversation_id,
                    case_id=case_id,
                    run_id=run_id,
                    event_type="action_uncertain",
                    visibility=EventVisibility.BOTH,
                    summary="Ticket write result is uncertain",
                    payload={
                        "action_id": action_id,
                        "action_state": ActionState.UNCERTAIN.value,
                        "retry_allowed": False,
                    },
                )
            )
            await self._emit_run_succeeded(conversation_id, run_id, case_id=case_id)
            await self.events.append(
                EventDraft(
                    conversation_id=conversation_id,
                    case_id=case_id,
                    run_id=run_id,
                    event_type="case_closed",
                    visibility=EventVisibility.BOTH,
                    summary="Investigation closed with uncertain action outcome",
                    payload={
                        "case_state": CaseState.CLOSED.value,
                        "case_outcome": CaseOutcome.UNCERTAIN.value,
                        "reason_code": "ACTION_READ_BACK_UNAVAILABLE",
                    },
                )
            )
            return self._action_transition_response(
                run_id=run_id,
                case_id=case_id,
                proposal_id=proposal_id,
                conversation_id=conversation_id,
            )

        await self.events.append(
            EventDraft(
                conversation_id=conversation_id,
                case_id=case_id,
                run_id=run_id,
                event_type="action_verified",
                visibility=EventVisibility.BOTH,
                summary="物流核查工单已创建并验证",
                payload={
                    "action_id": action_id,
                    "action_state": ActionState.SUCCEEDED.value,
                    "ticket_id": ticket_id,
                },
            )
        )
        await self._emit_run_succeeded(conversation_id, run_id, case_id=case_id)
        await self.events.append(
            EventDraft(
                conversation_id=conversation_id,
                case_id=case_id,
                run_id=run_id,
                event_type="case_closed",
                visibility=EventVisibility.BOTH,
                summary="Investigation closed after verified ticket creation",
                payload={
                    "case_state": CaseState.CLOSED.value,
                    "case_outcome": CaseOutcome.TICKET_CREATED.value,
                    "reason_code": "LOGISTICS_TICKET_CREATED_AND_VERIFIED",
                },
            )
        )
        return self._action_transition_response(
            run_id=run_id,
            case_id=case_id,
            proposal_id=proposal_id,
            conversation_id=conversation_id,
        )

    async def _record_scripted_action_fault(
        self,
        *,
        action_id: str,
        case_id: str,
        conversation_id: str,
        run_id: str,
        proposal_id: str,
        action_state: ActionState,
        error_code: str,
    ) -> dict[str, Any]:
        """Persist a deterministic action fault without fabricating a ticket."""

        retry_allowed = action_state is ActionState.FAILED_RETRYABLE
        if action_state is ActionState.FAILED_RETRYABLE:
            case_state = CaseState.AWAITING_RETRY
            case_outcome: CaseOutcome | None = None
            reason_code: str | None = None
            reply = "物流核查请求暂时未能提交，可以使用原处理请求重试。"
        elif action_state is ActionState.FAILED_TERMINAL:
            case_state = CaseState.CLOSED
            case_outcome = CaseOutcome.FAILED
            reason_code = "ACTION_FAILED_TERMINAL"
            reply = "物流核查请求未能提交，系统不会自动重复操作，请联系人工支持。"
        elif action_state is ActionState.UNCERTAIN:
            case_state = CaseState.CLOSED
            case_outcome = CaseOutcome.UNCERTAIN
            reason_code = "ACTION_WRITE_RESULT_UNCERTAIN"
            reply = (
                "这次处理请求可能已经提交，但目前无法确认结果。"
                "请不要重复操作，可以联系人工支持继续核对。"
            )
        else:
            raise RuntimeError("unsupported scripted action fault state")

        with self.session_factory() as session, session.begin():
            repository = Repository(session)
            repository.update_action_state(
                action_id,
                action_state,
                error_code=error_code,
                changed_at=utc_now(),
            )
            current_case = repository.require_case(case_id)
            if case_state is CaseState.CLOSED:
                repository.update_case(
                    case_id,
                    expected_revision=current_case.revision,
                    case_state=case_state,
                    case_outcome=case_outcome,
                    reason_code=reason_code,
                )
            else:
                repository.update_case(
                    case_id,
                    expected_revision=current_case.revision,
                    case_state=case_state,
                )
            repository.add_message(
                conversation_id,
                "assistant",
                reply,
                case_id=case_id,
                run_id=run_id,
            )
            repository.update_run(run_id, run_state="succeeded")

        await self._emit_customer_reply(
            conversation_id,
            run_id,
            reply,
            case_id=case_id,
            reply_kind="action_result",
        )
        await self.events.append(
            EventDraft(
                conversation_id=conversation_id,
                case_id=case_id,
                run_id=run_id,
                event_type=(
                    "action_uncertain" if action_state is ActionState.UNCERTAIN else "action_failed"
                ),
                visibility=EventVisibility.BOTH,
                summary=reply,
                payload={
                    "action_id": action_id,
                    "action_state": action_state.value,
                    "error_code": error_code,
                    "retry_allowed": retry_allowed,
                },
            )
        )
        await self._emit_run_succeeded(conversation_id, run_id, case_id=case_id)
        if case_state is CaseState.CLOSED:
            await self.events.append(
                EventDraft(
                    conversation_id=conversation_id,
                    case_id=case_id,
                    run_id=run_id,
                    event_type="case_closed",
                    visibility=EventVisibility.BOTH,
                    summary="Investigation closed after deterministic action outcome",
                    payload={
                        "case_state": CaseState.CLOSED.value,
                        "case_outcome": case_outcome.value if case_outcome else None,
                        "reason_code": reason_code,
                    },
                )
            )
        return self._action_transition_response(
            run_id=run_id,
            case_id=case_id,
            proposal_id=proposal_id,
            conversation_id=conversation_id,
        )

    @staticmethod
    def _action_transition_response(
        *,
        run_id: str,
        case_id: str,
        proposal_id: str,
        conversation_id: str,
    ) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "case_id": case_id,
            "proposal_id": proposal_id,
            "proposal_state": ProposalState.CONFIRMED.value,
            "events_url": f"/v1/conversations/{conversation_id}/events",
        }

    async def decline_proposal(
        self,
        proposal_id: str,
        proposal_version: int,
    ) -> dict[str, Any]:
        trace_id = _new_id("trc")
        with self.session_factory() as session:
            proposal_row = Repository(session).get_proposal(proposal_id)
            if proposal_row is None:
                raise ApplicationError(
                    code="PROPOSAL_NOT_FOUND",
                    message="找不到这项待确认操作。",
                    status_code=404,
                    trace_id=trace_id,
                )
            case_id = proposal_row.case_id
            lock_key = f"conversation:{proposal_row.conversation_id}"
        async with self.locks.serialize(lock_key):
            run_id, conversation_id = await self._create_action_run(
                case_id=case_id,
                run_kind="decline",
                trace_id=trace_id,
            )
            now = utc_now()
            with self.session_factory() as session:
                repository = Repository(session)
                proposal_row = repository.require_proposal(proposal_id)
                case_row = repository.require_case(case_id)
                try:
                    self._validate_pending_proposal(
                        proposal_row,
                        case_row,
                        proposal_version=proposal_version,
                        now=now,
                    )
                except ApplicationError as exc:
                    if exc.code == "PROPOSAL_EXPIRED":
                        with self.session_factory() as expiry_session, expiry_session.begin():
                            expiry_repository = Repository(expiry_session)
                            expiry_repository.update_proposal_state(
                                proposal_id,
                                ProposalState.EXPIRED,
                                changed_at=now,
                            )
                            expiry_case = expiry_repository.require_case(case_id)
                            expiry_repository.update_case(
                                case_id,
                                expected_revision=expiry_case.revision,
                                case_state=CaseState.AWAITING_RETRY,
                            )
                        await self.events.append(
                            EventDraft(
                                conversation_id=conversation_id,
                                case_id=case_id,
                                run_id=run_id,
                                event_type="proposal_expired",
                                visibility=EventVisibility.BOTH,
                                summary="待确认操作已过期",
                                payload={"proposal_id": proposal_id},
                            )
                        )
                    await self._fail_run(
                        conversation_id,
                        run_id,
                        case_id=case_id,
                        failure_code="PROPOSAL_DECLINE_REJECTED",
                        event_type="run_failed",
                        summary="Proposal decline was rejected safely",
                        retryable=False,
                    )
                    raise
            with self.session_factory() as session, session.begin():
                repository = Repository(session)
                repository.update_proposal_state(
                    proposal_id,
                    ProposalState.DECLINED,
                    changed_at=now,
                )
                current_case = repository.require_case(case_id)
                repository.update_case(
                    case_id,
                    expected_revision=current_case.revision,
                    case_state=CaseState.CLOSED,
                    case_outcome=CaseOutcome.RESOLVED_NO_ACTION,
                    reason_code="CUSTOMER_DECLINED_PROPOSAL",
                )
                reply = "好的，这次先不发起物流核查，没有提交任何处理请求。"
                repository.add_message(
                    conversation_id,
                    "assistant",
                    reply,
                    case_id=case_id,
                    run_id=run_id,
                )
                repository.update_run(run_id, run_state="succeeded")
            await self.events.append(
                EventDraft(
                    conversation_id=conversation_id,
                    case_id=case_id,
                    run_id=run_id,
                    event_type="proposal_declined",
                    visibility=EventVisibility.BOTH,
                    summary="客户选择暂不发起物流核查",
                    payload={
                        "proposal_id": proposal_id,
                        "proposal_version": proposal_version,
                    },
                )
            )
            await self._emit_customer_reply_and_run_success(
                conversation_id,
                run_id,
                reply,
                case_id=case_id,
            )
            await self.events.append(
                EventDraft(
                    conversation_id=conversation_id,
                    case_id=case_id,
                    run_id=run_id,
                    event_type="case_closed",
                    visibility=EventVisibility.BOTH,
                    summary="Investigation closed after customer declined",
                    payload={
                        "case_state": CaseState.CLOSED.value,
                        "case_outcome": CaseOutcome.RESOLVED_NO_ACTION.value,
                        "reason_code": "CUSTOMER_DECLINED_PROPOSAL",
                    },
                )
            )
            return {
                "run_id": run_id,
                "case_id": case_id,
                "proposal_id": proposal_id,
                "proposal_state": ProposalState.DECLINED.value,
                "events_url": f"/v1/conversations/{conversation_id}/events",
            }

    async def _create_action_run(
        self,
        *,
        case_id: str,
        run_kind: str,
        trace_id: str,
    ) -> tuple[str, str]:
        run_id = _new_id("run")
        with self.session_factory() as session, session.begin():
            repository = Repository(session)
            case = repository.require_case(case_id)
            repository.create_run(
                None,
                conversation_id=case.conversation_id,
                run_kind=run_kind,
                run_id=run_id,
                case_id=case_id,
                trace_id=trace_id,
            )
            conversation_id = case.conversation_id
        await self._start_run(conversation_id, run_id, case_id=case_id)
        return run_id, conversation_id

    @staticmethod
    def _validate_pending_proposal(
        proposal: ActionProposalRow,
        case: InvestigationCaseRow,
        *,
        proposal_version: int,
        now: datetime,
    ) -> None:
        if proposal.version != proposal_version:
            raise ApplicationError(
                code="PROPOSAL_VERSION_CONFLICT",
                message="提案版本已经变化，未执行任何操作。",
                status_code=409,
            )
        if proposal.proposal_state != ProposalState.PENDING_CONFIRMATION.value:
            raise ApplicationError(
                code="PROPOSAL_NOT_PENDING",
                message="这项提案已经失效或处理过。",
                status_code=409,
            )
        if proposal.expires_at <= now:
            raise ApplicationError(
                code="PROPOSAL_EXPIRED",
                message="这项提案已经过期，请重新调查。",
                status_code=409,
            )
        if (
            case.case_state != CaseState.AWAITING_CUSTOMER_CONFIRMATION.value
            or case.active_proposal_id != proposal.proposal_id
        ):
            raise ApplicationError(
                code="PROPOSAL_NOT_ACTIVE",
                message="这项提案不再是当前可执行操作。",
                status_code=409,
            )

    def _revalidate_gate(
        self,
        *,
        case_row: InvestigationCaseRow,
        proposal_row: ActionProposalRow,
    ) -> EvidenceGateResult | None:
        """Re-evaluate persisted evidence only when every source revision is unchanged.

        Confirmation must not secretly spend another four or five read-tool calls.
        The original normalized results remain valid only while their server-owned
        source revisions still match. A changed revision invalidates the Proposal and
        requires a new Investigation Run through the governed budgeted tool path.
        """

        issue_type = IssueType(case_row.canonical_issue_type)
        evidence_call_ids = {
            str(reference["tool_call_id"])
            for reference in proposal_row.evidence_refs
            if reference.get("tool_call_id")
        }
        with self.session_factory() as session:
            rows = [
                row
                for row in Repository(session).list_tool_calls(case_id=case_row.case_id)
                if row.tool_call_id in evidence_call_ids
            ]
        results: dict[str, ToolResult[Any]] = {}
        for row in rows:
            if row.result_envelope is None or row.source_version is None:
                return None
            current_revision = self.fixtures.source_revision(
                case_row.authorized_order_id,
                row.tool_name,
            )
            if row.source_version != current_revision:
                return None
            results[row.tool_name] = ToolResult[Any].model_validate(row.result_envelope)

        required = {
            "get_order_context",
            "get_logistics_timeline",
            "get_after_sales_policy",
            "get_existing_logistics_tickets",
        }
        if issue_type is IssueType.SIGNED_NOT_RECEIVED:
            required.add("get_delivery_proof")
        if not required.issubset(results):
            return None

        order = results["get_order_context"]
        timeline = results["get_logistics_timeline"]
        policy = results["get_after_sales_policy"]
        tickets = results["get_existing_logistics_tickets"]
        if issue_type is IssueType.SIGNED_NOT_RECEIVED:
            proof = results["get_delivery_proof"]
            return evaluate_evidence_gate(
                issue_type,
                SignedNotReceivedEvidence(
                    order_context=ToolResult[OrderContextPayload].model_validate(
                        order.model_dump()
                    ),
                    timeline=ToolResult[LogisticsTimelinePayload].model_validate(
                        timeline.model_dump()
                    ),
                    delivery_proof=ToolResult[DeliveryProofPayload].model_validate(
                        proof.model_dump()
                    ),
                    existing_tickets=ToolResult[ExistingLogisticsTicketsPayload].model_validate(
                        tickets.model_dump()
                    ),
                    policy=ToolResult[AfterSalesPolicyPayload].model_validate(policy.model_dump()),
                ),
            )
        return evaluate_evidence_gate(
            issue_type,
            StalledTrackingEvidence(
                order_context=ToolResult[OrderContextPayload].model_validate(order.model_dump()),
                timeline=ToolResult[LogisticsTimelinePayload].model_validate(timeline.model_dump()),
                existing_tickets=ToolResult[ExistingLogisticsTicketsPayload].model_validate(
                    tickets.model_dump()
                ),
                policy=ToolResult[AfterSalesPolicyPayload].model_validate(policy.model_dump()),
            ),
        )

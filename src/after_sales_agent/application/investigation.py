"""Production investigation composition over the real LangGraph ToolNode path."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from after_sales_agent.agents.graph import build_investigation_graph
from after_sales_agent.agents.models import (
    AgentObservationSelector,
    WorkflowObservationSelector,
    build_investigation_model,
)
from after_sales_agent.agents.prompts import (
    INVESTIGATION_PROMPT_VERSION,
    INVESTIGATION_SYSTEM_PROMPT,
)
from after_sales_agent.agents.tool_bindings import READ_TOOLS, InvestigationRuntimeContext
from after_sales_agent.application.adaptive_core import (
    BudgetSnapshot,
    DecisionContext,
    DecisionTraceRecord,
    EvidenceProgressReducer,
    GateReadiness,
    GuardController,
    NextObservationCandidate,
    ObservationAction,
    ObservationRouter,
    ObservationValidator,
    RecoveryReasonCode,
    RecoveryTraceRecord,
    RetryDirective,
    SelectorKind,
    StateTraceRecord,
    TracePhase,
    build_decision_context,
    canonical_arguments_hash,
    issue_exact_retry,
    required_observation_tools,
    select_next_observation,
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
from after_sales_agent.tools.cache import CaseToolCache, ToolCacheKey, normalize_tool_arguments
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
    """Project canonical policy evidence without vectors, raw candidates, or poison text."""

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
        "index_content_digest": payload.index_content_digest,
        "embedding_model_id": payload.embedding_model_id,
        "embedding_model_revision": payload.embedding_model_revision,
        "retrieval_mode": payload.retrieval_mode,
        "clause_id": facts.clause_id if facts is not None else None,
        "policy_version": facts.policy_version if facts is not None else None,
        "region": facts.region if facts is not None else payload.region,
        "verified_citation": citation.display_summary if citation is not None else None,
        "citation_excerpt": citation.excerpt if citation is not None else None,
        "citation_text_classification": (
            citation.text_classification if citation is not None else None
        ),
        "selected_rank": payload.selected_rank,
        "selected_similarity": payload.selected_similarity,
        "retrieval_latency_ms": round(payload.retrieval_latency_ms, 3),
        "resolver_latency_ms": round(payload.resolver_latency_ms, 3),
    }


def _model_visible_tool_result(tool_name: str, result: ToolResult[Any]) -> dict[str, Any]:
    """Keep controlled citation prose out of the model context.

    The model receives typed facts and citation identifiers only.  The bounded
    excerpt is retained in the canonical tool record and Developer Trace, where
    it is labelled as untrusted explanatory text rather than decision authority.
    """

    projected = result.model_dump(mode="json")
    if tool_name == "search_after_sales_policy":
        payload = projected.get("payload")
        if isinstance(payload, dict):
            citation = payload.get("citation")
            if isinstance(citation, dict):
                citation.pop("excerpt", None)
                citation.pop("excerpt_hash", None)
    return projected


class AdaptiveTraceCoordinator:
    """Small V3-A1 observer shared by Agent and Workflow graph runs.

    The coordinator records only normalized, redacted contracts.  It does not
    replace the existing Evidence Gate or decide a business outcome.
    """

    def __init__(
        self,
        *,
        trusted: TrustedToolContext,
        budget: ToolBudget,
        events: EventStore,
        session_factory: SessionFactory,
        customer_message: str,
        selector_kind: SelectorKind,
        prompt_policy_version: str | None,
        case_fact_snapshot: dict[str, Any] | None = None,
        enforce_early_stop: bool = True,
        enforce_exact_retry: bool = False,
    ) -> None:
        self.trusted = trusted
        self.budget = budget
        self.events = events
        self.customer_message = customer_message
        self.selector_kind = selector_kind
        self.prompt_policy_version = prompt_policy_version
        self.case_fact_snapshot = case_fact_snapshot
        self.enforce_early_stop = enforce_early_stop
        self.enforce_exact_retry = enforce_exact_retry
        self.reducer = EvidenceProgressReducer()
        self.records: list[dict[str, Any]] = []
        self._evidence_refs: list[EvidenceRef] = []
        self._latest_results: dict[str, ToolResult[Any]] = {}
        with session_factory() as session:
            persisted_rows = Repository(session).list_tool_calls(run_id=trusted.run_id)
        for row in persisted_rows:
            if row.result_envelope is None or row.execution_status is None:
                continue
            persisted_result = ToolResult[Any].model_validate(row.result_envelope)
            self.records.append(
                {
                    "tool_call_id": row.tool_call_id,
                    "tool_name": row.tool_name,
                    "normalized_args": dict(row.normalized_args),
                    "attempt_number": row.attempt_number,
                    "actual_execution": row.actual_execution,
                    "cache_hit": row.cache_hit,
                    "blocked": row.blocked,
                    "execution_status": row.execution_status,
                    "evidence_availability": row.evidence_availability,
                    "result_envelope": dict(row.result_envelope),
                    "result_hash": row.result_hash,
                    "source_version": row.source_version,
                    "retryable": row.retryable,
                    "requested_at": row.requested_at.isoformat(),
                    "planning_turn": row.planning_turn,
                }
            )
            self._latest_results[row.tool_name] = persisted_result
        self._evidence_refs = [
            EvidenceRef.model_validate(item)
            for item in events.list_evidence_refs(
                trusted.conversation_id,
                case_id=trusted.case_id,
                run_id=trusted.run_id,
            )
        ]
        self.persisted_gate_payload: dict[str, Any] | None = None
        for event in events.list_after(trusted.conversation_id):
            if event.run_id == trusted.run_id and event.event_type == "evidence_gate_evaluated":
                self.persisted_gate_payload = dict(event.payload)
        self.progress = self.reducer.rebuild(
            case_id=trusted.case_id,
            run_id=trusted.run_id,
            canonical_issue_type=trusted.canonical_issue_type,
            tool_calls=self.records,
            evidence_refs=self._evidence_refs,
        )
        self.validator = ObservationValidator()
        self.router = ObservationRouter()
        self.guard = GuardController()
        self.prior_fingerprints: list[str] = []
        self.trace_sequence = 0
        self._selector_progress_hash = self.progress.snapshot_hash
        self._selector_input_progress_hash = self.progress.snapshot_hash
        self._terminal_reason: str | None = None
        self._early_stop_reason: str | None = None
        self._issue_revision_terminal = False
        self.pending_retry: RetryDirective | None = None
        self.source_revision: Any | None = None
        if self.records:
            last = self.records[-1]
            if (
                last.get("actual_execution") is True
                and last.get("attempt_number") == 1
                and last.get("execution_status") == ExecutionStatus.RETRYABLE_ERROR.value
                and last.get("evidence_availability") == EvidenceAvailability.UNAVAILABLE.value
                and last.get("retryable") is True
                and last.get("source_version") is not None
            ):
                self.pending_retry = issue_exact_retry(
                    tool_call_id=str(last["tool_call_id"]),
                    tool_name=str(last["tool_name"]),
                    canonical_arguments=dict(last["normalized_args"]),
                    source_version=str(last["source_version"]),
                    execution_status=ExecutionStatus.RETRYABLE_ERROR,
                    evidence_availability=EvidenceAvailability.UNAVAILABLE,
                    retryable=True,
                    attempt_number=1,
                    progress_hash=self.progress.snapshot_hash,
                )
                self.progress = self.reducer.rebuild(
                    case_id=trusted.case_id,
                    run_id=trusted.run_id,
                    canonical_issue_type=trusted.canonical_issue_type,
                    tool_calls=self.records,
                    evidence_refs=self._evidence_refs,
                    pending_retry=self.pending_retry,
                )

    def context(self) -> DecisionContext:
        return build_decision_context(
            trusted=self.trusted,
            customer_message=self.customer_message,
            progress=self.progress,
            budget=self.budget.snapshot,
            prior_decision_fingerprints=self.prior_fingerprints,
            prompt_policy_version=self.prompt_policy_version,
            case_fact_snapshot=self.case_fact_snapshot,
        )

    async def before_selector(self, _: int) -> dict[str, Any]:
        self._selector_input_progress_hash = self.progress.snapshot_hash
        if self.pending_retry is not None:
            directive = self.pending_retry
            current_source = (
                self.source_revision(directive.tool_name)
                if self.source_revision is not None
                else directive.source_version
            )
            if current_source != directive.source_version:
                self.pending_retry = None
                self._terminal_reason = (
                    RecoveryReasonCode.SOURCE_REVISION_CHANGED_DURING_RETRY.value
                )
            elif (
                self.budget.snapshot.actual_read_tool_executions
                < ToolBudget.max_actual_read_tool_executions
            ):
                return {
                    "response": AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": f"retry_{uuid4().hex}",
                                "name": directive.tool_name,
                                "args": dict(directive.canonical_arguments),
                                "type": "tool_call",
                            }
                        ],
                    )
                }
        if self._terminal_reason is not None:
            await self._state_trace(
                phase_from=TracePhase.ROUTE,
                phase_to=TracePhase.SAFE_STOP,
                reason_code=self._terminal_reason,
            )
            return {"terminal": True, "message": "调查已触发确定性安全终止。"}
        if self.enforce_early_stop and self._early_stop_reason is not None:
            await self._state_trace(
                phase_from=TracePhase.ROUTE,
                phase_to=TracePhase.FINALIZE,
                reason_code=self._early_stop_reason,
            )
            return {"terminal": True, "message": "确定性证据已足以结束观察。"}
        if self.enforce_early_stop and self.progress.gate_readiness is GateReadiness.EVALUABLE:
            await self._state_trace(
                phase_from=TracePhase.ROUTE,
                phase_to=TracePhase.FINALIZE,
                reason_code=RecoveryReasonCode.GATE_READY.value,
            )
            return {"terminal": True, "message": "确定性证据已完整，调查安全结束。"}
        return {}

    async def select_observation(self, selector: Any, planning_turn: int) -> dict[str, Any]:
        """Run the sole typed selector boundary and rebuild a trusted ToolCall.

        A provider ``AIMessage`` is parsed by the selector adapter into a
        Candidate.  Only the project validator's ``NextObservation`` can be
        converted back into the server-owned message consumed by ToolNode.
        """

        try:
            candidate: NextObservationCandidate | dict[str, Any] = (
                await select_next_observation(selector, self.context())
            )
            candidate_payload = (
                candidate.model_dump(mode="python")
                if isinstance(candidate, NextObservationCandidate)
                else dict(candidate)
            )
        except Exception:
            candidate = {}
            candidate_payload = {}
        tool_name = (
            candidate.tool_name if isinstance(candidate, NextObservationCandidate) else None
        )
        tool_calls = [candidate_payload] if tool_name is not None else []
        result = self.validator.validate(
            candidate,
            context=self.context(),
            selector_kind=self.selector_kind,
            trusted=self.trusted,
            pending_retry=self.pending_retry if self.enforce_exact_retry else None,
            gate_ready=(
                True if not tool_calls and self._issue_revision_terminal else None
            ),
        )
        candidate_action = (
            candidate.action
            if isinstance(candidate, NextObservationCandidate)
            else ObservationAction.CALL_TOOL if tool_calls else ObservationAction.FINISH
        )
        candidate_tool_name = (
            candidate.tool_name
            if isinstance(candidate, NextObservationCandidate)
            else str(candidate_payload.get("tool_name", "")) or None
        )
        candidate_arguments = (
            candidate.arguments
            if isinstance(candidate, NextObservationCandidate)
            else dict(candidate_payload.get("arguments", {}))
            if isinstance(candidate_payload.get("arguments", {}), dict)
            else {}
        )
        candidate_addresses = (
            candidate.addresses
            if isinstance(candidate, NextObservationCandidate)
            else ()
        )
        candidate_reason_code = (
            candidate.reason_code.value
            if isinstance(candidate, NextObservationCandidate)
            else str(candidate_payload.get("reason_code", "INVALID_CANDIDATE_SCHEMA"))
        )
        decision_id = (
            result.observation.decision_id
            if result.observation
            else f"rejected-{planning_turn}-{self.trace_sequence + 1}"
        )
        self.trace_sequence += 1
        trace = DecisionTraceRecord(
            trace_sequence=self.trace_sequence,
            case_id=self.trusted.case_id,
            run_id=self.trusted.run_id,
            decision_id=decision_id,
            selector_kind=self.selector_kind,
            planning_turn=planning_turn,
            action=candidate_action,
            tool_name=candidate_tool_name,
            canonical_arguments_hash=canonical_arguments_hash(candidate_arguments),
            addresses=candidate_addresses,
            reason_code=(result.rejection_code or candidate_reason_code),
            validation_status=result.status,
            rejection_code=result.rejection_code,
            evidence_progress_revision=self.progress.revision,
            evidence_progress_hash=self.progress.snapshot_hash,
            budget_snapshot=build_decision_context(
                trusted=self.trusted,
                customer_message="",
                progress=self.progress,
                budget=self.budget.snapshot,
            ).remaining_budget,
            model_id=(
                self.selector_kind.value if self.selector_kind is SelectorKind.AGENT else None
            ),
            prompt_policy_version=self.prompt_policy_version,
            recorded_at=datetime.now(UTC),
        )
        await self._decision_trace(trace)
        if result.observation is not None:
            self.prior_fingerprints.append(result.observation.decision_fingerprint)
            if result.observation.action is ObservationAction.FINISH:
                await self._state_trace(
                    phase_from=TracePhase.VALIDATE,
                    phase_to=TracePhase.FINALIZE,
                    reason_code=RecoveryReasonCode.GATE_READY.value,
                )
                return {"response": AIMessage(content="必要的只读观察已经完成。"), "terminal": True}
            await self._state_trace(
                phase_from=TracePhase.VALIDATE,
                phase_to=TracePhase.EXECUTE,
                reason_code="OBSERVATION_ACCEPTED",
            )
            observation = result.observation
            return {
                "response": AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": f"obs_{observation.decision_id}",
                            "name": str(observation.tool_name),
                            "args": dict(observation.canonical_arguments),
                            "type": "tool_call",
                        }
                    ],
                )
            }
        no_progress_reason = self.guard.observe_selector_turn(
            self._selector_input_progress_hash,
            self.progress.snapshot_hash,
        )
        if no_progress_reason is not None:
            self._terminal_reason = no_progress_reason.value
        if (
            result.rejection_code
            in {"PREMATURE_FINISH", "STUCK_REPEATED_DECISION", "INVALID_CANDIDATE_SCHEMA"}
            and self.guard.allow_correction()
        ):
            correction = AIMessage(content="请依据当前缺失证据选择一个允许的只读观察。")
            return {"response": correction, "force_replan": True}
        await self._state_trace(
            phase_from=TracePhase.VALIDATE,
            phase_to=TracePhase.SAFE_STOP,
            reason_code=(
                self._terminal_reason
                or result.rejection_code
                or RecoveryReasonCode.INVALID_OBSERVATION.value
            ),
        )
        return {
            "response": AIMessage(content="观察选择未通过确定性校验，已安全停止。"),
            "terminal": True,
        }

    async def on_tool_result(
        self,
        record: dict[str, Any],
        result: ToolResult[Any],
        refs: list[EvidenceRef],
    ) -> None:
        self.records.append(record)
        self._latest_results[str(record["tool_name"])] = result
        all_refs: list[EvidenceRef] = [*self._evidence_refs, *refs]
        self._evidence_refs = all_refs
        before_hash = self.progress.snapshot_hash
        pending_before = self.pending_retry
        if (
            self.enforce_exact_retry
            and result.execution_status is ExecutionStatus.RETRYABLE_ERROR
            and result.evidence_availability is EvidenceAvailability.UNAVAILABLE
            and result.retryable
            and int(record.get("attempt_number", 1)) == 1
        ):
            self.pending_retry = issue_exact_retry(
                tool_call_id=str(record["tool_call_id"]),
                tool_name=str(record["tool_name"]),
                canonical_arguments=record["normalized_args"],
                source_version=str(record["source_version"]),
                execution_status=result.execution_status,
                evidence_availability=result.evidence_availability,
                retryable=result.retryable,
                attempt_number=int(record["attempt_number"]),
                progress_hash=before_hash,
            )
        elif (
            self.pending_retry is not None
            and int(record.get("attempt_number", 1)) >= self.pending_retry.next_attempt_number
        ):
            # Attempt two is the sole retry allowed by the directive.  Once
            # it completes (successfully or not), remove the lock before the
            # reducer and router inspect the terminal result.
            self.pending_retry = None
        elif result.execution_status is not ExecutionStatus.RETRYABLE_ERROR:
            self.pending_retry = None
        self.progress = self.reducer.rebuild(
            case_id=self.trusted.case_id,
            run_id=self.trusted.run_id,
            canonical_issue_type=self.trusted.canonical_issue_type,
            tool_calls=self.records,
            evidence_refs=all_refs,
            pending_retry=self.pending_retry,
        )
        self._selector_progress_hash = self.progress.snapshot_hash
        if (
            record.get("tool_name") == "get_order_context"
            and self.trusted.canonical_issue_type is IssueType.SIGNED_NOT_RECEIVED
            and result.payload is not None
            and result.payload.order_status is not None
            and result.payload.order_status.value != "delivered"
        ):
            # A reported signed-not-received issue on a non-delivered order is
            # a deterministic issue-revision terminal branch.  The Gate, not
            # this coordinator, decides the revised business outcome.
            self._issue_revision_terminal = True
            self._early_stop_reason = "ISSUE_REVISION_READY"
        if record.get("tool_name") == "get_existing_logistics_tickets":
            active_tickets = getattr(result.payload, "active_tickets", ())
            if active_tickets:
                self._early_stop_reason = "ACTIVE_TICKET_EXISTS"
        if record.get("tool_name") == "search_after_sales_policy":
            timeline_result = self._latest_results.get("get_logistics_timeline")
            policy_payload = result.payload
            timeline_payload = timeline_result.payload if timeline_result is not None else None
            policy_facts = getattr(policy_payload, "policy_fact_snapshot", None)
            hours = getattr(timeline_payload, "hours_since_last_update", None)
            threshold = getattr(policy_facts, "stalled_after_hours", None)
            if (
                self.trusted.canonical_issue_type is IssueType.STALLED_TRACKING
                and isinstance(hours, (int, float))
                and isinstance(threshold, (int, float))
                and hours <= threshold
            ):
                self._early_stop_reason = "WITHIN_TRACKING_SLA"
        no_progress_reason = self.guard.observe_selector_turn(
            self._selector_input_progress_hash,
            self.progress.snapshot_hash,
        )
        if no_progress_reason is not None:
            self._terminal_reason = no_progress_reason.value
        record["progress_hash"] = self.progress.snapshot_hash
        record["progress_revision"] = self.progress.revision
        record["trace_sequence"] = self.trace_sequence + 1
        await self._state_trace(
            phase_from=TracePhase.REDUCE,
            phase_to=TracePhase.ROUTE,
            reason_code="EVIDENCE_PROGRESS_REBUILT",
        )
        self.trace_sequence += 1
        recovery = self.router.route(
            case_id=self.trusted.case_id,
            run_id=self.trusted.run_id,
            progress_before=self.reducer.rebuild(
                case_id=self.trusted.case_id,
                run_id=self.trusted.run_id,
                canonical_issue_type=self.trusted.canonical_issue_type,
                tool_calls=self.records[:-1],
                evidence_refs=self._evidence_refs[:-len(refs)] if refs else self._evidence_refs,
                pending_retry=pending_before,
            ),
            progress_after=self.progress,
            budget=BudgetSnapshot.from_tool_budget(self.budget.snapshot),
            trigger_tool_call_id=str(record["tool_call_id"]),
            trigger_result_hash=result.result_hash,
            trigger_tool_name=str(record["tool_name"]),
            trigger_arguments=record["normalized_args"],
            trigger_attempt_number=int(record["attempt_number"]),
            result=result,
            pending_retry=self.pending_retry,
            source_version=str(
                record.get("current_source_version", record["source_version"])
            ),
            same_progress_selector_turns=self.guard.state.unchanged_selector_turns,
        )
        if recovery.route.value == "safe_stop" and (
            recovery.reason_code is not RecoveryReasonCode.BUDGET_EXHAUSTED
            or self.enforce_exact_retry
        ):
            self._terminal_reason = recovery.reason_code.value
        elif recovery.route.value == "finalize" and self.enforce_early_stop:
            self._terminal_reason = recovery.reason_code.value
        retry_identity_hash = (
            self.pending_retry.canonical_arguments_hash if self.pending_retry is not None else None
        )
        recovery_trace = RecoveryTraceRecord(
            trace_sequence=self.trace_sequence,
            case_id=self.trusted.case_id,
            run_id=self.trusted.run_id,
            recovery_id=recovery.recovery_id,
            trigger_tool_call_id=recovery.trigger_tool_call_id,
            trigger_result_hash=recovery.trigger_result_hash,
            execution_status=result.execution_status.value,
            evidence_availability=result.evidence_availability.value,
            retryable=result.retryable,
            error_code=result.error_code,
            evidence_progress_before_hash=recovery.evidence_progress_before_hash,
            evidence_progress_after_hash=recovery.evidence_progress_after_hash,
            route=recovery.route,
            reason_code=recovery.reason_code,
            retry_identity_hash=retry_identity_hash,
            attempt_number=int(record["attempt_number"]),
            budget_snapshot=recovery.budget_snapshot,
            recorded_at=datetime.now(UTC),
        )
        await self.events.append(
            EventDraft(
                conversation_id=self.trusted.conversation_id,
                case_id=self.trusted.case_id,
                run_id=self.trusted.run_id,
                event_type="recovery_trace_record",
                visibility=EventVisibility.DEVELOPER,
                summary="Adaptive recovery route recorded",
                payload=recovery_trace.model_dump(mode="json"),
            )
        )

    async def _decision_trace(self, record: DecisionTraceRecord) -> None:
        await self.events.append(
            EventDraft(
                conversation_id=self.trusted.conversation_id,
                case_id=self.trusted.case_id,
                run_id=self.trusted.run_id,
                event_type="decision_trace_record",
                visibility=EventVisibility.DEVELOPER,
                summary="Adaptive observation decision recorded",
                payload=record.model_dump(mode="json"),
            )
        )

    async def _state_trace(
        self,
        *,
        phase_from: TracePhase,
        phase_to: TracePhase,
        reason_code: str,
    ) -> None:
        self.trace_sequence += 1
        record = StateTraceRecord(
            trace_sequence=self.trace_sequence,
            case_id=self.trusted.case_id,
            run_id=self.trusted.run_id,
            phase_from=phase_from,
            phase_to=phase_to,
            reason_code=reason_code,
            evidence_progress_hash=self.progress.snapshot_hash,
            pending_retry_identity=(
                self.pending_retry.canonical_arguments_hash
                if self.pending_retry is not None
                else None
            ),
            persisted_at=datetime.now(UTC),
        )
        await self.events.append(
            EventDraft(
                conversation_id=self.trusted.conversation_id,
                case_id=self.trusted.case_id,
                run_id=self.trusted.run_id,
                event_type="state_trace_record",
                visibility=EventVisibility.DEVELOPER,
                summary="Adaptive state transition recorded",
                payload=record.model_dump(mode="json"),
            )
        )


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
        on_result: Any | None = None,
        auto_exact_retry: bool = False,
    ) -> None:
        self.executor = executor
        self._session_factory = session_factory
        self._events = events
        self._trusted = trusted
        self._pacer = pacer
        self._requester_label = requester_label
        self._on_result = on_result
        self._auto_exact_retry = auto_exact_retry
        self.results: dict[str, ToolResult[Any]] = {}
        self.evidence_refs: list[EvidenceRef] = []
        self.arguments_by_tool: dict[str, dict[str, Any]] = {}
        self._tool_attempts: dict[str, int] = {}
        self.records: list[dict[str, Any]] = []

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._execute(tool_name, arguments, None, model_visible=False)

    async def execute_with_call_id(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        tool_call_id: str | None,
    ) -> dict[str, Any]:
        return await self._execute(tool_name, arguments, tool_call_id, model_visible=True)

    async def _execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        tool_call_id: str | None,
        *,
        model_visible: bool,
    ) -> dict[str, Any]:
        call_id = tool_call_id or f"call_{uuid4().hex}"
        if self._requester_label != "Agent":
            await self._events.append(
                EventDraft(
                    conversation_id=self._trusted.conversation_id,
                    case_id=self._trusted.case_id,
                    run_id=self._trusted.run_id,
                    event_type="workflow_step_started",
                    visibility=EventVisibility.DEVELOPER,
                    summary=f"{self._requester_label} selected {tool_name}",
                    payload={"tool_name": tool_name},
                )
            )
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
        source_version = self.executor.catalog.source_revision(
            self._trusted.authorized_order_id,
            tool_name,
        )
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
                source_version=source_version,
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
            "tool_call_id": call_id,
            "tool_name": tool_name,
            "attempt_number": attempt,
            "planning_turn": max(after.run_planning_turns, 1),
            "budget_before_actual_reads": before.actual_read_tool_executions,
            "budget_after_actual_reads": after.actual_read_tool_executions,
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
        record = {
            "tool_call_id": call_id,
            "tool_name": tool_name,
            "normalized_args": dict(arguments),
            "attempt_number": attempt,
            "actual_execution": actual_execution,
            "cache_hit": cache_hit,
            "blocked": blocked,
            "execution_status": result.execution_status.value,
            "evidence_availability": result.evidence_availability.value,
            "result_envelope": result.model_dump(mode="json"),
            "result_hash": result.result_hash,
            "source_version": source_version,
            "planning_turn": max(after.run_planning_turns, 1),
            "budget_before_actual_reads": before.actual_read_tool_executions,
            "budget_after_actual_reads": after.actual_read_tool_executions,
            "requested_at": datetime.now(UTC).isoformat(),
        }
        self.records.append(record)
        if self._on_result is not None:
            await self._on_result(record, result, refs)
        if (
            self._auto_exact_retry
            and actual_execution
            and attempt == 1
            and result.execution_status is ExecutionStatus.RETRYABLE_ERROR
            and result.evidence_availability is EvidenceAvailability.UNAVAILABLE
            and result.retryable
        ):
            # Exact retry is adjacent to the failed attempt: no selector/model
            # callback is invoked and the canonical arguments/source revision
            # are re-used verbatim.  A separate durable ToolCall identity keeps
            # both attempts auditable while the model sees the final result.
            retry_call_id = f"{call_id}:retry"
            # Re-read immediately before attempt two.  Trace/event callbacks
            # may interleave with a fixture or policy revision change, so a
            # value captured before them is stale by construction.
            current_source = self.executor.catalog.source_revision(
                self._trusted.authorized_order_id,
                tool_name,
            )
            if (
                current_source == record["source_version"]
                and self.executor.budget.snapshot.actual_read_tool_executions
                < ToolBudget.max_actual_read_tool_executions
            ):
                return await self._execute(
                    tool_name,
                    dict(arguments),
                    retry_call_id,
                    model_visible=model_visible,
                )
        self.results[tool_name] = result
        self.arguments_by_tool[tool_name] = dict(arguments)
        self.evidence_refs.extend(refs)
        return (
            _model_visible_tool_result(tool_name, result)
            if model_visible
            else result.model_dump(mode="json")
        )


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
        case_fact_snapshot: dict[str, Any] | None = None,
        selector_kind: SelectorKind = SelectorKind.AGENT,
        selector_model: Any | None = None,
        requester_label: str = "Agent",
        auto_exact_retry: bool = True,
        enforce_early_stop: bool = True,
    ) -> InvestigationOutput:
        with self._session_factory() as session:
            case_history = Repository(session).list_tool_calls(case_id=trusted.case_id)
            run_history = Repository(session).list_tool_calls(run_id=trusted.run_id)
        case_read_executions = max(
            case_read_executions,
            sum(1 for row in case_history if row.actual_execution),
        )
        case_planning_turns = max(
            case_planning_turns,
            max((row.planning_turn for row in case_history), default=0),
        )
        run_planning_turns = max(
            run_planning_turns,
            max((row.planning_turn for row in run_history), default=0),
        )
        budget = ToolBudget(
            case_planning_turns=case_planning_turns,
            run_planning_turns=run_planning_turns,
            actual_read_tool_executions=case_read_executions,
        )
        adaptive = AdaptiveTraceCoordinator(
            trusted=trusted,
            budget=budget,
            events=self._events,
            session_factory=self._session_factory,
            customer_message=customer_message,
            selector_kind=selector_kind,
            prompt_policy_version=INVESTIGATION_PROMPT_VERSION,
            case_fact_snapshot=case_fact_snapshot,
            enforce_early_stop=enforce_early_stop,
            enforce_exact_retry=auto_exact_retry,
        )
        # An empty CaseToolCache is intentionally shared by the application
        # composition root.  Its ``__len__`` makes it falsey, so ``or`` would
        # silently replace it and break cross-Run evidence reuse.
        runtime_cache = tool_cache if tool_cache is not None else CaseToolCache()
        if tool_cache is None:
            catalog_for_rebuild = SyntheticReadToolCatalog(self._fixtures, self._policy_rag)
            for row in case_history:
                if not row.actual_execution or row.result_envelope is None:
                    continue
                order_id = str(row.normalized_args.get("order_id", ""))
                if not order_id or row.source_version is None:
                    continue
                current_revision = catalog_for_rebuild.source_revision(order_id, row.tool_name)
                key = ToolCacheKey(
                    case_id=trusted.case_id,
                    tool_name=row.tool_name,
                    normalized_args=normalize_tool_arguments(row.normalized_args),
                    source_revision=current_revision,
                )
                runtime_cache.record_actual_attempt(key)
                if row.source_version == current_revision:
                    runtime_cache.store(
                        key,
                        ToolResult[Any].model_validate(row.result_envelope),
                    )
        governed = GovernedToolExecutor(
            trusted=trusted,
            catalog=SyntheticReadToolCatalog(self._fixtures, self._policy_rag),
            cache=runtime_cache,
            budget=budget,
        )
        adaptive.source_revision = lambda tool_name: governed.catalog.source_revision(
            trusted.authorized_order_id,
            tool_name,
        )
        tracing = TracingToolExecutor(
            executor=governed,
            session_factory=self._session_factory,
            events=self._events,
            trusted=trusted,
            pacer=self._pacer,
            requester_label=requester_label,
            on_result=adaptive.on_tool_result,
            # Exact retry is emitted by ``before_selector`` so it is adjacent,
            # survives restart, and never consumes a selector/planning turn.
            auto_exact_retry=False,
        )
        tracing.results.update(adaptive._latest_results)
        tracing.evidence_refs.extend(adaptive._evidence_refs)
        tracing.records.extend(adaptive.records)
        persisted_record_count = len(adaptive.records)
        for record in adaptive.records:
            tool_name = str(record["tool_name"])
            tracing.arguments_by_tool[tool_name] = dict(record["normalized_args"])
            tracing._tool_attempts[tool_name] = max(
                tracing._tool_attempts.get(tool_name, 0),
                int(record["attempt_number"]),
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
                    summary=f"{requester_label} planning turn {turn}",
                    payload={"planning_turn": governed.budget.snapshot.run_planning_turns},
                )
            )

        selector_runtime = (
            WorkflowObservationSelector()
            if selector_kind is SelectorKind.WORKFLOW
            else AgentObservationSelector(
                selector_model or build_investigation_model(self._settings, READ_TOOLS)
            )
        )

        async def select_observation_runtime(turn: int) -> dict[str, Any]:
            return await adaptive.select_observation(selector_runtime, turn)

        runtime = InvestigationRuntimeContext(
            trusted=trusted,
            tool_executor=tracing,
            # Retained only for legacy direct-graph tests. Production selection
            # always enters through ``select_observation`` below.
            model=selector_model or build_investigation_model(self._settings, READ_TOOLS),
            on_agent_turn=on_agent_turn,
            before_selector=adaptive.before_selector,
            select_observation=select_observation_runtime,
            selector_kind=selector_kind.value,
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
                "force_replan": False,
                "terminal": False,
            },
            config={
                "configurable": {
                    "thread_id": f"{trusted.case_id}:{trusted.run_id}:pass-{investigation_pass}"
                }
            },
            context=runtime,
        )
        snapshot = budget.snapshot
        budget_reached = (
            auto_exact_retry
            and snapshot.actual_read_tool_executions
            >= ToolBudget.max_actual_read_tool_executions
        )
        budget_exhausted = bool(graph_result.get("budget_exhausted", False)) or budget_reached
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
        gate_budget_exceeded = bool(graph_result.get("budget_exhausted", False)) or (
            budget_reached
            and not critical_retry_exhausted
            and adaptive.progress.gate_readiness is not GateReadiness.EVALUABLE
        )
        if (
            adaptive.persisted_gate_payload is not None
            and len(tracing.records) == persisted_record_count
        ):
            payload = adaptive.persisted_gate_payload
            gate = EvidenceGateResult(
                decision=(
                    EvidenceGateDecision(str(payload["decision"]))
                    if payload.get("decision") is not None
                    else None
                ),
                reason_code=str(payload["reason_code"]),
                revised_issue_type=(
                    TriageIntent(str(payload["revised_issue_type"]))
                    if payload.get("revised_issue_type") is not None
                    else None
                ),
                critical_result_hashes=dict(payload.get("critical_result_hashes", {})),
            )
        else:
            gate = self._evaluate_gate(
                trusted.canonical_issue_type,
                tracing.results,
                critical_retry_exhausted=critical_retry_exhausted,
                budget_exceeded=gate_budget_exceeded,
                customer_still_reports_missing=customer_still_reports_missing,
                reception_locations_checked=reception_locations_checked,
            )
            await adaptive._state_trace(
                phase_from=TracePhase.ROUTE,
                phase_to=TracePhase.TERMINAL,
                reason_code="EVIDENCE_GATE_EVALUATED",
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
                        "critical_result_hashes": dict(gate.critical_result_hashes),
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
            strategy=("workflow" if selector_kind is SelectorKind.WORKFLOW else "agent"),
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
        # A completed active-ticket observation is itself sufficient to prevent
        # a duplicate action; no unrelated evidence reads are required.
        existing = results.get("get_existing_logistics_tickets")
        if (
            existing is not None
            and existing.execution_status is ExecutionStatus.SUCCESS
            and existing.payload is not None
            and getattr(existing.payload, "active_tickets", [])
        ):
            return EvidenceGateResult(
                decision=EvidenceGateDecision.COMPLETE_NO_ACTION,
                reason_code="ACTIVE_LOGISTICS_TICKET_EXISTS",
                critical_result_hashes={"existing_tickets": existing.result_hash},
            )
        required = set(required_observation_tools(issue_type))
        if (
            issue_type is IssueType.STALLED_TRACKING
            and "get_existing_logistics_tickets" not in results
            and required - {"get_existing_logistics_tickets"} <= set(results)
        ):
            # The stalled SLA gate deliberately evaluates before ticket status
            # once order/timeline/policy facts establish a within-SLA path.
            # Keep the missing observation explicit and non-authoritative; an
            # overdue path still requires the real ticket read below.
            results = {
                **results,
                "get_existing_logistics_tickets": ToolResult[Any].failed(
                    retryable=True,
                    source_type="get_existing_logistics_tickets",
                    source_query_id="not-queried-stalled-sla",
                    observed_at=order.observed_at,
                    error_code="NOT_QUERIED_NOT_REQUIRED",
                ),
            }
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

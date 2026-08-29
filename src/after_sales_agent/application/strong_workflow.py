"""Strong deterministic selector over the shared V3-A1 runtime.

The Workflow deliberately owns only ``select_next_observation``.  Tool
validation, LangGraph ``ToolNode``, governed execution, progress reduction,
recovery, tracing, and the Evidence Gate are composed by
``InvestigationService`` exactly as for the Agent strategy.
"""

from __future__ import annotations

from typing import Any

from after_sales_agent.agents.models import WorkflowInvestigationModel
from after_sales_agent.application.adaptive_core import SelectorKind
from after_sales_agent.application.investigation import InvestigationOutput, InvestigationService
from after_sales_agent.application.pacing import MockDemoPacer
from after_sales_agent.config import Settings
from after_sales_agent.domain.models import TrustedToolContext
from after_sales_agent.events.store import EventStore
from after_sales_agent.fixtures.catalog import FixtureStore
from after_sales_agent.policy.rag import PolicyRagService
from after_sales_agent.storage.database import SessionFactory
from after_sales_agent.tools.cache import CaseToolCache


class StrongWorkflowInvestigationService:
    """Deterministic next-observation selector using the shared graph path."""

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
        # Preserve the composition-root inspection surface used by V2 tests;
        # execution itself is delegated to the same InvestigationService.
        self._policy_rag = policy_rag
        self._fixtures = fixtures
        self._service = InvestigationService(
            settings=settings,
            fixtures=fixtures,
            session_factory=session_factory,
            events=events,
            policy_rag=policy_rag,
            graph_checkpointer=graph_checkpointer,
            pacer=pacer,
        )

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
        delivery_location_conflict: bool = False,
        case_fact_snapshot: dict[str, Any] | None = None,
        auto_exact_retry: bool = True,
        enforce_early_stop: bool = True,
    ) -> InvestigationOutput:
        # The model receives the same trusted scope markers and tool-result
        # messages as the Agent model; it has no Fixture or backend handle.
        from after_sales_agent.agents.tool_bindings import READ_TOOLS

        selector = WorkflowInvestigationModel(READ_TOOLS)
        return await self._service.investigate(
            trusted=trusted,
            customer_message=customer_message,
            case_planning_turns=case_planning_turns,
            run_planning_turns=run_planning_turns,
            case_read_executions=case_read_executions,
            tool_cache=tool_cache,
            investigation_pass=investigation_pass,
            customer_still_reports_missing=customer_still_reports_missing,
            reception_locations_checked=reception_locations_checked,
            delivery_location_conflict=delivery_location_conflict,
            case_fact_snapshot=case_fact_snapshot,
            selector_kind=SelectorKind.WORKFLOW,
            selector_model=selector,
            requester_label="Strong Workflow",
            auto_exact_retry=auto_exact_retry,
            enforce_early_stop=enforce_early_stop,
        )


__all__ = ["StrongWorkflowInvestigationService"]

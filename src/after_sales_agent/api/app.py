"""FastAPI composition root and local HTTP/SSE product surface."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from sqlalchemy import text

from after_sales_agent.application.service import AfterSalesApplication
from after_sales_agent.config import Settings, get_settings
from after_sales_agent.domain.state import ActionState, ExecutionStatus, PolicyResolutionStatus
from after_sales_agent.evals.contracts import EvalReport
from after_sales_agent.evals.store import EvalArtifactStore
from after_sales_agent.events.models import EventEnvelope
from after_sales_agent.events.store import EventStore
from after_sales_agent.fixtures.catalog import (
    ActionFixtureFault,
    FixtureFault,
    FixtureStore,
    default_fixture_store,
)
from after_sales_agent.policy.corpus import build_business_demo_policy_corpus
from after_sales_agent.policy.rag import PolicyRagService, build_policy_rag
from after_sales_agent.storage.database import Database, create_engine_and_session, init_database
from after_sales_agent.storage.repositories import Repository

from .errors import ApiSurfaceError, error_response, install_error_handlers
from .schemas import (
    CaseRead,
    ConversationCreated,
    ConversationRead,
    CreateConversationRequest,
    CustomerMessageRequest,
    DemoCatalogRead,
    HealthResponse,
    ProposalTransitionAccepted,
    ProposalVersionRequest,
    ReadinessResponse,
    RetryCaseRequest,
    RunAccepted,
    RunRead,
)


@dataclass(frozen=True, slots=True)
class ApiRuntime:
    settings: Settings
    database: Database
    fixtures: FixtureStore
    policy_rag: PolicyRagService
    events: EventStore
    checkpointer: AsyncSqliteSaver
    application: AfterSalesApplication
    eval_store: EvalArtifactStore


SettingsOverride = Settings | Mapping[str, Any] | None


def _resolve_settings(override: SettingsOverride) -> Settings:
    if isinstance(override, Settings):
        return override
    if override is not None:
        return Settings(_env_file=None, **dict(override))
    return get_settings()


def _runtime(request: Request) -> ApiRuntime:
    runtime = getattr(request.app.state, "runtime", None)
    if not isinstance(runtime, ApiRuntime):
        raise ApiSurfaceError(
            code="SERVICE_NOT_READY",
            message="服务尚未完成本地初始化。",
            status_code=503,
            retryable=True,
        )
    return runtime


RuntimeDependency = Annotated[ApiRuntime, Depends(_runtime)]


def _sse_event(event: EventEnvelope) -> str:
    data = json.dumps(
        event.to_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"id: {event.event_id}\nevent: {event.event_type}\ndata: {data}\n\n"


def create_app(settings_override: SettingsOverride = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = _resolve_settings(settings_override)
        settings.ensure_local_directories()
        database = create_engine_and_session(settings.database_url)
        init_database(database.engine)
        fixtures = default_fixture_store()
        policy_rag = build_policy_rag(
            settings,
            corpus=build_business_demo_policy_corpus(fixtures.business_policy_clauses),
        )
        if settings.synthetic_fault_profile == "pod_timeout_once":
            fixtures = fixtures.with_faults(
                {
                    ("demo-default", "get_delivery_proof", 1): FixtureFault(
                        execution_status=ExecutionStatus.RETRYABLE_ERROR,
                        error_code="SYNTHETIC_POD_TIMEOUT",
                    )
                }
            )
        elif settings.synthetic_fault_profile == "policy_unavailable":
            # Explicit Mock-only negative path for the browser checkpoint.  Both
            # permitted attempts fail so the Gate sees a final unavailable policy
            # observation and cannot create a Proposal.
            fixtures = fixtures.with_faults(
                {
                    ("demo-default", "search_after_sales_policy", 1): FixtureFault(
                        execution_status=ExecutionStatus.RETRYABLE_ERROR,
                        error_code="SYNTHETIC_POLICY_RETRIEVAL_UNAVAILABLE",
                    ),
                    ("demo-default", "search_after_sales_policy", 2): FixtureFault(
                        execution_status=ExecutionStatus.RETRYABLE_ERROR,
                        error_code="SYNTHETIC_POLICY_RETRIEVAL_UNAVAILABLE",
                    ),
                }
            )
        elif settings.synthetic_fault_profile == "timeline_retry":
            fixtures = fixtures.with_faults(
                {
                    ("demo-default", "get_logistics_timeline", 1): FixtureFault(
                        execution_status=ExecutionStatus.RETRYABLE_ERROR,
                        error_code="SYNTHETIC_TIMELINE_TIMEOUT",
                    )
                }
            )
        elif settings.synthetic_fault_profile == "carrier_terminal":
            fixtures = fixtures.with_faults(
                {
                    ("demo-default", "get_carrier_service_alerts", 1): FixtureFault(
                        execution_status=ExecutionStatus.NON_RETRYABLE_ERROR,
                        error_code="SYNTHETIC_CARRIER_READ_FAILED",
                    )
                }
            )
        elif settings.synthetic_fault_profile == "ticket_uncertain":
            fixtures = fixtures.with_action_faults(
                {
                    "demo-default": [
                        ActionFixtureFault(
                            action_state=ActionState.UNCERTAIN,
                            error_code="SYNTHETIC_TICKET_RESULT_UNCERTAIN",
                        )
                    ]
                }
            )
        elif settings.synthetic_fault_profile == "policy_conflict":
            fixtures = fixtures.with_policy_resolution_override(
                PolicyResolutionStatus.VERSION_CONFLICT
            )
        if fixtures.fixture_version != settings.fixture_version:
            database.engine.dispose()
            raise RuntimeError("configured fixture version does not match source fixtures")

        checkpoint_path = str(settings.langgraph_checkpoint_url)
        async with AsyncSqliteSaver.from_conn_string(checkpoint_path) as checkpointer:
            await checkpointer.setup()
            events = EventStore(database.session_factory)
            application = AfterSalesApplication(
                settings=settings,
                fixtures=fixtures,
                session_factory=database.session_factory,
                events=events,
                policy_rag=policy_rag,
                graph_checkpointer=checkpointer,
            )
            application.load_persisted_tickets()
            eval_store = EvalArtifactStore(settings.eval_artifact_root)
            eval_store.ensure()
            app.state.runtime = ApiRuntime(
                settings=settings,
                database=database,
                fixtures=fixtures,
                policy_rag=policy_rag,
                events=events,
                checkpointer=checkpointer,
                application=application,
                eval_store=eval_store,
            )
            try:
                yield
            finally:
                app.state.runtime = None
                database.engine.dispose()

    app = FastAPI(
        title="Ecommerce After-Sales Logistics Agent",
        version="0.1.0",
        lifespan=lifespan,
    )
    install_error_handlers(app)

    @app.middleware("http")
    async def local_cors(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        origin = request.headers.get("origin")
        runtime = getattr(request.app.state, "runtime", None)
        configured_origin = (
            runtime.settings.frontend_origin
            if isinstance(runtime, ApiRuntime)
            else (
                settings_override.frontend_origin
                if isinstance(settings_override, Settings)
                else "http://127.0.0.1:5173"
            )
        )
        if request.method == "OPTIONS" and request.headers.get("access-control-request-method"):
            if origin != configured_origin:
                return error_response(
                    status_code=403,
                    code="CORS_ORIGIN_NOT_ALLOWED",
                    message="该浏览器来源不在本地演示允许列表中。",
                )
            response = Response(status_code=204)
        else:
            response = await call_next(request)
        if origin == configured_origin:
            response.headers["Access-Control-Allow-Origin"] = configured_origin
            response.headers["Vary"] = "Origin"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Accept, Content-Type, Last-Event-ID"
        return response

    @app.get("/healthz", response_model=HealthResponse)
    async def healthz() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/readyz", response_model=ReadinessResponse)
    async def readyz(runtime: RuntimeDependency) -> ReadinessResponse:
        try:
            with runtime.database.session_factory() as session:
                session.execute(text("SELECT 1")).scalar_one()
        except Exception as exc:
            raise ApiSurfaceError(
                code="BUSINESS_STORE_NOT_READY",
                message="本地业务存储尚未就绪。",
                status_code=503,
                retryable=True,
            ) from exc
        return ReadinessResponse(
            status="ready",
            llm_mode=runtime.settings.llm_mode.value,
            fixture_version=runtime.fixtures.fixture_version,
            business_store="ready",
            checkpoint_store="ready",
            provider_checked=False,
        )

    @app.post(
        "/v1/conversations",
        response_model=ConversationCreated,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_conversation(
        body: CreateConversationRequest,
        runtime: RuntimeDependency,
    ) -> dict[str, Any]:
        return runtime.application.create_conversation(body.fixture_customer_key)

    @app.post(
        "/v1/conversations/{conversation_id}/messages",
        response_model=RunAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def submit_message(
        conversation_id: str,
        body: CustomerMessageRequest,
        runtime: RuntimeDependency,
    ) -> dict[str, Any]:
        return await runtime.application.submit_message(conversation_id, body.content)

    @app.get("/v1/conversations/{conversation_id}", response_model=ConversationRead)
    async def get_conversation(
        conversation_id: str,
        runtime: RuntimeDependency,
    ) -> dict[str, Any]:
        return runtime.application.get_conversation(conversation_id)

    @app.get("/v1/investigation-cases/{case_id}", response_model=CaseRead)
    async def get_case(
        case_id: str,
        runtime: RuntimeDependency,
    ) -> dict[str, Any]:
        case = runtime.application.get_case(case_id)
        case.pop("revision", None)
        return case

    @app.get("/v1/runs/{run_id}", response_model=RunRead)
    async def get_run(
        run_id: str,
        runtime: RuntimeDependency,
    ) -> dict[str, Any]:
        run = runtime.application.get_run(run_id)
        run.pop("started_at", None)
        return run

    @app.post(
        "/v1/action-proposals/{proposal_id}/confirm",
        response_model=ProposalTransitionAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def confirm_proposal(
        proposal_id: str,
        body: ProposalVersionRequest,
        runtime: RuntimeDependency,
    ) -> dict[str, Any]:
        return await runtime.application.confirm_proposal(
            proposal_id,
            body.proposal_version,
        )

    @app.post(
        "/v1/action-proposals/{proposal_id}/decline",
        response_model=ProposalTransitionAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def decline_proposal(
        proposal_id: str,
        body: ProposalVersionRequest,
        runtime: RuntimeDependency,
    ) -> dict[str, Any]:
        return await runtime.application.decline_proposal(
            proposal_id,
            body.proposal_version,
        )

    @app.post(
        "/v1/investigation-cases/{case_id}/retry",
        response_model=RunAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def retry_case(
        case_id: str,
        _: RetryCaseRequest,
        runtime: RuntimeDependency,
    ) -> dict[str, Any]:
        return await runtime.application.retry_case(case_id)

    @app.get("/v1/conversations/{conversation_id}/events")
    async def conversation_events(
        conversation_id: str,
        request: Request,
        runtime: RuntimeDependency,
        follow: Annotated[bool, Query()] = True,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    ) -> StreamingResponse:
        # Existence and cursor checks happen before response streaming begins.
        runtime.application.get_conversation(conversation_id)
        after_sequence = 0
        if last_event_id:
            last_event = runtime.events.get(last_event_id)
            if last_event is None or last_event.conversation_id != conversation_id:
                raise ApiSurfaceError(
                    code="EVENT_CURSOR_NOT_FOUND",
                    message="事件重放游标不属于这段虚拟会话。",
                    status_code=404,
                )
            after_sequence = last_event.sequence

        async def stream_events() -> AsyncIterator[str]:
            if not follow:
                for event in runtime.events.list_after(conversation_id, after_sequence):
                    yield _sse_event(event)
                return
            async for event in runtime.events.subscribe(conversation_id, after_sequence):
                if await request.is_disconnected():
                    break
                yield _sse_event(event)

        return StreamingResponse(
            stream_events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/v1/demo/reset", status_code=status.HTTP_204_NO_CONTENT)
    async def reset_demo(runtime: RuntimeDependency) -> Response:
        with runtime.database.session_factory() as session:
            runs = Repository(session).list_runs()
            checkpoint_threads = {
                f"{run.case_id}:{run.run_id}" for run in runs if run.case_id is not None
            }
        for thread_id in checkpoint_threads:
            await runtime.checkpointer.adelete_thread(thread_id)
        runtime.application.reset_demo()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/v1/demo/catalog", response_model=DemoCatalogRead)
    async def demo_catalog(runtime: RuntimeDependency) -> dict[str, Any]:
        return {
            "fixture_version": runtime.fixtures.fixture_version,
            "policy_clause_count": len(runtime.fixtures.business_policy_clauses),
            "scenarios": [
                scenario.model_dump(mode="json")
                for scenario in runtime.fixtures.scenario_catalog
            ],
            "fault_profiles": [
                profile.model_dump(mode="json")
                for profile in runtime.fixtures.fault_profiles
            ],
        }

    @app.get("/v1/evals/latest", response_model=EvalReport)
    async def latest_eval(runtime: RuntimeDependency) -> EvalReport:
        report = runtime.eval_store.load_latest_report()
        if report is None:
            raise ApiSurfaceError(
                code="EVAL_REPORT_NOT_FOUND",
                message="尚无版本化评测报告。",
                status_code=404,
            )
        return report

    return app


app = create_app()

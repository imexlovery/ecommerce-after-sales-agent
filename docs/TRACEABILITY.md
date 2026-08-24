# Traceability

Statuses are `planned`, `implemented`, `verified_mock`, `verified_live`, or `blocked`. `verified_mock` means the current Mock implementation and its stated automated/browser evidence passed; it never implies a Live provider or release result.

| ID | Requirement / acceptance | Planned implementation owner | Planned tests | Current status |
|---|---|---|---|---|
| PR-001 | free-text end-customer chat; example controls only fill text | React customer surface | `tests/integration/test_application_service.py`; Mock browser | verified_mock |
| PR-002 | only two supported logistics issue types | triage/policy/domain schemas | route, revision, and no-action regressions in `tests/integration/test_application_service.py` | verified_mock |
| PR-003 | two-column traceable demo, responsive trace drawer and read-only Eval Dashboard | React surface/event projection | frontend type/build; Edge customer journey and Dashboard scroll surface | verified_mock |
| AI-001 | lightweight triage schema with no tools | triage provider/service | schema/route tests and 32-case development/locked ScenarioManifest contract | verified_mock |
| AI-002 | one bounded native-tool LangGraph Agent | agent graph and ToolNode | `tests/integration/test_agent_graph.py`, budget regressions | verified_mock |
| AI-003 | explicit Mock/Live and no silent fallback | provider factory/config | configuration/provider-failure regressions; Live result recorded separately | implemented |
| DOM-001 | Conversation/Case/Run/Proposal hierarchy | domain models/repositories | multi-Case, closed/repeat, decline, and action integration tests | verified_mock |
| DOM-002 | five status vocabularies remain separate | domain enums and API schemas | state-transition and action-outcome integration tests | verified_mock |
| DOM-003 | one Case scope and clarification/tool/turn budgets | domain guards | clarification, planning/read budget, and serial-mutation tests | verified_mock |
| TOOL-001 | six read-only tools use central authorization | governed-tool runner | cross-account, canonical arguments, and mixed-fragment tests | verified_mock |
| TOOL-002 | present/absent/unavailable, relevance, and cache/retry semantics | result envelope/cache | absent/unavailable, one-retry, exhaustion, issue-irrelevant blocked read, and Carrier Alert tests | verified_mock |
| EVD-001 | deterministic signed-not-received gate | evidence gate | signed delivery proof, duplicate ticket, and revalidation tests | verified_mock |
| EVD-002 | deterministic stalled-tracking gate | evidence gate | server-clock SLA, ORD-002 no-action, ORD-003 proposal, and issue revision tests | verified_mock |
| ACT-001 | immutable proposal + exact confirmation + revalidation | proposal service/API | stale/expired/version/changed-evidence/superseded/decline tests | verified_mock |
| ACT-002 | idempotent write/read-back/uncertain terminal state | executor/ticket repository | duplicate, retryable, terminal, uncertain, and replay tests | verified_mock |
| EVT-001 | persist-before-SSE, replay/dedup, no re-execution | event service/API/client | API replay regression and Mock browser refresh access log | verified_mock |
| SEC-001 | fragment-level override/unauthorized/prohibited blocking | validator/policy/serializers | mixed-input integration test and Mock browser journey | verified_mock |
| SEC-002 | no data existence leak or sensitive trace/CoT | tool errors/event serializers | foreign-order/error serialization and event redaction tests | verified_mock |
| EVAL-001 | three layers and same ScenarioManifest/fault seed | Eval runner/manifests | loader/runner tests; 52-run Mock development matrix | verified_mock |
| EVAL-002 | fair strong Workflow baseline | strong Workflow/application composition | paired Agent/Workflow Investigation and Full-E2E scenarios | verified_mock |
| EVAL-003 | three runs, stable/flaky/fail, no single score | report engine/append-only store/Dashboard | matrix completeness, immutable store, report and Edge Dashboard tests | verified_mock |
| EVAL-004 | pre-registered adoption conclusion | conclusion engine | ADOPT/PREFER/safety-first truth-table tests | verified_mock |
| EVAL-005 | Pilot/freeze source lineage and absolute resource budgets | Eval CLI, freeze contract, report/Dashboard | clean-source/version mismatch, freeze-only lineage, and over-budget acceptance regressions | verified_mock |
| OPS-001 | clean local start, restart/persistence, docs match | trusted operational scripts/docs | clean commit `71f7337` passed archive install, migration/start, restart persistence/SSE, and reset scope; final RC reruns against its exact revision | verified_mock |

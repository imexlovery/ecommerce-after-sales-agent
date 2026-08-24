# Repository Operating Contract

This repository builds a local portfolio-grade ecommerce after-sales logistics Agent. Read this file, `PROJECT.md`, `NON_GOALS.md`, `docs/IMPLEMENTATION-SOURCE-MAP.md`, and `docs/ARCHITECTURE.md` before changing code.

## Product boundary

- The only supported business issues are `signed_not_received` and `stalled_tracking`.
- The end customer chats in free text. Two example controls may fill the composer, but must never select a route.
- One lightweight LLM triage produces only `intent`, `risk_flags`, `order_ids_mentioned`, and `confidence`.
- One bounded logistics investigation Agent may dynamically select allowlisted, read-only tools.
- Authorization, policy, evidence completeness, proposal validity, customer confirmation, idempotency, and write verification are deterministic project-owned code.
- The model never receives or calls a write tool. The only simulated write is `create_logistics_investigation_ticket`, invoked by the deterministic executor after exact customer confirmation.
- This is a synthetic local demonstration, not a production customer-service system.

## Hard invariants

1. Never merge `CaseState`, `CaseOutcome`, `RunState`, `ProposalState`, or `ActionState` into one status field.
2. Keep `evidence_availability=absent` distinct from `unavailable`. `absent` is a successful observation; `unavailable` is unknown.
3. Every order-scoped tool calls the same `authorize_order(customer_id, order_id)` before data access.
4. The model may provide only `order_id` and, where declared, `issue_type`; trusted identity and carrier fields come from server context.
5. A blocked tool call consumes a planning turn but not a read-tool execution. A retryable tool call may be retried once and both actual executions consume the budget.
6. One `InvestigationCase` is bounded to one authorized order, one primary issue, at most two business clarifications, six actual read-tool executions, sixteen total planning turns, and one active executable proposal.
7. A proposal is immutable, versioned, bound to critical evidence and execution parameters, and expires after 15 minutes. Any changed material fact requires a new Evidence Gate decision and a new proposal record.
8. Natural-language assent never executes a write. Confirmation must name the exact `proposal_id` and `version` through the UI/API action.
9. Preserve the original action identity and idempotency key in `uncertain`; never create a new key to retry an ambiguous write.
10. Persist canonical events before SSE delivery. Reconnect/replay may redeliver events but must never re-execute model, tool, or action work.
11. Do not emit raw chain-of-thought, system prompts, provider payloads, API keys, unredacted PII, stack traces, or fault seeds to browser events.
12. `LLM_MODE=mock|live` is explicit. Live provider failures never fall back silently to Mock.

## Framework and ownership

- Product strategy: `OTHER_FRAMEWORK`.
- Use actual LangGraph/LangChain public APIs for the Agent loop and `ToolNode`; do not implement a JSON loop that merely resembles tool calling.
- Use `langchain-deepseek` for the Live provider and `deepseek-v4-flash` unless a recorded decision changes it.
- Hypha is not a dependency and must not be claimed.
- Project-owned modules remain authoritative for domain state, authorization, evidence gates, execution, events, persistence, evaluation, and UI.
- There is one production composition root. Tests may replace adapters, but may not contain business wiring absent from the application.

## Development rules

- Python: 3.12, managed with `uv`; use the repository `.venv`, `uv run`, `uv add`, and committed `uv.lock`. Never commit `.venv`.
- Frontend: React + TypeScript + Vite. Keep one lockfile and use the existing package manager once established.
- Hand-authored edits use `apply_patch`. Formatting or lockfile generation may use their native tools.
- Read `pyproject.toml`, `uv.lock`, `frontend/package.json`, and existing test commands before dependency or code changes.
- Keep fixtures fictional. Never place real customer/order data or secret values in source, logs, screenshots, events, or evaluation reports.
- Do not read or print `.env`. Validate only whether named settings are present and use `.env.example` for shapes.
- Keep changes within this repository. Remote publication, deployment, and GitHub writes require separate user authorization.

## Test and evidence contract

- Label results as `static`, `mock`, `contract`, `integration`, `real_external`, `surface_e2e`, or `operational`.
- A Mock browser run is not a Live provider gate. A process listening on a port is not a completed user journey.
- Include every run in evaluation statistics, including timeouts, schema failures, and provider errors. Never select the best run.
- `safety_gate_pass` is a hard boolean. A safety violation cannot be averaged away by quality metrics.
- Agent and strong Workflow baselines share tools, budgets, evidence gate, fixtures, fault seeds, response layer, and executor.
- Do not hand-author `delivery/framework-integration-report.json`, `delivery/test-execution-report.json`, or `delivery/release-evidence.json`; only the trusted scripts may generate them from a committed revision.

## Human checkpoints

Continue autonomously through ordinary implementation choices and repair ordinary test/lint failures. Pause for the owner only at:

1. the first complete browser vertical slice. It counts as the Live checkpoint only when `LLM_MODE=live` actually reaches DeepSeek; otherwise label it Mock and the Live gate remains open;
2. the release candidate after evaluation, dashboard, documentation, and clean-start verification;
3. a scope/authority conflict, missing irreversible-action approval, or external credential/resource blocker that cannot be safely bypassed.

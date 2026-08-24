# Architecture

## Architectural claim

The product uses probabilistic reasoning only where the investigation path benefits from it. Everything that grants access, decides whether evidence is sufficient, authorizes a side effect, or records official state is deterministic.

```text
Untrusted CustomerMessage
  -> Deterministic input validation and redaction
  -> Lightweight structured Triage
  -> Deterministic Policy Router
  -> Authorized InvestigationCase
  -> Bounded LangGraph Logistics Agent <-> governed read-only ToolNode
  -> Deterministic Evidence Gate
  -> customer reply OR ActionRecommendation
  -> server-built immutable ActionProposal
  -> exact customer confirmation
  -> deterministic idempotent executor
  -> read-back verification
  -> terminal or recoverable state + persisted events
```

The strong Workflow baseline enters after a normalized authorized Case and shares the same governed tools, budgets, evidence gate, proposal service, response renderer, and executor. It replaces only dynamic next-tool selection with a competent conditional path.

## Product composition

```text
React customer surface + Developer Trace + Eval Dashboard
                         |
                     FastAPI /v1
                         |
              Application composition root
      +------------------+------------------+
      |                  |                  |
 deterministic       LangGraph         persistence
 domain/policy       Agent graph       and event service
      |                  |                  |
      +------ governed read tool runner ----+
                         |
             synthetic fixture repositories
```

One production composition root creates settings, database sessions, repositories, event service, provider, graph, tools, policy, evidence gate, proposal/executor services, and API routes. Mock and Live replace only the inference provider behind the same graph contract.

## Canonical ownership

| Concern | Canonical owner | Why |
|---|---|---|
| Conversation/Case/Run/Proposal/Action state | project domain + SQL repositories | state must remain deterministic and queryable independent of provider |
| Dynamic next observation | LangGraph Agent | this is the experimental value under evaluation |
| Tool dispatch | LangGraph `ToolNode` plus project governed-tool wrappers | native tool-call trajectory with server enforcement |
| Authorization and canonical context | project policy/tool runner | cannot depend on model compliance |
| Evidence availability/conflict/eligibility | pure project Evidence Gate | reproducible and shared by Agent/Workflow |
| Write proposal and execution | project proposal/executor services | exact confirmation, idempotency, uncertain state, read-back |
| Events and SSE replay | project event service | durable audit facts and customer/developer visibility policies |
| Checkpoint of graph execution | LangGraph SQLite checkpoint store | graph-local durable progress only; not business-state authority |
| Evaluation | project harness | fair paired comparison and fixed acceptance rules |

## Trust boundaries

1. **Browser to API:** customer text, mentioned order IDs, proposal actions, and Last-Event-ID are untrusted. Virtual identity is resolved server-side.
2. **Triage output:** schema-validated intent/confidence come from the lightweight
   model. The server replaces mentioned order IDs with literal regex extraction
   and unions only allowlisted deterministic injection/prohibited/multi-order/PII
   flags. Triage cannot authorize, access data, or make policy decisions.
3. **Agent to tools:** every tool call is untrusted. The server replaces or validates canonical scope, reauthorizes ownership, checks budgets, and returns structured observations.
4. **Tool data to Agent:** free-text fields are untrusted data. They are marked by field path and cannot modify system policy or tool authority.
5. **Recommendation to proposal:** Agent text has no execution rights. Deterministic code independently evaluates evidence and builds a typed proposal.
6. **Confirmation to executor:** customer action is rebound to identity, proposal version, expiry, critical evidence hash, active-ticket state, and policy before one idempotent write.
7. **Internal facts to browser:** separate serializers create customer and developer projections. Sensitive fields are never shipped and merely hidden with CSS.

## Bounded Agent graph

The graph is a single Agent loop, not multiple agents:

```text
START
  -> plan_with_model
       -> no tool call: validate candidate conclusion -> END
       -> tool call: governed ToolNode -> record observation -> budget guard -> plan_with_model
       -> malformed/forbidden/budget exhausted: deterministic failure/recovery node -> END
```

- Model: `deepseek-v4-flash` in Live mode.
- Tools: six allowlisted read-only functions.
- Per Run: at most 8 planning turns.
- Per Case: at most 16 planning turns and 6 actual read-tool executions.
- Tool cache is Case-scoped and revision-aware; cache hits do not consume execution budget.
- The graph returns a structured investigation recommendation. It never performs the write.
- Hidden reasoning is neither requested nor stored. Developer Trace contains actions and structured observations only.

## Persistence

Two SQLite databases are intentionally separate:

- business database: conversations, messages, triage, policy, cases, runs, tool calls, evidence, proposals, executions, tickets, events, fixture versions, and eval report metadata;
- LangGraph checkpoint database: graph execution checkpoints keyed by Case/Run thread identity.

Business tables are the current-state read model. Events are append-only audit facts, not a complete Event Sourcing architecture. Database migrations are managed by Alembic. Local `var/` data is generated and ignored by Git.

## Failure and recovery principles

- Triage timeout/schema failure: no tools; customer sees retry.
- Retryable read-tool failure: one bounded retry; do not cache the failure.
- Critical evidence unavailable: no proposal. Temporary failure maps to `awaiting_retry`; persistent conflict/unsafe uncertainty maps to `human_support_required`.
- Model failure: preserve prior evidence and create a failed Run; no write.
- Duplicate ticket: close with no new action and expose existing ticket status.
- Write response lost and read-back unavailable: `ActionState=uncertain`, close automation with `CaseOutcome=uncertain`, preserve action identity, never blindly reissue.
- SSE reconnect: replay persisted events after the last sequence; do not restart work.
- Closed Case: never reopen. A later request creates a new Case with optional `related_case_id`.

## Framework decision

`OTHER_FRAMEWORK` is selected because LangGraph directly provides the bounded state graph and native ToolNode required to demonstrate the Agent loop, while the project retains business authority. Hypha is not selected, imported, copied, or claimed. See `docs/FRAMEWORK-INTEGRATION.md` and `docs/AGENT-MODULE-MATRIX.md`.

## Deployment boundary

The supported target is a local single-user prototype bound to loopback. Docker Compose may package local reproducibility, but no public/cloud/production deployment is in scope. A listening API or rendered screenshot is not delivery evidence; the browser journey and read-back side effect must be verified.

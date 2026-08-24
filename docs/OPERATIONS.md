# Local Operations Runbook

Status: **planned local operations contract; operational verification pending**

This runbook applies only to the local synthetic portfolio prototype. It is not
an on-call guide, production SLO, disaster-recovery plan, or authorization to
deploy publicly.

## Runtime topology

```text
Browser http://127.0.0.1:5173
  -> React/Vite
  -> FastAPI http://127.0.0.1:8000
     -> business SQLite: var/business.sqlite3
     -> LangGraph checkpoint SQLite: var/langgraph-checkpoints.sqlite3
     -> explicit Mock adapter OR DeepSeek Live provider
```

Both web processes must bind to loopback. Docker Compose, if added, must publish
ports to `127.0.0.1`, not all interfaces. No real ecommerce, carrier, payment,
CRM, or ticketing service belongs in this topology.

## Normal operating sequence

1. Confirm the committed `uv.lock` and frontend lockfile match the checkout.
2. Select `LLM_MODE=mock` or `live` explicitly.
3. Run schema migrations and verify both database paths are distinct.
4. Seed or verify the versioned synthetic fixture set.
5. Start FastAPI on `127.0.0.1:8000`.
6. Start Vite on `127.0.0.1:5173`.
7. Check liveness/readiness, then complete an actual browser journey.
8. Record the evidence level and exact revision; never promote Mock evidence to
   Live or browser evidence.

The exact commands remain in `docs/STARTUP.md` and are currently unverified.

## Liveness and readiness target

| Check | Meaning | Must not claim |
|---|---|---|
| process/listener | a process is bound to the expected loopback port | that the API, Agent, database, provider, or user journey works |
| `/healthz` | FastAPI can return a minimal response | readiness or provider success |
| `/readyz` | settings, migration revision, fixture version, and both local stores pass preflight | successful DeepSeek inference or native tool calling |
| browser vertical slice | one visible user journey reaches its asserted state | all scenarios, restart safety, or release readiness |
| Live browser vertical slice | the same journey actually reaches DeepSeek in `live` mode | locked Eval or production readiness |

Readiness should not make a paid provider call. Provider availability belongs
to an explicit Live probe or journey and is reported as `real_external`.

## Mode operation

### Mock

- UI and API must label the process `Mock`.
- The real application composition, LangGraph graph contract, governed tools,
  Evidence Gate, proposal/executor, events, and storage remain in use.
- Results may support `mock`, `integration`, or `surface_e2e` evidence, depending
  on what was actually exercised. They do not support `real_external`.

### Live

- UI and API must label the process `Live`.
- The owner supplies `DEEPSEEK_API_KEY`; operators check presence only and never
  print the value.
- Missing configuration fails startup; provider failures remain Live failures.
- No retry policy may switch to Mock. Every timeout/schema/provider failure is
  retained in Eval statistics.

## SQLite operations

### Business database

This is the canonical local read model and audit-fact store. Migrations use
Alembic. Do not manually edit state to make a demonstration pass. Before a
schema experiment, a local copy may be made for recovery, but it is not a
production backup claim.

### LangGraph checkpoint database

This stores framework recovery state only. It cannot be used to reconstruct or
override official Case, Proposal, Action, Ticket, or Event truth. Cleanup must
be scoped to reset synthetic Cases or a documented compatibility migration.

### Restart expectations

After a normal process restart:

- closed Cases stay closed;
- persisted events replay after `Last-Event-ID` without restarting work;
- pending proposals retain identity, version, and original expiry;
- submitted/uncertain actions retain their action identity and idempotency key;
- a verified ticket is not created again.

These are acceptance requirements and remain unverified until restart tests are
recorded.

## Demo reset boundary

Reset is a local developer action over synthetic data. It must be transactional
or fail without presenting a partial reset as success.

Reset may:

- restore generated fixture rows to the source-controlled fixture version;
- remove demo conversations, messages, triage records, policy decisions,
  Cases, Runs, tool/evidence records, proposals, actions, tickets, and events;
- remove LangGraph checkpoint rows belonging to the reset synthetic Cases.

Reset must preserve:

- `.env`, `.env.example`, credentials, model/provider settings, and runtime
  mode;
- source-controlled fixture definitions, prompts, policies, tool schemas, and
  migrations;
- `uv.lock`, the frontend lockfile, source code, and documentation;
- Eval manifests, raw Eval runs, historical reports, and trusted delivery
  reports.

Do not implement reset as an unscoped deletion of `var/` or the repository. The
target CLI/API command will be documented after it exists and passes reset-scope
tests. Until then, manual database deletion is not the supported operation.

## Logs, events, and trace

Operational logs should include safe identifiers, state transitions, event IDs,
duration, result codes, retryability, mode, and version labels. They must not
include:

- API keys or `.env` values;
- raw system/developer prompts or chain-of-thought;
- full provider request/response payloads;
- unredacted PII or unnecessary malicious text;
- hidden Eval fault seeds in browser-visible events;
- stack traces in customer or Developer Trace projections.

Canonical events are persisted before SSE publication. At-least-once delivery
means the browser may receive a duplicate event; it deduplicates by event ID.
Reconnect never invokes the Agent, a tool, or the executor.

## Failure and recovery table

| Failure | Safe operating result | Recovery |
|---|---|---|
| Triage timeout/schema error | no Case tools, failed Run or retry response | explicit customer retry; no Mock fallback |
| retryable read-tool error | at most one retry; both executions count | retry within Case budget, then `awaiting_retry` if critical |
| critical evidence unavailable | no proposal | later retry or `human_support_required`, depending on persistence/conflict |
| model/provider failure | preserve Case evidence; no write | new retry Run when state allows |
| stale/expired/changed proposal | block confirmation and retain old record | rerun Evidence Gate and create a new proposal if still eligible |
| duplicate active ticket | no new ticket | show existing synthetic ticket and close no-action |
| write response lost, read-back unavailable | `ActionState=uncertain`, terminal automation outcome | preserve the same action/idempotency identity; do not reissue |
| SSE disconnect | replay persisted events | reconnect with cursor; never re-run work |
| reset failure | show reset failed and preserve current projection | repair the transaction/path; do not claim a clean demo |

## Dependency and schema changes

- Python dependencies change through `uv add` / `uv add --dev`, followed by a
  committed `uv.lock`.
- Frontend dependencies use the selected package manager and its one committed
  lockfile.
- Framework/provider upgrades require contract, native-tool, failure, restart,
  browser, and Eval regression evidence.
- Database changes use Alembic; never use an unrecorded schema mutation or
  `alembic stamp` to conceal a failed migration.
- A model, prompt, tool schema, fixture, Evidence Gate, Workflow, graph, or
  environment change after the locked-set freeze creates a new Eval version.

## Evidence capture

For every operational claim, retain:

- timestamp and source revision;
- operating system, Python, Node, and lockfile identities;
- `LLM_MODE` and non-secret model/version labels;
- exact command and exit code;
- relevant safe log excerpt or report path;
- expected result and actual result;
- evidence level from `docs/TEST-REPORT.md`;
- every failed/timeout run, not only the best attempt.

Trusted JSON reports named in `AGENTS.md` are generated only by their future
trusted scripts from a committed revision. Do not hand-author them.

## Owner checkpoints

Stop for the owner only at the first complete browser vertical slice, the
release candidate, an authority/scope conflict, or a credential/resource
blocker that cannot be bypassed safely. A Mock browser path may be reviewed at
the first checkpoint, but it leaves the Live gate open.

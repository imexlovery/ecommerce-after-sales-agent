# Troubleshooting

Status: **diagnostic plan; concrete commands must be rechecked after scaffold**

Start with the evidence boundary: determine whether the failure is installation,
configuration, listener, API, model, tool, state, SSE, frontend, or browser
journey. Do not turn “a process exists” into “the product works,” and do not turn
a Mock success into a Live success.

## Safe first checks

After the corresponding files and routes exist, these checks should be safe:

```bash
cd /Users/tristana/Develop/ecommerce-after-sales-agent
uv --version
uv run python --version
uv run python -c "import sys; print(sys.executable)"
test -f pyproject.toml && test -f uv.lock
test -f frontend/package.json && test -f frontend/package-lock.json
lsof -nP -iTCP:8000 -sTCP:LISTEN
lsof -nP -iTCP:5173 -sTCP:LISTEN
curl --fail --silent http://127.0.0.1:8000/healthz
curl --fail --silent http://127.0.0.1:8000/readyz
```

The expected Python executable is inside this repository's `.venv`. The
frontend lockfile check assumes npm is selected; use the actually committed
lockfile once established. Health routes are planned and currently unverified.

To check only whether the Live key is present without printing it:

```bash
if [ -n "${DEEPSEEK_API_KEY:-}" ]; then echo configured; else echo missing; fi
```

Never run `env`, `printenv`, `set`, `cat .env`, or a debug endpoint that dumps
settings while collecting shareable evidence.

## Installation and process symptoms

| Symptom | Likely cause | Check | Safe response |
|---|---|---|---|
| `pyproject.toml` or `uv.lock` missing | implementation scaffold is incomplete | `test -f` checks above | do not invent a startup command or use global `pip`; finish the planned scaffold |
| Python is not 3.12 or executable is global | `uv sync` was not run in repository | `uv run python --version`; print `sys.executable` | run the target `uv sync --locked --python 3.12` path after it has been verified against the lockfile |
| import fails despite a global install | wrong interpreter or missing locked dependency | inspect `sys.executable`; run the future provenance probe | resolve through root `uv.lock`; do not `pip install` ad hoc |
| `npm ci` fails because no lockfile exists | frontend package manager not established | inspect `frontend/` manifests | create/commit exactly one lockfile, then update `docs/STARTUP.md` |
| frontend starts on 5174 | 5173 is occupied | `lsof` checks | stop the stale local process or deliberately update both origin and URL; do not silently use a mismatched port |
| API binds `0.0.0.0` | unsafe/local profile misconfiguration | inspect startup arguments and listener | stop and restart with `--host 127.0.0.1` |
| API root returns 404 | only operational and `/v1` routes may exist | request `/healthz`, `/readyz`, or documented `/v1` route | open the product at the Vite URL, not the API root |

## UI, API, CORS, and SSE

| Symptom | Likely cause | Check | Safe response |
|---|---|---|---|
| UI loads but requests fail | wrong `VITE_API_BASE_URL`, API absent, or CORS mismatch | browser Network panel; compare exact scheme/host/port | use `http://127.0.0.1:8000` consistently; restart Vite after config changes |
| CORS error between `localhost` and `127.0.0.1` | origins are different even on one machine | compare browser origin with `CORS_ALLOWED_ORIGINS` | use the documented `127.0.0.1` URLs on both sides |
| trace stops updating | SSE listener disconnected or server event stream failed | inspect Network event stream and latest persisted event ID | reconnect with `Last-Event-ID`; do not resubmit the customer message |
| trace shows duplicates after reconnect | at-least-once replay without client dedup | compare duplicate `event_id`/sequence | deduplicate rendering by event ID; never suppress persistence or re-execute work |
| reconnect creates a second ticket | reconnect path incorrectly triggers business execution | inspect persisted runs/actions/events | treat as a hard safety failure; stop acceptance and repair before retrying |
| browser exposes prompt/key/raw PII | serializer boundary failed | inspect actual Network event payload, not only hidden UI | treat as a hard safety failure; fix server visibility policy and invalidate evidence |

## Mock and Live provider behavior

| Symptom | Interpretation | Response |
|---|---|---|
| UI says Mock while Live was expected | process started with `LLM_MODE=mock` or wrong config precedence | stop; correct only `LLM_MODE`; restart and verify the API/UI label |
| Live startup says key missing | expected fail-fast behavior when no owner key is configured | owner supplies the key locally; do not insert a placeholder or inspect `.env` |
| Live request times out or schema validation fails | real provider/run failure | retain the failed Run, show retry, and include it in Eval; never return a Mock answer |
| Live reply has no native tool call | model/prompt/tool binding or scenario path issue | inspect safe `assistant.tool_calls`/ToolMessage trajectory and framework probe | do not manufacture pseudo-tool JSON; reopen framework feasibility if repeated |
| model requests a foreign or mismatched order | untrusted model argument | confirm central wrapper blocks before data access | blocked call uses a planning turn, not a read execution; no data may leak |
| tool data contains instruction-like text | untrusted fixture field | inspect structured field-path marking and resulting authority | the text may inform facts only; any authority change is a hard safety failure |

## Data and state behavior

| Symptom | Likely cause or meaning | Safe response |
|---|---|---|
| `database is locked` | overlapping local writers, long transaction, or two processes using one file | stop duplicate API processes; inspect transaction boundaries; keep same-Case mutations serialized |
| business and checkpoint data appear mixed | database paths or repository ownership crossed | verify the two configured paths and schema ownership | stop; separate the stores before continuing evidence collection |
| migration revision mismatch | checkout and business DB schema differ | run the documented Alembic current/history checks once implemented | back up synthetic demo state if needed, then use a real upgrade/downgrade; never conceal with `stamp` |
| POD is missing | may be valid `absent`, not a failure | inspect `execution_status` and `evidence_availability` | `success + absent` may support a decision; do not map it to `unavailable` |
| POD/ticket query timed out | evidence is `unavailable` | inspect typed tool envelope | block Proposal; follow bounded retry/support behavior |
| proposal button is disabled | proposal expired, superseded, invalidated, declined, or already confirmed | read Proposal state/version/expiry and critical evidence hash | do not mutate the old record; rerun Evidence Gate before a new proposal |
| natural-language “好” does nothing | expected safety behavior | verify no confirmation endpoint/action Run occurred | use the exact proposal confirmation button |
| repeated confirm returns conflict/existing result | idempotency or inactive Proposal guard | inspect action/ticket read-back | do not create a new proposal/key solely to force another write |
| action is `uncertain` | write may have happened and read-back failed | preserve the original action and idempotency key | never blindly resubmit; this is a terminal automation outcome |

## Fixture reset and Eval dashboard

| Symptom | Check | Safe response |
|---|---|---|
| reset changed `.env`, model config, or Eval history | compare protected paths/artifact identities | hard reset-scope failure; restore if possible and repair the reset transaction |
| reset leaves old Case checkpoint that resumes work | compare reset Case IDs with checkpoint rows | clean only associated checkpoint rows; do not delete unrelated reports/configuration |
| reset partially succeeds | inspect reset transaction/result event | report failure and preserve current UI state; never label it clean |
| Dashboard is empty | no immutable Eval report exists yet | show “尚无评测报告”; do not synthesize chart data |
| only successful Eval attempts appear | failed/timeout/provider runs were dropped | invalidate the report and rerun accounting with every attempt |
| Agent appears better only because Workflow differs | baseline parity violation | compare tools, budgets, gates, fixture/fault seed, response layer, and executor | invalidate comparison; repair parity before drawing a conclusion |

## When to stop and ask the owner

Pause only when:

- the first complete browser vertical slice is ready for product review;
- Mock is complete but the required owner credential/resource blocks Live;
- a release candidate is ready;
- a requested change expands into real integrations/data, public deployment,
  irreversible actions, or another scope/authority conflict.

Ordinary lint, unit-test, implementation, port, migration, and local dependency
failures should be diagnosed and repaired without repeated owner interruption.

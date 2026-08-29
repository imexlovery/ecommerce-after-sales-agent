# Configuration Contract

Status: **implemented local configuration contract; runtime evidence is recorded in `TEST-REPORT.md`**
Supported profile: local, single-user, loopback-only, synthetic data

This document defines the implemented local configuration boundary. A listed
setting is consumed by the typed `Settings` object unless marked as a future
release concern; a configured credential alone is not evidence of a successful
Live provider request or browser journey.

## Principles

- `LLM_MODE` is always explicit: `mock` or `live`.
- Optional Mock pacing delays only newly executed Demo milestones; it never delays Live calls or SSE replay.
- A Live startup or request failure never changes the process to Mock.
- Policy retrieval is independent of `LLM_MODE`: runtime defaults to `real_local`, while
  `fake_test` is an explicit deterministic adapter permitted only in automated tests.
- Real-local policy retrieval never falls back to fake embeddings, keyword search, or a remote vector DB.
- The supported server bind address is loopback only.
- DeepSeek credentials are supplied and owned by the repository owner. They are
  never committed, printed, sent to the browser, or copied into an Eval report.
- The browser receives only `VITE_*` public configuration. It never receives a
  provider key or direct provider URL.
- Business state and LangGraph checkpoints use two different local SQLite
  files. Neither database is a production-data store.
- Source-controlled fixtures and immutable Eval reports are configuration or
  evidence, not disposable demo state.

The default customer Demo reads the read-only seed at
`data/business-demo-v1/`. Its mutable Case, Proposal, Action, Ticket, and Event
records remain in the existing business SQLite store. `fixture-v1` is retained
only for explicit legacy tests and historical evaluation paths; it is never the
implicit default Demo dataset.

## Backend settings

The typed `Settings` object selects Mock or Live explicitly, rejects a missing
Live credential, legacy model aliases, timezone-naive evaluation time, and any
synthetic fault profile in Live mode. Loopback is the supported local profile.

| Setting | Value / shape | Required | Secret | Contract |
|---|---|---:|---:|---|
| `LLM_MODE` | `mock` or `live` | yes | no | Selects one composition at startup; never changes because of provider failure. |
| `API_HOST` | default `127.0.0.1` | yes | no | Supported local bind address. |
| `API_PORT` | integer, default `8000` | yes | no | FastAPI port; must match the frontend API base URL. |
| `FRONTEND_ORIGIN` | exact local origin, default `http://127.0.0.1:5173` | yes | no | The only configured CORS origin for the local surface. |
| `DATABASE_URL` | default `sqlite:///./var/after-sales.db` | yes | no | Current business state, synthetic fixtures, tickets, events, and metadata. |
| `LANGGRAPH_CHECKPOINT_URL` | path, default `./var/langgraph-checkpoints.db` | yes | no | Graph recovery state only; never the business source of truth. |
| `EVAL_ARTIFACT_ROOT` | path, default `./var/evals` | yes | no | Append-only raw Eval runs and versioned reports; Demo reset must preserve it. |
| `POLICY_RETRIEVAL_MODE` | `real_local` or `fake_test` | yes | no | Explicit embedding path. Browser/demo evidence requires `real_local`; `fake_test` is test-only. |
| `POLICY_EMBEDDING_MODEL` | fixed `BAAI/bge-small-zh-v1.5` | yes | no | Pinned local Chinese/mixed-language embedding model; Phase 2-A rejects other values. |
| `POLICY_EMBEDDING_REVISION` | fixed commit `7999e1d3359715c523056ef9478215996d62a620` | yes | no | Immutable model revision; no `latest` resolution is allowed. |
| `POLICY_INDEX_ROOT` | path, default `./var/policy-rag-index` | yes | no | Rebuildable local vector index. It records corpus digest, chunker, model, vector dimension, and source hashes; it is never policy authority. |
| `POLICY_RETRIEVAL_EVAL_ARTIFACT_ROOT` | path, default `./var/retrieval-evals` | yes | no | Development retrieval records/reports; the final schema-v3 Retrieval Locked report is stored separately and is bound to F-final. |
| `POLICY_RETRIEVAL_TOP_K` | range `1–3`, default `3` | yes | no | Maximum candidate count passed to the deterministic Resolver; it remains one governed read-tool execution. |
| `POLICY_RETRIEVAL_MIN_SIMILARITY` | `-1..1`, default `0.50` | yes | no | Minimum normalized cosine score. Below it is structured `no_hit`, not `EvidenceAvailability.absent`. |
| `FIXTURE_VERSION` | default `business-demo-v1` | yes | no | Recorded on trusted context and current Demo/API responses; legacy `fixture-v1` must be selected explicitly by historical tests/evals. |
| `SCENARIO_EVALUATED_AT` | timezone-aware timestamp; default synthetic fixture time | yes | no | Trusted server time for policy/SLA evaluation; browser/model time is never used. |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | Live | no | Frozen model ID unless `PROJECT.md` records a replacement decision. |
| `DEEPSEEK_API_KEY` | non-empty owner-supplied value | Live only | yes | Used server-side only. Missing or invalid key fails Live preflight/request visibly. |
| `DEEPSEEK_API_BASE` | default `https://api.deepseek.com` | Live | no | Server-side provider base URL. |
| `DEEPSEEK_TIMEOUT_SECONDS` | `1–120`, default `30` | Live | no | Provider request timeout. |
| `MOCK_DEMO_STEP_DELAY_MS` | integer `0–1000`; Demo example `300` | no | no | Mock-only pause after durable milestones so the real trajectory is observable. `0` disables pacing; Live is always no-op. |
| `SYNTHETIC_FAULT_PROFILE` | `none`, `pod_timeout_once`, or `policy_unavailable` | Mock only | no | Acceptance-only scripted read failure. `policy_unavailable` exhausts the policy read's permitted attempts so the browser can verify a no-Proposal fail-closed path. It cannot be enabled in Live, does not alter persisted facts, and is never emitted to the browser. |
| `SCENARIO_FAULT_SEED` | opaque non-empty synthetic seed | Eval/Demo | no | Reproduces registered fixture failures server-side; never emitted to browser events. |
| `LOG_LEVEL` | default `INFO` | no | no | Logs structured summaries; never raw prompts, keys, full provider payloads, or unredacted PII. |

Any added field must preserve the hard invariants in `AGENTS.md` and be listed
here before the release candidate.

## Frontend settings

| Setting | Planned value | Contract |
|---|---|---|
| `VITE_API_BASE_URL` | `http://127.0.0.1:8000` | Browser talks only to the local FastAPI service. API paths retain the `/v1` prefix. |

No `VITE_*` setting may contain `DEEPSEEK_API_KEY`, a provider credential, raw
system instructions, or a private server-only path. Values compiled by Vite are
public by definition.

## Mock and Live mode matrix

| Behavior | `mock` | `live` |
|---|---|---|
| Provider credential required | no | yes |
| Native LangGraph/ToolNode application path required | yes | yes |
| Network request to DeepSeek | no | yes |
| Optional milestone pacing | `MOCK_DEMO_STEP_DELAY_MS`; new execution only | never |
| UI mode label | `Mock` | `Live` |
| Provider failure response | explicit Mock failure if the configured Mock fails | explicit Live failure; no Mock result |
| May satisfy Mock browser gate | yes, after a complete browser journey | no |
| May satisfy Live provider gate | no | yes, only after a real DeepSeek browser journey |

Mock mode is an inference adapter replacement, not a second business runtime.
Authorization, tools, budgets, Evidence Gate, proposals, executor, events,
persistence, and UI composition remain the production application path.
Pacing is application-layer Demo behavior after an event is persisted; it does
not rewrite timestamps, buffer historical SSE, or pretend a replay is new work.

## Controlled Policy RAG mode matrix

| Behavior | `real_local` | `fake_test` |
|---|---|---|
| Embeddings | pinned `sentence-transformers==5.7.0` + BGE model revision | deterministic test double only |
| Vector similarity | real normalized cosine over a local rebuilt index | deterministic test-only cosine fixture |
| Silent fallback | never | not applicable; selected explicitly |
| Browser vertical-slice evidence | yes, when the LLM mode is separately shown | no; test replacement only |
| Policy authority | canonical corpus reload + hash/window/scope Resolver | same Resolver/corpus contract |

The model download/cache remains local machine state and is not committed. The
pinned BGE model is MIT-licensed according to its public model card; no policy
text, vector, raw provider payload, or credential is sent to browser events.

## File and precedence contract

The target local layout is:

```text
.env                 # local, ignored, never inspected or committed by Codex
.env.example         # names and non-secret examples only
pyproject.toml
uv.lock
.venv/               # local, ignored
src/after_sales_agent/
tests/
frontend/
var/
  business.sqlite3
  langgraph-checkpoints.sqlite3
  evals/             # generated immutable report artifacts, preserved by reset
```

Precedence is process environment over the local `.env` file over non-secret
application defaults. The settings loader uses this order; it does not export a
server-only setting to Vite.

`.env.example` may contain only placeholder values. `.env` must be ignored by
Git. Automation may check whether a required setting is present, but must not
read it into logs or print its value.

## Two-database boundary

| Store | Owns | Must not own |
|---|---|---|
| Business SQLite | conversations, messages, triage, policy, Cases, Runs, tool/evidence records, proposals, actions, synthetic tickets, persisted events, fixture and Eval metadata | LangGraph internal checkpoint serialization |
| LangGraph checkpoint SQLite | framework checkpoint data keyed by Case/Run thread identity | canonical Case state, official event history, ticket truth, proposal authority, Eval result truth |

Both files are local generated data and must be ignored by Git. A reset may
remove checkpoint rows associated with reset synthetic Cases, but it must not
promote checkpoint state to business truth or delete unrelated evidence.

## Startup/preflight boundary

The current startup path creates the two stores, initializes the business
schema, initializes the checkpoint store, and verifies the configured fixture
version. The typed settings object validates the mode/key/model/time/fault
constraints described above. These are the local startup checks; a configured
key is deliberately not treated as provider availability.

The following are configuration preflight requirements. The final release
claim is established by the separate trusted Live, Locked, browser, and
operational scripts, not by settings presence alone:

1. `LLM_MODE` is recognized and shown in the UI/API;
2. the server bind is loopback in the supported profile;
3. both SQLite paths are distinct, writable local paths;
4. the business schema is at the expected migration revision;
5. fixtures match the configured version;
6. Live mode has a configured key and supported model ID;
7. CORS contains only the expected local frontend origin; and
8. logs and browser configuration do not contain secrets.

Configuration preflight is not a provider availability check. A configured key
does not prove a successful DeepSeek request, native tool call, or browser
journey.

## Evidence status

The settings loader and explicit Mock runtime have executable `contract`,
`integration`, and Mock browser evidence. The final F-final evidence also has a
real DeepSeek native-tool trajectory and a Live Microsoft Edge journey; no
Live-to-Mock fallback occurred. See `docs/TEST-REPORT.md`. Configuration
presence alone still never substitutes for those executed gates.

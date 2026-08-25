# Local Startup

Status: **committed-source Mock/Live API, real Edge automation, and trusted clean install/restart verified on F-final**
Supported address: `http://127.0.0.1:5173`

This document separates explicit Mock and Live local operation from the final
release evidence. The final Live-provider, locked-acceptance, and release gates
passed on F-final; this remains a local synthetic portfolio prototype.
See `docs/TEST-REPORT.md` for the exact evidence level.

## Prerequisites

- macOS or another local development environment capable of loopback networking;
- `uv` with Python 3.12 support;
- Node.js/npm compatible with the committed `frontend/package-lock.json`;
- an owner-supplied DeepSeek key only for `LLM_MODE=live`;
- local access to the pinned policy embedding model/cache for the default
  `POLICY_RETRIEVAL_MODE=real_local` path.

## Clean installation

```bash
cd /Users/tristana/Develop/ecommerce-after-sales-agent
uv sync --locked --python 3.12
npm ci --prefix frontend
```

`uv sync` must create/use this repository's `.venv`. A successful global import
does not count. `npm ci` is the target only if `frontend/package-lock.json` is
the selected lockfile; update this runbook if another package manager is
explicitly adopted.

## Target local configuration

After `.env.example` exists, create the local file without overwriting an
existing one:

```bash
cd /Users/tristana/Develop/ecommerce-after-sales-agent
test -e .env || cp .env.example .env
```

Edit `.env` locally. Do not paste a provider key into source, screenshots,
issues, browser configuration, shell output, or Eval artifacts. Codex must not
read or print `.env`.

For the first offline run, select:

```text
LLM_MODE=mock
POLICY_RETRIEVAL_MODE=real_local
API_HOST=127.0.0.1
API_PORT=8000
```

See `docs/CONFIGURATION.md` for the complete target inventory.

## Target database preparation

The implementation uses a root Python project and a local `var/` directory.
The committed Alembic migration can be applied with:

```bash
cd /Users/tristana/Develop/ecommerce-after-sales-agent
uv run alembic upgrade head
```

Fixture source data is code-owned and loaded by the application; there is no
separate seed command. The migration shape has contract coverage. The trusted
clean-archive script passed from the exact F-final release-candidate revision;
future material candidates require a new run. The business and LangGraph
checkpoint databases remain separate.

## Mock startup

Terminal 1 — API:

```bash
cd /Users/tristana/Develop/ecommerce-after-sales-agent
LLM_MODE=mock POLICY_RETRIEVAL_MODE=real_local MOCK_DEMO_STEP_DELAY_MS=300 uv run uvicorn after_sales_agent.api.app:app --app-dir src --host 127.0.0.1 --port 8000
```

Terminal 2 — React/Vite:

```bash
cd /Users/tristana/Develop/ecommerce-after-sales-agent
npm run dev --prefix frontend -- --host 127.0.0.1 --port 5173
```

Open `http://127.0.0.1:5173`. Do not open `/` on port 8000 and infer that the
product is broken if the API intentionally exposes only operational and `/v1`
routes.

Both commands were exercised together on loopback for the Mock browser
checkpoint. The optional 300 ms setting makes persisted Mock milestones
observable; use `0` for fast automated checks. It never affects Live mode or
SSE replay. The trusted final clean-start lane separately proves an archived
clean checkout from F-final.

The first policy search may load the pinned local embedding model and build
`./var/policy-rag-index`. This is a rebuildable derived index, not a policy data
store. If model/index retrieval fails, the policy tool surfaces `unavailable`
and the Evidence Gate fails closed; it never switches to fake embeddings or
keyword retrieval. `POLICY_RETRIEVAL_MODE=fake_test` is reserved for automated
tests and cannot close a browser retrieval-evidence gate.

## Live startup

Live mode uses the same API, domain, tools, Evidence Gate, executor, event, and
UI path. Only the inference provider changes.

1. The owner places `DEEPSEEK_API_KEY` in the local `.env` without exposing it.
2. Set `LLM_MODE=live` in `.env`, or override only that non-secret selector at
   process start.
3. Start the API with the same loopback command:

```bash
cd /Users/tristana/Develop/ecommerce-after-sales-agent
LLM_MODE=live POLICY_RETRIEVAL_MODE=real_local uv run uvicorn after_sales_agent.api.app:app --app-dir src --host 127.0.0.1 --port 8000
```

If the key is absent, the model is unsupported, or the provider request fails,
the API must fail preflight or surface a Live failure. It must not return a Mock
success. Merely detecting that a key is present is not `real_external`
evidence.

## Health checks

Operational routes are implementation work, not part of the frozen customer
`/v1` API. The supported checks are:

```bash
curl --fail --silent http://127.0.0.1:8000/healthz
curl --fail --silent http://127.0.0.1:8000/readyz
```

- `healthz` should show that the process can answer.
- `readyz` should verify configuration, migrations, and local stores without
  making a paid provider request.

Both routes exist and passed API surface tests. The separate clean-start
operational probe also passed on F-final, including the real-local Policy RAG
cold start.

## Required first browser journey

A complete Mock or Live vertical slice is more than startup:

1. select a fictional customer;
2. type a signed-not-received request in free text;
3. observe triage, policy, native Agent tool calls, and Evidence Gate events;
4. receive an immutable ticket proposal;
5. confirm the exact `proposal_id` and version with the UI button;
6. observe one simulated ticket write and successful read-back;
7. refresh/reconnect and confirm that no model/tool/action work is repeated.

The UI must visibly show `Mock` or `Live`. A screenshot, open port, API-only
request, or Mock run cannot close the Live checkpoint; the final Live Edge run
did reach DeepSeek and completed the asserted customer journey.

## Developer checks

```bash
cd /Users/tristana/Develop/ecommerce-after-sales-agent
uv run ruff check .
uv run mypy src
uv run pytest -q
npm run typecheck --prefix frontend
npm run build --prefix frontend
npm run e2e:surface --prefix frontend
```

The Edge command requires the local API and Vite processes described above.
Trusted committed-revision commands are registered in
`delivery/test-plan.json`; they refuse to generate evidence from a dirty or
uncommitted tree. Final trusted evidence is bound to F-final, not to this
documentation-only descendant.

## Evaluation commands

```bash
cd /Users/tristana/Develop/ecommerce-after-sales-agent
uv run after-sales-eval validate
LLM_MODE=mock POLICY_RETRIEVAL_MODE=real_local \
  uv run after-sales-eval retrieval-development \
  --revision phase2-final-retrieval-dev-20260825-r1
uv run after-sales-eval pilot \
  --revision phase2-final-live-pilot-20260825-r1 \
  --mode live --timeout 120 --concurrency 2
uv run after-sales-eval freeze \
  --pilot-revision phase2-final-live-pilot-20260825-r1 \
  --retrieval-development-revision phase2-final-retrieval-dev-20260825-r1 \
  --evaluation-revision acceptance-live-phase2-policy-rag-20260825-r3
LLM_MODE=mock POLICY_RETRIEVAL_MODE=real_local \
  uv run after-sales-eval retrieval-locked \
  --freeze evals/config/freezes/acceptance-live-phase2-policy-rag-20260825-r3.json
uv run after-sales-eval locked \
  --freeze evals/config/freezes/acceptance-live-phase2-policy-rag-20260825-r3.json \
  --concurrency 2
```

The final commands were executed from the clean F-final revision. Do not
overwrite the final reports from the documentation-only D-final; create a new
revision chain for any future material change. The Live Pilot requires the
owner-supplied key, and every failed, timed-out, or over-budget attempt remains
retained.

## Stop and restart

Stop each foreground development server with `Ctrl-C`. A normal restart must
reuse the business and checkpoint SQLite files and must not duplicate a ticket,
re-run a completed action, or reopen a closed Case. Browser refresh/SSE replay
has been verified without re-execution; full process restart and clean-start
behavior have passed the committed-source operational harness. Live startup,
provider, and browser behavior were verified as separate final gates; none of
this changes the local-only, non-production boundary.

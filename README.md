# Ecommerce After-Sales Logistics Agent

A local, synthetic customer-service portfolio prototype that tests whether a bounded logistics-investigation Agent adds measurable value over a strong deterministic Workflow.

The customer-facing surface behaves like a logistics support Agent: it acknowledges the problem, explains what it found, and asks before taking the only supported next step. Internally, lightweight triage extracts intent, deterministic code enforces authorization, a single LangGraph Agent chooses read-only observations, and a deterministic Evidence Gate decides whether the system may offer a logistics investigation. The customer must confirm the exact hidden proposal identity and version before a simulated idempotent write.

## Current maturity

`G1 local portfolio prototype` / `T1 synthetic low external impact`.

VS-01 through VS-04 are implemented with Mock/integration evidence. VS-05 has a complete Mock development Pilot, immutable multi-axis reports, a read-only Dashboard, a real Microsoft Edge Mock journey, and a clean-commit non-Live operational pass. The Live Pilot/freeze, locked acceptance set, final Live Edge evidence, and release-candidate gate remain open; this repository must not yet be described as release verified.

Phase 1 of the V2 route additionally makes every declared Manifest assertion
executable through a fail-closed grader registry and requires a sanitized,
two-revision Evidence Pack for any current release claim. A freeze or ignored
delivery report tied to an earlier source revision is historical evidence only;
it never substitutes for a fresh V2 Pilot, versioned freeze, locked result, and
Evidence Pack.

## What this project demonstrates

- native LLM tool calling inside a bounded LangGraph loop;
- deterministic authorization, policy, evidence, proposal, and execution boundaries;
- `absent` versus `unavailable` evidence semantics;
- immutable customer-confirmed proposals, idempotent writes, read-back verification, and `uncertain` outcomes;
- developer-visible trace without raw chain-of-thought;
- a fair Agent-versus-strong-Workflow evaluation with hard safety gates.

## Local Mock start

```bash
uv sync --locked --python 3.12
uv run uvicorn after_sales_agent.api.app:app --host 127.0.0.1 --port 8000
```

In a second terminal:

```bash
cd frontend
npm ci
VITE_API_BASE_URL=http://127.0.0.1:8000 npm run dev -- --host 127.0.0.1 --port 5173
```

Open `http://127.0.0.1:5173`. Mock and Live are explicit modes; Mock never
proves provider capability.

## Evaluation operator flow

```bash
uv run after-sales-eval validate
uv run after-sales-eval pilot --revision pilot-live-r1 --mode live
uv run after-sales-eval freeze \
  --pilot-revision pilot-live-r1 \
  --evaluation-revision acceptance-live-r1
git add evals/config/freezes/acceptance-live-r1.json
git commit -m "freeze live acceptance configuration"
uv run after-sales-eval locked \
  --freeze evals/config/freezes/acceptance-live-r1.json
```

The Live commands require an owner-supplied `DEEPSEEK_API_KEY` in the ignored
local environment. Freeze must be created on the exact clean Pilot commit; the
next commit may add only the immutable freeze file before locked execution.
Every failed, timed-out, or over-budget attempt is retained. See
`docs/EVALUATION.md` for the subsequent trusted Evidence Pack and lineage
verification flow.

## What it does not do

It does not connect to real marketplaces or carriers, process refunds/compensation/returns, use production data, run multiple agents, use RAG/MCP/long-term memory, or claim production readiness. See `NON_GOALS.md`.

## Documentation map

- `PROJECT.md` — canonical status, authority, lifecycle, and decisions
- `docs/PRODUCT-SPEC.md` and `docs/UX-SPEC.md` — product and surface contract
- `docs/ARCHITECTURE.md` and `docs/DOMAIN-CONTRACTS.md` — runtime and domain contracts
- `docs/API-REFERENCE.md` — API and SSE contract
- `docs/EVALUATION.md` — datasets, metrics, gates, and Agent-versus-Workflow decision rule
- `docs/SECURITY-PRIVACY.md` — trust boundaries and threat treatment
- `docs/IMPLEMENTATION-PLAN.md` and `docs/TRACEABILITY.md` — slices and acceptance mapping
- `docs/FRAMEWORK-INTEGRATION.md` and `docs/AGENT-MODULE-MATRIX.md` — exact framework route and ownership

Detailed startup, configuration, evidence labels, and current results live in
`docs/STARTUP.md`, `docs/CONFIGURATION.md`, and `docs/TEST-REPORT.md`. Do not
infer Live capability from Mock fixtures, a configured key, or an old
provider-only probe.

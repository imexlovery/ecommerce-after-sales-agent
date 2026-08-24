# Session Checkpoint

```yaml
checkpoint_date: "2026-08-24"
status: VS_04_MOCK_VERIFIED_VS_05_RELEASE_CANDIDATE_WORK_IN_PROGRESS
repository: /Users/tristana/Develop/ecommerce-after-sales-agent
git_state: local_commits_present_no_remote_publication
product_stage: 6_evaluation_and_tuning
completed_slices:
  - VS-01_MULTI_CASE_MOCK
  - VS-02_STALLED_TRACKING_MOCK
  - VS-03_SAFETY_AND_RECOVERY_MOCK
  - VS-04_STRONG_WORKFLOW_MOCK
in_progress:
  - VS-05_EVALUATION_AND_RELEASE_CANDIDATE
external_gate:
  - LIVE_DEEPSEEK_EXECUTION_IN_PROGRESS
```

The owner authorized work through the release-candidate checkpoint. Ordinary
implementation and test failures are not pause points.

## Newly integrated work

- `StrongWorkflowInvestigationService` implements a competent conditional
  baseline on the same normalized Case, governed read tools, cache/retry rules,
  budgets, fixtures/fault seeds, deterministic Evidence Gate, response/proposal
  services, customer confirmation, and idempotent executor as the Agent path.
- The evaluation package now owns strict ScenarioManifest, raw-run, freeze, and
  report contracts; append-only artifact storage; a three-layer runner; exact
  repeated-run accounting; hard Safety gates; and the pre-registered
  Agent-versus-Workflow conclusion engine.
- The source-controlled collection contains 48 scenarios: 20 development + 12
  locked Triage scenarios and eight development + eight locked shared
  Investigation/Full-E2E scenarios.
- The complete 52-run Mock development matrix passes. It covers all three
  layers and both Layer-2/Layer-3 architectures. The report preserves Safety,
  Task Quality, Tool Trajectory, Stability, Latency, Token, Cost, and paired
  architecture sections without a composite score.
- Development reports are now explicitly non-acceptance artifacts: they always
  retain `KEEP_EXPERIMENTAL`, mark acceptance as not applicable, and cannot
  select Agent or Workflow after a single Pilot repetition. A regression test
  locks that distinction.
- Freeze creation now proves that all Pilot runs came from one exact clean
  source revision with identical model/prompt/tool/framework versions. The
  locked commit may differ only by the immutable freeze file.
- Frozen absolute latency, token, and cost ceilings are executable Acceptance
  conditions. Each overage remains attached to its raw run and is shown in the
  existing Latency/Token/Cost Dashboard axes.
- The Eval API reads the latest immutable report and returns a real 404 empty
  state when none exists. Demo reset does not delete Eval artifacts.
- The Dashboard is a two-track architecture acceptance surface rather than a
  flattened JSON list. It owns vertical scrolling despite the application's
  fixed-height shell and exposes failures, measurement coverage, and version
  locks explicitly.
- A real local Microsoft Edge automation completes Mock reset → free-text
  signed-not-received → proposal → exact confirmation → verified processing
  number → browser refresh, and separately verifies the Dashboard scroll/empty
  state.
- Trusted scripts now exist for staged tests, framework provenance, native
  LangGraph integration, real DeepSeek contracts, real Edge surface execution,
  clean install/restart/reset, release checks, and protected report generation.
  They intentionally refuse to claim trusted evidence before a clean commit.
- The first clean local commit (`71f7337`) passed the full non-Live trusted
  lane: dependency provenance, unit/component/native-framework stages, real
  Edge Mock surface, clean archive install, Alembic start, ticket journey,
  process restart persistence/SSE, reset-scope preservation, and release
  checks. Exact final-source evidence is regenerated after every source commit
  and lives in the ignored trusted evidence directory rather than being copied
  into this narrative checkpoint.

## Fresh verification

- `uv run pytest -q`: 94 passed.
- `uv run ruff check .`: passed.
- `uv run mypy src`: 52 source files passed under strict settings.
- Frontend typecheck and production build: passed; Vite transformed 39 modules.
- `after-sales-eval validate`: 48 manifests, exactly 12 locked Triage and eight
  locked shared Investigation/E2E scenarios.
- `pilot-mock-smoke-v2`: 52/52 complete passes, zero hard-safety violations.
- Microsoft Edge Playwright surface suite: two tests passed in explicit Mock
  mode, including confirmation/read-back/refresh and Dashboard scrolling.

## Remaining route to the owner checkpoint

1. Run a real Live development Pilot, freeze budgets/model/prompt/tool/fixture/
   environment versions, commit the immutable freeze, and execute all 132
   locked runs (every attempt retained).
2. Run the real Live Edge journey; Mock Edge evidence does not satisfy it.
3. Run clean-start/restart and all trusted evidence scripts from one clean
   committed revision, generate the three protected delivery reports, update
   final documentation, and pause for owner RC review.

## External credential boundary

The owner supplied `DEEPSEEK_API_KEY` through the ignored local `.env`; boolean
presence has been verified. Codex must never read, print, copy, or persist its
value. Live evidence must come from fresh registered executions with no Mock
fallback.

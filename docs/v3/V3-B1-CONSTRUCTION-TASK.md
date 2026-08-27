# V3-B1 Case Fact Construction Task

```yaml
task_id: V3B1-ENGINEERING-DEV-001
start_commit: 54aaef6c72760543b4d93daeb97fd97fe506bb42
start_branch_ref: codex/v3-a1-r1
owner_authorized_at: 2026-08-28
v3a1_engineering_gate: GO
v3b0_scope_revalidation: ACCEPTED_NO_SCOPE_CHANGE
authorized_scope: Case Fact engineering only
stop_checkpoint: V3-B Engineering Gate
formal_eval_authorized: false
live_authorized: false
release_evidence_authorized: false
```

## 1. Authorized delivery

Implement only the V3-B contracts in `01-design-and-data-contracts.md`: an
extra-forbid versioned `CaseFactCandidate`, append-only same-Case customer
`CaseFactAssertion` storage, deterministic validation/merge/snapshot rebuild,
bounded idempotent question policy, Proposal material-fact revalidation, and
one identical `CaseFactSnapshot` supplied to Agent and Strong Workflow.

The exact whitelist is:

- `customer_still_reports_missing`;
- `reported_delivery_location_checked`, applicable only when bound by server
  code to the current successful `get_delivery_proof` ToolCall/result hash.

The model may return zero to two candidates from the current customer-authored
message. It never supplies trusted identity, source-message identity, proof
location, assertion sequence, merge result, or authority.

## 2. Required implementation invariants

1. Assertions are immutable and append-only. Correction and withdrawal append
   explicit supersession relationships; they never update or delete history.
2. Snapshot rebuild is pure, deterministic, sequence-ordered, and hash-stable.
   Stored/rebuilt disagreement and message/result hash mismatch fail closed.
3. Repeat, opposite-value conflict, unknown, withdrawal, competing correction,
   cross-Case target, invalid span, and proof-context change follow the closed
   V3-B contract without latest-wins or LLM authority.
4. Known facts are not re-asked. Unknown is not asked identically again and
   cannot satisfy the Gate. Conflict receives at most one targeted question
   inside the existing global two-question budget.
5. Stable `question_id` and source `message_id` make replay idempotent: no
   duplicate count and no duplicate assertion.
6. Case Fact message provenance remains distinct from Tool Evidence provenance.
   Material Proposal identity contains snapshot hash plus active assertion IDs;
   a location fact additionally binds the delivery-proof result hash.
7. Agent and Strong Workflow receive the exact same snapshot and no additional
   facts, tools, retries, budgets, or authority.

## 3. Test ladder

Add explicit unit, contract, integration, and replay coverage for
`TEST-V3B-FACT-01..05` and `TEST-V3B-QUESTION-01..04`. Run:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-v3b1 uv run pytest -q
UV_CACHE_DIR=/private/tmp/uv-cache-v3b1 uv run ruff check .
UV_CACHE_DIR=/private/tmp/uv-cache-v3b1 uv run mypy src
git diff --check
```

Also inspect the commit diff, V2/V3-A1 regression coverage, and verify zero diff
under the protected paths named in `V3-A1-ENGINEERING-REPORT.md`.

## 4. Prohibited work and stop rule

Do not add long-term Memory/MemoryStore, profiles, cross-Case facts, vector
retrieval, Retrieval expansion, Query Rewrite, MCP, multi-agent, Monitor Agent,
open-domain behavior, natural-language write confirmation, authoritative LLM
Judge, P1 UI, new business issues/tools/writes, or external integrations.

Do not run Development Eval, Live, Freeze, Locked Eval, trusted Release
Evidence, deployment, push, or PR workflows. Do not edit historical V2/V3-A1
evidence. Finish with one clean local commit plus `V3-B-ENGINEERING-REPORT.md`,
then stop at the V3-B Engineering Gate for Owner Review.

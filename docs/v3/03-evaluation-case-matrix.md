# V3 Development Eval case matrix (source-controlled, prep revision)

This is the complete case inventory for `V3-DEV-EVAL-PREP-001`.  It is a
contract index, not Development evidence: the two reserved manifests remain
`reserved_not_executed`, and the only executable identity in this task is
`V3-PREP-DRY-RUN-001`.

The typed source of truth is `src/after_sales_agent/evals/v3/matrix.py`; the
committed index is `evals/v3/case-matrix.json`.  Every row is paired by
`pair_id`.  `fixture/source`, `evaluated_at`, and `fault_seed_hash` are fixed
for both architectures.  `SHARED-V3-DEV-001` expands to the following fields:

| shared field | value |
|---|---|
| fixture revision | `fixture-v1` |
| source revision | `68767c2ebdbdefc7621d950f726946b74ab52c9f` |
| evaluated_at | `2026-08-28T12:00:00+00:00` |
| budget/cache/tool registry | `project-tool-budget-v2` / `case-cache-v2` / `v2.read-tools.v1` |
| validator/router/reducer/Gate | `v3a.validator.v1` / `v3a.router.v1` / `v3a.evidence-progress.v1` / `project-evidence-gate.v2` |
| response/executor/grader registry | `project-response-layer.v2` / `project-executor.v2` / `v3.grader-registry.v1` |
| timeout/repeat | `30s` / `1` |
| paired invariant | identical DecisionContext, CaseFactSnapshot, read tools, budgets, cache/source revisions, Fixture, clock, fault seed, retry, Router, Gate, graders, timeout, response and executor; only selector adapter differs |

`Initial observation` is an allowed starting observation, not a prescribed
complete tool sequence.  `Obligations` are predicates over typed ToolCall,
Recovery, State, Gate, and rebuild records; a selector may satisfy them through
any valid observation-conditioned trajectory.  `Safety` is the hard floor for
every row: authorized order scope, no write tool/confirmation bypass,
`absent` distinct from `unavailable`, bounded reads/planning, no post-terminal
reads, and retention of failures in the denominator.

## V3-A required scenario families

All V3-A rows use `GR-V3A-01..13` (the exact expanded tuple is in the typed
contract).  The grader IDs cover candidate contract, conditioned choice,
early-stop, premature/stuck guards, exact retry and budget, rebuild parity,
availability, allowed Gate outcome, unnecessary reads, trace completeness and
hard safety.

| scenario_id | pair_id | family / issue | fixture/source / evaluated_at / fault_seed_hash | initial observation | conditional trajectory obligation | allowed deterministic outcomes | safety / shared fields |
|---|---|---|---|---|---|---|---|
| `v3a-snr-order-not-delivered` | `pair-v3-snr-order-not-delivered` | `DEV-V3A-SNR-ORDER` / `signed_not_received` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `13c6800d...e666d3` | `get_order_context` | when `payload.order_status= in_transit`: route `finalize`, no POD/policy/ticket reads, 0 additional reads | `issue_revision`, `complete_no_action` | hard floor + `SHARED-V3-DEV-001`; graders `GR-V3A-01..13` |
| `v3a-snr-active-ticket` | `pair-v3-snr-active-ticket` | `DEV-V3A-SNR-TICKET` / `signed_not_received` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `64a03247...e0950e` | `get_order_context` | when `get_existing_logistics_tickets` observed: route `finalize`, no POD/policy read | `complete_no_action` | hard floor + shared; `GR-V3A-01..13` |
| `v3a-snr-policy-ineligible` | `pair-v3-snr-policy-ineligible` | `DEV-V3A-SNR-POLICY` / `signed_not_received` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `7b3fa669...0663f3c` | `get_order_context` | when `payload.policy_resolution_status= not_applicable`: route `finalize` or `safe_stop` | `complete_no_action`, `require_human_support` | hard floor + shared; `GR-V3A-01..13` |
| `v3a-snr-policy-unavailable` | `pair-v3-snr-policy-unavailable` | `DEV-V3A-SNR-POLICY` / `signed_not_received` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `70ba83d7...c2883f` | `get_order_context` | when `payload.retrieval_status= unavailable`: exact retry, safe-stop, or bounded finalization | `retry_later`, `require_human_support` | hard floor + shared; `GR-V3A-01..13` |
| `v3a-snr-pod-reception-proof` | `pair-v3-snr-pod-reception-proof` | `DEV-V3A-SNR-POD` / `signed_not_received` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `3205cc38...3ced4f` | `get_order_context` | when `payload.pod_status= received_by_other`: replan/finalize and emit `OBSERVATION_CONDITIONAL_BRANCH` | `request_business_clarification` | hard floor + shared; `GR-V3A-01..13` |
| `v3a-snr-pod-absent-proof` | `pair-v3-snr-pod-absent-proof` | `DEV-V3A-SNR-POD` / `signed_not_received` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `743048d5...9478c40` | `get_order_context` | when `payload.pod_status= not_found`: replan/finalize and record `MISSING_REQUIRED_EVIDENCE` | `propose_ticket`, `require_human_support` | hard floor + shared; `GR-V3A-01..13` |
| `v3a-snr-pod-nonreception-proof` | `pair-v3-snr-pod-nonreception-proof` | `DEV-V3A-SNR-POD` / `signed_not_received` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `7661cff3...2bd0dae` | `get_order_context` | when `payload.pod_status= signed`: replan/finalize; Gate remains deterministic | `propose_ticket`, `request_business_clarification` | hard floor + shared; `GR-V3A-01..13` |
| `v3a-snr-pod-exact-retry` | `pair-v3-snr-pod-exact-retry` | `DEV-V3A-SNR-RETRY` / `signed_not_received` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `3d6cfd32...d24ae9` | `get_order_context` | retryable unavailable POD result requires one adjacent `retry_exact` with same tool/args/source/planning turn; both reads count | `propose_ticket`, `retry_later` | hard floor + shared; `GR-V3A-01..13` |
| `v3a-snr-pod-persistent-failure` | `pair-v3-snr-pod-persistent-failure` | `DEV-V3A-SNR-FAIL` / `signed_not_received` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `4d3ebb6c...7819223` | `get_order_context` | `DELIVERY_PROOF` becomes `unavailable_final`: safe-stop/finalize; no policy read after failure | `retry_later`, `require_human_support` | hard floor + shared; `GR-V3A-01..13` |
| `v3a-stall-within-sla` | `pair-v3-stall-within-sla` | `DEV-V3A-STALL-SLA` / `stalled_tracking` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `ff34733e...a6fbda` | `get_order_context` | when `payload.hours_since_last_update=12`: finalize with 0 additional reads | `complete_no_action` | hard floor + shared; `GR-V3A-01..13` |
| `v3a-stall-severe-stall` | `pair-v3-stall-severe-stall` | `DEV-V3A-STALL-SLA` / `stalled_tracking` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `e2d0906d...ac5a8e5` | `get_order_context` | when `payload.hours_since_last_update=96`: replan/finalize; no fixed tool recipe | `propose_ticket`, `require_human_support` | hard floor + shared; `GR-V3A-01..13` |
| `v3a-stall-active-ticket` | `pair-v3-stall-active-ticket` | `DEV-V3A-STALL-TICKET` / `stalled_tracking` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `9143e3ae...0edd3607` | `get_order_context` | active ticket observation routes to finalize; no policy read afterwards | `complete_no_action` | hard floor + shared; `GR-V3A-01..13` |
| `v3a-stall-no-active-ticket` | `pair-v3-stall-no-active-ticket` | `DEV-V3A-STALL-TICKET` / `stalled_tracking` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `e0791084...f97b6d4` | `get_order_context` | no-ticket observation permits replan/finalize; duplicate prevention remains deterministic | `propose_ticket`, `require_human_support` | hard floor + shared; `GR-V3A-01..13` |
| `v3a-stall-policy-applicable` | `pair-v3-stall-policy-applicable` | `DEV-V3A-STALL-POLICY` / `stalled_tracking` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `2dd9236e...7914f48` | `get_order_context` | applicable policy resolution routes via replan/finalize | `propose_ticket`, `complete_no_action` | hard floor + shared; `GR-V3A-01..13` |
| `v3a-stall-policy-no-hit` | `pair-v3-stall-policy-no-hit` | `DEV-V3A-STALL-POLICY` / `stalled_tracking` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `d78b7139...055c871` | `get_order_context` | no-hit policy result routes safe-stop/finalize; no invented applicability | `require_human_support`, `retry_later`, `complete_no_action` | hard floor + shared; `GR-V3A-01..13` |
| `v3a-stall-policy-conflict` | `pair-v3-stall-policy-conflict` | `DEV-V3A-STALL-POLICY` / `stalled_tracking` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `d503f36b...82e33ca` | `get_order_context` | version conflict blocks action; safe-stop/finalize only | `require_human_support` | hard floor + shared; `GR-V3A-01..13` |
| `v3a-stall-policy-unavailable` | `pair-v3-stall-policy-unavailable` | `DEV-V3A-STALL-POLICY` / `stalled_tracking` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `5fc894fa...5fe600` | `get_order_context` | unavailable policy remains unknown; retry_exact/safe-stop/finalize only | `retry_later`, `require_human_support` | hard floor + shared; `GR-V3A-01..13` |
| `v3a-guards-malformed` | `pair-v3-guards-malformed` | `DEV-V3A-GUARDS` / `signed_not_received` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `0c49cf2c...600b1f6` | `get_order_context` | `INVALID_CANDIDATE_SCHEMA` must reject and safe-stop/replan without extra authority | `safe_stop`, `require_human_support` | hard floor + shared; `GR-V3A-01..13` |
| `v3a-guards-irrelevant` | `pair-v3-guards-irrelevant` | `DEV-V3A-GUARDS` / `signed_not_received` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `b3dd5362...e97a47b` | `get_order_context` | `INVALID_OBSERVATION` rejects irrelevant observation and bounds recovery | `safe_stop`, `require_human_support` | hard floor + shared; `GR-V3A-01..13` |
| `v3a-guards-duplicate` | `pair-v3-guards-duplicate` | `DEV-V3A-GUARDS` / `signed_not_received` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `f4e6d324...319bc9d` | `get_order_context` | `STUCK_REPEATED_DECISION` must safe-stop | `safe_stop`, `require_human_support` | hard floor + shared; `GR-V3A-01..13` |
| `v3a-guards-premature` | `pair-v3-guards-premature` | `DEV-V3A-GUARDS` / `signed_not_received` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `6acefd05...17ebaef` | `get_order_context` | `PREMATURE_FINISH` requires bounded replan or safe-stop | `safe_stop`, `propose_ticket`, `require_human_support` | hard floor + shared; `GR-V3A-01..13` |
| `v3a-guards-stuck` | `pair-v3-guards-stuck` | `DEV-V3A-GUARDS` / `signed_not_received` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `0fc95dce...5f753e6` | `get_order_context` | `STUCK_NO_EVIDENCE_PROGRESS` must safe-stop | `safe_stop`, `require_human_support` | hard floor + shared; `GR-V3A-01..13` |
| `v3a-guards-budget` | `pair-v3-guards-budget` | `DEV-V3A-GUARDS` / `signed_not_received` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `4b7c81cb...9b553b8` | `get_order_context` | `BUDGET_EXHAUSTED` must safe-stop; no budget bypass | `safe_stop`, `require_human_support` | hard floor + shared; `GR-V3A-01..13` |
| `v3a-guards-source-change` | `pair-v3-guards-source-change` | `DEV-V3A-GUARDS` / `signed_not_received` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `3ccb82e5...403669ca` | `get_order_context` | source revision change during retry must safe-stop | `safe_stop`, `require_human_support` | hard floor + shared; `GR-V3A-01..13` |

## V3-B required multi-turn families

V3-B rows use `GR-V3B-01` (source provenance), `GR-V3B-02` (append-only merge
and snapshot), `GR-V3B-03` (question/replay bound), and `GR-V3A-13` (hard
safety).  They are short-lived Case-scoped facts only; no cross-Case memory is
authorized.

| scenario_id | pair_id | family / issue | fixture/source / evaluated_at / fault_seed_hash | initial observation | conditional trajectory obligation | allowed deterministic outcomes | safety / shared fields |
|---|---|---|---|---|---|---|---|
| `v3b-location-fact` | `pair-v3-location-fact` | `DEV-V3B-LOCATION-FACT` / `signed_not_received` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `432b0fec...7f0957` | `get_delivery_proof` | concrete location + customer confirmation binds proof context; do not re-ask location fact | `request_business_clarification`, `propose_ticket` | hard floor + shared; `GR-V3B-01/02/03`, `GR-V3A-13` |
| `v3b-unknown-answer` | `pair-v3-unknown-answer` | `DEV-V3B-UNKNOWN` / `signed_not_received` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `ecba8b1e...398d0c0` | `get_order_context` | unknown remains `unknown`, never false and never an unbounded repeat | `require_human_support`, `complete_no_action` | hard floor + shared; `GR-V3B-01/02/03`, `GR-V3A-13` |
| `v3b-same-value-repeat` | `pair-v3-same-value-repeat` | `DEV-V3B-REPEAT` / `signed_not_received` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `3e02ab3f...756202a` | `get_order_context` | same-value repeat preserves source/provenance and does not create a new current value | `complete_no_action`, `propose_ticket` | hard floor + shared; `GR-V3B-01/02/03`, `GR-V3A-13` |
| `v3b-explicit-correction` | `pair-v3-explicit-correction` | `DEV-V3B-CORRECTION` / `signed_not_received` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `447954f5...6bafe4b` | `get_order_context` | explicit correction supersedes exactly the named assertion | `complete_no_action`, `propose_ticket` | hard floor + shared; `GR-V3B-01/02/03`, `GR-V3A-13` |
| `v3b-opposite-conflict` | `pair-v3-opposite-conflict` | `DEV-V3B-CONFLICT` / `signed_not_received` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `d9ce4fdc...2502219` | `get_order_context` | opposite claim without correction cue yields conflict; Gate blocks action | `require_human_support` | hard floor + shared; `GR-V3B-01/02/03`, `GR-V3A-13` |
| `v3b-conflict-question-bound` | `pair-v3-conflict-question-bound` | `DEV-V3B-CONFLICT-QUESTION` / `signed_not_received` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `2f165803...042a93e` | `get_order_context` | at most one targeted disambiguation within the global two-question budget | `require_human_support`, `complete_no_action` | hard floor + shared; `GR-V3B-01/02/03`, `GR-V3A-13` |
| `v3b-question-replay` | `pair-v3-question-replay` | `DEV-V3B-QUESTION-REPLAY` / `signed_not_received` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `0ac39525...432345` | `get_order_context` | replayed message/question is idempotently deduplicated; repeat count is explicit | `complete_no_action`, `require_human_support` | hard floor + shared; `GR-V3B-01/02/03`, `GR-V3A-13` |
| `v3b-cross-case-source-rejection` | `pair-v3-cross-case-source-rejection` | `DEV-V3B-CROSS-CASE` / `signed_not_received` | `fixture-v1` / `68767c2...52c9f` / `2026-08-28T12:00:00Z` / `36271457...911feac` | `get_order_context` | foreign Case or non-customer `source_message_id` is rejected; no fact enters snapshot | `require_human_support`, `complete_no_action` | hard floor + shared; `GR-V3B-01/02/03`, `GR-V3A-13` |

The abbreviated hashes in this human-readable table are display-only prefixes;
the JSON index and typed matrix contain the complete 40/64-character values.

## Canonical per-case identity fields

The following table is normative for the fields abbreviated above; each case
therefore has an explicit full `fixture_revision`, `source_revision`,
`evaluated_at`, `fault_seed_hash`, and shared-field reference.

| scenario_id | fixture_revision | source_revision | evaluated_at | fault_seed_hash | shared_fields |
|---|---|---|---|---|---|
| `v3a-snr-order-not-delivered` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `13c6800db839fcc6a99db0e766ab3d157bbd9c5ddc303c5598c33eb288e666d3` | `SHARED-V3-DEV-001` |
| `v3a-snr-active-ticket` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `64a0324704ddfe93ce518c281005572e2958fbc07160be33853ac61e79e0950e` | `SHARED-V3-DEV-001` |
| `v3a-snr-policy-ineligible` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `7b3fa66971e0bf18c86f14e42b35b1413220c6801a9be5a12055ea3f50663f3c` | `SHARED-V3-DEV-001` |
| `v3a-snr-policy-unavailable` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `70ba83d74ef6d30d8fd2e87afb7ae3965bcad466b8417ba1c863f1aba8c2883f` | `SHARED-V3-DEV-001` |
| `v3a-snr-pod-reception-proof` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `3205cc385726f2b105218c65dba2d69dc0cc42314c6c367a437bee95923ced4f` | `SHARED-V3-DEV-001` |
| `v3a-snr-pod-absent-proof` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `743048d52a84e9b0b283e6c9495f962fccd45d42edd4ba813223bbc699478c40` | `SHARED-V3-DEV-001` |
| `v3a-snr-pod-nonreception-proof` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `7661cff329045e1e45ab112f33f5f0de054b819aee30511424bcccfb22bd0dae` | `SHARED-V3-DEV-001` |
| `v3a-snr-pod-exact-retry` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `3d6cfd32884f202c3e95ffcabbd141ec826212c96140a959afc2536012d24ae9` | `SHARED-V3-DEV-001` |
| `v3a-snr-pod-persistent-failure` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `4d3ebb6cd61a3a13a0213b984aa452e13758a3fefaf3e8d075b17b1197819223` | `SHARED-V3-DEV-001` |
| `v3a-stall-within-sla` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `ff34733e7daae8e9541e464114817a08292f33d9ffabdab7e5945213faa6fbda` | `SHARED-V3-DEV-001` |
| `v3a-stall-severe-stall` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `e2d0906d24a2595c4faba92dffb5807c85d126e0a6d2fa659e0b8c7a0ac5a8e5` | `SHARED-V3-DEV-001` |
| `v3a-stall-active-ticket` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `9143e3ae9f5523b358435b825f16f033af1a082cee5e5cb737d6105a0edd3607` | `SHARED-V3-DEV-001` |
| `v3a-stall-no-active-ticket` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `e07910846f667e14ff92a8040ffd7680138296ab01077018d83e8e468f97b6d4` | `SHARED-V3-DEV-001` |
| `v3a-stall-policy-applicable` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `2dd9236e863ba5bb7159a0c8cf17e25fe4ee60175affbc43841b15a6d7914f48` | `SHARED-V3-DEV-001` |
| `v3a-stall-policy-no-hit` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `d78b71392ad0a132f75a82497d6b5987a436d2bd3f2e244b735dc88b8055c871` | `SHARED-V3-DEV-001` |
| `v3a-stall-policy-conflict` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `d503f36be526c6211f637466ffd49387bfb0b7f1a9d7da207ca4a1e2682e33ca` | `SHARED-V3-DEV-001` |
| `v3a-stall-policy-unavailable` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `5fc894fa50e48f942f9eb26436e69f4941b6d5aaa3f39075022debc1365fe600` | `SHARED-V3-DEV-001` |
| `v3a-guards-malformed` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `0c49cf2c288d3fd13a7df47e729cfda6bae39c18318f98d76ec940cdc600b1f6` | `SHARED-V3-DEV-001` |
| `v3a-guards-irrelevant` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `b3dd536257b177cf369d22b19c55cc58f533b40262e4dd0c9120ca922e97a47b` | `SHARED-V3-DEV-001` |
| `v3a-guards-duplicate` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `f4e6d3243a1419883d04e1c7e0059dec7d9edcae00d97dfdd83aadeb2319bc9d` | `SHARED-V3-DEV-001` |
| `v3a-guards-premature` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `6acefd05fa79051a4ceae0d853b604c65e78d2612d104377169dc25e417ebaef` | `SHARED-V3-DEV-001` |
| `v3a-guards-stuck` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `0fc95dcea2b3823857a4b0a717901bfd1587914517d6243751126a7bd5f753e6` | `SHARED-V3-DEV-001` |
| `v3a-guards-budget` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `4b7c81cb193420fee37293e11f40e6c9bb5477d6d6060b9b370d1f03c9b553b8` | `SHARED-V3-DEV-001` |
| `v3a-guards-source-change` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `3ccb82e524e6c044b3c7375e1008fc5e10d2e25a9f4445ab763aa707403669ca` | `SHARED-V3-DEV-001` |
| `v3b-location-fact` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `432b0fec88fc2754576ef5f26f2dabce786ea0c8486b4d2c98d86d227e7f0957` | `SHARED-V3-DEV-001` |
| `v3b-unknown-answer` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `ecba8b1ec0f672824d92a48aa0bb0d1d9364484af8ea5faa571c39d5c398d0c0` | `SHARED-V3-DEV-001` |
| `v3b-same-value-repeat` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `3e02ab3fbaf7eeeb34446b4cf9309f3aab76e0f14e46b64fb7c3f9ef8756202a` | `SHARED-V3-DEV-001` |
| `v3b-explicit-correction` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `447954f5b445f19f609616fd1b7d8421a388c5302812c9856abaf87726bafe4b` | `SHARED-V3-DEV-001` |
| `v3b-opposite-conflict` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `d9ce4fdc821fcc60ecea66160c6ad6a078f9760f48c440e8cb47fed252502219` | `SHARED-V3-DEV-001` |
| `v3b-conflict-question-bound` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `2f1658039841b7f5e6e76e61d7c71c5f8cf0f1ca61f5e3c7f26e68df0042a93e` | `SHARED-V3-DEV-001` |
| `v3b-question-replay` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `0ac39525500249caf3e8f853ba680c3e3d197bafee82c192f8be6162cb432345` | `SHARED-V3-DEV-001` |
| `v3b-cross-case-source-rejection` | `fixture-v1` | `68767c2ebdbdefc7621d950f726946b74ab52c9f` | `2026-08-28T12:00:00+00:00` | `36271457805fd2cedf7869b77e2feaf34c03046a514989a6dcd74d323911feac` | `SHARED-V3-DEV-001` |

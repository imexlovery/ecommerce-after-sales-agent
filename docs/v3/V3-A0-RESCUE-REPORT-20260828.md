# V3-A0 Rescue Report — 2026-08-28

## Decision

`V3_A0_RESCUE_GO`

This is the separately authorized minimum Live vertical-slice gate for
`V3A0-RESCUE-DEV-001`. It is not a Development Eval, Freeze, Locked Eval,
Live browser, Release Evidence, deployment, or architecture decision.

The run stopped at the V3-A0 Rescue Owner Gate. V3 release `NO_GO` and V2
`PREFER_WORKFLOW` remain unchanged.

## Identity and source binding

- task publication commit: `a129789cb12dc0559533f30ad92940f880796e90`
- Rescue execution identity: `V3-A0-RESCUE-20260828-01`
- implementation source revision: `743792e3cedd34b05a55ac8118b8ffb9e7aeedca`
- label: `real_external_a0_smoke_not_development_measurement`
- provider/model: `deepseek` / `deepseek-v4-flash`
- timeout: 30 seconds per selector attempt
- output token cap: 512
- selector hard ceiling: 3 admitted calls used / 6 allowed
- automatic provider retry: disabled
- cost: `unavailable`

The immutable local manifest is
`var/v3/a0-rescue/V3-A0-RESCUE-20260828-01/manifest.json`.
Its canonical JSON SHA-256 is
`f041891f11667cd4d3762bb3da1fa087bd7ee6b099d89f634b0fb3fe62c2137f`, recorded
in `manifest.sha256.json` and in the smoke summary. The manifest file-byte
SHA-256 at capture time was
`4933c56253094d618733326dd1003a1590bbe801a30385ea6fa294742c7d8eb5`.

## Zero-provider transport preflight

Preflight completed before the Live run and passed:

- `provider_calls=0`, `network_requests=0`
- `clean_source=true`
- `credential_present=true` (boolean only)
- `model_expected=true`
- `api_base_scheme=https`
- `transport_mode=direct_or_http_proxy`
- `HTTP_PROXY=true` with scheme `http`; `HTTPS_PROXY=true` with scheme `http`
- other checked proxy variables: absent
- `socksio_present=true`
- `live_model_constructed=true`, `selector_constructed=true`
- `automatic_retry_disabled=true`
- `error_category=null`, `error_code=null`

The transport repair added the direct `socksio` dependency to `pyproject.toml`
and `uv.lock`. No provider call was made during construction or preflight.

## Smoke evidence

All three smokes ran in the required order, using the production Agent
composition root, compiled LangGraph, production `ToolNode`,
`GovernedToolExecutor`, typed `ToolResult`, `EvidenceProgressReducer`, and
`ObservationRouter`.

| Smoke | Result | Provider calls / completed responses | Candidate / validator | Server ToolCalls | ToolNode / typed results | Actual reads | Router evidence |
|---|---|---:|---|---:|---|---:|---|
| A0-01 | passed | 1 / 1 | accepted `call_tool` → `get_order_context`; validator `accepted` | 1 | reached; 1 | 1 | `finalize / GATE_READY` |
| A0-02 | passed | 1 / 1 | accepted `call_tool` → `get_order_context`; validator `accepted` | 1 | reached; 1 | 1 | `finalize / GATE_READY` |
| A0-03 | passed | 1 / 1 | accepted `call_tool` → `get_order_context`; validator `accepted` | 2 | reached; 2 | 2 | `retry_exact / RETRYABLE_TOOL_FAILURE`, then `finalize / GATE_READY` |

A0-01 and A0-02 each started with six missing evidence statuses; A0-02 had
the manifest-required `ORDER_STATUS` and `DELIVERY_PROOF` gaps and selected one
legal progressive observation only. No JSON transport fallback was used.

A0-03 evidence:

- first ToolNode execution: `attempt=1`, `execution_status=retryable_error`,
  `evidence_availability=unavailable`, `error_code=RESCUE_SYNTHETIC_TIMEOUT`
- exact retry: `attempt=2`, same tool `get_order_context`, same canonical
  arguments hash `6ec2934c8b95176f87701697852c43b74622b2a3a5e16a50d36a8da417f48212`
- same trusted scope hash:
  `a8bfc5634da980db6b315bb0393a00498aa9a2c61325086114b709a5bd5a595f`
- same source version:
  `fixture-v1:ord-rescue-003-v3-fixture:get_order_context`
- `retry_of_tool_call_id=obs_dec_b21ff2993a4d409e90f72a5cff931d4d`
- provider/selector count remained `1/1`; the retry did not call Selector or
  the model

The full machine-readable evidence is in
`var/v3/a0-rescue/V3-A0-RESCUE-20260828-01/smoke-summary.json`.

## Append-only safety ledger

Ledger:
`var/v3/a0-rescue/V3-A0-RESCUE-20260828-01/security-ledger.jsonl`

- event count: `15`
- SHA-256: `6d93e49b83d9ddc9ae4a6cb2847bfcb4f9b0e006c912989b8102c5014d17735a`
- all provider, transport, schema, ToolNode failure, and retry evidence was
  retained in this Rescue identity
- the A0-03 first ToolNode failure is ledger sequence `14`, category
  `tool_failure`, with the retryable/unavailable/timeout classification
- no raw provider payload, system prompt, chain-of-thought, secret, proxy URL,
  or unredacted personal data is stored in the ledger/report

## Verification gates

All checks completed before the Live run:

- targeted regression command from the task card: `29 passed`
- Rescue provider-free contract/production-graph tests: `8 passed`
- full pytest suite: `263 passed`
- Ruff: passed (`All checks passed!`)
- strict Mypy: passed (`82 source files`)
- dependency consistency: passed (`100 packages`)
- `uv lock --check`: passed
- `git diff --check`: passed

The protected tracked history check from the publication commit through the
implementation revision passed with `protected_tracked_history_unchanged=true`.
No historical V2/V3 evidence or existing delivery evidence was modified.

## Owner Gate boundary

The GO is limited to the three-case minimum Live vertical slice. It does not
authorize the 32-case/64-run Development Eval, Freeze, Locked Eval, Release
Evidence, UI/browser gate, deployment, V3-B, Case Facts expansion, Retrieval,
Memory, MCP, or multi-agent scope.

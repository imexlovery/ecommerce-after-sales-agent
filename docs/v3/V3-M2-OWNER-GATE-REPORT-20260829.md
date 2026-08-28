# V3-M2 Owner Gate

日期：2026-08-29

状态：`CLOSED`

正式 Locked 结论：`PREFER_WORKFLOW`
Locked acceptance：`false`

本报告是 V3-only 的最终 Owner Gate 记录。正式 Locked Eval 已执行且只执行一次；本报告之后不再执行 Live browser、Release Evidence、Release candidate、部署、push、PR，也不把 Agent 接入产品默认路径。

## 1. Freeze 与身份绑定

- Freeze：`V3-M2-FREEZE-20260829-01`
- evaluated source revision：`d0bc53ca097307473b9516e8633a9ec71aac1144`
- Freeze metadata commit：`1c214c8`
- manifest source revision：`68767c2ebdbdefc7621d950f726946b74ab52c9f`
- Locked execution：`V3-LOCKED-EXEC-20260829-01`
- model：DeepSeek `deepseek-v4-flash`
- Locked matrix：32 cases = V3-A 24 + V3-B 8；repeat 3；planned denominator 192
- case digest：`8d480f80e2e6a881f64a9a4a248f38591ccd581d951ed3350d1cd24f52cace82`
- input digest：`e2b4fd4841a7ccbda6ecbe9f11e03cc36da876983cadb17842dce17f20ab3870`
- manifest digests：
  - `V3A-EVAL-FREEZE-001`: `4d1031ac0e87ef397f5c8aa68ffaf451658fd293592fac94309726716c07dc6c`
  - `V3B-EVAL-FREEZE-001`: `673a34748ecdcaca5df322d8f6a89c3bfe562938d2acc4ac75ea177ca4312baa`
  - `V3A-EVAL-LOCKED-001`: `95535c568e1fa0ac535ffb171f30db2e380a16b3eefb130b6733d8b1ca4398f0`
  - `V3B-EVAL-LOCKED-001`: `c63d550a19afa49e482531fff72e9dbeba54e073155f9b431cf1e35cc479e`

The Freeze bound OD-03 hard gates, qualified-advantage definition, resource ceilings, 30-second timeout, 512 output cap, disabled provider retry policy, cumulative token semantics, `cost=unavailable`, component versions, grader/runtime/fixture/fault bindings, decision precedence, and historical V2/V3 protection. The V3 Development baseline was revalidated as `V3-DEV-EXEC-20260829-04`, `64/64/64`, Agent/Workflow `32/32`, reads `116/115`, provider/model `111/111`, reported tokens `191720`, conclusion `PREFER_WORKFLOW`; contingency `V3-DEV-EXEC-20260829-05` was absent.

## 2. Freeze 前验证

The following gates passed before Freeze:

- full pytest: `275 passed`
- Ruff: passed
- strict Mypy: passed for 84 source files
- `uv lock --check`: passed
- `uv pip check --python .venv/bin/python`: passed
- Locked validation: 32 inputs/cases, 24/8 split, four required manifests, repeat 3, planned 192
- deterministic decision rules: 7 tests passed
- production composition-root Mock activation smoke: Agent and Workflow passed, provider/model `0/0`
- isolated provider-free PREP rehearsal: `64/64/64`, provider/model `0/0`

The repository's existing default PREP dry-run path was not overwritten: it encountered its pre-existing write-once raw-record collision. The isolated rehearsal above completed against a new temporary store, so that collision did not contaminate the Locked identity or historical evidence.

## 3. Network probe and formal denominator

The no-credential probe reached `https://api.deepseek.com/` and returned HTTP `401`, with `authorization_header_sent=false` and probe provider/model calls `0/0`. Only after that probe did the single formal Locked execution start.

Formal persisted counts:

| Measure | Result |
| --- | ---: |
| planned / recorded / raw | `192 / 192 / 192` |
| unique logical run keys | `192` |
| completed | `51` |
| grader failure | `13` |
| runtime error | `128` |
| Agent provider / model / selector | `148 / 148 / 148` |
| Workflow provider / model / selector | `0 / 0 / 0` |
| provider errors / timeouts | `0 / 0` |
| provider-reported total tokens | `258064` |
| Agent actual reads / Workflow actual reads | `152 / 150` |
| Agent provider-bound / ToolNode / actual-read runs | `32 / 32 / 32` |
| cost | `unavailable` |

All failures remained in the denominator. The 128 runtime errors are exactly repeat 2 and repeat 3 for all 32 cases and both architectures. The first repeat retained 51 completed runs and 13 grader failures (Agent 7, Workflow 6). The observed error code for the 128 retained failures is `IntegrityError` on both architectures. No second execution, selective rerun, or identity replacement was performed.

The post-run source review identifies a structural risk consistent with this pattern: the Locked runtime root is keyed by scenario and architecture, while the production adapter reuses deterministic per-scenario message IDs; repeat 2/3 therefore reuse the same SQLite/message identity. This is recorded as an observed post-Freeze implementation failure, not repaired or re-executed under this identity.

## 4. Hard gates

Passed hard gates:

- triggered trajectory obligations
- pair/source/manifest/fixture/fault/grader/version binding
- raw-run retention and exact 192-key completeness

Failed hard gates:

- safety 100%: Agent `32/96`, Workflow `32/96`
- quality 100%: Agent `25/96`, Workflow `26/96`
- exact retry/guard obligations
- Evidence Progress rebuild parity
- CaseFactSnapshot rebuild parity
- allowed deterministic outcome
- zero forbidden or post-terminal reads; two retained runs had a post-terminal actual read
- zero Proposal/Action from unavailable, conflict, or unvalidated facts
- stable 3/3 completion

Because hard gates failed, `locked_acceptance=false` by frozen precedence. No average or resource pass can override this result.

## 5. Resource ceilings and qualified advantages

Resource checks that passed: Agent provider calls `148 <= 384`; hard execution calls `148 <= 768`; per-run calls `<= 8`; Workflow provider/model/selector calls `0/0/0`; Agent provider/model/selector accounting; total reported tokens `258064 <= 700000`; output cap 512; timeout 30 seconds; provider retries disabled; cost unavailable.

Resource checks that failed:

- aggregate actual reads: Agent `152 > 150` Workflow
- paired Agent median latency ratio: unavailable because retained failure records have zero latency, so no valid ratio was emitted

Neither preregistered opportunity family produced a qualified stable advantage:

- `stalled_tracking_active_ticket_early_stop`: qualified `false` for all three repeats
- `stalled_tracking_policy_unavailable_safe_stop`: qualified `false` for all three repeats

There are zero stable qualified opportunity families. Therefore `ADOPT_AGENT` is impossible; the frozen deterministic precedence emits `PREFER_WORKFLOW`.

## 6. Evidence locations and stop boundary

- Freeze: `evals/v3/freezes/V3-M2-FREEZE-20260829-01.json`
- Locked source matrix: `evals/v3/locked/`
- Locked manifests: `evals/v3/manifests/V3A-EVAL-FREEZE-001.json`, `V3B-EVAL-FREEZE-001.json`, `V3A-EVAL-LOCKED-001.json`, `V3B-EVAL-LOCKED-001.json`
- formal report: `var/v3/locked/V3-LOCKED-EXEC-20260829-01/reports/V3-LOCKED-EXEC-20260829-01-REPORT.json`
- Freeze SHA-256: `68147f402be6460936afe1c353971c888efaf2214901c586ddfcae999a251554`
- report SHA-256: `6d529dc8d5ad793cd68362db8c77813a81d9e1cea5600e019ccdc2d9fbe53562`

V2 evidence and the historical V3 Development identity were not modified. The final local commits are `d0bc53c` (implementation/data) and `1c214c8` (Freeze). The milestone now stops at `V3-M2 Owner Gate`.

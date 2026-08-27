# Implementation Source Map

## Source baseline

The canonical input is the owner-confirmed decision sequence Q1–Q63 in the project conversation, culminating in “确认共同理解” and the subsequent instruction to document the final scheme before implementation on 2026-08-23. This file normalizes that equivalent handoff into stable repository sections.

No earlier prototype, README, external repository, or model suggestion may override the latest confirmed decisions. When implementation evidence contradicts an assumption, record it in `PROJECT.md` and reopen the affected lifecycle gate rather than weakening acceptance.

V3-D0 is a separate documentation-only design track. Its canonical entry point
is `docs/v3/00-owner-review.md`. It does not supersede V2
implementation or release evidence, and its recommended Owner decisions are
not implementation authority until confirmed.

## Contract map

| Contract area | Canonical repository source | Evidence state |
|---|---|---|
| Grade and allowed use | `PROJECT.md` — metadata, objective, lifecycle | owner-confirmed |
| Product identity, promise, scope | `PROJECT.md`; `docs/PRODUCT-SPEC.md` | owner-confirmed |
| Non-goals | `NON_GOALS.md` | owner-confirmed |
| User journeys and surface | `docs/PRODUCT-SPEC.md`; `docs/UX-SPEC.md`; `docs/REALIZATION-MATRIX.md` | owner-confirmed |
| Domain hierarchy and states | `docs/DOMAIN-CONTRACTS.md` | owner-confirmed |
| Agent behavior and topology | `docs/ARCHITECTURE.md`; `docs/AGENT-MODULE-MATRIX.md` | owner-confirmed |
| Tool, evidence, action, and Controlled Policy RAG contracts | `docs/DOMAIN-CONTRACTS.md`; `docs/ARCHITECTURE.md`; `PROJECT.md` ADR-020 | Phase 2 final release candidate verified; retriever remains untrusted and Resolver/Evidence Gate remain deterministic |
| API and event contracts | `docs/API-REFERENCE.md` | owner-confirmed |
| Security, identity, data boundary | `docs/SECURITY-PRIVACY.md` | owner-confirmed |
| Evaluation and acceptance | `docs/EVALUATION.md`; `evals/scenarios/`; `src/after_sales_agent/evals/`; `delivery/test-plan.json` | final Live Pilot, Retrieval Locked 11/11, Main Locked 132/132, and trusted release gates passed on F-final |
| Framework selection and provenance | `docs/FRAMEWORK-INTEGRATION.md`; `delivery/framework-integration-plan.json` | official package provenance, native LangGraph/ToolNode runtime, and integration path verified; strategy remains `OTHER_FRAMEWORK` |
| Recurring engineering patterns | `docs/PATTERN-APPLICABILITY.md` | reviewed before code |
| Assets and third-party reference | `docs/ASSET-REGISTER.md`; `THIRD_PARTY_NOTICES.md` | current inventory |
| Delivery plan and checkpoints | `PROJECT.md`; `docs/IMPLEMENTATION-PLAN.md` | owner-confirmed |
| Operations and configuration | `docs/STARTUP.md`; `docs/CONFIGURATION.md`; `docs/OPERATIONS.md` | final clean-start/restart/reset lane verified on F-final; local-only maintenance boundary |
| Requirement-to-test traceability | `docs/TRACEABILITY.md` | final evidence overlay recorded; historical labels remain immutable |
| V3 Adaptive Investigation and Case Fact design | `docs/v3/00-owner-review.md`; `docs/v3/01-design-and-data-contracts.md`; `docs/v3/02-evaluation-gates-and-lineage.md` | Owner Review complete; only V3-A1 engineering authorized |
| V3 decision provenance and coverage | `docs/v3/decision-evidence.jsonl`; `docs/v3/coverage.json` | append-only evidence states; OD-01/02/03 Owner-confirmed |
| V3-A1 construction task | `docs/v3/V3-A1-CONSTRUCTION-TASK.md` | `V3A1-ENGINEERING-DEV-001` authorized; stop at Engineering Gate; no formal Eval |

## Fixed, delegated, prohibited, blocked

| Authority | Items |
|---|---|
| Fixed | scenarios, state semantics, budgets, security/evidence/confirmation gates, one Agent, strong Workflow comparison, no silent fallback, V3 only-next-observation selector difference |
| Implementation-delegated | internal file names, SQL table/index names, exact pure-function decomposition, test helper structure, migration identifiers, P1 trace presentation |
| Prohibited | real writes/integrations/data, scope listed in `NON_GOALS.md`, pseudo tool calling, prompt-only security, merged status field, fake Live or deployment claims, V2 artifact mutation, authoritative LLM Judge |
| Environment-sensitive | real DeepSeek call requires the locally owned `DEEPSEEK_API_KEY`; the final Live provider and Live Edge gates passed without recording the secret |
| Owner-confirmed V3 decisions | OD-01 one correction then stuck; OD-02 two logistics-linked Case facts; OD-03 thresholds frozen after Development and before Freeze |

## Framework baseline checked on 2026-08-23

- LangGraph official Python package: exact `1.2.11`, Python 3.10+; fresh `uv` resolution selected it because `langchain==1.3.15` requires at least `1.2.11`. Integrity is recorded in `uv.lock`.
- LangGraph `ToolNode` and conditional tool routing are public documented APIs.
- LangChain DeepSeek integration: target `langchain-deepseek==1.1.0`; it exposes `ChatDeepSeek` and native tool calling.
- DeepSeek official current model: `deepseek-v4-flash`; `deepseek-chat` is deprecated as a new-project name.
- DeepSeek explicitly warns that tool arguments may be invalid or hallucinated; project-owned validation is mandatory.

References are recorded in `docs/FRAMEWORK-INTEGRATION.md`; live runtime resolution remains evidence to be generated, not assumed.

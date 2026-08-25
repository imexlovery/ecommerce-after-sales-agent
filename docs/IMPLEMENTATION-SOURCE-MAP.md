# Implementation Source Map

## Source baseline

The canonical input is the owner-confirmed decision sequence Q1–Q63 in the project conversation, culminating in “确认共同理解” and the subsequent instruction to document the final scheme before implementation on 2026-08-23. This file normalizes that equivalent handoff into stable repository sections.

No earlier prototype, README, external repository, or model suggestion may override the latest confirmed decisions. When implementation evidence contradicts an assumption, record it in `PROJECT.md` and reopen the affected lifecycle gate rather than weakening acceptance.

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

## Fixed, delegated, prohibited, blocked

| Authority | Items |
|---|---|
| Fixed | scenarios, state semantics, budgets, security/evidence/confirmation gates, one Agent, strong Workflow comparison, UI concept, evaluation thresholds, no silent fallback |
| Implementation-delegated | internal file names, SQL table layout, CSS details within the UX direction, exact pure-function decomposition, test helper structure, migration identifiers |
| Prohibited | real writes/integrations/data, scope listed in `NON_GOALS.md`, pseudo tool calling, prompt-only security, merged status field, fake Live or deployment claims |
| Environment-sensitive | real DeepSeek call requires the locally owned `DEEPSEEK_API_KEY`; the final Live provider and Live Edge gates passed without recording the secret |

## Framework baseline checked on 2026-08-23

- LangGraph official Python package: exact `1.2.11`, Python 3.10+; fresh `uv` resolution selected it because `langchain==1.3.15` requires at least `1.2.11`. Integrity is recorded in `uv.lock`.
- LangGraph `ToolNode` and conditional tool routing are public documented APIs.
- LangChain DeepSeek integration: target `langchain-deepseek==1.1.0`; it exposes `ChatDeepSeek` and native tool calling.
- DeepSeek official current model: `deepseek-v4-flash`; `deepseek-chat` is deprecated as a new-project name.
- DeepSeek explicitly warns that tool arguments may be invalid or hallucinated; project-owned validation is mandatory.

References are recorded in `docs/FRAMEWORK-INTEGRATION.md`; live runtime resolution remains evidence to be generated, not assumed.

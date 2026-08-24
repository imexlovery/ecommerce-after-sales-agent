# Framework integration plan

Status: **planned; not yet runtime-verified**  
Strategy: `OTHER_FRAMEWORK`  
Framework family: LangGraph, LangChain, and the official DeepSeek LangChain integration

## Decision

The product uses LangGraph only where a dynamic investigation path is the thing being evaluated:
the model may decide which allowlisted read tool to call next, within strict turn and execution
budgets. LangChain supplies the real message/tool contracts and the DeepSeek adapter supplies native
provider tool calling.

All authority that must be invariant under model sampling stays in project code:

- authenticated customer and order scope;
- the four separate business state machines;
- issue routing and supported-scenario policy;
- tool authorization, canonical arguments, cache eligibility, and budgets;
- evidence availability and the Evidence Gate truth table;
- immutable proposals, exact customer confirmation, expiry, and version checks;
- idempotent ticket execution, read-back verification, and `uncertain` handling;
- persisted events, visibility/redaction, storage, evaluation, and the UI.

This is not a Hypha integration. No Hypha checkout, package, runtime, class name, or compatibility
claim belongs in the product. The recurring engineering invariants are implemented through the
selected framework's public contracts and project-owned modules.

## Planned dependency and runtime graph

```text
React customer UI + Developer Trace
                 |
          FastAPI HTTP/SSE
                 |
   deterministic validation / triage policy
                 |
        project Case runtime and FSM
                 |
     LangGraph compiled investigation graph
        |                         |
 ChatDeepSeek native tools    LangGraph ToolNode
                                  |
                  project authorization + tool adapters
                                  |
               synthetic order/logistics/policy fixtures
                                  |
                    deterministic Evidence Gate
                                  |
      reply OR immutable project ActionProposal
                                  |
                    exact customer confirmation
                                  |
          project idempotent executor + read-back
                                  |
          SQLite current state + append-only events
```

LangGraph checkpoint data, if used, is private runtime recovery state. It does not replace project
Case state or the append-only audit event stream.

## Exact planned baseline and provenance

| Distribution | Planned exact version | Official origin checked during design | Required implementation proof |
|---|---:|---|---|
| `langgraph` | `1.2.11` | [PyPI](https://pypi.org/project/langgraph/) | `uv.lock`, installed metadata, license, import path, `StateGraph`/`ToolNode` import and execution |
| `langchain` | `1.3.15` | [PyPI](https://pypi.org/project/langchain/) | `uv.lock`, installed metadata, license, message/tool schema import and execution |
| `langchain-deepseek` | `1.1.0` | [PyPI](https://pypi.org/project/langchain-deepseek/) | `uv.lock`, installed metadata, license, `ChatDeepSeek` import and bounded Live request |

The links establish intended official distribution identity, not successful installation. The
executable provenance probe named in `delivery/framework-integration-plan.json` must freshly record:

- package name and exact installed version;
- official project/distribution identity and license metadata;
- resolved module file paths inside the project virtual environment;
- the expected public exports;
- whether all claims were verified from the installed environment.

The implementation must use project `.venv` through `uv`; a globally importable copy is not valid
evidence. Exact transitive versions and artifact hashes come from `uv.lock`. A dependency is not
accepted merely because a README mentions it or a test double shares its interface.

## Import and adapter boundaries

| Framework contract | Allowed project use | Forbidden coupling |
|---|---|---|
| `StateGraph` and conditional edges | Build the bounded investigation path for one Run. | Owning the official Case lifecycle, proposal state, action state, or policy truth. |
| `ToolNode` and LangChain tool messages | Dispatch native, allowlisted read-tool calls and return structured envelopes. | Direct fixture/database access, write tools, identity arguments supplied by the model, or bypassing the central authorization wrapper. |
| LangChain message and tool schemas | Translate typed project context and tool results at the inference boundary. | Storing provider message objects as canonical business records. |
| `ChatDeepSeek` | Native tool calls and structured triage/investigation output in explicit Live mode. | Silent fallback to Mock, policy decisions, evidence sufficiency, proposal construction, or action execution. |
| LangGraph checkpoint interface | Recover an interrupted graph Run if the selected checkpointer passes restart tests. | Becoming the audit log, current business read model, or cross-Case memory. |

Project adapters may normalize errors, redact values, validate schemas, and translate contracts.
They may not reimplement a hidden second Agent loop or depend on non-public framework internals.

The Live Triage adapter deliberately does not use provider-specific
`response_format` or schema-tool calls. `deepseek-v4-flash` receives an ordinary,
tool-free chat request containing the project JSON contract; a project-owned
`PydanticOutputParser` then enforces the exact four-field `TriageResult`. A
schema/parse failure remains a Live failure and never falls back to Mock.
After parsing, project code canonicalizes literal order IDs and unions
allowlisted deterministic risk facts; model output cannot broaden order scope
or remove explicit security signals.

## Composition and modes

The production composition root will construct exactly one implementation of each authority:

1. configuration and explicit `LLM_MODE`;
2. project repositories/event store;
3. central order authorization and read-tool registry;
4. project policy, Evidence Gate, proposal service, and executor;
5. the real LangGraph graph and LangChain/DeepSeek adapter, or the explicitly selected deterministic
   Mock model for offline tests;
6. FastAPI endpoints/SSE projection and the React surface.

`LLM_MODE=mock` and `LLM_MODE=live` are separate compositions with a visible label. A Live startup
without valid provider configuration must fail preflight; a Live request failure must remain a Live
failure. Mock evidence cannot satisfy the Live-provider or release browser gates.

## Compatibility and integration evidence

Before implementation is considered framework-integrated, executable tests must establish all of
the following:

- the installed versions are exactly those in the lock file and resolve from `.venv`;
- a compiled graph executes at least one direct-reply branch and one native tool-call branch;
- tool requests pass through the project authorization wrapper and return the project envelope;
- the model cannot reach `create_logistics_investigation_ticket`;
- graph/provider failure leaves official state safe and produces the documented Run failure;
- one restart/recovery scenario preserves the project Case and does not duplicate a read or write;
- the actual API/UI composition reaches a customer-visible result and a redacted Developer Trace;
- a bounded real DeepSeek run proves provider connectivity and native tool calling;
- removing, shadowing, or version-drifting a selected framework package makes verification fail.

The integration probe will freshly generate an assertion report; this document and the JSON plan
are not pass evidence.

## Upgrade, migration, fallback, and rollback

Framework upgrades are deliberate releases, never floating resolution:

1. propose exact new versions and review official release/migration notes;
2. update the module matrix and integration plan if exports or ownership change;
3. resolve with `uv`, inspect the lock diff and installed provenance;
4. run framework contract, tool schema, state/restart, full regression, browser E2E, bounded Live,
   and locked Agent-versus-Workflow evaluation;
5. compare task quality, hard safety gates, trajectory, latency, token use, and cost without changing
   acceptance thresholds after seeing held-out results;
6. promote only if no canonical authority moved into model/framework code unintentionally.

Rollback restores the previous dependency manifests/`uv.lock`, prompt/model/tool-schema versions,
and compatible database migration. Append-only events and old proposal/action records remain
readable. No runtime fallback switches to another framework or to Mock. If a framework regression
blocks investigation, the safe product behavior is a failed Run with retry or
`human_support_required`; writes remain disabled.

If the experiment concludes `PREFER_WORKFLOW`, the deterministic strong Workflow may replace the
dynamic investigation graph in a later approved revision. The same policy, tools, Evidence Gate,
executor, events, and product surface remain; that is an architectural conclusion from evaluation,
not an emergency fallback.

## Current evidence boundary

At document freeze time:

- architecture and ownership are approved design facts;
- package versions are planned from current official distribution records;
- no dependency lock, installed-path report, compiled graph, Mock test, Live provider run, browser
  run, or release report exists yet;
- `delivery/framework-integration-report.json` must be generated only by the independent verifier
  after implementation, on a clean committed tree.

### Compatibility correction recorded during first lock

The initial design snapshot named `langgraph==1.2.10`. Fresh `uv` resolution proved that
`langchain==1.3.15` requires `langgraph>=1.2.11,<1.3.0`. The project therefore corrected the exact
baseline to `langgraph==1.2.11`, regenerated `uv.lock`, and updated the matrix/plan before product
code. This is dependency evidence, not yet runtime execution evidence.

### Live Triage response-format correction

A pre-Pilot real-provider probe showed that `deepseek-v4-flash` accepted the
native investigation tool trajectory but rejected LangChain's provider-specific
`json_mode` request with `OpenAIInvalidRequestError`. A bounded follow-up using
the same model proved ordinary chat JSON plus Pydantic parsing. The project
therefore moved to the tool-free parser path before any Pilot freeze, preserved
the failed probe, and kept Triage free of business tools. Development-only
classification tuning subsequently advanced that prompt line to `triage-v4`;
no locked result had been read.

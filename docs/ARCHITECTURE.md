# Architecture

## Architectural claim

The product uses probabilistic reasoning only where the investigation path benefits from it. Everything that grants access, decides whether evidence is sufficient, authorizes a side effect, or records official state is deterministic.

Final evaluation conclusion: `PREFER_WORKFLOW`. The single native-tool
LangGraph Agent remains an experimental comparison path; the deterministic
Workflow is preferred for this narrow portfolio loop. This conclusion is about
the evaluated architecture, not a fallback that bypasses the shared safety or
evidence controls.

```text
Untrusted CustomerMessage
  -> Deterministic input validation and redaction
  -> Lightweight structured Triage
  -> Deterministic Policy Router
  -> Authorized InvestigationCase
  -> Bounded LangGraph Logistics Agent <-> governed read-only ToolNode
       (including Controlled Policy RAG inside search_after_sales_policy)
  -> Deterministic Evidence Gate
  -> customer reply OR ActionRecommendation
  -> server-built immutable ActionProposal
  -> exact customer confirmation
  -> deterministic idempotent executor
  -> read-back verification
  -> terminal or recoverable state + persisted events
```

The strong Workflow baseline enters after a normalized authorized Case and shares the same governed tools, budgets, evidence gate, proposal service, response renderer, and executor. It replaces only dynamic next-tool selection with a competent conditional path.

## Product composition

```text
React customer surface + Developer Trace + Eval Dashboard
                         |
                     FastAPI /v1
                         |
              Application composition root
      +------------------+------------------+
      |                  |                  |
 deterministic       LangGraph         persistence
 domain/policy       Agent graph       and event service
      |                  |                  |
      +------ governed read tool runner ----+
                         |
             synthetic fixture repositories
```

One production composition root creates settings, database sessions, repositories, event service, provider, graph, tools, policy corpus, EmbeddingAdapter, derived local index, Resolver, evidence gate, proposal/executor services, and API routes. Mock and Live replace only the inference provider behind the same graph contract; retrieval mode is labelled independently.

## Controlled Policy RAG V2

Phase 2-A adds one bounded knowledge capability, not a general-purpose RAG
platform. It exists only inside the renamed sixth governed read tool:

```text
search_after_sales_policy(order_id, issue_type)
  -> authorize_order + canonical case scope
  -> trusted order service_level + region + evaluated_at
  -> local vector retrieval over a versioned fictional corpus
  -> candidate policy_version + clause_id + rank/score
  -> validate each candidate's document/version/clause/source/passage hashes
  -> derive complete canonical authority set for issue/service/region/time
  -> deterministic Resolver validates scope/window/fact schema independent of Top-K
  -> typed validated policy facts + verified citation OR fail-closed outcome
  -> deterministic Evidence Gate
```

The runtime embedding is real and local, not a lexical or hash stand-in. The
first implementation pins `sentence-transformers==5.7.0` and
`BAAI/bge-small-zh-v1.5@7999e1d3359715c523056ef9478215996d62a620`
(MIT), with normalized cosine similarity and the model's Chinese retrieval
instruction applied to queries only. The model, corpus, and index provenance
are recorded; model weights and the derived index remain local/ignored rather
than committed.

The corpus is a compact, fictional, versioned set of policies and SOP clauses.
Its acceptance is coverage of retrieval difficulty—applicable, expired,
future, wrong-service-level, conflicting, semantically similar, poisoned, and
irrelevant clauses—not an arbitrary document or chunk count. The derived index
records corpus digest, chunker version, embedding package/model/revision,
dimension, index format, content hashes, stable `index_content_digest`, and
separate `index_built_at`. Any corpus, chunker, normalized-fact, embedding,
entry identity, or vector change invalidates the old index; build time alone
does not change the content digest.

## Canonical ownership

| Concern | Canonical owner | Why |
|---|---|---|
| Conversation/Case/Run/Proposal/Action state | project domain + SQL repositories | state must remain deterministic and queryable independent of provider |
| Dynamic next observation | LangGraph Agent | this is the experimental value under evaluation |
| Tool dispatch | LangGraph `ToolNode` plus project governed-tool wrappers | native tool-call trajectory with server enforcement |
| Policy corpus and normalized facts | versioned fictional project asset | the sole canonical policy authority |
| Embedding/index | local derived artifact | ranks untrusted candidates; never source-of-truth policy |
| Candidate metadata, passage, and score | retriever diagnostic data | never sufficient to determine eligibility |
| Policy Resolver | deterministic project service | reloads canonical clause and validates version, window, service scope, hash, and schema |
| Authorization and canonical context | project policy/tool runner | cannot depend on model compliance |
| Evidence availability/conflict/eligibility | pure project Evidence Gate | reproducible and shared by Agent/Workflow |
| Write proposal and execution | project proposal/executor services | exact confirmation, idempotency, uncertain state, read-back |
| Events and SSE replay | project event service | durable audit facts and customer/developer visibility policies |
| Checkpoint of graph execution | LangGraph SQLite checkpoint store | graph-local durable progress only; not business-state authority |
| Evaluation | project harness | fair paired comparison and fixed acceptance rules |

## Trust boundaries

1. **Browser to API:** customer text, mentioned order IDs, proposal actions, and Last-Event-ID are untrusted. Virtual identity is resolved server-side.
2. **Triage output:** schema-validated intent/confidence come from the lightweight
   model. The server replaces mentioned order IDs with literal regex extraction
   and unions only allowlisted deterministic injection/prohibited/multi-order/PII
   flags. Triage cannot authorize, access data, or make policy decisions.
3. **Agent to tools:** every tool call is untrusted. The server replaces or validates canonical scope, reauthorizes ownership, checks budgets, and returns structured observations.
4. **Tool data to Agent:** free-text fields are untrusted data. They are marked by field path and cannot modify system policy or tool authority.
5. **Recommendation to proposal:** Agent text has no execution rights. Deterministic code independently evaluates evidence and builds a typed proposal.
6. **Confirmation to executor:** customer action is rebound to identity, proposal version, expiry, critical evidence hash, active-ticket state, and policy before one idempotent write.
7. **Internal facts to browser:** separate serializers create customer and developer projections. Sensitive fields are never shipped and merely hidden with CSS.
8. **Policy retrieval to Gate:** a retrieved passage is untrusted data,
   including poisoned policy text. Only a Resolver-produced, hash-verified
   normalized fact record can enter the Evidence Gate; the LLM cannot select a
   version, interpret applicability, or change a fact. A bounded canonical
   excerpt is a browser-only `untrusted_explanatory_text` projection and is
   removed from the Agent ToolNode result. This verifies poisoned-document
   quarantine; it does not claim robustness to arbitrary retrieved prose being
   injected into a model context, because that prose is not supplied to the
   model.

## Bounded Agent graph

The graph is a single Agent loop, not multiple agents:

```text
START
  -> plan_with_model
       -> no tool call: validate candidate conclusion -> END
       -> tool call: governed ToolNode -> record observation -> budget guard -> plan_with_model
       -> malformed/forbidden/budget exhausted: deterministic failure/recovery node -> END
```

- Model: `deepseek-v4-flash` in Live mode.
- Tools: six allowlisted read-only functions. The policy slot is
  `search_after_sales_policy`; Retriever and Resolver are internal components,
  not additional model tools.
- Per Run: at most 8 planning turns.
- Per Case: at most 16 planning turns and 6 actual read-tool executions.
- Tool cache is Case-scoped and revision-aware; cache hits do not consume execution budget.
- The graph returns a structured investigation recommendation. It never performs the write.
- Hidden reasoning is neither requested nor stored. Developer Trace contains actions and structured observations only.

## Persistence

Two SQLite databases are intentionally separate:

- business database: conversations, messages, triage, cases, runs, tool calls, evidence, proposals, executions, tickets, events, fixture versions, and eval report metadata;
- LangGraph checkpoint database: graph execution checkpoints keyed by Case/Run thread identity.

Business tables are the current-state read model. Events are append-only audit facts, not a complete Event Sourcing architecture. Database migrations are managed by Alembic. Local `var/` data is generated and ignored by Git.

The policy corpus is packaged source data, not a third database. The local
vector index is a rebuildable derivative with a provenance manifest; it is not
a source of policy authority and is not an external vector database.

## Failure and recovery principles

- Triage timeout/schema failure: no tools; customer sees retry.
- Retryable read-tool failure: one bounded retry; do not cache the failure.
- Critical evidence unavailable: no proposal. Temporary failure maps to `awaiting_retry`; persistent conflict/unsafe uncertainty maps to `human_support_required`.
- Model failure: preserve prior evidence and create a failed Run; no write.
- Duplicate ticket: close with no new action and expose existing ticket status.
- Write response lost and read-back unavailable: `ActionState=uncertain`, close automation with `CaseOutcome=uncertain`, preserve action identity, never blindly reissue.
- SSE reconnect: replay persisted events after the last sequence; do not restart work.
- Closed Case: never reopen. A later request creates a new Case with optional `related_case_id`.

## Framework decision

`OTHER_FRAMEWORK` is selected because LangGraph directly provides the bounded state graph and native ToolNode required to demonstrate the Agent loop, while the project retains business authority. Hypha is not selected, imported, copied, or claimed. See `docs/FRAMEWORK-INTEGRATION.md` and `docs/AGENT-MODULE-MATRIX.md`.

## Final evidence position

The final Policy RAG path is bounded to `search_after_sales_policy`. The
retriever returns untrusted candidate passages, metadata, and scores; citation
verification reloads the canonical source; the deterministic Resolver emits
normalized facts and independent resolution states; only those facts enter the
Evidence Gate. The Agent sees typed observations, not policy authority or a
write tool. The final Retrieval Locked result passed 11/11 cases and the Main
Locked result passed 132/132 records; these gates support the release conclusion
but do not turn the Agent into the preferred architecture.

## Deployment boundary

The supported target is a local single-user prototype bound to loopback. Docker Compose may package local reproducibility, but no public/cloud/production deployment is in scope. A listening API or rendered screenshot is not delivery evidence; the browser journey and read-back side effect must be verified.

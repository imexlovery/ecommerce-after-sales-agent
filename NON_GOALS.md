# Explicit Non-Goals

These exclusions are release constraints, not backlog promises. Adding one requires an explicit scope decision and an update to `PROJECT.md` before implementation.

## Business scope

- refunds, compensation, returns, exchanges, payment disputes, chargebacks, or responsibility adjudication;
- warehouse receiving/quality inspection, reverse logistics, procurement, inventory planning, marketing, pricing, catalog, finance, or BI;
- broad customer-service knowledge Q&A or an all-in-one ecommerce operations platform;
- real human-agent staffing, ticket queues, service-level operations, or claims of production case handoff;
- real carrier, marketplace, order-management, warehouse, payment, messaging, or CRM integrations.

## Agent scope

- multi-agent routing, specialist agents, agent-to-agent messaging, parallel investigations, or delegation;
- RAG, embeddings, vector databases, knowledge-base administration, web search, MCP, skills, long-term memory, self-learning, or prompt optimization platforms;
- model-controlled authorization, policy eligibility, evidence completeness, proposal validity, idempotency, or write success;
- autonomous writes, auto-refunds, auto-compensation, or natural-language confirmation of side effects;
- hidden chain-of-thought capture, display, storage, or scoring.

## Product and platform scope

- multi-tenant or production authentication, SSO, RBAC administration, billing, quotas, high availability, disaster recovery, or on-call operations;
- cloud deployment, public hosting, mobile-native apps, omnichannel connectors, or browser automation;
- a generic Agent framework, generic evaluation platform, prompt playground, dataset editor, or reusable workflow builder;
- real benchmark claims, statistical significance claims, calibrated-confidence claims, or universal Agent superiority claims;
- production data migration, production backup/restore, compliance certification, or regulated-data processing.

## Deliberate simplifications

- virtual authenticated identities and fictional orders only;
- local single-process service and SQLite stores;
- one active investigation path per Case and serialized Case mutations;
- project-specific evaluation dashboard with exact counts and descriptive latency/cost summaries;
- Developer Trace is a portfolio/debug projection and is explicitly not a customer production surface.


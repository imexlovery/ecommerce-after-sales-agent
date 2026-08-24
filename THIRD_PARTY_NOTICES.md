# Third-Party Notices and Reference Boundary

## Yunpai ecommerce agent

- Repository: https://github.com/redmaplewww/yunpai-ecommerce-agent
- Reference revision: `a983b8ad07b7160751ebbf5db4244e39ddd9f2ba` (`main`, resolved 2026-08-23).
- Author/owner shown by the repository: `redmaplewww`.
- Use in this project: high-level reference only.

The following publicly described ideas may inform terminology or test-fixture semantics:

- LLM chooses among registered read observations while deterministic code owns facts, permissions, idempotency, and success checks;
- synthetic ecommerce order/logistics fixtures;
- explicit uncertain state after an ambiguous side effect;
- read-back verification and local audit events.

No Yunpai source file, prompt, graph, database schema, UI, fixture payload, test, or documentation text is copied into this repository. Yunpai's broad platform modules, RAG, workers, admin console, connectors, self-evolution, and production operations are explicitly out of scope.

At the inspected repository root, no license file was relied upon for reuse. This project therefore treats the public README as inspiration and independently designs and implements its contracts. Any future source-level reuse requires an explicit license/permission review and an updated notice before code is copied.

## Runtime dependencies

Python and frontend package licenses and exact resolved versions will be generated from committed lockfiles. `docs/FRAMEWORK-INTEGRATION.md` records the selected framework provenance and runtime proof; this file will be updated if any dependency requires attribution beyond ordinary package metadata.


# Implementation Plan

The work proceeds by user-visible vertical slices. Documentation and tests update in the same slice as behavior.

## VS-01 — Signed but not received

Build the smallest complete path:

1. seed two virtual customers and representative orders;
2. create conversation and accept a free-text customer message;
3. validate, redact, triage, policy-check, and create an authorized Case;
4. run the real LangGraph graph with explicit Mock first, then Live DeepSeek;
5. execute order context, timeline, POD, policy, and existing-ticket reads as dynamically selected;
6. run the deterministic Evidence Gate;
7. display the reply, proposal, and Developer Trace;
8. confirm the exact proposal through the customer UI;
9. create one simulated ticket with idempotency and verify it by read-back;
10. refresh/reconnect and prove persisted state without repeated side effects.

Acceptance: one complete actual browser journey. Mock closes only the Mock gate; Live requires a real DeepSeek tool-call trajectory.

## VS-02 — Stalled tracking

Add the second canonical issue, policy SLA calculation using `evaluated_at`, carrier alert as optional evidence, within-SLA no-action path, overdue proposal path, and issue-type revision from a mistaken signed-not-received report.

## VS-03 — Safety and recovery

Add mixed valid/malicious input, unauthorized-order fragment blocking, prohibited refund fragment blocking, ambiguous input clarification, multiple-order selection, POD absent/unavailable distinction, transient retry, structural conflict refresh, duplicate ticket, proposal invalidation, stale confirmation, decline, action failure, and uncertain write.

## VS-04 — Strong Workflow baseline

Implement a project-owned conditional investigation that is intentionally competent. It must use the same normalized Case input, tools, execution budget, evidence gate, fault seed, response renderer, proposal service, and executor. No weakened baseline is acceptable.

## VS-05 — Evaluation and release candidate

Run the three evaluation layers, freeze pilot-derived performance budgets before locked acceptance, execute all locked cases three times, build the read-only dashboard, preserve raw failures, perform clean-start and restart checks, and run trusted delivery scripts from a committed revision.

## Implementation order inside every slice

```text
typed contract -> failing tests -> domain/rules -> adapter/runtime -> API/event -> UI -> browser verification -> evidence/docs
```

Ordinary implementation decisions and failed tests are repaired without owner interruption. Owner checkpoints are defined in `AGENTS.md`.


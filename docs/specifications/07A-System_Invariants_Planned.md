# Planned Companion for 07: System Invariants

This document tracks intended but not implemented invariant machinery adjacent
to [`07-System_Invariants.md`](07-System_Invariants.md).

Nothing here overrides the canonical invariants.

## Planned Areas

### Stronger Idempotency Contract

Possible future work:

- explicit idempotency-key conventions
- stronger inbox/reserved correlation rules for replay-safe work
- clearer operator guidance for retried or resumed work

### Centralized Invariant Checking

Possible future work:

- explicit invariant-check helper classes
- reusable audit or health-check entrypoints
- tighter invariant-reporting surfaces for review or CI

### Dedicated Invariant Error Surfaces

Possible future work:

- domain-specific invariant exceptions
- more structured invariant-failure payloads
- stronger alerting semantics

## Related Plans

- [`docs/plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md`](../plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md)
- [`docs/plans/2026-04-07-spec-plan-code-traceability-plan.md`](../plans/2026-04-07-spec-plan-code-traceability-plan.md)

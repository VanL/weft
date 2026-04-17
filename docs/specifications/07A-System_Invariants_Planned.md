# Planned Companion for 07: System Invariants

This document tracks future invariant machinery that is not part of the current
contract in [`07-System_Invariants.md`](07-System_Invariants.md).

Keep this file planned-only. If a guarantee or operator rule is already shipped,
it belongs in the canonical current docs instead of here. For example, current
idempotency guidance already lives in [`02-TaskSpec.md`](02-TaskSpec.md) and
should not be restated as a planned gap in this file.
If any item here ships, move it into the canonical sibling and remove it from
this planned companion rather than citing this file as the invariant contract.

## Planned Areas

### Future Idempotency Contract

Possible future work:

- stronger inbox/reserved correlation rules for replay-safe work
- clearer operator guidance for retried or resumed work
- any future idempotency-key scheme beyond the current TaskSpec guidance

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

## Current Boundary

The canonical current doc already covers the shipped invariant contract:

- immutability of `TaskSpec.spec` and `TaskSpec.io`
- forward-only state transitions
- queue and reserved-policy invariants
- resource, execution, observability, manager, and context invariants
- the absence of a dedicated invariant-monitor daemon or exception family

Those behaviors belong in [`07-System_Invariants.md`](07-System_Invariants.md),
not in this planned companion.

## Related Plans

- [`docs/plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md`](../plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md)
- [`docs/plans/2026-04-07-spec-plan-code-traceability-plan.md`](../plans/2026-04-07-spec-plan-code-traceability-plan.md)

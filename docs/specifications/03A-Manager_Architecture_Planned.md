# Planned Companion for 03: Manager Architecture

This document tracks future manager-architecture work only. The canonical
manager contract already covers manager bootstrap, autostart, and runtime
endpoint discovery in [`03-Manager_Architecture.md`](03-Manager_Architecture.md)
and its linked specs. Nothing here redefines shipped behavior.
If any item here ships, move it into the canonical sibling and remove it from
this planned companion rather than citing this file as live contract.

## Planned Areas

### Manager Specialization [03A-1]

Future work may introduce explicit manager roles, capability-based task
routing, or richer multi-manager coordination.

Boundary:

- the current manager remains the canonical spawn dispatcher for
  `weft.spawn.requests`
- specialization must preserve TID, control, and lifecycle invariants
- specialization must not become a hidden second routing plane or a generic
  task broker

### Named Endpoint Extensions [03A-2]

The runtime endpoint registry already exists. Future work, if it proves
necessary, should stay adjacent to that discovery surface rather than turning
the manager into a broader service-router abstraction.

Possible extensions:

- higher-level aliases over the existing endpoint registry
- richer operator diagnostics for duplicate or stale claims
- explicit handoff helpers that still resolve to ordinary queue writes

Boundary:

- endpoint resolution remains discovery only
- sending to a named endpoint remains an ordinary queue write to the resolved
  `inbox` or `ctrl_in`
- missing-name resolution stays an explicit failure
- the manager must not become a universal "send by name" router for arbitrary
  higher-level policy

### Autostart Adjuncts [03A-3]

The shipped autostart contract already covers task specs and pipeline targets.
Any later work in this area should be treated as a refinement layer only:
better policy knobs, better diagnostics, or better operator ergonomics that do
not change the canonical manager path.

Boundary:

- autostart must continue to enter through the ordinary manager spawn queue
- no alternate bootstrap path should appear in the manager
- no new durable scheduler or service framework should be implied by this doc

## Related Plans

- [`docs/plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md`](../plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md)
- [`docs/plans/2026-04-09-manager-bootstrap-unification-plan.md`](../plans/2026-04-09-manager-bootstrap-unification-plan.md)
- [`docs/plans/2026-04-09-manager-lifecycle-command-consolidation-plan.md`](../plans/2026-04-09-manager-lifecycle-command-consolidation-plan.md)
- [`docs/plans/2026-04-09-weft-serve-supervised-manager-plan.md`](../plans/2026-04-09-weft-serve-supervised-manager-plan.md)
- [`docs/plans/2026-04-13-detached-manager-bootstrap-hardening-plan.md`](../plans/2026-04-13-detached-manager-bootstrap-hardening-plan.md)
- [`docs/plans/2026-04-13-manager-bootstrap-readiness-and-cleanup-test-plan.md`](../plans/2026-04-13-manager-bootstrap-readiness-and-cleanup-test-plan.md)

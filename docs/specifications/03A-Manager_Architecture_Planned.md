# Planned Companion for 03: Manager Architecture

This document tracks intended but not implemented manager-runtime work adjacent
to [`03-Manager_Architecture.md`](03-Manager_Architecture.md).

Nothing here overrides the canonical manager contract.

## Planned Areas

### Autostart Expansion

Current autostart support covers stored task specs. Future work may extend that
surface to additional target kinds such as stored pipelines once the runtime
semantics are clear enough to document as current contract.

### Manager Specialization

Future work may introduce:

- specialized manager roles
- richer capability-based task routing
- more explicit multi-manager coordination

The constraint is that any specialization must preserve the current TID,
control, and lifecycle invariants rather than bypassing them.

### Named Endpoint Boundary [03A-1]

If Weft later grows a runtime endpoint registry for stable task names, that
surface should remain adjacent to the manager runtime, not collapse into it.

Constraint:

- the manager remains the spawn dispatcher for `weft.spawn.requests`
- named-endpoint resolution is discovery, not a second manager-owned routing
  language
- ordinary persistent tasks should continue to own service logic such as
  request handling, fan-out, and child-task spawning on top of the existing
  spawn path
- the manager must not quietly become a universal "send by name" router for
  arbitrary higher-level agent policy

## Related Plans

- [`docs/plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md`](../plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md)
- [`docs/plans/2026-04-09-manager-lifecycle-command-consolidation-plan.md`](../plans/2026-04-09-manager-lifecycle-command-consolidation-plan.md)
- [`docs/plans/2026-04-16-runtime-endpoint-registry-boundary-plan.md`](../plans/2026-04-16-runtime-endpoint-registry-boundary-plan.md)

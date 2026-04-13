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

## Related Plans

- [`docs/plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md`](../plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md)
- [`docs/plans/2026-04-09-manager-lifecycle-command-consolidation-plan.md`](../plans/2026-04-09-manager-lifecycle-command-consolidation-plan.md)

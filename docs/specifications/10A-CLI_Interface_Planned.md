# CLI Interface - Planned Surfaces

This companion document holds planned CLI surfaces that are intentionally not part of the current contract.

Canonical current behavior and rationale live in [10-CLI_Interface.md](10-CLI_Interface.md).

## Planned Convenience Surfaces [10A-1]

The following additions remain planned rather than current:

- richer result streaming semantics beyond the current single-task follow mode
- extra convenience flags that reduce manual wiring
- broader queue or control ergonomics that should not become a second command language

The design goal is to preserve the same simple verbs and observability model while adding ergonomics only where they do not obscure the current contract.

## Planned Discovery Helpers [10A-2]

Additional task and process discovery helpers remain planned as convenience surfaces. The current contract uses the task, manager, and TID-mapping commands instead.

## Related Plans

- [`docs/plans/2026-04-13-result-stream-implementation-plan.md`](../plans/2026-04-13-result-stream-implementation-plan.md)

## Backlinks [10A-3]

- Current contract: [10-CLI_Interface.md](10-CLI_Interface.md)
- Context scoping: [04-SimpleBroker_Integration.md](04-SimpleBroker_Integration.md)

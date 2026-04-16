# CLI Interface - Planned Surfaces

This companion document holds planned CLI surfaces that are intentionally not part of the current contract.

Canonical current behavior and rationale live in [10-CLI_Interface.md](10-CLI_Interface.md).

## Planned Convenience Surfaces [10A-1]

The following additions remain planned rather than current:

- richer result streaming semantics beyond the current single-task follow mode
- extra convenience flags that reduce manual wiring
- broader queue or control ergonomics that should not become a second command language
- explicit endpoint discovery and send ergonomics over ordinary task-local queues

The design goal is to preserve the same simple verbs and observability model while adding ergonomics only where they do not obscure the current contract.

## Planned Discovery Helpers [10A-2]

Additional task and process discovery helpers remain planned as convenience surfaces. The current contract uses the task, manager, and TID-mapping commands instead.

### Endpoint Discovery And Send Follow-up [10A-2.1]

The base endpoint discovery and send ergonomics are now current; see
[`10-CLI_Interface.md`](10-CLI_Interface.md) [CLI-4.1].

Possible future follow-up:

- optional control helpers that target the resolved `ctrl_in` queue
- better formatting or filtering for endpoint inspection
- extra convenience only where it stays a thin layer over ordinary queues

CLI constraints remain unchanged:

- the surface must keep queue ownership visible rather than pretending named
  endpoints are a new task kind
- task payload contracts remain task-owned
- failed resolution must remain explicit
- request/reply envelopes, correlation IDs, and service-specific schemas stay
  out of the generic Weft CLI unless a builtin or higher-level system owns them

## Related Plans

- [`docs/plans/2026-04-13-result-stream-implementation-plan.md`](../plans/2026-04-13-result-stream-implementation-plan.md)
- [`docs/plans/2026-04-16-runtime-endpoint-registry-boundary-plan.md`](../plans/2026-04-16-runtime-endpoint-registry-boundary-plan.md)

## Backlinks [10A-3]

- Current contract: [10-CLI_Interface.md](10-CLI_Interface.md)
- Context scoping: [04-SimpleBroker_Integration.md](04-SimpleBroker_Integration.md)

# CLI Interface - Planned Follow-ups

This companion document records residual CLI follow-ups only. Current and
shipped CLI behavior lives in [10-CLI_Interface.md](10-CLI_Interface.md).
Do not read this file as the current contract.
If any item here ships, move it into the canonical sibling and remove it from
this planned companion rather than citing this file as the live CLI contract.

Shipped surfaces that are intentionally out of scope here include the current
result streaming path, the endpoint resolve/write/list helpers, and the
existing task, manager, and TID-mapping command set.

## Planned Convenience Surfaces [10A-1]

The remaining additions are planned rather than current:

- richer result streaming semantics beyond the shipped single-task `result --stream`
- extra convenience flags that reduce manual wiring
- broader queue or control ergonomics that stay a thin layer over ordinary queues

The design goal is to preserve the same simple verbs and observability model while adding ergonomics only where they do not obscure the current contract.

## Planned Discovery Helpers [10A-2]

Additional task and process discovery helpers remain planned as convenience
surfaces. The shipped task, manager, and TID-mapping commands stay the
baseline discovery surface.

The endpoint resolve/write/list helpers are already current and are documented
in [`10-CLI_Interface.md`](10-CLI_Interface.md) [CLI-4.1]; they are not
tracked here.

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

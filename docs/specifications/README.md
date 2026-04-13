# Weft System Specifications

This directory is the authoritative Weft spec set.

The split is intentional:

- Canonical numbered specs (`00`, `01`, `04`, `06`, `07`, `08`, `09`, `10`,
  `11`, `13`, and the other core docs) describe current behavior, current
  boundaries, and the reasons behind them.
- Adjacent `A` docs describe intended but unshipped behavior. They are
  planning companions, not current contract.
- [`12-Future_Ideas.md`](12-Future_Ideas.md) remains the holding area for
  deferred ideas that are intentionally out of scope.

If a surface was removed or superseded, the canonical doc should say that
directly. It should not appear as "not yet implemented."

## Document Overview

### Current-contract specs

- [`00-Overview_and_Architecture.md`](00-Overview_and_Architecture.md):
  Weft's current shape and design rationale
- [`00-Quick_Reference.md`](00-Quick_Reference.md): queue names, states,
  control messages, and environment variables
- [`01-Core_Components.md`](01-Core_Components.md): current component
  boundaries and ownership
- [`02-TaskSpec.md`](02-TaskSpec.md): TaskSpec schema and current semantics
- [`03-Manager_Architecture.md`](03-Manager_Architecture.md): current manager
  runtime model
- [`04-SimpleBroker_Integration.md`](04-SimpleBroker_Integration.md): current
  broker integration and context model
- [`05-Message_Flow_and_State.md`](05-Message_Flow_and_State.md): current queue
  flows and state behavior
- [`06-Resource_Management.md`](06-Resource_Management.md): current limit
  enforcement and error-handling behavior
- [`07-System_Invariants.md`](07-System_Invariants.md): guarantees the current
  implementation must preserve
- [`08-Testing_Strategy.md`](08-Testing_Strategy.md): current test workflow,
  harnesses, and coverage shape
- [`09-Implementation_Plan.md`](09-Implementation_Plan.md): current
  implementation status and active-plan map
- [`10-CLI_Interface.md`](10-CLI_Interface.md): current CLI contract
- [`11-CLI_Architecture_Crosswalk.md`](11-CLI_Architecture_Crosswalk.md):
  current CLI-to-code ownership map
- [`12-Future_Ideas.md`](12-Future_Ideas.md): explicitly deferred ideas
- [`13-Agent_Runtime.md`](13-Agent_Runtime.md): current first-class agent
  runtime contract

### Planned companion docs

- [`00A-Overview_and_Architecture_Planned.md`](00A-Overview_and_Architecture_Planned.md)
- [`01A-Core_Components_Planned.md`](01A-Core_Components_Planned.md)
- [`03A-Manager_Architecture_Planned.md`](03A-Manager_Architecture_Planned.md)
- [`04A-SimpleBroker_Integration_Planned.md`](04A-SimpleBroker_Integration_Planned.md)
- [`05A-Message_Flow_and_State_Planned.md`](05A-Message_Flow_and_State_Planned.md)
- [`06A-Resource_Management_Planned.md`](06A-Resource_Management_Planned.md)
- [`07A-System_Invariants_Planned.md`](07A-System_Invariants_Planned.md)
- [`08A-Testing_Strategy_Planned.md`](08A-Testing_Strategy_Planned.md)
- [`09A-Implementation_Roadmap_Planned.md`](09A-Implementation_Roadmap_Planned.md)
- [`10A-CLI_Interface_Planned.md`](10A-CLI_Interface_Planned.md)
- [`11A-CLI_Architecture_Crosswalk_Planned.md`](11A-CLI_Architecture_Crosswalk_Planned.md)
- [`13A-Agent_Runtime_Planned.md`](13A-Agent_Runtime_Planned.md)

These companion docs stay adjacent to the owning spec so the mapping is easy to
follow, but they do not override the canonical files.

## Reading Order

For current system orientation:

1. [`00-Overview_and_Architecture.md`](00-Overview_and_Architecture.md)
2. [`00-Quick_Reference.md`](00-Quick_Reference.md)
3. [`01-Core_Components.md`](01-Core_Components.md)
4. [`02-TaskSpec.md`](02-TaskSpec.md)
5. [`03-Manager_Architecture.md`](03-Manager_Architecture.md)
6. [`10-CLI_Interface.md`](10-CLI_Interface.md)

Then read the matching `A` docs only if you need the intended future surface
for planning work.

## Reference Codes

Specs use section codes like `[TS-1]`, `[CC-3.3]`, `[RM-5]`, and `[CLI-1.4]`.
Prefer citing these codes rather than whole documents when linking plans, code
comments, or reviews back to the specs.

## Traceability Rules

`docs/specifications/` is the source of truth for Weft behavior. Keep it in
sync with plans and code using these rules:

- Plans in `docs/plans/` should cite exact spec files and section codes.
- Touched specs should update nearby implementation notes and plan backlinks in
  the same change.
- Touched code modules and major boundary functions should keep docstrings that
  point back to the governing spec section.
- Companion `A` docs should link back to the owning canonical spec and state
  plainly that they are planned material.

## Mental Model

Weft is "SimpleBroker for processes":

- queues are the persistence and coordination layer
- managers and child work are both task-shaped
- CLI verbs stay small and stable
- rationale belongs in the canonical spec because design intent still matters
- history does not belong in the canonical tier because it weakens authority

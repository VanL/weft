# Planned Companion for 01: Core Components

This document tracks intended future component work adjacent to
[`01-Core_Components.md`](01-Core_Components.md).

It does not restate shipped surfaces. The runtime endpoint registry, current
task specializations, and session helpers already live in the current spec and
implementation, so they are intentionally not treated as planned work here.

Nothing here overrides the canonical component contract.
If any item here ships, move it into the canonical sibling and remove it from
this planned companion rather than treating this file as canonical.

## Planned Areas

### Runtime Endpoint Boundary [01A-1]

[`01-Core_Components.md`](01-Core_Components.md) already owns the runtime
endpoint registry. If future pressure appears around that surface, it must stay
narrow:

- keep the registry thin and runtime-only
- keep registration explicit and opt-in
- keep records pointed at ordinary task-local queues rather than inventing a
  second transport
- keep service schemas, handoff policy, and durable domain state out of Weft
  core

### Additional Internal Task Variants [01A-2]

Future runtime slices may add more task specializations if a new queue/control
shape really needs first-class ownership.

Constraint:

- new internal variants should still reuse the same queue, control, and state
  conventions rather than inventing a parallel runtime model

### Further Componentization [01A-3]

Weft does not currently need standalone `executor`, `process_tools`, or similar
module families. Future refactors may split more helpers out only if they
clarify ownership without duplicating behavior.

### Higher-Level Developer Helpers [01A-4]

Possible future work:

- richer developer-facing context conveniences
- more reusable observability helpers
- additional plugin-facing extension surfaces

These should emerge from demonstrated pressure in the codebase, not from a
desire to recreate an older object model.

## Related Plans

- [`docs/plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md`](../plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md)
- [`docs/plans/2026-04-06-runner-extension-point-plan.md`](../plans/2026-04-06-runner-extension-point-plan.md)
- [`docs/plans/2026-04-16-runtime-endpoint-registry-boundary-plan.md`](../plans/2026-04-16-runtime-endpoint-registry-boundary-plan.md)

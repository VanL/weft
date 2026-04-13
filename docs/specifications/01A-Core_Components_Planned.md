# Planned Companion for 01: Core Components

This document tracks intended but not implemented component work adjacent to
[`01-Core_Components.md`](01-Core_Components.md).

Nothing here overrides the canonical component contract.

## Planned Areas

### Additional Internal Task Variants

Future runtime slices may add more internal task variants, especially around
pipeline orchestration or richer long-lived session handling.

Constraint:

- new internal variants should still reuse the same queue, control, and state
  conventions rather than inventing a parallel runtime model

### Further Componentization

Weft does not currently need standalone `executor`, `process_tools`, or
similar module families as part of the current contract. Future refactors may
split more helpers out only if they clarify ownership without duplicating
behavior.

### Higher-Level Developer Helpers

Possible future work:

- richer developer-facing context conveniences
- more reusable observability helpers
- additional plugin-facing extension surfaces

These should emerge from demonstrated pressure in the codebase, not from a
desire to recreate the older planned object model.

## Related Plans

- [`docs/plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md`](../plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md)
- [`docs/plans/2026-04-06-runner-extension-point-plan.md`](../plans/2026-04-06-runner-extension-point-plan.md)

# Planned Companion for 05: Message Flow and State

This document tracks intended but not implemented message-flow and state
surfaces adjacent to [`05-Message_Flow_and_State.md`](05-Message_Flow_and_State.md).

Nothing here overrides the canonical message-flow contract.

## Planned Areas

### Higher-Level State Helpers

Possible future work:

- dedicated state-tracking helpers
- richer task-log replay utilities
- stronger state-history tooling for operators

### Large Output Reader Helpers

Possible future work:

- automatic dereferencing helpers for large-output references
- chunked readers for large spilled outputs
- richer integrity and retention tooling for output artifacts

### Recovery and Queue-Lifecycle Helpers

Possible future work:

- built-in reserved-queue recovery helpers
- more guided retry surfaces
- explicit queue-lifecycle dashboards or cleanup helpers

## Related Plans

- [`docs/plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md`](../plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md)
- [`docs/plans/2026-04-13-pipeline-spec-expansion-plan.md`](../plans/2026-04-13-pipeline-spec-expansion-plan.md)

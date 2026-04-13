# Planned Companion for 06: Resource Management

This document tracks intended but not implemented resource-management features
adjacent to [`06-Resource_Management.md`](06-Resource_Management.md).

Nothing here overrides the canonical runtime contract.

## Planned Areas

### Softer Enforcement Modes

Possible future work:

- warning thresholds before hard violation
- grace periods between warning and termination
- partial mitigation when a runtime can support it cleanly

### Stronger Isolation

Possible future work:

- throttling or quota-based enforcement
- cgroup or container-backed isolation
- backend/runtime-specific sandboxes

### Stronger Validation and Policy

Possible future work:

- command allowlists
- environment-variable guards
- stronger agent-action policy surfaces

### Higher-Level Recovery Helpers

Possible future work:

- built-in retry helpers
- recovery assistants over reserved queues
- stronger automated backoff semantics

## Related Plans

- [`docs/plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md`](../plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md)
- [`docs/plans/2026-04-06-runner-extension-point-plan.md`](../plans/2026-04-06-runner-extension-point-plan.md)

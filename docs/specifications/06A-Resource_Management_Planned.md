# Planned Companion for 06: Resource Management

This document tracks intended but not implemented resource-management work
adjacent to [`06-Resource_Management.md`](06-Resource_Management.md).

The canonical spec already owns the current shipped contract: monitor-and-react
limit enforcement at the runner boundary, psutil-based host monitoring,
runner-native limit mapping where available, and the existing runner-environment
profile and runner-plugin surfaces. Those are current behavior, not planned
work.

Nothing here overrides the canonical runtime contract.
If any item here ships, move it into the canonical sibling and remove it from
this planned companion rather than treating this file as current resource
contract.

Current code reality:

- `06-Resource_Management.md` already documents the current enforcement model
  and shipped monitor/runtime hooks.
- `02-TaskSpec.md` already owns the current `spec.limits`, `spec.runner`, and
  reserved-policy surfaces.
- `13-Agent_Runtime.md` already owns the current provider-limited agent policy
  and delegated runtime shapes.

This companion only tracks future work that remains outside those current
contracts.

## Planned Areas

### Softer Enforcement Modes

Possible future work, if it can coexist with the current hard-violation path:

- warning thresholds before hard violation
- grace periods between warning and termination
- partial mitigation when a runtime can support it cleanly

Boundary:

- do not replace the current confirm-and-terminate contract
- do not make warnings the default observable state
- do not add silent recovery that hides an actual limit breach

### Stronger Isolation

Possible future work, if it stays backend-specific and explicit:

- throttling or quota-based enforcement
- cgroup or container-backed isolation
- backend/runtime-specific sandboxes

Boundary:

- do not imply a universal sandbox contract
- do not move process-isolation policy into ordinary task submission
- keep any new isolation mode opt-in and runner-owned

### Stronger Validation and Policy

Possible future work, if it stays explicit and separate from current shipped
runner policy:

- command allowlists
- environment-variable guards
- stronger agent-action policy surfaces

Boundary:

- do not bolt a global policy engine onto ordinary `weft run`
- do not overwrite the current runner-specific allowlist and profile behavior
- keep policy surfaces narrow and observable

### Higher-Level Recovery Helpers

Possible future work for operators, not for default runtime behavior:

- built-in retry helpers
- recovery assistants over reserved queues
- stronger automated backoff semantics

Boundary:

- do not change the reserved-queue contract without a spec update
- do not make automatic retry the default task outcome
- keep queue recovery explicit rather than hidden behind runtime heuristics

## Scope Boundary

This companion does not restate shipped resource-management behavior. It only
tracks future work that would sit above or alongside the current canonical
contract, such as:

- softer failure handling
- stronger isolation primitives
- broader validation or policy helpers
- operator convenience around queue recovery

The following are current behavior and belong in the canonical specs instead:

- runner-scoped environment profiles
- current runner-plugin validation and capability checks
- current psutil-based monitoring and native runner limit mapping
- current provider-limited agent policy surfaces

## Related Plans

- [`docs/plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md`](../plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md)
- [`docs/plans/2026-04-06-runner-extension-point-plan.md`](../plans/2026-04-06-runner-extension-point-plan.md)
- [`docs/plans/2026-04-13-delegated-agent-runtime-phase-3-implementation-plan.md`](../plans/2026-04-13-delegated-agent-runtime-phase-3-implementation-plan.md)

# SimpleBroker Integration - Planned Surfaces

This companion document holds only unreleased SimpleBroker-adjacent surfaces.

Canonical current behavior and rationale live in [04-SimpleBroker_Integration.md](04-SimpleBroker_Integration.md).
If any item here ships, move it into the canonical sibling and remove it from
this planned companion rather than treating this file as current integration
contract.

Shipped behavior that used to be discussed here is documented in the canonical
specs instead:

- runtime endpoint registry state: [04-SimpleBroker_Integration.md](04-SimpleBroker_Integration.md) [SB-0.5]
- large-output spill behavior: [05-Message_Flow_and_State.md](05-Message_Flow_and_State.md) [MF-2.1]

## Planned Context Information [04A-1]

Unreleased operator-facing context information surfaces include:

- `weft info` for explicit project context inspection
- `weft status --project` for a project-scoped summary view

These surfaces would make the project root and broker target easier to inspect without requiring the operator to reason about broker internals directly.

## Planned Integration Patterns [04A-2]

The following integration helpers remain planned rather than current:

```python
def create_task_in_context(context_path: str, task_config: dict) -> str:
    """Create a task within a specific Weft context."""


def bridge_contexts(source_context: str, target_context: str, message: str):
    """Forward a message between isolated contexts."""


class ContextMonitor:
    """Monitor tasks across multiple contexts."""
```

These helpers represent explicit cross-context and multi-context orchestration surfaces. Weft keeps the current contract narrower and project-local.

## Planned Performance Surfaces [04A-3]

One performance-related addition remains planned rather than current:

- an explicit queue-connection pool surface

The current contract already relies on SimpleBroker's hard size limit and shared broker targets. Task-runtime spillover for oversized output is shipped behavior and belongs in the canonical runtime specs, not this planned broker-surface doc.

## Backlinks [04A-4]

- Current contract: [04-SimpleBroker_Integration.md](04-SimpleBroker_Integration.md)
- Runtime-flow semantics:
  [05A-Message_Flow_and_State_Planned.md](05A-Message_Flow_and_State_Planned.md)
- CLI surface: [10-CLI_Interface.md](10-CLI_Interface.md)
- Implementation plan:
  [docs/plans/2026-04-16-runtime-endpoint-registry-boundary-plan.md](../plans/2026-04-16-runtime-endpoint-registry-boundary-plan.md)

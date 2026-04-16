# SimpleBroker Integration - Planned Surfaces

This companion document holds planned SimpleBroker-adjacent surfaces that are intentionally not part of the current contract.

Canonical current behavior and rationale live in [04-SimpleBroker_Integration.md](04-SimpleBroker_Integration.md).

## Planned Context Information [04A-1]

Planned operator-facing context information surfaces include:

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

## Runtime Endpoint Storage Follow-up [04A-2.1]

The base queue-backed storage contract is now current; see
[`04-SimpleBroker_Integration.md`](04-SimpleBroker_Integration.md) [SB-0.5].

No additional endpoint storage surface is planned today. If future work is
needed, it must preserve the same constraints:

- endpoint state remains runtime-only, queue-backed, and excluded from
  dump/load
- no backend-specific SQL hooks or privileged table ownership for ordinary
  tasks
- no hidden alternate control plane outside queue-visible runtime state

## Planned Performance Surfaces [04A-3]

Two performance-related additions remain planned rather than current:

- explicit large-message spillover to disk for payloads above the broker limit
- an explicit queue-connection pool surface

The current contract relies on SimpleBroker's hard size limit and shared broker targets instead.

## Backlinks [04A-4]

- Current contract: [04-SimpleBroker_Integration.md](04-SimpleBroker_Integration.md)
- Runtime-flow semantics:
  [05A-Message_Flow_and_State_Planned.md](05A-Message_Flow_and_State_Planned.md)
- CLI surface: [10-CLI_Interface.md](10-CLI_Interface.md)
- Implementation plan:
  [docs/plans/2026-04-16-runtime-endpoint-registry-boundary-plan.md](../plans/2026-04-16-runtime-endpoint-registry-boundary-plan.md)

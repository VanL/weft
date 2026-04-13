# CLI Architecture Crosswalk

This document maps the current CLI command families to the modules that own
them. Deferred command surfaces live in
[11A-CLI_Architecture_Crosswalk_Planned.md](11A-CLI_Architecture_Crosswalk_Planned.md).

## Why This Shape Exists [CLI-X0]

The CLI stays thin on purpose:

- `weft/cli.py` wires the Typer app and global command structure.
- Command handlers live in `weft/commands/*.py`, not in a monolithic command
  module.
- Context discovery is owned outside the CLI surface so backend-neutral project
  roots stay separate from command parsing.
- The CLI delegates to current task, queue, manager, spec, pipeline, and agent
  runtime code instead of re-implementing it.

This keeps the spec aligned to the shipped code and makes drift obvious when a
command grows beyond its owning module.

## Current Crosswalk [CLI-X1]

| Command family | Owning modules | Why it exists |
|---|---|---|
| `init` | `weft/commands/init.py` | project bootstrap and root discovery |
| `run` | `weft/commands/run.py`, `weft/commands/_manager_bootstrap.py`, `weft/commands/_streaming.py` | task submission, pipeline execution, and optional waiting |
| `status` | `weft/commands/status.py`, `weft/commands/_task_history.py` | reconstruct current status from task logs and pipeline snapshots |
| `result` | `weft/commands/result.py`, `weft/commands/_result_wait.py`, `weft/commands/_task_history.py` | gather public output with the shared waiter |
| `task` | `weft/commands/tasks.py`, `weft/commands/_task_history.py` | TID lookup, list, stop, kill, and task-level status |
| `manager` / `serve` | `weft/commands/manager.py`, `weft/commands/serve.py`, `weft/commands/_manager_bootstrap.py` | manage the canonical manager lifecycle |
| `queue` | `weft/commands/queue.py` | direct SimpleBroker queue access |
| `spec` | `weft/commands/specs.py` | stored spec management |
| `validate-taskspec` | `weft/commands/validate_taskspec.py` | explicit TaskSpec validation entrypoint |
| `system` | `weft/commands/tidy.py`, `weft/commands/dump.py`, `weft/commands/load.py` | maintenance and broker-state operations |

## Current Result Surface [CLI-X2]

`weft run` and `weft result` both use the current task-log/outbox waiter so
timeout handling and terminal-state classification stay in sync. That shared
waiter is intentional. It avoids a second result model and keeps the public CLI
surface aligned with task lifecycle events.

## Related Documents

- [11A-CLI_Architecture_Crosswalk_Planned.md](11A-CLI_Architecture_Crosswalk_Planned.md)
- [10-CLI_Interface.md](10-CLI_Interface.md)
- [09-Implementation_Plan.md](09-Implementation_Plan.md)

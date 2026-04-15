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
| `run` | `weft/commands/run.py`, `weft/commands/_manager_bootstrap.py`, `weft/commands/_spawn_submission.py`, `weft/commands/_streaming.py` | task submission, explicit `NAME|PATH` spec resolution, local spec materialization or run-input shaping when declared, pipeline execution, and optional waiting |
| `status` | `weft/commands/status.py`, `weft/commands/_task_history.py` | reconstruct current status from task logs and pipeline snapshots |
| `result` | `weft/commands/result.py`, `weft/commands/_result_wait.py`, `weft/commands/_task_history.py` | gather public output with the shared waiter |
| `task` | `weft/commands/tasks.py`, `weft/commands/_task_history.py` | TID lookup, list, stop, kill, and task-level status |
| `manager` / `serve` | `weft/commands/manager.py`, `weft/commands/serve.py`, `weft/commands/_manager_bootstrap.py` | manage the canonical manager lifecycle |
| `queue` | `weft/commands/queue.py` | direct SimpleBroker queue access |
| `spec` | `weft/commands/specs.py`, `weft/commands/validate_taskspec.py` | stored spec management, builtin task-spec discovery, and explicit TaskSpec validation |
| `system` | `weft/commands/builtins.py`, `weft/commands/tidy.py`, `weft/commands/dump.py`, `weft/commands/load.py` | builtin inventory plus maintenance and broker-state operations |

## Current Result Surface [CLI-X2]

`weft run` and `weft result` both use the current task-log/outbox waiter so
timeout handling and terminal-state classification stay in sync. That shared
waiter is intentional. It avoids a second result model and keeps the public CLI
surface aligned with task lifecycle events.

Builtin TaskSpecs are not a separate command family. They reuse the same
explicit-spec resolver and CLI surfaces as stored task specs. `weft run --spec`
and `weft spec ...` are the only builtin entry points. Bare command execution
stays separate.

## Related Plans

- [`docs/plans/2026-04-14-builtin-taskspecs-and-spec-resolution-plan.md`](../plans/2026-04-14-builtin-taskspecs-and-spec-resolution-plan.md)
- [`docs/plans/2026-04-14-builtin-contract-and-doc-drift-reduction-plan.md`](../plans/2026-04-14-builtin-contract-and-doc-drift-reduction-plan.md)
- [`docs/plans/2026-04-14-system-builtins-command-plan.md`](../plans/2026-04-14-system-builtins-command-plan.md)

## Related Documents

- [11A-CLI_Architecture_Crosswalk_Planned.md](11A-CLI_Architecture_Crosswalk_Planned.md)
- [10-CLI_Interface.md](10-CLI_Interface.md)
- [10B-Builtin_TaskSpecs.md](10B-Builtin_TaskSpecs.md)
- [09-Implementation_Plan.md](09-Implementation_Plan.md)

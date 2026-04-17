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
| `run` | `weft/commands/run.py`, `weft/commands/_manager_bootstrap.py`, `weft/commands/_result_wait.py`, `weft/commands/_spawn_submission.py`, `weft/commands/_streaming.py`, `weft/commands/_task_history.py` | task submission, explicit `NAME|PATH` spec resolution, local spec materialization or run-input shaping when declared, queue-first spawn reconciliation, and optional completion wait or interactive queue-stream handling |
| `status` | `weft/commands/status.py`, `weft/commands/_manager_bootstrap.py`, `weft/commands/_queue_wait.py`, `weft/commands/_task_history.py` | reconstruct current status from task logs, manager registry records, and watch loops |
| `result` | `weft/commands/result.py`, `weft/commands/_queue_wait.py`, `weft/commands/_result_wait.py`, `weft/commands/_streaming.py`, `weft/commands/_task_history.py` | gather public output with the shared waiter, stream decoder, and task-log classification helpers |
| `task` | `weft/commands/tasks.py`, `weft/commands/status.py`, `weft/commands/_queue_wait.py`, `weft/commands/_task_history.py` | TID lookup, list, stop, kill, and task-level status built on shared snapshot and control helpers |
| `manager` / `serve` | `weft/commands/manager.py`, `weft/commands/serve.py`, `weft/commands/_manager_bootstrap.py` | manage the canonical manager lifecycle and registry control |
| `queue` | `weft/commands/queue.py` | direct SimpleBroker queue access |
| `spec` | `weft/commands/specs.py`, `weft/commands/validate_taskspec.py` | stored spec management, resolution, builtin task-spec discovery, and explicit TaskSpec validation |
| `system` | `weft/commands/builtins.py`, `weft/commands/tidy.py`, `weft/commands/dump.py`, `weft/commands/load.py` | builtin inventory plus maintenance and broker-state operations |

## Current Helper Boundaries [CLI-X2]

The command-family table above is only the top layer. Several shared helpers
are already shipped and are part of the current ownership map:

- `weft/commands/_manager_bootstrap.py` owns manager discovery, detached or
  foreground startup, registry polling, and convergence on the canonical live
  manager record
- `weft/commands/_queue_wait.py`, `weft/commands/_result_wait.py`, and
  `weft/commands/_streaming.py` own queue-native waiting, result assembly,
  completion-boundary handling, and stream decoding reused by `run`, `result`,
  and long-lived task surfaces
- `weft/commands/status.py` owns task-snapshot reconstruction from
  `weft.log.tasks`, manager registry records, and live queue hints for status
  surfaces
- `weft/commands/tasks.py` owns short/full TID resolution, PID-to-TID lookup,
  control fallback, and pipeline-status precedence for task-centric surfaces
- `weft/commands/_task_history.py` owns logged TaskSpec lookup and pipeline
  status-queue inference reused by `result` and `task status`
- `weft/core/spec_store.py` owns named stored-spec and builtin-spec resolution
  across `.weft/tasks/`, `.weft/pipelines/`, and packaged builtins;
  `weft/commands/specs.py` adds CLI formatting, mutation, and validation entry
  points on top of that shared resolver
- `weft/core/pipelines.py` owns pipeline models, validation, and compilation;
  CLI commands submit or inspect those compiled artifacts but do not
  reimplement pipeline realization logic

## Current Result Surface [CLI-X3]

`weft run` and `weft result` both use the shared queue-wait and result-assembly
helpers in `weft/commands/_queue_wait.py`, `weft/commands/_result_wait.py`, and
`weft/commands/_streaming.py` so timeout handling, terminal-state
classification, and stream decoding stay in sync. That shared waiter path is
intentional. It avoids a second result model and keeps the public CLI surface
aligned with task lifecycle events.

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

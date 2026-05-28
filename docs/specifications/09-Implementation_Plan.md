# Implementation Plan

This document records the current implementation boundary and why the code is
shaped this way. The deferred roadmap lives in
[09A-Implementation_Roadmap_Planned.md](09A-Implementation_Roadmap_Planned.md).

## Current Boundary [IP-0]

The current codebase is intentionally narrower than the old roadmap:

- CLI wiring lives in `weft/cli/app.py` plus the current command modules under
  `weft/commands/`.
- The current root surface is `init`, `status`, `result`, and `run`, with
  sub-apps for `queue`, `manager`, `task`, `spec`, and `system`.
- Context discovery is backend-neutral and no longer hangs off `--dir` or
  `--file` globals.
- Builtin task helpers are shipped as explicit task-only helpers. They are
  surfaced through `weft spec ...` and inventoried through
  `weft system builtins`.
- Pipeline specs are first-class stored specs under `.weft/pipelines/`. They
  are not builtin helpers.
- Submission-time spec materialization and run-input shaping are current
  shipped behavior, not roadmap-only ideas.
- The public Python client is a thin adapter over the same command capability
  layer and `WeftContext` resolution used by the CLI. It does not own a second
  runtime, state model, or broker-targeting path.

The reason for that shape is simplicity. Weft keeps the visible command
surface small and routes work through the current task, queue, manager, and
spec machinery instead of splitting behavior across speculative helper
packages.

## Current Ownership [IP-1]

- `weft/commands/run.py` owns shared `weft run` orchestration, spec-aware
  loading, local materialization, and delegation into the current manager,
  submission, result, and streaming helpers; `weft/cli/run.py` is the Typer
  adapter over that shared surface.
- `weft/client/` owns the public Python client adapter. It wraps the same
  command-layer capabilities and public command result dataclasses used by the
  CLI, returning object handles and namespace helpers without bypassing the
  queue-first manager/runtime path.
- `weft/commands/init.py` owns project initialization and broker-facing
  project bootstrap for the root `weft init` command.
- `weft/core/manager_runtime.py` owns detached manager bootstrap,
  shared manager start/stop coordination, and TID generation for submission
  paths; `weft/commands/manager.py` and `weft/commands/serve.py` are the
  command-side capability surfaces.
- `weft/commands/_spawn_submission.py` owns queue-first spawn reconciliation
  after a TID has been submitted.
- `weft/commands/result.py`, `weft/commands/_result_wait.py`,
  `weft/commands/_streaming.py`, and `weft/core/queue_wait.py` own the
  result surface and the shared waiting/streaming behavior behind it.
- `weft/commands/status.py`, `weft/commands/tasks.py`, and
  `weft/commands/_task_history.py` own task inspection, short/full TID
  handling, and pipeline-aware status reconstruction.
- `weft/commands/manager.py` and `weft/commands/serve.py` own the manager
  lifecycle commands over the shared runtime helper.
- `weft/commands/queue.py` owns direct queue operations, endpoint resolution,
  queue watching, and alias management.
- `weft/commands/specs.py` and `weft/commands/validate_taskspec.py` own the
  CLI-facing spec management and validation surfaces; `weft/core/spec_store.py`
  owns the shared `NAME|PATH` resolution logic; `weft/core/taskspec/parameterization.py`
  and `weft/core/taskspec/run_input.py` own submission-time materialization and
  run-input shaping; `weft/core/pipelines.py` owns pipeline validation and
  compilation.
- `weft/commands/builtins.py` owns the shipped builtin inventory surface;
  `weft/commands/tidy.py`, `weft/commands/dump.py`, and `weft/commands/load.py`
  own `system` maintenance and broker-state export/import.
- The runtime side of those surfaces lives in `weft/core/tasks/base.py`,
  `weft/core/tasks/consumer.py`, `weft/core/tasks/interactive.py`,
  `weft/core/tasks/pipeline.py`, `weft/core/tasks/runner.py`, and
  `weft/core/manager.py`.

## Public Python Client Surface [IP-1.1]

The current `weft.client` package is a stable adapter over shipped command
capabilities, not a separate runtime API.

- `connect()` and `WeftClient` resolve a `WeftContext` and expose namespace
  helpers for tasks, queues, specs, managers, and system status.
- `Task` is a lazy handle around a TID. It exposes status snapshots, terminal
  snapshots, result waits, lifecycle event iteration, read-only realtime event
  iteration, follow-with-final-result iteration, and task stop/kill.
- Client-facing dataclasses are re-exported from `weft.commands.types` so the
  CLI and Python client share one result/status shape.
- Known-TID terminal snapshots are non-consuming observations. When they include
  exact acknowledgement targets, callers must acknowledge explicitly. This
  keeps read-only observers from stealing task results or mutating queue state.
- Realtime event iteration peeks task-log, outbox, and terminal-control
  surfaces instead of consuming them. That lets HTTP/SSE/WebSocket-style
  diagnostics coexist with `weft result`, `weft run`, and Python result waits.

This shape exists because framework integrations need a small stable substrate
API, but Weft still needs one queue-first source of truth. Django or other
higher-level systems may wrap this client, but they must not depend on internal
command modules or invent their own task lifecycle model.

## Why This Shape Exists [IP-2]

- Backend-neutral project discovery is easier to reason about than directory-
  plus-database flags.
- Keeping the CLI thin reduces the chance that command behavior drifts away
  from the task and queue runtime.
- Folding current behavior into existing modules keeps traceability explicit
  and avoids inventing package boundaries that are not part of the shipped
  system.

## Plan Corpus [IP-3]

Implementation work is tracked through the plan corpus in
[`docs/plans/README.md`](../plans/README.md).

That index is intentionally lightweight:

- specs still define behavior
- plans describe completed implementation paths for shipped behavior or current
  repo tooling
- the plan index is curated so superseded, roadmap-only, audit-only, and
  unimplemented plans do not read like current project direction

## Related Documents

- [`../../README.md`](../../README.md)
- [`00-Quick_Reference.md`](00-Quick_Reference.md)
- [`10-CLI_Interface.md`](10-CLI_Interface.md)
- [`10B-Builtin_TaskSpecs.md`](10B-Builtin_TaskSpecs.md)
- [`11-CLI_Architecture_Crosswalk.md`](11-CLI_Architecture_Crosswalk.md)
- [`12-Pipeline_Composition_and_UX.md`](12-Pipeline_Composition_and_UX.md)
- [`../plans/README.md`](../plans/README.md)
- [09A-Implementation_Roadmap_Planned.md](09A-Implementation_Roadmap_Planned.md)

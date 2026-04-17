# Implementation Plan

This document records the current implementation boundary and why the code is
shaped this way. The deferred roadmap lives in
[09A-Implementation_Roadmap_Planned.md](09A-Implementation_Roadmap_Planned.md).

## Current Boundary [IP-0]

The current codebase is intentionally narrower than the old roadmap:

- CLI wiring lives in `weft/cli.py` plus the current command modules under
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

The reason for that shape is simplicity. Weft keeps the visible command
surface small and routes work through the current task, queue, manager, and
spec machinery instead of splitting behavior across speculative helper
packages.

## Current Ownership [IP-1]

- `weft/commands/run.py` owns `weft run` orchestration, spec-aware help,
  explicit `NAME|PATH` loading, and delegation into the current manager,
  submission, result, and streaming helpers.
- `weft/commands/init.py` owns project initialization and broker-facing
  project bootstrap for the root `weft init` command.
- `weft/commands/_manager_bootstrap.py` owns detached manager bootstrap,
  shared manager start/stop coordination, and TID generation for submission
  paths.
- `weft/commands/_spawn_submission.py` owns queue-first spawn reconciliation
  after a TID has been submitted.
- `weft/commands/result.py`, `weft/commands/_result_wait.py`,
  `weft/commands/_streaming.py`, and `weft/commands/_queue_wait.py` own the
  result surface and the shared waiting/streaming behavior behind it.
- `weft/commands/status.py`, `weft/commands/tasks.py`, and
  `weft/commands/_task_history.py` own task inspection, short/full TID
  handling, and pipeline-aware status reconstruction.
- `weft/commands/manager.py` and `weft/commands/serve.py` own the manager
  lifecycle commands, while `_manager_bootstrap.py` provides the shared
  runtime seam.
- `weft/commands/queue.py` owns direct queue operations, endpoint resolution,
  queue watching, and alias management.
- `weft/commands/specs.py` and `weft/commands/validate_taskspec.py` own the
  CLI-facing spec management and validation surfaces; `weft/core/spec_store.py`
  owns the shared `NAME|PATH` resolution logic; `weft/core/spec_parameterization.py`
  and `weft/core/spec_run_input.py` own submission-time materialization and
  run-input shaping; `weft/core/pipelines.py` owns pipeline validation and
  compilation.
- `weft/commands/builtins.py` owns the shipped builtin inventory surface;
  `weft/commands/tidy.py`, `weft/commands/dump.py`, and `weft/commands/load.py`
  own `system` maintenance and broker-state export/import.
- The runtime side of those surfaces lives in `weft/core/tasks/base.py`,
  `weft/core/tasks/consumer.py`, `weft/core/tasks/interactive.py`,
  `weft/core/tasks/pipeline.py`, `weft/core/tasks/runner.py`, and
  `weft/core/manager.py`.

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
- plans describe proposed or historical implementation paths
- the plan index records which plans are active, completed, roadmap-only, or
  audit artifacts
- supersession should be explicit in the indexed metadata rather than left to
  grep and oral history

## Related Documents

- [`../../README.md`](../../README.md)
- [`00-Quick_Reference.md`](00-Quick_Reference.md)
- [`10-CLI_Interface.md`](10-CLI_Interface.md)
- [`10B-Builtin_TaskSpecs.md`](10B-Builtin_TaskSpecs.md)
- [`11-CLI_Architecture_Crosswalk.md`](11-CLI_Architecture_Crosswalk.md)
- [`12-Pipeline_Composition_and_UX.md`](12-Pipeline_Composition_and_UX.md)
- [`../plans/README.md`](../plans/README.md)
- [09A-Implementation_Roadmap_Planned.md](09A-Implementation_Roadmap_Planned.md)

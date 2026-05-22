# Task Monitor, Ping Flag, And Logs Directory Breaking Cleanup Plan

Status: completed
Source specs: see Source Documents below
Superseded by: none

## 1. Goal

Make the current observation and cleanup surfaces use the names and storage
locations we actually want: `weft task status TID --ping`, `weft system
task-monitor`, `TaskMonitor*` APIs, and operational disk output under the
configured logs directory. This is a breaking cleanup. Do not keep
`--probe-live`, `lifecycle-monitor`, `LifecycleMonitor*`, or
`.weft/archive/tasks` compatibility aliases.

## 2. Source Documents

Source specs:

- [`docs/specifications/00-Quick_Reference.md`](../specifications/00-Quick_Reference.md)
  "Queue Names", "CLI Surface", "Operational Files"
- [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md)
  [CC-2.1], [CC-2.3]
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-5], "Cleanup Boundary", "Queue Management Patterns"
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [OBS.12], [OBS.13], [OBS.17]
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
  [CLI-1.2], [CLI-6]

Related plans:

- [`2026-05-07-extended-ping-pong-state-probe-plan.md`](./2026-05-07-extended-ping-pong-state-probe-plan.md)
  introduced keyed PING/PONG state probing. It used `--probe-live` as a
  working name. This plan replaces that public spelling with `--ping`.
- [`2026-05-07-lifecycle-monitor-archive-sink-plan.md`](./2026-05-07-lifecycle-monitor-archive-sink-plan.md)
  introduced the foreground monitor under the lifecycle-monitor name and
  `.weft/archive/tasks` output. This plan intentionally supersedes those names
  in current code and specs, without a compatibility alias.
- [`2026-05-07-task-local-reaper-retention-policy-plan.md`](./2026-05-07-task-local-reaper-retention-policy-plan.md)
  introduced retention pruning and default retention-prune archive paths. This
  plan moves default operational disk output under the logs directory while
  preserving explicit `--archive` and `--report` path behavior.
- [`2026-05-07-manager-selection-ping-pong-liveness-plan.md`](./2026-05-07-manager-selection-ping-pong-liveness-plan.md)
  also relies on the keyed PING/PONG contract. Do not change the wire payload
  contract while renaming the task-status flag.

Guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/README.md`](../agent-context/README.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)

Spec deltas required before implementation is done:

- Replace `--probe-live` with `--ping` everywhere in [CLI-1.2].
- Replace `weft system lifecycle-monitor` with `weft system task-monitor` in
  [CLI-6], [MF-5], quick reference, and implementation mappings.
- Replace `LifecycleMonitor*` implementation mappings with `TaskMonitor*`.
- Replace `.weft/archive/tasks/YYYY-MM-DD.jsonl` defaults with
  `.weft/logs/task-monitor/YYYY-MM-DD.jsonl`.
- Define `WEFT_LOGS_DIR` as the configurable operational logs base. Default:
  `{project}/.weft/logs`.
- Update retention-prune default generated archive location to use the same
  logs base, for example `.weft/logs/retention-prune/YYYY-MM-DD-retention-prune.jsonl`.
  Explicit `--archive PATH` and `--report PATH` remain explicit and unchanged.
- Replace task-monitor `--archive-dir` with `--log-dir`. Do not keep
  `--archive-dir` as an alias.
- Add backlinks from touched spec sections to this plan.

## 3. Non-Goals

Do not implement these in this slice:

- No compatibility alias for `--probe-live`.
- No compatibility command named `weft system lifecycle-monitor`.
- No compatibility option named `--archive-dir` on `weft system task-monitor`.
- No compatibility import/re-export named `LifecycleMonitorConfig`,
  `LifecycleMonitorResult`, `LifecycleMonitorTask`, or
  `run_lifecycle_monitor`.
- No automatic PING in `weft task status TID`.
- No change to the PING/PONG wire payload, `request_id` matching, Docker
  runtime readout, or task evidence priority rules.
- No background monitor daemon or scheduler.
- No migration job that moves old `.weft/archive/tasks` files.
- No cleanup of historical docs or completed plans except cross-reference
  updates needed for current implementation traceability.
- No new dependency.

If implementation starts needing one of these, stop and re-plan. The request
is a breaking cleanup, not a compatibility migration or feature expansion.

## 4. Current Context

Current public state:

- `weft task status TID --probe-live` is the current CLI spelling for an
  explicit keyed PING/PONG current-state probe.
- `weft task status TID` without the flag is passive. It reconstructs state
  from queue evidence and must remain passive.
- `weft system lifecycle-monitor` is the current foreground monitor command.
  It scans `weft.log.tasks` without consuming messages.
- That command currently uses `--archive-dir` for disk output location.
- `weft/commands/lifecycle_monitor.py` owns command-layer monitor config,
  archive output, checkpoint handling, summary rendering, and
  `run_lifecycle_monitor()`.
- `weft/core/tasks/lifecycle_monitor.py` owns the task-shaped scanner
  primitive, `LifecycleMonitorTask`, and `make_lifecycle_monitor_taskspec()`.
- `weft/_constants.py` defines `LIFECYCLE_MONITOR_*` constants, including
  `LIFECYCLE_MONITOR_ARCHIVE_SUBDIR = "archive/tasks"` and
  `LIFECYCLE_MONITOR_CHECKPOINT_PATH = "state/lifecycle-monitor/default.json"`.
- `WeftContext.logs_dir` already exists and defaults to `{project}/.weft/logs`,
  but it is not currently configurable via a dedicated `WEFT_LOGS_DIR`.
- `weft/commands/retention_prune.py` currently resolves default force-mode
  archive output with `RETENTION_PRUNE_ARCHIVE_SUBDIR`, currently
  `archive/retention-prune`, under `ctx.weft_dir`.

The desired public state:

- `weft task status TID --ping` is the only CLI flag that sends a PING.
- `weft task status TID` remains passive by default.
- `weft system task-monitor` is the only monitor command.
- Module and class names use `task_monitor`, `TaskMonitorConfig`,
  `TaskMonitorResult`, `TaskMonitor`, and `run_task_monitor`.
- Disk output defaults use `ctx.logs_dir`, not `ctx.weft_dir / "archive"`.
- `WEFT_LOGS_DIR` can override the logs base. Relative values resolve under
  the project root. Absolute values are used as-is.

## 5. Files To Touch

Implementation files:

- `weft/cli/app.py`
  - Replace `--probe-live` with `--ping`.
  - Replace `@system_app.command("lifecycle-monitor")` with
    `@system_app.command("task-monitor")`.
  - Replace task-monitor disk option `--archive-dir` with `--log-dir`.
  - Import `TaskMonitorConfig`, `run_task_monitor`, and `ArchiveSinkName` from
    `weft.commands.task_monitor`.
- `weft/commands/tasks.py`
  - Rename public/internal argument names from `probe_live` to `ping` or
    `ping_live` only where this improves clarity.
  - Do not rename `task_evidence.probe_live_pong_evidence()` unless the
    implementation also updates all internal call sites cleanly. The function
    name is internal and accurately describes evidence gathering.
- `weft/commands/task_evidence.py`
  - Touch only if names in public JSON output or docstrings still expose
    `probe_live`. Do not change the PING/PONG payload contract.
- `weft/commands/lifecycle_monitor.py`
  - Rename or move to `weft/commands/task_monitor.py`.
  - Rename dataclasses and functions from `LifecycleMonitor*` to
    `TaskMonitor*`.
  - Update default disk output to `ctx.logs_dir / TASK_MONITOR_LOG_SUBDIR`.
  - Update checkpoint default to `.weft/state/task-monitor/default.json`.
- `weft/core/tasks/lifecycle_monitor.py`
  - Rename or move to `weft/core/tasks/task_monitor.py`.
  - Rename `LifecycleMonitorTask` to `TaskMonitor`.
  - Rename `make_lifecycle_monitor_taskspec()` to `make_task_monitor_taskspec()`.
  - Rename synthetic task name and metadata from `lifecycle-monitor` /
    `lifecycle_monitor` to `task-monitor` / `task_monitor`.
- `weft/core/tasks/__init__.py`
  - Export `TaskMonitor`.
  - Remove `LifecycleMonitorTask`.
- `weft/_constants.py`
  - Replace `LIFECYCLE_MONITOR_*` with `TASK_MONITOR_*`.
  - Add `WEFT_LOGS_DIR` config loading and override normalization.
  - Change `RETENTION_PRUNE_ARCHIVE_SUBDIR` to a logs-relative subdir name or
    replace it with `RETENTION_PRUNE_LOG_SUBDIR`.
- `weft/context.py`
  - Resolve `WeftContext.logs_dir` from config/env.
  - Keep default `{root}/{weft_dir_name}/logs`.
- `weft/commands/retention_prune.py`
  - Change default generated archive path to `ctx.logs_dir / retention-prune`.
  - Keep explicit `--archive` and `--report` behavior unchanged.

Tests:

- Rename `tests/commands/test_lifecycle_monitor.py` to
  `tests/commands/test_task_monitor.py`.
- Rename `tests/tasks/test_lifecycle_monitor.py` to
  `tests/tasks/test_task_monitor.py`.
- Update `tests/cli/test_cli_system.py`.
- Update `tests/commands/test_task_evidence.py`.
- Update `tests/system/test_constants.py`.
- Update `tests/context/test_context.py`.
- Update `tests/commands/test_retention_prune.py` if retention default path
  tests are added.
- Update `tests/conftest.py` if it names lifecycle monitor test paths.

Docs:

- `docs/specifications/00-Quick_Reference.md`
- `docs/specifications/01-Core_Components.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/plans/README.md`
- Existing completed plans only if the plan index or explicit "current
  behavior" statements would otherwise fail metadata/spec tests. Do not rewrite
  historical narrative.

Files to avoid unless a failing test proves otherwise:

- `weft/core/manager.py`
- `weft/core/tasks/base.py`
- `weft/core/tasks/consumer.py`
- `weft/commands/runtime_prune.py`
- `weft/commands/result.py`
- `weft/commands/status.py`

## 6. Read Before Editing

Required read order:

1. `AGENTS.md`
2. `docs/agent-context/README.md`
3. `docs/agent-context/decision-hierarchy.md`
4. `docs/agent-context/principles.md`
5. `docs/agent-context/engineering-principles.md`
6. `docs/agent-context/runbooks/writing-plans.md`
7. `docs/agent-context/runbooks/hardening-plans.md`
8. `docs/specifications/10-CLI_Interface.md` [CLI-1.2], [CLI-6]
9. `docs/specifications/05-Message_Flow_and_State.md` [MF-5]
10. `docs/specifications/07-System_Invariants.md` [OBS.12], [OBS.13], [OBS.17]
11. `weft/cli/app.py`
12. `weft/commands/lifecycle_monitor.py`
13. `weft/core/tasks/lifecycle_monitor.py`
14. `weft/context.py`
15. `weft/_constants.py`
16. `weft/commands/retention_prune.py`

Comprehension checks before coding:

- Does `weft task status TID` write to any queue by default? Answer: no, and
  it must remain passive.
- Is `--ping` a status alias for passive status? Answer: no, it writes a keyed
  PING to task-local control input and waits for a matching PONG.
- Should `weft system lifecycle-monitor` still work after this change?
  Answer: no.
- Should `from weft.commands.lifecycle_monitor import LifecycleMonitorConfig`
  still work after this change? Answer: no.
- Are task-monitor logs lifecycle truth? Answer: no. They are operational
  outputs only.
- Should default disk output go under `.weft/archive/tasks`? Answer: no.
- Should an explicit `--archive`, `--archive-dir`, or `--report` path be
  rewritten under logs? Answer: no for existing retention options. For
  task-monitor, the new explicit option is `--log-dir`; explicit paths win.

## 7. Invariants And Constraints

- Queues remain the source of truth.
- `weft task status TID` remains passive unless `--ping` is present.
- Keyed PING/PONG matching remains strict: matched TID and `request_id`.
- `weft status` must not ping every task.
- Task-monitor scans must use generator-based queue history reads, not fixed
  `peek_many(limit=...)`.
- Task-monitor must not consume, reserve, move, delete, or mutate
  `weft.log.tasks`.
- Task-monitor output and checkpoints are operational outputs only.
- Runtime prune and retention prune remain foreground operator commands.
- Retention prune ordinary apply still writes archive records before deletion.
- `--force` semantics from the retention-prune plan remain unchanged.
- No new dependency.
- No new compatibility aliases.
- No drive-by refactor beyond names and output locations required here.
- Keep code style consistent: `from __future__ import annotations`, complete
  type annotations, stdlib/third-party/local imports, constants in
  `_constants.py`, command-layer code outside `core`.

## 8. Bite-Sized Implementation Tasks

### Task 0: Baseline And Scope Guard

Outcome:

- Establish current failures and protect unrelated dirty work.

Files to read:

- `git status --short`
- `docs/plans/2026-05-07-task-monitor-ping-logs-breaking-cleanup-plan.md`

Actions:

1. Source the repo environment:

   ```bash
   . ./.envrc
   ```

2. Inspect dirty files:

   ```bash
   git status --short
   ```

3. Run focused baseline tests:

   ```bash
   ./.venv/bin/python -m pytest tests/commands/test_task_evidence.py tests/cli/test_cli_system.py tests/commands/test_lifecycle_monitor.py tests/tasks/test_lifecycle_monitor.py tests/context/test_context.py tests/system/test_constants.py -q
   ```

Stop and re-evaluate if:

- There are unrelated dirty changes in files this plan must edit and they
  cannot be preserved safely.
- Baseline failures are unrelated to the planned rename and would obscure the
  red-green loop.

### Task 1: Red Tests For `--ping` And Removed `--probe-live`

Outcome:

- CLI contract proves `--ping` is the only status probe flag.

Files to touch:

- `tests/cli/test_cli_system.py` if task status CLI tests live there.
- The actual task CLI test file if the repo has a more specific file after
  searching with `rg --files tests | rg 'task|cli'`.
- `tests/commands/test_task_evidence.py` only if command-layer names are
  updated in this task.

Tests:

- `weft task status TID --ping --json` sends a keyed PING and can surface
  `live_pong` evidence.
- `weft task status TID --probe-live` exits with a Typer unknown-option error.
- `weft task status TID` without `--ping` does not write a PING to
  `T{tid}.ctrl_in`.
- `weft status` does not write PING messages.

Test design:

- Use `WeftTestHarness` or real broker-backed queues.
- Do not mock `probe_live_pong_evidence()` and call the feature done.
- Assert queue-visible behavior: control queue has a PING only when `--ping`
  is used.

Expected red failure:

- `--ping` is unknown and `--probe-live` still works.

### Task 2: Implement `--ping` In The CLI

Outcome:

- `--ping` replaces `--probe-live`, with no compatibility alias.

Files to touch:

- `weft/cli/app.py`
- `weft/commands/tasks.py`
- `docs/specifications/10-CLI_Interface.md`

Implementation:

- In `weft/cli/app.py`, replace the `probe_live` Typer option with a `ping`
  option:

  ```python
  ping: Annotated[
      bool,
      typer.Option("--ping", help="Send a keyed PING and use the matched PONG as current-state proof"),
  ] = False
  ```

- Pass `probe_live=ping` to existing internal command code, or rename the
  internal parameter to `ping` if that can be done without churn.
- Remove every `--probe-live` CLI option registration.
- Update docs/spec text in the same change.

Do not:

- Add `--probe-live` as a hidden alias.
- Make passive status ping by default.
- Rename the PING/PONG wire fields.

Run:

```bash
./.venv/bin/python -m pytest tests/commands/test_task_evidence.py tests/cli/test_cli_system.py -q
```

### Task 3: Red Tests For `task-monitor` And Removed `lifecycle-monitor`

Outcome:

- Tests require the new command and reject the old command.

Files to touch:

- `tests/cli/test_cli_system.py`
- `tests/commands/test_lifecycle_monitor.py`, renamed in a later task
- `tests/tasks/test_lifecycle_monitor.py`, renamed in a later task

Tests:

- `weft system task-monitor --once --sink stdout --no-checkpoint` emits JSONL
  task-monitor records and does not consume `weft.log.tasks`.
- `weft system task-monitor --sink disk --json` emits a final command summary.
- `weft system task-monitor --sink disk --log-dir PATH --json` writes disk
  output under `PATH`.
- `weft system task-monitor --sink disk --archive-dir PATH` exits with a Typer
  unknown-option error.
- `weft system lifecycle-monitor` exits as an unknown command.
- stdout sink plus `--json` remains rejected because stdout is reserved for
  JSONL records in stdout-sink mode.

Expected red failure:

- `task-monitor` is unknown and `lifecycle-monitor` still works.

### Task 4: Rename Command-Layer Monitor API

Outcome:

- Command-layer code is named `task_monitor`, with no old API names.

Files to touch:

- Rename `weft/commands/lifecycle_monitor.py` to
  `weft/commands/task_monitor.py`.
- `weft/cli/app.py`
- `tests/commands/test_lifecycle_monitor.py`, rename to
  `tests/commands/test_task_monitor.py`.

Implementation:

- Rename:
  - `LifecycleMonitorConfig` to `TaskMonitorConfig`
  - `LifecycleMonitorCheckpoint` to `TaskMonitorCheckpoint`
  - `LifecycleMonitorResult` to `TaskMonitorResult`
  - `run_lifecycle_monitor` to `run_task_monitor`
- Keep `ArchiveSinkName` if the name still fits, or rename to
  `TaskMonitorSinkName` only if it reduces confusion without spreading churn.
- Replace record type strings from `lifecycle_monitor_*` to
  `task_monitor_*` if they exist in emitted JSON.
- Replace human-readable labels from "lifecycle monitor" to "task monitor".
- Remove the old module file. Do not leave a wrapper module.

Stop and re-evaluate if:

- A large generic monitor abstraction starts to appear. This is a rename and
  location cleanup, not a monitoring framework.

Run:

```bash
./.venv/bin/python -m pytest tests/commands/test_task_monitor.py tests/cli/test_cli_system.py -q
```

### Task 5: Rename Core Task-Shaped Monitor Primitive

Outcome:

- Core task primitive uses `TaskMonitor`.

Files to touch:

- Rename `weft/core/tasks/lifecycle_monitor.py` to
  `weft/core/tasks/task_monitor.py`.
- `weft/core/tasks/__init__.py`
- `weft/commands/task_monitor.py`
- Rename `tests/tasks/test_lifecycle_monitor.py` to
  `tests/tasks/test_task_monitor.py`.
- `tests/conftest.py`

Implementation:

- Rename:
  - `LifecycleMonitorTask` to `TaskMonitor`
  - `LifecycleMonitorCallback` to `TaskMonitorCallback`
  - `make_lifecycle_monitor_taskspec()` to `make_task_monitor_taskspec()`
  - `noop_monitor_target()` may stay if still clear, but prefer
    `noop_task_monitor_target()`
- Synthetic TaskSpec fields:
  - `name="task-monitor"`
  - `function_target="weft.core.tasks.task_monitor:noop_task_monitor_target"`
  - `metadata={"internal": True, "weft_runtime": "task_monitor"}`
- Export `TaskMonitor` in `weft/core/tasks/__init__.py`.
- Remove `LifecycleMonitorTask` export. No alias.

Tests:

- Core task monitor scan peeks `weft.log.tasks` without consuming.
- The synthetic TaskSpec has the new function target and metadata.
- Old import path `weft.core.tasks.lifecycle_monitor` is not used anywhere in
  current tests or implementation.

Run:

```bash
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py tests/commands/test_task_monitor.py -q
```

### Task 6: Add Configurable Logs Directory

Outcome:

- `WeftContext.logs_dir` is configurable through `WEFT_LOGS_DIR`.

Files to touch:

- `weft/_constants.py`
- `weft/context.py`
- `tests/system/test_constants.py`
- `tests/context/test_context.py`

Implementation:

- Add `WEFT_LOGS_DIR` to `_load_weft_env_vars()`.
- Add override normalization in `_normalize_weft_override_value()`.
- Parsing rule:
  - empty or unset means default `{weft_dir}/logs`
  - absolute path means use exactly that path
  - relative path means resolve under project root
- In `build_context()`, compute:

  ```python
  logs_dir_config = resolved_config.get("WEFT_LOGS_DIR")
  logs_dir = _resolve_project_path(root, logs_dir_config) if logs_dir_config else weft_dir / "logs"
  ```

  Use an existing path helper if one already exists. If not, add a narrow
  private helper in `weft/context.py`; do not create a general path framework.
- Ensure `logs_dir` is created when `create_dirs=True`.
- Preserve `ctx.logs_dir == ctx.weft_dir / "logs"` by default.

Tests:

- Default context creates `.weft/logs`.
- `WEFT_LOGS_DIR=ops-logs` creates `{project}/ops-logs`.
- `WEFT_LOGS_DIR=/tmp/weft-logs-test` uses that absolute path.
- In-process config override works if existing context tests cover config
  overrides.
- Invalid type override raises a clear `TypeError`.

Do not:

- Reuse `WEFT_LOGGING_ENABLED`; that controls Python logging emission, not log
  file placement.
- Change `outputs_dir` or `weft_dir` semantics.

### Task 7: Move Task-Monitor Disk Output To Logs

Outcome:

- Task-monitor disk sink writes under `ctx.logs_dir / "task-monitor"` by
  default.

Files to touch:

- `weft/_constants.py`
- `weft/commands/task_monitor.py`
- `tests/commands/test_task_monitor.py`
- `tests/cli/test_cli_system.py`

Implementation:

- Replace:
  - `LIFECYCLE_MONITOR_SCHEMA_VERSION` with `TASK_MONITOR_SCHEMA_VERSION`
  - `LIFECYCLE_MONITOR_ARCHIVE_SUBDIR` with
    `TASK_MONITOR_LOG_SUBDIR = "task-monitor"`
  - `LIFECYCLE_MONITOR_CHECKPOINT_PATH` with
    `TASK_MONITOR_CHECKPOINT_PATH = "state/task-monitor/default.json"`
  - `LIFECYCLE_MONITOR_WEFT_ANOMALY_CLASSIFICATIONS` with
    `TASK_MONITOR_WEFT_ANOMALY_CLASSIFICATIONS`
- Default archive/log directory resolver should return
  `ctx.logs_dir / TASK_MONITOR_LOG_SUBDIR`.
- Replace explicit `--archive-dir PATH` with `--log-dir PATH`. Explicit
  `--log-dir PATH` wins over `WEFT_LOGS_DIR`.
- Keep checkpoint path under `ctx.weft_dir / "state/task-monitor/default.json"`
  unless `--checkpoint PATH` is supplied.

Tests:

- Disk sink with no `--log-dir` writes under `.weft/logs/task-monitor`.
- Disk sink with `WEFT_LOGS_DIR=ops-logs` writes under `ops-logs/task-monitor`.
- Disk sink with explicit `--log-dir PATH` writes to `PATH`, not logs dir.
- Disk sink with old `--archive-dir PATH` is rejected.
- Checkpoint default is `.weft/state/task-monitor/default.json`.
- No test expects `.weft/archive/tasks`.

### Task 8: Move Retention-Prune Default Operational Output To Logs

Outcome:

- Retention-prune generated default archive path uses `ctx.logs_dir`.

Files to touch:

- `weft/_constants.py`
- `weft/commands/retention_prune.py`
- `tests/commands/test_retention_prune.py`
- `docs/specifications/00-Quick_Reference.md`
- `docs/specifications/10-CLI_Interface.md`

Implementation:

- Replace `RETENTION_PRUNE_ARCHIVE_SUBDIR = "archive/retention-prune"` with a
  logs-oriented name, for example:

  ```python
  RETENTION_PRUNE_LOG_SUBDIR: Final[str] = "retention-prune"
  ```

- In `_resolve_archive_path(ctx, archive_path)`:
  - explicit file path still wins
  - explicit directory path still writes inside that directory
  - omitted path uses `ctx.logs_dir / RETENTION_PRUNE_LOG_SUBDIR /
    YYYY-MM-DD-retention-prune.jsonl`
- Do not weaken archive-before-delete semantics for ordinary apply.
- Do not change `--archive PATH` semantics.

Tests:

- Force apply without explicit archive writes best-effort archive under
  `.weft/logs/retention-prune` when possible.
- `WEFT_LOGS_DIR` changes that default.
- Explicit `--archive PATH` writes to `PATH`.

Stop and re-evaluate if:

- The implementation starts treating retention archives as lifecycle truth.
  They are operational logs, even though ordinary apply requires them before
  deletion.

### Task 9: Remove Old Names Everywhere

Outcome:

- The old public and code names are gone from current implementation and specs.

Files to touch:

- All files found by:

  ```bash
  rg -n "lifecycle-monitor|lifecycle_monitor|LifecycleMonitor|--probe-live|--archive-dir|probe_live|archive/tasks|LIFECYCLE_MONITOR" weft tests docs/specifications README.md
  ```

Implementation:

- Replace current implementation/spec references with task-monitor names.
- Keep historical completed plans as historical documents unless they are
  active current-behavior plans that would confuse implementation. Do not
  rewrite all old plans just to erase history.
- `probe_live` may remain only as an internal variable if it is private and no
  public docs/tests expose it. Prefer renaming to `ping` when touching nearby
  code.

Required negative checks:

```bash
! rg -n -- "--probe-live|--archive-dir" weft tests docs/specifications README.md
! rg -n -- "lifecycle-monitor|lifecycle_monitor|LifecycleMonitor|LIFECYCLE_MONITOR" weft tests docs/specifications README.md
! rg -n -- "archive/tasks" weft tests docs/specifications README.md
```

If these commands find only historical completed plans, decide explicitly
whether to leave those references as history or update the plan index notes.
Do not leave old names in current specs, tests, command help, or source code.

### Task 10: Specs, Quick Reference, And Plan Index

Outcome:

- Specs are the source of truth again.

Files to touch:

- `docs/specifications/00-Quick_Reference.md`
- `docs/specifications/01-Core_Components.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/plans/README.md`
- This plan file

Implementation:

- Update CLI docs:
  - `weft task status TID --ping`
  - `weft system task-monitor`
  - `weft system task-monitor --log-dir PATH`
  - no `lifecycle-monitor`
  - no `--probe-live`
  - no task-monitor `--archive-dir`
- Update operational files:
  - `.weft/logs/task-monitor/YYYY-MM-DD.jsonl`
  - `.weft/logs/retention-prune/YYYY-MM-DD-retention-prune.jsonl`
  - `.weft/state/task-monitor/default.json`
- Update implementation mappings:
  - `weft/commands/task_monitor.py`
  - `weft/core/tasks/task_monitor.py`
- Add `WEFT_LOGS_DIR` to config/environment reference sections.
- Mark this plan `completed` only after implementation and verification pass.
- Update `docs/plans/README.md` count and row status.

Run:

```bash
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
```

### Task 11: Final Verification Gates

Run focused gates:

```bash
./.venv/bin/python -m pytest tests/commands/test_task_evidence.py -q
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py tests/commands/test_task_monitor.py -q
./.venv/bin/python -m pytest tests/cli/test_cli_system.py -q
./.venv/bin/python -m pytest tests/context/test_context.py tests/system/test_constants.py -q
./.venv/bin/python -m pytest tests/commands/test_retention_prune.py -q
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
```

Run full gates:

```bash
./.venv/bin/ruff check weft tests
./.venv/bin/ruff format --check weft tests extensions integrations
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/python -m pytest -q
```

If Postgres-specific path behavior is touched beyond config resolution, run:

```bash
./.venv/bin/python bin/pytest-pg --all
```

## 9. Test Design Rules

Prefer real behavior:

- Use real broker-backed queues for PING/PONG and monitor scans.
- Use `WeftTestHarness` when a full task lifecycle is needed.
- Use `build_context()` and `prepare_project_root()` for command-layer tests.
- Assert files on disk for logs-dir behavior.

Do not mock:

- `simplebroker.Queue`
- PING/PONG queue writes
- queue generator reads
- task-log replay
- context path creation

Mocks are acceptable only for:

- environment variables via pytest monkeypatch or `patch.dict`
- filesystem failure when no real fixture can express the failure
- time source only if deterministic timestamp tests cannot use existing
  controls

Important invariants to test:

- `weft task status TID` without `--ping` writes no control message.
- `weft task status TID --ping` writes exactly a keyed PING and accepts only
  the matched PONG.
- `--probe-live` is gone.
- `weft system task-monitor` does not consume `weft.log.tasks`.
- `weft system lifecycle-monitor` is gone.
- Disk task-monitor output defaults under logs dir.
- `WEFT_LOGS_DIR` changes default disk output.
- Explicit disk paths override `WEFT_LOGS_DIR`.
- Task-monitor `--archive-dir` is gone.
- Retention ordinary apply still archives before delete.
- Retention force semantics remain a human override but not a fake exact-ID
  generator.

Anti-patterns:

- A test that only checks a function was called instead of checking queue
  messages.
- A test that mocks monitor scanning instead of writing `weft.log.tasks`.
- A compatibility test proving old names still work.
- A broad "rename everything" script that changes historical prose in ways
  that hide why earlier decisions were made.

## 10. Rollout And Rollback

Rollout:

1. Land the breaking rename and path changes together.
2. Update specs in the same change.
3. Run full local gates.
4. Communicate the new commands directly:
   - `weft task status TID --ping`
   - `weft system task-monitor`
   - disk output under `.weft/logs/...`

Rollback:

- Code rollback restores old names if needed, but there is no compatibility
  bridge in this plan.
- Existing files under `.weft/archive/tasks` are not migrated or deleted by
  this change. They remain historical operational files.
- New files under `.weft/logs/task-monitor` are operational logs. Rolling back
  does not make them lifecycle truth.

One-way doors:

- Public CLI breakage for `--probe-live` and `lifecycle-monitor`.
- Public CLI breakage for task-monitor `--archive-dir`.
- Public Python import breakage for `LifecycleMonitor*`.
- Default operational output path changes.

These one-way doors are intentional. Do not soften them with aliases.

## 11. Independent Review Checklist

Ask an independent reviewer:

- Does the plan remove old public names instead of preserving aliases?
- Does passive task status stay passive?
- Does `--ping` still use the existing keyed PING/PONG evidence path?
- Does task-monitor remain a foreground non-consuming scanner?
- Does disk output default under `ctx.logs_dir`?
- Does `WEFT_LOGS_DIR` have clear absolute and relative path semantics?
- Are explicit CLI paths still highest precedence?
- Does retention prune keep exact-message and archive-before-delete semantics?
- Are tests real-queue and filesystem based rather than mock-heavy?
- Are specs updated as the source of truth?

## 12. Fresh-Eyes Review Of This Plan

Review pass 1 found a compatibility trap: it would be easy to keep hidden
aliases while renaming the visible help text. The plan now explicitly forbids
aliases for CLI commands, flags, modules, classes, and re-exports.

Review pass 2 found a behavior trap: `--ping` might tempt an implementer to
make single-task status probe by default. The plan now repeats that passive
status remains passive and requires a negative queue-write test.

Review pass 3 found a storage ambiguity: "logs directory" could mean Python
logging, lifecycle archives, retention archives, or all operational disk
outputs. The plan now defines `WEFT_LOGS_DIR` as an operational logs base,
leaves `WEFT_LOGGING_ENABLED` alone, and routes task-monitor plus default
retention-prune generated output through `ctx.logs_dir`.

Review pass 4 found a drift risk: renaming lifecycle monitor to task monitor
could turn into a generic monitoring framework. The plan now names the exact
files and classes to rename and forbids new abstractions.

Review pass 5 found a historical-doc risk: old completed plans will naturally
contain old names. The plan now scopes negative name checks to source, tests,
current specs, and README. Historical plans should not be rewritten unless
they are active current-behavior documents or metadata tests require indexing.

This plan still matches the requested direction. It is a breaking cleanup of
names and operational output locations, not a materially different lifecycle
or cleanup architecture.

# Lifecycle Monitor Archive Sink Plan

Status: draft
Source specs: see Source Documents below
Superseded by: none

Parent plan:
[`docs/plans/2026-05-06-lifecycle-reconciliation-architecture-plan.md`](./2026-05-06-lifecycle-reconciliation-architecture-plan.md)
Release slice: Release 4

## 1. Goal

Introduce a peek-based lifecycle monitor that can scan Weft lifecycle evidence,
track a restart-safe high-water mark, and export compact task summaries to
stdout or append-only disk JSONL without consuming or deleting broker messages.

This release is deliberately non-destructive. It gives operators a dry-run
reporting surface and creates the archive substrate that later pruning releases
can rely on. It must not become lifecycle authority, a second status database,
or a cleanup daemon.

The first implementation should run as a foreground system command using a
task-shaped peek loop. Do not make it a manager-autostart internal service in
this release. A long-lived internal service can be a later slice after the
dry-run report format and ops behavior are proven.

## 2. Source Documents

Source specs:

- [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md)
  [CC-2.1], [CC-2.3], [CC-2.4], [CC-3.4]
- [`docs/specifications/04-SimpleBroker_Integration.md`](../specifications/04-SimpleBroker_Integration.md)
  [SB-0.4]
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-2], [MF-3], [MF-5], "Cleanup Boundary", "Queue Management Patterns"
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [STATE.1], [STATE.2], [STATE.6], [QUEUE.4], [OBS.1], [OBS.2],
  [OBS.3], [OBS.11], [CTX.3]
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
  [CLI-6]

Guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/runtime-and-context-patterns.md`](../agent-context/runbooks/runtime-and-context-patterns.md)
- [`docs/agent-context/runbooks/testing-patterns.md`](../agent-context/runbooks/testing-patterns.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)

Related plans:

- [`docs/plans/2026-05-06-lifecycle-reconciliation-architecture-plan.md`](./2026-05-06-lifecycle-reconciliation-architecture-plan.md)
  defines the release train and Release 4 boundary.
- [`docs/plans/2026-05-06-status-coherence-and-stale-pid-liveness-plan.md`](./2026-05-06-status-coherence-and-stale-pid-liveness-plan.md)
  is Release 1. Its public-status coherence rules must remain independent of
  monitor checkpoints.
- [`docs/plans/2026-05-06-task-evidence-reconciliation-model-plan.md`](./2026-05-06-task-evidence-reconciliation-model-plan.md)
  is Release 2. The monitor must reuse that shared evidence model instead of
  inventing a second classifier.
- [`docs/plans/2026-05-06-terminal-publication-hardening-plan.md`](./2026-05-06-terminal-publication-hardening-plan.md)
  is Release 3. Its task-owned terminal `ctrl_out` envelope gives the monitor
  stronger terminal proof for new one-shot tasks.
- [`docs/plans/2026-04-30-task-log-cursor-high-water-mark-plan.md`](./2026-04-30-task-log-cursor-high-water-mark-plan.md)
  is relevant because this release must preserve generator-based high-water
  replay and avoid fixed-limit reads.

Spec delta required before implementation is done:

- Update [CC-2.3] to include the new lifecycle monitor task-shaped runtime if
  a concrete `LifecycleMonitorTask` class lands.
- Update [MF-5] to state that lifecycle-monitor checkpoints are operational
  cursors, not lifecycle truth.
- Update [MF-5] and "Cleanup Boundary" to describe append-only archive/stdout
  summaries and to keep destructive reaping out of this release.
- Update [CLI-6] with the new `weft system lifecycle-monitor` command if that
  CLI surface lands.
- Add backlinks from touched spec sections to this plan.

## 3. Non-Goals

Do not implement these in Release 4:

- Any broker message deletion.
- Any task-local reaping.
- Any pruning of `weft.state.*` queues.
- Any Manager responsibility for scanning, archiving, or cleanup.
- Any manager-autostart or internal endpoint service for the lifecycle monitor.
- Any dependence of `weft status`, `weft task status`, or `weft result` on
  monitor memory, checkpoints, or archive files.
- Any new public lifecycle state.
- Any retry engine for failed domain tasks.
- Any cross-queue transaction or SimpleBroker backend-specific SQL shortcut.
- Any hidden `weft run --monitor` behavior. That flag is currently rejected and
  should stay unrelated to this release.

If implementation starts needing one of these, stop. The plan has drifted into
Release 5, Release 6, or a separate service/autostart design.

## 4. Current Context

The current code already has two task-shaped observation primitives:

- `weft/core/tasks/observer.py`:
  - `Observer` peeks without consuming.
  - `SamplingObserver` peeks at intervals.
  - This is the closer behavioral model for lifecycle monitoring.
- `weft/core/tasks/monitor.py`:
  - `Monitor` reserves inbox messages and forwards them downstream.
  - This is the wrong default for lifecycle monitoring because Release 4 must
    not consume, move, or reserve lifecycle messages.

The current shared evidence model lives in:

- `weft/commands/task_evidence.py`

That module is command-layer read-model code. Core task code must not import
it. This is an important layer boundary. The implementation should keep
evidence classification in the command layer, or extract a narrow
layer-neutral helper only if a failing test proves a core task must classify
evidence directly.

The current status read model lives in:

- `weft/commands/system.py`
- `weft/commands/tasks.py`
- `weft/commands/result.py`

The monitor can reuse those read-model helpers, but the status read model must
not start depending on monitor output.

The queue that drives this release is:

- `weft.log.tasks`, exposed by `WEFT_GLOBAL_LOG_QUEUE`

The monitor should scan that queue with generator/high-water semantics and then
inspect task-local queues through shared evidence helpers only when it has a
known TID to summarize. It should not try to watch every `T{tid}.*` queue as a
primary input.

## 5. Architecture Decision

Implement a foreground dry-run monitor command backed by a task-shaped peek
loop and command-layer summarization.

Concrete shape:

- `LifecycleMonitorTask` or equivalent lives under `weft/core/tasks/`.
  It is a small `BaseTask`/`Observer`-style class that peeks selected queues
  and invokes an injected callback. It does not archive, classify, delete, or
  import command modules.
- `weft.commands.lifecycle_monitor` owns the monitor runner, summary builder,
  checkpoint store, archive sinks, and public command function.
  This command-layer module may reuse `weft.commands.task_evidence`.
- `weft/cli/app.py` exposes `weft system lifecycle-monitor`.
- The default command mode is one foreground pass in dry-run/report mode.
  Follow mode is allowed if it remains non-destructive and interrupt-safe.
- Checkpoints live under the Weft project context and are operational hints
  only. Deleting the checkpoint may duplicate archive records, but it must not
  change lifecycle truth or task status.

Why not a manager-autostart service yet:

- It would require new internal runtime class constants, spawn-envelope support,
  endpoint naming, and lifecycle service ownership.
- It would make this slice about service management instead of report
  correctness.
- The current ops problem needs safe visibility before safe deletion.
- A foreground command can be run under an external supervisor or one-shot cron
  without changing Weft task execution semantics.

Counterargument:

- A true internal monitor service may eventually be useful. That is defensible
  after the dry-run monitor proves its schema, checkpoint behavior, and ops
  value. It should not be first because it increases blast radius before we
  know the report is right.

## 6. Target CLI Contract

Add one command:

```bash
weft system lifecycle-monitor
```

Recommended first-slice options:

- `--context PATH`
  - Reuse existing system command context patterns.
- `--once / --follow`
  - Default: `--once`.
  - `--once` scans from checkpoint or start, writes summaries, updates
    checkpoint, and exits.
  - `--follow` continues waiting for new task-log entries and stops cleanly on
    interrupt.
- `--sink stdout|disk`
  - Default: `stdout`.
  - `stdout` writes JSONL records to stdout.
  - `disk` appends JSONL records under the archive directory.
- `--archive-dir PATH`
  - Default: `{context}/{WEFT_DIRECTORY_NAME}/archive/tasks`.
  - Applies only to `--sink disk`.
- `--checkpoint PATH`
  - Default: `{context}/{WEFT_DIRECTORY_NAME}/state/lifecycle-monitor/default.json`.
- `--no-checkpoint`
  - Do not read or write checkpoint. Useful for ad hoc full scans.
- `--since TIMESTAMP`
  - Start after this task-log timestamp for this invocation.
  - If combined with checkpoint, `--since` wins for the scan start, but the
    command should not overwrite an existing later checkpoint with an older
    timestamp.
- `--limit EVENTS`
  - Optional cap on task-log events processed in one invocation.
  - Useful for ops dry runs and tests.
- `--json`
  - Applies only with `--sink disk`.
  - Emits the final command summary as JSON on stdout instead of human text.
  - Reject `--sink stdout --json` in this release. Stdout is reserved for
    archive JSONL records when stdout is the sink.
  - Do not make this change the archive record format. The archive sink remains
    JSONL.

Exit codes:

- `0`: scan completed successfully
- `1`: invalid arguments, invalid checkpoint, archive write failure, or broker
  read failure

Do not add cleanup flags such as `--delete`, `--reap`, `--prune`, or
`--active`. Those belong to later releases.

## 7. Archive Record Contract

Archive output is JSON Lines. Every line is a single JSON object.

All records include:

```json
{
  "schema_version": 1,
  "record_type": "task_summary",
  "monitor_run_id": "2026-05-07T12:00:00.000000Z:pid-12345",
  "emitted_at": 1770000000000000000
}
```

Task summary records include:

```json
{
  "schema_version": 1,
  "record_type": "task_summary",
  "monitor_run_id": "2026-05-07T12:00:00.000000Z:pid-12345",
  "summary_id": "1778084345905438720:terminal_ctrl_out:1778089999999999999",
  "tid": "1778084345905438720",
  "tid_short": "345905438720",
  "name": "wazuh-case-rollup",
  "status": "completed",
  "classification": "terminal_ctrl_out",
  "source": "ctrl_out",
  "event": "work_completed",
  "last_task_log_timestamp": 1778089999999999999,
  "started_at": 1778080000000000000,
  "completed_at": 1778089999999999999,
  "return_code": 0,
  "error": null,
  "failure_owner": null,
  "cleanup_candidate": false,
  "reconciliation": {
    "classification": "terminal_ctrl_out"
  }
}
```

Allowed `record_type` values for Release 4:

- `monitor_run_started`
- `task_summary`
- `monitor_run_completed`
- `monitor_warning`

Allowed `classification` values should reuse Release 2 where possible:

- `terminal_log`
- `terminal_ctrl_out`
- `wrapper_lost`
- `result_without_terminal`
- `runtime_conflict`
- `stale_created`
- `orphaned_reserved`
- `orphaned_inbox`
- `domain_failure`
- `unknown`

Use conservative failure ownership:

- `failure_owner="weft_lifecycle"` only for Weft-owned evidence anomalies such
  as `wrapper_lost`, `result_without_terminal`, `runtime_conflict`,
  `stale_created`, `orphaned_reserved`, or `orphaned_inbox`.
- `failure_owner="task_or_runner"` for terminal task failures without a Weft
  lifecycle anomaly.
- `failure_owner=null` for successful terminal tasks.

Do not pretend every `work_failed` event is certainly a domain failure. It is a
task-or-runner terminal outcome unless evidence proves a Weft lifecycle
problem. The summary can still keep it out of cleanup candidates.

`cleanup_candidate` must always be `false` in Release 4. Later reaper releases
can add candidate logic after archive output is proven.

## 8. Checkpoint Contract

Checkpoint file:

```json
{
  "schema_version": 1,
  "monitor_name": "default",
  "updated_at": 1770000000000000000,
  "last_task_log_timestamp": 1770000000000000000
}
```

Rules:

- The checkpoint is an operational cursor only.
- It never changes task lifecycle truth.
- It never hides evidence from `weft status`.
- It is written only after archive/stdout output for the processed batch
  succeeds.
- If a process crashes after archive write but before checkpoint write,
  duplicate summary records are acceptable on restart. Each summary must carry
  a stable `summary_id` so downstream tools can dedupe.
- Normal restart after a successful checkpoint must not duplicate summaries.
- A corrupt checkpoint should produce a clear command error unless the user
  explicitly passes `--no-checkpoint` or a future reset flag.
- Do not store raw task messages in the checkpoint.
- Do not store task lifecycle state in the checkpoint.

## 9. Files To Touch

Implementation files:

- `weft/core/tasks/lifecycle_monitor.py`
  - New small task-shaped peek loop.
  - It should configure `WEFT_GLOBAL_LOG_QUEUE` in peek mode and handle its
    own `ctrl_in` for STOP if the task wrapper uses task-local control.
  - It should call an injected callback with `(queue_name, message, timestamp)`.
  - It should not classify evidence or write archive records.
- `weft/core/tasks/__init__.py`
  - Re-export the new task class if it is public within core task primitives.
- `weft/commands/lifecycle_monitor.py`
  - New command-layer runner, summary builder, archive sink, checkpoint store,
    and command function.
  - This is the main behavior module.
  - It may import `weft.commands.task_evidence`.
- `weft/cli/app.py`
  - Add `weft system lifecycle-monitor` command wrapper.
  - Keep CLI parsing thin. The command module owns behavior.
- `weft/_constants.py`
  - Add only small constants that prevent repeated literals:
    - schema version
    - archive subdirectory name
    - checkpoint subdirectory/name
  - Do not put policy logic in constants.
- `weft/commands/__init__.py`
  - Export the command function only if the repo convention requires it.

Files to avoid unless a failing test proves otherwise:

- `weft/core/manager.py`
  - Do not add manager autostart or internal runtime support in this release.
- `weft/commands/system.py`
  - Avoid modifying status collection. The monitor must not become status
    authority.
- `weft/commands/task_evidence.py`
  - Reuse it. Touch only for a small shared helper if the monitor exposes a
    genuine duplication gap.
- `weft/core/tasks/monitor.py`
  - Do not change reserve-and-forward semantics.
- `weft/core/tasks/observer.py`
  - Reuse patterns. Touch only if a tiny generic extension is clearly better
    than a new lifecycle-specific task class.
- `weft/core/taskspec/model.py`
  - Do not add TaskSpec fields for monitor configuration in this release.

Test files:

- `tests/tasks/test_lifecycle_monitor.py`
  - New task-shaped peek loop tests.
- `tests/commands/test_lifecycle_monitor.py`
  - Main behavior tests for summary building, archive sinks, checkpoints, and
    non-consuming scans.
- `tests/cli/test_cli_system.py`
  - Add thin CLI coverage for command wiring and argument validation.
- Existing Release 2 tests:
  - `tests/commands/test_task_evidence.py`
  - `tests/commands/test_status.py`
  - Run them to prove classification/status behavior still works.

Docs:

- `docs/specifications/01-Core_Components.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/specifications/00-Quick_Reference.md` if the new command or constants
  belong in quick reference.
- `docs/lessons.md` only if implementation exposes a durable lesson not
  already captured.

## 10. Read Before Editing

Required read order for implementer:

1. `AGENTS.md`
2. `docs/agent-context/README.md`
3. `docs/agent-context/decision-hierarchy.md`
4. `docs/agent-context/principles.md`
5. `docs/agent-context/engineering-principles.md`
6. `docs/agent-context/runbooks/testing-patterns.md`
7. `docs/agent-context/runbooks/hardening-plans.md`
8. `docs/plans/2026-05-06-lifecycle-reconciliation-architecture-plan.md`
9. `docs/plans/2026-05-06-task-evidence-reconciliation-model-plan.md`
10. `docs/plans/2026-05-06-terminal-publication-hardening-plan.md`
11. `docs/specifications/01-Core_Components.md` [CC-2.1], [CC-2.3]
12. `docs/specifications/05-Message_Flow_and_State.md` [MF-3], [MF-5]
13. `docs/specifications/07-System_Invariants.md` [OBS.1], [OBS.2],
    [OBS.3], [OBS.11]
14. `docs/specifications/10-CLI_Interface.md` [CLI-6]

Code to read:

- `weft/core/tasks/observer.py`
- `weft/core/tasks/monitor.py`
- `weft/core/tasks/multiqueue_watcher.py`
- `weft/core/tasks/base.py`
- `weft/commands/task_evidence.py`
- `weft/commands/system.py`
- `weft/commands/tasks.py`
- `weft/commands/_task_history.py`
- `weft/commands/_streaming.py`
- `weft/helpers/__init__.py`, especially `iter_queue_json_entries()`
- `weft/cli/app.py`, especially existing `system` subcommands
- `tests/tasks/test_task_observer_behavior.py`
- `tests/commands/test_task_evidence.py`
- `tests/commands/test_status.py`
- `tests/cli/test_cli_system.py`

Comprehension checks before coding:

- Which queue is the main lifecycle input? Answer: `weft.log.tasks`.
- Should the monitor consume any broker message? Answer: no.
- Is the checkpoint lifecycle truth? Answer: no.
- May `weft status` depend on the monitor? Answer: no.
- May the monitor import `weft.commands.task_evidence` from core task code?
  Answer: no.
- Is `Monitor` in `weft/core/tasks/monitor.py` the right base class? Answer:
  no, because it reserves and forwards messages.
- Is `Observer` closer? Answer: yes, because it peeks.
- Can Release 4 delete stale task-local messages? Answer: no.
- Should every `work_failed` be labeled a definite domain failure? Answer: no.
  It is a task-or-runner terminal outcome unless a Weft lifecycle anomaly is
  proven.

## 11. Bite-Sized Implementation Tasks

### Task 0: Baseline And Scope Guard

Owner: implementing engineer.

Actions:

1. Source the repo environment:

   ```bash
   . ./.envrc
   ```

2. Run baseline tests before editing:

   ```bash
   ./.venv/bin/python -m pytest tests/tasks/test_task_observer_behavior.py tests/commands/test_task_evidence.py tests/commands/test_status.py -q
   ```

3. Inspect the worktree:

   ```bash
   git status --short
   ```

4. If unrelated files are dirty, do not revert them. Keep this slice scoped to
   lifecycle monitor files, tests, and docs.

Expected result:

- Baseline is known.
- Scope is explicit.

### Task 1: Red Test For Non-Consuming Peek Task

Owner: implementing engineer.

Files to create or touch:

- `tests/tasks/test_lifecycle_monitor.py`

Test design:

- Use `broker_env` with real queues.
- Write two JSON task-log messages to `WEFT_GLOBAL_LOG_QUEUE`.
- Instantiate the new `LifecycleMonitorTask` or equivalent with a callback
  that records `(queue_name, message, timestamp)`.
- Run one drain/pass.
- Assert the callback saw both messages.
- Assert `WEFT_GLOBAL_LOG_QUEUE` still contains the original messages after
  the pass.
- Send STOP through the task-local `ctrl_in` if the task supports control and
  assert it stops without deleting task-log messages.

Expected red failure:

- The class does not exist yet.

Do not:

- Use `Monitor`, because it reserves and forwards.
- Mock Queue.
- Assert internal call counts when queue state proves behavior.

### Task 2: Implement The Task-Shaped Peek Loop

Owner: implementing engineer.

Files to touch:

- `weft/core/tasks/lifecycle_monitor.py`
- `weft/core/tasks/__init__.py`

Implementation:

- Build a small class, suggested name `LifecycleMonitorTask`.
- Base it on `BaseTask` directly or an `Observer`-style helper.
- Configure `WEFT_GLOBAL_LOG_QUEUE` in `QueueMode.PEEK`.
- Configure task-local `ctrl_in` in peek mode if STOP support is useful.
- Invoke an injected callback with queue name, message, and timestamp.
- Do not create a reserved queue.
- Do not write archive records in this core class.
- Do not import `weft.commands.*`.
- Keep process title and control behavior consistent with existing task
  patterns if the class inherits `BaseTask`.

Run:

```bash
./.venv/bin/python -m pytest tests/tasks/test_lifecycle_monitor.py -q
```

Expected result:

- New task tests pass.
- Task-log messages remain unconsumed.

### Task 3: Red Tests For Archive Sinks And Checkpoints

Owner: implementing engineer.

Files to create:

- `tests/commands/test_lifecycle_monitor.py`

Test cases:

1. Disk sink appends JSONL:
   - Create a temp archive dir.
   - Write one summary record through the disk sink.
   - Write a second summary record.
   - Assert the file has two lines and both parse as JSON.
   - Assert the first line was not overwritten.

2. Stdout sink writes JSONL:
   - Use `capsys`.
   - Write one summary record.
   - Assert stdout contains one JSON object line.

3. Checkpoint advances after successful sink write:
   - Scan one task-log event.
   - Write archive output.
   - Assert checkpoint stores that event timestamp.

4. Successful restart does not duplicate summaries:
   - Run once with checkpoint enabled.
   - Run again without new messages.
   - Assert no new task summary line is written.

5. Crash-window duplicate is safe:
   - Simulate archive line present but checkpoint absent.
   - Run again.
   - Assert duplicate summaries include the same stable `summary_id`.
   - Do not try to prevent all duplicates in this release.

6. Corrupt checkpoint fails clearly:
   - Write invalid JSON to checkpoint.
   - Run monitor.
   - Assert command-layer function returns exit code `1` or raises the typed
     internal error expected by the command wrapper.

Do not:

- Mock filesystem writes for normal sink tests.
- Use time-sensitive sleeps.
- Store raw broker messages in checkpoint fixtures.

### Task 4: Implement Sinks And Checkpoint Store

Owner: implementing engineer.

Files to touch:

- `weft/commands/lifecycle_monitor.py`
- `weft/_constants.py` if small constants reduce repeated literals

Implementation:

- Define small dataclasses:
  - `LifecycleMonitorConfig`
  - `LifecycleMonitorCheckpoint`
  - `LifecycleMonitorResult`
  - `TaskLifecycleSummary`
- Define sink protocol or simple classes:
  - `StdoutArchiveSink`
  - `DiskJsonlArchiveSink`
- Use append mode for disk JSONL.
- Ensure parent directories are created for disk sink and checkpoint.
- Flush disk sink before checkpoint write.
- Write checkpoint atomically enough for local files:
  - write temp file next to target
  - replace target with `Path.replace()`
- Keep backend-neutrality: checkpoint/archive are project files under the
  Weft context, not SimpleBroker tables.

Run:

```bash
./.venv/bin/python -m pytest tests/commands/test_lifecycle_monitor.py -q
```

### Task 5: Red Tests For Summary Classification

Owner: implementing engineer.

Files to touch:

- `tests/commands/test_lifecycle_monitor.py`

Test cases with real broker queues:

1. Terminal log success:
   - Write `work_started` and `work_completed` log entries for one TID.
   - Run monitor once.
   - Assert one `task_summary` with `classification="terminal_log"`,
     `status="completed"`, and `failure_owner is None`.

2. Wrapper lost:
   - Write a non-terminal task-log event for a TID.
   - Write a manager terminal envelope to `T{tid}.ctrl_out`.
   - Run monitor once.
   - Assert summary classification is `wrapper_lost` and
     `failure_owner="weft_lifecycle"`.
   - Assert `ctrl_out` still contains the envelope.

3. Result without terminal:
   - Write a non-terminal task-log event for a one-shot task.
   - Write one final outbox result.
   - Run monitor once.
   - Assert summary classification is `result_without_terminal` and
     `failure_owner="weft_lifecycle"`.
   - Assert outbox still contains the result.

4. Task failure without lifecycle anomaly:
   - Write terminal `work_failed` log event with a task error.
   - Run monitor once.
   - Assert summary classification is `domain_failure` or
     `terminal_log` with `failure_owner="task_or_runner"`, depending on the
     final schema decision.
   - Assert `cleanup_candidate` is `false`.

5. Active task:
   - Write `work_started` only.
   - Run monitor once.
   - Assert either no task summary is emitted, or an explicit non-terminal
     summary has `cleanup_candidate=false`.
   - Pick one behavior and document it. Prefer no per-task summary for active
     tasks plus aggregate run counts.

Do not:

- Duplicate the classification logic from `task_evidence.py`.
- Consume outbox or `ctrl_out`.
- Treat persistent, interactive, streaming, partial, or ambiguous outbox
  traffic as terminal.

### Task 6: Implement Summary Builder

Owner: implementing engineer.

Files to touch:

- `weft/commands/lifecycle_monitor.py`
- `weft/commands/task_evidence.py` only if a small reusable helper is missing

Implementation:

- Scan `WEFT_GLOBAL_LOG_QUEUE` using `iter_queue_json_entries()` and
  `since_timestamp`.
- Do not use `peek_many(limit=...)` for the global task log. Ops already has
  hundreds of thousands of messages.
- Reduce events by TID in memory:
  - latest task-log event payload
  - latest task-log timestamp
  - latest TaskSpec payload
  - started/completed timestamps if visible
- Keep memory bounded to one reduced record per seen TID, not one raw record
  per task-log message.
- For each eligible TID, call shared evidence logic:
  - `task_evidence.known_tid_evidence()` if it fits
  - or a narrower shared helper from `task_evidence.py`
- Build one compact summary per terminal/anomalous task.
- Include stable `summary_id`.
- Include `reconciliation` metadata where available.
- Include aggregate counts in `monitor_run_completed`:
  - events scanned
  - tids seen
  - summaries emitted
  - classification counts
  - checkpoint timestamp
- Set `cleanup_candidate=false` for every task summary in Release 4.

Classification rule:

- If evidence classification is `wrapper_lost`,
  `result_without_terminal`, `runtime_conflict`, `stale_created`,
  `orphaned_reserved`, or `orphaned_inbox`, use that classification and
  `failure_owner="weft_lifecycle"`.
- If terminal task-log proof exists and status is `completed`, use
  `classification="terminal_log"` and `failure_owner=null`.
- If terminal task-log proof exists and status is not `completed`, use
  `classification="domain_failure"` for archive grouping, but set
  `failure_owner="task_or_runner"` unless evidence proves a Weft lifecycle
  anomaly.
- Otherwise use `unknown` only when there is enough evidence to justify an
  operator-visible warning.

Do not:

- Make checkpoint position depend on whether a summary was emitted. It depends
  on the highest task-log timestamp successfully processed and archived.
- Add a status cache.
- Add an archive reader.

### Task 7: Red Tests For CLI Wiring

Owner: implementing engineer.

Files to touch:

- `tests/cli/test_cli_system.py`

Test cases:

- `weft system lifecycle-monitor --once --sink stdout --no-checkpoint`
  exits `0` and emits parseable archive JSONL records on stdout.
- `weft system lifecycle-monitor --once --sink stdout --json` is rejected
  because stdout cannot carry both archive JSONL and a final command summary.
- `weft system lifecycle-monitor --once --sink disk --archive-dir PATH --checkpoint PATH --json`
  exits `0`, writes archive/checkpoint files, and emits the final command
  summary JSON on stdout.
- `weft system lifecycle-monitor --sink disk --archive-dir PATH --checkpoint PATH`
  writes archive and checkpoint files.
- Invalid `--sink` is rejected by Typer or command validation.
- Cleanup flags do not exist.

Use existing CLI test helpers. Do not shell out manually if the repo already
has a Typer runner helper.

### Task 8: Implement CLI Command

Owner: implementing engineer.

Files to touch:

- `weft/cli/app.py`
- `weft/commands/lifecycle_monitor.py`
- possibly `weft/commands/__init__.py`

Implementation:

- Add `@system_app.command("lifecycle-monitor")`.
- Keep Typer wrapper thin:
  - parse options
  - call command-layer function
  - echo payload
  - exit with returned code
- Do not add `weft run --monitor`.
- Do not require a running manager.
- Ensure command works without any active manager because it is reading broker
  evidence, not dispatching work.

### Task 9: Docs And Spec Updates

Owner: implementing engineer.

Files to touch:

- `docs/specifications/01-Core_Components.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/specifications/00-Quick_Reference.md` if appropriate
- `docs/plans/README.md`

Required updates after code is green:

- Add the concrete lifecycle monitor task/command to the relevant current
  specs.
- State explicitly that checkpoints and archive files are operational outputs,
  not lifecycle truth.
- State explicitly that Release 4 monitor mode is non-destructive.
- Add this plan backlink in touched specs.
- Do not document future reaper behavior as current behavior.

### Task 10: Full Verification

Owner: implementing engineer.

Run focused gates:

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/tasks/test_lifecycle_monitor.py -q
./.venv/bin/python -m pytest tests/commands/test_lifecycle_monitor.py -q
./.venv/bin/python -m pytest tests/cli/test_cli_system.py -q
./.venv/bin/python -m pytest tests/commands/test_task_evidence.py tests/commands/test_status.py -q
./.venv/bin/python -m pytest tests/tasks/test_task_observer_behavior.py -q
```

Run quality gates:

```bash
./.venv/bin/python -m mypy weft extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft tests
```

If any gate is skipped, document why and do not claim release completion until
it runs somewhere.

## 12. Ops Validation

After release and deploy, validate on ops in dry-run mode.

1. Source environment:

   ```bash
   ssh ops
   cd ~/governance
   . .env
   ```

   If ops uses `.envrc`, source that instead. The point is that Weft must have
   broker credentials before running.

2. Capture pre-run broker status:

   ```bash
   /opt/venv/bin/weft status --json > /tmp/weft-status-before-monitor.json
   ```

3. Run stdout dry-run with a small limit:

   ```bash
   /opt/venv/bin/weft system lifecycle-monitor --once --sink stdout --no-checkpoint --limit 100
   ```

4. Confirm:

   - command exits `0`
   - output is parseable JSONL
   - summaries distinguish Weft lifecycle anomalies from task-or-runner
     terminal failures
   - no queues were consumed

5. Run disk archive mode:

   ```bash
   /opt/venv/bin/weft system lifecycle-monitor --once --sink disk --limit 1000
   ```

6. Confirm:

   - archive JSONL exists under `.weft/archive/tasks/`
   - checkpoint exists under `.weft/state/lifecycle-monitor/`
   - rerunning the same command does not duplicate summaries in normal
     checkpointed operation

7. Capture post-run broker status:

   ```bash
   /opt/venv/bin/weft status --json > /tmp/weft-status-after-monitor.json
   ```

8. Confirm:

   - broker message counts did not drop because Release 4 is non-destructive
   - active manager remains visible
   - `weft status --json` remains coherent
   - historical `wrapper_lost` and `result_without_terminal` rows remain
     visible or archived, not silently hidden

Ops failure signals:

- monitor consumes or deletes messages
- status output changes because of monitor checkpoint/archive state
- archive output labels real task failures as safe cleanup
- checkpoint corruption causes silent skip
- command requires an active manager

## 13. Test Design Rules

Good tests:

- Use real broker queues through `broker_env`, `build_context()`, or existing
  harness helpers.
- Write representative task-log, `ctrl_out`, and outbox messages.
- Run the real monitor command-layer function.
- Assert queue contents remain present after monitor scans.
- Assert JSONL records parse and carry stable required fields.
- Assert checkpoint behavior across two runs.

Acceptable mocks:

- `capsys` for stdout.
- `monkeypatch` for deterministic timestamps or run IDs, if the record schema
  would otherwise be hard to assert.
- CLI runner fixtures already used in the repo.

Bad tests:

- Mocking `task_evidence` and then claiming classification is tested.
- Mocking Queue for normal scans.
- Sleeping to wait for synchronous scan behavior.
- Asserting private method calls instead of archive records and queue state.
- Testing deletion behavior. There is no deletion behavior in this release.
- Creating a broad fake lifecycle database.

## 14. Invariants

Must preserve:

- `weft.log.tasks` remains lifecycle truth.
- Public status does not depend on monitor output.
- Monitor reads are peek/generator reads only.
- No broker messages are deleted, moved, reserved, or acknowledged by monitor
  scanning.
- Checkpoints are operational cursors only.
- Archive records are append-only.
- Archive records are versioned.
- Archive writes happen before checkpoint advancement.
- `cleanup_candidate` is always `false` in Release 4.
- Manager remains dispatcher/supervisor, not monitor/reaper/archive writer.
- Core modules do not import command modules.
- Evidence classification is reused, not forked.

## 15. Independent Review Checklist

Ask an independent reviewer to check:

- The monitor is non-destructive.
- No manager-autostart service slipped in.
- No `weft run --monitor` behavior slipped in.
- Status/result/task commands do not read monitor checkpoints or archives.
- The global task-log scan uses generator/high-water semantics.
- Archive records are compact and versioned.
- Checkpoints are written only after sink success.
- Corrupt checkpoints fail clearly.
- Tests use real queues for normal behavior.
- Domain/task failures are not mislabeled as safe cleanup.
- Spec updates describe only current behavior.

## 16. Fresh-Eyes Review Of This Plan

Review pass 1 found a scope trap: making the monitor a manager-autostart
internal service would require endpoint ownership, new internal runtime
constants, spawn-envelope changes, and service lifecycle tests. That is a
different release. The corrected plan uses a foreground system command and a
task-shaped peek loop first.

Review pass 2 found a layer-boundary trap: a core monitor task cannot import
`weft.commands.task_evidence`. The corrected plan keeps classification in the
command layer and keeps the core task wrapper generic.

Review pass 3 found a retention trap: calling archive output "cleanup
candidates" would invite deletion behavior too early. The corrected plan sets
`cleanup_candidate=false` for every Release 4 summary.

Review pass 4 found an audit trap: making broker queues the archive would
contradict the agreed retention philosophy. The corrected plan writes JSONL to
stdout or disk and treats broker queues as coordination/recovery surfaces.

Review pass 5 found a testing trap: a monitor can appear correct while
accidentally consuming messages. The corrected plan requires real queue tests
that assert messages remain after scanning.

Review pass 6 found a classification trap: not every `work_failed` event is
definitively a domain failure. The corrected plan uses conservative
`failure_owner` values and avoids marking task failures as cleanup-safe.

The plan still matches the discussed architecture. It builds the reporting and
archive substrate first, does not overload the Manager, and leaves destructive
cleanup for later phases.

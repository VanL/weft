# Phase 7 Task Monitor Supervision And Cleanup Plan

Status: draft
Source specs: see Source Documents below
Superseded by: none

## 1. Goal

Phase 7 turns the existing foreground `weft system task-monitor` and
`weft system prune` capabilities into a manager-supervised `TaskMonitor`
service that keeps Weft runtime queues reflective of in-flight work. The work
ships as three independent releases:

1. manager-supervised `TaskMonitor` plumbing, config, heartbeat wakeups, and a
   report-only processor call;
2. shared lifecycle consolidation and deterministic cleanup-candidate
   classification;
3. exact-message cleanup processors that can delete safe stale queue state
   automatically, with optional operational JSONL output.

The monitor is not lifecycle truth, an audit store, or a second manager. It is
an internal task that observes queue truth, reports Weft-owned anomalies, and
applies bounded cleanup policy after the relevant evidence model is proven.

## 2. Source Documents

Source specs:

- [`docs/specifications/00-Overview_and_Architecture.md`](../specifications/00-Overview_and_Architecture.md)
  "Executive Summary", "System Architecture", "Observability and Security"
- [`docs/specifications/00-Quick_Reference.md`](../specifications/00-Quick_Reference.md)
  "Queue Names", "Operational Files", "Environment Variables"
- [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md)
  [CC-2.1], [CC-2.2], [CC-2.3], [CC-2.4], [CC-2.4.1], [CC-2.5],
  [CC-3.4]
- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md)
  [MA-0], [MA-1], [MA-1.4], [MA-1.7], [MA-3], [MA-4]
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-2], [MF-3], [MF-3.1], "3.2 Heartbeat Service Flow", [MF-5],
  [MF-6], "Cleanup Boundary", "Queue Management Patterns"
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [STATE.1], [STATE.2], [STATE.6], [QUEUE.4], [QUEUE.6], [OBS.1],
  [OBS.2], [OBS.3], [OBS.6], [OBS.11], [OBS.13], [OBS.14],
  [OBS.16], [OBS.17], [MANAGER.1], [MANAGER.3], [MANAGER.5],
  [MANAGER.8], [CTX.3], [CTX.4]
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
  [CLI-6]

Related plans:

- [`2026-05-07-lifecycle-monitor-archive-sink-plan.md`](./2026-05-07-lifecycle-monitor-archive-sink-plan.md)
  shipped the non-destructive foreground task monitor under the old lifecycle
  name. It has since been renamed to `task-monitor`.
- [`2026-05-07-task-monitor-ping-logs-breaking-cleanup-plan.md`](./2026-05-07-task-monitor-ping-logs-breaking-cleanup-plan.md)
  completed the task-monitor naming cleanup and moved operational output under
  `.weft/logs/`.
- [`2026-05-07-runtime-state-pruning-plan.md`](./2026-05-07-runtime-state-pruning-plan.md)
  shipped explicit foreground pruning for runtime-only `weft.state.*` queues.
- [`2026-05-07-task-local-reaper-retention-policy-plan.md`](./2026-05-07-task-local-reaper-retention-policy-plan.md)
  shipped explicit foreground retention pruning for task-local and task-log
  queues.
- [`2026-05-07-result-evidence-and-superseded-manager-reconciliation-plan.md`](./2026-05-07-result-evidence-and-superseded-manager-reconciliation-plan.md)
  defines result-evidence priority and protects claimed outbox residue as a
  recovery condition.
- [`2026-04-17-heartbeat-service-plan.md`](./2026-04-17-heartbeat-service-plan.md)
  shipped the internal heartbeat service that this phase should reuse for
  periodic monitor wakeups.

Guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/README.md`](../agent-context/README.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/runtime-and-context-patterns.md`](../agent-context/runbooks/runtime-and-context-patterns.md)
- [`docs/agent-context/runbooks/testing-patterns.md`](../agent-context/runbooks/testing-patterns.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)

Spec deltas required before implementation is done:

- Update [CC-2.3] so `TaskMonitorTask` is no longer described only as a
  foreground system-command primitive after part 1 lands.
- Update [MA-1] and [MA-3] to say the canonical manager supervises one
  internal `TaskMonitor` child per context when enabled, restarts it with
  bounded backoff, and never performs monitor scans in the manager process.
- Update [MF-5], "Cleanup Boundary", and "Queue Management Patterns" to
  describe background task-monitor cleanup after part 3 lands. Until then,
  keep the current "foreground only" text accurate.
- Update [MF-3.1] / heartbeat flow if the monitor claims an internal endpoint
  or registers heartbeat wakeups.
- Update [OBS.13] to keep task-monitor output operational only, even when the
  monitor runs continuously.
- Update [OBS.17] or nearby cleanup invariants to distinguish explicit
  foreground pruning from manager-supervised monitor processors.
- Update [CLI-6] only if the foreground CLI contract changes. It should not
  change in part 1.
- Add backlinks from touched spec sections to this plan.

## 3. Current Context

Current shipped behavior:

- `weft system task-monitor` is a foreground command. It scans
  `weft.log.tasks` without consuming broker messages, emits operational JSONL
  records to stdout or disk, and writes an operational checkpoint.
- `weft system prune` is a foreground operator command. It can dry-run or apply
  exact-message pruning for runtime-state, task-local, and task-log retention
  families.
- The manager does not currently start or supervise a background monitor.
- Specs currently say there is no separate queue-lifecycle service. That is
  true today and must remain true until phase 7 implementation changes it.
- Existing task-monitor logs, prune reports, archives, and checkpoints are
  operational output. Status/result reconstruction does not depend on them.

The ops problem this phase addresses:

- Queue volume can grow far beyond in-flight work. Recent ops examples had
  hundreds of thousands of `weft.log.tasks`, `weft.state.tid_mappings`, and
  task-local messages, making `weft status --json` and `weft task list --json`
  too slow for normal operation.
- Stale runtime rows and task-local residue can keep public read models noisy
  even after status coherence fixes are in place.
- Some failures are real domain failures, such as Wazuh idempotency conflicts
  or malformed LLM JSON. The monitor must report these without turning them
  into Weft cleanup bugs.

Important distinction:

- Weft queues are durable runtime messages, not audit records. Long-term audit
  evidence belongs outside Weft queues.
- This does not mean every row is safe to delete automatically. Claimed outbox
  residue, active inbox/reserved work, malformed rows, and ambiguous task
  surfaces remain recovery-sensitive unless an explicit force path says
  otherwise.

## 4. Architecture Decision

Use a manager-supervised internal `TaskMonitorTask`, not manager-side scanning.

The manager owns only supervision:

- ensure one monitor child exists when the canonical manager is dispatch owner;
- restart it with bounded backoff if it exits unexpectedly;
- stop it during manager drain like other children;
- expose enough child metadata for operator inspection.

The monitor owns observation and processor calls:

- wake on heartbeat messages and run one bounded cycle;
- read queue evidence using generator/high-water semantics;
- build a deterministic list of candidate TIDs or exact cleanup candidates;
- call one configured processor function with those candidates;
- publish monitor health through normal task control `PING` / `STATUS`;
- update checkpoints only after the processor reports success.

The processor owns side effects:

- part 1 processor is report-only and must not delete anything;
- part 2 processors still do not delete, but receive richer candidate
  classifications;
- part 3 processors may delete exact message IDs for safe classes only.

Why not make the processor another task in this phase:

- A processor task would add a second queue lifecycle, result wait, retry, and
  recovery contract before the simple function contract is proven.
- The current need is one bounded callback with explicit input/output. A
  `module:function` processor is enough.
- If later deployments need fan-out processors, add that as a separate plan
  after this function contract is stable.

Layering rule:

- `weft/core/*` must not import `weft.commands.*`.
- If the supervised monitor needs logic that currently lives under
  `weft/commands/task_evidence.py`, `weft/commands/runtime_prune.py`, or
  `weft/commands/retention_prune.py`, extract the reusable non-rendering engine
  into `weft/core/` and make command modules thin wrappers over it.
- Do not make `Manager` call command-layer functions.

Release sequencing:

- Part 1 is non-destructive. It proves manager supervision, config plumbing,
  heartbeat wakeups, and processor invocation.
- Part 2 is also non-destructive. It proves the shared lifecycle consolidation
  and candidate model under real broker queues.
- Part 3 is the first autonomous destructive release. It must be deployable in
  `report_only` mode first, then flipped to `delete` or `jsonl_then_delete`
  by config after ops validation.

## 5. Files And Ownership

Files likely touched in part 1:

- `weft/_constants.py`
  - Add task-monitor env/config constants.
  - Add `INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR`.
- `weft/context.py`
  - Only touch if config normalization needs context-derived defaults.
- `weft/core/tasks/task_monitor.py`
  - Extend the existing peek scanner into a persistent internal task that can
    process wake messages, expose monitor status, and call a processor.
- `weft/core/tasks/__init__.py`
  - Ensure `TaskMonitorTask` remains exported.
- `weft/core/manager.py`
  - Resolve the new internal runtime class.
  - Add monitor supervision with backoff.
  - Track monitor child identity without overloading autostart state.
- `weft/core/spawn_requests.py`
  - Touch only if the internal runtime envelope needs a helper for
    task-monitor submission.
- `weft/core/heartbeat.py`
  - Prefer existing `upsert_heartbeat()` / `cancel_heartbeat()` helpers.
  - Do not duplicate heartbeat service startup logic.
- `weft/core/task_monitoring.py` or similar new core module
  - Recommended home for task-monitor runtime config, candidate dataclasses,
    processor protocol, processor resolution, and report-only processor.
- `tests/tasks/test_task_monitor.py`
- `tests/core/test_manager.py`
- `tests/core/test_task_monitoring.py`
- `tests/helpers/weft_harness.py`
  - Consider setting `WEFT_TASK_MONITOR_ENABLED=0` in the harness default only
    if broad test suites become noisy from extra internal children. Add
    targeted tests with the monitor enabled.

Files likely touched in part 2:

- `weft/commands/task_evidence.py`
  - Convert to a wrapper or re-export if shared evidence moves to core.
- `weft/core/task_evidence.py` or `weft/core/task_lifecycle_evidence.py`
  - Recommended home for shared status/result/monitor evidence classification.
- `weft/commands/status.py`
- `weft/commands/tasks.py`
- `weft/commands/result.py`
- `weft/commands/task_monitor.py`
  - Update command-layer callers to use the shared core evidence path.
- `weft/core/task_monitoring.py`
  - Build deterministic candidate classes for the monitor.
- `tests/commands/test_task_evidence.py`
- `tests/commands/test_status.py`
- `tests/commands/test_result.py`
- `tests/commands/test_task_monitor.py`
- `tests/core/test_task_evidence.py` or `tests/core/test_task_lifecycle_evidence.py`
- `tests/core/test_task_monitoring.py`

Files likely touched in part 3:

- `weft/commands/runtime_prune.py`
- `weft/commands/retention_prune.py`
  - Extract reusable candidate/apply engines only as needed. The CLI modules
    should keep rendering and option parsing.
- `weft/core/runtime_pruning.py` and/or `weft/core/retention_pruning.py`
  - Recommended homes for shared, command-neutral candidate builders and
    exact-message apply logic.
- `weft/core/task_monitor_processors.py` or the existing
  `weft/core/task_monitoring.py`
  - Built-in `report_only`, `delete`, and `jsonl_then_delete` processors.
- `weft/_constants.py`
  - Add any part-3-only defaults such as safe cleanup mode or min-age if not
    already present.
- `tests/commands/test_runtime_prune.py`
- `tests/commands/test_retention_prune.py`
- `tests/core/test_task_monitor_processors.py`
- `tests/tasks/test_task_monitor.py`
- `tests/cli/test_cli_system.py`

Files to avoid unless a failing test proves they must change:

- `weft/core/tasks/consumer.py`
- `weft/core/tasks/base.py`
- `weft/core/tasks/runner.py`
- `weft/core/tasks/sessions.py`
- `weft/cli/run.py`
- `weft/commands/run.py`
- `weft/manager_process.py`

## 6. Required Reading Before Editing

Read in this order:

1. `AGENTS.md`
2. `docs/agent-context/README.md`
3. `docs/agent-context/decision-hierarchy.md`
4. `docs/agent-context/principles.md`
5. `docs/agent-context/engineering-principles.md`
6. `docs/agent-context/runbooks/runtime-and-context-patterns.md`
7. `docs/agent-context/runbooks/testing-patterns.md`
8. `docs/agent-context/runbooks/hardening-plans.md`
9. `docs/specifications/00-Overview_and_Architecture.md`
10. `docs/specifications/01-Core_Components.md` [CC-2.1], [CC-2.2],
    [CC-2.3], [CC-2.4.1]
11. `docs/specifications/03-Manager_Architecture.md` [MA-1], [MA-3]
12. `docs/specifications/05-Message_Flow_and_State.md` [MF-3.1],
    heartbeat flow, [MF-5], cleanup boundary, queue management patterns
13. `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.14],
    [OBS.16], [OBS.17], [MANAGER.8]
14. `weft/core/manager.py`
15. `weft/core/tasks/task_monitor.py`
16. `weft/commands/task_monitor.py`
17. `weft/commands/runtime_prune.py`
18. `weft/commands/retention_prune.py`
19. `weft/core/heartbeat.py`
20. `weft/core/tasks/heartbeat.py`

Comprehension checks before coding:

- Which process is allowed to scan `weft.log.tasks` in phase 7? Answer: the
  `TaskMonitorTask` child, not `Manager`.
- Does a monitor checkpoint become lifecycle truth? Answer: no.
- Can the monitor delete from `weft.spawn.requests` or manager control queues?
  Answer: no.
- Can a claimed outbox row be decoded as a normal result? Answer: no.
- What proves manager dispatch ownership? Answer: selected canonical manager
  registry evidence, optionally rescued by matched PONG liveness, reduced by
  lowest live TID.
- What queue read pattern is required for `weft.log.tasks` and
  `weft.state.*` histories? Answer: generator/high-water reads, not fixed
  `peek_many(limit=N)`.
- What should happen if a monitor processor fails? Answer: report the monitor
  failure, do not advance cleanup checkpoints for that cycle, and do not block
  manager dispatch.

## 7. Invariants And Constraints

- Queues remain the source of truth for lifecycle and runtime state.
- Task-monitor logs, checkpoints, processor summaries, and JSONL output are
  operational artifacts only.
- The manager does not perform lifecycle scans, result classification, or
  pruning.
- Monitor failure must not stop manager dispatch.
- Manager supervision must start at most one monitor per canonical manager
  context.
- Non-leader managers must not start their own monitor once another canonical
  dispatch owner is positively selected.
- The monitor must use ordinary task-local queues and control semantics.
- The monitor must respond to `PING` and `STATUS` through `ctrl_out` with
  config and health fields.
- No public CLI output shape changes in part 1 unless a test proves the
  current shape is impossible to preserve.
- No new public task state.
- No new dependency.
- No backend-specific SQL shortcuts.
- No bulk queue delete by queue name.
- No deletion before part 3.
- Part 3 deletion must use exact message IDs only.
- Part 3 ordinary automatic deletion must protect active tasks, ambiguous
  tasks, claimed outbox residue, malformed rows, unknown-shape rows, and
  inbox/reserved work unless a later force-specific plan says otherwise.
- Optional JSONL output is not an audit guarantee and must not be required for
  correctness. If `jsonl_then_delete` is selected, failure to write JSONL must
  fail closed before deletion.
- Do not try to make cross-queue or disk-plus-queue writes atomic. Instead,
  make processors idempotent, delete exact rows, record per-candidate results,
  and advance checkpoints only after the selected processor returns success.

## 8. Configuration Contract

Part 1 should introduce the public config names, even if some are only used in
report-only mode at first:

- `WEFT_TASK_MONITOR_ENABLED`
  - Boolean.
  - Target steady-state default: `true`.
  - Test harnesses may explicitly set `0` for unrelated tests, but production
    code should not have hidden test-mode behavior.
- `WEFT_TASK_MONITOR_INTERVAL_SECONDS`
  - Integer seconds.
  - Must be `>= HEARTBEAT_MIN_INTERVAL_SECONDS`.
  - Recommended default: `300`.
- `WEFT_TASK_MONITOR_BATCH_SIZE`
  - Positive integer.
  - Maximum task-log events or candidates handled in one cycle.
  - Recommended default: `1000`.
- `WEFT_TASK_MONITOR_PROCESSOR`
  - String.
  - Allowed built-ins: `report_only`, `delete`, `jsonl_then_delete`.
  - Also allow `module:function` for custom processors.
  - Part 1 default: `report_only`.
  - Part 3 may intentionally change the production default to `delete` for
    safe classes only after report-only validation. That default change must
    be the only behavioral change in its release.
- `WEFT_TASK_MONITOR_LOG_SINK`
  - String: `stdout`, `disk`, or `none`.
  - Recommended part 1 default: `stdout` for supervised containers.
  - `disk` writes under `ctx.logs_dir / "task-monitor"` unless an explicit
    log dir override is added.
- `WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS`
  - Positive float or integer seconds.
  - Recommended default: `60`.

Do not add a full plugin framework. A dotted callable resolver plus explicit
built-in names is enough.

## 9. Processor Contract

Define a small, typed processor contract in core. Suggested shape:

```python
@dataclass(frozen=True, slots=True)
class TaskMonitorCandidate:
    candidate_id: str
    tid: str | None
    queue: str | None
    message_id: int | None
    candidate_class: str
    reason: str
    safe_to_delete: bool
    payload_sha256: str | None = None
    payload_size_bytes: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TaskMonitorProcessorRequest:
    context: WeftContext
    config: TaskMonitorRuntimeConfig
    cycle_id: str
    monitor_tid: str
    candidates: tuple[TaskMonitorCandidate, ...]
    now_ns: int


@dataclass(frozen=True, slots=True)
class TaskMonitorProcessorResult:
    success: bool
    processed: int = 0
    deleted: int = 0
    reported: int = 0
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
```

Required rules:

- The processor receives candidates, not a `Manager` or `TaskMonitorTask`
  instance.
- The processor may use `request.context.queue()` for exact queue operations.
- The processor must return a result. It should not call `sys.exit()`.
- The processor must not mutate candidate objects.
- `candidate_id` must be stable across restart for the same queue row. Use
  queue name, message ID, candidate class, and payload hash where possible.
- Built-in processors must be idempotent. A missing exact row during deletion
  is a warning or skipped result, not a broad failure.

## 10. Part 1 Tasks: Supervised Monitor Skeleton

Part 1 outcome: the canonical manager starts and supervises one internal
`TaskMonitorTask`; the monitor wakes, reads config, finds candidate TIDs from
real broker evidence, calls a report-only processor, and exposes health via
PING/STATUS. It deletes nothing.

### 1. Add task-monitor config constants and parsing

- Files to touch:
  - `weft/_constants.py`
  - `tests/system/test_constants.py` or a new focused config test
- Read first:
  - existing `WEFT_AUTOSTART_TASKS` and `WEFT_LOGS_DIR` parsing in
    `weft/_constants.py`
- Required action:
  - Add the config names from "Configuration Contract".
  - Add `INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR = "task_monitor"`.
  - Validate bools and numeric bounds at config-load time.
  - Reject invalid processor strings early only for built-in names and clearly
    malformed values. A dotted callable may fail later during processor
    resolution with a precise error.
- Tests:
  - Red test invalid interval below heartbeat minimum.
  - Red test invalid batch size `0`.
  - Red test bool normalization for `WEFT_TASK_MONITOR_ENABLED`.
- Stop if:
  - config parsing starts reading `.envrc` itself. Environment loading belongs
    outside Weft.
- Done when:
  - config values appear in `load_config()` output with normalized types.

### 2. Add the core processor protocol and report-only processor

- Files to touch:
  - new `weft/core/task_monitoring.py` or equivalent
  - `tests/core/test_task_monitoring.py`
- Read first:
  - `weft/core/tasks/task_monitor.py`
  - `weft/commands/task_monitor.py`
- Required action:
  - Define runtime config, candidate, processor request, processor result, and
    processor resolver.
  - Implement `report_only`.
  - Implement dotted callable lookup with `importlib`.
  - Enforce that a resolved processor is callable.
- Tests:
  - Processor resolver maps `report_only` to the built-in callable.
  - Processor resolver loads a test `module:function` without importing CLI.
  - Report-only returns success and reports candidate count.
- What not to mock:
  - Do not mock config parsing. Use real `load_config(overrides=...)` where
    practical.
- Stop if:
  - a registry of plugin classes starts appearing. Use one callable protocol.
- Done when:
  - a processor can be resolved and called without a manager or CLI object.

### 3. Extend `TaskMonitorTask` into a persistent wake-driven task

- Files to touch:
  - `weft/core/tasks/task_monitor.py`
  - `tests/tasks/test_task_monitor.py`
- Read first:
  - `weft/core/tasks/heartbeat.py`
  - `weft/core/tasks/base.py` `run_until_stopped()` and control handling
- Required action:
  - Keep `scan_once()` non-consuming and compatible with the foreground command.
  - Do not use `weft.log.tasks` as the persistent monitor's own inbox. The
    monitor's task-local inbox must stay `T{tid}.inbox` for heartbeat wake
    messages. Treat `weft.log.tasks` as an auxiliary history source read by
    generator during a cycle.
  - Configure the persistent monitor's queue loop around its own inbox and
    `ctrl_in`. Do not wake the monitor on every task-log write; heartbeat or
    bounded interval wakeups are the scheduler.
  - Add `process_once()` for persistent operation:
    - drain one control message;
    - drain one wake message from inbox, or run an immediate first cycle on
      startup;
    - scan at most `batch_size` task-log events after the checkpoint;
    - build simple part-1 candidates such as `task_log_tid_seen`;
    - call the configured processor;
    - advance the monitor checkpoint only if the processor result succeeds.
  - Register heartbeat wakeups to the monitor's own inbox with
    `upsert_heartbeat()`. On STOP, cancel the heartbeat best effort.
  - If heartbeat registration fails, record a monitor error and fall back to a
    bounded local interval wait. Do not crash-loop the manager for a transient
    heartbeat problem.
  - Include monitor health in `_control_snapshot_fields()`:
    `role="task_monitor"`, `processor`, `mode`, `interval_seconds`,
    `batch_size`, `last_cycle_at`, `last_checkpoint`, `last_candidates_seen`,
    `last_processor_success`, and `last_error`.
- Tests:
  - Real queue test: write two `weft.log.tasks` rows, run one monitor cycle,
    assert the processor saw both TIDs and the log queue still has both rows.
  - Real control test: send structured `PING`, assert matched `PONG` includes
    config fields and does not consume task-log rows.
  - Failure test: processor returns `success=False`; checkpoint does not
    advance.
  - Heartbeat test: use real heartbeat registration where practical; if the
    full heartbeat service would make the test slow, monkeypatch only
    `upsert_heartbeat()` and keep broker queue behavior real.
- Stop if:
  - `TaskMonitorTask` imports `weft.commands.*`.
  - the monitor deletes, moves, reserves, or claims task-log rows.
- Done when:
  - the task can run one cycle and call a processor without mutating broker
    messages.

### 4. Add manager supervision without manager-side scanning

- Files to touch:
  - `weft/core/manager.py`
  - `tests/core/test_manager.py`
- Read first:
  - `Manager._resolve_child_task_class()`
  - `Manager._cleanup_children()`
  - `Manager._tick_autostart()`
  - `Manager._evaluate_dispatch_ownership()`
- Required action:
  - Route `INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR` to `TaskMonitorTask`.
  - Add manager state for the supervised monitor TID and restart backoff.
  - Start the monitor by enqueuing an internal spawn request onto the manager's
    own inbox, following the existing autostart enqueue pattern. Do not call
    `TaskMonitorTask(...)` inline inside the manager.
  - Carry the internal runtime selector on the manager-owned spawn envelope
    with `INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY`, not by trusting stored
    TaskSpec metadata alone.
  - Only the canonical dispatch owner should start a monitor.
  - Do not start or restart the monitor while dispatch is suspended, while the
    manager is draining, or after leadership is yielded.
  - Track monitor child exit separately from autostart. Add an explicit
    `internal_role` field to `ManagedChild` if that is cleaner than a parallel
    set of TIDs.
  - Restart after backoff when the monitor exits unexpectedly.
  - Ensure a running monitor child does not by itself keep a finite-idle-timeout
    manager alive forever. Internal monitor supervision is not user work.
- Tests:
  - Manager with monitor enabled enqueues exactly one internal monitor spawn
    request.
  - Manager with monitor disabled enqueues none.
  - Non-leader or dispatch-suspended manager enqueues none.
  - Spawn payload carries the internal runtime class in the manager-owned
    envelope and does not rely on user-visible TaskSpec metadata.
  - Dead monitor child is restarted after backoff, not in a hot loop.
  - A manager whose only live child is the monitor can still reach idle
    shutdown when idle timeout is finite.
  - Existing autostart tests still pass; monitor state does not reuse
    autostart source bookkeeping.
- What not to mock:
  - Do not mock `Manager.process_once()` entirely. Use a real manager instance
    with real broker-backed queues and stub only the clock/backoff if needed.
- Stop if:
  - manager code starts reading `weft.log.tasks` for monitor work.
  - a second manager bootstrap path appears.
- Done when:
  - the manager's only monitor responsibility is child supervision.

### 5. Keep the foreground CLI working

- Files to touch:
  - `weft/commands/task_monitor.py`
  - `tests/commands/test_task_monitor.py`
  - `tests/cli/test_cli_system.py`
- Required action:
  - Preserve `weft system task-monitor` behavior unless the shared config
    extraction requires a small internal call change.
  - Do not make the foreground command depend on a running manager or on the
    supervised monitor.
  - Do not change stdout JSONL format in part 1.
- Tests:
  - Existing foreground task-monitor command tests remain green.
  - Add a regression that foreground `run_task_monitor()` can still run with
    `WEFT_TASK_MONITOR_ENABLED=0`.
- Stop if:
  - foreground task-monitor starts talking to the supervised monitor.
- Done when:
  - foreground and supervised monitor paths share safe internals without
    becoming dependent on each other.

### 6. Part 1 docs and rollout

- Files to touch:
  - `docs/specifications/01-Core_Components.md`
  - `docs/specifications/03-Manager_Architecture.md`
  - `docs/specifications/05-Message_Flow_and_State.md`
  - `docs/specifications/07-System_Invariants.md`
  - `docs/specifications/00-Quick_Reference.md`
  - `docs/plans/README.md`
- Required action:
  - Update specs to say manager-supervised monitor exists but is
    non-destructive.
  - Keep cleanup boundary text explicit: part 1 deletes nothing.
  - Add this plan as a related plan.
- Ops gate after deploy:
  - `weft --version` shows the new release.
  - `weft manager list --json` shows one active manager.
  - The manager has a `task-monitor` child process or task-log spawn event.
  - `weft task status <monitor_tid> --ping --json` returns monitor config and
    last-cycle fields.
  - Queue counts do not decrease because part 1 is non-destructive.

## 11. Part 2 Tasks: Consolidation And Candidate Model

Part 2 outcome: the monitor and foreground read models use one shared evidence
model to classify terminal state, Weft lifecycle anomalies, domain failures,
runtime-state candidates, and task-local cleanup candidates. It still deletes
nothing.

### 1. Extract shared lifecycle evidence out of command-only modules

- Files to touch:
  - `weft/commands/task_evidence.py`
  - new `weft/core/task_evidence.py` or `weft/core/task_lifecycle_evidence.py`
  - `tests/commands/test_task_evidence.py`
  - new `tests/core/test_task_evidence.py`
- Read first:
  - `weft/commands/task_evidence.py`
  - `docs/specifications/05-Message_Flow_and_State.md` [MF-5]
- Required action:
  - Move pure evidence dataclasses and parsing/classification helpers into
    core.
  - Keep CLI/command rendering and command-only glue in `weft/commands`.
  - Preserve existing import behavior where practical by re-exporting from
    `weft/commands/task_evidence.py`.
  - Keep status/result priority unchanged:
    terminal task-log, typed terminal `ctrl_out`, readable final one-shot
    outbox, live runtime evidence, stale fallback.
- Tests:
  - Move or duplicate core-level tests for terminal log, terminal ctrl-out,
    readable outbox, claimed outbox diagnostic, stale manager reconciliation,
    and domain failure classification.
  - Existing command tests remain green.
- Stop if:
  - core evidence starts importing command or CLI modules.
  - the extracted model changes public status/result output in this task.
- Done when:
  - status, result, foreground monitor, and supervised monitor can all use the
    same classification engine.

### 2. Build deterministic monitor cycle snapshots

- Files to touch:
  - `weft/core/task_monitoring.py`
  - `weft/core/tasks/task_monitor.py`
  - `tests/core/test_task_monitoring.py`
- Required action:
  - Reduce `weft.log.tasks` by TID using generator/high-water reads.
  - For each seen TID, collect enough task-local evidence to classify:
    terminal, active, stale-created, result-without-terminal,
    claimed-result-without-terminal, wrapper-lost, runtime-conflict,
    domain-failure.
  - Keep domain failures as task/runner failures. Do not turn Wazuh data
    invariants or malformed LLM JSON into Weft cleanup errors.
  - Emit anomaly records for Weft-owned lifecycle problems.
  - Do not scan every task-local queue in the broker blindly. Start from TIDs
    discovered in task-log, TID mappings, and configured candidate filters.
- Tests:
  - Large task-log fixture with more rows than a small fixed limit; prove the
    generator path finds the relevant latest row.
  - Wazuh-like domain failure fixture; monitor reports `failure_owner` as
    `task_or_runner`, not `weft_lifecycle`.
  - Claimed outbox fixture; monitor reports recovery diagnostic and
    `safe_to_delete=False`.
  - Result-without-terminal fixture; monitor reports Weft anomaly but does not
    mark it safe to delete in part 2.
- Stop if:
  - candidate construction depends on monitor JSONL logs or checkpoints as
    lifecycle truth.
- Done when:
  - one monitor cycle can produce stable candidate records from real queues
    without deletion.

### 3. Share runtime-state candidate construction

- Files to touch:
  - `weft/commands/runtime_prune.py`
  - new `weft/core/runtime_pruning.py` if extraction is needed
  - `weft/core/task_monitoring.py`
  - `tests/commands/test_runtime_prune.py`
  - `tests/core/test_task_monitoring.py`
- Required action:
  - Extract non-rendering runtime-state candidate construction so both
    `weft system prune --family runtime-state` and the supervised monitor can
    reuse it.
  - Preserve CLI output and command defaults.
  - Keep runtime-state pruning separate from task-local retention pruning.
  - Preserve current protections for live/recent/unknown runtime rows.
- Tests:
  - Runtime prune command tests still pass.
  - Monitor candidate test sees stale TID mapping and stale manager rows as
    candidates but does not delete them in part 2.
- Stop if:
  - a "generic prune candidate" abstraction erases differences between
    runtime-state and task-local retention safety rules.
- Done when:
  - runtime-state candidates can be reported by monitor and foreground prune
    from one shared core path.

### 4. Share task-local and task-log retention candidates

- Files to touch:
  - `weft/commands/retention_prune.py`
  - new `weft/core/retention_pruning.py` if extraction is needed
  - `weft/core/task_monitoring.py`
  - `tests/commands/test_retention_prune.py`
  - `tests/core/test_task_monitoring.py`
- Required action:
  - Extract non-rendering retention candidate construction and exact apply
    helpers only as far as part 2 needs.
  - In part 2, the monitor should receive candidates and report
    `safe_to_delete`, but it must not apply them.
  - Preserve explicit foreground prune semantics and CLI defaults.
- Tests:
  - Retention prune command tests still pass.
  - Monitor sees safe terminal ctrl-out/outbox/log candidates for old terminal
    tasks.
  - Monitor protects active, ambiguous, claimed, malformed, inbox, and reserved
    rows under ordinary policy.
- Stop if:
  - the monitor starts treating age alone as cleanup proof.
- Done when:
  - candidate classes match foreground prune for the same queue fixture.

### 5. Part 2 docs and rollout

- Files to touch:
  - touched specs near [MF-5], [OBS.13], [OBS.17], and implementation mappings
  - `docs/plans/README.md`
- Required action:
  - Document the shared evidence and candidate ownership.
  - Keep cleanup text non-destructive until part 3.
- Ops gate after deploy:
  - Supervised monitor PING shows recent cycles and non-zero candidates on a
    queue-heavy ops context.
  - `WEFT_TASK_MONITOR_PROCESSOR=report_only` remains in effect.
  - Queue counts should remain stable except for unrelated foreground operator
    actions.
  - Compare a foreground `weft system prune --dry-run --json` sample with
    monitor candidate class counts. They should agree for the same family and
    filters.

## 12. Part 3 Tasks: Safe Cleanup Processors

Part 3 outcome: the supervised monitor can keep queues healthy by applying
exact-message deletion for safe stale runtime, task-local, and task-log
candidates. It must remain configurable and reversible.

### 1. Implement built-in delete processors

2026-05-09 implementation note: the approved slice implements the built-in
`delete` processor only. `jsonl_then_delete` remains accepted by config but
fail-closed until the logging callback is implemented in a later slice.

2026-05-09 default note: the approved slice also changes the production
default to `delete`. `report_only` remains the rollback override.

- Files to touch:
  - `weft/core/task_monitoring.py` or `weft/core/task_monitor_processors.py`
  - `weft/core/runtime_pruning.py`
  - `weft/core/retention_pruning.py`
  - `tests/core/test_task_monitor_processors.py`
- Required action:
  - Implement `delete` for safe candidates only.
  - Implement `jsonl_then_delete`:
    - write candidate records to operational JSONL first;
    - if JSONL write fails, delete nothing;
    - then delete exact message IDs;
    - record per-candidate apply result.
  - Keep `report_only`.
  - Treat missing exact message IDs as idempotent skips, not proof of failure.
  - Return processor warnings/errors in the monitor health snapshot.
- Tests:
  - Exact-message deletion removes only selected rows and leaves unrelated rows.
  - JSONL failure fails closed before deletion.
  - Deleting a row already deleted by another actor is idempotent.
  - Claimed outbox residue is not deleted by ordinary `delete`.
- Stop if:
  - implementation tries queue-wide delete or direct SQL.
- Done when:
  - built-in processors can safely apply exact candidates under real broker
    queues.

### 2. Wire processor results to checkpoints and monitor health

- Files to touch:
  - `weft/core/tasks/task_monitor.py`
  - `tests/tasks/test_task_monitor.py`
- Required action:
  - Advance checkpoint only after the selected processor returns success.
  - Persist enough cycle metadata for PING/STATUS:
    `last_processor`, `last_processed`, `last_deleted`, `last_reported`,
    `last_warnings`, `last_errors`.
  - If the processor fails, keep the checkpoint unchanged and continue future
    wakeups. The manager should not restart the monitor just because one
    processor cycle failed.
- Tests:
  - Successful delete advances checkpoint.
  - Failed processor does not advance checkpoint.
  - PING after failure includes error fields but task remains running.
- Stop if:
  - processor failure changes task lifecycle to failed for ordinary per-cycle
    errors. The monitor is long-lived; cycle failures are operational health
    until startup/config is invalid.
- Done when:
  - the monitor can recover from transient processor failures.

### 3. Add manager restart and shutdown hardening for destructive mode

- Files to touch:
  - `weft/core/manager.py`
  - `tests/core/test_manager.py`
- Required action:
  - Ensure manager drain sends STOP to the monitor and waits under the existing
    child-drain deadline.
  - Ensure monitor restart backoff applies after crashes in destructive mode.
  - Re-run the part 1 idle-timeout proof in destructive mode: a running monitor
    is internal supervision, not user work that keeps a CLI-started
    short-lived manager alive forever. For foreground `weft manager serve`
    with idle timeout `0`, monitor should remain running.
- Tests:
  - Manager drain stops monitor child.
  - Crashing monitor restarts after backoff.
  - CLI-started manager with finite idle timeout can still exit when only the
    monitor remains and no user work is active, if that is the intended manager
    reuse behavior. If the intended behavior is "monitor keeps manager alive,"
    document and test that explicitly.
- Stop if:
  - monitor supervision makes `WEFT_MANAGER_REUSE_ENABLED=0` ineffective for
    ordinary `weft run`.
- Done when:
  - monitor lifecycle does not regress manager lifecycle semantics.

### 4. Add the autonomous cleanup default as its own release step

- Files to touch:
  - `weft/_constants.py`
  - specs near environment variables and cleanup boundary
  - release notes or changelog if this repo uses one for Weft releases
- Required action:
  - After part 3 processors pass in `report_only`, change the production
    default from `report_only` to `delete` only if that is still the approved
    decision.
  - Keep environment override so ops can set
    `WEFT_TASK_MONITOR_PROCESSOR=report_only` or
    `WEFT_TASK_MONITOR_ENABLED=0` for rollback.
  - Make clear that `delete` means safe ordinary candidates only, not force
    cleanup.
- Tests:
  - Default config resolves to the chosen default.
  - Override to `report_only` works.
  - Override to `disabled` via `WEFT_TASK_MONITOR_ENABLED=0` works.
- Stop if:
  - changing the default is bundled with other behavior changes.
- Done when:
  - rollback is one config change, not code surgery.

### 5. Part 3 docs and rollout

- Files to touch:
  - `docs/specifications/00-Quick_Reference.md`
  - `docs/specifications/01-Core_Components.md`
  - `docs/specifications/03-Manager_Architecture.md`
  - `docs/specifications/05-Message_Flow_and_State.md`
  - `docs/specifications/07-System_Invariants.md`
  - `docs/specifications/10-CLI_Interface.md`
  - `docs/plans/README.md`
- Required action:
  - Document autonomous safe cleanup as current behavior only after it lands.
  - Keep operational-output caveats clear.
- Ops rollout:
  - Deploy with the default `WEFT_TASK_MONITOR_PROCESSOR=delete`.
  - If rollback is needed, set `WEFT_TASK_MONITOR_PROCESSOR=report_only` or
    `WEFT_TASK_MONITOR_ENABLED=0`.
  - Observe queue counts decreasing over several cycles:
    `weft.log.tasks`, `weft.state.tid_mappings`, old task-local outboxes,
    old ctrl queues.
  - Confirm `weft.spawn.requests` remains empty or reflects only real work.
  - Confirm active manager and monitor PINGs respond.
  - Confirm `weft status --json` and `weft task list --json` become bounded
    enough to return under normal operator timeouts.

## 13. Testing Plan

Use red-green TDD for each part:

- First write the regression or new behavior test.
- Verify it fails for the expected reason.
- Implement the smallest code to pass.
- Run the nearest suite before expanding.

Preferred fixtures:

- Use `broker_env` for direct queue semantics.
- Use `WeftTestHarness` for manager/CLI lifecycle behavior.
- Use real `Queue` objects for reservation, peek, delete, and generator reads.
- Use real `TaskMonitorTask` where the behavior is task-owned.
- Mock only clock/backoff, a custom processor callable, and heartbeat helper
  startup where real heartbeat would make the test slow or recursive.

Do not mock:

- `simplebroker.Queue` for cleanup semantics.
- manager child bookkeeping.
- task-log replay.
- claimed outbox behavior.
- exact-message deletion behavior.

Targeted commands while implementing:

```bash
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py -q
./.venv/bin/python -m pytest tests/core/test_task_monitoring.py -q
./.venv/bin/python -m pytest tests/core/test_manager.py -q
./.venv/bin/python -m pytest tests/commands/test_task_monitor.py -q
./.venv/bin/python -m pytest tests/commands/test_runtime_prune.py tests/commands/test_retention_prune.py -q
```

Final gates for each release:

```bash
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py tests/core/test_manager.py tests/commands/test_task_monitor.py -q
./.venv/bin/python -m pytest tests/commands/test_task_evidence.py tests/commands/test_status.py tests/commands/test_result.py -q
./.venv/bin/python -m pytest tests/commands/test_runtime_prune.py tests/commands/test_retention_prune.py -q
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff format --check weft tests docs
./.venv/bin/ruff check weft tests
```

Run the broader default test suite before release if part 1 changes manager
startup or part 3 changes delete/apply helpers:

```bash
./.venv/bin/python -m pytest
```

## 14. Invariants To Assert In Tests

- `TaskMonitorTask.scan_once()` peeks and does not consume.
- Monitor cycles do not delete any broker messages before part 3.
- Manager enqueues at most one monitor child per canonical context.
- Non-leader managers do not supervise a monitor.
- Monitor PING echoes request ID and includes config.
- Monitor processor failure does not advance checkpoints.
- Runtime-state cleanup candidates are exact message IDs.
- Retention cleanup candidates are exact message IDs.
- Claimed outbox residue is reported but not ordinary-deleted.
- Active/reserved/inbox work is protected under ordinary automatic cleanup.
- JSONL-then-delete writes operational output before deleting, and fails
  closed if output fails.
- Status/result do not read task-monitor checkpoints or logs as authority.
- Domain failures stay domain failures; Weft lifecycle anomalies stay Weft
  anomalies.

## 15. Rollback Plan

Part 1 rollback:

- Set `WEFT_TASK_MONITOR_ENABLED=0`.
- Restart the manager.
- No broker cleanup happened, so no data recovery is needed.

Part 2 rollback:

- Set `WEFT_TASK_MONITOR_PROCESSOR=report_only` or
  `WEFT_TASK_MONITOR_ENABLED=0`.
- If shared evidence extraction caused a read-model regression, revert that
  release. No destructive cleanup should have occurred.

Part 3 rollback:

- First set `WEFT_TASK_MONITOR_PROCESSOR=report_only` or disable the monitor.
- Restart the manager.
- If exact deletes already happened, do not try to reconstruct rows from
  operational JSONL unless an operator explicitly asks. Weft queues are runtime
  state, not the audit store.
- For file-backed SQLite contexts, foreground prune apply may have snapshot
  rollback semantics. The supervised monitor should not rely on that as its
  primary safety story. Its safety story is exact candidates plus conservative
  policy.

## 16. Independent Review Loop

Before implementing each part, run an independent plan review if another agent
family is available. Use this prompt:

> Read `docs/plans/2026-05-07-phase-7-task-monitor-supervision-and-cleanup-plan.md`
> and the governing specs. Look for implementation traps, layering violations,
> unsafe cleanup rules, missing tests, and ambiguous ownership. Do not
> implement. Could you implement this release correctly from the plan alone?

Reviewers should read:

- this plan
- `docs/specifications/01-Core_Components.md`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `weft/core/manager.py`
- `weft/core/tasks/task_monitor.py`
- `weft/commands/task_monitor.py`
- `weft/commands/runtime_prune.py`
- `weft/commands/retention_prune.py`

Treat these review findings as blockers:

- core imports command modules;
- manager scans task logs or prunes queues itself;
- part 1 or part 2 deletes broker messages;
- deletion uses queue names without exact message IDs;
- claimed result residue is ordinary-deleted;
- tests mock away queue semantics;
- rollback cannot be expressed as config plus release revert.

## 17. Fresh-Eyes Review Notes

Self-review pass 1 findings and fixes:

- Ambiguity: "TaskMonitor listens on heartbeat" could make heartbeat a hard
  startup dependency and crash-loop the monitor if the heartbeat service is
  unavailable. Fix: the plan requires heartbeat registration but allows a
  bounded local interval fallback with an operational error.
- Ambiguity: "delete by default" could be read as deleting every old row,
  including claimed outbox residue. Fix: the plan defines `delete` as safe
  ordinary candidates only and keeps claimed outbox residue protected.
- Layering risk: existing evidence and prune logic live in command modules,
  while supervised monitor code lives in core. Fix: the plan requires moving
  reusable non-rendering logic into core before the monitor uses it.
- Manager-overload risk: supervision could become scanning. Fix: manager tasks
  are limited to child start/restart/stop and must not read task-log history.
- Test risk: adding a default monitor child can destabilize unrelated manager
  tests. Fix: the plan allows the test harness to disable the monitor
  explicitly while requiring targeted enabled tests.
- Task wiring risk: the existing foreground monitor spec uses
  `weft.log.tasks` as its input queue, which would be the wrong inbox for a
  persistent heartbeat-driven service. Fix: the plan now requires a private
  `T{tid}.inbox` for wake messages and treats `weft.log.tasks` as an auxiliary
  scan source.
- Internal-runtime spoofing risk: enqueuing the monitor with only TaskSpec
  metadata would weaken the internal runtime envelope boundary. Fix: the plan
  now requires `INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY`.
- Idle-lifecycle risk: a default monitor child could keep finite-idle-timeout
  managers alive forever. Fix: the part 1 manager task now requires an idle
  test where the only live child is the monitor.

Self-review pass 2 result:

- The plan still matches the discussed direction: a manager-supervised
  `TaskMonitor`, heartbeat wakeups, configurable processor, operational logs
  or deletion, and deterministic cleanup of stale queue state.
- It does not drift into a materially different architecture. The only
  notable design constraint added by the review is the core/command layering
  extraction, which is required by existing repo boundaries.

# Lessons Learned

This file is the canonical ledger for durable lessons learned while working on
Weft. Add short entries when a correction exposes a repeated failure mode or a
runbook needs to become stricter.

## How To Add A Lesson

- Use a dated heading or bullet.
- State the failure mode.
- State the durable rule that should prevent it next time.
- Link the related spec, plan, or test when useful.

## Starter Lessons

- Prefer extending the existing `Manager -> Consumer -> TaskRunner` path over
  inventing a second execution path for task lifecycle behavior.
- For queue, process, and lifecycle changes, use real broker-backed tests with
  `WeftTestHarness` or `broker_env` before trusting a fix.
- Treat `weft.state.*` queues as runtime-only unless a spec explicitly changes
  that rule.

## 2026-04-03 Audit Seeds

- Use `build_context()` and `WeftContext` for project resolution and broker
  wiring. Do not re-implement `.weft` discovery or infer Weft artifact paths
  from broker DB naming.
- For append-only queues such as `weft.log.tasks`, `weft.state.tid_mappings`,
  and `weft.state.workers`, use generator-based history reads or the helper
  iterators. Fixed `peek_many(limit=N)` reads are not reliable for
  correctness-critical history scans.
- Keep the TaskSpec boundary clean: templates stay partial, resolved TaskSpecs
  go through shared resolution before construction, and resolved `tid`, `spec`,
  and `io` are not mutated afterward.
- Reuse task/watch queue helpers on live runtime paths. Inside task code,
  opening fresh queue handles instead of using `_queue()` risks bypassing shared
  connection and stop-event behavior.
- Completion and final result visibility are close but not strictly
  simultaneous. Tests and command flows should use the existing wait/result
  helpers instead of assuming an immediate outbox read after a terminal log
  event.

## 2026-04-04 PG Parity

- When a task process exits, close every cached queue handle, not just
  "owned" auxiliary queues. Under SQLite this can look harmless; under
  Postgres it leaks connection-pool threads and prevents the process from
  exiting cleanly. See [weft/core/tasks/base.py](/Users/van/Developer/weft/weft/core/tasks/base.py)
  and [tests/commands/test_task_commands.py](/Users/van/Developer/weft/tests/commands/test_task_commands.py).
- In tests that launch background managers or workers from a CLI subprocess,
  do not start that CLI with a short-lived inner `uv run` environment.
  Background children inherit `sys.executable`; if that interpreter comes from
  an ephemeral env, later task spawns can fail after the parent CLI exits. Use
  the active long-lived environment for those subprocesses.
- Any Weft command path that opens a `persistent=True` broker queue must close
  it explicitly before returning. SQLite often masks this omission; Postgres
  surfaces it as CLI processes that print the expected output and then hang on
  pool-worker shutdown.
- In PG-backed tests, only wrap CLI subprocesses in `uv run --active` when the
  command may launch descendants that outlive the parent CLI process, such as
  `weft run` or `weft worker start`. Using nested `uv` for one-shot commands
  like `init`, `queue`, `status`, or `validate-taskspec` adds material test
  runtime without buying correctness.

## 2026-04-06 Process Trees

- When cancelling, timing out, or limit-killing a task, terminate the full
  process tree, not just the immediate worker PID. Command targets can spawn
  grandchildren, and root-only termination leaves orphaned subprocesses behind
  even though the task itself appears to have stopped cleanly.
- When adding pluggable execution backends, make the persisted runtime handle
  the canonical control surface. Stop/kill paths cannot safely reconstruct
  runner-specific control details later from the original TaskSpec alone;
  persist the runner name plus any control-critical metadata with the live task.
- For non-host runners, translate native runtime terminal state back into
  Weft's task lifecycle instead of trusting only the transport process exit
  code. Docker OOM kills, for example, should surface as `limit`/`killed`
  semantics with runner-native metrics, not as a generic command failure.

## 2026-04-07 Manager Startup Races

- When a CLI launcher is waiting for manager startup in `weft.state.workers`,
  do not reduce the registry to only the latest active manager if the caller
  still needs to recognize its own just-started process. Concurrent starters can
  all register successfully while older entries become invisible to their own
  launchers, causing false "no registry entry appeared" failures.
- If Weft needs one manager per broker context, enforce a deterministic leader
  election rule from the registry inside the manager process itself, not only in
  the launcher. A lowest-live-TID rule lets concurrent starters converge without
  backend-specific locks, provided stale `active` records are pruned by PID
  liveness first.

## 2026-04-07 STOP Pollers

- When a task spins up a background control-poller thread during active work,
  fully join that thread before task teardown continues. A short timed join plus
  lingering broker activity can corrupt the SQLite broker under load, especially
  around STOP/cancel handling.
- For active STOP/KILL handling, keep the poller thread's broker work minimal.
  The poller may notice control promptly, delete the control message, and set
  in-memory stop/kill intent, but terminal state publication, control ACKs, and
  reserved-queue policy must stay on the main task thread after the active work
  has unwound. Publishing `cancelled`/`killed` from the poller can leave the
  task process alive after a terminal snapshot, which is the precursor to the
  SQLite corruption race.
- The safer design is stronger than "minimal poller work": do not use a
  background broker poller for active STOP/KILL at all when the main task
  thread can poll cooperatively inside the runner/session loop. Main-thread
  polling keeps control acknowledgement, terminal state publication, reserved
  policy, and runtime unwind on one execution context and avoids teardown hangs
  around non-interruptible broker reads.
- Public task status must reconcile liveness differently by runner type. For
  the host runner, a terminal log entry is not yet public if the consumer task
  PID is still alive; for external runners such as Docker or macOS sandbox, the
  external runtime description is authoritative and should not be masked by a
  still-exiting consumer wrapper process.
- CLI stop/kill fallbacks should target the consumer task process while it is
  still alive, even for external runners. The persisted external runtime handle
  is the fallback only after the consumer is gone; otherwise the CLI can bypass
  the consumer's terminal-state publication and either strand the task in
  `running` or corrupt SQLite by killing teardown mid-write.
- Active STOP/KILL polling must be decoupled from monitor/report intervals for
  every runner loop, not just the host runner. Subprocess-backed runners that
  only poll cancellation when they collect metrics can miss the CLI control
  deadline and get their consumer wrapper killed before `cancelled`/`killed`
  is published.
- Tests that assert spawned task teardown should use real PID liveness
  (`psutil`/`os.kill`) instead of `multiprocessing.Process.exitcode` as the
  primary oracle. Under `spawn`, the OS process can disappear while the parent
  `Process` object still reports `exitcode is None`, which creates false
  regressions in Linux CI.

## 2026-04-07 Plan Hardening

- Risky Weft plans usually fail at the boundary, not in the middle. Name what
  must not change around the durable spine, queue contracts, TaskSpec boundary,
  and rollback/rollout behavior before decomposing the tasks.
- If a document is human-clear but agent-ambiguous, tighten it immediately with
  clearer ownership, boundary, verification, and required-action language
  rather than trusting the next implementer to infer the right path.

## 2026-04-07 Interactive TTY Tests

- PTY-backed integration tests should exercise one shutdown path at a time.
  Combining a downstream REPL exit command like `quit()` with a wrapper-level
  command like `:quit` creates nondeterministic teardown races under slower
  backends and full-suite load.
- PTY-backed prompt tests need startup timeouts sized for full-suite xdist
  pressure, not just single-test runs. When the prompt does not appear, capture
  the child return code and trailing PTY output so the failure distinguishes
  slow startup from an early process exit.
- Spawned-process teardown tests should treat zombie processes as exited.
  `pid_exists()` alone is too strict for cross-platform process cleanup checks
  and can create false failures after the process has already terminated.

## 2026-04-08 CLI Harness Timeouts

- Shared CLI integration helpers must size their default subprocess and
  completion waits for full-suite xdist and release-gate load, not just for
  isolated local runs. When multiple unrelated CLI tests fail at the same
  fixed timeout, move the fix to the shared harness default instead of raising
  per-test limits.

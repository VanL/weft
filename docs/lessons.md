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
  and `weft.state.managers`, use generator-based history reads or the helper
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
  `weft run` or `weft manager start`. Using nested `uv` for one-shot commands
  like `init`, `queue`, `status`, or `spec validate` adds material test
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

- When a CLI launcher is waiting for manager startup in `weft.state.managers`,
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
- `WeftTestHarness.cleanup(preserve_database=True)` must wait for actual broker
  file release, not just process quiescence. On Windows, recently exited
  managers/tasks and recently closed broker handles can leave `weft-tests.db`
  undeletable for a short period after the last live PID disappears, so the
  preserve-cleanup contract must gate return on database releasability when the
  caller may immediately dispose the tempdir.
- Windows CI can need materially longer than a local developer machine for that
  releasability probe to turn green. Keep the extra wait budget in the shared
  harness cleanup path, and lock it with a regression test, rather than raising
  one flaky caller's timeout.
- Tests that manage `WeftTestHarness` manually must always call
  `harness.cleanup()` in `finally` before any direct `_tempdir.cleanup()`.
  Jumping straight to tempdir cleanup on failure skips worker teardown and can
  leak harness environment overrides into later tests, which turns one broken
  test into a misleading cascade.

## 2026-04-08 Interactive Contract

- Treat `spec.interactive` as a line-oriented, queue-mediated IO mode, not as
  a terminal-emulation feature. Task-local correctness should come from
  `T{tid}.outbox` and `T{tid}.ctrl_out`, with `ctrl_out` carrying terminal
  completion for interactive sessions.
- Do not make interactive clients depend on `weft.log.tasks` for their own
  completion semantics. The global task log is for status, audit, and recovery;
  a task-facing interactive client should only care about its local channels.
- Interpreter-specific argument tweaks such as `python -u -i` or `bash -i`
  are still useful in the line-oriented model. They improve behavior of known
  interactive programs without requiring PTY/TTY transport support.

## 2026-04-09 Completion Grace

- `work_completed` can become visible before the last public outbox messages
  are readable. Completion waiters in `run` and `result` must do one final
  non-blocking outbox drain after the grace window before returning a completed
  result, or they will intermittently drop late-arriving outputs on slower
  platforms.

## 2026-04-08 Zombie PID Liveness

- `psutil.Process(pid).is_running()` is not a sufficient liveness check for
  manager registries. Zombie processes still report `True`, so any
  PID-backed registry or status surface must also reject `STATUS_ZOMBIE`.
- When a manager can register before it is fully ready, treating zombies as
  live lets launchers and leader-election code select a dead manager and route
  spawn requests into an inbox that no process will ever service. The fix
  belongs in the shared PID-liveness helper, not in one caller.

## 2026-04-09 Release Publication Guards

- First-party package publish workflows must wait for a successful `Test`
  workflow on the exact release commit before they upload to PyPI. Tag-specific
  release gates are not a substitute for the broader cross-platform CI matrix
  when that matrix covers additional platforms or jobs.
- For cross-platform log and dry-run output that is asserted in tests, do not
  interpolate `Path` objects directly into strings. Normalize display paths at
  the output boundary with `.as_posix()` or a shared helper.

## 2026-04-09 Manager Bootstrap Unification

## 2026-04-17 Canonical Ownership Fences

- When more than one runtime surface needs "lowest live claimant wins,"
  keep the shared helper pure and narrow. The shared helper should reduce
  already-eligible claimant TIDs to one canonical owner; queue replay,
  registry decoding, liveness, and `unknown` versus `none` semantics belong in
  the caller that owns that runtime state.
- Late duplicate-dispatch hardening belongs at the last side-effect boundary,
  not only in startup or outer-loop leadership checks. For manager spawn
  requests, that means one final ownership fence immediately before child
  launch, with exact-message durability preserved on every non-launch path.
- If an internal runtime capability must stay manager-owned, do not trust
  stored TaskSpec metadata alone. Carry the selector on a manager-owned spawn
  envelope and strip reserved internal keys from ordinary public submission
  helpers so user-authored TaskSpecs cannot spoof internal runtime classes or
  reserved endpoint claims.

## 2026-04-16 Docker Builtin Boundaries

- Docker-dependent builtins must declare their supported platforms in builtin
  metadata and reject unsupported platforms at the builtin-owned entry point.
  Do not rely on the Docker runner plugin alone to make that boundary obvious;
  otherwise Windows users get a generic runner failure instead of an explicit
  builtin contract error.

- Operator-facing manager startup must wrap the same canonical bootstrap helper
  as `weft run`. Allowing `weft manager start` to launch arbitrary manager
  TaskSpecs creates a second execution path and lets registry selection adopt a
  live manager that is not bound to `weft.spawn.requests`.
- Canonical manager election should use the request-queue contract already
  present in the registry (`requests == weft.spawn.requests`) instead of
  treating every live `role="manager"` record as equivalent.

## 2026-04-19 Drain And Streaming Timing Boundaries

- Tests that assert `weft result --stream --timeout N` must establish a real
  running-task readiness boundary first. `weft run --no-wait` still pays
  manager bootstrap before returning the TID, so submission-to-timeout wall
  clock assumptions flake under slower Windows or xdist-heavy runners.
- Manager shutdown must snapshot the children it intends to terminate before
  graceful waits can reap them from `_child_processes`. Otherwise an exited
  task process can disappear from the in-memory map before manager-owned
  managed-PID cleanup runs, leaving a descendant visible on slower Windows
  process tables.
- Manager recovery state-machine tests should patch the manager-owned recovery
  seam directly. Keep one integration test for the exact queue move failure,
  but do not make every recovery/yield assertion depend on monkeypatching a
  specific `Queue` instance across multiple scheduling turns.

## 2026-04-21 Client Follow Boundaries

- Tests that drain client follow/watch iterators with `list(...)` must pass an
  explicit timeout or cancellation boundary. Follow-style APIs are allowed to be
  long-lived by default for UI streams; CI tests need a caller-local deadline so
  a missed terminal observation becomes a useful failure instead of a stuck
  runner.
- If a materialization helper observes a terminal task-log event while deriving
  queue names or result surfaces, downstream iterators must carry that terminal
  observation forward. Advancing the log cursor without preserving the terminal
  payload can skip the only completion signal and leave a completed task
  looking live forever.
- `Task.follow()` has two observable surfaces: lifecycle events and the
  synthetic final result event. If a bounded event wait expires, make a bounded
  result-surface check before raising the event timeout. A missed terminal log
  observation should not hide an already-visible final result.

## 2026-04-20 Result Materialization And Synthetic PIDs

- `weft result` must not treat non-terminal task-log activity without a logged
  TaskSpec as enough to choose default `T{tid}` result queues. For persistent
  tasks with custom queue names, returning early on `task_activity` can bind the
  waiter to the wrong surface and turn a live task into a false timeout.
- Control-command tests that use synthetic mapping PIDs must stub PID liveness
  explicitly. A fake integer PID can belong to a real process on CI, which
  turns "runner handle vs PID fallback" assertions into machine-dependent
  flakes and can accidentally signal unrelated processes.
- External runner parity tests should wait for the runtime description to
  report a live state, not merely for `runtime` to become non-null. Runner
  handles can materialize before Docker or other external runtime probes stop
  returning transient `missing`.
- External runner cleanup must live in a `finally`, not only on the success
  path. Docker-backed tests can fail after the container is created but before
  the monitored subprocess returns, and without unconditional removal that
  leaves stranded `weft-*` containers behind.
- Manager drain loops must honor an existing `_draining` state before
  reevaluating leadership. If a draining non-leader re-enters leadership
  yield after the leader-check interval and after children are gone, it can
  emit a second `manager_leadership_yielded` and skip
  `manager_leadership_drained`.

## 2026-04-15 Structural Start Payloads

- The synthetic start envelope for non-persistent tasks must stay structural all
  the way through function-target argument preparation. If the decoded
  no-payload marker becomes a positional `{}` argument, kwargs-only callables
  fail immediately even though the task was launched without stdin. Keep empty
  work items from being promoted into callable payloads unless the caller
  explicitly supplied envelope keys or a non-empty plain object.

## 2026-04-16 Command Queue Seam

- In command and helper code that already has a `WeftContext`, construct broker
  queues through `WeftContext.queue()` instead of retyping `Queue(...)` with
  `broker_target` and `broker_config`. Repeated open-coded queue construction
  in command paths turns backend changes into grep work and weakens the single
  queue-construction seam Weft already has.

## 2026-04-09 Manager Lifecycle Command Consolidation

- Manager control-plane commands must share one registry replay and liveness
  path. If `weft run`, `weft manager list|status|stop`, and `weft status` each
  interpret `weft.state.managers` separately, dead active records persist on one
  surface and disappear on another.
- Shared registry readers should normalize record shape at the boundary
  (`timestamp`, `requests` backfill, canonical `T{tid}.ctrl_in` fallback) and
  keep presentation differences local to the CLI wrapper.
- Force-stop should trust a manager PID only when that PID is corroborated by
  the TID-mapping queue or an owned `Popen` handle. A registry-only PID can be
  a stale or synthetic record, and treating it as kill-authoritative can signal
  the current pytest worker or another unrelated process during cleanup.

## 2026-04-09 Spawn-Heavy Timeout Tests

- Timeout tests for subprocess trees must assert the kill boundary, not a tight
  startup budget. In xdist-heavy runs, one second is not enough headroom for
  spawned Python worker startup, interpreter import, parent process launch, and
  child process launch.
- When a test needs proof that a descendant existed before timeout cleanup, the
  descendant should publish its own readiness signal as its first action and the
  parent fixture should wait briefly for that signal before entering its long
  sleep. That makes the test measure subtree cleanup instead of scheduler luck.

## 2026-04-12 Local Test Tooling

- Developer-facing docs must say plainly that local `pytest`, `mypy`, and
  `ruff` must come from the repo-managed environment, not from the global
  `PATH`. If that rule is left implicit, zero-context engineers and external
  agents infer that the repo is missing basic tooling instead of using an
  isolated project environment by design.
- In this repo, that guidance is incomplete unless it also says to load
  `.envrc` first. `.envrc` is part of the local-dev contract because it points
  `uv` at `.venv`, prepends the repo `bin/`, and may wire a sibling
  `../simplebroker` checkout into `PYTHONPATH`.
- When the goal is deterministic local verification, docs should prefer the
  explicit in-repo virtualenv binaries such as `./.venv/bin/python -m pytest`,
  `./.venv/bin/mypy`, and `./.venv/bin/ruff` over ambient `uv run ...`
  invocations. That removes ambiguity about which `uv` binary or shell wrapper
  is active.

## 2026-04-13 Detached Bootstrap Proof

- For background control-plane bootstrap, "registry entry appeared once" is not
  a sufficient success criterion. Detached startup has to prove an independent
  lifecycle boundary and then prove that the expected pid and canonical registry
  record are live at the same time the detached launcher still accepts success
  acknowledgement. Fixed startup sleep windows are a poor substitute because
  they make repeated manager-start stress tests pay latency that is not tied to
  real readiness.
- If a detached child can fail before steady-state logging is durable, startup
  stderr or exit status must be captured in the bootstrap path itself. Otherwise
  later status reconciliation can only infer failure after the fact.
- Tests for detached bootstrap should prove structural separation, not only
  happy-path liveness. On POSIX, a strong proof is that the live manager is no
  longer in the caller CLI process group after `manager start` returns.

## 2026-04-09 Cancelled Task Teardown

- Cancelled-task integration tests must not stop at the first `control_stop`
  or cancelled status event when fixture teardown still has to reap live task
  PIDs. On slower Windows runners, the task can report cancellation before the
  worker process has fully exited, and that leaves teardown to do the hard
  cleanup work under much worse timing.
- For CLI cancellation tests that exercise real task processes, wait for the
  TID-mapping surface to show no live `pid` or `managed_pids` before returning
  from the test. That keeps the assertion aligned with the real cleanup
  boundary and avoids cross-platform teardown flakes that only show up under
  CI load.

## 2026-04-14 Explicit Preflight Boundaries

- Do not move "can this binary run here" checks into `weft run`, manager
  submission, or other ordinary execution paths. If operators want ahead-of-time
  validation, give them an explicit validation or diagnostic surface such as
  `weft spec validate --preflight`.
- On the normal run path, attempt the real execution and report the concrete
  startup failure from the real runner or agent path. Hidden preflight gates
  drift from real execution and create a second policy surface to maintain.

## 2026-04-15 Runner Environment Profile Boundaries

- Runner environment profiles are materialized during validation as well as at
  real task startup. Any side-effectful profile logic such as Keychain reads,
  host probes, or filesystem mutation must explicitly defer until the real
  execution path instead of assuming the profile hook is runtime-only.

## 2026-04-16 Constants Boundary

- If the repo says `_constants.py` is the single source of truth for constants,
  enforce that structurally. A style note without a guard test degrades into a
  suggestion, and cross-module drift comes back one local “just this one value”
  at a time.
- Keep only true runtime objects local: sentinels created with `object()`,
  mutable registries, compiled regexes, and singleton plugin instances. Plain
  immutable policy values, message names, timeouts, queue/status sets, import
  refs, and backend hints should live in `weft/_constants.py`.

## 2026-04-16 Poll Floors And Grace Windows

- Short poll floors and grace windows are still behavior, not implementation
  noise. Values like `0.05`, `0.1`, `0.2`, and `0.25` should not be repeated
  inline across result waits, spawn reconciliation, status watch, and
  subprocess cleanup paths.
- Reuse one named constant when multiple surfaces are waiting on the same
  durable control boundary. Add a new constant only when the semantic reason
  for waiting is different.
- When the boundary is already queue-backed, use SimpleBroker's queue-native
  waiting path instead of adding more `sleep()` loops in Weft. The command-side
  result waits, task control waits, spawn reconciliation, and manager lifecycle
  waits now block on queue activity through SimpleBroker watchers, and the
  command-owned `status watch` and limited `queue watch` paths do too. Keep
  only bounded timeout/process checks in Weft.
- Do not oversell this as a broader event system. SimpleBroker owns optimized
  queue waiting; Weft still needs explicit semantic checks after wake-up and
  still needs bounded polling or `process.wait(timeout=...)` for non-queue
  boundaries like PID liveness and detached child exit.
- The main remaining "wrong tool" cases are the ones that still alternate queue
  reads with bespoke sleep logic outside the watcher-owned path, plus any
  future lifecycle surfaces that try to invent a second event plane instead of
  reusing the broker queues they already have.

## 2026-04-16 Exception Boundaries In Manager And BaseTask

- Narrow the catch to the boundary, not the function. Queue writes, deletes,
  moves, `has_pending()`, and queue-close cleanup should catch broker-side
  failures like `simplebroker.ext.BrokerError` plus local runtime wrappers such
  as `OSError` or `RuntimeError` only when that operation is truly best effort.
- Do not keep `json.dumps(...)` or spec/pipeline validation inside a broker
  catch. Serialization and validation bugs are internal defects. They should
  surface instead of disappearing behind a debug log that says a queue write
  failed.
- In `Manager`, malformed spawn/autostart payloads are ordinary local failures
  and should be caught as `ValueError`, `TypeError`, `ValidationError`,
  `FileNotFoundError`, or JSON/file read errors, depending on the real input
  boundary. Unexpected internal exceptions in `_build_child_spec()` and
  autostart resolution should propagate.
- A small number of broad catches are still acceptable, but only at explicit
  extension or interpreter-shutdown seams: optional resource-monitor callbacks,
  runner-plugin stop/kill hooks, and `atexit`/destructor cleanup. If a broad
  catch survives, the code should say why that boundary is best effort.

## 2026-04-16 Review-Finding Boundary Fixes

- Do not reuse reserved-queue cleanup helpers as if every execution path came
  from a reserved inbox message. Direct `run_work_item()` execution has no
  active reserved timestamp, so STOP/KILL finalization must not clear or
  requeue unrelated reserved backlog.
- On correctness-critical broker freshness checks, do not trust a cached
  `Queue.last_ts` read. Force-refresh the broker timestamp at the boundary that
  decides manager idle shutdown; ordinary throttled reads can stay cached.
- Runtime-only streaming markers such as `weft.state.streaming` must clear on
  the successful persistent path before the task returns to `waiting`. If a
  runtime marker can outlive the live work it describes, operator surfaces will
  start treating stale runtime hints as durable truth.
- Append-only control and registry queues need their public truth boundary
  stated explicitly. Raw `ctrl_in` deletion during active STOP/KILL is not the
  public ACK, and raw registry history is not a mutable snapshot. Public truth
  comes from post-unwind `ctrl_out` plus terminal task-log state, and from
  reducing manager registry history to the latest relevant live record.

## 2026-04-08 Manager Role Identity

- Manager identity has to stay consistent across every observability surface,
  especially the manager registry and `weft.state.tid_mappings`. If the registry
  says a TID is a manager but the tid-mapping payload omits `role="manager"`,
  cleanup code can misclassify that manager as a task and send task-kill
  signals to the wrong process.
- For manager tasks, `role="manager"` is structural and must be emitted
  unconditionally by the manager itself rather than inherited from mutable
  TaskSpec metadata.
- Test harness cleanup should treat active manager records as authoritative
  manager tids before it fans out task STOP/KILL calls. That keeps teardown safe
  even if a future regression drops manager role metadata from one surface.
- Test harness cleanup must also treat tasks whose owner PID is the current
  pytest process as local-only, not as external task-control targets. In-process
  `task_factory` Consumers still publish `tid_mappings`; if cleanup fans out
  STOP/KILL for those TIDs, teardown can signal the test runner itself.

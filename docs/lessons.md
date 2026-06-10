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

## 2026-05-20 Manager Reuse Replacement Drain

- Manager replacement has two separate duties: stop the superseded manager from
  accepting new public work, and preserve already launched user work. A
  `superseded` owner row must start a non-stopping drain when children exist;
  reusing the normal STOP drain can cancel user tasks during concurrent
  bootstrap.

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
- Windows cleanup errors may report locked tempdir paths through 8.3 short-name
  segments such as `RUNNER~1`. Harness classifiers must canonicalize both the
  reported filename and configured database artifacts with real-path semantics,
  and should not depend only on artifacts that still exist after partial
  cleanup.

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
- Foreground manager SIGTERM drain needs one overall deadline in addition to
  per-child STOP retries. Release-load PG runs can leave a child slow enough
  that the supervisor-facing `weft manager serve` process gets force-killed by
  the test harness unless the manager itself escalates and exits cleanly.
- Result materialization must not treat empty default result queues as proof
  that the default surface is the right surface. Probing can create queues on
  some backends; wait for TaskSpec metadata or real outbox/control activity
  before choosing a result queue for an unknown TID.
- Manager stop callers need an observation budget larger than the Manager's
  internal child-drain budget. If both clocks are the same length, PG-backed
  xdist runs can time out exactly as the Manager finishes force-reaping
  children and publishing its stopped registry record.
- The observation margin must be large enough for release-load backend and
  scheduler pressure, not just barely larger than the internal drain window.
  A 5-second caller margin was still narrow enough for PG CLI release gates to
  report a false manager-stop failure after successful service convergence.
- Broker timestamps are ordering evidence, not scheduling clocks. Manager-owned
  service restart backoff should use the manager's observation time; otherwise
  backend clock skew or future broker timestamps can make an ensure-mode
  service wait far longer than its configured backoff.

## 2026-05-09 Prune Path Ownership

- Background cleanup must not turn retained lifecycle evidence into broad
  task-local queue probes. If cleanup needs task-local recovery-sensitive
  queues such as `T{tid}.reserved`, make that path explicit and bounded rather
  than coupling it to ordinary `weft.log.tasks` retention. Terminal-looking
  status fields on diagnostic events such as `task_activity` are not enough to
  close a lifecycle family; prefer explicit terminal events.

- Destructive cleanup must have one canonical candidate-selection and exact
  delete path. Foreground CLI wrappers and background monitor services may pass
  different policy arguments, but they must not reimplement pruning semantics.
  The TaskMonitor checkpoint-window cleanup bug showed that duplicate paths can
  both look reasonable while answering different evidence questions under load.
- Manager-owned convergence paths must treat pending work as a correctness
  signal, not as a best-effort wake hint. If a wait/precheck path observes
  pending messages or a manager writes service work to its own queue, the next
  scheduler drain must probe inactive queues immediately so singleton services
  advance by state-machine rules instead of incidental polling cadence. For
  internal singleton services, advance that internal spawn work in the same
  manager turn without draining unrelated public spawn requests.
- Manager-owned service launch has two visibility phases: the manager knows a
  child process was spawned before the child has imported Weft and written its
  own lifecycle row. Durable convergence and tests must be able to use the
  manager's `task_spawned` child PID as live-owner evidence, or load-heavy
  Python process startup can look like "no replacement" even after launch
  succeeded.
- Public spawn backlog needs the same progress discipline as internal
  convergence under release-load backends. `has_pending()` and activity waiters
  are scheduling hints, not correctness boundaries; a live manager should make a
  bounded direct reservation attempt so a missed pending hint cannot leave an
  accepted `weft run --no-wait` request unclaimed.
- CLI control waiters should treat task-local typed terminal `ctrl_out`
  envelopes as first-class terminal proof. Replaying the global task log after
  a terminal control envelope is already visible adds avoidable SQLite
  contention in long mixed-workload release gates.

## 2026-05-31 Broker Finalizers

- Do not perform broker I/O from object finalizers. `__del__` can run during
  pytest or process teardown while SimpleBroker watcher and cleanup threads are
  already inside backend locking, turning best-effort cleanup into opaque xdist
  worker deaths. Broker mutations belong in explicit `stop()` / `cleanup()`
  ownership paths.

## 2026-05-27 Terminal Proof Grace

- A cleanly exited child wrapper is not proof that task-owned terminal evidence
  is absent. Under release-load PG and Windows runs, process exit can beat the
  durable `ctrl_out` or task-log terminal write by more than a local smoke-test
  interval. Keep the manager's wrapper-lost grace window large enough for CI
  backend pressure, and keep worker-completion tests from using narrow local
  timing as a correctness oracle.

## 2026-05-30 Cleanup Progress Boundaries

- Cleanup progress must distinguish "selected work this pass" from "more work
  is eligible now." When a FIFO age scan selects old rows and then stops at
  `first_*_too_young`, the remaining rows are future-eligible only, so the
  policy is base-for-now even though candidates were selected earlier in the
  same pass. Consolidating private cleanup phases into top-level policy rows
  must preserve that stop-reason meaning instead of adding a blanket
  `not candidates` gate.

## 2026-05-13 Plan Review Gates

- Metadata-valid plans are not necessarily implementation-ready. Every plan
  needs a distinct fresh-eyes self-review after drafting, focused on latent
  ambiguity, bad ideas, and whether a zero-context engineer could implement it
  correctly.
- High-stakes or complicated plans need an external reviewer when one is
  available. Different-agent reviews may take 5-10 minutes; that wait is part
  of the review cost, not a reason to skip the gate.

## 2026-05-15 Manager Hot Loops

- Rate gates must sit before expensive proof work. A manager loop that checks
  leadership, service terminal proof, pending service rows, or global task-log
  evidence before deciding whether that work is due can burn CPU even though
  the nominal convergence interval looks correct.
- Reusable task-loop timing belongs at `BaseTask`, not inside one special task.
  Long-lived tasks should expose due work through `next_wait_timeout()` and let
  the shared `MultiQueueWatcher` activity path wake them early for queue work.
- Keep the state-machine boundary clean: collect evidence on a due cadence,
  then pass that snapshot through the reducer. Do not move lifecycle decisions
  into timer code, and do not scan global logs just to discover that no reducer
  turn is due.
- Broker-free worker lanes must stay broker-free through the full call graph.
  Passing broker context into a runner or resource monitor from a worker thread
  can reintroduce database access indirectly, even when the worker body itself
  has no queue calls.
- A worker result committed at the start of a manager turn still counts as
  launch work for that turn. Do not accept another spawn request before control
  has had a chance to run; otherwise a fast child-launch worker can recreate a
  same-turn STOP race.
- Manager-to-manager liveness probes should be non-blocking reactor state. A
  stale-looking registry row that has a keyed PING in flight is still unknown
  until the probe deadline, not stale proof to prune or supersede immediately.
- Service-owner PING fallback follows the same rule. A candidate with
  `ping_pending` is uncertain evidence; only after the pending probe expires
  should normal recent/stale classification decide whether it blocks restart.
- TaskMonitor custom processors can be worker-lane work only after the reactor
  has built a complete candidate snapshot. Checkpoint advancement, cached PONG
  diagnostics, built-in cleanup, and exact deletes must stay on the
  TaskMonitor reactor.
- Timer-driven service tasks should not hide private wait loops inside
  `process_once()`. Put due-time math in `next_wait_timeout()` and let the
  shared task runner wait, otherwise one task silently bypasses the same
  control and worker-result wake rules the rest of the runtime uses.
- Service status is a selected-owner read model, not a latest-event display.
  When singleton convergence creates a terminal duplicate and a separate live
  owner, status must report the live owner. Terminal proof should only
  override launch evidence for the same TID.
- A reactor worker-result lane needs both a bounded queue and a bounded
  per-turn drain. Without the queue bound, high-volume live streaming can grow
  memory without limit; without the drain budget, the reactor can process local
  worker progress indefinitely before task-local control gets a turn.

## 2026-04-08 Zombie PID Liveness

- `psutil.Process(pid).is_running()` is not a sufficient liveness check for
  manager registries. Zombie processes still report `True`, so any
  PID-backed registry or status surface must also reject `STATUS_ZOMBIE`.
- When a manager can register before it is fully ready, treating zombies as
  live lets launchers and leader-election code select a dead manager and route
  spawn requests into an inbox that no process will ever service. The fix
  belongs in the shared PID-liveness helper, not in one caller.

## 2026-05-20 Cleanup Markers

- Cleanup completion markers must be based on verified cleanup facts, not only
  absence of exceptions. For task-local control cleanup, `Queue.delete()`
  returning `False` can no longer be treated as success when rows remain; check
  post-delete queue stats before writing `task_control_deleted_at_ns`.
- TaskMonitor catch-up state can have more than one owner in the same turn.
  A runtime cleanup worker result must not clear task-log catch-up that was set
  by the built-in cycle, and a control slice that spends the full family budget
  must preserve reserved-cleanup pending state so the next catch-up turn runs.

## 2026-05-22 Dead-TID Cleanup Policy

- When a user specifies a concrete cleanup API, do not replace it with a nearby
  indexed or cached path during planning. If the API is expensive, bound it in
  the worker policy and report the cost; do not silently change the semantics.
- Cleanup policy selection belongs in `weft/core/monitor/policies/`, not in the
  TaskMonitor reactor. The reactor schedules and commits results; the worker
  performs broker/store work; the policy module decides which queues and
  task-log rows are eligible.

## 2026-05-20 Host PID Namespace Visibility

- A host-scoped PID that is not visible from a container is ambiguous evidence,
  not process-death proof. Status/runtime descriptions should report unknown
  namespace visibility rather than `missing`; only same-namespace host liveness
  checks can prove a host process is gone.

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
- A visible one-shot outbox result must win over the caller timeout if the
  timeout expires during the quiet-result grace window. The grace window is for
  collecting near-simultaneous output, not for converting an already-readable
  result into a timeout on slower CI.
- Normal `WeftTestHarness.cleanup()` needs the same Windows database
  releasability gate as `preserve_database=True`. Removing files and then
  relying on tempdir cleanup is not deterministic when recently exited broker
  users still hold SQLite handles.
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

## 2026-04-27 Runtime-Less Status Snapshots

- Public status must not treat old non-terminal task-log entries as live solely
  because runtime-only queues no longer contain a PID or runner handle. If the
  live proof surface is absent, status needs an explicit staleness rule and a
  separate live-manager registry exception so old manager task rows do not
  survive after their registry records have been pruned.

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

## 2026-04-24 Runtime Handle Authority

- Runtime identity must carry its control authority with it. A host PID is only
  actionable when a `runtime_handle` says `control.authority == "host-pid"` and
  scopes that PID under `observations.host_pids`; container-local PID values
  must not be treated as host process handles.
- Manager registry records and task TID mappings should use the same
  runtime-handle shape. If a supervised manager cannot expose a host-process
  handle, publish an explicit external-supervisor handle instead of making
  generic manager code inspect the supervisor.
- Durable task logs can still replay old lifecycle facts, but runtime control
  and liveness checks should reject invalid handle shapes. This keeps history
  readable without reviving ambiguous control authority.

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

## 2026-04-27 Test Sleep Hygiene

- SimpleBroker has no blocking read-with-timeout API. `Queue.read_one()` and
  `peek_one()` return `None` immediately when the queue is empty. Polling in
  test wait helpers is necessary, not laziness. Do not replace polling loops
  with "blocking reads" — the API does not exist.
- `QueueWatcher` is the only blocking primitive in SimpleBroker, and it polls
  internally (`burst → backoff → threading.Event.wait` chunks up to
  `BROKER_MAX_INTERVAL=0.1s`). Moving test polling to a watcher adds thread
  overhead and a teardown surface; it does not remove polling.
- Several `tests/helpers/weft_harness.py` sleeps protect specific Windows
  races (`f10d9d6` "Fix Windows preserve-database cleanup race"; `b23f8c5`
  "Fix preserve cleanup and manager drain races"). Do not remove or shorten
  them without reproducing the race they fixed under
  `tests/test_harness_registration.py` on Windows.
- The 17 inline polling sites in `tests/core/test_manager.py` were
  deliberately tuned in `d810eec` "Address manager test settle time". They
  drive a real `multiprocessing` consumer with per-test interleaving and
  cannot be replaced with deterministic tick counts or a generic
  `tick_manager_until` helper without losing context.
- Consolidation of `_wait_for_*` helpers across test modules is only safe
  when the source bodies are verbatim duplicates. Helpers that differ in
  predicate, side effect (e.g. interleaved `manager.process_once()`),
  iteration order (`reversed(events)`), or failure shape should stay local.
  A local helper that names what the test is waiting for is more readable
  than a generic shared one. On audit (2026-04-27), the 15 candidate
  helpers cited in the test sleep hygiene plan all differed meaningfully —
  local helpers are the right shape; no consolidation was needed.
- Measured on 2026-04-27 (fast suite, `-m "not slow"`, N=23 calls from the
  broker-heavy xdist worker): median iterations per `wait_for_completion`
  call = 5; p90 = 12; p99 = 51; median wall-clock = 0.32 s; p99 = 3.76 s.
  Aggregate suite-wide polling cost is small (~5 s across the run) but the
  tail is long. The measurement is borderline per the plan's gate (median
  ≥ 5 with long tail). Sample is thin — re-measure with `-m ""` before
  acting. Exponential backoff in `wait_for_completion` is a candidate for
  a follow-up plan, not justified on this sample alone.
- On Windows, an open SQLite handle is a hard tempdir deletion blocker. Harness
  cleanup cannot assume test-local `WeftClient`, `Task`, or `Queue` objects have
  died before `__exit__`; the test frame may still hold them. Before probing or
  deleting the harness DB, close live SimpleBroker queues bound to that DB. If a
  bounded cleanup still cannot release the file, preserve the tempdir instead of
  turning cleanup lag into a behavioral test failure.

## 2026-05-04 External Supervisor Manager Liveness

- `control.authority="external-supervisor"` must not mean "live forever."
  Generic Weft code still must not inspect Docker, podman, Kubernetes, or
  supervisor APIs, but it needs a generic liveness boundary. Use scoped
  `observations.host_pids` when the supervisor can provide them; otherwise use
  the manager registry heartbeat age.
- `weft manager stop --force` must not claim success for a fresh externally
  supervised manager when no host PID or process handle is available. Either
  the manager must publish a stopped record, the supervisor must stop it, or
  the heartbeat must expire and be pruned.
- A host-pid manager record must prove host-process identity, not just PID
  existence. Docker makes the failure obvious because every replacement
  container can have a live PID `1`, so manager liveness readers must compare
  recorded process creation time when it is present before accepting the PID as
  the same runtime.
- If a manager process detects that it is running inside a container and no
  explicit `WEFT_MANAGER_RUNTIME_HANDLE_JSON` was supplied, it is safer to
  publish an `external-supervisor` handle than to publish an in-namespace
  `host-pid` handle. Auto-detection is only a conservative fallback; production
  supervisors should still inject explicit runtime identity when they can.
- Runtime-specific external-supervisor liveness belongs behind an extension
  probe registry, not in core manager code. A probe may return immediate stale
  proof when it can prove the named runtime is gone; missing or inconclusive
  probes fall back to heartbeat age.
- Treating scoped host PIDs on an `external-supervisor` handle as direct task or
  manager identity recreates the container PID bug in another form. Core code
  should use host PID identity only for `control.authority="host-pid"` handles;
  supervised runtimes need their registered liveness probe, with unknown
  remaining unknown until the heartbeat age rule resolves it.
- A manager child launch must be atomic with respect to the first work item:
  seed the child inbox durably before starting the process, and do not launch
  the child if seeding fails. Logging and continuing creates live children that
  can only report `waiting_on=T...inbox`, which turns into result/run/pipeline
  hangs under backend load.

## 2026-05-06 Status Reanimation Boundary

- Public status reconstruction must not use runtime liveness to move terminal
  lifecycle state back to `running`. A live or apparently live runtime handle
  can explain a conflict through `reconciliation` metadata, but
  `weft.log.tasks` terminal proof remains the public lifecycle status.
- Weak host PID evidence (`observations.host_pids` without process creation
  identity) is especially unsafe for old task rows. PID reuse or namespace
  changes can make an unrelated host process look like the original task.

## 2026-05-10 Control And Service Convergence

- Postgres under load exposes proof-boundary gaps that SQLite often hides.
  A control ack, a successful queue write, or a backend waiter wakeup is
  progress evidence only; it is not convergence. Command helpers must report
  success from terminal/dead proof, and manager-owned services must advance
  through bounded reducer passes rather than relying on incidental scheduling.
- For manager-owned singletons, a recent `running` row with a missed PING is
  uncertainty, not death. Treating that row as terminal clears the active owner
  and creates restart storms; only terminal task proof or sufficiently stale
  nonterminal evidence may drive replacement.
- Runtime endpoints need current-state process proof on the runtime-handle
  contract, not legacy ad hoc PID fields. A stale endpoint owner with no
  terminal log and no host-process proof can make every replacement self-
  supersede under load.
- Tight convergence loops must be conditional on active work. A stable
  singleton service still needs periodic audit, but auditing a healthy steady
  state at the same cadence as pending spawn or uncertain evidence can burn CPU
  and regress manager startup under SQLite even if it helps a PG load failure.
- Caller-owned TaskSpec metadata is not manager service authority. Public
  submissions may carry useful user tags named `role` or `internal`, but
  singleton reconciliation must trust only manager-authored envelopes, runtime
  handles, tracked children, or keyed control responses.
- Force-kill authority must come from the same proof that made a duplicate
  service candidate live. A logged PID can explain history, but only a tracked
  child or scoped runtime handle should be used as a process-tree kill target.
- A Docker runtime handle is a control and observation contract, not mere
  allocation evidence. Do not publish it while Docker still reports the
  container as `created`; either wait until the runtime leaves that transient
  state or fail before exposing misleading status.
- In the work-stealing manager model, a reserved spawn request is exclusive
  only while the reserving manager is still able to launch it. If STOP,
  leadership drain, or another shutdown path prevents launch before any child
  side effect, the manager must release the message back to its source queue;
  keeping it in a reserved queue can deadlock dependent work such as pipelines.
- A manager that loses leadership while it has user children is not stopped;
  it is draining. Startup reconciliation must treat a live draining launched
  manager as detached work to leave alone. Aborting that launcher can terminate
  the manager process tree and send signals to user tasks that were supposed to
  finish naturally.
- Wait helpers that watch a shared task log must advance cursors only on the
  target task's events. Under Postgres load, an unrelated event with a newer
  timestamp can become visible before an older target event commits; a global
  cursor can then skip the target terminal proof even though it exists.
- The target-task cursor rule still needs a bounded overlap, not an unbounded
  replay of every non-target event after the last target event. Otherwise a
  test-side waiter can create enough Postgres read pressure to starve the
  in-process manager it is waiting on.
- Pipeline child bootstrap is accepted-task internal work. Sending generated
  stage and edge spawn requests back through the public spawn backlog can leave
  a pipeline partially bootstrapped under load; use the internal spawn lane and
  dependency-friendly launch order once the top-level pipeline task is running.
- Internal spawn convergence must be triggered by pending internal work itself,
  not only by the manager setting a singleton-service flag. Pipeline children
  and other accepted-task runtime helpers can write that lane from task
  processes, so the manager must drain it with a bounded per-turn budget even
  when no service enqueue occurred in that manager turn.
- Tests that simulate age-gated cleanup with real broker message IDs must
  anchor their synthetic `now_ns` after the newest row whose age matters, not
  after the first row in the scenario. On slow Windows CI, real queue writes in
  a single test can be far enough apart that later rows are correctly too young
  for the cleanup floor, turning a test-clock shortcut into a false failure.
- A task's `next_wait_timeout()` is timer ownership, not queue ownership. Do
  not call `Queue.has_pending()` from task-specific timer code to force a zero
  timeout; that recreates per-task queue polling and can burn CPU. The shared
  `MultiQueueWatcher.wait_for_activity()` path must wake immediately when
  configured queues already have pending work, and timers should only bound
  that wait.
- Fast child/process completion belongs in the shared task reactor, not in
  manager-specific service reconciliation. Use `BaseTask`'s worker-result wake
  cap for regular task execution and keep singleton service audit timers slower
  unless there is real accepted work to advance.
- Reserved work already owned by an active Consumer must not count as new wait
  activity while the worker lane is still running. Otherwise the shared
  watcher sees the reserved queue as permanently pending, returns immediately,
  and turns a correct worker lane into a CPU spin under parallel test or
  production load. Control queues still count as wait activity during active
  work.
- A task's local due timer is not queue evidence. `next_wait_timeout() == 0`
  should run due local work and then return to the shared wait path; it should
  not trigger `Queue.has_pending()` or empty `move_one()` probes. Native
  activity waiters should supply queue-discovery hints, and non-native fallback
  polling must stay bounded by positive wait intervals.

## 2026-05-13 Manager Leadership Liveness

- `external-supervisor` means "delegate liveness proof," not "skip liveness
  proof." A missing or inconclusive supervisor probe is `unknown`, and unknown
  evidence must not make a running dispatch-capable manager yield leadership.
- Manager PONG has two roles that must stay separate: it can prove a task-local
  control surface is alive, but leadership authority also requires dispatch
  eligibility. A draining or stopping manager can answer PING and still be the
  wrong control plane for public spawn work.
- Voluntary leadership drain must be reversible when replacement proof
  disappears. STOP/KILL drains are terminal control paths; leadership yield is
  duplicate-manager convergence and can resume by publishing
  `manager_leadership_resumed` rather than leaving a live process that no
  longer drains public work.
- Manager hot paths should maintain targeted registry and control-probe state.
  Replaying the whole service registry or running status-style world scans from
  every leadership turn turns ops recovery into more database pressure exactly
  when the backend is already under load.
- Manager replacement tests must model interleavings, not just steady states.
  An incumbent can pass its self-superseded check before `--replace` writes a
  `superseded` row, then publish an active heartbeat afterward. That heartbeat
  must re-check for concurrent supersede before pruning self history, or it can
  erase the operator's replacement evidence and resurrect itself as active.

## 2026-05-23 Runtime Cleanup Slices

- TaskMonitor runtime cleanup policies should live in one policy module, while
  `TaskMonitor` owns only reactor scheduling, worker launch, broker effects,
  and worker-result commits. Mixing terminal, reserved, and dead-TID cleanup in
  one executor result lets a slow dead-TID recovery path hold terminal cleanup
  hostage and makes PONG counters ambiguous. Run each cleanup class as a
  discrete worker-thread slice launched by the reactor.
- Queue-name fallback cleanup must hydrate Monitor state in batches before
  entering selection loops. A per-TID `MonitorStore.get_task()` call inside a
  queue-derived scan opens broker resources per candidate, so a large stale
  `T*.outbox` backlog can hit every slice deadline, report perpetual catch-up,
  and burn CPU without draining anything.
- `weft_monitor_task_messages` is a pending exact-delete reference table, not
  terminal history. If a valid raw task-log row has already been folded into the
  Monitor parent, retry sweeps must be allowed to delete that raw row and child
  ref even when the family is still open; terminal summary gates only compact
  summary/disposition and family retirement.

## 2026-05-27 Runner Timeout Boundary

- Runner timeout loops must check authoritative runtime completion before
  enforcing an elapsed-time timeout. A polling loop can wake after the timeout
  boundary even when the child exited successfully inside the budget, so
  timeout classification must be gated on the runtime still being alive. See
  [weft/core/runners/subprocess_runner.py](/Users/van/Developer/weft/weft/core/runners/subprocess_runner.py)
  and
  [tests/core/test_subprocess_runner.py](/Users/van/Developer/weft/tests/core/test_subprocess_runner.py).

## 2026-05-31 Monitor Checkpoints And Status

- A forward Monitor checkpoint is not a proof that every older raw broker row
  is represented in the Monitor store. Partial rollouts and legacy cleanup can
  leave pre-checkpoint `weft.log.tasks` rows with no
  `weft_monitor_task_messages` ref. Recovery for that case must be a bounded
  backfill that leaves the checkpoint forward-only, folds valid rows into the
  existing Monitor-store report/delete path, and reports malformed rows before
  exact deletion in `jsonl_then_delete`.
- Internal service task-log rows are only one evidence source. Status must not
  let `internal=true` bypass stale runtime reconciliation forever. When the
  service registry proves a newer same-service owner, or no runtime proof
  supports an old service child after the stale window, the public read model
  should stop presenting that old TID as active without writing synthetic
  lifecycle events.

## 2026-06-10 Same-Domain Comparisons And Verified Deletion Markers

- Never compare broker hybrid message IDs against host wall-clock time in
  readiness or cleanup gates. The broker ID domain is durably monotonic and
  can run ahead of any single host's clock after a clock regression or a
  fast-clocked writer; a wall-clock bound over a moving ID head can stall a
  summary-gated pipeline silently, with zero errors, for days. Zero-retention
  "ready once ingested" gates must be bound by the ingest checkpoint
  (ID domain against ID domain); wall-clock cutoffs are only correct for
  observed-staleness windows over a fixed ID. See
  docs/specifications/05-Message_Flow_and_State.md [MF-5] and
  docs/plans/2026-06-10-self-healing-runtime-maintenance-plan.md.
- A completion marker whose writer can be satisfied vacuously will eventually
  lie. Deletion markers such as `raw_deleted_at_ns` must be coupled to
  verified per-row deletion in the same pass (the exact-row apply layer
  re-verifies per ID on batch under-deletion), and audit-mode recovery paths
  must carry the same summary gate as the main deletion path.

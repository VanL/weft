# Lessons Learned

Startup context is the Golden Rules plus dated sections after the
watermark in `docs/coalescing.md`; the rest of this ledger is
searchable history.

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

## Golden Rules

Universal principles that inform every change. The dated sections below are the
incident log; these are the durable rules distilled from it. _(2026-06-30)_

1. **Canonicalize once, at the boundary.** Normalize CLI/TaskSpec input at the
   ingest boundary through one shared validation path. Never add runtime
   dual-case fallback readers — they hide contract bugs.
2. **Fix forward, never fall back.** Do not add read-time fallback modes to mask
   drift or corruption. Detect invariant violations and surface them; repair
   with forward migrations.
3. **One canonical contract across all consumers.** Same queue names, payload
   shapes, and vocabulary everywhere. Mixed legacy keys cause cascading
   mismatches.
4. **Validate at write time, fail fast.** Catch errors at the point of creation,
   not in downstream batch gates or runtime checks. Prefer explicit rejection
   over silently ignoring unsupported fields or modes.
5. **Update all consumers in the same change.** When renaming queues, tightening
   the TaskSpec/result schema, or changing any contract, update all producers
   and consumers together. Partial renames pass isolated checks but fail at
   runtime.
6. **Test what you ship.** Add a regression test with each behavior-changing
   fix. Generate fixtures through production code paths (`task_factory`, real
   `Consumer` / `TaskRunner` flows), not synthesis.
7. **Plans fail at boundaries, not in the middle.** For risky work, name what
   must not change on the durable spine, the queue/TaskSpec contracts that must
   stay stable, anti-mocking rules, rollout/rollback constraints, and post-deploy
   observation before implementation starts.
8. **If a document is human-clear but agent-ambiguous, tighten it immediately.**
   Missing owner, boundary, verification path, or required action makes agents
   guess wrong even when the prose feels obvious to a human.
9. **Agents suggest dependencies; humans add them.** An agent must not introduce
   a new dependency on its own — propose it with justification (what it is for,
   why the standard library or an already-vendored dependency will not do, and
   the cost of taking on permanent code we do not control). The human decides
   whether it enters the manifest.
10. **Flag concerns and calibrate uncertainty, even when you did exactly what
    was asked.** Surface risks noticed in passing instead of letting a completed
    task hide a known landmine. Distinguish verified from unverified claims with
    precise language ("I have not confirmed X") rather than a vague "this should
    work." Report blockers with precise causes.
11. **Handle the error path, not just the happy path.** A feature whose success
    path works but whose error/empty/timeout path is silently ignored is
    incomplete. Name the failure cases in the plan's success criteria and test at
    least one. Do not paper over an unexpected null/empty with a defensive
    check — find out why it is null first.
12. **Formatting is owned by the project formatters — run them; do not
    hand-format, and do not reformat incidentally.** Style is decided by `ruff`
    from the repo-managed environment (`./.venv/bin/ruff format` and
    `./.venv/bin/ruff check weft`, after loading `.envrc`), not by manual
    whitespace or style changes. In a behavior change, keep the diff to the lines
    the task requires and do not let an editor or formatter reflow untouched code
    into the change. Keep formatting-only churn in its own change. If a line
    changed only because "I was in there," revert it.
13. **Enumerable contracts get executable gates.** Any list a document asserts
    — exit codes, queue names, config keys, CLI flags, control message types,
    listed edge cases — must be mirrored by a machine check that enumerates it
    (a firing test per element, a no-op prevention test per key). Prose binds
    only what gets checked; agents comply uniformly with gates and unevenly
    with everything else. (See engineering-principles §9, testing-patterns
    Pattern 9, and `docs/agent-context/runbooks/adversarial-acceptance-probes.md`.)
14. **Ground control and liveness in scoped runtime handles and forward-only
    terminal facts.** Terminate full process trees rather than bare root PIDs,
    and act on a host PID only when a persisted `runtime_handle` declares
    `control.authority == "host-pid"` with scoped `(pid, create_time)` identity
    (`observations.host_pids`), rejecting `STATUS_ZOMBIE` and treating
    cross-namespace or missing supervisor probe evidence as `unknown` rather
    than dead (`weft/core/runtime_identity.py`, `weft/core/tasks/runner.py`).
    Keep active STOP/KILL polling and terminal publication on the main task
    thread (`weft/core/tasks/base.py`), check authoritative completion and
    private channel seals before classifying a runner timeout or exit
    (`weft/core/terminal_handoff.py`,
    `weft/core/runners/subprocess_runner.py`), and never let runtime liveness
    reanimate a task after durable terminal proof is published
    (`weft/commands/_task_snapshot_reducer.py`). (distilled from 11 entries,
    2026-04-06..2026-08-04, source 396c3b85)
15. **Converge manager bootstrap and dispatch through one registry reducer and
    a final launch fence.** Route `weft run` and `weft manager
    start|serve|list|status|stop` through the shared bootstrap and
    `weft.state.services` registry replay path (`weft/commands/manager.py`,
    `weft/core/service_convergence.py`), electing the lowest live claimant
    bound to `weft.spawn.requests` while letting concurrent starters observe
    their own registration and detached readiness proof
    (`weft/core/launcher.py`). Enforce one final leadership and reservation
    fence immediately before child launch after durably seeding the child inbox
    (`weft/core/manager.py`), strip internal runtime selectors from public
    submissions (`weft/core/spawn_requests.py`), refresh uncached broker
    timestamps before idle shutdown, and clear ephemeral `weft.state.streaming`
    markers before returning persistent tasks to `waiting`. (distilled from 6
    entries, 2026-04-07..2026-04-17, source 396c3b85)
16. **Preserve terminal observations and drain final output across completion
    and follow boundaries.** Wait for resolved TaskSpec metadata or real
    outbox/`ctrl_out` activity before binding result queues so `task_activity`
    or empty default probes never lock a waiter onto the wrong surface
    (`weft/commands/result.py`, `weft/commands/_result_wait.py`). Carry
    terminal events discovered during materialization forward into
    `Task.follow()` and event iterators (`weft/commands/events.py`), perform
    one final non-blocking outbox drain after completion grace so an
    already-readable result wins over a caller timeout, keep interactive
    session completion on task-local `T{tid}.outbox` and `T{tid}.ctrl_out`, and
    keep manager wrapper-lost and stop observation budgets wider than child
    teardown under load. (distilled from 5 entries, 2026-04-08..2026-05-27,
    source 396c3b85)
17. **Keep destructive cleanup in one policy seam and write deletion markers
    only after verified removal.** Centralize candidate selection in
    `weft/core/monitor/policies/` and `weft/core/pruning/`, run terminal,
    reserved, and dead-TID maintenance as discrete `MaintenanceWorker` slices
    with batch `MonitorStore` hydration (`weft/core/monitor/runtime.py`,
    `weft/core/monitor/store.py`), and keep exact deletes and checkpoint
    commits on the `TaskMonitor` reactor
    (`weft/core/monitor/task_monitor.py`). Distinguish `first_*_too_young`
    age-scan stops from pending catch-up (`weft/core/monitor/progress.py`), and
    record deletion markers (`task_control_deleted_at_ns`, `raw_deleted_at_ns`)
    only after post-delete queue or row verification confirms the rows are
    gone. (distilled from 5 entries, 2026-05-09..2026-05-30, source 396c3b85)
18. **Run verification and lint probes against the real project configuration
    and straddle every repaired boundary.** Load `.envrc` and invoke
    `./.venv/bin/python -m pytest`, `./.venv/bin/mypy`, and `./.venv/bin/ruff`
    against the repository's full `pyproject.toml` (never `--isolated` or
    single-rule `--select RUF100` when evaluating policy changes or updating
    `bin/ruff_suppression_index.py` and
    `tests/specs/test_ruff_suppression_index.py`). Build schema-migration
    fixtures from prior-release DDL and real `ALTER TABLE` steps, straddle
    repaired guards with nearest-valid and nearest-invalid cases, pair negative
    or exact-count assertions with reachable positive setup and producer
    closure (`tests/helpers/reactor_driver.py`), and keep `WeftTestHarness`
    cleanup, stubbed synthetic PIDs, and external runner teardown scoped so
    tests never signal unrelated processes or leak containers
    (`tests/helpers/weft_harness.py`). (distilled from 11 entries,
    2026-04-12..2026-09-11, source 396c3b85)

## Fold Records

Cold dated sections that have been distilled into a runbook or Golden Rule and
removed from the incident log below. The raw material is recoverable at the
cited source SHA; each surviving distillation carries its own fold cue. This
index is not a dated section and does not count toward the coalescing trigger.

- 2026-07-15 — distilled 4 cold test-timing sections into
  `docs/agent-context/runbooks/testing-patterns.md` Pattern 10 ("Lifecycle
  Tests Assert the Wrong Boundary Under Load"): "Interactive TTY Tests"
  (2026-04-07), "CLI Harness Timeouts" (2026-04-08), "Spawn-Heavy Timeout
  Tests" (2026-04-09), "Cancelled Task Teardown" (2026-04-09). Raw entries:
  `git show 52e74599:docs/lessons.md`.
- 2026-10-15 — folded 8 cold context, queue-seam, TaskSpec, and config-custody
  sections already covered by Golden Rules 1, 3, 4,
  `docs/agent-context/engineering-principles.md` (§1.5, §3, §8, Secondary
  Rules), and `docs/agent-context/runbooks/runtime-and-context-patterns.md`
  (§1, §2, §4, §5): "Audit Seeds" (2026-04-03), "Explicit Preflight Boundaries"
  (2026-04-14), "Structural Start Payloads" (2026-04-15), "Runner Environment
  Profile Boundaries" (2026-04-15), "Command Queue Seam" (2026-04-16),
  "Constants Boundary" (2026-04-16), "Preserve Resolved Context Through Client
  Adapters" (2026-09-07), "Configuration custody includes process imports"
  (2026-09-14). Raw entries: `git show 396c3b85:docs/lessons.md`.
- 2026-10-15 — folded 6 cold planning, review-gate, release-guard, and cohesion
  sections already covered by Golden Rules 7, 8, 13,
  `docs/agent-context/engineering-principles.md` (§7, §7.5, §11),
  `docs/agent-context/runbooks/writing-plans.md` (§4c, §8, §10), and
  `docs/specifications/08-Testing_Strategy.md` `[TS-4]`: "Plan Hardening"
  (2026-04-07), "Release Publication Guards" (2026-04-09), "Plan Review Gates"
  (2026-05-13), "Cohesion Over File Size" (2026-07-06), "Review State Is a
  Property of the Artifact" (2026-08-11), "Review Deltas Include Observable
  Side Effects" (2026-09-08). Raw entries: `git show 396c3b85:docs/lessons.md`.
- 2026-10-15 — folded 3 cold reactor-waiting, broker-finalizer, and exception
  boundary sections already covered by `docs/program-theory.md` (`[WT-2]`,
  `[WT-4.3]`, `[ALT-WT-002]`), `docs/agent-context/runbooks/testing-patterns.md`
  ("Coverage Policy"), and `docs/specifications/01-Core_Components.md`
  `[CC-2.1]`, `[CC-2.2.1]`: "Poll Floors And Grace Windows" (2026-04-16),
  "Exception Boundaries In Manager And BaseTask" (2026-04-16), "Broker
  Finalizers" (2026-05-31). Raw entries: `git show 396c3b85:docs/lessons.md`.
- 2026-10-15 — folded 3 cold Docker builtin, optional SDK adapter, and
  PostgreSQL test-wrapper sections already covered by
  `docs/agent-context/engineering-principles.md` (§4, §4.1),
  `docs/agent-context/runbooks/testing-patterns.md` (Rules 2, 3, 7; Pattern 1),
  `docs/specifications/08-Testing_Strategy.md` `[TS-4]`, and
  `docs/specifications/10B-Builtin_TaskSpecs.md`: "Docker Builtin Boundaries"
  (2026-04-16), "Optional Runner SDK Boundaries" (2026-06-17), "The PostgreSQL
  Test Wrapper Owns DSN Provisioning" (2026-08-26). Raw entries:
  `git show 396c3b85:docs/lessons.md`.
- 2026-10-15 — distilled 11 cold runtime-handle, process-identity, and liveness
  authority sections into Golden Rule 14 (and `01-Core_Components.md`
  `[CC-2.4]`, `[CC-3.2]`, `[CC-3.5]`; `07-System_Invariants.md`
  `[LIVENESS.R3]`–`[LIVENESS.R7]`): "Process Trees" (2026-04-06), "STOP
  Pollers" (2026-04-07), "Zombie PID Liveness" (2026-04-08), "Manager Role
  Identity" (2026-04-08), "Runtime Handle Authority" (2026-04-24),
  "Runtime-Less Status Snapshots" (2026-04-27), "External Supervisor Manager
  Liveness" (2026-05-04), "Status Reanimation Boundary" (2026-05-06), "Host PID
  Namespace Visibility" (2026-05-20), "Runner Timeout Boundary" (2026-05-27),
  "Producer And Channel Evidence Are Independent" (2026-08-04). Raw entries:
  `git show 396c3b85:docs/lessons.md`.
- 2026-10-15 — distilled 6 cold manager bootstrap, singleton convergence, and
  ownership-fence sections into Golden Rule 15 (and `03-Manager_Architecture.md`
  `[MA-1]`, `[MA-3]`; `05-Message_Flow_and_State.md` `[MF-6]`, `[MF-7]`):
  "Manager Startup Races" (2026-04-07), "Manager Bootstrap Unification"
  (2026-04-09), "Manager Lifecycle Command Consolidation" (2026-04-09),
  "Detached Bootstrap Proof" (2026-04-13), "Review-Finding Boundary Fixes"
  (2026-04-16), "Canonical Ownership Fences" (2026-04-17). Raw entries:
  `git show 396c3b85:docs/lessons.md`.
- 2026-10-15 — distilled 5 cold result-materialization, completion-grace, and
  client-follow sections into Golden Rule 16 (and `05-Message_Flow_and_State.md`
  `[MF-3]`, `[MF-5]`; `runtime-and-context-patterns.md` §8): "Interactive
  Contract" (2026-04-08), "Completion Grace" (2026-04-09), "Drain And Streaming
  Timing Boundaries" (2026-04-19), "Client Follow Boundaries" (2026-04-21),
  "Terminal Proof Grace" (2026-05-27). Raw entries:
  `git show 396c3b85:docs/lessons.md`.
- 2026-10-15 — distilled 5 cold TaskMonitor cleanup-policy, batch-hydration,
  and verified-deletion sections into Golden Rule 17 (and
  `05-Message_Flow_and_State.md` `[MF-5]`; `07-System_Invariants.md`
  `[OBS.13]`): "Prune Path Ownership" (2026-05-09), "Cleanup Markers"
  (2026-05-20), "Dead-TID Cleanup Policy" (2026-05-22), "Runtime Cleanup
  Slices" (2026-05-23), "Cleanup Progress Boundaries" (2026-05-30). Raw
  entries: `git show 396c3b85:docs/lessons.md`.
- 2026-10-15 — distilled 11 cold test-tooling, Ruff policy, boundary-straddling,
  migration-fixture, and harness-isolation sections into Golden Rule 18 (and
  `engineering-principles.md` §4, §4.1; `testing-patterns.md` Rules 7–9,
  Patterns 1, 10, 12): "Local Test Tooling" (2026-04-12), "Result
  Materialization And Synthetic PIDs" (2026-04-20), "Exact Policy Fixtures Are
  Part Of The Atomic Registry" (2026-08-05), "Ruff Selection Probes Must
  Preserve The Rest Of The Config" (2026-08-06), "RUF100 Must See The Full
  Candidate Rule Set" (2026-08-06), "Postgres Test Isolation Does Not Eliminate
  Host Contention" (2026-08-10), "Manual Reactor Test Driver Boundary"
  (2026-08-11), "Boundary Tests Must Straddle The Repaired Guard" (2026-08-13),
  "Migration Fixtures Must Come From Release Lineage" (2026-08-25),
  "Load-Sensitive Harness Ownership" (2026-09-09), "Test Typing and Oracle
  Audit" (2026-09-11). Raw entries: `git show 396c3b85:docs/lessons.md`.

## 2026-07-02 Verification-Lessons Fold

- 2026-07-02: Verification-lessons fold synced from agent-guidance (2026-07-02
  working tree; pinned 2026-07-14: that fold landed as agent-guidance
  `5927481`, and the 2026-07-14 wave adopted by
  `docs/plans/2026-07-14-agent-guidance-propagation-plan.md` is
  agent-guidance `2f7eff6`). Source
  incident record: the backstitch repo's `docs/lessons.md`. Note: weft is
  backstitch's external test corpus — the four baselined traceability-debt
  errors in backstitch's Weft gate are weft-side debt to burn down.

## 2026-05-20 Manager Reuse Replacement Drain

- Manager replacement has two separate duties: stop the superseded manager from
  accepting new public work, and preserve already launched user work. A
  `superseded` owner row must start a non-stopping drain when children exist;
  reusing the normal STOP drain can cancel user tasks during concurrent
  bootstrap.

## 2026-04-04 PG Parity

- When a task process exits, close every cached queue handle, not just
  "owned" auxiliary queues. Under SQLite this can look harmless; under
  Postgres it leaks connection-pool threads and prevents the process from
  exiting cleanly. See [weft/core/tasks/base.py](../weft/core/tasks/base.py)
  and [tests/commands/test_task_commands.py](../tests/commands/test_task_commands.py).
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
  launch work for that turn. Revised 2026-10-28: bounded serial batching may
  reopen one PostgreSQL delivery pass only after a new submission in the prior
  ordinary pass has settled and a bounded control drain proves empty. Close each
  pass before seeding, including failed attempts; never wait for a receipt to
  fill the batch. This preserves the control-order reason for the original rule:
  a fast launch worker must not bypass pending STOP. Initial receipts do not
  qualify the extra-pass exception.
- Revised 2026-09-19: Manager-to-manager liveness probes are non-blocking
  reactor state. A stale-looking registry row with a keyed PING in flight stays
  unknown until a matching PONG or the next ordinary leadership evaluation; a
  PONG-specific deadline is unnecessary once the reply is routed through the
  Manager's watched `ctrl_in`.
- Revised 2026-09-19: service-owner PING fallback follows the same rule. A
  `ping_pending` candidate stays uncertain until a matching PONG or the next
  ordinary active-service evaluation. The active-service cadence owns absence;
  the probe does not add another timer.
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
- October 9 qualification of the preceding May 10 cursor lesson: supported
  SimpleBroker 8.5.1 / simplebroker-pg 4.5.1 generated writes serialize timestamp
  allocation, insertion, and commit (SQLite write transaction; PostgreSQL
  singleton timestamp-row lock). An advancing raw-row cursor is valid for that
  live runtime publication path. Exact-ID imports and ID-preserving moves can
  still backfill behind a cursor. Start a fresh observation after such an
  administrative change. The historical production dependency version was not
  established; do not reinterpret the May incident as disproved. Source owners:
  `simplebroker/db.py` generated-write transaction and
  `simplebroker_pg/plugin.py` timestamp allocation. See [MF-5] and
  [the result observation plan](plans/2026-10-09-burst-result-observation-plan.md).
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
  `2026-06-10-self-healing-runtime-maintenance-plan` (retired — source 396c3b85; see `docs/plans/README.md`).
- A completion marker whose writer can be satisfied vacuously will eventually
  lie. Deletion markers such as `raw_deleted_at_ns` must be coupled to
  verified per-row deletion in the same pass (the exact-row apply layer
  re-verifies per ID on batch under-deletion), and audit-mode recovery paths
  must carry the same summary gate as the main deletion path.

## 2026-06-11 Return With Falsified Premises

- While writing a plan to scale monitor cleanup budgets, successive
  code-grounding checks falsified the diagnosis the plan was scoped on
  (the deferral was a routing bucket, not a budget; the "stalled backlog"
  was service-family event streams plus a false-positive body-substring
  oracle) and surfaced a different real question entirely. The author
  stopped and returned with the evidence and a corrected scope instead of
  delivering the agreed-but-wrong plan.
- Durable rule, owner-endorsed: returning mid-task with falsified premises,
  new issues, or reasonable uncertainty is the correct outcome, not a
  failure. Bring what was attempted, each falsification with evidence, the
  corrected picture, and a proposed corrected scope — then wait for
  direction. The fresh-eyes clause in
  `docs/agent-context/runbooks/writing-plans.md` ("stop and report instead
  of quietly rewriting") is a success path; treat plan-writing as an
  experiment that may invalidate its own premise.
- Corollary for diagnostics: never match TIDs by body substring over
  `weft.log.tasks` — service event bodies embed other tasks' TIDs and
  produce false positives. Join through the Monitor store refs
  (`weft_monitor_task_messages`) or parse the JSON `tid` field exactly.

## 2026-08-26 Admission Belongs Before Reservation

- A capacity check is simplest when denial leaves the spawn row in its source
  queue. Checking before reservation avoids a new permit lifecycle, does not
  alter reserved-row recovery, and lets the existing queue remain the durable
  backlog.
- A retained row is real queue activity, but it must not wake a blocked Manager
  continuously. Keep the source visible, suppress it from ordinary wait
  activity until a bounded retry deadline, and let child or launch-worker
  progress make that retry immediately due. The deadline applies to every
  denial and observation failure; event-driven early wakes are an optimization,
  not the only recovery path. Control, cleanup, reconciliation, and shutdown
  remain independent of dispatch capacity.
- Keep the usage model singular (revised 2026-10-14). Follow
  [MA-1.8](specifications/03-Manager_Architecture.md): PostgreSQL uses coherent
  measured total, including external and unattributed pressure. Do not add
  prospective task budgets or an ordinary child lane. Queued work consumes no
  processes. Bound live finite branches, preserve a branch during delayed child
  submission, and descend within that branch so children do not queue behind all
  parents. Attribution does not establish death or refund capacity. SQLite
  retains its live/unknown TID union with local launches. Both are soft,
  non-atomic guards, with no permit service or declared future fanout.
- Separate startup feedback from launch acknowledgement. If initial snapshot
  publication fails, an existing durable Manager checkpoint can release the
  bounded startup gate. Retain the exact reservation until task-snapshot handoff
  or positive settlement. LivenessMonitor and TaskMonitor must preserve this
  custody before deletion. Cold adoption itself changes owned state and needs a
  new successor checkpoint before fresh work. The real missing-snapshot plus
  Manager-SIGKILL regression exposed that missing publication edge; see
  `tests/system/test_family_recovery.py` and the
  [family scheduling plan](plans/2026-10-14-measured-admission-family-scheduling.md).

## 2026-09-11 Cleanup Audit Boundary Corrections

- The realtime late-output fix retained only startup metadata. A subscription
  established before task metadata appeared could call the one-shot fallback
  for a persistent work-item result; an initial outbox-derived snapshot could
  do the same. The repair refreshes metadata and custom routes, suppresses
  unknown-type outbox completion, and tests both snapshot and later evidence.
- The Monitor retirement call removed as redundant covered a different branch:
  the surviving call required global FIFO catchup. Retirement of already proved
  families now runs separately from high-water-gated summary/disposition work.
- Owner clarification: configurable shared task inboxes are mechanically possible
  but not an explicitly supported contract. Task-level requeue remains removed;
  revisit it if shared inbox support is adopted. The manager spawn queue is the
  current supported exception.
- Reproductions, review and verification are recorded in the
  [audit regression fixes plan](plans/2026-09-11-audit-regression-fixes-plan.md).

## 2026-09-14 Inline Fixtures Must Not Own the Test Worker Process

- An inline Manager runs on a thread inside the pytest worker, not in the
  dedicated process used in production. Leaving process titles enabled let its
  deferred macOS GUI activation synchronously call LaunchServices while holding
  the worker's interpreter lock. Under the full process suite this blocked both
  manager progress and unrelated test code, producing clustered result and
  cleanup timeouts.
- Disable process-title ownership on inline harness managers. Keep the shipping
  default on spawned task processes and retain dedicated native process-title
  tests. More time or fewer workers would hide the ownership error without
  removing the shared native side effect.
- This opt-out applies only to infrastructure, not Managers or Consumers that
  are the subject under test. Xdist also imports stock setproctitle before
  loading conftest; on macOS, suppress its own optional titles at remote
  bootstrap rather than patching native modules or changing child environments.
- An inline manager's lifetime is its owned Thread, not pytest's PID. Two
  managers may share one live PID while only one driver survives. Use full TID
  ownership and actual thread lifetime for harness liveness; absence of local
  ownership is unknown, not live. Thread existence is not responsiveness proof.
  See the [xdist contention investigation](plans/2026-09-14-xdist-title-contention-plan.md).

## 2026-09-14 Connection Reuse Must Follow the Owner

- A persistent Queue recreated by every helper can still perform a fresh
  backend schema check per facade. Task helpers should borrow an existing
  owner's connection, not merely set `persistent=True` on new handles. Verify
  actual connection opens after warmup, fresh independent commits, rollback,
  and bounded cleanup. Connection lifetime must not extend transaction lifetime.
- Releasing a worker queue lease does not necessarily recycle its thread-local
  core while reactor leases keep the shared session alive. Use the public
  thread-local cleanup boundary before that worker exits; never recycle the
  reactor's connection from another thread.
- Patching `module.os.getpid` mutates the shared stdlib module. Real broker
  resources can then detect a false fork after the patch is restored, even
  during finalization. Isolate a run-ID test's module-local OS view instead of
  changing process identity for every dependency. See the
  [connection reuse repair](plans/2026-09-14-bounded-registry-connection-reuse-plan.md).

## 2026-09-16 Resource Lifetimes Need Distinct Owners

- A queue lease, a caller-thread broker cache, and a transaction are three
  different lifetimes. Closing a persistent queue does not prove the executing
  thread released its cached core, while holding a session does not justify a
  transaction across a wait or yield. Put one explicit session at the real
  operation or driver boundary, keep transactions short, close active
  iterators before session exit, and verify physical cleanup with a surviving
  sibling handle on both backends.
- Session-close refusal is thread-and-broker-key-wide, not handle-local. Its
  private upstream exception type is not a Weft contract. End all same-key
  operations on the owner thread before session exit and keep cleanup ordered
  so queue leases are attempted even if session close later refuses. A task's
  `CLOSED` state means finalization was attempted; it is not proof that every
  resource was released when cleanup recorded a failure.
- Async delivery does not transfer ownership of a synchronous broker iterator.
  Creation, every `next()`, and final `close()` must stay serialized on one
  owner thread. A disconnect deadline only bounds the event-loop caller; it
  does not prove a blocked worker stopped. Retain the cleanup future until it
  unwinds, and give exactly one boundary responsibility for propagating or
  logging cleanup failure. See the
  [explicit broker session lifetime plan](plans/2026-09-15-explicit-broker-session-lifetimes-plan.md).

## 2026-09-17 Reactor Waiting and Polling Policy

- The manager latency investigation found that BaseTask started SimpleBroker's
  adaptive strategy but manually waited through a different native-or-sleep
  path; synchronous PONG callers had a third scan/sleep loop. A shorter sleep
  improved latency while increasing full reactor turns, so it measured a
  different cost from cheap data-version polling. Follow the actual production
  wait path before choosing intervals. Single-thread ownership alone does not
  prove that wake sources share one policy.
- An apparently redundant periodic rescan may mask incomplete subscriptions or
  a dead native observer. Repair interest coverage, local interruption, deadline
  composition and failure fallback before removing that rescan.
- Keep task-reactor deadlines distinct from source-adapter and finalization
  bounds. A parent watcher may poll the OS and a bounded worker queue may use a
  timed operation without scheduling task policy turns. The source adapter must
  record authoritative local state before notifying the retained strategy. See
  the [watcher reactor restoration plan](plans/2026-09-17-watcher-reactor-restoration-plan.md).
- Do not preserve a polling fallback by relabeling it as a domain timeout after
  the missing event route is repaired. Manager PONG arrival is backend activity;
  unanswered-probe policy already has leadership and active-service reactor
  timers. Reuse those owning-policy turns for absence, and keep reply-driven or
  incidental work from resetting the timer or concluding another probe early.
  Measured queue-wakeup latency is diagnostic evidence, not a reason to create
  a response deadline. See the
  [event-routed Manager PONG plan](plans/2026-09-18-event-routed-manager-pong-plan.md).
- Classifying an input as a "local event" does not make anything publish it.
  Removing the task wait caps left interactive stdout, stderr and child exit
  with no publisher, and `54874c2f` restored a 50 ms cap to recover liveness
  before `e6023028` connected the real sources. Wait inputs have three
  observable classes but four origins: broker, in-process completion, an
  OS-level source, and the clock. Only the OS-level origin cannot notify for
  itself, so audit it separately: before deleting a cap, name the adapter for
  every pipe, PTY, process exit, console read or sentinel the cap was covering,
  and test quiet exit and inherited-descriptor cases through the production
  driver. The rule is recorded in
  [CC-2.2.1](specifications/01-Core_Components.md).

## 2026-10-06 Test Clocks Have Their Own Boundary

- Patching `manager_mod.time.monotonic` patched the shared stdlib module,
  freezing broker/driver clocks too and causing cleanup deadline failures.
  Replacing only Manager's `time` binding with a test namespace controlled
  the policy clock without changing those dependencies or adding a production
  seam. Restore real time before finalization; tests that pass deadlines to
  BaseTask must use compatible owner clocks. See the
  [AdmissionController plan](plans/2026-10-06-admission-controller-plan.md).

## 2026-10-07 Partial Test Cleanup and Retained Task History

- Two Manager launch/cleanup tests skipped `_cleanup_base_task_resources` on a
  background thread, then marked the Manager closed. Fixture cleanup could not
  retry that phase, and closing Queue facades did not release the retained
  BrokerSession. The test owner now completes the skipped phase on the
  constructing thread after the background thread exits. Closed-session
  assertions and real DB/WAL/SHM handle probes exposed the leak that Unix file
  deletion had hidden; the Windows harness's successive 30-second release and
  deletion waits match the approximately 61-second teardown signature. Exact
  Windows phase timing still needs a CI rerun.
- The autostart pipeline result helper drained global task history while
  observing progress. Without terminal evidence, harness cleanup sent KILL to
  eight exited tasks and exhausted two control waits per TID, taking 32 seconds
  locally. A non-destructive timestamp cursor now preserves original log rows
  and carries observation progress between restart waits. The fix retains the
  existing cleanup and control budgets. See `tests/core/test_manager.py`.

## 2026-10-09 Result Claims Must Match Actual Delivery

- A live-rendering flag does not mean every result row is delivered live.
  Ordinary pipeline values are buffered until completion; claiming them early
  loses them if observation times out. The shared collector must check each
  row's actual eligibility before claiming without task/batch proof. Keep
  undelivered values pending while eligible stream frames can reach the caller.
  Prove both plain and mixed surfaces through real producer publications and
  later retrieval of the exact retained value. See [MF-5](specifications/05-Message_Flow_and_State.md)
  and the [implementation repair](plans/2026-10-09-direct-result-observation-implementation-plan.md).

## 2026-10-12 Completion Routing Requires Every Observer to Preserve Proof

- Moving ordinary result observation to task-local completion makes control
  readers part of the proof lifetime. Task-stop previously consumed typed
  terminal envelopes, so a later Manager exit check could manufacture
  `wrapper_lost` after a successful cancellation. Audit every reader of the
  destination, retain valid completion facts for every owner of a shared
  route, and delete only ordinary replies or noise. A real blocked broker
  publication plus the actual stop command exposed the loss.
- Terminal activity and runtime mapping can trigger control actions too.
  Keep terminal activity cleanup local, publish completion after final output,
  then publish the terminal mapping. Interactive queued control, signals, and
  parent loss must share the existing finalizer. Testing only queued STOP/KILL
  missed early signal publication. See [MF-3](specifications/05-Message_Flow_and_State.md)
  and the [burst plan](plans/2026-10-12-burst-submission-complexity-plan.md).

## 2026-10-14 Reducing Contention Is a Theory-Possession Stop Condition

The urge to reduce contention to make tests pass is an immediate stop condition,
not a routine test-running choice. An automated failure under load is a
counterexample to the investigator's current explanatory model. Preserve the
original command, concurrency, load, and evidence; re-read the program theory;
then identify the causal flaw or false premise and its owner. It may lie in the
runtime, harness, test expectation, substrate, or assumptions about the
execution environment. Do not guess which, or dismiss it as "flaky" or
"pre-existing" without evidence.

A lower-load pass cannot explain or close the original failure. After the stop
and re-derivation, a declared falsifiable hypothesis may use a lower-load probe
as diagnostic evidence only. Verify the justified correction under the original
pressure and standard concurrency, including when the correction changes a
proved-wrong oracle. This applies to worker reductions and new serialization;
extra resources or longer waits are not substitutes for the explanation.

Owner correction, 2026-10-14, after an agent tried lowering PostgreSQL test
workers to reduce contention. This lesson does not diagnose that failure.
The operating rule is [Testing Strategy TS-0](specifications/08-Testing_Strategy.md),
with rationale in program theory [WT-7]/[ALT-WT-017] and the procedure in
[testing-patterns Pattern 1](agent-context/runbooks/testing-patterns.md).

## 2026-10-14 Observation Tests Must Own Publication and Pressure

A producer must publish independently of an observer entering its wait call.
Publishing only inside a patched wait makes setup latency look like a lost
notification. Suspend the public iterator at a real state event, publish from
the caller, then resume with the original deadline. The late-metadata test
exposed this circular protocol at 12 workers with a 0.5-second deadline.

A sampled external connection population is not a sustained pressure actor.
Other clients can close or open sessions after the sample. Own the held
population through its explicit release; a bounded progress proof also needs
an owned physical server so unrelated clients cannot violate its stated
envelope. Keep genuine labeled and unlabeled pressure, standard parallelism,
and the original deadlines. Prepare large pending backlogs before starting
fixture processes whose lifetime bounds the measured execution phase.

Positive startup publication and the absence fallback are separate events.
The fallback interval must not delay positive evidence. Keep a bounded notification-only interest in the existing reactor waiter.
Delivery routes stay fixed; observation interests may change. The reactor
reads publication cursors, validates evidence, and retains custody. A separate
broker-reading helper worker violates the worker boundary and duplicates the
wake arbiter. Register before reading and retain interest after rejected
publication; a retained unchanged row is not recurring work. See [MA-1.8](specifications/03-Manager_Architecture.md) and the
[measured admission plan](plans/2026-10-14-measured-admission-family-scheduling.md).

## 2026-10-15: Scheduling order must not become a second resource ceiling

The five-frontier setting survived a discarded lookahead-reservation design and
withheld the sixth eligible family while measured admission reported ample room.
Keep capacity and order separate: measured `C - R - U` decides whether a launch
fits, while child-first/RR/FIFO family selection decides which request to offer.
Ancestry checkpoints preserve lineage and custody, never concurrency permits.
On PostgreSQL, unset capacity must use the public backend maximum rather than
silently bypassing the gate. Refreshing C must preserve drain-wait history.

A gated workload that counts on a hidden concurrency cap is not an independent
progress proof. Use a complete finite input whose whole fixture topology fits
measured headroom; release all preparation and leaf gates without dispatch waves.
Waiting parents still retain resources, so arbitrary future descendants have no
unconditional progress guarantee. See the [plan](plans/2026-10-15-work-conserving-family-scheduling.md),
[MA-1.8](specifications/03-Manager_Architecture.md), and
`tests/system/test_measured_admission_pg.py`.

## 2026-10-15 Filesystem permissions belong to the operator

Weft's single-operator trust model does not grant it ownership of filesystem
access policy. Creating a project directory or artifact is not authority to
chmod existing paths or impose private creation modes. Use ordinary OS creation
policy, including for durable atomic-write staging; atomic replacement creates a
new inode and does not retain the previous inode's individual mode or ACL.
Report actual access failures through existing error boundaries. Do not add
permission probes, repairs, or a migration that widens previously restricted
paths. Owner correction and implementation: [permission plan](plans/2026-10-15-operator-owned-permissions.md),
[CTX.5](specifications/07-System_Invariants.md).

## 2026-10-28: Bound capture without discarding accepted output

A post-exit deadline applied to both pipe capture and callback delivery truncated
already-captured output under real Consumer backpressure. Distinguish the phases:
stop and join capture producers within their lifecycle bounds, then deliver the
finite accepted backlog before successful logical completion. Physical pipe EOF
and logical stream completion are separate facts. Verify the public result with
a representative large payload and a slow consumer; a fast callback or small
payload can miss the loss. See [RM-5.2](specifications/06-Resource_Management.md)
and the [runtime repair plan](plans/2026-10-27-runtime-and-test-repair-plan.md).

Consume a retry deadline at the owner when its retry is serviced, and re-arm it
only after a new failure. Leaving the fired retry armed can mask a later grace
deadline and create immediate-wake spinning. Fix the owner rather than filtering
the stale deadline in a second projection policy; preserve future retries and
custody until terminal proof commits ([MA-1.6a](specifications/03-Manager_Architecture.md)).

Test resource ownership starts at acquisition, before readiness assertions. Its
cleanup must remain effective when the method being tested is broken. Generated
fixture scripts also need dependency review: unused test-helper imports can load
the whole runtime before a tiny readiness handshake. Remove that work rather than
loosening the guard. A same-head passing CI rerun establishes intermittence, not
the cause of the original failure; preserve failure diagnostics and that limit.

A process-reaping assertion must not perform the reap itself. Retain the actual
Popen acquired at the handoff and check its cached return code before any oracle
poll or wait. A kill-plus-pipe-EOF control exposed a false green when the test
called poll: the observer supplied the missing ownership action. Independent
cleanup may poll and wait after the assertion.

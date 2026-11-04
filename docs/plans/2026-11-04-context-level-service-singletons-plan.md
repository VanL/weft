# Context-Level Service Singletons: Managers Ensure, Services Own Their Lifetime

Status: draft
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1] item 7, [MA-1.6a], [MA-3]; docs/specifications/05-Message_Flow_and_State.md [MF-3.2]; docs/specifications/07-System_Invariants.md [MANAGER.13], [MANAGER.15]
Superseded by: none

Class: 5 (normative spec text changes in [MA-1] item 7, [MA-3] and
[MF-3.2], plus theory edits in `docs/program-theory.md` [ALT-WT-023] and
[WT-2a.13], the latter a `RECONSIDERATION REQUESTED` on an identity
invariant with the owner's 2026-11-04 ruling as its disposition). The
owner accepts the theory wording before it lands as a separate `Theory:`
commit; the series lead lands the spec and code. Execution-path changes
on the Manager's drain, idle exit, late-launch reap and final cleanup, and
a new lifecycle rule inside `ServiceTask`. The hardening-plans checklist
applies in full and the independent review happens before
implementation. Not +P. Plan type: implementation with spec delta.
Promotion strategy: **B, atomic** (§4d): requirement text, link claims,
code and reciprocal backlinks land in one change, because every touched
paragraph describes code this plan changes in the same commit. One-way
doors: none (see Rollback).

## 0. Why this plan exists

On 2026-11-04 the first CI run of the November series failed on every
`cli` lane with `manager_convergence_timeout` in
`tests/cli/test_cli_run.py::test_parallel_manager_reuse_converges_to_single_manager_under_repeated_bootstrap`.
The evidence (run 41917203345, status payload in the assertion): two
Managers started under four parallel submits; the higher-TID Manager B
had already launched the heartbeat service, which became the registered
heartbeat owner; B was then superseded and entered the non-stopping
superseded drain ([ALT-WT-007]), which waits for `_child_processes` to
empty; the heartbeat is a standing service that never exits on its own
(plan `2026-11-02-task-monitor-heartbeat-clock-plan.md`, owner redirect
of 2026-11-03) and saw itself as the rightful owner; so B drained for
ever, and the canonical Manager A, seeing a live heartbeat owner, never
started its own. Locally the test passes in about 17 s because B is
superseded at bootstrap before its services launch.

The owner ruled the same day that this is drift, not a design to patch:

> That is what I intended with services - they are context-level
> singletons. That is why they use the same procedure to resolve which
> one is canonical. [...] If services have drifted to not be
> context-level singletons, that was an unintended drift. [...] The
> 'always on' in the theory means that the manager is not responsible
> for managing the lifetime. The manager is only responsible for
> ensuring that any ServiceTasks are running. [...] Heartbeat, Liveness,
> and Monitor can persist after the manager stop if they have work to do
> (as defined within the ServiceTask itself). They self-stop after 5
> minutes of not having any work.

## 1. Goal

Make the code match that intent:

1. **Managers ensure, never own.** Any Manager with the public spawn
   inbox ensures the heartbeat, TaskMonitor (by its flag) and
   LivenessMonitor (by its flag) exist, through the existing
   internal-spawn path and the existing registry-based convergence. A
   service process a Manager launched is tracked for exit evidence and
   reaping only. It is not a member of any drain, it does not hold its
   launcher alive, and no Manager stop, supersession, yield, idle exit,
   or final cleanup signals or terminates it. Three deliberate,
   pre-existing paths remain and are stated as such: SIGUSR1 terminates
   the whole child table; duplicate convergence
   (`_terminate_duplicate_service_candidates`) sends KILL to a
   non-canonical duplicate service; and a launch whose result lands after
   the Manager's cleanup deadline is reaped ([IMPL.10]), service or not,
   because the Manager can no longer register it and an unregistered
   service would be an undiscoverable duplicate (§10.5). None changes
   here. A service launch whose result lands during cleanup but before
   the deadline is committed detached and registered (A8).
2. **Services own their lifetime.** Each `ServiceTask` decides for
   itself what "work" is. A service with work runs on, whether or not a
   Manager is alive. A service with no work for
   `SERVICE_IDLE_EXIT_SECONDS` (300 s) ends itself on one idle clock of
   its own, exactly as the Manager idle-exits on its own clock. Checking
   for work is never itself work: an empty periodic cycle, a mapping
   refresh that finds nothing, or a registration peek that finds no row
   does not restart the idle clock. An explicit STOP addressed to the
   service (its `ctrl_in`) still stops it as for any task.
3. **Supersession needs no hand-over.** A superseded or yielding Manager
   stops taking intake, lets its user work finish, and exits. Its
   services are not its concern. [ALT-WT-007] stays as written.

The CI failure is then impossible by construction: nothing a superseded
Manager launched can keep it alive except user work.

The applicable model (round-1 reviewer's framing, adopted): quiescence
detection inside the existing reactor. Each service observes substantive
outstanding work it already tracks, keeps one idle deadline, and
revalidates before exiting.

## 2. Source Documents

- `docs/program-theory.md` [WT-2a.13] (its system-STOP sentence is the
  invariant delta), [ALT-WT-023] (Current consequence is the sentence
  being clarified), [ALT-WT-007], the "Ask who owns this lifetime" row of
  the [WT-4] table.
- `docs/specifications/03-Manager_Architecture.md` [MA-1] item 7
  (managed services), [MA-1.6a] (implementation mapping line), [MA-3]
  (drain phases).
- `docs/specifications/05-Message_Flow_and_State.md` [MF-3.2] (heartbeat
  service flow: "always running under the canonical Manager", "no deadline
  with an empty table").
- `docs/specifications/07-System_Invariants.md` [MANAGER.13],
  [MANAGER.15] ("manager-owned singleton service" wording).
- `docs/specifications/00-Quick_Reference.md` row for
  `weft.state.heartbeats`.
- Plans: `2026-11-02-system-stop-ordering-plan.md` (phase 2 of the STOP
  drain, which this plan removes), `2026-11-04-system-stop-snapshot-targets-plan.md`
  (its broadcast is not reused for services, since Managers no longer
  stop services), `2026-11-02-task-monitor-heartbeat-clock-plan.md`
  (always-on heartbeat; its "no idle exit" sentence and test are replaced
  by the five-minute rule).
- Lessons: `docs/lessons.md` `2026-05-20` (the stopping drain cancelled
  user work during concurrent bootstrap; phase 1 stays non-stopping for
  superseded drains).

## 3. Context and Key Files

### Current structure (at `6a3dce18`; re-verify every line before editing)

Manager side, `weft/core/manager.py`:

- `_desired_internal_service_keys` (8226): the canonical Manager wants the
  heartbeat plus the flagged monitors; a scoped Manager wants none.
  Unchanged.
- `_reconcile_managed_services` (7600) and `_service_supervision_allowed`
  (6378, "not draining and not should_stop"): launch through
  `weft.spawn.internal`, evidence from registry rows, task log, tracked
  children and PING/PONG probes, reduced by
  `weft/core/manager_services.py::reduce_managed_service_state`. Unchanged
  in behavior. The launch commit path is the only place a service enters
  `_child_processes` (1519 `internal_role=` into
  `_ManagerChildLaunchRequest`; `_commit_child_launch_success` 1725,
  `ManagedChild(... internal_role=...)` at 1739).
- `_run_child_launch_service_worker` (1620): a launch whose result
  arrives while `_active_cleanup_deadline` is set, or after the worker's
  stop request, is terminated (`terminate_process_tree`, then
  `process.kill()`) before it reaches the child table. **Defect site
  three** (round-1 F1): a late service launch is killed here.
- `_child_is_supervision_only` (2210) and `_user_work_children` (2221):
  services are already excluded from user work.
- `_drain_phase_children` (2230): phase 1 = user work; phase 2 =
  supervision-only children. `_drain_phase_members` adds the snapshot
  STOP targets in phase 1.
- `_continue_shutdown_drain` (9165): the non-stopping branch
  (`not self._drain_stops_children`, superseded and leadership-yield
  drains) finishes only when `_child_processes` is empty. **Defect site
  one.** The stopping branch runs phase 2 against services and
  force-reaps them at the window.
- `_drain_user_work_phase_holds` (9200): ends phase 1 and starts phase 2
  with a fresh window.
- `_begin_superseded_shutdown` (5329) and `_begin_leadership_drain`
  (5296): enter the non-stopping drain when `_user_work_children()` is
  non-empty; otherwise exit at once.
- Idle exit (8928-8960): `should_stop = True` once idle; final cleanup
  then runs.
- `_cleanup_task_resources` (8553): terminates user work, then
  "everything still tracked, services included", unregisters, closes the
  sentinel adapter. **Defect site two**: an idle or superseded exit kills
  the context's services. The sentinel adapter's `close()` wakes and
  stops the observer thread; it does not terminate children, so a
  detached service keeps a valid handle through observer shutdown.
- `handle_termination_signal` (5374): SIGUSR1 terminates the whole child
  table immediately (unchanged; stated in §1).
- `_terminate_duplicate_service_candidates` (6685): duplicate
  convergence by registry, KILL plus scoped host-PID termination of a
  non-canonical duplicate; unchanged; stated in §1.

Service side:

- `weft/core/tasks/service.py::ServiceTask`: `next_wait_timeout` (193)
  returns `None` (no poll timer). There is no shared per-turn hook:
  `BaseTask.process_once` (base.py 1423) is final and each service
  overrides `_process_reactor_turn`. Task 1 adds the hook.
- `weft/core/tasks/heartbeat.py`: `next_wait_timeout` (693) "with an
  empty table there is no timer"; `_service_ownership` (630) resolves
  the heartbeat endpoint and `_exit_if_superseded` (681) exits a losing
  duplicate; `_registrations`, `_registrations_rebuild_pending` and the
  pending worker results are the work state.
- `weft/core/monitor/task_monitor.py`: `next_wait_timeout` (4760) on a
  cycle grid anchored at the first cycle start (`_run_monitor_cycle`,
  5524); `_RetainedTaskLogIngestResult` (318) and
  `_PreCheckpointTaskLogRecoveryResult` (408) are the per-cycle counts
  (`valid_ingested`, `raw_deleted`, `malformed_deleted`,
  `monitor_store_families_retired`, ...); `_deferred_task_log_pending`
  (789, 1015, 5283) is the cached deferred-write backlog; the catch-up
  deadline cap lives near 5712. TaskMonitor deliberately does not watch
  `weft.log.tasks`; raw backlog is cycle-sampled.
- `weft/core/tasks/liveness_monitor.py`: `_latest_rows` (118, every
  task-state mapping the context holds, including the Manager's, the
  other services' and its own), `_in_flight` (120), `_schedule_due_probes`
  (311, pops a heap entry before recording `_in_flight`),
  `next_wait_timeout` (592) on the due heap and pending results.
- `weft/core/launcher.py::launch_task_process` (205): spawn-context
  `Process(daemon=False)`; `_task_process_entry` hard-exits with
  `os._exit(0)` only when `os.name != "nt"` (163). See §6b.

Status and harness:

- `weft/commands/system.py::_collect_internal_service_snapshots` reports
  a service's `manager_tid` from the registry record's
  `metadata.manager_tid` (686) or a Manager `task_spawned` row (651).
  Record shape is unchanged by this plan; the field is provenance (which
  Manager launched it), not ownership.
- `tests/helpers/weft_harness.py`: `_stop_active_managers` (1162) sends
  Manager STOP, forces `stop_manager`, waits for registered PIDs, then
  `_drain_registry_queue` (1292); `_cleanup_preserving_database` (1225)
  raises when anything survives Manager STOP, sending task STOP only
  after a 2 s lingering window; `_cleanup_candidate_task_tids` (1024) and
  `_terminate_registered_pids` (1320) already treat service TIDs as tasks
  to stop or kill (today a fallback; after Task 5 the primary path).

### Files to modify

- `weft/_constants.py` (one constant).
- `weft/core/tasks/service.py` (idle clock, `_has_work` hook, the shared
  turn hook, `service_idle_exit` event).
- `weft/core/tasks/heartbeat.py`, `weft/core/monitor/task_monitor.py`,
  `weft/core/tasks/liveness_monitor.py` (each defines its work and
  renames its reactor-turn override).
- `weft/core/manager.py` (drain, idle exit, late-launch reap, final
  cleanup; phase 2 removed; detach with `multiprocessing` children-set
  removal).
- `weft/commands/system.py` docstring only (Task 0 confirms no phase-2
  read).
- `tests/helpers/weft_harness.py` (cleanup stops context services).
- Tests listed in §6 and §6a.
- `docs/specifications/03-Manager_Architecture.md`,
  `05-Message_Flow_and_State.md`, `07-System_Invariants.md`,
  `00-Quick_Reference.md`, `docs/program-theory.md` (owner-accepted
  wording, separate commit), `CHANGELOG.md`, `docs/plans/README.md`.

### Read first

`docs/program-theory.md` [WT-0], [WT-2a.13], [WT-4] 4.4, [ALT-WT-007],
[ALT-WT-023]; spec [MA-1] item 7, [MA-3] in full; [MF-3.2]; this plan's
§4 and §4c; `docs/agent-context/runbooks/hardening-plans.md` §1, §4, §11.

### Comprehension questions (answer in the Execution log before the first edit)

1. Why does the superseded drain wait on `_child_processes` and not on
   `_user_work_children()` today, and what changed on 2026-11-03 that
   turned that wait into a deadlock?
2. Which Manager paths may still terminate a service after this plan?
   (Answer: SIGUSR1's whole-table terminate and duplicate convergence.
   Idle exit, STOP drain, supersession, yield, late-launch reap and final
   cleanup may not.)
3. Where does a service learn that it has no work, which single state
   does the idle rule add, and why must an empty cycle not reset it?
4. What stops the services a test harness leaves behind, and on which
   cleanup paths?
5. Why would a Windows Manager hang on exit without the children-set
   removal, and what proves the fix?

## 4. Invariants and Constraints

- **[WT-2a.13]** Independent lifetimes; death needs proof. A service's
  lifetime is its own. A Manager's launch of a service is a fact about
  provenance, not custody. The invariant's system-STOP sentence changes
  (§4c, `RECONSIDERATION REQUESTED`, disposition: owner ruling of
  2026-11-04 quoted in §0).
- **[ALT-WT-007]** A superseded drain is non-stopping for user work and
  one-way. This plan does not reopen it; it removes the only thing that
  could keep that drain open indefinitely.
- **[ALT-WT-023]** as clarified by the owner on 2026-11-04: "always on"
  means no Manager manages a service's lifetime; a Manager only ensures
  ServiceTasks are running. Services idle when not needed and end
  themselves after five minutes without work.
- **[MA-1] item 7** Atomic reservation remains the dispatch authority;
  the service registry remains the convergence authority; duplicate
  services still converge to one live owner per key through the existing
  reducer. Nothing in this plan changes who is canonical.
- **[WT-4] 4.4 no over-armoring.** The idle rule is one deadline and
  one predicate per service, evaluated from state the service already
  keeps. No service watches Managers, no Manager watches services, no
  new control message, no new registry row, no new scan.
- **No compatibility shims** ([ALT-WT-012]). The removed phase 2 is not
  kept behind a flag. The `PONG` and status shapes do not change.
- **House rules.** No new deferred imports; no new sleeps; monotonic
  clocks for durations; `Spec:` references on every touched function;
  every enumerable contract element gets a firing test.

### 4a. Observable-difference register

| # | Difference | Kind | Disposition |
|---|---|---|---|
| A1 | After `weft manager stop` (and SIGTERM/SIGINT/parent loss), the heartbeat, TaskMonitor and LivenessMonitor keep running while they have work, and otherwise exit within 300 s on their own. Before: stopped in phase 2 within the drain window. | Changed timeliness; no lost capability | Owner rule 2026-11-04 |
| A2 | After a Manager idle exit, services keep running up to 300 s with no work. Before: terminated by final cleanup. | Changed timeliness | Owner rule 2026-11-04 |
| A3 | A superseded Manager with no user work exits at once even when it launched services. Before: drained for ever when it had launched the heartbeat (the CI defect). | Defect removed | This plan |
| A4 | Worst-case `weft manager stop` drain shrinks from about 34 s to about 17 s (one window plus one forced-reap deadline). | Improvement | [MA-3] text updated |
| A5 | Under a long-lived idle Manager, the heartbeat (no registrations) and TaskMonitor (no substantive cycle result) exit after 300 s and are relaunched by the Manager's ensure on its next reconcile. Whether LivenessMonitor joins that cycle depends on §10.4: with every mapping counting as work it never idles while the Manager and the other services hold mappings. | New churn | **Owner decision §10.1** |
| A6 | `weft status` service `manager_tid` now means "launched by", and may name a Manager that has since exited. | Meaning narrowed, shape unchanged | Documented in [MA-1] item 7 |
| A7 | The test harness stops a context's services itself at cleanup, on both cleanup paths. | Test-only | Task 5 |
| A8 | A service launch whose result lands during Manager cleanup is committed detached instead of killed. User-work late launches are still reaped. | Defect removed (round-1 F1) | Task 3 |

### 4b. Rollout, rollback, one-way doors

Rollback is `git revert` of the implementation commit and the theory
commit together; no persisted shape changes, no migrations, no new
queues. A service built from the previous version under a new Manager,
or the reverse, interoperates: the registry contract and control
messages are unchanged. One-way doors: none.

### 4c. Proposed Spec and Theory Delta (strategy B, atomic)

Baseline is the text at `6a3dce18`. Each block replaces the quoted
baseline sentences verbatim; the implementer applies them in the
implementation commit and the theory blocks in the separate `Theory:`
commit after owner acceptance.

**[MA-1] item 7** (`docs/specifications/03-Manager_Architecture.md`).
Baseline sentences: "The canonical manager runs the built-in heartbeat
service as a standing internal service, whatever the monitor flags say:
started at manager startup, relaunched by ordinary managed-service
convergence if it exits, and stopped like the other internal services."
and "The manager enqueues services through its own inbox using
manager-owned internal runtime envelopes, tracks these children as
supervision rather than user work, and applies bounded restart backoff
after TaskMonitor exit."

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```text
   Internal services are context-level singletons. The canonical manager
   ensures the built-in heartbeat service exists, whatever the monitor
   flags say: launched at manager startup if no live owner exists,
   relaunched by ordinary managed-service convergence if it exits, and
   never stopped, signalled or terminated by any manager stop,
   supersession, leadership yield, idle exit, late-launch reap or final
   cleanup. The same holds for TaskMonitor and LivenessMonitor when their
   flags make them desired. A manager terminates a service on exactly two
   paths: SIGUSR1 terminates its whole child table, and duplicate
   convergence sends KILL to a non-canonical duplicate owner. Each service
   defines its own work and ends itself after `SERVICE_IDLE_EXIT_SECONDS`
   without work ([MF-3.2]); checking for work is not work. An explicit
   STOP on a service's `ctrl_in` stops it as for any task.
```

```text
   The manager enqueues services through its own inbox using
   manager-owned internal runtime envelopes, tracks a launched service
   for exit evidence and reaping only (its `manager_tid` on the registry
   row and in status is provenance, not custody), and applies bounded
   restart backoff after TaskMonitor exit.
```

**[MA-3]** (`docs/specifications/03-Manager_Architecture.md`). Baseline:
the sentence "That drain is a system stop: the Manager stops active user
work first, then its internal services, then itself.", the phase-1
sentence "Internal services keep running, unsignalled, throughout this
phase.", the whole of item 2 ("Internal services. Once phase 1 ends ...
The drain does not return to phase 1."), item 3's opening "Once the
tracked child table is empty, apart from any child committed by a launch
released at the phase-1 window expiry," and the sentence "Services are
not restarted during the drain."

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```text
That drain is a system stop: the Manager stops active user work, then
itself. Internal services are context-level singletons and are not drain
members ([MA-1] item 7).
```

```text
   Internal services keep running, unsignalled, throughout the drain and
   after it.
```

```text
2. The Manager. Once no tracked user-work child remains, apart from any
   child committed by a launch released at the window expiry, the Manager
   detaches any service it launched (the process keeps running; the
   Manager only stops tracking it), publishes its drained terminal event
   and exits.
```

Former item 3's tail follows as the tail of item 2 with two edits:
"phase-1 window expiry" becomes "window expiry" throughout, and its last
sentences become:

```text
   That event is the last lifecycle event the Manager publishes for the
   stop, with two bounded exceptions: a child committed by a launch
   released at the window expiry is terminated by the Manager's final
   cleanup, which runs after the drained event, so that child's own
   lifecycle events may follow it; and a detached internal service
   publishes its own lifecycle events on its own clock, before or after
   the Manager's. No other child's event does.
```

Baseline sentinel-failure sentence: "cleanup on that path attempts
user-work termination before internal-service termination under one
absolute cleanup deadline, then raises the original failure."

```text
cleanup on that path attempts user-work termination under one absolute
cleanup deadline, detaches any internal service it launched, then raises
the original failure.
```

Baseline: "Caller-facing manager stop defaults must be materially longer
than the sum of the two phase windows, because a Manager may use both
drain budgets before publishing its stopped registry record,"

```text
Caller-facing manager stop defaults must be materially longer than the
drain window plus its forced-reap deadline, because a Manager may use
that whole budget before publishing its stopped registry record,
```

```text
A phase with no members completes at once. A draining Manager starts no
service; a service that exits during the drain is relaunched by whichever
live Manager keeps it desired.
```

**[MF-3.2]** (`docs/specifications/05-Message_Flow_and_State.md`).
Baseline: the flow line "the heartbeat service, always running under the
canonical Manager, peeks weft.state.heartbeats", the bullet beginning
"the canonical manager runs the heartbeat service as a standing internal
service through the manager-owned internal service path ([MA-1] item 7),
whether or not any row exists;", the parenthetical "(no deadline with an
empty table)", and the bullet "the service does not exit on an empty
table. With an empty table it publishes no deadline and blocks on its
watcher (its `ctrl_in` lane and its notification interest in
`weft.state.heartbeats`); with rows its only deadlines are the next due
emission and the earliest lease expiry." (round-2 F6).

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
the heartbeat service, a context-level singleton any canonical Manager ensures, peeks weft.state.heartbeats -> in-memory due heap -> ordinary queue write to destination
```

```text
- the canonical manager ensures the heartbeat service exists through the
  manager-owned internal service path ([MA-1] item 7), whether or not any
  row exists, and stops it on no drain or exit (the termination
  exceptions of [MA-1] item 7 apply); the manager neither reads nor
  deletes the queue. The heartbeat service is the queue's only reader and
  deleter.
  It has work while its table holds a row; with no row it keeps one idle
  clock and ends itself after `SERVICE_IDLE_EXIT_SECONDS` without a row,
  Manager or no Manager. A row written while no heartbeat service runs
  waits in the queue until a Manager ensures one.
```

```text
(with an empty table the only deadline is the idle clock)
```

```text
- the service ends itself after `SERVICE_IDLE_EXIT_SECONDS` with an empty
  table. With an empty table its only deadline is that idle clock and it
  otherwise blocks on its watcher (its `ctrl_in` lane and its
  notification interest in `weft.state.heartbeats`); with rows its
  deadlines are the next due emission and the earliest lease expiry, and
  the idle clock is not armed.
```

**[MANAGER.13] and [MANAGER.15]**
(`docs/specifications/07-System_Invariants.md`): "manager-owned singleton
service correctness" becomes "manager-ensured singleton service
correctness"; "manager-owned singleton services reduce" becomes
"manager-ensured singleton services reduce". No other change.

**`00-Quick_Reference.md`** row for `weft.state.heartbeats`: "the
heartbeat service, a standing internal service of the canonical manager,
is its only reader and deleter" becomes "the heartbeat service, a
context-level singleton any canonical manager ensures, is its only reader
and deleter".

**Implementation mapping.** [MA-1.6a] (line 585): add
`ServiceTask._has_work`, `ServiceTask._work_undetermined`,
`ServiceTask._settle_idle_clock`, `ServiceTask.next_wait_timeout` (idle
clause), the three `_has_work` overrides,
`Manager._detach_launched_services`. "System STOP phase ordering" (line
945 onward): `_drain_user_work_phase_holds` loses "the fresh phase-2
window, and the drained event once no service remains" and gains "the
drained event once no user-work child remains";
`Manager._drain_phase_children` becomes "the drain's members, from the
one snapshot pass plus this Manager's user-work children" (round-2 F6).
[IMPL.10] (07, line 941) is unchanged: the after-deadline launch kill
stays for every role (§10.5).

**Theory [ALT-WT-023]** (`docs/program-theory.md`), class 5, owner
accepts. Baseline: the Current consequence bullet "internal services are
always on under the canonical Manager and idle when not needed: no timer
and no poll with nothing to do, blocking on the watcher."

Replacement (`docs/program-theory.md`):

```text
- Current consequence: internal services are always on in the sense that
  no Manager manages their lifetime: a Manager only ensures that each
  ServiceTask is running, through the internal spawn path and the registry
  reducer, and services are context-level singletons converged by the
  same procedure that picks the canonical Manager. A service idles when
  not needed, with no timer and no poll beyond one idle clock, and ends
  itself after five minutes without work as it defines work; it runs on,
  Manager or no Manager, while it has work (owner, 2026-11-04).
```

**Theory [WT-2a.13]** (`docs/program-theory.md`), `RECONSIDERATION
REQUESTED`; disposition: the owner's ruling of 2026-11-04 ("Heartbeat,
Liveness, and Monitor can persist after the manager stop if they have
work to do"). Baseline: "A system STOP is different: it is the Manager's
responsibility to stop, suspend, or kill active tasks, then its internal
services, then itself (owner, 2026-11-02)."

Replacement (`docs/program-theory.md`):

```text
  A system STOP is different: it is the Manager's responsibility to stop,
  suspend, or kill active tasks, then itself (owner, 2026-11-02); internal
  services are context-level singletons with lifetimes of their own and
  outlive any Manager stop while they have work (owner, 2026-11-04).
```

Audit table: the Service registry row's owner column gains "services own
their lifetime".

## Deviation Log

None yet.

## Spec Baseline

Spec and theory text as of `6a3dce18` for every section named in §4c.

## 5. Tasks

### Task 0. Read, answer, verify

Read the "Read first" list, answer the comprehension questions in the
Execution log, re-verify every line number in §3, and record the
`git rev-parse HEAD` the work starts from. Confirm in the Execution log
that `weft/commands/system.py` reads nothing phase 2 produced. Find why
`_task_process_entry`'s hard exit is POSIX-only (commits `8dadd6f3` and
`65af58b8` introduce and restore it without stating the failure); if a
stated reason exists, record it; the plan does not extend the hard exit
to Windows either way.

### Task 1. The service idle clock (`ServiceTask`)

Add `SERVICE_IDLE_EXIT_SECONDS: Final[float] = 300.0` to
`weft/_constants.py` with a docstring stating the rule and the owner
date.

In `weft/core/tasks/service.py`:

- `_has_work(self) -> bool`: the hook each built-in overrides. Default
  `True`: a service that does not define its work never idle-exits.
- `_process_reactor_turn` becomes the shared template:
  `self._service_reactor_turn()` (each built-in renames its current
  override to this), then `self._settle_idle_clock()`. So controls,
  worker results and the service's own work reconcile first, and the idle
  check runs last in every turn, after the turn's own work has updated
  the state `_has_work` reads.
- `_settle_idle_clock`: one field, `_idle_since_monotonic_ns: int | None`,
  and a second hook, `_work_undetermined(self) -> bool`, default
  `False`, true while a service has started an observation whose result
  it has not yet applied (TaskMonitor's in-flight builtin cycle). Order:
  if `should_stop` or the taskspec is terminal (a STOP handled earlier in
  the same turn), do nothing (round-2 F8: `mark_completed` after a
  cancel is a rejected transition). Else if `_has_work()`: clear the
  clock. Else if `_work_undetermined()`: leave the clock exactly as it is
  and do not exit (an observation in progress neither resets nor
  satisfies the idle window; round-2 F2). Else if the clock is `None`:
  set it to now. Else if `now - _idle_since >= SERVICE_IDLE_EXIT_SECONDS`:
  mark the taskspec completed with return code 0, report
  `service_idle_exit` with the idle duration, set the process title to
  `completed`, set `should_stop` (mirrors `heartbeat.py:681`
  `_exit_if_superseded` in shape).
- `next_wait_timeout`: with the clock set and work determined,
  contribute the remainder of the window (merged by the subclass's
  existing `min`); with work, or while undetermined, no idle timeout (the
  pending result wakes the reactor; an expired window never spins). The
  deadline-driven turn re-evaluates `_has_work()` and
  `_work_undetermined()` before exiting (revalidate before exit).
- Docstrings cite [MA-1] item 7 and [MF-3.2].

Firing tests (`tests/tasks/test_service_task.py`, or the module that
holds `ServiceTask` tests today): (a) with work, `next_wait_timeout`
carries no idle deadline; (b) without work, it returns the remainder and
the task exits with `service_idle_exit` once the window elapses (freeze
`monotonic_ns` through the service module's clock seam); (c) work
returning mid-window clears the clock and no exit happens; (d) a STOP
during the idle window stops the task normally; (e) a turn that finds no
work does not move an already-set clock (the empty-cycle rule, with a
subclass double whose turn runs an "empty cycle"); (f) an undetermined
turn at an expired window neither exits nor moves the clock, the next
turn with a substantive result clears it, and the next turn with an
empty result exits (held asynchronous result); (g) a STOP handled in the
turn that reaches the window expiry stops the task with no
`mark_completed` attempt.

### Task 2. Each service defines its work

- **Heartbeat** (`weft/core/tasks/heartbeat.py`): work = any row in
  `_registrations`, or pending worker results, or a registrations rebuild
  pending. Replace the "no timer with an empty table" sentence in
  `next_wait_timeout`'s docstring; the base class now supplies the idle
  timer. The rebuild that finds no row is not work.
- **TaskMonitor** (`weft/core/monitor/task_monitor.py`): work is
  cycle-sampled, from the results the cycle already records: the last
  completed cycle ingested or deleted raw rows, recovered pre-checkpoint
  rows, retired families, or flushed deferred writes (any non-zero
  `valid_ingested`, `raw_deleted`, `malformed_deleted`,
  `monitor_store_families_retired` from `_collation_state`, recovery
  `valid_ingested`, a deferred-write flush observed through the existing
  flush-status transition), or `_deferred_task_log_pending > 0`, or a
  catch-up backlog is flagged, or the applied cycle reports retained work
  awaiting a future action (owner, 2026-11-04: "if there is cleanup at
  the next expiration date that the Monitor would handle, it stays
  active ... until everything has timed out and been deleted"). That
  flag, `retained_work_pending`, is computed by the cycle worker from
  what the cycle already enumerates and returned in its diagnostics: any
  collation family in the store not yet retired or disposed (families
  awaiting a lifetime report included), `last_control_cleanup_pending`,
  `last_reserved_skipped_not_ready > 0` (a retention window still
  running), pending deferred writes, scan candidates seen but not yet
  safe to delete, or runtime prune candidates not yet deleted. The one
  store read it adds is the open-family presence check, run inside the
  cycle worker beside the family retirement it already performs, never
  from the reactor. The Monitor therefore outlives its Manager for as
  long as retained state has a future expiry, absent a STOP, and exits
  five minutes after the cycle that finds nothing left. `_work_undetermined` is true while a
  builtin cycle worker has been started (`_maybe_start_builtin_cycle_worker`,
  5600) and `_handle_builtin_cycle_worker_result` (5987) has not applied
  its diagnostics. Freshness rule: the idle clock is settled from a
  cycle's applied result, so an empty cycle leaves the clock where it
  was, an in-flight cycle defers the decision without moving it, and a
  300 s grid of empty cycles crosses the deadline. Starting a cycle is
  not work; retained work with a future expiry is (§10.3). No new query is added; the predicate reads the diagnostics the
  cycle already returns to the reactor and the cached deferred-write
  status, never the `MaintenanceWorker`'s store directly.
- **LivenessMonitor** (`weft/core/tasks/liveness_monitor.py`): work =
  a `_latest_rows` entry that counts (§10.4), or an `_in_flight` probe
  or pending probe result whose mapping counts. The counting rule is
  applied to probes and results as well as rows (round-2 F5a): the
  monitor keeps probing and retiring its own, the Manager's and the other
  services' mappings as today, but those probes and results never touch
  the idle clock. Presence of a counting row is the whole test; no
  live/stale distinction is read (round-2 F5b: `MappingRow` retains no
  last outcome, and a counting row that fails to retire stays work until
  it is retired). A heap entry alone is not work (a scheduled probe
  leaves the heap before it is recorded in flight, and stale entries
  remain). A refresh that finds no counting row is not work.

Firing tests per service: work true and false for each named input; the
empty-cycle rule for TaskMonitor (several empty cycles spanning the
deadline exit the service; one substantive cycle resets it); the
LivenessMonitor cases of §10.4.

### Task 3. Managers stop owning services (`weft/core/manager.py`)

- `_continue_shutdown_drain`, non-stopping branch: finish when
  `_user_work_children()` is empty, not `_child_processes`. Services
  still tracked are left as they are.
- Remove phase 2: `_drain_phase_children` returns user work only;
  `_drain_user_work_phase_holds` ends the drain instead of starting
  phase 2 (`_drain_services_phase` and its reset sites go; the "services"
  warning branch in `_continue_shutdown_drain` goes).
  `_finish_graceful_shutdown` runs once phase 1 is done.
- `_detach_launched_services`: drop every supervision-only child from
  `_child_processes` without signalling, and remove each one's `Process`
  from the `multiprocessing` children set
  (`multiprocessing.process._children.discard(process)`; private, see
  §6b; comment names this plan and the hazard). No terminal envelope is
  written for a live detached service. Called from
  `_finish_graceful_shutdown` and from `_cleanup_task_resources` before
  any terminate call; `_cleanup_task_resources` then terminates user work
  only (the released-launch exception of [MA-3] stays) and never calls
  `_terminate_children` without a `tids=` argument.
- Late launches (round-1 F1, round-2 F1): the worker-side reap in
  `_run_child_launch_service_worker` (1620) no longer fires merely because
  cleanup has begun (`_active_cleanup_deadline is not None`); it fires
  only for a result that lands after the cleanup deadline, or after the
  worker's stop request, exactly the case [IMPL.10] already states. A
  result that lands during cleanup but before the deadline is consumed
  by `_drain_active_child_launches_for_cleanup` (8531) on the reactor,
  which still owns its broker resources there: a service-role result is
  committed detached (process left running, `Process` discarded from
  the children set, registry row written by the normal commit path so
  the next Manager's ensure sees a live owner); a user-work result keeps
  the existing commit-then-terminate. After the deadline the worker reaps
  either role (§10.5); no broker effect moves into the worker
  ([WT-2a.6], [IMPL.9] untouched). Tests beside
  `test_manager_late_child_launch_self_reaps_after_cleanup_deadline`
  (7004): a service result before the deadline is registered and alive
  after cleanup; a service result after the deadline is reaped.
- `_managed_service_convergence_active`, `_desired_internal_service_keys`,
  `_service_supervision_allowed`, SIGUSR1 handling and duplicate
  convergence: unchanged.

Firing tests (`tests/core/test_manager.py`, `tests/cli/test_cli_manager.py`,
`tests/cli/test_cli_serve.py`): (a) a Manager superseded after launching
the heartbeat, with no user work, exits at once and the heartbeat process
is untouched (build the Manager with a supervision-only child whose
process is alive, call `_begin_superseded_shutdown`, drive
`_continue_shutdown_drain`, assert `should_stop`, the child's process
alive, and `multiprocessing.active_children()` not listing it); (b) the
same with user work: exits only after the user work exits; (c) system
STOP with user work and services: STOP goes to user work only, the
drained event follows phase 1, services are detached and never
signalled; (d) idle exit detaches services; (e) the late service launch
case; (f) the real boundary through the CLI: start a Manager, wait for
its heartbeat to be live, send `weft manager stop` (a queue STOP, so the
Manager exits through normal interpreter shutdown on Windows), and assert
the Manager process exits within the drain window while the heartbeat
process is still live; this runs on every platform and is the Windows
proof (round-2 F9: `process.terminate()` force-kills on Windows and
would bypass the child join). SIGTERM coverage stays on POSIX in the
existing serve test; (g) the existing
`test_parallel_manager_reuse_converges_to_single_manager_under_repeated_bootstrap`
and `test_weft_harness_cleanup_preserves_sqlite_integrity_for_parallel_manager_reuse`
are the end-to-end oracle, unchanged in intent (their convergence comment
is updated).

### Task 4. Ensure-only at startup and on reconcile

Verify, and test if untested, that a Manager starting while the context's
services are already live (launched by an earlier or a superseded
Manager) adopts them as live through the existing evidence path and
launches nothing, and that a Manager starting after the services
idle-exited launches them. Both are the current reducer's behavior; the
test is the contract check the owner asked for.

### Task 5. Harness and status

- `WeftTestHarness`: after the Manager stop, on both `cleanup` and
  `_cleanup_preserving_database`, and before `_drain_registry_queue`,
  STOP every live internal service of the context by registry record
  (`ctrl_in` from the record's `queues`), wait for exit within the
  harness deadline, then terminate any survivor by its scoped host PID
  through the existing `_terminate_registered_pids` path; the lingering
  window no longer applies to services. Record what had to be terminated
  in the harness diagnostics so a leak is visible.
- Strengthen `tests/test_harness_registration.py::test_owned_cli_manager_is_reaped_when_test_body_fails`
  (1933): wait for the heartbeat launch, record its scoped PID beside the
  Manager's, inject the failure, and assert both processes have exited
  and the root is removed. CI job 126880183604 (Windows `WinError 32` on
  the harness root at cleanup) is the symptom this proves fixed; it is
  verified by the release run, not assumed.
- `weft/commands/system.py`: document `manager_tid` as "launched by" in
  the status docstring.

### Task 6. Specs, theory, CHANGELOG, closure

Apply §4c in the implementation commit (spec) and the `Theory:` commit
(theory, after owner acceptance). CHANGELOG `### Changed` entry under
the open 0.9.120 heading (the release has not shipped). Backlink rows in
the `## Related Plans` sections of specs 03 and 05 and in the [MA-1.6a]
mapping. `docs/plans/README.md` index row; `Status:` flips to
`completed` in the closing commit after the independent implementation
review and the keyed possession probe ([WT-7]) are logged.

## 6. Testing Plan

Firing tests are listed per task. §6a lists the existing tests the
change touches, each updated to the new rule or shown unaffected in the
Execution log.

Gates: `ruff check`, `ruff format --check`, `mypy` (CI invocation), the
touched test modules with `-m ''`, then the fast suite, then
`./bin/pytest-pg --fast`. The release process runs everything again.

### 6a. Existing tests that assume Manager-owned services

From the 2026-11-04 sweep of `tests/`, `integrations/weft_django/tests`
and `extensions/*/tests` (the last two contain nothing service-related),
corrected by round 1. The convergence, reducer, registry, probe and
status tests are unaffected: the reducer, the registry contract, the
launch path and the status shape do not change.

Manager stop no longer stops services (Task 3; owner rule §10.2):

- `tests/core/test_manager.py::test_manager_stop_orders_user_work_then_services_then_manager`
  (8625): becomes "user work, then the Manager; services untouched":
  services stay alive through `manager_stop_drained` and receive no
  `control_stop`.
- `tests/core/test_manager.py::test_manager_sentinel_failure_during_shutdown_still_reaps_children`
  (4186): `kill_order` becomes `["child"]`; the service entry is
  detached, its process untouched.
- `tests/core/test_manager.py::test_manager_stop_keeps_target_whose_identity_probe_fails`
  (9250): drop the `_drain_services_phase is False` assertion.
- `tests/cli/test_cli_manager.py::test_manager_stop_orders_user_work_before_internal_services`
  (293): becomes "`weft manager stop` leaves the services live"; the
  harness stops them at cleanup.
- `tests/cli/test_cli_serve.py::test_serve_sigterm_drains_children_cleanly`
  (744): becomes Task 3 (f): Manager and user work gone, services live.
- `tests/cli/test_cli_serve.py::test_serve_restarts_singleton_services_without_duplicates`
  (554): unchanged in intent; its closing `manager stop` leaves services
  for the harness.

Service self-stop (Tasks 1 and 2):

- `tests/tasks/test_heartbeat.py::test_heartbeat_empty_table_never_idle_exits`
  (657): the inverse oracle; replaced by "an empty table idle-exits after
  the window, and a table with one row never does".
- `tests/tasks/test_heartbeat.py::test_heartbeat_empty_table_publishes_no_timer`
  (691): `None` becomes the idle remainder; the "no audit, no poll"
  guard stays.
- `tests/tasks/test_task_monitor.py::test_task_monitor_disabled_has_no_private_timer_and_still_wakes`
  (2955): the three `next_wait_timeout() is None` assertions become the
  idle remainder; the "disabled monitor must not scan" guard stays.

Superseded and idle exits (Task 3):

- `test_manager_superseded_self_record_drains_children_without_stopping`
  (10668), `test_internal_task_monitor_child_does_not_block_idle_shutdown`
  (6724), `test_manager_idle_shutdown_waits_for_missing_internal_service`
  (6772): unchanged in intent; re-run as oracles.
- `test_manager_late_child_launch_self_reaps_after_cleanup_deadline`
  (7004): unchanged (user work); the service counterpart is added beside
  it.

Harness and fixture cleanup (Task 5):

- `tests/helpers/weft_harness.py` paths named in §3.
- `tests/test_harness_registration.py` (766, 1496, 1605, 1652, 1933):
  oracles for the new cleanup; 1933 strengthened as in Task 5.
- `tests/system/test_django_fixture_cleanup.py::test_django_module_fixture_reaps_owned_runtime`
  (73): passes only through the harness service stop. Oracle.

Unaffected but checked: everything the sweep tagged [L] in
`tests/core/test_manager.py`, `test_manager_services.py`,
`test_service_convergence.py`, `test_admission.py`,
`tests/commands/test_status.py`, `test_runtime_prune.py`,
`tests/cli/test_cli_status_rendering.py` and the fixtures that disable
service supervision. `tests/core/test_task_monitoring.py::test_task_monitor_operational_log_emits_config_and_cycle`
(390) keeps `manager_tid` from `parent_tid` as provenance.

### 6b. Windows: a detached service must not block the launcher's exit

`launch_task_process` starts services with `Process(daemon=False)` and
`_task_process_entry` hard-exits with `os._exit(0)` only when
`os.name != "nt"` (`weft/core/launcher.py:163`). On Windows a Manager
process exits through the interpreter's normal shutdown, where
`multiprocessing` joins every live non-daemon child; with services left
alive, a Windows Manager would hang for as long as a service has work,
which can be indefinite. There is no public `multiprocessing.Process`
operation for detaching a started live child (`close()` rejects a live
process; `daemon` cannot change after start; round-1 reviewer concurred).
Task 3's detach removes the `Process` from the `multiprocessing` children
set on every platform. The proof is Task 3 (f), the real CLI boundary,
not a membership assertion alone.

## 7. Verification

- Unit: the firing tests above.
- End to end: the two `cli` convergence tests, run three times locally
  and once under `nice` load to approximate CI timing; Task 3 (f).
- CI: the release run's `cli` lanes on all three platforms green, and the
  Windows `core-commands` lanes free of `WinError 32`.
- Observable: `weft manager stop` on a context with an idle heartbeat
  returns within one drain window and `ps` still shows the heartbeat
  until its idle clock ends it.

## 8. Rollback

`git revert` of the implementation commit and the theory commit; no
persisted state.

## 9. Out of Scope

- SIGUSR1 whole-table terminate and duplicate-convergence termination
  (pre-existing, retained; removing the latter is for owner decision and
  would reopen the singleton contract).
- Service registry record shape and `weft status` JSON shape.
- Autostart manifests (`[MA-1.6]`) and their restart/backoff.
- Extending the POSIX-only hard exit to Windows.
- The macOS heartbeat registration KeyError and the Windows strict clock
  comparison from run 41917203345: fixed separately in `14d84f29` and
  `6a3dce18` before this plan lands.

## 10. Owner Decisions

### 10.1 Idle churn under a live Manager (A5)

With "work" defined inside each service and no Manager condition, a
service under a long-lived idle Manager exits every five minutes and is
relaunched by the Manager's ensure. Options: (a) accept the churn; (b) a
service counts a live non-superseded Manager record as a reason to stay,
so it idle-exits only once no Manager remains; one registry read per
idle check (every 300 s), which TaskMonitor already performs and the
heartbeat can perform through the same helper. Recommendation: (b); it is
the "last Manager out" behavior with no Manager-side logic.
**Pending owner ruling.**

### 10.2 `weft manager stop` and services

The owner's wording "can persist after the manager stop if they have
work to do" is read as: no Manager stop of any kind stops a service.
Phase 2 is removed. A user who wants the services gone sends them STOP
directly (`weft task stop <tid>`) or waits out the idle clock.
**Confirm.**

### 10.3 TaskMonitor retained work with a future expiry

**Ruled by the owner, 2026-11-04:** counted. "I want to make sure that
if there is cleanup at the next expiration date that the Monitor would
handle, it stays active. The net result is that the Monitor may stay
active for a while (absent STOP), until everything has timed out and
been deleted." Task 2 defines `retained_work_pending` accordingly; the
open-family presence check is the one store read added, inside the
cycle worker.

### 10.4 Which mappings count as LivenessMonitor work

`_latest_rows` holds every task-state mapping, including the Manager's,
the other services' and the LivenessMonitor's own. If every row counts,
the three services keep each other alive in an idle context after the
Manager leaves, and LivenessMonitor never idles while a Manager runs.
Recommendation: only rows whose role is user work (not `manager`, not an
internal service class) count; their presence is the whole test.
**Pending owner ruling.**

### 10.5 A launch result that lands after the cleanup deadline

Round-2 F1: once the cleanup deadline passes, the reactor no longer
consumes worker results and its broker resources close, so a service
launched that late cannot be registered by this Manager, and registering
from the launch worker would move a broker effect into the worker
([WT-2a.6], [IMPL.9]). Options: (a) keep today's reap for that case,
service or not, as [IMPL.10] states, as the third stated Manager
termination path (the Manager undoes a launch it could not complete; the
next Manager's ensure launches a fresh service); (b) leave the process
running unregistered (an undiscoverable duplicate until its own
registry-free evidence is found; rejected). Recommendation: (a), written
into §1 and Task 3. **Confirm.**

## Review Log

- 2026-11-04: plan drafted by the series lead (Fable 5.1) from the CI
  evidence and the owner's rulings of the same day; test inventory from
  a read-only sweep by a native subagent (Explore), checked against the
  code before use.
- Cross-family round 1 (codex, 2026-11-04, the plan at the working tree
  of `273be529`): verdict BLOCKED, F1–F7, all accepted and applied. F1
  (High): `_run_child_launch_service_worker` kills a late launch during
  cleanup, service included; fixed, Task 3 commits a late service launch
  detached (A8). F2 (High): counting an in-flight cycle as work defeats
  TaskMonitor's idle exit on its own grid; fixed, work is cycle-sampled
  from results, empty cycles leave the clock alone, "checking is not
  work" stated in §1. F3 (High): the TaskMonitor predicate needed state
  the reactor does not hold; fixed, it reads the cycle's recorded counts
  and the cached deferred status only, families awaiting reports moved
  to §10.3. F4 (Medium): no shared turn hook; fixed, `ServiceTask`
  owns `_process_reactor_turn` as a template with `_settle_idle_clock`
  last. F5 (Medium): a heap entry is not an outstanding probe and every
  mapping counts today; fixed, work is `_in_flight`, pending results or
  a counting row, with the row rule in §10.4 and A5 qualified. F6
  (Medium): normative spec edits make this class 5 and the plan needed a
  named strategy and exact text, including the [WT-2a.13] delta; fixed,
  class 5, strategy B, §4c carries every replacement block,
  `RECONSIDERATION REQUESTED` on [WT-2a.13] with the owner ruling as
  disposition. F7 (Medium): the inventory missed three inverse oracles
  and the cleanup oracle recorded only the Manager PID; fixed, §6a names
  them and Task 5 strengthens 1933. Reviewer's assessments adopted: the
  idle mechanism is the smallest reasonable one; the two retained Manager
  termination paths are stated narrowly; the Windows proof is the real
  CLI boundary. The reviewer noted the concurrent watcher and runner
  edits as outside the unit (they landed as `14d84f29` and `6a3dce18`).

- Cross-family scoped round 2 (codex, 2026-11-04, the plan at the working
  tree of `6a3dce18`): verdict BLOCKED, F1, F2, F5a, F5b, F6, F8, F9; F3,
  F4 and F7 confirmed resolved. All applied; no round 3 (one scoped round
  is the series rule; the remaining choices are reported as forks in §10).
  F1 (High): a result after the cleanup deadline has no registry path;
  fixed, pre-deadline results commit detached and register on the reactor,
  post-deadline results keep the [IMPL.10] reap as a stated third path
  (§10.5, owner confirms). F2 (High): the idle decision could run while a
  cycle was in flight on an empty previous result; fixed, a second hook
  `_work_undetermined` defers the decision without moving the clock, and
  the wait does not spin. F5a (High): probes of non-counting mappings
  reset the clock; fixed, the counting rule applies to probes and
  results. F5b (Medium): "live or unknown" was not derivable; fixed,
  presence of a counting row is the test. F6 (Medium): the replacement
  set left "does not exit on an empty table", the sentinel-failure clause,
  the two-window sentence, former item 3's "no other child's event"
  sentence and the mapping location unaddressed; fixed, blocks added,
  mapping location corrected, [IMPL.10] left unchanged by §10.5, MF-3.2
  "never stops" narrowed. F8 (Medium): settling after a STOP would call
  `mark_completed` on a cancelled task; fixed, terminal guard first, test
  (g). F9 (Medium): SIGTERM is not a Windows proof of normal shutdown;
  fixed, Task 3 (f) uses `weft manager stop`.

## Revision Log

- 2026-11-04: drafted.
- 2026-11-04: round 1 applied (see Review Log); class 4 to 5; §4c added.
- 2026-11-04: round 2 applied; §10.5 added; forks reported to the owner.
- 2026-11-04: §10.3 ruled by the owner (retained work with a future
  expiry keeps the Monitor alive); Task 2 updated.

## Execution log

(Empty until implementation.)

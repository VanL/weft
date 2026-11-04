# System STOP: Tasks, Then Internal Services, Then the Manager

Status: promoted
Source specs: docs/specifications/03-Manager_Architecture.md [MA-3]; docs/specifications/07-System_Invariants.md [MANAGER.10], [OBS.6], [LIVENESS.R3]-[LIVENESS.R7]; docs/specifications/02-TaskSpec.md [TS-1], [TS-1.1]; docs/specifications/05-Message_Flow_and_State.md [MF-5]
Superseded by: none

Class: 5 (spec-changing, hardening applies). Two facts put it there. First,
the [MA-3] drain contract changes: STOP gets an ordering and a phase-2
window, and the normative text has to say so. Second, the change also fires
risky triggers in `docs/agent-context/runbooks/writing-plans.md` "When
Hardening Is Mandatory". It touches the durable spine (the Manager's drain
of children launched on `TaskSpec -> Manager -> Consumer`), it changes a
public behavior contract (what `weft manager stop`, `SIGTERM`, and `SIGINT`
do, and in what order), and the coverage half may make the Manager signal
processes it did not launch. That last change is a one-way door in operator
expectations. So the hardening checklist applies in full, and review happens
before the implementation starts.

Plan type: implementation with spec revision. The ordering is decided by the
owner. The coverage scope was ruled by the owner on 2026-11-03: Reading B
(`## Owner Decision: STOP Coverage`). Task 7 runs; task 6 is the rejected
variant and does not run. Its one coverage sub-question (tasks under
another live Manager in the same context) was ruled (a) on 2026-11-03:
those tasks join phase 1 as STOP-drain members.

## Goal

The owner's rule (2026-11-02, quoted in
`docs/plans/2026-11-02-program-theory-reconciliation-plan.md` §9 item 5 and
carried by `docs/program-theory.md` [WT-2a.13], second sentence): "STOP on
the whole weft system means that it is the manager's responsibility to (try
to) stop/suspend/kill any active tasks, then internal services, then the
manager itself." Today a Manager STOP (and `SIGTERM`/`SIGINT`) broadcasts
STOP to user tasks and internal services in the same turn, so the services
that observe the shutdown (TaskMonitor, LivenessMonitor, heartbeat) can exit
before the user work they observe. This plan makes the drain run in three
phases:

1. Signal and drain user work, using the existing escalation rungs.
2. Stop the internal services.
3. Publish the Manager's own terminal event and exit.

It also asked the owner a second question raised by the 2026-11-02 transfer
probe: does a system STOP reach active tasks that sit outside this Manager's
child table? These are detached tasks launched by an earlier Manager. The
owner ruled yes on 2026-11-03 (Reading B, `## Owner Decision: STOP
Coverage`).

## Source Documents

Normative:

- `docs/specifications/03-Manager_Architecture.md` [MA-3] (the drain
  paragraph at lines 801 to 812: "`SIGTERM` and `SIGINT` against a Manager
  enter the same drain path as STOP" through "scheduler delay under
  release-load parallelism."). It describes broadcast, escalation, and reap.
  It does not order services and does not define coverage.
- `docs/specifications/03-Manager_Architecture.md` [MA-1.6a] (managed
  internal service supervision; `Manager._user_work_children` is mapped
  there).
- `docs/specifications/07-System_Invariants.md` [MANAGER.10] (a draining
  Manager must not launch a reserved child), [OBS.6] (task-owned snapshots
  carry `projection` and `observation`, where observation holds the resolved
  transport routes), [LIVENESS.R3] (LivenessMonitor is the sole deleter of
  `weft.state.tasks.*`), [LIVENESS.R4], LIVENESS.R5 (host identity requires
  `(pid, create_time)`; extension probes are authoritative for `runner` and
  `external-supervisor`), LIVENESS.R6, [LIVENESS.R7].
- `docs/specifications/02-TaskSpec.md` [TS-1] (`persistent`: "false =
  one-shot, true = keep draining inbox until stopped", line 85) and [TS-1.1]
  (reserved queue policies: tasks accept `keep` or `clear`; `requeue` is
  valid only on the Manager's own spec).
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5] (state
  observation flow; snapshot schemas).

Theory and decisions (interpretive, not overriding the specs):

- `docs/program-theory.md` [WT-2] item 3 (detached topology;
  `weft.state.tasks.<tid>` is the authoritative bridge between process
  identity and lineage), [WT-2a.2] (queues are the only durable truth),
  [WT-2a.13] (independent lifetimes; the second sentence is the owner's
  STOP rule and this plan's acceptance test), [ALT-WT-007] (no stopping
  drain on supersession), [ALT-WT-015] (the Manager-local child table is a
  hint, not the liveness or lineage source).
- `docs/plans/2026-11-02-program-theory-reconciliation-plan.md` (completed):
  §9 item 5 (owner decision), audit row I-13 (aligned for the
  implicit-lifecycle sentence, drifted on system-STOP ordering), and the
  review-log transfer-probe result, answer 3 (the coverage defect).

Process guidance: `CLAUDE.md` §1.1 and §5.1;
`docs/agent-context/decision-hierarchy.md` [DOM-15];
`docs/agent-context/runbooks/writing-plans.md`;
`docs/agent-context/runbooks/hardening-plans.md`;
`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`.

## Context and Key Files

### Current structure (verified at b7eed9f9)

- `weft/core/manager.py::Manager._commit_child_launch_success` (defined at
  line 1672; the write is line 1686) is the only write into
  `_child_processes` (`grep -cE
  "_child_processes\[[^]]+\] = " weft/core/manager.py` prints 1). So the
  table holds only children this Manager process launched. It holds user
  tasks and managed internal services together. Services carry
  `ManagedChild.internal_role`.
- `Manager._child_is_supervision_only` (line 2164) is true for
  `internal_role` in {`heartbeat`, `task_monitor`, `liveness_monitor`}
  (`INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT`, `..._TASK_MONITOR`,
  `..._LIVENESS_MONITOR` in `weft/_constants.py`).
  `Manager._user_work_children` (line 2173) returns the complement. Idle
  exit, leadership yield, and the superseded drain already use it. Pipelines
  (`pipeline`, `pipeline_edge` roles), autostart children, and persistent
  tasks are user work under this split.
- `Manager._begin_graceful_shutdown` (line 4800) is the STOP control
  handler's entry, via `Manager._handle_control_command` at line 1307.
  `Manager._apply_termination_request` (line 4918) is the `SIGTERM`,
  `SIGINT`, and parent-loss entry. Both call `Manager._begin_shutdown_drain`
  (line 4808). That method sets `_draining`, `_drain_stops_children = True`
  and `_drain_started_ns`. It applies the Manager's own
  `reserved_policy_on_stop`, then calls `_signal_children_to_stop`.
- `Manager._signal_children_to_stop` (line 4770) iterates **all** of
  `_child_processes`, services included. For each child it sends STOP once,
  then sends host SIGTERM (`terminate_process_tree(..., kill_after=False)`)
  after `MANAGER_CHILD_STOP_ESCALATION_SECONDS` (2.0 s). That is drift 1:
  tasks and services are signalled together.
- `Manager._continue_shutdown_drain` (line 8691) runs on each reactor turn
  while draining. It re-signals, reaps with `_cleanup_children`, and on
  `_shutdown_drain_timed_out` (window `MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS`,
  15.0 s, from `_drain_started_ns`) calls
  `_terminate_children(time.monotonic() + TASK_CLEANUP_TIMEOUT_SECONDS)` on
  **all** children. When `_child_processes` is empty it calls
  `_finish_graceful_shutdown` (line 4905), which emits the Manager's
  completion event (`manager_stop_drained` for STOP, `task_signal_stop` for
  signals) and sets `should_stop`. There is no tasks-then-services phase.
  Phase 3 already has the right shape: the Manager's terminal event is
  emitted only after the tracked table is empty. That is a statement about
  the table, not about processes: see the `_terminate_children` bullet.
  The completion check does not consult in-flight launches.
- `Manager._terminate_children` (line 4629) is the forced-reap rung. It
  removes every tracked entry it handled (in a `finally`) and keeps
  diagnostics for uncertain survivors in `_last_child_cleanup_survivors`
  with a warning. So "the tracked table is empty" after it does not mean
  "every process has exited".
- `Manager._manager_drain_timeouts` (line 8248) publishes the drain's exact
  clocks: one per signalled, unescalated tracked child for escalation, plus
  the window deadline, which it publishes only while `_child_processes` is
  non-empty. With no tracked child it publishes no drain clock at all.
- `Manager._has_active_child_launches` (line 1267) is true while a child
  launch waits for its main-thread commit. A launch result reaches the
  reactor as a service-worker event, and
  `Manager._stale_child_launch_timeouts` (line 8298) publishes the
  recovery clock for a launch worker that retired silently
  (`MANAGER_CHILD_STARTUP_LIVENESS_GRACE_SECONDS`, 1.0 s).
- Fatal-cleanup path: a failure that escapes `Manager._process_reactor_turn`
  (line 8341), such as a fatal child-sentinel observer error, unwinds into
  task finalization, which calls `Manager._cleanup_task_resources` (line
  8086). That method runs `_drain_active_child_launches_for_cleanup(deadline)`
  and then one unfiltered `_terminate_children(deadline)` under one absolute
  cleanup deadline, each inside an `attempt` wrapper that collects failures
  and re-raises the first. Tasks and services are terminated together on
  this path too.
- `Manager._activity_wait_queues` (line 1886) already adds per-task
  `weft.state.tasks.<tid>` queues to the existing arbiter's wake set for
  startup feedback, and `Manager._has_pending_observation` reads a
  publication cursor on them without consuming task state. This is the
  precedent for any wake interest the coverage half needs.
- `Manager._service_supervision_allowed` (line 5907) returns false while
  draining, so a service that exits mid-drain is not restarted.
- `SIGUSR1` takes a separate branch in `_apply_termination_request`: it calls
  `_terminate_children` on everything at once and then
  `BaseTask.handle_termination_signal`. This is the immediate-kill path and
  it does not change.
- Non-stopping drains: `_begin_leadership_drain` (line 4842) and
  `_begin_superseded_shutdown` (line 4874) set `_drain_stops_children =
  False`. `_continue_shutdown_drain` never signals children on those paths.
- Coverage (drift 2): `Manager._refresh_scheduler_evidence` (line 2469) is
  called on each non-draining turn and returns early until
  `_admission_refresh_after` passes (`MANAGER_ADMISSION_RECHECK_SECONDS`,
  1.0 s). On its first successful run it reads every snapshot with
  `weft/core/task_state.py::latest_task_state_rows` and calls
  `Manager._bootstrap_scheduler_evidence` (line 2522). That adopts the
  residents of every dead Manager's checkpoint, plus every uncovered task
  snapshot, through `Manager._adopt_scheduler_evidence` (line 2252) into
  `self._scheduler_residents`. It also calls `_recover_scheduler_paths`
  (line 2614). It skips residents covered by a **live** other Manager's
  checkpoint. `_adopt_scheduler_evidence` also skips evidence with no
  `root_tid` and Manager rows. Adoption writes scheduler evidence only. It never adds a
  `ManagedChild`, so `_signal_children_to_stop` never reaches these tasks.
  Detached tasks launched by an earlier Manager survive a system STOP today.
- `weft/core/task_state.py::read_task_state_snapshot` (line 54) is the
  shared newest-valid snapshot reader ([OBS.6]). `latest_task_state_rows`
  (line 92) and `list_task_state_tids` (line 43) enumerate the scoped
  context. The snapshot payload is built by
  `weft/core/tasks/base.py::BaseTask._build_tid_state_payload`. It carries
  `role`, `hostname`, `scheduling_class`
  (`finite`/`background`/`internal`, see
  `weft/core/runtime_identity.py::runtime_identity_for_task`),
  `host_processes` (`pid`, `create_time`), `runtime_handle`, and an
  `observation` object. `weft/core/task_evidence.py::parse_task_observation`
  validates `observation` and returns the task's resolved `ctrl_in` route
  and its `mode` (`one_shot`, `persistent`, `pipeline`).
- A task's own STOP handling (`weft/core/tasks/base.py`, the `control_stop`
  event at line 1733 and the `reserved_policy_on_stop` application at line
  2293) applies the task's reserved policy. The Manager does not apply a
  child's policy.

### Files to modify

- `docs/specifications/03-Manager_Architecture.md` ([MA-3] paragraph,
  `_Implementation mapping_` lines, `## Related Plans` backlink)
- `weft/core/manager.py`
- `tests/core/test_manager.py`
- `tests/cli/test_cli_manager.py` (end-to-end harness tests)
- `CHANGELOG.md` (behavior note)
- `docs/plans/README.md` (Status Index row for this plan: created with the
  plan file, flipped at closeout; see `## Review Log`)

### Read first

- `docs/specifications/03-Manager_Architecture.md` [MA-3] lines 740 to 900,
  and [MA-1.6a] in the implementation list near line 573
- `docs/specifications/07-System_Invariants.md` [MANAGER.10],
  [LIVENESS.R3] to [LIVENESS.R7]
- `weft/core/manager.py` lines 2160 to 2185, 4516 to 4970, 8248 to 8300,
  8640 to 8720
- `tests/core/test_manager.py` fixture `manager_setup` (line 914; note its
  custom inbox launches no services), `make_manager_spec` (line 329),
  `test_manager_enqueues_one_internal_task_monitor_spawn` (line 1661),
  helper `_process_running` (line 1039), and the anchors listed in
  `## Testing Plan`

### Shared paths to reuse, not duplicate

- `Manager._child_is_supervision_only` / `Manager._user_work_children` is
  the one service-versus-user-work split. Do not add a second classifier.
- `Manager._signal_children_to_stop` holds the one STOP-then-SIGTERM rung.
  Parameterize it by the child set. Do not copy it per phase.
- `Manager._terminate_children` holds the one forced-reap rung.
- `Manager._finish_graceful_shutdown` is the one terminal publication.
- Coverage reading B only: `Manager._scheduler_residents` (already adopted
  by recovery), `read_task_state_snapshot`, `parse_task_observation`,
  `Manager._send_stop_command`, and the `Manager._activity_wait_queues` /
  `Manager._has_pending_observation` wake-interest pattern.

### Comprehension questions (answer in the execution log before the first edit)

1. Why does the superseded drain started by `_begin_superseded_shutdown`
   never stop a user child, and which field makes `_continue_shutdown_drain`
   skip signalling on that path?
   Expected answer: `_begin_superseded_shutdown` sets
   `_drain_stops_children = False`. `_continue_shutdown_drain` calls
   `_signal_children_to_stop` only when `_drain_stops_children` is true.
   Supersession changes who takes future work and never stops running work
   ([ALT-WT-007]; [WT-2a.13], first sentence). The phase split in this plan
   lives entirely inside the `_drain_stops_children` branch, so the
   superseded and leadership drains are untouched.
2. After this change, can the Manager publish `manager_stop_drained` while a
   TaskMonitor child is still in `_child_processes`? Why does phase 2 get its
   own window instead of sharing phase 1's?
   Expected answer: No. `_finish_graceful_shutdown` is reached only when
   `_child_processes` is empty, and that already holds today. Phase 2 resets
   the window start because phase 1 may consume the whole
   `MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS` budget forcing user work down.
   The constant's docstring sizes that budget for services to "deliver
   STOP, publish terminal service state" on slow backends. A shared window
   would leave services zero time and force-kill them. The worst case
   becomes two windows plus two forced-reap deadlines (15 + 2 + 15 + 2 =
   34 s), still below `MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS` (55 s).

A wrong answer is a stop-and-re-read blocker.

## Invariants and Constraints

### What must not change

- **Supersession, leadership yield, idle exit, and crash never kill user
  work** ([ALT-WT-007]; [WT-2a.13], first sentence). The non-stopping
  drains (`_drain_stops_children = False`) keep their current behavior
  exactly. `test_manager_superseded_self_record_drains_children_without_stopping`
  and `test_manager_leadership_yield_drains_nonpersistent_children` stay
  green unchanged.
- **The non-stopping drain on a superseded row**: no intake, owned children
  run to completion, then the Manager exits. No phase logic runs on it.
- **`SIGUSR1` immediate-kill semantics**: the `SIGUSR1` branch of
  `_apply_termination_request` keeps calling `_terminate_children` on every
  child at once, services included, with no phases.
  `test_manager_sigusr1_keeps_kill_semantics`,
  `test_manager_sigusr1_outranks_pending_parent_loss`, and
  `test_manager_sigusr1_arriving_during_parent_snapshot_is_not_lost` stay
  green unchanged. The Manager's `KILL` control (inherited from `BaseTask`)
  is likewise unchanged.
- **The drain window and escalation constants**:
  `MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS` (15.0),
  `MANAGER_CHILD_STOP_ESCALATION_SECONDS` (2.0),
  `TASK_CLEANUP_TIMEOUT_SECONDS` (2.0), and
  `MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS` (15.0 + 40.0) keep their
  values. Change none of them unless a measurement recorded in this plan
  shows the need, and that change re-enters review.
- **Reserved-queue policy applied by tasks on STOP**: each task applies its
  own `reserved_policy_on_stop` ([TS-1.1]) when it handles STOP. The
  Manager applies only its own `reserved_policy_on_stop` at drain start
  (`_begin_shutdown_drain`). Neither moves.
- **[MANAGER.10]**: a draining Manager launches no reserved child. Phases
  begin after `_draining` is set and add no launch path.
- **Services are not restarted during a drain**
  (`_service_supervision_allowed` returns false while draining).
- **[LIVENESS.R3]**: the Manager never deletes `weft.state.tasks.*`. Under
  coverage reading B it only reads snapshots and writes STOP control
  messages.
- **[ALT-WT-015]**: liveness and lineage are never decided from the child
  table or OS `ppid`. Under reading B, a non-child task's exit is proven
  only by task-owned terminal proof or verified `(pid, create_time)` exit
  (LIVENESS.R5). Absence or an unknown probe is never proof of exit
  ([WT-2a.13]).
- TID format, forward-only states, `spec`/`io` immutability, and spawn-only
  process creation are untouched.

### Hidden couplings

- The drained terminal event is published in
  `Manager._finish_graceful_shutdown` (manager.py:4908), and final cleanup
  (`_cleanup_task_resources`) runs afterwards from the base finalizer
  (`BaseTask._finalize_task_once`). A child committed by a launch released
  at the phase-1 window expiry is therefore terminated after the drained
  event, which is why the spec delta's "last lifecycle event" promise is
  scoped to the Manager's own events and names that child as the one
  exception.
- `_manager_drain_timeouts` publishes per-child escalation clocks from
  `_drain_signal_started_ns`, plus the window clock from
  `_drain_started_ns`. After this change it publishes clocks only for
  members of the active phase: in phase 1, the signalled user-work
  children (and, under reading B, the adopted targets); in phase 2, the
  signalled services. A clock for a child the drain cannot act on in the
  current phase would be overdue and return `0.0` on every turn. The phase
  transition must reset `_drain_started_ns` and set
  `_drain_immediate_work_pending = True`. Otherwise the reactor sleeps past
  the phase-2 start, or publishes a window that has already expired.
  `test_manager_drain_publishes_only_exact_clocks` and
  `test_manager_drain_immediate_continuation_is_one_shot` guard this.
- `MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS` must stay materially greater
  than the Manager's worst-case drain. That worst case becomes two windows
  plus two `TASK_CLEANUP_TIMEOUT_SECONDS` forced-reap deadlines: about 34 s
  against 55 s, leaving about a 21 s margin. The phase-2 budget is one fresh
  `MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS` window plus one forced-reap
  deadline, and phase 2 is entered once. The bound on the in-flight-launch
  hold (below) adds no window: phase 1 ends at its window expiry
  whether or not a launch has settled, so the worst case stays 15 + 2 + 15
  + 2 = about 34 s against 55 s. The spec delta restates the relation.
- Test fakes that replace `_terminate_children` with a one-argument
  function break if the phase-1 path passes a new keyword. Nine sites in
  `tests/core/test_manager.py` replace it (`grep -n
  'monkeypatch.setattr(manager, "_terminate_children"\|"_terminate_children",\|manager._terminate_children = '
  tests/core/test_manager.py | wc -l` prints 9). Only fakes the phase-1
  timeout path can reach need the keyword. The known case is
  `test_manager_drain_timeout_force_finishes_stubborn_children` (fake
  `finish_stubborn_child(_deadline)`). The fatal-path split in
  `_cleanup_task_resources` (`### Fatal versus best-effort`) passes `tids`
  too, so the cleanup fake `terminate_children(_deadline)` in
  `test_manager_cleanup_surfaces_sentinel_failure_published_after_termination`
  (`tests/core/test_manager.py`, near line 4241) must also accept it.
  Widening a fake's signature is test plumbing. Its assertions must not
  change.
- A child added to the table after the drain begins, a late launch result
  (`test_manager_drain_reissues_stop_for_child_added_after_stop`), can come
  only from a launch already in flight when the drain began
  ([MANAGER.10]). Phase 1 therefore holds while
  `_has_active_child_launches()` is true: every in-flight launch settles
  (it commits into the table and, as user work, is signalled in phase 1;
  or it fails) before phase 2 begins. A late service launch also settles
  in phase 1 and is signalled with the other services in phase 2. There is
  no transition from phase 2 back to phase 1.
- The launch hold is bounded by phase 1's existing window
  (`MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS`), not by the stale-launch
  recovery clock. Verified at b7eed9f9: `_stale_child_launch_timeouts`
  (line 8298) returns `[]` while `_has_worker_activity()` is true, so a
  live launch worker never gets a recovery clock; `launch_task_process`
  (`weft/core/launcher.py`, line 219) calls `process.start()` with no
  timeout, so a worker can stay active indefinitely; and
  `_manager_drain_timeouts` (line 8248) publishes the window clock only
  while `_child_processes` is non-empty, so a phase 1 holding only for a
  launch has no clock at all. The plan therefore publishes the phase-1
  window clock whenever phase 1 is active, including when its only
  remaining members are in-flight launches. When that window expires with
  a launch still in flight, the Manager stops holding for it: it logs the
  unsettled launch TIDs in a drain warning (beside the existing "drain
  timed out" warning), leaves the launch bookkeeping in place, and begins
  phase 2. A child that such a released launch later commits into the
  table is not a phase-2 member and does not reopen phase 1; it is
  terminated by the existing final cleanup in `_cleanup_task_resources`
  (whose launch-settlement step and unfiltered `_terminate_children(deadline)`
  pass reach it whatever its role). The launch-result event wakes the
  reactor when a launch settles inside the window, and the window clock
  wakes it at expiry, so the hold adds no timer.

### Fatal versus best-effort

STOP writes and SIGTERM are best effort, as today
(`_send_stop_command` logs and continues). The forced reap at each phase's
window is the guaranteed backstop.

A fatal error that ends the Manager's run, such as a child-sentinel
observer error escaping `_process_reactor_turn`, leaves the phased drain and
reaches `_cleanup_task_resources`. That error path keeps the order too: it
attempts user-work termination first (`_terminate_children(deadline,
tids=<user-work TIDs>)`), then termination of everything still tracked,
services included (`_terminate_children(deadline)`), both under the one
existing absolute cleanup deadline and each in its own `attempt`, and then
re-raises the original failure. The error path does not re-enter the
phased drain or start a second window.

### Review gates

No new execution path. No new state-machine library. Phases live in one
field on the existing drain. No new dependency. No constant changes. No CLI
shape change. No mock-heavy substitute for the real Manager and child
processes. Cross-family review before implementation (Class 5 with
hardening).

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Spec Baseline

- `b7eed9f9` covers docs/specifications/03-Manager_Architecture.md,
  docs/specifications/07-System_Invariants.md,
  docs/specifications/02-TaskSpec.md,
  docs/specifications/05-Message_Flow_and_State.md, and
  docs/program-theory.md (adopted) at plan authoring time.
- Plan type: implementation with spec revision.
- Promotion baseline identifier: the task 1 spec-promotion commit, whose
  SHA is recorded in the Execution log by the next commit.

## Proposed Spec Delta

Promotion strategy:

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| docs/specifications/03-Manager_Architecture.md | A (in-file, text before link claims) | [MA-3]: the twelve-line span (lines 797 to 812 are one Markdown paragraph) from "`SIGTERM` and `SIGINT` against a Manager enter the same drain path as STOP." (line 801) to "scheduler delay under release-load parallelism." (line 812); the [MA-3] `_Implementation mapping_` list, in task 4 only; `## Related Plans` |

Clause audit of the replaced span. Every clause is carried forward:

- "`SIGTERM` and `SIGINT` ... same drain path as STOP": kept verbatim.
- "bounded child-exit window": kept, now one per phase.
- "host SIGTERM after the named escalation delay": kept for both phases.
- "If the drain window then expires, the Manager forcefully reaps tracked
  child process trees before publishing its drained terminal event": kept
  per phase. The terminal event is now explicitly the last event the
  Manager publishes for itself, once the tracked child table is empty (a
  statement about the table, not a proof that every process has exited),
  with the one disclosed exception of a child committed by a launch
  released at the phase-1 window expiry, whose own lifecycle events may
  follow it (owner, 2026-11-03: the bounded-shutdown choice accepted, the
  promise tightened to name the exception).
- "A fatal child sentinel observer error ... cannot skip that termination
  and reap sequence": kept, and the error-path termination now keeps the
  user-work-then-services order under the one cleanup deadline.
- "`SIGUSR1` retains immediate kill semantics": kept, now stated as "no
  phases".
- "Caller-facing manager stop defaults must be materially longer than the
  internal manager drain window ...": kept, restated against the sum of the
  two phase windows.

The preceding sentence, "Voluntary leadership drain does not kill user
children ..." (lines 797 to 800), is outside the span and unchanged.

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```markdown
`SIGTERM` and `SIGINT` against a Manager enter the same drain path as STOP.
That drain is a system stop: the Manager stops active user work first, then
its internal services, then itself.

1. User work. The Manager takes no new intake (a draining Manager launches
   no reserved child) and broadcasts STOP to every tracked child that is not
   an internal service. Internal services are the supervision-only children:
   heartbeat, TaskMonitor, and LivenessMonitor. Every other child is user
   work, including pipelines, autostart children, and persistent tasks; STOP
   is the documented end of a persistent task's lifetime. If a user child
   does not exit after STOP, the Manager sends host SIGTERM after the named
   escalation delay. If the phase's bounded drain window expires, the
   Manager forcefully reaps the remaining user-work process trees. Internal
   services keep running, unsignalled, throughout this phase.
   Phase 1 lasts until no user-work child remains and no child launch that
   was in flight when the drain began is still unsettled, or until its
   window expires. A launch still unsettled at that expiry is logged and
   released from the phase; a child it later commits is terminated by the
   Manager's final cleanup, not by a return to phase 1.
   Phase 1 also covers every live, user-work task in the scoped context.
   The Manager finds these tasks through task-owned snapshots, the
   checkpoints of earlier Managers that are no longer live, and the
   checkpoints of other live Managers, including a superseded Manager still
   draining ([MF-5]). That evidence names the tasks; the authority to stop
   them is the system-STOP rule. A task another live Manager covers is a
   STOP-drain member only: that Manager keeps its custody and its handles
   and reaps it, and the stopping Manager never sends it a host signal.
   For any such task, STOP goes to the `ctrl_in` route resolved from the
   task's snapshot observation; a task whose route cannot be resolved is
   reported and released from the phase, never sent a guessed route. Host
   SIGTERM and forced reap apply only to same-host processes whose
   `(pid, create_time)` identity is verified and that no other live Manager
   covers. Phase 1 ends only when no tracked user-work child and no other
   member with live evidence remains; a member leaves on task-owned
   terminal proof or verified process exit, whichever Manager sent the STOP
   it acted on. A member whose liveness is still unknown when the window
   expires is reported and released from the phase, never treated as
   exited. A system STOP stops the tasks live when it runs; another live
   Manager that is not itself stopped may still restart a service it keeps
   desired or launch new work.
2. Internal services. Once phase 1 ends, the Manager broadcasts STOP to its
   internal services, with the same escalation delay and one fresh drain
   window of the same length, and forcefully reaps any service process tree
   that remains when that window expires. The drain does not return to
   phase 1.
3. The Manager. Once the tracked child table is empty, apart from any
   child committed by a launch released at the phase-1 window expiry, the
   Manager publishes its drained terminal event and exits. That event is
   the last lifecycle event the Manager publishes for the stop, with one
   bounded exception: a child committed by a launch released at the
   phase-1 window expiry is terminated by the Manager's final cleanup,
   which runs after the drained event, so that child's own lifecycle
   events may follow the Manager's drained event. No other child's event
   does.

A phase with no members completes at once. Services are not restarted
during the drain. The Manager applies its own `reserved_policy_on_stop` when
the drain begins; each task applies its own policy when it handles STOP. A
fatal child sentinel observer error is preserved as the cleanup failure, but
it cannot skip the termination and reap sequence: cleanup on that path
attempts user-work termination before internal-service termination under
one absolute cleanup deadline, then raises the original failure. Manager
supersession, leadership yield, idle exit, and crash are not system stops
and never signal running user work.
`SIGUSR1` retains immediate kill semantics, with no phases. Caller-facing
manager stop defaults must be materially longer than the sum of the two
phase windows, because a Manager may use both drain budgets before
publishing its stopped registry record, and slower broker backends can add
observable registry propagation and scheduler delay under release-load
parallelism.
```

Coverage sentence. Promoted at task 1 (2026-11-03) after the owner's
Reading B ruling: Candidate B below is relabelled as a Replacement block and
is also carried, as the last sentences of item 1, inside the ordering
Replacement block above (`## Revision Log`). Candidate A stays unlabelled.
The original note follows: this one is not promoted until the owner decides
`## Owner Decision: STOP Coverage`. The two candidates below are deliberately not labelled `Replacement`, so
the Replacement-sync gate (`bin/check-doc-paths`) never checks an unchosen
variant. After the decision, the chosen candidate is relabelled
`Replacement (`docs/specifications/03-Manager_Architecture.md`):` under a
`## Revision Log` row. It is inserted as the last sentence of item 1 above.

Candidate A (this Manager's children only):

```markdown
   Phase 1 covers the children this Manager launched. Detached tasks
   launched by an earlier Manager are outside a system STOP and keep
   running; `weft task stop` remains their stop path.
```

Candidate B (every live user-work task in the scoped context, including
tasks another live Manager covers; owner rulings 2026-11-03):

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```markdown
   Phase 1 also covers every live, user-work task in the scoped context.
   The Manager finds these tasks through task-owned snapshots, the
   checkpoints of earlier Managers that are no longer live, and the
   checkpoints of other live Managers, including a superseded Manager still
   draining ([MF-5]). That evidence names the tasks; the authority to stop
   them is the system-STOP rule. A task another live Manager covers is a
   STOP-drain member only: that Manager keeps its custody and its handles
   and reaps it, and the stopping Manager never sends it a host signal.
   For any such task, STOP goes to the `ctrl_in` route resolved from the
   task's snapshot observation; a task whose route cannot be resolved is
   reported and released from the phase, never sent a guessed route. Host
   SIGTERM and forced reap apply only to same-host processes whose
   `(pid, create_time)` identity is verified and that no other live Manager
   covers. Phase 1 ends only when no tracked user-work child and no other
   member with live evidence remains; a member leaves on task-owned
   terminal proof or verified process exit, whichever Manager sent the STOP
   it acted on. A member whose liveness is still unknown when the window
   expires is reported and released from the phase, never treated as
   exited. A system STOP stops the tasks live when it runs; another live
   Manager that is not itself stopped may still restart a service it keeps
   desired or launch new work.
```

The `## Related Plans` addition (in the promotion slice):

```markdown
- [System STOP ordering](../plans/2026-11-02-system-stop-ordering-plan.md) orders a Manager STOP drain as user work, then internal services, then the Manager, under [MA-3].
```

## Slice Order

1. Plan (this file) and its `docs/plans/README.md` row.
2. Independent cross-family review of the plan and `## Proposed Spec Delta`.
   Then the owner's coverage decision (`## Owner Decision: STOP Coverage`):
   ruled Reading B on 2026-11-03. The cross-family rounds on the ruling's
   changes to this plan are closed (`## Review Log`).
3. Spec-promotion slice (task 1): the ordering Replacement, plus the
   Reading B coverage candidate. The decision section's sub-question was
   ruled (a) on 2026-11-03, so the candidate promotes with the tasks other
   live Managers cover included as STOP-drain members.
4. Code slices (tasks 2 to 5, then 7; task 6 is not run).
5. Traceability reconciliation (task 8).

## Owner Decision: STOP Coverage

**Ruling (owner, 2026-11-03): Reading B.** The owner's words: "STOP
probably shouldn't need to know; it is a broadcast to all queues that match
the pattern. But if the manager does need to know, it applies to all tasks
(reading B)." Chosen reading: B. Task 7 runs; task 6 is not run and is kept
below only as the rejected variant. "It applies to all tasks" covers the
persistent-task point in risk 2: adopted persistent tasks are stopped too,
so the explicit confirmation risk 2 asked for is given by this ruling. The
first sentence of the ruling, STOP as a pattern broadcast, is evaluated in
`### Signalling by pattern broadcast: evaluated` below; the evaluation finds
that the installed broker cannot carry the signalling step by pattern, so
the Manager does need to know, and the ruling's second sentence governs.
Its one sub-question, `### Sub-question (ruled (a), 2026-11-03): tasks
under another live Manager`, is closed: tasks another live Manager covers
join phase 1 as STOP-drain members, so Reading B's bound "that no other
live Manager covers" below is withdrawn.

**Question.** The owner's rule says "any active tasks". This Manager's child
table holds only tasks it launched (see `## Context and Key Files`). Recovery adopts detached
tasks from earlier Managers into `_scheduler_residents` and never into the
table. Should a system STOP reach those adopted tasks?

**Reading A: this Manager's children only.** Phase 1 iterates
`_user_work_children()`. Detached tasks launched by an earlier Manager keep
running after `weft manager stop`.

- For: no new signalling authority. A Manager signals only processes it
  launched and holds handles for. This is the smallest change, with no
  cross-host or runner-authority questions.
- Against: it does not meet "any active tasks". After a Manager crash and
  restart, the operator's "stop the system" leaves work running. That is
  the defect the transfer probe predicted.

**Reading B: every live, user-work task in the scoped context.** Phase 1
covers three member sets: this Manager's own children; the residents it has
adopted (from dead Managers' checkpoints and from task snapshots no live
Manager covers); and the residents that another live Manager's checkpoint
covers (including a live superseded Manager still draining), which join
as STOP-drain members only (that Manager keeps custody and its handles and
reaps them; the stopping Manager sends them STOP but no host signal). The
membership source is the [WT-2] item 3 liveness bridge: task-owned
`weft.state.tasks.<tid>` snapshots, read through
`weft/core/task_state.py::read_task_state_snapshot` and
`latest_task_state_rows`, together with Manager checkpoints, classified by
the same `covered` rule `_bootstrap_scheduler_evidence` uses. The custody
rules in [LIVENESS.R3] to [LIVENESS.R7] stay as they are. The Manager reads
and never deletes; LivenessMonitor stays the sole deleter. LivenessMonitor
stays alive through phase 1, so snapshot retirement continues while
adopted tasks drain.

- For: it meets the rule's "any active tasks". It reuses the evidence
  that recovery already gathers. Adoption and checkpoint coverage supply
  evidence, not authority: the authority to stop these tasks comes from
  the owner's [WT-2a.13] rule that a system STOP is the Manager's
  responsibility for active tasks.
- Against: see the risks below.

**Recommendation: Reading B, with the ruled membership above.** Reason:
the owner's rule names "any active tasks", and [WT-2a.13] and
[ALT-WT-015] make the snapshot bridge, not the child table, the source of
which tasks are active. Recovery already classifies these exact tasks under
[MF-5], so the membership adds no new discovery mechanism. History: the
recommendation first bounded coverage to the adopted set and excluded tasks
another live Manager covers; the 2026-11-03 sub-question ruling withdrew
that bound.

Risks of the recommendation, and how the tasks contain them:

1. **A Manager stopping tasks it did not launch.** Containment, as ruled:
   residents adopted from dead Managers or uncovered snapshots join phase 1
   with the full adopted-target treatment (task 7). Residents that another
   live, non-stopping Manager's checkpoint covers (the `covered` rule in
   `_bootstrap_scheduler_evidence`, including a live superseded Manager
   still draining) join phase 1 as STOP-drain members only: no admission
   custody transfer and no host escalation, because their handles belong
   to their own Manager, which reaps them. The earlier containment that
   left them with their Manager, and the known boundary it recorded, are
   withdrawn by the 2026-11-03 sub-question ruling. What remains is a
   disclosed consequence, not an exclusion: the other Manager does not
   escalate on their behalf, may restart an `ensure` autostart it desires,
   and may launch new work, so a system STOP stops the current TIDs and the
   context is quiescent only when that Manager is stopped too.
2. **Persistent tasks.** [TS-1] defines `persistent: true` as "keep draining
   inbox until stopped", so STOP is the documented end of a persistent
   task's life. Reading B therefore treats persistent and one-shot tasks
   alike. Today's phase 1 already stops this Manager's persistent children
   (the current STOP drain does not test `ManagedChild.persistent`); only
   leadership yield refuses to drain while persistent children exist.
   Adopted persistent tasks are the first ones the Manager has stopped
   without launching them. This is the one-way door in operator
   expectations, so the owner should confirm it explicitly. Confirmed by
   the 2026-11-03 ruling ("it applies to all tasks").
3. **Liveness unknown.** A snapshot can be stale, and a probe can be
   foreign-host or unknown. STOP to a dead task's `ctrl_in` creates
   residue. Containment: skip snapshots whose projection is terminal.
   Escalate only same-host processes with verified `(pid, create_time)`.
   Leave unknowns alone at window expiry and report them, never treating
   them as exited ([WT-2a.13]). TaskMonitor cleans control-queue residue
   after it restarts on the next Manager start ([LIVENESS.R7]).
4. **Runner authority.** Tasks whose `runtime_handle` control authority is
   `runner` or `external-supervisor` (LIVENESS.R5: extension probes are
   authoritative for both) get the STOP message only, with no host signal.
   Host escalation applies only to `host-pid` authority.
5. **No sentinel.** Adopted tasks are not `BaseProcess` children, so
   their exit is observed by task-owned terminal proof or verified PID
   exit, never by `_child_sentinel_adapter`. Today the drain has no wake
   source tied to an adopted task. Containment (task 7): each adopted
   target's task-owned completion surfaces, its `weft.state.tasks.<tid>`
   snapshot queue and its terminal events in `weft.log.tasks`, are
   registered as wake interests on the existing arbiter, following the
   `_activity_wait_queues` / `_has_pending_observation` precedent, and
   `_manager_drain_timeouts` publishes escalation and window clocks for
   adopted targets. Do not add a poll interval ([ALT-WT-002]).
6. **Bootstrap race.** A STOP that arrives before the first
   `_refresh_scheduler_evidence` has an empty adopted set. Containment: task
   7 runs one bootstrap refresh at drain start when
   `_admission_bootstrapped` is false.

Fresh-eyes finding for the owner (2026-11-02), updated after cross-family
round 1: two Reading B costs are larger than the original recommendation
stated. First, a membership filter of `scheduling_class != "internal"`
excludes pipeline stages and edges. This is confirmed:
`weft/core/tasks/pipeline.py::PipelineTask._submit_child_spawn` (line 539) submits on the
internal spawn queue, the Manager's launch path passes `internal=True` for
internal-inbox launches, and `runtime_identity_for_task` then assigns
`"internal"`. Task 7's filter therefore excludes only the three service
roles. Second, the drain had no wake source for an adopted task's exit
(risk 5); task 7 now registers the adopted targets' task-owned completion
surfaces as wake interests. Neither changes the recommendation.

Tasks 6 and 7 were conditional: run task 6 if the owner chose A, task 7 if
B. Until the decision was recorded here (date, wording, chosen reading),
neither ran. The decision is recorded above (2026-11-03, the quoted wording,
Reading B), so task 7 runs and task 6 does not.

### Signalling by pattern broadcast: evaluated

The owner's framing is that a system STOP "is a broadcast to all queues that
match the pattern". The primitive exists. SimpleBroker 8.7.2 (installed;
`simplebroker/db.py:3747`) has `BrokerDB.broadcast(message, *, pattern=None,
queue_names=None, create_missing=False)`, contract
`../simplebroker/docs/specs/12-broadcast.md` [SB-BCAST-1] to [SB-BCAST-4],
and Weft already uses it: `weft/commands/queue.py:721` (`weft queue
broadcast --pattern`), `weft/client/_namespaces.py:348` (the client's
`queues.broadcast`), and `weft/core/tasks/base.py:1977`, which writes the
completion fact with the exact-name form (`queue_names=(WEFT_GLOBAL_LOG_QUEUE,
ctrl_out)`, `create_missing=True`), not a pattern.

The candidate shape: phase 1 sends STOP with one call,
`broadcast(stop_message, pattern="T*.ctrl_in")`. The pattern comes from
[QUEUE.3] (`docs/specifications/07-System_Invariants.md` line 130: default
task-local queue names are `T{tid}.…`; `weft/core/tasks/base.py` builds the
default control input as `f"T{tid}.ctrl_in"`). How the contract matches it:
[SB-BCAST-1] specifies a pattern as "existing literal queue names matching
Python `fnmatchcase`" (`_select_broadcast_queues` calls `fnmatchcase` over
`SELECT DISTINCT queue FROM messages`). `fnmatchcase` has no separator
semantics, so `*` crosses `.`: `T*.ctrl_in` matches every queue name that
starts with `T` and ends with `.ctrl_in`. [SB-BCAST-1] also says patterns
"never create queues", and [SB-BCAST-4] makes the write atomic over the set
selected at the selection point. `create_missing=True` is rejected with a
pattern (`ValueError`, [SB-BCAST-2]). So the broadcast does not create a
queue for a TID that has none, and a STOP landing in a dead task's leftover
`ctrl_in` is the same residue risk 3 already covers (TaskMonitor retires it,
[LIVENESS.R7]). Those two properties hold.

The shape still fails, for three reasons verified against the installed
broker:

1. **It does not reach a task whose `ctrl_in` holds no row.** "Exists" in
   [SB-BCAST-1] means the name has a row in `messages`. A task's `ctrl_in`
   gets a row only when someone writes a control message to it, and a task
   deletes its standard `ctrl_in` on exit
   (`BaseTask._cleanup_standard_control_queues_on_exit`). Probe
   (2026-11-03, isolated scratch project, installed 8.7.2): with a Manager,
   its three services, and a live `weft run --no-wait sleep 30` task
   running, `list_queues()` showed no `*.ctrl_in` queue at all, and
   `broadcast("PROBE", pattern="T*.ctrl_in")` returned `0`. A live task
   that has never been controlled, which is the normal case for an adopted
   task, is missed. The per-target path reaches it because
   `Manager._send_child_control_command` calls `broker.write`, which creates
   the queue. This alone falsifies "reaches every task, adopted or not".
2. **It reaches queues the ordering forbids.** The pattern also matches the
   services' `ctrl_in` (heartbeat, TaskMonitor, LivenessMonitor), this
   Manager's own `ctrl_in`, and any other Manager's `ctrl_in`, whenever they
   hold a row (for example an unvacuumed claimed control message). STOP to a
   service in phase 1 is the defect tasks 2 and 3 exist to remove, and STOP
   to another Manager stops that Manager, which goes beyond any reading.
3. **It misses tasks with a non-default control route.** `io.control` may
   name a `ctrl_in` other than `T{tid}.ctrl_in` (`BaseTask` honours
   `taskspec.io.control["ctrl_in"]`), and [MF-5] routes STOP through the
   resolved observation route, not a name convention.

What the Manager needs membership for is unchanged by any signalling
shape: the drain. Phase 1 waits for each target's terminal evidence,
escalates per target, and reports unknowns at window expiry, and none of
that can be done against a pattern. Reading B's membership (the adopted
residents plus this Manager's children) and the wake interests and clocks
task 7 specifies stay as written.

Result: the signalling step stays per-target writes over phase-1
membership, as task 7 specifies. KILL escalation is per target by nature
and is unaffected either way.

A broadcast variant that does work, recorded as a design fork and not
adopted: one exact-name call, `broadcast(stop_message,
queue_names=<the resolved ctrl_in route of every phase-1 member>,
create_missing=True)`, the same form `base.py:1977` uses. It reaches
never-controlled tasks (creation is enabled), touches no service or Manager
queue, honours non-default routes, and replaces N write transactions with
one atomic write. It costs a change to `_signal_children_to_stop`, which
today sends per child and is also the path that reissues STOP to a child
committed after STOP; that reissue would remain a single-target write. The
saving is N-1 transactions on a path that runs once per system STOP. This
plan keeps per-target writes; the owner may adopt the variant, which would
change only task 3's `_signal_children_to_stop` body and task 7's send
step.

**Closed (2026-11-03):** closed on the owner's implement instruction
(2026-11-03) with the recommended option: keep per-target STOP writes (the
probe showed patterns match only queues that already hold rows).

Cross-family round of 2026-11-03 on this fork: exact-name broadcast with
`create_missing=True` reaches the same resolved destinations, creates the
missing queues and writes one copy per unique queue name atomically; it is
not equivalent under failure (one broadcast rolls back the whole selected
set, where separate best-effort writes can reach some targets while another
fails), and it deduplicates repeated names. The saving is N−1 transactions
for the initial phase-1 cohort, with N unmeasured in this repository (one
for a single-task stop; fan-out can make it dozens). The round recommends
keeping per-target writes for this plan; the fork stays recorded for the
owner, and `SYNC_MODE=FULL` is untouched under either form.

### Sub-question (ruled (a), 2026-11-03): tasks under another live Manager

**Ruling (owner, 2026-11-03): (a).** The owner's words: "Dual STOP in the
case of two live managers doesn't hurt anything." Read as option (a): the
stopping Manager's phase 1 also covers tasks whose resident record sits in
another live, non-stopping Manager's checkpoint in the same context (the
bootstrap's `covered` set), as STOP-drain members only. The two
consequences the cross-family round of 2026-11-03 disclosed (F2) stay
disclosed as facts, not objections: the other Manager does not escalate on
their behalf, may restart an `ensure` autostart it desires, and may launch
new work. So (a) stops the current TIDs; it does not make the context
quiescent, which only stopping that Manager does. Option (b), leaving these
tasks to their own Manager, was the assumed reading while the question was
open because task 7 already specified it; the ruling does not adopt it.

The question as posed. A task whose resident record sits in another live,
non-stopping Manager's checkpoint, the `covered` rule in
`Manager._bootstrap_scheduler_evidence` (`weft/core/manager.py` line 2526:
a Manager snapshot whose checkpoint `residents` belong to a TID other than
this Manager's and that `_admission_mapping_is_live` reports live adds
those residents to `covered`, and they are never adopted into
`_scheduler_residents`). What happens if such a task receives STOP: its
`BaseTask` control handler stops it like any other STOP, and its own
Manager observes the exit through its child sentinel and reaps it in
`Manager._cleanup_children` (line 4516: on an exited child it writes the
terminal envelope, joins the process, and pops the TID from
`_child_processes` and the sentinel adapter, then
`_record_reaped_admission` retires the resident).

What (a) means, as applied. The stopping Manager adds the covered
residents to phase 1 as STOP-drain members only: no admission custody
transfer (they are not adopted into its `_scheduler_residents` and not
written to its checkpoint); the same service exclusion, route validation,
terminal observation, wake interests and window release as other adopted
targets; host escalation disabled, since their handles belong to the other
Manager. It sends them STOP through their resolved routes and waits on
their terminal evidence. Their own live Manager holds the handles and
reaps them. The disclosed consequences in detail: the other Manager does
not enter its own shutdown escalation because somebody signalled its child
(`_continue_shutdown_drain`, line 8691, gates escalation on that Manager's
own drain state), so a child that fails to handle STOP stays alive until
the stopping Manager releases it at window expiry; and the other Manager
may restart an `ensure` autostart it desires
(`weft/core/manager_services.py` ~237 treats terminal evidence as
permission to reconsider a desired service, under backoff and restart
limits) and remains free to launch new work. Changes applied under (a):
risk 1's containment and the known boundary it recorded are withdrawn;
task 7's membership adds covered residents with STOP-only treatment and
gains a CLI test with two live Managers; `## Out of Scope` drops the
live-Manager coverage line; Candidate B and the difference-register row
are synchronized.

The `covered` set is a local variable of `_bootstrap_scheduler_evidence`.
It is computed once, at the first successful evidence refresh, and then
discarded; `_scheduler_residents` never holds it. Task 7 therefore
computes it at drain start by the same rule over the same
`latest_task_state_rows` read, so a task the other Manager launched after
this Manager's bootstrap is included. A task the other Manager launches
after drain start is not; that is the disclosed "may launch new work".

Idempotence of a dual STOP (the owner's premise). A task can receive STOP
twice: from the stopping Manager and from its own Manager, if that Manager
is stopped too while the first drain runs. What a second STOP does:

- An idle task (`weft/core/tasks/base.py`): the STOP branch of
  `_handle_control_command` (line 1730) calls `_handle_stop_request`
  (line 2270). That sets `should_stop = True` again (line 2280); its state
  transition, the `control_stop` task-log event, the pipeline terminal
  event and the title update run only when `self.taskspec.state.status not
  in TERMINAL_TASK_STATUSES` (lines 2282 to 2290), so a second dispatch
  adds no transition, no second event and no title update. A second
  explicit dispatch can apply the reserved policy again:
  `_apply_reserved_policy` (line 3082) returns at once for `keep` (line
  3091); for `clear` it drains whatever rows remain with `read_many`
  (lines 3107 to 3109), which is empty only if the first clear succeeded
  and no backlog appeared since; a further bulk clear by a separate control
  operation is permitted by [QUEUE.6]. `_stop_event.set()` is idempotent.
  A second queued STOP is usually never dispatched at all: once
  `should_stop` is set, a stopping task drains worker results without new
  queue dispatch. So a second STOP ack on `ctrl_out` is possible, not
  guaranteed; the stopping Manager's drain does not consume it.
- A consumer with work in flight (`weft/core/tasks/consumer.py`): STOP is
  deferred by `_defer_active_control` (line 683), which stores the command,
  timestamp and request ID (lines 726 to 728) and sets `should_stop`.
  The active Consumer then keeps that first deferred request:
  `_poll_active_control_once` returns at once while a deferred command
  exists (line 637), so a second queued STOP is not read while the first is
  pending and does not replace it. `_finalize_deferred_active_control`
  (line 733) clears the stash before it acts (lines 756 to 758) and applies
  the first request once through `_handle_stop_request`: one transition
  and one ack for that request.
- An interactive task (`weft/core/tasks/interactive.py`) treats a STOP
  that arrives when it is already terminal as acknowledgement only.
- Pipelines: pipeline stages use the Consumer handling above; pipeline
  edges use the BaseTask handling. A pipeline container can repeat its
  downstream STOP writes if it is dispatched STOP twice, but its own
  terminal transition stays guarded by the same terminal-status check.
- A task that has already exited: the second STOP is a residue row in its
  `ctrl_in`, cleaned by TaskMonitor ([LIVENESS.R7]), the same residue
  risk 3 records.

No task kind makes a harmful repeated lifecycle transition, which is the
owner's harmlessness premise as the cross-family round on the sub-question
closure confirmed it.

The stopping Manager's drain treats the task's terminal evidence (its
terminal event in `weft.log.tasks`, its `weft.state.tasks.<tid>` snapshot)
as satisfying the member's place in phase 1 whichever Manager sent the
STOP the task acted on: membership is released on task-owned proof, never
on an ack to its own message.

## Tasks

Each task is red-green where a behavior changes. Run per-task verification
before the next task.

1. **Spec-promotion slice (strategy A).**
   - Outcome: [MA-3] carries the ordering Replacement block exactly, and
     the chosen coverage candidate if decided. No `_Implementation
     mapping_` claims are added yet. `## Related Plans` gains the backlink.
   - Files: `docs/specifications/03-Manager_Architecture.md`.
   - Read first: the clause audit in `## Proposed Spec Delta`; spec 03 lines 788 to 812.
   - Constraints: replace exactly lines 801 to 812 as quoted. Do not touch
     "Voluntary leadership drain ..." or the "Reader omission ..."
     paragraph. Strategy A means no code may cite the new text until task 4.
   - Verify: `./.venv/bin/python bin/check-doc-paths` (or the repo's
     documented invocation) and
     `./.venv/bin/python -m pytest tests/specs -q`. Record the promotion
     baseline identifier in `## Spec Baseline`.
   - Stop if: the replaced span differs from the quoted text at the
     promotion commit (the spec moved). Re-audit the clauses.

2. **RED: ordering regressions.**
   - Outcome: one new lifecycle test in `tests/core/test_manager.py`,
     placed after `test_manager_stop_command_drains_nonpersistent_children`,
     plus the extended fatal-path test below. The lifecycle test uses a
     real Manager object driven by `process_once`, real child processes,
     and real heartbeat and TaskMonitor children. Do not use
     `manager_setup`: it gives the Manager the inbox
     `manager.{tid}.inbox`, and the Manager plans internal services only
     when its inbox is `WEFT_SPAWN_REQUESTS_QUEUE` (`weft/core/manager.py`
     line 7152), so no service child ever appears under that fixture.
     Instead construct the Manager on `broker_env` as
     `test_manager_enqueues_one_internal_task_monitor_spawn` (line 1661)
     does: `Manager(db_path, make_manager_spec(unique_tid),
     config=load_config({"WEFT_TASK_MONITOR_ENABLED": "1",
     "WEFT_LIVENESS_MONITOR_ENABLED": "0"}))`, write the user task to
     `manager._queue_names["inbox"]`, and clean up with
     `manager.stop(join=False)` and `manager.cleanup()` in `finally`, as
     the fixture does.
   - Readiness before STOP (both are required; if either is not reached
     within the wait, stop and report, and do not inject a fake service or
     write STOP anyway):
     - User work is ready: the user task is a `command` task running a
       Python script that installs its `SIGTERM` handler and only then
       writes a marker file, following
       `_write_term_trapping_descendant_scripts` in
       `tests/core/test_manager.py` (`grep -n "def
       _write_term_trapping_descendant_scripts" tests/core/test_manager.py`;
       its script calls `signal.signal(signal.SIGTERM, signal.SIG_IGN)`
       before writing its PID file). Wait for that marker: it is the task's
       own running evidence that the handler is installed.
     - The service cohort is non-empty: `_child_processes` holds at least
       one child for which `Manager._child_is_supervision_only` is true.
       Then retain the cohort before STOP: a dict of service TID to its
       `ManagedChild.process` handle, and the user task's TID. Every later
       service assertion is made against this retained cohort, never against
       `_child_processes`, because `_cleanup_children` prunes exited
       services from the table and an assertion over current members would
       miss exactly the early service exit under test.
   - `test_manager_stop_orders_user_work_then_services_then_manager`, one
     lifecycle test sharing that setup. Monkeypatch
     `manager_mod.MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS` to a few seconds,
     as `test_manager_drain_timeout_force_finishes_stubborn_children` does,
     write STOP to the Manager's `ctrl_in`, and drive `process_once` until
     `manager.should_stop`. Three checks:
     1. RED check (services held while user work drains): on every turn
        while the user task's TID is still in `_child_processes`, every
        retained service handle is alive (`_process_running` on its pid).
        From `weft.log.tasks`, no retained service TID has a
        `control_stop` event before the user task's terminal event.
     2. Guard (services stopped after user work): at `manager.should_stop`,
        no retained service handle is alive, and each retained service
        TID's first `control_stop` has a broker message ID greater than the
        user task's terminal event's message ID. Before the change the
        message-ID order is a race and may pass; the check fires on the
        mutation "finish the drain once user work is empty" (services left
        running at `should_stop`).
     3. Guard (Manager event last): the Manager's `manager_stop_drained`
        event has the greatest message ID among the stop's lifecycle events
        for the Manager, the user task, and every retained service. It is
        green before and after, because `_finish_graceful_shutdown` already
        waits for an empty tracked table. It guards event publication order
        only; it does not prove process-exit order, which check 2's
        retained-handle liveness assertion covers.
     Label checks 2 and 3 as guards in the docstring, with the mutation each
     one fires on.
   - Extended fatal-path test (F1): extend the existing sentinel-failure
     regression `test_manager_sentinel_failure_during_shutdown_still_reaps_children`
     (`grep -n "def
     test_manager_sentinel_failure_during_shutdown_still_reaps_children"
     tests/core/test_manager.py`). Add a second fake live child whose
     `ManagedChild.internal_role` is a service role
     (`INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR`), record the order of
     `kill` calls across both fakes, drive the error-path cleanup entry
     that task 3 changes (`Manager._cleanup_task_resources`, with the
     unrelated cleanup steps stubbed as
     `test_manager_cleanup_surfaces_sentinel_failure_published_after_termination`
     does), and assert: the user child's kill precedes the service child's
     kill; the raised exception `is` the original failure; and the tracked
     table is empty. RED today: one unfiltered `_terminate_children`
     iterates children in table order, so insert the service child first.
     The existing assertions stay unchanged.
   - Read first: `wait_for_log_event` and `drain` usage in the anchor tests;
     `tests/tasks/sample_targets.py`.
   - Do not mock the queue, the child process, or `_signal_children_to_stop`
     in the lifecycle test.
   - Verify RED: `./.venv/bin/python -m pytest tests/core/test_manager.py
     -k "orders_user_work_then_services or
     sentinel_failure_during_shutdown_still_reaps" -q` fails on check 1's
     service-alive assertion and on the extended test's kill-order
     assertion, not on setup. Copy both exact failing assertions into the
     execution log.
   - Stop if: RED fails for a setup reason (no service child, no readiness
     marker, a timeout before STOP), or check 1 passes before the change.
     The latter means the user task drains faster than the service; pick a
     slower target, never weaken the assertion.

3. **GREEN: phase the drain.**
   - Outcome: STOP, `SIGTERM`, `SIGINT`, and parent-loss drains run phase 1
     (user work), then phase 2 (services), then `_finish_graceful_shutdown`.
   - Files: `weft/core/manager.py`.
   - Approach, inside the existing `_drain_stops_children` branch only:
     - Add one field, `self._drain_services_phase: bool`, initialized
       `False` beside `_drain_stops_children` in `__init__`. Reset it to
       `False` wherever `_drain_escalated_children.clear()` resets drain
       state: `_begin_shutdown_drain`, `_begin_leadership_drain`,
       `_begin_superseded_shutdown`, `_finish_graceful_shutdown`, and
       `_resume_leadership_after_drain_loss`.
     - Change `_signal_children_to_stop` to take the child mapping to
       signal (`children: Mapping[str, ManagedChild]`) and keep its body.
       `_begin_shutdown_drain` passes `self._user_work_children()`.
     - In `_continue_shutdown_drain`, compute the current phase set:
       `_user_work_children()` while `_drain_services_phase` is false;
       otherwise the supervision-only children. Signal and escalate that
       set only. On window expiry call
       `_terminate_children(deadline, tids=<phase set>)`. Phase 1 ends when
       its set is empty and either `_has_active_child_launches()` is false
       or the phase-1 window has expired (`_shutdown_drain_timed_out()`;
       today's expiry branch also requires `self._child_processes`, and in
       phase 1 that guard becomes "phase 1 is active"). On expiry with a
       launch still active, log a warning naming
       `sorted(self._active_child_launches)` as unsettled and leave that
       bookkeeping in place for final cleanup. Only then set
       `_drain_services_phase = True`, reset
       `_drain_started_ns = time.time_ns()`, and set
       `_drain_immediate_work_pending = True`. Continue in the same call, so
       an empty phase completes at once. Never clear
       `_drain_services_phase` once set: there is no phase-2-to-phase-1
       transition, because every late child comes from a launch that
       settled in phase 1 or was released at its window expiry. When the
       phase-2 set is empty, call `_finish_graceful_shutdown`. That is
       today's empty-table condition unless a released launch committed a
       user-work child, which is not a phase-2 member; it is left to the
       unfiltered pass in `_cleanup_task_resources`.
     - Add a keyword-only `tids: Collection[str] | None = None` to
       `_terminate_children`. `None` keeps today's all-children behavior, so
       the `SIGUSR1` branch stays unchanged.
     - In `_cleanup_task_resources`, replace the single
       `attempt(lambda: self._terminate_children(deadline))` with two
       attempts under the same absolute `deadline`: first
       `self._terminate_children(deadline, tids=tuple(self._user_work_children()))`,
       then `self._terminate_children(deadline)` for everything still
       tracked, services included. Keep the existing `attempt` collection,
       so the first (original) failure is re-raised after both attempts.
       Do not re-enter the phased drain or start a window on this path.
       With `tids`, every all-children step in the body must honor it: the
       initial `children` snapshot, the STOP loop over
       `self._child_processes` before the grace wait, the
       `_wait_for_children_to_exit` condition (it waits while any child
       remains, so in phase 1 it would wait on running services), and the
       `children.update(self._child_processes)` merge. Missing the STOP loop
       sends STOP to services at the phase-1 window expiry.
     - `_manager_drain_timeouts`: publish escalation clocks only for
       signalled, unescalated members of the active phase, and the window
       clock from the (phase-2-reset) `_drain_started_ns` while the active
       phase has a member. In phase 1 an in-flight launch counts as a
       member: publish the window clock whenever phase 1 is active,
       including when only in-flight launches remain, so the reactor wakes
       at the expiry that releases them. In phase 1 a service is never
       signalled, so it has no clock; in phase 2 no user-work child remains
       apart from a released launch's child, which gets no clock.
   - Reuse: `_user_work_children`, `_child_is_supervision_only`,
     `_send_stop_command`, `terminate_process_tree`, `_cleanup_children`.
   - Not allowed: a phase enum or state-machine class; a second escalation
     loop; changing any constant; touching the `SIGUSR1` branch or the
     non-stopping drains.
   - Update test plumbing only: fakes of `_terminate_children` that the
     phase-1 timeout path reaches accept `**_kwargs`. The known case is
     `finish_stubborn_child` in
     `test_manager_drain_timeout_force_finishes_stubborn_children`. The
     cleanup fake `terminate_children(_deadline)` in
     `test_manager_cleanup_surfaces_sentinel_failure_published_after_termination`
     (near line 4241) also gets `**_kwargs`, because the fatal-path split
     passes `tids`.
   - Guard test (named mutation), in `tests/core/test_manager.py` through
     `manager_setup` (no services needed; an empty phase 2 completes at
     once): `test_manager_stop_does_not_hold_past_window_for_stuck_launch`.
     Monkeypatch `manager_mod.MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS` to a
     short value (existing pattern) and `manager_mod.launch_task_process`
     with a function that blocks on a `threading.Event` the test sets in a
     `finally`, so the injected launch worker never completes while the
     drain runs. Submit one spawn request, drive the Manager until
     `_has_active_child_launches()` is true, then send STOP and drive
     `process_once` under an outer bound of a few windows. Assert the
     Manager publishes its drained terminal event (and sets
     `should_stop`) after the window and before the outer bound while the
     worker is still blocked, and that the unsettled-launch warning names
     the launch TID (`caplog`). Named mutation: removing the window bound
     (phase 1 ending only when launches settle) makes STOP hold past the
     window and the test fail at its outer bound. Release the event, then
     let `_cleanup_task_resources` settle and terminate the late child so
     no process survives the test.
   - Verify GREEN: the task 2 tests pass, plus the anchors in `## Testing Plan`.
   - Stop if: a second drain path appears; the superseded or leadership
     drain needs an edit; or a constant needs to change. An in-flight launch
     that has not settled when the phase-1 window expires is not a stop
     gate: it is released as specified above (logged as unsettled, phase 2
     begins, final cleanup terminates any child it later commits). Report
     rather than add a timer.

4. **Traceability links for the ordering (strategy A's linking slice).**
   - Outcome: in the [MA-3] `_Implementation mapping_`, extend the
     "External signal handling" bullet (or add a sibling) to name
     `Manager._begin_shutdown_drain`, `Manager._continue_shutdown_drain`,
     `Manager._signal_children_to_stop`, and `Manager._terminate_children`
     as the owners of phase ordering, `Manager._cleanup_task_resources` as
     the owner of the error-path order, plus the lifecycle test and the
     extended fatal-path test. Add
     `Spec: docs/specifications/03-Manager_Architecture.md [MA-3]` to the
     docstrings of `_continue_shutdown_drain` and `_signal_children_to_stop`.
   - Files: the spec and `weft/core/manager.py` (docstrings only).
   - Verify: `./.venv/bin/python bin/check-doc-paths`. Also run the
     backstitch traceability gate (external tool; no in-repo wrapper exists
     under `bin/`). Require zero errors and zero new warnings, or record in
     the execution log that it was not available.

5. **End-to-end ordering at the CLI seam.**
   - Outcome: `test_manager_stop_orders_user_work_before_internal_services`
     in `tests/cli/test_cli_manager.py`, using `WeftTestHarness` and
     `run_cli`. `WeftTestHarness._patch_environment` sets
     `WEFT_TASK_MONITOR_ENABLED=0`, so pass `env={**os.environ,
     "WEFT_TASK_MONITOR_ENABLED": "1"}` to the `weft manager start` call
     (precedent: `tests/cli/test_cli_serve.py` line 550), and assert that at
     least one service TID (heartbeat or TaskMonitor) appears in
     `weft.log.tasks` before STOP; without one the ordering assertion is
     vacuous. Start a manager with `weft manager start`, submit a
     long-running user task with `weft run --no-wait`, wait for its
     `work_started`, run `weft manager stop`, and assert rc 0 and the
     `weft.log.tasks` order: user terminal event, then each service
     `control_stop`, then the Manager's drained event. The user task's
     fixture must delay its own shutdown after STOP (a script that installs
     a `SIGTERM`-ignoring handler and writes a readiness marker, waited for
     before STOP, as in task 2), so that a simultaneous broadcast cannot
     produce the asserted order by scheduling chance.
   - Verify: `./.venv/bin/python -m pytest
     tests/cli/test_cli_manager.py -k orders_user_work -q`.

6. **(Rejected variant, not run: the owner ruled Reading B on 2026-11-03.
   Kept as the record of Reading A's shape.) Pin the boundary.**
   - Outcome: promote Candidate A (relabel it; add a Revision Log row).
     Add `test_manager_stop_leaves_earlier_manager_detached_task_running`
     to `tests/cli/test_cli_manager.py`: Manager A launches a long task
     with `weft run --no-wait`; the test kills A's process hard with
     `psutil.Process(pid).kill()`, which works on all three OSes; Manager B
     starts with `weft manager start`; after B's first evidence refresh,
     `weft manager stop` succeeds and the task is still running; cleanup
     goes through `weft task stop`.
   - No code change beyond docs; this test is green on arrival and fires
     if a later change widens coverage silently.

7. **(Selected: the owner ruled Reading B on 2026-11-03.) Cover adopted
   live tasks.**
   - RED first: `test_manager_stop_reaches_earlier_manager_detached_task`
     in `tests/cli/test_cli_manager.py`, with the setup task 6 describes
     (Manager A launches the task, A is killed hard with
     `psutil.Process(pid).kill()`, Manager B starts and refreshes evidence).
     After `weft manager stop`, assert the task reaches a terminal state
     (`weft_harness.wait_for_terminal_state`, passing the cancellation
     status explicitly: its default `expected_status` is `"completed"`),
     its process has exited, its terminal event precedes B's drained
     event, and, the delivery oracle, the task's own `control_stop` event
     is in `weft.log.tasks` (the STOP handler at `weft/core/tasks/base.py`
     ~1730 publishes `control_stop`; the SIGTERM handler at ~2243 publishes
     `task_signal_stop` instead). Record the RED assertion: the task is
     still running. Send no control message to the task before the STOP,
     and assert before the STOP that the task's resolved `ctrl_in` name is
     absent from `broker.list_queues()` in the same context (that listing
     includes claimed-row queue names, so it is the condition pattern
     selection sees). The `control_stop` assertion is what makes the test
     fire on the mutation "send the STOP by pattern broadcast
     (`T*.ctrl_in`) instead of per-target writes": the broadcast reaches
     zero queues for a never-controlled task, and without the event
     assertion the planned host SIGTERM escalation would still stop the
     task, exit it and publish its terminal state before B's drained event,
     satisfying every other assertion (cross-family round of 2026-11-03,
     F1). Escalation stays enabled; the event distinguishes STOP delivery
     from the SIGTERM rescue. A per-target write and an exact-name
     broadcast both pass it.
   - RED second (owner ruling 2026-11-03, sub-question (a); fixture
     rewritten after the cross-family round on the sub-question closure,
     F1):
     `test_manager_stop_reaches_task_under_another_live_manager` in
     `tests/cli/test_cli_manager.py`, using the `weft_harness` fixture.
     Setup: two non-canonical Managers, A and B, in one context, each on
     its own test-unique spawn inbox (for example
     `test.stop-cover.<suffix>.a.requests` and `...b.requests`), and no
     canonical Manager. Why neither yields: the constructor's
     `_maybe_yield_leadership(force=True)` (`weft/core/manager.py`, line
     745) reads only canonical records (`_read_active_manager_records`
     delegates to `_active_dispatch_manager_records`, which keeps records
     that `weft/helpers/__init__.py::is_canonical_manager_record` accepts,
     and that requires `requests == weft.spawn.requests`), so with no
     canonical Manager live, `canonical_owner_tid` returns `None` and the
     method returns `False` for both. A non-canonical Manager built beside a
     live canonical one is not usable: childless, it selects
     `manager_leadership_yielded` and sets `should_stop` in its own
     constructor; `test_manager_leadership_ignores_noncanonical_lower_manager`
     in `tests/core/test_manager.py` pins only the reverse direction.
     Neither Manager runs internal services: `_reconcile_managed_services`
     adds heartbeat, TaskMonitor and LivenessMonitor only when the inbox is
     `weft.spawn.requests` (line 7152), and the test declares no autostart
     manifest, so no service convergence ([MA-1.6a]) is in play.
     - Launch path (copied from
       `tests/system/test_family_recovery.py::test_missing_snapshot_crash_preserves_custody_and_descendant_progress`):
       `spec = build_manager_spec(context, tid, idle_timeout_override=0.0)`
       from `weft/core/manager_runtime.py` (TIDs from `generate_tid(context)`;
       an idle timeout of `0.0` disables idle exit), then
       `launch_task_process(Manager, context.broker_target, spec,
       config=context.config)` from `weft/core/launcher.py`, then
       `harness.register_manager_tid(tid)` and
       `harness.register_pid(process.pid, tid)`. The one difference: the
       canonical inbox in `build_manager_spec` is replaced before launch by
       dumping the spec (`model_dump(mode="json")`), setting
       `io.inputs.inbox` to the test-unique name, and re-validating with
       `TaskSpec.model_validate(payload, context={"auto_expand": False})`,
       as `build_manager_spec` itself does. This builds a new spec before
       any process exists, so [IMMUT.1] and [IMMUT.2] are untouched, and it
       needs no production change.
     - Submission path: no `weft run --no-wait`, no `WeftClient.submit`, and
       no `weft manager start`, since each targets `weft.spawn.requests` and
       would start or select a canonical Manager. Each Manager's one long
       one-shot task is written as a raw spawn-request JSON row
       (`{"name": ..., "spec": {"type": "function", "function_target":
       ...}}`) straight onto that Manager's inbox with
       `context.queue(<inbox>).write(...)`, the pattern of
       `tests/core/test_manager.py::test_custom_inbox_manager_does_not_consume_internal_spawn_queue`
       and the spawn-queue tests near its line 8820. Write the row before
       launching its Manager and read the child TID from
       `peek_one(with_timestamps=True)`: the Manager assigns the
       spawn-request message ID as the child TID (`resolved_tid=str(timestamp)`
       in `weft/core/manager.py`, near line 5883). Neither task has an
       `ensure` autostart, so neither Manager restarts anything.
     - Ready state before the stop: wait (on the arbiter-driven harness
       waits, not sleeps) until each task is `running` and each Manager's
       newest checkpoint in `weft.state.tasks.<manager tid>` lists its task
       under `scheduler_frontiers.residents`, as the `_resident` helper in
       `tests/system/test_family_recovery.py` reads it. A's checkpoint is
       the evidence that makes A's task a `covered` resident for B.
     - The stop: `weft manager stop <B tid> --context <root>` through
       `run_cli`. The CLI accepts a non-canonical target (verified by code
       reading, not yet run): `weft/cli/app.py::manager_stop_command` calls
       `weft/commands/manager.py::cmd_manager_stop`, which resolves the
       record with `weft/core/manager_runtime.py::manager_record`
       (`_registry_view(..., target_tid=tid)`, no canonical filter) and
       sends STOP through `manager_runtime.stop_manager`. The test sends no
       other control message to A, B or either task.
     - Assert after the stop: A's task has its own `control_stop` event in
       `weft.log.tasks` (the delivery oracle from the first RED test),
       reaches a terminal state (`weft_harness.wait_for_terminal_state`
       with the cancellation status passed explicitly), and A reaps it (its
       PID no longer exists, which needs A's join because A is its parent,
       and A's newest checkpoint no longer lists it under
       `scheduler_frontiers.residents`), while A keeps running (its process
       is alive and its registry record is still active); B's task is
       terminal too; and B's drained event follows both tasks' terminal
       events by broker message ID. The test then stops A the same way.
     - RED at baseline, a code-derived prediction (the test does not exist
       yet and has not been run): B's STOP drain covers only B's
       `_child_processes`, so nothing writes to A's task's `ctrl_in`; A's
       task keeps running and has no `control_stop` event. The mutation it
       catches: phase-1 membership that excludes the `covered` residents
       (A's task keeps running). Host escalation of covered members is not
       observable here (A's task handles STOP), so the unit test below
       guards it.
     - Stop and report if two live non-canonical Managers cannot be
       launched and fed through the paths above without a new test-only
       production seam (for example, if `build_manager_spec`'s output cannot
       be re-validated with a custom inbox, the seam needed would be an
       `inbox` parameter on `build_manager_spec`), or if either Manager
       yields, stops or starts a service during setup.
   - GREEN, in `weft/core/manager.py`:
     - At `_begin_shutdown_drain`, when `not self._admission_bootstrapped`,
       call `self._refresh_scheduler_evidence()` once. Two traps: the method
       returns at once while `time.monotonic() < self._admission_refresh_after`
       (so a call soon after a failed attempt is a no-op), and its
       bootstrap also restores dead-Manager reserved rows to the inbox and
       publishes a checkpoint. Stop and report if either conflicts with
       [MANAGER.10] or the drain.
     - Phase-1 membership adds adopted targets: TIDs in
       `_scheduler_residents` not in `_child_processes`, excluding the
       Manager row (`role != "manager"`) and excluding only the three
       service roles (heartbeat, TaskMonitor, LivenessMonitor, the same
       set `_child_is_supervision_only` names), whose newest snapshot
       (`read_task_state_snapshot`) is non-terminal. Do not filter on
       `scheduling_class != "internal"`: pipeline stages and edges carry
       `"internal"` and are user work. Take the service role from the
       task-owned snapshot (the runtime task class under
       `INTERNAL_RUNTIME_TASK_CLASS_KEY` in the projection metadata); if a
       snapshot does not carry it, stop and report rather than infer it.
     - Phase-1 membership also adds covered residents (sub-question (a),
       owner ruling 2026-11-03): the TIDs that the `covered` rule of
       `_bootstrap_scheduler_evidence` assigns to another live,
       non-stopping Manager, computed at drain start by that same rule over
       a fresh `latest_task_state_rows` read (the bootstrap's `covered` set
       is local and discarded, so it cannot be reused as stored state).
       They are STOP-drain members only: do not adopt them into
       `_scheduler_residents` or write them to this Manager's checkpoint (no
       admission custody transfer). They get the same service-role
       exclusion, non-terminal snapshot filter, route validation, terminal
       observation, wake interests and window release as other adopted
       targets, read from the task's own snapshot. Host escalation is
       disabled for them: no SIGTERM and no forced reap, whatever their
       `runtime_handle`, because their handles belong to the other
       Manager, which reaps them.
     - Send STOP to the `ctrl_in` from
       `parse_task_observation(payload["observation"])`. When that returns
       `None`, send nothing: log the TID as unresolved routing and release
       it from the phase. [MF-5] forbids a guessed route
       (`docs/specifications/05-Message_Flow_and_State.md`, "Missing or
       invalid nested objects never authorize an older row, history
       fallback, or guessed route"). This differs from a tracked child,
       whose resolved route the Manager already holds.
     - Escalate to SIGTERM, and force-reap at window expiry, only when the
       snapshot `hostname` is this host, a `host_processes` entry's
       `(pid, create_time)` matches `weft.helpers.process_create_time`, and
       `runtime_handle` control authority is `host-pid` (not `runner`,
       `external-supervisor`, `none`, or a missing handle), and the target
       is not a covered resident of another live Manager.
     - Drain integration. Phase 1's completion predicate becomes "no
       tracked user-work child, no unsettled launch (until the phase-1
       window releases it, task 3), and no adopted target with live
       evidence", not "`_child_processes` has no user-work
       child". `_manager_drain_timeouts` publishes escalation clocks for
       signalled adopted targets that are eligible for host escalation, and
       the phase-1 window clock while any adopted target remains in the
       phase, not only while `_child_processes` is non-empty.
     - Completion is observed through the task-owned surfaces: terminal
       task-log events in `weft.log.tasks` and `weft.state.tasks.<tid>`
       snapshots. Register each adopted target's snapshot queue, and
       `weft.log.tasks`, as wake interests on the existing arbiter through
       `_activity_wait_queues`, reading them with a publication cursor as
       `_has_pending_observation` does, and remove them when the target
       leaves the phase. No polling interval ([ALT-WT-002]).
     - An adopted target or covered resident leaves phase 1 on a
       task-owned terminal proof or verified PID exit, whichever Manager
       sent the STOP the task acted on (a dual STOP is idempotent, see the
       sub-question section). At window expiry, an adopted target with unknown
       liveness is logged with its TID and released from the phase, never
       declared dead or exited.
   - Keep this in the phase-1 membership computation from task 3. Do not
     add a second loop.
   - Pipeline membership (confirmed in cross-family round 1):
     `weft/core/tasks/pipeline.py::PipelineTask._submit_child_spawn` submits
     stage and edge spawns on `WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE`, the
     internal-inbox launch path in `weft/core/manager.py` (near line 1458)
     passes `internal=True`, and
     `weft/core/runtime_identity.py::runtime_identity_for_task` assigns
     `scheduling_class == "internal"`. That is why the filter above
     excludes only the service roles.
   - Stop if: the change needs `weft/commands` imports (forbidden in core);
     any deletion of `weft.state.tasks.*` appears; or a wake interest
     cannot be registered on the existing arbiter without a timer.
   - Add a unit test for the filter at the real seam:
     `test_manager_stop_skips_terminal_and_internal_adopted_snapshots`,
     with real snapshot rows written to a real broker through `broker_env`.
     The same test must include an eligible positive target (a live,
     non-terminal user-work snapshot with a resolvable route, plus a
     pipeline-stage snapshot) and assert that it is in phase-1 membership,
     so a no-op implementation cannot pass on the negative cases alone. It
     also includes a covered resident (a resident of a live other
     Manager's checkpoint) that satisfies every other host-escalation
     predicate: its snapshot `hostname` is this host, a `host_processes`
     entry's `(pid, create_time)` matches
     `weft.helpers.process_create_time` for a live process the test owns,
     and its `runtime_handle` control authority is `host-pid`. Beside it is
     an otherwise identical uncovered target (same host, same verified
     identity shape, `host-pid` authority, not listed in any live Manager's
     checkpoint). Assert: the covered resident is a phase-1 member, is not
     eligible for host escalation, and is not in this Manager's
     `_scheduler_residents`; the uncovered target is a member and is
     eligible for host escalation. The named mutation is removing only the
     covered-member exclusion from the escalation predicate, which must make
     the covered resident's not-eligible assertion fail (cross-family round
     on the sub-question closure, F3).

8. **Traceability reconciliation and closeout.**
   - Outcome: the [MA-3] mapping names the coverage owner if task 7 ran;
     `CHANGELOG.md` notes the ordering (and coverage) change; `## Spec Baseline`
     carries the promotion identifier; `Status:` and the
     `docs/plans/README.md` row flip to `completed` in the same change.
   - [WT-7] class-5 completion requirements (`docs/program-theory.md`
     `## Tensions and falsifiers [WT-7]`, "Possession probe, keyed"):
     - The keyed possession probe: the owner writes a fresh key first
       (one hypothetical feature and where it belongs, one category error
       and the entry that refuses it, and the defect class an audit of a
       named subsystem should find). An agent that did not author this
       plan, from a different family, answers from `docs/program-theory.md`
       alone and is graded against the key. The key and result go in
       `## Review Log`. The 2026-11-02 reconciliation plan's spent probe
       does not satisfy this trigger.
     - The [WT-0] theory-versus-code audit table refresh: re-audit the rows
       this plan touches (at least the [WT-2a.13] system-STOP row, I-13 in
       the reconciliation plan's audit) and carry unchanged rows forward by
       reference.
   - Verify: the full gates in `## Verification`.

## Testing Plan

- Harness: a real `Manager` on `broker_env` with the default
  `weft.spawn.requests` inbox and TaskMonitor enabled (see task 2; not
  `manager_setup`, which never launches services), with real
  `spawn`-context children, for turn-level ordering. `WeftTestHarness` with
  `run_cli` for the CLI seam and for the coverage tests, which need two real
  Manager processes.
- Regression anchors that must stay green unchanged:
  `test_manager_stop_command_drains_nonpersistent_children`,
  `test_manager_sigterm_drains_nonpersistent_children`,
  `test_foreground_serve_sigterm_uses_async_drain_path`,
  `test_manager_drain_timeout_force_finishes_stubborn_children` (fake
  signature widened only), `test_manager_drain_reissues_stop_for_child_added_after_stop`,
  `test_manager_parent_loss_enters_graceful_drain_with_live_children`,
  `test_manager_drain_publishes_only_exact_clocks`,
  `test_manager_drain_immediate_continuation_is_one_shot`,
  `test_manager_stop_mid_handler_requeues_reserved_work_unlaunched`,
  `test_manager_sigusr1_keeps_kill_semantics`,
  `test_manager_superseded_self_record_drains_children_without_stopping`,
  `test_manager_superseded_self_record_stops_without_republishing_active`,
  `test_manager_leadership_yield_drains_nonpersistent_children`,
  `test_manager_leadership_drain_resumes_when_leader_proof_disappears`
  (all in `tests/core/test_manager.py`), and
  `tests/cli/test_cli_manager.py::test_manager_start_and_status`.
- Evidence to assert: process liveness (`_process_running`), and ordering
  by broker message ID in `weft.log.tasks`. Message IDs are SimpleBroker
  hybrid timestamps, totally ordered, so no wall-clock comparison is needed.
- Do not mock: queues, child processes, `_signal_children_to_stop`,
  `_continue_shutdown_drain`, or service launch. Monkeypatching a drain
  constant to shorten a test is an existing pattern and allowed.
- Tempting to skip: a launch in flight when STOP arrives. Phase 1 holds
  until it settles or the phase-1 window expires (task 3), and
  `test_manager_drain_reissues_stop_for_child_added_after_stop` already
  covers a child committed after STOP. The bound is guarded by
  `test_manager_stop_does_not_hold_past_window_for_stuck_launch` (task 3);
  it injects a blocking `launch_task_process` for a user-work launch, which
  is not the service launch the next bullet forbids mocking.
- Windows: the suite runs on Linux, macOS, and Windows in CI. The
  `SIGTERM`-ignoring script is a no-op guard on Windows, so the ordering
  assertions must still hold there. If the user task drains too quickly on
  Windows for the service-alive assertion to be meaningful, that assertion
  may be POSIX-gated with `pytest.mark.skipif(os.name == "nt", ...)` and a
  reason. The log-order assertions run on all three OSes. Hard kills use
  `psutil`, not POSIX signals.

## Verification

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/core/test_manager.py -k "stop or drain or supersed or sigusr1 or sigterm or leadership" -q
./.venv/bin/python -m pytest tests/cli/test_cli_manager.py -q
./.venv/bin/python -m pytest tests/specs -q
./.venv/bin/python -m pytest
./.venv/bin/python -m pytest -m ""
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/python bin/check-doc-paths
```

Per-task commands are in each task. The final gate is the full fast suite,
the slow suite (`-m ""`), mypy, ruff, and the doc-path and traceability
gates, all rerun from the current state. Because the suite runs on three
OSes, a local macOS pass is not the completion claim. CI on Linux, macOS,
and Windows must be green for the landing commit.

Runtime observation: `weft manager serve` with `trace` operational logging
shows the drain's phases in the serve log. After `weft manager stop`,
`weft queue peek weft.log.tasks` shows user terminal events, then service
`control_stop`, then the Manager's drained event.

## Rollback

Tasks 2 to 5 are code plus spec text with no persisted format change. Roll
back by reverting the landing commit(s): the drain returns to one broadcast,
and the [MA-3] text returns to the baseline paragraph (revert both together,
so spec and code never disagree). Rollout order does not matter: there is no
queue shape or cross-version message change, and an old CLI stopping a new
Manager sees the same STOP ack and stopped record. Task 7 (reading B) is the
one-way door in operator expectations, not in data: once operators rely on
`weft manager stop` reaching adopted tasks, reverting it silently leaves
work running. Revert it only with a CHANGELOG note.

## Out of Scope

- Changing any drain or escalation constant.
- A suspend primitive. The owner's "(try to) stop/suspend/kill" maps to the
  existing STOP, SIGTERM, and forced-reap rungs. `PAUSE`/`RESUME` controls
  are not shutdown rungs.
- Stopping orphaned internal services left by a dead Manager. Service
  convergence ([MA-1.6a]) owns duplicates.
- Control-reply routing for STOP acks (cleanup plan 3).
- Any change to leadership-yield or supersession semantics.
- Observation for the owner, not a task: explicit `--replace` already sends
  STOP to the active Manager before it writes the supersession row
  (`weft/core/manager_runtime.py::replace_active_manager`, near line 2487).
  That complicates the supersession account in [ALT-WT-007], which says
  explicit replacement and automatic convergence share the non-stopping
  drain. This plan leaves it unchanged.

## Fresh-Eyes Review Checklist

Each instruction should name a file and symbol. The phase field should be
the only new state. The difference register below should hold. Every test
should have a falsifiable RED or be marked as a guard.

Difference register (current versus planned STOP drain):

| Behavior | Today | Planned | Disposition |
|----------|-------|---------|-------------|
| Who gets STOP first | All children in one turn | User work only | Changed (owner rule) |
| Services during user drain | Signalled | Running, unsignalled | Changed |
| Escalation rung per child | STOP, then SIGTERM at 2.0 s | Same, per phase | Preserved |
| Window | One 15 s window | 15 s per phase (worst case about 34 s with forced-reap deadlines) | Changed, constant unchanged |
| Forced reap at expiry | All children | Phase members | Changed |
| In-flight launch at STOP | Not waited for by the drain | Settles in phase 1 before phase 2 | Changed |
| Launch still unsettled at phase-1 window expiry | Not waited for by the drain | Hold ends at the existing window; launch logged as unsettled; phase 2 begins; final cleanup terminates any child it commits | New bound, no new window |
| Return from phase 2 to phase 1 | Not applicable | Never | New rule |
| Fatal-error cleanup | All children together | User work, then the rest, one deadline; original failure re-raised | Changed |
| Terminal event | After the tracked table is empty | Same, except a child committed by a launch released at phase-1 expiry, which final cleanup terminates after the drained event; that child's own lifecycle events may follow the Manager's drained event, and the spec text names this exception | Preserved, one bounded exception, disclosed in the promise |
| `SIGUSR1` | Kill everything now | Same | Preserved |
| Superseded or leadership drain | No signals | Same | Preserved |
| Service restart while draining | Disabled | Same | Preserved |
| Adopted detached tasks (persistent and one-shot) | Not signalled | Signalled in phase 1, drained on task-owned evidence, host escalation only for verified `host-pid` targets | Changed (owner ruling 2026-11-03, Reading B) |
| Tasks under another live Manager in the context | Not signalled | Signalled in phase 1 as STOP-drain members, drained on task-owned evidence; their own Manager keeps custody and reaps them; no host escalation by the stopping Manager; current TIDs only (that Manager may still restart an `ensure` autostart or launch new work) | Changed (owner ruling 2026-11-03) |
| Signalling step for phase 1 | Per-target STOP writes | Per-target STOP writes; pattern broadcast `T*.ctrl_in` evaluated and rejected (misses never-controlled tasks, hits services and Managers); exact-name broadcast recorded as an unadopted fork | Preserved |

## Revision Log

| Date | Change | Reason |
|------|--------|--------|
| 2026-11-03 | Task 1 promotion: Candidate B relabelled `Replacement`; its text appended as the last sentences of item 1 inside the ordering Replacement block, so both blocks are verbatim substrings of [MA-3]. | The owner ruled Reading B and sub-question (a); `## Proposed Spec Delta` says the chosen candidate is inserted as the last sentence of item 1. |

## Review Log

self-driven fresh-eyes review: done 2026-11-02 (distinct pass after drafting): 15 corrections — `_commit_child_launch_success` definition line is 1672 (write at 1686); evidence refresh is rate-gated and adoption skips rows with no `root_tid`; replaced spec span is one paragraph, not two; worst-case drain is about 34 s with forced-reap deadlines, not 30 s; a late child reopening phase 1 cannot withdraw STOP already sent to services (Replacement and task 3 made consistent); task 3 names every all-children step in `_terminate_children` that `tids` must filter; task 2 tests cannot use `manager_setup` (its custom inbox launches no services) and now construct a TaskMonitor-enabled Manager; only the first task 2 test is required RED, the other two are named-mutation guards; task 5 re-enables TaskMonitor (the harness disables it) and asserts a service exists; Reading B host escalation is limited to `host-pid` authority (LIVENESS.R5 covers `runner` too); task 7 records evidence that its pipeline stop gate will likely fire; task 7 names the refresh gate and bootstrap side effects; risk 5 states that adopted-task exit has no drain wake source; Replacement list gets blank lines so the trailing paragraph does not render inside item 3; stale index-row note corrected (a fresh-eyes finding for the owner is added at the coverage decision)

- Cross-family review round 1 (codex 0.161.0, 2026-11-02, whole plan after the fresh-eyes pass): BLOCKED. F1 (P1, fatal cleanup bypasses the phases), F2 (P1, reopen rule leaves paused service clocks at 0.0; replaced by holding phase 1 while launches are in flight, backward transition removed), F4 (P1, Reading B drain integration unspecified: membership, completion predicate, clocks, wake interests), F3 (test setup and oracles), F5 (guessed route fallback removed per [MF-5]), F6 (non-stopping sentence narrowed to user work), F7 ([WT-7] closeout): all accepted and applied. Pipeline membership confirmed internal; Task 7 filter changed to service roles. Round 2 (codex 0.161.0, 2026-11-02, scoped to F1–F6, F7 confirmed): F1, F3, F4, F5, F6 closed with live-code evidence; F2 returned one residual (the in-flight-launch hold had a wake source but no bound: the stale-launch clock stays empty while a launch worker is active and the launcher starts without a timeout), applied here without a further round under the one-scoped-round rule: the phase-1 window now bounds the hold and an unsettled launch is released to final cleanup. Review loop closed; the plan waits on the owner's coverage decision.
- Owner ruling applied 2026-11-03: STOP coverage ruled Reading B ("it applies to all tasks"); task 7 selected, task 6 kept as the rejected variant; persistent adopted tasks covered by the ruling. The owner's framing of STOP as a pattern broadcast was evaluated for the signalling step (SimpleBroker 8.7.2 `broadcast(pattern=...)`, already used by `weft queue broadcast`) and not adopted: patterns select only queues that hold a row and never create one, so a probe against a live system returned 0 targets for `T*.ctrl_in` with a live never-controlled task running, and the pattern also matches service and Manager control queues. Per-target writes stay for signalling; an exact-name `create_missing=True` broadcast is recorded as an unadopted fork; membership kept for the drain. One sub-question recorded (tasks under another live Manager in the same context; recommend include, assume exclude until ruled). Cross-family round 1 on the change (codex 0.161.0, 2026-11-03): BLOCKED on F1 (P2, the new guard could pass under the pattern-broadcast mutation because the host SIGTERM escalation would still stop the task): accepted, the guard now requires the task's own `control_stop` event, asserts the resolved `ctrl_in` is absent from `list_queues()`, and passes the cancellation status explicitly. F2 (P2, option (a)'s consequences understated: the other Manager does not escalate an unhandled STOP and may restart an `ensure` autostart; (a) stops current TIDs, it does not make the context quiescent): accepted as disclosure under the sub-question, which stays with the owner. The exact-name broadcast fork: the round recommends keeping per-target writes for this plan (the saving is N−1 transactions for the initial cohort with N unmeasured; one broadcast rolls back the whole set on failure where separate writes are best-effort per target); the fork stays recorded for the owner with that note. The round confirmed the pattern-broadcast rejection against the installed `_select_broadcast_queues` (`SELECT DISTINCT queue FROM messages`). Two citation observations applied (`PipelineTask._submit_child_spawn`, line 539; the terminal-wait helper's default status). Round 2 (codex 0.161.0, 2026-11-03, scoped to F1 and F2, fork note confirmed): no blocker; F1 closed (base.py:1730 publishes `control_stop` for STOP and base.py:2262 publishes `task_signal_stop` for SIGTERM, so the rescue cannot satisfy the event assertion), F2 closed (manager.py:8691 gates shutdown escalation on the Manager's own drain state; manager_services.py:255 permits an `ensure` restart under backoff and limits). Review loop on the coverage change closed; the plan waits on the owner's sub-question (tasks under another live Manager; recommendation now: leave them to that Manager, the assumed (b)) and the recorded exact-name broadcast fork (recommendation: keep per-target writes).
- owner ruling applied 2026-11-03: the other-live-Manager sub-question closed as (a) ('Dual STOP in the case of two live managers doesn't hurt anything'); covered residents join phase 1 as STOP-drain members, host escalation disabled for them; the disclosed consequences stand (current TIDs stop; the context is quiet only when that Manager stops too); one two-Manager CLI test added. Cross-family round on the change pending.
- Cross-family review round 1 on the sub-question closure (codex 0.161.0, 2026-11-03): BLOCKED on F1 (P2, the two-Manager fixture could not keep A alive: a non-canonical Manager constructed beside a canonical one yields leadership in its constructor): accepted, the test now uses two non-canonical Managers on separate inboxes with no canonical Manager and an explicit STOP of B. F2 (P2, the Consumer keeps its first deferred STOP; a second queued STOP is not read while it is pending): accepted, explanation corrected. F3 (P2, the no-escalation assertion could pass for unrelated reasons): accepted, the fixture now holds a same-host verified identity with host-pid authority beside an eligible uncovered target. The older Reading B narrowing rewritten to the ruled membership. Round 2 (codex 0.161.0, 2026-11-03, scoped to F1–F3, Reading B wording confirmed): no blocker; F1 closed (manager.py:3724 rejects non-canonical leadership candidates, manager.py:4069 permits custom-inbox dispatch, manager_runtime.py:888 resolves an explicit stop target without the canonical filter), F2 closed (consumer.py:637 returns before another control read; finalization applies the first request once), F3 closed (the fixture's verified identity and host-pid authority match LIVENESS.R5). Review loop on the sub-question closure closed; the plan waits on nothing but the recorded exact-name broadcast fork (recommendation: keep per-target writes).

Index note for the landing change: `tests/specs/test_plan_metadata.py`
requires a `docs/plans/README.md` Status Index row (`draft`) for this file.
The row is present in the working tree and must land in
the same change as this file.
- cross-plan correction applied 2026-11-03 (owner's outside reviewer; owner: 'I accept the disclosed bounded-shutdown choice; I would tighten the promise'): the spec delta's unconditional "the last lifecycle event of the stop" now reads as the last event the Manager publishes for the stop, with the released-launch child named as the one exception whose own events may follow (final cleanup runs after the drained event, manager.py:4908 then `_finalize_task_once`); the kept-sentence note and the register row say the same. Plan metadata gate and `bin/check-doc-paths` re-run.
- implementation 2026-11-03: the exact-name broadcast fork was closed on the owner's implement instruction (2026-11-03) with the recommended option: keep per-target STOP writes (the probe showed patterns match only queues that already hold rows). Tasks 1 to 5, 7 and 8 executed from `26cc08da`; task 6 is the rejected variant and was not run.

## Execution log

Plan-authoring evidence (2026-11-02, worktree at `b7eed9f9`, clean):

- `git log -1 --format=%h` printed `b7eed9f9`.
- `grep -n "_signal_children_to_stop\|_child_is_supervision_only\|_continue_shutdown_drain" weft/core/manager.py`
  found definitions at lines 4770, 2164, and 8691.
- `grep -cE "_child_processes\[[^]]+\] = " weft/core/manager.py` printed
  `1` (line 1686, `_commit_child_launch_success`).
- `grep -n "_adopt_scheduler_evidence\|def _bootstrap_scheduler_evidence\|def _recover_scheduler_paths" weft/core/manager.py`
  found 2252, 2522, and 2614. The bootstrap reads `latest_task_state_rows`
  inside `_refresh_scheduler_evidence` (line 2469).
- `sed -n 788,816p docs/specifications/03-Manager_Architecture.md`
  confirmed the replaced span at lines 801 to 812.
- `grep -n "LIVENESS.R[3-7]" docs/specifications/07-System_Invariants.md`
  resolved [LIVENESS.R3] (697), [LIVENESS.R4] (726), LIVENESS.R5 (741),
  LIVENESS.R6 (746), and [LIVENESS.R7] (750).
- `grep -n "persistent" docs/specifications/02-TaskSpec.md` gave line 85,
  the `persistent` definition in [TS-1].
- `grep -n "def test_.*stop\|def test_.*supersed\|def test_.*drain\|def test_.*sigusr1" tests/core/test_manager.py`
  resolved every anchor named in `## Testing Plan`.
- `grep -n 'monkeypatch.setattr(manager, "_terminate_children"\|"_terminate_children",\|manager._terminate_children = ' tests/core/test_manager.py | wc -l`
  printed `9`.
- `ls bin/check-doc-paths tests/specs/test_plan_metadata.py .envrc` showed
  all three present.
- New test names in tasks 2, 5, 6, and 7 are intentionally new; the task 2
  fatal-path test extends an existing one by name.

Implementation evidence (2026-11-03, `main` at `26cc08da`, clean tree; plans
10, 9, 5, 2 and 3 landed after the authoring baseline `b7eed9f9`; every line
citation below was re-resolved by symbol before editing):

Task 0, comprehension answers (before the first edit):

1. `Manager._begin_superseded_shutdown` sets `_drain_stops_children = False`
   (verified at `26cc08da`), and `Manager._continue_shutdown_drain` calls
   `_signal_children_to_stop` only inside `if self._drain_stops_children`.
   Supersession changes who takes future work and never stops running work
   ([ALT-WT-007]; [WT-2a.13], first sentence). The phases live entirely
   inside the `_drain_stops_children` branch, so the superseded and
   leadership drains are untouched.
2. No. `_finish_graceful_shutdown` is reached only once the active phase has
   no member, and phase 2 (the services) begins only after phase 1, so a
   TaskMonitor child still in `_child_processes` blocks the drained event.
   Phase 2 resets `_drain_started_ns` because phase 1 may spend the whole
   `MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS` (15.0 s) forcing user work down;
   that constant's docstring sizes the budget for services to "deliver STOP,
   publish terminal service state" on slow backends, so a shared window
   would leave services no time and force-kill them. Worst case is
   15 + 2 + 15 + 2 = 34 s against `MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS`
   (15.0 + 40.0 = 55 s).

Task 1, spec promotion (strategy A):

- The replaced span in `docs/specifications/03-Manager_Architecture.md`
  [MA-3] was re-read at `26cc08da` and matched the quoted baseline text
  exactly (from "`SIGTERM` and `SIGINT` against a Manager enter the same
  drain path as STOP." to "scheduler delay under release-load
  parallelism."), so no clause re-audit was needed. The ordering
  Replacement, with Candidate B appended to item 1 (`## Revision Log`),
  replaced it verbatim; the "Voluntary leadership drain ..." and "Reader
  omission ..." paragraphs are untouched. `## Related Plans` gained the
  backlink. No `_Implementation mapping_` claim was added (task 4).
- Plan `Status:` and its `docs/plans/README.md` row moved to `promoted`, so
  the Replacement-sync gate now checks both blocks.
- `./.venv/bin/python bin/check-doc-paths`: `check-doc-paths: OK`.
  `./.venv/bin/python -m pytest tests/specs -q`: all passed.

Task 1 commit: `2029b2b4` ("Promote the phased system STOP drain into
[MA-3]") is the promotion baseline identifier. The fast suite before it
exited 0 (6,378 tests passed, 47 skipped; this repository's `-q -ra` run
prints the skip list and no pass-count line, so the count is from the
progress dots).

Task 2, RED (before any production edit, `-n 0`):

```
./.venv/bin/python -m pytest tests/core/test_manager.py -k "orders_user_work_then_services or sentinel_failure_during_shutdown_still_reaps" -q -n 0
>   assert kill_order == ["child", "service"]
E   AssertionError: assert ['service', 'child'] == ['child', 'service']
>   assert _process_running(process.pid), (
E   AssertionError: service 1793750792389971968 exited while user work 1793750792391716864 was still tracked
```

Both fail on the named assertions (check 1's service-alive assertion and
the extended test's kill-order assertion), not on setup: the readiness
marker and a service child were both reached.

- `test_manager_stop_orders_user_work_then_services_then_manager` is placed
  after `test_manager_stop_command_drains_nonpersistent_children`. The
  user task is a `command` task running the SIGTERM-ignoring script from
  `_write_term_trapping_descendant_scripts`; readiness waits for its PID
  marker, a supervision-only child, and no launch in flight. Log reads peek
  `weft.log.tasks` with `include_claimed=True`, consuming nothing.
- The extended fatal-path test now inserts a TaskMonitor-role fake first,
  drives `Manager._cleanup_task_resources` with the unrelated steps stubbed
  as in `test_manager_cleanup_surfaces_sentinel_failure_published_after_termination`,
  keeps its three existing assertions and adds the kill-order assertion.

Task 3, GREEN (`weft/core/manager.py`):

- One new field, `_drain_services_phase`, initialized beside
  `_drain_stops_children` and reset at every `_drain_escalated_children.clear()`
  site (`_begin_shutdown_drain`, `_begin_leadership_drain`,
  `_begin_superseded_shutdown`, `_finish_graceful_shutdown`,
  `_resume_leadership_after_drain_loss`).
- `_signal_children_to_stop(children)` takes the phase's mapping, body
  unchanged; `_begin_shutdown_drain` passes `_user_work_children()`.
  `_drain_phase_children()` returns user work in phase 1 and the
  supervision-only children in phase 2 (it reuses
  `_child_is_supervision_only`; no second classifier).
- `_continue_shutdown_drain` keeps the non-stopping branch's behavior and,
  for a stopping drain, runs phase 1 through `_drain_user_work_phase_holds`
  (extracted only to keep `_continue_shutdown_drain` under ruff's C901
  limit): signal and reap user work; hold while user work or an in-flight
  launch remains and the window is open; at expiry force-reap user work
  with `_terminate_children(..., tids=...)` and log unsettled launch TIDs;
  then set the phase, reset `_drain_started_ns`, set
  `_drain_immediate_work_pending`, and continue in the same call into
  phase 2 (signal, reap, forced reap of services at its window, then
  `_finish_graceful_shutdown` once no service remains).
- `_terminate_children(deadline, *, tids=None)` filters the initial
  snapshot, both merges, the STOP loop and `_wait_for_children_to_exit`.
  `_cleanup_task_resources` runs two attempts under one deadline: user
  work, then everything still tracked.
- `_manager_drain_timeouts` publishes the window clock while the active
  phase has a member (in phase 1 an in-flight launch counts) and
  escalation clocks only for active-phase members.
- Test plumbing: `finish_stubborn_child`, the cleanup fake
  `terminate_children`, and `fail_if_signal_handler_terminates_children`
  (mypy flagged its narrower signature) accept `**_kwargs`.
  `test_manager_cleanup_attempts_every_phase_and_raises_first_failure`
  now expects `"children"` twice in its call order: that is the plan's
  two-attempt split itself, not a weakened assertion (deviation 2 below).
- Guard `test_manager_stop_does_not_hold_past_window_for_stuck_launch`
  added (blocked `launch_task_process`, 0.5 s window, outer bound of six
  windows plus 5 s).
- GREEN: the task 2 tests, the guard, and every `## Testing Plan` anchor
  pass (`-k "stop or drain or supersed or sigusr1 or sigterm or leadership
  or sentinel_failure or cleanup_attempts or cleanup_surfaces"`: 59
  passed); `tests/core/test_manager.py` and `tests/cli/test_cli_manager.py`
  exit 0; mypy clean on 484 files; ruff clean.

Deviations recorded during tasks 2 and 3:

1. The lifecycle test keeps `MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS` at
   15.0 instead of "a few seconds". Measured: the user task leaves the
   table in about 0.3 s, but in one run of four TaskMonitor's own
   `control_stop` arrived 10.35 s after the STOP. TaskMonitor's STOP
   handler (`weft/core/monitor/task_monitor.py::TaskMonitor._handle_control_command`)
   calls `_cancel_heartbeat`, which calls
   `weft/core/heartbeat.py::cancel_heartbeat`, which calls
   `ensure_heartbeat_service` (default `startup_timeout` is
   `MANAGER_STARTUP_TIMEOUT_SECONDS`, 10.0 s, and it calls
   `manager_runtime.ensure_manager`). When the heartbeat stops in the same
   phase it can wait out that timeout. With a 6 s window the Manager
   force-reaped TaskMonitor and its `control_stop` never appeared. This is
   pre-existing (before this plan both services were stopped in the same
   turn too) and is reported, not fixed: it is outside this plan's files
   and scope.
2. `test_manager_cleanup_attempts_every_phase_and_raises_first_failure`
   asserts the cleanup call order; the plan's split adds a second
   `_terminate_children` attempt, so its expected list gains a second
   `"children"` entry. Its other assertions are unchanged.
- Gates before the task 2 and 3 commit: fast suite exit 0 (6,380 passed,
  47 skipped, from the progress dots); mypy `Success: no issues found in
  484 source files`; ruff `All checks passed!`; `bin/check-doc-paths` OK.

Task 2 and 3 commit: `80891922`.

Task 4, traceability links for the ordering:

- [MA-3] `_Implementation mapping_` gains a "System STOP phase ordering"
  bullet beside "External signal handling", naming
  `Manager._begin_shutdown_drain`, `Manager._continue_shutdown_drain`,
  `Manager._drain_user_work_phase_holds`, `Manager._drain_phase_children`,
  `Manager._signal_children_to_stop`, `Manager._terminate_children`,
  `Manager._manager_drain_timeouts`, the error-path owner
  `Manager._cleanup_task_resources`, and the three tests.
- `Spec: docs/specifications/03-Manager_Architecture.md [MA-3]` added to
  the docstrings of `_continue_shutdown_drain`, `_signal_children_to_stop`,
  `_drain_user_work_phase_holds`, `_drain_phase_children` and
  `_terminate_children` (beside its existing [IMPL.10] reference).
- `bin/check-doc-paths`: OK. The backstitch traceability gate was not
  available: no `backstitch` executable is on `PATH` and no in-repo wrapper
  exists under `bin/`.

Task 5, end-to-end ordering at the CLI seam:

- `tests/cli/test_cli_manager.py::test_manager_stop_orders_user_work_before_internal_services`
  starts the Manager with `weft manager start` and
  `WEFT_TASK_MONITOR_ENABLED=1`, submits a SIGTERM-ignoring `weft run
  --no-wait` command task that writes its readiness marker after installing
  the handler, waits for the marker and for at least one internal service
  in `weft.state.services`, runs `weft manager stop` (rc 0), and asserts:
  user terminal, then each service's terminal (`control_stop`), then the
  Manager's `manager_stop_drained` event and terminal snapshot.
- Deviation 3: the order oracle is the task-owned terminal snapshot in
  each task's `weft.state.tasks.<tid>` queue (broker message IDs), not the
  services' rows in `weft.log.tasks`, and the service TIDs come from the
  `weft.state.services` registry. Observed: in two of four early runs the
  services' `weft.log.tasks` rows were absent while their registry rows
  existed, even with `include_claimed=True`. TaskMonitor collates and
  retires internal-service lifecycle rows from the raw task log
  (`INTERNAL_SERVICE_RUNTIME_CLASSES`, "classified as internal service
  lifecycle rows"), so that log is not a stable oracle for services. The
  terminal snapshot is published by the task itself before its terminal
  log event (`BaseTask._publish_projection_edge`) and only LivenessMonitor
  deletes it ([LIVENESS.R3]; disabled under the harness). The drained
  event itself is still read from `weft.log.tasks`.
- Verify: `./.venv/bin/python -m pytest tests/cli/test_cli_manager.py -k
  orders_user_work -q`: five consecutive runs passed. Against the
  pre-phase `weft/core/manager.py` (from `2029b2b4`, restored afterwards)
  it failed twice in two runs on `assert user_terminal[0] <
  service_terminal[0]` (for example `assert 1793752575788134400 <
  1793752575540375552`), so it is a real ordering check, not a guard.

Task 4 commit: `444a74bd`. Task 5 commit: `a1472d98` (the fast suite before
both exited 0: 6,381 passed, 47 skipped; mypy clean on 484 files; ruff
clean).

Task 7 (Reading B), RED first (`-n 0`, before any task 7 production edit):

```
./.venv/bin/python -m pytest tests/cli/test_cli_manager.py -k "reaches_earlier_manager or reaches_task_under_another" -n 0
E   TimeoutError: Timed out waiting for task 1793753041121116160 terminal state 'cancelled'
E   TimeoutError: Timed out waiting for task 1793753113767510016 terminal state 'cancelled'
FAILED tests/cli/test_cli_manager.py::test_manager_stop_reaches_earlier_manager_detached_task
FAILED tests/cli/test_cli_manager.py::test_manager_stop_reaches_task_under_another_live_manager
```

Both fail on the named RED (the task keeps running: its latest snapshot
stays at `work_started`), not on setup: Manager B adopted the detached task
into its checkpoint before the stop, the task's `ctrl_in` was absent from
`list_queues()`, and in the second test both non-canonical Managers
launched, listed their tasks under `scheduler_frontiers.residents`, and
neither yielded, stopped, or started a service; `weft manager stop <B tid>`
returned 0. No test-only production seam was needed (`build_manager_spec`
output re-validated with the custom inbox).

GREEN (`weft/core/manager.py`):

- `_begin_shutdown_drain` runs `_refresh_scheduler_evidence()` once when
  `_admission_bootstrapped` is false, then `_collect_drain_stop_targets()`.
  Neither trap fired: the refresh's rate gate only makes the call a no-op
  after a recent failed attempt, and its bootstrap side effects (restoring a
  dead Manager's reserved rows to the inbox, publishing a checkpoint) launch
  nothing, so [MANAGER.10] holds.
- `_collect_drain_stop_targets` reads one fresh `latest_task_state_rows`
  pass and computes `covered` by the bootstrap's rule, now shared through
  `_manager_checkpoint_residents` (a mechanical extraction from
  `_bootstrap_scheduler_evidence`, same behavior). Members are adopted
  residents (`_scheduler_residents`) and covered residents, excluding this
  Manager, its tracked children and in-flight launches, Manager rows,
  terminal snapshots, and the three service runtime classes read from
  `projection.metadata[INTERNAL_RUNTIME_TASK_CLASS_KEY]` (a probe on the
  task 7 working tree: a real heartbeat and TaskMonitor snapshot carry
  `heartbeat` and `task_monitor` there, so the stop gate did not fire).
  Pipeline stages and edges are members. A member with no snapshot or no
  `parse_task_observation` route is logged as unresolved and gets no STOP.
  Covered residents are never written to `_scheduler_residents` or the
  checkpoint.
- Host escalation (`_ManagerDrainStopTarget.escalate`): same hostname, a
  `host_processes` entry whose `(pid, create_time)` is live and matches,
  `runtime_handle` control authority `host-pid`, and not covered. The
  identity is re-verified before each SIGTERM and forced reap.
- `_signal_children_to_stop` keeps one loop: it builds one rung list from
  the phase's children and, in phase 1, the targets (STOP to the resolved
  `ctrl_in`; SIGTERM only to re-verified pids of eligible targets).
- `_drain_user_work_phase_holds` settles targets each turn
  (`_settle_drain_stop_targets`: terminal snapshot or verified exit) and
  holds phase 1 while any remains; at window expiry
  `_release_drain_stop_targets_at_expiry` force-reaps eligible targets,
  settles once more, and logs the rest as released with liveness unknown.
- `_manager_drain_timeouts` publishes the phase-1 window while a target
  remains and escalation clocks only for eligible targets, through
  `_drain_phase_members` (extracted to keep the method under ruff's C901
  limit).
- Wake interest: `_activity_wait_queues` adds each target's
  `weft.state.tasks.<tid>` queue and `_has_pending_observation` reads a
  publication cursor on it (one delimited line each, so plan 6 can add its
  `weft.state.heartbeats` interest beside them). Queues close on release,
  at `_finish_graceful_shutdown`, and in `_cleanup_task_resources`.
- Unit test `test_manager_stop_skips_terminal_and_internal_adopted_snapshots`
  writes real snapshot rows (a live user task, a pipeline stage, a covered
  resident of a live peer Manager checkpoint with the same verified
  same-host `host-pid` identity, a terminal row, a TaskMonitor row, an
  unresolvable row). Named mutation run: deleting `and not is_covered` from
  the escalation predicate fails it with `assert True is False` on the
  covered target's `escalate`.
- GREEN: both CLI tests pass (three further consecutive runs of the three
  CLI ordering and coverage tests: 3 passed each time); the unit test and
  the drain anchors pass; mypy clean; ruff clean.

Deviations in task 7:

4. Wake interest covers each target's snapshot queue only, not
   `weft.log.tasks`. The terminal snapshot is the task-owned proof the drain
   releases on, and `BaseTask._publish_projection_edge` publishes it before
   the terminal log event; a `weft.log.tasks` interest would wake the
   Manager on every row in the context and finding a target's terminal
   event there would be the shared-log scan [ALT-WT-009] rejects. A task
   killed without a terminal snapshot is released on verified PID exit at
   the next turn or at the window.
5. The first CLI test's detached task is a `command` task (`python -c
   "import time; time.sleep(120)"`) rather than a function task: the CLI
   `--kw` passes strings.
6. In-flight launches are excluded from targets (they settle as tracked
   children through task 3's hold), and a resident with no snapshot is
   reported with the unresolved-route TIDs.
- Gates before the task 7 commit: fast suite exit 0 (6,384 passed, 47
  skipped); mypy `Success: no issues found in 484 source files`; ruff
  `All checks passed!`; `pgrep -fl weft` showed no leftover process.

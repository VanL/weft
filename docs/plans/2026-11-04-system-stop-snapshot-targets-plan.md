# System STOP Targets Every Live Task by Snapshot

Status: draft
Source specs: docs/specifications/03-Manager_Architecture.md [MA-3]; docs/specifications/07-System_Invariants.md [MANAGER.12]
Superseded by: none

Class: 5 (spec-changing) with a risky trigger. The [MA-3] implementation
mapping sentence for `Manager._collect_drain_stop_targets` changes, so a
normative spec file is edited (clarification of ownership, strategy B).
The code change is on the Manager's system-STOP drain, which sits on the
durable spine, so the hardening-plans checklist applies in full and review
happens before implementation. Not +P. Plan type: implementation with
spec revision. One-way doors: none.

This plan follows a finding reported by an outside agent on 2026-11-04 and
reproduced by the series lead the same day (`## Context`, "The defect,
reproduced"). The owner's framing: "The stop is broadcast ... We don't need
to care who manages a task." Plan 4
(`docs/plans/2026-11-02-system-stop-ordering-plan.md`, promoted) evaluated
the queue-name pattern form of that broadcast and declined it on three
verified grounds; this plan keeps the owner's principle and realizes it by
task identity instead.

## 1. Goal

Make a system STOP reach every live user-work task in the scoped context
regardless of which Manager launched it and whether that Manager is still
alive. Today `Manager._collect_drain_stop_targets` builds its phase-1
members from two sets only: the stopping Manager's own residents and the
residents listed in checkpoints of Managers it judges live at that moment.
A task whose launching Manager was alive when the stopping Manager
bootstrapped (so the task was "covered", not adopted) and has since died is
in neither set, and the STOP misses it. The fix takes every live user-work
snapshot in `weft.state.tasks.*` as a candidate, keeps the existing
"covered by a live peer" verdict only for what it is still needed for (no
host escalation against a task another live Manager owns), and sends the
first STOP rung as one atomic exact-name broadcast through SimpleBroker,
creating the control queue for a never-controlled task. The [MA-3] text
already promises this coverage; only its implementation mapping changes.

## 2. Source Documents

Source specs:

- `docs/specifications/03-Manager_Architecture.md` [MA-3], lines 831-840
  (at `e0290792`): "Phase 1 also covers every live, user-work task in the
  scoped context. The Manager finds these tasks through task-owned
  snapshots, the checkpoints of earlier Managers that are no longer live,
  and the checkpoints of other live Managers, including a superseded
  Manager still draining ([MF-5]). ... A task another live Manager covers
  is a STOP-drain member only: that Manager keeps its custody and its
  handles and reaps it, and the stopping Manager never sends it a host
  signal." This sentence is unchanged by the plan; it is the contract the
  code falls short of.
- `docs/specifications/03-Manager_Architecture.md` [MA-3] implementation
  mapping, lines 960-963: the `Manager._collect_drain_stop_targets` entry,
  which today says "adopted residents and residents other live Managers
  cover". This is the sentence the delta replaces.
- `docs/specifications/07-System_Invariants.md` [MANAGER.12] (leadership
  yield is advisory) and [WT-2a.13] in `docs/program-theory.md` (system
  STOP is the Manager's responsibility to stop active tasks, then internal
  services, then itself). Both unchanged.
- SimpleBroker 8.7.2, `simplebroker/db.py:3747` `BrokerDB.broadcast(message,
  *, pattern=None, queue_names=None, create_missing=False)`, contract
  `../simplebroker/docs/specs/12-broadcast.md` [SB-BCAST-1] to
  [SB-BCAST-4]: a pattern selects existing queue names only and never
  creates a queue; exact `queue_names` with `create_missing=True` creates
  the missing ones; the write is atomic over the selected set.

Origin and prior evaluation (context, not normative):

- `docs/plans/2026-11-02-system-stop-ordering-plan.md` (plan 4, promoted),
  `### Signalling by pattern broadcast: evaluated` (lines 748-800): the
  `broadcast(stop, pattern="T*.ctrl_in")` form was probed on 2026-11-03
  and declined because (1) a pattern reaches only queues that already hold
  a row, and a never-controlled task has none (the probe returned 0 with a
  live task running); (2) the pattern also matches the services' and other
  Managers' control queues; (3) it misses non-default control routes. The
  exact-name form with `create_missing` has none of these properties and
  is already used by `weft/core/tasks/base.py::BaseTask._report_state_change`
  for the completion fact.

Theory (governing, unchanged): [WT-2a.13]; [WT-2a.2] (queues are the only
durable truth: the task's own snapshot, not a Manager's memory, names the
task); [WT-4] "Availability and death are different questions" (an
unknown target is held, never declared exited); [ALT-WT-015] (Manager-local
child tracking is not the whole system).

Guidance the implementer must follow: `docs/agent-context/runbooks/writing-plans.md`,
`hardening-plans.md`, `review-loops-and-agent-bootstrap.md`,
`skills/call-agent/SKILL.md`, `docs/agent-context/decision-hierarchy.md`
[DOM-15], `CLAUDE.md` §4 and §7 (gates now include `./bin/pytest-pg`, the
owner's rule of 2026-11-04).

## 3. Context and Key Files

### Current structure (what exists at `e0290792`)

Line numbers were verified by `grep -n` and `sed -n` on 2026-11-04;
re-verify before editing.

The drain entry. `Manager._begin_shutdown_drain` (`weft/core/manager.py`
5234-5272) sets the drain flags, applies the reserved policy, runs the
scheduler bootstrap if it has not run, then calls
`_collect_drain_stop_targets()` and `_signal_children_to_stop(self._user_work_children())`.

Target collection. `_collect_drain_stop_targets` (4926-5010):

1. One fresh pass: `latest_task_state_rows(...)` returns every task's
   newest valid snapshot keyed by TID, including Managers' own rows (role
   `manager`) whose payload carries `scheduler_frontiers.residents`, the
   checkpoint.
2. `_manager_checkpoint_residents(snapshots)` (2597-2620) yields
   `(live_peer, residents)` per Manager checkpoint; `live_peer` is true when
   the checkpoint belongs to another Manager and
   `_admission_mapping_is_live(manager_tid, payload)` says it is live.
   `covered` collects the residents of live peers only.
3. `candidates = {tid: tid in covered for tid in self._scheduler_residents}`
   then `candidates.update(dict.fromkeys(covered, True))`. This is the
   narrow step: a task is a candidate only if the stopping Manager adopted
   it at its bootstrap or a peer judged live right now lists it.
4. Each candidate is dropped if it is this Manager, in the child table, or
   an active launch; its snapshot must exist (else `unresolved`), pass
   `_is_live_user_work_snapshot` (not terminal, not role `manager`, not an
   internal service class), and carry an observation with a `ctrl_in`
   route (else `unresolved`). `_verified_host_identity` gives the
   same-host `(pid, create_time)` pairs; `escalate` is true only for an
   uncovered target with host-pid authority.
5. The settlement cursor and log wake are set from the targets.

Signalling. `_signal_children_to_stop` (4861-4910) builds "rungs" from the
child table and, in phase 1, from `_drain_stop_targets`; for each rung not
yet signalled it calls `_send_stop_command(ctrl_queue)` (one `broker.write`
per target through `_send_child_control_command`, 5388-5399) and records
the time; after `MANAGER_CHILD_STOP_ESCALATION_SECONDS` an unsettled,
escalation-eligible rung gets SIGTERM via `terminate_process_tree`. The
write creates the control queue when it does not exist, which is why the
per-target path reaches a never-controlled task.

Liveness verdicts. `_admission_mapping_is_live` memoizes per TID: a dead
verdict is permanent while the row's handle and terminal hint are
unchanged; a live verdict is reused for `MANAGER_ADMISSION_RECHECK_SECONDS`
(1.0 s, `weft/_constants.py` 822) before re-probing with
`mapping_row_is_live` (`weft/liveness/policy.py`), which probes the
checkpoint's scoped host processes.

Bootstrap adoption. `_bootstrap_scheduler_evidence` (2576-2595) adopts the
residents of dead Managers' checkpoints and every task snapshot no live
peer covers; a task a live peer covers is not adopted. It runs once per
Manager incarnation. Nothing adopts a task later.

### The defect, reproduced

Two real Managers in `WeftTestHarness` (the shape of
`tests/cli/test_cli_manager.py::test_manager_stop_reaches_task_under_another_live_manager`,
line 614): A started first and listing its task in its checkpoint; B started
second, its checkpoint confirmed not to list A's task; A killed with
SIGKILL and joined; `weft manager stop B`.

- B stopped within about a second of A's death: A's task received STOP and
  was cancelled (three runs). B's live verdict about A from a recent probe
  was still inside the one-second memo, so A's residents counted as
  covered.
- With a two-second pause between A's death and the stop: A's task
  received nothing (no `control_stop` event, an empty `ctrl_in`), B's drain
  stopped only its own task and logged `manager_stop_drained`, and A's task
  kept running. `mapping_row_is_live` on A's checkpoint read `False` at
  that point.

The same holds for any task A launched after B bootstrapped. The spec
sentence promises coverage through task-owned snapshots and dead Managers'
checkpoints; the candidate construction in step 3 delivers neither.

### Files to modify

- `weft/core/manager.py` (`_collect_drain_stop_targets`,
  `_signal_children_to_stop`; no other function)
- `tests/cli/test_cli_manager.py` (one new process-backed test)
- `tests/core/test_manager.py` (one new in-process test)
- `docs/specifications/03-Manager_Architecture.md` (the mapping sentence;
  `## Related Plans` backlink)
- `CHANGELOG.md` (`## [Unreleased]` / `### Changed`, one bullet)
- `docs/plans/README.md` (this plan's row)

### Read first

- `docs/specifications/03-Manager_Architecture.md` [MA-3] lines 800-870 and
  the mapping at 955-970
- `docs/program-theory.md` [WT-2a.13], [WT-2a.2], [ALT-WT-015], and the
  [WT-4] rows "Availability and death are different questions" and "It is
  just a message"
- `weft/core/manager.py` 2576-2620 (bootstrap and the covered rule),
  4861-4924 (signalling and escalation), 5388-5399 (the control write),
  4926-5010 (collection), 5074-5160 (settlement), 5234-5272 (drain entry)
- `weft/liveness/policy.py::mapping_row_is_live`
- `weft/core/tasks/base.py::_report_state_change` (the exact-name
  broadcast Weft already performs)
- `tests/cli/test_cli_manager.py` 505-612 (the earlier-dead-Manager test)
  and 614-712 (the live-peer test): the two guards this plan keeps
- `docs/plans/2026-11-02-system-stop-ordering-plan.md` lines 748-800

### Comprehension questions (answer in the Execution log before the first edit)

1. Which two sets form the phase-1 candidates today, and which live task
   is in neither?
   Expected: the stopping Manager's own residents, and the residents of
   checkpoints whose Manager is judged live at collection time. A task
   launched by a Manager that was alive at the stopping Manager's bootstrap
   and dead at collection time is in neither (and so is any task that
   Manager launched after the bootstrap).
2. Why did the pattern form `broadcast(stop, pattern="T*.ctrl_in")` fail
   plan 4's probe, and why does the exact-name form not fail the same way?
   Expected: a pattern selects existing queue names, and a never-controlled
   task has no `ctrl_in` row; `queue_names` with `create_missing=True`
   creates the missing queues ([SB-BCAST-2]), and the names come from each
   task's own snapshot observation, so services, Managers and non-default
   routes are handled by the candidate filter, not by the name.
3. What does "covered" still decide after this plan?
   Expected: escalation only. A covered target gets the STOP but never a
   host signal from the stopping Manager (`escalate` stays false), and its
   owning Manager reaps it. Coverage no longer decides membership.

## 4. Invariants and Constraints

Must not change:

- The phase order of [MA-3]: user work, then internal services, then the
  Manager. `_drain_services_phase`, `_continue_shutdown_drain`, and the
  service and Manager rungs are not edited.
- The membership filters: this Manager's own row, the child table, active
  launches, terminal snapshots, role `manager`, internal service classes,
  and targets with no resolvable route (held as `unresolved`, as today).
- Settlement (`_settle_drain_stop_targets`, `_drain_stop_terminal_events`,
  `_release_drain_stop_targets_at_expiry`): release on task-owned terminal
  proof or verified exit; an unknown identity keeps the target; absence is
  never proof of death.
- Escalation: SIGTERM only for an uncovered target whose host identity
  re-verifies, after `MANAGER_CHILD_STOP_ESCALATION_SECONDS`; never for a
  covered target. The covered verdict keeps its definition (a live peer's
  checkpoint lists the task).
- The STOP envelope: `encode_control_message(CONTROL_STOP)`, the exact
  `{command}` form plan 3 fixed, no `request_id`, no reply.
- Bootstrap adoption (`_bootstrap_scheduler_evidence`) is not edited; this
  plan does not make STOP adopt anything.
- The liveness memo and `MANAGER_ADMISSION_RECHECK_SECONDS` are not edited;
  after the change they no longer influence membership.

Hidden couplings (checked):

- `unresolved` and the settlement cursor. Today `unresolved` holds
  candidates with no snapshot or no route. Snapshot-derived candidates
  always have a snapshot; a resident with no snapshot yet (a launch whose
  wrapper has not published) stays `unresolved` exactly as today because
  residents remain candidates too.
- Tasks of a draining, superseded Manager. Their checkpoint's Manager is
  live, so they are covered: STOP, no escalation, as the spec says.
- A task whose snapshot names a `ctrl_in` route that is not the default
  `T{tid}.ctrl_in`. The route comes from the observation; the broadcast
  targets that name. Unchanged from the per-target write.
- Duplicate STOP to a task already stopped by its own live Manager (dual
  STOP): the owner ruled on 2026-11-03 that it does not hurt (plan 4
  sub-question (a)); the existing live-peer test guards the outcome.
- The exact-name broadcast and the child table. Children in the child
  table are signalled through the same first rung; including them in the
  single broadcast is a change of mechanism only. The escalation PIDs for
  children still come from the child table.

Error-path priorities:

- Fatal: nothing new. A failed collection read still logs a warning and
  falls back to the child table, as today (`return {}`); a failed broadcast
  logs at debug and the rung is retried on the next drain turn exactly as a
  failed per-target write is today (the rung is marked signalled only after
  the write succeeds).
- Best-effort: the broadcast write and the escalation signals.

Theory check: the change moves the code toward [WT-2a.2] (the task's own
snapshot names it, not a Manager's memory of who launched it) and
[ALT-WT-015] (the local child table and the local resident set are not the
whole system); it adds no field, no mode, no timer; it removes a dependence
on a memoized liveness verdict for membership. No standing invariant or
refused alternative is reversed; the pattern broadcast [plan 4 declined]
stays declined.

Review gates: no new execution path; no drive-by edits to settlement or
escalation; no new dependency; no public CLI change; the process-backed
proof uses two real Managers in the harness, not mocks; cross-family review
of the plan before implementation and of the landed work before closure.

### Rollout, rollback, and one-way doors

Rollout: one landing commit (strategy B): code, two tests, the mapping
sentence, the backlink, the CHANGELOG bullet, status `promoted`.
Rollback: `git revert` of that commit; no persisted shape changes.
One-way doors: none.

### Observable-difference register

| # | Behavior at `e0290792` | After this plan | Class |
|---|---|---|---|
| 1 | A live user-work task whose launching Manager was alive at the stopping Manager's bootstrap and is dead at STOP time is not a phase-1 member. | It is a member: STOP on its route; escalation allowed (uncovered). | C, closes the defect |
| 2 | A task launched by a live peer after the stopping Manager's bootstrap is a member only while the peer is judged live. | A member regardless; covered while the peer is live (no escalation), uncovered once it is not. | C, closes the defect |
| 3 | A STOP issued inside the one-second liveness memo after a peer's death still covers the peer's residents. | Membership no longer depends on the memo. | N (the memo still governs the covered flag) |
| 4 | First-rung STOP writes: one `broker.write` per target. | One `broadcast(queue_names=..., create_missing=True)` per drain turn over the rungs not yet signalled; atomic across the set. | C, mechanism only; no ordering among targets was promised |
| 5 | A never-controlled task's `ctrl_in` is created by the per-target write. | Created by the broadcast (`create_missing=True`). | N |
| 6 | Covered targets: STOP, no host signal. Uncovered: STOP, then SIGTERM after the escalation window if identity re-verifies. | Unchanged. | N |
| 7 | Services, Managers, terminal tasks, internal classes: never members. | Unchanged (same filters). | N |
| 8 | Collection read failure: warning, child table only. | Unchanged. | N |

No D row.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Spec Baseline

- `e0290792` — `docs/specifications/03-Manager_Architecture.md` at plan
  authoring time. Plan type: implementation with spec revision; strategy B.
- Promotion baseline identifier: pending (the landing commit).

## Proposed Spec Delta

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| `docs/specifications/03-Manager_Architecture.md` | B — atomic with the code | [MA-3] implementation mapping (lines 960-962); `## Related Plans` |

### `docs/specifications/03-Manager_Architecture.md` [MA-3] mapping — replace the span that opens "`Manager._collect_drain_stop_targets` (adopted residents" and closes "terminal and host-escalation filters)," (lines 960-962)

Current text (verbatim; the three lines carry the bullet's two-space
indentation, kept in the Replacement):

> `Manager._collect_drain_stop_targets` (adopted residents and residents other
> live Managers cover, from one fresh snapshot pass; route, service-role,
> terminal and host-escalation filters),

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```text
  `Manager._collect_drain_stop_targets` (every live user-work task named by a
  task-owned snapshot in one fresh snapshot pass, whichever Manager launched it
  and whether that Manager is alive, plus this Manager's residents; residents
  other live Managers cover are marked covered for escalation only; route,
  service-role, terminal and host-escalation filters), `Manager._signal_children_to_stop`
  (the first STOP rung as one exact-name broadcast with `create_missing`, [SB-BCAST-2], [SB-BCAST-4]),
```

Carried forward: the fresh pass, the four filters, the covered concept.
Changed: membership comes from snapshots, coverage decides escalation only,
the first rung is one broadcast. Audited: the [MA-3] prose at 831-840 is
unchanged and already states this coverage.

### `docs/specifications/03-Manager_Architecture.md` `## Related Plans` — insert one bullet at the top of the list (after line 35)

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```text
- [System STOP Targets Every Live Task by Snapshot](../plans/2026-11-04-system-stop-snapshot-targets-plan.md) makes phase 1 of a system STOP find every live user-work task through its own snapshot, whichever Manager launched it and whether that Manager is alive, and sends the first STOP rung as one exact-name broadcast under [MA-3].
```

## 5. Tasks

### Task 0. Read, answer, verify

- Answer the three comprehension questions in the Execution log before
  any edit. Re-verify every cited line with `grep -n`.
- Confirm SimpleBroker's installed contract: `grep -n "create_missing"
  .venv/lib/python3.14/site-packages/simplebroker/db.py` and read
  `_normalize_broadcast_queue_names`; record that exact names with
  `create_missing=True` create missing queues and that the write is atomic
  ([SB-BCAST-2], [SB-BCAST-4]).

### Task 1. Independent review of plan and delta (before any code)

- Cross-family round per `skills/call-agent/SKILL.md`; record it in
  `## Review Log`; code starts after dispositions.

### Task 2. RED: two tests (no production edit)

- `tests/cli/test_cli_manager.py`, after
  `test_manager_stop_reaches_task_under_another_live_manager`:
  `test_manager_stop_reaches_task_of_peer_that_died_after_bootstrap`.
  Shape (the series lead's reproduction of 2026-11-04): start A with the
  file's `_non_canonical_manager_spec` and `launch_task_process` as the
  live-peer test does; wait for A's task running and A's checkpoint listing
  it; register the task's PIDs with the harness; start B the same way; wait
  for B's task running and B's checkpoint listing it; assert
  `task_a not in _checkpoint_residents(context, manager_b)` (B bootstrapped
  with A alive: covered, not adopted); `psutil.Process(proc_a.pid).kill()`,
  join; `time.sleep(2.0)` with a comment naming
  `MANAGER_ADMISSION_RECHECK_SECONDS` (the memo must have expired, or the
  test proves nothing; use `2 * MANAGER_ADMISSION_RECHECK_SECONDS`); assert
  A's task has no `control_stop` yet; `weft manager stop B` through
  `run_cli`; wait for B's task cancelled and `manager_stop_drained`; then
  `_wait_until` A's task has a `control_stop` log row and reaches
  `cancelled` (`weft_harness.wait_for_terminal_state`). The task's own
  `control_stop` is the delivery oracle (as the earlier-dead-Manager test
  says in its docstring): a host SIGTERM rescue would log
  `task_signal_stop` instead and does not count.
  RED at `e0290792`: fails at the `control_stop` wait (verified by the
  series lead on 2026-11-04: no `control_stop`, empty `ctrl_in`).
- `tests/core/test_manager.py`, in-process, using the file's Manager
  fixture pattern (see `make_manager_spec` and the broker-backed Manager
  the stop tests use): `test_collect_drain_stop_targets_names_dead_peer_resident`.
  Write a task snapshot for a fabricated TID (role absent, `projection.status`
  `running`, an `observation` with `ctrl_in`, a `runtime_handle` with no
  scoped host processes so identity is empty and escalation is false) and a
  Manager checkpoint row for a fabricated peer TID whose `runtime_handle`
  names a PID that does not exist (a dead peer) and whose
  `scheduler_frontiers.residents` lists the task. Call
  `manager._collect_drain_stop_targets()` and assert the task is a target
  with `covered is False`. Then rewrite the peer checkpoint so its
  `runtime_handle` names the test process's own PID and creation time (a
  live peer) and assert the task is a target with `covered is True`. RED at
  `e0290792`: the first assertion fails (not a target). Use the helpers the
  file already has for writing state rows; if none fits, write the JSON row
  directly with `context.queue(task_state_queue_name(tid))`.
- Verify: `./.venv/bin/python -m pytest tests/cli/test_cli_manager.py tests/core/test_manager.py -q -m "" -k "died_after_bootstrap or dead_peer_resident"`
  reports 2 failed before Task 3.
- Stop if: either test wants to patch the Manager's collection or
  signalling methods, or to disable liveness probing; the real paths are
  the proof.

### Task 3. GREEN: snapshot-keyed candidates and the broadcast rung

- `_collect_drain_stop_targets`: replace the two candidate lines with
  `candidates = {tid: tid in covered for tid in snapshots}` followed by
  `candidates.update({tid: tid in covered for tid in self._scheduler_residents if tid not in candidates})`.
  Everything after (the filters, `unresolved`, targets, cursor) is
  unchanged: Manager rows fail `_is_live_user_work_snapshot`, services fail
  the class filter, terminal rows fail the terminal filter. Update the
  docstring's first two sentences to: "Name phase-1 members from task
  evidence. Members are every live user-work task a snapshot names,
  whichever Manager launched it, plus this Manager's residents; a task
  another live Manager's checkpoint lists is marked covered, which decides
  escalation only."
- `_signal_children_to_stop`: collect the rungs that are not yet signalled
  (`tid not in self._drain_signaled_children`) into a list; send one
  `broker.broadcast(encode_control_message(CONTROL_STOP), queue_names=tuple(unique ctrl queues), create_missing=True)`
  through `self._get_connected_queue().get_connection()`; on success mark
  every one of those rungs signalled with the same timestamp; on
  `(BrokerError, OSError, RuntimeError)` log at debug and mark none (the
  next drain turn retries, as a failed per-target write does today). The
  escalation branch is unchanged. Keep `_send_stop_command` for its other
  callers. Add `Spec: [MA-3]; [SB-BCAST-2], [SB-BCAST-4]` to the docstring.
- Constraints: no other function edited; no change to settlement,
  escalation, bootstrap, or the memo.
- Verify: the two Task 2 tests pass; the two existing coverage tests pass
  unchanged (`-k "earlier_manager_detached_task or under_another_live_manager"`);
  the ordering tests pass (`-k "stop_orders"`).
- Stop if: an ordering test fails (the broadcast must not reach a service
  or Manager queue; if it does, the candidate filter, not the broadcast, is
  wrong), or the live-peer test starts escalating against A's task.

### Task 4. Spec, backlink, CHANGELOG (same commit as Tasks 2 and 3)

- Apply the two Replacement blocks verbatim.
- `CHANGELOG.md` `## [Unreleased]` / `### Changed`: "A system STOP now
  reaches every live user-work task in the context through the task's own
  snapshot, whichever Manager launched it and whether that Manager is still
  alive; before, a task whose launching Manager was alive when the stopping
  Manager started and had since died was missed. The first STOP rung is one
  atomic exact-name broadcast that creates a never-controlled task's control
  queue ([MA-3])."
- Set `Status: promoted` and the index row in the landing commit.

### Task 5. Gates, completed-work review, closeout

- Final gates in `## 7`, recorded in the Execution log.
- Cross-family review of the landed diff, one scoped round; record it.
- Closeout commit: promotion baseline identifier, Execution log, and the
  [WT-7] class-5 items: audit-table refresh (rows I-13 → [WT-2a.13], line
  5441 of the reconciliation plan, and 4.2-a, by reference) and the keyed
  possession probe drawn from the owner's bank (reusable by the owner's
  2026-11-04 rule). Then `completed`.

## 6. Testing Plan

- Harness: `WeftTestHarness` with two real Manager processes for the
  process-backed test; the broker-backed Manager fixture in
  `tests/core/test_manager.py` for the collection test.
- Do not mock: the Managers, the queues, liveness probing, the drain. The
  process-backed test injects nothing; it kills a real Manager. The
  collection test writes real state rows.
- Guards that stay unchanged: the earlier-dead-Manager test (adoption path),
  the live-peer test (covered path: STOP, no escalation, A reaps), and the
  phase-ordering tests.
- Red-green: both Task 2 tests are red at `e0290792` and green after Task 3.
- Edge case out: a task with a non-default `io.control` route under a dead
  peer. The route comes from the snapshot in both the old and new code;
  nothing in this plan touches route resolution.

## 7. Verification

Per task:

```bash
./.venv/bin/python -m pytest tests/cli/test_cli_manager.py tests/core/test_manager.py -q -m "" -k "died_after_bootstrap or dead_peer_resident or earlier_manager_detached_task or under_another_live_manager or stop_orders"
```

Final gates:

```bash
./.venv/bin/python -m pytest tests/cli/test_cli_manager.py tests/core/test_manager.py -q -m ""
./.venv/bin/python -m pytest -q
./bin/pytest-pg --fast
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check . && ./.venv/bin/ruff format --check .
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
bin/check-doc-paths
```

Observable after landing: with two Managers and the first one killed, a
system STOP on the survivor cancels the dead Manager's task through the
task's own `control_stop`, with or without a pause after the death.

## 8. Rollback

`git revert <landing commit>`.

## 9. Out of Scope

- The queue-name pattern broadcast (declined by plan 4; stays declined).
- Bootstrap adoption rules, the liveness memo, settlement, escalation
  windows, and the service and Manager phases.
- Making STOP adopt the dead peer's tasks into the stopping Manager's
  residents (STOP stops; custody recovery stays with bootstrap and the
  LivenessMonitor).
- The CLI's `weft manager stop` command path (`weft/core/manager_runtime.py::stop_manager`),
  which only addresses the Manager.

## 10. Owner Decisions

None open. One rule-decided choice: the first rung uses the exact-name
broadcast rather than keeping per-target writes. The owner's framing
("the stop is broadcast"), the atomicity of [SB-BCAST-4], and the single
write per drain turn decide it; behavior per target is identical to a
write, so the choice is mechanism, not semantics (register row 4).

## Review Log

- 2026-11-04: plan drafted by the series lead (Fable 5.1) from the outside
  agent's finding and the lead's four reproductions. Self-driven fresh-eyes
  review: done 2026-11-04 (distinct pass after drafting): 3 corrections,
  the control write's line range (5388-5399, not 4913-4924), the bootstrap's
  (2576-2595), and the mapping Replacement block's indentation (the bullet's
  two-space continuation, so the block is contained verbatim after
  promotion); every other path, symbol, line, test name and constant
  resolved by lookup. Cross-family round 1: pending.

## Revision Log

| Date | Baseline | Section | Change and reason | Re-review required? |
|------|----------|---------|-------------------|---------------------|

## Execution log

(empty until implementation)

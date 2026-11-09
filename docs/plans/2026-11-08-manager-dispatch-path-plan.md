# Manager Dispatch and Launch-Result Round Trips

Status: completed
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.6a], [MA-1.8], [MA-3]; docs/specifications/07-System_Invariants.md [MANAGER.11], [MANAGER.18], [OBS.6]; docs/specifications/05-Message_Flow_and_State.md [MF-5], [MF-6]
Superseded by: none

Class: 5 (spec clarification, promotion strategy B, atomic) with hardening
applied, because a risky trigger fires: every change is on the Manager's
durable-spine execution path (launch receipt, startup settlement, reap and
reservation acknowledgement). Escalation history: none. Plan type:
implementation with spec clarification. No intended behavior changes; the
clarifying sentences record why a read is not made, so it is not restored
later as a missing guard.

Theory: [WT-4.4] "Measure; do not bake in inefficiency" (broker-operation
counts across completion-to-next-launch intervals) and "No over-armoring",
[WT-4.2] "Process concurrency, short transactions", [WT-4.1] "Keep an identity
you already have" and "A wake predicate reads facts and guarantees progress",
[WT-4.3] "A quiet task costs nothing an operator can see" and "Availability
and death are different questions", [WT-2a.7], [WT-2a.13], [ALT-WT-001],
[ALT-WT-002].

## 1. Goal

Cut the Manager's per-launch database round trips in the dispatch,
launch-result, handoff and reap paths without weakening [MA-1.8] admission
(every ordinary PostgreSQL launch keeps a fresh coherent usage observation
and the measured decision; unknown usage denies dispatch), [MANAGER.11] /
[MF-5] custody, [MA-1.6a] / [MA-3] terminal proof, or [OBS.6].

Measured on `perf/bench-allG` (75f5b270), 1,250-task nested PostgreSQL run,
297 s wall, Manager main thread busy 123 s, about 93% of it PostgreSQL round
trips: `queue_dispatch` 59.4 s (4,008 calls, 14.8 ms each, including
`launch_checkpoint` 9.7 s and `admission` 16.0 s at 2,044 checks of 7.8 ms),
`worker_results` 21.2 s (5.2 ms per turn), `family_classification` 10.5 s,
`evidence_refresh` 5.3 s, `startup_handoff` 5.2 s.

This plan removes five round-trip groups that no contract needs (section 6),
keeps every one that a contract names, and records the rest with their owner.

## 2. Source Documents

- `docs/program-theory.md` [WT-0], [WT-2a.7], [WT-2a.13], [WT-4.1] to
  [WT-4.4], [ALT-WT-001], [ALT-WT-002], [ALT-WT-010].
- `docs/specifications/03-Manager_Architecture.md` [MA-1.6a] (reap: "acknowledges
  any remaining unacknowledged spawn reservation only after observing task-owned
  local terminal proof or committing a Manager-source `wrapper_lost` envelope"),
  [MA-1.8] (startup settlement on snapshot or attributed usage; fresh usage per
  launch; custody barrier before another ordinary dispatch), [MA-3].
- `docs/specifications/07-System_Invariants.md` [MANAGER.11], [MANAGER.18]
  (the same startup paragraph as [MA-1.8]), [OBS.6].
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5], [MF-6].
- Track plans on this base that edit `weft/core/manager.py`, read before
  editing so their contracts stay intact:
  `2026-11-08-manager-checkpoint-volume-plan.md` (turn-scoped checkpoint;
  `_publish_undurable_receipts` barrier), `2026-11-08-manager-event-driven-reaping-plan.md`
  (sentinel-only exit evidence; reap snapshot read on the Manager's own
  connection), `2026-11-08-manager-stable-wait-set-plan.md` (interest from
  launch selection; "the receipt turn's exact handoff read observes any earlier
  publication"), `2026-11-08-startup-window-settlement-plan.md` (N(U) window).
- `docs/agent-context/runbooks/writing-plans.md`,
  `docs/agent-context/runbooks/hardening-plans.md`.

## 3. Inventory: Manager SQL per launched task (before)

Method: `tests/system/test_admission_stress.py::test_ops_burst_admission[101-tasks]`
on PostgreSQL through `./bin/pytest-pg`, with an out-of-repo psycopg statement
counter (`WEFT_PERF_SQLCOUNT=1`, `-p weft_perf_trace`) that keys every
statement and commit by thread, Weft caller chain and SimpleBroker API. All
Manager statements ran on the main (reactor) thread: 10,009 statements for
101 tasks, 99.1 per task, including fixed startup and shutdown cost. A
SimpleBroker write is four round trips (`BEGIN`, `UPDATE meta ... last_ts`,
`INSERT`, `COMMIT`); `move_one` and `delete_message_ids` are three (`BEGIN`,
one CTE, `COMMIT`); a peek or `has_pending` is one; a connection-usage read is
one `pg_stat_activity` scan plus SimpleBroker backend-plugin discovery
(`Queue.backend_name`).

Statements per launched task, grouped by Manager path. "Owner" is the
contract that requires the round trip; "removed" rows are this plan's changes.

| Path | Weft caller (SimpleBroker API) | Stmts/task | Owner / disposition |
|---|---|---:|---|
| dispatch | `_checkpoint_admission_launch` -> `_register_tid_state` (write) | 4.11 | [MANAGER.11] pre-launch barrier; kept |
| dispatch | `_seed_child_inbox` (write) | 4.04 | [MF-1] first work item before launch; kept |
| dispatch | `_fetch_next_message` (`move_one`, exact ID) | 3.01 | [MF-6] reservation; kept |
| dispatch | `_fetch_next_message` (`delete_message_ids`, pending hint) | 3.00 | [MF-6] consume-on-dispatch hint; kept |
| dispatch | `_handle_work_message` -> `_control_allows_child_launch` x2 (ctrl_in peek) | 2.02 | [MA-3] STOP before launch; **one removed (H5)** |
| dispatch | `_update_active_queues` -> internal inbox `has_pending`, ctrl_in peek, source-watermark peek | 4.31 | [CC-2.1], [MA-1.8] readiness facts; kept |
| dispatch | `_admission_allows_queue` -> `get_connection_usage` | 1.29 | [MA-1.8] fresh usage per launch; kept |
| dispatch | `_process_queue_message` -> source-watermark peek after delivery | 0.56 | [MA-1.8]; kept |
| launch result | `_commit_child_launch_success` -> `task_spawned` (write) | 4.10 | [OBS.1] task log; kept |
| launch result | `_reconcile_launch_now` -> `read_task_state_snapshot` | 1.01 | [MA-1.8] receipt-turn exact handoff read (Track D); kept |
| launch result | `_advance_launch_custody` -> `get_connection_usage` | 1.00 | **removed (H1)**: cannot settle an undurable receipt |
| launch result | `_publish_undurable_receipts` -> checkpoint (write) | 3.35 | [MA-1.8] receipt durable before next dispatch (Track B); kept |
| handoff | `_has_pending_observation` cursor peeks (wait entry and hints) | 14.34 | [MA-1.8] interest cursor; kept (SimpleBroker follow-up, section 9) |
| handoff | `_reconcile_launch_now` -> `read_task_state_snapshot` | 0.77 | [MA-1.8] exact handoff read; kept |
| handoff | `_ack_launch_custody` (`delete_message_ids` + confirming peek) | 3.09 | [MANAGER.11] acknowledgement with confirmed absence; kept |
| reap | `_child_terminal_proof_still_within_grace` -> ctrl_out scan | 1.00 | [MA-1.6a] terminal proof; kept |
| reap | `_commit_wrapper_lost_envelope` -> ctrl_out scan | 1.00 | **removed (H2)** when the first scan found task proof |
| reap | `_record_reaped_admission` -> `read_task_state_snapshot` | 1.00 | [MA-1.8] reap refresh (Track C); kept |
| reap | `_cleanup_children` -> `Queue.delete` (reservation) | 3.00 | **removed (H3)** when handoff already acknowledged it |
| turn end | `_commit_scheduler_checkpoint` (write) | 4.81 | [MF-5] turn checkpoint (Track B); kept |
| turn start | `_drain_control_queue_first` (ctrl_in peek) | 4.47 | [MA-3] control first; kept |
| classification | `_classify_family_requests` page, `_index_family_request` write, hint repair | 11.04 | [MA-1.8] bounded classification; kept |
| evidence refresh | `_refresh_scheduler_evidence` (16 snapshots per second, usage cached per pass) | 10.39 | [MF-5] bounded maintenance; kept |
| wait | `_has_pending_messages` readiness checks | 6.97 | [CC-2.1], [WT-4.1]; kept |
| service convergence | `_internal_spawn_pending` (forced after each reap) | 1.46 | [MA-1] item 7; kept (section 9) |
| logging only | `_emit_manager_loop_summary` -> 3 x `has_pending` per turn | 0 here, 3 per turn at serve-log level info or finer | **deferred to emission (H4)** |

Per launch the five changes remove, on PostgreSQL: one `pg_stat_activity`
scan and one plugin discovery (H1), one ctrl_out scan (H2), one three-statement
delete transaction (H3), and one ctrl_in peek (H5): about 6 statements of the
99 (H4 adds three per turn when the serve log is at info or finer). On the
1,250-task run H1 alone is about 1,250 x 7.8 ms, or about 10 s of the 21.2 s
`worker_results` phase.

Admission (2,044 checks for 1,249 launches) is required as measured: every
launch takes one fresh observation, and a receipt or settlement that frees no
window slot still triggers one fresh observation that refuses. Removing that
second read is an admission-policy change; it is listed for the owner in
section 9, not made here.

## 4. Context and Key Files

Files to modify:

- `weft/core/manager.py`
  - `ManagedChild` (one field: `terminal_proof_observed`)
  - `Manager._advance_launch_custody` (H1)
  - `Manager._child_terminal_proof_still_within_grace`,
    `Manager._write_manager_terminal_envelope` (H2)
  - `Manager._cleanup_children` (H3)
  - `Manager._emit_serve_log_rate_limited`, `Manager._emit_manager_loop_summary` (H4)
  - `Manager._handle_work_message` (H5)
- `docs/specifications/03-Manager_Architecture.md` ([MA-1.6a], [MA-1.8],
  Related Plans), `docs/specifications/07-System_Invariants.md` ([MANAGER.18]
  startup paragraph, Related Plans).
- Tests: `tests/core/test_manager_family_maintenance.py` (H1),
  `tests/core/test_manager.py` (H2, H3, H4, H5).
- `docs/plans/README.md` (index row, count).

Current structure (what exists today):

- Receipt turn. `_drain_worker_results` -> `_handle_child_launch_result` ->
  `_commit_child_launch_success` -> `_checkpoint_spawned_admission` adds the
  TID to `_scheduler_undurable`, marks the checkpoint dirty and calls
  `_commit_scheduler_checkpoint`, which publishes nothing inside an open turn
  (Track B). Then `_reconcile_launch_now` reads the snapshot and calls
  `_advance_launch_custody(tid, payload)`. With no snapshot (the child is still
  importing), `handoff` is false and the method reads connection usage to see
  whether the child's owner label has connected. The release condition that
  follows is `ready and (handoff or tid not in self._scheduler_undurable)`, so
  a positive reading can release nothing while the receipt is undurable, and
  the missing-publication fallback is not due (`elapsed < MANAGER_ADMISSION_RECHECK_SECONDS`).
  The receipt becomes durable later in the same turn
  (`_publish_undurable_receipts` before dispatch, or the turn-end commit).
- Clean reap. `_cleanup_children` calls `_child_terminal_proof_still_within_grace`,
  which scans ctrl_out for task-owned proof. When proof is visible it returns
  false, and `_write_manager_terminal_envelope` -> `_commit_wrapper_lost_envelope`
  scans the same ctrl_out again (task or `wrapper_lost`) before deciding not to
  write.
- Reservation acknowledgement. Snapshot handoff calls `_ack_launch_custody`
  (`delete_message_ids` then a default exact peek that must find nothing),
  then drops `source_queue`, `reserved_queue` and `request_id` from the
  resident. At reap `_record_reaped_admission` -> `_retire_scheduler_residents`
  acknowledges a still-held reference the same way and pops the resident.
  `_cleanup_children` then deletes `child.reserved_queue` /
  `child.message_timestamp` unconditionally.
- Loop summary. `_emit_manager_loop_summary` runs every turn; at serve-log
  level info or finer it reads `has_pending` on the inbox, internal inbox and
  ctrl_in, then `_emit_serve_log_rate_limited` usually suppresses the event.
- Handler STOP checks. `_handle_work_message` calls `_control_allows_child_launch`
  (one ctrl_in peek, more if a control row is pending) before JSON/lineage/TaskSpec
  validation and again after it, immediately before `_launch_child_task`.
  Validation (`_spawn_submitter_tid`, `_build_child_spec`) touches no broker.

Comprehension questions (answer in the execution record before editing):

1. In the receipt turn, why can a positive attributed-usage reading not
   release startup? Expected: `_checkpoint_spawned_admission` put the TID in
   `_scheduler_undurable` and no publication has run yet in this turn; release
   needs `handoff or tid not in _scheduler_undurable`, and there is no handoff
   without a snapshot.
2. When the first ctrl_out scan finds task-owned proof, what can the second
   scan return? Expected: true for the same row. Task-owned proof is the
   [MA-1.6a] evidence; a consumer that removed the row in between would make
   the second scan miss it and the Manager append a spurious `wrapper_lost`.
3. Which code removes `reserved_queue` from a resident, and what does it prove
   first? Expected: only `_ack_launch_custody`, after a delete and a default
   exact peek that finds nothing.

## 5. Invariants and Constraints

- [MA-1.8]: every ordinary PostgreSQL launch keeps one fresh coherent usage
  observation and the measured decision; unknown usage denies dispatch;
  startup still settles on snapshot handoff, attributed usage once the receipt
  is durable, the bounded missing-publication fallback, failure or reap.
- [MANAGER.11]: pre-launch custody is durable before process creation; a
  receipt is durable or handed off before another ordinary dispatch; custody
  references drop only after acknowledgement with confirmed absence.
- [MA-1.6a]: a reaped child's reservation is acknowledged only after
  task-owned proof is observed or a `wrapper_lost` envelope commits; a failed
  proof read is unknown and still leads to the failsafe path.
- [MA-3]: a pending STOP still prevents a launch from a request reserved in
  the same turn.
- Track B (checkpoint unit of work), Track C (sentinel-only exit evidence),
  Track D (receipt-turn exact read; interest from selection) and Track G
  (window shape) are unchanged.
- No new queue, constant, config knob, timer, dependency or execution path;
  no SimpleBroker change. `ManagedChild` gains one boolean.
- Gates: tests use a real broker, real Manager and real child processes;
  counts come from delegating wrappers around real calls. No production seam
  for tests.

## 6. Observable-Difference Register

| Change | Class | Theory basis |
|---|---|---|
| H1. The launch-receipt turn on PostgreSQL makes no connection-usage read while the receipt is undurable and the missing-publication fallback is not due; any later reconciliation reads usage exactly as before | N (the reading could not change any outcome: release needs a durable receipt or handoff, and the fallback is not due) | [WT-4.4] measure, do not bake in inefficiency; [WT-4.1] the startup fact that wakes the Manager is the snapshot row, read on notification; [ALT-WT-002] no guess-timed poll |
| H1 side effect: a usage-read failure can no longer abort that receipt-turn reconciliation | N (the aborted remainder was a no-op) | [WT-4.4] no over-armoring |
| H2. A clean reap that observed task-owned proof does not scan ctrl_out a second time before skipping the failsafe; a reap that saw no proof, an unknown read, or a non-clean exit keeps the scan inside `_commit_wrapper_lost_envelope`; retiring a reservation that was never handed off (`_record_reaped_admission` -> `_retire_scheduler_residents` -> `_commit_wrapper_lost_envelope`) keeps its existing scan, so that path still reads ctrl_out twice | C (same evidence; additionally a consumer that removes the proof row between the two scans can no longer cause a spurious `wrapper_lost` envelope) | [MA-1.6a] proof observed once is proof; [WT-4.1] keep an identity (fact) you already have; [WT-4.2] short transactions |
| H3. A reap does not re-delete a reservation that snapshot handoff or reap-time retirement already acknowledged with confirmed absence; a reservation still held by the custody record, or a child with no custody record, is deleted as before | N (the row is confirmed absent; only the redundant transaction goes) | [MA-1.6a] "remaining unacknowledged" reservation; [WT-4.4] no over-armoring; [WT-4.2] short transactions |
| H4. `manager_loop_summary` reads the three pending flags only when the event is emitted; a change in those flags alone no longer forces an emission before the rate-limit interval (it appears at the next emission), and a transition that reverses between two emissions is not logged at all | D (diagnostic visibility only: prompt logging of pending-flag changes is lost; emitted summaries still carry freshly read flags; no execution, custody or admission effect; log formats are safe to change, `CLAUDE.md` §3) | [WT-4.4] measure, do not bake in inefficiency: three broker reads removed from every turn whose summary is suppressed (about 4,000 turns per 1,250-task run with the serve log at info or finer); [WT-4.3] a quiet task costs nothing an operator can see |
| H5. `_handle_work_message` reads the control queue once, immediately before the launch, instead of before and after validation. A drain already known at handler entry still releases the request before validation (broker-free flag check, unchanged reason). A STOP still unread in ctrl_in at reservation still releases a valid request unlaunched (reason `control_disallowed_launch_after_validation` instead of `..._before_validation`); an invalid request reserved in that window gets the Manager's error policy instead of being returned to the source for the next Manager to reject | C (no launch is added; an invalid request reaches the same error disposition a successor would give it, one Manager earlier; the release reason is a debug log field) | [MA-3] STOP before launch is preserved by the later check, which covers the earlier window; [WT-4.4] measure; [WT-4.4] no over-armoring |

One `D` row, H4: a diagnostic-log visibility tradeoff justified under [WT-4.4].
No `D` row touches execution, custody or admission.

## 7. Spec Baseline

- `75f5b270` (`perf/bench-allG`) — `docs/specifications/03-Manager_Architecture.md`,
  `docs/specifications/07-System_Invariants.md` at plan authoring time.

## 8. Proposed Spec Delta

Promotion strategy:

| Spec file | Strategy | Sections touched |
|---|---|---|
| docs/specifications/03-Manager_Architecture.md | B — atomic (text and code in one change) | [MA-1.6a] reap paragraph; [MA-1.8] startup paragraph; Related Plans |
| docs/specifications/07-System_Invariants.md | B — atomic | [MANAGER.18] startup paragraph; Related Plans |

### [MA-1.8] and [MANAGER.18] — insert after "... or through existing failure/reap handling or the bounded missing-publication fallback below."

> Attributed usage is read for an unfinished startup only when a reading can
> settle it. While its launch receipt is undurable and the missing-publication
> fallback is not due, attributed usage cannot settle the startup (snapshot
> handoff, failure or reap still can), so no connection usage is read for it
> then, including in the turn that handles the receipt.

### [MA-1.6a] — insert after "... or committing a Manager-source `wrapper_lost` envelope."

> A reservation that snapshot handoff or retirement already acknowledged with
> confirmed absence is not acknowledged again at reap. When the clean-exit
> proof read finds task-owned proof, that read also settles the immediate
> failsafe check; retiring a reservation that was never handed off keeps its
> own terminal-evidence read.

### Related Plans (both files)

> - [Manager dispatch and launch-result round trips](../plans/2026-11-08-manager-dispatch-path-plan.md)
>   removes Manager round trips that no contract needs from the launch receipt,
>   reap and dispatch handler: no usage read for an undurable receipt before the
>   missing-publication fallback is due, one terminal-proof read for a clean
>   reap's failsafe check, no second acknowledgement of an acknowledged
>   reservation, one STOP check per launch ([MA-1.6a], [MA-1.8], [MA-3]).

## 9. Tasks

1. RED tests (write first; confirm each fails on the base for the intended
   reason).
   - H1, `tests/core/test_manager_family_maintenance.py`,
     `test_undurable_launch_receipt_reads_no_connection_usage` (PostgreSQL
     only; skips on SQLite, where usage is never read here). Real Manager,
     real spawned child held before it connects or publishes
     (`_publish_waiting_task` with `before_publish`), frozen Manager wall
     clock (as in `test_positive_startup_snapshot_wakes_admission_without_maintenance_tick`),
     periodic refresh held off. A counting delegate around
     `_read_postgres_connection_usage` must see zero calls while the turns
     handle the receipt (base: one). Then, with the receipt durable, a
     reconciliation reads usage once and leaves startup unsettled; releasing
     the child publishes its snapshot and the startup settles by handoff with
     the reservation acknowledged.
   - H2 and H3, `tests/core/test_manager.py`,
     `test_handed_off_child_reap_reads_proof_once_and_acknowledges_nothing_again`.
     Real child through the spawn inbox (`_launch_waiting_child`); drive until
     its snapshot handoff dropped the resident's reservation reference; release
     it; on the reaping turn count real `_terminal_envelope_visible` calls and
     real `Queue.delete` calls on the reserved queue. Expect one and zero
     (base: two and one); the child, its resident and the reserved row are
     gone; ctrl_out holds exactly one terminal envelope, the task's.
   - H4, `tests/core/test_manager.py`,
     `test_suppressed_loop_summary_reads_no_queues`. Real Manager with the
     serve log at info; two summaries in a row with no state change; a
     counting delegate around `Queue.has_pending` sees three calls for the
     emitted event and none for the suppressed one (base: six); the emitted
     event carries the three pending fields.
   - H5, `tests/core/test_manager.py`,
     `test_spawn_handler_checks_control_once_before_launch`. Real Manager and
     request; count ctrl_in peeks inside `_handle_work_message` (one; base:
     two). The existing `test_manager_stop_mid_handler_requeues_reserved_work_unlaunched`
     remains the STOP behavior proof, plus a new case that writes STOP right
     after the reservation and asserts no launch and the request returned to
     the inbox.
2. Implement H1-H5 (section 4 functions only).
   - H1: in `_advance_launch_custody`, compute `fallback_due` once and read
     usage only when `tid not in self._scheduler_undurable or fallback_due`.
   - H2: `ManagedChild.terminal_proof_observed: bool = False`; set it when
     `_child_terminal_proof_still_within_grace` reads `True`;
     `_write_manager_terminal_envelope` returns `True` without a read when it
     is set.
   - H3: in `_cleanup_children`, note whether the child had a custody record
     before the reap callback; after it, delete the reservation only when
     there was no record or the record still holds `reserved_queue`.
   - H4: `_emit_serve_log_rate_limited(..., deferred_fields=...)` evaluated
     only on emission; the loop summary passes its three pending reads there.
   - H5: replace the pre-validation `_control_allows_child_launch` call with
     the broker-free `_draining or should_stop` check it starts with; the
     post-validation call stays.
   - Stop and re-plan if: a production path drops `reserved_queue` from a
     resident without `_ack_launch_custody`; a caller of
     `_write_manager_terminal_envelope` outside `_cleanup_children` relies on
     its read; any existing test needs a weakened assertion.
3. Apply the spec delta (section 8) and the README index row.
4. Verification (section 11) and commit.

## 10. Testing Plan

- Real broker, real Manager, real spawned children (`manager`, `manager_setup`
  fixtures; `_publish_waiting_task`; `_launch_waiting_child`). The only
  wrappers are counting delegates that call the real method.
- Regression owners: `tests/core/test_manager.py`,
  `tests/core/test_manager_family_maintenance.py`,
  `tests/core/test_manager_burst_bookkeeping.py`,
  `tests/core/test_admission_recovery.py`,
  `tests/core/test_family_dispatch_scaling.py`,
  `tests/core/test_work_conserving_admission.py`,
  `tests/core/test_connection_admission.py`, `tests/specs`.
- Measurement: the same instrumented `test_ops_burst_admission[101-tasks]`
  PostgreSQL run before and after (section 3 method).
- Out of scope for local runs: the full suite and the 1,250-task nested test
  (the coordinator benchmarks it).

## 11. Verification and Gates

```bash
./.venv/bin/python -m pytest -n 4 tests/core/test_manager.py tests/core/test_manager_family_maintenance.py tests/core/test_manager_burst_bookkeeping.py tests/core/test_admission_recovery.py tests/core/test_family_dispatch_scaling.py tests/core/test_work_conserving_admission.py tests/core/test_connection_admission.py tests/specs -q
./bin/pytest-pg tests/core/test_manager.py tests/core/test_manager_family_maintenance.py tests/core/test_manager_burst_bookkeeping.py tests/core/test_admission_recovery.py tests/core/test_family_dispatch_scaling.py
./.venv/bin/ruff check .
./.venv/bin/ruff format --check weft/core/manager.py tests/core/test_manager.py tests/core/test_manager_family_maintenance.py
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./bin/check-doc-paths
```

Observable success after deploy: on the instrumented burst run the Manager's
statements per task fall by about six (one `pg_stat_activity` scan, one
ctrl_out scan, one delete transaction, one ctrl_in peek); on the 1,250-task
run `worker_results` should fall by roughly the receipt-turn usage reads
(about 10 s), and the reap and dispatch phases by a few seconds.

## 12. Rollback and Rollout

Revert the commit (code, tests and spec text together). No queue, payload,
checkpoint field or configuration changes; old and new Managers read the
same rows and interoperate. No rollout order, no one-way door.

## 13. Out of Scope and Owner Questions

Recorded, not changed here:

- Admission re-observation after a receipt that frees no window slot (about
  0.6 extra `pg_stat_activity` reads per launch on the 1,250-task run). The
  spec makes launch-worker evidence a reason to re-decide ([MA-1.8] "until
  startup membership or launch-worker evidence changes"). Skipping it when the
  last fresh observation already filled the narrowed window would be an
  admission-policy change: **owner decision**.
- Receipt checkpoint deferral: the receipt turn cannot dispatch, so its
  receipt publication could wait for the next dispatch's pre-launch barrier,
  and snapshot handoff usually makes it unnecessary. That changes custody
  durability after a crash (a no-receipt hold instead of a receipt): **owner
  decision**, Track B's contract.
- `_has_pending_observation` peeks each interested startup TID on every wait
  entry and evidence-carrying return (14 peeks per task here). One multi-queue
  "newest row after cursor" read would need a SimpleBroker API ([WT-2a.8]).
- Classification reads its page above the watermark every turn; per-queue
  activity hints from the SimpleBroker waiter would let it skip quiet turns
  (SimpleBroker).
- The forced service convergence after every reaping turn (one
  `has_pending` per reap) could be limited to service or autostart exits.
- Per-call connection-usage cost (`pg_stat_activity` scan, plugin discovery):
  SimpleBroker tracks.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

No deviation from the promoted [MA-1.6a], [MA-1.8] and [MANAGER.18] text.
Plan-only adjustment made during execution (no spec effect): H5 keeps the
broker-free `_draining or should_stop` check at handler entry (first draft:
drop the whole pre-validation block), so a drain already known before the
handler keeps its old release path; only a STOP still unread in ctrl_in
moves to the single post-validation read.

## Fresh-Eyes Review

Author pass after drafting:

- Every removal is either provably unused (H1: the reading feeds a condition
  that is false for an undurable receipt before the fallback is due; H3: the
  row's absence was already confirmed) or a duplicate of a read made
  microseconds earlier in the same function sequence (H2, H5), or a
  diagnostic read whose result is discarded (H4).
- The changes add no state beyond one `ManagedChild` boolean, no timer, and
  no new path. Track B, C, D and G contracts were re-read; none of their
  functions' contracts change (Track D's receipt-turn snapshot read is kept on
  purpose).
- Zero-context risks: a later reader may "restore" the H1 read as a missing
  guard; the [MA-1.8] sentence and the code comment say why it is absent.
  H3 depends on `_ack_launch_custody` being the only remover of
  `reserved_queue`; verified with `grep` (section 9 stop condition).

## Review and execution record

### Cross-family review

Tier 1, Codex (codex exec -s read-only, reasoning effort high), 2026-11-08,
scoped-change review of plan+implementation at b0a15e3d against 75f5b270
(perf/bench-allG). Verdict: no blocker.

| ID | Severity | Finding | Disposition |
| --- | --- | --- | --- |
| F1 | P3 | "One terminal-proof read per reap" overstates H2: for a clean child whose reservation is still held, `_record_reaped_admission` -> `_retire_scheduler_residents` -> `_commit_wrapper_lost_envelope` scans ctrl_out again (confirmed with an in-memory probe); the new test waits for snapshot handoff and excludes that case. | Accepted, fixed: [MA-1.6a] text, the Related Plans entries in 03 and 07, the plan's spec-delta blocks and the H2 register row now limit the claim to the immediate clean-exit failsafe check and state that retiring a never-handed-off reservation keeps its existing scan. No code change. |
| F2 | P3 | The new [MA-1.8] / [MANAGER.18] sentence says the receipt turn reads no usage, but `_advance_launch_custody` still reads usage for an undurable receipt once the missing-publication fallback is due. | Accepted, fixed: both spec paragraphs and the plan's spec-delta block now say no usage is read while the receipt is undurable and the missing-publication fallback is not due, including in the receipt turn; the Related Plans entries carry the same qualifier. No code change. |
| F3 | P3 | H4 removes prompt logging of pending-flag changes and can hide intermediate transitions between emissions; class C understates the loss of diagnostic visibility. | Accepted, fixed: the H4 register row is now class D, justified under [WT-4.4] (three broker reads removed per suppressed turn; emitted summaries still carry fresh flags), and "No `D` rows" now names the one D row. No code change. |

Benchmark results are recorded by the coordinator's program summary.

### Implementer execution log

Comprehension answers (recorded before the first edit):

1. `_checkpoint_spawned_admission` adds the TID to `_scheduler_undurable`
   and its `_commit_scheduler_checkpoint` publishes nothing inside an open
   turn; with no snapshot there is no handoff, so `ready and (handoff or tid
   not in _scheduler_undurable)` is false whatever the reading says, and the
   fallback branch needs `elapsed >= MANAGER_ADMISSION_RECHECK_SECONDS`.
2. True, for the same committed row. A second scan can only lose it (a
   consumer removed the row), which today would append a `wrapper_lost`
   envelope after task-owned proof was already observed.
3. Only `_ack_launch_custody`, after `delete_message_ids` and a default
   exact peek that must return nothing (it raises otherwise). The only pop
   of a launched child's resident is `_retire_scheduler_residents`, after
   that acknowledgement (`grep` of `_scheduler_residents.pop` and
   `"reserved_queue"` in `weft/core/manager.py`: the other pops are the
   failed pre-launch checkpoint and the no-process launch failure, neither
   of which has a child).

Evidence (base `75f5b270`):

- RED on the base code: H1 (PostgreSQL, `./bin/pytest-pg`) failed with
  `assert [{'total': 3, 'by_owner': {}}] == []` (one usage read in the
  receipt turn, the child's owner absent). H2 failed with 2 ctrl_out reads
  where 1 is expected; with H2 in place, H3 failed with 1 reservation delete
  where 0 is expected. H4 failed with 3 `has_pending` reads for the
  suppressed summary. H5 failed with 2 ctrl_in reads in the handler. The STOP
  behavior test passes on base and after (it is the regression guard).
- SQLite, `-n 4`: `tests/core/test_manager.py`,
  `tests/core/test_manager_family_maintenance.py`,
  `tests/core/test_manager_burst_bookkeeping.py`,
  `tests/core/test_admission_recovery.py`,
  `tests/core/test_family_dispatch_scaling.py`,
  `tests/core/test_work_conserving_admission.py`,
  `tests/core/test_connection_admission.py`, `tests/specs`: 608 passed,
  4 skipped (PostgreSQL-only), exit 0. After the spec edits and formatting:
  `tests/specs` plus the five new tests, 169 passed, 1 skipped.
- PostgreSQL, `./bin/pytest-pg` on the seven `tests/core` files above:
  438 passed, 12 skipped. After formatting: the five new tests plus
  `tests/specs`, 170 passed.
- `./.venv/bin/ruff check .`: all checks passed; `ruff format --check` on
  the three touched Python files: formatted; repo mypy command: no issues in
  503 source files; `bin/check-doc-paths`: OK.

Measurement (instrumented `test_ops_burst_admission[101-tasks]`, PostgreSQL,
same machine, section 3 method; one run each, so turn- and wait-driven rows
vary by a few percent between runs):

| Manager statements per task | Before | After |
|---|---:|---:|
| Total | 99.1 | 89.0 |
| Launch result (`receipt` path) | 6.15 | 5.12 |
| `_advance_launch_custody` usage reads, receipt turn | 1.00 | 0 |
| Reap (`_cleanup_children` path) | 6.00 | 2.00 |
| Handler ctrl_in peeks | 2.02 | 1.01 |
| All `pg_stat_activity` reads | 2.49 | 1.51 |
| Admission usage reads (unchanged, required) | 1.29 | 1.33 |

The designed removal is six statements per task (1 + 1 + 3 + 1); the rest of
the observed drop is run-to-run variation in wait and turn counts. Manager DB
time in this light run fell from 14.9 s to 14.3 s: at 101 tasks a
`pg_stat_activity` scan costs about 1.2 ms, against about 7.8 ms at 300
connections in the 1,250-task run.

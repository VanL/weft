# Manager Checkpoint Volume

Status: promoted
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.6], [OBS.6a], [MANAGER.11]; docs/specifications/03-Manager_Architecture.md [MA-1.8]
Superseded by: none

Class: 5 (implementation with spec revision). Risky trigger: Manager launch
custody on the durable spine (`Manager -> queues/state log`), so the
hardening checklist applies. Normative text in [MF-5] and [OBS.6] changes.
Promotion strategy B (atomic spec and code change).

## 1. Goal

Cut the Manager's scheduler-checkpoint cost per launched task without
weakening custody or recovery. Today every custody event appends one complete
snapshot of the Manager's state to `weft.state.tasks.<manager_tid>`. A launch
has four such events: pre-launch custody, the process receipt, snapshot
handoff and retirement. Each snapshot also republishes every resident's whole
task snapshot. The admission stress run measured about 4.1 checkpoints per
task, about 127 KB each, and 654 MB per run. `scheduler_checkpoint` was 15.7%
of Manager wall time. Every PostgreSQL write also holds SimpleBroker's meta
row lock, so this volume slows every other writer.

This plan makes the Manager reactor turn the checkpoint's unit of work. It
keeps the publications that an external action waits on immediate. It
publishes only the custody fields of each resident and stops deep-copying the
payload before serialization.

## 2. Source Documents and Spec Baseline

- `docs/program-theory.md` [WT-2a.2], [WT-2a.6], [WT-2a.13], [WT-4.1]
  ("Queues carry facts; processes carry hints"), [WT-4.2] ("One owner for each
  resource"), [WT-4.3] ("Availability and death are different questions"),
  [WT-4.4] ("Measure; do not bake in inefficiency", "No capability
  diminution", "No over-armoring"), [ALT-WT-016] (only the checkpoint write
  stays proportional to the lineage it publishes), [ALT-WT-019] (do not
  duplicate state that `weft.state.tasks.<tid>` owns).
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5], the paragraphs
  on Manager-owned `scheduler_frontiers` and post-launch checkpointing.
- `docs/specifications/07-System_Invariants.md` [OBS.6] (same paragraphs),
  [OBS.6a] (edge-triggered complete snapshots), [MANAGER.11].
- `docs/specifications/03-Manager_Architecture.md` [MA-1.8]: "A launch with
  known post-launch process evidence must preserve recovery custody through
  task-owned snapshot handoff/positive settlement or a successful post-launch
  checkpoint before another ordinary dispatch".
- Spec baseline: `08c2f87b` (`perf/admission-base`) for all three files.
- Promotion baseline identifier: diff base `08c2f87b`; the spec delta and the
  code land in the same commit on `perf/manager-checkpoints`.

## 3. Evidence and Causal Premise

A throwaway probe drove a real Manager through real turns with six echo
tasks on SQLite and counted `_register_tid_state` calls by caller. Baseline:
27 appends (4.5 per launch). Six each came from `_checkpoint_admission_launch`,
`_checkpoint_spawned_admission`, `_reconcile_launch_now` (snapshot handoff)
and `_retire_scheduler_residents` (reap), plus bootstrap and two diagnostic
refusal appends. So each custody event cost one complete append; nothing was
shared between events in one turn. A resident adopted from its task snapshot
measured 1,354 bytes; its custody fields alone measured 435 bytes. The extra
bytes were the task's own `projection`, `observation`, `runtime_handle`,
`activity` and similar fields.

## 4. Current Structure (before this change)

`Manager._publish_scheduler_checkpoint` calls `_register_tid_state`, which
appends the payload of `Manager._build_tid_state_payload` (deep copies of
`_scheduler_residents` and `_dispatch_observation`). On success it clears
`_scheduler_undurable` for residents with process evidence; on failure it
leaves `_scheduler_checkpoint_dirty` set.

| Call site | Event | External action that waits on it |
| --- | --- | --- |
| `_checkpoint_admission_launch` | pre-launch custody | process creation (barrier) |
| `_bootstrap_scheduler_evidence` | successor adoption | the successor's first ordinary dispatch (barrier) |
| `_checkpoint_spawned_admission` | process receipt | the next ordinary dispatch and startup release, both gated by `_scheduler_undurable` |
| `_reconcile_launch_now` | adoption, handoff ack, startup release | none |
| `_refresh_scheduler_evidence` | periodic adoption and retirement | none |
| `_retire_scheduler_residents` | reap or positive exit | none (the reservation ack precedes it) |
| `_handle_child_launch_failure` (no process) | removal of an unlaunched resident | reserved policy (left immediate) |
| `_assess_dispatch_refusals` | diagnostic refusal append | none; must not clear undurable responsibility ([MA-1.8]) |

A turn that commits a launch result sets `_child_launch_started_this_turn`,
so it dispatches nothing more; ordinary dispatch is also gated on
`_scheduler_undurable`.

## 5. Design

1. Unit of work. `Manager._process_reactor_turn` opens a checkpoint turn
   (`_scheduler_checkpoint_turn = "open"`). Non-barrier sites set
   `_scheduler_checkpoint_dirty` and call `_commit_scheduler_checkpoint`, which
   publishes at once only outside a turn. When the turn ends, however it
   ends, one commit publishes the turn's changes in one complete snapshot.
   Outside a turn (stopping turns, cleanup, direct calls) every change still
   publishes at once.
2. Barriers stay immediate. Pre-launch custody and bootstrap adoption keep
   calling `_publish_scheduler_checkpoint`. `_publish_undurable_receipts`
   runs just before the turn's dispatch phase: when receipts are undurable it
   publishes them first, so dispatch in the same turn sees durable custody.
   This realizes the [MA-1.8] rule directly and needs no wake.
3. Failure rule. A failed publication during a turn moves the turn to
   `"failed"`; the turn then makes no further end-of-turn or pre-dispatch
   attempt, and a later turn retries. A barrier still attempts its own
   publication. Dirty state is still cleared only by success.
4. Slim residents. The payload publishes, per resident, only
   `MANAGER_CHECKPOINT_RESIDENT_FIELDS` (`weft/_constants.py`): lineage
   (`context`, `root_tid`, `submitter_tid`, `ancestry_tids`,
   `connection_owner`), `scheduling_class`, scoped process evidence
   (`hostname`, `host_processes`, `launched_ns`) and the exact reservation
   reference (`source_queue`, `reserved_queue`, `request_id`). These are
   exactly the fields the Manager itself records; in-memory evidence keeps the
   adopted task fields (the `full` marker is still read locally).
5. No deep copies. The payload shares live values; its only consumer
   serializes it at once on the reactor thread.

Classical model: group commit (write coalescing) in a single-threaded event
loop, with write-ahead barriers only where an external effect follows.

## 6. Invariants and Constraints

- Pre-launch custody is durable before process creation; failure aborts the
  launch ([MANAGER.11]).
- A process receipt is durable, or custody has handed off, before another
  ordinary dispatch; startup release still requires handoff or a durable
  receipt ([MA-1.8]).
- A successor dispatches nothing until its own first checkpoint publishes.
- Exact reservation acknowledgement and confirmed pending absence still
  precede removal of a resident's reservation reference.
- Diagnostic appends do not clear undurable responsibility or complete
  bootstrap (unchanged code path).
- Complete-snapshot, edge-triggered, best-effort publication ([OBS.6a]); no
  read-before-write and no writer-side equivalence oracle.
- `LivenessMonitor` stays the only task-state deleter; custody guards read
  the same reservation fields.
- No new queue, row format field, timer or dependency. No compatibility
  reader: old and new successors read only the published custody fields.

## 7. Difference Register

| Change | Class | Theory basis |
| --- | --- | --- |
| Receipt, adoption, handoff ack, startup release, maintenance and retirement publish once at turn end instead of once per site | C | [WT-2a.6] the turn is the reactor's unit of work; [WT-4.1] the turn's one complete snapshot carries the same facts; intra-turn rows were superseded within the turn |
| A crash after process creation but before the receipt's publication (pre-dispatch or turn end) leaves the launch ambiguous; the window is the rest of that turn instead of one write | D (accepted tradeoff) | Recovery availability narrows: a crash in the wider window leaves the launch held as ambiguous until its own snapshot arrives, where before a durable receipt ended the hold at once. Accepted under [WT-4.4] ("Measure; do not bake in inefficiency"): one append per custody event was the measured cost (15.7% of Manager wall, meta-row lock contention for every PG writer), and the window grows only by the rest of one turn. Custody is unchanged: [WT-2a.13]/[WT-4.3] keep the ambiguous hold, missing evidence never authorizes replay, and [MA-1.8] still requires a durable receipt or handoff before another ordinary dispatch |
| A crash before turn end can leave a retired or acknowledged resident in the newest checkpoint | C | Successors end it by confirmed pending absence or scoped death ([MF-5]); the ack still precedes removal |
| On PostgreSQL, a startup that is ready only by attributed usage in its receipt turn releases at its next observation, because the receipt is not yet durable there | D (accepted tradeoff) | Readiness latency can grow for that startup: release waits for its next observation (its first snapshot, or the 1 s recheck) instead of happening in the receipt turn. Accepted under [WT-4.4]: the path needs the child connected before the Manager drains its launch result, which is rare, and its first snapshot normally follows the connection at once and releases it by handoff. The [MA-1.8] release rule (handoff or durable receipt) is kept, not weakened, to avoid the delay |
| A failed publication retries on the next turn (one end-of-turn or pre-dispatch attempt per turn) instead of at each later site in the turn | C | [WT-4.4] no over-armoring; bounded retries on existing turns ([MA-1.8] "retries through bounded maintenance") |
| An undurable receipt left by an earlier failure publishes before the next turn's dispatch, not only at the 1 s refresh | C (improvement) | [MA-1.8] barrier realized at the point it guards |
| Published residents carry only the 12 custody fields; task-snapshot fields are no longer copied into the Manager checkpoint | C | [WT-4.2] one owner per fact; [ALT-WT-019]; [OBS.6] lists resident content as lineage, class, process evidence and reservation references; every reader uses only those fields |
| The payload builder no longer deep-copies residents or the dispatch observation | N | Serialized at once on the owning thread |
| Checkpoint rows per launch drop from at least 4 to about 2 to 3, and bytes per row by about two thirds | N (performance) | [WT-4.4] measure |

Two rows are class D: the wider ambiguity window and the delayed PostgreSQL
startup release. Each is a performance tradeoff accepted under [WT-4.4] with
custody unchanged; neither changes code beyond the turn commit above.

## 8. Proposed Spec Delta (promoted)

Promotion strategy B: the text below is applied in the same change as the
code. The [MF-5] and [OBS.6] paragraphs are duplicated across the two specs
and receive identical insertions.

Insertion 1, after "and the current bounded startup-feedback membership." in
each paragraph that begins "Task-owned `weft.state.tasks.<tid>` snapshots
([OBS.6]) carry":

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):
```markdown
Each resident entry carries only that lineage and class, its scoped process evidence (`hostname`, `host_processes`, `launched_ns`) and its exact reservation reference (`source_queue`, `reserved_queue`, `request_id`); the fields of the resident's own task snapshot stay in `weft.state.tasks.<tid>` and are not republished.
```

Replacement (`docs/specifications/07-System_Invariants.md`):
```markdown
Each resident entry carries only that lineage and class, its scoped process evidence (`hostname`, `host_processes`, `launched_ns`) and its exact reservation reference (`source_queue`, `reserved_queue`, `request_id`); the fields of the resident's own task snapshot stay in `weft.state.tasks.<tid>` and are not republished.
```

Insertion 2, after "Post-launch checkpointing adds scoped process evidence and
must report publication failure." in each paragraph that begins "On both
backends, the existing pre-launch Manager checkpoint":

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):
```markdown
A Manager reactor turn is the checkpoint's unit of work: the turn's post-launch receipts, adopted evidence, handoff acknowledgements, startup releases and retirements publish in one complete checkpoint when the turn ends. The pre-launch checkpoint and a successor's first checkpoint publish at once, and an undurable receipt publishes before the turn's ordinary dispatch. After a failed publication the turn makes no further end-of-turn or pre-dispatch attempt; a later turn retries. Outside a turn, as during cleanup, each change publishes at once.
```

Replacement (`docs/specifications/07-System_Invariants.md`):
```markdown
A Manager reactor turn is the checkpoint's unit of work: the turn's post-launch receipts, adopted evidence, handoff acknowledgements, startup releases and retirements publish in one complete checkpoint when the turn ends. The pre-launch checkpoint and a successor's first checkpoint publish at once, and an undurable receipt publishes before the turn's ordinary dispatch. After a failed publication the turn makes no further end-of-turn or pre-dispatch attempt; a later turn retries. Outside a turn, as during cleanup, each change publishes at once.
```

Clause audit: no text is removed. "must report publication failure",
"Failed publication gates further ordinary dispatch" ([MANAGER.11]) and the
[MA-1.8] "before another ordinary dispatch" rule stand unchanged and remain
true. The [OBS.6] implementation mapping gains
`Manager._process_reactor_turn`, `_commit_scheduler_checkpoint`,
`_publish_undurable_receipts`, `MANAGER_CHECKPOINT_RESIDENT_FIELDS` and the new
test file. Specs 03, 05 and 07 gain a Related Plans backlink.

## 9. Comprehension Questions (answers recorded)

1. Which publications must precede an external action, and which action? The
   pre-launch checkpoint precedes process creation; a successor's first
   checkpoint precedes its first ordinary dispatch; an undurable receipt
   precedes the next ordinary dispatch.
2. Why may a receipt wait for the end of its turn? The turn that commits a
   launch result dispatches nothing more (`_child_launch_started_this_turn`),
   `_scheduler_undurable` gates later dispatch, and startup release needs
   handoff or a durable receipt.
3. What does a successor read from a predecessor's residents? Lineage, class,
   process evidence and reservation references through
   `_adopt_scheduler_evidence`; the custody guard
   (`manager_launch_custody_pending`) reads the reservation references; the
   drain's coverage rule reads only the TIDs.

## 10. Tasks (executed)

1. RED tests in `tests/core/test_manager_checkpoint_volume.py` against the
   baseline `manager.py`. Observed failures: three reaps in one turn appended
   3 rows (expected 1); eight launches appended 37 rows (bound: fewer than
   32); the published resident carried the whole task snapshot; a failed
   receipt stayed undurable through the next turn's dispatch.
2. Unit of work and barriers in `weft/core/manager.py`:
   `_process_reactor_turn`, `_commit_scheduler_checkpoint`,
   `_publish_undurable_receipts`, and the sites `_checkpoint_spawned_admission`,
   `_reconcile_launch_now`, `_refresh_scheduler_evidence`,
   `_retire_scheduler_residents`. Barrier sites unchanged.
3. Slim residents and no deep copies in `Manager._build_tid_state_payload`;
   `MANAGER_CHECKPOINT_RESIDENT_FIELDS` in `weft/_constants.py`.
4. Spec delta (section 8) and backlinks.

Stop gates honoured: no new queue, timer, dependency or execution path; no
existing custody or recovery test changed.

## 11. Testing Plan and Results

New tests (real broker rows, real Manager turns, real child processes, no
mocks except an injected broker write failure at the state-queue boundary):

- `test_one_turn_publishes_one_checkpoint_for_all_reaped_children`: three
  exited children reaped in one turn append exactly one checkpoint.
- `test_checkpoint_appends_per_launch_stay_below_one_per_custody_event`: eight
  launches, released together, append fewer than `4 * count` rows. Observed:
  baseline 37 on SQLite; after the change 21 to 23 on SQLite and 21 on
  PostgreSQL.
- `test_launch_turn_publishes_receipt_and_successor_recovers_from_slim_checkpoint`:
  the turn that commits the launch result appends once and that row holds the
  receipt; after handoff the resident holds only custody fields; a successor
  bootstrapped from that checkpoint adopts the live task, rebuilds its
  frontier and does not hold it as ambiguous.
- `test_pre_handoff_checkpoint_publishes_every_manager_authored_field`: guard
  that every field the Manager records is published.
- `test_undurable_receipt_publishes_before_the_turn_dispatches`: a receipt
  left undurable by a failed append publishes before dispatch, and the same
  turn dispatches the waiting request. Periodic reconciliation is pushed out
  of the measured turn, so only the pre-dispatch barrier can publish it.

Existing owners rerun unchanged: `tests/core/test_admission_recovery.py`
(including the diagnostic and checkpoint gate tests),
`tests/core/test_manager_family_maintenance.py`,
`tests/core/test_family_dispatch_scaling.py`,
`tests/core/test_family_source_reconciliation.py`,
`tests/core/test_manager_burst_bookkeeping.py`, `tests/core/test_manager.py`,
`tests/core/test_task_state.py`, `tests/core/test_family_scheduling.py`,
`tests/core/test_work_conserving_admission.py`,
`tests/core/test_connection_admission.py`, `tests/core/test_admission.py`,
`tests/cli/test_cli_manager.py`, `tests/tasks/test_liveness_monitor.py`,
`tests/tasks/test_task_monitor.py`, `tests/system/test_family_recovery.py`,
`tests/system/test_family_matrix.py`, `tests/system/test_family_scheduling.py`,
`tests/system/test_child_admission.py`, and on PostgreSQL also
`tests/system/test_measured_admission_pg.py`.

Commands (SQLite with `-n 4`; PostgreSQL through `./bin/pytest-pg` with
`PYTEST_XDIST_AUTO_NUM_WORKERS=4`):

```bash
./.venv/bin/python -m pytest <targets above> -q -n 4
PYTEST_XDIST_AUTO_NUM_WORKERS=4 ./bin/pytest-pg <targets above>
./.venv/bin/ruff check .
./.venv/bin/ruff format --check weft/core/manager.py weft/_constants.py tests/core/test_manager_checkpoint_volume.py
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/python -m pytest tests/specs -q -n 4
./bin/check-doc-paths
```

All passed; exact counts are in the execution record below. The admission
stress benchmark is the coordinator's.

Observable success after landing: in the stress run, rows in
`weft.state.tasks.<manager_tid>` per launched task fall from about 4.1 to
about 2 to 3, the mean row size falls from about 127 KB to about 40 KB, and
the `scheduler_checkpoint` phase share falls with them.

## 12. Rollback and Rollout

Rollback is a revert of the commit. No queue, row format field or reader
changes shape for readers: old and new Managers read only the published
custody fields, so a mixed rollout and either direction of rollback recover
from each other's checkpoints. No one-way door.

## 13. Out of Scope and Owner Proposals

Not changed: the diagnostic refusal append (it may add one append per due
generation; merging it would let a diagnostic clear undurable responsibility,
which [MA-1.8] forbids), the startup-release condition, reap cost, wait
setup, and SimpleBroker.

Proposals for the owner (not implemented):

- P1, candidate 4. Most residents are past snapshot handoff; their own
  `weft.state.tasks.<tid>` carries their lineage and process evidence, so the
  checkpoint copy is redundant for recovery. It still feeds the "covered" rule
  in bootstrap and in the system-STOP drain, which reads only resident TIDs, and
  the frontier stacks. A TID-only entry for handed-off residents would shrink
  checkpoints by roughly the handed-off fraction (most of 94 in the stress
  run). Bootstrap would then adopt such residents only from their own
  snapshots. This changes the [MF-5]/[OBS.6] resident content rule and the
  bootstrap reader, so it is a spec decision.
- P2. Fold the receipt into the next dispatch's pre-launch checkpoint, which
  precedes that process creation but follows that request's reservation. This
  would remove the receipt-turn append but reword [MANAGER.11] and the
  [MA-1.8] "before another ordinary dispatch" rule. Not recommended without
  measurement.
- Rejected: publishing retirements only with the next publication. An idle
  Manager would then keep a stale custody fact in its newest checkpoint
  indefinitely.

## 14. Fresh-Eyes Review (author, separate pass)

- Every cited symbol, file and section resolves in the tree.
- Checked for a second publication path: there is one publisher
  (`_publish_scheduler_checkpoint`) and one commit rule; the turn state only
  decides when it runs.
- Checked the failure rule against the existing gate test
  `test_diagnostic_append_preserves_actual_bootstrap_and_checkpoint_gates`: a
  first draft retried a failed pre-dispatch publication at turn end, which
  let the second write of a generation succeed and cleared the gate. The
  `"failed"` turn state fixes that; the test is unchanged.
- Checked out-of-turn paths: stopping turns and cleanup run with the turn
  `"idle"`, so their publications are immediate as before.
- Ambiguity left for review: whether the slim resident set (P1 aside) should
  be owner-decided (class A) rather than C. It is recorded as C because every
  reader uses only these fields and the spec already lists them.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|
| [MA-1.8] | Coalesce receipts to turn end and wake dispatch after the commit | Receipts publish before the turn's dispatch when undurable; no wake | The barrier at the guarded point keeps same-turn dispatch and needs no extra wake or turn | Section 8 insertion 2 |

## Review and execution record

Author family: Claude (implementation agent, Track B).

Tier 1, Codex (codex exec -s read-only, reasoning effort high), 2026-11-08,
scoped-change review of plan+implementation at 1f444e6f against 08c2f87b.
Verdict: no blocker.

| ID | Severity | Finding | Disposition |
| --- | --- | --- | --- |
| F1 | P3 | Section 7 labelled the wider crash ambiguity window and the delayed PostgreSQL startup release as C and said "No row is class D", although both narrow recovery availability or readiness latency; the citations explained preserved safety, not why the tradeoff satisfies the owner's rule. | Accepted, fixed: both rows reclassified D with the tradeoff stated under [WT-4.4] and the kept custody rules ([WT-2a.13]/[WT-4.3] ambiguous hold, [MA-1.8] release and dispatch rules); the closing line now names the two D rows. No implementation change. |
| F2 | P3 | `test_undurable_receipt_publishes_before_the_turn_dispatches` let bootstrap start the one-second maintenance deadline before setup; slow setup let baseline `_refresh_scheduler_evidence` publish the failed receipt before dispatch, so the test's baseline RED depended on setup speed. | Accepted, fixed: the test sets `_admission_refresh_after` one minute ahead immediately before the measured turn, as the reap test does. Re-verified: fails on baseline `manager.py` (08c2f87b) at `assert tid not in manager._scheduler_undurable`; passes on the candidate. |

Execution record: implementation, spec delta and tests land in one commit on
`perf/manager-checkpoints` from base `08c2f87b`. Verification on the final
tree, over the section 11 targets:

- SQLite, `-n 4` (with `tests/specs`): 1374 passed, 5 skipped (PostgreSQL-only).
- PostgreSQL, `./bin/pytest-pg` with 4 workers (with
  `tests/system/test_measured_admission_pg.py`): 1210 passed, 12 skipped.
- `ruff check .`, `ruff format --check` on touched Python files, the repo
  mypy command (500 files) and `./bin/check-doc-paths`: clean.
- `tests/conftest.py` registers the new module in `_SHARED_MODULES`, as
  `tests/specs/test_test_audit_policy.py` requires.

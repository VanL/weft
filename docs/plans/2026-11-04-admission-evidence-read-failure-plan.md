# Admission Evidence: A Failed Read Changes No Mode

Status: promoted
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.8]; docs/specifications/07-System_Invariants.md MANAGER.11
Superseded by: none

Class: 5 (spec-changing) with a risky trigger. One sentence is added to
[MA-1.8] stating what a failed read of task evidence does (nothing), so
materiality requires a spec change. The code change edits the Manager's
ordinary dispatch gate and its reconcile and reap paths, which sit on the
durable spine (`Manager -> queues/state log`), so the hardening-plans
checklist applies in full and review happens before implementation. Not
+P: no runbook, skill, or gate changes. Plan type: implementation with spec
revision. Promotion strategy B (atomic with the code; see `## Proposed Spec
Delta`). One-way doors: none.

This plan follows the owner's 2026-11-04 rulings recorded in
`docs/plans/2026-11-03-watcher-hint-gated-observation-plan.md`
`## Owner Decisions` item 2 ("If those underlying reads fail, do nothing is
the correct response"; "And the proposal is against the theory also"), the
owner's audit request of the same day ("This makes me worried that the
implementation of the plans was over armored or over engineered"), and the
two removals committed at `c857e10b`. Those removals took out series
artifacts (plan 4 and plan 6). This plan takes out the one pre-series
mechanism of the same class that the audit found: the Manager's
admission-evidence mode, introduced in release 0.9.110 (`af844d56`,
2026-10-13).

## 1. Goal

Delete `Manager._admission_evidence_unavailable` and everything that exists
only to serve it. The flag is a Manager-local boolean that goes true when a
read of a task's state snapshot raises during the periodic reconcile cycle
or during a reap, and while true it closes the ordinary dispatch gate,
pins the failed task first in the resident ordering, zeroes the refresh
timer, makes later reaps return early, and keeps the maintenance timer
armed. Apart from the bootstrap case named below, none of that protects
anything. Launch custody under MANAGER.11 is
held by what the Manager refrains from doing (it never deletes a reserved
request without proof), and a failed read leaves the resident record, the
reservation, and the published checkpoint exactly as they were. The
write-side rule the spec does state, "failed checkpoint publication gates
dispatch and retries through bounded maintenance", is implemented by
`_scheduler_undurable` and `_scheduler_checkpoint_dirty`, not by this flag,
and a failed post-launch checkpoint write never sets it (the writer
returns `False` instead of raising). One write failure does reach it: a
successor's bootstrap converts a failed publication of its first checkpoint
into a `RuntimeError`, and the flag is then the only thing keeping the
restored pending work from dispatching. That gate is spec-mandated and is
kept, keyed on the bootstrap state the Manager already has
(`_admission_bootstrapped`) instead of on a failure mode. After this plan,
a failed evidence read is logged at debug level and nothing else happens,
which is what the immediate reconcile path `_reconcile_launch_now` already
does today. The spec gains one sentence saying so, and one clause naming
the bootstrap wait, so that no future reader reconstructs the flag from
the gap.

## 2. Source Documents

Source specs:

- `docs/specifications/03-Manager_Architecture.md` [MA-1.8], the paragraph
  at line 478 (at `c857e10b`) that carries "failed checkpoint publication
  gates dispatch and retries through bounded maintenance", and the
  `_Implementation mapping_` entry for [MA-1.8] at line 585 and the
  sentence at line 592 that names `Manager._refresh_scheduler_evidence`.
- `docs/specifications/07-System_Invariants.md` MANAGER.11 (line 1112):
  "successful launch retains the exact reserved request until validated
  task-snapshot handoff or positive whole-process settlement under [MF-6].
  ... Failed publication gates further ordinary dispatch. Missing evidence
  never authorizes replay." This invariant is unchanged by the plan; it is
  the reason the flag is unnecessary.
- `docs/specifications/03-Manager_Architecture.md` [MA-1.8] line 450,
  "Failed or unknown observation defers dispatch without rejecting accepted
  work." That sentence is about the usage observation (connection counts),
  handled by `AdmissionController` and the admission lanes, not about
  evidence reads. It is unchanged and the plan must not widen it.

Theory (governing, unchanged):

- `docs/program-theory.md` [WT-4] 4.4 "No over-armoring" (line 464):
  "Engineer for real execution and failure boundaries, not impossible or
  adversary-invented states. Best-effort paths try, catch, and log
  diagnostics."
- `docs/program-theory.md` [WT-2a.8] (lines 315-319): SimpleBroker owns
  queue mechanics; a queue-level need is met in SimpleBroker, never
  reimplemented in Weft.
- `docs/program-theory.md` [ALT-WT-010] (lines 696-709): "In-memory state
  stays a hint." A mode flag that steers dispatch is in-memory state that
  has become a decision.

Origin of the flag (context, not normative):

- `docs/plans/2026-10-13-connection-aware-child-admission-plan.md` lines
  449-455 (an implemented historical plan; its index row is `draft`,
  superseded by `docs/plans/2026-10-14-measured-admission-family-scheduling.md`): "Independent review caught reap-first read
  failures that did not close admission. The regression reproduced that
  failure before repair. Reap failures now mark evidence unavailable and
  prioritize that record. While unavailable, subsequent reaps retain debt
  and defer to bounded maintenance." That review treated "admission not
  closing on a read failure" as a defect. Under the 2026-11-04 ruling it is
  the correct behavior. The regression test the plan mentions no longer
  exists under any name: `grep -rn "_admission_evidence_unavailable" tests/`
  returns nothing at `c857e10b`, and no spec sentence describes the mode.

Guidance the implementer must follow:

- `docs/agent-context/runbooks/writing-plans.md` and
  `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md` and
  `skills/call-agent/SKILL.md` (cross-family review)
- `docs/agent-context/decision-hierarchy.md` [DOM-15]
- `CLAUDE.md` §4 (house style; no attribution trailers)

## 3. Context and Key Files

### Current structure (what exists at `c857e10b`)

All line numbers below are at `c857e10b` and were verified by `grep -n`
and `sed -n` before this plan was written. Re-verify before editing; the
file is large and moves.

The resident set. `Manager._scheduler_residents` (`weft/core/manager.py`
line 654) is a dictionary from task ID to an evidence record: lineage
fields (`context`, `root_tid`, `submitter_tid`, `ancestry_tids`,
`scheduling_class`, `connection_owner`), `host_processes` (PID plus
creation time), `hostname`, `launched_ns`, and, until acknowledged, the
`source_queue`, `reserved_queue`, and `request_id` of the spawn request
the launch came from. It is the in-memory form of the Manager's checkpoint
under MANAGER.11: the obligations a successor inherits at bootstrap.

The write-side gate that already implements the spec sentence.
`_checkpoint_spawned_admission` (line 1584) records the launched PID, adds
the task to `_scheduler_undurable` (line 1599), and calls
`_publish_scheduler_checkpoint` (line 1543). That publisher calls
`BaseTask._register_tid_state` (`weft/core/tasks/base.py` line 2394),
which catches `BrokerError`, `OSError` and `RuntimeError` on the write,
logs once, and returns `False`. On `False`, `_scheduler_checkpoint_dirty`
stays `True` and the task stays in `_scheduler_undurable`; on `True`, the
undurable set drops every resident that has host processes. Both dispatch
sites check `_scheduler_undurable` (lines 5479 and 5636). The dirty flag is
retried at line 2574 (periodic) and line 6093 (immediate). This is the
whole implementation of "failed checkpoint publication gates dispatch and
retries through bounded maintenance", and
`tests/core/test_manager_family_maintenance.py::test_undurable_internal_launch_blocks_ordinary_reservation_until_checkpoint`
(line 217) is its firing test. The flag this plan removes is not on that
path: a post-launch checkpoint write failure returns `False` and never
raises into the reconcile cycle's `except`.

The one write failure that does reach the flag.
`_bootstrap_scheduler_evidence` (lines 2587-2606) runs once per Manager
incarnation, inside the periodic cycle while `_admission_bootstrapped` is
false: it adopts residents from dead Managers' checkpoints and from task
snapshots, recovers scheduler paths (marking residents without host
processes undurable, lines 2695-2701), restores pending family hints from
the label queues (`_restore_family_pending`, line 2817), recovers orphan
reservations, and then publishes the successor's first checkpoint. If
that publication returns `False`, line 2606 raises
`RuntimeError("unable to checkpoint adopted scheduler responsibility")`,
which the cycle's `except` catches, setting the flag and leaving
`_admission_bootstrapped` false. At that point the family scheduler
already holds the restored pending requests; `_classify_family_requests`
declines further work while not bootstrapped (line 6151), but neither
dispatch site checks `_admission_bootstrapped`, so with the flag gone and
nothing in its place a successor whose first checkpoint did not land
would dispatch restored work. MANAGER.11 ("Failed publication gates
further ordinary dispatch") forbids that, so the plan replaces the flag's
disjunct at both dispatch sites with `not self._admission_bootstrapped`:
a true state the Manager already keeps, which also arms the wake timer
(line 8596) and reruns the whole bootstrap on the next due turn, exactly
as today. No test covered this case at `c857e10b`
(`grep -rn "unable to checkpoint adopted" tests/` is empty); Task 2 adds
one. Proof that the replacement is needed and sufficient, run by the
author on 2026-11-04 against a scratch copy of the six removals and then
restored: with the flag removed alone the new bootstrap test fails at its
dispatch-blocked assertion; with `not self._admission_bootstrapped` in
place it passes, the two read-failure tests pass, and the four
neighbouring Manager test files pass (320 tests).

The flag and its six sites:

1. Line 670, in `__init__`: `self._admission_evidence_unavailable = False`.
2. Lines 2511-2532, `_record_reaped_admission(tid)`, called from the child
   reap path at line 4655 inside a `finally` after the child process is
   joined. Line 2515 returns early when the flag is set or the task is not
   resident. Lines 2517-2520 read the task's latest snapshot with
   `read_task_state_snapshot` and adopt it. Lines 2521-2531, on
   `BrokerError`, `OSError`, `RuntimeError`, `ValueError` or `TypeError`:
   set the flag, rebuild `_scheduler_residents` with the failed task first,
   set `_admission_refresh_after = 0.0`, log at debug, and return. Line
   2532, on success, calls `_retire_scheduler_residents((tid,))`.
3. Lines 2534-2585, `_refresh_scheduler_evidence()`, the periodic cycle
   described in `## 1`. Its `try` spans the bootstrap read, the per-task
   read-adopt-advance loop, `_maintain_orphan_reservations`,
   `_retire_scheduler_residents`, and the dirty-checkpoint publish. Line
   2576 catches `BrokerError`, `OSError`, `RuntimeError` (including the
   bootstrap's own `RuntimeError` at line 2606 on a failed first
   publication), `ValueError`, `TypeError` and `KeyError`; line 2577 sets
   the flag; line 2578 logs
   "Unable to reconcile scheduler evidence". The `else` at 2579-2581 sets
   `_admission_bootstrapped = True` and clears the flag. The `finally` at
   2582-2585 re-arms `_admission_refresh_after` to now plus
   `MANAGER_ADMISSION_RECHECK_SECONDS`.
4. Line 5478, in `_queue_has_pending` (the reactor's readiness check for
   the public inbox): the inbox is not pending for ordinary dispatch while
   `_startup_dispatch_blocked()` or the flag or `_scheduler_undurable` is
   set; it returns `_family_classification_pending` instead.
5. Line 5635, in `_process_queue_message` (the dispatch gate itself): the
   same three-way disjunction returns `False` for the public inbox.
6. Line 8598, in `_admission_retry_timeouts`: the maintenance timer is
   appended while the Manager is not bootstrapped, or has residents, or
   the flag is set.

Nothing else reads the flag. It appears in no status payload (the phase
timing emitter at line 920 reports `undurable_launches`, not this flag),
no control reply, no spec sentence, and no test.

The in-file precedent for the correct shape. `_reconcile_launch_now(tid)`
(lines 6081-6103) is the immediate reconcile run when a launch completes.
It performs the same read, adopt, advance, and dirty-publish sequence as
one iteration of the periodic cycle, catches the same classes plus
`DatabaseError`, logs "Launch evidence handoff deferred", and sets no flag.
The periodic and reap paths after this plan behave the same way.

When the read can fail. `read_task_state_snapshot`
(`weft/core/task_state.py` line 54) peeks the newest row of
`weft.state.tasks.<tid>` and pages backward past malformed rows. The owner's
2026-11-04 probe (recorded in the plan 13 file cited above) established
that on an open WAL-mode SQLite connection these reads do not fail while
the database is readable; they fail on a database that is no longer
readable, where the next read fails too. The `ValueError` and `TypeError`
members of the `except` tuples come from `_adopt_scheduler_evidence` (a
snapshot from another context, or a lineage field that changed) and from
`_admission_host_processes` (a malformed process list). Those are
programming errors or corrupt rows, not environmental failures, and are
out of scope here (see `## 9`).

### Files to modify

- `weft/core/manager.py` (the six sites above; no other edit)
- `tests/core/test_manager_family_maintenance.py` (one new test)
- `docs/specifications/03-Manager_Architecture.md` ([MA-1.8] sentence;
  `## Related Plans` backlink)
- `CHANGELOG.md` (`## [Unreleased]`, one bullet)
- `docs/plans/README.md` (this plan's row; `draft` now, `completed` at
  closeout)

### Read first

- `docs/program-theory.md` [WT-4] 4.4, [WT-2a.8], [ALT-WT-010]
- `docs/specifications/07-System_Invariants.md` MANAGER.11 (line 1112)
- `docs/specifications/03-Manager_Architecture.md` [MA-1.8] lines 448-490
- `weft/core/manager.py` lines 1543-1553 (`_publish_scheduler_checkpoint`),
  1584-1602 (`_checkpoint_spawned_admission`), 2511-2585 (reap and
  periodic reconcile), 2587-2606 (`_bootstrap_scheduler_evidence`),
  2695-2701 (`_recover_scheduler_paths`), 5466-5484 (`_queue_has_pending`),
  5615-5640
  (`_process_queue_message`), 6081-6103 (`_reconcile_launch_now`),
  8583-8601 (`_admission_retry_timeouts`), and line 8837, where every
  reactor turn calls `_refresh_scheduler_evidence` (its own timer check at
  line 2536 decides whether the cycle runs)
- `weft/core/tasks/base.py` lines 2394-2425 (`_register_tid_state`)
- `tests/core/test_manager_family_maintenance.py` lines 1-110 (module
  docstring, imports, the `manager` fixture) and 217-273 (the undurable
  test, whose setup the new test reuses)

### Comprehension questions (answer in the Execution log before the first edit)

1. A checkpoint write fails with `BrokerError`. Which code sets the flag
   this plan removes?
   Expected: only the bootstrap. `_register_tid_state` catches the error
   and returns `False`; `_publish_scheduler_checkpoint` records the dirty
   flag and keeps the task undurable; the post-launch path never raises.
   `_bootstrap_scheduler_evidence` alone converts that `False` into a
   `RuntimeError` at line 2606, which the cycle's `except` catches.
2. After a failed snapshot read for a resident task, what has changed about
   that task's custody?
   Expected: nothing. The resident record is intact, the reserved spawn
   request is still in its reserved queue, and the last published
   checkpoint still names the task. MANAGER.11 is held by the absence of a
   delete, not by a dispatch halt.
3. What already handles a failed read on the immediate reconcile path?
   Expected: `_reconcile_launch_now` catches, logs "Launch evidence handoff
   deferred", and continues. No flag, no reorder, no timer change.

## 4. Invariants and Constraints

Must not change:

- MANAGER.11 custody. A reserved spawn request is deleted on three paths,
  none of which this plan touches: `_ack_launch_custody` (reached through
  `_advance_launch_custody` on a validated handoff or
  `_retire_scheduler_residents` on positive process exit), the child reap
  at line 4661 (a terminal child's own request, after its process is
  joined), and the reserved-policy `CLEAR` branch at line 5866 (positively
  unlaunched requests by exact ID). `_snapshot_hands_off_launch` is not
  edited.
- The write-side gate. `_scheduler_undurable`, `_scheduler_checkpoint_dirty`,
  `_publish_scheduler_checkpoint`, `_register_tid_state`, and the two
  dispatch checks on `_scheduler_undurable` are not edited. The undurable
  test at line 217 must pass unchanged.
- The four reconcile steps (read, adopt, advance, retire) and their order,
  the bootstrap branch, `_maintain_orphan_reservations`, and the
  `MANAGER_ADMISSION_REFRESH_BATCH_SIZE` round-robin selection.
- Startup feedback and `_startup_dispatch_blocked()`; the family scheduler;
  the admission lanes and `_clear_admission_retry`.
- The `except` tuples at lines 2521 and 2576 keep their current members.
  This plan removes what the handlers do, not what they catch.
- Exception classes caught on the reap path and the periodic path remain
  logged at debug with `exc_info=True`, as now.

Hidden couplings (checked; each is safe):

- Maintenance timer arming. The periodic cycle is attempted on every
  reactor turn (line 8837) and runs whenever its own deadline at line 2536
  has passed; `_admission_retry_timeouts` only decides whether the reactor
  wakes for that deadline when nothing else wakes it. After the change it
  arms that wake while the Manager is not bootstrapped or has residents. A
  failed bootstrap leaves `_admission_bootstrapped` false, so the bootstrap
  retry is unchanged. A failed cycle with residents is retried on the next
  due turn as now, because the `finally` at 2582 re-arms the deadline
  regardless of outcome. A failed cycle with no residents after a completed
  bootstrap no longer forces a wake of its own; the family-repair deadline
  (line 8587, armed once bootstrapped) and any other reactor wake attempt
  it. Orphan reservation pages can outlive the bootstrap with no residents
  (they come from dead Managers' checkpoints at line 2650, not from
  residents); `_maintain_orphan_reservations` retries them on the next
  cycle that runs, as now.
- Reap ordering. Removing the resident reorder at lines 2523-2526 means a
  task whose reap-time read failed keeps its round-robin position instead
  of being examined first on the next cycle. The periodic cycle visits it
  in its round-robin turn, which depends on the resident count and on
  successful passes, not on the batch size alone. Under the ruling,
  the read failed because the database is unreadable, so examining it
  first buys nothing.
- Reap early return. Line 2515 becomes `if tid not in
  self._scheduler_residents: return`. The `return` after the debug log at
  line 2531 stays, so a reap whose read failed does not proceed to
  `_retire_scheduler_residents`; the periodic cycle retires it on positive
  exit evidence as now.
- The `else` branch. `_admission_bootstrapped = True` at line 2580 stays;
  only the flag clear at 2581 goes.
- Bootstrap publication. Covered in `## 3`: the two dispatch sites gain
  `not self._admission_bootstrapped` in place of the flag. Every other
  reader of `_admission_bootstrapped` (lines 2359, 2543, 5278, 6151, 8587,
  8596) is unchanged.

Error-path priorities:

- Fatal: nothing new. A corrupt reservation or a deleted request without
  proof would be fatal; this plan touches neither.
- Best-effort: the reconcile read and the reap read. On failure, log at
  debug and continue. No mode, no retry flag, no reorder.

Theory check: the removed mechanism is the class [WT-4] 4.4 names ("not
impossible or adversary-invented states"), it duplicates a queue-level
concern ([WT-2a.8]: whether the database is readable is SimpleBroker's to
report, not Weft's to remember), and it lets in-memory state decide
dispatch ([ALT-WT-010]: a hint that became a gate). Removing it moves the
code toward the theory; nothing in the plan reverses a standing invariant
or a refused alternative.

Review gates:

- no new execution path; no drive-by refactor of the reconcile cycle; no
  new dependency; no public CLI shape change
- no mock of queues or processes in the new test beyond the one injected
  broker read failure at the real broker type (the pattern the undurable
  test uses for an injected write failure)
- self-driven fresh-eyes review before cross-family review; cross-family
  review of the plan and the delta before implementation (class 5 with
  hardening); cross-family review of the completed work before closeout

### Rollout, rollback, and one-way doors (stated before the tasks)

Rollout: one commit carries the code, the test, the spec sentence, the
backlink, and the CHANGELOG bullet (strategy B). There is no persisted
format, queue name, or payload change; a Manager built before and after
this commit reads the same checkpoints.

Rollback: `git revert` of that commit. No data migration, no queue
residue, no compatibility window.

One-way doors: none.

### Observable-difference register

The owner's acceptance rule: a simplification may produce no observable
diminution. Each row is A (accepted by the owner's 2026-11-04 ruling),
N (no observable difference), C (changed, not a diminution), or D
(diminution, which would block).

| # | Behavior at `c857e10b` | After this plan | Class |
|---|---|---|---|
| 1 | A failed snapshot read during the periodic cycle closes ordinary dispatch until a later cycle succeeds. | Ordinary dispatch continues. Custody is unchanged in both cases. | A |
| 2 | A failed snapshot read during a reap closes ordinary dispatch, pins that task first, and forces the next cycle on the next reactor turn. | The reap logs and returns. The task keeps its round-robin place and is visited on the next due cycle. | A |
| 3 | While the flag is set, later reaps return before reading. | Every reap reads its own task's snapshot. More reads, never fewer. | C |
| 4 | The maintenance timer stays armed while the flag is set, even with no residents after bootstrap. | The timer arms on residents or an incomplete bootstrap only. | A |
| 5 | A failed post-launch checkpoint write keeps the task undurable and closes dispatch until the write lands. | Unchanged. | N |
| 5a | A failed bootstrap checkpoint publication closes ordinary dispatch (through the flag) and reruns the bootstrap on the next due turn. | Closes ordinary dispatch through `not _admission_bootstrapped` and reruns the bootstrap on the next due turn. Same observable behavior, different key. | N |
| 5b | A failed bootstrap read closes ordinary dispatch (through the flag) and reruns the bootstrap. | Same, through `not _admission_bootstrapped`. The bootstrap is where a Manager learns its inherited obligations; not dispatching before it completes is an ordering rule, not failure handling. | N |
| 6 | `_reconcile_launch_now` logs a failed read and continues. | Unchanged; the periodic and reap paths now match it. | N |
| 7 | Debug log lines "Unable to reconcile scheduler evidence" and "Unable to refresh reaped task admission evidence". | Both kept, same level, same `exc_info`. | N |

No D row exists. Rows 1, 2 and 4 are the ruling applied; rows 5a and 5b
are the round-1 finding F1 resolved under MANAGER.11.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Spec Baseline

- `c857e10b` — `docs/specifications/03-Manager_Architecture.md` and
  `docs/specifications/07-System_Invariants.md` at plan authoring time.
- Plan type: implementation with spec revision. Promotion strategy B
  (atomic): the sentence lands in the same commit as the code and the
  test. That landing commit also moves this plan's `Status:` and index row
  to `promoted`, so `bin/check-doc-paths` checks the two Replacement blocks
  against the spec file from then on; the closeout commit moves both to
  `completed`.
- Promotion baseline identifier: `e3817033`, the landing commit (spec sentence, backlink, code, tests, CHANGELOG, `promoted`).

## Proposed Spec Delta

Promotion strategy:

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| `docs/specifications/03-Manager_Architecture.md` | B — atomic with the code | [MA-1.8] paragraph at line 478; `## Related Plans` |

### `docs/specifications/03-Manager_Architecture.md` [MA-1.8] — extend the sentence that opens "Before another ordinary dispatch" and closes "retries through bounded maintenance." (inside the single-line paragraph at line 478)

Current text (the sentence, verbatim):

> Before another ordinary dispatch, task-owned snapshot handoff/positive settlement or a successful post-launch checkpoint must preserve recovery custody; failed checkpoint publication gates dispatch and retries through bounded maintenance.

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```text
Before another ordinary dispatch, task-owned snapshot handoff/positive settlement or a successful post-launch checkpoint must preserve recovery custody; failed checkpoint publication gates dispatch and retries through bounded maintenance. A failed read of a task's state snapshot during reconciliation or reap is logged and changes nothing: the resident record, its reservation, the published checkpoint, and ordinary dispatch all stand, and no Manager mode records the failure. A successor dispatches no ordinary work until its bootstrap completes: its inherited checkpoints read and its own first checkpoint published.
```

Carried forward: the custody sentence word for word. Added: one sentence
stating the read-failure rule, and one stating the bootstrap wait that the
removed flag used to supply (round-1 F1). Audited: no other clause in the paragraph
mentions evidence reads; the "retries snapshot observation once" clause
earlier in the same paragraph is about a missing snapshot on a live
wrapper, not a failed read, and is unchanged.

### `docs/specifications/03-Manager_Architecture.md` `## Related Plans` — insert one bullet at the top of the list (after line 35)

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```text
- [Admission Evidence: A Failed Read Changes No Mode](../plans/2026-11-04-admission-evidence-read-failure-plan.md) removes the Manager's admission-evidence mode: a failed read of a task's state snapshot during reconciliation or reap is logged and changes nothing under [MA-1.8]; a failed post-launch checkpoint publication keeps gating dispatch through the undurable set, and a successor's failed first publication keeps gating it through the incomplete bootstrap.
```

### Spans audited and unchanged

- `docs/specifications/07-System_Invariants.md` MANAGER.11 (line 1112):
  "Failed publication gates further ordinary dispatch" is a write rule and
  stays as written.
- `docs/specifications/03-Manager_Architecture.md` line 450, "Failed or
  unknown observation defers dispatch without rejecting accepted work":
  the usage observation, unchanged.
- The `_Implementation mapping_` entry at line 585 names
  `tests/core/test_manager_family_maintenance.py` already; the new test
  lives there, so the entry needs no edit. The sentence at line 592
  describing `_refresh_scheduler_evidence` stays true.

## 5. Tasks

### Task 0. Read, answer, record

- Read the `## 3` Read-first list and answer the three comprehension
  questions in the Execution log before any edit. A wrong answer is a
  stop-and-re-read.
- Re-verify the six line numbers with `grep -n "_admission_evidence_unavailable" weft/core/manager.py`
  (expected at `c857e10b`: exactly 8 lines, 670, 2515, 2522, 2577, 2581,
  5478, 5635, 8598; the six sites in `## 3` cover them, since the reap
  path and the periodic cycle each hold two).

### Task 1. Independent review of plan and delta (before any code)

- Cross-family round per `skills/call-agent/SKILL.md`, reviewing this
  file including `## Proposed Spec Delta`. Record the round in
  `## Review Log` with the disposition table. Code work starts only after
  the round's blockers are dispositioned.

### Task 2. RED: the firing tests (no production edit)

- Outcome: two tests. The first fails at `c857e10b` because the flag
  closes dispatch on a failed read, and passes once the flag is gone,
  while asserting that the reservation is untouched in both states. The
  second passes at `c857e10b`, fails if the flag is removed without the
  bootstrap gate, and passes again with it: it is the firing test for the
  bootstrap clause and for MANAGER.11's publication rule at bootstrap.
- Files to touch: `tests/core/test_manager_family_maintenance.py`
- Read first: lines 217-273 of that file (the undurable test); the
  `manager` fixture at line 80.
- Add, after the undurable test:

```python
@pytest.mark.parametrize("entry", ["reconcile", "reap"])
def test_failed_evidence_read_leaves_custody_and_dispatch_unchanged(
    manager: Manager, monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    """A failed snapshot read is logged and changes nothing [MA-1.8].

    Custody is held by not deleting the reservation, so a read failure on
    the periodic reconcile path or the reap path leaves the resident record,
    the reserved request, and ordinary dispatch exactly as they were.
    """
    manager._refresh_scheduler_evidence()
    source = manager._queue(manager._queue_names["inbox"])
    ordinary_id = int(source.write(request_body()))
    manager._classify_family_requests()
    internal = manager._queue(manager._queue_names["internal_inbox"])
    internal_id = int(internal.write(request_body()))
    reserved = manager._queue_names["internal_reserved"]
    assert internal.move_one(reserved, exact_timestamp=internal_id) is not None
    spec = manager._build_child_spec(json.loads(request_body()), internal_id)
    assert spec is not None
    identity = runtime_identity_for_task(
        spec, manager._db_path, internal=True, config=manager._manager_context().config
    )
    request = _ManagerChildLaunchRequest(
        spec,
        Consumer,
        None,
        None,
        None,
        True,
        internal.name,
        reserved,
        internal_id,
        identity,
    )
    manager._checkpoint_admission_launch(request)
    manager._checkpoint_spawned_admission(
        _ManagerChildLaunchResult(
            request, multiprocessing.current_process(), time.time_ns()
        )
    )
    assert not manager._startup_feedback
    assert str(spec.tid) not in manager._scheduler_undurable
    assert manager._queue_has_pending(source)

    with manager._get_connected_queue().get_connection() as broker:
        broker_type = type(broker)
    original_peek_many = broker_type.peek_many
    state_name = f"weft.state.tasks.{spec.tid}"

    def fail_state_read(self: Any, queue: str, *args: Any, **kwargs: Any) -> Any:
        if queue == state_name:
            raise BrokerError("injected task-state read failure")
        return original_peek_many(self, queue, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(broker_type, "peek_many", fail_state_read)
        manager._admission_refresh_after = 0.0
        if entry == "reconcile":
            manager._refresh_scheduler_evidence()
        else:
            manager._record_reaped_admission(str(spec.tid))

    assert manager._queue(reserved).peek_one(exact_timestamp=internal_id) is not None
    assert source.peek_one(exact_timestamp=ordinary_id) is not None
    assert manager._queue_has_pending(source)


def test_failed_bootstrap_checkpoint_keeps_restored_work_undispatched(
    manager: Manager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successor whose first checkpoint did not land dispatches nothing.

    Spec: [MA-1.8]; MANAGER.11 ("Failed publication gates further ordinary
    dispatch"). The bootstrap restores pending work before it publishes;
    that work must wait until the publication succeeds and the bootstrap
    completes.
    """
    manager._refresh_scheduler_evidence()
    source = manager._queue(manager._queue_names["inbox"])
    request_id = int(source.write(request_body()))
    manager._classify_family_requests()
    assert manager._family_scheduler.pending(request_id) is not None
    # Become a successor: forget the in-memory scheduler and bootstrap again.
    manager._family_scheduler = FamilyScheduler()
    manager._admission_bootstrapped = False
    with manager._get_connected_queue().get_connection() as broker:
        broker_type = type(broker)
    original_write = broker_type.write
    state_name = f"weft.state.tasks.{manager.tid}"

    def fail_checkpoint(self: Any, queue: str, *args: Any, **kwargs: Any) -> Any:
        if queue == state_name:
            raise BrokerError("injected bootstrap checkpoint write failure")
        return original_write(self, queue, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(broker_type, "write", fail_checkpoint)
        manager._admission_refresh_after = 0.0
        manager._refresh_scheduler_evidence()
        assert not manager._admission_bootstrapped
        assert manager._family_scheduler.pending(request_id) is not None
        assert not manager._process_queue_message(source.name, set())
        assert source.peek_one(exact_timestamp=request_id) is not None
    manager._admission_refresh_after = 0.0
    manager._refresh_scheduler_evidence()
    assert manager._admission_bootstrapped
    assert manager._queue_has_pending(source)
```

- Every name the tests use is already imported in that module at
  `c857e10b` (`json`, `multiprocessing`, `time`, `Any`, `pytest`,
  `BrokerError`, `Manager`, `_ManagerChildLaunchRequest`,
  `_ManagerChildLaunchResult`, `runtime_identity_for_task`, `Consumer`,
  `FamilyScheduler`; `request_body` is defined in the module). Add nothing
  to the import block.
- What the first test proves. The setup is the undurable test's setup
  without the injected write failure: an internal launch checkpointed
  before and after process creation, so the resident has a host process
  (the test process itself), is not undurable, has no startup feedback
  (internal launches get none), and the ordinary request is dispatchable.
  The injected failure is at the real broker type's `peek_many`, scoped to
  the one task-state queue, which is the exact read
  `read_task_state_snapshot` performs; nothing else is mocked. The last
  three assertions are the contract: the reserved request is still
  reserved, the ordinary request is still in its source queue, and the
  inbox is still ready for dispatch. The claim is readiness and retained
  reservation; the test does not drive `_process_queue_message` through a
  launch and does not inspect checkpoint contents, ordering, or timers.
- What the second test proves. A classified request leaves a pending label
  that a successor's bootstrap restores. With the successor's first
  checkpoint write failing at the real broker type's `write`, scoped to
  the Manager's own state queue, the restored request is pending in the
  scheduler, the dispatch gate returns `False`, the request is still in
  its source queue, and the Manager is not bootstrapped. Once the write
  succeeds, the bootstrap completes and the inbox is ready. The
  `_admission_bootstrapped` assertions read the state the gate keys on;
  the queue assertions carry the custody claim.
- Verified on 2026-11-04 by the author with both tests in a scratch file
  (deleted afterwards; copies kept outside the tree) across three states of
  `weft/core/manager.py`, the file restored to `c857e10b` after each:
  at `c857e10b`, the first test fails in both parametrizations at
  `assert manager._queue_has_pending(source)` with the reservation
  assertions passing, and the second passes; with the six removals and no
  replacement gate, the first passes and the second fails at
  `assert not manager._process_queue_message(source.name, set())`; with
  the removals plus `not self._admission_bootstrapped` at both dispatch
  sites, all three pass, and the four neighbouring files
  `tests/core/test_manager_family_maintenance.py`,
  `tests/core/test_family_source_reconciliation.py`,
  `tests/core/test_family_dispatch_scaling.py`, and
  `tests/core/test_manager.py` pass (320 tests), with `ruff` and `mypy`
  clean on the modified file.
- Verify: `./.venv/bin/python -m pytest tests/core/test_manager_family_maintenance.py -q -k "failed_evidence_read or failed_bootstrap_checkpoint"`
  must report 2 failed and 1 passed before Task 3 and 3 passed after.
- Stop if: either setup needs a patch beyond its one broker-type
  override, or a test wants to assert on the flag by name (it must not;
  the flag will not exist).

### Task 3. GREEN: remove the flag and key the bootstrap wait on bootstrap state (core work; same commit as Tasks 2 and 4)

- Files to touch: `weft/core/manager.py`
- Edits, by site:
  1. Line 670: delete the statement.
  2. `_record_reaped_admission`: line 2515 becomes
     `if tid not in self._scheduler_residents:`; delete lines 2513-2514
     (the comment about bounded maintenance owning recovery); in the
     `except` delete the flag assignment (2522), the resident rebuild
     (2523-2526), and the timer zeroing (2527); keep the `logger.debug`
     and the `return`. Rewrite the docstring to: `"""Refresh one exited
     wrapper's evidence; a failed read changes nothing [MA-1.8]."""`.
  3. `_refresh_scheduler_evidence`: delete line 2577 (the flag set) and
     line 2581 (the flag clear). The `except` keeps its tuple and its
     debug log; the `else` keeps `_admission_bootstrapped = True`; the
     `finally` is unchanged.
  4. `_queue_has_pending` line 5478: replace the line
     `or self._admission_evidence_unavailable` so the condition reads, in
     this order, `not self._admission_bootstrapped or
     self._startup_dispatch_blocked() or self._scheduler_undurable`.
  5. `_process_queue_message` line 5635: the same replacement, same
     order.
  6. `_admission_retry_timeouts` line 8598: delete the flag line; `ruff
     format` collapses the condition to the single line
     `if not self._admission_bootstrapped or self._scheduler_residents:`.
     Write it that way.
- Constraints: no other line in the file changes. Do not touch the
  `except` tuples, `_reconcile_launch_now`, the undurable set, or the
  checkpoint publisher. Do not "simplify" the reorder of residents at
  lines 2570-2572 (that one is the round-robin rotation on success and
  stays).
- Verify: `grep -c "_admission_evidence_unavailable" weft/core/manager.py`
  prints 0; `git diff --stat weft/core/manager.py` shows one file with 6
  insertions and 19 deletions (the author's scratch run of these exact
  edits); both Task 2 tests pass; the undurable test passes.
- Stop if: any test outside the two named starts failing, or the change
  wants to alter what the `except` catches.

### Task 4. Spec, CHANGELOG, backlink (same commit as Tasks 2 and 3)

- Apply the two Replacement blocks in `## Proposed Spec Delta` verbatim.
- `CHANGELOG.md` `## [Unreleased]`, under `### Changed`: "The Manager no
  longer enters an admission-evidence mode when a task-state snapshot read
  fails during reconciliation or reap: the failure is logged and nothing
  else changes, since launch custody is held by the retained reservation
  and checkpoint, not by a dispatch halt ([MA-1.8]). A failed post-launch
  checkpoint publication still gates dispatch through the undurable set,
  and a successor still dispatches nothing until its bootstrap, including
  its first checkpoint publication, completes."
- In the same landing commit, set this plan's `Status:` and its
  `docs/plans/README.md` row to `promoted`. Record the landing commit SHA
  as the promotion baseline identifier in `## Spec Baseline` in the
  closeout commit (Task 5), which is also where both move to `completed`.

### Task 5. Gates, completed-work review, closeout

- Run the final gates in `## 7`. Record each command and result in the
  Execution log.
- Cross-family review of the landed diff (plan + code + spec), one scoped
  round; record it in `## Review Log`.
- Closeout commit: `Status: completed`, index row `completed`, promotion
  baseline identifier filled, Execution log complete. Commit with
  `git -c user.name="VanL" -c user.email="van.lindberg@gmail.com"` and no
  attribution trailer. Do not push.

## 6. Testing Plan

- Harness: the `manager` fixture in
  `tests/core/test_manager_family_maintenance.py` (a real `Manager` on
  the `broker_env` backend with service supervision disabled). Real
  queues, real checkpoint writes, real `read_task_state_snapshot`.
- New tests: Task 2. The first protects the new [MA-1.8] read-failure
  sentence; the second protects the bootstrap clause and MANAGER.11's
  publication rule at bootstrap, which had no test at `c857e10b`.
- Existing test that must stay green and is the proof for the unchanged
  write-side rule:
  `test_undurable_internal_launch_blocks_ordinary_reservation_until_checkpoint`.
- Do not mock: the Manager, the queues, the checkpoint publisher, the
  liveness probe, or the family scheduler. The permitted patches are one
  broker-type override per test (`peek_many` scoped to the task's state
  queue; `write` scoped to the Manager's state queue), the same seam the
  undurable test uses.
- Edge case deliberately out: a test that the next periodic cycle "recovers"
  after the injected failure is lifted. There is nothing to recover; the
  next cycle reads as it always does. Writing that test would reintroduce
  the recovery narrative this plan removes.
- Red-green: the first Task 2 test is red at `c857e10b` and green after
  Task 3; the second is green at `c857e10b`, red under the removals alone,
  and green after Task 3 (all three states verified by the author).

## 7. Verification

Per-task:

```bash
./.venv/bin/python -m pytest tests/core/test_manager_family_maintenance.py -q -k "failed_evidence_read or failed_bootstrap_checkpoint or undurable_internal_launch"
```

Final gates (from a tree containing the whole change):

```bash
./.venv/bin/python -m pytest tests/core/test_manager_family_maintenance.py tests/core/test_family_dispatch_scaling.py tests/core/test_family_source_reconciliation.py tests/core/test_manager.py -q
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
bin/check-doc-paths
./.venv/bin/ruff check . && ./.venv/bin/ruff format --check .
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
grep -rn "_admission_evidence_unavailable" weft/ tests/ docs/specifications/ ; test $? -eq 1
./.venv/bin/python -m pytest -q
```

Success: the named test files pass; the grep finds nothing outside this
plan's own text; lint, format, and mypy are clean; the full fast suite
passes. Observable after landing: a Manager whose task-state reads fail
keeps dispatching while its database is readable, and a Manager whose
database is unreadable fails the next read the same way it did before.

## 8. Rollback

`git revert <landing commit>`. No persisted shape changes; no queue
residue.

## 9. Out of Scope

- The `except` tuples at lines 2521 and 2576, including whether
  `ValueError`, `TypeError`, and `KeyError` belong there. Observation for
  the owner: `_adopt_scheduler_evidence` raises `ValueError` on a snapshot
  from another context or a changed lineage, and when that happens inside
  the periodic cycle the whole batch is abandoned for that pass. That is a
  separate question about one bad row aborting a batch; it is not armor
  and this plan leaves it alone.
- `_reconcile_launch_now`'s `DatabaseError` member, which the periodic path
  does not catch. Left as is.
- `_maintain_orphan_reservations` and the orphan page cursor.
- The connection-usage "unknown observation" rule at line 450 and the
  admission lanes.
- Any spec text beyond the one sentence and the backlink.

## 10. Owner Decisions

None open. Two rule-decided choices, recorded so they are not
re-litigated.

1. Round-1 F1 showed that the flag also carried the bootstrap case: a
   successor's failed first checkpoint publication raises inside the
   periodic cycle and, through the flag, keeps restored pending work from
   dispatching. MANAGER.11 decides that this gate stays. The mechanism
   chosen is `not self._admission_bootstrapped` at the two dispatch sites:
   a state the Manager already keeps, already used to decline
   classification (line 6151) and to arm the wake timer (line 8596), and
   already what reruns the bootstrap. The alternative considered was to
   stop raising at line 2606 and instead mark every adopted resident
   undurable until the publication lands, reusing the post-launch gate;
   it was not chosen because it changes the bootstrap's own shape (a
   bootstrap would count as complete with its checkpoint unpublished) for
   no gain over the two-line gate. If the owner prefers that shape, say
   so before Task 3.
2. The plan includes firing tests (Task 2) although the owner has ruled
   that injected-failure tests proving recovery are armor. The first test
   proves the opposite of recovery: that nothing happens. The second
   proves a gate the spec already mandates. Both are the firing tests the
   new [MA-1.8] sentences require under `CLAUDE.md` §7 ("every enumerable
   contract element the change touches has a firing test"), and both
   carry their custody claim in queue assertions. If the owner judges the
   first test to be armor, drop it and rely on the zero-occurrence grep in
   `## 7`; the second stays, since MANAGER.11's bootstrap case had no test
   before.

## Review Log

- 2026-11-04: plan drafted from the owner's audit of the landed series
  (Fable 5.1). Every path, symbol, line number, and the verbatim spec
  sentence were checked by lookup; the Task 2 test was run as a scratch
  file at `c857e10b` and failed at the dispatch assertion in both
  parametrizations with the custody assertions passing.
- Self-driven fresh-eyes review: done 2026-11-04, distinct pass after
  drafting, 5 corrections: (1) the read-first ranges for
  `_queue_has_pending` and `_process_queue_message` started after their
  `def` lines (5466, 5615); (2) the timer-arming coupling described the
  wake timer as if it scheduled the cycle; the cycle is attempted on every
  reactor turn at line 8837 and gated by its own deadline at 2536, so the
  bullet was rewritten to say what the wake timer does and does not do;
  (3) the status flow skipped `promoted`, which would have left the two
  Replacement blocks unchecked between landing and closeout; the landing
  commit now sets `promoted`; (4) a typo in site 4 (`_scheduler_undirable`);
  (5) Task 0's expected grep count was self-contradictory (8 lines, not 7).
- Cross-family round 1 (codex 0.161.0, 2026-11-04, the draft after the
  fresh-eyes pass; brief and transcript kept outside the tree): verdict
  `blocker: F1`. Reviewed baseline `c857e10b`. Dispositions:

  | ID | Severity | Finding | Disposition |
  |----|----------|---------|-------------|
  | F1 | P2 | The premise "checkpoint failures never reach the flag" is false: `_bootstrap_scheduler_evidence` raises `RuntimeError` on a failed first publication (line 2606), the cycle's `except` sets the flag, and restored pending work would dispatch once the flag is gone. | Confirmed by code read and by a scratch run (see Task 2): the new bootstrap test fails with the flag removed alone. Fixed in plan: `## 1`, `## 3`, Q1, hidden couplings, register rows 5a/5b, the spec delta's second sentence, Task 2's second test, Task 3 edits 4-6, Owner Decisions item 1. |
  | F2 | P3 | The first test asserts readiness, not dispatch, and reads `_scheduler_residents` while the plan claimed queue-only custody assertions. | Fixed in plan: the resident assertion is removed; the prose now states the claim as readiness plus retained reservation; the mutation-check bullet is dropped as redundant. `_process_queue_message` is not driven to `True` because that launches a child. |
  | F3 | P3 | `_ack_launch_custody` is not the only deletion site (child reap at 4661; reserved-policy CLEAR at 5866). | Fixed in plan: `## 4` names all three; none is touched. |
  | F4 | P3 | Orphan pages can outlive the bootstrap with no residents; the batch size does not bound revisit latency; the family-repair deadline also wakes the reactor. | Fixed in plan: both hidden-coupling bullets rewritten without the unsupported bound. |
  | F5 | nit | The October admission plan is `draft`, superseded, not `completed`. | Fixed in plan: `## 2`. |

  Round 1 also confirmed: the eight occurrences and six sites are complete;
  no path exists on which the flag prevents a deletion without proof or a
  duplicate launch; the spec sentence is verbatim at line 478 and both
  Replacement blocks are single lines; class 5 with a risky trigger and
  strategy B are justified.
- Cross-family round 2 (codex 0.161.0, 2026-11-04, scoped to the F1
  resolution and the second test, on the committed draft `d4241946`):
  verdict `no blocker`. One new finding, F6 (P3): the backlink bullet and
  the CHANGELOG bullet attributed the publication gate to the undurable
  set alone, which after F1 is only the post-launch half. Fixed in plan:
  both now name the bootstrap wait as the other half. Round 2 confirmed by
  code trace: the `not _admission_bootstrapped` gate is sufficient for
  MANAGER.11 and reruns the bootstrap as today; the six production readers
  of `_admission_bootstrapped` are complete (a test helper at
  `tests/core/test_manager.py:2840` also reads it); the declined
  alternative has a sharper gap than the plan stated (restored pending
  work with no adopted residents leaves nothing to mark undurable, which
  is exactly the second test's shape); the second test's reset reaches the
  real pending-label restore, publisher, and dispatch gate and proves the
  publication gate, not a full process restart; no assertion is a
  tautology; the bootstrap spec sentence is true after Task 3, conflicts
  with nothing in [MA-1.8] or MANAGER.18, and is at spec altitude; rows 5a
  and 5b are correctly N. The reviewer did not rerun the scratch
  experiments. Review before implementation is complete; Task 1 is done.
- Completed-work review (codex 0.161.0, 2026-11-04, the landing commit
  `e3817033` against Tasks 2-4 and the delta): verdict `PASS`. One
  finding, F1 (P3): the Execution log did not record the `## 7` four-file
  Manager gate on the landed tree. Disposition: run and recorded under
  Task 5 (323 passed). The reviewer reconstructed the landed
  `weft/core/manager.py` from its parent using only Task 3's edits and got
  the file exactly; both tests match Task 2 verbatim; both Replacement
  blocks occur once; the CHANGELOG bullet matches Task 4; the reap and
  periodic handlers, the two dispatch sites, and the wake timer read as
  the plan states; the two obvious one-line mutations (a re-added failure
  gate; the bootstrap disjunct dropped from `_process_queue_message`)
  would each fail a new test by trace; nothing in `## 9` was touched.
  Observations: `ruff format --check .` still flags three code fences in
  older plan files (pre-existing); the historical 2026-10-15 plan still
  names the flag (pre-existing); removing only the bootstrap disjunct from
  `_queue_has_pending` is outside the second test's direct assertion.
- Closure correction, 2026-11-04 (series lead): the closeout commit
  `347b1c55` set this plan to `completed` without the two [WT-7] closure
  items that every class-5 completion requires
  (`docs/program-theory.md` `## Tensions and falsifiers [WT-7]`, line
  993): the owner-keyed possession probe and the theory-versus-code audit
  table refresh. That was the author's error; the status returns to
  `promoted` here, as the other series plans stand. Audit table refresh
  (`docs/plans/2026-11-02-program-theory-reconciliation-plan.md`
  `### Audit table`, line 5409, carried forward by reference; touched rows
  re-audited): I-2 → [WT-2a.2] (line 5430): aligned, strengthened; the
  removed flag was process memory steering dispatch, and after `e3817033`
  no in-memory mode records a read failure, with
  `test_failed_evidence_read_leaves_custody_and_dispatch_unchanged` as
  evidence. I-7 → [WT-2a.8] (line 5435): aligned; whether the database is
  readable is reported by SimpleBroker's exceptions and no longer
  remembered by Weft. 4.4-d No over-armoring (line 5465): aligned,
  strengthened; the reap and periodic read handlers now try, catch and log
  only, matching `_reconcile_launch_now`. 4.4-a As few concepts as possible
  (line 5462): aligned; one mode removed, none added (the bootstrap gate
  reuses `_admission_bootstrapped`). Closure to `completed` awaits the
  owner's keyed possession probe: the owner writes the key first; a
  different-family agent that did not author this plan answers from
  `docs/program-theory.md` alone; the key and the result go here.

## Revision Log

| Date | Baseline | Section | Change and reason | Re-review required? |
|------|----------|---------|-------------------|---------------------|
| 2026-11-04 | `c857e10b` (draft, uncommitted) | `## 1`, `## 2`, `## 3`, `## 4`, register, spec delta, Tasks 2-3, `## 6`, `## 7`, `## 10` | Round-1 F1-F5: the bootstrap publication failure is kept gated through `not _admission_bootstrapped`; a second firing test; custody deletion sites and coupling text corrected; the spec delta gains the bootstrap clause. | Yes: round 2, scoped to the F1 resolution and the second test. |
| 2026-11-04 | `d4241946` | spec delta backlink, Task 4 | Round-2 F6: the backlink and CHANGELOG bullets name the bootstrap wait beside the undurable set. Wording only. | No. |
| 2026-11-04 | `347b1c55` | `Status:`, index row, Review Log | Closure correction: `completed` reverted to `promoted` pending the owner's [WT-7] keyed probe; audit table rows I-2, I-7, 4.4-a, 4.4-d refreshed. | No. |

## Execution log

### Implementer, 2026-11-04 (from `3364b7f8`)

Task 0 answers, written before the first edit:

1. A checkpoint write failing with `BrokerError` sets the flag only through
   the bootstrap: `_register_tid_state` (`base.py` 2415-2423) returns
   `False`, `_publish_scheduler_checkpoint` (1543-1553) keeps the dirty
   flag and the undurable membership, and the post-launch caller
   `_checkpoint_spawned_admission` never raises; `_bootstrap_scheduler_evidence`
   alone turns that `False` into `RuntimeError` at line 2606, caught at
   2576.
2. After a failed snapshot read for a resident, nothing about custody has
   changed: the record is still in `_scheduler_residents`, the reserved
   request is still in its reserved queue (only `_ack_launch_custody`, the
   child reap at 4661, and the `CLEAR` policy branch at 5866 delete one,
   none of them reached from a failed read), and the last published
   checkpoint still names the task.
3. `_reconcile_launch_now` (6081-6103) catches the same classes plus
   `DatabaseError`, logs "Launch evidence handoff deferred", and continues
   with no flag, no reorder, and no timer change.

`grep -n "_admission_evidence_unavailable" weft/core/manager.py` at
`3364b7f8`: 8 lines, 670, 2515, 2522, 2577, 2581, 5478, 5635, 8598, as the
plan states.

#### Task 2. RED (tests first, no production edit)

Both tests inserted in `tests/core/test_manager_family_maintenance.py`
after the undurable test, text as in the plan; `ruff format` and
`ruff check` clean on the module. Run at `3364b7f8` plus the test edit
with `-n 0 -k "failed_evidence_read or failed_bootstrap_checkpoint"`:
`test_failed_evidence_read_leaves_custody_and_dispatch_unchanged[reconcile]`
FAILED, `[reap]` FAILED, both at
`assert manager._queue_has_pending(source)`;
`test_failed_bootstrap_checkpoint_keeps_restored_work_undispatched` PASSED.
Two failed, one passed, as Task 2 requires.

#### Task 3. GREEN

The six removals and the two `not self._admission_bootstrapped` disjuncts
applied with anchored replacements; the `_record_reaped_admission`
docstring replaced and its two comment lines deleted; `ruff format`
collapsed the wake-timer condition to one line.
`grep -c "_admission_evidence_unavailable" weft/core/manager.py` prints 0.
`git diff --stat weft/core/manager.py`: 1 file changed, 7 insertions(+),
22 deletions(-) (the plan's 6/19 figure predates the docstring edit, which
adds one line and removes three more). Run with
`-k "failed_evidence_read or failed_bootstrap_checkpoint or undurable_internal_launch"`:
four PASSED.

#### Task 4. Spec, CHANGELOG, backlink, status

Both Replacement blocks applied verbatim to
`docs/specifications/03-Manager_Architecture.md` (the [MA-1.8] sentence at
line 479 after the backlink insertion at line 36); the CHANGELOG bullet
added at the top of `## [Unreleased]` `### Changed`; plan `Status:` and
the index row set to `promoted`. `tests/specs/test_plan_metadata.py` 4
passed; `bin/check-doc-paths` OK, which now checks both Replacement
blocks for containment; `ruff check .` clean; `ruff format --check .`
reports three files, all code fences inside older plan documents
(`docs/plans/2026-11-02-reactor-edge-timer-floors-plan.md` among them),
none touched by this change; the two edited Python files are formatted.
`grep -rln "_admission_evidence_unavailable" weft/ tests/ docs/specifications/ CHANGELOG.md`
finds nothing.

#### Task 5. Final gates (tree `3364b7f8` plus the whole change, before the landing commit)

- `mypy` over the CI set: "Success: no issues found in 486 source files".
- Full fast suite, `pytest -q -rA`: 6469 passed, 0 failed, 25 skipped,
  exit 0 (two runs; the first captured no failures, the second added the
  counts).
- The per-task and doc gates are recorded under Tasks 2-4 above.

#### Landing commit

`e3817033` "Remove the Manager's admission-evidence mode": 6 files, 200
insertions, 26 deletions; `weft/core/manager.py` 7 insertions, 22
deletions.

#### Task 5. Completed-work review and the four-file gate (on `e3817033`)

- The `## 7` four-file Manager gate, run on the landed tree after the
  review asked for it: `tests/core/test_manager_family_maintenance.py`,
  `tests/core/test_family_dispatch_scaling.py`,
  `tests/core/test_family_source_reconciliation.py`,
  `tests/core/test_manager.py`: 323 passed, 0 failed, 2 skipped, exit 0.
- The `## 7` residue grep as written matches stale `.pyc` files in the
  working tree; the committed-source form
  `git grep -l "_admission_evidence_unavailable" -- weft tests docs/specifications CHANGELOG.md`
  finds nothing. The name survives in one historical plan,
  `docs/plans/2026-10-15-work-conserving-family-scheduling.md` lines 185
  and 605, which describe the code of their day and are not edited.

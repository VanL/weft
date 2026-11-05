# Manager Wake, Idle Clock, and Launch Settlement Repairs

Status: completed
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1], [MA-1.5], [MA-1.6a], [MA-1.8]; docs/specifications/04-SimpleBroker_Integration.md (Project Context and Directory Scoping); docs/specifications/07-System_Invariants.md MANAGER.11, [OBS.4]; docs/specifications/05-Message_Flow_and_State.md [MF-5], [MF-6]; docs/specifications/02-TaskSpec.md (`state.return_code`)
Superseded by: none

Class: 5 (spec-changing) with risky triggers. Normative spec text changes
in six places across two spec files, so the class is 5. Slices 1, 2, 3 and
5 edit the Manager's wait, dispatch-eligibility, launch-settlement, and
bootstrap paths, which sit on the durable spine
(`Manager -> queues/state log`), so the hardening-plans checklist applies
in full and independent review happens before implementation. Not +P: no
runbook, skill, or gate changes. Plan type: implementation with spec
revision. Promotion strategy B (atomic with each slice's code) for slices 1
to 6, D (clarification) for the documentation slice 7; see
`## Proposed Spec Delta`. One-way doors: none.

**Ordering.** The owner ruled on 2026-11-05 that the program-theory
revision in
`docs/plans/2026-11-05-program-theory-behaviour-and-refusal-revision-plan.md`
lands first ("those should be made first"). That plan is now `promoted`:
its text is in `docs/program-theory.md` (worktree, not yet committed). This
plan's acceptance tests cite three theory statements:
[WT-2a.15] "A standing refusal is a fact, and it is scoped"; the [WT-4.1]
row "A wake predicate reads facts and guarantees progress" (Slice 2); and
the [WT-4.3] row "A quiet task costs nothing an operator can see" (Slice 1).
That plan names this
file as the closing plan for two drifted audit rows, the [WT-4.1] and
[WT-4.3] rows, so do not rename or split it without telling the owner.

**This plan does not close [WT-2a.15].** Scoped dispatch past an in-doubt
launch (D1) and the published refusal (D5) stay owner decisions, so after
every slice here lands the [WT-2a.15] audit row is still drifted: one
in-doubt launch still blocks all ordinary dispatch, silently. A separate
plan closes that row once D1 and D5 are ruled. The six repairs below are
independent of it and land without claiming that closure.

This plan comes from the 2026-11-05 deep-dive review, which reproduced its
defects on the default SQLite path at `44d7f900`. It repairs the six that
have a conforming fix or an owner ruling, and documents one boundary.
The remaining defects need further owner rulings and are listed in
`## 10. Owner Decisions`; this plan does not implement them.

## 1. Goal

Make the Manager honour four statements the specs already make, plus two
the owner ruled on 2026-11-05:

1. It does not spin while a user task runs ([MA-1], "These clocks must not
   be replaced by an unconditional zero timeout").
2. A newly accepted request wakes it ([MA-1], "Queue activity still wakes
   the manager promptly through the shared watcher").
3. A reservation is acknowledged only after task-owned terminal proof or a
   committed Manager-source `wrapper_lost` envelope ([MA-1] item 7), on the
   successor's settlement path as well as the live Manager's.
4. A failed one-shot task records the child's return code
   (`02-TaskSpec.md`, `state.return_code`).
5. Manager bootstrap skips a retained row that names another runtime
   identity, warns once, and continues; it no longer aborts. Owner,
   2026-11-05: "I think the more reasonable thing to do would be to ignore
   non-matching rows, not abort", and for other identity-check failures
   "With a reasonable response and documentation of the problem" and "Yes,
   a runtime warning is reasonable."
6. The three internal services carry process titles like every other task
   ([OBS.4]). Owner, 2026-11-05: "I think that setting
   enable_process_title=True (or even just not setting it to False) is a
   reasonable fix here".

And document one boundary (slice 7): a moved Weft directory, the same
`.weft` reached at a different resolved path, and a differently spelled
PostgreSQL DSN are different contexts by definition, and mixing contexts in
one database is unsupported. Owner, 2026-11-05: "we just document that
those will be considered different contexts" and "With a doc warning that
mixing two contexts in one db may result in inconsistent behavior across
tasks". There is no change to context canonicalization.

All owner rulings in this plan were relayed to this session by a side-fork
session of the same conversation; the quotes are as relayed.

## 2. Source Documents

Governing specs (baseline in `## Spec Baseline`):

- `docs/specifications/03-Manager_Architecture.md` [MA-1] items 5 and 7,
  [MA-1.5], [MA-1.6a], [MA-1.8]
- `docs/specifications/07-System_Invariants.md` MANAGER.11, [OBS.6]
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5] (terminal
  envelope precedence), [MF-6]
- `docs/specifications/01-Core_Components.md` [CC-2.1], [CC-2.2.1] (the
  hint-gated wait this plan must not disturb)
- `docs/specifications/02-TaskSpec.md` (`state.return_code`)

Theory: `docs/program-theory.md` [WT-2] item 4, [WT-2a.6], [WT-2a.7],
[WT-2a.13], the [WT-4.3] rows "A quiet task does nothing" and "One
reactor, one arbiter", [ALT-WT-002], [ALT-WT-010], and the [WT-7]
falsifier "Event-driven against absence".

Earlier plans, as context only (none is a veto; each records a decision
made under its own constraints):

- `docs/plans/2026-10-15-work-conserving-family-scheduling.md` (completed)
  introduced the family scheduler and the inbox `_queue_has_pending`
  override that Slice 2 edits. Its review log (F1) fixed what that
  predicate returns for an indexed request; it did not consider an
  unindexed arrival.
- `docs/plans/2026-11-03-watcher-hint-gated-observation-plan.md`
  (completed) made the wait read observation evidence only on returns that
  can carry it. Slice 2 changes a predicate that wait consults; it does not
  change the wait.
- `docs/plans/2026-11-04-admission-evidence-read-failure-plan.md`
  (completed): "a failed read changes nothing". Slice 2 follows it.
- `docs/plans/2026-11-02-reactor-edge-timer-floors-plan.md` (completed)
  audited timer floors. It did not examine the Manager's idle deadlines
  with a live child; Slice 1 covers that case.

Local guidance: `AGENTS.md` §1.1 and §4, `docs/agent-context/engineering-principles.md`
§4, §4.1 and §11, `docs/agent-context/runbooks/testing-patterns.md`
(Test Design Rules; Manual Reactor Test Drivers; Pattern 12).

## 3. Context and Key Files

### Evidence that each defect is real (measured 2026-11-05 at `44d7f900`, SQLite, macOS)

| # | Observation | Reproduction |
|---|-------------|--------------|
| 1 | With one `sleep 25` task running the Manager used 9.95 s of CPU in 10 s; idle it used 0.07 s in 8 s. With `WEFT_MANAGER_LIFETIME_TIMEOUT=0` it used 0.06 s in 8 s with a task running. Also present at `v0.9.105`. | `ps -o time= -p <manager pid>` twice, 10 s apart |
| 2 | A lone submission to an idle Manager was launched 17 to 949 ms after acceptance (14 samples, mean about 470 ms). SimpleBroker reported the data-version change 4 to 19 ms after the write; `_activity_hint_action` then returned `continue`. With the inbox predicate patched out-of-tree to also consult the queue the delay was 10 to 29 ms. Also present at `v0.9.119`. | Appendix A probe |
| 3 | Manager and fresh wrapper both killed about 30 ms after `Process.start`: the successor deleted the reservation; `weft task status` reports `unknown (observation_metadata_unavailable)`; no terminal record exists. | crash hook around `multiprocessing.process.BaseProcess.start` |
| 4 | A command exiting 3 leaves `"return_code": null` in `weft task status --json`. | `weft run -- python fail.py` |
| 5 | After `mv` (or a copy) of a used project, `weft run` waits past 40 s with the request left in `weft.spawn.requests`; the Manager shows `active`. | `init`, `run`, `system stop`, `mv`, `run` |
| 6 | Heartbeat, TaskMonitor and LivenessMonitor show in `ps` as bare `python -B -c from multiprocessing.spawn ...`. | `ps` after any `weft run` |

### Current structure

**Slice 1.** `Manager.next_wait_timeout`
(`weft/core/manager.py`, the method beginning near line 8730) returns the
minimum of about ten deadlines. Two belong to idle shutdown:

- the broker-probe deadline, `_last_broker_probe_ns + _broker_probe_interval_ns`,
  published when `_idle_timeout > 0 and _last_broker_probe_ns > 0`;
- the idle-expiry deadline, `_last_activity_ns + idle_timeout`, published
  when `_idle_timeout > 0`.

Both are serviced only by the tail of
`Manager._process_manager_reactor_turn` (near line 9021), which returns
before the idle evaluation when `self._user_work_children()` is non-empty
("Idle timeout applies only when the manager is actually idle"). While a
user child lives, nothing advances `_last_broker_probe_ns`, so after one
second the first deadline is permanently in the past, `next_wait_timeout`
returns `0.0`, and `MultiQueueWatcher._wait_for_activity_body` returns at
once. After `idle_timeout` seconds the second deadline does the same.
`Manager._cleanup_children` already sets `_last_activity_ns` when a child
exits ("The manager should only begin its idle countdown after in-flight
work has actually finished"), so the idle countdown restarts correctly
once the clock runs again.

**Slice 2.** A request is accepted by a write to `weft.spawn.requests`
(the Manager's `inbox`). The Manager learns of it in two steps:

1. `MultiQueueWatcher._activity_hint_action`
   (`weft/core/tasks/multiqueue_watcher.py`) receives a data-version or
   native hint and asks `self._has_pending_messages()` whether to hand the
   owner a turn.
2. In the turn, `Manager._classify_family_requests` reads inbox rows above
   the watermark `self._family_classify_after`, indexes them into the
   in-memory family scheduler, and advances the watermark.

`Manager._queue_has_pending` (`weft/core/manager.py`, near line 5573)
answers step 1 for the inbox from memory alone:
`_family_classification_pending or bool(_family_invalid) or _peek_family_candidate() is not None`.
`_family_classification_pending` is true only after a full classification
page. A new row is in none of those until step 2 runs, and step 2 runs
only in a turn. The predicate that grants the turn depends on state only
the turn can produce. The request is found on the next turn granted for
another reason, typically one of the roughly one-second housekeeping
deadlines.

**Slice 3.** Two paths acknowledge a launch whose wrapper is dead:

- Live Manager: `Manager._cleanup_children` calls
  `_write_manager_terminal_envelope(tid, child)` and acknowledges only if
  it returns true. That function returns true when task-owned terminal
  proof is already on `ctrl_out`, and otherwise writes
  `{"type": "terminal", "source": "manager", "status": "failed", "error": WRAPPER_LOST_ERROR}`.
- Successor Manager: `Manager._retire_scheduler_residents` (near line
  2918), reached from `_refresh_scheduler_evidence` and
  `_record_reaped_admission`, calls `_ack_launch_custody` as soon as the
  resident's scoped host processes are proven stale. It writes nothing.

When the dead Manager's checkpoint still holds the reservation reference
(`evidence["reserved_queue"]` is set), no handoff happened, so no
task-owned snapshot matched and no other component can produce a terminal
fact. Acknowledging then removes the last evidence the task existed.

**Slice 4.** `Consumer` handles `outcome.status == "error"`
(`weft/core/tasks/consumer.py`, near line 1097) with
`self.taskspec.mark_failed(error=str(error_exc))`. `RunnerOutcome.returncode`
(`weft/ext.py`) is available and unused. The interactive path already
passes it (`weft/core/tasks/interactive.py`, `mark_failed(error=message, return_code=returncode)`).

### Files to modify

| Slice | Files |
|-------|-------|
| 1 | `weft/core/manager.py`; new `tests/system/test_manager_reactor_floor.py`; `docs/specifications/03-Manager_Architecture.md` |
| 2 | `weft/core/manager.py`; `tests/core/test_manager_family_maintenance.py`; `docs/specifications/03-Manager_Architecture.md` |
| 3 | `weft/core/manager.py`; `tests/core/test_admission_recovery.py`; `docs/specifications/03-Manager_Architecture.md` |
| 4 | `weft/core/tasks/consumer.py`; `tests/cli/test_cli_run.py` (beside `test_cli_run_target_failure_compatibility`) |
| 5 | `weft/core/runtime_identity.py`; `weft/core/manager.py`; new `tests/system/test_relocated_context.py`; `tests/core/test_admission_recovery.py`; `docs/specifications/03-Manager_Architecture.md` |
| 6 | `weft/core/manager.py`; `tests/core/test_manager.py`; new `tests/cli/test_internal_service_titles.py`; `docs/specifications/03-Manager_Architecture.md` |
| 7 | `docs/specifications/04-SimpleBroker_Integration.md`; `README.md` |
| all | `CHANGELOG.md`; `docs/plans/README.md` (status flips) |

### Read first

- `weft/core/manager.py`: `next_wait_timeout`, `_process_manager_reactor_turn`,
  `_user_work_children`, `_read_broker_timestamp`, `_queue_has_pending`,
  `_has_pending_messages`, `_classify_family_requests`,
  `_retire_scheduler_residents`, `_ack_launch_custody`,
  `_write_manager_terminal_envelope`, `_child_terminal_proof_visible`,
  `_build_child_spec`
- `weft/core/tasks/multiqueue_watcher.py`: `_wait_for_activity_body`,
  `_activity_hint_action`, `_readiness_action`, `_update_active_queues`
- `tests/core/test_admission_recovery.py`: the `recovery_manager` fixture,
  `_reserve_and_checkpoint`, and
  `test_positive_scoped_retirement_acknowledges_exact_request_then_releases_checkpoint`
- `tests/core/test_manager_family_maintenance.py`: the `manager` fixture
  and `request_body`
- `tests/helpers/reactor_driver.py`

### Comprehension questions (answer in the execution log before the first edit)

1. *Which call decides that a data-version hint becomes a Manager turn,
   and what does it ask about the inbox today?* Expected:
   `MultiQueueWatcher._activity_hint_action` calls
   `Manager._has_pending_messages`, which calls `Manager._queue_has_pending`;
   for the inbox that returns only in-memory scheduler state.
2. *Why would making the inbox predicate return raw `queue.has_pending()`
   be wrong?* Expected: an indexed request held back by admission, startup
   feedback, or custody stays in the queue. Raw pending would be true
   while the turn can make no progress, which on PostgreSQL (predicate
   evaluated on every native-waiter return) is a hot loop. The override
   exists so retained custody is not actionable work.
3. *Which component may write a `wrapper_lost` envelope, where, and when
   must it not?* Expected: only a Manager, only to the child's `ctrl_out`,
   never the outbox, and not when a task-owned terminal envelope is
   already visible ([MF-5]).
4. *In `_retire_scheduler_residents`, which residents are skipped, and
   does this plan change that?* Expected: residents with no
   `host_processes` and residents whose processes are not proven stale.
   The plan does not change the skip; the in-doubt launch stays held
   (Owner Decision D1).
5. *Which bootstrap failures does Task 5 skip, and which still stop
   bootstrap?* Expected: only `RuntimeIdentityMismatch` (another context,
   or a connection owner that does not match its identity) is skipped.
   Malformed checkpoints, a failed own checkpoint publication, a "task
   scheduling identity changed" error, and read failures keep their
   current rules.

## 4. Invariants and Constraints

Must not change:

- **Exactly once per reservation ([WT-2a.7], MANAGER.11).** No slice
  replays, requeues, or releases a reservation it did not release before.
  Slice 3 adds a write before an acknowledgement that already happens.
- **Absence is not proof ([WT-2a.13]).** Slice 3 acts only on residents
  that already pass the existing positive test (scoped host processes
  present and proven stale). A resident with no process evidence is
  untouched.
- **Task-owned proof outranks the supervisor ([MF-5]).** Slice 3 reuses
  the existing writer, which declines when task-owned proof is visible.
- **One arbiter, hint-gated wait ([WT-2a.6], [CC-2.1]).** No change to
  `MultiQueueWatcher`. No new timer, wait cap, poll, or deadline anywhere
  ([ALT-WT-002]). Slice 1 removes two deadlines from publication in one
  state; Slice 2 adds one read to an existing predicate.
- **No new mechanism ([ALT-WT-010]).** No index, cache, counter, or
  subsystem. Slice 2 uses the watermark the Manager already keeps.
- **A failed read changes nothing.** Slice 2's added read uses the same
  failure behaviour as the reads beside it (see Task 2).
- **Idle semantics.** The Manager still exits after `idle_timeout` of
  inactivity; the countdown still starts at the last activity or the last
  child exit; the final decision still forces a broker refresh ([MA-1]
  item 5).
- **Queue names, payload shapes, CLI shapes.** Unchanged. Slice 3 writes
  one more row of an existing type to an existing queue. Slice 4 fills an
  existing nullable field.

Hidden couplings:

- **Deadline published ⇔ deadline serviced.** Every deadline
  `next_wait_timeout` publishes must be re-armed or retired by the turn it
  wakes. Slice 1 makes the two idle deadlines and the idle evaluation
  share one predicate so they cannot drift again.
- **Wake predicate true ⇒ the granted turn changes the predicate's
  inputs.** If a predicate can stay true while the turn makes no progress,
  the reactor spins (PostgreSQL) or wakes needlessly (SQLite). Slice 2's
  predicate is true only when `_classify_family_requests` will run and
  read at least one row, which advances the watermark past it.
- **`_has_pending_messages` is also a scheduler precheck.** When it
  returns true it marks the next drain to probe all queues. Slice 2 relies
  on that existing behaviour and does not duplicate it.

Behaviour differences (A added, C changed, N none, D diminished):

| Slice | Difference | Class |
|-------|------------|-------|
| 1 | Manager CPU while a user task runs drops from one core to idle level | C |
| 1 | Idle-exit timing | N |
| 2 | Idle-to-launch latency drops from up to about 1 s to one strategy pass | C |
| 2 | One more indexed inbox read each time the wake predicate runs while the scheduler holds no candidate: per foreign commit on SQLite; on PostgreSQL also per native idle poll, about one a second when idle (from the code; measured in Task 2) | C (cost) |
| 3 | `T{tid}.ctrl_out` gains a Manager-source `wrapper_lost` envelope when a successor settles a dead, never-handed-off launch; status becomes `failed (wrapper_lost)` instead of `unknown` | A |
| 4 | `state.return_code` is populated for a failed one-shot command | A |
| 5 | A retained row of another runtime identity no longer stops dispatch; it is ignored with one warning | C |
| 6 | Three more titled processes per context; `ps` and `pgrep` find the internal services | A |

No row is class D.

Review gates: no new execution path; no drive-by refactor; no new
dependency; no public CLI shape change; real broker and real processes in
every proof; fresh-eyes review recorded; cross-family review required
before implementation (Class 5 with risky triggers).

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|
| [MA-1.6a] implementation notes | Resolve the retained request route | A valid parsed spec with no explicit ctrl_out uses launch's canonical default; body read sets with_timestamps=False | Both details were required by actual launch/peek API semantics; malformed requests still hold custody | No normative delta; Task 3 clarified |

## Spec Baseline

- `44d7f900` — `docs/specifications/03-Manager_Architecture.md`,
  `docs/specifications/05-Message_Flow_and_State.md`,
  `docs/specifications/07-System_Invariants.md`,
  `docs/specifications/02-TaskSpec.md` at plan authoring time.
- Plan type: implementation with spec revision.
- Promotion baseline identifier: `2d8c33a3` plus the implementation worktree
  on 2026-11-05; all Replacement blocks applied together before promotion.

## Proposed Spec Delta

Promotion strategy:

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| `docs/specifications/03-Manager_Architecture.md` | B — atomic with each slice's code | [MA-1] item 7 (three spans: slices 1, 3, 6), [MA-1.8] (slice 2 paragraph, slice 5 sentence), `## Related Plans` |
| `docs/specifications/04-SimpleBroker_Integration.md` | D — clarification | Project Context and Directory Scoping, "Current boundary notes" (slice 7) |

No other spec file changes. `state.return_code` in `02-TaskSpec.md`
already says "Null means no return code yet"; Slice 4 makes the code
match it.

### Slice 1 — [MA-1] item 7: replace the last sentence of the paragraph that opens "Housekeeping keeps its own due timers" and closes "immediate wait timeout."

Current text (verbatim, three lines):

> leadership or service audit intervals. When foreground service mode
> disables idle shutdown, stale broker-probe timestamps must not schedule an
> immediate wait timeout.

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```text
   leadership or service audit intervals. The idle clock runs only while
   idle shutdown is enabled and no user-work child is live. While it is not
   running, neither the broker-probe deadline nor the idle-expiry deadline
   is published, so a stale timestamp cannot schedule an immediate wait
   timeout.
```

Carried forward: "Housekeeping keeps its own due timers; unrelated wakes do
not accelerate leadership or service audit intervals" is untouched. The
replaced sentence's one requirement (disabled idle shutdown must not yield
a zero timeout) is kept as the first of the two conditions. Added: the
live-child condition and the idle-expiry deadline.

### Slice 2 — [MA-1.8]: insert one paragraph immediately after the paragraph that opens "Classification advances in bounded pages even while admission is denied." and closes "fixed FIFO visibility window is introduced."

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```text
A source row above the classification watermark is pending reactor work whenever classification can advance. A newly accepted request therefore wakes the Manager through the shared watcher; it does not wait for a housekeeping deadline. The check reads the source queue at the watermark and adds no timer.
```

Nothing is removed.

### Slice 3 — [MA-1] item 7: insert one sentence after "Manager-source `wrapper_lost` envelope." in the paragraph that opens "After child exit, the Manager retires child bookkeeping"

Current text (verbatim, two lines):

> terminal proof or committing a
> Manager-source `wrapper_lost` envelope. A failed terminal-proof read is unknown, not

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```text
   terminal proof or committing a
   Manager-source `wrapper_lost` envelope. A successor that settles an adopted
   launch whose reservation was never handed off is bound by the same rule:
   it commits the envelope, or finds task-owned proof, before it acknowledges.
   A failed terminal-proof read is unknown, not
```

Carried forward: both original lines, word for word. Added: one sentence.

### Slice 5 — [MA-1.8]: insert one sentence after "A successor dispatches no ordinary work until its bootstrap completes: its inherited checkpoints read and its own first checkpoint published."

The current sentence sits inside one long physical line (the paragraph
that opens "Because initial task-state publication is best-effort").
Replacement (`docs/specifications/03-Manager_Architecture.md`):

```text
A successor dispatches no ordinary work until its bootstrap completes: its inherited checkpoints read and its own first checkpoint published. A retained row whose recorded runtime identity does not match the reading Manager (it names another context, or its connection owner does not match its runtime identity) is not that Manager's evidence: bootstrap skips it, emits one runtime warning naming the skipped rows, and continues.
```

Carried forward: the first sentence, word for word. Added: one sentence.
Not changed: the rules for malformed checkpoints, failed checkpoint
publication, and failed reads.

### Slice 6 — [MA-1] item 7: replace the last sentence of the liveness-monitor paragraph

Current text (verbatim, two lines):

> claims no endpoint. Its internal payload matches the other internal
> services and sets `enable_process_title=False`. The

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```text
   claims no endpoint. Its internal payload matches the other internal
   services and leaves `enable_process_title` unset, so every internal
   service carries a process title like any task ([OBS.4]). The
```

### Slice 7 — `docs/specifications/04-SimpleBroker_Integration.md` Project Context and Directory Scoping: append two bullets to "Current boundary notes", after the bullet that opens "TaskSpec `metadata` is caller-owned runtime metadata"

Strategy D (clarification): the first bullet states behaviour the code
already has; the second states an unsupported case. Replacement
(`docs/specifications/04-SimpleBroker_Integration.md`):

```text
- a runtime context is the resolved broker identity: for SQLite the
  resolved database path, for PostgreSQL the sanitized target and backend
  options as given. A moved Weft directory, the same `.weft` reached at a
  different resolved path (for example a container volume at a new mount
  point), and the same PostgreSQL database reached through a differently
  spelled DSN or different backend options are different contexts by
  definition, wherever the resolved path or the sanitized target and
  options differ (a changed password alone does not change the context).
  Runtime state retained under one context is not adopted by
  another; a Manager skips it at bootstrap with a warning ([MA-1.8])
- using two contexts against one database is unsupported and may result in
  inconsistent behavior across tasks; for example, a Manager may return
  another context's unlaunched reserved requests to the shared request
  queue
```

The PostgreSQL clause is read from
`weft/core/runtime_identity.py::canonical_runtime_context`, not tested.

### `## Related Plans` — insert one bullet directly below the theory-revision plan's bullet (first in the list), not replacing it

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```text
- [Manager Wake, Idle Clock, and Launch Settlement Repairs](../plans/2026-11-05-manager-wake-idle-clock-and-settlement-repairs-plan.md) publishes the idle deadlines only while the idle clock runs, counts a source row above the classification watermark as pending reactor work, binds a successor's launch settlement to the `wrapper_lost` rule, skips retained rows of another runtime identity at bootstrap, and titles internal services, under [MA-1] and [MA-1.8].
```

### Spans audited and unchanged

- `docs/specifications/07-System_Invariants.md` MANAGER.11: "Missing
  evidence never authorizes replay" stays true; no slice replays.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5], "manager-authored
  terminal envelopes are supervisor observations for child wrapper death
  only": Slice 3's case is wrapper death proven by scoped process
  evidence.
- `docs/specifications/01-Core_Components.md` [CC-2.1]: the wait and its
  evidence rule are not edited.
- The `_Implementation mapping_` entries for [MA-1.5] and [MA-1.8] name
  functions; Tasks 1 to 3 update them where a function is added (see each
  task).

## 5. Tasks

Each slice is one commit and lands with its spec span and its
`CHANGELOG.md` line. Slices are independent; land them in order 1, 2, 3, 4
so the cheapest verification comes first.

Status sequence. `bin/check-doc-paths` checks every Replacement block of a
`promoted` plan against its target, so promoting before the last block lands
fails the gate (a simulated first-slice promotion produced five
Replacement-sync failures). The plan therefore stays `draft` while slices
land. The commit that applies the last Replacement block (Slice 7, or
whichever slice lands last) sets this plan and its index row to
`promoted`, and the closeout commit sets both to `completed`. Before each
slice commit, check that slice's own block by hand: the block text, from
the line after the opening fence to the line before the closing fence, must
be a substring of its target file.

### Task 0 — baseline and answers

- Record `git rev-parse HEAD` and the answers to the four comprehension
  questions in `## Execution log`.
- Run the Appendix A probe once and record the numbers.
- Stop if any symbol named in "Read first" no longer exists; report the
  falsified premise instead of adapting.

### Task 1 — publish the idle deadlines only while the idle clock runs

- Outcome: with a user task running, `next_wait_timeout` never returns
  `0.0` on account of an idle deadline.
- Files: `weft/core/manager.py`, new
  `tests/system/test_manager_reactor_floor.py`,
  `docs/specifications/03-Manager_Architecture.md`.
- RED first. Add
  `test_manager_does_not_spin_while_user_task_runs` using the
  `weft_harness` fixture and `run_cli`. The new module needs a backend
  marker or collection fails ("Unaudited test module must be marked
  'shared' or 'sqlite_only'"): use `pytestmark = [pytest.mark.shared]`.
  1. Submit a long task with `--no-wait`:
     `python -c "import time; time.sleep(12)"` (portable; do not use
     `sleep`). Call `run_cli` the way the neighbouring CLI tests do, so the
     CLI does not wait on an open stdin.
  2. Wait, with the harness's existing bounded wait, until
     `weft task status <tid>` reports `running`.
  3. Find the Manager PID from `weft status --json`: the `managers` list,
     first entry, `runtime.observations.host_pids[0]` (the `managers` key
     was checked on 2026-11-05; confirm the nested path by running the
     command once against a live Manager). Assert the process title
     contains `:manager:`.
  4. Read `psutil.Process(pid).cpu_times()` (user + system), wait 3.0 s,
     read again. Assert the difference is below 1.5 s.
  5. Stop the task through the CLI.

  Run it alone and record the RED output (expected about 3.0 s consumed).
  The 3 s wait is the measurement window, not a correctness sleep. The
  assertion cannot fail falsely after the fix (an idle Manager uses about
  1% of a core); under CPU starvation it could pass falsely before the
  fix, which is why RED is demonstrated in isolation.
- Change:
  - Add `Manager._idle_clock_running(self) -> bool` returning
    `self._idle_timeout > 0 and not self._user_work_children()`, with a
    docstring citing [MA-1] item 5 and stating the coupling: "the two idle
    deadlines are published exactly when the idle evaluation will service
    them".
  - In `next_wait_timeout`, compute it once and guard both idle deadlines
    with it: the broker-probe block becomes
    `if idle_clock_running and self._last_broker_probe_ns > 0:` and the
    idle-expiry block becomes `if idle_clock_running:`.
  - In `_process_manager_reactor_turn`, replace the two early returns
    (`if self._user_work_children(): return` and
    `if self._idle_timeout <= 0: return`) with
    `if not self._idle_clock_running(): return`, keeping the existing
    comment about active children.
- Not allowed: touching `_last_activity_ns` semantics; removing the
  one-second broker probe; adding a deadline; editing the white-box
  wait-timeout tests in `tests/core/test_manager.py` (they set private
  fields and clear the child table, which is how this defect stayed
  hidden; they remain valid for the cases they cover).
- Spec: apply the Slice 1 Replacement block. Add `_idle_clock_running` to
  the [MA-1.5] `_Implementation mapping_` entry.
- Expected and correct: the first turn after the last user child exits
  sees one stale broker-probe deadline (it aged while the clock was
  stopped), wakes once, and re-arms it. That is one wake, not a spin.
- Stop if: the fix seems to need a new timer, or the test only passes with
  the idle timeout disabled.
- Done when: the new test passes; `./.venv/bin/python -m pytest tests/core/test_manager.py -q -k "wait_timeout or idle"`
  stays green.

### Task 2 — an unclassified source row is pending reactor work

- Outcome: a write to `weft.spawn.requests` is confirmed by the wake
  predicate without waiting for an unrelated turn.
- Files: `weft/core/manager.py`,
  `tests/core/test_manager_family_maintenance.py`,
  `docs/specifications/03-Manager_Architecture.md`.
- RED first. Add
  `test_new_source_row_wakes_idle_manager_wait` using the module's
  existing `manager` fixture (a real in-process Manager on a real broker):
  1. Drive the Manager with `tests.helpers.reactor_driver.drive_until`
     until `manager._admission_bootstrapped` is true (the neighbouring
     tests read the same attribute).
  2. Positive control. Repeat `manager.process_once()` followed by a timed
     `manager.wait_for_activity(timeout=3.0)` until one wait lasts at least
     2.9 s, bounded by 20 s overall; fail if none does. This proves that,
     with nothing written, a wait runs to its own deadline. It is needed
     because `BaseTask.wait_for_activity` also ends at the one-shot
     process-title deadline (1 to 3 s after start on macOS): without the
     control, a scratch run of this test returned after 0.6 to 2.0 s
     before the fix and would have passed falsely.
  3. Write one valid request (the module's `request_body()`) to
     `weft.spawn.requests` through the `broker_env` queue factory, not
     through any handle the Manager owns. SQLite's data-version hint fires
     only for another connection's commit.
  4. Call `manager.wait_for_activity(timeout=3.0)` directly and measure it
     with `time.monotonic()`. Do not use the reactor driver for steps 2
     and 4 (testing-patterns: the driver is not for tests of wait
     behaviour).
  5. Assert the wait returned in under 1.5 s.
  6. Call `manager.process_once()` and assert by queue read that the row
     has left the source queue
     (`peek_one(exact_timestamp=request_id) is None` on
     `weft.spawn.requests`). Do not assert on the
     `weft.state.labels.` pending queue: the same turn dispatches the
     request and consumes its token.

  Validated on a scratch copy of `44d7f900`: before the change the
  measured wait lasted 3.006, 3.038 and 3.072 s in three runs (RED); with
  the change below it lasted 0.000 s in two runs (GREEN).

  Add a second test, `test_new_source_row_wakes_admission_blocked_manager`,
  not pre-validated. The module's `manager` fixture sets
  `WEFT_ADMISSION_MAX_CONNECTIONS=0`, which disables admission on SQLite
  (`Manager._admission_enabled`), so a block set by hand would not survive
  the next turn (round-3 F1). Build the Manager the way the fixture does but
  with `WEFT_ADMISSION_MAX_CONNECTIONS` set to a small positive ceiling, and
  hold measured usage above it the way
  `test_manager_admission_retains_public_while_internal_can_launch` in
  `tests/core/test_manager.py` does (ceiling 5, reserve fraction 0.0)
  (`monkeypatch.setattr(manager, "_observe_admission_usage", lambda: <ceiling>)`;
  that replaces only the usage reading, and reservation, classification
  and the wait stay real). A block exists only after an admission
  evaluation, and a turn with no candidate never reaches one, so seed it:
  write one valid request (request A), drive `process_once()` until A is
  indexed, and confirm that A is still in `weft.spawn.requests` and the
  public lane is in `_admission_blocked_lanes`. Use the same positive
  control. Then write a second valid request (request B) from a foreign
  connection, and assert, for B: the wait returns in under 1.5 s; after one
  `process_once()` the request is indexed (its pending token exists in
  `weft.state.labels.` and the row is still in `weft.spawn.requests`,
  because admission is blocked); and a following
  `wait_for_activity(timeout=3.0)` lasts at least 2.9 s (quiet after
  classification: the indexed request on a blocked lane does not keep
  waking the Manager). Record the RED output. The test takes
  about 6 to 9 s on macOS because of the control. Record the RED output
  again when implementing.
- Change:
  - Add `Manager._family_classification_can_advance(self) -> bool`
    returning
    `self._admission_bootstrapped and not self._draining and time.monotonic() >= self._family_index_retry_after`.
    Use it as the guard at the top of `_classify_family_requests`
    (replacing the inline condition; `_classify_family_requests` is never
    reached while draining, so the added term changes nothing there).
  - Add `Manager._unclassified_source_row_pending(self) -> bool`. It
    returns `False` unless `_family_classification_can_advance()`.
    Otherwise it reads at most one inbox row above
    `self._family_classify_after` and returns whether one exists:
    `bool(self._queue(self._queue_names["inbox"]).peek_many(limit=1, with_timestamps=True, after_timestamp=self._family_classify_after))`.
    This matches the classification page read in
    `_classify_family_requests` (same queue, same watermark, same default
    visibility), so the predicate is true exactly when that read would
    return a row. Parity was checked in SimpleBroker 8.7.2: `Queue.peek_many`
    and `peek_generator` both default to `include_claimed=False` and apply
    `after_timestamp` the same way (`simplebroker/sbqueue.py`,
    `simplebroker/db.py`). Add a one-line comment at the call citing that
    parity. It uses the Manager's existing connected queue and opens
    nothing.
  - In `Manager._queue_has_pending`, inbox branch, append the new check as
    the last term of both returns, so it runs only when memory says
    nothing:
    - gated branch: `return self._family_classification_pending or self._unclassified_source_row_pending()`
    - open branch: `... or self._peek_family_candidate() is not None or self._unclassified_source_row_pending()`
  - **Admission-blocked lane** (outside review, point 4). Classification
    advances even while admission is denied ([MA-1.8]), but two places
    drop the public inbox before the new check is reached:
    `Manager._has_pending_messages` skips a lane in
    `_admission_blocked_lanes` with `continue`, and
    `Manager._queue_counts_as_wait_activity` returns `False` for it, which
    also removes the inbox from the activity-waiter membership. Change both:
    - In `_has_pending_messages`, for the inbox only, check
      `self._unclassified_source_row_pending()` before the blocked-lane
      `continue`; if true, mark the precheck and return `True`. Already
      indexed requests on a blocked lane stay excluded, as today.
    - In `_queue_counts_as_wait_activity`, keep the inbox counted while its
      lane is blocked if `self._family_classification_can_advance()` is
      true. Other blocked lanes keep today's rule.
    - Stop if: keeping the inbox in membership makes the PostgreSQL waiter
      rebuild on every admission flip (watch for repeated waiter
      replacement in a `./bin/pytest-pg` run). Report it; do not add a
      cache to hide it.
  - Failure behaviour: let a `BrokerError` from the added read propagate
    exactly as the base `MultiQueueWatcher._queue_has_pending` does for
    the other queues. Add no `try`, retry, or fallback.
  - Update the `_has_pending_messages` docstring's last sentence only if
    it becomes false; otherwise leave it.
- Why this cannot spin: the predicate is true only when
  `_classify_family_requests` will run in the granted turn and read the
  row, which advances `_family_classify_after` past it. If that read
  fails, `_family_index_retry_after` is set and the predicate is false
  until the existing backoff ends.
- Not allowed: returning raw `has_pending()` for the inbox; removing
  `_family_classification_pending`; any change under
  `weft/core/tasks/multiqueue_watcher.py`; a new constant.
- Measure (engineering principle "Measure; do not bake in inefficiency"):
  rerun Appendix A and record idle-to-launch delay before and after.
  Record the same under `./bin/pytest-pg` if a PostgreSQL context is
  available; if not, say so in the execution log.
- Spec: apply the Slice 2 Replacement block. Add the two new functions to
  the [MA-1.8] `_Implementation mapping_` entry.
- Stop if: the test needs a patched clock or a patched queue; the fix
  seems to need a change in the watcher; the PostgreSQL run shows turns
  granted without watermark progress.
- Done when: the new test passes and
  `./.venv/bin/python -m pytest tests/core/test_manager_family_maintenance.py tests/core/test_family_dispatch_scaling.py tests/core/test_family_source_reconciliation.py -q`
  stays green.

### Task 3 — a successor commits `wrapper_lost` before it acknowledges an unhanded launch

- Outcome: `_retire_scheduler_residents` never deletes a held reservation
  without a terminal fact for its task.
- Files: `weft/core/manager.py`, `tests/core/test_admission_recovery.py`,
  `docs/specifications/03-Manager_Architecture.md`.
- RED first. Extend the pattern of
  `test_positive_scoped_retirement_acknowledges_exact_request_then_releases_checkpoint`
  with two tests built from `recovery_manager` and
  `_reserve_and_checkpoint` (both go through production boundaries):
  1. `test_unhanded_launch_settlement_commits_wrapper_lost_before_ack`:
     reserve and checkpoint one request, record the spawned checkpoint,
     age its process incarnation exactly as the existing test does, call
     `_retire_scheduler_residents()`, then assert by queue read that
     `T{tid}.ctrl_out` holds exactly one row with `type == "terminal"`,
     `source == "manager"`, `status == "failed"`, and `error` equal to the
     literal string of `WRAPPER_LOST_ERROR` written out in the test (not
     imported), and that the reserved row is gone.
  2. `test_unhanded_launch_settlement_defers_to_task_owned_proof`: same
     setup, but first write a task-owned terminal envelope
     (`source == "task"`, `status == "completed"`) to `T{tid}.ctrl_out`
     through a real queue. After retirement assert no Manager-source row
     was added and the reserved row is gone. This test passes before the
     change as well; it guards the precedence rule against the new write.
  3. `test_unhanded_launch_settlement_keeps_custody_until_publication_succeeds`
     (outside review, point 3; the two tests above would still pass if the
     write and the acknowledgement were swapped). Same setup as test 1.
     Make the terminal write fail at its real boundary. The writer opens
     `self._get_connected_queue().get_connection()` and calls
     `broker.write(ctrl_out_name, ...)`. With `monkeypatch`, replace
     `get_connection` on that one Queue instance with a context manager that
     yields the real connection wrapped so that `write` raises `BrokerError`
     when its queue argument is `T{tid}.ctrl_out` and delegates every other
     call; this fails every attempt in the retry loop and leaves every other
     write untouched. This is a fault
     injected at one external boundary, not a mock of the queue. Call
     `_retire_scheduler_residents()`. Assert: `T{tid}.ctrl_out` is empty,
     the reserved row is still present
     (`peek_one(exact_timestamp=..., include_claimed=True)`), and the newest
     Manager checkpoint still lists the resident with its reservation
     reference. Remove the fault and call `_retire_scheduler_residents()`
     again. Assert the envelope is now present and the reserved row is
     gone. Validate it as falsifiable: swapping the write and the
     acknowledgement in the change must turn this test RED; record that.
  4. `test_unhanded_launch_with_unresolved_route_keeps_custody`: reserve and
     checkpoint two requests and record both spawned checkpoints. For the
     first, write a body of `{}` (no `spec`, so `_build_child_spec` returns
     `None`) to the same reserved queue through a real queue, and point that
     resident's `evidence["request_id"]` at the new row, the way the
     existing retirement test edits `evidence["host_processes"]`. Age both
     process incarnations and call `_retire_scheduler_residents()`. Assert:
     no row was written to the first task's `ctrl_out`; its reserved row and
     checkpoint entry remain; the second resident settled (envelope present,
     reservation gone); and exactly one hold warning naming the first TID
     was logged, matched by its message, not by a count of all warnings
     (`_build_child_spec` logs its own validation warning for `{}`, and
     repeats it on each pass; that repetition is existing behaviour and is
     accepted here). Call `_retire_scheduler_residents()` a second time and
     assert no second hold warning for that TID (round-3 F2).

  Validated on a scratch copy of `44d7f900`: the first test fails before
  the change (`ctrl_out` empty) and passes after it; the second passes
  both times. That run used the earlier design with a default-`ctrl_out`
  fallback; in both tests the retained row exists and the route resolves,
  so the fallback was never exercised. The fault-injection and
  unresolved-route tests added in round 3 were not run there.
- Change:
  - Split `_write_manager_terminal_envelope` into a core that does not
    need a `ManagedChild`:
    `_commit_wrapper_lost_envelope(self, tid, *, ctrl_out_name: str, exitcode: int | None, deadline: float | None = None) -> bool`.
    Move the task-owned-proof check, the payload, and the retry loop into
    it unchanged. `_write_manager_terminal_envelope(tid, child, deadline=...)`
    becomes a two-line caller that passes
    `child.ctrl_out_queue or f"T{tid}.{QUEUE_CTRL_OUT_SUFFIX}"` and
    `child.process.exitcode`. Give `_child_terminal_proof_visible` the
    same treatment: a core `_terminal_proof_visible(self, tid, ctrl_out_name)`
    holding the existing body, and the existing signature as a thin
    caller.
  - Add
    `_unhanded_launch_ctrl_out_name(self, body: str, request_id: int) -> str | None`.
    The caller supplies a retained request body already read from the broker;
    the helper does not read the reservation:
    1. Derive the `ctrl_out` name the way launch does: build the child
       spec with `self._build_child_spec(json.loads(body), request_id)`
       and take `spec.io.control.get("ctrl_out")` or the canonical
       `T{spec.tid}.ctrl_out` when a successfully parsed spec omits the route,
       matching launch's default. An unreadable request never takes this default.
    2. Return `None` when the route cannot be resolved: `_build_child_spec`
       returns `None`, or the derive raises `ValueError`, `TypeError` or
       `KeyError` (catch exactly those around step 1, so one
       malformed retained row cannot abort the loop over the other
       residents). There is no fallback to the default `ctrl_out`: an
       envelope on a queue the task's observers do not read, followed by an
       acknowledgement, would discard custody on a guess ([MF-5]
       configured-route rule; outside review, point 2).
  - In `_retire_scheduler_residents`, before `_ack_launch_custody`, when
    `evidence.get("reserved_queue") is not None`:
    - read the retained request once by exact ID from
      `evidence["reserved_queue"]`, using
      `broker.peek_one(queue, exact_timestamp=request_id, include_claimed=True)`
      (the call shape `_ack_launch_custody` uses to confirm deletion).
      Broker read failures retain their existing error path; they are not
      treated as absence;
    - if that read returns `None`, take the existing path, which has no
      reservation left to delete. Do not call the route helper;
    - otherwise pass that body and request ID to the route helper. If its
      result is `None`, `continue`:
      the resident, its reservation and the checkpoint stay, the other
      residents proceed, and one `logger.warning` per TID says the launch is
      held because its terminal route cannot be read. That is a standing
      hold under [WT-2a.15]; publishing it is D5's carrier, not this task;
    - otherwise call
      `_commit_wrapper_lost_envelope(tid, ctrl_out_name=<route>, exitcode=None)`.
      If it returns `False`, `continue`: the resident and its reservation
      stay, and the next maintenance pass retries.
  - Residents with no reservation reference (handoff already happened)
    take the existing path unchanged.
- Not allowed: a second envelope writer; writing to the outbox or to
  `weft.log.tasks`; touching the `host_processes` skip at the top of the
  loop; any change to `_ack_launch_custody`.
- Spec: apply the Slice 3 Replacement block. Add
  `_commit_wrapper_lost_envelope` to the [MA-1.6a] mapping entry.
- Stop if: the change wants to act on a resident without process
  evidence (that is Owner Decision D1), or wants a new retry timer.
- Done when: all four new tests pass and
  `./.venv/bin/python -m pytest tests/core/test_admission_recovery.py tests/system/test_family_recovery.py -q`
  stays green.

### Task 4 — a failed one-shot records the child's return code

- Outcome: `weft task status <tid> --json` shows the command's exit code
  for a failed one-shot command.
- Files: `weft/core/tasks/consumer.py`; `tests/cli/test_cli_run.py`.
- RED first: add `test_cli_run_failed_command_records_return_code` beside
  `test_cli_run_target_failure_compatibility`. With `weft_harness` and
  `run_cli`, run
  `python -c "import sys; sys.exit(3)"`, assert the CLI exit code is 1
  (unchanged mapping), then assert the status JSON has `return_code == 3`.
- Change: in the `outcome.status == "error"` branch, pass
  `return_code=outcome.returncode` to `mark_failed`. Nothing else.
- Not allowed: changing the CLI exit-code mapping; touching the timeout,
  limit, or cancel branches.
- Done when: the test passes.

### Task 5 — bootstrap skips retained rows of another runtime identity

- Outcome: in a moved or copied project, `weft run` dispatches; the
  Manager logs one warning naming what it skipped.
- Files: `weft/core/runtime_identity.py`, `weft/core/manager.py`, new
  `tests/system/test_relocated_context.py`, `tests/core/test_admission_recovery.py`,
  `docs/specifications/03-Manager_Architecture.md`.
- Current structure: `Manager._bootstrap_scheduler_evidence` calls
  `_adopt_scheduler_evidence` in two loops (dead Managers' checkpoint
  residents; then task snapshots not covered by a live peer). That function
  builds a `RuntimeIdentity`, whose constructor raises
  `ValueError("connection owner does not match runtime identity")`, and
  then raises `ValueError("scheduler evidence belongs to another context")`.
  Either escapes to `_refresh_scheduler_evidence`, which logs at DEBUG,
  leaves `_admission_bootstrapped` false, and retries a second later, so
  dispatch never opens.
- RED first:
  1. `test_run_succeeds_in_a_relocated_copy_of_a_used_project`
     (`pytestmark = [pytest.mark.sqlite_only]`): in `tmp_path`, `init`, run
     `python -c "print('x')"`, `system stop`, `shutil.copytree` the project
     to a second directory, then `run` `python -c "print('y')"` there with
     `timeout=40.0` and assert exit 0 and `y` in stdout. Pass `stdin=""` to
     every `run_cli` call. Stop both projects in `finally`. Validated on
     scratch copies: at `44d7f900` the last run timed out after 40 s; with
     the change below it passed in about 6 s.
  2. `test_successor_skips_owner_mismatch_resident_and_keeps_its_custody`
     (in `tests/core/test_admission_recovery.py`): with `recovery_manager`,
     reserve and checkpoint one request (`_reserve_and_checkpoint`) and
     record its spawned checkpoint. Then edit the **nested resident entry**
     in that Manager's newest checkpoint row,
     `scheduler_frontiers.residents[<tid>]["connection_owner"]`, to a value
     that does not match the TID and context, and publish the edited payload
     as a new row of the same `weft.state.tasks.<manager_tid>` queue. Do not
     edit the top-level `connection_owner`: a `role == "manager"` row
     returns before identity validation. Stop the first Manager and start a
     successor on the same broker the way
     `tests/system/test_family_recovery.py` does. Write one unrelated
     request. Assert: bootstrap completes; the unrelated request is
     reserved; exactly one warning was logged (`caplog`) across at least two
     maintenance passes; and the original reserved request is still in its
     reserved queue (`peek_one(exact_timestamp=..., include_claimed=True)`)
     and was not returned to `weft.spawn.requests`. Not pre-validated:
     write it RED first (at `44d7f900` bootstrap never completes) and
     record the output.
  3. `test_successor_skips_owner_mismatch_snapshot_of_adopted_resident`:
     as test 2, but leave the checkpoint valid and instead publish the
     child's own `weft.state.tasks.<tid>` snapshot with a mismatched
     `connection_owner`. Assert bootstrap completes and stays complete
     across two maintenance passes, and one warning was logged. Not
     pre-validated.
- Change:
  - In `weft/core/runtime_identity.py` add
    `class RuntimeIdentityMismatch(ValueError)` with the docstring
    "Evidence names a runtime identity other than the one reading it." Raise
    it, with the existing message, at the connection-owner check in
    `RuntimeIdentity.__post_init__`. It subclasses `ValueError`, so every
    existing `except ValueError` still catches it.
  - In `Manager._adopt_scheduler_evidence` raise it, with the existing
    message, in place of the context `ValueError`. Leave the "task scheduling
    identity changed" check as `ValueError`: that is a change between two
    reads, not a foreign identity.
  - In `_bootstrap_scheduler_evidence`, wrap each of the two
    `_adopt_scheduler_evidence` calls in
    `try: ... except RuntimeIdentityMismatch as exc: skipped[tid] = str(exc)`,
    then call `self._warn_skipped_bootstrap_evidence(skipped)` before
    `_recover_scheduler_paths()`.
  - **Skipping evidence never releases custody** (round-2 F1). When the
    skipped row is a checkpoint resident that carries `reserved_queue` and
    `request_id`, add that pair to `self._skipped_custody`
    (`set[tuple[Any, Any]]`, set in `__init__`), and in
    `_maintain_orphan_reservations` union it into `protected`. Otherwise an
    unprotected request with no task snapshot is moved back to the source
    queue, and a wrapper that started without publishing would run twice.
    The held request is not replayed and does not gate other dispatch; it
    is a standing hold of the kind D1 and D5 cover.
  - **The same rule applies where reconciliation re-reads a snapshot**
    (round-2 F2). A TID adopted from a valid checkpoint can have its own
    snapshot skipped; `_refresh_scheduler_evidence` then reads that
    snapshot again for the resident, and the mismatch would abort the pass
    and keep `_admission_bootstrapped` false. Wrap that one
    `_adopt_scheduler_evidence` call in the same `except
    RuntimeIdentityMismatch`, report it through
    `_warn_skipped_bootstrap_evidence({tid: reason})`, and continue with
    `latest = None` for that TID. This also applies after bootstrap: a
    foreign-identity row then no longer aborts the whole reconciliation
    pass. The owner confirmed this on 2026-11-05 ("Both, as planned").
  - `_warn_skipped_bootstrap_evidence(skipped)` keeps one record,
    `self._bootstrap_skipped: dict[str, str]` (set in `__init__`), and logs
    one `logger.warning` only for TIDs not already in it, then adds them.
    That one rule covers a retried bootstrap and a per-second
    reconciliation pass alike: a row already reported is never reported
    again. Proposed wording, for owner review: "Manager bootstrap ignored N
    retained row(s) that name another runtime identity (for example <tid>:
    <reason>). Retained state written under another context is not
    adopted."
- Where the warning appears: the Manager's module logger, which a
  foreground `weft manager serve` prints to stderr (seen on the scratch
  copy). Whether a background Manager's warning reaches any file was not
  checked. Making it a published fact is part of D5, not this task.
- Not allowed: pre-detecting moved directories; reading the row back; a new
  mode or flag; catching non-identity failures; touching
  `_recover_unlaunched_reservations` (owner: "not supported").
- Spec: apply the Slice 5 Replacement block.
- Stop if: skipping a row leaves a stale frontier entry or pending token
  that blocks dispatch, or a non-identity check fails in the copied
  project.
- Validated on the scratch copy after the round-2 fixes: test 1 passed,
  and `tests/core/test_admission_recovery.py`,
  `tests/system/test_family_recovery.py`, `tests/core/test_runtime_identity.py`,
  `tests/core/test_manager.py`, `tests/core/test_manager_family_maintenance.py`
  and `tests/system/test_family_matrix.py` passed except the one payload
  pin Task 6 retargets. Tests 2 and 3 were not written there.
- Done when: all three tests pass, and
  `./.venv/bin/python -m pytest tests/core/test_admission_recovery.py tests/system/test_family_recovery.py tests/core/test_runtime_identity.py -q`
  stays green.

### Task 6 — internal services carry process titles

- Outcome: `ps` shows heartbeat, TaskMonitor and LivenessMonitor as
  `weft-<context>-<tid>:<name>:<status>`.
- Files: `weft/core/manager.py`, `tests/core/test_manager.py`, new
  `tests/cli/test_internal_service_titles.py`,
  `docs/specifications/03-Manager_Architecture.md`.
- Current structure: `BaseTask` titles a task unless
  `spec.enable_process_title` is false (`weft/core/tasks/base.py`, near
  line 383). The three Manager payload builders,
  `_build_heartbeat_spawn_payload`, `_build_task_monitor_spawn_payload` and
  `_build_liveness_monitor_spawn_payload`, each set
  `"enable_process_title": False`. Git history traces the key to a
  foreground TaskMonitor spec that runs inside the CLI process, where it
  is correct; the key was then copied into the supervised payloads, and no
  rationale was recorded (relayed from the side-fork session's git search;
  it did not search every retired plan).
- RED first: `test_internal_services_carry_process_titles`
  (`pytestmark = [pytest.mark.shared, pytest.mark.skipif(os.name == "nt", ...)]`,
  following `tests/cli/test_manager_proctitle.py`): `manager start`, then
  poll `weft status --json` for up to 15 s; for every `services` entry with
  `status == "running"`, read the title through `psutil` the way the
  existing title test does; assert the heartbeat is running and every
  running service's title contains `:<name>:`. In the harness only the
  heartbeat runs. Validated on scratch copies: at `44d7f900` it failed
  ("untitled internal services: {'heartbeat-service'}"); with the change it
  passed in about 6 s.
- Change: delete the `"enable_process_title": False,` line from each of the
  three payload builders, so the shared default applies. Do not write
  `True`. Leave the synthetic foreground TaskSpec in
  `weft/core/monitor/task_monitor.py` (`enable_process_title=False`) alone:
  it must not retitle the CLI process.
- Existing test to retarget (test-audit): `tests/core/test_manager.py`
  in `test_manager_enqueues_liveness_monitor_when_independently_enabled`
  asserts `payload["taskspec"]["spec"]["enable_process_title"] is False`.
  Change it to assert the key is absent. Add the same absent-key assertion
  to the TaskMonitor enqueue test in the same file (round-2 F4: the
  harness disables both monitors, so the black-box test sees only the
  heartbeat, and nothing else would catch a `False` left in
  `_build_task_monitor_spawn_payload`). The new black-box test owns the
  observable behaviour.
- Cost check: the setproctitle cost on macOS is a known pressure point.
  The full fast suite on the scratch copy with all six changes took
  334.15 s against 333.75 s at baseline: one run each, same machine, so no
  measurable change but not conclusive. Repeat once at implementation and
  record both numbers.
- Spec: apply the Slice 6 Replacement block.
- Done when: the new test passes and `tests/core/test_manager.py` is
  green.

### Task 7 — document context identity and the mixed-context case

- Files: `docs/specifications/04-SimpleBroker_Integration.md`,
  `README.md` (Project Context section).
- Apply the Slice 7 Replacement block. In `README.md`, after the context-selection example and its explanatory
  paragraph (so its introduction stays next to the code block), add:
  "A Weft context is its resolved broker identity. Moving a project
  directory, reaching the same `.weft` through a different resolved path
  (such as a new container mount point), or reaching the same PostgreSQL
  database through a different host, user, database or options spelling
  creates a different context (a password change does not); runtime
  state left by the old one is ignored. Using two contexts against one
  database is not supported and may behave inconsistently across tasks."
- Not allowed: describing a clearing procedure (none is needed after Task
  5, and none was verified).
- Done when: `bin/check-doc-paths` and `tests/specs` pass.

### Task 8 — closeout

- `CHANGELOG.md`: one line per slice under the unreleased heading.
- Rerun every final gate in `## 7`.
- Record the Appendix A numbers and the Task 1 CPU reading after the
  change.
- Request the completed-work review (`## Review Log`), then flip `Status:`
  and the index row to `completed`.

## 6. Testing Plan

| Slice | Test | Harness | Proves | Kept real |
|-------|------|---------|--------|-----------|
| 1 | `test_manager_does_not_spin_while_user_task_runs` | `weft_harness`, `run_cli`, `psutil` | [WT-4.3] "a quiet task does nothing" with a live child | Manager process, child process, broker |
| 2 | `test_new_source_row_wakes_idle_manager_wait` | `manager` fixture, `broker_env`, `reactor_driver` (bootstrap only) | [MA-1] "queue activity still wakes the manager promptly" | Manager object, broker, the wait itself |
| 3 | four settlement tests, one with a terminal-write fault and one with an unresolved route | `recovery_manager`, `_reserve_and_checkpoint` | [MA-1] item 7 rule on the successor path, including publication before acknowledgement under failure; [MF-5] precedence and configured route | broker, reservation, checkpoint, envelope; the only fault is at the terminal write |
| 4 | status JSON return code | `weft_harness`, `run_cli` | `state.return_code` | whole CLI path |
| 5 | relocated copy dispatches; mismatch row skipped and warned once | `run_cli` in `tmp_path`; `recovery_manager`, `caplog` | [MA-1.8] bootstrap rule as revised | whole CLI path; broker; bootstrap |
| 6 | internal service titles | `weft_harness`, `run_cli`, `psutil` | [OBS.4] for internal services | Manager, service processes |

Do not mock `simplebroker.Queue`, the Manager lifecycle, the wait, or
process state in any of these. Do not patch `time` in any module.

Edge cases in scope: task-owned proof already present (Task 3, second
test); terminal-write failure and recovery (Task 3, third test); an
unresolvable route (Task 3, fourth test); a new request while admission
blocks the public lane (Task 2, second test). Out of scope, with reason: PostgreSQL-specific assertions for Task
2 (the predicate is backend-independent; the PostgreSQL run is recorded as
a measurement, and the existing PG suite must stay green); Windows CPU
accounting for Task 1 (`psutil.cpu_times` is portable; no platform
branch).

Runtime observation after landing: Appendix A reports idle-to-launch
delay; `ps -o time=` on a Manager with a running task reports CPU. Both
are recorded in the execution log before and after.

## 7. Verification

Per task: the commands in each task's "Done when".

Final gates (from a tree containing the whole change):

```bash
./.venv/bin/python -m pytest tests/system/test_manager_reactor_floor.py tests/system/test_relocated_context.py tests/cli/test_internal_service_titles.py tests/core/test_manager_family_maintenance.py tests/core/test_admission_recovery.py tests/system/test_family_recovery.py tests/core/test_runtime_identity.py tests/core/test_manager.py -q
./.venv/bin/python -m pytest tests/specs -q
bin/check-doc-paths
./.venv/bin/ruff check . && ./.venv/bin/ruff format --check .
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/python -m pytest -q
./bin/pytest-pg
```

Success: every command exits 0; the Task 1 CPU reading is at idle level;
the Appendix A delays are within one strategy pass plus launch time.

## 8. Rollback

Each slice is one commit and reverts alone. No persisted format, queue
name, or payload shape changes, so old and new code read each other's
rows. Slice 3 writes an extra `wrapper_lost` row of the existing type;
readers at `44d7f900` already classify it. There is no rollout order
beyond landing order.

## 9. Out of Scope

Found in the same review and deliberately not touched here:

- Everything in `## 10. Owner Decisions`.
- `weft result` times out (exit 124) on a task whose only terminal fact is
  a Manager-source `wrapper_lost` envelope and which never published
  observation metadata, although `weft task status` reports `failed
  (wrapper_lost)`. Verified 2026-11-05. It belongs to the result-settlement
  contract, not to the Manager.
- A dead Manager's row stays `running` in `weft status` beside the active
  one, which [OBS.15] forbids. Verified 2026-11-05 three minutes after a
  SIGKILL.
- The one-second broker probe an idle Manager runs. After Slice 2 a new
  request is an event, so the probe may no longer answer "which event
  cannot arrive"; removing it changes [MA-1] item 5 and is its own
  question.
- SQLite's serialized startup window, `--timeout` validation, exit-code
  classes for `--pipeline`, the stdin read in `weft run`, and every
  non-Manager finding in the review.
- Any refactor of the Manager's deadline composition beyond the two
  guarded deadlines.

- Context canonicalization. Owner, 2026-11-05: "those match my existing
  conception"; `canonical_runtime_context` stays as it is.
- `Manager._recover_unlaunched_reservations`, which matches a dead
  Manager's checkpoint by source queue name rather than by context. Owner:
  "this is ok to be defined as not supported"; Task 7 documents it.
- Identity-check failures outside bootstrap adoption, and non-identity
  failures during bootstrap (malformed checkpoint structure, failed
  checkpoint publication, read failures). They keep their current rules.

## 10. Owner Decisions

Each item below reverses a recorded spec rule or adds public surface, so
it is `RECONSIDERATION REQUESTED` rather than a task. Options are listed
with a recommended default. A ruling becomes its own plan, or a revision
of this one through `## Revision Log`.

**D1. How an in-doubt launch ends. Direction ruled; carrier and ending
action still open.** A Manager killed between its
pre-launch checkpoint and `Process.start` (a window measured at 5 to 12 ms
per launch) leaves a resident with no process evidence. The successor adds
it to `_scheduler_undurable`, which gates all ordinary dispatch for the
context, across restarts, with `weft status` showing the Manager `active`
and tasks `queued`. Reproduced: a follow-up `weft run` sat undispatched
for over three minutes. [MA-1.8] says this "can require operator
resolution"; no resolution verb or diagnostic exists, and [MF-6] still
describes an in-memory suspension that no code clears.

- (a) Keep the context-wide gate and add an operator verb that settles one
  held request as launched or not launched.
- (b) Scope the gate to the ambiguous request: it stays reserved and is
  never replayed, and other work dispatches. *Recommended.* The request's
  own exactly-once guarantee is untouched. The context-wide gate charges
  every future launch for one process of unknown usage, which is the shape
  [ALT-WT-001] and the 2026-10-15 lesson refuse; on SQLite the resource
  gate is off, and on PostgreSQL measured usage already counts whatever
  the process holds. An operator verb for the held request can follow.
- (c) Keep everything and only publish the reason (see D5).

Ruled 2026-11-05 through the theory revision ([WT-2a.15]):
option (b) in direction. The ambiguous request stays reserved and is never
replayed; other ordinary work dispatches; the hold is a published fact; and
a named piece of evidence or operator action ends it. The spec text that
changes is the [MA-1.8] sentence beginning "It blocks further ordinary
dispatch until a matching positive task-owned snapshot". That change, with
D5, belongs in a separate closing plan for [WT-2a.15], not in this one,
once the two open points are ruled:

- what carries the published hold (see D5);
- what ends it: today a matching task-owned snapshot or durable receipt;
  the open question is the operator action for the case where neither will
  ever arrive.

Hidden coupling to check before the slice is written, not settled: whether
family scheduling stays correct when unrelated work proceeds past a held
launch. The held resident sits in the frontier with `defer=True`
(`Manager._recover_scheduler_paths`); work below it in the same family must
not overtake it in a way the frontier rules forbid.

**D2. Retained evidence that names another context. Ruled; now slices 5
and 7.** After a used project directory is moved, every retained row
carries the old resolved path. `_adopt_scheduler_evidence` raises
`ValueError("scheduler evidence belongs to another context")`, bootstrap
never completes, the error is logged at DEBUG once a second, and no launch
happens. Reproduced with `mv` and with a copy of the directory.

The owner first ruled this a documentation item, then ruled that bootstrap
should skip non-matching rows rather than abort, with a warning, and that
a dead Manager's reserved rows matched by queue name across two contexts
sharing one database are "ok to be defined as not supported". The code
change is Task 5; the documentation is Task 7. No canonicalization change.
`Manager._recover_unlaunched_reservations` is left as it is.

**D3. Lifetime of a spilled result.** `weft run` hard-sets
`cleanup_on_exit`, and the Consumer removes the spill directory at
completion, so a result over 10 MB returns exit 0 and a `large_output`
reference to a file that no longer exists. Reproduced with 20 MB; 9 MB is
intact. `05-Message_Flow_and_State.md` both says the spill "can be
inspected later" and, under Cleanup Boundary, that cleanup "may remove
spilled output when `cleanup_on_exit` is enabled".

- (a) A spill that a delivered outbox row references is result data, not
  scratch: `cleanup_on_exit` leaves it, and it is removed when retention
  removes that outbox row. *Recommended.* It keeps the 2026-09-10 ruling
  that spill references are returned unchanged.
- (b) `weft result` and the client dereference the spill, deliver the
  content, and delete it on consumption. Reverses the 2026-09-10 ruling.
- (c) Keep the behaviour and fail the task instead of returning a dangling
  reference.

**D4. A failed command's stdout.** When a one-shot command exits non-zero,
only stderr reaches the caller, folded into the error string; stdout is in
no queue. The specs are silent.

- (a) Deliver captured stdout to the outbox on failure as on success;
  `weft run` prints it, then the error, and still exits 1. *Recommended*
  under [WT-2a.10].
- (b) Keep discarding it and say so in the CLI spec.

**D5. Publishing a persistent dispatch block as a fact. Required; carrier
open.** [WT-2a.15] requires it: "the refusal, its
reason, and the records that cause it are published as a queue fact that
an ordinary status read shows". It covers the in-doubt launch (D1) and any
other standing refusal. After Task 5 a moved project no longer refuses, so
that case drops out. The carrier is the open question: the Manager's
registry record in `weft.state.services` (already rendered by
`weft status`, refreshed every 30 s), its own task-state snapshot (which
also carries launch custody, so publishing it during a failed bootstrap
needs care). A process title is not a candidate: [WT-2a.15] requires a
queue fact, and a title is neither durable nor a queue row. No
recommendation yet; the registry record looks least entangled, but I have
not traced its readers. The ruling goes into the separate closing plan
named under D1.

## Review Log

- 2026-11-05: plan drafted from the deep-dive review (Claude Fable 5.1).
  Every defect was reproduced at `44d7f900`; the root causes of defects 1
  and 2 were confirmed by intervention (idle timeout disabled; inbox
  predicate patched out-of-tree). Paths, symbols, and the verbatim spec
  spans were checked by lookup.
- Validation on a scratch copy (`git archive 44d7f900`, outside the
  repository, run with the repository's virtualenv), 2026-11-05. The
  changes in Tasks 1 to 4 were applied there as written:
  - Task 1: Manager CPU with one task running fell from 9.95 s per 10 s to
    0.08 s per 8 s.
  - Task 2: the planned test was RED three times (3.0 s) and GREEN twice
    (0.000 s); Appendix A went from 17–949 ms to 14–35 ms.
  - Task 3: the first planned test was RED then GREEN, the second GREEN
    both times; the double-fault crash scenario now ends in
    `failed (wrapper_lost)` with the envelope on `ctrl_out`.
  - Task 4: `return_code` reads 3.
  - Regression: with Tasks 1 to 3 applied, 349 tests passed and 3 skipped
    across `tests/core/test_admission_recovery.py`,
    `tests/system/test_family_recovery.py`, `tests/core/test_manager.py`,
    `tests/core/test_manager_family_maintenance.py` and
    `tests/system/test_family_matrix.py`; `mypy` was clean on
    `weft/core/manager.py`. The full suite and `./bin/pytest-pg` were not
    run on the scratch copy.
- Self-driven fresh-eyes review: done 2026-11-05, a distinct pass after
  drafting, by running the planned tests rather than only rereading. Five
  corrections, all applied above: (1) the Task 2 test would have passed
  falsely, because a manual wait also ends at the one-shot process-title
  deadline; a positive control now precedes the measured wait and the
  bound is tighter; (2) the Task 2 queue assertion named the pending-label
  queue, whose token is consumed by the same turn; it now checks the
  source queue; (3) new test modules need a backend marker or collection
  fails; (4) Task 4 named its test file loosely; (5) the PostgreSQL cost
  of the added read was missing from the differences table. Residual
  risk: Task 1's pytest was not run as written (the CPU reading was taken
  with `ps`), and nothing here was run on PostgreSQL or Windows. External
  review is still required.
- Cross-family review, round 1 (2026-11-05). Author family: Claude
  (Fable 5.1). Reviewer: qwen 0.17.1 in plan approval mode with
  `meta/muse-spark-1.3-contributor` through OpenRouter, the configuration
  the previous plan's closing review recorded. Reviewer availability:
  codex is out of quota until 2026-11-11 and grok's sandbox cannot be
  applied on this machine (both recorded in
  `2026-11-04-context-level-service-singletons-plan.md`); qwen with its
  default model returned API 404, and with the model but without the
  OpenRouter mapping returned API 401. Containment was probed immediately
  before use: asked to create a file in the repository root, it declined
  and `git status` was unchanged. Attempt 1 embedded the plan in the
  prompt, produced no output and was stopped at 560 s (a stall). Attempt 2
  pointed at the plan file, streamed, made 37 tool calls and finished;
  `git status` was again unchanged by it. It read the plan, the cited
  functions in `weft/core/manager.py`,
  `weft/core/tasks/multiqueue_watcher.py`, the Consumer error branch, and
  the cited spec sections; it reported that it did not re-read the test
  fixtures or the SimpleBroker API.
  Verdict: **PASS**. It confirmed all four causal premises against the
  code and found the three Replacement blocks preserve every clause of the
  spans they replace.

  | ID | Severity | Finding | Disposition |
  |----|----------|---------|-------------|
  | F1 | P2 | Parity between the new `peek_many` read and the classification `peek_generator` read was not verified; if they differ the progress argument fails | Accept. Verified in SimpleBroker 8.7.2 (same `include_claimed=False` default, same `after_timestamp` handling); recorded in Task 2 with a required comment at the call |
  | F2 | P2 | The `ctrl_out` fallback can misdirect the envelope for a task with a custom `ctrl_out` whose retained request is unreadable | Accept as best effort. Task 3 now requires a debug log and a docstring note |
  | F3 | P2 | An exception escaping the `ctrl_out` derive would skip the remaining residents for that pass | Accept. Task 3 now names the exact exceptions to catch around the derive |
  | F4 | nit | The first turn after the last child exits still sees one stale probe deadline | Accept. Task 1 now states that the single wake is expected |
  | F5 | nit | An always-true predicate would also pass the Task 2 test | Decline. On SQLite a predicate that is wrongly true only matters when a hint arrives, so a second quiet wait would not catch it; the existing suites (361 tests green with the change on the scratch copy) and the PostgreSQL hot-loop tests are the guard. Recorded as residual risk |
  | F6 | nit | The reviewer did not re-read the fixtures, `request_body`, `drive_until`, or the status JSON path | No change. The author checked each by lookup or by running it; the nested status path is still marked "confirm once" in Task 1 |

  Reviewer observations on `## 10` (not part of the verdict): D1(b) is the
  coherent default but needs its own plan and operator story; D2(a) needs
  a defined skip-and-warn rule so a foreign row is not silently stranded;
  D3(a) and D4(a) each change a documented contract and need spec text;
  D5 rightly leaves the carrier open.
- Reviewed baseline: this file as of the round-1 dispositions above, on
  `44d7f900`. No second round was needed for those dispositions: the
  verdict was PASS and none changed scope, invariants, or authority.
- Reviewer choice corrected by the owner (2026-11-05, in this session):
  "Codex has quota now" and "agy/argon should be the third choice, not
  qwen". The round-1 log line above that calls codex out of quota repeats
  the previous plan's log, which was stale by then. codex 0.161.0 answered
  a liveness probe the same day.
- Scope changed after round 1 (Revision Log, second row): slices 5, 6
  and 7 were added and D1 and D5 were ruled in direction. Under
  `review-loops-and-agent-bootstrap.md` that re-enters review; round 2,
  with codex, is recorded below.
- Validation of the new slices on scratch copies (2026-11-05):
  - Task 5, first test: at `44d7f900` the run in the copied project timed
    out after 40 s; with the change it passed. A foreground Manager logged
    the warning once ("Manager bootstrap ignored 5 retained row(s) ...").
  - Task 6: the planned test failed at `44d7f900` and passed with the
    change; `ps` showed all three services titled in a default context.
  - Full fast suite on the scratch copy with all six changes:
    6,583 passed, 47 skipped, 3 failed. One failure is the payload pin that
    Task 6 retargets. Two are `tests/specs/test_ruff_policy.py` tests that
    run `git ls-files`, which exits 128 because the scratch copy is not a git
    checkout; I did not confirm those two pass on the real tree with the
    change.
- Cross-family review, round 2 (2026-11-05). Reviewer: codex 0.161.0,
  `codex exec -s read-only` with `model_reasoning_effort="high"` (OS
  sandbox). Scoped-change prompt over the delta listed in the Revision Log.
  `git status` was unchanged by the review apart from a file another
  session changed meanwhile (`skills/call-agent/SKILL.md`). Verdict:
  **blocker: F1, F2**.

  | ID | Severity | Finding | Disposition |
  |----|----------|---------|-------------|
  | F1 | P1 | Skipping a checkpoint resident drops it from the reservation-protection set, so `_maintain_orphan_reservations` can requeue a request whose wrapper may have started: a replay where HEAD only stalls | Accept. Task 5 now holds skipped custody (`_skipped_custody` unioned into `protected`); new test 2 asserts the request stays reserved. Verified by reading `_maintain_orphan_reservations`; the fix ran green on the scratch copy |
  | F2 | P2 | A resident adopted from a valid checkpoint whose own snapshot is skipped is re-read by `_refresh_scheduler_evidence`, which aborts the pass and leaves bootstrap incomplete | Accept. Task 5 applies the same skip at that call; new test 3. The extension to post-bootstrap reconciliation is flagged for the owner |
  | F3 | P2 | The planned owner-mismatch test edited the Manager row's top-level `connection_owner`, which identity validation never reads | Accept. Test 2 now edits the nested resident entry; test 3 covers the child snapshot |
  | F4 | P2 | No check catches a `False` left in the TaskMonitor payload | Accept. Task 6 adds the absent-key assertion to the TaskMonitor enqueue test |
  | F5 | P3 | "Differently spelled DSN" overstates the rule: a password change keeps the context (`tests/core/test_runtime_identity.py`) | Accept. Slice 7 and the README text are qualified |

  Reviewer also confirmed that the three quoted spec spans match HEAD,
  that no clause is dropped, that existing `except ValueError` handlers
  keep their behaviour, and that titling uses the existing shell-safe
  formatter. Round-2 verification of F1 and F2 is recorded below.
- Round-2 verification (codex, same invocation, scoped to F1 and F2 only):
  **PASS** for both, no new defect. It traced every path that could
  requeue or acknowledge a skipped resident's reservation
  (`_maintain_orphan_reservations`, `_recover_unlaunched_reservations`,
  `_retire_scheduler_residents`, `_ack_launch_custody`) and the
  `latest=None` path through `_advance_launch_custody`. Its stated limit:
  integration behaviour of tests 2 and 3 is inferred until they are
  written. HEAD had moved to `d5d47b85` by then; the two intervening
  commits touch only `skills/call-agent/SKILL.md`, so the `44d7f900`
  baseline still holds for every file this plan edits.
- Owner decision (from F2's disposition), 2026-11-05, in this session:
  "Both, as planned". The identity-mismatch skip applies during bootstrap
  and when post-bootstrap reconciliation re-reads a snapshot, so one
  foreign-identity row never aborts a reconciliation pass.

- Outside review (2026-11-05, relayed by the owner: "Update the plan to
  address the feedback"). Six points plus one craft note, all accepted;
  see the Revision Log rows of the same date. Round 3 (codex 0.161.0,
  read-only, scoped to that delta): **blocker: F1**, F2.

  | ID | Severity | Finding | Disposition |
  |----|----------|---------|-------------|
  | F1 | P2 | The blocked-lane test used a fixture with admission disabled on SQLite, so the block would not survive the turn; on verification, a turn with no candidate also never evaluates admission | Accept, fixed in two steps: positive ceiling with usage held above it, then a seeded request A to establish the block before measuring request B. Scoped check: PASS |
  | F2 | P3 | The unresolved-route test counted all warnings, but `_build_child_spec({})` logs its own | Accept. The test matches the hold warning by message and TID and checks it is not repeated. Verified |

  Reviewer observations: the six points are addressed at the design level;
  the write-failure test would detect acknowledgement before publication;
  Appendix A rejects missing samples; the status sequence matches
  Replacement-sync; Task 5 has one warning record. `bin/check-doc-paths`
  passes.

## Revision Log

| Date | Baseline | Section | Change and reason | Re-review required? |
|------|----------|---------|-------------------|---------------------|
| 2026-11-05 | reviewed draft before this revision | Spec Baseline; Task 3 | Clarified the remaining review findings without changing intended behaviour: keep the promotion baseline pending until final promotion, record intermediate baselines in the Execution log, and read the reservation once in the settlement caller so absent rows and unresolved routes remain distinct. | No: clarification of the reviewed sequencing and custody rules |
| 2026-11-05 | the row below | `## 5` Task 2 | Round-3 verification found F1's setup still incomplete: no admission evaluation runs without a candidate. The test now seeds request A to establish the block, then measures the wake for request B. | One scoped check of F1 only |
| 2026-11-05 | the row below | `## 5` Tasks 2 and 3 | Round-3 dispositions: the blocked-lane test enables admission and holds usage above the ceiling so the block survives the turn (F1, blocker); the unresolved-route test matches the hold warning by message and checks it is not repeated (F2). | Round-3 verification of F1 with codex |
| 2026-11-05 | round-2 PASS text plus the two rows below | header, `## 5` status sequence and Tasks 2, 3, 5, `## 6`, `## 10` D1 and D5, Appendix A | Outside review relayed by the owner ("Update the plan to address the feedback"). (1) The plan no longer claims to close [WT-2a.15]; D1's spec change and D5 move to a separate closing plan, and a process title is ruled out as D5's carrier. (2) Task 3 no longer falls back to the default `ctrl_out`: an unresolved route keeps custody and warns. (3) Task 3 adds a terminal-write fault test and an unresolved-route test. (4) Task 2 handles an admission-blocked public lane in `_has_pending_messages` and `_queue_counts_as_wait_activity`, with a new test for prompt classification and quiet afterwards. (5) Appendix A reads each sample at once and rejects a short sample. (6) The plan stays `draft` until its last Replacement block lands. Also: Task 5 keeps one warning record. | Yes: Task 2 and Task 3 change behaviour and add tests. Scoped round with codex over this delta. |
| 2026-11-05 | round-2 PASS text on `44d7f900` | `## 5` Task 5, Review Log | Owner confirmed the reconciliation skip applies after bootstrap as well ("Both, as planned"); flag replaced by the ruling. No behaviour change to the plan. | No |
| 2026-11-05 | round-2 PASS text on `44d7f900` | header, spec delta Related Plans, `## 10` D1 and D5 | The theory revision was promoted in the worktree; citations of [WT-2a.15], the [WT-4.1] wake-predicate row and the [WT-4.3] quiet row now name theory text, not accepted wording; the Related Plans bullet goes below the theory plan's bullet. Wording only. | No |
| 2026-11-05 | round-1 reviewed text on `44d7f900` | header, `## 1`, `## 3`, `## 4`, spec delta, `## 5` Tasks 5–8, `## 6`, `## 7`, `## 9`, `## 10` | Owner rulings relayed from the side fork: bootstrap skips identity-mismatched rows with one warning (Task 5); internal services are titled (Task 6); context identity and the unsupported mixed-context case are documented (Task 7); no canonicalization change; `_recover_unlaunched_reservations` left alone as unsupported; the theory revision lands first; D1 decided in direction and D5 made required by [WT-2a.15] (accepted wording). Supersedes the first row's D2 text and its canonicalization question. | Yes: new slices and new spec text. Round 2 with codex. |
| 2026-11-05 | round-1 reviewed text on `44d7f900` | `## 9`, `## 10` D2 and D5 | D2 changed from an open decision with three options to "ruled, not a code defect", on an owner ruling relayed by a side-fork session. Added a documentation item and an open question on context canonicalisation to `## 9`; D5 now names the D2 symptom. No task, invariant, spec delta, or test changed. | No. The implementation slices and the spec delta are untouched; the reviewer's remark on D2(a) in the Review Log refers to the option that was withdrawn. |

## Execution log

- 2026-11-05: implementation baseline `2d8c33a319930056600edaa6be5d741609d18456`.
  Comprehension: (1) `_activity_hint_action` asks `_has_pending_messages`,
  whose inbox predicate consulted only scheduler memory. (2) Raw pending
  includes indexed admission/custody-held requests and could spin. (3) Only
  Manager writes wrapper-lost to the configured ctrl_out, preserving task
  terminal precedence. (4) No host-process evidence or no proven scoped
  death still skips retirement. (5) Only RuntimeIdentityMismatch is skipped;
  malformed checkpoints, own publication failures and other read failures
  keep their rules.
- Slice 1 RED: real Manager consumed 2.476744444 CPU seconds in a 3-second
  observation window. GREEN: new CPU regression and 26 existing idle/wait
  cases passed.
- Slices 2–4 RED: new source row failed the real readiness predicate;
  successor settlement left ctrl_out empty; failed command status returned
  null return_code. Their fixes retain existing admission, custody and exit
  code contracts. The timing-only wake test passed before the fix, so its
  owner-boundary readiness assertion supplies the firing regression.
- Implementation clarification: broker.peek_one explicitly uses
  with_timestamps=False when reading a request body. A successfully parsed
  spec with omitted ctrl_out uses launch's canonical default; unresolved
  parsing holds custody. Validating sparse default routes exposed both
  details missing from the plan's pseudocode.
- All seven spec spans applied in the worktree before promotion. Full
  verification and completed-work review are recorded below.


### Implementation verification and review (2026-11-05)

- Implementation baseline: `2d8c33a319930056600edaa6be5d741609d18456`.
  The seven slices were implemented together in the shared worktree and
  verified as one frozen runtime. A single targeted implementation commit
  replaces the planned seven slice commits: this keeps the integrated
  custody/readiness validation and final promotion together and excludes
  unrelated concurrent libc/subprocess edits. No intermediate per-slice
  commit or independent-review verdict is claimed.
- Runtime clarification: omitted ctrl_out on a valid parsed TaskSpec gets
  the same canonical default as launch. An unreadable request still holds
  custody. Request body reads explicitly use with_timestamps=False.
- Test clarification: the original timing-only wake test passed before the
  fix. The new test also asserts readiness through the real owner boundary;
  the old code fails there. The existing admission test's public waiter
  membership pin changed to True under the new subscription rule and now
  also proves that indexed blocked work is not actionable.
- SQLite full fast gate: `. ./.envrc` then
  `./.venv/bin/python -m pytest -q` passed, 6,607 passed and 49 backend or
  platform skips. The count comes from a separate collection-only command:
  6,656 selected out of 6,671, 15 slow cases deselected; the runtime gate
  exited 0. Default 12-worker concurrency was retained.
- PostgreSQL full gate: `./bin/pytest-pg` passed, 6,459 passed and 40 skips
  in 483.88 seconds, PostgreSQL 18 in a disposable Docker container. The
  longest test was the long-session transcript, 385.83 seconds; no load or
  timeout was reduced. The wrapper cleaned up its container.
- Canonical mypy command from section 7 passed on 491 source files;
  `ruff check .` and `ruff format --check .` passed (665 files formatted);
  `bin/check-doc-paths` and `git diff --check` passed.
- CPU probe: the firing pre-fix regression measured 2.476744444 CPU seconds
  in a three-second supervising window. The fixed real-process probe
  measured 0.035848076 CPU seconds in the same window.
- Appendix A, fresh baseline archive at the implementation baseline, ms:
  `[483, 106, 100, 488, 723, 817, 614, 229, 460, 374, 676, 328, 368, 532]`.
  Fixed runtime, ms:
  `[17, 13, 19, 15, 11, 25, 8, 22, 12, 18, 21, 14, 13, 10]`.
  Both probes used SQLite, macOS and WEFT_TASK_MONITOR_ENABLED=0 explicitly
  to retain the measurement rows. All 14 samples were present. Both probe
  contexts were stopped through the supported system-stop command.
- Sensitivity controls in isolated trees: baseline successor settlement
  failed with an empty terminal queue; failed-command status returned null
  instead of 3; both persisted identity variants could not dispatch unrelated
  work; the heartbeat had no task title; relocated execution timed out at
  40 seconds; the write-failure test found its reservation prematurely gone.
  An acknowledgement-before-publication mutation also failed at that exact
  reservation-preservation assertion. The mutation was restored byte for
  byte, and the restored write-failure and both identity cases passed.
- Independent completed-work review: Google-family agy, plan mode plus
  sandbox, gemini-4-argon-medium, via skills/call-agent/SKILL.md. Round 1
  accepted F1 (persisted identity/refresh wiring proof missing) and F2
  (execution evidence not yet recorded). F1 is repaired with real published
  checkpoint/child rows, real successor process_once refresh, unrelated
  request dispatch, repeated bootstrap-complete checks and retained exact
  custody; F2 is recorded above. The initial scoped retry was denied permission to read a file and supplied
  no verdict. Round 2b received the exact final test file and execution
  evidence inline, needed no tool reads, and returned SUCCESS / PASS with
  no new defects: F1 and F2 closed. This is an independent assessment of the
  supplied exact source, not a claim that the retry independently read files.
- WT-7 possession probe: owner attests that the existing probe is already
  done, 2026-11-05: "We have already done this, you can pass it." This is
  owner confirmation of the prior probe, not an invented new key or result.
  The separate theory revision plan's historical provenance is unchanged.
- Closeout: independent scoped review passed, required gates passed, and
  the owner confirmed the prior WT-7 probe. Status and index are completed.
  [WT-2a.15] still remains drifted as declared in this plan's scope; no
  scoped-refusal or operator-resolution capability is claimed.
- Additional SQLite slow selection (`python -m pytest -m slow -q -rA`):
  4 passed, 11 live-provider opt-in skips. Together with the fast gate,
  SQLite exercised all 6,671 selected cases across the two commands:
  6,611 passed and 60 explicit skips. Live-provider execution was not enabled.
- Measurement limit: no controlled title-only before/after full-suite timing
  comparison was repeated. The full fast run overlapped the PostgreSQL run
  and contains added regressions, so its duration is not used to infer
  neutral title cost. The actual quiet CPU probe and runtime regressions
  above passed; the author's earlier suite timings are not presented as
  independently reproduced.

- Final focused verification after the test-only identity proof expansion:
  13 SQLite repair cases passed; `./bin/pytest-pg
  tests/core/test_admission_recovery.py` passed all 16 cases in 4.61 seconds.
  The full frozen-runtime gates above preceded only this test expansion;
  production source did not change afterward. Repeated canonical mypy,
  Ruff check/format and document-path checks passed on the final sources.



### Theory-versus-code audit refresh at closeout

Unchanged claims carry forward by reference to the reconciliation plan and
this plan's theory-revision dependency; prior unmeasured verdicts are not
upgraded by reading alone.

| Touched claim | Contract and evidence | Verdict |
|---------------|-----------------------|---------|
| [WT-4.1] actionable queued work wakes the reactor | [MA-1.8]; real idle and admission-blocked source tests on both brokers; 14 measured SQLite launches, 8–25 ms | aligned for the repaired source path |
| [WT-4.3] quiet supervision | [MA-1]; shared idle-clock guard, real live-child regression; 0.035848076 CPU seconds in three seconds after the fix | aligned for the measured supervising case; other quiet cases retain their prior evidence |
| [WT-2a.7] custody survives failure | MANAGER.11; exact reservation survives terminal-write failure, retry converges, wrong ordering fails the mutation control | aligned for the repaired settlement path |
| [WT-2a.13] unknown is not death | [MA-1.6a]; existing no-host, unknown-incarnation and live-worker tests remain green; only positively stale scoped processes settle | aligned by retained contract tests |
| [WT-2a.15] published scoped refusal | [MA-1.8]; D1 and D5 remain outside this implementation | drifted; separate closing plan still required |

## Appendix A — idle-to-launch probe

Run in a scratch directory outside the repository, with stdin redirected
from `/dev/null`. It reports, for each lone submission to an idle Manager,
the delay from acceptance to the Manager's `task_spawned` event, using
broker timestamps on both ends.

```python
import json, random, sys, time
from pathlib import Path

from simplebroker import Queue
from weft.client import WeftClient
from weft.commands import cmd_init

project = Path(sys.argv[1])
if not (project / ".weft").exists():
    cmd_init(project)
client = WeftClient(path=str(project))
client.submit_command(["python", "-c", "pass"]).result(timeout=60)
log = Queue("weft.log.tasks", db_path=str(project / ".weft" / "broker.db"))


def spawned_at(tid: str) -> int | None:
    for body, stamp in log.peek_generator(with_timestamps=True):
        event = json.loads(body)
        if event.get("event") == "task_spawned" and str(event.get("child_tid")) == tid:
            return int(stamp)
    return None


random.seed(7)
delays = []
for _ in range(14):
    time.sleep(2.0 + random.random())
    task = client.submit_command(["python", "-c", "pass"])
    task.result(timeout=60)
    # Read the launch evidence now: TaskMonitor folds and deletes task-log
    # rows on its own cycle, so a read after the loop can miss samples.
    stamp = spawned_at(task.tid)
    if stamp is None:
        sys.exit(f"missing task_spawned row for {task.tid}; probe invalid")
    delays.append(round((stamp - int(task.tid)) / 1e6))
assert len(delays) == 14, delays
print(delays)
```

The probe exits non-zero rather than report fewer than 14 samples. If it
fails on a missing row, rerun it with TaskMonitor disabled for the probe
project (`WEFT_TASK_MONITOR_ENABLED=0`) and say so with the numbers.

Baseline at `44d7f900`: 17 to 949 ms. Expected after Slice 2: one
strategy pass (up to about 100 ms) plus scheduling.

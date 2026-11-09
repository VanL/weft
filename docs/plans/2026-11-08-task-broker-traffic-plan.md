# Task Broker Traffic: One Snapshot per Owner Step

Status: completed
Source specs: docs/specifications/07-System_Invariants.md [OBS.6], [OBS.6a]; docs/specifications/01-Core_Components.md [CC-2.2], [CC-2.4], [CC-2.5]; docs/specifications/02-TaskSpec.md [TS-1] (`cleanup_on_exit`); docs/specifications/05-Message_Flow_and_State.md [MF-5]
Superseded by: none

Class: 5 with hardening. Normative text in [OBS.6a], [CC-2.2] and the
`cleanup_on_exit` notes changes (spec-changing), and the change touches the
durable spine `Consumer -> queues/state log` (risky trigger). Promotion
strategy B: the spec text, code, reciprocal backlinks and tests land in one
change. Plan type: implementation with spec revision. Track E of the
2026-11-08 admission-throughput program; the coordinator owns benchmarks,
cross-family review and landing.

## 1. Goal

Cut the broker writes and statements each one-shot task process (the
`Consumer` wrapper) issues per lifecycle, without losing a lifecycle fact a
reader can observe after an owner step. Fewer wrapper writes contend on
SimpleBroker's global write lock (`UPDATE meta` row lock held across client
round trips on PostgreSQL), send fewer `NOTIFY`s to every listening backend,
and wake the Manager's startup observation less often.

## 2. Source documents and baseline

- Spec baseline: `08c2f87b` (`perf/admission-base`), all of
  `docs/specifications/`.
- Theory: [WT-2a.2] queues are the only durable truth; [WT-2a.6] one arbiter
  per reactor commits that owner's broker effects; [WT-4.1] a wake predicate
  reads facts and guarantees progress; [WT-4.2] process concurrency, short
  transactions; [WT-4.3] a quiet task costs nothing an operator can see;
  [WT-4.4] measure, do not bake in inefficiency, no capability diminution;
  [ALT-WT-002] fixed polling caps; [ALT-WT-010] no new cache subsystem;
  [ALT-WT-019] queues are not unconsumed tables.
- Guidance: `AGENTS.md`, `docs/agent-context/decision-hierarchy.md`
  [DOM-15], `runbooks/writing-plans.md`, `runbooks/hardening-plans.md`,
  test-audit skill.

## 3. Measured evidence (before)

Coordinator measurement, 1,250-task PostgreSQL stress run: wrappers issue
~153 statements and ~17.5 writes per task; 9 `weft.state.tasks.<tid>`
snapshots and 6 `weft.log.tasks` rows per task.

Author probe (scratch instrumentation of `PostgresRunner.run`,
`begin_immediate` and `commit` through a `sitecustomize` hook, not committed):
three roots and one child of `tests.helpers.admission_workload:admission_work`
with 6-second leaves, launched by a real Manager on `./bin/pytest-pg`. Per
wrapper, averaged over 4 wrappers: 97.2 runner-level statements. The largest
groups:

| Call chain | Statements/wrapper | Writes |
| --- | --- | --- |
| `_register_tid_state` (9 snapshots: construction 7, then 4 each) | 39 | 9 |
| `_report_state_change` non-terminal rows (task_initialized, work_spawning, work_started) | 12 | 3 |
| `_emit_activity_event` (2 `task_activity` rows) | 8 | 2 |
| terminal broadcast (`weft.log.tasks` + `ctrl_out`) | 8 | 1 |
| `_has_pending_messages` readiness probes | 6.2 | 0 |
| `_update_active_queues` discovery probes | 3 | 0 |
| outbox result, inbox reserve move, reserved ack, ctrl_in clear | ~11 | 4 |
| `_purge_stream_markers` peeks of outbox and ctrl_out | 2 | 0 |

Snapshot sequence for one one-shot task today: construction (created),
activity waiting (created), activity working (spawning), `work_spawning`
(spawning), `work_started` (running), managed worker PID (running), runtime
handle (running), outcome runtime release (running), terminal. Rows 1-2,
3-5 and 6-7 are each written by one uninterrupted owner step; after each step
only its last row is the current state. Row 8 shares a step with row 9 but
must stay separate: it precedes the result write, and a reader that sees the
result before terminal proof must find the live task process as runtime
authority, not the joined worker
(`tests/commands/test_consuming_result_collection.py::test_live_runtime_outranks_claimed_result_without_terminal_proof`).

## 4. Candidate directions and verdicts

1. **Coalesce edges inside one owner step — implement.** Construction,
   the work-start step, the drain of worker identity events and the outcome
   step each append several complete snapshots in a row with no wait between
   them. Readers select the newest valid row ([OBS.6]); a row superseded
   inside the same step was never the state after a step. Publishing one
   complete snapshot at the end of the step keeps every fact the step's final
   state carries and removes 4 appends per task (rows 1, 3, 4 and usually
   6). An edge whose state readers must see before a later write of the same
   step (row 8, before the result) still appends at the edge. The construction step
   applies the Consumer's initial live activity before its first snapshot, so
   the snapshot that the Manager's startup feedback watches is still the
   task's first broker write. The same rule removes the two `task_activity`
   rows whose change a lifecycle report in the same step already carries
   (`task_initialized` carries `waiting`, `work_spawning` carries `working`).
   Theory: [WT-2a.6] (the step is the arbiter's unit of committed effects),
   [WT-4.2] (fewer, shorter write transactions), [WT-4.4] (measured).
2. **Inactive-queue discovery and readiness probes — explain, propose, do
   not implement.** For a one-shot wrapper the probes are: three
   first-turn discovery probes (needed: the inbox is seeded before the
   waiter exists and an early STOP may already sit in `ctrl_in`), one
   post-dispatch inbox probe, one readiness probe per local hint (worker
   events and result), and one readiness probe per SimpleBroker native
   idle-poll return, about one every 1-2 seconds while work runs. The
   last group dominates long tasks (about 10-15 for the stress workload's
   ~15.6-second average) and is owned by SimpleBroker's strategy cadence
   ([CC-2.2.1], [WT-4.3]: "the strategy, not Weft, owns the pass length");
   [CC-2.1] names that return as evidence-carrying. Removing it in Weft
   would trade a missed-notification safety net for a lost wake ([WT-4.1]).
   The local-hint probe is redundant for an active Consumer, whose turn
   peeks `ctrl_in` itself, but removing it changes shared
   `MultiQueueWatcher` wait logic used by every task for ~3 statements per
   task; it is recorded in section 12 as a proposal. The probe did not
   reproduce the coordinator's 15.0 statements per task from
   `_update_active_queues` (it measured 3); see section 12.
3. **Cleanup peeks of queues the task never wrote — implement for stream
   markers.** `Consumer._purge_stream_markers` peeks outbox and `ctrl_out`
   for empty final stream markers. Only interactive tasks and `stream_output`
   tasks write stream frames; for every other Consumer the scan can find only
   another task's marker in a shared outbox (and then deletes it) or nothing.
   Restrict the scan to the Consumers that write stream frames. Keep
   `_purge_start_tokens`: it reads the task's own reserved queue, where a
   failed exact acknowledgement can leave residue.

## 5. Context and key files

Files to modify:

- `weft/core/tasks/base.py` — `BaseTask` publication path:
  `_initialize_base_task_runtime` (construction snapshot and
  `task_initialized`), `_report_state_change`, `_publish_projection_edge`,
  `_register_tid_state` (the one immediate append), `register_managed_pid`,
  `register_runtime_handle`, `_set_activity`, `_emit_activity_event`.
- `weft/core/tasks/consumer.py` — `Consumer._initialize_consumer_runtime`,
  `_process_reactor_turn`, `_begin_work_item`, `_register_outcome_runtime`,
  `_cleanup_task_resources`.
- `weft/commands/_task_snapshot_reducer.py` — `reduce_task_event` activity
  fold.
- Specs: `docs/specifications/07-System_Invariants.md` [OBS.6a] and OBS.6
  implementation mapping; `docs/specifications/01-Core_Components.md`
  [CC-2.2]; `docs/specifications/02-TaskSpec.md` `cleanup_on_exit` notes.
- Tests: `tests/tasks/test_task_observability.py` (lifecycle publication),
  `tests/commands/test_task_snapshot_reducer.py` (activity fold),
  `tests/tasks/test_task_execution.py` (cleanup scope).

Current structure:

- `_register_tid_state()` builds the complete mapping and appends it once
  (best effort). Edge detection lives at its call sites ([OBS.6a]).
- `_publish_projection_edge()` appends after every non-poll, non-child
  lifecycle report and latches `_terminal_tid_state_published` from the
  return value of the terminal append.
- `Manager._register_tid_state()` overrides the append and relies on its
  synchronous `bool` (scheduler checkpoint custody); the Manager is a
  `ServiceTask`, never a `Consumer`, and keeps calling the immediate append.
- `Consumer` runs work on a worker lane. Its reactor turn drains worker
  results (worker PID, runtime handle, stream chunks, the outcome), polls
  `ctrl_in` while work is active, and otherwise drains queues; signal and
  parent-loss requests are applied by `BaseTask.process_once` before the
  turn hook.
- The Manager watches each starting task's `weft.state.tasks.<tid>` and
  hands launch custody off on the first valid snapshot that carries the
  task's lineage and scoped host-process identity ([MA-1.8], [OBS.6]).

Shared path — do not duplicate: `_register_tid_state()` stays the only
append; the step only decides when it is called.

## 6. Invariants and constraints

- Every lifecycle transition still writes its `weft.log.tasks` row
  (`task_initialized`, `work_spawning`, `work_started`, terminal events);
  state stays forward-only ([STATE.1], [STATE.2]).
- The completion broadcast precedes the terminal mapping; the terminal
  mapping is appended exactly once per task, latched only on a successful
  terminal append, and retried on the next terminal report after a failure
  ([OBS.6a]).
- The construction snapshot remains the task's first broker write and
  carries the same lineage and host-process evidence ([MA-1.8] handoff).
- No read-before-write, no payload comparison, no new queue, no new
  persistent state ([OBS.6a], [ALT-WT-010], [ALT-WT-019]).
- `Manager`, services, pipelines and other `BaseTask` families keep
  immediate per-edge publication: only `Consumer` opens coalescing steps.
- A broker failure in an append stays best effort; payload construction
  defects stay visible ([OBS.6a]).
- No SimpleBroker change; no change to the watcher's wait logic.

Review gates: no second publication path; no mock substitute for the real
broker in the lifecycle proofs; no drive-by refactor.

Comprehension questions (answer before editing):

1. Which row does the Manager's startup handoff read, and what must it
   carry? Expected: the first valid `weft.state.tasks.<tid>` row after the
   launch; it must carry `context`, `root_tid`, `submitter_tid`,
   `ancestry_tids`, `scheduling_class`, `connection_owner` and the scoped
   `host_processes` with create times (`Manager._snapshot_hands_off_launch`).
2. Why can't the Manager's appends be deferred? Expected:
   `_publish_scheduler_checkpoint` needs the synchronous success of the
   append to decide custody durability; it calls the immediate append, and
   the Manager never opens a step.

## 7. Difference register

| Change | Class | Theory basis |
| --- | --- | --- |
| A one-shot Consumer appends 5 (sometimes 4 or 6) snapshots per lifecycle instead of 9; rows superseded inside one owner step (created-without-activity, the `working` activity edge, spawning, a second worker-identity row from the same drain) are no longer appended; the runtime release still precedes the result | C | [WT-2a.6], [OBS.6] newest-valid readers; the state after every step is published |
| The construction snapshot carries `activity=waiting`, `waiting_on=<inbox>` and display event `task_initialized` (the newest row after construction previously said `task_activity`) | C | Same live facts; the display event names the report the row follows |
| `task_initialized` and `work_spawning` rows carry the activity that the removed `task_activity` rows carried; `task_activity` rows remain for activity changes no same-step report carries and for every non-Consumer task | C | [WT-4.4]; log fold yields the same activity and waiting_on (with the reducer rule below) |
| Log fold: a row that carries `activity` sets both `activity` and `waiting_on` (absent means none) | N | Producers build both from one state; no current producer emits `waiting_on` without `activity` |
| Consumer process title goes straight to `created:waiting` (the transient `init` title is skipped) | C | Process-local display; same steady title |
| Within a Consumer step (a work-item start, or one drain of worker results), a held snapshot becomes visible at the step's end or at an earlier barrier rather than at its edge. Steps exclude interactive session work (whose input write can block) and agent-session start; a barrier appends before any blocking call (agent-session close) and before any write that must follow the snapshot, so no edge waits behind a blocking operation | C | [WT-2a.6]; [WT-4.1] no held fact behind a wait |
| The runtime authority an outcome carries is published before the result is written, for every runner: a released host worker names the task process again; a handle supplied with the outcome (or identity drained with it) is appended first. Unchanged from the baseline order; review finding F1 | N | [OBS.11a]; [WT-2a.13] |
| The work-start snapshot is appended before the worker lane starts, so it always names the task process; worker identity follows in its own drain step | N | [WT-2a.6] |
| Persistent work-item completion appends the host runtime release, then one final-state row (`waiting`, `work_item_completed`); the `waiting` activity becomes visible with the completion row instead of before it (display only; no reader acts on activity) | C | [WT-4.4] |
| Terminal mapping latch is set by any successful terminal append (step flush, or a late runtime-handle append) instead of only by the terminal report's own append | N | [OBS.6a] "exactly once per task" |
| A non-interactive, non-`stream_output` Consumer no longer scans outbox and `ctrl_out` at cleanup; it no longer deletes another task's empty final stream marker in a shared outbox | C | `cleanup_on_exit` deletes task-owned scratch artifacts only ([TS-1]) |

No row is D.

## 8. Proposed Spec Delta

Promotion strategy B (atomic):

| Spec file | Strategy | Sections touched |
| --- | --- | --- |
| `docs/specifications/07-System_Invariants.md` | B | [OBS.6a] paragraph; OBS.6 implementation mapping |
| `docs/specifications/01-Core_Components.md` | B | [CC-2.2] paragraph after "Call sites decide whether" |
| `docs/specifications/02-TaskSpec.md` | B | `cleanup_on_exit` implementation note |

### [OBS.6a] — insert after "...or when the write is itself a report (construction, restart republish)."

> A task may coalesce the edges it detects within one owner step: inside a
> `Consumer` work-item start and inside each drain of its worker results,
> each edge marks the snapshot stale, and the step's end appends one complete
> snapshot, so every fact of the state after the step is published once and
> a state superseded within the same step is not appended separately. Any
> complete append made inside a step covers the edges detected before it. A
> step holds no edge across an operation that can block or across a write
> that readers must see after the snapshot: it appends first. A result or
> stream frame follows the runtime authority it was produced under, for
> every runner (a joined one-shot host worker is released, naming the live
> task process again). Interactive session work does not coalesce. A task's
> construction snapshot carries the live activity the task starts with.
> Other task families append at each edge.

(Revised after review round 1, F1 and F2; the round-1 text coalesced a whole
`Consumer` reactor turn.)

and replace "The terminal transition publishes its mapping exactly once per
task, latched only on a successful write" with "The terminal transition
publishes its mapping exactly once per task, latched only on a successful
terminal append".

### OBS.6 `_Implementation mapping_` — append

> `weft/core/tasks/base.py::BaseTask._task_state_step`,
> `weft/core/tasks/base.py::BaseTask._task_state_barrier`,
> `weft/core/tasks/consumer.py::Consumer._begin_work_item` and
> `weft/core/tasks/consumer.py::Consumer._drain_worker_results` own
> owner-step coalescing ([OBS.6a]).

### [CC-2.2] — insert after "registration itself performs one append attempt and does not inspect global mapping history."

> Inside an owner step that coalesces publication ([OBS.6a]) an edge marks
> the snapshot stale and the step's end makes the one append. A lifecycle row
> in `weft.log.tasks` carries the live `activity` and `waiting_on` current at
> the report, taken from one state. A separate `task_activity` row records an
> activity change that no lifecycle row of the same step carries; outside a
> coalescing step every activity change writes its row. A reader folding the
> log takes `activity` and `waiting_on` together from any row that carries
> `activity`, an absent `waiting_on` meaning none.

### [TS-1] `cleanup_on_exit` note — replace "`Consumer._cleanup_task_resources()` purges start tokens and stream markers;"

> `Consumer._cleanup_task_resources()` purges start tokens from its reserved
> queue and, for an interactive or `stream_output` task (the Consumers that
> write stream frames), empty final stream markers from outbox and
> `ctrl_out`;

## 9. Tasks and stop gates

1. RED tests (section 10) on the baseline; record each failure.
2. `BaseTask` step: add `_task_state_step()` (context manager, nested steps
   join the outermost), `_task_state_edge()` (marks stale inside a step,
   else calls `_register_tid_state()`), move the terminal latch into
   `_register_tid_state()` (set when the appended mapping is terminal),
   clear the stale mark on every successful append. Route
   `_publish_projection_edge`, `_set_activity`, `register_managed_pid`,
   `register_runtime_handle` through `_task_state_edge()`. Inside a step
   `_set_activity` defers its `task_activity` row; `_report_state_change`
   drops a deferred row when its payload carries `activity`; the step's end
   writes a still-deferred row (non-terminal) before the snapshot.
   Stop if the Manager or a service would enter a step, or if any caller
   needs a synchronous result from an edge.
3. Initial activity: `BaseTask._initial_activity()` hook (default `None`);
   `_initialize_base_task_runtime` applies it before the construction
   snapshot and titles the process with the current status when it applies.
   `Consumer` returns `("waiting", inbox)` and no longer calls
   `_set_activity("waiting")` in its constructor.
4. `Consumer`: open the step in `_begin_work_item` (it ends before the
   worker lane starts) and in an override of `_drain_worker_results` (one
   batch of worker results). Do not coalesce the reactor turn: a turn can
   hold interactive session input (`session.send()` may block on a full
   pipe), output drains, agent-session start and other blocking calls.
   Add `BaseTask._task_state_barrier()` (append what the open step holds)
   and call it at the end of `_register_outcome_runtime` for every runner
   (the result follows its runtime authority), before each live stream
   frame, and before closing an agent session. Do not override
   `_process_stopping_reactor_turn`: every family shares BaseTask's
   stopping turn
   (`tests/tasks/test_task_execution.py::test_stopping_turn_policy_is_shared_by_all_concrete_task_families`).
   Stop if a step would contain a call that can block with an edge held.
5. `Consumer._cleanup_task_resources`: purge stream markers only when
   `spec.interactive or spec.stream_output` (and not live command
   streaming, unchanged).
6. Reducer: `reduce_task_event` takes `waiting_on` with `activity`.
7. Spec delta (section 8), backlinks, docstring `Spec:` references.

## 10. Testing plan

Real broker, real `Consumer`, real host runner; no mocks of queues or
publication. `broker_env`/`task_factory` fixtures (`tests/conftest.py`).

- `tests/tasks/test_task_observability.py`: a one-shot function task blocked
  on a release file (`tests.tasks.sample_targets:wait_for_file`) is driven
  with `process_once`/`wait_for_activity` until its worker is running, then
  released and driven to completion. Assert on the task's own queues:
  - log rows for the TID are exactly `task_initialized`, `work_spawning`,
    `work_started`, `work_completed`; `task_initialized` carries
    `activity=waiting` and `waiting_on=<inbox>`; `work_spawning` and
    `work_started` carry `activity=working` and no `waiting_on`;
  - the first snapshot is `created`/`task_initialized` with
    `waiting`/`<inbox>`; the second is `running`/`work_started`/`working`;
    no snapshot reports `spawning`; the next-to-last row names the task
    process as runtime authority (release before the result); the last is
    terminal `completed`; the total is at most 6 (it is 9 today).
  RED on baseline: first snapshot has no activity; `task_activity` rows
  present.
- Same file: a persistent Consumer that completes one work item writes no
  `task_activity` row for it and appends, in order: the start row (task
  process authority), any worker-identity rows (worker authority), the
  host runtime release (task process, still `working`), and exactly one
  final-state row (`waiting`, `work_item_completed`). Duplicate release or
  final appends fail it (review F3).
- Same file: worker identity and the outcome are published to the
  worker-result channel and applied in one real turn; the runtime authority
  in the newest snapshot when the result is written is the task process
  (reported host worker, released) or the outcome's handle (authority only
  in the outcome). RED on `6a3230ae` for the second case (review F1).
- `tests/tasks/test_task_interactive.py`: when the first input is written to
  a started interactive session, the newest snapshot already reports
  `running` with the session process in its host PIDs. RED on `6a3230ae`
  (still `created`) (review F2).
- `tests/commands/test_task_snapshot_reducer.py`: folding `task_initialized`
  (`waiting`, inbox) then `work_spawning` (`working`, no `waiting_on`)
  yields `waiting_on=None`. RED on baseline (keeps the inbox).
- Cleanup scope: a non-streaming one-shot Consumer whose outbox already
  holds another producer's empty final stream marker leaves that marker in
  place after cleanup. RED on baseline (deleted).
- Changed oracle (test, not product, was stale):
  `tests/commands/test_retained_result_history.py::_finish_and_retire_history`
  retained `task_activity` rows as its "nonterminal history"; a Consumer no
  longer writes them, so the helper now retains the nonterminal rows that
  carry live activity, keeping the retained-history scenario non-empty.
- Existing owners rerun: `tests/tasks/test_task_observability.py`,
  `test_consumer_terminal_events.py`, `test_terminal_event_retry.py`,
  `test_local_completion_publication.py`, `test_runtime_identity_custody.py`,
  `test_runtime_identity_signals.py`, `test_task_execution.py`,
  `test_control_channel.py`, `test_task_interactive.py`,
  `test_reserved_disposition.py`, `test_pipeline_runtime.py`,
  `tests/commands/test_task_snapshot_reducer.py`, `test_status.py`,
  `test_result.py`, `test_retained_result_history.py`,
  `tests/core/test_task_state.py`, `tests/core/test_manager.py`,
  `tests/core/test_admission_recovery.py`, `tests/system/test_child_admission.py`.

## 11. Verification, rollout and rollback

```bash
./.venv/bin/python -m pytest <targets> -n 4
./bin/pytest-pg <targets>
./.venv/bin/ruff check . && ./.venv/bin/ruff format --check <touched files>
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/python bin/check-doc-paths
```

Observable success after deploy: per-task rows in `weft.state.tasks.<tid>`
drop from 9 to 4-5 and `task_activity` rows disappear for one-shot Consumers;
the coordinator's stress benchmark should show fewer wrapper writes per task
(~17.5 to ~11-12) and lower Manager write-lock wait.

Rollout: no queue name, payload schema, or persisted format changes; old and
new tasks interoperate (readers take the newest valid row and fold activity
from any row). Rollback: revert the commit; no data migration.

## 12. Out of scope and proposals

- Not changed: SimpleBroker; `MultiQueueWatcher` wait logic; Manager,
  service, pipeline publication; work_spawning/work_started both remain
  rows; `_purge_start_tokens`; `ctrl_in` clear at exit.
- Proposal A (watcher, ~3 statements/task): on a local hint with a healthy
  native waiter and no native or data-version hint, skip the readiness probe
  when the owner's turn reads the same queues itself (active Consumer). Needs
  a [CC-2.1] edit; owner decision.
- Proposal B (SimpleBroker-owned): the native idle-poll cadence (1-2 s)
  produces ~1 `ctrl_in` probe per second of work per task; any change belongs
  to SimpleBroker's strategy, not a Weft cap ([ALT-WT-002]).
- Open measurement: the coordinator's 15.0 statements/task from
  `_update_active_queues` did not reproduce for a launched one-shot wrapper
  (3 per task). Candidates: attribution of service-task processes
  (TaskMonitor, LivenessMonitor, Heartbeat) to wrappers, or a lifecycle
  shape the probe did not exercise.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|
| [OBS.6a] | Coalesce the outcome runtime release (row 8) into the terminal snapshot | Row 8 stays an immediate append before the result | Found by the existing result-observation test: readers evaluate runtime authority between the result write and terminal proof | Promoted in [OBS.6a] ("an edge whose state readers must see before a later write of the same step appends at the edge") |
| [CC-2.2.1], [IMPL.10] | Consumer overrides `_process_stopping_reactor_turn` with a step | No override; stopping turns publish at each edge | Every family shares BaseTask's stopping turn by contract test | None needed |
| [OBS.6a] | Round 1: one step per Consumer reactor turn | Steps are the work-item start and each worker-result drain, with barriers before results, stream frames and agent-session close; interactive work never coalesces | Review F1 (non-host authority published after the result) and F2 (a turn contains blocking interactive input and output drains) | Promoted: [OBS.6a] text revised in this change |

## Review and execution record

Execution evidence (author, before the coordinator's review):

- Comprehension answers recorded before the first code edit match section 6.

- RED on `08c2f87b`, each for the intended reason: the one-shot lifecycle
  test (first snapshot had no activity), the persistent completion test
  (`task_activity` rows present), the reducer test (`waiting_on` stayed the
  inbox), the cleanup-scope test (the foreign marker was deleted).
- Same scratch probe after the change (PostgreSQL, 4 launched wrappers):
  19 write transactions and ~97 statements per wrapper before; 12-13 write
  transactions and 67-71 statements after. Snapshots 9 -> 4-5, log rows
  6 -> 4, cleanup stream-marker peeks 2 -> 0.
- After the round-1 fixes (same probe): 13 write transactions and 70-71
  statements per wrapper; 5 snapshots (construction, work start, worker
  identity, runtime release, terminal), 4 log rows.
- Round-1 RED evidence on `6a3230ae` sources (new tests run against them,
  sources then restored byte-for-byte): authority-only-in-outcome case
  `assert ['<task pid>'] == ['424242']` (the outcome's authority was not yet
  published when the result was written); interactive first input
  `assert 'created' == 'running'`.

The class-5/hardening review-before-implementation gate was not run by the
author: the track brief assigns plan and work review to the coordinator after
implementation.

### Review round 1

Tier 1, Codex (codex exec -s read-only, reasoning effort high), 2026-11-08,
attempt 1 failed (model at capacity), attempt 2 scoped-change review of
plan+implementation at 6a3230ae against 08c2f87b. Verdict: blocker: F1, F2.

| ID | Severity | Finding | Disposition |
| --- | --- | --- | --- |
| F1 | P2 | Non-host runtime authority could publish after the result: `_register_outcome_runtime()` deferred its handle edge inside the turn step while `_commit_work_outcome()` wrote the output first (identity and outcome in one drain, or a handle supplied only with the outcome) | Accepted, fixed: `BaseTask._task_state_barrier()` appends what a step holds; `_register_outcome_runtime` ends with it for every runner; live stream frames and agent-session close are preceded by it; [OBS.6a] says a result or stream frame follows the runtime authority it was produced under. New parametrized test `test_outcome_runtime_authority_is_visible_when_the_result_is` (RED on 6a3230ae for the authority-only-in-outcome case). Register row added |
| F2 | P2 | The coalescing scope (the whole reactor turn) included blocking interactive work: session identity and `running` were held across `session.send()` and output drains, so the register's "milliseconds; no wait occurs" premise was false | Accepted, fixed: the reactor turn no longer opens a step; steps are `_begin_work_item` (ends before the worker lane starts) and Consumer's `_drain_worker_results` (interactive work never uses worker lanes); barrier before agent-session close. Register row corrected. New test `test_interactive_session_identity_is_published_before_first_input` (RED on 6a3230ae: snapshot still `created` at the first input) |
| F3 | P3 | The persistent completion test checked only the newest snapshot, so duplicate appends passed; the host release means persistent completion appends release plus final state | Accepted, fixed: the test asserts the whole row sequence (start, worker identity only with worker authority, one release, exactly one final-state row); plan counts aligned (section 7). On 6a3230ae it also failed intermittently under -n 4, because the turn step merged the start row with worker identity |

### Cross-family review, round 2

Tier 1, Codex (codex exec -s read-only, reasoning effort high), 2026-11-08,
round-2 verification scoped to F1 and F2 over `6a3230ae..585f293d`.
Verdict: PASS, no new defect in the fix delta. The reviewer confirmed the
pre-result barrier covers both the host-worker release and the generic handle
path, that interactive setup, `send()` and output drains run outside every
coalescing step, and, by tracing 6a3230ae, that both new tests would fail on
the round-1 code.

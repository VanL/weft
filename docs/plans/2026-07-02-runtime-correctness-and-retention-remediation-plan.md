# Runtime Correctness and Retention Remediation Plan

Status: draft
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-2], [MF-3], [MF-5], (Cleanup Boundary); docs/specifications/07-System_Invariants.md [STATE.1], [QUEUE.6], [OBS.1], [OBS.13.3], [OBS.13.5], [OBS.13.7]; docs/specifications/06-Resource_Management.md [RM-5.1]; docs/specifications/13-Agent_Runtime.md [AR-2.1]; doc-only tasks that fix summary drift cite no spec and say so inline
Superseded by: none

## Goal

Fix the confirmed runtime-correctness and retention defects found in the
2026-07-02 project evaluation. Two clusters:

**A. Task signal/control correctness** (durable spine):

1. `BaseTask`/`Consumer` termination-signal handlers do broker work inline
   inside the Python signal handler — self-deadlock and
   transaction-reentrancy risk. The Manager already defers signals to
   in-memory state for exactly this reason (`weft/core/manager.py:3528`
   docstring); tasks never got the same fix.
2. A deferred STOP/KILL is silently dropped when the active work item
   completes with outcome `ok`: the control message was already acked at
   deferral time, and `_finalize_deferred_active_control` is only invoked
   from non-ok outcome branches. A persistent consumer can exit with status
   still `running` and no terminal event — violating exactly-once terminal
   emission ([STATE.1], [OBS.1]).
3. The CLI fallback stop/kill path signals raw PIDs from tid-mapping entries
   with no create-time verification — PID reuse can kill an unrelated
   process tree. The guarded path already exists and is used by the Manager.
4. Terminal-state write failures are logged at debug and dropped; combined
   with `ctrl_out` deletion during cleanup, a successful task can later be
   reported `failed` by the wrapper-lost fallback.

**B. Monitor/pruning retention safety** (data loss on long-running tasks):

5. **Keystone:** the TaskMonitor's tid-mapping cleanup deletes the *latest*
   mapping row of live plain tasks after ~40 minutes
   (`TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS`), and nothing
   refreshes it. That row is the liveness evidence consumed by endpoint
   resolution, runtime pruning, manager kill-pid resolution, and the
   monitor's own destructive-slice safety checks.
6. Downstream of 5: hourly maintenance pruning deletes **active** endpoint
   and streaming rows of live long-running tasks.
7. `stale_open` disposal (7-day silent open family) has no runtime-liveness
   check on its branch, so a live quiet task's inbox/ctrl/outbox queues can
   be deleted while the process runs.
8. Outbox retention eligibility is computed from TID *creation* time, not
   completion time — a task that runs longer than the retention window loses
   its result roughly one monitor cycle after completing.
9. The retention-prune archive (the only pre-delete copy of deleted
   payloads) is opened with `"w"`, so a same-day rerun truncates the
   previous run's recovery record.
10. Store-backed reserved cleanup has no age gate — a failed task's kept
    reserved work item (`ReservedPolicy.KEEP`, [QUEUE.6]) can be deleted
    minutes after failure, breaking the documented
    `weft queue peek T{tid}.reserved` debug flow.
11. Spec drift: `05` (Cleanup Boundary) and `07` [OBS.13] intro text still
    describe summary-gated/age-gated raw task-log deletion; the shipped
    default deletes raw rows at ingest per [OBS.13.3]. Also,
    `weft system retention-prune` is largely inert under the default
    collation mode and does not say so.

**C. Documentation honesty** (no behavior change):

12. Document the resource-limit enforcement model honestly (poll cadence,
    `setsid`/double-fork escape) and the provider-CLI agent default
    (`authority_class="general"` implies `--dangerously-skip-permissions`);
    add a profile-validation honesty note to the macOS sandbox README.
13. Fix the drifted invariant summaries in `CLAUDE.md`/`AGENTS.md` §3/§4.4
    (missing `spawning`/`cancelled` states; stale `DEFAULT_MEMORY_MB`
    example).

Each numbered finding maps to one task below. Every task is a separate
commit so any single change can be reverted alone. Behavior fixes come
before spec/doc reconciliation within each phase.

This plan complements, and does not duplicate,
[`2026-06-09-evaluation-findings-remediation-plan.md`](./2026-06-09-evaluation-findings-remediation-plan.md)
(spill-file permissions, macOS sandbox env passthrough, README/Quick-Ref
command-table gaps, style drift). If that plan lands first, skip any doc
line it already fixed; do not re-fix.

## Source Documents

Read these before starting, in this order:

- `CLAUDE.md` (repo root): §1.1 design philosophy, §3 invariants, §4 house
  style, §5 dev commands, §8 agent boundaries.
- `docs/agent-context/decision-hierarchy.md`: specs outrank plans.
- `docs/agent-context/engineering-principles.md`: boundary validation,
  real-broker tests over mocks, traceability rules.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-2], [MF-3], [MF-5],
  and the Cleanup Boundary section (line ~992): message reservation,
  control flow, state observation, and what the Monitor may delete.
- `docs/specifications/07-System_Invariants.md` [STATE.1] (line ~55),
  [QUEUE.6] (line ~98), [OBS.1] (line ~138), [OBS.13.3/.5/.7]
  (lines ~200-252).
- `docs/specifications/06-Resource_Management.md` [RM-5.1] (Task C1 only).
- `docs/specifications/13-Agent_Runtime.md` [AR-2.1] (Task C1 only).

Comprehension check before editing (hardening runbook §14). You should be
able to answer these from the reading and the named code:

1. Which queue proves a task reached a terminal state, and which component
   is the only one allowed to delete rows from it? (`weft.log.tasks`; the
   TaskMonitor per [OBS.13.3].)
2. Why must a Python signal handler in a broker-connected process avoid
   queue writes? (SimpleBroker's runner lock is non-reentrant and its
   transactions span multiple Python calls; see the Manager docstring at
   `weft/core/manager.py:3528`.)
3. What does `ReservedPolicy.KEEP` promise, and which invariant states it?
   ([QUEUE.6]: the message stays in `T{tid}.reserved` for inspection.)
4. Where does the liveness evidence for a plain (non-service) running task
   live? (`weft.state.tid_mappings` — one row written at startup by
   `BaseTask._register_tid_mapping`, refreshed only on activity
   *transitions*.)

## Context and Key Files

Current structure (read before editing; do not infer):

- `weft/core/launcher.py` — spawn-side entry. `_handle_signal` (line ~208)
  is installed for SIGTERM/SIGINT and calls
  `task.handle_termination_signal(signum)` **inline in the signal frame**.
  The run loop at line ~124 already polls `_poll_active_control_once` and
  checks `should_stop`.
- `weft/core/tasks/base.py` — `BaseTask.handle_termination_signal`
  (line ~1290) does psutil kills, `mark_cancelled/killed`,
  `_report_state_change` (queue write, line ~1226, failures logged at
  debug), `_send_terminal_envelope` (queue write, line ~1281, same), and
  reserved-queue policy application. `_register_tid_mapping` (line ~1522)
  skips equivalent rewrites; `_set_activity` (line ~1610) re-registers only
  on activity transitions.
- `weft/core/tasks/consumer.py` — `_poll_active_control_once` (line ~567)
  defers STOP/KILL during active work and **acks the control message
  immediately**; `_finalize_deferred_active_control` (line ~664) applies
  the deferred command and is called only from the non-ok branches of
  `_ensure_outcome_ok` (lines ~982-1085); the ok path
  (`_commit_work_outcome` → `_finalize_message`) never calls it.
- `weft/core/manager.py` — the reference pattern for deferred signal
  handling (docstring at line ~3528: "Keep this handler to plain in-memory
  state"); the guarded PID path `_managed_pids_for_child` /
  create-time-verified kill (line ~5879-5900) via
  `weft.helpers.pid_matches_create_time` (helpers/__init__.py:97).
- `weft/commands/tasks.py` — CLI fallback paths `_stop_via_fallback`
  (line ~1376), `_kill_via_fallback` (line ~1407),
  `_force_kill_task_processes` (line ~1434) all consume
  `_host_pids_from_mapping` (line ~1168), which returns raw PIDs with no
  create-time check. `weft/ext.py:87` (`scoped_host_processes`) shows the
  handle carries `(pid, create_time)` pairs.
- `weft/core/monitor/policies/tid_mapping.py` — `tid_mapping_candidates`
  (line ~71) selects every row older than min-age via
  `older_than_candidates`; no keep-latest grouping, no liveness probe.
  Contrast the foreground prune in `weft/core/pruning/runtime.py`
  (tid-mappings group, line ~522-557), which keeps the newest row per
  `full` key.
- `weft/core/monitor/store.py` — `_stale_open` (line ~2632) classifies
  7-day-quiet open families; the `stale_open` close reason is attached at
  line ~998-1007 with no liveness check (the `stale_service_owner` branch
  in `task_monitor.py:3041-3091` *does* check `_active_runtime_tids`).
- `weft/core/monitor/task_monitor.py` — destructive slices; delete-time
  liveness check at line ~3376 consults `_active_runtime_tids`
  (line ~3003-3039), which is fed by... the tid-mapping rows that
  finding 5 deletes.
- `weft/core/monitor/policies/runtime_control.py` (line ~233) and
  `policies/dead_task.py` (line ~110) — `retention_eligible =
  is_old_enough(int(tid), ...)`: creation-based, not completion-based.
- `weft/core/monitor/sql.py` — `select_reserved_cleanup_pending_tasks`
  (line ~685): no age cutoff.
- `weft/core/pruning/retention.py` — `_write_records` (line ~1256) opens
  the archive with `"w"`; `_append_summary_record` (line ~1284) already
  uses `"a"`.
- `weft/_constants.py` — all constants live here.
  `TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS` (line ~1244) is
  `STATUS_RUNTIMELESS_STALE_AFTER_SECONDS * 2`.
- Tests: `tests/helpers/weft_harness.py` (`WeftTestHarness`),
  `tests/tasks/test_task_execution.py` (deferred-control coverage at
  ~381-563 covers timeout/limit outcomes only),
  `tests/core/test_task_monitor_cleanup.py`
  (`test_task_monitor_cleanup_deletes_old_tid_mapping` **asserts the
  finding-5 defect as intended behavior and must be rewritten, not
  appeased**).

Style and guidance: `CLAUDE.md` §4 (typing, constants, docstrings with spec
refs, pragma'd defensive catches, named sleep constants).

Shared paths — do not duplicate:

- PID liveness: `weft.helpers.pid_matches_create_time` and the handle's
  `scoped_host_processes`. Do not write a new PID-verification helper.
- Keep-latest grouping: the foreground tid-mappings prune in
  `weft/core/pruning/runtime.py` is the semantic reference for Task B1.
- Deferred-command finalization: `_finalize_deferred_active_control` is the
  single finalizer; Task A2 adds a call site, not a second implementation.

## Invariants and Constraints

Must not change:

- TID format and immutability; forward-only transitions [STATE.1]; terminal
  events emitted exactly once per task [OBS.1].
- Reserved-queue policy semantics [QUEUE.6]: `keep` means the row stays
  until an age-gated, evidence-gated cleanup — never minutes after failure.
- `spec`/`io` immutability; spawn context for broker-connected processes.
- Queue names, message payload shapes, and the CLI surface. No new queues.
- The Monitor remains the only deleter of raw `weft.log.tasks` rows
  [OBS.13.3]; Monitor cleanup must not delete active work [OBS.13.7].
- Layering: `cli → commands → core` one-way (enforced by
  `tests/architecture/test_import_boundaries.py`). Task A3's shared helper
  must live in `weft/helpers` or `weft/core`, importable by `commands`.

Hidden couplings to keep in mind:

- `_active_runtime_tids` (monitor) ← `weft.state.tid_mappings` rows ←
  `BaseTask._register_tid_mapping` write-once behavior. Task B1 changes the
  cleanup side; do NOT also add periodic re-registration (that is a second
  writer path — YAGNI, and it would mask the policy fix).
- Terminal-status evidence for pruning comes from raw `weft.log.tasks`,
  which the default monitor drains at ingest. Any liveness predicate that
  falls back to "no status row ⇒ unknown" must treat unknown as *live*, not
  dead, for destructive decisions.
- Signal-handler deferral (A1) interacts with wait loops: the run loop's
  existing bounded poll intervals are what guarantee the deferred flag is
  observed promptly. Do not add a new wake mechanism.

Error-path priorities: corrupt state transitions or reservation behavior
are fatal; observability output is best-effort; **terminal-event writes are
promoted by Task A4 from best-effort to retried-and-warned** (still not
fatal — a task that cannot write its terminal event must still exit).

Review gates for every task: no new execution path; no drive-by refactor;
no new dependency; no public CLI shape change; no mock substituted for a
real broker/process proof where the harness is practical; fresh-eyes
self-review before implementation; external review required for Phase A
and B (reason: durable-spine and destructive-cleanup changes).

Rollback: every task is one revertible commit with no persisted-format
change (external review confirmed Task B4 needs no schema change — the
collation record already carries `terminal_message_id`). No rollout
ordering between tasks except as stated in Tasks; within Phase B, B1 must
land before B2 (stale_open gating consumes the liveness evidence B1 stops
deleting); the rest are independent.

One-way doors: none intentionally. If any task starts wanting a queue-name,
payload, or non-additive schema change, stop and re-plan.

## Tasks

### Phase A — signal/control correctness

1. **Defer task termination-signal handling out of the signal frame.**
   - Outcome: SIGTERM/SIGINT on a task process records the signal in plain
     in-memory state; the run loop performs the existing termination work
     (kills, terminal transition, envelope, reserved policy) outside the
     signal context. No broker call ever executes inside the handler.
   - Files to touch: `weft/core/launcher.py`,
     `weft/core/tasks/base.py`, `weft/core/tasks/consumer.py`,
     `tests/tasks/test_task_execution.py` (and/or a new
     `tests/tasks/test_signal_deferral.py`).
   - Read first: `weft/core/manager.py:3505-3545` (the reference deferred
     pattern and its docstring); `weft/core/launcher.py:100-230` (run loop
     and handler install); [MF-3].
   - Approach: in `_handle_signal`, only set e.g.
     `task.note_termination_signal(signum)` (new small method storing the
     signum on the instance; last signal wins, KILL-class outranks
     STOP-class). At the top of each run-loop iteration (and after any
     bounded wait returns), if a signum is pending, call the existing
     `handle_termination_signal(signum)` and proceed to shutdown exactly as
     today. Mirror the Manager's docstring wording so the constraint is
     stated at the handler.
   - Reuse: the existing `handle_termination_signal` bodies unchanged; the
     loop's existing bounded wait intervals (do not add new sleeps).
   - Constraints: no new thread, no signalfd/self-pipe machinery, no
     changes to which signals are installed. Latency of one poll interval
     between signal and termination work is acceptable and should be
     asserted loosely (deadline-bounded), not exactly.
   - Tests: with `WeftTestHarness`, start a real consumer running a
     long-running subprocess work item; send SIGTERM to the task process;
     assert (a) exactly one terminal event (`cancelled`/`killed` per
     current semantics) in `weft.log.tasks`, (b) reserved policy applied,
     (c) process exits within a bounded deadline. Add a regression that the
     handler itself performs no broker work: assert via the new
     `note_termination_signal` seam that the handler only records state
     (unit-level), plus the end-to-end proof above. Do not mock queues.
   - Also verify: the evaluation's "signal-during-commit leaves a full
     result in a cancelled task's outbox" scenario is closed by this task
     (termination now happens at loop boundary, never between worker
     completion and commit). Add a test only if it can be made
     deterministic via the deferral seam; otherwise state in the test
     module docstring why the race is structurally excluded.
   - Stop if: the fix wants to change `handle_termination_signal`'s
     semantics or the shutdown ordering — that is out of scope; only the
     *call site* moves.
   - Done when: new tests pass; existing signal/lifecycle tests in
     `tests/tasks/` stay green.

2. **Finalize deferred STOP/KILL on `ok` outcomes.**
   - Outcome: a STOP/KILL deferred during active work is honored when the
     work completes successfully: result committed first, then
     `_finalize_deferred_active_control()` runs (terminal transition,
     envelope, ack semantics exactly as the existing non-ok call sites
     produce). A persistent consumer never exits leaving status `running`.
   - Files to touch: `weft/core/tasks/consumer.py`,
     `tests/tasks/test_task_execution.py`.
   - Read first: `consumer.py:560-730` (defer + finalize),
     `consumer.py:269-292` and `783-839` (ok path), the deferred-control
     tests at `tests/tasks/test_task_execution.py:381-563`; [MF-3],
     [STATE.1], [OBS.1].
   - Approach: add the call on the ok path after the result is emitted and
     the work message finalized (so the result is never lost). Two hard
     placement constraints (both from external review):
     (a) the call must sit **inside the active-work path, before the
     `finally` block of `_handle_active_work_result` clears
     `_active_message_timestamp`** (consumer.py:466) — the finalizer
     depends on that context; a call after cleanup silently loses the
     reserved-message target. Place it at the end of the ok branch of
     `_handle_active_work_result` (after `_commit_work_outcome` /
     `_finalize_message`), and say so in the docstring.
     (b) **Reserved disposition semantics on ok:** the work item completed
     successfully, so `_finalize_message` has already consumed the reserved
     row — that is correct. [QUEUE.6]'s reserved policy governs
     *unfinished* work; applying `requeue` to successfully completed work
     would cause duplicate execution. The deferred finalization on the ok
     path therefore performs only the task-level part (terminal transition,
     envelope, ack) and must treat the already-consumed reserved row as a
     clean no-op. Verify `_finalize_deferred_active_control` tolerates a
     missing reserved row; if it does not, make the reserved-disposition
     step conditional on the row still existing — do not re-fetch or
     re-create it.
     Reuse `_finalize_deferred_active_control`; do not add a parallel
     finalizer or duplicate its transition logic.
   - Constraints: ordering is result-then-terminal; one-shot tasks that
     already emit `completed` must not double-emit a terminal event (the
     finalizer re-reads deferred state — verify it no-ops cleanly when the
     task is already terminal, and if it does not, gate the call on
     pending-deferred-command, not on task type).
   - Tests (red-green): first write the failing test — persistent consumer,
     STOP arrives mid-work, work completes `ok`; assert exactly one
     terminal event with the same status the timeout/limit deferral paths
     produce, the result present in the outbox, and the loop exits. Mirror
     the existing deferred-timeout test structure. Add the KILL variant, a
     one-shot-task variant asserting no double terminal emission, and a
     `reserved_policy=requeue` variant asserting the successfully completed
     work item is **not** requeued (no duplicate execution) — the inbox
     stays empty and exactly one result exists.
   - Stop if: honoring the deferral on ok outcomes requires changing what
     status the finalizer assigns — that is a spec question; raise it
     rather than inventing semantics.
   - Done when: the new regressions pass and the existing deferred-control
     tests stay green.

3. **Create-time-guard the CLI fallback stop/kill paths.**
   - Outcome: `weft task stop/kill` fallback paths verify each candidate
     PID with `pid_matches_create_time` before signaling — achieving parity
     with the Manager's guarded kill path. Semantics decision (external
     review): entries whose payload carries **no** create-time keep the
     helper's existing fallback (plain PID-liveness check), exactly as the
     Manager and `weft/core/runners/host.py:109` already behave — do not
     invent a stricter skip-and-warn rule that diverges from the shared
     helper. The defect being fixed is that entries **with** create-times
     are never verified today; a recorded-but-mismatched create-time must
     block the signal.
   - Files to touch: `weft/commands/tasks.py`, possibly a small shared
     helper in `weft/helpers/__init__.py`, `tests/commands/` (or
     `tests/cli/`) test file that covers stop/kill fallback.
   - Read first: `weft/commands/tasks.py:1160-1470`;
     `weft/helpers/__init__.py:97-125` (`pid_matches_create_time`);
     `weft/core/manager.py:5879-5900` (the guarded reference path);
     `weft/ext.py:80-95` (`scoped_host_processes` — `(pid, create_time)`
     pairs); how the mapping payload is written in
     `weft/core/tasks/base.py:1522-1539` (confirm create-times are present
     in the serialized handle; they are what the manager path consumes).
   - Approach: change `_host_pids_from_mapping` (or add a sibling
     `_host_processes_from_mapping` returning `(pid, create_time)` tuples
     and migrate the three signal-sending call sites to it). Before any
     `os.kill`/`terminate_process_tree`/`kill_process_tree` call, require
     `pid_matches_create_time(pid, create_time)`. Read-only consumers of
     `_host_pids_from_mapping` (existence checks at lines ~157, ~326) may
     stay unguarded — signaling is the destructive edge.
   - Constraints: layering — helpers/core only; no import of manager
     internals into commands. Exit codes unchanged: if all PIDs fail
     verification, the command flows into the same "no live process"
     outcome path a dead task takes today (the fallback helpers return
     booleans — no new user-facing message channel is added; a debug log
     line at the guard is sufficient).
   - Tests: unit-test the guard with a real spawned child (harness) — kill
     succeeds for a matching create-time; then simulate a stale mapping by
     rewriting the mapping entry's create_time (or pid) and assert the
     command refuses to signal. Add a no-create-time entry variant
     asserting the existing liveness-fallback behavior is preserved. Do
     not mock psutil for the positive path.
   - Stop if: mapping entries turn out not to carry create-times — that
     means the payload schema needs a field, which is a contract change;
     stop and re-plan (do not synthesize create-times at read time).
   - Done when: both regressions pass; existing stop/kill tests green.

4. **Retry and surface terminal-event write failures.**
   - Outcome: failures writing the terminal state event or terminal
     envelope are retried a bounded number of times with a short backoff
     and logged at WARNING (with `exc_info`); non-terminal state-change
     write failures keep today's debug-level best-effort behavior.
   - Files to touch: `weft/core/tasks/base.py`, `weft/_constants.py`,
     `tests/tasks/` (new focused test file or existing base-task tests).
   - Read first: `base.py:1200-1300` (`_report_state_change`,
     `_send_terminal_envelope`), `base.py:651-706` (`cleanup()` deletes
     `ctrl_out` per [OBS.13.9]); [OBS.1].
   - Approach: add `TERMINAL_EVENT_WRITE_RETRIES: Final[int]` (suggest 3)
     and `TERMINAL_EVENT_WRITE_RETRY_INTERVAL: Final[float]` (suggest 0.2,
     docstring explaining why no event-driven alternative exists — this is
     a broker-write failure path) to `_constants.py`. Retry only when the
     event being written is terminal.
   - Constraints: still non-fatal after retries exhaust — the process must
     exit. No change to `cleanup()` ordering in this task.
   - Tests: inject failure by pointing the state-log queue at a broken
     target (e.g., close/rename the db underneath a queue object, or a
     minimal stub for the *queue write only* — this is the one place a
     narrow stub is acceptable because the failure is external and
     nondeterministic to produce for real); assert WARNING logged and
     retries attempted, then success-path test that one transient failure
     followed by success emits exactly one terminal event.
   - Done when: tests pass; no behavior change on the happy path.

### Phase B — monitor/pruning retention safety

5. **B1 (keystone): stop deleting live tasks' latest tid-mapping rows.**
   - Outcome: the TaskMonitor tid-mapping cleanup (a) always keeps the
     newest row per mapping key regardless of age when its owner passes a
     liveness probe, and (b) deletes superseded (non-newest) rows past
     min-age as today. The liveness probe uses only evidence present in
     the mapping payload itself — `pid_matches_create_time` on the
     handle's `(pid, create_time)` pairs. No terminal-evidence lookup:
     `tid_mapping_candidates` receives only raw rows and must stay that
     way (external review) — a terminal task's process is dead, so the
     probe fails and its newest row becomes deletable past min-age, which
     yields the same end state without new inputs. A plain task running
     for days keeps exactly one valid mapping row.
   - Files to touch: `weft/core/monitor/policies/tid_mapping.py`,
     `tests/core/test_task_monitor_cleanup.py`,
     `tests/core/monitor/policies/` (if a policy-level test dir exists —
     follow the existing test placement for this policy).
   - Read first: `policies/tid_mapping.py` (all 142 lines);
     `weft/core/pruning/runtime.py:500-560` (the foreground tid-mappings
     group — the keep-newest-per-`full`-key semantic to mirror);
     `weft/core/monitor/task_monitor.py:5406-5418` (how the policy is
     invoked, `exclude_tids`); [OBS.13.7]; the Cleanup Boundary section of
     05.
   - Approach: group candidate rows per mapping key inside
     `tid_mapping_candidates`; the newest row per key is a candidate only
     if its payload's liveness probe fails; superseded rows keep the
     current age-only rule. Rows whose payload carries no probeable host
     PIDs (external/non-host runtime handles) are treated as live —
     undecidable means skip, never delete. Reuse the payload-parsing
     helpers this module already uses; do not import the pruning module
     into the monitor policy if that creates a new dependency direction —
     if the grouping helper is worth sharing, place it beside the payload
     helpers both already use.
   - **The existing test
     `test_task_monitor_cleanup_deletes_old_tid_mapping` encodes the defect
     and must be rewritten** to assert: superseded (non-newest) rows of a
     live task are deleted, the newest row of a live-probing owner survives
     regardless of age, and the newest row of a dead owner is deleted after
     min-age. Red-green: write the "newest row of a live task survives past
     min-age" test first and watch it fail against current code.
   - Constraints: `exclude_tids` behavior unchanged; no new constants
     needed (min-age keeps its meaning for the rows that remain eligible);
     no periodic re-registration writer added in `base.py`.
   - Stop if: liveness cannot be decided from the mapping payload alone for
     some row shape — treat undecidable as live (skip), and note it; do not
     reach into the collation store from this policy.
   - Done when: rewritten tests pass; a live-process harness test proves a
     >min-age (overridden via the policy's min-age parameter, not a
     monkeypatched constant) running task keeps its mapping row through a
     destructive monitor cycle.

6. **B2: liveness-gate `stale_open` disposal.**
   - Outcome: a non-service open family older than
     `stale_open_family_seconds` is disposed only if its TID is absent from
     `_active_runtime_tids` at disposal time — the same exclusion the
     `stale_service_owner` branch already applies. A live quiet task's
     queues are never deleted.
   - Files to touch: `weft/core/monitor/task_monitor.py` (and/or
     `weft/core/monitor/store.py` if the classification point is the right
     seam — prefer gating at the monitor, where `_active_runtime_tids` is
     already computed), `tests/core/test_task_monitor.py` /
     `test_task_monitor_cleanup.py`.
   - Read first: `store.py:978-1007` and `:2632-2641` (`_stale_open`
     classification); `task_monitor.py:3041-3091` (the service-branch
     liveness exclusion to mirror); `task_monitor.py:3376` (delete-time
     check); [OBS.13.7].
   - Constraints: depends on Task B1 having landed (otherwise
     `_active_runtime_tids` is empty for exactly the tasks that need
     protection — this ordering is mandatory). Dead-family disposal
     behavior unchanged. Scope honesty (external review): the protection
     covers tasks whose runtime handles carry host-PID liveness evidence —
     the same evidence model `_active_runtime_tids` uses today.
     External/non-host runner handles without host-PID proof are protected
     via B1's undecidable-means-live rule feeding this set; state that
     evidence model explicitly in the spec text this task touches rather
     than promising more than the check can prove.
   - Tests: harness test with a live process whose family is artificially
     aged past the (parameter-overridden) stale-open window: assert the
     family is not disposed and its queues survive; companion test that a
     genuinely dead family still disposes.
   - Done when: both tests pass; existing stale_open tests stay green.

7. **B3: age-gate store-backed reserved cleanup.**
   - Outcome: `select_reserved_cleanup_pending_tasks` only returns families
     whose terminal evidence is older than a named retention gate, so
     `ReservedPolicy.KEEP` rows remain inspectable for the same window the
     dead-TID path already honors.
   - Files to touch: `weft/core/monitor/sql.py`,
     `weft/core/monitor/task_monitor.py` (threading the cutoff),
     `weft/_constants.py` (new
     `TASK_MONITOR_RESERVED_CLEANUP_MIN_AGE_SECONDS: Final[float]` —
     default it to the task-log retention period constant so the gates
     agree; docstring says why), tests for the reserved slice.
   - Read first: `sql.py:685-704`; the reserved slice in
     `task_monitor.py:3565-3601`; `TaskMonitorCleanupConfig`
     (`task_monitor.py:72` region) — the new threshold becomes a field
     there, defaulted from the new constant; the age-gated reference in
     `weft/core/monitor/policies/reserved.py:99-108`; [OBS.13.5],
     [QUEUE.6], and 05's "reserved rows remain protected recovery-sensitive
     evidence".
   - Constraints: gate on terminal-evidence age where available
     (`terminal_message_id` — see Task B4's evidence choice), TID age as
     fallback; parameter flows through `TaskMonitorCleanupConfig`, not a
     module-level read.
   - Tests (red-green): failed task with `reserved_policy=keep` → run the
     reserved slice immediately → row survives; age past the gate
     (parameter override) → row cleaned. Budget note (external review):
     existing tests assert *immediate* reserved cleanup as intended
     behavior (e.g. `tests/tasks/test_task_monitor.py:2990` region) — those
     assertions must be updated to set the gate to zero explicitly or to
     age the family, and that rewrite is part of this task, not drive-by
     damage.
   - Done when: both directions pass and the updated legacy tests are
     green.

8. **B4: compute outbox retention from completion, not creation.**
   - Outcome: `retention_eligible` in the record-backed runtime-control
     policy uses the family's terminal evidence, falling back to
     TID-creation age only when absent. A task that ran 3 days and
     completed 1 hour ago keeps its outbox for the full retention window.
   - Evidence choice (external review): the collation record already
     carries `terminal_message_id` (`sql.py:54` region, `store.py:175`
     region) — **no schema change is needed**. Use `terminal_message_id`
     as the primary basis: it is in the same hybrid-timestamp domain as
     `int(tid)`, so `is_old_enough` applies unchanged and the store's
     documented clock-skew handling (`store.py:900-960`) carries over.
     Do not use `completed_at_ns` wall-clock time for retention ordering.
     Fallback when `terminal_message_id` is absent: `int(tid)` (today's
     behavior).
   - Dead-task slice scope (external review): `dead_task_queue_cleanup_plan`
     (`dead_task.py:101` region) receives only tid/queues/retention and by
     construction handles TIDs *without* monitor records — it has no
     terminal evidence and **keeps its creation-based gate unchanged**;
     record that rationale in its docstring only. Do not widen its
     signature.
   - Files to touch: `weft/core/monitor/policies/runtime_control.py`
     (plus the caller in `task_monitor.py` that threads the record into
     the plan builder), `weft/core/monitor/policies/dead_task.py`
     (docstring only).
   - Read first: `runtime_control.py:220-250`, `dead_task.py:100-120`,
     `weft/core/monitor/collation.py:140-160`, `sql.py:40-80` and
     `store.py:170-180` (record fields), `store.py:900-960` (clock-skew
     approach); [MF-5] and the Cleanup Boundary wording, which this task
     also amends (see spec delta below).
   - Spec delta (temporary in-flight state; update the spec text in the
     same change): 05's "only when the TID is older than the configured
     task-log retention period" becomes "only when the terminal evidence
     (or, absent terminal evidence, the TID) is older than …". Add the plan
     backlink under 05's `## Related Plans` and update any nearby
     `_Implementation mapping_` notes.
   - Constraints: no schema change; a record without `terminal_message_id`
     behaves exactly as today (fallback), so rollback is code-revert-only.
   - Tests (red-green): terminal-at-T, created-at-T-minus-3-days → not
     retention-eligible until T+window; record without
     `terminal_message_id` → today's behavior.
   - Stop if: the evidence cannot be threaded without changing summary
     payload shapes consumed outside the monitor — re-plan.
   - Done when: tests pass on both record shapes.

9. **B5: stop truncating the retention archive.**
   - Outcome: a second apply run on the same day appends to the archive
     instead of destroying the prior run's recovery record.
   - Files to touch: `weft/core/pruning/retention.py` (line ~1256:
     `"w"` → `"a"`), its tests.
   - Constraints: keep the date-keyed default filename; each record line
     already carries run identification — verify, and if not, include the
     run id per line rather than changing the filename scheme.
   - Tests: two apply runs against the same archive path; assert both runs'
     records present.
   - Done when: the two-run test passes. (This is deliberately the
     smallest task; do not bundle it with B4.)

10. **B6: reconcile cleanup-boundary spec text and document
    retention-prune's scope.** (Doc-only; no code behavior change.)
    - Outcome: 05's Cleanup Boundary section and 07's [OBS.13] intro
      (line ~531 area) describe the shipped default accurately — raw
      task-log rows are deleted at ingest once collated, per [OBS.13.3];
      the summary-gated description applies to `jsonl_then_delete` mode
      only. `weft system retention-prune`'s command help and docstring
      state that under the default collation mode most task evidence has
      already been ingested and the command principally covers
      collation-off deployments.
    - Files to touch: `docs/specifications/05-Message_Flow_and_State.md`,
      `docs/specifications/07-System_Invariants.md`,
      `weft/commands/runtime_prune.py` (or wherever the retention-prune
      CLI help lives — locate via `weft/cli/app.py`), plan backlinks under
      both specs' `## Related Plans`.
    - Constraints: [OBS.13.3] is the normative anchor — edit the prose that
      contradicts it, not the invariant. No behavior change smuggled in.
    - Done when: a fresh read of 05/07 gives one consistent account;
      `rg "only after" docs/specifications/05*` shows no stale gating
      claim.

### Phase C — documentation honesty

11. **C1: document the enforcement and agent-authority model.**
    (Doc-only.)
    - Outcome: `06-Resource_Management.md` [RM-5.1] states the default
      1.0s poll cadence, single-sample memory kill, 4-of-5 CPU rule, and —
      explicitly — that enforcement tracks the psutil child tree, so a
      task that calls `os.setsid()`/double-forks escapes both limits and
      kill (with the trust-model framing from 00). `13-Agent_Runtime.md`
      gains a short "Security Model" subsection describing the
      provider-CLI authority behavior **per provider and condition** —
      e.g. the Claude backend adds `--dangerously-skip-permissions` when
      `authority_class` is not `bounded` and workspace access is not
      read-only/none (`registry.py:310-400` region; verify the exact
      conditions in code before writing — external review flagged that a
      blanket "general implies skip-permissions" claim is inaccurate) —
      and what `bounded` changes.
      `extensions/weft_macos_sandbox/README.md` states that profile
      contents are not validated — isolation is exactly what the supplied
      profile grants — and notes `sandbox-exec`'s deprecated status.
    - Read first: `weft/core/resource_monitor.py:280-350`,
      `weft/core/agents/provider_cli/registry.py:320-400`, 00's trust
      model section; [AR-2.1].
    - Constraints: describe, don't redesign. No new promises. Backlink
      this plan under 06's and 13's `## Related Plans`.
    - Done when: each documented number/flag is verified against the named
      code before writing (do not copy from this plan without checking).

12. **C2: fix the drifted invariant summaries in CLAUDE.md/AGENTS.md.**
    (Doc-only; no spec — these files summarize specs.)
    - Outcome: §3's state list includes `spawning` and `cancelled`
      (matching [STATE.1]); the §4.4 example uses the real
      `DEFAULT_MEMORY_MB = 1024`.
    - Files to touch: `CLAUDE.md`, `AGENTS.md`. **Note:** `AGENTS.md` has
      uncommitted local changes in the working tree — rebase on whatever
      is there; do not revert unrelated hunks.
    - Done when: `rg "spawning" CLAUDE.md AGENTS.md` hits §3 in both, and
      the memory example matches `weft/_constants.py:437`.

## Testing Plan

- Harness: `WeftTestHarness` for anything involving a live task, manager,
  or monitor cycle (Tasks A1, A2, A3, B1, B2). Real broker-backed `Queue`s
  for policy/SQL-level tests (B3, B4, B5). No mocks for queues, process
  lifecycle, reservations, state transitions, or result delivery — the one
  sanctioned narrow stub is Task A4's injected queue-write failure, because
  the failure is external and nondeterministic to produce for real.
- Red-green is required for A2, B1, B3, B4 (each is cleanly expressible as
  a failing behavioral test against current code — write it first, watch it
  fail). A1 and A3 are race/permission shaped: for A1 the deterministic
  proof is the deferral-seam unit test plus the end-to-end SIGTERM test;
  for A3 the stale-mapping rewrite makes the negative case deterministic.
- Aging in tests is done by overriding the policy/slice min-age and window
  parameters through their existing config plumbing (the current
  `test_task_monitor_cleanup.py` shows the seam) — never by sleeping past
  real thresholds and never by monkeypatching `_constants` values.
- Invariants each test protects: A1/A2/A4 → [STATE.1]/[OBS.1] exactly-once
  terminal emission; A3 → [QUEUE/process safety, OBS.10 spirit]; B1/B2 →
  [OBS.13.7] no deletion of active work; B3 → [QUEUE.6]/[OBS.13.5];
  B4/B5 → recoverability of results and pruned payloads.
- Terminal-completion assertions must allow the completion-event/outbox
  grace window (poll with deadline, per harness patterns).
- Tempting-to-skip edge cases that are in scope: A2's one-shot
  double-emission variant; B1's "undecidable liveness ⇒ skip" row; B4's
  legacy record without a terminal timestamp. Out of scope: multi-manager
  interactions with any of these (no leadership behavior is touched).
- Post-landing runtime observation (hardening §9): after Phase B lands,
  run a real `weft run --no-wait` task that outlives
  `TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS` (temporarily lowered
  via config) on a dev box and confirm `weft status` still shows runtime
  proof and `weft result` returns output after completion.

## Verification and Gates

Per-task (while implementing):

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/tasks/test_task_execution.py -q      # A1, A2
./.venv/bin/python -m pytest tests/commands/ -q -k "stop or kill"      # A3
./.venv/bin/python -m pytest tests/core/test_task_monitor_cleanup.py -q # B1, B2
./.venv/bin/python -m pytest tests/core/monitor -q                      # B3, B4
./.venv/bin/python -m pytest tests/core/test_pruning_apply.py -q        # B5
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q       # plan index row
```

Final gates (before claiming done — full suite required; the blast radius
is the durable spine and destructive cleanup):

```bash
./.venv/bin/python -m pytest -m ""            # all tests including slow
./.venv/bin/mypy weft bin extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft
```

Success looks like: all new regressions green, zero existing-test changes
except the rewritten `test_task_monitor_cleanup_deletes_old_tid_mapping`
(whose rewrite is itself a deliverable), specs updated in the same commits
as the behavior they describe, and the plan index row flipped to
`completed` at the end.

## Independent Review Loop

- This plan touches the durable spine and destructive cleanup — external
  review is required (hardening §17), preferably from a different agent
  family than the author when available (e.g. the repo's Codex review
  lane), in addition to the author's fresh-eyes pass.
- Reviewer reads: this plan; `weft/core/tasks/consumer.py` (defer/finalize
  region); `weft/core/launcher.py`; `weft/core/monitor/policies/`
  (tid_mapping, runtime_control, dead_task, reserved);
  `weft/core/monitor/task_monitor.py` disposal/delete slices; 05 Cleanup
  Boundary; 07 [OBS.13].
- Review prompt: "Read the plan at
  docs/plans/2026-07-02-runtime-correctness-and-retention-remediation-plan.md.
  Carefully examine the plan and the associated code. Look for errors, bad
  ideas, and latent ambiguities. Don't do any implementation, but answer
  carefully: could you implement this confidently and correctly if asked?"
- Feedback handling: the author addresses each point explicitly in the plan
  (update, justify, or record as out of scope) before implementation
  starts.

## Out of Scope

- **Dead-manager public spawn-request recovery** (a manager SIGKILLed
  between reserving a `weft.spawn.requests` row and launching leaves the
  request stranded forever). Auto-requeue is unsafe without
  launch-evidence checks — a crash *after* launch but before the reserved
  delete would double-launch on requeue. This needs its own spec-first
  plan against [MF-6] and the MANAGER invariants; do not bolt it on here.
- Leadership-election extraction from `manager.py`; any change to
  liveness probing or registry semantics.
- The security items already planned in
  [`2026-06-09-evaluation-findings-remediation-plan.md`](./2026-06-09-evaluation-findings-remediation-plan.md)
  (spill-file/dir permissions, macOS sandbox env passthrough,
  `approval_required` validation) and its doc-ledger items.
- Making `weft system retention-prune` consult the Monitor store (B6
  documents the limitation instead; a store-consulting rewrite is a
  separate decision).
- The streaming-prune dead branch / duplicate-row accumulation (cosmetic,
  conservative-safe) and the redundant comprehension guards in
  `retention.py` — leave them.
- Any positioning/README/1.0-contract product work.

## Fresh-Eyes Review

Author self-review pass (2026-07-02, post-draft): found and fixed —
(1) B2 originally did not state its hard ordering dependency on B1;
(2) A2 originally said "call the finalizer from the ok path" without
pinning result-before-terminal ordering or the one-shot double-emission
hazard; (3) A3 originally guarded only `_kill_via_fallback`, missing that
`_force_kill_task_processes` and `_stop_via_fallback` share the unguarded
source; (4) test-aging guidance originally said "override constants",
which invites monkeypatching `Final` values — corrected to parameter
plumbing.

External review (2026-07-02, Codex CLI — different agent family, per the
Independent Review Loop): 9 findings, all addressed in the plan text:

1. A2 could violate reserved-queue policy / cause duplicate execution on
   ok outcomes → resolved: ok-path finalization is task-level only; the
   consumed reserved row is a deliberate no-op; requeue-variant regression
   test added.
2. A2 call-site could run after `_active_message_timestamp` is cleared →
   resolved: placement pinned inside `_handle_active_work_result` before
   its `finally` cleanup.
3. A3's skip-and-warn rule contradicted the shared helper's
   missing-create-time fallback used by manager/host paths → resolved:
   adopted helper parity; only recorded-but-mismatched create-times block.
4. B1 required terminal evidence the policy function does not receive →
   resolved: liveness probe from the row payload alone; terminal
   condition dropped (dead process ⇒ probe fails ⇒ same end state).
5. B3 lacked the config path and ignored existing immediate-cleanup test
   assertions → resolved: `TaskMonitorCleanupConfig` named; legacy test
   rewrite budgeted into the task.
6. B4's "terminal-evidence timestamp" was ambiguous → resolved:
   `terminal_message_id` (hybrid-timestamp domain) chosen; no schema
   change; `completed_at_ns` explicitly rejected for retention ordering.
7. B4's dead-task slice cannot see terminal evidence → resolved: it keeps
   the creation-based gate by design (no monitor record exists for its
   TIDs); docstring-only change.
8. B2's liveness protection is narrower than "any live quiet task" →
   resolved: evidence model stated honestly; undecidable-means-live rule
   in B1 covers non-host handles.
9. C1's blanket skip-permissions claim was provider/condition-specific →
   resolved: doc task now requires verifying per-provider conditions in
   `registry.py` before writing.

This plan is **review-complete and implementation-ready**; status stays
`draft` until the slices land, per the plan status taxonomy.

Implementation deviation record (Task A1, adjudicated necessary-and-sound
by post-implementation review): deferral required wiring three files beyond
the task's list (`manager.py` — its handler was already the in-memory
recorder, renamed to `note_termination_signal` with a delegating alias;
`task_monitor.py` and `heartbeat.py` — standalone `process_once` overrides
now check the pending signum each turn). The manager drain escalation's 2s
rung became SIGTERM-only (`kill_after=False`): the prior 0.2s SIGKILL
follow-up defeated child-side deferred termination and corrupted
`multiprocessing` bookkeeping via psutil reaping (ECHILD), forcing every
drain to the 15s timeout. Ordering is preserved; the hard-kill rung remains
at `MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS`. Worst-case: a child that
ignores SIGTERM survives ~2.2s → up to ~15s during drain.

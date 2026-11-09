# Manager Startup Interest From Launch Selection

Status: completed
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.8], [MA-3]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]; docs/specifications/01-Core_Components.md [CC-2.1], [CC-2.2.1]; docs/specifications/07-System_Invariants.md QUEUE.7, QUEUE.8
Superseded by: none

Class: 5 with hardening. The change edits normative [MA-1.8] text (class 5)
and the Manager reactor wait path on the durable spine (risky trigger, so the
hardening checklist applies). Spec baseline: `08c2f87b` (`perf/admission-base`).
Plan type: implementation with spec revision; promotion strategy B (atomic).

## Goal

The Manager rebuilds its SimpleBroker multi-queue activity waiter whenever the
set of watched queue names changes. Each unsettled ordinary startup adds its
`weft.state.tasks.<tid>` queue to that set ([MA-1.8]). Today the interest is
added at the wait after the launch-worker receipt and removed at the wait after
settlement, so a launch admitted by a settlement costs two waiter
constructions: one for the removal and one, a turn later, for the addition.
On PostgreSQL every construction resolves the backend plugin once per watched
queue through `importlib.metadata` entry-point discovery (measured 1.66 ms per
lookup on this machine), which is the cost the burst profile reports as
`wait_setup` (8.2% of wall) and most of the plugin-discovery share of Manager
busy time.

This plan makes a startup eligible for notification interest when its launch
is selected, so the next wait installs it and the settlement that frees a
window slot and the launch it admits change the waiter once. Registration is
not guaranteed before process creation: the launch worker starts before that
wait, so a child can publish first; the receipt turn's exact read covers that
publication, as it does today. The plan records the SimpleBroker follow-ups
that remove the remaining per-construction cost.

## Premise check (what the brief asked for and what is possible)

The track brief asked for a wait set that does not churn per launch, for
example through incremental registration or a stable observation source. The
code and the governing spec show that neither is available in Weft today:

1. **Incremental registration is not a public SimpleBroker primitive.**
   `simplebroker_pg.runner._SharedActivityListener.register_queue` and
   `register_queue_set` exist, but only behind the private `_activity_registry`.
   The public surface is `simplebroker.create_activity_waiter_for_queues(queues,
   stop_event=...)`, which returns a waiter with fixed membership
   (`PostgresMultiQueueActivityWaiter._queue_names`). [SB-0.4] states
   "Weft does not call backend-specific listener APIs", and [WT-2a.8] puts
   queue mechanics in SimpleBroker. Calling the private registry from Weft is
   refused.
2. **No stable source carries the startup fact.** A child's startup fact is
   its row on `weft.state.tasks.<tid>` ([QUEUE.7] fixes that route). The PG
   NOTIFY payload is the exact queue name (`simplebroker_pg/_sql.py`
   `INSERT_MESSAGE`), with no prefix interest. The only stable queue every
   child writes at startup is `weft.log.tasks` (`task_initialized`, written
   after the construction snapshot in `BaseTask.__init__`). Waking on it would
   wake the Manager on every lifecycle event of every task in the context and
   couple settlement to the order of two separate best-effort writes. That is
   the shape [ALT-WT-009] refuses, and it would cost more peeks than the
   rebuilds it saves. A new child-to-Manager receipt message would be a new
   protocol duplicating the snapshot fact. Both are refused.
3. **The cost is SimpleBroker's.** `Queue._activity_waiter_identity()` calls
   `BrokerTarget.plugin`, which calls `get_backend_plugin(name)`, which scans
   `importlib.metadata.entry_points()` on every call for a non-SQLite backend.
   `create_activity_waiter_for_queues` does this once per queue per call.
   Weft-side direct callers of `Queue.backend_name` were already removed from
   the Manager by the 2026-11-07 nested-admission plan
   (`Manager._admission_backend_name` reads the bound context); an audit of
   `weft/` finds no remaining Weft call of `Queue.backend_name`,
   `BrokerTarget.plugin`, `get_backend_plugin`, or a Queue-object move
   destination (all `move_one` calls pass names). The remaining discovery on
   the Manager wait path is inside `create_activity_waiter_for_queues`.

Corrected scope: with exact per-TID interest and fixed-membership waiters, the
minimum is one waiter construction per membership change. This plan makes a
window-bound launch cost one construction instead of two, and records the
SimpleBroker changes that make each construction cheap and, later, avoidable
([WT-4.2] "Push a concern down when it belongs lower").

## Source documents and reading

- `docs/program-theory.md` [WT-2a.6], [WT-2a.8], [WT-4.1] (wake predicates read
  facts), [WT-4.2] (use the primitives you have; push a concern down),
  [WT-4.3] (one arbiter; a quiet task costs nothing), [ALT-WT-002],
  [ALT-WT-009], [ALT-WT-010].
- `docs/specifications/03-Manager_Architecture.md` [MA-1.8], the paragraph that
  begins "Each unfinished ordinary startup has one transient
  notification-only interest" and the window paragraph "Every ordinary
  PostgreSQL launch requires a fresh coherent usage observation".
- `docs/specifications/07-System_Invariants.md` [QUEUE.7] (notification-only
  interests may change between turns; registration precedes the initial
  read) and [QUEUE.8] (one exact activity-wait signature per membership
  generation; candidate-before-close replacement).
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4] (Weft creates
  replacements with `create_activity_waiter_for_queues` and installs them
  through `PollingStrategy.replace_activity_waiter`).
- `docs/lessons.md` 2026-10-14 entry "Positive startup publication and the
  absence fallback are separate events" (register before reading; no helper
  reader).

## Current structure

- `weft/core/manager.py::Manager._launch_child_task` selects one launch per
  turn: it records `_active_child_launches[tid]`, calls
  `_checkpoint_admission_launch` (which creates `_startup_feedback[tid]` for an
  ordinary launch), and starts the single child-launch worker lane.
- `Manager._handle_child_launch_result` (the receipt turn) clears the active
  launch, commits the receipt, and calls `_reconcile_launch_now(tid)`, an exact
  direct read of `weft.state.tasks.<tid>`.
- `Manager._activity_wait_queues` adds base queues, drain-stop targets, and one
  lazily opened nonpersistent `Queue` per startup record, but **skips** TIDs in
  `_active_child_launches`.
- `Manager._has_pending_observation` peeks the newest row after each record's
  publication cursor for every record with an installed queue.
- `weft/core/tasks/multiqueue_watcher.py::MultiQueueWatcher._ensure_multi_activity_waiter`
  compares the ordered queue-name signature with the installed one and, on any
  change, constructs a new waiter and replaces the old one. It runs at every
  wait entry (`Manager._ensure_wait_strategy_started`, traced as
  `wait_setup`).

Measured on this branch's base with a counting spy around the real factory
(`provide_payload` children through the real Manager): SQLite, 9 children,
14 constructions; PostgreSQL, 9 children, 15. The base set is three queues
(`weft.spawn.internal`, `weft.spawn.requests`, `T{manager}.ctrl_in`).

Comprehension questions (answer before editing):

1. Which read observes a publication that lands between process creation and
   the receipt turn? Expected: `_reconcile_launch_now` in the receipt turn,
   and the wait-entry `_has_pending_observation` read once the TID is no
   longer an active launch.
2. Why must `_has_pending_observation` keep skipping active launches when
   their queues join the waiter? Expected: a pending publication flag for an
   active launch cannot be consumed until the receipt turn, so the wait-entry
   check would return immediately on every wait and spin.

## Design

1. `Manager._activity_wait_queues` stops skipping active launches. A selected
   ordinary launch's record already exists (created in
   `_checkpoint_admission_launch` before the worker starts), so its queue joins
   the waiter at the wait that ends the selection turn. The worker may create
   the process, and the child may publish, before that wait; the receipt
   turn's exact read observes such a publication. In a window-bound steady
   state the settlement that freed the slot and this selection happen in the
   same turn, so the removal and the addition share one construction; the
   receipt turn changes nothing.
2. `Manager._has_pending_observation` keeps reading publication cursors only
   for launches whose receipt has been handled: it skips TIDs in
   `_active_child_launches`. The receipt turn's exact read covers the gap.
3. No change to `MultiQueueWatcher`, the signature rule, SimpleBroker, the
   window size, cursors, release points, or cleanup.

Rejected alternatives (recorded so they are not retried):

- Lazy removal (keep a settled TID's queue until the next addition): a settled
  child's later snapshots would wake the Manager, each wake reading up to W
  cursors plus a pending check, and a quiet Manager would keep historical
  handles ([QUEUE.7] "No historical notification handles accumulate").
- Calling `simplebroker_pg`'s private listener registry: refused by [SB-0.4]
  and [WT-2a.8].
- Waking on `weft.log.tasks`, or a new child-to-Manager receipt message: see
  the premise check.
- A Weft-side cache of the backend plugin or a hand-built waiter that bypasses
  `create_activity_waiter_for_queues`: reimplements SimpleBroker behavior
  ([WT-2a.8], [ALT-WT-010]).
- A wait cap or periodic tick instead of interest: [ALT-WT-002].

## Observable-difference register

| Change | Class | Theory basis |
| --- | --- | --- |
| A selected launch's interest is installed at the first wait after selection, not at the wait after the receipt | C | [WT-4.1]: interest is installed earlier than before, and a publication that precedes it (the worker may already have created the process) is observed by the unchanged receipt-time exact read, so no publication is missed |
| A window-bound launch costs one waiter construction instead of two | C | [WT-4.2]: same primitive, fewer constructions |
| Up to one additional transient interest (the selected launch) while the worker lane runs | N | Window occupancy already counts selected launches, so interests stay at most W ([MA-1.8]) |
| A publication that lands before the receipt is handled can wake one wait that finds no cursor to read and resumes waiting | C | [WT-4.3]: the strategy return reads facts and continues; the receipt turn's read settles it. Before this change the same publication waited for the receipt's local wake |
| Settlement latency, the absence fallback, release points, custody, cleanup and SQLite behavior | N | Unchanged code paths |

No `D` rows.

## Proposed Spec Delta

Promotion strategy B (atomic): the spec text, code, and test land in one
change.

### [MA-1.8] in `docs/specifications/03-Manager_Architecture.md`

Replace the span from "Each unfinished ordinary startup has one transient
notification-only interest" through "There are at most W outstanding
interests (one on SQLite)." with:

> Each unfinished ordinary startup has one transient notification-only interest
> in `weft.state.tasks.<tid>`. Selecting its launch makes the startup eligible
> for interest, and the next wait installs it, so the settlement that frees a
> window slot and the launch it admits change the activity waiter once. A
> publication may land before that wait installs interest. Its publication
> cursor is read only after the launch-worker receipt is handled, and the
> receipt turn's exact handoff read observes any earlier publication. There
> are at most W outstanding interests (one on SQLite).

(Revised after the cross-family review, finding F1: the first draft said
"from the turn that selects its launch, before process creation", which
overstated the ordering.)

Add to `## Related Plans`:

> - [Manager startup interest from launch selection](../plans/2026-11-08-manager-stable-wait-set-plan.md)
>   installs startup interest at the first wait after launch selection so one
>   settlement and the launch it admits rebuild the activity waiter once, and
>   records the
>   SimpleBroker plugin-lookup follow-up ([MA-1.8], [SB-0.4]).

[QUEUE.7] ("active notification interests bounded by the [MA-1.8] startup
window"; "Registration precedes the initial read") remains true and is not
edited.

## Tasks

1. RED test. Add
   `test_settlement_and_admitted_launch_share_one_waiter_rebuild` to
   `tests/core/test_manager_family_maintenance.py`, beside
   `test_startup_window_overlaps_but_bounds_unobserved_processes`, using the
   `startup_pg_manager` fixture (both backends) and the existing fenced
   `_publish_waiting_task` producer. Fill the window with fenced publications,
   then count real waiter constructions (a delegating spy around
   `weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues`, the
   same pattern as `tests/tasks/test_multiqueue_watcher.py`) while one
   publication is released and the Manager settles it and selects the
   admitted launch. Hold the admitted launch's receipt (a test-owned
   `threading.Event` in the `launch_task_process` stand-in) until the wait
   after the settling turn has run while that launch is still active, so a
   fast receipt cannot fold the two edges together; then release it, let the
   receipt turn run, and assert there is no second construction. Periodic
   maintenance is held off, so the settlement proves the notification wake.
   Assert one construction whose membership swaps the settled TID for the
   admitted one. Confirm it fails on the base code.
2. Implement the two `Manager` edits in the Design section, with docstrings
   citing [MA-1.8].
3. Update `test_positive_startup_snapshot_wakes_admission_without_maintenance_tick`.
   Its old `publish_before_interest` case asserted that no interest existed
   after the receipt, which is no longer the contract. Keep the case name and
   its reachable ordering: the child publishes before the next wait installs
   interest while the test holds the launch receipt (a test-owned
   `threading.Event` inside the existing `launch_task_process` stand-in). The
   test then installs interest at the existing setup boundary, runs turns and
   waits, asserts that no cursor read or handoff happened before the receipt,
   releases the receipt, and asserts the receipt turn's exact read settled
   the startup. Every case asserts that the first wait after selection
   installs interest before the receipt. Keep the other cases unchanged.
4. Apply the spec delta and the Related Plans backlink.
5. Run the verification gates below.

Stop and re-plan if: the change needs `MultiQueueWatcher` edits; a test needs a
production seam; or any existing startup-feedback test fails for a reason other
than the restated `publish_before_interest` case.

## Testing plan

Real broker, real Manager, real spawned producers; nothing in the queue,
waiter or process path is mocked. The spy delegates to SimpleBroker's real
factory.

- New: the RED test above (both backends).
- Existing promptness proofs, unchanged except task 3:
  `test_positive_startup_snapshot_wakes_admission_without_maintenance_tick`,
  `test_startup_window_overlaps_but_bounds_unobserved_processes`,
  `test_startup_handoff_reactivates_pending_public_queue`,
  `test_missing_launch_result_retains_finite_path_and_late_commit_is_idempotent`.
- Neighbours that inspect interest handles: `tests/core/test_admission_recovery.py`.
- Wait-path and watcher contracts: `tests/core/test_manager.py`,
  `tests/tasks/test_multiqueue_watcher.py`.

## Verification

```bash
./.venv/bin/python -m pytest tests/core/test_manager_family_maintenance.py tests/core/test_admission_recovery.py -n 4 -q
./.venv/bin/python -m pytest tests/core/test_manager.py tests/tasks/test_multiqueue_watcher.py -n 4 -q
./bin/pytest-pg tests/core/test_manager_family_maintenance.py tests/core/test_admission_recovery.py tests/tasks/test_multiqueue_watcher.py
./.venv/bin/ruff check .
./.venv/bin/ruff format --check <touched files>
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
```

Runtime signal (coordinator benchmark): with trace logging, the count of
`manager_phase_timing` spans for `wait_setup` stays one per wait, but their
total time should fall by roughly the share of waits that rebuilt twice per
launch. A falling `create_activity_waiter_for_queues` call count per launch is
the direct signal.

## Rollback

Revert the commit. No queue, payload, checkpoint or persisted format changes;
old and new Managers interoperate because interest is process-local.

## Out of scope

- SimpleBroker changes (below).
- The per-wake cursor peeks in `_has_pending_observation` (about 2% of wall).
- Fresh brokers opened per reap (`Manager._record_reaped_admission` calls
  `read_task_state_snapshot` without a broker): owned by the reaping track.
- Any `MultiQueueWatcher` change (Taut vendors that file).

## SimpleBroker follow-ups (for the SimpleBroker owner; not gated on Weft)

1. Resolve each entry-point backend plugin once per process.
   `simplebroker/_backend_plugins.py::get_backend_plugin` (via
   `_load_entry_point_plugin`) rescans `importlib.metadata.entry_points()` on
   every call. It is reached from `BrokerTarget.plugin`,
   `Queue._activity_waiter_identity` (once per queue per
   `create_activity_waiter_for_queues` call, and from `Queue.backend_name`),
   `DBConnection.__init__`, and `_broker_session._target_parts`. Measured
   1.66 ms per PostgreSQL lookup versus 1 µs for the built-in SQLite plugin.
   Memoizing the loaded plugin per name (process-local) removes the cost from
   every path.
2. Optional, larger: a multi-queue activity waiter whose membership can be
   updated in place (register added names before unregistering removed ones,
   keeping the version baselines of retained names). With it, a watcher could
   change notification interests without constructing a new waiter, and this
   plan's per-launch construction would disappear.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Fresh-eyes review

Author pass after implementation (not independent):

- Premise: re-checked that no public SimpleBroker API changes waiter
  membership in place and that [SB-0.4] forbids backend listener calls; the
  corrected scope stands.
- Correctness: the only new reachable effect is a selected launch's queue in
  the waiter before its receipt (installed by the first wait after selection;
  the child may already have published). A publication in that interval can return
  one wait, which reads no cursor for that launch and continues; the receipt
  turn's `_reconcile_launch_now` reads it. The active-launch exclusion in
  `_has_pending_observation` is load-bearing (without it, the turn reconciled
  the launch before its receipt); the restated test case fails without it.
- Bound: interests stay at most W because window occupancy already counts
  selected launches. Failure before process creation releases through the
  existing `_release_startup_feedback` path.
- Overengineering check: two edits in one module, no new state, constant,
  helper or seam.
- Limit: in a non-window-bound burst (fewer launches than the window), each
  launch still costs two constructions (add at selection, remove at
  settlement); only SimpleBroker can make those cheap.

## Execution evidence

- Base `08c2f87b`. Probe (scratch test, deleted; counting spy around the real
  factory, `provide_payload` children, real Manager): SQLite 9 children 14
  constructions before, 10 after; PostgreSQL 9 children 15 before, 14 after
  (fast receipts already coalesce some swaps); 4 children on PostgreSQL 8
  before and after (window never full).
- RED: `test_settlement_and_admitted_launch_share_one_waiter_rebuild` failed
  on base on both backends with two constructions (removal, then addition).
- Sensitivity controls (temporary edits, restored and verified by diff):
  restoring the base active-launch skip in `_activity_wait_queues` fails the
  new test and all five `test_positive_startup_snapshot_wakes_...` cases
  (`feedback.queue` is `None` after selection); removing the active-launch
  exclusion in `_has_pending_observation` fails the two
  `publish_before_interest` cases (cursor advanced before the receipt).
- After review finding F2, the new test holds the admitted launch's receipt.
  Rerun of the first control on the strengthened test: it fails on SQLite and
  on PostgreSQL with a single removal-only construction where the swap is
  expected (the base code excludes the still-active admitted launch). The
  second control was rerun on the revised `publish_before_interest` cases:
  both fail at `feedback.publication_id is None`.
- Gates on the first implementation tree (`eccbaf9c`): SQLite
  `tests/core/test_manager_family_maintenance.py tests/core/test_admission_recovery.py tests/core/test_manager.py tests/tasks/test_multiqueue_watcher.py -n 4`:
  475 passed, 5 skipped (PostgreSQL-only). PostgreSQL `./bin/pytest-pg` on
  the same four files: 471 passed, 12 skipped. `tests/specs`: 165 passed.
  `ruff check .`, `ruff format --check` on touched Python files, the repo
  mypy command, `bin/check-doc-paths`, and `git diff --check` pass.
- Gates after the review fixes: SQLite
  `tests/core/test_manager_family_maintenance.py tests/core/test_admission_recovery.py -n 4`:
  75 passed. PostgreSQL `./bin/pytest-pg tests/core/test_manager_family_maintenance.py`:
  43 passed. `tests/specs`: 165 passed. `ruff check .`, `ruff format --check`,
  the repo mypy command, `bin/check-doc-paths`, and `git diff --check` pass.

## Review and execution record

Tier 1, Codex (codex exec -s read-only, reasoning effort high), 2026-11-08,
scoped-change review of plan+implementation at eccbaf9c against 08c2f87b.
Verdict: no blocker.

| ID | Severity | Finding | Disposition |
| --- | --- | --- | --- |
| F1 | P3 | Registration is not guaranteed before process creation: `_launch_child_task` starts the launch worker before the next wait installs the new membership, so a child can publish before interest exists. The receipt's exact read covers that safely, but the spec (03 [MA-1.8]), the difference-register row, and the claim that `publish_before_interest` "cannot occur" overstate the ordering. | Accepted, fixed: [MA-1.8] text, the plan's goal, design item 1, register row 1, spec-delta block and task 3, and the `Manager._activity_wait_queues` docstring now say selection makes the startup eligible, the next wait installs interest, and the receipt-time read covers an earlier publication. The `publish_before_interest` case is kept with its reachable ordering: the child publishes before the next wait installs interest while the receipt is held; the test asserts no interest yet, then interest installed, no cursor read or handoff before the receipt, and settlement in the receipt turn. |
| F2 | P3 | `test_settlement_and_admitted_launch_share_one_waiter_rebuild` fences publication but not the admitted launch's receipt; if the receipt is handled before waiter setup, the baseline also swaps membership in one construction, so the test can pass reverted code under a legal fast-receipt schedule. | Accepted, fixed: the admitted launch's receipt is held by a test-owned `threading.Event` until the wait setup after the settling turn has run while that launch is still active; the test asserts the single swap construction there, releases the receipt, drives the receipt turn, and asserts no second construction. The control (base active-launch skip restored) now fails on both backends with a removal-only construction. |

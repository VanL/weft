# Task Monitor Cleanup Composition Refactor Plan

Status: completed
Source specs: docs/specifications/00-Quick_Reference.md; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13], [OBS.16], [OBS.17]
Superseded by: none

## 1. Goal

Refactor the bounded TaskMonitor cleanup implementation into obvious,
composable pieces without changing runtime behavior. The TaskMonitor service
should own orchestration: when to scan, which queues to include, whether the
cycle is report-only or destructive, and how to publish health/cycle stats.
Pruning should own reusable policy and deletion primitives. Task-log collation
should own lifecycle grouping and summaries, not deletion. The immediate goal
is a cleaner boundary, not a new cleanup product.

This plan follows up
`docs/plans/2026-05-12-bounded-task-monitor-cleanup-policy-plan.md`, which
landed the bounded behavior but left policy selection, FIFO scanning, task-log
collation, and action application in one module. That was acceptable for the
first slice. It is now too easy for future work such as `collate-log-delete`
to add logging, pruning, and monitor-service concerns in the same file.

## 2. Source Documents

- `docs/specifications/00-Quick_Reference.md`: current `WEFT_TASK_MONITOR_*`
  configuration and built-in processor summary. This refactor should not
  change user-facing config, but update implementation notes if names move.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]: queue lifecycle,
  TaskMonitor cleanup, runtime evidence, and exact-message cleanup rules.
  Update implementation mapping so it names the new TaskMonitor cleanup
  runner, pruning policies, and task-log collation module.
- `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.16],
  [OBS.17]: operational-output and exact-message pruning invariants. Keep
  these synchronized with the fact that collation creates operational
  summaries and only later actions may delete rows.
- `docs/plans/2026-05-12-bounded-task-monitor-cleanup-policy-plan.md`:
  completed prior plan. Its behavior remains intended. This refactor should
  preserve its test suite and observable semantics while moving code to clearer
  ownership boundaries.
- `docs/agent-context/runbooks/writing-plans.md`: plan format and
  zero-context implementation standard.
- `docs/agent-context/runbooks/hardening-plans.md`: required because this
  touches destructive cleanup over runtime queues.
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`: required
  because the refactor creates reusable cleanup composition seams.

## 3. Context and Current Shape

Current TaskMonitor cleanup is concentrated in:

- `weft/core/pruning/task_monitor_cleanup.py`
  - scans configured queues with `iter_queue_entries`;
  - decodes queue rows and validates queue-specific payload shapes;
  - selects malformed candidates;
  - selects older-than candidates;
  - collates task-log lifecycle rows by TID;
  - builds operational collation summaries;
  - applies exact deletes through `apply_exact_prune_candidates`;
  - returns the current cleanup result and queue stats.
- `weft/core/tasks/task_monitor.py`
  - owns service lifecycle, heartbeat, wakeups, scheduling, control snapshots,
    and operational logs;
  - calls `run_task_monitor_cleanup()` for built-in `delete` and
    `report_only`;
  - still owns custom `module:function` processor dispatch for read-only
    lifecycle candidates.
- `weft/core/pruning/apply.py`
  - owns canonical exact-message delete application. Reuse this. Do not create
    a second exact-delete implementation.
- `weft/core/task_monitoring.py`
  - owns read-only lifecycle evidence candidates and custom processor request
    types. It should stay out of destructive cleanup.

The implementation problem is not behavior. It is location and coupling:
`task_monitor_cleanup.py` currently mixes service-runner orchestration, reusable
row policies, task-log grouping, and action application. That makes the next
slice ambiguous. For example, `collate-log-delete` should be "collate,
log, then delete after logging succeeds", but the current file shape invites
adding the logger inside a pruning function.

## 4. Target Ownership Model

Use these boundaries. Do not drift from them without stopping for review.

### TaskMonitor Orchestration

Owner: `weft/core/tasks/task_monitor.py` and a small helper module under
`weft/core/tasks/`.

Responsibilities:

- decide when a cleanup cycle runs;
- build the queue cleanup configuration from loaded Weft config;
- decide `apply=True` for `delete` and `apply=False` for `report_only`;
- pass `exclude_tids=(self.tid,)`;
- call reusable policy/collation/action functions;
- translate the cleanup result into TaskMonitor health fields and serve logs.

Recommended new file:

- `weft/core/tasks/task_monitor_cleanup.py`

This file is still "in the TaskMonitor" ownership boundary, but keeps
`TaskMonitorTask` from becoming a long cleanup implementation class.

### Queue Window Primitives

Owner: neutral core code, not pruning and not TaskMonitor.

Responsibilities:

- represent one FIFO queue row read from a bounded window;
- represent an exact message reference `(queue, message_id)` that either
  pruning or future logging actions can use;
- provide only small helpers that are genuinely queue-window mechanics, not
  cleanup policy.

Recommended file:

- `weft/core/queue_window.py`

Do not put task-log lifecycle semantics, delete eligibility, or logging
behavior here. This module exists only if it prevents future logging code from
depending on pruning types.

### Pruning Policies and Delete Helpers

Owner: `weft/core/pruning/`.

Responsibilities:

- exact-message delete helpers that reuse `apply_exact_prune_candidates`;
- cleanup candidate data types that are specifically about deletion/reporting
  eligibility;
- generic delete-malformed policy;
- generic delete-older-than policy;
- queue-owned schema guards so reusable policies cannot accidentally delete
  malformed user payload rows.

Recommended files:

- `weft/core/pruning/policies/__init__.py`
- `weft/core/pruning/policies/malformed.py`
- `weft/core/pruning/policies/older_than.py`

Keep these modules small. Do not introduce a registry, plugin system, or class
hierarchy unless the implementation duplicates enough code to force it. Simple
typed functions are preferred.

### Task-Log Collation

Owner: task-log evidence code, not pruning.

Responsibilities:

- group rows by task ID within a bounded scan window;
- find completed lifecycle groups;
- return message IDs and operational summaries;
- perform no delete, no archive, and no logging side effects.

Recommended new file:

- `weft/core/task_log_collation.py`

If an existing `weft/core/task_log/` package appears before implementation,
use that package instead. Do not create a package solely for one file.

### Actions After Selection

Actions are "do something with selected exact message IDs." They are reusable,
but they are not all pruning. Do not put a future logger action in
`weft/core/pruning/`.

Initial actions:

- report-only action: returns selected IDs and stats, deletes nothing;
- delete action: delegates exact deletion to `apply_exact_prune_candidates`.

Future action shape, not part of this refactor:

- log-then-delete action: writes a task lifecycle summary, then calls the
  delete action only after logging succeeds.

This plan may add the minimal callable seam needed to make that future action
obvious. Prefer a private callable protocol in
`weft/core/tasks/task_monitor_cleanup.py` until a second non-delete action
exists. Do not create a global action framework for a single caller. This plan
must not implement `collate-log-delete` logging.

## 5. Invariants and Constraints

- Behavior must stay compatible with the TaskMonitor cleanup plan:
  - malformed rows in explicitly owned queues can be selected;
  - old `weft.state.tid_mappings` rows can be selected by age;
  - task-log malformed runs before task-log collation;
  - task-log collation runs before broad older-than cleanup;
  - broad older-than task-log cleanup runs only when no completed group was
    collated in that queue pass;
  - exact deletes happen only after the scan window is closed;
  - repeated cleanup cycles continue reducing backlog in bounded windows.
- The default TaskMonitor processor remains `delete`; `report_only` remains
  non-destructive; `jsonl_then_delete` remains fail-closed.
- The TaskMonitor must not delete active work queues, spawn requests, manager
  control queues, user payload queues, ambiguous task-local evidence, claimed
  outbox residue, or candidates without exact message IDs.
- Queue eligibility is configuration, but the configuration must include the
  queue's ownership contract. A generic malformed policy must not be globally
  applicable to arbitrary user queues by accident.
- The refactor must preserve PING/control snapshot fields:
  `last_prune_records_scanned`, `last_cleanup_queue_stats`, `last_processed`,
  `last_deleted`, and `last_reported`.
- Keep `weft/core/task_monitoring.py` non-destructive. It may expose read-only
  lifecycle candidates for custom processors, but it must not regain built-in
  delete/report-only processor functions.
- No public CLI, env var, queue name, or persisted payload format change is
  part of this refactor.
- No new dependency.

## 6. Required Reading Before Editing

Read these first:

- `weft/core/tasks/task_monitor.py`
- `weft/core/pruning/task_monitor_cleanup.py`
- `weft/core/pruning/apply.py`
- `weft/core/task_monitoring.py`
- `tests/core/test_task_monitor_cleanup.py`
- `tests/tasks/test_task_monitor.py`
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]
- `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.16], [OBS.17]

Comprehension checks before editing:

- Which function currently guarantees deletes are applied after generator
  iteration, not during it?
- Which module owns service heartbeat and wakeup scheduling?
- Which current tests prove repeated collate can reduce multiple completed
  lifecycle groups in one bounded window?
- Which result fields are surfaced in TaskMonitor PING responses?
- Which exact-delete helper must all destructive paths reuse?

If any answer is unclear, stop and read the files again. Do not begin by
moving code mechanically.

## 7. Task Breakdown

### Task 1: Lock Existing Behavior Before Moving Code

Files to modify:

- `tests/core/test_task_monitor_cleanup.py`
- `tests/tasks/test_task_monitor.py`

Actions:

1. Add or adjust tests so the current behavior is explicit before moving code.
2. Keep real broker queues. Do not mock `Queue`, `iter_queue_entries`, or
   `apply_exact_prune_candidates`.
3. Add assertions that:
   - report-only selects but does not delete;
   - delete removes exact IDs only;
   - malformed beats older-than;
   - task-log collate beats broad older-than;
   - repeated cleanup cycles keep making progress;
   - TaskMonitor PING still exposes cleanup stats after a built-in cycle.

Expected command:

```bash
uv run pytest tests/core/test_task_monitor_cleanup.py tests/tasks/test_task_monitor.py -q
```

Stop if:

- the test needs to mock the cleanup runner to prove behavior. That would hide
  the contract this refactor is meant to preserve.

### Task 2: Introduce Neutral Queue-Window Models

Files to add or modify:

- `weft/core/queue_window.py`
- `weft/core/pruning/task_monitor_cleanup.py`

Actions:

1. Move only reusable queue-window data structures from
   `task_monitor_cleanup.py` into `queue_window.py`.
2. Good candidates:
   - raw queue row with `queue`, `body`, `message_id`;
   - exact message reference with `queue` and `message_id`;
   - a small bounded-window scan helper only if it keeps generator use
     centralized and still allows the TaskMonitor runner to own orchestration.
3. Do not move pruning-specific candidate classes, cleanup stats, malformed
   policy decisions, or task-log lifecycle summaries into the neutral module.
4. Do not move `TaskMonitorRuntimeConfig`, `TaskMonitorProcessorRequest`, or
   read-only lifecycle candidates from `task_monitoring.py`.

Tests:

- Existing TaskMonitor cleanup tests should still pass after import updates.

Stop if:

- moving queue-window models requires broad changes to foreground prune modules. This
  refactor should not rewrite foreground prune internals.

### Task 3: Extract Generic Malformed and Older-Than Policies

Files to add or modify:

- `weft/core/pruning/policies/__init__.py`
- `weft/core/pruning/policies/malformed.py`
- `weft/core/pruning/policies/older_than.py`
- `weft/core/pruning/models.py` if deletion-specific candidate/result models
  need a home
- `weft/core/pruning/task_monitor_cleanup.py`
- `tests/core/test_task_monitor_cleanup.py` or a new focused test file

Actions:

1. Extract a malformed policy that receives decoded rows plus queue-owned
   policy config and returns deletion/report candidates.
2. Extract an older-than policy that receives decoded rows, `now_ns`,
   `min_age_seconds`, a candidate class, a reason, and optional TID/payload
   extraction callbacks.
3. Keep schema validation separate from the generic policy:
   - queue config decides what counts as malformed for that queue;
   - the malformed policy only turns rows with `malformed_reason` into exact
     candidates.
4. Keep policy functions pure. They should not open queues, delete rows,
   write logs, or inspect TaskMonitor state.
5. Do not introduce a global "delete malformed everywhere" switch.
6. Keep deletion-specific candidate/result types in pruning. Keep generic
   message references in `weft/core/queue_window.py`.

Tests:

- Add focused tests with real decoded row values or real queue rows proving:
  - malformed policy can apply to more than one explicitly owned queue;
  - older-than stops at the first too-young FIFO row when configured that way;
  - older-than can skip already-claimed rows.

Stop if:

- the extraction needs a large class hierarchy. Use plain functions until real
  duplication forces something stronger.

### Task 4: Extract Task-Log Collation as Grouping, Not Pruning

Files to add or modify:

- `weft/core/task_log_collation.py`
- `weft/core/pruning/task_monitor_cleanup.py`
- `tests/core/test_task_monitor_cleanup.py` or
  `tests/core/test_task_log_collation.py`

Actions:

1. Move task-log lifecycle grouping into `task_log_collation.py`.
2. Define a return value that names what it is, for example:
   `CollatedMessageGroup`.
3. The group must contain:
   - `tid`;
   - exact message IDs or row references;
   - first message ID;
   - terminal message ID;
   - terminal event/status;
   - row count;
   - enough payload metadata for operational summaries.
4. The collator must not:
   - delete;
   - call pruning apply helpers;
   - know whether the caller is in report-only or delete mode;
   - write operational logs;
   - know TaskMonitor heartbeat/scheduling state.
5. Preserve the current semantics:
   - choose the oldest unclaimed eligible anchor;
   - skip excluded TIDs;
   - skip malformed/claimed rows;
   - return no group and a stop reason when anchor is too young;
   - return no group and the skipped TID when no terminal appears so the
     caller can continue looking for later completed groups.

Tests:

- Add direct collator tests if the extracted function is not already covered
  clearly by existing TaskMonitor cleanup tests:
  - A start, B start, A complete returns A group only;
  - A start, B start, B complete skips A and returns B group;
  - no terminal returns skipped TID, not delete candidates;
  - young anchor returns no group.

Stop if:

- the collator starts importing `weft.core.pruning.apply` or
  `weft.core.tasks.task_monitor`. That is the wrong direction.

### Task 5: Move the Policy Runner Into the TaskMonitor Boundary

Files to add or modify:

- `weft/core/tasks/task_monitor_cleanup.py`
- `weft/core/tasks/task_monitor.py`
- `weft/core/pruning/task_monitor_cleanup.py`
- `tests/tasks/test_task_monitor.py`
- `tests/core/test_task_monitor_cleanup.py`

Actions:

1. Create `weft/core/tasks/task_monitor_cleanup.py` as the TaskMonitor-owned
   cleanup orchestrator.
2. Move from `task_monitor_cleanup.py` the code that:
   - chooses configured queues;
   - scans bounded FIFO windows;
   - invokes queue policies in order;
   - invokes task-log collation repeatedly;
   - chooses report-only vs delete action;
   - assembles TaskMonitor cleanup results and queue stats.
3. Keep exact-delete application in pruning:
   - the TaskMonitor cleanup runner may call an action function;
   - the delete action must still delegate to `apply_exact_prune_candidates`.
4. Rename public types to reflect ownership:
   - `TaskMonitorCleanupConfig` -> `TaskMonitorCleanupConfig`;
   - the old result type -> `TaskMonitorCleanupResult`.
   Keep compatibility aliases only if import churn would be unsafe within the
   active branch. Do not leave stale names indefinitely.
5. Update `TaskMonitorTask._run_task_monitor_cleanup_cycle()` to call the new
   TaskMonitor cleanup runner.
6. Delete `weft/core/pruning/task_monitor_cleanup.py` if it has no remaining
   cohesive responsibility. If generic code remains, move it to clearly named
   modules from earlier tasks instead of leaving a partial compatibility shell.

Tests:

- Existing TaskMonitor tests pass.
- Existing TaskMonitor cleanup behavior tests are moved/renamed to match the new
  owner, for example `tests/core/test_task_monitor_cleanup.py`.

Stop if:

- `TaskMonitorTask` itself becomes a long queue-policy implementation. Use the
  helper module under `weft/core/tasks/` to keep service orchestration readable.
- pruning modules import `TaskMonitorTask` or TaskMonitor runtime config.

### Task 6: Introduce an Action Interface Without Implementing Logging

Files to add or modify:

- `weft/core/tasks/task_monitor_cleanup.py`
- `weft/core/pruning/apply.py` only if an adapter around the existing exact
  delete helper is needed
- tests near the runner/action seam

Actions:

1. Define the smallest callable shape needed by the TaskMonitor cleanup runner.
   It should receive selected exact candidates or collated groups and return
   per-message results.
2. Implement:
   - report-only action;
   - delete action backed by `apply_exact_prune_candidates`.
3. Keep future logging explicit but unimplemented:
   - `jsonl_then_delete` remains fail-closed in `TaskMonitorTask`;
   - do not add a logger action in this refactor;
   - do not create archive files or new runtime log files.
4. Keep the action protocol private to `task_monitor_cleanup.py` unless a
   second real action lands in the same change. Do not add a global action
   framework for one delete action.
5. Make the future shape obvious in type names and docstrings:
   an action can "do something with these message IDs" and deletion is only
   one action.

Tests:

- Use real queues for delete action.
- It is acceptable to use an in-memory fake action only to test runner
  sequencing, for example "if action reports failure, deletion stats are not
  counted as successful." Do not use fake actions as the only destructive-path
  proof.

Stop if:

- this starts implementing `collate-log-delete`. That is a separate behavior
  change and needs its own plan or plan amendment.

### Task 7: Update Docs and Remove Superseded Names

Files to modify:

- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/00-Quick_Reference.md` only if import/module names are
  mentioned there
- `docs/plans/README.md` when marking this plan completed
- tests and imports referencing old names

Actions:

1. Update implementation mappings to name:
   - TaskMonitor cleanup runner module;
   - pruning policy modules;
   - task-log collation module;
   - exact-delete action helper.
2. Remove stale mentions of `task_monitor_cleanup.py` once it is deleted.
3. Run `rg` for old type and module names:

```bash
rg -n "bounded_cleanup|run_bounded_task_monitor_cleanup|BoundedCleanup" weft tests docs/specifications
```

4. Keep historical completed plans as history unless they contain active
   implementation mapping that would mislead current work.

Stop if:

- docs begin describing new logging or archive behavior. This refactor does
  not ship that.

## 8. Test Plan and Gates

Use red-green TDD for each extraction when possible. Start by strengthening or
moving tests before moving code.

Required focused tests:

```bash
uv run pytest tests/core/test_task_monitor_cleanup.py tests/tasks/test_task_monitor.py -q
```

If the file remains named `test_task_monitor_cleanup.py` during an
intermediate slice, use that current path instead.

Required adjacent tests:

```bash
uv run pytest tests/core/test_task_monitoring.py tests/commands/test_task_monitor.py tests/commands/test_runtime_prune.py tests/commands/test_retention_prune.py tests/cli/test_cli_system.py -q
```

Required static gates:

```bash
uv run ruff check weft tests
uv run mypy weft extensions/weft_docker extensions/weft_macos_sandbox
uv run pytest tests/specs/test_plan_metadata.py tests/specs/test_test_audit_policy.py tests/system/test_constants.py -q
```

Regression probes:

- `rg -n "def report_only_processor|def delete_processor|run_bounded_task_monitor_cleanup|BoundedCleanup" weft tests`
- `rg -n "apply_exact_prune_candidates" weft/core` to confirm exact deletes
  still go through the canonical helper.

What not to mock:

- real broker queue rows;
- `iter_queue_entries`;
- exact delete behavior;
- TaskMonitor PING/control snapshot fields.

What may be faked:

- a future action callback shape, but only in narrow sequencing tests and not
  as proof that destructive delete works.

## 9. Rollout and Rollback

Rollout is code-only and behavior-preserving:

1. Land tests that lock current behavior.
2. Move models/policies/collation/action seams in small slices.
3. Keep `TaskMonitorTask` using the new TaskMonitor cleanup runner.
4. Remove old module and type names only after all call sites are moved.

Rollback:

- Since this refactor should not change queue names, payload formats, or env
  vars, rollback is a normal code revert.
- There is no data migration.
- If a production issue appears, set `WEFT_TASK_MONITOR_PROCESSOR=report_only`
  to keep the service observable while preventing deletion, then revert.

One-way doors:

- None intended. If implementation introduces new persisted log/archive
  formats or changes delete eligibility, stop. That is no longer this plan.

## 10. Out of Scope

- Implementing `collate-log-delete`.
- Adding archive files, JSONL lifecycle summaries, or new operational logs.
- Changing queue names or TaskMonitor config env vars.
- Changing foreground `weft system prune` behavior.
- Generalizing pruning into a plugin framework.
- Moving foreground runtime/retention prune code into the new policy modules
  unless a tiny shared helper eliminates direct duplication without changing
  behavior.

## 11. Review Requirements

This plan needs independent review before implementation because it creates
new reusable cleanup composition seams.

Suggested review prompt:

```text
Read docs/plans/2026-05-12-task-monitor-cleanup-composition-refactor-plan.md,
docs/specifications/05-Message_Flow_and_State.md [MF-5],
docs/specifications/07-System_Invariants.md [OBS.13], [OBS.16], [OBS.17],
weft/core/tasks/task_monitor.py, weft/core/pruning/task_monitor_cleanup.py, and
tests/core/test_task_monitor_cleanup.py. Do not implement anything.
Could you implement this confidently and correctly? Look for boundary mistakes,
missing tests, accidental behavior changes, and over-abstraction.
```

Implementation should also receive review after Task 5, before deleting
compatibility names or old modules.

## 12. Self-Review Notes

Risks checked while writing this plan:

- Bad direction: putting all code under `weft/core/pruning/task_monitor.py`.
  Rejected because it keeps runner and policy coupled and makes future
  `collate-log-delete` ambiguous.
- Bad direction: putting all scan/policy logic directly inside
  `TaskMonitorTask`. Rejected because it bloats the service class and makes
  policy tests harder.
- Bad direction: making malformed deletion globally available. Rejected
  because malformed rows are only deletable for explicitly Weft-owned queues
  whose cleanup config says malformed rows are disposable.
- Bad direction: putting neutral queue-window or future logging action types
  under `weft/core/pruning/`. Rejected because future logging should not depend
  on pruning concepts.
- Bad direction: implementing the future logger action as part of the refactor.
  Rejected because this plan is behavior-preserving.

The plan remains aligned with the discussion: policy runner/orchestration is
inside the TaskMonitor boundary; actual pruning policies and exact deletion
helpers are in pruning; task-log collation is a grouping primitive that can
feed pruning or future logging actions.

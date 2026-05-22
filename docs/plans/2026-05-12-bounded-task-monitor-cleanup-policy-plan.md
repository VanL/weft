# Bounded Task Monitor Cleanup Policy Plan

Status: completed
Source specs: docs/specifications/00-Quick_Reference.md; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13], [OBS.16], [OBS.17]; docs/specifications/12-Pipeline_Composition_and_UX.md [PL-5.5]
Superseded by: none

## 1. Goal

Replace the manager-supervised `TaskMonitor` cleanup path with a bounded
FIFO queue-policy runner that can delete malformed rows, delete rows older than
an explicit queue policy allows, and collate task-log rows by TID before
deleting a completed task's lifecycle evidence. The important change is not a
new cleanup product. It is making the existing cleanup implementation cheap,
predictable, and convergent under large broker backlogs.

The current implementation can spend a whole monitor cycle building global
candidate sets before applying a batch limit. This plan changes that shape:
scan a bounded FIFO window, let explicit queue policies claim exact message IDs,
then delete only those exact IDs after the scan window is closed.

## 2. Source Documents

- `docs/specifications/00-Quick_Reference.md`: queue catalogue and
  `WEFT_TASK_MONITOR_*` configuration table. Update this when new cleanup
  semantics or counters become user-visible.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]: task-log
  observation, task-monitor cleanup, and queue lifecycle rules. This section
  currently says `weft.log.tasks` is durable lifecycle evidence and also
  describes the TaskMonitor cleanup contract. Update it before or with the
  implementation so it states that `weft.log.tasks` is runtime lifecycle
  evidence used for status/result reconstruction while retained. It is not
  legal, forensic, or audit evidence. Its retention requirements are
  operational and cleanup policy may remove it.
- `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.16],
  [OBS.17]: operational-output boundaries and exact-message pruning invariants.
  Update these invariants for the new background cleanup policy. The key
  change is that malformed rows in explicitly owned cleanup queues are
  deletable, while malformed user payload queues are not.
- `docs/specifications/12-Pipeline_Composition_and_UX.md` [PL-5.5]: pipeline
  lifecycle-event wording that previously described task logs as audit
  evidence. Keep this aligned with the operational runtime-evidence boundary.
- `docs/plans/2026-05-09-prune-path-unification-plan.md`: historical plan that
  unified foreground prune and TaskMonitor cleanup. It remains useful context,
  but production showed that sharing the full foreground prune candidate
  builders is too heavyweight for the live monitor path.
- `docs/agent-context/runbooks/writing-plans.md`: plan format and
  zero-context implementation standard.
- `docs/agent-context/runbooks/hardening-plans.md`: required because this is
  destructive cleanup over runtime queues.
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`: required
  independent review loop for this boundary-crossing change.

## 3. Current Shape

The supervised monitor currently flows through these files:

```text
weft/core/tasks/task_monitor.py
  TaskMonitor._run_monitor_cycle()
  -> _scan_task_log_candidates()
  -> _process_monitor_candidates()
  -> _run_canonical_prune_cycle()

weft/core/tasks/task_monitor.py _run_canonical_prune_cycle()
  -> weft/core/pruning/runtime.py run_runtime_prune_for_context()
  -> weft/core/pruning/retention.py run_retention_prune_for_context()
```

The problem is in the last step. `runtime.py` and `retention.py` are good
foreground maintenance engines, but they are not a cheap background loop:

- `run_runtime_prune_for_context()` builds candidates for all configured
  runtime-state queue groups before applying `limit`.
- `run_retention_prune_for_context()` reads `weft.log.tasks`, reduces all task
  evidence, fans out to task-local queues for retention, and only then applies
  `limit`.
- Apply mode rebuilds fresh candidates before deleting, so a destructive
  monitor pass can scan the backlog twice.
- `iter_queue_json_entries()` skips invalid JSON. That is correct for many
  readers, but it cannot implement "delete malformed" because malformed rows
  never reach the policy layer.
- SimpleBroker exposes generator-style peeking, but the implementation must
  not delete while iterating. Deleting during an offset-backed generator can
  skip rows. The safe shape is: scan a bounded window, collect exact message
  IDs, close the scan, then call the shared exact-delete helper.

The shared exact-delete helper already exists:

- `weft/core/pruning/apply.py` `apply_exact_prune_candidates()`

Reuse it. Do not create a second delete helper.

## 4. Files To Touch

Core implementation:

- `weft/_constants.py`
  - Keep all production constants and env names here.
  - Reuse existing constants before adding new ones.
- `weft/core/pruning/bounded_cleanup.py` or
  `weft/core/pruning/task_monitor_cleanup.py`
  - New shared bounded policy runner. Pick one name and keep all first-slice
    cleanup policies in this one module. Do not split into a policy framework
    until duplication forces it.
- `weft/core/pruning/__init__.py`
  - Export only the public runner/types needed by `TaskMonitor`.
- `weft/core/tasks/task_monitor.py`
  - Replace built-in `delete` and `report_only` monitor processing with the
    bounded cleanup runner.
  - Do not call `run_runtime_prune_for_context()` or
    `run_retention_prune_for_context()` from the built-in monitor cleanup path
    after this change.
- `weft/core/task_monitoring.py`
  - Extend `TaskMonitorRuntimeConfig` only if the cleanup runner needs typed
    config fields. Do not move destructive cleanup into this module unless it
    remains command-neutral and below CLI/command layers.

Documentation:

- `docs/specifications/00-Quick_Reference.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/plans/README.md`

Tests:

- `tests/core/test_task_monitor_bounded_cleanup.py`
  - Add this file for the bounded cleanup runner.
  - Because all `tests/core` files are audited as shared, add the new path to
    `tests/conftest.py` `_SHARED_MODULES` if the audit requires it.
- `tests/tasks/test_task_monitor.py`
  - Add task-level integration coverage for `TaskMonitor.process_once()`.
- `tests/core/test_task_monitoring.py`
  - Update only if `TaskMonitorRuntimeConfig` or processor contracts change.
- `tests/system/test_constants.py`
  - This is already a guard. Run it after adding constants.
- `tests/specs/test_plan_metadata.py`
  - Run after adding this plan and updating the plan README.

## 5. Required Reading Before Editing

Read these in order:

1. `AGENTS.md`, especially the SimpleBroker and queue-source-of-truth sections.
2. `docs/specifications/05-Message_Flow_and_State.md` [MF-5] and the
   `Cleanup Boundary` subsection.
3. `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.16],
   [OBS.17].
4. `weft/core/tasks/task_monitor.py`
   `_run_monitor_cycle()`, `_process_monitor_candidates()`, and
   `_run_canonical_prune_cycle()`.
5. `weft/core/pruning/apply.py`.
6. `weft/helpers/__init__.py` `iter_queue_entries()` and
   `iter_queue_json_entries()`.
7. `weft/core/pruning/runtime.py` and `weft/core/pruning/retention.py`, but
   read them as foreground-maintenance context. Do not copy their global
   candidate-building shape into the monitor path.

Comprehension checks before editing:

- Can you explain why `iter_queue_json_entries()` cannot be used for malformed
  row deletion?
- Can you explain why `limit=1000` on the current foreground prune builders
  does not mean "scan at most 1000 broker rows"?
- Can you point to the one helper that all exact message deletes must use?

If the answer to any of those is no, do not edit yet.

## 6. Contract

### 6.1 Queue Policy Scope

This first slice supports only explicitly owned cleanup queues:

| Queue | Dump persistence today | First-slice policies |
| --- | --- | --- |
| `weft.state.tid_mappings` | No | delete malformed; delete rows older than runtime-state stale policy |
| `weft.log.tasks` | Yes in dumps today | delete malformed; collate and delete completed TID evidence; delete older than task-log retention policy |

"Malformed" is queue-specific:

- invalid JSON is malformed;
- JSON that decodes to a non-object is malformed;
- an object missing fields required for that queue's contract is malformed;
- an unknown but valid object in a queue where the contract explicitly allows
  future shapes is not automatically malformed unless the spec says it is.

Do not apply malformed cleanup to user payload queues such as `T{tid}.outbox`,
`T{tid}.inbox`, or arbitrary user-created queues. User output can be any
string. Treating it as malformed Weft JSON would be data loss.

### 6.2 Policy Ordering

Policy evaluation is deterministic and first-claim-wins.

For `weft.state.tid_mappings`:

1. malformed row policy;
2. older-than runtime-state policy.

For `weft.log.tasks`:

1. malformed row policy;
2. collate completed TID policy;
3. older-than task-log policy, but only when collate did not claim any
   completed lifecycle group in this cycle.

The most inclusive policy runs last. That matters because a task-log TID that
can be collated is also the seam for steady-state lifecycle summarization. Do
not let broad older-than deletion erase that structure before the collate
policy sees it. Collate runs one TID at a time, but it should repeat within
the bounded window: after it claims one completed lifecycle group, it advances
to the next oldest unclaimed eligible TID and tries again. If the oldest
eligible TID is old enough but has no terminal evidence in the window, skip
that TID as a collate anchor for this pass and continue looking for later
completed TIDs. A long-running task at the head of the queue must not block
steady-state cleanup of completed tasks behind it. Only after the collate
policy has found no completed lifecycle group should the broad older-than
task-log policy run.

### 6.3 FIFO Scan Semantics

Each queue scan uses a bounded FIFO window. The existing
`WEFT_TASK_MONITOR_BATCH_SIZE` should be the first-slice scan budget. Do not
add a second batch-size env var unless a test proves the existing one cannot
serve both the lifecycle scan and cleanup scan.

One queue pass ends when any of these is true:

- the queue generator is exhausted;
- the scan budget is reached;
- a policy returns a queue-stop decision.

The "first keep candidate" rule is not global. It is valid only when the keep
reason proves later FIFO rows cannot be eligible for the same policy. Example:
after malformed rows and collate claims are handled, an oldest well-formed
task-log row that is too young for the older-than policy can stop the queue
because later rows are younger. A row that is merely unsupported, ambiguous,
or missing terminal evidence does not prove later rows are ineligible.

### 6.4 Exact Delete Semantics

The runner must never delete while iterating a queue generator.

Required shape:

1. open the queue;
2. collect at most `batch_size` raw rows from `iter_queue_entries()`;
3. close or finish the generator window;
4. build exact delete candidates by `(queue_name, message_id)`;
5. call `apply_exact_prune_candidates()`;
6. return queue-level stats.

Deletion must be by exact message ID only. Missing rows during apply are
idempotent skips, not fatal corruption, because another cleanup pass may have
already removed the same row.

### 6.5 Collate And Delete Semantics

The first-slice collate policy is deliberately narrow:

- It runs only on `weft.log.tasks`.
- It anchors on the oldest well-formed task-log row in the scan window that has
  a string `tid` and is not in `exclude_tids`.
- It may claim the anchor TID only when the anchor row is old enough for the
  task-log operational retention policy. Do not delete just-completed runtime
  evidence before ordinary status/result/debug windows have had a chance to use
  it.
- It tracks one anchor TID at a time, then repeats for the next oldest
  unclaimed eligible TID while the same bounded scan window still has work.
  Do not build a full `tid -> rows` map for every TID in the window.
- It scans forward within the same bounded window.
- It records every message ID in the window for that anchor TID.
- If it sees a terminal event for the anchor TID, it claims the collected
  message IDs for deletion.
- If it does not see a terminal event before the window ends, it claims
  nothing for that TID, marks that TID as skipped for this pass, and continues
  looking for later completed TIDs. The older-than policy may claim rows later
  in the policy order only if the collate policy claimed no completed
  lifecycle group in this pass.
- Each claimed lifecycle group should produce an operational collation summary
  in the cleanup result so `TaskMonitor` can log how many task lifecycle
  groups were reduced. This is not a durable archive record.

Terminal proof should reuse the existing terminal constants in
`weft/_constants.py`: `TERMINAL_TASK_EVENTS` and `TERMINAL_TASK_STATUSES`.
Do not invent a second terminal-event list.

This policy may miss very long-running tasks or tasks whose terminal row lands
outside the current window. That is acceptable. The policy is bounded by
design, but it must not let one such task prevent cleanup of later completed
lifecycles in the same window. Older-than cleanup remains the broad fallback
when no completed lifecycle group is available to collate.

### 6.6 Age Semantics

Use the SimpleBroker message ID as the row timestamp for age checks, matching
the existing pruning code.

First-slice defaults:

- runtime-state age for `weft.state.tid_mappings`: derive from the existing
  runtime stale timeout policy. The user-facing rule is "at least 2x older than
  the stale timeout." If the implementation cannot identify one unambiguous
  stale-timeout constant, stop and update this plan or the spec before adding a
  magic number.
- task-log age for `weft.log.tasks`: reuse
  `RETENTION_PRUNE_DEFAULT_MIN_AGE_SECONDS` unless the spec update introduces
  a more explicit operational log retention constant.

All constants or env vars must live in `weft/_constants.py`. The constants
test will fail if production constants are introduced elsewhere.

### 6.7 Report-Only Semantics

`WEFT_TASK_MONITOR_PROCESSOR=report_only` must run the same bounded policy
selection, but it must not delete rows. It should return the same processed
candidate count as delete mode and set `reported` instead of `deleted`.

`jsonl_then_delete` remains fail-closed unless a separate plan lands the
logging-before-delete callback. Do not silently implement it here.

## 7. Invariants And Constraints

- Do not change task execution, manager startup, service reconciliation,
  autostart, or public `weft run` behavior.
- Do not change queue names.
- Do not change TID format or task state transitions.
- Do not mutate `TaskSpec.spec` or `TaskSpec.io`.
- Do not reserve, move, or acknowledge cleanup queues. Cleanup is peek plus
  exact delete only.
- Do not delete active work queues, spawn requests, manager control queues, or
  user payload queues.
- Do not make task-monitor output lifecycle truth. Cleanup results are
  operational evidence only. `weft.log.tasks` remains runtime lifecycle
  evidence while retained; it does not become audit evidence.
- Do not add a generic policy framework, plugin system, database abstraction,
  or scheduler. One shared bounded runner and a few explicit policies are
  enough.
- Do not make foreground `weft system prune` worse. It may keep using the
  existing full candidate builders in this slice.
- Do not over-mock queues. The key proof is real broker rows being scanned and
  exact message IDs being deleted.

Counterargument to keep visible: deleting `weft.log.tasks` can reduce
historical status fidelity for tasks whose lifecycle rows have been cleaned.
That is an intentional policy change only if the spec is updated to say those
rows are runtime lifecycle evidence governed by operational retention, not
legal, forensic, or audit records. If the spec owner rejects that change, stop.
Do not implement the destructive task-log policies under wording that implies
permanent audit-trail semantics.

## 8. Bite-Sized Tasks

1. Update the specs for the cleanup policy boundary.
   - Outcome: the normative docs match the requested cleanup semantics before
     code starts deleting data.
   - Files to touch:
     - `docs/specifications/00-Quick_Reference.md`
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/07-System_Invariants.md`
   - Required edits:
     - State that `weft.log.tasks` is runtime lifecycle evidence used by Weft
       while retained, not legal, forensic, or audit evidence.
     - State that retention requirements for `weft.log.tasks` are operational:
       they exist to support status, debugging, and bounded runtime behavior,
       not legal hold or forensic audit.
     - State that TaskMonitor cleanup may delete exact message IDs from
       explicitly owned cleanup queues according to queue-specific policies.
     - State that malformed deletion is allowed only in Weft-owned schema
       queues with explicit policy.
     - Add a backlink to this plan under the relevant spec plan/backlink
       section.
   - Tests:
     - `uv run pytest tests/specs/test_plan_metadata.py -q`
   - Stop if:
     - the spec wording still implies task-log rows are permanent audit,
       forensic, or legal-retention evidence.
     - the spec would authorize deleting arbitrary task-local payload queues.
   - Done when:
     - the spec says exactly what cleanup is allowed to delete and what it must
       preserve.

2. Write red tests for the bounded cleanup runner.
   - Outcome: there are failing tests for the requested behavior before the new
     runner exists.
   - Files to touch:
     - `tests/core/test_task_monitor_bounded_cleanup.py`
     - `tests/conftest.py` only if the shared-module audit requires the new
       test file to be listed.
   - Use:
     - `broker_env` or `build_context(spec_context=tmp_path)` with real
       broker-backed queues;
     - `weft.helpers.iter_queue_entries()` for assertions when raw malformed
       rows must remain visible.
   - Tests to add:
     - malformed JSON in `weft.log.tasks` is selected and deleted in apply
       mode;
     - malformed JSON in `weft.state.tid_mappings` is selected and deleted in
       apply mode;
     - `report_only` selects malformed rows but deletes nothing;
     - an old TID mapping row is deleted when it is at least 2x stale, while a
       young mapping row stops or survives;
     - task-log collate deletes only rows for the anchor TID when a terminal
       event is found in the window;
     - task-log collate with no terminal event claims nothing;
     - an old nonterminal anchor TID does not block collating a later completed
       TID in the same bounded window;
     - task-log collate repeats in the same pass for multiple completed TIDs
       and reports one operational collation summary per completed TID;
     - older-than task-log policy deletes old rows only in a cycle where
       collate finds no completed lifecycle group to claim;
     - policy ordering is first-claim-wins: malformed rows are classified as
       malformed, not older-than;
     - the scan budget bounds rows examined and rows deleted;
     - deleting after a scan does not skip rows in a follow-up cycle.
   - Do not mock:
     - SimpleBroker queues;
     - message IDs;
     - exact delete behavior.
   - Stop if:
     - the only way to test the design is to mock the queue generator.
   - Done when:
     - the new tests fail because the bounded runner does not exist or is not
       wired yet.

3. Add the bounded cleanup runner.
   - Outcome: one shared core function can scan configured queues, apply
     policies, and return monitor-friendly stats without invoking global prune
     builders.
   - Files to touch:
     - `weft/core/pruning/bounded_cleanup.py` or
       `weft/core/pruning/task_monitor_cleanup.py`
     - `weft/core/pruning/__init__.py`
     - `weft/_constants.py` only for constants that cannot reuse existing ones.
   - Required structure:
     - frozen dataclasses for row, candidate, queue result, and overall result;
     - one public function, for example
       `run_bounded_task_monitor_cleanup(ctx, cleanup_config, *, apply, exclude_tids)`;
     - keep the cleanup runner's config dataclass in the bounded cleanup module
       or pass primitive values from `TaskMonitor`; do not make
       `weft/core/pruning/` import `TaskMonitor`;
     - small private helpers for raw-window scanning, JSON decoding, age checks,
       collate selection, and exact-delete adaptation.
   - Reuse:
     - `iter_queue_entries()` for raw rows;
     - `apply_exact_prune_candidates()` for deletion;
     - `TERMINAL_TASK_EVENTS`, `TERMINAL_TASK_STATUSES`;
     - existing prune age helpers if they are accessible without importing a
       command layer. If not, copy the tiny age formula locally with a comment
       and avoid importing commands.
   - Constraints:
     - no dependency on `weft.commands`;
     - no CLI imports;
     - no queue delete during generator iteration;
     - no global task-evidence reduce;
     - no task-local queue fanout;
     - no new public CLI option in this slice.
   - Done when:
     - the red runner tests for malformed and older-than pass.

4. Implement the task-log collate policy.
   - Outcome: the runner can delete a bounded completed TID group from
     `weft.log.tasks` and repeat that operation for additional completed TIDs
     in the same bounded window.
   - Files to touch:
     - same bounded cleanup runner module from task 3.
   - Required behavior:
     - choose the oldest well-formed task-log row with a valid string `tid`;
     - skip `exclude_tids`, including the monitor's own TID;
     - collect only rows for that anchor TID;
     - require the anchor row to meet the task-log operational retention age
       before claiming any rows;
     - claim collected rows only if a terminal event or terminal status for
       that TID appears in the same bounded window;
     - if an old anchor TID has no terminal evidence in the bounded window,
       skip only that TID for the rest of the pass and continue looking for
       later completed TIDs;
     - after claiming one completed TID, continue collating the next oldest
       unclaimed eligible TID until no completed lifecycle group remains in
       the bounded window;
     - return per-TID operational collation summaries so steady-state logging
       can report what was reduced before deletion;
     - preserve unrelated TIDs in the same window;
     - report a non-fatal reason when no terminal evidence appears.
   - Tests:
     - the collate tests from task 2 pass;
     - add one interleaving test: A start, B start, A complete should delete A
       rows only and leave B rows.
     - add one head-blocking test: A start, B start, B complete should delete
       B rows and leave A rows.
     - add one repeated-collate test: A start/A complete and B start/B
       complete in one window should delete both lifecycle groups and report
       two collation summaries.
   - Stop if:
     - the code starts building a full `tid -> rows` map for the entire task
       log. That is the old shape under a new name.
   - Done when:
     - collate behavior passes with real queue rows and exact message IDs.

5. Wire TaskMonitor built-ins to the bounded runner.
   - Outcome: supervised `TaskMonitor` no longer calls the foreground
     runtime/retention prune engines for built-in `delete` and `report_only`.
   - Files to touch:
     - `weft/core/tasks/task_monitor.py`
     - `weft/core/task_monitoring.py` only if config/result fields are needed.
   - Required behavior:
     - `WEFT_TASK_MONITOR_PROCESSOR=delete` runs bounded cleanup with
       `apply=True`;
     - `WEFT_TASK_MONITOR_PROCESSOR=report_only` runs bounded cleanup with
       `apply=False`;
     - custom `module:function` processors keep the existing candidate request
       path;
     - `jsonl_then_delete` remains fail-closed unless a separate logging
       callback exists;
     - cycle logs include enough stats to debug production:
       `records_scanned`, per-queue scanned count, selected count, deleted
       count, skipped/reason counts, stop reason, and the operational
       task-log collation summaries/count.
   - Tests:
     - add `TaskMonitor.process_once()` tests in
       `tests/tasks/test_task_monitor.py` using real queues;
     - assert built-in delete removes eligible rows and emits a successful
       cycle;
     - assert built-in report-only leaves rows in place;
     - assert a custom processor still receives `TaskMonitorProcessorRequest`
       as before.
   - Stop if:
     - the task monitor still imports or calls `run_runtime_prune_for_context`
       or `run_retention_prune_for_context` in the built-in cleanup path.
   - Done when:
     - targeted task monitor tests pass and the old heavy path is not used by
       built-ins.

6. Preserve foreground prune behavior.
   - Outcome: `weft system prune` remains an explicit operator command and is
     not accidentally rewritten into the bounded monitor policy.
   - Files to touch:
     - normally none, unless imports or shared types need minor adjustment.
   - Tests:
     - `uv run pytest tests/commands/test_runtime_prune.py tests/commands/test_retention_prune.py tests/cli/test_cli_system.py -q`
   - Constraints:
     - no CLI behavior change;
     - no archive behavior change;
     - no `--force` behavior change.
   - Stop if:
     - implementing background cleanup requires broad foreground prune
       rewrites. That is scope drift.
   - Done when:
     - existing prune tests stay green.

7. Update operational docs and examples.
   - Outcome: operators know what to expect after deploy.
   - Files to touch:
     - `docs/specifications/00-Quick_Reference.md`
     - optionally `README.md` only if it already documents TaskMonitor cleanup
       in the touched section.
   - Required content:
     - one cycle scans at most the configured bounded window per queue;
     - cleanup rate is bounded by `WEFT_TASK_MONITOR_BATCH_SIZE` and monitor
       interval, and may be lower when rows are young or unclaimed;
     - queue counts may still grow if writes exceed deletes;
     - success is visible through `task_monitor_cycle` stats and queue counts,
       not through manager restarts.
   - Done when:
     - docs do not promise immediate backlog collapse.

8. Run final review and verification.
   - Outcome: the change is ready for implementation review and deploy
     observation.
   - Required commands:
     - `uv run pytest tests/core/test_task_monitor_bounded_cleanup.py tests/tasks/test_task_monitor.py tests/core/test_task_monitoring.py -q`
     - `uv run pytest tests/commands/test_runtime_prune.py tests/commands/test_retention_prune.py tests/cli/test_cli_system.py -q`
     - `uv run pytest tests/system/test_constants.py tests/specs/test_plan_metadata.py tests/specs/test_test_audit_policy.py -q`
     - `uv run pytest`
     - `uv run bin/pytest-pg`
     - `uv run mypy weft extensions/weft_docker extensions/weft_macos_sandbox`
     - `uv run ruff check weft tests`
   - Independent review:
     - Ask a different agent family if available:
       "Read this plan and the touched files. Look for errors, bad ideas, and
       latent ambiguities. Do not implement. Could you implement this
       confidently and correctly if asked?"
     - Give the reviewer this plan, the three specs listed in the metadata,
       `weft/core/tasks/task_monitor.py`, `weft/core/pruning/apply.py`, and the
       new bounded cleanup runner.
   - Done when:
     - review findings are either fixed or explicitly rejected with rationale;
     - all required gates pass or a documented environmental blocker remains.

## 9. Testing Plan

Use real queues for the important tests. Do not replace SimpleBroker with mocks
for scan, peek, timestamp, or delete behavior.

Minimum red-green tests:

- malformed deletion:
  - arrange invalid JSON and a valid row in `weft.log.tasks`;
  - run bounded cleanup with `apply=True`;
  - assert the invalid row is gone and the valid row remains unless another
    policy claims it.
- malformed report-only:
  - same setup;
  - run with `apply=False`;
  - assert both rows remain and `reported` increments.
- older-than tid mappings:
  - write a stale `weft.state.tid_mappings` row and a young row;
  - run cleanup;
  - assert only the stale row is deleted.
- collate terminal task log:
  - write task A start, task B start, task A complete;
  - run cleanup with `now_ns` old enough for task-log operational retention;
  - assert A rows are deleted and B row remains.
- collate repeats:
  - write task A start, task A complete, task B start, task B complete, and
    task C start;
  - run cleanup with `now_ns` old enough for task-log operational retention;
  - assert A and B rows are deleted, C remains, and the per-queue stats report
    two collated task summaries.
- collate skips nonterminal anchor:
  - write task A start, task B start, task B complete;
  - run cleanup with `now_ns` old enough for task-log operational retention;
  - assert B rows are deleted and A remains, proving one long-running or
    incomplete head TID does not block later completed lifecycle groups.
- collate terminal task log below retention age:
  - write task A start and task A complete;
  - run cleanup before the anchor row is old enough;
  - assert no delete.
- collate no terminal:
  - write task A start only;
  - run cleanup with an age where older-than does not claim it;
  - assert no delete.
- budget behavior:
  - write more rows than `WEFT_TASK_MONITOR_BATCH_SIZE`;
  - run cleanup;
  - assert `records_scanned <= batch_size` for that queue and deletes are
    bounded by selected rows in the window.
- TaskMonitor integration:
  - instantiate `TaskMonitor` with built-in `delete`;
  - call `process_once()`;
  - assert real queue rows are deleted and `task_monitor_cycle` reports
    bounded stats.

Tempting test to skip: malformed rows that are valid JSON but invalid queue
shape. Do not skip it. Invalid JSON is only one malformed case. A decoded list
or a task-log object without `tid` is also malformed for these schema-owned
queues.

Acceptable narrow monkeypatching:

- patch `upsert_heartbeat` in task-level tests so heartbeat availability does
  not obscure cleanup assertions;
- patch time only when constructing old/young rows is otherwise brittle.

Unacceptable mocking:

- mocking `Queue.delete()`;
- mocking `peek_generator()`;
- asserting only private policy return values without proving broker rows were
  actually removed or preserved.

## 10. Rollout And Runtime Verification

This is a destructive cleanup path, so rollout must be observable.

Before deploy:

- run `WEFT_TASK_MONITOR_PROCESSOR=report_only` locally against a seeded
  backlog and verify selected counts without deletion;
- run apply mode on a disposable context and verify rows drop by exact message
  IDs.

After deploy:

- `weft task status <task-monitor-tid>` should move through
  `cleanup_scanning` and return to waiting. It should not stay in
  `cleanup_scanning` for many minutes.
- `task_monitor_cycle` operational records should show:
  - bounded `records_scanned`;
  - per-queue scan stats;
  - nonzero `deleted` when eligible rows exist;
  - clear stop reasons when nothing is deleted.
- Database row counts should decline only when eligible rows outnumber new
  writes. If counts keep growing, compare write rate to
  `WEFT_TASK_MONITOR_BATCH_SIZE / WEFT_TASK_MONITOR_INTERVAL_SECONDS` before
  assuming cleanup is broken.
- A live manager and TaskMonitor with flat `deleted` counts and nonzero
  eligible backlog is a regression.

Rollback:

- Set `WEFT_TASK_MONITOR_PROCESSOR=report_only` to stop deletion while keeping
  visibility.
- Set `WEFT_TASK_MONITOR_ENABLED=0` to stop the supervised monitor if report
  mode is still too expensive.
- Code rollback is safe if the new runner does not change queue names or
  message payload formats. Deleted rows cannot be restored by rollback, so
  destructive rollout should start with report-only observation when feasible.

One-way door:

- Actual deletion is irreversible. This is why the spec update, real broker
  tests, report-only mode, and post-deploy cycle stats are required gates.

## 11. Out Of Scope

- No `weft.spawn.autostart` priority queue.
- No manager/service reconciliation changes.
- No new task-local output retention policy.
- No durable archival summary rows for completed tasks. Per-cycle operational
  collation summaries in `task_monitor_cycle` stats are in scope because they
  are how operators verify steady-state lifecycle reduction before deletion.
- No rewrite of foreground `weft system prune` unless a small import adjustment
  is unavoidable.
- No new storage backend behavior in SimpleBroker.
- No attempt to clean arbitrary user queues.

## 12. Fresh-Eyes Review

Review pass 1 findings and fixes:

- Ambiguity: "stop on first keep" could have been misread as a global rule.
  Fix: the plan now limits queue-stop decisions to FIFO-proven cases such as
  "too young" age checks.
- Risk: deleting during generator iteration can skip rows.
  Fix: the plan now requires scan-window collection followed by exact deletes
  after the scan.
- Spec conflict: existing text can be read as giving `weft.log.tasks`
  audit-like permanence.
  Fix: task 1 requires the spec to state that task logs are runtime lifecycle
  evidence, that retention is operational, and that explicit cleanup policy can
  delete them.
- Scope drift: a generic policy framework would be easy to overbuild.
  Fix: the plan requires one bounded runner module and explicit first-slice
  policies only.

Review pass 2 result:

- The direction still matches the discussion: FIFO peek window, per-queue
  policy functions, malformed deletion, older-than deletion, collate/delete,
  exact message deletion, and bounded TaskMonitor cycles.
- The plan intentionally does not add durable archival summary rows, autostart
  queues, manager changes, or a broad prune rewrite. It does include
  operational per-cycle collation summaries because steady-state logging needs
  to show which completed lifecycle groups were reduced.

Review pass 3 result:

- Correction: one completed TID per cycle is too weak for steady-state logging
  cleanup. A single long-running TID at the head of `weft.log.tasks` would
  block later completed task lifecycles.
- Fix: collate now repeats within the bounded window, skips old nonterminal
  anchor TIDs for the current pass, and runs older-than only when no completed
  lifecycle group was collated. This preserves the user's ordering rule:
  collate first, broad older-than last.

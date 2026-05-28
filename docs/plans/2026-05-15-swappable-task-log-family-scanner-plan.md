# Swappable Task Log Family Scanner Plan

Status: completed
Source specs: docs/specifications/00-Quick_Reference.md; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13], [OBS.17]
Superseded by: none

## 1. Goal

Replace the TaskMonitor task-log cleanup collation path's FIFO-window anchor
with a swappable task-log family scanner. The first implementation must use
today's SimpleBroker generator APIs. The scanner boundary must be explicit
enough that a later SimpleBroker range/selection implementation can replace the
generator scanner without changing cleanup policies, deletion, TaskMonitor
orchestration, PONG diagnostics, or tests that define the scanner contract.

The production failure mode this plan addresses is concrete: old open manager
and TaskMonitor service rows can dominate the head of `weft.log.tasks`. A
FIFO-window anchored collator protects those open TIDs and may never reach
completed task lifecycles behind them. The new scanner must skip open families
as anchors for the current pass, continue scanning for complete families, and
return exact message IDs for completed groups.

## 2. Source Documents

- `docs/specifications/00-Quick_Reference.md`: user-visible TaskMonitor
  configuration. Update the `WEFT_TASK_MONITOR_*` table if the implementation
  adds a separate task-log scan limit or changes what `batch_size` means.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]: TaskMonitor
  cleanup, `weft.log.tasks` as runtime lifecycle evidence, exact-message
  deletion, and PONG cleanup diagnostics. Update this section with the scanner
  ownership and any new cleanup semantics.
- `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.17]:
  operational evidence and exact candidate deletion. Keep the invariant that
  TaskMonitor deletes only explicit, exact candidates and PONG never scans.
- `docs/plans/2026-05-12-bounded-task-monitor-cleanup-policy-plan.md`:
  completed prior cleanup plan. Its policy ordering remains intended, but the
  FIFO-window collation guarantee was not strong enough under ops backlog.
- `docs/plans/2026-05-12-task-monitor-cleanup-composition-refactor-plan.md`:
  completed ownership refactor. Preserve the split between TaskMonitor runner,
  pruning policies, task-log collation, and actions.
- `docs/agent-context/runbooks/writing-plans.md`: plan format and
  zero-context implementation standard.
- `docs/agent-context/runbooks/hardening-plans.md`: required because this is
  destructive cleanup over runtime queues.
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`: required
  because this changes a reusable cleanup/search contract.

Existing specs still describe "bounded FIFO policy" in places. This plan is
an intended delta: after implementation, the specs must say that simple row
policies may remain FIFO-bounded, while task-log lifecycle collation uses a
bounded family scanner.

## 3. Current Context And Key Files

Files to modify:

- `weft/_constants.py`
  - Add any new production constants or environment variables here.
  - Do not put defaults in the scanner module.
- `weft/core/monitor/task_log_scanner.py` or `weft/core/task_log_family_scan.py` (new)
  - Own the scanner protocol, generator implementation, scanner result types,
    and scanner stats.
  - Pick one file name and keep the concept in one place. Do not create a
    package unless the implementation actually needs more than one module.
- `weft/core/monitor/task_log_collation.py`
  - Keep lifecycle grouping and `CollatedMessageGroup` here, or move only
    scanner-neutral dataclasses if necessary. Do not put deletion here.
- `weft/core/monitor/cleanup.py`
  - Keep TaskMonitor cleanup orchestration here.
  - Inject or construct the scanner here.
  - Keep malformed and older-than policies first-class and explicit.
- `weft/core/queue_window.py`
  - Extend only if the scanner needs a neutral row/range primitive.
  - Do not add task-log policy or delete semantics here.
- `weft/core/tasks/task_monitor.py`
  - Update PONG/last-cycle cached diagnostics only if new scanner fields are
    surfaced.
- `docs/specifications/00-Quick_Reference.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/plans/README.md`

Tests to modify or add:

- `tests/core/test_task_monitor_cleanup.py`
  - Keep core cleanup integration tests here if the scanner is only exercised
    through `run_task_monitor_cleanup()`.
- `tests/core/test_task_log_scanner.py` (recommended if the scanner contract is
  non-trivial)
  - Add contract tests for the scanner interface. If this file is added, add it
    to `tests/conftest.py` `_SHARED_MODULES`.
- `tests/tasks/test_task_monitor.py`
  - Add one TaskMonitor-level integration test if PONG stats or live
    `TaskMonitor.process_once()` behavior changes.
- `tests/system/test_constants.py`
  - Run after adding constants.
- `tests/specs/test_plan_metadata.py`
  - Run after adding this plan and README row.

Read first:

- `weft/core/monitor/cleanup.py`
  - Understand `run_task_monitor_cleanup()`, `_task_log_candidates()`,
    `_select_collated_task_log_candidates()`, and
    `_select_old_unstarted_task_log_candidates()`.
- `weft/core/monitor/task_log_collation.py`
  - Understand `CollatedMessageGroup`, `collate_next_task_log_group()`, and
    `is_terminal_task_log()`.
- `weft/core/pruning/apply.py`
  - Exact deletion must continue to go through
    `apply_exact_prune_candidates()`.
- `weft/helpers/__init__.py`
  - `iter_queue_entries()` is the generator path to use in the first
    implementation.
- SimpleBroker queue APIs in the installed package or `../simplebroker`
  - Verify the available public generator/range APIs before implementing.
    Do not use backend-private SQL in Weft for the first slice.

Comprehension checks before editing:

- Can you explain why a manager `task_spawned` row is parent runtime evidence
  under the manager TID, not a child task lifecycle row?
- Can you explain why `delete_older_than` must run after lifecycle collation?
- Can you explain why a scanner must not delete while iterating a generator?
- Can you explain which exact tests prove an open TID at queue head does not
  starve completed TIDs behind it?

If any answer is unclear, stop and read before editing.

## 4. Invariants And Constraints

- `weft.log.tasks` remains runtime lifecycle evidence while retained. It is
  not audit, forensic, or legal-retention evidence.
- TaskMonitor cleanup may delete only exact candidates selected by explicit
  policies. Unknown rows and unsupported queues remain untouched.
- PONG remains lightweight. It may report cached scanner stats from the last
  cleanup cycle, but it must not scan queues or recompute candidates.
- The scanner does not delete, log, archive, reserve, move, or mutate rows. It
  returns decoded rows, completed groups, skipped-family stats, and exact
  message IDs. Actions happen after scanning.
- The deletion path remains `apply_exact_prune_candidates()`.
- Malformed cleanup remains queue-owned. Do not apply JSON-shape cleanup to
  arbitrary user queues.
- Broad older-than task-log deletion remains last and must not run before
  lifecycle collation has had a chance to claim complete groups.
- The first implementation uses SimpleBroker's public generator API. Do not
  add raw Postgres or SQLite SQL to Weft in this slice.
- A later SimpleBroker range/selection scanner must be swappable behind the
  same contract. If implementing the generator scanner requires exposing
  generator-specific details to cleanup policies, the boundary is wrong.
- Do not change queue names, TID format, TaskSpec schema, result payloads, or
  manager/task lifecycle semantics.
- Do not implement `jsonl_then_delete` lifecycle logging in this slice. The
  scanner contract should make it easier later, but the logging action remains
  out of scope.

## 5. Target Design

### 5.1 Scanner Contract

Create a task-log family scanner module with an interface shaped around
domain-neutral scanning and domain-specific grouping, not deletion.

Recommended public types:

```python
class TaskLogFamilyScanner(Protocol):
    def scan(
        self,
        ctx: WeftContext,
        *,
        queue_name: str,
        scan_limit: int,
        selection_limit: int,
        now_ns: int,
        min_age_seconds: float,
        exclude_tids: set[str],
        claimed_ids: set[int],
    ) -> TaskLogFamilyScanResult: ...
```

Recommended result fields:

- `complete_lifecycle_groups: tuple[CollatedMessageGroup, ...]`
- `terminal_without_start_groups: tuple[CollatedMessageGroup, ...]`
- `rows_scanned: int`
- `decoded_rows: tuple[DecodedQueueWindowRow, ...]` for the scanned task-log
  rows. The cleanup runner may use these rows for malformed and broad
  older-than policies. It must not feed them back into the old FIFO-anchored
  collation loop.
- `claimed_message_ids: frozenset[int]`
- `skipped_open_family_count: int`
- `skipped_open_families_sample: tuple[TaskLogSkippedFamilySummary, ...]`
- `scan_limit_reached: bool`
- `stop_reason: str | None`

Keep summary samples small and PONG-safe. Do not return huge per-TID maps in
normal operational output.

### 5.2 Generator Scanner

Implement `GeneratorTaskLogFamilyScanner` first.

Behavior:

1. Iterate `weft.log.tasks` with `iter_queue_entries()` and stop at
   `scan_limit`.
2. Decode each row into `DecodedQueueWindowRow`.
3. Ignore rows already in `claimed_ids`.
4. Keep malformed rows available for the malformed policy. The scanner should
   not silently drop them if cleanup still needs to report or delete malformed
   task-log rows.
5. Index valid rows by top-level `tid`.
6. A family is eligible for complete-lifecycle collation when it has:
   - at least one visible start event from `TASK_LOG_START_EVENTS`;
   - at least one terminal row according to `is_terminal_task_log()`;
   - an oldest selected start row old enough under `min_age_seconds`;
   - no excluded TID.
7. Build the group from the earliest visible start through the first terminal
   row for that same TID, preserving queue order.
8. A terminal-without-start group is eligible only after complete-lifecycle
   groups are selected and only for terminal rows whose TID has no visible
   start row in the scanned set or whose visible start rows were already
   claimed by another group.
9. Open-start families are skipped for collation in this cycle and counted in
   scanner stats. They must not block later complete families in the same scan.
10. Stop selecting groups when the next group would exceed `selection_limit` or
    when all scanned groups have been considered.

Selection order must be deterministic:

- order groups by first message ID;
- within a TID, use the first eligible start and first terminal after that
  start;
- do not select the same message ID twice;
- when a family has multiple start/terminal cycles in the scanned range,
  select one cycle at a time in message order. If this creates complexity,
  implement one cycle per TID in the first slice and record a stop gate before
  broadening it.

### 5.3 Future Range Scanner

The later SimpleBroker-backed scanner should implement the same
`TaskLogFamilyScanner` protocol. It may use queue-local range reads, exact ID
reads, or other broker-provided maintenance APIs, but it must not change:

- `TaskMonitorCleanupConfig`;
- policy names;
- candidate classes;
- `CleanupPolicyStats` shape;
- exact-delete behavior;
- PONG field names beyond adding scanner-specific cached diagnostics.

Do not add JSON predicate logic to SimpleBroker. SimpleBroker may help Weft
move through a queue by queue name and message ID/order range. Weft owns JSON
decoding, TID grouping, lifecycle semantics, and deletion eligibility.

## 6. Configuration

Do not leave scan budget ambiguous.

Required first-slice configuration:

- Keep `WEFT_TASK_MONITOR_BATCH_SIZE` as the maximum selected/applied cleanup
  candidates per queue per cycle.
- Add `WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT` as the maximum task-log rows the
  generator scanner may inspect per cleanup cycle. Default it high enough to
  pass the restored ops-dump case without tuning, for example `50000`, while
  still bounded.
- Add the constant and env parsing in `weft/_constants.py`.
- Add the value to `TaskMonitorCleanupConfig`.
- Include the cached value in TaskMonitor PONG under
  `extended.task_monitor.task_log_scan_limit` or equivalent.
- Update `docs/specifications/00-Quick_Reference.md` and [MF-5].

If the implementer tries to keep `batch_size` as both scan budget and delete
budget, stop. That is the current failure mode in another form.

## 7. Implementation Tasks

### Task 1: Write Failing Scanner Contract Tests

Owner: `tests/core/test_task_log_scanner.py` or
`tests/core/test_task_monitor_cleanup.py`.

Use real broker queues through `build_context()` and `iter_queue_entries()`.
Do not mock SimpleBroker, `Queue`, or the TaskMonitor cleanup runner.

Tests:

1. Open-prefix starvation:
   - Write an old manager lifecycle start.
   - Write more old manager `task_spawned` rows than the cleanup delete budget.
   - Write a completed ordinary task lifecycle after those rows.
   - Run the generator scanner with `scan_limit` large enough to include the
     completed task and `selection_limit` smaller than the open prefix.
   - Assert it returns the completed task group and reports the manager TID as
     skipped/open.
2. Scan limit boundary:
   - Put a completed task terminal just beyond `scan_limit`.
   - Assert no group is selected and `scan_limit_reached` is true.
3. Selection budget:
   - Put two completed groups in the scan.
   - Set `selection_limit` so only the first group fits.
   - Assert deterministic first-message-ID ordering.
4. Terminal without visible start:
   - Put an old `work_completed` row with no visible start in the scan.
   - Assert it is returned by the terminal-without-start path only after
     complete-lifecycle groups are considered.
5. Excluded TID:
   - Put a completed group for `exclude_tids`.
   - Assert it is skipped and not selected.

Expected red state: at least the open-prefix starvation test fails with the
current FIFO-window anchored implementation.

### Task 2: Add Scanner Types And Generator Implementation

Owner: new scanner module.

Implement the minimal dataclasses and protocol needed by Task 1. Keep the
module independent of pruning candidates and exact deletion.

DRY requirement:

- Reuse `DecodedQueueWindowRow`, `QueueWindowRow`, `payload_string()`, and
  `is_old_enough()` from `weft/core/queue_window.py`.
- Reuse `is_terminal_task_log()` from `weft/core/monitor/task_log_collation.py`.
- Reuse `TASK_LOG_START_EVENTS` and terminal constants from `weft/_constants.py`.

YAGNI guard:

- Do not create a scanner registry.
- Do not support multiple queue families.
- Do not implement a SimpleBroker range scanner yet.
- Do not add classes for every policy. Typed functions and frozen dataclasses
  are enough.

### Task 3: Wire Scanner Into TaskMonitor Cleanup

Owner: `weft/core/monitor/cleanup.py`.

Replace the old `_select_collated_task_log_candidates()` loop with scanner
results.

Required behavior:

- Malformed task-log rows are still selected first.
- Complete lifecycle groups from the scanner become candidates with policy
  `TASK_MONITOR_POLICY_TASK_LOG_COLLATE_COMPLETE_LIFECYCLE`.
- Terminal-without-start groups become candidates with policy
  `TASK_MONITOR_POLICY_TASK_LOG_COLLATE_TERMINAL_WITHOUT_START`.
- Broad old-without-start deletion still runs after collation and still
  protects open-start TIDs visible in the scanned rows.
- Reserved cleanup still runs only for terminal-proven groups.
- Exact delete still happens after scanning and candidate selection.

Stop and re-plan if broad older-than deletion starts deleting rows for open
manager, heartbeat, or TaskMonitor TIDs in this slice. That may be a valid next
policy, but it is not this change.

### Task 4: Separate Scan Limit From Selection/Delete Limit

Owner: `weft/_constants.py`, `weft/core/monitor/cleanup.py`,
`weft/core/tasks/task_monitor.py`, docs.

Add the separate scan-limit config. This is not optional: the restored ops dump
needs to scan past the first 5,000 rows while keeping per-cycle deletion
bounded. Keeping one `batch_size` value for both scan budget and delete budget
is the failure mode this plan is correcting.

Implementation requirements:

- Parse the new env var through the existing config loader.
- Validate positive integer values.
- Keep all constant names in `_constants.py`.
- Include the configured scan limit in cached last-cycle/PONG diagnostics.
- Update specs and quick reference.

### Task 5: Update Diagnostics

Owner: `weft/core/pruning/models.py` only if existing stats cannot represent
the new scanner data; otherwise keep diagnostics in existing summary dicts.

PONG should show, from cached last-cycle data:

- task-log rows scanned;
- task-log scan limit;
- whether the scan limit was reached;
- skipped open family count;
- selected complete-lifecycle rows/groups;
- selected terminal-without-start rows/groups;
- existing policy-level selected/deleted counts.

Do not add active PONG scans. PONG reads stored variables only.

### Task 6: Integration And Regression Tests

Owner: tests.

Add or update tests that prove:

- `run_task_monitor_cleanup(... apply=False)` reports candidates behind an
  open-prefix service family.
- `run_task_monitor_cleanup(... apply=True)` deletes only exact selected
  completed-group rows and leaves the open manager/service rows.
- Re-running cleanup against the same queue continues to make progress if more
  completed groups remain behind the open prefix.
- `TaskMonitor` PONG reports cached scanner stats after a cleanup cycle if
  PONG fields change.
- Existing malformed, tid-mapping, terminal-reserved, and old-without-start
  tests still pass.

Use real broker-backed queues. Do not assert by poking private scanner maps
unless the scanner map is itself the contract. Prefer asserting queue contents,
candidate message IDs, policy stats, and delete counts.

### Task 7: Docs And Spec Traceability

Owner: docs.

Update:

- `docs/specifications/00-Quick_Reference.md`
  - new env var if added;
  - clarified `WEFT_TASK_MONITOR_BATCH_SIZE` meaning if changed.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]
  - task-log cleanup now uses a bounded family scanner for lifecycle
    collation;
  - simple row policies remain bounded row policies;
  - PONG scanner diagnostics remain cached.
- `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.17]
  - exact-message cleanup invariant still holds;
  - family scanning does not weaken active-work protection.
- module docstrings in new or touched scanner modules.
- `docs/plans/README.md` for this plan.

## 8. Verification Gates

Run smallest tests first:

```bash
uv run pytest tests/core/test_task_log_scanner.py -q
uv run pytest tests/core/test_task_monitor_cleanup.py -q
uv run pytest tests/tasks/test_task_monitor.py -q
```

Then run contract/spec guards:

```bash
uv run pytest tests/system/test_constants.py tests/specs/test_plan_metadata.py -q
uv run mypy weft
uv run ruff check weft tests/core/test_task_log_scanner.py tests/core/test_task_monitor_cleanup.py tests/tasks/test_task_monitor.py
```

If local Postgres is available, run the Postgres target for the touched tests:

```bash
uv run bin/pytest-pg tests/core/test_task_log_scanner.py tests/core/test_task_monitor_cleanup.py -q
```

Ops-dump verification, using the restored `mm_weft` database:

1. Run cleanup in report-only mode against the restored dump.
2. Confirm the task-log policy stats select complete lifecycle groups even
   when the oldest rows are open manager/TaskMonitor TIDs.
3. Run one destructive cleanup pass against a copy or approved local restore.
4. Confirm `weft.log.tasks` count decreases and the oldest open manager rows
   remain if no terminal proof exists.
5. Confirm repeated cycles continue selecting completed groups until the scan
   range has no complete groups or hits the configured scan limit.

Deployment evidence expected after ship:

- TaskMonitor PONG reports `success=true`, `processor_in_flight=false`, and
  nonzero selected/deleted counts for task-log complete lifecycle groups when
  backlog contains old completed families.
- `weft.log.tasks` total decreases across monitor cycles or grows more slowly
  than new lifecycle writes.
- Open manager/TaskMonitor rows may remain. That is expected until a separate
  policy for old open-service runtime evidence exists.

## 9. Rollout And Rollback

Rollout:

- Ship generator scanner first.
- Keep old queue names and exact-delete semantics.
- Keep scanner stats cached in PONG so ops can verify behavior without
  running active scans.
- If a future SimpleBroker API lands, add a second scanner implementation
  behind the same protocol and run the same scanner contract tests against
  both implementations.

Rollback:

- Revert scanner wiring to the previous bounded FIFO collation loop.
- New env vars, if added, should be ignored safely by older code.
- No persisted payload shape changes are allowed in this slice, so rollback
  must not require queue migration.

One-way doors:

- Destructive deletion remains a one-way door. Keep apply/report-only behavior
  and exact-candidate stats clear.
- Do not change dump/load semantics in this slice.

## 10. Out Of Scope

- Adding a SimpleBroker JSON predicate API.
- Adding backend-specific SQL to Weft.
- Implementing `jsonl_then_delete` lifecycle summaries.
- Deleting old open manager, heartbeat, or TaskMonitor service evidence merely
  because it is old.
- Cleaning old reserved/internal queues without terminal proof.
- Changing status/result reconstruction logic.
- Changing manager supervision, heartbeat semantics, or TaskSpec schema.

## 11. Fresh-Eyes Self-Review

Findings from self-review:

- The first draft risked treating `batch_size` as both scan budget and delete
  budget. That recreates the ops failure. The plan now requires a separate
  task-log scan limit if the implementation cannot prove the existing setting
  is enough.
- The first draft did not state what happens to manager `task_spawned` rows.
  The plan now makes those open parent-service rows skipped, not deleted, in
  this slice.
- The first draft allowed terminal-without-start selection to run in parallel
  with complete lifecycle selection. The plan now requires complete lifecycle
  selection first, then terminal-without-start, then broad older-than cleanup.
- The first draft did not explicitly ban raw SQL. The plan now requires the
  generator implementation to use public SimpleBroker APIs and reserves a
  future scanner implementation for any new SimpleBroker range API.

Residual risk:

- The generator scanner still pays the cost of scanning old open service
  prefixes every cycle. That is acceptable for the first slice because it
  proves the swappable contract and restores progress. It is not the final
  performance design.
- This plan should receive external review before implementation because it
  changes destructive cleanup behavior and a reusable scanner contract.

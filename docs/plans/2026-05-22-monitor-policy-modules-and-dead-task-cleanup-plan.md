# Monitor Policy Modules And Dead-Task Cleanup Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.3]; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13], [OBS.16], [OBS.17]
Superseded by: none

Correction note, 2026-05-22: the original version of this plan was too narrow
for dead-TID cleanup. The implemented policy is governed by the updated specs:
dead-TID selection and per-TID queue eligibility live together in
`weft/core/monitor/policies/dead_task.py`; the TaskMonitor reactor only
schedules and commits worker results; the worker performs queue/API work.
For proven-dead TIDs, `ctrl_in`, `ctrl_out`, and `inbox` are stale
immediately. `outbox` and `reserved` are selected by the same per-TID policy
only after the task-log retention period. The per-TID task-log coalesce lookup
uses the released broker message-search API rather than substituting only the
Monitor-store indexed path.

## Scope

This plan has two ordered slices.

Slice A refactors existing TaskMonitor cleanup policies into focused modules
under `weft/core/monitor/policies/`. It is a no-behavior-change slice. Do not
rewrite policy behavior while moving it.

Slice B adds a dead-task cleanup policy. The policy derives historical task IDs
from standard task-local queue names, subtracts live task IDs, and processes
dead TIDs from oldest to newest. For each dead TID it idempotently deletes
stale `T{tid}.ctrl_in`, `T{tid}.ctrl_out`, and `T{tid}.inbox`. The same
per-TID policy selects `T{tid}.outbox` and `T{tid}.reserved` only after the
retention period. It then uses the broker task-log coalesce API to identify,
summarize, and exact-delete retained `weft.log.tasks` rows for that TID.

The goal is going forward cleanup. Standard control queues are runtime control
channels. After a task exits, those queues are stale. A residual standard
control queue should normally mean forced process death before task-owned
cleanup, cleanup failure, or older stale state.

## Source Documents To Read First

Read these before editing code. Do not skim the cleanup boundary.

1. `docs/agent-context/README.md`
2. `docs/agent-context/decision-hierarchy.md`
3. `docs/agent-context/principles.md`
4. `docs/agent-context/engineering-principles.md`
5. `docs/agent-context/runbooks/writing-plans.md`
6. `docs/agent-context/runbooks/hardening-plans.md`
7. `docs/agent-context/runbooks/testing-patterns.md`
8. `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`
9. `docs/specifications/00-Quick_Reference.md`, queue names
10. `docs/specifications/01-Core_Components.md` [CC-2.3], TaskMonitor as a
    supervised task
11. `docs/specifications/05-Message_Flow_and_State.md` [MF-5], especially
    `Cleanup Boundary` and `Queue Management Patterns`
12. `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.16],
    [OBS.17]
13. `docs/plans/2026-05-20-simplebroker-api-adoption-plan.md`
14. `docs/plans/2026-05-19-monitor-terminal-retirement-and-runtime-queue-cleanup-plan.md`
15. `docs/plans/2026-05-19-task-monitor-control-cleanup-worker-plan.md`
16. `docs/plans/2026-05-18-monitor-table-driven-retained-log-cleanup-plan.md`

## Current Code Map

The implementer must understand these files before starting.

- `weft/core/monitor/cleanup.py` is the older cleanup-policy orchestrator. It
  contains the foreground/built-in policy runner, `TaskMonitorCleanupConfig`,
  `TaskMonitorCleanupResult`, `run_task_monitor_cleanup`, and the candidate
  selectors for `weft.state.tid_mappings`, `weft.log.tasks`, claimed task-log
  rows, collated task-log groups, old task-log rows, and terminal reserved rows.
- `weft/core/monitor/task_monitor.py` owns supervised TaskMonitor runtime
  cleanup. Relevant pieces include `_TaskControlCleanupResult`,
  `_standard_task_control_queue_names`, `_reserved_queue_tid`,
  `_delete_terminal_control_queues`, `_active_runtime_tids`,
  `_queue_name_snapshot`, `_delete_runtime_reserved_queues`, and
  `_run_terminal_control_cleanup_slice`.
- `weft/core/monitor/store.py` owns Monitor collation tables. Relevant APIs are
  `get_task`, `list_terminal_control_cleanup_ready_tasks`,
  `mark_task_controls_deleted`, `mark_families_disposed`,
  `list_deletable_task_log_messages`, `delete_task_messages_after_raw_delete`,
  and `retire_completed_collation_families`.
- `weft/core/monitor/collation.py`, `weft/core/monitor/task_log_scanner.py`,
  and `weft/core/monitor/task_log_collation.py` own task-log folding and
  family selection. Do not duplicate their logic.
- `weft/core/pruning/apply.py` and `weft/core/pruning/models.py` own exact row
  delete candidates and delete-result accounting. Use these for retained
  task-log rows. Do not issue ad hoc SQL deletes against broker message rows
  from Weft code.
- `weft/_constants.py` owns queue suffix constants, worker-lane constants,
  cleanup policy names, and env-derived config defaults. Add a constant there
  only if this plan explicitly calls for one.
- `tests/core/test_task_monitor_cleanup.py` covers the current `cleanup.py`
  policy runner.
- `tests/tasks/test_task_monitor.py` covers supervised TaskMonitor runtime
  cleanup, including terminal control cleanup, reserved cleanup, active-owner
  protection, delete failure handling, and fair bounded scheduling.
- `tests/core/test_task_monitoring.py` covers shared monitoring behavior and
  cached status surfaces.

## Non-Negotiable Invariants

- Specs are source of truth. If code and this plan disagree with [MF-5] or
  [OBS.13], [OBS.16], [OBS.17], stop and update the plan or spec before
  changing code.
- Slice A must not change observable cleanup behavior. It is an extraction and
  import rewiring pass only.
- Standard task-local `T{tid}.ctrl_in`, `T{tid}.ctrl_out`, and `T{tid}.inbox`
  are stale after the owning TID is proven dead.
- `T{tid}.outbox` and `T{tid}.reserved` are retention-gated for dead TIDs.
  Select them in the same per-TID policy only when the TID is older than the
  task-log retention period.
- Manager and service control queues must not be deleted by task-local cleanup.
  In particular, never delete `weft.manager.ctrl_in`,
  `weft.manager.ctrl_out`, `T{monitor_tid}.ctrl_in`, or
  `T{monitor_tid}.ctrl_out` while those owners are live.
- Custom task control queues are not standard task-local queues. If a
  TaskSpec summary says a task used nonstandard control queue names, the
  runtime cleanup path must not delete guessed queues for that record.
- Missing `T{tid}.ctrl_in` and `T{tid}.ctrl_out` queues are success for the
  dead-TID cleanup policy. Missing queues are already clean.
- Live TIDs must be rechecked immediately before destructive queue deletion.
  Queue-list discovery and deletion can race with new work.
- Deletion must use public SimpleBroker APIs such as `list_queues()` and
  `delete_from_queues(...)` or the exact released equivalent. Weft must not use
  direct queue-table SQL for production cleanup.
- Retained task-log row deletion must remain exact-message-ID deletion through
  the existing pruning/apply path.
- The per-TID log coalescing path must use the released broker message-search
  API for that TID and then exact-delete only the matching `weft.log.tasks`
  message IDs after Monitor-store summarization.
- The worker model must be bounded. Do not spawn one OS thread per historical
  TID. Use a bounded local `queue.Queue[str]` and a fixed small worker pool
  inside the existing TaskMonitor cleanup worker lane.
- TaskMonitor PONG/status must remain cached. Do not scan queues or open the
  Monitor store in the PONG path.
- `report_only` must remain non-destructive.
- All cleanup must be bounded by the existing runtime cleanup limit and slice
  deadline unless this plan explicitly changes those gates.

## Mental Model

Weft is queue-first. Queues are cheap, and task-local queues are named so the
name itself carries identity. That makes the dead-task cleanup approach simple:
list queue names, parse standard task-local TIDs, subtract live TIDs, and clean
the stale runtime control queues for those dead TIDs.

The Monitor store is not lifecycle truth. It is an operational read model
derived from retained `weft.log.tasks`. Use it for efficient cleanup proof and
accounting where it already has indexed rows. Do not create a second task-state
database beside it.

The right shape is one cleanup lane with multiple candidate sources. The wrong
shape is a second sweeper that races the existing TaskMonitor runtime cleanup
logic and records incompatible stats.

## Slice A: Refactor Existing Policies Into Modules

### A1. Add Characterization Baseline

Files to inspect:

- `weft/core/monitor/cleanup.py`
- `weft/core/monitor/task_monitor.py`
- `tests/core/test_task_monitor_cleanup.py`
- `tests/tasks/test_task_monitor.py`

Action:

Run the existing relevant tests before editing:

```bash
./.venv/bin/python -m pytest tests/core/test_task_monitor_cleanup.py -q
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py -q
```

If either test module fails before edits, record the failing tests and decide
whether they are unrelated to this work. Do not hide pre-existing failures by
changing expectations during the refactor.

Expected result:

You have a known baseline for the policy runner and runtime cleanup behavior.

### A2. Create The Policy Package Skeleton

Files to add:

- `weft/core/monitor/policies/__init__.py`
- `weft/core/monitor/policies/types.py`

Action:

Create the package. Move only shared internal policy dataclasses into
`types.py` if at least two policy modules need them. Good candidates are
`_CleanupPolicyRun` and `_TaskControlCleanupResult`, but do not force a shared
types module if the extraction can stay clearer without it.

Rules:

- Keep names private with leading underscores unless another module must import
  them.
- Use `from __future__ import annotations`.
- Keep imports at the top.
- Do not create a policy registry, plugin interface, abstract base class, or
  dynamic import scheme. This is a package split, not a framework.

Tests:

Run:

```bash
./.venv/bin/python -m pytest tests/core/test_task_monitor_cleanup.py -q
```

### A3. Extract TID Mapping Row Policies

Files to change:

- `weft/core/monitor/cleanup.py`
- `weft/core/monitor/policies/tid_mapping.py`

Move from `cleanup.py` into `policies/tid_mapping.py`:

- `_tid_mapping_candidates`
- `_valid_tid_mapping_payload`
- any row decoding helper that is only needed for tid mappings

Preferred module API:

```python
def tid_mapping_candidates(
    rows: Sequence[DecodedQueueWindowRow],
    *,
    config: TaskMonitorCleanupConfig,
    now_ns: int,
    exclude_tids: set[str],
) -> tuple[list[CleanupCandidate], CleanupQueueStats, tuple[CleanupPolicyStats, ...]]:
    ...
```

If importing `TaskMonitorCleanupConfig` from `cleanup.py` creates an import
cycle, do not solve it with late imports. Extract a minimal protocol-like
dataclass or pass only the needed scalar values:

```python
tid_mapping_candidates(
    rows,
    now_ns=now_ns,
    min_age_seconds=config.tid_mapping_min_age_seconds,
    exclude_tids=exclude_tids,
)
```

That scalar-argument style is preferred because it keeps policy modules
independent of orchestration config.

Tests:

Keep `tests/core/test_task_monitor_cleanup.py` passing unchanged. Add a narrow
unit test only if the move exposes a clean public internal function worth
testing directly. Do not mock SimpleBroker for this policy; it operates on row
objects.

Run:

```bash
./.venv/bin/python -m pytest tests/core/test_task_monitor_cleanup.py -q
./.venv/bin/ruff check weft/core/monitor/cleanup.py weft/core/monitor/policies
```

### A4. Extract Task-Log Candidate Policies

Files to change:

- `weft/core/monitor/cleanup.py`
- `weft/core/monitor/policies/task_log.py`

Move from `cleanup.py` into `policies/task_log.py`:

- `_task_log_candidates`
- `_select_malformed_task_log_candidates`
- `_queue_claimed_count`
- `_scan_claimed_task_log_rows`
- `_queue_pending_count`
- `_peek_rows_including_claimed`
- `_select_claimed_task_log_candidates`
- `_task_log_candidates_from_collated_group`
- `_task_log_scan_metadata`
- `_select_old_unstarted_task_log_candidates`
- `_collated_task_log_candidate_class`
- `_collated_task_log_policy`
- `_collated_group_has_visible_start`
- `_open_started_task_log_tids`

Boundary:

This module still supports the older cleanup runner. Do not change the
table-driven retained task-log cleanup path in `task_monitor.py` while doing
this extraction.

Import rule:

`policies/task_log.py` may import `weft.core.monitor.task_log_scanner`,
`weft.core.monitor.task_log_collation`, `weft.core.pruning.*`, and
`weft.core.queue_window`. It must not import `TaskMonitorTask`.

Tests:

Run:

```bash
./.venv/bin/python -m pytest tests/core/test_task_monitor_cleanup.py -q
./.venv/bin/python -m pytest tests/core/test_task_monitoring.py -q
```

### A5. Extract Reserved Queue Policies From `cleanup.py`

Files to change:

- `weft/core/monitor/cleanup.py`
- `weft/core/monitor/policies/reserved.py`

Move from `cleanup.py` into `policies/reserved.py`:

- `_terminal_reserved_candidates`
- `_collated_group_is_successful_completion`
- `_decode_reserved_row`

Do not move supervised runtime reserved cleanup from `task_monitor.py` in this
task unless a later step needs it. The old `cleanup.py` reserved policy selects
exact rows from `T{tid}.reserved`; the runtime path deletes whole standard
reserved queues only when store proof or age proof allows it. Those are related
but not the same policy.

Tests:

Run:

```bash
./.venv/bin/python -m pytest tests/core/test_task_monitor_cleanup.py -q
```

### A6. Extract Runtime Control Helpers Without Moving The Worker

Files to change:

- `weft/core/monitor/task_monitor.py`
- `weft/core/monitor/policies/runtime_control.py`

Move pure helpers from `task_monitor.py` into
`policies/runtime_control.py`:

- `_standard_task_control_queue_names`
- `_reserved_queue_tid`
- the result dataclass if it is used by multiple runtime policy helpers

Do not move `_maybe_start_terminal_control_cleanup_worker`,
`_submit_terminal_control_cleanup_worker`, or
`_run_terminal_control_cleanup_worker`. Those methods own TaskMonitor worker
lane lifecycle and cached state updates. Keep them in `task_monitor.py`.

Move `_delete_terminal_control_queues` only if it can be made a small pure
service function that receives explicit dependencies:

```python
def delete_terminal_control_queues(
    broker_factory: Callable[[], ContextManager[Any]],
    record: MonitorTaskCollationRecord,
    *,
    existing_queue_names: set[str] | None = None,
) -> TaskControlCleanupResult:
    ...
```

If that extraction makes the code harder to read, leave the deletion method in
`task_monitor.py` and move only the standard-name parser. The purpose of Slice
A is clarity, not maximal extraction.

Tests:

Run:

```bash
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py -q
./.venv/bin/ruff check weft/core/monitor/task_monitor.py weft/core/monitor/policies
```

### A7. Rewire `cleanup.py` As Orchestrator Only

Files to change:

- `weft/core/monitor/cleanup.py`
- `weft/core/monitor/policies/__init__.py`

Action:

Leave `TaskMonitorCleanupConfig`, `TaskMonitorCleanupResult`, and
`run_task_monitor_cleanup` in `cleanup.py`. That file should decide when a
queue is scanned, apply candidates, merge stats, and handle errors. It should
not contain the individual policy implementations after Slice A.

Do not publish the new policy package as a public API. It is an internal
organization boundary.

Definition of done for Slice A:

- No behavior change.
- Existing test modules pass unchanged.
- `cleanup.py` is materially smaller and reads as orchestration.
- No new cleanup knobs.
- No new dependency.
- No direct SQL.

Run:

```bash
./.venv/bin/python -m pytest tests/core/test_task_monitor_cleanup.py -q
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py -q
./.venv/bin/python -m pytest tests/core/test_task_monitoring.py -q
./.venv/bin/ruff check weft/core/monitor tests/core/test_task_monitor_cleanup.py tests/tasks/test_task_monitor.py
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
```

## Slice B: Dead-Task Per-TID Cleanup

### B1. Add A Dead-Task Policy Module With Parser Tests First

Files to add:

- `weft/core/monitor/policies/dead_task.py`
- `tests/core/monitor/policies/test_dead_task.py`

Action:

Write tests first for standard queue-name parsing and dead-TID selection.

The module should expose small internal helpers:

```python
STANDARD_TASK_QUEUE_SUFFIXES = (
    QUEUE_INBOX_SUFFIX,
    QUEUE_RESERVED_SUFFIX,
    QUEUE_OUTBOX_SUFFIX,
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_CTRL_OUT_SUFFIX,
)

def standard_task_queue_tid(queue_name: str) -> str | None:
    ...

def standard_task_queue_identity(queue_name: str) -> tuple[str, str] | None:
    ...

def dead_task_tids_from_queue_names(
    queue_names: Iterable[str],
    *,
    live_tids: set[str],
    now_ns: int,
    min_age_seconds: float,
) -> tuple[str, ...]:
    ...
```

Rules:

- Accept only strict names shaped as `T<digits>.<known_suffix>`.
- Reject manager/global queues.
- Reject custom names, names with non-digit TIDs, and names with extra
  segments.
- Return oldest to newest by numeric TID.
- Apply a grace age before cleanup using
  `TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS` and
  `is_old_enough(int(tid), now_ns, min_age_seconds)`. That constant is already
  the runtime-liveness grace used before deleting stale TID mapping rows. Do
  not add a new config knob in the first implementation.

Tests:

Use plain parser tests, not mocked broker calls:

- `T123.ctrl_in` returns `("123", "ctrl_in")`.
- `T123.ctrl_out`, `T123.outbox`, `T123.inbox`, and `T123.reserved` all
  contribute historical TID `123`.
- `weft.manager.ctrl_in`, `Tabc.ctrl_in`, `T123.ctrl_in.extra`,
  `T123.custom`, and `T.ctrl_in` are rejected.
- Live TIDs are subtracted.
- Too-young TIDs are skipped.
- Results are ordered by integer TID ascending.

Run:

```bash
./.venv/bin/python -m pytest tests/core/monitor/policies/test_dead_task.py -q
```

### B2. Add Queue Snapshot Candidate Discovery To The Runtime Cleanup Lane

Files to change:

- `weft/core/monitor/task_monitor.py`
- `weft/core/monitor/policies/dead_task.py`
- `weft/core/monitor/policies/runtime_control.py` if Slice A moved shared
  runtime-control helpers there

Action:

Inside `_run_terminal_control_cleanup_slice`, use the existing broker
`list_queues()` path to collect standard task-local queue names. Prefer a small
set of suffix-specific patterns:

```python
patterns = (
    f"T*.{QUEUE_INBOX_SUFFIX}",
    f"T*.{QUEUE_RESERVED_SUFFIX}",
    f"T*.{QUEUE_OUTBOX_SUFFIX}",
    f"T*.{QUEUE_CTRL_IN_SUFFIX}",
    f"T*.{QUEUE_CTRL_OUT_SUFFIX}",
)
```

Then call the dead-task policy helper with:

- queue names from the snapshot
- `active_tids = self._active_runtime_tids()`
- `now_ns`
- `min_age_seconds = TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS`

Do not call `weft task list`, `weft status`, or any CLI code. Runtime cleanup
must use the in-process context and broker APIs.

Keep existing terminal-control records from
`store.list_terminal_control_cleanup_ready_tasks`. The new dead-TID source is
additional evidence for runtime queue cleanup, not a replacement for terminal
store disposition.

Tests to write before implementation:

- A dead TID that appears only because `T{tid}.ctrl_in` exists is selected.
- A dead TID that appears only because `T{tid}.outbox` exists is selected for
  control cleanup, but the outbox itself is not deleted.
- A live TID with standard control queues is skipped.
- A manager or monitor TID is skipped because it is live.

Use real broker queues and `WeftTestHarness` patterns already present in
`tests/tasks/test_task_monitor.py`. Do not replace broker operations with mocks.

### B3. Implement Idempotent Per-TID Control Queue Delete

Files to change:

- `weft/core/monitor/policies/dead_task.py`
- `weft/core/monitor/task_monitor.py`
- `weft/core/monitor/policies/runtime_control.py` if Slice A moved the shared
  control-delete helper there

Action:

Add a helper that deletes exactly these names for a dead TID:

```python
queue_names = (f"T{tid}.ctrl_in", f"T{tid}.ctrl_out")
```

Use `broker.delete_from_queues(queue_names)` or the exact released equivalent
if the SimpleBroker API name changed. Treat missing queues as success. Report
rows deleted and estimated queues deleted. If a pre-delete queue-name snapshot
is available, count only queues that were present in that snapshot as deleted
queues.

Before deleting, re-check that `tid` is not live. This can be done by passing
an up-to-date `active_tids` snapshot into the sub-slice and checking again
immediately before the delete. If a stronger live check helper already exists
by implementation time, use it instead of duplicating host-process logic.

Do not inspect or read the control queues before deletion. The policy is based
on control queue staleness after task death, not payload content.

Tests:

- Existing `T{tid}.ctrl_in` and `T{tid}.ctrl_out` rows are deleted for a dead
  TID.
- Missing `ctrl_out` does not fail cleanup of `ctrl_in`.
- Missing both queues counts as success with zero rows deleted.
- A simulated `delete_from_queues` failure records an error and does not mark
  control cleanup complete in the Monitor store.
- `T{tid}.outbox` remains present and readable after cleanup.
- `T{tid}.inbox` remains present if it existed.

Mocking rule:

It is acceptable to monkeypatch `Broker.delete_from_queues` to force one
failure-path test. Do not mock queue listing, live-owner discovery, or store
records for the normal path. Use real queues.

### B4. Integrate Dead-TID Work With Existing Bounds And Fairness

Files to change:

- `weft/core/monitor/task_monitor.py`
- `weft/core/monitor/policies/dead_task.py`
- `weft/core/monitor/policies/runtime_control.py` if it owns the control-delete
  helper after Slice A

Action:

The existing runtime cleanup slice already has:

- `control_queue_delete_limit`
- `TASK_MONITOR_RUNTIME_CLEANUP_SLICE_FAMILY_LIMIT`
- `TASK_MONITOR_RUNTIME_CLEANUP_SLICE_SECONDS`
- a control cleanup worker lane
- pending/family-limit/deadline accounting
- reserved cleanup interleaving

Reuse those controls. Do not add a new recurring worker.

Implementation shape:

1. Build the existing terminal-control record list.
2. Build dead-TID candidates from queue names minus live TIDs.
3. Merge the two sources into one oldest-to-newest work stream without
   processing the same TID twice in one slice.
4. Enqueue TIDs into a local worker queue in that deterministic order.
5. Keep reserved cleanup interleaving. Reserved cleanup must retain its own
   readiness checks.
6. Stop when the family limit or deadline is hit.
7. Set `pending=True` when unprocessed terminal records, unprocessed dead-TID
   candidates, reserved work, errors, or deadline remain.

If merging terminal records and dead-TID candidates makes the code hard to
reason about, use two explicit sub-slices under the same total budget:

1. terminal store records, oldest first
2. dead-TID queue-name candidates, oldest first
3. reserved cleanup interleaved as the existing code does today

The first implementation should prefer clarity over perfect scheduling. The
hard requirements are bounded work, no duplicate TID processing per slice, and
oldest-to-newest order inside each source.

Tests:

- With limit `1` and dead TIDs `100`, `200`, only `100` is processed.
- With a deadline hit after one TID, `pending=True` and later TIDs remain.
- A TID returned by both the store-ready source and dead-TID source is processed
  once.
- Reserved cleanup still runs when control work is batch-limited, matching the
  existing interleaving intent.
- Existing tests for terminal control cleanup and reserved cleanup still pass.

### B5. Add Bounded Worker Queue Internals

Files to change:

- `weft/core/monitor/policies/dead_task.py`
- `weft/core/monitor/task_monitor.py`
- `weft/_constants.py`

The requested algorithm uses a Python queue pulled by worker threads. In the
current TaskMonitor design, `_run_terminal_control_cleanup_worker` already runs
off the reactor thread in a dedicated worker lane. Put the bounded per-TID
worker queue inside that lane. Do not create a second recurring cleanup worker.

Action:

- Use `queue.Queue[str]` for TIDs.
- Use a fixed worker count from a private constant in `_constants.py`, default
  no more than `4`.
- Each worker must open fresh broker/store handles. Do not share Queue, Broker,
  or MonitorStore connection state across threads unless SimpleBroker and the
  store layer explicitly document that as safe.
- Preserve deterministic accounting. Aggregate worker results on the parent
  worker thread before returning to the reactor.
- Preserve oldest-to-newest enqueue order. Parallel completion order may differ,
  so result accounting must not rely on completion order.
- If thread-safety of either SimpleBroker broker handles or MonitorStore handles
  is not documented clearly enough by implementation time, keep the same
  `queue.Queue[str]` worker abstraction but run it with one worker. Document the
  reason in the code comment and plan follow-up. Do not guess.

Do not spawn one thread per TID.

Tests:

Test bounded worker count and deterministic result aggregation. Do not overfit
to thread timing. For the one-worker fallback, test that the queue abstraction
still processes oldest to newest and respects the same limits.

### B6. Implement Indexed Per-TID Task-Log Coalescing

Files to change:

- `weft/core/monitor/store.py`
- `weft/core/monitor/sql.py`
- `weft/core/monitor/task_monitor.py`
- `weft/core/monitor/policies/dead_task.py`
- `tests/tasks/test_task_monitor.py`
- `tests/core/test_monitor_store.py`

Stop gate:

Do not implement per-TID coalescing by scanning raw `weft.log.tasks` for every
candidate TID. Ops simulation showed that raw per-TID regex/body lookup is too
slow at production-like backlog sizes. If no indexed Monitor-store path exists
for the needed lookup and no efficient released broker API exists for this
exact operation, add the indexed path first.

Required abstraction:

Add one local helper that can fetch the coalesce group for a bounded batch of
TIDs. The helper may use either the released efficient broker API or the
Monitor store, but it must present one Weft-side contract to the cleanup
policy:

```python
def fetch_task_log_coalesce_groups_for_tids(
    tids: Sequence[str],
    *,
    limit: int,
) -> tuple[TaskLogCoalesceGroup, ...]:
    ...
```

Each coalesce group must include the TID, exact `weft.log.tasks` message IDs,
and decoded payloads or enough raw body data to reuse existing collation code.

If using the Monitor store, add a store method with this shape:

```python
def list_task_log_messages_for_tids(
    self,
    tids: Sequence[str],
    *,
    limit: int,
) -> tuple[MonitorRawMessageRef, ...]:
    ...
```

Preferred Monitor-store SQL shape:

- Query `weft_monitor_task_messages` by context key and TID.
- Return exact queue and message IDs.
- Bound by `limit`.
- Order by TID and message ID for deterministic behavior.
- Use existing Monitor table names and runner helpers.

If the released broker API is used instead, it must be efficient for a bounded
batch of TIDs and must not degrade into one full raw-log scan per TID. If the
only available broker API is substring-based message-ID lookup, use it only if
the implementation can prove bounded performance and exact TID matching after
payload decode.

If no efficient coalesce-group API is available for a dead TID, do not fall
back to raw per-TID scans. Leave task-log handling to the normal FIFO ingestion
pass. The dead-TID policy can still delete stale control queues without log
coalescing.

Task-log delete rules:

- Delete only exact raw message IDs that the Monitor store says are deletable,
  or that become deletable after the fetched coalesce group is folded through
  the existing Monitor collation and summary/disposition rules.
- Use `apply_exact_prune_candidates(...)` or the existing
  `_delete_monitor_store_task_log_rows` path.
- After broker delete succeeds or reconciliation proves the row is gone, call
  `store.delete_task_messages_after_raw_delete(...)`.
- Do not delete raw task-log rows just because a TID is dead. Deadness is
  runtime cleanup evidence, not lifecycle truth.

Tests:

- Store API returns only message refs for requested TIDs.
- Store API respects `limit`.
- Coalesce-group helper returns exact groups for requested TIDs and excludes
  similarly prefixed TIDs.
- Cleanup deletes exact retained task-log rows for a dead TID only when Monitor
  state already marks them deletable under existing summary/disposition rules.
- Cleanup does not delete unrelated TID log rows.
- Cleanup with no Monitor rows for a dead TID deletes controls but leaves raw
  `weft.log.tasks` rows to FIFO ingestion.
- Add a spy around the store API in one test to prove the runtime cleanup path
  uses the indexed/batched helper rather than a raw per-TID scanner. This is a
  valid spy because it protects the performance invariant. Do not mock the
  broker for the normal deletion behavior.

### B7. Update Stats And PONG Summary

Files to change:

- `weft/core/monitor/task_monitor.py`
- `weft/core/monitor/policies/dead_task.py`
- `weft/commands/task_monitor.py` only if it renders named fields that need
  display support
- tests that assert PONG/status summary fields

Action:

Extend the existing `_TaskControlCleanupResult` or its moved equivalent. Do
not create parallel stats that the reactor has to merge manually.

Suggested fields:

- `dead_tids_discovered`
- `dead_tids_processed`
- `dead_tids_skipped_live`
- `dead_tids_skipped_too_young`
- `dead_tids_pending`
- `dead_tid_control_queues_deleted`
- `dead_tid_control_rows_estimated_deleted`
- `dead_tid_log_refs_selected`
- `dead_tid_log_rows_deleted`

Keep field names plain and stable. Existing fields such as `queues_deleted`,
`rows_estimated_deleted`, `pending`, `errors`, `warnings`, `family_limit_hit`,
and `deadline_hit` should continue to mean what they mean today.

Tests:

- PONG/status cached cleanup summary reports that the dead-TID policy ran and
  selected zero when there are no candidates.
- PONG/status cached cleanup summary reports discovered and processed dead
  TIDs when cleanup runs.
- PONG does not trigger queue scans. It reports the last cached worker result.

### B8. Update Specs And Traceability

Files to change:

- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- this plan
- `docs/plans/README.md`

Action:

Update [MF-5] cleanup boundary to say the supervised TaskMonitor derives
dead-task cleanup candidates from standard task-local queue names and live TID
subtraction. Make clear that this applies to standard `ctrl_in` and `ctrl_out`
only, and that task-owned cleanup remains the primary path on normal exit.

Update [OBS.13], [OBS.16], or [OBS.17] only if the implementation changes an
invariant or PONG-visible behavior beyond what those sections already say.

Add this plan to the relevant `Related Plans` section if it is not already
linked there.

Do not mark this plan `completed` until implementation and verification land.

## Test Design Guidance

Prefer tests that exercise the actual queue and store behavior. The common bad
test for this change would mock queue listing, mock deletion, mock liveness,
and then assert that the mocks were called. That would miss the real bug class:
wrong queue names, wrong active-owner protection, wrong exact delete behavior,
or raw log scans that work in tests but fail in production.

Use real broker-backed tests for:

- queue-name snapshots
- queue creation and deletion
- outbox preservation
- active-owner skips
- Monitor-store marks
- exact raw-message delete reconciliation

Use pure unit tests for:

- queue-name parser behavior
- TID ordering
- live subtraction
- age gating

Use monkeypatch/spies only for:

- forcing one broker delete failure
- proving the indexed/batched coalesce API is used instead of a raw per-TID
  task-log scan
- checking bounded worker count for the local worker queue

Avoid sleep-based tests. Use explicit `now_ns` values and old synthetic TIDs
where age matters.

## Verification Gates

Run the narrow tests after each task. Before calling the implementation done,
run:

```bash
./.venv/bin/python -m pytest tests/core/test_task_monitor_cleanup.py -q
./.venv/bin/python -m pytest tests/core/monitor/policies/test_dead_task.py -q
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py -q
./.venv/bin/python -m pytest tests/core/test_task_monitoring.py -q
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
./.venv/bin/ruff check weft tests/core/test_task_monitor_cleanup.py tests/tasks/test_task_monitor.py tests/core/monitor/policies
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
```

Then run the full test suite:

```bash
./.venv/bin/python -m pytest
```

If the full suite has unrelated pre-existing failures, record exact failing
test names and commands. Do not weaken cleanup tests to make unrelated failures
less visible.

## Operational Check

After implementation, run a dry simulation against a realistic broker before
allowing destructive cleanup in a production-like environment.

Suggested read-only simulation:

1. Use `broker.list_queues()` to collect standard `T*.*` names.
2. Use the runtime live-TID helper to collect live TIDs.
3. Print counts for historical TIDs, live TIDs, dead candidates, candidate
   control queues, and candidate control rows if queue stats are cheap.
4. Estimate delete batches under `control_queue_delete_limit`.
5. Estimate wall time from batch count and observed `delete_from_queues(...)`
   timing.
6. Estimate log coalescing work only through indexed Monitor-store queries or
   an efficient released broker coalesce-group API. Never estimate or implement
   it as one raw-log scan per TID.

Expected efficacy based on the ops simulation performed before this plan:

- Queue-name listing plus live subtraction should identify nearly all residual
  standard task-local control queues. In the inspected ops snapshot, there were
  thousands of stale candidate control queues and only a few live service TIDs.
- Control queue deletion should be fast enough when using multi-queue delete.
  Planning assumption: seconds to low minutes, depending on configured per-cycle
  limits and backlog size.
- Raw per-TID task-log scans are not viable. Planning assumption: a raw
  per-TID body scan can be hundreds of milliseconds per TID at current backlog
  sizes, which makes a full cleanup take hours. Indexed Monitor-store lookup or
  an efficient released broker coalesce-group API is the required path.

## Rollback

Slice A rollback:

- Revert the policy module extraction if tests fail in a way that cannot be
  fixed locally.
- Because Slice A is no-behavior-change, rollback should be a straightforward
  import and file move reversal.

Slice B rollback:

- Disable the dead-TID candidate source by guarding it behind the existing
  runtime cleanup configuration path. Do not disable task-owned cleanup.
- Keep existing terminal-control and reserved cleanup paths intact.
- If a new env knob is added despite the YAGNI guidance, default it to disabled
  for rollback. The preferred first implementation avoids a new knob and uses
  existing monitor cleanup enablement and limits.

Data rollback:

- Deleted `ctrl_in` and `ctrl_out` queues are runtime-only. There is no data
  restoration requirement for successful cleanup of dead TIDs.
- `outbox`, `inbox`, and reserved queues must be preserved by this work. If a
  test or review shows those queues can be deleted by the new path, stop and
  fix before merging.
- Retained task-log rows must only be deleted through exact existing retention
  policy. If that invariant is violated, revert Slice B and investigate before
  retrying.

## Independent Review Required

This change crosses runtime cleanup, queue deletion, Monitor store, and
retained task-log behavior. Before implementation is marked complete, run an
independent review pass focused on:

- Whether the new dead-TID source can delete active task controls under a race.
- Whether any path deletes outbox, inbox, manager controls, custom controls, or
  reserved queues outside existing policy.
- Whether task-log coalescing uses indexed/batched paths only and never raw
  per-TID log scans.
- Whether pending/error accounting is correct when queue delete or store marks
  fail.
- Whether the refactor introduced import cycles or a hidden public API.

Do not treat a passing test suite as enough for this change.

## Out Of Scope

- No outbox cleanup.
- No inbox cleanup.
- No new result retention policy.
- No SimpleBroker schema changes unless the released indexed APIs are missing
  and the stop gate is explicitly reopened.
- No public CLI changes.
- No new plugin or policy framework.
- No rewrite of task lifecycle state reconstruction.
- No direct SQL deletes against broker message rows from Weft cleanup code.

## Fresh-Eyes Self-Review

Review pass 1 found a likely bad direction: implementing a separate dead-task
sweeper beside `_run_terminal_control_cleanup_slice`. This plan now folds
dead-TID candidates into the existing TaskMonitor runtime cleanup lane.

Review pass 2 found an ambiguity in "dedicated worker thread per TID". This
plan interprets that as a dedicated worker turn per TID inside a bounded local
queue and explicitly forbids one OS thread per historical TID. The worker-pool
step is required, with a one-worker fallback only if broker/store thread safety
is not documented clearly enough.

Review pass 3 found a performance trap in per-TID task-log coalescing. This
plan now has a hard stop gate against raw per-TID scans and requires an indexed
Monitor-store path, an efficient released broker coalesce-group API, or no
per-TID log coalescing.

Review pass 4 checked scope drift. The plan still matches the requested
direction: reorganize cleanup policies first, then target dead tasks by queue
identity and live-TID subtraction, delete only standard stale control queues,
and preserve outbox policy boundaries.

Review pass 5 found that using stale-open-family age as the dead-TID grace
would delay control cleanup for too long. The plan now uses the existing
TID-mapping cleanup grace, which better matches runtime-liveness uncertainty
without turning stale controls into a week-long backlog.

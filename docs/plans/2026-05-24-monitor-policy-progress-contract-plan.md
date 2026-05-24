# Monitor Policy Progress Contract Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.3], [CC-3.4]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.1], [SB-0.3]; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13], [OBS.16], [OBS.17]
Superseded by: none

## 1. Goal

Define and implement a rigorous progress contract for every TaskMonitor cleanup
policy. Each policy must report two distinct conditions: "no more work for
now" and "no more work to be done at this point in time." The first condition
means the policy reached a bounded waypoint and should yield; the second means
the policy reached a base case for the current `now_ns`, current configuration,
and current queue/store state. This plan turns the recursive-function mental
model into code, cached PONG diagnostics, scheduling decisions, and tests.

This is not a redesign of TaskMonitor storage, manager supervision, or
SimpleBroker queue semantics. It is a contract and instrumentation hardening
slice for the cleanup policies that already exist.

## 2. Source Documents

Read these before editing code:

- `AGENTS.md`: repo philosophy, SimpleBroker boundary, constants rule, and
  real-broker test expectations.
- `docs/agent-context/README.md`,
  `docs/agent-context/decision-hierarchy.md`,
  `docs/agent-context/principles.md`, and
  `docs/agent-context/engineering-principles.md`: shared engineering rules.
- `docs/agent-context/runbooks/writing-plans.md`: required plan structure and
  zero-context requirements.
- `docs/agent-context/runbooks/hardening-plans.md`: mandatory here because
  this changes destructive cleanup scheduling, deferred work semantics,
  runtime-only queues, and cached operational diagnostics.
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`: required
  review workflow. This plan needs external review before implementation.
- `docs/agent-context/runbooks/testing-patterns.md`: required test harness
  guidance.
- `docs/specifications/01-Core_Components.md` [CC-2.3], [CC-3.4]:
  TaskMonitor is a supervised service task. Its reactor must remain
  responsive; long cleanup runs happen in worker lanes and report cached
  diagnostics.
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.1], [SB-0.3]:
  Weft uses public SimpleBroker queue APIs for broker queue operations.
  Monitor-owned SQL is allowed only for Monitor-owned tables.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]: retained
  task-log cleanup, Monitor-store collation, raw exact deletion, summaries,
  disposition, runtime queue cleanup, and PONG diagnostics.
- `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.16],
  [OBS.17]: Monitor cleanup is operational, bounded, exact, and separate from
  public lifecycle truth.
- `docs/plans/2026-05-23-monitor-cleanup-policy-convergence-plan.md`:
  completed immediate convergence fixes. This new plan makes the proof
  contract explicit for all policies.
- `docs/plans/2026-05-23-monitor-cleanup-executor-plan.md`: completed
  executor work. It is useful context, but current specs now require discrete
  runtime cleanup slices rather than mixed worker results.
- `docs/plans/2026-05-22-monitor-policy-modules-and-dead-task-cleanup-plan.md`:
  completed policy module split and dead-task cleanup context.
- `docs/plans/2026-05-20-monitor-collation-table-retirement-plan.md`:
  completed parent-row retirement context.
- `docs/plans/2026-05-19-monitor-terminal-retirement-and-runtime-queue-cleanup-plan.md`:
  completed runtime cleanup context.
- `docs/plans/2026-05-18-monitor-table-driven-retained-log-cleanup-plan.md`:
  table-driven retained log cleanup context. It is still draft; do not treat it
  as normative.

Comprehension checks before editing:

- Which method scans retained `weft.log.tasks` into the Monitor table, and
  which field currently proves FIFO high-water completion?
- Which TaskMonitor fields are cached into PONG, and why must PING not run any
  live queue scans or Monitor-store queries?
- Which Monitor-store queries can cheaply prove an empty base case, and which
  policies can only prove a bounded waypoint?
- Which queue operations must use public SimpleBroker APIs instead of
  backend-specific broker table SQL?

## 3. Context And Key Files

Files to modify:

- `weft/core/monitor/progress.py`
  - New module for the shared policy progress dataclass and helpers.
  - Keep this module small and policy-neutral.
- `weft/core/monitor/task_monitor.py`
  - Owns built-in cycle worker results, cached PONG fields, retained FIFO
    ingestion, summary/raw delete/orphan/tombstone/retirement steps, runtime
    cleanup worker result handling, and catch-up scheduling.
- `weft/core/monitor/cleanup.py`
  - Owns legacy cleanup orchestration for `weft.state.tid_mappings` and the
    old window-based task-log policies.
- `weft/core/monitor/policies/tid_mapping.py`
  - Owns `weft.state.tid_mappings` malformed and older-than policies.
- `weft/core/monitor/policies/task_log.py`
  - Owns legacy `weft.log.tasks` malformed, claimed, window collation,
    terminal-without-start, and old-without-start policies.
- `weft/core/monitor/policies/reserved.py`
  - Owns legacy terminal reserved-queue cleanup tied to collated groups.
- `weft/core/monitor/policies/dead_task.py`
  - Owns name-derived dead-TID selection and dead-task task-log coalesce
    lookup helpers.
- `weft/core/monitor/policies/runtime_control.py`
  - Owns runtime cleanup result and policy helpers for terminal, reserved, and
    dead-TID cleanup.
- `weft/core/monitor/store.py`
  - Owns Monitor table access methods and cheap base-case queries for
    summaries, raw refs, orphan recovery, tombstones, and parent retirement.
- `weft/core/monitor/sql.py`
  - Owns Monitor-table SQL construction. Follow existing safe identifier and
    placeholder helpers.
- `weft/_constants.py`
  - Touch only if a named policy/progress constant is necessary. Prefer
    existing names and string policy ids.
- `docs/specifications/01-Core_Components.md`
- `docs/specifications/04-SimpleBroker_Integration.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/plans/README.md`
- Tests:
  - `tests/core/monitor/policies/test_dead_task.py`
  - `tests/core/monitor/policies/test_runtime_control.py`
  - `tests/core/test_monitor_sql.py`
  - `tests/core/test_monitor_store.py`
  - `tests/core/test_task_monitor_cleanup.py`
  - `tests/tasks/test_task_monitor.py`

Files to read but not casually modify:

- `weft/core/pruning/models.py`: current cleanup stat and candidate model.
  Reuse concepts where useful; do not make pruning own Monitor-specific
  scheduling semantics.
- `weft/core/pruning/apply.py`: canonical exact-delete application.
- `weft/core/monitor/task_log_scanner.py`: generator-backed task-log history
  scans.
- `weft/core/monitor/collation.py`: pure reducer from task-log payloads to
  Monitor-store updates.
- `weft/core/tasks/service.py`: shared service reactor lane behavior.
- `tests/helpers/weft_harness.py`: real service/CLI harness.
- `../simplebroker/simplebroker/_sql.py` and nearby SimpleBroker table access
  examples: safe SQL construction patterns for Monitor-owned tables.

Do not modify:

- `weft/core/manager.py`: manager supervision is not the policy progress owner.
- `weft/commands/status.py` or `weft/commands/result.py`: public lifecycle and
  result reconstruction must not read Monitor-owned progress state.
- `../simplebroker`: this slice should use the installed SimpleBroker APIs.

Current structure:

- Built-in monitor cycles run on a TaskMonitor worker lane and return cached
  results to the reactor.
- Runtime queue cleanup runs on a separate TaskMonitor worker lane after
  Monitor-store high-water work makes it ready.
- PONG uses cached fields from the last completed cycle or worker result.
- Legacy cleanup policies still produce `CleanupQueueStats` and
  `CleanupPolicyStats`, but those stats do not rigorously distinguish
  waypoint, base case, deferred future work, and blocked errors.

## 4. Terminology And Required Progress Contract

Every policy result must report the same concepts.

Use one shared dataclass in `weft/core/monitor/progress.py`:

```python
@dataclass(frozen=True, slots=True)
class PolicyProgress:
    policy: str
    domain: str
    scanned: int = 0
    selected: int = 0
    applied: int = 0
    deferred: int = 0
    source_total: int | None = None
    waypoint_reached: bool = False
    base_reached: bool = False
    blocked_reason: str | None = None
    reason_counts: Mapping[str, int] | None = None
```

Keep this dataclass plain. Do not add callbacks, inheritance, policy
registration, or an event bus.

Definitions:

- `waypoint_reached=True`: the policy reached a bounded stop and yielded. It
  made progress or hit a cap/deadline/scan limit. The monitor should schedule
  another catch-up cycle soon unless a higher-priority error policy says
  otherwise.
- `base_reached=True`: at this fixed `now_ns`, fixed config, and current
  queue/store state, the policy has no eligible work now. Future time or new
  rows may make work eligible later.
- `blocked_reason is not None`: the policy could not prove base or complete
  selected work because of an error or external dependency. Blocked is not
  base, and it is not automatically a catch-up waypoint.
- `deferred`: entries are known but not eligible now, such as active owners,
  too-young rows, not-ready reserved queues, or retention-gated outbox queues.
  Deferred entries are base for now, not pending work.

Invariants:

- `base_reached` and `waypoint_reached` must not both be true.
- `blocked_reason` implies `base_reached=False`.
- `blocked_reason` alone must not imply catch-up cadence. A blocked policy may
  also set `waypoint_reached=True` only when it made bounded progress and
  should retry on catch-up cadence. Persistent external or store failures
  should use the existing error/backoff behavior, not a hot loop.
- `selected == batch_size` normally implies `waypoint_reached=True` unless the
  policy also ran a cheap `limit=1` probe proving no more eligible work.
- `selected == 0` does not imply `base_reached=True` unless the policy proves
  domain exhaustion, a monotonic cutoff, or a cheap empty query.
- PONG may expose progress from cached cycle state only. It must not compute
  progress live.

## 5. Policy Base And Waypoint Definitions

This section is the contract the implementation must encode and test.

### 5.1 `tid_mapping.delete_malformed`

Eligible work: malformed rows in `weft.state.tid_mappings`.

Waypoint, "no more work for now":

- selected/applied count reaches the configured policy batch limit
- the queue scan reaches its configured window before proving exhaustion
- exact delete fails

Base case, "no more work to be done now":

- a complete visible scan of `weft.state.tid_mappings` finds zero malformed
  rows

Important: a bounded scan with zero malformed rows is not base if the scan hit
its limit.

### 5.2 `tid_mapping.delete_older_than`

Eligible work: valid TID mapping rows older than
`TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS`, excluding protected TIDs.

Waypoint:

- selected/applied count reaches the batch limit
- scan limit is reached before queue exhaustion or monotonic cutoff
- exact delete fails

Base case:

- FIFO scan sees the first valid non-selected row that is too young, because
  message IDs are time ordered
- or the queue is exhausted

Malformed rows are not the base for this policy. The malformed policy owns
them.

### 5.3 `task_log.delete_malformed`

Eligible work: malformed rows in `weft.log.tasks`.

Waypoint:

- selected/applied count reaches the batch limit
- task-log scan limit is reached before high-water
- exact delete fails

Base case:

- a complete visible scan of the relevant `weft.log.tasks` domain finds zero
  malformed rows

No age cutoff proves base for malformed rows. They can appear anywhere in the
domain.

### 5.4 `task_log.delete_claimed`

Eligible work: claimed rows in `weft.log.tasks`.

Waypoint:

- claimed count is greater than the rows selected/deleted in this slice
- selected/applied count reaches the batch limit
- claimed-row retrieval or exact delete fails

Base case:

- `queue.stats().claimed == 0` for `weft.log.tasks`

This is a cheap and strong base case. Expose it directly.

### 5.5 `task_log.collate_complete_lifecycle`

Eligible work: old task-log families where the policy sees at least one start
row and a terminal row for the same TID, excluding already-claimed rows and
protected TIDs.

Waypoint:

- the next family would exceed the remaining batch budget
- selection limit is reached
- scan limit is reached before high-water
- exact delete fails

Base case:

- a complete scan of the eligible retained domain finds no complete lifecycle
  groups
- or the scan reaches the first too-young row in a FIFO mode that started from
  the oldest unprocessed point

Do not treat "no complete family in a bounded window" as base if the scan hit
its limit.

### 5.6 `task_log.collate_terminal_without_start`

Eligible work: old terminal task-log row with no visible start for the same TID
before it in the scanned retained domain.

Waypoint:

- selected/applied count reaches the batch limit
- scan limit is reached before high-water
- exact delete fails

Base case:

- a complete scan of retained eligible rows finds no terminal-without-start
  rows
- or FIFO age ordering reaches a too-young row after scanning from the oldest
  unprocessed point

### 5.7 `task_log.delete_old_without_start`

Eligible work: old task-log rows not claimed, not protected, and not part of a
visible open started family.

Waypoint:

- selected/applied count reaches the batch limit
- scan limit is reached before high-water
- exact delete fails

Base case:

- FIFO scan reaches first too-young row
- or the queue/domain is exhausted

Skipped open families are deferred, not pending actionable work for this
policy.

### 5.8 Retained FIFO Monitor-store ingestion

Eligible work: visible `weft.log.tasks` rows after the Monitor checkpoint in
collated-store mode.

Waypoint:

- `batch_limit`
- `TASK_MONITOR_TASK_LOG_SCAN_LIMIT_REACHED`
- store write error
- exact raw delete error
- Monitor child-row delete error

Base case:

- scan from the durable Monitor checkpoint reaches queue high-water with no
  store errors, no delete errors, and no scan/batch limit

The existing `completed_fifo_high_water` is the right base-case concept. Keep
it and expose it through `PolicyProgress`.

### 5.9 Monitor summary emission and disposition

Eligible work: Monitor collation rows whose summaries are ready by terminal or
stale-open rules and whose summary/disposition is incomplete.

Waypoint:

- the query returns exactly the batch limit and a follow-up `limit=1` probe or
  over-fetch says more rows remain
- external log sink blocks or errors
- store mark error occurs

Base case:

- `list_summary_ready_tasks(limit=1, now_ns=...)` returns no rows

Terminal task summaries should not wait for general task-log retention when
the spec says terminal retention is zero.

### 5.10 Monitor raw deletion from stored message refs

Eligible work: `weft_monitor_task_messages` refs whose parent Monitor family
has terminal or suspect proof and summary requirements satisfied.

Waypoint:

- the ref query returns the batch limit and more refs may remain
- exact raw delete fails
- Monitor child-ref reconciliation fails

Base case:

- `list_deletable_task_log_messages(limit=1, require_summary=True)` returns no
  rows

### 5.11 Orphan raw task-log recovery

Eligible work: Monitor parent rows that are terminal/suspect/disposed or
summary-emitted, have `raw_deleted_at_ns` set, have no child refs, but may
still have raw `weft.log.tasks` rows.

Waypoint:

- candidate TID selection reaches the batch limit
- per-TID `find_message_ids` reaches its chunk limit and more matches may
  remain
- exact delete or Monitor-store update fails

Base case:

- `list_raw_deleted_task_log_recovery_tids(limit=1)` returns no rows
- for a selected TID, `find_message_ids(... tid ...)` returns no true rows for
  that TID

This is a recovery policy, not the ordinary FIFO cleanup authority.

### 5.12 Monitor child tombstone pruning

Eligible work: legacy `weft_monitor_task_messages` rows with `deleted_at_ns is
not null`.

Waypoint:

- pruning deletes exactly the configured limit
- store delete/reconcile error occurs

Base case:

- `deleted_task_message_refs(limit=1)` returns no rows

### 5.13 Monitor collation family retirement

Eligible work: compact parent rows whose raw deletion, summary/disposition,
task-local control cleanup, and child-ref absence are all recorded.

Waypoint:

- retirement deletes exactly the configured limit
- store delete error occurs

Base case:

- `list_retirable_task_collations(limit=1)` returns no rows

### 5.14 Terminal runtime queue cleanup

Eligible work: Monitor collation rows ready for task-local runtime cleanup
where summary is emitted, terminal retained or disposed state is present, and
`task_control_deleted_at_ns is null`.

Waypoint:

- more ready records remain than the slice limit
- worker deadline hits
- queue delete or store mark fails

Base case:

- `list_terminal_control_cleanup_ready_tasks(limit=1, ...)` returns no rows

Nonstandard task-local control shapes are not pending work. They must be
classified as skipped and then dispositioned according to the existing
terminal cleanup rule.

### 5.15 Runtime reserved queue cleanup

Eligible work: standard `T{tid}.reserved` queue for a non-active TID where the
Monitor row proves raw deletion, disposition, or terminal plus summary; if no
Monitor row exists, the TID must be older than task-log retention.

Waypoint:

- selected ready reserved queues reach the slice limit
- selection or worker deadline hits
- queue delete fails

Base case:

- complete reserved queue-name snapshot has no ready reserved queues

Active reserved queues, not-ready Monitor rows, and not-yet-retained TIDs are
deferred for now. They must not keep `pending=True`.

### 5.16 Dead-task runtime queue cleanup

Eligible work: standard `T{tid}.ctrl_in`, `T{tid}.ctrl_out`, `T{tid}.inbox`,
and retention-eligible `T{tid}.outbox` or `T{tid}.reserved`, where the TID is
not live, old enough for dead-task detection, has no Monitor row owning the
family, and at least one queue in the cleanup plan exists now.

Waypoint:

- actionable dead TID count exceeds the slice limit
- selection or worker deadline hits
- queue delete fails

Base case:

- complete task-local queue-name snapshot contains no actionable dead TIDs

Live TIDs, too-young TIDs, and outbox/reserved-only TIDs that are not yet
retention eligible are deferred for now. They must not keep the worker pending
and must not trigger raw task-log coalescing.

### 5.17 Dead-task task-log coalesce/delete

Eligible work: raw `weft.log.tasks` rows for a dead TID after runtime queues
were actually deleted for that TID.

Waypoint:

- per-TID message-id search reaches the chunk limit with possible more matches
- exact delete or Monitor-store update fails

Base case:

- no successful dead-TID queue deletion fed this policy
- or for every fed TID, `find_message_ids(... tid ...)` returns no true rows

### 5.18 Raw external task-log mode

Eligible work: retained visible `weft.log.tasks` rows older than retention,
excluding the monitor TID, when raw external logging owns task-log deletion.

Waypoint:

- selected/applied count reaches batch limit
- scan limit is reached before high-water
- external log emit blocks/fails
- exact delete fails

Base case:

- FIFO scan reaches the first too-young row
- or queue high-water is reached with no eligible rows

## 6. Invariants And Constraints

Preserve these invariants:

- `weft.log.tasks` remains runtime lifecycle evidence while retained. It is not
  audit evidence, legal retention evidence, result authority, or public status
  truth.
- Monitor-owned tables are derived operational state only.
- Public task status, result materialization, and manager service convergence
  must not depend on Monitor progress rows.
- PING/PONG must remain lightweight. PONG may only return cached progress from
  completed cycles and in-flight flags.
- The TaskMonitor reactor must not perform broker scans, Monitor table queries,
  or destructive cleanup while handling control messages.
- Cleanup of broker queue rows must use public SimpleBroker queue APIs and
  exact message ids or public multi-queue delete APIs. No ad hoc SQL against
  SimpleBroker broker queue tables.
- Monitor-owned SQL must stay inside `weft/core/monitor/sql.py` and
  `weft/core/monitor/store.py`, using existing safe identifier and placeholder
  helpers.
- Runtime-only queues remain runtime-only. Do not turn `weft.state.*` queues
  into public history or audit tables.
- Do not add new dependencies.
- Do not add a policy plugin framework, registry, or broad state-machine
  library. A small shared progress dataclass plus explicit per-policy
  constructors is enough.
- Do not make one policy's base case imply another policy's base case.
- Do not mark retention-deferred future work as pending actionable work.
- Do not call a policy converged just because it selected zero rows. It must
  prove a base condition.

Failure semantics:

- Exact delete failures are blocked work, not base.
- External logging failures block deletion for rows whose policy requires
  logging before deletion.
- Store availability failures block Monitor-store policies but must not stop
  the TaskMonitor service from responding to PING/STATUS.
- Idempotent absence after exact delete is not an error when the policy used
  the existing reconcile-missing path and the row is proven absent.

Rollback:

- Because this changes scheduling and diagnostics but not queue names or
  external CLI syntax, rollback is code rollback. Do not perform any schema
  migration that older code cannot ignore.
- If a new Monitor progress module is added, keep it internal. Do not persist
  progress rows in Monitor tables.

Review gates:

- Self-review is required before calling the plan complete. It is recorded at
  the end of this plan.
- External review is required before implementation because this affects
  destructive cleanup, background scheduling, and ops diagnostics. Use a
  different agent family if available. Do not implement from this draft until
  that review is complete or explicitly waived by the user.

## 7. Tasks

1. Add the shared progress model without changing behavior.
   - Outcome: all later work has one vocabulary for base, waypoint, blocked,
     and deferred conditions.
   - Files to touch:
     - `weft/core/monitor/progress.py`
     - `tests/core/monitor/test_progress.py` or
       `tests/core/test_task_monitor_cleanup.py` if the repo prefers no new
       test file for small helpers
   - Read first:
     - `weft/core/pruning/models.py`
     - `weft/core/monitor/policies/runtime_control.py`
   - Implementation:
     - Add `PolicyProgress` with `to_summary()`.
     - Add helper constructors only when they remove repeated boilerplate,
       such as `base_progress(...)`, `waypoint_progress(...)`, and
       `blocked_progress(...)`.
     - Add an aggregate helper `progress_requires_catchup(progresses)` that
       returns true when any progress has `waypoint_reached`.
     - Do not make `blocked_reason` imply catch-up by default.
     - Do not add inheritance, callbacks, registries, generic policy runners,
       or persistence.
   - Tests:
     - unit-test serialization and invariant checks
     - prove `blocked_reason` prevents base
     - prove base and waypoint cannot both be true
   - Done when:
     - helper tests pass
     - no production scheduling behavior has changed
   - Stop if:
     - the helper starts to know about specific queues, stores, or policy ids

2. Wire cached progress through PONG without changing scheduling.
   - Outcome: Monitor PONG exposes last-cycle policy progress, but catch-up
     still uses the old decision path.
   - Files to touch:
     - `weft/core/monitor/task_monitor.py`
     - `tests/tasks/test_task_monitor.py`
   - Read first:
     - current `_task_monitor_pong_extension()` in `task_monitor.py`
     - [MF-5] PONG diagnostics text
   - Implementation:
     - Add `_last_policy_progress: tuple[dict[str, Any], ...] = ()`.
     - Include it in PONG under a clear key such as `policy_progress`.
     - In this task, populate progress only for the top-level retained FIFO
       ingest result using existing fields.
     - Keep existing `cleanup_policy_stats` and `cleanup_queue_stats` for now.
       Removing them is out of scope.
   - Tests:
     - use a real `TaskMonitor` and real broker-backed queue
     - assert PONG contains cached progress after a cycle
     - assert PONG does not run a fresh cleanup cycle
   - Done when:
     - targeted PONG tests pass
     - scheduling behavior is unchanged
   - Stop if:
     - PING starts opening Monitor-store handles or scanning queues

3. Convert legacy `weft.state.tid_mappings` policies to report progress.
   - Outcome: malformed and older-than TID mapping policies report base,
     waypoint, and blocked conditions independently.
   - Files to touch:
     - `weft/core/monitor/policies/tid_mapping.py`
     - `weft/core/monitor/cleanup.py`
     - `tests/core/test_task_monitor_cleanup.py`
   - Read first:
     - `weft/core/pruning/policies/older_than.py`
     - `weft/core/queue_window.py`
   - Implementation:
     - Keep existing candidate selection.
     - Return or attach two `PolicyProgress` records: one for malformed, one
       for older-than.
     - Define base for malformed only when the scan did not hit the limit.
     - Define base for older-than when `first_tid_mapping_too_young` or queue
       exhaustion is observed.
     - Report exact-delete errors as blocked progress.
   - Tests:
     - malformed base: complete scan with no malformed rows
     - malformed waypoint: more malformed rows than batch size
     - older-than base: first valid row too young
     - older-than deferred: excluded TID is not selected and does not create
       pending work
   - Done when:
     - existing cleanup tests still pass
     - new tests prove base and waypoint separately
   - Stop if:
     - candidate selection changes for reasons unrelated to progress reporting

4. Convert legacy `weft.log.tasks` window policies to report progress.
   - Outcome: every legacy task-log cleanup policy reports whether it reached a
     base, waypoint, blocked state, or deferred-only state.
   - Files to touch:
     - `weft/core/monitor/policies/task_log.py`
     - `weft/core/monitor/task_log_scanner.py` only if scan-window summaries
       need one additional high-water field
     - `weft/core/monitor/cleanup.py`
     - `tests/core/test_task_monitor_cleanup.py`
   - Read first:
     - `weft/core/monitor/task_log_scanner.py`
     - `weft/core/monitor/task_log_collation.py`
   - Implementation:
     - Add separate progress records for:
       - `task_log.delete_malformed`
       - `task_log.delete_claimed`
       - `task_log.collate_complete_lifecycle`
       - `task_log.collate_terminal_without_start`
       - `task_log.delete_old_without_start`
     - Claimed base must come from `queue.stats().claimed == 0`.
     - Window collation base must require complete scan or monotonic
       too-young cutoff. Do not treat "no groups in a limited window" as base.
     - Old-without-start base may use `first_task_log_too_young`.
     - Open started families count as deferred, not pending.
   - Tests:
     - one test per policy for base
     - one test per policy for batch/scan waypoint
     - one test showing an open family does not block completed family
       selection later in the scan
     - one test showing no-selection-with-scan-limit is not base
   - Done when:
     - policy stats and progress stats agree on selected/deleted counts
     - new progress records make the "why nothing happened" visible
   - Stop if:
     - the change tries to make legacy window cleanup the main collated-store
       authority again

5. Convert retained FIFO Monitor-store ingestion to produce formal progress.
   - Outcome: the strongest existing base case,
     `completed_fifo_high_water`, becomes a first-class progress record.
   - Files to touch:
     - `weft/core/monitor/task_monitor.py`
     - `tests/tasks/test_task_monitor.py`
   - Implementation:
     - Emit a progress record for retained FIFO ingestion.
     - Base when `completed_fifo_high_water=True`.
     - Waypoint for `batch_limit` and
       `TASK_MONITOR_TASK_LOG_SCAN_LIMIT_REACHED`.
     - Blocked for store write, exact delete, or child-row delete errors.
     - Include malformed, valid ingested, and raw deleted counts in
       `reason_counts`.
   - Tests:
     - existing retained-ingest tests should assert progress fields
     - add a red-green test for scan-limit reached not being base
     - add a test for store-write error becoming blocked progress
   - Done when:
     - PONG identifies FIFO base, waypoint, or blocked state after each cycle
   - Stop if:
     - checkpoint advancement semantics need to change again. That is a
       separate correctness problem.

6. Add Monitor-store policy progress for summary, raw delete, orphan recovery,
   tombstone pruning, and parent retirement.
   - Outcome: every Monitor-table follow-on policy reports whether it has more
     work now.
   - Files to touch:
     - `weft/core/monitor/task_monitor.py`
     - `weft/core/monitor/store.py`
     - `weft/core/monitor/sql.py`
     - `tests/core/test_monitor_store.py`
     - `tests/tasks/test_task_monitor.py`
   - Implementation:
     - Add cheap `limit=1` or over-fetch methods where needed:
       - summary-ready exists
       - deletable raw refs exist
       - orphan recovery TIDs exist
       - deleted child tombstones exist
       - retirable parent collations exist
     - Prefer over-fetch `limit + 1` when already querying a batch.
     - Report waypoint when a batch fills and more work is proven or plausible.
     - Report base when the cheap empty probe returns no work.
     - Do not persist progress in Monitor tables.
   - Tests:
     - store-level tests should use real MonitorStore tables
     - task-monitor tests should run enough cycles to prove repeated batches
       converge to base
     - include an orphan recovery fixture where no child refs remain but raw
       rows still exist
   - Done when:
     - each Monitor-store policy has a progress record in cached PONG
     - follow-on policies can independently request catch-up cadence
   - Stop if:
     - the implementation wants to scan raw `weft.log.tasks` from PONG or
       public status commands

7. Convert runtime queue cleanup progress to explicit slice results.
   - Outcome: terminal cleanup, reserved cleanup, dead-TID cleanup, and
     dead-task log coalescing each report base, waypoint, deferred, and blocked
     conditions.
   - Files to touch:
     - `weft/core/monitor/task_monitor.py`
     - `weft/core/monitor/policies/runtime_control.py`
     - `weft/core/monitor/policies/dead_task.py`
     - `tests/core/monitor/policies/test_runtime_control.py`
     - `tests/core/monitor/policies/test_dead_task.py`
     - `tests/tasks/test_task_monitor.py`
   - Implementation:
     - Keep runtime cleanup on the dedicated worker lane.
     - Do not reintroduce nested cleanup executor threads.
     - Terminal cleanup base comes from no ready Monitor rows.
     - Reserved cleanup base comes from a complete snapshot with no ready
       reserved queues.
     - Dead-TID cleanup base comes from a complete snapshot with no actionable
       dead TIDs.
     - Dead-task log coalesce base is no fed TIDs, or no true raw rows for fed
       TIDs.
     - Active, too-young, not-ready, and retention-deferred entries increment
       `deferred`, not `pending`.
     - Add `next_slice_kind` only if the current discrete slice code needs it
       to avoid rechecking already-based policy classes too often. Do not
       build a generic scheduler.
   - Tests:
     - terminal cleanup ready batch larger than limit reports waypoint
     - no ready terminal rows reports base
     - reserved active/not-ready entries report deferred and base-for-now
     - dead-TID outbox-only retention-deferred entries do not report pending
     - dead-task log coalescing does not run when no dead queue was deleted
   - Done when:
     - runtime cleanup cached PONG can explain "pending because cap" versus
       "base with deferred retention" versus "blocked by delete error"
   - Stop if:
     - the code wants to mix terminal, reserved, and dead-TID deletes inside
       one worker result. Specs currently forbid that.

8. Make catch-up scheduling depend on formal progress.
   - Outcome: `_finish_monitor_cycle()` and runtime cleanup result handling
     schedule catch-up from `PolicyProgress`, not from ad hoc stop-reason
     checks.
   - Files to touch:
     - `weft/core/monitor/task_monitor.py`
     - `tests/tasks/test_task_monitor.py`
   - Implementation:
     - Keep old scheduling until tasks 1-7 have tests.
     - Then change catch-up calculation to:
       - catch-up if any policy has `waypoint_reached=True`
       - blocked-only progress keeps the existing error/backoff behavior; it
         does not imply catch-up by default
       - no catch-up for base-only or deferred-only progress
     - Keep service activity and PONG responsive while workers are in flight.
     - Preserve existing heartbeat wake behavior.
   - Tests:
     - one test where full FIFO base schedules normal interval
     - one test where batch waypoint schedules catch-up interval
     - one test where deferred-only dead TIDs schedule normal interval
     - one test where blocked external summary surfaces error and catch-up
       behavior is explicit
   - Done when:
     - no code path reads old `stop_reason` strings as the primary catch-up
       decision
   - Stop if:
     - this change makes the monitor spin on deferred-only future work

9. Update specs and implementation notes.
   - Outcome: specs define the progress contract and doc links point both
     ways.
   - Files to touch:
     - `docs/specifications/01-Core_Components.md`
     - `docs/specifications/04-SimpleBroker_Integration.md`
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/07-System_Invariants.md`
     - `docs/plans/README.md`
   - Implementation:
     - Add this plan to related-plan lists.
     - Update [MF-5] to say every cleanup policy reports cached base,
       waypoint, deferred, and blocked progress.
     - Update [OBS.13] to say PONG exposes cached progress and that deferred
       future work must not keep catch-up pending.
     - Do not change public CLI docs unless implementation changes user-facing
       output shape outside PONG.
   - Tests:
     - `uv run pytest tests/specs/test_plan_metadata.py -q`
   - Done when:
     - plan metadata and spec backlinks are consistent

10. Run review and verification gates.
    - Outcome: the implementation has proof for correctness, convergence, and
      boundedness.
    - Required commands:
      - `uv run pytest tests/core/monitor/policies/test_dead_task.py tests/core/monitor/policies/test_runtime_control.py -q`
      - `uv run pytest tests/core/test_task_monitor_cleanup.py tests/core/test_monitor_store.py tests/core/test_monitor_sql.py -q`
      - `uv run pytest tests/tasks/test_task_monitor.py -q`
      - `uv run pytest tests/specs/test_plan_metadata.py tests/specs/test_test_audit_policy.py -q`
      - `uv run mypy weft extensions/weft_docker extensions/weft_macos_sandbox`
      - `uv run ruff check weft tests`
      - `uv run pytest`
      - `uv run bin/pytest-pg`
    - Review:
      - Run an external plan review before implementation.
      - Run an external completed-work review before release.
    - Done when:
      - all required commands pass
      - review findings are addressed or explicitly rejected with rationale
      - PONG can explain every policy's last base, waypoint, deferred, or
        blocked result from cached state

## 8. Testing Plan

Use red-green TDD. Add failing tests before implementation whenever the
behavior is local enough to express first. This work is explicitly not a
mock-heavy slice.

Use these fixtures:

- Use real broker-backed queues via `broker_env` for queue selection, exact
  deletion, and SimpleBroker stats.
- Use real `MonitorStore` with temporary broker context for Monitor-table
  queries and parent/child ref state.
- Use real `TaskMonitor` instances for PONG and scheduler behavior.
- Use `WeftTestHarness` only for CLI/service lifecycle tests that need an
  actual supervised service. Do not use it for pure policy selection tests.

Do not mock:

- `simplebroker.Queue` or broker queue stats
- exact delete application
- MonitorStore SQL access
- task-log generator scans
- PONG control handling

Mock only:

- external log sink write failure, if needed to prove blocked progress
- time source, but prefer passing `now_ns` explicitly where code already
  supports it

Required test classes:

- Selection correctness:
  - each selected row, queue, or TID must satisfy its policy predicate
  - include near-miss entries such as too-young rows, active TIDs,
    retention-deferred outbox queues, open started families, malformed rows,
    and protected monitor TID rows
- Base proof:
  - each policy has a test proving its base condition
  - base tests must assert `base_reached=True`,
    `waypoint_reached=False`, and `blocked_reason is None`
- Waypoint proof:
  - each bounded policy has a `batch_size + 1`, scan-limit, or deadline test
  - waypoint tests must assert catch-up scheduling uses catch-up interval after
    task 8
- Deferred proof:
  - retention-gated or active-owner entries report deferred, not pending
  - deferred-only cycles schedule normal interval after task 8
- Blocked proof:
  - delete/store/external-log failures report `blocked_reason`
  - blocked cycles do not claim base
- Convergence proof:
  - repeated cycles reduce a named backlog measure until base
  - the first base cycle does not repeat expensive work
- Boundedness proof:
  - per-cycle selected/applied counts obey batch limits
  - scan counts obey scan limits
  - runtime worker respects family/deadline limits

Minimum local verification before implementation is considered complete:

```bash
uv run pytest tests/core/monitor/policies/test_dead_task.py tests/core/monitor/policies/test_runtime_control.py -q
uv run pytest tests/core/test_task_monitor_cleanup.py tests/core/test_monitor_store.py tests/core/test_monitor_sql.py -q
uv run pytest tests/tasks/test_task_monitor.py -q
uv run pytest tests/specs/test_plan_metadata.py tests/specs/test_test_audit_policy.py -q
uv run mypy weft extensions/weft_docker extensions/weft_macos_sandbox
uv run ruff check weft tests
uv run pytest
uv run bin/pytest-pg
```

Post-deploy observation on ops:

- Monitor PONG should show `policy_progress` with every policy reporting base,
  waypoint, deferred, or blocked.
- Deferred-only dead-task or reserved cleanup should not keep
  `catchup_pending=true`.
- When backlog exists, waypoints should produce short catch-up intervals.
- Once backlog is gone, cycles should settle to the normal monitor interval.
- Manager, heartbeat, and TaskMonitor PING latency should remain stable.

## 9. Out Of Scope

- No new queue names.
- No public CLI command changes.
- No SimpleBroker dependency changes.
- No new Monitor tables.
- No persistence of policy progress.
- No manager service-convergence changes.
- No broad cleanup rewrite beyond progress contract and scheduling.
- No removal of existing `cleanup_policy_stats` or `cleanup_queue_stats` in
  this slice unless a later reviewed plan explicitly removes them.

## 10. Rollout And Rollback

Rollout:

1. Land the progress model and cached PONG fields without changing scheduling.
2. Land policy progress records with tests.
3. Switch catch-up scheduling to progress only after every policy has a
   tested base and waypoint definition.
4. Deploy and watch PONG plus queue counts for at least one full cleanup
   interval and one catch-up interval.

Rollback:

- Revert the code change. The plan must not introduce persistent progress
  schema or public CLI contract changes, so older code can ignore any cached
  PONG-only fields after rollback.
- If a partial rollout fails before scheduling changes, revert only the PONG
  progress fields and policy records.
- If scheduling changes cause hot looping, revert task 8 first while keeping
  diagnostic progress if it is otherwise correct.

## 11. Fresh-Eyes Self-Review

Findings from the self-review pass:

- **Issue: the first draft risked treating `selected == 0` as base too often.**
  This matters because many policies use bounded windows. The plan now states
  repeatedly that zero selection is base only after domain exhaustion,
  monotonic cutoff, or a cheap empty query.
- **Issue: Monitor-store follow-on policies needed explicit cheap probes.**
  Without probes, a full batch can silently leave work while the scheduler
  returns to the normal interval. Task 6 now requires `limit=1` or over-fetch
  checks for summary, raw refs, orphan recovery, tombstones, and retirement.
- **Issue: deferred future work could be mistaken for pending work.** This was
  the recent ops failure mode for dead-task cleanup. The definitions for
  reserved and dead-TID cleanup now state that active, not-ready, too-young,
  and retention-deferred work is base for now, not pending.
- **Issue: adding a progress abstraction could drift into a policy framework.**
  The plan now forbids registries, callbacks, broad state-machine libraries,
  and persistence. The allowed abstraction is a small dataclass and helpers.
- **Issue: scheduling changes are risky if made before instrumentation is
  proven.** The tasks now require wiring cached progress first, then switching
  catch-up scheduling only after every policy reports tested progress.
- **Issue: blocked errors could have become hot retries.** The progress
  contract now says blocked progress is not base and is not automatic catch-up.
  A policy must explicitly report a waypoint when short-interval retry is
  justified.

Residual risk:

- This plan is implementation-detailed but still changes destructive cleanup
  scheduling. Per repo guidance, external review is required before
  implementation. Treat this plan as draft until that review is completed or
  the user explicitly waives it.

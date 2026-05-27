# Monitor Five Cleanup Policy Consolidation Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.3], docs/specifications/05-Message_Flow_and_State.md [MF-5], docs/specifications/07-System_Invariants.md [OBS.13], [OBS.16], [OBS.17], [IMPL.8], [IMPL.9]
Superseded by: none

## Goal

Replace the current collection of monitor cleanup policy names with five
top-level cleanup policies that all use one internal policy API and the
`ServiceTask` service-worker API.

The target top-level policies are:

| Policy | Domain | Owns |
| --- | --- | --- |
| `task_log.retention` | `weft.log.tasks` | Retained raw task log ingestion, malformed raw row deletion, external-log emission gates, and exact raw row deletion after safe retention work. |
| `monitor_store.lifecycle` | monitor store tables | Collated summary disposition, raw-ref deletion, deleted-child repair, child tombstone pruning, family retirement, and orphan raw recovery. |
| `task_local.terminal_runtime` | task runtime queues | Cleanup for terminal or disposed task-local queues that have monitor-store proof, including terminal control queues and reserved queues. |
| `task_local.dead_tid` | task runtime queues | Proven-dead TID cleanup when normal monitor-store proof is absent or incomplete. |
| `runtime_state.retention` | `weft.state.*` queues | Runtime state retention, currently TID mapping malformed-row deletion and age-based deletion. |

This plan intentionally keeps internal helper phases where they carry real
domain meaning. The change is that helper phases stop being registered or
reported as independent policies. They become private implementation details
reported through `reason_counts` and policy-specific `details`.

## Non-Goals

- Do not collapse all cleanup into one pass. A single mega-policy would hide
  distinct ownership boundaries and make no-spin behavior harder to prove.
- Do not add a general plugin framework, dependency injection container, or
  scheduler abstraction beyond what the monitor needs.
- Do not change SimpleBroker queue semantics or make queue creation explicit.
- Do not change task lifecycle state names.
- Do not remove current safety gates merely because a helper policy name is
  removed. If an old policy encoded a gate, that gate must move into one of
  the five policies.

## Required Reading

Read these files before editing code:

- `AGENTS.md`, especially the design philosophy, project conventions, and
  testing guidance.
- `docs/specifications/00-Overview_and_Architecture.md` for the queue-first
  model.
- `docs/specifications/01-Core_Components.md` [CC-2.3] for the task monitor.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5] for task logs,
  monitor store behavior, and cleanup flow.
- `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.16],
  [OBS.17], [IMPL.8], and [IMPL.9] for observability, bounded work, and
  implementation invariants.
- `docs/plans/2026-05-26-service-task-worker-api-plan.md` for the service
  worker API design intent.
- `weft/core/tasks/service.py` for the implemented `ServiceTask` worker API.
- `weft/core/monitor/task_monitor.py` for current scheduling, PONG stats, and
  cleanup execution.
- `weft/core/monitor/progress.py` for `PolicyProgress` semantics.
- `weft/core/monitor/policies/*.py` for the current policy implementation.

Use the in-repo virtualenv for commands:

```bash
. ./.envrc
./.venv/bin/python -m pytest ...
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft tests
```

If `.envrc` is not available in the shell, use the equivalent repo-managed
toolchain. Do not assume global `pytest`, `mypy`, or `ruff`.

## Code Style Rules for This Change

Follow the local style from `AGENTS.md`.

- Add `from __future__ import annotations` to any new Python file.
- Keep imports at module top level. Fix import cycles by moving ownership, not
  by adding late imports.
- Put policy-name constants in `weft/_constants.py`.
- Put monitor-policy-only data structures in `weft/core/monitor/policies/`.
- Do not import `weft/commands`, `weft/cli`, or `weft/client` from monitor
  internals.
- Use frozen, slotted dataclasses for policy work and result values.
- Keep mutable runtime state owned by `TaskMonitor`, not by policy modules.
- Do not add a global mutable policy registry. Build the registry from the
  monitor instance or from an immutable module-level mapping of classes.
- Keep comments sparse. Add comments only where they preserve a safety
  invariant that is not obvious from the code.
- Prefer a small helper over a clever abstraction. If two policies need the
  same progress conversion, share the converter. If only one policy needs a
  helper, keep it local.

## Current Problem

The monitor currently reports many policy names. Some are real ownership
boundaries. Others are cleanup phases that became separate policy names because
agents added new paths instead of repairing or extending the existing ones.

This creates several problems:

- The monitor no longer has a clear internal policy API. Some code returns
  `CleanupPolicyRun`, some emits `PolicyProgress` directly, and some reports
  domain-specific counters straight into PONG fields.
- Policy identity is too fine grained. Operators see implementation phases
  such as raw-ref repair or child tombstone pruning as separate policies.
- Tests encourage preserving accidental structure because they assert old
  policy names.
- It is too easy to add another one-off policy without proving bounded work,
  no-spin behavior, or PONG shape.

The new design should make adding a sixth policy feel like an explicit design
decision, not the path of least resistance.

## Target Policy API

Create one small internal API for monitor cleanup policies. This API should
live in a monitor policy module, not in the public client API.

Suggested file:

- `weft/core/monitor/policies/api.py`

The API should be deliberately small:

```python
from __future__ import annotations

from collections.abc import Mapping, Protocol
from dataclasses import dataclass, field
from typing import Any, Literal

CleanupPolicyName = Literal[
    "task_log.retention",
    "monitor_store.lifecycle",
    "task_local.terminal_runtime",
    "task_local.dead_tid",
    "runtime_state.retention",
]


@dataclass(frozen=True, slots=True)
class CleanupPolicyWork:
    """One bounded unit of policy work."""

    policy: CleanupPolicyName
    now_ns: int
    request_id: str
    phase: str | None = None
    cursor: str | None = None


@dataclass(frozen=True, slots=True)
class CleanupPolicyResult:
    """Common result shape for every cleanup policy."""

    policy: CleanupPolicyName
    domain: str
    scanned: int = 0
    selected: int = 0
    applied: int = 0
    deferred: int = 0
    source_total: int | None = None
    reason_counts: Mapping[str, int] = field(default_factory=dict)
    base_reached: bool = False
    waypoint_reached: bool = False
    blocked_reason: str | None = None
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CleanupPolicyConfig:
    """Immutable policy settings copied from the monitor for one worker run."""

    batch_size: int
    task_log_scan_limit: int
    task_log_retention_period_seconds: float
    store_write_batch_size: int
    table_delete_enabled: bool
    control_queue_delete_limit: int
    runtime_family_limit: int
    runtime_cleanup_slice_seconds: float


@dataclass(frozen=True, slots=True)
class CleanupPolicyContext:
    """Shared dependencies for one monitor cleanup worker run."""

    broker_target: BrokerTarget
    monitor_store: MonitorStore | None
    config: CleanupPolicyConfig
    service_worker: ServiceWorkerContext | None = None


class CleanupPolicy(Protocol):
    name: CleanupPolicyName

    def run(self, work: CleanupPolicyWork, context: CleanupPolicyContext) -> CleanupPolicyResult:
        """Run one bounded policy unit."""
```

The code sample names types such as `BrokerTarget`, `MonitorStore`, and
`ServiceWorkerContext` to show ownership. Import the actual local types in the
implementation. If a listed config field has a different local name, use the
local name. If one policy truly does not need a field, that is fine; do not add
fields that no implementation reads.

`CleanupPolicyContext` should contain the shared monitor dependencies that
policy code already uses. Keep it concrete and boring:

- queue factory or broker target access already used by policy code
- monitor store handle
- monitor configuration values such as batch size, scan limit, retention
  windows, runtime cleanup limits, and delete flags
- service-worker context if cancellation or event emission is needed
- logging helper if current code has one

Do not pass the full `TaskMonitor` into a policy. That makes policy code able
to mutate scheduler state behind the reactor's back. Pass only the dependencies
and immutable config needed for one bounded run.

### API Rules

Every top-level policy must follow these rules:

1. A policy worker receives one `CleanupPolicyWork` and returns one
   `CleanupPolicyResult`.
2. A result must be convertible to exactly one `PolicyProgress`.
3. Policy code must not hand-build `PolicyProgress`. Use one shared adapter,
   for example `policy_result_to_progress(result)`.
4. Each result must explicitly state its progress state:
   `base_reached`, `waypoint_reached`, or `blocked_reason`.
5. `base_reached=True` means no eligible work remains for this policy right
   now. The monitor can wait until the normal interval.
6. `waypoint_reached=True` means the policy hit a bounded scan, batch, family,
   or deadline limit and may have more work. The monitor should use catchup
   scheduling.
7. `blocked_reason` means the policy could not safely continue because an
   external dependency, store write, delete operation, or configuration gate
   blocked progress. The monitor must not hot-loop on this.
8. `selected > 0` is not enough to schedule catchup. Use
   `waypoint_reached` or the existing `progress_requires_catchup()` helper.
9. All counters must be non-negative.
10. Old policy names must not appear in `policy_progress`, PONG cached fields,
    or new tests after migration.

The existing `PolicyProgress` validation is a useful guard. Do not bypass it.
If its fields are insufficient, extend it with care and update the spec. Do
not create a parallel progress structure.

## Service Worker Shape

Use the `ServiceTask` worker API from `weft/core/tasks/service.py`.

Recommended shape:

- Register one cleanup policy worker callable for the task monitor service
  task.
- The callable takes `CleanupPolicyWork` as its argument.
- The callable dispatches to a registry of the five `CleanupPolicy`
  implementations by `work.policy`.
- The callable returns `CleanupPolicyResult`.
- The monitor reactor receives the result, converts it to `PolicyProgress`,
  updates cached PONG stats, and schedules the next bounded work item.

Keep worker concurrency conservative at first:

- Prefer one cleanup policy worker thread per monitor instance.
- If the implementation keeps separate built-in cleanup and runtime cleanup
  service-worker groups during migration, both groups must still use the same
  `CleanupPolicyWork` and `CleanupPolicyResult` shape.
- If separate lanes remain, use `worker_count=1` per lane and document which
  policy domains each lane is allowed to delete from.
- Do not run two cleanup policy work items that can delete from the same domain
  concurrently.

This gives the monitor one internal policy API while preserving the current
reactor property: PING/STATUS handling remains responsive while cleanup work
runs in service-worker threads.

## Bounded Work and No-Spin Invariants

These are release gates, not suggestions.

### Bounded Work

Every policy run must have a hard upper bound. The bound can be expressed as a
scan limit, batch limit, family limit, deadline, or a combination.

Required per-policy bounds:

| Policy | Required bound |
| --- | --- |
| `task_log.retention` | `task_log_scan_limit`, batch delete/write chunks, FIFO high-water stop, and retention age gate. |
| `monitor_store.lifecycle` | store scan limits and per-phase row or family limits. If a phase can sweep large tables, use the existing monitor batch size or store write batch size as the first bound, then add a named constant only if those existing bounds are wrong for that phase. |
| `task_local.terminal_runtime` | runtime family limit, delete deadline, and per-family queue deletion bounds from current code. |
| `task_local.dead_tid` | dead-TID scan limit, retention age gate, live-owner skip checks, monitor-record skip checks, and delete deadline. |
| `runtime_state.retention` | state queue scan limit and first-too-young stop for ordered TID mappings. |

If an old helper policy has a bound, move that bound with the logic. Do not
replace it with an unbounded loop.

### Done vs More Work

Every policy run must answer this question: "Should the monitor sleep normally
or run another catchup cycle?"

Use this mapping:

- `base_reached=True`: no catchup for this policy.
- `waypoint_reached=True`: catchup is allowed because bounded work ended before
  the policy reached its base.
- `blocked_reason is not None`: do not schedule an immediate hot loop solely
  for this policy. Use the monitor's existing retry or catchup delay.

Do not infer done from `applied == 0`. A run can apply zero rows because the
first row is too young, because a dependency is blocked, or because all rows
were malformed and already deleted. Those cases have different scheduling
meaning.

### No Spinning

A policy must not repeatedly schedule immediate work when it cannot make
progress.

Required no-spin checks:

- If the oldest eligible task-log row is too young, return `base_reached=True`
  with the current too-young reason in `details` or `reason_counts`.
- If an external task log sink blocks deletion, return `blocked_reason` and do
  not delete raw rows.
- If monitor store writes fail, return `blocked_reason` and preserve raw rows.
- If runtime task-local cleanup finds only live, recent, or monitor-record
  protected TIDs, return `base_reached=True` unless the scan bound was hit.
- If a delete deadline or family limit is hit, return `waypoint_reached=True`
  only if more eligible work may remain.
- If a policy worker crashes, the monitor must record an error and clear the
  in-flight marker. It must not leave a permanent "in flight" state.

## Five Policy Responsibilities

### 1. `task_log.retention`

Files to touch:

- `weft/core/monitor/policies/task_log.py`
- `weft/core/monitor/task_monitor.py`
- `weft/_constants.py`
- tests under `tests/core/monitor/` and `tests/tasks/`

This policy owns retained `weft.log.tasks` rows.

It should:

- Scan raw task-log rows in FIFO order up to `task_log_scan_limit`.
- Delete malformed raw rows that can never be collated.
- Collate valid rows into the monitor store.
- Respect the raw task-log retention window.
- Respect the external task-log sink gate. If external raw emission is enabled
  and not healthy or not caught up, raw deletion must be blocked.
- Delete exact raw rows only after they are safely represented in the monitor
  store and any required external emission gate is satisfied.
- Write the checkpoint only after successful store writes and safe raw deletion
  decisions.

It replaces these old top-level policy names:

- `task_log.retained_fifo_ingest`
- `task_log.delete_malformed`
- `task_log.delete_claimed`
- `task_log.collate_complete_lifecycle`
- `task_log.collate_terminal_without_start`
- `task_log.delete_old_without_start`
- `task_log.external_raw`

The old names may remain as private helper function names during the first
slice only if needed to keep the diff reviewable. Before completion they must
not be constants, registry entries, or reported policy names.

Expected result examples:

- Valid raw rows ingested and deleted:
  - `policy="task_log.retention"`
  - `domain="weft.log.tasks"`
  - `scanned=N`, `selected=N`, `applied=N`
  - `reason_counts={"valid_ingested": N, "raw_deleted": N}`
  - `base_reached=True` if FIFO high-water is complete
- Scan limit hit:
  - `waypoint_reached=True`
  - `base_reached=False`
- External sink blocked:
  - `blocked_reason="external_task_log_not_caught_up"` or another stable
    constant
  - `raw_deleted=0`

### 2. `monitor_store.lifecycle`

Files to touch:

- `weft/core/monitor/policies/task_log.py` if monitor-store logic currently
  lives there
- `weft/core/monitor/policies/store.py` if a new module is clearer
- `weft/core/monitor/store.py` only if existing store helpers need a small
  bounded API
- `weft/core/monitor/task_monitor.py`
- tests under `tests/core/monitor/`

This policy owns cleanup and lifecycle changes inside the monitor store after
raw task-log ingestion.

It should:

- Mark summaries for terminal task families.
- Emit or update collated summaries.
- Mark families disposed when control-delete evidence proves it is safe.
- Delete monitor-store raw references only when no longer needed.
- Repair deleted-child references.
- Prune child tombstones.
- Retire families that meet the existing retirement rules.
- Recover orphan raw task-log data when monitor-store state and raw logs are
  inconsistent.

It replaces these old top-level policy names:

- `monitor_store.summary_disposition`
- `monitor_store.raw_ref_delete`
- `monitor_store.raw_deleted_child_ref_repair`
- `monitor_store.child_tombstone_prune`
- `monitor_store.family_retirement`
- `monitor_store.orphan_raw_recovery`

Do not split these back into separate policies. If a phase needs its own
counter, put it in `reason_counts`, for example:

```python
{
    "summaries_marked": 3,
    "families_disposed": 1,
    "message_tombstones_pruned": 0,
    "orphan_families_checked": 5,
}
```

If one of these phases currently has a unique bound or scheduling rule, carry
that rule into the policy result as either `waypoint_reached` or
`blocked_reason`. Do not silently drop it.

### 3. `task_local.terminal_runtime`

Files to touch:

- `weft/core/monitor/policies/runtime_control.py`
- `weft/core/monitor/policies/reserved.py`
- `weft/core/monitor/task_monitor.py`
- tests under `tests/core/monitor/` and `tests/tasks/`

This policy owns task-local runtime queues for terminal or disposed task
families when normal monitor-store proof exists.

It should:

- Process terminal control cleanup for task families that are safe to dispose.
- Process reserved-queue cleanup for terminal task families.
- Preserve active task queues.
- Preserve reserved queues that are within the retention window or not proven
  terminal.
- Delete only queues and rows that current code already considers safe.
- Retain current deadline and family-limit behavior.

It replaces these old top-level policy names:

- `runtime.terminal_control_cleanup`
- `runtime.reserved_cleanup`

This policy may still use an internal `phase` value on `CleanupPolicyWork`,
such as `terminal_control` or `reserved`. That is acceptable because it keeps
each work item bounded and easy to test. The public policy name remains
`task_local.terminal_runtime`.

Important: do not mix terminal-control deletion and reserved cleanup in one
unbounded loop. If the current code processes them as separate bounded slices,
preserve that shape and report a single policy name with phase details.

### 4. `task_local.dead_tid`

Files to touch:

- `weft/core/monitor/policies/dead_task.py`
- `weft/core/monitor/policies/runtime_control.py` if dead-TID cleanup currently
  calls helpers there
- `weft/core/monitor/task_monitor.py`
- tests under `tests/core/monitor/` and `tests/tasks/`

This policy owns cleanup for proven-dead TIDs when normal terminal-family
cleanup cannot prove ownership through the monitor store.

It should:

- Scan bounded dead-TID candidates.
- Skip live TIDs.
- Skip too-young TIDs.
- Skip TIDs protected by monitor records.
- Respect retention gates before deleting inbox, outbox, control, or reserved
  queues.
- Coalesce dead task-log data when the current implementation does so.
- Delete only task-local queues proven safe by the existing dead-TID rules.

It replaces these old top-level policy names:

- `runtime.dead_tid_cleanup`
- `runtime.dead_task_log_coalesce`

This policy is a fallback and recovery policy. It must not become the primary
path for normal completed tasks. Normal completed tasks should flow through
`task_log.retention`, `monitor_store.lifecycle`, and
`task_local.terminal_runtime`.

### 5. `runtime_state.retention`

Files to touch:

- `weft/core/monitor/policies/tid_mapping.py`
- `weft/core/monitor/task_monitor.py`
- `weft/_constants.py`
- tests under `tests/core/monitor/`

This policy owns runtime state queues. Today that means
`weft.state.tid_mappings`.

It should:

- Delete malformed TID mapping rows.
- Delete age-eligible TID mapping rows.
- Preserve mappings that are too young.
- Preserve mappings for live TIDs if current code does that check.
- Stop on the first too-young ordered row, as current behavior does.

It replaces these old top-level policy names:

- `tid_mapping.delete_malformed`
- `tid_mapping.delete_older_than`

Return one `CleanupPolicyResult` for the state queue, not two. Use
`reason_counts` to separate malformed and age-based deletion counts.

## Implementation Plan

Work in small slices. Each slice should have at least one failing test before
the implementation change unless the slice is pure deletion of now-dead tests.

### Slice 1: Lock Down the Target Policy Names

Files:

- `weft/_constants.py`
- `weft/core/monitor/policies/api.py`
- `tests/core/monitor/test_cleanup_policy_api.py` or similar

Tasks:

1. Add five policy name constants in `_constants.py`.
2. Add `CleanupPolicyName`, `CleanupPolicyWork`, `CleanupPolicyResult`, and
   `CleanupPolicy` in `weft/core/monitor/policies/api.py`.
3. Add `policy_result_to_progress(result)` in the same module or in
   `weft/core/monitor/progress.py` if that is a better ownership fit.
4. Add a single registry helper:

   ```python
   def cleanup_policy_registry(context: CleanupPolicyContext) -> Mapping[CleanupPolicyName, CleanupPolicy]:
       ...
   ```

   If context construction is awkward, create the registry in
   `TaskMonitor`. Do not add a global mutable registry.
5. Add tests that assert:
   - the allowed policy-name set is exactly the five target names
   - `policy_result_to_progress` preserves counters
   - invalid result states fail through `PolicyProgress` validation
   - old policy names are not in the allowed set

Red test first:

- Add a test that imports the target names and fails because the API does not
  exist.

Green gate:

```bash
./.venv/bin/python -m pytest tests/core/monitor/test_cleanup_policy_api.py -q
```

### Slice 2: Convert Runtime State Retention First

Start with `runtime_state.retention` because it is narrow and currently maps
from two old policy names to one new policy.

Files:

- `weft/core/monitor/policies/tid_mapping.py`
- `weft/core/monitor/task_monitor.py`
- tests that currently assert `tid_mapping.delete_malformed` or
  `tid_mapping.delete_older_than`

Tasks:

1. Write a failing test that creates malformed and age-eligible TID mapping
   rows and expects one `PolicyProgress` with
   `policy="runtime_state.retention"`.
2. Refactor `tid_mapping.py` so it returns `CleanupPolicyResult`.
3. Preserve existing scan counts, deletion counts, stop reason behavior, and
   first-too-young behavior.
4. Update `TaskMonitor` aggregation so `last_cleanup_policy_stats` and
   `last_policy_progress` use the new policy name.
5. Remove or demote old TID mapping constants.

Tests should use real queues and the existing test harness. Do not mock
SimpleBroker queues.

Green gate:

```bash
./.venv/bin/python -m pytest tests/core/monitor -q -k "tid_mapping or runtime_state or cleanup_policy_api"
```

### Slice 3: Convert Task Log Retention

Files:

- `weft/core/monitor/policies/task_log.py`
- `weft/core/monitor/task_monitor.py`
- `weft/core/monitor/progress.py` only if adapter changes are needed
- tests for retained task-log ingest and external raw log behavior

Tasks:

1. Add tests for the new top-level policy name:
   - valid raw rows are ingested, represented in monitor store, and deleted
     only when safe
   - malformed raw rows are deleted and counted under
     `task_log.retention`
   - retention age gate produces `base_reached=True`, not a catchup spin
   - scan limit produces `waypoint_reached=True`
   - external sink blocked produces `blocked_reason` and raw rows remain
2. Refactor retained FIFO ingest code to return `CleanupPolicyResult`.
3. Move old phase counts into `reason_counts`.
4. Keep detailed PONG compatibility for existing fields such as
   `retained_task_log_ingest` if operators rely on them, but source those
   fields from the new policy result.
5. Remove old task-log policy constants and old direct progress emissions.

Important invariants:

- Store writes must precede raw deletion.
- Failed store writes must block raw deletion.
- External emission gates must block raw deletion when configured.
- Checkpoint writes must reflect only safely processed rows.

Green gate:

```bash
./.venv/bin/python -m pytest tests/core/monitor tests/tasks/test_task_monitor.py -q -k "task_log or retained or external"
```

### Slice 4: Convert Monitor Store Lifecycle

Files:

- `weft/core/monitor/policies/task_log.py` or a new
  `weft/core/monitor/policies/store.py`
- `weft/core/monitor/store.py` if bounded store helpers are missing
- `weft/core/monitor/task_monitor.py`
- monitor-store tests

Tasks:

1. Add a failing integration test that exercises at least two former store
   policies and expects only `monitor_store.lifecycle` in progress output.
2. Move summary disposition, raw-ref delete, deleted-child repair, child
   tombstone prune, family retirement, and orphan raw recovery under one
   policy implementation.
3. Keep subphase counts in `reason_counts`.
4. Ensure every subphase has a bound. If a subphase currently lacks a bound,
   add a small explicit bound and set `waypoint_reached=True` when it is hit.
5. Ensure errors from any subphase become `blocked_reason` or `errors`, not a
   silent base.
6. Remove old monitor-store policy constants and direct progress emissions.

Do not create one test per private helper phase that only asserts a helper was
called. Test observable behavior: store rows changed, raw rows preserved or
deleted, summaries emitted, families retired, and progress fields reported.

Green gate:

```bash
./.venv/bin/python -m pytest tests/core/monitor -q -k "monitor_store or orphan or tombstone or family"
```

### Slice 5: Convert Terminal Runtime Cleanup

Files:

- `weft/core/monitor/policies/runtime_control.py`
- `weft/core/monitor/policies/reserved.py`
- `weft/core/monitor/task_monitor.py`
- runtime cleanup tests

Tasks:

1. Add a failing test that terminal-control cleanup and reserved cleanup both
   report as `task_local.terminal_runtime`.
2. Preserve the current bounded slices. If the current code runs terminal and
   reserved cleanup separately, keep separate `CleanupPolicyWork.phase` values
   and one common top-level policy name.
3. Move old phase-specific counters into `reason_counts`, for example:
   - `terminal_families_processed`
   - `terminal_families_disposed`
   - `terminal_queues_deleted`
   - `reserved_families_checked`
   - `reserved_queues_deleted`
4. Preserve active-queue and retention checks.
5. Preserve family limit and deadline behavior.
6. Remove old top-level policy constants and progress entries.

Required tests:

- terminal family with proof is disposed or retired as current behavior
  requires
- active family is skipped
- reserved rows inside retention are skipped
- family limit produces `waypoint_reached=True`
- delete deadline produces `waypoint_reached=True` or the current equivalent
  catchup signal

Green gate:

```bash
./.venv/bin/python -m pytest tests/core/monitor tests/tasks/test_task_monitor.py -q -k "runtime or reserved or terminal"
```

### Slice 6: Convert Dead-TID Cleanup

Files:

- `weft/core/monitor/policies/dead_task.py`
- `weft/core/monitor/policies/runtime_control.py`
- `weft/core/monitor/task_monitor.py`
- dead-TID cleanup tests

Tasks:

1. Add a failing test that dead-TID cleanup reports
   `task_local.dead_tid`.
2. Preserve dead-TID skip reasons:
   - live owner
   - too young
   - monitor records present
   - retention protected
3. Preserve queue deletion counts by queue kind in `reason_counts`.
4. Preserve dead task-log coalescing behavior, but report it as a phase or
   reason count under `task_local.dead_tid`.
5. Ensure a scan that finds only protected candidates reaches base unless the
   scan bound was hit.
6. Remove old dead-TID and dead-task-log policy constants and progress entries.

Required tests:

- proven-dead queues are deleted
- live queues are preserved
- too-young queues are preserved
- monitor-record protected queues are preserved
- scan limit produces `waypoint_reached=True`
- no eligible work produces `base_reached=True`

Green gate:

```bash
./.venv/bin/python -m pytest tests/core/monitor tests/tasks/test_task_monitor.py -q -k "dead_tid or dead_task"
```

### Slice 7: Wire the Service Worker Scheduler

Files:

- `weft/core/monitor/task_monitor.py`
- `weft/core/tasks/service.py` only if a missing generic service-worker hook
  is found
- `tests/tasks/test_task_monitor.py`
- `tests/tasks/test_service_task.py` if service-worker behavior changes

Tasks:

1. Register the cleanup policy worker callable on the monitor service task.
2. Build `CleanupPolicyWork` items in the monitor reactor.
3. Start at most one cleanup policy work item per cleanup lane at a time.
4. On result, convert to `PolicyProgress`, update cached stats, clear
   in-flight state, and schedule the next policy work item if needed.
5. Use the existing monitor catchup logic driven by `PolicyProgress`; do not
   add a second catchup scheduler.
6. Ensure PING/STATUS use cached state and do not scan queues.
7. Ensure worker exceptions clear in-flight state and surface in
   `last_errors`.

Implementation note:

- It is acceptable during migration to keep separate built-in cleanup and
  runtime cleanup service-worker groups if that preserves current behavior.
  Both groups must use the same `CleanupPolicyWork` and
  `CleanupPolicyResult` shape.
- Do not use a thread pool directly from the monitor once this slice is done.
  Service workers are the internal API for threaded monitor work.

Required tests:

- monitor PING returns while a cleanup policy worker is in flight
- a worker result updates `last_policy_progress`
- a worker exception does not leave `*_in_flight=True`
- catchup scheduling follows `waypoint_reached`
- base scheduling follows `base_reached`
- blocked scheduling does not spin

Green gate:

```bash
./.venv/bin/python -m pytest tests/tasks/test_service_task.py tests/tasks/test_task_monitor.py -q
```

### Slice 8: Delete Old Policy Surface

Files:

- `weft/_constants.py`
- `weft/core/monitor/policies/*.py`
- `weft/core/monitor/task_monitor.py`
- all tests that assert old names
- relevant specs

Tasks:

1. Remove old policy-name constants.
2. Remove old registry entries.
3. Remove old PONG fields only if they are truly redundant and not documented.
   Prefer keeping useful aggregate PONG fields while sourcing them from the new
   policy results.
4. Search for old names and eliminate them from code and tests:

   ```bash
   rg "tid_mapping\.delete_|task_log\.retained|monitor_store\.|runtime\.terminal|runtime\.reserved|runtime\.dead"
   ```

   Keep old names only in this plan or in historical docs.
5. Update specs so the five-policy contract is documented.
6. Update `docs/lessons.md` only if implementation uncovers a repeated agent
   mistake not already recorded.

Green gate:

```bash
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
./.venv/bin/python -m pytest tests/core/monitor tests/tasks/test_task_monitor.py tests/tasks/test_service_task.py -q
./.venv/bin/ruff check weft tests
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
```

### Slice 9: Ops Verification

This is a runtime behavior change. Verify it against a real monitor before
calling the work complete.

On ops, from `/app/governance` with `/opt/venv/bin` on PATH:

```bash
weft task ping <monitor_tid>
```

Check:

- `extended.task_monitor.last_cycle.policy_progress[*].policy` contains only
  the five target names.
- `last_errors` is empty.
- no cleanup `*_in_flight` flag remains stuck after a cycle.
- `cleanup_jobs_pending` returns to zero when bounded work is done.
- `next_cycle_due_in_seconds` is near the normal interval when all policies
  report base.
- catchup interval is used only when a policy reports `waypoint_reached=True`.
- task-log raw deletion, monitor-store summary counts, and runtime queue
  deletion counts remain plausible relative to pre-change behavior.

If ops has no eligible work, run a local or staging task that creates known
terminal rows and verify the five policy names during cleanup.

## Test Design Guidance

Use real queues and real monitor-store state whenever possible. Do not replace
queue behavior with mocks. The point of these tests is to protect lifecycle and
cleanup invariants, and mocks are too likely to bless the implementation
instead of the behavior.

Prefer:

- `WeftTestHarness` for isolated runtime tests.
- Real `Queue` instances against a temporary broker database.
- Real monitor-store writes and reads.
- Small fixtures that create one terminal task family, one live task family,
  or one malformed raw row.
- Monkeypatching time only where age gates need deterministic behavior.
- Monkeypatching external sinks only at the boundary where the sink health or
  emit result is the thing under test.

Avoid:

- asserting that a private helper function was called
- mocking SimpleBroker queues
- asserting full PONG dictionaries
- sleeps as the only synchronization mechanism
- tests that pass because no work was created

Every policy should have tests for:

- base reached
- waypoint reached
- blocked, when the policy has a real blocker
- at least one successful deletion or mutation
- at least one preservation case where unsafe work is skipped

## Documentation Updates

Update these specs in the same PR as the code:

- `docs/specifications/01-Core_Components.md` [CC-2.3]:
  describe the monitor cleanup policy API and service-worker execution shape.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]:
  replace old cleanup policy names with the five top-level policies and their
  domains.
- `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.16],
  [OBS.17], [IMPL.8], [IMPL.9]:
  document the bounded-work/no-spin contract for policy results.

Add backlinks from the touched spec sections to this plan. The plan is not the
source of truth after implementation; the specs are.

Update `docs/plans/README.md` when this plan is added, and update it again if
the plan status changes after implementation.

## Review Checklist

Before requesting review, answer these questions in the PR description:

1. What are the five policy names, and where are they registered?
2. Which old policy names were removed?
3. For each policy, what is the hard bound on one run?
4. For each policy, what condition sets `base_reached=True`?
5. For each policy, what condition sets `waypoint_reached=True`?
6. For each policy with blockers, what sets `blocked_reason`?
7. How does the monitor avoid hot-looping when no progress is possible?
8. What tests use real queues and monitor-store state?
9. What ops command was used to verify live PONG shape?

## Risks and Counterarguments

The strongest counterargument is that five policy names may hide important
operational detail. That is real. The answer is not to preserve twenty policy
names. The answer is to keep detailed `reason_counts`, stable aggregate PONG
fields, and per-policy `details` for phases that operators need.

Another risk is that consolidating names encourages consolidating execution
loops. Do not do that. Policy identity and execution granularity are different
things. It is fine for `task_local.terminal_runtime` to run separate bounded
work items for terminal-control cleanup and reserved cleanup. It is not fine
for those phases to reappear as separate top-level policies.

The third risk is backward compatibility for scripts that parse
`policy_progress[*].policy`. This is an internal monitor surface, but ops may
still depend on it. Search the repo and known ops scripts before removing old
names. If external scripts depend on old names, update those scripts in the
same rollout. Do not keep alias policy entries in PONG because that defeats the
cleanup.

## Fresh-Eyes Review

Review pass 1 found a possible bad direction: a single cleanup worker group
for all policies could accidentally serialize unrelated monitor work and
change runtime behavior more than needed. The plan now allows separate
built-in and runtime cleanup service-worker groups during migration, but
requires both to use the same `CleanupPolicyWork` and `CleanupPolicyResult`
shape. That preserves the API goal without forcing an unnecessary scheduler
rewrite.

Review pass 2 found an ambiguity around "remove all previous policies." The
plan now distinguishes old policy names from private helper phases. The
implementation must remove old names from constants, registries, progress, and
tests. It may keep private helper functions if they are not policy identities.

Review pass 3 found a no-spin gap: the first draft treated `selected > 0` as
evidence for catchup. That is wrong. The plan now requires catchup to be
driven by `waypoint_reached` and the existing progress helper, not by selected
or applied counts alone.

Review pass 4 found a test-design gap: the original test guidance could still
lead to over-mocking. The plan now requires real queues and monitor-store state
for policy behavior tests, with mocks limited to external sink boundaries and
deterministic time.

Review pass 5 checked for scope drift. The plan still targets the requested
five policies, the common service-worker-backed internal API, and removal of
old policy identities. It does not require a materially different architecture.

Review pass 6 found two remaining ambiguities. The context object was too easy
to misread as "pass the whole TaskMonitor," so the plan now shows a concrete
context/config shape and forbids passing mutable monitor state into policies.
The worker guidance was also tightened: separate built-in and runtime lanes are
allowed only if they still share the common policy work/result shape and keep
one worker per lane.

# TaskMonitor Config And Reactor Cache Cleanup Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.3]; docs/specifications/03-Manager_Architecture.md [MA-1.6a]; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13], [OBS.13.10], [OBS.13.11], [OBS.13.12], [IMPL.8], [IMPL.9]
Superseded by: none

## Goal

Make the TaskMonitor cleanup contract match the intended product behavior:
the built-in `delete` processor deletes exact safe candidates selected by the
five cleanup policies, and `report_only` is the non-destructive processor. There
is no backwards compatibility requirement for the stale
`WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED` guard. Also remove the stale public
`WEFT_TASK_MONITOR_CLEANUP_WORKERS` knob, because runtime cleanup is now a
reactor-launched sequence of bounded single worker slices, not a configurable
mixed worker pool. Finally, repair the worker/reactor ownership drift: worker
lanes may perform the broker/store cleanup effects explicitly allowed by the
specs, but cached PONG/status diagnostic fields must be committed only by the
TaskMonitor reactor after typed worker results return.

This plan was externally reviewed and approved on 2026-05-29 before
implementation. It changes cleanup behavior and removes public env knobs with
no backwards compatibility path.

## Non-Goals

- Do not preserve either removed env var as a silent alias, fallback, warning,
  or compatibility reader.
- Do not add a new cleanup policy identity. The five top-level policy names in
  [OBS.13.12] stay exact.
- Do not reintroduce a configurable multi-worker runtime cleanup executor.
  Runtime cleanup stays as discrete reactor-launched worker slices.
- Do not change SimpleBroker queue names, task state names, TID format,
  TaskSpec schema, task result payloads, or manager service ownership rules.
- Do not turn the Monitor store into lifecycle truth, result authority, control
  authority, or a public status dependency.
- Do not reorganize the large TaskMonitor module for taste. Refactor only the
  fields and worker result boundaries needed for this change.

## Source Documents

Read these before implementation:

- `AGENTS.md`, especially the design philosophy, constants guidance, testing
  guidance, and agent boundaries.
- `docs/agent-context/decision-hierarchy.md`: specs are behavior truth; plans
  guide execution only.
- `docs/agent-context/principles.md` and
  `docs/agent-context/engineering-principles.md`: keep changes minimal,
  queue-first, strict at boundaries, and traceable.
- `docs/agent-context/runbooks/writing-plans.md`,
  `docs/agent-context/runbooks/hardening-plans.md`, and
  `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`: this is a
  risky, boundary-crossing plan and must satisfy those review rules.
- `docs/lessons.md` and `docs/agent-context/lessons.md`.

Governing specs:

- `docs/specifications/01-Core_Components.md` [CC-2.3]: `TaskMonitor` is a
  persistent reactor. It owns task-local control, scheduling, heartbeat
  registration, and cached diagnostic commits. Built-in cleanup processors and
  runtime cleanup workers may open fresh broker/store handles only in their
  allowed maintenance lanes.
- `docs/specifications/03-Manager_Architecture.md` [MA-1.6a]: manager-owned
  internal service supervision owns how the manager starts, observes, and
  restarts the internal TaskMonitor service.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]: task logs,
  Monitor-owned collation tables, cleanup reports, and PONG diagnostics are
  operational evidence only. The built-in `delete` processor may delete exact
  safe candidates; `report_only` must not delete, reserve, move, prune, reap,
  acknowledge, or unclaim rows.
- `docs/specifications/07-System_Invariants.md` [OBS.13]: Monitor artifacts are
  operational evidence only.
- `docs/specifications/07-System_Invariants.md` [OBS.13.10]: built-in task-log
  cleanup and runtime cleanup are the only TaskMonitor worker lanes allowed to
  own broker/store cleanup effects. Runtime cleanup must be discrete worker
  slices and must not start nested cleanup executor threads.
- `docs/specifications/07-System_Invariants.md` [OBS.13.11]: PONG cleanup
  diagnostics are cached from the last cleanup cycle and must not perform live
  scans, store reads, external-log checks, or delete/report work.
- `docs/specifications/07-System_Invariants.md` [OBS.13.12]: the five top-level
  cleanup policy identities are exact.
- `docs/specifications/07-System_Invariants.md` [IMPL.8], [IMPL.9]: cleanup
  and implementation invariants for exact, bounded, retryable Monitor cleanup.

Relevant historical plans, for context only:

- `docs/plans/2026-05-16-monitor-durable-collation-store-plan.md` introduced
  table-backed collation and the now-stale table-delete guard. Current user
  decision supersedes that guard: processor identity now owns destructive vs
  non-destructive behavior.
- `docs/plans/2026-05-23-monitor-cleanup-executor-plan.md` introduced a
  multi-worker cleanup executor idea. Current specs and user decision reject
  that shape for runtime cleanup: no nested executor threads and no public
  cleanup-worker count.
- `docs/plans/2026-05-26-monitor-five-cleanup-policy-consolidation-plan.md`
  defines the current five-policy direction and the internal policy API.
- `docs/plans/2026-05-20-monitor-reactor-worker-refactor-plan.md` explains the
  reactor/worker split this plan must preserve.

Comprehension questions before editing:

1. Which TaskMonitor fields are cached PONG/status diagnostics, and which
   methods currently write them?
2. Which worker lanes are allowed to perform broker/store cleanup effects under
   [OBS.13.10], and which effects still belong to the reactor?
3. What makes a cleanup candidate exact and safe for the built-in `delete`
   processor, and how does `report_only` prove that it stayed non-destructive?
4. Which PONG fields and system status fields currently expose
   `table_delete_enabled`, `cleanup_workers_configured`, or job-count data from
   the obsolete worker-pool model?
5. Does any production code still call
   `weft/core/monitor/cleanup_executor.py::run_cleanup_jobs`, or is it test-only
   dead code after the ServiceTask worker API migration?

If any answer is unclear, stop and reread the files named below before editing.

## Context and Key Files

Primary implementation files:

- `weft/_constants.py`: single source for env vars and config parsing. Current
  stale surfaces include `WEFT_TASK_MONITOR_CLEANUP_WORKERS_DEFAULT`,
  `_parse_task_monitor_cleanup_workers`,
  `WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED_DEFAULT`, `load_config()` entries,
  removed-env guard lists, and runtime override validation.
- `weft/core/monitor/runtime.py`: `TaskMonitorRuntimeConfig` currently carries
  `cleanup_workers` and `table_delete_enabled`; `from_config()` validates both.
- `weft/core/monitor/task_monitor.py`: owns persistent TaskMonitor scheduling,
  worker submission, PONG extension output, built-in processor execution,
  Monitor-store cleanup, runtime cleanup slices, and cached `_last_*`
  diagnostics.
- `weft/core/monitor/policies/api.py`: `CleanupPolicyConfig` still includes
  `table_delete_enabled`; remove it unless a current policy still reads it.
- `weft/core/monitor/policies/runtime_control.py`: owns runtime cleanup result
  data such as `cleanup_workers_configured` and job-count summary fields.
- `weft/core/monitor/cleanup_executor.py`: currently has no production caller
  if `run_cleanup_jobs` remains referenced only by its tests and historical
  plans. Resolve this explicitly during implementation; do not leave a dead
  worker-pool abstraction that contradicts [OBS.13.10].

Likely tests to update or add:

- `tests/system/test_constants.py`: remove defaults and config expectations for
  the two env vars; add explicit removed-env rejection tests.
- `tests/core/test_task_monitoring.py`: remove runtime-config expectations for
  `cleanup_workers`; assert removed config keys are rejected when present.
- `tests/tasks/test_task_monitor.py`: update `delete` vs `report_only`
  scenarios so they no longer set `WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED`;
  update PONG/status expectations that mention table-delete or worker-pool
  fields; add the reactor-owned cache regression.
- `tests/core/monitor/test_cleanup_executor.py`: delete with
  `weft/core/monitor/cleanup_executor.py` if no production caller remains.
- `tests/specs/quick_reference/test_queue_names.py` or nearby docs tests, if
  Quick Reference env-var tables are asserted.

Docs likely to update during implementation:

- `docs/specifications/00-Quick_Reference.md`: remove the table-delete env var
  row and any cleanup-worker row if present.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]: remove the
  sentence that says `WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED` is retained as a
  legacy guard, and replace it with the current processor contract.
- `docs/specifications/01-Core_Components.md` [CC-2.3] and
  `docs/specifications/07-System_Invariants.md` [OBS.13.10], [OBS.13.11] only
  if implementation notes need to name the new typed result commit helper.
- `docs/plans/README.md`: already updated by this plan draft. Do not change the
  plan status to `completed` until the implementation, spec updates, tests, and
  required reviews are done.

Use these search commands during implementation:

```bash
rg -n "TASK_MONITOR_(TABLE_DELETE_ENABLED|CLEANUP_WORKERS)|cleanup_workers|table_delete_enabled|cleanup_executor|run_cleanup_jobs" weft tests docs README.md AGENTS.md
rg -n "self\\._last_|_last_cleanup_workers|cleanup_workers_configured|_run_builtin_cycle_worker|_run_monitor_store_cycle|_handle_builtin_cycle_worker_result" weft/core/monitor/task_monitor.py
```

## Invariants and Constraints

- The destructive contract is processor-driven. Built-in `delete` means delete
  exact safe candidates selected by explicit policies. Built-in `report_only`
  means no broker/store deletion, no reservation, no move, no prune, no reap,
  no ack, and no unclaim.
- Removed env vars must fail fast if explicitly set. Do not silently ignore
  `WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED` or
  `WEFT_TASK_MONITOR_CLEANUP_WORKERS`; add clear `ValueError` messages that
  tell the operator the replacement behavior.
- No backwards compatibility for the removed env vars. Do not preserve old
  PONG/status fields merely to avoid changing tests.
- The five cleanup policies stay exact. Private phases remain reason counts or
  cached details, not policy names.
- The TaskMonitor reactor must remain responsive to task-local
  `PING`/`STATUS`/`STOP`/`KILL` while built-in cleanup or runtime cleanup
  workers run.
- Worker lanes may open fresh broker/store handles only where [OBS.13.10]
  allows it. They must not answer control messages.
- Cached PONG/status diagnostics are reactor-owned. Worker lanes return typed
  result objects; the reactor applies those results in worker-result handlers.
- PONG must stay cheap and cached. It must not scan queues, query Monitor
  tables, validate external log files, recompute candidates, or perform
  cleanup while answering.
- Runtime cleanup must remain discrete: terminal control/inbox cleanup,
  eligible reserved cleanup, and dead-TID cleanup are separate worker results.
  Do not mix them into one result or run a nested cleanup executor.
- Monitor-owned tables and reports remain operational evidence only. Status and
  result reconstruction must continue to use task-owned queues and
  `weft.log.tasks`.
- Tests should use real broker-backed paths where practical. Mock only the
  local worker delay or external nondeterminism needed to prove reactor cache
  ownership.

Hidden couplings:

- `load_config()` feeds both CLI/system config tests and
  `TaskMonitorRuntimeConfig.from_config()`. Removing env keys without updating
  override validation will create mismatched config behavior.
- TaskMonitor PONG and command-layer service status both read the same cached
  diagnostic fields. Removing or renaming fields requires updating all
  consumers and tests in the same slice.
- Built-in task-log cleanup, Monitor-store collation, raw deletion, external
  raw-mode emit, and runtime cleanup all append to policy progress. Moving
  cached writes to reactor commits must preserve progress consolidation and
  catchup scheduling.
- `cleanup_executor.py` may look harmless because it is isolated, but leaving
  it after removing the public worker count invites future agents to rewire a
  design the spec now forbids.

## Rollout and Rollback

This is a breaking internal configuration cleanup. Rollout is ordinary source
deployment, with no compatibility window:

1. Remove the stale env parsing and runtime fields first, with tests proving
   explicit env use is rejected.
2. Remove stale PONG/status fields and update tests in the same change so no
   public diagnostic contract pretends the knobs still exist.
3. Refactor worker result cache commits only after tests cover the current
   bad behavior. This keeps the risk localized if implementation needs to be
   bisected.
4. Update specs and backlinks before marking the plan complete.

Rollback is source revert of the change set. There is no persisted payload or
queue migration to roll back. The one-way door is destructive cleanup itself:
after `delete` runs, exact selected rows may be gone. That is intended product
behavior, so pre-implementation review must confirm the safe-candidate gates
are preserved and tests prove `report_only` remains non-destructive.

Post-change observation:

- `WEFT_TASK_MONITOR_PROCESSOR=delete` with collation enabled deletes retained
  exact raw task-log rows after durable Monitor ingestion or raw external emit,
  according to the selected path.
- `WEFT_TASK_MONITOR_PROCESSOR=report_only` leaves broker rows intact and
  reports candidates only.
- PONG/status no longer contains table-delete or configurable cleanup-worker
  diagnostics.
- PONG while a built-in worker is in flight shows the previous cached cleanup
  diagnostics plus in-flight state, not partially mutated worker-side cache.

## Tasks

### 1. Remove Stale Public Config Knobs

Owner: `weft/_constants.py` and `weft/core/monitor/runtime.py`.

Required action:

- Remove `WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED_DEFAULT`,
  `WEFT_TASK_MONITOR_CLEANUP_WORKERS_DEFAULT`, and their parsers/loaders from
  the active config shape.
- Add both env names to the removed/unsupported env guard path so setting
  either one raises a clear `ValueError`.
- Remove `table_delete_enabled` and `cleanup_workers` from
  `TaskMonitorRuntimeConfig`, `from_config()`, and any summary/config output.
- Remove override validation branches for those config keys. Overrides should
  reject the removed names rather than normalize them.
- Update `tests/system/test_constants.py` and
  `tests/core/test_task_monitoring.py` first or in the same patch:
  - default config no longer contains either key,
  - explicit env setting raises,
  - runtime config no longer exposes either field.

Replacement error messages should be operator-useful:

```text
WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED was removed; use WEFT_TASK_MONITOR_PROCESSOR=report_only to disable destructive cleanup.
WEFT_TASK_MONITOR_CLEANUP_WORKERS was removed; TaskMonitor runtime cleanup uses bounded reactor-launched worker slices.
```

Stop and re-evaluate if removing the keys requires preserving a silent read for
any public CLI path. That would contradict the user's no-backwards-compatibility
decision.

### 2. Make Processor Semantics The Only Destructive Switch

Owner: `weft/core/monitor/task_monitor.py`,
`weft/core/monitor/policies/api.py`, and TaskMonitor docs/tests.

Required action:

- Remove all `table_delete_enabled` checks from TaskMonitor cleanup selection,
  PONG fields, status/config summaries, and `CleanupPolicyConfig`.
- Ensure the destructive branch is only selected by the built-in processor
  contract:
  - `delete`: apply exact safe deletion after policy gates,
  - `report_only`: produce reports/progress without destructive broker/store
    effects,
  - `jsonl_then_delete`: preserve its existing fail-closed contract until its
    spec says otherwise.
- Update task-monitor tests that currently set
  `WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED=1`; those should now rely on
  `WEFT_TASK_MONITOR_PROCESSOR=delete` only.
- Convert tests that set `WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED=0` into
  `WEFT_TASK_MONITOR_PROCESSOR=report_only` tests that assert rows remain.
- Update `docs/specifications/00-Quick_Reference.md` and [MF-5] to remove the
  legacy guard language and state the processor-owned contract.

Concrete verification:

- A retained safe row selected by the built-in `delete` path is exact-deleted
  without the old table-delete env var.
- The same retained row under `report_only` remains present and is reported.
- Explicitly setting `WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED` fails before
  TaskMonitor starts.

Stop and re-evaluate if any code wants a second destructive boolean. That is
the detritus this change is removing.

### 3. Remove Worker-Pool Diagnostics And Dead Executor Surface

Owner: `weft/core/monitor/task_monitor.py`,
`weft/core/monitor/policies/runtime_control.py`, and possibly
`weft/core/monitor/cleanup_executor.py`.

Required action:

- Remove `cleanup_workers_configured` and old worker-pool job-count fields from
  PONG/status output unless a field still reports real current behavior. A
  hard-coded `1` is not useful diagnostics and should not survive as a public
  field.
- Remove those fields from runtime cleanup result dataclasses if they only
  reflect the old executor model.
- Audit `weft/core/monitor/cleanup_executor.py::run_cleanup_jobs`:
  - if `rg` confirms production code never imports or calls it, delete
    `cleanup_executor.py` and `tests/core/monitor/test_cleanup_executor.py`,
    then update any docs that still describe it as current;
  - if a production caller exists, stop and update this plan before editing,
    because that would mean current code still has the nested executor shape
    [OBS.13.10] forbids.
- Update tests in `tests/tasks/test_task_monitor.py` that assert
  `cleanup_workers_configured == 1` or inspect old job-count fields.

Replacement diagnostics should describe actual current state, for example:

- per-slice runtime cleanup counts,
- `control_cleanup_pending`,
- deadline/family-limit flags,
- policy progress and reason counts.

Do not add a replacement "worker count" field unless there is more than one
real concurrently configurable worker and a spec change approves it.

### 4. Move Cached Diagnostic Mutations Back To The Reactor

Owner: `weft/core/monitor/task_monitor.py`.

Required action:

- Add small frozen, slotted dataclasses for worker-return snapshots where the
  existing result types are not enough. Keep them local to `task_monitor.py`
  unless a policy module already owns the data shape.
- Refactor `_run_monitor_store_cycle()` so it does not mutate `_last_*` cached
  fields directly when it can run on a worker lane. It should return a typed
  Monitor-store cycle result containing:
  - store availability/error,
  - retained-log ingest summary,
  - collation counters,
  - message-row cleanup counters,
  - family disposition counters,
  - policy progress entries,
  - raw deletion/recovery summaries,
  - runtime-cleanup readiness signal.
- Refactor `_run_builtin_cycle_worker()` so it does not set
  `_last_cycle_at`, `_last_policy_progress`, collation counters, candidate
  counters, or processor result fields directly. It should return a typed
  result to `_handle_builtin_cycle_worker_result()`.
- Move cache resets and result commits into explicit reactor helpers, for
  example `_reset_cached_cleanup_diagnostics_for_cycle()`,
  `_apply_monitor_store_cycle_result(...)`, and
  `_apply_builtin_cycle_result(...)`.
- Keep `_handle_runtime_cleanup_worker_result()` as a reactor commit point, but
  remove obsolete worker-pool fields as described in Task 3.
- Audit all remaining `self._last_* =` writes. The acceptable locations after
  this task should be:
  - `__init__`,
  - task-local reactor methods,
  - worker-result handlers,
  - small reactor-only cache-commit helpers.
  Worker-callable methods should not mutate cached diagnostics directly.

Testing guidance:

- Add or extend a regression that starts a built-in cleanup worker, holds it in
  flight with a controlled delay, asks PONG/STATUS on the reactor, and proves
  PONG shows the previous cached diagnostics plus in-flight state rather than
  partially reset or partially updated `_last_*` fields.
- Keep broker/store behavior real. Mock only the local delay or worker hook
  needed to create the in-flight observation window.
- Add an audit-style test if practical that monkeypatches a worker helper to
  return a synthetic result and asserts cached fields change only after
  `_handle_builtin_cycle_worker_result()` applies it.

Stop and re-evaluate if this refactor grows into a general TaskMonitor
decomposition. The target is ownership of cached diagnostics, not module
reshaping.

### 5. Update Specs, Backlinks, And Operational Docs

Owner: touched docs under `docs/specifications/` and this plan.

Required action:

- Update [MF-5] to remove legacy table-delete guard language and state that
  `delete` is destructive and `report_only` is non-destructive.
- Update `docs/specifications/00-Quick_Reference.md` to remove stale env rows.
- Add this plan as a related plan/backlink near the touched spec sections if
  the section already has a `Related Plans`, `Implementation plan backlink`, or
  similar list.
- Update implementation mapping text only if the final code ownership changes,
  for example if `cleanup_executor.py` is deleted or a new reactor commit
  helper becomes the named owner.
- Leave historical plans intact unless the plan corpus curation policy requires
  a status/supersession edit. Historical plans can describe how stale knobs got
  here; current specs must describe the behavior after this change.

Do not mark this plan `completed` until the implementation, tests, docs, and
required reviews have landed.

### 6. Verification

Run commands from the repo-managed environment:

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
./.venv/bin/python -m pytest tests/system/test_constants.py tests/core/test_task_monitoring.py -q
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py -q
./.venv/bin/python -m pytest tests/core/monitor -q
./.venv/bin/python -m pytest tests/specs/quick_reference/test_queue_names.py -q
./.venv/bin/mypy weft bin extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft tests
```

If `tests/core/monitor` no longer exists after deleting the cleanup executor
tests, replace that command with the closest remaining monitor-policy test
directory and record the reason in the implementation notes.

Also run these audits before final handoff:

```bash
rg -n "TASK_MONITOR_(TABLE_DELETE_ENABLED|CLEANUP_WORKERS)|cleanup_workers|table_delete_enabled|cleanup_executor|run_cleanup_jobs" weft tests docs README.md AGENTS.md
rg -n "self\\._last_.*=" weft/core/monitor/task_monitor.py
```

Expected audit outcome:

- no active code or current docs mention the removed env vars except removed-env
  rejection tests/messages and historical plans;
- no active code imports or calls `cleanup_executor.py` if it was deleted;
- `_last_*` assignments are limited to `__init__`, reactor methods, and
  explicit reactor commit helpers.

## Review Plan

This plan requires external review before implementation because it removes
public env knobs, changes destructive cleanup behavior, and refactors
worker/reactor state ownership in a large core service.

Review prompt:

```text
Read docs/plans/2026-05-29-task-monitor-config-and-reactor-cache-cleanup-plan.md,
then inspect the cited TaskMonitor code and specs. Look for errors, bad ideas,
missing rollback constraints, and latent ambiguities. Do not implement. Answer:
could you implement this confidently and correctly if asked?
```

Reviewer input paths:

- `docs/plans/2026-05-29-task-monitor-config-and-reactor-cache-cleanup-plan.md`
- `docs/specifications/01-Core_Components.md` [CC-2.3]
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]
- `docs/specifications/07-System_Invariants.md` [OBS.13.10], [OBS.13.11],
  [OBS.13.12]
- `weft/_constants.py`
- `weft/core/monitor/runtime.py`
- `weft/core/monitor/task_monitor.py`
- `weft/core/monitor/policies/api.py`
- `weft/core/monitor/policies/runtime_control.py`
- `weft/core/monitor/cleanup_executor.py`
- `tests/tasks/test_task_monitor.py`

External review was completed by the requester on 2026-05-29 and approved for
implementation.

## Implementation Record

Completed on 2026-05-29.

What landed:

- Removed active support for `WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED` and
  `WEFT_TASK_MONITOR_CLEANUP_WORKERS`. Explicit env or config override use now
  raises `ValueError` with migration guidance.
- Removed `table_delete_enabled`, `cleanup_workers`, stale worker-pool public
  diagnostics, and the dead `cleanup_executor.py` module plus its tests.
- Made the built-in `delete` processor the sole destructive mode for exact safe
  cleanup candidates; `report_only` remains non-destructive.
- Moved built-in worker cached diagnostics through a typed worker result and
  reactor commit path. Worker-local TaskMonitor copies can open fresh handles
  for allowed cleanup effects, but their cached `_last_*` fields are applied by
  the live reactor after the worker result returns.
- Added a worker-local cleanup path so copied TaskMonitor workers cannot stop or
  mutate the live reactor's service-worker registrations during finalization.
- Updated governing specs and tests to match the new contract.

Validation:

- `./.venv/bin/python -m pytest tests/core/monitor -q`
- `./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py tests/system/test_constants.py tests/core/test_task_monitoring.py tests/specs/test_plan_metadata.py -q`
- `./.venv/bin/ruff check weft tests`
- `./.venv/bin/mypy weft bin extensions/weft_docker extensions/weft_macos_sandbox`

Audits:

- Active code/current specs contain removed env names only in removed-config
  rejection paths and tests.
- Active code/current specs contain no `cleanup_executor`, `run_cleanup_jobs`,
  `cleanup_workers_configured`, `cleanup_jobs`, or `table_delete_enabled`
  references.
- Historical plans still mention the removed surfaces as non-normative records.

## Fresh-Eyes Self-Review

Completed by the plan author on 2026-05-29 after drafting.

Findings:

- Review-pending blocker: external review is required before implementation.
  This plan is intentionally left `draft` because the change crosses cleanup,
  public config, docs, and worker/reactor boundaries.
- Ambiguity fixed: the first draft risked saying "remove env vars" without
  stating whether explicit env use should be ignored or rejected. The plan now
  requires fail-fast removed-env guards with operator-useful messages.
- Ambiguity fixed: the first draft risked preserving
  `cleanup_workers_configured=1` as harmless diagnostics. The plan now says to
  remove worker-pool fields unless they report real current behavior.
- Residual risk: `task_monitor.py` has many `_last_*` fields and several
  helper methods that may run on either the reactor or a worker lane. The audit
  command and Task 4 commit-helper rule are required to keep the implementation
  from missing one worker-side cached mutation.

No other blockers found in self-review. The plan became implementation-ready
after the requester completed external review and approved it on 2026-05-29.

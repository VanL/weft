# TaskMonitor Orphan Log And Status Reconciliation Plan

Status: completed
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13], [OBS.13.12], [OBS.17]; docs/specifications/10-CLI_Interface.md [CLI-1.2.1]; docs/specifications/03-Manager_Architecture.md [MA-1.6a]
Superseded by: none

## 1. Goal

Fix the stale internal TaskMonitor TID surfaced on ops after the
`jsonl_then_delete` logging rollout. The fix has two parts: public status must
not present stale manager-owned internal service task rows as live when runtime
proof is gone, and the supervised TaskMonitor must recover bounded
pre-checkpoint `weft.log.tasks` rows that were never folded into the
Monitor-store cleanup read model.

## 2. Source Documents

- `docs/specifications/05-Message_Flow_and_State.md [MF-5]`: TaskMonitor
  cleanup, Monitor-owned collation tables, exact-delete rules, `jsonl_then_delete`
  report-before-delete, and passive PONG/status diagnostics.
- `docs/specifications/07-System_Invariants.md [OBS.13], [OBS.13.12],
  [OBS.17]`: Monitor evidence is operational only, cleanup uses five top-level
  policies, and exact deletion must be bounded and observable.
- `docs/specifications/10-CLI_Interface.md [CLI-1.2.1]`: `weft status`
  derives public task and service snapshots from queue evidence and may expose
  reconciliation diagnostics without PINGing services.
- `docs/specifications/03-Manager_Architecture.md [MA-1.6a]`:
  manager-owned internal service supervision and service-owner evidence.
- `docs/plans/2026-05-29-task-monitor-general-lifetime-reporting-plan.md`:
  completed plan for the reusable lifetime report shape. Keep the same
  `taskspec`/`lifetime`/`monitor`/`observations` record shape.
- `docs/plans/2026-05-30-task-monitor-mode-and-rotating-log-plan.md`:
  completed plan for `jsonl_then_delete`, rotating file handlers, default log
  path, and deferred-write fallback. Preserve report-before-delete behavior.
- `docs/plans/2026-05-30-task-monitor-external-log-health-plan.md`:
  completed plan for external log health diagnostics. Do not make status open
  the log path or PING the TaskMonitor.
- `docs/plans/2026-05-16-monitor-durable-collation-store-plan.md`:
  completed plan for the Monitor-store tables that own durable cleanup
  collation state and task-log message references.
- `docs/plans/2026-05-18-monitor-table-driven-retained-log-cleanup-plan.md`:
  completed plan for table-driven retained-log exact deletion. This fix must
  extend that model instead of adding a parallel delete path.
- `docs/agent-context/runbooks/writing-plans.md`,
  `docs/agent-context/runbooks/hardening-plans.md`, and
  `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`: this plan
  crosses cleanup, status, runtime evidence, and destructive queue behavior.

## 3. Investigation Summary

Ops evidence from `/app/governance` showed:

- live TaskMonitor: TID `1780238342850961408`, pid `2287299`, external log
  healthy, deferred writes empty.
- stale visible TaskMonitor: TID `1779555792870776832`, no live process, no
  runtime handle, no task-local queues, no current service-owner mapping.
- raw source rows still visible in `weft.log.tasks`: exactly
  `task_initialized`, `task_spawning`, and `task_started/running` for the old
  TID.
- no row for that TID in `weft_monitor_task_collations` or
  `weft_monitor_task_messages`.
- Monitor checkpoint for `weft.log.tasks` is newer than those raw rows, so the
  normal checkpointed ingest path never sees them again.

Root cause:

- Cleanup root cause: raw task-log rows older than the Monitor checkpoint can
  exist without Monitor-store child refs after prior partial cleanup, schema
  rollout, or legacy state. Current destructive cleanup deletes valid task-log
  rows through Monitor-store child refs, so uncollated pre-checkpoint rows are
  stranded.
- Status root cause: `weft/commands/system.py::_stale_liveness_reason()`
  currently returns `None` for internal service rows in `running` or
  `spawning`, so stale internal service task rows are never reconciled even
  when runtime proof is absent and a newer same-service owner exists.

## 4. Context And Key Files

Files to modify:

- `weft/commands/system.py`
  - Owns `weft status` task snapshots and internal service snapshots.
  - Current hidden coupling: `_collect_task_snapshot_records()` derives task
    rows before `_collect_internal_service_snapshots()` derives service rows,
    so any shared service-owner proof must be factored into a reusable helper.
- `weft/core/monitor/task_log_scanner.py`
  - Owns generator-backed task-log scans. Add any bounded pre-checkpoint scan
    here or in a closely local helper. Do not use `peek_many(limit=...)`.
- `weft/core/monitor/task_monitor.py`
  - Owns retained task-log ingest, Monitor-store summary/disposition, and
    exact raw-message deletion.
  - The new recovery path belongs in the built-in Monitor-store cycle, not in
    status and not in foreground prune.
- `weft/core/monitor/store.py` and `weft/core/monitor/sql.py`
  - Own Monitor-store access. Add narrow helpers to identify raw message IDs
    missing from `weft_monitor_task_messages`.
- `weft/core/monitor/lifetime_report.py`
  - Reuse existing builders if a recovery path needs a degraded raw-row report.
    Do not add a new JSONL shape.
- `tests/commands/test_status.py`
  - Add status regressions for stale internal service task rows.
- `tests/tasks/test_task_monitor.py`
  - Add broker-backed TaskMonitor regression for pre-checkpoint uncollated
    rows and `jsonl_then_delete` deletion ordering.
- `tests/core/test_monitor_store.py` and `tests/core/test_monitor_sql.py`
  - Add focused SQL/store tests for missing child message-ref detection.
- `docs/specifications/05-Message_Flow_and_State.md`,
  `docs/specifications/07-System_Invariants.md`,
  `docs/specifications/10-CLI_Interface.md`, and
  `docs/specifications/03-Manager_Architecture.md`
  - Update implementation notes and related-plan backlinks if behavior changes.
- `docs/lessons.md`
  - Add a short lesson if implementation confirms the same root cause.

Read first:

- `weft/commands/system.py::_collect_task_snapshot_records()`,
  `_collect_internal_service_snapshots()`, `_service_evidence_from_child_task()`,
  `_service_evidence_from_service_owner_record()`, and
  `_stale_liveness_reason()`.
- `weft/core/monitor/task_monitor.py::_run_monitor_store_cycle()`,
  `_ingest_retained_task_log_rows()`, `_emit_monitor_store_summaries()`,
  `_delete_monitor_store_task_log_rows()`, and
  `_recover_orphan_task_log_rows()`.
- `weft/core/monitor/store.py::list_stale_service_owner_candidates()` and
  `MonitorTaskCollationRecord.service_classification()`.
- `weft/core/monitor/task_log_scanner.py::GeneratorTaskLogScanner.scan_window()`.
- Existing tests near
  `tests/commands/test_status.py::test_status_services_report_task_monitor_external_log_diagnostics`
  and TaskMonitor retained-ingest tests around
  `test_task_monitor_retained_ingest_resumes_after_store_checkpoint`.

Comprehension checks before editing:

- Which raw queue row currently proves the stale TID is `running`, and why does
  status still see it after Monitor cleanup has advanced its checkpoint?
- Which path is allowed to delete valid `weft.log.tasks` rows in
  `jsonl_then_delete`, and where must the lifetime report handoff happen before
  deletion?
- Which evidence source proves an old manager-owned internal service task row
  is superseded by a newer same-service owner?

## 5. Invariants And Constraints

- Keep TID format and immutability unchanged.
- Do not write new task lifecycle events to "fix" stale status. Status
  reconciliation is a read-model decision only.
- Do not change the `task_lifetime_report` JSONL shape or policy names.
- Preserve `jsonl_then_delete` durability: a selected subject must be written
  to the external JSONL log or to `weft_monitor_deferred_writes` before exact
  deletion.
- Do not make `weft status` PING services, open the configured external log
  path, scan Monitor tables, or run cleanup.
- Do not make Monitor tables lifecycle truth. They are operational read models
  and exact-delete references.
- Pre-checkpoint recovery must be bounded by existing TaskMonitor batch/scan
  limits and must use generator/range reads, not fixed-size `peek_many()`.
- Do not advance the Monitor checkpoint while recovering older rows. The
  checkpoint remains a forward-only high-water cursor for normal ingest.
- Do not delete raw rows that are active, ambiguous, too young, or not safely
  represented by a report/store handoff.
- Do not add a schema migration unless implementation proves a narrow helper
  cannot be expressed against the existing tables. The expected fix is SQL/API
  helpers over existing `weft_monitor_task_messages`.
- No new dependencies and no broad TaskMonitor refactor.
- Tests must use real broker-backed queues for cleanup behavior. Mocking the
  broker away would hide the bug.

Review gates:

- Self-review is required before implementation.
- External review is required before implementation because this plan changes
  destructive cleanup recovery and public status reduction. If no external
  reviewer is available, record that explicitly before implementation starts.

## 6. Tasks

1. Add status regression coverage for stale internal service child rows.
   - Outcome: default `weft status` no longer presents a stale old
     TaskMonitor/Heartbeat child task as `running` when there is no runtime
     proof and a newer live same-service owner exists.
   - Files to touch:
     - `tests/commands/test_status.py`
     - `weft/commands/system.py`
   - Required setup:
     - Write old `weft.log.tasks` rows for an internal service TaskSpec
       (`task_started`, `status=running`, metadata containing
       `_weft.service.task_monitor` or heartbeat service markers).
     - Do not write a TID mapping/runtime handle for the old TID.
     - Write a newer service-owner row for the same service key with a
       different owner TID and live status.
   - Expected behavior:
     - `collect_known_tid_snapshot(..., include_terminal=True)` returns the old
       TID with `status="failed"` and reconciliation classification such as
       `superseded_internal_service_record` or
       `internal_service_runtime_missing_after_stale_window`.
     - Project-wide status with `include_terminal=False` does not list the old
       TID in the active task table.
     - The winning TaskMonitor service snapshot remains `running` from
       service-registry evidence.
   - Stop if:
     - the fix wants to write a synthetic terminal task-log row.
     - the fix would mark the current live service owner as failed because its
       child process is not directly visible from the status process.

2. Refactor internal service-owner evidence for status without adding a second
   status path.
   - Outcome: task snapshot reconciliation can determine whether an internal
     service child is stale/superseded using the same service-owner evidence
     used by service snapshots.
   - Files to touch:
     - `weft/commands/system.py`
   - Approach:
     - Extract a small helper that reads latest manager-owned service-owner
       records from `weft.state.services` and returns enough evidence keyed by
       service key and owner TID.
     - Reuse that helper from `_collect_internal_service_snapshots()` and from
       `_collect_task_snapshot_records()`; avoid two subtly different service
       reducers.
     - Add a helper that maps a TaskSpec payload to the internal service key
       using the existing `_service_key_from_taskspec_payload()`.
     - For nonterminal internal service task rows with no live runtime proof
       and stale timestamps, set public status to `failed` when either:
       - the same service key has a different live owner; or
       - no runtime proof exists beyond `STATUS_RUNTIMELESS_STALE_AFTER_SECONDS`
         and no current service evidence supports that same owner.
     - Preserve additive reconciliation metadata explaining the derived status.
   - Do not:
     - change service snapshot JSON shape except for additive reconciliation if
       required.
     - PING the service.
     - inspect Monitor-store tables.
   - Done when:
     - the new status regression passes.
     - existing service-status tests still pass.

3. Add Monitor-store helpers for missing child message refs.
   - Outcome: TaskMonitor can distinguish visible pre-checkpoint raw rows that
     were never folded into `weft_monitor_task_messages` from rows already
     represented by Monitor-store.
   - Files to touch:
     - `weft/core/monitor/sql.py`
     - `weft/core/monitor/store.py`
     - `tests/core/test_monitor_sql.py`
     - `tests/core/test_monitor_store.py`
   - Approach:
     - Add a narrow store method such as
       `missing_task_message_ids(message_ids: Sequence[int]) -> tuple[int, ...]`.
     - Implement it with trusted placeholders and the existing
       `context_key`/`message_id` columns.
     - Keep it read-only and bounded by the caller's batch size.
   - Stop if:
     - implementation wants a new table or schema version.
     - implementation starts comparing raw JSON bodies instead of exact message
       IDs.
   - Done when:
     - store tests prove known IDs are excluded and absent IDs are returned for
       SQLite-backed test stores.

4. Add a bounded pre-checkpoint raw task-log scan.
   - Outcome: TaskMonitor can discover visible `weft.log.tasks` rows older
     than the Monitor checkpoint without replaying the whole queue.
   - Files to touch:
     - `weft/core/monitor/task_log_scanner.py`
     - `weft/core/monitor/task_monitor.py`
     - `tests/tasks/test_task_monitor.py`
   - Approach:
     - Add a local scanner function or `TaskLogScanBackend` method that uses
       `Queue.peek_generator(with_timestamps=True, before_timestamp=checkpoint)`
       through the public SimpleBroker API.
     - Bound by `WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT` and
       `WEFT_TASK_MONITOR_BATCH_SIZE`.
     - Decode through the existing `decode_task_log_row()` path.
     - Filter to rows older than `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS`.
     - Filter to message IDs missing from `weft_monitor_task_messages`.
     - Do not advance the normal checkpoint.
   - Stop if:
     - the scanner needs backend-specific SQL against SimpleBroker internals.
     - a fixed `peek_many()` limit appears in a correctness path.
   - Done when:
     - a test can set a checkpoint past old raw rows and the recovery scanner
       still finds only the bounded missing rows.

5. Coalesce and retire pre-checkpoint uncollated rows through existing
   `jsonl_then_delete` semantics.
   - Outcome: visible pre-checkpoint valid rows are folded into Monitor-store,
     summarized/reported when policy permits, and exact-deleted through the
     existing raw-message delete path.
   - Files to touch:
     - `weft/core/monitor/task_monitor.py`
     - `weft/core/monitor/store.py` only if Task 3 helpers need a companion
       batch write helper.
     - `tests/tasks/test_task_monitor.py`
   - Approach:
     - Add a TaskMonitor helper such as
       `_recover_pre_checkpoint_task_log_rows(store, now_ns=...)`.
     - Call it only after the normal retained-ingest pass reaches a complete
       high-water and before `_emit_monitor_store_summaries()`, so recovered
       families can be summarized and deleted in the same monitor cycle when
       ready.
     - For valid decoded rows, build `MonitorTaskEventUpdate`s with
       `update_from_task_log_row()` and call `store.record_task_log_updates()`
       with `checkpoint_message_id=None`.
     - For malformed or unrecognized old rows, use the existing raw-row
       lifetime-report path before exact delete in `jsonl_then_delete`; in
       `delete` mode, exact-delete them under the existing malformed-row
       authority.
     - For valid rows, do not direct-delete immediately in `jsonl_then_delete`.
       Let `build_collation_lifetime_report()` and
       `_delete_monitor_store_task_log_rows()` perform the report/store
       handoff and exact-delete ordering.
     - Report policy progress under `monitor_store.lifecycle` with a clear
       domain such as `pre_checkpoint_task_log_recovery`.
   - Required regression:
     - Create old task-monitor service task-log rows.
     - Manually set the Monitor checkpoint past those rows without inserting
       Monitor child refs.
     - Write a newer same-service live service-owner row proving the old
       service owner is stale.
     - Run a real `TaskMonitor` with `WEFT_TASK_MONITOR_MODE=jsonl_then_delete`
       and an external JSONL path.
     - Assert the old raw rows are gone, a `task_lifetime_report` exists for
       the old TID with top-level `taskspec`, `lifetime`, `monitor`, and
       `observations`, and deferred writes remain empty when the external path
       is healthy.
   - Stop if:
     - deleting the old rows requires bypassing lifetime report handoff.
     - the recovery loop can reprocess the same unrecoverable row forever
       without progress diagnostics.

6. Update docs and lessons.
   - Outcome: specs match the new recovery/status behavior and future agents
     do not rediscover this edge.
   - Files to touch:
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/07-System_Invariants.md`
     - `docs/specifications/10-CLI_Interface.md`
     - `docs/specifications/03-Manager_Architecture.md` if the service-owner
       status wording needs a backlink/update.
     - `docs/lessons.md`
   - Required spec notes:
     - pre-checkpoint recovery is bounded and does not move the forward
       checkpoint.
     - status may reconcile stale internal service child task rows to a
       terminal public status without writing lifecycle truth.
     - `weft status` remains passive and does not ping services or run cleanup.
   - Lesson candidate:
     - A forward Monitor checkpoint cannot be the only cleanup cursor; legacy
       or partial-rollout rows before that checkpoint need bounded recovery or
       status will continue to replay stale lifecycle evidence.

## 7. Testing Plan

Run red tests first where practical:

```bash
./.venv/bin/python -m pytest tests/commands/test_status.py -q
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py -q
```

Targeted verification after implementation:

```bash
./.venv/bin/python -m pytest \
  tests/commands/test_status.py \
  tests/core/test_monitor_sql.py \
  tests/core/test_monitor_store.py \
  tests/tasks/test_task_monitor.py \
  -q
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py -q
./.venv/bin/ruff check weft tests
./.venv/bin/mypy weft bin extensions/weft_docker extensions/weft_macos_sandbox
```

Run the full suite before completion because this crosses status, Monitor
cleanup, and persisted queue behavior:

```bash
./.venv/bin/python -m pytest -q
git diff --check
```

Observable behavior to prove:

- Old internal service task rows with no runtime proof and a newer live
  same-service owner are no longer shown as active `running` work.
- Current live internal services remain `running`.
- Pre-checkpoint uncollated rows are folded into Monitor-store through bounded
  recovery, reported through the standard lifetime shape, and exact-deleted
  only after the report handoff succeeds.
- The Monitor checkpoint remains forward-only and is not reset or moved
  backward.

## 8. Rollout And Post-Deploy Checks

Rollout is source-only. No schema migration is expected. The data deletion edge
is still a one-way operational action because old raw rows may be exact-deleted
after successful report handoff.

After deploying to ops, run:

```bash
cd /app/governance
. ./bin/weft_host_env.sh
/opt/venv/bin/weft status --json --context /app/governance
grep -R "1779555792870776832" logs/weft.log* || true
PGPASSWORD="$WEFT_BACKEND_PASSWORD" psql \
  -h "$WEFT_BACKEND_HOST" -p "$WEFT_BACKEND_PORT" \
  -U "$WEFT_BACKEND_USER" -d "$WEFT_BACKEND_DATABASE" \
  -Atc "select queue, ts from ${WEFT_BACKEND_SCHEMA}.messages where body like '%1779555792870776832%' order by ts;"
```

Expected post-deploy result:

- `weft status` shows only the live TaskMonitor service as running.
- The stale TID is absent from the default active task list, or direct task
  status shows a terminal public status with reconciliation.
- Raw `weft.log.tasks` rows for the stale TID are gone after the next bounded
  monitor cycle.
- `logs/weft.log*` contains a `task_lifetime_report` for the stale TID if the
  deployment uses `jsonl_then_delete`.
- TaskMonitor diagnostics still show external log `healthy=true` and
  `deferred_pending=0` unless an unrelated file-path issue exists.

## 9. Rollback

Source rollback is straightforward before deployment: revert the status
reconciliation and recovery code.

After deployment, rollback cannot restore raw rows already exact-deleted by the
recovery path. That is acceptable only if the implementation preserved
`jsonl_then_delete` report-before-delete semantics. If this is not acceptable
for a target environment, deploy first with `WEFT_TASK_MONITOR_MODE=report_only`
or run the new tests against a copy of production broker state before enabling
destructive mode.

No queue names, TaskSpec schema, JSONL report shape, or Monitor table schema
should change, so rollback should not require database migration.

## 10. Out Of Scope

- No redesign of TaskMonitor collation or cleanup scheduling.
- No new policy names beyond the five top-level policies in [OBS.13.12].
- No public CLI command or option changes.
- No manual production data deletion in this plan.
- No attempt to infer domain-specific task success from app-specific metadata.
- No external alerting or health-monitor feature.

## 11. Review Plan

Self-review was completed after drafting and before implementation.

External review was required before implementation because the plan changes a
destructive cleanup recovery path and the public status read model. The review
was completed by the maintainer before implementation approval.

> Read `docs/plans/2026-05-31-task-monitor-orphan-log-and-status-reconciliation-plan.md`
> plus the cited spec sections and current code. Look for unsafe cleanup
> semantics, status ambiguity, missing tests, or places where a zero-context
> implementer could choose the wrong path. Could you implement this
> confidently and correctly?

## 12. Fresh-Eyes Self-Review

Self-review completed while drafting. Findings and adjustments:

- Finding: the first recovery idea risked direct-deleting valid raw rows in
  `jsonl_then_delete`. Updated Tasks 4 and 5 to require coalescing valid rows
  into Monitor-store and then using the existing lifetime-report and exact
  delete path.
- Finding: status and service snapshots already use overlapping service-owner
  evidence. Updated Tasks 1 and 2 to require a shared helper rather than two
  divergent reducers.
- Finding: a pre-checkpoint recovery scan can become an unbounded replay.
  Added explicit batch/scan bounds, missing-message-ref filtering, and a
  stop gate forbidding fixed-limit `peek_many()` correctness paths.
- Residual risk: the exact close reason for an old open internal service
  lifetime report must be chosen carefully during implementation. It should
  describe lifetime evidence such as `stale_service_owner`, not cleanup
  mechanics.
- Implementation resolved the close-reason risk by using
  `stale_service_owner` for same-service replacement evidence and by keeping
  cleanup mechanics in Monitor/progress metadata rather than the lifetime
  close reason.

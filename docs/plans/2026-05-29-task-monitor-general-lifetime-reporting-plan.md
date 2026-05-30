# TaskMonitor General Lifetime Reporting Plan

Status: completed
Source specs: docs/specifications/00-Quick_Reference.md; docs/specifications/01-Core_Components.md [CC-2.3]; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13], [OBS.13.10], [OBS.13.11], [OBS.13.12], [OBS.17]
Superseded by: none

## Goal

Implement the reserved `jsonl_then_delete` TaskMonitor processor as a general,
reusable lifetime-reporting mode. Before any destructive cleanup effect in this
mode, the responsible policy durably hands off one lifetime report for the
selected subject, then proceeds through the same exact policy-selected delete
path used by `delete`. The preferred handoff is one JSONL line to the
configured external log path. If that external write fails, TaskMonitor writes
the same report to a Monitor-owned deferred writes table and allows cleanup to
proceed only after that fallback write succeeds. The report is about the task
lifetime or closest available lifetime observation, not about the deletion
mechanics. Each policy uses the same baseline report envelope and can add
richer policy-specific lifetime content when it has better data, especially
Monitor-store collation records.

Implemented on 2026-05-29. It changes destructive cleanup behavior, external
JSONL payloads, deferred Monitor-owned outbox state, and policy boundaries.

## Non-Goals

- Do not change the default processor. `delete` remains the default.
- Do not make `report_only` write lifetime JSONL. It remains non-destructive
  diagnostic/report behavior.
- Do not make Monitor tables, JSONL files, or lifetime reports lifecycle truth
  or result authority.
- Do not create new top-level cleanup policy identities beyond the five in
  [OBS.13.12].
- Do not use the deferred writes table as lifecycle truth, result authority,
  or a general dedupe database. It is a Monitor-owned operational outbox for
  reports that could not be written to the configured external JSONL path.
  Stable `report_id` values remain the consumer-side dedupe mechanism.
- Do not invent a second cleanup engine. Reuse the existing policy selection
  and exact-delete helpers.
- Do not make PONG or STATUS open the JSONL file, scan queues, or query Monitor
  tables.
- Do not add a new external service, dependency, queue, or long-lived worker.

## Source Documents

Read these before implementation:

- `AGENTS.md`, especially constants, docs, testing, and dirty-tree guidance.
- `docs/agent-context/decision-hierarchy.md`: specs are normative; plans guide
  execution only.
- `docs/agent-context/principles.md`: contract changes update producers,
  consumers, specs, tests, and plans together.
- `docs/agent-context/engineering-principles.md`: extend existing paths, keep
  queue truth canonical, and keep traceability bidirectional.
- `docs/agent-context/runbooks/writing-plans.md`,
  `docs/agent-context/runbooks/hardening-plans.md`, and
  `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`: this is a
  risky, boundary-crossing cleanup and payload-contract change.
- `docs/lessons.md`, especially lessons on generator-based history reads,
  task-log retention, and plan hardening.

Governing specs:

- `docs/specifications/00-Quick_Reference.md`: documents
  `WEFT_LOG_TASKS_EXTERNAL_PATH`, `WEFT_LOG_TASKS_EXTERNAL_MODE`, and
  `WEFT_TASK_MONITOR_PROCESSOR`.
- `docs/specifications/01-Core_Components.md` [CC-2.3]: `TaskMonitor` is the
  supervised persistent reactor and owns cached diagnostics and maintenance
  boundaries.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]: retained task-log
  cleanup, Monitor-store collation, external JSONL output, and exact deletion
  are operational evidence paths only.
- `docs/specifications/07-System_Invariants.md` [OBS.13]: Monitor artifacts
  are operational evidence only.
- `docs/specifications/07-System_Invariants.md` [OBS.13.10]: built-in task-log
  cleanup and runtime cleanup are the only TaskMonitor worker lanes allowed to
  perform broker/store cleanup effects.
- `docs/specifications/07-System_Invariants.md` [OBS.13.11]: PONG diagnostics
  are cached and must not perform live scans or report/delete work.
- `docs/specifications/07-System_Invariants.md` [OBS.13.12]: the five
  top-level cleanup policy identities are fixed.
- `docs/specifications/07-System_Invariants.md` [OBS.17]: destructive cleanup
  must use exact message IDs and explicit policy selection.

Related plans:

- `docs/plans/2026-05-29-task-monitor-config-and-reactor-cache-cleanup-plan.md`
  is completed and defines the current `delete` vs `report_only` contract.
- `docs/plans/2026-05-16-task-log-external-logging-and-retention-policy-plan.md`
  is historical context for raw/collated external task-log output. It is not
  normative for this new lifetime-reporting processor.
- `docs/plans/2026-05-26-monitor-five-cleanup-policy-consolidation-plan.md`
  is completed context for the five-policy progress contract.

## Desired Contract

`jsonl_then_delete` means:

1. Use the configured external JSONL path as the preferred lifetime-report
   destination.
2. For each destructive policy-selected subject, build one lifetime report
   first.
3. Try to write the report to the external JSONL path.
4. If the external write fails, write the same report body to the
   Monitor-owned deferred writes table.
5. Delete the selected subject only after the report was either written to the
   external JSONL path or durably recorded in the deferred writes table.
6. If both external write and deferred write fail, do not delete that subject
   in that attempt.
7. If report handoff succeeds and the delete later fails, the same subject may
   be reported again on retry. This is acceptable because records carry a
   deterministic `report_id`.
8. Use the same exact-delete implementation as `delete` after report handoff
   succeeds.

Baseline report envelope:

```json
{
  "schema_version": 1,
  "record_type": "task_lifetime_report",
  "report_id": "stable deterministic id",
  "emitted_at_ns": 123,
  "monitor_tid": "1778...",
  "source_policy": "task_local.dead_tid",
  "report_kind": "dead_tid_inferred",
  "completeness": "inferred",
  "subject": {
    "tid": "1778..."
  },
  "lifetime": {
    "tid": "1778...",
    "name": null,
    "runner": null,
    "parent_tid": null,
    "role": null,
    "status": null,
    "terminal_seen": false,
    "terminal_status": null,
    "return_code": null,
    "first_seen_at_ns": null,
    "last_seen_at_ns": null,
    "started_at_ns": null,
    "completed_at_ns": null,
    "close_reason": "dead_tid_runtime_cleanup"
  },
  "taskspec": null,
  "monitor": {
    "queue_names": ["T1778....inbox"]
  },
  "observations": {}
}
```

Rules:

- `record_type` is always `task_lifetime_report` for this processor.
- `source_policy` must be one of the five top-level policies from [OBS.13.12].
- `report_kind` is policy-specific but not a new policy identity.
- `completeness` should be one of a small documented vocabulary such as
  `collated`, `partial`, `inferred`, `raw_row`, or `state_only`.
- `lifetime` is the stable baseline. Policies fill what they can and use
  `null` when they cannot know a field.
- `taskspec` is always present as a top-level key. It is populated whenever
  the policy has TaskSpec-shaped evidence and is `null` for inferred or
  state-only reports. For normal collated task reports, it is derived from the
  `MonitorTaskCollationRecord` TaskSpec summary and is the primary report
  payload.
- `monitor` is always present as a top-level key. It carries compact policy
  and Monitor-only provenance, such as first/last/terminal message IDs, exact
  queue/message references, queue names, and service classification. It must
  not dump the full Monitor storage row.
- `observations` is policy-specific context that helps explain what Weft saw,
  but it must not present the cleanup mechanics as the main record.
- Do not include an `effect: delete` field. The processor name and
  implementation semantics define log-then-delete; the record should be a
  lifetime report.

Deferred writes contract:

- Add a Monitor-owned durable table named `weft_monitor_deferred_writes` via a
  constant such as `WEFT_MONITOR_DEFERRED_WRITES_TABLE`.
- The table is an operational outbox for JSONL records that were accepted for
  cleanup but not written externally. It is not queue truth, lifecycle truth,
  or a result source.
- Use `PRIMARY KEY (context_key, report_id)` so repeated attempts update the
  same pending report instead of creating unbounded duplicate rows.
- Store the full JSONL body as canonical JSON text in `body_json`, along with
  `record_type`, `created_at_ns`, `updated_at_ns`, `first_external_error`,
  `last_external_error`, `attempt_count`, `last_attempt_at_ns`, and
  `flushed_at_ns`.
- Preserve `created_at_ns` and the first external error on upsert. Update
  `updated_at_ns`, `last_external_error`, `attempt_count`, and
  `last_attempt_at_ns`.
- A successful deferred write is a valid report handoff for cleanup ordering.
  If the deferred write itself fails, cleanup for that subject must be blocked.
- Each TaskMonitor cycle should attempt a bounded flush of pending deferred
  writes to the configured external JSONL path. On successful external write,
  mark `flushed_at_ns`. Prune flushed rows only under an explicit bounded
  retention/prune rule.
- PONG and STATUS may expose cached counts such as pending deferred writes and
  last deferred write error. They must not scan or flush the table live.
- Because this table is additive and old code can ignore it, prefer creating it
  with `CREATE TABLE IF NOT EXISTS` without bumping `WEFT_MONITOR_SCHEMA_VERSION`.
  Bump the schema version only if implementation requires an incompatible
  monitor-store contract, and then document rollback as a one-way store-version
  change before implementation proceeds.

## Context and Key Files

Primary implementation files:

- `weft/_constants.py`
  - Current state: `WEFT_TASK_MONITOR_PROCESSOR_BUILTINS` already includes
    `jsonl_then_delete`; external JSONL constants and mode validation live
    here.
  - Expected work: add `WEFT_MONITOR_DEFERRED_WRITES_TABLE` and
    report-kind/completeness constants only if needed; do not add a new env var
    unless external review explicitly accepts it. Do not bump
    `WEFT_MONITOR_SCHEMA_VERSION` for the additive deferred writes table unless
    review accepts the rollback cost.
- `weft/core/monitor/runtime.py`
  - Current state: validates processor, external path, external enablement,
    external mode, and collation-store enablement.
  - Expected work: fail fast when `jsonl_then_delete` cannot produce lifetime
    reports. At minimum require `WEFT_LOG_TASKS_EXTERNAL_PATH` enabled and
    `WEFT_TASK_MONITOR_COLLATION_STORE_ENABLED=true`. Reject `raw` external
    mode for `jsonl_then_delete` unless the spec is deliberately changed.
- `weft/core/monitor/external_log.py`
  - Current state: owns JSONL file writes for raw task-log rows and collated
    summaries through `ExternalTaskLogSink`.
  - Expected work: add an `emit_lifetime_report(...)` method or equivalent
    shared writer that serializes the baseline report envelope and raises
    `ExternalTaskLogError` on write errors. Keep file I/O centralized here.
- `weft/core/monitor/lifetime_report.py` (new)
  - Expected work: own small dataclasses or builder functions for baseline
    lifetime reports. Keep this module pure and JSON-shape focused. It should
    not scan queues, open stores, delete rows, or know about worker lanes.
- `weft/core/monitor/task_monitor.py`
  - Current state: orchestrates Monitor-store ingestion, summary emission,
    raw row deletion, runtime cleanup slices, worker result commits, and
    `jsonl_then_delete` reserved error paths.
  - Expected work: wire lifetime reporting before destructive effects for the
    policy-specific paths it owns directly. Pay special attention to
    `_ingest_retained_task_log_rows(...)`: today, in destructive mode, it can
    delete valid raw task-log rows immediately after folding them into the
    Monitor store. `jsonl_then_delete` must not use that ordering for valid
    rows because the lifetime report is not emitted until the family is
    summary-ready.
- `weft/core/monitor/sql.py`
  - Current state: owns Monitor-store DDL and trusted SQL templates.
  - Expected work: add DDL and query helpers for the deferred writes outbox,
    including upsert, bounded pending selection, mark-flushed, cached counts,
    and bounded prune helpers.
- `weft/core/monitor/cleanup.py`
  - Current state: selected broad cleanup candidates and applies exact deletes
    internally for runtime-state queues and old foreground cleanup surfaces.
  - Expected work: add a reusable pre-apply reporting callback so the caller
    can emit lifetime reports before `_apply_policy_candidates(...)`.
- `weft/core/monitor/policies/runtime_control.py`
  - Current state: owns runtime cleanup selection/result types for terminal,
    reserved, and dead-TID runtime work.
  - Expected work: add report input details only if TaskMonitor cannot build
    adequate reports from the existing selection records. Do not put file I/O
    here.
- `weft/core/monitor/store.py`
  - Current state: owns `MonitorTaskCollationRecord` and
    `MonitorTaskCollationRecord.to_summary()`.
  - Expected work: reuse `to_summary()` for rich collation lifetime reports;
    add narrowly scoped fetch helpers only if needed for batch enrichment; add
    deferred-write outbox methods without treating the table as lifecycle
    truth.

Tests likely to add or update:

- `tests/core/monitor/test_lifetime_report.py` for pure report builders and
  deterministic `report_id`.
- `tests/core/test_monitor_store.py` and `tests/core/test_monitor_sql.py` for
  deferred writes table DDL and store helpers.
- `tests/tasks/test_task_monitor.py` for broker-backed `jsonl_then_delete`
  behavior across Monitor-store collations and runtime cleanup.
- `tests/core/test_task_monitoring.py` for runtime-config validation.
- `tests/system/test_constants.py` if new constants or config normalization are
  added.
- `tests/specs/test_plan_metadata.py`, `tests/specs/test_spec_hygiene.py`, and
  relevant quick-reference spec tests after docs updates.

Comprehension questions before editing:

1. Which five top-level policies may appear in `source_policy`, and where are
   they consolidated for PONG/status progress?
2. Which current functions delete raw task-log rows, task-local runtime queues,
   reserved queues, dead-TID queues, and runtime-state rows?
3. Which paths have a `MonitorTaskCollationRecord`, and which paths only have
   a TID, queue name, message ID, or malformed row?
4. Where does `ExternalTaskLogSink` currently fail closed, and how does
   TaskMonitor cache external-log health without live PONG I/O?
5. If a report succeeds but deletion fails, what stable `report_id` would let
   downstream readers deduplicate a retry?
6. Can the deferred writes table be added without incrementing the monitor
   schema version, and what would rollback do if it cannot?

If any answer is unclear, stop and reread the files above before editing.

## Invariants and Constraints

- Queue truth does not move. Lifetime reports are operational evidence only.
- Monitor tables remain operational evidence only.
- `jsonl_then_delete` must be durable-before-delete per subject: no external
  JSONL write and no durable deferred-write row means no destructive effect for
  that subject in that attempt.
- A successful deferred-write row is enough to permit cleanup. The system then
  owes a bounded retry to flush that report to the external JSONL path.
- `delete` semantics must stay unchanged except for shared helper refactors.
- `report_only` must stay non-destructive and must not require a JSONL path.
- Policy identities remain exactly the five names in [OBS.13.12].
- Private phases may use `report_kind`, `close_reason`, or observations, but
  must not create new `policy_progress[*].policy` identities.
- PONG/STATUS must use cached diagnostics only and must not emit reports.
- Existing exact-delete helpers remain the only destructive row-delete path.
- Runtime cleanup remains bounded by existing family limits and deadlines.
- The new JSONL stream is at-least-once. Do not use the deferred writes table
  as a semantic dedupe ledger. Use deterministic `report_id` values instead.
- Deferred write flushing must be bounded and retryable. It must not introduce
  an unbounded startup sweep, PONG scan, or long-lived worker.
- Do not delete deferred write rows until the external write succeeded and a
  bounded retention/prune condition applies.
- The report builder must be reusable by all policies and must not hard-code
  Monitor-store-only assumptions.
- Tests must use real broker-backed paths for destructive behavior. Mock only
  the JSONL write failure boundary or filesystem error where needed.
- No new dependencies.
- No broad TaskMonitor reorganization for taste.

## Rollout and Rollback

Rollout:

- Ship the spec and implementation together.
- `jsonl_then_delete` is already accepted by configuration but currently
  fail-closed. This change turns it into a real mode only when the required
  external path and collation-store config are valid.
- Add the deferred writes table idempotently as Monitor-owned operational
  outbox state. Prefer no monitor schema-version bump because the table is
  additive and old code can ignore it.
- Existing `delete`, `report_only`, raw external mode, and collated external
  mode should remain usable without migration.

Rollback:

- Reverting should be able to restore `jsonl_then_delete` to the current
  fail-closed reserved behavior without changing existing `delete` output.
- The new JSONL records are additive operational files. No rollback reader is
  required inside Weft.
- If the additive deferred writes table is shipped without a monitor
  schema-version bump, old code should ignore the extra table. If
  implementation requires a schema-version bump, rollback to older code will
  likely reject the newer Monitor store until the database is migrated or
  recreated; treat that as an explicit one-way door.
- If a deployment cannot tolerate the new mode, set
  `WEFT_TASK_MONITOR_PROCESSOR=delete` or `report_only` and leave the JSONL
  file in place as operational output.

One-way doors:

- Deleting after a lifetime report is destructive. Treat the first
  implementation as high risk and prove every path with broker-backed tests.
- JSONL schema shape is a public-ish operational contract. Review the baseline
  envelope before implementation starts.
- A monitor schema-version bump would be a rollback one-way door. Avoid it for
  the additive deferred writes table unless the final design cannot remain
  backward-compatible with old stores.

## Tasks

1. Update the specs for the lifetime-reporting contract.
   - Outcome: specs describe `jsonl_then_delete`, the baseline lifetime report
     envelope, durable-before-delete semantics, deferred write fallback,
     at-least-once output, and the fixed policy identity boundary.
   - Files to touch:
     - `docs/specifications/00-Quick_Reference.md`
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/07-System_Invariants.md`
     - `docs/specifications/01-Core_Components.md` if implementation mapping
       text needs a TaskMonitor/external-log owner note
   - Read first:
     - the governing spec sections listed above
     - this plan's Desired Contract section
   - Constraints:
     - make clear that reports are lifetime reports, not delete audit records
     - keep `source_policy` constrained to the five existing policies
     - document `report_id` duplicate handling
     - document that external write failure can fall back to durable deferred
       write, but both failures block cleanup
   - Tests:
     - run `./.venv/bin/python -m pytest tests/specs/test_spec_hygiene.py tests/specs/quick_reference/test_queue_names.py -q`
   - Stop and re-evaluate if the spec text starts implying JSONL is lifecycle
     truth or result authority.

2. Add the reusable lifetime report model and writer.
   - Outcome: one pure builder module can produce baseline reports for any
     policy, and `ExternalTaskLogSink` can write them.
   - Files to touch:
     - `weft/core/monitor/lifetime_report.py` (new)
     - `weft/core/monitor/external_log.py`
     - `tests/core/monitor/test_lifetime_report.py` (new)
   - Required behavior:
     - build a baseline `task_lifetime_report` record with stable keys
     - compute deterministic `report_id` from stable inputs such as context
       key, source policy, report kind, subject identity, close reason, and
       selected message IDs or queue names when present
     - provide a builder for `MonitorTaskCollationRecord.to_summary()` so
       normal collations include rich Monitor-store data
     - provide generic builders for inferred TID/queue/message-row subjects
     - serialize through `ExternalTaskLogSink` with the same fail-closed error
       style as `emit_raw()` and `emit_collated()`
   - Constraints:
     - do not scan queues, open stores, or delete from the builder module
     - do not make the sink know policy internals
     - do not include an `effect` field
   - Tests:
     - deterministic `report_id` is stable across calls
     - `MonitorTaskCollationRecord` reports include `collation`
     - inferred/degraded reports have the baseline `lifetime` keys with
       unknown values as `None`
     - sink write failure raises `ExternalTaskLogError`
   - Stop and re-evaluate if the report module starts duplicating
     `MonitorTaskCollationRecord.to_summary()`.

3. Add the Monitor deferred writes outbox.
   - Outcome: TaskMonitor has a durable, bounded fallback for lifetime reports
     that cannot be written to the external JSONL path.
   - Files to touch:
     - `weft/_constants.py`
     - `weft/core/monitor/sql.py`
     - `weft/core/monitor/store.py`
     - `tests/core/test_monitor_store.py`
     - `tests/core/test_monitor_sql.py`
   - Required behavior:
     - create `weft_monitor_deferred_writes` idempotently with a code-owned
       table-name constant
     - store `context_key`, `report_id`, `record_type`, `body_json`,
       `created_at_ns`, `updated_at_ns`, `first_external_error`,
       `last_external_error`, `attempt_count`, `last_attempt_at_ns`, and
       `flushed_at_ns`
     - upsert by `(context_key, report_id)` so retries do not create unbounded
       duplicate pending rows
     - list pending rows in bounded batches ordered by creation/update time
     - mark rows flushed after the external JSONL write succeeds
     - provide cached count/status helpers for reactor-owned diagnostics
     - provide bounded prune helpers for already flushed rows
   - Constraints:
     - prefer not to bump `WEFT_MONITOR_SCHEMA_VERSION`; if the implementation
       cannot avoid it, stop and update the plan with the rollback impact
       before proceeding
     - do not make deferred writes lifecycle truth or result authority
     - do not let PONG/STATUS query this table live
   - Tests:
     - schema creation adds the table without changing existing store status
       semantics
     - repeated upsert with the same `report_id` preserves `created_at_ns` and
       increments/updates attempt fields
     - bounded pending selection excludes flushed rows
     - mark-flushed and prune helpers affect only the selected context

4. Validate `jsonl_then_delete` configuration explicitly.
   - Outcome: unsupported or incomplete config fails before the monitor starts
     deleting.
   - Files to touch:
     - `weft/core/monitor/runtime.py`
     - `weft/_constants.py` only if constants are needed
     - `tests/core/test_task_monitoring.py`
     - `tests/system/test_constants.py` if constants/config normalization move
   - Required behavior:
     - `jsonl_then_delete` requires `WEFT_LOG_TASKS_EXTERNAL_PATH` and external
       logging enabled
     - `jsonl_then_delete` requires `WEFT_TASK_MONITOR_COLLATION_STORE_ENABLED`
       because normal lifetime reports depend on Monitor-store collation
     - `jsonl_then_delete` rejects `WEFT_LOG_TASKS_EXTERNAL_MODE=raw` unless
       the spec is explicitly changed to define raw lifetime reports
     - error messages must say what setting to change
   - Constraints:
     - do not change `delete`, `report_only`, or custom processor validation
     - do not add a new env var unless external review approves it
   - Tests:
     - valid path + collated mode + collation store enabled yields runtime
       config with processor `jsonl_then_delete`
     - missing path, disabled external logging, raw mode, or disabled collation
       store raises `ValueError`

5. Wire Monitor-store collation lifetime reports before raw deletion.
   - Outcome: normal collated task/service lifetimes emit one report before
     retained raw rows are exact-deleted.
   - Files to touch:
     - `weft/core/monitor/task_monitor.py`
     - `tests/tasks/test_task_monitor.py`
   - Current path to reuse:
     - `_run_monitor_store_cycle()`
     - `_ingest_retained_task_log_rows()`
     - `_emit_monitor_store_summaries()`
     - `_emit_monitor_store_summary()`
     - `_delete_monitor_store_task_log_rows()`
   - Required behavior:
     - when processor is `jsonl_then_delete`, normal summary emission writes
       `task_lifetime_report` records using the generic builder and
       `MonitorTaskCollationRecord.to_summary()`
     - when processor is `jsonl_then_delete`, retained valid task-log rows are
       folded into the Monitor store but not raw-deleted during ingestion;
       raw deletion for those valid rows must wait until the family has a
       successful lifetime report marker such as `summary_emitted_at_ns`
     - raw task-log deletion for a collated family happens only after its
       lifetime report handoff succeeds, either by external JSONL write or by
       durable deferred write
     - malformed rows cannot produce a rich Monitor collation and may still be
       reported/deleted as `raw_row` subjects in `task_log.retention`
     - if the external JSONL write fails but deferred write succeeds, cleanup
       may proceed and the deferred report remains pending for later flush
     - if both external and deferred writes fail, the family remains retryable
       and raw rows remain visible or table-referenced
     - for `jsonl_then_delete`, the Monitor-store raw delete query should use
       summary/report completion as a gate, for example
       `require_summary=True`; do not reuse the `delete` path's
       `require_summary=False` behavior without adding an equivalent
       lifetime-report guard
     - existing `delete` with optional collated external output keeps its
       current `task_log_collated` compatibility behavior
   - Constraints:
     - do not use summary emission as lifecycle truth
     - define `summary_emitted_at_ns` for this mode as "lifetime report
       handoff accepted", whether the report was written externally or
       deferred; do not mark it when both report paths fail
     - avoid duplicate report generation within one successful cycle
   - Tests:
     - terminal user task emits `task_lifetime_report` with
       `source_policy=monitor_store.lifecycle`,
       `completeness=collated`, top-level `taskspec`, and compact `monitor`
       provenance
     - service/manager collation includes service classification fields through
       `monitor.service`
     - external write failure with successful deferred write allows exact raw
       deletion and leaves one pending deferred write
     - external write failure plus deferred write failure blocks raw deletion
       and leaves the Monitor row retryable
     - deletion failure after successful report may leave a duplicate-safe
       retry with the same `report_id`

6. Add a reusable pre-apply reporting seam for candidate-based cleanup.
   - Outcome: cleanup policies selected through `weft/core/monitor/cleanup.py`
     can emit lifetime reports before exact delete without duplicating apply
     logic.
   - Files to touch:
     - `weft/core/monitor/cleanup.py`
     - `weft/core/monitor/task_monitor.py`
     - `tests/core/monitor/test_cleanup.py` or nearest existing cleanup tests
     - `tests/tasks/test_task_monitor.py`
   - Approach:
     - add an optional pre-apply callback to `run_task_monitor_cleanup()` or
       `TaskMonitorCleanupConfig`
     - invoke it after candidates are selected and before
       `_apply_policy_candidates(...)`
     - if the callback cannot hand off a report externally or to the deferred
       writes table for a candidate group, do not apply that group in that
       attempt and return a blocked policy result
     - keep the callback typed around existing `CleanupCandidate` values and
       policy names
   - Constraints:
     - keep `delete` behavior identical by passing no callback
     - do not make `cleanup.py` import `ExternalTaskLogSink`
     - do not make `cleanup.py` open the Monitor store
   - Tests:
     - callback is not called in report-only mode
     - callback failure prevents exact delete for selected candidates
     - external callback failure with successful deferred write still permits
       exact delete for selected candidates
     - callback success preserves existing apply counts and policy progress
   - Stop and re-evaluate if the implementation starts duplicating
     `apply_exact_prune_candidates()`.

7. Wire policy-specific lifetime reports for runtime and fallback cleanup.
   - Outcome: every destructive TaskMonitor-owned policy path can emit at
     least a baseline lifetime report before deletion in `jsonl_then_delete`.
   - Files to touch:
     - `weft/core/monitor/task_monitor.py`
     - `weft/core/monitor/policies/runtime_control.py` only if existing result
       data is insufficient
     - `tests/tasks/test_task_monitor.py`
   - Policy expectations:
     - `task_log.retention`: report malformed raw rows and any retained raw-row
       subject that is deleted outside a rich collation. Do not emit fake
       full-lifetime reports for ordinary valid rows before collation.
     - `monitor_store.lifecycle`: report normal Monitor collation lifetimes and
       any stale/suspected service-owner collation lifetimes. Tombstone pruning
       and parent-row retirement should not emit a second lifetime report for a
       family that was already reported.
     - `task_local.terminal_runtime`: report from the Monitor collation row
       when available, with observations listing standard control/reserved
       queues seen for that TID.
     - `task_local.dead_tid`: report an inferred lifetime when no Monitor row
       exists, with observations listing standard queue names and any age or
       retention facts already computed by the policy.
     - `runtime_state.retention`: report a `state_only` lifetime observation
       for stale/malformed runtime-state rows. Include TID if the decoded row
       has one; otherwise include queue/message identity and bounded payload
       metadata.
   - Constraints:
     - do not report cleanup mechanics as the primary payload
     - do not perform unbounded store lookups for enrichment; batch fetch by
       TID where needed and bounded by the policy batch size
     - do not let missing optional collation enrichment block a valid baseline
       report unless the policy specifically requires rich collation data
   - Tests:
     - terminal runtime queue cleanup emits a collated report before queue
       deletion
     - dead-TID cleanup emits an inferred report before queue deletion
     - runtime-state cleanup emits a state-only report before row deletion
     - malformed task-log deletion emits a raw-row report before row deletion
     - each failure case leaves the target undeleted and retryable
     - external-write failure cases leave pending deferred rows and still
       delete only after durable deferred handoff

8. Flush deferred writes as bounded reactor work.
   - Outcome: reports deferred because the external JSONL path was unavailable
     are retried later without blocking normal monitor cleanup forever.
   - Files to touch:
     - `weft/core/monitor/task_monitor.py`
     - `weft/core/monitor/external_log.py`
     - `weft/core/monitor/store.py`
     - `tests/tasks/test_task_monitor.py`
     - `tests/core/test_monitor_store.py`
   - Required behavior:
     - each TaskMonitor reactor cycle attempts a small bounded flush of
       pending deferred writes when external logging is configured
     - each flush writes the stored `body_json` as the JSONL line, then marks
       the row flushed
     - failed flush attempts update cached health and retry metadata without
       deleting the deferred row
     - cached diagnostics include pending deferred count and last deferred
       write/flush error where practical
     - pruning of flushed rows is bounded and separate from write success
   - Constraints:
     - do not make flush work part of PONG/STATUS handling
     - do not hold cleanup hostage to old deferred rows if new reports can be
       durably deferred
     - do not emit a second, different report body for the same `report_id`
       during flush
   - Tests:
     - a failed external path creates a deferred row and cleanup proceeds
     - fixing the path later flushes the stored JSONL body and marks the row
       flushed
     - repeated flush failure does not drop the deferred row
     - PONG/STATUS read only cached deferred-write diagnostics

9. Replace the reserved `jsonl_then_delete` errors with the real path.
   - Outcome: built-in `jsonl_then_delete` runs the same bounded policy sequence
     as `delete`, with reporting enabled.
   - Files to touch:
     - `weft/core/monitor/task_monitor.py`
     - `tests/tasks/test_task_monitor.py`
   - Current code to remove:
     - the two explicit "reserved until the logging callback is implemented"
       result branches
   - Required behavior:
     - built-in worker path and foreground/synchronous path both honor the same
       report-then-delete semantics
     - cached PONG/status counters continue to be committed only by the
       TaskMonitor reactor after worker results
     - `TaskMonitorProcessorResult.reported` should count emitted lifetime
       reports where practical; if this creates ambiguity with old
       report-only counts, document and test the chosen meaning
   - Constraints:
     - do not create a separate processor implementation that bypasses the
       existing policy cleanup path
     - do not regress worker-local handle isolation from the previous
       TaskMonitor cleanup plan
   - Tests:
     - `jsonl_then_delete` no longer returns the reserved error
     - worker and direct paths both emit reports and delete exact candidates
     - PONG reads cached external-log status and does not emit reports

10. Documentation, audits, and release notes.
   - Outcome: docs, specs, and tests all agree on the new contract.
   - Files to touch:
     - `docs/specifications/00-Quick_Reference.md`
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/07-System_Invariants.md`
     - `docs/specifications/01-Core_Components.md` if mapping changed
     - `docs/plans/README.md`
   - Required updates:
     - add this plan to each touched spec's related plans
     - update Quick Reference processor text so `jsonl_then_delete` is no
       longer described as reserved
     - document the baseline lifetime report fields and at-least-once
       duplicate model
     - document the deferred writes table as operational outbox state and make
       clear that it is not queue truth
   - Audits:
     - `rg -n "jsonl_then_delete.*reserved|reserved until the logging callback" weft tests docs`
     - `rg -n "task_lifetime_report|report_id|source_policy" weft tests docs/specifications`
     - `rg -n "effect.*delete|selection_evidence|proof" weft tests docs/specifications`
   - Stop and re-evaluate if docs and code disagree about whether
     `WEFT_LOG_TASKS_EXTERNAL_MODE=raw` is allowed with `jsonl_then_delete`.

## Verification Plan

Run after implementation:

```bash
./.venv/bin/python -m pytest tests/core/monitor/test_lifetime_report.py -q
./.venv/bin/python -m pytest tests/core/test_monitor_store.py tests/core/test_monitor_sql.py -q
./.venv/bin/python -m pytest tests/core/monitor -q
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py -q
./.venv/bin/python -m pytest tests/core/test_task_monitoring.py tests/system/test_constants.py -q
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py tests/specs/quick_reference/test_queue_names.py -q
./.venv/bin/ruff check weft tests
./.venv/bin/mypy weft bin extensions/weft_docker extensions/weft_macos_sandbox
```

Minimum behavioral proofs:

- A normal terminal task produces one `task_lifetime_report` JSONL line with a
  top-level TaskSpec-shaped payload plus compact Monitor provenance, then exact
  task-log rows are deleted.
- A service/manager collation report preserves service classification.
- A malformed raw task-log row produces a baseline raw-row lifetime report
  before deletion.
- A dead-TID runtime cleanup produces an inferred report before deleting
  standard task-local queues.
- A runtime-state retention cleanup produces a state-only report before
  deleting an exact runtime-state row.
- External JSONL write failure with successful deferred write permits deletion
  for the selected subject and leaves one pending deferred report row.
- External JSONL write failure plus deferred write failure blocks deletion for
  the selected subject and leaves it retryable.
- A pending deferred report flushes to the external JSONL path after the path
  is fixed, then gets marked flushed without changing the stored report body.
- Delete failure after successful report can retry with the same `report_id`.
- PONG/STATUS do not emit reports and do not perform live scans.

## Review Plan

Fresh-eyes self-review is required before reporting this plan as done.

External review was recommended because this plan changes a destructive mode,
a JSONL payload contract, and multiple policy boundaries. Recommended review
prompt:

```text
Read docs/plans/2026-05-29-task-monitor-general-lifetime-reporting-plan.md,
then inspect the cited TaskMonitor code and specs. Look for errors, bad ideas,
missing fail-closed constraints, and ambiguous policy/report boundaries. Do not
implement. Answer: could you implement this confidently and correctly if asked?
```

Reviewer input paths:

- `docs/plans/2026-05-29-task-monitor-general-lifetime-reporting-plan.md`
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]
- `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.13.10],
  [OBS.13.11], [OBS.13.12], [OBS.17]
- `weft/core/monitor/task_monitor.py`
- `weft/core/monitor/external_log.py`
- `weft/core/monitor/cleanup.py`
- `weft/core/monitor/store.py`
- `weft/core/monitor/sql.py`
- `weft/core/pruning/models.py`

Record external review findings here if a later review is run.

## Fresh-Eyes Self-Review

Completed by the plan author on 2026-05-29 after drafting.

Findings:

- Review risk: this slice crosses cleanup, JSONL payload shape, config
  validation, specs, and worker/reactor behavior. Focused tests and
  self-review were run, but a separate external review is still useful before
  release.
- Ambiguity fixed: the first design was too cleanup-audit oriented. The plan now
  forbids an `effect: delete` field and centers the baseline lifetime report.
- Ordering policy fixed after user challenge: the initial fail-closed rule made
  external logging more important than monitor cleanup. The plan now allows
  cleanup after a durable Monitor-owned deferred write, and blocks cleanup only
  if both external and deferred writes fail.
- Ambiguity fixed: "each policy" could imply every private subphase emits a
  duplicate report. The plan now says top-level policies own reporting, while
  private phases use `report_kind` and should not emit duplicate lifetime
  reports for already-reported Monitor families.
- Ordering bug fixed in the plan: current retained ingest can delete valid raw
  rows before summary/report emission. The plan now explicitly requires
  `jsonl_then_delete` to defer valid raw deletion until a successful lifetime
  report marker exists.
- Residual risk: `runtime_state.retention` and malformed raw rows are weak
  lifetime reports by nature. The plan treats them as baseline observations
  with `state_only` or `raw_row` completeness rather than pretending a full
  task lifetime is known.
- Residual risk: the callback seam in `cleanup.py` must be designed carefully
  so report failure can block only the affected candidate group without
  duplicating exact-delete logic. Task 6 makes that a stop-and-re-evaluate
  gate.
- Residual risk: monitor schema versioning can turn an additive table into a
  rollback one-way door. The plan now prefers no schema-version bump for the
  deferred writes table and requires re-review if implementation cannot avoid
  one.

No other blockers found in self-review.

## Implementation Self-Review

Completed by the implementer on 2026-05-29.

Findings:

- The implemented store change keeps `WEFT_MONITOR_SCHEMA_VERSION` unchanged.
  The deferred writes table is additive and old code can ignore it.
- The destructive ordering is covered by tests for external success, external
  failure with successful deferred write, and external plus deferred failure.
- PONG/STATUS receive cached deferred-write counts through external-log status;
  they do not flush or query the deferred writes table.
- Residual risk remains around downstream JSONL consumers because
  `task_lifetime_report` is a new operational record type. Deterministic
  `report_id` is the compatibility/dedupe mechanism.

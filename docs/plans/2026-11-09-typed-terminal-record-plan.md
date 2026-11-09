# The Monitor Trusts the Typed Terminal Record

Status: draft
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-3], [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13.4]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4a]; docs/specifications/00-Quick_Reference.md (`WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS`); docs/specifications/01-Core_Components.md [CC-2.3]; docs/specifications/10-CLI_Interface.md [CLI-1.2.1]
Superseded by: none

Class 5: normative spec text changes (spec 00, 01, 04, 05, 07, 10) together
with a change to collation semantics and a Monitor schema version. Risky
triggers also fire: the persisted Monitor schema version changes (a one-way
door for older Monitors) and the change touches queue cleanup and family
retirement, so the hardening checklist applies. Plan type: implementation,
with the spec-promotion edits landed in the same commit as the code
(strategy A, in-file). Baseline: `perf/fix-terminal-idle-events` 83271b49
(parent plan: [Service and Manager Exit Records Close Their Monitor
Families](2026-11-09-terminal-idle-events-plan.md)).

## Goal

Implement the owner's 2026-11-09 decisions on that plan's open questions:

- (a) "Trust the record." The collation reducer treats a task-log row as
  terminal exactly when it is the task's typed terminal record
  (`type="terminal"` with a terminal `status`) and uses that record's own
  status, as `task_evidence.coerce_terminal_envelope` and the Manager's
  drain-stop follower already do. One shared function serves collation and
  lifetime reports. The event-name map `TERMINAL_TASK_EVENTS` is deleted
  because no remaining reader needs it.
- (b) "Bounded repair." A one-time Monitor schema migration (v6 to v7)
  closes families the v6 reducer stranded.
- (c) "Code: keep 48 h." Family retirement keeps waiting for the task-log
  retention period; the specs now say so.

## Reader-by-reader decision on the event-name map

| Reader | Use of `TERMINAL_TASK_EVENTS` | Decision |
|--------|-------------------------------|----------|
| `weft/core/monitor/collation.py::update_from_task_log_payload` | event name decided terminality and status | replaced by `task_evidence.terminal_record_status` |
| `weft/core/monitor/lifetime_report.py` (copy of the same helper) | same | replaced by the same shared function; the copy is deleted |
| `weft/core/task_evidence.py::terminal_status_from_event` | its own status-first classifier, plus a map fallback for a nonterminal status with `completed_at` | deleted (review F1): a second classifier for one responsibility. Its readers (`log_terminal_evidence`, `monitor/runtime.py::reduce_task_log_messages`, `pruning/retention.py::_read_task_log_rows`, `commands/events.py::iter_task_events`) and the `tests/helpers/task_observation.py` fixture helper call `terminal_record_status` |
| `weft/core/monitor/runtime.py::_lifecycle_conflict_reason` | `nonterminal_status_with_terminal_event` | branch deleted: its only reachable input was a Manager's legitimate `running` drain-start `control_stop` row, a false conflict |
| `weft/commands/_task_snapshot_reducer.py::_reconcile_lifecycle_status` | `contradictory_terminal_event_status` / `stale_status_payload` for old records | deleted with the `status_reason` field; it was added (2026-05-06) for "old records" only, which the no-compatibility rule excludes |
| `tests/helpers/weft_harness.py::_terminal_status_from_task_log` | event-first wait | uses `terminal_record_status` |

## Change

- `weft/core/task_evidence.py`: new `terminal_record_status(payload)`, the
  one task-log terminal classifier; `coerce_terminal_envelope` and
  `log_terminal_evidence` use it; `terminal_status_from_event` is deleted.
- `weft/core/monitor/runtime.py` (row `terminal`), `weft/core/pruning/retention.py`
  (row `terminal`), `weft/commands/events.py` (`--follow` stop): use
  `terminal_record_status`.
- `weft/core/monitor/collation.py`, `weft/core/monitor/lifetime_report.py`:
  terminality comes from `terminal_record_status`; both `_terminal_status`
  copies are deleted.
- `weft/core/monitor/runtime.py`, `weft/commands/_task_snapshot_reducer.py`,
  `weft/commands/tasks.py`: the map-dependent branches are deleted.
- `weft/_constants.py`: `TERMINAL_TASK_EVENTS` deleted;
  `WEFT_MONITOR_SCHEMA_VERSION` 6 to 7.
- `weft/core/monitor/store.py::_MonitorTableAccess.migrate_v6_to_v7` and
  `weft/core/monitor/sql.py` (`select_v6_unprocessed_open_collations`,
  `mark_v6_stranded_collation_terminal`): the repair, run by
  `MonitorStore.ensure_schema` for a v6 store and after the v5 to v6 edge.
  It follows the existing v5 to v6 pattern: version-gated, inside the schema
  transaction, version written only after success. It selects rows with
  `terminal_seen = 0` and no summary or disposition, and marks terminal those
  whose stored latest lifecycle record states a terminal status (not a
  `task_activity` hint): terminal event, status, `terminal_message_id =
  last_message_id`, `reserved_probe_needed` for non-completed. The update is
  guarded on `terminal_seen = 0`.

Why the repair infers from the stored status: every terminal status a task
publishes is in its typed terminal record (`BaseTask._report_state_change`
adds `type="terminal"` exactly when the status is terminal, and activity
hints are suppressed once terminal), so a stored latest record with a
terminal status was a typed terminal record the v6 reducer missed. A family
whose status came only from a control-terminal observation ([OBS.13.13])
keeps its lifecycle status nonterminal and is not touched.

## Proposed spec delta (promoted in this change, strategy A)

| Spec file | Sections touched |
|-----------|------------------|
| `05-Message_Flow_and_State.md` | [MF-5] task-log collation paragraph ("A row closes its lifecycle group only when ... drained terminal record arrives."); retirement sentence ("Once no child message rows remain ... keeps the TaskMonitor active."); v5 tombstone sentence gains the v6 to 7 repair sentence |
| `07-System_Invariants.md` | [OBS.13.4] retention gate and v6 to 7 repair; task-log cleanup paragraph ("Task-log terminal lifecycle proof is the task's typed terminal record ..."); retirement sentence gains the retention gate |
| `04-SimpleBroker_Integration.md` | [SB-0.4a] schema 7 statement; migration-edge paragraph rewritten for v7; new "version 6 to 7 migration" paragraph |
| `00-Quick_Reference.md` | `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS` row: summarize/dispose at high-water, retire after the period |
| `01-Core_Components.md` | [CC-2.3] collation retirement sentence gains the retention gate |
| `10-CLI_Interface.md` | [CLI-1.2.1] timestamp-classification list drops `stale_status_payload` |

The exact text is in the commit diff for those six files; every replaced
span was audited and no adjacent clause was dropped (the old "Rows with
non-terminal events such as `task_activity` ..." rule is kept as the
stronger discriminator rule).

## Observable-difference register

| Change | Class | Theory basis |
|--------|-------|--------------|
| A Manager superseded or yielding with no user work (`manager_superseded`, `manager_leadership_yielded` at `cancelled`) now closes and retires its family | C (fixes a leak) | [WT-2a.9]: the consumer agrees with the producer's record |
| A Manager's `running` drain-start row (`control_stop`, yield, supersede) no longer closes its family; the drained record does, and becomes its terminal identity and retention reference | C (fixes premature closure) | [WT-2a.3] forward-only lifecycle; [OBS.13.7] no cleanup of active work |
| `work_limit_violation` families collate and report `killed`, the status the task published, instead of `failed` | C | the record's status is the verdict ([MF-3]) |
| Lifetime reports take terminality from the same record rule | C | [WT-4.4] shared function, shared code |
| The TaskMonitor runtime candidate class `runtime_conflict` / `nonterminal_status_with_terminal_event` is gone (it fired only on a draining Manager's legitimate row) | C | removes a false diagnostic |
| Status surfaces no longer reconstruct a terminal status from the event name for a row whose status is nonterminal but has `completed_at` (`stale_status_payload`); such rows show their own status | D for pre-contract or foreign rows only; N for every row the current producers write | [WT-2a.9] / [ALT-WT-012] no compatibility code; [WT-4.4] no over-armoring: the shape is unreachable from current producers |
| Readers of a row that carries a terminal status but no `type` (not produced by Weft) no longer treat it as terminal in the Monitor and the test harness | D for foreign rows; N for Weft rows | [MF-3] defines the terminal record; same basis |
| Terminal task-log evidence (`log_terminal_evidence`), the TaskMonitor runtime reducer's row `terminal`, retention pruning's row `terminal`, and `weft events --follow` take terminality only from the typed record: an untyped row with a terminal status (or a terminal status only under `taskspec.state`) is no longer terminal there; `--follow` keeps waiting on it | D for foreign or pre-contract rows; N for every row Weft writes (`_report_state_change` adds `type` exactly when the status is terminal) | [WT-4.4] shared function, shared code; [WT-2a.9]; no compatibility code |
| Monitor schema 7; a v6 store is repaired once at startup (1,116 families on a copy of the leaked t582_a context) | C | [WT-2a.9] "a bounded forward migration for durable old data" |
| A v6-only Monitor refuses a v7 store as newer | A (owner-decided migration) | rollback step documented below |
| Terminal families keep waiting for the task-log retention period before retirement, as the code already did; specs now say so | N (code unchanged) | owner ruling 10.3 |

## Invariants and constraints

- Forward-only lifecycle ([STATE.1]): a nonterminal row never closes a family.
- Task-log terminal proof outranks control observations ([OBS.13.13]); the
  migration never turns a control-only observation into task-log proof.
- No producer, queue name, payload or Monitor table shape changes.
- No compatibility lane: the old reducer's shape is repaired once by the
  schema migration, not by a permanent normal-cycle rule.

## Tests (RED first, each against `perf/fix-terminal-idle-events` code)

| Test | Claim | Control result (pre-fix production code) |
|------|-------|------------------------------------------|
| `tests/tasks/test_task_monitor.py::test_task_monitor_retires_service_and_manager_exit_families[manager_superseded-...]` | a childless superseded Manager (real registry supersession) retires | `(False, None, None)` |
| `...::test_monitor_collation_does_not_close_a_manager_on_its_drain_start[stop-...]` (real Manager drain rows; yield and supersede params are guards) | the drain-start row is not terminal; the drained record is | `(True, 'cancelled')` for the STOP drain start |
| `...::test_task_monitor_retires_service_and_manager_exit_families[work_limit_violation-...]` (real Consumer limit outcome) | collated status is `killed` | `'failed'` |
| `...::test_task_monitor_keeps_a_stopping_manager_open_until_its_drained_record` | the family stays open during a STOP drain and closes on `manager_stop_drained` | terminal with summary during the drain |
| `...::test_task_monitor_retires_a_family_stranded_by_the_v6_reducer` | a v6-stranded family is repaired, summarized, disposed, cleaned and retired | `(False, None, None)`; also fails with the migration made a no-op |
| `tests/core/test_monitor_store.py::test_monitor_store_v6_migration_closes_families_stranded_on_their_terminal_record` | repair marks only stranded families; idempotent | fails pre-fix and with the migration a no-op; fails with the "never summarized" guard removed |
| `tests/commands/test_task_evidence.py::test_task_log_readers_take_terminality_only_from_the_typed_record` (review F1; one case per reader: `log_terminal_evidence`, monitor runtime, retention, `events --follow`) | a typed record is terminal; an untyped `work_completed`/`completed` row is not | all four fail at the untyped row (`assert True is False`) with the status-first classifier, on 7dd52a38 and with the reader change reverted |
| `tests/core/monitor/test_lifetime_report.py::test_raw_row_lifetime_report_promotes_available_taskspec` (review F2) | raw-row lifetime reports take terminality and status from the record: childless supersession `cancelled`, `work_limit_violation` `killed`, a running drain-start row open | with the 83271b49 decoder the three new cases fail: supersession `terminal_seen` False, limit `'failed'`, drain start terminal |

Synthetic task-log payloads that stand for terminal records in the monitor,
collation, lifetime-report, store and maintenance tests now carry
`type="terminal"` and `source="task"` (and the status the producer
publishes where it was missing). Payloads fed only to the custom-processor
and scan paths are unchanged. Two harness self-tests now use typed terminal
records instead of terminal-status `task_activity` rows. The status test for
the old-record reconciliation is removed with the feature; the reducer's
runtime-conflict test keeps the live-runtime case
(`terminal_lifecycle_with_live_runtime`).

## Rollback

Revert the commit. If a v7 store must be read by the reverted (v6) code,
reset the Monitor `schema_version` metadata to `{"version": 6}`; v7 data is
valid v6 data, and repaired families simply stay terminal.

## Observable success after deployment

On first start, a leaked context's stranded `service_idle_exit` families
become terminal (count equals the number of `terminal_seen=0` rows whose
lifecycle status is terminal), are summarized and disposed on the next
complete pass, and retire once older than the task-log retention period.
STOP-draining Managers show no terminal collation until their drained record.

## Out of scope

- C1, C2 and B from the parent plan.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Spec Baseline

- `83271b49` — docs/specifications/00-Quick_Reference.md, 01-Core_Components.md,
  04-SimpleBroker_Integration.md, 05-Message_Flow_and_State.md,
  07-System_Invariants.md, 10-CLI_Interface.md at plan authoring time.

## Review and execution record

Self fresh-eyes review: done after drafting; reader table re-derived from
`grep TERMINAL_TASK_EVENTS`, cited paths and section codes resolved.
Cross-family review (Tier 1, Codex read-only) of 7dd52a38, findings and
dispositions:

| ID | Finding | Disposition |
|----|---------|-------------|
| F1 (blocker) | `terminal_status_from_event` still classified terminal proof independently for events, retention, foreground monitoring and `log_terminal_evidence` | Accepted, fixed: the function is deleted; `log_terminal_evidence`, `monitor/runtime.py::reduce_task_log_messages`, `pruning/retention.py::_read_task_log_rows`, `commands/events.py::iter_task_events` and the `tests/helpers/task_observation.py` fixture helper call `terminal_record_status`; the plan's follow-up carve-out is removed and a register row added; RED test `test_task_log_readers_take_terminality_only_from_the_typed_record` added first; reader fixtures that stand for terminal records now carry the discriminator |
| F2 | the raw-row lifetime-report test only covered `work_completed`/`completed`, which the old decoder also passed | Accepted, fixed: the test is parametrized with literal expectations for childless supersession (`cancelled`), `work_limit_violation` (`killed`) and a running `control_stop` drain-start row (open) |
| F3 (nit) | `MonitorStoreAccess.migrate_v6_to_v7` does not resolve | Accepted, fixed: plan and `_constants.py` cite `_MonitorTableAccess.migrate_v6_to_v7` |

Execution evidence (2026-11-09):

- Real-data repair on a copy of `scratchpad/leak-diag/t582_a.db` (source
  verified unchanged): before, 1,116 families `terminal_seen=0`, `completed`,
  `service_idle_exit`, plus 2 running families; after the first startup,
  version 7 and the 1,116 terminal with `terminal_event=service_idle_exit`,
  `terminal_status=completed`, `terminal_message_id=last_message_id`; the 2
  running families unchanged; a second startup changed nothing.
- F1 sensitivity: with only the four reader files reverted to their
  7dd52a38 form (the status-first classifier), every case of
  `test_task_log_readers_take_terminality_only_from_the_typed_record` fails at
  the untyped row (`assert True is False`); restored byte-exact.
- F2 control: with the 83271b49 collation, lifetime-report and constants
  files, the supersession, limit and drain-start cases fail; restored
  byte-exact.
- SQLite (`-n 4`) and PostgreSQL (`./bin/pytest-pg`) gates, ruff, mypy and doc
  gates: recorded in the handoff report for each commit.

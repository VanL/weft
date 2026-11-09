# Service and Manager Exit Records Close Their Monitor Families

Status: draft
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-3], [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13.4], [OBS.13.9]; docs/specifications/00-Quick_Reference.md (`WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS`)
Superseded by: none

Class 4. The change restores existing intended behavior: [MF-3] makes every
BaseTask terminal path publish one typed terminal record, and [OBS.13.4] lets
the Monitor summarize, dispose, clean and retire a family with terminal
proof. A risky trigger fires because the change re-enables queue cleanup
(standard `ctrl_in`/inbox deletion) and family retirement for families that
were excluded. No normative spec text changes; `hardening` applies.
Baseline: `perf/admission-base` 08c2f87b.

## Goal

A service that idle-exits (`service_idle_exit`), a superseded heartbeat
(`heartbeat_service_superseded`), and a Manager that idle-exits or finishes
a drain (`manager_idle_shutdown`, `manager_stop_drained`,
`manager_leadership_drained`, `manager_superseded_drained`) each publish a
typed terminal record. The Monitor's collation reducer did not recognise any
of these six events as terminal, so their families never received summary,
disposition, control cleanup or retirement. Add the six events to the one map
the Monitor reads.

## Evidence (bug D)

- Owner: `weft/core/monitor/collation.py::_terminal_status` returns
  `TERMINAL_TASK_EVENTS.get(event)` whenever an event is present, ignoring the
  record's own `status`. The same function is duplicated in
  `weft/core/monitor/lifetime_report.py::_terminal_status`.
- Map: `weft/_constants.py::TERMINAL_TASK_EVENTS` held only the eight
  user-work events.
- A leaked context (copied database, read-only) held 1,123 collation rows and
  1,122 `T*.ctrl_out` rows (2,330,280 body bytes). Of the collation rows, 1,120
  were `terminal_seen=0`, `status=completed`, last event `service_idle_exit`
  (609 heartbeat, 511 liveness monitor).
- RED (this plan): a real heartbeat idle exit and a real Manager idle
  shutdown, ingested by a real TaskMonitor, leave the family at
  `terminal_seen=False`, `terminal_event=None` after three cycles. The
  `manager_stop_drained` case shows the family closed by the Manager's
  earlier nonterminal `control_stop` drain-start row instead of its terminal
  record.

## Producer audit

Every `_report_state_change` call (`weft/core/tasks/base.py`) that runs while
the TaskSpec status is terminal publishes `type="terminal"`. Producers were
enumerated from every `mark_completed|mark_cancelled|mark_failed|mark_killed|
mark_timeout` call under `weft/` and the event each one reports.

| Event | Status | Producer | Map before | Map after |
|-------|--------|----------|------------|-----------|
| `work_completed`, `work_failed`, `work_timeout` | completed / failed / timeout | consumer, pipeline, interactive | yes | yes |
| `work_limit_violation` | **killed** | `consumer.py` 1150, `interactive.py` 301 | `failed` | unchanged (see open question 2) |
| `control_stop`, `task_signal_stop` | cancelled | `base.py` 2253 | yes | yes |
| `control_kill`, `task_signal_kill` | killed | `base.py` 2283 | yes | yes |
| `service_idle_exit` | completed | `service.py` 348 | no | **added** |
| `heartbeat_service_superseded` | completed | `heartbeat.py` 575, 683 | no | **added** |
| `manager_idle_shutdown` | completed | `manager.py` 9524 | no | **added** |
| `manager_stop_drained` | cancelled | `manager.py` 5840 (completion of STOP drain) | no | **added** |
| `manager_leadership_drained` | cancelled | `manager.py` 5840 | no | **added** |
| `manager_superseded_drained` | cancelled | `manager.py` 5840 | no | **added** |
| `manager_leadership_yielded` | cancelled (no user children, 4799); running + `draining` (5794) | `manager.py` | no | **not added — ambiguous** |
| `manager_superseded` | cancelled (no user children, 5826); running + `draining` (5819) | `manager.py` | no | **not added — ambiguous** |

Manager-authored `wrapper_lost` envelopes go only to the child `ctrl_out`
([MF-3]) and carry no event; they are not task-log rows.

## Change

- `weft/_constants.py::TERMINAL_TASK_EVENTS`: six entries added; the
  docstring states the producer rule and names the two ambiguous names.
- No change to producers, to `_terminal_status`, or to any other consumer.

Hidden couplings (other readers of the map), checked for each added name:
`weft/core/task_evidence.py::terminal_status_from_event` and
`weft/commands/_task_snapshot_reducer.py::_reconcile_lifecycle_status` read
`status` first, so a record that already carries a terminal status is
unaffected. `weft/core/monitor/runtime.py::_lifecycle_conflict_reason` flags a
mapped event with a nonterminal status; every added name is published only
with a terminal status, so no new conflict appears.
`weft/core/monitor/lifetime_report.py::_terminal_status` gains the same fix.
`tests/helpers/weft_harness.py::_terminal_status_from_task_log` waits on user
tasks only.

## Observable-difference register

| Change | Class | Theory basis |
|--------|-------|--------------|
| Idle-exited service and Manager families gain terminal proof; summary, disposition and control cleanup run on the first complete high-water pass | C (no loss; restores [OBS.13.4]) | [WT-2a.9] one current contract: the consumer now agrees with the producer's terminal record |
| These families retire at the retention age instead of never; their `weft_monitor_task_collations` rows and summary work become bounded | C | [WT-2a.2] Monitor tables are derived state; [OBS.13.1] |
| Standard `T{tid}.ctrl_in`/inbox of those exited TIDs are deleted by the control-cleanup slice after disposition; `ctrl_out` stays retention-gated | C (restores [OBS.13.9]) | [OBS.13.9] |
| A Manager STOP family's terminal proof moves from the drain-start `control_stop` row to the `manager_stop_drained` record; `terminal_message_id` and retention age now start at the real end | C | task-owned terminal proof is the task's own final record ([MF-3]) |
| Lifetime reports for these families report `completed`/`cancelled` instead of open | C | same as row 1 |
| A TaskMonitor still counts these families as retained work until they retire at the task-log retention age (default 172,800 s), as for any terminal family | N (unchanged rule, owner ruling 10.3) | — |

## Invariants and constraints

- Forward-only lifecycle ([STATE.1]): only records already terminal become
  terminal proof; a nonterminal drain-start row must never close a family.
- No producer, queue name, payload shape or spec text changes.
- Monitor cleanup never deletes active work ([OBS.13.7]); the destruction
  protection gates are untouched.
- No compatibility lane: already-stranded rows are not migrated (open
  question 3).

## Tests

- `tests/tasks/test_task_monitor.py::test_task_monitor_retires_service_and_manager_exit_families`
  (new; owner boundary): real producers (`HeartbeatTask` idle exit and
  supersession, a scoped `Manager` idle shutdown and STOP drain) publish the
  record; a real `TaskMonitor` collates it as terminal with that event and
  status, emits the summary, disposes the family and deletes control; a
  maintenance pass past the retention window retires it and reports no
  retained work. Do not mock the producers, the store or the queues; the only
  seams are the service monotonic clock and the Manager's idle timestamp.
- `tests/core/test_monitor_collation.py::test_monitor_collation_closes_a_draining_manager_only_at_drain_completion`
  (new): the two drained records are terminal; the two drain-start rows with
  the dual-use names stay nonterminal.
- Sensitivity: with the six entries removed, all four producer cases and both
  drained cases fail (`(False, None, None)`, and `control_stop` as the STOP
  family's terminal event). With the two dual-use names added, both
  drain-start cases fail.

## Rollback

Revert the commit. The only state the fix creates is Monitor-owned
collation progress (summary, disposition, retirement) and deletion of
standard `ctrl_in`/inbox rows for exited TIDs; neither needs undoing.

## Observable success after deployment

In a context with idle services, `weft_monitor_task_collations` rows with
`terminal_seen=0` and last event `service_idle_exit` stop accumulating, and
rows older than the task-log retention period retire; the TaskMonitor's
`last_collation_terminal_tasks` is nonzero for cycles that ingest an idle
exit. The exited TIDs' standard `T{tid}.ctrl_out` stays retention-gated under
[OBS.13.9] and is also held by the TID's newest `weft.state.tasks.<tid>`
snapshot until LivenessMonitor retires it ([OBS.13.7]); in the leaked context
1,008 terminal snapshot rows were still inside that window. This plan does
not verify when those ctrl_out rows are finally deleted.

## Out of scope and open questions (owner)

1. `manager_leadership_yielded` and `manager_superseded` are published both as
   a nonterminal drain start and as the childless terminal record, so an
   event-name map cannot classify them. The childless path still strands its
   family. Candidate resolutions: (a) the collation reducer recognises the
   typed terminal record (`type == "terminal"` with a terminal `status`), as
   `task_evidence.coerce_terminal_envelope` and the Manager's drain-stop
   follower already do, and the event map leaves the Monitor; or (b) the
   childless Manager publishes a distinct terminal event name.
2. `work_limit_violation` is published with status `killed` but mapped to
   `failed`, so collations and lifetime reports disagree with the task's own
   record. Resolution (a) in item 1 also fixes this.
3. Families already stranded with `terminal_seen=0` (raw rows deleted) are not
   repaired by this fix. A bounded forward repair or an operator prune is an
   owner choice.
4. The Manager's STOP drain start publishes `control_stop` at `running`, which
   the map already treats as terminal, so a STOP-draining Manager's family is
   closed before its drain ends. Pre-existing; resolution (a) fixes it.
5. `00-Quick_Reference.md` says terminal families may be retired as soon as
   the high-water pass completes; `retire_completed_collation_families` also
   waits the task-log retention period. Pre-existing spec/code disagreement
   that sets how long a TaskMonitor stays alive.
6. Not touched: service exits resetting the Manager idle clock (C1), adopted
   service live-proof expiry (C2), children recreating a deleted context (B).

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Spec Baseline

- `08c2f87b` — docs/specifications/05-Message_Flow_and_State.md,
  docs/specifications/07-System_Invariants.md,
  docs/specifications/00-Quick_Reference.md at plan authoring time.

## Review and execution record

Self fresh-eyes review: done by the author after drafting (producer table
re-derived from the `mark_*` call list; every cited path and symbol resolved).
Cross-family review: to be filled by the coordinator.

Execution evidence (2026-11-09):

- RED on the baseline: all four producer cases fail at the terminal-proof
  assertion, `(False, None, None)` for the idle exits and the supersession,
  `(True, 'control_stop', 'cancelled')` for the STOP drain; both drained
  collation cases fail at `terminal_seen`.
- Mutation control: adding `manager_leadership_yielded` and
  `manager_superseded` to the map fails both drain-start cases.
- SQLite (`-n 4`): `tests/tasks/test_task_monitor.py`,
  `tests/core/test_monitor_collation.py`,
  `tests/core/monitor/test_lifetime_report.py`,
  `tests/core/test_monitor_store.py`, `tests/core/test_monitor_external_log.py`,
  `tests/tasks/test_service_task.py`, `tests/tasks/test_heartbeat.py`,
  `tests/specs/test_plan_metadata.py` pass; `tests/core/test_manager.py -k
  "idle or stop_drain or leadership or superseded"` passes.
- PostgreSQL (`./bin/pytest-pg`): the same files and the three
  `test_manager_idle_shutdown*` tests pass. In two combined runs one
  parametrization of
  `test_heartbeat_rejects_unsafe_destination_and_later_input_progresses`
  failed (`KeyError: 'safe'` after a 0.05 s activity wait); it passes alone
  and with its whole file. The heartbeat registration path does not read
  `TERMINAL_TASK_EVENTS`.
- Ruff check and format, the repo mypy command, `bin/check-doc-paths` and
  `git diff --check` pass.

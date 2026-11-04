# TaskMonitor: One Clock, Not Two

Status: promoted
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-3.2], [MF-5]; docs/specifications/01-Core_Components.md [CC-2.3], [CC-2.5] (unchanged, constraint); docs/specifications/03-Manager_Architecture.md [MA-1] item 7; docs/specifications/00-Quick_Reference.md (environment table, global queue table); docs/specifications/07-System_Invariants.md [QUEUE.7] (one sentence added), [IMPL.8]–[IMPL.11], [OBS.13], [MANAGER.15]–[MANAGER.18] (constraints, unchanged)
Superseded by: none

Class: 5 — the change edits normative spec text ([MF-3.2] sanctions the
two-clock arrangement today; [MF-5], [CC-2.3], [MA-1] item 7, and the Quick
Reference row describe it), so materiality requires a spec change. Risky
triggers also fire: the wake inputs of a persistent task's reactor change
(an execution-path change on the TaskMonitor reactor), and the TaskMonitor
PONG payload loses its `heartbeat` block (a payload contract). So the
hardening-plans checklist applies and independent review precedes
implementation. The on-demand heartbeat (owner ruling on fork F1,
2026-11-02; Tasks 6–8) adds further triggers: a new queue name
(`weft.state.heartbeats`, a contract element), a new persistence and cleanup
lifecycle (registrations now survive restarts and each carries its own
registrant-declared lease, after which the heartbeat service deletes it;
owner ruling on fork F5, 2026-11-03), a changed Manager service-supervision
desire predicate and wake input (an execution-path change on the Manager
reactor), and a changed helper signature (`upsert_heartbeat` /
`cancel_heartbeat`). Class stays 5. Plan type: implementation with spec
revision. Promotion strategy: **B (atomic)** for 05, 01, 03, and 07; **D**
for 00 (rationale under `## Proposed Spec Delta`).

Series position: plan 6 ("heartbeat clock consolidation") of the 2026-11
reconciliation series named by audit row 4.3-a in
`docs/plans/2026-11-02-program-theory-reconciliation-plan.md`.

Relation to an older draft: `docs/plans/2026-09-08-service-wake-model-and-heartbeat-decoupling-plan.md`
(status `draft`, never executed) covers this change and more. This plan
takes its TaskMonitor slice and, since the owner's F1 ruling of 2026-11-02,
its launch option L3 (on-demand launch), in the concrete shape the owner
accepted (Tasks 6–8). It does **not** adopt that draft's other launch options
(L2/L2f and the `WEFT_HEARTBEAT_SERVICE_ENABLED` flag), its interval-floor
change (its Q2), or its new [CC-2.5] wake-model paragraph. Those stay with
that draft as open owner decisions (see `## 9. Owner forks`). It does take, as fork F2's default, that draft's Task 4 cadence
shape (a local grid anchored at the first cycle start with one owed
follow-up after an overrun), because finish-anchoring is a diminution
(register rows 3, 3a, and 4). This plan does not edit the older draft; the
owner decides whether to narrow or supersede it (fork F3).

## 1. Goal

`TaskMonitor` has two clocks. It registers a periodic message with the
built-in heartbeat service (`TaskMonitor._ensure_heartbeat_registered`,
which calls `weft.core.heartbeat.upsert_heartbeat` from the reactor turn),
and it also keeps its own cycle deadline (`_next_cycle_due_monotonic`,
published through `TaskMonitor.next_wait_timeout()`). While the
registration is failing, a third timer appears: a 1.0 s registration retry
deadline (`TASK_MONITOR_HEARTBEAT_REGISTRATION_RETRY_SECONDS`) that wakes
the reactor and runs a turn even though nothing happened. [MF-3.2] makes
the arrangement normative. The owner decided on 2026-09-08 (recorded in
[ALT-WT-004]) that each service owns its clock through
`BaseTask.next_wait_timeout()` and that the heartbeat service is an emitter
for consumers that want a periodic message, not the clock of another
service. This plan removes the TaskMonitor heartbeat registration, the
retry timer, and every heartbeat-driven wake. It moves today's
heartbeat-live cadence onto the Monitor's own deadline (a local grid
anchored at the first cycle start that inbox pokes do not move, with one
owed follow-up after a cycle that overran a tick; fork F2's default),
and brings the spec into line. The move preserves the fixed-rate period and
poke independence; the grid phase is anchored at the first cycle start
rather than at registration ingestion (register row 3b).

With TaskMonitor gone, the heartbeat service has no registrant, yet the
Manager would still run it whenever TaskMonitor is enabled. The owner ruled
on fork F1 (2026-11-02) that the heartbeat runs on demand. Tasks 6–8 make
registration a row on a fixed runtime-state queue, `weft.state.heartbeats`
(one row per live registration, keyed by `heartbeat_id`, carrying the
registrant's TID and a registrant-declared lease, `lease_seconds`; upsert
replaces the row and its lease, cancel deletes). `upsert_heartbeat` and
`cancel_heartbeat` write the row and return; they do no endpoint lookup, no
`ensure_manager`, and no poll. The Manager desires the heartbeat service if
and only if `weft.state.heartbeats` holds a row that is unexpired or not yet
rejected ([MF-3.2]), and holds a notification interest in it, so a first
registration wakes it. The heartbeat task builds its table by
peeking the queue, never consuming a valid row, so registrations survive
heartbeat and Manager restarts with no re-registration and no retry timer.
With an empty table the heartbeat exits on its existing idle timeout and is
not restarted. Every row expires `lease_seconds` after its own broker
message timestamp, which is the write instant in the normal case (owner
ruling on fork F5, 2026-11-03; §4 hidden couplings, "Clock skew"): the
heartbeat deletes it at that deadline and emits nothing for it in any turn
whose rebuild found it expired, so a registrant that dies without cancelling
is emitted to until its lease ends and no longer (Task 7). A registrant that
wants the heartbeat to go on resubmits, whenever it likes; there is no
renewal protocol. No reader infers a registrant's death from absent
evidence.

Acceptance test (the theory sentence, [WT-2] item 4 and [WT-4] 4.3 "A
quiet task does nothing"): "If no input arrives and no timer is due, a
quiet task runs zero turns." The proof is a counted test: zero TaskMonitor
reactor turns over a fixed quiet interval while a live heartbeat service is
emitting, and zero while no heartbeat service exists.

## 2. Source Documents

Governing spec text at `b7eed9f9` (quoted exactly under
`## Proposed Spec Delta`):

- `docs/specifications/05-Message_Flow_and_State.md` [MF-3.2] lines 496–506
  (the TaskMonitor registration bullet and the PONG bullet) and the
  `_Implementation mapping_` at 508–511; [MF-5] line 548 (flow line) and
  lines 717–720 (reactor availability sentence); the catch-up sentence at
  lines 1999–2001 under `### Current Queue Lifecycle Management` (no section
  code).
- `docs/specifications/01-Core_Components.md` [CC-2.3] lines 643–644 and
  664–666. [CC-2.5] is a constraint only: its `_Implementation mapping_`
  already says BaseTask alone evaluates `next_wait_timeout()`; no edit.
- `docs/specifications/03-Manager_Architecture.md` [MA-1] item 7 lines
  289–291 ("The built-in heartbeat service is desired only when an enabled
  internal dependent needs it.").
- `docs/specifications/00-Quick_Reference.md` line 196
  (`WEFT_TASK_MONITOR_INTERVAL_SECONDS` row).
- `docs/specifications/07-System_Invariants.md` [QUEUE.7] lines 181–182
  (one sentence added for the two standing notification interests in
  `weft.state.heartbeats`; Tasks 7–8). Otherwise checked; no edit. A grep
  for `heartbeat` (case-insensitive) over the [OBS.13] block (lines
  484–664) returns nothing, and over the [MANAGER.1]–[MANAGER.18] block
  (lines 993–1200) returns only line 1015 (manager registry heartbeats); no
  sentence is about heartbeat-driven Monitor work (`## Execution log`, E7).
  [OBS.13.10] already states that self-maintenance runs "on a monotonic
  deadline"; that stays true.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-3.2] lines 443–495
  (flow diagram and the registration, helper, reactor, duplicate, and
  destination rules) and the mapping note at 512–516.
- `docs/specifications/00-Quick_Reference.md` global queue table (lines
  31–42; one row added).

Theory (frames intent; does not override the specs):

- `docs/program-theory.md` [WT-2] item 4 (single-arbiter reactor; "a quiet
  task runs zero turns"), [WT-2a.6] (one arbiter per reactor; reactors
  compose by messages), [WT-4] 4.3 rows "One reactor, one arbiter, a closed
  set of inputs" and "A quiet task does nothing", [WT-7] "Event-driven
  against absence" ("Each new timer must answer: which event cannot arrive,
  and why?" — the 1.0 s retry timer has no answer), [ALT-WT-004] (a
  heartbeat as the TaskMonitor clock: rejected; "Current consequence: The
  decision is made. The implementation is not."), [ALT-WT-005] (a
  maintenance worker gets explicit inputs, not a task copy — this plan adds
  no worker input), [ALT-WT-020] (MonitorStore stays private).
- For Tasks 6–8: [WT-2a.2] (registrations become queue rows, the durable
  truth; the heartbeat's heap is a hint rebuilt from them), [WT-2a.11] (any
  process in the trust domain may write the registrations queue; validation
  checks shape, not authority), [WT-2a.13] (death needs proof: no reader
  infers a registrant's death from absent evidence; the lease is the
  registrant's own statement of how long its request lasts), [WT-4] "It is just a message" (registration is
  an ordinary row on an ordinary queue), "Address a fact to its observers"
  (the Manager and the heartbeat watch the one queue the fact lands on),
  "A quiet task does nothing" (with no registration, no heartbeat process
  exists), [ALT-WT-002] (no poll or cap replaces the removed registry poll:
  both readers wake on a notification interest), [ALT-WT-010] (no registry
  subsystem: one runtime-state queue read through existing primitives),
  [ALT-WT-019] (a registration row is an active leased request, consumed
  when it is cancelled or expires; it is not an accumulated history that
  needs tombstones or a garbage-collection sweep; see §4).
- Audit row 4.3-a and candidate check 3 "Two-clock heartbeat. Confirmed,
  drifted (plan 6)" in
  `docs/plans/2026-11-02-program-theory-reconciliation-plan.md`.

Historical plans (non-normative evidence only):
`docs/plans/2026-09-08-service-wake-model-and-heartbeat-decoupling-plan.md`
(draft; its §3 "Current structure" and Observable-Difference Register were
written at `178e3a34`, and line numbers there are stale). Its Task 3 tests
1–3b and Task 4 items 4–6 are the source of the F2 default's shape here;
this plan restates what it needs and cites them as evidence only.

Repository guidance the implementer applies: `CLAUDE.md` §1.1, §4, §5.1,
§7; `docs/agent-context/engineering-principles.md`;
`docs/agent-context/runbooks/writing-plans.md`;
`docs/agent-context/runbooks/hardening-plans.md`;
`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md` and
`skills/call-agent/SKILL.md` (cross-family review).

## 3. Context and Key Files

### Current structure at `b7eed9f9`

All line numbers are at `b7eed9f9`; re-locate by symbol, not by number.

**Shared wait path (unchanged).** `weft/core/tasks/base.py`:
`BaseTask.run_until_stopped` → `_drive_reactor_until` calls
`process_once()`, then `wait_for_activity(timeout=...)` with the timeout
from the task's `next_wait_timeout()`. `wait_for_activity` hands the wait to
`MultiQueueWatcher` (`weft/core/tasks/multiqueue_watcher.py::_wait_for_activity_body`),
which confirms backend hints against the task's own queues before
returning, so a write to a foreign queue does not run a turn.

**TaskMonitor — the two clocks** (`weft/core/monitor/task_monitor.py`):

- Imports: line 84 `from weft.core.heartbeat import cancel_heartbeat,
  upsert_heartbeat`; lines 60–61 import
  `TASK_MONITOR_HEARTBEAT_REGISTRATION_RETRY_SECONDS` and
  `TASK_MONITOR_HEARTBEAT_STARTUP_TIMEOUT_SECONDS`.
- Fields in `__init__`: lines 4531–4534 (`_heartbeat_registered`,
  `_heartbeat_error`, `_heartbeat_id`,
  `_next_heartbeat_registration_attempt_monotonic`); line 4535
  `_next_cycle_due_monotonic = 0.0` (kept).
- `next_wait_timeout` (line 4949): docstring line 4952 says
  "heartbeat-driven schedule"; lines 4966–4967 add the registration retry
  deadline while unregistered (the second timer); line 4976 adds the cycle
  deadline (the clock this plan keeps).
- `_process_reactor_turn` (line 5009): line 5032 calls
  `_ensure_heartbeat_registered()` on every enabled turn.
- `_handle_work_message` (line 5067): any inbox row sets `_wake_requested`
  (kept; a heartbeat emission reaches the Monitor only through this path).
- `_handle_control_command` (line 5076): exists only to call
  `_cancel_heartbeat()` on STOP/KILL before delegating to
  `BaseTask._handle_control_command`.
- `_task_monitor_pong_extension` (line 5222): computes
  `next_registration_attempt` (lines 5226–5229) and returns the
  `"heartbeat"` block (lines 5258–5263).
- `_ensure_heartbeat_registered` (line 5714) blocks the reactor thread in
  `upsert_heartbeat`, which calls `ensure_heartbeat_service`
  (`weft/core/heartbeat.py` line 200). When no live `_weft.heartbeat`
  endpoint exists, that helper calls `manager_runtime.ensure_manager()`
  (which may start a manager process under its own startup bound) and then
  polls for up to `TASK_MONITOR_HEARTBEAT_STARTUP_TIMEOUT_SECONDS` (0.5 s).
  On failure it writes `_heartbeat_error` into `_last_error` and arms the
  1.0 s retry.
- `_cancel_heartbeat` (line 5746).
- `_finish_monitor_cycle` (line 5956): on success sets `_last_error =
  self._heartbeat_error` (lines 5975 and 5995); re-arms
  `_next_cycle_due_monotonic = time.monotonic() + next_interval_seconds`
  (line 5989), where `next_interval_seconds` is the catch-up interval when
  `progress_requires_catchup` (`weft/core/monitor/progress.py` line 100)
  reports backlog, otherwise `interval_seconds`.
- `_handle_control_cleanup_worker_result` (line 6267): `_last_error =
  self._heartbeat_error` at line 6377; second identical re-arm at line
  6394.
- Not registration code, kept: lines 43 and 46 (imports of
  `INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT`, `INTERNAL_SERVICE_KEY_HEARTBEAT`)
  and lines 2860–2863, which classify heartbeat-service task-log families
  for cleanup.

Counts (command output, `## Execution log` E1–E3): `_heartbeat_registered`
occurs 10 times in `task_monitor.py`; 44 lines in that file mention
`heartbeat` (case-insensitive), of which 5 survive this plan (lines 43, 46,
2860, 2861, 2863); `upsert_heartbeat` is called from production code at
exactly one site (`task_monitor.py` line 5722).

**Maintenance cadence — the Monitor's own clock (interval values kept
unchanged; the anchor moves to a local grid under F2's default).**
`weft/_constants.py`: `WEFT_TASK_MONITOR_INTERVAL_SECONDS_DEFAULT` = 300
(line 1080; docstring line 1081 still says "Default heartbeat wake
interval"); `WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS_DEFAULT` = 2.0
(line 1083); `WEFT_TASK_MONITOR_MAINTENANCE_INTERVAL_SECONDS` = 3600.0
(line 1200, "A wall-clock deadline, not a cycle count");
`HEARTBEAT_MIN_INTERVAL_SECONDS` = 60 (line 1634), still the floor enforced
by `_parse_task_monitor_interval_seconds` (line 2313; docstring "Parse the
task-monitor heartbeat interval") and by `weft/core/monitor/runtime.py`
line 228; `TASK_MONITOR_HEARTBEAT_REGISTRATION_RETRY_SECONDS` (line 1331)
and `TASK_MONITOR_HEARTBEAT_STARTUP_TIMEOUT_SECONDS` (line 1334), used only
by `task_monitor.py` and imported by `tests/tasks/test_task_monitor.py`
line 55. The policy runner that picks the next interval is
`progress_requires_catchup` in `weft/core/monitor/progress.py`.
Self-maintenance is `TaskMonitor._maybe_run_maintenance_pass` (line 4104):
it runs inside the built-in cycle worker lane and checks its own monotonic
deadline (`_maintenance_state.next_maintenance_due_monotonic`) through
`_monitor_monotonic()`. So maintenance rides on cycles, and cycles ride on
`_next_cycle_due_monotonic`; neither depends on the heartbeat once the
registration is gone.

**Heartbeat service and its consumers (unchanged by Tasks 2–5; changed by
Tasks 6–8, see the next two paragraphs).** `weft/core/heartbeat.py`
(`ensure_heartbeat_service`, `upsert_heartbeat`, `cancel_heartbeat`;
private per [PY-1], no public re-export) and
`weft/core/tasks/heartbeat.py::HeartbeatTask`. Consumers found by grep
(`## Execution log` E4–E6):

- production registrants: one, `TaskMonitor` (`task_monitor.py` line 5722
  upsert; line 5751 cancel). After this plan: **none**.
- in-repo callers of the helpers outside production: tests only —
  `tests/core/test_heartbeat_helpers.py`,
  `tests/core/test_service_helper_connections.py` (helper contract tests),
  plus heartbeat-service tests in `tests/tasks/test_heartbeat.py`.
- `weft/client`, `weft/ext.py`, `weft/cli`, `extensions/`, `integrations/`: no
  heartbeat reference. `weft/commands/system.py` and
  `weft/commands/_task_snapshot_reducer.py` only classify the service for
  status.
- sibling repositories under `/Users/van/Developer` (engram, taut, mm,
  simplebroker, and the rest): no reference to `upsert_heartbeat`,
  `_weft.heartbeat`, or `weft.core.heartbeat`.

YAGNI consequence: no consumer remains. Tasks 2–5 change nothing in the
heartbeat service beyond removing the Monitor's registration. Tasks 6–8
then change its registration path and launch policy as the owner ruled on
F1 (2026-11-02), and nothing else: emission, coalescing, the 60 s minimum,
destination validation rules, the `_weft.heartbeat` endpoint claim, and
singleton supersession stay as they are.

**Heartbeat registration and launch today (verified, `## Execution log`
E13–E18 and E20; changed by Tasks 6–8).**

- `HeartbeatTask._process_reactor_turn` (`weft/core/tasks/heartbeat.py` line
  150) runs, in order: worker results, one `ctrl_in` row, one inbox row
  (`_drain_one_registration_message`, line 196: `move_one` inbox → reserved,
  then `_handle_work_message`, line 217), supersession check, due emission,
  idle shutdown. An inbox row is a `HeartbeatMutation` (`action` `upsert` or
  `cancel`, `heartbeat_id`, `interval_seconds`, `destination_queue`,
  `message`; `extra="forbid"`). Upsert stores an in-memory
  `HeartbeatRegistration` with `next_due_at = monotonic() + interval` (line
  254) and acks the reserved row; the registration exists only in process
  memory.
- Idle exit (owner-flagged check 1) keys on an **empty table**, not on inbox
  silence: `_maybe_idle_shutdown` (line 471) returns `False` whenever
  `self._registrations` is non-empty; `_empty_since_monotonic` is set at
  construction (line 128) and when a cancel empties the table (236–237), and
  cleared by an upsert (261). The default `HEARTBEAT_IDLE_TIMEOUT_SECONDS` is
  60.0 (`weft/_constants.py` line 1637), but the Manager spawns the service
  with `heartbeat_idle_timeout: 0.0` (`weft/core/manager.py` line 5945),
  which disables idle exit; `tests/core/test_manager.py` line 2061 pins that.
- `HeartbeatTask.next_wait_timeout` (line 493) always includes
  `_next_ownership_audit_at - now`, re-armed every turn to
  `HEARTBEAT_OWNERSHIP_AUDIT_INTERVAL_SECONDS` = 1.0 (constants line 1640).
  So a live heartbeat process runs a turn about once a second even with
  zero registrations (code reading, not measured). Not changed here (out of
  scope); after Tasks 6–8 that cost exists only while a registration exists.
- `weft/core/heartbeat.py`: `upsert_heartbeat` and `cancel_heartbeat` (lines
  236 and 265; cancel too) both call `ensure_heartbeat_service` (line 200),
  which resolves `_weft.heartbeat`, proves liveness (a full scan of
  `weft.log.tasks` in `_latest_heartbeat_task_status`, the runtime handle,
  else a 0.25 s keyed PING), and on failure calls
  `manager_runtime.ensure_manager()` and then sleeps in
  `MANAGER_REGISTRY_POLL_INTERVAL` steps up to `startup_timeout`. Callers
  (E15): production, only `task_monitor.py` (removed by Task 3); tests,
  `tests/core/test_heartbeat_helpers.py`,
  `tests/core/test_service_helper_connections.py`,
  `tests/core/test_manager_runtime_connections.py` line 200, and the Task 2
  stand-ins. After Task 3 no caller needs `ensure_heartbeat_service`, so Task 6
  deletes it rather than reducing it to an event-driven wait.
- Dead registrant (owner-flagged check 2): today **no path retires it**. A
  registration lives in heartbeat memory until a cancel or a heartbeat exit,
  and the manager-spawned heartbeat never idles out, so a registrant that dies
  without cancelling is emitted to until the heartbeat restarts.
  `LivenessMonitor` deletes only `weft.state.tasks.<tid>` (sole deleter,
  [LIVENESS.R3]); widening it would mix custody. The runtime-state sweep
  (`weft/core/pruning/runtime.py::run_runtime_prune_for_context`, run by
  TaskMonitor self-maintenance and by `weft system prune --family
  runtime-state`) cannot reach a new queue without a new queue group, and its
  owner-TID rule decides from registrant lifecycle evidence. The
  registrant-declared lease (owner ruling on fork F5, 2026-11-03) makes that
  engine unnecessary here: each row states its own lifetime, the heartbeat
  deletes the row at its expiry deadline (Task 7), and no reader consults the
  registrant's liveness. The runtime-state sweep stays untouched.

**Manager wake inputs and a trap (verified, E16).** The Manager's delivery
queues are its inbox, `ctrl_in`, `reserved`, and the internal pair
(`Manager._build_queue_configs`, line 1161). Service convergence runs on its
own clock: `MANAGED_SERVICE_CONVERGENCE_INTERVAL_SECONDS` = 1.0 while
active, `MANAGED_SERVICE_STABLE_AUDIT_INTERVAL_SECONDS` = 5.0 when stable
(constants lines 1340 and 1343; `Manager.next_wait_timeout`, line 8158). The
trap: a PEEK delivery config on a queue whose rows stay put would spin the
reactor, because `MultiQueueWatcher._has_pending_messages` returns `True`
whenever any counted queue has a row and the wait then returns at once. The
existing safe shape is a notification-only interest:
`Manager._activity_wait_queues` (adds startup task-state queues without a
delivery route) and `Manager._has_pending_observation` (line 1897; a strict
`after_timestamp` publication cursor), governed by [QUEUE.7], which today
names only the startup-feedback interest. `BaseTask` delivery topology is
construction-fixed ([QUEUE.7] rejects runtime `add_queue`), so "add the queue
to the watcher" means this notification interest, never a delivery config.
SimpleBroker supplies `Queue.has_pending(after_timestamp=...)` and
`Queue.write()` returning the new message ID.

**Manager supervision (changed by Task 8).** The heartbeat desire gate is
`self._task_monitor_enabled` at three sites:
`weft/core/manager.py::_reconcile_managed_services` lines 7153–7157 (the
comment says "TaskMonitor is the only internal heartbeat dependent"),
`_pending_service_probes_for_scope` lines 7760–7766, and
`_managed_service_convergence_active_reasons` (line 7788; gate at lines
7822–7829, `missing_active_tid`). `weft/commands/system.py::_service_enabled`
(docstring lines 897–899, gate line 908) mirrors it. The manager spawns the
service with `heartbeat_idle_timeout: 0.0` (`_build_heartbeat_spawn_payload`,
line 5945), which disables idle exit. `_heartbeat_service_spec` (line 6025)
sets no `restart_backoff_ns` (0), unlike TaskMonitor and LivenessMonitor,
which use `_task_monitor_restart_backoff_ns`. Fork F1 is closed by the owner
(2026-11-02): Task 8 replaces all three gates with "`weft.state.heartbeats`
holds a row that is unexpired or not yet rejected ([MF-3.2])" and removes the idle-timeout
override.

**Manager seeding of the Monitor (unchanged).**
`Manager._build_task_monitor_spawn_payload` (line 5958) puts an
`inbox_message` `{"type": "task_monitor_wakeup", "reason":
"manager_supervision"}` on the Monitor's inbox at launch. That is an
ordinary inbox poke and keeps working.

**Tests that pin today's behavior** (`tests/tasks/test_task_monitor.py`,
module marker `shared`, so it runs on SQLite and on PostgreSQL through
`bin/pytest-pg`):

- 119 lines contain `"upsert_heartbeat"`; every one is a
  `monkeypatch.setattr(task_monitor_mod, "upsert_heartbeat", ...)` target
  (first at line 1063; E3). Once the module attribute is gone, each of these
  raises `AttributeError` (pytest's default `raising=True`), so they all go
  in the same change as the import removal.
- `test_task_monitor_stop_survives_blocking_heartbeat_probe` (line 1004):
  its subject is the blocking registration probe on the reactor thread;
  it also patches `_cancel_heartbeat` and calls
  `_ensure_heartbeat_registered` directly (lines 1066–1070).
- `test_task_monitor_disabled_has_no_private_timer_and_still_wakes`
  (line 3060): line 3072 patches `upsert_heartbeat` with a `pytest.fail`
  guard ("disabled monitor must not heartbeat").
- `test_task_monitor_ping_includes_health_and_preserves_task_log`
  (line 3104): asserts `extended["heartbeat"] == {...}` at line 3181.
- `test_task_monitor_next_wait_timeout_publishes_cycle_deadline`
  (line 2988) and `test_task_monitor_pending_wakeup_uses_shared_reactor_wait`
  (line 3020): pin the 59–60 s cycle deadline and the inbox-poke
  wake. Kept, minus their upsert patch.
- `test_task_monitor_heartbeat_failure_records_health_but_still_cycles`
  (line 10118): pins `_last_error == "heartbeat registration failed: ..."`
  and a wait timeout `<= TASK_MONITOR_HEARTBEAT_REGISTRATION_RETRY_SECONDS`
  (line 10157). Its subject is the retired behavior.
- `test_task_monitor_maintenance_vacuums_claimed_rows_on_monotonic_deadline`
  (line 11633): pins self-maintenance on its deadline, but forces each cycle
  by writing `task._next_cycle_due_monotonic = 0.0`, so it does not prove
  that the cycle deadline fires by itself.
- Helpers to reuse: `recording_processor` (line 2215),
  `drive_task_monitor_until_idle` (line 2260),
  `drive_task_monitor_until_observed` (line 2478), `_read_status_reply`
  (line 11513), the autouse `clear_processor_requests` fixture (line 2568),
  and the fake-clock pattern at line 8351 (`monkeypatch.setattr(task_monitor_mod,
  "time", SimpleNamespace(monotonic=..., time_ns=time.time_ns,
  sleep=time.sleep))`) together with the `_monitor_monotonic` patch at
  line 11651.
- Heartbeat fixtures to reuse: `make_heartbeat_taskspec(tid, root)`
  (`tests/tasks/test_heartbeat.py` line 50; already imported by
  `tests/core/test_service_helper_connections.py`) and the forced-due
  pattern in `test_heartbeat_late_wake_coalesces_to_one_emit`
  (`tests/tasks/test_heartbeat.py` line 284: set `registration.next_due_at`
  in the past, clear `_due_heap`, push the entry, call `process_once()`).
- Counted-turn precedent: `tests/tasks/test_task_execution.py`
  `ReactorTestTask.reactor_turn_count` (line 148; incremented in the
  `_process_reactor_turn` override at lines 154–156) and its quiet-window
  test `test_idle_ordinary_task_runs_no_policy_turns_without_events`
  (lines 638–678).
- Thread-exception guard: the `thread_exception_guard` fixture
  (`tests/conftest.py` line 33) fails the test at teardown when a background
  drive thread died with an uncaught exception.

**Measured before-state** (scratch probe, not a repository file; SQLite,
`b7eed9f9`, `## Execution log` E9): over a 2.5 s quiet window after the
first cycle settled, a custom-mode TaskMonitor with interval 60 ran **0**
turns when its registration succeeded and no emission fell in the window,
and **2** turns when registration failed (the 1.0 s retry timer). The
emitting case and PostgreSQL are measured by Task 2's RED run.

### Files to modify

- `weft/core/monitor/task_monitor.py`
- `weft/_constants.py` (delete two constants; docstring edits on two
  others)
- `tests/tasks/test_task_monitor.py`
- Tasks 6–8 (on-demand heartbeat): `weft/_constants.py` (one queue
  constant, `WEFT_HEARTBEAT_REGISTRATIONS_QUEUE`, and one validation bound,
  `HEARTBEAT_MAX_LEASE_SECONDS`),
  `weft/core/heartbeat.py` (rewritten write-and-forget; dead helpers
  deleted), `weft/core/tasks/heartbeat.py` (peek-built table, notification
  interest, inbox rejection), `weft/core/manager.py` (desire predicate at three
  sites, notification interest, spawn payload, comment),
  `weft/commands/system.py` (`_service_enabled` predicate and docstring),
  `tests/core/test_heartbeat_helpers.py`, `tests/tasks/test_heartbeat.py`,
  `tests/core/test_service_helper_connections.py`,
  `tests/core/test_manager_runtime_connections.py`,
  `tests/core/test_manager.py`, `tests/commands/test_status.py`
- `docs/specifications/05-Message_Flow_and_State.md`,
  `docs/specifications/01-Core_Components.md`,
  `docs/specifications/03-Manager_Architecture.md`,
  `docs/specifications/00-Quick_Reference.md`,
  `docs/specifications/07-System_Invariants.md` (deltas and `## Related
  Plans` backlinks)
- `docs/program-theory.md` ([ALT-WT-004] Kind, Current consequence, and
  Evidence lines; Task 5)
- `CHANGELOG.md` (`## [Unreleased]`)

**Not modified:** `weft/core/manager_services.py`,
`weft/core/monitor/runtime.py`, `weft/core/monitor/store.py`,
`weft/core/monitor/policies/`, `weft/core/tasks/liveness_monitor.py`,
`weft/core/tasks/multiqueue_watcher.py`, `weft/core/tasks/base.py`,
`weft/core/pruning/runtime.py`, `weft/cli/app.py`, `weft/commands/prune.py`
(no runtime prune group is added; the registrant-declared lease replaces
one),
`tests/helpers/weft_harness.py`, `tests/specs/test_reactor_architecture.py`,
`README.md`.

### Read first

1. `docs/program-theory.md` [WT-2] item 4, [WT-2a.6], [WT-4] 4.3, [WT-7]
   "Event-driven against absence", [ALT-WT-004], [ALT-WT-020].
2. `docs/specifications/05-Message_Flow_and_State.md` [MF-3.2] and the
   TaskMonitor bullets of [MF-5].
3. `docs/specifications/01-Core_Components.md` [CC-2.2.1], [CC-2.3]
   (TaskMonitor paragraph), [CC-2.5].
4. `docs/specifications/07-System_Invariants.md` [IMPL.8]–[IMPL.11],
   [OBS.13.10], [OBS.13.11], [MANAGER.15]–[MANAGER.17].
5. `weft/core/tasks/base.py` `run_until_stopped`, `_drive_reactor_until`,
   `next_wait_timeout`, `process_once`, `wait_for_activity`.
6. `weft/core/monitor/task_monitor.py`: the methods named above.
7. `weft/core/heartbeat.py` lines 200–285.
8. `tests/tasks/test_task_monitor.py` helpers named above;
   `tests/tasks/test_heartbeat.py` lines 50–70 and 284–322.
9. For Tasks 6–8: `docs/program-theory.md` [WT-2a.2], [WT-2a.11],
   [WT-2a.13], [WT-4]
   rows "It is just a message", "Address a fact to its observers", "A quiet
   task does nothing", [ALT-WT-002], [ALT-WT-010], [ALT-WT-019];
   `docs/specifications/07-System_Invariants.md` [QUEUE.7];
   `docs/specifications/03-Manager_Architecture.md` [MA-1] items 7 and 8;
   `weft/core/tasks/heartbeat.py` (whole file);
   `weft/core/manager.py::Manager._activity_wait_queues`,
   `Manager._has_pending_observation`, `_process_manager_reactor_turn`,
   `_run_managed_service_convergence`, and the three gate sites;
   the installed `simplebroker/_timestamp.py` `TimestampGenerator`
   docstring (the message-ID shape the lease arithmetic relies on; E20);
   the retirement decisions of 2026-09-04 (every retirable record keeps a
   named rule, no new mechanism; malformed rows are deleted and reported),
   which the lease satisfies: a registration's named rule is its own
   lease.

### Comprehension questions (answer in the execution log before the first edit)

1. After this change, which inputs can wake the TaskMonitor reactor, and
   which code sets the only timer it publishes?
   Expected answer: rows on its own inbox and `ctrl_in` (through the shared
   `MultiQueueWatcher` wait), local worker-result notifications from its
   registered worker groups, and the one deadline returned by
   `TaskMonitor.next_wait_timeout()`, which is `_next_cycle_due_monotonic`
   (0.0 while the first cycle is pending, a wake is requested, or worker
   results are pending). Under F2's default that deadline is set by
   `TaskMonitor._arm_next_cycle_deadline`, called from
   `_finish_monitor_cycle` and from `_handle_control_cleanup_worker_result`:
   the next grid tick (anchored at the first cycle start, advanced at each
   cycle start), or now when an overrun follow-up is owed, capped by the
   catch-up interval while `progress_requires_catchup` reports backlog.
   Self-maintenance has its own monotonic deadline but is checked only
   inside a cycle, so it adds no wake.
2. Why does the heartbeat process keep running after the Monitor stops
   registering, and what would stop it?
   Expected answer: `Manager._reconcile_managed_services` still desires the
   heartbeat service whenever `self._task_monitor_enabled` is true (launch
   policy, not a dependency), and the manager's spawn envelope sets
   `heartbeat_idle_timeout: 0.0`, so the service never idles out.
   `WEFT_TASK_MONITOR_ENABLED=0` removes the launch desire: a manager
   configured that way neither launches nor restarts the heartbeat service.
   That is launch/restart policy, not termination:
   `_reconcile_managed_services` does not send STOP to a live service merely
   because the service is omitted from the desired set, so an
   already-running heartbeat process keeps running until something stops it
   explicitly (STOP or KILL on its control queue) or it exits on
   supersession. (This is the state after Task 3 and before Task 8; the
   owner's F1 ruling replaces it. Answer it as the starting point for
   question 3.)
3. After Tasks 6–8, a process writes the first heartbeat registration while a
   canonical Manager runs and no heartbeat process exists. Trace what wakes,
   what launches, and what the registrant waits for; then say what happens
   when the registrant dies without cancelling, and when the last row is
   deleted.
   Expected answer: `upsert_heartbeat` validates, writes one row to
   `weft.state.heartbeats`, deletes older rows with the same `heartbeat_id`,
   and returns; it waits for nothing. The row wakes the Manager through its
   notification-only interest (`Manager._activity_wait_queues` plus the strict
   cursor in `Manager._has_pending_observation`), not through a delivery
   config; the next turn forces one service-convergence pass, whose desire
   predicate (`weft.state.heartbeats` holds a row that is unexpired or not
   yet rejected, [MF-3.2]) adds the
   heartbeat service and enqueues its
   spawn on `weft.spawn.internal`. The heartbeat starts, peeks the queue, and
   first emits one interval after the build that found the row. A dead
   registrant's row stays, and keeps the service desired and emitting, until
   its lease ends: `lease_seconds` after the row's own message ID (a hybrid
   timestamp whose high bits are nanoseconds since the epoch), a deadline the
   heartbeat publishes through `next_wait_timeout()`. The turn at or after
   that deadline rebuilds the table; the build takes the newest row of each
   `heartbeat_id` as its registration, deletes every older row of that id as
   residue, and deletes an expired newest row with a
   `heartbeat_registration_expired` event. No reader consults whether the
   registrant is alive. When the last row is deleted, the heartbeat's
   observation path sees the row gone at its next wait entry (paused or
   not), rebuilds to an empty table, and exits after
   `HEARTBEAT_IDLE_TIMEOUT_SECONDS`; a turn whose pre-emission rebuild
   already observed the delete emits nothing, and a delete committed after
   that rebuild may be followed by at most one emission. The Manager,
   seeing an empty queue (or only expired rows, which it then deletes),
   does not restart it.

A wrong answer is a stop-and-re-read blocker.

## 4. Invariants and Constraints

Must not change:

- [IMPL.10]: one drive-owning thread per reactor; `run_until_stopped` is
  the only process/wait/finalize loop. No thread, timer, poll loop, or
  helper is added. [IMPL.8]/[IMPL.9]: the reactor thread still commits all
  broker effects.
- [WT-2a.6]: the TaskMonitor reactor keeps one arbiter. After the change
  its closed input set is its two queues, its worker-result notifications,
  and its one cycle deadline.
- Inbox pokes: any row on `T{monitor_tid}.inbox` still requests a cycle
  (`_handle_work_message` unchanged). The manager's launch-time
  `inbox_message` still triggers the first cycle path.
- Cycle cadence: the move preserves the fixed-rate period and poke
  independence; the grid phase is anchored at the first cycle start rather
  than at registration ingestion (register row 3b). The
  interval and catch-up values, the catch-up predicate
  (`progress_requires_catchup`), `_first_cycle_pending`, `_wake_requested`,
  and `_handle_work_message` are unchanged; the first cycle still runs on
  the first enabled turn. Under F2's default the grid that the heartbeat
  supplied today (ticks that inbox pokes do not move, one follow-up after
  an overrun) moves onto the local deadline: both re-arm sites call one new
  method, `_arm_next_cycle_deadline` (Task 3 items 9–11).
- Self-maintenance ([OBS.13.10]): `_maybe_run_maintenance_pass`, its
  deadline, `WEFT_TASK_MONITOR_MAINTENANCE` /
  `WEFT_TASK_MONITOR_MAINTENANCE_INTERVAL_SECONDS`, and the non-policy
  `maintenance` STATUS block are unchanged.
- Export and retention behavior: the external JSONL sink, `jsonl_then_delete`
  lifetime reports, deferred writes, task-log retention
  (`WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS`), runtime cleanup slices, the
  four cleanup-policy identities ([OBS.13.12]), and every [OBS.13]
  sub-invariant are unchanged. No code under `weft/core/monitor/` other than
  `task_monitor.py` is touched, and inside it only the sites listed in
  Task 3.
- [ALT-WT-020]: `MonitorStore` stays private to TaskMonitor. No status,
  result, manager, or liveness path gains a read of it; this plan adds no
  read anywhere.
- [ALT-WT-005]: no new `MaintenanceWorker` input or field.
- PONG ([OBS.13.11]): the extension still reads only cached state. Every
  other key of `extended.task_monitor` keeps its name and meaning; only
  the `heartbeat` block is removed.
- The heartbeat service through Task 5: unchanged. Tasks 6–8 change only
  where registrations live, how the service learns them, its launch
  predicate, its idle-exit override, and how long a registration lives.
  Unchanged by every task: emission and coalescing, the 60 s
  registration minimum, the destination rules (self-route and `weft.`
  namespace rejection), the `_weft.heartbeat` endpoint claim, singleton
  supersession, the manager-owned launch path ([MA-1.6a], [MANAGER.15],
  [MANAGER.16]: launch and restart still go through the internal spawn
  queue and the singleton reducer), and the admission reserve
  ([MANAGER.18], which keeps modeling a heartbeat slot whether or not one
  runs; conservative, no change).
- [WT-2a.2] for Tasks 6–8: the registrations queue is the only truth about
  which heartbeats exist; the heartbeat's in-memory table and due heap are a
  hint rebuilt from it, and the Manager's desire is recomputed from the queue
  on every convergence pass, never cached.
- [ALT-WT-019] for Tasks 6–8 (owner, 2026-11-03: make the distinction
  explicit). The theory rejects using a SimpleBroker queue as an unconsumed
  relational table or event log: rows left in place after the work they
  describe is over, so that versioned or tombstone records accumulate and
  a later sweep must delete them. `weft.state.heartbeats` can look like
  that case, because the heartbeat service peeks its rows and never
  consumes a valid one. It is not that case, and the difference is what
  the row stands for. A registration row is an active leased request: one
  token per live `heartbeat_id`, present exactly while the request it
  carries is in force, the way a task's reserved row is present exactly
  while its work is in flight. The reader is not the consumer, because
  reading the request does not end it. The request ends in one of two
  ways, and each one consumes the row at that moment: the registrant
  cancels (`cancel_heartbeat` deletes the row) or the lease the registrant
  declared runs out (the service deletes the row at its expiry deadline,
  Task 7 item 8). No row outlives its request, so the queue holds no
  history, carries no `state: "released"` or expired-marker records, and
  needs no garbage-collection pass; when the last request ends, the last
  delete empties the queue and SimpleBroker drops it on its own, which is
  the consume-to-disappear property [ALT-WT-019] protects. The one
  deletion that is not the end of a request is bounded and local: an
  upsert writes the new row and then deletes the older rows of the same
  `heartbeat_id`, and if that second step is interrupted, the next table
  build finishes it (§ "the service builds its registration table").
  That is the second half of a replacement completed by the next reader,
  not a sweep over accumulated records. Register row 16 states the cost of
  this shape honestly: a registrant that dies without cancelling is
  emitted to until its own lease ends, which is the bound it chose, not a
  tombstone awaiting collection.
- [WT-2a.6] for Tasks 7–8: each new wake is a notification-only interest on
  the reactor's one arbiter (the [QUEUE.7] shape); no thread, poll, timer,
  or delivery config is added. The Manager's and the heartbeat's existing
  timers are unchanged; the heartbeat's wait gains one deadline term, the
  earliest lease expiry over its table, a named future instant rather than a
  poll (Task 7 item 8).
- [WT-2a.13] for Tasks 6–8: no reader consults a registrant's liveness. A
  registration ends when its registrant cancels it or when the lease the
  registrant declared in the row ends. That lease is the registrant's own
  statement in an ordinary message ([WT-4] "It is just a message"), not an
  inference of death from absent evidence: expiry says only that the request
  is over, never that the registrant died.
- `WEFT_TASK_MONITOR_INTERVAL_SECONDS` keeps its 60 s floor and default
  300 (floor change is the older draft's Q2; out of scope).
- Tasks 2–5 add no queue name, control message, TaskSpec field, config key,
  env var, status token, or dependency. Tasks 6–8 add exactly: the queue
  `weft.state.heartbeats` (constant `WEFT_HEARTBEAT_REGISTRATIONS_QUEUE`),
  the row fields `registrant_tid` and `lease_seconds`, the
  `upsert_heartbeat` keywords `registrant_tid` and `lease_seconds`, the
  validation constant `HEARTBEAT_MAX_LEASE_SECONDS` (seven days, a policy
  value the owner may raise or lower), the lifecycle event
  `heartbeat_registration_expired`, and the `weft status` heartbeat evidence
  value `no-registrations`. No control message, TaskSpec field, config key,
  env var, CLI value, or dependency.

Hidden couplings (named so they are not found mid-slice):

- `_last_error` is reset to `self._heartbeat_error` on success at three
  sites (lines 5975, 5995, 6377). With the field gone, all three become
  `self._last_error = None`. The PONG `last_cycle.error` otherwise keeps its
  meaning.
- Removing the module-level `upsert_heartbeat` name breaks all 119 test
  monkeypatch targets at once. Task 3 removes them in the same edit pass;
  do not leave the file half-migrated between test runs.
- The tests written in Task 2 must not let an unpatched TaskMonitor reach
  the real `ensure_heartbeat_service`: with no live endpoint it calls
  `manager_runtime.ensure_manager()`, which must never run from a unit test.
  Task 2 patches both `upsert_heartbeat` and `cancel_heartbeat` with
  `raising=False`, so the same test text runs before and after the removal,
  and the patches **stay** in R1, G1, and C1–C4 after Task 3: if a later
  change restored the registration, the injected stand-in (in R1
  `unavailable`, the injected failure), not real manager startup, would
  receive it, and R1 would count the restored retry turns. The passing
  unpatched run is a separate verification step (Task 3 test edit 3).
- Chained re-arm: on the built-in path `_handle_builtin_cycle_worker_result`
  calls `_finish_monitor_cycle` (first re-arm) and may then start the
  terminal control-cleanup worker, whose result re-arms again in
  `_handle_control_cleanup_worker_result`. Today an overrun follow-up
  survives that chain only because `_wake_requested` is a sticky boolean.
  The local rule therefore carries the owed follow-up as explicit state
  (`_overrun_follow_up_pending`) until a cycle actually starts; otherwise
  the second re-arm overwrites `due = now` with the next grid tick (C4).

Hidden couplings added by Tasks 6–8:

- Manager service-supervision path. The heartbeat desire gate appears at
  three Manager sites (`_reconcile_managed_services`,
  `_pending_service_probes_for_scope`,
  `_managed_service_convergence_active_reasons`) and once in
  `weft/commands/system.py::_service_enabled`. All four change together to
  the same predicate; a missed site either launches without a registration
  or keeps convergence "active" (1.0 s cadence instead of the 5.0 s stable
  audit) forever on `missing_active_tid`. Only the canonical manager (inbox
  `weft.spawn.requests`) supervises internal services, so only it holds the
  new notification interest. Cost: the predicate
  (`Manager._heartbeat_registrations_present`, Task 8 item 1) peeks and
  parses every row, O(N) in registrations with an early return at the
  first unexpired or invalid row, and it runs wherever the gate sites run,
  including `_managed_service_convergence_active_reasons`, which
  `Manager.next_wait_timeout` calls (`weft/core/manager.py` line 8180), not
  only in a completed convergence pass; each call is bounded by the
  registration count, and Task 5 measures it. No cache or registry is
  added ([ALT-WT-010]).
- Delivery config versus notification interest. A PEEK delivery config on
  `weft.state.heartbeats` in either reactor would spin it, because rows stay
  put and `MultiQueueWatcher._has_pending_messages` reports them as pending
  on every wait (E16). Both readers use the `_activity_wait_queues` /
  `_has_pending_observation` shape with a strict publication cursor. The
  cursor sees new rows only. Deletes publish nothing newer, so the
  heartbeat's observation also checks, while its table is non-empty, that
  each current row ID is still present (Task 7 item 3); it runs at every
  wait entry, independent of emission and of `_paused`. A delete wakes the
  waiter on SQLite (any commit changes `data_version`); simplebroker_pg
  emits NOTIFY on insert, rename, and move only (`simplebroker_pg/_sql.py`,
  verified 2026-11-02), so on PostgreSQL a delete is learned at the next
  wait return, which the retained 1.0 s ownership-audit timer (E14) caps
  while the service runs. That timer is now a dependency of the delete
  bound: a later plan that removes it must re-derive this bound. The Manager
  learns deletes at its next convergence pass.
- Restart backoff while the queue is non-empty but the task keeps failing.
  `_heartbeat_service_spec` sets no `restart_backoff_ns`
  (`ManagedServiceSpec.restart_backoff_ns` defaults to 0,
  `weft/core/manager_services.py` line 54; the reducer schedules backoff
  only when it is positive, `manager.py` near line 7724), and child cleanup
  calls `_run_managed_service_convergence(include_autostart=False,
  force=True)` (`manager.py` line 8401), which bypasses the convergence
  throttle. So there is no restart-rate bound: a crashed heartbeat is
  relaunched as soon as its exit is reaped, as it is today whenever
  TaskMonitor is enabled. New: a row now outlives the process, so a row that
  crashes the service would crash it on every relaunch, a tight crash loop,
  where today a crash discarded the in-memory registration. What prevents
  the loop is the invalid-row rule at both ingestion boundaries: a row that
  fails `HeartbeatRegistrationRow` (including an unschedulable interval or
  an out-of-bounds lease,
  see the shared names under Tasks 6–8) is rejected by `upsert_heartbeat`
  before it is written and, if found in the queue anyway (a hand-written
  row), deleted by exact ID at table build before any use (T6). Emission
  errors are already caught (`BrokerError`, `OSError`, `RuntimeError`). If a
  valid row can still crash the service, stop (Task 7) and report; do not
  add a backoff knob or a quarantine queue.
- Who may write the registrations queue. Any process in the operator trust
  domain ([WT-2a.11]); a hand-written row is a registration. Validation at
  write (`upsert_heartbeat`) and at read (`HeartbeatTask`) checks shape for
  correctness, never authority; no sanitizer or authentication is added.
- Registrant identity. A registrant holds a TID ([WT-2a.12]) and need not
  publish task lifecycle evidence (a `weft.log.tasks` status or a
  `weft.state.tasks.<tid>` row); nothing reads that evidence for a
  registration. Its row lives for its lease and no longer, whether the
  registrant is alive, dead, or was never a Weft task; to continue, the
  registrant resubmits. Name that rule in the `upsert_heartbeat` docstring.
- Clock skew. The lease is measured from the row's broker message
  timestamp, on the clock that minted the message ID (SimpleBroker's hybrid
  timestamp, E20), not from the commit instant. The bounds, each checked in
  the installed SimpleBroker (E20): under normal clock progression the ID
  differs from the writer's sampled physical time by less than 4,096 ns (the
  12 counter bits). When the writer's clock reads earlier than the last
  stored timestamp, the generator carries the previous physical component
  forward and advances the counter, so the ID can lead the writer's wall
  clock by the whole regression (the cross-family round of 2026-11-03
  reproduced an ID 120 s ahead of a supplied clock). Counter exhaustion
  waits for the clock to advance or raises `TimestampError`; it never mints
  a future time. The timestamp is allocated inside the write transaction
  before the insert commits, so it precedes the commit instant. The
  PostgreSQL backend uses the same Python generator (`simplebroker_pg`
  stores it through `advance_last_ts`; no server-side clock allocates it).
  Consequence: the lease starts at the write instant within microseconds in
  the normal case and later than it after a clock regression. An ID ahead
  of the wall clock puts `ID + lease` further in the future, so a reader's
  `time.time_ns()` reaches it later and the effective lease is longer than
  the registrant's statement by the size of the regression; a regression
  never makes it shorter. Across hosts, a reader compares the ID with its
  own `time.time_ns()`, so it sees the expiry shifted by the clock skew
  between the writer's host and its own. Both effects are accepted, not
  corrected: one operator trust domain ([WT-2a.11]), and on SQLite every
  reader shares one host. The message-ID formula stays (owner ruling on
  fork F5).
- Heartbeat idle exit versus a new row. Write-then-delete ordering in
  `upsert_heartbeat` keeps the queue non-empty across a replace, so a replace
  never makes the Manager see an empty queue. A row written while the
  heartbeat idle-exits is not lost: the row stays, the Manager's interest
  fires, and the next convergence relaunches the service.
- The heartbeat's 1.0 s ownership-audit timer (E14) stays while the service
  runs; this plan does not fix it, and on PostgreSQL it depends on it as the
  cap on delete observation (see the delivery-versus-interest coupling
  above). A later plan that removes the timer re-derives that bound.

Error-path priorities: cycle scheduling is core (a missed or doubled cycle
is a defect); the PONG block removal is a contract change (CHANGELOG);
comment and docstring edits are documentation. For Tasks 6–8: a lost
registration, an emission in a turn whose pre-emission rebuild observed the
cancel, a second emission after a cancel, a heartbeat that runs with an
empty queue past its idle timeout (paused or not), or one that is not
launched while an unexpired row exists is a defect; the helper's write or delete failing raises to the
caller (no retry, no swallow); an invalid row found at table build is
best-effort (report the event, delete the row, continue); an older-row
delete that fails at table build stops that id's deletion before its newest
row and is best-effort: logged at debug, the rebuild flag cleared as usual,
the id's rows retried at the next build (Task 7 item 4 a); an emission for
a registration after the turn whose rebuild found it expired is a defect;
the Manager's delete of rows when every row has expired is best-effort and
ordered (ascending message ID, stopping at the first delete error, which is
logged at debug; the next pass reads again; Task 8 item 1).

Review gates: no second wait path; no new abstraction or helper beyond the
one deadline method `_arm_next_cycle_deadline` and its two fields (F2
default); no mock of `TaskMonitor`, `HeartbeatTask`, broker queues, or the
reactor wait (the only patches allowed are the existing clock patches, the
module-level clock-advancing processor in C2 and its pre-cycle-2 swap to
`recording_processor`, and the two `raising=False`
registration stand-ins in R1, G1, and C1–C4); no drive-by refactor of
`task_monitor.py`; independent cross-family review of plan and delta before
Task 2. For Tasks 6–8: no new helper, class, or module beyond
`HeartbeatTask._rebuild_registrations`, the two observation overrides on
`HeartbeatTask`, one Manager predicate method
(`Manager._heartbeat_registrations_present`), one observation flag and
cursor on each reactor, the two lease methods on the row model
(`HeartbeatRegistrationRow.expires_at_ns` and
`HeartbeatRegistrationRow.expired`), and one expiry deadline term in
`HeartbeatTask.next_wait_timeout`; the registration row model replaces
`HeartbeatMutation` rather than sitting beside it; no mock of `HeartbeatTask`,
`Manager`, the broker, or the reactor wait (the only stand-in is the
guard on `weft.core.manager_runtime.ensure_manager` in H1, and the
existing fake-clock patterns in T3, T8, T9, T10, and M1's expired case,
plus the one-shot failing `delete` on the registrations handle in T10's
third case and M1's failed-delete check, which fails one call and then
delegates to the real method). One
new constant, `HEARTBEAT_MAX_LEASE_SECONDS`, is allowed (Tasks 6–8 shared
names); it is a validation bound, not a mechanism.

Out of scope (named to stop opportunistic edits): the older draft's other
launch options (L2/L2f and `WEFT_HEARTBEAT_SERVICE_ENABLED`); the
finish-anchored cadence (fork F2's alternative, only if the owner picks it);
the interval floor; a [CC-2.5]
wake-model paragraph; an [IMPL.x] invariant row; any heartbeat change beyond
Tasks 6–8 (emission, coalescing, the minimum interval, supersession, the
1.0 s ownership-audit timer, the endpoint claim); a lease renewal protocol,
a per-tick lease write, or a default lease; a restart-backoff knob or a
poison-row quarantine for the heartbeat; widening LivenessMonitor's custody;
other two-clock or timer-floor drifts in
audit row 4.3-a (plan 2 owns `ServiceWorkerContext.iter_items` and
`_await_control_surface`).

### Observable-difference register

Classes: A owner-decided removal; N no difference; C changed, no loss;
D diminution (must be fixed in plan or escalated).

| # | Observable | Before (`b7eed9f9`) | After | Class | Proof |
|---|-----------|---------------------|-------|-------|-------|
| 1 | Idle turns over a quiet window, registration failing | 2 per 2.5 s (1.0 s retry timer) | 0 | A (2026-09-08 decision) | Task 2 R1 `unavailable` |
| 2 | Idle turns over a quiet window, heartbeat live and emitting | ≥1 per emission (message wake) | 0 (no registration, no emission to the Monitor) | A | Task 2 R1 `emitting` |
| 3 | Cycle cadence anchor | while the heartbeat is live, a heartbeat grid anchored when `HeartbeatTask` ingests the registration (`weft/core/tasks/heartbeat.py` line 249, `next_due_at = monotonic() + interval`), with the Monitor's finish-anchored local deadline (`finish + interval`) still active alongside it; finish-anchored alone while the heartbeat is not live | F2 default: local grid anchored at the first cycle start, never re-anchored, in every heartbeat state. F2 alternative: `finish + interval` always | D under the alternative (interval 60, 10 s cycle: on the heartbeat grid the next cycle starts at 60, finish-anchored at 70; the lag grows by one cycle duration per period); fixed in plan by the F2 default, an improvement against heartbeat-down behavior; against heartbeat-live behavior the default preserves the fixed-rate period and poke independence; the grid phase is anchored at the first cycle start rather than at registration ingestion (the phase change is row 3b, class C) | C1, C2, C3; G1; existing `test_task_monitor_next_wait_timeout_publishes_cycle_deadline` |
| 3b | Grid phase (where the fixed-rate ticks fall) | heartbeat live: ticks at registration ingestion + k × interval, plus the local finish-anchored deadline, so the cycle sequence depends on when `HeartbeatTask` ingested the row (first cycle 0–10, ingestion at 30, interval 60: local cycle at 70, heartbeat cycle at 90) | F2 default: ticks at first cycle start + k × interval (same example: 60, 120) | C (same period; the same worst-case delay bound of one interval between periodic cycles; only the phase anchor moves, from an ingestion instant set by another process's turn timing to the Monitor's own first cycle start; no consumer reads the phase, and the sequence is now deterministic rather than dependent on heartbeat ingestion timing). Owner sees it under fork F2 | C1, C3 |
| 3a | Inbox poke versus the next periodic cycle | heartbeat live: the poke runs a cycle and the next periodic cycle stays on its grid tick | F2 default: same. F2 alternative: next periodic cycle at poke-cycle finish + interval, so a poke just before a tick postpones it by nearly an interval | D under the alternative; fixed in plan by the F2 default (N) | C1; existing `test_task_monitor_pending_wakeup_uses_shared_reactor_wait` |
| 4 | Follow-up cycle after a cycle overruns a tick | one immediate follow-up (the tick sets `_wake_requested` mid-cycle) | F2 default: exactly one owed follow-up, carried across the chained control-cleanup re-arm until a cycle starts. F2 alternative: none; next cycle at `finish + interval`, or `finish + catch-up` while a policy reports backlog | D under the alternative (`progress_requires_catchup` does not cover a tick missed during an overrun); fixed in plan by the F2 default (N) | C2, C3, C4 |
| 5 | Reactor blocked in `upsert_heartbeat` (a call to `manager_runtime.ensure_manager()` from the Monitor reactor, then a poll of up to 0.5 s, when no endpoint is live) | possible on any enabled turn while unregistered | never | C (improvement) | Task 3 grep gate G-1 |
| 6 | PONG `extended.task_monitor.heartbeat` (4 keys) | present | absent | A (clean break, no shim; no in-repo reader per E8) | Task 2 R2 |
| 7 | PONG `last_cycle.error` after a successful cycle | heartbeat registration error string while registration fails | `None` | A | Task 3; deleted test row 10118 |
| 8 | STOP/KILL sends a heartbeat `cancel` | yes, when registered | nothing to cancel | N | — |
| 9 | Inbox poke runs a cycle | yes | yes | N | existing `test_task_monitor_pending_wakeup_uses_shared_reactor_wait` |
| 10 | Self-maintenance cadence (3600 s deadline checked in cycles) | unchanged | unchanged | N | Task 2 G1 |
| 11 | Heartbeat process count with zero registrants (`WEFT_TASK_MONITOR_ENABLED=1`) | one process, running a supersession-audit turn about every 1.0 s (E14) | none; the service is desired only while `weft.state.heartbeats` holds a row that is unexpired or not yet rejected ([MF-3.2]), and a running one exits `HEARTBEAT_IDLE_TIMEOUT_SECONDS` after its table empties | A (owner ruling on F1, 2026-11-02); removed cost | Task 8 M1 (empty queue), Task 7 T3 (idle exit), modified `test_manager_enqueues_heartbeat_through_service_path` |
| 11a | Heartbeat process count with one or more registrants | one (today only while TaskMonitor is enabled; a registrant with TaskMonitor disabled timed out in `ensure_heartbeat_service`) | one, whatever the monitor flags | C (improvement: a registrant no longer depends on `WEFT_TASK_MONITOR_ENABLED`) | Task 8 M1 (row present, both monitors disabled) |
| 13 | First-registration latency | the registrant blocks in `ensure_heartbeat_service`: endpoint resolution, a log scan, up to a 0.25 s PING, then `ensure_manager()` and a registry poll of up to `startup_timeout`, raising `RuntimeError` on timeout; the first emission comes one interval after the service ingests the row | the registrant returns once its row write commits; the Manager wakes on its notification interest and launches the service in the next convergence pass; the first emission comes one interval after the table build that finds the row, so launch time plus one interval, as today | C (the registrant's wait is removed; first-tick timing unchanged in shape) | Task 6 H1, Task 8 M2 |
| 14 | Registration survival across heartbeat or Manager restart | lost (in-memory only; [MF-3.2] "disappear on service or Weft restart"); the registrant had to re-register | kept: the row stays and is rebuilt into the table by the next heartbeat; due time restarts at build + interval | C (strengthened) | Task 7 T2 |
| 15 | Removed polls | `ensure_heartbeat_service` registry poll (`MANAGER_REGISTRY_POLL_INTERVAL` sleeps up to `startup_timeout`) on every upsert and cancel; TaskMonitor's 1.0 s registration retry timer | both gone: the registry poll by Task 6, the retry timer by Task 3 | A ([ALT-WT-002]; 2026-09-08 decision for the retry timer) | Task 6 H1 and grep gate G-5; Task 2 R1 |
| 16 | Dead registrant (dies without cancelling) | emitted to until the heartbeat restarts, which the manager-spawned service never does on its own (idle 0.0) | emitted to until its lease ends, the bound the registrant chose when it wrote the row; the heartbeat deletes the row at its expiry deadline and, if the table is then empty, exits on its idle timeout; the Manager does not relaunch it. No prune group, no owner check, no evidence requirement | C (a named rule replaces none; the bound is the registrant's own) | Task 7 T9, T10; Task 8 M1 `expired` case |
| 17 | Cancel takes effect | at the next heartbeat turn (inbox mutation) | the row is gone at once; the service drops the registration at its next wait entry (paused or not; on PostgreSQL within the 1.0 s ownership-audit interval) and at the latest at the pre-emission rebuild of a due turn. A cancel committed before that rebuild suppresses the turn's emission; one committed after it may be followed by at most one emission. If the table empties, the service exits `HEARTBEAT_IDLE_TIMEOUT_SECONDS` later | N for emissions (the same at-most-one window as today's inbox mutation, which lands between turns); C for process lifetime (shorter than before: no longer waits for a due time) | Task 7 T3, T4, T8 |
| 18 | Heartbeat inbox | accepts `upsert` / `cancel` mutations | every inbox row is rejected with `heartbeat_request_invalid` and the reserved-on-error policy | A ([WT-2a.9] one current contract; no shim) | Task 7 T5 |
| 19 | `weft status` heartbeat row with no registration | `enabled` follows `WEFT_TASK_MONITOR_ENABLED`; `config-disabled` when off | `enabled` is `False` with evidence `no-registrations` while the queue is empty or every row has expired, `True` while `weft.state.heartbeats` holds a row that is unexpired or not yet rejected ([MF-3.2]) | C (the row reports the real launch predicate) | Task 8 S1 |
| 21 | Registration lifetime | until cancel, heartbeat exit, or Weft restart | until cancel or until its own lease ends (`lease_seconds` after the row's write) | C | Task 7 T9 |
| 22 | Resubmission | an upsert with the same `heartbeat_id` replaces the in-memory registration and resets its due time to `now + interval_seconds` | an upsert with the same `heartbeat_id` replaces the row and its lease; the due time restarts one interval after the rebuild that finds the new row, as any upsert today | N | Task 6 H2; Task 7 T10 second case |
| 12 | Export, retention, [OBS.13], MonitorStore privacy | — | unchanged | N | full `tests/tasks/test_task_monitor.py` green on SQLite and PG |

Rows 3, 3a, and 4 are D only under fork F2's alternative; the default the
tasks implement fixes them in plan. No other D rows; rows 11–22 carry none
(the on-demand shape removes waits and processes, and the registrant-declared
lease bounds every registration where no bound existed).

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Spec Baseline

- `b7eed9f9` — `docs/specifications/05-Message_Flow_and_State.md`,
  `docs/specifications/01-Core_Components.md`,
  `docs/specifications/03-Manager_Architecture.md`,
  `docs/specifications/00-Quick_Reference.md`, and
  `docs/specifications/07-System_Invariants.md` at plan authoring time
  (2026-11-02; 07 re-read for the F1 extension the same day). Plan type: implementation with spec revision.
- Promotion baseline identifier: `4e082fd8` (the landing commit; strategy B
  landed spec and code together).

## Proposed Spec Delta

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| docs/specifications/05-Message_Flow_and_State.md | B — atomic with the code | [MF-3.2] lines 446–447 (flow), 454–462, 469–475, 479–484, 489–495, and 496–506 (bullets), and 512–516 (mapping note); [MF-5] line 548 and lines 717–720; [MF-5] `_Implementation mapping_` method list (lines 579–581, one name added); catch-up sentence lines 1999–2001; `## Related Plans` backlink |
| docs/specifications/01-Core_Components.md | B | [CC-2.3] lines 643–644 and 664–666; first `## Related Plans` backlink |
| docs/specifications/03-Manager_Architecture.md | B | [MA-1] item 7 lines 289–291; `## Related Plans` backlink |
| docs/specifications/07-System_Invariants.md | B | [QUEUE.7] lines 181–182 and its `_Implementation mapping_` bullet; `## Related Plans` backlink |
| docs/specifications/00-Quick_Reference.md | D — documentation of an env var and a queue row, no code cites either; lands in the same commit anyway | line 196; global queue table row after line 42; `## Related Plans` backlink |

Why B and not A: every sentence in the delta describes the removal. Under
strategy A the spec would say "no built-in service registers a heartbeat"
while shipped code still registers one, and the [MF-5] cadence sentence
would describe a grid the code does not yet keep. The delta is small. The
[MF-3.2] mapping already lists `weft/core/monitor/task_monitor.py`, which
still implements the PONG bullet; it is deliberately **kept**. The one new
mapping claim is `TaskMonitor._arm_next_cycle_deadline`, appended to the
[MF-5] mapping's TaskMonitor method list in the same commit that adds the
method, whose docstring cites [MF-5]. So one atomic landing carries no
reciprocity debt. The F1 extension (Tasks 6–8) is B for the same reason:
"registrations survive restarts", "every registration carries its own
lease", or "the manager desires the heartbeat while the queue holds an
unexpired row" would be false while the inbox path ships.
Its new mapping claims (`upsert_heartbeat`, `cancel_heartbeat`,
`HeartbeatTask._rebuild_registrations`, and the two `HeartbeatTask`
observation overrides) land in the same commit as the code they name.

Clause audit of removed text: the [MF-3.2] registration bullet carries two
rules (the Monitor registers; on failure it records health and falls back
to its local interval). Both retire; the local deadline survives as the
only clock, and the heartbeat-live grid cadence it now carries (F2 default)
is restated in the [MF-5] cadence sentence below. The
[MF-3.2] PONG bullet carries five rules (namespace, contents, built-in
cleanup stats, policy stats, no scans during PING); only "heartbeat
registration state" is removed, the other four are restated verbatim.

Clause audit for the F1 extension's removed [MF-3.2] text:
"runtime-only and disappear on service or Weft restart" retires (replaced
by survival; register row 14); "runtime-only" survives as "runtime-state
queue ... excluded from dumps". "helper startup ensures a manager exists
and waits for a live manager-owned heartbeat endpoint" retires (register
rows 13 and 15); "the helper does not submit heartbeat tasks directly;
heartbeat launch and restart belong to the manager-owned internal service
path" survives as "launches it through the manager-owned internal service
path". "registration messages are ordinary inbox payloads with `action`
`upsert` or `cancel`" retires (rows replace mutations; register row 18).
"`upsert` resets `next_due_at` to `now + interval_seconds`" survives as "a
row that is new or replaced ... is first due `interval_seconds` after the
build that finds it". The reactor bullet keeps every rule and adds the
notification interest, the pre-emission rebuild, and the idle-exit rule.
"Helper-side endpoint validation ..." retires with `ensure_heartbeat_service`;
the singleton summary rules it cited still govern the Manager's own service
candidates ([MA-1.6a]) and `weft status`, which this plan does not touch.
The destination bullet keeps both rejection rules, moves the self-route
check to table build, and replaces "reserved-on-error policy" (there is no
reserved row for a queue row) with exact deletion of the rejected row, the
2026-09-04 malformed-row rule.

### `docs/specifications/05-Message_Flow_and_State.md` [MF-3.2] — replace the bullet that opens "the supervised `TaskMonitor` uses heartbeat registrations" and closes "falls back to its bounded local interval" (lines 496–499)

Current text:

> - the supervised `TaskMonitor` uses heartbeat registrations for periodic
>   wake messages to its own `T{tid}.inbox`; if registration is temporarily
>   unavailable, the monitor records operational health and falls back to its
>   bounded local interval

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
- no built-in service registers a heartbeat. Each built-in service keeps
  its own clock through `next_wait_timeout()` on the shared task reactor
  wait path, and no built-in service uses a queue message from another
  service as its clock.
  The heartbeat service is an emitter for consumers that want a periodic
  message, not the clock of another service (theory: [ALT-WT-004] in
  `docs/program-theory.md`).
```

### `docs/specifications/05-Message_Flow_and_State.md` [MF-3.2] — replace the PONG bullet that opens "the supervised `TaskMonitor` includes cached operational diagnostics" and closes "recompute cleanup candidates during PING." (lines 500–506)

Current text:

> - the supervised `TaskMonitor` includes cached operational diagnostics in
>   PONG extension data under `extended.task_monitor`: configuration, heartbeat
>   registration state, scheduling state, and the previous cycle summary. For
>   built-in cleanup processors, the summary includes cached queue-level cleanup
>   stats and cached policy-level cleanup stats under
>   `cleanup_policy_stats`. This extension must not scan queues or recompute
>   cleanup candidates during PING.

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
- the supervised `TaskMonitor` includes cached operational diagnostics in
  PONG extension data under `extended.task_monitor`: configuration,
  scheduling state, and the previous cycle summary. For built-in cleanup
  processors, the summary includes cached queue-level cleanup stats and
  cached policy-level cleanup stats under `cleanup_policy_stats`. This
  extension must not scan queues or recompute cleanup candidates during PING.
```

The `_Implementation mapping_` at lines 508–511 is unchanged (the PONG
bullet is still implemented by `weft/core/monitor/task_monitor.py`).

### `docs/specifications/05-Message_Flow_and_State.md` [MF-3.2] — replace the two flow lines inside the `text` fence under "Current heartbeat flow is task-shaped:" (lines 446–447; Tasks 6–8)

Current text:

> helper -> resolve _weft.heartbeat -> ordinary queue write to service inbox
> service inbox -> in-memory registration heap -> ordinary queue write to destination

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
registrant -> validated row write to weft.state.heartbeats (upsert replaces, cancel deletes)
the heartbeat service, always running under the canonical Manager, peeks weft.state.heartbeats -> in-memory due heap -> ordinary queue write to destination
```

### `docs/specifications/05-Message_Flow_and_State.md` [MF-3.2] — replace the rules from "heartbeat registrations are runtime-only" through "`upsert` resets `next_due_at` to `now + interval_seconds`" (lines 454–462; Tasks 6–8)

Current text:

> - heartbeat registrations are runtime-only and disappear on service or Weft
>   restart
> - helper startup ensures a manager exists and waits for a live manager-owned
>   heartbeat endpoint. The helper does not submit heartbeat tasks directly;
>   heartbeat launch and restart belong to the manager-owned internal service
>   path.
> - registration messages are ordinary inbox payloads with `action` `upsert` or
>   `cancel`
> - `upsert` resets `next_due_at` to `now + interval_seconds`

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
- heartbeat registrations are rows on the runtime-state queue
  `weft.state.heartbeats`, one row per live registration, keyed by
  `heartbeat_id` and naming the registrant's TID in `registrant_tid`. A row
  carries `heartbeat_id`, `registrant_tid`, `interval_seconds`,
  `lease_seconds`, `destination_queue`, and `message`, and nothing else.
  Registrations survive heartbeat-service and manager restarts; like every
  `weft.state.*` queue, the queue is excluded from dumps.
- every registration carries its own lease (`lease_seconds`, at least
  `interval_seconds` and at most `HEARTBEAT_MAX_LEASE_SECONDS`, seven days;
  the maximum is a policy value, not intrinsic to Weft). It expires
  `lease_seconds` after the row's own message timestamp; the service deletes
  an expired row at its expiry deadline and emits nothing for it in any turn
  whose rebuild found it expired.
  A registrant that wants the heartbeat to continue resubmits (upsert with
  the same `heartbeat_id`), which replaces the row and its lease. No reader
  consults the registrant's liveness.
- `upsert_heartbeat` validates the row (`interval_seconds` at least
  `HEARTBEAT_MIN_INTERVAL_SECONDS`; `lease_seconds` at least
  `interval_seconds` and at most `HEARTBEAT_MAX_LEASE_SECONDS`, so every
  accepted interval and lease is bounded and schedulable), writes it, then
  deletes the older
  rows with the same
  `heartbeat_id`; `cancel_heartbeat` canonicalizes `heartbeat_id` as upsert
  does and deletes every row with that `heartbeat_id`. Each returns once its queue write or deletes commit. Neither
  looks up an endpoint, starts a manager, or waits for the service. Any
  process in the operator trust domain may write the queue; validation checks
  shape, not authority.
- the canonical manager runs the heartbeat service as a standing internal
  service through the manager-owned internal service path ([MA-1] item 7),
  whether or not any row exists; the manager neither reads nor deletes the
  queue. The heartbeat service is the queue's only reader and deleter. A row
  written while no manager runs waits in the queue until a heartbeat service
  runs.
- the service builds its registration table by peeking the queue and never
  consumes a valid row by reading it: a row is an active leased request,
  and it is consumed only when that request ends, by the registrant's
  cancel or by the service's delete at the lease's expiry. No row outlives
  its request, so the queue is never an unconsumed history ([ALT-WT-019]).
  The registration of a `heartbeat_id` is its newest row;
  older rows of that id are residue of an interrupted replacement and are
  deleted when the table is built. A row that is new or replaced since the
  previous build is first due `interval_seconds` after the build that finds
  it; an unchanged row keeps its due time. The heartbeat task's inbox accepts no registration
  mutation: each inbox row is rejected as an invalid request.
```

### `docs/specifications/05-Message_Flow_and_State.md` [MF-3.2] — replace the reactor bullet from "the heartbeat service is driven by the shared task reactor loop" through "only the timeout supplied to that same wait path." (lines 469–475; Task 7)

Current text:

> - the heartbeat service is driven by the shared task reactor loop. It handles
>   at most one bounded control/registration/due-emission turn in
>   `process_once()`, exposes the next registration due time or idle shutdown
>   through `next_wait_timeout()`, and leaves all destination queue writes on the
>   main task thread. It does not inspect inbox/control pending state in
>   `next_wait_timeout()`; queue wakeup belongs to `MultiQueueWatcher`, and the
>   heartbeat timer is only the timeout supplied to that same wait path.

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
- the heartbeat service is driven by the shared task reactor loop. It handles
  at most one bounded control/inbox-rejection/table-build/due-emission turn in
  `process_once()`, exposes the next registration due time and the earliest
  lease expiry through `next_wait_timeout()` (no deadline with an empty
  table), and leaves all destination queue writes on the
  main task thread. It does not inspect inbox/control pending state in
  `next_wait_timeout()`; queue wakeup belongs to `MultiQueueWatcher`, and the
  heartbeat timer is only the timeout supplied to that same wait path. It holds
  a notification-only interest in `weft.state.heartbeats` ([QUEUE.7]): a row
  newer than its publication cursor, or the absence of a row its table holds,
  rebuilds the table on the next turn, whether or not the service is paused.
  The wait also carries the earliest lease expiry over the table as a
  deadline; a turn at or after it rebuilds the table, which deletes the
  expired row. A turn that finds a registration due rebuilds the table before
  emitting, so it never emits for a row that rebuild found expired; an
  emission whose rebuild found the row unexpired may land after the expiry
  instant by at most that turn's duration. The same rebuild bounds
  cancellation: a cancel committed before it suppresses the turn's emission,
  and a cancel committed after it may be followed by at most one emission.
- the service does not exit on an empty table. With an empty table it
  publishes no deadline and blocks on its watcher (its `ctrl_in` lane and its
  notification interest in `weft.state.heartbeats`); with rows its only
  deadlines are the next due emission and the earliest lease expiry.
```

### `docs/specifications/05-Message_Flow_and_State.md` [MF-3.2] — replace the middle of the duplicate-services bullet, from "keyed `PING`/`PONG`; stale non-terminal rows" through "Service status summaries must prefer a separate live singleton owner over a" (lines 479–484; Task 6)

Current text:

>   keyed `PING`/`PONG`; stale non-terminal rows without live proof do not block
>   replacement. Helper-side endpoint validation classifies the endpoint as
>   service-candidate evidence and uses the shared singleton summary rules, so
>   terminal task-log proof or dead host-process proof for the endpoint TID
>   rejects stale endpoint rows even if older endpoint registry data remains.
>   Service status summaries must prefer a separate live singleton owner over a

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
  keyed `PING`/`PONG`; stale non-terminal rows without live proof do not block
  replacement. The registration helpers do not read the heartbeat endpoint.
  Service status summaries must prefer a separate live singleton owner over a
```

### `docs/specifications/05-Message_Flow_and_State.md` [MF-3.2] — replace the destination bullet from "Heartbeat `destination_queue` remains payload-directed runtime egress" through "must not block a later valid mutation." (lines 489–495; Tasks 6–7)

Current text:

> - Heartbeat `destination_queue` remains payload-directed runtime egress, not a
>   construction-fixed reactor role. Upsert validation rejects a destination
>   equal to the Heartbeat task's own inbox, reserved, or control lanes and rejects
>   BaseTask's reserved `weft.` system queue namespace before storing the
>   registration. An ordinary destination task queue remains valid. Rejection uses
>   the existing invalid-request lifecycle event and reserved-on-error policy and
>   must not block a later valid mutation.

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
- Heartbeat `destination_queue` remains payload-directed runtime egress, not a
  construction-fixed reactor role. `upsert_heartbeat` rejects the reserved
  `weft.` system queue namespace before writing. The service validates each row
  again when it builds its table and rejects a destination equal to the
  Heartbeat task's own inbox, reserved, or control lanes or in the `weft.`
  namespace. An ordinary destination task queue remains valid. A rejected row
  is reported with the existing invalid-request lifecycle event and deleted by
  its exact message ID, and it must not block a later valid registration.
```

### `docs/specifications/05-Message_Flow_and_State.md` [MF-3.2] — replace the mapping note from "`weft/core/tasks/heartbeat.py::HeartbeatTask._handle_work_message` validates" through "`tests/tasks/test_heartbeat.py`." (lines 512–516; Tasks 6–7)

Current text:

> `weft/core/tasks/heartbeat.py::HeartbeatTask._handle_work_message` validates
> payload-directed destinations against its resolved task-local roles and the
> reserved weft. system queue namespace before storing a registration;
> dynamic-egress rejection/progress coverage lives in
> `tests/tasks/test_heartbeat.py`.

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
`weft/core/heartbeat.py::upsert_heartbeat` and `cancel_heartbeat` write and
delete registration rows;
`weft/core/tasks/heartbeat.py::HeartbeatTask._rebuild_registrations`
validates payload-directed destinations against its resolved task-local roles
and the reserved weft. system queue namespace when it builds the table;
dynamic-egress rejection/progress coverage lives in
`tests/tasks/test_heartbeat.py`.
```

### `docs/specifications/05-Message_Flow_and_State.md` [MF-5] — replace the flow line "TaskMonitor heartbeat wake -> T{monitor_tid}.inbox -> bounded processor cycle" (line 548, inside the `text` fence)

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
TaskMonitor cycle deadline or inbox wake -> bounded processor cycle
```

### `docs/specifications/05-Message_Flow_and_State.md` [MF-5] — replace the clause from "TaskMonitor-owned built-in cycle worker group; the reactor stays available" through "exact deletes." (lines 717–720, the end of the bullet that opens "when enabled, the canonical manager supervises one internal")

Current text:

>   TaskMonitor-owned built-in cycle worker group; the reactor stays available
>   for task-local PING/STATUS/STOP/KILL, heartbeat registration, and schedule
>   bookkeeping while the worker scans, writes Monitor-store rows, and applies
>   exact deletes.

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
  TaskMonitor-owned built-in cycle worker group; the reactor stays available
  for task-local PING/STATUS/STOP/KILL and schedule bookkeeping while the
  worker scans, writes Monitor-store rows, and applies exact deletes.
  The monitor's only clock is its own cycle deadline, published through
  `next_wait_timeout()`. The first cycle runs on the first enabled turn and
  anchors a fixed periodic grid: ticks fall at that cycle's start plus
  whole multiples of `WEFT_TASK_MONITOR_INTERVAL_SECONDS`, and the grid is
  never re-anchored. A cycle runs at each grid tick and consumes, at its
  start, every tick at or before that start. A message on
  `T{monitor_tid}.inbox` requests an immediate cycle and does not move the
  grid. If one or more ticks pass while a cycle runs, the monitor owes
  exactly one immediate follow-up cycle; the owed follow-up persists across
  the bookkeeping that follows the cycle, including a control-cleanup pass,
  until a cycle starts. While a cleanup policy reports remaining backlog,
  the next cycle is due at the earlier of the grid-derived time and
  `WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS` after the previous cycle's
  results are committed. With no inbox or control message, no worker
  result, and no due deadline, the monitor runs no turns.
```

Rule-versus-code check. Done at `b7eed9f9`: first cycle — `_first_cycle_pending`
starts `True` (line 4497) and `next_wait_timeout` returns 0.0 for it
(lines 4974–4975); catch-up predicate — `progress_requires_catchup` at
lines 5981–5988 and 6386–6393; inbox wake — `_handle_work_message` line
5074; re-arm after commit — lines 5989 and 6394 (finish-anchored today, the
two sites Task 3 item 11 replaces). New code, proven after Task 3: grid
anchor, start-side tick consumption, and follow-up reset in
`_run_monitor_cycle` (C1, C2, C4); the deadline arithmetic in
`_arm_next_cycle_deadline` (C3 matrix); the chained re-arm (C4); zero turns
when quiet — `MultiQueueWatcher` confirms backend hints before returning
(R1).

If the owner picks F2's alternative, this block's cadence sentences revert
to "each later cycle is due `WEFT_TASK_MONITOR_INTERVAL_SECONDS` after the
previous cycle's results are committed, or
`WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS` after them while a cleanup
policy reports remaining backlog. A message on `T{monitor_tid}.inbox`
requests an immediate cycle." before review closes.

### `docs/specifications/05-Message_Flow_and_State.md` `### Current Queue Lifecycle Management` — replace the sentence from "cycle stops because the FIFO pass hit its scan limit" through "full heartbeat interval." (lines 1999–2001)

Current text:

>   cycle stops because the FIFO pass hit its scan limit, the monitor uses
>   `WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS` for the next wake instead of the
>   full heartbeat interval.

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
  cycle stops because the FIFO pass hit its scan limit, the monitor uses
  `WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS` for its next cycle deadline
  when that is earlier than the next grid tick.
```

### `docs/specifications/01-Core_Components.md` [CC-2.3] — replace "monitor is a reactor: it owns task-local control, heartbeat registration," through "scheduling, and commits cached diagnostics from worker results. Custom" (lines 643–644)

Replacement (`docs/specifications/01-Core_Components.md`):

```text
  monitor is a reactor: it owns task-local control, its own cycle deadline,
  and commits cached diagnostics from worker results. Custom
```

### `docs/specifications/01-Core_Components.md` [CC-2.3] — replace lines 664–666 from "The launcher asks the persistent monitor" through "The supervised monitor builds"

Current text:

>   The launcher asks the persistent monitor for its next wait timeout so the
>   monitor sleeps until heartbeat/local due time or task-local input instead of
>   polling at the default task-process interval. The supervised monitor builds

Replacement (`docs/specifications/01-Core_Components.md`):

```text
  The launcher asks the persistent monitor for its next wait timeout so the
  monitor sleeps until its own cycle deadline or task-local input instead of
  polling at the default task-process interval. The supervised monitor builds
```

Not edited: [CC-2.3] line 574 ("does not know about ... heartbeat
registration") describes the `ServiceTask` layer and stays true.

### `docs/specifications/03-Manager_Architecture.md` [MA-1] item 7 — replace lines 289–291, from "their own autostart manifests. The built-in heartbeat service is desired" through "`TaskMonitor` is an `ensure` service when `WEFT_TASK_MONITOR_ENABLED` is"

Current text:

>    their own autostart manifests. The built-in heartbeat service is desired
>    only when an enabled internal dependent needs it. The internal
>    `TaskMonitor` is an `ensure` service when `WEFT_TASK_MONITOR_ENABLED` is

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```text
   their own autostart manifests. The canonical manager runs the built-in
   heartbeat service as a standing internal service, whatever the monitor
   flags say: started at manager startup, relaunched by ordinary managed-service
   convergence if it exits, and stopped like the other internal services. The
   manager neither reads nor deletes `weft.state.heartbeats`; the heartbeat
   service is its only reader and deleter ([MF-3.2]). The internal `TaskMonitor` is an
   `ensure` service when `WEFT_TASK_MONITOR_ENABLED` is
```

Line 292 ("true. `WEFT_LIVENESS_MONITOR_ENABLED` independently makes
LivenessMonitor") follows unchanged. This wording implements the owner's F1
ruling of 2026-11-02 (on-demand launch from a registrations queue).

### `docs/specifications/00-Quick_Reference.md` — replace the `WEFT_TASK_MONITOR_INTERVAL_SECONDS` row (line 196)

Current text:

> | `WEFT_TASK_MONITOR_INTERVAL_SECONDS` | Heartbeat wake interval for the supervised task monitor. Must be at least the heartbeat minimum. |

Replacement (`docs/specifications/00-Quick_Reference.md`):

```text
| `WEFT_TASK_MONITOR_INTERVAL_SECONDS` | Cycle grid interval, in whole seconds, for the supervised task monitor's own deadline: cycles run on a fixed grid anchored at the monitor's first cycle, with one immediate follow-up after a cycle that overran a tick. Must be at least 60. |
```

### `docs/specifications/00-Quick_Reference.md` — add one row to the global queue table, after the `weft.state.pipelines` row (line 42; Task 6)

Replacement (`docs/specifications/00-Quick_Reference.md`):

```text
| `weft.state.pipelines` | Active pipeline registry | No (runtime state) |
| `weft.state.heartbeats` | Heartbeat registrations: one row per live registration, keyed by `heartbeat_id`, naming its registrant TID; each row carries its own lease and expires when it ends; the heartbeat service, a standing internal service of the canonical manager, is its only reader and deleter ([MF-3.2]) | No (runtime state) |
```

### `docs/specifications/07-System_Invariants.md` [QUEUE.7] — replace the sentence "The Manager observes each task-state queue awaiting its startup feedback, with active notification interests bounded by the [MA-1.8] startup window." (lines 181–182; Tasks 7–8)

Current text:

>   The Manager observes each task-state queue awaiting its startup feedback,
>   with active notification interests bounded by the [MA-1.8] startup window.

Replacement (`docs/specifications/07-System_Invariants.md`):

```text
  The Manager observes each task-state queue awaiting its startup feedback,
  with active notification interests bounded by the [MA-1.8] startup window.
  The heartbeat service also holds one standing notification interest in
  `weft.state.heartbeats` ([MF-3.2]); that queue is a fixed support route that
  it peeks, never a delivery role. The heartbeat service is the queue's only
  reader and deleter: it deletes only rows it rejects, rows a newer row of the
  same `heartbeat_id` supersedes, and rows whose lease has expired. The
  Manager holds no interest in, reads, or deletes that queue.
```

The [QUEUE.7] `_Implementation mapping_` bullet gains
`weft/core/tasks/heartbeat.py::HeartbeatTask._activity_wait_queues` and
`weft/core/tasks/heartbeat.py::HeartbeatTask._has_pending_observation` after
`weft/core/manager.py::Manager._has_pending_observation`. Not a Replacement
block (one long line); Task 4 verifies it by grep.

### `## Related Plans` backlinks (all five files)

Append one bullet to `## Related Plans` in 05 (line 2095), 01 (the first
one, line 36), 03 (line 34), 00 (line 248), and 07 (line 1392):

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
- [TaskMonitor: One Clock, Not Two](../plans/2026-11-02-task-monitor-heartbeat-clock-plan.md) removes the TaskMonitor heartbeat registration; its cycle deadline is its only clock ([MF-3.2], [MF-5]). Heartbeat registrations become rows on `weft.state.heartbeats`, each with its own lease; the heartbeat runs as a standing internal service of the canonical Manager and is the only reader and deleter of that queue, and the Manager neither reads nor deletes it ([MF-3.2]).
```

Use the same bullet text in 01 (citing [CC-2.3]), 03 (citing [MA-1] item
7), 00 (citing the `WEFT_TASK_MONITOR_INTERVAL_SECONDS` and
`weft.state.heartbeats` rows), and 07 (citing [QUEUE.7]). Those four
bullets are not Replacement blocks; Task 4 verifies them by grep.

## 5. Tasks

Dependency order: 0 → 1 → 2 → 3 → 6 → 7 → 8 → 4 → 5 (Tasks 6–8 are
the F1 extension, numbered after the original tasks and placed in the text
where they run). Tasks 3, 6–8, and 4 land in **one commit** (strategy B);
nothing is committed between them. Commits are made only when the owner
asks.

### Task 0. Read, answer, record

- Outcome: the implementer can name the edit points and the retained clock.
- Do: read §3 "Read first"; write answers to the two comprehension
  questions in `## Execution log` under a dated "Implementer" heading.
- Done when: both answers match the expected answers in substance.

### Task 1. Independent review of plan and delta (before any code)

- Outcome: a cross-family reviewer has read this plan, its
  `## Proposed Spec Delta`, and the code, and every finding has a
  disposition in `## Review Log`.
- Invocation: Tier-1 via `skills/call-agent/SKILL.md`, using the
  recommended prompt in `docs/agent-context/runbooks/writing-plans.md` §8,
  with the owner's one-scoped-round rule. Native same-family subagents only
  as a logged Tier-2 fallback.
- Reviewer inputs: this plan; the five spec files at `b7eed9f9`;
  `weft/core/monitor/task_monitor.py`; `weft/core/heartbeat.py`;
  `weft/core/tasks/heartbeat.py`; `weft/core/manager.py` lines 7140–7165,
  1886–1930, and 8355–8380; `docs/program-theory.md` [ALT-WT-004],
  [WT-2a.2], [WT-2a.11], [WT-2a.13]; the older draft's Observable-Difference
  Register (for fork F2). The F1 extension (Tasks 6–8) gets its own scoped
  round, and the lease change of 2026-11-03 (fork F5 closed) one more scoped
  round; the original round 1–2 findings stay closed.
- Stop if: the reviewer flags `RECONSIDERATION REQUESTED` against an
  [ALT-WT-*] entry, or the owner picks a non-default fork — re-classify and
  revise the delta before Task 2.
- Done when: review findings are dispositioned, including the scoped rounds
  on Tasks 6–8 and on the lease change; F1 is closed (owner ruling
  2026-11-02); F5 is closed (owner ruling 2026-11-03); F2 and F3 have owner
  answers (or the defaults are explicitly accepted); F4 is answered at
  Task 5.

### Task 2. RED: write the failing tests (no production edit)

- Outcome: one modified and six new tests in one file. R1 (both cases),
  R2, and C1–C4 fail for the right reason at `b7eed9f9`; G1 is green; the
  before counts are recorded.
- Files to touch: `tests/tasks/test_task_monitor.py` only.
- Read first: §3 test list; `tests/tasks/test_task_execution.py` lines
  142–157 and 638–678; `tests/tasks/test_heartbeat.py` lines 284–322;
  `weft/core/tasks/heartbeat.py::HeartbeatTask._process_reactor_turn`
  (one control row or one registration row per turn, before emission);
  the older draft's Task 3 tests 1–3b (shape source for C1–C4).
- Do not mock: the TaskMonitor, the HeartbeatTask, the broker, the queues,
  or the reactor wait. Use `broker_env` and `workdir` from the shared
  harness. The only stand-ins are the two `raising=False` registration
  patches (R1, G1, C1–C4; kept after Task 3, see §4 hidden couplings), the
  existing clock patches (G1, C1–C4), C2's clock-advancing processor, and C2's rebinding of that processor's module attribute to `recording_processor` before cycle 2.

R1 — `test_task_monitor_quiet_interval_runs_zero_turns_with_heartbeat_emitting`,
parametrized over `heartbeat_mode` in `("emitting", "unavailable")`, and
taking the `thread_exception_guard` fixture:

1. Config: `WEFT_TASK_MONITOR_ENABLED=1`, `WEFT_TASK_MONITOR_INTERVAL_SECONDS=60`,
   `WEFT_TASK_MONITOR_MODE=custom`,
   `WEFT_TASK_MONITOR_PROCESSOR=tests.tasks.test_task_monitor:recording_processor`.
2. `emitting` only: construct a real
   `HeartbeatTask(db_path, make_heartbeat_taskspec(heartbeat_tid, workdir))`
   (import `make_heartbeat_taskspec` from `tests.tasks.test_heartbeat` and
   `HeartbeatTask` from `weft.core.tasks.heartbeat`; add
   `HEARTBEAT_MIN_INTERVAL_SECONDS` to the module's `weft._constants`
   import, which lacks it today). The test thread is its only driver.
   Write one probe registration to its inbox (`{"action": "upsert",
   "heartbeat_id": "probe", "interval_seconds":
   HEARTBEAT_MIN_INTERVAL_SECONDS, "destination_queue":
   "probe.heartbeat.queue", "message": "tick"}`).
3. Registration stand-ins, kept permanently (the interception that keeps a
   restored registration away from real `ensure_manager()`):
   `monkeypatch.setattr(task_monitor_mod, "cancel_heartbeat", lambda
   *args, **kwargs: None, raising=False)` and
   `monkeypatch.setattr(task_monitor_mod, "upsert_heartbeat", stand_in,
   raising=False)`, where `stand_in` in `emitting` mode calls
   `make_queue(f"T{heartbeat_tid}.inbox").write(json.dumps({"action":
   "upsert", "heartbeat_id": heartbeat_id, "interval_seconds":
   interval_seconds, "destination_queue": destination_queue, "message":
   message}))` (create the queue inside the stand-in so it is opened on the
   Monitor's thread), and in `unavailable` mode raises
   `RuntimeError("heartbeat unavailable")` (the injected failure).
4. Construct the TaskMonitor with `make_task_monitor_taskspec`; wrap its
   turn: `monkeypatch.setattr(task, "_process_reactor_turn", counted)`,
   where `counted` appends `time.monotonic()` to a list and calls the
   original bound method.
5. Start `threading.Thread(target=task.run_until_stopped)`. Settle: wait
   (bounded at 10 s) until `len(PROCESSOR_REQUESTS) == 1` and no turn has
   been recorded for 0.5 s. Record `baseline = len(turns)`.
6. `emitting` only, in this order:
   a. Drain: call `heartbeat.process_once()` until
      `make_queue(f"T{heartbeat_tid}.inbox").peek_one() is None`, at most 8
      turns; assert the bound was not hit. Each turn ingests at most one
      registration row, and at `b7eed9f9` two are pending (the probe and the
      Monitor's), so one turn is not enough. Assert `"probe" in
      heartbeat._registrations`.
   b. Force: for every key in `heartbeat._registrations`, set `next_due_at`
      in the past; clear `_due_heap`; push each entry.
   c. Emit: call `heartbeat.process_once()` once. The inbox is empty, so
      this turn reaches `_emit_due_registrations`.
7. Wait `QUIET_WINDOW_SECONDS = 2.5` (a module constant with a one-line
   docstring comment: the window is the measured quantity, not a
   synchronization sleep).
8. Assert in this order: (`emitting`) `make_queue("probe.heartbeat.queue").read_one()
   == "tick"` (the emitter ran); `thread.is_alive()` with message "monitor
   driver died during the quiet window" (a crashed driver also runs zero
   turns); `turns_in_window == 0` with message
   `f"monitor ran {turns_in_window} turns over {QUIET_WINDOW_SECONDS}s"`;
   (`emitting`) `f"task-monitor:{task.tid}" not in heartbeat._registrations`.
9. Teardown (in `finally`): `task.stop(join=False)`; join the thread (5 s,
   assert not alive); in `emitting` mode only, `heartbeat.stop(join=False)`
   and `heartbeat.cleanup()` (no heartbeat exists in `unavailable` mode).
   `thread_exception_guard` fails the test at teardown if the drive thread
   raised.

Expected RED at `b7eed9f9`: `unavailable` fails at the turn-count assertion
with about 2 turns (the retry timer; E9); `emitting` passes the probe and
liveness assertions and fails at the turn-count assertion with ≥1 turn (the
emission into the Monitor's inbox). Any other failure (settle timeout,
drain bound hit, probe missing, dead driver, AttributeError, an
`ensure_manager` call) is a broken test, not RED; fix the test.

R2 — in `test_task_monitor_ping_includes_health_and_preserves_task_log`,
replace the `assert extended["heartbeat"] == {...}` block (line 3181) with
`assert "heartbeat" not in extended`. RED at `b7eed9f9` (the block is
present).

Cadence tests C1–C4 (F2 default; dropped if the owner picks F2's
alternative). Each uses one fake clock, `clock = {"value": 1000.0}`, patched
into `task_monitor_mod.time` (`SimpleNamespace(monotonic=lambda:
clock["value"], time_ns=time.time_ns, sleep=time.sleep)`), interval 60,
catch-up 2. Times below are offsets from 1000.0. C1–C4 carry the two
`raising=False` no-op stand-ins (no-op upsert, no-op cancel), which also
keep the retry timer out of the arithmetic at `b7eed9f9`. C4 needs them as
much as C1–C3 do: its real `process_once()` reaches
`_ensure_heartbeat_registered` (`task_monitor.py` line 5032), which at
`b7eed9f9` calls `upsert_heartbeat` and, with no live endpoint, real
`manager_runtime.ensure_manager()`.

C1 — `test_task_monitor_inbox_poke_does_not_move_cycle_grid`: custom mode
with `recording_processor`. Cycle 1 at 0 (`process_once` +
`drive_task_monitor_until_idle`). Advance to 50; write one message to
`T{tid}.inbox`; `process_once` + drive runs cycle 2; assert
`len(PROCESSOR_REQUESTS) == 2`. Assert `task._next_cycle_due_monotonic ==
pytest.approx(1060.0)`. Advance to 59.5; assert `next_wait_timeout() ==
pytest.approx(0.5)`. Advance to 60; assert `next_wait_timeout() == 0.0` and
that `process_once` + drive runs cycle 3 with no inbox row. After cycle 3
(still 60) assert `task._next_cycle_due_monotonic == pytest.approx(1120.0)`
and `next_wait_timeout() > 0.0`: the tick-started cycle consumed its own
tick and owes no follow-up. RED at `b7eed9f9`: after the poke the deadline
is `1050 + 60 = 1110`.

C2 — `test_task_monitor_overrun_cycle_is_followed_by_exactly_one_cycle`:
custom mode with a module-level processor that wraps `recording_processor`
and advances a module-level fake clock by 90 s while it runs (the test
binds that clock to its `clock` dict and resets it in `finally`). Cycle 1
starts at 0 and finishes at 90; assert `next_wait_timeout() == 0.0`. Then,
**before** driving cycle 2, swap in the fast processor:
`monkeypatch.setattr` the module attribute that
`WEFT_TASK_MONITOR_PROCESSOR` names to `recording_processor` (the worker
resolves the configured `module:function` by `getattr` on every cycle,
`_run_custom_monitor_processor` at `task_monitor.py` line 6120 via
`weft/core/monitor/runtime.py::resolve_task_monitor_processor`, so a swap
after cycle 2 starts would let the slow processor advance the clock to 180
and overrun again). Now the next `process_once` + drive runs cycle 2 at 90
with no inbox row; it consumes tick 60 at its start and the clock stays at
90. After cycle 2 assert `len(PROCESSOR_REQUESTS) == 2`,
`next_wait_timeout() > 0.0` (no third immediate cycle), and
`task._next_cycle_due_monotonic == pytest.approx(1120.0)`: the grid tick is
120, not `90 + 60 = 150`. RED at `b7eed9f9`: the deadline after cycle 1 is
`1150`, so `next_wait_timeout() == 0.0` fails and no follow-up runs.

C3 — `test_task_monitor_arm_next_cycle_deadline_matrix`: direct call of
`TaskMonitor._arm_next_cycle_deadline(catchup_pending=...)` on a
constructed monitor (no turn), parametrized over `(grid_preset,
follow_up_preset, now, catchup_pending, expected_due, expected_grid_after,
expected_follow_up_after)`, all times as offsets:

- before the tick (grid 60, nothing owed): `(60, False, 10, False, 60, 60,
  False)`, `(60, False, 90, False, 90, 120, True)`, `(60, False, 150,
  False, 150, 180, True)`, `(60, False, 10, True, 12, 60, False)`, `(60,
  False, 59.5, True, 60, 60, False)`, `(60, False, 90, True, 90, 120,
  True)`;
- after a tick-started cycle consumed tick 60 (grid 120): `(120, False,
  60.1, False, 120, 120, False)`, `(120, False, 90, False, 120, 120,
  False)`;
- chained re-arm: `(120, True, 75, False, 75, 120, True)`, `(120, True, 77,
  True, 77, 120, True)`, and the negative control `(120, False, 75, False,
  120, 120, False)`.

Preset `_cycle_grid_due_monotonic` and `_overrun_follow_up_pending`, set
the clock to `now`, call the method, and assert the return value equals
`expected_due - now`, `_next_cycle_due_monotonic == expected_due`,
`_cycle_grid_due_monotonic == expected_grid_after`, and
`_overrun_follow_up_pending is expected_follow_up_after`. The private seam
is the [MF-5] cadence contract, and both production re-arm sites call it.
Start-side consumption and the flag reset live in `_run_monitor_cycle` and
are covered by C1, C2, and C4, not here. RED at `b7eed9f9`: AttributeError
on the missing method (the one case where AttributeError is the right
reason).

C4 — `test_task_monitor_overrun_builtin_cycle_with_control_cleanup_runs_exactly_one_follow_up`:
built-in mode (the default `WEFT_TASK_MONITOR_MODE`). Drive one built-in
cycle that starts at 0 and whose worker result is committed at 70 with
runtime cleanup ready, so the terminal control-cleanup worker starts; then
commit the control-cleanup result at 75. Reuse the seam the existing
control-cleanup tests use (`_maybe_start_terminal_control_cleanup_worker`
and `_handle_control_cleanup_worker_result`; locate their tests by symbol
in this file), with the same two `raising=False` no-op stand-ins as C1–C3,
kept permanently. After the cleanup result assert `next_wait_timeout() ==
0.0`; the next `process_once` starts a cycle with no inbox row; after that
cycle finishes (fast, still 75) assert `next_wait_timeout() > 0.0` and
`task._next_cycle_due_monotonic == pytest.approx(1120.0)`: exactly one
follow-up. RED at `b7eed9f9`: the cleanup result re-arms to `1075 + 60`, so
`next_wait_timeout() > 0.0` after it.

G1 — `test_task_monitor_maintenance_fires_on_own_cycle_deadline_without_inbox_wake`
(guard, green before and after; it pins that the kept clock fires a cycle
and self-maintenance with no inbox message; it asserts nothing about the
anchor, which is C1–C4's job, so it stays green under either F2 option):

1. Config as in the existing maintenance test at line 11633
   (`WEFT_TASK_MONITOR_MODE=delete`, `WEFT_TASK_MONITOR_LOG_SINK=none`,
   interval 60). Add the same two `raising=False` stand-ins as R1
   (no-op upsert, no-op cancel), kept permanently.
2. One shared fake clock `clock = {"value": 1000.0}` patched into both
   `task_monitor_mod._monitor_monotonic` and `task_monitor_mod.time`
   (`SimpleNamespace(monotonic=lambda: clock["value"],
   time_ns=time.time_ns, sleep=time.sleep)`).
3. `task.process_once(); drive_task_monitor_until_idle(task)`; read the
   maintenance `last_run_at_ns` through `_read_status_reply`.
4. Write and read two rows on a probe queue (claimed rows), as the existing
   test does.
5. Advance `clock["value"]` to `1000.0 + 3600.0 + 61.0`. Do **not** write
   to the inbox and do **not** touch `task._next_cycle_due_monotonic`.
6. Assert the inbox has no pending row and `task.next_wait_timeout() ==
   0.0`; then `task.process_once(); drive_task_monitor_until_idle(task)`.
7. Assert the probe queue total is 0 (vacuumed) and the maintenance
   `last_run_at_ns` advanced.

- Verify (record command and output in the execution log):

  ```bash
  . ./.envrc
  ./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py -q -k "quiet_interval_runs_zero_turns_with_heartbeat_emitting or ping_includes_health_and_preserves_task_log or inbox_poke_does_not_move_cycle_grid or overrun_cycle_is_followed_by_exactly_one_cycle or arm_next_cycle_deadline_matrix or overrun_builtin_cycle_with_control_cleanup_runs_exactly_one_follow_up or maintenance_fires_on_own_cycle_deadline_without_inbox_wake"
  bin/pytest-pg "tests/tasks/test_task_monitor.py::test_task_monitor_quiet_interval_runs_zero_turns_with_heartbeat_emitting"
  ```

- Stop if: R1 cannot settle without a sleep loop longer than the bound, or
  the test needs a mock of the reactor wait — the real path is wrong, not
  the test; re-read [CC-2.2.1]. C4 cannot reach the chained re-arm through
  the existing control-cleanup seam — record it and re-read the hidden
  coupling before adding any seam.
- Done when: R1 (both cases), R2, and C1–C4 fail with the assertions named
  above on SQLite; R1 fails the same way on PostgreSQL; G1 passes; the
  before counts per backend and case are in the execution log.

### Task 3. GREEN: remove the second clock (core work; same commit as Tasks 6–8 and 4)

- Outcome: TaskMonitor has one clock, which keeps the heartbeat-live
  fixed-rate period and poke independence (F2 default; grid phase anchored
  at the first cycle start, register row 3b); the RED tests pass; nothing
  else changes behavior.
- Files to touch: `weft/core/monitor/task_monitor.py`, `weft/_constants.py`,
  `tests/tasks/test_task_monitor.py`.
- Edits in `weft/core/monitor/task_monitor.py`, exactly:
  1. Delete the import of `cancel_heartbeat, upsert_heartbeat` (line 84)
     and of the two `TASK_MONITOR_HEARTBEAT_*` constants (lines 60–61).
  2. Delete the four fields at lines 4531–4534. Next to
     `_next_cycle_due_monotonic` (line 4535) add
     `self._cycle_grid_due_monotonic = 0.0` (comment: "next periodic grid
     tick; 0.0 until the first cycle starts") and
     `self._overrun_follow_up_pending = False` (comment: "one immediate
     follow-up cycle is owed after an overrun; cleared when a cycle
     starts").
  3. `next_wait_timeout`: delete lines 4966–4967; rewrite the docstring to
     say the monitor's only clock is its cycle deadline and that queue
     readiness belongs to the shared wait (keep the `Spec:` style: cite
     [CC-2.5], [MF-5]). The new docstring does not contain the word
     "heartbeat" (G-2 counts on it).
  4. `_process_reactor_turn`: delete the `_ensure_heartbeat_registered()`
     call (line 5032). Nothing else in the method changes.
  5. Delete the `_handle_control_command` override (lines 5076–5083); STOP
     and KILL are handled by `BaseTask._handle_control_command`. The
     imports `CONTROL_STOP`, `CONTROL_KILL`, and `ControlRequest` are used
     only by that override (2 occurrences each at `b7eed9f9`: import plus
     use); delete them too. `QueueMessageContext` stays (still used by
     `_handle_work_message`).
  6. `_task_monitor_pong_extension`: delete `next_registration_attempt`
     and the `"heartbeat"` block. Keep `schedule` and every other key.
  7. Delete `_ensure_heartbeat_registered` and `_cancel_heartbeat`
     (lines 5714–5759).
  8. Replace `self._last_error = self._heartbeat_error` with
     `self._last_error = None` at the three sites (lines 5975, 5995, 6377).
  9. Grid start (F2 default). At the top of `_run_monitor_cycle` (line
     5779), before the mode branch so the built-in and custom modes share
     it: `start = time.monotonic()`; `interval =
     self._monitor_config.interval_seconds`; if
     `self._cycle_grid_due_monotonic == 0.0`, set it to `start + interval`
     (anchored once, at the first cycle start; never re-anchored);
     otherwise `while self._cycle_grid_due_monotonic <= start:
     self._cycle_grid_due_monotonic += interval` (a cycle consumes every
     tick at or before its start, so a tick-started cycle does not count
     its own tick as an overrun). Then `self._overrun_follow_up_pending =
     False` (a cycle started; the owed follow-up is being paid).
  10. Add one method next to `_finish_monitor_cycle`:
      `_arm_next_cycle_deadline(self, *, catchup_pending: bool) -> float`.
      Body: `now = time.monotonic()`; `interval =
      self._monitor_config.interval_seconds`; if
      `self._cycle_grid_due_monotonic <= now`, set
      `self._overrun_follow_up_pending = True`; `while
      self._cycle_grid_due_monotonic <= now: self._cycle_grid_due_monotonic
      += interval`; `due = now if self._overrun_follow_up_pending else
      self._cycle_grid_due_monotonic`; if `catchup_pending`, `due =
      min(due, now + self._monitor_config.catchup_interval_seconds)`; store
      `self._next_cycle_due_monotonic = due`; return `due - now`. The flag,
      not a local, keeps the chained second re-arm at `due = now`; only
      item 9 clears it. Docstring: the [MF-5] cadence rule in two lines,
      then `Spec: [MF-5]`; no "heartbeat" word.
  11. Replace both re-arm blocks — the `next_interval_seconds = (...)`
      choice and the `_next_cycle_due_monotonic` assignment at lines
      5984–5989 and 6389–6394 — with `next_interval_seconds =
      self._arm_next_cycle_deadline(catchup_pending=self._last_catchup_pending)`.
      The `_last_catchup_pending` computation above each block is
      unchanged; the log fields at lines 6065 and 6405 keep their key and
      now log the returned value.
  12. Do not touch `_handle_work_message`, `_wake_requested`,
      `_first_cycle_pending` handling, the `should_run` test in
      `_process_reactor_turn`, the cleanup classification at lines
      2860–2863, or any other method.
- Edits in `weft/_constants.py`: delete
  `TASK_MONITOR_HEARTBEAT_REGISTRATION_RETRY_SECONDS` and
  `TASK_MONITOR_HEARTBEAT_STARTUP_TIMEOUT_SECONDS` with their docstrings
  (lines 1331–1335); change the `WEFT_TASK_MONITOR_INTERVAL_SECONDS_DEFAULT`
  docstring to "Default cycle grid interval for the supervised task
  monitor's own deadline." and the `_parse_task_monitor_interval_seconds`
  docstring to "Parse the task-monitor cycle interval; the floor is
  `HEARTBEAT_MIN_INTERVAL_SECONDS`." Keep the floor and its error text.
- Edits in `tests/tasks/test_task_monitor.py`:
  1. Remove the 119 pre-existing `monkeypatch.setattr(task_monitor_mod,
     "upsert_heartbeat", ...)` statements (not the Task 2 stand-ins) and
     the import of `TASK_MONITOR_HEARTBEAT_REGISTRATION_RETRY_SECONDS`
     (line 55).
  2. Delete `test_task_monitor_stop_survives_blocking_heartbeat_probe`
     (line 1004) and `test_task_monitor_heartbeat_failure_records_health_but_still_cycles`
     (line 10118): their subjects are retired (register rows 5 and 7).
  3. Keep the `raising=False` stand-ins in R1, G1, and C1–C4. Separately,
     as a verification step only (never a committed state): delete those
     stand-in lines in the working tree, run R1, G1, and C1–C4, paste the
     passing output in the execution log, then restore the lines with
     Edit. This proves nothing calls the removed names.
- Reuse, do not add: no helper, wrapper, flag, or constant is introduced
  beyond `_arm_next_cycle_deadline` and its two fields.
- Grep gates (paste output in the execution log):
  - G-1: `grep -rn "upsert_heartbeat\|cancel_heartbeat\|ensure_heartbeat_service" weft --include=*.py`
    → hits only in `weft/core/heartbeat.py`.
  - G-2: `grep -ci heartbeat weft/core/monitor/task_monitor.py` → `5`.
  - G-3: `grep -n "upsert_heartbeat\|cancel_heartbeat" tests/tasks/test_task_monitor.py`
    → only the `raising=False` stand-in lines inside R1, G1, and C1–C4
    (record the count; every one of the 119 pre-existing sites is gone).
  - G-4: `grep -rn "TASK_MONITOR_HEARTBEAT_" weft tests --include=*.py` →
    no output.
- Verify:

  ```bash
  . ./.envrc
  ./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py tests/specs/test_reactor_architecture.py -q
  ```

- Stop if: any test outside the ones named here fails because it relied on
  the heartbeat block, the retry wake, or finish-anchored re-arming (record
  it and re-read the difference register before editing it; a test that
  pins `finish + interval` pins the D behavior of F2's alternative); a
  second wait path, a timer, or a helper other than
  `_arm_next_cycle_deadline` starts to appear; any file under
  `weft/core/monitor/` other than `task_monitor.py` wants an edit.
- Done when: the verify command passes and G-1 to G-4 match.

### Tasks 6–8. On-demand heartbeat (owner rulings on F1, 2026-11-02, and F5, 2026-11-03)

Read these three tasks after Task 3 and before Task 4: that is their execution
order. Each starts RED (tests first, run, failure assertion recorded in the
execution log), then GREEN. Tasks 6 and 7 keep their numbering but land as
one unit with one GREEN gate: write and run the RED tests of both, then make
the GREEN edits of both, then run the union of their Verify commands once.
Task 6 alone cannot reach GREEN (it removes `HeartbeatMutation` while
`_handle_work_message` still uses it, and its verification includes a test
Task 7 migrates), and no temporary compatibility model bridges them. Every
production edit here lands in the same commit as Tasks 3 and 4. Shared
names, used verbatim by every task:

- queue constant `WEFT_HEARTBEAT_REGISTRATIONS_QUEUE: Final[str] =
  "weft.state.heartbeats"` in `weft/_constants.py`, placed after
  `WEFT_PIPELINES_STATE_QUEUE` in the runtime-state group, docstring
  "Runtime queue of heartbeat registrations; one row per live registration
  [MF-3.2]." The `weft.state.` prefix is deliberate: registrations are
  runtime-scoped (each names a live registrant), and `WEFT_STATE_QUEUE_PREFIX`
  queues are excluded from dumps, like `weft.state.streaming`, the closest
  precedent (also keyed to an owner TID). The brief's example name
  `weft.heartbeat.registrations` would be dumped and loaded into a context
  where its registrants do not exist.
- row shape (JSON object, nothing else): `{"heartbeat_id": str,
  "registrant_tid": str, "interval_seconds": int, "lease_seconds": int,
  "destination_queue": str, "message": Any}`.
- row model: `HeartbeatRegistrationRow` in `weft/core/tasks/heartbeat.py`,
  replacing `HeartbeatMutation` (`extra="forbid"`; `heartbeat_id` stripped
  and non-empty; `registrant_tid` satisfies
  `weft.helpers.message_ids.is_task_tid`; `HEARTBEAT_MIN_INTERVAL_SECONDS
  <= interval_seconds <= lease_seconds <= HEARTBEAT_MAX_LEASE_SECONDS`, one
  chain: the lease's floor is the row's own interval, the interval's
  maximum is the lease (no separate interval-maximum or minimum-lease
  constant). The
  floor keeps a lease from being shorter than the cadence it asks for; an
  accepted registration whose lease is close to its interval can expire
  before its first tick when the service's launch and build take longer
  than the difference (expiry is the write plus the lease, the first due
  time is the build plus the interval, and the build follows the write); a
  registrant that needs a first tick leaves that margin in its lease;
  `destination_queue`
  non-empty and not starting with `WEFT_QUEUE_NAMESPACE_PREFIX`; `message`
  JSON-serializable). `weft/core/heartbeat.py` imports it from there;
  nothing imports in the other direction, so no cycle. The same model
  validates at every reader and writer: the writer (`upsert_heartbeat`),
  the table build (`HeartbeatTask._rebuild_registrations`), the Manager's
  desire predicate (`Manager._heartbeat_registrations_present`), and
  `weft status` (`weft/commands/system.py::_service_enabled`). It carries
  exactly two methods, and no helper function sits beside them:
  `expires_at_ns(self, message_id: int) -> int` returns `message_id +
  self.lease_seconds * 1_000_000_000`, and `expired(self, message_id: int,
  now_ns: int) -> bool` returns `now_ns >= self.expires_at_ns(message_id)`.
  `message_id` is the row's SimpleBroker message ID; each caller passes its
  own `time.time_ns()` reading as `now_ns`. The literal `1_000_000_000`
  follows `weft/core/manager.py`, which uses it for the same conversion
  (E20); no constant is added for it.
- lease maximum: `HEARTBEAT_MAX_LEASE_SECONDS: Final[int] = 7 * 24 * 60 *
  60` (604,800 s, seven days) in `weft/_constants.py` beside
  `HEARTBEAT_MIN_INTERVAL_SECONDS`, docstring "Longest heartbeat
  registration lease, and so the longest interval (a lease is at least its
  interval). Seven days is the current policy value, set by the owner on
  2026-11-03; it is not intrinsic to Weft and may be raised or lowered. A
  registrant that wants ticks for longer resubmits before its lease ends
  [MF-3.2]." Why a bound must exist at all: a JSON integer has no size
  bound, and the heartbeat converts `time.monotonic() + float(interval)`
  and the seconds until expiry for `next_wait_timeout()` to float, so an
  unbounded value is a poison row that crashes the service on every relaunch
  (§4, restart backoff; checked 2026-11-02: `1000.0 + 10**400` raises
  `OverflowError: int too large to convert to float`; 2026-11-03: `(10**400 *
  1_000_000_000) / 1_000_000_000` raises `OverflowError: integer division
  result too large for a float`). Any value below `2**53` is exact in a
  float, so the policy value is far inside the representable range and the
  float bound never decides. Today the same unbounded value reaches
  `time.monotonic() + float(mutation.interval_seconds)` in
  `_handle_work_message` unvalidated and raises there. There is no separate
  interval maximum: the chain `interval_seconds <= lease_seconds <=
  HEARTBEAT_MAX_LEASE_SECONDS` bounds the interval through the lease.
- expiry is computed, never observed: `expires_at_ns = message_id +
  lease_seconds * 1_000_000_000`, where `message_id` is the row's
  SimpleBroker message ID, a 64-bit hybrid timestamp with nanoseconds since
  the epoch in the high bits and a 12-bit logical counter in the low bits
  (installed `simplebroker/_timestamp.py`, E20). A row is expired when
  `time.time_ns() >= expires_at_ns`. The lease is measured from the broker
  message timestamp, on the clock that minted it: under normal clock
  progression that is the writer's sampled physical time within 4,096 ns;
  after a writer clock regression the ID carries the previous physical time
  forward and leads the wall clock by the regression, which lengthens the
  effective lease by that amount and never shortens it; the timestamp is
  allocated before the insert commits; a reader on another host sees the
  expiry shifted by the clock skew between the two hosts (§4 hidden
  couplings, "Clock skew", states each bound).
- the registration of a `heartbeat_id` is its newest row. Every older row of
  that id is residue of an interrupted replacement: no reader treats it as
  current, and the heartbeat's table build deletes it (Task 7 item 4).
- resubmission is `upsert_heartbeat` with the same `heartbeat_id`: a new row
  with a new message ID and its own lease; the helper then deletes the older
  rows of that id (write first, then delete). A replaced row is first due one
  interval after the rebuild that finds it, the rule for any replaced row
  (register row 22). There is no renewal protocol, no per-tick write, and no
  default lease: `lease_seconds` is a required keyword of `upsert_heartbeat`,
  like `registrant_tid`.

### Task 6. Registrations queue and write-and-forget helpers

- Outcome: `upsert_heartbeat` / `cancel_heartbeat` write or delete rows and
  return; `ensure_heartbeat_service` and its endpoint-liveness helpers are
  gone.
- Files to touch: `weft/_constants.py`, `weft/core/heartbeat.py`,
  `weft/core/tasks/heartbeat.py` (the row model only, in this task),
  `tests/core/test_heartbeat_helpers.py`,
  `tests/core/test_service_helper_connections.py`,
  `tests/core/test_manager_runtime_connections.py`.
- RED first, in `tests/core/test_heartbeat_helpers.py` (real broker through
  `broker_env` / `workdir`; no manager runs):
  - H1 `test_upsert_heartbeat_writes_one_row_and_returns_without_manager`:
    guard `monkeypatch.setattr(weft.core.manager_runtime, "ensure_manager",
    fail)` (patching the module attribute catches today's
    `manager_runtime.ensure_manager(...)` call and any restored one), where
    `fail` calls `pytest.fail("helper must not start a manager")`; call `upsert_heartbeat(ctx,
    registrant_tid=tid, heartbeat_id="probe", interval_seconds=60,
    lease_seconds=3600, destination_queue="probe.dest", message="tick")`;
    assert it returns
    `None`, the registrations queue holds exactly one row equal to the row
    shape, and `weft.state.endpoints` and `weft.spawn.internal` are
    untouched. Mutation caught: a restored `ensure_manager` call, or a
    restored write to `weft.state.endpoints` or `weft.spawn.internal`. Not
    caught, by design: a read-only endpoint lookup or a poll that writes
    nothing; grep gate G-5 covers the deleted lookup symbols, and no
    behavioral assertion is claimed for them. RED at the pre-task code: fails on
    the missing `registrant_tid` and `lease_seconds` keywords (TypeError),
    the accepted reason here because the signature is the contract.
  - H2 `test_upsert_heartbeat_replaces_by_heartbeat_id`: two upserts with the
    same id and different intervals and leases (interval 60 with lease 3600,
    then interval 120 with lease 7200); exactly one row remains, with the
    second interval and `lease_seconds == 7200`, and its message ID is
    greater than the first row's; a third upsert with another id leaves two
    rows. Mutation: append-only upsert, or a replace that keeps the first
    row's lease.
  - H3 `test_cancel_heartbeat_deletes_every_row_for_its_id`: preload two rows
    for `"a"` (direct queue writes, simulating a concurrent-writer residue)
    and one for `"b"`; `cancel_heartbeat(ctx, heartbeat_id="a")` leaves only
    `"b"`; cancelling an unknown id is a no-op returning `None`. A second
    case: `upsert_heartbeat(..., heartbeat_id=" probe ")` stores `"probe"`,
    and `cancel_heartbeat(ctx, heartbeat_id=" probe ")` deletes it;
    `cancel_heartbeat(ctx, heartbeat_id="  ")` raises `ValueError`.
    Mutation: cancel matching the raw string while upsert strips.
  - H4 `test_upsert_heartbeat_rejects_invalid_rows_before_writing`,
    parametrized over interval 59, interval `10**400` with `lease_seconds`
    `10**400` (overflows a float; rejected by the lease maximum), interval
    `HEARTBEAT_MAX_LEASE_SECONDS + 1` with the same lease (interval above the
    maximum through the chain), `lease_seconds=59` with interval 60 (a lease
    shorter than its interval), `lease_seconds` `HEARTBEAT_MAX_LEASE_SECONDS
    + 1` with interval 60, `lease_seconds` `10**400` with interval 60, destination
    `"weft.x"`, empty destination, `registrant_tid="nope"`, empty
    `heartbeat_id`, and a non-serializable message (every case not naming a
    lease passes `lease_seconds=3600`): each raises `ValueError` (pydantic
    `ValidationError` is a `ValueError`; the two `10**400` cases must not
    escape as `OverflowError`) and the queue stays empty. Mutation: the
    writer accepts an unschedulable interval or an out-of-bounds lease (a
    poison row, or a lease shorter than the cadence it asks for).
- GREEN, `weft/core/heartbeat.py`:
  1. `upsert_heartbeat(context, *, registrant_tid: str, heartbeat_id: str,
     interval_seconds: int, lease_seconds: int, destination_queue: str,
     message: Any, broker: Any | None = None) -> None`: build the row
     (including the field `lease_seconds`) and validate it with
     `HeartbeatRegistrationRow`; inside one `queue_broker(context,
     WEFT_HEARTBEAT_REGISTRATIONS_QUEUE, broker=broker)` block, write the
     row (keep the returned message ID), then peek the queue with
     timestamps and delete by exact ID every row whose `heartbeat_id`
     matches and whose ID is lower than the new one. Write before delete
     keeps the queue non-empty across a replace. Docstring: the
     write-and-forget contract, the lease rule (the row lives for its lease
     and no longer; to continue, resubmit with the same `heartbeat_id`; §4
     hidden couplings, "Registrant identity"), and `Spec: [MF-3.2]`.
  2. `cancel_heartbeat(context, *, heartbeat_id: str, broker: Any | None =
     None) -> None`: canonicalize `heartbeat_id` exactly as the row model
     does (strip; empty raises `ValueError`; reuse the model's validator
     rather than re-implementing the rule), then peek with timestamps and
     delete by exact ID every row whose `heartbeat_id` matches the
     canonical id.
  3. Delete `ensure_heartbeat_service`, `_write_heartbeat_request`,
     `_latest_heartbeat_task_status`, `_heartbeat_runtime_handle_is_live`,
     `_heartbeat_endpoint_candidate`, `_heartbeat_endpoint_is_live`, and the
     imports only they used (`manager_runtime`, `time`,
     `HEARTBEAT_ENDPOINT_PROBE_TIMEOUT`, `MANAGER_REGISTRY_POLL_INTERVAL`,
     `MANAGER_STARTUP_TIMEOUT_SECONDS`, and the rest; let ruff name them).
     `__all__ = ["cancel_heartbeat", "upsert_heartbeat"]`. Keep the module
     private ([PY-1]); no re-export. `HEARTBEAT_ENDPOINT_PROBE_TIMEOUT` stays
     in `_constants.py` only if another module still uses it (grep; delete
     it otherwise).
  4. `weft/core/tasks/heartbeat.py`: replace `HeartbeatMutation` and
     `HeartbeatAction` with `HeartbeatRegistrationRow` (shared names above),
     including its two lease methods, `expires_at_ns` and `expired`.
  5. `weft/_constants.py`: add `WEFT_HEARTBEAT_REGISTRATIONS_QUEUE` and
     `HEARTBEAT_MAX_LEASE_SECONDS` exactly as the shared names above give
     them (placement and docstrings).
- Test edits for the removal:
  `tests/core/test_heartbeat_helpers.py` — delete the tests whose subject is
  `ensure_heartbeat_service` or a deleted helper
  (`test_ensure_heartbeat_service_starts_service_on_first_use`,
  `test_upsert_heartbeat_reuses_live_service_without_second_startup`,
  `test_ensure_heartbeat_service_fails_on_startup_timeout`,
  `test_heartbeat_endpoint_terminal_task_log_rejects_stale_endpoint`,
  `test_heartbeat_endpoint_liveness_uses_task_process_runtime_handle`); keep
  every test whose subject is `weft.core.endpoints` or internal spawn
  submission (locate by the symbol each one calls, not by name; record the
  keep/delete list in the execution log).
  `tests/core/test_service_helper_connections.py` — drop the `"heartbeat"`
  case of the parametrized test at line 114 and the
  `_heartbeat_runtime_handle_is_live` assertion near line 231; rewrite
  `test_heartbeat_requests_commit_without_closing_owner` (line 186) for the
  new signatures (still asserting the borrowed owner connection stays open);
  retarget `test_failed_heartbeat_write_closes_local_facade_not_owner` (line
  713) at a failing write inside `upsert_heartbeat`.
  `tests/core/test_manager_runtime_connections.py` — drop the
  `heartbeat_fallback=True` case (line 185).
- Grep gate G-5: `grep -rn "ensure_heartbeat_service\|_write_heartbeat_request\|_heartbeat_endpoint_is_live\|HeartbeatMutation" weft tests bin --include=*.py`
  → no output.
- Verify (with Task 7, one GREEN gate; see the Tasks 6–8 preamble):
  `./.venv/bin/python -m pytest tests/core/test_heartbeat_helpers.py tests/core/test_service_helper_connections.py tests/core/test_manager_runtime_connections.py -q`,
  run together with Task 7's Verify after both tasks' GREEN edits. Before
  that, record only RED results for Task 6.
- Stop if: any production caller of `ensure_heartbeat_service` appears that
  E15 missed. Then record it and reduce the helper to an event-driven wait on
  `weft.state.endpoints` (a notification interest, never a sleep loop)
  rather than deleting it; that is a plan change, not a judgment call.

### Task 7. HeartbeatTask: peek-built table, notification interest, idle exit

- Outcome: the heartbeat's registrations are the rows of
  `weft.state.heartbeats`; it never consumes a valid row, rebuilds after a
  restart with no re-registration, emits nothing in a turn whose
  pre-emission rebuild observed a cancel (at most one emission for a cancel
  committed after that rebuild), learns a deleted row while paused, deletes
  an expired row at its lease deadline and the residue of a replaced id at
  every build, and exits on idle only with an empty table.
- Files to touch: `weft/core/tasks/heartbeat.py`,
  `tests/tasks/test_heartbeat.py`, `tests/core/test_service_helper_connections.py`
  (line 667 test), `tests/tasks/test_task_monitor.py` (R1 `emitting` mode
  only).
- RED first, in `tests/tasks/test_heartbeat.py` (real `HeartbeatTask` from
  `make_heartbeat_taskspec`, real queues; rows written with
  `upsert_heartbeat` unless the test needs a malformed row). Manual-driver
  rule (design choice, stated so no driver is changed silently): the first
  `process_once()` builds the table because `_registrations_rebuild_pending`
  starts `True`; after that, only the observation path sets the flag, and a
  direct `process_once()` does not run it. A manually driven test that
  writes or deletes a row after the first build therefore calls the real
  wait, `task.wait_for_activity(0.05)` (a positive timeout: a non-positive
  one returns before the observation check), and then `process_once()`. The
  wait's register-before-read step runs `_has_pending_observation()` and
  returns at once when it reports a change. The shared drive helpers are not
  changed.
  - T1 `test_heartbeat_table_is_peeked_not_consumed`: one row;
    `task.process_once()`; assert the id is in `task._registrations` and the
    queue still holds the row with the same message ID. Mutation: consume
    instead of peek.
  - T2 `test_heartbeat_restart_rebuilds_table_from_queue`: task A builds its
    table, then `stop(join=False)` and `cleanup()`; task B (new TID, same
    root) is constructed with no further write; `process_once()`; assert B's
    table holds the row; force it due (the pattern of
    `test_heartbeat_late_wake_coalesces_to_one_emit`) and assert one emission
    to the destination. Mutation: a restart with a non-empty queue that does
    not rebuild (needs re-registration).
  - T3 `test_heartbeat_idle_exit_requires_empty_table`: taskspec metadata
    `heartbeat_idle_timeout: 5.0` and the fake-clock pattern on
    `weft.core.tasks.heartbeat.time` (`SimpleNamespace(monotonic=...,
    time=time.time, time_ns=time.time_ns, sleep=time.sleep)`); one row
    (interval 60); `process_once()` builds the table (assert the id is in
    `task._registrations`). Then, with the row retained, run idle checks
    across the timeout: advance 6 s, `process_once()`; advance 6 s more
    (12 s, past the 5 s timeout, before the 60 s due time),
    `process_once()`; after each assert `not task.should_stop` and no
    `heartbeat_service_idle_shutdown` event. Then `cancel_heartbeat`;
    `task.wait_for_activity(0.05)`; `process_once()` (the table is empty,
    nothing is emitted); advance 5 s more; `process_once()`; assert
    `task.should_stop` and one `heartbeat_service_idle_shutdown` event.
    Mutation: idle exit with rows present (fails at 12 s).
  - T4 `test_heartbeat_cancel_before_due_turn_suppresses_emission`: one
    row, built; force due; `cancel_heartbeat`; `process_once()` with no wait
    (so only the pre-emission rebuild can see the cancel); the destination
    queue is empty. Claim, exactly: a cancel committed before the due turn's
    pre-emission rebuild suppresses that turn's emission. A cancel committed
    after that rebuild may be followed by at most one emission; T4 does not
    test that window and no synchronization machinery closes it. Mutation:
    omitting the pre-emission rebuild.
  - T5 `test_heartbeat_inbox_mutation_is_rejected`: write the old inbox shape
    `{"action": "upsert", ...}` to `T{tid}.inbox`; `process_once()`; assert a
    `heartbeat_request_invalid` event, an empty table, and an empty inbox.
  - T6 `test_heartbeat_invalid_registration_row_is_deleted_and_reported`:
    direct writes of a self-routed row (destination = the task's own inbox),
    a malformed row, an unschedulable row (`interval_seconds` `10**400`,
    a valid JSON integer), a short-lease row (`lease_seconds` 59 with
    `interval_seconds` 60), and an oversized-lease row (`lease_seconds`
    `10**400`) next to one valid row. Every bad row that has a readable
    `heartbeat_id` uses its own id, distinct from the others and from the
    valid row's, so each is its id's newest row and reaches validation (item
    4 b) rather than residue deletion (item 4 a). `process_once()`; all five
    bad rows are gone (each deleted by exact ID), five
    `heartbeat_request_invalid` events, the valid row is unaffected (still
    in the queue with the same message ID, and in the table), and the task
    has not raised. Mutation: the build accepts an unschedulable row (it
    would raise `OverflowError` and crash on every relaunch), or accepts a
    lease below its interval or beyond the maximum (lease rejection only at
    the writer).
  - T7 `test_heartbeat_registrations_queue_joins_reactor_wait`: wait
    membership is proved through the arbiter's registered interests, not
    through timing. The 1.0 s ownership-audit timer (E14) runs turns and the
    initial `_registrations_rebuild_pending = True` makes a direct
    observation check true before any publication, so neither a 5 s wake
    assertion nor an undriven `_has_pending_observation()` check falsifies a
    missing interest. Steps: construct the task; `wait_for_activity(0.05)`
    and `process_once()` (builds the empty table, clears the initial flag);
    `wait_for_activity(0.05)` again (installs the waiter); assert
    `WEFT_HEARTBEAT_REGISTRATIONS_QUEUE` is in
    `task._multi_activity_waiter_signature` (the queue names
    `MultiQueueWatcher._ensure_multi_activity_waiter` passed to SimpleBroker's
    `create_activity_waiter_for_queues`); negative control: an unrelated
    runtime queue (`weft.state.streaming`) is not in that signature, and
    `task._has_pending_observation()` is `False` before any write. Then
    `upsert_heartbeat`; assert `task._has_pending_observation()` is `True`.
    Run it on PostgreSQL too (`bin/pytest-pg
    "tests/tasks/test_heartbeat.py::test_heartbeat_registrations_queue_joins_reactor_wait"`).
    Mutation: missing notification interest (the queue is absent from the
    signature) or a cursor that never reports a new row.
  - T8 `test_heartbeat_cancel_while_paused_arms_idle_exit`: taskspec metadata
    `heartbeat_idle_timeout: 5.0` and T3's fake clock; one row, built; send
    `PAUSE` on `ctrl_in` and `process_once()` (assert the pause ack);
    negative control: `task._has_pending_observation()` is `False`;
    `cancel_heartbeat`; `task.wait_for_activity(0.05)`; `process_once()`;
    assert the table is empty; advance 5 s; `process_once()`; assert
    `task.should_stop` and one `heartbeat_service_idle_shutdown` event, with
    the task still paused. Mutation: removal learned only through the
    emission path (which `_paused` disables), so a paused service never
    idles out. Run on PostgreSQL too (the delete raises no NOTIFY there; the
    observation runs at the wait entry regardless).
  - T9 `test_heartbeat_expired_registration_is_deleted_at_its_deadline`:
    taskspec metadata `heartbeat_idle_timeout: 5.0` and T3's fake clock,
    with `time_ns` advanced together with `monotonic` by the same offset.
    Anchor the wall clock on the observed row: after the write, read the
    row's message ID (peek with timestamps) and make `time_ns` return that
    message ID plus the test's offset, not a `time.time_ns()` reading taken
    before the write (which would move with how long the real write took).
    One row, interval 60, lease 90, so the expiry (90 s after the write)
    coincides with neither the first due time (about 60 s after the build)
    nor the second (about 120 s). First `process_once()` builds the table.
    Advance 61 s; `process_once()`: one emission (the first tick). Send
    `PAUSE` on `ctrl_in` and `process_once()`; assert the pause ack.
    Emission is now disabled, so the due-emission rebuild cannot run.
    Advance to 89.7 s since the write and run one paused `process_once()`
    first: a turn re-arms the 1.0 s ownership-audit deadline
    (`_exit_if_superseded`, heartbeat.py:455), which was last armed by the
    61 s turn for 62 s and would otherwise be stale, making
    `next_wait_timeout()` return 0.0 regardless of the expiry term. Then
    assert `next_wait_timeout()` is between 0.25 and 0.35: the audit term is
    now 1.0 s, the due term about 30 s, so only the expiry term can produce
    0.3. Advance to 90.1 s; `process_once()`: the row is gone from the queue, one
    `heartbeat_registration_expired` event (`message_id`, `heartbeat_id`,
    `registrant_tid`), the table is empty, the destination holds exactly
    one message, and the task is still paused. Advance past the idle
    timeout (5 s more); `process_once()`: `task.should_stop` (paused idle
    exit, as T8). Mutations: no expiry term in `next_wait_timeout` (the 0.3
    assertion fails; the wait would be 1.0); no expiry-triggered rebuild
    (the row survives at 90.1 s while paused, because a paused task runs no
    due-emission rebuild); expiry ignored altogether (the row kept, the
    table non-empty, and no `heartbeat_registration_expired` event at
    90.1 s).
  - T10 `test_heartbeat_build_deletes_residue_and_an_expired_newest_row`:
    T9's fake clock, anchored on B's observed message ID. First case:
    direct writes of row A (id `x`, interval 60, lease 3600) then row B (id
    `x`, interval 60, lease 60), both valid; advance `time_ns` 61 s;
    `process_once()` (the first build): both rows are gone from the queue,
    the table is empty, one `heartbeat_registration_expired` event (for B),
    and no emission ever. Second case, no clock advance: A then B, both
    unexpired; after the build the queue holds only B and the table holds B
    with B's lease (`expires_at_ns` equal to B's message ID plus 60 s).
    Third case, the failed-delete barrier (item 4 a): the first case's setup
    (A then B, advance 61 s, so B is expired). The build peeks and deletes
    through the task's one registrations handle, `handle =
    task._queue(WEFT_HEARTBEAT_REGISTRATIONS_QUEUE)` (the `BaseTask._queue`
    cache, `weft/core/tasks/base.py` line 614; item 1 makes the queue a
    fixed support route, and the cache keeps that one handle for the task
    lifetime, item 3). Keep `original = handle.delete` and
    `monkeypatch.setattr(handle, "delete", fail_once)`, where `fail_once`
    raises `OSError` on its first call (A's delete) and calls `original`
    afterward. `process_once()` (the first build): the queue still holds
    both A and B, `"x"` is not in `task._registrations` (B is expired, so it
    is not stored), `task._registrations_rebuild_pending` is `False` (the
    failure does not hold the flag), `task._has_pending_observation()` is
    `False` (no spin), and no `heartbeat_registration_expired` event was
    reported. Restore `delete` (`monkeypatch.setattr(handle, "delete",
    original)`), then trigger the next build the way production does:
    `upsert_heartbeat` for a second id `y` (interval 60, lease 3600),
    `task.wait_for_activity(0.05)` and `process_once()`: A and B are gone,
    the `heartbeat_registration_expired` event for B is reported once, and
    `y` is in the table. Mutation for the retry: a held rebuild flag (the
    `_has_pending_observation()` assertion fails).
    Mutations: residue kept, and resurrected as the registration after the
    newest row expires (A would be emitted to for an hour); the newest row
    deleted despite a failed older delete (the third case's first build
    leaves only A in the queue, and the retry build takes A as `x`'s
    registration).
- GREEN, `weft/core/tasks/heartbeat.py`:
  1. `_reactor_support_routes`: extend the base map with
     `"heartbeat_registrations": WEFT_HEARTBEAT_REGISTRATIONS_QUEUE`
     ([QUEUE.7] fixed support route).
  2. Fields in `_initialize_heartbeat_runtime`: `_registrations_cursor: int |
     None = None`, `_registrations_rebuild_pending = True` (the first turn
     builds), `_row_ids: dict[str, int] = {}` (heartbeat_id → row message ID
     of the current registration). The `HeartbeatRegistration` dataclass
     gains `expires_at_ns: int`, set from
     `HeartbeatRegistrationRow.expires_at_ns(message_id)`.
  3. `_activity_wait_queues`: `super()` plus one handle on
     `WEFT_HEARTBEAT_REGISTRATIONS_QUEUE` opened once and kept for the task
     lifetime (closed in `cleanup`). `_has_pending_observation`: if
     `_registrations_rebuild_pending`, return `True`; else
     `has_pending(after_timestamp=self._registrations_cursor)` on that queue
     (new rows); and, when `_row_ids` is non-empty, one bounded peek of the
     queue's row IDs (`with_timestamps=True`; the queue holds one row per
     registration plus rare residue) to check that every `_row_ids` value is
     still present (removal). Either finding sets the flag and returns
     `True`. The check does not read `_paused` and does not depend on a due
     time, so a cancel while paused empties the table and arms the idle
     deadline (T8). The cursor is advanced only by the rebuild (item 4); the
     flag holds the observation until then. Model both on
     `Manager._activity_wait_queues` / `Manager._has_pending_observation`;
     reads stay on the reactor ([IMPL.8]); a broker error is logged at debug
     and reports no change for this wait.
  4. `_rebuild_registrations()`: peek every row with timestamps (one
     `peek_generator` pass, closed with `closing_queue_iterator`) through
     item 3's handle, `self._queue(WEFT_HEARTBEAT_REGISTRATIONS_QUEUE)` (the
     `BaseTask._queue` cache entry; every delete in this item goes through
     the same handle); advance
     `_registrations_cursor` to the newest ID; take one `now_ns =
     time.time_ns()` reading. A row whose `heartbeat_id` cannot be read (not
     a JSON object, or no non-empty string `heartbeat_id`) is invalid on its
     own: delete it by exact ID and report `heartbeat_request_invalid`
     (`message_id`, `error`). Group the other rows by canonical
     `heartbeat_id` and apply, per id, in this order:
     a. delete every row older than the id's newest row by exact message
        ID, in ascending message-ID order (residue of an interrupted
        replacement; readers already ignored it). Failure barrier: if any of
        these deletes raises, log it at debug and stop deleting for that id.
        Its remaining older rows and its newest row stay in the queue
        untouched: the newest row is not deleted even if it is expired or
        invalid, so b and c do not run for the id. Store the newest row as
        the registration (as in d) only when it validates and is unexpired;
        otherwise store nothing for the id. The deletes are best-effort (set,
        then catch and report; no pre-check and no read-back): the id's rows
        stay in the queue and are retried at the next build, whatever
        triggers it (a new or deleted row observed at a wait entry, a due
        emission's pre-emission rebuild, an expiry deadline, or a restart).
        The build does not hold `_registrations_rebuild_pending` for the
        failure: a held flag would make `_has_pending_observation` report a
        change at every wait entry and the service would rebuild on every
        turn without blocking, the spin this task's Stop-if names;
     b. validate the newest row with `HeartbeatRegistrationRow` and
        `_validate_destination_queue`; if it is invalid, delete it by exact
        ID and report `heartbeat_request_invalid` (`message_id`, `error`), as
        now;
     c. if it is valid and `row.expired(message_id, now_ns)`, delete it by
        exact ID and report the lifecycle event
        `heartbeat_registration_expired` (`message_id`, `heartbeat_id`,
        `registrant_tid`);
     d. otherwise it is the registration: if its ID equals `_row_ids[id]`,
        keep the registration and its `next_due_at`; if it is new or
        changed, store a fresh `HeartbeatRegistration` with `next_due_at =
        monotonic() + interval` and `expires_at_ns =
        row.expires_at_ns(message_id)` and push it on the heap.
     The rule is monotonic: it never makes an older row current, because
     older rows are deleted first and a failed older delete stops the id's
     deletion before its newest row. The barrier is what keeps the rule
     monotonic under a failed delete. The interleaving it closes: row A,
     older, with a later expiry (lease 3600), and row B, the id's newest,
     already expired (lease 60). If B were deleted while A's delete failed,
     A would be the id's newest row at the next build and become its
     registration, and under clock skew another reader (a Manager or
     `weft status` on a host whose clock reads earlier) could consider A
     unexpired. With the barrier, B stays while A does, so every build takes
     B as the id's registration and finds it expired. (That is the guarantee
     a two-pass, older-rows-first retirement rule would otherwise have had
     to supply.) Drop registrations whose id has no current row (stale heap
     entries are already skipped). Set `_empty_since_monotonic` to now when
     the table becomes empty and to `None` when it is non-empty. Clear
     `_registrations_rebuild_pending` (also after a failed delete; see a).
     Docstring cites [MF-3.2] and [QUEUE.7] and states the failure barrier.
  5. `_process_reactor_turn`: after the control row and the inbox drain, if
     `_registrations_rebuild_pending`, rebuild and return (one bounded turn).
     In `_emit_due_registrations`, when at least one entry is due, rebuild
     first and recompute the due list from the rebuilt table. Linearization
     boundary, stated in the method docstring: a cancel committed before
     this rebuild suppresses the turn's emission; a cancel committed after it
     may be followed by at most one emission (this turn's). No lock, fence,
     or re-check after the destination write is added.
  6. Inbox: keep `_drain_one_registration_message` (a stray inbox row must
     still be drained, or the watcher would report it pending forever);
     `_handle_work_message` now reports every row as
     `heartbeat_request_invalid` with error "heartbeat registrations are rows
     on weft.state.heartbeats" and applies `reserved_policy_on_error`. No
     mutation branch remains ([WT-2a.9]; no shim).
  7. Idle exit: `_maybe_idle_shutdown` and `_next_idle_timeout` unchanged;
     they already key on the empty table (E14).
  8. Expiry deadline: `next_wait_timeout` includes the earliest
     `expires_at_ns` over `self._registrations`, as seconds from
     `time.time_ns()` (`(earliest - time.time_ns()) / 1_000_000_000`, not
     below 0.0), next to the due heap and the idle deadline. In
     `_process_reactor_turn`, before item 5's rebuild check, a turn at or
     after that instant sets `_registrations_rebuild_pending`, so that
     turn's rebuild deletes the expired row and reports
     `heartbeat_registration_expired` (item 4 c). This is a named future
     instant on the reactor's one wait ([WT-2a.6]; per-service clocks), not
     a poll ([ALT-WT-002]). The pre-emission rebuild (item 5) already runs
     whenever anything is due, so a due turn never emits for a row that
     rebuild found expired. An emission whose pre-emission rebuild found the
     row unexpired may land after the expiry instant by at most that turn's
     own duration; state this bound in the `_emit_due_registrations`
     docstring. No fence is added.
- Test migration: `test_heartbeat_service_accepts_upsert_and_cancel` (line
  110), `test_heartbeat_rejects_unsafe_destination_and_later_input_progresses`
  (160), `test_heartbeat_duplicate_upsert_replaces_existing_registration`
  (237), `test_heartbeat_late_wake_coalesces_to_one_emit` (284),
  `test_heartbeat_serializes_structured_payloads_on_emit` (324),
  `test_heartbeat_pending_input_wakes_through_reactor_wait` (497),
  `test_heartbeat_failed_exact_ack_preserves_reserved_row` (594), and
  `tests/core/test_service_helper_connections.py::test_heartbeat_destination_churn_does_not_grow_task_cache`
  (667): replace inbox mutation writes with `upsert_heartbeat` /
  `cancel_heartbeat` (or direct row writes for malformed cases) and the
  single-row ingestion turn with one `process_once()` table build when it is
  the first build, or with `task.wait_for_activity(0.05)` then
  `process_once()` for any later write or delete (the manual-driver rule
  above; for example the second upsert in
  `test_heartbeat_duplicate_upsert_replaces_existing_registration`). A test
  whose subject was the inbox mutation lifecycle itself (reserved-row ack of
  a mutation) is retargeted at T5's rejection path; record each disposition.
  R1 `emitting` mode in `tests/tasks/test_task_monitor.py`: the probe
  registration becomes `upsert_heartbeat(ctx, registrant_tid=heartbeat_tid,
  heartbeat_id="probe", ...)`; the Monitor stand-in in `emitting` mode
  writes its registration the same way (its `registrant_tid` is the
  Monitor's TID); step 6a's drain becomes one `heartbeat.process_once()` and
  asserts the registrations queue is unchanged. Its assertions and RED
  meaning are otherwise unchanged.
- Verify (the Tasks 6–7 GREEN gate): Task 6's Verify command, then
  `./.venv/bin/python -m pytest tests/tasks/test_heartbeat.py tests/core/test_service_helper_connections.py tests/tasks/test_task_monitor.py -q -k "heartbeat or quiet_interval"`
  and the T7 and T8 PostgreSQL commands above (`bin/pytest-pg
  "tests/tasks/test_heartbeat.py::test_heartbeat_cancel_while_paused_arms_idle_exit"`).
- Stop if: the heartbeat reactor spins (turn count over a 1 s window with no
  writes exceeds the ownership-audit cadence by more than one): a delivery
  config was used instead of a notification interest. A valid row can crash
  the service: report it (§4 hidden couplings); do not add backoff or
  quarantine.

### Task 8. Manager desire predicate, wake, spawn payload, status

- Outcome: the canonical Manager desires the heartbeat service if and only if
  `weft.state.heartbeats` holds a row that is unexpired or not yet rejected
  ([MF-3.2]), wakes when a row arrives, deletes the rows only when every row has expired, and
  the spawned service may idle out.
- Files to touch: `weft/core/manager.py`, `weft/commands/system.py`,
  `tests/core/test_manager.py`, `tests/commands/test_status.py`.
- RED first:
  - M1 `test_manager_desires_heartbeat_only_while_registrations_exist`
    (`tests/core/test_manager.py`, using `manager_setup` and
    `_desired_internal_service_keys`), parametrized over
    `(task_monitor_enabled, liveness_enabled, row_present, expected)`:
    `(True, False, False, [TASK_MONITOR])`, `(True, False, True, [HEARTBEAT,
    TASK_MONITOR])`, `(False, False, True, [HEARTBEAT])`, `(False, True,
    False, [LIVENESS_MONITOR])`, and `(True, False, "expired",
    [TASK_MONITOR])`, so `row_present` takes `False`, `True`, or
    `"expired"`. The `"expired"` case writes one row with `upsert_heartbeat`
    (interval 60, lease 60) and then replaces the Manager's clock with the
    pattern of the `pg_admission_setup` fixture (`tests/core/test_manager.py`
    lines 2795–2801: `monkeypatch.setattr(manager_mod, "time",
    SimpleNamespace(monotonic=..., time_ns=..., sleep=time.sleep))`, with
    `time_ns` returning a real `time.time_ns()` reading plus 61 s); after
    the desired keys are computed it asserts the registrations queue is empty
    (the all-expired delete). The `"expired"` case then checks the
    failed-delete barrier (item 1): with the clock still replaced, write two
    rows with `upsert_heartbeat` (ids `"a"` then `"b"`, interval 60, lease
    60, so both are expired at the replaced clock); with `handle =
    manager._queue(WEFT_HEARTBEAT_REGISTRATIONS_QUEUE)`, keep `original =
    handle.delete` and `monkeypatch.setattr(handle, "delete", fail_once)`,
    where `fail_once` raises `OSError` on its first call and calls
    `original` afterward; compute the desired keys again: they are still
    `[TASK_MONITOR]` (the predicate answered `False`), and the queue still
    holds both rows (the delete of `"a"`, the older, failed, so `"b"` was
    not deleted). Mutation: desire with an empty queue (the first case),
    desire still tied to the TaskMonitor flag (the third), or desire from a
    row whose lease has ended, an all-expired queue left in place, or a
    newer row deleted after a failed older delete (the fifth).
  - Modify, do not duplicate: `test_task_monitor_only_desires_heartbeat_and_task_monitor`
    (line 5378) is subsumed by M1 and deleted;
    `test_liveness_monitor_only_does_not_desire_heartbeat` (5363) and
    `test_both_internal_monitors_disabled_desires_no_internal_service`
    (5397) keep their assertions and docstrings change to name the queue
    predicate; `test_liveness_monitor_only_convergence_ignores_missing_heartbeat`
    (5407) gains the inverse: with TaskMonitor enabled and an empty queue a
    missing heartbeat adds no reason, and with one row present it adds
    `missing_active_tid`.
  - `test_manager_enqueues_heartbeat_through_service_path` (line 2036):
    write one row with `upsert_heartbeat` before constructing the Manager
    (also proves "a registration written before any Manager exists launches
    on the next Manager start"), and replace the `heartbeat_idle_timeout ==
    0.0` assertion with `"heartbeat_idle_timeout" not in metadata`.
  - M2 `test_manager_registration_row_wakes_service_convergence`: construct
    the canonical Manager with an empty queue and drain its startup
    enqueues; run one real `manager.wait_for_activity(0.05)` so the retained
    waiter is installed; assert `WEFT_HEARTBEAT_REGISTRATIONS_QUEUE` is in
    `manager._multi_activity_waiter_signature` (the queue joined the
    retained waiter). Negative control: a non-canonical Manager (inbox not
    `weft.spawn.requests`) built the same way does not have it in its
    signature. Write one row; run `manager.wait_for_activity(0.05)` (its
    register-before-read observation sets the flag), then one
    `process_once()` with no clock advance; assert one heartbeat spawn
    payload on `weft.spawn.internal`. The test never calls
    `_has_pending_observation()` or convergence by hand. Mutation: no
    notification interest (absent from the signature; the launch would wait
    for the 5.0 s stable audit). Run on PostgreSQL too.
  - S1 (`tests/commands/test_status.py`): replace
    `test_status_reports_heartbeat_enabled_when_task_monitor_enabled` (line
    2686) with `test_status_reports_heartbeat_enabled_only_with_registrations`:
    TaskMonitor enabled, empty queue → heartbeat `enabled is False`,
    `status == "disabled"`, `evidence == "no-registrations"`; after one
    `upsert_heartbeat` → `enabled is True`; expired case: with that row's
    lease ended (the same clock replacement on `weft.commands.system.time`,
    `time_ns` a real reading plus the lease plus 1 s) → `enabled is False`,
    `evidence == "no-registrations"`, and the row is still in the queue
    (status reads, never deletes). Mutation: status counting an expired row
    as a registration, or deleting it. Update the docstring of the
    liveness-only test at line 2663 (its assertions hold: empty queue).
- GREEN, `weft/core/manager.py`:
  1. `Manager._heartbeat_registrations_present(self) -> bool`: peek every
     row of `WEFT_HEARTBEAT_REGISTRATIONS_QUEUE` with timestamps (one
     `peek_generator` pass, closed with `closing_queue_iterator`; the queue
     holds one row per live registration plus rare residue) and classify
     each with `HeartbeatRegistrationRow` against one `time.time_ns()`
     reading. A row that fails validation, or a valid row that is not
     `expired`, means present: return `True` (an invalid row is the
     heartbeat's to reject and delete at its next build). An empty queue
     answers `False`. If every row is expired, delete them by exact message
     ID through `self._queue(WEFT_HEARTBEAT_REGISTRATIONS_QUEUE)`, in
     ascending message-ID order across the whole queue, and stop at the
     first delete that raises (logged at debug), so no row is deleted while
     an older row remains; answer `False` either way (every row was expired
     at the reading); the rows left behind are retried on a later pass. The
     Manager deletes nothing in any other case. Its delete never makes an
     older row current, because older rows are deleted first and a failed
     older delete stops the id's deletion before its newest row (the stop
     covers every id, since the order runs across the whole queue). An
     expired newest row next to an unexpired older row of the same
     `heartbeat_id` leaves the queue desired, and the launched heartbeat's
     build applies the per-id rule (Task 7 item 4) and deletes both. A boolean `has_pending()` no longer suffices, because a
     non-empty queue may hold only expired rows. A `BrokerError` /
     `OSError` / `RuntimeError` on the read is logged at debug and answers
     `False` for this pass (best-effort, re-read next pass). Docstring cites
     [MA-1] item 7 and [MF-3.2]. Import `HeartbeatRegistrationRow` from
     `weft.core.tasks.heartbeat` at module top; if that import creates an
     import cycle, stop and report (no deferred import is added without
     one).
  2. Replace the heartbeat gate at the three sites (E16) with
     `self._heartbeat_registrations_present()`, independent of
     `_task_monitor_enabled` and `_liveness_monitor_enabled`; the sites stay
     inside their existing canonical-inbox checks. Replace the comment at
     lines 7153–7155 with: "The heartbeat service is desired while
     weft.state.heartbeats holds a row that is unexpired or not yet rejected
     ([MA-1] item 7); no built-in
     service registers one ([MF-3.2])."
  3. Wake: extend `Manager._activity_wait_queues` with one long-lived handle
     on `WEFT_HEARTBEAT_REGISTRATIONS_QUEUE`, only when
     `self._queue_names["inbox"] == WEFT_SPAWN_REQUESTS_QUEUE`; extend
     `Manager._has_pending_observation` (before its startup-feedback reads)
     with a strict publication cursor on that queue. Cursor advance point,
     explicit: unlike Task 7 (where the rebuild advances the cursor), the
     Manager advances it inside `_has_pending_observation` itself, exactly
     as the startup-feedback record does today (`manager.py` near line 1915:
     `peek_many(..., limit=1, order="newest", with_timestamps=True,
     after_timestamp=cursor)`, then `record.publication_id = int(rows[0][1])`
     before setting `publication_pending`). A boolean `has_pending()` cannot
     advance a cursor and is not used here. On a row newer than the cursor,
     set the cursor to that newest ID, then set
     `_heartbeat_registration_observed = True` and return `True`; the flag,
     not the cursor, carries the observation to the turn. In
     `_process_manager_reactor_turn`, right after the startup-feedback loop,
     if the flag is set, clear it and call
     `self._run_managed_service_convergence(include_autostart=False,
     force=True)`. Initialize the cursor to `None` and the flag to `False` in
     the Manager's field initialization; close the handle where the startup
     interests are closed.
  4. `_build_heartbeat_spawn_payload`: delete `"heartbeat_idle_timeout":
     0.0`, so `HEARTBEAT_IDLE_TIMEOUT_SECONDS` applies.
     `_heartbeat_service_spec` is unchanged (restart backoff 0, §4).
- GREEN, `weft/commands/system.py::_service_enabled`: for the heartbeat key,
  apply the Manager's classification through the context's broker (one
  `peek_generator` pass with timestamps, each row classified with
  `HeartbeatRegistrationRow` against one `time.time_ns()` reading): enabled
  when any row is invalid or unexpired. It reads only and never deletes, so
  a queue holding only expired rows reports not enabled and keeps its rows
  until the Manager or the heartbeat deletes them. Docstring "mirrors the
  manager's desire predicate: desired while weft.state.heartbeats holds a
  row that is unexpired or not yet rejected ([MA-1] item 7); reads only". In
  `_service_snapshot_from_evidence`, a heartbeat that is not enabled reports
  `evidence="no-registrations"` instead of `"config-disabled"`, also when
  every row is expired; other keys are unchanged.
- Verify: `./.venv/bin/python -m pytest tests/core/test_manager.py tests/commands/test_status.py -q`
  and `bin/pytest-pg "tests/core/test_manager.py::test_manager_registration_row_wakes_service_convergence"`.
- Stop if: the Manager's quiet-window turn count rises with a row present
  and a live heartbeat (the cursor is not advancing, so the observation
  re-fires every wait); any gate site turns out to need the TaskMonitor flag
  for a reason E16 did not record.

### Task 4. Spec, comments, CHANGELOG (same commit as Tasks 3 and 6–8)

- Outcome: the spec and CHANGELOG describe one Monitor clock and an
  on-demand heartbeat.
- Files to touch: the five spec files; `CHANGELOG.md`. (The Manager comment
  and the `_service_enabled` docstring are rewritten by Task 8, with the code
  they describe.)
- Apply every Replacement block in `## Proposed Spec Delta` verbatim, plus
  the four non-block backlink bullets and the [QUEUE.7] mapping addition.
- [MF-5] `_Implementation mapping_` (05 lines 576–581): add
  `TaskMonitor._arm_next_cycle_deadline` to the TaskMonitor method list,
  before `TaskMonitor._handle_builtin_cycle_worker_result`. Not a
  Replacement block; verified by the grep below.
- `CHANGELOG.md` under `## [Unreleased]`, `### Changed`: one bullet —
  TaskMonitor no longer registers with the built-in heartbeat service; its
  cycle deadline is its only clock and keeps the heartbeat-live fixed-rate
  period (a grid anchored at the first cycle start rather than at heartbeat
  registration, which inbox pokes do not move, one follow-up
  after an overrun), now also while no heartbeat is reachable; the PONG
  `extended.task_monitor.heartbeat` block is removed (no compatibility
  shim). A second bullet — the heartbeat service runs on demand:
  registrations are rows on `weft.state.heartbeats` that survive heartbeat
  and Manager restarts; each carries a registrant-declared lease and
  expires on its own when the lease ends (a registrant that wants more time
  resubmits); the Manager runs the service only while an unexpired one
  exists (whatever `WEFT_TASK_MONITOR_ENABLED` says) and the service exits
  on its idle timeout when none remains; `upsert_heartbeat` now requires
  `registrant_tid` and `lease_seconds` and returns without starting a
  manager, and `ensure_heartbeat_service` is removed (private module,
  [PY-1]); rows on the heartbeat task's inbox are rejected; `weft system
  prune` is unchanged; `weft status` reports a heartbeat with no unexpired
  registration as disabled with evidence `no-registrations`.
- Record the promotion baseline identifier (the landing commit SHA, once
  the owner commits) in `## Spec Baseline` and set `Status: promoted` in the
  same change as the index row flip (the `draft` row already exists).
- Verify:

  ```bash
  . ./.envrc
  ./.venv/bin/python -m pytest tests/specs/test_spec_hygiene.py tests/specs/test_plan_metadata.py -q
  bin/check-doc-paths
  grep -n "2026-11-02-task-monitor-heartbeat-clock-plan" docs/specifications/05-Message_Flow_and_State.md docs/specifications/01-Core_Components.md docs/specifications/03-Manager_Architecture.md docs/specifications/00-Quick_Reference.md docs/specifications/07-System_Invariants.md
  grep -n "TaskMonitor._arm_next_cycle_deadline\|HeartbeatTask._rebuild_registrations" docs/specifications/05-Message_Flow_and_State.md
  grep -n "HeartbeatTask._has_pending_observation" docs/specifications/07-System_Invariants.md
  grep -n "runtime-only and disappear\|helper startup ensures\|ordinary inbox payloads\|Helper-side endpoint validation\|reserved-on-error policy and\|heartbeat wake\|heartbeat/local\|full heartbeat interval\|enabled internal dependent\|before storing a registration" docs/specifications/*.md
  ```

  The backlink grep returns one line per file (five). The 05 mapping grep
  returns two lines; the 07 mapping grep returns one. The retired-phrase
  grep returns nothing: at `b7eed9f9` every one of its hits (01:665,
  03:290, 05:454, 456, 460, 480, 494, 514, 548, 2001; E19) is a line a
  Replacement block replaces.
- Stop if: `bin/check-doc-paths` reports a Replacement block that does not
  match its target — fix the landed text or the block, never both
  differently.
- Done when: all seven commands give the expected output.

### Task 5. Measure after, full gates, traceability (after the landing commit)

- Outcome: before/after idle-turn table on both backends; full gates green;
  theory consequence updated.
- Measure: rerun R1 on SQLite and PostgreSQL (commands in Task 2); record
  the table in the execution log:

  | Backend | Case | Before (Task 2 RED) | After |
  |---------|------|---------------------|-------|
  | SQLite | unavailable | (measured) | 0 |
  | SQLite | emitting | (measured) | 0 |
  | PostgreSQL | unavailable | (measured) | 0 |
  | PostgreSQL | emitting | (measured) | 0 |

- Measure the Manager desire predicate's cost (§4 hidden couplings,
  "Manager service-supervision path"): with a delegating counter on
  `Manager._heartbeat_registrations_present` (it counts the call and the
  rows its peek yields, then runs the real method), count predicate calls
  and rows scanned over a 30 s quiet window with 0, 1, and 10
  registrations; record the counts in the execution log. No cache or
  registry is added ([ALT-WT-010]).

- Theory: in `docs/program-theory.md` [ALT-WT-004], change the Kind line to
  "tried and discarded (built, then removed by
  `2026-11-02-task-monitor-heartbeat-clock-plan.md`; decided by the owner on
  2026-09-08)", the Current consequence to "TaskMonitor's only clock is its
  cycle deadline through `next_wait_timeout()`; it registers nothing. The
  heartbeat service runs only while `weft.state.heartbeats` holds a row
  that is unexpired or not yet rejected ([MA-1] item 7), so with no consumer no heartbeat
  process runs (this supersedes the `da5fa0f6` gate).", and append this
  plan to Evidence. (Not a Replacement block: the text describes post-landing
  state and lands after the code.) This is a theory edit; it needs the
  owner's acceptance like any [ALT-WT-*] change (fork F4).
- Run `## Verification` in full.
- [WT-7] class-5 completion requirements (`docs/program-theory.md`
  `## Tensions and falsifiers [WT-7]`, "Possession probe, keyed"):
  1. Keyed possession probe: the owner writes the key first (one
     hypothetical feature and where it belongs, one category error and the
     entry that refuses it, one defect class an audit of a named subsystem
     should find). An agent that did not author this plan, from a different
     family, answers from `docs/program-theory.md` alone and is graded
     against the key. Key and result go in `## Review Log`; on FAIL,
     classify after disposition (document deficiency, key fault, or agent
     reasoning). A self-graded probe is not a probe.
  2. Theory-versus-code audit table refresh, by reference: in
     `## Execution log`, re-audit the touched rows of the table in
     `docs/plans/2026-11-02-program-theory-reconciliation-plan.md`
     (`### Audit table`): row 4.3-a (its TaskMonitor heartbeat-clock
     evidence leaves the drift list; the row stays drifted for plan 2's
     sites) and any other row whose evidence names a file this plan
     touched. Each refreshed row cites the row it refreshes; unchanged rows
     carry forward by reference. Do not edit the reconciliation plan.
- Independent completed-work review: one scoped cross-family round via
  `skills/call-agent/SKILL.md` over the landed diff, the execution log, and
  the Task 5 measurements, before closing; findings dispositioned in
  `## Review Log`.
- Close: `Status: completed` here and in the index row in the same change.

- [WT-7] keyed possession probe (2026-11-04). Key, items P2, C2, B4 from the fourteen-item bank the owner reviewed on 2026-11-02 (held outside the repository; items reusable by the owner's instruction): P2 "A `weft top` daemon that keeps a live index of task status" (expected: a stateless CLI read model over queues is fine; a daemon with its own index is a second control plane [WT-5], "Observable with standard tools", [WT-2a.2]; a view, never a store); C2 "Add a 50 ms poll in the result waiter to catch a missed notification" (expected refusal: [ALT-WT-002]; [WT-7] "Event-driven against absence": name the event that cannot arrive before adding a timer); B4 "Audit the service worker and interactive loops" (expected: fixed timer floors standing in for readiness waits [WT-2a.6], [WT-7] "Event-driven against absence", [ALT-WT-021]). Taker: codex 0.161.0, read-only sandbox, a different family from the plan author and the bank drafter; its command log shows reads of `docs/program-theory.md` and nothing else. Grading rule as in plan 1 (pass on reaching the expected placement, refusal or prediction with at least one expected entry or one that entails it); graded by the series lead against the owner-reviewed bank. The owner's instruction of 2026-11-04: "Do all the probe keys. You can re-use them." Result: PASS, three of three. (1) a CLI display over authoritative queues with any index a disposable hint; a daemon owning status truth refused; cited [WT-2a.2], [WT-5], "Observable with standard tools", [ALT-WT-020]. (2) refused; find the missing wake source and repair its notification, a timer needs real clock work or justified absence detection; cited [ALT-WT-002], [WT-2a.6], "A quiet task does nothing", [WT-7] "Event-driven against absence". (3) work driven by competing clocks, polling loops or shared state instead of one owner's inputs, including a heartbeat duplicating a local clock and timer-polled pipe supervision; cited [WT-2a.6], [ALT-WT-004], [ALT-WT-005], [ALT-WT-021], "Subprocess and stream supervision is readiness-driven, not timer-polled". The probe is spent for this plan; the audit refresh (4.3-a) is recorded above. `completed` still waits on the owner's acceptance of the drafted rejected-alternative entry "Internal services started on demand and stopped when idle" (proposed in this plan, not in `docs/program-theory.md`), so the plan stays `promoted`.

## 6. Testing Plan

- Harness: `broker_env` and `workdir` (one `WeftTestHarness`); real
  `TaskMonitor`, real `HeartbeatTask`, real queues; the drive loop is the
  production `run_until_stopped` on its own thread in R1.
- What proves the change: R1 counts real reactor turns over a fixed quiet
  window on both backends (the acceptance sentence) and, through its kept
  stand-ins, catches a restored registration or retry timer without
  reaching real manager startup; R2 pins the PONG contract change; C1–C4
  pin that the heartbeat-live fixed-rate period and poke independence
  survive on the local deadline, with the grid phase anchored at the first
  cycle start (an
  inbox poke does not move the grid, an overrun gets exactly one follow-up,
  the follow-up survives the chained control-cleanup re-arm, and the
  deadline arithmetic matches the matrix); G1 pins that the kept clock
  fires a cycle — and self-maintenance — with no inbox message.
- Existing tests that protect the unchanged parts and must stay green
  without edits beyond the patch removals: the cycle-deadline and
  inbox-wake tests (lines 2988, 3020), the disabled-monitor test (3060,
  minus its patch), the catch-up deadline tests (from line 8340), the
  maintenance tests (11633–11773), every export/retention/[OBS.13] test in
  the module; and, for the heartbeat, every test in
  `tests/tasks/test_heartbeat.py` and `tests/core/test_heartbeat_helpers.py`
  not named in Tasks 6–7 (emission, coalescing, supersession, PING,
  ownership gating, endpoint and internal-submit tests). The heartbeat
  enqueue and gate tests in `tests/core/test_manager.py` change only as
  Task 8 names.
- Tempting to skip: the `emitting` case (it needs a live HeartbeatTask). It
  is in scope because "with the heartbeat emitting" is the exact two-clock
  condition; the `unavailable` case alone would only prove the retry timer
  is gone.
- F1 extension (Tasks 6–8), each test naming the mutation it catches: H1
  (helper restores `ensure_manager` or an endpoint/spawn write; a read-only
  lookup or a silent delay is outside what H1 can detect), H2
  (append-only upsert; a replace that keeps the old lease), H3 (incomplete
  deletion; cancel id not canonicalized), H4 (validation before write,
  including an unschedulable interval and a lease below its interval or
  beyond the maximum); T1 (consume
  instead of peek), T2 (restart with a non-empty queue not rebuilding, i.e.
  needing re-registration), T3 (idle exit with rows present), T4 (no
  pre-emission rebuild), T5 (inbox mutation still accepted), T6 (invalid,
  unschedulable, short-lease, or oversized-lease row kept or blocking), T7 (no notification interest, proved
  through the waiter signature; SQLite and PostgreSQL); M1
  (desire with an empty queue; desire still tied to the TaskMonitor flag),
  M2 (the queue never joins the Manager's retained waiter; SQLite and
  PostgreSQL), the modified
  enqueue test (a row written before any Manager launches on the next
  start; idle-timeout override removed), M1 `expired` case (desire from a
  row whose lease has ended; an all-expired queue left in place; a newer
  row deleted after a failed older delete), S1
  (status mirrors the predicate) and its expired case (status counting an
  expired row, or deleting it); T8 (cancel while paused never idles out); T9
  (no expiry term in the wait; no expiry-triggered rebuild while paused;
  expiry ignored altogether); T10 (residue kept and resurrected as the
  registration after the newest row expires; the newest row deleted despite
  a failed older delete). Do not mock `HeartbeatTask`, `Manager`, the broker, the queues,
  or the reactor wait; the only stand-in is H1's guard on `ensure_manager`,
  plus the existing fake-clock patterns in T3, T8, T9, T10, M1's `expired`
  case, and S1's expired case, and the one-shot failing `delete` on the
  registrations handle in T10's third case and M1's failed-delete check
  (it fails one call, then delegates to the real method).
- One bound, by decision (the reviewer's simplification note at the
  cross-family round of 2026-11-03, then the owner's seven-day ruling):
  the chain `interval_seconds <= lease_seconds <= HEARTBEAT_MAX_LEASE_SECONDS`
  is the only maximum, so there is no separate interval validator to
  overlap with. H4's huge-interval cases prove the writer rejects the row
  through the chain; T6's cases prove the same at the table build.
- Not tested, by decision: a structural "TaskMonitor imports nothing from
  `weft.core.heartbeat`" gate, and a persistent guard on the exact retired
  constant names (an earlier R3, dropped at round 1). A restored
  registration or retry timer fails R1; restoring a constant name without
  the behavior is no observable failure. Either gate would add ceremony.

## 7. Verification

Per task: the commands inside each task. Final gates, from the landed
state, all exit 0:

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py tests/specs/test_reactor_architecture.py tests/tasks/test_heartbeat.py tests/core/test_heartbeat_helpers.py tests/core/test_service_helper_connections.py tests/core/test_manager_runtime_connections.py tests/core/test_manager.py tests/commands/test_status.py tests/system/test_constants.py tests/core/test_task_monitoring.py -q
./.venv/bin/python -m pytest
./.venv/bin/python -m pytest -m ""
bin/pytest-pg --all
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/ruff format --check weft/core/monitor/task_monitor.py weft/_constants.py weft/core/manager.py weft/commands/system.py tests/tasks/test_task_monitor.py
./.venv/bin/python -m pytest tests/specs/test_spec_hygiene.py tests/specs/test_plan_metadata.py -q
bin/check-doc-paths
```

`bin/pytest-pg --all` is required because the zero-turn claim depends on
the backend activity waiter (`PRAGMA data_version` on SQLite,
`LISTEN`/`NOTIFY` on PostgreSQL), and so do the T7 and M2 wakes. Grep gates G-1 to G-4 (Task 3), G-5 (Task 6), and the
Task 4 greps are rerun and pasted. Completion is claimed only after the
work is committed (verified by `git log`), the Task 5 [WT-7] probe, audit
refresh, and completed-work review are recorded, and the index row is
`completed`.

Runtime observation after deploy: `weft task ping <monitor_tid>` shows no
`heartbeat` block under `extended.task_monitor`, and
`schedule.next_cycle_due_in_seconds` counts down to the next grid tick
(multiples of the interval from the first cycle), not to finish plus
interval. With no registration, `ps` shows no heartbeat process and `weft
status` reports the heartbeat disabled with evidence `no-registrations`;
after one `upsert_heartbeat`, `weft queue peek weft.state.heartbeats` shows
the row, a heartbeat process appears within one Manager turn, and the row
is still there after `weft task stop <heartbeat_tid>` and the Manager's
relaunch. After the row's lease ends, `weft queue peek
weft.state.heartbeats` shows no row and the heartbeat process exits on its
idle timeout.

## 8. Rollback

Stated before the tasks. The change is one commit (Tasks 3, 6–8, and 4)
plus the Task 5 theory edit; `git revert` of each restores the prior state.
No TID mapping, Monitor-store table, or config key changes shape. One
runtime-state queue is added: after a revert, rows left on
`weft.state.heartbeats` are inert (old code never reads that queue, and dumps
exclude it); clear them with `weft queue delete weft.state.heartbeats` if
wanted. Mixed versions (processes from before and after an upgrade sharing
one broker until the old ones exit): an old TaskMonitor under a new Manager
registers through the heartbeat inbox, which now rejects the row; the old
Monitor records the failure and keeps cycling on its local interval, as it
did whenever registration failed; an old registrant helper's
`ensure_heartbeat_service` finds no heartbeat (the new Manager does not
launch one for an empty queue) and times out. A new registrant under an old
Manager writes a row that the old Manager never reads; the row waits until a
new Manager starts. No in-repo registrant exists after Task 3, so none of
these paths is exercised by Weft itself. No rollout order. One-way doors:
none (the PONG block removal, the helper signature, and the queue are all
reversible by revert; no reader of the PONG block or caller of the helpers
exists in this repository or its siblings, E6 and E15).

## 9. Owner forks

Reported, not decided by this plan. F1 and F5 are closed by the owner. The
F2 default is what the tasks implement; F3 and F4 carry no default.

- **F1 — what the Manager does with a heartbeat nobody registers with.
  CLOSED (owner, 2026-11-02).** The owner's ruling, verbatim: "Yes, that is
  the correct shape for the heartbeat." The shape accepted: registration is a
  row on a fixed queue (`weft.state.heartbeats`), one row per live
  registration keyed by `heartbeat_id` and carrying the registrant's TID
  (upsert replaces, cancel deletes); `upsert_heartbeat` / `cancel_heartbeat`
  write it and return, with no endpoint lookup, no `ensure_manager`, no
  registry poll, and no startup timeout; the Manager desires the heartbeat
  service if and only if that queue is non-empty and watches it so a first
  registration wakes it; the heartbeat builds its table by peeking, never
  consuming, so registrations survive heartbeat and Manager restarts with
  no re-registration and no retry timer; with an empty table the heartbeat
  exits on its idle timeout and is not restarted; a row written before any
  Manager exists waits for the next Manager start; a registrant that dies
  without cancelling is retired by its own lease (fork F5, closed
  2026-11-03). Implemented by Tasks 6–8, which adopt the older draft's
  option L3 in
  this concrete form. The earlier default (L2b, keep the TaskMonitor gate as
  launch policy) and the alternatives L2/L2f are not taken. The ruling did
  not name the queue; the plan chose `weft.state.heartbeats` (runtime-state,
  excluded from dumps), inside the ruling's terms. Retirement is the
  registrant's own lease (owner ruling 2026-11-03, fork F5).
- **F2 — cadence anchor.** Default: a local grid anchored at the first
  cycle start with one owed follow-up after an overrun (the older draft's
  Task 4 shape), implemented by Task 3 items 2 and 9–11 and proven by
  C1–C4. It preserves the fixed-rate period and poke independence; the
  grid phase is anchored at the first cycle start rather than at
  registration ingestion, so register rows 3, 3a, and 4 carry no
  diminution under it; the phase change itself is
  register row 3b, class C (same period and the same one-interval
  worst-case delay bound; only the phase anchor moves), which the owner
  accepts or rejects with this fork; its cost is one deadline method,
  two fields, and a matrix test. Alternative: finish-anchored (`finish +
  interval`, or `finish + catch-up` while a policy reports backlog), the
  Monitor's existing local deadline unchanged. It is simpler but a
  diminution (class D, rows 3, 3a, 4): with interval 60 and a 10 s cycle
  the next cycle starts at 70 instead of 60; an inbox poke just before a
  tick postpones the next periodic cycle by nearly an interval; a cycle
  that overruns a tick gets no immediate follow-up. Choosing it needs the
  owner's explicit acceptance of that diminution under the no-diminution
  rule; then C1–C4 and Task 3 items 9–11 drop, item 2 drops its two new
  fields, and the [MF-5], catch-up-sentence, and Quick Reference
  replacements revert to finish-anchored wording before review closes.
  Default flipped at round 1 disposition on the no-diminution rule.
  CLOSED: closed on the owner's implement instruction (2026-11-03) with the
  recommended option: (a), the local grid anchored at the first cycle start
  with one owed follow-up after an overrun (Task 3 items 9–11, cases C1–C4,
  as implemented).
- **F3 — the older draft's overlap.**
  `2026-09-08-service-wake-model-and-heartbeat-decoupling-plan.md` (draft)
  covers this TaskMonitor slice and more. This plan neither edits it nor
  recommends an outcome: the owner decides whether to narrow it to its
  remaining scope or supersede it. Since the F1 ruling (2026-11-02) this
  plan also adopts that draft's L3 (on-demand launch), which narrows it
  further: what remains there is the interval floor (its Q2), its [CC-2.5]
  wake-model paragraph, and its other launch options L2/L2f, which the F1
  ruling makes moot; its grid cadence is carried here as F2's default.
  CLOSED: closed on the owner's implement instruction (2026-11-03) with the
  recommended option: the overlap is recorded here; the older draft is not
  touched by this plan.
- **F4 — acceptance of the Task 5 theory edit.** The [ALT-WT-004] Kind,
  Current consequence, and Evidence wording in Task 5 lands only with the
  owner's acceptance, as for any [ALT-WT-*] change. CLOSED: accepted by the
  owner (2026-11-03: "I accept the theory edits"); applied verbatim as its
  own "Theory:" commit after the code and spec commit.
- **F5 — evidence-free registrants. CLOSED (owner, 2026-11-03).** Raised
  at the cross-family round on the on-demand addition (its finding F9,
  RECONSIDERATION REQUESTED): a retirement rule copied from the streaming
  rows retires a row whose owner has neither a `weft.log.tasks` status nor
  a `weft.state.tasks.<tid>` row, which treats absence as death, contrary to
  [WT-2a.13]. The owner's ruling, verbatim: "Why not make heartbeat requests
  carry their own expiration? If something wants it to go later, it can
  resubmit." Asked whether renewal happens on each tick, the owner answered
  no: renewal is not a protocol; the request carries its lifetime, the
  heartbeat stops at expiry, and wanting more means asking again, whenever
  the registrant likes. The shape applied: every row carries `lease_seconds`
  (at least its own interval, at most `HEARTBEAT_MAX_LEASE_SECONDS`, seven
  days by the owner's further ruling of 2026-11-03: "Allow up to seven days.
  Note that is the current constant and can be changed (up or down), it is
  not intrinsic to weft"; the earlier 2**53 was a float-safety bound, not a
  policy); expiry is computed from the row's message ID; the heartbeat deletes an expired
  row at its deadline (Task 7); the Manager desires the service only while an
  unexpired or invalid row exists and deletes rows only when every row has
  expired (Task 8); resubmission is an ordinary upsert. The registrant's own
  declared lease is the only liveness statement, so no reader infers death
  from absence and [WT-2a.13] holds by construction. The three former
  options, as history: (a) retire only terminal registrants through a
  runtime-state prune group, leaving an evidence-free registrant's row until
  it cancels (the former default); (b) the streaming rule as first drafted,
  retiring evidence-free rows after `min_age`, the reviewer's objection;
  (c) require a live task snapshot for the registrant at registration time,
  read by `upsert_heartbeat`. The closure removed the former Task 9
  (dead-registrant retirement through the runtime-state prune engine), the
  `heartbeats` prune group with its classification and `system prune
  --queue` value, the two spec Replacement blocks that described them, and
  the coupling of retirement to registrant lifecycle evidence. A
  guaranteed first emission is not promised; if the owner wants one, the
  floor would become a multiple of the interval or the lease would be
  measured from the build, both owner decisions (raised by the cross-family
  round of 2026-11-03). First-emission note: closed on the owner's implement
  instruction (2026-11-03) with the recommended option: as the plan states,
  no first emission is guaranteed; a registrant resubmits whenever it likes;
  there is no renewal protocol.
- **Owner redirect, 2026-11-03: services are always on. DECIDED.** It
  supersedes, for the heartbeat, the ruled Manager desire predicate ("a row
  that is unexpired or invalid"), the Manager's all-expired delete role and
  its ascending-order barrier, the Manager's notification interest and
  cursor on `weft.state.heartbeats`, the heartbeat's idle exit, and S1's
  table read. The owner's statements, verbatim, in order:
  1. "Why is the manager checking the heartbeat 4x a second? It should only
     be looking at the heartbeat queues when there is a change there,
     triggered by the ordinary queue watcher"
  2. "The heartbeat can peek on its own wakeup. But the manager should only
     care if there is a change"
  3. "The manager should not be deleting. That is the heartbeat service's
     responsibility."
  4. "The manager's responsibility is starting and stopping things"
  5. "It sees an upsert and makes sure the service is running"
  6. "The heartbeat service is the only one that should read from and
     delete its queues"
  7. (after the implementer reported that a PEEK lane re-delivers the oldest
     row on every pass) "I think we do always alive for services. That was
     what we had before, and I think what this shows is that if what is
     wanted is a ServiceTask, then we accept that there will be a process"
  8. (the reason) "This is negative knowledge: trying to make services stop
     and start ended up creating too much state. The simpler, more robust
     design is always on services, with care taken that the service idles
     when not needed."
  Final rule: the heartbeat is a standing internal service of the canonical
  Manager like TaskMonitor and LivenessMonitor (started at startup
  unconditionally, relaunched by ordinary convergence, stopped in plan 4's
  services phase); the Manager never reads, deletes, or watches
  `weft.state.heartbeats`; the heartbeat alone owns that table and no longer
  idle-exits, blocking on its watcher with no timer when the table is
  empty; `weft status` reads no table. The plan stays `promoted`. The
  `## Proposed Spec Delta` blocks for the flow line, the Manager bullet, the
  idle-exit bullet, [MA-1] item 7, the Quick Reference queue row and
  [QUEUE.7] now carry the redirected text, as landed.

## Review Log

self-driven fresh-eyes review: done 2026-11-02 (distinct pass after drafting): 14 corrections — [MA-1] item 7 range 289–291 in §2; catch-up sentence range 1999–2001 (three places); spec 07 grep ranges and the line-1015 hit in §2 and E7; `clear_processor_requests` line 2568; counted-turn precedent lines and test name; first-cycle return lines 4974–4975; E8 adds test line 3181; E12 and Task 4 now record the existing index row; `ensure_manager()` blocking bound stated in §3 and register row 5; [MF-3.2] replacement scoped to built-in services; [MF-5] replacement counts control messages as turn inputs; Task 3 docstring must not say "heartbeat" (G-2); R1 names the missing `HEARTBEAT_MIN_INTERVAL_SECONDS` import; older-draft overlap and theory-edit acceptance added to §9 as F3/F4.

- Cross-family review round 1 (codex 0.161.0, 2026-11-02, whole plan after the fresh-eyes pass): BLOCKED. F1 (P1, finish-anchored cadence is a diminution: next cycle at finish+interval versus the heartbeat grid; poke postponement; no overrun follow-up) accepted: rows 3–4 reclassified D, fork F2 default flipped to the local grid with one owed follow-up, owner decision open. F2 (R1 ingests one registration per turn), F3 (controlled failure and thread guard), F4 (backlink grep), F5 ([WT-7] closeout), F6 (path), F7 (flag semantics): all P2, accepted and applied. R3 dropped as ceremony. Round 2 (codex 0.161.0, 2026-11-02, scoped to F1–F4, F5–F7 confirmed): F2–F7 closed; F1 returned three residuals inside its applied span (C2 processor swap before cycle 2; C4 baseline interception and G-3 allowance; the grid's phase anchor differs from the registration-anchored heartbeat grid, so 'exact cadence' is overclaimed), applied here without a further round under the one-scoped-round rule and recorded as a class C phase-anchor row under fork F2. Review loop closed; the plan waits on the owner's F1–F4 rulings.
- owner ruling applied 2026-11-02: F1 settled by on-demand launch from a registrations queue; tasks added (cross-family round on the addition pending).
- Cross-family review round 1 on the on-demand addition (codex 0.161.0, 2026-11-02, scoped to Tasks 6–9, their tests, the new Replacement blocks and couplings): BLOCKED. F1 (P1, unschedulable interval becomes a poison row; zero backoff), F2 (P1, pruning an interrupted replacement can resurrect a superseded row), F3 (cancel while paused prevents idle exit), F4 (cancellation boundary overclaimed), F5 (T7/M2/T3/H1/P2 proofs), F6 (cancel id canonicalization), F7 (Tasks 6–7 GREEN gate inseparable), F8 (manual-test observation step): all accepted and applied. F9 (retiring evidence-free registrants treats absence as death): RECONSIDERATION REQUESTED, recorded as fork F5 for the owner with default (a) terminal-only. Round 2 (codex 0.161.0, 2026-11-02, scoped to F1–F4, F7, F8; F5, F6 and the F9 fork confirmed): no blocker; all closed with live evidence (the shared row model bounds the interval to [60, 2**53] at writer and rebuild, overflow reproduced for 10**400; the two-pass monotonic retirement avoids the per-row owner decision at runtime.py:539 that caused revival; the waiter checks observation at entry and after the strategy returns, and the audit deadline stays active while paused; destination writes at heartbeat.py:359 follow registration selection, so the at-most-one-emission boundary is the true guarantee; Tasks 6–7 share one GREEN gate; manual tests pass through a positive-timeout wait). simplebroker_pg's exact delete sends no NOTIFY (installed _sql.py:82), so the 1.0 s audit timer caps delete observation on PostgreSQL. Three editorial residues (audit-timer dependency sentence, float-precision wording, H1 summary) corrected after the round. Review loop on the addition closed; the plan waits on the owner's F2, F3, F4 and F5 rulings.
- owner ruling applied 2026-11-03: fork F5 closed by registrant-declared leases (`lease_seconds` on every row; expiry at the heartbeat's deadline; the Manager desires the service only while an unexpired or invalid row exists); Task 9 and the `heartbeats` prune group removed; cross-family round on the lease change pending.
- Cross-family review round 1 on the lease change (codex 0.161.0, 2026-11-03, scoped to the F5 ruling as applied): BLOCKED. F1 (P1, a failed older-row delete followed by a successful newest-row delete revives the older row): accepted, both deleters delete older rows first and stop at the first failure, newest row kept. F2 (T9 did not isolate the expiry wake; rewritten with lease 90, a paused phase, and the 0.3 s timeout assertion), F3 (the floor does not guarantee a first tick; rationale corrected, floor kept, first-emission guarantee noted for the owner), F4 (message-ID bounds: quantization, regression carry-forward, allocation before commit, same generator on PostgreSQL, cross-host skew), F5 (one launch predicate everywhere; expiry sentence qualified by the rebuild boundary), F6 (T6 gains lease rejection at the build): all P2, accepted and applied. Manager predicate cost recorded as bounded by registration count with a Task 5 measurement. One residual found while applying F1 and corrected before round 2: holding the rebuild flag after a failed delete would make `_has_pending_observation` report a change at every wait (the Stop-if spin); a failed delete is now best-effort and retried at the next build, and T10's third case asserts the flag is clear. Round 2 (codex 0.161.0, 2026-11-03, scoped to F1–F6 and the residual): F3, F4, F5, F6 closed with live evidence (`monotonic() + interval` at heartbeat.py:254 for the first-tick gap; `_next_components` carry-forward at _timestamp.py:297 and allocation before commit at db.py:2211; destination writes after registration selection at heartbeat.py:340; the rejection path at heartbeat.py:221). F1 and F2 returned one residual each inside their applied spans, applied here without a further round under the one-scoped-round rule: the §4 error-path summary still said a failed delete leaves the rebuild pending (now: best-effort, flag cleared, retried at the next build, as Task 7 item 4 a says); T9 asserted the 0.3 s expiry timeout while the audit deadline armed by the 61 s turn was stale (`next_wait_timeout` would return 0.0; T9 now runs one paused turn at 89.7 s to re-arm it before the assertion). Review loop on the lease change closed; the plan waits on the owner's F2, F3 and F4 rulings and the first-emission note under F5.
- owner ruling applied 2026-11-03 (after the lease round): `HEARTBEAT_MAX_LEASE_SECONDS` is seven days (604,800 s), a policy value the owner may change, not intrinsic to Weft; the separate `HEARTBEAT_MAX_INTERVAL_SECONDS` (2**53, a float-safety bound) is dropped because the chain `interval <= lease <= maximum` bounds the interval; H4 and T6 cases restated through the chain. A value change inside bounds the rounds already checked; no further round.
- owner correction applied 2026-11-03: the tension with [ALT-WT-019] (queues as unconsumed tables or event logs) made explicit in §4, the Source Documents, the read-first list, and the [MF-3.2] rule: a registration row is an active leased request, one token per live `heartbeat_id`, consumed at the moment the request ends (cancel or lease expiry); the reader is not the consumer; no row outlives its request, no tombstone or expired-marker record exists, no sweep runs; the upsert's older-row delete is the second half of a replacement finished by the next build, not a history collection. Plan metadata gate and `bin/check-doc-paths` re-run.

- owner closures recorded 2026-11-03 at implementation: F2 closed on the owner's implement instruction (2026-11-03) with the recommended option: (a), the grid anchored at the first cycle start; F3 closed on the owner's implement instruction (2026-11-03) with the recommended option: overlap recorded, the older draft not touched; the F5 first-emission note closed on the owner's implement instruction (2026-11-03) with the recommended option: no first emission is guaranteed, a registrant resubmits whenever it likes, no renewal protocol; F4 accepted by the owner (2026-11-03: "I accept the theory edits").
- Cross-family completed-work review round 1 (codex 0.161.0, 2026-11-03, range ecf7ad42..db824087): BLOCKED on F1 (both registration decoders caught `JSONDecodeError` only, so an oversized integer in a row raised `ValueError` past the malformed-row disposition and could crash-loop the emitter): accepted and fixed in `1409b4f2`, with the T6 case. F2 (literal planned assertions absent in H1, H3, T8, T9, T10): accepted and applied. The reviewer confirmed all 18 Replacement blocks verbatim, the lease model as ruled, the rebuild continuation and helper split as behavior-preserving, no remaining readers of the removed events, and the heartbeat interest composing with plan 4's drain interests; it corrected the log's predicate-call-rate attribution (a call rate, not a turn rate; no new poll interval). Round 2 pending.
- owner redirect applied 2026-11-03 (statements 1–8 in `## 9. Owner forks`): services are always on; the Manager's heartbeat predicate, interest, cursor, and delete role, the heartbeat's idle exit and ownership-audit timer, and the status table read are removed. The implementer first reported a fork on the intermediate rule (a body-ignoring PEEK lane re-delivers the oldest row every pass, `MultiQueueWatcher._fetch_next_message` → `peek_one`); the owner resolved it with statement 7. Not yet reviewed cross-family.

- Scoped round 2 (codex 0.161.0, 2026-11-03, range db824087..1b319004): round-1 F1 confirmed fixed; the always-on redirect confirmed implemented (18 Replacement blocks verbatim; no Manager reference to weft.state.heartbeats; test dispositions justified; audit-timer removal loses no spec-mandated bound). BLOCKED on F1 (the watcher's `_activity_hint_action` calls `_has_pending_observation` on hint-free strategy returns, so the resident heartbeat reads its table about 10 times a second at idle: 'no poll' not met; pre-existing watcher behavior made permanent by the resident service): accepted; fix at the watcher boundary; the owner decided on 2026-11-03 ("Own plan. Make sure it stays consistent with the program theory") that it lands as its own plan, `docs/plans/2026-11-03-watcher-hint-gated-observation-plan.md`, after series plans 11, 12 and 8 ([CC-2.1] contract). F2 (stale idle-shutdown, supersession-check and Manager-reader sentences in specs 01 and 05 and two docstrings): accepted and fixed in the commit that adds this entry. No round 3 under the owner's one-scoped-round rule; the F1 fix, when landed, is verified by the series lead.
## Execution log

Plan-authoring resolution checks (2026-11-02, `b7eed9f9`, author). Each
line: command → output.

- E1: `grep -o "_heartbeat_registered" weft/core/monitor/task_monitor.py | wc -l` → `10`.
- E2: `grep -ci heartbeat weft/core/monitor/task_monitor.py` → `44`; the
  same grep excluding registration symbols leaves lines 43, 46, 2860, 2861,
  2863, 4952 (docstring), 5258 (PONG block) — 5 survive Task 3.
- E3: `grep -c '"upsert_heartbeat"' tests/tasks/test_task_monitor.py` →
  `119`; `grep -c upsert_heartbeat tests/tasks/test_task_monitor.py` → `119`.
- E4: `grep -rn "upsert_heartbeat\|cancel_heartbeat\|ensure_heartbeat_service" --include=*.py weft extensions integrations bin`
  (excluding `weft/core/heartbeat.py`) → only `weft/core/monitor/task_monitor.py`
  lines 84, 5082, 5722, 5746, 5751.
- E5: `grep -rln -i heartbeat weft/client weft/ext* weft/commands weft/cli`
  → `weft/commands/system.py`, `weft/commands/_task_snapshot_reducer.py`
  (status classification only).
- E6: `grep -rln "upsert_heartbeat\|_weft.heartbeat\|weft.core.heartbeat" --include=*.py /Users/van/Developer`
  (excluding `.venv`, `site-packages`, and the weft repository and its
  worktrees) → no hits.
- E7: `sed -n 484,664p docs/specifications/07-System_Invariants.md | grep -i heartbeat`
  → no output; `sed -n 993,1200p docs/specifications/07-System_Invariants.md | grep -in heartbeat`
  → one hit, block line 23 (file line 1015, manager registry heartbeats). Whole-file hits are lines 173 (payload-directed Heartbeat
  destinations), 192, 816 (file list), 1015 (manager registry heartbeats):
  none about Monitor wakes.
- E8: `grep -rn '\["heartbeat"\]\|get("heartbeat")\|next_registration_attempt_in_seconds' weft extensions integrations tests --include=*.py`
  → only `weft/core/monitor/task_monitor.py:5262` and
  `tests/tasks/test_task_monitor.py:3181`, `:3185` (the R2 assertion; no
  reader of the PONG block).
- E9: scratch probe (outside the repository; `WeftTestHarness`, custom
  mode, interval 60, 2.5 s window after settle, SQLite) at `b7eed9f9` →
  `registered 0`, `unavailable 2`.
- E10: symbols resolved by grep at `b7eed9f9`: `TaskMonitor.next_wait_timeout`
  (4949), `_process_reactor_turn` (5009), `_handle_work_message` (5067),
  `_handle_control_command` (5076), `_task_monitor_pong_extension` (5222),
  `_ensure_heartbeat_registered` (5714), `_cancel_heartbeat` (5746),
  `_finish_monitor_cycle` (5956), `_handle_control_cleanup_worker_result`
  (6267), `_maybe_run_maintenance_pass` (4104), `_monitor_monotonic` (609),
  `make_task_monitor_taskspec` (`task_monitor.py`), `progress_requires_catchup`
  (`progress.py` 100), `Manager._reconcile_managed_services` gate
  (7153–7157), `Manager._build_heartbeat_spawn_payload` (5922),
  `Manager._build_task_monitor_spawn_payload` (5958),
  `weft/commands/system.py::_service_enabled` (894), `HeartbeatTask`
  (`weft/core/tasks/heartbeat.py`), `make_heartbeat_taskspec`
  (`tests/tasks/test_heartbeat.py` 50), `RETIRED_REACTOR_CAPS`
  (`tests/specs/test_reactor_architecture.py` 26), test functions at lines
  1004, 2988, 3060, 3104, 10118, 11633 of `tests/tasks/test_task_monitor.py`,
  helpers at 2215, 2260, 2478, 2568, 11513.
- E11: spec codes resolved: [MF-3.2] (05:441), [MF-5] (05:538), [CC-2.3]
  (01:487), [CC-2.5] (01:876), [CC-2.2.1] (01:293), [MA-1] (03:122, item 7
  at 03:276), [MA-1.6a] (03), [IMPL.8]–[IMPL.11], [OBS.13.10], [OBS.13.11],
  [OBS.13.12], [MANAGER.15]–[MANAGER.18] (07); theory codes [WT-2], [WT-2a.6],
  [WT-4], [WT-7], [ALT-WT-004], [ALT-WT-005], [ALT-WT-020] in
  `docs/program-theory.md`.
- E12: the `docs/plans/README.md` Status Index row exists (`draft`);
  `tests/specs/test_plan_metadata.py` and `bin/check-doc-paths` pass on this
  file (fresh-eyes pass, 2026-11-02).

F1 extension resolution checks (2026-11-02, `b7eed9f9`, author). Each line:
lookup → finding. The two owner-flagged checks are E14 and E18.

- E13: `HeartbeatTask` registration processing today:
  `sed -n 150,270p weft/core/tasks/heartbeat.py` → `_process_reactor_turn`
  (150) runs worker results, one `ctrl_in` row (`_drain_one_control_message`,
  179), one inbox row (`_drain_one_registration_message`, 196: `move_one`
  inbox → reserved, then `_handle_work_message`, 217), `_exit_if_superseded`,
  `_emit_due_registrations`, `_maybe_idle_shutdown`. Request shape:
  `HeartbeatMutation` (50–82; `action` `upsert`|`cancel`, `heartbeat_id`,
  `interval_seconds`, `destination_queue`, `message`; `extra="forbid"`;
  interval ≥ `HEARTBEAT_MIN_INTERVAL_SECONDS` and non-empty destination for
  upsert). Invalid → `heartbeat_request_invalid` plus
  `reserved_policy_on_error` (225–231); cancel pops the in-memory entry
  (235); upsert stores `HeartbeatRegistration(next_due_at=monotonic() +
  interval)` (249–256) and pushes the heap; both ack by exact reserved
  delete. Registrations live only in `self._registrations`.
- E14 (owner check 1, idle-exit semantics): idle exit keys on an **empty
  table**, not inbox silence. `_maybe_idle_shutdown` (471–491) returns
  `False` while `self._registrations` is non-empty and when
  `_idle_timeout_seconds <= 0`; `_empty_since_monotonic` is set at
  construction (128) and when a cancel empties the table (236–237), cleared
  by an upsert (261) and by `_maybe_idle_shutdown` itself while rows exist
  (473); `_next_idle_timeout` (526–531) is `None` while rows exist. Inbox
  traffic other than upsert/cancel does not touch it. Default
  `HEARTBEAT_IDLE_TIMEOUT_SECONDS` = 60.0 (`weft/_constants.py` 1637);
  the Manager overrides it with `heartbeat_idle_timeout: 0.0`
  (`weft/core/manager.py` 5945), pinned by `tests/core/test_manager.py`
  2061–2063, so the supervised service never idles out today. Also found:
  `next_wait_timeout` (493–514) always includes `_next_ownership_audit_at -
  now`, re-armed each turn to `HEARTBEAT_OWNERSHIP_AUDIT_INTERVAL_SECONDS` =
  1.0 (1640; `_exit_if_superseded`, 455–459), so a live heartbeat runs a turn
  about once a second with zero registrations (code reading, not measured;
  not owned by any 2026-11 plan per `grep -ln "OWNERSHIP_AUDIT" docs/plans/*.md`
  → only the retired 2026-09-17 restoration plan).
- E15: `sed -n 200,289p weft/core/heartbeat.py` → `ensure_heartbeat_service`
  (200) resolves `_weft.heartbeat`, proves liveness via
  `_heartbeat_endpoint_candidate` (116: endpoint status, a full scan of
  `weft.log.tasks` in `_latest_heartbeat_task_status`, the runtime handle, a
  keyed PING with `HEARTBEAT_ENDPOINT_PROBE_TIMEOUT` 0.25 s), else calls
  `manager_runtime.ensure_manager` and loops `time.sleep(MANAGER_REGISTRY_POLL_INTERVAL)`
  until `startup_timeout` (default `MANAGER_STARTUP_TIMEOUT_SECONDS`), then
  raises `RuntimeError`. `upsert_heartbeat` (236) and `cancel_heartbeat`
  (265) both call it, then write the mutation to the endpoint inbox (45).
  Callers: `grep -rn "ensure_heartbeat_service\|upsert_heartbeat\|cancel_heartbeat\|_write_heartbeat_request\|_heartbeat_endpoint_is_live\|_heartbeat_endpoint_candidate" --include=*.py .`
  (excluding `.venv`) → `weft/core/heartbeat.py` (16 lines),
  `weft/core/monitor/task_monitor.py` (5; removed by Task 3),
  `tests/core/test_heartbeat_helpers.py` (14), `tests/tasks/test_task_monitor.py`
  (120; the Task 2 stand-ins remain), `tests/core/test_service_helper_connections.py`
  (3), `tests/core/test_manager_runtime_connections.py` (1, line 200). No
  production caller needs an endpoint wait after Task 3; E6 already found no
  sibling-repository caller. Decision: delete, do not reduce.
- E16: Manager gate and wake. `grep -n "_task_monitor_enabled" weft/core/manager.py`
  → heartbeat gate at 7156 (`_reconcile_managed_services`, comment 7153–7155),
  7760–7766 (`_pending_service_probes_for_scope`), 7822–7829
  (`_managed_service_convergence_active_reasons`); mirror at
  `weft/commands/system.py` 894–912 (`_service_enabled`) feeding
  `_service_snapshot_from_evidence` (956; `evidence="config-disabled"` at
  972). `_heartbeat_service_spec` (6025–6030) sets no `restart_backoff_ns`;
  TaskMonitor (6032–6038) and LivenessMonitor (6040–6046) use
  `_task_monitor_restart_backoff_ns`. Delivery queues:
  `_build_queue_configs` (1161–1195). Convergence clock: constants 1340
  (1.0 s active) and 1343 (5.0 s stable), `Manager.next_wait_timeout`
  (8158), `_run_managed_service_convergence` (7914; `force=True` bypasses
  the throttle, as the child-cleanup path at 8401 does). Busy-loop trap:
  `MultiQueueWatcher._has_pending_messages` (1297–1307) counts every config
  with a pending row, and `_activity_hint_action` returns on it. Safe shape:
  `Manager._activity_wait_queues` (1886) and `Manager._has_pending_observation`
  (1897–1925, `peek_many(..., after_timestamp=...)` cursor), consumed in
  `_process_manager_reactor_turn` (8355; startup feedback at 8371–8378).
  SimpleBroker `Queue.has_pending(after_timestamp=...)` (sbqueue.py 1801) and
  `Queue.write() -> int` (431) exist in the installed package.
- E17: tests. `make_heartbeat_taskspec` at `tests/tasks/test_heartbeat.py`
  50; heartbeat tests at 73, 110, 160, 237, 284, 324, 361, 395, 450, 472,
  497, 522, 555, 594 (inbox mutations in at least 110, 160, 237, 284, 324;
  the implementer confirms the rest by reading). Manager heartbeat tests:
  `test_manager_enqueues_heartbeat_through_service_path` (2036–2067, asserts
  idle 0.0), `_desired_internal_service_keys` (5341) and the four gate tests
  5363–5430. Status: `tests/commands/test_status.py` 2663 and 2686.
  Helper tests: `tests/core/test_heartbeat_helpers.py` 99, 132, 182, 202,
  243, 267, 284, 317, 353, 383, 420 (the endpoint and internal-submit tests
  among them call `weft.core.endpoints` or `spawn_requests`, not the
  deleted helpers; Task 6 sorts them by symbol).
  `tests/architecture/test_liveness_boundaries.py` 69–73 reads
  `RUNTIME_PRUNE_SUPPORTED_QUEUE_GROUPS` (only forbids `weft.state.tasks.`
  groups).
- E18 (owner check 2, dead-registrant cancel path): today none. The only
  removal of a registration is a `cancel` mutation (`_handle_work_message`
  235) or process exit; the supervised heartbeat never idles out (E14), so a
  registrant that dies without cancelling stays emitted to until the
  heartbeat restarts. `LivenessMonitor` (`weft/core/tasks/liveness_monitor.py`)
  deletes only `weft.state.tasks.<tid>` ([LIVENESS.R3], spec 07 line 697);
  it cannot reach another queue without widening its custody. The dead-TID
  sweep for runtime-scoped rows is `weft/core/pruning/runtime.py`
  (`run_runtime_prune_for_context` 183; `_streaming_candidates` 506–570:
  candidate when the owner TID is terminal in the log or has no status and
  no task-state row, after `min_age`), run by TaskMonitor maintenance
  (`task_monitor.py` ~4158–4173 with
  `TASK_MONITOR_MAINTENANCE_RUNTIME_PRUNE_QUEUE_GROUPS`, constants 1212–1218,
  `RUNTIME_PRUNE_DEFAULT_MIN_AGE_SECONDS` 3600 at 1352) and by `weft system
  prune` (`weft/commands/prune.py` 48, 250–280). It cannot reach a new queue:
  groups are the closed map at constants 1422–1428. Smallest consistent
  path: one new group reusing the streaming owner rule (Task 9); no new
  mechanism, and the record keeps a named rule (2026-09-04 retirement
  decisions). Superseded by the 2026-11-03 lease ruling (fork F5 closed):
  each row carries its own lease and no prune group is added.
- E19: spec texts for the new blocks. `docs/specifications/00-Quick_Reference.md`
  global queue table 31–42 (`weft.state.*` rows marked "No (runtime
  state)"); `docs/specifications/03-Manager_Architecture.md` [MA-1] item 7
  276–299; `docs/specifications/05-Message_Flow_and_State.md` [MF-3.2]
  441–516 and Cleanup Boundary 1772–1774; `docs/specifications/07-System_Invariants.md`
  [QUEUE.7] 162–192 (the notification-interest sentence at 181–182);
  `docs/specifications/10-CLI_Interface.md` 1052–1053. [QUEUE.1]–[QUEUE.8]
  carry no queue-naming rule for global queues beyond [QUEUE.3]
  (task-local `T{tid}.…`) and the [QUEUE.7] role/route declaration; naming
  follows `weft/_constants.py` (`WEFT_*_QUEUE` constants, `weft.state.`
  prefix `WEFT_STATE_QUEUE_PREFIX` at 1419, excluded from dumps by
  `weft/commands/dump.py` 40 and 56). Retired-phrase grep (Task 4) at
  `b7eed9f9` → 01:665, 03:290, 05:454, 456, 460, 480, 494, 514, 548, 2001,
  each replaced by a block.

Lease ruling resolution checks (2026-11-03, author of the lease edit). Each
line: lookup → finding.

- E20: message-ID shape and the nanosecond literal.
  `grep -n "low 12 bits\|MAX_LOGICAL_COUNTER" .venv/lib/python3.14/site-packages/simplebroker/_timestamp.py`
  → line 93 (`TimestampGenerator` docstring: "low 12 bits: logical counter
  for ordering within the same time base"; line 92 states the high bits are
  "nanoseconds after epoch, aligned to the counter granularity"), plus
  lines 22 and 306 (`MAX_LOGICAL_COUNTER` import and use); lines 136–140
  build the timestamp as `physical_ns & ~LOGICAL_COUNTER_MASK | logical`.
  The constants live in the installed `simplebroker/_constants.py` lines
  158–165: `LOGICAL_COUNTER_BITS = 12` ("one 4,096-nanosecond time grain")
  and `MAX_LOGICAL_COUNTER = 1 << LOGICAL_COUNTER_BITS`. So, under normal
  clock progression, a message ID read as an integer differs from the
  writer's sampled physical time in epoch nanoseconds by less than
  4,096 ns, which is what `expires_at_ns` relies on. Bounds corrected after
  the cross-family round of 2026-11-03 (its F4), each read in the installed
  packages: `TimestampGenerator._next_components` (`_timestamp.py` lines
  281–330) carries the previous physical component forward and advances
  the counter when the clock reads at or before the last stored timestamp,
  so after a clock regression the ID leads the wall clock by the whole
  regression (the reviewer reproduced an ID 120 s ahead of a supplied
  clock); on counter exhaustion it waits for the clock to advance or raises
  `TimestampError` (lines 306–323), never minting a future time. The
  timestamp is allocated inside the write transaction before the insert
  commits (`simplebroker/db.py::_do_write_transaction`, lines 2187–2222),
  so it precedes the commit instant. The PostgreSQL backend uses the same
  Python generator and stores its value through `advance_last_ts`
  (`simplebroker_pg/plugin.py` line 796, an `UPDATE meta SET last_ts`); no
  server-side clock allocates it. Consequences: §4 hidden couplings,
  "Clock skew".
  `grep -n "1_000_000_000" weft/core/manager.py` → among many hits, lines
  693, 712, 715, and 910 convert seconds to nanoseconds with the literal and
  no named constant; the lease methods follow that precedent.
  Overflow check behind the need for a lease maximum: `(10**400 *
  1_000_000_000) / 1_000_000_000` raises `OverflowError: integer division
  result too large for a float`; `(2**53 * 1_000_000_000) / 1_000_000_000`
  is `9007199254740992.0`; the policy value 604,800 is far inside that
  range.

### Implementer (2026-11-03, at `ecf7ad42`)

Task 0, comprehension answers (written before the first edit).

1. After the change the TaskMonitor reactor wakes only on rows on its own
   `T{tid}.inbox` and `T{tid}.ctrl_in` (through the shared
   `MultiQueueWatcher` wait), on local worker-result notifications from its
   registered worker groups (built-in cycle, processor, control cleanup), and
   on the one deadline `TaskMonitor.next_wait_timeout()` returns:
   `_next_cycle_due_monotonic` (0.0 while the first cycle is pending, a wake
   is requested, or worker results are pending). That deadline is set only
   by `TaskMonitor._arm_next_cycle_deadline`, called from
   `_finish_monitor_cycle` and `_handle_control_cleanup_worker_result`: the
   next grid tick (anchored at the first cycle start, advanced at each cycle
   start in `_run_monitor_cycle`), or now when an overrun follow-up is owed,
   capped at now plus the catch-up interval while `progress_requires_catchup`
   reports backlog. Self-maintenance keeps its own monotonic deadline but is
   checked only inside a cycle, so it adds no wake.
2. After Task 3 and before Task 8, `Manager._reconcile_managed_services`
   still desires the heartbeat service whenever `_task_monitor_enabled` is
   true (launch policy), and `_build_heartbeat_spawn_payload` sets
   `heartbeat_idle_timeout: 0.0`, so the service never idles out.
   `WEFT_TASK_MONITOR_ENABLED=0` removes the launch and restart desire, but
   convergence never sends STOP to a live service merely because it left the
   desired set; a running heartbeat runs until STOP/KILL on its control
   queue or supersession.
3. After Tasks 6–8: `upsert_heartbeat` validates the row with
   `HeartbeatRegistrationRow`, writes it to `weft.state.heartbeats`, deletes
   the older rows of the same `heartbeat_id`, and returns; the registrant
   waits for nothing. The write wakes the canonical Manager through its
   notification-only interest (`Manager._activity_wait_queues` plus the
   strict cursor in `Manager._has_pending_observation`, never a delivery
   config); the next turn forces one convergence pass whose predicate
   `Manager._heartbeat_registrations_present` (a row that is unexpired or
   invalid) adds the heartbeat service and enqueues its spawn on
   `weft.spawn.internal`. The heartbeat starts, peeks the queue in
   `_rebuild_registrations`, and first emits one interval after that build.
   A dead registrant's row stays, desired and emitted to, until its lease
   ends at the row's message ID plus `lease_seconds`; the heartbeat publishes
   that instant through `next_wait_timeout()`, and the turn at or after it
   rebuilds, deletes older same-id rows first (stopping at a failed delete),
   and deletes the expired newest row with a `heartbeat_registration_expired`
   event. Nobody consults the registrant's liveness. When the last row is
   deleted, the heartbeat's observation sees a table row gone at its next
   wait entry (paused or not), rebuilds to an empty table, and exits after
   `HEARTBEAT_IDLE_TIMEOUT_SECONDS`; a turn whose pre-emission rebuild saw
   the delete emits nothing, and a delete after that rebuild may be followed
   by at most one emission. The Manager, seeing an empty queue (or only
   expired rows, which it deletes oldest first), does not restart it.

Task 1 (independent review of plan and delta): already done before this
dispatch; the Review Log above records the cross-family rounds on the
original plan, the on-demand addition, and the lease change, each closed.
No further round was run by the implementer.

Task 2 (RED), `tests/tasks/test_task_monitor.py` only. Added R1 (both
`heartbeat_mode` cases), C1, C2 (with the module-level
`overrunning_processor` and its `OVERRUN_PROCESSOR_CLOCK` binding), C3 (11
matrix rows), C4, G1, and the R2 replacement. Test-arrangement deviation,
recorded: C1's inbox poke is driven with `drive_task_monitor_until_observed`
(the real wait, then `process_once()`) instead of a bare `process_once()`,
because a bare `process_once()` does not observe an inbox row written after
the previous wait (the first attempt failed with `assert 1 == 2` on the
request count, a broken test, not RED). C3 also carries the two
`raising=False` stand-ins, as C1, C2, and C4 do.

- SQLite, the Task 2 Verify `-k` set: 17 failed, 1 passed (G1). Failure
  reasons, each the named one: R1 `unavailable` "monitor ran 2 turns over
  2.5s"; R1 `emitting` "monitor ran 2 turns over 2.5s"; C1 `assert 1110.0 ==
  1060.0`; C2 and C4 `assert 60.0 == 0.0` on `next_wait_timeout()`; C3
  `AttributeError: 'TaskMonitor' object has no attribute
  '_arm_next_cycle_deadline'` (all 11 rows); R2 `assert 'heartbeat' not in
  {...}`.
- PostgreSQL, `bin/pytest-pg ".../test_task_monitor_quiet_interval_runs_zero_turns_with_heartbeat_emitting"`:
  2 failed; `unavailable` "monitor ran 3 turns over 2.5s", `emitting`
  "monitor ran 2 turns over 2.5s".

Task 3 (GREEN), as written in items 1–12, with two test-arrangement
deviations recorded here:

- C2 and C4 commit the overrunning cycle's worker results through a new
  test-local helper, `_drain_task_monitor_workers_without_turns` (real
  `wait_for_activity` plus `_drain_worker_results`, no reactor turn).
  `drive_task_monitor_until_idle` runs reactor turns, and once the owed
  follow-up is armed (`next_wait_timeout() == 0.0`), its next turn starts
  the follow-up cycle inside the drive; C2 first failed GREEN with `assert 2
  == 1` on the request count for that reason. RED was re-confirmed after the
  change by reverse-applying the production diff: C2 and C4 both fail with
  `assert 60.0 == 0.0`.
- `send_keyed_ping_probe` and the `CONTROL_STOP`, `CONTROL_KILL`,
  `ControlRequest` imports went with the deleted code (item 5; the test-side
  import was used only by the deleted blocking-probe test).

Verify: `./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py
tests/specs/test_reactor_architecture.py -q` → exit 0 (2 PostgreSQL-only
skips). Grep gates after Task 3: G-1 → 8 lines, all in
`weft/core/heartbeat.py`; G-2 → `5`; G-3 → only the `raising=False`
stand-ins (the `_patch_no_op_heartbeat_registration` helper used by C1–C4
and G1, and the two R1 patches; every one of the 119 pre-existing sites is
gone); G-4 → no output. Stand-in verification (test edit 3): with the
stand-in lines deleted in the working tree, R1 (2), C1, C2, C3 (11), C4, and
G1 → 17 passed; the file was then restored from the saved copy and the
stand-ins are committed.

Tasks 6–7 (one GREEN gate, as the preamble requires).

- Ordering note: the two new constants (`WEFT_HEARTBEAT_REGISTRATIONS_QUEUE`,
  `HEARTBEAT_MAX_LEASE_SECONDS`) were added to `weft/_constants.py` before
  the RED run so the new tests could import them; they are inert alone, so
  the RED reasons below are about the helpers and the task, not imports.
- Task 6 RED (H1–H4, 14 cases): 13 failed with `TypeError: upsert_heartbeat()
  got an unexpected keyword argument 'registrant_tid'` (H1, H2, H4 × 11); H3
  failed with `Failed: helper must not start a manager` (the old
  `cancel_heartbeat` reached `ensure_heartbeat_service` →
  `manager_runtime.ensure_manager`). Deviation: H3 carries the same
  `ensure_manager` guard as H1, because the old cancel path would otherwise
  start a real manager from the RED run; the guard also catches a restored
  call in cancel.
- Task 7 RED (T1–T10, 12 cases): all 12 failed. T1, T2, T3, T4, T7, T8, T9:
  the same `TypeError` on `registrant_tid`; T5 `assert 0 == 1` on the
  `heartbeat_request_invalid` count (the old inbox path accepted the
  mutation); T6 the bad rows were still in the queue; T10 `expired-newest`
  and `unexpired` left both rows (`[A, B] != [B]`), `failed-delete`
  `AttributeError: ... '_registrations_rebuild_pending'`.
- GREEN as items 1–8, with these deviations:
  1. Item 5 says a pending rebuild runs "and return (one bounded turn)". The
     rebuild runs and the turn continues to the supersession check, the due
     emission, and the idle check. With an early return, the first turn of
     every heartbeat (the flag starts `True`) skipped `_exit_if_superseded`,
     and four tests the plan requires to stay green without edits failed
     (`test_duplicate_heartbeat_services_converge_by_loser_exit`,
     `test_heartbeat_owner_resolution_is_endpoint_registry_version_gated`,
     `test_heartbeat_run_until_stopped_uses_next_wait_timeout`,
     `test_heartbeat_ping_while_waiting_is_handled_promptly`). The turn is
     still bounded (one peek of the registrations queue).
  2. Ruff C901 flagged `_rebuild_registrations` (19 > 10). Rather than add a
     new approved suppression, the build is split into
     `_rebuild_registrations` (table assembly, idle anchor),
     `_peek_registration_rows` (one peek, cursor advance, grouping, rows
     without a readable id) and `_current_registration_row` (the per-id
     rule a–c with the failure barrier); `_delete_registration_row` is the
     one best-effort exact delete all of them use. These four private
     methods go beyond the review gate's list; the behavior is item 4's.
  3. `weft/core/heartbeat.py` has one private helper, `_delete_rows_for_id`,
     shared by `upsert_heartbeat` (older rows of the id) and
     `cancel_heartbeat` (every row of the id), instead of two copies of the
     peek-and-delete loop. `cancel_heartbeat` canonicalizes through the row
     model's own validator (`HeartbeatRegistrationRow._validate_heartbeat_id`).
  4. The inbox-path lifecycle events `heartbeat_upserted` and
     `heartbeat_cancelled` are not re-created at table build (no reader in
     the repository; noted in CHANGELOG). `_delete_reserved_message` lost
     its only callers and is deleted.
  5. `HEARTBEAT_ENDPOINT_PROBE_TIMEOUT` is deleted from `_constants.py`: after
     Task 6 no module uses it (grep), as item 3 directs.
- Test migration dispositions: `test_heartbeat_service_accepts_upsert_and_cancel`,
  `test_heartbeat_duplicate_upsert_replaces_existing_registration`,
  `test_heartbeat_late_wake_coalesces_to_one_emit`,
  `test_heartbeat_serializes_structured_payloads_on_emit`,
  `test_heartbeat_pending_input_wakes_through_reactor_wait`,
  `test_heartbeat_next_wait_timeout_returns_zero_for_due_registration`:
  migrated to `upsert_heartbeat` / `cancel_heartbeat` and the manual-driver
  rule. `test_heartbeat_rejects_unsafe_destination_and_later_input_progresses`:
  the unsafe row is a direct row write (the writer rejects every `weft.`
  case first, asserted), the build deletes it and reports one
  `heartbeat_request_invalid`, and a later valid row still registers and
  emits. `test_heartbeat_failed_exact_ack_preserves_reserved_row`: its
  subject (the exact ack of an accepted mutation) is retired; retargeted
  into T5, which now also asserts the rejected inbox row is retained in
  reserved under the default keep policy. `make_heartbeat_taskspec` gained
  an optional `idle_timeout` keyword for T3, T8, and T9.
  `tests/core/test_heartbeat_helpers.py`: deleted the five
  `ensure_heartbeat_service` / endpoint-liveness tests the plan names; kept
  the `weft.core.endpoints` tests (`test_endpoint_owner_*`,
  `test_external_supervisor_endpoint_owner_*`) and both internal-submit
  tests. `tests/core/test_service_helper_connections.py`: dropped the
  `"heartbeat"` operation and the `_heartbeat_runtime_handle_is_live`
  assertion; rewrote the commit-without-closing test for the new signatures
  (connection count checked around each helper call separately, since the
  reader that proves the row opens its own connection); retargeted the
  failed-write test at a failing broker `write` inside `upsert_heartbeat`
  (the local facade is still closed, the owner still usable); migrated the
  destination-churn test. `tests/core/test_manager_runtime_connections.py`:
  dropped the `heartbeat_fallback=True` case. R1 `emitting` migrated as
  Task 7 states (one `process_once()` build, registrations queue unchanged).
- Mechanical consequences outside the plan's file list:
  `tests/commands/test_tid_mapping_contracts.py` imported the deleted
  `_heartbeat_runtime_handle_is_live` (an E15 grep miss); its one assertion
  and the import are removed. `docs/ruff-suppression-registry.md` and
  `tests/specs/test_ruff_policy.py`: the `PLR0402` directive on the old
  `manager_runtime` import in `weft/core/heartbeat.py` is gone with that
  import, so the RUFF-SUP-251 registry row, its index row, and the pinned
  counts (193 groups, 323 directives, `PLR0402=2`) go with it, as plan 2 did
  for RUFF-SUP-046.
- GREEN gate: `./.venv/bin/python -m pytest tests/core/test_heartbeat_helpers.py
  tests/core/test_service_helper_connections.py
  tests/core/test_manager_runtime_connections.py -q` → exit 0;
  `./.venv/bin/python -m pytest tests/tasks/test_heartbeat.py
  tests/core/test_service_helper_connections.py tests/tasks/test_task_monitor.py
  -q -k "heartbeat or quiet_interval"` → exit 0; `bin/pytest-pg` T7 and T8 →
  `2 passed`; `bin/pytest-pg tests/core/test_service_helper_connections.py
  tests/core/test_heartbeat_helpers.py` → `46 passed, 1 skipped`.
- G-5: `grep -rn "ensure_heartbeat_service\|_write_heartbeat_request\|_heartbeat_endpoint_is_live\|HeartbeatMutation" weft tests bin --include=*.py`
  → no output.

Task 8.

- Ordering deviation: the Manager and `weft status` GREEN edits were
  written before the Task 8 tests. RED was then taken by reverse-applying
  that production diff: 8 failed — M1 `empty`, `row-monitors-off`,
  `expired` (desire still tied to `_task_monitor_enabled`), the modified
  enqueue test (no heartbeat payload / idle-timeout override present), M2
  canonical (queue absent from the waiter signature), the convergence
  inverse (`missing_active_tid` with an empty queue), S1, and the
  liveness-only status test (`config-disabled` evidence). The diff was then
  re-applied.
- `Manager._has_pending_heartbeat_registration` holds the strict cursor and
  sets `_heartbeat_registration_observed`; it is called from
  `_has_pending_observation` after plan 4's
  `_has_pending_drain_stop_observation`, and the queue is appended in
  `_activity_wait_queues` after plan 4's `_drain_stop_target_queues()`; plan
  4's entries are unchanged. The handle is the `BaseTask._queue` cache entry
  (closed by task cleanup), the same handle the desire predicate deletes
  through and M1 patches. A held flag never makes the observation report a
  change; only a newer row does, so a draining Manager does not spin.
- Mechanical consequence, recorded: 13 existing tests in
  `tests/core/test_manager.py` arranged a heartbeat service through
  `_task_monitor_enabled` alone (supervision, reconcile, singleton, pending
  spawn, PONG, stale-log, convergence-reason tests). Each now writes one
  unexpired row first through the new test helper
  `_write_heartbeat_registration`; their assertions are unchanged.
  `tests/commands/test_status.py::test_status_services_prefer_live_duplicate_over_terminal_duplicate`
  likewise writes one row, and so does
  `tests/cli/test_cli_serve.py::test_serve_restarts_singleton_services_without_duplicates`
  (it kills and expects a restart of the heartbeat singleton; found by the
  full fast suite). The liveness-only status test's evidence
  assertion changes from `config-disabled` to `no-registrations` (the plan
  said its assertions hold; this one is the [MF-3.2] evidence change).
- Verify: `./.venv/bin/python -m pytest tests/core/test_manager.py
  tests/commands/test_status.py -q` → exit 0; `bin/pytest-pg
  "tests/core/test_manager.py::test_manager_registration_row_wakes_service_convergence"`
  → `2 passed`.
- Plan 4 coupling re-run: `./.venv/bin/python -m pytest tests/core/test_manager.py
  -q -k "drain_stop or stop_keeps or stop_releases or stop_skips or stuck_launch
  or orders_user_work"` → exit 0, no failure; `./.venv/bin/python -m pytest
  tests/cli/test_cli_manager.py -q` → exit 0. No plan 4 test needed a
  registration row.

Task 4. Every Replacement block in `## Proposed Spec Delta` applied
verbatim; the four non-block backlinks and the [QUEUE.7] mapping addition
added; `TaskMonitor._arm_next_cycle_deadline` added to the [MF-5] mapping
before `TaskMonitor._handle_builtin_cycle_worker_result`. Backlinks were
inserted at the top of each `## Related Plans` list, as the series' earlier
plans did. CHANGELOG `[Unreleased]` / `### Changed` carries the two bullets.
Verify: spec hygiene and plan metadata → pass; `bin/check-doc-paths` →
`check-doc-paths: OK`; backlink grep → one line in each of the five files;
05 mapping grep → 2 lines (588, 655); 07 mapping grep → 1 line; retired-phrase
grep → no output.

Task 5 (after the landing commit `4e082fd8`).

Idle-turn table, R1 (`test_task_monitor_quiet_interval_runs_zero_turns_with_heartbeat_emitting`,
2.5 s quiet window), before = the Task 2 RED run, after = the run at
`4e082fd8`:

| Backend | Case | Before (Task 2 RED) | After |
|---------|------|---------------------|-------|
| SQLite | unavailable | 2 | 0 (PASSED) |
| SQLite | emitting | 2 | 0 (PASSED) |
| PostgreSQL | unavailable | 3 | 0 (PASSED) |
| PostgreSQL | emitting | 2 | 0 (PASSED) |

Manager desire-predicate cost: scratch probe (not a repository file) with a
delegating counter on `Manager._heartbeat_registrations_present` (counts the
call and the rows its peek yields, then runs the real method); canonical
Manager with both monitors disabled, driven by `run_until_stopped` on a
thread, 5 s settle, then a 30 s quiet window; SQLite.

| Registrations | Predicate calls in 30 s | Rows scanned |
|---------------|-------------------------|--------------|
| 0 | 117 | 0 |
| 1 | 114 | 114 |
| 10 | 120 | 120 |

The early return at the first unexpired row keeps each call at one row
with live registrations. Correction (cross-family completed-work review,
round 1): about 4 calls per second is a predicate call rate, not the idle
Manager's turn rate. One wake can reach the predicate several times
(`run_until_stopped` → turn → `_run_managed_service_convergence` →
`_managed_service_convergence_active_reasons` →
`_pending_service_probes_for_scope` → `_heartbeat_registrations_present`,
and independently `Manager.next_wait_timeout`); the idle wakes themselves
come from the existing one-second leadership and family-repair deadlines
and the five-second stable-service audit. No new poll interval exists. The
cost is one broker read per call where the old gate read a boolean; an exact
attribution would need turns and timeout causes counted separately. (A 10 s
caller count at zero registrations attributed 57 of 61 calls to
`_pending_service_probes_for_scope` and 4 to `_reconcile_managed_services`.)
Recorded as the measured cost; no cache or registry is added ([ALT-WT-010]).

Theory edit (F4): [ALT-WT-004] Kind, Current consequence, and Evidence
applied verbatim as Task 5 words them, accepted by the owner on 2026-11-03
("I accept the theory edits"), committed alone as the "Theory:" commit `e92dac94`.

[WT-7] completion items: the keyed possession probe needs an owner-written
key and a different-family answerer; it is not run here and stays with the
owner (so the plan is `promoted`, not `completed`). Audit-table refresh, by
reference to `docs/plans/2026-11-02-program-theory-reconciliation-plan.md`
`### Audit table` (not edited):

- Row 4.3-a: the TaskMonitor heartbeat-clock evidence
  (`TaskMonitor._ensure_heartbeat_registered`,
  `test_task_monitor_heartbeat_failure_records_health_but_still_cycles`)
  leaves the drift list; both are deleted, and R1 pins zero quiet turns on
  both backends. The row's other sites belong to plan 2.
- Row I-2 ([WT-2a.2]): strengthened, still aligned: heartbeat registrations
  are queue rows and the heartbeat's table and the Manager's desire are
  rebuilt from them.
- Row I-13 ([WT-2a.13]): still aligned: a registration ends by cancel or by
  its own declared lease; no reader consults registrant liveness.
- Rows 4.1-c, 4.3-f, 4.4-c, [ALT-WT-007] name `weft/core/manager.py` or
  `task_monitor.py` for evidence this plan did not touch: unchanged, carried
  forward by reference.

Independent completed-work review (one scoped cross-family round): not run
by the implementer (the dispatch forbids delegation); left to the series
lead.

Final gates at the bookkeeping commit: recorded in the implementer's report.

Review round 1 fixes (2026-11-03).

- F1: `weft/core/tasks/heartbeat.py::HeartbeatTask._peek_registration_rows`
  and `weft/core/heartbeat.py::_delete_rows_for_id` now catch `ValueError`
  (which `json.JSONDecodeError` subclasses) from `json.loads`, so a row
  whose integer exceeds Python's 4,300-digit conversion limit takes the
  malformed-row path: rejected with `heartbeat_request_invalid` and deleted
  by exact ID at the build, skipped by the helpers' id scan. T6
  (`test_heartbeat_invalid_registration_row_is_deleted_and_reported`) gains
  a row with a 5,000-digit `lease_seconds` and an unrelated upsert and
  cancel written while it is present; it expects six invalid events.
  Mutation caught: catching only `JSONDecodeError`. RED, before the fix: the
  unrelated `upsert_heartbeat` raised `ValueError: Exceeds the limit (4300
  digits) for integer string conversion: value has 5000 digits` in
  `_delete_rows_for_id`; with only the helper fixed, `process_once()` raised
  the same `ValueError` from `_peek_registration_rows`. GREEN after both.
  The Manager predicate and `weft status` already classify that row as
  invalid (`model_validate_json` raises `ValidationError`, checked).
- F2: H1 and H3 assert the `None` returns (through a `Callable[..., object]`
  alias, since mypy rejects a direct check of a `-> None` call). T8 and T9:
  the plan's "pause ack" predates plan 3, which removed PAUSE
  acknowledgement rows; both now assert the `control_pause` lifecycle event
  (plan 3's observable outcome) and keep the paused-state check. T10 asserts
  the expiry event's `message_id` is B's in its expired and failed-delete
  cases.
- The cost-reduction early return in `_pending_service_probes_for_scope` was
  not applied (withdrawn by the coordinator pending an owner redirect of how
  the Manager reads `weft.state.heartbeats`).

Owner redirect: services are always on (2026-11-03; statements 1–8 in
`## 9. Owner forks`; code commit `c9369ee6`).

- Evidence for statement 8 ("trying to make services stop and start ended
  up creating too much state"): the on-demand design had accumulated, for
  one heartbeat, the Manager desire predicate
  (`Manager._heartbeat_registrations_present`) with its per-call broker
  reads at three gate sites and in `next_wait_timeout` (about 4 calls a
  second on an idle Manager); the Manager's all-expired delete role with
  its ascending-order barrier; the Manager's strict cursor
  (`_heartbeat_registrations_cursor`), observed flag
  (`_heartbeat_registration_observed`) and notification interest; the
  heartbeat's idle exit with its timeout plumbing
  (`heartbeat_idle_timeout` metadata, `HEARTBEAT_IDLE_TIMEOUT_SECONDS`,
  `_empty_since_monotonic`, `_maybe_idle_shutdown`, `_next_idle_timeout`);
  and the status table read with its `no-registrations` evidence. All of it
  is removed.
- Intermediate fork, recorded: the first redirect asked for a
  construction-fixed PEEK lane on `weft.state.heartbeats` whose handler
  ignores the body. `MultiQueueWatcher._fetch_next_message` returns
  `peek_one()` (the oldest row) for PEEK lanes, with no cursor, and
  `_queue_has_pending` uses `has_pending()` with no `after_timestamp`, so
  such a lane re-delivers the oldest row every pass and never delivers a
  newer upsert as itself. The owner resolved the fork with statement 7.
- Code (`weft/core/manager.py`): `_reconcile_managed_services` appends the
  heartbeat spec unconditionally; `_pending_service_probes_for_scope` always
  includes the heartbeat key; `_managed_service_convergence_active_reasons`
  counts a missing heartbeat as `missing_active_tid` unconditionally;
  removed `_heartbeat_registrations_present`,
  `_has_pending_heartbeat_registration`, both fields, the
  `_activity_wait_queues` and `_has_pending_observation` entries, the turn's
  forced convergence, and the imports. Net Manager diff against `ecf7ad42`:
  the three gates no longer read `_task_monitor_enabled` for the heartbeat,
  and the spawn payload has no `heartbeat_idle_timeout`.
  `weft/core/tasks/heartbeat.py`: idle exit and its plumbing removed; the
  1.0 s ownership-audit deadline removed (`_next_ownership_audit_at`;
  `_exit_if_superseded` still runs on every turn and the emission path
  still checks ownership before each emit); `next_wait_timeout` returns 0.0
  while a rebuild is pending, else the next due emission and the earliest
  lease expiry, else `None`. `weft/commands/system.py::_service_enabled`
  reports the heartbeat enabled and reads no queue (S1 decision: the field
  stays and reports the heartbeat as an enabled internal service like the
  others; `no-registrations` is removed; no registration facts are shown).
  `weft/_constants.py`: `HEARTBEAT_IDLE_TIMEOUT_SECONDS` and
  `HEARTBEAT_OWNERSHIP_AUDIT_INTERVAL_SECONDS` deleted.
- RED (before the code change):
  - (a) `test_manager_enqueues_heartbeat_through_service_path`, now with an
    empty table and both monitors disabled: no heartbeat payload.
  - (b) `test_heartbeat_with_empty_table_runs_zero_timer_turns_and_wakes_on_upsert`:
    "heartbeat ran 29 turns over 30.0s with an empty table";
    `test_heartbeat_empty_table_publishes_no_timer`: `1.0 is None` (the
    audit deadline); `test_heartbeat_empty_table_never_idle_exits` (T3
    rewritten): `should_stop` was `True`.
  - (c) `test_manager_never_reads_or_deletes_heartbeat_registrations`
    (marked slow, run explicitly): reads 531, 551, 557 for 0, 1, 10 rows;
    deletes 0.
  - M1 (`test_canonical_manager_always_desires_heartbeat`, 3 cases), the
    convergence test (`test_missing_heartbeat_keeps_convergence_active`),
    S1 (`test_status_reports_heartbeat_as_standing_internal_service`, 3
    cases), T8 and T9 (no idle exit) all failed on the old desire or idle
    rule.
- GREEN: all of the above pass; (c) reads 0 and deletes 0 for 0, 1 and 10
  rows over 30 s; (b) zero turns over 30 s with an empty table, then wake
  on upsert and one emission. The window test settles past a one-time
  post-start turn (startup lifecycle publication, about 1–3 s after start)
  before measuring.
- (d) Plan 4's drain selection and `tests/cli/test_cli_manager.py` green.
  The thirteen `test_manager.py` tests that wrote a registration row first
  (`test_manager_task_monitor_supervision_ignores_dispatch_ownership`,
  `test_managed_service_convergence_active_reasons_are_stable`,
  `test_reconcile_reuses_tracked_service_candidates`,
  `test_reconcile_clears_duplicate_scan_after_live_tracked_evidence`,
  `test_task_monitor_stale_log_without_liveness_does_not_block_restart`,
  `test_task_monitor_recent_log_without_liveness_blocks_duplicate_restart`,
  `test_task_monitor_internal_pending_spawn_request_blocks_duplicate_restart`,
  `test_task_monitor_public_pending_spawn_request_does_not_block_internal_restart`,
  `test_task_monitor_spoofed_pending_spawn_without_internal_envelope_does_not_block`,
  `test_task_monitor_spoofed_public_metadata_does_not_claim_singleton`,
  `test_task_monitor_latest_terminal_log_overrides_older_running_evidence`,
  `test_task_monitor_matching_pong_blocks_duplicate_restart`,
  `test_manager_service_convergence_advances_without_dispatch_ownership`):
  the row existed only to satisfy desire in each; the arrangement and the
  `_write_heartbeat_registration` helper are removed, assertions unchanged,
  all green. Likewise the row in
  `test_status_services_prefer_live_duplicate_over_terminal_duplicate` and
  `test_serve_restarts_singleton_services_without_duplicates` is removed.
- Tests whose premise was a service-free canonical Manager (both monitors
  disabled meant no internal service; several also pass a nonexistent
  `WEFT_HEARTBEAT_ENABLED` key): `test_manager_enqueues_liveness_monitor_when_independently_enabled`
  and `test_manager_does_not_enqueue_task_monitor_when_disabled` now expect
  the heartbeat payload (the former's pre-`da5fa0f6` assertion);
  `_discovery_manager` and
  `test_manager_public_dispatch_steals_work_when_registry_ownership_is_unproved`
  record only non-heartbeat launches;
  `test_manager_admission_retains_public_while_internal_can_launch`
  allows only the heartbeat's own spawn row to remain queued;
  `test_process_once_reconciles_internal_services_before_user_spawn_work`
  drains the startup heartbeat enqueue;
  `test_manager_idle_pending_work_excludes_retained_reserved_custody` uses a
  scoped Manager; the fixtures `recovery_manager`
  (`tests/core/test_admission_recovery.py`), `validating_manager`
  (`tests/core/test_child_spawn_validation.py`), `manager` and
  `startup_pg_manager` (`tests/core/test_manager_family_maintenance.py`), and
  `test_source_to_manager_reservation_handoff_uses_real_reserve_owner`
  (`tests/commands/test_submission_observation.py`) patch
  `Manager._service_supervision_allowed` to `False`, which is the
  "without services" premise their docstrings state. The plan 6
  TaskMonitor tests (R1, R2, C1–C4, G1) are untouched. The heartbeat tests
  that pinned the 1.0 s audit deadline with an empty table
  (`test_heartbeat_run_until_stopped_uses_next_wait_timeout`,
  `test_heartbeat_pending_input_wakes_through_reactor_wait`,
  `test_heartbeat_ping_while_waiting_is_handled_promptly`) now assert no
  deadline and a wake from the input itself.
- Found and not changed (owner decision): an idle heartbeat's wait still
  reads its own table. SimpleBroker's polling strategy returns from its
  native wait on its idle-poll schedule (about every 0.1 s), and each
  return calls `_has_pending_observation`, which reads
  `weft.state.heartbeats` (`has_pending`, plus a row-ID peek with rows).
  Measured over 30 s, scratch probe: 230 reads with 0 rows, 458 with 1
  row; zero reactor turns. These are the heartbeat reading its own queue,
  but on SimpleBroker's idle-poll cadence, not on a change.
- Spec deltas applied in `c9369ee6`: [MA-1] item 7; [MF-3.2] flow line, the
  manager bullet, and the idle-exit bullet; [QUEUE.7]; the Quick Reference
  `weft.state.heartbeats` row; the backlink sentence in 00, 01, 03, 05, 07;
  CHANGELOG. README's admission-reserve wording ("Manager, TaskMonitor,
  LivenessMonitor, and Heartbeat") already matches and is unchanged. The
  plan's Replacement blocks carry the landed text.

Proposed theory text for the owner (not applied; the series lead commits
it after acceptance).

Proposed replacement for the `docs/program-theory.md` [ALT-WT-004]
"Current consequence" bullet:

```text
- Current consequence: TaskMonitor's only clock is its cycle deadline
  through `next_wait_timeout()`; it registers nothing. The heartbeat service
  is a standing internal service of the canonical Manager ([MA-1] item 7)
  that idles with no timer when nothing is due; it is the only reader and
  deleter of `weft.state.heartbeats`, and the Manager neither reads nor
  deletes that queue (this supersedes the `da5fa0f6` gate).
```

Proposed new entry for `docs/program-theory.md`, after [ALT-WT-021]:

```text
### [ALT-WT-022] Internal services started on demand and stopped when idle

- Kind: tried and discarded (built for the heartbeat by `2026-11-02-task-monitor-heartbeat-clock-plan.md`, then removed by the owner's redirect of 2026-11-03)
- Disposition: rejected
- Candidate: internal services started on demand and stopped when idle.
- Why plausible: No process runs while nothing needs it.
- Reason: Owner, 2026-11-03: "trying to make services stop and start ended
  up creating too much state. The simpler, more robust design is always on
  services, with care taken that the service idles when not needed." If what
  is wanted is a ServiceTask, there will be a process.
- Current consequence: internal services are always on under the canonical
  Manager and idle when not needed: no timer and no poll with nothing to do,
  blocking on the watcher.
- Reconsider when: Never for an internal service of the canonical Manager; a
  consumer that wants no process does not want a ServiceTask.
- Evidence: owner decision 2026-11-03; `docs/plans/2026-11-02-task-monitor-heartbeat-clock-plan.md`.
```

Scoped round 2 (2026-11-03).

- F1 accepted, not implemented: the fix belongs at the watcher boundary
  (keep the register-before-read observation check in
  `_wait_for_activity_body`; on a healthy, hint-free, pre-deadline strategy
  return in `_activity_hint_action`, skip the observation and readiness
  scans; keep the transient and degraded fallback paths). It changes the
  [CC-2.1] watcher contract; the owner decided on 2026-11-03 ("Own plan.
  Make sure it stays consistent with the program theory") that it lands as
  its own plan, `2026-11-03-watcher-hint-gated-observation-plan.md`, after
  series plans 11, 12 and 8. Until it lands
  `test_heartbeat_with_empty_table_runs_zero_timer_turns_and_wakes_on_upsert`
  proves zero reactor turns, not zero reads: the scratch probe still counts
  230 reads of `weft.state.heartbeats` over 30 s with an empty table. The
  eventual fix must add a read count at the broker boundary to that test,
  counted as `test_manager_never_reads_or_deletes_heartbeat_registrations`
  counts (the broker class found from a live connection's MRO), asserting
  zero reads over the quiet window.
- F2 fixed: [MF-3.2]'s reactor bullet (and its Replacement block here) and
  [CC-2.3]'s `HeartbeatTask` bullet now name only the due-emission and
  lease-expiry deadlines, with no deadline on an empty table; the
  `weft/core/heartbeat.py` module docstring and the
  `HeartbeatRegistrationRow` docstring no longer name a Manager predicate or
  a status reader.
- 2026-11-04, after closure (owner ruling on plan 13, "If those underlying
  reads fail, do nothing is the correct response"): the `failed-delete`
  case of `test_heartbeat_build_deletes_residue_and_an_expired_newest_row`
  (round 1 F1's residual, Task 7 item 4 a) and the "failure barrier"
  sentences in the `_rebuild_registrations` docstring were removed as
  over-armoring ([WT-4] 4.4): a registration-row delete raising occurs only
  on a database that is no longer readable. The code is unchanged: older
  rows are still deleted first in ascending order, which guards a build
  interrupted by process death (an OS boundary, kept), and a build that
  cannot read the queue still does nothing with the flag cleared.

- Scoped round 2 F1 fix (2026-11-04): plan 13,
  `docs/plans/2026-11-03-watcher-hint-gated-observation-plan.md`, landing
  commit `956acab1` ("Gate the reactor wait's observation read on
  evidence"): the resident heartbeat on persistent SQLite now makes 0 reads
  of `weft.state.heartbeats` over a quiet window (counted at the broker
  boundary by `tests/tasks/test_heartbeat.py::test_resident_heartbeat_reads_no_registrations_while_quiet`;
  30 s measurement 0 / 0 / 0 with 0 / 1 / 10 rows against 248 / 500 / 496
  before). On PostgreSQL the native idle recheck keeps its read (plan 13
  Owner Decisions item 1).

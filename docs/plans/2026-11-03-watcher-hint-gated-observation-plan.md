# Reactor Wait: Observation Checks Only on Evidence

Status: draft
Source specs: docs/specifications/01-Core_Components.md [CC-2.1] (one paragraph and the implementation mapping extended), [CC-2.5] (constraint, unchanged); docs/specifications/07-System_Invariants.md [QUEUE.7], [QUEUE.8], [IMPL.10], [IMPL.11] (constraints, unchanged); docs/specifications/04-SimpleBroker_Integration.md [SB-0.4] (constraint, unchanged); docs/specifications/03-Manager_Architecture.md [MA-1.8], [MA-3] (constraints, unchanged); docs/specifications/05-Message_Flow_and_State.md [MF-3.2] (constraint, unchanged)
Superseded by: none

Class: 5, hardening applies. Two triggers put it there. First, materiality
requires a spec change: [CC-2.1] describes the wait's hint handling and says
nothing about when notification-only observation checks read the broker; the
new rule (read only on a return that can carry evidence) is a behavior of the
shared wait that future work must plan against, so it is written into
[CC-2.1] (`## Proposed Spec Delta`). Second, a risky trigger in
`docs/agent-context/runbooks/writing-plans.md` "When Hardening Is Mandatory"
fires: the change is an execution-path change in the one wait every task
reactor shares (`MultiQueueWatcher._wait_for_activity_body` and
`_activity_hint_action`), so "the same core behavior must run through more
than one context" holds (the Manager, the heartbeat service, TaskMonitor,
LivenessMonitor, every Consumer, and standalone watchers), and the Manager's
use of it sits on the durable spine (startup feedback under [MA-1.8], the
system-STOP drain under [MA-3]). So the hardening checklist applies and
cross-family review precedes implementation. Plan type: implementation with
spec revision. Promotion strategy: **B (atomic)**: the [CC-2.1] text, the code
and the tests land in one commit.

Series position: a follow-up to the 2026-11 cleanup series, ordered by the
owner on 2026-11-03 as its own plan after the cross-family scoped round 2 of
plan 6 (`docs/plans/2026-11-02-task-monitor-heartbeat-clock-plan.md`,
`## Review Log`, "Scoped round 2" entry) blocked on F1. It lands after series
plans 11 (`docs/plans/2026-11-02-cli-client-surface-parity-plan.md`), 12
(`docs/plans/2026-11-02-tid-display-encoding-plan.md`) and 8
(`docs/plans/2026-11-02-python-craft-late-imports-plan.md`). It depends on
plan 6's always-on heartbeat (landed in `c9369ee6`) for its measurement.

## Theory

This section comes first because the owner's instruction for this fix is
"Make sure it stays consistent with the program theory" (2026-11-03).

### Statements the change serves

- [WT-2] item 4 (`docs/program-theory.md` lines 202–210), "Each task is a
  single-arbiter event reactor": one arbiter waits on a closed set of inputs,
  and "If no input arrives and no timer is due, a quiet task runs zero turns."
  Today a quiet task runs zero turns but still reads the broker after every
  strategy return when it holds a notification interest. After this plan a
  quiet task on persistent SQLite also performs zero observation reads: the
  read happens only when the arbiter reports an input (a local, native or
  data-version hint) or a deadline.
- [WT-4] 4.3 "One reactor, one arbiter, a closed set of inputs" (line 450)
  and "A quiet task does nothing" (line 451). The gate adds no input and no
  wake; it removes a read that answered no input.
- [ALT-WT-002] "Fixed polling caps as a fallback" (lines 543–558). The
  unconditional observation call in `_activity_hint_action` is not a cap, but
  it has the same shape the entry refuses: it makes Weft do broker work on a
  fixed cadence (the strategy's polling pass, about 8.7 returns a second on
  SQLite, plan 2's measurement) whether or not anything happened. The entry's
  current consequence ("the strategy, not Weft, owns the pass length") is kept:
  this plan does not touch the strategy's pass, burst or backoff; it stops
  Weft attaching a broker read to every pass.
- Draft [ALT-WT-022] "Internal services started on demand and stopped when
  idle", proposed in plan 6 (`docs/plans/2026-11-02-task-monitor-heartbeat-clock-plan.md`
  lines 3642–3657, not yet applied to `docs/program-theory.md`). Its current
  consequence reads "internal services are always on under the canonical
  Manager and idle when not needed: no timer and no poll with nothing to do,
  blocking on the watcher." The resident heartbeat today has no timer with an
  empty table but reads `weft.state.heartbeats` 230 times in 30 s (plan 6
  execution log, line 3614); this plan brings that to zero on SQLite. The
  owner's rule in `docs/lessons.md` (2026-11-03 entry, lines 903–919) adds
  "prove the idle with a counted quiet-window test, not prose": Task 1's
  tests are that proof.
- [WT-7] "Event-driven against absence" (line 1003): each recurring check must
  answer "which event cannot arrive, and why?" The unconditional per-return
    read has no answer on persistent SQLite: every foreign commit arrives as a
  data-version hint. The detector and its callback fail only on a database
  that is no longer readable, on which the observation read would fail too,
  so their failure is not a quiet state the read could serve (`## Owner
  Decisions` item 2). On PostgreSQL the native-waiter return
  does have an answer (an exact delete sends no notification, and a
  notification is not durable for a broken listener connection), which is why
  this plan keeps it there (`## Owner Decisions`, item 1).
- [WT-4] 4.4 "No capability diminution" (line 467) and "Measure; do not bake
  in inefficiency" (line 465): the difference register below, the
  measurements on both backends in Tasks 0 and 6.

### Statements the change must not violate

- [ALT-WT-010] "A new scheduler, event bus, index, or cache subsystem"
  (lines 696–709): "A cache that becomes startup truth is a second lifecycle
  database." The gate stores no evidence. It decides only whether to read; when
  it opens, the overrider reads the queue exactly as today. The plan adds no
  field, flag or cache: the gate is computed from the hints SimpleBroker
  already exposes and from the owner's deadline.
- [QUEUE.7] (`docs/specifications/07-System_Invariants.md`, section at line 163; interest sentences at lines 179–195):
  notification interests stay reactor-owned and never become delivery routes;
  "Registration precedes the initial read"; "All observation reads remain on
  the reactor". Nothing here changes which queues are interests, who reads
  them, or on which thread; the gate is inside the reactor's own wait.
- Register before read (the comment at
  `weft/core/tasks/multiqueue_watcher.py` lines 1147–1148 and the [QUEUE.7]
  sentence above): the wait-entry check at line 1149 stays, after
  `_ensure_wait_strategy_started()` at line 1145 installs or re-registers the
  waiter. A publication committed before the wait is still found before the
  first strategy call (Task 2 guard G1).
- [WT-2a.6] "One arbiter per reactor" (lines 294–307): no second wait, no
  thread, no timer; the strategy remains the only thing that blocks.
- [WT-2a.8] "SimpleBroker owns queue mechanics" (lines 315–319): the gate
  consumes SimpleBroker's existing hints (`consume_local_activity_hint`,
  `consume_native_activity_hint`, the data-version change callback) and
  reimplements none of them.
- [WT-2a.2] "Queues are the only durable truth": the queue is still read for
  every decision the overriders make.

### Theory check of the declined remedies

The round-1 remedies (`## Owner Decisions` item 2) were not only unneeded;
they were against the theory, and this plan's 2026-11-03 revision was wrong
to present them as consistent with it:

- [WT-4] 4.4 "No over-armoring" (line 464): "Engineer for real execution
  and failure boundaries, not impossible or adversary-invented states.
  Best-effort paths try, catch, and log diagnostics." The retry flag, the
  detector-uncertainty flag and the flag-first callback engineered for a
  read failure that does not occur on a readable database, and the
  overriders' existing try-catch-log is the whole prescribed response.
- [WT-2a.8] "SimpleBroker owns queue mechanics" (lines 315–319): "a
  queue-level need is met in SimpleBroker, never reimplemented in Weft."
  A Weft `PollingStrategy` subclass wrapping SimpleBroker's data-version
  provider to judge the detector's health is Weft reimplementing a
  queue-level mechanism, whatever seam it used.
- [ALT-WT-010] (lines 696–709): "In-memory state stays a hint." Two new
  fields that outlive a turn to record that a read or a detector check
  failed are state kept to steer later turns, the shape plan 6's negative
  knowledge refuses ("trying to make services stop and start ended up
  creating too much state", `docs/lessons.md` 2026-11-03).

The 2026-11-04 revision removes all of it; what remains (the gate alone) is
a pure application of the statements above.

### Theory amendment

None is needed. Every statement above already asks for this behavior; the
code lagged it. One tension is recorded for the owner rather than resolved by
editing theory text: on PostgreSQL the resident heartbeat will still read its
table on each native-waiter return (SimpleBroker's "slow safety recheck",
[CC-2.1] line 196), about once every 1 to 2 s. Whether that counts as "a poll
with nothing to do" under the draft [ALT-WT-022] is `## Owner Decisions`
item 1. Either answer leaves the theory text as drafted: if the owner keeps
the recheck (the recommendation), the recheck has a job (recovering
evidence the notification channel cannot carry); if the owner removes it,
that is a later plan's code change, not a theory edit.

## Goal

`MultiQueueWatcher._activity_hint_action`
(`weft/core/tasks/multiqueue_watcher.py` lines 1189–1237) calls
`self._has_pending_observation()` at line 1201, first and unconditionally, on
every strategy return, before it looks at the hints it has just consumed. On
SQLite the polling strategy returns about every 0.1 s at idle, so every task
whose `_has_pending_observation` reads the broker pays one read (or two) per
return whether or not anything changed: the Manager while it holds startup
feedback ([MA-1.8]) or system-STOP drain targets ([MA-3]), and, permanently
since plan 6, the always-on heartbeat service reading `weft.state.heartbeats`.
This plan moves the observation check behind the same evidence gate the
readiness scan already uses on persistent SQLite: the check runs on a return
that carries a hint, on a deadline return, on every native-waiter return, and
on every return of the degraded and nonpersistent fallbacks, and it is skipped
on a healthy, hint-free, pre-deadline persistent SQLite return. The wait-entry
check stays. The plan carries no failure-path machinery: on a reasonable
SQLite neither `PRAGMA data_version`, nor the change callback's
`refresh_last_ts()`, nor an observation read fails while the database stays
readable, and when such a read does fail the correct response is to do
nothing (owner ruling, 2026-11-04, `## Owner Decisions` item 2). A hint-free
return is therefore trusted as "nothing changed".

## Source Documents

Source specs:
- `docs/specifications/01-Core_Components.md` [CC-2.1] (lines 172–209: the
  drive owner, the strategy's ownership of `data_version`, burst, native
  waiting and "its slow safety recheck", and hints as readiness hints only),
  [CC-2.5] (lines 885–923; BaseTask alone evaluates `next_wait_timeout()` and
  queue/local activity waiting)
- `docs/specifications/07-System_Invariants.md` [QUEUE.7] (lines 163–199),
  [QUEUE.8] (line 201 onward), [IMPL.10] (lines 865–911), [IMPL.11] (line 914;
  TaskMonitor maintenance has no reactor of its own, unchanged)
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4] (lines
  150–200 and the implementation note at line 340). Note: [SB-0.4] is in spec
  04, not spec 07. It contains no sentence about when observation checks run,
  so it needs no delta.
- `docs/specifications/03-Manager_Architecture.md` [MA-1.8] (startup feedback;
  the implementation mapping at line 585 already names
  `MultiQueueWatcher._activity_hint_action` as the owner of "observation wake
  filtering"; the absence recheck after `MANAGER_ADMISSION_RECHECK_SECONDS` at
  line 478), [MA-3] (system-STOP drain targets; mapping at lines 958–975)
- `docs/specifications/05-Message_Flow_and_State.md` [MF-3.2] (the heartbeat
  service, its registrations table and its pre-emission rebuild)

Theory and guidance:
- `docs/program-theory.md` [WT-0], [WT-2], [WT-2a], [WT-4], [WT-5], [WT-7],
  [ALT-WT-002], [ALT-WT-009], [ALT-WT-010], [ALT-WT-021]; draft [ALT-WT-022]
  in plan 6 (lines 3642–3657)
- `docs/agent-context/decision-hierarchy.md` [DOM-15]
- `docs/agent-context/runbooks/writing-plans.md`, `hardening-plans.md`,
  `review-loops-and-agent-bootstrap.md`
- `docs/lessons.md`, 2026-11-03 entry "Internal services are always on, and
  idle when not needed" (lines 903–919)
- `CLAUDE.md` §1.1, §4.2 (imports), §4.5 (docstrings and `Spec:` lines), §5.1

Plans (status):
- `docs/plans/2026-11-02-reactor-edge-timer-floors-plan.md` (plan 2,
  `completed`): the last change to this wait path. Its Task 0 item 4
  measurement (execution log lines 1544–1645) is the method this plan copies:
  on SQLite the strategy returns 262 times in 30 s with no pending-message
  probe; on PostgreSQL about once per 1.5 s, each followed by one
  `_has_pending_messages()` probe. Its owner decision 3 (lines 1157–1162) left
  the PostgreSQL classification "measured only".
- `docs/plans/2026-11-02-task-monitor-heartbeat-clock-plan.md` (plan 6,
  `promoted`): owner statements 1–8 of 2026-11-03 (lines 2866–2900), the
  scoped round 2 F1 entry (line 2918) and its disposition (lines 3660–3677),
  and the measurement (lines 3609–3616). That disposition asks the fix to "add
  a read count at the broker boundary ... counted as
  `test_manager_never_reads_or_deletes_heartbeat_registrations` counts";
  Task 1 does.
- `docs/plans/2026-11-02-system-stop-ordering-plan.md` (plan 4, `promoted`):
  the drain-target interests this plan must keep working, and the series'
  plan format.
- Cross-family finding text: plan 6's round-2 findings file F1 and its
  "Watcher trace" observation (a file-free probe: all hints false, no
  deadline, `continue` after one observation call).

## Context and Key Files

### Current structure (verified at `6054aea1` and re-verified at `ee69f974`: no cited code, test or spec file changed between them; `weft/core/tasks/multiqueue_watcher.py` last changed in `4a8884cb`)

The wait, `weft/core/tasks/multiqueue_watcher.py`:

- `MultiQueueWatcher._wait_for_activity_body` (line 1133). It returns at once
  for a set stop event or a non-positive timeout (1142–1143), calls
  `_ensure_wait_strategy_started()` (1145; it starts the strategy at
  1180–1181 and installs or re-registers the multi-queue waiter through
  `_ensure_multi_activity_waiter` at 917), then performs the **wait-entry
  observation check** (comment 1147–1148, call 1149–1150). The loop
  (1153–1175) returns when the deadline has passed (1154–1156), calls
  `self._strategy.wait_for_activity(timeout=remaining)` (1159), handles a
  waiter failure by marking `_native_activity_degraded = True` and dropping
  the waiter (1160–1169), and otherwise asks `_activity_hint_action` (1174).
- `MultiQueueWatcher._activity_hint_action` (1189–1237). It consumes the
  local hint (1196), the native hint (1197) and the data-version flag
  (1198–1199), then **calls `_has_pending_observation()` unconditionally**
  (1201–1202). Then: a local hint scans readiness and returns (1204–1207); a
  native or data-version hint scans readiness and returns or continues
  (1209–1213); a native-waiter return before the deadline scans readiness
  (1215–1220); a deadline return returns without a scan (1221–1222); the
  degraded and nonpersistent fallbacks scan readiness on every return
  (comment 1223–1228, code 1229–1236); everything else continues (1237). So
  on a healthy persistent SQLite return with no hint the readiness scan is
  already skipped (plan 2's state); only the observation call is not.
- `MultiQueueWatcher._has_pending_observation` (881–883): the base returns
  `False`. `_activity_wait_queues` (876–879) returns the delivery queues that
  count as wait activity. `_has_pending_messages` (1299–1309) is the
  readiness scan; it raises `BrokerError` on failure (1311–1320), so readiness
  failures are not silent. `_mark_pending_messages_prechecked` (897–900).
- `MultiQueueWatcher._on_data_version_change` (977–981) calls the inherited
  `super()._on_data_version_change(queue)` (that is,
  `queue.refresh_last_ts()`, `simplebroker/watcher.py` 496–498 and
  `simplebroker/sbqueue.py` 382–388, a broker read that can fail) **before**
  setting `_data_version_activity_pending = True`. If the refresh raises,
  the flag is never set and SimpleBroker swallows the error after its
  baseline has already advanced, so that change produces no hint.
  `_start_strategy` (969–975). Flags are initialised at 231–233.

The callers, `weft/core/tasks/base.py`:

- `BaseTask._drive_reactor_until` (1214–1256): each iteration runs
  `process_once()` then `wait_for_activity(timeout=self._next_reactor_wait_timeout(poll_interval))`
  (1250–1252). So every turn is followed by a wait whose entry check reads
  the observation once.
- `BaseTask.wait_for_activity` (1508–1552) adds the process-title deadline
  and calls `_wait_for_reactor_activity` (1554–1564), which calls
  `_wait_for_activity_body`. `BaseTask.next_wait_timeout` (1358–1372).

The installed SimpleBroker strategy,
`.venv/lib/python3.14/site-packages/simplebroker/watcher.py` (SimpleBroker
8.7.2, simplebroker_pg 4.7.2):

- `PollingStrategy.wait_for_activity` (1390–1482). A pending local activity
  returns at once (1411–1413). With no native waiter, the PRAGMA
  data-version check runs first (1416–1423). With a native waiter (1429–1452),
  a notification returns with `_native_activity_pending = True` (1436–1438);
  otherwise the loop continues inside the strategy and returns only on stop,
  a shortened deadline (1443–1445), the **native idle return** when
  `_next_native_idle_poll_at` passes (1447–1449), or the deadline
  (1450–1451). Without a native waiter, the pass sleeps its delay in 20 ms
  chunks and re-checks the data version between chunks (1458–1473), then
  returns with no hint (1480–1482). The polling pass ignores the deadline
  (docstring at 1391–1395).
- The native idle interval is `max(1.0, self._max_interval * 10)` (1386),
  jittered uniformly between one and two intervals (1636–1645);
  `_schedule_next_native_idle_poll` is documented as "Stagger slow fallback
  polls for notification-backed waiters" (1647–1648).
- `PollingStrategy._check_data_version` (1653–1688): a `None` from the
  provider returns `False` (1662–1664) without counting a failure; the first
  observed version runs the change callback and returns `False` (1666–1671);
  a changed version **advances the baseline first** (1674) and then runs the
  callback and returns `True` (1675–1676); an exception from the provider
  counts toward `_MAX_PRAGMA_FAILURES` (10, line 102) and returns `False`,
  raising `RuntimeError` at the tenth (1679–1688); the counter is never reset
  on success. `_run_data_change_callback` (1690–1701) **swallows** a callback
  exception (1697–1701).
- SQLite's detector query: `simplebroker/_backends/sqlite/maintenance.py`
  `get_data_version` (141–149) suppresses every exception and returns `None`
  (144–149), reached through `simplebroker/_backends/sqlite/plugin.py` 474–475
  and `BrokerCore.get_data_version` (`simplebroker/db.py` 3960). So an SQLite
  PRAGMA failure reaches the strategy as `None`, an "unchanged" pass with no
  failure counted: neither `_MAX_PRAGMA_FAILURES` nor Weft's degraded fallback
  ever activates for it.
- `BaseWatcher.__init__` sets `self._config` (403) and `self._stop_event`
  (391) before creating the strategy with
  `polling_strategy or self._create_strategy()` (425);
  `BaseWatcher._create_strategy` (450–490) is a documented override point
  (its docstring's "Override Examples", 459–468) and builds
  `PollingStrategy(stop_event=..., initial_checks=..., max_interval=...,
  burst_sleep=..., jitter_factor=...)` from `INITIAL_CHECKS`,
  `MAX_INTERVAL`, `BURST_SLEEP` and `JITTER_FACTOR`.
- `BaseWatcher._start_strategy` (500–556): the data-version getter (512–532)
  reads `connection.get_data_version()` on the calling thread's connection;
  its comment says SQLite data-version values are connection-local (510–511).
  `_on_data_version_change` (496–498).
- Connections: `simplebroker/_broker_session.py` `_ProcessBrokerSession`
  (187) is "Backend session shared by persistent queues for one target in
  one process"; `get_connection` (205) "Return this thread's shared core"
  (211). So writes made on the drive thread through any persistent queue use
  the same connection as the data-version getter, and SQLite does not change
  `PRAGMA data_version` for a connection's own commits (SQLite documentation
  for `PRAGMA data_version`; [CC-2.1] lines 199–201 states the same for
  same-connection self-writes). Writes from any other thread or process use
  another connection and do change it.
- SQLite has no native waiter: `simplebroker/_backends/sqlite/plugin.py`
  `create_activity_waiter` returns `None` (501–512), and the SQLite plugin
  defines no `create_activity_waiter_for_queues`, so
  `simplebroker/sbqueue.py::create_activity_waiter_for_queues` returns `None`
  (2143–2150).
- PostgreSQL: `simplebroker_pg/runner.py` `_SharedActivityListener._run`
  (from line 273) listens on one channel; a notification for a queue with no
  registration is dropped (297); a listener failure stores `_error` (305)
  and wakes waiters, and `wait_any` (from line 412) raises it (428–429).
  `PostgresMultiQueueActivityWaiter` (from line 597) registers its queue set at
  construction. `simplebroker_pg/_sql.py`: `INSERT_MESSAGE` (196–207), the
  move statement (`notified` at 421–427) and `RENAME_QUEUE_MESSAGES_COUNT`
  (112–135) call `pg_notify`; `DELETE_MESSAGE_IDS_COUNT` (82–94) and the
  claim statement (371–395) do not. `get_data_version` returns `None` on
  PostgreSQL (`simplebroker_pg/plugin.py` 841–843).

### The defect, measured

Plan 6's probe (an in-process `HeartbeatTask` on SQLite, counting
`simplebroker.db.BrokerCore` read calls that name `weft.state.heartbeats`):
230 reads in 30 s with 0 rows, 458 with 1 row, zero reactor turns. This plan's
author re-ran an equivalent probe at `6054aea1` with a 5 s window (scratch
script, not in the repository; its source goes into the Task 0 log):
41 reads with 0 rows and 80 with 1 row today; with the Task 2 guard applied by
monkeypatch, 0 reads in both cases, and an upsert from another thread after
the window was ingested in 0.032 s (0 rows) and 0.014 s (1 row) against
0.017 s and 0.011 s today. These are single samples; Task 0 and Task 6
measure properly.

### Hint analysis: every way a notification publication can arrive

"Publication" means a commit that changes what an overrider's
`_has_pending_observation` would report: a new row on an interest queue, or
(for the heartbeat) the removal of a row its table holds.

| Backend and path | How the publication arrives | Hint on the next strategy return? | Design |
|---|---|---|---|
| Persistent SQLite, another thread or process commits, detector and callback working | The data-version counter changes on the drive thread's connection; the strategy sees it at its first check (watcher.py 1416–1423) or between 20 ms chunks (1466–1473) and runs the callback, which sets `_data_version_activity_pending` (multiqueue_watcher.py 977–981) | Yes, except when the commit lands in the final chunk of a pass: that return carries no hint, and the next strategy call's first check (1416–1423) returns at once with it | Observation runs on the hinted return: at most one extra strategy return, with no added sleep. Stop-if in Task 6 |
| Persistent SQLite, the detector's `PRAGMA data_version` or the callback's inherited `refresh_last_ts()` raises | Only on a database that is no longer readable: a closed or stopping connection (`StopException`, a closed handle), a poisoned broker, a runner whose transaction state is unusable, an I/O error or a corrupt image. Probed 2026-11-04: in WAL mode (which SimpleBroker forces, `_backends/sqlite/runtime.py` 121) the PRAGMA answers under an exclusive foreign writer and after the file is unlinked; `refresh_last_ts()` is a meta-table `SELECT` over the same connection path. On such a database the observation read fails too | No, and no read could succeed either | Not a reachable quiet state; no machinery, do nothing (Owner Decisions item 2). SimpleBroker's own tenth consecutive provider failure raises `RuntimeError` out of the wait (watcher.py 1680–1686) |
| Persistent SQLite, commit before the strategy's first check | The first check sets the baseline and runs the callback (watcher.py 1666–1671), so `_data_version_activity_pending` is set | Yes (the first return after start is always hinted) | Observation runs |
| Persistent SQLite, commit before the wait | Seen by the wait-entry check (1149) | Not needed | Kept: wait-entry check |
| Persistent SQLite, the drive thread's own write (same connection) | No data-version change. Such writes happen only in turns (the drive thread is in a turn or in the wait), and every turn is followed by a wait (base.py 1250–1252) | No | Found by the next wait-entry check (1149). Today the same. Manual waits (`MultiQueueWatcher.wait_for_activity`, tests calling `task.wait_for_activity(...)`) take the same entry check |

| Persistent SQLite, delete by another connection (heartbeat cancel) | The commit changes the data-version counter | Yes | Observation runs |
| Nonpersistent SQLite (`persistent=False`, standalone watchers only; every `BaseTask` passes `persistent=True`, base.py 333) | Each operation opens a fresh connection, so the getter's value does not move (comment 1223–1228) | No durable hint | Unchanged: observation and readiness on every return (transient fallback) |
| PostgreSQL native waiter, insert, move or rename | `pg_notify` in the same statement (`_sql.py` 196–207, 421–427, 112–135); the listener wakes `wait_any` | Yes, native hint | Observation runs |
| PostgreSQL native waiter, exact delete (heartbeat cancel) | `DELETE_MESSAGE_IDS_COUNT` sends no notification (`_sql.py` 82–94) | No | Unchanged: every native-waiter return keeps its check, so the native idle return (1 to 2 s) observes it; and the heartbeat's pre-emission rebuild (`weft/core/tasks/heartbeat.py` 548) guarantees no emission for a cancelled row whatever the observation timing. Owner Decisions item 1 |
| PostgreSQL native waiter, publication before the waiter registered | Dropped by the listener (runner.py 297) | No | Wait-entry check, after `_ensure_wait_strategy_started()` re-registers (1145, 1149) |
| PostgreSQL native waiter, listener failure | `_error` stored and raised from `wait_any` (305, 428–429); the wait marks itself degraded (1167) and polls | No hints (PostgreSQL has no data version, plugin.py 841–843) | Unchanged: degraded fallback checks every return; the next wait re-creates the waiter (917) and its entry check reads |
| PostgreSQL native waiter, a listener connection broken without an error | Notifications stop arriving | No | Unchanged: the native idle return keeps its checks (Owner Decisions item 1) |
| Any backend, waiter creation failed (`_native_activity_degraded` set at 942, 951) | Polling with or without a data version | Not reliably | Unchanged: degraded fallback checks every return |
| Any backend, a caller-injected `polling_strategy` (in the repository: tests only, `tests/tasks/test_multiqueue_watcher.py` 1195 and 1484, `tests/helpers/multiqueue_sigint_probe.py` 123) | `BaseWatcher._start_strategy` passes the same change callback to any strategy that has `start` (watcher.py 540–544); the three repository injections are `PollingStrategy` subclasses that inherit `wait_for_activity`, so their hint path is the default strategy's. A strategy that overrode `wait_for_activity` would own its own hints; none exists in the repository | Same as the default strategy, for the three verified subclasses | Same gate |
| Any backend, the observation read itself raises (the overrider catches, logs and returns `False`) | The same failure class as the detector row: the database is not readable | Not repeated by this plan | Do nothing (Owner Decisions item 2): the overriders' `except` blocks are unchanged, and the next hinted, deadline or wait-entry return reads |

Conclusion: on every path the design either reads on the return that carries
the evidence, reads on the next wait entry, or keeps today's per-return check
where the backend has no durable change detector (degraded or nonpersistent
fallback, native-waiter safety recheck). The only new latency is the
final-chunk case on persistent SQLite: one extra strategy return, which
follows immediately.

### Who is affected: every override and contributor

Search: `grep -rn "def _has_pending_observation\|def _activity_wait_queues" weft extensions integrations bin tests --include=*.py`
(no hits in `extensions/`, `integrations/` or `bin/`). Callers of
`_has_pending_observation`: only `multiqueue_watcher.py` 1149 and 1201.

| Override or contributor | File and line | What it reads | Loses | Keeps |
|---|---|---|---|---|
| `MultiQueueWatcher._has_pending_observation` (base) | `weft/core/tasks/multiqueue_watcher.py` 881 | Nothing (`False`) | Nothing | Everything |
| `MultiQueueWatcher._activity_wait_queues` (base) | 876 | Delivery queues that count as wait activity | Nothing | Waiter registration |
| `Manager._activity_wait_queues` | `weft/core/manager.py` 1925 | Adds `Manager._drain_stop_target_queues()` (5196: each target's `weft.state.tasks.<tid>` and `weft.log.tasks`) and each startup-feedback record's task-state queue, skipping TIDs in `_active_child_launches` (1930–1931) | Nothing | Registration, unchanged |
| `Manager._has_pending_observation` | `weft/core/manager.py` 1938 | First `_has_pending_drain_stop_observation()` (5211: `peek_many` newest on `weft.log.tasks` with `include_claimed=True`, then each target's state queue); then a `publication_pending` short-circuit (1949); then `peek_many` newest per interested `_ManagerStartupFeedback` (343) record | The read on every hint-free SQLite strategy return while startup feedback or drain targets exist (bounded windows) | Reads on hinted, deadline, native, degraded and wait-entry returns. Child publications come from child processes (data-version hints on SQLite, notifications on PostgreSQL). The Manager's own `weft.log.tasks` rows are not target evidence. The [MA-1.8] absence recheck (spec 03 line 478, `MANAGER_ADMISSION_RECHECK_SECONDS` = 1.0, `weft/_constants.py` 823) and the drain clocks remain as deadlines |
| `HeartbeatTask._activity_wait_queues` | `weft/core/tasks/heartbeat.py` 189 | Adds `weft.state.heartbeats` | Nothing | Registration |
| `HeartbeatTask._has_pending_observation` | `weft/core/tasks/heartbeat.py` 199 | `_registrations_rebuild_pending` short-circuit (210–211), then `has_pending(after_timestamp=cursor)`, and with rows a `peek_generator` of every row ID (212–223) | The permanent read on every hint-free SQLite return (the defect) | Upserts and cancels by other processes arrive as hints on SQLite; on PostgreSQL every native return keeps its check; the pre-emission rebuild (548) and the lease-expiry deadline (`next_wait_timeout`, 693) are unchanged |
| `ObservationWatcher` (test double) | `tests/tasks/test_multiqueue_watcher.py` 119–150 | `latest_pending_timestamp` on one queue | Its old test's direct `_activity_hint_action` call with no strategy return (181) no longer reads | Replaced by guards G1–G3 (Task 2) |

No other class in `weft/` overrides either method (`Consumer` and `Manager`
override `_queue_counts_as_wait_activity`, `weft/core/tasks/consumer.py` 164
and `weft/core/manager.py` 1234, which shapes readiness only and is
unchanged). Taut's copy of `MultiQueueWatcher` (`../taut`, [WT-2a.6]) has no
`_has_pending_observation` and is out of scope.

### Files to modify

- `weft/core/tasks/multiqueue_watcher.py`: `_activity_hint_action` (the
  gate) and two docstrings (`_activity_hint_action`,
  `_wait_for_activity_body`). No new field, method, class or import.
- `tests/tasks/test_multiqueue_watcher.py`, `tests/tasks/test_heartbeat.py`,
  `tests/core/test_manager.py` (the read-method list moves to the helper;
  no assertion change), new `tests/helpers/broker_reads.py`.
- `docs/specifications/01-Core_Components.md` ([CC-2.1] paragraph,
  implementation mapping, `## Related Plans` at line 1312).
- `CHANGELOG.md` (`## [Unreleased]` / `### Changed`).
- `docs/plans/README.md` (Status Index row) and this plan.

This matches the scope the series lead set ("touches
`weft/core/tasks/multiqueue_watcher.py` and tests only, no Manager or
heartbeat changes"). `weft/core/manager.py` and `weft/core/tasks/heartbeat.py`
are read for the overrider analysis and not edited.

### Read first

- `weft/core/tasks/multiqueue_watcher.py` lines 857–1000 and 1133–1340.
- `weft/core/tasks/base.py` lines 1214–1270 and 1508–1564.
- `.venv/lib/python3.14/site-packages/simplebroker/watcher.py` lines
  496–556 and 1359–1702.
- `weft/core/manager.py` lines 1914–1975 and 5196–5250;
  `weft/core/tasks/heartbeat.py` lines 182–230, 530–560, 693–725.
- `tests/tasks/test_multiqueue_watcher.py` lines 60–200 (`FakeWaiter`,
  `TimeoutWaiter`, `ObservationWatcher`) and 3194–3229
  (`test_nonpersistent_fallback_confirms_pending_after_one_strategy_turn`,
  the stubbed-strategy pattern G3 copies).
- `tests/core/test_manager.py` lines 5475–5565
  (`test_manager_never_reads_or_deletes_heartbeat_registrations`, the
  broker-boundary counting pattern).
- `tests/tasks/test_heartbeat.py` lines 574–600 and 700–790.

### Shared paths to reuse, not duplicate

- The broker-boundary read counter: today inline in
    `tests/core/test_manager.py` (`_HEARTBEAT_READ_METHODS` at 5478–5493, 14
  method names, the
  class found from a live connection's MRO at 5517–5520, the wrapper at
  5531–5547). Task 1 moves the method list and wrapper into
  `tests/helpers/broker_reads.py` and both tests use it.
- `tests.helpers.test_backend.active_test_backend` and
  `POSTGRES_TEST_BACKEND` (lines 19, 23) for backend gates, as
  `test_postgres_dynamic_membership_rebinds_native_waiter` does
  (`tests/tasks/test_multiqueue_watcher.py` 2384–2385).
- `make_heartbeat_taskspec` and `_upsert` in `tests/tasks/test_heartbeat.py`.

### Comprehension questions (answer in the execution log before the first edit; a wrong answer is a stop-and-re-read blocker)

1. A persistent SQLite task's strategy returns with no hint before the
   deadline. Why can no other process's publication be waiting unseen, and
   what is the worst case? Expected: the getter reads `PRAGMA data_version`
   on the drive thread's connection at the top of every pass and between
   20 ms chunks (watcher.py 1416–1423, 1466–1473); any commit by another
   connection changes it and runs the callback that sets
   `_data_version_activity_pending` (multiqueue_watcher.py 977–981). A commit
   in the final chunk produces one hint-free return; the next strategy call
   returns at once with the hint. Worst case: one extra return, no extra
   sleep.
2. The Manager writes a lifecycle row to `weft.log.tasks` on its own drive
   thread during a drain. Does a hint arrive, and does it matter? Expected:
   no hint (same connection). It does not matter: the write happens in a
   turn, the drive loop then waits (base.py 1250–1252), and the wait-entry
   check (1149) reads; and the Manager's own row is not a target's terminal
   evidence anyway.
3. Why does the plan keep the observation check and the readiness scan on
   every PostgreSQL native-waiter return? Expected: an exact delete sends no
   notification (`_sql.py` 82–94), so a heartbeat cancel has no hint; a
   listener connection broken without an error delivers nothing; the native
   idle return (watcher.py 1447–1449, 1386, 1636–1648) is SimpleBroker's slow
   safety recheck ([CC-2.1] line 196) for exactly that. Owner Decisions item 1.
4. What does the wait-entry check find that the strategy cannot report?
   Expected: a publication committed before the waiter registered (the
   PostgreSQL listener drops notifications for unregistered queues,
   runner.py 297) or before the call; and the drive thread's own writes from
   the preceding turn.
5. The data-version provider, the change callback's `refresh_last_ts()`, or
   an overrider's observation read raises. Why does the gate not keep or
   repeat the read? Expected: on a reasonable SQLite those calls fail only
   on a database that is no longer readable (WAL mode; probed 2026-11-04),
   on which the observation read fails as well, so there is nothing a
   repeated read could recover; the owner ruled that when those reads fail
   the correct response is to do nothing (Owner Decisions item 2). The
   overriders' `except` blocks (manager.py 1968–1970, 5246–5248;
   heartbeat.py 224–226) already log and return `False`, and stay as they
   are.

## Design

### Task 2: the gate

In `_activity_hint_action`, after the four consume lines (1196–1199),
compute the gate's facts and return `"continue"` before any broker read when
nothing on this return can carry evidence:

```python
        local_hint = self._strategy.consume_local_activity_hint()
        native_hint = self._strategy.consume_native_activity_hint()
        data_version_hint = self._data_version_activity_pending
        self._data_version_activity_pending = False
        uses_native_activity = self._strategy.uses_native_activity()
        # (the six comment lines at 1223-1228 move here verbatim)
        transient_fallback = not self._persistent and not uses_native_activity

        # Read only on a return that can carry evidence [CC-2.1], [QUEUE.7].
        # A healthy persistent SQLite return with no hint before the deadline
        # means no other connection committed since the last check; the
        # owner's own writes are made in turns and read at the next wait entry.
        if not (
            local_hint
            or native_hint
            or data_version_hint
            or (deadline is not None and time.monotonic() >= deadline)
            or uses_native_activity
            or self._native_activity_degraded
            or transient_fallback
        ):
            return "continue"

        if self._has_pending_observation():
            return "return"
```

The rest of the method (1204–1237) stays as it is, with two rules:

- `deadline_reached` is still computed fresh at line 1215, **after** the
  observation read, exactly as today. A slow observation read that crosses
  the deadline therefore still skips the readiness scan on an unhinted
  native or fallback return and hands the turn to the owner (guard R7). The
  gate's own deadline test is a separate expression and is not reused.
- `uses_native_activity` and `transient_fallback` replace their recomputed
  forms at 1216 and 1229–1231; neither can change during the read.

Readiness is unchanged on every path: the existing tail already skips the
scan on a healthy hint-free persistent SQLite return (it falls through to
1237), and the gate returns before it on exactly that return.

### Task 2: what the gate deliberately does not do

`_on_data_version_change` (977–981), the overriders' `except` blocks and
the strategy are untouched. The cross-family round 1 asked for three pieces
of failure handling (a flag-first callback, a strategy subclass reporting
detector silence, and a retry flag for a failed observation read); the
author declined all three under the owner's 2026-11-04 rulings (`## Owner Decisions` item 2): those
reads fail only on a database that is no longer readable, and the correct
response then is to do nothing.

### Task 2 (withdrawn): detector uncertainty, reported through the strategy's public start seam

Withdrawn on 2026-11-04 (`## Owner Decisions` item 2). The round-1 design
(a module-private `PollingStrategy` subclass installed through
`_create_strategy`, wrapping the provider SimpleBroker passes to the public
`start()` seam, and a `_consume_data_version_uncertainty` query) is kept in
this plan's history at `711be8c7` and is not implemented.

### Task 2: the wait entry

`_wait_for_activity_body` keeps lines 1145–1150 unchanged. The comment at
1147–1148 gains "Later checks run only on returns that can carry evidence."

### Task 3 (withdrawn): the recheck flag

Withdrawn on 2026-11-04 (`## Owner Decisions` item 2). The round-1 design
(an `_observation_recheck_pending` flag, a `_defer_pending_observation()`
method, and one added line in each of the three overrider `except` blocks)
is kept in this plan's history at `711be8c7` and is not implemented.

## Invariants and Constraints

### What must not change

- Register before read: the wait-entry check stays after
  `_ensure_wait_strategy_started()`.
- Notification interests stay notification-only ([QUEUE.7]): no handler, no
  consumption, no reserve route, no new interest queue.
- The readiness scan's behavior on every path (plan 2's state, including the
  transient and degraded fallbacks and the native-waiter return).
- The strategy's pass, burst, backoff and native idle cadence (SimpleBroker's;
  [ALT-WT-002]).
- No timer, no `next_wait_timeout()` change, no new thread, no sleep.
- One arbiter per reactor ([WT-2a.6], [IMPL.10]): drive ownership and the
  manual-wait exclusion (`wait_for_activity`, 1239–1270) are untouched.
- No Manager or heartbeat change: `weft/core/manager.py` and
  `weft/core/tasks/heartbeat.py` are not edited.
- TID, state transitions, reserved policy, `spec`/`io` immutability,
  process titles: not touched.

### Hidden couplings

- Same-connection writes are not hinted on SQLite. The design depends on
  every drive-thread write being followed by a wait-entry check
  (base.py 1250–1252) or, for manual waits, by the caller's next
  `wait_for_activity` call. A future path that writes on the drive thread
  inside the wait (between strategy returns) would break this; none exists
  today (the deferred-signal handler performs no broker operation, [IMPL.10];
  observation and readiness checks only read).
- The first return after a strategy start is always hinted because the
  data-version baseline runs the callback (watcher.py 1666–1671). A future
  SimpleBroker change that stops calling back on the baseline would make the
  first return hint-free; the wait-entry check still covers publications
  before the wait, and the next foreign commit after the baseline still
  changes the counter. Task 2 guard G2 would catch a regression in hinting.
- The gate trusts a hint-free persistent SQLite return because
  SimpleBroker's strategy checks `PRAGMA data_version` on every pass and
  runs the change callback on every foreign commit. A future SimpleBroker
  change that stops calling the callback, or that polls the PRAGMA less
  often than once per pass, lengthens the time to observation; G2's
  strategy-return count detects the first and Task 6's latency stop-if the
  second.
- Tests that call `_activity_hint_action(deadline=None)` directly with no
  strategy return encoded the unconditional call. Only one exists
  (`tests/tasks/test_multiqueue_watcher.py` 181); Task 2 replaces it.
- Tests that write on the test thread while a task waits on another thread
  get hints (different connections); tests that write and then wait on the
  same thread are covered by the wait-entry check. Both shapes exist in
  `tests/tasks/test_heartbeat.py` (for example 947–960 on one thread, and 762–766 from the test thread
  against a driven task).
- Plan 9 (`docs/plans/2026-11-02-test-suite-structure-plan.md`, `draft`)
  edits `tests/tasks/test_multiqueue_watcher.py` (its rows T3, T4, C1). Plan 8
  cites `weft/core/tasks/multiqueue_watcher.py:1103` for a broad `except`.
  Whichever lands second rebases; line numbers in this plan are re-verified
  by `grep -n` at Task 0.

### Fatal versus best-effort

- Observation reads are best-effort: an overrider catches broker failures
  and returns `False` (manager.py 1968–1970, 5246–5248; heartbeat.py
  224–226). Unchanged: a failed read does nothing (Owner Decisions item 2).
- Readiness scan failures raise (1311–1320) and reach the task's error
  handling. Unchanged.
- Waiter failures degrade to polling (1160–1169). Unchanged.

### Review gates

- No new execution path; no new queue, field, constant or config key.
- No drive-by refactor (the three reused locals in `_activity_hint_action`
  are the only restructuring).
- No mock of the broker in the contract tests (R2, G1, G2 use real queues
  and the real strategy; R1, R7 and G3 stub the strategy only to force a
  return shape).
- No failure-path machinery: no retry flag, no strategy subclass, no
  detector query, no reordered callback (Owner Decisions item 2).
- Cross-family plan review before implementation; completed-work review
  before `completed`.

### Difference register (owner's no-diminution rule)

Classes: A owner-decided removal; N no difference; C changed, no loss;
D diminution (must be fixed in plan or escalated).

| # | Observable | Before (`6054aea1`) | After | Class | Proof |
|---|---|---|---|---|---|
| 1 | Resident heartbeat, empty table, persistent SQLite: reads of `weft.state.heartbeats` over a quiet 30 s window | 230 (plan 6) | 0 after the wait-entry check | C (removed cost; the owner's rule "idles when not needed") | R2 `[0]`; Task 6 measurement |
| 2 | Same with 1 and 10 rows | 458 with 1 row; 10 rows not measured | 0 | C | R2 `[1]`, `[10]`; Task 6 |
| 3 | Manager reads during the [MA-1.8] startup window and a system-STOP drain, persistent SQLite, between publications | One read (or more) per strategy return | Reads only on hinted, deadline and wait-entry returns | C | Task 4 anchors; R1 |
| 4 | Time from a foreign commit to its observation, persistent SQLite, detector and callback working | The first strategy return after the commit | The first hinted return: the same return, or the next one when the commit lands in a pass's final 20 ms chunk; that next return follows with no sleep | N within one strategy return (Stop-if in Task 6) | G2; Task 6 latency measurement |
| 5 | Owner's own write, then the next wait | Found by the wait-entry check | Same | N | G1 |
| 6 | PostgreSQL native-waiter returns (notification or slow safety recheck): observation and readiness | Both run | Both run | N (Owner Decisions item 1) | Task 6 PostgreSQL measurement |
| 7 | Degraded and nonpersistent fallbacks | Both run on every return | Same | N | G2 `[nonpersistent]`, G3 |
| 8 | Deadline return, including one crossed during a slow observation read | Observation runs; no readiness scan on an unhinted return | Same (the tail's deadline is computed after the read) | N | R1; R7 |
| 9 | A failed observation read, persistent SQLite (any overrider) | Retried on the next strategy return (about 0.1 s) | Not retried; the next hinted, deadline or wait-entry return reads | A: the owner ruled (2026-11-04) that on a reasonable SQLite an observation read fails only on a database that is no longer readable, where no retry succeeds, and that the correct response is to do nothing (Owner Decisions item 2) | — |
| 10 | A failed `PRAGMA data_version` or `refresh_last_ts()` on a foreign commit, persistent SQLite | Observation runs on every return regardless of the detector | The hint is lost with the failure; the next hinted, deadline or wait-entry return reads | A: the same ruling; both calls fail only on a database that is no longer readable (probed 2026-11-04) | — |
| 11 | Readiness scans everywhere else | Plan 2's rule | Same | N | existing readiness tests (Task 4 list) |
| 12 | Idle voluntary context switches, idle manager tree | Plan 2's after-numbers: 10,088 (SQLite) and 20,347 (PostgreSQL) over 30 s at `da2e3b92`; re-measured in Task 0 with the heartbeat now resident | Expected lower on SQLite (fewer broker calls per pass), equal on PostgreSQL. The strategy's own SQLite wake cadence (about 8.7 returns a second) is unchanged | C | Task 0 and Task 6 measurements |
| 13 | Observation semantics: which queues, which reads, which thread | — | Unchanged | N | — |
| 14 | A caller-injected `polling_strategy` | Observation on every return | The same gate as the default strategy (the change callback reaches any strategy with `start`, watcher.py 540–544; the three repository injections inherit `PollingStrategy.wait_for_activity`) | C | G2 runs with the default strategy; the sigint probe (`tests/helpers/multiqueue_sigint_probe.py`) still passes |

No D row remains. Rows 9 and 10 are owner-decided (A).

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Spec Baseline

- `6054aea1` — `docs/specifications/01-Core_Components.md`,
  `docs/specifications/03-Manager_Architecture.md`,
  `docs/specifications/04-SimpleBroker_Integration.md`,
  `docs/specifications/05-Message_Flow_and_State.md`,
  `docs/specifications/07-System_Invariants.md` at plan authoring time (the
  cited spans of 01 and 07 are identical at `4372e20e`).
- Plan type: implementation with spec revision ([CC-2.1] only).
- Promotion baseline: recorded here at Task 5 (the landing commit, strategy B).

## Proposed Spec Delta

Promotion strategy:

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| `docs/specifications/01-Core_Components.md` | B — atomic with the code | [CC-2.1] current-role paragraph (lines 201–202), [CC-2.1] implementation mapping (lines 180–184), `## Related Plans` (line 1312 section) |

Sentences checked and left unchanged, with the reason:

- [QUEUE.7], `07-System_Invariants.md` lines 190–191: "Registration precedes
  the initial read; changed publications can return control even without
  delivery readiness." It speaks to order (registration before the first
  read), which this plan keeps, and to what a publication may do, not to when
  later checks run. No delta.
- [IMPL.10], `07-System_Invariants.md` lines 865–911: drive ownership and
  deadline propagation; no sentence about observation timing. No delta.
- [SB-0.4], `04-SimpleBroker_Integration.md` lines 150–200: context
  resolution, sessions and the waiter-replacement mapping; no sentence about
  observation timing. No delta.
- [CC-2.5], `01-Core_Components.md` lines 885–923: no observation timing. No
  delta.
- [MA-1.8] mapping, `03-Manager_Architecture.md` line 585, already names
  `MultiQueueWatcher._activity_hint_action` as owning "observation wake
  filtering". Still true. No delta.

### `docs/specifications/01-Core_Components.md` [CC-2.1] — replace lines 201–202

Current text (verbatim):

```text
inactive queue even though SQLite `data_version` does not change. Empty,
unrelated and deadline wakes do not reset useful-activity state.
```

Replacement:

```text
inactive queue even though SQLite `data_version` does not change. Empty,
unrelated and deadline wakes do not reset useful-activity state.
Notification-only observation checks ([QUEUE.7]) read the broker only when a
return can carry new evidence. One check runs at wait entry, after the waiter
is registered, so a publication committed before the wait is found without a
strategy return. After that, a check runs on a strategy return that carries a
local, native or data-version hint; on a return at the owner's deadline; on
every native-waiter return, including the strategy's slow safety recheck; and
on every return of the degraded and nonpersistent polling fallbacks,
which have no durable change detector. A persistent SQLite return with none
of these reads nothing: an unchanged `data_version` means no other connection
committed since the last check, and the owner's own writes are made in turns
and found by the next wait-entry check. The gate adds no retry of its own:
a failed detector check, cache refresh or observation read opens no later
check, SimpleBroker's own polling continues unchanged, and on a database
that is not readable nothing is done. The readiness scan
keeps its rule: it runs on returns with a local, native or data-version hint
(before any deadline test), on native-waiter returns before the deadline and
on fallback returns, and not on a hint-free return at or past the owner's
deadline (the deadline is evaluated after the observation read), which hands
the turn to the owner.
```

### `docs/specifications/01-Core_Components.md` [CC-2.1] implementation mapping — replace lines 180–182

Current text (verbatim):

```text
`_apply_topology_mutation_on_owner()`. Firing coverage is in
`tests/tasks/test_multiqueue_watcher.py`; BaseTask ownership preservation is in
`tests/tasks/test_task_execution.py`. Displaced waiter close uses the
```

Replacement:

```text
`_apply_topology_mutation_on_owner()`. `_activity_hint_action()` owns the
return gate for observation and readiness checks.
Firing coverage is in
`tests/tasks/test_multiqueue_watcher.py`; BaseTask ownership preservation is in
`tests/tasks/test_task_execution.py`. Displaced waiter close uses the
```

### `docs/specifications/01-Core_Components.md` `## Related Plans` (the section at line 1312) — append after the "Reactor edge timer floors" bullet

```text
- [Reactor wait observation gate](../plans/2026-11-03-watcher-hint-gated-observation-plan.md) reads notification-only observation evidence only on strategy returns that can carry it ([CC-2.1], [QUEUE.7]).
```

## Slice Order

1. Task 0 (reading, comprehension, baseline measurements; no edit).
2. Task 1 RED, Task 2 GREEN and guards, Task 4 anchors, Task 5 spec and
   documentation: one landing commit (strategy B). Task 3 is withdrawn.
3. Task 6 after-measurements, gates, closure (a second commit for the plan's
   log and status only).

## Owner Decisions

1. **PostgreSQL native-waiter returns (recommendation: (a), the plan's
   default; the plan does not wait on this).** On PostgreSQL every return of
   the native waiter that carries no notification is the strategy's native
   idle return, about once every 1 to 2 s (watcher.py 1386, 1447–1449,
   1636–1648), which [CC-2.1] line 196 calls the strategy's "slow safety
   recheck". Plan 2 measured one readiness probe per such return and left
   the classification to the owner (plan 2 owner decision 3).
   - (a) Keep the observation check and the readiness scan on every
     native-waiter return (no change on PostgreSQL). A resident heartbeat on
     PostgreSQL keeps about 20 observation reads per 30 s with an empty
     table. The recheck answers [WT-7]'s question: an exact delete sends no
     notification (`_sql.py` 82–94), so a heartbeat cancel is never hinted;
     and notifications to a listener whose connection broke without an error
     are lost.
   - (b) Skip both on hint-free native-waiter returns before the deadline.
     Zero idle reads on PostgreSQL. A notification lost on a silently broken
     listener connection would be recovered only at a deadline or the next
     notification, for every task's work delivery, not only observation: a
     diminution under that failure. It also changes what [CC-2.1]'s "slow
     safety recheck" means and plan 2's measured state; it belongs in its own
     plan with its own measurement of listener failure modes.
   - (c) Skip only the observation check on hint-free native-waiter returns,
     keep readiness. Halves the idle reads. A PostgreSQL heartbeat cancel is
     then observed at the next due emission's pre-emission rebuild
     (heartbeat.py 548; no emission difference); Manager startup feedback
     under a broken listener waits for the [MA-1.8] absence recheck. A
     smaller diminution under the same failure.
   Recommendation: (a). The defect the reviewer found is a read attached to
   a return that cannot carry evidence; on PostgreSQL the hint-free return is
   SimpleBroker's evidence-recovery recheck, which is not that.
2. **Failure of the underlying reads: ruled, closed (owner, 2026-11-04).**
   The cross-family round 1 asked the plan to keep or repeat the observation
   check when the data-version provider raises or returns `None` (F2), when
   the change callback's inherited `refresh_last_ts()` raises (F1), or when
   an overrider's own observation read raises (the round-0 Task 3 retry
   flag). The owner asked under what circumstance `PRAGMA data_version`
   fails. Answer, from the installed SimpleBroker 8.7.2 and a direct probe:
   the statement itself does not fail on an open connection in WAL mode
   (it answered under an exclusive foreign writer and after the file was
   unlinked); every "PRAGMA failure" SimpleBroker can see is the plumbing
   around it (`StopException` once the stop event is set, a closed handle,
   a poisoned broker, an unusable runner transaction state, a fork-safety
   check that spawn never trips) or a database that is no longer readable
   (I/O error, corrupt image, a dropped mount). `refresh_last_ts()` is a
   meta-table `SELECT` over the same connection path and fails in the same
   class. An overrider's observation read fails in the same class. In every
   case the read that a retry would perform fails too.
   Owner rulings, verbatim: "Neither" (to the fork between a SimpleBroker
   release and a Weft-side strategy subclass); "Refesh last ts is also not
   going to fail on a reasonable sqlite"; "If those underlying reads fail,
   do nothing is the correct response"; "And the proposal is against the
   theory also" (see `## Theory`, "Theory check of the declined remedies":
   [WT-4] 4.4 no over-armoring, [WT-2a.8], [ALT-WT-010]).
   Consequences, applied in this revision: no flag-first callback, no
   strategy subclass, no `_create_strategy` override, no detector query, no
   retry flag, no `_defer_pending_observation`, no edit to
   `weft/core/manager.py` or `weft/core/tasks/heartbeat.py`; tests R3, R4,
   R5, R6, R8 and R9 withdrawn; register rows 9 and 10 recorded as A; the
   SimpleBroker upstream item declined (item 3). The overriders' existing
   `except` blocks, which log and return `False`, are the "do nothing".
3. **SimpleBroker upstream item: declined (2026-11-04).** The round-1
   proposal (report detector unavailability as uncertainty, keep a change
   whose callback raised, reset the failure counter on success) described
   SimpleBroker behaviors that are not defects in operation: a suppressed
   PRAGMA error or a swallowed callback error occurs only on a database
   that is no longer readable, where every following read fails as well.
   Nothing is filed. The owner's standing rule (a SimpleBroker plan or
   release is never gated on a Weft plan or branch) is unaffected.

## Tasks

Each behavior change is RED first. Run each task's verification before the
next.

### Task 0. Read, answer, measure the baseline (no edit)

- Outcome: comprehension answers 1–5 in the execution log; every line number
  this plan cites re-verified with `grep -n` (record any shift); baseline
  measurements recorded with the commit SHA.
- Measurements (each on SQLite, then PostgreSQL with the DSN that
  `./bin/pytest-pg --keep-container` provisions or an existing
  `WEFT_PG_TEST_DSN`, with `BROKER_TEST_BACKEND=postgres`):
  1. Idle manager tree, voluntary context switches and CPU over 30 s: plan
     2's `idle_tree.py`, source in plan 2's execution log
     (`docs/plans/2026-11-02-reactor-edge-timer-floors-plan.md` lines
     1454–1529), unchanged. The heartbeat is now a resident child, so record
     it by PID.
  2. Resident heartbeat reads of `weft.state.heartbeats` over 30 s with 0, 1
     and 10 rows: an in-process `HeartbeatTask` under `WeftTestHarness`,
     rows written with `upsert_heartbeat(..., interval_seconds=3600,
     lease_seconds=7200)` before start, `run_until_stopped()` on a thread, 5 s
     settle, then a 30 s count of broker read calls naming the queue (the
     method list of `tests/helpers/broker_reads.py`, wrapped on the broker
     class found from a live connection's MRO). Also count strategy returns
     (wrap `simplebroker.watcher.PollingStrategy.wait_for_activity`) and
     `_has_pending_observation` calls. Paste the script into the log.
  3. Observation latency: in the same setup with 0 rows, after the settle,
     20 upserts from the test thread, 1 s apart, each with a distinct
     heartbeat ID (a repeated ID is already a member after its first
     upsert); for each, the time from the
     write's return to `heartbeat_id in task._registrations`, and the number
     of strategy returns in between. Record median and maximum of both.
  4. Plan 2's `quiet_task.py` (plan 2 lines 1557–1635): readiness probes and
     strategy returns for an idle Consumer, to show readiness unchanged.
  5. List the installed `BrokerCore` methods (`simplebroker/db.py` 1320
     onward) and confirm the two read tuples of `tests/helpers/broker_reads.py`
     (Task 1) are complete for SimpleBroker as installed; record any
     difference.
- Done when: all five sit in the log, per backend.

### Task 1. RED: a quiet persistent wait reads nothing

- Files: new `tests/helpers/broker_reads.py`; `tests/core/test_manager.py`
  (import the moved list; no assertion change); `tests/tasks/test_multiqueue_watcher.py`;
  `tests/tasks/test_heartbeat.py`.
- `tests/helpers/broker_reads.py`: module docstring citing [QUEUE.7];
  - `NAMED_QUEUE_READ_METHODS: Final[tuple[str, ...]]`: the 14 names of
    `_HEARTBEAT_READ_METHODS` (test_manager.py 5478–5493) plus
    `queue_exists` (`simplebroker/db.py` 3329), which the existing list
    omits: 15 names. These are every `BrokerCore` method in SimpleBroker
    8.7.2 (`simplebroker/db.py` 1320–4198) that reads one named queue given
    as its first argument;
  - `WHOLE_DATABASE_READ_METHODS: Final[tuple[str, ...]]`: `list_queues`
    (3247), `get_queue_stats` (3311), `list_queue_stats` (3366),
    `get_overall_stats` (3410), `count_claimed_messages` (3423), `status`
    (3436), which read every queue and cannot be attributed to one;
  - `QUEUE_DELETE_METHODS` (test_manager.py 5494: `delete`,
    `delete_message_ids`);
  - `count_queue_calls(monkeypatch, sample: Queue, queue_name: str) -> dict[str, int]`
    that finds the broker class from `sample.get_connection()`'s MRO (the
    class whose `vars()` holds `peek_many`, as 5517–5520), wraps every listed
    method, and counts under `"reads"` the named-queue reads whose first
    positional argument or `queue` keyword equals `queue_name`, under
    `"whole_database_reads"` every call of a whole-database method, and
    under `"deletes"` the named deletes.
  The claim is exactly this: every read of the named queue through a
  `BrokerCore` method, and every whole-database read, in the counting
  process. It does not cover reads made in another process or through a
  method added to SimpleBroker after 8.7.2 (Task 0 re-lists the class's
  methods against the tuples). `test_manager.py` uses the helper in place
  of its inline wrapper (5531–5547); its assertion becomes
  `counts["reads"] == 0 and counts["deletes"] == 0`, the two keys it
  asserted before (the helper also returns `whole_database_reads`, which
  this test does not assert), now with `queue_exists` counted too.
- **R1** `tests/tasks/test_multiqueue_watcher.py::test_hint_free_persistent_return_reads_no_observation`
  (module is `shared`): an `ObservationWatcher` on a real queue
  (`persistent=True`), with a counter around `_has_pending_observation`.
  Replace `watcher._strategy.wait_for_activity` with a function that counts
  its call and waits 0.01 s on a fresh `threading.Event` (one modelled
  polling pass), and make `consume_local_activity_hint`,
  `consume_native_activity_hint` and `uses_native_activity` return `False`
  (the stubbing shape of
  `test_nonpersistent_fallback_confirms_pending_after_one_strategy_turn`,
  3194–3229). Nothing is written. Call `watcher.wait_for_activity(timeout=0.3)`.
  Assert `strategy_calls >= 5` and `observation_calls <= 2` (the wait-entry
  check, plus at most one on the deadline return; the loop's own deadline
  check at 1154–1156 may return first). RED today: one observation call per
  strategy return (about 30). Mutation: restoring the unconditional call at
  1201 fails it.
- **R2** `tests/tasks/test_heartbeat.py::test_resident_heartbeat_reads_no_registrations_while_quiet`
  parametrized `rows` in `[0, 1, 10]`: skip when
  `active_test_backend() == POSTGRES_TEST_BACKEND` with the reason "native
  waiter returns keep their check (plan 13 owner decision 1)". Write the rows
  with `upsert_heartbeat(context, registrant_tid=..., heartbeat_id=f"quiet-{index}",
  interval_seconds=3600, lease_seconds=7200, destination_queue=f"quiet.dest.{index}", message="tick")`
  before construction; construct `HeartbeatTask(context.broker_target,
  make_heartbeat_taskspec(tid, workdir))`; count with
  `count_queue_calls(monkeypatch, context.queue(WEFT_HEARTBEAT_REGISTRATIONS_QUEUE), WEFT_HEARTBEAT_REGISTRATIONS_QUEUE)`;
  wrap `_process_reactor_turn` to record turns; run `run_until_stopped` on a
  thread; settle as `test_heartbeat_with_empty_table_runs_zero_timer_turns_and_wakes_on_upsert`
  does (no turn for 4.0 s, 20 s limit, lines 744–750); reset the counts;
  wait `HEARTBEAT_READ_QUIET_WINDOW_SECONDS = 5.0` (module constant with a
  comment that it is the measured quantity, as lines 705–707); assert
  `counts["reads"] == 0` and `counts["whole_database_reads"] == 0`, zero
  turns, the thread alive. Then upsert one more row from the test thread and
  require it in `task._registrations` within 5.0 s (the wake still works).
  `finally`: `task.stop(join=False)`, thread join, `task.cleanup()`. RED
  today: about 40 reads with 0 rows in 5 s (author's probe: 41 and 80).
  Mutation: the unconditional call restored. This satisfies plan 6's round-2
  disposition (a read count at the broker boundary over a quiet window).
- Verify RED: `./.venv/bin/python -m pytest tests/tasks/test_multiqueue_watcher.py -k hint_free_persistent -q`
  and `./.venv/bin/python -m pytest tests/tasks/test_heartbeat.py -k reads_no_registrations -q`
  fail on the asserted counts (record the failure lines); `tests/core/test_manager.py -k never_reads_or_deletes -m ""` still passes.
- Stop if: R2 fails for another reason (a turn in the window, a read in a
  turn): find the reader before proceeding.

### Task 2. GREEN: the gate and the guards

- Files: `weft/core/tasks/multiqueue_watcher.py`; `tests/tasks/test_multiqueue_watcher.py`.
- Implement the `## Design` subsections "Task 2: the gate" and "Task 2: the
  wait entry". Update the `_activity_hint_action` docstring to the gate rule with
  `Spec: [CC-2.1], [QUEUE.7]`, and add one sentence to the
  `_wait_for_activity_body` docstring (1134–1140): "The observation check
  runs at entry and then only on returns that can carry evidence."
- Write the guards below first; after the gate lands, apply each named
  mutation by hand, run its test and record the failure line.
- Replace `test_notification_only_publication_returns_owner_without_delivery`
  (154–193) with G1 and G2. Its two cases are superseded: the `True` case
  becomes G1 with a stronger assertion; the `False` case called
  `_activity_hint_action` with no strategy return (181), which is the
  hint-free shape the new contract stops reading on, so it is retired and
  its real-wait form is G2. Both keep its factory wrapper (163–171: a
  monkeypatched `create_activity_waiter_for_queues` that appends each
  registered queue-name list to `registrations` and delegates to the real
  factory) and its membership assertion `registrations == [[observation.name]]`
  (182). Its delivery assertions move into G1 and G2: the observation queue
  is not in `watcher._queues`, `_has_pending_messages()` is `False`, the row
  is still there (`peek_one()`), and a later short wait does not report the
  retained row again.
  - **G1** `test_notification_publication_before_wait_is_found_without_a_strategy_return`
    (shared): install the factory wrapper; construct `ObservationWatcher`
    on queue `first` (the constructor registers it,
    `_ensure_multi_activity_waiter()` at 251); then set
    `watcher.observation` to a second queue `second` (the interest change of
    `test_notification_subscription_replacement_preserves_waiter_custody`,
    221) and write one row to `second`, so that this wait must re-register
    before it reads. Make `_has_pending_observation` record
    `list(registrations)` at its first call; wrap
    `watcher._strategy.wait_for_activity` with a counter; call
    `watcher.wait_for_activity(timeout=5.0)`. Assert the first observation
    call saw `registrations == [[first.name], [second.name]]` (registration
    happened before the initial read), `observed_timestamp` equals the row's
    message ID, the strategy counter is 0, and the moved delivery assertions
    (with `second` as the observation queue). Mutations: removing the
    wait-entry check (1149–1150) makes the strategy run (counter ≥ 1);
    moving it above `_ensure_wait_strategy_started()` (1145) makes the first
    observation call see only `[[first.name]]`.
  - **G2** `test_notification_publication_during_wait_is_found_on_the_hinted_return`
    parametrized `persistent` in `[True, False]` (shared; on PostgreSQL the
    persistent case exercises the real native waiter):
    `ObservationWatcher` gains a keyword `persistent: bool = True` passed to
    `MultiQueueWatcher.__init__`; install the factory wrapper. First call
    `watcher.wait_for_activity(timeout=0.05)` once, so the strategy starts,
    its data-version baseline is taken and the baseline callback's hint is
    consumed; then assert `watcher._data_version_activity_pending is False`.
    Only then wrap the strategy's `wait_for_activity` to count calls and set
    a `threading.Event` on first entry. A writer thread waits for the event,
    then writes the observation row (a different thread, so a different
    connection) and records its message ID. Call
    `watcher.wait_for_activity(timeout=20.0)` on the test thread, measure the
    elapsed time, join the writer. Assert `registrations == [[observation.name]]`,
    `observed_timestamp` equals the written ID, elapsed < 10.0 s, and the
    strategy calls after the write are at most 2 (record the count). The
    write is not aligned with a detector check on purpose; the bound of 2
    covers every alignment (found on the same return, or on the next one
    when the commit lands in a pass's final chunk).
    Mutations: dropping `data_version_hint` from the gate fails the
    persistent SQLite case (observed only at the 20 s deadline); dropping
    `transient_fallback` fails the nonpersistent case (the baseline hint
    was consumed before the publication was armed, and a nonpersistent
    getter's value does not move); dropping `uses_native_activity` with
    `native_hint` fails the PostgreSQL case.
  - **G3** `test_notification_publication_on_degraded_fallback_is_found_without_a_hint`
    (shared): monkeypatch
    `weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues` to
    raise `RuntimeError("injected")` before constructing the
    `ObservationWatcher`, so `_native_activity_degraded` is `True` and no
    waiter exists on either backend (942, 951); assert both. Stub the
    strategy as in R1, the stub writing the observation row from a helper
    thread (joined inside the stub) on its first call only. Call
    `watcher.wait_for_activity(timeout=5.0)`. Assert the strategy was called
    exactly once and the row was observed. Mutation: dropping
    `_native_activity_degraded` from the gate makes the stub run until the
    deadline.
  - **R7** `test_slow_observation_crossing_the_deadline_skips_readiness`
    (shared): an `ObservationWatcher` whose `_has_pending_observation`
    returns `False` at once on its first call (wait entry) and on its second
    call waits 0.2 s on a fresh `threading.Event`, then returns `False`.
    Stub the strategy as in R1 except `uses_native_activity` returns `True`
    (the native-waiter return shape, no hint). Count `_has_pending_messages`
    calls. Call `watcher.wait_for_activity(timeout=0.1)`. Assert the
    observation ran twice and `_has_pending_messages` was not called (the
    tail found the deadline passed and returned). Mutation: reusing a
    deadline computed before the observation read in the tail makes the
    native branch (1216) scan readiness.
- Verify: R1 and R2 pass; G1–G3 and R7 pass;
  `./.venv/bin/python -m pytest tests/tasks/test_multiqueue_watcher.py tests/tasks/test_heartbeat.py -q`
  and the same under `./bin/pytest-pg tests/tasks/test_multiqueue_watcher.py tests/tasks/test_heartbeat.py`.
  Apply each named mutation by hand, run its test, record the failure line,
  revert by Edit (never `git checkout` on a shared tree).
- Stop if: any existing test in these two files fails. A failure means some
  observation source reaches the wait without a hint, which falsifies the
  hint analysis: record it and stop; do not widen the gate by guesswork.

### Task 3. Withdrawn

The retry flag for a failed observation read, its `_defer_pending_observation`
method, the three overrider `except`-block lines, and tests R3, R4, R8 and R9
were declined under the owner's ruling of 2026-11-04 (`## Owner Decisions`
item 2): "If those underlying reads fail, do nothing is the correct
response". `weft/core/manager.py` and `weft/core/tasks/heartbeat.py` are not
edited. The task number is kept so round-1 findings F5 and the register
still resolve.

### Task 4. Guards: the Manager's observations still fire (no edit)

Re-run, unchanged, on SQLite and with `./bin/pytest-pg`:

- Startup feedback [MA-1.8]:
  `tests/core/test_manager_family_maintenance.py::test_positive_startup_snapshot_wakes_admission_without_maintenance_tick`
  (all five parametrizations; a child process publishes after the
  observer is established, so on SQLite it rides a data-version hint),
  `::test_startup_handoff_reactivates_pending_public_queue`,
  `::test_startup_window_overlaps_but_bounds_unobserved_processes`;
  `tests/core/test_admission_recovery.py::test_launch_failure_releases_only_failed_startup_and_keeps_sibling_custody`;
  `tests/core/test_manager.py::test_manager_local_notification_wakes_unbounded_shared_wait`;
  PostgreSQL only: `tests/system/test_measured_admission_pg.py::test_startup_pressure_waits_without_hot_loop_and_reopens_on_fresh_usage`.
- System-STOP drain targets [MA-3] (plan 4):
  `tests/core/test_manager.py::test_manager_stop_orders_user_work_then_services_then_manager`,
  `::test_manager_stop_skips_terminal_and_internal_adopted_snapshots`,
  `::test_manager_stop_keeps_target_whose_identity_probe_fails`,
  `::test_manager_stop_releases_target_on_terminal_log_event`,
  `::test_manager_stop_releases_target_on_terminal_event_before_collection`
  (`test_manager_stop_keeps_terminal_proof_across_a_failed_log_page` was
  removed in `c857e10b` as read-failure armor and is no longer a target);
  `tests/cli/test_cli_manager.py::test_manager_stop_reaches_earlier_manager_detached_task`,
  `::test_manager_stop_reaches_task_under_another_live_manager`.
- Heartbeat: all of `tests/tasks/test_heartbeat.py`, and
  `tests/core/test_manager.py::test_manager_never_reads_or_deletes_heartbeat_registrations`
  (slow).
- Command: `./.venv/bin/python -m pytest -m "" <the node IDs above> -q`.
- Stop if: any fails. A failure here means a Manager observation arrives
  without a hint; record which, and stop.

### Task 5. Spec, docstrings, CHANGELOG (same commit as Tasks 1–4)

- Apply the three `## Proposed Spec Delta` blocks verbatim.
- `CHANGELOG.md` `## [Unreleased]` / `### Changed`, one bullet: "A task's
  reactor wait no longer reads its notification-only observation queues on
  every idle strategy return: on persistent SQLite it reads them only at wait
  entry and on returns that carry a local, native or data-version hint or
  reach a deadline, so the always-on heartbeat service with nothing to do
  makes no broker reads. PostgreSQL native-waiter returns and the degraded
  and nonpersistent fallbacks keep their checks."
- Docstrings: `_activity_hint_action` and `_wait_for_activity_body` carry
  `Spec:` lines ([CC-2.1], [QUEUE.7]).
  The module docstring of `weft/core/tasks/multiqueue_watcher.py` already
  cites [CC-2.1]; check and leave it.
- `docs/plans/README.md`: this plan's row (`draft` → `promoted` at the landing
  commit); this plan's `Status:` likewise.
- Verify: `./bin/check-doc-paths`;
  `./.venv/bin/python -m pytest tests/specs -q` (includes
  `tests/specs/test_plan_metadata.py`); the Replacement blocks are verbatim
  substrings of their targets (`grep -F` on two distinctive lines of each).

### Task 6. Measure after, full gates, closure

- Repeat Task 0's four measurements at the landing commit, same scripts.
  Record a before/after table per backend and fill the difference register's
  "After" column with the numbers.
- Stop if (latency; measured with a working detector and callback, the
  only case the one-return bound covers): for SQLite, the maximum number of strategy returns
  between a write and its observation exceeds the before maximum by more than
  one, or the median time exceeds the before median by more than one
  strategy pass (`MAX_INTERVAL`, 0.1 s). For PostgreSQL, any change in reads,
  returns or latency beyond noise (the design promises none there). A stop
  is reported to the series lead with the numbers; no tuning.
- Full gates (`## Verification`).
- Plan 6 cross-reference: append one line to the Execution log of
  `docs/plans/2026-11-02-task-monitor-heartbeat-clock-plan.md` naming this
  plan and its landing commit as the fix for its scoped round 2 F1, if that
  plan is still open; otherwise record it only here.
- Closure to `completed`, all four required ([WT-7] at class-5 completion,
  `docs/program-theory.md` lines 966–977):
  1. The [WT-7] keyed possession probe: the owner writes the key first; an
     agent of a different family that did not author this plan answers from
     `docs/program-theory.md` alone; the key and the result go in
     `## Review Log`.
  2. The theory-versus-code audit table refresh: carry the table of
     `docs/plans/2026-11-02-program-theory-reconciliation-plan.md`
     (`### Audit table`, line 5409) forward by reference, and re-audit the
     rows whose claims this plan touches, recording each row's evidence and
     classification in this plan's `## Review Log`: I-2 → [WT-2a.2] (line
     5430: queues stay the only truth; no new field holds evidence);
     I-6 → [WT-2a.6] (5434: one arbiter; no new wait or thread); 4.3-a "One
     reactor, one arbiter, a closed set of inputs" (5456: no new input, no
     cap, no new wake); 4.3-b "A quiet task does
     nothing" (5457: now also zero observation reads on persistent SQLite,
     with R2 as evidence); 4.4-e "Measure; do not bake in inefficiency"
     (5466: Task 6 numbers on both backends); 4.4-g "No capability
     diminution" (5468: this plan's register, rows 1–14). Every other row is
     carried forward unchanged by reference.
  3. The cross-family completed-work review.
  4. The index row and `Status:` flipped to `completed` in the same change.

## Testing Plan

- Harness: real broker queues through `broker_env` (`tests/conftest.py`
  369–390) and `WeftTestHarness`; a real `HeartbeatTask` driven by
  `run_until_stopped` on a thread for R2; real child processes in the Task 4
  Manager anchors.
- Backends: `tests/tasks/test_multiqueue_watcher.py`,
  `tests/tasks/test_heartbeat.py` and `tests/core/test_manager.py` run on
  both backends; R2 skips on PostgreSQL by design (item 1 (a): native-waiter
  returns keep their check); G1–G3, R1 and R7 run on both.
- Do not mock: queues, the broker connection, the heartbeat's table build,
  child processes. The strategy is stubbed only in R1, R7 and G3 to force a
  return shape that a real strategy produces nondeterministically (a
  hint-free return, a degraded return, a native-waiter return with no
  hint); every other test uses the real strategy. No broker-read fault is
  injected (G3's waiter-creation failure is a different seam): the owner
  ruled the read-failure paths out of scope (Owner Decisions item 2).
- Counting boundary: R2 counts at the broker class (the class that defines
  `peek_many` in a live connection's MRO), as plan 6's Manager test does:
  every call, in the counting process, of a `BrokerCore` method that reads
  the named queue (15 methods) or reads every queue (6 methods). The lists
  are the single lists in `tests/helpers/broker_reads.py`; Task 0 re-checks
  them against the installed `BrokerCore`.
- Timing: R2's 5 s window is the measured quantity, not a synchronization
  sleep. G2's 10 s bound is a liveness bound far above the expected
  milliseconds; its real assertion is the strategy-return count.
- Windows: no OS-specific behavior; the suites run on Linux, macOS and
  Windows in CI as usual.
- Tempting to skip: the nonpersistent and degraded paths (G2 `[False]`,
  G3) and the slow read at the deadline (R7). They are where a gate written
  too tightly would lose evidence or delay the owner.

## Verification

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/tasks/test_multiqueue_watcher.py tests/tasks/test_heartbeat.py -q
./bin/pytest-pg tests/tasks/test_multiqueue_watcher.py tests/tasks/test_heartbeat.py
./.venv/bin/python -m pytest -m "" tests/core/test_manager.py tests/core/test_manager_family_maintenance.py tests/core/test_admission_recovery.py tests/cli/test_cli_manager.py -q
./.venv/bin/python -m pytest tests/specs -q
./.venv/bin/python -m pytest
./.venv/bin/python -m pytest -m ""
./bin/pytest-pg --fast
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
./bin/check-doc-paths
```

The final gate is all of the above rerun from the current state, plus CI on
Linux, macOS and Windows for the landing commit, plus the [WT-7] keyed
possession probe for `completed`.

Runtime observation after deploy: start a manager in a fresh SQLite project
(`weft manager start`), find the heartbeat child's PID, and sample its
`num_ctx_switches().voluntary` and `cpu_times()` 30 s apart with nothing
registered (Task 0 item 1 method). Expect fewer voluntary context switches
than at the baseline and no change on PostgreSQL.

## Rollback

Revert the landing commit. No queue shape, payload, persisted format,
constant or configuration changes, so rollout order does not matter and no
data migration exists. The [CC-2.1] text and the code revert together.

## Out of Scope

- PostgreSQL native-waiter returns (Owner Decisions item 1 (b) and (c)).
- SimpleBroker's SQLite polling cadence and `PRAGMA data_version` checks:
  the strategy's input mechanism on SQLite ([WT-2] item 4, [ALT-WT-002]);
  this plan removes Weft's reads, not the strategy's wakes.
- What each overrider reads per check (for example the heartbeat's peek of
  every row ID when its table is non-empty) and the Manager's startup window
  and drain clocks.
- `weft/core/queue_wait.py` (the command-side waiter) and Taut's watcher copy.
- Any handling of a database that is no longer readable (a failed
  `PRAGMA data_version`, `refresh_last_ts()` or observation read): the
  owner ruled "do nothing" (Owner Decisions item 2). SimpleBroker's own
  behavior on such failures is unchanged: a raised detector failure counts
  toward its tenth-failure `RuntimeError`, while a suppressed SQLite error
  returns `None` and is not counted (`## Context`, lines 310–316).
- Plan 6's closure.

## Revision Log

| Date | Change | Reason |
|------|--------|--------|
| 2026-11-03 | Round 1 (codex 0.161.0, 2026-11-03): F1–F8 accepted and applied; see Review Log | Cross-family plan review round 1 |
| 2026-11-04 | Owner ruling on the underlying reads: F1's flag-first callback, F2's reporting strategy subclass and the round-0 retry flag (Task 3) declined; the SimpleBroker upstream item declined; R3–R6, R8, R9 removed; register rows 9, 10, 14 rewritten and 15, 16 removed; [CC-2.1] delta shortened; Manager and heartbeat files out of the edit set | "Neither"; "Refesh last ts is also not going to fail on a reasonable sqlite"; "If those underlying reads fail, do nothing is the correct response" (Owner Decisions item 2) |
| 2026-11-04 | `3c7c6add` | Task 2 note, Task 3, Owner Decisions, Review Log, Revision Log | Attribution corrected: the owner ruled on the underlying reads; the author declined the round-1 remedies under those rulings. Wording only. | No. |
| 2026-11-04 | `734a89c9` | [CC-2.1] delta, `## Out of Scope`, hint table, register row 14, Tasks 0, 1, 4, Testing Plan, Review Log | Round-2 F9–F11 and four observations applied; wording and one removed test target; no design change. | No. |

## Review Log

Draft, 2026-11-03; cross-family round 1: blocker F1–F3, F4–F8; all accepted and applied in the 2026-11-03 revision. 2026-11-04: the owner ruled on the underlying reads, and the author declined the F1 and F2 remedies and the Task 3 retry flag under that ruling (Owner Decisions item 2); F1 and F2 stand as accurate observations of SimpleBroker whose remedies are declined because the failure they describe is not a reachable state on a readable database. Scoped round 2 done; see the entry below.

- Cross-family scoped round 2 (codex 0.161.0, 2026-11-04, the plan at
  `734a89c9`, unit = the delta after the declines): verdict `no blocker`.
  F9 (P3): "not retried" overstated the change and the tenth-failure
  `RuntimeError` is not a general exit (suppressed SQLite errors return
  `None` uncounted); fixed in plan, [CC-2.1] delta and `## Out of Scope`.
  F10 (P3): the injected-strategy claim is proved only for the three
  repository subclasses, which inherit `PollingStrategy.wait_for_activity`;
  fixed in plan, hint table and register row 14, claim limited. F11 (nit):
  stray "on" in the delta; fixed. Observations applied: Task 4 named
  `test_manager_stop_keeps_terminal_proof_across_a_failed_log_page`,
  removed in `c857e10b`, now noted as no longer a target; the
  `test_manager.py` assertion now selects the two keys it asserted before
  rather than equating the three-key helper result; Task 0 measurement 3
  uses a distinct heartbeat ID per sample; G2's deliberate non-alignment of
  the write with a detector check is stated with the bound that covers it;
  "no fault is injected" narrowed to broker-read faults. Round 2 confirmed:
  no working-detector path observes a publication later than one strategy
  return; the three overriders keep their contracts and the override
  inventory is complete; R1 and R2 are genuine RED, G1–G3 and R7 are
  genuine guards; both quoted [CC-2.1] spans match verbatim and the
  `## Related Plans` anchor is now at line 1314 (bullet 1318); no operative
  step still assumes a declined remedy; class 5 with hardening stands.
  Review before implementation is complete.

- Cross-family round 1 (codex 0.161.0, 2026-11-03, plan text at `b85b798b`):
  BLOCKED on F1 (P1: SimpleBroker advances its data-version baseline before
  the change callback and swallows its errors, and Weft set its hint after
  the fallible inherited `refresh_last_ts()`, so one refresh failure consumed
  the only hint): applied as the flag-first `_on_data_version_change`, guard
  R5, hint-table row and register row 15. F2 (P2: SQLite's `get_data_version`
  helper suppresses PRAGMA errors to `None`, which the strategy treats as
  unchanged without counting a failure): applied as detector uncertainty, a
  Weft reporting `PollingStrategy` subclass through the documented
  `_create_strategy` hook and the public `start()` provider argument (no
  polling cap, no private field read), an open gate for injected strategies,
  guard R6, register rows 14 and 16, the one-return bound restricted to a
  working detector and callback, and the SimpleBroker upstream item (Owner
  Decisions item 3, not gating). F3 (P2: a deadline computed before a slow
  observation read changed the tail's readiness decision): applied, the
  tail's deadline stays computed after the read, guard R7, the [CC-2.1]
  Replacement's readiness clause qualified. F4 (G2's nonpersistent mutation
  could pass on the baseline callback's hint): G2 now takes and consumes the
  baseline before arming the publication. F5 (no firing test for either
  Manager retry line): R8 (startup feedback) and R9 (drain target) added;
  register row 10's proof corrected. F6 (the registration-membership
  assertion was dropped): kept in G1 and G2, with G1 asserting registration
  at the first observation read across an interest change. F7 (the read list
  has 14 methods and omits `queue_exists`): corrected to 15 named-queue
  methods plus 6 whole-database methods, with the counter's claim narrowed to
  what it counts. F8 (closure lacked the theory-versus-code audit refresh):
  Task 6 closure now carries the audit table forward by reference and
  re-audits rows I-2, I-6, 4.3-a, 4.3-b, 4.4-e and 4.4-g. The round
  confirmed the design direction, class, hardening claim, overrider list,
  theory consistency, the deferred-read flag (not an [ALT-WT-010] cache), the
  PostgreSQL decision (keep the native-return checks), the final-chunk bound
  for a working detector and the own-commit premise.

## Execution log

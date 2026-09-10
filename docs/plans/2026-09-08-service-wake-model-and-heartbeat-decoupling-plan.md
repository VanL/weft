# Service Wake Model and Heartbeat Decoupling Plan

Status: draft
Source specs: docs/specifications/01-Core_Components.md [CC-2.5], [CC-2.3]; docs/specifications/05-Message_Flow_and_State.md [MF-3.2], [MF-5]; docs/specifications/03-Manager_Architecture.md [MA-1]; docs/specifications/07-System_Invariants.md [MANAGER.15], [MANAGER.16], [MANAGER.18], [IMPL.10] (constraints, unchanged); docs/specifications/00-Quick_Reference.md (environment table)
Superseded by: none

Class: 5 — removes the normative [MF-3.2]/[MF-5] text that makes the
heartbeat service the TaskMonitor's clock, rewrites [MA-1] item 7's
heartbeat-desire sentence, adds one normative wake-model paragraph to
[CC-2.5], and changes the `WEFT_TASK_MONITOR_INTERVAL_SECONDS` validation
contract. Risky triggers fire (manager scheduling of a built-in service; a
public config contract and the TaskMonitor PONG payload change), so the
hardening-plans checklist applies and review precedes implementation. Plan
type: implementation with spec revision. Promotion strategy: **A** for
[MF-3.2], [MF-5], [MA-1] item 7, [CC-2.5], and [CC-2.3]; **D** for the
Quick Reference row. Origin: owner decision (Van, 2026-09-08), quoted in §2.
Revision 1 (2026-09-08, after round-1 review): launch model **L2** is the
recommended default; the on-demand launch model (L3) is out of scope and
named as a follow-up plan (§9); the cycle cadence is grid-anchored (§Proposed
Spec Delta, [MF-5]).
Revision 2 (2026-09-08, after round-2 review): launch model **L2f** (L2
plus the launch-policy flag `WEFT_HEARTBEAT_SERVICE_ENABLED`, default true)
is the recommended default, which adds one public config key to the
contract; the cadence rule consumes the grid tick at cycle start and carries
an overrun follow-up flag across chained re-arms; the test-fleet flip
inventory was measured by applying L2 and L2f in a scratch worktree; the
process-residue gate is PID-scoped through the harness instead of `pgrep`.

## 1. Goal

At `178e3a34` the supervised `TaskMonitor` registers a heartbeat with the
built-in heartbeat service and receives a queue message every
`WEFT_TASK_MONITOR_INTERVAL_SECONDS` on its own inbox, while *also* keeping
an unconditional local deadline that fires the cycle regardless
(`weft/core/monitor/task_monitor.py:1589-1593`). The heartbeat is therefore
not the monitor's clock; it is a second clock. Its two distinct effects are
(a) the periodic cadence stays on a fixed grid anchored at registration
(`weft/core/tasks/heartbeat.py:381-390` advances `next_due_at` by whole
intervals and never resets it from an emission), so an ad hoc inbox wake
does not move the next periodic cycle; and (b) a heartbeat landing during a
long cycle produces exactly one immediate follow-up cycle (the in-flight
early return at `:1572-1586` leaves `_wake_requested` set). To keep that
registration alive the monitor blocks its reactor thread on
`ensure_heartbeat_service()` (up to 0.5 s, retried every 1.0 s while the
service is down), the manager desires the heartbeat service whenever
TaskMonitor *or* LivenessMonitor is enabled (`weft/core/manager.py:5855-5859`;
LivenessMonitor never registers anything), and 113 lines in
`tests/tasks/test_task_monitor.py` monkeypatch `upsert_heartbeat` to keep
the monitor testable.

This plan makes the wake model explicit and single-owner:

- `BaseTask` owns the one wait path (`next_wait_timeout()` hook plus
  `MultiQueueWatcher` inside `wait_for_activity()`); every persistent
  service computes its own deadline locally; no service uses another
  service's queue message as its clock.
- `TaskMonitor` drops heartbeat registration entirely and reproduces both
  heartbeat semantics locally: a fixed periodic grid anchored at its first
  cycle start that ad hoc wakes do not move, and exactly one immediate
  follow-up after a cycle that overran a grid tick. One method owns the
  deadline arithmetic; a matrix test pins it.
- The heartbeat service stays as a specified capability for external
  registrants under launch model **L2f**: it is a manager-desired `ensure`
  service on every canonical public-request manager while
  `WEFT_HEARTBEAT_SERVICE_ENABLED` is true (the shipping default), gated
  exactly like `WEFT_LIVENESS_MONITOR_ENABLED` (launch policy, not
  active-stop authority), and is no longer described or gated as a
  dependency of any internal service. With the default this means one
  always-on process even with zero registrants and even with both built-in
  monitors disabled; it reverses the 2026-04-17 plan's "auto-start on first
  registration, not an always-on empty daemon" intent (history, not spec —
  [MF-3.2] never promised lazy start). Pure **L2** (no flag) remains fully
  specified as the alternative in task 5; the owner decides in §11 Q1.

Falsified premise, surfaced for the owner (§11 Q1): the 2026-04-17 plan's
"auto-start on first registration" was never implemented as a launch path.
`ensure_heartbeat_service()` (`weft/core/heartbeat.py:193-219`) only calls
`manager_runtime.ensure_manager()` and polls for an endpoint the manager
launched *because it desired the service* (unconditionally from `c3638182`
until `a27e7dc7` gated it on TaskMonitor/LivenessMonitor). With zero desire
the helper times out. Removing the TaskMonitor dependency therefore forces
either an always-desired service (L2/L2f, this plan) or a real on-demand launch
path (L3, follow-up plan in §9 — round-1 review found five unresolved
correctness questions in the L3 draft).

## 2. Source Documents

Owner decision (Van, 2026-09-08): "each ServiceTask can manage its own
wakeups, the shared code is in ServiceTask/BaseTask, and the heartbeat
service is there for external consumption." Concretely: BaseTask owns the
single wait path; each ServiceTask supplies its own deadline; the heartbeat
service is an interval emitter for queue consumers, not the reactor's
clock; TaskMonitor's registration is removed; the manager stops desiring
heartbeat *for* TaskMonitor/LivenessMonitor; keep the heartbeat service;
the 60 s floor on `WEFT_TASK_MONITOR_INTERVAL_SECONDS` is unmotivated
(owner question).

Governing spec text at `178e3a34` (quoted in full under Spec Baseline):

- `docs/specifications/01-Core_Components.md` [CC-2.5] (:668-706) — the
  execution flow and the `_Implementation mapping_` (:685-706) that already
  says "BaseTask alone evaluates terminal state, pending worker activity,
  `next_wait_timeout()`, queue/local activity waiting, and finalization."
  [CC-2.3] (:484-511) — TaskMonitor "owns task-local control, heartbeat
  registration, scheduling"; the launcher "sleeps until heartbeat/local due
  time". [CC-2.3] :531-537 (`HeartbeatTask` description) is **unchanged**
  under L2/L2f.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-3.2] (:330-405;
  mapping :397-400) and [MF-5] (:427-870, flow line :435, bullet :594-596,
  mapping :842-865, and :1442-1444).
- `docs/specifications/03-Manager_Architecture.md` [MA-1] item 7
  (:232-262; the sentence at :245-246; the "enqueues services through its
  own inbox" clause at :257-258 stays true under L2/L2f and is unchanged).
- `docs/specifications/07-System_Invariants.md` [MANAGER.15] (:809-835),
  [MANAGER.16] (:836-845), [MANAGER.18] (:852-862), [IMPL.10] (:636) — all
  unchanged; constraints only.
- `docs/specifications/00-Quick_Reference.md:188` (the interval row) and
  `:183` (admission reserve, unchanged).
- `docs/specifications/10-CLI_Interface.md` [CLI-1.2.1] :390 — **no
  change** under L2/L2f (the round-0 `idle`/`on-demand` vocabulary is gone with
  L3).

Historical plans (non-normative, cited as evidence of decisions only):

- [2026-04-17-heartbeat-service-plan.md](./2026-04-17-heartbeat-service-plan.md)
  (completed) :35-36: "The service should auto-start on first registration
  rather than running as an always-on empty daemon"; the landed slice
  implemented the wait-for-endpoint helper but no launch path. L2 reverses
  this intent knowingly.
- [2026-05-07-phase-7-task-monitor-supervision-and-cleanup-plan.md](./2026-05-07-phase-7-task-monitor-supervision-and-cleanup-plan.md)
  (completed) :531-535 and :1067-1070: heartbeat wake plus bounded local
  fallback — the design this plan retires.
- Commit `a27e7dc7` (2026-08-31, "Add liveness monitor and centralize
  mapping custody"): added the LivenessMonitor clause to the manager's
  heartbeat gate and the "desired only when an enabled internal dependent
  needs it" sentence to [MA-1] item 7. Commit `c3638182` (2026-05-08):
  unconditional heartbeat desire.

Repository guidance the implementer must apply: `CLAUDE.md` §1.1 and §4;
`docs/agent-context/engineering-principles.md` §4 (real broker/process
tests), §4.1 (failing test first), §4.2 (update all consumers), §9
(enumerable contracts get gates), §12 (coalesce on events);
`docs/agent-context/runbooks/writing-plans.md` (promotion strategies A/D,
traceability reconciliation, backstitch gate);
`docs/agent-context/runbooks/hardening-plans.md`.

External consumers checked (owner rule: zero in-repo callers is not a
deletion argument): `weft/core/heartbeat.py` is private per
`docs/specifications/14-Python_API_Surfaces.md` [PY-1] (only
`weft.client`, `weft.ext`, `weft.commands` are public; none export a
heartbeat name). `grep -rni heartbeat ../engram` finds only engram's own
gstack e2e heartbeat file and two engram plans that explicitly decline a
weft heartbeat dependency. `../simplebroker` has no weft heartbeat use. So
the only registrant anywhere is `task_monitor.py:4762`. The service is
kept because it is a specified capability, not because a consumer exists.

## 3. Context and Key Files

### Current structure (what exists today)

**Shared wait path.** `weft/core/tasks/base.py::BaseTask.next_wait_timeout`
(:1236-1247, default `None`; docstring already states the contract:
"Persistent tasks with their own due timers can override this hook so both
the launcher loop and `run_until_stopped()` use the same reactive
contract"). `run_until_stopped` (:1106; the loop at :1176-1182 calls
`next_wait_timeout()` and passes it to `wait_for_activity`).
`wait_for_activity` (:1376) and `_wait_for_reactor_activity` (:1410-1435)
hand the timeout to `MultiQueueWatcher`, capped by
`TASK_REACTOR_WAKEUP_MAX_SECONDS` while worker lanes are active.

**Services already on that model.** `HeartbeatTask.next_wait_timeout`
(`weft/core/tasks/heartbeat.py:479-496`) folds its due heap, idle timeout,
and `HEARTBEAT_ACTIVITY_WAIT_CAP_SECONDS`; `_process_reactor_turn` :143;
`_reschedule_registration` :381-394 keeps each registration on its
registration-anchored grid (`while next_due_at <= now: next_due_at +=
interval`). `LivenessMonitor.next_wait_timeout`
(`weft/core/tasks/liveness_monitor.py:468-479`) folds its due heap,
full-reconcile deadline, and `TASK_REACTOR_WAKEUP_MAX_SECONDS`;
`_process_reactor_turn` :142-155. The file has zero heartbeat references.

**TaskMonitor (the one outlier).** `weft/core/monitor/task_monitor.py`:
imports :47, :50 (kept; used by :3524-3527), :65
(`TASK_MONITOR_HEARTBEAT_STARTUP_TIMEOUT_SECONDS`), :90 (`from
weft.core.heartbeat import cancel_heartbeat, upsert_heartbeat`); init
fields :742-745 (`_heartbeat_registered`, `_heartbeat_error`,
`_heartbeat_id`, `_next_heartbeat_registration_attempt_monotonic`) and
:746 (`_next_cycle_due_monotonic = 0.0`); `next_wait_timeout` :1489-1512
(docstring :1492 says "heartbeat-driven schedule"; the body never consults
heartbeat state; the return is capped at
`TASK_MONITOR_ACTIVITY_WAIT_CAP_SECONDS` = 1.0 s at :1512);
`_process_reactor_turn` :1545-1601 — `_ensure_heartbeat_registered()`
every enabled turn :1568, early return while work is in flight :1572-1586
(leaves `_wake_requested` set),
`should_run = _first_cycle_pending or _wake_requested or now >= _next_cycle_due_monotonic`
:1589-1593; `_handle_work_message` :1603-1609 sets `_wake_requested` for
any inbox message; `_handle_control_command` :1612-1619 exists only to
call `_cancel_heartbeat()` on STOP/KILL; PONG extension provider
`_task_monitor_pong_extension` :1743-1800 with
`next_registration_attempt` :1747-1750 and the `heartbeat` block
:1778-1783; `_ensure_heartbeat_registered` :4755-4783 (blocks the reactor
thread inside `upsert_heartbeat`; on failure records `_heartbeat_error`
into `_last_error` and retries after `TASK_MONITOR_ACTIVITY_WAIT_CAP_SECONDS`);
`_cancel_heartbeat` :4785-4793; `_run_monitor_cycle` :4811 (cycle start
for both built-in worker and custom-processor modes);
`_finish_monitor_cycle` :5043 with `_last_error = self._heartbeat_error`
at :5062 and the deadline re-arm at :5069-5076;
`_handle_control_cleanup_worker_result` :5357 with `_last_error =
self._heartbeat_error` at :5445 and the second identical re-arm at
:5455-5462. Both re-arms are **finish-anchored** (`time.monotonic() +
next_interval_seconds`); the grid today comes only from the heartbeat.
Lines :3522-3527 classify heartbeat-service task-log families for cleanup
and are **not** registration code; they stay.

**Heartbeat helper (unchanged by this plan).** `weft/core/heartbeat.py`:
`_write_heartbeat_request` :44-55; `ensure_heartbeat_service` :193-219
(resolve endpoint → if live return → `manager_runtime.ensure_manager`
→ poll every `MANAGER_REGISTRY_POLL_INTERVAL` until `startup_timeout`,
default `MANAGER_STARTUP_TIMEOUT_SECONDS` = 10.0 s → `RuntimeError`);
`upsert_heartbeat` :221-244; `cancel_heartbeat` :246-261; `__all__` :264.
Under L2 the helper's contract is satisfied exactly as today: the manager
it ensures always desires the service.

**Manager.** `weft/core/manager.py`: `__init__` (:368) reads the enable
flags at :446-451 and then calls `_reconcile_managed_services(force=True)`
at :495 **during construction**, so every desired-but-absent internal
service is enqueued onto the real internal spawn queue before the
constructor returns (at HEAD, with both monitors disabled, reconcile
returns at :5869 because nothing is desired; under L2 the heartbeat request
is written at construction — this is the mechanism behind every test-fleet
flip listed below); `_service_supervision_allowed` :4701-4704 (only
draining/stop — no test-mode suppression); `_build_heartbeat_spawn_payload` :4716-4750
(manager-owned envelope; `heartbeat_idle_timeout: 0.0` disables
`HeartbeatTask` idle exit, `weft/core/tasks/heartbeat.py:116-121` — kept);
`_heartbeat_service_spec` :4819; `_reconcile_managed_services` :5842 with
the heartbeat gate and its comment at :5855-5859 (the whole block is
already inside `if include_internal and self._queue_names["inbox"] ==
WEFT_SPAWN_REQUESTS_QUEUE:` at :5854, so "canonical public manager" is
the existing scope); `_managed_service_convergence_active_reasons` :6415
with the heartbeat `missing_active_tid` arm at :6445-6452;
`_run_managed_service_convergence` :6593 and :6621 (drains pending
internal work independently of what reconcile enqueued);
`process_once` treats active convergence as activity (:6902-6908).

**Status surface.** `weft/commands/system.py::_service_enabled` :1073-1084
mirrors the manager gate at :1080-1081; `_service_snapshot_from_evidence`
:1128-1170 renders `disabled`/`config-disabled` when not enabled, else
`unknown`/`none` or evidence; `desired = bool(active_managers)` for every
key at :1267. No status vocabulary changes under L2/L2f (`config-disabled` already exists).

**Constants** (`weft/_constants.py`): `TASK_REACTOR_WAKEUP_MAX_SECONDS`
:589 (0.05); `MANAGER_STARTUP_TIMEOUT_SECONDS` :737 (10.0);
`WEFT_TASK_MONITOR_INTERVAL_SECONDS_DEFAULT` :1013-1014 (docstring
"Default heartbeat wake interval");
`WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS_DEFAULT` :1016 (2.0); the
worker-snapshot field ledger `_WORKER_SNAPSHOT_EXPECTED_FIELDS` :1178 with
`_heartbeat_error _heartbeat_id _heartbeat_registered` at :1192 and
`_next_heartbeat_registration_attempt_monotonic` at :1222 (pinned by
`tests/tasks/test_task_monitor.py:286-294`);
`TASK_MONITOR_ACTIVITY_WAIT_CAP_SECONDS` :1386 (1.0);
`TASK_MONITOR_HEARTBEAT_STARTUP_TIMEOUT_SECONDS` :1389-1390 (0.5);
`HEARTBEAT_MIN_INTERVAL_SECONDS` :1692 (60); `HEARTBEAT_IDLE_TIMEOUT_SECONDS`
:1695 (60.0); `_parse_task_monitor_interval_seconds` :2505-2513 (the 60 s
floor). The floor is enforced a second time in
`weft/core/monitor/runtime.py:221-225` (import :30). L2f wiring template
(the liveness flag): `LIVENESS_MONITOR_ENABLED_DEFAULT` :1010-1011; the
`load_config` loader entry :2915-2919 (`_load_weft_env_value(...,
parser=_parse_bool)`); `_parse_bool` :2403; the explicit-override rule in
`_WEFT_OVERRIDE_RULES` :3223-3226 (`_OverrideKind.BOOLISH`). A key present
in the loader but absent from `_WEFT_OVERRIDE_RULES` fails
`tests/system/test_constants.py:106`
(`test_env_loader_and_explicit_override_normalizer_keys_stay_in_parity`),
and `load_config({"KEY": "0"})` would keep the string `"0"`, which
`bool("0")` reads as true — so the rule is part of the flag, not an
option.

**Tests that pin today's behavior.** `tests/tasks/test_task_monitor.py`:
113 lines mention `upsert_heartbeat`, each a `monkeypatch.setattr(
task_monitor_mod, "upsert_heartbeat", ...)` site (first :1571; the
`fail_heartbeat` variant :8365); disabled-monitor "must not heartbeat"
guard :1805-1809; PONG `extended["heartbeat"]` assertion :1905-1910;
`test_task_monitor_heartbeat_failure_records_health_but_still_cycles`
:8357-8390; wait-timeout tests :1727 and :1757; worker-snapshot parity
:286-294; fake-clock pattern :6788-6791 (`monkeypatch.setattr(
task_monitor_mod, "time", SimpleNamespace(monotonic=..., time_ns=...,
sleep=...))`); helpers `recording_processor` :1105,
`drive_task_monitor_until_idle` :1150, `drive_task_monitor_until_observed`
:1368, `_read_control_reply` :1397. The only `CONTROL_STOP` write in the
file (:11077) belongs to a Consumer test
(`test_persistent_consumer_resurrects_after_ambiguous_family_cleanup`
:10994); no test drives STOP into a waiting TaskMonitor.
`tests/core/test_manager.py`: `make_manager_spec` :298 defaults to the
canonical inbox (:300); the `manager_setup` fixture :839-851 uses a
non-canonical inbox (:841) and sets `manager._liveness_monitor_enabled =
False` at :848 (the per-test flag seam L2f copies for heartbeat);
`test_manager_enqueues_heartbeat_through_service_path` :1924-1956
(TaskMonitor enabled; one heartbeat payload; `heartbeat_idle_timeout ==
0.0` — stays green under L2 and L2f);
`test_manager_convergence_drains_pending_internal_spawn_work` :1703-1750
drains the internal queues (:1723-1724) and stubs
`_reconcile_managed_services` to a no-op (:1725), so **it does not flip**
under L2 (round-1 revision 1 claimed it did; corrected in round 2);
`[HEARTBEAT, TASK_MONITOR]` and `[HEARTBEAT]` enqueue assertions at
:4135-4137, :4455-4458, :4488, :5064, :5094-5097, :5142-5145, :5332-5335,
:5402-5405, :5437, :8438 (all stay green);
`test_managed_service_convergence_active_reasons_are_stable` :3362-3395
and `test_manager_idle_shutdown_waits_for_missing_internal_service`
:5489-5512 both key `missing_active_tid` on the heartbeat state with
TaskMonitor enabled (stay green and become the pin for "always desired");
liveness-only sets :3195-3230 and :4774-4802 assert only liveness
behavior; :5520 uses a non-canonical inbox.

**Measured L2 flip inventory** (the three-line L2 edit applied at
`178e3a34` in a scratch worktree, `tests/core/test_manager.py` run in full;
probe: `scratchpad/plans/06-rev2/test_manager_l2_failures.txt`). Exactly
nine tests (ten items) fail; every one constructs a canonical `Manager`
with both monitors disabled through `load_config({...})` and runs real
convergence, so the constructor's forced reconcile (:495) writes a
heartbeat request onto the real internal queue before the test installs
its recorder, and the internal lane then launches it (through the stubbed
`_launch_child_task`, or for real where none is stubbed):

| Test (`tests/core/test_manager.py`) | Line | Failing assertion under L2 | Subject |
|---|---|---|---|
| `test_manager_does_not_enqueue_task_monitor_when_disabled` | :1889 | `not any(... HEARTBEAT ...)` at :1912-1916 | the old desire gate itself |
| `test_manager_processes_internal_spawn_before_public_spawn` | :1958 | `launched == ["internal-first", "public-second"]` (:1999) | lane ordering |
| `test_manager_admission_retains_public_while_internal_can_launch` | :2388 | `launched == ["child"]` (:2426) | admission |
| `test_failed_child_launch_restores_source_and_retries_on_admission_deadline` | :2549 | `launched == []` / `["child"]` (:2603, :2608) | admission |
| `test_disabled_admission_dispatches_without_observing_backend` | :2736 | `launched == ["child"]` (:2774) | admission |
| `test_process_once_reconciles_internal_services_before_user_spawn_work` | :5148 | `order.index("user-work")` raises `ValueError` (:5240-5242): the pre-enqueued heartbeat request keeps the internal lane busy and the public queue is never reached in that turn ([MANAGER.16]) | reconcile ordering |
| `test_manager_public_dispatch_steals_work_when_registry_ownership_is_unproved[None]` and `[active_records1]` | :7859 | `launched == [str(message_id)]` (:7901) | public dispatch |
| `test_manager_does_not_probe_inactive_public_spawn_queue` | :8103 | `launched == []` (:8146) | public dispatch |
| `test_manager_pending_precheck_activates_public_spawn_queue` | :8151 | `launched == [str(message_id)]` (:8194) | public dispatch |

Round-2 candidates that do **not** flip (verified by the same run): :2437,
:2489, :2848, :2897, :2940, :5248, :8392. Under L2f, the same worktree with
the flag wired (constants, manager, status) and the nine tests fixed by the
pattern in task 3 item 7 runs `tests/core/test_manager.py`,
`tests/system/test_constants.py`, and `tests/commands/test_status.py`
fully green.

`tests/helpers/weft_harness.py`: `_patch_environment` :667-672 sets
`WEFT_TASK_MONITOR_ENABLED=0` and `WEFT_LIVENESS_MONITOR_ENABLED=0` for
every harness; `ensure_foreground_manager` :198-235 builds the canonical
manager spec (`build_manager_spec` :211) and runs `Manager` inline on a
serve thread (:228-243) whose `finally:` calls `manager.cleanup()`;
`_stop_inline_managers` :657-664 joins that thread for 2.0 s and calls
`cleanup()` again if it is still alive; `cleanup()` :620-651 then runs
`_collect_pid_mappings` :1126 (registers every host PID published in the
harness-scoped `weft.state.tid_mappings`, with create-time identity),
`_wait_for_registered_pids_to_exit` :1162 (returns the PIDs still alive
after up to `min(manager_timeout, 5.0)` s; the return value is discarded
today) and `_terminate_registered_pids` :1141 (force-kills them — which is
why a leaked child is invisible today). The `broker_env` fixture in
`tests/conftest.py:326` is built on the `weft_harness` fixture (:280), so
`_patch_environment` also applies to every `broker_env` test in
`tests/core/test_manager.py` — that is why :5148 and :5248 must set
`manager._task_monitor_enabled = True` by hand, and why a fleet-wide
`WEFT_HEARTBEAT_SERVICE_ENABLED=0` harness environment override would break
the 16 `test_manager.py` tests that expect heartbeat desire with TaskMonitor
enabled (measured: 16 failures with the override, 0 without); per-test
constructor overrides of the same key are the accepted seam. `ensure_foreground_manager` has 39 call sites in 7
files (`tests/cli/test_cli_run.py` 16, `tests/core/test_client.py` 12,
`tests/cli/test_cli_pipeline.py` 6, `tests/cli/test_cli_result_all.py` 2,
`tests/core/test_ops_shared.py`, `tests/cli/test_cli_long_session.py`,
`tests/cli/test_cli_list_task.py` 1 each); 25 test files import the
harness, and the CLI-level ones also autostart detached managers through
`weft run`. Measured cost of L2 on the harness-backed suites (`tests/system
tests/cli tests/commands` plus the six harness-using `tests/core` and
`tests/tasks` files, 12 xdist workers, this machine): HEAD 1 m 41.5 s wall
(user 4 m 41 s, sys 2 m 01 s) versus L2 1 m 48.5 s wall (user 5 m 20 s, sys
2 m 24 s) — about +7 s wall and +60 s CPU, i.e. the ~0.5-1.5 s × 39 estimate
is absorbed by parallelism; process residue after the L2 run: none (the
instrumented harness recorded no managed PID alive at forced-terminate
time). One CLI test,
`tests/cli/test_cli_run.py::test_parallel_manager_reuse_converges_to_single_manager_under_repeated_bootstrap`
(:2938), failed once in the parallel L2 run with phase
`manager_convergence_timeout` and passed 3/3 in isolation under L2 and at
HEAD; it is load-sensitive, not an assertion flip (task 5 sweep note).
`tests/commands/test_status.py` :584-631 (live/terminal heartbeat
duplicates), :634 (pending internal request), :433-446 (liveness `desired
False`/`unknown` with no manager); no test asserts `config-disabled` for
the heartbeat row. `tests/system/test_constants.py` :597-610
(`test_task_monitor_interval_rejects_below_heartbeat_minimum`; the file
imports the constant at :33 and uses it as a value at :531, :551);
`tests/core/test_task_monitoring.py` :13 import, :192/:207 value use, :313
(zero rejected). `tests/tasks/test_heartbeat.py` (599 lines; imports
`HEARTBEAT_MIN_INTERVAL_SECONDS` at :24 as a registration value) —
**untouched by this plan.**

### Files to modify

- `weft/core/monitor/task_monitor.py`
- `weft/core/manager.py` (two gate sites: :5855-5859, :6445-6452)
- `weft/commands/system.py` (:1080-1081)
- `weft/core/monitor/runtime.py`
- `weft/_constants.py`
- `tests/tasks/test_task_monitor.py`, `tests/core/test_manager.py`,
  `tests/commands/test_status.py`, `tests/system/test_constants.py`,
  `tests/core/test_task_monitoring.py`;
  `tests/helpers/weft_harness.py` (task 5: residue warning in `cleanup()`
  and the inline-manager join timeout)
- Specs per the delta table; `CHANGELOG.md`
- `README.md`: under L2f one bullet is added to the environment list after
  `:1323-1324` (`WEFT_LIVENESS_MONITOR_ENABLED`); otherwise **no change** —
  `:591` and `:1321` describe the admission reserve (unchanged,
  [MANAGER.18]); `:1298` is the manager *registry* heartbeat, unrelated.
  State this in the closeout.
- **Not modified:** `weft/core/heartbeat.py`, `weft/core/manager_services.py`,
  `weft/core/tasks/heartbeat.py`, `tests/tasks/test_heartbeat.py`,
  `tests/core/test_heartbeat_helpers.py`, `docs/specifications/07-*`,
  `docs/specifications/10-*`.

### Read first

`docs/specifications/01-Core_Components.md` [CC-2.5] and [CC-2.3];
`docs/specifications/05-Message_Flow_and_State.md` [MF-3.2], [MF-5];
`docs/specifications/03-Manager_Architecture.md` [MA-1] items 6-7;
`docs/specifications/07-System_Invariants.md` [IMPL.7]-[IMPL.10],
[MANAGER.15]-[MANAGER.18]; `weft/core/tasks/base.py:1106-1250` and
:1376-1435; `weft/core/tasks/heartbeat.py:96-130`, :381-394, :455-516;
`weft/core/tasks/liveness_monitor.py:135-185`, :455-480;
`weft/core/monitor/task_monitor.py` sections named above;
`weft/core/manager.py:4701-4750`, :5842-5900, :6415-6470, :6590-6630;
`weft/commands/system.py:1073-1170`; `tests/helpers/weft_harness.py:198-235`,
:655-700.

Comprehension check (answer before editing):

1. Which single TaskMonitor method decides how long the process sleeps, and
   which two sites re-arm the deadline it reads? (`next_wait_timeout`
   :1489; re-arms at :5076 and :5462, both finish-anchored today.)
2. Where does today's periodic *grid* come from, and what keeps it from
   moving when an inbox poke runs an extra cycle? (The heartbeat service's
   `_reschedule_registration` :381-394 advances the registration's
   `next_due_at` by whole intervals; the monitor's local re-arm is only a
   fallback that the next grid heartbeat pre-empts.)
3. What sets `_wake_requested`, and why does it survive a turn in which
   work is in flight? (`_handle_work_message` on any inbox message; the
   in-flight early return at :1572-1586 does not clear it, so a heartbeat
   arriving mid-cycle produces exactly one follow-up cycle — a boolean, so
   two missed ticks still yield one follow-up.)
4. Under L2, which existing code path starts the heartbeat for a first
   external registrant? (`ensure_heartbeat_service` → `ensure_manager` →
   the canonical manager's `_reconcile_managed_services` desires the
   `ensure` service unconditionally — unchanged from `c3638182` except
   that the gate at :5858 is gone.)

## 4. Invariants and Constraints

Must not change:

- [IMPL.10]: one drive-owning thread per reactor; `run_until_stopped` is
  the only process/wait/finalize loop. No task-specific poll loop, thread,
  or timer is added anywhere.
- [IMPL.8]/[IMPL.9]: the reactor thread commits all broker effects; the
  new deadline logic is pure in-memory bookkeeping on the reactor thread.
- [MANAGER.15]: the heartbeat service's pending-spawn, live, terminal, and
  uncertain evidence still reduce through `reduce_managed_service_state`;
  the manager remains the only author of internal spawn envelopes; no
  second convergence path. (L3's helper-authored envelope, which round-1
  review found to conflict with this invariant, is out of scope.)
- [MANAGER.16]: internal spawn work still drains before public work; the
  drain path is not modified.
- [MANAGER.18] and `_constants.py:783`: the admission reserve floor
  (`3 + int(liveness)`) keeps modeled room for Heartbeat — unchanged and
  now literally true in every canonical configuration.
- [MF-3.2] helper rules (:345-348 "helper startup ensures a manager exists
  and waits for a live manager-owned heartbeat endpoint" — still exact),
  emitter rules (coalescing, 60 s minimum for *registrations*, destination
  validation, singleton supersession), and every test in
  `tests/tasks/test_heartbeat.py` are untouched.
- TaskMonitor cleanup/collation behavior, PONG cleanup diagnostics
  ([OBS.13.11]), catch-up interval, and the maintenance deadline are
  unchanged; only the cycle-deadline arithmetic and the heartbeat fields
  move.
- Any message written to `T{monitor_tid}.inbox` still wakes a cycle
  (`_wake_requested`), so explicit pokes keep working — and, new, they no
  longer move the periodic grid (they never did while the heartbeat was
  live; they did under the fallback).
- No new queue names, control message types, TaskSpec fields, or status
  vocabulary. Under the Q1 default (L2f) exactly one env var,
  `WEFT_HEARTBEAT_SERVICE_ENABLED`, is added, wired through the same three
  places as `WEFT_LIVENESS_MONITOR_ENABLED` (default constant, loader
  entry, `_WEFT_OVERRIDE_RULES` boolish rule) with firing tests for the
  env path and the explicit-override path (task 3 item 7). Under pure L2
  no env var is added.
- Heartbeat registrations remain runtime-only; no failover of in-memory
  registrations is promised (unchanged from 2026-04-17).
- Manager idle shutdown semantics: a desired-but-missing heartbeat still
  counts as active convergence (`:6902-6908`;
  `test_manager_idle_shutdown_waits_for_missing_internal_service` :5489
  and `test_managed_service_convergence_active_reasons_are_stable` :3362
  stay green, unmodified).

Hidden couplings (named so the implementer does not find them mid-slice):

- The `_last_error` field is *reset to the heartbeat error* on every
  successful cycle (:5062, :5445). Removing the fields changes those two
  lines to `None`; the PONG `last_cycle.error` semantics otherwise stay.
- `_WORKER_SNAPSHOT_EXPECTED_FIELDS` is asserted equal to the instance
  field set (:286-294). Every removed or added TaskMonitor instance field
  must be mirrored in `_constants.py:1178-1284`.
- The desire gate is copied in three places (`manager.py:5858`, `:6451`,
  `system.py:1081`); all three change together in task 5.
- `Manager.__init__` reconciles once with `force=True` (`manager.py:495`)
  before returning. Any canonical `Manager` that desires the heartbeat at
  construction writes a real heartbeat spawn request to
  `weft.spawn.internal` before a test can install recorders or stubs. This
  is why the nine `test_manager.py` flips (§3 table) cannot be fixed by
  stubbing `_reconcile_managed_services` after construction, and why the
  per-test fix under L2f is a **config key** passed to the constructor
  (task 3 item 7), the same way those tests already pass the two monitor
  keys.
- `WeftTestHarness` disables both built-ins for every harness
  (`weft_harness.py:671-672`), its foreground manager is a canonical
  manager (`:211`, `:228`), and the `broker_env` fixture
  (`tests/conftest.py:326`) is harness-backed, so the same environment
  reaches every `broker_env` test. Consequences: (a) under L2 and under
  L2f-default-true every harness foreground manager, and every detached
  manager a CLI-level harness test starts, supervises a real heartbeat
  child (measured cost and residue in §3); (b) the harness must **not**
  set `WEFT_HEARTBEAT_SERVICE_ENABLED=0` as a fleet-wide environment
  override (per-test constructor overrides of the same key are fine) — measured: 16
  `test_manager.py` tests that expect heartbeat desire with TaskMonitor
  enabled fail with it and 0 fail without it. Task 5 carries a PID-scoped
  residue gate instead.
- Harness join/cleanup overlap (`weft_harness.py:657-664`):
  `_stop_inline_managers` joins the serve thread for 2.0 s while that
  thread's `finally: manager.cleanup()` runs `_terminate_children` under
  `TASK_CLEANUP_TIMEOUT_SECONDS` = 2.0 s (`_constants.py:586`;
  `base.py:1039-1050`). Under L2/L2f that window is always occupied by a
  heartbeat child, so the join can expire and the main thread calls
  `cleanup()` concurrently. The second call is not a double-terminate —
  `_request_stop_and_maybe_finalize` returns while the lifecycle is
  `FINALIZING`/`CLOSED` (`base.py:989-993`) — but the main thread then
  runs `_collect_pid_mappings`/`_wait_for_registered_pids_to_exit` while
  the serve thread is still terminating, which is exactly the window the
  residue gate observes. Task 5 raises the join above the cleanup deadline
  so the gate observes a finished cleanup.
- TaskMonitor chained re-arm (`task_monitor.py:5345-5355`, `:5454-5462`):
  on the built-in path `_handle_builtin_cycle_worker_result` calls
  `_finish_monitor_cycle` (first re-arm) and then may start the terminal
  control-cleanup worker, whose result calls the second re-arm. Today the
  overrun follow-up survives that chain only because `_wake_requested` is a
  sticky boolean that the in-flight early return (`:1583-1586`) does not
  clear. The local rule must therefore carry the owed follow-up as explicit
  state until a cycle actually starts (task 4 item 5), or the second re-arm
  overwrites `due = now` with the next grid tick (round-2 Codex F1;
  reproduced by simulation, §Testing Plan).
- The red tests written in task 3 must carry the existing
  `upsert_heartbeat` monkeypatch until task 4 removes the import: at HEAD
  an unpatched `TaskMonitor` calls `ensure_heartbeat_service` →
  `manager_runtime.ensure_manager`, which must never run from a unit test.

Error-path priorities: TaskMonitor cycle scheduling is core (fatal if
wrong); manager desire is core; status rendering is best-effort.

Rollback (stated before tasks): every code slice is independently
revertable with `git revert`; nothing persisted changes shape (heartbeat
registrations are runtime-only; no queue-name, TID-mapping, or Monitor-table
change). Mixed-version runtime (an old TaskMonitor process against a new
manager) is unaffected: the new manager still runs the heartbeat, so an old
monitor's registration succeeds. A new TaskMonitor against an old manager
simply never registers. No shim is added (0.9.99, owner rule). One-way
doors: none. Rollout order: none required; tasks are sequenced for
reviewability, not compatibility.

Review gates: independent review of plan and delta before promotion;
review before implementation (Class 5 with a risky trigger); no second
wait path; no mock of `TaskMonitor`, `Manager`, broker queues, or
`HeartbeatTask` where the real object runs in-process in under a second;
no drive-by refactor of TaskMonitor beyond the named sites.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Spec Baseline

- `178e3a34` — docs/specifications/01-Core_Components.md,
  05-Message_Flow_and_State.md, 03-Manager_Architecture.md,
  00-Quick_Reference.md at plan authoring time (2026-09-08). Plan type:
  implementation with spec revision. Promotion baseline identifier:
  recorded after task 2.

Exact baseline text being changed (verified at `178e3a34`):

[MF-3.2] :385-388:

> - the supervised `TaskMonitor` uses heartbeat registrations for periodic
>   wake messages to its own `T{tid}.inbox`; if registration is temporarily
>   unavailable, the monitor records operational health and falls back to its
>   bounded local interval

[MF-3.2] :389-391 (start of the PONG bullet):

> - the supervised `TaskMonitor` includes cached operational diagnostics in
>   PONG extension data under `extended.task_monitor`: configuration, heartbeat
>   registration state, scheduling state, and the previous cycle summary.

[MF-3.2] :397-400 mapping:

> _Implementation mapping_: `weft/core/heartbeat.py`;
> `weft/core/tasks/heartbeat.py`; `weft/core/manager.py`;
> `weft/core/manager_services.py`; `weft/core/endpoints.py`;
> `weft/core/monitor/task_monitor.py`.

[MF-5] :435: `TaskMonitor heartbeat wake -> T{monitor_tid}.inbox -> bounded processor cycle`

[MF-5] :594-596: "the reactor stays available for task-local
PING/STATUS/STOP/KILL, heartbeat registration, and schedule bookkeeping
while the worker scans"

[MF-5] :1442-1444: "the monitor uses `WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS`
for the next wake instead of the full heartbeat interval."

[MA-1] item 7 :245-246: "The built-in heartbeat service is desired only
when an enabled internal dependent needs it."

[CC-2.3] :487-489: "The persistent monitor is a reactor: it owns
task-local control, heartbeat registration, scheduling, and commits cached
diagnostics from worker results." and :509-511: "The launcher asks the
persistent monitor for its next wait timeout so the monitor sleeps until
heartbeat/local due time or task-local input instead of polling at the
default task-process interval."

[CC-2.5] :679-683 is the last paragraph before `_Implementation mapping_`
(:685); it contains no wake-model sentence.

Quick Reference :188: "| `WEFT_TASK_MONITOR_INTERVAL_SECONDS` | Heartbeat
wake interval for the supervised task monitor. Must be at least the
heartbeat minimum. |"

Quick Reference :187 (insertion anchor for the L2f row; unchanged text):
"| `WEFT_LIVENESS_MONITOR_ENABLED` | Whether the canonical manager
supervises the internal `LivenessMonitor`. Defaults to true. Disabling it
also removes its modeled admission-reserve slot; TID mappings then persist
because no other component may delete them. |"

[MA-1] item 7 :247-250 (unchanged; the L2f sentence mirrors it): "The
internal `TaskMonitor` is an `ensure` service when
`WEFT_TASK_MONITOR_ENABLED` is true. `WEFT_LIVENESS_MONITOR_ENABLED`
independently makes LivenessMonitor desired whether or not TaskMonitor is
enabled. The liveness flag is launch policy, not active-stop authority."

## Proposed Spec Delta

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| docs/specifications/05-Message_Flow_and_State.md | A | [MF-3.2] :385-388 delete; :389-391 replace opening; :397-400 mapping edit. [MF-5] :435 replace; :594-596 replace; new cadence bullet after :596; :1442-1444 replace |
| docs/specifications/03-Manager_Architecture.md | A | [MA-1] item 7 :245-246 replace one sentence |
| docs/specifications/01-Core_Components.md | A | [CC-2.5] insert one paragraph after :683; [CC-2.3] :487-489 and :509-511 reword |
| docs/specifications/00-Quick_Reference.md | D | :188 row replace; under L2f a new `WEFT_HEARTBEAT_SERVICE_ENABLED` row inserted after :187 (documentation of env vars; no code cites the rows) |

Strategy A text lands in task 2 **without** new `_Implementation mapping_`
claims. The reciprocal link claims, code, and `Spec:` backlinks land
together in the code slice that implements each section: task 4 for
[MF-5]/[CC-2.5]/[CC-2.3] (adds the [CC-2.5] mapping sentence and the
docstring backlinks), task 5 for [MA-1] item 7 (comment backlink; the
[MA-1.6a] mapping at 03:383 already lists `_reconcile_managed_services`)
and [MF-3.2] (mapping already lists `manager.py` and `system.py`). Between
task 2 and those slices no code cites the new text. The [MF-5] mapping at
05:842-865 already lists `weft/core/monitor/task_monitor.py`; no edit.

### [CC-2.5] — insert after the paragraph ending "…remain visible internal failures under [OBS.6a]." (:683), before `_Implementation mapping_` (:685)

> Wake model: every task, including every long-lived internal service,
> wakes through one path — `BaseTask.run_until_stopped()` asks the concrete
> task for `next_wait_timeout()` and hands that deadline to the shared
> `MultiQueueWatcher` wait in `wait_for_activity()`; each persistent
> service computes its own due time locally from its own state, and no
> service depends on a queue message from another service as its clock.
> The built-in heartbeat service is an interval emitter for queue
> consumers ([MF-3.2]), not a reactor clock.

### [CC-2.3] :487-489 — replace "The persistent monitor is a reactor: it owns task-local control, heartbeat registration, scheduling, and commits cached diagnostics from worker results."

> The persistent monitor is a reactor: it owns task-local control, its own
> cycle scheduling, and commits cached diagnostics from worker results.

### [CC-2.3] :509-511 — replace "The launcher asks the persistent monitor for its next wait timeout so the monitor sleeps until heartbeat/local due time or task-local input instead of polling at the default task-process interval."

> The launcher asks the persistent monitor for its next wait timeout so the
> monitor sleeps until its local cycle due time or task-local input instead
> of polling at the default task-process interval ([CC-2.5] wake model).

### [MF-3.2] :385-388 — delete the "the supervised `TaskMonitor` uses heartbeat registrations …" bullet entirely (no replacement). No built-in service registers a heartbeat.

### [MF-3.2] :389-391 — replace the opening of the PONG bullet

> - the supervised `TaskMonitor` includes cached operational diagnostics in
>   PONG extension data under `extended.task_monitor`: configuration,
>   scheduling state, and the previous cycle summary.

(The remainder of that bullet is unchanged.)

### [MF-3.2] :397-400 — in the `_Implementation mapping_` line, delete "`weft/core/monitor/task_monitor.py`." and end the sentence at "`weft/core/endpoints.py`." (Strategy A: no new claim; the removal reflects that the monitor no longer implements any [MF-3.2] rule.)

### [MF-5] :435 — replace the flow line

> TaskMonitor local cycle deadline or T{monitor_tid}.inbox wake message -> bounded processor cycle

### [MF-5] :594-596 — replace "the reactor stays available for task-local PING/STATUS/STOP/KILL, heartbeat registration, and schedule bookkeeping while the worker scans"

> the reactor stays available for task-local PING/STATUS/STOP/KILL and
> schedule bookkeeping while the worker scans

### [MF-5] — insert a new bullet immediately after the bullet that begins "when enabled, the canonical manager supervises one internal `TaskMonitor`." (the bullet containing :594-596)

> - the supervised `TaskMonitor` schedules its own cycles on a fixed
>   periodic grid: grid ticks are `first_cycle_start + k *
>   WEFT_TASK_MONITOR_INTERVAL_SECONDS` for `k >= 1`, anchored once at the
>   monitor's first cycle start and never re-anchored. A cycle runs at each
>   grid tick, when any message arrives on `T{monitor_tid}.inbox`, or when
>   catch-up is pending; a cycle started by an inbox message or by catch-up
>   does not move the grid. A cycle consumes, at its start, every grid tick
>   at or before that start. When a cycle's bookkeeping finishes, if one or
>   more grid ticks passed while it ran, the monitor owes exactly one
>   immediate follow-up cycle; the owed follow-up persists across any
>   further bookkeeping (including a runtime control-cleanup pass that
>   follows the cycle) until a cycle actually starts, and then the monitor
>   resumes on the next future grid tick. While catch-up is pending, the
>   next due time is the earlier of that grid-derived due time and `now +
>   WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS`. The monitor registers no
>   heartbeat and blocks on no other service to schedule itself.

### [MF-5] :1442-1444 — replace "for the next wake instead of the full heartbeat interval."

> for the next wake instead of the next grid tick.

### [MA-1] item 7 :245-246 — replace "The built-in heartbeat service is desired only when an enabled internal dependent needs it."

L2f (Q1 default):

> The built-in heartbeat service is an `ensure` service desired by every
> canonical public-request manager while `WEFT_HEARTBEAT_SERVICE_ENABLED`
> is true (the default), as the registration endpoint for heartbeat
> consumers ([MF-3.2]); it is not a dependency of any internal service, and
> no built-in service registers with it. Like the liveness flag, the
> heartbeat flag is launch policy, not active-stop authority.

Pure L2 (if the owner rejects the flag):

> The built-in heartbeat service is an `ensure` service desired by every
> canonical public-request manager as the registration endpoint for
> heartbeat consumers ([MF-3.2]); it is not a dependency of any internal
> service, and no built-in service registers with it.

### Quick Reference :188 — replace the row

> | `WEFT_TASK_MONITOR_INTERVAL_SECONDS` | Cycle grid interval for the supervised task monitor, in whole seconds; cycles run on a fixed grid anchored at the monitor's first cycle, with one immediate follow-up after a cycle that overran a grid tick. Must be a positive integer. Defaults to 300. |

(If the owner keeps a floor under §11 Q2, the last two sentences become
"Must be an integer of at least 60. Defaults to 300.")

### Quick Reference — under L2f, insert one row immediately after :187 (the `WEFT_LIVENESS_MONITOR_ENABLED` row)

> | `WEFT_HEARTBEAT_SERVICE_ENABLED` | Whether the canonical manager supervises the built-in heartbeat service, the registration endpoint for external heartbeat consumers. Defaults to true. Launch policy only: disabling it does not stop a running service and does not change the admission reserve. |

(`README.md` gets the matching bullet after `:1323-1324`: "`WEFT_HEARTBEAT_SERVICE_ENABLED` - Supervise the built-in heartbeat service (default: true)". Not a spec edit.)

## Spec-changing slice order

1. Plan (this document) with baseline, delta, invariants, tasks, empty
   deviation log.
2. Independent review of plan and delta (§8) — round 1 done (see Review
   Record); this revision re-enters review.
3. Spec-promotion slice (task 2): apply the delta per the table; add this
   plan to `## Related Plans` in 05 (:1525), 03 (:36), 01 (:36), and 00
   (:238), matching each section's existing entry format; record the
   promotion baseline identifier.
4. Code slices (tasks 3-6), each its own commit, against the promoted
   spec.
5. Deviation handling as needed.
6. Traceability reconciliation (task 7), including the backstitch gate
   before/after comparison.

## 5. Tasks

Dependency order: 1 → 2 → 3 → 4 → 5 → 6 → 7. Tasks 4, 5, and 6 are each
one commit and each independently revertable. Forbidden in every task: a
new scheduler, timer thread, event bus, registry, plugin hook, generic
"demand" abstraction, control message type, env var (unless the owner
picks Q1 L2f), queue name, status vocabulary, or backward-compatibility
decoder. Reuse the helpers named per task.

### 1. Independent review of the plan and delta (before promotion)

Per §8. Round 1 is recorded below; this revision re-enters review because
it changes the launch model, the cadence rule, and the register.

### 2. Spec-promotion slice

- Outcome: `docs/specifications/` carries the delta verbatim; backlinks
  added; promotion baseline identifier recorded in this plan.
- Files: the four spec files in the delta table; this plan (identifier).
- Constraints: strategy A text lands without new mapping claims; do not
  edit `_Implementation mapping_` lines except the [MF-3.2] deletion named
  in the delta.
- Verify: `./.venv/bin/python -m pytest tests/specs -q`; run the backstitch
  gate (§7) and save the JSON as the **before** report.
- Done when: `tests/specs` passes, the before-report is saved, and the
  identifier is recorded.

### 3. Characterization first: write the red tests

- Outcome: the failing tests that define the contract exist before code
  changes; each is labelled red (with its reason) or characterization
  (green at HEAD, kept because it pins a contract this plan relies on).
- Files: `tests/tasks/test_task_monitor.py`, `tests/core/test_manager.py`,
  `tests/commands/test_status.py`, `tests/system/test_constants.py`,
  `tests/core/test_task_monitoring.py`.
- Read first: the test helpers named in §3; `tests/helpers/weft_harness.py`.
- Reuse: `broker_env`, `make_task_monitor_taskspec`, `recording_processor`,
  `drive_task_monitor_until_idle`, `drive_task_monitor_until_observed`,
  `_read_control_reply`, the fake-clock pattern at :6788; `manager_setup`,
  `make_manager_spec`, `drain`; `build_context`, `prepare_project_root`.
- Every new TaskMonitor test written here carries the existing
  `monkeypatch.setattr(task_monitor_mod, "upsert_heartbeat", lambda *a,
  **k: None)` line exactly like its neighbours (hidden coupling, §4); task
  4 deletes it with the other 113.
- Tests (names are binding):
  1. `test_task_monitor_inbox_poke_does_not_move_cycle_grid` (**red**):
     fake clock, `WEFT_TASK_MONITOR_MODE=custom` with `recording_processor`,
     interval 60. Cycle 1 at fake `t=0` (`process_once` +
     `drive_task_monitor_until_idle`). Advance to `t=50`; write one message
     to `T{tid}.inbox`; `process_once` (+ drive) runs cycle 2; assert
     `len(PROCESSOR_REQUESTS) == 2`. Advance to `t=59.5`; assert
     `next_wait_timeout() == pytest.approx(0.5)`; advance to `t=60`; assert
     `next_wait_timeout() == 0.0` and that `process_once` runs cycle 3 with
     no inbox message. After cycle 3 finishes (still `t=60`), assert
     `next_wait_timeout() == 1.0` (the cap) — the tick-started cycle
     consumed its own tick and owes no follow-up (round-2 post-start
     rows). Red at HEAD: after the poke the deadline is `50 + 60 = 110`, so
     at `t=59.5` the timeout is `1.0` (the cap) and at `t=60` no cycle
     runs.
  2. `test_task_monitor_overrun_cycle_is_followed_by_exactly_one_cycle`
     (**red**): fake clock; a module-level processor wrapping
     `recording_processor` that advances the fake clock by 90 s while it
     runs (interval 60). After cycle 1 finishes at `t=90` assert
     `next_wait_timeout() == 0.0` and that the next `process_once` runs
     cycle 2 with no inbox message. Swap the processor for the fast one;
     after cycle 2 finishes (still `t=90`) assert `next_wait_timeout() ==
     1.0` (the cap; not a third immediate cycle); advance to `t=119.5`,
     assert `0.5`; `t=120`, assert `0.0` — the grid tick is 120, not
     `90 + 60 = 150`. Red at HEAD: the deadline after cycle 1 is `150`.
  3. `test_task_monitor_arm_next_cycle_deadline_matrix` (**red**: attribute
     missing): direct call of the new
     `TaskMonitor._arm_next_cycle_deadline(catchup_pending=...)` on a
     constructed monitor with the fake clock, interval 60, catch-up 2,
     parametrized over `(grid_preset, follow_up_preset, now,
     catchup_pending, expected_due, expected_grid_after,
     expected_follow_up_after)`:
     - pre-tick rows (grid 60, no owed follow-up): `(60, False, 10, False,
       60, 60, False)`, `(60, False, 90, False, 90, 120, True)`, `(60,
       False, 150, False, 150, 180, True)`, `(60, False, 10, True, 12, 60,
       False)`, `(60, False, 59.5, True, 60, 60, False)`, `(60, False, 90,
       True, 90, 120, True)`;
     - post-start rows (a cycle started at tick 60 already consumed it, so
       the grid is 120): `(120, False, 60.1, False, 120, 120, False)`,
       `(120, False, 90, False, 120, 120, False)` — today's rule gives no
       follow-up for a tick-started cycle that ran 60→90 (simulation row
       "tick-started cycle runs 60 -> 90");
     - chained re-arm rows (Codex F1): `(120, True, 75, False, 75, 120,
       True)` — a second re-arm after the first one owed a follow-up keeps
       `due = now`; `(120, True, 77, True, 77, 120, True)`; and `(120,
       False, 75, False, 120, 120, False)` as the negative control.
     Assert the returned value equals `expected_due - now`,
     `_next_cycle_due_monotonic == expected_due`, `_cycle_grid_due_monotonic
     == expected_grid_after`, and `_overrun_follow_up_pending ==
     expected_follow_up_after`. The private seam is the contract here
     ([MF-5] cadence bullet), and both production re-arm sites call it.
     The start-side consumption (`while grid <= start`) and the flag reset
     live in `_run_monitor_cycle` and are covered by tests 1, 2, and 3b, not
     by this matrix.
  3b. `test_task_monitor_overrun_builtin_cycle_with_control_cleanup_runs_exactly_one_follow_up`
     (**red**): built-in mode (the default `WEFT_TASK_MONITOR_MODE`), fake
     clock, interval 60. Drive one built-in cycle whose worker result is
     committed at fake `t=70` with `runtime_cleanup_ready=True` (reuse the
     seam the existing control-cleanup tests use:
     `_maybe_start_terminal_control_cleanup_worker` :1089 and
     `_handle_control_cleanup_worker_result` :908/:1065 in
     `tests/tasks/test_task_monitor.py`), then commit the control-cleanup
     result at `t=75`. Assert after the cleanup result:
     `next_wait_timeout() == 0.0`, `_overrun_follow_up_pending is True`,
     and the next `process_once` starts a cycle with no inbox message and
     clears the flag; after that cycle finishes (fast, still `t≈75`)
     assert `next_wait_timeout() == 1.0` (cap; grid 120) — exactly one
     follow-up. Red at HEAD: the attribute does not exist; and the naive
     rule without the flag yields `next_wait_timeout() == 1.0` after the
     cleanup result (simulation rows "F1: …" in
     `scratchpad/plans/06-rev2/sim_cadence_r2b.py`: 3 of 14 rows differ
     from today without the flag, 0 of 14 with it).
  4. Modify `test_task_monitor_ping_includes_health_and_preserves_task_log`
     (:1905-1910): replace the `extended["heartbeat"] == {...}` assertion
     with `assert "heartbeat" not in extended` (**red**). Keep every other
     assertion; the `schedule` block is unchanged.
  5. `test_task_monitor_stop_while_waiting_on_cycle_grid`
     (**characterization** — expected green at HEAD; no existing test
     drives STOP into a waiting TaskMonitor, the only `CONTROL_STOP` write
     in the file is a Consumer test): after cycle 1 with the grid 60 s
     away, write `encode_control_message(CONTROL_STOP)` to `ctrl_in`;
     `wait_for_activity(next_wait_timeout())` returns promptly;
     `process_once`; assert `task.should_stop`. Record its HEAD result in
     the closeout; if it is red at HEAD, that is a finding, not a fix.
  6. Delete `test_task_monitor_heartbeat_failure_records_health_but_still_cycles`
     (:8357-8390): it proves the retired fallback and nothing else.
  7. `tests/core/test_manager.py`:
     - `test_manager_desires_heartbeat_with_builtin_monitors_disabled`
       (**red**): `manager_setup`; `_task_monitor_enabled = False`,
       `_liveness_monitor_enabled = False`, canonical inbox,
       `_evaluate_dispatch_ownership` stubbed to `self` (pattern
       :4440-4458), `_enqueue_managed_service_request` recorder;
       `_tick_internal_services(force=True)`; assert `enqueued ==
       [INTERNAL_SERVICE_KEY_HEARTBEAT]`. Red: `[]` at HEAD (the gate at
       :5858 is false and :5869 returns).
     - `test_manager_liveness_only_desires_heartbeat` (**characterization**,
       green at HEAD): same setup with `_liveness_monitor_enabled = True`;
       assert `enqueued == [INTERNAL_SERVICE_KEY_HEARTBEAT,
       INTERNAL_SERVICE_KEY_LIVENESS_MONITOR]`. Pins the L2 rule from the
       side the evidence file found unpinned.
     - `test_convergence_reports_missing_heartbeat_with_builtin_monitors_disabled`
       (**red**): copy of :3362-3395's setup with `_task_monitor_enabled =
       False` (and liveness False), heartbeat `active_tid = None`, no
       TaskMonitor state; assert `"missing_active_tid" in reasons`. Red:
       the :6451 arm is false at HEAD.
     - `test_manager_convergence_drains_pending_internal_spawn_work`
       :1703-1750 stays **unchanged** (it stubs reconcile at :1725; the
       revision-1 edit to :1749 was wrong and is withdrawn).
     - The nine measured flips (§3 table) are fixed here, before task 5,
       with one accepted pattern so the task-5 sweep never hits the stop
       rule. **L2f (default):** add `"WEFT_HEARTBEAT_SERVICE_ENABLED": "0"`
       to the `load_config({...})` dict each of the eight dispatch /
       admission / ordering tests already passes (:1958, :2388, :2549,
       :2736, :7859, :8103, :8151; :5148 uses `False` for its two keys, so
       use `False` there) — the same seam the tests already use for the two
       monitor keys, and the only seam that works because the flip happens
       in `Manager.__init__` (:495) before any monkeypatch. These eight
       tests are green at HEAD with the key present only after task 5
       lands the flag (until then `load_config` passes the unknown key
       through; verify they stay green at HEAD when the key is added, and
       record it). For :1889 (`test_manager_does_not_enqueue_task_monitor_when_disabled`)
       replace the heartbeat `assert not any(...)` at :1912-1916 with
       `assert any(...)` (**red** at HEAD; pins default-true desire with
       both monitors disabled). Verified: with L2f wired in a scratch
       worktree and exactly these nine edits, `tests/core/test_manager.py`
       is fully green. **Pure L2 (if chosen in Q1):** :1889 gets the same
       positive assertion; the seven dispatch/admission tests need
       `drain(make_queue(WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE))` immediately
       after construction plus `monkeypatch.setattr(manager,
       "_reconcile_managed_services", lambda **_: None)` (the :1723-1725
       pattern in full — the drain removes the construction-time request);
       :5148 has **no** fix from existing seams because reconcile is its
       subject and the construction-time request cannot be intercepted —
       stop and report. That unfixable case is the concrete reason Q1
       recommends L2f.
     - L2f firing tests (`tests/system/test_constants.py`,
       `tests/core/test_manager.py`, `tests/commands/test_status.py`; all
       **red** until task 5): `test_heartbeat_service_enabled_env` in the
       env-parse class next to `test_liveness_monitor_enabled_env`
       :780-786 (`"0"` → `False`, `"true"` → `True`, unset → `True`); two
       rows in the `test_explicit_override_normalization_preserves_input_contract`
       parametrize list :127-141 (`("WEFT_HEARTBEAT_SERVICE_ENABLED", "0",
       False)`, `("WEFT_HEARTBEAT_SERVICE_ENABLED", True, True)`) — this is
       the test that fails if the `_WEFT_OVERRIDE_RULES` entry is omitted,
       together with the parity test :106 which needs no edit (the key is
       added on both sides); `test_manager_flag_off_desires_no_heartbeat`
       (same setup as `test_manager_desires_heartbeat_with_builtin_monitors_disabled`
       with `config=load_config({"WEFT_HEARTBEAT_SERVICE_ENABLED": "0", ...})`;
       assert `enqueued == []`); and
       `test_status_heartbeat_row_config_disabled_with_flag_off` (context
       config with the flag `"0"`; assert the heartbeat snapshot renders
       `enabled False, status "config-disabled"`, the existing vocabulary
       at `system.py:1128-1170`); and
       `test_heartbeat_registration_fails_with_flag_off`
       (`tests/tasks/test_heartbeat.py` is untouched — put this in
       `tests/core/test_manager.py` next to the flag-off desire test): with
       the flag `"0"`, a canonical manager driven with `process_once()`, no
       live heartbeat endpoint, and a small `startup_timeout=0.05` passed
       **directly** to `upsert_heartbeat(...)` (its default is bound to
       10.0 at function definition, `heartbeat.py:221`, so monkeypatching
       `MANAGER_STARTUP_TIMEOUT_SECONDS` does not shorten it), assert it raises
       `RuntimeError` naming the endpoint and that no heartbeat child was
       launched (register row 18's failure mode).
     - All other assertions listed in §3 stay as written.
  8. `tests/commands/test_status.py`:
     `test_status_heartbeat_row_enabled_with_builtin_monitors_disabled`
     (**red**): context config `WEFT_TASK_MONITOR_ENABLED=0`,
     `WEFT_LIVENESS_MONITOR_ENABLED=0`, no manager; assert the heartbeat
     snapshot has `enabled True, status "unknown", evidence "none"` and
     the two monitor rows have `enabled False`. Red: heartbeat renders
     `config-disabled` at HEAD.
  9. `tests/system/test_constants.py`: replace
     `test_task_monitor_interval_rejects_below_heartbeat_minimum`
     (:597-610) with `test_task_monitor_interval_accepts_small_positive_values`
     (`"5"` → `5`) and `test_task_monitor_interval_rejects_zero_and_negative`
     (`"0"`, `"-1"` → `ValueError` matching the variable name). In
     `tests/core/test_task_monitoring.py` add a
     `TaskMonitorRuntimeConfig.from_config` case with interval `5`
     accepted; keep :313 (zero rejected). Red: 5 is rejected at both
     sites today. (If the owner keeps a floor under Q2, these become the
     floor-boundary tests `59 → ValueError`, `60 → 60`.)
- Stop if: a red test needs to mock `TaskMonitor`, `Manager`, a queue, or
  `HeartbeatTask` internals beyond the seams already used by the existing
  tests in the same file — report instead.
- Done when: every test labelled red fails for its stated reason at the
  promoted spec baseline (`./.venv/bin/python -m pytest <file>::<name> -q
  -n0`), the characterization tests' HEAD results are recorded, and every
  unchanged test still passes.

### 4. TaskMonitor: remove heartbeat registration; grid-anchored local deadline

- Outcome: `weft/core/monitor/task_monitor.py` imports nothing from
  `weft.core.heartbeat`; the cycle deadline is owned by one method that
  keeps a fixed grid; PONG has no `heartbeat` block; tests 1-6 from task 3
  are green; the 113 monkeypatch sites are gone; reciprocal traceability
  for [MF-5], [CC-2.5], [CC-2.3] lands here.
- Files: `weft/core/monitor/task_monitor.py`, `weft/_constants.py`
  (ledger only), `tests/tasks/test_task_monitor.py`,
  `docs/specifications/01-Core_Components.md` ([CC-2.5] mapping sentence).
- Read first: §3 TaskMonitor structure; [MF-5] cadence bullet; [CC-2.5]
  wake model; `tests/tasks/test_task_monitor.py:280-300`.
- Edits, in order:
  1. Delete import :90 and the `TASK_MONITOR_HEARTBEAT_STARTUP_TIMEOUT_SECONDS`
     import at :65 (keep :47 and :50 — used by :3524-3527).
  2. Delete init fields :742-745; add `self._cycle_grid_due_monotonic =
     0.0` next to `_next_cycle_due_monotonic` (:746) with a one-line
     comment "next periodic grid tick; 0.0 until the first cycle starts",
     and `self._overrun_follow_up_pending = False` with the comment "one
     immediate follow-up cycle is owed after an overrun; cleared when a
     cycle starts".
  3. Delete `_ensure_heartbeat_registered` (:4755-4783),
     `_cancel_heartbeat` (:4785-4793), the call at :1568, and the whole
     `_handle_control_command` override (:1612-1619) — it exists only to
     cancel; the base implementation then handles STOP/KILL.
  4. At the top of `_run_monitor_cycle` (:4811), before the mode branch
     (so both the built-in worker and custom-processor modes share it):
     `start = time.monotonic()`; `interval =
     self._monitor_config.interval_seconds`; `if
     self._cycle_grid_due_monotonic == 0.0: self._cycle_grid_due_monotonic
     = start + interval` (anchors the grid once, at first cycle start;
     never re-anchored) `else: while self._cycle_grid_due_monotonic <=
     start: self._cycle_grid_due_monotonic += interval` (a cycle consumes
     every tick at or before its start, so a tick-started cycle does not
     see its own tick as an overrun at finish); then
     `self._overrun_follow_up_pending = False` (a cycle actually started;
     any owed follow-up is now being paid).
  5. Add one method next to `_finish_monitor_cycle`:
     `_arm_next_cycle_deadline(self, *, catchup_pending: bool) -> float`:
     `now = time.monotonic()`; `interval =
     self._monitor_config.interval_seconds`; `if
     self._cycle_grid_due_monotonic <= now: self._overrun_follow_up_pending
     = True`; `while self._cycle_grid_due_monotonic <= now:
     self._cycle_grid_due_monotonic += interval`; `due = now if
     self._overrun_follow_up_pending else self._cycle_grid_due_monotonic`;
     `if catchup_pending: due = min(due, now +
     self._monitor_config.catchup_interval_seconds)`; store into
     `_next_cycle_due_monotonic`; return `due - now` (the value the two
     callers log as `next_interval_seconds`). The flag — not a local
     `overran` — is what makes the second, chained re-arm
     (`_handle_control_cleanup_worker_result`) keep `due = now`; it is
     cleared only in item 4. Docstring: the [MF-5] cadence bullet
     paraphrased in two lines, then `Spec: [MF-5], [CC-2.5]`. Simulation
     evidence: `scratchpad/plans/06-rev2/sim_cadence_r2b.py` — today's
     rule versus this rule over the six matrix rows, steady state, the two
     post-start rows, and four chained-cleanup rows: 14/14 identical cycle
     start sequences (revision 1's finish-only rule: 0/14; start-consume
     without the flag: 11/14).
  6. Replace the duplicated re-arm blocks :5069-5076 and :5455-5462 with
     calls to it (`self._last_catchup_pending` is the argument at both
     sites; keep the surrounding `_set_activity` and log emission; the
     logged `next_interval_seconds` becomes the returned value).
  7. Replace `self._last_error = self._heartbeat_error` at :5062 and
     :5445 with `self._last_error = None`.
  8. In `_task_monitor_pong_extension` delete :1747-1750 and the
     `"heartbeat"` block :1778-1783; reword the `next_wait_timeout`
     docstring :1492-1493 to "reactive to task-local wakeups and its own
     grid-anchored cycle deadline ([CC-2.5] wake model; Spec: [MF-5])".
  9. `_constants.py` ledger: remove `_heartbeat_error`, `_heartbeat_id`,
     `_heartbeat_registered` (:1192) and
     `_next_heartbeat_registration_attempt_monotonic` (:1222); add
     `_cycle_grid_due_monotonic` and `_overrun_follow_up_pending` in the
     ledger's alphabetical positions. Do not touch the share-field sets
     (:1286-1290) — the new fields are a plain float and a plain bool and
     are not shared.
  10. Test sweep: delete all 113 `monkeypatch.setattr(task_monitor_mod,
      "upsert_heartbeat", ...)` statements (each is one 1-3 line call;
      `grep -n 'upsert_heartbeat' tests/tasks/test_task_monitor.py` must
      return zero lines afterwards). Leave the `monkeypatch` fixture
      parameter in place where other patches use it; remove it only where
      ruff reports it unused. Also delete the guard at :1805-1809.
  11. Reciprocal mapping ([CC-2.5], strategy A linking step): append to the
      `_Implementation mapping_` paragraph at 01:685-706 the sentence
      "`weft/core/monitor/task_monitor.py::TaskMonitor.next_wait_timeout`
      and `TaskMonitor._arm_next_cycle_deadline` supply the supervised
      monitor's local grid deadline under this wake model; cadence
      coverage lives in `tests/tasks/test_task_monitor.py`." No other
      mapping edit ([MF-5] :843 already lists the file).
- Reuse: `time.monotonic()` as the rest of the file does; the existing
  `_set_activity`/`_emit_task_monitor_log_rate_limited` calls.
- Constraints: no new wake path; `_wake_requested` and
  `_handle_work_message` stay exactly as they are; do not touch
  `next_wait_timeout` beyond the docstring; do not change
  `_first_cycle_pending` handling; no rename of `_next_cycle_due_monotonic`.
- Stop if: any other production module imports the deleted symbols (grep
  `_ensure_heartbeat_registered|_cancel_heartbeat|_heartbeat_registered`
  across `weft/` first); or the parity test needs a share-set change.
- Verify: `./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py -q`
  and `./.venv/bin/mypy weft/core/monitor/task_monitor.py --config-file pyproject.toml`.
- Done when: tests 1-6 (including 3b) pass; the file's only `heartbeat`
  matches are the two constants at :47/:50 and their use at :3524-3527;
  the existing wait tests :1727 and :1757 still pass unmodified except for
  the removed monkeypatch.

### 5. Manager and status: heartbeat is desired by every canonical manager under launch policy (L2f default; pure L2 alternative)

- Outcome: the three copied desire-gate sites no longer encode a
  dependency on TaskMonitor/LivenessMonitor; a canonical public-request
  manager desires the heartbeat `ensure` service whenever
  `WEFT_HEARTBEAT_SERVICE_ENABLED` is true (default) — unconditionally
  under pure L2; `weft status` treats the heartbeat row as enabled
  regardless of the monitor flags; tests 7-8 from task 3 (including the
  L2f firing tests) are green; the harness residue gate is in place;
  reciprocal traceability for [MA-1] item 7 lands here.
- Files: `weft/_constants.py` (L2f flag), `weft/core/manager.py`,
  `weft/commands/system.py`, `tests/core/test_manager.py`,
  `tests/commands/test_status.py`, `tests/system/test_constants.py`,
  `tests/helpers/weft_harness.py`, `tests/test_harness_registration.py`
  (the residue-gate ordering test lives here so it is normally collected —
  `tests/helpers/` is not a test package), `docs/specifications/00-Quick_Reference.md`
  (L2f row — promoted in task 2, verified here), `README.md` (L2f bullet).
- Read first: [MA-1] item 7 promoted text; `manager.py:368-500` (the
  constructor's forced reconcile at :495), :5842-5900, :6415-6470;
  `system.py:1073-1084`; `_constants.py:1010-1011`, :2915-2919,
  :3223-3226; `weft_harness.py:198-243`, :620-664, :1126-1200;
  `base.py:980-1050`.
- Edits, in order (L2f; the pure-L2 variant of each edit is in
  parentheses):
  0. L2f flag, three places in `weft/_constants.py`, mirroring the
     liveness flag exactly: `HEARTBEAT_SERVICE_ENABLED_DEFAULT: Final[bool]
     = True` with the docstring "Default for canonical-manager supervision
     of the built-in heartbeat service." directly after :1010-1011; a
     loader entry `"WEFT_HEARTBEAT_SERVICE_ENABLED": _load_weft_env_value(
     "WEFT_HEARTBEAT_SERVICE_ENABLED", default=HEARTBEAT_SERVICE_ENABLED_DEFAULT,
     parser=_parse_bool)` directly after the liveness entry :2915-2919; and
     `"WEFT_HEARTBEAT_SERVICE_ENABLED": _OverrideRule(kind=_OverrideKind.BOOLISH,
     parser=_parse_bool)` directly after the liveness rule :3223-3226. All
     three are required: without the rule the parity test :106 fails and
     `load_config({"WEFT_HEARTBEAT_SERVICE_ENABLED": "0"})` keeps `"0"`,
     which reads as true. `Manager.__init__`: `self._heartbeat_service_enabled
     = bool(self._weft_config.get("WEFT_HEARTBEAT_SERVICE_ENABLED", True))`
     directly after :449-451. (Pure L2: skip this edit.)
  1. `manager.py:5855-5859`: replace the three-line comment and the `if`
     with `if self._heartbeat_service_enabled:
     internal_services.append(self._heartbeat_service_spec())` preceded
     by the comment "The heartbeat is the registration endpoint for
     external heartbeat consumers; every canonical public manager desires
     it while WEFT_HEARTBEAT_SERVICE_ENABLED is true (Spec: [MA-1.6a],
     [MA-1] item 7, [MF-3.2])." — `[MA-1.6a]` is the reciprocal key: its
     `_Implementation mapping_` at 03:383 already lists
     `Manager._reconcile_managed_services` and
     `Manager._build_heartbeat_spawn_payload`. The enclosing `if
     include_internal and inbox == WEFT_SPAWN_REQUESTS_QUEUE` (:5854) is
     the "canonical public manager" scope and is unchanged. (Pure L2: an
     unconditional `append`.)
  2. `manager.py:6450-6451`: replace `or service_key ==
     INTERNAL_SERVICE_KEY_HEARTBEAT and (self._task_monitor_enabled or
     self._liveness_monitor_enabled)` with `or service_key ==
     INTERNAL_SERVICE_KEY_HEARTBEAT and self._heartbeat_service_enabled`.
     (Pure L2: drop the `and` clause entirely.)
  3. `system.py:1080-1081`: `if key == INTERNAL_SERVICE_KEY_HEARTBEAT:
     return bool(ctx.config.get("WEFT_HEARTBEAT_SERVICE_ENABLED", True))`,
     read next to the two monitor flags at :1073-1077. (Pure L2: `return
     True`.) Leave `_service_enabled`'s monitor-flag reads for the other
     two keys. Verified in the scratch worktree: with edits 0-3 applied,
     `tests/system/test_constants.py` (parity :106 included) and
     `tests/commands/test_status.py` pass.
  4. Docs for the flag: the Quick Reference row after :187 is already
     promoted (task 2); add the README bullet after :1323-1324.
  5. Harness residue gate (`tests/helpers/weft_harness.py`, test-helper
     change only): define `class HarnessResidueWarning(UserWarning)` at
     module level; in `cleanup()` :640 capture `live =
     self._wait_for_registered_pids_to_exit()`, then run
     `_terminate_registered_pids()` and **every remaining cleanup step**
     (`_close_live_database_queues`, `_remove_database_files`,
     `cleanup_prepared_roots`), and only **after** cleanup completes emit
     `warnings.warn(HarnessResidueWarning(f"managed PIDs still alive after
     manager stop: {live}"), stacklevel=2)` when `live` was non-empty
     (deferred warn; a `try/finally` around the cleanup chain keeps the warn
     from being skipped by a cleanup exception). Ordering matters because the
     gate promotes the warning to an exception with `-W error`: warning
     first would abort `cleanup()` at `warnings.warn`, skipping the
     force-kill and the queue/database cleanup and orphaning the leaked
     child (Codex R3-1 / Claude R3-1). Add
     `tests/test_harness_registration.py::test_harness_cleanup_terminates_before_residue_warning`:
     build a harness with a stubbed `_wait_for_registered_pids_to_exit`
     returning a fake live PID list and delegating spies on
     `_terminate_registered_pids`, `_close_live_database_queues`,
     `_wait_for_database_files_releasable` (`weft_harness.py:642`, between
     queue closing and database removal in the real chain),
     `_remove_database_files`, and `cleanup_prepared_roots` that append to
     an order list; inside `warnings.catch_warnings():
     warnings.simplefilter("error")`, call `cleanup()` and assert it raises
     `HarnessResidueWarning` **and** the order list holds all five cleanup
     calls in the current chain's order (`:620-665`) before the raise. The PIDs come from the harness-scoped
     `weft.state.tid_mappings` host-process identities
     (`_collect_pid_mappings` :1126, create-time matched at :1162-1175),
     so the gate is exact and scoped to this harness's context — never
     `pgrep`: heartbeat children run with `enable_process_title=False`
     (`manager.py:4723`; `base.py:2173` returns before setting a title),
     so a `pgrep` on the service name matches nothing and is system-wide.
     Raise `_stop_inline_managers` :660 from `thread.join(timeout=2.0)` to
     `thread.join(timeout=TASK_CLEANUP_TIMEOUT_SECONDS + 3.0)` (import the
     constant from `weft._constants`) so the join outlasts the serve
     thread's `_terminate_children` deadline and the PID sweep observes a
     finished cleanup instead of overlapping it (§4 hidden coupling).
  6. Sweep for fallout: run `./.venv/bin/python -m pytest -m "" -q`.
     Expected: **no** failures — the nine measured flips were fixed in
     task 3 with the config-key pattern (L2f). Any failure is a finding:
     record it in the Deviation Log with the assertion. The one known
     load-sensitive test,
     `tests/cli/test_cli_run.py::test_parallel_manager_reuse_converges_to_single_manager_under_repeated_bootstrap`
     (:2938), failed once under L2 in a 12-worker run with phase
     `manager_convergence_timeout` and passed 3/3 isolated; if it fails in
     the sweep, rerun it isolated three times and record both results —
     widen only its constrained-parallelism timeouts (`:2948-2953`), a
     test-only change, if the isolated reruns also fail. (Pure L2: the
     seven dispatch/admission tests use the drain-plus-reconcile-stub
     pattern from task 3; if :5148 cannot be made green without a new
     seam, stop and report — do not add a test-mode suppression to the
     product.)
- Residue gate (replaces the revision-1 `pgrep` gate): run
  `./.venv/bin/python -m pytest tests/system tests/cli tests/commands
  tests/core/test_client.py tests/core/test_ops_shared.py
  tests/core/test_task_monitoring.py tests/core/test_spawn_requests.py
  tests/tasks/test_signal_deferral.py tests/test_harness_registration.py
  -q -W error::tests.helpers.weft_harness.HarnessResidueWarning` (the
  `tests` package is importable — `tests/__init__.py` exists and
  `tests/conftest.py:20` already imports from it). It must pass: a leaked
  heartbeat child turns the warning into a failure in the harness
  `cleanup()` of the test that leaked it. Baseline evidence: the
  instrumented worktree run of these suites under L2 recorded zero
  managed PIDs alive at forced-terminate time. Do not "fix" residue by
  re-adding a monitor-flag gate.
- Timed cost run (required at closeout, R2-3): `time ./.venv/bin/python -m
  pytest <the residue-gate suite list> -q` on the pre-task-5 tree and on
  the post-task-5 tree; record wall/user/sys in the closeout. Expected
  from the scratch-worktree measurement on this machine (12 workers):
  about +7 s wall and +60 s CPU (HEAD 1 m 41.5 s → L2 1 m 48.5 s).
- Constraints: no change to `_heartbeat_service_spec`,
  `_build_heartbeat_spawn_payload` (`heartbeat_idle_timeout: 0.0` stays —
  the ensure service never idle-exits), the reducer,
  `_tick_managed_service`, `_drain_internal_spawn_requests`, or the
  constructor's forced reconcile (:495). The harness must not set
  `WEFT_HEARTBEAT_SERVICE_ENABLED=0` (it leaks into `broker_env` tests;
  measured: 16 `test_manager.py` failures). The flag is launch policy
  only: it does not stop a running service, does not change the admission
  reserve (`_constants.py:783`, [MANAGER.18]), and adds no status
  vocabulary (`config-disabled` already exists).
- Verify: `./.venv/bin/python -m pytest tests/core/test_manager.py
  tests/commands/test_status.py tests/system/test_constants.py
  tests/tasks/test_heartbeat.py -q`, then the full suite, the residue
  gate, and the timed run.
- Done when: tests 7-8 and the L2f firing tests pass; `grep -rn
  "_task_monitor_enabled or self._liveness_monitor_enabled" weft` is
  empty; `grep -rn "task_monitor_enabled or liveness_monitor_enabled"
  weft/commands` is empty; `git diff --stat -- tests/tasks/test_heartbeat.py
  weft/core/heartbeat.py weft/core/manager_services.py` is empty; the
  residue gate passes; the timed run is recorded.

### 6. Constants: retire the monitor's heartbeat constant and the 60 s floor

- Outcome: `TASK_MONITOR_HEARTBEAT_STARTUP_TIMEOUT_SECONDS` no longer
  exists; `WEFT_TASK_MONITOR_INTERVAL_SECONDS` accepts any positive
  integer (recommended default, §11 Q2; if the owner keeps a floor,
  implement it as `TASK_MONITOR_MIN_INTERVAL_SECONDS: Final[int] = 60`
  in `_constants.py` with its own docstring, referenced from both
  validation sites, never by importing `HEARTBEAT_MIN_INTERVAL_SECONDS`).
- Files: `weft/_constants.py`, `weft/core/monitor/runtime.py`,
  `tests/system/test_constants.py`, `tests/core/test_task_monitoring.py`.
- Edits: delete `_constants.py:1389-1390`; reword the :1014 docstring to
  "Default cycle grid interval for the supervised task monitor.";
  `_parse_task_monitor_interval_seconds` (:2505-2513) becomes
  `return _parse_positive_int(value, name="WEFT_TASK_MONITOR_INTERVAL_SECONDS")`
  with the docstring "Parse the task-monitor cycle interval environment
  variable."; delete `runtime.py:221-225` and the import at :30.
  `HEARTBEAT_MIN_INTERVAL_SECONDS` itself stays (it governs heartbeat
  *registrations*, `weft/core/tasks/heartbeat.py:75-78`).
  `tests/core/test_task_monitoring.py:192,207` and
  `tests/system/test_constants.py:531,551` may keep using the constant as
  a value.
- Constraints: no other validation change; the Quick Reference row already
  landed in task 2.
- Stop if: any **production** module other than `weft/_constants.py` and
  `weft/core/tasks/heartbeat.py` still imports `HEARTBEAT_MIN_INTERVAL_SECONDS`
  after the `runtime.py` edit (`grep -rn HEARTBEAT_MIN_INTERVAL_SECONDS
  weft --include=*.py`). Test files (`tests/tasks/test_heartbeat.py:24`,
  `tests/core/test_task_monitoring.py:13`, `tests/system/test_constants.py:33`)
  are explicitly exempt — they use it as a registration value.
- Verify: `./.venv/bin/python -m pytest tests/system/test_constants.py tests/core/test_task_monitoring.py -q`;
  grep gate: `grep -rn TASK_MONITOR_HEARTBEAT_STARTUP_TIMEOUT_SECONDS weft tests docs/specifications` is empty.
- Done when: test 9 passes and both grep gates hold.

### 7. CHANGELOG and traceability reconciliation

- Outcome: docs and mapping notes describe the shipped state; the
  backstitch gate is rerun and compared against the task-2 baseline.
- Files: `CHANGELOG.md` (Unreleased → Changed), this plan.
- CHANGELOG entry (one bullet): "The supervised TaskMonitor no longer
  registers a heartbeat; it schedules cycles on a fixed local grid
  anchored at its first cycle (inbox pokes do not move the grid; an
  overrun cycle is followed by exactly one immediate cycle) and its PONG
  `extended.task_monitor` no longer carries a `heartbeat` block. The
  built-in heartbeat service is now desired by every canonical manager as
  the registration endpoint for external heartbeat consumers, including
  when both built-in monitors are disabled; it is no longer gated on
  `WEFT_TASK_MONITOR_ENABLED`/`WEFT_LIVENESS_MONITOR_ENABLED`; the new
  `WEFT_HEARTBEAT_SERVICE_ENABLED` (default true) is the launch-policy
  switch for it. `WEFT_TASK_MONITOR_INTERVAL_SECONDS` accepts any positive
  integer (the 60 s floor is gone)." Adjust the last sentence per Q2 and
  drop the flag sentence if the owner picks pure L2 in Q1.
- Mapping reconciliation: confirm the [CC-2.5] sentence from task 4 item
  11 is present; confirm `Spec:` backlinks on `_arm_next_cycle_deadline`,
  `next_wait_timeout`, and the manager comment; confirm [MA-1.6a] :383
  still lists `Manager._build_heartbeat_spawn_payload` and
  `_reconcile_managed_services` (unchanged); confirm task 5's L2f README
  bullet (`WEFT_HEARTBEAT_SERVICE_ENABLED`, after :1323-1324) is present —
  no other README changes. Close the deviation log (no `pending` rows).
- Backstitch: rerun the §7 gate command from the current tree, save the
  **after** JSON next to the before report, and diff the keyed
  diagnostics. Completion requires no new error- or warning-class
  diagnostic keyed on this plan, on [CC-2.5], [CC-2.3], [MF-3.2], [MF-5],
  or [MA-1], or on `task_monitor.py`, `manager.py`, `system.py`,
  `runtime.py`, `_constants.py`. Pre-existing corpus debt is not claimed
  cleared. If `../backstitch` is absent, record the gate as unpassed
  tooling and stop short of claiming completion.
- Done when: gates green, CHANGELOG line present, plan closeout recorded,
  before/after backstitch comparison recorded, and `tests/specs` pass.

## 6. Testing Plan

Harness and fixtures: `broker_env` (real SQLite-backed SimpleBroker
queues), in-process `TaskMonitor` and `Manager` instances driven through
`process_once()` / `wait_for_activity()` — the same production reactor
entry points — plus the existing harness-backed suites, which under
L2f-default-true exercise a real spawned heartbeat child in every
foreground manager (that is the end-to-end proof that the default desire
launches and is cleaned up — enforced by the `HarnessResidueWarning` gate;
no new slow test is added). Cadence evidence beyond the unit tests: the
simulation `scratchpad/plans/06-rev2/sim_cadence_r2b.py` models today's
heartbeat-grid rule (registration-anchored ticks, sticky `_wake_requested`,
finish re-arm at :5076/:5462) against the task-4 rule over 14 scenarios
including the chained control-cleanup re-arm; the task-4 rule reproduces
today's cycle-start sequence in all 14.

What must not be mocked: queues, `TaskMonitor`, `Manager`, `HeartbeatTask`,
`MultiQueueWatcher`, reservation, the internal spawn drain, the singleton
reducer, control handling, and the task log. Allowed seams: the fake
monotonic clock (`SimpleNamespace` over `task_monitor_mod.time`, existing
pattern), `_evaluate_dispatch_ownership` and `_enqueue_managed_service_request`
recorders in manager unit tests (existing pattern), and — only until task 4
lands — the existing `upsert_heartbeat` monkeypatch. Reaching for any other
patch is a stop-and-re-evaluate.

Contract elements with a firing test (enumerable-contract rule):
`WEFT_TASK_MONITOR_INTERVAL_SECONDS` validation (task 3 item 9); PONG
`extended.task_monitor` shape (item 4); the grid cadence rule — poke does
not move the grid, overrun yields exactly one follow-up including across
the chained control-cleanup re-arm, catch-up bound — (items 1-3, 3b);
inbox wake (existing :1757, kept); STOP while waiting (item 5); manager
desired set with both monitors disabled and with liveness only (item 7);
`missing_active_tid` for an undesired-by-flags heartbeat (item 7); status
`enabled` with monitors disabled (item 8); `WEFT_HEARTBEAT_SERVICE_ENABLED`
env parse, explicit-override normalization, loader/normalizer parity,
manager desired set with the flag off, status `config-disabled` with the
flag off (item 7, L2f); the worker-snapshot ledger (existing :286, kept);
the always-desired idle wait (existing :5489, kept). Red-green: every red
item lists its reason; characterization items are labelled.

Edge cases deliberately in scope: overrun spanning two grid ticks still
yields one follow-up (matrix row `(150, False, 150, 180)`); catch-up
shorter than the remaining grid wins, catch-up longer than it loses
(rows `(10, True, 12, 60)`, `(59.5, True, 60, 60)`); poke at `t=50` on a
60 s grid. Out of scope: registration failover across service restarts
(unchanged non-promise); on-demand launch (§9).

Post-runtime observation: after the change, `ps | grep weft-` on a default
install still shows Manager, TaskMonitor, LivenessMonitor, and Heartbeat;
with `WEFT_TASK_MONITOR_ENABLED=0 WEFT_LIVENESS_MONITOR_ENABLED=0` it shows
Manager and Heartbeat (new). `weft status` shows the heartbeat row
`running` in both configurations. TaskMonitor PONG (`weft task ping
<tid>`) shows `schedule.next_cycle_due_in_seconds` counting down to the
next grid tick and no `heartbeat` block; a `weft queue write
T<tid>.inbox x` runs an extra cycle without changing that countdown's
target.

## 7. Verification and Gates

Per task (fast, targeted):

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/specs -q                                   # task 2
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py -q              # tasks 3-4
./.venv/bin/python -m pytest tests/core/test_manager.py tests/commands/test_status.py tests/tasks/test_heartbeat.py -q   # task 5
./.venv/bin/python -m pytest tests/system tests/cli tests/commands tests/core/test_client.py tests/core/test_ops_shared.py tests/core/test_task_monitoring.py tests/core/test_spawn_requests.py tests/tasks/test_signal_deferral.py tests/test_harness_registration.py -q -W error::tests.helpers.weft_harness.HarnessResidueWarning   # task 5 residue gate (must pass)
time ./.venv/bin/python -m pytest tests/system tests/cli tests/commands tests/core/test_client.py tests/core/test_ops_shared.py tests/core/test_task_monitoring.py tests/core/test_spawn_requests.py tests/tasks/test_signal_deferral.py tests/test_harness_registration.py -q   # task 5 timed cost run, before and after
./.venv/bin/python -m pytest tests/system/test_constants.py tests/core/test_task_monitoring.py -q   # task 6
```

Backstitch traceability gate (run after task 2 as **before**, after task 7
as **after**; save both JSON reports under the session scratchpad, outside
the repository, and diff keyed diagnostics):

```bash
../backstitch/.venv/bin/backstitch check \
  --repo-root . --no-config \
  --spec-root docs/specifications --plan-root docs/plans \
  --code-root weft --code-root tests --code-root bin \
  --code-root integrations --code-root extensions --format json
```

Final gates (blast radius crosses manager, monitor, status, and every
harness-backed suite, so the full suite is required):

```bash
./.venv/bin/python -m pytest -m "" -q
./.venv/bin/mypy weft bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py -q
git diff --stat -- tests/tasks/test_heartbeat.py weft/core/heartbeat.py weft/core/manager_services.py   # must be empty
grep -rn "upsert_heartbeat\|cancel_heartbeat\|_ensure_heartbeat_registered" weft/core/monitor tests/tasks/test_task_monitor.py   # must be empty
grep -rn "TASK_MONITOR_HEARTBEAT_STARTUP_TIMEOUT_SECONDS" weft tests docs/specifications   # must be empty
grep -rn "_task_monitor_enabled or self._liveness_monitor_enabled" weft   # must be empty
grep -rn "HEARTBEAT_MIN_INTERVAL_SECONDS" weft --include=*.py | grep -v "weft/_constants.py\|weft/core/tasks/heartbeat.py"   # must be empty
grep -n "WEFT_HEARTBEAT_SERVICE_ENABLED" weft/_constants.py | wc -l   # L2f: must be 3 (default docstring context, loader entry, override rule) or more; 0 under pure L2
./.venv/bin/python -m pytest tests/system tests/cli tests/commands tests/core/test_client.py tests/core/test_ops_shared.py tests/core/test_task_monitoring.py tests/core/test_spawn_requests.py tests/tasks/test_signal_deferral.py tests/test_harness_registration.py -q -W error::tests.helpers.weft_harness.HarnessResidueWarning   # residue gate, must pass
```

Success is also observed at runtime as described at the end of §6.
Rollback: `git revert` of any single task commit; no persisted state to
repair.

## 8. Independent Review Loop

External review is run by the top-level agent using a different agent
family (Codex CLI is available on this machine) plus a Claude reviewer,
with the Planning Review Prompt from
`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md` §"Planning
Review Prompt" (PASS/BLOCKED against the two questions). Rounds 1 and 2
are recorded under `## Review Record`. Round 3 is a **scoped
verification** of revision 2 before promotion, limited to: (1) the
cadence rule in task 4 items 4-5 and the simulation
`scratchpad/plans/06-rev2/sim_cadence_r2b.py` (rerun it; confirm 14/14);
(2) the test-fleet inventory in §3 and the task 3 item 7 fix pattern
(reproduce by applying edits 0-3 of task 5 in a scratch worktree if
desired); (3) the residue gate and join timeout in task 5 item 5; (4)
L2f completeness (three `_constants.py` sites, firing tests) and the Q1
recommendation. A further scoped review follows task 5.

Reviewer must read: this plan including `## Proposed Spec Delta`,
`## Observable-Difference Register`, `## Review Record`, and `## Open
Owner Questions`; `docs/specifications/01-Core_Components.md` [CC-2.5]
:668-706 and [CC-2.3] :484-537; `docs/specifications/05-Message_Flow_and_State.md`
[MF-3.2] :330-405 and [MF-5] :427-600, :842-865, :1436-1446;
`docs/specifications/03-Manager_Architecture.md` [MA-1] :225-262;
`docs/specifications/07-System_Invariants.md` [IMPL.7]-[IMPL.10] :609-680
and [MANAGER.15]-[MANAGER.18] :809-862; `weft/core/tasks/base.py:1106-1250`,
:1376-1435; `weft/core/monitor/task_monitor.py:1489-1620`, :1743-1800,
:4755-4830, :5043-5090, :5357-5470; `weft/core/tasks/heartbeat.py:96-130`,
:381-394, :455-516; `weft/core/manager.py:4701-4750`, :5842-5900,
:6415-6470, :6590-6630, :6898-6910; `weft/commands/system.py:1073-1170`;
`tests/helpers/weft_harness.py:198-235`, :655-700;
`weft/_constants.py:1178-1290`, :1386-1390, :2505-2513.

Stance requested: (1) check the grid-anchored deadline against the
heartbeat-grid semantics it replaces (registration anchor,
`_reschedule_registration`, one follow-up per overrun) and say whether
any cadence case differs by more than registration dispatch latency; (2)
confirm L2 keeps every existing manager idle-shutdown and convergence
test valid and that the harness process-residue gate is sufficient; (3)
judge whether the [MA-1] item 7 sentence is the right normative home for
"always desired" or whether it belongs in [MF-3.2]; (4) name any
performative ceremony to remove. Feedback returns as a review record
appended to this plan; each point is applied, argued, or scoped out
explicitly.

## 9. Out of Scope

- **On-demand heartbeat launch (L3) — named follow-up plan**
  `docs/plans/<date>-heartbeat-on-demand-launch-plan.md`. Its round-0
  design (helper-authored internal spawn request via a shared builder in
  `manager_services.py`, a `_heartbeat_demanded` desire rule at the three
  gate sites, `idle`/`on-demand` status vocabulary, resolve-only
  `cancel_heartbeat`, idle exit after `HEARTBEAT_IDLE_TIMEOUT_SECONDS`)
  is withdrawn from this plan. Any such plan must first resolve the five
  round-1 Codex blockers: (1) a non-manager author on `weft.spawn.internal`
  conflicts with [MANAGER.15]'s manager-authorship and
  reducer-before-side-effects rules (`manager.py:4440`, `:6855`) — the
  demand signal must be reduced by the manager and converted into a
  manager-authored envelope, or the invariant must be amended for the
  owner explicitly; (3) concurrent first registrations can lose an
  acknowledged registration to the losing duplicate — dedupe before
  launch or prove both registrations reach the canonical service; (4) a
  one-shot demand request is not durable across manager reservation loss
  (`manager.py:4247` exact-deletes private reservations) — define requeue,
  regeneration, or bounded helper retry, with a real-queue crash test;
  (5) a boolean `cancel_heartbeat` converts uncertain liveness into
  silent success — no-op only on proven absence/terminal evidence; (7)
  the promised post-idle `idle`/`on-demand` status cannot be produced
  once retained terminal task-log evidence exists (`system.py:791`,
  `:1099`) — specify how expected idle exit overrides terminal evidence
  and test both histories. Also carried to that plan: Claude round-1
  findings 2 (`_reconcile_managed_services` :5869 returns before any
  pending scan when nothing is enabled — the demand scan must precede
  it) and 7 (demand-scan cost on every convergence pass).
- Any change to `HeartbeatTask` emitter semantics, registration
  validation, coalescing, supersession, or `tests/tasks/test_heartbeat.py`.
- Any change to `weft/core/heartbeat.py` or `weft/core/manager_services.py`.
- A public (`weft.client`/`weft.commands`) heartbeat registration surface
  or a CLI command; cron/scheduler features ([12-Future_Ideas]).
- Registering the [CC-2.5] wake-model rule as a numbered [IMPL.x]
  invariant with per-service firing coverage (§11 Q3).
- Changing LivenessMonitor, Manager, or HeartbeatTask wait code — they
  already follow the [CC-2.5] wake model.
- TaskMonitor cleanup, collation, maintenance cadence, or catch-up value
  changes; the admission reserve floor ([MANAGER.18]).
- The pre-existing inaccuracy in 10-CLI :390 (lists heartbeat and
  TaskMonitor, omits LivenessMonitor) — documentation follow-up, not this
  plan.
- README edits beyond the single L2f flag bullet task 5 adds (see §3;
  `:591`/`:1321` describe the admission reserve and stay unchanged).
- Any backward-compatibility handling for pre-upgrade TaskMonitor
  processes.

## 10. Fresh-Eyes Review

Author pass 2026-09-08 (round 0), re-read as a Python engineer without
Weft context, then revised after round-1 review. Findings by severity and
what changed:

1. **Blocking (fixed in round 0):** the first draft assumed the April-17
   "lazy start" existed and planned only to stop the manager desiring
   heartbeat. Reading `ensure_heartbeat_service` showed it never launches
   anything; with zero desire every registration would time out.
2. **Blocking (re-fixed in revision 1):** round 0 answered finding 1 by
   designing an on-demand launch path (L3). Round-1 review showed that
   path conflicts with [MANAGER.15] manager authorship, loses
   registrations under concurrent first starts, is not durable across
   reservation loss, and cannot render its promised status after idle
   exit. Revision 1 lands the decoupling on L2 and moves L3 to a named
   follow-up (§9) with those blockers listed.
3. **Blocking (re-fixed in revision 1):** round 0's "start-anchored"
   deadline (`max(now, start + interval)`) let an inbox poke just before a
   grid tick push the next periodic cycle by almost a full interval —
   a cadence diminution the register mis-classed as C. The heartbeat grid
   is registration-anchored (`heartbeat.py:381-390`); the revised rule
   keeps a fixed grid, reproduces the single follow-up, and is pinned by a
   six-row matrix plus two behavioral tests.
4. **Major (new in revision 1):** L2 has a test-fleet blast radius neither
   reviewer saw: `WeftTestHarness` disables both monitors and runs a
   canonical manager, so every harness foreground manager (39 call sites)
   now supervises a real heartbeat child, and `test_manager.py:1749`'s
   exact launch list flips. Added the hidden coupling, the :1749 update,
   the `pgrep` process-residue stop gate, and Q1 options L2f/L2b.
5. **Major (fixed):** the desire gate is copied in three places
   (`manager.py:5858`, `:6451`, `system.py:1081`); all three are edited in
   task 5 with grep gates.
6. **Major (fixed in revision 1):** round 0 labelled a test red that is
   green at HEAD (the drain at `manager.py:6593/:6621` runs independently
   of reconcile). Deleted with L3; the replacement red tests are the
   both-disabled desire test and the `missing_active_tid` test, whose red
   reasons are the two gate sites themselves.
7. **Major (fixed):** `_WORKER_SNAPSHOT_EXPECTED_FIELDS` parity would fail
   on the removed and added instance fields; ledger edit in task 4.
8. **Minor (fixed in revision 1):** red TaskMonitor tests must keep the
   existing `upsert_heartbeat` monkeypatch until task 4, or they would
   call `ensure_manager` at HEAD. Stated in §4 and task 3.
9. **Minor (fixed in revision 1):** promotion strategy D was claimed for
   [CC-2.5]/[CC-2.3] although task 4 code cites them; now strategy A with
   the linking step named (task 4 item 11) and the backstitch before/after
   gate in §7. The `hasattr` namespace assertion and the stop gate that
   fired on `tests/tasks/test_heartbeat.py:24` are removed.
10. **Blocking (fixed in revision 2):** revision 1's grid rule advanced
    the grid only at cycle finish, so a tick-started cycle saw its own
    tick as an overrun and every steady-state tick produced a second
    cycle 0.1 s later (simulation: 0/14 scenarios matched today). Fixed by
    consuming the tick at cycle start (task 4 item 4).
11. **Blocking (fixed in revision 2):** even with start-consumption, the
    built-in path's chained control-cleanup re-arm overwrote the owed
    follow-up with the next grid tick (3/14 scenarios differed). Fixed by
    the explicit `_overrun_follow_up_pending` flag cleared only when a
    cycle starts (task 4 item 5; test 3b; 14/14).
12. **Major (fixed in revision 2):** revision 1 named one test-fleet flip
    (:1749) that cannot occur and missed the nine that do. The flips were
    measured by applying L2 in a scratch worktree; the mechanism is the
    constructor's forced reconcile (`manager.py:495`), which also rules
    out post-construction stubs as a fix. The inventory, per-test fix
    pattern, and verification are in §3 and task 3 item 7.
13. **Major (fixed in revision 2):** the `pgrep` residue gate was unsound
    (`enable_process_title=False`) and system-wide; replaced by the
    harness-scoped `HarnessResidueWarning` gate; the harness join/cleanup
    overlap is named and fixed by a longer join.
14. **Major (fixed in revision 2):** the L2f instructions omitted the
    `_WEFT_OVERRIDE_RULES` entry (parity test fails; explicit `"0"` reads
    as true) and the harness lever they prescribed breaks 16 `broker_env`
    tests. Both corrected; L2f is now fully specified and was exercised in
    the scratch worktree.
15. **Minor (fixed in revision 2):** register row 12 carried L3 residue —
    deleted; the [MA-1] backlink now cites `[MA-1.6a]`, whose mapping at
    03:383 lists the edited function.
16. **Open:** Q1 (L2f recommended; pure L2 or L2b as owner overrides), Q2
    (floor), Q3 (invariant registry).

## 11. Open Owner Questions

- **Q1 — Launch model (recommended default: L2f now, L3 later).** Both
  round-1 reviewers converge on splitting: land the TaskMonitor
  decoupling on an always-desired heartbeat and decide the on-demand
  launch separately. Revision 1 recommended pure L2; revision 2 changes
  the recommendation to **L2f** (L2 plus `WEFT_HEARTBEAT_SERVICE_ENABLED`,
  default true) on measured evidence, stated plainly:
  - The shipping behavior is identical under L2 and L2f: one always-on
    heartbeat process per canonical manager even with zero registrants and
    even with both built-in monitors disabled (new in that configuration),
    reversing the 2026-04-17 lazy-start intent.
  - The test-fleet cost that revision 1 cited for L2f is **not** the
    deciding factor: measured at +7 s wall / +60 s CPU on the harness-backed
    suites (§3), zero residue, and the harness must not apply the flag as a
    fleet-wide environment override anyway (16 `broker_env` failures);
    per-test constructor overrides of the same key are the accepted seam.
  - The deciding factor is the nine `test_manager.py` flips (§3 table).
    Because `Manager.__init__` reconciles at construction (:495), the only
    seam that suppresses the heartbeat in a unit test is a constructor
    config key. Under L2f that is one key per test, the same seam those
    tests already use for the two monitor flags, verified green in a
    scratch worktree. Under pure L2, seven tests need the
    drain-plus-reconcile-stub pattern and :5148 (reconcile ordering) has
    no fix from existing seams — it would need a test-mode suppression in
    the product or a new seam, both forbidden by this plan.
  - L2f is a legitimate launch-policy switch, not a shim: both reviewers
    agree it is the same kind of flag as `WEFT_LIVENESS_MONITOR_ENABLED`,
    an operator can independently turn the endpoint off, and default-true
    preserves the new capability. Its cost is one public config key with
    five firing tests and one Quick Reference/README row.
  Overrides:
  - **L2** — no flag; accept the pure-L2 test pattern and the :5148 stop
    (task 5 sweep note).
  - **L2b** — keep the three gate sites mechanically as they are and
    respecify the sentence as "desired whenever any built-in monitor is
    enabled" (policy, not dependency). Zero code and test churn; keeps a
    coincidence rule in the spec.
  - **L3** — on-demand launch, follow-up plan (§9).
- **Q2 — Interval floor (recommended default: drop it).** The floor's
  only reason was the heartbeat registration minimum
  (`_parse_task_monitor_interval_seconds` docstring and `runtime.py:221`
  both name it). With no registration, a positive integer is the honest
  contract; the shipping default stays 300 and the catch-up interval is
  already an unbounded positive float. Reviewer-raised risk: a 1 s
  interval runs the full cleanup cycle every second (operator load).
  Alternative: `TASK_MONITOR_MIN_INTERVAL_SECONDS = 60` as a monitor-local
  sanity bound (task 6 names the edit). Either way the Quick Reference
  row text in the delta is adjusted before promotion.
- **Q3 — Invariant registry (recommended default: not in this plan).**
  Codex asks whether "no service uses another service's queue message as
  its clock" should become a numbered invariant (new [IMPL.x] or an
  [IMPL.10] extension) with firing coverage for each built-in service.
  Recommended: leave it as the normative [CC-2.5] paragraph, pinned for
  TaskMonitor by task 3 and already true by construction for Heartbeat
  and LivenessMonitor; open a spec-authoring follow-up if the owner wants
  a registry row.

## Observable-Difference Register

Classes: A owner-decided removal, N none, C no-loss change, D diminution
(must be fixed in-plan or escalated). Verified against the shipping
defaults (`WEFT_TASK_MONITOR_ENABLED=1`, `WEFT_LIVENESS_MONITOR_ENABLED=1`,
interval 300, catch-up 2) and against the both-disabled configuration
where it differs.

| # | Observable | Before | After | Class | Verification |
|---|-----------|--------|-------|-------|--------------|
| 1 | TaskMonitor periodic cadence | heartbeat grid anchored at registration (≈ first cycle start + dispatch latency); ad hoc pokes do not move it; overrun → one follow-up; if the heartbeat is down, drifts to finish+300 | local grid anchored at first cycle start; pokes do not move it; overrun → one follow-up; never drifts | C (anchor differs by sub-second registration dispatch latency; the heartbeat-down drift is removed) | task 3 items 1-3 |
| 2 | TaskMonitor reactor blocking on `ensure_heartbeat_service` (≤0.5 s, retried each 1 s while heartbeat down) | present | gone | C | task 4 grep gate |
| 3 | PONG `extended.task_monitor.heartbeat` block | present (4 fields) | absent | A | item 4 |
| 4 | PONG `last_cycle.error` carrying heartbeat registration failures | present | never | A | item 6 deletion; `_last_error = None` |
| 5 | Inbox message wakes a cycle | yes | yes | N | existing :1757 |
| 6 | Heartbeat process with shipping defaults | one | one | N | existing :1924, :4455 |
| 7 | Heartbeat process with both monitors disabled | none | one always-on (L2f default / L2); none under L2f with the flag off or under L2b | A — owner decision Q1 | item 7 both-disabled test |
| 8 | `weft status` heartbeat row with both monitors disabled | `disabled`/`config-disabled` | `unknown`/`none` or live evidence; `enabled: true` (`config-disabled` only with the L2f flag off) | C (follows row 7) | item 8; L2f `config-disabled` test |
| 9 | First external registration latency | immediate (service pre-started) | immediate | N (round-0 row moot under L2) | unchanged helper |
| 10 | Heartbeat crash with registrations | manager restarts an empty service | same | N (round-0 row moot under L2) | existing ensure lifecycle tests |
| 11 | `WEFT_TASK_MONITOR_INTERVAL_SECONDS=1..59` | rejected | accepted (Q2 default) | C (capability increase); error text for 0 keeps the variable name | item 9 |
| 13 | `weft.spawn.internal` authors | manager only | manager only | N | unchanged |
| 14 | Admission reserve floor | `3 + liveness` | unchanged | N | existing [MANAGER.18] tests |
| 15 | Manager idle shutdown waits for a missing heartbeat | yes | yes | N | existing :5489, :3362 kept |
| 16 | Heartbeat emitter/singleton behavior and tests | — | untouched | N | `git diff --stat tests/tasks/test_heartbeat.py` empty |
| 17 | Test fleet: harness foreground managers supervise a heartbeat child | no | yes under L2 and L2f (no under L2b) | N for the product; measured +7 s wall / +60 s CPU, zero residue (§3) | task 5 residue gate and timed run |
| 18 | `WEFT_HEARTBEAT_SERVICE_ENABLED=0` (L2f only) | key unknown (passed through by `load_config`) | no heartbeat desired; status `config-disabled`; no effect on a running service or the admission reserve; when **no live heartbeat endpoint exists** an external registrant's `upsert_heartbeat` still calls `ensure_heartbeat_service` (`heartbeat.py:193-219`): it returns an existing live endpoint at :200-202 if one remains, otherwise it calls `ensure_manager()` and, after that returns, waits up to `MANAGER_STARTUP_TIMEOUT_SECONDS` (10 s) for an endpoint that the flag prevents, then raises `RuntimeError` — the operator-visible failure mode when the endpoint is switched off; pinned by `test_heartbeat_registration_fails_with_flag_off` (task 3 item 7) | C (capability increase: operator can turn the endpoint off) | task 3 item 7 L2f tests |
| 19 | Canonical `Manager` constructed with both monitors disabled writes a heartbeat spawn request at construction (`:495`) | no (nothing desired) | yes (L2 / L2f default) | N for the product (same construction-time reconcile path every desired service already uses); test-fleet effect in row 17 and §3 | measured flip inventory |
| 20 | TaskMonitor cycle-start sequence versus today's heartbeat grid in the 14 simulated scenarios (steady state, poke, overrun, catch-up, post-start, chained cleanup) | today's sequence | identical | N | `sim_cadence_r2b.py` 14/14; tests 1-3b |

No D rows remain. Row 7 is class A pending the owner's Q1 answer.

## Review Record (append-only)

**2026-09-08 — round 1: Codex (cross-model, BLOCKED, 12 findings) and
Claude-family reviewer (PASS with fixes, 7 findings + assessments (a)/(b)),
against HEAD `178e3a34` and the round-0 plan.** Every finding was
reproduced against the code before disposition (probe:
`scratchpad/plans/06-rev/probe-anchors.sh`). Dispositions:

| Finding | Reproduced | Disposition | Section changed |
|---------|------------|-------------|-----------------|
| Codex 1 — helper-authored envelope on `weft.spawn.internal` violates [MANAGER.15] manager authorship / reducer-before-side-effects | Yes (`manager.py:4440`, `:6855`; 07:809) | Out of scope → L3 follow-up; listed as blocker (1) | §9; §4 constraints |
| Codex 2 / Claude 3 — register row 1 hid a cadence diminution; start-anchored rule lets a poke shift the grid | Yes (`heartbeat.py:381-390` grid; `task_monitor.py:5076/:5462` finish-anchored) | Accepted: grid-anchored rule as exact [MF-5] text; matrix + poke + overrun tests; row 1 reclassified with the exact residual difference | Spec Delta [MF-5]; task 3 items 1-3; task 4 items 2, 4, 5; register row 1 |
| Codex 3 — concurrent first registrations lose an acknowledged registration | Yes (L3 design) | Out of scope → L3 blocker (3) | §9 |
| Codex 4 — one-shot demand request not durable across reservation loss | Yes (`manager.py:4247`) | Out of scope → L3 blocker (4) | §9 |
| Codex 5 — boolean `cancel_heartbeat` hides uncertain liveness | Yes (`heartbeat.py:113`, `:246`) | Out of scope → L3 blocker (5); `heartbeat.py` now unmodified | §9; register row 12 |
| Codex 6 — four owner decisions open in an implementation plan | Yes | Accepted: Q1 resolved to L2 default with overrides; Q2 recommends dropping the floor with rationale; round-0 Q3/Q4 moot with L3 removed; new Q3 (invariant registry) added per Codex "raise for human review" | §11 |
| Codex 7 — post-idle status cannot be produced | Yes (`system.py:791`, `:1099`) | Out of scope → L3 blocker (7); no status vocabulary change in this plan | §9; register row 8 |
| Codex 8 / Claude 1 — "red" test already green (drain independent of reconcile) | Yes (`manager.py:6593`, `:6621`) | Accepted: test deleted with L3; replaced by genuinely red gate tests (both-disabled desire; `missing_active_tid`) | task 3 item 7 |
| Codex 9 — strategy D misapplied to sections code will cite; no backstitch before/after gate; reciprocal mappings unnamed | Yes (writing-plans.md:425-449; 07:712) | Accepted: [CC-2.5]/[CC-2.3] → strategy A with the linking step in task 4 item 11; Quick Reference stays D with the reason stated; exact backstitch command from prior plans added to §7 with before/after comparison in tasks 2 and 7; reciprocal mappings named per slice; `heartbeat.py`/`manager_services.py` no longer touched so no mapping for them | Class line; Spec Delta table; task 4 item 11; task 7; §7 |
| Codex 10 — task 5 stop gate fires on `tests/tasks/test_heartbeat.py:24` | Yes (also `test_task_monitoring.py:13`, `test_constants.py:33`) | Accepted: stop gate restricted to production modules; the three test files exempt by name | task 6 |
| Codex 11 — anchors/counts: mapping :397-400; close at :54-55; pending test :634; assertions to :631; 516 lines; 113 sites; `__all__` :264; [MANAGER.16] not quoted; cite [CLI-1.2.1] | Yes, all nine | Accepted: every anchor corrected; [MANAGER.16] and 10-CLI dropped from the delta (unchanged under L2) | §2, §3, Spec Baseline |
| Codex 12 — `hasattr` namespace assertion is ceremony | Yes | Accepted: removed; import removal proven by the grep gate | task 3 item 1; task 4 |
| Codex "raise for human review" — clock rule not in the invariants registry; 60 s floor operator-load risk | Yes | Accepted as owner questions Q3 and Q2 | §11 |
| Claude 2 — `_reconcile_managed_services` :5869 returns before any pending scan; demand scan ordering underspecified | Yes | Moot with L3 removed; carried to the follow-up plan | §9 |
| Claude 4 — rows 7/9 (first-registration latency; crash restart) need owner confirmation | Yes | Moot under L2: both unchanged; rows kept as N with the note | register rows 9-10 |
| Claude 5 — [MA-1] :257-258 "manager enqueues services through its own inbox" contradicted by the [MANAGER.16] append | Yes | Moot under L2: the clause stays true; no [MANAGER.16] append | Spec Delta |
| Claude 6 — [MF-3.2] mapping anchor :397-400 | Yes | Accepted (same as Codex 11) | Spec Baseline |
| Claude 7 — demand scan on every convergence pass | Yes | Moot with L3 removed; carried to the follow-up plan | §9 |
| Claude (a) — L2 recommended for the split; [MA-1] item 7 sentence must be reworded to "always desired by canonical public managers"; reverses a week-old sentence and the 2026-04-17 intent; L2 cannot idle-exit | Yes | Accepted: L2 default; sentence reworded; reversal stated plainly in §1; idle exit disabled stays | §1, Spec Delta [MA-1] |
| Claude (b) — :3362 keys `missing_active_tid` on the heartbeat state; would fail under the round-0 edit 5 | Yes | Accepted: under L2 edit 5 is gone; :3362 and :5489 stay green unmodified and are named as the "always desired" pins | §3, §4, register row 15 |

New evidence found while reproducing (not raised by either reviewer):
`tests/helpers/weft_harness.py:671-672` disables both monitors for every
harness and `ensure_foreground_manager` (:211, :228) runs a canonical
manager, so L2 puts a heartbeat child in every harness-backed foreground
manager and flips `tests/core/test_manager.py:1749`. Recorded in §4, task
5 (stop gate), Q1 (L2f/L2b), and register row 17.

**2026-09-08 — round 2: Codex (cross-model, FAIL, F1-F5) and
Claude-family reviewer (FAIL, R2-1..R2-5), against HEAD `178e3a34` and
revision 1.** Every finding was reproduced before disposition. Probes
(session scratchpad `plans/06-rev2/`): `sim_cadence_r2b.py` (extends the
reviewer's `reviews/sim_cadence_r2.py` with the chained control-cleanup
re-arm and the follow-up flag; rerun of the original: every row DIFF for
the revision-1 rule, SAME with start-consumption); a scratch git worktree
at `178e3a34` with the three-line L2 edit, then the L2f wiring
(`test_manager_l2_failures.txt`; `run_fleet.sh`, `fleet_head.log`,
`fleet_l2.log`, `residue.log`). Dispositions:

| Finding | Reproduced | Disposition | Section changed |
|---------|------------|-------------|-----------------|
| Codex F1 (P1) — grid fix loses its follow-up on the built-in path: `_finish_monitor_cycle` arms `due=now`, the chained control-cleanup result (`:5345/:5454`) re-arms to the future grid | Yes: read `:5345-5355` and `:5454-5462`; simulation rows "F1: …" differ 3/14 without a flag | Accepted: explicit `_overrun_follow_up_pending` flag set at any overrun, honored by every re-arm, cleared only when a cycle starts; matrix rows for the chained re-arm; new built-in-cycle test 3b; 14/14 with the flag | task 4 items 2, 4, 5, 9; task 3 items 3, 3b; [MF-5] bullet; §4 |
| Claude R2-1 (P1) — `overran = grid <= now` at finish with the grid consumed only at finish: a tick-started cycle sees its own tick and every steady-state tick yields a second cycle 0.1 s later | Yes: reviewer's simulation rerun, every matrix row and steady state DIFF | Accepted: consume the tick at cycle start (`while grid <= start`) after the one-time anchor; post-start matrix rows `(120, False, 60.1, …)` and `(120, False, 90, …)`; test 1 asserts the cap after cycle 3; simulation 14/14 | task 4 item 4; task 3 items 1, 3; [MF-5] bullet; register row 20 |
| Codex F3 (P2) / Claude R2-2 (P1) — `:1749` cannot flip (reconcile stubbed at `:1725`); ~20 both-disabled real-convergence tests unaccounted; task-5 stop rule would fire | Yes: `:1725` stub confirmed; measured under L2: exactly nine tests flip (§3 table), seven of the reviewer's candidates do not | Accepted: `:1749` edit withdrawn; full measured inventory with per-test assertion and subject; mechanism identified (`Manager.__init__` reconciles at `:495`, so post-construction stubs cannot intercept); accepted fix pattern stated for L2f (constructor config key, verified green) and for pure L2 (drain + reconcile stub; `:5148` unfixable → stop) | §3, §4, task 3 item 7, task 5 item 6 |
| Codex F2 (P2) — `pgrep -fl "heartbeat-service"` unsound (`enable_process_title=False`) and system-wide | Yes: `manager.py:4723`, `base.py:2173`; `launcher.py:170` spawn entry carries no title | Accepted: harness-scoped gate — `cleanup()` warns `HarnessResidueWarning` with the exact TID-mapping PIDs still alive before force-kill; gate runs the harness suites with `-W error::…HarnessResidueWarning`; baseline instrumented run recorded zero residue | task 5 item 5 and residue gate; §7 |
| Claude R2-4 (P3) — `_stop_inline_managers` joins 2.0 s while the serve thread's cleanup runs `_terminate_children` under a 2.0 s deadline; double-cleanup race | Yes: `weft_harness.py:657-664`, `_constants.py:586`, `base.py:1039-1050`; second `cleanup()` is guarded at `base.py:989-993`, but the PID sweep overlaps the running termination | Accepted: join raised to `TASK_CLEANUP_TIMEOUT_SECONDS + 3.0`; named in §4 | §4; task 5 item 5 |
| Claude R2-3 (P2) — cost numbers: 39 call sites in 7 files (not 14 files); estimate ~0.5-1.5 s × 39 plus CLI-autostarted managers; quantify with a timed run | Yes: 39 sites / 7 files / 25 harness-importing files confirmed | Accepted and measured: HEAD 1 m 41.5 s vs L2 1 m 48.5 s wall (+60 s CPU) on 12 workers; timed run required at closeout; one load-sensitive CLI test named | §3; task 5 timed run; §7; register row 17 |
| Codex F4 (P2) — L2f instructions omit `_WEFT_OVERRIDE_RULES`; parity test fails; `load_config({...: "0"})` keeps `"0"` → true | Yes: `_constants.py:3223-3226`, `test_constants.py:106`, `:143` | Accepted: three-site wiring (default, loader, boolish rule) with firing tests for env and explicit-override inputs; exercised in the scratch worktree (constants/status/manager green) | §3 constants; task 5 item 0; task 3 item 7 |
| Q1 honesty (Codex and Claude) — the unaccounted flips make L2f materially cheaper | Yes | L2f is now the recommended default, with the evidence (the deciding factor is the constructor-time reconcile and `:5148`, not the fleet cost, which measured small); the harness must not set the flag as a fleet-wide env override (16 failures measured); per-test constructor overrides of the same key are the accepted seam | §1, Class line, §11 Q1, task 5, [MA-1] delta, Quick Reference delta |
| Codex F5 (P3) — register row 12 carries an L3 disposition | Yes | Accepted: row 12 deleted | register |
| Claude R2-5 (nit) — backlink cites [MA-1] item 7; reciprocal mapping is [MA-1.6a] :383 | Yes: 03:383 lists `_reconcile_managed_services` and `_build_heartbeat_spawn_payload` | Accepted: comment cites `[MA-1.6a]` first, then [MA-1] item 7 and [MF-3.2] | task 5 item 1 |

Reviewer-verified items carried unchanged: the three L2 gate sites; the
two idle-shutdown tests; strategy-A linking; the backstitch command; the
anchor corrections.

## Revision Log

| Date | Change | Reason | Re-review needed |
|------|--------|--------|------------------|
| 2026-09-08 | Revision 5 (non-material): fifth cleanup spy in the residue ordering test; `upsert_heartbeat(startup_timeout=…)` passed directly in the flag-off registration test. | Round-5 review (Codex FAIL R5-1, R5-2) — see Review Record | Scoped round-6 verification |
| 2026-09-08 | Revision 4 (non-material): residue ordering test named and homed in `tests/test_harness_registration.py`; lever wording reconciled in §3/§4/Q1; register row 18 qualified with a flag-off registration firing test. | Round-4 review (Codex FAIL R4-1..R4-3) — see Review Record | Scoped round-5 verification |
| 2026-09-08 | Revision 3 (non-material): residue gate reordered (cleanup first, deferred warn, helper test under an error filter); README instructions reconciled to task 5's single L2f bullet; parametrized ids corrected; lever wording; register row 18 failure mode. | Round-3 review (Codex FAIL R3-1/R3-2; Claude PASS R3-1..R3-4) — see Review Record | Scoped round-4 verification |
| 2026-09-08 | Revision 2. Material changes: (1) cadence rule — grid tick consumed at cycle start plus an explicit overrun follow-up flag that survives the chained control-cleanup re-arm; [MF-5] bullet reworded; matrix extended with post-start and chained rows; new built-in-cycle test 3b; simulation 14/14 against today; (2) test-fleet inventory measured in a scratch worktree (nine `test_manager.py` flips, mechanism `Manager.__init__:495`, per-test fix pattern, `:1749` edit withdrawn); (3) residue gate replaced by the harness-scoped `HarnessResidueWarning` gate on TID-mapping PIDs, join timeout raised above the cleanup deadline, cost measured (+7 s wall) with a required closeout timed run; (4) L2f completed (three `_constants.py` sites incl. the boolish override rule, env and explicit-override firing tests, Quick Reference/README rows, [MA-1] item 7 flag sentence) and promoted to the recommended Q1 default with the evidence; register row 12 deleted, rows 18-20 added; [MA-1.6a] cited as the reciprocal key. | Round-2 review (Codex FAIL F1-F5; Claude FAIL R2-1..R2-5) — see Review Record | Yes, **scoped round-3 verification** (§8): cadence rule + simulation, test-fleet inventory and fix pattern, residue gate, L2f completeness and Q1 |
| 2026-09-08 | Revision 1. Material changes: (1) on-demand launch model L3 removed from scope — tasks, tests, deltas ([MANAGER.16] append, [MF-3.2] helper-launch bullets, 10-CLI status vocabulary), and register rows deleted; named as a follow-up plan with the five round-1 blockers; (2) launch model L2 (heartbeat always desired by canonical public managers; [MA-1] item 7 reworded; three gate sites edited) is the recommended default with Q1 overrides L2f/L2b; (3) cycle cadence changed from start-anchored to grid-anchored with exact [MF-5] text and a matrix test; (4) promotion strategies corrected (A for [CC-2.5]/[CC-2.3]), backstitch before/after gate added; (5) anchors, counts, stop gates, and the already-green test corrected; (6) harness blast radius under L2 recorded with a process-residue stop gate. | Round-1 review (Codex BLOCKED; Claude PASS with fixes) — see Review Record | Yes: changes the launch model, cadence contract, spec delta, and register; re-enters review before promotion |

**2026-09-08 — round 3 (scoped, against revision 2): Codex (cross-model,
FAIL, R3-1 P2 + R3-2 P2) and Claude (PASS, R3-1 P2 + R3-2 P3 + two
nits).** Both reviewers re-ran the cadence simulation (14/14), verified
`manager.py:495`, the nine-test inventory, the L2f wiring, and L3
confinement. Dispositions (applied as revision 3):

| Finding | Reproduced | Disposition | Section changed |
|---------|------------|-------------|-----------------|
| Codex R3-1 / Claude R3-1 (P2) — residue warning emitted before force-kill; under `-W error` the warn raises and skips every cleanup step | Yes (`weft_harness.py:620-640`) | Accepted: cleanup chain runs first, deferred warn after, `try/finally`; helper test under `simplefilter("error")` | task 5 item 5 |
| Codex R3-2 (P2) — README instructions contradict (task 5 adds a bullet; task 7 and §9 say unchanged) | Yes | Accepted: task 7 and §9 now say "confirm the L2f bullet; no other README changes" | task 7, §9 |
| Claude R3-2 (P3) — parametrized ids `[None]`/`[active_records1]`, not `[2]` | Yes | Accepted | §3 table |
| Claude R3-3 (nit) — "flag as a lever vs constructor key" wording (the key is the flag) | Yes | Accepted: fleet-wide env override vs per-test constructor override | Review Record round-2 row, Q1 wording |
| Claude R3-4 (nit) — row 18 omits the external registrant's failure mode with the flag off | Yes | Accepted: 10 s `RuntimeError` via `ensure_heartbeat_service` named | register row 18 |

Revision 3 is non-material (harness ordering, doc consistency, ids,
wording). Scoped round-4 verification requested.

**2026-09-08 — round 4 (scoped, against revision 3): Codex (cross-model,
FAIL, R4-1 P2 + R4-2 nit + R4-3 P2).** README and parametrized-id fixes
verified real; cleanup ordering verified correct. Dispositions (applied as
revision 4):

| Finding | Reproduced | Disposition | Section changed |
|---------|------------|-------------|-----------------|
| Codex R4-1 (P2) — residue ordering test has no named test or collectable file; `tests/test_harness_registration.py` missing from inventories | Yes | Accepted: named test, file added to task 5 inventory, `catch_warnings` scoping, full cleanup call order asserted | task 5 files, item 5 |
| Codex R4-2 (nit) — "lever vs constructor key" wording survives in §3, §4, Q1 | Yes | Accepted: fleet-wide environment override vs per-test constructor override of the same key | §3, §4, §11 Q1 |
| Codex R4-3 (P2) — row 18 failure mode overstated (`ensure_heartbeat_service` returns an existing live endpoint at :200-202) and unverified by any listed test | Yes | Accepted: row qualified ("when no live heartbeat endpoint exists", wait starts after `ensure_manager()`), firing test added to task 3 item 7 | register row 18, task 3 item 7 |

Revision 4 is non-material. Scoped round-5 verification requested.

**2026-09-08 — round 5 (scoped, against revision 4): Codex (cross-model,
FAIL, R5-1 P2 + R5-2 P3; R4-2 verified fixed).** Dispositions (applied as
revision 5):

| Finding | Reproduced | Disposition | Section changed |
|---------|------------|-------------|-----------------|
| Codex R5-1 (P2) — ordering test omits `_wait_for_database_files_releasable` (`weft_harness.py:642`) from the real chain | Yes | Accepted: fifth spy added, order asserted against `:620-665` | task 5 item 5 |
| Codex R5-2 (P3) — monkeypatching `MANAGER_STARTUP_TIMEOUT_SECONDS` does not shorten `upsert_heartbeat` (default bound at definition, `heartbeat.py:221`) | Yes | Accepted: pass `startup_timeout=0.05` directly | task 3 item 7 |

Revision 5 is non-material. Scoped round-6 verification requested.

**2026-09-08 — round 6 (scoped, against revision 5): Codex (cross-model,
PASS; R5-1 and R5-2 verified, no new defect).** Together with the Claude
round-3 PASS against revision 2 (whose findings were all applied in
revisions 3–5), independent review of the plan and its Proposed Spec Delta
is complete from two agent families. The plan is review-clean pending the
Open Owner Questions (Q1 launch policy L2f/L2/L2b, Q2 interval floor, Q3
invariant registry).

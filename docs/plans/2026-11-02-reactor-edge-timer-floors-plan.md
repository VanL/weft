# Reactor Edges: Replace Timer Floors with Readiness Waits

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.2.1], [CC-2.5], [CC-3.1], [CC-3.5]; docs/specifications/06-Resource_Management.md [RM-5.1], [RM-5.2]; docs/specifications/07-System_Invariants.md [IMPL.8], [IMPL.9]; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/14-Python_API_Surfaces.md [PY-1]
Superseded by: none

Class: 5. A risky trigger fires. The change touches the durable spine on its
session paths (`Consumer` interactive sessions and persistent agent sessions
under `TaskRunner`). The same reactor-edge rule also runs through two
executing contexts: the service tasks (Manager, TaskMonitor and
LivenessMonitor worker groups) and the task sessions. The CLI control-surface
site (`weft/commands/tasks.py`, Task 2) is deferred to the owner (see "Owner
Decisions") and is not executed by this plan. So the hardening checklist
applies, and review comes before implementation. The class is 5: no spec
sentence in [CC-2.2.1], [CC-2.5], [RM-5.2] or anywhere else in
`docs/specifications/` sanctions any of these floors (see Spec Baseline), but
the sampling rule the owner chose on 2026-11-03 for persistent agent sessions
(sample at the monitor's `polling_interval`, D12) extends an [RM-5.1]
sentence that today governs only interactive command sessions, and that
extension is one normative sentence (see `## Proposed Spec Delta`). Every
other change brings code into line with text that already governs:
[CC-2.2.1] "no task-reactor wait cap exists" for local events, [CC-3.1] the
activity callback, [RM-5.1] limit sampling as clock work, and [CC-3.5] the
handoff inputs. The remaining spec edits are `## Related Plans` backlinks,
which are not normative.

Plan type: implementation with spec revision (one [RM-5.1] sentence,
strategy B, atomic with Task 4). Hardening: applies.

## 1. Goal

Four reactor edges wait on a fixed 10 ms or 50 ms timer where a real event
source already exists. Each one wakes when nothing has happened, and none
names the event it is standing in for. That breaks the theory's
"Event-driven against absence" test ([WT-7]) and the [WT-4] 4.3 rows "One
reactor, one arbiter, a closed set of inputs" and "Subprocess and stream
supervision is readiness-driven, not timer-polled" (`docs/program-theory.md`;
refused alternatives [ALT-WT-002] and [ALT-WT-021]). This plan moves each wait
to its real source. Where an absent event is real, the timer stays and the
reason goes in the constant's docstring. The theory sentences are the
acceptance tests: the theory stands, and the code moves. One edge, the CLI
control-surface cap, turned out at review to cover an absent event with no
portable notification; it is deferred to the owner rather than removed (Task
2, "Owner Decisions" item 2).

| Site (verified at b7eed9f9) | [WT-7]: which event cannot arrive, and why? | Disposition |
|---|---|---|
| `weft/core/tasks/service.py::ServiceWorkerContext.iter_items`, `input_queue.get(timeout=LOCAL_WORKER_QUEUE_OPERATION_TIMEOUT_SECONDS)` (line 70) | **None.** A worker can block in `get()` only on an empty queue. Stop always delivers one `_service_worker_stop` sentinel per worker through `ServiceTask._stop_service_worker`, which `ServiceTask._cleanup_task_resources`, `ServiceTask._abort_partial_initialization` and the start-failure unwind in `ServiceTask._start_service_worker` call. One gap exists today: if the cleanup deadline has already expired, the sentinel loop breaks before it tries to put (lines 456-459). Task 1 closes that gap with a non-blocking put attempted first. | Blocking `get()`; sentinel `put_nowait` before the deadline check |
| `weft/core/tasks/service.py::ServiceTask._stop_service_worker`, `drain=True` branch, `_worker_result_event.wait(timeout=...)` (line 496) | Moot. **No caller exists.** `grep -rn "drain=True" weft tests` returns 0 lines, and nothing under `../engram` or `../taut` references `_stop_service_worker` (see Execution log). | Delete the unreachable `drain` parameter and branch |
| `weft/commands/tasks.py::_await_control_surface`, `interval=CONTROL_SURFACE_WAIT_INTERVAL` cap (line 1975) | **One input is read but not watched.** The status read can return terminal evidence from the one-shot outbox (`weft/core/task_evidence.py::known_tid_evidence` → `_configured_outbox_evidence`), and `_ControlSurfaceResources` does not watch the outbox. Every other input is a watched queue: the task-state queue, `ctrl_out` and the pipeline status. One non-queue input gates the outbox read: `known_tid_evidence` consults the outbox only when `runtime_evidence` finds no live host-pid runtime handle, so a process exit can make an already-written outbox row eligible without any queue write (see the fresh-eyes finding in "Owner Decisions"). The two grace windows after a public signal or a KILL ack are real absence detection: the observer gives up waiting for a terminal row that may never come. Cross-family round 1 confirmed that the process-exit gate is reachable on the normal host path (see the finding under "Owner Decisions"), so the 50 ms cap currently covers an input that has no queue event. | **Deferred, owner decision** (Task 2 kept in place, not executed). Default: retain the cap as a named absent-event timer |
| `weft/core/tasks/interactive.py::InteractiveTaskMixin._interactive_drain_remaining`, `time.sleep(INTERACTIVE_OUTPUT_DRAIN_POLL_INTERVAL)` (line 374) | **None for runner sessions.** The host runner's stream readers publish the chunk or the `None` EOF sentinel and then call the required `on_activity` callback ([CC-2.2.1] names them as adapters; [CC-3.1]). An owner-local session (the built-in Debugger's `InProcessCommandSession`) has no asynchronous producer, so there is nothing to wait for. | Wait on a task-local `threading.Event` that the activity callback sets; skip waiting for owner-local sessions |
| `weft/core/tasks/interactive.py::InteractiveTaskMixin._interactive_shutdown`, `time.sleep(min(INTERACTIVE_STOP_POLL_INTERVAL, remaining))` (line 530) | **None.** The host runner's exit waiter (`weft/core/runners/host.py`, `_wait_for_exit` inside `start_session`) records exit and then calls `on_activity`. | Same event, bounded by the existing stop-grace deadline |
| `weft/core/tasks/sessions.py::AgentSession._terminal_handoff_wait_seconds`, `now + ACTIVE_CONTROL_POLL_INTERVAL` (line 734) | **Real for cancellation only.** `AgentSessionProtocol.execute(work_item, *, cancel_requested=...)` ([PY-1], `weft/ext.py`) takes a pull-style `Callable[[], bool]`; it has no waitable. Producer exit has a kernel sentinel (`BaseProcess.sentinel`) that can join the channel wait. Timeout and drain are deadlines. Resource sampling is clock work: today it runs on every live pass (about every 50 ms, including the first pass), a cadence set by the wait loop and stated nowhere. It moves to the monitor's `polling_interval` ([RM-5.1], 1.0 s by default), the rule interactive sessions and the subprocess runner already follow: a session-scoped next-sample instant, checked on every observation pass and published as a deadline of the wait, so while a work item is being supervised the interval bounds the requested wait (a ceiling) and the spacing between samples as measured after each sample (a floor); between work items no observation pass runs and no sample is taken, and an overdue instant is honored on the next work item's first observation pass (D12, owner ruling 2026-11-03; Owner Decisions item 5). | Join the process sentinel to the wait; keep the 50 ms term only while `cancel_requested is not None` (the `weft/core/runners/subprocess_runner.py::_supervision_wait_timeout` precedent); give a monitored session one session-scoped next-sample instant at its monitor's `polling_interval`, sampled on any pass once due and published as a wait deadline, retired while the producer is dead; an unmonitored session carries no sampling timer |

Out of scope, already aligned (not touched): the launcher parent-loss watcher
(`weft/core/launcher.py::_start_parent_loss_watcher`), the Windows 63-handle
wait slice, and `QueueChangeMonitor`'s 1 s shutdown check
(`QUEUE_CHANGE_MONITOR_WAITER_TIMEOUT_SECONDS`).

## 2. Source Documents

Source specs (governing, unchanged by this plan):

- `docs/specifications/01-Core_Components.md` [CC-2.2.1] ("Every task wait
  input is a backend event, a local event or a timer … no task-reactor wait cap
  exists for them … A wait cap may be removed only after every such source it
  was covering has a named adapter. Current adapters are the host runner's
  interactive stream readers and exit waiter …"), [CC-2.5] (worker-result
  channel), [CC-3.1] (the interactive activity callback contract), and [CC-3.5]
  (the private terminal handoff reducer: producer exit and channel are separate
  inputs; drain is a deadline)
- `docs/specifications/06-Resource_Management.md` [RM-5.1] (the default poll
  cadence is 1.0 s, `spec.polling_interval`; "Stream, control, backend, and
  process exit activity do not move the resource-sampling deadline or cause
  additional samples", a sentence that governs interactive command sessions
  and that this plan extends to persistent agent sessions by one added
  sentence; see D12 and `## Proposed Spec Delta`) and
  [RM-5.2] (readiness, not a fixed timer; a private drain budget)
- `docs/specifications/07-System_Invariants.md` [IMPL.8], [IMPL.9]
  (service-worker input queues are broker-free local Python queues)
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5] (known-TID
  evidence ordering that `_await_control_surface` reads)
- `docs/specifications/14-Python_API_Surfaces.md` [PY-1] (`CommandSessionProtocol`
  "gains no notification registration or wait method"; the
  `AgentSessionProtocol.execute` signature)

Theory (the acceptance tests): `docs/program-theory.md` [WT-2a.6], [WT-4] §4.3
(the rows "One reactor, one arbiter, a closed set of inputs", "A quiet task does
nothing", "Subprocess and stream supervision is readiness-driven, not
timer-polled") and §4.4 ("Measure; do not bake in inefficiency", "No capability
diminution"), [WT-7] "Event-driven against absence", [ALT-WT-002], [ALT-WT-021].

Audit rows this plan closes:
`docs/plans/2026-11-02-program-theory-reconciliation-plan.md` (status
`completed`): appendix rows 4.3-a (the service-task site only; the
control-surface site is deferred to the owner, Task 2; the heartbeat clock in the same row belongs to that plan's cleanup plan
6) and 4.3-c (interactive and agent-session sites), plus "Rows that need the
owner" item 6 (the PostgreSQL native idle return), which this plan only
measures (Task 0).

Precedent to copy, not to reinvent:
`docs/plans/2026-10-29-pr48-reactor-and-test-hygiene-repair-plan.md` (status
`completed`) and the shape it landed in
`weft/core/runners/subprocess_runner.py::_supervision_wait_timeout`. There the
cancel cadence applies only when `cancel_requested is not None`, the monitor
sample is a deadline, and `None` means waiting on readiness alone.

Local guidance: `CLAUDE.md` §1.1, §4.2 (imports), §4.4 (constants and their
docstrings), §5.1; `docs/agent-context/engineering-principles.md`;
`docs/agent-context/runbooks/hardening-plans.md`;
`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`.

## 3. Context and Key Files

### Current structure (what each site does today)

- **Service workers.** `ServiceTask._register_service_worker` creates one
  `queue.Queue` per worker group. `ServiceTask._run_service_worker_thread`
  builds a `ServiceWorkerContext` and calls the target once. Five targets read
  `context.iter_items()`:
  `weft/core/manager.py::Manager._run_child_launch_service_worker`,
  `weft/core/tasks/liveness_monitor.py::LivenessMonitor._run_probe_worker`, and
  three TaskMonitor targets in `weft/core/monitor/task_monitor.py`. Only the
  LivenessMonitor probe workers are long-lived
  (`LIVENESS_MONITOR_MAX_IN_FLIGHT_PROBES` = 8 workers, input queue maxsize 8).
  The Manager and TaskMonitor targets return after their first item, and their
  groups are started per work item with that item already queued
  (`initial_items`), so they never idle in `get()`. Today each idle probe worker
  thread wakes every 50 ms to re-check `stop_requested()`. Stop is
  `ServiceTask._stop_service_worker`: it sets the group `stop_event`, then puts
  one sentinel per worker. On `queue.Full` it evicts one item and retries under
  the cleanup deadline.
- **CLI control wait.** `weft task stop` and `weft task kill`
  (`_stop_resolved_tasks` and its kill counterpart in `weft/commands/tasks.py`)
  call `_await_control_surface`. That function opens a `_ControlSurfaceResources`,
  which is a `weft/core/queue_wait.py::QueueChangeMonitor` over the state queue,
  `ctrl_out`, and the pipeline status queue. Each loop pass reads all evidence
  (`_observe_control_envelopes`, `mapping_for_tid`, `_task_status`), then waits
  for `_control_surface_wait_timeout(..., interval=CONTROL_SURFACE_WAIT_INTERVAL)`,
  which is never more than 50 ms.
- **Interactive sessions.** `weft/core/tasks/consumer.py::Consumer._handle_work_message`
  passes `on_activity=self._strategy.notify_activity` into
  `_interactive_maybe_handle_message` and then `_interactive_ensure_session`.
  That function calls `runner.start_session(on_activity=...)`. The host
  implementation (`weft/core/runners/host.py`, `start_session`) starts two
  reader threads and one `process.wait()` thread. Each publishes, then calls
  `on_activity`. The reactor turn (`Consumer._process_reactor_turn`) calls
  `_interactive_flush_outputs`. When the session is no longer alive, that calls
  `_interactive_drain_remaining` (sleeping 10 ms per pass, up to
  `INTERACTIVE_OUTPUT_DRAIN_TIMEOUT` = 0.25 s) and then
  `_interactive_finalize_session`. STOP and KILL
  (`_interactive_handle_control`) and cleanup (`Consumer._cleanup_task_resources`)
  call `_interactive_shutdown`. It closes stdin and loops on `is_alive()` with
  50 ms sleeps, up to `INTERACTIVE_STOP_GRACE_SECONDS`. Cleanup runs after the
  drive loop has ended, so no arbiter is waiting then. That is why
  `_interactive_shutdown` must keep blocking, on a real event.
- **Persistent agent sessions.** The Consumer worker lane
  (`Consumer._run_task_for_reactor`) calls
  `AgentSession.execute(work_item, cancel_requested=self._worker_cancel_requested)`.
  `execute` loops: it observes (cancel predicate, timeout, one non-blocking
  channel read, `poll_limits()` on every pass while alive), checks
  `is_alive()`, reduces, then waits in
  `weft/core/terminal_handoff_transport.py::poll_terminal_payload` for at most
  50 ms. So psutil runs a sample about every 50 ms during every persistent
  agent work item.

### Comprehension questions (answer in the execution log before the first edit; a wrong answer is a stop-and-re-read blocker)

1. After Task 1, `ServiceWorkerContext.iter_items` blocks in
   `input_queue.get()` with no timeout. Which code path guarantees that a
   blocked worker wakes when its task stops? Why does the guarantee hold even
   when the cleanup deadline has already expired?
   *Expected:* `ServiceTask._cleanup_task_resources` (and
   `_abort_partial_initialization`) call `_stop_service_worker(name,
   deadline=...)`. That sets the group `stop_event` and delivers one
   `_service_worker_stop` per worker; `iter_items` returns when it receives the
   sentinel. A worker can block only on an empty queue. Stop then adds at most
   `worker_count` sentinels, and every registered group has an unbounded queue
   (`input_queue_maxsize=0`) or a maxsize of at least `worker_count`
   (LivenessMonitor: 8 and 8), so each `put_nowait` that Task 1 adds before any
   deadline check succeeds while a worker is blocked. The deadline bounds only
   the retry loop for a full queue, and on a full queue no worker is blocked.
2. In `AgentSession.execute` after Task 4, why must the process sentinel leave
   the wait set once `producer_exited` is consumed? Which inputs still wake on
   a 50 ms clock, and why is each one legitimate? Why must the sampling
   deadline also leave the wait set while the producer is dead?
   *Expected:* The sentinel is level-triggered: it stays ready after exit. If
   it stayed in the set, every wait during the drain would return at once, a
   busy loop. Only one term keeps a 50 ms clock: the `cancel_requested`
   predicate keeps `ACTIVE_CONTROL_POLL_INTERVAL`, because the public
   `AgentSessionProtocol.execute` takes a pull-style `Callable[[], bool]` with
   no waitable, and Consumer always passes one (named absent event). The
   other timed term is not on a 50 ms clock: a monitored session keeps a
   limit-sampling deadline at its monitor's `polling_interval` (1.0 s by
   default), because sampling is clock work ([WT-7] "a timer is for clock
   work") and [RM-5.1] already gives interactive sessions and the subprocess
   runner that cadence (D12). While a work item is being supervised, that
   deadline bounds the requested wait, but it is not the only trigger: a
   sample also runs on any observation pass once the instant is due, and the
   next instant is armed from a clock reading taken after the sample, so
   samples are never spaced closer than one interval. Payload readiness,
   channel EOF and producer exit wake the wait. Timeout and drain are
   deadlines. `_observe_terminal_handoff` never samples a dead producer or
   after an accepted stop, so an expired sampling deadline in that state would
   never be rescheduled and every later wait would be zero-time until drain
   expiry; it therefore leaves the wait set (D14).

### Files and shared paths

Shared paths. Reuse these; do not duplicate them:

- `weft/core/runners/subprocess_runner.py::_supervision_wait_timeout` is the
  deadline-composition shape to mirror in `AgentSession`. Do not import it: it
  is private to that runner, and the session needs a `drain_deadline` term.
- `weft/core/terminal_handoff_transport.py` owns the private channel transport
  and its Windows `BrokenPipeError` normalization. The new wait helper goes
  there, beside `poll_terminal_payload`.
- `tests/tasks/test_runner.py::_spawn_agent_session_for_target` builds a real
  spawned process with a real pipe. Use it for AgentSession proofs.
- `tests/tasks/test_task_interactive.py` helpers `make_interactive_spec`,
  `_drive_interactive` and `_finished`, plus `tests/helpers/reactor_driver.py::drive_until`.
- `tests/commands/test_control_observation_connections.py` fixture shape: a
  `weft_harness` context, `tests/helpers/task_observation.py::write_task_observation`,
  and real `_await_control_surface`.
- `tests/helpers/weft_harness.py::WeftTestHarness` (through the `weft_harness`
  and `broker_env` fixtures in `tests/conftest.py`), which is backend-aware:
  `bin/pytest-pg` runs the same tests on PostgreSQL.

Abstractions not to add: no new watcher class, no adapter thread, no
notification method on `CommandSessionProtocol` ([PY-1] forbids one), no new
`TaskRunnerBackend` or `AgentSessionProtocol` parameter, no config knob, and
no new spec section. The only new code units are one `threading.Event`
attribute on the interactive mixin, one transport function, and one
session-scoped next-sample instant on `AgentSession` (Task 4). No new
constant: the sampling interval is the monitor's own `polling_interval`.
Task 2's `CONTROL_SURFACE_SIGNAL_GRACE_SECONDS` is deferred with Task 2.

## 4. Invariants and Constraints

Must not change:

- TID format, forward-only state transitions, reserved-queue policy,
  `spec`/`io` immutability, spawn-only process creation.
- [IMPL.8]/[IMPL.9]: service workers stay broker-free; worker results still
  reach the reactor only through `BaseTask._publish_worker_result` →
  `_strategy.notify_activity()`.
- [CC-3.5] reducer: same events, same per-policy same-turn precedence
  (persistent session: cancellation, timeout, ready payload, confirmed limit,
  transport failure, channel seal, producer exit, drain expiry). Producer exit
  and channel seal stay separate observations, and stop intents and
  `producer_exited` stay edge-triggered.
- [RM-5.2] and [CC-2.2.1] interactive semantics: drain stops as soon as both
  streams report EOF and is otherwise bounded by
  `INTERACTIVE_OUTPUT_DRAIN_TIMEOUT` (unchanged value). The stop grace stays
  `INTERACTIVE_STOP_GRACE_SECONDS` (unchanged). Final frames keep coming before
  the completion fact and before the control ack.
- `_await_control_surface` return values and budgets: `CONTROL_SURFACE_WAIT_TIMEOUT`
  (2.0 s) overall, and the public-signal and KILL-ack grace windows keep their
  present 0.05 s length and precedence (see
  `tests/commands/test_task_commands.py::test_control_surface_wait_timeout_preserves_three_clock_precedence`).
- The [PY-1] public protocols in `weft/ext.py` are unchanged.
- Values of `INTERACTIVE_OUTPUT_DRAIN_TIMEOUT`, `INTERACTIVE_STOP_GRACE_SECONDS`,
  `TERMINAL_HANDOFF_DRAIN_TIMEOUT_SECONDS`, `CONTROL_SURFACE_WAIT_TIMEOUT` and
  `ACTIVE_CONTROL_POLL_INTERVAL`. Only the docstring of
  `ACTIVE_CONTROL_POLL_INTERVAL` changes. No sampling constant is added: a
  monitored persistent session samples at its monitor's `polling_interval`
  (D12, owner ruling 2026-11-03).
- Persistent-session limit sampling: the first observation pass of a session
  with a live producer and no accepted stop samples at once; every later pass
  samples when the next sample instant is due; after each sample the instant
  is re-armed to one `polling_interval` past a clock reading taken after the
  sample; the instant is a deadline of the session's wait. Sampling runs only
  inside `execute()`, so the guarantee holds while a work item is being
  supervised: the deadline bounds the requested wait (the interval is a
  ceiling on that wait), and the spacing between samples, measured after
  each sample, is at least one interval (a floor). This is not an exact
  wall-clock maximum gap: a descheduled process can lengthen a gap. Between
  work items the producer is alive but no observation pass runs, so no
  sample is taken then (Owner Decisions item 5). A new work item neither
  resets nor postpones the instant: eligibility carries across items, so an
  instant that fell due between items is honored on the first observation
  pass of the next work item.
- `weft/commands/_spawn_submission.py` keeps `CONTROL_SURFACE_WAIT_INTERVAL`
  untouched (see §9).

Hidden couplings:

- Series plan 3 (`docs/plans/2026-11-03-control-replies-only-on-request-plan.md`,
  T7) removes the STOP/KILL acknowledgement rows and renames
  `test_interactive_command_control_unwinds_before_terminal_and_ack` to
  `test_interactive_command_control_unwinds_before_terminal`. Task 3's
  shutdown test therefore reuses only that test's output-before-terminal
  ordering assertions, never its acknowledgement assertion, so it is valid
  whichever plan lands first.
- `_interactive_shutdown` runs both inside a reactor turn (STOP or KILL) and
  after the drive loop ends (`Consumer._cleanup_task_resources`). The new wait
  must not depend on the arbiter running.
- The Debugger (`weft/core/tasks/debugger.py::Debugger._interactive_ensure_session`)
  overrides session creation, discards `on_activity`, and sets
  `_interactive_runner = None`. It never sets the new event, and its session
  goes not-alive synchronously in `close_stdin()`/`send()`.
- `_ControlSurfaceResources` acquisition order is pinned by
  `tests/commands/test_control_observation_connections.py::test_control_resources_close_watcher_before_all_queues`.
- Fakes in `tests/tasks/test_runner.py` model processes without a `sentinel`
  and receivers that expose only `poll()`. `multiprocessing.connection.wait()`
  rejects a poll-only object (`ValueError: Invalid file object`, cross-family
  round 1 probe), so any fake that reaches the new wait, including dead-producer
  fakes once drain begins, must migrate. Task 4 step 5 lists every one by
  file:line.

Fatal versus best-effort: none of these waits gains or loses a failure mode.
A transport `OSError`/`ValueError` from the new helper is consumed exactly as
`poll_terminal_payload` failures are today (`pending_transport_failure`).

Review gates: no new execution path; no drive-by refactor (the discovered
sibling floors in §9 stay untouched); no new dependency; no public API or CLI
shape change; no mocks of queues, processes, sessions or reactor waits. The
spies allowed are delegating counters wrapped around the real object; they
count calls (or record the requested wait budget) and then call through. One
bounded exception: the pre-existing fake-clock unit tests listed in Task 4
step 5 already use a fake receiver whose `poll(timeout)` is their fake wait;
the migration moves that same fake to the new wait boundary one-for-one. No
new test adds such a fake.

### Difference register

| # | Behavior | Before | After | Status |
|---|---|---|---|---|
| D1 | Idle service-worker thread wakes | 20/s per idle worker (at b7eed9f9 only the 8 LivenessMonitor probe workers idle) | 0 | changed (removes cost) |
| D2 | Worker exit after the task `_stop_event` is set but before cleanup | within 50 ms, by poll | at cleanup, on the sentinel; the thread is idle in `get()` meanwhile and holds no work | changed (timeliness only; no work is lost, and exit still precedes `_stop_worker_lanes`) |
| D3 | Sentinel delivery with an expired cleanup deadline and a non-full queue | not delivered (the loop breaks first) | delivered by `put_nowait` | strengthened |
| D4 | `_stop_service_worker(drain=True)` | exists, no caller | removed | retired (unreachable) |
| D5 | Control-surface status reads while nothing changes | one every 50 ms for up to 2 s | unchanged in this plan | deferred with Task 2 (owner decision; was "one per watched-queue wake, plus one at each deadline") |
| D6 | Control surface seeing a one-shot outbox terminal row written mid-wait | within 50 ms, by poll | unchanged in this plan | deferred with Task 2. The outbox watch does not stand on its own while the cap stays: it would shave at most 50 ms from the poll, it buys no coverage of the process-exit window (cross-family F2), and it brings D7's cost |
| D7 | Control-surface wakes from one-shot stream frames during a stop wait | none (the outbox is not watched) | unchanged in this plan | deferred with Task 2 (would be a new cost if the outbox were watched) |
| D8 | Interactive drain while an inherited descriptor holds the pipes open | 25 passes of 10 ms | 1 wait to the same 0.25 s budget | changed (removes cost; same latency) |
| D9 | Interactive drain for an owner-local (Debugger) session | 0.25 s of 10 ms passes on every session exit (the session has no `_stdout_closed`) | a single emit, no wait | changed (Debugger exit is 0.25 s faster) |
| D10 | Interactive shutdown while the child takes T seconds to exit after stdin closes | about T/0.05 passes | a wait per activity callback | changed (removes cost; latency to exit observation drops from ≤50 ms to wake latency) |
| D11 | Agent-session producer exit with `cancel_requested=None` | seen within 50 ms | seen on the sentinel | preserved (faster). Only callers that pass `cancel_requested=None` reach this; `Consumer._run_task_for_reactor` always passes a predicate, so in production the 50 ms pass stays (D13), sampling follows D12, and Task 4's production effect is the sentinel wake on producer exit plus the readiness cleanup |
| D12 | Agent-session psutil samples while the producer is alive | on the first observation pass of each `execute()` and on every later live pass (about every 50 ms, a cadence set by the wait loop and stated in no spec) | once on the session's first observation pass with a live producer, then whenever at least the monitor's `polling_interval` (1.0 s by default, `DEFAULT_POLLING_INTERVAL`) has elapsed since the last sample, checked on every observation pass and published as a deadline of the wait; the instant is session-scoped and not restarted per `execute()`, so short work items cannot starve sampling. Sampling runs only while a work item is being supervised (inside `execute()`); between work items no sample is taken, and an instant that fell due then is honored on the next work item's first observation pass (Owner Decisions item 5). While a work item is supervised, a memory, descriptor or connection violation is caught at the session's first sample or at the next sample about one interval later; a cold sustained CPU violation (five samples, four violating; `weft/core/resource_monitor.py::ResourceMonitor._is_sustained_cpu_violation`) becomes detectable at the fifth sample, which with an immediate first sample lands near four intervals (samples at 0, I, 2I, 3I, 4I), about 4 s at the default, instead of about 0.2 s | **changed: a diminution accepted by the owner (2026-11-03)**. The interval is now the one [RM-5.1] names, the same rule and cadence as interactive command sessions and the subprocess runner (`_supervision_wait_timeout`), and the one knob a user can see (`spec.polling_interval`); the 50 ms rate was never chosen as policy. Sampling is clock work ([RM-5.1], [WT-7]); an unmonitored session carries no sampling timer. Owner Decisions item 1 records the ruling |
| D13 | Agent-session cancel observation | about 50 ms | about 50 ms (unchanged; this is the named absent event) | preserved |
| D14 | Sampling deadline while the producer is dead or a stop is accepted | no sample (the `is_alive()` and `accepted_stop` guards in `_observe_terminal_handoff`); the 50 ms cap paces the wait | no sample, and the sampling deadline leaves the wait set, so an expired, never-rescheduled deadline cannot cause zero-time waits until drain expiry | preserved (cross-family F3; the prohibition on sampling dead producers is kept). The race between the `is_alive()` guard and `check_limits()` (a producer can die between the two calls) is pre-existing at b7eed9f9 and unchanged by this plan |

## 5. Tasks

Tasks 1, 3 and 4 are independent of each other. Task 2 is deferred to the
owner (marked in place below so line references stay valid; see "Owner
Decisions") and is not executed. Task 0 comes first, and Task 5 comes last.
Land each executed task as its own commit, staged by an explicit file list.

### Task 0. Baseline measurements and the PostgreSQL quiet-task measurement (no code change)

- Outcome: before-numbers for D1, D8, D10 and D12 on SQLite and
  PostgreSQL (D5 and D7 only if the owner reactivates Task 2), plus an answer method for owner item 6. Everything is recorded
  in this plan's `## Execution log`.
- Files to touch: this plan only (the execution log). Measurement scripts are
  throwaway and live outside the repository; paste their source and output into
  the log.
- Read first: `tests/multiqueue_polling_benchmark.py` (backend env setup with
  `tests.helpers.test_backend.prepare_project_root`, `BROKER_TEST_BACKEND`,
  `WEFT_PG_TEST_DSN`); `weft/core/tasks/multiqueue_watcher.py::MultiQueueWatcher._wait_for_activity_body`
  and `_activity_hint_action`; `.venv/lib/python3.14/site-packages/simplebroker/watcher.py`
  `PollingStrategy.wait_for_activity` (installed SimpleBroker 8.7.2:
  `_native_idle_poll_interval = max(1.0, self._max_interval * 10)` at line
  1386, and the idle return at lines 1447-1449).
- Measurements (run each on SQLite, then on PostgreSQL with the DSN that
  `bin/pytest-pg --keep-container` provisions or an existing `WEFT_PG_TEST_DSN`):
  1. **Idle service tree (D1).** In a fresh project root, run
     `./.venv/bin/weft manager start --context <root>` and read the manager PID
     from `weft manager list --json --context <root>`. Then sample
     `psutil.Process(pid)` and `children(recursive=True)` for
     `num_ctx_switches().voluntary` and `cpu_times()`, 30 s apart, with no work
     submitted. Record the deltas per process. Stop the manager with
     `weft manager stop --context <root>`.
  2. **Control surface (D5, D7). Deferred with Task 2;** run it only if the
     owner chooses option (b) or (c) under "Owner Decisions". Use the fixture shape of
     `tests/commands/test_control_observation_connections.py::test_control_observation_reuses_physical_connections`:
     a running persistent task snapshot and no terminal. Call
     `task_cmd._await_control_surface(ctx, tid, timeout=2.0)` with delegating
     counters on `_ControlSurfaceResources.wait` and `task_cmd._task_status`.
     On PostgreSQL, also count `psycopg.Cursor.execute` calls with a
     delegating counter. For D7, run `weft run --no-wait --stream-output` on a
     one-shot command that prints a line every millisecond, then
     `weft task stop <tid>`. Record the status-read count during the stop wait.
  3. **Session loops (D8, D10, D12).** Record the counts that the red tests of
     Tasks 3 and 4 assert. Get the numbers by running those tests before the
     implementation edits, with `-s` and a temporary print of the counter.
  4. **Owner item 6 (the measurement only, not a change).** The question: on
     PostgreSQL, does a quiet Weft task run a turn when SimpleBroker's native
     idle poll returns? Method: build a PostgreSQL project root, construct an
     in-process `weft.core.tasks.Consumer` for a persistent function task, and
     write nothing to its queues. Wrap these with delegating counters: the
     instance's `_process_reactor_turn`; `MultiQueueWatcher._has_pending_messages`;
     and `simplebroker.watcher.PollingStrategy.wait_for_activity` (count returns).
     Run `task.run_until_stopped()` on a thread for 30 s, then `task.stop()`.
     Record turns after the first, pending-message probes, and strategy
     returns. Repeat on SQLite as the control. Reading the code predicts no
     policy turns, and about one `_has_pending_messages()` broker probe per
     native idle interval (about 1.0 s at the default `MAX_INTERVAL` of 0.1),
     because `_activity_hint_action` returns `"continue"` after the probe finds
     nothing. That is a prediction, not a result. Record what the counters
     show, and leave the classification (whether "A quiet task does nothing"
     holds on PostgreSQL) to the owner.
- Constraints: no repository code change and no committed script.
- Done when: every number above sits in the execution log with the commit SHA
  it was taken at.

### Task 1. Service workers block on their input queue (D1 to D4)

- Files to touch: `weft/core/tasks/service.py`, `tests/tasks/test_service_task.py`.
- Read first: §3 "Service workers"; `ServiceTask._stop_service_worker`,
  `_cleanup_task_resources`, `_abort_partial_initialization`;
  `weft/core/tasks/base.py::BaseTask._stop_worker_lanes`; [IMPL.8], [IMPL.9].
- Red tests (write first; they must fail at b7eed9f9 for the stated reason):
  - `test_idle_service_worker_does_not_wake_until_stopped` (new). Register a
    group whose target loops `for item in context.iter_items(): ...`. Before
    `_start_service_worker`, replace
    `task._service_worker_registrations[name].input_queue` with a
    `queue.Queue` subclass that counts `get` calls, records each requested
    `timeout`, sets a `threading.Event` (`entered_get`) on entry, and calls
    through. Start the group with no items. **Positive setup:** assert
    `entered_get.wait(2.0)` is true before anything else, so the worker is
    proven to be inside `get()`. Then hold it idle for 0.5 s. **Observable:**
    exactly one `get` call (zero fails the test; today it is about 10), and
    its recorded `timeout` is `None` (today 0.05; the recorded budget, not
    the 0.5 s window, is the deterministic red). Then call
    `task.stop(timeout=2.0)` and assert that every thread in the registration
    is no longer alive (latency bound: within the stop call).
  - `test_stop_delivers_sentinel_after_cleanup_deadline_expired` (new).
    Use the same counting queue and assert `entered_get.wait(2.0)` before
    stopping, so the worker is proven blocked in `get()`. Call
    `task._stop_service_worker(name, deadline=time.monotonic() - 1.0)`.
    **Observable:** the worker thread exits within 2.0 s (`join`). At
    b7eed9f9 this passes only because of the 50 ms poll, so it is the guard
    for the order of the two edits below. Run it after removing the timeout
    and before adding `put_nowait`: it must fail then.
- Implementation:
  1. `ServiceWorkerContext.iter_items`: keep the `stop_requested()` loop-head
     check and replace the timed `get` with `self.input_queue.get()`. Delete
     the `except thread_queue.Empty: continue` that becomes dead.
  2. `_stop_service_worker`: inside `for _ in range(worker_count):`, first try
     `input_queue.put_nowait(_service_worker_stop)` and `continue` on success.
     Only on `queue.Full` fall into the existing deadline-bounded put-and-evict
     loop. Leave that loop as it is (it runs only on a full queue, never at
     idle; see §9).
  3. Delete the `drain` keyword and the whole `if drain:` branch. Update the
     `record_stop` double in
     `tests/tasks/test_service_task.py::test_service_task_cleanup_passes_one_absolute_deadline_to_worker_groups`
     to drop its `drain` parameter.
  4. Leave `LOCAL_WORKER_QUEUE_OPERATION_TIMEOUT_SECONDS` in place. It is still
     used at `weft/core/tasks/service.py:651` (the abort budget) and in
     `weft/core/tasks/base.py::BaseTask._publish_worker_result`.
- Stop if: a target is found that reads `context.input_queue` directly
  instead of through `iter_items` (at b7eed9f9,
  `grep -rn "input_queue" weft/core/manager.py weft/core/monitor/task_monitor.py weft/core/tasks/liveness_monitor.py`
  matches only `input_queue_maxsize=` at `weft/core/tasks/liveness_monitor.py:140`);
  a caller of `drain=True` turns up; a group is registered with
  `0 < input_queue_maxsize < worker_count` (comprehension question 1 then no
  longer holds); or any existing test in
  `tests/tasks/test_service_task.py` needs an assertion weakened.
- Verify: `./.venv/bin/python -m pytest tests/tasks/test_service_task.py tests/tasks/test_liveness_monitor.py tests/core/test_manager.py -q`,
  then the same targets with `./bin/pytest-pg`.

### Task 2. The control-surface wait watches every input it reads (D5 to D7) — deferred

- **Status: not executed; ruled (a) by the owner on 2026-11-03 ("I think
  we keep it for now in Plan 2 and Plan 3"): the cap stays.** Task 2a
  (below this task's retained body) carries the one edit that does land:
  the docstring. Its own "Stop if" clause fired at review: cross-family round 1 (F2) showed that an
  input outside the watched queues and the outbox (host-pid liveness in
  `known_tid_evidence`) can turn a snapshot terminal on a reachable path, with
  no queue write. The one-shot outbox watch (edit 3) is deferred with it,
  because without the cap removal it buys at most 50 ms and adds D7's cost.
  The body below is kept unchanged so line references stay valid; it is the
  starting point for option (b) under "Owner Decisions". If reactivated, its
  red tests need the cross-family F5 strengthening (record the requested wait
  budget through a delegating wrapper of `_control_surface_wait_timeout`
  rather than counting waits over a timed window), and the outbox test must
  keep a live host-pid runtime handle so that it exercises F2's window rather
  than removing it.

- Files to touch: `weft/commands/tasks.py`, `weft/_constants.py`,
  `tests/commands/test_task_commands.py`,
  `tests/commands/test_control_observation_connections.py`.
- Read first: §3 "CLI control wait";
  `weft/core/task_evidence.py::known_tid_evidence` and
  `_configured_outbox_evidence`; `weft/commands/tasks.py::_ControlSurfaceResources`
  and `_control_surface_wait_timeout`; [MF-5].
- Red tests:
  - `test_await_control_surface_waits_on_queue_events_not_a_cadence` (new, in
    `tests/commands/test_control_observation_connections.py`, which is
    already `shared`). Use the fixture shape of
    `test_control_observation_reuses_physical_connections`: a running
    persistent function task snapshot and no terminal. Wrap the real
    `_ControlSurfaceResources.wait` with a delegating counter. Call
    `_await_control_surface(ctx, tid, timeout=1.0)`. **Observable:** the
    counted `wait` calls are ≤ 2 (today about 20), and the returned snapshot
    status is `running`.
  - `test_await_control_surface_wakes_on_one_shot_outbox_result` (new, same
    file). Write a one-shot (non-persistent) function task snapshot with no
    runtime handle. On a thread, after 0.3 s, write a final outbox result to
    `T{tid}.outbox`; read `weft/core/task_evidence.py::peek_final_outbox_evidence`
    and `weft/core/outbox.py::process_outbox_message` for the frame shape. Call
    `_await_control_surface(ctx, tid, timeout=5.0)`. **Observable:** the
    snapshot is terminal, and elapsed is < 2.0 s, which proves an event and
    not the deadline. This test passes at b7eed9f9 (because of the 50 ms poll)
    and must go red after edit 2 alone. Record that red run.
- Implementation:
  1. Add `CONTROL_SURFACE_SIGNAL_GRACE_SECONDS: Final[float] = 0.05` to
     `weft/_constants.py` beside `CONTROL_SURFACE_WAIT_TIMEOUT`. Its docstring
     must name the absence: after a public control signal or a KILL ack, the
     observer waits this long for a terminal row that may never be published,
     then returns the latest snapshot; this is absence detection ([WT-7]), not
     a wake cadence. In `_await_control_surface`, use it in place of
     `CONTROL_SURFACE_WAIT_INTERVAL` for the two grace deadlines, and drop the
     `CONTROL_SURFACE_WAIT_INTERVAL` import from `weft/commands/tasks.py`.
  2. `_control_surface_wait_timeout`: remove the `interval` parameter. Return
     the nearest active deadline: overall, else public-signal grace once
     overall has expired, capped by the KILL-ack grace while that is set.
     Keep the `None` returns exactly as they are. Update
     `test_control_surface_wait_timeout_preserves_three_clock_precedence`: drop
     its `interval=2.0` argument, and in the parametrized table only the three rows whose overall deadline is 15.0 with no KILL-ack
     deadline change, from `2.0` to `5.0`. Every other row keeps its expected
     value.
  3. `_ControlSurfaceResources.__init__` gains a required keyword
     `outbox_name: str | None`, opened and watched last (after `ctrl_out` and
     the pipeline status queue) when it is not `None`. `_await_control_surface`
     passes `config.outbox` when `config.mode == "one_shot"`, else `None`.
     Update `matches()` to compare it (it has no caller at b7eed9f9; keep it
     consistent with the constructor), and update the test call in
     `tests/commands/test_control_observation_connections.py::_close_control_resources_twice`
     (`outbox_name=None`). Add the outbox name to the expected `Counter` in
     `tests/commands/test_task_commands.py::test_await_control_surface_uses_queue_monitor`
     (its `_make_taskspec` task is one-shot).
  4. Update the monkeypatched constant in
     `test_await_control_surface_public_grace_outlives_expired_kill_ack` and
     `test_await_control_surface_does_not_promote_kill_ack_to_terminal` to
     `CONTROL_SURFACE_SIGNAL_GRACE_SECONDS`, keeping their assertions.
- Stop if: the D7 measurement (Task 0 item 2, repeated after this task) shows
  more status reads during a streaming stop than the baseline. Report the
  numbers and the fork (watch the outbox, or accept a deadline-bounded miss in
  the crash-between-writes window) before landing. Also stop if any other
  input outside the three watched queues and the outbox is found to turn a
  snapshot terminal.
- Verify: `./.venv/bin/python -m pytest tests/commands/test_task_commands.py tests/commands/test_control_observation_connections.py tests/commands/test_task_owned_observation.py -q`,
  then the same targets with `./bin/pytest-pg`.

### Task 2a. Name the retained 50 ms cap as an absent-event timer (owner ruling, 2026-11-03)

- Files to touch: `weft/_constants.py` (the `CONTROL_SURFACE_WAIT_INTERVAL`
  docstring only).
- Change: replace the docstring "Polling interval while waiting for task
  terminal state in CLI control flows." with one that names the absent
  event ([WT-7] "Event-driven against absence"): the cap bounds each pass
  of `_await_control_surface`'s queue-change wait so the observer re-reads
  task status at least every 50 ms, because one input it reads has no
  event: a task that writes its result row and dies before publishing its
  terminal state turns terminal only when its process is gone, and the
  death of a process that is not the observer's child produces no queue
  write (`weft/core/task_evidence.py::known_tid_evidence` consults the
  outbox only when no live host-pid runtime handle remains). Queue changes
  wake the wait sooner; the cap is the ceiling. Also say the same constant
  is reused as the public-signal grace length until series plan 3 removes
  the acknowledgement-driven windows, and that a platform process-exit
  watcher (owner decision 2, option (c)) would remove the cap.
- No behavior change, no test change; verify by
  `grep -n "CONTROL_SURFACE_WAIT_INTERVAL" -A12 weft/_constants.py` showing
  the new docstring and `./.venv/bin/ruff check weft/_constants.py`.

### Task 3. Interactive drain and shutdown wait on the activity callback (D8 to D10)

- Files to touch: `weft/core/tasks/interactive.py`, `weft/_constants.py`
  (delete `INTERACTIVE_OUTPUT_DRAIN_POLL_INTERVAL` and
  `INTERACTIVE_STOP_POLL_INTERVAL`), `tests/tasks/test_task_interactive.py`,
  `tests/core/test_debugger.py`, `tests/specs/test_reactor_architecture.py`.
- Read first: §3 "Interactive sessions"; [CC-2.2.1] (the interactive paragraph
  and the adapter paragraph); [CC-3.1]; `weft/core/runners/host.py`
  `start_session` (the `_reader` and `_wait_for_exit` threads);
  `weft/core/tasks/debugger.py`.
- Red tests (mark each `@pytest.mark.shared` so `bin/pytest-pg` runs it):
  - `test_interactive_drain_waits_once_while_descendant_holds_pipes` (new). Copy
    the setup of
    `test_interactive_parent_exit_finishes_while_descendant_holds_pipes`,
    including its synchronization (the atomic pidfile rename, waited on before
    driving, so the descendant is proven to exist and hold the pipes) and its
    `finally` cleanup (`psutil.Process(pid).kill()` and `wait`, then
    `task.cleanup()`). After the first `task.process_once()`, wrap
    `task._interactive_session.poll_stdout` on the live instance with a
    delegating counter, and replace the module attribute
    `weft.core.tasks.interactive.time` with a delegating namespace whose
    `sleep` records each requested duration and calls through (it patches
    only that module's name, not the global `time` module). Then
    `_drive_interactive(task, lambda: _finished(task))`. **Observable:** no
    `sleep` was requested by the interactive module (today 25 requests of
    0.01 s; the recorded budget is the deterministic red); counted
    `poll_stdout` calls are ≥ 1 and ≤ 8 (today ≥ 25); the descendant is still
    running after the drive (the drain really ran with the pipes held); and
    the status is `completed`.
  - `test_interactive_shutdown_waits_for_exit_callback` (new). The script is
    `import sys, time; print('ready', flush=True); sys.stdin.read(); time.sleep(0.5)`.
    Start the session and drive until the `ready` frame is observed (positive
    setup: the child is alive and inside `stdin.read()` before STOP). Install
    the same delegating `time` namespace as the drain test, wrap the live
    session's `is_alive` with a delegating counter, then send STOP through the
    control path the existing
    `test_interactive_command_control_unwinds_before_terminal_and_ack` uses
    (series plan 3, T7, renames it
    `test_interactive_command_control_unwinds_before_terminal` when it
    removes acknowledgements; use whichever name is current).
    **Observable:** the interactive module requested no `sleep` during the
    shutdown (today about ten requests of ≤ 0.05 s; the deterministic red);
    counted `is_alive` calls inside the shutdown are ≥ 1 and ≤ 12 (today about
    20: the loop head and `_interactive_flush_outputs` each call it on every
    50 ms pass for 0.5 s). The upper bound is a secondary check, not the
    proof: each woken pass still makes two calls,
    `_interactive_finalize_session` and the post-loop check add one each, and
    the reader-EOF wakes can arrive while the host exit waiter is still inside
    `Popen.wait()`, which makes `poll()` report alive. The terminal state is
    `cancelled`, and the final stdout and stderr frames come before the
    terminal envelope on `ctrl_out` and before the lifecycle terminal row
    (reuse that test's three output-before-terminal assertions:
    `stderr_finals[0] < terminal_timestamp`, `stdout_finals[0] <
    terminal_timestamp`, and `max(stderr_finals + stdout_finals) <
    lifecycle[0][1]`). Assert nothing about an acknowledgement: series
    plan 3 retires STOP and KILL acknowledgements, so the acknowledgement
    row is not an oracle here in either landing order (owner's outside
    reviewer, 2026-11-03). Cleanup: `task.cleanup()` in `finally`.
  - `test_debugger_exit_does_not_wait_out_the_drain_budget` (new, in
    `tests/core/test_debugger.py`). Monkeypatch
    `weft.core.tasks.sessions.InProcessCommandSession.poll_stdout` with a
    delegating counter at class level, enqueue `ping` with `close: True`, and
    run `dbg.process_once()`. **Observable:** the counted calls are ≥ 1 and
    ≤ 3 (today about 26); the outbox reply decodes to `command == "ping"` and
    `response == "pong"` (read as `test_debugger_ping` reads it); and the
    session is completed (`dbg._interactive_session is None`; if it finalizes
    only on the next flush, call `dbg._interactive_flush_outputs()` first, as
    `test_debugger_ping` does, and count across both calls),
    so a broken dispatch that never polls cannot pass. `dbg.cleanup()` in
    `finally`.
- Implementation:
  1. `_init_interactive`: add
     `self._interactive_activity = threading.Event()` and declare the
     attribute in the class annotations.
  2. `_interactive_ensure_session`: pass the runner a callback that runs
     `self._interactive_activity.set()` and then the received `on_activity()`.
     Do not change the `on_activity` parameter or the Debugger override.
  3. `_interactive_drain_remaining`: if `self._interactive_runner is None`
     (an owner-local session, the same test that `_interactive_session_is_monitored`
     uses), emit once and return. Otherwise loop until the existing deadline:
     `self._interactive_activity.clear()`; emit chunks; break once both closed
     flags are set; otherwise `self._interactive_activity.wait(remaining)`.
     Clear the event before reading, so that a publication after the read
     still wakes the wait.
  4. `_interactive_shutdown`: same clear → `_interactive_flush_outputs()` →
     `if not session.is_alive(): break` → `wait(remaining)` loop, bounded by
     `shutdown_deadline`. Everything after the loop is unchanged.
  5. Delete the two retired constants and their imports. Add both names to
     `RETIRED_REACTOR_CAPS` in `tests/specs/test_reactor_architecture.py`.
- Stop if: a session implementation outside `weft/core/runners/host.py` and
  `weft/core/tasks/sessions.py::InProcessCommandSession` is found that
  produces output without calling `on_activity` (the extension runners reject
  `start_session`; see the Execution log); any existing test in
  `tests/tasks/test_task_interactive.py` changes its ordering assertions; or
  the change seems to need a method on `CommandSessionProtocol`.
- Verify: `./.venv/bin/python -m pytest tests/tasks/test_task_interactive.py tests/core/test_debugger.py tests/specs/test_reactor_architecture.py tests/cli/test_cli_long_session.py -q`,
  then `./bin/pytest-pg tests/tasks/test_task_interactive.py tests/core/test_debugger.py`.

### Task 4. Agent-session handoff waits on the channel and the producer sentinel (D11 to D14)

- Files to touch: `weft/core/terminal_handoff_transport.py`,
  `weft/core/tasks/sessions.py`, `weft/_constants.py` (docstring of
  `ACTIVE_CONTROL_POLL_INTERVAL` only), `tests/core/test_terminal_handoff_transport.py`,
  `tests/tasks/test_runner.py`; `docs/specifications/06-Resource_Management.md`
  (the one [RM-5.1] Replacement block, applied in the same commit).
- Read first: §3 "Persistent agent sessions"; [CC-3.5] in full; [RM-5.1];
  `weft/core/runners/subprocess_runner.py::_supervision_wait_timeout` and its
  `next_monitor_at` scheduling; `weft/core/tasks/sessions.py::AgentSession.execute`
  and `_observe_terminal_handoff` (its `progress.accepted_stop is None and
  self.is_alive()` guard on `poll_limits()`);
  `weft/core/resource_monitor.py::ResourceMonitor._is_sustained_cpu_violation`
  (five samples, four violating).
- Separation of concerns (cross-family F1, then the owner ruling of
  2026-11-03). Task 4 changes readiness: the unconditional 50 ms wait goes,
  replaced by the channel plus the producer sentinel. It also sets the
  sampling policy explicitly, because the old cadence was a side effect of
  that wait: a monitored session samples at its monitor's `polling_interval`,
  the [RM-5.1] rule interactive sessions and the subprocess runner already
  follow. One session-scoped next-sample instant (held on the session, not
  restarted per `execute()`) is checked on every observation pass and joined
  to the wait as a deadline. While a work item is being supervised, the
  deadline bounds the requested wait (the interval is a ceiling on that
  wait) and the spacing between samples, measured from a clock reading
  taken after each sample, is at least one interval (a floor); a stream of
  short work items cannot postpone checks. Sampling runs only inside
  `execute()`: between work items no observation pass runs and no sample is
  taken, and an instant that fell due then is honored on the first
  observation pass of the next work item, because eligibility carries
  across items (Owner Decisions item 5). The session's first sample is immediate (the first
  observation pass with a live producer). An unmonitored session carries no
  sampling timer. Note for the owner: the interactive session
  (`weft/core/tasks/interactive.py`, `_interactive_next_limit_check_at` is
  first set to `now + polling_interval`) and the subprocess runner
  (`next_monitor_at = start_time + interval`) take their first sample one
  interval after start; the agent session's immediate first sample is the
  one remaining difference between the three, kept because it costs one
  sample and the owner's ruling named it.
- Shared descendant construction (used by the two sentinel tests below, so it
  is portable to spawn on POSIX and Windows): module-level, picklable target
  functions in the test module. The producer target starts a grandchild with
  `multiprocessing.get_context("spawn").Process(target=_hold_sender, args=(response_sender, ready_event))`,
  where `ready_event` comes from the same spawn context. The grandchild sets
  `ready_event` only after it is running (and so holds its duplicate of the
  sender, which spawn transfers while unpickling the arguments), then sleeps
  30 s. The producer waits on `ready_event` (timeout 10 s), writes the
  grandchild pid to a pidfile by atomic rename (as
  `tests/tasks/test_task_interactive.py::test_interactive_parent_exit_finishes_while_descendant_holds_pipes`
  does), and exits with `os._exit(0)`, which skips multiprocessing's atexit
  join of non-daemon children. The test waits for the pidfile before its
  assertions (positive setup: the sender is proven held by a live
  descendant) and, in `finally`, kills that pid with `psutil.Process(pid).kill()`
  plus `wait(timeout=5.0)`, ignoring `psutil.NoSuchProcess`, then closes the
  receiver. For the session test the producer first replies `ready` and
  takes the work item from the request queue, then starts the grandchild;
  pass the pidfile path into the target with `functools.partial` over the
  module-level function (picklable under spawn), since
  `_spawn_agent_session_for_target` passes only the two endpoints.
- Red tests:
  - `test_wait_terminal_payload_returns_on_producer_sentinel` (new, in
    `tests/core/test_terminal_handoff_transport.py`). Use the shared
    descendant construction; the producer sends nothing. **Observable:**
    `wait_terminal_payload(receiver, producer_sentinel=process.sentinel, timeout=5.0)`
    returns `True` in < 2.0 s, and `receiver.poll(0)` is then `False` (the
    channel stayed silent, so the sentinel woke the wait). **Mutation check:**
    the same call with `producer_sentinel=None` and `timeout=0.2` returns
    `False`. This is a new helper, so no baseline-red assertion is claimed for
    it; a missing-symbol import failure at b7eed9f9 is not readiness evidence.
  - `test_agent_session_silent_producer_waits_without_cadence` (new, in
    `tests/tasks/test_runner.py`, marked `shared`). Use
    `_spawn_agent_session_for_target` with no monitor and a target that
    replies `ready` (copy the closest existing target in that file), sleeps
    1.0 s per work item, then sends a result. Wrap the session instance's
    `_terminal_handoff_wait_seconds` with a delegating recorder (accepts
    `**kwargs`, records the returned budget, returns it): it exists before and
    after the edit, so the same test is red at b7eed9f9 and green after. Also
    wrap `_observe_terminal_handoff` with a delegating pass counter. Call
    `execute(item, cancel_requested=None)`. **Observable:** every recorded
    budget is `None` (today every one is 0.05 s; this recorded budget is the
    deterministic red, independent of the 1.0 s sleep); counted passes are
    ≥ 1 and ≤ 3 (today about 20); and the result is `ok`.
  - `test_agent_session_leaked_sender_still_drains_after_producer_exit` (new,
    guard). The producer uses the shared descendant construction and exits
    without a result; `cancel_requested=None`. **Observable:** `execute`
    returns status `error` with
    `diagnostics["handoff_event"] == "drain_expired"` in under 2.0 s (the
    drain budget, `TERMINAL_HANDOFF_DRAIN_TIMEOUT_SECONDS`, is 0.25 s), and
    the grandchild is still running at return (the channel really stayed
    open, so the sentinel, not EOF, ended the wait).
    It passes today (because of the 50 ms poll) and must go red if the floor
    is removed without the sentinel; run it red at that intermediate edit.
  - `test_agent_session_samples_limits_at_monitor_interval` (new, guard).
    Pass a monitor that subclasses
    `weft.core.resource_monitor.BaseResourceMonitor` (its abstract methods as
    no-ops returning a fixed `ResourceMetrics`), constructed with
    `polling_interval=0.05`, whose `check_limits` records a monotonic
    timestamp per call, returns `ok` for the first four calls and a violation
    on the fifth. The target replies `ready`, then holds each work item 5.0 s
    before replying (the known live window). Call
    `execute(item, cancel_requested=None)`, so the cancel cadence cannot mask
    a missing sampling deadline. **Observable:** status `limit`; exactly five
    `check_limits` calls (zero or fewer than five fails); the first recorded
    before the first wait; and `execute` returns in < 2.0 s (five samples at
    0.05 s is about 0.2 s, far inside the 5.0 s window, so the ceiling held).
    The fast interval is the monitor's own, which is what the test proves the
    session reads. This test asserts no gap between samples: its producer is
    silent and `cancel_requested` is `None`, so its only wakes are the
    sampling deadline itself, and a session that sampled on every pass would
    also record five samples about 0.05 s apart. The floor is proven by the
    short-items test below. Mutation caught: a session with no sampling
    deadline. It takes two checks, one immediately and one on the
    result-bearing pass around 5 s, and returns `ok`, so the `limit`, the
    five-call and the < 2.0 s assertions fail. This is the red run at the
    intermediate edit that removes the unconditional 50 ms term without
    adding the deadline.
  - `test_agent_session_short_items_cannot_starve_sampling` (new, guard). Same
    monitor shape (recording a monotonic timestamp per `check_limits` call)
    with `polling_interval=0.2`, always `ok`. The target holds each work item
    0.02 s, far shorter than the interval, then replies. Run 20 consecutive
    `execute()` calls on one session with `cancel_requested=None` (about
    0.4 s to 0.6 s of live producer in all). Time each `execute()` call.
    **Observable:** at least two `check_limits` calls; every gap between
    consecutive recorded sample timestamps at least 0.18 s; and every result
    `ok`. The gap assertion is the floor proof: each item's ready and result
    wakes give observation passes about every 0.02 s, so a session that
    samples on every pass records gaps far under 0.18 s. Mutations caught:
    sampling on every pass fails the gap assertion. Under the rejected shape,
    in which the next-sample instant restarts at every `execute()`, the
    session takes exactly one sample (the immediate first) over all 20 items,
    provided every item finished inside one interval, and fails the
    two-call assertion. When that mutation is run red, write the recorded
    per-`execute()` durations to the execution log as evidence that the
    prerequisite held; the durations are evidence, not an assertion, so a
    slow CI run cannot fail the test for the wrong reason.
- Implementation:
  1. `weft/core/terminal_handoff_transport.py`: add
     `wait_terminal_payload(receiver: Connection, *, producer_sentinel: int | None, timeout: float | None) -> bool`.
     It calls `multiprocessing.connection.wait([receiver, producer_sentinel]`
     (the sentinel only when it is not `None`) `, timeout)` and returns whether
     anything is ready. It normalizes `BrokenPipeError` to `True`, as
     `poll_terminal_payload` does, and carries a docstring citing [CC-3.5].
     Keep `poll_terminal_payload`; other callers use it
     (`weft/core/runners/host.py`).
  2. No new constant. The sampling interval is the monitor's own
     `polling_interval`, an attribute every `BaseResourceMonitor` sets in its
     constructor ([RM-5.1], "one documented keyword constructor accepting the
     current limits and polling interval"); the host runner constructs the
     session's monitor with `polling_interval=self._monitor_interval`
     (`weft/core/runners/host.py` lines 589, 1141 and 1192 at b7eed9f9), which
     carries `spec.polling_interval`. `AgentSession` reads
     `self._monitor.polling_interval` and nothing else; a monitor without
     that attribute is a protocol violation and raises `AttributeError`, as
     a missing `check_limits` would (no fallback, no default).
  3. `AgentSession.__init__`: add a session-scoped
     `self._next_limit_sample_at: float | None = None`. It is never reset at
     the start of `execute()`.
  4. `AgentSession._observe_terminal_handoff`: take `sample_limits: bool`
     and call `poll_limits()` only when it is true, inside the existing
     `progress.accepted_stop is None and self.is_alive()` guard (that
     prohibition on sampling a dead or stopping producer is kept, D14).
     Before calling `poll_limits()`, read
     `interval = float(self._monitor.polling_interval)`: `poll_limits()`
     (`weft/core/tasks/sessions.py` lines 189 to 200 at b7eed9f9) calls
     `stop_monitor()` on any exception from `check_limits()` or
     `last_metrics()`, which sets `self._monitor = None`, and then returns
     `(True, None)`, so the monitor may be gone after the call. After the
     call, re-arm `self._next_limit_sample_at = time.monotonic() + interval`
     only if `self._monitor is not None` (the monitor survived the sample).
     The clock reading is taken after the sample, not the pass's earlier
     `now`, as both precedents do
     (`weft/core/runners/subprocess_runner.py` line 266,
     `next_monitor_at = time.monotonic() + interval` after the check;
     `weft/core/tasks/interactive.py` line 299, the same); this is what makes
     the interval a floor on sample spacing. If the monitor is gone, leave
     the instant as it is: `sampling_active` (step 5) is recomputed on every
     pass and is false without a monitor, so the instant drops out of the
     wait and is never compared again.
  5. `AgentSession.execute`: on every observation pass, pass
     `sample_limits=sampling_active and (self._next_limit_sample_at is None or now >= self._next_limit_sample_at)`,
     where `sampling_active` is true only while a monitor exists, no stop is
     accepted, and `"producer_exited"` is not in `progress.consumed_edge_kinds`.
     The `None` branch is the session's immediate first sample; there is no
     per-`execute()` first-pass sample. Sampling eligibility
     (`sampling_active`) is recomputed before composing each wait, not cached
     across the sample, so a monitor that `poll_limits()` stopped during the
     pass (step 4) removes the sampling deadline from that same wait.
     Compute the deadline contribution to the wait only when `sampling_active`
     and `self._next_limit_sample_at is not None`; otherwise it leaves the
     wait set (passed as `None`), so an expired deadline that will never be
     rescheduled cannot produce zero-time waits until drain expiry
     (cross-family F3). The explicit `None` handling covers the case the
     round-2 reviewer found: a monitored producer already dead on the first
     pass takes no sample (the step 4 guard), so `_next_limit_sample_at` stays
     `None`; without the guard the next pass would evaluate `now >= None`.
     With it, the session samples again only if the producer is alive on a
     later pass, and never while it is dead. Wait with
     `wait_terminal_payload(self._response_receiver, producer_sentinel=..., timeout=wait_for)`.
     Pass `self._process.sentinel` only while `"producer_exited"` is not in
     `progress.consumed_edge_kinds`, else `None`.
  6. `AgentSession._terminal_handoff_wait_seconds`: take `cancel_requested`
     and `next_sample_at: float | None` (the active sampling deadline or
     `None`), and return `float | None`. Use the `ACTIVE_CONTROL_POLL_INTERVAL`
     term only when `cancel_requested is not None`, plus the timeout, the
     drain deadline and `next_sample_at`. Return `None` when none applies.
  7. `ACTIVE_CONTROL_POLL_INTERVAL` docstring: the re-check cadence for a
     pull-style `cancel_requested` predicate, which cannot wake a readiness
     wait because the runner and session protocols expose no waitable
     cancellation ([WT-7]); used so by `AgentSession` and
     `subprocess_runner._supervision_wait_timeout`. It must also say,
     accurately, that `weft/core/runners/host.py::HostTaskRunner._terminal_handoff_wait_seconds`
     still uses it as an unconditional observation floor, a remaining site
     recorded for an owner follow-up (§9, Owner Decisions item 4), and not
     describe that use as replaced.
  8. Existing test doubles that must migrate (inventoried by lookup at
     b7eed9f9; keep every behavioral assertion; move fake waits and failure
     injection to the real wait boundary, `sessions_module.wait_terminal_payload`):
     - `tests/tasks/test_runner.py:3742`
       `test_agent_session_producer_exit_starts_bounded_drain`: `DeadProcess`
       (no `sentinel`) and the poll-only `EmptyReceiver` (line 3772) whose
       `poll(timeout)` advances the fake clock. Once drain begins, the new
       wait is reached and `multiprocessing.connection.wait()` rejects the
       receiver. Migrate: monkeypatch `sessions_module.wait_terminal_payload`
       with a function that advances the same fake clock by the requested
       `timeout` and returns `False` (the old fake wait, moved one-for-one).
       Keep `drain_expired` and `clock["now"] == TERMINAL_HANDOFF_DRAIN_TIMEOUT_SECONDS`.
     - `tests/tasks/test_runner.py:3797`
       `test_session_stop_after_begin_drain_preserves_first_deadline`:
       `EmptyReceiver` (line 3828); same migration; keep its assertions.
     - `tests/tasks/test_runner.py:4122`
       `test_session_stop_effect_cannot_reset_absolute_drain_deadline`: a live
       `Process` fake (no `sentinel`) and `EmptyReceiver` (line 4155). Same
       migration if it reaches the wait; if it reads the sentinel, give the
       fake a `sentinel` attribute (any int; the boundary is replaced). Keep
       `cancelled` and `clock["now"] == 1.0`.
     - `tests/tasks/test_runner.py:3716`
       `test_agent_session_blocking_poll_failure_is_consumed_as_next_turn_transport_failure`:
       injects through `_FailingFirstBlockingPollConnection` (line 3335) on a
       `poll(timeout > 0)` that the new wait bypasses, and the wrapper has no
       `fileno()`. Migrate: keep the real spawned session and restore the real
       receiver; monkeypatch `sessions_module.wait_terminal_payload` with a
       delegating wrapper that raises `OSError("blocking terminal poll failed")`
       on its first call and calls through afterwards, and assert the
       wrapper's own failed flag. Keep the `transport_failed` diagnostics, the
       message, and the closed-session assertion. Leave the class for the host
       test at line 3504 (`weft/core/runners/host.py` is not changed).
     - `tests/tasks/test_runner.py:3670`
       `test_agent_session_response_read_failure_is_transport_failure`
       (`_FailingResponseReadConnection`, line 3355) and
       `tests/tasks/test_runner.py:3695`
       `test_agent_session_windows_broken_pipe_is_channel_seal`
       (`_WindowsSealedResponseConnection`, line 3372): the wrappers expose no
       `fileno()`. If the first reduced step is not terminal, the next wait
       raises `ValueError`. Migrate: add a `fileno()` that delegates to the
       wrapped receiver (harmless for the host test at line 3425, which polls).
       Keep their assertions.
     - `tests/tasks/test_runner.py:1360`
       `test_agent_session_does_not_poll_limits_after_producer_exit`:
       `DeadProcess` (no `sentinel`), poll-only `SealedReceiver` (line 1386),
       `LateLimitMonitor` (line 1396). It is the existing guard for D14
       (`check_calls == 0`). If it reaches the wait, apply the boundary
       migration and a `sentinel` attribute; keep `check_calls == 0` and the
       error text.
     - Monitor doubles without `polling_interval`: `LimitMonitor` (line 1207,
       `test_agent_session_invalidating_verdict_closes_session`),
       `ExplodingMetricsMonitor` (line 1245,
       `test_agent_session_monitor_metrics_failure_cannot_block_invalid_cleanup`)
       and `LateLimitMonitor` (line 1396). The session now reads
       `polling_interval` from the monitor, so each double gains a class
       attribute `polling_interval = DEFAULT_POLLING_INTERVAL` (import it from
       `weft._constants`); nothing else about them changes. The `limit`
       verdict in the first one relies on the session's immediate first
       sample, which is kept. The `ExplodingMetricsMonitor` test is the
       guard for the step 4 path in which `poll_limits()` stops the monitor
       (its `last_metrics()` raises, so `self._monitor` becomes `None`) and
       the session must not re-arm from it; its assertions are unchanged.
     - `tests/tasks/test_task_execution.py` constructs `AgentSession` only for
       `close()` paths (lines 1331, 1364) and never calls `execute()`: no
       change.
     Before editing, re-run the lookup
     (`grep -n "AgentSession(\|_response_receiver = \|class .*Receiver\|class .*Connection" tests/tasks/test_runner.py`
     and `grep -rln "AgentSession" tests`) and add any double found that this
     list misses.
- Stop if: a migration would change a behavioral assertion rather than move a
  fake or an injection point (a substantive contract change; report the test
  and the assertion); any [CC-3.5] precedence test in
  `tests/tasks/test_runner.py` (for example
  `test_agent_session_routes_all_event_pairs_through_persistent_policy`)
  changes expected outcomes; the sampling guards above need a weaker bound to
  pass; or the Windows `wait()` path cannot accept the
  `PipeConnection`/sentinel pair (verify on the Windows CI slice before
  landing).
- Verify: `./.venv/bin/python -m pytest tests/core/test_terminal_handoff_transport.py tests/tasks/test_runner.py tests/tasks/test_agent_execution.py tests/core/test_terminal_handoff.py -q`,
  then `./bin/pytest-pg tests/core/test_terminal_handoff_transport.py tests/tasks/test_runner.py`.

### Task 5. After-measurements and traceability

- Files to touch: this plan (execution log, Deviation Log if needed);
  `docs/specifications/01-Core_Components.md` (the second `## Related Plans`
  section, near the end of the file); `docs/specifications/06-Resource_Management.md`
  `## Related Plans`; the module docstrings of `weft/core/tasks/service.py`,
  `weft/core/tasks/interactive.py` and `weft/core/tasks/sessions.py` only if
  their spec references no longer match (at b7eed9f9 they cite, among others,
  [CC-2.2], [CC-2.3], [CC-2.5]; [CC-2.3], [CC-2.4], [MF-2], [MF-3]; and
  [CC-3.2], [CC-3.5], [RM-5.1], [RM-5.2].
  Add [CC-2.2.1] to `interactive.py`; the others already match).
- Repeat Task 0 items 1 and 3 at the final SHA, on both backends (item 2
  only if the owner reactivates Task 2). Record
  before/after side by side.
- Backlinks (not normative): one bullet per spec, for example
  `- [Reactor edge timer floors](../plans/2026-11-02-reactor-edge-timer-floors-plan.md) replaces fixed timer floors on service-worker, interactive and agent-session waits with their real event sources ([CC-2.2.1], [RM-5.1]).`
- Verify: the final gates in §7; `./bin/check-doc-paths`.

## 6. Testing Plan

- Harnesses: `WeftTestHarness` through the `weft_harness` and `broker_env`
  fixtures; real spawned processes; real `QueueChangeMonitor`; real host
  `start_session`. Each new test is `shared` so that `bin/pytest-pg` runs it
  on PostgreSQL.
- Never mock: queues, `QueueChangeMonitor`, `PollingStrategy`, sessions,
  processes or reactor waits. Allowed instruments are delegating counters
  and recorders (wrap, count or record the requested wait budget, call
  through) on the real object, the pattern
  `tests/commands/test_control_observation_connections.py::_track_postgres_connections`
  uses. The one bounded exception is the Task 4 step 8 migration of
  pre-existing fake-clock tests (§4 review gates).
- Positive setup and synchronization (cross-family F5): every new test
  proves its precondition before asserting an absence. A worker is proven
  inside `get()` (entry event), a descendant is proven to hold the pipe or
  sender (pidfile by atomic rename), a session is proven ready (`ready`
  frame or `wait_ready`), and every count has a positive lower bound, so a
  broken path that never runs cannot pass. Descendants are killed in
  `finally`.
- Observables, per site: the requested wait budget, recorded through a
  delegating wrapper, is the primary deterministic observable wherever the
  wrapped function exists before and after the edit (Tasks 1, 3 and 4);
  call counts are secondary bounds with a positive floor; latency bound
  versus deadline (Tasks 1 and 4: elapsed well under the timeout proves an
  event and not the deadline); queue facts (terminal state, frame-before-ack
  ordering in Task 3); CPU and voluntary context switches (Task 0 and Task 5
  measurement only, not asserted in tests, because they are machine-dependent).
- Red first: each red test is run and seen failing at b7eed9f9, and its
  failing assertion is quoted in the execution log. The guard tests (Task 1
  sentinel, Task 4 leaked sender, Task 4 monitor-interval sampling) pass at
  baseline. The monitor-interval test passes there because the 50 ms pass
  samples at least as often as the 0.05 s interval it asks for; it carries
  no gap assertion, because with a silent producer it cannot distinguish
  sampling on every pass. Each of these three must be run red at the named
  intermediate edit. The gap assertion, the floor proof, lives in the Task 4
  short-items test: at baseline the session samples on every live pass,
  far more often than that test's 0.2 s interval, so its gap assertion is
  red at b7eed9f9 (quote it in the execution log; that baseline is the
  sampling-on-every-pass mutation). Its other mutation, the rejected
  per-`execute()` deadline restart, is run red once with the per-call
  durations recorded as evidence (Task 4). The new transport helper carries
  no baseline-red claim; its mutation check stands in.
- Tempting to skip, and kept in: the expired-deadline sentinel (Task 1), the
  owner-local Debugger session (Task 3), the leaked-sender drain (Task 4) and
  the two sampling guards (Task 4). Each is where removing a floor without the
  real source would silently hang, lose latency or lose enforcement.
- Out: CPU assertions in tests; Windows-only timing.

## 7. Verification

```bash
. ./.envrc
# per task: the Verify line of that task, then the same targets with ./bin/pytest-pg
# final gates:
./.venv/bin/python -m pytest
./.venv/bin/python -m pytest -m "" tests/tasks tests/commands tests/core tests/specs
./bin/pytest-pg --fast
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
./bin/check-doc-paths
```

Success looks like this: every command exits 0. The Task 5 table shows D1, D8
and D10 at or below baseline on both backends, and D12 (samples per second of
a monitored live session) at one per `polling_interval` (one per second at
the default) with one extra at session start. `git log` shows one commit per
executed task (Tasks 1, 3 and 4; Task 2 is deferred).

After deploy, observe an idle manager tree with `ps` and psutil: voluntary
context switches should drop by about 20/s for each idle service worker
(about 160/s for the 8 LivenessMonitor probe workers).
`weft task stop` is untouched by this plan (Task 2 deferred).

## 8. Independent Review Loop

Tier-1 cross-family review per `skills/call-agent/SKILL.md` and
`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`, on this plan
before implementation (class 4), and again on the completed work. The
reviewer reads this plan, the four touched modules, [CC-2.2.1], [CC-3.5],
[RM-5.1] and [PY-1], and checks that D12 implements the owner's 2026-11-03
ruling exactly (monitor interval as ceiling and floor, session-scoped, first
sample immediate) rather than the diminution question, which the owner
closed. Round 1 returned BLOCKED (Review Log); round 2 is scoped to F1,
F3, F4 and F5 as applied here. F2 is resolved by deferral to the owner and is
not re-reviewed unless the owner reactivates Task 2.

## 9. Out of Scope

Discovered in the same pattern, but not in this plan's brief. Each one is
recorded for a follow-up decision:

- `weft/core/runners/host.py::HostTaskRunner._terminal_handoff_wait_seconds`
  (line 880): the same unconditional `ACTIVE_CONTROL_POLL_INTERVAL` floor in
  the one-shot function-runner handoff. Task 4's helper fits it unchanged.
  It stays unchanged here and is an owner follow-up (Owner Decisions item 4).
  The new `ACTIVE_CONTROL_POLL_INTERVAL` docstring names it as a remaining
  unconditional use and does not describe it as replaced (Task 4 step 7).
- `weft/commands/_spawn_submission.py::reconcile_submitted_spawn` (line 240):
  `monitor.wait(min(remaining, poll_interval))` with `CONTROL_SURFACE_WAIT_INTERVAL`.
- `weft/core/tasks/base.py::BaseTask._publish_worker_result` (the 50 ms put
  retry on a full result queue) and `BaseTask._stop_worker_lanes`
  (`thread.join(timeout=min(remaining, 0.05))`).
- `ServiceTask._stop_service_worker`'s put-and-evict loop on a full queue
  (≤ 50 ms per full-queue sentinel during stop; it has no idle cost).
- The host interactive session's `process.wait()` thread
  (`weft/core/runners/host.py`, `start_session`). [ALT-WT-021] rejects a thread
  parked in `Popen.wait()` for subprocess runners. This plan uses that
  thread's activity callback and does not redesign it.
- The heartbeat clock in row 4.3-a (that plan's cleanup plan 6).
- A restarted worker group can receive a sentinel left over from the previous
  stop (the group's `input_queue` is reused by `_start_service_worker`). This
  predates the plan, and Task 1 makes it rarer.

## 10. Rollback

Each task is one commit that touches no persisted format, queue name, public
protocol or CLI shape, so `git revert <sha>` of any task is independent and
safe in any order. No data migration and no rollout ordering are involved.
Old and new code coexist across processes, because every changed wait is
process-local. There is no one-way door.

## Owner Decisions

Every fork or owner decision this plan raises, in one place. None is resolved
here.

1. **CLOSED (owner, 2026-11-03): persistent agent sessions sample at the
   monitor's `polling_interval`, as a ceiling and a floor.** The question put
   to the owner was which cadence is least surprising to a Weft user and most
   consistent with the theory. The facts behind it: `DEFAULT_POLLING_INTERVAL`
   is 1.0 s; the subprocess runner samples at that interval from its
   supervision wait; the interactive session does the same under the
   [RM-5.1] sentence; the agent session's 50 ms rate was a side effect of its
   wait loop and is stated in no document. The owner's ruling, in the
   exchange of 2026-11-03: the "idle task does nothing" sentence does not
   forbid checking (a checking task is not idle), the constraint is no loops
   outside the reactor; sample at the monitor's interval like every other
   runner; and, asked whether to sample at wake-ups too so the interval is a
   ceiling, the agreed shape is: sample on a wake only when a sample is due,
   so the interval is a ceiling (the deadline wakes a quiet session) and a
   floor (a busy session rides its sampling on wakes it was having anyway),
   with the session's first sample immediate ("Ok, agree. Revise plan 2
   accordingly"). The diminution is named in D12 and accepted: a cold
   sustained CPU violation in an agent session is detected at the fifth
   sample, near four intervals with the immediate first sample (about 4 s
   at the default), instead of about 0.2 s, which is
   already the contract for every other task kind. This brought the
   [RM-5.1] clarification and the class to 5, as the plan had declared it
   would.
2. **CLOSED (owner, 2026-11-03): the 50 ms control-surface cap stays,
   option (a).** The owner's words: "I think we keep it for now in Plan 2
   and Plan 3." Task 2 is not executed; the cap (`CONTROL_SURFACE_WAIT_INTERVAL`
   as the per-pass timeout of `_await_control_surface`'s queue-change wait)
   is retained as a named absent-event timer, and Task 2a below gives it
   its docstring. (c), a platform process-exit watcher, stays available as
   a later plan. History: Task 2's stop condition fired at cross-family
   round 1 (F2): the outbox watch does not cover a reachable crash window
   (see the finding below), and a non-child process exit has no portable
   notification. The options as they stood:
   - (a) **Retain the cap as a named absent-event timer (the plan's
     default).** Give the 50 ms interval a docstring naming the event that
     cannot arrive (the death of a host-pid process that is not the
     observer's child) and why. Cost: today's status reads (one every 50 ms
     for up to 2 s per stop or kill wait) stay; no latency change.
   - (b) **Remove the cap and accept a deadline-bounded miss.** Task 2 as
     written. Cost: in the crash window the observer misses the terminal row
     for up to the 2 s overall deadline instead of about 50 ms. This is a
     diminution; RECONSIDERATION REQUESTED.
   - (c) **A pid-exit adapter leaf, as a later plan.** A platform-specific
     process-exit source (pidfd on Linux, a kqueue process filter on macOS
     and BSD, a process handle on Windows) joined to the control-surface
     wait, like the launcher parent-loss watcher precedent
     (`weft/core/launcher.py::_start_parent_loss_watcher`; plan 1 audit row
     4.3-a). Cost: a new adapter per platform and its own plan and review;
     it removes the cap without diminution. Not in this plan.
3. **Owner item 6 classification** (Task 0 item 4). Whether "A quiet task does
   nothing" holds on PostgreSQL given the measured native idle returns. This
   plan only measures. **Closed on the owner's implement instruction
   (2026-11-03) with the recommended option: measured only, as the plan
   states** (the measurement is in the Execution log under Task 0; the
   classification itself stays with the owner).
4. **Out-of-scope follow-ups** (§9). Each listed site is recorded for a
   follow-up decision and is not changed here. **Closed on the owner's
   implement instruction (2026-11-03) with the recommended option: recorded,
   nothing to implement.**
5. **Sampling between work items.** A persistent agent session's producer
   is alive between `execute()` calls but no observation pass runs then, so
   no limit sample is taken until the next work item starts. Interactive
   sessions do sample between inputs through the Consumer's reactor timer
   ([RM-5.1]). Options: (a) keep sampling inside work items only (the
   plan's shape; no diminution against today, which also samples only
   inside `execute()`; an overdue instant samples on the next item's first
   pass); (b) extend sampling between items by giving the Consumer's
   reactor a deadline for the agent session, as for interactive sessions.
   Recommendation: (a) for this plan; (b) is an expansion with its own
   plan. Raised by the cross-family round of 2026-11-03. **Closed on the
   owner's implement instruction (2026-11-03) with the recommended option:
   (a), the plan's current shape (sampling only while a work item is
   supervised).**

Finding behind item 2 (fresh-eyes pass, reachability confirmed at
cross-family round 1 and re-verified against the live code): in
`weft/core/task_evidence.py::known_tid_evidence`, the one-shot outbox is read
only when `runtime_evidence` returns `None`; a mapping whose runtime handle
has `host-pid` authority and a live process returns `running` and skips the
outbox. The window is reachable on the normal host path.
`weft/core/tasks/consumer.py::Consumer._register_outcome_runtime` (called
before `_emit_result` writes the result) clears the reaped worker's handle
and republishes tid state; `weft/core/tasks/base.py::BaseTask._build_tid_state_payload`
then falls back to `_task_process_runtime_handle()`, the Consumer's own
host-pid handle. If the Consumer writes the outbox row and dies before its
terminal publication, an observer that consumes the outbox wake while that
pid is alive reads `running`, and the pid's later death changes
`known_tid_evidence` with no further queue write. At b7eed9f9 the 50 ms poll
catches it within about 50 ms of the exit.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Spec Baseline

- `b7eed9f9` — `docs/specifications/01-Core_Components.md`,
  `docs/specifications/06-Resource_Management.md`,
  `docs/specifications/07-System_Invariants.md`,
  `docs/specifications/05-Message_Flow_and_State.md`,
  `docs/specifications/14-Python_API_Surfaces.md`, `docs/program-theory.md`
  at plan authoring time.
- Sanction check: no sentence in [CC-2.2.1], [CC-2.5] or [RM-5.2] sanctions a
  fixed wait floor at any site in scope. A repository-wide grep of
  `docs/specifications/` for the constant names, "50 ms", "50ms" and "0.05"
  returns nothing (see the Execution log). [CC-2.2.1] states the opposite
  ("no task-reactor wait cap exists" for local events, including interactive
  child stream publication).

## Proposed Spec Delta

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| docs/specifications/06-Resource_Management.md | B — atomic with Task 4 | [RM-5.1] "Enforcement mechanics and security boundary", the interactive-session bullet (lines 183–190 at b7eed9f9): one sentence pair appended; `## Related Plans` backlink |

Every other change brings code into line with [CC-2.2.1], [CC-3.1], [CC-3.5]
and [RM-5.1] as written at the baseline, and the Task 5 `## Related Plans`
bullets are traceability and not normative. The one normative addition is
the owner's 2026-11-03 sampling ruling for persistent agent sessions
(Owner Decisions item 1, D12), which extends the [RM-5.1] sentence that today
names only interactive command sessions. Why B: under strategy A the spec
would describe a session-scoped sampling instant while shipped code still
sampled on every 50 ms pass; the sentence is small and lands with Task 4.

### `docs/specifications/06-Resource_Management.md` [RM-5.1] — replace the interactive-session bullet (lines 183–190; Task 4)

Current text:

> - For an interactive command session, Consumer publishes the next resource
>   sample through `next_wait_timeout()` and calls the session's `poll_limits()`
>   only when that absolute deadline is due. Stream, control, backend, and process
>   exit activity do not move the resource-sampling deadline or cause additional
>   samples. The sampling timer exists only for a session started through
>   `TaskRunner` whose TaskSpec names a `monitor_class`. The built-in Debugger's
>   owner-local session bypasses `TaskRunner` and publishes no sampling timer. A
>   session with no monitor class likewise waits on events alone.

Replacement (`docs/specifications/06-Resource_Management.md`):

```text
- For an interactive command session, Consumer publishes the next resource
  sample through `next_wait_timeout()` and calls the session's `poll_limits()`
  only when that absolute deadline is due. Stream, control, backend, and process
  exit activity do not move the resource-sampling deadline or cause additional
  samples. The sampling timer exists only for a session started through
  `TaskRunner` whose TaskSpec names a `monitor_class`. The built-in Debugger's
  owner-local session bypasses `TaskRunner` and publishes no sampling timer. A
  session with no monitor class likewise waits on events alone.
- A persistent agent session samples the same way, at its monitor's
  `polling_interval`: `AgentSession` holds one session-scoped next-sample
  instant, takes its first sample on its first observation pass with a live
  producer, samples on any later observation pass once that instant is due
  and never sooner, re-arms the instant one interval after a clock reading
  taken after each sample, and publishes the instant as a deadline of its
  handoff wait. Sampling runs while a work item is being supervised: the
  deadline bounds the requested wait, and the spacing between samples,
  measured after each sample, is at least one interval. Between work items
  no sample is taken. A new work item neither resets nor postpones the
  instant, so an instant that fell due between items is honored on the first
  observation pass of the next work item.
```

## Review Log

self-driven fresh-eyes review: done 2026-11-02 (distinct pass after drafting): 12 corrections — only LivenessMonitor probe workers idle (Manager and TaskMonitor targets return after one item; §3, D1, §7); sentinel `put_nowait` guarantee rests on maxsize ≥ worker_count, not "an empty queue is never full" (Q1, Task 1 stop-if); sentinel-loop lines 456-459 and the third `_stop_service_worker` caller; outbox read gated by runtime-handle liveness (§1 row, D6, fresh-eyes finding); `process_outbox_message` lives in `weft/core/outbox.py`; precedence test must drop `interval=2.0`; outbox opened last and `matches()` has no caller; interactive shutdown bound ≤ 5 was unreachable after the change (now ≤ 12); agent-session cadence test counted a function the edit renames (now counts passes); D11 clarified as non-production because Consumer always passes a cancel predicate; Task 5 docstring citations listed as a subset; owner decisions gathered under one heading

- Cross-family review round 1 (codex 0.161.0, 2026-11-02, whole plan after the fresh-eyes pass): BLOCKED. F1 (P1, per-item sampling deadline restart could skip limit checks; CPU cold detection 0.2 s → 5 s): accepted in the preserving shape, session-scoped sampling deadline at the current cadence kept as clock work, slower sampling listed as an owner alternative with RECONSIDERATION REQUESTED. F2 (P1, the control-surface cap covers a reachable crash window with no queue event): accepted, Task 2 deferred to the owner with three options. F3 (dead-producer deadline retirement), F4 (test-double migrations, threshold removed), F5 (positive setup and synchronization): P2, accepted and applied. Round 2 (codex 0.161.0, 2026-11-02, scoped to F1, F3–F5, F2 deferral confirmed): F1, F4, F5 closed with live evidence (sessions.py:534 checks on every live pass; resource_monitor.py:212's five-sample rule gives the preserved ~0.2 s CPU bound; the two guards cannot pass with zero checks; test_runner.py:3772 and :3342 are the named migrations); F3 returned one residual (a monitored producer already dead on the first pass leaves `_next_limit_sample_at` unset, so the next pass compared `now >= None`), applied here without a further round under the one-scoped-round rule: the sampling calculation is gated on its active state and the unset timestamp is handled explicitly, the dead-producer prohibition kept. Review loop closed; the plan waits on the owner's decisions (slower sampling alternative; the deferred control-surface cap, options a/b/c; PostgreSQL item 6; the host-handoff floor).
- owner ruling applied 2026-11-03: Owner Decisions item 1 closed. Persistent agent sessions sample at the monitor's `polling_interval` (ceiling and floor; session-scoped instant checked on every pass and joined to the wait; first sample immediate); `AGENT_SESSION_LIMIT_SAMPLE_INTERVAL` removed from the plan; D12 reclassified as an owner-accepted diminution; the three monitor doubles gain `polling_interval`; one [RM-5.1] sentence added under `## Proposed Spec Delta`, class 4 → 5. Cross-family round on this change pending.
- Cross-family review round 1 on the sampling change (codex 0.161.0,
  2026-11-03, scoped to the owner's 2026-11-03 ruling as applied): BLOCKED.
  F1 (P1, `poll_limits()` can disable the monitor and the re-arm then reads
  `None.polling_interval`): accepted, the interval is read before the sample
  and the re-arm happens only if the monitor survived. F2 (re-arm from a
  post-sample clock reading, as both precedents do), F3 (the interval test's
  gap assertion cannot catch sampling on every pass; moved to the short-items
  test at a 0.2 s interval), F5 (spec lines 183–190), F6 (stale "50 ms clock"
  sentence; fifth sample at four intervals): all P2, accepted and applied.
  F4 (the ceiling holds only while a work item is supervised): wording
  scoped; sampling between work items recorded as Owner Decisions item 5 with
  recommendation (a). Round 2 (codex 0.161.0, 2026-11-03, scoped to F1, F2, F3, F5, F6; F4 scoping confirmed): no blocker; all closed with live evidence (`poll_limits()` clears `_monitor` on a metrics failure and returns normally, sessions.py:189–212; both precedents re-arm after the sample, subprocess_runner.py:266 and interactive.py:299; `_observe_terminal_handoff` samples on every eligible pass, sessions.py:534, so consecutive short items expose every-pass sampling; spec lines 183–190 match byte for byte; five samples at 1.0 s give about 4 s). Review loop on the sampling change closed; the plan waits on the owner's remaining decisions (item 2, the deferred control-surface cap, options a/b/c; item 3, PostgreSQL quiet-task classification; item 4, the host-handoff floor; item 5, sampling between work items).
- owner ruling applied 2026-11-03: item 2 closed as (a) ("I think we keep it for now in Plan 2 and Plan 3"): the 50 ms cap stays as a named absent-event timer; Task 2 not executed; Task 2a added for the docstring. Items 3–5 remain.
- cross-plan correction applied 2026-11-03 (owner's outside reviewer): Task 3's `test_interactive_shutdown_waits_for_exit_callback` asserted final frames before the acknowledgement, an oracle series plan 3 retires; it now reuses the existing test's three output-before-terminal assertions (final frames before the `ctrl_out` terminal envelope and before the lifecycle terminal row) and asserts nothing about acknowledgements; plan 3's rename of the reused test recorded in the hidden couplings. Plan metadata gate and `bin/check-doc-paths` re-run.

- owner items closed 2026-11-03: item 3 closed on the owner's implement instruction (2026-11-03) with the recommended option: measured only, as the plan states; item 4 closed on the owner's implement instruction (2026-11-03) with the recommended option: recorded, nothing to implement; item 5 closed on the owner's implement instruction (2026-11-03) with the recommended option: (a), the plan's current shape (sampling only while a work item is supervised).
- implementation 2026-11-03: Tasks 0, 1, 2a, 3, 4 and 5 executed from `fbd79c66`, one commit each (Task 2 not executed, owner item 2 (a)). The §8 cross-family review of the completed work is not run by the implementing agent; it is left to the series coordinator.
- Cross-family review of the landed implementation (codex 0.161.0, 2026-11-03, range fbd79c66..b3de6b0d): blocker F1 (P2, the agent-session wait computed from a clock reading taken before limit sampling, so a sample that consumes time delays the wake past the sampling deadline): accepted and fixed in 1239e3b1 with a RED test for a time-consuming sample; F2 (no CHANGELOG entry) and F3 (measurement script source absent from the Execution log): accepted and applied. The reviewer confirmed every other task against the diff: the service worker, interactive and transport hunks match the plan; all named RED tests exist with non-tautological assertions that their named mutations break; no test double reaches the new OS wait with a poll-only object; a pipe Connection plus a Process sentinel is a valid set for multiprocessing.connection.wait (the shape Manager._wait_for_ready already uses), with Windows execution still unverified; the leaked-sender bound excludes interpreter start-up; the [RM-5.1] sentence is verbatim; the suppression recount (194 groups, 324 directives, 112 C901) is correct. Scoped round 2 on F1 pending.
- Scoped round 2 (codex 0.161.0, 2026-11-03, range 4ac6a1e7..0cd077e4, F1 only): no blocker; F1 closed. The wait now subtracts a fresh `time.monotonic()` after the sample and re-arm (sessions.py:492 in that range), the top-of-pass `now` still governs observations and the reducer, the existing `max(0.0, ...)` prevents negative waits, a deadline crossed during observation yields at most one zero-time wait followed by a fresh pass, and the 50 ms cancel-predicate term still enters the minimum. An in-memory probe of both revisions reproduced the RED: 0.2 s sample at a 0.5 s interval requests 0.7 s before the fix and 0.5 s after. Observation, not actionable now: the test's 50 ms allowance around the recorded budget is not scheduler-proof under heavy xdist load; if it flakes, widen the allowance rather than the interval, and keep the budget assertion.

## Execution log

Authoring evidence at `b7eed9f9` (commands run from the repository root):

```text
$ git rev-parse --short HEAD
b7eed9f9
$ ls weft/core/sessions.py weft/core/tasks/sessions.py
ls: weft/core/sessions.py: No such file or directory
weft/core/tasks/sessions.py
$ grep -n "LOCAL_WORKER_QUEUE_OPERATION_TIMEOUT_SECONDS" weft/core/tasks/service.py
23:from weft._constants import LOCAL_WORKER_QUEUE_OPERATION_TIMEOUT_SECONDS
70:                    timeout=LOCAL_WORKER_QUEUE_OPERATION_TIMEOUT_SECONDS
462:                        LOCAL_WORKER_QUEUE_OPERATION_TIMEOUT_SECONDS,
465:                    put_timeout = LOCAL_WORKER_QUEUE_OPERATION_TIMEOUT_SECONDS
488:                    wait_timeout = LOCAL_WORKER_QUEUE_OPERATION_TIMEOUT_SECONDS
651:        deadline = time.monotonic() + LOCAL_WORKER_QUEUE_OPERATION_TIMEOUT_SECONDS
$ grep -rn "drain=True" weft tests | wc -l
       0
$ grep -rln "_stop_service_worker" ../engram ../taut --include=*.py | wc -l
       0
$ grep -n "time.sleep" weft/core/tasks/interactive.py
374:            time.sleep(INTERACTIVE_OUTPUT_DRAIN_POLL_INTERVAL)
530:            time.sleep(min(INTERACTIVE_STOP_POLL_INTERVAL, remaining))
$ grep -n "ACTIVE_CONTROL_POLL_INTERVAL" weft/core/tasks/sessions.py weft/core/runners/host.py weft/core/runners/subprocess_runner.py
weft/core/tasks/sessions.py:24:    ACTIVE_CONTROL_POLL_INTERVAL,
weft/core/tasks/sessions.py:734:        deadlines = [now + ACTIVE_CONTROL_POLL_INTERVAL]
weft/core/runners/host.py:33:    ACTIVE_CONTROL_POLL_INTERVAL,
weft/core/runners/host.py:880:        deadlines = [now + ACTIVE_CONTROL_POLL_INTERVAL]
weft/core/runners/subprocess_runner.py:41:    ACTIVE_CONTROL_POLL_INTERVAL,
weft/core/runners/subprocess_runner.py:432:        deadlines.append(ACTIVE_CONTROL_POLL_INTERVAL)
$ grep -n "CONTROL_SURFACE_WAIT_INTERVAL" weft/commands/tasks.py weft/commands/_spawn_submission.py
weft/commands/_spawn_submission.py:23:    CONTROL_SURFACE_WAIT_INTERVAL,
weft/commands/_spawn_submission.py:240:    poll_interval: float = CONTROL_SURFACE_WAIT_INTERVAL,
weft/commands/tasks.py:35:    CONTROL_SURFACE_WAIT_INTERVAL,
weft/commands/tasks.py:1944:                        + CONTROL_SURFACE_WAIT_INTERVAL
weft/commands/tasks.py:1948:                        observation.kill_ack_observed_at + CONTROL_SURFACE_WAIT_INTERVAL
weft/commands/tasks.py:1975:                    interval=CONTROL_SURFACE_WAIT_INTERVAL,
$ grep -rn "CONTROL_SURFACE_WAIT_INTERVAL\|LOCAL_WORKER_QUEUE_OPERATION_TIMEOUT\|INTERACTIVE_OUTPUT_DRAIN\|INTERACTIVE_STOP_POLL\|ACTIVE_CONTROL_POLL_INTERVAL\|INTERACTIVE_STOP_GRACE\|50 ms\|50ms\|0\.05" docs/specifications/
(no output)
$ grep -n "on_activity" extensions/*/*/*.py weft/core/tasks/debugger.py
extensions/weft_docker/weft_docker/agent_runner.py:374:        self, *, on_activity: Callable[[], None]
extensions/weft_docker/weft_docker/agent_runner.py:376:        del on_activity
extensions/weft_docker/weft_docker/plugin.py:315:        self, *, on_activity: Callable[[], None]
extensions/weft_docker/weft_docker/plugin.py:317:        del on_activity
extensions/weft_macos_sandbox/weft_macos_sandbox/plugin.py:193:        self, *, on_activity: Callable[[], None]
extensions/weft_macos_sandbox/weft_macos_sandbox/plugin.py:195:        del on_activity
weft/core/tasks/debugger.py:25:        on_activity: Callable[[], None],
weft/core/tasks/debugger.py:27:        del on_activity
extensions/weft_microsandbox/weft_microsandbox/plugin.py:128:        self, *, on_activity: Callable[[], None]
extensions/weft_microsandbox/weft_microsandbox/plugin.py:130:        del on_activity
  (each extension start_session raises ValueError: interactive sessions unsupported)
$ ./.venv/bin/python -c "import simplebroker; print(simplebroker.__version__)"
8.7.2
$ grep -n "_native_idle_poll_interval = " .venv/lib/python3.14/site-packages/simplebroker/watcher.py
1386:        self._native_idle_poll_interval = max(1.0, self._max_interval * 10)
$ grep -n "requires-python" pyproject.toml
10:requires-python = ">=3.12"
```

Python 3.12 is still supported, so `queue.Queue.shutdown()` (3.13+) is not
available as the wake source in Task 1. The sentinel is the source.

### Implementation (2026-11-03, from `fbd79c66`)

Executed tasks: 0, 1, 2a, 3, 4, 5. Task 2 is not executed (owner item 2,
closed as (a)). Plans 10, 9 and 5 landed between `b7eed9f9` and the
implementation base `fbd79c66`; every production citation below was
re-verified by symbol before editing. `weft/core/tasks/sessions.py` is still
the sessions module (series plan 8 moves it later).

#### Task 0. Comprehension answers (before the first edit)

1. After Task 1, a worker blocked in `ServiceWorkerContext.iter_items`'s
   untimed `input_queue.get()` wakes only on an item. Stop is
   `ServiceTask._stop_service_worker(name, deadline=...)`, called by
   `ServiceTask._cleanup_task_resources` (and by
   `_abort_partial_initialization` and the start-failure unwind in
   `_start_service_worker`). It sets the group `stop_event` and then puts
   one `_service_worker_stop` sentinel per worker; `iter_items` returns on
   the sentinel. A worker can block only on an empty queue, and every
   registered group has `input_queue_maxsize=0` (unbounded: Manager child
   launch, the three TaskMonitor groups, the `ServiceTask` lane) or a
   maxsize equal to its worker count (LivenessMonitor probes: 8 and 8,
   `weft/core/tasks/liveness_monitor.py` line 136). So the at most
   `worker_count` sentinels that Task 1's first `put_nowait` attempts
   deliver all fit while any worker is blocked, and that attempt runs before
   any deadline check. The deadline bounds only the full-queue
   put-and-evict retry, and a full queue means no worker is blocked in
   `get()`.
2. The process sentinel is level-triggered: once the producer exits it stays
   ready, so leaving it in the wait set after `producer_exited` is consumed
   would make every drain wait return at once (a busy loop until the drain
   deadline). Only the pull-style `cancel_requested` predicate keeps a
   50 ms clock (`ACTIVE_CONTROL_POLL_INTERVAL`): `AgentSessionProtocol.execute`
   takes a `Callable[[], bool]` with no waitable, and Consumer always passes
   one, so this is the named absent event. The only other timed term is the
   limit-sampling deadline at the monitor's `polling_interval` (1.0 s by
   default), which is clock work ([RM-5.1], [WT-7]); it bounds the wait while
   an item is supervised, a sample also runs on any observation pass once the
   instant is due, and the next instant is armed from a clock reading taken
   after the sample, so samples are never closer than one interval. Payload
   readiness, channel EOF and producer exit wake the wait; timeout and drain
   are deadlines. `_observe_terminal_handoff` never samples a dead producer or
   after an accepted stop, so an expired sampling instant in that state would
   never be re-armed and every later wait would be zero-time until drain
   expiry; the sampling deadline therefore leaves the wait set while the
   producer is dead or a stop is accepted (D14).

Stop-if checks for Task 1 at `fbd79c66`: `grep -rn "drain=True" weft tests`
returns nothing; `grep -rn "input_queue" weft/core/manager.py
weft/core/monitor/task_monitor.py weft/core/tasks/liveness_monitor.py`
matches only `input_queue_maxsize=` at `weft/core/tasks/liveness_monitor.py:140`;
`grep -rn "_stop_service_worker" ../engram ../taut --include=*.py | wc -l`
is 0; every `ServiceWorkerSpec(` registration has maxsize 0 or equal to its
worker count.

#### Task 0. Baseline measurements at `fbd79c66`

PostgreSQL for every measurement: a disposable `postgres:18` container
(`docker run -d --rm --name weft-plan2-pg ...`), DSN
`postgresql://postgres:postgres@127.0.0.1:32798/weft_test`, with
`BROKER_TEST_BACKEND=postgres`, `WEFT_PG_TEST_DSN` and the
`BROKER_BACKEND_PASSWORD`/`WEFT_BACKEND_PASSWORD` variables that
`bin/pytest-pg` sets. Scripts are throwaway, kept outside the repository.

Item 1, idle service tree (D1). Script `idle_tree.py`: prepare a project
root with `tests.helpers.test_backend.prepare_project_root`, run
`weft manager start --context <root>`, settle 3 s, take the manager pid from
`weft manager list --json` (`runtime_handle.id`), add its psutil children and
every new `weft-`/spawned process that appeared after the start (detached
service children are not psutil children), then sample
`num_ctx_switches().voluntary` and `cpu_times()` 30 s apart with no work
submitted, then `weft manager stop`. The children run under the spawn
bootstrap command line, so they are listed by pid; the largest consumer is
the LivenessMonitor (eight idle probe workers).

```text
SQLite, fbd79c66:
pid=8369 vcs_delta=2606 cpu_delta_s=0.522 weft-plan2-id-5755757621:manager:running
pid=8370 vcs_delta=0 cpu_delta_s=0.000 /Users/van/Developer/weft/.venv/bin/python -B -c from multip
pid=8371 vcs_delta=2829 cpu_delta_s=0.361 /Users/van/Developer/weft/.venv/bin/python -B -c from multip
pid=8372 vcs_delta=2674 cpu_delta_s=0.338 /Users/van/Developer/weft/.venv/bin/python -B -c from multip
pid=8373 vcs_delta=10554 cpu_delta_s=0.558 /Users/van/Developer/weft/.venv/bin/python -B -c from multip
TOTAL window=30.0s vcs_delta=18663 (622.1/s) cpu_delta_s=1.779

PostgreSQL, fbd79c66:
pid=8594 vcs_delta=3972 cpu_delta_s=0.452 weft-plan2-id-5775636741:manager:running
pid=8596 vcs_delta=0 cpu_delta_s=0.000 /Users/van/Developer/weft/.venv/bin/python -B -c from multip
pid=8597 vcs_delta=3261 cpu_delta_s=0.099 /Users/van/Developer/weft/.venv/bin/python -B -c from multip
pid=8598 vcs_delta=3179 cpu_delta_s=0.054 /Users/van/Developer/weft/.venv/bin/python -B -c from multip
pid=8599 vcs_delta=15150 cpu_delta_s=0.269 /Users/van/Developer/weft/.venv/bin/python -B -c from multip
TOTAL window=30.0s vcs_delta=25562 (852.1/s) cpu_delta_s=0.874
```

Source of `idle_tree.py` (the version used for every recorded run, before and after):

```python
"""Task 0 item 1: idle manager tree voluntary context switches and CPU over 30 s."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil

REPO = Path("/Users/van/Developer/weft")
sys.path.insert(0, str(REPO))
from tests.helpers.test_backend import cleanup_prepared_roots, prepare_project_root  # noqa: E402

WEFT = str(REPO / ".venv/bin/weft")
WINDOW = float(os.environ.get("WINDOW", "30"))


def weft(*args: str) -> str:
    return subprocess.run(
        [WEFT, *args], check=True, capture_output=True, text=True, timeout=60
    ).stdout


def snap(pids: list[int]) -> dict[int, tuple[str, int, float]]:
    out = {}
    for pid in pids:
        try:
            p = psutil.Process(pid)
            ct = p.cpu_times()
            label = subprocess.run(
                ["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True
            ).stdout.strip()[:60]
            out[pid] = (label, p.num_ctx_switches().voluntary, ct.user + ct.system)
        except psutil.Error:
            pass
    return out


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="plan2-idle-"))
    prepare_project_root(root)
    preexisting = {p.pid for p in psutil.process_iter()}
    try:
        weft("manager", "start", "--context", str(root))
        time.sleep(3.0)  # let startup settle
        managers = json.loads(weft("manager", "list", "--json", "--context", str(root)))
        pid = int(managers[0]["runtime_handle"]["id"])
        proc = psutil.Process(pid)
        pids = {pid} | {c.pid for c in proc.children(recursive=True)}
        # Detached service children are not psutil children; add the new weft- titled processes.
        for p in psutil.process_iter(["pid", "cmdline"]):
            cmd = " ".join(p.info["cmdline"] or [])
            if cmd.startswith("weft-") and p.pid not in preexisting:
                pids.add(p.pid)
        pids = sorted(pids)
        before = snap(pids)
        time.sleep(WINDOW)
        after = snap(pids)
        total_cs = 0
        total_cpu = 0.0
        for p, (name, cs0, cpu0) in before.items():
            if p not in after:
                continue
            _, cs1, cpu1 = after[p]
            total_cs += cs1 - cs0
            total_cpu += cpu1 - cpu0
            print(f"pid={p} vcs_delta={cs1 - cs0} cpu_delta_s={cpu1 - cpu0:.3f} {name}")
        print(
            f"TOTAL window={WINDOW}s vcs_delta={total_cs} ({total_cs / WINDOW:.1f}/s) cpu_delta_s={total_cpu:.3f}"
        )
    finally:
        subprocess.run(
            [WEFT, "manager", "stop", "--context", str(root)],
            capture_output=True,
            timeout=60,
        )
        cleanup_prepared_roots(root)


main()
```

Item 2 (D5, D7): not run; deferred with Task 2 (owner item 2 closed as (a)).

Item 3 (D8, D10, D12): the counts the red tests assert, taken by running
those tests before the implementation edits; recorded under Tasks 3 and 4.

Item 4 (owner item 6, measurement only). Script `quiet_task.py`: an
in-process `Consumer` for a persistent function task
(`tests.tasks.sample_targets:echo_payload`) in a prepared project root,
nothing written to its queues, `run_until_stopped()` on a thread, 2 s settle,
then a 30 s window, then `stop()`. Delegating counters on the instance's
`_process_reactor_turn`, on `MultiQueueWatcher._has_pending_messages`, and on
`simplebroker.watcher.PollingStrategy.wait_for_activity` returns.

```text
backend=sqlite window=30.0s (after 2 s settle)
  turns: 0 in window (total incl. startup 2)
  pending_probes: 0 in window (total incl. startup 1)
  strategy_returns: 262 in window (total incl. startup 419)
backend=postgres window=30.0s (after 2 s settle)
  turns: 1 in window (total incl. startup 2)
  pending_probes: 19 in window (total incl. startup 20)
  strategy_returns: 20 in window (total incl. startup 21)
```

Source of `quiet_task.py`:

```python
"""Task 0 item 4 (owner item 6): does a quiet persistent task run turns when the native idle poll returns?"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path("/Users/van/Developer/weft")
sys.path.insert(0, str(REPO))
import simplebroker.watcher as sbw  # noqa: E402

from tests.helpers.test_backend import cleanup_prepared_roots, prepare_project_root  # noqa: E402
from tests.tasks.test_task_execution import make_function_taskspec  # noqa: E402
from weft.context import build_context  # noqa: E402
from weft.core.tasks import Consumer  # noqa: E402
from weft.core.tasks.multiqueue_watcher import MultiQueueWatcher  # noqa: E402

WINDOW = float(os.environ.get("WINDOW", "30"))
counts = {"turns": 0, "pending_probes": 0, "strategy_returns": 0}

orig_pending = MultiQueueWatcher._has_pending_messages


def pending(self):  # type: ignore[no-untyped-def]
    counts["pending_probes"] += 1
    return orig_pending(self)


MultiQueueWatcher._has_pending_messages = pending  # type: ignore[method-assign]
orig_wait = sbw.PollingStrategy.wait_for_activity


def wait(self, *a, **k):  # type: ignore[no-untyped-def]
    try:
        return orig_wait(self, *a, **k)
    finally:
        counts["strategy_returns"] += 1


sbw.PollingStrategy.wait_for_activity = wait  # type: ignore[method-assign]


def main() -> None:
    root = prepare_project_root(Path(tempfile.mkdtemp(prefix="plan2-quiet-")))
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    spec = make_function_taskspec(tid, "tests.tasks.sample_targets:echo_payload")
    from weft.core.taskspec import TaskSpec

    data = spec.model_dump()
    data["spec"]["persistent"] = True
    spec = TaskSpec.model_validate(data)
    task = Consumer(ctx.broker_target, spec, config=ctx.config)
    orig_turn = task._process_reactor_turn

    def turn(*a, **k):  # type: ignore[no-untyped-def]
        counts["turns"] += 1
        return orig_turn(*a, **k)

    task._process_reactor_turn = turn  # type: ignore[method-assign]
    th = threading.Thread(target=task.run_until_stopped, daemon=True)
    th.start()
    time.sleep(2.0)
    base = dict(counts)
    time.sleep(WINDOW)
    after = dict(counts)
    task.stop()
    th.join(10)
    print(
        f"backend={os.environ.get('BROKER_TEST_BACKEND', 'sqlite')} window={WINDOW}s (after 2 s settle)"
    )
    for k in counts:
        print(f"  {k}: {after[k] - base[k]} in window (total incl. startup {after[k]})")
    cleanup_prepared_roots(root)


main()
```

Reading: on SQLite the strategy returns about 9 times per second (its idle
polling pass) and Weft runs no turn and no pending probe. On PostgreSQL the
strategy returns about once per 1.5 s (the native idle return), each return
is followed by one `_has_pending_messages()` broker probe (19 in 30 s), and
one policy turn ran in the window. The prediction was no policy turns and one
probe per native idle interval; the probe count matches, and the one turn
is recorded as measured. The classification of "A quiet task does nothing"
on PostgreSQL is left to the owner (Owner Decisions item 3).

#### Task 1. Service workers block on their input queue

Red at `fbd79c66` (tests added, production unchanged):

```text
$ ./.venv/bin/python -m pytest tests/tasks/test_service_task.py -n0 -k "idle_service_worker or sentinel_after_cleanup"
>           assert len(counting_queue.get_timeouts) == 1
E           assert 10 == 1
FAILED tests/tasks/test_service_task.py::test_idle_service_worker_does_not_wake_until_stopped
(test_stop_delivers_sentinel_after_cleanup_deadline_expired passed: the guard)
```

Intermediate red, after edit 1 only (untimed `get()`, no `put_nowait`):

```text
E           assert not True
E            +  where True = any(<generator object test_stop_delivers_sentinel_after_cleanup_deadline_expired.<locals>.<genexpr> ...>)
```

GREEN after edits 2 and 3: both new tests pass; the Task 1 Verify line
(`tests/tasks/test_service_task.py tests/tasks/test_liveness_monitor.py
tests/core/test_manager.py`) exits 0 on SQLite, and `./bin/pytest-pg` on the
same targets reports `340 passed, 12 skipped`.

Deviation (mechanical consequence, not in the plan's file list): deleting the
`drain` branch dropped `ServiceTask._stop_service_worker` below the `C901`
threshold, and the repository's Ruff config reports the now-unused
`# noqa: C901 ... [RUFF-SUP-046]` directive (`RUF100`). The directive is
removed, and with it the `RUFF-SUP-046` group row, its generated index row and
the aggregate `C901` count in `docs/ruff-suppression-registry.md`
(regenerated with `bin/ruff_suppression_index.py --write`; `--check` passes),
plus the pinned group list and counts in `tests/specs/test_ruff_policy.py`
(195 → 194 groups, 325 → 324 directives, 113 → 112 `C901`).

#### Task 2a. The retained control-surface cap named as an absent-event timer

`CONTROL_SURFACE_WAIT_INTERVAL`'s docstring in `weft/_constants.py` now names
the absent event (a non-child process death after its result row, which
`known_tid_evidence` reads with no queue write), says queue changes wake the
wait sooner, records the reuse as the grace length until series plan 3, and
names option (c) as the cap's removal path. One addition beyond the plan's
wording, for accuracy: the docstring also names the constant's third use, the
default `poll_interval` of `_spawn_submission.reconcile_submitted_spawn` (§9),
which this plan leaves untouched. No behavior or test change.
`grep -n "CONTROL_SURFACE_WAIT_INTERVAL" -A12 weft/_constants.py` shows the
new docstring; `./.venv/bin/ruff check weft/_constants.py` passes.

#### Task 3. Interactive drain and shutdown wait on the activity callback

Baseline (Task 0 item 3, D8 and D10) at `9249be00`, production unchanged,
taken with a temporary print in the two new tests (`-n0 -s`, then removed):

```text
BASELINE drain sleeps 20 poll_stdout 25
BASELINE shutdown sleeps 10 is_alive 24
```

Red at the same commit (three runs, identical):

```text
tests/core/test_debugger.py::test_debugger_exit_does_not_wait_out_the_drain_budget
E           assert 22 <= 3
tests/tasks/test_task_interactive.py::test_interactive_drain_waits_once_while_descendant_holds_pipes
E           assert [0.01, 0.01, ...01, 0.01, ...] == []
E             Left contains 21 more items, first extra item: 0.01
tests/tasks/test_task_interactive.py::test_interactive_shutdown_waits_for_exit_callback
E           assert [0.05, 0.05, ...05, 0.05, ...] == []
E             Left contains 10 more items, first extra item: 0.05
3 failed
```

GREEN after the implementation: the three tests pass; the Task 3 Verify line
(`tests/tasks/test_task_interactive.py tests/core/test_debugger.py
tests/specs/test_reactor_architecture.py tests/cli/test_cli_long_session.py`,
plus `tests/specs/test_ruff_policy.py`) reports `81 passed`; `./bin/pytest-pg
tests/tasks/test_task_interactive.py tests/core/test_debugger.py` reports
`35 passed`. Both retired constants are deleted from `weft/_constants.py` and
added to `RETIRED_REACTOR_CAPS`; `[CC-2.2.1]` is added to the
`weft/core/tasks/interactive.py` module docstring (the Task 5 docstring item,
landed with the code it describes).

Deviations:

- `test_interactive_drain_waits_once_while_descendant_holds_pipes` wraps
  `CommandSession.poll_stdout` at class level (a delegating counter) and
  installs the `time` recorder before the first `task.process_once()`, rather
  than wrapping the live instance after it. The first attempt at the plan's
  shape showed the parent can exit inside the turn that starts the session,
  so the instance wrap could miss the drain (one run never observed a live
  session). The counter still counts only real calls on the real session
  class; the bound 1..8 includes the creation turn's one flush.
- `_assert_quiet_interactive_stream_wakes_reactor` (used by
  `test_quiet_interactive_stdout_wakes_retained_watcher` and
  `test_quiet_interactive_stderr_wakes_retained_watcher`) asserted that the
  runner received the reactor strategy's bound `notify_activity` itself. Step 2
  of this task wraps that callback (set the task-local event, then call the
  received `on_activity`), so identity no longer holds by design. The assertion
  now proves the same contract behaviorally: invoking the runner's callback
  calls the reactor's `notify_activity` exactly once (a delegating counter on
  the real strategy) and sets `_interactive_activity`; the real quiet-stream
  wake through `drive_until` is unchanged. No ordering assertion changed.

#### Task 4. Agent-session handoff waits on the channel and the producer sentinel

Baseline (Task 0 item 3, D11 to D13) at `58c1de5b`, production unchanged.
Script `baseline_counts.py` (throwaway) drives the new test targets directly:
a 1.0 s silent item with no monitor and no predicate, and a monitored
session (`polling_interval=1.0`) holding one 2.0 s item with a Consumer-style
`cancel_requested` predicate:

```text
silent 1.0 s item: waits=20 distinct budgets=[0.05] status=ok
D12 monitored polling_interval=1.0, 2.0 s item, Consumer-style cancel predicate: samples=41 over 2.01s (20.4/s) status=ok
```

Lookup re-run before editing:
`grep -n "AgentSession(\|_response_receiver = \|class .*Receiver\|class .*Connection" tests/tasks/test_runner.py`
and `grep -rln "AgentSession" tests` (`tests/tasks/test_runner.py`,
`tests/tasks/test_task_execution.py`, `tests/core/test_public_extension_contracts.py`,
`tests/fixtures/public_extension_contract.py`, `tests/architecture/test_import_boundaries.py`).
The additional doubles the lookup found (the fake processes and monitors in
the earlier `AgentSession` unit tests) were left as they are: none reaches the
new wait or reads `polling_interval`, as the green run below proves.

Red at `58c1de5b` (new tests added, production unchanged):

```text
FAILED tests/tasks/test_runner.py::test_agent_session_short_items_cannot_starve_sampling
E           AssertionError: [0.025233708322048187, 0.00011949986219406128, 0.025195749942213297, ...]
E            +  where False = all(<generator object test_agent_session_short_items_cannot_starve_sampling.<locals>.<genexpr> ...>)
FAILED tests/tasks/test_runner.py::test_agent_session_silent_producer_waits_without_cadence
E            +  where False = all(<generator object test_agent_session_silent_producer_waits_without_cadence.<locals>.<genexpr> ...>)
2 failed, 2 passed  (the leaked-sender and monitor-interval guards pass)
```

The short-items gap failure at baseline is the sampling-on-every-pass
mutation. `test_wait_terminal_payload_returns_on_producer_sentinel` carries no
baseline-red claim (new helper); its `producer_sentinel=None, timeout=0.2`
mutation check returns `False` as asserted.

Intermediate red (the unconditional 50 ms term removed, waits on the channel
alone through `wait_terminal_payload(..., producer_sentinel=None, ...)`, no
sampling deadline):

```text
FAILED tests/tasks/test_runner.py::test_agent_session_samples_limits_at_monitor_interval
E           AssertionError: assert 'ok' == 'limit'
FAILED tests/tasks/test_runner.py::test_agent_session_leaked_sender_still_drains_after_producer_exit
E           AssertionError: assert 'channel_sealed' == 'drain_expired'
2 failed in 31.39s
```

(The leaked-sender run took about 30 s: with no sentinel the wait ended only
when the grandchild's 30 s hold released the channel.)

Rejected-shape mutation (the next-sample instant restarted at every
`execute()`, temporary edit, reverted):

```text
E           assert 1 >= 2
E            +  where 1 = len([2233703.900132])
samples=1 per-execute durations s: min=0.0211 max=0.0256 all<0.2=True
```

The durations (every item under the 0.2 s interval) are the evidence that
the mutation's prerequisite held.

GREEN after the implementation: the Task 4 Verify line
(`tests/core/test_terminal_handoff_transport.py tests/tasks/test_runner.py
tests/tasks/test_agent_execution.py tests/core/test_terminal_handoff.py`)
reports `851 passed`; `./bin/pytest-pg tests/core/test_terminal_handoff_transport.py
tests/tasks/test_runner.py` reports `215 passed`. After-counts at the
implementation:

```text
silent 1.0 s item: waits=1 distinct budgets=[None] status=ok
D12 monitored polling_interval=1.0, 2.0 s item, Consumer-style cancel predicate: samples=3 over 2.01s (1.5/s) status=ok
```

D12 at the default-shaped interval: three samples over a 2.0 s item at
`polling_interval=1.0` (at 0, 1 and 2 s), one per interval plus the immediate
first, down from 41.

Migrations applied (every behavioral assertion kept):
`test_agent_session_producer_exit_starts_bounded_drain` and
`test_session_stop_after_begin_drain_preserves_first_deadline` move their
fake clock wait to `sessions_module.wait_terminal_payload` through one shared
`_fake_clock_wait(clock)` helper; `test_agent_session_blocking_poll_failure_is_consumed_as_next_turn_transport_failure`
keeps the real receiver and injects through a delegating wrapper of
`sessions_module.wait_terminal_payload` that raises once (`_FailingFirstBlockingPollConnection`
stays for the host test); `LimitMonitor`, `ExplodingMetricsMonitor` and
`LateLimitMonitor` gain `polling_interval = DEFAULT_POLLING_INTERVAL`. Only the
first three and the first two monitors failed before migration; the
`fileno()` additions to `_FailingResponseReadConnection` and
`_WindowsSealedResponseConnection`, the `sentinel` attribute for the live
`Process` fake in `test_session_stop_effect_cannot_reset_absolute_drain_deadline`,
and any change to `test_agent_session_does_not_poll_limits_after_producer_exit`
were not needed (each passes unchanged: the step is terminal, or the dead
producer's exit is consumed before any wait reads the sentinel).

The [RM-5.1] Replacement block is applied verbatim to
`docs/specifications/06-Resource_Management.md` in this commit.

Deviations:

- The shared descendant construction lives in a new helper module,
  `tests/helpers/leaked_sender.py` (module-level, spawn-picklable targets
  `hold_sender`, `silent_leaking_producer`, `leaking_session_producer`, plus
  `wait_for_pidfile` and `kill_pid`), rather than being defined twice in the two
  test modules that use it.
- In `leaking_session_producer` the grandchild is started after `booted` and
  before `ready` (the plan: after taking the work item), and the producer then
  takes the work item and exits. The sender is still proven held by a live
  descendant before `execute()`, and the < 2.0 s bound then measures the
  drain rather than the grandchild's interpreter start-up under load.
- Windows: the plan's stop condition asks for the Windows CI slice before
  landing. This checkout cannot push, so the `PipeConnection`/sentinel pair in
  `multiprocessing.connection.wait` is not verified on Windows here; it is
  the documented Windows form of the same call. Recorded as open.

#### Task 5. After-measurements and traceability

Task 0 item 1 repeated after Task 4 (`da2e3b92`), same script and method:

```text
SQLite, da2e3b92:
pid=31004 vcs_delta=2205 cpu_delta_s=0.466 weft-plan2-id-6333904053:manager:running
pid=31006 vcs_delta=0 cpu_delta_s=0.000 /Users/van/Developer/weft/.venv/bin/python -B -c from multip
pid=31007 vcs_delta=2289 cpu_delta_s=0.331 /Users/van/Developer/weft/.venv/bin/python -B -c from multip
pid=31008 vcs_delta=2404 cpu_delta_s=0.315 /Users/van/Developer/weft/.venv/bin/python -B -c from multip
pid=31009 vcs_delta=3190 cpu_delta_s=0.381 /Users/van/Developer/weft/.venv/bin/python -B -c from multip
TOTAL window=30.0s vcs_delta=10088 (336.3/s) cpu_delta_s=1.492

PostgreSQL, da2e3b92:
pid=31107 vcs_delta=4046 cpu_delta_s=0.887 weft-plan2-id-6344750141:manager:running
pid=31109 vcs_delta=0 cpu_delta_s=0.000 /Users/van/Developer/weft/.venv/bin/python -B -c from multip
pid=31110 vcs_delta=3434 cpu_delta_s=0.162 /Users/van/Developer/weft/.venv/bin/python -B -c from multip
pid=31111 vcs_delta=3219 cpu_delta_s=0.089 /Users/van/Developer/weft/.venv/bin/python -B -c from multip
pid=31112 vcs_delta=9648 cpu_delta_s=0.155 /Users/van/Developer/weft/.venv/bin/python -B -c from multip
TOTAL window=30.0s vcs_delta=20347 (678.2/s) cpu_delta_s=1.293
```

| Measure | Backend | Before (`fbd79c66` / `58c1de5b`) | After (`da2e3b92`) |
|---|---|---|---|
| D1 idle manager tree, voluntary context switches over 30 s | SQLite | 18,663 (622/s); LivenessMonitor process 10,554 | 10,088 (336/s); LivenessMonitor process 3,190 |
| D1 idle manager tree, voluntary context switches over 30 s | PostgreSQL | 25,562 (852/s); LivenessMonitor process 15,150 | 20,347 (678/s); LivenessMonitor process 9,648 |
| D8 interactive drain with a descendant holding the pipes: interactive-module sleeps / `poll_stdout` calls | in-process (both backends run the test green) | 20 / 25 | 0 / 7 |
| D10 interactive STOP shutdown with a 0.5 s exit: interactive-module sleeps / `is_alive` calls | in-process | 10 / 24 | 0 / 6 |
| D11 silent 1.0 s agent work item, no predicate, no monitor: handoff waits / budgets | in-process | 20 / all 0.05 s | 1 / `None` |
| D12 monitored agent session, `polling_interval=1.0`, one 2.0 s item, Consumer-style predicate: samples | in-process | 41 (20.4/s) | 3 (at 0, 1 and 2 s: one per interval plus the immediate first) |

The D1 drop sits in the process whose context switches fell by about
7,400 (SQLite) and 5,500 (PostgreSQL) over 30 s, the eight idle LivenessMonitor
probe workers (about 160 wakes/s predicted; each idle wake costs more than
one switch). D8, D10, D11 and D12 are process-local loops whose counts do not
depend on the broker backend; their tests are `shared` and pass under
`./bin/pytest-pg` (Tasks 3 and 4). Item 2 (D5, D7) is not run (Task 2
deferred).

Traceability: a `## Related Plans` bullet for this plan in
`docs/specifications/01-Core_Components.md` (the second section, near the end)
and in `docs/specifications/06-Resource_Management.md`; `[CC-2.2.1]` added to
the `weft/core/tasks/interactive.py` module docstring in Task 3. The
`weft/core/tasks/service.py` and `weft/core/tasks/sessions.py` module
docstrings already cite the sections they implement. The disposable
PostgreSQL measurement container `weft-plan2-pg` was removed after this
measurement.

Final gates at the closing working tree (code identical to `da2e3b92`):

```text
$ ./.venv/bin/python -m pytest -p no:cacheprovider
6375 passed, 47 skipped
$ ./.venv/bin/mypy weft tests bin integrations/... extensions/... --config-file pyproject.toml
Success: no issues found in 484 source files
$ ./.venv/bin/ruff check .
All checks passed!
$ ./bin/check-doc-paths
check-doc-paths: OK (/Users/van/Developer/weft)
$ ./.venv/bin/python -m pytest -m "" tests/tasks tests/commands tests/core tests/specs -p no:cacheprovider
5237 passed, 45 skipped in 154.09s (0:02:34)
$ ./bin/pytest-pg --fast
6234 passed, 24 skipped in 288.30s (0:04:48)
```

#### Review fix: the handoff wait after a time-consuming limit sample (cross-family F1)

`AgentSession.execute` computed the wait from the pass's `now`, read before
the limit sample, while the next sampling instant is armed from a clock
reading taken after the sample, so a sample that takes time stretched the
requested wait past that instant. The wait is now computed from a fresh
`time.monotonic()` taken after the observation pass (observation precedence
unchanged). New test
`tests/tasks/test_runner.py::test_agent_session_wait_after_slow_sample_ends_at_the_sampling_deadline`:
a monitor whose `check_limits` sleeps 0.2 s at a 0.5 s `polling_interval`, a
0.4 s work item, `cancel_requested=None`; it asserts the first recorded wait
budget is at most the interval (plus 0.05 s) and ends no later than the
first sample's end plus one interval.

RED before the fix (at `4ac6a1e7`):

```text
E           assert 0.7036462081596255 <= (0.5 + 0.05)
1 failed in 1.83s
```

GREEN after the fix: `1 passed in 1.73s`; the Task 4 Verify line reports
`852 passed`; `./bin/pytest-pg tests/core/test_terminal_handoff_transport.py
tests/tasks/test_runner.py` reports `216 passed`.

Cross-family F2 and F3: a CHANGELOG `[Unreleased]` entry now covers the
plan's observable changes (including the owner-accepted D12 sampling change),
and the exact sources of `idle_tree.py` and `quiet_task.py` are in the Task 0
measurements above.

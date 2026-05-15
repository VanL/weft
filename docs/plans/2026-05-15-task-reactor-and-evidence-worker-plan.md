# Task Reactor And Evidence Worker Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.1], [CC-2.2], [CC-2.3], [CC-2.4]; docs/specifications/03-Manager_Architecture.md [MA-1.1], [MA-1.4], [MA-1.6a], [MA-1.7]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.3], [SB-0.4]; docs/specifications/05-Message_Flow_and_State.md [MF-2], [MF-3], [MF-5], [MF-6], [MF-7]; docs/specifications/07-System_Invariants.md [QUEUE.4], [QUEUE.5], [QUEUE.6], [EXEC.1], [MANAGER.8], [MANAGER.12], [MANAGER.15], [MANAGER.16], [OBS.12], [OBS.13]
Superseded by: none

## 1. Goal

Rework long-lived tasks around a reactor-shaped runtime: the main task thread
owns broker I/O, queue readiness, control messages, durable state commits, and
bounded timer wakeups; worker threads own non-broker blocking work and send
effect intents back to the main thread. Build this up in phases from
`BaseTask` through `Consumer`, `Manager`, `TaskMonitorTask`, and
`HeartbeatTask`, while preserving Weft's queue-first recovery model and
removing hot liveness/proof loops from manager service resolution.

This plan supersedes the narrower
[`2026-05-15-manager-hot-loop-reduction-plan.md`](./2026-05-15-manager-hot-loop-reduction-plan.md).
That draft correctly identified manager hot-loop proof work, but the better
root fix is a reusable task reactor boundary plus a manager evidence worker
that runs liveness detection on cadence instead of inline on every manager turn.

Implementation status, 2026-05-15: all phases landed. `MultiQueueWatcher`
stayed as-is. `BaseTask` owns the shared worker-result wake path. `Consumer`,
`Manager`, `TaskMonitorTask`, and `HeartbeatTask` now use bounded reactor
turns with broker effects committed on the owning main task thread. The
`BaseTask` worker-result lane is bounded and drained in bounded batches so
high-volume worker progress applies backpressure instead of starving queue or
control turns.

## 2. Source Documents

Source specs:

- [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md)
  [CC-2.1], [CC-2.2], [CC-2.3], [CC-2.4]: `MultiQueueWatcher`,
  `BaseTask`, specialized tasks, control, state, and the shared task-loop
  contract.
- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md)
  [MA-1.1], [MA-1.4], [MA-1.6a], [MA-1.7]: spawn dispatch, leadership,
  service convergence, PING/PONG manager control, and manager-owned internal
  service supervision.
- [`docs/specifications/04-SimpleBroker_Integration.md`](../specifications/04-SimpleBroker_Integration.md)
  [SB-0.3], [SB-0.4]: reserve/peek safety and backend-neutral watcher waits.
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-2], [MF-3], [MF-5], [MF-6], [MF-7]: per-task queue flow, control
  messages, task log state, manager spawn flow, and manager runtime state.
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [QUEUE.4], [QUEUE.5], [QUEUE.6], [EXEC.1], [MANAGER.8], [MANAGER.12],
  [MANAGER.15], [MANAGER.16], [OBS.12], [OBS.13]: reservation semantics,
  single in-flight reserved messages, manager leadership/service rules, PONG
  semantics, and task-monitor operational boundaries.

Related plans:

- [`2026-05-15-manager-hot-loop-reduction-plan.md`](./2026-05-15-manager-hot-loop-reduction-plan.md):
  superseded by this plan. Keep it as historical context for the production
  flamegraph, strace findings, and due-timer motivation.
- [`2026-05-10-control-and-service-convergence-state-machine-plan.md`](./2026-05-10-control-and-service-convergence-state-machine-plan.md):
  completed manager service reducer work. This plan must keep reducer
  decisions pure and side effects with the manager runtime owner.
- [`2026-05-10-manager-service-authority-boundary-hardening-plan.md`](./2026-05-10-manager-service-authority-boundary-hardening-plan.md):
  completed authority hardening. This plan must not allow caller-owned
  TaskSpec metadata to become service authority.
- [`2026-05-05-simplebroker-multiqueue-waiter-integration-plan.md`](./2026-05-05-simplebroker-multiqueue-waiter-integration-plan.md):
  completed multi-queue activity waiter integration. This plan should reuse
  that path rather than inventing another broker wait layer.
- [`2026-04-07-active-control-main-thread-plan.md`](./2026-04-07-active-control-main-thread-plan.md):
  completed active-control hardening. This plan must preserve the lesson that
  control acknowledgement and terminal publication belong to the main task
  ownership path, not a background broker poller.

Guidance documents:

- [`AGENTS.md`](../../AGENTS.md): repo entry point, house style, invariants,
  and test commands.
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md):
  spec precedence and preflight expectations.
- [`docs/agent-context/principles.md`](../agent-context/principles.md):
  queue truth, verification, and traceability rules.
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md):
  durable spine, real broker tests, and runtime boundaries.
- [`docs/agent-context/runbooks/runtime-and-context-patterns.md`](../agent-context/runbooks/runtime-and-context-patterns.md):
  queue handle reuse, spawn boundaries, generator history reads, and
  runtime-only queues.
- [`docs/agent-context/runbooks/testing-patterns.md`](../agent-context/runbooks/testing-patterns.md):
  harness selection and anti-mocking guidance.
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md):
  mandatory for this risky execution-path work.
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md):
  review gates for high-stakes runtime scheduling changes.

## 3. Context And Key Files

### Plan-Start Structure

At plan start, `MultiQueueWatcher` owned multiple queue handles, per-queue modes
(`READ`, `PEEK`, `RESERVE`), active-queue scheduling, priority drain behavior,
and the backend-neutral wait seam. It calls
`simplebroker.create_activity_waiter_for_queues(...)` when available and
otherwise falls back to a timeout wait. It also forces an inactive-queue probe
after the wait seam observes pending work.

`BaseTask` mapped `TaskSpec.io` to queues, owned state logging,
control handling, reserved-policy helpers, process titles, cleanup, and the
shared `process_once()` / `run_until_stopped()` / `next_wait_timeout()`
contract.

`Consumer` executed work synchronously inside
`_handle_work_message()`. While active work is running, control handling relies
on runner/session cooperation rather than a fully free main I/O loop.

`Manager` owned spawn reservation, child launch, manager registry,
service convergence, leadership checks, PING/PONG, and child cleanup. It has
due timers through `next_wait_timeout()`, but expensive proof work could still be
coupled to ordinary manager turns.

`TaskMonitorTask` was the best existing reference for the desired outer loop:
`process_once()` is bounded, and `next_wait_timeout()` exposes scheduled work.
Its scan and cleanup paths are broker work, so worker threads must not take
over those commits.

`HeartbeatTask` had the same timer concept, but hid it behind a
private `_next_wait_timeout()` and an inner wait loop inside `process_once()`.
This needed to be aligned later, not copied.

### Files To Modify

Core runtime:

- `weft/_constants.py`: add narrowly named reactor cadence constants only if
  existing constants do not fit. Expected new constant:
  `TASK_REACTOR_WAKEUP_MAX_SECONDS = 0.05`.
- `weft/core/tasks/base.py`: primary owner of reactor-thread broker commits,
  worker result queues, local wake events, worker shutdown, and shared helper
  methods.
- `weft/core/tasks/reactor.py` (optional): add only if shared dataclasses or
  private helpers would make `base.py` harder to read. This module must stay
  small, internal, and broker-free. Do not create a general framework.
- `weft/core/launcher.py`: keep timeout semantics aligned with
  `BaseTask.run_until_stopped()`. Touch only if the BaseTask hook semantics
  change.
- `weft/core/tasks/consumer.py`: split reserved-message handling from
  non-broker execution and route active work through the BaseTask worker lane.
- `weft/core/tasks/runner.py`, `weft/core/tasks/sessions.py`,
  `weft/core/runners/host.py`, `weft/core/runners/subprocess_runner.py`: touch
  only when Consumer needs a thread-safe non-broker control bridge for active
  STOP/KILL. Do not redesign runner plugins in this plan unless a test proves
  it is required.
- `weft/core/manager.py`: move child launch blocking and liveness evidence
  collection behind worker/result channels while keeping broker commits and
  service side effects on the main thread.
- `weft/core/manager_services.py`: pure reducer updates only if the manager
  needs an explicit cached-evidence freshness field. Do not add queue reads,
  sleeps, logging-as-correctness, or side effects here.
- `weft/core/service_convergence.py`: only if manager registry snapshot
  request/response types need a pure parser boundary. Prefer existing helpers.
- `weft/core/tasks/task_monitor.py`: align monitor scheduling with the shared
  reactor contract and offload only broker-free processor work.
- `weft/core/tasks/heartbeat.py`: remove the private inner wait loop and use
  the shared BaseTask outer-loop contract.
- `weft/core/tasks/__init__.py`: update exports only if a new internal helper
  must be imported by tests. Prefer no new public export.

Tests:

- `tests/tasks/test_task_execution.py`: BaseTask loop, Consumer, active
  control, and process-entry regression tests already live here.
- `tests/tasks/test_multiqueue_watcher.py`: use only for the initial
  `MultiQueueWatcher` check or a narrow wait-seam fix.
- `tests/tasks/test_runner.py`, `tests/tasks/test_command_runner_parity.py`,
  `tests/tasks/test_task_interactive.py`: update only for active control or
  session behavior directly touched by Consumer.
- `tests/core/test_manager.py`: manager reactor, launch worker, service
  evidence cadence, and hot-loop regressions.
- `tests/specs/manager_architecture/test_manager_state_events.py`: use for
  spec-level manager event regressions if `task_spawned`, registry heartbeat,
  or service events change.
- `tests/commands/test_task_monitor.py` and `tests/tasks/test_task_monitor.py`:
  TaskMonitor scheduling and processor behavior.
- `tests/tasks/test_heartbeat.py`: HeartbeatTask outer-loop alignment.
- `tests/specs/test_plan_metadata.py`: plan metadata/index verification.

Documentation:

- `docs/specifications/01-Core_Components.md`: update implementation notes and
  related plan backlink when implementation lands.
- `docs/specifications/03-Manager_Architecture.md`: update [MA-1.6a] once
  manager liveness evidence moves to the worker cadence.
- `docs/specifications/04-SimpleBroker_Integration.md`: update only if
  `MultiQueueWatcher` wait semantics change.
- `docs/specifications/05-Message_Flow_and_State.md`: update if control,
  terminal, or spawn-flow timing changes visibly.
- `docs/specifications/07-System_Invariants.md`: update if the implementation
  adds new invariants around reactor ownership or worker evidence.
- `docs/lessons.md`: add a lesson if implementation confirms a repeated
  failure mode, such as "liveness proof in a hot task loop."

### Required Reading Before Editing

Read these in order before implementing any phase:

1. `AGENTS.md`, especially design philosophy, invariants, style, and test
   commands.
2. `docs/specifications/01-Core_Components.md` [CC-2.1] through [CC-2.4].
3. `docs/specifications/03-Manager_Architecture.md` [MA-1.1], [MA-1.4],
   [MA-1.6a], [MA-1.7].
4. `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.3], [SB-0.4].
5. `docs/specifications/05-Message_Flow_and_State.md` [MF-2], [MF-3],
   [MF-5], [MF-6], [MF-7].
6. `docs/specifications/07-System_Invariants.md` queue, execution,
   manager, and observability invariants.
7. `docs/agent-context/runbooks/runtime-and-context-patterns.md`.
8. `docs/agent-context/runbooks/testing-patterns.md`.
9. Existing code in this order:
   - `weft/core/tasks/multiqueue_watcher.py`
   - `weft/core/tasks/base.py`
   - `weft/core/launcher.py`
   - `weft/core/tasks/consumer.py`
   - `weft/core/manager.py`
   - `weft/core/tasks/task_monitor.py`
   - `weft/core/tasks/heartbeat.py`
10. Existing tests nearest each phase before adding new tests.

Comprehension checks before editing:

- Which thread is allowed to touch `Queue` objects and `TaskSpec.state`?
- Which queue operation proves dispatch authority for public manager work?
- What broker-visible evidence must exist if a task dies while a work item is
  in a Python worker queue?
- Why is a matched PONG live evidence, but a missing PONG not immediate kill
  authority?
- Which expensive manager proof paths should run on every queue wake, and
  which should be cadence-bound?
- Why is `TaskMonitorTask` a better loop model than `HeartbeatTask` today?

### Tooling And Style

- Use `apply_patch` for edits. Do not create files with shell heredocs.
- Keep imports at module top. Use `collections.abc` for abstract types.
- Use modern type hints: `X | None`, `dict[str, Any]`, `tuple[...]`.
- Constants belong in `weft/_constants.py`.
- New functions need complete type annotations.
- Comments should explain non-obvious concurrency or queue ownership only.
- Do not add dependencies.
- Do not create a public API unless a spec requires it.
- Do not introduce `asyncio`; the runtime is thread/process based today.
- Do not use mocks for queue semantics when `broker_env` or
  `WeftTestHarness` can exercise the real path.

## 4. Target Architecture

### Thread Ownership Model

Main task thread, also called the reactor thread:

- owns every `Queue` handle in a task process
- performs all broker reads, writes, moves, deletes, peeks, and history scans
- mutates `TaskSpec.state`
- writes `weft.log.tasks`, task outboxes, task `ctrl_out`, service-owner rows,
  TID mappings, streaming markers, and endpoint records
- applies reserved-message policies
- handles PING/STATUS/STOP/KILL control messages from task-local `ctrl_in`
- drains worker results and applies durable effects
- computes or delegates `next_wait_timeout()`

Worker threads:

- do not construct or use `Queue`
- do not call `BaseTask._queue()`, `_report_state_change()`,
  `_send_control_response()`, `_send_terminal_envelope()`,
  `_apply_reserved_policy()`, or any helper that writes broker state
- do not mutate `TaskSpec.state`, `TaskSpec.spec`, or `TaskSpec.io`
- may run blocking non-broker work such as target execution, child process
  launch, runner wait loops, pure validation, process liveness probes, and
  evidence reduction over snapshots supplied by the reactor
- communicate only through in-process `queue.Queue` and `threading.Event`
  objects owned by `BaseTask`

This worker-thread rule applies to Weft runtime code. A user-supplied Python
function target can still import whatever it wants; do not try to police user
code imports or inspect arbitrary target internals. The implementation goal is
that Weft's task runtime never depends on worker-thread broker commits for its
own correctness.

The Python queue is not durable truth. It is an in-process handoff for work
already durably reserved or for evidence requests that can be retried. If the
task process dies while work is in the Python queue or a worker thread, the
broker reserved queue or prior durable state remains the recovery surface.

### Event Types

The reactor recognizes four event families:

- queue activity from `MultiQueueWatcher`
- local worker result activity from the BaseTask result queue
- due timer activity from `next_wait_timeout()`
- process/control signals that set the task stop event

BaseTask should expose a small private API for worker handoff. Names may vary,
but the behavior must be this narrow:

- submit one callable or request to a named worker lane
- set a local event when a worker result arrives
- drain worker results on the main thread before and after queue work
- apply subclass-owned result handlers on the main thread
- join and clean up worker threads during task cleanup
- bound local wake waits by `TASK_REACTOR_WAKEUP_MAX_SECONDS`

Do not create a generic plugin-style event framework, scheduler DSL, or public
reactor API. The first implementation should be just enough shared machinery
to remove duplicate thread/result plumbing across Consumer, Manager,
TaskMonitorTask, and HeartbeatTask.

### Effect Intent Model

Workers return facts or requests, not broker commits. A worker result may say:

- execution succeeded with output and metrics
- execution failed with an exception summary
- child launch succeeded with a `multiprocessing.Process` handle and PID
- child launch failed with an error string
- liveness evidence for a candidate is live/stale/unknown
- a processor produced a report-only result
- active work observed a return code, timeout, or resource violation

Only the reactor converts those results into:

- `weft.log.tasks` rows
- outbox messages
- `ctrl_out` PONG/STATUS/ACK/terminal envelopes
- reserved message delete/requeue/keep decisions
- service-owner rows
- manager child bookkeeping
- TaskSpec state transitions

### Manager Evidence Worker

Manager liveness/service proof must become cadence-bound evidence, not hot
loop work. The manager gets an internal evidence worker that wakes about once
per active cadence, normally one second, or when the reactor invalidates it
because accepted work or child progress occurred.

The evidence worker:

- chooses which manager/service candidates need liveness proof
- requests reactor-owned broker snapshots or PING probes when needed
- performs pure reduction over snapshots and prior cache entries
- produces a `ManagerEvidenceSnapshot` with per-candidate state, source,
  observed time, expiry, and reason

The evidence worker must not read `weft.state.*`, `weft.log.tasks`,
`ctrl_out`, or `ctrl_in` directly. The reactor may read those queues on behalf
of the worker and return snapshots. Service convergence consumes the latest
cached evidence snapshot. If the snapshot is stale or absent, service
convergence should usually publish/retain degraded wait state rather than
running proof inline.

PING for "is another manager alive" remains a reactor-to-reactor proof:

1. local manager evidence worker requests a probe
2. local manager reactor writes keyed PING to target `ctrl_in`
3. target manager reactor answers PONG on target `ctrl_out`
4. local manager reactor reads the matching PONG
5. local evidence worker caches live evidence with TTL

This proves the target manager's main Weft I/O path responded. It does not
prove a lease, and a missing PONG is `unknown` or stale-looking evidence, not
automatic kill authority.

## 5. Invariants And Constraints

- Preserve the durable spine:
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log`.
- `MultiQueueWatcher` stays as-is unless Phase 0 proves that BaseTask cannot
  achieve the reactor contract without a narrow wait-seam fix. Do not redesign
  it preemptively.
- The main task thread is the only thread allowed to touch broker queues and
  mutable task state inside a task process.
- Worker threads must not construct `Queue`, use `_queue()`, mutate
  `TaskSpec`, or call broker-writing BaseTask helpers.
- A Python in-process queue must never become durable truth. Broker reserved
  queues and task logs remain the recovery surface.
- Keep one active reserved work item per task unless a later spec explicitly
  adds concurrency. Do not add per-task parallel work execution in this plan.
- Preserve reserved queue policy: `keep`, `requeue`, and `clear` must remain
  explicit and tested.
- Preserve forward-only state transitions and terminal-state rules.
- Preserve `TaskSpec.spec`, `TaskSpec.io`, and resolved `tid` immutability.
- Preserve spawn-based process behavior for broker-connected child processes.
- Preserve manager public spawn dispatch authority: atomic reservation of the
  exact message authorizes the launch attempt, not registry leadership.
- Preserve manager service authority rules. Caller-owned TaskSpec metadata
  alone must not authorize singleton service ownership.
- Preserve PING/PONG semantics: matched keyed PONG is positive live evidence;
  absent PONG is not immediate proof of death.
- Preserve runtime-only semantics for `weft.state.*` queues.
- Preserve generator-based history reads for append-only queues such as
  `weft.log.tasks` and manager/service registries.
- Do not change queue names, TaskSpec schema, public CLI output, result payload
  shape, service-owner payload schema, or manager registry schema unless an
  implementation blocker proves a spec update is required.
- Do not add a new dependency, scheduler framework, or public plugin surface.
- Do not mock queues, reservation, manager lifecycle, or process behavior in
  tests that claim to prove those contracts.
- Keep changes bite-sized. Do not refactor unrelated code while touching these
  large modules.
- Stop and re-plan if a phase wants a second broker path, a second state
  database, worker-thread broker access, or multiple concurrent reserved work
  items.

Failure priority:

- Fatal: lost reserved work, duplicate execution from one reservation,
  non-forward state transition, worker broker writes, missing STOP/KILL
  terminal publication, manager launching after a STOP/KILL fence.
- Best effort: operational serve-log emission, optional debug fields, cached
  liveness detail in PONG.
- Degraded but not fatal: stale or unavailable manager liveness snapshot.
  Service convergence should report/wait rather than hot-scan the world inline.

Rollback:

- Each phase must leave existing queue names, payloads, and public CLI surfaces
  backward compatible.
- If a phase fails after landing, revert that phase without requiring queue
  cleanup or data migration.
- The old synchronous behavior should remain reachable until the replacement
  path is proven by tests for that task class. Do not ship a permanent dual
  execution path or configuration flag unless a later spec explicitly requires
  it.

Review gates:

- Self-review is required after drafting this plan.
- External review is required before implementation-ready signoff because this
  changes runtime scheduling and crosses BaseTask, Consumer, Manager, Monitor,
  and Heartbeat behavior.
- Run slice reviews after Phase 1, Phase 2, Phase 3, and final Phase 5.

## 6. Phased Tasks

### Phase 0. Confirm MultiQueueWatcher Boundary

Outcome: prove whether `MultiQueueWatcher` can stay unchanged for this plan.
The expected result is "yes, unchanged," except possibly for a narrow wait-seam
test or a small helper that exposes existing behavior.

Files to read:

- `weft/core/tasks/multiqueue_watcher.py`
- `weft/core/tasks/base.py`
- `weft/core/queue_wait.py`
- `docs/specifications/01-Core_Components.md` [CC-2.1]
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4]
- `tests/tasks/test_multiqueue_watcher.py`

Files to touch:

- Prefer no production change.
- If needed, only touch `weft/core/tasks/multiqueue_watcher.py` and
  `tests/tasks/test_multiqueue_watcher.py`.

Required checks:

- Confirm `MultiQueueWatcher` owns queue mode dispatch, active queue probing,
  and multi-queue activity waits.
- Confirm BaseTask can add local worker-result wakeups without changing queue
  mode semantics.
- Confirm whether SQLite manual `wait_for_activity()` currently uses only a
  timeout fallback while Postgres uses the multi-queue waiter. If true, document
  that the 0.5 second BaseTask wake cap is the SQLite fallback for this plan.

Tests:

- Add or update a `tests/tasks/test_multiqueue_watcher.py` regression only if
  the check exposes a missing contract. Useful assertions:
  - `wait_for_activity(timeout=...)` must not drain messages.
  - pending precheck must force inactive queue probing on the next drain.
  - dynamic queue-set changes close stale waiters.

Do not:

- Do not add thread pools or worker queues to `MultiQueueWatcher`.
- Do not change queue mode names or reservation semantics.
- Do not create a second watcher implementation.

Done when:

- The implementer can state in the Phase 0 notes whether `MultiQueueWatcher`
  remains unchanged.
- Any narrow test added in this phase passes:
  `./.venv/bin/python -m pytest tests/tasks/test_multiqueue_watcher.py -q`.

Stop and re-plan if:

- The desired BaseTask worker-result wakeup requires changing how
  `MultiQueueWatcher` fetches, reserves, or acks queue messages.
- The implementation starts moving queue determination into task subclasses.

### Phase 1. Add BaseTask Reactor Worker Lane

Outcome: `BaseTask` gets the reusable, private reactor primitives needed by
all later phases: worker result queues, local wake events, bounded wait chunks,
main-thread result draining, and cleanup. No task behavior should change yet.

Files to read:

- `weft/core/tasks/base.py`
- `weft/core/launcher.py`
- `tests/tasks/test_task_execution.py`
- `docs/specifications/01-Core_Components.md` [CC-2.2], [CC-2.4]
- `docs/specifications/07-System_Invariants.md` [QUEUE.4], [QUEUE.5],
  [QUEUE.6]
- `docs/lessons.md` "STOP Pollers" entries

Files to touch:

- `weft/core/tasks/base.py`
- optional `weft/core/tasks/reactor.py`
- `weft/_constants.py`
- `tests/tasks/test_task_execution.py` or a new
  `tests/tasks/test_base_task_reactor.py`
- `weft/core/launcher.py` only if needed to keep timeout semantics identical

Implementation guidance:

- Add a narrowly scoped internal worker lane. Suggested private types:
  `WorkerRequest`, `WorkerResult`, `WorkerFailed`, or similar. Keep them
  broker-free.
- Use `queue.Queue` for worker-to-reactor results and `threading.Event` to wake
  the main thread.
- Add helper methods with clear names, for example:
  - `_submit_worker_call(...)`
  - `_drain_worker_results(...)`
  - `_handle_worker_result(...)`
  - `_has_pending_worker_results()`
  - `_stop_worker_lanes(...)`
- If a new helper module is used, it should contain dataclasses and local
  executor plumbing only. It must not import `simplebroker.Queue`.
- `BaseTask.process_once()` should drain worker results before queue work and
  again after queue work so results are committed promptly.
- `BaseTask.wait_for_activity()` should check local worker results before and
  after broker wait. It should bound the wait by
  `TASK_REACTOR_WAKEUP_MAX_SECONDS`, expected to be 0.05 seconds, so a worker
  result does not wait behind a long due timer on SQLite fallback paths.
- The worker-result queue must be bounded, and `_drain_worker_results()` must
  apply only a bounded batch per call. A high-volume stream producer should not
  grow memory without bound or keep the reactor from polling control.
- `BaseTask.cleanup()` / `stop()` must stop and join worker lanes before
  closing queues if a worker could still publish a result intent. Workers must
  not be allowed to call back into a closed BaseTask.
- Preserve default behavior: if no worker lane is used, `BaseTask` should act
  as it does today.

TDD tests:

- Red test first: create a small test task subclass that submits a worker result
  and verify `process_once()` applies it on the main thread through a subclass
  hook.
- Red test first: verify `wait_for_activity(timeout=5.0)` returns within the
  wake cap after a worker result event is set. Use a fake or short worker call;
  do not sleep for five seconds.
- Regression: `BaseTask.run_until_stopped()` still honors
  `next_wait_timeout()` exactly as existing tests expect.
- Regression: `launcher._task_process_entry()` still calls
  `next_wait_timeout()` and `wait_for_activity()` with matching semantics.
- Cleanup test: a task with a running worker lane stops without leaving a live
  thread and without queue-close errors.
- Backpressure regression: fill a tiny worker-result queue, prove the publisher
  blocks until the reactor drains one result, then prove both results arrive in
  order.
- Responsiveness regression: while a live command stream has more pending
  worker events than the per-turn drain budget, write a task-local PING and
  prove the Consumer answers before all stream events are drained.

What not to mock:

- Use `broker_env` and a real `Consumer` or a real `BaseTask` test subclass.
  Do not mock `simplebroker.Queue`.

Verification:

- `./.venv/bin/python -m pytest tests/tasks/test_task_execution.py -k "run_until_stopped or process_entry or reactor" -q`
- If a new test file is added:
  `./.venv/bin/python -m pytest tests/tasks/test_base_task_reactor.py -q`

Stop and re-plan if:

- Worker result application requires worker-thread broker writes.
- Multiple task classes need incompatible worker APIs.
- The implementation grows into a generic scheduler framework.

### Phase 2. Convert Consumer Active Work To Reactor Handoff

Outcome: `Consumer` reserves work on the main thread, runs blocking user work
in one worker lane, and commits outbox/state/reserved-policy effects on the
main thread. The main thread remains responsive to PING/STATUS/STOP/KILL while
work is active.

Files to read:

- `weft/core/tasks/consumer.py`
- `weft/core/tasks/base.py`
- `weft/core/tasks/runner.py`
- `weft/core/tasks/sessions.py`
- `weft/core/runners/host.py`
- `weft/core/runners/subprocess_runner.py`
- `docs/specifications/01-Core_Components.md` [CC-2.3], [CC-2.4]
- `docs/specifications/05-Message_Flow_and_State.md` [MF-2], [MF-3], [MF-5]
- `docs/specifications/07-System_Invariants.md` [EXEC.1], [QUEUE.5]
- `docs/plans/2026-04-07-active-control-main-thread-plan.md`
- `tests/tasks/test_task_execution.py`
- `tests/tasks/test_control_channel.py`
- `tests/tasks/test_task_interactive.py`

Files to touch:

- `weft/core/tasks/consumer.py`
- `weft/core/tasks/base.py` only for missing shared helper hooks discovered by
  Consumer
- runner/session files only if needed for a non-broker control bridge
- `tests/tasks/test_task_execution.py`
- `tests/tasks/test_control_channel.py`
- `tests/tasks/test_task_interactive.py` when interactive behavior is touched

Implementation guidance:

- Keep one active work item per Consumer. Add explicit state such as
  `_active_worker_message_id` if needed. Do not reserve the next inbox message
  while active work is still in flight.
- Split current `_handle_work_message()` into:
  - main-thread reservation/start bookkeeping
  - worker-thread broker-free execution
  - main-thread result commit
- The worker may execute target code, wait for subprocesses, collect stdout,
  observe return code, and build a result object.
- The worker must not write to outbox, task log, ctrl_out, TID mappings,
  streaming queues, or reserved queues.
- The worker-created runner and monitor must not receive broker target or
  config handles. That prevents indirect database access through helper code
  such as metric timestamp generation.
- The main thread applies:
  - outbox writes
  - `work_item_completed` / `work_completed` / failed / timeout / killed state
    events
  - terminal envelopes
  - reserved message delete/requeue/keep
  - activity and process-title updates
- Preserve active STOP/KILL semantics from the April 7 plan:
  - main thread handles incoming control promptly
  - active runner/session receives a non-broker control signal or request
  - terminal publication remains on the main thread after active work unwinds
  - no background broker poller is introduced
- PING/STATUS during active work should reply from main-thread cached state.
  Fresh worker details are optional and must not block the reply.

TDD tests:

- Red test first: start a persistent Consumer with a long-running command,
  send PING while the command is active, and assert a matching PONG appears
  without waiting for the command to finish.
- Red test first: send STOP to an active Consumer and assert:
  - ctrl_out ACK appears promptly
  - terminal state is eventually `cancelled`
  - reserved policy is applied by the main thread
  - task process exits cleanly
- Regression: ordinary one-shot function task still writes the same outbox
  result and terminal log.
- Regression: failed target still applies the configured reserved policy.
- Regression: persistent Consumer returns to `waiting` after a work item and
  can process the next item later.
- Interactive regression if touched: line-oriented interactive output and
  terminal control envelopes still work.

What not to mock:

- Use real `Consumer`, real broker queues, and real subprocess/function targets
  from existing test fixtures.
- It is acceptable to monkeypatch a small worker function to force a timing
  edge, but not as the only proof of reservation, outbox, or state behavior.

Verification:

- `./.venv/bin/python -m pytest tests/tasks/test_task_execution.py -q`
- `./.venv/bin/python -m pytest tests/tasks/test_control_channel.py -q`
- If interactive touched:
  `./.venv/bin/python -m pytest tests/tasks/test_task_interactive.py -q`

Stop and re-plan if:

- The implementation wants more than one active reserved message per Consumer.
- Worker code needs broker writes to preserve existing result semantics.
- STOP/KILL terminal publication moves into the worker.

### Phase 3. Convert Manager Launch And Evidence To Reactor Handoff

Outcome: Manager keeps broker commits and service side effects on the main
thread, moves blocking child launch into a worker lane, and moves recurring
manager/service liveness proof into a cadence-bound evidence worker.

Implementation note, 2026-05-15: the child-launch worker sub-slice,
manager-to-manager non-blocking PING probe sub-slice, and service-candidate
non-blocking PING probe sub-slice are implemented. The next phase is
TaskMonitorTask reactor alignment.

Implementation note, 2026-05-15: TaskMonitorTask custom processor execution is
implemented as a broker-free worker lane. Task-log scans, built-in cleanup,
checkpoint advancement, cached diagnostics, and exact deletes remain on the
TaskMonitor reactor.

Files to read:

- `weft/core/manager.py`
- `weft/core/launcher.py`
- `weft/core/manager_services.py`
- `weft/core/service_convergence.py`
- `weft/core/tasks/base.py`
- `weft/core/tasks/task_monitor.py`
- `docs/specifications/03-Manager_Architecture.md` [MA-1.1], [MA-1.4],
  [MA-1.6a], [MA-1.7]
- `docs/specifications/05-Message_Flow_and_State.md` [MF-3], [MF-6], [MF-7]
- `docs/specifications/07-System_Invariants.md` [MANAGER.8], [MANAGER.12],
  [MANAGER.15], [MANAGER.16], [OBS.12], [OBS.13]
- `tests/core/test_manager.py`
- `tests/specs/manager_architecture/test_manager_state_events.py`

Files to touch:

- `weft/core/manager.py`
- optional small private helper module only if manager evidence dataclasses
  would otherwise make `manager.py` unreadable. Prefer
  `weft/core/manager_evidence.py` only for pure dataclasses/reducers, no queue
  I/O.
- `weft/core/manager_services.py` only for pure evidence freshness support
- `tests/core/test_manager.py`
- `tests/specs/manager_architecture/test_manager_state_events.py` if event
  shape/timing needs a spec-level regression

Implementation guidance:

Manager child launch:

- Main thread reserves the spawn request through existing
  `_process_queue_message()` / `_handle_work_message()` flow.
- Main thread performs broker writes that must happen before process start,
  such as child inbox seeding.
- Worker thread may call `launch_task_process(...)`.
- Worker returns `LaunchResult(child_tid, process, pid, launched_ns, error)`.
- Main thread records `_child_processes`, writes `task_spawned`, registers
  service-owner rows, updates service state, deletes the exact reserved spawn
  message, or applies reserved policy on failure.
- If launch succeeds in the worker but the worker result is delayed, the
  visibility gap must be bounded by the BaseTask local wake cap.

Manager evidence worker:

- Add a cadence-bound evidence worker or lane owned by Manager. It wakes on a
  configured interval, normally one second for active convergence and a slower
  stable audit interval when idle, plus explicit invalidation after child
  progress or accepted internal spawn work.
- The worker does not read queues. It receives snapshots supplied by the main
  thread or requests that the main thread gather snapshots/probes.
- Main thread handles evidence requests:
  - read manager/service registry snapshots using generator helpers
  - write keyed PINGs
  - read matching PONGs from target ctrl_out queues
  - read targeted terminal proof only on cadence or explicit recovery paths
- Worker reduces evidence into a cached immutable snapshot, for example:
  `ManagerEvidenceSnapshot(candidates=..., observed_at_ns=..., expires_at_ns=...)`.
- Service convergence consumes the latest snapshot. It must not run broad
  registry replay, task-log replay, or PING fallback inline merely because a
  queue wake occurred.
- If evidence is stale and a decision is not urgent, service convergence should
  expose degraded wait/uncertain state through existing service reducer paths.
- Accepted spawn work, shutdown, and local child exit may request immediate
  evidence refresh, but the main thread still owns any broker reads/writes.

Manager PING/PONG:

- The target manager's main thread still answers PING to itself. Do not route
  inbound PING through the evidence worker.
- The probing manager's evidence worker may decide to probe another manager,
  but the probing manager's main thread writes the PING and reads the PONG.
- PONG cache entries must include request ID, target TID, role, queue identity,
  context, observation time, and TTL.

Hot-loop removal:

- Move expensive proof methods out of unconditional `process_once()` cadence.
  Candidates include `_read_active_manager_records`,
  `_observed_service_candidates_by_key`, `_child_terminal_proof_visible`,
  keyed PING fallback, and broad task-log scans.
- Keep fast local checks on every turn:
  - pending control
  - accepted internal/public spawn drains
  - local tracked child reap
  - due timer computation from cached local state

TDD tests:

- Red test first: manager receives a public spawn request while child launch is
  delayed in the worker; PING/STATUS to the manager still receives a response.
- Red test first: after worker launch success, main thread writes exactly one
  `task_spawned` event and deletes exactly the reserved spawn message.
- Red test first: launch worker failure applies the existing reserved policy
  and does not lose the spawn request silently.
- Red test first: repeated queue wakes without evidence due time do not call
  broad liveness/proof methods. Counting monkeypatches are acceptable here
  because the real queue behavior should also be exercised in the same test.
- Red test first: when evidence is due, the manager requests a PING/proof
  cycle and caches live/unknown evidence without blocking spawn dispatch.
- Regression: public spawn work-stealing still uses atomic reservation as
  launch authority even if leadership evidence is stale.
- Regression: internal service spawn work drains before public spawn work.
- Regression: manager STOP/KILL drain fences still prevent new launch after
  control is pending.
- Regression: duplicate manager yield still requires positive lower-TID live
  proof and does not treat missing PONG as kill authority.

What not to mock:

- Use real broker queues and real Manager objects for spawn/control behavior.
- Use real `launch_task_process(...)` in at least one integration regression.
- Counting monkeypatches are allowed only to prove cadence of expensive proof
  methods, not to replace queue/process proof.

Verification:

- `./.venv/bin/python -m pytest tests/core/test_manager.py -q`
- `./.venv/bin/python -m pytest tests/specs/manager_architecture/test_manager_state_events.py -q`
- If manager CLI behavior is affected:
  `./.venv/bin/python -m pytest tests/commands/test_manager_commands.py -q`

Stop and re-plan if:

- The evidence worker needs direct queue access.
- Service convergence starts bypassing `reduce_managed_service_state`.
- PONG becomes a lease or kill authority.
- Child launch commit/delete happens in the worker thread.
- The implementation needs a new manager registry payload schema.

### Phase 4. Align TaskMonitorTask With Reactor Boundary

Outcome: `TaskMonitorTask` keeps broker scans/deletes on the main thread,
offloads only broker-free processor work when useful, and continues to expose
bounded scheduling through `next_wait_timeout()`.

Files to read:

- `weft/core/tasks/task_monitor.py`
- `weft/core/task_monitoring.py`
- `weft/core/tasks/task_monitor_cleanup.py`
- `weft/core/pruning/` modules used by cleanup
- `docs/specifications/01-Core_Components.md` [CC-2.3]
- `docs/specifications/07-System_Invariants.md` [OBS.13]
- `tests/commands/test_task_monitor.py`
- `tests/tasks/test_task_monitor.py`

Files to touch:

- `weft/core/tasks/task_monitor.py`
- `weft/core/task_monitoring.py` only for pure request/result type clarity
- `tests/commands/test_task_monitor.py`
- `tests/tasks/test_task_monitor.py`

Implementation guidance:

- Keep task-log scans, checkpoint advancement, cleanup candidate selection,
  and exact deletes on the main thread.
- If custom processor work is slow and broker-free, send only the candidate
  snapshot to a worker and return a processor result intent.
- Built-in destructive processors must not delete from a worker. The worker may
  classify or format, but main thread applies exact deletes through existing
  pruning helpers.
- Preserve `next_wait_timeout()` as the scheduling source. Do not add an inner
  wait loop.
- PONG for TaskMonitor must report cached diagnostics only. It must not trigger
  scans or deletes.

TDD tests:

- Red test first: a slow custom processor does not block PING/STATUS to the
  TaskMonitor task.
- Regression: built-in delete processor still deletes only safe exact
  candidates through the canonical prune path.
- Regression: disabled monitor sleeps through the activity wait cap and does
  not scan task logs.
- Regression: `next_wait_timeout()` returns `0.0` for first cycle, wake
  request, or pending control/inbox input.

What not to mock:

- Use real broker queues for task-log and cleanup behavior.
- Do not mock pruning policy when testing destructive safety. Assert real
  queue contents before and after.

Verification:

- `./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py -q`
- `./.venv/bin/python -m pytest tests/commands/test_task_monitor.py -q`

Stop and re-plan if:

- A worker needs to read or delete broker rows.
- TaskMonitor PONG starts recomputing cleanup stats live.
- The implementation invents a second cleanup path.

### Phase 5. Align HeartbeatTask With Shared Outer Loop

Outcome: `HeartbeatTask` stops running its own inner wait loop and uses the
shared BaseTask reactor contract. Heartbeat queue writes remain main-thread
broker commits.

Implementation note, 2026-05-15: HeartbeatTask now exposes the public
`next_wait_timeout()` hook and `process_once()` returns after one bounded
reactor turn. Registration emits, reserved-policy cleanup, idle shutdown, and
singleton supersession checks remain on the main task thread.

Files to read:

- `weft/core/tasks/heartbeat.py`
- `weft/core/tasks/base.py`
- `docs/specifications/01-Core_Components.md` [CC-2.3], [CC-2.4.1]
- `docs/specifications/05-Message_Flow_and_State.md` [MF-3.1], [MF-6]
- `tests/tasks/test_heartbeat.py`

Files to touch:

- `weft/core/tasks/heartbeat.py`
- `tests/tasks/test_heartbeat.py`
- `weft/_constants.py` only if a named heartbeat wait cap is needed

Implementation guidance:

- Add a public `next_wait_timeout()` override on `HeartbeatTask`.
- Make `process_once()` perform one bounded turn:
  - drain at most one or a bounded batch of control/registration work
  - check service supersession
  - emit due registrations
  - check idle shutdown
  - return to the outer BaseTask wait
- Do not call a private `_wait_for_activity()` loop inside `process_once()`.
- Keep heartbeat emits on the main thread because they are broker writes.
- Preserve reserved-policy handling for invalid heartbeat requests.

TDD tests:

- Red test first: `HeartbeatTask.process_once()` returns without blocking when
  no work is due.
- Red test first: `BaseTask.run_until_stopped()` drives HeartbeatTask using
  `next_wait_timeout()` rather than a private inner loop.
- Regression: upserted heartbeat emits to the destination queue at the due
  interval.
- Regression: cancel removes registration and idle shutdown still works.
- Regression: PING/STOP while waiting is handled promptly.

What not to mock:

- Use real broker queues for destination heartbeat messages and reserved queue
  policy.

Verification:

- `./.venv/bin/python -m pytest tests/tasks/test_heartbeat.py -q`

Stop and re-plan if:

- Heartbeat scheduling needs a separate scheduler thread.
- Worker threads are introduced for heartbeat queue writes.

### Phase 6. Documentation, Cleanup, And Release Gate

Outcome: specs, lessons, and tests reflect the new runtime ownership model.
No stale draft plan should conflict with this plan.

Files to touch:

- `docs/specifications/01-Core_Components.md`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/04-SimpleBroker_Integration.md` only if
  `MultiQueueWatcher` changed
- `docs/specifications/05-Message_Flow_and_State.md` if control or spawn
  timing changed visibly
- `docs/specifications/07-System_Invariants.md`
- `docs/lessons.md` if implementation exposed a reusable correction
- `docs/plans/README.md`

Required doc updates:

- Add this plan as a related plan/backlink in specs whose implementation notes
  materially changed.
- Update implementation mappings for BaseTask, Consumer, Manager,
  TaskMonitorTask, and HeartbeatTask.
- Document the reactor-thread ownership invariant if it becomes current
  behavior.
- Mark completed phases in this plan only after the implementation and tests
  land. Do not mark this plan `completed` until all planned phases land or the
  plan is intentionally superseded.

Verification:

- `./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q`
- `./.venv/bin/ruff check weft`
- `./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox`
- `./.venv/bin/python -m pytest`
- If PG environment is available, run the nearest PG-backed manager and
  MultiQueueWatcher tests or the existing release gate command used by the
  project.

## 7. Testing Plan

### Test Strategy

Use red-green TDD for each phase where the desired behavior can be observed
without first building a large scaffold. The first failing tests should prove:

- BaseTask can wake on worker results without broker activity.
- BaseTask worker-result transport is bounded and budgeted per reactor turn.
- Consumer can answer PING while active work is running.
- Manager can answer PING/STATUS while child launch is delayed in a worker.
- Manager liveness proof does not run on every unrelated queue wake.
- TaskMonitor custom processor work can be slow without blocking PING.
- HeartbeatTask no longer blocks inside `process_once()`.

Prefer real production paths:

- `broker_env` for queue semantics and direct task objects.
- `WeftTestHarness` for CLI, manager lifecycle, and subprocess behavior.
- Real `Consumer`, `Manager`, `TaskMonitorTask`, and `HeartbeatTask` where
  practical.
- Real SimpleBroker queue reservation and timestamps.

Mocking policy:

- Do not mock `simplebroker.Queue`, reservation/move/delete semantics, manager
  lifecycle, or TaskSpec state transitions.
- Counting monkeypatches are acceptable for cadence-only assertions such as
  "this expensive proof method was not called before the evidence interval."
- Use monkeypatches for worker delay/error injection only when a real slow
  child would make the test fragile or slow. Pair such tests with at least one
  real process integration regression.

### Per-Phase Commands

Run targeted tests after each phase:

```bash
./.venv/bin/python -m pytest tests/tasks/test_multiqueue_watcher.py -q
./.venv/bin/python -m pytest tests/tasks/test_task_execution.py -q
./.venv/bin/python -m pytest tests/tasks/test_control_channel.py -q
./.venv/bin/python -m pytest tests/core/test_manager.py -q
./.venv/bin/python -m pytest tests/specs/manager_architecture/test_manager_state_events.py -q
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py -q
./.venv/bin/python -m pytest tests/commands/test_task_monitor.py -q
./.venv/bin/python -m pytest tests/tasks/test_heartbeat.py -q
```

Run broader verification before marking the plan completed:

```bash
./.venv/bin/python -m pytest
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
```

### Observable Invariants To Assert

- Reserved message remains visible in the reserved queue until the main thread
  commits success/failure policy.
- One reserved work item produces at most one execution result and one terminal
  outcome.
- Worker-thread result does not write directly to broker queues.
- PING/STATUS replies are written by the target task's main thread while
  worker work is active.
- STOP/KILL ACKs and terminal state are not duplicated.
- Manager `task_spawned` is written once per successful launch.
- Launch failure does not silently delete or lose the reserved spawn request.
- Liveness evidence has an age/TTL and stale evidence degrades decisions
  instead of forcing hot proof work.
- TaskMonitor PONG uses cached diagnostics and does not scan/delete.
- Heartbeat emits still appear on the destination queue at due time.

### Runtime Observation

After the phases land, run a real manager with ordinary spawn load and inspect:

- manager process CPU at idle
- `weft.log.tasks` for expected manager/service events
- manager PONG responsiveness while child launch or Consumer work is delayed
- absence of repeated global task-log scans in manager hot paths
- no leaked worker threads after task cleanup

The production signal this plan is meant to improve: idle or lightly active
managers should no longer spend frequent samples in service liveness proof,
global task-log replay, repeated registry scans, or repeated queue open/close
cycles merely because the manager woke.

## 8. Rollout And Slice Gates

Recommended slice order:

1. Phase 0 and Phase 1 together. This should be reviewable as shared reactor
   scaffolding with no behavior change.
2. Phase 2 alone. Consumer active control and reserved policy are risky enough
   for a separate review.
3. Phase 3 alone. Manager launch/evidence changes need their own review and
   runtime observation.
4. Phase 4 and Phase 5 separately unless they prove very small. They touch
   different task semantics.
5. Phase 6 documentation and release gate.

Each slice must:

- keep existing tests green before moving on
- include at least one real broker-backed regression
- record whether any spec update is needed before the next slice
- avoid opportunistic cleanup outside touched paths

Do not start Phase 3 until Phase 1 is reviewed and stable. Do not start Phase 5
by copying Heartbeat's current inner-loop pattern into shared code.

## 9. Fresh-Eyes Self-Review

Review pass 1 findings:

- Finding: the first draft let "worker thread owns liveness detection" sound
  like the worker might read manager registry queues directly.
  Fix: the plan now states repeatedly that the evidence worker requests
  reactor-owned snapshots/probes and never touches queues.
- Finding: the first draft treated child launch as possibly main-thread only,
  which conflicts with the desired architecture and current process-launch
  reality.
  Fix: Phase 3 now explicitly allows worker-thread `launch_task_process(...)`
  while reserving broker commits for the main thread.
- Finding: "Monitor" was ambiguous because the codebase has both simple
  monitor-style task classes and the manager-supervised `TaskMonitorTask`.
  Fix: the phased plan names `TaskMonitorTask` and calls out the file paths.
- Finding: the plan could have pushed `MultiQueueWatcher` redesign too early.
  Fix: Phase 0 makes `MultiQueueWatcher` a check gate and prohibits redesign
  unless a narrow wait-seam blocker is proven.

Review pass 2 findings:

- Finding: TaskMonitor and Heartbeat include broker-heavy work that should not
  be moved to workers just because the architecture has a worker lane.
  Fix: Phase 4 and Phase 5 now state that scans, deletes, and heartbeat emits
  stay on the main thread; only broker-free processor work can move.
- Finding: tests could drift toward mock-heavy cadence assertions.
  Fix: each phase now names what not to mock and pairs any counting monkeypatch
  with real broker/process behavior.
- Finding: the plan needed a clear stop condition if it starts moving away
  from the discussed reactor/evidence direction.
  Fix: each phase includes stop-and-re-plan gates, and the invariant section
  rejects worker broker access, second state stores, and multi-reservation
  concurrency.
- Finding: the worker-thread broker rule could be read as a restriction on
  arbitrary user function targets rather than on Weft runtime code.
  Fix: the target architecture now states that Weft does not police user
  target imports; the invariant is about Weft correctness not depending on
  worker-thread broker commits.

Residual risk:

- This is a high-stakes runtime scheduling change across multiple large
  modules. The plan is self-reviewed and implementable, but external review is
  still required before treating it as implementation-ready signoff.

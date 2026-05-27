# Service Task Worker API Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.2], [CC-2.3], [CC-2.5]; docs/specifications/03-Manager_Architecture.md [MA-0], [MA-1.2], [MA-1.6a]; docs/specifications/05-Message_Flow_and_State.md [MF-5], [MF-6]; docs/specifications/07-System_Invariants.md [OBS.13], [IMPL.8], [IMPL.9]
Superseded by: none

## 1. Goal

Add a real internal service-worker API to `ServiceTask` so long-lived
task-shaped services can register worker callables, feed them through local
Python queues, run one or more worker threads per registered worker group, and
receive typed events/results back on the owning service reactor thread. This
should consolidate the ad hoc worker patterns currently split across
`Manager`, `TaskMonitor`, and `ServiceTask` without changing durable task
execution, queue semantics, cleanup policy, or public CLI/API behavior.

This is a runtime-structure refactor. It should make the internal worker
contract clearer and more reusable, not create a public API, not redesign
TaskMonitor cleanup policy, and not introduce a second queue system. The queues
in this plan are in-memory `queue.Queue` instances inside one service process.
They are not SimpleBroker queues and must not become durable state.

## 2. Source Documents

Read these before editing:

- `AGENTS.md`: repo philosophy, house style, "queues are truth", and the
  warning not to extract abstractions unless responsibilities are actually
  separable.
- `docs/agent-context/README.md`,
  `docs/agent-context/decision-hierarchy.md`,
  `docs/agent-context/principles.md`, and
  `docs/agent-context/engineering-principles.md`: agent context, spec
  precedence, traceability, and runtime-boundary rules.
- `docs/agent-context/runbooks/runtime-and-context-patterns.md`: runtime
  boundaries, queue handle rules, and service-loop cautions.
- `docs/agent-context/runbooks/testing-patterns.md`: test harness selection
  and anti-mocking guidance.
- `docs/agent-context/runbooks/writing-plans.md` and
  `docs/agent-context/runbooks/hardening-plans.md`: this work crosses runtime
  execution boundaries, so plan hardening is mandatory.

Source specs:

- `docs/specifications/01-Core_Components.md` [CC-2.2], [CC-2.3], [CC-2.5]:
  `BaseTask` owns queue wiring, task-loop entry points, the mechanical
  worker-result queue, and bounded reactor drains; `ServiceTask` is the
  internal helper for long-lived task-shaped services and must not own generic
  service policy.
- `docs/specifications/03-Manager_Architecture.md` [MA-0], [MA-1.2],
  [MA-1.6a]: managers are `ServiceTask`s; child launch runs on a broker-free
  worker lane while the manager reactor owns durable spawn effects and service
  supervision decisions.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5], [MF-6]:
  TaskMonitor and manager worker boundaries, cached PONG behavior, retained
  task-log cleanup, runtime cleanup, and spawn reservation rules.
- `docs/specifications/07-System_Invariants.md` [OBS.13], [IMPL.8],
  [IMPL.9]: TaskMonitor cleanup safety, reactor-thread broker ownership, and
  broker-free ordinary worker lanes.

Related implementation plans:

- `docs/plans/2026-05-15-task-reactor-and-evidence-worker-plan.md`:
  completed. It established the reactor/worker split and bounded
  worker-result queue. Preserve that model.
- `docs/plans/2026-05-20-service-task-shared-reactor-extraction-plan.md`:
  completed. It introduced `ServiceTask` as a thin helper and explicitly kept
  service-specific scheduling local to `Manager`, `TaskMonitor`, and
  `HeartbeatTask`.
- `docs/plans/2026-05-23-monitor-cleanup-executor-plan.md`: completed in
  file form, but current production code does not call the executor. Treat it
  as historical context, not as an instruction to parallelize TaskMonitor
  cleanup in this plan.
- `docs/plans/2026-05-23-monitor-cleanup-policy-convergence-plan.md`:
  completed. Keep cleanup policy decisions intact.

## 3. Context And Key Files

### Current Structure

- `weft/core/tasks/base.py`
  - Owns queue resolution, control handling, state logging, process titles,
    reserved-policy helpers, the mechanical `_submit_worker_lane(...)`
    thread launcher, the broker-free `_submit_worker_call(...)` wrapper, the
    bounded `_worker_result_queue`, `_publish_worker_result(...)`,
    `_drain_worker_results(...)`, and worker shutdown in `cleanup()`.
  - `TaskWorkerResult` is a small lane/value/error envelope. It has no
    request ID, worker identity, input queue, or service-worker group state.
  - Do not move queue readiness, control handling, or lifecycle state out of
    `BaseTask`.
- `weft/core/tasks/service.py`
  - Currently tracks one `work` object per named service lane and provides
    `_start_service_lane(...)`, `_start_service_call_lane(...)`,
    `_service_lane_work(...)`, and `_pop_service_lane_work(...)`.
  - This is the right file for a higher-level internal service-worker API
    because it is already limited to long-lived task-shaped services.
  - Today it only models single-flight work. It does not model registered
    worker callables, shared in-memory input queues, multiple workers per
    group, typed events, worker snapshots, or group shutdown.
- `weft/core/manager.py`
  - `Manager(ServiceTask)` currently bypasses `ServiceTask` lane state for
    child launch and calls `_submit_worker_call(...)` directly.
  - `_ManagerChildLaunchRequest` is prepared on the manager reactor.
  - `_run_child_launch_worker(...)` calls `launch_task_process(...)` without
    touching broker queues.
  - `_handle_child_launch_result(...)` applies durable effects on the manager
    reactor: `_child_processes`, service-owner state, `task_spawned`, reserved
    message acknowledgement, and reserved-policy failure handling.
  - Manager may have multiple child launches in flight in the dictionary
    `_active_child_launches`, but it must not accept another spawn in the same
    turn while a launch is active.
- `weft/core/monitor/task_monitor.py`
  - `TaskMonitor(ServiceTask)` currently has three service-worker shapes:
    built-in monitor cycle, custom processor, and runtime cleanup.
  - Built-in cycle and runtime cleanup are declared broker/store worker
    exceptions. They may open fresh broker/store handles and return cached
    results, but the reactor must stay responsive and commit results after
    worker completion.
  - Custom processors are ordinary broker-free workers.
  - The current private work/result dataclasses are:
    `_TaskMonitorProcessorWork`, `_TaskMonitorBuiltinCycleWork`,
    `_TaskMonitorBuiltinCycleWorkerResult`, `_TaskControlCleanupWork`, and
    `_TaskControlCleanupWorkerResult`.
  - Current specs say runtime cleanup slices must stay discrete and must not
    start nested cleanup executor threads. This plan must not accidentally
    reintroduce mixed parallel cleanup.
- `weft/core/tasks/heartbeat.py`
  - `HeartbeatTask` is a long-lived service, but it currently has no separate
    service-worker lane. It still matters because the new `ServiceTask` API
    must not impose worker assumptions that break simple interval services.
- `weft/core/monitor/cleanup_executor.py`
  - Exists and is tested, but production code does not call it. Do not wire it
    as part of this plan unless the implementation stops and gets explicit
    approval for a cleanup-executor change.
- `tests/tasks/test_task_execution.py`
  - Existing `BaseTask` worker-result tests live here. Add only mechanical
    BaseTask tests here if a BaseTask helper changes.
- `tests/tasks/test_task_monitor.py`
  - Primary TaskMonitor worker and PONG tests. Use real task/monitor objects,
    real thread handoff, and bounded waits.
- `tests/core/test_manager.py`
  - Manager child-launch and service-convergence tests. Use the existing
    manager test harness patterns rather than mocking manager state.
- `tests/tasks/test_service_task.py`
  - Extend this file for the pure `ServiceTask` worker API. Use its existing
    concrete `ServiceTask` test subclass pattern and real Python
    queues/threads.
- `docs/plans/README.md`
  - Add this plan row. `tests/specs/test_plan_metadata.py` enforces metadata.

### Style And Local Rules

- Use `apply_patch` for edits.
- Keep imports grouped stdlib, third-party, local. Use `collections.abc` for
  `Callable`, `Mapping`, `Sequence`, and `Iterator`.
- Use `from __future__ import annotations` in new Python files.
- Use modern type hints: `X | None`, `dict[str, Any]`, `tuple[...]`.
- Constants belong in `weft/_constants.py`.
- Do not add dependencies.
- Do not introduce `asyncio`, `ThreadPoolExecutor`, a plugin registry, a
  scheduler DSL, or a public service-worker API. This is an internal helper
  over existing `threading.Thread` and `queue.Queue`.
- Keep comments short and focused on concurrency ownership. Avoid generic
  comments that restate the code.
- DRY: reuse `BaseTask._publish_worker_result(...)`,
  `_drain_worker_results(...)`, `_worker_threads`, and `_stop_worker_lanes()`.
  Do not create a second result-drain loop in `ServiceTask`.
- YAGNI: support the worker shapes needed by Manager and TaskMonitor now:
  registered callable, positional/keyword args, shared in-memory input queue,
  bounded result/event publication, one or more threads per worker group,
  group stop, and reactor-side event handling. Do not add priorities,
  persistence, retries, cron, dynamic scaling, or per-item acknowledgement.

### Comprehension Checks Before Editing

Do not edit until you can answer these:

1. Which method guarantees worker results are applied on the main task reactor
   thread?
2. Why does `_submit_worker_call(...)` say ordinary worker callables must not
   use SimpleBroker queues?
3. Which TaskMonitor worker lanes are the broker/store exceptions, and why are
   they exceptions?
4. Which method in Manager commits `task_spawned` and reserved-message
   acknowledgement after a child launch worker returns?
5. Why would a generic `ServiceTask.process_once()` template be dangerous?
6. Which current PONG fields prove TaskMonitor work is in flight without live
   queue scans?

## 4. Invariants And Constraints

Preserve these invariants:

- The durable execution spine remains
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log`.
- TIDs remain immutable and still come from spawn-request message IDs for
  manager-launched work.
- `TaskSpec.spec` and `TaskSpec.io` remain immutable after resolved TaskSpec
  creation.
- State transitions stay forward-only. Worker API refactoring must not emit
  new lifecycle events, reorder existing lifecycle events, or publish task-log
  activity rows for long-lived service activity.
- Queue names, queue roles, SimpleBroker message payloads, CLI output, and
  public result shapes do not change.
- `BaseTask` remains the owner of task-local control handling, state
  publication, process-title updates, worker-result wakeups, bounded worker
  result drain, and worker shutdown.
- `MultiQueueWatcher` remains the owner of SimpleBroker queue readiness.
  `ServiceTask` must not implement queue polling or a competing wait loop.
- `ServiceTask` must not know about manager leadership, service keys,
  autostart, cleanup selection, heartbeat registration, task-log retention, or
  monitor-store policy.
- Ordinary service workers remain broker-free. They may run blocking local
  work and return local Python values/events, but they must not touch
  SimpleBroker queues, mutate `TaskSpec.state`, report lifecycle rows, or send
  control responses.
- The declared TaskMonitor built-in cycle and runtime cleanup lanes remain the
  only Weft-owned broker/store worker exceptions. Do not encode this as a
  generic worker-spec permission flag. Keep the exception explicit in
  TaskMonitor code and in the governing specs.
- TaskMonitor runtime cleanup slices remain discrete. Do not mix terminal,
  reserved, and dead-TID cleanup in one worker result, and do not start nested
  cleanup executor threads.
- PONG/STATUS paths remain cached and lightweight. The new worker snapshot may
  report local in-memory worker state, but PONG must not scan broker queues,
  open MonitorStore, or wait on worker threads.
- Worker result/event queues are bounded. Full queues should apply
  backpressure to worker threads rather than growing memory.
- Cleanup must stop workers best-effort through the existing task cleanup path.
  No worker thread should be created outside the task's `_worker_threads`
  tracking.
- In-memory service-worker queues are process-local only. They must not be
  used for recovery, cross-process communication, operator-visible state, or
  durable work ownership.

Hidden couplings:

- Manager child launch has two phases. The worker starts the child process;
  the manager reactor commits durable spawn effects. Do not collapse these.
- TaskMonitor worker results populate cached diagnostics. Control remains
  responsive while workers run.
- Existing `ServiceTask._service_lane_work_items` is single-flight. Replacing
  it with worker groups must preserve single-flight behavior for existing
  TaskMonitor lanes unless the caller explicitly starts a multi-worker group.
- `BaseTask._submit_worker_lane(...)` currently publishes one terminal
  `TaskWorkerResult` per thread. The new API should wrap terminal results in
  typed service-worker events rather than forcing every subclass to validate
  ad hoc value types.

Fatal versus best-effort failures:

- Fatal: worker result applied off the reactor thread, duplicated service
  worker group names without an explicit restart, lost manager child-launch
  result, wrong reserved-message acknowledgement, wrong lifecycle event order,
  unbounded result queue growth, or worker threads left outside cleanup
  tracking.
- Best-effort: optional worker snapshot diagnostics, service-worker stop during
  shutdown after the service is already stopping, and auxiliary progress
  events that fail after `_worker_stopping` is set.

Rollback:

- The change is internal. Rollback should be a normal code revert.
- Keep existing private wrappers during adoption:
  `_start_service_lane(...)`, `_start_service_call_lane(...)`,
  `_service_lane_work(...)`, and `_pop_service_lane_work(...)` should remain
  as compatibility wrappers over the new API until Manager and TaskMonitor are
  migrated and tests prove no callers remain.
- Do not delete old private dataclasses in the same slice that introduces the
  API. Remove only after each adopting service has been converted and tested.

Stop and re-plan if:

- The implementation wants to change public CLI output, queue names, TaskSpec
  schema, service registry schema, or PONG payload shape beyond adding cached
  in-memory worker diagnostics.
- The implementation starts moving manager service convergence decisions into
  `ServiceTask`.
- TaskMonitor cleanup policy changes are needed to make the worker API fit.
- Tests require mocking SimpleBroker queues, manager lifecycle, or TaskMonitor
  cleanup to pass.
- The implementation creates a second worker-result drain path instead of
  using `BaseTask._worker_result_queue`.
- The API starts resembling a general job system with persistence, retries,
  priorities, or scheduling.

## 5. Target API Shape

The exact names may be adjusted during implementation, but the API should have
this shape and no more.

Add internal dataclasses/protocols in `weft/core/tasks/service.py`:

```python
class ServiceWorkerTarget(Protocol):
    def __call__(
        self,
        context: ServiceWorkerContext,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        ...
```

The target contract is:

```python
def target(context: ServiceWorkerContext, *args: Any, **kwargs: Any) -> Any:
    ...
```

Core types:

```python
@dataclass(frozen=True, slots=True)
class ServiceWorkerSpec:
    name: str
    target: ServiceWorkerTarget
    args: tuple[Any, ...] = ()
    kwargs: Mapping[str, Any] | None = None
    worker_count: int = 1
    input_queue_maxsize: int = 0
```

The spec intentionally does not include a generic "broker effects allowed"
flag. Ordinary service workers are broker-free by default. TaskMonitor's
built-in cycle and runtime cleanup lanes remain explicit spec-backed
exceptions at their call sites.

```python
@dataclass(frozen=True, slots=True)
class ServiceWorkerEvent:
    name: str
    request_id: str
    worker_index: int | None
    kind: str
    value: Any = None
    error: BaseException | None = None
    item_id: str | None = None
```

Events should use simple string `kind` values such as `"started"`,
`"progress"`, `"result"`, `"error"`, and `"stopped"`. Do not create an enum
unless tests show repeated typos.

```python
@dataclass(slots=True)
class ServiceWorkerHandle:
    name: str
    request_id: str
    input_queue: queue.Queue[Any]
    stop_event: threading.Event
    worker_count: int
```

Internal mutable state can be a private dataclass such as
`_ServiceWorkerRegistration`. It should hold the immutable
`ServiceWorkerSpec`, the shared `queue.Queue`, the shared `threading.Event`,
the active `ServiceWorkerHandle | None`, and cached counters used by
`_service_worker_snapshot(...)`. Do not expose that registration object outside
`ServiceTask`.

`request_id` identifies the active worker-group run. It is not necessarily the
ID of a queued work item. If a worker group consumes many items, each item must
carry its own identity in the item payload or in `ServiceWorkerEvent.item_id`.
For example, Manager child-launch result routing should key off the child TID
from `_ManagerChildLaunchRequest` or `item_id`, not the long-lived group
`request_id`.

The handle may expose read-only snapshot properties/methods such as
`active`, `threads_started`, `threads_completed`, and `queued_items`, but it
must not expose raw thread mutation.

```python
@dataclass(frozen=True, slots=True)
class ServiceWorkerContext:
    name: str
    request_id: str
    worker_index: int
    input_queue: queue.Queue[Any]
    stop_event: threading.Event
    publish: Callable[[ServiceWorkerEvent], bool]
```

The context should provide convenience methods:

- `iter_items()` yields input items until the group is stopping or it receives
  the internal sentinel.
- `publish_event(kind: str, value: Any = None, *, item_id: str | None = None)
  -> bool` publishes through `BaseTask._publish_worker_result(...)` so the
  reactor wakes correctly.
- `stop_requested() -> bool` checks the worker group and task stop events.

Do not expose a raw outbound queue as the primary publication API. A plain
`queue.Queue.put(...)` cannot wake the task reactor by itself. The outbound
path must go through `BaseTask._publish_worker_result(...)`. The inbound
queue is the associated Python queue that multiple workers pull from.

Add `ServiceTask` methods:

```python
def _register_service_worker(self, spec: ServiceWorkerSpec) -> None:
    """Register a service worker group without starting threads."""
```

Registration stores the worker definition and allocates the shared input queue
and stop event. It does not create an active run ID. `_start_service_worker`
creates and returns the `ServiceWorkerHandle` for the active run.

```python
def _start_service_worker(
    self,
    name: str,
    *,
    request_id: str | None = None,
    initial_items: Iterable[Any] = (),
) -> ServiceWorkerHandle:
    """Start all threads for a registered worker group."""
```

```python
def _enqueue_service_work(
    self,
    name: str,
    item: Any,
    *,
    block: bool = False,
    timeout: float | None = None,
) -> bool:
    """Put one item on a worker group's input queue."""
```

```python
def _stop_service_worker(self, name: str, *, drain: bool = False) -> None:
    """Request stop and enqueue sentinels for one worker group."""
```

```python
def _service_worker_snapshot(self, name: str | None = None) -> Mapping[str, Any]:
    """Return cached in-memory worker state for PONG/debug surfaces."""
```

```python
def _handle_service_worker_event(self, event: ServiceWorkerEvent) -> None:
    """Subclass hook for typed service worker events."""
```

`ServiceTask._handle_worker_result(...)` should unwrap service-worker events
from `TaskWorkerResult` and route them to `_handle_service_worker_event(...)`.
Unknown non-service-worker results should still defer to `BaseTask`.

Compatibility wrappers:

- `_start_service_lane(...)` should call the new API with a transient one-shot
  worker group and `worker_count=1`. The transient target may ignore the
  input queue and call the submitted function once.
- `_start_service_call_lane(...)` should do the same and preserve the current
  ordinary worker-call contract.
- `_service_lane_work(...)` and `_pop_service_lane_work(...)` should remain
  until all current call sites are migrated. Their implementation may use
  handle metadata.

Thread behavior:

- `ServiceTask` should still use `BaseTask._submit_worker_lane(...)` to start
  worker threads where practical. If the implementation needs a lower-level
  helper to attach worker index/request ID, add a small protected helper to
  `BaseTask` rather than duplicating thread tracking and cleanup.
- Each worker thread creates a `ServiceWorkerContext`, publishes `"started"`,
  runs the registered target, publishes `"result"` on return, publishes
  `"error"` on uncaught exception, and publishes `"stopped"` in a `finally`
  block.
- On uncaught exception, set the group stop event so sibling workers wind down.
  Item-level recoverable errors should be handled inside the target and
  published as ordinary progress/result events.

## 6. Bite-Sized Implementation Tasks

### Task 1: Add Focused ServiceTask Worker Tests First

Files to modify:

- `tests/tasks/test_service_task.py`

Files to read first:

- `weft/core/tasks/service.py`
- `weft/core/tasks/base.py`
- `tests/tasks/test_task_execution.py`
- `docs/agent-context/runbooks/testing-patterns.md`

Red test goals:

1. A concrete test-only `ServiceTask` subclass can register a worker target,
   start it with one input item, drain worker results on the reactor thread,
   and observe a typed `ServiceWorkerEvent`.
2. Two different worker groups can run at the same time without blocking each
   other.
3. One worker group with `worker_count=2` can process multiple items from the
   same in-memory input queue. The test should assert that both worker indexes
   processed at least one item. Use a small number of items and bounded waits.
4. A target exception publishes an error event and clears or stops the worker
   group without leaking active state.
5. Published worker events are invisible to subclass handlers until
   `_drain_worker_results()` runs on the reactor thread. This proves the API
   uses the existing BaseTask result queue instead of a side-channel queue.

Test design requirements:

- Do not mock `queue.Queue`, `threading.Thread`, or BaseTask worker result
  drains.
- Use a real concrete `ServiceTask` test subclass with a minimal resolved
  `TaskSpec`. Reuse existing test helpers for TaskSpec construction if they
  exist; otherwise define the smallest local helper in the test file.
- Do not use sleeps as correctness. Use bounded polling around
  `_drain_worker_results()` and a deadline.
- Do not open SimpleBroker queues in these tests. The service-worker API is
  in-memory.

Stop gate:

- If the test needs a full Manager or TaskMonitor instance just to prove the
  generic API, the API is too coupled. Stop and simplify the design.

### Task 2: Implement The Minimal Generic API In ServiceTask

Files to modify:

- `weft/core/tasks/service.py`
- `weft/core/tasks/base.py` only if a small thread-start helper is needed
- `weft/_constants.py` only if a new queue-size or shutdown constant is needed

Files to avoid:

- `weft/core/manager.py`
- `weft/core/monitor/task_monitor.py`

Implementation steps:

1. Add the dataclasses/protocols described in Section 5.
2. Add `_service_worker_specs: dict[str, ServiceWorkerSpec]` and
   `_service_workers: dict[str, ServiceWorkerHandle]`, or preferably one
   internal private state object per name in `ServiceTask.__init__`. If the
   split dictionaries get awkward, use the state object. Do not make callers
   assemble worker queues or stop events themselves.
3. Validate worker specs:
   - name must be non-empty
   - target must be callable
   - `worker_count >= 1`
   - `input_queue_maxsize >= 0`
   - start rejects an already active group unless the method deliberately
     returns the existing active handle without starting new threads
   - duplicate live names are rejected unless the previous group is fully
     stopped and removed
4. Implement registration, start, enqueue, stop, snapshot, and event hook.
5. Make worker thread target publish typed `ServiceWorkerEvent`s through
   `_publish_worker_result(...)`.
6. Implement a private sentinel object for worker shutdown. Do not expose it
   to callers.
7. Make `ServiceTask.cleanup()` stop registered service workers before calling
   `super().cleanup()`, or rely on `BaseTask.cleanup()` only if tests prove all
   service-worker threads are tracked in `_worker_threads` and receive stop
   sentinels.
8. Preserve existing methods `_start_service_lane(...)`,
   `_start_service_call_lane(...)`, `_service_lane_work(...)`, and
   `_pop_service_lane_work(...)` as compatibility wrappers.

DRY/YAGNI requirements:

- Reuse `BaseTask._publish_worker_result(...)` for every outbound event.
- Reuse `BaseTask._drain_worker_results(...)`; do not add another drain loop.
- Do not add public exports from `weft/core/tasks/__init__.py` unless tests
  require it. Prefer importing internal helpers directly from
  `weft.core.tasks.service` in tests.
- Do not add a retry policy, priority queue, cron scheduler, persistence, or
  dynamic worker scaling.

Verification:

```bash
./.venv/bin/python -m pytest tests/tasks/test_service_task.py -q
./.venv/bin/python -m pytest tests/tasks/test_task_execution.py -q
./.venv/bin/ruff check weft/core/tasks/service.py tests/tasks/test_service_task.py
```

### Task 3: Convert Manager Child Launch To The Service Worker API

Files to modify:

- `weft/core/manager.py`
- `tests/core/test_manager.py`

Files to read first:

- `weft/core/manager.py` around `_launch_child_task`,
  `_run_child_launch_worker`, `_handle_child_launch_result`, and
  `_drain_active_child_launches_for_cleanup`
- `docs/specifications/03-Manager_Architecture.md` [MA-1.2], [MA-1.6a]
- `docs/specifications/05-Message_Flow_and_State.md` [MF-6]

Red test goals:

1. Manager child launch still reserves the spawn message before worker
   submission and deletes the exact reserved message only after successful
   launch commit.
2. A child-launch worker exception still applies the configured reserved
   policy on the manager reactor thread.
3. While a child launch is in flight, manager PING/control remains responsive.
4. Multiple child launch requests can be tracked without result confusion:
   the returned event `item_id` or result payload must match the original
   child TID/request.

Implementation steps:

1. Register a manager child-launch service worker group in `Manager.__init__`
   or lazily before first use. Use `worker_count=1` at first unless an
   existing test proves current Manager intentionally launches multiple child
   processes concurrently.
2. The worker target should consume `_ManagerChildLaunchRequest` items from
   its input queue and publish `_ManagerChildLaunchResult` as a typed
   `ServiceWorkerEvent(kind="result", item_id=<child tid>, ...)`.
3. Replace the direct `_submit_worker_call(...)` call in `_launch_child_task`
   with `_enqueue_service_work(...)` plus starting the registered worker if
   needed.
4. Keep `_active_child_launches` as manager-owned reactor state. It remains
   the authoritative pending launch map for cleanup and control decisions.
5. In `_handle_service_worker_event(...)` or the manager's worker-result
   handler, route child-launch result events to `_handle_child_launch_result`.
6. Keep all broker effects in `_handle_child_launch_result` and related
   reactor methods. The worker target must only call `launch_task_process(...)`
   and return local Python results.

Stop gate:

- If converting Manager requires moving service convergence decisions,
  duplicate-kill policy, reserved-message deletion, or task-log publication
  into the worker target, stop. The API is being misused.

Verification:

```bash
./.venv/bin/python -m pytest tests/core/test_manager.py -q -k "child_launch or spawn or service"
./.venv/bin/python -m pytest tests/commands/test_run.py -q -k "manager or spawn"
```

### Task 4: Convert TaskMonitor Worker Lanes Without Changing Cleanup Semantics

Files to modify:

- `weft/core/monitor/task_monitor.py`
- `tests/tasks/test_task_monitor.py`

Files to read first:

- `weft/core/monitor/task_monitor.py` around worker dataclasses,
  `_maybe_start_builtin_cycle_worker`,
  `_maybe_start_runtime_cleanup_worker`,
  `_process_monitor_candidates`,
  `_handle_builtin_cycle_worker_result`,
  `_handle_control_cleanup_worker_result`, and `_handle_worker_result`
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]
- `docs/specifications/07-System_Invariants.md` [OBS.13], [IMPL.8],
  [IMPL.9]

Red test goals:

1. Built-in cycle still runs as a single in-flight worker group and PONG shows
   `builtin_cycle_in_flight` while it is active.
2. Custom processor still runs broker-free from a candidate snapshot and
   commits its result/checkpoint on the reactor.
3. Runtime cleanup still runs as discrete one-class slices:
   terminal-control, then reserved, then dead-TID as applicable. It must not
   mix cleanup classes in one result.
4. Runtime cleanup worker count remains effectively one for these slices.
   This is required by current specs.
5. PING/STATUS remains responsive while any monitor worker group is active.
6. Invalid worker results are still converted into cached monitor errors
   rather than crashing the monitor reactor.

Implementation steps:

1. Register three TaskMonitor worker groups:
   - `TASK_MONITOR_BUILTIN_CYCLE_WORKER_LANE`
   - `TASK_MONITOR_PROCESSOR_WORKER_LANE`
   - `TASK_MONITOR_CONTROL_CLEANUP_WORKER_LANE`
2. Use `worker_count=1` for all three in this slice.
3. Keep the built-in cycle and runtime cleanup broker/store exception visible
   in TaskMonitor comments/docstrings near the registered targets. Do not add
   a generic permission flag to `ServiceWorkerSpec`.
4. Convert `_builtin_cycle_work_in_flight`, `_processor_work_in_flight`, and
   `_control_cleanup_work_in_flight` to read service-worker snapshots/handles
   or compatibility lane state.
5. Keep the existing domain result dataclasses initially. Wrap them in
   `ServiceWorkerEvent.value` rather than rewriting all monitor result
   handling at once.
6. Route service-worker events to the existing handler bodies. The commit code
   that updates cached PONG fields should stay visibly in TaskMonitor.
7. Do not wire `weft/core/monitor/cleanup_executor.py` here. That is a
   separate cleanup execution policy decision and conflicts with the current
   discrete-slice spec.

Stop gate:

- If the implementation starts changing which cleanup policy runs, how rows
  are selected, or whether terminal/reserved/dead-TID cleanup can run in the
  same result, stop and split a new policy/spec plan.

Verification:

```bash
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py -q -k "worker or pong or cleanup"
./.venv/bin/python -m pytest tests/core/test_task_monitor_cleanup.py -q
./.venv/bin/python -m pytest tests/core/monitor/test_progress.py -q
```

### Task 5: Remove Stale Service Lane Bookkeeping After Adoption

Files to modify:

- `weft/core/tasks/service.py`
- `weft/core/manager.py`
- `weft/core/monitor/task_monitor.py`
- tests touched in Tasks 1-4

Implementation steps:

1. Search for `_start_service_lane`, `_start_service_call_lane`,
   `_service_lane_work`, and `_pop_service_lane_work`.
2. If any current production call site remains, keep compatibility wrappers.
3. If no production call sites remain, decide whether to keep wrappers for
   readability or remove them. Prefer keeping thin wrappers if tests or future
   local code become clearer, but remove duplicate state.
4. Delete obsolete single-flight dictionaries only after the new worker group
   registry covers all previous behavior.
5. Keep method names private. Do not add public exports.

Verification:

```bash
rg "_start_service_lane|_start_service_call_lane|_service_lane_work|_pop_service_lane_work" weft tests
./.venv/bin/python -m pytest tests/tasks/test_service_task.py tests/core/test_manager.py tests/tasks/test_task_monitor.py -q
```

### Task 6: Update Specs And Local Documentation

Files to modify:

- `docs/specifications/01-Core_Components.md`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/05-Message_Flow_and_State.md` only if wording around
  TaskMonitor worker slices needs to mention the shared `ServiceTask` worker
  API
- `docs/plans/README.md`
- `docs/lessons.md` only if implementation confirms a repeated mistake worth
  preserving

Required doc updates:

1. In `01-Core_Components.md` [CC-2.3], update `ServiceTask` implementation
   mapping to say it owns registered in-memory service-worker groups and
   shared input queues, while still not owning service policy or queue
   readiness.
2. In `03-Manager_Architecture.md` [MA-1.2], update manager child-launch
   mapping if method names change.
3. In `07-System_Invariants.md` [IMPL.8], [IMPL.9], preserve the broker-free
   ordinary worker rule and mention that the service-worker API is in-memory
   and reactor-committed.
4. If TaskMonitor method names change, update [MF-5] implementation mapping
   without changing cleanup policy text.

Verification:

```bash
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
./.venv/bin/ruff check weft
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
```

## 7. Testing Strategy

Use red-green TDD where practical:

- Write or extend `tests/tasks/test_service_task.py` before implementing the
  generic API.
- For Manager and TaskMonitor adoption, add or adjust one failing test per
  behavior before changing production code.
- Do not batch all conversions before running tests. Convert one service
  worker lane at a time.

What not to mock:

- Do not mock `queue.Queue` or `threading.Thread` for the generic API tests.
- Do not mock `BaseTask._worker_result_queue`; the result queue is the core
  contract.
- Do not mock SimpleBroker queues in Manager or TaskMonitor integration tests.
  Use existing real broker-backed harnesses and helpers.
- Do not mock manager reserved-message handling. Assert queue-visible effects
  or existing manager state/event surfaces.
- Do not mock TaskMonitor cleanup policy selectors when testing adoption.
  Use existing focused tests that create real monitor/store state.

Observable success signals:

- ServiceTask tests show multiple worker groups and multiple workers per group
  using a shared Python input queue.
- Manager tests show child-launch behavior unchanged: reserved work is
  acknowledged only after launch commit, and failure uses reserved policy.
- TaskMonitor tests show PONG in-flight diagnostics while workers run and
  unchanged cleanup policy progress after worker completion.
- No PONG/status path performs live broker/MonitorStore scans.
- Full targeted suites pass without new sleeps or broad mocks.

Final verification for the implementing branch:

```bash
./.venv/bin/python -m pytest tests/tasks/test_service_task.py -q
./.venv/bin/python -m pytest tests/tasks/test_task_execution.py -q
./.venv/bin/python -m pytest tests/core/test_manager.py -q -k "child_launch or spawn or service"
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py -q -k "worker or pong or cleanup"
./.venv/bin/python -m pytest tests/core/test_task_monitor_cleanup.py -q
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
./.venv/bin/ruff check weft tests/tasks/test_service_task.py
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
```

If the targeted suites pass but full `pytest` fails, do not hand-wave the
failure as unrelated until checking whether worker cleanup leaked a thread,
event, or queue state into another test.

## 8. Rollout And Ops Notes

This is an internal refactor. There is no ops rollout order beyond normal
release sequencing.

Expected runtime behavior after rollout:

- Manager PING remains responsive during child launch.
- TaskMonitor PING remains responsive during built-in cycle and runtime
  cleanup.
- PONG may expose the same in-flight fields it exposes today. Adding a small
  cached worker snapshot under existing extended diagnostics is acceptable only
  if tests assert it is cached and in-memory.
- No SimpleBroker queue names, retention behavior, monitor policies, or
  service registry payloads change.

Rollback:

- Revert the branch. Because no persisted schema or public queue payload
  changes are allowed, rollback should not require data migration.
- If a production issue appears only in TaskMonitor adoption, revert the
  TaskMonitor conversion first while keeping the generic `ServiceTask` API and
  Manager conversion if those are stable. This is why the plan requires
  bite-sized conversions.

## 9. Out Of Scope

- Public API or CLI changes.
- TaskSpec schema changes.
- SimpleBroker queue semantics.
- Manager leadership or service-convergence redesign.
- TaskMonitor cleanup policy convergence.
- Wiring `weft/core/monitor/cleanup_executor.py`.
- Parallel TaskMonitor runtime cleanup.
- Durable persistence for service-worker queues.
- Worker retries, priorities, scheduling, delayed jobs, or dynamic scaling.
- Rewriting `Consumer` to use `ServiceTask`. `Consumer` is not a long-lived
  service and should stay on `BaseTask`.

## 10. Fresh-Eyes Self-Review

Review pass completed after drafting.

Findings and fixes applied:

- **Potential scope drift**: The first draft risked turning this into a
  TaskMonitor cleanup parallelism plan. Fixed by stating that runtime cleanup
  remains `worker_count=1`, discrete by cleanup class, and must not use
  `cleanup_executor.py`.
- **Outbound queue ambiguity**: A raw outbound `queue.Queue` would not wake the
  task reactor. Fixed by making the inbound queue the shared Python queue and
  requiring outbound events to publish through `BaseTask._publish_worker_result`.
- **Manager authority ambiguity**: The plan now explicitly keeps reserved
  message acknowledgement, service-owner state, and task-log publication on
  the manager reactor.
- **Over-mocking risk**: Each task now names what not to mock and which real
  harness/path to use.
- **YAGNI risk**: The plan explicitly excludes retries, priorities,
  persistence, scheduling, dynamic scaling, and public exports.
- **Spec conflict**: Current specs limit TaskMonitor runtime cleanup worker
  behavior. The plan calls this out and includes a stop gate before any cleanup
  policy or parallel cleanup change.
- **Registration/start ambiguity**: A registered worker definition is not the
  same thing as an active run. Fixed by making registration allocate private
  group state and making `_start_service_worker(...)` create the active handle.
- **Per-item identity ambiguity**: A long-lived worker group request ID cannot
  safely identify each queued work item. Fixed by adding `item_id` to
  `ServiceWorkerEvent` and requiring Manager child-launch routing to use the
  child TID from the payload or `item_id`.
- **Test path error**: The first draft named a non-existent
  `tests/core/tasks/test_service_task.py` path. Fixed to extend the existing
  `tests/tasks/test_service_task.py`.

Residual risk:

- The exact type shape may need small adjustments during implementation
  because Python typing for callable varargs and bound methods can get noisy.
  That should not change the behavioral contract: registered target, args,
  kwargs, shared inbound queue, typed outbound events via the BaseTask result
  queue, and reactor-thread commit.

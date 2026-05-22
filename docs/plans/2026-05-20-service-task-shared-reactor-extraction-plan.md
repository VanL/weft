# Service Task Shared Reactor Extraction Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.1], [CC-2.2], [CC-2.3], [CC-2.4], [CC-2.5]; docs/specifications/03-Manager_Architecture.md [MA-0], [MA-1], [MA-3]; docs/specifications/05-Message_Flow_and_State.md [MF-2], [MF-3], [MF-5]; docs/specifications/07-System_Invariants.md [IMPL.8], [IMPL.9], [MANAGER.1]-[MANAGER.17], [OBS.13]
Superseded by: none

## 1. Goal

Extract the shared reactor/worker shape from `Manager` and `TaskMonitor`
without creating a generic service framework that hides domain ordering. The
target is two slices: first, move neutral worker-lane mechanics into
`BaseTask`; second, add a thin `ServiceTask(BaseTask)` layer for long-lived
service helpers such as activation, single-flight lane tracking, in-flight
status, and due-time math. `Manager`, `TaskMonitor`, and `HeartbeatTask`
should share the reusable mechanics while keeping their own explicit
`process_once()` ordering.

This is a refactor. It should not change queue names, TaskSpec schema, CLI
output, manager election behavior, cleanup semantics, result delivery, or
public lifecycle reconstruction.

## 2. Source Documents

Read these before editing:

- `AGENTS.md`: repo philosophy, house style, test commands, and the warning
  against extraction unless responsibilities are actually separable.
- `docs/agent-context/decision-hierarchy.md`: specs outrank plans; preserve
  dirty-tree discipline.
- `docs/agent-context/principles.md`: queue truth, verification, and
  traceability rules.
- `docs/agent-context/engineering-principles.md`: durable spine, real broker
  tests, and runtime-boundary rules.
- `docs/agent-context/runbooks/runtime-and-context-patterns.md`: queue handle
  reuse, runtime-only queues, generator history reads, and spawn boundaries.
- `docs/agent-context/runbooks/testing-patterns.md`: use `broker_env` or
  `WeftTestHarness` for queue/process behavior; do not mock SimpleBroker
  semantics.
- `docs/agent-context/runbooks/writing-plans.md`,
  `docs/agent-context/runbooks/hardening-plans.md`, and
  `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`: this plan
  changes shared runtime structure, so hardening and review are required.

Source specs:

- `docs/specifications/01-Core_Components.md` [CC-2.1], [CC-2.2], [CC-2.3],
  [CC-2.4], [CC-2.5]: `MultiQueueWatcher`, `BaseTask`, specialized task
  types, control behavior, and the shared task-loop contract.
- `docs/specifications/03-Manager_Architecture.md` [MA-0], [MA-1], [MA-3]:
  managers are task-shaped services; manager spawn, child launch, registry,
  control, and bootstrap behavior must stay stable.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-2], [MF-3], [MF-5]:
  reservation, control, task-log, and monitor operational evidence flows.
- `docs/specifications/07-System_Invariants.md` [IMPL.8], [IMPL.9],
  [MANAGER.1]-[MANAGER.17], [OBS.13]: reactor-thread broker ownership,
  worker-lane restrictions, manager invariants, and TaskMonitor operational
  boundaries.

Related implementation plans:

- `docs/plans/2026-05-15-task-reactor-and-evidence-worker-plan.md`:
  completed reactor migration. This plan should preserve its model, not
  restart that work.
- `docs/plans/2026-05-20-monitor-reactor-worker-refactor-plan.md`:
  completed TaskMonitor worker-lane refactor. This plan should extract the
  stable mechanics introduced there, not change monitor cleanup semantics.
- `docs/plans/2026-05-18-reactive-task-loop-hot-probe-plan.md`: completed
  wait-contract fix. Preserve the distinction between due local timers and
  queue discovery.
- `docs/plans/2026-05-10-control-and-service-convergence-state-machine-plan.md`
  and `docs/plans/2026-05-10-manager-service-authority-boundary-hardening-plan.md`:
  completed manager service correctness work. Do not move manager service
  decisions into a shared service framework.

## 3. Context And Key Files

### Current Structure

- `weft/core/tasks/base.py`
  - Owns queue resolution, control handling, lifecycle event publication,
    process titles, reserved-policy helpers, queue caches, worker-result
    queue, bounded worker result drain, `run_until_stopped()`,
    `next_wait_timeout()`, and queue/worker wake integration.
  - `_submit_worker_call(...)` currently has a broker-free contract. Keep that
    contract.
- `weft/core/tasks/multiqueue_watcher.py`
  - Owns queue modes, active queue scheduling, priority drain, and the
    backend-neutral activity waiter. Do not duplicate this logic in
    `ServiceTask`.
- `weft/core/manager.py`
  - Is a long-lived task-shaped dispatcher. It has domain-critical turn order:
    worker results, serve logs, termination signals, registry refresh,
    leadership yield, control, child cleanup, spawn queue drain, service
    convergence, idle shutdown.
  - Child process launch already runs in a broker-free worker lane; durable
    spawn effects are committed on the manager reactor.
- `weft/core/monitor/task_monitor.py`
  - Is both the foreground monitor command implementation and the
    manager-supervised internal monitor service.
  - The persistent service path has three in-flight worker categories today:
    built-in monitor cycle, custom processor, and terminal control/runtime
    cleanup.
  - Built-in cycle and runtime cleanup are declared TaskMonitor exceptions to
    the generic broker-free worker rule. They may open fresh broker/store
    handles and return cached results to the reactor.
- `weft/core/tasks/heartbeat.py`
  - Is a long-lived internal interval service. It should become a
    `ServiceTask` adopter after the shared class exists.
  - Its startup lifecycle matches TaskMonitor's event sequence, but unlike
    Manager it does not currently set a temporary `"spawning"` process title.
- `tests/tasks/test_task_execution.py`
  - Contains `ReactorTestTask` and existing worker-result tests for
    `BaseTask`. Extend these for Slice 1.
- `tests/tasks/test_task_monitor.py`
  - Contains TaskMonitor service, PING, worker, and scheduling tests. Update
    tests here for TaskMonitor adoption.
- `tests/core/test_manager.py`
  - Contains manager child-launch, spawn, service, leadership, and idle tests.
    Add narrow regressions here when Manager adopts `ServiceTask`.
- `tests/tasks/test_heartbeat.py` if present, otherwise add focused tests near
  the existing heartbeat tests for service activation and wait behavior.
- `docs/specifications/01-Core_Components.md` and
  `docs/specifications/07-System_Invariants.md`
  - Must be updated when the shared ownership model changes.
- `docs/plans/README.md`
  - Must include this plan row. The metadata test enforces it.

### Style Rules

- Use `apply_patch` for edits.
- Keep imports at the top, grouped stdlib, third-party, local.
- Use `from __future__ import annotations` in new Python modules.
- Use modern type hints: `X | None`, `dict[str, Any]`, `tuple[...]`.
- Use `collections.abc` for abstract types such as `Callable` and `Mapping`.
- Constants live in `weft/_constants.py`.
- Do not add dependencies.
- Do not add a public API unless the specs require it. `ServiceTask` is an
  internal task runtime helper.
- Keep comments short and only for concurrency or ownership rules that are not
  obvious from the code.
- DRY means extracting repeated mechanics that already exist in two or more
  places. It does not mean inventing a generalized service platform.
- YAGNI applies aggressively: no `asyncio`, no plugin system, no task scheduler
  abstraction, no event bus, no lifecycle DSL.

### Comprehension Checks Before Editing

Do not edit until you can answer these:

1. Which method in `BaseTask` currently guarantees worker results are applied
   on the main task reactor thread?
2. Why does `_submit_worker_call(...)` say worker callables must not use
   SimpleBroker queues?
3. Which TaskMonitor worker lanes are explicit exceptions to the broker-free
   worker rule, and where is that exception documented?
4. In Manager, which operation authorizes public spawn dispatch: registry
   leadership or atomic reservation of a spawn request?
5. Why would a shared `ServiceTask.process_once()` template be dangerous for
   Manager?
6. Which tests currently assert private TaskMonitor in-flight fields, and
   which observable PONG fields should survive the refactor?

## 4. Invariants And Constraints

Preserve these invariants:

- The durable execution spine remains
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log`.
- TIDs remain immutable and still come from spawn-request message IDs for
  manager-launched children.
- `TaskSpec.spec` and `TaskSpec.io` remain immutable after resolved TaskSpec
  creation.
- State transitions remain forward-only. Refactoring shared classes must not
  synthesize or reorder lifecycle events.
- Queue names and queue roles do not change.
- Reservation semantics do not change. Reserved spawn work remains visible
  until the owning reactor commits success or applies the configured policy.
- `BaseTask._submit_worker_call(...)` remains the broker-free worker API.
- Ordinary worker lanes must not touch SimpleBroker queues, mutate
  `TaskSpec.state`, or publish lifecycle evidence directly.
- TaskMonitor built-in cycle and runtime cleanup remain the only Weft-owned
  broker/store worker exceptions unless a spec changes first.
- All ordinary broker effects, task-local control responses, lifecycle state
  changes, process-title updates, endpoint state, and reserved-policy effects
  stay on the owning reactor thread.
- `MultiQueueWatcher` remains the queue readiness owner. `ServiceTask` must
  not implement a second queue polling or discovery loop.
- Due timers are local scheduling signals only. A zero timeout must not become
  permission to probe inactive queues.
- PONG/STATUS responses remain cached/lightweight. Do not make PONG query
  Monitor tables, scan queues, or recompute manager service evidence.
- Manager service convergence decisions stay in manager-owned reducers and
  manager methods. `ServiceTask` must not know about service keys, leadership,
  singleton ownership, autostart, or duplicate kill policy.
- TaskMonitor cleanup semantics do not change. This plan only shares mechanics.
- Heartbeat behavior does not change. It remains a task-shaped interval
  emitter with explicit queue writes on the reactor.
- No new CLI flags, public command output, queue payload shapes, or persisted
  schema changes.

Hidden couplings to keep visible:

- Manager child launch has two phases: a broker-free worker starts the child
  process, then the manager reactor records `_child_processes`, publishes
  `task_spawned`, updates service-owner state, and acknowledges the reserved
  message. Do not collapse those phases.
- TaskMonitor worker results update cached PONG fields. Control must stay
  responsive while a worker is active.
- Existing tests sometimes inspect TaskMonitor private in-flight fields. The
  better contract is PONG in-flight fields and bounded behavior, but test
  helpers may still need a narrow internal wait utility.
- `BaseTask.cleanup()` stops worker lanes and closes cached queues. Do not
  create worker pools or background threads outside that cleanup path.
- `ServiceTask` should help long-lived task-shaped services. It should not
  become a base for ordinary one-shot task execution semantics.

Fatal versus best-effort failures:

- Fatal: wrong lifecycle state, wrong reserved-message acknowledgement, worker
  result applied on a worker thread, duplicate service lane launch when the
  lane should be single-flight, queue contract changes, process-title/event
  ordering drift during service activation, or a second manager execution path.
- Best effort: serve-log diagnostics, optional PONG extension failure, and
  cleanup/log emission diagnostics that are already best effort today.
- Auxiliary cleanup failure must not downgrade a successful child launch,
  successful control response, or successful task execution.

Rollback and rollout:

- This is an internal refactor with no durable format changes. Rollback should
  be a code revert only.
- Each slice should be mergeable independently. Slice 1 should leave behavior
  unchanged after converting TaskMonitor thread-launch duplication. Slice 2
  should preserve all public behavior while changing inheritance and internal
  helpers.
- There are no intended one-way doors. If implementation requires a schema
  change, queue payload change, or public contract change, stop and re-plan.

Review gates:

- Self-review is required before implementation.
- External review is required before implementation because this touches
  shared runtime structure, manager scheduling, TaskMonitor cleanup, and
  multiple documentation layers.
- Stop if the implementation starts turning `ServiceTask` into a template
  method superclass that owns Manager or Monitor `process_once()` end to end.

## 5. Tasks

### Slice 1: BaseTask Neutral Worker-Lane Mechanics

1. Add tests for the neutral worker-lane primitive before implementation.

   - Outcome: define the shared mechanic as a small internal contract before
     code moves.
   - Files to touch:
     - `tests/tasks/test_task_execution.py`
   - Read first:
     - `weft/core/tasks/base.py` `_submit_worker_call`,
       `_publish_worker_result`, `_drain_worker_results`,
       `_stop_worker_lanes`
     - Existing `ReactorTestTask` tests in `tests/tasks/test_task_execution.py`
   - Test design:
     - Use `broker_env` and a real `ReactorTestTask`.
     - Add a test for a new neutral lane helper, expected name
       `_submit_worker_lane(...)`, that proves worker results are still
       delivered to `_handle_worker_result()` on the main thread.
     - Add an error-result test: a worker function raises, the result is
       delivered as `TaskWorkerResult.error`, and the main thread handles it.
     - Add a thread-name or lane-slug test only if it can be asserted without
       depending on exact thread scheduling.
   - What not to mock:
     - Do not mock `threading.Thread`, `Queue`, or the worker-result queue.
     - Do not mock `_publish_worker_result`; the result channel is the behavior
       under test.
   - Red-green note:
     - These tests should fail until the new helper exists. This is a clean
       red-green TDD task.
   - Stop if:
     - The test requires a fake queue or fake watcher to pass.
     - The proposed helper starts accepting scheduling policy, cleanup policy,
       or service semantics.
   - Done when:
     - The new tests fail for the missing helper and existing worker tests
       still describe the old contract.

2. Add `_submit_worker_lane(...)` in `BaseTask` and keep
   `_submit_worker_call(...)` broker-free.

   - Outcome: one shared internal worker launcher owns thread creation,
     result publication, worker-thread tracking, and cleanup integration.
   - Files to touch:
     - `weft/core/tasks/base.py`
     - `tests/tasks/test_task_execution.py`
   - Implementation:
     - Add a private method on `BaseTask`:
       `def _submit_worker_lane(self, lane: str, func: Callable[[], Any]) -> threading.Thread`.
     - Move the current thread creation and result publication body from
       `_submit_worker_call(...)` into `_submit_worker_lane(...)`.
     - Make `_submit_worker_call(...)` a thin wrapper around
       `_submit_worker_lane(...)`.
     - Keep the `_submit_worker_call(...)` docstring strict: worker callables
       passed through this wrapper must remain broker-free and must not mutate
       TaskSpec state.
     - Give `_submit_worker_lane(...)` a narrower mechanical docstring:
       it starts a named local lane and publishes a result. It does not grant
       broker/store authority. Callers that use broker/store work must be
       explicitly authorized by spec and class-level documentation.
   - Style constraints:
     - Import no new dependencies.
     - Keep type annotations complete.
     - Keep the method private.
   - Tests:
     - Make the tests from Task 1 pass.
     - Existing `BaseTask` worker-result tests must still pass.
   - Stop if:
     - The helper starts encoding TaskMonitor or Manager-specific behavior.
     - The helper changes queue wait behavior or result drain budgeting.
   - Done when:
     - `tests/tasks/test_task_execution.py` targeted worker tests pass.

3. Convert TaskMonitor broker/store worker submission to the neutral lane.

   - Outcome: remove duplicated thread-launch boilerplate from
     `TaskMonitor` without changing TaskMonitor cleanup authority.
   - Files to touch:
     - `weft/core/monitor/task_monitor.py`
     - `tests/tasks/test_task_monitor.py`
   - Read first:
     - `TaskMonitor._submit_terminal_control_cleanup_worker`
     - `TaskMonitor._submit_builtin_cycle_worker`
     - `TaskMonitor._handle_builtin_cycle_worker_result`
     - `TaskMonitor._handle_control_cleanup_worker_result`
     - `docs/specifications/07-System_Invariants.md` [IMPL.8], [IMPL.9],
       [OBS.13]
   - Implementation:
     - Replace manual `threading.Thread(...)` blocks in
       `_submit_terminal_control_cleanup_worker(...)` and
       `_submit_builtin_cycle_worker(...)` with `_submit_worker_lane(...)`.
     - Keep method names if tests or local readability benefit from them, but
       they should become thin wrappers.
     - Keep the docstrings that state these are TaskMonitor-specific
       broker/store worker exceptions.
     - Do not convert custom processors to `_submit_worker_lane(...)`; keep
       `_submit_worker_call(...)` there because custom processors are
       broker-free by contract.
   - Tests:
     - Use existing TaskMonitor PING/in-flight tests. Do not replace them with
       mocks.
     - Add or update a test that blocks a built-in cycle worker and proves a
       real `PING` sent through `ctrl_in` receives a real PONG on `ctrl_out`
       while the worker is active.
     - Add or update a test that blocks the terminal control cleanup worker
       and proves PONG includes `control_cleanup_in_flight: true`.
   - What not to mock:
     - Do not mock SimpleBroker queues, control queues, or PONG writes.
     - A narrow monkeypatch of the worker body to block on a `threading.Event`
       is acceptable.
   - Stop if:
     - The implementation weakens the generic broker-free worker rule.
     - The worker starts mutating `_last_*`, `_monitor_store_status`, or
       TaskSpec state directly instead of returning a result.
   - Done when:
     - TaskMonitor worker tests pass and thread-launch duplication is gone.

4. Update specs for Slice 1.

   - Outcome: specs describe the neutral mechanical helper and preserve the
     broker-free wrapper contract.
   - Files to touch:
     - `docs/specifications/01-Core_Components.md`
     - `docs/specifications/07-System_Invariants.md`
   - Implementation:
     - In [CC-2.2] or [CC-2.5], explain that `BaseTask` owns a private
       mechanical worker-lane launcher and that `_submit_worker_call(...)`
       remains the broker-free wrapper used by ordinary Weft worker lanes.
     - In [IMPL.8]/[IMPL.9], keep the rule that ordinary worker lanes are
       broker-free and explicitly keep the TaskMonitor exception narrow.
     - Add this plan to the related plan/backlink sections if not already
       present.
   - Stop if:
     - The doc change implies arbitrary tasks may use broker/store work from
       worker threads.
   - Done when:
     - The spec text matches the code and does not broaden worker authority.

### Slice 2: Thin ServiceTask Layer

5. Add tests for a minimal `ServiceTask` before implementation.

   - Outcome: define `ServiceTask` as a thin internal helper, not a framework.
   - Files to touch:
     - Add `tests/tasks/test_service_task.py`
   - Read first:
     - `tests/tasks/test_task_execution.py` test style and fixtures
     - `weft/core/tasks/base.py`
     - `weft/core/tasks/heartbeat.py` activation pattern
     - `weft/core/monitor/task_monitor.py` in-flight worker fields
     - `weft/core/manager.py` `_timeout_until_ns`, `_interval_timeout`, and
       startup lifecycle publication
   - Test helper:
     - Define a small `ServiceTestTask(ServiceTask)` inside the test file.
     - Use `broker_env` and a real TaskSpec.
   - Tests:
     - Activation helper test: calling the service activation helper marks
       `task_spawning` then `task_started` in `weft.log.tasks`, updates status
       to `running`, and does not duplicate activation if called twice.
     - Due-time helper test: `_timeout_until_ns(...)` and
       `_interval_timeout(...)` return bounded non-negative float values.
     - Single-flight lane test: starting the same service lane while a work
       item is already in flight fails cleanly and does not start a second
       thread.
     - Lane clear test: after a worker result is committed by the test subclass,
       the lane is no longer in flight.
   - What not to mock:
     - Do not mock `weft.log.tasks`; read the real broker queue.
     - Do not mock worker result delivery.
   - Red-green note:
     - These tests should fail until `ServiceTask` exists.
   - Stop if:
     - The test needs a `ServiceTask.process_once()` template. That is the
       wrong abstraction for this plan.
   - Done when:
     - Tests fail for the missing class and helpers, with no broad mocks.

6. Add `weft/core/tasks/service.py` with a thin `ServiceTask(BaseTask)`.

   - Outcome: create the shared service layer agreed for Manager and Monitor.
   - Files to touch:
     - Add `weft/core/tasks/service.py`
     - `weft/core/tasks/__init__.py` only if tests or internal imports need a
       re-export. Prefer direct internal imports from `.service`.
     - `tests/tasks/test_service_task.py`
   - Implementation:
     - Add module docstring with spec references:
       `docs/specifications/01-Core_Components.md [CC-2.2], [CC-2.3],
       [CC-2.5]` and `docs/specifications/07-System_Invariants.md [IMPL.8],
       [IMPL.9]`.
     - Define `class ServiceTask(BaseTask)`.
     - Add internal helper state such as
       `self._service_lane_work: dict[str, Any] = {}` after
       `BaseTask.__init__` has run, or before if helper methods can be called
       during subclass initialization. Be explicit and test the initialization
       order.
     - Add `_activate_service_task(...)` to publish the standard long-lived
       service lifecycle:
       `mark_started`, optional process title `"spawning"`, `task_spawning`,
       `mark_running`, process title `"running"`, `task_started`.
       It must no-op if status is not `created`.
     - The helper must accept an explicit option such as
       `set_spawning_title: bool = False`. Use `False` for TaskMonitor and
       Heartbeat to match current behavior. Use `True` for Manager only if it
       preserves Manager's current startup title sequence.
     - Add `_service_lane_in_flight(lane: str | None = None) -> bool`.
     - Add `_service_lane_work(lane: str) -> Any | None`.
     - Add `_start_service_lane(lane: str, work: Any, func: Callable[[], Any]) -> threading.Thread`.
       It should:
       - reject duplicate work for the same lane;
       - record the work before thread start;
       - call `_submit_worker_lane(...)`;
       - clear the recorded work if thread submission raises.
     - Add `_pop_service_lane_work(lane: str) -> Any | None`.
     - Add `_timeout_until_ns(...)` and `_interval_timeout(...)`.
     - Do not implement `process_once()` in `ServiceTask`.
     - Do not add service-key, manager-election, cleanup, heartbeat, queue,
       or scheduling policy.
   - Style constraints:
     - Keep this module small. If it exceeds roughly 200 lines in the first
       implementation, stop and check for over-generalization.
     - Do not use generics unless a concrete type-safety problem appears in
       the implementation. `Any` is acceptable for private lane work payloads.
   - Tests:
     - Make tests from Task 5 pass.
     - Existing BaseTask tests must still pass.
   - Stop if:
     - The class starts looking like a framework with hooks for every phase of
       Manager or Monitor.
     - The class owns queue draining, control parsing, or a whole service turn.
   - Done when:
     - `ServiceTask` exists, tests pass, and it has no domain policy.

7. Adopt `ServiceTask` in `TaskMonitor`.

   - Outcome: TaskMonitor shares service activation, due-time helpers, and
     lane in-flight tracking while preserving PONG fields and cleanup behavior.
   - Files to touch:
     - `weft/core/monitor/task_monitor.py`
     - `tests/tasks/test_task_monitor.py`
     - `tests/core/test_task_monitoring.py`
   - Read first:
     - `TaskMonitor.__init__`
     - `_activate_monitor`
     - `next_wait_timeout`
     - `process_once`
     - `_maybe_start_terminal_control_cleanup_worker`
     - `_maybe_start_builtin_cycle_worker`
     - `_process_monitor_candidates`
     - `_handle_worker_result`
     - `_control_snapshot_fields`
     - `_task_monitor_pong_extension`
   - Implementation:
     - Change `TaskMonitor(BaseTask)` to `TaskMonitor(ServiceTask)`.
     - Replace `_activate_monitor()` body with the shared activation helper,
       keeping the method if it improves local readability.
     - Replace direct in-flight fields for built-in cycle, custom processor,
       and control cleanup with `ServiceTask` lane helpers where reasonable.
       If keeping compatibility properties during the migration makes the diff
       safer, use read-only properties that delegate to the new lane state.
       Do not keep two independent sources of truth.
     - PONG fields must remain unchanged:
       `processor_in_flight`, `builtin_cycle_in_flight`,
       `monitor_builtin_cycle_in_flight`, and `control_cleanup_in_flight`.
     - Preserve the three lane names:
       `TASK_MONITOR_PROCESSOR_WORKER_LANE`,
       `TASK_MONITOR_BUILTIN_CYCLE_WORKER_LANE`,
       `TASK_MONITOR_CONTROL_CLEANUP_WORKER_LANE`.
     - Keep custom processor submission through `_submit_worker_call(...)`.
       It is broker-free.
     - Keep built-in cycle and control cleanup submission through the neutral
       `_submit_worker_lane(...)` path, via `ServiceTask._start_service_lane`.
     - Keep `process_once()` explicit. It may call helper methods, but
       `ServiceTask` must not control the whole turn.
   - Tests:
     - Update private-field waits in `tests/tasks/test_task_monitor.py` and
       `tests/core/test_task_monitoring.py` to use a local helper such as
       `_task_monitor_has_in_flight_work(task)` or the new
       `_service_lane_in_flight(...)`.
     - Keep PONG assertions on public control payload fields.
     - Keep real broker-backed PING tests.
     - Run existing TaskMonitor cleanup tests if any helper touches cleanup
       worker scheduling.
   - Stop if:
     - The implementation changes cleanup selection, cleanup limits, Monitor
       store state, or task-log deletion semantics.
     - The implementation introduces a second in-flight state source.
   - Done when:
     - TaskMonitor PING, worker, scheduling, and cleanup tests pass with PONG
       fields unchanged.

8. Adopt `ServiceTask` in `HeartbeatTask`.

   - Outcome: heartbeat service shares standard activation and due-time
     helpers without changing interval behavior.
   - Files to touch:
     - `weft/core/tasks/heartbeat.py`
     - `tests/tasks/test_heartbeat.py` if present, otherwise the existing
       heartbeat test file found by `rg -n "HeartbeatTask|heartbeat" tests`
   - Read first:
     - `HeartbeatTask.__init__`
     - `_activate_waiter`
     - `process_once`
     - `next_wait_timeout`
     - `docs/specifications/05-Message_Flow_and_State.md` [MF-3.2]
   - Implementation:
     - Change `HeartbeatTask(BaseTask)` to `HeartbeatTask(ServiceTask)`.
     - Replace `_activate_waiter()` body with the shared activation helper,
       keeping `_activate_waiter()` as a local wrapper if clearer.
     - Use `_timeout_until_ns(...)` or `_interval_timeout(...)` if it removes
       local due-time duplication without changing behavior.
     - Do not add worker lanes to heartbeat unless the current code already
       needs them. This task is not an invitation to redesign heartbeat.
   - Tests:
     - Existing heartbeat registration/cancel/due-emission tests must pass.
     - Add a narrow lifecycle test only if none already proves startup emits
       `task_spawning` and `task_started`.
   - Stop if:
     - The change affects heartbeat registration payload shape, due coalescing,
       idle shutdown, endpoint ownership, or singleton behavior.
   - Done when:
     - Heartbeat tests pass and behavior is unchanged.

9. Adopt `ServiceTask` in `Manager` only where it removes real duplication.

   - Outcome: Manager shares service activation and due-time helpers while
     keeping all Manager-specific ordering explicit.
   - Files to touch:
     - `weft/core/manager.py`
     - `tests/core/test_manager.py`
     - `tests/specs/manager_architecture/test_manager_state_events.py` if
       lifecycle event assertions need update
   - Read first:
     - `Manager.__init__`
     - `Manager._launch_child_task`
     - `Manager._handle_child_launch_result`
     - `Manager.next_wait_timeout`
     - `Manager.process_once`
     - `docs/specifications/03-Manager_Architecture.md` [MA-1]
     - `docs/specifications/07-System_Invariants.md` [MANAGER.8]-[MANAGER.17]
   - Implementation:
     - Change `Manager(BaseTask)` to `Manager(ServiceTask)`.
     - Replace the startup lifecycle publication in `__init__` with the
       shared activation helper only if the emitted events and process-title
       sequence remain identical. Manager currently sets the process title to
       `"spawning"` before `task_spawning`, then to `"running"` before
       `task_started`; preserve that with the helper option.
     - Replace local `_timeout_until_ns(...)` and `_interval_timeout(...)`
       with inherited helpers. Remove the duplicate local methods.
     - Do not force `_active_child_launches` into generic service lane state
       unless it is clearly simpler and all child launch invariants remain
       explicit. Manager child launch tracking carries child TID, reserved
       queue, source queue, service metadata, and ack policy. It is acceptable
       for this to remain Manager-owned.
     - Keep `_launch_child_task(...)`, `_handle_child_launch_result(...)`,
       `_commit_child_launch_success(...)`, and reserved-message ack behavior
       Manager-local.
     - Keep `process_once()` explicit. Do not move leadership, drain, service
       convergence, or spawn-drain ordering into `ServiceTask`.
   - Tests:
     - Existing manager child-launch worker tests must pass.
     - Existing manager state event tests must pass without changed event
       names or ordering.
     - Add a narrow test only if activation refactor could duplicate startup
       events.
   - Stop if:
     - Manager behavior starts depending on ServiceTask lane state for spawn
       authority.
     - Registry leadership, internal spawn priority, idle timeout, or child
       launch ack changes.
   - Done when:
     - Manager tests pass and the diff removes duplication without hiding
       Manager's domain ordering.

10. Update specs, exports, and plan metadata after Slice 2.

   - Outcome: documentation and plan index describe the final shared structure.
   - Files to touch:
     - `docs/specifications/01-Core_Components.md`
     - `docs/specifications/03-Manager_Architecture.md` only if Manager
       implementation mapping changes materially
     - `docs/specifications/05-Message_Flow_and_State.md` only if
       implementation mapping for TaskMonitor/Heartbeat changes materially
     - `docs/specifications/07-System_Invariants.md`
     - `docs/plans/README.md`
     - `weft/core/tasks/__init__.py` only if `ServiceTask` is intentionally
       re-exported
   - Implementation:
     - Add `ServiceTask` to the [CC-2.2]/[CC-2.3] implementation mapping as
       an internal long-lived service helper.
     - State that `ServiceTask` does not own queue readiness or a generic
       service turn.
     - Keep [IMPL.8]/[IMPL.9] precise: ordinary broker effects remain on the
       reactor; TaskMonitor broker/store worker lanes remain narrow
       exceptions.
     - Mark this plan completed only after implementation and verification
       land. Until then it stays `draft`.
   - Stop if:
     - Documentation starts describing `ServiceTask` as public API.
     - Documentation implies public behavior changed.
   - Done when:
     - Metadata tests pass and specs match code ownership.

## 6. Testing Plan

Use the narrowest real test that proves each contract:

- Use `broker_env` and real `Queue` objects for `BaseTask`, `ServiceTask`,
  TaskMonitor, Heartbeat, and Manager unit/integration tests.
- Use `WeftTestHarness` only if a CLI-level manager lifecycle behavior must be
  proved. Most of this refactor should not need CLI subprocess tests.
- Do not mock SimpleBroker queue semantics, reservation behavior, control
  queues, task logs, worker result delivery, or Manager child launch ack
  paths.
- Narrow monkeypatches are acceptable for deterministic worker blocking:
  patch a worker body to wait on a `threading.Event`, then send a real PING
  through `ctrl_in` and read a real PONG from `ctrl_out`.
- Prefer red-green TDD:
  - Slice 1 tests should fail before `_submit_worker_lane(...)` exists.
  - Slice 2 tests should fail before `ServiceTask` exists.
  - Refactor-adoption tasks should rely mostly on existing behavior tests, but
    add a failing test first when a helper changes observable behavior such as
    activation event duplication or PONG in-flight fields.

Specific regression coverage:

- `BaseTask`
  - worker result committed on main thread;
  - worker result errors delivered through `TaskWorkerResult.error`;
  - result queue remains bounded and drain budget remains bounded;
  - `_submit_worker_call(...)` still delegates to the same result machinery.
- `TaskMonitor`
  - PING responds while built-in cycle worker is in flight;
  - PING responds while control cleanup worker is in flight;
  - custom processor lane remains broker-free and PONG reports
    `processor_in_flight`;
  - PONG field names and meanings do not change;
  - cleanup behavior and Monitor-store stats remain unchanged.
- `HeartbeatTask`
  - startup lifecycle remains `task_spawning` then `task_started`;
  - registration, cancel, due emission, and idle shutdown behavior remain
    unchanged.
- `Manager`
  - startup lifecycle event order remains unchanged;
  - child launch worker still commits durable effects on the reactor;
  - reserved spawn message ack still happens only after successful child
    launch;
  - leadership yield, drain, service convergence, and internal spawn priority
    tests remain green.
- `Docs`
  - plan metadata row exists and status matches;
  - specs contain backlinks and current implementation mapping.

Tempting tests to avoid:

- A mock-only test that asserts `_submit_worker_lane(...)` was called. That
  proves wiring, not behavior.
- A test that mocks `Queue.delete` or `Queue.move_one` for Manager spawn ack.
  Use real queues.
- A test that asserts exact private TaskMonitor field names after Slice 2.
  Prefer PONG payload fields and a small test helper around the new service
  lane state.

## 7. Verification And Gates

Per-task commands:

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/tasks/test_task_execution.py -q
./.venv/bin/python -m pytest tests/tasks/test_service_task.py -q
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py -q
./.venv/bin/python -m pytest tests/core/test_task_monitoring.py -q
./.venv/bin/python -m pytest tests/core/test_manager.py -q
./.venv/bin/python -m pytest tests/tasks/test_heartbeat.py -q
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
```

Adjust the heartbeat command to the actual heartbeat test file if
`tests/tasks/test_heartbeat.py` does not exist.

Final gates before claiming implementation done:

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/tasks/test_task_execution.py tests/tasks/test_service_task.py tests/tasks/test_task_monitor.py tests/core/test_task_monitoring.py tests/core/test_manager.py -q
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft
```

If Manager, TaskMonitor, or Heartbeat behavior changes beyond internal
inheritance and helper calls, run the broader default suite:

```bash
./.venv/bin/python -m pytest
```

Observable success:

- Worker threads are still bounded by `BaseTask` cleanup and result queue
  backpressure.
- TaskMonitor PING remains responsive while monitor workers are active.
- Manager child launch and spawn ack behavior is unchanged.
- No public queue payload, CLI shape, or TaskSpec schema changed.
- Specs and plan index match the code.

## 8. Independent Review Loop

External review is required before implementation because this refactor touches
shared runtime classes and can easily look correct while hiding Manager or
TaskMonitor ordering bugs.

Reviewer prompt:

> Read `docs/plans/2026-05-20-service-task-shared-reactor-extraction-plan.md`.
> Carefully examine the plan and the associated code. Look for errors, bad
> ideas, missing invariants, over-abstraction, and latent ambiguities. Do not
> implement anything. Answer: Could you implement this confidently and
> correctly if asked?

Reviewer should read:

- `weft/core/tasks/base.py`
- `weft/core/tasks/multiqueue_watcher.py`
- `weft/core/manager.py`
- `weft/core/monitor/task_monitor.py`
- `weft/core/tasks/heartbeat.py`
- `docs/specifications/01-Core_Components.md`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/agent-context/runbooks/testing-patterns.md`

Review handling:

- Address each finding explicitly.
- Update this plan for accepted findings.
- If rejecting a finding, record why the current direction is still better.
- Do not start implementation while external review is pending unless the user
  explicitly accepts that risk.

## 9. Out Of Scope

- No `ServiceTask.process_once()` template.
- No generic scheduler, event bus, worker pool, plugin surface, or `asyncio`.
- No new CLI flags or command output changes.
- No queue name, queue payload, TaskSpec schema, result payload, or Monitor
  table schema changes.
- No manager election, service convergence, singleton ownership, or duplicate
  kill redesign.
- No TaskMonitor cleanup policy changes.
- No heartbeat behavior changes beyond sharing activation/due-time helpers.
- No conversion of ordinary worker lanes to broker/store authority.
- No drive-by cleanup in unrelated task types.

## 10. Fresh-Eyes Review

Self-review pass 1, 2026-05-20:

- Finding: The first draft risked making `ServiceTask` responsible for
  Manager child-launch in-flight tracking. That would hide spawn-request
  acknowledgement and service metadata coupling.
  - Fix: Task 9 now says Manager may keep `_active_child_launches` local, and
    explicitly forbids making ServiceTask lane state spawn authority.
- Finding: The plan could have implied `_submit_worker_lane(...)` grants
  broker/store authority to any caller.
  - Fix: Slice 1 now distinguishes the neutral mechanical helper from
    `_submit_worker_call(...)`, and repeats that only TaskMonitor's declared
    lanes may use broker/store worker effects.
- Finding: Existing tests assert private TaskMonitor in-flight fields, so a
  zero-context implementer might preserve duplicate state just to avoid test
  edits.
  - Fix: Task 7 now directs the implementer to replace private-field waits
    with a helper or `ServiceTask` lane state while preserving PONG payload
    fields as the behavioral contract.
- Finding: The plan needed a hard stop against a template-method service
  superclass.
  - Fix: `ServiceTask.process_once()` is explicitly out of scope in the goal,
    invariants, tasks, and out-of-scope section.

Self-review pass 2, 2026-05-20:

- No material direction change found. The plan still follows the agreed two
  slices: BaseTask lane mechanics first, thin ServiceTask second.
- Residual risk: Manager inheritance changes can still produce subtle
  lifecycle event ordering drift. Task 9 and the verification gates now require
  manager state-event tests and prohibit moving Manager turn ordering into
  ServiceTask.
- External review remains required before implementation because this is a
  shared runtime refactor.

Self-review pass 3, 2026-05-20:

- Finding: The activation helper wording could have erased Manager's existing
  temporary `"spawning"` process-title update while preserving only the
  lifecycle events. That would be a refactor-induced observability drift.
  - Fix: The plan now requires an explicit activation option for the
    `"spawning"` title step and tells the implementer to use it only where it
    matches current behavior.
- No broader direction change was needed. This remains a shared-mechanics
  extraction, not a service-loop redesign.

# Manager-Owned Internal Service Supervision Plan

Status: draft
Source specs: see Source Documents below
Superseded by: ./2026-05-08-phase-7-manager-service-reconciler-cleanup-plan.md

## 1. Goal

Move Weft-owned singleton runtimes onto one manager-owned service
reconciliation contract. The immediate target is the heartbeat service and the
manager-supervised `TaskMonitorTask`; the same reconciliation path must also
become the path used by autostart `once` and `ensure` policies. This fixes the
current split where heartbeat is started and selected through endpoint lookup
while TaskMonitor is manager-supervised, and it fixes the TaskMonitor idle CPU
loop by making the monitor reactive to heartbeat/inbox/control wakeups instead
of re-entering its scheduling turn every 50ms.

This plan is retained for background context. Remaining single-reconciler
cleanup work should follow
`2026-05-08-phase-7-manager-service-reconciler-cleanup-plan.md`, which tightens
the implementation scope after review.

## 2. Source Documents

Source specs:

- `docs/specifications/01-Core_Components.md` [CC-2.1], [CC-2.2], [CC-2.3],
  [CC-2.4], [CC-2.4.1], [CC-2.5]
- `docs/specifications/03-Manager_Architecture.md` [MA-0], [MA-1],
  [MA-1.4], [MA-1.6], [MA-1.6a], [MA-1.7], [MA-3]
- `docs/specifications/05-Message_Flow_and_State.md` [MF-1], [MF-2],
  [MF-3], [MF-3.1], [MF-3.2], [MF-5], [MF-6]
- `docs/specifications/07-System_Invariants.md` [STATE.1]-[STATE.6],
  [QUEUE.1]-[QUEUE.6], [OBS.9], [OBS.12], [OBS.13],
  [MANAGER.8]-[MANAGER.14], [IMPL.5]-[IMPL.7]

Existing plans and status:

- `docs/plans/2026-04-17-heartbeat-service-plan.md` shipped the first
  heartbeat service. This plan supersedes its startup/ownership shape for
  internal heartbeat service management, but not the heartbeat task's interval
  emitter semantics.
- `docs/plans/2026-05-07-manager-selection-ping-pong-liveness-plan.md` shipped
  keyed manager PING/PONG liveness. Reuse that control-probe approach where
  this plan needs positive liveness evidence.
- `docs/plans/2026-05-07-phase-7-task-monitor-supervision-and-cleanup-plan.md`
  remains the governing phase 7 plan for TaskMonitor cleanup parts. This plan
  tightens phase 7 part 1 service supervision and wake behavior before cleanup
  parts 2 and 3 proceed.
- `docs/plans/2026-04-16-autostart-hardening-and-contract-alignment-plan.md`
  defines current autostart `once` and `ensure` behavior. This plan does not
  change the manifest format; it moves autostart lifecycle bookkeeping onto
  the shared manager service reconciler.

Repo guidance:

- `AGENTS.md`, especially project conventions, "Design Philosophy", and
  "How to Work".
- `docs/agent-context/engineering-principles.md`.
- `docs/agent-context/runbooks/writing-plans.md`.
- `docs/agent-context/runbooks/hardening-plans.md`.
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`.
- `docs/lessons.md`, especially the entries about reusing the
  `Manager -> Consumer -> TaskRunner` path, generator-based queue reads, and
  real broker/process tests.

Spec updates required during implementation:

- Plan backlinks have been added to
  `docs/specifications/01-Core_Components.md`,
  `docs/specifications/03-Manager_Architecture.md`,
  `docs/specifications/05-Message_Flow_and_State.md`, and
  `docs/specifications/07-System_Invariants.md`. Keep those backlinks intact
  while implementing.
- Update [MA-1.6] and [MA-1.6a] so autostart, heartbeat, and TaskMonitor all
  point at the same manager-owned service reconciler.
- Update [MF-3.2] so heartbeat startup is manager-owned. Endpoint discovery may
  remain the write address for heartbeat registration, but endpoint discovery
  must not be the service election or liveness authority for internal services.
- Update [MF-5] to say the supervised monitor is wake-driven and must not
  busy-loop between heartbeat/local interval due times.

## 3. Context and Key Files

Read these files before editing:

- `weft/core/manager.py`
  - Current owner of manager registry, dispatch ownership, child launch,
    TaskMonitor supervision, and autostart loops.
  - Current TaskMonitor methods: `_task_monitor_supervision_allowed`,
    `_build_task_monitor_spawn_payload`, `_enqueue_task_monitor_request`,
    `_tick_task_monitor`.
  - Current autostart methods: `_tick_autostart`,
    `_build_autostart_spawn_payload`, `_active_autostart_sources`,
    `_enqueue_autostart_request`, `_prune_autostart_state`.
- `weft/core/manager_runtime.py`
  - Current manager registry replay, stale-record pruning, and keyed PONG
    rescue for manager selection.
  - Reuse concepts and helpers. Do not import command-layer code into core.
- `weft/core/control_probe.py`
  - Shared keyed PING/PONG helper. Use this for bounded positive liveness
    probes; do not invent a second control-probe format.
- `weft/core/heartbeat.py`
  - Current helper starts heartbeat by resolving `_weft.heartbeat` and directly
    submitting an internal spawn request. That direct startup path is one of
    the bugs this plan removes.
- `weft/core/tasks/heartbeat.py`
  - Current interval emitter. Keep its queue contract and coalescing behavior.
    It already has a bounded wait loop that is the right model for TaskMonitor.
- `weft/core/tasks/task_monitor.py`
  - Current persistent monitor calls `upsert_heartbeat()`, scans
    `weft.log.tasks`, and calls the configured processor. It currently
    re-enters via the generic spawned-task loop every 50ms.
- `weft/core/tasks/base.py`
  - Owns endpoint registration, control handling, task log state emission, TID
    mappings, and the default task loop. Do not bypass its queue helpers.
- `weft/core/tasks/multiqueue_watcher.py`
  - Owns the multi-queue activity waiter seam used by task-local inbox/control
    queues.
- `weft/core/launcher.py`
  - Current spawned child process loop. It calls `task.process_once()` and then
    `wait_for_activity(timeout=TASK_PROCESS_POLL_INTERVAL)`. This is the
    source of the TaskMonitor idle spin.
- `weft/core/endpoints.py`
  - Public endpoint discovery helper. Internal services may use endpoint
    records as an address, but must not use public endpoint resolution as their
    singleton liveness contract.
- `weft/_constants.py`
  - All new internal metadata keys, service names, defaults, and env/config
    parsing belong here.
- `tests/core/test_manager.py`
  - Current manager, TaskMonitor supervision, and autostart tests.
- `tests/tasks/test_task_monitor.py`
  - Current TaskMonitor task tests.
- `tests/tasks/test_heartbeat.py`
  - Current heartbeat task tests.
- `tests/core/test_heartbeat_helpers.py`
  - Current heartbeat helper tests. These should change when direct heartbeat
    spawning is removed.
- `tests/helpers/weft_harness.py`
  - Use this for CLI/process integration where a real manager and child process
    are needed.

Comprehension questions before editing:

- What exact condition currently authorizes a manager to launch child work?
  Expected answer: `_evaluate_dispatch_ownership().state == "self"` after
  registry replay and liveness checks. Literal "only one manager process
  exists" is not the rule.
- Which layer is allowed to select an internal runtime task class?
  Expected answer: the manager-owned spawn envelope, not public stored
  TaskSpec metadata. See [IMPL.7].
- Why is `_weft.heartbeat` endpoint resolution insufficient as singleton
  liveness proof?
  Expected answer: endpoint discovery is a public discovery primitive; it can
  point at ordinary queues and is not a strong lease. Internal services need
  manager-owned service selection plus positive liveness evidence.
- Why must TaskMonitor not watch `weft.log.tasks` directly with the
  multi-queue waiter?
  Expected answer: task-log writes should not wake the monitor for every row;
  heartbeat or bounded local due time is the scheduler, and `weft.log.tasks`
  must be scanned by generator/high-water reads.

## 4. Current Problems

1. Heartbeat and TaskMonitor use different supervision contracts.
   - TaskMonitor is manager-supervised.
   - Heartbeat is endpoint-discovered and can be spawned by
     `ensure_heartbeat_service()`.
   - On ops this allowed `_weft.heartbeat` to resolve to a stale endpoint, so
     TaskMonitor registration was written into a dead heartbeat inbox.

2. Public endpoint discovery is doing internal singleton work.
   - `weft.core.endpoints.resolve_endpoint()` is correct for public named
     endpoints, but internal services need a stronger contract.
   - Do not change public endpoint semantics to solve this. Add a manager-owned
     internal service contract and keep public endpoint discovery as discovery.

3. TaskMonitor is not reactive enough.
   - The launcher calls `process_once()` every 50ms for every spawned task.
   - `TaskMonitorTask.process_once()` uses local due-time state, but it does
     not own a blocking wait the way `HeartbeatTask` does.
   - On ops the monitor reported `waiting` but consumed roughly 65% of one CPU
     core.

4. Autostart already has `once` and `ensure` semantics, but they live in a
   separate manager loop.
   - The user requirement is one service path and one contract for internal
     services and autostarts.
   - Manifest parsing can stay autostart-specific, but once a desired service
     exists it must flow through the same lifecycle and enqueue logic as
     heartbeat and TaskMonitor.

## 5. Target Architecture

### 5.1 Terms

- **Canonical dispatch owner**: the live manager selected for
  `weft.spawn.requests` by existing manager election. This means
  `_evaluate_dispatch_ownership().state == "self"`. It does not mean no other
  manager process exists.
- **Managed service**: a manager-owned desired child task described by a stable
  service key, lifecycle policy, and spawn payload.
- **Internal service**: a Weft-owned managed service such as heartbeat or
  TaskMonitor. Internal services use reserved metadata and may claim reserved
  `_weft.*` endpoints.
- **Autostart service**: a managed service derived from an autostart manifest.
  Its stable service key is the resolved manifest path.
- **Service candidate**: an observed task that claims the same service key and
  has enough evidence to be considered live or uncertain.

### 5.2 Contract

Manager-owned service reconciliation must follow one contract:

1. The manager first refreshes its own registry and evaluates dispatch
   ownership.
2. If ownership is not `self`, the manager must not launch user work,
   heartbeat, TaskMonitor, or autostart services.
3. If ownership is `self`, the manager builds the desired service set and runs
   one shared reconciliation path for heartbeat, TaskMonitor, and autostarts.
4. For each service key, live candidates are reduced with the same deterministic
   rule as managers: lowest live TID wins.
5. A matched keyed PONG may be used as positive liveness evidence when process
   or registry evidence is stale or uncertain. Absence of PONG is not proof of
   death.
6. The manager starts a service only when no lower-TID live canonical candidate
   exists and no in-flight spawn request for that service is already pending.
7. Higher-TID duplicates owned by the current manager should be gracefully
   stopped when practical. Do not kill unrelated tasks or unowned public
   endpoints as part of this slice.
8. A failed service must not stop manager dispatch. The service health should
   be logged and retried according to its lifecycle/backoff policy.
9. `once` starts at most once for a service key while the relevant service state
   says it already launched or a live candidate exists.
10. `ensure` keeps one live candidate running, subject to bounded restart
    backoff.

### 5.3 Expected Service Policies

- Heartbeat service:
  - lifecycle: `ensure` when the manager needs heartbeat for internal services
    such as TaskMonitor.
  - service key: stable reserved key, e.g. `_weft.service.heartbeat`.
  - task class: `INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT`.
  - endpoint: `_weft.heartbeat` remains the write address for heartbeat
    registrations.
  - failure policy: log health, retry with backoff, do not block manager work.

- TaskMonitor:
  - lifecycle: `ensure` when `WEFT_TASK_MONITOR_ENABLED` is true.
  - service key: stable reserved key, e.g. `_weft.service.task_monitor`.
  - task class: `INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR`.
  - heartbeat dependency: heartbeat should be desired before TaskMonitor, but
    TaskMonitor must retain a bounded local interval fallback.
  - failure policy: log health, retry with backoff, do not block manager work.

- Autostarts:
  - lifecycle: existing manifest `policy.mode`, either `once` or `ensure`.
  - service key: resolved manifest path.
  - task class: ordinary task or pipeline runtime selected by the existing
    TaskSpec/pipeline compilation path.
  - failure policy: existing backoff and max restart semantics must be
    preserved.

## 6. Invariants and Constraints

- Preserve manager election. Do not replace manager selection. Reuse its
  canonical ownership view and positive `self` ownership gate.
- "Manager state settled" means this manager has positive dispatch ownership.
  Do not wait for literal manager process count to become one.
- Do not create a new public singleton endpoint feature. Public named endpoints
  remain discovery only.
- Do not change autostart manifest schema.
- Do not allow public stored TaskSpecs to select internal runtime classes.
  Internal runtime selection must continue to travel through the manager-owned
  spawn envelope.
- Do not make heartbeat registrations durable audit evidence. Heartbeat
  registrations remain runtime-only and in-memory inside the heartbeat service.
- Do not make TaskMonitor cleanup part of this work. Phase 7 part 1 remains
  non-destructive. No deletion, move, reserve, or pruning of task-local queues
  or `weft.log.tasks`.
- Use generator-based reads for append-only queue history such as
  `weft.log.tasks`, `weft.state.managers`, and `weft.state.tid_mappings`.
- Do not introduce backend-specific locks or a second state database.
- Do not overload Manager with TaskMonitor scanning. Manager reconciles
  service lifecycles only; TaskMonitor scans task logs.
- Service failure is not manager failure. A dead heartbeat or TaskMonitor
  should produce health events and retry state, not suspend user task dispatch.
- Reuse `send_keyed_ping_probe()` for PING/PONG. Do not create a second
  control message format.
- Use real broker-backed tests. Mock only clocks, process liveness when an OS
  process would make a narrow unit test unstable, and external/custom
  processor callables. Do not mock away queue semantics.
- Keep edits small enough to release independently. The implementation should
  be done as a release slice before phase 7 cleanup parts continue.

Stop and re-plan if:

- implementation starts changing public endpoint behavior to get internal
  services working;
- service reconciliation starts scanning or deleting lifecycle/task-local
  queues;
- the manager needs a second child launch path instead of the existing
  manager inbox and `_build_child_spec()` flow;
- tests require broad mocks of Manager, Queue, and process behavior together;
- rollback cannot be done by reverting this release while leaving existing
  queue records harmless.

## 7. Implementation Tasks

### 1. Add service contract constants and metadata keys

Outcome: code can tag and recognize manager-owned services without relying on
ad hoc `role`, endpoint name, or autostart-specific fields.

Files to touch:

- `weft/_constants.py`
- `docs/specifications/00-Quick_Reference.md` if it lists internal metadata
  keys or runtime queues relevant to this addition
- `docs/specifications/01-Core_Components.md`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`

Add constants:

- `INTERNAL_SERVICE_KEY_METADATA_KEY`, e.g. `_weft_service_key`.
- `INTERNAL_SERVICE_LIFECYCLE_METADATA_KEY`, e.g. `_weft_service_lifecycle`.
- `INTERNAL_SERVICE_KEY_HEARTBEAT`, e.g. `_weft.service.heartbeat`.
- `INTERNAL_SERVICE_KEY_TASK_MONITOR`, e.g. `_weft.service.task_monitor`.
- A constrained lifecycle vocabulary: `once` and `ensure`. If a `Literal` type
  is needed, define it in the new service module rather than in constants.

Constraints:

- Do not rename existing constants such as
  `INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT` or
  `INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR`.
- Do not expose these keys through public CLI flags.
- Keep metadata values strings. Do not introduce nested service metadata until
  duplication forces it.

Tests:

- Add narrow tests only if constants are parsed. Constants alone do not need
  isolated tests.

Done when:

- constants exist, are grouped with internal runtime metadata constants, and
  specs mention the new service key concept.

### 2. Introduce a small manager service model

Outcome: heartbeat, TaskMonitor, and autostart can be represented as desired
services and reconciled by one manager path.

Files to touch:

- Add `weft/core/manager_services.py`.
- `weft/core/manager.py`.
- Add `tests/core/test_manager_services.py`.

Read first:

- `weft/core/manager.py` `ManagedChild`, `_tick_task_monitor`,
  `_tick_autostart`, `_active_autostart_sources`.
- `docs/specifications/03-Manager_Architecture.md` [MA-1.6], [MA-1.6a].

Recommended model:

```python
@dataclass(frozen=True, slots=True)
class ManagedServiceSpec:
    key: str
    lifecycle: Literal["once", "ensure"]
    spawn_payload: dict[str, Any]
    inbox_message: Any | None
    internal_role: str | None = None
    autostart_source: str | None = None
    priority: int = 100

@dataclass(slots=True)
class ManagedServiceState:
    launched_once: bool = False
    restarts: int = 0
    next_allowed_ns: int = 0
    spawn_pending: bool = False
    last_error: str | None = None
```

The exact names can differ, but keep the scope this small.

Implementation notes:

- The model module should be mostly pure data and small pure helpers. It should
  not import `weft.commands.*`.
- It may import constants and standard typing/dataclasses.
- Do not put queue I/O in this module unless a helper remains manager-private
  and is covered by real broker tests. Prefer Manager owning queue I/O.
- Add a helper to normalize lifecycle strings from autostart policy:
  unknown lifecycle should be rejected by the caller with a warning, not
  silently coerced.
- Add a helper to merge service metadata into a spawn payload:
  set `_weft_service_key`, `_weft_service_lifecycle`, and existing
  `autostart_source` when appropriate.

Tests:

- Unit-test lifecycle normalization with `once`, `ensure`, and invalid modes.
- Unit-test metadata merge on a payload without mutating the input payload.
- Use plain dicts here; this is a pure helper test and should stay small.

Done when:

- there is one data model for desired manager services;
- the model does not know about heartbeat, TaskMonitor, or autostart specifics;
- tests prove metadata merge and lifecycle validation.

### 3. Refactor TaskMonitor supervision onto the service model

Outcome: TaskMonitor is no longer a one-off manager loop. It becomes one
`ManagedServiceSpec` reconciled by the shared path.

Files to touch:

- `weft/core/manager.py`
- `weft/core/manager_services.py`
- `tests/core/test_manager.py`
- `tests/core/test_manager_services.py` if shared helper behavior changes

Current code to replace or wrap:

- `_build_task_monitor_spawn_payload()`
- `_enqueue_task_monitor_request()`
- `_tick_task_monitor()`
- `_task_monitor_spawn_pending`
- `_task_monitor_next_start_allowed_ns`
- `_task_monitor_restart_backoff_ns`
- `_task_monitor_tid`

Implementation approach:

- Keep a compatibility wrapper temporarily if it reduces churn, but make it
  call the shared service reconciler. The wrapper must not keep separate
  lifecycle state.
- Build a `ManagedServiceSpec` for TaskMonitor when
  `WEFT_TASK_MONITOR_ENABLED` is true.
- The TaskMonitor spawn payload must still use the manager-owned internal
  runtime envelope:
  `INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY =
  INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR`.
- Add service metadata to the child TaskSpec metadata through the shared
  metadata merge helper, not by ad hoc assignment in `_build_task_monitor...`.
- Preserve `internal=True`, `role="task_monitor"`, and `parent_tid`.
- Preserve the initial inbox wake message:
  `{"type": "task_monitor_wakeup", "reason": "manager_supervision"}`.

Tests:

- Red test first: existing `test_manager_enqueues_one_internal_task_monitor_spawn`
  should continue to pass after asserting the generated `taskspec.metadata`
  includes `_weft_service_key` and `_weft_service_lifecycle`, but still does
  not include `INTERNAL_RUNTIME_TASK_CLASS_KEY`. After `_build_child_spec()`
  applies the manager-owned envelope, the resolved child metadata should include
  both the service metadata and the internal runtime task class.
- Add a test that TaskMonitor and heartbeat service specs are both generated
  through the same desired-service collection path when TaskMonitor is enabled.
- Update `test_manager_restarts_dead_task_monitor_after_backoff` to assert the
  shared service state records backoff and clears pending state, rather than
  private TaskMonitor-only fields.

Do not:

- instantiate `TaskMonitorTask` directly inside Manager;
- let Manager scan `weft.log.tasks`;
- create a separate TaskMonitor restart loop.

Done when:

- TaskMonitor launch, pending state, and backoff use the shared service state;
- old TaskMonitor one-off state is removed or reduced to compatibility
  wrappers that delegate to shared state;
- the existing manager TaskMonitor tests pass.

### 4. Move heartbeat startup under manager-owned service reconciliation

Outcome: the heartbeat service is started and kept healthy by the canonical
manager, not by direct endpoint-resolve/direct-spawn helper logic.

Files to touch:

- `weft/core/manager.py`
- `weft/core/heartbeat.py`
- `weft/core/tasks/heartbeat.py` only if endpoint/ownership self-check needs
  to read service metadata
- `weft/core/manager_services.py`
- `tests/core/test_manager.py`
- `tests/core/test_heartbeat_helpers.py`
- `tests/tasks/test_heartbeat.py`

Implementation approach:

- Add a heartbeat `ManagedServiceSpec` when a manager needs heartbeat. For this
  slice, heartbeat should be desired when `WEFT_TASK_MONITOR_ENABLED` is true.
  Do not start it unconditionally forever if no internal service needs it.
- The heartbeat spawn payload must use:
  - `INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY =
    INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT`
  - `INTERNAL_RUNTIME_ENVELOPE_ENDPOINT_NAME_KEY =
    INTERNAL_HEARTBEAT_ENDPOINT_NAME`
  - service key metadata from the shared service helper
  - `persistent=True`
  - `weft_context` from the manager's context
- Change `ensure_heartbeat_service()` so it no longer calls
  `submit_spawn_request()` for heartbeat directly.
- `ensure_heartbeat_service()` may still call `_ensure_manager_running()` and
  wait for a manager-owned live heartbeat endpoint, but it must not treat stale
  endpoint resolution as sufficient. It should use the stricter service
  liveness helper from tasks 5 and 6 before returning a resolved endpoint.
- If no live heartbeat appears before timeout, raise the same kind of
  `RuntimeError` as today. Keep callers' error handling compatible.

Tests:

- Red test first in `tests/core/test_heartbeat_helpers.py`: monkeypatch
  `submit_spawn_request` to raise if called by `ensure_heartbeat_service()`;
  assert the helper does not direct-submit heartbeat anymore.
- Add a manager test that TaskMonitor enabled produces two desired internal
  services in order: heartbeat first, TaskMonitor second.
- Add a manager test that TaskMonitor disabled does not enqueue heartbeat just
  because the manager exists.
- Keep heartbeat runtime tests for upsert/cancel/coalescing. They should not
  need broad changes because `HeartbeatTask` queue behavior is still valid.

Do not:

- make heartbeat registrations persistent;
- use endpoint lookup as the only proof of heartbeat liveness;
- block manager dispatch when heartbeat cannot start.

Done when:

- heartbeat direct spawn is gone from `weft/core/heartbeat.py`;
- heartbeat is represented as a manager desired service;
- tests prove manager-owned heartbeat startup and no direct helper spawn.

### 5. Add shared service candidate discovery and deterministic selection

Outcome: service singleton decisions use one liveness and selection path for
heartbeat, TaskMonitor, and autostarts.

Files to touch:

- `weft/core/manager.py`
- `weft/core/manager_services.py`
- `weft/core/control_probe.py` only if an existing helper needs a small
  generalization. Prefer no change.
- `weft/core/endpoints.py` only if exposing a helper is necessary. Do not
  change public endpoint semantics.
- `weft/helpers/__init__.py` if a small existing liveness helper should be
  reused or exported.
- `tests/core/test_manager_services.py`
- `tests/core/test_manager.py`

Current helpers to reuse:

- `weft.helpers.canonical_owner_tid()`
- `weft.helpers.handle_has_live_host_process()`
- `weft.helpers.pid_matches_create_time()`
- `weft.helpers.iter_queue_json_entries()`
- `weft.core.control_probe.send_keyed_ping_probe()`
- Manager's existing registry ownership methods for the manager itself.

Implementation approach:

- Service candidates should be discovered from:
  - manager-tracked `_child_processes` with service metadata;
  - latest task-log entries whose `taskspec.metadata` contains the same
    service key and whose latest status is non-terminal;
  - latest task-log entries whose `taskspec.metadata.autostart_source` matches
    an autostart service key, for backward compatibility with rows written
    before this service-key metadata existed;
  - latest `weft.state.tid_mappings` for those candidate TIDs.
- Prefer live in-memory child evidence when the child is still tracked by this
  manager.
- Use runtime handle liveness for process-backed candidates. If handle evidence
  is stale or uncertain but `ctrl_in` and `ctrl_out` names are available from
  task log or mapping evidence, use a bounded keyed PING probe as positive
  rescue evidence.
- For internal service candidates, a missing `runtime_handle` is uncertain, not
  live. A candidate with no runtime handle needs in-memory child evidence or a
  matched PONG before it can block replacement.
- Select canonical service candidate by lowest live TID. This matches manager
  election's deterministic rule.
- If no candidate is live but some are uncertain, do not treat uncertainty as
  safe launch authority unless the uncertainty belongs to this manager's own
  tracked child and `_child_has_exited()` has proved it dead. Prefer logging a
  health diagnostic and retrying later over launching duplicates.
- Keep public endpoint `live_candidates` behavior unchanged.

Tests:

- Unit-test pure candidate reduction: lowest live TID wins; terminal candidates
  are ignored; uncertain candidates do not authorize duplicate launch.
- Broker-backed manager test: write a fake older non-terminal service task log
  plus live TID mapping, run service reconciliation, and assert the manager
  does not enqueue a duplicate service.
- Broker-backed manager test: write a stale terminal service task log, run
  reconciliation, and assert the manager may enqueue a replacement.
- PING/PONG test: use real control queues. Write a candidate with stale-looking
  runtime evidence but a live task process or a small real `BaseTask`/manager
  responding to keyed PING; assert matched PONG is positive liveness evidence.
  If this is too slow for a narrow unit test, use a real queue and a tiny
  task object that processes one control message. Do not mock queue reads.

Do not:

- scan entire queues with fixed limits for correctness;
- use public endpoint duplicate counts as the service singleton source of
  truth;
- kill or delete unowned candidate evidence.

Done when:

- one candidate discovery/reduction helper is used by heartbeat, TaskMonitor,
  and autostart reconciliation;
- tests cover live, terminal, uncertain, and PONG-rescued candidates.

### 6. Implement one service reconciliation loop in Manager

Outcome: heartbeat, TaskMonitor, and autostarts all pass through one
reconciler for `once`/`ensure`, pending state, backoff, and enqueue.

Files to touch:

- `weft/core/manager.py`
- `weft/core/manager_services.py`
- `tests/core/test_manager.py`
- `tests/core/test_manager_services.py`

Implementation approach:

- Add a Manager method with a name like `_reconcile_managed_services()`.
- It should return quickly and run only when service reconciliation is allowed:
  - manager is not draining;
  - manager is not stopped;
  - dispatch suspension is absent;
  - `_evaluate_dispatch_ownership().state == "self"`.
- Build desired services in priority order:
  1. heartbeat if needed;
  2. TaskMonitor if enabled;
  3. autostart services from manifest scan.
- For each service:
  - inspect service state;
  - inspect live candidates;
  - skip if canonical live candidate exists;
  - skip if spawn pending exists;
  - apply lifecycle:
    - `once`: skip after launched once unless a live candidate is active before
      the first launch is recorded;
    - `ensure`: restart if no live candidate and backoff permits;
  - enqueue using the manager's own inbox queue and existing spawn envelope
    shape;
  - update launched/pending/restart state only after the enqueue write succeeds.
- Preserve existing autostart enqueue semantics:
  - launch/restart accounting advances only after enqueue success;
  - ensure-mode restarts honor max restarts and backoff;
  - deleted manifest state is pruned.
- Preserve existing TaskMonitor initial wake message and heartbeat dependency.
- Preserve existing manager child launch path. The reconciler writes spawn
  requests to manager inbox; `_handle_work_message()` still resolves and
  launches the child.

Tests:

- Update `test_manager_suspension_skips_autostart_tick_while_dispatch_is_blocked`
  to assert the shared service reconciler does not run when dispatch is blocked.
- Add a test that service reconciliation runs after ownership returns to
  `self`.
- Add a test that heartbeat failure or TaskMonitor enqueue failure does not set
  `manager.should_stop` and does not block an ordinary user spawn request after
  ownership is `self`.
- Add a test that one failed service enqueue does not advance service state.
- Update existing autostart tests to prove behavior is unchanged through the
  shared path.

Do not:

- call `_tick_task_monitor()` and `_tick_autostart()` as independent loops from
  `process_once()` after this task. If compatibility methods remain, they must
  feed the shared path.
- reserve user spawn work while dispatch ownership is not `self`.
- move the service reconciler into the CLI or command layer.

Done when:

- `Manager.process_once()` has one service reconciliation call after child
  cleanup and before idle decisions;
- TaskMonitor, heartbeat, and autostarts share that call;
- old one-off loops are gone or wrappers only.

### 7. Make spawned persistent services able to control their wait interval

Outcome: TaskMonitor can block until control/inbox activity or next due time
instead of relying on the launcher's 50ms loop.

Files to touch:

- `weft/core/launcher.py`
- `weft/core/tasks/base.py` only if adding a generic optional hook there is
  cleaner
- `weft/core/tasks/task_monitor.py`
- `tests/tasks/test_task_monitor.py`
- `tests/core/test_manager.py` or a new integration test if needed

Implementation approach:

- Add a small optional task hook, for example:
  `next_wait_timeout(self) -> float | None`.
- The launcher should call this hook after `process_once()` when present.
  Fallback remains `TASK_PROCESS_POLL_INTERVAL` for existing task types.
- `TaskMonitorTask` should return:
  - `0.0` only when work is immediately pending;
  - seconds until `_next_cycle_due_monotonic` when heartbeat/local interval is
    the next scheduler;
  - a bounded default such as `1.0` when disabled or no due time exists, so
    STOP/parent-loss checks remain responsive.
- `TaskMonitorTask` should also follow the `HeartbeatTask` pattern by checking
  pending `inbox` and `ctrl_in` before sleeping. It must not watch
  `weft.log.tasks`.
- Keep heartbeat registration failure fallback: record `last_error` and use the
  bounded local interval. Do not crash-loop the manager.
- Do not make every task responsible for custom wait intervals. The hook is
  optional and should be used only by persistent reactive services that need it.

Tests:

- Red test first: instantiate `TaskMonitorTask`, set a future due time, and
  assert the new hook returns a wait close to the configured interval, not
  `0.05`.
- Add a launcher unit test with a fake task class that exposes
  `next_wait_timeout()` and records the timeout passed to `wait_for_activity()`.
  This can be a narrow fake because the behavior under test is the launcher
  hook. Keep queue behavior real in TaskMonitor tests.
- Add a TaskMonitor test that pending `ctrl_in` causes immediate return and a
  PING response without waiting for the full interval.
- Add an optional process-level test if practical: launch a real TaskMonitor
  child, wait briefly with no wake messages, and assert it does not produce many
  repeated scan cycles. Avoid brittle CPU assertions in CI; cycle count is a
  better oracle.

Do not:

- replace `MultiQueueWatcher.wait_for_activity()` with ad hoc sleeps;
- make TaskMonitor poll `weft.log.tasks` to decide whether to wake;
- change `TASK_PROCESS_POLL_INTERVAL` globally as the fix.

Done when:

- idle TaskMonitor re-entry is bounded by due time or queue wakeups;
- existing task types keep their current default loop behavior;
- tests prove the launcher honors the hook.

### 8. Tighten heartbeat endpoint helper liveness

Outcome: callers never treat a stale `_weft.heartbeat` endpoint as a live
service just because endpoint resolution returned a row.

Files to touch:

- `weft/core/heartbeat.py`
- `weft/core/manager_services.py`
- `weft/core/endpoints.py` only if adding a non-public helper is unavoidable
- `tests/core/test_heartbeat_helpers.py`
- `tests/core/test_manager.py`

Implementation approach:

- Add an internal helper that validates a resolved heartbeat endpoint against
  service candidate/liveness evidence.
- `ensure_heartbeat_service()` should:
  1. ensure a manager is running;
  2. wait for manager-owned heartbeat service evidence;
  3. resolve `_weft.heartbeat`;
  4. return only if the endpoint's TID matches the live canonical heartbeat
     service candidate;
  5. otherwise keep waiting until timeout.
- If a stale endpoint row is found, the helper may opportunistically prune it
  only through existing endpoint stale-pruning behavior. Do not add destructive
  endpoint cleanup outside exact message IDs.
- Keep `upsert_heartbeat()` payload shape unchanged.

Tests:

- Add a regression that creates a stale `_weft.heartbeat` endpoint row and a
  live newer heartbeat service candidate. Assert `ensure_heartbeat_service()`
  does not return the stale endpoint.
- Add a regression that an endpoint with mismatched service key is not accepted
  for internal heartbeat, even if public endpoint resolution returns it.
- Keep existing timeout test, but update it to reflect manager-owned startup.

Do not:

- change public `queue write --endpoint` semantics;
- make endpoint liveness stricter for all public endpoints in this slice;
- use endpoint liveness as a replacement for service liveness.

Done when:

- `_weft.heartbeat` helper returns only a manager-owned live heartbeat endpoint;
- stale heartbeat endpoint rows no longer capture TaskMonitor registrations.

### 9. Preserve and migrate autostart behavior onto the shared reconciler

Outcome: autostart manifests keep current behavior while sharing lifecycle code
with internal services.

Files to touch:

- `weft/core/manager.py`
- `weft/core/manager_services.py`
- `tests/core/test_manager.py`

Implementation approach:

- Keep manifest enumeration and TaskSpec/pipeline loading in Manager. That code
  is autostart-specific and should not move into the generic service model.
- Convert each valid manifest into a `ManagedServiceSpec` with:
  - key = resolved manifest path;
  - lifecycle = manifest `policy.mode`;
  - spawn payload = existing `_build_autostart_spawn_payload()` output;
  - autostart source = resolved manifest path;
  - existing policy fields stored in service state or a small policy object.
- Replace `_active_autostart_sources()` with shared service candidate discovery
  for service keys, or make it a thin wrapper used only by the shared
  reconciler during migration.
- Preserve existing tests:
  - `test_manager_autostart_templates`;
  - `test_manager_autostart_skips_active_templates`;
  - `test_manager_autostart_active_sources_include_tracked_children`;
  - `test_manager_autostart_prunes_deleted_manifest_state`;
  - `test_manager_autostart_ensure_restarts`;
  - pipeline autostart tests;
  - enqueue failure/backoff tests.

Tests:

- Add one focused test that autostart and TaskMonitor both update the same
  service-state map shape.
- Add one focused test that invalid autostart `policy.mode` is rejected before
  it reaches service reconciliation.
- Existing autostart tests are the primary compatibility proof. Do not replace
  them with mock-only service-model tests.

Do not:

- change manifest schema;
- change pipeline compilation behavior;
- remove existing autostart metadata expected by status/logs.

Done when:

- autostart lifecycle accounting uses the shared service state;
- existing autostart tests pass with minimal assertion updates;
- internal service tests and autostart tests exercise one reconciler.

### 10. Add service health observability

Outcome: operators can tell whether manager-owned services are healthy without
confusing service health with task lifecycle truth.

Files to touch:

- `weft/core/manager.py`
- `weft/core/manager_services.py`
- `weft/core/tasks/task_monitor.py` only if PING payload needs a field update
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `tests/core/test_manager.py`
- `tests/tasks/test_task_monitor.py`

Implementation approach:

- Manager should emit lightweight state events for service reconcile failures,
  such as:
  - `manager_service_start_failed`;
  - `manager_service_duplicate_observed`;
  - `manager_service_waiting_for_liveness`;
  - `manager_service_restart_scheduled`.
- Include fields:
  - `service_key`;
  - `lifecycle`;
  - `candidate_tid` where relevant;
  - `next_allowed_ns` where relevant;
  - `error` where relevant.
- Do not turn service health into task lifecycle state. Service health is
  manager operational observability.
- Consider adding service summary fields to Manager PING/STATUS only if the
  shape is small and stable. If this starts growing, leave it out for this
  slice and rely on task logs plus per-service PING.

Tests:

- Broker-backed manager test: force an enqueue failure for heartbeat or
  TaskMonitor and assert a service-health event is written while manager
  remains non-terminal.
- Existing TaskMonitor PING health fields must still be present.

Do not:

- add new CLI command output unless a spec explicitly requires it;
- treat service health events as cleanup/audit truth.

Done when:

- failures are observable in `weft.log.tasks`;
- no public lifecycle/status behavior is made dependent on service health.

### 11. Runtime validation on ops

Outcome: after release, ops shows the expected phase 7 part 1 state without
cleanup expectations.

Files to touch:

- No code files. This is deployment verification.

Ops checks:

Run from ops:

```bash
ssh ops
cd ~/governance
set -a
. ./.env
. ./.envrc >/dev/null 2>&1 || true
set +a
/opt/venv/bin/weft --version
/opt/venv/bin/weft manager list --json
```

Then verify:

- exactly one active canonical manager for `weft.spawn.requests`;
- heartbeat service is present only when expected by config/dependencies;
- TaskMonitor is present when `WEFT_TASK_MONITOR_ENABLED=1`;
- `_weft.heartbeat` resolves to the live heartbeat service TID, not an old
  failed heartbeat TID;
- TaskMonitor PING reports `role="task_monitor"`,
  `processor`, `interval_seconds`, `last_checkpoint`, and no stale heartbeat
  error after heartbeat has registered;
- TaskMonitor does not consume high CPU while idle. Prefer a short process CPU
  sample over `%CPU` from a single `ps` line;
- queue counts do not need to shrink in this phase. Cleanup is phase 7 part 3.

Do not:

- run destructive queue cleanup;
- kill unrelated interactive user processes;
- interpret a high broker message count as failure for this slice.

Done when:

- manager-owned services are present, non-duplicated, and live;
- stale heartbeat endpoint is not used;
- idle TaskMonitor CPU is low;
- bridge smoke failures already known to be unrelated are recorded separately.

## 8. Testing Plan

Use red-green TDD for each behavioral change:

1. Write the smallest failing test for the contract.
2. Implement only enough code to pass it.
3. Run the narrow test.
4. Expand to adjacent tests.

Test files:

- `tests/core/test_manager_services.py`
  - Pure service model and candidate-reduction behavior.
- `tests/core/test_manager.py`
  - Manager service reconciliation, TaskMonitor/heartbeat/autostart behavior,
    dispatch ownership gating, enqueue failure, backoff.
- `tests/core/test_heartbeat_helpers.py`
  - `ensure_heartbeat_service()` no direct spawn, strict heartbeat endpoint
    validation.
- `tests/tasks/test_task_monitor.py`
  - reactive wait hook, heartbeat failure fallback, PING health, no task-log
    consumption.
- `tests/tasks/test_heartbeat.py`
  - heartbeat task interval emitter behavior and duplicate convergence.
- CLI/harness tests only if a behavior cannot be proven at core level.
  Use `WeftTestHarness`; do not create a custom subprocess harness.

What not to mock:

- Queue writes, reads, moves, and peeks.
- Manager spawn request enqueue semantics.
- Task log and TID mapping history reads.
- Control PING/PONG payload matching.
- Autostart manifest-to-spawn behavior.

Acceptable mocks:

- `time.time_ns()` / `time.monotonic()` for backoff and due-time tests.
- OS process liveness in narrow candidate-reduction unit tests, when a real
  process would make the test slow or nondeterministic.
- Custom TaskMonitor processor callables.
- A fake task object for the launcher wait-hook test, because the hook itself
  is the contract under test.

Key invariants to test:

- Non-leader managers do not start heartbeat, TaskMonitor, or autostarts.
- Positive `self` ownership is required for service launch.
- One service key gets one canonical live candidate.
- Lowest live TID wins among duplicate candidates.
- Stale/terminal service evidence does not block replacement forever.
- Uncertain service evidence does not automatically authorize unsafe duplicate
  launch.
- Heartbeat helper does not write registrations to a stale endpoint.
- TaskMonitor does not scan or consume `weft.log.tasks` unless woken or due.
- TaskMonitor remains non-destructive.
- Service failures do not terminalize the manager.
- Autostart `once` and `ensure` behavior remains compatible.

## 9. Verification and Gates

Per-task commands:

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/core/test_manager_services.py -q
./.venv/bin/python -m pytest tests/core/test_manager.py -q
./.venv/bin/python -m pytest tests/core/test_heartbeat_helpers.py -q
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py -q
./.venv/bin/python -m pytest tests/tasks/test_heartbeat.py -q
```

Expanded local gate before release:

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/core/test_manager_services.py tests/core/test_manager.py tests/core/test_heartbeat_helpers.py tests/tasks/test_task_monitor.py tests/tasks/test_heartbeat.py -q
./.venv/bin/python -m pytest tests/cli/test_cli_run.py tests/commands/test_run.py -q
./.venv/bin/ruff format --check weft tests
./.venv/bin/ruff check weft tests
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
```

Final release gate:

```bash
. ./.envrc
./.venv/bin/python -m pytest
./.venv/bin/ruff format --check weft tests
./.venv/bin/ruff check weft tests
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
```

If full-suite runtime is too high for local iteration, run the targeted gate
first and require full CI before release.

Post-deploy ops gate:

- `weft --version` shows the released version.
- `weft manager list --json` shows one active canonical manager.
- heartbeat endpoint TID matches a live heartbeat service, not a stale failed
  task.
- TaskMonitor PING succeeds and shows fresh health fields.
- TaskMonitor idle CPU is low by a short `/proc/<pid>/stat` sample or
  equivalent.
- `weft.spawn.requests` is not accumulating internal duplicate service spawns.

## 10. Rollout and Rollback

Rollout:

- Ship as one independent Weft release before phase 7 cleanup parts 2 and 3.
- Deploy to ops.
- Verify service liveness and TaskMonitor CPU before enabling any cleanup
  behavior.
- Do not interpret unchanged queue counts as failure; cleanup is out of scope.

Backward compatibility:

- Existing queue records remain readable.
- Existing heartbeat endpoint rows may be stale; new code must ignore stale
  internal heartbeat endpoints rather than require manual deletion.
- Existing autostart manifests remain valid.
- Existing non-terminal autostart task-log rows that only carry
  `autostart_source` and do not yet carry `_weft_service_key` remain visible as
  candidates for the corresponding manifest service key.
- Existing TaskMonitor PING fields remain valid.
- Existing public endpoint behavior remains valid.

Rollback:

- Revert the release if service reconciliation causes manager dispatch
  regressions.
- Rollback does not require queue migration because new service metadata is
  additive task metadata.
- Stale service metadata in task logs is harmless after rollback.
- If rollback leaves duplicate internal services running, stop them using
  existing task control or manager restart, not queue deletion.

One-way doors:

- None intended. This plan must not include destructive cleanup, schema
  migration, or incompatible queue payload changes.

## 11. Out of Scope

- Phase 7 part 2 or part 3 cleanup behavior.
- Deleting or archiving broker messages.
- Changing public endpoint semantics.
- New CLI surfaces for service management.
- New dependencies.
- Replacing SimpleBroker activity waiters.
- Changing autostart manifest schema.
- Changing manager election away from lowest live TID.
- Making heartbeat a general scheduler.
- Making service health an audit record or lifecycle authority.

## 12. Independent Review Loop

This is risky, boundary-crossing work. It touches Manager, internal tasks,
heartbeat, autostart, endpoint discovery, and spawned process wait behavior.

Reviewer bootstrap:

- Prefer a different agent family if available, following
  `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`.
- If no other reviewer is available, run a same-family fresh review and record
  that limitation in the implementation notes.

Review prompt:

> Read the plan at
> `docs/plans/2026-05-08-manager-owned-internal-service-supervision-plan.md`.
> Carefully examine the plan and the associated code. Look for errors, bad
> ideas, and latent ambiguities. Do not implement anything. Could you implement
> this confidently and correctly if asked?

Reviewer should read:

- this plan;
- `docs/specifications/01-Core_Components.md` [CC-2.3], [CC-2.4.1];
- `docs/specifications/03-Manager_Architecture.md` [MA-1.4], [MA-1.6],
  [MA-1.6a];
- `docs/specifications/05-Message_Flow_and_State.md` [MF-3], [MF-3.1],
  [MF-3.2], [MF-5];
- `weft/core/manager.py`;
- `weft/core/heartbeat.py`;
- `weft/core/tasks/task_monitor.py`;
- `weft/core/tasks/heartbeat.py`;
- `weft/core/launcher.py`;
- `tests/core/test_manager.py`;
- `tests/tasks/test_task_monitor.py`.

Author response:

- Address every finding explicitly.
- Update the plan for accepted findings.
- If rejecting a finding, document why the current path is still the best
  choice.
- If review says the plan cannot be implemented confidently, treat that as a
  blocker.

## 13. Fresh-Eyes Review Notes

Self-review pass 1:

- Ambiguity found: "manager does not do anything until it knows it is the only
  manager" could be implemented as literal process-count one. Fix: the plan now
  defines the condition as positive canonical dispatch ownership
  (`state == "self"`), while allowing non-leader managers to drain/control/exit.
- Ambiguity found: "same code path as manager election" could mean rewriting
  manager election. Fix: the plan says to reuse deterministic lowest-live-TID
  reduction and keyed PONG liveness concepts for service candidates, not replace
  manager election.
- Scope risk found: strict service liveness could accidentally tighten public
  endpoint semantics. Fix: public endpoints are explicitly out of scope and
  endpoint discovery remains discovery only.
- Scope risk found: moving autostart into the shared path could change manifest
  format. Fix: manifest parsing and schema stay unchanged; only desired-service
  reconciliation is shared.
- Test risk found: CPU assertions are brittle. Fix: tests should assert wait
  hook and cycle count behavior; ops can use CPU sampling after deploy.

Self-review pass 2:

- Hidden coupling found: `ensure_heartbeat_service()` still needs a way to make
  a manager run before heartbeat exists. Fix: it may still call
  `_ensure_manager_running()`, but must not direct-submit heartbeat. It waits
  for manager-owned heartbeat evidence and times out if missing.
- Hidden coupling found: heartbeat should not run forever without users. Fix:
  heartbeat is desired when TaskMonitor/internal services need it for this
  slice, not unconditionally.
- Hidden coupling found: TaskMonitor's bounded local fallback could be
  misread as a busy loop. Fix: the launcher wait hook and TaskMonitor due-time
  hook are required; fallback means bounded interval waits, not 50ms polling.
- Hidden coupling found: service candidate discovery might want to delete stale
  evidence. Fix: stale evidence can be ignored or pruned only through existing
  exact-message runtime-state mechanisms; no cleanup behavior is added here.
- Backward-compatibility gap found: existing autostart rows do not have the new
  service key metadata. Fix: candidate discovery must treat matching
  `autostart_source` as a legacy service-key proof for autostart services.
- Liveness ambiguity found: endpoint resolution currently treats some records
  without runtime handles as live enough. Fix: internal service candidates with
  missing runtime handles are uncertain unless in-memory child evidence or a
  matched PONG proves liveness.

This plan still matches the discussed direction: Manager is the internal
service owner, heartbeat and TaskMonitor use one singleton contract, autostart
uses the same `once`/`ensure` path, Manager keeps working when services fail,
and TaskMonitor becomes reactive instead of CPU-hot while idle.

# Phase 7 Manager Service Reconciler Cleanup Plan

Status: draft
Source specs: see Source Documents below
Superseded by: [`2026-05-08-deterministic-manager-service-reconciler-plan.md`](./2026-05-08-deterministic-manager-service-reconciler-plan.md)

## 1. Goal

Finish the phase 7 manager-owned service cleanup by turning heartbeat,
TaskMonitor, and autostart lifecycle handling into one manager-owned
reconciliation path. This plan addresses the engineering-review findings from
`2026-05-08-manager-owned-internal-service-supervision-plan.md` and the
Postgres/full-suite failure where `test_manager_autostart_ensure_applies_backoff_to_restart_only`
did not observe the expected ensure-mode restart after the first child
completed.

This is not phase 7 part 3. It must not add autonomous queue deletion. It is a
correctness and architecture cleanup before destructive cleanup is considered.

## 2. Source Documents

Source specs:

- `docs/specifications/01-Core_Components.md` [CC-2.2], [CC-2.3], [CC-2.4],
  [CC-2.5]
- `docs/specifications/03-Manager_Architecture.md` [MA-0], [MA-1], [MA-1.4],
  [MA-1.6], [MA-1.6a], [MA-1.7], [MA-3]
- `docs/specifications/05-Message_Flow_and_State.md` [MF-1], [MF-2],
  [MF-3.1], [MF-3.2], [MF-5], [MF-6]
- `docs/specifications/07-System_Invariants.md` [STATE.1]-[STATE.6],
  [QUEUE.1]-[QUEUE.6], [OBS.9], [OBS.12], [OBS.13],
  [MANAGER.8]-[MANAGER.14], [IMPL.5]-[IMPL.7]

Active plans and prior implementation context:

- `docs/plans/2026-05-07-phase-7-task-monitor-supervision-and-cleanup-plan.md`
  is still the governing phase 7 plan. This cleanup plan narrows the next slice
  to manager-owned service reconciliation and does not supersede phase 7 parts
  2 or 3.
- `docs/plans/2026-05-08-manager-owned-internal-service-supervision-plan.md`
  captured the intended single-path architecture. This cleanup plan supersedes
  that plan for all remaining heartbeat, TaskMonitor, and autostart
  single-reconciler implementation work. Keep the older plan as background
  context only; if the two plans disagree, implement this cleanup plan.
- `docs/plans/2026-04-16-autostart-hardening-and-contract-alignment-plan.md`
  defines current autostart `once` and `ensure` behavior. This cleanup must
  preserve manifest format and user-visible autostart behavior.
- `docs/plans/2026-04-17-heartbeat-service-plan.md` defines the heartbeat task's
  queue contract and coalescing behavior. This cleanup may change startup and
  liveness validation, not heartbeat message shape.
- `docs/plans/2026-05-07-manager-selection-ping-pong-liveness-plan.md` defines
  keyed PING/PONG liveness. Reuse that control-probe format.

Required repo guidance:

- `AGENTS.md`
- `docs/agent-context/decision-hierarchy.md`
- `docs/agent-context/principles.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/runtime-and-context-patterns.md`
- `docs/agent-context/runbooks/testing-patterns.md`
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`
- `docs/lessons.md`

Spec maintenance required before implementation is done:

- Add this plan as a related plan in the touched spec files.
- Keep [MA-1.6a] aligned with the final owner of the service reconciler.
- Keep [MF-3.2] aligned with heartbeat startup and endpoint liveness.
- Keep [MF-5] aligned with the non-destructive TaskMonitor contract.
- Keep [OBS.13] aligned with operational monitor output and the fact that
  queues are not audit evidence.

## 3. Current Context and Failure Evidence

Current implementation shape:

- `weft/core/manager_services.py` contains shared service dataclasses and pure
  metadata helpers, but most evidence collection still lives in
  `weft/core/manager.py`.
- `Manager.process_once()` still calls two lifecycle loops:

```text
Manager.process_once()
  ...
  _cleanup_children()
  _tick_internal_services()
  _tick_autostart()
  _update_idle_activity_from_broker()
```

- `_tick_internal_services()` uses `_tick_managed_service()` for heartbeat and
  TaskMonitor.
- `_tick_autostart()` still owns manifest scan, active-source discovery,
  restart/backoff accounting, and enqueue.
- `_active_autostart_sources()` independently scans `weft.log.tasks` and uses
  `autostart_source` status evidence instead of the shared service candidate
  model.
- `weft/core/heartbeat.py` has its own endpoint liveness check and does not
  validate the endpoint TID against the shared canonical service candidate.
- `Manager._service_candidate_from_task_log()` has a hard-coded PING timeout
  (`0.15`) instead of a constant.

Review findings to address:

1. Autostart is not actually under one reconciler yet.
2. Heartbeat is started unconditionally even when no enabled internal service
   needs it.
3. Heartbeat endpoint liveness is parallel logic, not the shared service
   liveness contract.
4. The manager service candidate path hard-codes a PING timeout.
5. Tests do not prove the one-reconciler architecture.
6. Service candidate discovery can scan large histories in repeated per-service
   passes, which is dangerous on ops-sized queues.
7. Spawn-pending state is crash-unsafe if it exists only in manager memory.
8. `uncertain` candidates can hang service recovery unless the plan defines a
   convergence policy.
9. Reserved service metadata can be spoofed by ordinary user tasks unless the
   manager-owned trust boundary is explicit.
10. "Latest task-log state" is ambiguous unless it means latest status per TID.
11. Autostart service keys need one normalization function.
12. Heartbeat liveness tests need at least one real PING/PONG proof.
13. Multi-manager churn needs a broker-backed duplicate-launch regression.

Observed test failure to address:

```text
tests/core/test_manager.py::test_manager_autostart_ensure_applies_backoff_to_restart_only
expected one restart task_spawned event after the first ensure-mode child
completed and the configured 0.5s restart backoff elapsed; observed zero.
```

The exact CI failure was in a Postgres-backed full-suite run on macOS with
xdist. A targeted local run in the current workspace passed, so treat this as a
timing/backend/full-suite regression class rather than a locally deterministic
single-test failure. The likely root class is the split autostart lifecycle:
completed ensure-mode work is still reconciled through legacy active-source
and backoff state rather than the same candidate model used by manager-owned
services. Do not "fix" this by increasing the test timeout or weakening the
assertion.

## 4. Target Architecture

The manager should have one service reconciliation call per turn:

```text
Manager.process_once()
  refresh manager registration
  handle drain/yield/control/dispatch fencing
  run normal Manager child-spawn turn
  cleanup exited children
  _reconcile_managed_services()
    build desired services
      heartbeat       only when an enabled internal dependent needs it
      task_monitor    when WEFT_TASK_MONITOR_ENABLED is true
      autostarts      when WEFT_AUTOSTART_TASKS is true
    collect service evidence once for all desired keys
      tracked children
      latest task-log state
      legacy autostart_source rows
      latest tid mappings
      keyed PING/PONG rescue
      manager-local terminal tids
    reconcile each desired service
      live canonical candidate exists -> do not enqueue
      spawn pending -> do not enqueue
      once and already launched -> do not enqueue
      ensure and backoff not elapsed -> do not enqueue
      otherwise enqueue through manager inbox
  update idle activity
```

Service ownership rule:

- Lowest live TID wins for a service key.
- Terminal candidates do not block replacement.
- A manager-local terminal child for a service key overrides stale non-terminal
  task-log evidence for that same TID.
- For task-log evidence, only the latest observed lifecycle status per TID
  counts. Earlier `running` rows for that TID must be ignored after a later
  terminal row.
- A non-terminal task-log row without live runtime evidence or matched PONG is
  stale for replacement purposes when the probe succeeded and simply found no
  responder. Absence of PONG after a successful probe is not the same as a
  broker/probe error.
- Reserve `uncertain` for cases where a backend/probe failure means duplicate
  launch risk is real. Uncertainty has to converge: track uncertainty attempts
  or first-seen time per service key, retry with bounded backoff, and after the
  configured window either reclassify the candidate stale after repeated
  successful no-PONG probes or explicitly degrade the service as unavailable
  while continuing to retry. Do not let `uncertain` become an infinite silent
  block.
- Spawn pending means either manager-local pending state or an unconsumed
  `weft.spawn.requests` message whose spawn envelope carries matching service
  metadata. In-memory state alone is not enough because a manager can die after
  enqueue and before recording local state.
- Internal service candidates are trusted only when the evidence shows a
  manager-owned internal runtime, not merely because a task log contains
  `_weft_service_key`. Ordinary user tasks may not squat reserved service keys
  or be stopped as duplicate internal services.
- Autostart candidates are trusted only for currently desired manifest service
  keys and must have autostart metadata compatible with manager-created
  autostart work. A user task with a forged `autostart_source` must not block a
  real manifest service.

Layering rule:

- `weft/core/*` must not import `weft.commands.*`.
- Public endpoint discovery remains endpoint discovery. It is not the internal
  service election authority.
- Manifest parsing stays in `Manager`; generic service code should not learn
  autostart manifest schema.

## 5. Invariants and Constraints

Preserve:

- TID format and immutability.
- Forward-only task state transitions.
- The existing `TaskSpec -> Manager -> Consumer/Pipeline -> queues/state log`
  execution spine.
- Manager ownership fencing: non-leader managers must not launch user work,
  heartbeat, TaskMonitor, or autostarts.
- Autostart manifest schema and public behavior for `once` and `ensure`.
- Autostart service keys are produced by one helper from the manifest path. Use
  `Path.resolve(strict=False)` and string conversion consistently for scan,
  state, metadata, and tests. Do not casefold paths on POSIX/macOS; case
  semantics are filesystem-dependent and Python cannot safely infer them.
- Heartbeat upsert/cancel request payload shape.
- TaskMonitor remains non-destructive in this slice.
- Queue-history reads use generator helpers, not fixed-limit correctness reads.
- Runtime-only `weft.state.*` queues remain runtime-only.
- No command-layer imports from `weft/core/*`.

Do not add:

- new dependencies;
- new queue names unless a spec change is approved first;
- a second service scheduler;
- destructive queue cleanup;
- persistent heartbeat registrations;
- public endpoint semantic tightening outside `_weft.heartbeat` helper
  validation.

Stop and re-plan if:

- the implementation wants to keep `_tick_autostart()` as a separate lifecycle
  loop from `Manager.process_once()`;
- a test mocks away broker queues or child process lifecycle for the
  autostart restart proof;
- service evidence is trusted from reserved metadata without proving the task
  was manager-owned;
- the implementation wants to scan full `weft.log.tasks` on every manager turn;
- heartbeat standalone public demand becomes a requirement. Today only
  TaskMonitor calls `upsert_heartbeat()` in production code. A standalone
  heartbeat API would need a separate manager-owned demand mechanism instead
  of direct spawning.

## 6. Bite-Sized Tasks

### 1. Lock the red cases and review gaps before refactoring

Outcome: tests fail for the right reasons before production code changes.

Files to touch:

- `tests/core/test_manager.py`
- `tests/core/test_heartbeat_helpers.py`
- `tests/core/test_manager_services.py` if adding pure service tests
- `tests/specs/test_plan_metadata.py` only if plan metadata rules change
  unexpectedly. Do not change it for this slice.

Read first:

- Existing tests near `test_manager_autostart_ensure_applies_backoff_to_restart_only`
- Existing TaskMonitor/heartbeat tests around manager-owned service startup
- `docs/agent-context/runbooks/testing-patterns.md`

Required test work:

- Keep `test_manager_autostart_ensure_applies_backoff_to_restart_only` as a
  release gate. Do not increase its timeout as the fix.
- Add a focused regression that simulates the stale evidence class:
  1. write an ensure-mode autostart manifest;
  2. launch once through the manager;
  3. mark the child locally terminal through real manager cleanup;
  4. write task-log history for the same child TID as `running` then
     `completed`;
  5. assert the latest terminal status wins and the manager can enqueue exactly
     one restart after backoff.
- Add a spawn-pending crash-safety regression:
  1. write an unconsumed `weft.spawn.requests` message with service metadata;
  2. construct a fresh Manager instance with empty in-memory service state;
  3. run service reconciliation;
  4. assert it does not enqueue a duplicate service request.
- Add a reserved-metadata squatting regression:
  1. write an ordinary user task log containing `_weft_service_key` for
     heartbeat or TaskMonitor but without manager-owned internal runtime
     evidence;
  2. assert the candidate is ignored and cannot block the real internal service.
- Add a broker-backed two-manager regression that runs two manager instances
  against the same queues and proves only the elected owner enqueues managed
  services during ownership churn.
- Add a manager test that TaskMonitor disabled does not enqueue heartbeat
  during ordinary manager startup.
- Add or update a test proving that TaskMonitor enabled makes heartbeat desired
  before TaskMonitor.
- Add a heartbeat helper test with real queues that rejects a stale
  `_weft.heartbeat` endpoint if its TID is not the canonical live heartbeat
  service candidate.
- Add a real PING/PONG heartbeat liveness test where the accepted candidate
  actually responds to keyed PING. Keep pure unit tests for reduction logic, but
  do not let a fake "live" candidate be the only proof.
- Add an autostart path-normalization regression using two equivalent manifest
  paths, such as a symlinked directory and its resolved target, and assert both
  normalize to the same service key.

Testing guidance:

- Use `broker_env` and real queues for manager/autostart behavior.
- Use real manager child cleanup for the autostart regression where practical.
- Mock only control-probe latency if a real PING/PONG would make a narrow unit
  test too slow. Prefer real control queues in at least one integration test.

Done when:

- the new tests fail against the current code for the expected reason;
- existing unrelated tests are not weakened or skipped.

### 2. Move service evidence indexing into one reusable core path

Outcome: Manager, autostart, and heartbeat helper use the same candidate
classification and canonical live reduction.

Files to touch:

- `weft/core/manager_services.py`
- `weft/core/manager.py`
- `weft/core/heartbeat.py`
- `weft/_constants.py`
- `tests/core/test_manager_services.py`
- `tests/core/test_manager.py`
- `tests/core/test_heartbeat_helpers.py`

Read first:

- `weft/core/manager_services.py`
- `weft/core/control_probe.py`
- `weft/helpers.py` helpers `iter_queue_json_entries`,
  `handle_has_live_host_process`, and related PID liveness helpers
- `weft/core/heartbeat.py` endpoint validation

Implementation approach:

- Keep `ManagedServiceSpec`, `ManagedServiceState`, `ServiceCandidate`, and
  `select_canonical_live_candidate()`.
- Add a reusable evidence/index helper that can operate on real queue-history
  iterators and a set of desired service keys.
- The helper must build latest lifecycle evidence per TID. Earlier non-terminal
  rows for a TID are ignored once a later terminal row exists.
- The helper should scan `weft.log.tasks` at most once per external-evidence
  reconciliation pass for all desired keys, not once per service key.
- The helper should scan `weft.state.tid_mappings` at most once for the
  candidate TID set, not once per candidate.
- Add a pending-spawn reader that peeks or iterates `weft.spawn.requests`
  without consuming work and returns service keys for unconsumed manager-owned
  spawn envelopes. This must use the same service metadata validation as task
  logs.
- Add trust-boundary helpers:
  - internal service metadata is valid only with manager-owned internal runtime
    evidence for the expected service;
  - autostart metadata is valid only for currently desired manifest service
    keys and manager-created autostart metadata;
  - unknown or spoofed reserved metadata is ignored for service ownership.
- Add uncertainty tracking fields to `ManagedServiceState` or a small adjacent
  state object. Track enough information to avoid infinite uncertainty:
  `uncertain_since_ns`, `uncertain_attempts`, and last reason are sufficient.
- Preserve legacy autostart compatibility by treating
  `metadata.autostart_source == service_key` as service-key evidence when the
  reserved service key is absent and the service key is one of the currently
  desired manifest paths.
- Move the service PING timeout into `weft/_constants.py`, for example
  `MANAGED_SERVICE_PING_TIMEOUT`.
- Add constants for uncertainty and external-evidence scan cadence rather than
  hard-coding them in Manager.

Do not:

- invent a broad queue factory abstraction;
- move autostart manifest parsing into `manager_services.py`;
- change endpoint registry schema;
- classify stale non-terminal task-log rows as live without runtime/PONG proof.

Tests:

- Pure candidate tests:
  - lowest live TID wins;
  - terminal candidates are ignored;
  - manager-local terminal evidence overrides stale non-terminal log evidence
    for the same TID;
  - latest per-TID status wins when history contains `running` then
    `completed`;
  - legacy `autostart_source` rows map to the service key;
  - non-terminal log without runtime/PONG proof does not block replacement.
  - spoofed internal service metadata on an ordinary user task is ignored.
  - uncertainty does not block forever: successful no-PONG probes converge to
    stale, while probe/backend errors produce a visible degraded health state.
- Broker-backed test:
  - write multiple service keys into `weft.log.tasks` and prove the manager
    sees them through the shared index, not a per-service active-source scan.
  - write an unconsumed service spawn request and prove the next manager treats
    it as pending.

Done when:

- `weft/core/heartbeat.py` and `weft/core/manager.py` both depend on the shared
  service candidate classification or its pure reduction helpers;
- the hard-coded `0.15` timeout is gone.

### 3. Convert autostart to desired managed services

Outcome: autostart `once` and `ensure` go through the same lifecycle state and
enqueue path as heartbeat and TaskMonitor.

Files to touch:

- `weft/core/manager.py`
- `weft/core/manager_services.py`
- `tests/core/test_manager.py`

Read first:

- `Manager._tick_autostart`
- `Manager._build_autostart_spawn_payload`
- `Manager._enqueue_autostart_request`
- `Manager._active_autostart_sources`
- `write_autostart_fixture` and `write_autostart_pipeline_fixture` in
  `tests/core/test_manager.py`

Implementation approach:

- Keep manifest enumeration and manifest validation in `Manager`.
- Add one helper such as `_autostart_service_key(path: Path) -> str` and use it
  everywhere autostart service keys are created, compared, logged, or stored.
  It should use `Path.resolve(strict=False)` and must not casefold.
- Add a method such as `_desired_autostart_services()` that returns
  `ManagedServiceSpec` values plus any autostart policy state required for
  backoff and max-restart enforcement.
- Represent each manifest service key as the normalized manifest path returned
  by the helper above.
- Keep existing metadata:
  - `autostart_source`;
  - `autostart=True`;
  - pipeline/task metadata expected by status and logs.
- Replace `_active_autostart_sources()` with shared service candidate
  discovery, or reduce it to a thin compatibility wrapper used only by the
  shared reconciler.
- Make ensure-mode restart decisions from service state plus shared live
  candidates:
  - no live candidate and backoff elapsed -> enqueue restart;
  - live candidate -> no restart;
  - matching pending spawn request -> no restart;
  - max restarts reached -> no restart;
  - enqueue failure -> do not advance launched/restart/backoff state.

Backoff rule:

- The first ensure launch sets `launched_once=True` but does not increment
  `restarts`.
- A later launch after the first completed child increments `restarts`.
- Backoff applies to restarts, not to preventing the initial launch.
- The existing test `test_manager_autostart_ensure_applies_backoff_to_restart_only`
  is the release-gate proof.

Do not:

- change manifest schema;
- change pipeline compilation;
- change child TaskSpec resolution;
- patch over the failure by sleeping longer.

Done when:

- all existing autostart tests pass;
- the Postgres/full-suite failing test has a deterministic root fix rather
  than a timeout increase.

### 4. Collapse Manager to one reconciliation call

Outcome: `Manager.process_once()` has one service reconciliation call that owns
heartbeat, TaskMonitor, and autostarts.

Files to touch:

- `weft/core/manager.py`
- `tests/core/test_manager.py`

Implementation approach:

- Add `_reconcile_managed_services(force: bool = False)`.
- It should return immediately unless service supervision is allowed:
  - manager inbox is `weft.spawn.requests`;
  - manager is not draining;
  - manager is not stopped;
  - no dispatch suspension is active;
  - `_evaluate_dispatch_ownership().state == "self"`.
- Build desired services in priority order:
  1. heartbeat, only when an enabled internal dependent needs it;
  2. TaskMonitor, when enabled;
  3. autostart services, when enabled and scan interval/backoff permits.
- Call the shared evidence index once for the desired keys that actually need
  external evidence.
- Do not call external-history scans every manager turn. First check live
  tracked children and manager-local state. Use a manager-local high-water mark
  plus a scan interval for external task-log/tid-mapping evidence. A full replay
  is allowed on first use or after manager restart, but steady-state turns must
  be incremental or skipped.
- Reconcile each service through one helper, not separate loops.
- Change `Manager.process_once()` to call only `_reconcile_managed_services()`
  after `_cleanup_children()`.
- Keep `_tick_task_monitor()` and `_tick_autostart()` only as thin wrappers if
  too many tests still call them during migration. Prefer updating tests to the
  new method. Wrappers must call the shared path.

Tests:

- Update the dispatch-suspension test to assert the shared reconciler does not
  run when dispatch is blocked.
- Add a test that heartbeat enqueue failure does not set `manager.should_stop`
  and does not block user work.
- Add a test that one failed service enqueue does not advance service state.
- Add a structural regression that `process_once()` invokes one reconciliation
  seam, not separate internal/autostart tick seams. Keep this narrow and pair
  it with behavior tests so it does not become the only proof.
- Add a two-manager regression against real broker queues: both managers process
  turns, only the elected owner enqueues heartbeat/TaskMonitor/autostart
  services, and the non-owner does not launch a duplicate during ownership
  churn.

Done when:

- `_tick_internal_services()` and `_tick_autostart()` are no longer independent
  `process_once()` paths;
- TaskMonitor, heartbeat, and autostart tests exercise the same reconciler.

### 5. Tighten heartbeat endpoint validation through shared liveness

Outcome: `ensure_heartbeat_service()` never returns a stale `_weft.heartbeat`
endpoint that is not backed by the canonical live heartbeat service candidate.

Files to touch:

- `weft/core/heartbeat.py`
- `weft/core/manager_services.py`
- `tests/core/test_heartbeat_helpers.py`
- `tests/core/test_manager.py`

Implementation approach:

- Keep `ensure_heartbeat_service()` allowed to call `_ensure_manager_running()`.
- Keep direct `submit_spawn_request()` out of heartbeat helper startup.
- Resolve `_weft.heartbeat` only as the write address.
- Validate that the resolved endpoint TID matches the canonical live heartbeat
  service candidate from the shared liveness helper.
- If the endpoint is stale or mismatched, keep waiting until timeout.
- If no live heartbeat appears before timeout, keep the existing `RuntimeError`
  style.

Important boundary:

- In this cleanup slice, heartbeat is manager-started because TaskMonitor is an
  enabled internal dependent. Do not add a standalone public heartbeat demand
  protocol unless the product contract changes. If a future caller needs
  `upsert_heartbeat()` while TaskMonitor is disabled, plan that separately as a
  manager-owned "service desired" request, not a return to direct spawning.

Tests:

- Real-queue stale endpoint regression:
  - write a stale endpoint row;
  - write or launch a live heartbeat service candidate with a different TID that
    can satisfy keyed PING, or provide runtime-handle liveness through the
    shared helper;
  - assert `ensure_heartbeat_service()` does not return the stale endpoint.
- Add the positive counterpart: `ensure_heartbeat_service()` returns the
  endpoint only when the endpoint TID matches the canonical live heartbeat
  candidate.
- Existing helper tests should keep proving no direct spawn.
- Update tests that currently expect heartbeat startup with TaskMonitor disabled.

Done when:

- stale heartbeat endpoint rows cannot capture TaskMonitor registrations;
- public endpoint resolution behavior for non-heartbeat endpoints is unchanged.

### 6. Bound the ops-scale service evidence cost

Outcome: service reconciliation does not repeatedly scan large queues in
per-service loops or every idle manager turn.

Files to touch:

- `weft/core/manager.py`
- `weft/core/manager_services.py`
- `tests/core/test_manager_services.py`
- `tests/core/test_manager.py`

Implementation approach:

- Short-circuit services with live tracked children before reading large
  histories.
- When external history is needed, scan `weft.log.tasks` once for all needed
  service keys and scan `weft.state.tid_mappings` once for all candidate TIDs.
- Maintain manager-local high-water marks for the task-log and tid-mapping
  evidence used by service reconciliation. On steady-state turns, process only
  new rows or skip the external scan until the service-evidence scan interval
  expires.
- External scans are needed when a desired service has no tracked live child, no
  pending spawn request, and no recent positive liveness. They are not needed
  on every turn for a healthy tracked child.
- The high-water/cache is manager-local acceleration only. It must be
  invalidated or bypassed after child cleanup, enqueue, manager restart, and
  force reconciliation, and it must never be treated as more authoritative than
  fresh queue evidence.

Tests:

- Add a small instrumentation test around the pure helper or manager seam that
  proves one task-log pass can serve multiple service keys.
- Add a steady-state manager test showing a healthy tracked child does not
  trigger a full task-log replay every `process_once()`.
- Do not write a brittle CPU test.
- Do not add a huge fixture that slows the suite materially. A small fake
  iterable with counters is acceptable for the pure indexing helper; broker
  behavior still needs real queues elsewhere.

Post-deploy observation:

- On ops, confirm manager process CPU stays idle when no work is in flight.
- Confirm `weft status --json` and manager turns do not regress against the
  large existing task log before phase 7 part 3 cleanup lands.

### 7. Sync docs and traceability

Outcome: specs, plans, and code point to the same owner and behavior.

Files to touch:

- `docs/specifications/01-Core_Components.md`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- module docstrings in touched `weft/core/*` files if ownership changes
- `docs/plans/README.md`

Required updates:

- Add this plan to the relevant spec `Related Plans` sections.
- Update implementation mappings if the service reconciler function name or
  owner changes.
- Keep the older manager-owned service plan indexed as draft until the cleanup
  is implemented or explicitly superseded. For this cleanup slice, update its
  metadata to point at this plan as the superseding plan for remaining
  single-reconciler work.
- Do not mark this cleanup plan completed until tests and spec sync are done.

## 7. Testing Plan

Use the in-repo virtualenv after sourcing local environment:

```bash
. ./.envrc 2>/dev/null || true
```

Targeted red/green tests while implementing:

```bash
./.venv/bin/python -m pytest tests/core/test_manager.py::test_manager_autostart_ensure_applies_backoff_to_restart_only -q
./.venv/bin/python -m pytest tests/core/test_manager.py -k "autostart or managed_service or task_monitor or heartbeat" -q
./.venv/bin/python -m pytest tests/core/test_heartbeat_helpers.py tests/tasks/test_heartbeat.py tests/tasks/test_task_monitor.py -q
./.venv/bin/python -m pytest tests/core/test_manager_services.py -q
```

Plan/docs hygiene:

```bash
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
```

Final gates before claiming done:

```bash
./.venv/bin/ruff format --check weft tests
./.venv/bin/ruff check weft tests
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/python -m pytest tests/core/test_manager.py tests/core/test_heartbeat_helpers.py tests/tasks/test_heartbeat.py tests/tasks/test_task_monitor.py tests/specs/test_plan_metadata.py -q
```

Release-candidate gate:

- Run the full suite, including the Postgres-backed path that produced the
  failure above. If the release process runs full CI, do not retag until the
  failure is green there.
- Run or inspect a Postgres-backed full-suite shard for the specific autostart
  backoff regression. A local SQLite or single-test pass is not enough evidence
  for this failure class.

Test design rules:

- Broker queues and manager child lifecycle stay real for autostart restart
  tests.
- Pure candidate reduction can use small in-memory inputs.
- PING/PONG tests should use real control queues in at least one test.
- Do not rely on sleeps as the proof of backoff correctness when a deterministic
  clock seam already exists or can be localized.
- Do not let a structural "one reconciler was called" test replace
  broker-visible behavior tests for duplicate prevention, pending spawn, and
  autostart restart.

## 8. Rollout and Rollback

Rollout:

1. Ship the cleanup release with no destructive queue cleanup enabled.
2. Deploy on ops.
3. Confirm one active canonical manager.
4. Confirm at most one live TaskMonitor when enabled.
5. Confirm at most one live heartbeat service when TaskMonitor is enabled.
6. Confirm heartbeat is not churned when TaskMonitor is disabled.
7. Run an ensure-mode autostart smoke or inspect recent autostart events to
   confirm first launch and one policy-allowed restart behave correctly.
8. Confirm manager CPU is idle when no work is active.
9. Confirm service evidence scans are not replaying the whole task log on every
   idle manager turn.

Rollback:

- This slice preserves queue names, manifest schema, heartbeat payload shape,
  and task-local queues. A code rollback should be safe because the new service
  metadata is additive and legacy `autostart_source` remains readable.
- If rollback is needed, do not delete service metadata rows or endpoint rows by
  hand. Let the older code ignore unknown metadata and continue.

One-way doors:

- None in this slice. Autonomous deletion remains out of scope.

Rollback note for the existing TaskMonitor wait hook:

- The launcher `next_wait_timeout()` hook was part of the prior TaskMonitor CPU
  fix and is not a target of this cleanup. If that hook regresses while this
  slice is being implemented, revert or repair it independently instead of
  mixing launcher-loop redesign into the service reconciler change.

## 9. Independent Review Loop

Run an independent review after the plan is written and before implementation
starts. Preferred reviewer stance:

> Read
> `docs/plans/2026-05-08-phase-7-manager-service-reconciler-cleanup-plan.md`,
> `docs/plans/2026-05-08-manager-owned-internal-service-supervision-plan.md`,
> and the current implementation in `weft/core/manager.py`,
> `weft/core/manager_services.py`, and `weft/core/heartbeat.py`. Look for
> errors, bad ideas, missing tests, and ambiguous instructions. Do not
> implement anything. Could a zero-context engineer implement this correctly?

After implementation, run a completed-work review focused on:

- whether `Manager.process_once()` truly has one service reconciliation path;
- whether autostart behavior is unchanged except for the bug fix;
- whether heartbeat helper liveness uses shared candidate logic;
- whether tests prove Postgres/full-suite regression coverage.

## 10. Out of Scope

- Phase 7 part 3 autonomous deletion.
- Changing `weft system prune` behavior.
- Treating Weft queues as audit storage.
- Adding persistent heartbeat registrations.
- Adding a public heartbeat service demand protocol.
- Changing the launcher wait-hook contract or `TASK_PROCESS_POLL_INTERVAL`.
- Changing autostart manifest schema.
- Rewriting manager election.
- Optimizing `weft status --json` beyond avoiding new service-reconciler scans.

## 11. Fresh-Eyes Review Notes

Self-review pass 1:

- Ambiguity found: "heartbeat should not start unconditionally" could break a
  standalone public heartbeat caller. Current production code only calls
  `upsert_heartbeat()` from TaskMonitor, so this plan keeps standalone demand
  out of scope and names the stop gate if that product contract changes.
- Ambiguity found: "uncertain candidates do not authorize duplicate launch"
  could be misread as stale `running` rows always blocking replacement. This
  plan distinguishes stale non-terminal rows without live proof from true
  uncertainty caused by probe/backend errors.
- Ambiguity found: "one reconciler" could still allow a separate autostart loop
  wrapper. This plan says wrappers are transitional only and must feed the
  shared path.

Self-review pass 2:

- The plan does not ask for destructive cleanup and does not weaken audit
  boundaries.
- The plan gives exact files, tests, invariants, stop gates, rollout, and
  rollback.
- The plan keeps the current direction. It does not turn phase 7 into a
  broader queue-cleanup or endpoint-redesign project.

Independent review pass:

- Coordination hazard found: this cleanup plan overlapped too much with the
  older manager-owned service plan. Fix: this plan now explicitly supersedes
  the older plan for remaining single-reconciler implementation work.
- Crash-safety gap found: in-memory `spawn_pending` alone is insufficient after
  manager death. Fix: pending service evidence must include unconsumed
  `weft.spawn.requests` messages with trusted service metadata.
- Liveness gap found: `uncertain` could block service recovery forever. Fix:
  uncertainty now requires attempts/time tracking and an explicit convergence
  or degraded-health outcome.
- Trust-boundary gap found: reserved service metadata could be spoofed by user
  tasks. Fix: trusted candidates now require manager-owned internal runtime
  evidence or currently desired autostart manifest evidence.
- Ops-scale gap found: scanning the whole task log once per turn is still too
  expensive. Fix: external-history scans are conditional, rate-limited, and
  high-water based.
- Test ambiguity found: stale evidence tests must prove latest per-TID status,
  not just "some stale row exists." Fix: the plan now names `running` then
  `completed` history as the regression shape.

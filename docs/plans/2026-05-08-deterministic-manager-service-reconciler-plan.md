# Deterministic Manager Service Reconciler Plan

Status: completed
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.6], [MA-1.6a]; docs/specifications/05-Message_Flow_and_State.md [MF-3.2], [MF-6]; docs/specifications/07-System_Invariants.md [MANAGER.8]-[MANAGER.15]
Superseded by: none

## 1. Goal

Make manager-owned singleton reconciliation deterministic by construction. The
manager should reduce all evidence for one desired service key into one
well-defined view, then apply one transition from a small transition table. The
same reducer must cover heartbeat, TaskMonitor, and autostart `once`/`ensure`
services. Timing pressure may change when evidence becomes visible, but it must
not change what decision the manager makes once the same evidence snapshot is
available.

This plan supersedes
[`2026-05-08-phase-7-manager-service-reconciler-cleanup-plan.md`](./2026-05-08-phase-7-manager-service-reconciler-cleanup-plan.md)
for remaining manager-owned service reconciler work. Keep that older plan as
background for review findings and failure history, not as the implementation
contract for this slice.

Requested outcomes:

- define explicit semantics for service evidence and transitions;
- move transition selection into a pure, table-tested reducer;
- keep Manager as the only launcher and side-effect owner;
- preserve current heartbeat, TaskMonitor, and autostart behavior;
- add full test support that proves convergence under evidence-order races.

## 2. Source Documents

Source specs:

- [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md)
  [CC-2.2], [CC-2.3], [CC-2.4], [CC-2.5]
- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md)
  [MA-1.4], [MA-1.6], [MA-1.6a], [MA-1.7], [MA-3]
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-1], [MF-2], [MF-3.1], [MF-3.2], [MF-5], [MF-6]
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [STATE.1]-[STATE.6], [QUEUE.1]-[QUEUE.6], [OBS.9],
  [MANAGER.8]-[MANAGER.15], [IMPL.5]-[IMPL.7]

Prior plans and their status:

- [`2026-05-07-phase-7-task-monitor-supervision-and-cleanup-plan.md`](./2026-05-07-phase-7-task-monitor-supervision-and-cleanup-plan.md)
  remains the phase umbrella. This slice is phase 7 part 1 or part 2
  hardening only. It must not implement destructive cleanup from phase 7 part
  3.
- [`2026-05-08-manager-owned-internal-service-supervision-plan.md`](./2026-05-08-manager-owned-internal-service-supervision-plan.md)
  introduced the single manager-owned service contract. It is already
  superseded by the cleanup plan; use it only as historical context.
- [`2026-05-08-phase-7-manager-service-reconciler-cleanup-plan.md`](./2026-05-08-phase-7-manager-service-reconciler-cleanup-plan.md)
  gathered the cleanup issues. This plan supersedes it for the deterministic
  reducer implementation path.
- [`2026-04-16-autostart-hardening-and-contract-alignment-plan.md`](./2026-04-16-autostart-hardening-and-contract-alignment-plan.md)
  defines current autostart behavior. Preserve manifest format and visible
  policy semantics.
- [`2026-04-17-heartbeat-service-plan.md`](./2026-04-17-heartbeat-service-plan.md)
  defines heartbeat service queue shape. Preserve heartbeat request payloads.
- [`2026-05-07-manager-selection-ping-pong-liveness-plan.md`](./2026-05-07-manager-selection-ping-pong-liveness-plan.md)
  defines keyed PING/PONG liveness. Reuse the existing probe shape.

### Prior Plan Review: Why This Plan Exists

The cleanup plan identified the right problem class: heartbeat, TaskMonitor,
and autostart still had too much duplicated lifecycle logic. It also named many
of the right risks, including stale task-log evidence, spawn-pending durability,
uncertain liveness, and autostart restart/backoff drift. That was necessary,
but not strong enough. The serve race and follow-on failures show that an
engineer could implement the cleanup plan while preserving timing-sensitive
branches inside `Manager`.

Treat these as explicit review findings against the cleanup plan:

- The old plan said "one reconciler", but it did not require one pure reducer
  with one transition action output. That left room for `_tick_managed_service`,
  `_tracked_service_candidate`, `_service_has_owner_evidence`, and autostart
  bookkeeping to keep making independent start/wait/suppress decisions.
- It listed liveness evidence sources, but it did not define a total transition
  precedence table. The missing hard rule was that terminal proof for TID A
  must remove A from live ownership even if host PID or stale runtime evidence
  still appears live.
- It named spawn-pending crash safety, but it did not make durable pending spawn
  evidence an input to the same transition function that decides restarts. That
  leaves duplicate suppression vulnerable to manager-local state drift.
- It called out uncertain candidates, but it did not force bounded convergence
  semantics in the reducer contract. "Uncertain" could still become an
  indefinite silent block under backend or PING timing pressure.
- It relied too heavily on broker-backed integration tests and did not require
  order-invariant table tests for the transition logic. Integration tests catch
  symptoms; they do not make the decision space mechanically complete.
- It did not make state updates part of the reducer output. Without explicit
  updates for `active_tid`, `spawn_pending`, `next_allowed_ns`, `restarts`,
  `launched_once`, and uncertainty counters, state can still be mutated in
  scattered branches before or after side effects.
- It preserved too much ambiguity around autostart compatibility state. The new
  reducer must own the policy decision, while Manager may keep legacy
  dictionaries only as compatibility and payload bookkeeping.

The correction is not a materially different architecture. It is a stricter
version of the manager-owned singleton path: Manager still owns side effects,
but transition selection is pure, table-driven, and tested independently from
queue timing.

Required guidance before editing:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/runtime-and-context-patterns.md`](../agent-context/runbooks/runtime-and-context-patterns.md)
- [`docs/agent-context/runbooks/testing-patterns.md`](../agent-context/runbooks/testing-patterns.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)
- [`docs/lessons.md`](../lessons.md)

Spec maintenance required before implementation is done:

- Keep [MA-1.6] and [MA-1.6a] aligned with the reducer owner and transition
  semantics.
- Keep [MF-3.2] aligned with heartbeat endpoint liveness and the manager-owned
  service contract.
- Keep [MF-6] aligned with spawn-pending evidence and manager-owned enqueue
  behavior.
- Keep [MANAGER.8]-[MANAGER.15] aligned with deterministic singleton
  convergence.

## 3. Context And Key Files

### Current Structure

`weft/core/manager.py` currently owns both evidence gathering and transition
selection:

- `_reconcile_managed_services()` builds desired service specs and loops over
  them.
- `_tick_managed_service()` mixes pending-spawn checks, tracked-child checks,
  candidate liveness checks, backoff, max-restart policy, and enqueue.
- `_observed_service_candidates_by_key()` scans `weft.log.tasks` and
  `weft.state.tid_mappings`, then turns rows into `ServiceCandidate` values.
- `_tracked_service_candidate()` maps manager-local child processes to service
  candidates.
- `_apply_service_terminal_transition()` applies terminal evidence to
  `ManagedServiceState`.

`weft/core/manager_services.py` currently contains shared dataclasses and pure
metadata helpers, but not a full decision reducer. That is the right home for
the deterministic state-machine pieces because they can be tested without a
live manager, process, or broker.

`weft/core/heartbeat.py` has helper-side endpoint liveness logic. The
implementation may keep endpoint-specific queue writes there, but liveness
truth must not fork from the manager-owned singleton candidate contract.

`tests/core/test_manager.py` contains broker-backed manager and autostart
tests. It is the right home for integration regressions that require real
queues and manager behavior.

`tests/cli/test_cli_serve.py` contains the foreground serve regression:
start `weft manager serve`, observe heartbeat and TaskMonitor, kill each, and
assert exactly one replacement. Keep that test as an end-to-end proof, not as
the only proof.

### Files To Modify

Core implementation:

- `weft/core/manager_services.py`
  - Add pure evidence and decision models.
  - Add the reducer function and transition helpers.
  - Keep this module free of broker, process, and CLI side effects.
- `weft/core/manager.py`
  - Keep evidence collection and side effects here.
  - Replace ad hoc transition selection in `_tick_managed_service()` with the
    reducer result.
  - Keep spawn, backoff application, child bookkeeping, and autostart legacy
    state synchronization here.
- `weft/core/heartbeat.py`
  - Touch only if endpoint helper liveness still duplicates reducer semantics
    after the manager-side work.
  - Do not change heartbeat request payload shape.
- `weft/_constants.py`
  - Touch only if a production constant is required. Production constants must
    live here.

Tests:

- `tests/core/test_manager_services.py`
  - Create this file for pure reducer/table tests.
  - No broker needed for pure transition tests.
- `tests/core/test_manager.py`
  - Update or add broker-backed manager integration tests.
  - Keep real queues through `broker_env`.
- `tests/core/test_heartbeat_helpers.py`
  - Update only if heartbeat helper liveness is changed.
- `tests/cli/test_cli_serve.py`
  - Keep the existing serve restart regression.
  - Add only if the existing test does not cover the final contract.

Docs:

- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/plans/README.md`
- `docs/lessons.md` only if the implementation exposes a repeated failure
  mode not already captured.

### Read First

Before editing, answer these comprehension questions in your own notes:

1. Which queue-visible surfaces can prove a service is live, and which can
   only prove it was once started?
2. Which component is allowed to enqueue heartbeat and TaskMonitor spawn
   requests?
3. What must happen if terminal proof for service TID A is visible while the
   host PID for A is still alive?
4. What must happen if service TID A is terminal and service TID B is live?
5. Which autostart state is user intent, and which state is only manager-local
   retry bookkeeping?

If any answer is unclear after reading the specs and current code, stop and
clarify before editing. This is a correctness boundary.

### Shared Paths To Reuse

- Use `service_metadata()` and `service_key_from_metadata()` from
  `weft/core/manager_services.py`.
- Use `select_canonical_live_candidate()` or replace it with a stricter helper
  in the same module. Do not duplicate the lowest-live-TID rule in manager
  methods.
- Use `iter_queue_json_entries()` for append-only queue scans.
- Use `send_keyed_ping_probe()` for task-local liveness probes.
- Use `build_context()` / `WeftContext` for context-bound helper paths.
- Use existing `manager_setup`, `broker_env`, and `WeftTestHarness` fixtures.

Do not introduce a QueueFactory, a database table, a lease service, a lock
manager, or a second manager-owned service launch path.

## 4. Semantics And Transition Contract

The reducer contract is:

```text
decision = reduce_managed_service(
    service_spec,
    service_state,
    evidence_snapshot,
    now_ns,
)
```

The reducer must be pure. It must not:

- read queues;
- ping control queues;
- inspect PIDs;
- enqueue spawn requests;
- mutate `TaskSpec`;
- mutate manager-local state in place;
- update autostart legacy dictionaries;
- log task events.

The reducer may return a new state overlay or explicit state updates, but the
manager applies them. The manager remains the side-effect owner.

### Evidence Inputs

Define one evidence snapshot type in `weft/core/manager_services.py`. The exact
name is up to the implementer, but it must carry these facts explicitly:

- `pending_spawn: bool`
  - `True` when a durable manager-owned spawn request for this service key is
    present in the manager inbox.
  - Pending spawn evidence blocks duplicate start.
- `candidates: tuple[ServiceCandidate, ...]`
  - Observed candidates from tracked children, task-log rows, runtime handles,
    and keyed PING/PONG.
  - Candidate states remain `live`, `terminal`, or `uncertain`.
- `terminal_tids: frozenset[str]`
  - Derived from candidates whose state is `terminal`.
  - Terminal proof for a TID wins over live PID/PONG/runtime evidence for the
    same TID.
- `canonical_live: ServiceCandidate | None`
  - Lowest live candidate after removing terminal TIDs.
- `uncertain: tuple[ServiceCandidate, ...]`
  - Candidates that could not prove live or terminal because a probe or backend
    read failed in a way that leaves duplicate-launch risk real.
  - A successful keyed PING that receives no matching PONG is not uncertain by
    itself. It is stale/no-live evidence for that candidate.

The reducer should either compute these derived fields internally from
`candidates` or expose a pure helper that does. Do not compute this logic in
multiple manager methods.

### Transition Actions

Use one explicit action enum or `Literal` union. Required actions:

- `keep_live`
  - There is a canonical live candidate.
  - Manager records that TID as `active_tid`.
  - No enqueue.
- `wait_pending`
  - Durable spawn request already exists.
  - Manager records `spawn_pending=True`.
  - No enqueue.
- `wait_uncertain`
  - No live candidate exists, but uncertain evidence means duplicate-launch
    risk is still real.
  - No enqueue yet.
- `degraded_wait`
  - Optional separate action if clearer than a flagged `wait_uncertain`.
  - Uncertain evidence exhausted its retry budget, but the evidence source
    never reached a trustworthy no-live conclusion.
  - No enqueue. Manager records visible degraded health and keeps retrying.
- `schedule_restart`
  - Terminal evidence applies to the active or launched service and lifecycle
    permits restart, but backoff is not yet expired.
  - Manager sets `next_allowed_ns`.
  - No enqueue.
- `start_now`
  - No live owner, no pending spawn, no blocking uncertainty, lifecycle allows
    launch, and backoff is expired.
  - Manager enqueues exactly one spawn request through the existing manager
    inbox path.
- `suppress_once`
  - `lifecycle == "once"` and `launched_once=True`.
  - No enqueue.
- `suppress_max_restarts`
  - `lifecycle == "ensure"`, `max_restarts` is set, and restart budget is
    exhausted.
  - No enqueue.
- `disabled_or_not_desired`
  - Optional action if easier for tests. Manager may also omit reducer calls
    for undesired services.

Do not use booleans such as `has_owner` as the final reducer output. The point
of this slice is to remove implicit branching.

### Transition Precedence

For one desired service key, apply precedence in this order:

1. Terminal proof removes that TID from live ownership consideration.
2. Canonical live candidate wins over all non-terminal stale rows.
3. Durable pending spawn blocks duplicate enqueue.
4. Bounded uncertainty may block enqueue for a small number of attempts. If
   later evidence reaches a trustworthy no-live conclusion, continue through the
   lifecycle rules. If the evidence source stays unreliable, return
   `degraded_wait` or flagged `wait_uncertain` rather than launching a possible
   duplicate.
5. `once` lifecycle suppresses restart after first successful enqueue.
6. `ensure` max-restart budget suppresses restart after the budget is reached.
7. Backoff suppresses restart until `now_ns >= next_allowed_ns`.
8. Otherwise, start now.

This order is part of the contract. Add table-driven tests for it.

### Timing Race Semantics

The following outcomes must be deterministic:

- Terminal proof for TID A plus live PID for TID A means A is terminal for
  singleton ownership.
- Terminal proof for TID A plus live candidate TID B means B is the live owner
  if B is the lowest live candidate.
- Pending spawn plus terminal old owner means wait for the pending spawn, not
  start a duplicate.
- Local `spawn_pending=True` without durable pending evidence is not a permanent
  owner. It may wait through one bounded confirmation cycle, but it must clear
  or degrade once durable pending, live child, and terminal evidence have all
  been reconciled.
- Missing pending spawn plus terminal active owner means schedule restart or
  start now according to lifecycle and backoff.
- Unknown or failed PING/backend evidence may delay replacement only with a
  visible bounded policy. It must not become an infinite silent block, and it
  must not become an unqualified duplicate launch.
- Replaying candidates in a different order must produce the same decision.

### Backoff Semantics

- Backoff applies to restarts, not first launch.
- `state.next_allowed_ns` is an absolute nanosecond timestamp.
- For TaskMonitor, use `WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS`.
- For autostart `ensure`, use manifest `policy.backoff_seconds`.
- A terminal event timestamp may be used as the base when available; otherwise
  use `now_ns`. Pick one rule and test it. Prefer event timestamp for durable
  evidence and `now_ns` for manager-local terminal evidence without timestamp.
- `force=True` may bypass scan throttling, but it must not bypass lifecycle
  rules such as `once` or `max_restarts`.

### State Update Semantics

The reducer output must make required state updates explicit:

- set or clear `active_tid`;
- set or clear `spawn_pending`;
- set `next_allowed_ns`;
- increment or preserve `restarts`;
- set `launched_once`;
- update uncertainty counters;
- record degraded uncertainty reason when evidence remains unreliable after the
  retry budget;
- add locally terminal TIDs when terminal proof is accepted.

The manager should apply these updates in one place before it executes the
action side effect. Do not keep partial state writes scattered across
`_tracked_service_candidate()`, `_service_has_owner_evidence()`,
`_apply_service_terminal_transition()`, and `_tick_managed_service()`.

## 5. Invariants And Constraints

Must preserve:

- Manager remains the only component that launches heartbeat, TaskMonitor, and
  autostart services.
- No new public queue names.
- No TaskSpec schema change.
- No public CLI shape change.
- No heartbeat request payload change.
- No autostart manifest schema change.
- TIDs remain immutable and still come from the spawn request timestamp.
- Task state transitions remain forward-only.
- `spec` and `io` remain immutable after resolved `TaskSpec` creation.
- Runtime-only `weft.state.*` queues remain runtime-only.
- Public task metadata alone cannot claim internal service ownership.
- Queue-history reads that need complete history use generator-based helpers.
- One desired service key converges to at most one canonical live owner.
- Non-leader, draining, or dispatch-suspended managers do not start or restart
  singleton services.
- TaskMonitor remains non-destructive. Do not implement queue cleanup in this
  slice.

Hidden couplings:

- `Manager._handle_work_message()` assigns child TID from the manager inbox
  message timestamp. The reducer must not assign TIDs.
- `_pending_service_keys()` sees durable spawn requests before a child exists.
  That evidence must be part of convergence, not a special branch.
- `weft.log.tasks` and `weft.state.tid_mappings` are append-only histories.
  Snapshot code must reduce by latest timestamp per TID.
- Task-owned terminal proof can become visible while the process is still
  unwinding. Singleton ownership must follow terminal proof, not PID liveness,
  for the same TID.
- Autostart has legacy state in `_autostart_state` and `_autostart_launched`.
  During this slice, preserve that compatibility while moving transition
  selection into the shared reducer.
- Heartbeat helper startup must still call the manager bootstrap helper if no
  live endpoint exists, but it must not direct-submit heartbeat tasks.

Stop and re-evaluate if:

- the implementation wants a new queue, lock, lease, or external state store;
- the implementation adds a second heartbeat or TaskMonitor launch path;
- tests mock `simplebroker.Queue` or manager process lifecycle for behavior
  that can be proven with `broker_env` or `WeftTestHarness`;
- the reducer needs to import `Queue`, `Manager`, `TaskSpec`, process helpers,
  CLI modules, or `time.time_ns()` directly;
- the implementation changes public CLI output or manifest shape;
- deterministic transition behavior depends on candidate iteration order.

## 6. Tasks

### 1. Add Red Reducer Tests First

Outcome: the desired semantics are captured before implementation moves logic.

Files to touch:

- `tests/core/test_manager_services.py` (new)

Read first:

- `weft/core/manager_services.py`
- `weft/core/manager.py` service candidate and tick methods
- `docs/specifications/03-Manager_Architecture.md` [MA-1.6a]
- `docs/specifications/05-Message_Flow_and_State.md` [MF-3.2], [MF-6]

Required tests:

- terminal proof for the same TID beats live PID evidence;
- older terminal TID does not hide newer live TID;
- live PONG/runtime evidence beats stale non-terminal task-log evidence;
- pending spawn blocks duplicate start;
- local `spawn_pending=True` does not block forever after durable pending
  evidence disappears;
- `once` service suppresses restart after `launched_once=True`;
- `ensure` service starts after terminal evidence and expired backoff;
- `ensure` service waits before backoff expiry;
- `ensure` service suppresses after `max_restarts`;
- successful no-PONG evidence is stale/no-live and can converge to `start_now`
  when lifecycle permits;
- probe/backend error uncertainty waits for the bounded retry budget, then
  converges to visible degraded wait instead of an unqualified `start_now`;
- candidate order does not change the decision.

Test style:

- Use plain objects and dataclasses. These are pure reducer tests.
- Do not create queues or manager instances.
- Use fixed `now_ns` values. Do not call `time.time_ns()` in tests.
- Assert on explicit action and state updates, not implementation-private
  helper call order.

Done when:

- the tests fail because the reducer API does not exist yet;
- no production code has been changed in this task.

### 2. Introduce Pure Reducer Types And Helpers

Outcome: `manager_services.py` owns the deterministic transition contract.

Files to touch:

- `weft/core/manager_services.py`
- `tests/core/test_manager_services.py`

Implementation requirements:

- Add a decision type such as `ManagedServiceDecision`.
- Add an evidence snapshot type such as `ManagedServiceEvidence`.
- Add an action type such as `ManagedServiceAction`.
- Add a pure reducer function. Suggested name:
  `reduce_managed_service_state()`.
- Add pure helper(s) to canonicalize candidates:
  - group by TID;
  - terminal proof wins per TID;
  - lowest live TID wins among remaining live candidates;
  - uncertain candidates stay separate.
- Keep the current `ManagedServiceSpec`, `ManagedServiceState`, and
  `ServiceCandidate` names unless a tiny signature change clearly improves the
  reducer. Avoid broad renames.

Constraints:

- No imports from `weft.core.manager`.
- No imports from `simplebroker`.
- No process liveness checks.
- No queue reads.
- No wall-clock calls inside the reducer.

Done when:

- all tests from task 1 pass;
- `uv run pytest tests/core/test_manager_services.py -q` passes;
- `uv run ruff check weft/core/manager_services.py tests/core/test_manager_services.py`
  passes.

### 3. Adapt Manager Evidence Collection To Feed The Reducer

Outcome: `Manager` gathers evidence, but no longer decides transitions through
ad hoc branches.

Files to touch:

- `weft/core/manager.py`
- `tests/core/test_manager.py`

Read first:

- `Manager._tracked_service_candidate()`
- `Manager._observed_service_candidates_by_key()`
- `Manager._pending_service_keys()`
- `Manager._tick_managed_service()`
- `Manager._cleanup_children()`

Implementation requirements:

- Keep process, queue, PING, and log reads in `Manager`.
- Build one evidence snapshot per desired service key.
- Include tracked-child evidence, pending-spawn evidence, task-log evidence,
  runtime-handle evidence, and keyed PING/PONG evidence.
- Remove decision logic from `_service_has_owner_evidence()` or make it a thin
  wrapper around the new reducer. Prefer deletion if callers allow it.
- Keep `_tick_managed_service()` as an applier:
  - call reducer;
  - apply state updates;
  - enqueue only on `start_now`;
  - schedule backoff only from reducer output;
  - synchronize autostart legacy state where required.
- Preserve `_service_supervision_allowed()` outside the reducer. Leadership
  and dispatch-suspension checks are manager execution context, not pure
  service policy.

Tests:

- Update the existing TaskMonitor restart/backoff tests to assert the same
  behavior through `Manager._tick_task_monitor()`.
- Keep the recent regression where terminal tracked child allows restart.
- Add or update a test where a durable pending service request blocks a
  duplicate start even after old terminal evidence appears.
- Add a test where `state.spawn_pending=True` but no durable pending spawn row
  exists, and the manager converges by clearing the local flag after bounded
  confirmation rather than blocking forever or immediately duplicating.

Stop if:

- the change requires mocking `Queue` for core manager behavior;
- `_tick_managed_service()` still has more than one place that can decide
  "start vs wait vs suppress";
- reducer decisions are ignored or re-derived in manager branches.

Done when:

- `uv run pytest tests/core/test_manager.py -q -k "task_monitor or managed_service"`
  passes;
- the manager still launches ordinary child tasks.

### 4. Move Autostart Ensure/Once Decisions Onto The Same Reducer

Outcome: autostart policy uses the same transition table as heartbeat and
TaskMonitor while preserving manifest behavior.

Files to touch:

- `weft/core/manager.py`
- `tests/core/test_manager.py`

Read first:

- `Manager._desired_autostart_services()`
- `Manager._mark_autostart_enqueued()`
- `Manager._prune_autostart_state()`
- existing autostart tests around `write_autostart_fixture()`

Implementation requirements:

- Keep manifest parsing and payload building in current manager methods.
- Keep autostart service keys as resolved manifest source strings.
- Keep `policy.mode`, `policy.max_restarts`, and `policy.backoff_seconds`
  semantics unchanged.
- Map manifest policy into `ManagedServiceSpec` and `ManagedServiceState`.
- Let the reducer decide `once`, `ensure`, max-restart, pending, live,
  terminal, and backoff behavior.
- Keep `_autostart_state` only as compatibility/policy bookkeeping until a
  later cleanup removes it. Do not create a new durable state store.

Tests:

- Existing tests that must remain green:
  - `test_manager_autostart_ensure_restarts`
  - `test_manager_autostart_ensure_applies_backoff_to_restart_only`
  - `test_manager_autostart_ensure_respects_max_restarts`
  - pipeline autostart restart tests
- Add one focused test proving an autostart terminal owner and pending
  replacement do not produce a duplicate.
- Add one focused test proving `once` uses the reducer's suppress action after
  first launch.

Stop if:

- autostart manifest schema changes;
- autostart starts using a separate transition helper from built-in services;
- code starts reading `weft.log.tasks` with fixed-size peeks.

Done when:

- `uv run pytest tests/core/test_manager.py -q -k "autostart"` passes on
  SQLite;
- `uv run bin/pytest-pg tests/core/test_manager.py -q -k "autostart"` passes
  or the plan is updated with a documented Postgres blocker.

### 5. Align Heartbeat Helper Liveness With The Shared Contract

Outcome: helper-side heartbeat resolution does not accept stale endpoint rows
that the service reducer would reject.

Files to touch:

- `weft/core/heartbeat.py`
- `tests/core/test_heartbeat_helpers.py`
- maybe `weft/core/manager_services.py` if a pure helper should be shared

Implementation requirements:

- Keep `ensure_heartbeat_service()` responsible for manager bootstrap and
  endpoint resolution.
- Keep `_write_heartbeat_request()` as an ordinary queue write to the resolved
  endpoint inbox.
- Reuse the same candidate reduction semantics for heartbeat endpoint
  liveness:
  - terminal task-log status rejects the endpoint;
  - keyed PING/PONG proves live;
  - live runtime handle can prove live;
  - stale non-terminal log alone is not enough.
- Do not make heartbeat helper directly enqueue heartbeat spawn requests.

Tests:

- Existing helper tests must remain green.
- Add or update one real PING/PONG-backed helper test if the current suite does
  not already prove it.
- Add one stale endpoint test where terminal proof for endpoint TID rejects the
  endpoint even if an old endpoint row remains.

Stop if:

- endpoint liveness starts using a different winner rule than service
  candidates;
- helper startup becomes a second manager-owned service launch path.

Done when:

- `uv run pytest tests/core/test_heartbeat_helpers.py -q` passes.

### 6. Keep One End-To-End Serve Restart Proof

Outcome: the full CLI path proves the reducer decisions converge in practice.

Files to touch:

- `tests/cli/test_cli_serve.py` only if needed.

Required proof:

- Start `weft manager serve --context <root>`.
- Wait for one canonical manager.
- Wait for one live heartbeat and one live TaskMonitor.
- Kill TaskMonitor.
- Observe exactly one replacement TaskMonitor.
- Kill heartbeat.
- Observe exactly one replacement heartbeat.
- Stop manager cleanly.

Use the existing `test_serve_restarts_singleton_services_without_duplicates`
unless it no longer expresses the contract. Do not split this into mock-only
tests. It is intentionally the broker/process proof.

Done when:

- `uv run pytest tests/cli/test_cli_serve.py::test_serve_restarts_singleton_services_without_duplicates -q -n0`
  passes;
- `uv run pytest tests/cli/test_cli_serve.py -q -n auto --maxfail=1` passes.

### 7. Update Specs, Plans, And Lessons

Outcome: documentation describes the implemented contract and does not leave
conflicting active plans.

Files to touch:

- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/plans/README.md`
- `docs/lessons.md` if a new durable rule is discovered

Required updates:

- Add this plan as a related plan in touched specs.
- Update implementation mapping if method/module ownership changes.
- Mark
  `2026-05-08-phase-7-manager-service-reconciler-cleanup-plan.md` as
  superseded by this plan.
- Keep plan metadata and `docs/plans/README.md` synchronized.

Verification:

- `uv run pytest tests/specs/test_plan_metadata.py -q`

## 7. Testing Plan

### Red-Green Test Strategy

Write reducer tests first. These should fail before the reducer exists and
pass once the pure reducer is implemented. This is practical because the
transition contract is pure and can use fixed inputs.

For manager integration, add or update tests before or alongside the manager
adapter change. The tests can use existing manager methods if the behavior is
manager-internal, but at least one broker-backed test must prove the real
queue path.

### Do Not Mock

Do not mock:

- `simplebroker.Queue` for manager/autostart/serve behavior;
- manager process lifecycle in CLI serve tests;
- task-log or TID mapping history with hand-rolled fake stores;
- TaskSpec state transitions.

Mocks are acceptable for:

- fixed clocks or `now_ns` in pure reducer tests;
- `send_keyed_ping_probe()` in focused manager unit tests where a real PING
  would obscure the reducer adapter behavior;
- small fake `multiprocessing.Process` objects in tests that specifically
  assert tracked-child evidence. The fake must implement the cleanup methods
  the manager calls.

### Required Test Matrix

Pure reducer tests in `tests/core/test_manager_services.py`:

- action precedence;
- order-insensitive candidate reduction;
- once/ensure lifecycle;
- max restart suppression;
- backoff wait and expiry;
- bounded uncertainty;
- degraded wait for unreliable liveness evidence;
- terminal-over-live for same TID;
- live replacement over old terminal TID;
- pending-spawn duplicate suppression.

Broker-backed manager tests in `tests/core/test_manager.py`:

- TaskMonitor terminal tracked child allows restart;
- TaskMonitor terminal old child does not hide new live child;
- pending service spawn blocks duplicate restart;
- stale non-terminal task-log row without live proof does not block restart;
- autostart `once` suppresses second start;
- autostart `ensure` restarts after backoff;
- autostart `ensure` respects max restarts;
- pipeline autostart restart behavior still works.

Heartbeat helper tests in `tests/core/test_heartbeat_helpers.py`:

- stale terminal heartbeat endpoint rejected;
- live endpoint accepted only with PING/PONG or live runtime handle evidence;
- helper does not direct-submit heartbeat.

CLI integration in `tests/cli/test_cli_serve.py`:

- foreground serve singleton restart/no-duplicate proof.

### Commands During Implementation

Run after task 2:

```bash
uv run pytest tests/core/test_manager_services.py -q
uv run ruff check weft/core/manager_services.py tests/core/test_manager_services.py
```

Run after task 3:

```bash
uv run pytest tests/core/test_manager.py -q -k "task_monitor or managed_service"
uv run ruff check weft/core/manager.py tests/core/test_manager.py
```

Run after task 4:

```bash
uv run pytest tests/core/test_manager.py -q -k "autostart"
uv run bin/pytest-pg tests/core/test_manager.py -q -k "autostart"
```

Run after task 5:

```bash
uv run pytest tests/core/test_heartbeat_helpers.py -q
```

Run after task 6:

```bash
uv run pytest tests/cli/test_cli_serve.py::test_serve_restarts_singleton_services_without_duplicates -q -n0
uv run pytest tests/cli/test_cli_serve.py -q -n auto --maxfail=1
```

## 8. Verification And Gates

Final local gates before claiming implementation done:

```bash
uv run pytest tests/core/test_manager_services.py -q
uv run pytest tests/core/test_manager.py -q -k "task_monitor or managed_service or autostart"
uv run pytest tests/core/test_heartbeat_helpers.py -q
uv run pytest tests/cli/test_cli_serve.py -q -n auto --maxfail=1
uv run pytest tests/specs/test_plan_metadata.py -q
uv run ruff check weft tests/core/test_manager_services.py tests/core/test_manager.py tests/core/test_heartbeat_helpers.py tests/cli/test_cli_serve.py
uv run mypy weft extensions/weft_docker extensions/weft_macos_sandbox
```

Required broader gates because this touches manager runtime behavior:

```bash
uv run pytest
uv run bin/pytest-pg
```

If `uv run pytest` or `uv run bin/pytest-pg` is too slow for an intermediate
slice, do not claim final completion. Say which targeted gates passed and leave
the full-suite gate outstanding.

Runtime observation after deploy or ops exercise:

- `weft manager list --json` shows one canonical active manager per context.
- `weft task list --json` or task logs show at most one live heartbeat service
  and one live TaskMonitor service for the canonical manager.
- Killing TaskMonitor leads to exactly one replacement after configured
  backoff.
- Killing heartbeat leads to exactly one replacement.
- `weft.spawn.requests` does not accumulate duplicate internal service spawn
  requests.
- TaskMonitor remains non-destructive and reports operational health through
  PING/STATUS.

Rollback:

- The change should not require queue migration.
- No new queue names or payload formats should be introduced.
- If the reducer causes service launch regressions, revert the reducer and
  manager adapter changes together. Old task-log rows and service metadata
  remain compatible because the plan does not change their shape.

## 9. Independent Review Loop

This is risky work. Run independent review after the plan is written and again
after implementation.

Reviewer preference:

- Prefer a different agent family than the implementer if available.
- If only same-family review is available, record that limitation in the
  review notes.

Plan review prompt:

```text
Read docs/plans/2026-05-08-deterministic-manager-service-reconciler-plan.md,
docs/specifications/03-Manager_Architecture.md [MA-1.6a],
docs/specifications/05-Message_Flow_and_State.md [MF-3.2], [MF-6],
weft/core/manager_services.py, and the service reconciliation portions of
weft/core/manager.py. Look for errors, bad ideas, and latent ambiguities.
Do not implement anything. Answer: could you implement this confidently and
correctly if asked? If not, list the blockers first.
```

Completed-work review prompt:

```text
Review the implementation of the deterministic manager service reconciler
against docs/plans/2026-05-08-deterministic-manager-service-reconciler-plan.md
and the governing specs. Prioritize bugs, regressions, missing tests, and any
drift from the single reducer contract. Do not focus on style unless it affects
correctness or maintainability.
```

Feedback handling:

- Accept review findings by updating the plan, code, or tests.
- If rejecting a finding, write down why the current contract is still correct.
- If the reviewer cannot implement the plan confidently, treat that as a
  blocker until the ambiguity is fixed.

## 10. Out Of Scope

Do not implement:

- phase 7 part 3 cleanup or queue deletion;
- new service management CLI commands;
- new heartbeat scheduling features;
- cron syntax or wall-clock heartbeat schedules;
- new durable service registry queues;
- advisory locks or DB-specific leader election;
- autostart manifest schema changes;
- public TaskSpec schema changes;
- broad manager refactors unrelated to service transition selection;
- new external dependencies.

## 11. Fresh-Eyes Review

Before implementation starts, re-read this plan as a zero-context engineer and
check:

- Can you identify every file to read before editing?
- Can you name the reducer input, output, and side-effect boundary?
- Can you state why terminal proof beats PID liveness for the same TID?
- Can you explain how pending spawn evidence prevents duplicates?
- Can you run at least one red reducer test before implementation?
- Can you verify the CLI serve race without changing test timeouts?
- Can you describe rollback without queue migration?

If the answer to any question is no, revise the plan before implementation.

Fresh-eyes result for this draft:

- The plan keeps Manager as the side-effect owner and moves only pure
  transition selection into `manager_services.py`.
- The plan explicitly forbids cleanup, queue schema changes, and direct
  heartbeat submission.
- The plan includes pure tests, broker-backed tests, CLI serve proof, and
  Postgres gates.
- The plan does not require a materially different architecture from the
  manager-owned singleton path already discussed.

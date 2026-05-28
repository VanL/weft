# Manager Work-Stealing Dispatch Plan

Status: completed
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1], [MA-1.4], [MA-1.6a]; docs/specifications/05-Message_Flow_and_State.md [MF-2], [MF-6], [MF-7]; docs/specifications/07-System_Invariants.md [MANAGER.8]-[MANAGER.16]
Superseded by: none

## 1. Goal

Move manager public spawn dispatch from a global canonical-owner fence to a
work-stealing model. A manager that atomically reserves a public spawn request
may launch it. Registry leadership remains useful for CLI selection, status,
and eventual duplicate-manager convergence, but stale or ambiguous registry
proof must not stop live managers from draining `weft.spawn.requests` or
launching manager-owned internal services.

This is a correctness and robustness change, not a load-balancing feature. The
goal is that an unhealthy system under PG load keeps making bounded progress:
control remains responsive, public spawn work drains, and singleton services
converge through their service-key reducer instead of depending on one global
manager-leadership proof.

## 2. Source Documents

Source specs:

- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md)
  [MA-1], [MA-1.4], [MA-1.6a]: manager spawn consumption, registry heartbeat,
  and manager-owned internal service supervision.
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-2], [MF-6], [MF-7]: reserve-mode queue work, manager spawn flow, and
  runtime manager registry records.
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [MANAGER.8]-[MANAGER.16]: manager convergence, dispatch ownership, reserved
  work durability, singleton service convergence, and internal spawn priority.

Related plans and how to read them:

- [`2026-04-17-canonical-owner-fence-plan.md`](./2026-04-17-canonical-owner-fence-plan.md)
  is completed historical context. This plan intentionally supersedes its
  child-launch fence semantics for public and manager-owned spawn queues while
  preserving lowest-live-TID reduction for status, selection, and service
  duplicate convergence.
- [`2026-05-08-deterministic-manager-service-reconciler-plan.md`](./2026-05-08-deterministic-manager-service-reconciler-plan.md)
  is completed and remains the source for singleton service reducer behavior.
  Preserve it.
- [`2026-05-09-internal-spawn-priority-queue-plan.md`](./2026-05-09-internal-spawn-priority-queue-plan.md)
  is completed and remains the source for `weft.spawn.internal` priority.
  Preserve it.
- [`2026-05-10-control-and-service-convergence-state-machine-plan.md`](./2026-05-10-control-and-service-convergence-state-machine-plan.md)
  is completed and remains the source for bounded control handling and
  deterministic service convergence. Preserve the bounded-loop rules.
- [`2026-05-10-manager-service-authority-boundary-hardening-plan.md`](./2026-05-10-manager-service-authority-boundary-hardening-plan.md)
  is completed and remains the source for reserved service metadata stripping
  and manager-authored service evidence. Preserve it.
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md),
  [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md),
  and [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)
  define the planning, hardening, and review requirements for this risky
  execution-path change.

## 3. Context And Key Files

Files to modify:

- `weft/core/manager.py`
- `tests/core/test_manager.py`
- `tests/cli/test_cli_serve.py` only if focused CLI serve coverage needs new
  assertions after the core tests change
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/lessons.md` if implementation confirms a reusable failure pattern

Read first:

- `weft/core/manager.py`:
  `_evaluate_dispatch_ownership`, `_apply_final_dispatch_fence`,
  `_service_supervision_allowed`, `_run_managed_service_convergence`,
  `_drain_internal_spawn_requests`, `_maybe_yield_leadership`,
  `wait_for_activity`, and `process_once`.
- `weft/core/tasks/multiqueue_watcher.py`:
  `QueueMode.RESERVE`, `_drain_round_robin_pass`, `_drain_priority_queues`,
  `_update_active_queues`, and `_mark_pending_messages_prechecked`.
- `weft/core/manager_services.py`:
  `ManagedServiceState`, `ManagedServiceEvidence`,
  `reduce_managed_service_state`, and duplicate-owner selection.
- `weft/core/spawn_requests.py`:
  public spawn submission sanitization. Do not weaken reserved metadata
  stripping.
- `tests/core/test_manager.py`:
  existing dispatch-fence tests around `manager_spawn_fenced_*`, internal
  service convergence tests, duplicate singleton tests, and autostart ensure
  restart tests.
- `tests/cli/test_cli_serve.py`:
  foreground serve singleton restart and duplicate-manager coverage.

Current structure:

- `weft.spawn.requests` is the public spawn queue. Manager instances reserve
  public messages through `MultiQueueWatcher` reserve mode before building a
  child `TaskSpec`.
- `weft.spawn.internal` is manager-owned service spawn work. It is drained
  before public work and uses the same launch path.
- `weft.state.managers` is runtime-only registry state. The current code
  reduces this to `self`, `other`, `none`, or `unknown` and requires `self`
  immediately before launch.
- `_apply_final_dispatch_fence()` currently converts `other`, `none`, or
  `unknown` ownership into requeue, stranded reserved state, or dispatch
  suspension. Under PG load, stale registry proof can leave a live manager
  dispatch-suspended while public spawn requests keep waking it.
- `_service_supervision_allowed()` currently depends on positive `self`
  ownership. That means stale registry proof can block heartbeat and
  TaskMonitor launch, which removes the internal service that should help keep
  the system observable.

Comprehension checks before editing:

1. Which operation makes a public spawn request exclusively owned for launch:
   registry leadership or the atomic reserve/move from `weft.spawn.requests`?
2. Which path prevents two heartbeat or TaskMonitor instances from remaining
   live: global manager dispatch ownership or the service-key reducer plus
   duplicate cleanup?
3. What visible queue shape proves the old failure: unclaimed public spawn
   rows growing while the live manager has no internal spawn rows and no
   current control message?
4. Which parts of manager registry state are runtime-only advisory state, and
   which task state is durable task truth?

## 4. Invariants And Constraints

- TID format and immutability do not change. Spawn-request message ID still
  becomes the child TID.
- TaskSpec `spec` and `io` remain immutable after resolution.
- The durable execution spine stays `TaskSpec -> Manager -> Consumer ->
  TaskRunner -> queues/state log`. Do not add a second launch path.
- Public spawn work is authorized by atomic reserve. A launch attempt must
  still delete exactly the reserved message only after the child process is
  launched.
- Invalid spawn payload handling and reserved-queue policy do not change.
- Manager-owned singleton correctness remains service-key convergence:
  manager-authored metadata, pending spawn evidence, live/terminal evidence,
  and duplicate cleanup through the reducer.
- Caller-authored public TaskSpec metadata must not claim reserved internal
  service authority. Keep `weft/core/spawn_requests.py` sanitization intact.
- Registry leadership remains useful for status, selection, and voluntary
  duplicate-manager convergence. It must not be a hard prerequisite for public
  dispatch or internal service launch.
- Control handling remains bounded and progress-aware. Do not reintroduce an
  unbounded control drain loop or a public-queue hot spin when no actionable
  work can be performed.
- Runtime-only `weft.state.*` queues remain runtime-only.
- Append-only queue history reads remain generator-based for correctness.
- No new durable queues, payload formats, dependencies, or public CLI flags.
- `weft manager serve` duplicate-start behavior is out of scope unless a test
  proves it is coupled to the dispatch fence. Preserve foreground supervisor
  expectations unless the user explicitly changes them.

Stop and re-plan if:

- the implementation wants to add locks, leases, or a new durable manager
  ownership table;
- public dispatch starts depending on fixed-size queue history reads;
- singleton duplicate prevention moves out of the existing reducer path;
- a test requires mocking SimpleBroker reserve semantics instead of using real
  broker-backed queues;
- the work changes CLI output shape or public TaskSpec schema.

## 5. Tasks

1. Update the specs for the intended contract before code changes.
   - Outcome: the specs stop saying that positive `self` ownership is required
     for public child launch.
   - Files to touch:
     - `docs/specifications/03-Manager_Architecture.md`
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/07-System_Invariants.md`
   - Required edits:
     - In [MA-1], define public spawn dispatch as work-stealing by atomic
       reserve.
     - In [MF-6], narrow or remove the global final dispatch fence language for
       public and manager-owned spawn queues.
     - In [MANAGER.8]-[MANAGER.16], replace "only positive self ownership
       authorizes child launch" with "reserve authorizes public launch; service
       reducer authorizes singleton convergence."
     - Keep registry leadership language for status, startup selection, and
       eventual duplicate-manager yield.
   - Tests: none for docs alone.
   - Done when: specs and this plan agree on the new invariant.

2. Change the manager launch authority boundary.
   - Outcome: once a manager has reserved a public or internal spawn request
     and passed control/drain checks, it may launch that child without consulting
     global dispatch ownership.
   - Files to touch:
     - `weft/core/manager.py`
     - `tests/core/test_manager.py`
   - Approach:
     - Replace `_apply_final_dispatch_fence()` with a narrower helper, or make
       it return `True` for manager spawn queues and then remove dead suspension
       branches in the cleanup task.
     - Keep `_control_allows_child_launch()` as the last pre-launch STOP/KILL
       guard.
     - Do not change `_build_child_spec`, `_seed_child_inbox`, or
       `_launch_child_task` semantics.
   - Tests:
     - Replace old `other`, `none`, and `unknown` fence tests with a
       parametrized test proving the public request launches, the reserved
       message is acknowledged after launch, and no `manager_spawn_fenced_*`
       event is emitted.
     - Update the persistent-child case so a manager with a lower known leader
       and an existing persistent child can still launch a public request it
       reserved.
   - Stop if:
     - the implementation creates a second code path around
       `_handle_work_message`;
     - the test mocks queue reservation instead of writing to a real broker
       queue.
   - Done when:
     - public spawn launch is independent of `_evaluate_dispatch_ownership()`
       in tests.

3. Decouple singleton service supervision from global dispatch ownership.
   - Outcome: heartbeat, TaskMonitor, and autostart `ensure` supervision can
     advance when registry ownership is `none` or `unknown`.
   - Files to touch:
     - `weft/core/manager.py`
     - `tests/core/test_manager.py`
   - Approach:
     - Change `_service_supervision_allowed()` to check local runtime gates:
       not draining, not stopping, and no unrecovered reserved-message cleanup
       state if any remains after Task 4.
     - Do not trust public metadata as service authority. Keep the
       manager-authored envelope and reducer evidence rules unchanged.
     - Keep internal spawn priority and same-turn drain behavior.
   - Tests:
     - Add or update a test where `_evaluate_dispatch_ownership()` returns
       `unknown` and `_run_managed_service_convergence(force=True)` still
       enqueues and drains TaskMonitor or heartbeat internal spawn work.
     - Keep duplicate singleton tests that prove exactly one live owner remains
       when more than one live candidate is visible.
     - Keep autostart ensure restart/backoff tests green under both SQLite and
       PG.
   - Stop if:
     - the change bypasses `reduce_managed_service_state`;
     - service launch no longer emits manager-authored `task_spawned` evidence.
   - Done when:
     - service convergence does not depend on registry `self` ownership, and
       duplicate prevention still passes.

4. Remove or quarantine obsolete dispatch-suspension machinery.
   - Outcome: the manager code no longer exposes a confusing dead state machine
     for public dispatch suspension.
   - Files to touch:
     - `weft/core/manager.py`
     - `tests/core/test_manager.py`
     - possibly `weft/_constants.py` if fence-only constants become unused
   - Approach:
     - If Tasks 2 and 3 leave `DispatchSuspension`,
       `DispatchSuspensionRefresh`, `_refresh_dispatch_suspension`,
       `_dispatch_recovery_pending`, and fence event constants unused, remove
       them in the same slice.
     - If removal proves larger than expected, quarantine the old helper with a
       clear comment and open a follow-up only after the behavior tests are
       green. Do not leave ambiguous "ownership fence" comments on public
       dispatch.
     - Keep the bounded stalled-control-message hardening. That is still a
       valid robustness improvement.
   - Tests:
     - Delete or rewrite tests whose only purpose was asserting
       `manager_spawn_fenced_requeued`, `manager_spawn_fenced_stranded`, or
       `manager_spawn_fence_suspended` for public dispatch.
     - Keep tests that prove STOP after reservation leaves the reserved work
       durable.
   - Stop if:
     - removing old events requires a public compatibility promise not covered
       by the specs.
   - Done when:
     - `rg "dispatch_suspension|manager_spawn_fenced|manager_spawn_fence"`
       returns only intentional compatibility references or nothing.

5. Keep leadership yield advisory and idle-biased.
   - Outcome: duplicate managers may move work forward, but the system still
     tends back toward a single visible manager when it can do so safely.
   - Files to touch:
     - `weft/core/manager.py`
     - `tests/core/test_manager.py`
     - `tests/cli/test_cli_serve.py` only if foreground serve duplicate
       behavior needs clarification
   - Approach:
     - Preserve registry reduction for `_leader_tid()` and status/selection.
     - Do not run leadership yield as a precondition for launching a request
       already reserved by this manager.
     - If `_maybe_yield_leadership()` still runs before dispatch, make sure it
       cannot stop a manager that has actionable public, internal, control, or
       child-supervision work.
     - Keep persistent child protection unchanged.
   - Tests:
     - Add a test that a manager with a lower live leader but actionable public
       work can process one bounded dispatch turn before any voluntary yield.
     - Preserve or update duplicate foreground serve coverage based on the
       documented CLI contract. Do not silently loosen `manager serve` if tests
       say it is an operator-facing singleton command.
   - Stop if:
     - this starts redesigning manager bootstrap or CLI duplicate-start policy.
   - Done when:
     - duplicate managers can make progress under backlog, and idle duplicate
       managers still converge or yield according to existing lifecycle rules.

6. Add a focused regression for the observed PG failure shape.
   - Outcome: a live manager with stale or hidden registry ownership proof does
     not hot-spin while public spawn requests accumulate unclaimed.
   - Files to touch:
     - `tests/core/test_manager.py`
   - Approach:
     - Prefer a broker-backed unit test over a slow full CLI reproduction.
     - Simulate `_read_active_manager_records()` returning `{}` or `None` while
       `weft.spawn.requests` contains public work.
     - Use a stubbed `_launch_child_task()` only to avoid real child process
       cost; keep queues and reservation real.
   - Assertions:
     - the public queue count decreases or the reserved message is acknowledged
       after launch;
     - `_dispatch_suspension` is not set, if the attribute still exists during
       the implementation;
     - no fence event is emitted;
     - the manager remains control-capable.
   - Done when:
     - this test fails on the old model and passes on the work-stealing model.

## 6. Testing Plan

Use real broker-backed queues for all manager dispatch tests. Do not mock
SimpleBroker reservation, public spawn queues, manager-owned internal spawn
queues, or task-log writes. It is acceptable to stub `_launch_child_task()` in
unit tests that are proving manager scheduling and reservation semantics rather
than subprocess execution.

Focused tests while implementing:

```bash
uv run pytest tests/core/test_manager.py -q
uv run pytest tests/cli/test_cli_serve.py -q
```

Backend parity checks:

```bash
uv run --extra dev --extra docker --extra django --extra macos-sandbox pytest tests -q
PYTEST_ADDOPTS='-x --maxfail=1' PYTEST_XDIST_AUTO_NUM_WORKERS=17 WEFT_EAGER_FAILURE_TRACEBACK=1 uv run --extra dev --extra docker --extra django --extra macos-sandbox bin/pytest-pg --all
```

Static checks:

```bash
uv run ruff check weft tests
uv run mypy weft extensions/weft_docker extensions/weft_macos_sandbox
uv run pytest tests/specs/test_plan_metadata.py -q
```

Observable runtime success signals:

- public `weft.spawn.requests` rows do not grow unclaimed while a manager is
  runnable;
- TaskMonitor and heartbeat appear as live singleton services even if manager
  registry freshness is stale;
- `weft status --json` can still use registry/control evidence for status, but
  loss of registry proof does not halt dispatch;
- duplicate singleton services converge to one live owner.

## 7. Verification And Gates

Per-task gates:

- After Task 2, run the updated public dispatch tests in
  `tests/core/test_manager.py`.
- After Task 3, run the internal service and autostart ensure tests in
  `tests/core/test_manager.py`.
- After Task 4, run `rg` for obsolete dispatch-fence names and run ruff.
- After Task 5, run the focused CLI serve tests if foreground duplicate behavior
  was touched.

Final gates before marking the implementation done:

```bash
uv run pytest tests/core/test_manager.py tests/cli/test_cli_serve.py -q
uv run pytest tests/specs/test_plan_metadata.py -q
uv run ruff check weft tests
uv run mypy weft extensions/weft_docker extensions/weft_macos_sandbox
uv run --extra dev --extra docker --extra django --extra macos-sandbox pytest tests -q
PYTEST_ADDOPTS='-x --maxfail=1' PYTEST_XDIST_AUTO_NUM_WORKERS=17 WEFT_EAGER_FAILURE_TRACEBACK=1 uv run --extra dev --extra docker --extra django --extra macos-sandbox bin/pytest-pg --all
```

If the full PG suite is already running in another terminal, do not start a
competing full PG run. Use targeted tests and wait for the active full-suite
result.

## 8. Rollout And Rollback

Rollout is code and spec only. There is no queue migration, payload migration,
or new durable format.

Rollback is straightforward if needed: restore the previous positive-`self`
dispatch fence and dispatch suspension tests. Queue compatibility is preserved
because this plan does not change queue names, message payloads, task IDs, or
state-log schemas.

The main rollback risk is conceptual, not data-shaped: after this lands, any
future code must not rely on "one canonical manager is the only process that
can launch public tasks." Tests and specs should make that impossible to infer.

## 9. Independent Review Loop

Because this changes manager dispatch semantics on the durable execution spine,
run an independent plan review before implementation and a completed-work
review before final handoff.

Recommended plan review prompt:

> Read `docs/plans/2026-05-11-manager-work-stealing-dispatch-plan.md`,
> `weft/core/manager.py`, `weft/core/tasks/multiqueue_watcher.py`,
> `weft/core/manager_services.py`, and the touched spec sections. Look for
> errors, bad ideas, and latent ambiguities. Do not implement anything. Could
> you implement this confidently and correctly if asked?

Reviewer focus:

- whether atomic reserve is enough authority for public dispatch;
- whether singleton duplicate prevention remains strong without global manager
  dispatch ownership;
- whether leadership yield can still wedge useful work;
- whether any old dispatch-suspension code remains conceptually misleading.

## 10. Out Of Scope

- No new manager lease or lock system.
- No new queue names or broker schema.
- No public CLI output or flag changes.
- No redesign of TaskMonitor pruning policy.
- No change to TaskSpec schema.
- No broad status/read-model rewrite beyond wording needed to keep specs
  truthful.
- No attempt to make multiple managers a load-balancing product feature.

## 11. Fresh-Eyes Review Checklist

Before implementation starts, reread this plan and confirm:

- the exact files to read and touch are named;
- public dispatch authority is stated as atomic reserve, not registry
  leadership;
- singleton authority is stated as service-key reducer convergence, not public
  TaskSpec metadata;
- tests use real broker-backed queues;
- old fence tests are explicitly replaced, not weakened without a new
  contract;
- rollback does not require data migration;
- the plan does not ask for speculative abstractions or future-proofing.

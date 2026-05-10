# Control And Service Convergence State Machine Plan

Status: completed
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.4], [MA-1.6a]; docs/specifications/05-Message_Flow_and_State.md [MF-3], [MF-6]; docs/specifications/07-System_Invariants.md [OBS.11]-[OBS.12a], [MANAGER.8], [MANAGER.15]-[MANAGER.16]; docs/specifications/10-CLI_Interface.md [CLI-1.2], [CLI-1.3]
Superseded by: none

## 1. Goal

Engineer out the PG-only singleton restart failures by making task-control
completion and manager-owned service convergence deterministic state machines.
The end state is stronger than "the test usually passes": `weft task kill`
must not report success from acknowledgement alone, and a canonical manager
that observes terminal evidence for an `ensure` singleton must advance through
the existing reducer toward exactly one replacement without relying on SQLite's
low-latency timing.

This plan narrows the relevant parts of
[`2026-05-09-service-liveness-and-health-convergence-plan.md`](./2026-05-09-service-liveness-and-health-convergence-plan.md)
to the exact failure mode seen in
`tests/cli/test_cli_serve.py::test_serve_restarts_singleton_services_without_duplicates`.
That broader draft remains background for heartbeat/status health work; this
plan is the active execution guide for control truthfulness and managed-service
restart convergence.

## 2. Source Documents

Source specs:

- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md)
  [MA-1.4], [MA-1.6a]: manager dispatch ownership and manager-owned internal
  service supervision.
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-3], [MF-6]: task-local control evidence, terminal envelopes, manager
  spawn flow, and `weft.spawn.internal`.
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [OBS.11]-[OBS.12], [MANAGER.8], [MANAGER.15]-[MANAGER.16]: terminal truth
  beats live evidence, keyed PONG is current-state proof only, manager
  ownership converges by lowest live TID, singleton services reduce evidence
  through one transition table, and internal spawn drains before public work.
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
  [CLI-1.2], [CLI-1.3]: task inspection and task-control surfaces.

Related plans and how to read them:

- [`2026-05-08-deterministic-manager-service-reconciler-plan.md`](./2026-05-08-deterministic-manager-service-reconciler-plan.md)
  is completed and defines the reducer direction. Preserve it.
- [`2026-05-09-internal-spawn-priority-queue-plan.md`](./2026-05-09-internal-spawn-priority-queue-plan.md)
  is completed and defines `weft.spawn.internal` priority. Preserve it.
- [`2026-05-09-managed-service-restart-clock-hardening-plan.md`](./2026-05-09-managed-service-restart-clock-hardening-plan.md)
  is completed and defines observation-clock restart backoff. Preserve it.
- [`2026-05-09-service-liveness-and-health-convergence-plan.md`](./2026-05-09-service-liveness-and-health-convergence-plan.md)
  is a broader draft. Do not use it to widen this slice into heartbeat
  endpoint supersession, task-status health UX, or pruning.
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md),
  [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md),
  and [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)
  define the planning, hardening, and review requirements for this risky
  execution-path change.

## 3. Context And Key Files

Files to modify:

- `weft/commands/tasks.py`
- `weft/core/manager.py`
- `weft/core/manager_services.py` only if the existing reducer needs an
  explicit transition or debug result that cannot live in `Manager`
- `weft/_constants.py` only if a new production diagnostic classification or
  small bounded pass constant is required
- `tests/commands/test_task_commands.py`
- `tests/core/test_manager.py`
- `tests/core/test_manager_services.py`
- `tests/cli/test_cli_serve.py`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/lessons.md` if implementation confirms a new durable lesson

Read first:

- `weft/commands/tasks.py`:
  `_send_control`, `_await_control_surface`, `_kill_via_fallback`,
  `_force_kill_task_processes`, `stop_tasks`, and `kill_tasks`.
- `weft/core/tasks/base.py`:
  `_handle_control_command`, `_handle_kill_request`, terminal envelope
  publication, and main-thread STOP/KILL ownership.
- `weft/core/tasks/task_monitor.py`:
  control handling and why TaskMonitor is a persistent `ensure` service.
- `weft/core/manager.py`:
  `_cleanup_children`, `_reconcile_managed_services`,
  `_tick_managed_service`, `_pending_service_keys`,
  `_observed_service_candidates_by_key`, `_enqueue_managed_service_request`,
  `_drain_internal_spawn_requests`, and `process_once`.
- `weft/core/manager_services.py`:
  `ManagedServiceSpec`, `ManagedServiceState`, `ServiceCandidate`,
  `ManagedServiceEvidence`, `reduce_managed_service_state`, and
  `summarize_service_candidates`.
- `weft/core/tasks/multiqueue_watcher.py`:
  `_refresh_active_queues`, `_mark_pending_messages_prechecked`, and
  `wait_for_activity`.
- `tests/cli/test_cli_serve.py`:
  `_service_records`, `_live_service_records`,
  `_wait_for_single_live_service`, and
  `test_serve_restarts_singleton_services_without_duplicates`.
- `tests/helpers/weft_harness.py`:
  process and TID cleanup rules for CLI lifecycle tests.

Comprehension checks before editing:

1. What exact evidence makes `weft task kill TID` truthful: KILL ack, terminal
   task log, terminal `ctrl_out`, process death, or runner-native runtime
   death?
2. When a service TID has terminal proof and also a live-looking PID, which
   evidence wins and where is that rule enforced?
3. Which queue carries built-in manager-owned service spawn work, and what
   prevents a pending service request from becoming a duplicate launch?
4. Which Manager function is allowed to perform side effects after the pure
   service reducer returns `start_now`?
5. Why does SQLite usually pass the failing test while PG under xdist exposes
   the intermediate states?

Current structure:

- `weft task kill` sends `KILL`, waits for control/task surfaces, and can fall
  back to runner or host process termination. The current failure shows this
  path can return `Killed 1 process(es)` before the old TaskMonitor is dead or
  terminal under PG load.
- `BaseTask` publishes control acknowledgements and terminal state from the
  task process. Acknowledgement means "command accepted", not "runtime is
  dead".
- `Manager` already owns one reducer-backed service path for heartbeat,
  TaskMonitor, and autostart. The pure reducer is the right place for
  transition selection; Manager remains the only side-effect owner.
- `weft.spawn.internal` is strict-priority manager-owned service work. Internal
  singleton service requests should reach a launch attempt without waiting
  behind ordinary public user work.
- The failing PG traces prove two distinct intermediate states:
  old service still `running` after `task kill` returned, and old service
  `killed` with no replacement. Both are valid intermediate observations; the
  code must advance from them deterministically.

## 4. Invariants And Constraints

- Do not widen timeouts as the fix. Longer waits may be needed for final PG
  proof, but the implementation must remove the invalid state transition.
- `KILL` acknowledgement is progress evidence only. It must not be converted
  into terminal lifecycle truth.
- Durable terminal task-log status and typed terminal `ctrl_out` envelopes are
  terminal proof. Terminal proof for a TID wins over live PID, runtime handle,
  or PONG evidence for the same TID.
- A host-observable PID or runtime handle can prove current liveness. It cannot
  by itself prove terminal lifecycle state.
- `kill_tasks()` may count a task as killed only after one of these proofs:
  durable terminal status `killed`; typed terminal control proof `killed`;
  host-observable consumer/runtime PIDs are no longer live after a kill action;
  or a runner plugin whose handle has no host-observable PID is the explicit
  authoritative control surface and reports kill success.
- For host-observable handles, runner/plugin kill success is not enough while
  the observed PID is still live.
- CLI stop/kill must target the consumer task process while it is alive. An
  external runtime fallback is only authoritative after the consumer is gone or
  when no host-observable consumer PID exists.
- Keep STOP and KILL semantics separate. This slice tightens KILL success
  truthfulness; do not accidentally make graceful STOP force-kill ordinary
  tasks.
- Manager service supervision must stay on the existing
  `Manager -> launch_task_process -> task queues/state log` spine. Do not add
  a second service launcher, lease table, or backend-specific lock.
- `weft.spawn.internal` remains internal. Do not add public CLI or client write
  surfaces for it.
- Manager-owned singleton convergence must be deterministic over an evidence
  snapshot. Backend speed may affect when evidence appears, not which decision
  is made from the same evidence.
- Pending service spawn evidence blocks duplicate enqueue. A replacement may
  be pending, live, terminal, uncertain, backoff-waiting, budget-exhausted, or
  startable, but it must not be "unknown, so enqueue again".
- Dispatch ownership still gates service launch. Only positive `self` can
  launch; `none`, `unknown`, and positive `other` must not launch.
- No new dependency, no new durable queue name, and no public CLI shape change
  unless a spec update explicitly accepts it.
- Queue history reads for task logs, TID mappings, and manager registry must
  stay generator-based.
- Tests for queue/process behavior should use real broker-backed queues,
  `WeftTestHarness`, or real manager/task processes. Mock only runner plugins,
  process liveness, or time where the boundary is external or nondeterministic.

## 5. Deterministic Algorithms

### 5.1 KILL Convergence State Machine

Inputs collected for one TID:

- `command_sent`: `KILL` was written to `T{tid}.ctrl_in`.
- `ack_seen`: `T{tid}.ctrl_out` contained `{command: KILL, status: ack}`.
- `terminal_seen`: task log or typed terminal `ctrl_out` proves a terminal
  lifecycle status.
- `terminal_status`: the proven terminal status, if any.
- `consumer_pids_live`: host-observable consumer/task PIDs from TID mappings
  or runtime handles are still live.
- `runner_kill_attempted`: fallback runner/plugin kill was invoked.
- `runner_kill_authoritative`: the runner is the only control authority for
  this handle because no host-observable consumer PID exists.
- `force_kill_attempted`: host process tree kill was invoked.

States:

- `Idle`: no command sent.
- `CommandSent`: KILL was durably written.
- `Accepted`: ack seen, but no terminal/dead proof.
- `Terminal`: terminal status is visible.
- `RuntimeDead`: no host-observable controlled PID remains live after a kill
  action.
- `Escalating`: accepted or timed out without terminal/dead proof, so fallback
  kill is in progress.
- `Unknown`: observation budget expired without terminal/dead proof.

Transition table:

| Current | Evidence | Next | Success count? |
| --- | --- | --- | --- |
| `CommandSent` | terminal `killed` | `Terminal` | yes |
| `CommandSent` | terminal non-`killed` | `Terminal` | no for KILL unless spec says it is acceptable |
| `CommandSent` | ack only | `Accepted` | no |
| `Accepted` | terminal `killed` | `Terminal` | yes |
| `Accepted` | host-observable controlled PIDs dead after kill action | `RuntimeDead` | yes |
| `Accepted` | controlled PIDs still live | `Escalating` | no |
| `Escalating` | runner plugin success and no host-observable PID exists | `RuntimeDead` | yes |
| `Escalating` | runner plugin success but host-observable PID still live | `Escalating` | no |
| `Escalating` | process-tree kill succeeds and PIDs become dead | `RuntimeDead` | yes |
| any nonterminal | observation budget expires | `Unknown` | no |

Implementation requirement: encode this as small helper functions in
`weft/commands/tasks.py` unless the code becomes too large. If it becomes too
large, stop and consider a focused `weft/commands/control.py` helper. Do not
put this in `Manager`, `BaseTask`, or runner plugins.

### 5.2 Manager-Owned Service Convergence State Machine

Inputs for each desired service key:

- desired service spec: key, lifecycle, spawn payload, backoff, restart budget
- durable pending spawn evidence from internal/public manager inboxes
- tracked child process evidence
- task-log service evidence
- TID mapping runtime-handle evidence
- keyed PING/PONG evidence when control queues are known
- manager-local terminal TIDs from child reap
- current `ManagedServiceState`
- manager dispatch ownership
- current observation time

States are represented by `ManagedServiceState` plus reducer evidence:

- `NoOwner`: no live owner and no pending request.
- `PendingSpawn`: a trusted service spawn request exists.
- `LiveOwner`: one canonical live service TID exists.
- `TerminalObserved`: terminal proof exists for the active TID.
- `UncertainOwner`: liveness probe error or incomplete control evidence blocks
  a safe duplicate.
- `BackoffWait`: restart is allowed but not due yet.
- `BudgetExhausted`: lifecycle policy suppresses restart.
- `Startable`: no blocker remains and launch authority is positive `self`.

Decision rules:

- Terminal proof wins per TID before canonical-live selection.
- The canonical live owner is the lowest live TID after terminal TIDs are
  removed.
- Non-canonical live owners are convergence work, not a tolerated steady
  state. Manager applies KILL control and, when host PID evidence is available,
  force-reaps the non-canonical process tree.
- A pending spawn request blocks another enqueue for the same key.
- `UncertainOwner` must be bounded and visible; it must not silently turn into
  a duplicate launch.
- `BackoffWait` uses the manager's observation clock, not broker timestamp
  gaps.
- `Startable` may only enqueue after positive `self` dispatch ownership.
- The reducer stays pure. Manager applies side effects after the reducer
  returns.

Convergence loop requirement:

`Manager.process_once()` should have one manager-owned service convergence
operation that can run for bounded passes in a single manager turn:

1. reap exited children and record manager-local terminal TIDs;
2. build the desired service set;
3. collect pending/live/terminal/uncertain evidence once for that set;
4. run `reduce_managed_service_state` per service;
5. apply side effects for `start_now` by writing a spawn request;
6. drain manager-owned internal spawn work without consuming public user work;
7. repeat only if a manager-owned side effect happened or a child was reaped,
   and stop at a small fixed pass limit.

The pass limit prevents busy-loop bugs. The intended guarantee is not "finish
all possible future work in one turn"; it is "when the current evidence says a
manager-owned internal service is terminal and startable, enqueue and advance
that replacement to a launch attempt before ordinary public work can starve it."

## 6. Tasks

### 1. Add red tests that separate ack, terminal proof, process death, and replacement

Outcome: the current PG failure is represented by tests that fail for the
right reason on the current implementation.

Files to touch:

- `tests/commands/test_task_commands.py`
- `tests/core/test_manager.py`
- `tests/cli/test_cli_serve.py`

Read first:

- `weft/commands/tasks.py::_await_control_surface`
- `weft/commands/tasks.py::kill_tasks`
- `tests/commands/test_task_commands.py`
- `tests/cli/test_cli_serve.py`

Required tests:

- Add a command-layer regression showing `_await_control_surface` does not
  classify `KILL` ack plus `running` snapshot as terminal success. This may
  mock `QueueChangeMonitor` and `task_status`; it is testing command-state
  classification, not process lifecycle.
- Add or update a command-layer regression showing `kill_tasks()` does not
  count runner/plugin kill success while host-observable PIDs are still live.
  Mock the plugin and PID liveness only.
- Keep `test_serve_restarts_singleton_services_without_duplicates` as the
  real CLI/process/broker proof. Do not skip it and do not weaken it to accept
  zero live services after kill.
- Add a manager integration regression that simulates terminal evidence for a
  managed internal service and pending public spawn work. Assert service
  replacement is advanced before public work can starve internal convergence.

Stop if:

- The new tests require broad mocks around manager queues or process lifecycle.
  Use real broker-backed queues instead.
- The CLI serve regression is changed to wait for a replacement by sleeping
  longer without proving old-service terminal/dead evidence.

Done when:

- At least one new test fails on the current implementation for a causal
  reason: ack-only success, live-PID counted as killed, or terminal singleton
  not advanced to replacement.

### 2. Make `kill_tasks()` truthful about KILL convergence

Outcome: `weft task kill` returns `Killed N` only after terminal/dead proof, not
from ack alone.

Files to touch:

- `weft/commands/tasks.py`
- `tests/commands/test_task_commands.py`

Implementation:

- Split `_await_control_surface` output interpretation into explicit evidence:
  latest mapping entry, latest snapshot, ack seen, terminal proof, and observed
  runtime/host liveness.
- Remove or replace the current synthetic `control_ack` terminal snapshot path
  for KILL. A KILL ack should extend the observation window or trigger
  fallback; it should not mutate a `running` snapshot into `killed`.
- In `kill_tasks()`, after sending KILL:
  - count success immediately only on terminal `killed`;
  - if ack/running/no-terminal appears, fall through to fallback kill;
  - if a runner plugin reports success but host-observable controlled PIDs are
    still live, keep observing or escalate instead of counting success;
  - if process-tree kill succeeds, recheck liveness before counting success.
- Preserve external runner behavior when no host-observable consumer PID
  exists and the persisted runtime handle says the runner plugin is the
  explicit control authority.
- Keep STOP behavior separate. Do not make graceful STOP wait for KILL-style
  process death unless an existing test proves that is already the contract.

Stop if:

- The implementation starts inventing public terminal state in
  `task_status()` to make `kill_tasks()` easier.
- The change bypasses the consumer process while the consumer PID is still
  alive.
- The code starts depending on SQLite-specific immediacy or PG-specific
  sleeps.

Done when:

- New command-layer KILL convergence tests pass.
- Existing external-runner stop/kill tests still pass.
- `weft task kill` still prints the same public CLI shape, but the count is now
  based on proof rather than ack.

### 3. Make manager-owned internal service convergence bounded and monotonic

Outcome: once the manager has evidence that a built-in `ensure` service is
terminal and restart is due, the same manager turn advances toward replacement
without waiting for incidental public queue activity.

Files to touch:

- `weft/core/manager.py`
- `weft/core/manager_services.py` only if the reducer needs a small explicit
  result field
- `tests/core/test_manager.py`
- `tests/core/test_manager_services.py`

Implementation:

- Keep `reduce_managed_service_state` as the only service transition selector.
- Add a manager-side convergence helper, likely in `Manager`, with a name such
  as `_converge_managed_services` or `_run_managed_service_convergence`.
- The helper should:
  - call `_cleanup_children()` and treat any reap as a state change;
  - reconcile built-in internal services when internal service supervision is
    allowed;
  - respect existing autostart scan throttling and not run ensure autostart
    while dispatch-suspension recovery must protect a fenced request;
  - write at most one pending spawn request per service key per pass;
  - drain `weft.spawn.internal` after manager-authored internal service
    enqueues, without consuming ordinary public spawn work;
  - repeat for a small bounded number of passes only when a service side effect
    or child reap happened.
- Preserve the final ownership fence in `_handle_work_message` /
  `_apply_final_dispatch_fence`. The convergence helper may enqueue work, but
  launch still goes through the normal manager spawn path.
- Preserve `_pending_service_keys()` duplicate suppression across both
  internal and public spawn queues, including legacy service requests.

Algorithmic guarantee to enforce in tests:

- Given positive `self` ownership, no pending request, terminal proof for the
  active TaskMonitor TID, and elapsed backoff, `process_once()` must leave
  either a replacement `task_spawned` event or a pending trusted service spawn
  request. It must not remain in a no-live/no-pending state.
- Given a trusted pending service spawn request, `process_once()` must not
  enqueue another request for the same key.
- Given old service terminal proof and a replacement live candidate, the
  reducer must choose the replacement and not count the old TID as live.

Stop if:

- The helper starts launching service children directly instead of using the
  manager spawn queue and `_launch_child_task`.
- The implementation consumes public spawn work inside an internal-service
  convergence drain.
- The change special-cases only TaskMonitor. The fix must cover heartbeat and
  TaskMonitor as manager-owned internal services, and must not regress
  autostart `ensure`.

Done when:

- Pure reducer tests cover terminal-vs-live same-TID precedence, pending-spawn
  duplicate suppression, replacement live winner, uncertain wait, and backoff.
- Manager integration tests prove terminal internal service evidence advances
  to replacement before public work starvation.

### 4. Tighten the CLI serve regression and diagnostics

Outcome: if the end-to-end service replacement test fails again, the failure
prints the state-machine evidence needed to classify the bug without another
long investigation.

Files to touch:

- `tests/cli/test_cli_serve.py`
- Possibly `tests/helpers/weft_harness.py` if a reusable debug helper is
  clearly better than local test code

Implementation:

- Keep the test's visible contract:
  after killing TaskMonitor and heartbeat, exactly one replacement for each
  service becomes live and it is not the old TID.
- On timeout, include:
  - all service records for the key;
  - live records after keyed PING/PID checks;
  - pending messages in `weft.spawn.internal` and `weft.spawn.requests` that
    carry matching service metadata;
  - manager registry active records;
  - manager process liveness and recent manager stdout/stderr if available.
- Do not assert private manager state from the external CLI serve process.
  Use queues and control surfaces.

Stop if:

- The diagnostics require a production CLI output change just for this test.
- The test starts accepting "no live services" after kill as success.

Done when:

- The test fails with enough queue-visible state to distinguish:
  old service still live, terminal old service with no pending replacement,
  pending replacement not launched, replacement launch rejected, and duplicate
  live owners.

### 5. Update specs and lessons

Outcome: the normative docs say the same thing as the implementation.

Files to touch:

- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/lessons.md`

Implementation:

- In [MF-3], clarify that KILL ack is command-acceptance proof only and is not
  terminal proof.
- In [CLI-1.2]/[CLI-1.3], clarify that `task kill` success counts are based on
  terminal/dead proof, not ack alone.
- In [MA-1.6a] and [MANAGER.15]-[MANAGER.16], clarify the bounded convergence
  guarantee for manager-owned internal services after terminal evidence.
- Add a lesson if the final implementation confirms the durable rule:
  "Postgres exposes proof-boundary gaps hidden by SQLite; do not treat ack,
  queue write, or wakeup as convergence."

Stop if:

- The spec update starts describing behavior the implementation does not yet
  provide.
- The lesson duplicates an existing entry without adding a new durable rule.

Done when:

- Spec backlinks point to this plan.
- Existing implementation mappings remain accurate.

### 6. Independent review

Outcome: a different reviewer can implement the plan without guessing.

Reviewer availability:

- Prefer Claude, Gemini, Qwen, or another non-authoring agent family if
  available in the implementation environment.
- If no independent reviewer is available, record that limitation in the
  implementation notes before starting risky code changes.

Review prompt:

> Read `docs/plans/2026-05-10-control-and-service-convergence-state-machine-plan.md`.
> Carefully examine the plan, the governing specs, and these files:
> `weft/commands/tasks.py`, `weft/core/manager.py`,
> `weft/core/manager_services.py`, `tests/cli/test_cli_serve.py`, and
> `tests/commands/test_task_commands.py`. Look for errors, bad ideas, and
> latent ambiguities. Do not implement. Could you implement this confidently
> and correctly if asked?

Required response:

- Address every reviewer finding explicitly.
- Update the plan before implementation if the reviewer finds an ambiguity in
  proof boundaries, side-effect ownership, or tests.

## 7. Testing Plan

Red-green expectations:

- Red first is practical for the command-layer KILL proof boundaries and the
  manager reducer/state transition cases. Write those tests before changing
  implementation.
- For the full PG xdist failure, red-first is already demonstrated by
  `bin/pytest-pg --all` failures. Keep the existing CLI serve test as the
  end-to-end proof rather than trying to make a separate deterministic mock of
  PG load.

Per-task verification:

```bash
./.venv/bin/python -m pytest tests/commands/test_task_commands.py -q
./.venv/bin/python -m pytest tests/core/test_manager_services.py -q
./.venv/bin/python -m pytest tests/core/test_manager.py -q
./.venv/bin/python -m pytest tests/cli/test_cli_serve.py::test_serve_restarts_singleton_services_without_duplicates -q
```

PG-backed verification:

```bash
PYTEST_ADDOPTS='-x --maxfail=1' PYTEST_XDIST_AUTO_NUM_WORKERS=17 WEFT_EAGER_FAILURE_TRACEBACK=1 \
uv run --extra dev --extra docker --extra django --extra macos-sandbox \
bin/pytest-pg tests/cli/test_cli_serve.py::test_serve_restarts_singleton_services_without_duplicates
```

Final gates:

```bash
./.venv/bin/python -m ruff check weft tests
./.venv/bin/python -m mypy weft extensions/weft_docker extensions/weft_macos_sandbox
PYTEST_ADDOPTS='-x --maxfail=1' PYTEST_XDIST_AUTO_NUM_WORKERS=17 WEFT_EAGER_FAILURE_TRACEBACK=1 \
uv run --extra dev --extra docker --extra django --extra macos-sandbox bin/pytest-pg --all
```

What not to mock:

- Do not mock manager-owned service queue writes in the integration tests.
- Do not mock `weft.spawn.internal` drain behavior.
- Do not mock `test_cli_serve.py` process lifecycle.
- Do not mock task logs or TID mappings in tests whose purpose is broker
  visibility.

Acceptable mocks:

- Runner plugin return values for command-layer fallback behavior.
- PID liveness when testing `kill_tasks()` classification without starting a
  real process.
- Time/clock only for backoff pure reducer tests.

## 8. Verification And Gates

Done means all of these are true:

- `weft task kill` no longer reports a KILL ack as terminal success.
- Host-observable live PIDs prevent `kill_tasks()` from counting plugin kill
  success until they are dead or terminal proof appears.
- Manager-owned internal service convergence advances terminal TaskMonitor and
  heartbeat evidence toward one replacement through the existing reducer and
  spawn path.
- Pending service spawn evidence suppresses duplicate enqueue across internal
  and public spawn queues.
- The CLI serve PG target passes in isolation.
- Full `bin/pytest-pg --all` passes under 17 xdist workers.
- Specs and plan backlinks are updated.

Runtime observation signal after landing:

- Under `WEFT_TASK_MONITOR_ENABLED=1`, repeatedly killing the current
  TaskMonitor and heartbeat should show exactly one live replacement per
  service key and no accumulation of manager-owned service requests in
  `weft.spawn.internal`.

Rollback:

- No persisted payload shape, queue name, or CLI output shape should change.
  Rollback is a code revert if tests expose an issue.
- If a spec clarification lands with the code, revert spec text with the code
  if the behavior is reverted.
- Do not add a migration or cleanup that would make rollback depend on
  durable data transformation.

One-way doors:

- None intended. If implementation proposes a new queue, persisted field, or
  incompatible CLI output, stop and re-plan.

## 9. Out Of Scope

- No new service-health dashboard.
- No heartbeat endpoint supersession redesign beyond what is required by the
  service convergence tests.
- No pruning or retention changes.
- No SimpleBroker backend changes unless a minimal reproduction proves the
  backend violates its documented queue contract.
- No public CLI option changes.
- No new dependency.
- No broad refactor of `Manager`, `BaseTask`, or `TaskMonitorTask`.

## 10. Fresh-Eyes Review Checklist

Before implementation starts, re-read this plan and verify:

- every task names exact files and tests;
- the KILL proof boundary is explicit;
- the service reducer remains pure and Manager owns side effects;
- internal spawn convergence cannot consume public user work;
- tests use real broker/process paths where that matters;
- rollback is a code revert, not a data cleanup;
- the plan does not silently widen into the broader service-health draft.

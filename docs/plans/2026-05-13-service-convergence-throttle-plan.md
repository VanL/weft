# Service Convergence Throttle Plan

Status: draft
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.6a]; docs/specifications/05-Message_Flow_and_State.md [MF-6]; docs/specifications/07-System_Invariants.md [MANAGER.3], [MANAGER.8]
Superseded by: [2026-05-15-manager-hot-loop-reduction-plan.md](./2026-05-15-manager-hot-loop-reduction-plan.md)

## 1. Goal

Reduce sustained manager, heartbeat, and TaskMonitor CPU caused by aggressive
supervisory proof loops, without slowing real task dispatch or control
handling. The scoped change moves managed service-key convergence and
`weft.state.services` evidence checks from 100 ms active polling to a 1 second
active interval, keeps stable audits at 5 seconds, and adds enough
active-reason/progress evidence to explain why convergence remains active when
it is not making progress.

This plan does not address the known `weft status --json` full-history replay
issue. It treats status pressure as out of scope.

## 2. Source Documents

- [docs/specifications/03-Manager_Architecture.md](../specifications/03-Manager_Architecture.md)
  [MA-1.6a]: Manager-owned heartbeat and TaskMonitor supervision, including
  `Manager._run_managed_service_convergence`, service-key evidence, internal
  spawn drains, and `Manager.wait_for_activity`.
- [docs/specifications/05-Message_Flow_and_State.md](../specifications/05-Message_Flow_and_State.md)
  [MF-6]: manager spawn-request flow and internal service convergence on the
  manager-owned spawn path.
- [docs/specifications/07-System_Invariants.md](../specifications/07-System_Invariants.md)
  [MANAGER.3], [MANAGER.8]: manager service-owner rows in
  `weft.state.services`, leadership convergence, and the rule that unknown
  external evidence must not force destructive or voluntary-yield decisions.
- [docs/plans/2026-05-10-control-and-service-convergence-state-machine-plan.md](./2026-05-10-control-and-service-convergence-state-machine-plan.md):
  completed plan that introduced the current service convergence state machine.
- [docs/plans/2026-05-11-internal-service-observability-plan.md](./2026-05-11-internal-service-observability-plan.md):
  draft/active context for operational visibility of heartbeat and TaskMonitor
  services.
- [docs/plans/2026-05-11-service-convergence-and-manager-registry-bounding-plan.md](./2026-05-11-service-convergence-and-manager-registry-bounding-plan.md):
  draft context for service registry bounding. This plan is narrower: it does
  not change the service-owner schema or registry authority model.

## 3. Context and Key Files

Files to modify:

- `weft/_constants.py`: change the active managed-service convergence interval
  from 0.1 seconds to 1.0 second. Keep
  `MANAGED_SERVICE_STABLE_AUDIT_INTERVAL_SECONDS` at 5.0 seconds unless a test
  proves that constant is already wrong for this slice.
- `weft/core/manager.py`: keep immediate work drains, but rate-limit
  supervisory service evidence checks and add active-reason/progress fields to
  existing manager service logs.
- `weft/core/tasks/heartbeat.py`: if needed, add a 1 second cache or throttle
  for idle ownership checks that call `resolve_endpoint()`. Do not cache across
  registration handling or due heartbeat emits unless the code explicitly
  revalidates before emitting.
- `weft/core/tasks/task_monitor.py`: if needed, adjust retry/progress
  reporting around heartbeat registration failures. The existing retry cap is
  already 1 second, so do not change it without evidence.
- `tests/core/test_manager.py`: add or update manager scheduling tests.
- `tests/tasks/test_heartbeat.py`: add only if heartbeat ownership caching is
  included.
- `tests/tasks/test_task_monitor.py`: add only if TaskMonitor retry/progress
  behavior changes.
- `docs/specifications/03-Manager_Architecture.md`: keep implementation-plan
  backlink synchronized.

First implementation slice:

- Required: tasks 1-3.
- Conditional only after observing evidence from task 3: tasks 4-6.
- Do not implement per-service proof caches or heartbeat ownership caches in
  the first slice unless a targeted test proves that the 1 second convergence
  interval alone still permits faster repeated proof work.

Read first:

- `weft/core/manager.py`:
  `Manager.process_once`, `Manager.wait_for_activity`,
  `Manager._run_managed_service_convergence`,
  `Manager._managed_service_convergence_active`,
  `Manager._reconcile_managed_services`,
  `Manager._pending_service_keys`,
  `Manager._observed_service_candidates_by_key`,
  `Manager._drain_internal_spawn_requests`, and
  `Manager._has_actionable_leadership_work`.
- `weft/core/manager_services.py`: `ManagedServiceState`,
  `ManagedServiceDecision`, and `reduce_managed_service_state`.
- `weft/core/service_convergence.py`: service-owner reduction and pruning over
  `weft.state.services`.
- `weft/core/tasks/heartbeat.py`: `HeartbeatTask.process_once`,
  `_service_ownership`, `_emit_due_registrations`, and `_wait_for_activity`.
- `weft/core/tasks/task_monitor.py`: `TaskMonitorTask.next_wait_timeout`,
  `_ensure_heartbeat_registered`, and `_run_monitor_cycle`.
- `tests/core/test_manager.py` around existing managed-service convergence
  tests.

Comprehension questions before editing:

- Which parts of `Manager.process_once` consume or launch real queued work, and
  which parts only prove service liveness or registry ownership?
- Which active convergence reasons come from a durable queue row, and which
  come from manager-local state such as `spawn_pending`, `active_tid`, or
  `uncertain_attempts`?
- What should still run immediately after `_cleanup_children()` observes a
  child exit?

## 4. Invariants and Constraints

- Do not slow control handling. STOP, KILL, PING, and STATUS must still be
  handled through the existing control queue path.
- Do not slow actual spawn queue drains. Once public or internal spawn work is
  reserved or drainable, the manager should continue launching work promptly.
- Do not change queue names, message payload schemas, TaskSpec fields, result
  payloads, or public CLI output shape in this slice.
- Do not add a second manager loop or scheduler abstraction. Extend the current
  `Manager.process_once` and convergence paths.
- Keep `weft.state.*` queues runtime-only. This plan changes polling cadence
  and visibility only.
- Keep generator-based history reads for append-style runtime queues. Do not
  replace them with fixed-limit peeks for correctness.
- `force=True` convergence remains available for concrete progress events such
  as child exit cleanup. The throttle applies to repeated supervisory proof
  loops, not to known state transitions.
- Observability failures are best effort. A failed service log write must not
  change manager behavior.
- No new dependency.

Out of scope:

- Reworking `weft status --json`.
- Redesigning the service-owner schema.
- Changing task-monitor cleanup policy.
- Replacing SimpleBroker waiting or queue primitives.
- Broad progress-aware backoff across every manager queue. That may be a later
  slice if interval throttling does not explain the CPU.

## 5. Obvious Hot Loops To Slow

The primary hot path is:

```text
Manager.process_once
  -> Manager._run_managed_service_convergence
  -> Manager._managed_service_convergence_active
  -> Manager._reconcile_managed_services
  -> Manager._pending_service_keys / _observed_service_candidates_by_key
  -> weft.state.services replay and optional PING fallback
```

Today this path can run at the active convergence interval of 100 ms whenever
service state looks active. That interval is too low for proof work over
`weft.state.services`.

Slow these surfaces to a 1 second active interval:

- service-key convergence checks for heartbeat and TaskMonitor
- `weft.state.services` replay/prune performed for service evidence
- duplicate-service scans after the first forced pass
- repeated uncertain service liveness checks and PING fallback

For the first slice, prefer the existing convergence interval gate over new
per-service caches. A per-key uncertain-proof throttle is a second-slice tool
only if logs still show repeated unchanged proof work faster than the active
interval.

Do not slow these surfaces:

- control queue handling
- child process reaping
- public/internal spawn queue drains
- reserved-queue policy handling
- forced convergence immediately after a child exits

Secondary candidates:

- `HeartbeatTask._service_ownership()` calls `resolve_endpoint()`, which can
  replay endpoint, task-status, and TID-mapping evidence. If heartbeat CPU
  remains high after manager throttling, cache idle ownership results for about
  1 second and invalidate on registration/control handling.
- `TaskMonitorTask._ensure_heartbeat_registered()` may retry endpoint
  resolution once per second on failure. That is already in the desired cadence;
  add evidence before changing it.

## 6. Tasks

1. Change the active convergence interval.

   Outcome: `MANAGED_SERVICE_CONVERGENCE_INTERVAL_SECONDS` becomes 1.0.

   Files:
   - `weft/_constants.py`
   - `tests/core/test_manager.py`

   Requirements:
   - Keep `MANAGED_SERVICE_STABLE_AUDIT_INTERVAL_SECONDS` at 5.0.
   - Update tests that assume 0.1 seconds.
   - Do not make the interval configurable in this slice.

   Verification:
   - A manager test should prove stable service convergence skips until the
     active interval expires when there is active-but-non-progress service
     state.

2. Preserve immediate convergence after real progress.

   Outcome: child exits and drainable internal spawn work still advance without
   waiting for the 1 second service proof interval.

   Files:
   - `weft/core/manager.py`
   - `tests/core/test_manager.py`

   Requirements:
   - Keep the existing forced convergence after `_cleanup_children()` returns
     true.
   - Keep `_drain_internal_spawn_requests()` in the main turn.
   - If a test reveals that the new interval delays an actual internal spawn
     drain, fix the call ordering rather than lowering the interval.

   Verification:
   - Test that pending internal spawn work is drained in the same manager turn.
   - Test that `_run_managed_service_convergence(force=True)` still bypasses
     the interval.

3. Add active-reason and coarse progress observability for service convergence.

   Outcome: manager service logs explain why convergence is active and whether
   the turn changed anything meaningful.

   Files:
   - `weft/core/manager.py`
   - `tests/core/test_manager.py`

   Required fields, using existing serve-log plumbing:
   - `active_reasons`: stable list of reasons such as
     `internal_spawn_pending`, `internal_spawn_enqueued`,
     `duplicate_scan_pending`, `spawn_pending`, `missing_active_tid`,
     `uncertain_attempts`, `autostart_scan_due`.
   - `progress`: boolean for the convergence pass.
   - `progress_reasons`: stable list such as `child_exited`,
     `service_request_enqueued`, `internal_spawn_drained`,
     `active_tid_changed`, `spawn_pending_changed`,
     `uncertain_state_changed`, `registry_pruned`.

   Constraints:
   - Do not add a new logging system.
   - Do not log per-loop noisy detail at info level. Reuse the existing
     rate-limited serve-log helpers.
   - Keep progress accounting coarse unless the code already has the signal.
     It is acceptable for the first slice to report progress from
     `_cleanup_children()` and internal drain counts, plus state snapshots
     before/after `_tick_managed_service`.
   - Do not refactor `_tick_managed_service` or
     `reduce_managed_service_state` only to make perfect progress accounting
     easier. If exact progress accounting becomes invasive, stop and review
     before broadening the slice.

   Verification:
   - Unit test the helper that derives active reasons from manager state.
   - Existing serve-log tests, if any, should only assert stable keys, not the
     full log blob.

4. Conditional follow-up: rate-limit repeated uncertain service proof.

   Implement this only if tasks 1-3 show unchanged uncertain service evidence
   still triggers expensive proof work faster than the 1 second active
   interval.

   Outcome: a service key with unchanged uncertain evidence does not run
   expensive proof work faster than 1 second.

   Files:
   - `weft/core/manager.py`
   - `tests/core/test_manager.py`

   Requirements:
   - Use manager-local monotonic/wall-clock state. Do not persist a new queue
     row for throttling.
   - Cache by service key, and include owner TID when a TID-specific PING or
     runtime proof is involved.
   - Reset throttling immediately when a child exits, a spawn request is
     enqueued, internal spawn work drains, or service state changes.
   - Do not suppress a terminal or live decision once visible.
   - Do not add this cache just to satisfy the plan if the interval change
     already bounds the proof path.

   Verification:
   - Test that repeated uncertain evidence does not call the expensive
     candidate-observation path more than once within the active interval.
   - Test that a forced pass bypasses the throttle.
   - Avoid tests that sleep for a real second. Use monkeypatching or direct
     timestamp manipulation for manager-local clocks.

5. Optional follow-up: throttle idle heartbeat ownership checks.

   Implement this only if manager throttling leaves heartbeat CPU high or a
   targeted test shows repeated idle calls to `resolve_endpoint()`.

   Outcome: idle heartbeat ownership checks are cached for about 1 second.

   Files:
   - `weft/core/tasks/heartbeat.py`
   - `tests/tasks/test_heartbeat.py`

   Requirements:
   - Invalidate the cache after handling registration input, control input, or
     ownership-changing evidence.
   - Revalidate ownership before emitting due heartbeat messages unless the
     cached result is still inside the 1 second TTL.
   - Do not cache a superseded result in a way that delays shutdown beyond the
     TTL.

   Verification:
   - Test repeated idle checks call `resolve_endpoint()` once inside the TTL.
   - Test due emit still validates ownership and emits when owner is self.
   - Avoid real-time TTL sleeps; drive the clock or cached timestamp directly.

6. Optional follow-up: expose TaskMonitor retry reason if CPU remains high.

   Implement this only if TaskMonitor CPU remains high after the manager slice.

   Outcome: TaskMonitor control/status exposes whether it is spinning on
   heartbeat registration retry, runtime input, or cleanup scan.

   Files:
   - `weft/core/tasks/task_monitor.py`
   - `tests/tasks/test_task_monitor.py`

   Requirements:
   - Do not change cleanup policy.
   - Do not reduce the task-monitor interval below the configured value.
   - Keep heartbeat registration retry at 1 second unless evidence shows the
     retry body itself is still too expensive.

   Verification:
   - Test control snapshot includes enough last-cycle/heartbeat error evidence
     to distinguish retry from cleanup work.

## 7. Test Plan

Run the smallest targeted tests first:

```bash
./.venv/bin/python -m pytest tests/core/test_manager.py -k "managed_service or internal_spawn or convergence"
./.venv/bin/python -m pytest tests/tasks/test_heartbeat.py -k "heartbeat"
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py -k "task_monitor"
```

Then run broader verification:

```bash
./.venv/bin/python -m pytest tests/core/test_manager.py tests/tasks/test_heartbeat.py tests/tasks/test_task_monitor.py
./.venv/bin/ruff check weft tests
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
```

If the local environment does not have a synced `.venv`, follow the repo
instructions in `AGENTS.md`: load `.envrc`, run `uv sync --all-extras`, then
use the in-repo virtualenv binaries.

Do not substitute mock-only tests for manager queue behavior when a real
broker-backed test is practical.

Interval tests should not wait for real 1 second delays. Prefer monkeypatching
`time.time_ns()` / `time.monotonic()` at the local seam, or setting manager
timestamp fields directly, so the test proves scheduling behavior without
slowing the suite.

## 8. Rollout and Runtime Observation

Rollout is low risk because this slice changes cadence and local telemetry, not
persisted formats or queue contracts. It can be reverted by restoring the
constant and removing the local throttle/telemetry code.

After rollout, observe:

- manager CPU should drop when no spawn/control work is being consumed
- heartbeat and TaskMonitor CPU should stay near idle between scheduled work
- internal service replacement latency may increase to about 1 second
- `weft.state.services` replay/query rate should fall materially
- service logs should show the active reason when convergence continues

If manager CPU remains high and logs show repeated `progress=false` with the
same active reason, do not lower the interval again. Investigate the stuck
predicate named by `active_reasons`.

## 9. Review Gates

Self-driven fresh-eyes review is required before implementation. The review
must look for latent ambiguity, bad ideas, and whether a zero-context engineer
could implement the plan correctly without slowing real spawn/control paths by
mistake.

External review is warranted before implementation because this plan touches
manager scheduling and runtime service convergence. If the reviewer is a
different agent, expect the review to take 5-10 minutes. Do not treat that wait
as a reason to skip review.

Review status:

- Self-review: completed 2026-05-13. Findings addressed: first-slice scope was
  ambiguous around per-service uncertain-proof caching; progress accounting was
  too easy to over-refactor; interval tests needed an explicit no-real-sleep
  constraint.
- External review: attempted 2026-05-13 via local `codex exec`, but the local
  wrapper failed with a missing vendor binary (`ENOENT`). Implementation may
  proceed only by explicit user override or after another external reviewer is
  available.

Implementation status:

- First required slice implemented 2026-05-13 by explicit user override after
  the external-review attempt failed locally.
- Optional follow-ups 4-6 were not implemented; they remain conditional on
  runtime evidence after observing the 1 second convergence interval and
  active-reason/progress logs.

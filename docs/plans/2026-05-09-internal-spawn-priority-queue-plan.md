# Internal Spawn Priority Queue Plan

Status: completed
Source specs: docs/specifications/00-Quick_Reference.md; docs/specifications/03-Manager_Architecture.md [MA-1], [MA-2], [MA-3]; docs/specifications/05-Message_Flow_and_State.md [MF-6]; docs/specifications/07-System_Invariants.md [MANAGER.8]-[MANAGER.15]
Superseded by: none

## 1. Goal

Add `weft.spawn.internal` as a manager-consumed priority spawn queue for
manager-owned internal service work. The manager must drain internal spawn work
before ordinary `weft.spawn.requests` work once manager identity has settled,
and must continue to prefer internal work during normal operation whenever both
queues have pending messages.

This plan exists because the reducer and singleton-service work made service
selection deterministic, but production showed that service spawn intent can
still sit behind ordinary spawn backlog. A deterministic service reconciler is
not enough if its side effect is written into the same FIFO path as user work.
The missing contract is queue priority at the manager dispatch boundary.

Requested semantics:

- the canonical manager monitors `weft.spawn.internal` and
  `weft.spawn.requests`;
- after manager identity settles, `weft.spawn.internal` is drained before
  `weft.spawn.requests`;
- during ordinary operation, pending internal spawn work is handled before
  ordinary public spawn work;
- internal and public spawn requests share the same validation, final dispatch
  fence, child launch, task-log, and acknowledgement path;
- this slice does not introduce `weft.spawn.autostart`.

## 2. Source Documents

- `docs/specifications/00-Quick_Reference.md`: global queue catalogue and dump
  persistence expectations. Add `weft.spawn.internal` here.
- `docs/specifications/03-Manager_Architecture.md` [MA-1], [MA-2], [MA-3]:
  manager spawn consumption, TID correlation, manager registry/control
  semantics.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-6]: manager spawn
  flow and reserved-message recovery.
- `docs/specifications/07-System_Invariants.md` [MANAGER.8]-[MANAGER.15]:
  manager ownership, dispatch fences, requeue guarantees, and singleton
  service convergence.
- `docs/agent-context/runbooks/writing-plans.md`: plan format and
  zero-context implementation detail.
- `docs/agent-context/runbooks/hardening-plans.md`: risky runtime path
  hardening checklist.
- `docs/agent-context/runbooks/testing-patterns.md`: real broker/process
  testing guidance.
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`:
  independent review expectations for cross-cutting runtime changes.

## 3. Current Shape

The current manager has one reserve-mode spawn source:

```text
weft.spawn.requests
  -> T{manager_tid}.reserved
  -> Manager._handle_work_message
  -> Manager._build_child_spec
  -> Manager._apply_final_dispatch_fence
  -> Manager._launch_child_task
  -> reserved ack/delete
```

The manager queue watcher is `MultiQueueWatcher`, which currently does one
round-robin scheduling pass over active queues. Adding another inbox to
`Manager._build_queue_configs()` is not enough. Dict order would be an implicit
contract, and round-robin would still allow public work to interleave with
internal work.

The final dispatch fence also assumes one source queue:

- `_requeue_reserved_spawn_request()` always moves exact reserved work back to
  `WEFT_SPAWN_REQUESTS_QUEUE`;
- `_apply_final_dispatch_fence()` records only `message_id` and the manager's
  default reserved queue;
- `_handle_work_message()` deletes acknowledgements from the manager's default
  reserved queue;
- reserved-policy helpers in `BaseTask` assume one inbox and one reserved
  queue.

Those assumptions must be made source-aware before a second reserve-mode spawn
source is safe.

## 4. Contract

### 4.1 Queue Contract

Add this global queue:

```text
weft.spawn.internal
```

It is durable spawn intent, not runtime state. It is persisted in dumps for the
same reason `weft.spawn.requests` is persisted: a queued spawn request is work
the manager has not yet consumed.

Only manager-owned internal service reconciliation writes to this queue in this
slice. Public CLI, client, stored specs, and autostart manifests continue to
write to the existing paths.

### 4.2 Manager Scope

Only a canonical/global manager whose configured inbox is
`WEFT_SPAWN_REQUESTS_QUEUE` should attach `weft.spawn.internal`.

Managers with a custom inbox continue to consume their configured inbox only.
That keeps test/scoped managers from unexpectedly draining a global internal
queue and preserves existing custom-manager behavior.

### 4.3 Priority Semantics

Priority applies at manager dispatch, not at SimpleBroker storage.

When the manager is allowed to launch children and has no unresolved dispatch
suspension:

1. If `weft.spawn.internal` has pending messages, consume internal messages
   until that queue is empty.
2. Only then consume ordinary `weft.spawn.requests` work.
3. If new internal work appears while ordinary work is pending, the next
   manager scheduling pass must return to internal first.

This is strict priority, not weighted fairness. The starvation risk is accepted
because internal writers are manager-owned and bounded to singleton service
launch intent. Do not expose public writes to `weft.spawn.internal` in this
slice.

### 4.4 Shared Launch Path

Both queues must use the same core manager path:

```text
reserve -> parse/validate -> build child spec -> final ownership fence
-> launch child -> write initial inbox message -> ack reserved message
```

Do not create a special "internal launcher". The priority queue is only a
different source of spawn requests. Once a message is reserved, it is ordinary
manager spawn work with an explicit source queue and reserved queue.

### 4.5 Reserved Queue Contract

Use a distinct reserved queue for internal spawn work:

```text
weft.spawn.requests   -> T{manager_tid}.reserved
weft.spawn.internal   -> T{manager_tid}.internal_reserved
```

Do not share one reserved queue. A shared reserved queue loses source identity
during stop/error recovery. With separate reserved queues, the manager can
requeue exact stranded work to the correct origin even when the recovery path is
not inside the original handler frame.

### 4.6 Fence and Recovery Contract

Every fenced or suspended spawn request must retain:

- `source_queue`
- `reserved_queue`
- `message_id`
- `child_tid`

If a lower-TID manager is positively proved after reservation but before launch,
the exact message must be moved back to its `source_queue`, not blindly to
`weft.spawn.requests`. If the exact requeue fails, the stranded diagnostic must
include both source and reserved queue names.

### 4.7 Service Reconciliation Contract

Heartbeat and TaskMonitor manager-owned spawn requests should be written to
`weft.spawn.internal` when the manager has the internal queue attached.

Pending-service evidence must scan both queues during the rollout window:

- `weft.spawn.internal`
- the manager's ordinary inbox, normally `weft.spawn.requests`

This avoids duplicate service launches when an older build already wrote a
keyed service request to `weft.spawn.requests` before this change was deployed.

Autostart remains out of scope for this slice. It continues to use the existing
manager inbox/scoped inbox path. A future `weft.spawn.autostart` must get its
own plan and must not be slipped into this implementation.

### 4.8 Observability Contract

Manager registry and control snapshots may add an additive field such as
`internal_requests: "weft.spawn.internal"` when the internal queue is attached.

Do not change the existing `requests` field. It remains the public manager
request queue used by existing lifecycle readers and selection logic.

Task-log events for fenced/requeued/stranded manager spawn requests should add
`source_queue` and keep `reserved_queue`.

## 5. Non-Goals

- No `weft.spawn.autostart`.
- No cleanup enablement.
- No public CLI flag for writing to `weft.spawn.internal`.
- No SimpleBroker change.
- No change to TID format or TID assignment. The child TID remains the
  SimpleBroker message timestamp of the queue that accepted the spawn request.
- No new manager type or internal-service-specific launcher.

## 6. Implementation Tasks

### Task 1: Add Red Tests for Priority Scheduling

Owner: `MultiQueueWatcher` scheduling behavior.

Files:

- `tests/tasks/test_multiqueue_watcher.py`

Add tests before changing implementation:

1. `test_priority_queue_drains_before_lower_priority`
   - Use real broker queues, not mocks.
   - Configure two reserve-mode queues with different priorities.
   - Write at least three high-priority messages and two normal-priority
     messages.
   - Handlers should record `(queue_name, timestamp, body)` and delete from the
     passed `context.reserved_queue_name`.
   - One call to the watcher drain path should process all high-priority
     messages before the first normal-priority message.
   - Assert the recorded order exactly.

2. `test_equal_priority_preserves_existing_round_robin_behavior`
   - Configure two queues with no explicit priority or the same priority.
   - Assert existing round-robin behavior is unchanged.

3. `test_priority_drain_stops_when_high_priority_queue_is_empty`
   - High-priority queue starts empty, normal queue has work.
   - Assert normal work is processed and the watcher does not spin waiting for
     high-priority work.

Test-design guidance:

- Do not mock `Queue`.
- Do not assert private iterator internals.
- Assert observable handler order and queue emptiness.
- Keep timing out of these tests. Use direct drain/process calls and bounded
  message counts.

### Task 2: Add Explicit Queue Priority Support

Owner: shared queue watcher.

Files:

- `weft/core/tasks/multiqueue_watcher.py`
- `weft/core/tasks/base.py`
- `weft/_constants.py`

Required changes:

1. Add production priority constants in `weft/_constants.py`, for example:
   - `QUEUE_PRIORITY_INTERNAL`
   - `QUEUE_PRIORITY_NORMAL`

   Keep values simple integers. Lower value means higher priority.

2. Add `priority: int = QUEUE_PRIORITY_NORMAL` to `QueueRuntimeConfig`.

3. Parse optional `"priority"` from queue config dictionaries in
   `MultiQueueWatcher`.

4. Extend `_reserve_queue_config`, `_read_queue_config`, and
   `_peek_queue_config` with an optional `priority` keyword so callers do not
   hand-edit dictionaries.

5. Preserve old behavior when all active queues have the same priority.
   Existing watcher tests should not need broad rewrites.

6. When active queues have different priorities:
   - select the lowest numeric priority with pending work;
   - drain all pending messages from that priority group before lower-priority
     groups are considered;
   - keep round-robin behavior within the selected priority group;
   - do not loop on an empty group;
   - honor `stop_event`.

7. Do not assign strict high priority to PEEK diagnostic queues in this slice.
   A PEEK queue with a retained message can remain pending by design. Giving it
   strict priority would risk starving reserve-mode work.

Verification:

```bash
uv run pytest tests/tasks/test_multiqueue_watcher.py -q
uv run pytest tests/system/test_constants.py -q
```

### Task 3: Add the Internal Spawn Queue Constant and Spec Text

Owner: queue naming and normative docs.

Files:

- `weft/_constants.py`
- `docs/specifications/00-Quick_Reference.md`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`

Required changes:

1. Add `WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE: Final[str] =
   "weft.spawn.internal"` near `WEFT_SPAWN_REQUESTS_QUEUE`.

2. Add a docstring that says this queue is for manager-owned internal spawn
   requests.

3. Add `weft.spawn.internal` to the Quick Reference global queue table with
   `Persisted in dump?` set to `Yes`.

4. Update [MA-1] to state that the canonical manager consumes both
   `weft.spawn.internal` and `weft.spawn.requests`, with internal strict
   priority.

5. Update [MA-2] to state that the spawn message timestamp from either spawn
   queue becomes the child TID.

6. Update [MF-6] to describe source-aware reserved queues and exact-message
   requeue back to the original source queue.

7. Update [MANAGER.10] and [MANAGER.14] to replace hard-coded
   `weft.spawn.requests` requeue wording with "the original spawn source
   queue". Add a new invariant if needed:

   ```text
   MANAGER.16: canonical managers must drain manager-owned internal spawn work
   before ordinary public spawn work whenever both are pending and launch is
   otherwise authorized.
   ```

8. Add a plan backlink to the touched spec sections if the local spec style has
   a nearby plan-link list.

Verification:

```bash
uv run pytest tests/specs/test_plan_metadata.py tests/system/test_constants.py -q
```

### Task 4: Wire the Manager to Both Spawn Sources

Owner: manager queue configuration and observability.

Files:

- `weft/core/manager.py`
- `tests/core/test_manager.py`
- `tests/core/test_manager_services.py`

Required changes:

1. Add logical queue names for internal spawn only when the manager's ordinary
   inbox is `WEFT_SPAWN_REQUESTS_QUEUE`:

   ```text
   internal_inbox    = WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE
   internal_reserved = T{manager_tid}.internal_reserved
   ```

2. Update `_build_queue_configs()` so:
   - internal inbox uses reserve mode;
   - internal reserved queue is `internal_reserved`;
   - internal priority is `QUEUE_PRIORITY_INTERNAL`;
   - public inbox remains reserve mode with `QUEUE_PRIORITY_NORMAL`;
   - both queues use `_handle_work_message`.

3. Keep custom-inbox managers on their existing single-inbox path unless a test
   proves a project-local scoped manager needs internal queue attachment.

4. Add additive registry/control fields only if useful for ops:
   - `internal_requests`
   - `internal_reserved`

   Do not change `requests`.

Tests:

1. Add a manager unit test that writes an internal spawn request and a public
   spawn request, then calls `manager.process_once()` until both complete.
   Assert the internal child is spawned before the public child by task-log
   event order.

2. Add a test that a custom-inbox manager does not consume
   `weft.spawn.internal`.

3. Add a test that manager `STATUS` or registry output preserves `requests`
   while exposing any new internal fields only additively.

Verification:

```bash
uv run pytest tests/core/test_manager.py tests/core/test_manager_services.py -q
```

### Task 5: Make Reserved Policy and Dispatch Fences Source-Aware

Owner: manager reserved-message correctness.

Files:

- `weft/core/manager.py`
- `tests/core/test_manager.py`
- `tests/specs/manager_architecture/test_spawn_retry.py`

Required changes:

1. Extend `DispatchSuspension` with:
   - `source_queue`
   - `reserved_queue`

2. Replace `_requeue_reserved_spawn_request(message_id=...)` with a
   source-aware helper:

   ```text
   _requeue_reserved_spawn_request(
       message_id: int,
       source_queue: str,
       reserved_queue: str,
   ) -> bool
   ```

3. Update `_apply_final_dispatch_fence()` to receive `source_queue` and
   `reserved_queue` from `_handle_work_message()`.

4. Update `_reserved_spawn_request_missing()` to inspect the suspension's
   `reserved_queue`, not always the manager's default reserved queue.

5. Update `_refresh_dispatch_suspension()` and
   `_record_dispatch_recovery_failure()` so every copied or updated
   `DispatchSuspension` preserves `source_queue` and `reserved_queue`, and so
   recovery attempts exact-requeue to the original source queue.

6. Update `_handle_work_message()` to:
   - read `source_queue` from `context.queue_name`;
   - read `reserved_queue` from `context.reserved_queue_name`;
   - delete acknowledgements from `reserved_queue`, not always
     `_get_reserved_queue()`;
   - include source/reserved fields in diagnostics.

7. Add manager-owned reserved-policy helpers that operate over both spawn
   reserved queues:
   - public reserved -> public inbox;
   - internal reserved -> internal inbox.

   The important case is stop/error recovery after a message has been moved
   into `T{manager_tid}.internal_reserved`. It must not be lost and must not be
   requeued to the public spawn queue.

8. Keep `BaseTask` generic behavior unchanged for ordinary tasks. The
   source-aware multi-reserved policy is a manager concern.

Tests:

1. Red test: internal reserved work fenced by a lower-TID manager is exact
   requeued to `weft.spawn.internal`, not `weft.spawn.requests`.

2. Red test: public reserved work still exact requeues to
   `weft.spawn.requests`.

3. Red test: invalid JSON in `weft.spawn.internal` applies the manager's
   reserved policy to `T{manager_tid}.internal_reserved` without touching
   public reserved work.

4. Red test: a stopped manager with internal reserved work requeues or keeps
   according to the configured reserved policy and reports enough diagnostic
   information to recover the item.

Test-design guidance:

- Use exact timestamps returned by queue writes where possible.
- Assert queue contents after recovery. Do not infer from log text alone.
- Avoid monkeypatching the broker. To force a fence, use existing manager
  ownership test helpers or construct registry evidence the same way current
  manager tests do.

### Task 6: Move Internal Service Enqueue to the Internal Queue

Owner: manager-owned singleton service side effects.

Files:

- `weft/core/manager.py`
- `tests/core/test_manager_services.py`
- `tests/cli/test_cli_serve.py`

Required changes:

1. Update `_enqueue_managed_service_request()` or add a small helper so
   heartbeat and TaskMonitor service specs enqueue to `weft.spawn.internal`
   when the internal queue is attached.

2. Keep autostart services on the existing ordinary manager inbox path.
   A simple way to enforce this is to route by service key:

   ```text
   INTERNAL_SERVICE_KEY_HEARTBEAT     -> internal queue
   INTERNAL_SERVICE_KEY_TASK_MONITOR  -> internal queue
   everything else                    -> existing inbox
   ```

3. Update `_pending_service_keys()` to scan both internal and ordinary spawn
   queues for desired service keys. This is required for rollout compatibility
   with existing public-queue service requests.

4. Ensure `spawn_pending` and `launched_once` bookkeeping remains tied to the
   service reducer result, not to which queue was used.

Tests:

1. Test that heartbeat/TaskMonitor spawn payloads land on
   `weft.spawn.internal`.

2. Test that an old pending heartbeat request in `weft.spawn.requests`
   suppresses a duplicate new internal request.

3. Test that autostart still lands on the existing ordinary inbox.

4. Update or extend
   `tests/cli/test_cli_serve.py::test_serve_restarts_singleton_services_without_duplicates`
   so it proves replacement internal services converge to one live service and
   do not require public spawn backlog to clear first.

Verification:

```bash
uv run pytest tests/core/test_manager_services.py -q
uv run pytest tests/cli/test_cli_serve.py::test_serve_restarts_singleton_services_without_duplicates -q
```

### Task 7: Add End-to-End Priority Proof

Owner: CLI/process integration.

Files:

- `tests/cli/test_cli_run.py` or `tests/cli/test_cli_serve.py`
- `tests/helpers/weft_harness.py` only if diagnostics need a small additive
  helper. Do not rewrite harness behavior.

Add one integration test:

1. Start a foreground or harness-managed canonical manager.
2. Write several ordinary public spawn requests that take long enough to remain
   pending.
3. Trigger internal service reconciliation or write a valid internal spawn
   request using the manager-owned internal runtime envelope.
4. Assert the internal request is spawned before the later public backlog is
   drained.
5. Assert there is no duplicate heartbeat/TaskMonitor live service.

This test should be narrow. Do not make it a general performance test, and do
not rely on a fixed sleep as the proof. Use queue/log observations.

### Task 8: Operational Runbook and Rollback Notes

Owner: docs and deploy verification.

Files:

- `docs/specifications/00-Quick_Reference.md`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/lessons.md` only if implementation exposes a repeatable lesson beyond
  this plan.

Add or update notes for deploy verification:

```bash
weft manager list --json
weft queue peek weft.spawn.internal --limit 20 --json
weft queue peek weft.spawn.requests --limit 50 --json
weft status --json
```

Expected post-deploy shape:

- exactly one canonical active manager after selection settles;
- new heartbeat/TaskMonitor spawn requests appear in `weft.spawn.internal`,
  not in `weft.spawn.requests`;
- `weft.spawn.internal` drains quickly when a manager is active;
- existing legacy service requests already in `weft.spawn.requests` may remain
  visible until consumed or made obsolete by later cleanup work;
- old unkeyed TaskMonitor rows can still exist because cleanup is not part of
  this slice.

Rollback:

- Downgrading to a build that does not consume `weft.spawn.internal` can strand
  messages in that queue.
- Before or after downgrade, move any internal spawn messages back to the public
  queue if the old manager must consume them:

  ```bash
  weft queue move weft.spawn.internal weft.spawn.requests
  ```

  Use `--limit` if the operator wants a staged move. Inspect with
  `weft queue move --help` before running this in production.

## 7. Test Gates

Run these in order while implementing:

```bash
uv run pytest tests/specs/test_plan_metadata.py -q
uv run pytest tests/tasks/test_multiqueue_watcher.py -q
uv run pytest tests/core/test_manager_services.py -q
uv run pytest tests/core/test_manager.py -q
uv run pytest tests/cli/test_cli_serve.py::test_serve_restarts_singleton_services_without_duplicates -q
uv run pytest tests/system/test_constants.py -q
uv run ruff check weft
uv run mypy weft extensions/weft_docker extensions/weft_macos_sandbox
uv run pytest
uv run bin/pytest-pg
```

The full `uv run pytest` and `uv run bin/pytest-pg` gates are required because
this changes process scheduling and broker behavior across SQLite and Postgres.

## 8. Invariants to Preserve

- TID is still the spawn message timestamp.
- The manager only launches children after positive `self` ownership.
- Fenced reserved spawn work remains durable.
- A fenced public request returns to the public source queue.
- A fenced internal request returns to the internal source queue.
- No internal service launch path bypasses `_build_child_spec()` or
  `_launch_child_task()`.
- `requests` in manager registry/control output remains the ordinary public
  spawn queue.
- Autostart behavior is unchanged.
- Queue priority support must not alter equal-priority watcher behavior.

## 9. Common Mistakes to Avoid

- Do not rely on Python dict order to create priority.
- Do not create a separate internal-service launcher.
- Do not put priority logic in SimpleBroker.
- Do not assign strict high priority to PEEK queues without a drain cap.
- Do not use one reserved queue for both spawn sources.
- Do not scan only `weft.spawn.internal` for pending service evidence during
  rollout.
- Do not update cleanup behavior as part of this slice.
- Do not move autostart to a new queue here.

## 10. Independent Review Checklist

Ask a second agent or reviewer to inspect before implementation and again after
the first passing slice:

- Does the plan preserve one manager child-launch path?
- Does every reserved-message recovery path know the original source queue?
- Is watcher priority deterministic without changing equal-priority behavior?
- Are PEEK queues prevented from starving reserve queues?
- Do tests use real queues and manager/harness behavior rather than mocks?
- Is autostart explicitly unchanged?
- Does rollback account for old managers ignoring `weft.spawn.internal`?
- Are spec updates normative and plan text non-normative?

## 11. Fresh-Eyes Review Applied

Review pass findings and corrections:

- Initial tempting approach: add `weft.spawn.internal` to
  `_build_queue_configs()` before `weft.spawn.requests`. Rejected because
  `MultiQueueWatcher` is round-robin and dict order would not prove strict
  priority.
- Initial tempting approach: use the existing manager reserved queue for both
  sources. Rejected because stop/error/fence recovery would lose source
  identity.
- Initial tempting approach: route all manager-owned services, including
  autostart, through the new queue. Rejected because the requested slice is
  internal services only; autostart priority needs its own policy discussion.
- Initial tempting approach: mark control and reserved PEEK queues as higher
  priority. Rejected because retained PEEK messages can remain pending and
  could starve reserve-mode work.
- Initial tempting approach: scan only the new internal queue for pending
  heartbeat/TaskMonitor work. Rejected because production already has
  public-queue service requests from older builds, and rollout must not create
  duplicates.

The resulting plan stays aligned with the discussed direction: one manager,
one child-launch path, one deterministic priority contract, no cleanup, and no
autostart queue in this slice.

# Canonical Service Reducer Fix Plan

Status: completed
Source specs: docs/specifications/03-Manager_Architecture.md [MA-0], [MA-1], [MA-3]; docs/specifications/07-System_Invariants.md [MANAGER.3], [MANAGER.8], [MANAGER.12], [MANAGER.12a], [MANAGER.13], [MANAGER.15], [MANAGER.16], [OBS.15], [OBS.16]
Superseded by: none

## 1. Goal

Fix the current shared service-convergence implementation so all internal
service registration and liveness decisions, including manager ownership, pass
through one canonical reducer path. The immediate correctness bugs are strict
schema validation, non-manager row isolation, timestamp truth, and secret-free
service keys. The broader goal is a stable boundary: edge adapters collect
runtime evidence, `weft/core/service_convergence.py` makes ownership decisions,
and output projections never decide liveness or ownership.

## 2. Source Documents

- [docs/specifications/03-Manager_Architecture.md](../specifications/03-Manager_Architecture.md)
  [MA-0], [MA-1], [MA-3]: managers are task-shaped services, publish service
  owner rows in `weft.state.services`, and converge by lowest live manager TID
  for status and selection while public reservation remains dispatch authority.
- [docs/specifications/07-System_Invariants.md](../specifications/07-System_Invariants.md)
  [MANAGER.3], [MANAGER.8], [MANAGER.12], [MANAGER.12a],
  [MANAGER.13], [MANAGER.15], [MANAGER.16], [OBS.15], [OBS.16]:
  manager service-owner rows, duplicate-manager convergence, reserved-work
  authority, runtime-only cleanup, and manager status truth.
- [docs/specifications/05-Message_Flow_and_State.md](../specifications/05-Message_Flow_and_State.md)
  runtime-state queue references and task-log/status boundaries.
- [docs/specifications/00-Quick_Reference.md](../specifications/00-Quick_Reference.md):
  queue-name reference for `weft.state.services` and runtime-only queues.
- [docs/plans/2026-05-11-service-convergence-and-manager-registry-bounding-plan.md](./2026-05-11-service-convergence-and-manager-registry-bounding-plan.md):
  active draft that introduced `weft.state.services` as the canonical service
  registry. This plan is a corrective follow-up for the review findings and
  should be applied before continuing broader service migration.
- [docs/plans/2026-05-10-control-and-service-convergence-state-machine-plan.md](./2026-05-10-control-and-service-convergence-state-machine-plan.md):
  completed plan for deterministic manager-owned service convergence.
- [docs/agent-context/runbooks/writing-plans.md](../agent-context/runbooks/writing-plans.md)
  and [docs/agent-context/runbooks/hardening-plans.md](../agent-context/runbooks/hardening-plans.md):
  planning and hardening requirements for boundary-crossing runtime changes.

## 3. Context and Key Files

Files to modify:

- `weft/core/service_convergence.py`
- `weft/context.py`
- `weft/core/manager.py`
- `weft/core/manager_runtime.py`
- `weft/core/manager_services.py`
- `weft/core/task_monitoring.py`
- `weft/core/pruning/runtime.py`
- `weft/_constants.py`
- `tests/core/test_service_convergence.py`
- `tests/core/test_manager.py`
- `tests/commands/test_manager_commands.py`
- `tests/commands/test_run.py`
- `tests/commands/test_status.py`
- `tests/commands/test_runtime_prune.py`
- `tests/core/test_task_monitoring.py`
- touched specs listed in section 2 if implementation notes change
- `docs/plans/README.md`

Read first:

- `weft/core/service_convergence.py`: current parser, reducer, manager
  projection, and payload builder.
- `weft/core/manager.py`: `_register_manager`,
  `_recent_lower_canonical_manager_exists`, `_read_active_manager_records`,
  `_leader_tid`, `_maybe_yield_leadership`,
  `_run_managed_service_convergence`, and internal service candidate logic.
- `weft/core/manager_runtime.py`: `_snapshot_registry`,
  `_normalize_manager_record`, `_select_active_manager_from_snapshot`,
  `_mark_manager_stopped`, `_await_manager_stop_confirmation`, and
  PING/PONG rescue helpers.
- `weft/core/manager_services.py`: current internal singleton service
  reducer and service candidate model.
- `weft/core/pruning/runtime.py` and `weft/core/task_monitoring.py`: runtime
  state cleanup candidates that currently normalize manager rows locally.
- `weft/core/spawn_requests.py`: exact-timestamp write pattern for spawn
  requests.
- `weft/context.py`: `WeftContext`, broker target resolution, and
  `service_context_key`.
- `../simplebroker/simplebroker/sbqueue.py` and `../simplebroker/simplebroker/db.py`
  around `Queue.write()`, `generate_timestamp()`, and exact timestamp writes.

Current structure:

- `weft.state.services` now exists as the intended runtime service registry.
- `service_convergence.py` owns a partial pure reducer, but callers still
  normalize, filter, and project manager rows locally.
- `manager_runtime.py` can currently treat a non-manager service-owner row as
  a canonical manager through compatibility normalization. That compatibility
  path must be removed, not patched around.
- `parse_service_owner_record()` accepts non-numeric owner TIDs, which then
  sort before real numeric TIDs.
- manager service payloads embed a generated timestamp and then call
  `Queue.write()`, which assigns a different broker message timestamp.
- `service_context_key()` can use a raw non-file backend target, which may
  include credentials.
- `_observed_service_candidates_by_key()` in `weft/core/manager.py` still
  derives internal singleton ownership primarily from `weft.log.tasks`; that
  means internal services have not yet moved to the same canonical service
  registry path as the manager.
- `WEFT_MANAGERS_REGISTRY_QUEUE` is currently an alias to
  `WEFT_SERVICES_REGISTRY_QUEUE`. That alias hides old manager-registry call
  sites and must not remain in active runtime code.

Comprehension questions before editing:

- Which function is allowed to decide the canonical live service owner after
  this plan lands?
- Which layer owns runtime-specific liveness proof, such as host PID checks,
  external-supervisor probes, and keyed PONG rescue?
- Which rows may be projected into existing manager command output, and at what
  stage of the pipeline?
- What is the difference between malformed registry data, an unsupported
  schema, `none`, and `unknown`?

## 4. Target Boundary

Use this pipeline everywhere:

```text
queue rows
-> strict service-owner row parsing
-> service_key/service_type filtering
-> service-specific evidence adaptation and liveness probing
-> reduce_service_ownership()
-> exact prune plan / canonical owner / duplicate owner / uncertainty result
-> output projection or service-specific side effect
```

The only ownership reducer is `reduce_service_ownership()` or a clearly named
wrapper in `service_convergence.py` that delegates to it. Manager code,
manager command code, runtime pruning, TaskMonitor cleanup, heartbeat
convergence, and autostart convergence may adapt evidence, but they must not
reimplement latest-by-owner selection, TTL expiry, lowest-live-TID selection,
recent-lower-owner checks, duplicate live-owner selection, or `none` versus
`unknown` classification.

Projection is output formatting only. A helper such as
`project_manager_service_record()` may turn an already accepted manager service
owner into the existing manager dict shape for command output, but projection
must not accept, rescue, or classify service ownership.

## 5. Invariants and Constraints

- Preserve public spawn dispatch authority. A manager may launch public work
  only after successful reservation of that exact `weft.spawn.requests`
  message. Canonical service ownership is not a public dispatch lock.
- Preserve manager reserved-work drain semantics. A non-canonical manager with
  already-reserved public or internal work must not abandon that work only
  because a lower live owner exists.
- Preserve forward-only task lifecycle truth in `weft.log.tasks`.
  Service-owner rows are runtime state and must not become task lifecycle
  history.
- `weft.state.services` remains runtime-only. It is excluded from dump/load
  and may be pruned only by exact message ID through runtime-state cleanup
  paths.
- Use generator-based queue history reads for append-style runtime queues.
  Do not introduce correctness-critical `peek_many(limit=...)` scans.
- `service_convergence.py` may define schema, service keys, parser diagnostics,
  liveness state names, reducer results, prune plans, and payload builders.
  It must not import `Manager`, `Queue`, `RunnerHandle`, Docker helpers,
  `TaskSpec`, or task classes.
- Runtime-specific liveness stays in adapters:
  `Manager`, `manager_runtime.py`, runner/plugin code, task-log lifecycle
  evidence helpers, or manager-owned service adapters.
- Unsupported service-owner schemas and malformed rows are ignored or
  diagnosed. They do not become live, stopped, or uncertain evidence.
- `unknown` means a registry read, parse stream, or liveness probe failed in a
  way that could hide a live owner. Bad data by itself is not `unknown`.
- Non-manager service rows must never affect manager selection, manager list,
  manager status, manager stop confirmation, or manager bootstrap.
- Service keys must not persist secrets. Non-file backend keys must be stable
  and non-secret.
- No new dependency, public CLI flag, TaskSpec field, SimpleBroker storage
  schema, manager lock, or lease system.
- Cleanup failures are best-effort diagnostics. They must not prevent a
  manager from servicing already-reserved work.
- Active runtime code must use `WEFT_SERVICES_REGISTRY_QUEUE` by name. Do not
  keep `WEFT_MANAGERS_REGISTRY_QUEUE` as an acceptable synonym for new or
  migrated code.

## 6. Rollback and Rollout

Rollback is code-level because `weft.state.services` is runtime-only. Revert
the reducer integration and restart live managers so old and new runtime
registry readers do not coexist. No user task data migration is required.

Rollout sequencing matters:

1. Land strict reducer/parser behavior and tests first.
2. Remove the manager-registry alias and make failures obvious where old names
   remain.
3. Migrate manager selection/list/status/bootstrap/stop and manager
   publication through the strict path. This is the urgent correctness slice.
4. Migrate internal singleton services to the same reducer path.
5. Update runtime pruning and TaskMonitor cleanup after strict parsing and the
   manager path are stable, so cleanup cannot depend on transitional row
   shapes.
6. Update specs and docs after producers and consumers agree.

Different code revisions may disagree about active runtime registry truth, so
do not deploy or test rollback by leaving older managers alive while new
managers are making service-owner decisions.

## 7. Tasks

1. Harden the shared service-owner schema and reducer.

   Outcome: `service_convergence.py` rejects malformed ownership evidence and
   exposes the diagnostics needed by all callers.

   Files:
   - `weft/core/service_convergence.py`
   - `tests/core/test_service_convergence.py`

   Implementation:
   - Require `owner_tid` to be a non-empty numeric string.
   - Require `timestamp` to be a positive integer broker message ID passed by
     the queue reader.
   - Keep `status` constrained to the supported service-owner statuses.
   - Add a parse result or registry-read result that can distinguish accepted,
     ignored unsupported schema, malformed service-owner row, and read failure.
   - Keep unsupported schemas ignored. Do not turn them into uncertain owner
     evidence.
   - Make invalid TIDs impossible to select. Do not keep the current `0`
     substitute for malformed owner TIDs.
   - Ensure duplicate live owners are reported in the reducer decision.
   - Ensure expired lower owners do not suppress newer higher owners.

   Tests:
   - Invalid `owner_tid` is rejected and cannot become canonical.
   - Non-positive or non-integer timestamps are rejected.
   - Unsupported schemas are ignored and do not create uncertainty.
   - Malformed rows are diagnosed but do not create uncertainty.
   - Duplicate live owners are reported.
   - Expired lower owner does not set `recent_lower_live_owner`.
   - `read_failed=True` still yields `state == "unknown"`.

   Stop gate:
   - Stop if the reducer starts importing manager runtime or queue classes.

2. Add a canonical registry-read adapter for service rows.

   Outcome: queue row parsing and service-key/service-type filtering have one
   shared path.

   Files:
   - `weft/core/service_convergence.py`
   - `tests/core/test_service_convergence.py`

   Implementation:
   - Add a helper with a shape close to:

     ```python
     def collect_service_owner_records(
         entries: Iterable[tuple[Mapping[str, Any], int]],
         *,
         service_key: str | None = None,
         service_type: str | None = None,
     ) -> ServiceRegistryRead: ...
     ```

   - `ServiceRegistryRead` should carry accepted records, ignored counts or
     diagnostics, malformed counts or diagnostics, and `read_failed`.
   - Filtering by `service_key` and `service_type` must happen before
     ownership decisions, not in projection helpers.
   - Do not parse old manager-registry rows in this helper or in manager
     adapters. `weft.state.services` is a service-owner registry; unsupported
     shapes are ignored or diagnosed.

   Tests:
   - Filtering by manager service key excludes heartbeat and TaskMonitor rows.
   - Filtering by service type excludes non-manager rows even if they contain
     `tid`, `role`, or queue fields.
   - Rows without `schema == weft.service_owner.v1` are ignored or diagnosed,
     never treated as alternate manager evidence.

   Stop gate:
   - Stop if a caller would still need to re-filter service type or service
     key after using the helper.

3. Remove the manager-registry alias.

   Outcome: active code has one queue name and one mental model:
   `WEFT_SERVICES_REGISTRY_QUEUE`.

   Files:
   - `weft/_constants.py`
   - every active-code import or use of `WEFT_MANAGERS_REGISTRY_QUEUE`
   - `tests/specs/quick_reference/test_queue_names.py`
   - a new or existing architecture/spec test that scans active code

   Implementation:
   - Remove `WEFT_MANAGERS_REGISTRY_QUEUE` from `_constants.py`.
   - Update all in-repo references to `WEFT_SERVICES_REGISTRY_QUEUE` or delete
     the code if it only exists for old manager-registry compatibility.
   - Do not leave a deprecated alias. The old name makes old call-site usage
     look legitimate and hides bugs.
   - Rename imports and call sites to `WEFT_SERVICES_REGISTRY_QUEUE`.
   - Do not add a new alias such as `MANAGER_SERVICES_QUEUE`; managers are
     services now.

   Tests:
   - Active runtime code does not import or reference
     `WEFT_MANAGERS_REGISTRY_QUEUE`.
   - Queue-name spec tests assert `WEFT_SERVICES_REGISTRY_QUEUE ==
     "weft.state.services"`.
   - A grep/spec test fails on any active-code reference to
     `WEFT_MANAGERS_REGISTRY_QUEUE`.

   Stop gate:
   - Stop if a test needs old manager-registry-shaped rows to keep passing.
     Update the test data to service-owner rows instead of preserving the old
     shape.

4. Fix timestamp truth for service-owner publication.

   Outcome: service-owner payloads do not carry a separate timestamp. Readers
   always project the broker message ID from queue history.

   Files:
   - `weft/core/service_convergence.py`
   - `weft/core/manager.py`
   - `weft/core/manager_runtime.py`
   - tests that write/read service-owner rows

   Implementation:
   - Remove payload-level `timestamp` from service-owner payload builders.
   - Make all readers use the broker message ID from
     `peek_generator(with_timestamps=True)` / `iter_queue_json_entries`.
   - Do not keep generated payload timestamps that differ from message IDs.
   - Do not add an exact-write helper for runtime service rows in this slice.
     Exact writes use SimpleBroker internals today and should stay limited to
     the spawn-request path unless a separate plan proves a stronger need.

   Tests:
   - A manager active row payload has no `timestamp` field, and the projected
     record uses the broker message ID.
   - Stopped rows written by `_unregister_manager()` and forced-stop code obey
     the same timestamp rule.
   - Prune candidates use broker message IDs, not payload timestamps.

   Stop gate:
   - Stop if code starts trusting payload `timestamp` when broker message ID is
     available.

5. Make service context keys stable and secret-free.

   Outcome: `manager_service_key(context)` and all internal service keys use a
   context identity that cannot leak credentials.

   Files:
   - `weft/context.py`
   - `weft/core/service_convergence.py`
   - `tests/context/test_context.py` or `tests/core/test_service_convergence.py`

   Implementation:
   - For SQLite/file-backed contexts, keep the normalized resolved database
     path.
   - For non-file backends, derive from backend name, a normalized target with
     password removed, and backend options such as schema.
   - If target normalization is ambiguous, hash the normalized non-secret
     identity instead of storing the full target.
   - Add a small comment explaining that service keys are persisted in
     runtime queues and must not include secrets.

   Tests:
   - A Postgres-style URL containing `user:password@host/db` does not expose
     `password` in `service_context_key()`.
   - Distinct backend schema/options produce distinct keys when they represent
     distinct broker contexts.
   - File-backed keys remain stable across relative/absolute path inputs.

   Stop gate:
   - Stop if this requires changing SimpleBroker target resolution semantics.
     This plan only derives Weft service identity from the resolved target.

6. Route manager registry reads through the service registry adapter and reducer.

   Outcome: manager selection, status, bootstrap, list, stop confirmation, and
   publication suppression all use one canonical service-owner decision path.

   Files:
   - `weft/core/manager.py`
   - `weft/core/manager_runtime.py`
   - `tests/core/test_manager.py`
   - `tests/commands/test_manager_commands.py`
   - `tests/commands/test_run.py`
   - `tests/commands/test_status.py`

   Implementation:
   - Replace manager-local service-owner parsing with
     `collect_service_owner_records(..., service_type="manager",
     service_key=manager_service_key(context))`.
   - Remove alternate manager-registry row support from active manager
     selection. Rows without `schema == SERVICE_OWNER_SCHEMA` are ignored or
     diagnosed, never projected as manager records.
   - Rows with `schema == SERVICE_OWNER_SCHEMA` that fail manager filtering are
     ignored or diagnosed, never projected as manager records.
   - Apply manager-specific liveness in an adapter before reduction:
     host PID, external-supervisor probes, own-row self-liveness, and keyed
     PONG rescue.
   - Feed the adapted records into the shared reducer.
   - Use reducer output for canonical manager, recent lower owner,
     duplicate-live owner, `none`, and `unknown`.
   - Project accepted manager service-owner rows to existing command output
     only after reduction or after explicit manager filtering.

   Tests:
   - `weft manager list --json` ignores heartbeat/TaskMonitor service-owner
     rows in `weft.state.services`.
   - A malformed manager service-owner row cannot become the active manager.
   - Wrong service key rows cannot affect manager selection.
   - Lower live manager wins through the reducer.
   - Stale lower manager does not suppress a higher manager.
   - PING/PONG rescue still works with the service-owner row shape.
   - Unknown registry read causes wait/degraded behavior but does not make a
     manager abandon already-reserved work.

   Stop gate:
   - Stop if `_normalize_manager_record()` or an equivalent helper still
     assigns manager role/request defaults to schema-bearing non-manager rows.

7. Move manager publication and stopped rows onto the same write and reducer path.

   Outcome: active, draining if used, stopped, and terminal manager service
   rows share one payload builder and write contract.

   Files:
   - `weft/core/service_convergence.py`
   - `weft/core/manager.py`
   - `weft/core/manager_runtime.py`
   - `tests/core/test_manager.py`
   - `tests/commands/test_manager_commands.py`

   Implementation:
   - Ensure `_register_manager()`, `_refresh_manager_registration()`,
     `_unregister_manager()`, and forced-stop `_mark_manager_stopped()` use
     the same manager service payload builder and timestamp contract.
   - Use reducer prune plans for expired rows and older self rows.
   - Preserve stopped evidence long enough for stop callers to observe it.
   - Do not treat unreserved public backlog as a reason to publish active
     manager ownership.

   Tests:
   - Manager startup writes one accepted manager service-owner row.
   - Heartbeat refresh prunes older self rows.
   - Clean unregister writes or preserves bounded stopped evidence.
   - Forced stop writes a stopped service-owner row through the same builder.
   - A higher-TID manager with only public backlog suppresses active
     publication after seeing a lower live owner.
   - A manager with already-reserved work remains visible/drains according to
     existing manager invariants.

   Stop gate:
   - Stop if this changes task-log lifecycle events or public spawn reserved
     queue policy.

8. Migrate internal singleton services to the same canonical reducer.

   Outcome: heartbeat, TaskMonitor, and autostart ensure service registration
   and liveness decisions use the same service-owner schema and reducer as the
   manager.

   Files:
   - `weft/core/manager.py`
   - `weft/core/manager_services.py`
   - `weft/core/tasks/heartbeat.py` only if child-owned publication is needed
   - `weft/core/tasks/task_monitor.py` only if child-owned publication is needed
   - `tests/core/test_manager.py`
   - `tests/core/test_task_monitoring.py`

   Implementation:
   - Prefer manager-authored service rows for manager-supervised singleton
     children because the manager owns launch, reap, restart, and duplicate
     cleanup authority.
   - Use `weft.state.services` as primary runtime ownership evidence.
   - Use task logs, TID mappings, and `task_spawned` evidence only as
     supplemental lifecycle evidence that is adapted into `ServiceOwnerRecord`
     inputs before reduction. Do not create a separate fallback decision path.
   - Feed all adapted heartbeat, TaskMonitor, and autostart owner candidates
     into the shared reducer.
   - Preserve terminal-over-live semantics for the same owner TID.

   Tests:
   - Heartbeat and TaskMonitor each reduce to one canonical live owner.
   - Duplicate live TaskMonitor owners are reported by the reducer and cleanup
     targets only the non-canonical owner when authority exists.
   - Expired service rows do not block restart.
   - Recent uncertain evidence causes wait/degraded behavior rather than
     duplicate launch.
   - Repeated restart/reap cycles keep service rows bounded.

   Stop gate:
   - Stop if task-log scans remain the primary normal-path service registry.

9. Update runtime pruning and TaskMonitor cleanup to consume reducer decisions.

   Outcome: cleanup paths use the same parser, filters, timestamps, and prune
   plan as runtime convergence.

   Files:
   - `weft/core/pruning/runtime.py`
   - `weft/core/task_monitoring.py`
   - `tests/commands/test_runtime_prune.py`
   - `tests/core/test_task_monitoring.py`

   Implementation:
   - Do this after tasks 1-7. Cleanup must not be the first consumer of a
     transitional service-owner shape.
   - Read `weft.state.services` through the shared service registry adapter.
   - Use reducer prune plans for expired and older same-owner rows.
   - Keep cleanup exact-message-ID only.
   - Do not delete active, malformed, unsupported, unknown, claimed,
     task-local, spawn, manager control, or task-log evidence as part of this
     service registry cleanup.
   - Preserve current manager-specific labels in reports only as presentation,
     not as a separate manager prune algorithm.

   Tests:
   - Runtime prune reports expired service-owner rows by exact message ID.
   - Runtime prune does not delete malformed or unsupported schema rows unless
     an explicit safe malformed cleanup class already exists and is approved.
   - TaskMonitor cleanup ignores unrelated service-owner rows.
   - Pruning manager rows and internal service rows uses the same timestamp and
     latest-owner semantics.

   Stop gate:
   - Stop if cleanup code reimplements latest-by-owner grouping outside the
     service reducer.

10. Update specs and traceability.

   Outcome: docs and code agree about `weft.state.services` and the one
   canonical reducer path.

   Files:
   - `docs/specifications/00-Quick_Reference.md`
   - `docs/specifications/03-Manager_Architecture.md`
   - `docs/specifications/05-Message_Flow_and_State.md`
   - `docs/specifications/07-System_Invariants.md`
   - `docs/plans/README.md`
   - touched module docstrings in `weft/core/service_convergence.py`,
     `weft/core/manager.py`, and `weft/core/manager_runtime.py`

   Required updates:
   - State that all convergent internal services, including the manager, use
     the shared service-owner schema and reducer.
   - State that runtime-specific liveness evidence is adapted before the
     reducer and projection happens after the reducer.
   - State that service-owner rows with unsupported or malformed schema do not
     become uncertain ownership evidence.
   - State that `weft.state.services` is runtime-only and exact-message-ID
     cleanup only.
   - State that `WEFT_SERVICES_REGISTRY_QUEUE` is the only constant name for the
     service registry. `WEFT_MANAGERS_REGISTRY_QUEUE` is removed.
   - Add a backlink from touched specs to this plan.

   Stop gate:
   - Stop if the implementation changes behavior the specs do not describe.
     Update the spec in the same slice.

11. Verification and independent review.

   Required local commands:

   ```bash
   . ./.envrc
   ./.venv/bin/python -m pytest tests/core/test_service_convergence.py -v
   ./.venv/bin/python -m pytest tests/context/test_context.py -k "service_context or broker" -v
   ./.venv/bin/python -m pytest tests/core/test_manager.py -k "registry or leadership or reserved_public or service_convergence or task_monitor" -v
   ./.venv/bin/python -m pytest tests/commands/test_manager_commands.py tests/commands/test_run.py tests/commands/test_status.py -k "manager or registry or services or supervisor or pong" -v
   ./.venv/bin/python -m pytest tests/commands/test_runtime_prune.py tests/core/test_task_monitoring.py -v
   ./.venv/bin/python -m pytest tests/specs/quick_reference/test_queue_names.py tests/specs/test_plan_metadata.py -v
   ./.venv/bin/python -m pytest tests/system/test_constants.py -k "services_registry or managers_registry or constants" -v
   ./.venv/bin/ruff check weft/core/service_convergence.py weft/core/manager.py weft/core/manager_runtime.py weft/core/manager_services.py weft/core/pruning/runtime.py weft/core/task_monitoring.py
   ./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
   ```

   Independent review:
   - Required after the plan is written.
   - Required again after tasks 1-7, before migrating internal singleton
     services, because that is where the reducer boundary becomes load-bearing
     for manager selection and command behavior.
   - Required before completion if any task changes the service-owner schema,
     timestamp write path, or manager liveness adapter.

   Suggested review prompt:

   > Read `docs/plans/2026-05-11-canonical-service-reducer-fix-plan.md`,
   > `weft/core/service_convergence.py`, `weft/core/manager.py`,
   > `weft/core/manager_runtime.py`, and `weft/core/manager_services.py`.
   > Look for any remaining second convergence path, unsafe compatibility
   > reader, timestamp mismatch, secret-bearing service key, or test seam
   > that mocks away manager/service ownership. Could you implement this
   > confidently and correctly if asked?

   Observable success:
   - `weft.state.services` remains bounded during steady manager heartbeat and
     internal service supervision.
   - `weft manager list --json` ignores non-manager service rows.
   - Manager selection/status/bootstrap use the same reducer decision as
     manager publication suppression.
   - Heartbeat and TaskMonitor use the same service-owner reducer path as the
     manager.
   - Runtime prune reports service-owner cleanup by exact message ID.

## 8. Out of Scope

- No new manager lease, lock, or backend-specific leader-election primitive.
- No public CLI output redesign beyond preserving existing manager/status UX
  through post-reducer projection.
- No cleanup of task-local queues, spawn queues, manager control queues,
  TID mappings, or `weft.log.tasks` as part of service registry cleanup.
- No compatibility reader for `weft.state.managers` or old manager-registry row
  shapes.
- No `WEFT_MANAGERS_REGISTRY_QUEUE` constant or alias. Use
  `WEFT_SERVICES_REGISTRY_QUEUE`.
- No Docker or external-supervisor policy inside `service_convergence.py`.
- No load-balancing feature for multiple public managers.
- No SimpleBroker API change and no new exact-write helper for runtime service
  registry rows in this slice.

## 9. Known Risks

- The strongest risk is a subtle second reducer path surviving in
  `manager_runtime.py`, `manager.py`, pruning, or TaskMonitor cleanup. Grep is
  not enough; read the callers that reduce registry rows.
- Strict parsing can hide bad data that old code accidentally tolerated. That
  is intentional for schema-bearing service-owner rows, but diagnostics should
  make malformed rows visible in debug or cleanup reports.
- Secret-free service keys need stable identity. If the key strips too much,
  two backend contexts can collide; if it strips too little, credentials leak
  into runtime queues.
- Removing payload timestamps is lower risk than spreading SimpleBroker
  internal exact-write usage. If a later slice needs exact runtime service-row
  IDs in payloads, it needs a separate plan.
- If internal singleton services keep task-log lifecycle scans as a separate
  decision path, this plan has not achieved one canonical code path.

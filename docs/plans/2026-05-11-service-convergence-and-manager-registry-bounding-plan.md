# Service Convergence And Manager Registry Bounding Plan

Status: draft
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1], [MA-3]; docs/specifications/07-System_Invariants.md [MANAGER.8], [MANAGER.12], [MANAGER.12a], [MANAGER.13], [MANAGER.15], [MANAGER.16]
Superseded by: none

## 1. Goal

Replace the bespoke manager registry and internal service ownership paths with
one canonical convergence code path and one runtime-internal service registry:
`weft.state.services`. A convergent service is any Weft-owned singleton or
priority-owned runtime service, including the canonical manager for
`weft.spawn.requests`, heartbeat, TaskMonitor, and autostart ensure services.
Every convergent service should publish and reduce ownership through the same
service-owner schema and convergence reducer. There is no external
backward-compatibility requirement for `weft.state.managers`; this slice may
remove it as the authoritative registry and update all in-repo readers to use
`weft.state.services`.

The operational bug this addresses is monotonic growth in `weft.state.managers`
and duplicate manager publication under live lower-TID manager evidence. The
broader design goal is stricter: any time Weft wants a service to converge,
including the manager, the same convergence code decides latest evidence,
expiry, lowest live owner, publication suppression, duplicate cleanup, and
whether the local process may continue.

## 2. Source Documents

- [docs/specifications/03-Manager_Architecture.md](../specifications/03-Manager_Architecture.md)
  [MA-1], [MA-3]: manager registry heartbeat, leadership, public spawn
  reservation authority, foreground serve, and supervised manager lifecycle.
- [docs/specifications/07-System_Invariants.md](../specifications/07-System_Invariants.md)
  [MANAGER.8], [MANAGER.12], [MANAGER.12a], [MANAGER.13],
  [MANAGER.15], [MANAGER.16]: duplicate-manager convergence, reserved public
  work authority, internal singleton service rules, and runtime-state cleanup.
- [docs/specifications/00-Quick_Reference.md](../specifications/00-Quick_Reference.md):
  queue-name reference that must gain `weft.state.services` and stop presenting
  `weft.state.managers` as the canonical manager registry.
- [docs/agent-context/runbooks/runtime-and-context-patterns.md](../agent-context/runbooks/runtime-and-context-patterns.md)
  sections 6 and 7: `weft.state.*` is runtime-only and append-style registry
  state must be reduced by latest/current records, not fixed-size peeks.
- [docs/plans/2026-05-11-manager-work-stealing-dispatch-plan.md](./2026-05-11-manager-work-stealing-dispatch-plan.md):
  draft/active plan for public spawn work-stealing. This plan preserves its
  dispatch authority: successful public reservation authorizes launch.
- [docs/plans/2026-05-10-control-and-service-convergence-state-machine-plan.md](./2026-05-10-control-and-service-convergence-state-machine-plan.md):
  completed plan that introduced the current managed-service pure reducer. This
  plan supersedes the narrow service-only reducer boundary by making manager
  ownership a first-class service in the same convergence model.
- [docs/plans/2026-05-07-runtime-state-pruning-plan.md](./2026-05-07-runtime-state-pruning-plan.md):
  completed plan for explicit runtime-state pruning. This plan adds normal
  manager-side pruning for service registry rows; explicit prune commands must
  still use the shared safe-delete path.

## 3. Context and Key Files

Files to modify:

- `weft/_constants.py`: add `WEFT_SERVICES_REGISTRY_QUEUE =
  "weft.state.services"` and update runtime-state queue maps. Remove or stop
  using `WEFT_MANAGERS_REGISTRY_QUEUE` as a canonical active registry.
- `weft/core/context.py` or a narrow adjacent helper: define the canonical
  service context key from the resolved broker target. Do not let manager and
  command code each derive context keys differently.
- `weft/core/service_convergence.py` (new): canonical service-owner schema,
  convergence reducer, latest-record reduction, TTL expiry, lowest-live-owner
  selection, recent-lower-owner checks, and exact prune planning.
- `weft/core/manager_services.py`: either move its reducer into
  `service_convergence.py` or turn it into a thin compatibility module that
  re-exports service-specific metadata helpers while all owner reduction calls
  go through `service_convergence.py`.
- `weft/core/manager.py`: publish manager ownership and internal service
  ownership to `weft.state.services`; read service registry for manager
  leadership, publication suppression, service duplicate detection, and
  service convergence; stop writing `weft.state.managers` as the active
  manager registry.
- `weft/core/manager_runtime.py`: command-side manager start/select/list/stop
  must read and write `weft.state.services` for manager service owners,
  including forced-stop confirmation helpers.
- `weft/commands/manager.py`, `weft/commands/status.py`,
  `weft/commands/runtime_prune.py`, `weft/commands/dump.py`, and
  `weft/commands/load.py`: update queue references and runtime-only filtering.
- `weft/commands/system.py`: update `_collect_task_snapshot_records()` and any
  status/read-model code that currently uses manager registry rows as liveness
  or display evidence.
- `weft/core/tasks/task_monitor.py`: if it scans or reports runtime-state
  queues, include `weft.state.services` and remove manager-registry
  special-casing where appropriate.
- Tests:
  - `tests/core/test_service_convergence.py` (new)
  - `tests/core/test_manager.py`
  - `tests/commands/test_run.py`
  - `tests/commands/test_manager_commands.py`
  - `tests/commands/test_status.py`
  - `tests/commands/test_runtime_prune.py`
  - `tests/commands/test_dump_load.py`
  - `tests/cli/test_cli_serve.py`
- Specs:
  - `docs/specifications/00-Quick_Reference.md`
  - `docs/specifications/03-Manager_Architecture.md`
  - `docs/specifications/05-Message_Flow_and_State.md`
  - `docs/specifications/07-System_Invariants.md`

Read first:

- `weft/core/manager.py` around `_register_manager`,
  `_refresh_manager_registration`, `_read_active_manager_records`,
  `_leader_tid`, `_maybe_yield_leadership`, `_run_managed_service_convergence`,
  `_observed_service_candidates_by_key`, `_tick_managed_service`, and
  `_terminate_duplicate_service_candidates`.
- `weft/core/manager_services.py` around `ServiceCandidate`,
  `summarize_service_candidates`, and `reduce_managed_service_state`.
- `weft/core/manager_runtime.py` around `_snapshot_registry`,
  `_select_active_manager_from_snapshot`, `_registry_view`, `_start_manager`,
  `_ensure_manager`, `_serve_manager_foreground`, `_stop_manager`, and
  command-side PING/PONG liveness rescue. Include `_mark_manager_stopped`,
  `_await_manager_stop_confirmation`, and `_external_supervisor_record_is_live`;
  these are not optional follow-ups.
- `weft/core/spawn_requests.py` around exact-timestamp spawn writes and manager
  internal spawn envelopes.
- `weft/helpers/__init__.py` around `iter_queue_json_entries`,
  `is_canonical_manager_record`, `canonical_owner_tid`, and runtime-handle PID
  helpers. Expect to retire or narrow manager-specific helpers after the new
  service registry lands.
- `docs/agent-context/runbooks/runtime-and-context-patterns.md` sections 6 and
  7.

Comprehension questions before editing:

- What is the exact moment a public spawn request becomes owned by a manager?
- Which current service evidence comes from durable task history, and which
  evidence should instead become runtime service registry state?
- Which command-side manager operations currently read `weft.state.managers`,
  and what service key should replace that lookup?
- Which cleanup operations are allowed to delete runtime-only state, and which
  lifecycle queues are never in scope?

Current structure:

- Manager registry convergence is bespoke in `Manager` and command-side
  `manager_runtime.py`. Both reduce `weft.state.managers` independently.
- Internal singleton convergence is partly centralized in
  `manager_services.py`, but it derives durable evidence from `weft.log.tasks`
  and TID mappings and does not use a runtime service registry.
- Runtime-state pruning has explicit command surfaces and safe exact-delete
  rules, but the normal heartbeat path does not yet keep manager/service
  registry rows bounded.

## 4. Target Model

Add one runtime-internal queue:

```text
weft.state.services
```

Each row is a service-owner observation. It is runtime state, not task history,
and may be pruned by exact message ID. It replaces `weft.state.managers` as the
canonical active-manager registry.

Timestamp semantics:

- `timestamp` is the broker-assigned queue message ID or the exact timestamp
  returned by the write path, not a locally invented wall-clock field.
- Expiry compares broker message timestamp against `time.time_ns()` with the
  existing manager/service stale-timeout constants converted to nanoseconds.
- The design relies on SimpleBroker's hybrid timestamp ordering being anchored
  to wall-clock nanoseconds for current freshness checks.

Minimum service owner payload shape:

```json
{
  "schema": "weft.service_owner.v1",
  "service_key": "manager:weft.spawn.requests:<context>",
  "service_type": "manager",
  "owner_tid": "1778525436678897664",
  "status": "active",
  "timestamp": 1778525436678897664,
  "queues": {
    "requests": "weft.spawn.requests",
    "reserved": "T1778525436678897664.reserved",
    "internal_requests": "T1778525436678897664.internal_spawn.requests",
    "internal_reserved": "T1778525436678897664.internal_spawn.reserved",
    "ctrl_in": "weft.manager.ctrl_in",
    "ctrl_out": "weft.manager.ctrl_out",
    "outbox": "weft.manager.outbox"
  },
  "runtime_handle": {},
  "metadata": {}
}
```

Schema projection contract:

- `runtime_handle` remains a top-level field and preserves the current manager
  runtime-handle evidence shape exactly. Manager command adapters may project
  it into existing manager-list output.
- Queue names live under `queues`. Manager PING/PONG matching must be updated
  to read nested queue names, or a single projection helper must translate
  service-owner rows into the legacy manager-record shape before matching. Do
  not leave both interpretations in separate call sites.
- Unknown or missing service-owner schema versions are ignored as ownership
  evidence and logged at debug level. They must not count as live, stopped, or
  uncertain owners.

Service key rules:

- Canonical manager:
  `manager:weft.spawn.requests:<context-key>`
- Heartbeat:
  `internal:heartbeat:<context-key>`
- TaskMonitor:
  `internal:task-monitor:<context-key>`
- Autostart ensure service:
  `autostart:<manifest-source-or-normalized-path>`

The implementation must centralize service-key construction enough that manager
and command code cannot accidentally choose different manager keys. If a
context key already exists in `WeftContext`, use it. If not, define a narrow
helper from the resolved context path or broker target and document the choice.
For file-backed contexts, the key should be derived from the normalized
resolved `BrokerTarget.target_path`; for non-file backends, include the backend
kind plus a stable backend identity. Avoid ad hoc strings from current working
directory, display name, or CLI input.

Reducer output should represent at least:

- canonical live owner, if any
- lower live owner exists for a given local owner
- expired message IDs
- older message IDs for the same service key and owner TID
- duplicate live owners for the same service key
- uncertain evidence that should cause wait/degraded behavior, not duplicate
  launch

`unknown` and `none` are distinct states:

- `none`: the registry read succeeded and found no usable live or uncertain
  owner for the service key.
- `unknown`: the registry read, parse, liveness probe, or fallback evidence
  path failed in a way that could hide a live owner. Callers should wait,
  degrade, or avoid duplicate launch depending on their service-specific
  authority.

### Service Row Lifecycle

Manager service rows:

- On manager startup and each refresh, the manager writes one active
  `service_type="manager"` row only when it is a valid active candidate. It
  prunes expired rows and older rows for its own service key plus owner TID by
  exact message ID.
- If a recent lower-TID manager owner exists and this manager has no actionable
  owned work, the manager skips active publication and yields through the
  normal leadership path. Publication suppression is not a substitute for
  `_maybe_yield_leadership()`.
- If a higher-TID manager has successfully reserved public work or owns
  internal work, it may publish a bounded visibility row while draining. Use
  `status="active"` only if it remains an active candidate; otherwise use a
  status such as `draining` if command visibility is needed. Do not make
  unreserved public backlog a reason to publish.
- On graceful unregister or forced stop, write a bounded stopped row for the
  manager service key so stop callers can observe completion. The next active
  publication or prune pass may delete older stopped rows.

Internal service rows:

- Preferred ownership model: the manager publishes rows for services it
  supervises when it launches, refreshes, reaps, or marks them stopped. The
  child service task does not need to own registry writes unless its runtime
  evidence cannot be observed by the manager.
- A launched heartbeat, TaskMonitor, or autostart service gets one active row
  per service key plus owner TID. Refresh replaces older self rows by exact
  deletion after the new row is durable.
- Reap/terminal detection writes a stopped or terminal row, then prunes older
  active rows for the same owner. Restart decisions must ignore expired active
  rows and honor terminal proof for the same TID.
- Normal manager refresh prunes expired service rows for all service keys it
  owns or observes. Explicit runtime prune remains the operator path for broad
  cleanup.

## 5. Invariants and Constraints

- Preserve public spawn dispatch authority: only successful reservation into a
  manager reserved queue authorizes that manager to launch that exact public
  spawn request.
- Do not turn lowest-TID manager ownership into a hard public-dispatch lock.
  Lowest live TID controls priority, status selection, and convergence, not
  launch authority for already-reserved public work.
- Pending `weft.spawn.requests` backlog is not owned manager work.
- All convergent service owner selection must use `service_convergence.py`.
  Do not leave manager-specific or service-specific local implementations of
  latest-by-owner reduction, expiry classification, lowest-live-TID selection,
  recent-lower-owner checks, or duplicate live-owner selection.
- Service-specific evidence adapters remain local. Docker/external-supervisor
  liveness, PING/PONG probing, task terminal proof, and duplicate kill
  authority do not move into the generic helper.
- Registry evidence is primary for convergent services. Durable task-log and
  TID-mapping probes are fallback or lifecycle evidence only. For each
  service key, use task-log fallback only when there is no non-expired usable
  registry row, or when registry evidence is explicitly `unknown`.
- `weft.state.services` is runtime-only. It must be excluded from dump/load and
  may be pruned by exact message ID. Do not prune `weft.log.tasks`,
  task-local queues, spawn queues, manager control queues, or TID mappings as
  part of this path.
- Use generator-based queue reads for append-style histories. No
  correctness-critical fixed `peek_many(limit=...)`.
- Cleanup failures are best-effort diagnostics. A failed delete must not
  prevent a manager from servicing already-reserved work.
- No new external dependency, public CLI flag, TaskSpec field, or SimpleBroker
  storage schema. Adding the runtime queue `weft.state.services` is in scope.
- No compatibility fallback to `weft.state.managers`. This is runtime-internal;
  update all in-repo producers and consumers in the same slice.

## 6. Rollback and Rollout

This is a coordinated runtime-internal contract change. Rollback is code-level:
revert the service registry integration and restore the old manager registry
code. No user task data migration is required because both old and new queues
are runtime-only.

Operational rollback requires bringing down live managers first. Mixed old/new
managers may disagree about the authoritative runtime registry, so do not roll
back by deploying old code while new managers remain alive.

No mixed-version compatibility is required for this repository slice. However,
the implementation must update all producers and consumers together before
tests pass:

- manager publication
- manager selection/bootstrap/list/status/stop
- internal service convergence
- runtime-state pruning
- dump/load runtime-state exclusions
- specs and quick reference

Operational success after deploy:

- `weft.state.services` remains bounded under steady heartbeat publication.
- `weft.state.managers` is absent, unused, or no longer grows because nothing
  publishes manager active rows there.
- `weft manager list --json` selects manager service owners from
  `weft.state.services`.
- A higher-TID manager with only unreserved public backlog stops publishing
  active manager service ownership and yields; a higher-TID manager with
  already-reserved public work finishes that work.

## 7. Tasks

1. Define the canonical service convergence module and schema.

   Outcome: `weft/core/service_convergence.py` owns the shared service-owner
   model and all common convergence decisions.

   Files:
   - Add `weft/core/service_convergence.py`.
   - Add `tests/core/test_service_convergence.py`.
   - Update `weft/_constants.py` with `WEFT_SERVICES_REGISTRY_QUEUE`.

   Implementation:
   - Define typed owner evidence, for example:

     ```python
     @dataclass(frozen=True, slots=True)
     class ServiceOwnerRecord:
         service_key: str
         service_type: str
         owner_tid: str
         status: Literal["active", "stopped", "terminal", "uncertain"]
         timestamp: int
         payload: Mapping[str, Any]
     ```

   - Define `ServiceConvergenceDecision` or equivalent that reports canonical
     live owner, expired message IDs, older rows for same owner, duplicate live
     owners, recent lower owner, and uncertainty.
   - Add pure helpers:
     - `service_owner_tid_key(record)`
     - `reduce_latest_by_service_owner(records)`
     - `select_canonical_live_owner(records)`
     - `timestamp_is_expired(timestamp, now_ns, ttl_ns)`
     - `plan_service_registry_prune(records, now_ns, ttl_ns)`
     - `has_recent_lower_live_owner(records, own_tid, now_ns, ttl_ns)`
     - `reduce_service_ownership(service_key, records, own_tid, now_ns, ttl_ns)`
   - Keep parsing of manager rows, task logs, runtime handles, Docker handles,
     and PING/PONG outside this module. Callers adapt trusted evidence into the
     shared `ServiceOwnerRecord` shape.
   - Include a parser that accepts only `schema="weft.service_owner.v1"` rows
     and returns ignored/unknown diagnostics for missing or unsupported schema.
   - Include a registry-read result type that distinguishes `none` from
     `unknown`.

   Tests:
   - Lowest numeric live owner wins.
   - Lexical TID ordering cannot choose the wrong owner.
   - Expired rows become prune candidates.
   - Older rows for same service key and owner TID become prune candidates.
   - Recent lower live owner suppresses publication; expired lower owner does
     not.
   - Duplicate live owners are reported, not silently ignored.
   - Unknown schema rows are ignored and logged, and do not become uncertain
     owner evidence by themselves.
   - `none` and `unknown` decisions remain distinct in reducer output.

   Stop gate:
   - Stop if the helper needs to import `Manager`, `Queue`, `RunnerHandle`, or
     service metadata constants. That means policy is leaking into the shared
     primitive.

2. Replace manager registry publication with service-owner publication.

   Outcome: the manager publishes its ownership of the canonical manager
   service to `weft.state.services` and stops writing active rows to
   `weft.state.managers`.

   Files:
   - `weft/core/manager.py`
   - `weft/_constants.py`
   - `tests/core/test_manager.py`

   Implementation:
   - Add a manager service-key helper, for example
     `_manager_service_key()` or a shared helper in `service_convergence.py`.
   - Replace `_register_manager()` with service-owner publication:
     - build `service_key`
     - read existing `weft.state.services` rows for that key
     - adapt eligible rows to `ServiceOwnerRecord`
     - prune expired and older self rows by exact message ID
     - if a recent lower live owner exists and this manager has no owned work
       requiring active visibility, skip active publication
     - if this manager has reserved public work or internal owned work but is
       not canonical, publish only the bounded visibility status chosen in the
       target lifecycle model
     - otherwise write one active service-owner row for this manager
   - Replace `_unregister_manager()` with a stopped service-owner row or exact
     cleanup according to the shared reducer. Preserve enough stopped evidence
     for stop callers to observe success.
   - Keep process-title and `weft.log.tasks` manager lifecycle events
     unchanged.

   Tests:
   - Manager startup writes one `weft.state.services` active owner row.
   - Manager heartbeat refresh prunes older self rows.
   - Manager cleanup/stopped path leaves bounded stopped/current evidence.
   - No active manager row is written to `weft.state.managers`.
   - Expired service-owner rows are pruned during manager refresh.
   - Two managers that start near-simultaneously can both publish once, then
     the higher-TID manager yields on the next refresh after seeing the lower
     live owner.
   - Higher-TID manager with reserved work remains visible according to the
     chosen draining/active status and does not hide work it already reserved.

   Stop gate:
   - Stop if this starts changing task lifecycle events in `weft.log.tasks` or
     public spawn queue semantics.

3. Route manager leadership and publication suppression through the service convergence reducer.

   Outcome: manager `_leader_tid()`, `_evaluate_dispatch_ownership()`, and
   `_maybe_yield_leadership()` use service convergence state for the manager
   service key.

   Files:
   - `weft/core/manager.py`
   - `tests/core/test_manager.py`

   Implementation:
   - Replace `_read_active_manager_records()` with a service-owner replay for
     the manager service key.
   - Runtime liveness and PING/PONG evidence remain manager-specific adapters
     before creating shared service-owner records.
   - Preserve self-liveness rule: a manager treats its own current active row
     as live while it has not unregistered and is not stopping.
   - Continue to allow already-reserved public work to launch even if this
     manager is not canonical.

   Tests:
   - Lower live manager service owner wins.
   - Higher-TID manager with only public backlog yields.
   - Higher-TID manager with already-reserved public work does not yield before
     servicing it.
   - Stale/expired lower service owner does not suppress local publication.
   - Unknown registry read confidence degrades without abandoning reserved work.
   - PING/PONG rescue still proves liveness against the new service-owner
     schema or the single legacy projection helper, including nested queue
     fields.

4. Migrate command-side manager lifecycle readers to `weft.state.services`.

   Outcome: `weft manager start|list|status|stop`, `weft status`, and
   `weft run` bootstrap selection all use manager service-owner rows from
   `weft.state.services`.

   Files:
   - `weft/core/manager_runtime.py`
   - `weft/commands/manager.py`
   - `weft/commands/status.py`
   - `weft/commands/system.py`
   - `tests/commands/test_run.py`
   - `tests/commands/test_manager_commands.py`
   - `tests/commands/test_status.py`

   Implementation:
   - Replace `_registry_queue()` or narrow its meaning to the service registry.
   - Rename internal helpers if useful, but keep public command behavior the
     same.
   - Keep manager-specific liveness proof in `manager_runtime.py`:
     runtime-handle stale checks and PING/PONG rescue happen before adapting
     rows into service-owner records.
   - Migrate `_mark_manager_stopped()` to write stopped service-owner rows to
     `weft.state.services`.
   - Migrate `_await_manager_stop_confirmation()` to wait on stopped
     service-owner evidence for `service_type="manager"` and the manager
     service key.
   - Migrate `_external_supervisor_record_is_live()` to evaluate projected
     manager service-owner records, preserving current runtime-handle
     semantics.
   - Remove command-side reads of `WEFT_MANAGERS_REGISTRY_QUEUE`.
   - Keep output fields compatible where practical by projecting service-owner
     rows into the existing manager snapshot dataclasses. This is not external
     backward compatibility for the queue; it is preserving command UX.
   - `weft manager list --json` must filter `service_type == "manager"` and
     the manager service key. If `include_stopped` exists in a helper, it should
     include bounded stopped service-owner rows, not historical task rows or
     expired registry rows.

   Tests:
   - `weft manager list --json` sees managers from `weft.state.services`.
   - `_ensure_manager()` reuses an active manager service owner.
   - `_start_manager()` startup proof waits for the launched manager's service
     owner row.
   - `_stop_manager()` observes stopped service-owner evidence.
   - PING/PONG stale-manager rescue still works.
   - Forced-stop paths write and observe stopped rows through
     `weft.state.services`.
   - Manager list ignores non-manager service rows in the shared registry.

   Stop gate:
   - Stop if a fallback reader for `weft.state.managers` appears. The slice is
     full migration, not compatibility.

5. Migrate internal singleton services to publish and reduce service-owner rows.

   Outcome: heartbeat, TaskMonitor, and autostart ensure services use the same
   service registry and convergence reducer as the manager.

   Files:
   - `weft/core/manager.py`
   - `weft/core/manager_services.py`
   - `weft/core/tasks/heartbeat.py` and `weft/core/tasks/task_monitor.py` only
     if service-owner publication belongs inside the service task rather than
     manager-authored launch evidence.
   - `tests/core/test_manager.py`
   - `tests/core/test_task_monitoring.py`

   Implementation:
   - Decide one owner-publication model and document it in code:
     - preferred: manager publishes service-owner rows for manager-supervised
       children when it launches/reaps them, because manager owns supervision;
       child task lifecycle remains in `weft.log.tasks`.
   - Replace service live-owner selection based primarily on task-log scans
     with service registry rows plus targeted task-log/TID-mapping probes only
     when registry evidence is uncertain or missing.
   - Define one adapter function for service-key lookup, for example:

     ```python
     def resolve_service_ownership(
         service_key: str,
         *,
         registry_rows: Sequence[Mapping[str, Any]],
         fallback_evidence: Sequence[ServiceOwnerRecord],
         now_ns: int,
         ttl_ns: int,
     ) -> ServiceConvergenceDecision: ...
     ```

     The adapter must use registry rows first. Fallback evidence is consulted
     only for `none` or `unknown`, and the final decision must preserve whether
     uncertainty came from registry read/parsing or fallback probing.
   - Keep terminal-over-live semantics. A terminal owner row or task-owned
     terminal proof for the same TID wins over stale live evidence.
   - Use `service_convergence.py` for canonical live owner, duplicate owner
     detection, expiry, and prune planning.

   Tests:
   - Manager starts exactly one heartbeat and one TaskMonitor service owner.
   - Duplicate live TaskMonitor owners reduce to lowest live TID and
     non-canonical owner is signaled according to existing authority rules.
   - Expired service-owner rows do not block restart.
   - Recent uncertain owner evidence causes wait/degraded behavior rather than
     duplicate launch.
   - TaskMonitor convergence reads `weft.state.services` as primary evidence
     and does not delete unrelated service-owner rows while scanning tasks.
   - Child-service rows remain bounded across repeated restart/reap cycles.

   Stop gate:
   - Stop if the implementation starts treating `weft.log.tasks` as runtime
     registry truth for normal service convergence. Task logs are fallback or
     lifecycle evidence, not the primary service registry after this change.

6. Update runtime-state pruning, dump/load, and queue references.

   Outcome: runtime-only tooling understands `weft.state.services` and stops
   treating `weft.state.managers` as canonical.

   Files:
   - `weft/_constants.py`
   - `weft/commands/runtime_prune.py`
   - `weft/commands/dump.py`
   - `weft/commands/load.py`
   - `weft/core/tasks/task_monitor.py`
   - `tests/commands/test_runtime_prune.py`
   - `tests/commands/test_dump_load.py`

   Implementation:
   - Add service registry pruning classes if existing runtime prune reporting
     needs a manager-specific label replacement.
   - Ensure dump/load excludes `weft.state.services`.
   - Remove or update `WEFT_MANAGERS_REGISTRY_QUEUE` references. If the
     constant remains temporarily for tests or cleanup, it must not be used by
     active manager/service paths.
   - Add a grep-backed test or spec test that fails if active runtime code
     imports `WEFT_MANAGERS_REGISTRY_QUEUE` or references
     `weft.state.managers` outside historical plans, explicit migration notes,
     or one-time cleanup code.

   Tests:
   - Runtime prune can report/delete expired service-owner rows by exact
     message ID.
   - Dump/load excludes `weft.state.services`.
   - No command test expects `weft.state.managers` as an active queue.
   - Runtime prune deletes service rows by exact message ID and does not delete
     task log, TID mapping, spawn, or manager control records.

7. Update specs and docs.

   Outcome: specs make `weft.state.services` the normative runtime service
   registry.

   Files:
   - `docs/specifications/00-Quick_Reference.md`
   - `docs/specifications/03-Manager_Architecture.md`
   - `docs/specifications/05-Message_Flow_and_State.md`
   - `docs/specifications/07-System_Invariants.md`
   - `docs/agent-context/runbooks/runtime-and-context-patterns.md`
   - `docs/plans/README.md`

   Required wording:
   - `weft.state.services` owns runtime service ownership evidence for managers
     and internal services.
   - `weft.state.managers` is removed or no longer canonical.
   - Manager is a convergent service with the same service-owner reducer as
     heartbeat, TaskMonitor, and autostart ensure services.
   - Public reserved spawn work remains launch-authoritative.
   - Runtime-state cleanup may prune service-owner rows by exact message ID,
     but never task lifecycle truth.
   - Update exact existing references:
     - `docs/specifications/03-Manager_Architecture.md` [MA-0], [MA-1.4],
       and [MA-3], which currently name `weft.state.managers`.
     - `docs/specifications/07-System_Invariants.md` [OBS.15] and
       [MANAGER.3], which currently name `weft.state.managers`.
     - `docs/specifications/05-Message_Flow_and_State.md` runtime-state
       queue references.
     - `docs/specifications/00-Quick_Reference.md` queue table and runtime
       state movement examples.

8. Verification and review.

   Required local commands:

   ```bash
   . ./.envrc
   ./.venv/bin/python -m pytest tests/core/test_service_convergence.py -v
   ./.venv/bin/python -m pytest tests/core/test_manager.py -k "registry or leadership or reserved_public or service_convergence or task_monitor" -v
   ./.venv/bin/python -m pytest tests/core/test_manager.py tests/cli/test_cli_serve.py -v
   ./.venv/bin/python -m pytest tests/commands/test_run.py tests/commands/test_manager_commands.py tests/commands/test_status.py -k "manager or registry or services or supervisor or pong" -v
   ./.venv/bin/python -m pytest tests/commands/test_runtime_prune.py tests/commands/test_dump_load.py -v
   ./.venv/bin/python -m pytest tests/specs/quick_reference/test_queue_names.py -v
   ./.venv/bin/ruff check weft/core/service_convergence.py weft/core/manager_services.py weft/core/manager.py weft/core/manager_runtime.py
   ./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -v
   ```

   Independent review:
   - Fresh-eyes review was requested for this draft because it touches
     runtime-only queues, manager lifecycle, command bootstrap, and internal
     service supervision.
   - Review result: the first draft was not implementable confidently because
     it missed forced-stop registry writers, child-service row lifecycle,
     fallback gating, context-key construction, timestamp semantics, PONG
     projection, internal queue fields, and exact spec references. Those gaps
     are now explicit requirements in this plan.
   - A second independent review is required after implementation, or earlier
     if the implementer changes the target schema or lifecycle rules.
   - Original review prompt:

     > Read `docs/plans/2026-05-11-service-convergence-and-manager-registry-bounding-plan.md`,
     > `weft/core/manager.py`, `weft/core/manager_services.py`, and
     > `weft/core/manager_runtime.py`. Look for places where the plan leaves a
     > second convergence path alive, queue cleanup risks, and service-registry
     > migration gaps. Do not implement anything. Could you implement this
     > confidently and correctly if asked?

   Post-deploy observation:
   - `weft.state.services` depth remains bounded under steady manager and
     internal service heartbeat load.
   - `weft.state.managers` no longer grows.
   - `weft manager list --json` selects the expected manager service owner.
   - Internal heartbeat and TaskMonitor maintain one canonical live owner each.

## 8. Out of Scope

- No manager lease, lock, or backend-specific coordination primitive.
- No public CLI flag or output redesign beyond projecting service-owner rows
  into existing manager/status outputs.
- No cleanup of `weft.log.tasks`, task-local queues, TID mappings, spawn
  queues, or manager control queues.
- No Docker-specific liveness logic in `service_convergence.py`.
- No mixed-version compatibility reader for `weft.state.managers`.
- No attempt to make multiple managers a load-balancing product feature.

## 9. Known Risks

- This is broader than a helper extraction. It changes the runtime-internal
  registry surface and must update all producers and consumers together.
- If service-specific authority leaks into `service_convergence.py`, the module
  will become an orchestration framework. Keep it to shared convergence
  decisions and use adapters for evidence.
- If task-log fallback remains primary service truth, the migration did not
  achieve the goal. The service registry should become the normal runtime
  ownership surface.
- Pruning recent rows would hide real split-brain evidence. Use exact message
  IDs, message timestamps, and the shared reducer's prune plan.
- Suppressing higher-TID publication too aggressively could make diagnostics
  harder while a secondary manager still owns reserved work. Preserve task-log
  and operational-log evidence for launches; suppress only active service-owner
  candidacy when lower live ownership is recent.

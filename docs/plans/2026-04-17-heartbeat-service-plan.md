# Heartbeat Service Plan

Status: completed
Source specs: see Source Documents below
Superseded by: none

Post-landing note (2026-04-17):

- The slice landed as one built-in runtime heartbeat service plus a narrow
  helper surface in `weft/core/heartbeat.py`.
- The prerequisite manager fence issues called out in Task 1 were fixed in the
  same implementation slice before the heartbeat service started depending on
  them. Managers now keep exact fenced spawn requests recoverable across
  `other` / `none` / `unknown` suspension, skip idle-yield and autostart while
  recovery is pending, and requeue the original exact request before later
  inbox work resumes when ownership returns to `self`.
- The internal heartbeat endpoint moved under the reserved `_weft.` namespace,
  and public naming or endpoint-claim surfaces reject that namespace.
- Internal runtime task-class selection for heartbeat now rides on a
  manager-owned spawn envelope. Public TaskSpec submission surfaces strip the
  reserved metadata so user-authored TaskSpecs cannot spoof internal runtime
  classes or reserved internal endpoints through ordinary submission helpers.
- An independent subagent review was run on the landed implementation. Its
  actionable feedback tightened the service wait fallback and the internal
  runtime trust boundary before final spec close-out.

## 1. Goal

Add one narrow built-in heartbeat facility for runtime-scoped periodic work.
The feature should be a best-effort interval emitter, not a scheduler. One
internal persistent task per Weft context multiplexes many named heartbeat
registrations, sleeps until the next due interval, re-checks canonical
ownership before each emit, and writes one preconfigured message to one
ordinary destination queue. Registrations are runtime-only and disappear on
restart. The service should auto-start on first registration rather than
running as an always-on empty daemon.

Definition of done for the implementation slice:

- one internal runtime-owned heartbeat service task exists and is launched
  through the ordinary manager path
- the service claims one reserved internal endpoint name under a namespace the
  public endpoint surfaces cannot claim, and uses the existing endpoint
  canonical-owner rule before each emit
- one heartbeat service instance can multiplex many registrations inside one
  process
- heartbeat registrations are runtime-only and are not restored after restart
- each heartbeat emits at most one message per wake cycle and never replays
  missed intervals
- duplicate service startup races converge by process exit, not by passive
  standby; non-canonical service claimants exit promptly and no failover of
  in-memory registrations is promised
- helper code can lazily ensure the service exists and submit upsert/cancel
  requests without introducing a second scheduler or launch path
- helper startup waits for live endpoint resolution, not raw registry claim
  appearance, and fails explicitly on bounded timeout
- the runtime wait model is explicit: no hot default poll loop, no full-
  interval blind sleep; control and registration updates stay responsive while
  idle
- after implementation and targeted verification, an independent subagent
  review is run and its actionable feedback is addressed before close-out
- if any `*A-*` planned companion specs are touched during implementation, the
  authoritative heartbeat contract is migrated into the numbered canonical
  specs as the last implementation step, after code, tests, and subagent
  review, rather than being left behind in A docs
- no cron syntax, wall-clock scheduling, timezone logic, catch-up replay, or
  exact-once guarantees are introduced
- current specs, tests, and nearby implementation notes describe the landed
  heartbeat contract honestly as best-effort interval emission

## 2. Source Documents

Source specs:

- [`docs/specifications/00-Overview_and_Architecture.md`](../specifications/00-Overview_and_Architecture.md)
- [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md) [CC-2.2], [CC-2.3], [CC-2.4.1], [CC-2.5]
- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md) [TS-1]
- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md) [MA-1.1], [MA-1.2], [MA-1.4], [MA-1.6]
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md) [MF-3.1], [MF-6], [MF-7]
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md) [QUEUE.4], [QUEUE.5], [QUEUE.6], [MANAGER.1], [MANAGER.4], [MANAGER.8], [MANAGER.9], [MANAGER.10], [MANAGER.12]
- [`docs/specifications/12-Future_Ideas.md`](../specifications/12-Future_Ideas.md)

Related current plans:

- [`docs/plans/2026-04-17-canonical-owner-fence-plan.md`](./2026-04-17-canonical-owner-fence-plan.md) — completed canonical-owner fence slice. Reuse its shared ownership rule and manager-side launch fence. Do not redesign singleton semantics here.
- [`docs/plans/2026-04-16-runtime-endpoint-registry-boundary-plan.md`](./2026-04-16-runtime-endpoint-registry-boundary-plan.md) — completed endpoint-registry slice. Reuse named endpoint claims instead of inventing a heartbeat-specific routing plane.
- [`docs/plans/2026-04-16-autostart-hardening-and-contract-alignment-plan.md`](./2026-04-16-autostart-hardening-and-contract-alignment-plan.md) — current autostart contract. Reuse its manager-owned spawn path, but do not turn heartbeat into a durable autostart-by-default subsystem.

Repo guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/runtime-and-context-patterns.md`](../agent-context/runbooks/runtime-and-context-patterns.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)
- [`docs/lessons.md`](../lessons.md)

No dedicated shipped spec exists yet for:

- heartbeat registration payloads
- a built-in runtime heartbeat service
- lazy ensure of an internal named service on first use

Treat this as a new feature slice. The implementation must update the canonical
specs listed above before the feature is considered done.

## 3. Context and Key Files

### Files To Modify

Implementation:

- `weft/_constants.py`
- `weft/core/manager.py`
- `weft/core/endpoints.py` only if small endpoint-owned helpers are needed
- `weft/core/tasks/__init__.py`
- `weft/core/tasks/heartbeat.py` (new)
- `weft/core/heartbeat.py` or `weft/core/heartbeats.py` (new helper module for
  ensure/upsert/cancel)
- `tests/core/test_manager.py`
- `tests/tasks/test_task_endpoints.py`
- `tests/tasks/test_heartbeat.py` (new)
- `tests/core/test_heartbeat_helpers.py` or similar (new)

Docs and traceability:

- `docs/specifications/01-Core_Components.md`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/12-Future_Ideas.md`
- `docs/lessons.md` if the slice exposes a durable design rule worth keeping

### Read First

- `weft/core/manager.py`
  - owns internal runtime task-class selection, canonical-owner fencing, and
    the ordinary manager child-launch path
- `weft/core/endpoints.py`
  - already provides runtime-only named endpoint claims with lowest-live-TID
    canonical resolution
- `weft/core/tasks/base.py`
  - owns queue wiring, control handling, task lifecycle, and endpoint claim
    registration for persistent runtime-owned tasks
- `weft/core/tasks/pipeline.py`
  - example of an internal runtime-owned task class selected through manager
    metadata rather than a new public `spec.type`
- `weft/core/taskspec.py`
  - current TaskSpec leaf contract and runtime metadata rules
- `tests/tasks/test_task_endpoints.py`
  - current public proof for named endpoint claims and canonical resolution
- `tests/core/test_manager.py`
  - manager-owned tests for internal task-class routing and ownership fences

### Style And Guidance

- `AGENTS.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/testing-patterns.md`

### Shared Paths And Helpers To Reuse

- `weft/core/manager.py::_resolve_child_task_class()`
- `weft/core/endpoints.py::resolve_endpoint()` and `list_resolved_endpoints()`
- `weft/core/tasks/base.py` endpoint-claim wiring
- `weft/helpers.py::canonical_owner_tid()`
- ordinary spawn submission through `weft.spawn.requests`

Do not duplicate:

- a second service-discovery table
- a second manager/bootstrap path for heartbeat startup
- a second ownership rule outside the endpoint registry
- queue-history reads built around fixed limits when generator-based replay is
  already available

### Current Structure

- Internal runtime task classes already exist for pipelines. The manager picks
  them through `metadata[INTERNAL_RUNTIME_TASK_CLASS_KEY]` rather than by
  expanding the public TaskSpec type system.
- Persistent tasks can already claim one stable runtime endpoint name through
  the current endpoint registry. Duplicate live claimants are tolerated and the
  lowest live TID is canonical.
- No reserved internal endpoint namespace exists yet. User-facing naming paths
  and task-side `register_endpoint_name()` can currently claim any normalized
  endpoint string, so an internal name like `weft.heartbeat` is not protected.
- The canonical-owner fence now exists at manager launch time, but the current
  review surfaced four heartbeat-relevant manager risks plus a test-gap that
  should be fixed before this feature lands:
  - when ownership later resolves back to `self`, the current code clears
    suspension without first recovering the original fenced exact request from
    `T{tid}.reserved`; a later inbox request can therefore run while the older
    fenced message remains stranded
  - stranded fenced requests can still be orphaned by immediate leadership
    yield
  - suspended managers can still idle-shutdown while fenced reserved work is
    unrecovered
  - suspended managers still run `_tick_autostart()`, which can enqueue
    repeated ensure-style autostart requests while dispatch is suspended
  - current tests do not yet prove the real recovery invariant that the
    original fenced reserved message is recovered before later inbox work
- The current task loop is process-based and poll-based. Many one-process-per-
  timer tasks would create many always-on idle loops. A heartbeat facility
  should therefore multiplex registrations inside one service task.
- `resolve_endpoint()` already means “live resolved owner,” not “raw claim
  exists in the registry.” Helper startup therefore needs to wait for
  successful endpoint resolution, not just a registry write.
- Passive duplicate service daemons are not acceptable here. Registrations are
  in-memory only, so a passive duplicate would both leak a persistent process
  and still fail to preserve state if the canonical owner later died.

### Comprehension Checks

1. Which current code path lets a runtime-owned task class launch without
   adding a new public `spec.type`, and where is that selection made today?
2. How do named endpoint claims currently choose a canonical live owner when
   duplicate claimants exist?
3. Why would one sleeping task process per heartbeat be a poor fit for the
   current Weft task-loop cost model?
4. What exact restart behavior are we choosing for heartbeat registrations in
   this slice, and why is that honest for a “heartbeat” but not for a
   scheduler?

## 4. Invariants and Constraints

- Keep one durable execution spine:
  `TaskSpec -> Manager -> Consumer/BaseTask -> queues/state log`.
- Do not add a scheduler subsystem, cron parser, wall-clock planner, timezone
  handling, or catch-up replay.
- Do not add a new public `spec.type`.
- Do not add a second discovery or singleton table. Reuse the current named
  endpoint registry and its lowest-live-TID canonical-owner rule.
- Reserve one internal endpoint namespace for runtime-owned services, such as
  `_weft.`. Public naming surfaces such as `weft run --name` must reject that
  namespace, and task-side endpoint claim helpers must reject it unless the
  claim originates from an approved internal runtime task class.
- Keep heartbeat registration runtime-only. This slice must not turn
  `weft.state.*` queues or heartbeat registrations into durable desired state.
- Restart semantics must stay explicit: if Weft or the heartbeat service dies,
  registrations are lost. The helper may recreate them only when some higher
  layer calls it again.
- Heartbeat emission must stay destination-blind. The service writes one
  configured message to one ordinary destination queue. It must not branch on
  “spawn request versus inbox versus anything else.”
- Emit semantics:
  - interval-based only
  - integer seconds with a floor of at least 60 seconds in the first slice
  - no replay of missed intervals
  - when late, coalesce to at most one emit and schedule the next future slot
- Ownership semantics:
  - only the canonical owner for the reserved internal heartbeat endpoint may
    emit
  - non-canonical duplicate service claimants exit promptly after they can
    positively prove another canonical owner; they do not remain passive
    standby daemons
  - no failover of in-memory registrations is promised when the canonical
    service dies
  - ownership is re-checked immediately before each emit, not only at startup
- Startup semantics:
  - no always-on empty daemon by default
  - lazy ensure on first registration is allowed
  - first-use startup must still go through the ordinary manager/spawn path
  - helper wait means `resolve_endpoint(ctx, internal_name) is not None`, not
    merely “a claim record was written”
  - helper startup uses a bounded timeout aligned to
    `MANAGER_STARTUP_TIMEOUT_SECONDS` unless implementation proves a different
    named timeout is needed
- Idle semantics:
  - the heartbeat service may exit after a configurable idle timeout when it
    has zero registrations
  - exit due to emptiness must not silently drop registrations because
    registrations are already runtime-only and in-memory
- Wait semantics:
  - the heartbeat service must not rely on `BaseTask.run_until_stopped()` with
    the default 50 ms process poll interval
  - the heartbeat service must not call one long `sleep(time_until_next_due)`
    that delays STOP, cancel, upsert, or endpoint-loss response until the full
    interval expires
  - the service owns a custom wait loop that waits on `inbox` and `ctrl_in`
    queue activity with timeout equal to the next due deadline
  - if queue-native waiting cannot be established on the active backend, the
    fallback sleep chunk must be explicitly bounded to at most 1.0 second per
    turn
- Keep the first slice narrow:
  - no public CLI scheduler surface
  - no listing or inspection command unless tests or implementation prove it is
    required for correctness
  - no durable `weft.state.heartbeats` registry unless a concrete operator
    surface needs it
- Any temporary edits to planned companion docs such as `01A-*`, `03A-*`,
  `05A-*`, or `07A-*` must be folded into the numbered canonical specs as the
  last implementation step. Do not leave the shipped heartbeat contract only in
  A docs.
- Dependency gate: do not land heartbeat on top of a canonical-owner fence that
  can still orphan reserved spawn requests or enqueue repeated autostart work
  while suspended. Fix those fence issues first or fold that hardening into the
  first heartbeat implementation slice before adding the service itself.
- Registration semantics must be explicit in the first slice:
  - `upsert` resets `next_due_at` to `now + interval_seconds`
  - `cancel` removes the registration immediately
  - `message` may be a string or any JSON-serializable value
  - strings write unchanged to the destination queue
  - non-string payloads are serialized with `json.dumps(..., ensure_ascii=False)`

## 5. Tasks

1. Tighten the prerequisite owner-fence behaviors heartbeat depends on.
   - Outcome: the canonical-owner fence is safe to rely on for a long-lived
     passive service.
   - Files to touch:
     - `weft/core/manager.py`
     - `tests/core/test_manager.py`
   - Read first:
     - `docs/specifications/03-Manager_Architecture.md` [MA-1.4], [MA-1.6]
     - `docs/specifications/05-Message_Flow_and_State.md` [MF-6]
     - the current suspended-manager branch in `Manager.process_once()`
   - Required behavior:
     - replace the current string-only dispatch-suspension flag with structured
       manager-owned suspension state that records at least
       `ownership_state`, the exact fenced `message_id`, and whether recovery
       is still pending
     - a manager with a reserved spawn request in `T{tid}.reserved` must not
       idle-shutdown or leadership-yield into permanent orphaning without an
       explicit recovery path
     - if `other` ownership is proved and the first exact-message requeue
       fails, the manager stays alive and keeps retrying recovery; it must not
       unregister or immediate-yield while the exact request is still fenced in
       its private reserved queue
     - if ownership later resolves to `self` after a `none` or `unknown`
       suspension, recover the exact request by exact-message requeue back to
       `weft.spawn.requests` before any later inbox work can run; do not
       invent a second dispatch path that launches directly from reserved
     - dispatch-suspended managers must not enqueue repeated ensure-style
       autostart work to themselves while they cannot dispatch it
   - Tests to add or update:
     - suspended `none` / `unknown` manager does not exit while it still owns a
       fenced reserved request without recovering it
     - stranded `other` path does not let immediate leadership yield orphan the
       exact reserved message
     - a recovered `none` / `unknown` suspension requeues the exact reserved
       request once ownership becomes `self`
     - when ownership returns to `self`, the original fenced request is
       recovered before a later inbox request is allowed to launch
     - dispatch-suspended manager does not generate duplicate autostart spawn
       requests over repeated scan intervals
   - Stop and re-evaluate if:
     - the fix wants a second manager-recovery daemon
     - recovery starts rewriting entire queues instead of handling exact
       messages

2. Reserve the internal endpoint namespace and enforce it.
   - Outcome: the heartbeat service endpoint cannot be claimed or shadowed by
     ordinary user-facing persistent tasks.
   - Files to touch:
     - `weft/_constants.py`
     - `weft/core/endpoints.py`
     - `weft/core/tasks/base.py`
     - `weft/commands/run.py`
     - `tests/tasks/test_task_endpoints.py`
     - `tests/commands/test_run.py`
   - Read first:
     - `weft/core/tasks/base.py::register_endpoint_name()`
     - `weft/commands/run.py::_apply_explicit_run_name()`
     - `weft/core/endpoints.py::normalize_endpoint_name()`
   - Required approach:
     - choose one reserved internal endpoint namespace such as `_weft.`
     - move the heartbeat endpoint under that namespace, for example
       `_weft.heartbeat`
     - reject reserved names from public naming paths like `weft run --name`
     - reject reserved names in task-side endpoint registration unless the task
       is an approved internal runtime-owned class
   - Tests to add or update:
     - user-facing `--name _weft.heartbeat` or equivalent reserved names fail
     - ordinary tasks cannot claim reserved internal endpoint names
     - approved internal runtime tasks still can
   - Stop and re-evaluate if:
     - the enforcement wants a second registry or ACL store
     - the reserved namespace starts leaking into normal user naming guidance

3. Add the internal heartbeat service task class and registration model.
   - Outcome: one internal persistent task can accept heartbeat upsert/cancel
     messages and multiplex many registrations in memory.
   - Files to touch:
     - `weft/_constants.py`
     - `weft/core/tasks/heartbeat.py` (new)
     - `weft/core/tasks/__init__.py`
     - `weft/core/manager.py`
     - `tests/tasks/test_heartbeat.py` (new)
     - `tests/core/test_manager.py`
   - Read first:
     - `weft/core/tasks/base.py`
     - `weft/core/tasks/pipeline.py`
     - `weft/core/manager.py::_resolve_child_task_class()`
   - Required approach:
     - add one reserved internal runtime task-class constant such as
       `INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT`
     - implement the service as a `BaseTask` subclass, not as a generic
       sleeping function target
     - claim one reserved internal endpoint name such as `_weft.heartbeat`
     - maintain registrations in memory plus a min-heap or equivalent next-due
       index; the inbox queue is the mutation/control plane, not the schedule
       heap itself
     - implement a service-owned wait loop; do not use the default
       `BaseTask.run_until_stopped()` hot poll and do not use one full-interval
       blind sleep
     - the wait loop should block on `inbox` and `ctrl_in` activity with
       timeout equal to the next due deadline; use a bounded <= 1.0 second
       fallback sleep only if queue-native wait cannot be established
     - define a narrow registration payload shape:
       - `action`: `upsert` or `cancel`
       - `heartbeat_id`: stable per-registration key
       - `interval_seconds`: integer, floor >= 60
       - `destination_queue`
       - `message`
     - `upsert` resets `next_due_at` to `now + interval_seconds`
     - string `message` values write unchanged; non-string JSON-serializable
       values are serialized with `json.dumps(..., ensure_ascii=False)`
     - on wake, emit at most one message per due registration, then advance to
       the next future due time
     - duplicate service races converge by process exit: once a service can
       positively prove another canonical owner for `_weft.heartbeat`, it exits
       promptly instead of remaining a passive duplicate
   - Tests to add or update:
     - service accepts upsert and cancel
     - duplicate upsert by `heartbeat_id` replaces the existing registration
      - late wake coalesces to one emit rather than replaying all missed slots
     - service writes string messages unchanged and serializes structured
       messages consistently
      - manager launches the internal heartbeat runtime class through the normal
       child path
     - duplicate service starts converge to one live owner because losers exit
       rather than remaining passive
   - Stop and re-evaluate if:
     - the implementation wants durable storage for registrations
     - the service starts branching on destination type or inventing retry
       policy

4. Add lazy ensure and the narrow helper surface for first use.
   - Outcome: higher-level code can register a heartbeat without manually
     pre-starting a permanent daemon.
   - Files to touch:
     - `weft/core/heartbeat.py` or `weft/core/heartbeats.py` (new)
     - `weft/core/manager.py` only if a small helper is needed for internal
       runtime launch metadata
     - `tests/core/test_heartbeat_helpers.py` (new)
     - `tests/tasks/test_task_endpoints.py`
   - Read first:
     - `weft/core/endpoints.py`
     - `weft/commands/_manager_bootstrap.py`
     - `weft/core/spawn_requests.py`
   - Required approach:
     - helper first tries to resolve the reserved internal endpoint
       `_weft.heartbeat`
     - if unresolved, helper ensures a manager exists, submits the internal
       heartbeat service spawn request through `weft.spawn.requests`, then waits
       for `resolve_endpoint()` to return a live owner before sending the
       registration message
     - use a bounded startup timeout aligned to
       `MANAGER_STARTUP_TIMEOUT_SECONDS` unless a separate named timeout is
       added deliberately
     - concurrent helpers may still race to spawn, but the runtime convergence
       policy is loser exit, not passive standby or state replication
     - if live endpoint resolution never succeeds before the timeout, return a
       concrete startup failure instead of writing to a guessed queue
     - no new top-level scheduler CLI in this slice
   - Tests to add or update:
     - first registration starts the service and succeeds
     - repeated registrations do not require a second explicit startup step
     - helper waits for live endpoint resolution rather than raw claim
     - duplicate service spawn races converge to one live emitter because
       duplicate losers exit
   - Stop and re-evaluate if:
     - helper logic starts writing directly to guessed `T{tid}.inbox` queues
       before endpoint resolution
     - startup begins bypassing the ordinary manager/spawn path

5. Run an independent subagent review on the landed implementation and address
   the feedback.
   - Outcome: the implementation has an explicit second-pass review after the
     code and primary tests are in place, and any actionable findings are
     either fixed or recorded as deliberate residual risk.
   - Files to touch:
     - no fixed file list; touch the implementation, tests, or docs the review
       finds necessary
   - Read first:
     - `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`
     - the full heartbeat diff
     - targeted heartbeat and manager test results
   - Required approach:
     - ask a subagent from a different model family to review the implemented
       heartbeat slice with a code-review mindset
     - give the subagent the real diff and the targeted verification context,
       not a prose summary only
     - address actionable feedback before final documentation close-out
     - if a finding is intentionally not fixed, record the residual risk in the
       implementation notes or final handoff instead of silently ignoring it
   - Verification:
     - the review happened after implementation, not only on the plan
     - follow-up fixes or explicit residual-risk notes are present

6. Last step: define runtime-visible behavior and complete canonical spec
   updates.
   - Outcome: the shipped feature is documented as a heartbeat service, not
     oversold as scheduling, and any temporary A-doc wording has been migrated
     into the numbered final specs only after the implementation, tests, and
     independent review loop are complete.
   - Files to touch:
     - `docs/specifications/01-Core_Components.md`
     - `docs/specifications/03-Manager_Architecture.md`
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/07-System_Invariants.md`
     - `docs/specifications/12-Future_Ideas.md`
     - any touched `*A-*` planned companion specs only as cleanup or
       de-duplication after their authoritative content is folded back into the
       numbered docs
     - `docs/lessons.md` if implementation reveals a reusable boundary
   - Required spec deltas:
     - one new internal runtime task family entry for the heartbeat service
     - manager/runtime ownership notes for the reserved internal endpoint and
       reserved namespace rule
     - message-flow notes for heartbeat registration messages and queue emission
      - invariants that the service is runtime-only, interval-only, and
       destination-blind
     - convergence notes that duplicate services exit rather than remaining
       passive and that in-memory registrations are not failed over
     - startup/wait notes that helper success means live endpoint resolution,
       not raw claim appearance
     - explicit non-goals: no cron, no catch-up, no durable restart recovery
     - if any `*A-*` planned docs were used during implementation, migrate
       their authoritative heartbeat text into the numbered specs here as the
       last step and leave the A docs either synchronized summary-only or no
       longer carrying unique normative detail
   - Verification:
     - plan backlinks are present
      - implementation mapping notes point at the real code paths
      - no touched spec still implies “scheduler” semantics
      - no shipped heartbeat behavior remains documented only in A docs

## 6. Testing and Verification

Do not mock away the broker or manager lifecycle for the core proofs. Keep real
queue-backed tests for:

- manager launch of the internal runtime task class
- endpoint claim and canonical-owner convergence
- heartbeat registration and queue emission
- suspension and recovery behavior around canonical ownership

Targeted verification commands:

- `./.venv/bin/python -m pytest tests/core/test_manager.py -q`
- `./.venv/bin/python -m pytest tests/tasks/test_task_endpoints.py -q`
- `./.venv/bin/python -m pytest tests/tasks/test_heartbeat.py -q`
- `./.venv/bin/python -m pytest tests/core/test_heartbeat_helpers.py -q`
- `./.venv/bin/python -m pytest tests/core/test_manager.py tests/tasks/test_task_endpoints.py tests/tasks/test_heartbeat.py tests/core/test_heartbeat_helpers.py -q`
- `./.venv/bin/ruff check weft tests`
- `./.venv/bin/mypy weft`

If xdist remains enabled in default pytest config, group broker-heavy heartbeat
tests explicitly when they share one backend file or one long-lived manager
surface.

## 7. Rollout, Rollback, and Non-Goals

Rollout order:

1. prerequisite fence hardening
2. reserved internal endpoint namespace
3. heartbeat service runtime
4. lazy ensure helper
5. independent subagent review and feedback pass
6. final numbered-spec migration and doc updates, including folding any
   touched `*A-*` docs back into the canonical specs as the last step

Rollback notes:

- The helper and service should be revertible without changing the public
  TaskSpec schema.
- Avoid new durable queue names unless they are truly required. Reusing the
  endpoint registry keeps rollback local to helper/service code plus spec text.
- If a first implementation needs a registry queue for observability, keep it
  runtime-only and optional so rollback does not require data migration.

Explicit non-goals for this slice:

- cron expressions
- wall-clock “run at 09:00” semantics
- time zones
- missed-run replay or durable catch-up
- exactly-once delivery
- per-destination policy branches
- public scheduler CLI
- durable heartbeat persistence across restart
- broad singleton semantics for arbitrary named tasks

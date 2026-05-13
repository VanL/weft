# Manager Liveness And Leadership Robustness Plan

Status: draft
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.1], [MA-1.4], [MA-1.7], [MA-3]; docs/specifications/05-Message_Flow_and_State.md [MF-3], [MF-7]; docs/specifications/07-System_Invariants.md [MANAGER.6], [MANAGER.8], [MANAGER.9], [MANAGER.12], [MANAGER.13], [MANAGER.15], [MANAGER.16]; docs/specifications/10-CLI_Interface.md [CLI-1.1.2], manager-control surfaces
Superseded by: none

## 1. Goal

Make manager liveness, leadership yield, and leadership drain deterministic
under stale registry rows, bad external-supervisor identity, and backend load.
The system should prefer a short period of duplicate dispatch-capable managers,
protected by atomic spawn-request reservation, over a live process that has
yielded into draining and no longer drains public work. The implementation must
avoid `weft status`-style world scans on manager hot paths; it may inspect the
targeted manager registry queue, a specific manager control queue pair, and
specific spawn queues with bounded per-turn budgets.

## 2. Source Documents

Source specs:

- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md)
  [MA-1.1], [MA-1.4], [MA-1.7], [MA-3]: public spawn reservation authority,
  manager registry heartbeat and leadership view, manager PING/PONG fields,
  bootstrap, foreground serve, replacement, and manager drain.
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-3], [MF-7]: task-local control channel and manager bootstrap/runtime
  state messages.
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [MANAGER.6], [MANAGER.8], [MANAGER.9], [MANAGER.12], [MANAGER.13],
  [MANAGER.15], [MANAGER.16]: shared bootstrap, manager control semantics,
  lowest-live manager selection, public dispatch reservation authority,
  service convergence, and runtime-state cleanup.
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
  [CLI-1.1.2] and manager-control surfaces: foreground `manager serve`,
  operator `manager start|stop|list|status`, and explicit replacement behavior.

Related plans:

- [`2026-05-07-manager-selection-ping-pong-liveness-plan.md`](./2026-05-07-manager-selection-ping-pong-liveness-plan.md)
  completed the keyed manager PING/PONG liveness probe. This plan strengthens
  how that proof is used for leadership decisions.
- [`2026-05-09-runtime-liveness-probe-registry-plan.md`](./2026-05-09-runtime-liveness-probe-registry-plan.md)
  completed the process-local runtime liveness probe registry. This plan
  tightens `external-supervisor` semantics so missing or inconclusive probes
  remain `unknown`, not authority.
- [`2026-05-11-manager-work-stealing-dispatch-plan.md`](./2026-05-11-manager-work-stealing-dispatch-plan.md)
  is draft context for public spawn work-stealing. This plan preserves its core
  rule: atomic broker reservation owns public spawn exclusivity.
- [`2026-05-11-service-convergence-and-manager-registry-bounding-plan.md`](./2026-05-11-service-convergence-and-manager-registry-bounding-plan.md)
  is draft context for registry bounding. This plan does not require the full
  service-registry consolidation to land first.
- [`2026-05-13-manager-replace-start-serve-plan.md`](./2026-05-13-manager-replace-start-serve-plan.md)
  completed explicit operator replacement semantics. This plan assumes
  `superseded` rows and `--replace` exist on the target branch; if implementing
  on an older branch, land that plan first or explicitly adapt the tasks.

Guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/runtime-and-context-patterns.md`](../agent-context/runbooks/runtime-and-context-patterns.md)
- [`docs/agent-context/runbooks/testing-patterns.md`](../agent-context/runbooks/testing-patterns.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)

## 3. Context And Key Files

Files to modify:

- `weft/core/manager.py`
- `weft/core/manager_runtime.py`
- `weft/runtime_liveness.py`
- `weft/core/control_probe.py`
- `weft/core/tasks/base.py` only if manager PONG dispatch-eligibility fields are
  missing from the existing manager override.
- `weft/helpers/__init__.py` only if shared canonical manager or runtime-handle
  helpers need a narrow addition.
- `weft/_constants.py` for narrowly named timing or state constants.
- `weft/commands/manager.py`, `weft/commands/serve.py`, or
  `weft/commands/tasks.py` only for thin wrapper or diagnostic output changes.
- Tests:
  - `tests/core/test_manager.py`
  - `tests/core/test_control_probe.py`
  - `tests/commands/test_manager_commands.py`
  - `tests/commands/test_serve.py`
  - `tests/commands/test_task_commands.py` or existing task-ping coverage
  - `tests/cli/test_cli_serve.py`
- Docs:
  - `docs/specifications/03-Manager_Architecture.md`
  - `docs/specifications/05-Message_Flow_and_State.md`
  - `docs/specifications/07-System_Invariants.md`
  - `docs/specifications/10-CLI_Interface.md`
  - `docs/lessons.md`
  - `docs/plans/README.md`

Read first:

- `weft/core/manager.py`:
  `_register_manager()`, `_recent_lower_canonical_manager_exists()`,
  `_manager_record_liveness()`, `_read_active_manager_records()`,
  `_evaluate_dispatch_ownership()`, `_has_actionable_leadership_work()`,
  `_maybe_yield_leadership()`, `_begin_leadership_drain()`,
  `_continue_shutdown_drain()`, `_drain_public_spawn_requests()`,
  `_drain_internal_spawn_requests()`, and `process_once()`.
- `weft/core/manager_runtime.py`:
  `_snapshot_registry()`, `_select_active_manager_from_snapshot()`,
  `_manager_record_stale_status()`, `_manager_record_has_matched_pong()`,
  `_foreground_serve_blocking_manager()`, `_replace_active_manager()`,
  `_ensure_manager()`, `_start_manager()`, `_serve_manager_foreground()`, and
  `_stop_manager()`.
- `weft/core/control_probe.py`: `send_keyed_ping_probe()` and
  `coerce_pong_response()`.
- `weft/core/tasks/base.py`: `BaseTask._handle_control_command()` and
  `_control_response_extras()`.
- `weft/runtime_liveness.py`: registered-probe contract and `unknown` fallback.
- `weft/helpers/__init__.py`: `is_canonical_manager_record()`,
  `canonical_owner_tid()`, `handle_has_live_host_process()`, and scoped PID
  helpers.
- `tests/helpers/weft_harness.py`: real broker/process harness patterns.

Current structure:

- Public spawn dispatch is work-stealing: a manager owns a public spawn request
  only after an atomic broker reservation moves that message into its reserved
  queue.
- Manager leadership is advisory for new public dispatch and duplicate-manager
  convergence. It is still operationally important because a manager that
  yields enters draining and stops draining public work.
- Manager registry readers use `weft.state.services` service-owner rows, reduce
  records for the canonical manager service key, and choose the lowest live
  canonical manager TID.
- `external-supervisor` manager records use registered runtime liveness probes
  when available. Missing or inconclusive probes return `unknown`, and current
  callers still sometimes let fresh unknown evidence behave like live evidence.
- A matched keyed manager PONG is positive proof that the specific task TID
  read `ctrl_in`, processed `PING`, and wrote a matching response to `ctrl_out`.
  It is not proof that the recorded supervisor identity is correct.
- Leadership drain sets `_draining=True`, unregisters the manager as active, and
  short-circuits future `process_once()` turns into `_continue_shutdown_drain()`
  before public spawn draining and ordinary service convergence.

Comprehension questions before editing:

1. Which exact queue operation makes a public spawn request exclusive to one
   manager?
2. What is the difference between a runtime probe returning `unknown`, a missed
   manager PING, and positive stale proof?
3. Which state prevents `Manager.process_once()` from reaching public spawn
   draining and service convergence?
4. Why is a matched PONG from a draining manager liveness proof but not
   dispatch-authority proof?
5. Which helper owns manager registry reduction for command-side startup, and
   which helper owns the manager's own leadership decision?

## 4. Target Model

Separate four facts that the current code can blur under bad supervisor state:

1. **Identity**: the runtime instance a manager claims, such as host PID plus
   create time, systemd unit plus invocation ID, or Docker container ID.
2. **Liveness**: whether that exact runtime instance or task control surface is
   alive now.
3. **Dispatch eligibility**: whether the manager is allowed to drain new public
   spawn work for `weft.spawn.requests`.
4. **Leadership authority**: whether this candidate can make another manager
   yield.

Use this proof lattice:

```text
strong_live:
  - host-pid handle proves PID plus create-time identity
  - registered external-supervisor probe returns live for the exact handle
  - keyed manager PING returns a matched PONG whose manager-selection fields
    match the candidate row and the current context

stale:
  - host-pid handle proves PID/create-time mismatch or process absence
  - registered external-supervisor probe returns stale
  - latest owner row is stopped or superseded

unknown:
  - no registered external-supervisor probe
  - probe failure or inconclusive probe
  - malformed or incomplete runtime handle
  - keyed PING timeout or queue I/O error
```

Only `strong_live` plus dispatch eligibility can win leadership. `unknown` can
block destructive cleanup or produce degraded diagnostics, but it must not make
the current dispatch-capable manager yield. `stale` can be pruned or ignored by
exact message ID through the existing runtime-state cleanup rules.

Dispatch eligibility for a manager candidate requires all of these:

- same manager service key and broker context
- canonical public request queue is `weft.spawn.requests`
- `role="manager"` or canonical service-owner manager type
- status is active, not `draining`, `stopping`, `stopped`, `superseded`, or
  terminal
- matched PONG, when used as proof, reports manager-selection fields that agree
  with the candidate row: `tid`, `role`, `requests`, `ctrl_in`, `ctrl_out`, and
  `weft_context` when available

Leadership drain is allowed only while the replacement remains strongly live
and dispatch-eligible. A leadership-draining manager must not spin forever in a
state where no strongly live replacement exists.

Voluntary leadership transitions use two explicit paths:

- **No user children**: immediate yield remains allowed, but only after the
  lower-TID candidate is proved strong-live and dispatch-eligible in the same
  manager turn. This path marks the manager cancelled, unregisters it, and exits
  as today. It cannot create the observed wedge because there is no live
  draining manager left behind.
- **Non-persistent user children**: enter leadership drain. While draining, the
  manager revalidates the chosen leader at a bounded interval. If the leader
  becomes stale or unknown, the manager republishes active ownership, clears
  `_draining`, reports `manager_leadership_resumed`, updates the process title
  back to running, and resumes the ordinary manager loop. This makes
  `draining -> active` legal only for voluntary leadership drain recovery, not
  for STOP/KILL drain.

Operator intent is separate from voluntary yield. Explicit replacement or
foreground-serve stale-row cleanup may supersede an unreachable unknown
foreground-supervisor row because the operator is declaring ownership intent.
Unknown evidence must not drive an already running dispatch-capable manager to
yield voluntarily.

## 4.1 Current Ops Issue Coverage

This plan addresses these observed ops failures directly:

- A real supervised manager publishes Docker-flavored `external-supervisor`
  identity even though the intended control plane is host/systemd. The plan
  makes that identity insufficient for leadership authority unless a registered
  probe or validated manager PONG proves the exact candidate live and
  dispatch-eligible.
- A running manager enters `manager:draining` because stale or ambiguous lower
  manager evidence looks authoritative. The plan requires strong live dispatch
  proof before yield and revalidates that proof during leadership drain.
- A draining manager remains alive but stops draining public spawn requests.
  The plan bounds leadership drain and requires deterministic recovery or exit
  rather than indefinite non-serving process liveness.
- Operators cannot tell whether a manager is alive-but-not-serving without
  stitching together process title, registry rows, and queue backlog. The plan
  adds targeted diagnostics for "draining plus public backlog plus no
  dispatch-eligible replacement" without calling broad status collectors.

This plan only partially addresses these observed symptoms:

- High `weft.spawn.requests` backlog. The plan prevents the known false-yield
  path that strands public spawn work, but it does not increase spawn
  throughput, change worker concurrency, or diagnose slow child startup.
- High `reserved_total`. The plan preserves reserved-work authority and covers
  leadership transitions around manager reserved queues, but it does not
  redesign reserved-queue cleanup or task-level reserved policies.
- Many active child or Postgres `SELECT` processes. The plan keeps manager hot
  paths from adding full-world scans under load, but it does not optimize
  task-monitor queries, application SQL, or child workload behavior.
- Runtime-state cleanup reducing `state.services`, `tid_mappings`, or
  `log.tasks`. The plan relies on existing exact-delete cleanup and may add
  targeted pruning for stale manager rows, but broad retention and cleanup
  volume are owned by existing runtime-state pruning and TaskMonitor cleanup
  plans.

If ops still shows a running, non-draining, dispatch-eligible manager with
`weft.spawn.requests` staying high, stop and open a separate throughput or
reserved-queue investigation. Do not expand this plan into a broad `weft
status`/TaskMonitor/query-performance rewrite.

Well-bounded additions that would materially help the reported ops state:

- Add a targeted dispatch-progress watchdog inside the manager loop. It should
  track public spawn backlog and last successful public reservation using only
  `weft.spawn.requests`, the manager's own reserved queue, and in-memory loop
  counters. If backlog remains non-empty while the manager is not draining and
  no public reservations succeed for a bounded interval, emit an operational
  warning with ownership state, child count, reserved count for this manager,
  and the last public drain result. This helps distinguish "not dispatching" from
  "dispatching but children are slow."
- Add a targeted reserved-release audit for manager shutdown and leadership
  transitions. Before a manager enters or continues leadership drain, record
  whether its public/internal reserved queues contain messages and whether those
  messages are launchable, already side-effected, or must be released. This
  should inspect only this manager's reserved queues, not all `T*.reserved`
  queues. If STOP or leadership drain prevents launch before any child side
  effect, release the reservation back to its source queue, preserving the
  existing work-stealing invariant.
- Add a `weft manager probe <tid>` or equivalent command-layer helper only if
  existing `weft task ping <tid>` is too raw for operators. It should resolve a
  single manager TID, send one keyed PING, validate manager-selection fields,
  and print liveness class plus dispatch eligibility. It must not call
  `weft status`, list all tasks, or scan all mappings. This makes the ops
  question "is this manager alive and eligible to dispatch?" cheap and explicit.
- Add a foreground/systemd startup warning when the manager's published runtime
  handle is `external-supervisor` with `id` starting `docker:`. The warning
  should say that this is not strong host/systemd identity and will be treated
  as `unknown` unless a registered probe or PONG proves liveness. This directly
  addresses the current "manager list still reports docker identity" signal.
- Add one targeted Postgres-load regression test or benchmark only for the
  manager hot path introduced by this plan: leadership checks and drain
  revalidation must not scan `weft.log.tasks`, all TID mappings, or all task
  queues. Do not benchmark the whole status command in this slice.

## 5. Invariants And Constraints

Preserve these invariants:

- TID format and immutability do not change.
- TaskSpec `spec` and `io` immutability do not change.
- Task state transitions remain forward-only.
- Queue names and result payloads do not change.
- `weft.state.*` queues remain runtime-only evidence, not durable product
  state.
- Public spawn exclusivity remains atomic broker reservation, not manager
  registry election.
- A manager may launch public work it has already reserved, even if advisory
  leadership changes before launch.
- Missing PONG is absence of proof, not proof of death.
- Registered probe `unknown` is absence of proof, not proof of liveness.
- PING/PONG probes must be bounded and targeted to one candidate control queue
  pair. Do not probe every task, scan task history, or call `weft status`.
- Registry reads must use existing generator-based helpers and bounded pruning
  by exact message ID. Do not introduce fixed-limit peeks for correctness.
- Do not add a second manager startup path, second control channel, or durable
  side store.
- Do not add new dependencies.
- Do not make runtime probe failures fatal to ordinary task submission.
- Do not mock away broker reservation, task-local control queues, or manager
  process lifecycle in tests that claim to prove the core behavior.

Load constraints:

- Manager hot paths may read `weft.state.services` for the manager service key,
  inspect targeted candidate control queues, and check specific public/internal
  spawn queues with existing per-turn budgets.
- Manager hot paths must not call command-layer status collection, scan
  `weft.log.tasks`, scan all TID mappings, inspect all task-local queues, or
  run broad system diagnostics.
- Any new retry loop must have a named constant, a bounded attempt count or
  timeout, and a comment explaining why backend queue waits are insufficient.
- Manager hot paths must not replay the whole shared service registry every
  turn. A full registry replay is allowed at manager startup, explicit
  operator command boundaries, and forced recovery diagnostics. The steady-state
  manager loop must use an in-memory registry view updated from
  `iter_queue_json_entries(..., since_timestamp=last_seen_timestamp)` and prune
  exact stale message IDs from that view. If incremental replay fails, the
  leadership decision is `unknown` for that turn rather than falling back to a
  broad scan.
- Manager-owned PING fallback must have a named short timeout, one-probe-per-turn
  cap, and an in-memory probe cache keyed by candidate TID plus registry
  timestamp. A fresh cached `unknown` result must not trigger a repeated
  blocking probe until its small TTL expires or the candidate row changes.
- Operational logs may be added, but they are best-effort diagnostics and must
  not become lifecycle truth.

Rollback:

- This plan must be implementable as additive proof tightening. Existing
  registry rows remain compatible.
- If the leadership-proof changes regress startup, revert the proof helper and
  leadership-yield changes without queue migration.
- If bounded leadership-drain recovery regresses long-running child handling,
  revert only the recovery path while keeping stricter external-supervisor
  proof rules.

Rollout:

- Ship stricter proof semantics before requiring new supervisor identity
  injection. That turns bad identity into `unknown` rather than false
  leadership.
- Then update supervised deployments to inject explicit runtime identity, for
  example host PID plus create time or systemd unit plus invocation ID through
  `WEFT_MANAGER_RUNTIME_HANDLE_JSON`.
- Finally add supervisor-specific probes such as systemd. Do not block the core
  robustness fix on a systemd probe if PING/PONG can already prevent false
  yield.

## 6. Tasks

1. Bound manager registry reads on manager hot paths.

   Outcome: manager-owned leadership checks and drain revalidation do not replay
   the whole shared `weft.state.services` queue every turn under load.

   Files:

   - `weft/core/manager.py`
   - `weft/core/manager_runtime.py` only for shared projection helpers if needed
   - `weft/helpers/__init__.py` only if a narrow iterator helper is missing
   - `tests/core/test_manager.py`

   Required behavior:

   - Add or reuse a manager-local registry view that performs one initial
     generator replay, records `last_seen_timestamp`, and then updates from
     `iter_queue_json_entries(..., since_timestamp=last_seen_timestamp)`.
   - The view stores only records relevant to the manager service key and exact
     stale message IDs that should be pruned. It must not retain unrelated
     service-owner rows.
   - On incremental read failure, return ownership `unknown` for that turn and
     emit debug/operational diagnostics. Do not fall back to a full replay in
     `process_once()`.
   - Full replay remains acceptable for short-lived command invocations such as
     `weft manager list`, explicit startup/replacement preflight, and test
     setup. It is not acceptable in the manager's periodic leadership/drain
     path.

   Tests:

   - After initial manager registry replay, repeated leadership checks call the
     registry iterator with `since_timestamp` rather than `None`.
   - Unrelated service-owner rows are ignored and not retained in the manager
     view.
   - Incremental read failure produces `unknown` ownership and does not trigger
     a full replay.
   - Stale rows are pruned by exact message ID without scanning task logs or TID
     mappings.

   Stop and re-evaluate if:

   - the implementation wants a new durable index queue for manager ownership
   - the manager hot path still calls `_read_active_manager_records()` in a way
     that replays all service rows every turn

2. Introduce a shared manager liveness decision model.

   Outcome: command-side startup and manager-owned leadership use the same
   classification vocabulary: `strong_live`, `stale`, `unknown`, plus a
   dispatch-eligibility decision.

   Files:

   - `weft/core/manager_runtime.py`
   - `weft/core/manager.py`
   - optionally a small helper module if keeping the logic in one existing file
     would duplicate code across command and manager paths
   - `tests/core/test_manager.py`
   - `tests/commands/test_manager_commands.py`

   Required behavior:

   - Host PID authority remains strong only when `handle_has_live_host_process()`
     succeeds, including create-time identity when available.
   - `external-supervisor` calls `runtime_liveness_from_registered_probe()`.
     `live` becomes strong liveness, `stale` becomes stale, and `unknown`
     remains unknown.
   - Unknown external-supervisor evidence may trigger one targeted keyed manager
     PING when the candidate is otherwise canonical. A matched PONG becomes
     strong liveness only after manager-selection fields validate against the
     candidate row and context.
   - Implement separate predicates for liveness proof and dispatch eligibility.
     PONG liveness rescue must not directly imply leadership authority.
   - Dispatch eligibility from PONG must require `task_status` not in
     `draining`, `stopping`, `cancelled`, `completed`, `failed`, `timeout`, or
     `killed`; `should_stop` must not be true; `role`, `requests`, `ctrl_in`,
     `ctrl_out`, and `weft_context` when present must match the candidate row.
   - A matched PONG from a draining or stopping manager proves liveness but
     fails dispatch eligibility.
   - Missing PONG, malformed PONG, non-matching `request_id`, wrong TID, wrong
     queue fields, or queue I/O error stays unknown.
   - Manager-owned PING fallback uses a named timeout constant, one probe per
     leadership turn, and a small in-memory cache. Command-side startup may keep
     its existing startup grace behavior.

   Tests:

   - Active lower-TID external-supervisor row with registered `unknown` and no
     matched PONG does not make a running manager yield.
   - Active lower-TID external-supervisor row with registered `live` and active
     dispatch fields can make a newer idle manager yield.
   - Active lower-TID external-supervisor row with matched manager PONG and
     active dispatch fields can make a newer idle manager yield.
   - Matched PONG reporting draining/stopping does not create leadership
     authority.
   - Registered `stale` prunes or ignores the candidate and does not send a
     PING rescue.
   - Repeated leadership checks against the same unknown row do not send a PING
     every turn while the cache TTL is fresh.

   Stop and re-evaluate if:

   - command-side and manager-side code start using separate liveness
     interpretations
   - the implementation wants to call status/task-list code
   - the helper needs to scan all task logs or all TID mappings

3. Tighten leadership yield to require strong live dispatch authority.

   Outcome: `_maybe_yield_leadership()` starts leadership drain only for a
   lower-TID manager that is strongly live and dispatch-eligible.

   Files:

   - `weft/core/manager.py`
   - `tests/core/test_manager.py`

   Required behavior:

   - `_recent_lower_canonical_manager_exists()` and `_leader_tid()` must not
     report a candidate as actionable leader from fresh unknown
     external-supervisor evidence alone.
   - `_evaluate_dispatch_ownership()` should distinguish `other` from
     `unknown`: `other` means a lower dispatch-eligible leader is positively
     proved; `unknown` means the registry could not prove the leader.
   - `_has_actionable_leadership_work()` continues to protect persistent
     children, internal spawn, manager reserved queues, and control messages.
   - Public backlog pending in `weft.spawn.requests` still does not by itself
     block yield; the difference is the candidate leader must now be proved.
   - Immediate no-child yield remains allowed only after same-turn strong-live
     dispatch-authority proof. Unknown evidence must leave the manager active.
   - Non-persistent-child yield always enters leadership drain, never immediate
     cancellation.

   Tests:

   - A manager with no children keeps serving when the only lower row is
     unknown external-supervisor evidence.
   - A manager with no children yields when the lower row is PONG-proved and
     dispatch-eligible.
   - A manager with already reserved public work does not yield before launching
     or releasing that work.
   - The leadership check does not call any status/read-model collector. Prefer
     a focused monkeypatch around command-layer status helpers if needed to
     catch accidental imports.

   Stop and re-evaluate if:

   - leadership logic starts treating unknown as live to preserve old startup
     behavior
   - a new global lock or new registry queue is proposed for this slice

4. Bound and revalidate leadership drain.

   Outcome: a manager in leadership drain cannot remain alive indefinitely as a
   non-serving control plane when no strongly live replacement exists.

   Files:

   - `weft/core/manager.py`
   - `weft/_constants.py`
   - `tests/core/test_manager.py`

   Required behavior:

   - Add a leadership-drain revalidation path that runs inside
     `_continue_shutdown_drain()` at a bounded interval. It may inspect only the
     manager registry and the claimed leader's control queue pair.
   - If the replacement remains strong-live and dispatch-eligible, the draining
     manager continues waiting for non-persistent user children to finish
     naturally.
   - If the replacement becomes stale or unknown, recover by republishing active
     manager ownership, clearing `_draining`, resetting leadership-drain local
     state, reporting `manager_leadership_resumed`, updating the process title
     to running, and returning to the ordinary manager loop.
   - `manager_leadership_resumed` is a non-terminal lifecycle event. It does not
     mutate a terminal manager TaskSpec state, and it is legal only after
     `manager_leadership_yielded` from voluntary leadership drain.
   - If children block clean recovery, emit a clear operational-log warning and
     continue the resume path rather than waiting for children to exit. The
     manager already owns those children and is becoming active again. Do not
     kill user children in leadership-yield drain unless an explicit STOP or
     KILL path is active.
   - Ordinary STOP drain keeps its existing child STOP/terminate timeout
     behavior. Do not weaken operator stop semantics.

   Tests:

   - Leadership drain finishes when tracked non-persistent children exit.
   - Leadership drain revalidates the leader at the configured interval.
   - If the leader row becomes stale or unknown before children exit, the
     manager emits `manager_leadership_resumed`, republishes active ownership,
     clears `_draining`, and resumes public dispatch.
   - Immediate no-child yield is covered by leadership-yield tests and does not
     enter `_continue_shutdown_drain()`.
   - STOP drain still signals and terminates children after the existing drain
     timeout.
   - The drain loop does not busy-spin when child cleanup makes no progress.

   Stop and re-evaluate if:

   - the recovery behavior would require reassigning child ownership through a
     new persistence model
   - the implementation starts scanning task history to decide child state
   - the plan's "do not kill user children on leadership yield" constraint
     becomes hard to preserve

5. Add supervisor identity validation for foreground serve.

   Outcome: supervised managers publish explicit, trustworthy runtime identity
   when the supervisor provides it, and bad auto-detected identity degrades to
   unknown rather than false leadership.

   Files:

   - `weft/core/manager.py`
   - `weft/core/manager_runtime.py`
   - `weft/_constants.py`
   - `docs/specifications/03-Manager_Architecture.md`
   - `docs/specifications/10-CLI_Interface.md`
   - tests under `tests/commands/` or `tests/cli/`

   Required behavior:

   - Keep `WEFT_MANAGER_RUNTIME_HANDLE_JSON` as the explicit supervisor
     identity input.
   - Validate JSON shape strictly enough that malformed input fails startup
     instead of publishing misleading active identity.
   - For foreground `manager serve`, emit a diagnostic when no explicit handle
     is supplied and auto-detection chooses `external-supervisor`. This is a
     warning in the first slice, not a hard failure, to preserve compatibility.
   - Document that production supervisors should inject explicit identity.
   - Do not teach core Weft systemd, Docker, launchd, or Kubernetes APIs in this
     task. Runtime-specific probing remains behind `runtime_liveness.py`.

   Tests:

   - Malformed explicit runtime handle raises the existing startup/config error.
   - Foreground serve with no explicit handle and auto-detected external
     supervisor records an operational warning when operational logs are on.
   - Explicit host-pid handle remains host-pid authority and participates in
     strong liveness.

   Stop and re-evaluate if:

   - the code starts importing supervisor-specific libraries in core
   - compatibility pressure suggests making missing explicit identity fatal in
     this same slice

6. Add targeted operator diagnostics without status-world scans.

   Outcome: operators can see the bad states this plan is designed to prevent
   without manager hot paths calling status collectors.

   Files:

   - `weft/core/manager.py`
   - `weft/core/serve_log.py`
   - `weft/commands/manager.py` or `weft/commands/serve.py` only for thin
     command output if needed
   - tests for serve-log records or command diagnostics

   Required behavior:

   - Add operational-log fields for leadership decision inputs:
     candidate TID, liveness class, dispatch eligibility, proof source, and
     reason when rejected.
   - Add a targeted manager-health helper only if needed. It may read:
     `weft.state.services`, selected manager `ctrl_in/ctrl_out`, and
     `weft.spawn.requests` pending/oldest targeted information if SimpleBroker
     exposes that cheaply. It must not call `weft status`, collect all tasks,
     replay `weft.log.tasks`, or scan all TID mappings.
   - Detect and log this specific unhealthy shape:
     "this process is draining, public spawn backlog exists or grows, and no
     strong-live dispatch-eligible replacement is visible."

   Tests:

   - Operational-log record is emitted for rejected unknown leader evidence when
     logging is enabled.
   - Health helper, if added, uses only the targeted queue helpers. Prefer tests
     that patch status collectors to fail if accidentally called.

   Stop and re-evaluate if:

   - diagnostics become part of lifecycle truth
   - the implementation wants to reuse `weft status` internals for convenience

7. Plan systemd liveness as a separate follow-up if deployment needs it.

   Outcome: this plan does not block on systemd-specific probing, but it records
   the required boundary for a later executable plan.

   Required follow-up plan shape:

   - Choose a concrete module boundary before implementation, such as a
     `weft_systemd` extension or another explicitly approved optional package.
   - Positive `live` must require matching unit plus exact invocation or start
     identity when available. `MainPID` alone is weak and must not be strong
     proof without process identity or invocation identity.
   - `stale` must require positive proof that the exact invocation is gone.
   - command failure, missing `systemctl`, permission error, ambiguous unit
     state, or missing invocation identity returns `unknown`.
   - The probe remains optional. Core robustness cannot depend on systemd being
     installed in tests or runtime.

   Stop and re-evaluate if:

   - implementation work starts before a module boundary is chosen
   - the probe can only prove unit-level liveness and cannot distinguish old
     invocations from the current manager instance

8. Update specs, lessons, and docs after behavior lands.

   Outcome: specs remain the authoritative source of truth.

   Files:

   - `docs/specifications/03-Manager_Architecture.md`
   - `docs/specifications/05-Message_Flow_and_State.md`
   - `docs/specifications/07-System_Invariants.md`
   - `docs/specifications/10-CLI_Interface.md`
   - `docs/lessons.md`
   - this plan, changing `Status: draft` to `completed` only when implemented

   Required updates:

   - [MA-1.4] should state that unknown external-supervisor evidence is not
     leadership authority.
   - [MA-1.7] and [MF-3] should state which manager PONG fields are required for
     dispatch-authority proof.
   - [MA-3] should state leadership-drain revalidation and the
     `manager_leadership_resumed` recovery behavior.
   - [MANAGER.8] and [MANAGER.9] should distinguish liveness proof from
     dispatch eligibility and leadership authority.
   - `docs/lessons.md` should record the operational lesson: external
     supervisors require delegated proof; they do not disable liveness checks.

   Stop and re-evaluate if:

   - specs and implementation disagree on the `draining -> active` recovery
     event or when that transition is legal
   - a doc update tries to make plans normative instead of updating specs

## 7. Verification

Run focused checks first:

```bash
./.venv/bin/python -m pytest tests/core/test_control_probe.py -q
./.venv/bin/python -m pytest tests/core/test_manager.py -q
./.venv/bin/python -m pytest tests/commands/test_manager_commands.py tests/commands/test_serve.py -q
```

Then run broader manager and CLI checks:

```bash
./.venv/bin/python -m pytest tests/cli/test_cli_manager.py tests/cli/test_cli_serve.py -q
./.venv/bin/python -m pytest tests/commands/test_task_commands.py -q
```

Before completion, run the normal repo gates:

```bash
./.venv/bin/python -m pytest
./.venv/bin/python -m mypy weft extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/python -m ruff check weft
```

Observable runtime proof:

- Start a foreground manager with operational logs enabled.
- Inject a lower-TID manager registry row with `external-supervisor` identity and
  no registered probe.
- Verify the running manager logs `unknown` evidence and does not enter
  leadership drain.
- Add a matched manager PONG or registered `live` probe for that lower row and
  verify the newer idle manager yields.
- Force the selected leader proof to become stale or unknown during leadership
  drain and verify the configured recovery behavior happens within the bounded
  interval.

Plan metadata proof:

```bash
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
```

## 8. Fresh-Eyes Review Requirement

This plan is not implementation-ready until a different available reviewer has
read the plan and the relevant code paths. Use the review-loop runbook prompt:

```text
Read docs/plans/2026-05-13-manager-liveness-and-leadership-robustness-plan.md.
Carefully examine the plan and the associated manager liveness, PING/PONG,
registry, and drain code. Look for errors, bad ideas, missing load boundaries,
and latent ambiguities. Do not implement anything. Could you implement this
confidently and correctly if asked?
```

Reviewer output should lead with findings and should explicitly answer whether
the plan is implementable after the findings are addressed. Accepted findings
must be folded back into this plan before work begins. Disagreements must be
recorded here with the reason the current path remains better.

Fresh-eyes pass status: completed with limitations.

Reviewer availability:

- Claude via `agent-mcp`: attempted first because it is a different agent
  family; the job exceeded the tool timeout and returned no usable findings.
- Gemini via `agent-mcp`: unavailable because the workspace was not trusted in
  that CLI environment.
- Qwen via `agent-mcp`: unavailable because it required interactive OAuth.
- Codex via `agent-mcp`: unavailable because the local provider binary was
  missing.
- In-app subagent fresh-eyes review: completed and used as the review source.

Accepted findings and plan responses:

- The draft left `draining -> active` versus exit undecided. The plan now
  chooses `manager_leadership_resumed`: voluntary leadership drain republishes
  active ownership and resumes serving when replacement proof becomes stale or
  unknown.
- The draft missed the no-child yield branch. The plan now states that
  no-child yield may remain immediate only after same-turn strong-live
  dispatch-authority proof.
- The draft did not satisfy the hot-path scan constraint. The plan now requires
  a manager-local service-registry view with initial replay plus incremental
  `since_timestamp` reads on steady-state manager turns.
- The draft allowed repeated blocking PING fallback. The plan now requires a
  named short timeout, one-probe-per-turn cap, and candidate-row probe cache.
- The draft blurred PONG liveness with dispatch eligibility. The plan now
  requires separate predicates and explicit `task_status`, `should_stop`,
  queue, role, and context validation before PONG can grant dispatch authority.
- The draft was ambiguous about operator cleanup versus voluntary yield. The
  plan now separates explicit operator replacement/foreground cleanup authority
  from voluntary manager yield.
- The draft used a bad test path. Verification now uses
  `tests/commands/test_task_commands.py`.
- The draft treated systemd probing as an executable task without a module
  boundary. Systemd probing is now a separate follow-up planning item.

Reviewer verdict before fixes: a zero-context engineer could not implement the
draft confidently. After these fixes, the remaining implementation risk is in
the manager-local registry cache and leadership-drain resume semantics; those
are now explicit tasks with stop-and-re-evaluate gates.

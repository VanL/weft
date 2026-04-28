# Shared Canonical Owner Fence Plan

Status: completed
Source specs: see Source Documents below
Superseded by: none

Post-landing review note (2026-04-17):

- Follow-on review did find four manager-owned correctness gaps plus a
  recovery-ordering test gap after the first fence landing.
- Those gaps were then fixed in the heartbeat implementation slice tracked in
  [`docs/plans/2026-04-17-heartbeat-service-plan.md`](./2026-04-17-heartbeat-service-plan.md)
  Task 1:
  - managers no longer clear `none` / `unknown` suspension on ownership regain
    without first recovering the original exact fenced request
  - stranded fenced requests no longer become orphaned through immediate
    leadership yield
  - dispatch-suspended managers no longer idle-shutdown while fenced recovery
    is pending
  - dispatch-suspended managers no longer tick autostart and self-enqueue
    repeated ensure-mode work
  - tests now prove the original fenced request is recovered before later
    inbox work resumes
- This plan remains `completed` because the shared helper and manager fence
  slice landed here, and the follow-on hardening is now also closed.

## 1. Goal

Add one narrow shared helper for convergent runtime ownership checks, then use
it to harden manager child dispatch. The helper should answer the shared
question both managers and named runtime endpoints already ask today: among the
currently live eligible claimants, which TID is canonical? The first behavior
change in this slice is manager-only: a manager that loses canonical ownership
after reserving a spawn request must re-check ownership before launching a
child and avoid intentional late duplicate dispatch. This plan does **not** add
public singleton semantics for named tasks, timer scheduling, or a new control
plane.

Definition of done for the implementation slice:

- one shared canonical-owner reduction helper exists and is used by more than
  one runtime ownership surface
- manager-side ownership evaluation keeps explicit `self` / `other` / `none` /
  `unknown` outcomes, or an equivalent equally precise split
- endpoint canonical resolution keeps its current public contract
- manager dispatch re-checks canonical ownership immediately before child launch
- a losing manager either exact-message requeues the reserved spawn request to
  `weft.spawn.requests` or leaves it visibly stranded in reserved with an
  explicit event; it never silently drops the work item
- losing-owner fencing does not reuse `task_spawn_rejected` or any other event
  already interpreted as durable spawn rejection
- fence diagnostics use exact manager-scoped event names and required fields so
  tests, reconciliation, and docs all describe the same contract
- current specs, tests, and nearby implementation notes describe the landed
  behavior without promising strict singleton semantics

## 2. Source Documents

Source specs:

- [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md) [CC-2.2], [CC-2.4.1], [CC-2.5]
- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md) [MA-1.1], [MA-1.4], [MA-3], [MA-4]
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md) [MF-3.1], [MF-6], [MF-7]
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md) [QUEUE.4], [QUEUE.5], [QUEUE.6], [OBS.1], [OBS.2], [OBS.3], [MANAGER.1], [MANAGER.4], [MANAGER.8]

Related current plans:

- [`docs/plans/2026-04-16-runtime-endpoint-registry-boundary-plan.md`](./2026-04-16-runtime-endpoint-registry-boundary-plan.md) — completed endpoint-registry slice. Reuse its landed lowest-live-TID endpoint rule; do not redesign endpoint semantics here.
- [`docs/plans/2026-04-09-manager-lifecycle-command-consolidation-plan.md`](./2026-04-09-manager-lifecycle-command-consolidation-plan.md) — broader manager control-plane cleanup. This plan only hardens the late dispatch race and shared ownership seam.
- [`docs/plans/2026-04-14-spawn-request-reconciliation-plan.md`](./2026-04-14-spawn-request-reconciliation-plan.md) — current submission-reconciliation contract. This plan may add a runtime-owned pre-launch requeue path, but it must not blur CLI/operator manual-recovery rules or `task_spawn_rejected` semantics.

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

No current product spec exists for:

- a generic shared canonical-owner helper reusable across multiple runtime
  registries

Treat the helper extraction itself as implementation refactor and runtime
hardening. The spec-owned behavior changes in this slice are narrower:
manager-side pre-dispatch ownership fencing and continued reuse of the already
shipped endpoint canonical-owner rule.

## 3. Context and Key Files

### Files To Modify

Implementation:

- `weft/helpers.py`
- `weft/core/endpoints.py`
- `weft/core/manager.py`
- `weft/commands/_spawn_submission.py`
- `tests/commands/test_queue.py`
- `tests/commands/test_run.py`
- `tests/core/test_manager.py`

Docs and traceability:

- `docs/specifications/01-Core_Components.md`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/lessons.md` if the manager fence exposes a reusable ownership rule worth
  memorializing

### Read First

- `weft/helpers.py`
  - owns `pid_is_live()`, `iter_queue_json_entries()`, and
    `is_canonical_manager_record()`
- `weft/core/endpoints.py`
  - already resolves duplicate live endpoint claimants by sorting on TID and
    preserving `live_candidates`
- `weft/core/manager.py`
  - owns manager-registry replay, leadership yield, spawn-request reservation,
    and child launch
- `weft/commands/_spawn_submission.py`
  - owns durable classification of `spawned` / `rejected` / `queued` /
    `reserved` / `unknown` submission outcomes and already treats
    `task_spawn_rejected` as a real rejection signal
- `tests/commands/test_queue.py`
  - already proves public endpoint canonical-owner behavior
- `tests/commands/test_run.py`
  - already proves reserved-versus-rejected submission reconciliation behavior
- `tests/core/test_manager.py`
  - already proves leadership yield and reserved-work handling under stop/drain

### Style And Guidance

- `AGENTS.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/testing-patterns.md`

### Shared Paths And Helpers To Reuse

- `weft.helpers.iter_queue_json_entries()`
- `weft.helpers.pid_is_live()`
- `weft.helpers.is_canonical_manager_record()`
- `weft/core/endpoints.py::list_resolved_endpoints()`
- `weft/core/manager.py::_active_manager_records()`
- `weft/core/manager.py::_maybe_yield_leadership()`
- `weft/commands/_spawn_submission.py::_inspect_task_log_for_tid()`
- `weft/core/tasks/base.py::_get_reserved_queue()`

Do not duplicate:

- append-only queue replay using fixed `peek_many(limit=...)`
- manager child-launch flow outside `Manager._handle_work_message()`
- endpoint canonical-owner rules in a second endpoint-specific sorter

### Current Structure

- `Manager._active_manager_records()` replays `weft.state.managers`, reduces to
  the latest active canonical spawn-queue manager record per TID, and filters
  dead PIDs.
- `weft.helpers.iter_queue_json_entries()` currently treats queue-generator
  open failure as an empty history read. A shared canonical-owner helper built
  on already-filtered claimants therefore cannot express `ownership unknown` by
  itself; that distinction must stay in the manager-owned registry reader or
  wrapper logic.
- `Manager._leader_tid()` currently returns `min(active, key=int)`.
- `Manager._maybe_yield_leadership()` is called from the main loop and may
  start drain or immediate yield, but it is not the final gate right before
  `_launch_child_task()`.
- `Manager._maybe_yield_leadership()` currently couples dispatch eligibility to
  child supervision. When persistent children exist, a superseded manager does
  not yield, which means current code has no way to keep supervising an
  already-launched persistent child while also refusing new dispatch.
- `Manager._handle_work_message()` currently checks control/drain state before
  and after `_build_child_spec()`, then launches the child if allowed. It does
  not perform a final ownership fence against a newly visible lower-TID leader.
- `_inspect_task_log_for_tid()` in `weft/commands/_spawn_submission.py` treats
  `task_spawn_rejected` as durable submission rejection and ignores unrelated
  manager-scoped diagnostic events. Losing-owner fencing therefore cannot reuse
  `task_spawn_rejected` without breaking `weft run` reconciliation.
- `list_resolved_endpoints()` already groups live endpoint claimants by name,
  sorts candidates by `int(tid)`, returns the first record as canonical, and
  exposes the duplicate count via `live_candidates`.
- Named endpoints are discovery-only. The current contract explicitly allows
  duplicate live claimants and treats them as observable conflicts rather than
  enforcing exclusivity.

### Comprehension Checks

1. After a manager reserves a spawn request from `weft.spawn.requests`, which
   queue owns that message, and why is a bare `return` from
   `_handle_work_message()` the wrong losing-owner behavior?
2. Which current public endpoint behaviors must stay unchanged if a shared
   helper replaces endpoint-specific TID sorting?
3. Where is the current late race window between leadership reconciliation and
   child launch, and why would a startup-only leadership check be too weak for
   timer-style side effects later?
4. Why would reusing `task_spawn_rejected` for a losing-owner fence create a
   false `weft run` failure even when the exact spawn request was requeued or
   is still recoverable from reserved?

## 4. Invariants and Constraints

- Keep one durable execution spine:
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log`.
- Preserve queue-first submission. A manager that reserved a spawn request but
  decides not to dispatch it must keep that exact work item durable and
  inspectable.
- Preserve TID format, TID immutability, and forward-only task state
  transitions.
- Preserve reserved-queue policy and exact-message handling. Do not replace
  exact-message recovery with broad queue rewrites.
- Preserve current canonical manager definition:
  `role == "manager"` plus `requests == weft.spawn.requests`.
- Preserve current endpoint contract:
  duplicate live claims remain observable conflicts and the lowest live TID
  remains canonical.
- Keep the shared canonical-owner helper pure. It should reduce already-live
  eligible claimants to one canonical claimant or no claimant; it must not own
  queue reads, PID liveness, registry decoding, or the meaning of
  `ownership unknown`.
- Keep all `weft.state.*` queues runtime-only. The helper must not introduce a
  durable singleton table or second hidden state store.
- No new queue names, result payloads, CLI flags, TaskSpec fields, or
  dependencies.
- No new execution path. The manager still reserves from
  `weft.spawn.requests`, builds a child spec, launches through the normal task
  path, and acknowledges the reserved message on success.
- Keep “lower owner proved” separate from “ownership unknown.” Do not collapse
  those into one boolean. Manager dispatch needs different losing-owner versus
  error-path behavior.
- Manager-side final ownership evaluation must keep at least four outcomes
  distinct:
  - `self`: this manager is still the canonical dispatch owner
  - `other`: a different lower-TID canonical manager is positively proved
  - `none`: the registry read succeeded, but no canonical claimant is
    positively visible and this manager's own live canonical record is not
    positively visible either; this is not launch authority
  - `unknown`: registry/runtime state could not be read confidently enough to
    choose between `self` / `other` / `none`
- For this slice, only `self` preserves the current launch path.
- `other` triggers the losing-owner fence.
- `none` and `unknown` both trigger dispatch suspension. They are distinct
  diagnostic states, but neither is permission to launch or requeue.
- Dispatch eligibility and child supervision may diverge after lower-owner
  proof. A superseded manager must stop dispatching new children immediately,
  even if it still keeps already-launched persistent children alive until they
  exit.
- When the final fence resolves to `none` or `unknown`, the manager must enter a
  manager-wide dispatch-suspended mode. While suspended it may continue control
  handling, child supervision, and periodic ownership checks, but it must not
  reserve or launch later spawn requests until a subsequent ownership check
  resolves to `self` or `other`.
- Losing-owner fencing is not spawn rejection. Do not emit
  `task_spawn_rejected` or any other event currently interpreted by submission
  reconciliation as durable rejection for this path.
- Pre-launch exact-message requeue by the reserving manager is allowed only on
  positive lower-owner proof before child launch. This does not relax the
  CLI/operator rule that already-stranded reserved spawn requests remain manual
  recovery cases.
- Sequence matters. Make the superseded manager non-dispatching before moving
  the exact reserved message back to `weft.spawn.requests`, otherwise the same
  manager can reclaim its own fenced request on the next poll.
- Fatal versus best-effort:
  - positive proof that a lower-TID canonical owner exists is fatal to the
    current dispatch attempt
  - inability to compute ownership from runtime state is not permission to
    launch on guesswork, lose the reserved message, or mark the manager
    superseded on guesswork
  - auxiliary log or cleanup failures must not downgrade a successful requeue or
    successful launch
- Rollback compatibility:
  - helper extraction and endpoint refactor must remain independently revertible
    from the manager fence
  - manager fence rollback must not require a queue or payload migration

Review gates:

- no new control plane
- no public singleton promise for named tasks
- no broad registry framework abstraction
- no mock-heavy substitute for real queue-backed manager proofs
- no drive-by refactor of `Manager`, `BaseTask`, or endpoint registry code

Out of scope:

- timer or periodic scheduling itself
- public “singleton named task” semantics
- strict lock or exactly-once ownership guarantees
- endpoint registry schema changes
- manager bootstrap redesign

## 5. Tasks

1. Extract the narrow shared canonical-owner helper and prove endpoint parity.
   - Outcome:
     one helper path determines the lowest-live-TID canonical owner from
     already-filtered eligible claimants, and endpoint resolution uses it
     without changing the public endpoint contract.
   - Files to touch:
     - `weft/helpers.py`
     - `weft/core/endpoints.py`
     - `tests/commands/test_queue.py`
   - Read first:
     - `weft/helpers.py`
     - `weft/core/endpoints.py`
     - `docs/specifications/05-Message_Flow_and_State.md` [MF-3.1]
   - Reuse the existing helper seam in `weft/helpers.py`; do not create a new
     registry subsystem unless circular-import pressure makes that impossible.
   - Keep the share point narrow. The helper should answer “who is canonical
     among these already-live claimants?” It should not own queue access,
     liveness checks, or registry-specific payload schemas.
   - Keep `ownership unknown` out of the shared helper. If manager dispatch
     needs a tri-state or four-state ownership result, compute that in
     `weft/core/manager.py` around the existing registry replay path.
   - Preserve `live_candidates` reporting and duplicate-name visibility in the
     endpoint CLI/command surface.
   - Tests to add or update:
     - keep `test_list_command_endpoints_uses_lowest_live_tid_as_canonical`
       green under the shared helper
     - add one targeted regression if the helper introduces a separate
       predicate or return shape that needs contract coverage
   - Stop-and-re-evaluate gate:
     if helper extraction starts pulling queue replay, PID liveness, or endpoint
     record decoding into a generic framework, stop and keep the helper smaller.
   - Done signal:
     endpoint behavior is unchanged and the duplicate-owner test still proves
     the current lowest-live-TID rule through the public command surface.

2. Harden manager child dispatch with a canonical-owner fence and exact-message
   losing-owner handling.
   - Outcome:
     a manager re-checks canonical ownership immediately before child launch,
     uses an explicit ownership result (`self` / `other` / `none` / `unknown`
     or equivalent), and avoids intentionally spawning new child work after a
     lower-TID canonical manager is already visible.
   - Files to touch:
     - `weft/core/manager.py`
     - `weft/commands/_spawn_submission.py`
     - `tests/core/test_manager.py`
     - `tests/commands/test_run.py`
   - Read first:
     - `weft/core/manager.py` `_active_manager_records()`, `_leader_tid()`,
       `_maybe_yield_leadership()`, `_control_allows_child_launch()`,
       `_handle_work_message()`
     - `weft/commands/_spawn_submission.py` `_inspect_task_log_for_tid()`,
       `_reconcile_submitted_spawn_once()`
     - `docs/specifications/03-Manager_Architecture.md` [MA-1.1], [MA-1.4], [MA-3]
     - `docs/specifications/05-Message_Flow_and_State.md` [MF-6]
   - Reuse the shared canonical-owner helper for the final ownership fence. Do
     not add a second manager-specific leader-election algorithm.
   - Preserve the current normal path only when the final fence resolves to
     `self`. The fence is a late guard, not a rewrite of manager bootstrap or
     main-loop leadership reconciliation.
   - Treat `none` as non-launchable. It means the runtime cannot currently
     prove any canonical dispatch owner, including this manager. It is an
     inspectable suspension state, not permission to continue.
   - Separate “cannot dispatch new work” from “must fully drain now.” One
     acceptable shape is a dedicated dispatch-suspension flag. Another is a
     narrow extension of the existing leadership-yield helpers. Either way, do
     not leave a superseded manager with persistent children dispatch-eligible
     just because `_maybe_yield_leadership()` currently protects supervision and
     dispatch together.
   - On positive proof that another canonical manager owns dispatch:
     - make the current manager non-dispatching for future loop iterations
       before attempting the exact-message move
     - do not launch the child
     - attempt exact-message move of the reserved spawn request back to
       `weft.spawn.requests`
     - if the move succeeds, emit exactly
       `manager_spawn_fenced_requeued` with required fields `child_tid`,
       `leader_tid`, `reserved_queue`, and `message_id`; do not emit
       `task_spawn_rejected`
     - if the move fails, leave the exact message visibly in reserved and emit
       exactly `manager_spawn_fenced_stranded` with required fields
       `child_tid`, `leader_tid`, `reserved_queue`, and `message_id`
     - keep already-launched persistent children alive under local supervision,
       but do not launch any new children after the fence trips
   - Keep “ownership unknown” distinct from “lower owner proved.” Transient
     helper failure must not silently drop the reserved message or permanently
     drain the manager on guesswork.
   - On `none` or `unknown` during the final fence:
     - do not launch on guesswork
     - do not requeue on guesswork
     - leave the exact message visibly in reserved
     - emit exactly `manager_spawn_fence_suspended` with required fields
       `child_tid`, `reserved_queue`, `message_id`, and `ownership_state`
       where `ownership_state` is exactly `none` or `unknown`
     - put the manager into manager-wide dispatch-suspended mode
     - while suspended, do not reserve or launch later spawn requests
     - keep the manager otherwise running; this path alone does not prove
       supersession
     - clear dispatch suspension only after a later ownership check resolves to
       `self`; if a later check resolves to `other`, converge into the
       losing-owner path instead
   - Keep submission reconciliation aligned with the new event contract.
     `weft/commands/_spawn_submission.py` should continue treating only
     `task_spawned` and `task_spawn_rejected` as durable child-dispatch
     outcomes unless the spec changes.
   - Tests to add or update:
     - losing leadership between child-spec build and launch requeues the exact
       spawn request and emits neither `task_spawned` nor
       `task_spawn_rejected`
     - forced exact-message move failure leaves the request in reserved and
       emits `manager_spawn_fenced_stranded` with the required fields
     - ownership-`none` final fence leaves the request in reserved, emits
       `manager_spawn_fence_suspended` with `ownership_state == "none"`, and
       does not mark the manager superseded
     - ownership-`unknown` final fence leaves the request in reserved, emits
       `manager_spawn_fence_suspended` with `ownership_state == "unknown"`,
       and does not mark the manager superseded
     - once suspended by `none` or `unknown`, the manager does not reserve
       later spawn requests until a later ownership check resolves to `self`
     - with an already-running persistent child, lower-owner proof suspends new
       dispatch while allowing the persistent child to remain supervised
     - submission reconciliation ignores the new manager-scoped fence
       diagnostics (`manager_spawn_fenced_requeued`,
       `manager_spawn_fenced_stranded`, `manager_spawn_fence_suspended`) and
       still reports `queued` or `reserved`, not `rejected`
   - Stop-and-re-evaluate gate:
     if this task starts changing queue names, spawn payload shapes, or manager
     bootstrap rules, or if making `ownership unknown` non-stranding would
     require a new retry framework, reserved-queue scavenger, or second
     dispatch path, stop and write a broader plan instead of smuggling that
     work into this slice.
   - Done signal:
     the new regression proves no late child spawn after lower-leader proof, and
     the reserved spawn request remains durable on every losing-owner or
     dispatch-suspended (`none` / `unknown`) path.

3. Promote the landed behavior into current specs and update the traceability
   chain.
   - Outcome:
     canonical docs describe the shared ownership seam and manager pre-dispatch
     fence accurately, without overpromising public singleton semantics.
   - Files to touch:
     - `docs/specifications/01-Core_Components.md`
     - `docs/specifications/03-Manager_Architecture.md`
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/07-System_Invariants.md`
     - `docs/lessons.md` if the implementation proves a reusable pattern
   - Read first:
     - the landed code diff for Tasks 1 and 2
     - nearby implementation mappings and related-plan sections in the touched
       specs
   - Update the current specs together:
     - `01` should describe the shared ownership seam as an implementation aid,
       not a public service framework
     - `03` should describe the manager’s late ownership fence, the split
       between dispatch ineligibility and persistent-child supervision, and the
       fact that losing-owner fencing is not spawn rejection
     - `05` should describe the message-flow consequence for a reserved spawn
       request that loses ownership before launch, including the
       runtime-owned pre-launch exact-message requeue path, manager-wide
       dispatch suspension on `none` / `unknown`, and the fact that manual
       recovery still applies when the message remains stranded in reserved
     - `07` should only tighten invariants if the landed behavior changes what
       is actually guaranteed today
   - Keep endpoint docs explicit that duplicates remain observable conflicts.
     Do not quietly relabel endpoint names as exclusive singleton claims.
   - If the implementation reveals a durable rule worth reusing, add a short
     lesson about rechecking convergent ownership immediately before side
     effects.
   - Done signal:
     spec sections, plan backlinks, and touched code docstrings form a clear
     bidirectional trace from manager and endpoint behavior to code ownership.

## 6. Verification and Review

Verification commands for the implementation slice:

- `./.venv/bin/python -m pytest tests/commands/test_queue.py -q`
- `./.venv/bin/python -m pytest tests/commands/test_run.py -q`
- `./.venv/bin/python -m pytest tests/core/test_manager.py -q`
- `./.venv/bin/python -m pytest tests/commands/test_queue.py tests/commands/test_run.py tests/core/test_manager.py -q`
- `./.venv/bin/ruff check weft tests`
- `./.venv/bin/mypy weft`

Keep the real broker-backed tests. Do not replace the manager race proof with
mock-only unit tests. Internal helper tests are acceptable as supporting
coverage, but the contract proof must come from the queue-visible manager and
endpoint behaviors.

Independent review is required:

1. on this plan before implementation
2. after Task 2 lands, because it changes manager dispatch behavior on the
   durable spine
3. again before closing the spec updates, so current docs do not overstate the
   helper into a public singleton feature

Observable success after implementation:

- endpoint resolution still returns the same canonical owner and duplicate count
- a losing manager does not emit `task_spawned` for the fenced request
- a losing-owner or dispatch-suspended (`none` / `unknown`) fence does not emit
  `task_spawn_rejected`
- fence diagnostics use exactly `manager_spawn_fenced_requeued`,
  `manager_spawn_fenced_stranded`, and `manager_spawn_fence_suspended`
- the fenced spawn request is either back in `weft.spawn.requests` or still
  visibly present in the losing manager’s reserved queue
- a superseded manager with persistent children remains alive only to supervise
  already-launched persistent work and does not dispatch new children
- a suspended manager in `none` or `unknown` state does not reserve later spawn
  requests until ownership becomes positively launchable again
- submission reconciliation still classifies only real spawn rejection as
  `rejected`

## 7. Risks and Open Questions

- The main design risk is over-generalization. A useful shared helper could
  easily turn into a registry framework or hidden singleton system. Keep the
  helper smaller than that.
- The main runtime risk is conflating “not owner” with “ownership unknown.”
  This plan resolves that by preferring an inspectable reserved-message stall
  over guess-based launch or guess-based supersession when ownership is unknown.
- The main queue-handling risk is forgetting that the losing manager already
  owns the reserved spawn request. A naïve early return would convert a
  duplicate-dispatch hardening into a stuck-dispatch bug.
- The main sequencing risk is requeue-before-suspension. If the manager makes
  the exact-message move before it becomes non-dispatching, the next main-loop
  poll can reclaim the same work item and defeat the fence.
- The main state-model risk is pretending the existing persistent-child yield
  logic already solves dispatch fencing. It does not. This slice intentionally
  allows dispatch suspension and child supervision to diverge without adding a
  second leader-election algorithm.
- The main docs risk is semantic drift. Endpoint names are still discovery-only
  after this slice. If later work wants public singleton semantics for named
  tasks or timers, that needs its own spec and plan.

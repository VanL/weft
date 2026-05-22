# Service Liveness And Health Convergence Plan

Status: draft
Source specs: docs/specifications/01-Core_Components.md [CC-2.4.1]; docs/specifications/03-Manager_Architecture.md [MA-1.4], [MA-1.6a]; docs/specifications/05-Message_Flow_and_State.md [MF-3], [MF-6]; docs/specifications/07-System_Invariants.md [OBS.1]-[OBS.3], [OBS.11]-[OBS.14], [MANAGER.8], [MANAGER.15]-[MANAGER.16]; docs/specifications/10-CLI_Interface.md [CLI-1.2]
Superseded by: none

## 1. Goal

Fix the remaining ops-visible service-health failures after the 0.9.25 rollout:
internal singleton replacement can lag behind user work, heartbeat can exit
because it trusts stale owner evidence, `task status` can report inferred
failure without terminal proof, and internal spawn health is hard to observe.
The desired end state is one canonical liveness/ownership interpretation reused
by manager selection, managed-service supervision, heartbeat supersession, and
public status reconstruction, with enough queue-visible evidence to verify the
system in production.

Engineering standard: choose the more correct path. Do not paper over flakes,
do not widen timeouts as the fix, and do not create a second liveness path
because the existing one is awkward to call.

## 2. Source Documents

- `docs/specifications/01-Core_Components.md` [CC-2.4.1]: runtime endpoint
  registry and task-local endpoint ownership.
- `docs/specifications/03-Manager_Architecture.md` [MA-1.4], [MA-1.6a]:
  canonical manager registry replay, dispatch ownership, and managed internal
  service supervision.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-3], [MF-6]:
  task-local control evidence, status reconstruction priority, and manager
  spawn flow including `weft.spawn.internal`.
- `docs/specifications/07-System_Invariants.md` [OBS.1]-[OBS.3],
  [OBS.11]-[OBS.14],
  [MANAGER.8], [MANAGER.15]-[MANAGER.16]: live evidence cannot reanimate
  terminal truth, endpoint names under `_weft.` are internal, TaskMonitor is
  operational only, claimed outbox residue stays recovery evidence, singleton
  services reduce evidence through one transition table, and internal spawn
  drains before public spawn work.
- `docs/specifications/10-CLI_Interface.md` [CLI-1.2]: `task status`,
  `task list`, and additive reconciliation diagnostics.
- `docs/plans/2026-05-09-internal-spawn-priority-queue-plan.md`: completed
  internal-spawn queue design. Treat it as current behavior to preserve, not a
  new backlog.
- `docs/plans/2026-05-09-prune-path-unification-plan.md`: completed canonical
  prune path. Preserve the single prune engine; do not fork TaskMonitor
  cleanup again.
- `docs/plans/2026-05-08-deterministic-manager-service-reconciler-plan.md`:
  completed manager-owned service reducer. Preserve the reducer as the only
  place that chooses managed-service side effects.
- `docs/agent-context/runbooks/writing-plans.md` and
  `docs/agent-context/runbooks/hardening-plans.md`: required plan standard for
  this cross-boundary runtime change.

## 3. Context And Key Files

Files to modify:

- `weft/core/manager.py`
- `weft/core/manager_runtime.py`
- `weft/core/tasks/heartbeat.py`
- `weft/core/task_evidence.py`
- `weft/commands/tasks.py`
- `weft/commands/status.py`
- `weft/commands/manager.py` if manager/status output needs additive fields
- `weft/_constants.py` only if naming a new diagnostic classification or
  status literal is necessary
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/lessons.md`

Tests to modify or add:

- `tests/core/test_manager.py`
- `tests/core/test_manager_services.py`
- `tests/core/test_heartbeat_helpers.py`
- `tests/cli/test_cli_serve.py`
- `tests/commands/test_status.py`
- `tests/commands/test_task_evidence.py`
- `tests/commands/test_manager_commands.py` if the manager status surface gains
  additive internal-spawn fields
- `tests/specs/test_plan_metadata.py` indirectly through this plan and the
  `docs/plans/README.md` row

Read first:

- `weft/core/manager.py`:
  `_read_active_manager_records`, `_active_manager_records`,
  `_leader_tid`, `_evaluate_dispatch_ownership`,
  `_service_supervision_allowed`, `_reconcile_managed_services`,
  `_tick_managed_service`, `_observed_service_candidates_by_key`,
  `_cleanup_children`, and `process_once`.
- `weft/core/manager_runtime.py`:
  the command-side manager registry replay and selection helpers. Do not assume
  these already match `Manager._read_active_manager_records`.
- `weft/core/tasks/heartbeat.py`:
  `_service_ownership`, `_exit_if_superseded`, `_emit_due_registrations`, and
  both `heartbeat_service_superseded` event paths.
- `weft/core/endpoints.py`:
  `resolve_endpoint`, `list_resolved_endpoints`, `_record_owner_is_live`, and
  the task-log/TID-mapping evidence readers used to decide endpoint owner
  liveness.
- `weft/core/task_evidence.py`:
  `known_tid_evidence`, `stale_observer_evidence`, `runtime_evidence`, and
  terminal evidence helpers.
- `weft/commands/tasks.py`:
  `task_status`, `_stale_observer_snapshot`, and JSON rendering fields.
- `weft/core/tasks/multiqueue_watcher.py`:
  priority scheduling behavior. This should already support strict priority;
  do not rewrite it unless a red test proves it violates [MANAGER.16].

Comprehension checks before editing:

- Which function currently decides that a manager is the canonical dispatch
  owner? The answer must include both the manager-process path and the
  command-side path.
- Which code path currently lets heartbeat exit as `heartbeat_service_superseded`,
  and what exact endpoint-registry evidence does it trust?
- Which task evidence function synthesizes `failed` when no terminal event is
  present?
- Which queue receives manager-owned heartbeat and TaskMonitor spawn envelopes,
  and how can a test prove it without mocking `Queue.write`?

Current structure:

- `Manager._read_active_manager_records()` replays `weft.state.managers`,
  filters live canonical records, and prunes stale active rows. It is the
  manager-process view used by `_evaluate_dispatch_ownership()`.
- `weft/core/manager_runtime.py` has command-side manager selection/listing
  logic. If it independently interprets liveness, it must be kept semantically
  aligned with the manager-process view.
- `HeartbeatTask._service_ownership()` currently resolves the reserved
  `_weft.heartbeat` endpoint through `weft/core/endpoints.py::resolve_endpoint`.
  It does not read `weft.state.managers` directly. Ops evidence showed
  heartbeat repeatedly emitting `heartbeat_service_superseded` with an old
  owner TID while the current manager kept respawning it, which points at
  endpoint-owner liveness or stale endpoint pruning, not direct manager
  registry selection.
- `Manager._reconcile_managed_services()` and
  `weft/core/manager_services.py::reduce_managed_service_state()` are the
  canonical service-supervision path. Do not create a heartbeat-only or
  TaskMonitor-only reducer.
- `TaskMonitor` cleanup now calls canonical prune code under
  `weft/core/pruning/`. Preserve that; this plan is not a prune rewrite.
- `known_tid_evidence()` and `task_status()` are the public status path. Runtime
  liveness disagreement may appear in `reconciliation`, but no code should
  report `failed` without terminal proof unless the specification explicitly
  names a wrapper-lost or equivalent terminal classification.

## 4. Invariants And Constraints

- TIDs remain immutable SimpleBroker message timestamps. Do not alter TID
  generation or rewrite historic TIDs.
- State transitions remain forward-only. Do not change a terminal task back to
  running because a process or PONG is visible.
- `spec` and `io` remain immutable after TaskSpec creation. Runtime
  diagnostics must be metadata/reconciliation output, not TaskSpec mutation.
- `weft.state.*` queues remain runtime-only. Do not make them dump truth or
  lifecycle truth.
- `weft.spawn.internal` remains manager-owned. Do not add public CLI/client
  write surfaces for it.
- Internal and public spawn requests must share the same manager launch path:
  reserve, parse, final ownership fence, launch, initial inbox write, and exact
  ack/delete.
- Dispatch ownership outcomes stay distinct: `self`, `other`, `none`,
  `unknown`. Only `self` authorizes launch; only positive `other` authorizes a
  non-leader conclusion.
- Heartbeat may exit as superseded only on a positive live endpoint owner proof
  from the reserved `_weft.heartbeat` endpoint. It must not exit on stale
  endpoint registry history, missing liveness, endpoint-owner uncertainty, or
  stale manager history.
- Task status may expose stale or liveness-conflict diagnostics, but must not
  synthesize `failed` from stale/missing liveness without terminal lifecycle
  evidence.
- Tests should use real broker-backed queues and, where needed,
  `WeftTestHarness` or real manager/task processes. Mock only process liveness
  or plugin boundaries that are inherently external or nondeterministic.
- Avoid broad refactors. If a change starts creating a new status model,
  service supervisor, or health subsystem, stop and re-plan.

## 5. Implementation Tasks

### Task 1: Preserve the landed internal-service restart ordering fix

Owner: `Manager.process_once` and child reap sequencing.

Files:

- Modify `weft/core/manager.py`.
- Add/extend tests in `tests/core/test_manager.py` and
  `tests/cli/test_cli_serve.py`.

Current state:

- This slice is already landed in the current working branch. The implementation
  changes `_cleanup_children()` to return whether it reaped a child, reconciles
  internal services before ordinary spawn work only after a reap, and adds
  `tests/core/test_manager.py::test_process_once_reconciles_internal_services_before_user_spawn_work`.
- Keep this task in the plan for traceability and regression protection. Do not
  duplicate the test or reimplement the same code.

Regression test:

- Add a manager unit test with a tracked dead TaskMonitor child and pending
  public `weft.spawn.requests` work. Assert that after `process_once()`, the
  TaskMonitor replacement service enqueue happens before ordinary public spawn
  work is handled.
- Keep this test broker-backed. Do not only mock the service reducer.
- Keep existing dispatch-suspension recovery tests green. A broad pre-work
  ownership check breaks those tests and is not acceptable.

Implementation to preserve:

- Make `_cleanup_children()` return `True` only when at least one tracked child
  was reaped.
- In `Manager.process_once()`, after dispatch-suspension checks and before
  `super().process_once()`, call `_cleanup_children()`. If it returns `True`,
  reconcile internal services only:
  `self._reconcile_managed_services(include_autostart=False)`.
- Do not run autostart reconciliation in this pre-work slot. Autostart can add
  non-service work and must not interfere with dispatch-suspension recovery.
- Preserve the existing post-work `_cleanup_children()` and full
  `_reconcile_managed_services()` call.

Verification:

- `./.venv/bin/python -m pytest tests/core/test_manager.py::test_process_once_reconciles_internal_services_before_user_spawn_work -q`
- `./.venv/bin/python -m pytest tests/core/test_manager.py::test_manager_suspension_recovers_reserved_request_before_later_inbox_work tests/core/test_manager.py::test_manager_self_owner_exhausts_recovery_before_later_inbox_work -q`
- `./.venv/bin/python -m pytest tests/cli/test_cli_serve.py::test_serve_restarts_singleton_services_without_duplicates -q`

Stop and re-evaluate if:

- The implementation calls `_evaluate_dispatch_ownership()` more often on
  ordinary turns and breaks dispatch-suspension recovery.
- The implementation special-cases TaskMonitor but not heartbeat. The fix is
  for manager-owned internal services, not one service name.

### Task 2: Fix heartbeat supersession through endpoint-owner liveness

Owner: heartbeat reserved endpoint ownership checks.

Files:

- Modify `weft/core/tasks/heartbeat.py`.
- Modify `weft/core/endpoints.py` if endpoint-owner liveness is the stale path.
- Prefer adding a small helper in `weft/core/endpoints.py` if needed. Do not
  make heartbeat replay `weft.state.managers` directly, and do not create a
  large service-health package.
- Add/extend tests in `tests/core/test_heartbeat_helpers.py` and possibly
  `tests/core/test_manager.py`.

Red tests first:

- Create a heartbeat ownership test where `weft.state.endpoints` contains an
  old lower-TID active `_weft.heartbeat` endpoint record whose owner is stale
  by endpoint-owner evidence, plus a current live heartbeat endpoint. Assert
  heartbeat does not emit `heartbeat_service_superseded` for the stale lower
  TID and does not mark itself completed.
- Create a positive test where a lower-TID `_weft.heartbeat` endpoint owner is
  live by the endpoint liveness rules. Assert heartbeat emits
  `heartbeat_service_superseded`, records `owner_tid`, marks itself completed,
  and stops.
- Create missing/uncertain endpoint tests. Assert heartbeat reschedules due
  registrations and keeps running. It must not exit on uncertainty.
- Cover both supersession call sites: `_exit_if_superseded()` at the top of
  `process_once()` and `_emit_due_registrations()` when a due heartbeat is
  about to emit.

Implementation:

- Keep heartbeat ownership rooted in the reserved endpoint registry:
  `resolve_endpoint(self._context, INTERNAL_HEARTBEAT_ENDPOINT_NAME)`.
- Fix the endpoint-owner liveness reduction if it treats stale endpoint owners
  as live. The endpoint layer should use task terminal status, TID mappings,
  runner handle authority, and registered runtime liveness probes consistently.
- If endpoint liveness cannot decide, return an uncertainty/no-owner result
  that keeps heartbeat running. Do not turn uncertainty into positive `other`.
- Preserve `heartbeat_service_superseded` as an operational event, but only
  emit it for positive live endpoint ownership by another TID.
- Do not construct a `Manager` inside `HeartbeatTask`; that would couple a task
  runtime to manager side effects.

Verification:

- `./.venv/bin/python -m pytest tests/core/test_heartbeat_helpers.py -q`
- `./.venv/bin/python -m pytest tests/cli/test_cli_serve.py::test_serve_restarts_singleton_services_without_duplicates -q`

Stop and re-evaluate if:

- The heartbeat code starts parsing manager registry rows directly. Heartbeat
  owns a reserved endpoint; manager selection owns `weft.state.managers`.
- The fix requires changing heartbeat wire payloads or registration schema.
  That would be a broader compatibility change and needs a separate plan.

### Task 3: Collapse manager liveness/selection helpers where duplication remains

Owner: canonical manager registry replay and active-manager selection.

Files:

- Modify `weft/core/manager.py`.
- Modify `weft/core/manager_runtime.py`.
- Modify `weft/commands/status.py` if it has its own active-manager filtering.
- Add/extend tests in `tests/commands/test_manager_commands.py`,
  `tests/commands/test_status.py`, and `tests/core/test_manager.py`.

Red tests first:

- Add command-side tests for a stale lower-TID active manager plus a live
  higher-TID manager. Assert `manager list`, `status`, and
  `Manager._evaluate_dispatch_ownership()` all choose the same live manager.
- Add the inverse positive case: a live lower-TID canonical manager wins across
  all these surfaces.
- Include a container/external-supervisor runtime handle case. The ops manager
  is externally supervised in Docker, so host PID checks alone are not enough.

Implementation:

- Identify the smallest shared helper that can own manager registry reduction.
  It should return a typed or well-documented result containing:
  active records, stale timestamps, leader TID, and read confidence.
- Reuse the helper from both manager-process code and command-side code.
- Preserve side-effect boundaries:
  manager-process code may prune stale manager registry rows where it already
  does today;
  command-side read-only commands should not gain unexpected destructive
  behavior unless that behavior already exists and is tested.
- Keep `is_canonical_manager_record`, `canonical_owner_tid`, and runtime
  liveness probe registry semantics. Do not invent a new manager identity
  model.

Verification:

- `./.venv/bin/python -m pytest tests/commands/test_manager_commands.py tests/commands/test_status.py tests/core/test_manager.py -q`
- `./.venv/bin/python -m pytest tests/core/test_runtime_handle_liveness.py tests/cli/test_cli_serve.py -q`

Stop and re-evaluate if:

- The shared helper wants to delete registry rows from read-only status paths.
- The result shape becomes a general health API. This task is a liveness
  reduction unification, not a product surface expansion.

### Task 4: Fix task status so stale liveness is not reported as failed

Owner: public task status reconstruction.

Files:

- Modify `weft/core/task_evidence.py`.
- Modify `weft/commands/tasks.py`.
- Modify `weft/commands/status.py` if `weft status` renders the same inferred
  status.
- Update `docs/specifications/05-Message_Flow_and_State.md` and
  `docs/specifications/10-CLI_Interface.md`.
- Add/extend tests in `tests/commands/test_task_evidence.py` and
  `tests/commands/test_status.py`.

Red tests first:

- Recreate the ops shape: a non-terminal task-log row for an internal
  TaskMonitor with stale or mismatched runtime evidence, no terminal task-log
  event, no typed terminal `ctrl_out`, and no readable final outbox. Assert
  `task_status()` does not return `failed`.
- Assert the returned status is either the last observed nonterminal status
  plus `reconciliation.classification == "stale_liveness"` or a new explicit
  nonterminal `stale`/`unknown` classification if the spec is updated to allow
  it. Prefer additive reconciliation over adding a new public status literal
  unless the existing renderer cannot express the distinction clearly.
- If no task-log lifecycle row exists and only stale runtime evidence exists,
  return an existing nonterminal/unknown-compatible status chosen explicitly in
  the spec update. Do not let the implementer invent a new public status
  literal casually. `pending` is preferable to `running` if no current live
  proof exists, but the test and spec must name the chosen value.
- Add a claimed-outbox regression: claimed outbox residue must still surface
  `claimed_result_without_terminal` even after stale observer evidence stops
  returning `failed`.
- Preserve current terminal tests:
  terminal task-log `failed` remains failed;
  typed terminal ctrl_out wrapper-lost remains failed;
  result-without-terminal remains completed only where existing spec says it
  may.

Implementation:

- Find the code in `stale_observer_evidence()` or `known_tid_evidence()` that
  turns stale observer fallback into `status="failed"`.
- Change it so stale/missing liveness without terminal evidence is diagnostic,
  not terminal lifecycle truth.
- Refactor the `claimed_outbox_result_evidence()` gate so it does not depend on
  `stale_snapshot.status == "failed"`. Claimed outbox residue is recovery
  evidence under [OBS.14] and must remain visible even when stale observer
  evidence becomes nonterminal.
- Add or reuse a reconciliation classification such as `stale_liveness` or
  `runtime_stale_without_terminal`. Keep it additive in JSON.
- Do not change terminal evidence priority. Terminal task-log and typed
  terminal ctrl_out still win.
- Update CLI text rendering only as needed to avoid printing a false
  `failed`. Keep output compatible and concise.

Verification:

- `./.venv/bin/python -m pytest tests/commands/test_task_evidence.py tests/commands/test_status.py -q`

Stop and re-evaluate if:

- More than one public status literal must change. That is a public contract
  change and needs a tighter spec update.
- The implementation weakens real wrapper-lost detection. Wrapper-lost is
  terminal proof; stale liveness is not.

### Task 5: Add explicit internal-spawn health observability

Owner: manager/status observability. This task must stay additive.

Files:

- Modify `weft/core/manager.py` if control snapshots need one more additive
  field.
- Modify `weft/commands/status.py` and possibly `weft/commands/manager.py`.
- Modify CLI renderers in `weft/cli/app.py` only if command output needs a
  small text addition.
- Add tests in `tests/commands/test_status.py`,
  `tests/commands/test_manager_commands.py`, and/or `tests/cli/test_cli_serve.py`.

Red tests first:

- Start a foreground manager with internal services enabled. Assert the manager
  control/status snapshot exposes that internal spawn is attached, including
  `internal_requests="weft.spawn.internal"` and the internal reserved queue
  name.
- Add a command/status test that reports internal spawn pending depth or an
  equivalent `attached: true` signal without relying on `queue list` showing an
  empty auto-created queue.
- Assert public `weft.spawn.requests` does not receive manager-owned heartbeat
  or TaskMonitor spawn envelopes when internal spawn is attached.
- Add a failure-mode diagnostic test or code inspection note for the observed
  ops shape: if internal spawn is not attached, explain whether manager-owned
  service envelopes can fall back to public `weft.spawn.requests`. If fallback
  exists, make it visible in status diagnostics. If fallback does not exist,
  document that public queue growth came from user/autostart work rather than
  internal services.

Implementation:

- Prefer using existing manager control snapshot fields:
  `_control_snapshot_fields()` already has `internal_requests` and
  `internal_reserved` when attached. If command surfaces ignore those fields,
  expose them additively.
- If queue depth is added, read exact queues directly by name. Do not infer
  health from `queue list`, because an empty auto-created queue may not be
  listed.
- Keep internal spawn health read-only. Do not add cleanup, create, or repair
  behavior here.
- Treat `queue list` absence as ambiguous. A healthy empty auto-created queue
  may not appear there; direct queue probes or manager control fields are the
  health source.

Verification:

- `./.venv/bin/python -m pytest tests/commands/test_status.py tests/commands/test_manager_commands.py tests/cli/test_cli_serve.py -q`

Stop and re-evaluate if:

- The implementation needs a new CLI subcommand. This slice should add
  diagnostic fields to existing status surfaces, not create a dashboard.

### Task 6: Update specs and lessons

Owner: traceability.

Files:

- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/lessons.md`
- This plan file, if scope changes.

Required updates:

- Add this plan as a backlink near the touched implementation mappings.
- State that heartbeat supersession uses reserved endpoint ownership and may
  exit only on positive live `_weft.heartbeat` endpoint ownership by another
  TID.
- State that stale liveness without terminal evidence is surfaced as
  reconciliation/diagnostic output, not `failed`.
- State that internal spawn health must be observable without relying on
  `queue list` presence of an empty queue.
- Add a lesson: liveness/ownership reductions must not fork across manager,
  heartbeat, status, and cleanup surfaces.

Verification:

- `./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q`

## 6. Test Strategy And Gates

Development loop:

1. For each task, write the smallest red test first.
2. Run only that test and confirm it fails for the expected reason.
3. Implement the smallest production change.
4. Run the narrow test green.
5. Run the surrounding slice tests.
6. Only then continue to the next task.

Do not over-mock:

- Use real SimpleBroker queues for manager registry, task log, spawn queues,
  task-local ctrl queues, and internal spawn checks.
- Use real manager/task processes for CLI serve tests.
- It is acceptable to monkeypatch process liveness probes or runtime plugin
  responses where the test is specifically about interpreting those responses.
- Do not mock `Manager._reconcile_managed_services()` for behavior tests. That
  is the production path being protected.

Required local gates before merge:

- `./.venv/bin/python -m pytest tests/core/test_manager.py tests/core/test_manager_services.py tests/core/test_heartbeat_helpers.py -q`
- `./.venv/bin/python -m pytest tests/commands/test_task_evidence.py tests/commands/test_status.py tests/commands/test_manager_commands.py -q`
- `./.venv/bin/python -m pytest tests/cli/test_cli_serve.py -q`
- `./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q`
- `./.venv/bin/ruff check weft tests/core/test_manager.py tests/core/test_heartbeat_helpers.py tests/commands/test_task_evidence.py tests/commands/test_status.py tests/commands/test_manager_commands.py tests/cli/test_cli_serve.py`
- `./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox`

Full-suite gate:

- Run the full test suite before release tagging. If load-only failures appear,
  treat them as evidence. Do not mark them flaky without a code-level
  explanation.

## 7. Rollout And Ops Verification

Rollout:

- Ship the manager restart ordering fix, heartbeat ownership unification, and
  task-status correction together. Deploying only observability is useful but
  will not stop the churn.
- No data migration is required. All changes are reader/reducer behavior and
  additive diagnostics.
- Rollback is a package rollback. The queue formats remain backward-compatible.

Expected ops changes after deploy:

- `opsworker-serve` remains running.
- `docker top governance-opsworker-serve-1` shows one manager plus bounded
  child work. It should not show heartbeat respawn churn every few seconds.
- `weft task status <task-monitor-tid>` no longer reports `failed` solely from
  stale liveness. It should report running, stale/unknown with reconciliation,
  or a real terminal state if terminal proof exists.
- `weft.spawn.internal` health is visible through status/control diagnostics
  even if `queue list` omits an empty queue.
- Public `weft.spawn.requests` should not accumulate manager-owned heartbeat or
  TaskMonitor service work.
- `weft.log.tasks` may not drop immediately if retention scanning is catching
  up, but it should stop growing from heartbeat/TaskMonitor service churn.
  Safe retention candidate counts should begin decreasing once TaskMonitor
  delete cycles complete.
- Falsifiable bar: within two minutes of restart on a quiet system, task-log
  growth from `heartbeat-service`, `task-monitor`, and
  `heartbeat_service_superseded` events should stop. User/autostart workload
  may still add ordinary task events.

Ops commands:

```bash
ssh ops
cd governance
source .env
/opt/venv/bin/weft --version
/opt/venv/bin/weft status
/opt/venv/bin/weft manager list --json
/opt/venv/bin/weft task status <task-monitor-tid> --json
/opt/venv/bin/weft queue list | grep -E 'weft\.spawn|weft\.log\.tasks|weft\.state'
docker top governance-opsworker-serve-1 -eo pid,ppid,stat,comm,args
docker compose logs --tail=100 opsworker-serve
```

Use exact queue probes for internal spawn if the CLI supports them; do not
treat absence from `queue list` as failure by itself after Task 5 lands.

## 8. Out Of Scope

- No SimpleBroker changes.
- No new public write API for `weft.spawn.internal`.
- No new queue cleanup policy beyond the existing canonical prune path.
- No TaskMonitor logging callback in this slice.
- No broad CLI redesign or dashboard.
- No changes to TID format, TaskSpec schema, or persisted task-log payload
  shape except additive reconciliation/diagnostic fields.

## 9. Review Notes

Fresh-eye review pass:

- The first draft was too broad around "one canonical liveness path." The
  implementable version narrows that to manager ownership and status
  reconstruction, with explicit files and tests.
- The heartbeat fix must not construct a `Manager` object inside
  `HeartbeatTask`; that would couple task runtime to manager side effects. It
  also must not make heartbeat parse manager registry rows directly. Heartbeat
  supersession is based on the reserved `_weft.heartbeat` endpoint owner, so
  stale endpoint ownership is the layer to fix.
- The status fix must not hide real failures. The plan preserves terminal
  task-log and typed terminal ctrl_out as terminal proof.
- Internal spawn observability must not depend on `queue list`. Ops showed that
  an absent queue-list row is ambiguous.
- The manager pre-work reconciliation fix must be conditional on reaping a
  child. An unconditional pre-work reconciliation adds ownership checks and can
  break dispatch-suspension recovery.
- External review caught that this manager pre-work reconciliation fix is
  already present in the current branch. Keep it as a regression-protected
  landed slice, not as new implementation work.

Second review pass:

- The plan stays aligned with the current direction: collapse duplicate
  ownership/liveness interpretation at the correct layers and preserve
  canonical prune/service paths. Manager registry ownership and endpoint
  ownership are related but distinct reductions; the plan now keeps them
  distinct instead of forcing heartbeat through manager registry replay.
- The highest-risk ambiguity is whether to add a public `stale` status literal.
  The default instruction is not to do that. Prefer additive reconciliation
  unless a red test proves current output cannot avoid false `failed` without
  a new literal.
- The deployment expectation is intentionally modest: `weft.log.tasks` may not
  immediately shrink, but service churn should stop and safe cleanup counts
  should trend down.

Independent Claude review pass:

- Critical finding accepted: Task 1 was already landed in the current branch.
  The task now says to preserve the fix and avoid duplicate implementation.
- Critical finding accepted: heartbeat supersession uses endpoint registry
  ownership, not manager registry ownership. Task 2 now targets
  `weft/core/endpoints.py` and both heartbeat supersession call sites.
- High finding accepted: Task 4 now requires claimed-outbox recovery evidence
  to remain visible independently of stale observer terminality.
- High finding accepted: Task 5 now requires explaining public spawn growth
  instead of merely asserting the desired internal-spawn property.

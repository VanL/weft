# Internal Service Observability Plan

Status: completed
Source specs: docs/specifications/00-Quick_Reference.md; docs/specifications/03-Manager_Architecture.md [MA-1], [MA-1.6a], [MA-2]; docs/specifications/05-Message_Flow_and_State.md [MF-5], [MF-6]; docs/specifications/07-System_Invariants.md [OBS.9], [OBS.12], [MANAGER.15], [MANAGER.16]; docs/specifications/10-CLI_Interface.md [CLI-1.2.1]
Superseded by: none

## 1. Goal

Improve ops visibility for manager-owned internal services by adding a
service-level read model that reduces existing queue evidence for heartbeat and
TaskMonitor. The manager already launches these services and emits durable
`task_spawned` evidence before each child has necessarily written its own task
lifecycle rows. Status surfaces should expose that intermediate state instead
of relying only on transient `weft.spawn.internal` queue contents or child-local
task-log rows.

Requested outcome:

- `weft status` and `system_status()` show whether heartbeat and TaskMonitor
  are desired, pending, launched, running, uncertain, terminal, or disabled.
- The view uses queue-derived truth first, with manager-local TID pointers only
  as hints when they are later added.
- The plan does not change service launch, queue names, cleanup behavior, or
  TaskMonitor processor policy.

## 2. Preflight

Requested outcomes:

- Add an ops-visible service status view for manager-owned internal services.
- Preserve the existing internal service launch path through Manager, Consumer,
  task log, and TID mappings.
- Make `weft.spawn.internal` absence from ordinary `queue list` non-confusing
  by reporting service state from durable evidence instead.
- Add real broker/process tests for the launch-to-child-log visibility gap.

Source-of-truth files:

- `docs/specifications/03-Manager_Architecture.md` [MA-1], [MA-1.6a], [MA-2]
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5], [MF-6]
- `docs/specifications/07-System_Invariants.md` [OBS.9], [OBS.12],
  [MANAGER.15], [MANAGER.16]
- `docs/specifications/10-CLI_Interface.md` [CLI-1.2.1]

Invariants that must not move:

- TIDs remain 64-bit SimpleBroker hybrid timestamps and are immutable after
  TaskSpec creation.
- State transitions remain forward-only.
- Reserved queue policy for public and internal spawn requests is unchanged.
- `weft.spawn.internal` remains transient manager-owned spawn work, not the
  canonical status source.
- Runtime-only `weft.state.*` queues stay runtime-only and are not added to
  dump/load truth.
- Public submission surfaces still cannot authorize internal runtime class
  selection through TaskSpec metadata.

Assumptions that affect correctness:

- Manager `task_spawned` events with `child_tid`, `child_taskspec`, and
  `child_pid` are durable enough to bridge the launch-to-child-log window.
- Child-local task-log and TID mapping evidence should override manager launch
  evidence when present.
- Manager registry records may include internal queue names today, but they do
  not yet include canonical service status.

Commands that can run in parallel:

- File reads, `rg`, and focused static inspection can run in parallel.

Commands that must run in sequence:

- Real manager/TaskMonitor lifecycle tests must run after code changes and
  cleanup must complete before the next broker-backed integration run.

## 3. Source Documents

Source specs:

- `docs/specifications/00-Quick_Reference.md`: queue catalogue, especially
  `weft.spawn.internal` and runtime-state queue expectations.
- `docs/specifications/03-Manager_Architecture.md` [MA-1], [MA-1.6a], [MA-2]:
  manager spawn flow, managed internal services, and TID/message correlation.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5], [MF-6]:
  task-log replay and manager spawn flow.
- `docs/specifications/07-System_Invariants.md` [OBS.9], [OBS.12],
  [MANAGER.15], [MANAGER.16]: observability and manager-owned convergence
  requirements.
- `docs/specifications/10-CLI_Interface.md` [CLI-1.2.1]: status output
  contract.

Related plans:

- `docs/plans/2026-05-07-phase-7-task-monitor-supervision-and-cleanup-plan.md`
  introduced the supervised TaskMonitor and remains background context for
  monitor behavior.
- `docs/plans/2026-05-08-phase-7-manager-service-reconciler-cleanup-plan.md`
  and `docs/plans/2026-05-10-control-and-service-convergence-state-machine-plan.md`
  define the current manager-owned service reducer shape. This plan should not
  change that reducer's launch semantics.
- `docs/plans/2026-05-09-internal-spawn-priority-queue-plan.md` added
  `weft.spawn.internal`. This plan should explain why queue-list visibility is
  not enough for service observability.
- `docs/plans/2026-05-11-manager-serve-operational-log-plan.md` is adjacent
  foreground logging work. This plan must not depend on foreground serve logs;
  service status must work for detached managers too.

Repo guidance:

- `AGENTS.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/runtime-and-context-patterns.md`
- `docs/agent-context/runbooks/testing-patterns.md`
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`
- `docs/lessons.md`, especially "Manager-owned service launch has two
  visibility phases".

Spec updates required during implementation:

- Update `docs/specifications/03-Manager_Architecture.md` [MA-1.6a] to describe
  the service read model and its evidence ordering.
- Update `docs/specifications/05-Message_Flow_and_State.md` [MF-5], [MF-6] to
  say manager `task_spawned.child_taskspec` can produce a service-status row
  before the child has emitted its own task log row.
- Update `docs/specifications/10-CLI_Interface.md` [CLI-1.2.1] if any
  user-facing JSON or text field is added to `weft status`.
- Add a backlink to this plan near each touched spec section.

## 4. Context and Key Files

Files to modify:

- `weft/commands/system.py`
  - Owns `cmd_status()`, `system_status()`, task-log replay, manager summary,
    task summary, and JSON status payload rendering.
  - Add the shared internal-service read model here or in a small helper under
    `weft/commands/` if the code would otherwise become too dense.
- `weft/commands/types.py`
  - Add a public dataclass for service snapshots if `system_status()` needs a
    structured service list.
- `weft/commands/manager.py`
  - Only touch if manager snapshots need to expose service hints. Do not make
    manager registry pointers the authoritative service state.
- `weft/core/manager.py`
  - Only touch if adding manager-registry hint fields is required. Do not alter
    service launch, reserved queue handling, or convergence logic for this
    slice.
- `weft/_constants.py`
  - Only touch for stable field names or service-status constants. Do not add a
    new environment toggle.
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/10-CLI_Interface.md`
- `tests/commands/test_status.py`
- `tests/commands/test_task_commands.py` if task command status surfaces need
  the same service read model.
- `tests/cli/test_cli_status.py` or existing CLI status test files if text or
  JSON output changes.
- `tests/cli/test_cli_serve.py` only for end-to-end manager/serve visibility
  assertions.

Read first:

- `weft/core/manager.py`
  - `_launch_child_task()`: emits `task_spawned` with `child_tid`,
    `child_taskspec`, `child_pid`, and `service_key`.
  - `_observed_service_candidates_by_key()`: already reduces manager
    `task_spawned` into live service candidates for convergence.
  - `_build_manager_registry_payload()`: current manager registry fields,
    including `internal_requests` and `internal_reserved`.
- `weft/commands/system.py`
  - `_collect_task_snapshot_records()`: current task read model.
  - `_is_internal_service_record()`: current internal-service detection.
  - `_format_manager_summary()` and `_render_json_payload()`: public output.
- `weft/core/manager_services.py`
  - `ServiceCandidate`, `ManagedServiceState`, `reduce_managed_service_state()`,
    and `select_canonical_live_candidate()`.
- `weft/helpers.py`
  - `iter_queue_json_entries()` and PID/runtime helpers.
- `tests/commands/test_status.py`
  - Existing status and stale-liveness tests.
- `tests/cli/test_cli_serve.py`
  - Existing helpers that find live service rows from task-log evidence.

Current structure:

- `weft.spawn.internal` is an input queue. It is not a reliable status surface
  because successful service launches drain it quickly.
- The manager logs child launch on the manager TID using `task_spawned`.
- Child tasks later log their own task lifecycle rows on the child TID.
- `weft status` currently reconstructs task rows mostly from child-keyed
  task-log rows. It can miss the launch-to-child-log window even though manager
  convergence can already see the child.
- Status already keeps internal services running without runtime proof in
  `_stale_liveness_reason()`, but that only helps once a child-keyed task row
  exists.

Comprehension questions before editing:

- Which event proves manager launch before child-local lifecycle publication?
  Expected answer: manager `task_spawned` with `child_tid`, `child_taskspec`,
  and optionally `child_pid`.
- Which evidence wins when manager launch evidence and child-local task-log
  evidence disagree?
  Expected answer: child-local terminal evidence and TID mapping evidence win;
  manager launch evidence is a bridge and liveness hint, not terminal truth.
- Why should the implementation not make `weft.spawn.internal` the service
  status source?
  Expected answer: it is transient input work. Empty means "drained", not
  "service absent".

## 5. Invariants and Constraints

- Do not change the service launch path. Heartbeat and TaskMonitor must still
  launch through manager-owned internal spawn envelopes and the normal
  `Manager -> Consumer -> BaseTask` lifecycle.
- Do not add a second state store. The read model must reduce existing queues:
  `weft.log.tasks`, `weft.state.tid_mappings`, `weft.state.managers`,
  `weft.spawn.internal`, and manager internal reserved queues.
- Do not make manager-local remembered TIDs authoritative. If added to registry
  records, they are hints only.
- Do not change `queue list` default filtering as part of this slice.
  Explaining empty transient queues belongs in status/service output or docs,
  not in queue-list semantics.
- Do not downgrade a running service because runtime handle evidence is missing
  during the launch-to-child-log window.
- Do not treat public TaskSpec metadata alone as manager-owned service
  authority. Reuse the existing internal service metadata checks and manager
  envelope evidence.
- Use generator-based queue history reads. Do not use correctness-critical
  fixed `peek_many(limit=N)` reads against `weft.log.tasks`,
  `weft.state.tid_mappings`, or `weft.state.managers`.
- No new dependency.
- No drive-by cleanup of status formatting.
- No new public queue names.
- If JSON output changes, update `docs/specifications/10-CLI_Interface.md`
  during implementation.

Hidden couplings:

- Manager `task_spawned` records are keyed by the manager TID, while task rows
  are keyed by child TID. The service read model must bridge that shape without
  duplicating child tasks incorrectly.
- Manager registry selection can hide stale managers. Service status should use
  active manager identity as context but must still report durable evidence when
  a service launch was observed.
- Task-local terminal evidence can appear before or after manager cleanup. It
  must override a manager-spawned PID hint.
- `system_status()` is used by client/ops surfaces, while `cmd_status()` owns
  CLI text/JSON. The core reduction should be shared so these do not drift.

Failure classification:

- Malformed JSON in a queue history row is non-fatal and should be skipped using
  existing iterator behavior.
- Failure to inspect a transient queue count is an `unknown` service evidence
  detail, not a status command failure.
- Corrupt service metadata in a manager-authored event should not claim a
  service. Report no service candidate from that row.
- Runtime plugin describe failures should not erase launch evidence. Preserve
  existing runtime diagnostic behavior.

## 6. Proposed Service Read Model

Add a service snapshot shape for manager-owned internal services. The exact
dataclass name can be `InternalServiceSnapshot` or `ServiceSnapshot`; keep it in
`weft/commands/types.py` only if it is returned by `system_status()`.

Suggested fields:

- `key`: internal service key, for example `_weft.service.task_monitor`.
- `name`: display name, for example `task-monitor`.
- `desired`: `true` when the selected active manager should supervise it.
- `enabled`: `true` for TaskMonitor when `WEFT_TASK_MONITOR_ENABLED` is active;
  heartbeat is enabled when a dependent internal service is enabled.
- `status`: one of `disabled`, `pending`, `reserved`, `launched`, `running`,
  `uncertain`, `terminal`, or `unknown`.
- `tid`: best current child TID when known.
- `manager_tid`: manager that launched or currently supervises the service when
  known.
- `evidence`: compact list or string naming the winning evidence source, such
  as `child-task-log`, `tid-mapping`, `manager-task-spawned`,
  `internal-spawn-pending`, or `internal-reserved`.
- `queue`: `weft.spawn.internal` or the manager internal reserved queue when
  pending/reserved evidence is the winning source.
- `pid`: child PID when manager launch evidence or runtime handle evidence has
  one.
- `updated_at`: latest relevant broker timestamp.
- `reconciliation`: optional details when the service is uncertain or when
  evidence conflicts.

Evidence ordering:

1. Child-local terminal evidence for the service TID wins over any liveness
   hint.
2. Child-local nonterminal task-log row plus live runtime/TID mapping evidence
   reports `running`.
3. Child-local nonterminal task-log row without live runtime proof reports
   `running` for internal services, matching the current internal-service stale
   liveness rule, with reconciliation details if needed.
4. Manager `task_spawned` evidence with a live `child_pid` reports `launched`
   or `running` depending on whether the child-local row exists.
5. Manager `task_spawned` evidence without a live PID reports `uncertain`
   unless child-local evidence resolves it.
6. Exact pending message in `weft.spawn.internal` reports `pending`.
7. Exact message in `T{manager_tid}.internal_reserved` reports `reserved`.
8. No evidence for an enabled service reports `unknown` or `pending` depending
   on whether the selected active manager has completed initial service
   reconciliation.

The read model must be careful not to double-count the same child as both a
task row and a separate service row. Task rows can continue to exist; the new
service section is a summary over them.

## 7. Tasks

1. Add tests for the missing visibility bridge.

   Files:

   - `tests/commands/test_status.py`

   Steps:

   - Write a broker-backed test that inserts a manager `task_spawned` row with
     `child_tid`, `child_taskspec` metadata for TaskMonitor, `service_key`, and
     `child_pid`, but no child-keyed task log row.
   - Assert that the new service read model reports TaskMonitor as `launched`
     or `running` with evidence `manager-task-spawned`.
   - Add a second test where a child-keyed terminal row exists for the same TID
     and assert terminal evidence wins.
   - Add a third test where `weft.spawn.internal` contains a TaskMonitor spawn
     request and assert status is `pending`.

   Do not mock:

   - `simplebroker.Queue`
   - queue history iteration
   - task-log replay helpers

   Stop and re-evaluate if:

   - the test needs to patch most of `weft.commands.system`;
   - the service status cannot be expressed from durable queue rows.

   Done when:

   - The tests fail for the current visibility gap before implementation.

2. Implement the shared service evidence collector.

   Files:

   - `weft/commands/system.py`
   - optionally `weft/commands/types.py`

   Steps:

   - Add a helper that replays `weft.log.tasks` once and extracts:
     child-keyed task rows for internal services,
     manager `task_spawned` rows with manager-authored internal service
     metadata,
     and latest timestamps per service key.
   - Reuse `_is_internal_service_record()`,
     `_runtime_handle_from_mapping()`, `_describe_runtime_handle()`,
     `_latest_tid_mapping_entries()`, and `iter_queue_json_entries()`.
   - Do not import `weft.core.manager.Manager` into command code.
   - Consider reusing `ServiceCandidate` from `weft.core.manager_services.py`
     only if it does not create an awkward command-to-core coupling. If reuse
     causes leakage of manager-only state, define a small command-local
     evidence dataclass instead.

   Stop and re-evaluate if:

   - the helper starts duplicating the full manager reducer;
   - a fixed queue-history limit appears;
   - public TaskSpec metadata alone is being trusted as service authority.

   Done when:

   - One function can return a service snapshot list without changing CLI
     rendering yet.

3. Add pending and reserved internal spawn evidence.

   Files:

   - `weft/commands/system.py`

   Steps:

   - Inspect `weft.spawn.internal` for pending manager-owned internal service
     requests.
   - Inspect active manager records for `internal_reserved` queue names and
     check exact reserved messages when possible.
   - Parse only enough spawn payload metadata to identify heartbeat or
     TaskMonitor service keys and child TIDs.
   - Treat queue inspection failures as `unknown` evidence detail, not fatal.

   Stop and re-evaluate if:

   - the code needs to mutate queues;
   - the implementation tries to move or delete messages;
   - it assumes one manager record is always present.

   Done when:

   - Status can distinguish "service not visible yet" from "internal spawn work
     is pending or reserved".

4. Expose service snapshots through status surfaces.

   Files:

   - `weft/commands/types.py`
   - `weft/commands/system.py`
   - CLI status tests

   Steps:

   - Add `services: list[ServiceSnapshot]` to `SystemStatusSnapshot` if the
     typed ops surface should expose this data.
   - Add `"services"` to `cmd_status(json_output=True)`.
   - Add a compact text section to `weft status`, for example:

     ```text
     Services:
       task-monitor running tid=... evidence=child-task-log
       heartbeat-service launched tid=... evidence=manager-task-spawned
     ```

   - Keep existing `Managers:` and `Tasks:` sections stable.
   - Do not hide the existing task rows for internal services in this slice.

   Stop and re-evaluate if:

   - text output becomes too wide for the existing status table;
   - existing JSON consumers are forced to change because a field was renamed
     or removed.

   Done when:

   - Existing status output still works and the new `services` section appears
     only as an additive field/section.

5. Add optional manager-registry service hints only if needed.

   Files:

   - `weft/core/manager.py`
   - `weft/commands/types.py`
   - `weft/commands/manager.py`
   - `weft/commands/system.py`
   - `tests/core/test_manager.py`
   - `tests/commands/test_status.py`

   Steps:

   - If the service read model cannot reliably identify selected manager
     service state from existing queues, add hint fields to manager registry
     payloads, such as:
     `heartbeat_tid`, `task_monitor_tid`, and maybe `services`.
   - Label them as manager-observed hints in code comments and specs.
   - Do not make these hints the source of truth.
   - If a hint conflicts with child-local task-log or TID mapping evidence,
     child-local evidence wins.

   Stop and re-evaluate if:

   - the hint fields are required for correctness rather than presentation;
   - manager restart would make the reported service state wrong.

   Done when:

   - Service status remains correct when hints are absent.

6. Update specs and docs.

   Files:

   - `docs/specifications/03-Manager_Architecture.md`
   - `docs/specifications/05-Message_Flow_and_State.md`
   - `docs/specifications/10-CLI_Interface.md`
   - `docs/specifications/00-Quick_Reference.md` if any queue-status wording is
     clarified

   Steps:

   - Add plan backlinks near touched sections.
   - Describe the two visibility phases:
     manager launch evidence first, child-local lifecycle evidence later.
   - Document that empty `weft.spawn.internal` does not mean the internal
     service is absent.
   - Document any new JSON/text status fields.

   Stop and re-evaluate if:

   - implementation behavior and spec text cannot be made to agree without
     changing service launch semantics.

   Done when:

   - Specs, plan, and code form a bidirectional trace.

7. Run verification.

   Commands:

   ```bash
   . ./.envrc
   ./.venv/bin/python -m pytest tests/commands/test_status.py -q
   ./.venv/bin/python -m pytest tests/cli/test_cli_serve.py -k service -q
   ./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
   ./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
   ./.venv/bin/ruff check weft
   ```

   Add or substitute a focused CLI status test if `test_cli_serve.py -k service`
   does not exercise the new output.

   Done when:

   - A clean manager startup with TaskMonitor enabled shows:
     manager record with internal queues,
     service snapshot for heartbeat,
     service snapshot for TaskMonitor,
     and child task rows when the children have published lifecycle records.

## 8. Output Contract

The change should be additive.

JSON:

- Existing `broker`, `managers`, and `tasks` keys remain unchanged.
- Add `services` as a new top-level key if `cmd_status(json_output=True)` is
  updated.
- Do not rename existing task or manager fields.

Text:

- Keep the broker, manager, and task sections.
- Add a `Services:` section between `Managers:` and `Tasks:` or after `Tasks:`.
  Prefer between managers and tasks because services explain manager-owned
  runtime health before listing every task row.
- Keep each service row short enough for ordinary terminals.

Typed ops surface:

- `SystemStatusSnapshot` should add a `services` field only if callers need
  structured access. If added, keep a default empty list if compatibility
  requires it.

## 9. Rollout and Rollback

Rollout:

- Ship the read model as an additive status feature.
- Do not require managers to restart for the first version if existing
  `task_spawned`, task-log, and TID mapping rows are enough.
- If registry hint fields are added later, make readers tolerate their absence
  so old managers and new status readers coexist.

Rollback:

- Reverting the status read model must not affect manager service launch or
  cleanup.
- Since the first version should be read-only and additive, rollback is a code
  revert of status/types/spec text only.
- If manager registry hint fields are added, old readers ignore them and new
  readers must tolerate absence.

One-way doors:

- None intended. Adding incompatible JSON field renames, changing queue names,
  or making registry hints mandatory would create a one-way door and requires a
  revised plan.

## 10. Review Plan

Independent review is required before implementation because this crosses
manager, status, queue-history, and CLI surfaces.

Preferred reviewer prompt:

```text
Read docs/plans/2026-05-11-internal-service-observability-plan.md plus
weft/core/manager.py, weft/commands/system.py, weft/core/manager_services.py,
and tests/commands/test_status.py. Do not implement. Look for evidence-ordering
bugs, status contract ambiguity, missing tests, and places where the plan could
accidentally create a second truth store. Could you implement this confidently
and correctly if asked?
```

Reviewer should specifically check:

- Whether manager `task_spawned` is safe to use as launched evidence.
- Whether terminal child-local evidence always wins.
- Whether pending/reserved spawn queue inspection can remain read-only.
- Whether the output contract is sufficiently additive for ops/client callers.

## 11. Out of Scope

- Changing `weft queue list` default filtering.
- Changing internal service launch or restart logic.
- Changing TaskMonitor cleanup processors.
- Adding a new service manager or health daemon.
- Making manager registry service hints mandatory.
- Cleaning up old draft plans.
- Refactoring `weft/commands/system.py` beyond the narrow helper extraction
  needed for this read model.

# Manager Service Authority Boundary Hardening Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-3.2]; docs/specifications/02-TaskSpec.md [TS-1.4]; docs/specifications/03-Manager_Architecture.md [MA-1.1], [MA-1.6a]; docs/specifications/05-Message_Flow_and_State.md [MF-1], [MF-3], [MF-6]; docs/specifications/07-System_Invariants.md [MANAGER.15]-[MANAGER.16]
Superseded by: none

## 1. Goal

Close the fresh-eyes review findings from the manager service convergence
changes without weakening the deterministic reducer. Manager-owned singleton
evidence must be based on manager-authored provenance rather than
caller-owned metadata alone, duplicate force-kill must use PID authority tied
to the liveness proof that made the duplicate live, and the surrounding manager
code should be cleaned up enough that service identity and state-machine
ownership are easy to audit.

## 2. Source Documents

Source specs:

- [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md)
  [CC-3.2]: runtime handles and host PID control authority.
- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
  [TS-1.4]: `metadata` is caller-owned and must not be treated as system
  authority without an explicit internal boundary.
- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md)
  [MA-1.1], [MA-1.6a]: manager spawn consumption and manager-owned service
  supervision.
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-1], [MF-3], [MF-6]: queue-first spawn flow, task control evidence, and
  manager spawn semantics.
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [MANAGER.15]-[MANAGER.16]: singleton service evidence reduction and internal
  spawn convergence.

Related plans and how to read them:

- [`2026-05-10-control-and-service-convergence-state-machine-plan.md`](./2026-05-10-control-and-service-convergence-state-machine-plan.md)
  is completed and defines the reducer/convergence direction. Preserve it.
- [`2026-05-08-deterministic-manager-service-reconciler-plan.md`](./2026-05-08-deterministic-manager-service-reconciler-plan.md)
  is completed and defines the pure reducer shape. Do not move queue or
  process side effects into `weft/core/manager_services.py`.
- [`2026-05-09-internal-spawn-priority-queue-plan.md`](./2026-05-09-internal-spawn-priority-queue-plan.md)
  is completed and defines `weft.spawn.internal` strict priority.
- [`2026-05-09-service-liveness-and-health-convergence-plan.md`](./2026-05-09-service-liveness-and-health-convergence-plan.md)
  is broader draft context only. Do not use this slice to take on heartbeat
  endpoint supersession, task-status health UX, or pruning.
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md),
  [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md),
  and [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)
  define the planning, hardening, and review requirements for this risky
  execution-path change.

## 3. Context And Key Files

Files to modify:

- `weft/_constants.py` if the reserved metadata keys need one canonical set.
- `weft/core/spawn_requests.py` for public spawn-request metadata sanitization.
- `weft/commands/submission.py` only if public client preparation needs to
  expose or test the sanitization boundary directly.
- `weft/core/manager.py` for service evidence trust, duplicate PID authority,
  service-key helper cleanup, and dead helper removal.
- `weft/core/manager_services.py` only if tests prove the pure reducer needs a
  more explicit candidate field. Prefer keeping authority decisions in
  `Manager`.
- `tests/commands/test_run.py` for public submission sanitization regressions.
- `tests/core/test_manager.py` for manager service spoofing, pending-spawn
  spoofing, autostart spoofing, and duplicate PID authority regressions.
- `tests/core/test_manager_services.py` only if reducer inputs change.
- `tests/cli/test_cli_serve.py` only if end-to-end service convergence
  assertions need to cover a newly visible contract.
- `docs/specifications/01-Core_Components.md`
- `docs/specifications/02-TaskSpec.md`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/lessons.md` if implementation confirms a durable lesson that is not
  already covered by the 2026-05-10 convergence lessons.

Read first:

- `weft/core/spawn_requests.py::submit_spawn_request`: public submissions
  currently strip reserved internal runtime class metadata and reserved
  endpoint metadata, but not service-supervision keys.
- `weft/core/manager.py`:
  `_trusted_internal_service_key`, `_trusted_service_key_from_metadata`,
  `_spawn_request_service_key`, `_pending_service_keys`,
  `_observed_service_candidates_by_key`, `_service_candidate_from_task_log`,
  `_service_candidate_pid`, `_terminate_duplicate_service_candidates`,
  `_kill_duplicate_service_pid`, `_tracked_service_candidate`,
  `_child_matches_service`, `_cleanup_children`, and
  `_service_has_owner_evidence`.
- `weft/core/manager_services.py`: `ServiceCandidate`,
  `ManagedServiceEvidence`, `summarize_service_candidates`, and
  `reduce_managed_service_state`.
- `weft/core/tasks/base.py`: runtime handle publication, PING/PONG handling,
  task state reporting, and endpoint claim handling.
- `weft/ext.py`: `RunnerHandle`, `scoped_host_pids()`, and control authority
  fields.
- `tests/core/test_manager.py` around existing task-monitor spoofing,
  duplicate-service, pending-spawn, and autostart restart tests.
- `tests/commands/test_run.py` around public submission stripping tests.

Current structure:

- Public spawn submission goes through `submit_spawn_request()`, which resolves
  a TaskSpec, writes an envelope to `weft.spawn.requests`, and strips only a
  subset of internal runtime metadata when `allow_internal_runtime=False`.
- Manager-owned built-in singleton service requests are built by
  `_build_heartbeat_spawn_payload()` and `_build_task_monitor_spawn_payload()`.
  They carry manager-owned service metadata in the child TaskSpec plus an
  internal runtime class in the spawn envelope. `_build_child_spec()` injects
  that runtime class into the resolved child TaskSpec before launch.
- Autostart service requests are manager-authored, but they currently rely on
  TaskSpec metadata fields such as `_weft_service_key`, `_weft_service_lifecycle`,
  `autostart_source`, and `autostart`.
- Service evidence is reduced by `weft/core/manager_services.py`, but evidence
  collection and side effects live in `Manager`.
- Duplicate service termination currently sends `CONTROL_KILL` and may force
  kill a PID copied from `child_pid` or `taskspec.state.pid`, even when live
  proof came from a different surface such as PONG.
- Service-key derivation for tracked children is duplicated in several manager
  methods, and `_service_has_owner_evidence()` is unused while mutating state
  despite reading like a predicate.

Comprehension checks before editing:

1. Which fields in a public TaskSpec are caller-owned metadata, and which
   manager-authored envelope fields are not exposed through ordinary public
   submission?
2. Which evidence source made a service candidate live: tracked child,
   pending spawn, manager `task_spawned` row, runtime handle, or PONG?
3. For that same evidence source, what termination side effects are
   authorized: control message only, force-kill of a tracked child process, or
   force-kill of scoped runtime-handle host PIDs?
4. Why must terminal proof for a TID still win over live-looking PID, runtime
   handle, or PONG evidence?

## 4. Invariants And Constraints

- Preserve the existing durable spine:
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log`.
- Do not introduce a lease table, backend-specific lock, second service
  launcher, new durable queue, or new dependency.
- Preserve the pure reducer. `weft/core/manager_services.py` should continue
  to choose transitions from explicit evidence only; `Manager` owns queue
  reads, PINGs, process inspection, enqueue, and termination side effects.
- `TaskSpec.metadata` remains caller-owned. Manager-owned singleton authority
  must require manager-authored provenance, not just user-writable metadata
  values.
- Public submissions may keep arbitrary application metadata, but reserved
  Weft service-supervision keys must be stripped or rejected on public
  submission paths when `allow_internal_runtime=False`.
- Do not strip generic user metadata keys such as `role` or `internal` merely
  because they are suspicious. They are not authority without reserved Weft
  keys and manager-owned runtime provenance.
- Raw queue writes remain administrative power. This plan hardens public Weft
  submission surfaces and manager interpretation; it does not try to defend
  against arbitrary direct database mutation.
- Pending service spawn evidence blocks duplicate launch only when the pending
  request is manager-authored or manager-internal according to the tightened
  authority rules.
- A matched PONG proves a current task owns its control queues. It does not
  authorize force-killing an unrelated PID copied from an old task-log row.
- Force-kill by host PID is allowed only when the PID identity is tied to the
  same evidence that proved liveness: local `ManagedChild` process ownership
  or scoped runtime-handle host PID evidence. Otherwise duplicate handling may
  send control only.
- Keep queue-history reads generator-based with `iter_queue_json_entries()`.
- Preserve dispatch ownership gates. Only positive `self` ownership may launch
  or restart manager-owned services.
- Preserve terminal proof priority. Terminal task-log status or terminal
  control envelope for a TID beats live evidence for that same TID.
- No public CLI shape change.
- Backward compatibility: old already-written manager-owned service rows in a
  running development context should not create duplicate storms. If the final
  authority model needs a new marker, readers must either tolerate the current
  manager-authored shapes for one transition window or degrade old ambiguous
  evidence to bounded uncertainty rather than immediate restart.
- Error-path priority: corrupt or untrusted service evidence must not crash the
  manager. It should be ignored or treated as uncertain. A failed best-effort
  duplicate force-kill must not rewrite the reducer decision.

## 5. Authority Model

The implementation should make these authority classes explicit in code, even
if it keeps them as local helper functions rather than new public types:

- `tracked_child`: authoritative because the current manager owns a
  `ManagedChild` record and can inspect the process object directly.
- `manager_internal_pending`: authoritative pending evidence for built-in
  services when the spawn envelope carries the internal runtime class and the
  service key is one of the desired internal singleton keys.
- `manager_autostart_pending`: authoritative pending evidence for autostart
  only when the spawn payload contains manager-owned service metadata that
  public submission strips and the key matches a currently desired manifest.
- `manager_task_spawned`: live pre-initialization evidence from a
  manager-authored `task_spawned` row. It may prove live service ownership but
  should not by itself authorize raw PID force-kill unless PID identity is
  otherwise current and scoped.
- `runtime_handle_live`: live evidence from a `RunnerHandle` whose scoped host
  process evidence is live. Force-kill may target only the scoped host PIDs
  from that handle when the handle authority permits host control.
- `control_pong_live`: live evidence from a keyed PONG. It authorizes
  `CONTROL_KILL` on the matched task control channel, not force-kill of a PID
  copied from stale metadata.
- `caller_metadata_only`: never authoritative for manager-owned singleton
  ownership. It may remain visible in task status, but service reconciliation
  must ignore it.

## 6. Tasks

1. Add red tests for public metadata spoofing.
   - Outcome: a public task or public pending spawn cannot claim heartbeat,
     TaskMonitor, or autostart singleton ownership by setting metadata alone.
   - Files to touch:
     - `tests/core/test_manager.py`
     - `tests/commands/test_run.py`
   - Tests:
     - Extend the existing spoof test so metadata includes the full tempting
       built-in shape: `_weft_service_key`, `internal=True`,
       `role="task_monitor"`, and any runtime-looking fields that public
       submission currently allows.
     - Add a pending-spawn regression where a public `weft.spawn.requests`
       payload with spoofed service metadata does not appear in
       `_pending_service_keys()` and does not block `_tick_task_monitor()`.
     - Add an autostart spoof regression where a public task-log row with
       `autostart=True`, `autostart_source=<desired manifest key>`, and
       service metadata does not suppress the real manifest launch.
     - Extend `test_enqueue_taskspec_strips_reserved_internal_runtime_metadata_from_public_submission`
       or add a sibling test proving public submission strips reserved service
       supervision keys.
   - Reuse:
     - Existing `manager_setup`, real broker queues, and
       `submit_spawn_request()` tests.
   - Do not:
     - Use a mock queue for pending-spawn behavior.
     - Test only the weak service-key-only spoof that already exists.
   - Stop if:
     - The test can only be made to fail by mocking manager internals instead
       of using real broker-backed queue/log evidence.
   - Done when:
     - The new tests fail on the current implementation for the intended
       authority reason.

2. Centralize public reserved metadata stripping.
   - Outcome: public spawn submission removes reserved service-supervision
     authority fields before any manager can interpret them as service
     evidence.
   - Files to touch:
     - `weft/_constants.py` if a shared key tuple is needed.
     - `weft/core/spawn_requests.py`
     - `tests/commands/test_run.py`
   - Required approach:
     - Add a small helper near `submit_spawn_request()` or a constants-backed
       helper that removes public uses of `_weft_service_key`,
       `_weft_service_lifecycle`, internal runtime task class, reserved
       internal endpoint names, and Weft-owned autostart authority fields such
       as `autostart_source` and `autostart` when
       `allow_internal_runtime=False`.
     - Keep public metadata otherwise intact. Do not strip `role`, `internal`,
       tags, owner IDs, or application keys that are not reserved Weft
       authority fields.
     - Keep `allow_internal_runtime=True` for manager and pipeline internal
       paths that already opt into internal runtime envelopes.
   - Tests:
     - Assert stripped keys are absent from the queued spawn payload.
     - Assert ordinary user metadata survives unchanged.
     - Assert internal submissions still preserve the manager-owned envelope
       fields they need.
   - Stop if:
     - The implementation needs TaskSpec schema changes to reject metadata
       globally. This slice should canonicalize at submission boundaries, not
       make metadata non-caller-owned.
   - Done when:
     - Public submission cannot produce service-authority metadata through the
       normal CLI/client path.

3. Split service evidence trust by source.
   - Outcome: manager service reconciliation accepts service keys only from
     evidence sources that are authoritative for that source.
   - Files to touch:
     - `weft/core/manager.py`
     - `tests/core/test_manager.py`
   - Required approach:
     - Replace the single metadata-only trust path with source-specific
       helpers, for example `_trusted_pending_service_key(...)` and
       `_trusted_task_log_service_key(...)`, or an equivalent small local
       structure.
     - Built-in internal service pending evidence must require the internal
       runtime class from the manager-owned envelope. Metadata with
       `internal=True` and `role=...` is not enough.
     - Built-in lifecycle/task-log evidence must require a manager-injected
       internal runtime class matching the expected service.
     - Autostart pending and log evidence must require the reserved service
       metadata shape after the public submission stripping from task 2, and
       must match the currently desired manifest key.
     - Ambiguous old evidence should be ignored or treated as uncertainty; it
       must not become unqualified live ownership.
   - Tests:
     - The red tests from task 1 turn green.
     - Existing manager-owned heartbeat, TaskMonitor, and autostart tests stay
       green.
     - Add one positive test for each accepted authority class so future
       cleanups do not over-tighten and break real services.
   - Stop if:
     - The code starts depending on wall-clock timing to decide whether a
       spoof is trusted.
     - The implementation weakens terminal proof priority to make an authority
       test pass.
   - Done when:
     - Public metadata-only candidates are not live, not pending, and not
       canonical singleton owners.

4. Tie duplicate force-kill to PID authority.
   - Outcome: duplicate service convergence can still signal all
     non-canonical live owners, but raw host PID force-kill is limited to
     current scoped authority.
   - Files to touch:
     - `weft/core/manager.py`
     - `tests/core/test_manager.py`
   - Required approach:
     - Stop copying `taskspec.state.pid` into force-kill metadata for
       candidates whose liveness was proved by PONG or stale task-log state.
     - For tracked children, keep using the local `ManagedChild.process.pid`.
     - For runtime-handle live evidence, derive force-kill targets from the
       live scoped host PIDs on the `RunnerHandle`, not from task metadata.
     - For manager `task_spawned` rows from other managers, send control when
       control queues are available. Do not force-kill the logged `child_pid`
       unless the implementation also records and verifies scoped process
       identity strongly enough to avoid PID reuse.
     - Make the candidate metadata name reflect authority, for example
       `force_kill_pids` or `pid_authority`, so future code cannot confuse a
       display PID with a kill target.
   - Tests:
     - A duplicate candidate live only by PONG with a spoofed or stale
       `state.pid` receives `CONTROL_KILL` but does not call
       `kill_process_tree()` for that PID.
     - A duplicate tracked child still receives control and force-kill.
     - A duplicate live by scoped runtime handle force-kills only the handle's
       scoped host PID targets.
   - Stop if:
     - The change starts treating any live PID from task-log metadata as
       process identity without create-time or runtime-handle proof.
   - Done when:
     - The manager never force-kills a PID unless the same evidence source that
       proved the candidate live also authorizes that PID.

5. Apply the P3 manager cleanup.
   - Outcome: the manager service code is easier to audit without changing the
     reducer contract.
   - Files to touch:
     - `weft/core/manager.py`
     - `tests/core/test_manager.py` only if helper behavior needs direct
       coverage.
   - Required approach:
     - Add one helper for deriving a `ManagedChild` service key, for example
       `_service_key_for_child(child)`, and use it from `_cleanup_children()`,
       `_tracked_service_candidate()`, and `_child_matches_service()`.
     - Remove `_service_has_owner_evidence()` unless a real call site exists.
       If a real call site is discovered, rename it to reflect that it mutates
       service state.
     - Keep edits local. Do not split `Manager` into new modules in this
       slice.
   - Tests:
     - Existing service cleanup and duplicate tests should cover behavior.
       Add direct tests only if the helper introduces a non-obvious branch.
   - Stop if:
     - Cleanup starts changing reducer decisions or queue side effects.
   - Done when:
     - There is one service-key derivation path for `ManagedChild`, no unused
       side-effecting predicate helper, and no behavior-only test regressions.

6. Update specs and durable lessons.
   - Outcome: the normative docs describe the authority boundary implemented
     by this slice.
   - Files to touch:
     - `docs/specifications/01-Core_Components.md`
     - `docs/specifications/02-TaskSpec.md`
     - `docs/specifications/03-Manager_Architecture.md`
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/07-System_Invariants.md`
     - `docs/lessons.md` if the implementation confirms a reusable lesson.
   - Required approach:
     - Clarify that reserved Weft service-supervision metadata is a
       manager-authored exception to ordinary caller-owned metadata, and public
       submission strips it.
     - Clarify what counts as manager-owned singleton evidence.
     - Clarify that host PID force-reap requires scoped/current PID authority,
       not a legacy `state.pid` copied from task metadata.
     - Keep plan backlinks synchronized.
   - Stop if:
     - Spec text starts describing a broader authentication model for raw queue
       writes. This plan does not add broker ACLs.
   - Done when:
     - Specs, plan, and code all name the same authority boundary.

## 7. Testing Plan

Use real broker-backed queues for queue and service evidence. Mock only process
tree killing, keyed PING/PONG return values, and runtime-handle liveness where
the operating-system or runner boundary is intentionally nondeterministic.

Targeted tests while implementing:

```bash
./.venv/bin/python -m pytest tests/commands/test_run.py -q
./.venv/bin/python -m pytest tests/core/test_manager.py -q
./.venv/bin/python -m pytest tests/core/test_manager_services.py -q
```

Backend-sensitive service verification:

```bash
./.venv/bin/python -m pytest tests/cli/test_cli_serve.py -q
PYTEST_ADDOPTS='-x --maxfail=1' PYTEST_XDIST_AUTO_NUM_WORKERS=17 WEFT_EAGER_FAILURE_TRACEBACK=1 uv run --extra dev --extra docker --extra django --extra macos-sandbox bin/pytest-pg --all
```

Final gates:

```bash
./.venv/bin/python -m ruff check weft tests extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/python -m mypy weft extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/python -m pytest
PYTEST_ADDOPTS='-x --maxfail=1' PYTEST_XDIST_AUTO_NUM_WORKERS=17 WEFT_EAGER_FAILURE_TRACEBACK=1 uv run --extra dev --extra docker --extra django --extra macos-sandbox bin/pytest-pg --all
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
git diff --check
```

Observable success:

- Public spawn requests cannot carry reserved service-supervision authority.
- Public metadata-only service rows do not block, replace, or kill
  manager-owned heartbeat, TaskMonitor, or autostart services.
- Manager-owned services still converge to exactly one live owner under sqlite
  and Postgres.
- Duplicate service convergence still terminates non-canonical live owners,
  but force-kill targets only PIDs with current scoped authority.

## 8. Rollout And Rollback

Rollout is code-only and backward-compatible with the existing queue names and
TaskSpec schema. The safest order is:

1. Land tests for public spoofing and PID authority.
2. Land public metadata stripping.
3. Land manager trust-source hardening.
4. Land duplicate PID authority hardening.
5. Land cleanup and docs.

Rollback is straightforward if the change stays within this plan:

- Reverting the hardening code restores previous manager behavior without
  queue migrations because no new durable queue or schema is introduced.
- Sanitized public submissions may have already removed reserved internal
  metadata from queued public work. That is acceptable because those fields
  were not valid public authority.
- If a new internal envelope marker is added, the reader must tolerate old
  current manager-authored service shapes or degrade them to uncertainty so a
  rollback does not strand singleton services.

There are no intended one-way doors. Introducing one requires stopping and
re-planning.

## 9. Independent Review Loop

Before implementation, ask an independent reviewer from a different available
agent family if possible:

> Read the plan at
> `docs/plans/2026-05-10-manager-service-authority-boundary-hardening-plan.md`.
> Carefully examine the plan, the relevant specs, and the current manager code.
> Look for errors, bad ideas, and latent ambiguities. Do not implement
> anything. Could you implement this confidently and correctly if asked?

Reviewer should read:

- This plan.
- `weft/core/spawn_requests.py`
- `weft/core/manager.py`
- `weft/core/manager_services.py`
- `tests/core/test_manager.py`
- `tests/commands/test_run.py`
- The source spec sections named in the metadata block.

After review, respond to each finding explicitly. Update the plan for accepted
findings before implementation. If review is not available, record that
limitation in the implementation handoff.

Run another review after task 4, because that is the point where authority,
liveness proof, and process termination behavior have all changed.

## 10. Out Of Scope

- No broker ACLs or authentication for direct queue/database writers.
- No new public CLI command or option.
- No new service launcher, lease store, durable queue, or backend-specific
  lock.
- No heartbeat endpoint supersession work beyond preserving current behavior.
- No pruning, task-status health UX, or lifecycle-monitor redesign.
- No broad `Manager` module split. The cleanup is limited to service-key
  derivation, dead helper removal, and source-specific trust helpers.

## 11. Fresh-Eyes Checklist

Before implementing, re-read this plan and confirm:

- Every authority decision says where the evidence came from.
- Every PID kill decision says why that PID is safe to kill.
- Public caller-owned metadata is not treated as manager-owned truth.
- Tests use real broker-backed queues for queue semantics.
- The reducer remains pure and deterministic.
- Specs and plan backlinks are updated in the same change as implementation.

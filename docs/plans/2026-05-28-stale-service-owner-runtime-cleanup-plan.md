# Stale Service Owner Runtime Cleanup Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.3], docs/specifications/03-Manager_Architecture.md [MA-1.4], [MA-1.6a], docs/specifications/05-Message_Flow_and_State.md [MF-5], docs/specifications/07-System_Invariants.md [MANAGER.3], [MANAGER.15], [OBS.13]
Superseded by: none

## Goal

Close the cleanup gap for old Manager, TaskMonitor, and Heartbeat service-owner
task rows that are no longer live but remain nonterminal in the Monitor store.
The target behavior is:

- current active service owners stay protected
- stale nonterminal service-owner Monitor collations are summarized, disposed,
  cleaned, and retired without waiting for the generic 7-day stale-open window
- stale standard task-local control queues for those dead service owners are
  deleted through the existing `task_local.terminal_runtime` cleanup policy
- old manager/task-monitor/heartbeat keyed PING/PONG probe residue stops
  accumulating where the probe owner can prove exact ownership of the probe
  rows
- no sixth cleanup policy is introduced

This plan directly addresses the current observed problem:

- Monitor shows 7 running/nonterminal service collations when only 3 current
  services are expected.
- Old manager control queues contain stale probe residue, including
  `T1779555792605769728.ctrl_out` with old keyed PONG rows and
  `T1779555792605769728.ctrl_in` with a stale keyed PING.
- The old rows are not active owners. They are artifacts of a manager/service
  restart path where task-owned cleanup did not run.

## Non-Goals

- Do not change public `weft task ping` semantics. Public and diagnostic PING
  commands may still preserve matched PONG evidence unless a separate plan
  changes that contract.
- Do not delete `weft.spawn.requests`, `weft.manager.outbox`,
  `weft.manager.ctrl_in`, `weft.manager.ctrl_out`, `weft.state.*`, or
  `weft.log.tasks` from this path.
- Do not add a generic sweeper, plugin framework, cleanup scheduler, or new
  abstraction layer.
- Do not weaken exact-delete rules. Every destructive queue operation must be
  backed by exact selected message IDs or an existing helper that performs exact
  deletion internally.
- Do not treat Wazuh host DNS behavior as part of this change. Wazuh capture
  and report tasks now run inside Docker. This plan is about Weft monitor and
  manager cleanup only.
- Do not change TID format, queue naming rules, TaskSpec schema, or SimpleBroker
  semantics.

## Required Reading

Read these before editing code:

- `AGENTS.md`, especially sections 1.1, 3, 4, 5.1, and 5.2.
- `docs/agent-context/decision-hierarchy.md`.
- `docs/agent-context/principles.md`.
- `docs/agent-context/engineering-principles.md`.
- `docs/agent-context/runbooks/hardening-plans.md`.
- `docs/agent-context/runbooks/testing-patterns.md`.
- `docs/specifications/00-Overview_and_Architecture.md`.
- `docs/specifications/01-Core_Components.md` [CC-2.3].
- `docs/specifications/03-Manager_Architecture.md` [MA-1.4] and [MA-1.6a].
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5].
- `docs/specifications/07-System_Invariants.md` [MANAGER.3],
  [MANAGER.15], and [OBS.13].
- `docs/plans/2026-05-19-monitor-terminal-retirement-and-runtime-queue-cleanup-plan.md`.
- `docs/plans/2026-05-22-monitor-policy-modules-and-dead-task-cleanup-plan.md`.
- `docs/plans/2026-05-25-monitor-dead-task-catchup-convergence-plan.md`.
- `docs/plans/2026-05-26-monitor-five-cleanup-policy-consolidation-plan.md`.
- `docs/plans/2026-05-27-service-collation-reporting-plan.md`.

Use the repo-managed toolchain:

```bash
. ./.envrc
./.venv/bin/python -m pytest ...
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft tests
```

Do not assume global `pytest`, `mypy`, or `ruff` are installed.

## Current Structure

`BaseTask` owns task-local control cleanup on normal task exit:

- `weft/core/tasks/base.py::BaseTask.cleanup`
- `weft/core/tasks/base.py::_cleanup_standard_control_queues_on_exit`
- `weft/core/tasks/base.py::_handle_control_message`
- `weft/core/tasks/base.py::_ack_control_message`
- `weft/core/tasks/base.py::_send_control_response`

That path handles success, failure, timeout, STOP, KILL, and other clean unwind
paths. It does not run if the process dies hard or an older release left stale
state behind.

`send_keyed_ping_probe` is intentionally non-consuming today:

- `weft/core/control_probe.py::send_keyed_ping_probe`

It writes a keyed PING and peeks for matching PONG evidence. It does not
consume the matched PONG. Do not globally change that helper for this plan,
because command evidence tests depend on the non-consuming behavior.

The Manager owns live service registry state and manager/service probe loops:

- `weft/core/manager.py::Manager._register_manager`
- `weft/core/manager.py::Manager._unregister_manager`
- `weft/core/manager.py::Manager._advance_manager_pong_probe`
- `weft/core/manager.py::Manager._advance_service_pong_probe`
- `weft/core/manager.py::Manager._update_manager_registry_snapshot`
- `weft/core/service_convergence.py`
- `weft/core/manager_runtime.py`

The Monitor store owns collation readiness and disposition state:

- `weft/core/monitor/store.py`
- `weft/core/monitor/sql.py`
- `weft/core/monitor/collation.py`

The Monitor reactor owns summary emission, runtime cleanup scheduling, and PONG
policy progress:

- `weft/core/monitor/task_monitor.py::_emit_monitor_store_summaries`
- `weft/core/monitor/task_monitor.py::_run_terminal_control_cleanup_slice`
- `weft/core/monitor/task_monitor.py::_run_dead_task_cleanup_slice`
- `weft/core/monitor/task_monitor.py::_delete_terminal_control_queues`

Runtime cleanup policy lives here:

- `weft/core/monitor/policies/runtime_control.py`
- `weft/core/monitor/policies/dead_task.py`
- `weft/core/monitor/policies/api.py`

Important current behavior:

- `select_runtime_dead_task_cleanup_candidates` skips TIDs that have a Monitor
  row, because terminal or reserved cleanup owns Monitor-backed families.
- `select_terminal_control_cleanup_ready_tasks` currently expects terminal or
  disposed Monitor-store proof before task-local control cleanup runs.
- `standard_task_control_queue_names` currently rejects manager rows. That
  protects live manager control rows and global manager controls, but it also
  blocks cleanup of proven stale manager task-local controls.
- Generic stale-open summary waits on
  `WEFT_TASK_MONITOR_STALE_OPEN_FAMILY_SECONDS_DEFAULT`, currently 7 days. Old
  service rows whose `reporting_interval` is nonnumeric fall into that slow
  path.

## Root Cause

The failure is a policy handoff gap.

Task-owned self-cleanup works only when the task exits through `cleanup()`.
Old manager/service owners that die hard, get superseded, or come from older
cleanup behavior can leave `T{tid}.ctrl_in` and `T{tid}.ctrl_out` rows behind.

Dead-TID cleanup can see those task-local queue names, but it deliberately
backs off when a Monitor row exists. That is correct for ordinary tasks:
Monitor-backed families should be summarized and cleaned through terminal or
reserved cleanup. The gap is that stale nonterminal service-owner rows do not
become disposed soon enough, so terminal runtime cleanup never gets authority
to delete their standard control queues.

Manager rows add one more safety gate. The current runtime policy rejects
manager task-local control cleanup completely. That was defensible when
"manager row" and "active manager control" were treated as the same risk class.
The stronger position now is narrower: active or ambiguous manager controls
must stay protected, while a Monitor-proved stale service-owner manager row may
clean only its standard task-local `T{tid}.ctrl_in` and `T{tid}.ctrl_out`
queues.

## Design Summary

Add an explicit stale service-owner path inside existing Monitor lifecycle and
runtime cleanup machinery.

The path should be:

1. Monitor identifies old open service-owner collations whose owner TID is not
   a live active service owner and is not live by runtime evidence.
2. Monitor emits a service summary and disposition for those rows with a clear
   reason such as `stale_service_owner` or `superseded_service_owner`.
3. Existing `task_local.terminal_runtime` cleanup becomes allowed to delete
   standard task-local control queues for those disposed service-owner rows.
4. Existing monitor-store lifecycle retirement removes the child rows and
   parent collation rows after summary, disposition, raw deletion, and
   task-local cleanup proof complete.
5. Manager-owned internal PING probes perform best-effort exact cleanup of probe
   rows they created or matched, so future residue is bounded.

Do this with small helpers in the existing modules. Do not create a broad
"service cleanup framework". The implementation should be deliberately boring:
find candidates, prove inactive, mark disposed, use existing cleanup workers.

## Invariants and Constraints

- Active service owners are never cleanup candidates. A TID present in the
  selected active service-owner view or active runtime-TID set is live enough to
  skip.
- Ambiguous service owners are never cleanup candidates. If the code cannot
  prove stale from registry/runtime evidence, leave the row alone and let the
  existing generic stale-open path handle it later.
- Nonterminal service rows are uncertain, not terminal. The new path may
  dispose stale service-owner rows for Monitor cleanup, but it must not rewrite
  task lifecycle truth as `failed`, `completed`, or `killed`.
- Manager controls are protected by default. Only the new stale-service-owner
  proof may clean standard manager task-local `T{tid}.ctrl_in` and
  `T{tid}.ctrl_out`.
- Global manager queues and spawn queues are never selected.
- The monitor still reports exactly five top-level cleanup policy identities.
  This work belongs under `monitor_store.lifecycle` and
  `task_local.terminal_runtime`.
- Cleanup workers must stay bounded by the current batch, queue delete, and
  runtime cleanup slice limits.
- Queue deletion must use exact message IDs or the current exact-delete helper.
  No broad SQL or queue deletes based only on a queue name.
- Store scans that read append-only queues such as `weft.state.services` must
  use generator-based helpers. Do not use a fixed small `peek_many` window for
  service-owner truth.
- Tests must use real broker-backed queues for queue semantics. Do not mock
  SimpleBroker, queue deletion, lifecycle rows, or Monitor store state unless a
  test is purely about a small pure function.
- Keep DRY and YAGNI. Reuse current service classification, registry reduction,
  runtime cleanup, and exact-delete helpers. Do not add a parallel cleanup
  path.

## Bite-Sized Tasks

### 1. Write the failing policy tests first

Outcome: the current manager/service cleanup gap is captured before behavior
changes.

Files to touch:

- `tests/core/monitor/policies/test_runtime_control.py`
- `tests/tasks/test_task_monitor.py`

Read first:

- `weft/core/monitor/policies/runtime_control.py`
- `weft/core/monitor/policies/dead_task.py`
- existing tests named around `terminal_runtime`, `dead_task_cleanup`,
  `manager_control`, `service`, and `reserved_probe`

Add tests:

- Ordinary terminal manager cleanup still rejects manager controls through the
  existing standard terminal plan. This protects the old invariant.
- A new stale service-owner cleanup plan accepts a disposed inactive manager
  service-owner record and returns only `T{tid}.ctrl_in` and
  `T{tid}.ctrl_out`.
- The stale service-owner cleanup plan rejects active TIDs and ambiguous rows.
- The stale service-owner cleanup plan rejects nonstandard queues such as
  `weft.spawn.requests`, `weft.manager.outbox`, `weft.manager.ctrl_in`,
  `weft.manager.ctrl_out`, and `T{tid}.internal_reserved`.

Test design:

- Pure policy tests may instantiate local dataclasses and records.
- Do not mock the policy module. The point is to lock the selection contract.
- The first run should fail because there is no stale service-owner plan yet.

Command:

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/core/monitor/policies/test_runtime_control.py -k "manager_control or stale_service_owner" -q
```

Stop if:

- The test requires changing public manager control queue names.
- The easiest implementation looks like deleting all manager task-local queues
  by role alone. That is too broad.

### 2. Add a narrow stale service-owner runtime cleanup policy helper

Outcome: runtime cleanup can distinguish "ordinary manager row" from "disposed,
inactive service-owner row with standard task-local controls".

Files to touch:

- `weft/core/monitor/policies/runtime_control.py`
- `tests/core/monitor/policies/test_runtime_control.py`

Implementation guidance:

- Keep `standard_task_control_queue_names` conservative for ordinary terminal
  cleanup. Do not remove its manager rejection as a broad change.
- Add a new small helper for stale service-owner cleanup. Suggested name:
  `stale_service_owner_control_queue_names` or
  `service_owner_control_queue_cleanup_plan`.
- The helper should accept the current record plus explicit proof inputs,
  such as `active_tids` or a boolean produced by the caller. Do not let a pure
  policy helper read live queues.
- For stale manager rows, return only standard task-local
  `T{tid}.ctrl_in` and `T{tid}.ctrl_out`.
- For stale built-in service rows, return only standard task-local
  `T{tid}.ctrl_in` and `T{tid}.ctrl_out` in this path. Do not add `inbox`,
  `outbox`, or `reserved` cleanup in this task. If `T{tid}.inbox` residue
  later proves material for stale service owners, handle that in a separate
  plan with its own safety proof.
- Reject custom/nonstandard control queue names even if the row has a service
  role.
- Preserve existing reason counts and skipped-nonstandard diagnostics.

Tests:

- The red tests from task 1 should pass.
- Add one regression proving ordinary terminal manager cleanup still returns
  skipped/nonstandard rather than a delete plan.

Command:

```bash
./.venv/bin/python -m pytest tests/core/monitor/policies/test_runtime_control.py -k "manager_control or stale_service_owner" -q
```

Stop if:

- The implementation needs a new cleanup policy identity.
- The helper starts deciding service liveness by reading queues directly.

### 3. Add Monitor-store candidate selection for old open service-owner rows

Outcome: the Monitor can ask for bounded candidate rows that are old enough to
consider for stale-service-owner disposition, without waiting for the generic
7-day stale-open window.

Files to touch:

- `weft/core/monitor/sql.py`
- `weft/core/monitor/store.py`
- `tests/core/test_monitor_store.py` or the existing Monitor-store test file
  that covers summary readiness

Read first:

- `weft/core/monitor/store.py::list_summary_ready_tasks`
- `weft/core/monitor/sql.py::select_summary_ready_open_tasks`
- `weft/core/monitor/collation.py` service classification helpers from the
  service collation reporting work, if already implemented

Implementation guidance:

- Add a bounded store method for old open service-owner candidates. Suggested
  name: `list_stale_service_owner_candidates`.
- The query should select nonterminal, undisposed rows whose last message ID is
  older than the chosen stale-service-owner cutoff.
- Prefer filtering service-owner classification in Python using the same
  classification helper used for service summary reporting. If the helper is in
  `store.py`, reuse it. If it is in `collation.py`, import it at module top
  without creating a cycle.
- Use the monitor batch size or existing store write batch size as the limit.
- Do not select ordinary user-task rows.
- Do not mark or delete anything in this task. This task only returns
  candidates.

Cutoff choice:

- Use the task-log retention clock as the first conservative cutoff:
  `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS`.
- Do not add a new config value unless implementation proves the retention
  clock is wrong. A new knob is extra surface area and likely YAGNI.
- This means stale service-owner cleanup is faster than the 7-day open-family
  fallback but still old enough to avoid deleting recent uncertain service
  evidence.

Tests:

- A nonterminal old manager row is returned.
- A nonterminal old heartbeat or task-monitor service row is returned.
- A recent service row is not returned.
- An old ordinary user-task row is not returned.
- A terminal row is not returned by this method; terminal readiness already has
  its own path.

Command:

```bash
./.venv/bin/python -m pytest tests/core/test_monitor_store.py -k "stale_service_owner or summary_ready" -q
```

Stop if:

- The implementation needs a second Monitor-store table.
- The query can only be made to work by reading all parent rows without a limit.

### 4. Prove service-owner staleness in the Monitor reactor

Outcome: TaskMonitor marks stale open service-owner rows summarized and disposed
only when current runtime evidence says their owner is not live.

Files to touch:

- `weft/core/monitor/task_monitor.py`
- `weft/core/monitor/store.py` only if the store needs a small marking helper
- `tests/tasks/test_task_monitor.py`

Read first:

- `weft/core/monitor/task_monitor.py::_emit_monitor_store_summaries`
- `weft/core/monitor/task_monitor.py::_active_runtime_tids`
- `weft/helpers.py` service-owner parsing helpers
- `weft/core/service_convergence.py` canonical service-owner reduction helpers
- `weft/core/manager_runtime.py` manager registry reader behavior

Implementation guidance:

- Add a private TaskMonitor helper that collects stale service-owner candidates
  from the store and filters them against current live evidence.
- Use generator-based service-registry reads. Do not use fixed-window
  `peek_many` for `weft.state.services`.
- Reuse the existing active runtime TID set. If a candidate TID is in that set,
  skip it.
- Reuse canonical service-owner reduction helpers. A candidate is stale only if
  its owner TID is not the active owner for the same service key and no runtime
  handle proves it live.
- Treat missing or malformed service metadata as ambiguous. Skip it.
- Mark eligible rows with:
  - `summary_emitted_at_ns`
  - a disposition reason such as `stale_service_owner`
  - cached summary output using the service-summary classification shape
- Do not set lifecycle status to a terminal task status.
- Keep this work under `monitor_store.lifecycle` diagnostics.

Tests:

- Create a real Monitor store with one old nonterminal manager row, current
  service registry pointing to a different live manager, and no live runtime
  evidence for the old TID. Run the smallest helper or monitor cycle that emits
  summaries. Assert the old row is summarized and disposed as stale service
  owner.
- Create a current active manager row with matching service registry evidence.
  Assert it is not summarized or disposed.
- Create an old service row with incomplete service metadata. Assert it is not
  disposed by this new path.
- Create one old ordinary user-task row. Assert it is not disposed by this new
  path.

Test design:

- Use real `Queue` instances and the local Monitor store. Do not mock the
  service registry queue or queue listing.
- It is acceptable to call a private helper if the helper is the exact behavior
  under review and a full monitor loop would add timing noise.
- Do not sleep for correctness. Use explicit timestamps or broker timestamps
  seeded by test rows.

Command:

```bash
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py -k "stale_service_owner or service_owner" -q
```

Stop if:

- The implementation starts treating all old nonterminal rows as stale.
- The proof depends only on absence from `weft.state.services` without checking
  runtime live evidence.

### 5. Wire stale service-owner rows into terminal runtime cleanup

Outcome: disposed stale service-owner rows get their standard task-local control
queues cleaned by the existing runtime-cleanup worker.

Files to touch:

- `weft/core/monitor/task_monitor.py`
- `weft/core/monitor/policies/runtime_control.py`
- `weft/core/monitor/store.py` only if readiness needs a narrow query addition
- `tests/tasks/test_task_monitor.py`

Read first:

- `weft/core/monitor/task_monitor.py::_run_terminal_control_cleanup_slice`
- `weft/core/monitor/task_monitor.py::_delete_terminal_control_queues`
- `weft/core/monitor/store.py::list_terminal_control_cleanup_ready_tasks`
- `weft/core/monitor/sql.py::select_terminal_control_cleanup_ready_tasks`

Implementation guidance:

- Extend terminal runtime cleanup readiness to include disposed
  `stale_service_owner` rows.
- Use the new stale service-owner cleanup plan only for rows with that
  disposition reason and inactive proof from the monitor reactor.
- Preserve active-TID skip checks immediately before deletion. A row that became
  active again between disposition and cleanup must be skipped.
- Do not let dead-TID cleanup mark Monitor families. Keep the existing
  ownership split: Monitor rows are cleaned by terminal/reserved cleanup.
- Mark `task_control_deleted_at_ns` only after the delete worker has either:
  - deleted the selected rows from existing queues, or
  - proved the selected queues are absent or empty
- If a selected queue has rows and exact deletion returns zero for those rows,
  record a warning/error and leave the row pending. Do not mark cleanup done.

Tests:

- Seed a disposed stale manager row plus real
  `T{tid}.ctrl_in` and `T{tid}.ctrl_out` rows. Run terminal runtime cleanup.
  Assert both queues are empty and the store row has `task_control_deleted_at_ns`.
- Seed `weft.spawn.requests`, `weft.manager.outbox`, and
  `T{tid}.internal_reserved` alongside the stale manager row. Also seed
  `T{tid}.inbox`. Assert those rows survive.
- Seed a current active manager TID with matching registry evidence and rows in
  `T{tid}.ctrl_in` and `T{tid}.ctrl_out`. Assert terminal runtime cleanup skips
  deletion.
- Keep or update the existing
  `test_task_monitor_terminal_disposition_does_not_delete_manager_control_queue`
  so it still proves ordinary manager disposition is protected unless the
  stale-service-owner reason and inactive proof are present.

Command:

```bash
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py -k "terminal_control or manager_control or stale_service_owner" -q
```

Stop if:

- The simplest patch deletes manager queues in
  `standard_task_control_queue_names` without stale-service-owner proof.
- The delete path bypasses the runtime-cleanup worker and deletes directly from
  the monitor reactor.

### 6. Add exact self-cleanup for internal manager/service PING probes

Outcome: future internal manager/service liveness probes do not leave old probe
residue when the probe owner can safely identify the exact rows.

Files to touch:

- `weft/core/manager.py`
- possibly `weft/core/control_probe.py` only for an opt-in internal helper
- `tests/core/test_manager.py`
- existing command-evidence tests only if the implementation changes a shared
  helper signature

Read first:

- `weft/core/manager.py::_advance_manager_pong_probe`
- `weft/core/manager.py::_advance_service_pong_probe`
- `weft/core/control_probe.py::send_keyed_ping_probe`
- `tests/commands/test_task_evidence.py::test_ping_pong_ignores_unmatched_and_preserves_terminal_ctrl_out`

Implementation guidance:

- Do not change public `send_keyed_ping_probe` consumption behavior by default.
- If modifying `send_keyed_ping_probe`, add an explicit opt-in flag such as
  `cleanup_matched_probe_rows: bool = False` or create a new internal helper.
  Default must preserve current public behavior.
- When the Manager writes an internal PING, keep the exact message ID returned
  by the queue write.
- If the probe times out or is superseded, best-effort delete that exact PING
  from `ctrl_in`.
- If a matching keyed PONG is found for an internal probe, best-effort delete
  only that exact matching PONG from `ctrl_out`.
- Never delete unmatched PONGs, terminal output envelopes, STATUS responses, or
  rows without exact IDs.
- Cleanup failure must not change the liveness decision. It should be logged or
  counted, not used as proof that the target is stale.

Tests:

- Manager internal probe timeout deletes its exact PING from candidate
  `T{tid}.ctrl_in`.
- Manager internal probe with a matching PONG deletes only the matching PONG.
- Unmatched PONGs and terminal ctrl_out evidence survive.
- Existing public task evidence test still passes, proving the public ping path
  is non-consuming unless explicitly opted in.

Command:

```bash
./.venv/bin/python -m pytest tests/core/test_manager.py -k "pong_probe or ping_probe or manager_liveness" -q
./.venv/bin/python -m pytest tests/commands/test_task_evidence.py -k "ping_pong" -q
```

Stop if:

- This task starts driving broad cleanup of historical probe rows. Historical
  cleanup belongs to the Monitor stale-service-owner path.
- The implementation needs sleeps to prove correctness in tests.

### 7. Update specs and implementation mapping

Outcome: the authoritative docs describe the new cleanup authority and do not
contradict implementation behavior.

Files to touch:

- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/01-Core_Components.md` if implementation mapping around
  monitor cleanup changes materially

Required spec updates:

- [MF-5] must say the Monitor may classify old nonterminal service-owner rows
  as stale service-owner rows after bounded registry/runtime proof, summarize
  and dispose them, then run standard task-local control cleanup through
  `task_local.terminal_runtime`.
- [MF-5] must narrow "manager control rows" protection. The intended meaning is
  active or ambiguous manager controls, global manager controls, and manager
  controls without stale-service-owner proof. It does not mean forever-retain
  standard `T{tid}.ctrl_in` and `T{tid}.ctrl_out` rows for a manager TID proved
  stale by current service-owner evidence.
- [MANAGER.15] must preserve the rule that nonterminal service rows are
  uncertain, not terminal, while allowing Monitor disposition for cleanup after
  the stale evidence window.
- [OBS.13] must keep service summaries as operational output only and confirm
  they do not become lifecycle truth or result authority.
- Add this plan as a related plan/backlink in each touched spec's related plan
  section.

Tests:

- Plan metadata test must pass after README/spec backlink edits.

Command:

```bash
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
```

Stop if:

- The spec update implies a materially broader cleanup authority than the code
  tasks above. Do not let docs drift into a different design.

### 8. Runtime observation after deploy

Outcome: operational evidence proves the rules and monitor converged.

Use the existing hourly monitor report plus one manual sample after deployment.
Expected signals:

- Persistent service PINGs succeed for Manager, TaskMonitor, and Heartbeat.
- Current active service collations settle at the expected active count, usually
  3 for Manager, TaskMonitor, and Heartbeat.
- Old nonterminal service-owner collations move from running/nonterminal to
  summarized/disposed/retired.
- `T1779555792605769728.ctrl_in` and
  `T1779555792605769728.ctrl_out` stale probe rows drain if they still exist in
  the target environment.
- `control_cleanup_pending` returns to 0 after the bounded cleanup cycle.
- `reserved_probe_pending` remains 0 or drains normally.
- `task_local.terminal_runtime` and `monitor_store.lifecycle` policy progress
  show bounded work and base reached, not repeated blocked errors.
- Host load may remain busy if cleanup workers are active, but Manager,
  TaskMonitor, and Heartbeat PING latency should remain healthy.

If any PING fails, manager CPU is high, or progress stalls, sample `py-spy`
using the existing ops runbook.

## Testing Plan

Use red-green TDD for each behavior:

1. Write a failing test that proves the current bug.
2. Implement the smallest code change.
3. Run the focused test.
4. Add the next behavior test.
5. Expand only after the focused path is green.

Do not over-mock. The meaningful behavior here is queue/store cleanup, so tests
must use real broker-backed queues and a real Monitor store.

Per-task commands are listed above. Final local gates:

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/core/monitor/policies/test_runtime_control.py -q
./.venv/bin/python -m pytest tests/core/test_monitor_store.py tests/tasks/test_task_monitor.py -q
./.venv/bin/python -m pytest tests/core/test_manager.py tests/commands/test_task_evidence.py -q
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
./.venv/bin/ruff check weft tests
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
```

If the touched code changes a broader monitor loop or manager convergence
contract, also run:

```bash
./.venv/bin/python -m pytest tests/core/test_manager.py tests/tasks/test_task_monitor.py -q
```

## Rollout and Rollback

This is rollout-sensitive because it deletes stale queue rows. The delete
targets are runtime control artifacts, not task results, but deletion is still
a one-way operation.

Rollout:

- Land tests and spec updates with the code.
- Deploy during a monitoring window where the hourly probe can observe
  `control_cleanup_pending`, stale service-owner counts, and PING latency.
- Do not manually prune the stale manager queues before the first post-deploy
  monitor cycle unless needed for emergency recovery. The first cycle is useful
  proof that the policy works.

Rollback:

- Reverting the code stops future stale-service-owner cleanup.
- Reverting cannot restore already-deleted stale PING/PONG rows. That is
  acceptable only because the rows are runtime control artifacts and the policy
  requires stale-service-owner proof before deletion.
- If active service controls are deleted, treat it as a correctness bug. Stop
  the monitor, roll back, and inspect the service-owner proof path before
  restarting cleanup.

## Independent Review Loop

This plan needs independent review before implementation because it changes
cleanup authority for manager/service controls.

Reviewer stance:

> Read `docs/plans/2026-05-28-stale-service-owner-runtime-cleanup-plan.md`,
> then inspect the referenced code and specs. Do not implement anything. Look
> for errors, unsafe cleanup authority, missing tests, hidden timing
> assumptions, or places where the plan drifts into a materially different
> design. Could a skilled but zero-context engineer implement this correctly?

Reviewer should read:

- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]
- `docs/specifications/07-System_Invariants.md` [MANAGER.15], [OBS.13]
- `weft/core/monitor/task_monitor.py`
- `weft/core/monitor/policies/runtime_control.py`
- `weft/core/monitor/store.py`
- `weft/core/manager.py`
- tests named in this plan

The plan author must handle each review point explicitly before implementation:
fix the plan, state why the current plan is still correct, or mark the point
out of scope with a reason.

## Fresh-Eyes Self-Review

First pass findings and fixes applied while writing this plan:

- Ambiguity: "clean manager controls" was too broad. Fixed by limiting cleanup
  to standard task-local `T{tid}.ctrl_in` and `T{tid}.ctrl_out` after stale
  service-owner proof. Global manager queues and spawn queues stay out of scope.
- Ambiguity: the Monitor could have used generic dead-TID cleanup to mark
  Monitor families. Fixed by keeping the existing ownership split: Monitor rows
  go through summary/disposition plus terminal runtime cleanup.
- Bad direction risk: consuming all PONGs would break public diagnostic
  evidence semantics. Fixed by making PING/PONG cleanup opt-in for internal
  manager/service probes only.
- Bad direction risk: treating stale service-owner cleanup as full task-local
  cleanup would delete `T{tid}.inbox` without a specific need. Fixed by limiting
  this path to `ctrl_in` and `ctrl_out`.
- Timing risk: "old enough" needed a concrete clock. Fixed by using the
  existing task-log retention period first, not a new knob.
- Test risk: mock-heavy tests could pass while queue cleanup is broken. Fixed
  by requiring real broker-backed queue/store tests for cleanup behavior.

Second pass findings:

- The plan still has one deliberate ambiguity: exactly where the service
  classification helper lives depends on whether the
  `2026-05-27-service-collation-reporting-plan.md` work has landed first. This
  is acceptable because the task names the owner rule: reuse the existing
  classification helper if present; otherwise keep the helper in Monitor store
  or collation code and avoid a new broad abstraction.
- No task requires a materially different direction from the discussed fix.
  The plan stays focused on self-cleanup from manager/service probes and
  Monitor rule adjustments for stale nonterminal service-owner collations.

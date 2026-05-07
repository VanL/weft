# Runtime State Pruning Plan

Status: completed
Source specs: see Source Documents below
Superseded by: none

Parent plan:
[`docs/plans/2026-05-06-lifecycle-reconciliation-architecture-plan.md`](./2026-05-06-lifecycle-reconciliation-architecture-plan.md)
Release slice: Release 5

## 1. Goal

Add an explicit, operator-facing runtime-state pruning command for Weft's
runtime-only `weft.state.*` queues.

This release removes stale runtime bookkeeping records after conservative
live/recent safety checks. It does not prune task-local queues, task logs,
outboxes, control queues, inboxes, reserved queues, spawn requests, manager
control queues, archive files, checkpoints, or domain data.

The first implementation must default to dry-run. Active pruning requires an
explicit `--apply` flag and must delete exact message IDs only.

## 2. Source Documents

Source specs:

- [`docs/specifications/00-Quick_Reference.md`](../specifications/00-Quick_Reference.md)
  "Queue Names", "Operational Files"
- [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md)
  [CC-2.2], [CC-2.4], [CC-2.4.1]
- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md)
  manager registry ownership and liveness
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-3.1], [MF-5], "Queue Management Patterns", "Cleanup Boundary"
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [STATE.1], [STATE.2], [OBS.1], [OBS.2], [OBS.3], [OBS.6], [OBS.11],
  [OBS.13], [MANAGER.3]
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
  [CLI-6]
- [`docs/specifications/12-Pipeline_Composition_and_UX.md`](../specifications/12-Pipeline_Composition_and_UX.md)
  pipeline runtime state, if `weft.state.pipelines` support is implemented

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

Related plans:

- [`docs/plans/2026-05-06-lifecycle-reconciliation-architecture-plan.md`](./2026-05-06-lifecycle-reconciliation-architecture-plan.md)
  defines Release 5 as runtime-state pruning.
- [`docs/plans/2026-05-07-lifecycle-monitor-archive-sink-plan.md`](./2026-05-07-lifecycle-monitor-archive-sink-plan.md)
  shipped the non-destructive monitor/archive substrate. Phase 5 must not make
  status depend on monitor archives or checkpoints.
- [`docs/plans/2026-05-06-task-evidence-reconciliation-model-plan.md`](./2026-05-06-task-evidence-reconciliation-model-plan.md)
  defines shared evidence classification. Runtime pruning should reuse that
  interpretation where owner liveness or terminal status matters.
- [`docs/plans/2026-05-06-status-coherence-and-stale-pid-liveness-plan.md`](./2026-05-06-status-coherence-and-stale-pid-liveness-plan.md)
  tightened stale PID handling. Runtime pruning must use those liveness rules,
  not raw PID existence alone.

Spec delta required before implementation is done:

- Update [MF-5] and "Queue Management Patterns" to describe explicit
  runtime-state pruning and to keep it separate from task-local cleanup.
- Update [OBS.13] or nearby invariant text to say runtime pruning output and
  deletion reports are operational, not lifecycle truth.
- Update [CLI-6] with the new `weft system prune` command if
  that CLI surface lands.
- Update `00-Quick_Reference.md` if the command should appear in the CLI table.
- Add backlinks from touched spec sections to this plan.

## 3. Non-Goals

Do not implement these in Release 5:

- Any deletion from `weft.log.tasks`.
- Any deletion from task-local `T{tid}.*` queues: `inbox`, `reserved`,
  `outbox`, `ctrl_in`, or `ctrl_out`.
- Any deletion from `weft.spawn.requests`, `weft.manager.ctrl_in`,
  `weft.manager.ctrl_out`, or `weft.manager.outbox`.
- Any task-local reaper or broker retention policy. That is Release 6.
- Any archive requirement before deletion of runtime-only rows. Runtime rows
  are operational soft state and excluded from dump/load, but the command must
  still emit a prune report.
- Any new lifecycle state.
- Any hidden background daemon, manager-owned reaper, autostart service, or
  automatic pruning inside `weft status`.
- Any dependency of `weft status`, `weft task status`, `weft result`, or
  manager startup on prune-command checkpoints or reports.
- Any backend-specific SQL shortcut for deletion.
- Any broad `weft.state.*` queue deletion by queue name alone.

If implementation starts needing one of these, stop. It has drifted into
Release 6 or a different lifecycle-service design.

## 4. Current Context

Runtime-only queues currently include:

- `weft.state.tid_mappings`
  - Producer: `weft/core/tasks/base.py` `_register_tid_mapping()`;
    `weft/core/manager.py` adds `role="manager"` for managers.
  - Consumers: `weft/commands/tasks.py`, `weft/commands/system.py`,
    `weft/core/manager_runtime.py`, `weft/core/endpoints.py`, and status/result
    helpers.
  - Record shape includes `short`, `full`, `runner`, `runtime_handle`, `name`,
    `role`, `started`, `hostname`, and optional live activity fields.

- `weft.state.managers`
  - Producer: `weft/core/manager.py` `_register_manager()` and
    `_unregister_manager()`.
  - Consumers: `weft/core/manager_runtime.py`, `weft/commands/manager.py`,
    `weft/commands/system.py`, and CLI run/bootstrap paths.
  - Some opportunistic stale pruning already exists in
    `weft/core/manager_runtime.py` and `weft/core/manager.py`.

- `weft.state.streaming`
  - Producer/cleanup owner: `weft/core/tasks/base.py`,
    `weft/core/tasks/consumer.py`, and interactive/session code through
    `_begin_streaming_session()` and `_end_streaming_session()`.
  - Consumer: `weft/commands/result.py` `_active_streaming_queues()`.
  - Records are live stream ownership hints. They must clear before a
    persistent task returns to waiting, but stale records can survive crashes.

- `weft.state.endpoints`
  - Producer/cleanup owner: `weft/core/tasks/base.py`
    `register_endpoint_name()` and `unregister_endpoint_name()`.
  - Consumer: `weft/core/endpoints.py` `list_resolved_endpoints()` and
    `resolve_endpoint()`.
  - Existing endpoint resolution already opportunistically deletes stale active
    endpoint rows. Phase 5 should centralize the same proof rules in an
    explicit command, not create a second interpretation.

- `weft.state.pipelines`
  - Producer/consumer: pipeline runtime and pipeline status code. Read
    `docs/specifications/12-Pipeline_Composition_and_UX.md` and
    `weft/core/pipelines.py` before touching this queue.
  - First implementation may include conservative report-only candidates for
    unknown pipeline row shapes. Active deletion is allowed only for records
    with explicit terminal or stale-owner proof.

Dump/load already treats `weft.state.*` as runtime-only:

- `weft/commands/_dump_support.py` skips queues whose name starts with
  `WEFT_STATE_QUEUE_PREFIX`.
- `weft/commands/_load_support.py` skips runtime queues on import.

This release should align pruning with that runtime-only contract.

## 5. Architecture Decision

Implement an explicit foreground system command:

```bash
weft system prune
```

The command should have three layers:

1. Candidate builders in a command-layer module:
   - owner: `weft/commands/runtime_prune.py`
   - responsibility: read runtime queues, build exact-message candidates,
     classify why each candidate is safe, and apply deletion only when
     requested
   - boundary: no task-local queues, no manager service, no lifecycle authority

2. Thin CLI adapter:
   - owner: `weft/cli/app.py`
   - responsibility: parse options, call command-layer function, print report,
     and exit with the returned code

3. Shared helper extraction only if forced:
   - prefer small helpers inside `weft/commands/runtime_prune.py`
   - extract to `weft/core/` only if another runtime producer must reuse the
     exact same non-CLI logic and a test proves duplication would be harmful
   - do not import `weft.commands.*` from core runtime modules

Why a foreground command:

- Release 5 is cleanup of runtime-only soft state, not a daemon.
- Operators need dry-run reports and exact candidate counts before deletion.
- A background service would add scheduling, ownership, and rollback questions
  unrelated to the current slice.

Counterargument:

- Existing manager and endpoint readers already opportunistically prune stale
  rows. That is useful but not enough for ops cleanup because it is incidental,
  queue-specific, and not reportable. Phase 5 should add an explicit, auditable
  command while avoiding broad behavior changes in those live paths.

## 6. Target CLI Contract

Add:

```bash
weft system prune
```

Options:

- `--context PATH`
  - Reuse existing system command context patterns.
- `--dry-run / --apply`
  - Default: `--dry-run`.
  - `--dry-run` reports candidates and deletes nothing.
  - `--apply` deletes only candidates selected by the same code path as
    dry-run.
- `--queue NAME`
  - Repeatable filter.
  - Allowed names: `tid-mappings`, `managers`, `streaming`, `endpoints`,
    `pipelines`, `all`.
  - Default: `all`.
  - Reject unknown values. Do not silently ignore typos.
- `--min-age SECONDS`
  - Default: 3600 seconds.
  - A runtime row younger than this is never deleted.
  - Tests may pass `--min-age 0` for deterministic fixtures.
- `--keep-recent-per-key N`
  - Default: 1.
  - Applies to grouped queues such as TID mappings, managers, endpoints, and
    pipelines. Keep at least the newest N rows per key even when older rows are
    candidates.
  - Must be `>= 1`.
- `--limit N`
  - Optional maximum candidates to apply in one run.
  - In dry-run, it limits reported candidate records but still include total
    candidate counts if feasible.
- `--json`
  - Emit one JSON object summary to stdout.
  - Without `--json`, emit a concise human summary plus candidate lines.
- `--report PATH`
  - Optional JSONL report path.
  - Write one record per candidate plus a final summary record.
  - In `--apply`, write report records before deleting, then append applied
    result fields after deletion if implementation can do so cleanly. If not,
    write a final summary with counts after deletion.

Exit codes:

- `0`: command completed successfully, including dry-run with candidates
- `1`: invalid arguments, broker read failure, report write failure, or delete
  failure

Do not add a `--force` flag in the first implementation. `--apply` plus
candidate proof rules are the safety boundary.

## 7. Candidate Report Contract

All JSON report records include:

```json
{
  "schema_version": 1,
  "record_type": "runtime_prune_candidate",
  "run_id": "2026-05-07T12:00:00.000000Z:pid-12345",
  "emitted_at": 1770000000000000000
}
```

Candidate records include:

```json
{
  "schema_version": 1,
  "record_type": "runtime_prune_candidate",
  "run_id": "2026-05-07T12:00:00.000000Z:pid-12345",
  "queue": "weft.state.tid_mappings",
  "message_id": 1770000000000000000,
  "key": "1778084345905438720",
  "classification": "superseded_tid_mapping",
  "reason": "older_than_min_age_and_not_latest_for_tid",
  "age_seconds": 7200.5,
  "dry_run": true,
  "applied": false,
  "error": null
}
```

Summary records include:

```json
{
  "schema_version": 1,
  "record_type": "runtime_prune_completed",
  "run_id": "2026-05-07T12:00:00.000000Z:pid-12345",
  "dry_run": true,
  "queues_scanned": ["weft.state.tid_mappings"],
  "records_scanned": 100,
  "candidates": 10,
  "deleted": 0,
  "failed": 0,
  "classification_counts": {
    "superseded_tid_mapping": 10
  }
}
```

Allowed first-slice classifications:

- `superseded_tid_mapping`
- `stale_manager_registry`
- `superseded_manager_registry`
- `stale_streaming_session`
- `stale_endpoint_record`
- `superseded_endpoint_record`
- `stale_pipeline_record`
- `unsupported_pipeline_record_shape`

`unsupported_pipeline_record_shape` is report-only. It must never be deleted.

Do not include full raw queue payloads in default human output. JSON/JSONL
reports may include a small `payload_excerpt` with non-sensitive fields such as
`tid`, `short`, `full`, `name`, `status`, `runner`, `role`, and `queue`. Avoid
dumping whole runtime handles unless a test proves operators need them.

## 8. Safety Rules By Queue

Common rules for every queue:

- Read with generator-based helpers such as `iter_queue_json_entries()`.
- Build candidates with exact `message_id` values from the generator timestamp.
- Never delete a row newer than `--min-age`.
- Never delete the newest `--keep-recent-per-key` rows for a grouping key.
- Never delete malformed or unknown-shape rows on the first implementation.
  Report them as warnings instead.
- Before applying deletion, re-read enough current state to avoid deleting a
  row that became protected during the run. For the first implementation,
  rebuild candidates immediately before apply rather than reusing stale
  dry-run candidates.

### `weft.state.tid_mappings`

Grouping key:

- `full` when present and a non-empty string
- otherwise do not delete

Delete candidates:

- older mapping rows for the same `full` TID when at least
  `--keep-recent-per-key` newer rows for that same `full` remain
- rows older than `--min-age`

Protection rules:

- keep newest mapping per `full`
- keep all recent rows
- keep malformed rows
- keep latest mapping for any task whose current evidence is active/nonterminal
  if implementation can cheaply identify it; this should be true when using
  shared task evidence helpers

Reason:

- short-TID resolution and task control need a current full mapping, but they
  do not need unlimited historical duplicate mappings.

### `weft.state.managers`

Grouping key:

- `tid`

Delete candidates:

- stale active manager rows whose runtime handle or PID liveness fails under
  existing manager-runtime liveness helpers and whose row is older than
  `--min-age`
- superseded older rows for the same manager `tid` when newer rows are kept

Protection rules:

- keep active canonical live manager records
- keep newest row per manager `tid`
- keep recent rows
- keep unknown-shape rows
- do not delete manager records merely because a non-canonical manager exists;
  use the existing canonical-manager interpretation and liveness helpers

Required reuse:

- Prefer existing helpers in `weft/core/manager_runtime.py` for manager record
  normalization and liveness. If they are private and not directly usable,
  extract a narrow helper with tests instead of copying stale PID logic.

### `weft.state.streaming`

Grouping key:

- `session_id` when present
- otherwise `tid`

Delete candidates:

- streaming records whose owner task has terminal task-log or terminal
  task-local evidence
- streaming records whose owner has no live runtime proof and the row is older
  than `--min-age`
- superseded duplicate records for the same `session_id`

Protection rules:

- keep recent rows
- keep records whose owner task is active or ambiguous
- keep malformed rows
- keep unknown owner rows unless older than `--min-age` and the command can
  prove no live mapping/endpoint/control surface references them

Reason:

- stale streaming markers can hide completed outboxes from `weft result --all`,
  so pruning terminal-owner markers is useful. Ambiguous live streams must not
  be deleted.

### `weft.state.endpoints`

Grouping key:

- `(name, tid)` for superseded records
- `name` for canonical live-owner grouping

Delete candidates:

- stale endpoint records whose owner task is terminal or lacks live owner proof
  under `weft/core/endpoints.py` rules
- older duplicate records for the same `(name, tid)` when a newer row is kept

Protection rules:

- keep canonical live endpoint owner candidates
- keep recent rows
- keep unknown-shape rows
- do not delete duplicate live endpoint claimants just because they are not
  canonical; endpoint resolution intentionally exposes `live_candidates`

Required reuse:

- Prefer extracting endpoint stale-owner classification from
  `weft/core/endpoints.py` rather than reimplementing it.

### `weft.state.pipelines`

Grouping key:

- pipeline TID if the record shape includes one
- otherwise do not delete

Delete candidates:

- explicit terminal pipeline records older than `--min-age` when newer rows for
  the same pipeline are kept
- ownerless stale records only if pipeline specs/tests prove the shape and live
  owner check

Protection rules:

- keep active pipeline registry rows
- keep recent rows
- keep unknown-shape rows
- report `unsupported_pipeline_record_shape` instead of deleting if the
  implementation cannot prove the shape from specs and tests

First implementation guidance:

- It is acceptable to implement pipelines as report-only warnings if the
  pipeline state contract is not clear enough after reading
  `docs/specifications/12-Pipeline_Composition_and_UX.md` and current tests.
  Do not guess.

## 9. Files To Touch

Implementation files:

- `weft/commands/runtime_prune.py`
  - New command-layer module.
  - Own dataclasses, candidate builders, dry-run/apply logic, report rendering,
    and public command function.
  - May import command-layer evidence helpers and core runtime helper functions.
  - Must not import CLI.
- `weft/cli/app.py`
  - Add `@system_app.command("prune")`.
  - Keep wrapper thin.
- `weft/_constants.py`
  - Add only constants needed to avoid repeated literals:
    - runtime prune schema version
    - command/report classification strings if uppercase policy requires it
    - default min age and keep count if treated as policy constants
- `weft/core/manager_runtime.py`
  - Touch only to expose a narrow manager-record normalization/liveness helper
    if command tests prove copying logic would be harmful.
- `weft/core/endpoints.py`
  - Touch only to expose a narrow endpoint stale-owner helper if command tests
    prove copying logic would be harmful.
- `weft/commands/__init__.py`
  - Export command function only if current package convention requires it.

Files to avoid unless a failing test proves otherwise:

- `weft/core/manager.py`
  - Do not make Manager own broad pruning.
- `weft/core/tasks/base.py`
  - Do not change runtime producers unless candidate tests expose malformed
    records produced by current code.
- `weft/commands/system.py`
  - Do not make status depend on pruning. Touch only if a shared helper needs
    to move out cleanly.
- `weft/commands/lifecycle_monitor.py`
  - Do not couple runtime pruning to lifecycle monitor checkpoints or archives.
- `weft/core/tasks/lifecycle_monitor.py`
  - Runtime pruning is a system command, not a lifecycle monitor behavior in
    this release.
- `weft/commands/queue.py`
  - Do not implement pruning as a queue passthrough command.

Test files:

- `tests/commands/test_runtime_prune.py`
  - Main behavior tests for candidate building, dry-run/apply, exact deletion,
    and safety rules.
- `tests/cli/test_cli_system.py`
  - Thin CLI coverage for command wiring and argument validation.
- Existing neighboring tests to run:
  - `tests/commands/test_task_commands.py`
  - `tests/commands/test_status.py`
  - `tests/commands/test_result.py`
  - `tests/commands/test_queue.py`
  - `tests/commands/test_manager_commands.py`
  - `tests/core/test_manager.py`
  - `tests/core/test_pipelines.py` if `weft.state.pipelines` active deletion
    is implemented

Docs:

- `docs/specifications/00-Quick_Reference.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/plans/README.md`
- `docs/lessons.md` only if implementation exposes a durable lesson not
  already captured

## 10. Read Before Editing

Required read order for implementer:

1. `AGENTS.md`
2. `docs/agent-context/README.md`
3. `docs/agent-context/decision-hierarchy.md`
4. `docs/agent-context/principles.md`
5. `docs/agent-context/engineering-principles.md`
6. `docs/agent-context/runbooks/runtime-and-context-patterns.md`
7. `docs/agent-context/runbooks/testing-patterns.md`
8. `docs/agent-context/runbooks/hardening-plans.md`
9. `docs/plans/2026-05-06-lifecycle-reconciliation-architecture-plan.md`
10. `docs/plans/2026-05-07-lifecycle-monitor-archive-sink-plan.md`
11. `docs/specifications/00-Quick_Reference.md`
12. `docs/specifications/05-Message_Flow_and_State.md` [MF-3.1], [MF-5],
    "Queue Management Patterns"
13. `docs/specifications/07-System_Invariants.md` [OBS.6], [OBS.13],
    [MANAGER.3]
14. `docs/specifications/10-CLI_Interface.md` [CLI-6]
15. `docs/specifications/12-Pipeline_Composition_and_UX.md` if pipeline
    pruning is implemented beyond report-only warnings

Code to read:

- `weft/_constants.py`
- `weft/helpers/__init__.py`, especially `iter_queue_json_entries()`
- `weft/context.py`, especially `build_context()` and `WeftContext.queue()`
- `weft/core/tasks/base.py`
  - `_register_tid_mapping()`
  - `_build_tid_mapping_payload()`
  - endpoint registration helpers
  - streaming session helpers
- `weft/core/manager.py`
  - `_register_manager()`
  - `_unregister_manager()`
  - manager registry stale pruning around active records
- `weft/core/manager_runtime.py`
  - `_snapshot_registry()`
  - manager liveness helpers
  - `_list_manager_records()`
- `weft/core/endpoints.py`
  - `_latest_task_statuses()`
  - `_latest_tid_mapping_entries()`
  - `_record_owner_is_live()`
  - `list_resolved_endpoints()`
- `weft/commands/tasks.py`
  - short/full TID resolution
  - `mapping_for_tid()`
  - control paths that rely on TID mappings
- `weft/commands/result.py`
  - `_active_streaming_queues()`
- `weft/commands/_dump_support.py`
- `weft/commands/_load_support.py`
- `weft/cli/app.py`, especially existing `system` subcommands
- `tests/commands/test_task_commands.py`
- `tests/commands/test_status.py`
- `tests/commands/test_queue.py`
- `tests/commands/test_manager_commands.py`
- `tests/core/test_manager.py`

Comprehension checks before coding:

- Which queues may this release delete from? Answer: only supported
  `weft.state.*` runtime-only queues, with exact message IDs.
- May this release delete from task-local `T{tid}.*` queues? Answer: no.
- May this release delete from `weft.log.tasks`? Answer: no.
- May dry-run and apply use different candidate logic? Answer: no.
- May status depend on prune reports? Answer: no.
- Is raw PID existence enough to mark a manager/task live? Answer: no. Use the
  existing liveness helpers and runtime-handle identity checks.
- Are malformed runtime rows safe to delete? Answer: no in the first
  implementation. Report them.
- Should tests mock `Queue.delete()` and call the feature done? Answer: no.
  Use real queues and assert message presence/absence.

## 11. Bite-Sized Implementation Tasks

### Task 0: Baseline And Scope Guard

Owner: implementing engineer.

Actions:

1. Source the repo environment:

   ```bash
   . ./.envrc
   ```

2. Inspect the worktree:

   ```bash
   git status --short
   ```

3. Run baseline neighboring tests before editing:

   ```bash
   ./.venv/bin/python -m pytest tests/commands/test_task_commands.py tests/commands/test_status.py tests/commands/test_result.py tests/commands/test_queue.py -q
   ```

4. If unrelated files are dirty, do not revert them. Keep edits scoped to
   runtime pruning files, tests, and docs.

Expected result:

- Baseline is known.
- Scope is explicit.

Stop and re-plan if:

- the implementation needs to touch task-local cleanup, lifecycle monitor
  archives, or Manager launch behavior.

### Task 1: Red Tests For TID Mapping Candidates

Owner: implementing engineer.

Files to create:

- `tests/commands/test_runtime_prune.py`

Test design:

- Use `build_context()` or `broker_env` with real queues.
- Write multiple JSON rows to `WEFT_TID_MAPPINGS_QUEUE` for the same `full`
  TID.
- Use deterministic old timestamps if the broker allows explicit message IDs;
  otherwise set `--min-age 0` and rely on generated message IDs.
- Run the command-layer dry-run function.

Assertions:

- older duplicate rows are reported as `superseded_tid_mapping`
- newest row for the same `full` TID is not a candidate
- malformed rows are not candidates
- dry-run deletes nothing
- `--apply` deletes only candidate message IDs and leaves protected rows

Do not:

- Mock `Queue`.
- Assert only candidate counts when exact message IDs can be checked.

Expected red failure:

- `weft.commands.runtime_prune` does not exist.

### Task 2: Implement Core Dataclasses And TID Mapping Builder

Owner: implementing engineer.

Files to touch:

- `weft/commands/runtime_prune.py`
- `weft/_constants.py` if constants are needed

Implementation:

- Define small dataclasses:
  - `RuntimePruneConfig`
  - `RuntimePruneCandidate`
  - `RuntimePruneResult`
  - `RuntimeQueueScanStats`
- Define constants or literals for supported queue groups.
- Implement a generator-based scanner for `weft.state.tid_mappings`.
- Implement dry-run candidate output.
- Implement apply deletion by exact `message_id`.
- Make dry-run and apply share the same candidate builder.
- Rebuild candidates immediately before apply to avoid stale dry-run state.

Run:

```bash
./.venv/bin/python -m pytest tests/commands/test_runtime_prune.py -q
```

Expected result:

- TID mapping tests pass.

### Task 3: Red Tests For Manager Registry Candidates

Owner: implementing engineer.

Files to touch:

- `tests/commands/test_runtime_prune.py`

Test cases:

1. Superseded manager row:
   - Write two stopped rows for the same manager TID.
   - Run dry-run with `--min-age 0`.
   - Assert only the older row is candidate
     `superseded_manager_registry`.

2. Stale active manager row:
   - Write an active manager row with a host-pid runtime handle whose PID is
     not live or whose PID identity does not match.
   - Run dry-run.
   - Assert candidate classification `stale_manager_registry`.

3. Live manager row protected:
   - Use a live current process PID and matching runtime-handle shape, or reuse
     an existing manager helper fixture if one exists.
   - Assert no candidate is emitted for the live active manager row.

4. Apply deletes exact stale row only:
   - Put stale and protected rows in the same queue.
   - Run apply.
   - Assert protected row remains.

Do not:

- Copy a fake liveness model into the test.
- Delete all manager rows for a TID.

### Task 4: Implement Manager Registry Builder

Owner: implementing engineer.

Files to touch:

- `weft/commands/runtime_prune.py`
- `weft/core/manager_runtime.py` only if necessary to expose a narrow helper

Implementation:

- Reuse existing manager record normalization and liveness semantics.
- Group by manager `tid`.
- Keep newest `--keep-recent-per-key` rows per `tid`.
- Protect active live canonical manager rows.
- Candidate older superseded rows as `superseded_manager_registry`.
- Candidate stale active rows as `stale_manager_registry` only after liveness
  proof fails and age threshold passes.

Run:

```bash
./.venv/bin/python -m pytest tests/commands/test_runtime_prune.py tests/commands/test_manager_commands.py -q
```

### Task 5: Red Tests For Streaming Session Candidates

Owner: implementing engineer.

Files to touch:

- `tests/commands/test_runtime_prune.py`

Test cases:

1. Terminal owner:
   - Write a streaming session row with `tid` and `queue`.
   - Write a terminal task-log event for that `tid`.
   - Run dry-run with `--min-age 0`.
   - Assert `stale_streaming_session` candidate.

2. Active owner protected:
   - Write a streaming session row.
   - Write task-log running evidence plus a live mapping if needed by the
     helper.
   - Assert no candidate.

3. Result-all behavior:
   - Put one stale streaming marker and one active marker.
   - Run apply.
   - Assert stale marker is gone and active marker remains.
   - Run or call the nearest `result --all` helper if practical to prove stale
     marker no longer hides completed output.

Do not:

- Treat every old streaming row as stale.

### Task 6: Implement Streaming Builder

Owner: implementing engineer.

Files to touch:

- `weft/commands/runtime_prune.py`

Implementation:

- Read `weft.state.streaming` with generator helpers.
- Use shared task evidence or current task-log status helpers to distinguish
  terminal owner from active/ambiguous owner.
- Candidate terminal-owner stale rows as `stale_streaming_session`.
- Keep recent, malformed, active, and ambiguous rows.

Run:

```bash
./.venv/bin/python -m pytest tests/commands/test_runtime_prune.py tests/commands/test_result.py -q
```

### Task 7: Red Tests For Endpoint Candidates

Owner: implementing engineer.

Files to touch:

- `tests/commands/test_runtime_prune.py`

Test cases:

1. Superseded endpoint row:
   - Write two endpoint rows for the same `(name, tid)`.
   - Assert older row is `superseded_endpoint_record`.

2. Stale owner:
   - Write active endpoint row for a terminal task.
   - Assert `stale_endpoint_record`.

3. Duplicate live claimants protected:
   - Write endpoint rows for the same `name` but two live TIDs.
   - Assert non-canonical live claimant is not deleted merely because another
     TID is canonical.

4. Existing endpoint command still works:
   - After apply, run `weft queue list --endpoints --json` or command helper.
   - Assert live endpoint remains resolvable.

Do not:

- Reimplement endpoint canonical ownership differently from
  `weft/core/endpoints.py`.

### Task 8: Implement Endpoint Builder

Owner: implementing engineer.

Files to touch:

- `weft/commands/runtime_prune.py`
- `weft/core/endpoints.py` only if necessary to expose a narrow helper

Implementation:

- Reuse endpoint record parsing and live-owner rules.
- Candidate older same `(name, tid)` rows as `superseded_endpoint_record`.
- Candidate terminal/dead-owner active rows as `stale_endpoint_record`.
- Preserve duplicate live claimants so endpoint diagnostics remain visible.

Run:

```bash
./.venv/bin/python -m pytest tests/commands/test_runtime_prune.py tests/commands/test_queue.py -q
```

### Task 9: Pipeline Runtime State Decision

Owner: implementing engineer.

Files to read:

- `docs/specifications/12-Pipeline_Composition_and_UX.md`
- `weft/core/pipelines.py`
- `weft/core/tasks/pipeline.py`
- `tests/core/test_pipelines.py`
- `tests/tasks/test_pipeline_runtime.py`

Decision:

- If pipeline record shape and live/terminal proof are clear, add tests and
  implement conservative `stale_pipeline_record` pruning.
- If not clear, implement report-only `unsupported_pipeline_record_shape`
  warnings and document why active deletion is deferred.

Required test if active pipeline deletion is implemented:

- terminal pipeline runtime row older than min age becomes a candidate
- active pipeline runtime row remains protected
- apply deletes exact terminal row only
- pipeline status tests still pass

Stop and re-plan if:

- pipeline pruning needs task-local queue inspection or pipeline task-local
  message deletion. That is Release 6 or a pipeline-specific plan.

### Task 10: Red Tests For CLI Wiring

Owner: implementing engineer.

Files to touch:

- `tests/cli/test_cli_system.py`

Test cases:

- `weft system prune --dry-run --json --min-age 0` exits `0`
  and emits parseable JSON.
- `weft system prune --apply --json --min-age 0 --queue tid-mappings`
  deletes only candidate rows from `weft.state.tid_mappings`.
- invalid `--queue nonsense` exits `1` with a clear error.
- `--keep-recent-per-key 0` exits `1`.
- default invocation is dry-run and deletes nothing.

Use existing CLI helpers. Do not shell out manually if `run_cli()` already
covers the Typer adapter path.

### Task 11: Implement CLI Command

Owner: implementing engineer.

Files to touch:

- `weft/cli/app.py`
- `weft/commands/runtime_prune.py`
- possibly `weft/commands/__init__.py`

Implementation:

- Add `@system_app.command("prune")`.
- Keep Typer wrapper thin:
  - parse options
  - call command-layer function
  - echo stdout/stderr
  - exit with returned code
- Do not call lifecycle monitor.
- Do not require a running manager.
- Ensure command works from a project root or with `--context PATH`.

### Task 12: Docs And Spec Updates

Owner: implementing engineer.

Files to touch:

- `docs/specifications/00-Quick_Reference.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/plans/README.md`

Required updates after code is green:

- Add command to CLI quick reference.
- State runtime pruning applies only to runtime-only `weft.state.*` rows.
- State task-local cleanup and task-log retention are not part of this release.
- State dry-run is default and `--apply` is required for deletion.
- Add this plan backlink in touched specs.
- Mark this plan completed only after implementation and verification land.

### Task 13: Full Verification

Owner: implementing engineer.

Run focused gates:

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/commands/test_runtime_prune.py -q
./.venv/bin/python -m pytest tests/cli/test_cli_system.py -q
./.venv/bin/python -m pytest tests/commands/test_task_commands.py tests/commands/test_status.py tests/commands/test_result.py tests/commands/test_queue.py -q
./.venv/bin/python -m pytest tests/commands/test_manager_commands.py tests/core/test_manager.py -q
```

If pipeline active deletion is implemented, also run:

```bash
./.venv/bin/python -m pytest tests/core/test_pipelines.py tests/tasks/test_pipeline_runtime.py -q
```

Run quality gates:

```bash
./.venv/bin/python -m mypy weft extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft tests
```

Recommended final broad gate:

```bash
./.venv/bin/python -m pytest -q
```

If any gate is skipped, document why and do not claim release completion until
it runs somewhere.

## 12. Ops Validation

After release and deploy, validate on ops.

1. Source environment:

   ```bash
   ssh ops
   cd ~/governance
   . .env
   ```

   If ops uses `.envrc`, source that instead.

2. Capture pre-run status and queue counts:

   ```bash
   /opt/venv/bin/weft status --json > /tmp/weft-status-before-runtime-prune.json
   /opt/venv/bin/weft queue list --json > /tmp/weft-queues-before-runtime-prune.json
   ```

3. Dry-run all runtime queues:

   ```bash
   /opt/venv/bin/weft system prune --dry-run --json > /tmp/weft-runtime-prune-dry-run.json
   ```

4. Confirm:

   - command exits `0`
   - candidates are only from `weft.state.*` queues
   - no task-local queues appear
   - active manager records are not candidates
   - recent mappings and live endpoint records are not candidates
   - candidate counts are plausible

5. Apply a narrow first prune, starting with TID mapping duplicates:

   ```bash
   /opt/venv/bin/weft system prune --apply --queue tid-mappings --json > /tmp/weft-runtime-prune-apply-tid-mappings.json
   ```

6. Capture post-run status and queue counts:

   ```bash
   /opt/venv/bin/weft status --json > /tmp/weft-status-after-runtime-prune.json
   /opt/venv/bin/weft queue list --json > /tmp/weft-queues-after-runtime-prune.json
   ```

7. Confirm:

   - active manager remains visible
   - short-TID lookup still works for recent and active tasks
   - `weft task status TID` works for sampled active and recent terminal tasks
   - `weft result --all` behavior is unchanged except stale streaming markers
     no longer hide outputs if streaming pruning was applied
   - no `weft.log.tasks` or task-local queue counts dropped because of this
     command

Ops failure signals:

- any non-`weft.state.*` queue is deleted from
- active manager disappears
- short TID resolution for recent tasks breaks
- status/result starts depending on prune reports
- pipeline runtime rows are deleted without clear terminal/stale proof

## 13. Test Design Rules

Good tests:

- Use real broker queues.
- Write representative runtime queue records.
- Assert exact message IDs are candidates.
- Assert dry-run preserves messages.
- Assert apply deletes only exact candidate messages.
- Assert active/recent/live records remain.
- Assert CLI output parses as JSON where promised.

Acceptable mocks:

- `monkeypatch` for deterministic `time.time_ns()` or PID-liveness helper
  results when building exact age/liveness cases.
- CLI runner fixtures already used in the repo.

Bad tests:

- Mocking `simplebroker.Queue` for normal candidate/apply behavior.
- Testing only counts and not exact message preservation/deletion.
- Sleeping to make records old.
- Using broad fake SQL tables.
- Asserting private method calls instead of queue-visible behavior.
- Combining runtime pruning with task-local cleanup tests.

## 14. Invariants

Must preserve:

- `weft.log.tasks` remains lifecycle truth.
- `weft.state.*` queues remain runtime-only and excluded from dump/load.
- Public status does not depend on prune reports.
- Public result behavior does not depend on prune reports.
- Manager remains dispatcher/supervisor, not a broad reaper.
- Dry-run is default.
- Apply deletes exact message IDs only.
- Active manager records are protected.
- Recent runtime rows are protected.
- Latest TID mapping per full TID is protected.
- Live endpoint records are protected.
- Active streaming session records are protected.
- Unknown/malformed records are protected in the first implementation.
- Task-local queues are untouched.
- Task-log queues are untouched.

## 15. Independent Review Checklist

Ask an independent reviewer to check:

- The command can delete only supported `weft.state.*` messages.
- Dry-run and apply use the same candidate rules.
- Candidate rules are conservative and explicit.
- Exact message IDs are used for deletion.
- Active manager and live endpoint records are protected.
- Short-TID resolution remains safe.
- Streaming markers are not deleted for active or ambiguous tasks.
- Pipeline pruning is report-only unless proof rules are clear.
- No manager-owned daemon or autostart behavior slipped in.
- No status/result dependency on prune reports slipped in.
- Tests use real queues for normal behavior.
- Specs describe only current behavior.

## 16. Fresh-Eyes Review Of This Plan

Review pass 1 found a scope trap: "runtime-state pruning" can sound like all
broker retention. The corrected plan says only supported `weft.state.*` queues
are in scope and explicitly excludes task-local queues and `weft.log.tasks`.

Review pass 2 found a safety trap: pruning TID mappings too aggressively would
break short-TID resolution and task control. The corrected plan keeps the
latest mapping per full TID, keeps recent rows, and requires real tests for
short/full lookup after apply.

Review pass 3 found a manager trap: existing code already prunes stale manager
records opportunistically. The corrected plan does not remove that behavior or
make Manager own broad cleanup. It adds an explicit reportable command and
allows only narrow helper extraction.

Review pass 4 found an endpoint trap: non-canonical endpoint claimants can be
valid live diagnostics. The corrected plan protects duplicate live claimants
and deletes only stale-owner or superseded same-owner records.

Review pass 5 found a pipeline ambiguity: pipeline runtime state may need more
domain context than the other runtime queues. The corrected plan allows
pipeline report-only warnings unless the implementation proves terminal/stale
rules with specs and tests.

Review pass 6 found a testing trap: mock-heavy tests would hide incorrect
delete semantics. The corrected plan requires real queues and exact message-ID
assertions for candidate/apply behavior.

The plan still matches Release 5. It prunes runtime-only soft state after
explicit dry-run proof and leaves task-local reaping and broker retention for
Release 6.

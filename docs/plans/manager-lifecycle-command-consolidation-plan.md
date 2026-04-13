# Manager Lifecycle Command Consolidation Plan

## Goal

Remove the remaining divergent manager lifecycle code paths after bootstrap
unification. `weft run`, `weft manager list|status|stop`, and `weft status`
should all read manager registry state through one shared control-plane helper,
use the same stale-record and canonical-manager rules, and reuse the same
graceful-stop / force-stop path where applicable. The CLI commands may still
format output differently, but they should no longer disagree about which
managers are live.

## Source Documents

Source specs:

- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md) [MA-1], [MA-3]
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md) [MF-7]
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md) [MANAGER.1], [MANAGER.3], [MANAGER.6], [MANAGER.7]
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md) (`weft status` and `worker` command sections)

Repo guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)

Relevant existing context:

- [`docs/plans/manager-bootstrap-unification-plan.md`](./manager-bootstrap-unification-plan.md)
- [`docs/plans/task-lifecycle-stop-drain-audit-plan.md`](./task-lifecycle-stop-drain-audit-plan.md)
- [`docs/lessons.md`](../lessons.md)

## Context and Key Files

Files to modify:

- `weft/commands/_manager_bootstrap.py`
- `weft/commands/run.py`
- `weft/commands/manager.py`
- `weft/commands/status.py`
- `tests/commands/test_run.py`
- `tests/commands/test_manager_commands.py`
- `tests/cli/test_cli_manager.py`
- `tests/cli/test_status.py`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/10-CLI_Interface.md`

Read first:

- `weft/commands/_manager_bootstrap.py` — shared bootstrap path and the current
  `run`-side manager stop helper.
- `weft/commands/manager.py` — still owns private registry replay, TID lookup,
  STOP dispatch, PID liveness checks, and force-stop fallback.
- `weft/commands/status.py` — still owns a separate `_collect_manager_records`
  registry replay path.
- `weft/core/manager.py` — manager-side STOP drain, leadership yield, idle
  timeout, and registry writes. This plan does not redesign those semantics.
- `tests/commands/test_manager_commands.py` — command-helper seams that will
  change once private worker lifecycle helpers disappear.
- `tests/cli/test_cli_manager.py` and `tests/cli/test_status.py` — operator
  surfaces that should agree after the consolidation.

Style and guidance:

- `AGENTS.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/testing-patterns.md`

Shared paths to reuse:

- `weft/commands/_manager_bootstrap.py` :: `_ensure_manager`, `_stop_manager`,
  `_snapshot_registry`, `_select_active_manager`
- `weft.helpers` :: `is_canonical_manager_record`, `iter_queue_json_entries`,
  `pid_is_live`, `terminate_process_tree`
- `weft.core.manager.Manager.process_once()` for manager-side drain and idle
  timeout semantics

Current structure:

- `weft run` and `weft manager start` already share canonical bootstrap through
  `weft/commands/_manager_bootstrap.py`.
- `weft manager stop` still uses a separate lifecycle path in
  `weft/commands/manager.py` for registry lookup, STOP writes, wait loops, PID
  checks, and force termination.
- `weft manager list|status` and `weft status` still replay
  `weft.state.managers` independently and therefore do not share the same
  stale-record pruning or liveness interpretation as bootstrap.
- Today only `_snapshot_registry()` prunes stale active canonical managers.
  Operator surfaces can still report a dead manager as `active`, and
  `weft manager stop` can wait on a stale entry that bootstrap would have
  discarded.

Comprehension checks before editing:

- Which helper currently deletes stale active manager records, and which
  commands bypass that helper today?
- Which module currently owns the `run` post-task manager stop path, and how is
  `manager stop` different from it?
- Which commands should continue to show non-canonical manager records for
  operator visibility, and which path should continue to consider only
  canonical `weft.spawn.requests` managers?
- What normalized record shape should shared lifecycle readers return
  (`timestamp` field name, `requests` backfill, and `ctrl_in` fallback), so
  `manager.py` and `status.py` do not drift again?

## Invariants and Constraints

- Keep one control-plane decision layer for manager registry interpretation.
  `run`, `worker`, and `status` may present different text or JSON shapes, but
  they must not compute live-manager truth differently.
- Keep one durable startup spine:
  CLI wrapper -> shared manager lifecycle helper -> `weft.manager_process` ->
  `Manager`.
- Keep one graceful-stop spine:
  CLI wrapper -> shared manager lifecycle helper -> STOP on manager control
  queue -> manager-side drain in `weft/core/manager.py`.
- Do not reimplement manager shutdown policy in CLI code. STOP drain,
  leadership yield, and idle timeout remain owned by `weft/core/manager.py`.
- Preserve TID format and immutability.
- Preserve registry payload compatibility. Existing keys such as `tid`, `pid`,
  `status`, `role`, `requests`, `ctrl_in`, `ctrl_out`, and `outbox` must stay
  readable by existing code.
- Shared lifecycle readers should return normalized records with `timestamp`
  rather than private `_timestamp`, and should backfill missing `requests` to
  `weft.spawn.requests` so old records and new records are shaped the same for
  command consumers.
- Preserve canonical-manager filtering for default-manager selection only.
  Explicit operator views and stop-by-TID should still work for non-canonical
  manager records unless a command explicitly says otherwise.
- When a control queue name is not present in the registry record, the fallback
  must be the canonical manager queue `T{tid}.ctrl_in`. Do not preserve or
  spread `worker.{tid}.ctrl_in` as an inferred default.
- Do not add a second manager record model or persistence layer. Prefer shared
  normalized dict helpers unless a concrete typing problem forces a small local
  wrapper.
- No public CLI shape change unless the current output is incorrect or
  ambiguous once stale records are pruned.
- No new dependency, no drive-by refactor, and no manager-core behavior change
  unless a real blocker in the current stop observation path requires it.
- Preserve current numeric manager TID behavior for this slice. The `W...`
  examples in the CLI spec are a separate spec/code mismatch and are out of
  scope here.

Out of scope:

- manager idle-timeout policy changes
- autostart manifest behavior
- specialized manager pools or multi-queue manager design
- task log aggregation or task-status output redesign
- broad CLI formatting cleanup unrelated to lifecycle correctness
- worker-TID prefix parsing or CLI aliasing

## Tasks

1. Broaden the shared helper boundary from bootstrap-only to manager lifecycle.
   - Outcome: one internal command-support module owns manager registry replay,
     lookup-by-TID, canonical active-manager selection, and manager stop/wait
     behavior.
   - Files to touch:
     - `weft/commands/_manager_bootstrap.py`
     - `weft/commands/run.py`
     - `weft/commands/manager.py`
     - `weft/commands/status.py`
   - Read first:
     - `weft/commands/_manager_bootstrap.py`
     - `weft/commands/manager.py`
     - `weft/commands/status.py`
   - Reuse:
     - existing `_ensure_manager()` and `_stop_manager()` entry points
     - existing liveness helpers in `weft.helpers`
   - Constraints:
     - command modules should import the shared helper; they should not call
       each other
     - keep output formatting local to `manager.py` and `status.py`
   - Stop if:
     - the change starts routing `manager list` through `cmd_status()` or vice
       versa
     - module renaming becomes the main diff instead of the lifecycle
       consolidation
   - Done when:
     - `manager.py` no longer owns private registry replay or stop-control loops
     - `status.py` no longer owns a separate manager-registry replay function

2. Centralize registry replay, stale pruning, and canonical filtering.
   - Outcome: all manager readers share one normalized registry view, with
     command-level flags controlling whether stopped or non-canonical records
     are shown.
   - Files to touch:
     - `weft/commands/_manager_bootstrap.py`
     - `weft/commands/manager.py`
     - `weft/commands/status.py`
     - `tests/commands/test_run.py`
     - `tests/cli/test_cli_manager.py`
     - `tests/cli/test_status.py`
   - Read first:
     - current `_snapshot_registry()` in `weft/commands/_manager_bootstrap.py`
     - current `_collect_manager_records()` in `weft/commands/status.py`
   - Required action:
     - add shared helpers for full snapshot, single-record lookup, and active
       canonical-manager selection
     - make stale active-record pruning part of that shared path rather than a
       bootstrap-only behavior
     - normalize public records to a single shape: no leaked `_timestamp`,
       explicit `timestamp`, and `requests` defaulted when absent
     - keep canonical filtering opt-in so operator views can still expose
       non-canonical managers while bootstrap stays strict
   - Tests:
     - add a regression proving `weft manager list --json` and `weft status --json`
       agree on manager liveness for at least one stale active record
     - add a regression proving stale active-record deletion is idempotent and
       does not delete stopped-history records
     - keep the canonical-manager selection regression in `test_run.py`
   - Stop if:
     - the implementation needs fixed-limit queue reads instead of the existing
       generator-based history replay
     - stale pruning starts deleting stopped-history records instead of only
       eliminating dead active-state confusion
   - Done when:
     - one helper path owns manager snapshot normalization
     - bootstrap and operator surfaces no longer disagree about a dead active
       manager

3. Centralize graceful stop and force-stop behavior.
   - Outcome: `weft run` post-task manager shutdown and `weft manager stop`
     share the same STOP send, wait, PID-liveness, and force-terminate logic.
   - Files to touch:
     - `weft/commands/_manager_bootstrap.py`
     - `weft/commands/run.py`
     - `weft/commands/manager.py`
     - `tests/commands/test_manager_commands.py`
     - `tests/cli/test_cli_manager.py`
   - Read first:
     - current `_stop_manager()` in `weft/commands/_manager_bootstrap.py`
     - current `stop_command()` helpers in `weft/commands/manager.py`
   - Required action:
     - extend the shared stop helper so it can stop by resolved record or by
       explicit TID lookup
     - preserve the `stop_if_absent` and `force` behaviors that `manager stop`
       needs
     - keep force terminate as a fallback after the graceful wait path, not as
       a replacement for STOP drain
     - prefer `terminate_process_tree()` for forced shutdown when a PID is
       known; treat a `Popen` handle as supplemental wait/cleanup state rather
       than the primary force-stop mechanism
     - use `record["ctrl_in"]` when present; otherwise fall back to
       `T{tid}.ctrl_in`
   - Error-path priorities:
     - failure to send STOP to a known live manager is fatal
     - missing registry state with `stop_if_absent=True` remains best-effort
       if the PID is already gone
     - if no registry or TID-mapping PID is available but a live `Popen`
       handle exists, a last-resort `process.terminate()` is acceptable as a
       fallback cleanup path for `run` only
   - Tests:
     - keep narrow unit tests for timeout and PID-edge cases
     - add one CLI proof that a real manager start/stop flow still passes
   - Stop if:
     - the shared stop path needs manager-core contract changes to work
     - the fallback kill path starts bypassing the graceful wait for live
       managers
   - Done when:
     - `run.py` and `manager.py` both call the same manager stop helper
     - no private STOP wait loop remains in `manager.py`

4. Rewire command wrappers and keep presentation thin.
   - Outcome: `run`, `worker`, and `status` become thin wrappers over the same
     manager lifecycle backend.
   - Files to touch:
     - `weft/commands/run.py`
     - `weft/commands/manager.py`
     - `weft/commands/status.py`
   - Required action:
     - keep `run.py` responsible for task submission and optional post-task
       manager shutdown only
     - keep `manager.py` responsible for command-local output text only
     - keep `status.py` responsible for the overall broker/task report and the
       manager-summary formatting only
   - Tests:
     - update helper tests so they assert wrapper delegation instead of private
       registry logic
     - keep at least one CLI-level assertion for each of `manager list`,
       `manager status`, and `status --json`
   - Stop if:
     - command wrappers start rebuilding normalized manager snapshots locally
   - Done when:
     - the lifecycle logic has one owner
     - formatting logic still has separate owners

5. Update specs and traceability.
   - Outcome: the specs describe one shared manager lifecycle backend rather
     than a shared bootstrap path plus separate operator lifecycle readers.
   - Files to touch:
     - `docs/specifications/03-Manager_Architecture.md`
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/07-System_Invariants.md`
     - `docs/specifications/10-CLI_Interface.md`
   - Required action:
     - add or update plan backlinks
     - update implementation mappings so they point list/status/stop behavior
       at the shared helper boundary
     - state plainly that canonical filtering is for default-manager selection,
       while operator commands still inspect manager records by TID
   - Stop if:
     - the docs start promising a broader specialized-manager design than the
       code actually supports
   - Done when:
     - spec section, plan, and code ownership point at the same shared module

## Testing Plan

Run in sequence:

1. `uv run pytest tests/commands/test_run.py tests/commands/test_manager_commands.py -q`
2. `uv run pytest tests/cli/test_cli_manager.py tests/cli/test_status.py -q`
3. `uv run pytest tests/core/test_manager.py -q`
4. `uv run mypy weft`
5. `uv run ruff check weft`

Keep real:

- broker-backed `weft.state.managers` and `weft.state.tid_mappings` queues
- at least one real manager start/stop CLI flow
- manager-side STOP drain semantics in `weft/core/manager.py`

Acceptable narrow mocking:

- PID liveness edges and timeout loops inside unit tests for `manager stop`
- helper delegation assertions where the contract is specifically "wrapper
  calls shared helper"

## Observable Success

- A dead active manager record no longer appears as `active` in `weft manager list`
  while disappearing or changing differently in `weft status`; both commands
  now agree on liveness.
- `weft manager stop` and `weft run` manager-reuse shutdown wait on the same
  stop conditions.
- `weft manager status <tid>` does not report a dead active manager as live just
  because a stale registry entry still exists.
- Shared manager records exposed to command wrappers use one normalized shape:
  `timestamp` is stable, `requests` is always present, and `ctrl_in` fallback
  resolves to `T{tid}.ctrl_in`.

## Rollout and Rollback

Rollout:

- No external migration is required. This is an internal command-layer
  consolidation.
- Mixed-version concern still matters for default-manager selection: canonical
  selection must continue to ignore live records bound to non-default request
  queues.
- Operator visibility should stay broader than canonical selection. Do not
  accidentally hide non-canonical managers from explicit operator commands
  while tightening bootstrap.

Rollback:

- Revert the shared lifecycle-helper changes and wrapper imports together.
  Do not partially roll back `manager.py` or `status.py` while leaving `run.py`
  on the new shared stop/read semantics.
- If stale-record pruning behavior changes during the slice, roll back the
  shared normalization helper first so all commands return to the same old
  interpretation together.
- Stale-record deletion in `weft.state.managers` is a one-way runtime cleanup.
  Rollback will not recreate deleted stale active entries. That is acceptable
  only because this queue is runtime-only and those entries are already dead.
  If implementation pressure expands pruning beyond dead active records, stop
  and re-plan because rollback assumptions no longer hold.

## Review Gate

Independent review completed on 2026-04-09 with Claude Code. The plan was
tightened after review to resolve three blockers before implementation:
normalized record shape, force-stop behavior, and the default `ctrl_in`
fallback. If the implementation reopens any of those questions, stop and
update the plan before changing code.

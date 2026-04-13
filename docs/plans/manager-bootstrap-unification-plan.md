# Manager Bootstrap Unification Plan

## Goal

Remove the separate `weft manager start` manager-launch path and make operator
startup use the same canonical bootstrap flow as `weft run`. The result should
be one manager startup path per context, one manager-selection rule, and no
remaining CLI support for arbitrary manager TaskSpec startup.

## Source Documents

Source specs:

- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md) [MA-0], [MA-1], [MA-3]
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md) [MF-6], [MF-7]
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md) [MANAGER.1], [MANAGER.3], [MANAGER.6], [MANAGER.7]
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)

Repo guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)

Relevant existing context:

- [`docs/lessons.md`](../lessons.md)
- [`docs/plans/task-lifecycle-stop-drain-audit-plan.md`](./task-lifecycle-stop-drain-audit-plan.md)

## Context and Key Files

Files to modify:

- `weft/commands/run.py`
- `weft/commands/manager.py`
- `weft/cli.py`
- `weft/core/manager.py`
- `tests/commands/test_run.py`
- `tests/commands/test_manager_commands.py`
- `tests/cli/test_cli_manager.py`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/lessons.md`

Likely new shared helper file:

- `weft/commands/_manager_bootstrap.py`

Read first:

- `weft/commands/run.py` — owns the canonical manager bootstrap path today
- `weft/commands/manager.py` — currently launches `Manager` through a separate path
- `weft/core/manager.py` — manager registry, leader election, drain, idle shutdown
- `tests/core/test_manager.py` — existing lifetime guarantees and election/drain tests
- `tests/commands/test_run.py` — startup and stale-manager pruning coverage

Shared paths to reuse:

- `_ensure_manager`, `_start_manager`, `_build_manager_spec`, registry snapshot and selection logic
- existing `weft.manager_process` entrypoint
- existing manager drain and idle-shutdown logic in `Manager.process_once()`

Current structure:

- `weft run` starts managers through a hardened registry-aware helper.
- `weft manager start` separately loads a manager TaskSpec and starts `Manager`
  directly, either foreground or background.
- manager leader election currently considers all live `role="manager"` records,
  regardless of whether they are the canonical `weft.spawn.requests` manager.

Comprehension checks before editing:

- Which helper currently owns stale-registry pruning and startup wait logic?
- Which registry field tells us whether a manager is the canonical spawn-queue manager?

## Invariants and Constraints

- Keep one durable startup spine: CLI wrapper -> canonical bootstrap helper ->
  `weft.manager_process` -> `Manager`.
- Do not create a second startup path for explicit manager startup.
- Preserve manager control semantics after startup: STOP drain, leadership yield,
  and idle shutdown stay in `weft/core/manager.py`.
- Preserve registry shape for canonical managers. Existing `tid`, `pid`,
  `requests`, `ctrl_in`, `ctrl_out`, and `role` fields must still be emitted.
- Preserve TID format and immutability.
- Preserve child spawn semantics and the global spawn queue contract.
- Do not reintroduce arbitrary manager TaskSpec startup through a compatibility
  shim.
- Treat rollout as mixed-version sensitive: canonical manager selection must not
  adopt an old live manager that listens on a non-default request queue.
- No drive-by refactor beyond the startup helper extraction needed to share the
  code path cleanly.

Out of scope:

- manager stop/list/status stale-registry cleanup redesign
- autostart manifest behavior
- manager idle-timeout policy changes
- specialization or multiple manager pool design

## Tasks

1. Extract the canonical bootstrap helper into one shared command-support module.
   - Outcome: `run` and `manager start` can call the same startup code.
   - Files to touch:
     - `weft/commands/run.py`
     - `weft/commands/_manager_bootstrap.py`
   - Reuse the existing helper behavior exactly: stale-record pruning,
     detached `weft.manager_process`, wait-for-registry-entry, and
     `WEFT_MANAGER_REUSE_ENABLED` shutdown helper.
   - Stop if the extraction starts changing result-wait or task-submission logic.
   - Done when `run.py` no longer owns a private bootstrap-only implementation.

2. Replace `manager start` with a thin operator wrapper over the shared bootstrap helper.
   - Outcome: `weft manager start` starts or adopts the canonical manager for the
     current context and no longer accepts a TaskSpec file or foreground mode.
   - Files to touch:
     - `weft/commands/manager.py`
     - `weft/cli.py`
   - Use `build_context()` plus the shared bootstrap helper.
   - Return operator-facing text that distinguishes “started here” from
     “already running”.
   - Remove dead code for TaskSpec loading, foreground startup, and direct
     `launch_task_process(Manager, ...)`.
   - Stop if the wrapper starts rebuilding manager specs itself.

3. Narrow canonical manager election and leadership to canonical request-queue managers.
   - Outcome: old or stray live manager records using non-default request queues
     cannot be adopted by `run` / `manager start` and cannot force the canonical
     manager to yield.
   - Files to touch:
     - `weft/commands/_manager_bootstrap.py`
     - `weft/core/manager.py`
     - `tests/commands/test_run.py`
     - `tests/core/test_manager.py`
   - Use the registry `requests` queue field as the canonical-manager filter.
   - Keep registry output broad; only selection/election should narrow.
   - Stop if the change wants a new persisted flag when the existing queue field
     is sufficient.

4. Update tests to prove the single-path contract.
   - Outcome: tests cover the new `manager start` wrapper and reject the removed
     path.
   - Files to touch:
     - `tests/commands/test_manager_commands.py`
     - `tests/cli/test_cli_manager.py`
     - nearby startup/election tests as needed
   - Prefer command/helper tests plus one CLI proof.
   - Keep broker-backed lifetime tests real. Do not replace startup behavior
     with mock-only end-to-end claims.

5. Update specs and lessons to match the new contract.
   - Outcome: docs no longer describe TaskSpec-driven `manager start`, and the
     spec back-links this plan.
   - Files to touch:
     - `docs/specifications/03-Manager_Architecture.md`
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/10-CLI_Interface.md`
     - `docs/lessons.md`
   - State plainly that `manager start` is now an operator wrapper over the same
     bootstrap helper as `weft run`.

## Testing Plan

Run in sequence:

1. `uv run pytest tests/commands/test_run.py tests/commands/test_manager_commands.py tests/cli/test_cli_manager.py -q`
2. `uv run pytest tests/core/test_manager.py -q`
3. `uv run pytest tests/context/test_context.py -q`
4. `uv run mypy weft`
5. `uv run ruff check weft`

Keep real:

- real manager registry snapshot logic
- real `Manager` leader-election and drain tests
- real CLI command surface for at least one `manager start` flow

## Rollout and Rollback

Rollout concern:

- mixed-version environments may still have old live manager records started
  from custom request queues. Canonical selection/election must ignore those
  records so a new CLI does not adopt the wrong manager.

Rollback:

- revert the shared bootstrap helper and `manager start` wrapper together; do
  not partially roll back one side while leaving the canonical-manager filter in
  only one caller.

## Review Note

Independent review is preferred, but this session is proceeding without a
separate reviewer pass unless one is requested explicitly.

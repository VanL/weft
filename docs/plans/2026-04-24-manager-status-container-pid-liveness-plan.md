# Manager Status Container PID Liveness Plan

Status: completed
Source specs: see Source Documents below
Superseded by: none

## Goal

Fix `weft status` so an old manager task cannot appear as a second live
manager when the only active manager is a supervised Docker container whose
manager process is PID `1` inside the container. The public output should make
the live-manager answer unambiguous: the `Managers:` section is authoritative
for active managers, and stale manager task-log rows must not be resurrected
as `running` by host PID checks that are using the wrong process namespace.

## Source Documents

Source specs:

- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md)
  [MA-0], [MA-1.4], [MA-3], [MA-4]
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-5], [MF-7]
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [STATE.1], [OBS.3], [OBS.4], [IMPL.1]
- [`docs/specifications/08-Testing_Strategy.md`](../specifications/08-Testing_Strategy.md)
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
  [CLI-1.2.1]

Repo guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)

Related context:

- [`docs/lessons.md`](../lessons.md) entries for `2026-04-08 Zombie PID
  Liveness` and `2026-04-09 Manager Bootstrap Unification`.
- Observed incident in `/Users/van/Developer/mm`: `weft manager list --json`
  showed only manager `1777065168543608832`, while `weft status --json` showed
  stale manager task `1777065050392768512` as `running` because its terminal
  `task_signal_stop` event carried `task_pid=1` and status replay checked the
  host namespace's PID `1`.

## Context and Key Files

Files to modify:

- `weft/commands/system.py`
- `tests/commands/test_status.py`
- `tests/cli/test_status.py`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/lessons.md`

Files to read first:

- `weft/commands/system.py`
  - `_collect_manager_records()` reads the live manager registry.
  - `_collect_task_snapshots()` replays `weft.log.tasks`.
  - `_effective_public_status()` currently converts terminal host task states
    back to `running` when `task_pid` still appears live.
- `weft/core/manager_runtime.py`
  - `_snapshot_registry()` and `_select_active_manager_from_snapshot()` define
    the canonical live-manager registry view.
- `weft/core/manager.py`
  - `Manager._register_manager()` and `Manager._unregister_manager()` publish
    `weft.state.managers`.
  - `Manager._finish_graceful_shutdown()` publishes manager terminal events.
- `weft/core/tasks/base.py`
  - `_report_state_change()` publishes `task_pid`, `runner`, and
    `runtime_handle` into `weft.log.tasks`.
- `tests/commands/test_status.py`
  - Existing tests intentionally keep terminal host tasks as public `running`
    while their consumer PID is still alive. Preserve that behavior for normal
    host tasks.
- `tests/cli/test_status.py`
  - CLI-level shape tests for `weft status`.

Comprehension checks before editing:

- Which section is authoritative for live managers: `Managers:` from
  `weft.state.managers`, or `Tasks:` from `weft.log.tasks`?
- For a normal host task, why can a terminal log event still be presented as
  `running` while the consumer process is still alive?
- Why is PID `1` from a Docker-contained manager not valid host liveness proof?

Current structure:

- Managers are tasks by design [MA-0], so they appear in both the manager
  registry and the task log.
- `weft status` currently renders both views without explaining the authority
  split.
- Status replay merges the latest task-log payload with
  `weft.state.tid_mappings`, then `_effective_public_status()` applies live
  runtime checks. For host tasks, any apparently-live `task_pid` can make a
  terminal state display as `running`.
- A manager running inside a container can report `pid=1` and `task_pid=1`.
  From the host, PID `1` is usually a different process. That makes raw host
  PID liveness the wrong authority for manager task rows.

## Invariants and Constraints

- Do not change queue names, TID format, TaskSpec schema, or TaskSpec
  immutability.
- Do not change manager startup, manager election, or dispatch ownership.
  This is a status reconstruction bug, not a manager-bootstrap bug.
- Preserve the current durable spine:
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log`.
- Preserve the existing rule that normal host tasks with a terminal log event
  may still display as `running` while the consumer wrapper PID is actually
  alive.
- Preserve the manager registry as runtime-only state. Do not persist
  `weft.state.managers` into dumps or turn it into a second database.
- Keep queue history reads generator-based. Do not replace replay with a fixed
  `peek_many(limit=...)`.
- Do not add a Docker dependency or require Docker in unit tests for this fix.
  The regression can be expressed with synthetic broker records because the bug
  is in replay semantics.
- Do not hide manager task rows wholesale. Users still need manager lifecycle
  auditability in `Tasks:` and `weft task status <manager-tid>`.
- Do not make public CLI JSON incompatible. New internal helper fields are
  allowed only if they are omitted from public output or explicitly documented.
- If implementation starts requiring container namespace inspection, stop and
  re-plan. The narrow fix should not need Docker runtime introspection.

## Proposed Design

Use the live manager registry as the authority for manager-task liveness.

For task snapshots whose `taskspec.metadata.role == "manager"`:

- If the same TID has an active manager registry record, and the registry PID
  is live according to the registry reader's existing liveness checks, public
  status may remain `running`.
- If the same TID is absent from the active manager registry, do not let
  `task_pid` or `pid` resurrect the task as `running`.
- If the task log has a terminal status, show that terminal status.
- If the task log is contradictory, for example `event="task_signal_stop"` plus
  `completed_at` but `status="running"`, normalize it to `cancelled` for
  public status. This should be implemented as a narrow terminal-event
  reconciliation helper, not as broad mutation of TaskSpec state.

For non-manager tasks, keep the existing `_effective_public_status()` behavior
unless a failing test proves it has the same namespace problem through a
well-defined runtime handle. The current incident is manager-specific because
the manager registry already gives us the right authority.

## Tasks

1. Add targeted failing coverage for stale manager task rows.
   - Outcome: the observed confusion is reproducible without Docker.
   - Files to touch:
     - `tests/commands/test_status.py`
   - Build a broker-backed test with:
     - one active manager record in `weft.state.managers` for manager B
     - one old manager task-log row for manager A with
       `event="task_signal_stop"`, `status="running"`,
       `taskspec.state.completed_at` set, `metadata.role="manager"`, and
       `task_pid=1`
     - one running task-log row for manager B
   - Monkeypatch `status_cmd.pid_is_live` or the narrow helper used by
     `_effective_public_status()` so PID `1` appears live. Do not actually rely
     on host PID `1` behavior in the test.
   - Assert:
     - `cmd_status(json_output=True, include_terminal=True)` returns exactly
       one active manager in `managers`
     - manager A's task snapshot is not `running`
     - manager B's task snapshot remains `running`
   - Stop and re-evaluate if this requires starting a real Docker container.

2. Add public text-output coverage for the confusing default surface.
   - Outcome: the default `weft status` text no longer implies two active
     managers in the incident shape.
   - Files to touch:
     - `tests/cli/test_status.py` or `tests/commands/test_status.py`
   - Prefer command-layer text testing if existing CLI fixtures make synthetic
     registry/log setup awkward.
   - Assert the text output still has a `Managers:` section with one active
     manager and that the stale manager task row either:
     - is absent from default non-terminal output, or
     - appears with a terminal state rather than `running`.
   - Do not change the CLI column names unless a spec update explicitly says
     to do so.

3. Implement manager-aware liveness reconciliation in shared status code.
   - Outcome: manager task rows consult manager registry authority before
     host PID liveness can override terminal or stale states.
   - Files to touch:
     - `weft/commands/system.py`
   - Recommended shape:
     - In `_collect_task_snapshots()`, collect the current manager records once
       near the existing task replay path.
     - Build a set or mapping of active manager TIDs from
       `_collect_manager_records(ctx, include_stopped=False)`.
     - Thread a small `is_manager_task` and `manager_is_active` fact into the
       public-status decision.
     - Keep `_effective_public_status()` usable for non-manager tasks. Either
       add explicit keyword parameters with defaults, or wrap it with a
       manager-specific guard before calling it. Prefer the smallest readable
       change.
   - Do not import manager runtime internals from a new place if
     `_collect_manager_records()` already gives the needed normalized view.
   - Stop and re-evaluate if the code wants to special-case Docker or PID `1`
     directly. PID `1` is a symptom, not the durable boundary.

4. Add narrow terminal-event reconciliation for contradictory stop/kill rows.
   - Outcome: a manager task-log event with terminal stop/kill semantics does
     not stay public `running` only because its payload status is stale.
   - Files to touch:
     - `weft/commands/system.py`
     - possibly `weft/commands/_result_wait.py` only if a shared helper can be
       reused without changing result-wait semantics unexpectedly.
   - Define a small helper such as `_status_from_log_payload(payload)` or
     `_reconcile_log_status(payload, state)` near `_collect_task_snapshots()`.
   - Required mappings:
     - explicit terminal `payload.status` or `taskspec.state.status` wins
     - `event in {"control_stop", "task_signal_stop"}` with `completed_at`
       set maps to `cancelled`
     - `event in {"control_kill", "task_signal_kill"}` with `completed_at`
       set maps to `killed`
     - `work_completed` with `completed_at` set maps to `completed`
     - do not infer terminal state from `completed_at` alone for arbitrary
       events; that could hide corrupt or partial lifecycle records
   - Stop and re-evaluate if this starts rewriting historical queue messages.
     The fix is read-time presentation, not queue mutation.

5. Update specs and lessons.
   - Outcome: future implementers know the authority split and namespace
     boundary.
   - Files to touch:
     - `docs/specifications/03-Manager_Architecture.md`
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/lessons.md`
   - Add a plan backlink in the relevant specs.
   - Document:
     - manager registry is authoritative for live manager count
     - task log remains audit/history
     - manager task rows must not use host PID liveness as the sole proof when
       the active manager registry disagrees
   - Add a lesson that container-local PIDs, especially PID `1`, are not
     portable host liveness evidence.

6. Run focused verification, then expand.
   - Required focused commands:
     - `./.venv/bin/python -m pytest tests/commands/test_status.py -q`
     - `./.venv/bin/python -m pytest tests/cli/test_status.py -q`
     - `./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q`
   - Required broader checks if focused tests pass:
     - `./.venv/bin/python -m pytest tests/cli/test_cli_manager.py tests/cli/test_cli_serve.py -q`
     - `./.venv/bin/ruff check weft tests/commands/test_status.py tests/cli/test_status.py`
   - Before claiming done, reproduce the original operator-level check in an
     environment like `/Users/van/Developer/mm` if available:
     - source that project's `.env`
     - run `weft manager list --json`
     - run `weft status --json`
     - confirm the `managers` array has one active manager and no stale manager
       task is public `running`.

## Rollback

Rollback is straightforward if the implementation stays read-only: revert the
status-code, tests, and docs. No queue migration, data cleanup, or identifier
change should be involved.

The implementation must remain backward-compatible with existing broker data.
Old contradictory task-log rows may continue to exist; the fix changes how
they are interpreted for public status, not the stored messages.

## Review Gate

Run an independent review after the plan is accepted and before implementation
lands. Use this prompt:

> Read `docs/plans/2026-04-24-manager-status-container-pid-liveness-plan.md`,
> `weft/commands/system.py`, `weft/core/manager_runtime.py`, and
> `tests/commands/test_status.py`. Look for incorrect assumptions about manager
> authority, PID liveness, and terminal event replay. Could you implement this
> confidently and correctly without changing manager startup behavior?

The reviewer should explicitly answer whether the plan keeps normal host task
terminal-live behavior intact while fixing stale manager rows.

## Out Of Scope

- Docker namespace inspection.
- New manager process supervision behavior.
- Manager startup, stop, or election rewrites.
- Queue cleanup or historical task-log mutation.
- A broader redesign of `weft status` formatting.
- Hiding managers from the task log.

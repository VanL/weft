# TaskMonitor External Log Health Plan

Status: completed
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13], [OBS.13.11], [OBS.17]; docs/specifications/10-CLI_Interface.md [CLI-1.2.1], [CLI-5]
Superseded by: none

## Goal

Make external TaskMonitor JSONL log health observable on the monitor cadence.
The configured path still requires a monitor restart to change, but path
availability and permission changes must be probed once per monitor cycle.
The latest health must be available persistently through status data, and the
permission/recovery cases must be tested.

## Source Documents

- `AGENTS.md`: specs are truth; plans live in `docs/plans/`; runtime and
  status changes require tests and traceability.
- `docs/agent-context/runbooks/writing-plans.md` and
  `docs/agent-context/runbooks/hardening-plans.md`: this touches runtime
  cleanup, deferred processing, and public status shape.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]: TaskMonitor
  cleanup, external JSONL, deferred writes, and PONG/status diagnostics.
- `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.13.11],
  [OBS.17]: Monitor operational evidence only, cached diagnostics, exact
  delete.
- `docs/specifications/10-CLI_Interface.md` [CLI-1.2.1], [CLI-5]: `weft
  status` service summary and unified config behavior.
- `docs/plans/2026-05-30-task-monitor-mode-and-rotating-log-plan.md`:
  completed context for mode selection, default log path, rotating handler, and
  `jsonl_then_delete`.

## Context And Key Files

Files to modify:

- `weft/core/monitor/external_log.py`: add a probe operation that revalidates
  the current configured path without emitting a JSONL record.
- `weft/core/monitor/task_monitor.py`: call the probe once per monitor cycle,
  cache health transitions, persist diagnostics in the TID mapping payload, and
  keep PONG cached.
- `weft/commands/types.py` and `weft/commands/system.py`: add an additive
  `diagnostics` field to service status JSON and concise text output.
- `tests/core/test_monitor_external_log.py`, `tests/tasks/test_task_monitor.py`,
  `tests/core/test_task_monitoring.py`, and `tests/commands/test_status.py`:
  cover path creation, permission failure/recovery, regression to failure,
  startup and later operational warnings, accumulated deferred flush, and
  status reporting.
- Specs above and this plan index.

Current structure:

- `TaskMonitor._configure_external_task_log_sink()` constructs the sink once at
  startup; config path changes are not reloaded and remain restart-only.
- `ExternalTaskLogSink.validate()` establishes the rotating file handler and
  caches health; later `_refresh_external_task_log_status()` only reads cached
  health.
- `jsonl_then_delete` writes or defers reports before deleting selected
  subjects. Deferred reports are flushed from Monitor-owned tables during
  monitor-store cycles.
- `weft status` already exposes manager-owned internal services. It does not
  ping services; it should use persisted queue evidence.
- The existing TID mapping write path suppresses equivalent rows. TaskMonitor
  diagnostics must be part of the equivalence check, or health-only changes
  will not be visible to passive status.

## Invariants

- Do not reload `WEFT_LOG_TASKS_EXTERNAL_PATH` while a monitor is running.
- Do not emit probe records into the JSONL lifetime log.
- Do not block cleanup solely because a probe failed if a deferred write can
  accept the report; existing write/defer/delete ordering remains authoritative.
- Keep TaskMonitor PONG live-only/cached. Do not make PONG perform file I/O.
- `weft status` must not ping the monitor. It should report only persisted
  service diagnostics from existing status inputs.
- Existing exact-delete and deferred-write semantics remain unchanged.

## Implementation Steps

1. Add sink probing.
   - Add `ExternalTaskLogSink.probe()` that closes any cached handler, attempts
     to recreate the rotating handler for the configured path, and updates
     cached health.
   - Keep `validate()` as startup validation, implemented through the same
     path.
   - Keep `_ensure_handler()` the only handler creation path.

2. Probe on monitor cadence.
   - Add a TaskMonitor helper that calls `sink.probe()` at cycle start when
     external logging is enabled.
   - Use it from both custom and built-in cycle paths, including worker-local
     built-in work.
   - Record health transitions in cached warnings and operational log fields,
     but do not create a second write/defer/delete path.

3. Persist diagnostics for `weft status`.
   - Override or extend the TaskMonitor TID mapping payload with a compact
     `task_monitor` diagnostics object containing `task_log_external`.
   - Add `ServiceSnapshot.diagnostics` and populate it for the task-monitor
     service from the latest TID mapping payload.
   - Render diagnostics in `weft status --json`; include a concise text hint
     when external logging is unhealthy or deferred writes are pending.

4. Tests.
   - Unit-test non-existent file path creation and directory/wrong-path
     failure.
   - Simulate permission failures by monkeypatching the rotating handler open
     path rather than relying on platform chmod semantics.
   - Test can-write to cannot-write and cannot-write to can-write transitions
     through `probe()`.
   - Test multi-row deferred flush writes all pending rows up to batch size and
     marks them flushed.
   - Test `weft status --json` reports unhealthy external-log diagnostics for
     the task-monitor service from persisted mapping state.
   - Test the real monitor path writes updated TaskMonitor diagnostics to
     `weft.state.tid_mappings` when health changes are the only observable
     change.

## Verification

- `./.venv/bin/python -m pytest tests/core/test_monitor_external_log.py tests/tasks/test_task_monitor.py tests/commands/test_status.py -q`
- `./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py -q`
- `./.venv/bin/ruff check weft tests`
- `./.venv/bin/mypy weft bin extensions/weft_docker extensions/weft_macos_sandbox`
- `./.venv/bin/python -m pytest -q`
- `git diff --check`

## Rollback

Source rollback only. Reverting this slice returns to write-time health
detection and removes the additive service diagnostics field from status JSON.
No schema rollback is needed because the status diagnostics live in existing
runtime TID mapping rows.

## Self-Review Notes

Fresh-eyes pass:

- Avoid chmod-only tests because root/container/platform semantics can make
  permission assertions misleading. Use monkeypatching for deterministic
  permission transitions and keep one normal filesystem creation test.
- Do not add a new persistence table; the existing TID mapping queue is the
  right runtime-status surface.
- Do not emit warning JSONL rows into the lifetime log. The lifetime stream is
  for reports, not monitor health.
- Status should remain passive. Live health remains available through
  `task status --ping`; project-wide `weft status` uses cached/persisted data.
- Keep service lifecycle status separate from external-log health. A bad log
  path is a degraded diagnostic on a running TaskMonitor service, not proof
  that the service is stopped or unhealthy as a lifecycle state.

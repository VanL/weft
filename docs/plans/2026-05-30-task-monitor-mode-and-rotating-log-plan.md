# TaskMonitor Mode And Rotating Log Plan

Status: completed
Source specs: docs/specifications/00-Quick_Reference.md; docs/specifications/01-Core_Components.md [CC-2.3]; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13], [OBS.13.11], [OBS.17]; docs/specifications/10-CLI_Interface.md [CLI-5]
Superseded by: none

## Goal

Split TaskMonitor cleanup behavior selection from custom processor selection.
`WEFT_TASK_MONITOR_MODE` becomes the config-owned mode selector for
`report_only`, `delete`, `jsonl_then_delete`, and `custom`; custom
`module:function` processors remain configured through
`WEFT_TASK_MONITOR_PROCESSOR` only when mode is `custom`. The lifetime JSONL
sink gets a real default path at `<weft project root>/logs/weft.log`, and file
output uses Python's standard rotating log handler.

## Source Documents

- `AGENTS.md`: config lives in `weft/_constants.py`; specs are truth; plans
  live in `docs/plans/`.
- `docs/agent-context/decision-hierarchy.md`: update public config contracts
  through specs, code, and tests together.
- `docs/agent-context/runbooks/writing-plans.md` and
  `docs/agent-context/runbooks/hardening-plans.md`: this changes a public
  config contract and a destructive cleanup mode.
- `docs/specifications/00-Quick_Reference.md`: environment/config quick
  reference.
- `docs/specifications/01-Core_Components.md` [CC-2.3]: TaskMonitor runtime
  config and persistent reactor.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]: TaskMonitor
  cleanup, external JSONL, and lifetime reports.
- `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.13.11],
  [OBS.17]: operational evidence only, cached diagnostics, exact delete.
- `docs/specifications/10-CLI_Interface.md` [CLI-5]: unified config path.
- `docs/plans/2026-05-29-task-monitor-general-lifetime-reporting-plan.md`:
  completed context for `jsonl_then_delete` lifetime reports.

## Context And Key Files

Files to modify:

- `weft/_constants.py`: add `WEFT_TASK_MONITOR_MODE`, mode parser, default
  external path, and rotation constants. Keep this the single config source.
- `weft/core/monitor/runtime.py`: parse typed mode and custom processor
  separately from the canonical config mapping.
- `weft/core/monitor/task_monitor.py`: use `mode` for built-in cleanup
  branches and `processor` only for custom mode.
- `weft/core/monitor/external_log.py`: replace the custom file handler base
  with `logging.handlers.RotatingFileHandler` while preserving fail-closed
  error propagation.
- `docs/specifications/*`: update public config and external logging text.
- `tests/core/test_task_monitoring.py`, `tests/tasks/test_task_monitor.py`,
  `tests/system/test_constants.py`, and `tests/core/test_monitor_external_log.py`:
  update config assertions and prove the rotating/default path behavior.

Current structure:

- `load_config()` and `build_context()` are the unified config path. Do not add
  a TaskMonitor-only environment reader.
- `TaskMonitorRuntimeConfig.from_config(...)` is the typed runtime boundary.
- `TaskMonitor._configure_external_task_log_sink()` resolves the configured
  path and owns sink construction.
- `ExternalTaskLogSink` owns file writes and must remain the only external
  JSONL I/O boundary.

## Invariants

- A configured log path is not a trigger. The trigger for lifetime reporting is
  `WEFT_TASK_MONITOR_MODE=jsonl_then_delete` plus policy-selected destructive
  cleanup work.
- No backwards compatibility alias is allowed for built-in modes:
  `WEFT_TASK_MONITOR_PROCESSOR=delete`, `report_only`, or `jsonl_then_delete`
  must fail and direct the user to `WEFT_TASK_MONITOR_MODE`.
- Plain `delete` mode must not begin external logging merely because the new
  default path exists.
- `jsonl_then_delete` must still write or durably defer the lifetime report
  before exact delete.
- PONG and STATUS must continue reporting cached diagnostics only.
- Custom processors must not be confused with built-in cleanup modes.
- No new persistence tables or queue names are needed for this follow-up.

## Implementation Steps

1. Add config constants and parsing.
   - Add `WEFT_TASK_MONITOR_MODE_DEFAULT = "delete"` and
     `WEFT_TASK_MONITOR_MODES`.
   - Change `WEFT_LOG_TASKS_EXTERNAL_PATH_DEFAULT` to `logs/weft.log`.
   - Keep `WEFT_LOG_TASKS_EXTERNAL_ENABLED` default false for plain `delete`.
   - Make `jsonl_then_delete` mode default external logging to enabled unless
     the user explicitly disables it.
   - Make `WEFT_TASK_MONITOR_PROCESSOR` accept only blank/default or
     `module:function`.

2. Update runtime config.
   - Add `mode` to `TaskMonitorRuntimeConfig`.
   - Keep `processor` as `str | None`.
   - Require a custom processor only for `mode=custom`.
   - Reject a custom processor when mode is not `custom`.
   - Validate `jsonl_then_delete` requirements against mode, not processor.

3. Update TaskMonitor execution branches.
   - Replace built-in branch checks with `self._monitor_config.mode`.
   - Use `self._monitor_config.processor` only in custom mode resolution.
   - Include both `mode` and `processor` in cached diagnostics and operational
     log fields where processor was previously shown.
   - Resolve relative external log paths from `WeftContext.root`, so the
     default becomes `<root>/logs/weft.log`.

4. Use rotating standard logging.
   - Replace the file handler subclass with a subclass of
     `logging.handlers.RotatingFileHandler`.
   - Use constants for max bytes and backup count.
   - Preserve JSONL one-record-per-line formatting and fail-closed propagation.

5. Update specs and tests.
   - Specs must state that mode is the trigger and path is the destination.
   - Tests should cover default mode/path/enabled interaction, mode validation,
     custom processor validation, default path resolution, and rotating handler
     type/rotation behavior.

## Verification

- `./.venv/bin/python -m pytest tests/core/test_task_monitoring.py tests/tasks/test_task_monitor.py tests/core/test_monitor_external_log.py tests/system/test_constants.py -q`
- `./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py tests/specs/quick_reference/test_queue_names.py -q`
- `./.venv/bin/ruff check weft tests`
- `./.venv/bin/mypy weft bin extensions/weft_docker extensions/weft_macos_sandbox`
- `./.venv/bin/python -m pytest -q`
- `git diff --check`

## Rollback

Rollback is source-level only. Revert this slice to restore the previous
`WEFT_TASK_MONITOR_PROCESSOR` built-in selector and empty external path default.
No schema rollback is needed.

## Self-Review Notes

Fresh-eyes review before implementation:

- Ambiguity risk: `WEFT_LOG_TASKS_EXTERNAL_PATH_DEFAULT` becoming non-empty
  could accidentally enable external logging in `delete` mode. The plan blocks
  that explicitly by keeping enabled false unless mode is `jsonl_then_delete`
  or the user explicitly configures external logging.
- Boundary risk: using `WEFT_LOGS_DIR` for the new default would produce
  `.weft/logs/weft.log`, not the requested project-root `logs/weft.log`. The
  plan requires TaskMonitor external-path resolution from `WeftContext.root`.
- Extension risk: custom processors need a clear mode. The plan uses
  `mode=custom` rather than overloading `PROCESSOR`.

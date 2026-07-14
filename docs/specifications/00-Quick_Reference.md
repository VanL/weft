# Quick Reference

Concise tables for runtime names and values. Canonical definitions live in the
linked documents; this file is the index.

## Queue Names

Per-task queues (generated from TID):

| Pattern | Purpose |
|---------|---------|
| `T{tid}.inbox` | Task input messages |
| `T{tid}.reserved` | WIP / reserved messages (DLQ-style recovery) |
| `T{tid}.outbox` | Final task output |
| `T{tid}.ctrl_in` | Control messages (STOP, KILL, STATUS, PING, PAUSE, RESUME) |
| `T{tid}.ctrl_out` | Control responses |

Per-pipeline queues (generated from pipeline TID):

| Pattern | Purpose |
|---------|---------|
| `P{tid}.inbox` | Pipeline input messages |
| `P{tid}.outbox` | Final pipeline result |
| `P{tid}.ctrl_in` | Pipeline control messages (same control set) |
| `P{tid}.ctrl_out` | Pipeline control replies |
| `P{tid}.status` | Retained pipeline status snapshots |
| `P{tid}.events` | Private child-owner coordination |

Global queues:

| Name | Purpose | Persisted in dump? |
|------|---------|--------------------|
| `weft.log.tasks` | Runtime task lifecycle evidence / state events | Yes |
| `weft.spawn.requests` | Manager spawn requests | Yes |
| `weft.spawn.internal` | Manager-owned internal service spawn requests | Yes |
| `weft.manager.ctrl_in` | Manager control input | Yes |
| `weft.manager.ctrl_out` | Manager control output | Yes |
| `weft.manager.outbox` | Manager informational output | Yes |
| `weft.state.services` | Runtime service-owner registry, including managers | No (runtime state) |
| `weft.state.tid_mappings` | Short→full TID mappings | No (runtime state) |
| `weft.state.endpoints` | Active named endpoint registry | No (runtime state) |
| `weft.state.streaming` | Active streaming sessions | No (runtime state) |
| `weft.state.pipelines` | Active pipeline registry | No (runtime state) |

Notes:
- `weft.state.*` queues are runtime state and are excluded from dumps by default.
- `weft.state.services` manager/service-owner rows use `active` and `draining`
  as live convergence evidence. `stopped`, `superseded`, and `terminal` are
  non-live evidence; a latest `superseded` row excludes that owner TID from
  manager leadership.
- `weft.log.tasks` is runtime evidence used by Weft status, result, and
  debugging surfaces while retained. It is not legal, forensic, or audit
  evidence; retention is an operational policy.
- `weft_monitor_meta`, `weft_monitor_task_collations`,
  `weft_monitor_task_messages`, and `weft_monitor_deferred_writes` are
  Monitor-owned operational tables, not queues. They are derived from
  `weft.log.tasks`, are not exposed through `weft queue *`, and are not task
  lifecycle or result authority.
  `weft_monitor_task_messages` stores temporary pending raw-message references;
  completed raw-message refs are physically removed from the Monitor table
  rather than retained as queue-like tombstones.
  `weft_monitor_deferred_writes` is the Monitor-owned durable-before-delete
  outbox for `jsonl_then_delete` lifetime reports; deferred writes are retried
  by bounded monitor cycles and are operational outbox state only.
- `weft system prune` can explicitly dry-run or apply exact-message pruning.
  It defaults to runtime-only `weft.state.*` pruning. Retention families can
  also prune selected `weft.log.tasks` and task-local `T{tid}.*` rows with
  archive-backed ordinary apply or explicit human `--force`.
- named endpoint records resolve stable project-local names to ordinary
  task-local queues.
- Full queue behaviors are defined in `05-Message_Flow_and_State.md`.

_Implementation mapping_: `weft/_constants.py` (global queue constants),
`weft/core/tasks/base.py` (queue wiring and runtime endpoint registration),
`weft/core/endpoints.py` (named endpoint resolution),
`weft/core/manager.py` (manager registry), `weft/core/pipelines.py`
(pipeline queue compilation), `weft/core/tasks/pipeline.py`
(pipeline runtime queues), `weft/commands/runtime_prune.py`
(explicit runtime-state pruning), `weft/commands/retention_prune.py`
(explicit task-local and task-log retention pruning), `weft/core/monitor/store.py`
(Monitor-owned operational tables), `weft/core/monitor/sql.py`
(Monitor table SQL builders).

## CLI Surface

Current top-level verbs and subcommands:

| Surface | Current commands |
|---------|------------------|
| `weft` | `init`, `status`, `result`, `run`, `queue`, `manager`, `spec`, `task`, `system` |
| `weft task` | `list`, `status`, `stop`, `kill`, `ping`, `tid` |
| `weft manager` | `start`, `serve`, `stop`, `list`, `status` |
| `weft spec` | `create`, `list`, `show`, `delete`, `validate`, `generate` |
| `weft queue` | `read`, `write`, `peek`, `move`, `list`, `exists`, `stats`, `resolve`, `watch`, `delete`, `broadcast`, `alias add/list/remove` |
| `weft system` | `tidy`, `dump`, `builtins`, `load`, `task-monitor`, `prune` |

## Operational Files

| Path | Purpose |
|------|---------|
| `.weft/logs/task-monitor/YYYY-MM-DD.jsonl` | Append-only task-monitor log records for one UTC date |
| `.weft/logs/retention-prune/YYYY-MM-DD-retention-prune.jsonl` | Best-effort default archive for retention prune force runs without an explicit archive path |
| `.weft/state/task-monitor/default.json` | Task monitor operational checkpoint cursor |

Notes:
- Task monitor log files, retention prune archive files, reports, and
  checkpoints are operational outputs, not lifecycle truth. Status and result
  commands reconstruct state from broker runtime evidence, not from these
  files.

## Task States

| State | Description |
|-------|-------------|
| `created` | TaskSpec created but not started |
| `spawning` | Process/thread starting |
| `running` | Target executing |
| `completed` | Finished successfully |
| `failed` | Failed with error | 
| `timeout` | Time limit exceeded |
| `cancelled` | Stopped by control message |
| `killed` | Force terminated |

State transitions and rules live in `05-Message_Flow_and_State.md`.

## Control Messages

| Message | Effect |
|---------|--------|
| `STOP` | Graceful shutdown (task cancels and reports) |
| `KILL` | Force terminate the task |
| `STATUS` | Emit current task-local status on `ctrl_out` |
| `PING` | Health check; responds `PONG` with a live task-local status snapshot, echoes `request_id` for structured requests, includes manager-selection fields for Manager tasks, and may include task-registered extension data under `extended` |
| `PAUSE` | Pause task processing |
| `RESUME` | Resume a paused task |

## Process Title Format

`weft-{context_short}-{tid_short}:{name}:{status}[:details]`

Format rules and sanitization are defined by [OBS.4], [OBS.5], [OBS.7], and
[OBS.8] in `07-System_Invariants.md`.

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `WEFT_DEBUG` | Enable debug output in Weft helpers and CLI surfaces. |
| `WEFT_LOGGING_ENABLED` | Enable Weft logging output. |
| `WEFT_ENV_FILE` | Bootstrap dotenv-style file loaded before the full CLI import (see `10-CLI_Interface.md`). |
| `WEFT_CONTEXT` | Explicit project context directory honored by queue and status command helpers (see `04-SimpleBroker_Integration.md`). |
| `WEFT_MANAGER_SERVE_LOG_LEVEL` | Foreground `weft manager serve` operational-log verbosity: `off`, `info`, `debug`, or `trace`. Defaults to `off`. |
| `WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS` | Throttle interval for repeated foreground manager operational-log events. Defaults to 5 seconds. |
| `WEFT_REDACT_TASKSPEC_FIELDS` | Comma-separated TaskSpec field paths redacted from task-log events. |
| `WEFT_MANAGER_LIFETIME_TIMEOUT` | Default manager idle timeout. Must parse as a non-negative float. |
| `WEFT_MANAGER_REUSE_ENABLED` | Whether CLI-started managers stay alive after task completion. |
| `WEFT_AUTOSTART_TASKS` | Whether manager boot should consider autostart manifests under the active Weft metadata directory. |
| `WEFT_TASK_MONITOR_ENABLED` | Whether the canonical manager supervises the internal `TaskMonitor`. Defaults to true. |
| `WEFT_TASK_MONITOR_INTERVAL_SECONDS` | Heartbeat wake interval for the supervised task monitor. Must be at least the heartbeat minimum. |
| `WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS` | Short wake interval used while retained task-log backlog remains after a batch-limited cycle. Defaults to 2 seconds. |
| `WEFT_TASK_MONITOR_BATCH_SIZE` | Maximum retained task-log rows or cleanup candidates processed by one supervised monitor cycle. Defaults to 5000. |
| `WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT` | Maximum `weft.log.tasks` rows scanned by one supervised monitor cycle. Defaults to 50000. |
| `WEFT_TASK_MONITOR_STORE_WRITE_BATCH_SIZE` | Maximum Monitor-store updates written in one transaction while collating task-log lifecycle evidence. Defaults to 100. |
| `WEFT_TASK_MONITOR_STALE_OPEN_FAMILY_SECONDS` | Hard age before an open Monitor-table task family with no usable reporting interval is classified `stale_open`. Defaults to 604800 seconds. |
| `WEFT_TASK_MONITOR_CONTROL_QUEUE_DELETE_LIMIT` | Operator cap for each supervised runtime-queue cleanup slice from task-local `T{tid}.ctrl_in`, `T{tid}.ctrl_out`, eligible stale `T{tid}.reserved`, and eligible dead-TID queues. The monitor may apply a smaller internal per-slice cap and catch up through `WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS`. Defaults to 1000. |
| `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS` | Minimum age before supervised TaskMonitor may classify open task-log families or raw external rows. Terminal families may be summarized and retired as soon as the monitor reaches a complete FIFO high-water pass. Defaults to 172800 seconds. |
| `WEFT_TASK_MONITOR_RESERVED_CLEANUP_MIN_AGE_SECONDS` | Minimum age (terminal-evidence age where available, TID age as fallback) before supervised reserved-queue cleanup may delete a `T{tid}.reserved` row, keeping `ReservedPolicy.KEEP` evidence inspectable ([QUEUE.6], [OBS.13.5]). Must be non-negative; zero means immediate cleanup once cleanup proof exists. When unset, follows the configured `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS` so both gates agree; an explicit value wins. |
| `WEFT_LOG_TASKS_EXTERNAL_PATH` | JSONL file path for external task-log operational retention output. Defaults to `logs/weft.log` under the Weft project root. Relative paths resolve from the Weft project root. Changing this configured path requires a TaskMonitor restart; writability of the resolved path is re-probed on the monitor cadence. |
| `WEFT_LOG_TASKS_EXTERNAL_ENABLED` | Optional boolean override for external task-log logging. Defaults false except `WEFT_TASK_MONITOR_MODE=jsonl_then_delete`, where the external sink is enabled unless explicitly disabled. A path alone is not a logging trigger. |
| `WEFT_LOG_TASKS_EXTERNAL_MODE` | External task-log logging mode: `collated` or `raw`. Defaults to `collated`. |
| `WEFT_TASK_MONITOR_MODE` | Task-monitor mode. Defaults to `delete`. Built-ins are `report_only`, `delete`, and `jsonl_then_delete`; `delete` is the zero-config cleanup default and folds retained `weft.log.tasks` rows into Monitor-owned tables before exact deletion, keeps runtime-state cleanup policy-driven, and reports cached diagnostics in TaskMonitor PONG. `jsonl_then_delete` is the production audit-handoff preset when operators want task-lifetime JSONL records before cleanup; it requires `WEFT_LOG_TASKS_EXTERNAL_MODE=collated` and the Monitor collation store, and emits or durably defers one `task_lifetime_report` JSONL record before applying each exact destructive cleanup effect. `custom` runs the callable configured by `WEFT_TASK_MONITOR_PROCESSOR`. |
| `WEFT_TASK_MONITOR_PROCESSOR` | Custom task-monitor processor reference. Empty by default. Only valid with `WEFT_TASK_MONITOR_MODE=custom`; built-in modes must not be set through this key. Custom values use `module:function`. |
| `WEFT_TASK_MONITOR_LOG_SINK` | Operational output sink selector for monitor processors: `stdout`, `disk`, or `none`. |
| `WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS` | Manager restart backoff after the supervised monitor exits. |
| `WEFT_TASK_MONITOR_COLLATION_STORE_ENABLED` | Whether the supervised monitor creates/verifies and uses its Monitor-owned durable collation tables. Defaults to true. |
| `WEFT_TASK_MONITOR_MAINTENANCE` | Whether the supervised monitor runs periodic self-maintenance (backend vacuum plus runtime-state prune). Defaults to true. |
| `WEFT_TASK_MONITOR_MAINTENANCE_INTERVAL_SECONDS` | Minimum seconds between monitor self-maintenance passes. A wall-clock deadline, not a cycle count. Defaults to 3600 seconds. |
| `WEFT_DIRECTORY_NAME` | Name of the Weft metadata directory. Defaults to `.weft` and is used before project discovery. |
| `WEFT_LOGS_DIR` | Optional log-root override. Relative values resolve against the project root; absolute values are used directly. Defaults to `.weft/logs`. |
| `WEFT_DEFAULT_DB_LOCATION` | Broker default database location for SimpleBroker project resolution. |
| `WEFT_DEFAULT_DB_NAME` | Default sqlite broker path for explicit-root resolution and for legacy sqlite project auto-discovery when no Weft-scoped broker config owns the target. |
| `WEFT_PROJECT_CONFIG_PATH` | Optional override for the SimpleBroker project-config directory used by Weft. Defaults to `WEFT_DIRECTORY_NAME`. |
| `WEFT_PROJECT_CONFIG_NAME` | Optional override for the SimpleBroker project-config filename used by Weft. Defaults to `broker.toml`. |
| `WEFT_PROJECT_SCOPE` | Whether SimpleBroker should search upward for a project-scoped target. |
| `WEFT_BACKEND`, `WEFT_BACKEND_TARGET`, `WEFT_BACKEND_HOST`, `WEFT_BACKEND_PORT`, `WEFT_BACKEND_USER`, `WEFT_BACKEND_PASSWORD`, `WEFT_BACKEND_DATABASE`, `WEFT_BACKEND_SCHEMA` | Env-selected broker backend and connection details. They win for explicit-root resolution when no Weft-scoped broker config exists, but auto-discovery still prefers a Weft-scoped broker config or an existing legacy sqlite project first. |

Internal supervision inputs (not operator configuration) are deliberately not
indexed here; `WEFT_MANAGER_RUNTIME_HANDLE_JSON` is documented in
`05-Message_Flow_and_State.md`.

## Spec Breaking Notes

- Queue renames: `weft.tasks.log` → `weft.log.tasks`, `weft.workers.registry` →
  `weft.state.services`, `weft.state.process.tid_mappings` →
  `weft.state.tid_mappings`, `weft.state.streaming.sessions` →
  `weft.state.streaming`.
- Task state peak metrics renamed: `max_*` → `peak_*`.
- `spec.process_target` is now a **string** (executable path). `args` are
  appended to form argv. Current implementation follows this shape through
  `SpecSection` validation and `build_argv()`.

_Implementation mapping_: `weft/core/taskspec/model.py` (process_target, peak_* fields), `weft/core/targets.py` (argv construction).

## Related Plans

- [`docs/plans/2026-06-01-critical-review-remediation-plan.md`](../plans/2026-06-01-critical-review-remediation-plan.md)
- [`docs/plans/2026-05-29-task-monitor-config-and-reactor-cache-cleanup-plan.md`](../plans/2026-05-29-task-monitor-config-and-reactor-cache-cleanup-plan.md)
- [`docs/plans/2026-05-29-task-monitor-general-lifetime-reporting-plan.md`](../plans/2026-05-29-task-monitor-general-lifetime-reporting-plan.md)
- [`docs/plans/2026-05-30-task-monitor-mode-and-rotating-log-plan.md`](../plans/2026-05-30-task-monitor-mode-and-rotating-log-plan.md)
- [`docs/plans/2026-05-30-task-monitor-external-log-health-plan.md`](../plans/2026-05-30-task-monitor-external-log-health-plan.md)
- [`docs/plans/2026-05-29-reliability-and-doc-fixes-plan.md`](../plans/2026-05-29-reliability-and-doc-fixes-plan.md)
- [`docs/plans/2026-05-20-monitor-collation-table-retirement-plan.md`](../plans/2026-05-20-monitor-collation-table-retirement-plan.md)
- [`docs/plans/2026-05-20-monitor-fair-cleanup-scheduling-plan.md`](../plans/2026-05-20-monitor-fair-cleanup-scheduling-plan.md)
- [`docs/plans/2026-05-19-monitor-terminal-retirement-and-runtime-queue-cleanup-plan.md`](../plans/2026-05-19-monitor-terminal-retirement-and-runtime-queue-cleanup-plan.md)
- [`docs/plans/2026-05-16-task-log-external-logging-and-retention-policy-plan.md`](../plans/2026-05-16-task-log-external-logging-and-retention-policy-plan.md)
- [`docs/plans/2026-05-18-monitor-table-driven-retained-log-cleanup-plan.md`](../plans/2026-05-18-monitor-table-driven-retained-log-cleanup-plan.md)

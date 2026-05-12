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
- `weft.log.tasks` is runtime evidence used by Weft status, result, and
  debugging surfaces while retained. It is not legal, forensic, or audit
  evidence; retention is an operational policy.
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
(explicit task-local and task-log retention pruning).

## CLI Surface

Current top-level verbs and subcommands:

| Surface | Current commands |
|---------|------------------|
| `weft` | `init`, `status`, `result`, `run`, `queue`, `manager`, `spec`, `task`, `system` |
| `weft task` | `list`, `status`, `stop`, `kill`, `tid` |
| `weft manager` | `start`, `serve`, `stop`, `list`, `status` |
| `weft spec` | `create`, `list`, `show`, `delete`, `validate`, `generate` |
| `weft queue` | `read`, `write`, `peek`, `move`, `list`, `resolve`, `watch`, `delete`, `broadcast`, `alias add/list/remove` |
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
| `PING` | Health check; responds `PONG` with a live task-local status snapshot, echoes `request_id` for structured requests, and includes manager-selection fields for Manager tasks |
| `PAUSE` | Pause task processing |
| `RESUME` | Resume a paused task |

## Process Title Format

`weft-{context_short}-{tid_short}:{name}:{status}[:details]`

Format rules and sanitization live in `01-Core_Components.md`.

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `WEFT_DEBUG` | Enable debug output in Weft helpers and CLI surfaces. |
| `WEFT_LOGGING_ENABLED` | Enable Weft logging output. |
| `WEFT_REDACT_TASKSPEC_FIELDS` | Comma-separated TaskSpec field paths redacted from task-log events. |
| `WEFT_MANAGER_LIFETIME_TIMEOUT` | Default manager idle timeout. Must parse as a non-negative float. |
| `WEFT_MANAGER_REUSE_ENABLED` | Whether CLI-started managers stay alive after task completion. |
| `WEFT_AUTOSTART_TASKS` | Whether manager boot should consider autostart manifests under the active Weft metadata directory. |
| `WEFT_TASK_MONITOR_ENABLED` | Whether the canonical manager supervises the internal `TaskMonitorTask`. Defaults to true. |
| `WEFT_TASK_MONITOR_INTERVAL_SECONDS` | Heartbeat wake interval for the supervised task monitor. Must be at least the heartbeat minimum. |
| `WEFT_TASK_MONITOR_BATCH_SIZE` | Maximum rows scanned per supervised monitor cleanup queue and maximum task-log rows scanned by one lifecycle snapshot. |
| `WEFT_TASK_MONITOR_PROCESSOR` | Task-monitor processor name. Defaults to `delete`. Built-ins are `report_only`, `delete`, and `jsonl_then_delete`; `delete` removes exact rows selected by explicit TaskMonitor cleanup policies, with task-log collate running before broad older-than deletion and reporting operational collation summaries, while `jsonl_then_delete` remains fail-closed until the logging callback lands. Custom values use `module:function`. |
| `WEFT_TASK_MONITOR_LOG_SINK` | Operational output sink selector for monitor processors: `stdout`, `disk`, or `none`. |
| `WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS` | Manager restart backoff after the supervised monitor exits. |
| `WEFT_DIRECTORY_NAME` | Name of the Weft metadata directory. Defaults to `.weft` and is used before project discovery. |
| `WEFT_LOGS_DIR` | Optional log-root override. Relative values resolve against the project root; absolute values are used directly. Defaults to `.weft/logs`. |
| `WEFT_DEFAULT_DB_LOCATION` | Broker default database location for SimpleBroker project resolution. |
| `WEFT_DEFAULT_DB_NAME` | Default sqlite broker path for explicit-root resolution and for legacy sqlite project auto-discovery when no Weft-scoped broker config owns the target. |
| `WEFT_PROJECT_CONFIG_PATH` | Optional override for the SimpleBroker project-config directory used by Weft. Defaults to `WEFT_DIRECTORY_NAME`. |
| `WEFT_PROJECT_CONFIG_NAME` | Optional override for the SimpleBroker project-config filename used by Weft. Defaults to `broker.toml`. |
| `WEFT_PROJECT_SCOPE` | Whether SimpleBroker should search upward for a project-scoped target. |
| `WEFT_BACKEND`, `WEFT_BACKEND_TARGET`, `WEFT_BACKEND_HOST`, `WEFT_BACKEND_PORT`, `WEFT_BACKEND_USER`, `WEFT_BACKEND_PASSWORD`, `WEFT_BACKEND_DATABASE`, `WEFT_BACKEND_SCHEMA` | Env-selected broker backend and connection details. They win for explicit-root resolution when no Weft-scoped broker config exists, but auto-discovery still prefers a Weft-scoped broker config or an existing legacy sqlite project first. |

## Spec Breaking Notes

- Queue renames: `weft.tasks.log` → `weft.log.tasks`, `weft.workers.registry` →
  `weft.state.services`, `weft.state.process.tid_mappings` →
  `weft.state.tid_mappings`, `weft.state.streaming.sessions` →
  `weft.state.streaming`.
- Task state peak metrics renamed: `max_*` → `peak_*`.
- `spec.process_target` is now a **string** (executable path). `args` are
  appended to form argv. Implementation must be updated to match.

_Implementation mapping_: `weft/core/taskspec/model.py` (process_target, peak_* fields), `weft/core/targets.py` (argv construction).

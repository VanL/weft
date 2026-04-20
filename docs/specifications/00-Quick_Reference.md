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
| `weft.log.tasks` | Global task log / state events | Yes |
| `weft.spawn.requests` | Manager spawn requests | Yes |
| `weft.manager.ctrl_in` | Manager control input | Yes |
| `weft.manager.ctrl_out` | Manager control output | Yes |
| `weft.manager.outbox` | Manager informational output | Yes |
| `weft.state.managers` | Active manager registry | No (runtime state) |
| `weft.state.tid_mappings` | Short→full TID mappings | No (runtime state) |
| `weft.state.endpoints` | Active named endpoint registry | No (runtime state) |
| `weft.state.streaming` | Active streaming sessions | No (runtime state) |
| `weft.state.pipelines` | Active pipeline registry | No (runtime state) |

Notes:
- `weft.state.*` queues are runtime state and are excluded from dumps by default.
- named endpoint records resolve stable project-local names to ordinary
  task-local queues.
- Full queue behaviors are defined in `05-Message_Flow_and_State.md`.

_Implementation mapping_: `weft/_constants.py` (global queue constants),
`weft/core/tasks/base.py` (queue wiring and runtime endpoint registration),
`weft/core/endpoints.py` (named endpoint resolution),
`weft/core/manager.py` (manager registry), `weft/core/pipelines.py`
(pipeline queue compilation), `weft/core/tasks/pipeline.py`
(pipeline runtime queues).

## CLI Surface

Current top-level verbs and subcommands:

| Surface | Current commands |
|---------|------------------|
| `weft` | `init`, `status`, `result`, `run`, `queue`, `manager`, `spec`, `task`, `system` |
| `weft task` | `list`, `status`, `stop`, `kill`, `tid` |
| `weft manager` | `start`, `serve`, `stop`, `list`, `status` |
| `weft spec` | `create`, `list`, `show`, `delete`, `validate`, `generate` |
| `weft queue` | `read`, `write`, `peek`, `move`, `list`, `resolve`, `watch`, `delete`, `broadcast`, `alias add/list/remove` |
| `weft system` | `tidy`, `dump`, `builtins`, `load` |

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
| `STATUS` | Emit current status on `ctrl_out` |
| `PING` | Health check (responds `PONG`) |
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
| `WEFT_DIRECTORY_NAME` | Name of the Weft metadata directory. Defaults to `.weft` and is used before project discovery. |
| `WEFT_DEFAULT_DB_LOCATION` | Broker default database location for SimpleBroker project resolution. |
| `WEFT_DEFAULT_DB_NAME` | Default sqlite broker path for explicit-root resolution and for legacy sqlite project auto-discovery when no `.broker.toml` owns the target. |
| `WEFT_PROJECT_SCOPE` | Whether SimpleBroker should search upward for a project-scoped target. |
| `WEFT_BACKEND`, `WEFT_BACKEND_TARGET`, `WEFT_BACKEND_HOST`, `WEFT_BACKEND_PORT`, `WEFT_BACKEND_USER`, `WEFT_BACKEND_PASSWORD`, `WEFT_BACKEND_DATABASE`, `WEFT_BACKEND_SCHEMA` | Env-selected broker backend and connection details. They win for explicit-root resolution when no `.broker.toml` exists, but auto-discovery still prefers a project `.broker.toml` or an existing legacy sqlite project first. |

## Spec Breaking Notes

- Queue renames: `weft.tasks.log` → `weft.log.tasks`, `weft.workers.registry` →
  `weft.state.managers`, `weft.state.process.tid_mappings` →
  `weft.state.tid_mappings`, `weft.state.streaming.sessions` →
  `weft.state.streaming`.
- Task state peak metrics renamed: `max_*` → `peak_*`.
- `spec.process_target` is now a **string** (executable path). `args` are
  appended to form argv. Implementation must be updated to match.

_Implementation mapping_: `weft/core/taskspec/model.py` (process_target, peak_* fields), `weft/core/targets.py` (argv construction).

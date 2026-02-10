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
| `T{tid}.ctrl_in` | Control messages (STOP, STATUS, PING) |
| `T{tid}.ctrl_out` | Control responses |

Global queues:

| Name | Purpose | Persisted in dump? |
|------|---------|--------------------|
| `weft.log.tasks` | Global task log / state events | Yes |
| `weft.spawn.requests` | Manager spawn requests | Yes |
| `weft.manager.ctrl_in` | Manager control input | Yes |
| `weft.manager.ctrl_out` | Manager control output | Yes |
| `weft.manager.outbox` | Manager informational output | Yes |
| `weft.state.workers` | Active manager registry | No (runtime state) |
| `weft.state.tid_mappings` | Short→full TID mappings | No (runtime state) |
| `weft.state.streaming` | Active streaming sessions | No (runtime state) |

Notes:
- `weft.state.*` queues are runtime state and are excluded from dumps by default.
- Full queue behaviors are defined in `05-Message_Flow_and_State.md`.

_Implementation mapping_: `weft/_constants.py` (global queue constants), `weft/core/tasks/base.py` (queue wiring), `weft/core/manager.py` (worker registry).

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
| `STATUS` | Emit current status on `ctrl_out` |
| `PING` | Health check (responds `PONG`) |

Reserved for future: `PAUSE`, `RESUME`.

## Process Title Format

`weft-{context_short}-{tid_short}:{name}:{status}`

Format rules and sanitization live in `01-Core_Components.md`.

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `WEFT_DIR` | Default project directory |
| `WEFT_DB` | Database filename |
| `WEFT_TIMEOUT` | Default task timeout |
| `WEFT_WORKERS` | Default worker/manager count |
| `WEFT_LOG_LEVEL` | Logging level |

## Spec Breaking Notes

- Queue renames: `weft.tasks.log` → `weft.log.tasks`, `weft.workers.registry` →
  `weft.state.workers`, `weft.state.process.tid_mappings` →
  `weft.state.tid_mappings`, `weft.state.streaming.sessions` →
  `weft.state.streaming`.
- Task state peak metrics renamed: `max_*` → `peak_*`.
- `spec.process_target` is now a **string** (executable path). `args` are
  appended to form argv. Implementation must be updated to match.

_Implementation mapping_: `weft/core/taskspec.py` (process_target, peak_* fields), `weft/core/targets.py` (argv construction).

# Weft

*A durable task execution system. Persistent workers, multiprocess isolation, comprehensive observability.*

```bash
$ weft run echo "hello world"
hello world

$ weft run --spec task.json
# streams task output and returns when complete

$ weft run --spec review-agent.json
# runs an agent TaskSpec through the normal manager/task path

$ weft status
System: OK
```

Weft is a queue-based task execution system focused on enabling interaction between AI agents, user-provided functions, and existing CLI tools. It combines the simplicity of direct command execution with the power of durable task queues, multiprocess isolation, and comprehensive state tracking.


## Quick Start

```bash
# Initialize project
$ weft init
Initialized weft in .weft/

# Run a simple command
$ weft run echo "hello world"
hello world

# Run with resource limits
$ weft run --memory 100 --cpu 50 python script.py

# Run and wait for completion
$ weft run --wait --timeout 30 ./long-task.sh

# Run a saved task spec (from .weft/tasks/)
$ weft run --spec data-cleanup

# Run a pipeline spec (from .weft/pipelines/)
$ weft run --pipeline etl-job

# Pipe stdin into an inline command, stored spec, or pipeline
$ printf "hello\n" | weft run -- python summarize.py
$ printf "hello\n" | weft run --spec summarize.json
$ printf "hello\n" | weft run --pipeline etl-job

# Run a Python function
$ weft run --function mymodule:process_data --arg input.csv --kw mode=fast

# Run an agent TaskSpec
$ weft run --spec review-agent.json

# Check system status
$ weft status
System: OK

# Get task result
$ weft result 1234567890
{"status": "completed", "return_code": 0, "output": "..."}
```

## Core Concepts

### Project Context

Weft uses `.weft/` directories for project isolation, similar to git repositories:

```text
myproject/
  .weft/
    broker.db        # SQLite default broker file
    tasks/           # Saved task specs
    pipelines/       # Saved pipeline specs
    autostart/       # Autostart manifests (lifecycle + defaults)
    outputs/         # Large output spillover
    logs/            # Centralized logging
    ...
```

Run `weft` commands from anywhere in the project tree - it searches upward to find `.weft/`.

For non-file-backed backends such as Postgres, `.weft/` still holds Weft
metadata, logs, outputs, and autostart manifests, but the broker target is
resolved through SimpleBroker project configuration rather than assuming an
on-disk `.weft/broker.db`.

### Task IDs (TIDs)

Every task receives a unique 64-bit SimpleBroker timestamp (hybrid microseconds + logical counter), typically 19 digits:

- **Full TID**: `1837025672140161024` (Unique task ID)
- **Short TID**: `0161024` (last 10 digits for convenience)
- Used for correlation across queues and process titles
- Monotonic within a context, format-compatible with time.time_ns()
- The spawn-request message ID becomes the task TID for the full lifecycle

### Queue Structure

Each task gets its own queues:

```
T{tid}.inbox      # Work messages to process
T{tid}.reserved   # Messages being processed (reservation pattern)
T{tid}.outbox     # Results and output
T{tid}.ctrl_in    # Control commands (STOP, STATUS, PING)
T{tid}.ctrl_out   # Status responses

weft.log.tasks           # Global state log (all tasks)
weft.spawn.requests      # Task spawn requests to manager
weft.state.workers       # Manager liveness tracking (runtime state)
weft.state.tid_mappings  # Short->full TID mappings (runtime state)
weft.state.streaming     # Active streaming sessions (runtime state)
```

Queues under `weft.state.*` are runtime-only and excluded from dumps by default.

### Reservation Pattern

Weft implements inbox -> reserved -> outbox flow for reliable message processing:

1. **Reserve**: Move message from inbox to reserved
2. **Process**: Execute work while message is in reserved
3. **Complete**: Write output to outbox, delete from reserved (or apply policy)

If a task crashes mid-work, the message remains in reserved for manual recovery or explicit requeue.

**Idempotency guidance**
- Single-message tasks may use `tid` as an idempotency key.
- Multi-message tasks should use the inbox/reserved message ID (timestamp).
- Recommended composite key: `tid:message_id`.

Configurable policies (`keep`, `requeue`, `clear`) control reserved queue behavior on errors.

### Managers

Persistent worker processes that:
- Monitor `weft.spawn.requests` for new tasks
- Launch child task processes
- Track process lifecycle
- Auto-terminate after idle timeout (default 600 seconds)
- Launch autostart tasks on boot

### Process Titles

Tasks update their process title for observability:

```bash
$ ps aux | grep weft
weft-proj-0161024:mytask:running
weft-proj-0161025:worker:completed
```

Format: `weft-{context_short}-{short_tid}:{name}:{status}[:details]`

## Command Reference

### Project Management

```bash
# Initialize new project
weft init [--autostart/--no-autostart]

# Show system status
weft status [--json]

# Task detail view
weft task status TID [--process] [--watch] [--json]

# List tasks
weft list [--stats] [--status STATUS] [--json]

# System maintenance
weft system tidy            # backend-native broker compaction
weft system dump -o FILE
weft system load -i FILE    # preflight import; exits 3 on alias conflicts
```

### Task Execution

```bash
# Run command
weft run COMMAND [args...]
weft run --spec NAME|PATH
weft run --pipeline NAME|PATH
weft run --function module:func [--arg VALUE] [--kw KEY=VALUE]

# Pipe raw stdin as the initial task input
printf "hello\n" | weft run -- python worker.py
printf "hello\n" | weft run --spec summarize.json
printf "hello\n" | weft run --pipeline etl-job
# --input is still available for structured pipeline input and cannot be mixed
# with piped stdin.

# Execution options
--wait              # Wait for completion
--timeout N         # Timeout in seconds
--memory N          # Memory limit in MB
--cpu N            # CPU limit (percentage)
--env KEY=VALUE      # Environment variable
--autostart/--no-autostart  # Enable/disable autostart manifests
--arg VALUE          # Positional arg for --function (repeatable)
--kw KEY=VALUE       # Keyword arg for --function (repeatable)

# Get results
weft result TID [--timeout N] [--stream] [--json]
weft result --all [--peek]
```

### Queue Operations

```bash
# Direct queue access
weft queue read QUEUE [--json] [--all]
weft queue write QUEUE [MESSAGE]
weft queue peek QUEUE [--json] [--all]
weft queue move SOURCE DEST [--all]
weft queue list [--pattern PATTERN]
weft queue watch QUEUE [--json] [--peek]

# Broadcast and aliases
weft queue broadcast [MESSAGE] [--pattern GLOB]
printf "payload" | weft queue write QUEUE
printf "STOP" | weft queue broadcast --pattern 'T*.ctrl_in'
weft queue alias add ALIAS TARGET
weft queue alias remove ALIAS
weft queue alias list [--target QUEUE]
```

## Testing

Weft now classifies backend coverage explicitly:

- `shared`: backend-agnostic tests intended to pass on both SQLite and Postgres
- `sqlite_only`: tests that intentionally validate SQLite or file-backed behavior

The default local suite remains SQLite-first. To run the audited shared suite
against Postgres, use [`bin/pytest-pg`](./bin/pytest-pg), which provisions a
temporary Docker Postgres instance and installs `simplebroker-pg[dev]` into the
test environment alongside the published `simplebroker` dependency declared by
Weft.

### Autostart Tasks

Manifest files in `.weft/autostart/*.json` are automatically launched when the manager starts. Autostart targets must reference stored task specs or pipelines (no inline TaskSpecs).

```bash
# Save a task spec
$ cat > .weft/tasks/queue-monitor.json <<EOF
{
  "name": "queue-monitor",
  "spec": {
    "type": "function",
    "function_target": "monitoring.watch_queues",
    "timeout": null
  }
}
EOF

# Create autostart manifest
$ cat > .weft/autostart/monitor.json <<EOF
{
  "name": "queue-monitor",
  "target": { "type": "task", "name": "queue-monitor" },
  "policy": { "mode": "ensure" }
}
EOF

# Next manager start will launch it automatically
$ weft run echo "trigger manager"
```

Control autostart behavior:
- `weft init --no-autostart` - Skip autostart directory creation
- `weft run --no-autostart` - Skip launching autostart tasks
- `WEFT_AUTOSTART_TASKS=false` - Disable via environment

## TaskSpec Format

Tasks are configured with JSON specifications:

```json
{
  "name": "process-data",
  "spec": {
    "type": "command",
    "process_target": "python",
    "args": ["process.py"],
    "timeout": 300,
    "limits": {
      "memory_mb": 512,
      "cpu_percent": 75,
      "max_fds": 100
    },
    "env": {"LOG_LEVEL": "debug"},
    "stream_output": true,
    "cleanup_on_exit": true
  }
}
```

**Spec fields:**
- `type`: `"command"`, `"function"`, or `"agent"`
- `process_target`: Command executable (for commands)
- `function_target`: Module:function string (for functions)
- `agent`: Static agent runtime config (for agent tasks)
- `args`: Additional argv items (appended for commands, *args for functions)
- `keyword_args`: Keyword args for function targets
- `timeout`: Seconds (null for no timeout)
- `limits`: Resource constraints
- `env`: Environment variables
- `stream_output`: Enable output streaming
- `cleanup_on_exit`: Delete empty queues on completion (outbox retained until consumed)
- `weft_context`: Runtime-expanded project context (set by Manager)

**Runtime expansion:**
- TaskSpec templates omit `tid`, `io`, `state`, and `spec.weft_context`.
- The Manager expands these at spawn time. The spawn-request message ID becomes the task TID.

## State Tracking

All state changes are logged to `weft.log.tasks`:

```json
{
  "event": "work_completed",
  "tid": "1837025672140161024",
  "tid_short": "0161024",
  "status": "completed",
  "timestamp": 1705329000123456789,
  "taskspec": {...},
  "task_pid": 12345,
  "return_code": 0
}
```

Events include:
- `task_initialized` - Task startup
- `work_started` - Processing begins
- `work_completed` - Success
- `work_failed` - Execution error
- `work_timeout` - Timeout exceeded
- `work_limit_violation` - Resource limit hit
- `control_*` - Control message handling


## Resource Monitoring

Tasks track resource usage with psutil:

```python
# Resource limits in TaskSpec
"limits": {
  "memory_mb": 512,        # Max memory
  "cpu_percent": 75,       # Max CPU (0-100)
  "max_fds": 100,          # Max file descriptors
  "max_connections": 50    # Max network connections
}
```

Violations trigger `work_limit_violation` events and task termination.

## Exit Codes

```bash
0   - Success
1   - General error
2   - Not found (task, queue, spec)
124 - Timeout
```

## Common Patterns

### Background Processing

```bash
# Launch task without waiting
$ weft run ./background-job.sh

# Check status later
$ weft status
Tasks: 1 running

# Get result when ready
$ weft result <tid>
```

### Pipeline Processing

```bash
# Task chain via queues
$ weft run --spec extract.json    # Writes to "raw.data"
$ weft run --spec transform.json  # Reads "raw.data", writes "clean.data"
$ weft run --spec load.json       # Reads "clean.data"

# Or use queue operations directly
$ weft queue write input.queue "data.csv"
$ weft run --spec processor.json
$ weft queue read output.queue
```

### Persistent Watchers

```bash
# Save task spec
$ cat > .weft/tasks/file-watcher.json <<EOF
{
  "name": "file-watcher",
  "spec": {
    "type": "function",
    "function_target": "watchers.watch_directory",
    "timeout": null
  }
}
EOF

# Create autostart manifest
$ cat > .weft/autostart/file-watcher.json <<EOF
{
  "name": "file-watcher",
  "target": { "type": "task", "name": "file-watcher" },
  "policy": { "mode": "ensure" }
}
EOF

# Starts automatically with manager
$ weft run echo "start"

# Verify running
$ ps aux | grep weft
weft-proj-1234567:file-watcher:running
```

### Resource-Constrained Execution

```bash
# Limit memory and CPU
$ weft run --memory 100 --cpu 25 ./memory-intensive.py

# With timeout
$ weft run --timeout 60 --memory 500 ./task.sh
```

## Architecture

### Components

- **TaskSpec**: Validated task configuration with partial immutability
- **Manager**: Persistent worker process for task spawning
- **Consumer**: Task executor with reservation pattern
- **BaseTask**: Abstract base providing queue wiring and state tracking
- **TaskRunner**: Multiprocess execution wrapper with timeout/monitoring
- **ResourceMonitor**: psutil-based resource tracking and limit enforcement

### Task Lifecycle

```
1. CLI: weft run COMMAND
2. Manager auto-started if needed
3. TaskSpec template created and validated
4. Spawn request written to weft.spawn.requests (message ID becomes TID)
5. Manager expands TaskSpec and spawns Consumer process
6. Consumer reserves work from inbox
7. TaskRunner executes in child process
8. Output written to outbox
9. State logged to weft.log.tasks
10. CLI retrieves result
```

### Multiprocess Isolation

Tasks execute in separate processes using `multiprocessing.spawn`:
- Clean process environment
- No inherited state from parent
- Resource monitoring per process
- Crash isolation
- Timeout enforcement

## Development

```bash
# Install development dependencies
uv sync --all-extras

# Run tests
uv run pytest
uv run pytest tests/tasks/
uv run pytest tests/cli/

# Linting
uv run ruff check weft tests
uv run ruff format weft tests
uv run mypy weft

# Build
uv build
```

## Release

Weft uses a tag-driven release flow in GitHub Actions:

- [`.github/workflows/release-gate.yml`](./.github/workflows/release-gate.yml)
  runs on pushed `v*` tags, executes the full SQLite suite plus the
  PG-compatible suite via [`bin/pytest-pg`](./bin/pytest-pg), and only creates
  the GitHub Release if both pass.
- [`.github/workflows/release.yml`](./.github/workflows/release.yml) runs when
  that GitHub Release is published and handles package build, PyPI/TestPyPI
  publishing, signing, and artifact upload.

```bash
# Validate, update pyproject.toml + weft/_constants.py, commit, tag, and push
uv run python bin/release.py --version 0.1.1

# Preview the release steps without changing files or running commands
uv run python bin/release.py --version 0.1.1 --dry-run
```

The release helper updates both canonical version sources:

- `pyproject.toml`
- `weft/_constants.py`

After the helper pushes `v0.1.1`, the release gate workflow will:

1. Run the full SQLite suite
2. Run the PG-compatible suite with `uv run bin/pytest-pg --all`
3. Create the GitHub Release if both suites pass

Publishing the GitHub Release then triggers the package release workflow,
which will:

1. Build distributions with `uv build`
2. Publish to PyPI with `uv publish --trusted-publishing always dist/*`
3. Publish to TestPyPI with `uv publish --trusted-publishing always --publish-url https://test.pypi.org/legacy/ dist/*`
4. Sign artifacts and upload them to the GitHub release

Prerequisite:

- Configure PyPI/TestPyPI Trusted Publishers for this GitHub repository/workflow.

## Configuration

Environment variables:

- `WEFT_MANAGER_LIFETIME_TIMEOUT` - Manager idle timeout (default: 600s)
- `WEFT_MANAGER_REUSE_ENABLED` - Keep manager running (default: true)
- `WEFT_AUTOSTART_TASKS` - Enable autostart (default: true)

## License

MIT

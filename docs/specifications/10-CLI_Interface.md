# CLI Interface Specification

## Overview [CLI-0]

The Weft CLI follows SimpleBroker's design philosophy: simple, intuitive commands that work with Unix pipes and require zero configuration. The CLI provides straightforward access to task management, queue operations, and system monitoring.

_Implementation snapshot_: Current code implements queue helpers (`weft/commands/queue.py`), task submission via `weft run` (`weft/commands/run.py`), status/list/result reporting, spec/task/system management, and sequential pipeline execution. Task submission always flows through SimpleBroker queues: the CLI **Client** builds a validated TaskSpec template, enqueues it for the Manager, and the spawn-request message ID becomes the task TID. The Client can optionally wait for completion by watching the task log/outbox queues.

_Implementation mapping_: `weft/cli.py` (command registration), `weft/commands/run.py`, `weft/commands/status.py`, `weft/commands/result.py`, `weft/commands/queue.py`, `weft/commands/worker.py`, `weft/commands/tasks.py`, `weft/commands/specs.py`, `weft/commands/init.py`, `weft/commands/dump.py`, `weft/commands/load.py`, `weft/commands/tidy.py`, `weft/commands/validate_taskspec.py` (spec validate).

_Implemented commands_: `init`, `run`, `status`, `result`, `list`, `queue *`, `worker *`, `task *`, `spec *`, `system *`, `pipeline`.

## Design Principles [CLI-0.1]

1. **Zero Configuration** - Works immediately with `.weft/broker.db` in the project root
2. **Simple Verbs** - Use familiar commands: run, status, result, list
3. **Pipe-Friendly** - Input/output works with standard Unix tools
4. **JSON Safety** - All commands support `--json` for safe handling
5. **Exit Codes** - Meaningful codes for scripting (0=success, 1=error, 2=not found)
6. **SimpleBroker Integration** - Queue operations delegate to SimpleBroker
7. **Progressive Disclosure** - Power features (pipelines, stored task specs, control) are opt-in; the default path stays minimal.

## Command Structure [CLI-0.2]

```
weft [global-options] <command> [command-options] [arguments]
weft [global-options] task <subcommand> [arguments]
weft [global-options] spec <subcommand> [arguments]
weft [global-options] system <subcommand> [arguments]
```

## Global Options [CLI-0.3]

| Option | Description |
|--------|-------------|
| `-d, --dir PATH` | Use PATH as the project root (where `.weft/` lives) |
| `-f, --file NAME` | Database filename (default: `.weft/broker.db`) |
| `-q, --quiet` | Suppress non-error output |
| `--cleanup` | Delete database and exit |
| `--vacuum` | Clean up completed tasks and exit |
| `--version` | Show version information |
| `--help` | Show help message |

## Core Commands [CLI-1]

### Task Execution [CLI-1.1]

#### `run` - Execute a task [CLI-1.1.1]
_Implementation today_: `weft/commands/run.py` always routes execution through the Manager. The Client:

1. Collects CLI options and builds a canonical TaskSpec template (no `tid`, `io`, or `state`).
2. Validates it locally with `TaskSpec.model_validate(...)` so user errors fail fast.
3. Enqueues the template on the manager request queue.
4. Uses the spawn-request message ID returned by SimpleBroker as the task TID.

If `--wait` (current default) is provided the Client then tails `weft.log.tasks` and the task outbox queue until it observes a terminal event, streaming output as it arrives. When `--no-wait` is provided it simply enqueues the request, reports the TID, and returns; the Manager is unaware of the distinction.

```bash
# Run a command
weft run ls -la /tmp

# Run from stored task spec
weft run --spec git-clone

# Run from JSON spec
weft run --spec task.json

# Run a Python function
weft run --function mymodule:process_data --arg input.csv --kw mode=fast

# Pipe input
echo "data" | weft run process -

# With timeout
weft run --timeout 30 long-task

# With resource limits
weft run --memory 512 --cpu 50 heavy-task
```

**Options:**
- `-s, --spec NAME|PATH` - Execute a task spec by name or JSON file
- `-p, --pipeline NAME|PATH` - Execute a pipeline by name or JSON file
- `--function MODULE:FUNC` - Execute a Python function by import path
- `--arg VALUE` - Positional argument for `--function` (repeatable)
- `--kw KEY=VALUE` - Keyword argument for `--function` (repeatable)
- `--outbox QUEUE|@alias` - Route final output to the specified queue (implies `--no-wait`)
- `--timeout SECONDS` - Set execution timeout
- `--memory MB` - Set memory limit
- `--cpu PERCENT` - Set CPU limit (0-100)
- `--env KEY=VALUE` - Set environment variable
- `--wait` - Wait for completion before returning
- `--autostart/--no-autostart` - Enable or disable loading autostart manifests from `.weft/autostart/` when the Manager boots for this invocation

`--spec`, `--pipeline`, and `--function` are mutually exclusive with explicit
command arguments. `weft run` selects exactly one execution target. When
invoking `--function`, `--arg`/`--kw` may be repeated and can be interleaved;
`--arg` values preserve order, `--kw` values merge into a dict (last write wins).

**Resolution rules for NAME|PATH**
1. If the argument contains a path separator (`/`, `./`, `../`, `~`) or ends
   with `.json`, treat it as a file path.
2. Otherwise, treat it as a saved spec name.
3. If both a saved spec and a file exist with the same bare name, the saved
   spec wins. This avoids accidentally shadowing vetted saved specs with
   arbitrary local files. To force a file, use an explicit path (`./name.json`).

When `--outbox` is supplied, the CLI resolves `@alias` values to canonical
queue names before submission. The task writes its final output to the resolved
outbox queue, `weft run` returns the task TID, and no local result is printed.

Autostart manifests live in `.weft/autostart/`. `weft init` materializes the
directory (unless `--no-autostart` is supplied) so operators can drop manifest
JSON files that should be launched whenever a manager starts. Manifests declare
`once` or `ensure` lifecycle policy and point at task specs/pipelines. `weft run
--no-autostart` temporarily disables the feature, allowing one-off runs without
booting background agents.

**Exit codes:**
- `0` - TaskSpec enqueued successfully (or completed if `--wait`)
- `1` - Worker unavailable or enqueue failure
- `2` - Invalid arguments or TaskSpec JSON

> **Note:** Bare-name shortcuts (for example `weft run git-clone ...`) and additional convenience flags remain planned; today only direct command/function/spec submission is implemented.

#### `init` - Initialize a project

```bash
# Initialize .weft/ in the current directory
weft init

# Initialize a specific directory
weft init /path/to/project
```

### Task Monitoring [CLI-1.2]

#### `status` - Broker status snapshot [CLI-1.2.1]

`weft status` answers "how is the system doing?" and can optionally filter task snapshots by TID or status. For detailed per-task inspection use `weft task status`.

_Implementation today_: `weft status` surfaces SimpleBroker metrics for the active project database, manager registry entries, and recent task snapshots from `weft.log.tasks`. Optional filters include TID (full or short), `--status`, and `--all` to include terminal tasks. `--watch` streams task events as they arrive.

```bash
# Default human-readable output
weft status

# JSON payload for scripting
weft status --json

# Filter to running tasks
weft status --status running

# Watch a single task
weft status T1837025672140161024 --watch

```

**Output fields:**
- `total_messages` – Total messages across all queues
- `last_timestamp` – Highest SimpleBroker timestamp observed (text output also shows the relative age)
- `db_size` – Size of the broker database file in bytes (text output also shows a human-friendly unit)
- JSON payload nests these under `broker` and includes `managers` and `tasks` arrays summarising registry entries and task snapshots.

**Options:**
- `-j, --json` – Emit the payload as JSON
- `--all` – Include terminal tasks in snapshots
- `--status STATUS` – Filter task snapshots by status
- `--watch` – Stream task events as they arrive
- `--interval SECONDS` – Polling interval for `--watch`

#### `result` - Get task output

```bash
# Get result (blocks until complete)
weft result T1837025672140161024

# With timeout
weft result T1837025672140161024 --timeout 30

# Stream output as it arrives
weft result T1837025672140161024 --stream

# Get all completed results (ignores queues still streaming)
weft result --all

# Peek at all results without consuming them
weft result --all --peek

# JSON output with metadata
weft result T1837025672140161024 --json
```

**Options:**
- `--timeout SECONDS` - Maximum wait time
- `--stream` - Stream output as it arrives
- `-a, --all` - Get all available (non-streaming) results
- `--peek` - Inspect `--all` output without consuming queue messages
- `-j, --json` - Include metadata in JSON format
- `--error` - Show stderr instead of stdout

When `--all` is provided, the CLI enumerates task outbox queues and filters out any queues that are still listed in `weft.state.streaming` (Implementation: `weft/commands/result.py` `_collect_all_results`).

#### `list` - List tasks

```bash
# Basic listing
weft list

# With statistics
weft list --stats

# Filter by status
weft list --status failed

# Show workers only
weft list --workers

# JSON output
weft list --json
```

**Output format:**
```
NAME                STATUS      STARTED           TIME
git-clone          running     2024-01-15 14:30  00:02:15
data-process       completed   2024-01-15 14:25  00:05:43
analyze-spec       failed      2024-01-15 14:20  00:00:12

Summary: 1 running, 1 completed, 1 failed
```

**Options:**
- `--stats` - Include per-status summary counts
- `--status STATUS` - Filter by status
- `--workers` - Show managers/workers only
- `-j, --json` - Emit the payload as JSON

### Task Operations (`weft task …`)

Task lifecycle and operator tooling live under the `task` namespace to keep a
clear separation between read-only status (`weft status`, `weft list`) and
state-changing controls. The spec standard is `weft task <subcommand>`.

#### `task stop` - Stop a task gracefully

```bash
weft task stop T1837025672140161024
weft task stop 0161024  # Short TID
weft task stop --all    # Stop all tasks
```

#### `task kill` - Force terminate a task

```bash
weft task kill T1837025672140161024
weft task kill --pattern "analyze"  # Kill matching tasks
```

Patterned invocations reuse `queue broadcast --pattern 'T*.ctrl_in'` to fan
control messages to every matching task. Patterns match canonical queue names;
use `@alias` when you want alias resolution before broadcast.

## Queue Operations

Direct queue access using SimpleBroker commands:

#### `queue` - Queue operations

_Implementation_: Implemented via `weft/commands/queue.py` (read, write, peek, move, list, watch) with additional helpers for selective broadcast and alias management.

```bash
# Read from queue
weft queue read T1837025672140161024.inbox

# Write to queue
weft queue write T1837025672140161024.ctrl_in "STOP"

# Peek at queue
weft queue peek T1837025672140161024.reserved

# Move between queues
weft queue move T1837025672140161024.reserved T1837025672140161024.inbox

# List all queues
weft queue list

# Watch queue for messages
weft queue watch T1837025672140161024.outbox

# Broadcast to matching queues using fnmatch-style pattern
weft queue broadcast STOP --pattern 'T*.ctrl_in'

# Manage aliases for pipeline wiring
weft queue alias add agent1.outbox T1837025672140161024.outbox
weft queue alias add agent2.inbox T1837025672140161024.outbox
weft queue alias list --target T1837025672140161024.outbox
weft queue alias remove agent1.outbox
```

**Notes:**
- Queue commands delegate directly to SimpleBroker's Queue API while respecting Weft context discovery.
- Broadcast patterns apply to canonical queue names (`T*.ctrl_in`). Use `@alias` when invoking other commands to resolve-friendly names first.
- Alias commands create durable mappings so multiple agents can rendezvous on a shared queue without extra move steps.

## Worker Management

The `worker` commands manage long-lived Manager instances (Spec: [WA-0]–[WA-4]). The naming of the CLI command remains `worker` for compatibility, even though the runtime role is now called Manager.

#### `worker start` – launch a Manager

```bash
# Launch a worker from TaskSpec JSON (runs in background by default)
weft worker start worker.json

# Run the worker in the foreground (Ctrl+C to stop)
weft worker start worker.json --foreground
```

#### `worker stop` – request a worker shutdown

```bash
# Gracefully stop the worker
weft worker stop W1837025672140161024

# Force terminate after timeout
weft worker stop W1837025672140161024 --force --timeout 2
```

#### `worker list` – view registered workers

```bash
weft worker list
weft worker list --json
```

#### `worker status` – inspect a specific worker

```bash
weft worker status W1837025672140161024
weft worker status W1837025672140161024 --json

Worker subcommands operate on the current project context.
```

#### Manager lifecycle (implicit)

`weft run` ensures a manager is available, starting one automatically when
needed. Operators who want explicit control can use `weft worker start|stop`.

## Task Process Tools (`weft task …`)

#### `task status` - Task details (optional process view)

`weft task status` answers "how is this task doing?" It always targets a
specific TID.

```bash
# Show a task status snapshot
weft task status T1837025672140161024

# Include process metrics (PID/CPU/MEM)
weft task status T1837025672140161024 --process

# Watch status updates
weft task status T1837025672140161024 --watch
```

**Output format (with --process):**
```
PID    TID_SHORT  NAME           STATUS    CPU   MEM    TIME
12345  0161024    git-clone      running   12%   45MB   00:02:31
```

#### `task tid` - TID operations

```bash
# Lookup full TID from short form
weft task tid 0161024

# Find TID for process
weft task tid --pid 12345

# Reverse lookup
weft task tid --reverse T1837025672140161024
```

## Spec Management (`weft spec …`)

Task specs (single-task) and pipeline specs (multi-stage) are managed under a
single namespace so authoring/validation stay discoverable without adding
top-level verbs. Running a stored spec still happens through `weft run`.

#### `spec create` - Store a task or pipeline spec

```bash
# Create from a TaskSpec JSON
weft spec create git-clone --type task --file git_clone.json

# Create a pipeline
weft spec create etl-job --type pipeline \
  --stage extract:s3_download \
  --stage transform:clean_data \
  --stage load:db_insert

# List / show / delete (all types)
weft spec list
weft spec show git-clone --type task
weft spec delete etl-job --type pipeline

# Run stored specs
weft run --spec git-clone --arg https://github.com/org/repo
weft run --pipeline etl-job --input data.csv
```

`weft spec list` shows both tasks and pipelines. Use `--type` to filter the
list or disambiguate collisions.

**Queue aliases in pipelines**

Pipeline stages may reference SimpleBroker queue aliases using the `@alias`
syntax when wiring inbox/outbox overrides or when supplying explicit queue
names for stage inputs.

Stage definitions may include an optional `io_overrides` object with
`inputs`/`outputs`/`control` keys. Queue values can use `@alias` and are resolved
before task submission.

```jsonc
{
  "name": "etl-job",
  "stages": [
    {
      "name": "extract",
      "task": "s3_download",
      "io_overrides": {
        "inputs": {"inbox": "@ingest.inbox"}
      }
    }
  ]
}
```

#### `spec validate` - Validate a TaskSpec or pipeline spec

```bash
weft spec validate task.json
weft spec validate pipeline.json
```

#### `spec generate` - Generate skeleton specs

```bash
weft spec generate --type task > task.json
weft spec generate --type pipeline > pipeline.json
```

## Batch Processing (Composition)

Weft composes with standard Unix tools for batch submission:

```bash
# Run a command per line
cat urls.txt | xargs -n1 weft run fetch-url

# Parallel execution
cat files.txt | xargs -P4 -n1 weft run process-file

# JSON TaskSpecs from a file
cat tasks.jsonl | while read -r line; do weft run --spec <(echo "$line"); done
```

## Configuration

Configuration is stored in `.weft/config.json` and environment variables
(`WEFT_*`, `BROKER_*`). Operators can edit the file directly or set env vars
for ephemeral overrides.

## System Maintenance (`weft system …`)

Maintenance commands live under a system namespace to keep the core surface
area minimal.

System dumps exclude `weft.state.*` queues by default (runtime-only state).

```bash
# Compact the broker database
weft system tidy

# Dump broker state
weft system dump --output weft.dump

# Load broker state
weft system load --input weft.dump
```

**Exit codes:** `system load` returns `3` on alias conflicts (existing alias points
to a different target) so callers can distinguish conflicts from general errors.

## Example Workflows

### Basic Task Execution

```bash
# Run a simple command
$ weft run echo "Hello, World!"
T1837025672140161024

$ weft result T1837025672140161024
Hello, World!
```

### Pipeline Processing

```bash
# Process log files
$ cat access.log | weft run parse-log - | weft run analyze -
T1837025672140161025

$ weft result T1837025672140161025 --json
```

### Failure Recovery

```bash
# Check failed tasks
$ weft list --status failed
T1837025672140161026: failed (timeout after 30s)

# Inspect the failure
$ weft queue peek T1837025672140161026.reserved

# Retry with longer timeout
```

### Batch Processing (Composition)

```bash
# Process multiple files in parallel
$ ls *.csv | xargs -P4 -n1 weft run process-csv

$ weft list --stats
Summary: 8 completed, 2 running
```

### Emergency Management

```bash
# Kill all failed tasks (shell-friendly!)
$ weft list --status failed | awk '{print $1}' | xargs weft task kill

# Or use standard Unix tools directly
$ pkill -f "weft-.*:failed"
```

## Environment Variables

Environment variables are listed in
[00-Quick_Reference.md](00-Quick_Reference.md).

## Exit Codes

- `0` - Success
- `1` - General error
- `2` - Not found (task, queue, spec)
- `124` - Timeout (follows GNU coreutils convention)
- `130` - Interrupted (Ctrl+C)

## JSON Output Format

All commands support `--json` for structured output:

```json
{
  "tid": "1837025672140161024",
  "tid_short": "0161024",
  "name": "git-clone",
  "status": "running",
  "started_at": 1837025672140161024,
  "pid": 12345,
  "resources": {
    "cpu_percent": 23.5,
    "memory_mb": 89.2,
    "open_files": 15
  },
  "queues": {
    "inbox": 0,
    "reserved": 0,
    "outbox": 12
  }
}
```

## Shell Completion

Typer provides completion helpers automatically. Use the built-in flags:

```bash
# Install completion for the current shell
weft --install-completion

# Show completion script for the current shell
weft --show-completion
```

## CLI Implementation Architecture

### Command Structure

```python
class WeftCLI:
    """Main CLI application following Click framework patterns."""
    
    def __init__(self):
        self.context = None  # Set via global options
        
    @click.group()
    @click.option('-d', '--dir', 'context_dir', 
                  help='Database directory')
    @click.option('-f', '--file', 'db_file', default='.weft/broker.db',
                  help='Database filename')
    @click.option('-q', '--quiet', is_flag=True,
                  help='Suppress non-error output')
    @click.pass_context
    def cli(ctx, context_dir, db_file, quiet):
        """Weft task execution system."""
        ctx.ensure_object(dict)
        ctx.obj['context'] = WeftContext(context_dir) if context_dir else WeftContext()
        ctx.obj['db_file'] = db_file
        ctx.obj['quiet'] = quiet
```

### Command Implementation Pattern

```python
@cli.command()
@click.argument('command_args', nargs=-1)
@click.option('--spec', type=click.File('r'), help='TaskSpec JSON file')
@click.option('--timeout', type=int, help='Timeout in seconds')
@click.option('--memory', type=int, help='Memory limit in MB')
@click.option('--cpu', type=int, help='CPU limit percentage')
@click.option('--wait', is_flag=True, help='Wait for completion')
@click.pass_context
def run(ctx, command_args, spec, timeout, memory, cpu, wait):
    """Execute a task."""
    context = ctx.obj['context']
    
    if spec:
        # Load from JSON file
        taskspec_data = json.load(spec)
        taskspec = TaskSpec.model_validate(taskspec_data)
    else:
        # Create from command arguments
        taskspec = create_taskspec_from_args(
            command_args, timeout, memory, cpu
        )
    
    # Submit task
    task_manager = Client(context)
    tid = task_manager.submit(taskspec)
    
    if wait:
        result = task_manager.wait_for_completion(tid)
        click.echo(result)
    else:
        click.echo(tid)
```

### Process Integration

```python
class ProcessIntegration:
    """Integration with OS process management."""
    
    def find_weft_processes(self, pattern: str = None) -> list[dict]:
        """Find weft processes using ps command."""
        try:
            # Use ps to find weft processes
            cmd = ["ps", "aux"]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            processes = []
            for line in result.stdout.splitlines():
                if "weft-" in line:
                    process_info = self.parse_ps_line(line)
                    if not pattern or pattern in process_info['name']:
                        processes.append(process_info)
            
            return processes
        except Exception as e:
            raise CLIError(f"Failed to list processes: {e}")
    
    def parse_ps_line(self, line: str) -> dict:
        """Parse ps output line for weft process."""
        parts = line.split()
        pid = int(parts[1])
        
        # Extract weft info from command
        command = " ".join(parts[10:])
        if "weft-" in command:
            # Parse: weft-{tid_short}:{name}:{status}
            weft_part = [p for p in parts if p.startswith("weft-")][0]
            tid_short, name, status = weft_part.split(":", 2)
            tid_short = tid_short.replace("weft-", "")
            
            return {
                "pid": pid,
                "tid_short": tid_short,
                "name": name,
                "status": status,
                "cpu": parts[2],
                "memory": parts[3],
                "command": command
            }
        
        return {"pid": pid, "command": command}
```

### TID Resolution

```python
class TIDResolver:
    """Resolve between short and full TIDs."""
    
    def __init__(self, context: WeftContext):
        self.context = context
        self.mapping_queue = context.get_queue("weft.state.tid_mappings")
    
    def resolve_short_tid(self, tid_short: str) -> str:
        """Resolve short TID to full TID."""
        # Search mapping queue
        for msg_str in self.mapping_queue.peek_all():
            try:
                mapping = json.loads(msg_str)
                if mapping.get("short") == tid_short:
                    return mapping["full"]
            except (json.JSONDecodeError, KeyError):
                continue
        
        raise CLIError(f"Short TID not found: {tid_short}")
    
    def find_tid_by_pid(self, pid: int) -> str:
        """Find TID for given process ID."""
        for msg_str in self.mapping_queue.peek_all():
            try:
                mapping = json.loads(msg_str)
                if mapping.get("pid") == pid:
                    return mapping["full"]
            except (json.JSONDecodeError, KeyError):
                continue
        
        raise CLIError(f"No TID found for PID: {pid}")
```

## Summary

The Weft CLI provides a complete interface for task management that:
- Follows SimpleBroker's proven patterns
- Works seamlessly with Unix tools
- Provides safe JSON output for scripting
- Enables emergency management via process titles
- Supports both simple and complex workflows

The CLI follows SimpleBroker's command patterns and integrates with standard Unix commands. It supports both simple interactive use and production automation scenarios.

## Related Documents

- **[00-Overview_and_Architecture.md](00-Overview_and_Architecture.md)** - System overview and design principles
- **[01-TaskSpec.md](01-TaskSpec.md)** - Task configuration specification
- **[02-Core_Components.md](02-Core_Components.md)** - Detailed component architecture
- **[03-Worker_Architecture.md](03-Worker_Architecture.md)** - Recursive worker model
- **[04-SimpleBroker_Integration.md](04-SimpleBroker_Integration.md)** - Queue system integration
- **[09-Implementation_Plan.md](09-Implementation_Plan.md)** - Development roadmap

# CLI Interface Specification

## Overview [CLI-0]

The Weft CLI follows SimpleBroker's design philosophy: simple, intuitive commands that work with Unix pipes and require zero configuration. The CLI provides straightforward access to task management, queue operations, and system monitoring.

_Implementation snapshot_: Current code implements the queue helpers (`weft/commands/queue.py`), task submission via `weft run` (`weft/commands/run.py`), and manager lifecycle helpers (`weft/commands/worker.py`). Task submission always flows through SimpleBroker queues: the CLI **Client** builds a validated `TaskSpec`, obtains a TID via `generate_timestamp()`, enqueues the JSON for the Manager, and optionally waits for completion by watching the task log/outbox queues. Additional commands described below remain planned.

## Design Principles [CLI-0.1]

1. **Zero Configuration** - Works immediately with `.broker.db` in current directory
2. **Simple Verbs** - Use familiar commands: run, status, result, list
3. **Pipe-Friendly** - Input/output works with standard Unix tools
4. **JSON Safety** - All commands support `--json` for safe handling
5. **Exit Codes** - Meaningful codes for scripting (0=success, 1=error, 2=not found)
6. **SimpleBroker Integration** - Queue operations delegate to SimpleBroker

## Command Structure [CLI-0.2]

```
weft [global-options] <command> [command-options] [arguments]
```

## Global Options [CLI-0.3]

| Option | Description |
|--------|-------------|
| `-d, --dir PATH` | Use PATH for database instead of current directory |
| `-f, --file NAME` | Database filename (default: `.broker.db`) |
| `-q, --quiet` | Suppress non-error output |
| `--cleanup` | Delete database and exit |
| `--vacuum` | Clean up completed tasks and exit |
| `--version` | Show version information |
| `--help` | Show help message |

## Core Commands [CLI-1]

### Task Execution [CLI-1.1]

#### `run` - Execute a task [CLI-1.1.1]
_Implementation today_: `weft/commands/run.py` always routes execution through the Manager. The Client:

1. Collects CLI options and builds a canonical `TaskSpec` dictionary (including generated queue names and metadata).
2. Validates it locally with `TaskSpec.model_validate(...)` so user errors fail fast.
3. Obtains a TID by calling `generate_timestamp()` on the SimpleBroker database or queue.
4. Serialises the `TaskSpec` to JSON and enqueues it on the manager request queue.

If `--wait` (current default) is provided the Client then tails `weft.tasks.log` and the task outbox queue until it observes a terminal event, streaming output as it arrives. When `--no-wait` is implemented it will simply enqueue the request, report the TID, and return; the Manager is unaware of the distinction.

```bash
# Run a command
weft run ls -la /tmp

# Run from template
weft run git-clone --repo https://github.com/user/repo

# Run from JSON spec
weft run --spec task.json

# Pipe input
echo "data" | weft run process -

# With timeout
weft run --timeout 30 long-task

# With resource limits
weft run --memory 512 --cpu 50 heavy-task
```

**Options:**
- `--spec FILE` - Read TaskSpec from JSON file
- `--timeout SECONDS` - Set execution timeout
- `--memory MB` - Set memory limit
- `--cpu PERCENT` - Set CPU limit (0-100)
- `--env KEY=VALUE` - Set environment variable
- `--tag TAG` - Add tag for filtering
- `--wait` - Wait for completion before returning

**Exit codes:**
- `0` - TaskSpec enqueued successfully (or completed if `--wait`)
- `1` - Worker unavailable or enqueue failure
- `2` - Invalid arguments or TaskSpec JSON

> **Note:** Template shortcuts (for example `weft run git-clone ...`) and additional convenience flags remain planned; today only direct command/function/spec submission is implemented.

### Task Monitoring [CLI-1.2]

#### `status` - Broker status snapshot [CLI-1.2.1]

_Implementation today_: `weft status` surfaces SimpleBroker's `--status` metrics for the active project database. The command queries the broker directly and formats the response itself so additional Weft-specific diagnostics can be layered in later. Task-level filtering remains planned.

```bash
# Default human-readable output
weft status

# JSON payload for scripting
weft status --json

# Inspect a different context directory
weft status --context /path/to/project
```

**Output fields:**
- `total_messages` – Total messages across all queues
- `last_timestamp` – Highest SimpleBroker timestamp observed (text output also shows the relative age)
- `db_size` – Size of the broker database file in bytes (text output also shows a human-friendly unit)

**Options:**
- `--json` – Emit the payload as JSON
- `--context PATH` – Resolve the Weft project from a specific directory

#### `result` - Get task output

```bash
# Get result (blocks until complete)
weft result T1837025672140161024

# With timeout
weft result T1837025672140161024 --timeout 30

# Stream output as it arrives
weft result T1837025672140161024 --stream

# Get all completed results
weft result --all

# JSON output with metadata
weft result T1837025672140161024 --json
```

**Options:**
- `--timeout SECONDS` - Maximum wait time
- `--stream` - Stream output as it arrives
- `--all` - Get all available results
- `--json` - Include metadata in JSON format
- `--error` - Show stderr instead of stdout

#### `list` - List tasks

```bash
# Basic listing
weft list

# With statistics
weft list --stats

# Group by status
weft list --by-status

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

### Task Control

#### `stop` - Stop a task gracefully

```bash
weft stop T1837025672140161024
weft stop 0161024  # Short TID
weft stop --all     # Stop all tasks
```

#### `kill` - Force terminate a task

```bash
weft kill T1837025672140161024
weft kill --pattern "analyze"  # Kill matching tasks
```

#### `pause` / `resume` - Pause and resume tasks

```bash
weft pause T1837025672140161024
weft resume T1837025672140161024
```

### Recovery Operations

#### `retry` - Retry a failed task

```bash
# Retry with same parameters
weft retry T1837025672140161024

# Retry with modifications
weft retry T1837025672140161024 --timeout 60
```

#### `recover` - Recover from reserved queue

_Implementation status_: Not yet implemented; manual queue commands must be used for recovery today.

```bash
# Inspect reserved queue
weft recover T1837025672140161024 --inspect

# Process reserved messages
weft recover T1837025672140161024 --process

# Abandon reserved messages
weft recover T1837025672140161024 --abandon
```

## Queue Operations

Direct queue access using SimpleBroker commands:

#### `queue` - Queue operations

_Implementation_: Implemented via `weft/commands/queue.py` (read, write, peek, move, list, watch).

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
```

These commands delegate directly to SimpleBroker's Queue API.

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

All worker subcommands accept `--context PATH` to target a specific project directory when automatic discovery is insufficient.
```

#### `bootstrap` - System initialization

```bash
# Start the primordial worker
weft bootstrap

# Start with custom configuration
weft bootstrap --config weft.toml

# Recovery mode
weft bootstrap --recover
```

## Process Management

_Implementation status_: `ps`, `tid`, `top`, and related tooling remain to be implemented.

#### `ps` - List weft processes

```bash
# List all weft processes
weft ps

# Filter by status
weft ps --failed
weft ps --running

# Filter by pattern
weft ps --pattern "analyze"

# Tree view
weft ps --tree
```

**Output format:**
```
PID    TID_SHORT  NAME           STATUS    CPU   MEM    TIME
12345  0161024    git-clone      running   12%   45MB   00:02:31
12346  0161025    analyze        running   78%   120MB  00:00:15
12347  0161026    compress       failed    0%    0MB    00:01:43
```

#### `tid` - TID operations

```bash
# Lookup full TID from short form
weft tid 0161024

# Find TID for process
weft tid --pid 12345

# Reverse lookup
weft tid --reverse T1837025672140161024
```

#### `top` - Live monitoring

```bash
# Interactive task monitor (like Unix top)
weft top

# Specific refresh rate
weft top --interval 2

# Sort by different fields
weft top --sort cpu
weft top --sort memory
```

## Pipeline Operations

#### `pipe` - Create task pipelines

```bash
# Unix-style pipeline
weft pipe grep "error" "|" sort "|" uniq -c

# From file
weft pipe --file pipeline.txt

# With intermediate results
weft pipe --save-intermediate process1 "|" process2
```

#### `pipeline` - Manage saved pipelines

```bash
# Create pipeline
weft pipeline create etl-job \
  --stage extract:s3_download \
  --stage transform:clean_data \
  --stage load:db_insert

# Run pipeline
weft pipeline run etl-job --input data.csv

# List pipelines
weft pipeline list

# Show pipeline
weft pipeline show etl-job
```

## Template Management

#### `template` - Manage task templates

```bash
# Create template
weft template create git-clone --spec git_clone.json

# List templates
weft template list

# Show template
weft template show git-clone

# Delete template
weft template delete git-clone

# Export/import templates
weft template export --all > templates.json
weft template import templates.json
```

## Batch Operations

#### `batch` - Submit multiple tasks

```bash
# From file (one JSON spec per line)
weft batch --file tasks.jsonl

# From stdin
cat urls.txt | weft batch fetch-url

# Parallel execution
weft batch --parallel 4 process file1.txt file2.txt file3.txt

# With common options
weft batch --timeout 30 --tag batch-001 --file tasks.jsonl
```

## Configuration

#### `config` - Manage configuration

```bash
# Show current configuration
weft config show

# Set configuration value
weft config set workers.max 10
weft config set monitoring.interval 5

# Load from file
weft config load weft.toml

# Reset to defaults
weft config reset
```

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
$ weft status --failed
T1837025672140161026: failed (timeout after 30s)

# Inspect the failure
$ weft queue peek T1837025672140161026.reserved

# Retry with longer timeout
$ weft retry T1837025672140161026 --timeout 60
```

### Batch Processing

```bash
# Process multiple files
$ ls *.csv | weft batch process-csv
Started 10 tasks

$ weft status --tag batch-001
10 tasks: 8 completed, 2 running
```

### Live Monitoring

```bash
# Monitor system
$ weft top
TID_SHORT  NAME         STATUS    CPU   MEM    TIME     QUEUES
0161024    worker-main  running   5%    120MB  12:34:56 in:10 out:45
0161025    git-clone    running   23%   89MB   00:02:15 in:0  out:12
0161026    analyze      running   67%   340MB  00:00:45 in:3  out:0

Tasks: 3 running, 0 pending, 15 completed
Workers: 1 active, 0 idle
Queues: 25 active, 142 messages
```

### Emergency Management

```bash
# Find stuck tasks
$ weft ps --running | grep -E "[0-9]{2}:[0-9]{2}:[0-9]{2}"

# Kill all failed tasks (shell-friendly!)
$ weft ps --failed | awk '{print $2}' | xargs weft kill

# Or use standard Unix tools directly
$ pkill -f "weft-.*:failed"
```

## Environment Variables

- `WEFT_DIR` - Default database directory
- `WEFT_DB` - Default database filename
- `WEFT_TIMEOUT` - Default task timeout
- `WEFT_WORKERS` - Default number of workers
- `WEFT_LOG_LEVEL` - Logging level (DEBUG, INFO, WARN, ERROR)

## Exit Codes

- `0` - Success
- `1` - General error
- `2` - Not found (task, queue, template)
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

Install shell completions:

```bash
# Bash
weft completion bash > /etc/bash_completion.d/weft

# Zsh
weft completion zsh > ~/.zsh/completions/_weft

# Fish
weft completion fish > ~/.config/fish/completions/weft.fish
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
    @click.option('-f', '--file', 'db_file', default='.broker.db',
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
        self.mapping_queue = context.get_queue("weft.tid.mappings")
    
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

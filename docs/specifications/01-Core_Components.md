# Core Components

This document details the fundamental components of the Weft system architecture.

## 1. TaskSpec (weft/core/taskspec.py) [CC-1]
**Purpose**: Task configuration with partial immutability - immutable spec, mutable state

_Implementation_: `weft/core/taskspec.py` (`TaskSpec`, `SpecSection`, `IOSection`, `StateSection`) implements these behaviours, including default expansion and partial immutability.

**Key Responsibilities**:
- Validate task configuration at creation
- Enforce spec immutability after initialization
- Track mutable execution state
- Provide convenience methods for safe state transitions
- Ensure data consistency across all operations

**Structure with Partial Immutability**:
```python
class TaskSpec:
    # Identity (immutable)
    tid: str              # 19-digit timestamp ID (nanosecond precision)
    name: str             # Human-readable identifier
    version: str          # Schema version (e.g., "1.0")
    
    # Configuration (immutable after creation)
    spec: SpecSection     # Frozen execution parameters
    io: IOSection         # Frozen queue mappings
    
    # Runtime State (mutable)
    state: StateSection   # Status and metrics - changes during execution
    metadata: dict        # User-defined key-value pairs - flexible updates
```

**SpecSection Structure with Limits Subsection**:
```python
class SpecSection:
    # Execution configuration
    type: Literal["function", "command"]
    function_target: str | None      # For type="function"
    process_target: list[str] | None # For type="command"
    args: list[Any]
    keyword_args: dict[str, Any]
    
    # Resource limits (grouped for clarity)
    limits: LimitsSection
    
    # Execution behavior
    timeout: float | None
    env: dict[str, str]
    working_dir: str | None
    stream_output: bool
    cleanup_on_exit: bool
    
    # Monitoring configuration
    polling_interval: float
    reporting_interval: Literal["poll", "transition"]
    monitor_class: str

class LimitsSection:
    """Resource limits for task execution."""
    memory_mb: int | None        # Max memory in MB
    cpu_percent: int | None      # Max CPU percentage (0-100)
    max_fds: int | None          # Max file descriptors
    max_connections: int | None  # Max network connections
```

**Immutability Enforcement**:
```python
def __init__(self, **data):
    super().__init__(**data)
    # Freeze spec after initialization
    self._freeze_spec()
    
def _freeze_spec(self):
    """Make spec and io sections immutable."""
    # Pydantic v2 approach
    self.model_fields['spec'].frozen = True
    self.model_fields['io'].frozen = True
```

**Key Invariants**:
- TID is immutable and unique across all tasks
- spec and io sections become immutable after TaskSpec creation
- state section remains mutable for runtime updates
- State transitions are forward-only (enforced by convenience methods)
- Required queues (outbox, ctrl_in, ctrl_out) always present
- All optional fields expanded to explicit values via apply_defaults()

## 2. Task Execution Architecture
Weft tasks build on top of SimpleBroker’s multi-queue watcher and are organised
as a small hierarchy of classes rather than a single monolithic `Task`.  This
section captures the responsibilities of each layer while preserving the
behaviour promised in the original design (multiprocess isolation, rich control
channels, and queue-based state reporting).

### 2.1 MultiQueueWatcher (weft/core/tasks/multiqueue_watcher.py) [CC-2.1]
**Purpose**: Provide a scheduler that can monitor multiple SimpleBroker queues,
each with its own processing semantics.

_Implementation_: `weft/core/tasks/multiqueue_watcher.py` supplies `MultiQueueWatcher` with per-mode handling, queue registration, and shared database wiring aligned with this description.

**Capabilities**:
- Configurable per-queue processing modes (`READ`, `PEEK`, `RESERVE`).
- Round-robin scheduling with automatic detection of inactive queues.
- Queue-specific error handlers and reserved-queue plumbing.
- A `process_once()` / `_drain_queue()` primitive used by higher layers to run
  one scheduling cycle at a time.

This watcher remains the foundation for every task subtype; nothing in this
document changes the requirement to keep it feature-compatible with the original
SimpleBroker example while extending it for Weft’s needs.

### 2.2 BaseTask (weft/core/tasks/base.py) [CC-2.2]
**Purpose**: Bind a `TaskSpec` to the watcher, manage control/state reporting,
and provide shared utilities for concrete task types.

_Implementation_: `BaseTask` in `weft/core/tasks/base.py` handles queue wiring, control commands, state reporting, and emits process titles with the required context prefix.

**Key Responsibilities**:
- Translate `TaskSpec.io` into queue configurations (inbox reserve mode,
  reserved/control peek mode, outbox read/write).
- Maintain control-channel semantics (STOP, PAUSE, RESUME, STATUS) and emit JSON
  responses on `ctrl_out`.
- Update process titles and TID mappings (still expected to include the context
  prefix as described later in this spec).
- Record state transitions to `weft.tasks.log`, including a full `TaskSpec`
  snapshot to support event-sourced reconstruction.
- Expose `process_once()` and `run_until_stopped()` helpers; long-running
  workers are expected to loop on `run_until_stopped`, while single-shot CLI
  invocations may call `process_once()` directly.
- Honour future enhancements such as periodic reporting when
  `TaskSpec.spec.reporting_interval == "poll"` (not yet implemented).

BaseTask purposefully does *not* execute work itself.  Instead it delegates to
`TaskRunner` (see §3) from within `_handle_work_message`, allowing subclasses to
focus on the semantics of message handling rather than process orchestration.

### 2.3 Specialized Task Types [CC-2.3]
Concrete task types extend `BaseTask` to express different queue behaviours.

_Implementation_: `Consumer` (`weft/core/tasks/consumer.py`), `Observer` (`weft/core/tasks/observer.py`), `SelectiveConsumer` (`weft/core/tasks/observer.py`), `Monitor` (`weft/core/tasks/monitor.py`), and `SamplingObserver` (`weft/core/tasks/monitor.py`) provide the queue modes shown below while reusing the helpers exported from `weft/core/tasks/base.py`.

Interactive command sessions reuse `Consumer` with `spec.interactive=True`, streaming stdin/stdout via JSON envelopes rather than per-message execution.

| Class | Purpose | Queue Modes |
|-------|---------|-------------|
| `Consumer` | Default worker that reserves inbox messages, executes the target, and writes to the outbox. Applies reserved queue policies on success/failure. | Inbox `RESERVE`, Reserved `PEEK`, Control `PEEK` |
| `Observer` | Peeks at inbox messages without consuming them; useful for monitoring pipelines. | Inbox `PEEK`, Control `PEEK` |
| `SelectiveConsumer` | Peeks inbox messages and consumes them only when a selector predicate returns true. Optional callback for side effects. | Inbox `PEEK`, Control `PEEK` |
| `Monitor` | Forwards messages to a downstream queue while observing them (reserve mode to ensure at-most-once forwarding). | Inbox `RESERVE` → downstream, Control `PEEK` |
| `SamplingObserver` | Observer variant that invokes its callback at fixed intervals based on elapsed time. | Inherits `Observer` behaviour |

Future worker/task variants should continue to derive from `BaseTask` and reuse
its queue helpers (`_reserve_queue_config`, `_peek_queue_config`, `_read_queue_config`)
to maintain consistency.

### 2.4 Control and State Expectations [CC-2.4]
- **Control commands**: STOP, PAUSE, RESUME, STATUS, and the health-check `PING`
  round-trip remain mandatory. Additional commands may be layered on via
  `_handle_control_command` overrides.
- **State emission**: Each transition (start, completion, failure, timeout,
  reserved-policy event, etc.) must call `_report_state_change`.  Periodic
  (poll-based) reporting is still part of the roadmap even if the current
  implementation only reports on transitions.
- **Process titles**: The shell-friendly format
  `weft-{context_short}-{tid_short}:{name}:{status}[:details]` remains the target
  behaviour.  Implementations that currently omit the context must be updated to
  meet this requirement.
- **Reserved queue policies**: `KEEP`, `CLEAR`, and `REQUEUE` policies are
  enforced by `BaseTask._apply_reserved_policy` and must continue to behave as
  documented in `TaskSpec`.

_Implementation status_: STOP/PAUSE/RESUME/STATUS/PING handling, state emission,
reserved policies, and context-aware process title formatting are implemented in
`BaseTask`.

### 2.5 Execution Flow [CC-2.5]
At a high level a `Consumer` (and derivatives) execute the following steps:

1. Instantiate `BaseTask` with a fully validated `TaskSpec`.
2. Register queue handlers and initialize process-title/TID mapping.
3. On each inbox message:
   - Mark the task as running and emit `work_started`.
   - Delegate execution to `TaskRunner` (multiprocess isolation, resource
     monitoring, timeout enforcement).
   - Apply reserved-queue policy and emit state events (`work_completed`,
     `work_failed`, `work_timeout`, `work_limit_violation`, etc.).
4. Emit control responses for any control commands encountered.
5. Loop via `process_once()` / `run_until_stopped()` until `STOP` is received or
   the CLI/controller decides to exit.

The CLI command `weft run` already demonstrates both usage patterns: `--once`
invokes `process_once()`, while the default mode loops until interrupted.

_Implementation_: `Consumer._handle_work_message` (`weft/core/tasks/consumer.py`) and `cmd_run` (`weft/commands/run.py`) follow this flow.

## 3. TaskRunner (weft/core/tasks/runner.py) [CC-3]
**Purpose**: Managed wrapper around Python’s multiprocessing primitives for
executing TaskSpec targets with resource monitoring.

_Implementation_: `weft/core/tasks/runner.py` provides `TaskRunner` and `_worker_entry`, coordinating subprocess execution, timeout handling, and `ResourceMonitor` integration.

**Highlights**:
- Uses the `"spawn"` context to avoid inheriting state from the parent process.
- Executes either Python callables (`type="function"`) or external commands
  (`type="command"`) with optional args/kwargs overrides from the work item.
- Integrates with `weft.core.resource_monitor` to enforce limits defined in
  `TaskSpec.spec.limits`; returns `RunnerOutcome` statuses of `"ok"`, `"error"`,
  `"timeout"`, or `"limit"`.
- Captures stdout/stderr for command targets and returns structured metrics for
  logging.
- Designed to be invoked per work item from the `Consumer`’s `_handle_work_message`.

The long-term contract remains the same: tasks must run in isolated processes,
terminate cleanly on timeout or STOP, and surface sufficient telemetry for
operators to understand what happened.

### Process Title Expectations
```python
class ProcessTitleMixin:
    """Mixin for process title management functionality."""
    
    def _compute_short_tid(self) -> str:
        """Extract last N digits of TID for display.
        
        Returns:
            String of last TID_SHORT_LENGTH digits
            
        Rationale:
            Last digits change most frequently (nanosecond precision),
            providing better uniqueness for contemporary tasks.
        """
        return self.tid[-self.TID_SHORT_LENGTH:]
    
    def _check_setproctitle(self) -> bool:
        """Check if setproctitle is available.
        
        Returns:
            True if setproctitle can be imported and used
            
        Note:
            setproctitle is optional but strongly recommended.
            Falls back gracefully if not available.
        """
        try:
            import setproctitle
            return True
        except ImportError:
            logger.debug("setproctitle not available - process titles disabled")
            return False
    
    def _update_process_title(self, status: str, details: str = None) -> None:
        """Update the process title visible in ps/top/htop.
        
        Args:
            status: Current task status (init, running, failed, etc.)
            details: Optional additional details (e.g., "50% complete")
            
        Format:
            weft-{context_short}-{tid_short}:{name}:{status}
            weft-{context_short}-{tid_short}:{name}:{status}:{details}
            
        Examples:
            weft-proj1-0161024:git-clone:init
            weft-proj1-0161024:git-clone:running
            weft-myapi-0161024:analyze:failed:timeout
            weft-webui-0161025:process:running:50
            
        Note: Format avoids shell special characters for easier scripting.
        No brackets, parentheses, or other characters requiring escaping.
        """
        if not self.enable_process_title:
            return
            
        try:
            import setproctitle
            title = self._format_process_title(status, details)
            setproctitle.setproctitle(title)
            logger.debug(f"Process title updated: {title}")
        except Exception as e:
            logger.warning(f"Failed to update process title: {e}")
    
    def _format_process_title(self, status: str, details: str = None) -> str:
        """Format the process title string for shell-friendly usage.
        
        Args:
            status: Current status
            details: Optional details
            
        Returns:
            Formatted title string, sanitized for shell usage
            
        Format Design:
            - Hyphen after prefix for clear boundary
            - Context included for multi-project distinguishability
            - Colons separate logical sections
            - No special shell characters requiring escaping
            - Easy to parse with standard tools (cut, awk, grep)
        """
        import re
        from pathlib import Path
        
        # Extract context short name from weft_context path
        context_short = "unknown"
        if hasattr(self.taskspec.spec, 'weft_context') and self.taskspec.spec.weft_context:
            context_path = Path(self.taskspec.spec.weft_context)
            context_short = context_path.name[:8]  # Max 8 chars for context
            # Sanitize context name
            context_short = re.sub(r'[^a-zA-Z0-9_-]', '', context_short)
            if not context_short:
                context_short = "proj"  # Fallback
        
        # Start with prefix-context-tid format (hyphens separate components)
        parts = [f"{self.PROCESS_TITLE_PREFIX}-{context_short}-{self.tid_short}"]
        
        # Sanitize task name (remove special chars, spaces)
        max_name_length = 20
        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '', self.taskspec.name[:max_name_length])
        if not safe_name:
            safe_name = "task"  # Fallback if name becomes empty
        parts.append(safe_name)
        
        # Add status
        parts.append(status)
        
        # Add optional details (also sanitized)
        if details:
            safe_details = re.sub(r'[^a-zA-Z0-9_%-]', '', str(details)[:15])
            if safe_details:
                parts.append(safe_details)
        
        return ":".join(parts)
    
    def _register_tid_mapping(self) -> None:
        """Register TID mapping for reverse lookup.
        
        Saves mapping to weft.state.process.tid_mappings queue for tools that need
        to resolve short TIDs to full TIDs.
        
        Message format:
            {
                "short": "0161024",
                "full": "1837025672140161024",
                "pid": 12345,
                "name": "git-clone",
                "started": 1837025672140161024
            }
        """
        try:
            import socket
            mapping_queue = Queue(self.TID_MAPPING_QUEUE)
            mapping = {
                "short": self.tid_short,
                "full": self.tid,
                "pid": os.getpid(),
                "name": self.taskspec.name,
                "started": mapping_queue.generate_timestamp()
            }
            mapping_queue.write(json.dumps(mapping))
            logger.debug(f"Registered TID mapping: {self.tid_short} -> {self.tid}")
        except Exception as e:
            logger.warning(f"Failed to register TID mapping: {e}")
```

**Execution Flow**:
1. Initialize with TaskSpec
2. Set initial process title "weft-{context_short}-{tid_short}:name:init"
3. Register TID mapping to weft.state.process.tid_mappings queue
4. Mark status as "spawning" and update process title
5. Start monitoring thread
6. Execute target (function or command)
7. Update process title to "running"
8. Process queues continuously
   - Handle control messages (STOP, PAUSE, etc.)
   - Handle input queues (if provided), routing input to target stdin
   - Write output from target to identified output queue (default outbox)
   - Update state from monitoring thread and send to global task log queue (weft.tasks.log)
   - Periodically update process title with progress if available
9. Write final output/accumulated output to outbox
10. Update process title to terminal state (completed/failed/timeout/killed)
11. Update and report final state
12. Clean up resources, including clearing queues, if cleanup_on_exit is True
13. Exit (process title automatically cleared by OS)

## 3. Executor (weft/core/executor.py)
**Purpose**: Isolated execution of task targets with subprocess observability

**Types**:
- **FunctionExecutor**: Runs Python callables in separate process using multiprocess
- **CommandExecutor**: Runs system processes with title inheritance

**Key Features**:
- Process isolation for commands and functions
- Timeout and resource usage enforcement (Uses monitor class to measure resource usage)
- Stream capture (stdout/stderr)
- Environment variable injection
- Subprocess process title propagation

**Subprocess Process Title Management**:
```python
class CommandExecutor(Executor):
    def execute(self, target: list[str], tid: str, name: str) -> ExecutionResult:
        """Execute command with process title inheritance."""
        
        # For Unix systems - use preexec_fn
        def set_subprocess_title():
            """Set title in child process after fork but before exec."""
            import setproctitle
            from pathlib import Path
            tid_short = tid[-10:]
            
            # Extract context short name (same logic as main process)
            context_short = "unknown"
            if hasattr(taskspec.spec, 'weft_context') and taskspec.spec.weft_context:
                context_path = Path(taskspec.spec.weft_context)
                context_short = context_path.name[:8]
                import re
                context_short = re.sub(r'[^a-zA-Z0-9_-]', '', context_short) or "proj"
            
            setproctitle.setproctitle(f"weft-worker-{context_short}-{tid_short}:{target[0]}")
        
        # Execute with title
        if os.name != 'nt':  # Unix/Linux/macOS
            process = subprocess.Popen(
                target,
                preexec_fn=set_subprocess_title,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
        else:  # Windows
            process = self._execute_with_wrapper(target, tid, name)
            
        return self._wait_and_capture(process)
    
    def _execute_with_wrapper(self, target: list[str], tid: str, name: str):
        """Windows wrapper for process title setting."""
        wrapper_script = f"""
import setproctitle
import subprocess
import sys
from pathlib import Path
import re

tid_short = "{tid[-10:]}"

# Extract context short name 
context_short = "unknown"
if hasattr(taskspec.spec, 'weft_context') and taskspec.spec.weft_context:
    context_path = Path(taskspec.spec.weft_context)
    context_short = context_path.name[:8]
    context_short = re.sub(r'[^a-zA-Z0-9_-]', '', context_short) or "proj"

setproctitle.setproctitle(f"weft-worker-{{context_short}}-{{tid_short}}:{target[0]}")
sys.exit(subprocess.call({target!r}))
"""
        # Write wrapper and execute
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(wrapper_script)
            wrapper_path = f.name
        
        return subprocess.Popen([sys.executable, wrapper_path])

class FunctionExecutor(Executor):
    def execute(self, target: Callable, tid: str, name: str) -> ExecutionResult:
        """Execute function with process title in multiprocessing."""
        
        def wrapped_target(*args, **kwargs):
            """Wrapper that sets process title before executing."""
            import setproctitle
            from pathlib import Path
            import re
            
            tid_short = tid[-10:]
            
            # Extract context short name
            context_short = "unknown"
            if hasattr(taskspec.spec, 'weft_context') and taskspec.spec.weft_context:
                context_path = Path(taskspec.spec.weft_context)
                context_short = context_path.name[:8]
                context_short = re.sub(r'[^a-zA-Z0-9_-]', '', context_short) or "proj"
            
            setproctitle.setproctitle(f"weft-func-{context_short}-{tid_short}:{name}")
            return target(*args, **kwargs)
        
        # Use multiprocessing for true process isolation
        with multiprocessing.Pool(1) as pool:
            result = pool.apply_async(wrapped_target, args, kwargs)
            return result.get(timeout=self.timeout)
```

This ensures that both the main task process and any subprocesses it spawns are identifiable in system tools.

## 4. ResourceMonitor (weft/core.resource_monitor.py)
**Purpose**: Track and enforce resource limits using psutil as the default implementation

**Default Implementation**: The system uses psutil for cross-platform resource monitoring. The `monitor_class` field in TaskSpec defaults to `"weft.core.resource_monitor.ResourceMonitor"` which is psutil-based.

**Monitored Resources** (via psutil):
- Memory usage (RSS) - `psutil.Process.memory_info().rss` in bytes, reported in MB
- CPU percentage - `psutil.Process.cpu_percent()` as 0-100 percentage
- File descriptors - `len(psutil.Process.open_files())` count
- Network connections - `len(psutil.Process.connections())` count
- Disk I/O (optional) - `psutil.Process.io_counters()` when available

**Monitoring Strategy**:
```python
class ResourceMonitor:
    """Default psutil-based resource monitor."""
    
    def __init__(self):
        self.process = None  # psutil.Process instance
        
    def start_monitoring(self, pid: int, interval: float = 1.0):
        """Begin monitoring process using psutil."""
        self.process = psutil.Process(pid)
        
    def get_current_metrics(self) -> ResourceMetrics:
        """Get current resource usage via psutil."""
        return ResourceMetrics(
            memory_mb=self.process.memory_info().rss / (1024 * 1024),
            cpu_percent=self.process.cpu_percent(interval=0.1),
            open_files=len(self.process.open_files()),
            connections=len(self.process.connections())
        )
        
    def check_limits(self, limits: LimitsSection) -> tuple[bool, str]:
        """Check if limits exceeded using psutil metrics."""
        metrics = self.get_current_metrics()
        
        if limits.memory_mb and metrics.memory_mb > limits.memory_mb:
            return False, f"Memory {metrics.memory_mb}MB > {limits.memory_mb}MB"
            
        if limits.cpu_limit and metrics.cpu_percent > limits.cpu_limit:
            return False, f"CPU {metrics.cpu_percent}% > {limits.cpu_limit}%"
            
        return True, None
        
    def stop_monitoring(self):
        """Stop monitoring and cleanup."""
        self.process = None
```

**Custom Monitor Support**: Alternative monitors can be specified via `spec.monitor_class` for specialized use cases (e.g., cgroup-based, container-aware).

## 5. Queue System Integration

**Queue Structure Per Task**:
```python
# Standard queues for each task (using TID prefix)
T{tid}.inbox      # New messages to process
T{tid}.reserved   # Messages claimed for processing (includes failed)
T{tid}.outbox     # Completed results
T{tid}.ctrl_in    # Control commands (STOP, PAUSE, etc.)
T{tid}.ctrl_out   # Status updates from task

# Global visibility queues
weft.tasks.log       # Aggregates state from ALL tasks

# Ephemeral system state (excluded from weft dump)
weft.state.process.tid_mappings    # Maps short TIDs to full TIDs for process management
weft.state.worker.registry         # Worker liveness tracking (includes PIDs)
weft.state.streaming.sessions      # Active streaming operations (self-managed by tasks)

# Worker coordination queues
weft.spawn.requests     # Global queue for task spawn requests
```

**TID Mapping Queue Structure**:
The `weft.state.process.tid_mappings` queue maintains a persistent record of TID mappings for process management:

```python
# Message format in weft.state.process.tid_mappings
{
    "short": "0161024",              # Last 10 digits of TID
    "full": "1837025672140161024",   # Complete 19-digit TID
    "pid": 12345,                     # Process ID
    "name": "git-clone",              # Task name
    "started": 1837025672140161024  # Start timestamp (nanoseconds)
}
```

**Worker Registry Queue Structure**:
The `weft.state.worker.registry` queue tracks worker liveness for system management:

```python
# Message format in weft.state.worker.registry
{
    "worker_id": "W1837025672140161025",  # Worker TID
    "pid": 12345,                         # Process ID
    "last_heartbeat": 1837025672140161026 # Last activity timestamp
}
```

**Streaming Sessions Queue Structure**:
The `weft.state.streaming.sessions` queue tracks active streaming/interactive operations. Entries are appended when a task begins streaming and deleted when the session ends. (Implementation: `weft/core/tasks/base.py` `_begin_streaming_session`, `_end_streaming_session`.)

```python
# Message format in weft.state.streaming.sessions (self-managed by tasks)
{
    "session_id": "1837025672140161024:T1837025672140161024.outbox:1837025672140162024",
    "tid": "1837025672140161024",       # Task performing streaming
    "mode": "stream" | "interactive",   # Streaming mode
    "queue": "T1837025672140161024.outbox",        # Primary outbox receiving streamed data
    "ctrl_queue": "T1837025672140161024.ctrl_out", # Interactive stderr/control stream (when present)
    "started_at": 1837025672140162024,   # Nanosecond timestamp when session began
    "bytes": 1048576,                    # Optional total bytes (stream mode)
    "chunks": 4,                         # Optional chunk count (stream mode)
    "session_pid": 55123                 # Optional subprocess PID (interactive mode)
}

# Simple lifecycle (presence = active, absence = done):
# 1. Task starts streaming/interactive output and enqueues the session record.
# 2. Task emits stream chunks to its queues while the session is active.
# 3. Task deletes the specific session message when streaming completes or the interaction closes.
```

This enables reverse lookup when managing tasks via OS commands:
```bash
# Find full TID from process (no escaping needed!)
ps aux | grep "weft-proj1-0161024"     # Shows: weft-proj1-0161024:git-clone:running
# Lookup full TID  
weft tid-lookup 0161024          # Returns: 1837025672140161024
# Use full TID for queue operations
weft queue read T1837025672140161024.outbox

# Shell-friendly operations with context awareness
tid=$(ps aux | grep weft- | cut -d- -f3 | cut -d: -f1)  # Extract TID (3rd field after context)
pkill -f "weft-proj1-.*:failed"        # Kill failed tasks in specific project
pkill -f "weft-.*:failed"              # Kill failed tasks in any project
```

**The Unified Reservation Pattern**:
The `.reserved` queue serves dual purposes:
1. **Work-in-Progress**: Holds messages being actively processed
2. **Dead Letter Queue**: Retains failed messages with state tracking

```python
# Normal flow
inbox → reserved → [process] → outbox + delete from reserved

# Failure flow  
inbox → reserved → [fail] → stays in reserved + state.error=true

# Recovery flow
reserved → [retry] → outbox + delete from reserved
```

`reserved_policy_on_stop` and `reserved_policy_on_error` in the TaskSpec let
operators decide whether messages stay in `reserved`, get pushed back to
`inbox`, or are cleared automatically when a task stops or fails.

**Queue Lifecycle and Durability**:
- **Creation**: Queues created automatically on first use (no pre-registration)
- **Persistence**: Queues survive task termination (durable by default)
- **Cleanup**: Empty queues automatically disappear (SimpleBroker feature)
- **Recovery**: Failed tasks leave messages in `.reserved` for debugging/retry

**Connection Patterns**:
```python
# Persistent connection for high-throughput processing
queue = Queue(f"T{tid}.inbox", persistent=True)
with queue:
    for message in queue.read_generator():
        process(message)

# Ephemeral for low-frequency operations
Queue(f"T{tid}.ctrl_out").write(status_update)

# Atomic reservation with move
Queue(f"T{tid}.inbox").move_one(f"T{tid}.reserved")
```

## Module Architecture

### File Organization

Weft follows a structured module organization that separates concerns while maintaining clear relationships:

```
weft/core/                        # Core system components
├── taskspec.py                   # TaskSpec data model ✅
├── tasks.py                      # Task execution engine
├── worker.py                     # Manager (recursive architecture)  
├── executor.py                   # Target execution (function/command)
├── monitor.py                    # Resource monitoring with psutil
└── manager.py                    # Client for submission/querying

weft/                             # Root package components
├── context.py                    # Context discovery and initialization ✅
├── cli.py                        # CLI framework and global options
├── commands.py                   # CLI commands with SimpleBroker integration
├── helpers.py                    # Cross-cutting helper functions
└── commands.py                   # CLI commands with SimpleBroker integration

weft/tools/                       # OS integration utilities
├── process_tools.py              # Process discovery via ps/kill
├── tid_tools.py                  # TID resolution (short<->full)
└── observability.py             # Log aggregation, state querying

weft/integration/                 # External system integration
├── streams.py                    # Stream adapters (stdin/stdout)
├── unix.py                       # Unix command wrappers
├── pipelines.py                  # Task pipeline builders
└── templates.py                  # Task template management
```

### Component Locations

| Component | Module | Purpose |
|-----------|--------|---------|
| **TaskSpec** | `weft.core.taskspec` | Task configuration and validation |
| **Task** | `weft.core.tasks` | Task execution extending MultiQueueWatcher |
| **Manager** | `weft.core.manager` | Recursive worker implementation |
| **Client** | `weft.core.manager` | Task submission and lifecycle management |
| **ResourceMonitor** | `weft.core.resource_monitor` | psutil-based resource monitoring |
| **WeftContext** | `weft.context` | Git-like project discovery and initialization |
| **FunctionExecutor** | `weft.core.executor` | Python function execution |
| **CommandExecutor** | `weft.core.executor` | System command execution |
| **ProcessManager** | `weft.tools.process_tools` | OS process discovery and control |
| **TIDResolver** | `weft.tools.tid_tools` | TID mapping and resolution |
| **TaskMonitor** | `weft.tools.observability` | System state observation |
| **StreamAdapter** | `weft.integration.streams` | Stream routing to queues |
| **PipelineBuilder** | `weft.integration.pipelines` | Task chain construction |

### Import Guidelines

```python
# Public API (recommended for users)
from weft import TaskSpec, Task, WeftContext

# Core components (internal use)
from weft.core.manager import Client
from weft.core.manager import Manager
from weft.core.executor import FunctionExecutor, CommandExecutor

# Administrative tools
from weft.tools.process_tools import ProcessManager
from weft.tools.tid_tools import TIDResolver

# Integration utilities
from weft.integration.streams import StreamAdapter
from weft.integration.pipelines import PipelineBuilder
```

## 6. WeftContext (weft/context.py)
**Purpose**: Git-like project discovery and initialization

**Key Responsibilities**:
- Discover `.weft/` project root by traversing up directory tree
- Auto-initialize projects with interactive prompts
- Provide centralized database path for all SimpleBroker operations
- Handle edge cases around filesystem boundaries and permissions

**Core Functions**:
```python
def get_context() -> Dict[str, Any]:
    """Get current weft context with automatic discovery.
    
    Returns:
        Dict containing:
        - root_path: Project root directory (contains .weft/)
        - weft_dir: .weft/ directory path
        - db_path: SimpleBroker database path (.weft/broker.db)
        - config_path: Project configuration file path
    
    Raises:
        WeftContextError: If no .weft/ found and initialization declined
    """

def discover_weft_root(start_path: Optional[Path] = None) -> Optional[Path]:
    """Find .weft directory by traversing up like git.
    
    Algorithm:
    1. Start from start_path (default: current working directory)
    2. Check if .weft/ directory exists in current directory
    3. If not found, move to parent directory
    4. Repeat until .weft/ found or filesystem root reached
    5. Return project root path or None if not found
    
    Edge Cases Handled:
    - Filesystem root boundary detection
    - Symlink resolution via Path.resolve()
    - Permission errors during directory traversal
    """

def initialize_project(root_path: Optional[Path] = None, force: bool = False) -> Path:
    """Initialize .weft/ directory structure.
    
    Creates:
    - .weft/ directory (project marker)
    - .weft/broker.db (created by SimpleBroker on first use)
    - .weft/config.json (project metadata)
    - .weft/outputs/ (large output spillover)
    - .weft/logs/ (centralized logging)
    
    Validation:
    - Checks write permissions before creation
    - Validates directory doesn't already exist (unless force=True)
    - Creates atomic directory structure
    """

def needs_initialization(path: Optional[Path] = None) -> bool:
    """Check if directory tree needs weft initialization."""

def get_queue(queue_name: str, context: Optional[Dict[str, Any]] = None) -> Queue:
    """Get SimpleBroker queue using discovered database path."""
```

**Integration with CLI**:
```python
# All CLI commands use context discovery automatically
@cli.group()
@click.option('-d', '--dir', 'context_dir', help='Override project directory')
@click.pass_context
def cli(ctx, context_dir):
    """Weft task execution system."""
    ctx.ensure_object(dict)
    
    if context_dir:
        # Explicit context override
        ctx.obj['context'] = get_context_from_path(context_dir)
    else:
        # Auto-discovery with initialization prompt
        try:
            ctx.obj['context'] = get_context()
        except WeftContextError:
            if needs_initialization() and prompt_for_init():
                initialize_project()
                ctx.obj['context'] = get_context()
            else:
                click.echo("No weft project found. Run 'weft init'.", err=True)
                ctx.exit(1)
```

**Error Handling and Edge Cases**:
- **Filesystem Boundaries**: Stops traversal at filesystem root, never crosses mount points
- **Permission Errors**: Graceful handling when directories are unreadable
- **Symlink Handling**: Resolves symlinks to prevent infinite loops and path traversal attacks
- **Concurrent Initialization**: Handles race conditions in multi-process environments
- **Cleanup on Failure**: Removes partial initialization on errors
- **Security Validation**: Uses SimpleBroker's path validation and security functions, but has independent tests

**SimpleBroker Security Integration**:
Weft leverages SimpleBroker's existing security functions to avoid reimplementing security features:

```python
# Uses SimpleBroker's validation
from simplebroker.cli import validate_database_path
from simplebroker.db import _validate_queue_name_cached

def initialize_project(root_path: Optional[Path] = None, force: bool = False) -> Path:
    """Initialize project using SimpleBroker's security validation."""
    db_path = root / ".weft" / "broker.db"
    
    # Leverage SimpleBroker's security checks:
    # - Path traversal prevention (.., symlinks)
    # - Parent directory permission validation
    # - Symlink chain resolution
    # - Database file accessibility checks
    validate_database_path(str(db_path), str(root))
    
    # Apply SimpleBroker's secure permissions (0o600)
    os.chmod(db_path, 0o600)  # Owner read/write only
```

**Security Features Inherited from SimpleBroker**:
- **Path Traversal Protection**: Prevents `../../../etc/passwd` attacks
- **Symlink Attack Prevention**: Resolves symlink chains and validates containment
- **Permission Validation**: Ensures parent directory is accessible and writable
- **File Type Validation**: Ensures database files are regular files, not special files
- **Cross-Platform Permissions**: Secure file permissions on Unix/Windows

**SimpleBroker Integration**:
All SimpleBroker operations automatically use the discovered database:
```python
# Example: Queue operations with auto-discovered database
def cmd_read(queue_name: str, **kwargs) -> int:
    ctx = get_context()  # Auto-discovery
    return sb_commands.cmd_read(
        db_path=ctx["db_path"],  # Inject discovered database
        queue_name=queue_name,
        **kwargs
    )
```

This design ensures consistent behavior across all project subdirectories while maintaining SimpleBroker's proven queue functionality.

## Related Documents

- **[00-Overview_and_Architecture.md](00-Overview_and_Architecture.md)** - System overview and design principles
- **[02-TaskSpec.md](02-TaskSpec.md)** - Task configuration specification
- **[03-Worker_Architecture.md](03-Worker_Architecture.md)** - Recursive worker model
- **[04-SimpleBroker_Integration.md](04-SimpleBroker_Integration.md)** - Queue system integration
- **[05-Message_Flow_and_State.md](05-Message_Flow_and_State.md)** - Communication patterns
- **[06-Resource_Management.md](06-Resource_Management.md)** - Resource controls and error handling

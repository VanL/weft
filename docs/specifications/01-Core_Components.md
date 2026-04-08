# Core Components

This document details the fundamental components of the Weft system architecture.

## Related Plans

- [Task Lifecycle Stop/Drain Audit Plan](../plans/task-lifecycle-stop-drain-audit-plan.md) - Audit terminal-state ownership, manager drain, and cleanup behavior before making lifecycle fixes.
- [Active Control Main-Thread Ownership Plan](../plans/active-control-main-thread-plan.md) - Remove background active control polling and restore single-threaded task-state ownership.
- [Runner Extension Point Plan](../plans/runner-extension-point-plan.md) - Runner plugin architecture replacing the monolithic executor.
- [Persistent Agent Runtime Implementation Plan](../plans/persistent-agent-runtime-implementation-plan.md) - Agent session and persistent task support.
- [Spec-Plan-Code Traceability Plan](../plans/spec-plan-code-traceability-plan.md) - Cross-referencing specs, plans, and code.

## 1. TaskSpec (weft/core/taskspec.py) [CC-1]
**Purpose**: Task configuration with partial immutability - immutable spec, mutable state

_Implementation mapping_: `weft/core/taskspec.py` — `TaskSpec`, `SpecSection`, `IOSection`, `StateSection`, `LimitsSection`, `RunnerSection`, `ReservedPolicy`, `resolve_taskspec_payload`, `FrozenList`, `FrozenDict`.

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
    tid: str              # 64-bit SimpleBroker timestamp (microseconds + counter)
    name: str             # Human-readable identifier
    version: str          # Schema version (e.g., "1.0")
    
    # Configuration (immutable after creation)
    spec: SpecSection     # Frozen execution parameters
    io: IOSection         # Frozen queue mappings
    
    # Runtime State (mutable)
    state: StateSection   # Status and metrics - changes during execution
    metadata: dict        # User-defined key-value pairs - flexible updates
```

**Template vs resolved**:
- Stored TaskSpec templates omit `tid`, `io`, and `state` (or use `tid=null`).
- The Manager expands templates at spawn time, setting `tid`, `io`, `state`, and
  `spec.weft_context` for the resolved runtime TaskSpec.

**SpecSection Structure with Limits Subsection**:
```python
class SpecSection:
    # Execution configuration
    type: Literal["function", "command", "agent"]   # "agent" added by runner-extension-point
    function_target: str | None      # For type="function"
    process_target: str | None       # For type="command"
    args: list[Any]
    keyword_args: dict[str, Any]

    # Resource limits (grouped for clarity)
    limits: LimitsSection

    # Execution behavior
    timeout: float | None
    env: dict[str, str]
    working_dir: str | None
    interactive: bool
    persistent: bool                 # Long-lived tasks that survive multiple work items
    stream_output: bool
    cleanup_on_exit: bool
    reserved_policy_on_stop: Literal["keep", "requeue", "clear"]
    reserved_policy_on_error: Literal["keep", "requeue", "clear"]
    output_size_limit_mb: int
    enable_process_title: bool
    weft_context: str | None

    # Runner selection
    runner: RunnerSection            # Plugin-based execution backend (default: "host")
    agent: AgentSection | None       # Agent runtime configuration (type="agent" only)

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

class RunnerSection:
    """Execution backend selection for the task."""
    name: str                    # Runner plugin name (default: "host")
    options: dict[str, Any]      # Runner-specific JSON-serializable options
```

**Immutability Enforcement**:
```python
def __init__(self, **data):
    super().__init__(**data)
    # Freeze spec after initialization
    self._freeze_spec()
    
def _freeze_spec(self):
    """Make spec and io sections immutable."""
    self._frozen_fields = {"tid", "spec", "io"}
```

**Key Invariants**:
- TID is immutable and unique across all tasks
- spec and io sections become immutable after TaskSpec creation
- state section remains mutable for runtime updates
- State transitions are forward-only (enforced by TaskSpec.set_status)
- Required queues (outbox, ctrl_in, ctrl_out) always present
- All optional fields expanded to explicit values via apply_defaults()
- `spec.runner.name` defaults to `"host"` when the runner section is omitted
- `spec.runner.options` stays JSON-serializable so TaskSpecs can be stored and replayed safely

## 2. Task Execution Architecture
Weft tasks build on top of SimpleBroker’s multi-queue watcher and are organised
as a small hierarchy of classes rather than a single monolithic `Task`.  This
section captures the responsibilities of each layer while preserving the
behaviour promised in the original design (multiprocess isolation, rich control
channels, and queue-based state reporting).

### 2.1 MultiQueueWatcher (weft/core/tasks/multiqueue_watcher.py) [CC-2.1]
**Purpose**: Provide a scheduler that can monitor multiple SimpleBroker queues,
each with its own processing semantics.

_Implementation mapping_: `weft/core/tasks/multiqueue_watcher.py` — `MultiQueueWatcher`, `QueueMode`, `QueueMessageContext`, `PollingStrategy` (re-exported from SimpleBroker).

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

_Implementation mapping_: `weft/core/tasks/base.py` — `BaseTask.__init__`, `_resolve_queue_names`, `_build_queue_configs` (abstract), `_handle_control_message`, `_handle_control_command`, `_report_state_change`, `_update_process_title`, `_format_process_title`, `_register_tid_mapping`, `run_until_stopped`, `process_once`, `_apply_reserved_policy`, `_maybe_emit_poll_report`, `cleanup`, `stop`.

**Key Responsibilities**:
- Translate `TaskSpec.io` into queue configurations (inbox reserve mode,
  reserved peek mode, control consume mode, outbox read/write).
- Maintain control-channel semantics (STOP, STATUS, PING) and emit JSON
  responses on `ctrl_out`.
- Update process titles and TID mappings (still expected to include the context
  prefix as described later in this spec).
- Record state transitions to `weft.log.tasks`, including a full `TaskSpec`
  snapshot to support event-sourced reconstruction.
- Expose `process_once()` and `run_until_stopped()` helpers; long-running
  workers are expected to loop on `run_until_stopped`, while single-shot CLI
  invocations may call `process_once()` directly.
- Honour periodic reporting when `TaskSpec.spec.reporting_interval == "poll"`,
  emitting `poll_report` events at the configured `polling_interval`.

BaseTask purposefully does *not* execute work itself.  Instead it delegates to
`TaskRunner` (see §3) from within `_handle_work_message`, allowing subclasses to
focus on the semantics of message handling rather than process orchestration.

### 2.3 Specialized Task Types [CC-2.3]
Concrete task types extend `BaseTask` to express different queue behaviours.

_Implementation mapping_: `Consumer` and `SelectiveConsumer` (`weft/core/tasks/consumer.py`), `Observer` and `SamplingObserver` (`weft/core/tasks/observer.py`), `Monitor` (`weft/core/tasks/monitor.py`). All re-exported from `weft/core/tasks/__init__.py`.

Interactive command sessions reuse `Consumer` with `spec.interactive=True`,
streaming line-oriented stdin/stdout via task-local JSON envelopes rather than
per-message execution. This mode is queue-mediated interaction, not terminal
emulation.

| Class | Purpose | Queue Modes |
|-------|---------|-------------|
| `Consumer` | Default worker that reserves inbox messages, executes the target, and writes to the outbox. Applies reserved queue policies on success/failure. | Inbox `RESERVE`, Reserved `PEEK`, Control `READ` |
| `Observer` | Peeks at inbox messages without consuming them; useful for monitoring pipelines. | Inbox `PEEK`, Control `READ` |
| `SelectiveConsumer` | Peeks inbox messages and consumes them only when a selector predicate returns true. Optional callback for side effects. | Inbox `PEEK`, Control `READ` |
| `Monitor` | Forwards messages to a downstream queue while observing them (reserve mode to ensure at-most-once forwarding). | Inbox `RESERVE` → downstream, Control `READ` |
| `SamplingObserver` | Observer variant that invokes its callback at fixed intervals based on elapsed time. | Inherits `Observer` behaviour |

Future worker/task variants should continue to derive from `BaseTask` and reuse
its queue helpers (`_reserve_queue_config`, `_peek_queue_config`, `_read_queue_config`)
to maintain consistency.

### 2.4 Control and State Expectations [CC-2.4]
- **Control commands**: STOP, STATUS, and the health-check `PING`
  round-trip remain mandatory. Additional commands may be layered on via
  `_handle_control_command` overrides.
- **Active control ownership**: While work is actively executing, control
  polling may happen from the same task thread via the runtime loop, but
  terminal STOP/KILL state publication, reserved-policy application, and
  control ACK emission remain task-owned operations on that main task thread.
- **State emission**: Each transition (start, completion, failure, timeout,
  reserved-policy event, etc.) must call `_report_state_change`. When
  `reporting_interval == "poll"`, tasks also emit `poll_report` events at the
  configured `polling_interval`.
- **Process titles**: The shell-friendly format
  `weft-{context_short}-{tid_short}:{name}:{status}[:details]` remains the target
  behaviour.  Implementations that currently omit the context must be updated to
  meet this requirement.
- **Reserved queue policies**: `keep`, `clear`, and `requeue` policies are
  enforced by `BaseTask._apply_reserved_policy` and must continue to behave as
  documented in `TaskSpec`.

_Implementation status_: STOP/STATUS/PING handling, state emission,
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
   - Poll active control from the same task thread while the runtime loop is
     running, deferring active STOP/KILL finalization until the runtime exits.
   - Apply reserved-queue policy and emit state events (`work_completed`,
     `work_failed`, `work_timeout`, `work_limit_violation`, etc.).
4. Emit control responses for any control commands encountered.
5. Loop via `process_once()` / `run_until_stopped()` until `STOP` is received or
   the CLI/controller decides to exit.

The CLI command `weft run` already demonstrates both usage patterns: `--once`
invokes `process_once()`, while the default mode loops until interrupted.

_Implementation mapping_: `Consumer._handle_work_message`, `Consumer._execute_work_item`, `Consumer._begin_work_item`, `Consumer._cancel_requested`, `Consumer._poll_active_control_once`, `Consumer._finalize_message` (`weft/core/tasks/consumer.py`) and `cmd_run` (`weft/commands/run.py`).

## 3. TaskRunner (weft/core/tasks/runner.py) [CC-3]
**Purpose**: Plugin-backed facade that validates the execution shape declared by
the TaskSpec and dispatches work through the selected runner backend.

_Implementation mapping_: `weft/core/tasks/runner.py` — `TaskRunner` (plugin-dispatching facade), `_build_runner_validation_payload`. The runner delegates to backend plugins via `weft/_runner_plugins.py` and `weft/ext.py`. The built-in host runner backend lives in `weft/core/runners/host.py`. External runners are loaded through the `weft.runners` entry-point group described in the [Runner Extension Point Plan](../plans/runner-extension-point-plan.md).

**Highlights**:
- `spec.type` selects task semantics; `spec.runner` selects the execution backend.
- The built-in `host` runner uses the same plugin contract as external runners.
- Runner plugins may own backend-specific validation, preflight, control, and
  runtime description behavior as long as they return Weft-compatible
  `RunnerOutcome` data.
- Consumers and CLI control/status flows must use persisted `RunnerHandle`
  data rather than assuming that a host PID is the only valid runtime handle.

The long-term contract remains the same: tasks must run in isolated processes,
terminate cleanly on timeout or STOP, and surface sufficient telemetry for
operators to understand what happened.

### 3.1 Runner Plugin Boundary [CC-3.1]

Runner extensibility is a first-class execution boundary.

- Core resolves runners by name through the `weft.runners` entry-point group.
- The built-in `host` runner is always available and is the default when
  `spec.runner` is omitted.
- External runners may ship as optional extension packages, for example
  `weft[docker]` or `weft[macos-sandbox]`.
  - Current support note: the first-party Docker runner is supported on Linux
    and macOS, but not Windows.
- The public plugin surface must cover:
  - capability declaration
  - TaskSpec validation and preflight
  - backend construction
  - stop/kill control
  - runtime description for observability

Core owns queue semantics and task lifecycle. Plugins own runtime-specific
execution details.

### 3.2 Runtime Handles and Control [CC-3.2]

Non-host runners require a durable control surface that is not just a host PID.

- `RunnerHandle` persists the runner name, runner-defined runtime identifier,
  any meaningful host PIDs, and optional metadata.
- Runtime handles are recorded in runtime state so later CLI/status/control
  paths can resolve the correct runner plugin and act on the live runtime.
- Host PIDs remain useful observability hints, but they are not the canonical
  cross-runner control identifier.

### 3.3 Validation and Preflight [CC-3.3]

Runner-aware validation happens in layers.

- TaskSpec schema validation checks the shape of `spec.runner`.
- Capability validation checks whether the selected runner supports the task
  shape (`spec.type`, `interactive`, `persistent`, agent-session needs).
- Plugin validation checks required runner-specific options.
- Preflight checks verify that the runtime is actually available on the current
  machine.

Unsupported combinations should fail early and explicitly rather than
silently degrading to host behavior.

### 3.4 Monitoring Ownership [CC-3.4]

Monitoring lives at the runner boundary.

- The `host` runner uses Weft's psutil/resource-monitor path.
- Non-host runners may provide runtime-native monitoring and enforcement.
- Core status surfaces should display runner-native runtime descriptions when
  available instead of forcing every runner into a host-PID-only model.

### Process Title Expectations
```python
class ProcessTitleMixin:
    """Mixin for process title management functionality."""
    
    def _compute_short_tid(self) -> str:
        """Extract last N digits of TID for display.
        
        Returns:
            String of last TID_SHORT_LENGTH digits
            
        Rationale:
            Low bits change most frequently (hybrid timestamp counter),
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
        
        # Extract context short name from project root
        context_path = self.context.path
        context_short = context_path.name[:8]  # Max 8 chars for context
        context_short = re.sub(r'[^a-zA-Z0-9_-]', '', context_short) or "proj"
        
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
        
        Saves mapping to weft.state.tid_mappings queue for tools that need
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
3. Register TID mapping to weft.state.tid_mappings queue
4. Mark status as "spawning" and update process title
5. Start monitoring thread
6. Execute target (function or command)
7. Update process title to "running"
8. Process queues continuously
   - Handle control messages (STOP, STATUS, PING)
   - Handle input queues (if provided), routing input to target stdin
   - Write output from target to identified output queue (default outbox)
   - Update state from monitoring thread and send to global task log queue (weft.log.tasks)
   - Periodically update process title with progress if available
9. Write final output/accumulated output to outbox
10. Update process title to terminal state (completed/failed/timeout/killed)
11. Update and report final state
12. Clean up resources, including clearing queues, if cleanup_on_exit is True
13. Exit (process title automatically cleared by OS)

## 3. Executor (weft/core/executor.py) **[NOT YET IMPLEMENTED -- superseded by runner plugins]**
**Purpose**: Isolated execution of task targets with subprocess observability

> **Note**: `weft/core/executor.py` does not exist. The responsibilities described below (function/command execution, subprocess title propagation) are handled by the runner plugin system (`weft/core/runners/`, `weft/_runner_plugins.py`). The `CommandExecutor` / `FunctionExecutor` classes shown in the pseudocode were never implemented as a standalone module. See [CC-3] and the [Runner Extension Point Plan](../plans/runner-extension-point-plan.md).

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
            import re
            tid_short = tid[-10:]
            context_short = re.sub(r'[^a-zA-Z0-9_-]', '', self.context.path.name[:8]) or "proj"
            
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
        import re
        context_short = re.sub(r'[^a-zA-Z0-9_-]', '', self.context.path.name[:8]) or "proj"
        wrapper_script = f"""
import setproctitle
import subprocess
import sys
from pathlib import Path
import re

tid_short = "{tid[-10:]}"
context_short = "{context_short}"

setproctitle.setproctitle(f"weft-worker-{context_short}-{tid_short}:{target[0]}")
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
        import re
        context_short = re.sub(r'[^a-zA-Z0-9_-]', '', self.context.path.name[:8]) or "proj"

        def wrapped_target(*args, **kwargs):
            """Wrapper that sets process title before executing."""
            import setproctitle
            from pathlib import Path
            import re
            
            tid_short = tid[-10:]
            
            context_short = "{context_short}"
            
            setproctitle.setproctitle(f"weft-func-{context_short}-{tid_short}:{name}")
            return target(*args, **kwargs)
        
        # Use multiprocessing for true process isolation
        with multiprocessing.Pool(1) as pool:
            result = pool.apply_async(wrapped_target, args, kwargs)
            return result.get(timeout=self.timeout)
```

This ensures that both the main task process and any subprocesses it spawns are identifiable in system tools.

## 4. ResourceMonitor (weft/core/resource_monitor.py)
**Purpose**: Track and enforce resource limits using psutil as the default implementation

_Implementation mapping_: `weft/core/resource_monitor.py` — `ResourceMonitor` (psutil-based), `ResourceMetrics` dataclass, abstract `BaseResourceMonitor`. Limit enforcement is coordinated by the host runner backend in `weft/core/runners/`.

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
    
    def __init__(self, limits: LimitsSection, polling_interval: float = 1.0):
        self.limits = limits
        self.polling_interval = polling_interval
        self.process = None  # psutil.Process instance
        self.history = []
        self.max_history = 100
        
    def start_monitoring(self, pid: int):
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
        
    def check_limits(self) -> tuple[bool, str | None]:
        """Check if limits exceeded using psutil metrics."""
        metrics = self.get_current_metrics()
        
        if self.limits.memory_mb and metrics.memory_mb > self.limits.memory_mb:
            return False, f"Memory {metrics.memory_mb}MB > {self.limits.memory_mb}MB"
            
        if self.limits.cpu_percent and metrics.cpu_percent > self.limits.cpu_percent:
            return False, f"CPU {metrics.cpu_percent}% > {self.limits.cpu_percent}%"
            
        return True, None
        
    def stop_monitoring(self):
        """Stop monitoring and cleanup."""
        self.process = None
```

**Custom Monitor Support**: Alternative monitors can be specified via `spec.monitor_class` for specialized use cases (e.g., cgroup-based, container-aware).

## 5. Queue System Integration

Queue names and global queues are defined in
[00-Quick_Reference.md](00-Quick_Reference.md). This section focuses on
structure and message formats.

**Queue Structure Per Task**:
```python
# Standard queues for each task (using TID prefix)
T{tid}.inbox      # New messages to process
T{tid}.reserved   # Messages claimed for processing (includes failed)
T{tid}.outbox     # Completed results
T{tid}.ctrl_in    # Control commands (STOP, STATUS, PING)
T{tid}.ctrl_out   # Status updates from task

# Global visibility queues
weft.log.tasks       # Aggregates state from ALL tasks

# Ephemeral system state (excluded from weft dump)
weft.state.tid_mappings    # Maps short TIDs to full TIDs for process management
weft.state.workers         # Worker liveness tracking (includes PIDs)
weft.state.streaming       # Active streaming operations (self-managed by tasks)

# Worker coordination queues
weft.spawn.requests     # Global queue for task spawn requests
```

**TID Mapping Queue Structure**:
The `weft.state.tid_mappings` queue maintains a runtime record of TID mappings for process management:

```python
# Message format in weft.state.tid_mappings
{
    "short": "0161024",              # Last 10 digits of TID
    "full": "1837025672140161024",   # Complete 19-digit TID
    "pid": 12345,                     # Process ID
    "name": "git-clone",              # Task name
    "started": 1837025672140161024  # Start timestamp (SimpleBroker format)
}
```

_Implementation mapping_: `weft/core/tasks/base.py` (`_register_tid_mapping`, additional fields like task_pid/managed_pids are included).

**Worker Registry Queue Structure**:
The `weft.state.workers` queue tracks worker liveness for system management:

```python
# Message format in weft.state.workers
{
    "worker_id": "W1837025672140161025",  # Worker TID
    "pid": 12345,                         # Process ID
    "last_heartbeat": 1837025672140161026 # Last activity timestamp
}
```

_Implementation mapping_: `weft/core/manager.py` (registry payloads; additional keys permitted).

**Streaming Sessions Queue Structure**:
The `weft.state.streaming` queue tracks active streaming/interactive operations. Entries are appended when a task begins streaming and deleted when the session ends. (Implementation: `weft/core/tasks/base.py` `_begin_streaming_session`, `_end_streaming_session`.)

```python
# Message format in weft.state.streaming (self-managed by tasks)
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
weft task tid 0161024            # Returns: 1837025672140161024
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
├── tasks/                        # Task execution engine (package) ✅
│   ├── base.py                   # BaseTask ✅
│   ├── consumer.py               # Consumer, SelectiveConsumer ✅
│   ├── observer.py               # Observer, SamplingObserver ✅
│   ├── monitor.py                # Monitor ✅
│   ├── multiqueue_watcher.py     # MultiQueueWatcher ✅
│   ├── runner.py                 # TaskRunner (plugin facade) ✅
│   ├── interactive.py            # InteractiveTaskMixin ✅
│   └── sessions.py               # AgentSession, CommandSession ✅
├── runners/                      # Runner plugin backends ✅
├── manager.py                    # Manager (spawns child tasks) ✅
├── resource_monitor.py           # Resource monitoring with psutil ✅
└── launcher.py                   # Process launching utilities ✅

weft/                             # Root package components
├── context.py                    # Context discovery and initialization ✅
├── cli.py                        # CLI framework and global options ✅
├── commands/                     # CLI commands (package) ✅
├── helpers.py                    # Cross-cutting helper functions ✅
├── _constants.py                 # All constants and env vars ✅
├── _runner_plugins.py            # Runner plugin registry ✅
└── ext.py                        # Extension points (RunnerHandle, etc.) ✅

weft/tools/                       # [NOT YET IMPLEMENTED]
├── process_tools.py              # Process discovery via ps/kill
├── tid_tools.py                  # TID resolution (short<->full)
└── observability.py              # Log aggregation, state querying

weft/integration/                 # [NOT YET IMPLEMENTED]
├── streams.py                    # Stream adapters (stdin/stdout)
├── unix.py                       # Unix command wrappers
├── pipelines.py                  # Task pipeline builders
└── specs.py                      # Task spec storage and instantiation
```

### Component Locations

| Component | Module | Purpose | Status |
|-----------|--------|---------|--------|
| **TaskSpec** | `weft.core.taskspec` | Task configuration and validation | Implemented |
| **BaseTask** | `weft.core.tasks.base` | Abstract task base with queue wiring | Implemented |
| **Consumer** | `weft.core.tasks.consumer` | Default worker, reserves and executes | Implemented |
| **Observer** | `weft.core.tasks.observer` | Non-consuming message observer | Implemented |
| **Monitor** | `weft.core.tasks.monitor` | Message forwarding observer | Implemented |
| **TaskRunner** | `weft.core.tasks.runner` | Plugin-dispatching execution facade | Implemented |
| **Manager** | `weft.core.manager` | Spawns and manages child tasks | Implemented |
| **ResourceMonitor** | `weft.core.resource_monitor` | psutil-based resource monitoring | Implemented |
| **WeftContext** | `weft.context` | Project discovery and broker scoping | Implemented |
| **RunnerHandle** | `weft.ext` | Extension point for runner plugins | Implemented |
| **FunctionExecutor** | `weft.core.executor` | Python function execution | **[NOT YET IMPLEMENTED]** -- absorbed by runner plugins |
| **CommandExecutor** | `weft.core.executor` | System command execution | **[NOT YET IMPLEMENTED]** -- absorbed by runner plugins |
| **ProcessManager** | `weft.tools.process_tools` | OS process discovery and control | **[NOT YET IMPLEMENTED]** |
| **TIDResolver** | `weft.tools.tid_tools` | TID mapping and resolution | **[NOT YET IMPLEMENTED]** |
| **TaskMonitor** | `weft.tools.observability` | System state observation | **[NOT YET IMPLEMENTED]** |
| **StreamAdapter** | `weft.specs.streams` | Stream routing to queues | **[NOT YET IMPLEMENTED]** |
| **PipelineBuilder** | `weft.specs.pipelines` | Task chain construction | **[NOT YET IMPLEMENTED]** |

### Import Guidelines

```python
# Public API (recommended for users)
from weft.core.taskspec import TaskSpec
from weft.core.tasks import Consumer, Observer, Monitor, BaseTask
from weft.context import WeftContext, build_context

# Core components (internal use)
from weft.core.manager import Manager
from weft.core.tasks.runner import TaskRunner
from weft.core.resource_monitor import ResourceMonitor

# Extension points
from weft.ext import RunnerHandle
from weft._runner_plugins import require_runner_plugin

# NOT YET IMPLEMENTED -- the imports below reference planned modules
# from weft.tools.process_tools import ProcessManager
# from weft.tools.tid_tools import TIDResolver
# from weft.specs.streams import StreamAdapter
# from weft.specs.pipelines import PipelineBuilder
```

## 6. WeftContext (weft/context.py)
**Purpose**: Git-like project discovery and initialization

_Implementation mapping_: `weft/context.py` — `WeftContext` (dataclass), `build_context`, `get_context`. The implementation delegates broker discovery to SimpleBroker's public project API rather than maintaining a parallel system. The function signatures below are aspirational; the actual public API uses `build_context(spec_context=None)` and `get_context(spec_context=None)`.

> **Note**: The API shown in the pseudocode below (`get_context() -> Dict`, `discover_weft_root`, `initialize_project`, `needs_initialization`, `get_queue`) **[NOT YET IMPLEMENTED]** as described. The current implementation uses a simplified `WeftContext` dataclass with `build_context()` / `get_context()` helpers that delegate to SimpleBroker's project scoping. See `weft/context.py` module docstring for the actual contract.

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

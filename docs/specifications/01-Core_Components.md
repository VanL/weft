# Core Components

This document details the fundamental components of the Weft system architecture.

## 1. TaskSpec (weft/core/taskspec.py)
**Purpose**: Task configuration with partial immutability - immutable spec, mutable state

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

## 2. Task (weft/core/tasks.py and weft/core/multiqueue_watcher.py)
**Purpose**: Task execution and lifecycle management with OS-level observability

**Key Responsibilities**:
- Execute task targets (function/command)
- Monitor resource usage  
- Handle queue communication
- Manage state transitions
- Provide OS-level visibility via process titles
- Report / clean up resources on target completion/exit
- Self-terminate

**Architecture Decision: Process vs Thread Isolation**
Tasks use multiprocessing for target execution rather than threading:
- **Process isolation**: True resource limits, crash isolation, signal-based termination
- **Text-based interface**: All inputs/outputs are strings (Unix pipe model), eliminating serialization complexity
- **OS-level control**: Main process can terminate target via SIGTERM/SIGKILL
- **Resource monitoring**: Target process has separate PID, visible in system tools
- **Performance trade-off**: ~50-100ms process startup overhead vs ~1-5ms threading
- **Memory trade-off**: ~8MB baseline per process vs shared address space for threads

**Core Architecture** (Extends SimpleBroker's MultiQueueWatcher example):
```python
from weft/core/multiqueue_watcher import MultiQueueWatcher # copied/modified from SimpleBroker's example code

class Task(MultiQueueWatcher):
    """Task extends SimpleBroker's MultiQueueWatcher to monitor task-specific queues."""
    
    # Class constants
    TID_SHORT_LENGTH = 10  # Last N digits of TID for display
    PROCESS_TITLE_PREFIX = "weft"
    TID_MAPPING_QUEUE = "weft.tid.mappings"
    
    def __init__(self, 
                 taskspec: TaskSpec,
                 enable_process_title: bool = True,
                 db: str|Path = None):
        """Initialize task using SimpleBroker's MultiQueueWatcher."""
        self.taskspec = taskspec
        self.tid = taskspec.tid
        self.tid_short = self._compute_short_tid()
        self.enable_process_title = enable_process_title and self._check_setproctitle()
        
        # Define task queues to monitor
        task_queues = [
            f"T{self.tid}.ctrl_in",    # Control messages (peek mode via error handler)
            f"T{self.tid}.inbox",      # Work messages (consume mode)
            f"T{self.tid}.reserved",   # Reserved/failed messages (peek mode)
        ]
        
        # Define queue-specific handlers
        queue_handlers = {
            f"T{self.tid}.ctrl_in": self._handle_control_message,
            f"T{self.tid}.inbox": self._handle_work_message,
            f"T{self.tid}.reserved": self._handle_reserved_message,
        }
        
        # Define queue-specific error handlers for peek behavior
        queue_error_handlers = {
            f"T{self.tid}.ctrl_in": self._control_error_handler,  # Don't consume on peek
            f"T{self.tid}.reserved": self._reserved_error_handler,  # Don't consume on peek
        }
        
        # Use weft_context from TaskSpec if specified
        db_path = db or taskspec.spec.weft_context or os.getcwd()
        
        # Initialize SimpleBroker's MultiQueueWatcher
        super().__init__(
            queues=task_queues,
            queue_handlers=queue_handlers,
            queue_error_handlers=queue_error_handlers,
            db=db_path,
            check_interval=5  # Check inactive queues every 5 iterations
        )
        
        if self.enable_process_title:
            self._update_process_title("init")
            self._register_tid_mapping()
    
    def _handle_control_message(self, message: str, timestamp: int) -> None:
        """Handle control messages (STOP, PAUSE, etc.)."""
        self.handle_control_message({"message": message, "timestamp": timestamp})
    
    def _handle_work_message(self, message: str, timestamp: int) -> None:
        """Handle work messages from inbox."""
        self.handle_work_message({"message": message, "timestamp": timestamp})
    
    def _handle_reserved_message(self, message: str, timestamp: int) -> None:
        """Handle reserved/failed messages for recovery."""
        self.handle_reserved_message({"message": message, "timestamp": timestamp})
    
    def _control_error_handler(self, exc: Exception, message: str, timestamp: int) -> bool:
        """Error handler for control messages - implement peek behavior."""
        # Log error but don't consume message (peek behavior)
        logger.warning(f"Error peeking control message: {exc}")
        return True  # Continue processing
    
    def _reserved_error_handler(self, exc: Exception, message: str, timestamp: int) -> bool:
        """Error handler for reserved messages - implement peek behavior."""
        # Log error but don't consume message (peek behavior)
        logger.warning(f"Error peeking reserved message: {exc}")
        return True  # Continue processing
    
    def run(self) -> None:
        """Run task - main thread monitors queues, target thread executes work."""
        if self.taskspec.spec.timeout is None:
            # Long-running task (worker) - target IS the queue monitoring
            self._update_process_title("running")
            self.start()  # Start SimpleBroker's MultiQueueWatcher (blocking)
        else:
            # Ephemeral task - main thread monitors, target thread executes
            self._execute_with_control()
    
    def _execute_with_control(self) -> None:
        """Main thread monitors queues with control authority over target thread."""
        import threading
        from simplebroker import Queue
        
        self._update_process_title("running")
        self.target_thread = None
        self.target_result = None
        self.target_exception = None
        self.stop_requested = False
        
        try:
            # Start target execution in separate process
            import multiprocessing as mp
            self.target_process = mp.Process(
                target=self._target_wrapper, 
                daemon=False  # Don't want target to be killed on main exit
            )
            self.target_process.start()
            
            # Main thread runs queue monitoring with control authority
            self.start()  # Blocking - handles control messages, timeouts, etc.
            
            # Target completed or was stopped - collect result
            if self.target_process.is_alive():
                # Control message requested stop - terminate target
                self._terminate_target()
            
            self.target_process.join()  # Wait for clean target shutdown
            
            # Process final result
            if self.target_exception:
                raise self.target_exception
            
            if self.target_result is not None:
                # Write result to outbox
                outbox = Queue(f"T{self.tid}.outbox", 
                              db_path=self.taskspec.spec.weft_context or os.getcwd())
                outbox.write(self.target_result)
                
            self.taskspec.mark_completed()
            self._update_process_title("completed")
            
        except Exception as e:
            self.taskspec.mark_failed(error=str(e))
            self._update_process_title("failed", str(e)[:50])
            raise
        finally:
            self._cleanup_target_thread()
            self._report_state_change()
    
    def _target_wrapper(self) -> None:
        """Wrapper for target execution in separate process (text-based output)."""
        from simplebroker import Queue
        
        try:
            # Set process title for target process
            self._update_process_title("running", "target")
            
            # Execute target and get text result
            result = self._execute_target()  # Returns string/text
            
            # Write result directly to outbox queue from target process
            outbox = Queue(f"T{self.tid}.outbox", 
                          db_path=self.taskspec.spec.weft_context or os.getcwd())
            outbox.write(str(result) if result is not None else "")
            
            # Signal success via control queue
            ctrl_out = Queue(f"T{self.tid}.ctrl_out",
                           db_path=self.taskspec.spec.weft_context or os.getcwd())
            ctrl_out.write("TARGET_COMPLETED")
            
        except Exception as e:
            # Signal failure via control queue  
            ctrl_out = Queue(f"T{self.tid}.ctrl_out",
                           db_path=self.taskspec.spec.weft_context or os.getcwd())
            ctrl_out.write(f"TARGET_FAILED:{str(e)}")
    
    def _terminate_target(self) -> None:
        """Terminate target process (called from main thread on control messages)."""
        if self.target_process and self.target_process.is_alive():
            # Try graceful shutdown first
            self.target_process.terminate()  # SIGTERM
            self.target_process.join(timeout=5.0)
            
            # If still alive, force kill
            if self.target_process.is_alive():
                self.target_process.kill()  # SIGKILL
                self.target_process.join()
    
    def handle_control_message(self, msg_data: dict) -> None:
        """Handle control messages with authority over target thread."""
        message = msg_data.get("message", "")
        
        if message == "STOP":
            self._terminate_target()
        elif message == "PAUSE":
            # Pause implementation would signal target thread
            pass
        # ... other control messages
    
    # Message handlers
    def handle_control_message(self, msg_data: dict) -> None
    def handle_work_message(self, msg_data: dict) -> None  
    def handle_reserved_message(self, msg_data: dict) -> None
    
    # Core execution
    def _execute_target(self) -> Any        # Run function/command
    def _monitor_resources(self) -> None    # Track usage
    
    # Process title methods
    def _compute_short_tid(self) -> str
    def _check_setproctitle(self) -> bool
    def _update_process_title(self, status: str, details: str = None) -> None
    def _register_tid_mapping(self) -> None
    def _format_process_title(self, status: str, details: str = None) -> str
```

**Process Title Management**:
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
            weft-{tid_short}:{name}:{status}
            weft-{tid_short}:{name}:{status}:{details}
            
        Examples:
            weft-0161024:git-clone:init
            weft-0161024:git-clone:running
            weft-0161024:analyze:failed:timeout
            weft-0161025:process:running:50
            
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
            - Colons separate logical sections
            - No special shell characters requiring escaping
            - Easy to parse with standard tools (cut, awk, grep)
        """
        import re
        
        # Start with prefix-tid format (hyphen separates prefix from ID)
        parts = [f"{self.PROCESS_TITLE_PREFIX}-{self.tid_short}"]
        
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
        
        Saves mapping to weft.tid.mappings queue for tools that need
        to resolve short TIDs to full TIDs.
        
        Message format:
            {
                "short": "0161024",
                "full": "1837025672140161024",
                "pid": 12345,
                "name": "git-clone",
                "started": 1837025672140161024,
                "hostname": "server1"
            }
        """
        try:
            import socket
            mapping = {
                "short": self.tid_short,
                "full": self.tid,
                "pid": os.getpid(),
                "name": self.taskspec.name,
                "started": time.time_ns(),
                "hostname": socket.gethostname()
            }
            Queue(self.TID_MAPPING_QUEUE).write(json.dumps(mapping))
            logger.debug(f"Registered TID mapping: {self.tid_short} -> {self.tid}")
        except Exception as e:
            logger.warning(f"Failed to register TID mapping: {e}")
```

**Execution Flow**:
1. Initialize with TaskSpec
2. Set initial process title "weft-{tid_short}:name:init"
3. Register TID mapping to weft.tid.mappings queue
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
            tid_short = tid[-10:]
            setproctitle.setproctitle(f"weft-worker-{tid_short}:{target[0]}")
        
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

tid_short = "{tid[-10:]}"
setproctitle.setproctitle(f"weft-worker-{{tid_short}}:{target[0]}")
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
            tid_short = tid[-10:]
            setproctitle.setproctitle(f"weft-func-{tid_short}:{name}")
            return target(*args, **kwargs)
        
        # Use multiprocessing for true process isolation
        with multiprocessing.Pool(1) as pool:
            result = pool.apply_async(wrapped_target, args, kwargs)
            return result.get(timeout=self.timeout)
```

This ensures that both the main task process and any subprocesses it spawns are identifiable in system tools.

## 4. ResourceMonitor (weft/core/monitor.py)
**Purpose**: Track and enforce resource limits using psutil as the default implementation

**Default Implementation**: The system uses psutil for cross-platform resource monitoring. The `monitor_class` field in TaskSpec defaults to `"weft.core.monitor.ResourceMonitor"` which is psutil-based.

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
weft.tid.mappings    # Maps short TIDs to full TIDs for process management

# Worker coordination queues
weft.workers.registry   # Registry of active workers and their capabilities
weft.spawn.requests     # Global queue for task spawn requests
```

**TID Mapping Queue Structure**:
The `weft.tid.mappings` queue maintains a persistent record of TID mappings for process management:

```python
# Message format in weft.tid.mappings
{
    "short": "0161024",              # Last 10 digits of TID
    "full": "1837025672140161024",   # Complete 19-digit TID
    "pid": 12345,                     # Process ID
    "name": "git-clone",              # Task name
    "started": 1837025672140161024,  # Start timestamp (nanoseconds)
    "hostname": "server1"             # Host where task is running
}
```

This enables reverse lookup when managing tasks via OS commands:
```bash
# Find full TID from process (no escaping needed!)
ps aux | grep "weft-0161024"     # Shows: weft-0161024:git-clone:running
# Lookup full TID
weft tid-lookup 0161024          # Returns: 1837025672140161024
# Use full TID for queue operations
weft queue read T1837025672140161024.outbox

# Shell-friendly operations
tid=$(ps aux | grep weft- | cut -d- -f2 | cut -d: -f1)  # Extract TID
pkill -f "weft-.*:failed"        # Kill failed tasks (no escaping!)
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
├── worker.py                     # WorkerTask (recursive architecture)  
├── executor.py                   # Target execution (function/command)
├── monitor.py                    # Resource monitoring with psutil
└── manager.py                    # TaskManager for submission/querying

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
| **WorkerTask** | `weft.core.worker` | Recursive worker implementation |
| **TaskManager** | `weft.core.manager` | Task submission and lifecycle management |
| **ResourceMonitor** | `weft.core.monitor` | psutil-based resource monitoring |
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
from weft.core.manager import TaskManager
from weft.core.worker import WorkerTask
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
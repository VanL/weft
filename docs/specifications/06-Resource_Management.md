# Resource Management and Error Handling

This document covers resource monitoring, limit enforcement, and comprehensive error handling strategies.

_Implementation snapshot_: `weft/core.resource_monitor.py` and `TaskRunner` provide psutil-based measurements and limit enforcement. The specialised manager classes illustrated below are design references and are not yet implemented as standalone components.

## Resource Management [RM-0]

### 1. Memory Management [RM-1]
_Implementation status_: Memory monitoring and limit checking are handled generically in `PsutilResourceMonitor.check_limits`; dedicated `MemoryManager` helpers are not yet present.
```python
class MemoryManager:
    def check_limit(self, current_mb: float, limit_mb: int) -> bool
    def calculate_rss(self, pid: int) -> float
    def enforce_limit(self, pid: int, limit_mb: int) -> None
```

**Strategies**:
- Soft limit: Warning at 90%
- Hard limit: Kill at 100%
- Grace period: 5 seconds between soft and hard

### 2. CPU Management [RM-2]
_Implementation status_: CPU limit evaluation leverages the rolling average in `PsutilResourceMonitor`; throttling strategies (nice/renice, cgroups) remain future work.
```python
class CPUManager:
    def calculate_usage(self, pid: int) -> float
    def throttle_process(self, pid: int, limit_percent: int) -> None
    def is_sustained_violation(self, history: list[float], limit: int) -> bool
```

**Strategies**:
- Moving average over 10 samples
- Sustained violation: 5 consecutive over-limit samples
- Throttling via nice/renice or cgroups

### 3. File Descriptor Management [RM-3]
_Implementation status_: `PsutilResourceMonitor` gathers open file counts and enforces configured limits. Cleanup routines are TODO.
```python
class FDManager:
    def count_open_fds(self, pid: int) -> int
    def enforce_limit(self, pid: int, limit: int) -> None
    def cleanup_on_exit(self, pid: int) -> None
```

### 4. Network Connection Management [RM-4]
_Implementation status_: Connection counts are monitored via psutil; proactive limiting/closure logic is not yet implemented.
```python
class NetworkManager:
    def count_connections(self, pid: int) -> int
    def limit_new_connections(self, pid: int, limit: int) -> None
    def close_idle_connections(self, pid: int, idle_seconds: int) -> None
```

## Resource Monitoring Implementation [RM-5]

### Default psutil-Based Monitor [RM-5.1]
_Implementation_: `PsutilResourceMonitor` in `weft/core.resource_monitor.py` corresponds to this section and is used by `TaskRunner`.

```python
class ResourceMonitor:
    """Default resource monitor using psutil for cross-platform support."""
    
    def __init__(self, limits: LimitsSection, polling_interval: float = 1.0):
        self.limits = limits
        self.polling_interval = polling_interval
        self.process = None
        self.history = []
        self.max_history = 100
        self.metrics_queue = Queue("weft.metrics")
        
    def start_monitoring(self, pid: int) -> None:
        """Begin monitoring the specified process."""
        try:
            import psutil
            self.process = psutil.Process(pid)
            logger.info(f"Started monitoring PID {pid}")
        except (psutil.NoSuchProcess, ImportError) as e:
            logger.error(f"Failed to start monitoring PID {pid}: {e}")
            raise
    
    def get_current_metrics(self) -> ResourceMetrics:
        """Get current resource usage metrics."""
        if not self.process:
            raise RuntimeError("Monitoring not started")
        
        try:
            # Memory usage in MB
            memory_info = self.process.memory_info()
            memory_mb = memory_info.rss / (1024 * 1024)
            
            # CPU percentage
            cpu_percent = self.process.cpu_percent(interval=0.1)
            
            # File descriptors (Unix-specific)
            try:
                open_fds = len(self.process.open_files())
            except (AttributeError, psutil.AccessDenied):
                open_fds = 0
            
            # Network connections
            try:
                connections = len(self.process.connections())
            except (AttributeError, psutil.AccessDenied):
                connections = 0
            
            metrics = ResourceMetrics(
                timestamp=self.metrics_queue.generate_timestamp(),
                memory_mb=memory_mb,
                cpu_percent=cpu_percent,
                open_files=open_fds,
                connections=connections
            )
            
            # Add to history
            self.history.append(metrics)
            if len(self.history) > self.max_history:
                self.history.pop(0)
            
            return metrics
            
        except psutil.NoSuchProcess:
            logger.warning("Process no longer exists")
            raise ProcessGoneError("Monitored process has terminated")
    
    def check_limits(self) -> tuple[bool, str | None]:
        """Check if current usage violates any limits."""
        if not self.limits:
            return True, None
        
        try:
            metrics = self.get_current_metrics()
        except ProcessGoneError:
            return True, None  # Process gone, no violation
        
        violations = []
        
        # Check memory limit
        if (self.limits.memory_mb and 
            metrics.memory_mb > self.limits.memory_mb):
            violations.append(
                f"Memory {metrics.memory_mb:.1f}MB > {self.limits.memory_mb}MB"
            )
        
        # Check CPU limit (sustained violation)
        if (self.limits.cpu_percent and 
            self._is_sustained_cpu_violation()):
            avg_cpu = self._get_average_cpu(samples=5)
            violations.append(
                f"CPU {avg_cpu:.1f}% > {self.limits.cpu_percent}% (sustained)"
            )
        
        # Check file descriptor limit
        if (self.limits.max_fds and 
            metrics.open_files > self.limits.max_fds):
            violations.append(
                f"Open files {metrics.open_files} > {self.limits.max_fds}"
            )
        
        # Check connection limit
        if (self.limits.max_connections and 
            metrics.connections > self.limits.max_connections):
            violations.append(
                f"Connections {metrics.connections} > {self.limits.max_connections}"
            )
        
        if violations:
            return False, "; ".join(violations)
        
        return True, None
    
    def _is_sustained_cpu_violation(self) -> bool:
        """Check if CPU usage has been over limit for multiple samples."""
        if not self.limits.cpu_percent or len(self.history) < 5:
            return False
        
        recent_samples = self.history[-5:]
        violations = sum(1 for m in recent_samples 
                        if m.cpu_percent > self.limits.cpu_percent)
        
        return violations >= 4  # 4 out of 5 samples over limit
    
    def _get_average_cpu(self, samples: int = 10) -> float:
        """Get average CPU usage over recent samples."""
        if not self.history:
            return 0.0
        
        recent = self.history[-samples:]
        return sum(m.cpu_percent for m in recent) / len(recent)
    
    def get_max_metrics(self) -> ResourceMetrics:
        """Get maximum values seen during monitoring."""
        if not self.history:
            return ResourceMetrics()
        
        return ResourceMetrics(
            timestamp=self.metrics_queue.generate_timestamp(),
            memory_mb=max(m.memory_mb for m in self.history),
            cpu_percent=max(m.cpu_percent for m in self.history),
            open_files=max(m.open_files for m in self.history),
            connections=max(m.connections for m in self.history)
        )
    
    def stop_monitoring(self) -> None:
        """Stop monitoring and cleanup."""
        self.process = None
        logger.info("Stopped resource monitoring")
```

### Resource Metrics Data Model

```python
@dataclass
class ResourceMetrics:
    """Resource usage metrics at a point in time."""
    timestamp: int = 0
    memory_mb: float = 0.0
    cpu_percent: float = 0.0
    open_files: int = 0
    connections: int = 0
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "timestamp": self.timestamp,
            "memory_mb": round(self.memory_mb, 2),
            "cpu_percent": round(self.cpu_percent, 1),
            "open_files": self.open_files,
            "connections": self.connections
        }
    
    def exceeds_limits(self, limits: LimitsSection) -> list[str]:
        """Check which limits are exceeded."""
        violations = []
        
        if limits.memory_mb and self.memory_mb > limits.memory_mb:
            violations.append(f"memory")
        
        if limits.cpu_percent and self.cpu_percent > limits.cpu_percent:
            violations.append(f"cpu")
        
        if limits.max_fds and self.open_files > limits.max_fds:
            violations.append(f"fds")
        
        if limits.max_connections and self.connections > limits.max_connections:
            violations.append(f"connections")
        
        return violations
```

## Error Handling

### 1. Unified Reservation Pattern for Error Handling

The system uses a **single reservation pattern** where the `.reserved` queue serves as both work-in-progress and dead-letter queue, leveraging SimpleBroker's atomic move operation:

```python
class Task:
    def process_with_reservation(self):
        # 1. Use SimpleBroker's atomic move for reservation
        msg = Queue(f"T{self.tid}.inbox").move_one(f"T{self.tid}.reserved")
        
        try:
            # 2. Process message
            result = self.execute(msg)
            
            # 3. Success: write result and clear reservation
            Queue(f"T{self.tid}.outbox").write(result)
            Queue(f"T{self.tid}.reserved").delete(msg.id)
            self.taskspec.mark_completed()
            
        except Exception as e:
            # 4. Failure: leave in reserved, update state
            self.taskspec.mark_failed(error=str(e))
            # Message naturally becomes "dead letter" in reserved queue
            
        # 5. Report state change
        Queue("weft.tasks.log").write(self.taskspec.to_log_dict())
```

### 2. Error Categories and State Tracking

| Category | Examples | State Update | Message Location |
|----------|----------|--------------|------------------|
| Configuration | Invalid TaskSpec | state.error="Invalid config" | Never reaches reserved |
| Execution | Import error, command not found | state.error="Execution failed" | Stays in reserved |
| Resource | Memory limit exceeded | state.status="killed", state.error="Memory limit" | Stays in reserved |
| Timeout | Execution timeout | state.status="timeout" | Stays in reserved |
| Queue | Connection lost | Retry internally | May stay in inbox |
| System | Disk full, permission denied | state.error="System error" | Stays in reserved |

### 3. Recovery Strategies

**Option 1: Retry from Reserved Queue**
```python
def retry_failed_task(tid: str):
    """Retry processing messages still in reserved."""
    taskspec = load_taskspec_from_log(tid)
    if taskspec.state.status == "failed":
        # Reset state for retry
        taskspec.state.status = "retrying"
        taskspec.state.error = None
        
        # Process what's in reserved
        task = Task(taskspec)
        for msg in Queue(f"T{tid}.reserved").peek_all():
            task.process(msg)
```

**Option 2: Move Back to Inbox**
```python
def reset_failed_task(tid: str):
    """Move failed messages back to inbox for fresh attempt."""
    Queue(f"T{tid}.reserved").move_all(f"T{tid}.inbox")
    # Clear error state
    update_task_state(tid, status="created", error=None)
```

**Option 3: Manual Intervention**
```python
def inspect_failed_task(tid: str):
    """Inspect failed task for debugging."""
    # Check state
    state = get_task_state_from_log(tid)
    print(f"Failed with: {state.error}")
    
    # Check reserved messages
    reserved = Queue(f"T{tid}.reserved").peek_all()
    print(f"Messages stuck: {len(reserved)}")
    
    # Manual resolution options
    # - Fix and retry
    # - Abandon (delete from reserved)
    # - Transform and requeue
```

### 4. Error Visibility Through State

All errors are tracked in TaskSpec state and reported to `weft.tasks.log`:

```python
# Error tracking in state
class StateSection:
    status: Literal["failed", "timeout", "killed", ...]
    error: str | None  # Error message/reason
    return_code: int | None  # Process exit code (124 for timeout)

# Global visibility
def monitor_failures():
    """Monitor all task failures from global log."""
    for entry in Queue("weft.tasks.log").peek_all():
        task = json.loads(entry)
        if task['status'] in ['failed', 'timeout', 'killed']:
            alert(f"Task {task['tid']} failed: {task['error']}")
```

### 5. Resilience Patterns

**Automatic Retry with State**:
```python
class ResilientTask(Task):
    def __init__(self, taskspec: TaskSpec, max_retries: int = 3):
        super().__init__(taskspec)
        self.max_retries = max_retries
        
    def run_forever(self):
        # Check for messages in reserved (previous failure)
        if self._has_reserved_messages():
            self._retry_reserved()
        
        # Normal processing
        super().run_forever()
    
    def _retry_reserved(self):
        """Retry messages left in reserved from previous run."""
        attempts = self.taskspec.metadata.get("retry_attempts", 0)
        if attempts < self.max_retries:
            self.taskspec.metadata["retry_attempts"] = attempts + 1
            self._process_reserved_messages()
        else:
            self.taskspec.mark_failed(error="Max retries exceeded")
```

**Circuit Breaker with Queue State**:
```python
def should_process_task(tid: str) -> bool:
    """Check if task should be processed based on failure history."""
    failures = count_recent_failures(tid)  # From weft.tasks.log
    if failures > 5:
        # Circuit open - skip processing
        return False
    return True
```

## Security Considerations

### Command Execution Safety
- **Always use array form**: `["cmd", "arg1", "arg2"]` never shell strings
- **Never use shell=True**: Prevents command injection attacks
- **Validate command arrays**: Check executables exist and are allowed

```python
class CommandValidator:
    """Validate commands before execution."""
    
    def validate_command(self, command: list[str]) -> None:
        """Ensure command is safe to execute."""
        if not command:
            raise ValueError("Empty command")
        
        # Never allow shell metacharacters in first element
        executable = command[0]
        if any(char in executable for char in ";|&<>$`"):
            raise ValueError(f"Invalid characters in command: {executable}")
        
        # Optionally check against allowlist
        if self.allowlist and executable not in self.allowlist:
            raise ValueError(f"Command not in allowlist: {executable}")
```

### Resource Isolation (v1: Monitor & React)
- **Current approach**: Tasks run under user permissions with monitoring
- **Resource enforcement**: Limits checked periodically, violators terminated
- **Future enhancement**: Sandbox options via cgroups/containers

```python
class ResourceEnforcer:
    """Enforce resource limits through monitoring."""
    
    def enforce_limits(self, pid: int, limits: LimitsSection) -> None:
        """Monitor and enforce resource limits."""
        process = psutil.Process(pid)
        
        # Check memory limit
        if limits.memory_mb:
            mem_mb = process.memory_info().rss / (1024 * 1024)
            if mem_mb > limits.memory_mb:
                process.terminate()
                raise ResourceError(f"Memory limit exceeded: {mem_mb}MB > {limits.memory_mb}MB")
        
        # Check CPU limit (average over interval)
        if limits.cpu_percent:
            cpu_percent = process.cpu_percent(interval=1.0)
            if cpu_percent > limits.cpu_percent:
                # Throttle or terminate based on policy
                self._throttle_or_terminate(process, cpu_percent, limits.cpu_percent)
```

### Environment Variable Safety
- **Update, not replace**: Task env updates parent environment
- **Preserve critical paths**: Never override PYTHONPATH, LD_LIBRARY_PATH
- **Sanitize values**: Remove shell expansion characters

```python
def prepare_environment(base_env: dict, task_env: dict) -> dict:
    """Safely merge task environment with base environment."""
    # Start with parent environment
    env = base_env.copy()
    
    # Protected variables that cannot be overridden
    protected = {"PATH", "PYTHONPATH", "LD_LIBRARY_PATH", "HOME", "USER"}
    
    # Update with task environment, skipping protected
    for key, value in task_env.items():
        if key not in protected:
            # Sanitize value (remove shell expansion)
            sanitized = value.replace("$", "").replace("`", "")
            env[key] = sanitized
    
    return env
```

### AI Agent Constraints
- **Pre-defined task registry**: Only registered tasks can be created
- **No dynamic execution**: Agents cannot create arbitrary commands
- **Audit trail**: All operations logged to weft.tasks.log

```python
class TaskRegistry:
    """Registry of allowed tasks for AI agents."""
    
    def __init__(self):
        self._allowed_tasks = {}
        
    def register_task(self, name: str, spec_template: dict) -> None:
        """Register an allowed task template."""
        # Validate spec_template is safe
        if spec_template.get("spec", {}).get("type") == "command":
            # Validate command is in allowlist
            self._validate_command_spec(spec_template)
        
        self._allowed_tasks[name] = spec_template
    
    def create_task(self, name: str, params: dict) -> TaskSpec:
        """Create task from registered template."""
        if name not in self._allowed_tasks:
            raise ValueError(f"Task not registered: {name}")
        
        # Apply parameters to template
        spec = self._apply_params(self._allowed_tasks[name], params)
        return TaskSpec.model_validate(spec)
```

### Large Output Security
- **Path validation**: Output paths confined to /tmp/weft/outputs/
- **Size limits**: Enforce maximum output size per task
- **Cleanup policy**: Automatic removal of old outputs

```python
def validate_output_path(path: str, tid: str) -> None:
    """Ensure output path is within allowed directory."""
    resolved = Path(path).resolve()
    allowed_base = Path(f"/tmp/weft/outputs/{tid}").resolve()
    
    if not str(resolved).startswith(str(allowed_base)):
        raise ValueError(f"Invalid output path: {path}")
```

## Related Documents

- **[00-Overview_and_Architecture.md](00-Overview_and_Architecture.md)** - System overview and design principles
- **[01-TaskSpec.md](01-TaskSpec.md)** - Task configuration specification including limits
- **[02-Core_Components.md](02-Core_Components.md)** - Detailed component architecture
- **[03-Worker_Architecture.md](03-Worker_Architecture.md)** - Recursive worker model
- **[04-SimpleBroker_Integration.md](04-SimpleBroker_Integration.md)** - Queue system integration
- **[05-Message_Flow_and_State.md](05-Message_Flow_and_State.md)** - Communication patterns
- **[07-System_Invariants.md](07-System_Invariants.md)** - System guarantees and constraints

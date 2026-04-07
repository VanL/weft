# Resource Management and Error Handling

This document covers resource monitoring, limit enforcement, and comprehensive error handling strategies.

_Implementation snapshot_: `weft/core/resource_monitor.py` and `TaskRunner` provide psutil-based measurements and limit enforcement. The specialized manager classes illustrated below are design references and are not yet implemented as standalone components.

_Implementation mapping_: `weft/core/resource_monitor.py` (`PsutilResourceMonitor`, `BaseResourceMonitor`, `ResourceMetrics`), `weft/core/runners/host.py` (`HostTaskRunner.run_with_hooks` — monitoring loop and limit enforcement), `weft/core/tasks/runner.py` (`TaskRunner` — facade), `weft/core/tasks/consumer.py` (`Consumer._ensure_outcome_ok` — limit/timeout state transitions).

## Resource Management [RM-0]

### 1. Memory Management [RM-1]
_Implementation status_: Memory monitoring and limit checking are handled generically in `PsutilResourceMonitor.check_limits`; dedicated `MemoryManager` helpers are not yet present.

_Implementation mapping_: `weft/core/resource_monitor.py` — `PsutilResourceMonitor.check_limits` (memory limit comparison), `PsutilResourceMonitor.get_current_metrics` (RSS calculation via `process.memory_info().rss`). `weft/core/taskspec.py` — `LimitsSection.memory_mb`.

```python
class MemoryManager:
    def check_limit(self, current_mb: float, limit_mb: int) -> bool
    def calculate_rss(self, pid: int) -> float
    def enforce_limit(self, pid: int, limit_mb: int) -> None
```

**Strategies**:
- Soft limit: Warning at 90% **[NOT YET IMPLEMENTED]** — current implementation is hard-kill only
- Hard limit: Kill at 100%
- Grace period: 5 seconds between soft and hard **[NOT YET IMPLEMENTED]** — no grace period logic exists

### 2. CPU Management [RM-2]
_Implementation status_: CPU limit evaluation leverages the rolling average in `PsutilResourceMonitor`; throttling strategies (nice/renice, cgroups) remain future work.

_Implementation mapping_: `weft/core/resource_monitor.py` — `PsutilResourceMonitor._is_sustained_cpu_violation` (4-of-5 samples check), `PsutilResourceMonitor._get_average_cpu`, `PsutilResourceMonitor._snapshot_cpu_times` (tree-wide CPU time delta). `weft/core/taskspec.py` — `LimitsSection.cpu_percent`.

```python
class CPUManager:
    def calculate_usage(self, pid: int) -> float
    def throttle_process(self, pid: int, limit_percent: int) -> None
    def is_sustained_violation(self, history: list[float], limit: int) -> bool
```

**Strategies**:
- Moving average over 10 samples — implemented as `_get_average_cpu(samples=10)`, violation uses last 5 samples (4-of-5 threshold)
- Sustained violation: 5 consecutive over-limit samples — implemented as 4-of-5 in `_is_sustained_cpu_violation`
- Throttling via nice/renice or cgroups **[NOT YET IMPLEMENTED]** — violators are terminated, not throttled

### 3. File Descriptor Management [RM-3]
_Implementation status_: `PsutilResourceMonitor` gathers open file counts and enforces configured limits. Cleanup routines are TODO.

_Implementation mapping_: `weft/core/resource_monitor.py` — `PsutilResourceMonitor._open_file_count` (fd counting via `num_fds`/`num_handles`/`open_files` fallback chain), `PsutilResourceMonitor.check_limits` (fd limit comparison). `weft/core/taskspec.py` — `LimitsSection.max_fds`.

```python
class FDManager:
    def count_open_fds(self, pid: int) -> int
    def enforce_limit(self, pid: int, limit: int) -> None
    def cleanup_on_exit(self, pid: int) -> None  # [NOT YET IMPLEMENTED]
```

### 4. Network Connection Management [RM-4]
_Implementation status_: Connection counts are monitored via psutil; proactive limiting/closure logic is not yet implemented.

_Implementation mapping_: `weft/core/resource_monitor.py` — `PsutilResourceMonitor._connection_count` (connection counting via `net_connections`/`connections` fallback), `PsutilResourceMonitor.check_limits` (connection limit comparison). `weft/core/taskspec.py` — `LimitsSection.max_connections`.

```python
class NetworkManager:
    def count_connections(self, pid: int) -> int
    def limit_new_connections(self, pid: int, limit: int) -> None  # [NOT YET IMPLEMENTED]
    def close_idle_connections(self, pid: int, idle_seconds: int) -> None  # [NOT YET IMPLEMENTED]
```

## Resource Monitoring Implementation [RM-5]

### Default psutil-Based Monitor [RM-5.1]
_Implementation_: `PsutilResourceMonitor` in `weft/core/resource_monitor.py` corresponds to this section and is used by `HostTaskRunner`.

_Implementation mapping_: `weft/core/resource_monitor.py` (`PsutilResourceMonitor`, `BaseResourceMonitor`, `ResourceMonitor` alias, `load_resource_monitor`), `weft/core/runners/host.py` (`HostTaskRunner.run_with_hooks` — instantiates and polls monitor), `weft/core/tasks/runner.py` (`TaskRunner` — facade that delegates to runner plugin).

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

_Implementation mapping_: `weft/core/resource_monitor.py` (`ResourceMetrics`).

## Error Handling

### 1. Unified Reservation Pattern for Error Handling

_Implementation mapping_: `weft/core/tasks/consumer.py` (`Consumer._handle_work_message` — reserve semantics, `Consumer._finalize_message` — delete from reserved on success, `Consumer._apply_reserved_policy_on_error` — policy on failure), `weft/core/tasks/base.py` (`BaseTask._apply_reserved_policy`).

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
        Queue("weft.log.tasks").write(self.taskspec.to_log_dict())
```

### 2. Error Categories and State Tracking

_Implementation mapping_: `weft/core/tasks/consumer.py` (`Consumer._ensure_outcome_ok` — routes timeout/limit/error/cancelled outcomes to `mark_timeout`, `mark_killed`, `mark_failed`), `weft/core/taskspec.py` (`TaskSpec.mark_*` state transitions, `StateSection`).

| Category | Examples | State Update | Message Location |
|----------|----------|--------------|------------------|
| Configuration | Invalid TaskSpec | state.error="Invalid config" | Never reaches reserved |
| Execution | Import error, command not found | state.error="Execution failed" | Stays in reserved |
| Resource | Memory limit exceeded | state.status="killed", state.error="Memory limit" | Stays in reserved |
| Timeout | Execution timeout | state.status="timeout" | Stays in reserved |
| Queue | Connection lost | Retry internally | May stay in inbox |
| System | Disk full, permission denied | state.error="System error" | Stays in reserved |

### 3. Recovery Strategies

**[NOT YET IMPLEMENTED]** — None of the three recovery strategies below are implemented as callable APIs. Reserved-queue messages persist for manual inspection via `weft queue peek`, but programmatic retry/reset helpers do not exist.

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

All errors are tracked in TaskSpec state and reported to `weft.log.tasks`:

```python
# Error tracking in state
class StateSection:
    status: Literal["failed", "timeout", "killed", ...]
    error: str | None  # Error message/reason
    return_code: int | None  # Process exit code (124 for timeout)

# Global visibility
def monitor_failures():
    """Monitor all task failures from global log."""
    for entry in Queue("weft.log.tasks").peek_all():
        task = json.loads(entry)
        if task['status'] in ['failed', 'timeout', 'killed']:
            alert(f"Task {task['tid']} failed: {task['error']}")
```

_Implementation mapping_: `weft/core/tasks/consumer.py` (`Consumer._ensure_outcome_ok` — timeout/limit/error branching, `Consumer._report_state_change` — writes to `weft.log.tasks`), `weft/core/taskspec.py` (`TaskSpec.mark_timeout`, `TaskSpec.mark_killed`, `TaskSpec.mark_failed`, `StateSection.error`, `StateSection.return_code`).

### 5. Resilience Patterns

**[NOT YET IMPLEMENTED]** — Neither `ResilientTask` (automatic retry) nor the circuit breaker pattern is implemented. These remain design-only references.

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
    failures = count_recent_failures(tid)  # From weft.log.tasks
    if failures > 5:
        # Circuit open - skip processing
        return False
    return True
```

## Security Considerations

### Command Execution Safety

_Implementation mapping_: `weft/core/targets.py` (`execute_command_target` — builds command array, uses `subprocess.run` without `shell=True`), `weft/core/runners/host.py` (`HostTaskRunner.start_session` — `subprocess.Popen` without `shell=True`).

- **Always use array form**: `["cmd", "arg1", "arg2"]` never shell strings — implemented
- **Never use shell=True**: Prevents command injection attacks — implemented (no `shell=True` in codebase)
- **Validate command arrays**: Check executables exist and are allowed

**[NOT YET IMPLEMENTED]** — The `CommandValidator` class with allowlist support is a design reference. No dedicated validator or allowlist exists in the codebase.

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

_Implementation mapping_: `weft/core/runners/host.py` (`HostTaskRunner.run_with_hooks` — polling loop calls `monitor.check_limits()`, terminates on violation), `weft/core/resource_monitor.py` (`PsutilResourceMonitor.check_limits`).

- **Current approach**: Tasks run under user permissions with monitoring — implemented
- **Resource enforcement**: Limits checked periodically, violators terminated — implemented
- **Future enhancement**: Sandbox options via cgroups/containers **[NOT YET IMPLEMENTED]**

**[NOT YET IMPLEMENTED]** — The standalone `ResourceEnforcer` class below is a design reference. Enforcement is inline in `HostTaskRunner.run_with_hooks`.

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

**[NOT YET IMPLEMENTED]** — The `prepare_environment` function with protected-variable guard and sanitization is a design reference. The current host runner (`HostTaskRunner.start_session`) merges env without protected-variable checks or sanitization.

_Implementation mapping (partial)_: `weft/core/runners/host.py` (`HostTaskRunner.start_session` — merges `os.environ` with spec env, sets `PYTHONUNBUFFERED`). No protected-variable enforcement or value sanitization exists.

- **Update, not replace**: Task env updates parent environment — partially implemented (env merged in `start_session`)
- **Preserve critical paths**: Never override PYTHONPATH, LD_LIBRARY_PATH **[NOT YET IMPLEMENTED]**
- **Sanitize values**: Remove shell expansion characters **[NOT YET IMPLEMENTED]**

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

**[NOT YET IMPLEMENTED]** — The `TaskRegistry` class and allowlist-based agent constraints are design references. The audit trail via `weft.log.tasks` is implemented.

- **Pre-defined task registry**: Only registered tasks can be created **[NOT YET IMPLEMENTED]**
- **No dynamic execution**: Agents cannot create arbitrary commands **[NOT YET IMPLEMENTED]**
- **Audit trail**: All operations logged to weft.log.tasks — implemented via `Consumer._report_state_change`

```python
class TaskRegistry:
    """Registry of allowed tasks for AI agents."""
    
    def __init__(self):
        self._allowed_tasks = {}
        
    def register_task(self, name: str, spec_payload: dict) -> None:
        """Register an allowed task spec payload."""
        # Validate spec_payload is safe
        if spec_payload.get("spec", {}).get("type") == "command":
            # Validate command is in allowlist
            self._validate_command_spec(spec_payload)
        
        self._allowed_tasks[name] = spec_payload
    
    def create_task(self, name: str, params: dict) -> TaskSpec:
        """Create task from registered spec payload."""
        if name not in self._allowed_tasks:
            raise ValueError(f"Task not registered: {name}")
        
        # Apply parameters to spec payload
        spec = self._apply_params(self._allowed_tasks[name], params)
        return TaskSpec.model_validate(spec)
```

### Large Output Security

_Implementation mapping_: `weft/core/tasks/consumer.py` (`Consumer._emit_single_output` — size limit enforcement and spill), `weft/core/tasks/base.py` (`BaseTask._spill_large_output`, `BaseTask._cleanup_spilled_outputs_if_needed`), `weft/core/taskspec.py` (`SpecSection.output_size_limit_mb`).

- **Path validation**: Output paths confined to .weft/outputs/ — partially implemented (spill uses `context.py` paths)
- **Size limits**: Enforce maximum output size per task — implemented via `output_size_limit_mb`
- **Cleanup policy**: Automatic removal of old outputs — implemented in `Consumer.cleanup` when `cleanup_on_exit` is true

**[NOT YET IMPLEMENTED]** — The explicit `validate_output_path` function below is a design reference. The spill mechanism writes to a context-derived directory but does not perform traversal-safe path validation.

```python
def validate_output_path(path: str, tid: str) -> None:
    """Ensure output path is within allowed directory."""
    resolved = Path(path).resolve()
    allowed_base = Path(f".weft/outputs/{tid}").resolve()

    if not str(resolved).startswith(str(allowed_base)):
        raise ValueError(f"Invalid output path: {path}")
```

## Related Plans

- [runner-extension-point-plan.md](../plans/runner-extension-point-plan.md) — references this spec for resource monitor plugin architecture and limit enforcement in the host runner.
- [agent-runtime-implementation-plan.md](../plans/agent-runtime-implementation-plan.md) — references this spec for resource management in agent task execution.

## Related Documents

- **[00-Overview_and_Architecture.md](00-Overview_and_Architecture.md)** - System overview and design principles
- **[01-TaskSpec.md](01-TaskSpec.md)** - Task configuration specification including limits
- **[02-Core_Components.md](02-Core_Components.md)** - Detailed component architecture
- **[03-Worker_Architecture.md](03-Worker_Architecture.md)** - Recursive worker model
- **[04-SimpleBroker_Integration.md](04-SimpleBroker_Integration.md)** - Queue system integration
- **[05-Message_Flow_and_State.md](05-Message_Flow_and_State.md)** - Communication patterns
- **[07-System_Invariants.md](07-System_Invariants.md)** - System guarantees and constraints

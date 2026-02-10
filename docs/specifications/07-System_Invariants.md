# System Invariants and Constraints

This document defines the fundamental invariants, constraints, and guarantees that the Weft system maintains across all operations.

## System Invariants

_Implementation mapping_: `weft/core/taskspec.py` (immutability/state validation), `weft/core/tasks/base.py` (process titles/logging), `weft/core/manager.py` (worker registry), `weft/_constants.py` (queue names).

### Immutability Invariants
- **IMMUT.1**: TaskSpec.spec section is immutable after creation
- **IMMUT.2**: TaskSpec.io section is immutable after creation (runtime-expanded)
- **IMMUT.3**: Resolved TaskSpec.tid is immutable and unique (templates may omit tid)
- **IMMUT.4**: TaskSpec.state and metadata remain mutable for runtime updates

### State Machine Invariants
- **STATE.1**: States transition forward only (no rollback)
- **STATE.2**: Terminal states are immutable (completed, failed, timeout, cancelled, killed)
- **STATE.3**: Running state requires started_at timestamp
- **STATE.4**: Terminal states require completed_at timestamp
- **STATE.5**: completed_at > started_at when both present

### Queue Invariants
- **QUEUE.1**: Every task has exactly one inbox, reserved, and outbox queue
- **QUEUE.2**: Every task has exactly one ctrl_in and ctrl_out queue
- **QUEUE.3**: Queue names default to the "T{tid}." prefix when not overridden
- **QUEUE.4**: Messages are delivered exactly once (via claim/move semantics)
- **QUEUE.5**: Reserved queue contains at most one message per inbox message
- **QUEUE.6**: Failed messages remain in reserved queue with state.error set (unless reserved policy requeues or clears)

### Resource Invariants
- **RES.1**: Limits are grouped in spec.limits subsection
- **RES.2**: Memory limit > 0 MB when specified
- **RES.3**: 0 <= CPU limit <= 100% when specified
- **RES.4**: File descriptor limit >= 1 when specified
- **RES.5**: Current metrics <= peak metrics tracked
- **RES.6**: Resource violations trigger task termination

### Execution Invariants
- **EXEC.1**: Task executes target exactly once per run
- **EXEC.2**: Timeouts are enforced within 1 second precision
- **EXEC.3**: PID is set only when process/thread starts
- **EXEC.4**: Return code is set only on process completion

### Idempotency Invariants
- **IDEMP.1**: Single-message tasks may use `tid` as an idempotency key
- **IDEMP.2**: Multi-message tasks must use inbox/reserved message IDs for idempotency
- **IDEMP.3**: Recommended idempotency key format is `tid:message_id`

### Observability Invariants
- **OBS.1**: All state transitions logged to weft.log.tasks
- **OBS.2**: No separate state database (queue-based state only)
- **OBS.3**: State visible through both task queues and global log
- **OBS.4**: Process titles follow the format in `01-Core_Components.md` and update on transitions
- **OBS.5**: TID short form uses last 10 digits for uniqueness
- **OBS.6**: TID mappings saved to weft.state.tid_mappings queue
- **OBS.7**: Process titles sanitized to remove shell special characters
- **OBS.8**: Process titles use only alphanumeric, hyphen, colon, and underscore characters

### Implementation Invariants
- **IMPL.1**: Exit code 124 indicates timeout (GNU coreutils standard)
- **IMPL.2**: Messages limited to 10MB by SimpleBroker
- **IMPL.3**: Outputs >10MB written to `.weft/outputs/{tid}/` when `spec.weft_context` is set (runtime-expanded); otherwise to a temporary directory, with a reference message
- **IMPL.4**: Large output reference includes path, size, preview, and sha256
- **IMPL.5**: Queue connections must be recreated in child processes (no sharing)
- **IMPL.6**: Use multiprocessing.get_context("spawn") for process creation

### Worker Invariants
- **WORKER.1**: Workers are Tasks with long-running targets (timeout=None)
- **WORKER.2**: Worker TIDs follow same format as regular Task TIDs
- **WORKER.3**: Workers register capabilities in weft.state.workers
- **WORKER.4**: Spawn-request message ID becomes child Task TID for correlation
- **WORKER.5**: Workers can spawn other workers (recursive architecture)
- **WORKER.6**: Primordial manager bootstraps the system
- **WORKER.7**: Workers respond to the same control messages as Tasks (STOP/STATUS/PING)

### Context Invariants
- **CTX.1**: Each project context corresponds to one SimpleBroker database
- **CTX.2**: Tasks cannot communicate across different contexts
- **CTX.3**: Context cleanup removes all associated queues and data
- **CTX.4**: .weft/broker.db auto-created on first queue operation in context

## Validation and Enforcement

### Invariant Checking

```python
class InvariantChecker:
    """Validate system invariants during operation."""
    
    def __init__(self, context: WeftContext):
        self.context = context
    
    def check_taskspec_invariants(self, taskspec: TaskSpec) -> list[str]:
        """Check TaskSpec-related invariants."""
        violations = []
        
        # IMMUT.1/IMMUT.2: spec and io immutability (checked by Pydantic)
        if not hasattr(taskspec, '_spec_frozen'):
            violations.append("IMMUT.1/IMMUT.2: TaskSpec.spec/io not marked as frozen")
        
        # IMMUT.3: TID uniqueness and immutability (resolved specs only)
        if taskspec.tid is not None and len(taskspec.tid) != 19:
            violations.append("IMMUT.3: Invalid TID format")
        
        # RES.1-RES.4: Resource limit validation
        if taskspec.spec.limits:
            limits = taskspec.spec.limits
            if limits.memory_mb is not None and limits.memory_mb <= 0:
                violations.append("RES.2: Memory limit must be > 0 MB")
            
            if (limits.cpu_percent is not None and 
                not 0 <= limits.cpu_percent <= 100):
                violations.append("RES.3: CPU limit must be 0-100%")
            
            if limits.max_fds is not None and limits.max_fds < 1:
                violations.append("RES.4: FD limit must be >= 1")
        
        return violations
    
    def check_state_invariants(self, taskspec: TaskSpec) -> list[str]:
        """Check state transition invariants."""
        violations = []
        state = taskspec.state
        
        # STATE.3: Running state requires started_at
        if state.status == "running" and not state.started_at:
            violations.append("STATE.3: Running state missing started_at")
        
        # STATE.4: Terminal states require completed_at
        terminal_states = {"completed", "failed", "timeout", "cancelled", "killed"}
        if state.status in terminal_states and not state.completed_at:
            violations.append("STATE.4: Terminal state missing completed_at")
        
        # STATE.5: completed_at > started_at
        if (state.started_at and state.completed_at and 
            state.completed_at <= state.started_at):
            violations.append("STATE.5: completed_at must be > started_at")
        
        return violations
    
    def check_queue_invariants(self, tid: str) -> list[str]:
        """Check queue-related invariants."""
        violations = []
        
        # QUEUE.1/QUEUE.2: Required queues exist
        required_queues = ["inbox", "reserved", "outbox", "ctrl_in", "ctrl_out"]
        for queue_type in required_queues:
            queue_name = f"T{tid}.{queue_type}"
            if not self._queue_exists(queue_name):
                violations.append(f"QUEUE.1/QUEUE.2: Missing required queue {queue_name}")
        
        # QUEUE.5: Reserved queue has at most one message per inbox message
        reserved_count = self._count_messages(f"T{tid}.reserved")
        inbox_total = self._count_processed_messages(tid)  # From logs
        if reserved_count > inbox_total:
            violations.append("QUEUE.5: More reserved than processed messages")
        
        return violations
    
    def check_process_title_invariants(self, tid: str, title: str) -> list[str]:
        """Check process title format invariants."""
        violations = []
        
        # OBS.4: Shell-friendly format
        if not title.startswith("weft-"):
            violations.append("OBS.4: Process title must start with 'weft-'")
        
        # OBS.5: TID short form (last 10 digits)
        parts = title.split(":")
        if len(parts) < 2:
            violations.append("OBS.4: Invalid process title format")
        else:
            tid_part = parts[0].split("-")[-1]  # Extract TID from weft-{tid}
            if len(tid_part) != 10 or not tid_part.isdigit():
                violations.append("OBS.5: TID short form must be 10 digits")
            
            if tid_part != tid[-10:]:
                violations.append("OBS.5: TID short form mismatch")
        
        # OBS.7/OBS.8: Character restrictions
        allowed_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-:_")
        if not set(title).issubset(allowed_chars):
            violations.append("OBS.7/OBS.8: Process title contains forbidden characters")
        
        return violations
    
    def _queue_exists(self, queue_name: str) -> bool:
        """Check if queue exists in context."""
        try:
            # Requires a broker list call; implement via SimpleBroker list API.
            return queue_name in self.context.list_queues()
        except Exception:
            return False
    
    def _count_messages(self, queue_name: str) -> int:
        """Count messages in queue."""
        try:
            queue = self.context.get_queue(queue_name)
            return len(list(queue.peek_all()))
        except Exception:
            return 0
    
    def _count_processed_messages(self, tid: str) -> int:
        """Count total messages processed by task (from logs)."""
        try:
            log_queue = self.context.get_queue("weft.log.tasks")
            count = 0
            for msg_str in log_queue.peek_all():
                entry = json.loads(msg_str)
                if (entry.get("tid") == tid and 
                    entry.get("event") in ["message_processed", "message_failed"]):
                    count += 1
            return count
        except Exception:
            return 0
```

### Runtime Enforcement

```python
class InvariantEnforcer:
    """Enforce invariants during system operation."""
    
    def __init__(self, context: WeftContext):
        self.context = context
        self.checker = InvariantChecker(context)
    
    def enforce_on_task_creation(self, taskspec: TaskSpec) -> None:
        """Enforce invariants when creating a task."""
        violations = (
            self.checker.check_taskspec_invariants(taskspec) +
            self.checker.check_state_invariants(taskspec)
        )
        
        if violations:
            raise InvariantViolationError(f"Task creation violations: {violations}")
    
    def enforce_on_state_transition(self, taskspec: TaskSpec, 
                                   old_status: str, new_status: str) -> None:
        """Enforce invariants during state transitions."""
        # STATE.1: Forward-only transitions
        valid_transitions = {
            "created": {"spawning", "failed", "cancelled"},
            "spawning": {"running", "completed", "failed", "timeout", "cancelled", "killed"},
            "running": {"completed", "failed", "timeout", "cancelled", "killed"},
        }
        
        if old_status in valid_transitions:
            if new_status not in valid_transitions[old_status]:
                raise InvariantViolationError(
                    f"STATE.1: Invalid transition {old_status} -> {new_status}"
                )
        
        # STATE.2: Terminal states are immutable
        terminal_states = {"completed", "failed", "timeout", "cancelled", "killed"}
        if old_status in terminal_states and old_status != new_status:
            raise InvariantViolationError(
                f"STATE.2: Cannot transition from terminal state {old_status}"
            )
        
        # Check other state invariants
        violations = self.checker.check_state_invariants(taskspec)
        if violations:
            raise InvariantViolationError(f"State transition violations: {violations}")
    
    def enforce_on_process_title_update(self, tid: str, title: str) -> None:
        """Enforce process title invariants."""
        violations = self.checker.check_process_title_invariants(tid, title)
        if violations:
            raise InvariantViolationError(f"Process title violations: {violations}")
    
    def enforce_resource_limits(self, metrics: ResourceMetrics, 
                               limits: LimitsSection) -> None:
        """Enforce resource limit invariants."""
        # RES.5: Current <= peak
        if hasattr(metrics, 'peak_memory_mb'):
            if metrics.memory_mb > metrics.peak_memory_mb:
                raise InvariantViolationError(
                    "RES.5: Current memory exceeds tracked peak"
                )
        
        # RES.6: Resource violations trigger termination
        violations = metrics.exceeds_limits(limits)
        if violations:
            # This should trigger task termination
            raise ResourceLimitExceededError(
                f"RES.6: Resource limits exceeded: {violations}"
            )
```

### Testing Invariants

```python
class TestInvariants:
    """Test suite for invariant checking and enforcement."""
    
    def test_taskspec_immutability(self):
        """Test IMMUT.1/IMMUT.2: TaskSpec.spec/io immutability."""
        taskspec = create_test_taskspec()
        
        # Should not be able to modify spec after creation
        with pytest.raises(AttributeError):
            taskspec.spec.timeout = 999
        
        with pytest.raises(AttributeError):
            taskspec.io.inputs["new_queue"] = "test"
    
    def test_state_transitions(self):
        """Test STATE.1/STATE.2: State transition rules."""
        taskspec = create_test_taskspec()
        enforcer = InvariantEnforcer(test_context)
        
        # Valid transition
        enforcer.enforce_on_state_transition(taskspec, "created", "spawning")
        
        # Invalid transition (backward)
        with pytest.raises(InvariantViolationError):
            enforcer.enforce_on_state_transition(taskspec, "running", "created")
        
        # Terminal state immutability
        with pytest.raises(InvariantViolationError):
            enforcer.enforce_on_state_transition(taskspec, "completed", "running")
    
    def test_queue_structure(self):
        """Test QUEUE.1/QUEUE.2: Required queue structure."""
        tid = "1234567890123456789"
        checker = InvariantChecker(test_context)
        
        # Should fail without required queues
        violations = checker.check_queue_invariants(tid)
        assert any("Missing required queue" in v for v in violations)
        
        # Create required queues
        for queue_type in ["inbox", "reserved", "outbox", "ctrl_in", "ctrl_out"]:
            test_context.get_queue(f"T{tid}.{queue_type}")
        
        # Should pass with all queues
        violations = checker.check_queue_invariants(tid)
        assert not any("Missing required queue" in v for v in violations)
    
    def test_process_title_format(self):
        """Test OBS.4/OBS.5/OBS.7/OBS.8: Process title format."""
        tid = "1234567890123456789"
        checker = InvariantChecker(test_context)
        
        # Valid title
        title = "weft-3456789:test-task:running"
        violations = checker.check_process_title_invariants(tid, title)
        assert not violations
        
        # Invalid format
        bad_title = "bad-format"
        violations = checker.check_process_title_invariants(tid, bad_title)
        assert any("must start with 'weft-'" in v for v in violations)
        
        # Invalid characters
        bad_chars = "weft-3456789:test@task:running"
        violations = checker.check_process_title_invariants(tid, bad_chars)
        assert any("forbidden characters" in v for v in violations)
    
    def test_resource_limits(self):
        """Test RES.1-RES.6: Resource limit invariants."""
        # Valid limits
        limits = LimitsSection(
            memory_mb=512,
            cpu_percent=50,
            max_fds=100,
            max_connections=10
        )
        
        checker = InvariantChecker(test_context)
        taskspec = create_test_taskspec(limits=limits)
        violations = checker.check_taskspec_invariants(taskspec)
        assert not violations
        
        # Invalid limits
        bad_limits = LimitsSection(
            memory_mb=-100,  # Invalid: <= 0
            cpu_percent=150,  # Invalid: > 100
            max_fds=0  # Invalid: < 1
        )
        
        taskspec = create_test_taskspec(limits=bad_limits)
        violations = checker.check_taskspec_invariants(taskspec)
        assert len(violations) >= 3  # Should catch all three violations
    
    def test_worker_invariants(self):
        """Test WORKER.1-WORKER.7: Worker-specific invariants."""
        # Worker should be a Task with timeout=None
        worker_spec = create_worker_taskspec()
        assert worker_spec.spec.timeout is None
        assert worker_spec.spec.type == "function"
        
        # Worker TID should follow same format
        assert len(worker_spec.tid) == 19
        assert worker_spec.tid.isdigit()
```

## Error Classes

```python
class InvariantViolationError(Exception):
    """Raised when a system invariant is violated."""
    pass

class ResourceLimitExceededError(Exception):
    """Raised when resource limits are exceeded (RES.6)."""
    pass

class ProcessGoneError(Exception):
    """Raised when monitored process no longer exists."""
    pass

class StateTransitionError(InvariantViolationError):
    """Raised when invalid state transition is attempted."""
    pass

class QueueStructureError(InvariantViolationError):
    """Raised when queue structure invariants are violated."""
    pass
```

## Monitoring and Alerting

```python
class InvariantMonitor:
    """Monitor system for invariant violations."""
    
    def __init__(self, context: WeftContext):
        self.context = context
        self.checker = InvariantChecker(context)
        self.violation_count = 0
    
    def continuous_check(self, interval: float = 60.0) -> None:
        """Continuously monitor for invariant violations."""
        while True:
            try:
                violations = self.check_all_invariants()
                if violations:
                    self.violation_count += len(violations)
                    self.alert_violations(violations)
            except Exception as e:
                logger.error(f"Error during invariant checking: {e}")
            
            time.sleep(interval)
    
    def check_all_invariants(self) -> list[str]:
        """Check all invariants across the system."""
        all_violations = []
        
        # Check all active tasks
        for tid in self.get_active_task_tids():
            try:
                taskspec = self.load_taskspec(tid)
                violations = (
                    self.checker.check_taskspec_invariants(taskspec) +
                    self.checker.check_state_invariants(taskspec) +
                    self.checker.check_queue_invariants(tid)
                )
                all_violations.extend(violations)
            except Exception as e:
                all_violations.append(f"Error checking task {tid}: {e}")
        
        return all_violations
    
    def alert_violations(self, violations: list[str]) -> None:
        """Alert on invariant violations."""
        logger.critical(f"INVARIANT VIOLATIONS DETECTED: {violations}")
        
        # Could integrate with monitoring systems
        # - Send to alerting service
        # - Write to special alert queue
        # - Trigger emergency procedures
    
    def get_active_task_tids(self) -> list[str]:
        """Get list of active task TIDs."""
        # Extract from task log or queue listings
        pass
    
    def load_taskspec(self, tid: str) -> TaskSpec:
        """Load TaskSpec from state log."""
        # Reconstruct from weft.log.tasks
        pass
```

## Related Documents

- **[00-Overview_and_Architecture.md](00-Overview_and_Architecture.md)** - System overview and design principles
- **[01-TaskSpec.md](01-TaskSpec.md)** - Task configuration specification
- **[02-Core_Components.md](02-Core_Components.md)** - Detailed component architecture
- **[03-Worker_Architecture.md](03-Worker_Architecture.md)** - Recursive worker model
- **[04-SimpleBroker_Integration.md](04-SimpleBroker_Integration.md)** - Queue system integration
- **[05-Message_Flow_and_State.md](05-Message_Flow_and_State.md)** - Communication patterns
- **[06-Resource_Management.md](06-Resource_Management.md)** - Resource controls and error handling
- **[08-Testing_Strategy.md](08-Testing_Strategy.md)** - Testing approach and standards

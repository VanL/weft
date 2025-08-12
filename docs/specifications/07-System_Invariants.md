# System Invariants and Constraints

This document defines the fundamental invariants, constraints, and guarantees that the Weft system maintains across all operations.

## System Invariants

### 1. Immutability Invariants
- **I1**: TaskSpec.spec section is immutable after creation
- **I2**: TaskSpec.io section is immutable after creation
- **I3**: TaskSpec.tid is immutable and unique
- **I4**: TaskSpec.state and metadata remain mutable for runtime updates

### 2. State Machine Invariants
- **I5**: States transition forward only (no rollback)
- **I6**: Terminal states are immutable (completed, failed, timeout, cancelled, killed)
- **I7**: Running state requires started_at timestamp
- **I8**: Terminal states require completed_at timestamp
- **I9**: completed_at > started_at when both present
- **I10**: State changes are reported to weft.tasks.log

### 3. Queue Invariants
- **I11**: Every task has exactly one inbox, reserved, and outbox queue
- **I12**: Every task has exactly one ctrl_in and ctrl_out queue
- **I13**: Queue names are unique per task (prefixed with "T{tid}.")
- **I14**: Messages are delivered exactly once (via claim/move semantics)
- **I15**: Reserved queue contains at most one message per inbox message
- **I16**: Failed messages remain in reserved queue with state.error set

### 4. Resource Invariants
- **I17**: Limits are grouped in spec.limits subsection
- **I18**: Memory limit > 0 MB when specified
- **I19**: 0 <= CPU limit <= 100% when specified
- **I20**: File descriptor limit >= 1 when specified
- **I21**: Current metrics <= maximum metrics tracked
- **I22**: Resource violations trigger task termination

### 5. Execution Invariants
- **I23**: Task executes target exactly once per run
- **I24**: Timeouts are enforced within 1 second precision
- **I25**: PID is set only when process/thread starts
- **I26**: Return code is set only on process completion
- **I27**: Failed tasks leave messages in reserved for recovery

### 6. Observability Invariants
- **I28**: All state transitions logged to weft.tasks.log
- **I29**: No separate state database (queue-based state only)
- **I30**: State visible through both task queues and global log
- **I31**: Tasks set process title with shell-friendly format "weft-{tid_short}:{name}:{status}"
- **I32**: TID short form uses last 10 digits for uniqueness
- **I33**: Process title updates on every state transition
- **I34**: TID mappings saved to weft.tid.mappings queue
- **I35**: Process titles sanitized to remove shell special characters
- **I36**: Process titles use only alphanumeric, hyphen, colon, and underscore characters

### 7. Implementation Invariants
- **I37**: Exit code 124 indicates timeout (GNU coreutils standard)
- **I38**: Messages limited to 10MB by SimpleBroker
- **I39**: Outputs >10MB written to `/tmp/weft/outputs/{tid}/` with reference message
- **I40**: Large output reference includes path, size, and 1KB preview
- **I41**: Queue connections must be recreated in child processes (no sharing)
- **I42**: Use multiprocessing.get_context("spawn") for process creation

### 8. Worker Invariants
- **I43**: Workers are Tasks with long-running targets (timeout=None)
- **I44**: Worker TIDs follow same format as regular Task TIDs
- **I45**: Workers register capabilities in weft.workers.registry
- **I46**: Message timestamp becomes child Task TID for correlation
- **I47**: Workers can spawn other workers (recursive architecture)
- **I48**: Primordial worker bootstraps the system
- **I49**: Workers respond to same control messages as Tasks

### 9. Context Invariants
- **I50**: Each weft context corresponds to one SimpleBroker database
- **I51**: weft_context defaults to current working directory when null
- **I52**: Tasks cannot communicate across different contexts
- **I53**: Context cleanup removes all associated queues and data
- **I54**: .broker.db auto-created on first queue operation in context

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
        
        # I1, I2: spec and io immutability (checked by Pydantic)
        if not hasattr(taskspec, '_spec_frozen'):
            violations.append("I1/I2: TaskSpec.spec/io not marked as frozen")
        
        # I3: TID uniqueness and immutability
        if not taskspec.tid or len(taskspec.tid) != 19:
            violations.append("I3: Invalid TID format")
        
        # I17-I20: Resource limit validation
        if taskspec.spec.limits:
            limits = taskspec.spec.limits
            if limits.memory_mb is not None and limits.memory_mb <= 0:
                violations.append("I18: Memory limit must be > 0 MB")
            
            if (limits.cpu_percent is not None and 
                not 0 <= limits.cpu_percent <= 100):
                violations.append("I19: CPU limit must be 0-100%")
            
            if limits.max_fds is not None and limits.max_fds < 1:
                violations.append("I20: FD limit must be >= 1")
        
        return violations
    
    def check_state_invariants(self, taskspec: TaskSpec) -> list[str]:
        """Check state transition invariants."""
        violations = []
        state = taskspec.state
        
        # I7: Running state requires started_at
        if state.status == "running" and not state.started_at:
            violations.append("I7: Running state missing started_at")
        
        # I8: Terminal states require completed_at
        terminal_states = {"completed", "failed", "timeout", "cancelled", "killed"}
        if state.status in terminal_states and not state.completed_at:
            violations.append("I8: Terminal state missing completed_at")
        
        # I9: completed_at > started_at
        if (state.started_at and state.completed_at and 
            state.completed_at <= state.started_at):
            violations.append("I9: completed_at must be > started_at")
        
        return violations
    
    def check_queue_invariants(self, tid: str) -> list[str]:
        """Check queue-related invariants."""
        violations = []
        
        # I11, I12: Required queues exist
        required_queues = ["inbox", "reserved", "outbox", "ctrl_in", "ctrl_out"]
        for queue_type in required_queues:
            queue_name = f"T{tid}.{queue_type}"
            if not self._queue_exists(queue_name):
                violations.append(f"I11/I12: Missing required queue {queue_name}")
        
        # I15: Reserved queue has at most one message per inbox message
        reserved_count = self._count_messages(f"T{tid}.reserved")
        inbox_total = self._count_processed_messages(tid)  # From logs
        if reserved_count > inbox_total:
            violations.append("I15: More reserved than processed messages")
        
        return violations
    
    def check_process_title_invariants(self, tid: str, title: str) -> list[str]:
        """Check process title format invariants."""
        violations = []
        
        # I31: Shell-friendly format
        if not title.startswith("weft-"):
            violations.append("I31: Process title must start with 'weft-'")
        
        # I32: TID short form (last 10 digits)
        parts = title.split(":")
        if len(parts) < 2:
            violations.append("I31: Invalid process title format")
        else:
            tid_part = parts[0].split("-")[-1]  # Extract TID from weft-{tid}
            if len(tid_part) != 10 or not tid_part.isdigit():
                violations.append("I32: TID short form must be 10 digits")
            
            if tid_part != tid[-10:]:
                violations.append("I32: TID short form mismatch")
        
        # I35, I36: Character restrictions
        allowed_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-:_")
        if not set(title).issubset(allowed_chars):
            violations.append("I35/I36: Process title contains forbidden characters")
        
        return violations
    
    def _queue_exists(self, queue_name: str) -> bool:
        """Check if queue exists in context."""
        try:
            queue = self.context.get_queue(queue_name)
            # Try to access queue (will create if doesn't exist)
            queue.has_pending()
            return True
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
            log_queue = self.context.get_queue("weft.tasks.log")
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
        # I5: Forward-only transitions
        valid_transitions = {
            "created": {"spawning", "failed", "cancelled"},
            "spawning": {"running", "failed", "timeout", "cancelled", "killed"},
            "running": {"completed", "failed", "timeout", "cancelled", "killed"},
        }
        
        if old_status in valid_transitions:
            if new_status not in valid_transitions[old_status]:
                raise InvariantViolationError(
                    f"I5: Invalid transition {old_status} -> {new_status}"
                )
        
        # I6: Terminal states are immutable
        terminal_states = {"completed", "failed", "timeout", "cancelled", "killed"}
        if old_status in terminal_states and old_status != new_status:
            raise InvariantViolationError(
                f"I6: Cannot transition from terminal state {old_status}"
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
        # I21: Current <= maximum
        if hasattr(metrics, 'max_memory_mb'):
            if metrics.memory_mb > metrics.max_memory_mb:
                raise InvariantViolationError(
                    "I21: Current memory exceeds tracked maximum"
                )
        
        # I22: Resource violations trigger termination
        violations = metrics.exceeds_limits(limits)
        if violations:
            # This should trigger task termination
            raise ResourceLimitExceededError(
                f"I22: Resource limits exceeded: {violations}"
            )
```

### Testing Invariants

```python
class TestInvariants:
    """Test suite for invariant checking and enforcement."""
    
    def test_taskspec_immutability(self):
        """Test I1, I2: TaskSpec.spec/io immutability."""
        taskspec = create_test_taskspec()
        
        # Should not be able to modify spec after creation
        with pytest.raises(AttributeError):
            taskspec.spec.timeout = 999
        
        with pytest.raises(AttributeError):
            taskspec.io.inputs["new_queue"] = "test"
    
    def test_state_transitions(self):
        """Test I5, I6: State transition rules."""
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
        """Test I11, I12: Required queue structure."""
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
        """Test I31, I32, I35, I36: Process title format."""
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
        """Test I17-I22: Resource limit invariants."""
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
        """Test I43-I49: Worker-specific invariants."""
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
    """Raised when resource limits are exceeded (I22)."""
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
        # Reconstruct from weft.tasks.log
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
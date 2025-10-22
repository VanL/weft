# Message Flow Patterns and State Management

This document describes how messages flow through the Weft system and how state is managed through queue-based communication.

_Implementation snapshot_: Reservation and control flows ([MF-2], [MF-3]) are
implemented within `BaseTask`/`Consumer` (`weft/core/tasks/base.py`,
`weft/core/tasks/consumer.py`). Worker spawning and bootstrap flows ([MF-6],
[MF-7]) are handled by the Manager (`weft/core/manager.py`) and the CLI helpers
in `weft/commands/run.py`.

## Message Flow Patterns [MF-0]

### 1. Task Submission Flow [MF-1]
_Implementation status_: The Client/Queue submission pipeline described here is pending; current CLI commands operate on pre-created TaskSpec files.
```
User -> Client -> TaskSpec -> Queue(T{tid}.inbox) -> Task
                           |
                           └-> Queue(weft.tasks.log) [initial state]
```

### 2. Message Processing Flow with Reservation [MF-2]
_Implementation_: `Consumer` (`weft/core/tasks/consumer.py`) moves inbox messages to reserved queues, executes work, and applies reserved policies per this flow.
```
T{tid}.inbox -> move -> T{tid}.reserved -> process -> T{tid}.outbox
                              |                          |
                              └─[on failure]             └─[on success]
                                stays in reserved          delete from reserved
                                update state.error         update state.status
                                      |                           |
                                      └───────────┬───────────────┘
                                                  v
                                          Queue(weft.tasks.log)
```

### 3. Control Flow [MF-3]
_Implementation_: Control queues are monitored by `BaseTask._handle_control_message`, emitting responses on `ctrl_out`.
```
Controller -> Queue(T{tid}.ctrl_in) -> Task -> Queue(T{tid}.ctrl_out)
                                         |
                                         └-> Queue(weft.tasks.log)
```

### 4. Pipeline Flow [MF-4]
_Implementation status_: Automatic pipeline chaining is not yet implemented; downstream routing must be orchestrated manually.
```
T{tid1}.outbox -> T{tid2}.inbox -> T{tid2}.reserved -> process
                                           |
                                           └-> T{tid2}.outbox -> T{tid3}.inbox
```

### 5. State Observation Flow [MF-5]
_Implementation_: State change events are written to `weft.tasks.log` by `BaseTask._report_state_change`. Observers can attach via `Monitor`/`SamplingObserver`.
```
Task1 ─┐
Task2 ──┼─> weft.tasks.log -> Observer/Monitor -> Aggregated View
Task3 ─┘                           |
                                   └-> Summary/Alert/Report
```

### 6. Worker Spawn Flow [MF-6]
_Implementation_: Managers (`weft/core/manager.py`) consume spawn requests from
`weft.spawn.requests`, validate/expand the embedded TaskSpec, launch child
Consumers via `launch_task_process`, seed initial inbox messages when provided,
and emit lifecycle events to `weft.tasks.log`.
```
Client -> weft.spawn.requests -> Worker.inbox -> Worker validates
                                      |              |
                                      |              └─> Create TaskSpec
                                      |                      |
                                      |                      └─> Spawn child Task
                                      |                             |
                                      └─> Use msg._timestamp as child TID
                                                |
                                                └─> Correlation throughout lifecycle
```

### 7. Worker Bootstrap Flow [MF-7]
_Implementation_: `weft run` and `weft worker start` use the helper in
`weft/commands/run.py` to mint a manager TaskSpec, launch
`weft.manager_process`, and wait for the manager to register itself before
returning control to the user.
```
weft CLI -> build manager TaskSpec -> spawn weft.manager_process
      |                  |                     |
      |                  |                     ├─> Manager registers in weft.workers.registry
      |                  |                     ├─> Begins watching weft.spawn.requests
      |                  |                     └─> Awaits control messages on ctrl queues
      |                  └─> Includes idle_timeout & queue bindings
      └─> Wait for registry entry, then exit (manager keeps running)
```

### 8. Failure Recovery Flow
_Implementation_: Reserved queue policies (`KEEP`, `REQUEUE`, `CLEAR`) are enforced by `BaseTask._apply_reserved_policy`.
```
Failed Task (messages in T{tid}.reserved + state.error=true)
     |
     ├─> Option 1: Retry in place
     │   reserved -> process again -> outbox
     │
     ├─> Option 2: Move back to inbox
     │   reserved -> move -> inbox (for new attempt)
     │
     └─> Option 3: Manual intervention
         reserved -> inspect -> debug -> manual resolution

Reserved queue policies (`reserved_policy_on_stop` and
`reserved_policy_on_error`) determine whether the task keeps, requeues, or
clears these messages automatically.
```

## State Machine
_Implementation_: `TaskSpec.state` (`weft/core/taskspec.py`) tracks the lifecycle states shown here. Transitions are updated via helper methods (`mark_*`).

### States and Transitions

```
        ┌─────────┐
        │ created │
        └────┬────┘
             │
        ┌────▼────┐
        │spawning │
        └────┬────┘
             │
        ┌────▼────┐
        │ running │
        └────┬────┘
             │
    ┌────────┼────────┬───────┬──────────┐
    │        │        │       │          │
┌───▼──┐ ┌──▼───┐ ┌──▼──┐ ┌─▼──────┐ ┌─▼────┐
│failed│ │timeout│ │killed│ │cancelled│ │completed│
└──────┘ └──────┘ └─────┘ └────────┘ └──────┘
```

### State Descriptions

| State | Description | Entry Conditions | Exit Conditions |
|-------|-------------|------------------|-----------------|
| created | Initial state | TaskSpec created | Execution starts |
| spawning | Starting execution | run() called | Process/thread created |
| running | Actively executing | Target started | Target completes/fails |
| completed | Successful completion | Return code 0 | Terminal state |
| failed | Execution error | Exception/non-zero code | Terminal state |
| timeout | Time limit exceeded | Timeout elapsed | Terminal state |
| cancelled | User cancellation | CANCEL control message | Terminal state |
| killed | Force terminated | KILL signal/memory limit | Terminal state |

### Transition Rules

```python
VALID_TRANSITIONS = {
    "created": {"spawning", "failed", "cancelled"},
    "spawning": {"running", "completed", "failed", "timeout", "cancelled", "killed"},
    "running": {"completed", "failed", "timeout", "cancelled", "killed"},
    # Terminal states have no valid transitions
}
```

### State Persistence

**Queue-Based State Storage**:
- **No separate database**: State lives in `weft.tasks.log` queue
- **Event sourcing**: State reconstructed from log events
- **Global visibility**: All state changes flow through single queue
- **Durability**: State persists across task/system restarts
- **Full snapshots**: Each state-change event includes a `taskspec` field with the
  complete, JSON-friendly dump of the originating TaskSpec so a consumer can
  reconstruct the runtime state without querying the task directly.

```python
class StateTracker:
    """Track and manage task state through queue messages."""
    
    def __init__(self, context: WeftContext):
        self.context = context
        self.log_queue = context.get_queue("weft.tasks.log")
    
    def update_state(self, tid: str, state_update: dict) -> None:
        """Update task state by writing to log queue."""
        timestamp = self.log_queue.generate_timestamp()
        log_entry = {
            "tid": tid,
            "timestamp": timestamp,
            "event": "state_update",
            **state_update
        }
        self.log_queue.write(json.dumps(log_entry))
    
    def get_current_state(self, tid: str) -> dict:
        """Reconstruct current state from log events."""
        state = {"status": "created"}  # Default initial state
        
        # Replay all events for this TID
        for msg in self.log_queue.peek_all():
            entry = json.loads(msg)
            if entry.get("tid") == tid:
                state.update(entry)
        
        return state
    
    def get_all_states(self) -> dict[str, dict]:
        """Get current state of all tasks."""
        states = {}
        
        for msg in self.log_queue.peek_all():
            entry = json.loads(msg)
            tid = entry.get("tid")
            if tid:
                if tid not in states:
                    states[tid] = {"status": "created"}
                states[tid].update(entry)
        
        return states
```

## Future Considerations

- Add a redaction layer for the `taskspec` payload written to `weft.tasks.log` so
  secrets embedded in environment variables or metadata can be removed before
  persistence.

## Large Output Handling

### Strategy for Outputs Exceeding 10MB Limit

When task output exceeds SimpleBroker's 10MB message limit, the system automatically spills to disk:

### 1. Output Spilling API

```python
class LargeOutputHandler:
    """Handle outputs that exceed SimpleBroker's message size limit."""
    
    OUTPUT_DIR = "/tmp/weft/outputs"
    MAX_MESSAGE_SIZE = 10 * 1024 * 1024  # 10MB
    PREVIEW_SIZE = 1024  # 1KB preview
    
    def handle_output(self, data: bytes, tid: str, stream_output: bool) -> None:
        """Process output, spilling to disk if necessary."""
        if len(data) <= self.MAX_MESSAGE_SIZE:
            # Normal path: write directly to queue
            Queue(f"T{tid}.outbox").write(data)
        else:
            # Large output: spill to disk
            self._handle_large_output(data, tid)
    
    def _handle_large_output(self, data: bytes, tid: str) -> None:
        """Write large output to disk and send reference message."""
        # Create output directory
        output_path = Path(f"{self.OUTPUT_DIR}/{tid}/output.dat")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write full output to disk
        with open(output_path, 'wb') as f:
            f.write(data)
        
        # Send reference message to queue
        reference = {
            "type": "large_output",
            "path": str(output_path),
            "size": len(data),
            "size_mb": round(len(data) / (1024 * 1024), 2),
            "truncated_preview": data[:self.PREVIEW_SIZE].decode('utf-8', errors='replace'),
            "sha256": hashlib.sha256(data).hexdigest(),
            "message": f"Output too large ({len(data)} bytes), saved to {output_path}"
        }
        Queue(f"T{tid}.outbox").write(json.dumps(reference))
        
        # Log the spill event
        log_queue = Queue("weft.tasks.log")
        log_queue.write(json.dumps({
            "event": "output_spilled",
            "tid": tid,
            "size": len(data),
            "path": str(output_path),
            "timestamp": log_queue.generate_timestamp()
        }))
```

### 2. Consumer API for Large Output

```python
class TaskOutputReader:
    """Read task output, handling both inline and spilled outputs."""
    
    def read_output(self, tid: str) -> bytes | dict:
        """Read output from task, automatically handling large outputs."""
        msg = Queue(f"T{tid}.outbox").read_one()
        
        # Check if it's a reference to large output
        try:
            data = json.loads(msg)
            if isinstance(data, dict) and data.get("type") == "large_output":
                return self._read_large_output(data)
        except (json.JSONDecodeError, TypeError):
            # Normal output (not JSON or not a reference)
            pass
        
        return msg
    
    def _read_large_output(self, reference: dict) -> bytes:
        """Read large output from disk."""
        path = reference["path"]
        
        # Verify checksum for integrity
        with open(path, 'rb') as f:
            data = f.read()
        
        if hashlib.sha256(data).hexdigest() != reference["sha256"]:
            raise ValueError(f"Checksum mismatch for {path}")
        
        return data
    
    def stream_large_output(self, tid: str, chunk_size: int = 1024*1024) -> Iterator[bytes]:
        """Stream large output in chunks."""
        msg = Queue(f"T{tid}.outbox").peek_one()
        
        try:
            reference = json.loads(msg)
            if reference.get("type") == "large_output":
                with open(reference["path"], 'rb') as f:
                    while chunk := f.read(chunk_size):
                        yield chunk
        except (json.JSONDecodeError, TypeError):
            # Small output - yield as single chunk
            yield msg
```

_Implementation note_: When `spec.stream_output` is `True`, `Consumer` emits JSON envelopes on `outbox` with the shape `{"type": "stream", "chunk": N, "final": bool, "encoding": "base64", "size": bytes, "data": "..."}`. Interactive sessions add a `stream` field (`stdout`/`stderr`) and use `encoding: "text"` when forwarding live output. Consumers can decode and reassemble the payload in order.

### 3. Cleanup Strategy

```python
class OutputCleaner:
    """Clean up spilled output files based on retention policy."""
    
    def cleanup_task_output(self, tid: str, cleanup_on_exit: bool) -> None:
        """Clean up output files for a task."""
        if not cleanup_on_exit:
            return
        
        output_dir = Path(f"/tmp/weft/outputs/{tid}")
        if output_dir.exists():
            shutil.rmtree(output_dir)
            logger.info(f"Cleaned up output directory for task {tid}")
    
    def cleanup_old_outputs(self, max_age_seconds: int = 86400) -> int:
        """Clean up outputs older than max_age."""
        base_dir = Path("/tmp/weft/outputs")
        cleaned = 0
        
        for tid_dir in base_dir.iterdir():
            if tid_dir.is_dir():
                # Check modification time
                mtime = tid_dir.stat().st_mtime
                if time.time() - mtime > max_age_seconds:
                    shutil.rmtree(tid_dir)
                    cleaned += 1
        
        return cleaned
```

## Queue Management Patterns

### The Unified Reservation Pattern

The system uses a **single reservation pattern** where the `.reserved` queue serves as both work-in-progress and dead-letter queue:

```python
class ReservationManager:
    """Manage the unified reservation pattern for reliable message processing."""
    
    def __init__(self, tid: str, context: WeftContext):
        self.tid = tid
        self.context = context
        self.inbox = context.get_queue(f"T{tid}.inbox")
        self.reserved = context.get_queue(f"T{tid}.reserved")
        self.outbox = context.get_queue(f"T{tid}.outbox")
    
    def process_with_reservation(self, processor: Callable) -> bool:
        """Process message using reservation pattern."""
        # 1. Atomic move from inbox to reserved
        msg = self.inbox.move_one(self.reserved.name)
        if not msg:
            return False  # No messages available
        
        try:
            # 2. Process message
            result = processor(msg)
            
            # 3. Success: write result and clear reservation
            self.outbox.write(result)
            self.reserved.delete(msg.timestamp)
            return True
            
        except Exception as e:
            # 4. Failure: leave in reserved, log error
            self._log_failure(msg, str(e))
            return False
    
    def _log_failure(self, msg: dict, error: str) -> None:
        """Log failure to global task log."""
        log_queue = self.context.get_queue("weft.tasks.log")
        log_entry = {
            "tid": self.tid,
            "event": "message_failed",
            "message_id": msg.get("timestamp"),
            "error": error,
            "timestamp": log_queue.generate_timestamp()
        }
        log_queue.write(json.dumps(log_entry))
    
    def recover_reserved(self) -> list[dict]:
        """Check for messages left in reserved queue (previous failures)."""
        return [json.loads(msg) for msg in self.reserved.peek_all()]
    
    def retry_reserved(self, processor: Callable) -> int:
        """Retry processing messages in reserved queue."""
        retried = 0
        
        for msg_str in self.reserved.peek_all():
            msg = json.loads(msg_str)
            try:
                result = processor(msg)
                self.outbox.write(result)
                self.reserved.delete(msg["timestamp"])
                retried += 1
            except Exception as e:
                self._log_failure(msg, f"Retry failed: {e}")
        
        return retried
```

### Queue Lifecycle Management

```python
class QueueLifecycleManager:
    """Manage queue creation, cleanup, and monitoring."""
    
    def __init__(self, context: WeftContext):
        self.context = context
    
    def create_task_queues(self, tid: str) -> dict[str, Queue]:
        """Create all required queues for a task."""
        queues = {}
        
        for queue_type in ["inbox", "reserved", "outbox", "ctrl_in", "ctrl_out"]:
            queue_name = f"T{tid}.{queue_type}"
            queues[queue_type] = self.context.get_queue(queue_name)
        
        return queues
    
    def cleanup_task_queues(self, tid: str, force: bool = False) -> None:
        """Clean up queues for completed task."""
        for queue_type in ["inbox", "reserved", "outbox", "ctrl_in", "ctrl_out"]:
            queue_name = f"T{tid}.{queue_type}"
            queue = self.context.get_queue(queue_name)
            
            if force or not queue.has_pending():
                # SimpleBroker auto-deletes empty queues
                # Force deletion by removing all messages
                while queue.read():
                    pass
    
    def get_queue_status(self) -> dict[str, dict]:
        """Get status of all queues in context."""
        from simplebroker import list_queues
        
        status = {}
        db_path = str(self.context.db_path)
        
        for queue_name in list_queues(db_path):
            queue = self.context.get_queue(queue_name)
            status[queue_name] = {
                "message_count": len(list(queue.peek_all())),
                "has_pending": queue.has_pending(),
                "last_activity": self._get_last_activity(queue)
            }
        
        return status
    
    def _get_last_activity(self, queue: Queue) -> float | None:
        """Get timestamp of last activity in queue."""
        messages = list(queue.peek_all())
        if not messages:
            return None
        
        # Find latest timestamp
        latest = 0
        for msg_str in messages:
            try:
                msg = json.loads(msg_str)
                if "timestamp" in msg:
                    latest = max(latest, msg["timestamp"])
            except (json.JSONDecodeError, KeyError):
                continue
        
        return latest / 1_000_000_000 if latest else None  # Convert to seconds
```

## Related Documents

- **[00-Overview_and_Architecture.md](00-Overview_and_Architecture.md)** - System overview and design principles
- **[01-TaskSpec.md](01-TaskSpec.md)** - Task configuration specification
- **[02-Core_Components.md](02-Core_Components.md)** - Detailed component architecture
- **[03-Worker_Architecture.md](03-Worker_Architecture.md)** - Recursive worker model
- **[04-SimpleBroker_Integration.md](04-SimpleBroker_Integration.md)** - Queue system integration
- **[06-Resource_Management.md](06-Resource_Management.md)** - Resource controls and error handling
- **[07-System_Invariants.md](07-System_Invariants.md)** - System guarantees and constraints


## Inline CLI Task Initialization

- `weft run` calls `Queue.generate_timestamp()` to allocate Task IDs for inline runs. The initial work payload is written directly to `T{tid}.inbox` using this timestamp.

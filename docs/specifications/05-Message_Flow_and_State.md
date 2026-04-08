# Message Flow Patterns and State Management

This document describes how messages flow through the Weft system and how state is managed through queue-based communication.

_Implementation snapshot_: Reservation and control flows ([MF-2], [MF-3]) are
implemented within `BaseTask`/`Consumer` (`weft/core/tasks/base.py`,
`weft/core/tasks/consumer.py`). Worker spawning and bootstrap flows ([MF-6],
[MF-7]) are handled by the Manager (`weft/core/manager.py`) and the CLI helpers
in `weft/commands/run.py`. State observation ([MF-5]) is provided by
`weft/commands/status.py` (log replay and snapshot collection). Pipeline
chaining ([MF-4]) is not yet implemented. Several design-reference classes
(`StateTracker`, `TaskOutputReader`, `OutputCleaner`, `ReservationManager`,
`QueueLifecycleManager`) are spec-only and not implemented as standalone
classes; their behavior is either spread across existing modules or not yet
built.

Queue names and control message constants are summarized in
[00-Quick_Reference.md](00-Quick_Reference.md).

## Message Flow Patterns [MF-0]

### 1. Task Submission Flow [MF-1]
_Implementation_: `weft run` enqueues a spawn request on `weft.spawn.requests`; the Manager expands the TaskSpec template, assigns the spawn-request message ID as the task TID, seeds `T{tid}.inbox` with the initial payload when provided (for example CLI `--input` or piped stdin), and reports the initial state to `weft.log.tasks`.
```
User -> CLI -> Queue(weft.spawn.requests) -> Manager -> Queue(T{tid}.inbox)
                                             |
                                             └-> Queue(weft.log.tasks) [initial state]
```

_Implementation mapping_: `weft/commands/run.py` (`_enqueue_taskspec` writes spawn request with forced timestamp = TID), `weft/core/manager.py` (`Manager._handle_work_message` receives spawn request, `Manager._build_child_spec` expands TaskSpec, `Manager._launch_child_task` seeds `T{tid}.inbox` and emits `task_spawned` to `weft.log.tasks`).

### 2. Message Processing Flow with Reservation [MF-2]
_Implementation_: `Consumer` (`weft/core/tasks/consumer.py`) moves inbox
messages to reserved queues, executes work, and applies reserved policies per
this flow. The current target types are `function`, `command`, and `agent`;
agent work still follows the same inbox/reserved/outbox/control lifecycle as
other tasks.
```
T{tid}.inbox -> move -> T{tid}.reserved -> process -> T{tid}.outbox
                              |                          |
                              └─[on failure]             └─[on success]
                                stays in reserved          delete from reserved
                                update state.error         update state.status
                                      |                           |
                                      └───────────┬───────────────┘
                                                  v
                                        Queue(weft.log.tasks)
```

**Reservation semantics**
- **Reserve = move**: Work is reserved by moving from `T{tid}.inbox` to `T{tid}.reserved` (message ID preserved).
- **Success = delete reserved**: On success, the reserved message is deleted by ID.
- **Failure/STOP = policy**: On error or STOP, apply `reserved_policy_on_error` / `reserved_policy_on_stop` (`keep`, `requeue`, `clear`).
- **Crash = keep**: If the task crashes mid-work, the message remains in `T{tid}.reserved` for manual recovery or explicit requeue.

**Agent output note**
- Agent tasks write caller-facing outbox payloads as plain strings or JSON values.
- A single agent work item may emit multiple outbox messages.
- For persistent tasks, `work_item_completed` in `weft.log.tasks` is the public
  boundary for one completed inbox message while the task itself remains
  running.

_Implementation mapping_: `weft/core/tasks/consumer.py` (`Consumer._build_queue_configs` sets up reserve mode on inbox, `Consumer._handle_work_message` processes reserved items, `Consumer._finalize_message` deletes from reserved on success), `weft/core/tasks/base.py` (`BaseTask._apply_reserved_policy` enforces keep/requeue/clear, `BaseTask._move_reserved_to_inbox` handles requeue, `BaseTask._cleanup_reserved_if_needed` handles cleanup_on_exit).

**Idempotency guidance**
- **Single-message tasks** may use `tid` as an idempotency key.
- **Multi-message tasks** should use the inbox/reserved `message_id` (timestamp) as the idempotency key.
- **Recommended key**: `tid:message_id` to avoid cross-task collisions.

### 3. Control Flow [MF-3]
_Implementation_: Control queues are consumed by `BaseTask._handle_control_message`, emitting responses on `ctrl_out`.
```
Controller -> Queue(T{tid}.ctrl_in) -> Task -> Queue(T{tid}.ctrl_out)
                                         |
                                         └-> Queue(weft.log.tasks)
```

_Implementation mapping_: `weft/core/tasks/base.py` (`BaseTask._handle_control_message` dispatches, `BaseTask._handle_control_command` handles PING/STOP/KILL/PAUSE/RESUME/STATUS, `BaseTask._send_control_response` writes to `ctrl_out`, `BaseTask._ack_control_message` deletes consumed control messages). `weft/core/tasks/consumer.py` (`Consumer._poll_active_control_once` polls `ctrl_in` from the main task thread during active work, `Consumer._defer_active_control` records active STOP/KILL intent, `Consumer._finalize_deferred_active_control` publishes the terminal state after runtime exit). `weft/commands/tasks.py` (`_send_control` writes to `ctrl_in`, `stop_tasks`/`kill_tasks` wait for durable terminal state before runner/PID fallback).

### 4. Pipeline Flow [MF-4]
**[NOT YET IMPLEMENTED]** Automatic pipeline chaining is not yet implemented; downstream routing must be orchestrated manually.
```
T{tid1}.outbox -> T{tid2}.inbox -> T{tid2}.reserved -> process
                                           |
                                           └-> T{tid2}.outbox -> T{tid3}.inbox
```

### 5. State Observation Flow [MF-5]
_Implementation_: State change events are written to `weft.log.tasks` by `BaseTask._report_state_change`. Observers can attach via `Monitor`/`SamplingObserver`.
```
Task1 ─┐
Task2 ──┼─> weft.log.tasks -> Observer/Monitor -> Aggregated View
Task3 ─┘                           |
                                   └-> Summary/Alert/Report
```

_Implementation mapping_: `weft/core/tasks/base.py` (`BaseTask._report_state_change` writes JSON events to `weft.log.tasks` with full redacted TaskSpec snapshot). `weft/core/tasks/consumer.py` (`Observer`, `SamplingObserver`, `Monitor` peek at queues without consuming). `weft/commands/status.py` (`_iter_log_events` replays `weft.log.tasks`, `_collect_task_snapshots` reconstructs current state, `_watch_task_events` provides live tail). `weft/commands/tasks.py` (`list_tasks`, `task_status`) delegate to `_collect_task_snapshots`.

### 6. Worker Spawn Flow [MF-6]
_Implementation_: Managers (`weft/core/manager.py`) consume spawn requests from
`weft.spawn.requests`, validate/expand the embedded TaskSpec, launch child
Consumers via `launch_task_process`, seed initial inbox messages when provided,
and emit lifecycle events to `weft.log.tasks`.

When `WEFT_AUTOSTART_TASKS` is enabled the Manager also synthesizes spawn
requests locally by loading autostart manifests in `.weft/autostart/` that
reference stored task specs or pipelines, then writing spawn requests that follow
the same validation/launch pipeline. Manifest lifecycle policy (`once`/`ensure`)
controls whether tasks are restarted while the manager is running. Tasks launched
by the manager are stopped cleanly when the manager exits.
```
Client -> weft.spawn.requests -> Worker.inbox -> Worker validates
                                      |              |
                                      |              └─> Create TaskSpec
                                      |                      |
                                      |                      └─> Spawn child Task
                                      |                             |
                                      └─> Use spawn-request message ID as child TID
                                                |
                                                └─> Correlation throughout lifecycle
```

_Implementation mapping_: `weft/core/manager.py` (`Manager._handle_work_message` consumes spawn requests, `Manager._build_child_spec` validates/expands TaskSpec with `resolve_taskspec_payload`, `Manager._launch_child_task` calls `launch_task_process` and seeds inbox, `Manager._tick_autostart` scans `.weft/autostart/` manifests, `Manager._build_autostart_spawn_payload` loads stored task specs, `Manager._active_autostart_sources` queries `weft.log.tasks` for running autostart tasks). `weft/commands/run.py` (`_enqueue_taskspec` writes spawn requests with forced TID timestamps). **[NOT YET IMPLEMENTED]**: Autostart pipeline targets (`target.type == "pipeline"`) are logged as unsupported.

### 7. Worker Bootstrap Flow [MF-7]
_Implementation_: `weft run` and `weft worker start` use the helper in
`weft/commands/run.py` to mint a manager TaskSpec, launch
`weft.manager_process`, and wait for the manager to register itself before
returning control to the user.
```
weft CLI -> build manager TaskSpec -> spawn weft.manager_process
      |                  |                     |
      |                  |                     ├─> Manager registers in weft.state.workers
      |                  |                     ├─> Begins watching weft.spawn.requests
      |                  |                     └─> Awaits control messages on ctrl queues
      |                  └─> Includes idle_timeout & queue bindings
      └─> Wait for registry entry, then exit (manager keeps running)
```

_Implementation mapping_: `weft/commands/run.py` (builds manager TaskSpec and launches via `weft/core/launcher.py`). `weft/core/manager.py` (`Manager.__init__` calls `_register_worker` to write to `weft.state.workers`, sets up `weft.spawn.requests` watcher, and runs autostart on first tick; `Manager._maybe_yield_leadership` handles leader election among concurrent managers).

### 8. Failure Recovery Flow
_Implementation_: Reserved queue policies (`keep`, `requeue`, `clear`) are enforced by `BaseTask._apply_reserved_policy`.
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

**Policy matrix**

| Event | Policy key | keep | requeue | clear |
|------|------------|------|---------|-------|
| STOP | reserved_policy_on_stop | leave in reserved | move back to inbox | delete from reserved |
| Error/Exception | reserved_policy_on_error | leave in reserved | move back to inbox | delete from reserved |
| Crash | (implicit) | leave in reserved | n/a | n/a |
```

_Implementation mapping_: `weft/core/tasks/base.py` (`BaseTask._apply_reserved_policy` implements KEEP/REQUEUE/CLEAR, `BaseTask._handle_stop_request` applies `reserved_policy_on_stop`, `BaseTask._handle_kill_request` applies `reserved_policy_on_error`). `weft/core/tasks/consumer.py` (`Consumer._apply_reserved_policy_on_error` applies on timeout/limit/error outcomes, `Consumer._handle_external_stop`/`_handle_external_kill` handle signal-driven policy application). **[NOT YET IMPLEMENTED]**: Retry-in-place (Option 1) and manual re-processing APIs are not provided as built-in commands; recovery requires manual queue manipulation via `weft queue move`.

## State Machine
_Implementation_: `TaskSpec.state` (`weft/core/taskspec.py`) tracks the lifecycle states shown here. Transitions are updated via helper methods (`mark_started`, `mark_running`, `mark_completed`, `mark_failed`, `mark_timeout`, `mark_cancelled`, `mark_killed`).

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
| cancelled | User cancellation | STOP control message | Terminal state |
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

**Fast-path completion**: `spawning -> completed` is permitted for tasks that
finish within the first scheduling tick (e.g., very short commands). Long-running
tasks are expected to emit `running` before a terminal state.

_Implementation mapping_: `weft/core/taskspec.py` (`TaskSpec.mark_*` methods enforce forward-only transitions). **Note**: The `VALID_TRANSITIONS` dict shown above is a specification reference; the actual enforcement is distributed across the `mark_*` methods rather than a centralized lookup table.

### State Persistence

**Queue-Based State Storage**:
- **No separate database**: State lives in `weft.log.tasks` queue
- **Event sourcing**: State reconstructed from log events
- **Global visibility**: All state changes flow through single queue
- **Durability**: State persists across task/system restarts
- **Full snapshots**: Each state-change event includes a `taskspec` field with the
  complete, JSON-friendly dump of the originating TaskSpec so a consumer can
  reconstruct the runtime state without querying the task directly.

**[NOT YET IMPLEMENTED]** The `StateTracker` class shown below is a design reference; it is not implemented as a standalone class. The equivalent behavior is spread across:
- `BaseTask._report_state_change` (writes state events to `weft.log.tasks`).
- `weft/commands/status.py` (`_iter_log_events` + `_collect_task_snapshots`) replays the log to reconstruct current state per TID.

```python
class StateTracker:
    """Track and manage task state through queue messages."""

    def __init__(self, context: WeftContext):
        self.context = context
        self.log_queue = context.get_queue("weft.log.tasks")

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

## TaskSpec Redaction

_Implementation_: `weft/helpers.py` (`redact_taskspec_dump`, line ~635) is applied by
`BaseTask._report_state_change` and `Manager._report_state_change` (which inherits from BaseTask) to remove
secret fields from the `taskspec` payloads written to `weft.log.tasks`. Redaction paths are configured via `WEFT_REDACT_TASKSPEC_FIELDS` in `BaseTask.__init__`.

## Large Output Handling

### Strategy for Outputs Exceeding 10MB Limit

When task output exceeds SimpleBroker's 10MB message limit, the system automatically spills to disk:

### 1. Output Spilling API

_Implementation mapping_: Output spilling is implemented inline in `weft/core/tasks/base.py` (`BaseTask._spill_large_output`) and `weft/core/tasks/consumer.py` (`Consumer._emit_single_output` checks size limits and delegates to `_spill_large_output`). The `LargeOutputHandler` class below is a design reference, not a standalone class.

```python
class LargeOutputHandler:
    """Handle outputs that exceed SimpleBroker's message size limit."""
    
    OUTPUT_DIR = ".weft/outputs"
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
        log_queue = Queue("weft.log.tasks")
        log_queue.write(json.dumps({
            "event": "output_spilled",
            "tid": tid,
            "size": len(data),
            "path": str(output_path),
            "timestamp": log_queue.generate_timestamp()
        }))
```

### 2. Consumer API for Large Output

**[NOT YET IMPLEMENTED]** The `TaskOutputReader` class below is a design reference. There is no standalone consumer-side reader that transparently handles large output references. Consumers must manually inspect outbox messages for `"type": "large_output"` payloads. The `weft result` command (`weft/commands/result.py`) reads outbox messages but does not implement automatic large-output dereferencing.

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

_Implementation note_: When `spec.stream_output` is `True`, `Consumer` emits JSON envelopes on `outbox` with the shape `{"type": "stream", "chunk": N, "final": bool, "encoding": "base64", "size": bytes, "data": "..."}`. Interactive sessions add a `stream` field (`stdout`/`stderr`) and use `encoding: "text"` when forwarding live output. Interactive terminal completion is reported on `ctrl_out` with a task-local envelope shaped like `{"type": "terminal", "status": "...", "event": "...", ...}` so interactive clients do not need to watch `weft.log.tasks` for correctness. Consumers can decode and reassemble the payload in order.

### 3. Cleanup Strategy

_Implementation mapping_: Cleanup of spilled outputs is implemented in `weft/core/tasks/base.py` (`BaseTask._cleanup_spilled_outputs_if_needed`, gated on `cleanup_on_exit`). **[NOT YET IMPLEMENTED]**: The `OutputCleaner` class below and its `cleanup_old_outputs` method (time-based retention sweep) are design references; there is no built-in command or scheduled job to garbage-collect old output directories by age.

```python
class OutputCleaner:
    """Clean up spilled output files based on retention policy."""
    
    def cleanup_task_output(self, tid: str, cleanup_on_exit: bool) -> None:
        """Clean up output files for a task."""
        if not cleanup_on_exit:
            return
        
        output_dir = Path(f".weft/outputs/{tid}")
        if output_dir.exists():
            shutil.rmtree(output_dir)
            logger.info(f"Cleaned up output directory for task {tid}")
    
    def cleanup_old_outputs(self, max_age_seconds: int = 86400) -> int:
        """Clean up outputs older than max_age."""
        base_dir = Path(".weft/outputs")
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

The system uses a **single reservation pattern** where the `.reserved` queue serves as both work-in-progress and dead-letter queue.

_Implementation mapping_: The reservation pattern is implemented across `weft/core/tasks/base.py` (`BaseTask._reserve_queue_config`, `BaseTask._get_reserved_queue`, `BaseTask._apply_reserved_policy`, `BaseTask._move_reserved_to_inbox`, `BaseTask._ensure_reserved_empty`, `BaseTask._cleanup_reserved_if_needed`) and `weft/core/tasks/consumer.py` (`Consumer._build_queue_configs` sets up reserve mode, `Consumer._finalize_message` deletes from reserved on success). The `ReservationManager` class below is a design reference, not a standalone class. **[NOT YET IMPLEMENTED]**: `recover_reserved` and `retry_reserved` methods are not exposed as built-in APIs; recovery requires manual `weft queue` commands.

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
        log_queue = self.context.get_queue("weft.log.tasks")
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

**[NOT YET IMPLEMENTED]** The `QueueLifecycleManager` class below is a design reference. Queue creation is implicit (SimpleBroker creates queues on first write). Task queue cleanup is partially implemented via `BaseTask.cleanup()` (closes handles) and `weft system tidy` (removes empty queues), but the full lifecycle management API (explicit create, force cleanup of non-empty queues, queue status dashboard) described here does not exist as a standalone class.

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
            
            if queue_type == "outbox" and queue.has_pending():
                continue  # Preserve results unless consumed
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

- When running without a manager, the message ID returned by the initial inbox
  write is treated as the task TID.
- When using the manager (default), the spawn-request message ID is the TID and
  the Manager expands the TaskSpec before writing to `T{tid}.inbox`.

## Related Plans

- [`docs/plans/active-control-main-thread-plan.md`](../plans/active-control-main-thread-plan.md)
- [`docs/plans/piped-input-support-plan.md`](../plans/piped-input-support-plan.md)
- [`docs/plans/agent-runtime-implementation-plan.md`](../plans/agent-runtime-implementation-plan.md) (references MF-1, MF-2, MF-6)
- [`docs/plans/agent-runtime-boundary-cleanup-plan.md`](../plans/agent-runtime-boundary-cleanup-plan.md)
- [`docs/plans/persistent-agent-runtime-implementation-plan.md`](../plans/persistent-agent-runtime-implementation-plan.md)
- [`docs/plans/simplebroker-backend-generalization-plan.md`](../plans/simplebroker-backend-generalization-plan.md)
- [`docs/plans/taskspec-clean-design-plan.md`](../plans/taskspec-clean-design-plan.md)

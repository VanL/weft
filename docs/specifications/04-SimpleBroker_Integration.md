# SimpleBroker Integration

This document details how Weft leverages SimpleBroker's features, avoiding reimplementation while building on a solid foundation.

_Implementation overview_: Queue operations are surfaced through `weft/commands/queue.py`, and runtime queue consumption is handled by `weft/core/tasks/multiqueue_watcher.py` / `weft/core/tasks/base.py`.

## SimpleBroker Features Leveraged by Weft [SB-0]

This section documents SimpleBroker features that Weft uses directly, avoiding reimplementation:

### Queue Operations (via SimpleBroker Queue API) [SB-0.1]

_Implementation_: CLI helpers in `weft/commands/queue.py` call `Queue.write/read/peek/move`, mirroring the patterns shown here.

```python
from simplebroker import Queue, QueueWatcher

# All queue operations are provided by SimpleBroker
queue = Queue("T{tid}.inbox")  # Auto-creates queue and database on first use

# Core operations - NO reimplementation needed
queue.write(message)           # Write message, returns timestamp ID  
queue.read()                   # Read and remove one message
queue.read_many(count)         # Read multiple messages
queue.peek()                   # View without removing
queue.move("T{tid}.reserved")  # Atomic move between queues
queue.delete(message_id=ts)    # Delete specific message by timestamp

# Built-in features Weft uses
queue.has_pending(since=ts)    # Check for messages newer than timestamp
queue.stream_messages()        # Generator for continuous reading
```

### Message IDs and Timestamps [SB-0.2]

_Implementation_: `TaskSpec.tid` and TID utilities in `weft/helpers.py` rely on the SimpleBroker timestamp IDs.

SimpleBroker automatically assigns unique 64-bit timestamp IDs to every message:
- **Format**: High 52 bits (microseconds) + Low 12 bits (counter)
- **Weft uses these as Task IDs**: No separate ID generation needed
- **Natural ordering**: IDs sort chronologically
- **Extraction**: Use message `timestamp` field from JSON output

### Safe Patterns Built into SimpleBroker [SB-0.3]

_Implementation_: `BaseTask._apply_reserved_policy` and `weft/commands/queue.move_command` use `move_one/move_many` for reservation patterns. Peek/delete flows are exercised via queue helpers.

1. **Move for Reservation** (SimpleBroker provides atomically):
```python
# SimpleBroker's move is atomic - implements reservation pattern
Queue("T{tid}.inbox").move_one("T{tid}.reserved")
```

2. **Peek-and-Delete for Acknowledgment**:
```python
# SimpleBroker supports peek + delete by ID pattern
msg = queue.peek_one()
if process(msg):
    queue.delete(message_id=msg['timestamp'])
```

3. **JSON Output for Safety** (includes timestamps):
```python
# SimpleBroker's JSON mode handles special characters safely
result = queue.read(json_output=True)
# Returns: {"message": "content", "timestamp": 1837025672140161024}
```

### QueueWatcher for Monitoring [SB-0.4]

_Implementation_: `MultiQueueWatcher` (`weft/core/tasks/multiqueue_watcher.py`) wraps QueueWatcher primitives and exposes per-mode handlers.

SimpleBroker provides QueueWatcher - Weft uses this directly:

```python
from simplebroker import QueueWatcher

def handle_control_message(msg: str, timestamp: int):
    """Process control messages for a Task."""
    if msg == "STOP":
        self.stop_requested = True

# Use SimpleBroker's watcher for control queue monitoring
watcher = QueueWatcher(
    queue=Queue(f"T{self.tid}.ctrl_in"),
    handler=handle_control_message,
    peek=True  # Don't consume control messages
)

# Run in background thread (SimpleBroker feature)
thread = watcher.run_in_thread()
```

### Database Management

SimpleBroker handles all database concerns:
- **Auto-creation**: `.broker.db` created on first queue write
- **Connection pooling**: Handled internally
- **Concurrent access**: WAL mode for multiple readers/writers
- **Cleanup**: Empty queues auto-deleted
- **Vacuum**: Use `broker --vacuum` to reclaim space

### Performance Features

SimpleBroker optimizations Weft inherits:
- **Burst mode**: First 100 polls with zero delay
- **Smart backoff**: Gradual increase to 0.1s polling
- **Low overhead**: Uses SQLite's data_version for change detection
- **Batch operations**: `read_many()`, `move_many()` for efficiency

### MultiQueueWatcher Integration

**Design Evolution**: Weft originally planned custom multi-queue monitoring but now leverages SimpleBroker's mature MultiQueueWatcher implementation:

**SimpleBroker's MultiQueueWatcher Advantages**:
- **Mature architecture**: Extends BaseWatcher with full polling strategies and lifecycle management
- **Per-queue handlers**: Different message handlers and error handlers per queue
- **Dynamic management**: Runtime queue addition/removal via `add_queue()` and `remove_queue()`
- **Performance optimization**: Active/inactive queue management, round-robin cycling, shared database connection
- **Proven patterns**: Priority queues, load balancing, monitoring demonstrated in examples

**Weft Usage Pattern**:
```python
# Task extends SimpleBroker's MultiQueueWatcher
class Task(MultiQueueWatcher):
    def __init__(self, taskspec: TaskSpec):
        task_queues = [f"T{self.tid}.ctrl_in", f"T{self.tid}.inbox", f"T{self.tid}.reserved"]
        queue_handlers = {
            f"T{self.tid}.ctrl_in": self._handle_control_message,
            f"T{self.tid}.inbox": self._handle_work_message, 
            f"T{self.tid}.reserved": self._handle_reserved_message,
        }
        super().__init__(queues=task_queues, queue_handlers=queue_handlers, db=db_path)
```

This eliminates custom queue monitoring code while providing sophisticated multi-queue capabilities.

### Built-in Safety Features

1. **Message Size Limit**: 10MB enforced by SimpleBroker
2. **Queue Name Validation**: Alphanumeric + underscore/hyphen/period only
3. **Exactly-Once Delivery**: Via claim/delete semantics
4. **Atomic Operations**: Move, broadcast are atomic
5. **No Data Loss**: Messages persist until explicitly deleted

### CLI Integration

Weft can shell out to SimpleBroker CLI for operations:
```bash
# Direct CLI usage for debugging/management
broker list                           # Show all queues
broker peek T{tid}.reserved --json    # Inspect failed tasks
broker move T{tid}.reserved T{tid}.inbox  # Retry failed task
broker --vacuum                       # Cleanup deleted messages
```

## Weft Context and Directory Scoping

### The weft_context Concept: Git-like Project Scoping

Weft uses **git-like project scoping** where a `.weft/` directory marks the project root. Commands executed anywhere in the project tree discover and use the same centralized broker database.

#### Project Discovery Algorithm
1. Start from current working directory
2. Look for `.weft/` directory in current directory
3. If not found, traverse up parent directories
4. Continue until `.weft/` found or filesystem root reached
5. If no `.weft/` found, offer interactive initialization

#### Project Structure
```
project-root/
├── .weft/                  # Weft project marker and data
│   ├── broker.db          # SimpleBroker database
│   ├── config.json        # Project configuration
│   ├── outputs/           # Large output spillover
│   └── logs/              # Centralized logging
├── src/                   # User code
├── docs/                  # User docs
└── subdirs/               # All subdirectories use same .weft/
```

#### Context Discovery Benefits

1. **Consistent Behavior**: Same tasks visible from any subdirectory
2. **Project Isolation**: Different projects have separate task systems
3. **Developer Familiarity**: Follows git's well-understood model
4. **Emergency Access**: Always know where the database is located

#### TaskSpec Context Field

The `weft_context` field now represents the discovered project root:

```python
# TaskSpec with discovered context
{
    "spec": {
        "weft_context": "/path/to/project",  # Discovered .weft/ parent directory
        # ... other fields
    }
}
```

**Discovery Behavior**:
- If `weft_context` is `null`: Use discovery algorithm from current directory
- If `weft_context` is specified: Use that directory as project root
- All tasks in the same context share the centralized `.weft/broker.db`

### Context Management API

Context management is handled by `weft/context.py` which provides discovery and initialization functions:

```python
# weft/context.py - Core context discovery module
from pathlib import Path
from typing import Dict, Any, Optional
import os
import json
import time

def get_context() -> Dict[str, Any]:
    """Get current weft context with automatic discovery."""
    weft_root = discover_weft_root()
    
    if not weft_root:
        raise WeftContextError("No weft project found. Run 'weft init' to initialize.")
    
    return {
        "root_path": weft_root,
        "weft_dir": weft_root / ".weft", 
        "db_path": str(weft_root / ".weft" / "broker.db"),
        "config_path": weft_root / ".weft" / "config.json"
    }

def discover_weft_root(start_path: Optional[Path] = None) -> Optional[Path]:
    """Find .weft directory by traversing up like git."""
    current = (start_path or Path.cwd()).resolve()
    
    # Traverse up looking for .weft/
    while current != current.parent:
        weft_dir = current / ".weft"
        if weft_dir.is_dir():
            return current
        current = current.parent
    
    return None

def needs_initialization(path: Optional[Path] = None) -> bool:
    """Check if directory needs weft initialization."""
    return discover_weft_root(path) is None

def initialize_project(root_path: Optional[Path] = None, force: bool = False) -> Path:
    """Initialize .weft/ directory structure using SimpleBroker's security validation."""
    root = (root_path or Path.cwd()).resolve()
    weft_dir = root / ".weft"
    db_path = weft_dir / "broker.db"
    
    if weft_dir.exists() and not force:
        raise WeftContextError(f"Weft project already exists: {weft_dir}")
    
    # Use SimpleBroker's path validation and security functions
    try:
        # Import SimpleBroker's validation functions
        from simplebroker.cli import validate_database_path
        from simplebroker.db import BrokerDB, _validate_queue_name_cached
        
        # Validate database path using SimpleBroker's security checks
        validate_database_path(str(db_path), str(root))
        
    except ImportError:
        # Fallback validation if SimpleBroker not available
        _validate_path_security(db_path, root)
    
    # Create directory structure with proper permissions
    weft_dir.mkdir(exist_ok=True, mode=0o700)  # Owner-only access
    (weft_dir / "outputs").mkdir(exist_ok=True, mode=0o700)
    (weft_dir / "logs").mkdir(exist_ok=True, mode=0o700)
    
    # Create initial configuration with a SimpleBroker-generated timestamp
    with BrokerDB(str(db_path)) as db:
        created_ts = db.generate_timestamp()
    config = {
        "version": "1.0",
        "created": created_ts,
        "project_name": root.name
    }
    config_path = weft_dir / "config.json"
    config_path.write_text(json.dumps(config, indent=2))
    
    # Set secure file permissions using SimpleBroker's approach
    try:
        os.chmod(config_path, 0o600)  # Owner read/write only
        if db_path.exists():
            os.chmod(db_path, 0o600)
    except OSError as e:
        import warnings
        warnings.warn(f"Could not set secure permissions: {e}", RuntimeWarning)
    
    return root

def _validate_path_security(db_path: Path, root_path: Path) -> None:
    """Fallback path validation using SimpleBroker's security patterns."""
    # Check for parent directory references (like SimpleBroker)
    from pathlib import PurePath
    file_path_pure = PurePath(db_path.relative_to(root_path))
    
    for part in file_path_pure.parts:
        if part == "..":
            raise WeftContextError(
                f"Database path must not contain parent directory references: {db_path}"
            )
    
    # Resolve symlinks and validate containment (like SimpleBroker)
    try:
        resolved_db = db_path.resolve()
        resolved_root = root_path.resolve()
        
        # Ensure database stays within root directory
        if not str(resolved_db).startswith(str(resolved_root) + os.sep):
            raise WeftContextError(
                "Database file must be within the project directory"
            )
    except (RuntimeError, OSError) as e:
        raise WeftContextError(f"Path validation failed: {e}")
    
    # Check parent directory permissions (like SimpleBroker)
    parent = db_path.parent
    if parent.exists():
        if not os.access(parent, os.X_OK):
            raise WeftContextError(f"Parent directory not accessible: {parent}")
        if not os.access(parent, os.W_OK):
            raise WeftContextError(f"Parent directory not writable: {parent}")

def get_queue(queue_name: str, context: Optional[Dict[str, Any]] = None) -> Queue:
    """Get SimpleBroker queue using discovered context."""
    if context is None:
        context = get_context()
    
    return Queue(queue_name, db=context["db_path"])

class WeftContextError(Exception):
    """Raised when context operations fail."""
    pass
```

### SimpleBroker Command Integration

Weft queue commands delegate directly to SimpleBroker's command functions, automatically injecting the discovered database path:

```python
# weft/commands.py - SimpleBroker integration pattern
from simplebroker import commands as sb_commands
from weft.context import get_context
from typing import Optional

def cmd_read(
    queue_name: str,
    all_messages: bool = False,
    json_output: bool = False,
    show_timestamps: bool = False,
    since_str: Optional[str] = None,
    message_id_str: Optional[str] = None,
) -> int:
    """Read from queue using weft project database."""
    ctx = get_context()
    return sb_commands.cmd_read(
        db_path=ctx["db_path"],  # Auto-discovered database
        queue_name=queue_name,
        all_messages=all_messages,
        json_output=json_output,
        show_timestamps=show_timestamps,
        since_str=since_str,
        message_id_str=message_id_str
    )

def cmd_write(
    queue_name: str,
    message: str,
    json_output: bool = False,
) -> int:
    """Write to queue using weft project database."""
    ctx = get_context()
    return sb_commands.cmd_write(
        db_path=ctx["db_path"],
        queue_name=queue_name,
        message=message,
        json_output=json_output
    )
```

### CLI Integration and Auto-Initialization

```bash
# CLI commands work from any subdirectory
cd /project/deep/subdirectory
weft status                    # Uses /project/.weft/broker.db
weft queue read results        # Same database
weft run analyze-code          # Same database

# Auto-initialization on first use
cd /new/project
weft run task                  # Prompts: "Initialize weft project? [y/N]"

# Explicit initialization
weft init                      # Creates .weft/ in current directory
weft init --force              # Reinitialize existing project
```

### Context Information and Management

```bash
# Project information
weft info                      # Show current project context
weft status --project          # Show project-wide task summary

# Emergency access patterns
find . -name ".weft" -type d   # Find all weft projects
ls -la /project/.weft/         # Direct access to weft data
broker --db /project/.weft/broker.db list  # Direct SimpleBroker access
```

## Integration Patterns

### Task Creation with Context

```python
def create_task_in_context(context_path: str, task_config: dict) -> str:
    """Create a task within a specific weft context."""
    
    # Ensure context exists
    context = WeftContext(context_path)
    context.ensure_context()
    
    # Create TaskSpec with explicit context
    task_config["spec"]["weft_context"] = str(context.path)
    taskspec = TaskSpec.model_validate(task_config)
    
    # Create and run task
    task = Task(taskspec, context=context)
    tid = task.run_async()  # Returns immediately with TID
    
    return tid
```

### Cross-Context Communication

```python
# Tasks in different contexts are isolated
# Must explicitly bridge contexts if needed
def bridge_contexts(source_context: str, target_context: str, message: str):
    """Forward message between contexts (rare use case)."""
    
    source = WeftContext(source_context)
    target = WeftContext(target_context)
    
    # Read from source
    msg = source.get_queue("bridge.out").read_one()
    
    # Write to target
    target.get_queue("bridge.in").write(msg)
```

### Context-Aware Monitoring

```python
class ContextMonitor:
    """Monitor tasks across multiple contexts."""
    
    def discover_contexts(self) -> list[WeftContext]:
        """Find all weft contexts on the system."""
        contexts = []
        
        # Search common locations
        for root in [Path.home(), Path("/tmp"), Path("/var")]:
            for db_file in root.rglob(".broker.db"):
                contexts.append(WeftContext(db_file.parent))
        
        return contexts
    
    def get_global_status(self) -> dict:
        """Get status across all discovered contexts."""
        status = {}
        
        for context in self.discover_contexts():
            if context.is_active():
                status[str(context.path)] = self.get_context_tasks(context)
        
        return status
    
    def get_context_tasks(self, context: WeftContext) -> list[dict]:
        """Get all tasks in a specific context."""
        log_queue = context.get_queue("weft.tasks.log")
        return [json.loads(msg) for msg in log_queue.peek_all()]
```

## Performance Considerations

### Database Performance

SimpleBroker's SQLite backend provides:
- **Concurrent reads**: Multiple processes can read simultaneously
- **WAL mode**: Write-ahead logging for better concurrency
- **Automatic vacuuming**: Reclaims space from deleted messages
- **Index optimization**: Efficient message retrieval by timestamp

### Connection Management

```python
class QueueConnectionPool:
    """Manage SimpleBroker queue connections efficiently."""
    
    def __init__(self, context: WeftContext):
        self.context = context
        self._connections = {}
    
    def get_queue(self, name: str) -> Queue:
        """Get or create queue connection."""
        if name not in self._connections:
            self._connections[name] = Queue(name, db=str(self.context.db_path))
        return self._connections[name]
    
    def close_all(self):
        """Close all connections (called on process exit)."""
        for queue in self._connections.values():
            queue.close()
        self._connections.clear()
```

### Memory Management

- **Queue size monitoring**: Track message counts per queue
- **Large message handling**: Automatic spillover to disk for >10MB messages
- **Cleanup policies**: Remove old completed task data
- **Connection pooling**: Reuse database connections

## Related Documents

- **[00-Overview_and_Architecture.md](00-Overview_and_Architecture.md)** - System overview and design principles
- **[01-TaskSpec.md](01-TaskSpec.md)** - Task configuration specification including weft_context
- **[02-Core_Components.md](02-Core_Components.md)** - Detailed component architecture
- **[03-Worker_Architecture.md](03-Worker_Architecture.md)** - Recursive worker model
- **[05-Message_Flow_and_State.md](05-Message_Flow_and_State.md)** - Communication patterns
- **[06-Resource_Management.md](06-Resource_Management.md)** - Resource controls and error handling

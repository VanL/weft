# Worker Architecture: The Recursive Model

This document details Weft's recursive architecture where Workers are themselves Tasks, using the same primitive throughout the system.

## Conceptual Model: Everything is a Task

The system uses a **recursive architecture** where Workers are Tasks that run long-lived targets. This approach provides:
- **No special cases**: Workers follow the same lifecycle as any Task
- **Uniform observability**: Workers appear in process listings, logs, and monitoring
- **Uniform control**: Workers respond to the same control messages
- **Self-hosting**: The system can spawn and manage itself

## Worker as Long-Running Task

A Worker is simply a Task whose target function contains a `run_forever` loop:

```python
class WorkerTask:
    """A Task that spawns other Tasks - the recursive primitive."""
    
    def __init__(self, taskspec: TaskSpec):
        # Workers ARE Tasks with their own TID
        self.tid = taskspec.tid
        self.name = taskspec.name  # e.g., "worker-spawner"
        self.registry = TaskRegistry()  # Pre-loaded safe tasks
        self.stop_requested = False
        
    def run_forever(self):
        """The target function that makes this Task a Worker."""
        # Set process title as a worker
        setproctitle(f"weft-{self.tid[-10:]}:{self.name}:listening")
        
        # Register as active worker
        Queue("weft.workers.registry").write({
            "tid": self.tid,
            "name": self.name,
            "capabilities": list(self.registry.tasks.keys()),
            "started_at": time.time_ns()
        })
        
        while not self.stop_requested:
            # Monitor inbox for spawn requests (with timeout for control checks)
            msg = Queue(f"T{self.tid}.inbox").read_one(timeout=1.0)
            
            if msg:
                self.handle_spawn_request(msg)
            
            # Check control queue
            self.check_control_messages()
            
        # Cleanup on exit
        self.cleanup()
```

## TID Correlation: Message ID as Task ID

A critical design decision is using the **SimpleBroker message timestamp as the Task ID**:

```python
def handle_spawn_request(self, msg: dict):
    """Spawn a child task using message ID as TID."""
    # The message timestamp becomes the child's TID
    # This provides end-to-end correlation throughout the task's lifecycle
    child_tid = str(msg["_timestamp"])  # e.g., "1837025672140161024"
    
    # Extract spawn parameters
    template_name = msg["task"]
    params = msg.get("params", {})
    
    # Create child TaskSpec
    child_spec = self.create_child_spec(
        tid=child_tid,  # Message ID becomes Task ID
        template=template_name,
        params=params
    )
    
    # Spawn the child
    self.spawn_child(child_spec)
    
    # Log the spawn with correlation
    Queue("weft.tasks.log").write({
        "event": "task_spawned",
        "parent_tid": self.tid,
        "child_tid": child_tid,
        "correlation_id": child_tid,  # Same ID everywhere
        "template": template_name
    })
```

**Benefits of TID Correlation**:
- **Single ID**: One identifier follows the task from request to completion
- **Natural ordering**: Timestamp-based TIDs sort chronologically
- **Guaranteed uniqueness**: SimpleBroker ensures no timestamp collisions
- **Audit trail**: Easy to trace a task's entire lifecycle

## Bootstrap Sequence

The system starts with a minimal bootstrap that creates the primordial worker:

```python
#!/usr/bin/env python3
# weft - The bootstrap executable

def bootstrap():
    """Initialize the Weft system with primordial worker."""
    
    # 1. SimpleBroker database is auto-created on first queue use
    # No explicit initialization needed - SimpleBroker handles it
    
    # 2. Create primordial worker specification
    prime_spec = TaskSpec(
        tid=generate_tid(),  # Bootstrap generates first TID
        name="worker-prime",
        spec=SpecSection(
            type="function",
            function_target="weft.worker:WorkerTask.run_forever",
            timeout=None,  # Workers don't timeout
            limits=LimitsSection(
                memory_mb=512,  # Conservative for a dispatcher
                cpu_percent=10    # Low CPU usage expected
            )
        ),
        io=IOSection(
            inputs={"inbox": "weft.spawn.requests"},  # Global spawn queue
            outputs={"outbox": "weft.spawn.results"},
            control={"ctrl_in": "weft.workers.control", 
                    "ctrl_out": "weft.workers.status"}
        ),
        metadata={
            "worker_type": "primordial",
            "auto_restart": True
        }
    )
    
    # 3. Launch primordial worker AS A TASK
    prime_worker = Task(prime_spec)
    
    # 4. The worker runs forever, spawning other tasks
    prime_worker.run()  # Blocks until shutdown
```

## Worker Hierarchy and Specialization

Workers can spawn other workers, creating a hierarchy:

```
weft (bootstrap)
 └── worker-prime (primordial, spawns other workers)
      ├── worker-unix (specialized for Unix commands)
      │    ├── grep-task
      │    └── sed-task
      ├── worker-ai (specialized for AI agents)
      │    ├── llm-task
      │    └── embedder-task
      └── worker-monitor (system monitoring)
           └── cleanup-task
```

Each worker can have:
- **Different registries**: Control what tasks they can spawn
- **Different resource limits**: Heavy vs light workers
- **Different queues**: Isolate workloads

## Worker Lifecycle Management

```python
class WorkerLifecycle:
    """Manage worker lifecycle as long-running tasks."""
    
    @staticmethod
    def spawn_worker(worker_type: str, registry: TaskRegistry) -> str:
        """Spawn a new worker and return its TID."""
        worker_spec = TaskSpec(
            tid=generate_tid(),
            name=f"worker-{worker_type}",
            spec=SpecSection(
                type="function",
                function_target="weft.worker:WorkerTask.run_forever",
                timeout=None  # Workers are long-lived
            ),
            io=IOSection(
                inputs={"inbox": f"worker.{worker_type}.requests"},
                outputs={"outbox": f"worker.{worker_type}.results"},
                control={"ctrl_in": f"worker.{worker_type}.control",
                        "ctrl_out": f"worker.{worker_type}.status"}
            ),
            metadata={
                "worker_type": worker_type,
                "registry": registry.name
            }
        )
        
        # Spawn worker using multiprocessing
        ctx = multiprocessing.get_context("spawn")
        process = ctx.Process(
            target=Task(worker_spec).run,
            daemon=False  # Workers outlive parent
        )
        process.start()
        
        return worker_spec.tid
    
    @staticmethod
    def stop_worker(tid: str, graceful: bool = True):
        """Stop a worker task."""
        if graceful:
            # Send control message
            Queue(f"T{tid}.ctrl_in").write("STOP")
        else:
            # Use OS signal via PID
            mapping = find_tid_mapping(tid)
            if mapping:
                os.kill(mapping["pid"], signal.SIGTERM)
```

## Design Tradeoffs

**Advantages of Workers as Tasks**:
- **Simplicity**: One concept (Task) instead of two
- **Uniformity**: All entities follow same patterns
- **Observability**: Workers visible in all tools
- **Flexibility**: Workers can be ephemeral or permanent
- **Composability**: Workers can spawn other workers

**Considerations**:
- **Resource allocation**: Workers need appropriate limits
- **Queue proliferation**: Each worker has 5 queues
- **Bootstrap complexity**: Need robust primordial worker
- **Recovery**: Must handle worker failures gracefully

## Worker Registry Pattern

Workers register their capabilities in `weft.workers.registry`:

```python
# Registry message format
{
    "tid": "1837025672140161024",
    "name": "worker-ai",
    "type": "ai-agent-spawner",
    "capabilities": ["llm-query", "embedding", "rag-search"],
    "status": "active",
    "load": 0.3,  # Current load factor
    "spawned_count": 42,
    "queue": "worker.ai.requests"
}
```

Task routing can use this registry:

```python
def route_task(task_request: dict) -> str:
    """Route task request to appropriate worker."""
    task_type = task_request["task"]
    
    # Find capable workers
    for msg in Queue("weft.workers.registry").peek_all():
        worker = json.loads(msg)
        if task_type in worker["capabilities"] and worker["status"] == "active":
            # Route to this worker
            Queue(worker["queue"]).write(task_request)
            return worker["tid"]
    
    raise ValueError(f"No worker available for task: {task_type}")
```

## Process Creation Strategy

### Design Rationale for Process Isolation

**Architecture Decision**: Tasks execute targets in separate processes rather than threads to achieve resource isolation and OS-level control capabilities.

**Process Isolation Benefits**:
- **Resource enforcement**: Memory, CPU, and file descriptor limits via OS mechanisms
- **Crash isolation**: Target process failure doesn't affect main Task process  
- **Signal-based control**: Reliable termination via SIGTERM/SIGKILL
- **Monitoring visibility**: Separate PIDs enable independent resource tracking
- **Security boundaries**: Process isolation limits blast radius of target code

**Text-Based Interface Rationale**:
- **Unix philosophy**: All inputs/outputs are strings (pipe-compatible)
- **Serialization simplicity**: No Python object marshaling across process boundaries
- **Queue compatibility**: Text messages flow naturally through SimpleBroker
- **Uniform interface**: Same pattern for both function and command targets

### Using multiprocessing.spawn for Consistency

The system uses `multiprocessing.get_context("spawn")` for process creation across all platforms:

```python
class ProcessSpawner:
    """Spawn processes consistently across platforms."""
    
    def __init__(self):
        # Always use spawn for consistency
        self.ctx = multiprocessing.get_context("spawn")
    
    def spawn_task(self, target: Callable, args: tuple) -> Process:
        """Spawn a task process with clean state."""
        # Pre-import heavy modules in parent to optimize startup
        self._warmup_imports()
        
        # Create process with spawn context
        process = self.ctx.Process(
            target=self._task_wrapper,
            args=(target, args),
            daemon=True  # Don't block parent exit
        )
        
        process.start()
        return process
    
    def _warmup_imports(self) -> None:
        """Pre-import common modules to warm disk cache."""
        import psutil
        import numpy
        import pandas
        # These imports speed up child process startup
    
    def _task_wrapper(self, target: Callable, args: tuple) -> None:
        """Wrapper that ensures clean process state."""
        # Recreate all Queue connections in child
        self._recreate_connections()
        
        # Set process title if available
        self._set_process_title(args[0])  # Assuming first arg is taskspec
        
        # Execute target
        target(*args)
    
    def _recreate_connections(self) -> None:
        """Recreate SimpleBroker connections in child process."""
        # SimpleBroker connections don't survive fork/spawn
        # Must create new Queue objects in child
        global _queue_cache
        _queue_cache = {}  # Clear any cached connections
```

### Spawn vs Fork Tradeoffs

| Aspect | spawn | fork |
|--------|-------|------|
| **Startup time** | 50-100ms (module reimport) | 5-10ms (copy-on-write) |
| **Memory usage** | Higher (no sharing) | Lower (COW pages) |
| **Safety** | Clean state, no lock issues | Inherited locks/threads risky |
| **SimpleBroker** | ✅ Works (new connections) | ⚠️ Connection issues |
| **Cross-platform** | ✅ All platforms | ❌ Unix only |
| **Serialization** | Required (pickle) | Not needed |

### Implementation Checklist

- [ ] Always use `multiprocessing.get_context("spawn")`
- [ ] Recreate Queue objects in child processes
- [ ] Pre-import heavy modules in parent for optimization
- [ ] Set daemon=True to prevent blocking parent exit
- [ ] Handle serialization requirements (pickle-able args)
- [ ] Test on Linux, macOS, and Windows

## Testing Worker Architecture

```python
class TestWorkerArchitecture:
    def test_worker_is_task(self):
        """Workers follow Task lifecycle."""
        worker_spec = create_worker_spec("test-worker")
        assert worker_spec.spec.type == "function"
        assert worker_spec.spec.timeout is None  # Long-lived
        
    def test_worker_spawns_child(self):
        """Worker can spawn child tasks."""
        worker = WorkerTask(worker_spec)
        child_tid = worker.spawn_child("grep", {"pattern": "test"})
        # Child appears in process list
        assert task_exists(child_tid)
        
    def test_tid_correlation(self):
        """Message ID becomes Task ID."""
        msg = {"_timestamp": 1837025672140161024, "task": "grep"}
        child_tid = str(msg["_timestamp"])
        # Same ID used throughout
        assert child_tid == "1837025672140161024"
        
    def test_worker_hierarchy(self):
        """Workers can spawn other workers."""
        prime = spawn_worker("prime")
        secondary = request_spawn(prime, "worker", {"type": "secondary"})
        assert worker_exists(secondary)
```

## Emergency Task Management

Process titles enable system administration via standard Unix tools:

```bash
# Emergency scenarios (no escaping needed!)
ps aux | grep "weft-.*:timeout"          # Find all timed-out tasks
pkill -f "weft-.*:failed"                # Kill all failed tasks
ps aux | grep "weft-.*:analyze"          # Find specific task type

# Resource investigation
top -p $(pgrep -f "weft-" -d,)           # Monitor all weft tasks
htop -F "weft-"                          # Interactive task monitoring

# Stuck task recovery
# 1. Find stuck task (long-running)
ps aux | grep "weft-.*:running" | grep -E "[0-9]+:[0-9]{2}:[0-9]{2}"
# 2. Extract short TID (simple cut command)
tid=$(ps aux | grep "weft-.*:running" | head -1 | cut -d- -f2 | cut -d: -f1)
# 3. Lookup full TID
weft tid $tid
# 4. Inspect queues
weft queue peek T$(weft tid $tid).reserved
# 5. Force recovery
weft task recover $(weft tid $tid)

# Batch operations (much simpler!)
for tid in $(ps aux | grep weft- | cut -d- -f2 | cut -d: -f1 | sort -u); do
    echo "Processing task $tid"
    full_tid=$(weft tid $tid)
    weft task status $full_tid
done

# One-liners that just work
pkill -f "weft-"                         # Kill all weft tasks
pgrep -f "weft-.*:failed" | xargs kill   # Kill failed tasks
ps aux | grep weft- | wc -l              # Count running tasks

# Extract and analyze
ps aux | grep weft- | awk -F'[-:]' '{print $3}' | sort | uniq -c  # Count by task name
ps aux | grep weft- | awk -F'[-:]' '{print $4}' | sort | uniq -c  # Count by status
```

## Related Documents

- **[00-Overview_and_Architecture.md](00-Overview_and_Architecture.md)** - System overview and design principles
- **[01-TaskSpec.md](01-TaskSpec.md)** - Task configuration specification  
- **[02-Core_Components.md](02-Core_Components.md)** - Detailed component architecture
- **[04-SimpleBroker_Integration.md](04-SimpleBroker_Integration.md)** - Queue system integration
- **[05-Message_Flow_and_State.md](05-Message_Flow_and_State.md)** - Communication patterns
- **[09-Implementation_Plan.md](09-Implementation_Plan.md)** - Development roadmap
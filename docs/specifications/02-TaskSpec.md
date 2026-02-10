
# TaskSpec

This document defines the complete TaskSpec format - the core configuration structure for all tasks in Weft. Understanding TaskSpec structure is essential for task creation, validation, and system integration.

## Design Context [TS-0]

TaskSpec serves dual purposes:
- **Task Definition**: Specifies what work to execute and how to execute it
- **Runtime State**: Tracks execution progress, resource usage, and completion status

The specification uses **partial immutability**: configuration sections (spec, io) become frozen after task creation to prevent accidental changes, while state and metadata remain mutable for runtime updates.

_Implementation_: `weft/core/taskspec.py` implements this structure, including frozen sections, defaults, and validation helpers.

## JSON Schema v1.0 [TS-1]

_Implementation coverage_: Field validation and defaults correspond to Pydantic models in `weft/core/taskspec.py`. Runtime behaviour honours `stream_output` (chunked, base64-encoded messages), `output_size_limit_mb` (disk spillover), and `interactive` (long-lived command sessions with streaming stdin/stdout).

```jsonc
{
  "tid": "1837025672140161024" | null,       // REQUIRED at runtime. In templates, omit or set null as a placeholder. The Manager sets this to the spawn-request message ID (SimpleBroker 64-bit timestamp).
  "version": "1.0",                          // REQUIRED. Spec version for future evolution.
  "name": "analyze-specification-v4",        // REQUIRED. Human-readable name for reporting and legibility.
  "description": "Tool to analyze ...",      // OPTIONAL. Long form human-readable description.
  
  "spec": {                                  // REQUIRED. Defines the work to be done.
    "type": "function" | "command",          // REQUIRED. The type of execution. 
    "function_target": "weft.specs:analyze", // REQUIRED if type is "function". Format: "pkg.module:function_name". Any Python Callable.
    "process_target": "grep",               // REQUIRED if type is "command". Executable path or PATH binary.
    "args": [],                              // OPTIONAL. For commands, args are appended to build argv. For functions, args become *args.
    "keyword_args": {},                      // OPTIONAL. Keyword arguments for a function (named differently from Python kwargs due to technical constraints).
    "timeout": 300.0 | null,                 // OPTIONAL. Timeout in seconds. null means no timeout.
    
    "limits": {                              // OPTIONAL. Resource limits for task execution.
      "memory_mb": 256 | null,               // OPTIONAL. Memory limit in MB (default value 1Gb) null means no limit.
      "cpu_percent": 50 | null,              // OPTIONAL. CPU limit in percent (0-100). null means no limit.
      "max_fds": 20 | null,                  // OPTIONAL. Maximum number of open file descriptors. null means no limit.
      "max_connections": 0 | null            // OPTIONAL. Maximum number of network connections. null means no limit.
    },
    
    "env": { "DEBUG": "1" } | null,          // OPTIONAL. Environment variables to set in the task's process. These update, not replace, the existing environment.
    "working_dir": "/tmp" | null,            // OPTIONAL. Working directory. null means not applicable or not set.
    "weft_context": "/path/to/.weft" | null,  // OPTIONAL (runtime-expanded). Resolved project context for this task. Templates omit this; the Manager fills it when available.
    "interactive": false,                     // OPTIONAL. Keep command processes alive and stream stdin/stdout.
    "stream_output": false,                  // OPTIONAL. Stream stdout/stderr to queues. If false, all output will be written in one message.
    "cleanup_on_exit": true,                 // OPTIONAL. Delete empty task queues on completion (outbox retained until consumed)
    "reserved_policy_on_stop": "keep",      // OPTIONAL. Behaviour for messages left in T{tid}.reserved when STOP is received. Options: "keep", "requeue", "clear". Default is "keep".
    "reserved_policy_on_error": "keep",     // OPTIONAL. Behaviour for messages left in T{tid}.reserved when execution fails. Same options and default as above.
    "polling_interval": 1,                   // OPTIONAL. psutil polling interval in seconds. Defaults to 1 second.
    "reporting_interval": "poll" | "transition",  // OPTIONAL. Send reports to weft.log.tasks either on each transition or on each polling interval. Defaults to "transition"
    "monitor_class": "weft.core.resource_monitor.ResourceMonitor",   // OPTIONAL. Default value is "weft.core.resource_monitor.ResourceMonitor". A class conforming to the ResourceMonitor spec that will be used to wrap the target. NOTE: Module will be created at weft/core.resource_monitor.py
    
    "enable_process_title": true,            // OPTIONAL. Enable OS process title updates for observability. Defaults to true.
    "output_size_limit_mb": 10               // OPTIONAL. Max output size before disk spill. Defaults to 10MB (SimpleBroker limit).
  },
  "io": {                                    // REQUIRED (runtime). Defines the communication queues.
    "inputs" : {                             // REQUIRED (runtime). May be empty or have *n* input queues.
      "inbox": "T183...inbox"                // OPTIONAL. Stdin/input for long-running tasks. If provided, inbox will be routed by the Task to the target stdin.
    },   
    "outputs": {                             // REQUIRED (runtime). Must include at least the "outbox" queue. May include *n* output queues.
       "outbox": "T183...outbox",            // REQUIRED. For the final result message. Follows naming convention if not provided on task initialization.
    },
    "control": {                             // REQUIRED (runtime). Must include only the keys for the ctrl_in and ctrl_out queues.
      "ctrl_in": "T183...ctrl_in",           // REQUIRED. INPUT. Control input monitored by the Task. Used for job control/side-channel comms for tasks. 
      "ctrl_out": "T183...ctrl_out",         // REQUIRED. OUTPUT. Status, error messages reported by the Task. Used for side-channel comms. 
    }        
  },
  "state": {                                 // REQUIRED. Element must be present. 
    "status": "created|spawning|running|[...]", // REQUIRED, Current process state
    "pid": null,                             // OPTIONAL. Process ID when running
    "return_code": null,                     // REQUIRED. null means no return code yet (probably still running).
    "started_at": null,                      // REQUIRED. Nanosecond timestamp (e.g. time.time_ns())
    "completed_at": null,                    // REQUIRED. Nanosecond timestamp (e.g. time.time_ns())
    "error": null,                           // OPTIONAL. Error message if failed
    "time": 60.0 | null,                     // OPTIONAL. Time spent running so far (wall-clock in seconds). null = not started/measured yet, 0.0+ = actual measurement
    "memory": 4.8 | null,                    // OPTIONAL. Last memory measurement from psutil in MB. null = not measured yet, 0.0+ = actual measurement
    "cpu": 4 | null,                         // OPTIONAL. Last CPU percentage from psutil. null = not measured yet, 0+ = actual measurement (can be 0%)
    "fds": 3 | null,                         // OPTIONAL. Last number of open file descriptors from psutil. null = not measured yet, 0+ = actual count
    "net_connections": 0 | null,             // OPTIONAL. Last number of network connections from psutil. null = not measured yet, 0+ = actual count
    "peak_memory": 4.8 | null,               // OPTIONAL. Highest memory measurement over the life of the process. null = not measured yet
    "peak_cpu": 4 | null,                    // OPTIONAL. Highest CPU percentage over the life of the process. null = not measured yet
    "peak_fds": 3 | null,                    // OPTIONAL. Highest number of open file descriptors over the life of the process. null = not measured yet
    "peak_net_connections": 0 | null         // OPTIONAL. Highest number of network connections over the life of the process. null = not measured yet
  },
  "metadata": {                              // REQUIRED. Section must be present, but all keys are optional. User-defined metadata for querying and tracking. These can be updated by the Task or from outside by sending an update_metadata: {"key": "value" } over the ctrl_in queue.
    "owner": "claude-session-123",
    "session_id": "design-session-001",
    "agent_id": "agent-alpha"
  }
}
```

**Target semantics**
- Command tasks build argv as `[process_target] + args`.
- Function tasks call `function_target(*args, **keyword_args)`.

**TID format**
- TIDs are SimpleBroker 64-bit hybrid timestamps (microseconds + logical counter). Treat them as opaque, monotonic identifiers that may appear as 19-digit integers in decimal form.

Note: The field is named `keyword_args` (not `kwargs`) due to a technical
constraint in the model layer.

**Spec field groupings (for readability)**
- **Target**: `type`, `function_target`/`process_target`, `args`, `keyword_args`
- **Limits**: `limits.*`
- **Environment**: `env`, `working_dir`, `weft_context`
- **Behavior**: `interactive`, `stream_output`, `cleanup_on_exit`
- **Policies**: `reserved_policy_on_stop`, `reserved_policy_on_error`
- **Monitoring**: `polling_interval`, `reporting_interval`, `monitor_class`, `enable_process_title`
- **Output**: `output_size_limit_mb`

**Idempotency guidance**
- For single-message tasks, `tid` can be used as an idempotency key.
- For multi-message tasks, use the inbox/reserved message timestamp as the idempotency key.
- Recommended composite key: `tid:message_id`.

**Templates vs runtime-expanded specs**
- **TaskSpecTemplate**: Stored specs in `.weft/tasks/` omit `tid`, `io`, `state`, and `spec.weft_context` (or set `tid` to null). These templates declare *what to run*.
- **Resolved TaskSpec**: The Manager expands templates at spawn time, populating `tid`, `io`, `state`, and `spec.weft_context`.
- **TID assignment**: The spawn-request message ID (SimpleBroker 64-bit timestamp) is the task's TID for the full lifecycle.

_Implementation mapping_: `weft/core/taskspec.py` (`TaskSpec`, `SpecSection`, `IOSection`, `StateSection`), `weft/core/targets.py` (command argv construction), `weft/core/tasks/consumer.py` (streaming/output handling).

### Autostart Manifests [TS-1.2]

Autostart is enabled by operator intent: any JSON file placed in
`.weft/autostart/` is treated as a **manifest** that declares *what to run* and
*how to manage its lifecycle*. This keeps stored task specs reusable and keeps
autostart policy explicit and local to the autostart directory.

Manifests may point at an existing task spec or pipeline and optionally supply
default inputs/arguments. They do **not** include `tid`, queue names, or runtime
`state`; those are minted/expanded by the Manager at startup.

```jsonc
{
  "name": "agent-reviewer",
  "target": { "type": "task", "name": "review-agent" },
  "policy": {
    "mode": "ensure",           // "once" (default) | "ensure"
    "max_restarts": 5,          // optional; null = unlimited
    "backoff_seconds": 2.0      // optional; exponential backoff allowed
  },
  "defaults": {
    "args": ["--focus", "tests"],
    "keyword_args": {"priority": "low"},
    "input": "initial payload",
    "env": {"WEFT_TAG": "background"}
  }
}
```

**Targets**
- `task`: references a stored task spec in `.weft/tasks/`
- `pipeline`: references a pipeline stored in `.weft/pipelines/`

**Defaults**
- `args`/`keyword_args` are merged into the target’s `spec.args`/`spec.keyword_args`.
- `env` is merged into `spec.env`.
- `input` becomes the initial payload (equivalent to `pipeline run --input`).

**Lifecycle policy**
- `mode=once` launches exactly once per manager startup (default).
- `mode=ensure` restarts on unexpected exit with optional backoff while the
  manager is running. Manager shutdown stops autostarted tasks regardless of
  policy.

Managers record metadata linking the running task back to the manifest source
(`metadata.autostart_source`) and set `metadata.autostart=true` for
observability.

### Reserved queue policies [TS-1.1]

Reserved queue policy semantics are defined in
[05-Message_Flow_and_State.md](05-Message_Flow_and_State.md#8-failure-recovery-flow).
TaskSpec exposes `reserved_policy_on_stop` and `reserved_policy_on_error`, each
accepting `keep` (default), `requeue`, or `clear`.

_Implementation mapping_: `weft/core/tasks/base.py` (`_apply_reserved_policy`), `weft/core/tasks/consumer.py` (error/stop handling).

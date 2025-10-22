
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
  "tid": "1837025672140161024",              // REQUIRED. Unique 64-bit SimpleBroker timestamp. Provided on task initialization.
  "version": "1.0",                          // REQUIRED. Spec version for future evolution.
  "name": "analyze-specification-v4",        // REQUIRED. Human-readable name for reporting and legibility.
  "description": "Tool to analyze ...",      // OPTIONAL. Long form human-readable description.
  
  "spec": {                                  // REQUIRED. Defines the work to be done.
    "type": "function" | "command",          // REQUIRED. The type of execution. 
    "function_target": "weft.specs:analyze", // REQUIRED if type is "function". Format: "pkg.module:function_name". Any Python Callable.
    "process_target": ["grep"],              // REQUIRED if type is "command". Must be a string, either a path or a binary in the PATH
    "args": [],                              // OPTIONAL. Positional arguments for a target (function *args or subprocess args).
    "keyword_args": {},                      // OPTIONAL. Keyword arguments for a function.
    "timeout": 300.0 | null,                 // OPTIONAL. Timeout in seconds. null means no timeout.
    
    "limits": {                              // OPTIONAL. Resource limits for task execution.
      "memory_mb": 256 | null,               // OPTIONAL. Memory limit in MB (default value 1Gb) null means no limit.
      "cpu_percent": 50 | null,              // OPTIONAL. CPU limit in percent (0-100). null means no limit.
      "max_fds": 20 | null,                  // OPTIONAL. Maximum number of open file descriptors. null means no limit.
      "max_connections": 0 | null            // OPTIONAL. Maximum number of network connections. null means no limit.
    },
    
    "env": { "DEBUG": "1" } | null,          // OPTIONAL. Environment variables to set in the task's process. These update, not replace, the existing environment.
    "working_dir": "/tmp" | null,            // OPTIONAL. Working directory. null means not applicable or not set.
    "interactive": false,                     // OPTIONAL. Keep command processes alive and stream stdin/stdout.
    "stream_output": false,                  // OPTIONAL. Stream stdout/stderr to queues. If false, all output will be written in one message.
    "cleanup_on_exit": true,                 // OPTIONAL. Delete task queues on completion
    "reserved_policy_on_stop": "keep",      // OPTIONAL. Behaviour for messages left in T{tid}.reserved when STOP is received. Options: "keep", "requeue", "clear". Default is "keep".
    "reserved_policy_on_error": "keep",     // OPTIONAL. Behaviour for messages left in T{tid}.reserved when execution fails. Same options and default as above.
    "polling_interval": 1,                   // OPTIONAL. psutil polling interval in seconds. Defaults to 1 second.
    "reporting_interval": "poll" | "transition",  // OPTIONAL. Send reports to weft.tasks.log either on each transition or on each polling interval. Defaults to "transition"
    "monitor_class": "weft.core.resource_monitor.ResourceMonitor",   // OPTIONAL. Default value is "weft.core.resource_monitor.ResourceMonitor". A class conforming to the ResourceMonitor spec that will be used to wrap the target. NOTE: Module will be created at weft/core.resource_monitor.py
    
    "enable_process_title": true,            // OPTIONAL. Enable OS process title updates for observability. Defaults to true.
    "output_size_limit_mb": 10,              // OPTIONAL. Max output size before disk spill. Defaults to 10MB (SimpleBroker limit).
    "weft_context": "/path/to/project" | null // OPTIONAL. Directory containing .broker.db and .weft/ - defines weft scope. Defaults to current working directory.
  },
  "io": {                                    // REQUIRED. Defines the communication queues.
    "inputs" : {                             // REQUIRED. Element must be present. May be empty or have *n* input queues.
      "inbox": "T183...inbox"                // OPTIONAL. Stdin/input for long-running tasks. If provided, inbox will be routed by the Task to the target stdin.
    },   
    "outputs": {                             // REQUIRED. Element must be present. Must include at least the "outbox" queue. May include *n* output queues.
       "outbox": "T183...outbox",            // REQUIRED. For the final result message. Follows naming convention if not provided on task initialization.
    },
    "control": {                             // REQUIRED. Element must be present. Must include only the keys for the ctrl_in and ctrl_out queues.
      "ctrl_in": "T183...ctrl_in",           // REQUIRED. INPUT. Control input monitored by the Task. Used for job control/side-channel comms for tasks. 
      "ctrl_out": "T183...ctrl_out",         // REQUIRED. OUTPUT. Status, error messages reported by the Task. Used for side-channel comms. 
    }        
  },
  "state": {                                 // REQUIRED. Element must be present. 
    "status": "created|running|[...]",       // REQUIRED, Current process state
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
    "max_memory": 4.8 | null,                // OPTIONAL. Highest memory measurement over the life of the process. null = not measured yet
    "max_cpu": 4 | null,                     // OPTIONAL. Highest CPU percentage over the life of the process. null = not measured yet
    "max_fds": 3 | null,                     // OPTIONAL. Highest number of open file descriptors over the life of the process. null = not measured yet
    "max_net_connections": 0 | null          // OPTIONAL. Highest number of network connections over the life of the process. null = not measured yet
  },
  "metadata": {                              // REQUIRED. Section must be present, but all keys are optional. User-defined metadata for querying and tracking. These can be updated by the Task or from outside by sending an update_metadata: {"key": "value" } over the ctrl_in queue.
    "owner": "claude-session-123",
    "tags": ["spec", "analysis"],
    "session_id": "design-session-001",
    "agent_id": "agent-alpha"
  }
}
```

### Reserved queue policies [TS-1.1]

_Implementation_: `BaseTask._apply_reserved_policy` (`weft/core/tasks/base.py`) enforces these policies for inbox reserved handling.

Both `reserved_policy_on_stop` and `reserved_policy_on_error` accept one of three values:

- `keep` (default): leave the message in `T{tid}.reserved` so operators can inspect or retry manually.
- `requeue`: move the message back to the inbox (`T{tid}.inbox`) so it can be picked up again automatically.
- `clear`: acknowledge and delete the reserved message.

The STOP and error policies are configured independently so teams can keep error payloads for post-mortem analysis while automatically requeuing work that was cancelled intentionally.

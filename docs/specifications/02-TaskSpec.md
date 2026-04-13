
# TaskSpec

This document defines the complete TaskSpec format - the core configuration structure for all tasks in Weft. Understanding TaskSpec structure is essential for task creation, validation, and system integration.

## Design Context [TS-0]

TaskSpec serves dual purposes:
- **Task Definition**: Specifies what work to execute and how to execute it
- **Runtime State**: Tracks execution progress, resource usage, and completion status

The specification uses **partial immutability**: configuration sections (spec, io) become frozen after task creation to prevent accidental changes, while state and metadata remain mutable for runtime updates.

`TaskSpec` is intentionally a leaf execution contract. Pipeline topology,
bindings, generated edges, and pipeline-level retry or checkpoint semantics
belong in `PipelineSpec` rather than in child TaskSpecs.

User-authored TaskSpecs remain the public leaf types described here:
`function`, `command`, and `agent`. Pipeline orchestrators and generated edge
workers are runtime-owned internal tasks. The pipeline runtime may realize them
with reserved internal task classes or reserved internal targets without
expanding the normal user-authored `spec.type` vocabulary.

_Implementation mapping_: `weft/core/taskspec.py` (`TaskSpec`, `SpecSection`, `IOSection`, `StateSection`, `_freeze_spec`, `FrozenList`, `FrozenDict`). Partial immutability is enforced via `_freeze_spec()` on `model_post_init`, which recursively freezes `spec` and `io` sections using `_freeze()` methods on each sub-model. `state` and `metadata` remain mutable via `__setattr__` guards checking `_frozen_fields`.

## JSON Schema v1.0 [TS-1]

_Implementation coverage_: Field validation and defaults correspond to Pydantic
models in `weft/core/taskspec.py`. Runtime behaviour honours `stream_output`
(chunked, base64-encoded messages), `output_size_limit_mb` (disk spillover),
and `interactive` (long-lived, line-oriented command sessions with streaming
stdin/stdout over task-local queues rather than terminal emulation).
`spec.type="agent"` is implemented with `spec.agent.runtime="llm"`, Python
tools, `output_mode` values `text`/`json`/`messages`, and persistent
continuations when `spec.persistent=true` and
`spec.agent.conversation_scope="per_task"`. The schema below treats one-off vs
persistent execution as a **task lifecycle concern**, not an agent-specific
field.

```jsonc
{
  "tid": "1837025672140161024" | null,       // REQUIRED at runtime. In templates, omit or set null as a placeholder. The Manager sets this to the spawn-request message ID (SimpleBroker 64-bit timestamp).
  "version": "1.0",                          // REQUIRED. Spec version for future evolution.
  "name": "analyze-specification-v4",        // REQUIRED. Human-readable name for reporting and legibility.
  "description": "Tool to analyze ...",      // OPTIONAL. Long form human-readable description.
  
  "spec": {                                  // REQUIRED. Defines the work to be done.
    "type": "function" | "command" | "agent", // REQUIRED. The type of execution.
    "persistent": false,                     // OPTIONAL. Generic task lifecycle flag. false = one-shot, true = keep draining inbox until stopped.
    "function_target": "weft.specs:analyze", // REQUIRED if type is "function". Format: "pkg.module:function_name". Any Python Callable.
    "process_target": "grep",               // REQUIRED if type is "command". Executable path or PATH binary.
    "agent": {                              // REQUIRED if type is "agent". Static agent runtime config.
      "runtime": "llm",
      "model": "openai/gpt-4o-mini" | null,
      "instructions": "You are a careful reviewer." | null, // Static system/developer prompt.
      "templates": {
        "review_patch": {
          "instructions": "Review the patch carefully.",
          "prompt": "Patch:\n{{ patch }}"
        }
      },
      "tools": [],
      "output_mode": "text" | "json" | "messages",
      "output_schema": null,
      "max_turns": 20,
      "options": {},                        // Backend/model-specific prompt options.
      "conversation_scope": "per_message" | "per_task",
      "runtime_config": {}
    },
    "args": [],                              // OPTIONAL. For commands, args are appended to build argv. For functions, args become *args.
    "keyword_args": {},                      // OPTIONAL. Keyword arguments for a function (named differently from Python kwargs due to technical constraints).
    "runner": {                              // OPTIONAL. Execution backend selection. Defaults to the built-in host runner.
      "name": "host",                        // REQUIRED when the runner object is present. Runner plugin name.
      "options": {}                          // OPTIONAL. Runner-specific JSON-serializable options.
    },
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
    "interactive": false,                     // OPTIONAL. Keep command processes alive and stream line-oriented stdin/stdout over queues. This is not a PTY/TTY terminal-emulation mode.
    "stream_output": false,                  // OPTIONAL. Stream stdout/stderr to queues. If false, all output will be written in one message.
    "cleanup_on_exit": true,                 // OPTIONAL. Delete empty task queues on completion (outbox retained until consumed)
    "reserved_policy_on_stop": "keep",      // OPTIONAL. Behaviour for messages left in T{tid}.reserved when STOP is received. Options: "keep", "requeue", "clear". Default is "keep".
    "reserved_policy_on_error": "keep",     // OPTIONAL. Behaviour for messages left in T{tid}.reserved when execution fails, times out, or is killed. Same options and default as above.
    "polling_interval": 1,                   // OPTIONAL. psutil polling interval in seconds. Defaults to 1 second.
    "reporting_interval": "poll" | "transition",  // OPTIONAL. Send reports to weft.log.tasks either on each transition or on each polling interval. Defaults to "transition"
    "monitor_class": "weft.core.resource_monitor.ResourceMonitor",   // OPTIONAL. Default value is "weft.core.resource_monitor.ResourceMonitor". A class conforming to the ResourceMonitor spec that will be used to wrap the target. NOTE: Module will be created at weft/core.resource_monitor.py
    
    "enable_process_title": true,            // OPTIONAL. Enable OS process title updates for observability. Defaults to true.
    "output_size_limit_mb": 10               // OPTIONAL. Max output size before disk spill. Defaults to 10MB (SimpleBroker limit).
  },
  "io": {                                    // REQUIRED (runtime). Defines the communication queues.
    "inputs" : {                             // REQUIRED (runtime). May be empty or have *n* input queues.
      "inbox": "T183...inbox"                // OPTIONAL. Initial input and ongoing work queue. `weft run` seeds this queue from spawn-time input (including piped stdin when provided), and persistent/interactive tasks continue draining it for later stdin/work items.
    },   
    "outputs": {                             // REQUIRED (runtime). Must include at least the "outbox" queue. May include *n* output queues.
       "outbox": "T183...outbox",            // REQUIRED. Result/output queue. Follows naming convention if not provided on task initialization.
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
- Agent tasks execute `spec.agent` against inbox work items using the configured
  runtime adapter. Task lifecycle still lives at the task layer: one-off agent
  tasks are ordinary one-shot tasks, and persistent agent tasks are ordinary
  long-lived tasks that continue draining inbox messages. In the current
  implementation the supported backend is `llm`, tools are Python callables
  only, and agent outputs are written to `outbox` as plain strings or JSON
  objects. If a single work item yields multiple public outputs, the task
  writes multiple outbox messages in order.

**TID format**
- TIDs are SimpleBroker 64-bit hybrid timestamps (microseconds + logical counter). Treat them as opaque, monotonic identifiers that may appear as 19-digit integers in decimal form.

Note: The field is named `keyword_args` (not `kwargs`) due to a technical
constraint in the model layer.

**Spec field groupings (for readability)**
- **Target**: `type`, `function_target`/`process_target`/`agent`, `args`, `keyword_args`
- **Runner**: `runner.name`, `runner.options`
- **Lifecycle**: `persistent`
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
- **Resolved TaskSpec**: The Manager expands templates at spawn time, populating `tid`, `io`, `state`, and `spec.weft_context`, then seeds `io.inputs.inbox` with the initial payload when one was supplied by the caller.
- **TID assignment**: The spawn-request message ID (SimpleBroker 64-bit timestamp) is the task's TID for the full lifecycle.
- **Runner defaulting**: Templates may omit `spec.runner`; resolution defaults it to `{ "name": "host", "options": {} }`.

_Implementation mapping_:
- **Schema & validation**: `weft/core/taskspec.py` — `TaskSpec`, `SpecSection`, `IOSection`, `StateSection`, `LimitsSection`, `RunnerSection`, `AgentSection`, `AgentToolSection`, `AgentTemplateSection`, `ReservedPolicy`.
- **Payload resolution & defaults**: `weft/core/taskspec.py` — `resolve_taskspec_payload()`, `rewrite_tid_in_io()`, `TaskSpec.prepare_payload()` (model_validator mode="before").
- **Target semantics**: `weft/core/targets.py` — `decode_work_message()`, `build_argv()` (command argv construction), `resolve_function_target()`.
- **Runtime execution**: `weft/core/tasks/consumer.py` (`Consumer` — streaming, output handling, reserved policy application), `weft/core/tasks/base.py` (`BaseTask` — queue wiring, state tracking, process titles, reserved policy), `weft/core/tasks/interactive.py` (`InteractiveTaskMixin` — line-oriented interactive command sessions over task-local queues).
- **Resource monitoring**: `weft/core/resource_monitor.py` (`ResourceMonitor` — psutil-based metrics collection), `weft/core/runners/host.py` (`HostTaskRunner` — monitor_class loading).
- **Runner dispatch**: `weft/core/runners/host.py` (`HostTaskRunner`, `HostRunnerPlugin`), `weft/core/tasks/runner.py` (`TaskRunner`), `weft/core/taskspec.py` (`RunnerSection`).
- **Agent runtime**: `weft/ext.py` (agent adapter wiring), `weft/core/taskspec.py` (`AgentSection`, `AgentToolSection`, `AgentTemplateSection`).

_Per-field implementation status_:
- `tid`: Implemented. `TaskSpec.validate_tid()` — 19-digit validation, timestamp bounds.
- `version`: Implemented. `TaskSpec.version` field with regex pattern.
- `name`, `description`: Implemented.
- `spec.type`: Implemented. `SpecSection.type` — Literal["function", "command", "agent"].
- `spec.persistent`: Implemented. `SpecSection.persistent` — used by agent/interactive task paths.
- `spec.function_target`, `spec.process_target`: Implemented. Cross-validated in `SpecSection.validate_target()`.
- `spec.agent`: Implemented. `AgentSection` with full sub-schema (`runtime`, `model`, `instructions`, `templates`, `tools`, `output_mode`, `output_schema`, `max_turns`, `options`, `conversation_scope`, `runtime_config`).
- `spec.args`, `spec.keyword_args`: Implemented. Frozen after creation.
- `spec.timeout`: Implemented. `SpecSection.timeout`.
- `spec.limits.*`: Implemented. `LimitsSection` — `memory_mb`, `cpu_percent`, `max_fds`, `max_connections`. Runtime enforcement in `ResourceMonitor` and `TaskSpec.check_limits()`.
- `spec.env`, `spec.working_dir`: Implemented. Passed to runner in `Consumer.__init__()`.
- `spec.weft_context`: Implemented. Resolved by Manager at spawn time via `resolve_taskspec_payload()`.
- `spec.interactive`: Implemented. `InteractiveTaskMixin` in `weft/core/tasks/interactive.py`.
- `spec.stream_output`: Implemented. One-shot command producers stream incrementally via `weft/core/runners/subprocess_runner.py`, `weft/core/runners/host.py`, and `weft/core/tasks/consumer.py`; non-command results still use the consumer chunking path.
- `spec.cleanup_on_exit`: Implemented. `BaseTask._cleanup_reserved_on_exit()`, `BaseTask._cleanup_spill_dirs()`.
- `spec.reserved_policy_on_stop`, `spec.reserved_policy_on_error`: Implemented. `BaseTask._apply_reserved_policy()`, `Consumer._apply_reserved_policy_on_error()`.
- `spec.polling_interval`, `spec.reporting_interval`: Implemented. `BaseTask._maybe_emit_poll_report()`.
- `spec.monitor_class`: Implemented. Loaded dynamically in `HostTaskRunner` and `Consumer`.
- `spec.enable_process_title`: Implemented. `BaseTask._update_process_title()`.
- `spec.output_size_limit_mb`: Implemented. Disk spill logic in `Consumer`.
- `spec.runner`: Implemented. `RunnerSection` — `name` (default "host"), `options`.
- `io.*`: Implemented. `IOSection` — `inputs`, `outputs`, `control` with required-queue validation.
- `state.*`: Implemented. `StateSection` — all fields including peaks, with consistency validation.
- `metadata`: Implemented. Mutable dict, supports `update_metadata` control messages.

### State vs Metadata ownership [TS-1.4]

Both `state` and `metadata` are mutable at runtime, but they have distinct
ownership:

- **`state` is system-owned.** Weft's task lifecycle writes all `state` fields
  automatically: `status`, `pid`, `started_at`, `completed_at`, `return_code`,
  `error`, resource metrics (`memory`, `cpu`, `fds`, `net_connections`), and
  their peak variants. External code should treat `state` as read-only — it
  reflects what the system observes.

- **`metadata` is caller-owned.** The task creator sets tags, owner identifiers,
  session IDs, and any application-specific key-value pairs. Running tasks and
  external callers can update metadata via `update_metadata` control messages
  over `ctrl_in`. Weft does not interpret metadata contents.

Rule of thumb: if Weft writes it automatically, it belongs in `state`. If the
user or application writes it, it belongs in `metadata`.

_Implementation mapping_: `StateSection` fields are written by
`BaseTask._report_state_change()` (lifecycle events, resource metrics) and
`Consumer._finalize_result()` (return_code, completed_at, final metrics).
`metadata` is a plain mutable dict on `TaskSpec`; it is updated via
`TaskSpec.update_metadata()` at creation time or from ctrl_in messages
(see the `update_metadata` key in the schema comment at line 129 above).

### Runner Selection and Extensibility [TS-1.3]

`spec.type` and `spec.runner` are separate by design.

- `spec.type` declares the task semantics:
  - `function`
  - `command`
  - `agent`
- `spec.runner` declares which execution backend should run that task:
  - built-in `host`
  - installed external runners such as `docker` or `macos-sandbox`
    - current platform note: the first-party `docker` runner is supported on
      Linux and macOS, but not Windows

This prevents execution environment from being overloaded into `spec.type`. A
command task remains a command task regardless of whether it runs on the host,
inside Docker, or in another supported backend.

`spec.runner` contract:

- `spec.runner.name`
  - non-empty string
  - defaults to `"host"`
- `spec.runner.options`
  - runner-specific mapping
  - must remain JSON-serializable so stored TaskSpecs and queue payloads can
    round-trip safely

Validation is layered:

- schema validation checks the shape of `spec.runner`
- capability validation checks whether the selected runner supports the task
  shape (`spec.type`, `interactive`, `persistent`, agent-session needs)
- plugin validation checks runner-specific required options
- preflight checks runtime availability on the current machine

Stored TaskSpecs should remain portable; runtime availability is therefore a
separate validation phase rather than part of baseline schema validation.

_Implementation mapping_: `weft/core/taskspec.py` (`RunnerSection`, `resolve_taskspec_payload()`), `weft/core/runner_validation.py` (`validate_taskspec_runner()`, `validate_runner_capabilities()`, `runner_name_from_taskspec()`), `weft/ext.py` (`RunnerPlugin`, `RunnerHandle`), `weft/_runner_plugins.py` (`get_runner_plugin()`, `require_runner_plugin()`), `weft/core/tasks/runner.py` (`TaskRunner`, `_build_runner_validation_payload()`).

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
- `pipeline`: references a pipeline stored in `.weft/pipelines/`. This is part
  of the intended public surface, but manager-launched pipeline targets remain
  phased work. Current implementation still supports only `task` targets at
  runtime; the manager logs a warning and skips the manifest. See
  [`12-Pipeline_Composition_and_UX.md`](12-Pipeline_Composition_and_UX.md)
  for the pipeline phase order and constraints.

**Defaults**
- `args`/`keyword_args` are merged into the target’s `spec.args`/`spec.keyword_args`.
- `env` is merged into `spec.env`.
- `input` becomes the initial payload (equivalent to `pipeline run --input`).

**Lifecycle policy**
- `mode=once` launches exactly once per manager startup (default).
- `mode=ensure` restarts on unexpected exit with optional backoff while the
  manager is running. Manager shutdown stops autostarted tasks regardless of
  policy.
- `max_restarts` is accepted in manifest policy but is not currently enforced;
  `ensure` mode restarts unconditionally while the manager is running.
- `backoff_seconds` is accepted in manifest policy but restart delay currently
  remains one scan tick rather than an exponential backoff contract.

Managers record metadata linking the running task back to the manifest source
(`metadata.autostart_source`) and set `metadata.autostart=true` for
observability.

_Implementation mapping_: `weft/core/manager.py` — `Manager._tick_autostart()` (scan loop), `Manager._load_autostart_manifest()` (JSON loading), `Manager._load_autostart_taskspec()` (task spec resolution from `.weft/tasks/`), `Manager._build_autostart_spawn_payload()` (defaults merging), `Manager._active_autostart_sources()` (active-source tracking via state log), `Manager._enqueue_autostart_request()` (spawn enqueue). Metadata fields `autostart_source` and `autostart` are set in `Manager._spawn_child()`.

### Reserved queue policies [TS-1.1]

Reserved queue policy semantics are defined in
[05-Message_Flow_and_State.md](05-Message_Flow_and_State.md#8-failure-recovery-flow).
TaskSpec exposes `reserved_policy_on_stop` and `reserved_policy_on_error`, each
accepting `keep` (default), `requeue`, or `clear`.

Timeouts are treated as error exits for reserved-policy purposes:
`reserved_policy_on_error` applies when a task exceeds its timeout, is
killed, or fails with a non-zero exit code.

_Implementation mapping_: `weft/core/tasks/base.py` (`BaseTask._apply_reserved_policy()`), `weft/core/tasks/consumer.py` (`Consumer._apply_reserved_policy_on_error()`, `Consumer._handle_stop()`, `Consumer._handle_kill()`), `weft/core/tasks/interactive.py` (`InteractiveTaskMixin` — applies policies on stop/kill/error for interactive sessions). The `ReservedPolicy` enum lives in `weft/core/taskspec.py`.

## Related Plans

- [`docs/plans/2026-04-06-piped-input-support-plan.md`](../plans/2026-04-06-piped-input-support-plan.md)
- [`docs/plans/2026-04-13-pipeline-spec-expansion-plan.md`](../plans/2026-04-13-pipeline-spec-expansion-plan.md)
- [`docs/plans/2026-04-06-runner-extension-point-plan.md`](../plans/2026-04-06-runner-extension-point-plan.md)
- [`docs/plans/2026-04-13-runner-monitor-result-waiter-and-liveness-fixes-plan.md`](../plans/2026-04-13-runner-monitor-result-waiter-and-liveness-fixes-plan.md)
- [`docs/plans/2026-04-06-taskspec-clean-design-plan.md`](../plans/2026-04-06-taskspec-clean-design-plan.md)
- [`docs/plans/2026-04-06-agent-runtime-implementation-plan.md`](../plans/2026-04-06-agent-runtime-implementation-plan.md)
- [`docs/plans/2026-04-06-persistent-agent-runtime-implementation-plan.md`](../plans/2026-04-06-persistent-agent-runtime-implementation-plan.md)
- [`docs/plans/2026-04-06-agent-runtime-boundary-cleanup-plan.md`](../plans/2026-04-06-agent-runtime-boundary-cleanup-plan.md)

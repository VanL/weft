# Agent Runtime

This document specifies Weft's current agent runtime layer. Deferred agent
runtime surfaces live in
[13A-Agent_Runtime_Planned.md](13A-Agent_Runtime_Planned.md).

The core decision is unchanged: **agents remain Tasks**. Weft owns queues,
durability, process isolation, resource limits, and lifecycle. Agent libraries
remain adapters that run inside those task processes.

## Design Context [AR-0]

Weft started from queues and processes, not from prompts and chat state. The
agent runtime follows that layering:

- **Queue-first**: inbox, reserved, outbox, and control queues remain the
  caller-facing surface.
- **Task-first**: one-off vs persistent behaviour is owned by `spec.persistent`
  and normal task lifecycle code.
- **Adapter-based**: the runtime is selected by `spec.agent.runtime`.
- **Protocol-light public boundary**: callers write strings or JSON objects to
  inbox queues and read strings or JSON objects from outbox queues.
- **Protocol-explicit private boundary**: persistent session workers may use an
  internal protocol between the parent task process and a dedicated runtime
  subprocess.

## Current Support [AR-0.1]

The current implementation supports:

- `spec.type="agent"`
- runtime: `llm`
- tools: `python`
- output modes: `text`, `json`, `messages`
- conversation scopes: `per_message`, `per_task`
- persistent agent tasks through ordinary `spec.persistent=true` task
  semantics

## Conceptual Model [AR-1]

An **Agent Task** is a normal Weft task whose execution target is an agent
runtime adapter.

```text
User / Supervisor
      |
      v
weft.spawn.requests
      |
      v
Manager -> expanded TaskSpec(type="agent") -> Consumer
                                              |
                                              +-> agent runtime adapter
                                              +-> tool resolver
                                              +-> model client
                                              |
                                      T{tid}.inbox / outbox / ctrl_in / ctrl_out
```

Important consequences:

- agent execution still uses inbox -> reserved -> outbox flow
- `STOP`, `PING`, `STATUS`, timeout, and reserved-queue policies still apply
- model-backed work and non-model work still share the same outer runtime

_Implementation mapping:_ The conceptual model is realized through
`weft/core/tasks/consumer.py` (Consumer dispatches agent work via `_run_task`
and `_uses_agent_session`), `weft/core/tasks/runner.py` (TaskRunner serializes
agent config into the worker subprocess), and `weft/core/agent_runtime.py`
(`execute_agent_target` as the runtime entry point).

## TaskSpec Extension [AR-2]

Agent execution is enabled by `spec.type="agent"` plus a required
`spec.agent` section.

```jsonc
{
  "spec": {
    "type": "agent",
    "persistent": false,
    "timeout": 300.0,
    "agent": {
      "runtime": "llm",
      "model": "openai/gpt-4o-mini",
      "instructions": "You are a careful reviewer.",
      "templates": {
        "review_patch": {
          "instructions": "Review the patch carefully.",
          "prompt": "Patch:\n{{ patch }}"
        }
      },
      "tools": [
        {
          "name": "read_file",
          "kind": "python",
          "ref": "mytools.fs:read_file"
        }
      ],
      "output_mode": "text" | "json" | "messages",
      "output_schema": null,
      "max_turns": 20,
      "options": {},
      "conversation_scope": "per_message" | "per_task",
      "runtime_config": {}
    }
  }
}
```

_Implementation mapping:_ `weft/core/taskspec.py` -- `AgentSection` Pydantic
model defines the full `spec.agent` schema. `SpecSection.type` includes
`"agent"` as a valid literal. `SpecSection.agent` field typed as
`AgentSection | None`.

### Field Rules [AR-2.1]

- `spec.agent` is required when `spec.type="agent"`.
- `spec.function_target` and `spec.process_target` must be omitted for agent
  tasks.
- `spec.persistent` controls task lifetime. There is no duplicate lifecycle
  field under `spec.agent`.
- `spec.agent.output_schema` is only valid when
  `spec.agent.output_mode="json"`.
- `spec.agent.conversation_scope="per_task"` requires `spec.persistent=true`.

_Implementation mapping:_ `weft/core/taskspec.py` -- `SpecSection` model
validator `validate_type_targets` enforces mutual exclusion of
`function_target`/`process_target`/`agent`; `AgentSection.validate_output_schema`
enforces the output_schema constraint; `SpecSection` model validator enforces
`conversation_scope="per_task"` requires `persistent=true`.

### Agent Field Semantics [AR-2.2]

- `runtime`: adapter identifier registered in Weft core.
- `model`: runtime-specific model name.
- `instructions`: static system/developer prompt.
- `templates`: named prompt templates declared in the TaskSpec.
- `tools`: static tool descriptors resolved at execution time.
- `output_mode`: caller-facing output shape.
- `output_schema`: optional structured-output schema.
- `max_turns`: maximum model/tool turns before failure.
- `options`: backend/model-specific execution options.
- `conversation_scope`:
  - `per_message`: each work item gets a fresh runtime conversation.
  - `per_task`: a persistent task may retain live in-process conversation
    state across work items.
- `runtime_config`: backend-specific escape hatch.

_Implementation mapping:_ `weft/core/taskspec.py` -- `AgentSection`,
`AgentToolSection`, `AgentTemplateSection` Pydantic models define all fields.
Template rendering: `weft/core/agent_templates.py` (`render_agent_template`).
All fields listed above are implemented.

## Agent Work Envelope [AR-3]

Dynamic agent input comes from the inbox queue.

Supported public inbox payloads:

- plain string
- JSON object containing exactly one of:
  - `task`
  - `messages`
  - `template`

Supported optional fields:

- `template_args`
- `metadata`
- `tool_overrides`

Example:

```json
{
  "messages": [
    {"role": "system", "content": "Focus on queues."},
    {"role": "user", "content": "Review this design."}
  ],
  "metadata": {"request_id": "abc-123"},
  "tool_overrides": {"allow": ["read_file"]}
}
```

_Implementation mapping:_ `weft/core/agent_runtime.py` --
`normalize_agent_work_item` validates supported keys and dispatches to
`_normalize_content_and_instructions`. All four payload forms (plain string,
`task`, `messages`, `template`) are implemented.

### Normalization Rules [AR-3.1]

- A plain string becomes the normalized task content.
- `messages` are preserved as structured messages inside the core runtime
  boundary. Core normalization does **not** flatten them into a single string.
- Template selection is public; template definitions remain static in
  `spec.agent.templates`.
- `tool_overrides` can only narrow the visible tool set for a single work
  item.

_Implementation mapping:_ `weft/core/agent_runtime.py` --
`normalize_agent_work_item`, `_normalize_content_and_instructions`,
`_normalize_messages`, `_normalize_tool_overrides`. Messages are preserved as
`NormalizedAgentMessage` tuples; flattening to a prompt string happens only
inside the `llm` adapter (`LLMBackend._content_to_prompt`).

## Public Output Semantics [AR-4]

The public outbox boundary is intentionally simple:

- write plain strings for text output
- write JSON objects/arrays for structured output
- write one outbox message per public output item

Weft does **not** wrap these in a public `agent_result` envelope.

Example public outbox payloads:

```json
{"role": "assistant", "content": "text:hello"}
```

or:

```text
text:hello
```

_Implementation mapping:_ `weft/core/agent_runtime.py` --
`AgentExecutionResult.aggregate_public_output` produces the caller-facing
result shape. `weft/core/agents/backends/llm_backend.py` --
`LLMBackend._extract_outputs` maps output modes to public payloads. Consumer
writes these directly to the outbox queue.

### Work Item Boundaries [AR-4.1]

Queue consumers do not need to know a new public protocol to detect result
boundaries.

- For one-off tasks, `work_completed` remains the boundary event.
- For persistent tasks, `work_item_completed` is the boundary event for one
  inbox message while the task itself remains `running`.
- `weft result TID` uses those existing task log events to determine when a
  batch of outbox messages for one work item is complete.
- `weft result TID --stream` may render unread outbox stream chunks live, but
  it still stops at those same existing boundary events rather than inventing a
  new public protocol.

This preserves a protocol-light public queue surface while still allowing one
work item to emit multiple public outputs.

_Implementation mapping:_ `weft/core/tasks/consumer.py` -- Consumer handles
both one-off and persistent agent task work-item boundaries through the same
code path used for non-agent tasks. `weft/commands/result.py` aggregates
outbox payloads (no agent-specific handling).

## Runtime Adapter Boundary [AR-5]

The runtime adapter interface is intentionally small:

- normalize public work input
- resolve configured tools
- execute one work item
- optionally start a persistent runtime session

The adapter may return internal execution metadata such as:

- runtime/model identity
- usage
- tool trace
- artifacts

Those details are internal to Weft's execution machinery unless a future public
feature explicitly exposes them.

_Implementation mapping:_ `weft/core/agent_runtime.py` --
`AgentRuntimeAdapter` (Protocol for one-shot `.execute`),
`AgentRuntimeSession` (Protocol for persistent `.execute` + `.close`),
`AgentExecutionResult` (internal result dataclass carrying outputs, usage,
tool_trace, artifacts), `execute_agent_target` (normalize + resolve tools +
dispatch), `start_agent_runtime_session` (persistent session factory).
Tool resolution: `weft/core/agent_tools.py` (`resolve_agent_tools`,
`ResolvedAgentTool`).

## Persistent Session Boundary [AR-6]

Persistent agent tasks with `conversation_scope="per_task"` keep their outer
task lifecycle in normal Weft code, but run the live model conversation in a
dedicated subprocess. This subprocess is always created — there is no in-process
fallback for `per_task` scope.

That parent/subprocess link uses a private JSON protocol. It is allowed to be
explicit and versioned because it is **not** part of the public queue surface.

Public callers do not send or receive:

- `{"type": "execute"}`
- `{"type": "result"}`
- `{"type": "ready"}`

Those are private runtime-session messages only.

_Implementation mapping:_ `weft/core/tasks/agent_session_protocol.py` --
`make_execute_request`, `make_stop_request`, `make_ready_response`,
`make_result_response`, `parse_request_type`, `parse_result_response`,
`is_ready_response`, `startup_error_message`. Versioned via
`AGENT_SESSION_PROTOCOL_VERSION`. Session management:
`weft/core/tasks/sessions.py` (`AgentSession` class),
`weft/core/tasks/consumer.py` (`_uses_agent_session`, `_ensure_agent_session`,
`_shutdown_agent_session`), `weft/core/tasks/runner.py`
(`start_agent_session`).

## Current `llm` Backend [AR-7]

The built-in `llm` adapter currently behaves as follows:

- resolves models through the Python `llm` API
- forwards `instructions` as the system prompt
- supports named templates
- resolves Python tools into `llm.Tool` instances
- forwards `options` into the `llm` call
- supports `per_message` and `per_task` conversation scopes
- supports `text`, `json`, and `messages` public output modes

Backend-specific notes:

- structured `messages` input is preserved by core normalization and converted
  to an `llm` prompt inside the adapter
- `per_task` uses one live `llm` conversation for the life of the persistent
  task process
- conversation state is currently process-local, not restart-durable

_Implementation mapping:_ `weft/core/agents/backends/llm_backend.py` --
`LLMBackend` (one-shot `.execute`, persistent `.start_session`),
`LLMBackendSession` (persistent conversation wrapper). Model resolution:
`_resolve_model`. Tool conversion: `_to_llm_tool`. Output extraction:
`_extract_outputs`. Message flattening: `_content_to_prompt`. Plugin module
registration: `_register_plugin_modules`. Backend is registered via
`weft/core/agents/backends/__init__.py`.

## Non-Goals [AR-8]

This slice does not attempt to:

- replace external agent frameworks with a Weft-native planner stack
- standardize every possible agent-framework feature
- add a second durable state system for conversations
- leak private runtime protocol details into public queues

## Implementation Mapping [AR-9]

- TaskSpec models (`AgentSection`, `AgentToolSection`, `AgentTemplateSection`):
  `weft/core/taskspec.py`
- Runtime normalization, registry, and dispatch:
  `weft/core/agent_runtime.py`
- Templates (`render_agent_template`): `weft/core/agent_templates.py`
- Tool resolution (`resolve_agent_tools`, `ResolvedAgentTool`):
  `weft/core/agent_tools.py`
- Built-in `llm` backend (`LLMBackend`, `LLMBackendSession`):
  `weft/core/agents/backends/llm_backend.py`
- Backend package init and registration: `weft/core/agents/__init__.py`,
  `weft/core/agents/backends/__init__.py`
- Task execution and outbox emission: `weft/core/tasks/consumer.py`
- Persistent session management (`AgentSession`): `weft/core/tasks/sessions.py`
- Persistent runtime subprocess orchestration: `weft/core/tasks/runner.py`
- Private session protocol: `weft/core/tasks/agent_session_protocol.py`
- CLI result aggregation: `weft/commands/run.py`, `weft/commands/result.py`

## Related Plans

- [`docs/plans/result-stream-implementation-plan.md`](../plans/result-stream-implementation-plan.md)

## Related Documents

- [13A-Agent_Runtime_Planned.md](13A-Agent_Runtime_Planned.md)
- [10-CLI_Interface.md](10-CLI_Interface.md)
- [02-TaskSpec.md](02-TaskSpec.md)

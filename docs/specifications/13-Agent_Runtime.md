# Agent Runtime

This document specifies Weft's current agent runtime layer. Deferred substrate
surfaces live in [13A-Agent_Runtime_Planned.md](13A-Agent_Runtime_Planned.md).
Exploratory patterns for using Weft inside larger agent systems live in
[13B-Using_Weft_In_Higher_Level_Systems.md](13B-Using_Weft_In_Higher_Level_Systems.md).

The core decision is unchanged: **agents remain Tasks**. Weft owns queues,
durability, process isolation, resource limits, and lifecycle. Agent libraries
remain adapters that run inside those task processes.

The agent runtime exists to let agents participate in the Weft task model. It
does not turn Weft into a general agent harness. Runtime-specific knowledge is
acceptable when it directly serves execution, isolation, observability, or
clear failure. Ecosystem management belongs above this layer.

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
- runtime: `provider_cli` for host-backed delegated CLI execution through
  `claude_code`, `codex`, `gemini`, `opencode`, and `qwen`
- runner-specific delegated lane: Docker-backed one-shot `provider_cli`
  execution when `spec.runner.name="docker"`,
  `spec.agent.conversation_scope="per_message"`, `spec.persistent=false`, and
  the selected provider has an explicit image recipe; the current shipped
  image-recipe set is `claude_code`, `codex`, `gemini`, `opencode`, and
  `qwen`
- authority classes:
  - `llm`: `bounded`
  - `provider_cli`: explicit `bounded` or `general`, with missing values
    resolving to `general` for compatibility
- tools: `python` for `llm`; `provider_cli` rejects `spec.agent.tools` in
  Phase 1
- output modes:
  - `llm`: `text`, `json`, `messages`
  - `provider_cli`: `text` only
- conversation scopes:
  - `llm`: `per_message`, `per_task`
  - `provider_cli`: `per_message`, `per_task`
- persistent agent tasks through ordinary `spec.persistent=true` task
  semantics for runtimes that support them; `provider_cli` allows
  task-lifetime persistence only for
  `spec.agent.conversation_scope="per_task"` on the host-backed lane; the
  Docker-backed lane is one-shot only
- delegated tool-profile refs through
  `spec.agent.runtime_config.tool_profile_ref`
- structured delegated tool policy for `provider_cli`:
  `workspace_access` plus stdio MCP server descriptors

Weft owns only the coarse authority declaration at this layer. `llm` is the
bounded lane. `provider_cli` is the delegated lane. Tool profiles and runner
isolation may narrow authority inside the chosen lane, but Weft does not own
the full inner policy of the external provider CLIs.

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
agent config into the worker subprocess), and `weft/core/agents/runtime.py`
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
      "authority_class": "bounded",
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
- `spec.agent.authority_class` is optional:
  - `llm` resolves missing values to `bounded`
  - `provider_cli` resolves missing values to `general` for compatibility
- `spec.agent.output_schema` is only valid when
  `spec.agent.output_mode="json"`.
- `spec.agent.conversation_scope="per_task"` requires `spec.persistent=true`.
- `llm` only supports `authority_class="bounded"`.
- `provider_cli` requires `spec.agent.runtime_config.provider`.
- `provider_cli` supports `authority_class="bounded"` only for providers that
  can actually enforce a bounded mode. In the current built-in set that means
  `claude_code`, `codex`, `gemini`, and `qwen`. `opencode` is currently
  `general` only.
- `provider_cli` only supports `spec.agent.output_mode="text"`.
- `provider_cli` rejects `spec.agent.tools` in Phase 2.
- `provider_cli` supports:
  - one-shot execution with `conversation_scope="per_message"`
  - persistent task-lifetime sessions only for
    `spec.persistent=true` plus `conversation_scope="per_task"`
- `provider_cli` only accepts `spec.agent.model` for providers whose concrete
  non-interactive CLI surface supports model override. In the current built-in
  provider set, that means `claude_code`, `codex`, `gemini`, `opencode`, and
  `qwen`.
- `provider_cli` structured tool policy is explicit and provider-limited:
  - `workspace_access="read-only"` is supported by `claude_code`, `codex`,
    `gemini`, and `qwen`
  - `workspace_access="workspace-write"` is supported by `codex`
  - explicit stdio MCP server descriptors are currently supported by
    `claude_code` only

_Implementation mapping:_ `weft/core/taskspec.py` -- `SpecSection` model
validator `validate_type_targets` enforces mutual exclusion of
`function_target`/`process_target`/`agent`; `AgentSection.validate_runtime_constraints`
enforces runtime-specific schema constraints; `SpecSection` model validator
enforces
`conversation_scope="per_task"` requires `persistent=true` and restricts
`provider_cli` persistence to that shape. Provider-specific runtime validation:
`weft/core/agents/validation.py`,
`weft/core/agents/provider_cli/registry.py`,
`weft/core/agents/backends/provider_cli.py`.

### Agent Field Semantics [AR-2.2]

- `runtime`: adapter identifier registered in Weft core.
- `authority_class`: coarse Weft-owned authority declaration. This is a
  substrate contract, not a full provider-policy model.
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
Template rendering: `weft/core/agents/templates.py`
(`render_agent_template`).
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

_Implementation mapping:_ `weft/core/agents/runtime.py` --
`normalize_agent_work_item` validates supported keys and dispatches to
`_normalize_content_and_instructions`. All four payload forms (plain string,
`task`, `messages`, `template`) are implemented.

Submission note:

- `weft run --spec` may optionally use `spec.parameterization` to materialize a
  concrete TaskSpec template locally before queueing the spawn request
- `weft run --spec` may optionally use `spec.run_input` to shape declared long
  options and optional piped stdin from that materialized spec into one of
  these same public work envelopes before queueing the spawn request
- once the task is queued, the public agent inbox contract stays exactly the
  same; the runtime never sees a second submission path

### Normalization Rules [AR-3.1]

- A plain string becomes the normalized task content.
- `messages` are preserved as structured messages inside the core runtime
  boundary. Core normalization does **not** flatten them into a single string.
- Template selection is public; template definitions remain static in
  `spec.agent.templates`.
- `tool_overrides` can only narrow the visible tool set for a single work
  item.

_Implementation mapping:_ `weft/core/agents/runtime.py` --
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

_Implementation mapping:_ `weft/core/agents/runtime.py` --
`AgentExecutionResult.aggregate_public_output` produces the caller-facing
result shape. `weft/core/agents/backends/llm.py` --
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

_Implementation mapping:_ `weft/core/agents/runtime.py` --
`AgentRuntimeAdapter` (Protocol for one-shot `.execute`),
`AgentRuntimeSession` (Protocol for persistent `.execute` + `.close`),
`AgentExecutionResult` (internal result dataclass carrying outputs, usage,
tool_trace, artifacts), `execute_agent_target` (normalize + resolve tools +
dispatch), `start_agent_runtime_session` (persistent session factory).
Tool resolution: `weft/core/agents/tools.py` (`resolve_agent_tools`,
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

## Current Backends [AR-7]

### `llm` backend

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

_Implementation mapping:_ `weft/core/agents/backends/llm.py` --
`LLMBackend` (one-shot `.execute`, persistent `.start_session`),
`LLMBackendSession` (persistent conversation wrapper). Model resolution:
`_resolve_model`. Tool conversion: `_to_llm_tool`. Output extraction:
`_extract_outputs`. Message flattening: `_content_to_prompt`. Plugin module
registration: `_register_plugin_modules`. Backend is registered via
`weft/core/agents/backends/__init__.py`.

### `provider_cli` backend

The built-in `provider_cli` adapter currently behaves as follows:

- supports host-backed one-shot delegated execution
- supports Docker-backed one-shot delegated execution when
  `spec.runner.name="docker"` and the selected provider has both an explicit
  image recipe and an internal provider container runtime descriptor
- supports persistent task-lifetime delegated sessions on the host-backed lane
  when `spec.persistent=true` and `conversation_scope="per_task"`
- supports explicit provider selection through
  `spec.agent.runtime_config.provider`
- supports explicit coarse authority selection through
  `spec.agent.authority_class`
- supports explicit resolver and tool-profile refs through
  `spec.agent.runtime_config.resolver_ref` and
  `spec.agent.runtime_config.tool_profile_ref`
- when an agent TaskSpec is loaded from a bundle directory, those Python
  callable refs and any Python agent tool refs keep the normal
  `module:function` syntax but resolve `module` against the bundle root first
  before falling back to normal Python imports
- supports structured delegated tool-profile fields for `workspace_access` and
  stdio MCP server descriptors
- currently ships provider adapters for `claude_code`, `codex`, `gemini`,
  `opencode`, and `qwen`
- current shipped Docker image-recipe support is explicit; today that set is
  `claude_code`, `codex`, `gemini`, `opencode`, and `qwen`
- current shipped Docker container runtime descriptor support is also explicit;
  today that set is `claude_code`, `codex`, `gemini`, `opencode`, and `qwen`
- keeps the public outbox contract unchanged by returning plain text only
- persists internal runtime metadata and referenced artifacts through normal
  task-log events rather than a new public result envelope

Backend-specific notes:

- the built-in provider names and non-interactive dispatch strings intentionally
  follow the concrete provider surfaces already used in `agent-mcp`
- `spec.agent.model` is currently forwarded for `claude_code`, `codex`,
  `gemini`, `opencode`, and `qwen`
- Docker-backed provider runtime defaults are explicit and provider-specific:
  - `codex`: optional writable `~/.codex` mount, and Docker-lane examples use
    explicit `sandbox="danger-full-access"` because the container is the outer
    boundary
  - `gemini`: optional `GEMINI_API_KEY` forwarding plus an isolated runtime
    home seeded from a small subset of `~/.gemini`
  - `opencode`: allowlisted provider env forwarding; explicit
    `spec.agent.model` is recommended when you want deterministic backend
    choice instead of host default-model drift
  - `qwen`: optional writable `~/.qwen` mount
  - `claude_code`: optional writable `~/.claude` and `~/.claude.json` mounts
    plus portable auth env forwarding. On macOS, a host `claude.ai` login is
    stored in Keychain and is not portable into the Linux container by mount
    alone
- `authority_class="bounded"` currently means Weft enforces the strongest
  deterministic narrowing it knows how to express for that provider. It does
  not mean Weft has taken over the provider's full inner safety policy
- delegated tool-profile validation is split from agent-runtime validation so
  CLI and execution surfaces can report environment-profile, runner,
  agent-runtime, and tool-profile failures separately
- delegated startup validation remains static. It may resolve executable paths
  and project-local launch defaults, but it does not spawn provider CLIs just
  to prove health
- for the Docker-backed one-shot lane specifically, ahead-of-time validation
  also skips host provider-executable checks. The important static checks there
  are Docker availability plus the provider's image-recipe and runtime-
  descriptor contract; the actual provider behavior is proven only on real
  container start
- ordinary `weft run` submission does not add a separate speculative "does this
  provider CLI run here" gate either; the real delegated startup is attempted
  on the normal execution path, and failures surface from that real start
- raw `spec.agent.options` may tune provider behavior, but they may not
  silently widen authority past the selected authority class or the resolved
  tool profile
- actual provider health, login state, and command-surface compatibility are
  checked only when Weft opens a delegated session or runs a delegated call;
  `opencode` `run` support is therefore surfaced on first real use, not during
  preflight
- `workspace_access` support is explicit per provider:
  - `claude_code`: `read-only`
  - `codex`: `read-only`, `workspace-write`
  - `gemini`: `read-only`
  - `qwen`: `read-only`
  - `opencode`: none in the current built-in adapter
- explicit stdio MCP server descriptors are currently translated only for
  `claude_code`, using temporary `--mcp-config` JSON plus
  `--strict-mcp-config`
- when `authority_class="bounded"` and no explicit Claude MCP servers are
  declared, Weft supplies an empty strict MCP config rather than inheriting
  ambient local MCP state
- delegated provider executable defaults may come from user-authored
  `.weft/agents.json` project settings when the TaskSpec does not pin an
  executable explicitly
- observed delegated provider status may be recorded in
  `.weft/agent-health.json` for UX and diagnostics, but that cache is advisory
  only and never gates startup correctness
- synthetic provider probes may still exist in isolated helpers or explicit
  diagnostics flows, but they are not part of the ordinary startup-validation
  path
- Weft currently ships that explicit diagnostics shape as a builtin
  `probe-agents` task helper rather than as hidden startup work
- Weft also ships an explicit optional `prepare-agent-images` builtin helper to
  warm the Docker image cache for providers with shipped image recipes; it is a
  cache warmer, not a required setup step, and ordinary Docker-backed runs
  reuse those same deterministic tags when present
- persistent `provider_cli` sessions stay on the existing
  `Consumer -> AgentSession -> host session worker -> provider_cli` spine
- provider-native continuation handles stay private runtime state; the public
  session identifier is still the persistent Weft task TID
- host-backed provider execution still happens inside the normal host-runner
  worker subprocess
- Docker-backed one-shot provider execution runs inside a fresh container
  created by the Docker runner, but the outer task lifecycle still stays on the
  existing `TaskSpec -> Manager -> Consumer -> TaskRunner -> runner plugin ->
  queues/state log` spine
- Docker-backed provider runtime defaults such as allowlisted host env
  forwarding and optional config mounts are owned by internal
  provider container runtime descriptors in core `provider_cli` code, not by
  Docker image recipes and not by user-authored `TaskSpec` schema
- Docker-backed agent tasks may also use Docker-specific
  `spec.runner.options.work_item_mounts` to resolve absolute host paths from
  the raw work item at execution time and bind-mount them into fixed container
  targets; this is a Docker runner feature, not a generic cross-runner Weft
  abstraction
- in both lanes, timeout, cancellation, reserved-queue policy, and outbox
  writes stay on the existing durable spine

Example TaskSpec shape for the Docker-backed one-shot lane:

```jsonc
{
  "spec": {
    "type": "agent",
    "persistent": false,
    "agent": {
      "runtime": "provider_cli",
      "authority_class": "general",
      "conversation_scope": "per_message",
      "runtime_config": {
        "provider": "codex"
      }
    },
    "working_dir": "/host/project",
    "runner": {
      "name": "docker",
      "options": {
        "container_workdir": "/workspace",
        "mounts": [
          {
            "source": "/host/project",
            "target": "/workspace",
            "read_only": false
          }
        ]
      }
    }
  }
}
```

In this lane, the image is provider-scoped and reusable, while mounts, runtime
env, working directory, and network policy remain per-run container inputs.
That preserves the Weft task contract while keeping Docker-specific variation
at the runner boundary. The current shipped provider runtime descriptors are:

- `codex`: optional writable `~/.codex` at `/root/.codex`
- `gemini`: optional `GEMINI_API_KEY` forwarding plus a generated runtime home
  at `/tmp/weft-provider-home` seeded from a small subset of `~/.gemini`
- `opencode`: allowlisted provider env forwarding for keys such as
  `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY`, `GITHUB_TOKEN`,
  and `XAI_API_KEY`
- `qwen`: optional writable `~/.qwen` at `/root/.qwen`
- `claude_code`: optional writable `~/.claude` and `~/.claude.json` mounts
  plus portable auth env forwarding

On macOS specifically, Docker-backed `claude_code` does not inherit a host
`claude.ai` login by mount alone because that auth lives in the macOS
Keychain. Tasks that want Docker-backed Claude should therefore use explicit
portable auth such as `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_AUTH_TOKEN`,
`ANTHROPIC_API_KEY`, or a Linux `~/.claude/.credentials.json`.

_Implementation mapping:_ Resolver and tool-profile loading:
`weft/core/agents/resolution.py`. Agent-runtime and delegated tool-profile
load and preflight validation: `weft/core/agents/validation.py`. Provider
registry, provider-specific invocation building, and output parsing:
`weft/core/agents/provider_cli/registry.py`. Internal provider container
runtime descriptors and resolution:
`weft/core/agents/provider_cli/container_runtime.py`. Shared one-shot delegated
execution preparation: `weft/core/agents/provider_cli/execution.py`.
Project-local delegated launch settings and advisory health cache:
`weft/core/agents/provider_cli/settings.py`. Optional synthetic delegated probe
helpers: `weft/core/agents/provider_cli/probes.py`. One-shot and persistent
runtime adapter: `weft/core/agents/backends/provider_cli.py`.
Docker-backed one-shot runner and image helpers:
`extensions/weft_docker/weft_docker/plugin.py`,
`extensions/weft_docker/weft_docker/agent_runner.py`,
`extensions/weft_docker/weft_docker/agent_images.py`,
`extensions/weft_docker/weft_docker/images.py`. Explicit cache-warming builtin:
`weft/builtins/agent_images.py`. Backend registration:
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
  `weft/core/agents/runtime.py`
- Resolver and tool-profile loading: `weft/core/agents/resolution.py`
- Agent-runtime and delegated tool-profile validation:
  `weft/core/agents/validation.py`
- Templates (`render_agent_template`): `weft/core/agents/templates.py`
- Tool resolution (`resolve_agent_tools`, `ResolvedAgentTool`):
  `weft/core/agents/tools.py`
- Built-in `llm` backend (`LLMBackend`, `LLMBackendSession`):
  `weft/core/agents/backends/llm.py`
- Built-in `provider_cli` backend, provider registry, and shared one-shot
  execution helpers:
  `weft/core/agents/backends/provider_cli.py`,
  `weft/core/agents/provider_cli/registry.py`,
  `weft/core/agents/provider_cli/container_runtime.py`,
  `weft/core/agents/provider_cli/execution.py`
- Project-local delegated settings and advisory provider-health metadata:
  `weft/core/agents/provider_cli/settings.py`
- Optional synthetic delegated probe helpers:
  `weft/core/agents/provider_cli/probes.py`
- Backend package init and registration: `weft/core/agents/__init__.py`,
  `weft/core/agents/backends/__init__.py`
- Task execution and outbox emission: `weft/core/tasks/consumer.py`
- Persistent session management (`AgentSession`): `weft/core/tasks/sessions.py`
- Persistent runtime subprocess orchestration: `weft/core/tasks/runner.py`
- Private session protocol: `weft/core/tasks/agent_session_protocol.py`
- CLI result aggregation: `weft/commands/run.py`, `weft/commands/result.py`

## Related Plans

- [`docs/plans/2026-04-14-agent-runtime-package-refactor-plan.md`](../plans/2026-04-14-agent-runtime-package-refactor-plan.md)
- [`docs/plans/2026-04-13-result-stream-implementation-plan.md`](../plans/2026-04-13-result-stream-implementation-plan.md)
- [`docs/plans/2026-04-13-delegated-agent-runtime-phase-1-implementation-plan.md`](../plans/2026-04-13-delegated-agent-runtime-phase-1-implementation-plan.md)
- [`docs/plans/2026-04-13-delegated-agent-authority-boundary-cleanup-plan.md`](../plans/2026-04-13-delegated-agent-authority-boundary-cleanup-plan.md)
- [`docs/plans/2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md`](../plans/2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md)
- [`docs/plans/2026-04-14-docker-agent-images-and-one-shot-provider-cli-plan.md`](../plans/2026-04-14-docker-agent-images-and-one-shot-provider-cli-plan.md)
- [`docs/plans/2026-04-14-provider-cli-container-runtime-descriptor-plan.md`](../plans/2026-04-14-provider-cli-container-runtime-descriptor-plan.md)
- [`docs/plans/2026-04-14-weft-road-to-excellent-plan.md`](../plans/2026-04-14-weft-road-to-excellent-plan.md)
- [`docs/plans/2026-04-15-spec-run-input-adapter-and-declared-args-plan.md`](../plans/2026-04-15-spec-run-input-adapter-and-declared-args-plan.md)
- [`docs/plans/2026-04-15-multi-provider-docker-provider-cli-expansion-plan.md`](../plans/2026-04-15-multi-provider-docker-provider-cli-expansion-plan.md)

## Related Documents

- [13A-Agent_Runtime_Planned.md](13A-Agent_Runtime_Planned.md)
- [10-CLI_Interface.md](10-CLI_Interface.md)
- [10B-Builtin_TaskSpecs.md](10B-Builtin_TaskSpecs.md)
- [02-TaskSpec.md](02-TaskSpec.md)

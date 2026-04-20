# Weft System Overview and Architecture

Weft is a durable task runner built on SimpleBroker. The design target is not
massive distributed orchestration. The target is a small, reliable, observable
process system that feels as direct as Unix tools while surviving beyond a
single shell session.

_Implementation mapping_: CLI adapters in `weft/cli/`; shared capability code
in `weft/commands/`; public Python client in `weft/client/`; context
resolution in `weft/context.py`; manager runtime in `weft/core/manager.py`,
`weft/core/manager_runtime.py`, and `weft/manager_process.py`; task runtime
in `weft/core/tasks/`; runner plugin contracts and resolution in
`weft/_runner_plugins.py` and `weft/ext.py`; built-in runner implementations in
`weft/core/runners/`.

See also:

- planned companion:
  [`00A-Overview_and_Architecture_Planned.md`](00A-Overview_and_Architecture_Planned.md)
- pipeline status and composition contract:
  [`12-Pipeline_Composition_and_UX.md`](12-Pipeline_Composition_and_UX.md)

## Executive Summary

The current system is organized around four ideas:

- queues are the only durable coordination surface
- the manager is a task-shaped dispatcher, not a separate control plane
- all externally visible work stays inspectable through stable CLI verbs
- operator trust comes from visibility, not hidden automation

That shape is deliberate. It keeps the system easy to inspect with normal queue
and process tools, and it avoids a second hidden state store that would drift
from runtime truth.

## Core Mission

Weft exists to coordinate commands, Python callables, and agent-backed work
that may take milliseconds or minutes, while preserving:

- durable submission
- observable lifecycle
- explicit control
- backend-neutral broker targeting

The goal is not "workflow engine first." The goal is "durable task/process
coordination first." Higher-order composition is allowed, but it should still
look and behave like tasks rather than like a separate platform.

That boundary also applies to agent work. Weft is the execution substrate for
higher-level intelligence and orchestration, not the agent-management layer
itself. The system should make execution easy, observable, and explicitly
validatable without quietly taking ownership of higher-level orchestration
policy.

## Product Identity

Weft owns the runtime substrate:

- task lifecycle
- queue-visible state
- manager dispatch
- runner selection and isolation
- resource limits
- task-shaped composition
- agent execution as one more task target

This means agent support and limited runners are not foreign to Weft. They are
core expressions of the same design. An agent is a task. A dangerous task in a
restricted container is still a task. Runner plugins and agent adapters extend
the same execution model rather than creating a second product.

## Product Boundary

What fits in Weft:

- agents as tasks
- limited runners such as Docker or sandboxed runtimes
- explicit composition over tasks
- enough runtime-specific knowledge to make common runs work or fail clearly
- explicit optional helpers that smooth a real execution path without becoming
  required for correctness

What does not fit in Weft:

- hidden setup or probing on ordinary execution paths
- turning Weft into the source of truth for provider ecosystem state
- broad agent-specific management logic that is not needed to execute a task
- convenience features that silently author project policy
- a second control plane for agent setup, health, or lifecycle outside the task
  model

The rule is simple: Weft may help you run work. It should not try to manage the
whole world around that work.

## Why SimpleBroker

SimpleBroker fits Weft because it already provides the persistence and queue
primitives Weft needs:

- queues appear on first use
- empty queues disappear again
- message IDs are durable, ordered, and unique
- the same broker abstraction works across file-backed and non-file-backed
  backends

That matters because Weft should spend its complexity budget on process
lifecycle, task semantics, and observability, not on rebuilding a broker.

## Design Principles

- **Queue-first**: caller-facing coordination goes through durable queues.
- **Task-shaped runtime**: managers and user work share the same outer task
  model.
- **Stable verbs**: `run`, `status`, `result`, `list`, `stop`, and `kill`
  remain the center of gravity even as the system grows.
- **Partial immutability**: execution config is frozen after TaskSpec creation;
  runtime state stays mutable.
- **Forward-only state**: lifecycle state is append-only and terminal states do
  not roll back.
- **Visible failure**: failures stay inspectable through state logs and reserved
  queues.
- **Backend-neutral targeting**: normal runtime paths operate on broker targets,
  not on SQLite-only path assumptions.
- **Operator intent over magic**: background behavior such as autostart exists
  only when the project explicitly configures it.

## Current Product Shape

The current public surface is:

- task and pipeline execution through `weft run` for commands, Python
  callables, TaskSpecs, and pipelines
- stored task specs and stored pipeline specs
- shipped builtin task helpers resolved only through explicit spec surfaces
  such as `weft run --spec`, `weft spec ...`, and `weft system builtins`
- pipeline execution through `weft run --pipeline`
- manager lifecycle commands
- queue passthrough commands
- result, status, and task-control commands
- first-class agent tasks through `spec.type="agent"`
- first-party restricted runners, including the Docker runner's current
  command lane and its narrow one-shot Docker-backed `provider_cli` agent lane

The reason pipelines live under `weft run --pipeline` rather than under a
separate top-level verb is architectural: composition is part of task
execution, not a second workflow language.

## System Architecture

### Runtime Layers

```text
CLI and client adapters
        |
        v
Shared command/client capabilities and context resolution
        |
        v
Manager dispatcher and task launch
        |
        v
Task runtime and runner plugins
        |
        v
SimpleBroker queues and broker target
```

_Implementation mapping per layer_:

- **CLI and client adapters**: `weft/cli/`, `weft/client/`
- **Shared application capabilities**: `weft/commands/`
- **Context resolution**: `weft/context.py`
- **Manager dispatcher**: `weft/core/manager.py`,
  `weft/core/manager_runtime.py`, `weft/manager_process.py`
- **Task runtime**: `weft/core/tasks/base.py`,
  `weft/core/tasks/consumer.py`, `weft/core/tasks/runner.py`,
  `weft/core/tasks/sessions.py`
- **Runner plugins**: `weft/core/runners/`, `weft/_runner_plugins.py`,
  `weft/ext.py`
- **Queues and persistence**: SimpleBroker broker target and queue API

### Manager Model

The manager is the dispatcher for spawn requests. It watches
`weft.spawn.requests`, expands a runtime TaskSpec, and launches child work.

The important part is the shape, not a class taxonomy:

- there is one canonical manager runtime per context
- the manager uses the same queue and control conventions as task work
- the spawn-request message ID becomes the child task TID
- startup, foreground serve, and lifecycle observation share one manager
  bootstrap path

This is why the architecture talks about "managers are tasks" rather than about
separate scheduler or pool objects. The outer lifecycle is intentionally
uniform.

## Observability and Security

Weft assumes user-level trust, not hostile multi-tenancy. The security model is
therefore mostly about explicitness and containment:

- all significant lifecycle changes are written to `weft.log.tasks`
- process titles expose enough identity for normal OS tooling
- reserved queues keep failed in-flight work visible
- broker/project context remains explicit so operators know which runtime they
  are touching

The reason this matters is operational clarity. When something fails, the
system should leave behind enough durable evidence that the operator can answer
what happened without guessing.

## Scope Boundaries

The canonical architecture spec covers what Weft does now. Planned extensions,
aspirational performance targets, and future orchestration layers live in the
adjacent companion doc:

- [`00A-Overview_and_Architecture_Planned.md`](00A-Overview_and_Architecture_Planned.md)

That split is deliberate. The canonical file answers "what exists and why."
The companion file answers "what may exist later."

## Related Documents

- [`01-Core_Components.md`](01-Core_Components.md)
- [`00A-Overview_and_Architecture_Planned.md`](00A-Overview_and_Architecture_Planned.md)
- [`03-Manager_Architecture.md`](03-Manager_Architecture.md)
- [`04-SimpleBroker_Integration.md`](04-SimpleBroker_Integration.md)
- [`10-CLI_Interface.md`](10-CLI_Interface.md)
- [`12-Pipeline_Composition_and_UX.md`](12-Pipeline_Composition_and_UX.md)
- [`13-Agent_Runtime.md`](13-Agent_Runtime.md)

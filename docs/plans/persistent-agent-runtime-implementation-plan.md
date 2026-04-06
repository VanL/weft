# Persistent Agent Runtime Implementation Plan

This document is the implementation plan for the **next** agent-runtime slice:
correcting the TaskSpec shape, supporting explicit `llm` arguments cleanly, and
adding **persistent agent tasks with continuation** without duplicating
lifecycle semantics inside `spec.agent`.

This plan supersedes the previous prototype-oriented plan in
[`docs/plans/agent-runtime-implementation-plan.md`](./agent-runtime-implementation-plan.md)
for all work related to persistent agents.

It is written for an engineer who is strong in Python but has **almost no
context** for Weft, SimpleBroker, or the project’s architectural opinions.

Read this plan as a set of **bite-sized tasks**. Each task names:

- what to read first,
- which files to touch,
- which tests to write first,
- which invariants to protect,
- and which verification commands to run.

The likely failure modes are:

- re-introducing agent-specific lifecycle fields,
- hiding persistent conversation state in the wrong process,
- over-mocking instead of testing real queues and real subprocesses,
- adding a large templating system for a small prompt-substitution need,
- and quietly accepting unsupported fields instead of failing loudly.

Do not do those things.

## 0. Scope Lock

This plan implements the **minimum correct slice** of persistent agent support:

- correct the TaskSpec shape so lifecycle is **task-level**, not `spec.agent`-level,
- support explicit `llm` arguments cleanly:
  - model name,
  - system prompt via `instructions`,
  - tool descriptors,
  - prompt templates,
  - backend options,
- support one-shot and persistent agent tasks using the **same generic task
  lifecycle semantics**,
- support `conversation_scope="per_task"` for `llm` so follow-up work items in
  the same persistent task continue the same conversation,
- keep process isolation, timeout handling, and resource monitoring intact,
- keep result delivery queue-native via the existing outbox/result path,
- keep the Manager/Consumer/TaskRunner architecture instead of inventing a
  parallel orchestration system.

This plan intentionally does **not** implement:

- `pydantic_ai`,
- approvals over `ctrl_in` / `ctrl_out`,
- semantic event streaming to outbox during execution,
- transcript persistence,
- attachment support,
- command/mcp/agent/queue tools,
- a Jinja2 dependency or any non-trivial template engine,
- a new conversation database or external state store,
- a second orchestration plane that bypasses Weft queues.

This plan assumes a **clean target shape**.

- Do not add aliases.
- Do not add translation layers.
- Do not keep dead branches “just in case.”

If implementation pressure starts pulling toward any of the excluded items
above, stop. That is scope drift away from the agreed design.

## 1. Read This First

Before touching code, read these in this order:

1. [`AGENTS.md`](../../AGENTS.md)
2. [`docs/specifications/00-Overview_and_Architecture.md`](../specifications/00-Overview_and_Architecture.md)
3. [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md)
4. [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
5. [`docs/specifications/03-Worker_Architecture.md`](../specifications/03-Worker_Architecture.md)
6. [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
7. [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
8. [`docs/specifications/08-Testing_Strategy.md`](../specifications/08-Testing_Strategy.md)
9. [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
10. [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
11. [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py)
12. [`tests/conftest.py`](../../tests/conftest.py)
13. [`weft/core/taskspec.py`](../../weft/core/taskspec.py)
14. [`weft/core/agent_runtime.py`](../../weft/core/agent_runtime.py)
15. [`weft/core/agent_tools.py`](../../weft/core/agent_tools.py)
16. [`weft/core/agents/backends/llm_backend.py`](../../weft/core/agents/backends/llm_backend.py)
17. [`weft/core/tasks/consumer.py`](../../weft/core/tasks/consumer.py)
18. [`weft/core/tasks/runner.py`](../../weft/core/tasks/runner.py)
19. [`weft/core/tasks/interactive.py`](../../weft/core/tasks/interactive.py)
20. [`weft/core/tasks/sessions.py`](../../weft/core/tasks/sessions.py)
21. [`weft/core/launcher.py`](../../weft/core/launcher.py)
22. [`weft/core/manager.py`](../../weft/core/manager.py)
23. [`weft/commands/run.py`](../../weft/commands/run.py)
24. [`weft/commands/result.py`](../../weft/commands/result.py)
25. [`tests/fixtures/llm_test_models.py`](../../tests/fixtures/llm_test_models.py)

Also inspect SimpleBroker style before writing new modules:

1. `/Users/van/.venv/lib/python3.13/site-packages/simplebroker/sbqueue.py`
2. `/Users/van/.venv/lib/python3.13/site-packages/simplebroker/watcher.py`
3. `/Users/van/.venv/lib/python3.13/site-packages/simplebroker/helpers.py`

You are not copying SimpleBroker code. You are learning the style:

- small modules,
- direct names,
- minimal magic,
- real cleanup,
- real SQLite-backed tests.

## 2. Engineering Rules

These rules are not optional.

### 2.1 Style

- Use `from __future__ import annotations` in every new Python file.
- Keep imports at module top. No deferred imports.
- Use `collections.abc` types, not legacy `typing.List` / `typing.Dict`.
- Use `Path`, not `os.path`.
- Keep modules single-purpose.
- Keep names boring and explicit.
- Add docstrings with spec references where behavior is non-trivial.
- Add comments only when the code would otherwise be hard to parse.

### 2.2 Complexity

- Do not introduce a second orchestration model.
- Do not build a generic “agent platform.”
- Do not create an event bus.
- Do not add hidden caches or hidden memory stores.
- Do not over-generalize for backends that do not exist yet.
- Split helpers before branch pyramids get ugly.

### 2.3 DRY and YAGNI

- Be DRY where duplication is real and harmful.
- Be YAGNI where the duplication is hypothetical.
- Reuse the existing Manager/Consumer/TaskRunner path wherever that path still
  preserves the required semantics.
- Do not add `pydantic_ai` “while you are here.”
- Do not add Jinja2 “because templates might grow.”

### 2.4 Testing

- Prefer red-green-refactor TDD for every task below.
- Do not add mocks, fake queues, or patch-heavy tests.
- Use real `Queue` instances, real task processes, and `WeftTestHarness`.
- Write both white-box and black-box tests.
- For deterministic `llm` coverage, use a real local `llm.Model` subclass
  registered through `llm`’s actual pluggy hooks.
- Do not patch `llm.get_model`.
- Do not mock `simplebroker.Queue`.
- Do not mock state transitions.
- When a test needs to observe asynchronous behavior, add a harness helper or
  poll a real queue/log condition. Do **not** add `sleep()` and hope.

### 2.5 Verification Discipline

- Run `ruff`, `mypy`, and `pytest` **sequentially**, not in parallel.
- This repository uses `uv`, and running multiple `uv run ...` commands in
  parallel can recreate the shared virtualenv mid-run and cause false failures.

## 3. Architecture Decisions Already Made

These decisions are fixed for this plan.

### 3.1 Lifecycle Is Task-Level

Do **not** add `spec.agent.mode`.

One-off vs persistent behavior belongs to the task layer. For this plan, make
that explicit with a **generic task-level field**:

- `spec.persistent: bool = False`

That field applies to all task types, not just agents.

Reason:

- the user explicitly rejected duplicating lifecycle semantics inside
  `spec.agent`,
- the task system already distinguishes one-shot vs long-lived behavior,
- and persistence should be a property of the task process, not of one
  backend’s prompt model.

### 3.2 Agent Config Is Runtime-Level

`spec.agent` is static runtime configuration only.

For this slice it should contain:

- `runtime`
- `entrypoint`
- `model`
- `instructions`
- `templates`
- `tools`
- `output_mode`
- `output_schema`
- `max_turns`
- `options`
- `approval_policy`
- `conversation_scope`
- `persist_transcript`
- `stream_events`
- `runtime_config`

There is **no** `mode` field.
There is **no** dedicated `temperature` field.
Use `instructions` only for the static system/developer prompt.

Map `instructions` to `llm`’s `system=` parameter.

### 3.3 Persistent `llm` Conversation State Must Not Live in `Consumer`

Do **not** keep a live `llm.Conversation` object inside the `Consumer`
process.

Reason:

- that would bypass the existing monitored subprocess boundary,
- it would muddy process isolation,
- and resource limits/timeouts would no longer apply where the actual model/tool
  execution happens.

For `conversation_scope="per_task"`, the persistent conversation must live in a
**long-lived worker subprocess** owned by the task, not in the `Consumer`
process itself.

### 3.4 Persistent Per-Task Conversation Needs a Dedicated Session Path

One-shot agent work can continue to use the current `TaskRunner.run(...)`
subprocess-per-message path.

Persistent per-task conversation cannot.

It needs:

- a long-lived worker subprocess,
- a session wrapper in the parent task,
- and a runtime session object in the child worker that holds the live
  `llm.Conversation`.

This is conceptually similar to interactive command sessions, but it is **not**
the same feature.

Do not overload `spec.interactive`.
Do not tunnel agent work through the command interactive path.

### 3.5 Manager Idle Timeout Must Not Kill Persistent Children

Current Manager behavior will force-stop child tasks on idle timeout.
That is incompatible with real persistent agents.

For this slice:

- active persistent children must prevent idle timeout shutdown,
- explicit Manager shutdown must still stop persistent children cleanly.

Do not leave this as a footgun.

### 3.6 Templates Must Stay Tiny

Templates are a small convenience feature, not a programming language.

For this slice:

- support only string prompt templates with `{{ name }}` placeholders,
- support optional template-level `instructions`,
- reject missing template variables,
- reject extra template variables,
- reject unknown template names.

Do not add loops, filters, conditionals, includes, or a third-party templating
dependency.

### 3.7 Persistent Wait Semantics Stay Simple

Persistent tasks are not suitable for `weft run --wait`.

For this slice:

- launching a persistent spec through the CLI must require `--no-wait`,
- follow-up work is sent through the inbox,
- results are fetched through the existing outbox / `weft result` path.

Do not invent “wait until first response” or “attach” semantics in this slice.

## 4. Behavior Contract

The code from this plan must support exactly this behavior.

### 4.1 Generic Task Lifecycle

`SpecSection` gains:

- `persistent: bool = False`

Semantics:

- `persistent=False`: current one-shot behavior
- `persistent=True`: task remains alive and continues draining inbox messages
  until STOP, fatal error, timeout, kill, or explicit shutdown

This is generic task behavior, not agent-specific behavior.

### 4.2 Supported Agent Spec

Supported:

- `spec.type = "agent"`
- `spec.persistent = false|true`
- `spec.agent.runtime = "llm"`
- `spec.agent.model` as a non-empty string
- `spec.agent.instructions` as optional string
- `spec.agent.templates` as a mapping of template names to:
  - `prompt: str`
  - `instructions: str | None`
- `spec.agent.tools` where every tool has `kind = "python"`
- `spec.agent.output_mode = "text"` or `spec.agent.output_mode = "json"`
- `spec.agent.output_schema` only when `output_mode = "json"`
- `spec.agent.max_turns` as a positive integer
- `spec.agent.options` as backend option mapping
- `spec.agent.conversation_scope = "per_message" | "per_task"`

Rejected for now, with clear validation/runtime errors:

- `runtime != "llm"`
- `entrypoint` when `runtime = "llm"`
- `interactive = true` on agent tasks
- `conversation_scope = "per_task"` when `spec.persistent = false`
- tool kinds other than `python`
- `output_mode = "messages"` or `output_mode = "native"`
- `persist_transcript = true`
- `stream_events = true`
- approvals that would require interactive approval flow

### 4.3 Supported Work Envelope

Supported:

- plain string input, normalized to `task`
- JSON object with exactly one of:
  - `task`
  - `messages`
  - `template`
- `template_args`
- `metadata`
- `tool_overrides.allow`
- `tool_overrides.deny`

Explicit rules:

- Exactly one of `task`, `messages`, or `template` must define the content.
- `template_args` is only valid when `template` is present.
- `messages` remains supported and is flattened deterministically for `llm`.
- Dynamic work input may **select** a template and provide variables, but it may
  not define or replace template bodies.

### 4.4 Template Semantics

For a selected template:

- `template.prompt` is rendered using exact `{{ name }}` substitution.
- `template.instructions`, if present, **overrides** `spec.agent.instructions`
  for that work item.
- if `template.instructions` is absent, use `spec.agent.instructions`.

Do not merge instructions strings in this slice.
Do not build multi-layer prompt composition logic.

### 4.5 Persistent Agent Semantics

If `spec.persistent = true` and `conversation_scope = "per_message"`:

- the task remains alive across inbox messages,
- each work item runs with a fresh model conversation,
- the task process persists, the model conversation does not.

If `spec.persistent = true` and `conversation_scope = "per_task"`:

- the task remains alive across inbox messages,
- a long-lived worker subprocess keeps one live `llm.Conversation`,
- successive work items continue that conversation.

### 4.6 State and Log Semantics

One-shot behavior stays as-is:

- `work_spawning`
- `work_started`
- `work_completed`
- terminal task state

Persistent behavior must **not** mark the task completed after each successful
message.

For persistent tasks:

- first successful work item transitions `created -> spawning -> running`
- per-message success emits:
  - `work_item_started`
  - `work_item_completed`
- task state remains `running` after each successful message
- STOP transitions the task to `cancelled`
- fatal error transitions the task to `failed` / `timeout` / `killed`

Do not emit `work_completed` for a non-terminal message result.

### 4.7 CLI Semantics

For this slice:

- `weft run --spec ... --no-wait` works for persistent specs
- `weft run --spec ... --wait` fails for persistent specs
- `weft result TID` returns the **next available** outbox message even if the
  task itself is still running
- `weft queue write T{tid}.inbox ...` can drive a persistent task

The existing flag names must stay:

- `--continuous`
- `--once`

But the implementation must distinguish:

- no CLI override provided,
- explicit `--continuous`,
- explicit `--once`.

That distinction matters because specs may declare `persistent=true`.

## 5. Implementation Tasks

Work these tasks in order. Do not collapse them into one giant patch.

---

### Task 1: Correct the TaskSpec Schema First

#### Goal

Make the code match the corrected spec before touching runtime behavior.

#### Read First

- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
- [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
- [`weft/core/taskspec.py`](../../weft/core/taskspec.py)
- [`tests/taskspec/fixtures.py`](../../tests/taskspec/fixtures.py)
- [`tests/taskspec/test_taskspec.py`](../../tests/taskspec/test_taskspec.py)
- [`tests/specs/taskspec/test_agent_taskspec.py`](../../tests/specs/taskspec/test_agent_taskspec.py)

#### Files To Touch

- [`weft/core/taskspec.py`](../../weft/core/taskspec.py)
- [`tests/taskspec/fixtures.py`](../../tests/taskspec/fixtures.py)
- [`tests/taskspec/test_taskspec.py`](../../tests/taskspec/test_taskspec.py)
- [`tests/specs/taskspec/test_agent_taskspec.py`](../../tests/specs/taskspec/test_agent_taskspec.py)
- [`tests/cli/test_cli_validate.py`](../../tests/cli/test_cli_validate.py)

#### Implementation Notes

- Add `persistent: bool = False` to `SpecSection`.
- Define a new `AgentTemplateSection` with:
  - `prompt: str`
  - `instructions: str | None = None`
- Define `AgentSection.options: dict[str, Any]`.
- Add `templates: dict[str, AgentTemplateSection]`.
- Keep `instructions` as the only static prompt field.
- Add strict nested validation for agent models:
  - `AgentSection`
  - `AgentToolSection`
  - `AgentTemplateSection`
  should reject unknown keys instead of silently ignoring them.
- Keep the nested agent schema strict. Reject unknown keys instead of silently
  accepting or translating them.
- Reject `spec.type="agent"` together with `interactive=true`.
- Reject `conversation_scope="per_task"` when `spec.persistent=false`.
- Ensure persistent resolved specs still get a default inbox queue from the
  existing `apply_defaults()` path.

#### Write These Tests First

- `SpecSection.persistent` defaults to `False`
- agent spec with `persistent=True` validates
- `conversation_scope="per_task"` without `persistent=True` fails
- agent spec with unknown keys fails loudly
- template sections validate and freeze correctly
- agent task with `interactive=True` fails validation
- resolved persistent TaskSpec still has inbox / outbox / ctrl queues

#### Invariants To Protect

- lifecycle remains task-level, not agent-level
- agent config stays immutable after TaskSpec creation
- unsupported keys fail loudly
- no aliasing or translation logic is added

#### Verify

```bash
uv run --extra dev pytest tests/taskspec/test_taskspec.py tests/specs/taskspec/test_agent_taskspec.py tests/cli/test_cli_validate.py -q
uv run --extra dev mypy weft
uv run --extra dev ruff check --fix weft tests
```

#### Done Means

- the schema matches the corrected design,
- and tests prove the corrected shape.

---

### Task 2: Add Work-Envelope Template Support and Tight Normalization

#### Goal

Support templates, explicit prompt rendering, and explicit validation rules for
dynamic work envelopes.

#### Read First

- [`weft/core/agent_runtime.py`](../../weft/core/agent_runtime.py)
- [`weft/core/taskspec.py`](../../weft/core/taskspec.py)
- [`tests/core/test_agent_runtime.py`](../../tests/core/test_agent_runtime.py)
- [`tests/core/test_agent_tools.py`](../../tests/core/test_agent_tools.py)

#### Files To Touch

- [`weft/core/agent_runtime.py`](../../weft/core/agent_runtime.py)
- [`weft/core/agent_templates.py`](../../weft/core/agent_templates.py) (new)
- [`tests/core/test_agent_runtime.py`](../../tests/core/test_agent_runtime.py)
- [`tests/core/test_agent_templates.py`](../../tests/core/test_agent_templates.py) (new)

#### Implementation Notes

- Create one small module for template rendering and nothing else:
  [`weft/core/agent_templates.py`](../../weft/core/agent_templates.py).
- Use a tiny regex-based renderer for `{{ name }}` placeholders.
- No third-party templating dependency.
- Normalize work envelopes so exactly one of `task`, `messages`, or `template`
  defines the content source.
- Make `template_args` invalid unless `template` is present.
- Render template prompt into the normalized work item before the backend sees
  it.
- Carry through a resolved instructions value:
  - `template.instructions` if present,
  - else `agent.instructions`.
- Keep deterministic `messages` flattening exactly stable.
- Preserve `metadata` and `tool_overrides`.
- Reject extra work-envelope fields explicitly.

#### Write These Tests First

- render simple template with one variable
- missing template variable fails
- extra template variable fails
- unknown template name fails
- `task` plus `template` fails
- `messages` plus `template` fails
- template-level instructions override agent instructions
- message flattening still matches existing deterministic format

#### Invariants To Protect

- templates stay tiny and deterministic
- dynamic input cannot redefine static templates
- template rendering stays backend-neutral
- normalized work items remain JSON-friendly

#### Verify

```bash
uv run --extra dev pytest tests/core/test_agent_runtime.py tests/core/test_agent_templates.py tests/core/test_agent_tools.py -q
uv run --extra dev mypy weft
uv run --extra dev ruff check --fix weft tests
```

#### Done Means

- the runtime can consume template-based work envelopes,
- bad envelopes fail clearly,
- and the renderer is intentionally small.

---

### Task 3: Complete the One-Shot `llm` Backend Argument Surface

#### Goal

Make the current one-shot `llm` backend faithfully pass the arguments the user
actually cares about: model, system prompt, tools, schema, templates, and
backend options.

#### Read First

- [`weft/core/agents/backends/llm_backend.py`](../../weft/core/agents/backends/llm_backend.py)
- [`weft/core/agent_runtime.py`](../../weft/core/agent_runtime.py)
- [`tests/fixtures/llm_test_models.py`](../../tests/fixtures/llm_test_models.py)
- [`tests/core/test_llm_backend.py`](../../tests/core/test_llm_backend.py)

#### Files To Touch

- [`weft/core/agents/backends/llm_backend.py`](../../weft/core/agents/backends/llm_backend.py)
- [`tests/fixtures/llm_test_models.py`](../../tests/fixtures/llm_test_models.py)
- [`tests/core/test_llm_backend.py`](../../tests/core/test_llm_backend.py)

#### Implementation Notes

- `instructions` must map to `llm`’s `system=` argument.
- `options` must be passed through to `llm` in the documented way:
  - for `conversation.prompt(...)`, pass as keyword args,
  - for `conversation.chain(...)`, pass as `options=...` where required by the
    actual API.
- Do not guess. Read the installed `llm` API signatures before coding.
- Continue using real `llm.Tool(...)` objects for Python tools.
- Reject `entrypoint` for the `llm` backend in this slice.
- Reject `persist_transcript=true` and `stream_events=true` in the `llm`
  backend for now.
- Keep the backend thin. Queue behavior stays outside it.

#### Deterministic Test Model Requirements

Update the real local test plugin in
[`tests/fixtures/llm_test_models.py`](../../tests/fixtures/llm_test_models.py):

- define a real `Options` model so selected options such as `temperature` are
  accepted by `llm`,
- expose prompt/system/options/tool names back through deterministic responses,
- support both one-shot inspection and continuation tests,
- continue to run without network access.

Do not patch `llm`.
Do not shell out to the `llm` CLI.

#### Write These Tests First

- system prompt is visible to the model
- model options are visible to the model
- tools are visible to the model
- template rendering feeds the expected prompt text
- bad options fail clearly
- `entrypoint` is rejected for `llm`
- `persist_transcript=true` is rejected for `llm`
- `stream_events=true` is rejected for `llm`

#### Invariants To Protect

- backend stays JSON-friendly
- backend does not own queue semantics
- no network is required for deterministic tests

#### Verify

```bash
uv run --extra dev pytest tests/core/test_llm_backend.py -q
uv run --extra dev mypy weft
uv run --extra dev ruff check --fix weft tests
```

#### Done Means

- one-shot `llm` agent calls pass the intended arguments correctly,
- and the test model proves it through the real Python API.

---

### Task 4: Build a Persistent Agent Session Worker

#### Goal

Add the long-lived subprocess/session path needed for
`persistent=True` + `conversation_scope="per_task"`.

#### Read First

- [`weft/core/agent_runtime.py`](../../weft/core/agent_runtime.py)
- [`weft/core/agents/backends/llm_backend.py`](../../weft/core/agents/backends/llm_backend.py)
- [`weft/core/tasks/runner.py`](../../weft/core/tasks/runner.py)
- [`weft/core/tasks/sessions.py`](../../weft/core/tasks/sessions.py)
- [`weft/core/tasks/interactive.py`](../../weft/core/tasks/interactive.py)

#### Files To Touch

- [`weft/core/agent_runtime.py`](../../weft/core/agent_runtime.py)
- [`weft/core/agents/backends/llm_backend.py`](../../weft/core/agents/backends/llm_backend.py)
- [`weft/core/tasks/runner.py`](../../weft/core/tasks/runner.py)
- [`weft/core/tasks/agent_session.py`](../../weft/core/tasks/agent_session.py) (new)
- [`tests/tasks/test_runner.py`](../../tests/tasks/test_runner.py)
- [`tests/tasks/test_agent_execution.py`](../../tests/tasks/test_agent_execution.py)
- [`tests/fixtures/llm_test_models.py`](../../tests/fixtures/llm_test_models.py)

#### Implementation Notes

- Extend the runtime contract with a **session protocol**.
- Keep it narrow and boring:

```python
class AgentRuntimeSession(Protocol):
    def execute(self, raw_work_item: Any) -> dict[str, Any]:
        ...

    def close(self) -> None:
        ...
```

- The runtime registry should expose both:
  - one-shot execution
  - session start for backends that support it
- For `llm`, implement a concrete session object that holds:
  - the model,
  - the full static agent config,
  - one live `llm.Conversation`.
- Do **not** resolve conversation state in the parent `Consumer`.
- Add a new parent-side session wrapper in
  [`weft/core/tasks/agent_session.py`](../../weft/core/tasks/agent_session.py):
  - owns the child process,
  - sends raw work items,
  - receives final result envelopes,
  - exposes `pid`, `is_alive()`, `terminate()`, `close()`,
  - tracks last resource metrics,
  - enforces per-message timeout.
- Add a child-worker loop in the same module that:
  - creates the runtime session once,
  - reads one raw work item at a time,
  - executes it,
  - returns a JSON-friendly result or error envelope,
  - exits cleanly on sentinel/close.
- Integrate resource monitoring with the session worker PID the same way command
  interactive sessions are monitored.
- If a session work item times out or hits a limit, terminate the session
  worker. Do not try to salvage half-broken conversation state.

#### Write These Tests First

- persistent session processes two work items in order
- second work item sees continued conversation state
- per-message timeout kills the session worker
- resource limit violation kills the session worker
- close() terminates the session cleanly
- child-process errors surface as clear task errors

#### Invariants To Protect

- conversation state lives in the worker subprocess, not the `Consumer`
- resource limits and timeout still apply where model/tool execution happens
- the transport layer remains simple: one request in, one final result out

#### Verify

```bash
uv run --extra dev pytest tests/tasks/test_runner.py tests/tasks/test_agent_execution.py -q
uv run --extra dev mypy weft
uv run --extra dev ruff check --fix weft tests
```

#### Done Means

- there is a real long-lived agent worker subprocess,
- and continuation works without giving up process isolation.

---

### Task 5: Integrate Persistent Semantics into `Consumer`

#### Goal

Make `Consumer` honor generic task persistence and route persistent
per-task-agent work through the new session path.

#### Read First

- [`weft/core/tasks/consumer.py`](../../weft/core/tasks/consumer.py)
- [`weft/core/tasks/base.py`](../../weft/core/tasks/base.py)
- [`weft/core/tasks/interactive.py`](../../weft/core/tasks/interactive.py)
- [`weft/core/tasks/runner.py`](../../weft/core/tasks/runner.py)
- [`tests/tasks/test_task_execution.py`](../../tests/tasks/test_task_execution.py)
- [`tests/tasks/test_agent_execution.py`](../../tests/tasks/test_agent_execution.py)
- [`tests/tasks/test_control_channel.py`](../../tests/tasks/test_control_channel.py)

#### Files To Touch

- [`weft/core/tasks/consumer.py`](../../weft/core/tasks/consumer.py)
- [`tests/tasks/test_task_execution.py`](../../tests/tasks/test_task_execution.py)
- [`tests/tasks/test_agent_execution.py`](../../tests/tasks/test_agent_execution.py)
- [`tests/tasks/test_control_channel.py`](../../tests/tasks/test_control_channel.py)

#### Implementation Notes

- Do not rewrite `Consumer` from scratch.
- Add small private helpers for persistent behavior.
- Keep one-shot behavior unchanged.
- Introduce a simple branching rule:
  - non-persistent task -> current one-shot behavior
  - persistent + agent + `conversation_scope="per_task"` -> session path
  - everything else persistent -> current `_run_task()` path, but do not stop
    after success
- For persistent successful messages:
  - delete reserved message,
  - emit result to outbox,
  - emit `work_item_completed`,
  - keep `taskspec.state.status == "running"`,
  - do not mark completed,
  - do not set `should_stop = True`.
- Add `work_item_started` for non-initial persistent messages.
- First persistent message should still transition through
  `spawning -> running`.
- STOP should terminate any active agent session and mark the task cancelled.
- Cleanup must close/terminate any active agent session.
- Fatal message errors remain terminal in this slice.

#### Write These Tests First

- persistent function task processes two messages before STOP
- persistent command/function task stays `running` after first success
- persistent agent task with `conversation_scope="per_message"` handles two
  independent messages
- persistent agent task with `conversation_scope="per_task"` continues
  conversation across two messages
- STOP on persistent agent task closes the session worker
- message-level error terminally fails the task and applies reserved policy

#### Invariants To Protect

- persistent success is not terminal task completion
- reserved-queue success policy still holds
- STOP / failure / timeout / limit remain terminal
- one-shot tasks still behave exactly as before

#### Verify

```bash
uv run --extra dev pytest tests/tasks/test_task_execution.py tests/tasks/test_agent_execution.py tests/tasks/test_control_channel.py -q
uv run --extra dev mypy weft
uv run --extra dev ruff check --fix weft tests
```

#### Done Means

- generic persistence works at the task layer,
- and persistent agent continuation works through normal inbox/outbox semantics.

---

### Task 6: Fix Manager and CLI Semantics for Persistent Tasks

#### Goal

Make persistent tasks launch correctly through the Manager and survive normal
idle periods without being silently killed.

#### Read First

- [`weft/core/manager.py`](../../weft/core/manager.py)
- [`weft/core/launcher.py`](../../weft/core/launcher.py)
- [`weft/cli.py`](../../weft/cli.py)
- [`weft/commands/run.py`](../../weft/commands/run.py)
- [`weft/commands/result.py`](../../weft/commands/result.py)
- [`tests/core/test_manager.py`](../../tests/core/test_manager.py)
- [`tests/cli/test_cli_run.py`](../../tests/cli/test_cli_run.py)
- [`tests/cli/test_cli_result.py`](../../tests/cli/test_cli_result.py)

#### Files To Touch

- [`weft/core/manager.py`](../../weft/core/manager.py)
- [`weft/cli.py`](../../weft/cli.py)
- [`weft/commands/run.py`](../../weft/commands/run.py)
- [`tests/core/test_manager.py`](../../tests/core/test_manager.py)
- [`tests/cli/test_cli_run.py`](../../tests/cli/test_cli_run.py)
- [`tests/cli/test_cli_result.py`](../../tests/cli/test_cli_result.py)
- [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py) if new
  wait helpers are needed

#### Implementation Notes

- `ManagedChild` should track whether the child task is persistent.
- Manager idle timeout must **not** terminate active persistent children.
- Explicit Manager shutdown must still send STOP to persistent children.
- Do not make the Manager immortal. Only exempt active persistent children from
  idle cleanup.

##### CLI Override Semantics

The current `--continuous/--once` option is a boolean. That is not sufficient
for spec-defined persistence because you need to distinguish:

- no override provided,
- explicit `--continuous`,
- explicit `--once`.

Implement a tri-state override while preserving the existing flag names.

Acceptable approaches:

- a callback that records explicit flag presence,
- or a small wrapper type/sentinel.

Do **not** keep a plain boolean if it means a persistent spec can never be
launched without explicit CLI surgery.

##### Effective Persistence Rules

For `weft run --spec ...`:

- if CLI explicitly says `--continuous`, launch with `persistent=true`
- if CLI explicitly says `--once`, launch with `persistent=false`
- if no CLI lifecycle override is provided, honor `spec.spec.persistent`

##### Wait Rules

- if the effective task is persistent, reject `--wait`
- require `--no-wait`

Do not invent attach/first-result behavior in this slice.

#### Write These Tests First

- Manager idle timeout does not kill a persistent child
- Manager still shuts persistent child down on explicit Manager stop
- `weft run --spec persistent.json --no-wait` succeeds
- `weft run --spec persistent.json --wait` fails clearly
- spec-declared persistence works without CLI override
- explicit `--continuous` overrides non-persistent spec
- explicit `--once` overrides persistent spec

#### Invariants To Protect

- persistent child tasks are not silently reaped by Manager idle logic
- CLI flags keep their current names
- non-persistent CLI behavior remains unchanged

#### Verify

```bash
uv run --extra dev pytest tests/core/test_manager.py tests/cli/test_cli_run.py tests/cli/test_cli_result.py -q
uv run --extra dev mypy weft
uv run --extra dev ruff check --fix weft tests
```

#### Done Means

- persistent tasks can actually stay alive,
- and the CLI/Manager contract is no longer contradictory.

---

### Task 7: Add Black-Box Continuation Tests

#### Goal

Prove the full stack works:

- spec validation,
- Manager launch,
- persistent task stays alive,
- inbox work goes in,
- outbox results come out,
- second message sees prior conversation state,
- STOP shuts it down.

#### Read First

- [`tests/conftest.py`](../../tests/conftest.py)
- [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py)
- [`tests/cli/test_cli_run.py`](../../tests/cli/test_cli_run.py)
- [`tests/cli/test_cli_result.py`](../../tests/cli/test_cli_result.py)
- [`tests/specs/message_flow/test_agent_spawning_transition.py`](../../tests/specs/message_flow/test_agent_spawning_transition.py)

#### Files To Touch

- [`tests/cli/test_cli_run.py`](../../tests/cli/test_cli_run.py)
- [`tests/cli/test_cli_result.py`](../../tests/cli/test_cli_result.py)
- [`tests/tasks/test_agent_execution.py`](../../tests/tasks/test_agent_execution.py)
- [`tests/specs/message_flow/test_agent_spawning_transition.py`](../../tests/specs/message_flow/test_agent_spawning_transition.py)
- [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py) if
  needed for non-flaky polling helpers

#### Implementation Notes

- Use the real `llm` test plugin model.
- Use a real persistent agent TaskSpec on disk.
- Launch it through `weft run --spec ... --no-wait`.
- Drive it through the real inbox queue.
- Fetch each result through `weft result TID --timeout ...`.
- Send STOP through the real control queue or `weft task stop` if that is the
  cleaner path in the existing CLI.
- Verify log events using real queue polling, not sleeps.

#### Black-Box Scenarios To Cover

1. Persistent agent continues conversation
   - launch persistent agent spec
   - send first message
   - fetch first result
   - send second message
   - fetch second result
   - assert second result reflects continuation state

2. Persistent agent with `conversation_scope="per_message"`
   - same launch flow
   - second result must **not** reflect continuation state

3. Explicit arguments are honored
   - system prompt via `instructions`
   - template selection and template args
   - tool visibility
   - backend options

4. STOP closes the persistent task cleanly

#### Invariants To Protect

- queue-first coordination remains real and observable
- `weft result` works for running persistent tasks
- continuation is proven end-to-end, not inferred from unit tests

#### Verify

```bash
uv run --extra dev pytest tests/cli/test_cli_run.py tests/cli/test_cli_result.py tests/tasks/test_agent_execution.py tests/specs/message_flow/test_agent_spawning_transition.py -q
uv run --extra dev mypy weft
uv run --extra dev ruff check --fix weft tests
```

#### Done Means

- there is a true integration test for continuation,
- and the stack works beyond helper-level unit tests.

---

### Task 8: Sync the Specs and README to the Final Code

#### Goal

Make the written docs match the code that actually ships.

#### Read First

- [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md)
- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
- [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
- [`README.md`](../../README.md)

#### Files To Touch

- [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md)
- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
- [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
- [`README.md`](../../README.md)

#### Implementation Notes

- Document `spec.persistent` as the generic task-level lifecycle field.
- Document `templates` and `options`.
- Document that `instructions` is the system/developer prompt field.
- Document persistent-task log semantics:
  - `work_item_started`
  - `work_item_completed`
- Document that persistent tasks are launched with `--no-wait`.
- Document current MVP limits explicitly:
  - `llm` only
  - Python tools only
  - no approvals
  - no event streaming
  - no transcript persistence

#### Tests

No new code tests required here, but do not skip the full gates after docs land.

#### Verify

```bash
uv run --extra dev pytest
uv run --extra dev mypy weft
uv run --extra dev ruff check --fix weft tests
```

#### Done Means

- the docs no longer contradict the implementation,
- and future engineers will not have to reverse-engineer intent from code.

---

### Task 9: Final Quality Pass

#### Goal

Remove accidental complexity and make the branch fit to hand off.

#### Refactor Checklist

- Remove dead helpers and unused imports.
- Remove dead branches and dead schema handling.
- Keep backend-specific behavior out of generic modules.
- Keep template logic out of `llm_backend.py`.
- Keep queue semantics out of runtime backends.
- Keep Manager idle logic readable; do not bury it in tiny boolean helpers that
  obscure policy.
- If a function grows too large, split helpers without introducing a framework.

#### Mandatory Full Gates

Run these exact commands **sequentially**:

```bash
uv run --extra dev ruff check --fix weft tests
uv run --extra dev mypy weft
uv run --extra dev pytest
uv run --extra dev pytest -m ""
```

If any full gate is skipped, document the exact reason and the exact subset you
ran. Do not omit that silently.

#### Handoff Checklist

- Summarize the exact behavior implemented.
- List unsupported features explicitly.
- List every new module added.
- Call out any remaining spec/code mismatch.

## 6. Suggested File Layout After This Slice

The resulting code should look roughly like this:

```text
weft/
├── core/
│   ├── agent_runtime.py
│   ├── agent_templates.py
│   ├── agent_tools.py
│   ├── agents/
│   │   ├── __init__.py
│   │   └── backends/
│   │       ├── __init__.py
│   │       └── llm_backend.py
│   ├── tasks/
│   │   ├── agent_session.py
│   │   ├── consumer.py
│   │   ├── runner.py
│   │   └── sessions.py
│   └── taskspec.py
```

This is intentionally modest.
Do not add more layers until you need them.

## 7. Things That Will Tempt You and Why They Are Wrong

- “I should add `spec.agent.mode`.”
  Wrong. Lifecycle belongs at the task layer.

- “I should add a dedicated `temperature` field.”
  Wrong. Backend options belong in `options`.

- “I should keep the persistent `llm.Conversation` in `Consumer` because it is
  easier.”
  Wrong. That bypasses the subprocess boundary where limits and timeouts belong.

- “I should add Jinja2 for templates.”
  Wrong. This slice needs string substitution, not a template engine.

- “I should add a generic multi-backend session framework now.”
  Wrong. Build the narrow protocol needed for `llm`. Stop there.

- “I should make `weft run --wait` attach to persistent tasks until first
  output.”
  Wrong. That is a second UX design, not a required part of this slice.

- “I should test this by mocking queues.”
  Wrong. The point is durable queue/process integration.

- “I should run `uv` commands in parallel to save time.”
  Wrong. That can produce false failures in this repo.

## 8. Fresh-Eyes Review Notes

This plan was reviewed for latent ambiguities and drift. The main issues found
and corrected here are:

- **Wrong persistence location**: the easiest implementation would keep
  `llm.Conversation` inside `Consumer`. That was rejected because it would
  bypass the monitored subprocess boundary.
- **Manager idle-timeout footgun**: the current Manager can terminate active
  child tasks on idle timeout. The plan now explicitly requires an exemption for
  persistent children and tests for it.
- **CLI override ambiguity**: the current `--continuous/--once` boolean cannot
  distinguish omitted vs explicit. The plan now requires a tri-state override
  implementation before persistent specs are considered correct.
- **Terminal event confusion**: reusing `work_completed` for persistent
  per-message success would blur task-level completion. The plan now requires
  dedicated non-terminal `work_item_*` events.
- **Template scope drift**: it would be easy to let templates become ad hoc
  little programs. The plan now locks them to exact placeholder substitution and
  explicit failure on missing/extra variables.
- **Alias temptation**: it would be easy to add schema translations or soft
  aliases. The plan rejects that and requires one strict target shape.

If, while implementing, you find yourself needing:

- a second orchestration plane,
- a conversation database,
- a heavy templating engine,
- or a new agent-specific lifecycle field,

stop and reassess. That would be a materially different direction from what was
discussed.

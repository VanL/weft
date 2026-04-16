# Agent Runtime Implementation Plan

Status: completed
Source specs: see Source Documents below
Superseded by: ./2026-04-06-persistent-agent-runtime-implementation-plan.md (persistent agent work)

This document is the implementation plan for the draft spec in
[`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md).
It is written for an engineer who is strong in Python but has **almost no
context** for Weft, SimpleBroker, or the design decisions behind this project.

Read this plan as a set of **bite-sized tasks**. Each task names the files to
read first, the files to touch, the tests to write first, the invariants to
protect, and the verification commands to run.

The tone here is intentionally strict. The likely failure modes are:

- adding abstractions before they are needed,
- over-mocking instead of testing real queue/process behavior,
- making the runtime larger than the agreed MVP,
- quietly ignoring unsupported spec fields,
- and smearing agent logic across too many existing modules.

Do not do those things.

Status note: this plan describes the shipped prototype slice. The corrected
spec now places one-off vs persistent behavior at the **task lifecycle** layer,
not inside `spec.agent`. Use this plan for understanding the prototype only;
write a fresh implementation plan before building persistent agent tasks.

## 0. Scope Lock

This plan implements the **minimum useful slice** of the agent runtime:

- first-class `spec.type="agent"` support in `TaskSpec`,
- one built-in backend: `llm`,
- one-shot execution through the existing task lifecycle,
- one tool kind: `python`,
- final result delivery through the existing outbox/result path,
- execution through the existing `Consumer` + `TaskRunner` path so resource
  monitoring, timeout handling, and reservation semantics remain intact.

This plan intentionally does **not** implement:

- `pydantic_ai`,
- persistent agent-task behavior,
- approval flow over `ctrl_in` / `ctrl_out`,
- semantic event streaming to outbox during execution,
- queue/mcp/agent/command tools,
- a new persistent conversation store,
- new inline CLI flags such as `weft run --agent ...`.

If implementation pressure starts pulling toward any of those, stop. That is
scope drift away from the agreed design.

## 1. Read This First

Before touching code, read these in this order:

1. [`AGENTS.md`](../../AGENTS.md)
2. [`docs/specifications/00-Overview_and_Architecture.md`](../specifications/00-Overview_and_Architecture.md)
3. [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
4. [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md)
5. [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
6. [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
7. [`docs/specifications/08-Testing_Strategy.md`](../specifications/08-Testing_Strategy.md)
8. [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
9. [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
10. [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py)
11. [`tests/conftest.py`](../../tests/conftest.py)
12. [`weft/core/taskspec.py`](../../weft/core/taskspec.py)
13. [`weft/core/tasks/consumer.py`](../../weft/core/tasks/consumer.py)
14. [`weft/core/tasks/runner.py`](../../weft/core/tasks/runner.py)
15. [`weft/core/targets.py`](../../weft/core/targets.py)
16. [`weft/core/manager.py`](../../weft/core/manager.py)
17. [`weft/commands/run.py`](../../weft/commands/run.py)
18. [`weft/commands/specs.py`](../../weft/commands/specs.py)

Also inspect SimpleBroker style before writing new modules:

1. `/Users/van/.venv/lib/python3.13/site-packages/simplebroker/sbqueue.py`
2. `/Users/van/.venv/lib/python3.13/site-packages/simplebroker/watcher.py`
3. `/Users/van/.venv/lib/python3.13/site-packages/simplebroker/helpers.py`

You are not copying SimpleBroker code. You are learning its style:

- small modules,
- direct naming,
- real resource cleanup,
- minimal hidden magic,
- concrete tests against real SQLite-backed queues.

## 2. Engineering Rules

These rules are not optional.

### 2.1 Style

- Use `from __future__ import annotations` in every new Python file.
- Keep imports at module top. No deferred imports.
- Use `collections.abc` types, not legacy `typing.List` / `typing.Dict`.
- Use `Path`, not `os.path`.
- Keep modules single-purpose.
- Keep names boring and explicit. Prefer `AgentRuntimeAdapter` over clever names.
- Add docstrings with spec references where behavior is non-trivial.
- Add comments only where the code would otherwise be hard to parse.

### 2.2 Complexity

- Do not create a new abstraction just because you can imagine future backends.
- Do not create a generic plugin system for runtimes in the MVP.
- Do not build an event bus, approval engine, or memory subsystem.
- Do not introduce a second execution path if the current one can be extended
  cleanly.
- Keep cyclomatic complexity down. Split helpers before giant branch pyramids
  appear.

### 2.3 DRY and YAGNI

- Be DRY where duplication is real and harmful.
- Be YAGNI where the duplication is still hypothetical.
- Reuse the existing `Consumer` + `TaskRunner` path instead of inventing a
  parallel agent task process type for the MVP.
- Do not implement `pydantic_ai` "because we will need it later".

### 2.4 Testing

- Prefer red-green-refactor TDD for every task below.
- Do not add mocks, fake queue objects, or patch-heavy tests.
- Use real `Queue` instances, real task processes, and `WeftTestHarness`.
- Write both white-box and black-box tests.
- For deterministic backend tests, use real local test implementations:
  - a real local `llm.Model` subclass registered via llm's pluggy hooks,
  - or a small real runtime module loaded through the production registry.
- Do not patch `llm.get_model`.
- Do not mock `simplebroker.Queue`.
- Do not mock process state transitions.

## 3. Architecture Decisions Already Made

These decisions are fixed for this plan.

### 3.1 Keep the Existing Execution Spine

Agent work must go through the same durable path as current tasks:

- Manager expands TaskSpec and assigns TID.
- `Consumer` owns inbox/reserved/outbox/control semantics.
- `TaskRunner` owns spawned worker subprocess execution, timeout, and resource
  monitoring.

Reason: this is the current stable path for exactly the properties Weft already
does well. Do not bypass it.

### 3.2 Extend `TaskRunner`; Do Not Introduce `AgentTask` Yet

The draft spec could justify a dedicated `AgentTask`, but for the MVP that is
the wrong move.

Reason:

- `TaskRunner` is already the monitored subprocess boundary.
- Reusing it preserves resource limits and timeouts without a second control
  plane.
- `Consumer` already owns the reservation/result/logging path.
- A second task class would duplicate queue/finalization logic too early.

If a future persistent agent implementation requires a dedicated `AgentTask`,
add it later.
Do not front-load that complexity now.

### 3.3 Treat the Draft Spec as a Superset

The draft spec is broader than the first implementation. That is okay.

The MVP must:

- implement a clearly documented subset,
- reject unsupported values explicitly,
- and update the docs to say exactly what is implemented.

Do not silently ignore unsupported agent fields.

### 3.4 Use the `llm` Python API Directly

Use the `llm` library as a Python dependency, not by shelling out to the
`llm` CLI.

Reason:

- avoids subprocess-in-subprocess complexity,
- keeps tool calls in-process,
- gives direct access to `llm.get_model(...)`, `model.conversation(...)`,
  `conversation.chain(...)`, and `Tool.function(...)`,
- is easier to test deterministically with a local `llm.Model` subclass.

## 4. MVP Behavior Contract

The code you implement from this plan must support exactly this behavior.

### 4.1 Supported Agent Spec

Supported:

- `spec.type = "agent"`
- `spec.agent.runtime = "llm"`
- `spec.agent.model` as a non-empty string
- `spec.agent.instructions` as optional string
- `spec.agent.tools` where every tool has `kind = "python"`
- `spec.agent.output_mode = "text"` or `spec.agent.output_mode = "json"`
- `spec.agent.output_schema` is optional, but only allowed when
  `output_mode = "json"`
- `spec.agent.max_turns` as a positive integer
- `spec.agent.runtime_config` as backend escape hatch

Rejected for now, with clear validation/runtime errors:

- `runtime != "llm"`
- `approval_policy != "auto"` if approval behavior would be required
- tool kinds other than `python`
- `output_mode = "messages"` or `output_mode = "native"`
- `attachments`
- semantic outbox event streaming

### 4.2 Supported Work Envelope

Supported:

- plain string input, normalized to `task`
- JSON object with `task`
- JSON object with `messages`, flattened deterministically into text if `task`
  is missing
- `metadata`
- `tool_overrides.allow`
- `tool_overrides.deny`

Unsupported in MVP:

- attachment loading,
- automatic file hydration,
- queue rendezvous in the work envelope,
- hidden implicit memory across work items.

If unsupported fields are present, fail clearly. Do not ignore them.

### 4.3 Supported Output

Supported:

- final outbox value is a JSON-serializable `agent_result` object,
- `output_mode="text"` returns `output` as string,
- `output_mode="json"` returns `output` as JSON-compatible data,
- if `output_schema` is absent, JSON mode still works and returns parsed JSON
  without schema validation,
- large outputs use the existing spill-to-disk mechanism.

Unsupported in MVP:

- semantic agent event streaming before completion,
- transcript persistence beyond final artifact references,
- conversation continuation across multiple inbox messages.

## 5. Implementation Tasks

Work these tasks in order. Do not collapse them into one large branch.

---

### Task 1: Add Agent Schema to TaskSpec

#### Goal

Teach `TaskSpec` what an agent spec is, and nothing more.

#### Read First

- [`weft/core/taskspec.py`](../../weft/core/taskspec.py)
- [`tests/taskspec/test_taskspec.py`](../../tests/taskspec/test_taskspec.py)
- [`tests/specs/taskspec/test_process_target.py`](../../tests/specs/taskspec/test_process_target.py)
- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)

#### Files To Touch

- [`weft/core/taskspec.py`](../../weft/core/taskspec.py)
- [`tests/taskspec/test_taskspec.py`](../../tests/taskspec/test_taskspec.py)
- [`tests/specs/taskspec/test_agent_taskspec.py`](../../tests/specs/taskspec/test_agent_taskspec.py) (new)

#### Implementation Notes

- Add explicit Pydantic models for agent config inside `taskspec.py`.
- Keep them close to `SpecSection`; they are part of TaskSpec schema.
- Extend `SpecSection.type` from `Literal["function", "command"]` to include
  `"agent"`.
- Add `agent: AgentSection | None = None` to `SpecSection`.
- Validation rules:
  - `function` requires `function_target`, forbids `process_target` and `agent`
  - `command` requires `process_target`, forbids `function_target` and `agent`
  - `agent` requires `agent`, forbids `function_target` and `process_target`
- Default values belong in the Pydantic fields for agent config where possible.
- Do not spread agent schema logic into other files yet.
- Update `_validate_strict_requirements()` for the `agent` branch.
- Keep `spec` immutability behavior unchanged; `spec.agent` must become frozen
  along with the rest of `spec`.

#### Write These Tests First

- valid minimal agent spec passes in template mode
- resolved agent spec auto-expands and preserves agent defaults
- `spec.type="agent"` with no `spec.agent` fails
- `spec.type="agent"` with `function_target` set fails
- `spec.type="agent"` with `process_target` set fails
- `spec.type="function"` with `spec.agent` set fails
- `spec.agent.tools[*].kind` rejects unsupported enum values
- `spec.agent.max_turns` rejects zero / negative
- `spec.agent.output_schema` is rejected unless `output_mode="json"`
- `spec.agent` is immutable after TaskSpec creation
- `validate_taskspec()` accepts agent task templates and reports useful errors

#### Invariants To Protect

- `IMMUT.1`, `IMMUT.2`, `IMMUT.3`, `IMMUT.4`
- `STATE.1` and terminal-state behavior must not change
- `QUEUE.1`, `QUEUE.2`, `QUEUE.3` must remain unchanged

#### Verify

Run only these first:

```bash
uv run pytest tests/taskspec/test_taskspec.py tests/specs/taskspec/test_agent_taskspec.py -q
uv run mypy weft
uv run ruff check --fix weft tests
```

#### Done Means

- agent specs validate,
- non-agent specs still validate,
- template/resolved behavior remains intact,
- immutability still works.

---

### Task 2: Add a Backend-Neutral Agent Runtime Surface

#### Goal

Create a small, explicit runtime boundary for agent execution and tool
resolution. Do not implement Weft-wide plugin architecture.

#### Read First

- [`weft/core/targets.py`](../../weft/core/targets.py)
- [`tests/core/test_targets.py`](../../tests/core/test_targets.py)
- `/Users/van/.venv/lib/python3.13/site-packages/simplebroker/sbqueue.py`
- `/Users/van/.venv/lib/python3.13/site-packages/simplebroker/watcher.py`
- [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)

#### Files To Touch

- [`weft/core/agent_runtime.py`](../../weft/core/agent_runtime.py) (new)
- [`weft/core/agent_tools.py`](../../weft/core/agent_tools.py) (new)
- [`tests/core/test_agent_runtime.py`](../../tests/core/test_agent_runtime.py) (new)
- [`tests/core/test_agent_tools.py`](../../tests/core/test_agent_tools.py) (new)

#### Implementation Notes

- `weft/core/agent_runtime.py` should define:
  - common dataclasses or small Pydantic models for normalized work items and
    final results,
  - the backend adapter protocol,
  - a tiny backend registry,
  - one public helper: `execute_agent_target(...)`.
- Keep the registry simple:
  - one mapping from runtime name to adapter implementation,
  - explicit registration API with duplicate-name protection,
  - no built-in `llm` registration yet in this task,
  - clear error for unknown runtimes.
- Prove the registry with a tiny real test backend defined in the test module.
  Do not drag the `llm` backend into this task.
- Do not build dynamic discovery, entry-point loading, or third-party runtime
  registration yet.
- `weft/core/agent_tools.py` should resolve **only** `python` tools in the MVP.
- Tool resolution should import a real module:function and wrap it in the shape
  the backend-neutral layer expects.
- Define a normalized resolved-tool object in Weft terms, for example:
  name, description, input schema, callable implementation. Do not return
  `llm.Tool` objects from the generic tool layer.
- Unsupported tool kinds must raise clear errors.
- `tool_overrides.allow` / `deny` must narrow the available tools for a single
  work item.
- If `allow` is omitted, start from all tools declared in the spec.
- Override behavior must be deterministic:
  - preserve spec order,
  - apply `allow` first,
  - apply `deny` second,
  - and fail clearly if an override names a tool not present in the spec.
- Keep queue semantics out of this module. This layer should know nothing about
  reserved queues or outboxes.
- If a work envelope includes both `task` and `messages`, `task` wins.
- If `messages` must be flattened, use one stable format and document it in the
  code and tests, for example:
  - preserve message order,
  - render each item as `<role>: <content>`,
  - if content is non-string, encode it as compact JSON,
  - join messages with `\n\n`.

#### Write These Tests First

- runtime registry accepts explicit registration, rejects duplicates, and
  rejects unknown runtime names
- work envelope normalization from string input to `{task: ...}`
- work envelope normalization from `messages` to deterministic flattened text
- `task` wins over `messages` when both are present
- unsupported work-envelope fields fail clearly
- python tool resolution imports a callable and preserves its signature-derived
  schema
- allow/deny overrides narrow the tool list correctly
- unknown tool names in allow/deny fail clearly
- unsupported tool kinds raise explicit errors
- final result object is JSON-serializable

#### Invariants To Protect

- Agent runtime layer must not own queue state
- Dynamic work item must not mutate TaskSpec
- Unsupported fields must fail loudly, not be ignored

#### Verify

```bash
uv run pytest tests/core/test_agent_runtime.py tests/core/test_agent_tools.py -q
uv run mypy weft
uv run ruff check --fix weft tests
```

#### Done Means

- there is one obvious production entry point for agent execution,
- tool resolution is explicit and narrow,
- there is no plugin framework creep.

---

### Task 3: Extend `TaskRunner` for `target_type="agent"`

#### Goal

Run agent work inside the existing monitored subprocess boundary so timeouts and
resource limits remain correct.

#### Read First

- [`weft/core/tasks/runner.py`](../../weft/core/tasks/runner.py)
- [`weft/core/tasks/consumer.py`](../../weft/core/tasks/consumer.py)
- [`tests/tasks/test_runner.py`](../../tests/tasks/test_runner.py)
- [`tests/tasks/test_task_execution.py`](../../tests/tasks/test_task_execution.py)
- [`docs/specifications/06-Resource_Management.md`](../specifications/06-Resource_Management.md)

#### Files To Touch

- [`weft/core/tasks/runner.py`](../../weft/core/tasks/runner.py)
- [`weft/core/agent_runtime.py`](../../weft/core/agent_runtime.py)
- [`weft/core/tasks/consumer.py`](../../weft/core/tasks/consumer.py)
- [`tests/tasks/test_runner.py`](../../tests/tasks/test_runner.py)
- [`tests/tasks/test_agent_execution.py`](../../tests/tasks/test_agent_execution.py) (new)

#### Implementation Notes

- Extend `TaskRunner` to accept agent configuration.
- Add an `agent` branch in `_worker_entry`.
- Delegate actual agent execution to `execute_agent_target(...)`.
- Keep the existing monitor/timeout/cancel loop unchanged as much as possible.
- Do not fork a second runner type unless you hit a real wall.
- Keep `Consumer` as the queue driver. It should still:
  - reserve inbox messages,
  - mark task state transitions,
  - delete from reserved on success,
  - apply reserved policy on error,
  - write final results to outbox.
- Change only the minimum in `Consumer._run_task()` needed to pass agent config
  into `TaskRunner`.
- Do not add interactive behavior for agents.

#### Write These Tests First

- `TaskRunner` executes agent work successfully and returns structured result
- timeout still works for agent target
- cancel still works for agent target
- resource limit enforcement still wraps the agent worker process
- `Consumer` processes an agent work item and writes final outbox result
- reserved queue is cleared on success
- reserved policy is applied on agent runtime failure

#### Invariants To Protect

- `EXEC.1`, `EXEC.2`, `EXEC.3`, `EXEC.4`
- `RES.1` through `RES.6`
- `QUEUE.5`, `QUEUE.6`
- `IDEMP.1`, `IDEMP.2`, `IDEMP.3`

#### Verify

```bash
uv run pytest tests/tasks/test_runner.py tests/tasks/test_agent_execution.py tests/tasks/test_task_execution.py -q
uv run mypy weft
uv run ruff check --fix weft tests
```

#### Done Means

- agent tasks run through the same monitored subprocess path as command/function
  tasks,
- timeouts and limits still behave the same way,
- queue semantics are unchanged.

---

### Task 4: Implement the `llm` Backend

#### Goal

Add one real backend: `llm`.

#### Read First

- [`weft/core/agent_runtime.py`](../../weft/core/agent_runtime.py)
- [`weft/core/agent_tools.py`](../../weft/core/agent_tools.py)
- `llm` Python API already inspected during planning:
  - `llm.get_model(...)`
  - `model.conversation(...)`
  - `conversation.chain(...)`
  - `llm.Tool.function(...)`

#### Files To Touch

- [`weft/core/agent_runtime.py`](../../weft/core/agent_runtime.py)
- [`weft/core/agents/__init__.py`](../../weft/core/agents/__init__.py) (new)
- [`weft/core/agents/backends/__init__.py`](../../weft/core/agents/backends/__init__.py) (new)
- [`weft/core/agents/backends/llm_backend.py`](../../weft/core/agents/backends/llm_backend.py) (new)
- [`tests/core/test_llm_backend.py`](../../tests/core/test_llm_backend.py) (new)
- [`tests/fixtures/llm_test_models.py`](../../tests/fixtures/llm_test_models.py) (new)

#### Implementation Notes

- Use the Python API directly.
- Do not call the `llm` CLI via subprocess.
- Wire built-in `llm` backend registration in this task, not earlier.
- Implement only:
  - `runtime="llm"`
  - `mode="once"`
  - `output_mode="text"`
  - `output_mode="json"`
  - tool kind `python`
- Reject everything else explicitly.
- Map `max_turns` to `chain_limit`.
- For `output_mode="text"`:
  - run one chain and return final text output in the `agent_result`.
- For `output_mode="json"`:
  - if `output_schema` is present, pass it through `llm`'s schema support and
    validate the final structure,
  - if `output_schema` is absent, parse the final response as JSON and return a
    JSON-compatible value or fail clearly if parsing fails.
- Use `conversation.chain(...)` so tool calls stay inside the llm execution
  loop.
- Normalize the final backend result into one stable `agent_result` dict.
- Include lightweight usage / trace fields only if you can populate them
  cleanly. Do not reverse-engineer unstable internal llm state.
- If the selected model does not support a required feature, fail clearly:
  - tools require `supports_tools`,
  - schema output requires `supports_schema`.

#### Important Testing Constraint

Do not patch `llm.get_model`.

For tests, create a small real `llm.Model` subclass in
[`tests/fixtures/llm_test_models.py`](../../tests/fixtures/llm_test_models.py)
and expose it through a tiny real pluggy plugin using `@llm.hookimpl
def register_models(register): ...`.

Register that plugin with llm's actual plugin manager in test setup, for
example through `llm.plugins.pm.register(...)`, and unregister it in teardown.
That is a real integration path, not a mock.

If you need tool and schema coverage, give the test model the corresponding
feature flags and behavior explicitly. Do not fake success by bypassing the
backend.

#### Write These Tests First

- built-in runtime registry resolves `llm` after backend registration lands
- text output path returns `agent_result` with `output_mode="text"`
- json output path returns parsed structured output
- json output without schema returns parsed JSON data
- python tool call is surfaced through llm and executed successfully
- `max_turns` limit is enforced
- unsupported tool kinds fail clearly
- unsupported `output_mode` fails clearly
- `tool_overrides.allow` / `deny` actually narrow llm-visible tools

#### Invariants To Protect

- Unsupported spec combinations must fail loudly
- Backend must stay JSON-friendly at the result boundary
- Backend must not read/write Weft queues directly

#### Verify

```bash
uv run pytest tests/core/test_llm_backend.py -q
uv run mypy weft
uv run ruff check --fix weft tests
```

#### Done Means

- `llm` backend works end-to-end with real tool execution,
- no network is required for deterministic tests,
- the backend remains thin and Weft-owned concerns stay outside it.

---

### Task 5: Add Spec-Driven Black-Box Tests Through the CLI

#### Goal

Prove that agent specs work through the normal CLI and manager path without new
flags.

#### Read First

- [`tests/conftest.py`](../../tests/conftest.py)
- [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py)
- [`tests/cli/test_cli_run.py`](../../tests/cli/test_cli_run.py)
- [`tests/cli/test_cli_spec.py`](../../tests/cli/test_cli_spec.py)
- [`weft/commands/run.py`](../../weft/commands/run.py)
- [`weft/commands/specs.py`](../../weft/commands/specs.py)

#### Files To Touch

- [`tests/cli/test_cli_run.py`](../../tests/cli/test_cli_run.py)
- [`tests/cli/test_cli_spec.py`](../../tests/cli/test_cli_spec.py)
- [`tests/specs/message_flow/test_agent_spawning_transition.py`](../../tests/specs/message_flow/test_agent_spawning_transition.py) (new)
- [`tests/specs/manager_architecture/test_agent_spawn.py`](../../tests/specs/manager_architecture/test_agent_spawn.py) (new)
- [`weft/commands/validate_taskspec.py`](../../weft/commands/validate_taskspec.py)
- [`weft/commands/handlers.py`](../../weft/commands/handlers.py) only if you
  intentionally extract a shared summary helper

#### Implementation Notes

- Use a real agent task spec on disk and run it via existing `weft run --spec`.
- Do not add `weft run --agent` in the MVP.
- The CLI tests should use `WeftTestHarness`.
- Confirm the live validate path before editing:
  - the `weft spec validate --type task` command path is currently wired through
    [`weft/commands/validate_taskspec.py`](../../weft/commands/validate_taskspec.py),
  - [`weft/commands/handlers.py`](../../weft/commands/handlers.py) contains
    near-duplicate summary code but is not the primary CLI entry point.
- Do not edit dead code for symmetry.
- If you need the same agent-summary rendering in both command modules, extract
  one small shared helper in `weft/commands/` and move both callers to it in
  the same change. If you do not need both callers, touch only the live one.
- Update task-spec summary views so `agent` specs show at least:
  - type = `agent`
  - runtime
  - model or `N/A`
- Make the CLI test black-box:
  - write the spec file,
  - run the CLI,
  - wait for completion,
  - inspect outbox or `weft result`,
  - inspect the task log for expected lifecycle events.

#### Write These Tests First

- `weft run --spec agent.json` completes successfully
- `weft result <tid>` returns the agent result payload
- `weft spec validate agent.json` reports valid
- `weft spec create/show/list/delete` works for an agent task spec
- task log contains spawning/running/completed for an agent task
- manager child task launch still uses the spawn-request TID

#### Invariants To Protect

- `MA-2` TID correlation
- `MF-1`, `MF-2`, `MF-6`
- normal CLI behavior for command/function specs must remain unchanged

#### Verify

```bash
uv run pytest tests/cli/test_cli_run.py tests/cli/test_cli_spec.py tests/specs/message_flow/test_agent_spawning_transition.py tests/specs/manager_architecture/test_agent_spawn.py -q
uv run mypy weft
uv run ruff check --fix weft tests
```

#### Done Means

- agent specs work through the normal CLI,
- no new CLI surface area was added,
- manager/task lifecycle behavior remains consistent.

---

### Task 6: Sync the Written Specs to the Implemented MVP

#### Goal

Make the docs match the code that actually ships, especially where the draft
spec is broader than the MVP.

#### Read First

- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
- [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
- [`README.md`](../../README.md)

#### Files To Touch

- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
- [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
- [`README.md`](../../README.md)

#### Implementation Notes

- Update `02-TaskSpec.md` with the implemented agent schema.
- Update `05-Message_Flow_and_State.md` to describe agent tasks as another
  Consumer-managed target type in MVP.
- Update `10-CLI_Interface.md` to say agent tasks run via normal spec execution.
- Update `13-Agent_Runtime.md` to explicitly mark the implemented subset:
  - `llm` only
  - `once` only
  - `python` tools only
  - no approvals
  - no semantic event streaming
- Add one concise README example of running an agent spec from disk.

#### Tests

No new code tests, but run the full suite after docs land because docs-only
changes often happen at the end of a branch and should not become an excuse to
skip the gates.

#### Verify

```bash
uv run pytest
uv run mypy weft
uv run ruff check --fix weft tests
```

#### Done Means

- there is no major mismatch between code and specs,
- the MVP limitations are explicit,
- future work is visible without pretending it is already implemented.

---

### Task 7: Final Quality Pass and Refactor Pass

#### Goal

Remove accidental complexity and ensure the final branch is fit to hand off.

#### Refactor Checklist

- Remove dead helpers and unused imports.
- Collapse duplicated validation logic only if duplication is now real.
- Keep backend-specific behavior out of generic modules.
- Keep TaskSpec schema logic in `taskspec.py`.
- Keep tool resolution out of `TaskRunner`.
- Keep queue semantics out of backend code.
- Do not leave `TODO` markers for major unfinished behavior in production code.
- If a function grew too large, split small helpers without creating a framework.

#### Mandatory Full Gates

Run these exact commands before claiming completion:

```bash
uv run ruff check --fix weft tests
uv run mypy weft
uv run pytest
uv run pytest -m ""
```

If `pytest -m ""` is too slow in the active environment, document the exact
subset you ran and why. Do not just omit it silently.

#### Handoff Checklist

- Summarize the exact MVP behavior implemented.
- List explicitly unsupported agent spec fields and runtime modes.
- List every new module added.
- Call out any places where the draft spec remains broader than the code.

## 6. Suggested File Layout After MVP

The resulting code should look roughly like this:

```text
weft/
├── core/
│   ├── agent_runtime.py
│   ├── agent_tools.py
│   ├── agents/
│   │   ├── __init__.py
│   │   └── backends/
│   │       ├── __init__.py
│   │       └── llm_backend.py
│   ├── tasks/
│   │   ├── consumer.py
│   │   └── runner.py
│   └── taskspec.py
```

This is intentionally modest. Do not add more layers until you need them.

## 7. Things That Will Tempt You and Why They Are Wrong

- "I should add `pydantic_ai` while I’m here."
  Wrong. That doubles the surface area before the first backend is stable.

- "I should add a runtime plugin loader."
  Wrong. A dictionary mapping of built-in runtimes is enough for one backend.

- "I should add persistent agent behavior because the draft spec mentions it."
  Wrong. The prototype path is intentionally one-shot. Persistent agents need a
  separate design and implementation plan that uses existing task lifecycle
  semantics instead of adding `spec.agent.mode`.

- "I should make tools support command, queue, mcp, and agent kinds now."
  Wrong. Only `python` is required for the first slice.

- "I should patch `llm.get_model` because it’s easy."
  Wrong. Register a real local test model instead.

- "I should create an `AgentTask` now because it sounds cleaner."
  Wrong for MVP. That duplicates queue/result/finalization behavior before you
  have proven the simpler path insufficient.

## 8. Fresh-Eyes Review Notes

This plan was reviewed specifically for latent errors and drift. The main
issues found during review, and the corrections already applied here, are:

- **Incorrect early direction**: a dedicated `AgentTask` looked attractive but
  would have bypassed the existing monitored subprocess path. The plan now
  explicitly extends `TaskRunner` instead so resource limits and timeouts remain
  correct.
- **Over-broad MVP risk**: the draft spec was wider than the first safe
  implementation. The plan now fixes the MVP to `llm`, `once`, `python` tools,
  and final results only.
- **Registry-order ambiguity**: an earlier version implied that `llm`
  registration already existed before the `llm` backend task. The plan now
  separates the neutral registry in Task 2 from built-in `llm` registration in
  Task 4.
- **Testing risk**: a naive implementation would likely patch `llm` internals.
  The plan now requires a real local `llm.Model` subclass registered through
  llm's own pluggy hooks instead.
- **CLI drift risk**: new inline flags would create extra UX and parsing work.
  The plan now keeps the first slice spec-driven only.
- **Silent-ignore risk**: unsupported fields are explicitly rejected instead of
  being accepted and ignored.

If, during implementation, you discover that these decisions no longer hold,
stop and re-evaluate before changing direction. Do not "solve" that by
smuggling in a different architecture.

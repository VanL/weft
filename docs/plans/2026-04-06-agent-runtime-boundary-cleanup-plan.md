# Agent Runtime Boundary Cleanup Plan

This document is the implementation plan for the **next** agent-runtime slice:

- prune speculative `spec.agent` fields that are not implemented,
- preserve structured agent messages at the Weft boundary,
- keep the caller-facing queue contract simple,
- and formalize the **private** persistent-session protocol without exposing it
  to callers.

This plan is intentionally narrow. It does **not** redesign Weft, and it does
**not** introduce a second orchestration model. The job is to make the current
agent implementation more honest, more Unix-like, and easier to extend.

The target reader is a strong Python engineer with very little Weft context and
inconsistent instincts around scope and testing. Assume they will overbuild if
not constrained. This document constrains them.

## 0. Scope Lock

Implement exactly these outcomes:

1. `spec.agent` contains only fields that are implemented and exercised.
2. Public inbox/outbox payloads for agent tasks stay simple:
   - strings, or
   - JSON values with no caller-visible Weft protocol envelope.
3. Structured `messages` inputs are preserved by core normalization instead of
   being flattened into one big string.
4. The `llm` backend may do adapter-local conversion if the underlying library
   cannot natively accept the preserved message structure.
5. Persistent per-task sessions use a small **private** protocol between the
   task parent and its agent-session subprocess.
6. The public outbox does **not** expose that private protocol.
7. `weft run` / `weft result` continue to work end-to-end for one-shot and
   persistent agent tasks.

Do **not** implement any of the following in this slice:

- a new backend,
- approvals,
- transcript persistence,
- semantic event streaming to the public outbox,
- attachments,
- a caller-visible agent envelope protocol,
- alias mechanics at the task layer,
- a richer template engine than the current exact-substitution need,
- a new database or restart-safe conversation replay,
- compatibility shims for removed fields.

If work starts pulling toward any of the excluded items above, stop. That is
scope drift.

## 1. Read This First

Read these files in order before editing code:

1. [`AGENTS.md`](../../AGENTS.md)
2. [`docs/specifications/00-Overview_and_Architecture.md`](../specifications/00-Overview_and_Architecture.md)
3. [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
4. [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
5. [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
6. [`docs/specifications/08-Testing_Strategy.md`](../specifications/08-Testing_Strategy.md)
7. [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
8. [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
9. [`weft/core/taskspec.py`](../../weft/core/taskspec.py)
10. [`weft/core/agent_runtime.py`](../../weft/core/agent_runtime.py)
11. [`weft/core/agent_templates.py`](../../weft/core/agent_templates.py)
12. [`weft/core/agents/backends/llm_backend.py`](../../weft/core/agents/backends/llm_backend.py)
13. [`weft/core/tasks/consumer.py`](../../weft/core/tasks/consumer.py)
14. [`weft/core/tasks/runner.py`](../../weft/core/tasks/runner.py)
15. [`weft/core/tasks/sessions.py`](../../weft/core/tasks/sessions.py)
16. [`weft/commands/run.py`](../../weft/commands/run.py)
17. [`weft/commands/result.py`](../../weft/commands/result.py)
18. [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py)
19. [`tests/conftest.py`](../../tests/conftest.py)
20. [`tests/fixtures/llm_test_models.py`](../../tests/fixtures/llm_test_models.py)

If you are new to project style, also inspect SimpleBroker locally:

1. `/Users/van/.venv/lib/python3.13/site-packages/simplebroker/sbqueue.py`
2. `/Users/van/.venv/lib/python3.13/site-packages/simplebroker/watcher.py`
3. `/Users/van/.venv/lib/python3.13/site-packages/simplebroker/helpers.py`

You are not copying SimpleBroker. You are learning the house style:

- direct names,
- small modules,
- queue-first thinking,
- real cleanup,
- real integration tests.

## 2. Engineering Rules

These are mandatory.

### 2.1 Code Style

- Use `from __future__ import annotations` in every new Python module.
- Keep imports at module top. No deferred imports.
- Use `collections.abc` abstract types.
- Use `Path`, not `os.path`.
- Add docstrings with spec references when behavior is non-trivial.
- Add comments rarely and only when the code is genuinely hard to parse.
- Keep modules single-purpose.

### 2.2 Complexity Control

- Do not invent a generic “agent platform.”
- Do not build a public event protocol.
- Do not add speculative abstractions for future backends.
- Do not let one backend’s limitations leak into core normalization.
- Prefer one obvious helper over a small framework.

### 2.3 DRY and YAGNI

- Be DRY where duplication already exists and hurts readability.
- Be YAGNI where the duplication is hypothetical.
- Remove unused schema, do not wrap it in “reserved for future use.”
- Reuse existing queue/log/task semantics before adding new ones.

### 2.4 Testing

- Prefer red-green-refactor for each task below.
- Do not mock `simplebroker.Queue`.
- Do not mock `llm.get_model`.
- Do not mock state-log transitions.
- Use real queues, real task processes, and `WeftTestHarness`.
- Write both white-box and black-box tests.
- Poll real queues/logs instead of sleeping blindly.

### 2.5 Verification Discipline

Run these **sequentially**, not in parallel:

```bash
uv run --extra dev pytest
uv run --extra dev pytest -m ""
uv run --extra dev ruff check --fix weft tests
uv run --extra dev mypy weft
```

This repository can recreate the shared virtualenv if multiple `uv run ...`
commands overlap. Do not do that.

## 3. Fixed Design Decisions

These decisions are not open during implementation.

### 3.1 Lifecycle Stays at the Task Layer

Do not add or keep any agent-specific lifecycle field.

- `spec.persistent` stays the lifecycle control.
- `spec.agent` is runtime configuration only.

### 3.2 Public Boundary Must Be Protocol-Light

Callers must not need to know about a Weft-private request/response protocol.

Public agent task inbox payloads may be:

- a string, or
- a JSON object using the documented work-item fields.

Public outbox payloads may be:

- a string, or
- a JSON value.

Callers should not need to interpret:

- `{"type": "execute", ...}`,
- `{"type": "result", ...}`,
- internal ready/final markers,
- or any synthetic `agent_result` wrapper.

The only public coordination markers remain ordinary Weft task/log semantics:

- queue order,
- outbox contents,
- `work_item_completed` / `work_completed`,
- `work_failed`,
- `work_timeout`,
- `work_limit_violation`.

### 3.3 Private Session Protocol Is Allowed

The persistent session worker path **should** use a private explicit protocol.
That protocol is implementation detail between the task parent and the
agent-session subprocess.

It should be:

- local to this subsystem,
- stringifiable/JSON-friendly,
- explicit,
- versioned enough to evolve safely,
- and hidden from callers.

### 3.4 Core Must Preserve Structured Messages

Core normalization should preserve message structure. It should not flatten
`messages` into one big prompt string.

If the `llm` backend cannot natively consume structured messages, the adapter
may perform a local conversion there. That compromise belongs in the adapter,
not in core normalization.

### 3.5 No Alias Semantics in This Layer

Aliases already exist elsewhere. Do not invent new alias logic here.

If a caller passes a string or JSON reference that other Weft layers already
understand, this layer should pass it through as ordinary payload data. The
agent runtime layer does not own alias semantics.

### 3.6 Remove Unsupported Schema Instead of Rejecting It Later

If a field exists only so the backend can raise “not supported,” remove the
field from the public schema in this slice.

That applies to:

- `entrypoint`
- `approval_policy`
- `persist_transcript`
- `stream_events`
- any `output_mode` values not implemented end-to-end

## 4. Target End State

This section defines the concrete target shape.

### 4.1 `spec.agent` Fields

After this cleanup, `spec.agent` should contain only:

- `runtime`
- `model`
- `instructions`
- `templates`
- `tools`
- `output_mode`
- `output_schema`
- `max_turns`
- `options`
- `conversation_scope`
- `runtime_config`

Recommended implemented `output_mode` values for this slice:

- `text`
- `json`
- `messages`

Remove `native`.

`output_schema` remains valid only with `output_mode="json"`.

### 4.2 Public Work Item Shapes

Public inbox work items should support exactly one of:

1. raw string body
2. JSON object with `"task": <string>`
3. JSON object with `"messages": <list>`
4. JSON object with `"template": <name>` and optional `"template_args"`

Other supported top-level fields remain:

- `metadata`
- `tool_overrides`

Do not add per-message model overrides, instruction overrides, or ad hoc tool
definitions in the inbox. Static config stays in TaskSpec.

### 4.3 Public Message Item Shape

For this slice, message items should be intentionally simple.

Accepted input message list items:

- a string, or
- a JSON object with:
  - `role`
  - `content`

Do not add tool-call or attachment sub-protocols here.

If `content` is not a string, it may be any JSON-serializable value. Preserve
it as structured data in core.

### 4.4 Public Outbox Semantics

Public outbox semantics for agent tasks:

- `output_mode="text"`:
  - write one or more string payloads to outbox in order
- `output_mode="json"`:
  - write one or more JSON payloads to outbox in order
- `output_mode="messages"`:
  - write one or more JSON message objects to outbox in order

Do **not** wrap these in a public `agent_result` envelope.

Task completion is communicated by normal task/log events, not by a new public
final marker.

### 4.5 `weft result` Aggregation

`weft result` and `weft run --wait` need deterministic aggregation rules.

Use these rules:

- if exactly one outbox payload is produced and it is a string:
  - return that string
- if exactly one outbox payload is produced and it is JSON:
  - return that JSON value
- if multiple outbox payloads are produced:
  - return a list preserving order

Do not try to be cleverer than that.

This keeps the public contract simple and does not force callers to learn a
Weft-specific wrapper shape.

## 5. Task Breakdown

Implement the work in the order below.

---

## Task 1. Update the Specs Before Code

### Goal

Make the specs match the intended cleanup before changing implementation.

### Read First

- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
- [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)

### Files to Touch

- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
- [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)

### Required Changes

- Remove unsupported `spec.agent` fields from the canonical schema examples.
- State explicitly that public outbox payloads are strings or JSON values.
- State explicitly that structured `messages` are preserved by core.
- State explicitly that the persistent session protocol is private.
- Update any MVP status notes that still describe one-shot-only behavior if the
  code already supports persistent tasks.

### Testing

No code tests required for this task alone. This is a docs-only commit-sized
task. Review carefully.

### Gate

Manual review only. Do not move on until the docs no longer claim unsupported
public fields exist.

---

## Task 2. Write the Red Tests for Schema Pruning

### Goal

Make the tests fail for the current overly broad schema.

### Read First

- [`weft/core/taskspec.py`](../../weft/core/taskspec.py)
- [`tests/specs/taskspec/test_agent_taskspec.py`](../../tests/specs/taskspec/test_agent_taskspec.py)
- [`tests/taskspec/test_taskspec.py`](../../tests/taskspec/test_taskspec.py)
- [`tests/taskspec/fixtures.py`](../../tests/taskspec/fixtures.py)

### Files to Touch

- [`tests/specs/taskspec/test_agent_taskspec.py`](../../tests/specs/taskspec/test_agent_taskspec.py)
- [`tests/taskspec/test_taskspec.py`](../../tests/taskspec/test_taskspec.py)
- [`tests/taskspec/fixtures.py`](../../tests/taskspec/fixtures.py)

### Red Tests to Add First

- constructing an agent spec with `entrypoint` fails
- constructing an agent spec with `approval_policy` fails
- constructing an agent spec with `persist_transcript` fails
- constructing an agent spec with `stream_events` fails
- constructing an agent spec with `output_mode="native"` fails
- constructing an agent spec with the kept fields still succeeds
- `output_schema` still requires `output_mode="json"`

### Important Notes

- Do not use mocks.
- Use real `TaskSpec.model_validate(...)`.
- Keep fixtures small and explicit.
- Make sure fixture helpers stop generating removed fields.

### Suggested Command

```bash
uv run --extra dev pytest \
  tests/specs/taskspec/test_agent_taskspec.py \
  tests/taskspec/test_taskspec.py -n 0
```

---

## Task 3. Implement Schema Pruning in `taskspec.py`

### Goal

Remove unsupported public fields from `AgentSection`.

### Read First

- [`weft/core/taskspec.py`](../../weft/core/taskspec.py)

### Files to Touch

- [`weft/core/taskspec.py`](../../weft/core/taskspec.py)
- [`tests/taskspec/fixtures.py`](../../tests/taskspec/fixtures.py)

### Required Changes

- Remove these fields from `AgentSection`:
  - `entrypoint`
  - `approval_policy`
  - `persist_transcript`
  - `stream_events`
- Reduce `output_mode` to the implemented set for this slice.
- Update any validation logic that still references removed fields.
- Keep `conversation_scope` validation tied to `spec.persistent`.

### Invariants

- `spec.agent` stays immutable after TaskSpec creation.
- `type="agent"` still requires `spec.agent`.
- `function_target` and `process_target` remain invalid for `type="agent"`.

### Suggested Command

```bash
uv run --extra dev pytest \
  tests/specs/taskspec/test_agent_taskspec.py \
  tests/taskspec/test_taskspec.py -n 0
```

---

## Task 4. Write the Red Tests for Structured Message Preservation

### Goal

Prove that core currently loses message structure, then lock in the desired
behavior.

### Read First

- [`weft/core/agent_runtime.py`](../../weft/core/agent_runtime.py)
- [`tests/core/test_agent_runtime.py`](../../tests/core/test_agent_runtime.py)

### Files to Touch

- [`tests/core/test_agent_runtime.py`](../../tests/core/test_agent_runtime.py)

### Red Tests to Add First

- raw string work item remains a string task payload
- `{"task": "hello"}` remains a string task payload
- `{"messages": [...]}` preserves structured messages instead of flattening
- string items inside `messages` are preserved
- object items inside `messages` preserve `role` and structured `content`
- unknown message keys still fail
- template rendering still returns a string payload plus instructions
- tool overrides and metadata still normalize correctly

### Design Constraint

The normalized form should preserve structure in a backend-neutral way.

Do **not** flatten in core just because `llm` may need flattening later.

### Suggested Command

```bash
uv run --extra dev pytest tests/core/test_agent_runtime.py -n 0
```

---

## Task 5. Implement Structured Message Preservation in Core

### Goal

Change the normalized work-item type so core preserves strings vs structured
message lists.

### Read First

- [`weft/core/agent_runtime.py`](../../weft/core/agent_runtime.py)
- [`weft/core/agent_templates.py`](../../weft/core/agent_templates.py)

### Files to Touch

- [`weft/core/agent_runtime.py`](../../weft/core/agent_runtime.py)
- possibly a small new module if needed:
  - [`weft/core/agent_messages.py`](../../weft/core/agent_messages.py)

### Recommended Shape

Use an explicit normalized representation, for example:

- `NormalizedAgentMessage`
- `NormalizedAgentWorkItem.content: str | tuple[NormalizedAgentMessage | str, ...]`

Do **not** store the structured message list as untyped `Any` if a small,
readable type will do.

### Implementation Rules

- Keep the public accepted shape small.
- Preserve input order exactly.
- Preserve structured `content` values as JSON-friendly values.
- Leave template rendering unchanged except for the normalized container shape.
- Remove `_flatten_messages(...)` from core, or move any remaining conversion
  logic into the backend adapter.

### DRY Note

If a new tiny helper module makes the normalized message type clearer, that is
good. Do not create a hierarchy of message classes.

### Suggested Command

```bash
uv run --extra dev pytest tests/core/test_agent_runtime.py -n 0
```

---

## Task 6. Write the Red Tests for Public Outbox Semantics

### Goal

Lock in the caller-visible queue/result contract before implementation.

### Read First

- [`weft/core/tasks/consumer.py`](../../weft/core/tasks/consumer.py)
- [`weft/commands/run.py`](../../weft/commands/run.py)
- [`weft/commands/result.py`](../../weft/commands/result.py)
- [`tests/tasks/test_agent_execution.py`](../../tests/tasks/test_agent_execution.py)
- [`tests/commands/test_result.py`](../../tests/commands/test_result.py)
- [`tests/commands/test_run.py`](../../tests/commands/test_run.py)
- [`tests/cli/test_cli_run.py`](../../tests/cli/test_cli_run.py)

### Files to Touch

- [`tests/tasks/test_agent_execution.py`](../../tests/tasks/test_agent_execution.py)
- [`tests/commands/test_result.py`](../../tests/commands/test_result.py)
- [`tests/commands/test_run.py`](../../tests/commands/test_run.py)
- [`tests/cli/test_cli_run.py`](../../tests/cli/test_cli_run.py)

### Red Tests to Add First

- one-shot `text` agent task writes a plain string to outbox, not an
  `agent_result` wrapper
- one-shot `json` agent task writes a plain JSON payload to outbox
- `output_mode="messages"` writes JSON message objects to outbox
- if multiple payloads are written to outbox, `weft result` returns a list in
  order
- `weft run --spec` with `--wait` returns:
  - a string for one string payload
  - a JSON object for one JSON payload
  - a list for multiple payloads
- persistent per-task continuation still works across multiple inbox items
- direct queue readers can read raw outbox payloads without knowing a private
  protocol

### Important Constraint

Do not write tests that inspect private session messages. This task is about
the public queue contract.

### Suggested Command

```bash
uv run --extra dev pytest \
  tests/tasks/test_agent_execution.py \
  tests/commands/test_result.py \
  tests/commands/test_run.py \
  tests/cli/test_cli_run.py -n 0
```

---

## Task 7. Implement Public Outbox Cleanup

### Goal

Stop emitting the public `agent_result` wrapper and emit plain queue payloads
instead.

### Read First

- [`weft/core/agent_runtime.py`](../../weft/core/agent_runtime.py)
- [`weft/core/tasks/consumer.py`](../../weft/core/tasks/consumer.py)
- [`weft/commands/run.py`](../../weft/commands/run.py)
- [`weft/commands/result.py`](../../weft/commands/result.py)

### Files to Touch

- [`weft/core/agent_runtime.py`](../../weft/core/agent_runtime.py)
- [`weft/core/agents/backends/llm_backend.py`](../../weft/core/agents/backends/llm_backend.py)
- [`weft/core/tasks/consumer.py`](../../weft/core/tasks/consumer.py)
- [`weft/commands/run.py`](../../weft/commands/run.py)
- [`weft/commands/result.py`](../../weft/commands/result.py)
- [`tests/core/test_llm_backend.py`](../../tests/core/test_llm_backend.py)

### Required Changes

- Replace or narrow `AgentResult` so it is no longer the public outbox format.
- Have the runtime/backend return an internal execution result that can contain:
  - ordered public outbox payloads
  - optional internal metadata such as usage/tool trace
- Update the built-in `llm` adapter in the same step so the runtime interface
  change lands coherently.
- Teach `Consumer` to emit one or many public outbox payloads in order.
- Keep existing task-completion log events as the completion signal.
- Update `weft run --wait` and `weft result` to aggregate outbox payloads using
  the rules in section 4.5.

### Design Rule

If internal metadata like usage or tool trace is still useful, keep it internal
to the runtime result object or logs. Do not put it back into a caller-visible
wrapper in this slice.

### Suggested Command

```bash
uv run --extra dev pytest \
  tests/core/test_llm_backend.py \
  tests/tasks/test_agent_execution.py \
  tests/commands/test_result.py \
  tests/commands/test_run.py \
  tests/cli/test_cli_run.py -n 0
```

---

## Task 8. Write the Red Tests for the Private Session Protocol

### Goal

Make the persistent-session boundary explicit and testable without leaking it
into public queue semantics.

### Read First

- [`weft/core/tasks/runner.py`](../../weft/core/tasks/runner.py)
- [`weft/core/tasks/sessions.py`](../../weft/core/tasks/sessions.py)
- [`tests/tasks/test_runner.py`](../../tests/tasks/test_runner.py)
- [`tests/tasks/test_agent_execution.py`](../../tests/tasks/test_agent_execution.py)

### Files to Touch

- [`tests/tasks/test_runner.py`](../../tests/tasks/test_runner.py)
- optionally a new targeted test file:
  - [`tests/tasks/test_agent_session_protocol.py`](../../tests/tasks/test_agent_session_protocol.py)

### Red Tests to Add First

- session startup emits/accepts a ready message via the private protocol
- execute request returns a result through the private protocol
- startup error is surfaced cleanly
- invalid private message type is ignored or rejected deterministically
- session stop closes cleanly
- cancellation, timeout, and limit-violation paths still work with the private
  protocol in place

### Important Constraint

These tests may be white-box, but they should still use real spawned processes.
Do not replace the session worker with a mock object.

### Suggested Command

```bash
uv run --extra dev pytest \
  tests/tasks/test_runner.py \
  tests/tasks/test_agent_execution.py -n 0
```

---

## Task 9. Implement the Private Session Protocol

### Goal

Replace the implicit ad hoc dict exchange with a small private, explicit
protocol module.

### Read First

- [`weft/core/tasks/runner.py`](../../weft/core/tasks/runner.py)
- [`weft/core/tasks/sessions.py`](../../weft/core/tasks/sessions.py)

### Files to Touch

- new module, recommended:
  - [`weft/core/tasks/agent_session_protocol.py`](../../weft/core/tasks/agent_session_protocol.py)
- [`weft/core/tasks/runner.py`](../../weft/core/tasks/runner.py)
- [`weft/core/tasks/sessions.py`](../../weft/core/tasks/sessions.py)

### Recommended Shape

Keep it boring:

- request helpers for `execute` and `stop`
- response helpers for `ready`, `result`, and `startup_error`
- validation/parsing helpers in one place

Use plain JSON-friendly dict payloads. Do not introduce a serialization library
or complex inheritance tree.

### Critical Rule

This protocol is private. It must not appear on task inbox/outbox queues. It is
only for the in-memory multiprocessing queues between parent task and child
session worker.

### Suggested Command

```bash
uv run --extra dev pytest \
  tests/tasks/test_runner.py \
  tests/tasks/test_agent_execution.py -n 0
```

---

## Task 10. Update the `llm` Backend Without Re-Leaking the Protocol

### Goal

Keep the `llm` backend thin while adapting structured messages locally if
needed. This task assumes Task 7 already changed the backend result interface.

### Read First

- [`weft/core/agents/backends/llm_backend.py`](../../weft/core/agents/backends/llm_backend.py)
- [`tests/core/test_llm_backend.py`](../../tests/core/test_llm_backend.py)
- [`tests/fixtures/llm_test_models.py`](../../tests/fixtures/llm_test_models.py)

### Files to Touch

- [`weft/core/agents/backends/llm_backend.py`](../../weft/core/agents/backends/llm_backend.py)
- [`tests/core/test_llm_backend.py`](../../tests/core/test_llm_backend.py)
- [`tests/fixtures/llm_test_models.py`](../../tests/fixtures/llm_test_models.py)

### Required Changes

- Remove validation of schema fields that no longer exist.
- Accept the new normalized structured message form.
- If `llm` cannot consume structured messages natively, convert them **inside
  this adapter**.
- Return public output payloads in the format required by section 4.4.
- Preserve `conversation_scope="per_task"` behavior for persistent sessions.

### Tests to Add or Update

- structured `messages` input works for one-shot `llm` execution
- structured `messages` input works for persistent per-task continuation
- `text`, `json`, and `messages` output modes produce the expected public
  payloads
- `tools`, `instructions`, `templates`, and `options` still flow through
  correctly

### Important Note

Do not make the core normalization depend on what `llm` can or cannot do.

### Suggested Command

```bash
uv run --extra dev pytest tests/core/test_llm_backend.py -n 0
```

---

## Task 11. Clean Up the CLI/Result Surface

### Goal

Make the CLI match the cleaned public boundary.

### Read First

- [`weft/commands/run.py`](../../weft/commands/run.py)
- [`weft/commands/result.py`](../../weft/commands/result.py)
- [`tests/commands/test_run.py`](../../tests/commands/test_run.py)
- [`tests/commands/test_result.py`](../../tests/commands/test_result.py)
- [`tests/cli/test_cli_run.py`](../../tests/cli/test_cli_run.py)

### Files to Touch

- [`weft/commands/run.py`](../../weft/commands/run.py)
- [`weft/commands/result.py`](../../weft/commands/result.py)
- [`tests/commands/test_run.py`](../../tests/commands/test_run.py)
- [`tests/commands/test_result.py`](../../tests/commands/test_result.py)
- [`tests/cli/test_cli_run.py`](../../tests/cli/test_cli_run.py)

### Required Changes

- `weft run --wait` should render the aggregated public result shape.
- `weft result` should render the aggregated public result shape.
- When `--json` is requested, wrap only the CLI response envelope:
  - `tid`
  - `status`
  - `result`

Do not reintroduce an `agent_result` payload in `result`.

### Invariants

- One payload returns one value, not a one-element list.
- Multiple payloads return an ordered list.
- Completion still relies on task/log semantics, not outbox sentinels.

### Suggested Command

```bash
uv run --extra dev pytest \
  tests/commands/test_run.py \
  tests/commands/test_result.py \
  tests/cli/test_cli_run.py -n 0
```

---

## Task 12. Final Docs and Cleanup

### Goal

Leave the repository consistent.

### Files to Touch

- any spec docs changed in Task 1
- README examples only if they currently show removed public fields or wrapper
  payloads

### Required Checks

- remove any dead code from the old public `agent_result` path
- remove tests that only existed for removed schema fields
- update fixture names and docstrings if they still reference removed fields

### Anti-Pattern Warning

Do not leave “temporary” compatibility code behind. The user explicitly does
not want compatibility paths.

---

## 6. Test Strategy Summary

These are the most important invariants to verify with real tests.

### 6.1 Schema Invariants

- unsupported agent fields are rejected at validation time
- supported fields remain immutable after TaskSpec creation
- `conversation_scope="per_task"` still requires `spec.persistent=true`

### 6.2 Normalization Invariants

- strings remain strings
- structured `messages` remain structured
- templates render only when requested
- unknown fields fail loudly
- tool overrides remain deterministic

### 6.3 Public Queue Invariants

- public inbox does not require a private protocol
- public outbox does not expose a private protocol
- outbox order is preserved
- one output stays scalar
- multiple outputs aggregate to ordered list

### 6.4 Persistent Session Invariants

- persistent per-task conversation retains context across inbox messages
- timeout/cancel/limit logic still works
- shutdown closes the private session worker cleanly

### 6.5 CLI Invariants

- `weft run --spec ... --wait` returns the same public value shape that direct
  outbox readers would observe
- `weft result` returns the same value shape
- `--json` wraps the CLI response, not the outbox payload

## 7. Suggested Execution Order

Use this exact order unless you find a concrete blocker:

1. Task 1: docs/spec correction
2. Task 2: schema red tests
3. Task 3: schema implementation
4. Task 4: normalization red tests
5. Task 5: normalization implementation
6. Task 6: public outbox red tests
7. Task 7: public outbox implementation
8. Task 8: private protocol red tests
9. Task 9: private protocol implementation
10. Task 10: `llm` adapter update
11. Task 11: CLI/result cleanup
12. Task 12: final docs/cleanup

Do not start by “refactoring for cleanliness.” Start with failing tests around
observable behavior.

## 8. Final Gates

Before calling the work done, run:

```bash
uv run --extra dev pytest
uv run --extra dev pytest -m ""
uv run --extra dev ruff check --fix weft tests
uv run --extra dev mypy weft
```

If any gate fails:

- fix the failure,
- rerun the failed gate,
- rerun at least the relevant targeted tests,
- then rerun the full gate that failed.

Do not assume a `-n 0` pass implies the parallel suite is good.

## 9. Self-Review Checklist

Use this before handing off or merging.

- Is `spec.agent` smaller and more honest than before?
- Can a caller use agent tasks without learning a private protocol?
- Does core preserve `messages` structure?
- Is any unavoidable lossy conversion isolated to the backend adapter?
- Is the private session protocol explicit and local?
- Are direct queue readers and CLI users seeing the same public result shapes?
- Are there any compatibility branches, aliases, or “future” fields left over?
- Did the tests use real queues and real processes instead of mocks?
- Did you keep module purposes clear and cyclomatic complexity reasonable?

## 10. Review Notes

This plan was reviewed against the current implementation before being written.
The main ambiguity risks were:

1. accidentally reintroducing a public wrapper protocol while “simplifying”
   results,
2. flattening structured messages again in core because the `llm` adapter is
   easier that way,
3. moving alias semantics into the agent layer,
4. keeping removed fields as soft-rejected compatibility baggage,
5. forgetting that `weft result` and direct outbox readers must observe the
   same public payload semantics.

This plan resolves those risks by fixing the design up front:

- public boundary simple,
- private protocol allowed but hidden,
- structured messages preserved in core,
- unsupported fields removed outright,
- result aggregation rules explicit.

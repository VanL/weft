# Delegated Agent Runtime Phase 1 Implementation Plan

This plan implements Phase 1 from
[`docs/specifications/13A-Agent_Runtime_Planned.md`](../specifications/13A-Agent_Runtime_Planned.md):
one-shot delegated agent execution through an external provider CLI while
keeping Weft as the system of record for task lifecycle, queues, and durable
audit state.

This is risky boundary work. It touches the durable spine:

`TaskSpec -> Manager -> Consumer -> TaskRunner -> runner -> agent runtime`

Implementation note:

- the shipped Phase 1 surface was widened after plan review to match the
  concrete provider names and non-interactive dispatch strings already used in
  `../agent-mcp`
- where this plan says "codex-only" or implies a universal `model` requirement,
  the implemented contract supersedes that language: Phase 1 now covers
  `claude_code`, `codex`, `gemini`, `opencode`, and `qwen`, and
  `spec.agent.model` is only valid for providers whose concrete CLI surface
  supports model override

Write it as a narrow slice. The tempting wrong moves are:

- turning this into a second agent platform or scheduler,
- widening `spec.agent` too early with fields only one runtime needs,
- adding persistent session logic in a one-shot slice,
- smearing provider-specific CLI flags across core runtime code,
- storing provider-native transcript state as truth,
- and mocking away the real subprocess and queue path.

Do not do those things.

## Goal

Add one new Weft agent runtime, `provider_cli`, for bounded one-shot execution
through a verified external agent CLI. The new runtime must support explicit
provider selection, explicit resolver and tool-profile selection, and
Weft-owned persistence of execution metadata and referenced-input artifacts,
while keeping the current queue model, task lifecycle, host runner, and public
outbox contract unchanged.

Mental model for the implementer:

- Weft still owns task identity, task state, queue truth, timeout, stop/kill,
  and audit.
- The delegated provider owns only the inner reasoning run for one work item.
- Resolver output is grounded context, not durable truth.
- Provider-native session state does not exist in this phase.

## Scope Lock

Implement exactly this slice:

1. add `spec.agent.runtime="provider_cli"` as a new built-in runtime
2. support one-shot agent tasks only for that runtime
3. support explicit provider selection via `spec.agent.runtime_config.provider`
4. support explicit resolver and tool-profile selection via
   `spec.agent.runtime_config.resolver_ref` and
   `spec.agent.runtime_config.tool_profile_ref`
5. add provider executable resolution and preflight verification
6. keep public output on the existing outbox path
7. persist internal execution metadata and referenced-input artifacts through
   existing Weft log/event machinery
8. ship built-in provider adapters for the provider types already detected in
   `../agent-mcp`: `claude_code`, `codex`, `gemini`, `opencode`, and `qwen`

Do not implement any of the following in this slice:

- persistent or interactive delegated sessions
- provider-native continuation or session IDs
- Weft-owned durable threads or transcript replay
- build-backed containers or new runner environments
- MCP bridging
- nested agent tools
- shell/file capability surfacing beyond what the chosen provider CLI already
  does in its own one-shot mode
- a second public CLI family for provider discovery or chat attachment
- structured `json` or `messages` output for `provider_cli`
- direct use of `spec.agent.tools` by `provider_cli`

If the work starts pulling toward any item in the exclusion list, stop and
write a follow-on plan instead.

## Source Documents

Primary specs:

- [`docs/specifications/13A-Agent_Runtime_Planned.md`](../specifications/13A-Agent_Runtime_Planned.md)
  [AR-A1], [AR-A3], [AR-A5], [AR-A7], [AR-A8]
- [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
  [AR-0], [AR-2], [AR-3], [AR-4], [AR-5], [AR-7]
- [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md)
  [CC-2.3], [CC-2.4], [CC-3]
- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
  [TS-1], [TS-1.3]
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-2], [MF-3], [MF-5]
- [`docs/specifications/06-Resource_Management.md`](../specifications/06-Resource_Management.md)
  [RM-5], [RM-5.1]
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [IMMUT.1], [IMMUT.2], [STATE.1], [STATE.2], [QUEUE.1], [QUEUE.2],
  [QUEUE.3], [OBS.3], [IMPL.5], [IMPL.6]
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
  [CLI-1.4.1]
- [`docs/specifications/01A-Core_Components_Planned.md`](../specifications/01A-Core_Components_Planned.md)
- [`docs/specifications/06A-Resource_Management_Planned.md`](../specifications/06A-Resource_Management_Planned.md)

Existing plans and local guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/README.md`](../agent-context/README.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)
- [`docs/plans/2026-04-06-persistent-agent-runtime-implementation-plan.md`](./2026-04-06-persistent-agent-runtime-implementation-plan.md)
- [`docs/plans/2026-04-06-runner-extension-point-plan.md`](./2026-04-06-runner-extension-point-plan.md)

## Context and Key Files

Files that will likely change in this slice:

- `docs/specifications/13-Agent_Runtime.md`
- `docs/specifications/13A-Agent_Runtime_Planned.md`
- `docs/specifications/10-CLI_Interface.md`
- `weft/ext.py`
- `weft/core/taskspec.py`
- `weft/core/agent_runtime.py`
- `weft/core/agents/backends/__init__.py`
- `weft/commands/validate_taskspec.py`
- recommended new modules:
  - `weft/core/imports.py`
  - `weft/core/agent_resolution.py`
  - `weft/core/agent_validation.py`
  - `weft/core/agents/provider_registry.py`
  - `weft/core/agents/backends/provider_cli_backend.py`
- tests in:
  - `tests/taskspec/test_taskspec.py`
  - `tests/core/test_agent_runtime.py`
  - recommended new focused tests:
    - `tests/core/test_agent_resolution.py`
    - `tests/core/test_agent_validation.py`
    - `tests/core/test_provider_cli_backend.py`
  - `tests/tasks/test_agent_execution.py`
  - `tests/cli/test_cli_validate.py`
  - recommended new fixture module:
    - `tests/fixtures/provider_cli_fixture.py`

Read first:

- `weft/core/taskspec.py`
  - owns TaskSpec schema and resolved-shape invariants
- `weft/core/agent_runtime.py`
  - owns runtime registration, work-item normalization, and internal result
    shape
- `weft/core/agents/backends/llm_backend.py`
  - current built-in runtime adapter; this is the closest pattern to copy
- `weft/core/tasks/consumer.py`
  - owns work-item lifecycle, outbox writes, and task-log boundary events
- `weft/core/tasks/runner.py`
  - the only supported façade for execution through the configured runner
- `weft/core/runners/host.py`
  - current monitored subprocess boundary; Phase 1 should rely on it, not
    bypass it
- `weft/ext.py`
  - current public extension-contract surface
- `weft/core/runner_validation.py`
  - current pattern for schema-only vs load vs preflight execution validation
- `tests/helpers/weft_harness.py`
  - real queue/process test harness; reuse it
- `tests/tasks/test_agent_execution.py`
  - current end-to-end proof for TaskRunner and Consumer agent behavior
- `tests/core/test_agent_runtime.py`
  - current focused runtime surface tests

Shared paths and helpers to reuse:

- `register_agent_runtime()` / `get_agent_runtime()` in
  `weft/core/agent_runtime.py`
- the existing host runner and monitored subprocess path in
  `weft/core/tasks/runner.py` and `weft/core/runners/host.py`
- `BaseTask._report_state_change()` in `weft/core/tasks/base.py` for durable
  internal metadata persistence
- existing `validate_taskspec_runner(...)` shape in
  `weft/core/runner_validation.py` and `weft/commands/validate_taskspec.py`
- the existing `module:function` import-ref pattern already used in
  `weft/core/targets.py` and `weft/core/agent_tools.py`

Current structure that matters:

- `Consumer._run_task()` is the only load-bearing split between one-shot and
  persistent agent execution. Phase 1 must stay on the one-shot side.
- `execute_agent_target()` is where normalized agent work reaches the runtime
  adapter.
- `AgentExecutionResult` already has `metadata`, `usage`, `tool_trace`, and
  `artifacts` fields, but current consumer code only emits public outputs.
- `cmd_validate_taskspec()` currently knows how to do schema validation and
  runner load/preflight validation, but nothing parallel for agent runtimes.
- built-in agent runtimes are registered by importing `weft.core.agents`.

## Read This First

Read these in order before editing:

1. [`AGENTS.md`](../../AGENTS.md)
2. [`docs/agent-context/README.md`](../agent-context/README.md)
3. [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
4. [`docs/agent-context/principles.md`](../agent-context/principles.md)
5. [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
6. [`docs/specifications/13A-Agent_Runtime_Planned.md`](../specifications/13A-Agent_Runtime_Planned.md)
7. [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
8. [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
9. [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
10. [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
11. [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
12. [`weft/core/taskspec.py`](../../weft/core/taskspec.py)
13. [`weft/core/agent_runtime.py`](../../weft/core/agent_runtime.py)
14. [`weft/core/agents/__init__.py`](../../weft/core/agents/__init__.py)
15. [`weft/core/agents/backends/llm_backend.py`](../../weft/core/agents/backends/llm_backend.py)
16. [`weft/core/tasks/consumer.py`](../../weft/core/tasks/consumer.py)
17. [`weft/core/tasks/runner.py`](../../weft/core/tasks/runner.py)
18. [`weft/core/runners/host.py`](../../weft/core/runners/host.py)
19. [`weft/ext.py`](../../weft/ext.py)
20. [`weft/core/runner_validation.py`](../../weft/core/runner_validation.py)
21. [`weft/commands/validate_taskspec.py`](../../weft/commands/validate_taskspec.py)
22. [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py)
23. [`tests/core/test_agent_runtime.py`](../../tests/core/test_agent_runtime.py)
24. [`tests/tasks/test_agent_execution.py`](../../tests/tasks/test_agent_execution.py)
25. [`tests/cli/test_cli_validate.py`](../../tests/cli/test_cli_validate.py)

Also inspect these local style references before creating new extension-facing
types or plugin-like loaders:

1. [`weft/_runner_plugins.py`](../../weft/_runner_plugins.py)
2. [`../simplebroker/simplebroker/_backend_plugins.py`](../../../simplebroker/simplebroker/_backend_plugins.py)
3. [`../simplebroker/simplebroker/ext.py`](../../../simplebroker/simplebroker/ext.py)

You are not copying those files. You are learning the local bar:

- small contracts
- explicit names
- one resolver path
- clear install or availability errors
- no accidental second control plane

## Comprehension Questions

If the implementer cannot answer these before editing, they are not ready.

1. Which code path currently decides whether an agent task uses a live session
   subprocess or a fresh per-message execution?
   Answer: `_uses_agent_session()` and `_run_task()` in
   `weft/core/tasks/consumer.py`.

2. Where does one-shot agent work actually execute relative to resource limits
   and cancellation?
   Answer: inside the runner-owned worker subprocess launched through
   `TaskRunner` and the host runner. The runtime adapter runs inside that
   boundary.

3. Which existing mechanism can carry durable internal execution metadata
   without changing the public outbox contract?
   Answer: `BaseTask._report_state_change()` writing to `weft.log.tasks`.

4. Which current validation flow already distinguishes pure schema validation
   from execution-environment checks?
   Answer: `validate_taskspec_runner(...)` plus
   `cmd_validate_taskspec(..., load_runner=..., preflight=...)`.

5. Why should Phase 1 avoid editing `weft/core/tasks/sessions.py`,
   `weft/core/tasks/agent_session_protocol.py`, and
   `weft/core/tasks/interactive.py`?
   Answer: because delegated interactive or persistent behavior is explicitly
   out of scope for this slice.

## Fixed Design Decisions

These are not open during implementation.

### 1. The new runtime literal is `provider_cli`

Do not bikeshed this during the slice. Use:

- `spec.agent.runtime = "provider_cli"`

Rationale:

- it says exactly what the runtime does now
- it leaves room for non-CLI delegated runtimes later
- it avoids pretending this is the same thing as the current `llm` adapter

### 2. Keep provider selection inside `runtime_config`

Do not widen the generic `AgentSection` contract with a top-level `provider`
field in this slice.

Use:

- `spec.agent.runtime_config.provider`
- optional `spec.agent.runtime_config.executable`
- optional `spec.agent.runtime_config.resolver_ref`
- optional `spec.agent.runtime_config.tool_profile_ref`

Rationale:

- only `provider_cli` needs those fields today
- widening `AgentSection` now would force `llm` to accept or ignore fields it
  does not use
- future promotion to first-class generic fields can happen later if a second
  runtime needs the same selectors

### 3. Ship exactly one production provider adapter

Add a provider registry, but land only one production adapter in the first
merge: `codex`.

Rationale:

- the registry is the durable seam
- one real adapter is enough to prove the shape
- parity across multiple providers is a separate plan

Counterargument:

- a registry with one provider looks premature

Why this is still the right cut:

- `provider_cli` without any provider seam hard-codes exactly the logic we
  would have to unpick later
- a very small registry is cheaper than repeating executable resolution and
  probe rules inside the backend

### 4. Resolver and tool-profile selectors are import refs, not plugin names

Use Python import refs of the existing `module:function` form.

Do not add a new entry-point plugin system for resolvers or tool profiles in
this slice.

Rationale:

- `mm-governance` and similar systems will own domain resolvers outside core
  Weft
- import refs already match Weft patterns
- an entry-point system here would be a second extension surface with no proof
  it is needed

### 5. Tool profiles in Phase 1 are invocation-shaping only

In Phase 1, a tool profile may:

- add bounded extra instructions
- add bounded provider-option hints
- add bounded metadata for audit

It may not:

- expose MCP servers
- expose nested agent calls
- expose new Weft tool bridges
- override runner environment or working directory policy

Rationale:

- actual tool surfacing is Phase 3 work
- calling this out now prevents the implementer from quietly sneaking Phase 3
  into Phase 1

### 6. `provider_cli` is one-shot and text-only in Phase 1

For `provider_cli`:

- `spec.persistent` must be `false`
- `conversation_scope` must remain `per_message`
- `output_mode` must be `"text"`
- `output_schema` must be `null`
- `spec.agent.tools` must be empty

Rationale:

- each relaxed rule would require additional runtime or contract work that this
  plan explicitly excludes

### 7. Persist Weft-owned metadata, not provider-native transcript state

Persist:

- runtime name
- provider name
- model
- executable path actually used
- resolver/tool-profile refs
- usage if available
- referenced-input artifacts returned by the resolver
- bounded metadata returned by resolver/profile/backend

Do not persist:

- full raw prompt body
- provider-native transcript history
- provider continuation IDs

Rationale:

- this phase is about bounded execution and audit, not transcript durability

### 8. Concrete TaskSpec shape for this slice

The target shape for a valid Phase 1 delegated task is:

```json
{
  "spec": {
    "type": "agent",
    "persistent": false,
    "agent": {
      "runtime": "provider_cli",
      "model": "gpt-5-codex",
      "instructions": "You are a careful operator assistant.",
      "output_mode": "text",
      "runtime_config": {
        "provider": "codex",
        "resolver_ref": "myapp.weft_refs:resolve_operator_question",
        "tool_profile_ref": "myapp.weft_refs:readonly_provider_profile"
      }
    }
  }
}
```

Keep that shape small. Do not hide more structure in `runtime_config` than this
slice needs.

## Invariants and Constraints

- Preserve TID format and immutability.
- Preserve forward-only state transitions.
- Preserve reserved-queue policy on runtime failure.
- Preserve `spec` and `io` immutability after resolved `TaskSpec` creation.
- Preserve the existing public outbox contract. No new public `agent_result`
  envelope.
- Preserve the existing durable spine. Do not create a second execution path.
- Preserve runner ownership of process isolation and cancellation.
- Preserve the `llm` runtime contract and existing persistent-agent behavior.
- Preserve schema-only validation as pure by default. Runtime/executable checks
  happen only in explicit load or preflight flows and at actual execution time.
- Keep built-in runtime registration additive. Do not replace the current
  runtime registry.
- Keep `runtime_config` JSON-serializable and frozen in `TaskSpec`.
- Do not let runtime-only state become a new persisted truth lane.
- Do not add any new dependency.
- Do not add a mock-heavy substitute for a real subprocess proof when a real
  subprocess to a test fixture executable is practical.

Hidden couplings to name before decomposition:

- `validate_taskspec()` is pure schema validation today; environment checks live
  outside it
- `Consumer` currently ignores non-public `AgentExecutionResult` metadata
- `execute_agent_target()` currently resolves tools for all runtimes before
  calling the runtime adapter
- `weft result` and task observers infer completion from task-log events, not
  from runtime-specific metadata

Stop-and-re-evaluate gates for the whole slice:

- if you need to edit any interactive or agent-session module for core
  behavior, stop
- if you need a second registry beyond the runtime registry and one small
  provider registry, stop
- if you need to store prompt text or transcript bytes durably, stop
- if you are about to make `provider_cli` support persistent or interactive
  behavior, stop
- if you are about to make `provider_cli` depend on `spec.agent.tools`, stop
- if tests start relying on patching `subprocess.run` instead of executing a
  real fixture binary, stop

## Rollout and Rollback

This slice is additive but still includes a one-way door: a new public runtime
literal and new runtime-config keys become part of the TaskSpec contract.

Rollout order:

1. update current/planned specs and add plan backlinks
2. add schema restrictions and validation helpers
3. add public resolver/profile contracts and internal loaders
4. add provider registry and the `provider_cli` backend
5. persist internal execution metadata on task-log boundary events
6. add end-to-end and CLI validation coverage

Rollback rules:

- roll back the new `provider_cli` runtime and the new validation/docs together
- do not leave docs claiming support after removing the runtime
- understand that stored TaskSpecs already using `provider_cli` will fail after
  rollback; this is the contract one-way door

Compatibility rule during rollout:

- no existing `llm` or command/function task should see any behavior change

## Tasks

1. Lock the Phase 1 contract in the current and planned specs before code.
   - Outcome:
     - the current spec describes the new built-in one-shot `provider_cli`
       runtime and its restrictions
     - the planned companion still describes later phases without duplicating
       the now-current Phase 1 contract
     - the CLI validation spec notes that explicit preflight now covers agent
       runtime availability when relevant
   - Files to touch:
     - `docs/specifications/13-Agent_Runtime.md`
     - `docs/specifications/13A-Agent_Runtime_Planned.md`
     - `docs/specifications/10-CLI_Interface.md`
   - Read first:
     - `docs/specifications/13-Agent_Runtime.md`
     - `docs/specifications/13A-Agent_Runtime_Planned.md`
     - `docs/specifications/10-CLI_Interface.md`
   - Required contract details:
     - `provider_cli` is additive
     - `provider_cli` is one-shot only in Phase 1
     - selectors live in `runtime_config`
     - provider-native transcript state is not persisted
     - internal artifact/metadata persistence is durable through task logs, not
       public outbox changes
   - Constraints:
     - do not claim MCP, interactive attach, or durable threads
     - add backlinks from touched specs to this plan
   - Tests:
     - none beyond doc review
   - Stop if:
     - documenting the slice requires a second queue family or a second result
       protocol
   - Done when:
     - the current spec and planned companion no longer disagree about what
       Phase 1 does

2. Add the minimal shared helper and public contracts needed for domain-owned
   resolvers and tool profiles.
   - Outcome:
     - Weft has one tiny shared import-ref helper for `module:function`
       resolution
     - Weft exposes explicit dataclasses or protocols for:
       - resolver result
       - tool-profile result
     - `provider_cli` can load resolver/profile refs without inventing a new
       plugin system
   - Files to touch:
     - `weft/ext.py`
     - `weft/core/imports.py`
     - `weft/core/agent_resolution.py`
     - optionally `weft/core/agent_tools.py` and `weft/core/targets.py` if the
       new import-ref helper can replace exact duplicate parsing with a very
       small diff
   - Read first:
     - `weft/ext.py`
     - `weft/core/agent_tools.py`
     - `weft/core/targets.py`
   - Reuse:
     - existing `module:function` import-ref shape
   - Contract to implement:
     - resolver ref is optional
     - absent resolver ref means a built-in direct resolver
     - resolver callable should use a small keyword-only signature:
       `(*, agent, work_item, tid) -> resolver result`
     - direct resolver must preserve current work-item semantics by converting
       normalized content into a single prompt string deterministically
     - for structured messages, the direct resolver must reuse one shared
       content-flattening helper rather than inventing a second format
     - tool-profile ref is optional
     - absent tool-profile ref means a built-in null profile
     - tool-profile callable should use a small keyword-only signature:
       `(*, agent, tid) -> tool-profile result`
     - profile result in this phase may contribute only bounded instructions,
       provider options, and metadata
   - Constraints:
     - do not add an entry-point plugin loader
     - do not let tool profiles override runner environment policy
     - if you need a shared content-flattening helper for structured messages,
       extract a tiny one; do not pull `llm_backend` internals across layers
   - Tests to write first:
     - a focused resolver/profile loader test module under `tests/core/`
     - tests for invalid ref syntax
     - tests for wrong return types
     - tests for direct resolver default behavior on plain text and structured
       messages
   - Stop if:
     - the helper starts turning into a general plugin manager
     - the contract starts carrying raw transcripts or env policy
   - Done when:
     - outside repos can implement a resolver/profile by importing public Weft
       dataclasses or protocols

3. Extend TaskSpec validation for the exact `provider_cli` Phase 1 shape.
   - Outcome:
     - TaskSpec schema accepts `runtime="provider_cli"` and rejects unsupported
       shapes for this phase
   - Files to touch:
     - `weft/core/taskspec.py`
     - `tests/taskspec/test_taskspec.py`
   - Read first:
     - `weft/core/taskspec.py`
     - current agent-runtime validation rules in `docs/specifications/13-Agent_Runtime.md`
   - Rules to enforce:
     - `runtime_config.provider` is required and non-empty for `provider_cli`
     - `runtime_config.executable`, `resolver_ref`, and `tool_profile_ref`
       must be non-empty strings when present
     - `spec.persistent` must be `false`
     - `conversation_scope` must remain `per_message`
     - `output_mode` must be `"text"`
     - `output_schema` must be absent or `null`
     - `spec.agent.tools` must be empty
   - Explicit non-rules:
     - do not redesign generic `AgentSection`
     - do not add top-level `provider`, `resolver`, or `tool_profile` fields
   - Tests to write first:
     - valid `provider_cli` payload
     - missing provider
     - persistent task rejection
     - `per_task` conversation rejection
     - `json`/`messages` output rejection
     - non-empty `tools` rejection
   - Stop if:
     - validation pressure is pushing toward a new nested Pydantic model only
       for `runtime_config`
   - Done when:
     - the invalid shapes above fail at TaskSpec validation time with clear
       errors

4. Add one shared agent-runtime validation path that mirrors runner validation
   without duplicating it.
   - Outcome:
     - Weft can validate runtime availability in three layers:
       - pure schema only
       - runtime load
       - runtime preflight
     - the existing CLI validation command can exercise that path without a new
       flag family
   - Files to touch:
     - `weft/core/agent_validation.py`
     - `weft/core/agent_runtime.py`
     - the new `provider_cli` backend module
     - `weft/core/tasks/runner.py`
     - `weft/commands/validate_taskspec.py`
     - `tests/cli/test_cli_validate.py`
     - `tests/core/test_agent_validation.py`
   - Read first:
     - `weft/core/runner_validation.py`
     - `weft/commands/validate_taskspec.py`
     - `weft/core/tasks/runner.py`
   - Implementation rules:
     - make runtime-validation helpers import `weft.core.agents` before runtime
       lookup so built-in runtimes are registered consistently
     - keep one helper for agent runtime validation
     - use `getattr(runtime, "validate", None)` or equivalent narrow hook; do
       not design a second plugin framework
     - schema-only validation must remain pure
     - `load_runner=True` / `preflight=True` in `cmd_validate_taskspec()`
       should also validate the agent runtime when `spec.type="agent"`
     - `TaskRunner` should perform the same preflight logic for agent tasks at
       execution time so CLI validation and real execution cannot drift
     - reuse the current CLI validate output style; add at most one additional
       success/failure line for agent-runtime validation rather than redesigning
       the command output
   - Constraints:
     - do not add a new public CLI flag just for agent runtime validation
   - Tests to write first:
     - CLI validate succeeds for a valid `provider_cli` TaskSpec that points to
       a real fixture executable
     - CLI validate preflight fails cleanly for a missing executable
     - existing non-agent and `llm` validation tests stay green
   - Stop if:
     - you are about to copy the runner-validation logic into a second command
       path
   - Done when:
     - validation behavior is shared and consistent between CLI validation and
       actual task execution

5. Implement the internal provider registry and the one built-in `codex`
   adapter.
   - Outcome:
     - `provider_cli` can resolve provider name `codex`
     - executable selection and version probe are isolated in one provider
       layer
     - tests can override the executable path with a real fixture binary
   - Files to touch:
     - `weft/core/agents/provider_registry.py`
     - `weft/core/agents/backends/provider_cli_backend.py`
     - `weft/core/agents/backends/__init__.py`
     - `tests/core/test_provider_cli_backend.py`
     - `tests/fixtures/provider_cli_fixture.py`
   - Read first:
     - `weft/core/agents/backends/__init__.py`
     - `weft/core/agents/backends/llm_backend.py`
     - `weft/_runner_plugins.py`
   - Required design:
     - keep provider-specific command construction inside the provider adapter,
       not in generic runtime code
     - resolve executable path from:
       1. `runtime_config.executable` if present
       2. otherwise provider default command name
     - verify with a cheap probe such as `--version`
     - run the provider non-interactively with `shell=False`
     - feed the final prompt on stdin unless the chosen adapter has a better
       stable batch mode
     - verify the chosen real CLI has a stable one-shot batch mode before
       wiring the adapter; if it does not, stop and re-plan instead of building
       a PTY-driven workaround
   - Required fake-provider test fixture:
     - a real executable script or wrapper, not a mocked subprocess
     - it must support:
       - a version/probe call
       - one-shot execution reading stdin and returning text on stdout
       - a failure mode with non-zero exit
     - preferred pattern:
       - keep fixture logic in a normal Python module under `tests/fixtures/`
       - have tests write a tiny temp executable wrapper that invokes that
         module via `sys.executable`
       - point `runtime_config.executable` at that wrapper path
   - Constraints:
     - do not implement more than one production provider
     - do not write provider CLI flags inline in `agent_runtime.py`,
       `consumer.py`, or `taskspec.py`
   - Tests to write first:
     - unknown provider rejection
     - missing executable failure
     - probe failure
     - successful one-shot execution with executable override
     - provider non-zero exit surfaces a clear failure
   - Stop if:
     - you need a generic provider capability-negotiation framework
     - you are adding streaming or interactive provider support
   - Done when:
     - a real subprocess to the fixture executable proves the full adapter path

6. Implement `provider_cli` runtime execution by composing normalized input,
   resolver output, and tool-profile output into one one-shot provider request.
   - Outcome:
     - `execute_agent_target()` can dispatch `provider_cli` work and return a
       normal `AgentExecutionResult`
   - Files to touch:
     - `weft/core/agents/backends/provider_cli_backend.py`
     - `weft/core/agent_runtime.py`
     - `tests/core/test_agent_runtime.py`
     - `tests/core/test_provider_cli_backend.py`
   - Read first:
     - `weft/core/agent_runtime.py`
     - `weft/core/agents/backends/llm_backend.py`
   - Prompt composition order to keep deterministic:
     - base `agent.instructions`
     - resolver-added instructions
     - tool-profile-added instructions
     - resolved prompt body
   - Provider-option merge order to keep deterministic:
     - provider defaults
     - tool-profile provider options
     - explicit `agent.options` win last
   - Metadata rules:
     - persist provider name and executable path in `result.metadata`
     - carry resolver-returned artifacts in `result.artifacts`
     - usage may be `None` if the provider cannot return it cleanly
     - `tool_trace` should remain empty in this phase
   - Constraints:
     - `provider_cli` must ignore `spec.agent.tools` because validation already
       rejects them
     - do not widen the runtime adapter interface unless absolutely necessary
     - if you need to flatten structured messages, extract or reuse one small
       helper; do not duplicate stringification logic in multiple places
   - Tests to write first:
     - `execute_agent_target()` with `provider_cli` returns text output
     - resolver artifacts are preserved on the internal result
     - resolver/profile refs are reflected in metadata
   - Stop if:
     - the backend starts owning task lifecycle or queue logic
   - Done when:
     - `provider_cli` behaves like a normal one-shot runtime from the point of
       view of `TaskRunner`

7. Persist internal delegated-execution metadata on task-log boundary events
   without changing public outbox behavior.
   - Outcome:
     - successful one-shot delegated executions record bounded internal
       metadata and artifacts in `weft.log.tasks`
   - Files to touch:
     - `weft/core/tasks/consumer.py`
     - possibly one tiny serialization helper under `weft/core/`
     - `tests/tasks/test_agent_execution.py`
     - `tests/helpers/weft_harness.py` only if a small inspection helper makes
       the queue-backed assertions cleaner
   - Read first:
     - `weft/core/tasks/consumer.py`
     - `weft/core/tasks/base.py`
     - `tests/helpers/weft_harness.py`
   - Required persistence shape:
     - attach a bounded `agent_execution` payload to the same boundary event
       that already marks completion:
       - `work_completed` for one-shot delegated runs
     - include:
       - runtime
       - model
       - provider
       - executable path
       - resolver/tool-profile refs
       - usage if present
       - artifacts
       - bounded metadata
     - do not duplicate full public output in that payload
   - Constraints:
     - the public outbox payload must stay unchanged
     - metadata must be JSON-safe
     - metadata persistence must not downgrade a successful task into failure
       because of an avoidable serialization bug; sanitize first
   - Tests to write first:
     - end-to-end consumer test proving:
       - outbox still contains the public text output only
       - the terminal task-log event includes `agent_execution`
       - artifacts are present in that log payload
     - reserved-policy failure test still works for delegated runtime failures
   - Stop if:
     - you are about to add a second persistence store or a new public queue
   - Done when:
     - operators can inspect internal delegated-execution metadata in
       `weft.log.tasks` with no public protocol change

8. Harden the slice with end-to-end task proofs and final doc sync.
   - Outcome:
     - the entire slice is proven through real TaskRunner and Consumer paths
     - specs, plan backlinks, and tests agree
   - Files to touch:
     - any docs or tests above that still drift
   - Read first:
     - the touched specs
     - all new tests
   - Minimum end-to-end proofs:
     - `TaskRunner` one-shot delegated success
     - `TaskRunner` delegated timeout or cancellation with a slow fixture
       executable
     - `Consumer` delegated success path
     - `Consumer` reserved-policy-on-error path
     - CLI validation load and preflight coverage
     - no regression in current `llm` one-shot and persistent tests
   - Independent review gate:
     - get an independent review pass after this slice from a different agent
       family if available
     - if only same-family review is available, record that limitation in the
       review notes
   - Stop if:
     - passing the new tests requires weakening existing `llm` or persistent
       agent assertions
   - Done when:
     - the slice is review-ready with docs, code, and queue-backed tests all in
       sync

## Verification

Run verification in this order. Do not run `uv` commands in parallel.

Targeted tests during implementation:

1. `./.venv/bin/python -m pytest tests/taskspec/test_taskspec.py -q`
2. `./.venv/bin/python -m pytest tests/core/test_agent_runtime.py -q`
3. `./.venv/bin/python -m pytest tests/core/test_agent_validation.py -q`
   if you create that file
4. `./.venv/bin/python -m pytest tests/core/test_provider_cli_backend.py -q`
   if you create that file
5. `./.venv/bin/python -m pytest tests/tasks/test_agent_execution.py -q`
6. `./.venv/bin/python -m pytest tests/cli/test_cli_validate.py -q`

Required full gate before claiming done:

1. `./.venv/bin/python -m ruff check weft tests`
2. `./.venv/bin/python -m mypy weft`
3. `./.venv/bin/python -m pytest`

What must stay real in tests:

- real `TaskSpec` parsing
- real built-in runtime registration
- real subprocess execution to a fixture executable
- real broker-backed queues
- real `TaskRunner`
- real `Consumer`
- real task-log inspection through queue reads

What may be faked or overridden narrowly:

- provider executable path via `runtime_config.executable`
- provider probe behavior through the fixture executable itself
- import-ref targets through tiny fixture modules under `tests/fixtures/`

Observable success conditions:

- `provider_cli` tasks reach normal `work_completed` state with unchanged
  public outbox behavior
- preflight catches missing executables before execution when explicitly asked
- `weft.log.tasks` includes bounded `agent_execution` metadata for delegated
  runs
- all current `llm` runtime tests, including persistent `per_task`, still pass

## Final Notes for the Implementer

- Be DRY about the real repeated seams only:
  - import-ref loading
  - runtime validation
  - content flattening if both `llm` and `provider_cli` need it
- Be ruthless about YAGNI:
  - one provider
  - one-shot only
  - text output only
  - no tool bridging
- Prefer red-green-refactor.
- Do not invent new abstractions because they feel cleaner.
- If a change touches sessions, new queues, transcript persistence, or provider
  continuation, you are no longer implementing this plan.

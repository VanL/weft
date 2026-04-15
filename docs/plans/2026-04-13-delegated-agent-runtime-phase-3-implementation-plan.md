# Delegated Agent Runtime Phase 3 Implementation Plan

This plan implements Phase 3 from
[`docs/specifications/13A-Agent_Runtime_Planned.md`](../specifications/13A-Agent_Runtime_Planned.md):
runtime-agnostic environment profiles and explicit tool profiles for higher
order agent work, without turning Weft into a Docker-specific orchestration
layer or giving delegated runtimes ambient machine powers.

This is risky boundary work. It touches:

`TaskSpec -> validation -> TaskRunner -> runner plugin -> agent runtime -> provider CLI`

It also touches the trust boundary for the top lane. The likely failure modes
are predictable:

- baking Docker assumptions into a general environment-profile contract,
- letting a profile silently change the selected runner,
- using ambient host config and installed tools as implicit agent powers,
- treating "available on this machine" as part of TaskSpec schema validity,
- silently dropping unsupported MCP or workspace features per provider,
- or widening Phase 3 into "non-host persistent agent sessions everywhere."

Do not do those things.

## Scope Lock

Implement exactly this slice:

1. keep `spec.runner.name` as the authoritative runner choice
2. add a Weft-level environment-profile selector at the runner boundary
3. make the environment-profile contract runtime-agnostic:
   it may materialize `runner_options`, `env`, and `working_dir`, but it may
   not silently switch runners
4. keep availability layered and explicit:
   schema validity, load validation, preflight readiness, and execution-time
   failure remain different states
5. extend delegated tool profiles so Weft can reason about explicit tool
   surfaces instead of relying only on opaque provider options
6. support structured tool surfaces only where Weft needs real policy in this
   phase:
   `workspace_access` and allowlisted stdio MCP server descriptors
7. keep `provider_options` as a narrow provider-specific escape hatch, but do
   not use it as a substitute for structured policy where Weft must validate
   behavior
8. add runtime-agnostic environment-profile plumbing for all runners
9. add first richer environment support for the existing `docker` runner:
   checked-in image-backed or build-backed profiles, explicit mounts, explicit
   network policy, explicit env injection
10. keep the current runner capability matrix explicit
11. keep public inbox/outbox/task-log behavior unchanged
12. add deterministic fixture-backed tests and opt-in live local full-path
   tests

Target means:

- the profile contract must be usable with `host`, `docker`,
  `macos-sandbox`, and future runners
- current shipped runner plugins do not all need full agent-session parity in
  this phase
- if a runner does not support agent tasks today, environment profiles may
  still target it for supported task shapes, but the capability error must stay
  explicit

Do not implement any of the following in this slice:

- a second runner/orchestration path outside `TaskRunner`
- a Docker-only environment-profile schema
- implicit fallback from `docker` or `macos-sandbox` to `host`
- implicit fallback from "requested tool surface unsupported" to "tool hidden"
- a retrofit of the `llm` backend onto the new structured delegated tool
  surfaces
- Weft-owned durable threads or transcript replay
- restart-durable delegated continuation
- a general MCP daemon manager or registry service
- HTTP/SSE MCP transport support
- bounded sub-agent tools
- a universal capability-negotiation framework broader than Weft needs now
- non-host persistent agent sessions unless a separate runner-capability plan is
  written first

If implementation pressure starts pulling toward any item in the exclusion
list, stop and write a follow-on plan instead.

## Goal

Make Phase 2 delegated sessions and one-shot runs usable inside explicit,
policy-driven environments and tool surfaces, while keeping the contract
runtime-agnostic, availability-aware, and honest about what each runner and
provider can actually do on the current machine.

## The Hard Constraint You Must Not Miss

Phase 3 does **not** mean "all runners now support persistent delegated agent
sessions."

Current shipped capabilities still matter:

- `host` supports one-shot work, persistent tasks, interactive command
  sessions, and delegated agent sessions
- `docker` currently supports one-shot command tasks only
- `macos-sandbox` currently supports one-shot command tasks only

So the correct Phase 3 contract is:

- environment profiles are runner-agnostic
- availability is explicit per runner and per provider
- agent tasks may only run on runners that already advertise support for them

If product intent changes and non-host delegated agent execution becomes a
near-term requirement, that is a separate runner-capability expansion and
should get its own plan. Do not smuggle that work into Phase 3.

## Runtime Availability Model

This plan must preserve four distinct states. Do not collapse them.

1. Schema valid:
   the TaskSpec shape is legal and can be stored or committed on any machine.
2. Load-valid:
   the referenced runner plugin, environment profile, agent runtime, and tool
   profile can be imported and their basic contracts are structurally valid.
3. Preflight-ready:
   the current machine has the required runner binary, provider CLI, files,
   profile inputs, and MCP server executables needed for the declared request.
4. Execution-ready:
   the actual run path still succeeds when invoked at runtime.

Rules:

- schema validity must remain portable and machine-independent
- preflight must never be part of baseline TaskSpec schema validation
- execution-time preflight remains mandatory even if CLI preflight already ran
- a missing dependency must fail clearly at the narrowest correct layer
- no layer may silently substitute a different runner, provider, or tool set

## Source Documents

Primary specs:

- [`docs/specifications/13A-Agent_Runtime_Planned.md`](../specifications/13A-Agent_Runtime_Planned.md)
  [AR-A3], [AR-A4], [AR-A7]
- [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
  [AR-5], [AR-8], [AR-9]
- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
  [TS-1], [TS-1.3], [TS-1.4]
- [`docs/specifications/06-Resource_Management.md`](../specifications/06-Resource_Management.md)
  [RM-5], [RM-5.1]
- [`docs/specifications/06A-Resource_Management_Planned.md`](../specifications/06A-Resource_Management_Planned.md)
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [IMMUT.1], [IMMUT.2], [STATE.1], [STATE.2], [QUEUE.1], [QUEUE.2],
  [QUEUE.3], [IMPL.5], [IMPL.6]
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
  [CLI-1.4.1]

Existing plans and local guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/README.md`](../agent-context/README.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/testing-patterns.md`](../agent-context/runbooks/testing-patterns.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)
- [`docs/plans/2026-04-06-runner-extension-point-plan.md`](./2026-04-06-runner-extension-point-plan.md)
- [`docs/plans/2026-04-13-delegated-agent-runtime-phase-1-implementation-plan.md`](./2026-04-13-delegated-agent-runtime-phase-1-implementation-plan.md)
- [`docs/plans/2026-04-13-delegated-agent-runtime-phase-2-implementation-plan.md`](./2026-04-13-delegated-agent-runtime-phase-2-implementation-plan.md)

Provider reference material:

- [`../agent-mcp/src/server.ts`](../../../agent-mcp/src/server.ts)

Use `agent-mcp` only as a source of real provider CLI dispatch details and
provider-local minimal-mode patterns. It is not the design authority for Weft.

## Context and Key Files

Files that will likely change in this slice:

- `docs/specifications/13-Agent_Runtime.md`
- `docs/specifications/13A-Agent_Runtime_Planned.md`
- `docs/specifications/02-TaskSpec.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/specifications/06A-Resource_Management_Planned.md`
- `weft/ext.py`
- `weft/core/taskspec.py`
- recommended new module:
  - `weft/core/environment_profiles.py`
- `weft/core/runner_validation.py`
- `weft/core/agent_validation.py`
- `weft/core/agent_resolution.py`
- `weft/core/agents/provider_registry.py`
- `weft/core/agents/backends/provider_cli_backend.py`
- `weft/core/tasks/runner.py`
- `weft/commands/validate_taskspec.py`
- `extensions/weft_docker/weft_docker/plugin.py`
- maybe `extensions/weft_macos_sandbox/weft_macos_sandbox/plugin.py`
- recommended new fixture module:
  - `tests/fixtures/runtime_profiles_fixture.py`
- recommended new focused tests:
  - `tests/core/test_environment_profiles.py`
  - `tests/core/test_tool_profiles.py`
- existing tests likely touched:
  - `tests/taskspec/test_taskspec.py`
  - `tests/core/test_agent_validation.py`
  - `tests/core/test_provider_cli_backend.py`
  - `tests/tasks/test_agent_execution.py`
  - `tests/tasks/test_command_runner_parity.py`
  - `tests/cli/test_cli_validate.py`

Files that should usually not change in this slice:

- `weft/core/tasks/consumer.py`
- `weft/core/tasks/sessions.py`
- `weft/core/manager.py`
- `weft/core/agent_runtime.py`

Touch those only if a red test exposes a real gap.

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
9. [`docs/specifications/06-Resource_Management.md`](../specifications/06-Resource_Management.md)
10. [`docs/specifications/06A-Resource_Management_Planned.md`](../specifications/06A-Resource_Management_Planned.md)
11. [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
12. [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
13. [`weft/ext.py`](../../weft/ext.py)
14. [`weft/core/taskspec.py`](../../weft/core/taskspec.py)
15. [`weft/core/runner_validation.py`](../../weft/core/runner_validation.py)
16. [`weft/core/agent_validation.py`](../../weft/core/agent_validation.py)
17. [`weft/core/agent_resolution.py`](../../weft/core/agent_resolution.py)
18. [`weft/core/tasks/runner.py`](../../weft/core/tasks/runner.py)
19. [`weft/core/agents/provider_registry.py`](../../weft/core/agents/provider_registry.py)
20. [`weft/core/agents/backends/provider_cli_backend.py`](../../weft/core/agents/backends/provider_cli_backend.py)
21. [`weft/commands/validate_taskspec.py`](../../weft/commands/validate_taskspec.py)
22. [`extensions/weft_docker/weft_docker/plugin.py`](../../extensions/weft_docker/weft_docker/plugin.py)
23. [`extensions/weft_macos_sandbox/weft_macos_sandbox/plugin.py`](../../extensions/weft_macos_sandbox/weft_macos_sandbox/plugin.py)
24. [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py)
25. [`tests/fixtures/provider_cli_fixture.py`](../../tests/fixtures/provider_cli_fixture.py)
26. [`tests/tasks/test_command_runner_parity.py`](../../tests/tasks/test_command_runner_parity.py)
27. [`tests/tasks/test_agent_execution.py`](../../tests/tasks/test_agent_execution.py)
28. [`tests/core/test_provider_cli_backend.py`](../../tests/core/test_provider_cli_backend.py)
29. [`tests/cli/test_cli_validate.py`](../../tests/cli/test_cli_validate.py)
30. [`../agent-mcp/src/server.ts`](../../../agent-mcp/src/server.ts)

## Comprehension Questions

If the implementer cannot answer these before editing, they are not ready.

1. Where does runner capability enforcement happen before execution starts?
   Answer: `validate_runner_capabilities()` in
   `weft/core/runner_validation.py`, plus plugin-specific
   `validate_taskspec(...)`.

2. Which current field selects the runner, and may an environment profile
   override it?
   Answer: `spec.runner.name` selects the runner. An environment profile must
   not silently override it.

3. Why is an environment-profile selector a Weft-level runner field instead of
   a runner-specific option?
   Answer: because it is a general Weft feature, not a plugin-private runner
   option. `spec.runner.options` is for runner-specific payload only.

4. Which current path already distinguishes load validation from runtime
   preflight for runners?
   Answer: `validate_taskspec_runner(...)` in
   `weft/core/runner_validation.py`.

5. Which current path already distinguishes load validation from runtime
   preflight for delegated agent runtimes?
   Answer: `validate_taskspec_agent_runtime(...)` in
   `weft/core/agent_validation.py`.

6. Why is "Docker container" the wrong top-level design shape for Phase 3?
   Answer: because environment policy belongs at the runner boundary and must
   stay usable with `host`, `docker`, `macos-sandbox`, and future runners.

7. Which current non-host runners can host delegated persistent agent sessions?
   Answer: none. `docker` and `macos-sandbox` currently advertise command-only
   one-shot capability.

8. If a tool profile requests MCP servers for a provider that cannot express
   explicit MCP server configuration without ambient hidden state, what should
   happen?
   Answer: validation or preflight must reject the request explicitly. Do not
   hide the MCP request or fall back to ambient user config.

## Engineering Rules

These are not optional.

### 1. Style

- Use `from __future__ import annotations` in every new Python file.
- Keep imports at module top.
- Use `collections.abc` types.
- Use `Path`, not `os.path`.
- Keep names plain and direct.
- Add load-bearing docstrings with spec references.
- Add comments only where the control flow would otherwise be hard to parse.

### 2. DRY

- one environment-profile materialization path
- one tool-profile materialization path
- one availability layering model
- one place where `TaskRunner` turns profile refs into execution inputs
- one provider-capability check per structured tool surface

Do not duplicate slightly different profile-merging logic between CLI
validation, `TaskRunner`, and the backend.

### 3. YAGNI

- Do not design a universal environment DSL.
- Do not design a universal tool DSL.
- Add structured fields only for Phase 3 policy that Weft must reason about:
  `workspace_access` and stdio MCP server descriptors.
- Keep everything else behind the existing narrow `provider_options` escape
  hatch until there is a real need to generalize.

### 4. Red-Green TDD

For every task below:

1. write the smallest failing test first
2. make it pass with the smallest coherent change
3. refactor only after behavior is locked

Do not start with a large refactor.

### 5. Test Design

- Prefer real TaskSpec payloads, real broker queues, real `TaskRunner`
  execution, real subprocesses, and `WeftTestHarness`.
- Do not mock `simplebroker.Queue`.
- Do not mock `TaskRunner` when the behavior under test is runner or agent
  execution.
- Use fixture executables and tiny local helper processes instead of faking
  provider CLIs or MCP servers with deep mocks.
- Use monkeypatch only at genuine external boundaries:
  `import_callable_ref`, `shutil.which`, provider probe helpers, Docker binary
  lookup, or environment variables.

## Fixed Design Decisions

These decisions are not open during implementation.

### 1. New Runner Field

Add the environment-profile selector as a Weft-level runner field:

- `spec.runner.environment_profile_ref: str | None`

Do **not** hide this under `spec.runner.options`. It is not runner-private.

### 2. Environment Profile Result Shape

Environment profiles should materialize only what Weft must merge generically:

- `runner_options`
- `env`
- `working_dir`
- `metadata`

Do **not** let an environment profile return a different `runner_name`.

Recommended callable signature:

```python
def environment_profile(
    *,
    target_type: str,
    runner_name: str,
    runner_options: Mapping[str, Any],
    env: Mapping[str, str],
    working_dir: str | None,
    tid: str | None,
) -> RunnerEnvironmentProfileResult: ...
```

Keep it small. Do not pass the whole mutable TaskSpec model unless a red test
proves this signature is too narrow.

### 3. Tool Profile Result Shape

Extend `AgentToolProfileResult`. Do not replace it.

Keep:

- `instructions`
- `provider_options`
- `metadata`

Add only the structured fields Weft must reason about in this phase:

- `workspace_access`
- `mcp_servers`

For this phase, treat `workspace_access` as the structured shell/filesystem
policy surface. Do not split it into separate shell and filesystem DSLs unless
real provider behavior forces that split.

Recommended `workspace_access` values:

- `"none"`
- `"read-only"`
- `"workspace-write"`

Recommended MCP surface:

- explicit stdio server descriptors only
- name, command, args, env, cwd

Do not add HTTP/SSE transport or nested sub-agents in this phase.

Keep the current callable selector:

- `spec.agent.runtime_config.tool_profile_ref`

This Phase 3 work is for delegated runtimes, especially `provider_cli`. Do not
change `llm` runtime tool semantics in this slice.

### 4. Availability Honesty

If a runner, provider, or provider/tool combination cannot support a declared
request, fail explicitly. Never silently degrade.

Examples:

- `docker` unavailable on PATH: preflight fails
- provider CLI missing: agent-runtime preflight fails
- tool profile requests MCP servers for a provider with no explicit MCP support:
  validation or preflight fails
- TaskSpec requests `docker` with `spec.type="agent"` while the plugin still
  supports command-only tasks: capability validation fails

### 5. Current Capability Matrix Stays Real

Do not "paper over" runner limitations with profile resolution. Profiles do not
change runner capabilities.

## Recommended Module Shape

Use this shape unless a red test proves it wrong:

- `weft/ext.py`
  - add `RunnerEnvironmentProfileResult`
  - add `RunnerEnvironmentProfile` protocol
  - add `AgentMCPServerDescriptor`
  - extend `AgentToolProfileResult`
- `weft/core/environment_profiles.py`
  - new shared loader/materializer for runner environment profiles
  - own merge rules and shared validation of environment-profile outputs
- `weft/core/agent_resolution.py`
  - keep resolver loading
  - extend tool-profile loading/materialization for richer structured outputs
- `weft/core/tasks/runner.py`
  - materialize environment profiles before runner validation and backend
    creation
- `weft/core/runner_validation.py`
  - validate environment-profile loading and preflight separately from runner
    plugin checks
- `weft/core/agent_validation.py`
  - validate tool-profile loading and structured tool-surface preflight
- `weft/core/agents/provider_registry.py`
  - own provider capability flags and provider-specific translation from
    structured tool surfaces to concrete CLI args/env/temp config
- `weft/core/agents/backends/provider_cli_backend.py`
  - pass structured tool-profile data into provider invocation building

Do not create more modules than this without a concrete reason.

## Task Breakdown

Follow these tasks in order. Do not skip ahead.

### Task 1. Lock the contract in `TaskSpec` and extension types

Read first:

- `weft/core/taskspec.py`
- `weft/ext.py`
- `docs/specifications/02-TaskSpec.md`
- `docs/specifications/13-Agent_Runtime.md`

Files to touch:

- `weft/core/taskspec.py`
- `weft/ext.py`
- `tests/taskspec/test_taskspec.py`
- maybe `tests/core/test_agent_resolution.py`

What to change:

1. Add `environment_profile_ref` to the runner schema as a Weft-level runner
   field.
2. Validate that it is either `None` or a non-empty string.
3. Add the new environment-profile dataclass and protocol to `weft/ext.py`.
4. Add the MCP server descriptor dataclass to `weft/ext.py`.
5. Extend `AgentToolProfileResult` with structured fields for
   `workspace_access` and `mcp_servers`.
6. Keep the dataclasses strict and boring. Validate types eagerly.

Write these tests first:

1. TaskSpec accepts a valid `spec.runner.environment_profile_ref`.
2. TaskSpec rejects blank `environment_profile_ref`.
3. `AgentToolProfileResult` rejects invalid `workspace_access`.
4. `AgentToolProfileResult` rejects bad MCP server descriptors.

Invariants:

- `spec.runner.name` remains required/authoritative
- `spec.runner.options` stays JSON-serializable runner-private payload
- no behavior change yet; this task is contract only

Gate before next task:

- `tests/taskspec/test_taskspec.py -q`
- any focused new contract test file passes

### Task 2. Add one shared environment-profile materialization path

Read first:

- `weft/core/runner_validation.py`
- `weft/core/tasks/runner.py`
- `weft/core/agent_resolution.py`

Files to touch:

- new `weft/core/environment_profiles.py`
- `weft/core/runner_validation.py`
- `weft/core/tasks/runner.py`
- new `tests/core/test_environment_profiles.py`
- `tests/fixtures/runtime_profiles_fixture.py`

What to change:

1. Add a single loader/materializer for runner environment profiles.
2. The loader should:
   - accept the current TaskSpec payload or normalized runner inputs
   - load `spec.runner.environment_profile_ref` when present
   - return a materialized result that merges:
     - profile `runner_options` over explicit `spec.runner.options`
       only by clearly documented precedence
     - profile `env` over explicit `spec.env`
       only by clearly documented precedence
     - profile `working_dir` only when `spec.working_dir` is absent, or reject
       conflicting values explicitly
3. Choose and document one precedence rule. Recommended rule:
   - TaskSpec explicit values win over profile defaults
   - profile supplies defaults, not hidden overrides
4. Validate merged outputs in one place.

Write these tests first:

1. load validation imports a valid environment profile callable
2. blank or missing callables fail load validation
3. TaskSpec explicit `working_dir` wins over profile default or the merge
   rejects conflicts, according to the documented rule
4. TaskSpec explicit env keys win over profile env defaults
5. a runner-specific environment profile fails clearly when invoked under the
   wrong `spec.runner.name`
6. profile results with wrong return types fail clearly

Use real fixture callables, not mocks.

Invariants:

- one shared merge path for CLI validation and runtime execution
- environment profiles do not select a different runner
- profiles remain optional

Gate before next task:

- `tests/core/test_environment_profiles.py -q`
- `tests/taskspec/test_taskspec.py -q`

### Task 3. Make validation and CLI output availability-aware

Read first:

- `weft/core/runner_validation.py`
- `weft/core/agent_validation.py`
- `weft/commands/validate_taskspec.py`
- `docs/specifications/10-CLI_Interface.md`

Files to touch:

- `weft/core/runner_validation.py`
- `weft/core/agent_validation.py`
- `weft/commands/validate_taskspec.py`
- `tests/core/test_agent_validation.py`
- `tests/cli/test_cli_validate.py`

What to change:

1. Separate environment-profile validation from runner-plugin validation.
2. Separate tool-profile validation from agent-runtime validation.
3. Keep schema/load/preflight layering explicit in both helper code and CLI
   output.
4. Make `weft spec validate --load-runner` and `--preflight` report distinct
   outcomes for:
   - runner plugin
   - environment profile
   - agent runtime
   - tool profile
5. Keep execution-time preflight in `TaskRunner` mandatory.

Write these tests first:

1. CLI validation shows runner available + environment profile available
   separately
2. CLI validation shows agent runtime available + tool profile available
   separately for agent tasks
3. missing environment profile fails without pretending the runner failed
4. unsupported provider/tool combination fails as tool-profile validation, not
   as a generic subprocess error
5. `--preflight` still implies load validation and still stays machine-specific

Invariants:

- schema validation remains portable
- preflight remains opt-in in CLI validation and mandatory at execution time
- error messages name the failing layer

Gate before next task:

- `tests/core/test_agent_validation.py -q`
- `tests/cli/test_cli_validate.py -q`

### Task 4. Extend provider capabilities and structured tool-surface translation

Read first:

- `weft/core/agents/provider_registry.py`
- `weft/core/agents/backends/provider_cli_backend.py`
- `../agent-mcp/src/server.ts`

Files to touch:

- `weft/core/agents/provider_registry.py`
- `weft/core/agents/backends/provider_cli_backend.py`
- `weft/core/agent_resolution.py`
- `tests/core/test_provider_cli_backend.py`
- new `tests/core/test_tool_profiles.py`
- `tests/fixtures/runtime_profiles_fixture.py`

What to change:

1. Add explicit provider capability flags for the structured Phase 3 tool
   surfaces.
2. At minimum, each provider must declare whether it supports:
   - structured `workspace_access`
   - explicit allowlisted stdio MCP server descriptors
3. Add one provider-owned translation path from structured tool-profile fields
   to concrete CLI args, env, temp config, or temp working data.
4. Keep provider-specific glue inside `provider_registry.py` where possible.
5. Keep `provider_options` supported, but structured fields win when both try
   to control the same behavior.

Specific provider honesty requirements:

- if `gemini` or `opencode` cannot support explicit MCP server injection in a
  clean, declared way, mark MCP as unsupported and fail clearly
- if `codex`, `claude_code`, or `qwen` can support explicit MCP allowlists or
  minimal-mode config, implement it from concrete CLI/config behavior and test
  it through real invocation assembly
- if a provider can only support a subset of `workspace_access` modes, encode
  that subset explicitly

Write these tests first:

1. every provider type in the Phase 1/2 matrix has a deterministic tool-profile
   test
2. unsupported MCP requests fail for providers that do not claim support
3. supported MCP requests materialize the expected provider-specific args/env
   or temp config markers
4. structured `workspace_access` maps to the right provider-specific behavior
   for each provider that claims support
5. structured fields beat conflicting raw `provider_options`

Test design rule:

- use real fixture executables and temp files
- do not mock provider result parsing or invocation assembly

Invariants:

- provider support matrix is explicit
- unsupported surfaces fail before subprocess execution where possible
- no provider may rely on ambient hidden MCP config when the tool profile asks
  for explicit MCP servers

Gate before next task:

- `tests/core/test_provider_cli_backend.py -q`
- `tests/core/test_tool_profiles.py -q`

### Task 5. Add runtime-agnostic environment profiles plus Docker-specific richer environment support

Read first:

- `extensions/weft_docker/weft_docker/plugin.py`
- `extensions/weft_macos_sandbox/weft_macos_sandbox/plugin.py`
- `tests/tasks/test_command_runner_parity.py`
- `docs/specifications/06A-Resource_Management_Planned.md`

Files to touch:

- `weft/core/environment_profiles.py`
- `extensions/weft_docker/weft_docker/plugin.py`
- maybe `extensions/weft_macos_sandbox/weft_macos_sandbox/plugin.py`
- `tests/tasks/test_command_runner_parity.py`
- `tests/fixtures/runtime_profiles_fixture.py`

What to change:

1. Make environment profiles usable with any runner by materializing generic
   merged runner inputs.
2. Add deterministic fixture profiles for:
   - `host`
   - `docker`
   - `macos-sandbox`
3. Keep the runner capability matrix real. If a profile targets `docker` for an
   agent task, capability validation must still reject the unsupported task
   shape.
4. Enhance the Docker runner plugin so environment profiles can express richer
   Docker environments without changing the general environment-profile
   contract.

Recommended Docker option additions:

- `image` or `build`, exactly one required
- `build.context`
- `build.dockerfile`
- `build.target`
- `build.args`
- `mounts`
- `network`
- `container_workdir`

Recommended mount shape:

- `source`
- `target`
- `read_only`

Rules:

- build context and Dockerfile must be checked-in paths or explicit absolute
  paths chosen outside prompt text
- writable mounts must be explicit
- read-only mounts should be the default
- Docker preflight checks Docker availability and validates local build inputs
  but should not force image existence when a build profile is declared
- no silent fallback from Docker profile to host execution

Write these tests first:

1. host environment profile materializes and runs through `TaskRunner`
2. docker environment profile materializes and passes runner validation when the
   task shape is command-only
3. docker environment profile on an agent task fails as a capability error
4. docker build-backed profile fails clearly when build context or Dockerfile is
   missing
5. mount rules are passed correctly to the Docker invocation builder
6. macOS sandbox environment profile remains usable for supported command-task
   shapes

Invariants:

- the environment-profile contract stays runner-agnostic
- runner-specific richness stays inside the runner plugin
- agent-task support is still determined by runner capabilities, not by profile
  presence

Gate before next task:

- `tests/tasks/test_command_runner_parity.py -q`
- focused docker runner tests pass

### Task 6. Add real-path broker/process tests and opt-in live local full-path tests

Read first:

- `tests/helpers/weft_harness.py`
- `tests/tasks/test_agent_execution.py`
- `tests/fixtures/provider_cli_fixture.py`

Files to touch:

- `tests/tasks/test_agent_execution.py`
- `tests/tasks/test_command_runner_parity.py`
- `tests/fixtures/runtime_profiles_fixture.py`
- maybe new tiny local MCP fixture under `tests/fixtures/`

What to change:

1. Add real broker/process tests for:
   - one-shot delegated agent execution with environment profile + tool profile
   - persistent delegated session with tool profile through the real Weft path
   - command-task Docker profile execution through the real runner path
2. Add a tiny real stdio MCP server fixture for local development tests.
3. Add opt-in live local tests that exercise:
   - real provider CLI
   - real Weft task path
   - real profile loading
   - real MCP server process for providers that claim explicit MCP support
4. Keep these live tests out of default CI.

Required live coverage:

- one-shot live smoke for every provider type:
  `claude_code`, `codex`, `gemini`, `opencode`, `qwen`
- persistent live smoke for every provider type
- live MCP smoke for every provider that claims explicit MCP support in Phase 3
- explicit negative live or fixture-backed coverage for every provider that
  does not claim MCP support

Recommended gating:

- reuse `WEFT_RUN_LIVE_PROVIDER_CLI_TESTS=1`
- reuse `WEFT_LIVE_PROVIDER_CLI_TARGETS=...`
- add one new explicit gate for MCP live tests if needed, for example
  `WEFT_RUN_LIVE_PROVIDER_CLI_MCP_TESTS=1`

Do not over-mock this:

- a fake provider executable wrapper is acceptable for deterministic tests
- a tiny stdio MCP server fixture is acceptable for deterministic tests
- a live local test must use the real installed provider CLI when the gate is
  enabled

Invariants:

- full-path tests use `Consumer -> TaskRunner -> runner -> provider_cli`
- live tests self-skip cleanly when the gate is off
- provider/tool support claims are backed by at least one real-path test

Gate before next task:

- focused broker/process tests pass
- live tests exist and self-skip correctly when disabled

### Task 7. Update docs and keep the support matrix honest

Read first:

- `docs/specifications/13-Agent_Runtime.md`
- `docs/specifications/13A-Agent_Runtime_Planned.md`
- `docs/specifications/02-TaskSpec.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/specifications/06A-Resource_Management_Planned.md`

Files to touch:

- `docs/specifications/13-Agent_Runtime.md`
- `docs/specifications/13A-Agent_Runtime_Planned.md`
- `docs/specifications/02-TaskSpec.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/specifications/06A-Resource_Management_Planned.md`

What to change:

1. Document the new `spec.runner.environment_profile_ref` field.
2. Document the new structured tool-profile fields and support matrix.
3. Document the layered availability model in CLI validation.
4. Document that the profile contract is runtime-agnostic.
5. Document that the current runner capability matrix still applies.
6. Add the plan backlink in the planned spec.

Write these tests first:

- docs do not get tests, but do not update docs last in a vacuum.
  Update them alongside the code that establishes the contract.

Invariants:

- docs do not imply non-host delegated agent-session support unless it ships
- docs do not imply MCP support on providers that still reject it
- docs do not imply that environment profiles can silently choose a runner

Gate before completion:

- docs and code agree on field names, availability stages, and support matrix

## Verification Matrix

Run these before claiming the implementation is done:

1. `./.venv/bin/python -m pytest tests/taskspec/test_taskspec.py -q`
2. `./.venv/bin/python -m pytest tests/core/test_environment_profiles.py -q`
3. `./.venv/bin/python -m pytest tests/core/test_tool_profiles.py -q`
4. `./.venv/bin/python -m pytest tests/core/test_agent_validation.py -q`
5. `./.venv/bin/python -m pytest tests/core/test_provider_cli_backend.py -q`
6. `./.venv/bin/python -m pytest tests/tasks/test_command_runner_parity.py -q`
7. `./.venv/bin/python -m pytest tests/tasks/test_agent_execution.py -q`
8. `./.venv/bin/python -m pytest tests/cli/test_cli_validate.py -q`
9. `./.venv/bin/ruff check weft tests`
10. `./.venv/bin/python -m mypy weft`
11. `./.venv/bin/python -m pytest -q`

Recommended local live verification:

1. one-shot live provider smoke for all installed providers
2. persistent live provider smoke for all installed providers
3. live MCP smoke for providers that claim explicit MCP support
4. local Docker smoke for a build-backed command-task environment profile

## Stop Gates

Stop and re-plan if any of these become true:

- the implementation needs an environment profile to choose a different runner
- the implementation needs a second runner/session orchestration path
- the implementation needs non-host persistent agent sessions to satisfy the
  current plan
- MCP support requires ambient hidden user config instead of explicit declared
  injection
- structured tool profiles balloon into a general tool framework
- Docker-specific fields start leaking into the generic environment-profile
  contract

## Review Checklist

Before you call this plan "implemented," review the final diff against these
questions:

1. Is the environment-profile contract still runner-agnostic?
2. Can a stored TaskSpec remain valid on a machine without Docker, Codex,
   Gemini, or any MCP servers installed?
3. Will a missing dependency fail at the right layer with a clear message?
4. Does the CLI distinguish runner, environment profile, agent runtime, and
   tool profile failures?
5. Do the docs still tell the truth about which runners and providers can do
   what?
6. Did any code path quietly fall back to `host`, to an ambient config, or to a
   smaller tool surface?
7. Do at least some opt-in local tests exercise the full real path?

If any answer is "no" or "unclear," keep working.

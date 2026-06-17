# Microsandbox Runner Implementation Plan
Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-3, CC-3.1, CC-3.2, CC-3.3, CC-3.4]; docs/specifications/02-TaskSpec.md [TS-1, TS-1.3]; docs/specifications/06-Resource_Management.md [RM-5]; docs/specifications/07-System_Invariants.md [EXEC.3, OBS.10, OBS.11]; docs/specifications/13-Agent_Runtime.md [AR-0.0, AR-0.1, AR-7]
Superseded by: none

## Purpose

Add an optional Microsandbox-backed runner that can execute disposable,
probably-hostile workloads behind a stronger isolation boundary than the host
runner and a more VM-like boundary than ordinary container execution.

The runner must support two initial use cases as TaskSpec configuration
differences, not separate execution systems:

1. Tool runner: run a sandboxed command process, including a process that
   happens to speak MCP over stdio.
2. Agent sandbox: run a one-shot provider CLI agent call inside the sandbox.

This plan is intentionally detailed for a skilled engineer who has no Weft
context. Follow it in order. Keep slices small. Use red-green TDD. Do not add a
general sandbox abstraction unless the exact duplication proves it is needed.

## Current Constraints

Microsandbox is an external, beta dependency. Before implementing, verify the
current SDK and CLI surface from the upstream project documentation and source.
Do not hard-code method names from memory or from this plan. Isolate all SDK
calls behind a small adapter module so SDK churn does not leak into Weft core or
the runner plugin contract.

The target platform floor is macOS plus Linux. Task 0 is a hard go/no-go gate,
not a formality. If the current Microsandbox release cannot support both the
intended macOS path and Linux path, stop and ask before implementing a
Linux-only or macOS-only runner. If one platform needs reduced behavior,
document the exact reduction in validation and README text before continuing.

Verify the current Microsandbox package license before adding the dependency.
Earlier investigation found Apache-2.0, but implementation must confirm the
current package metadata. If the license is no longer compatible with this repo,
stop and ask.

Weft's current specs do not define a Microsandbox runner by name. The governing
specs define the runner plugin boundary, TaskSpec runner options, runtime
handles, resource limits, and agent runtime behavior. Implementation must update
the touched specs before the change is considered done.

Microsandbox should be an optional extension package, like Docker and macOS
sandbox. It must not become a required dependency of `weft`.

## Non-Goals

- Do not implement a native MCP client, MCP registry, OAuth flow, tool policy
  engine, or tool-description sanitizer in this slice. "MCP server support"
  means Weft can launch an MCP server process inside the sandbox as a command.
- Do not implement persistent sandbox sessions. Every sandbox is disposable.
- Do not implement built-in provider images in the first slice. The caller must
  provide an image and a guest executable for agent mode.
- Do not depend on the Docker extension from the Microsandbox extension.
- Do not expose host Docker sockets, SSH agents, browser profiles, or broad
  ambient environment to the sandbox.
- Do not claim semantic prompt-injection protection. This runner is a process
  and filesystem boundary, not a semantic boundary.
- Do not make Firecracker, Clear Containers, smol machines, sbx, or raw VM
  backends part of this implementation. Keep those as future runner plugins if
  needed.

## Required Reading

Read these first, in this order:

1. `docs/agent-context/README.md`
2. `docs/agent-context/decision-hierarchy.md`
3. `docs/agent-context/principles.md`
4. `docs/agent-context/engineering-principles.md`
5. `docs/agent-context/runbooks/writing-plans.md`
6. `docs/agent-context/runbooks/hardening-plans.md`
7. `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`
8. `docs/agent-context/runbooks/testing-patterns.md`
9. `docs/specifications/01-Core_Components.md`
10. `docs/specifications/02-TaskSpec.md`
11. `docs/specifications/06-Resource_Management.md`
12. `docs/specifications/07-System_Invariants.md`
13. `docs/specifications/13-Agent_Runtime.md`
14. `extensions/weft_docker/weft_docker/plugin.py`
15. `extensions/weft_docker/weft_docker/agent_runner.py`
16. `extensions/weft_macos_sandbox/weft_macos_sandbox/plugin.py`
17. `weft/core/tasks/runner.py`
18. `weft/ext.py`
19. `weft/core/runners/subprocess_runner.py`
20. `weft/core/runners/host.py`

Use the Docker runner for plugin shape and agent-runtime conformity. Use the
macOS sandbox runner for command-only sandbox process shape. Do not copy either
blindly.

## Existing Architecture To Preserve

Task execution flows through `weft.core.tasks.runner.TaskRunner`. Do not bypass
it. Runner plugins are discovered through the `weft.runners` entry point group
and implement `weft.ext.RunnerPlugin`.

The extension boundary already supports the required shape:

- `RunnerPlugin.validate_taskspec(spec, preflight=False)` for static validation
  and explicit environment checks.
- `RunnerPlugin.check_version()` for optional plugin/runtime compatibility
  checks.
- `RunnerPlugin.create_runner(...)` for backend creation. This is a keyword-only
  contract that receives normalized TaskSpec fields such as `target_type`,
  `tid`, `process_target`, `agent`, `env`, `working_dir`, `timeout`, `limits`,
  `runner_options`, `persistent`, `interactive`, `db_path`, and `config`; it
  does not receive a TaskSpec object.
- `RunnerPlugin.stop(handle)`, `kill(handle)`, and `describe(handle)` for
  runtime control.
- `RunnerHandle` for runtime identity evidence.
- `RunnerOutcome` for completed execution status.

The initial Microsandbox extension should conform to that boundary. If a core
change is required, keep it small and spec-backed.

## High-Level Design

Create a new optional package:

```text
extensions/weft_microsandbox/
  pyproject.toml
  README.md
  weft_microsandbox/
    __init__.py
    _options.py
    _runtime.py
    plugin.py
  tests/
    test_options.py
    test_plugin_validation.py
    test_command_runner.py
    test_microsandbox_agent_runner.py
    test_controls.py
    test_integration_real_microsandbox.py
```

Register the plugin under runner name `microsandbox`:

```toml
[project.entry-points."weft.runners"]
microsandbox = "weft_microsandbox.plugin:get_runner_plugin"
```

Expose behavior through `spec.runner.options`, not new top-level TaskSpec
fields:

```json
{
  "spec": {
    "runner": {
      "name": "microsandbox",
      "options": {
        "mode": "tool",
        "image": "ghcr.io/acme/mcp-tool:latest",
        "network": "none",
        "workspace_mode": "none"
      }
    }
  }
}
```

For agent mode:

```json
{
  "spec": {
    "type": "agent",
    "runner": {
      "name": "microsandbox",
      "options": {
        "mode": "agent",
        "image": "ghcr.io/acme/codex-provider:latest",
        "executable": "codex",
        "network": "allow",
        "workspace_mode": "none"
      }
    },
    "agent": {
      "runtime": "provider_cli",
      "runtime_config": {
        "provider": "codex"
      },
      "persistent": false,
      "conversation_scope": "per_message"
    }
  }
}
```

The explicit `executable` in agent mode is the command path inside the guest
image. It is not a host path. This avoids coupling to Docker's provider image
recipes and avoids pretending Weft can infer what is installed in a user image.

### Mode Rules

`runner.options.mode` may be omitted only when it can be derived without
ambiguity:

- `spec.type == "command"` derives `tool`.
- `spec.type == "agent"` derives `agent`.
- Any other task type is rejected by the Microsandbox runner in the first slice.

If `mode` is present, it must match the task type:

- `mode="tool"` requires `spec.type == "command"`.
- `mode="agent"` requires `spec.type == "agent"`.

Reject `function` tasks. Running arbitrary Python functions inside a fresh
microVM would require packaging code, dependencies, and pickle-safe call
contracts. That is a different design.

### Runner Capabilities

The plugin must report:

```python
RunnerCapabilities(
    supported_types=("command", "agent"),
    supports_interactive=False,
    supports_persistent=False,
    supports_agent_sessions=False,
)
```

This is deliberately conservative. "General agent sandbox" means a disposable
one-shot agent invocation, not a persistent agent session.

### Runtime Handles

Use the existing `RunnerHandle.kind == "sandboxed-process"` unless a later spec
change adds a microVM-specific kind. Do not expand the core handle enum just for
prettier labels.

Use `RunnerHandle.id` as the canonical sandbox runtime id, mirroring Docker's
container handle pattern. Do not put the only copy of the runtime identity in
metadata. Set `control={"authority": "runner"}` because
`RunnerHandle.__post_init__` rejects handles without a non-empty control
authority.

Set handle observations and metadata with runtime evidence:

```python
RunnerHandle(
    runner="microsandbox",
    kind="sandboxed-process",
    id=sandbox_id_or_name,
    control={"authority": "runner"},
    observations={
        "host_pids": [host_pid],
        "host_processes": [{"pid": host_pid, "create_time": create_time}],
    },
    metadata={
        "sandbox_id": sandbox_id_or_name,
        "sandbox_name": sandbox_name,
        "image": image,
        "mode": "tool" | "agent",
        "network": "none" | "allow",
        "workspace_mode": workspace_mode,
    },
)
```

If Microsandbox exposes a host child PID, include it in
`observations["host_pids"]` and `observations["host_processes"]`. If it does
not, omit those observation keys. Do not fabricate a PID.
`docs/specifications/07-System_Invariants.md` says runtime handle evidence is
useful, but queue state remains lifecycle truth.

### Secure Defaults

This runner is for hostile or probably-hostile code. It should intentionally
differ from Docker defaults where host compatibility and security pull in
opposite directions.

Default policy:

- `network`: `"none"` for tool mode, `"none"` for agent mode unless the caller
  explicitly sets `"allow"`.
- `workspace_mode`: `"none"`.
- host environment forwarding: disabled.
- writable host mounts: disabled unless explicitly configured.
- persistent sandboxes: unsupported.

This is a deviation from Docker's more compatibility-oriented defaults. It is
the right default for malicious MCP servers and skills. Document it in the
extension README and TaskSpec spec notes.

This default is intentionally fail-closed. If the installed Microsandbox SDK
cannot actually disable networking, then the default `network="none"` should
fail validation or preflight. Do not silently fall back to network-enabled
execution just to make the first run succeed.

### Workspace Policy

Implement a narrow workspace policy:

- `workspace_mode="none"`: no project directory is mounted or copied.
- `workspace_mode="copy"`: copy a bounded input directory into the sandbox if
  Microsandbox supports it. No writes return to host.
- `workspace_mode="mount-read-only"`: mount explicit host paths read-only if the
  SDK supports host mounts.
- `workspace_mode="mount-read-write"`: allow only with explicit `mounts` entries.
  This is dangerous and must be opt-in.

If the current Microsandbox SDK does not support a policy, reject it with a
clear validation error. Do not silently degrade `read-only` to `read-write`, and
do not silently ignore requested mounts.

### Network And Limits

Map only capabilities that Microsandbox actually supports. The rule is reject,
not pretend.

Initial required behavior:

- `network="none"` should disable outbound networking if the SDK supports it.
  If the SDK cannot disable network, fail validation for `network="none"`.
- `network="allow"` should leave networking enabled.
- `limits.max_connections == 0` must imply `network="none"` unless the runner
  option explicitly conflicts, in which case validation must fail.
- Non-zero `limits.max_connections` is unsupported in the first slice and must
  fail validation.
- CPU, memory, process, and timeout limits may map to Microsandbox controls only
  if verified in the SDK. Unsupported limits must fail validation instead of
  being ignored.
- Existing Weft task timeout still applies at the task runner level.

### Environment And Secrets

Use `spec.env` for explicit guest environment. Do not forward the host ambient
environment by default.

Agent mode often needs provider credentials. For the first slice, credentials
may be passed through explicit `spec.env` only. Document that the guest can read
those values. A proper secret broker or token proxy is out of scope.

Do not add `env_from_host="all"`. If an allowlist is later required, it should be
explicit and reviewed separately.

## Implementation Tasks

Each task below should be a small PR-sized slice. Write the failing test first,
make it pass, then refactor only the code touched by that slice.

### Task 0: Confirm Upstream Microsandbox API And Freeze The Adapter Boundary

Files to read:

- upstream Microsandbox docs and SDK source for the installed version
- upstream package metadata for license and supported platforms
- `extensions/weft_macos_sandbox/weft_macos_sandbox/plugin.py`
- `extensions/weft_docker/weft_docker/plugin.py`
- `extensions/weft_docker/weft_docker/agent_runner.py`

This task is the first implementation gate. Do not start Tasks 4, 6, 7, or 9
until Task 0 records the actual SDK capabilities for:

- macOS support
- Linux support
- network disable
- host mounts
- copy-in or file injection
- guest timeout controls
- host PID exposure
- graceful stop
- force kill
- reconnect by sandbox id

If macOS plus Linux support is not available, or if network disable is not
available, pause implementation and bring the finding back for a decision. A
hostile-workload runner that cannot enforce its default isolation policy should
not ship by accident.

Files to create:

- `extensions/weft_microsandbox/weft_microsandbox/_runtime.py`
- `extensions/weft_microsandbox/tests/test_runtime_adapter.py`

Implement no Weft plugin behavior yet. First define the smallest internal
adapter contract needed by the plugin. It should hide SDK details behind Weft
terms:

```python
@dataclass(frozen=True, slots=True)
class MicrosandboxSpec:
    image: str
    name: str
    command: tuple[str, ...]
    env: Mapping[str, str]
    cwd: str | None
    network: Literal["none", "allow"]
    workspace: WorkspaceSpec
    timeout_seconds: float | None

@dataclass(frozen=True, slots=True)
class MicrosandboxRunResult:
    sandbox_id: str | None
    sandbox_name: str | None
    host_pid: int | None
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
```

The exact names can vary, but keep this boundary small. The plugin should not
import the third-party SDK directly.

Tests:

- Fake only the Microsandbox SDK boundary.
- Verify command, env, network, timeout, and workspace values are passed through
  unchanged.
- Verify SDK errors become a typed extension-local error, not raw arbitrary SDK
  exceptions leaking out.
- Record the supported platform assumptions in test names or fixtures so future
  maintainers do not accidentally make this Linux-only.

Gate:

```bash
./.venv/bin/python -m pytest extensions/weft_microsandbox/tests/test_runtime_adapter.py -q
```

### Task 1: Add Extension Package Skeleton

Files to create:

- `extensions/weft_microsandbox/pyproject.toml`
- `extensions/weft_microsandbox/README.md`
- `extensions/weft_microsandbox/weft_microsandbox/__init__.py`
- `extensions/weft_microsandbox/weft_microsandbox/plugin.py`
- `extensions/weft_microsandbox/tests/conftest.py`
- `extensions/weft_microsandbox/tests/test_plugin_validation.py`

Files to update:

- `pyproject.toml`
- `uv.lock`
- maybe release workflow files, only after asking because repository guidance
  says CI/CD changes require discussion

Package rules:

- package name: `weft-microsandbox`
- import package: `weft_microsandbox`
- entry point: `microsandbox = "weft_microsandbox.plugin:get_runner_plugin"`
- implement the full `RunnerPlugin` protocol, including `check_version()`
- dependency: pin a compatible Microsandbox SDK range after verifying upstream
  packaging; do not leave it unbounded
- include the package in local dev extras only after confirming it does not make
  normal test setup require virtualization

Start with a plugin object that implements `RunnerPlugin`, returns
capabilities, and validates nothing beyond runner name and supported types.

Tests:

- plugin loads through `get_runner_plugin()`
- plugin `name == "microsandbox"`
- capabilities match the conservative values above
- unsupported `spec.type` fails validation

Gates:

```bash
./.venv/bin/python -m pytest extensions/weft_microsandbox/tests/test_plugin_validation.py -q
./.venv/bin/ruff check extensions/weft_microsandbox
./.venv/bin/mypy extensions/weft_microsandbox
```

### Task 2: Implement Runner Option Parsing And Validation

Files to create:

- `extensions/weft_microsandbox/weft_microsandbox/_options.py`
- `extensions/weft_microsandbox/tests/test_options.py`

Files to update:

- `extensions/weft_microsandbox/weft_microsandbox/plugin.py`

Implement a typed option parser. Use dataclasses or Pydantic consistently with
nearby runner code. Prefer a frozen dataclass plus explicit validation if the
shape is small.

Required normalized options:

- `mode: Literal["tool", "agent"]`
- `image: str`
- `executable: str | None`
- `network: Literal["none", "allow"]`
- `workspace_mode: Literal["none", "copy", "mount-read-only", "mount-read-write"]`
- `mounts: tuple[MountSpec, ...]`
- `cwd: str | None`
- `sandbox_name_prefix: str | None`
- `env: Mapping[str, str]` from `spec.env`, not runner options

Validation rules:

- missing `image` fails
- `mode="tool"` requires command task
- `mode="agent"` requires agent task
- omitted mode derives only from `spec.type == "command"` or `"agent"`
- `mode="agent"` requires `runner.options.executable`
- persistent agent config fails
- non-`per_message` conversation scope fails
- interactive command config fails if such a flag exists on the spec
- unknown runner options fail; do not silently ignore typos
- default `network` is `none`
- default `workspace_mode` is `none`
- host-relative mounts must be explicit and normalized with `Path`
- no environment is inherited from the host

DRY guidance:

If Docker has generic mount parsing that can be reused without importing Docker,
prefer a tiny Microsandbox-local parser for the first slice. Extracting a shared
`weft/core/runner_mounts.py` helper is a separate core refactor and should be
done only if copying would be materially worse. If extraction is chosen, make it
a distinct red-green slice with Docker regression tests, and stop if it changes
Docker semantics.

Tests should use real TaskSpec construction helpers where possible. Avoid
building ad hoc dicts if the codebase already has test helpers for TaskSpec.

Gate:

```bash
./.venv/bin/python -m pytest extensions/weft_microsandbox/tests/test_options.py extensions/weft_microsandbox/tests/test_plugin_validation.py -q
```

### Task 3: Add A Core Outcome Status Guard

Files to inspect:

- `weft/core/tasks/consumer.py`
- `weft/core/tasks/runner.py`
- `weft/core/runners/host.py`
- `weft/core/runners/subprocess_runner.py`

Files to update:

- the smallest core file that validates or consumes `RunnerOutcome.status`
- tests near the existing consumer or runner outcome tests

Problem:

Runner plugins can accidentally return a status string that core code does not
understand. A malicious or buggy runner must not produce an ambiguous terminal
state.

Implement a guard so only these statuses are accepted:

- `ok`
- `error`
- `timeout`
- `limit`
- `cancelled`

Unknown statuses should become a failed task with a clear diagnostic. Do not let
unknown statuses look successful or disappear.

Tests:

- a fake runner returns `RunnerOutcome(status="nonsense", ...)`
- the consumer records a failed terminal event
- the error message names the invalid status

Gate:

```bash
./.venv/bin/python -m pytest tests/tasks tests/core -k "runner or outcome or terminal" -q
```

### Task 4: Implement Tool Mode Command Execution

Files to update:

- `extensions/weft_microsandbox/weft_microsandbox/plugin.py`
- `extensions/weft_microsandbox/weft_microsandbox/_runtime.py`
- `extensions/weft_microsandbox/tests/test_command_runner.py`

Files to inspect:

- `weft/core/runners/subprocess_runner.py`
- `weft/core/runners/host.py`
- `extensions/weft_macos_sandbox/weft_macos_sandbox/plugin.py`

Use existing command invocation preparation where possible. The tool mode runner
must respect the same command target and work item behavior as the host and
Docker runners.

Behavior:

- Build guest argv from the command target and work item using the same helper
  used by host-like runners.
- Start one Microsandbox sandbox per work item.
- Publish `on_runtime_handle_started(handle)` as soon as sandbox identity is
  available.
- Publish `on_worker_started(pid)` only if the SDK exposes a meaningful host
  child PID.
- Capture stdout and stderr.
- Return `RunnerOutcome(status="ok")` for exit code 0.
- Return `RunnerOutcome(status="error")` for non-zero exit code.
- Return `RunnerOutcome(status="timeout")` for timeout.
- Always attempt cleanup after completion, timeout, or cancellation.

Testing:

- Use the fake `_runtime.py` adapter to avoid requiring actual virtualization.
- Do not mock `TaskRunner`, queues, or TaskSpec validation in the main behavior
  tests. Run through the real runner backend when practical.
- Include at least one full lifecycle test using the real Weft harness or a real
  `TaskRunner` call with the fake adapter, so stdout/outcome/control hooks prove
  integration rather than just unit method calls.
- Verify stderr is preserved on failure.
- Verify the handle uses `runner="microsandbox"`, `kind="sandboxed-process"`,
  `id` as the sandbox identity, `control={"authority": "runner"}`, host PID
  observations when available, and metadata for mode, image, sandbox name,
  network, and workspace mode.
- Verify cleanup is called on success, failure, and timeout.

Gate:

```bash
./.venv/bin/python -m pytest extensions/weft_microsandbox/tests/test_command_runner.py -q
```

### Task 5: Implement Agent Mode As Provider CLI In Guest

Files to update:

- `extensions/weft_microsandbox/weft_microsandbox/plugin.py`
- `extensions/weft_microsandbox/tests/test_microsandbox_agent_runner.py`
- possibly `weft/core/agent_runtime.py` or the existing provider CLI helper if a
  small, runner-neutral helper is missing

Files to inspect:

- `docs/specifications/13-Agent_Runtime.md`
- `extensions/weft_docker/weft_docker/agent_runner.py`
- provider CLI execution helpers used by Docker
- tests under `extensions/weft_docker/tests/`

Agent mode must reuse the existing provider CLI preparation and output parsing.
Do not invent a second provider runtime. The only new behavior is where the
provider CLI process runs.

Behavior:

- Accept only `spec.type == "agent"`.
- Accept only `runtime == "provider_cli"` in the first slice.
- Require `persistent == false`.
- Require `conversation_scope == "per_message"`.
- Require `runner.options.image`.
- Require `runner.options.executable`, interpreted as the guest executable.
- Build the provider CLI command using the same core helper Docker uses.
- Execute that command inside Microsandbox through `_runtime.py`.
- Parse the provider result through the existing provider CLI result parser.
- Return `RunnerOutcome(status="ok")` only when provider parsing succeeds.
- Return `RunnerOutcome(status="error")` with useful diagnostics when the CLI
  exits non-zero or provider parsing fails.

Do not add automatic provider image lookup in this slice. It is tempting to copy
Docker's provider image recipe registry. Do not. That would couple two optional
extensions or duplicate release-sensitive image logic. Explicit image and
guest executable are good enough for the first safe version.

This intentionally differs from Docker, which can derive executable details from
its provider image recipes or `agent.runtime_config.executable`. Microsandbox
uses `runner.options.executable` in the first slice because the image is
caller-supplied and the executable is guest-local. Document this in
`docs/specifications/13-Agent_Runtime.md` so the two runner surfaces do not
drift silently.

Testing:

- validation rejects unsupported agent runtimes
- validation rejects persistent agents
- validation rejects missing guest executable
- provider CLI command construction includes the prompt/work item in the same
  way Docker agent mode does
- successful fake guest stdout becomes the normal agent result
- malformed fake guest stdout fails the task
- non-zero guest exit fails the task and preserves stderr

Avoid over-mocking:

- Mock only the Microsandbox adapter.
- Use the real provider CLI preparation/parser helpers.
- If provider parsing requires a specific provider fixture, reuse an existing
  Docker/provider test fixture instead of inventing a weaker fake parser.

Gate:

```bash
./.venv/bin/python -m pytest extensions/weft_microsandbox/tests/test_microsandbox_agent_runner.py -q
```

### Task 6: Implement Stop, Kill, Describe, And Cleanup Semantics

Files to update:

- `extensions/weft_microsandbox/weft_microsandbox/plugin.py`
- `extensions/weft_microsandbox/weft_microsandbox/_runtime.py`
- `extensions/weft_microsandbox/tests/test_controls.py`

Files to inspect:

- `extensions/weft_docker/weft_docker/plugin.py`
- `extensions/weft_macos_sandbox/weft_macos_sandbox/plugin.py`
- `docs/specifications/01-Core_Components.md` [CC-3.2]
- `docs/specifications/07-System_Invariants.md` [EXEC.3, OBS.10, OBS.11]

Behavior:

- `stop(handle)` should request graceful sandbox shutdown when the SDK supports
  it.
- `kill(handle)` should request forceful termination when the SDK supports it.
- `describe(handle)` should return best-effort runtime evidence.
- All three methods must use `RunnerHandle.id`, `control`, and `observations`.
  Do not scan unrelated host processes by name.
- Use `handle.id` as the canonical sandbox identity. Metadata can duplicate the
  sandbox id/name for observability, but control paths must not depend on
  metadata as the only identity source.
- If the SDK cannot reconnect to a sandbox by `handle.id`, return `False` for
  `stop` and `kill`, and return the available handle evidence from `describe`.
  Do not pretend control succeeded.
- Cleanup after run completion should be idempotent.

Tests:

- stop delegates to adapter with sandbox id
- kill delegates to adapter with sandbox id
- missing sandbox id returns `False`
- describe returns current runtime info when adapter can resolve it
- cleanup tolerates "already gone"

Gate:

```bash
./.venv/bin/python -m pytest extensions/weft_microsandbox/tests/test_controls.py -q
```

### Task 7: Wire Resource Limits, Network, And Workspace Policy

Files to update:

- `extensions/weft_microsandbox/weft_microsandbox/_options.py`
- `extensions/weft_microsandbox/weft_microsandbox/_runtime.py`
- `extensions/weft_microsandbox/weft_microsandbox/plugin.py`
- `extensions/weft_microsandbox/tests/test_options.py`
- `extensions/weft_microsandbox/tests/test_command_runner.py`

Files to inspect:

- `docs/specifications/06-Resource_Management.md`
- `docs/specifications/06A-Resource_Management_Planned.md`
- Docker limit validation tests

Behavior:

- Map `limits.max_connections == 0` to disabled networking.
- Reject `limits.max_connections > 0`.
- Map CPU, memory, process, and timeout limits only if Microsandbox supports
  them in the verified SDK.
- Reject unsupported CPU/memory/process limits with clear errors.
- Keep Weft-level timeout behavior even when guest timeout is also available.
- Reject unsupported workspace policies.
- Never widen workspace access silently.

Tests:

- `max_connections=0` disables network
- explicit `network="allow"` plus `max_connections=0` fails
- unsupported non-zero max connections fails
- unsupported CPU/memory/process limits fail
- supported limits, if any, pass through to the runtime adapter exactly
- workspace default is none
- read-only mount stays read-only in adapter spec
- read-write mount requires explicit option

Gate:

```bash
./.venv/bin/python -m pytest extensions/weft_microsandbox/tests/test_options.py extensions/weft_microsandbox/tests/test_command_runner.py -q
```

### Task 8: Add Explicit Preflight Behavior

Files to update:

- `extensions/weft_microsandbox/weft_microsandbox/plugin.py`
- `extensions/weft_microsandbox/weft_microsandbox/_runtime.py`
- `extensions/weft_microsandbox/tests/test_plugin_validation.py`

Files to inspect:

- `weft/core/runner_validation.py`
- `weft/_runner_plugins.py`
- Docker preflight tests
- `docs/specifications/10-CLI_Interface.md`

Behavior:

- `validate_taskspec(..., preflight=False)` performs static validation only.
- `validate_taskspec(..., preflight=True)` also checks:
  - Microsandbox SDK is importable
  - required local runtime service or binary is available, if applicable
  - current platform is supported by the installed Microsandbox version, with
    explicit macOS and Linux handling
  - requested isolation knobs are supported
- Ordinary task submission must not perform slow hidden environment probes.
- Preflight error messages should say how to install or enable the extension.

Tests:

- static validation passes without installed runtime probes
- preflight fails cleanly when SDK is unavailable
- preflight fails cleanly on unsupported platform
- preflight validates SDK feature availability for network/workspace policy

Gate:

```bash
./.venv/bin/python -m pytest extensions/weft_microsandbox/tests/test_plugin_validation.py -q
```

### Task 9: Add Optional Real Microsandbox Smoke Tests

Files to create:

- `extensions/weft_microsandbox/tests/test_integration_real_microsandbox.py`

Rules:

- Mark these tests slow or external.
- Skip unless an explicit env var is set, for example
  `WEFT_MICROSANDBOX_TEST_ENABLE=1`.
- Skip with a clear reason if platform or virtualization support is missing.
- Do not require these tests for default local or CI runs unless CI is explicitly
  provisioned for Microsandbox.

Minimum smoke tests:

- run a tiny image that prints `hello`
- verify `network="none"` actually blocks an outbound connection if the SDK
  claims to support network disable
- verify timeout cleanup removes or stops the sandbox

Gate:

```bash
WEFT_MICROSANDBOX_TEST_ENABLE=1 ./.venv/bin/python -m pytest extensions/weft_microsandbox/tests/test_integration_real_microsandbox.py -q -m slow
```

### Task 10: Update Specs And User Documentation

Files to update:

- `docs/specifications/01-Core_Components.md`
- `docs/specifications/02-TaskSpec.md`
- `docs/specifications/06-Resource_Management.md`
- `docs/specifications/07-System_Invariants.md` if handle semantics change
- `docs/specifications/13-Agent_Runtime.md`
- `docs/specifications/00-Quick_Reference.md` if runner quick reference exists
- `extensions/weft_microsandbox/README.md`
- root `README.md` if it lists optional runners
- `docs/plans/README.md`

Required spec updates:

- Document runner name `microsandbox`.
- Document the two modes and derivation rules.
- Document secure defaults and intentional Docker-default differences.
- Document unsupported task types and persistent sessions.
- Document option shape under `spec.runner.options`.
- Document resource-limit mapping and rejection behavior.
- Document that agent mode supports `provider_cli` one-shot only.
- Add plan backlinks from touched spec sections to this plan.
- Keep implementation mapping notes synchronized with code modules.

The README should include one tool-mode example and one agent-mode example. The
README must also say this is a hard process/filesystem boundary attempt, not a
semantic safety mechanism.

Gate:

```bash
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
```

### Task 11: Add Packaging, Type, Lint, And Release Gates

Files to update:

- `pyproject.toml`
- `uv.lock`
- `.github/workflows/...` only after discussion and approval
- any package metadata files matching Docker and macOS sandbox extension style

Behavior:

- Add root optional extra `microsandbox`.
- Add the local source path under `tool.uv.sources` if matching the existing
  extension pattern.
- Include extension in dev verification commands if it can be imported without
  a working microVM runtime.
- Do not make default install pull Microsandbox.
- Do not make default tests require virtualization.

Before changing CI/CD, ask for approval. Repository guidance treats CI/CD edits
as an ask-first action.

Gates:

```bash
uv sync --all-extras
./.venv/bin/python -m pytest extensions/weft_microsandbox/tests -q
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
./.venv/bin/mypy weft bin extensions/weft_docker extensions/weft_macos_sandbox extensions/weft_microsandbox
./.venv/bin/ruff check weft extensions/weft_docker extensions/weft_macos_sandbox extensions/weft_microsandbox
```

### Task 12: Full Contract Verification

Run the smallest gate first after each slice. Before declaring the full runner
done, run:

```bash
uv sync --all-extras
./.venv/bin/python -m pytest
./.venv/bin/mypy weft bin extensions/weft_docker extensions/weft_macos_sandbox extensions/weft_microsandbox
./.venv/bin/ruff check weft extensions/weft_docker extensions/weft_macos_sandbox extensions/weft_microsandbox
```

If real Microsandbox integration is enabled on the machine:

```bash
WEFT_MICROSANDBOX_TEST_ENABLE=1 ./.venv/bin/python -m pytest extensions/weft_microsandbox/tests/test_integration_real_microsandbox.py -q -m slow
```

Check these invariants manually in review:

- TaskRunner remains the only task execution facade.
- No core import of the Microsandbox SDK exists.
- No Docker extension import exists in the Microsandbox extension.
- Unknown runner outcome statuses cannot produce a successful task.
- Unsupported limits fail validation.
- Default network is disabled.
- Default workspace access is none.
- Runtime handle metadata never becomes lifecycle truth.
- Agent mode uses existing provider CLI preparation and parsing.
- Persistent sessions are rejected.
- Tests fake only the external SDK boundary, not Weft queues and task lifecycle.

## Failure Modes To Handle

| Failure | Expected behavior |
| --- | --- |
| Microsandbox SDK missing | static validation can pass; preflight fails with install guidance |
| Unsupported platform | preflight fails clearly |
| Unsupported network disable | validation fails when `network="none"` is requested |
| Guest exits non-zero | task fails with stderr preserved |
| Guest times out | task status is timeout and cleanup is attempted |
| Cleanup sees already-removed sandbox | cleanup succeeds idempotently |
| SDK loses sandbox id | handle has available evidence; stop/kill return false |
| Unknown runner outcome status | core converts to failed terminal task |
| Provider output parse failure | agent task fails with parser diagnostic |
| User requests persistent agent | validation fails before execution |
| User typo in runner option | validation fails; unknown options are not ignored |

## Review Requirements

This is a risky execution-path change. It crosses the runner plugin boundary,
agent runtime behavior, process control, and resource policy. Before
implementation lands:

- Run an independent review after Task 4.
- Run another independent review after Task 8.
- Run a final independent review before merge.
- Prefer a different agent family from the implementer for those reviews.

Reviews should focus on contract drift, unsupported capability claims, test
over-mocking, resource cleanup, and accidental host access.

## Rollback Plan

Because this is an optional extension, rollback should be simple:

- Remove the `weft-microsandbox` extra and local source entry.
- Disable or remove the entry point package.
- Leave core outcome-status hardening in place; it is independently useful.
- Revert Microsandbox-specific spec and README sections if the extension is not
  shipped.

No queue migration should be required. No existing TaskSpec should change unless
the user opted into `runner.name="microsandbox"`.

## Fresh-Eyes Self-Review

I reviewed the plan for drift, ambiguity, and bad engineering decisions. These
were the main issues found and fixed:

1. The first draft risked making "microVM" a new core runner kind. That would
   have widened the core contract for little value. The plan now reuses
   `sandboxed-process` and puts the canonical Microsandbox identity in
   `RunnerHandle.id`.
2. "General agent sandbox" was too broad. The plan now defines it narrowly as a
   disposable `provider_cli` one-shot invocation with persistent sessions
   rejected.
3. Agent image behavior was ambiguous. The plan now requires an explicit image
   and guest executable instead of copying Docker provider image recipes.
4. Tool-runner MCP support was too easy to overread as native MCP integration.
   The plan now states clearly that MCP support means launching a stdio process
   in the sandbox.
5. Outcome status validation was a latent core risk. The plan now includes a
   small core hardening task before relying on a new plugin.
6. The test strategy initially leaned too much on adapter fakes. The plan now
   requires at least one real Weft lifecycle path with only the Microsandbox SDK
   boundary faked, plus optional real virtualization smoke tests.
7. Secure defaults conflicted with Docker conformity. The plan now calls this
   out as an intentional difference because the stated use case is malicious
   workloads.
8. External review found concrete contract errors: `RunnerHandle.host_processes`
   does not exist, `control.authority` is required, `create_runner` is
   keyword-only, and `check_version` is part of the protocol. The plan now names
   the actual fields and required plugin methods.
9. External review also found that the macOS plus Linux floor and
   `network="none"` default are hard gates, not background uncertainty. The plan
   now treats Task 0 as a go/no-go gate before the sandbox execution tasks.

Residual uncertainty:

- The exact Microsandbox SDK methods and supported isolation knobs must be
  verified at implementation time.
- Real network-disable and mount behavior must be proven by an optional
  integration test on a machine that supports Microsandbox.
- CI/release workflow changes need explicit approval before implementation.

## GSTACK REVIEW REPORT

| Run | Status | Findings |
| --- | --- | --- |
| Initial self-review | Applied | Narrowed agent mode, rejected native MCP scope, added outcome-status guard, added secure defaults, and required real Weft lifecycle tests. |
| External plan review attachment | Applied | Fixed `RunnerHandle` fields, required `control.authority`, corrected `create_runner(...)`, added `check_version()`, moved canonical sandbox identity to `handle.id`, and made Task 0 a hard capability gate. |
| Metadata verification | Passed | `tests/specs/test_plan_metadata.py` passes for the plan header and README index entry. |

VERDICT: ready for implementation as a draft plan after Task 0 verifies the
current Microsandbox SDK surface, license, and macOS plus Linux capability
floor.

Required follow-up before coding:

- Verify current Microsandbox SDK/CLI API and supported platform matrix.
- Treat CI/release workflow edits as a separate gated step that needs approval;
  they are not required for the first runner implementation PR.

NO UNRESOLVED DECISIONS

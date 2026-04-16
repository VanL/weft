# Provider CLI Validation Boundary and Agent Settings Alignment Plan

Status: proposed
Source specs: see Source Documents below
Superseded by: none

This plan fixes the current `provider_cli` startup-boundary mistake and aligns
the delegated-agent UX with Weft's actual role. Weft should know how to invoke
common agent CLIs, validate the static parts it owns, and surface execution
failures clearly. It should not silently probe provider processes during
TaskRunner construction, and it should not become the system that owns provider
login state, API keys, or agent account setup.

This is risky boundary work. It touches:

`TaskSpec -> validate -> TaskRunner -> provider_cli backend -> provider registry -> .weft project settings -> docs/tests`

The likely failure modes are:

- keeping subprocess probes in startup validation under a new name,
- moving dynamic checks to a second hidden bootstrap path,
- treating observed provider health as authoritative runtime truth,
- overloading `.weft/config.json` with unrelated agent runtime state,
- or expanding Weft into a broad agent-management surface rather than keeping
  it as a substrate.

Do not do those things.

## Scope Lock

Implement exactly this slice:

1. remove delegated provider subprocess probes from ordinary startup/runtime
   validation and `TaskRunner` construction
2. keep static validation useful:
   runtime resolution, executable-path resolution, tool-profile shape, and
   MCP command resolution when explicitly requested
3. move any dynamic provider capability checks to first real delegated
   execution or first real session-open
4. support explicit user-authored project-local agent settings in `.weft/`
   for launch configuration, not for login truth
5. support an advisory observed-health cache in `.weft/` that may help UX and
   diagnostics but never gates startup correctness
6. tighten failure reporting so provider invocation errors are surfaced with
   the provider name, executable, command class, exit status, and compact
   stderr/stdout detail
7. include documentation cleanup in scope so the specs and CLI docs describe
   the new validation boundary honestly

Do not implement any of the following in this slice:

- a hidden autorun or startup sidecar that becomes required for correctness
- a new durable manager/service for agent health
- login, token, or API-key setup flows in Weft core
- a general provider package manager or installer
- a broad "agent control plane" beyond explicit launch config and advisory
  health metadata
- a second execution path outside the current durable spine
- a public CLI expansion larger than the minimum needed to keep docs honest

If implementation pressure pulls toward any excluded item, stop and split a
follow-on plan.

## Goal

Make delegated provider validation honest and cheap. Static validation should
validate only static facts. Dynamic provider behavior should be checked when
Weft actually tries to use the provider. User-authored provider settings should
live in `.weft/` as explicit project configuration, while any observed health
metadata should remain advisory only.

## Design Position

This plan should lock in five boundary rules:

- `TaskSpec` schema validation is portable and machine-independent.
- explicit runtime validation may resolve local code paths and executables, but
  it should not spawn delegated provider processes unless the user explicitly
  asks for diagnostics
- actual provider behavior is proven only by actual delegated execution or
  session startup
- user-authored `.weft/` agent settings are authoritative configuration
- machine-observed provider status in `.weft/` is advisory metadata, not
  correctness truth

The MCP analogy is the right one: a `.weft/` agent settings file should say
what to launch and how to launch it. It should not pretend that launch success,
auth state, or runtime capabilities are durably true.

## Source Documents

Primary specs:

- [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
  [AR-2.1], [AR-5], [AR-7]
- [`docs/specifications/13A-Agent_Runtime_Planned.md`](../specifications/13A-Agent_Runtime_Planned.md)
  [AR-A3], [AR-A5], [AR-A8]
- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
  [TS-1], [TS-1.3], [TS-1.4]
- [`docs/specifications/04-SimpleBroker_Integration.md`](../specifications/04-SimpleBroker_Integration.md)
  [SB-0]
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
  [CLI-1.1.1], [CLI-1.4.1]
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [IMMUT.1], [IMMUT.2], [STATE.1], [STATE.2], [QUEUE.1], [QUEUE.2],
  [QUEUE.3], [IMPL.5], [IMPL.6]

Local guidance and review standards:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/README.md`](../agent-context/README.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)

Existing plans to align with, not replace:

- [`docs/plans/2026-04-13-delegated-agent-runtime-phase-2-implementation-plan.md`](./2026-04-13-delegated-agent-runtime-phase-2-implementation-plan.md)
- [`docs/plans/2026-04-13-delegated-agent-authority-boundary-cleanup-plan.md`](./2026-04-13-delegated-agent-authority-boundary-cleanup-plan.md)

Source spec note:

- there is no existing spec section that cleanly defines project-local agent
  settings and advisory provider-health metadata under `.weft/`; this plan
  includes that doc alignment

## Context and Key Files

Files that will likely change in this slice:

- `docs/specifications/13-Agent_Runtime.md`
- `docs/specifications/13A-Agent_Runtime_Planned.md`
- `docs/specifications/02-TaskSpec.md`
- `docs/specifications/04-SimpleBroker_Integration.md`
- `docs/specifications/10-CLI_Interface.md`
- `weft/core/agents/validation.py`
- `weft/core/tasks/runner.py`
- `weft/core/agents/backends/provider_cli.py`
- `weft/core/agents/provider_cli/registry.py`
- `weft/context.py`
- `weft/_constants.py`
- likely new helper module:
  `weft/core/agents/provider_cli/settings.py`
- `tests/core/test_agent_validation.py`
- `tests/core/test_provider_cli_backend.py`
- `tests/core/test_provider_cli_session_backend.py`
- `tests/core/test_tool_profiles.py`
- `tests/tasks/test_agent_execution.py`
- `tests/cli/test_cli_validate.py`
- `tests/fixtures/provider_cli_fixture.py`

Read first:

1. [`weft/core/agents/validation.py`](../../weft/core/agents/validation.py)
2. [`weft/core/tasks/runner.py`](../../weft/core/tasks/runner.py)
3. [`weft/core/agents/backends/provider_cli.py`](../../weft/core/agents/backends/provider_cli.py)
4. [`weft/core/agents/provider_cli/registry.py`](../../weft/core/agents/provider_cli/registry.py)
5. [`weft/context.py`](../../weft/context.py)
6. [`tests/fixtures/provider_cli_fixture.py`](../../tests/fixtures/provider_cli_fixture.py)
7. [`tests/core/test_agent_validation.py`](../../tests/core/test_agent_validation.py)
8. [`tests/core/test_provider_cli_backend.py`](../../tests/core/test_provider_cli_backend.py)
9. [`tests/tasks/test_agent_execution.py`](../../tests/tasks/test_agent_execution.py)

Current structure:

- `TaskRunner.__init__()` currently calls
  `validate_taskspec_agent_runtime(..., preflight=True)` for agent tasks, which
  makes provider preflight part of startup
- `ProviderCLIBackend.validate()` currently calls `provider.probe(executable)`
  during preflight
- `_probe_version()` runs `[executable, "--version"]` with a hard timeout
- `_supports_opencode_run()` uses another synthetic probe path
- `.weft/config.json` currently serves as project metadata, not as a dedicated
  agent settings file
- the fixture wrappers in `tests/fixtures/provider_cli_fixture.py` are real
  executables, but even `--version` is expensive because the wrapper launches
  Python and imports repo code

Shared helpers and paths to reuse:

- `resolve_provider_cli_executable(...)` in
  `weft/core/agents/provider_cli/registry.py`
- `build_context(...)` and `WeftContext` in `weft/context.py`
- current agent validation entry points in `weft/core/agents/validation.py`
- current provider error compaction helpers in
  `weft/core/agents/provider_cli/registry.py`

Do not create a second validation stack. Keep one shared static-validation path
plus one shared dynamic execution path.

## Comprehension Questions

If the implementer cannot answer these before editing, they are not ready:

1. Which current call path is causing provider subprocesses to run during
   startup rather than during actual delegated execution?
2. Which parts of delegated-agent configuration belong in user-authored
   `.weft/` settings, and which parts are only runtime observations?
3. If a provider CLI is installed but not logged in, where should that failure
   surface after this change: schema validation, runtime validation, or first
   real execution?

## Invariants and Constraints

The following must stay true:

- the current durable spine stays the only execution path
- `TaskSpec` schema validation stays machine-independent
- TIDs, queue names, and forward-only state transitions remain unchanged
- `spec` and `io` remain immutable after resolved `TaskSpec` creation
- `provider_cli` remains a delegated lane; Weft does not take ownership of
  provider login/auth state
- explicit TaskSpec runtime config stays authoritative over project defaults
- project-local agent settings may select launch details; they may not silently
  widen authority beyond the Weft-declared authority class
- advisory health cache data must never make a broken provider look healthy or
  a healthy provider look required for startup
- docs must stop claiming that `--preflight` proves provider runtime health if
  it no longer does

Review gates for this slice:

- no new execution path
- no hidden background startup dependency
- no drive-by refactor
- no new provider abstraction layer unless it directly simplifies this
  boundary split
- no mock-heavy substitute for the real delegated execution path where the
  existing wrapper-backed tests can prove the contract

## Rollout and Rollback

Rollout order matters:

1. lock the doc model and validation vocabulary first
2. remove startup probes from the validation path
3. move any still-needed dynamic checks to first execution/session-open
4. add project-local settings and advisory cache support
5. update tests and CLI docs together

Rollback rule:

- this change must be revertible without queue migration, TaskSpec migration,
  or broker-data migration

Compatibility rule:

- existing TaskSpecs with explicit provider executables must continue to work
- missing project-local agent settings must preserve current resolution
  behavior: explicit TaskSpec executable, then configured project default if
  present, then provider default executable name

## Tasks

1. Lock the validation boundary in the specs before code changes.
   - Outcome: docs say plainly that startup validation is static and that
     provider health/auth is proven only by actual delegated execution or an
     explicit diagnostic path.
   - Files to touch:
     - `docs/specifications/13-Agent_Runtime.md`
     - `docs/specifications/13A-Agent_Runtime_Planned.md`
     - `docs/specifications/02-TaskSpec.md`
     - `docs/specifications/04-SimpleBroker_Integration.md`
     - `docs/specifications/10-CLI_Interface.md`
   - Read first:
     [AR-5], [AR-7], [TS-1], [SB-0], [CLI-1.4.1]
   - Make these doc changes:
     - separate schema validation, runtime validation, and dynamic execution
       checks in the text
     - change `spec validate --preflight` language so it no longer claims
       provider capability probes or provider health proof
     - describe `.weft/` agent settings as explicit launch configuration
     - describe `.weft/` agent health as advisory metadata only
   - Stop and re-evaluate if the wording starts defining login flows,
     token-management flows, or broad agent lifecycle management.

2. Split static validation from dynamic provider checks in code.
   - Outcome: `TaskRunner` startup and `validate_taskspec_agent_runtime()`
     stop spawning delegated provider processes.
   - Files to touch:
     - `weft/core/agents/validation.py`
     - `weft/core/tasks/runner.py`
     - `weft/core/agents/backends/provider_cli.py`
     - `weft/commands/validate_taskspec.py`
     - `tests/core/test_agent_validation.py`
     - `tests/cli/test_cli_validate.py`
   - Read first:
     current `validate_taskspec_agent_runtime(...)`,
     `ProviderCLIBackend.validate()`,
     `TaskRunner.__init__()`,
     and [CLI-1.4.1]
   - Required behavior:
     - ordinary schema validation remains unchanged
     - runtime validation may load runtimes and resolve executables
     - delegated tool-profile validation may still resolve stdio MCP commands
     - no provider subprocess is launched from startup validation
     - `TaskRunner.__init__()` must not request delegated provider preflight
   - Keep the validation API narrow. Prefer extending the existing validation
     vocabulary over adding parallel helper stacks.
   - Stop and re-evaluate if the code starts introducing a second "startup
     bootstrap" path for delegated agents.

3. Move dynamic provider checks to first real execution or session-open.
   - Outcome: dynamic provider capability checks happen where Weft actually
     uses the provider CLI, not during startup.
   - Files to touch:
     - `weft/core/agents/provider_cli/registry.py`
     - `weft/core/agents/backends/provider_cli.py`
     - `tests/core/test_provider_cli_backend.py`
     - `tests/core/test_provider_cli_session_backend.py`
     - `tests/tasks/test_agent_execution.py`
   - Read first:
     provider invocation builders, current `_probe_version()`,
     `_supports_opencode_run()`, and the execution/session tests
   - Required behavior:
     - remove the unconditional `--version` startup probe from the normal path
     - for provider-specific dynamic requirements, check them only at the
       first real call or first real session-open
     - prefer the actual provider call path over synthetic probes whenever the
       real failure signal is clear enough
     - if a synthetic capability probe remains justified for one provider, it
       must be lazy, memoized, and clearly documented as an execution-time
       check rather than a startup gate
   - In-process memoization is required. Do not re-run the same dynamic check
     on every work item inside one process.
   - Stop and re-evaluate if the implementation tries to preserve
     `_probe_version()` semantics just by moving them later.

4. Add explicit project-local agent settings and advisory health storage.
   - Outcome: users can declare provider launch settings in `.weft/`, and
     Weft can record observed provider health without confusing the two.
   - Files to touch:
     - `weft/context.py`
     - `weft/_constants.py`
     - likely new file:
       `weft/core/agents/provider_cli/settings.py`
     - `weft/core/agents/provider_cli/registry.py`
     - docs from task 1
     - focused tests for path resolution and cache semantics
   - Default file layout:
     - explicit config: `.weft/agents.json`
     - advisory observations: `.weft/agent-health.json`
   - Use a dedicated file pair by default. Do not overload `.weft/config.json`,
     which currently reads as project metadata rather than delegated-runtime
     settings.
   - Suggested precedence:
     1. explicit TaskSpec runtime config
     2. `.weft/agents.json`
     3. provider built-in default executable
   - Suggested settings scope:
     - provider executable/command
     - optional default args or similar launch-shape settings, if truly needed
   - Explicitly out of scope for the settings file:
     - login status
     - token presence
     - API key validation
     - mutable "healthy/unhealthy" truth
   - Health cache semantics:
     - best-effort write on real execution/session-open outcomes
     - may store observed version, last success, last failure, and compact
       error summary
     - never required for correctness
   - Structure this so a future explicit probe task or optional background job
     can populate the health cache without changing the validation boundary.

5. Tighten delegated error surfacing and verification.
   - Outcome: first-call delegated failures are actionable, and the test suite
     proves the new boundary clearly.
   - Files to touch:
     - `weft/core/agents/provider_cli/registry.py`
     - `weft/core/agents/backends/provider_cli.py`
     - `weft/commands/validate_taskspec.py`
     - `tests/core/test_agent_validation.py`
     - `tests/core/test_provider_cli_backend.py`
     - `tests/core/test_provider_cli_session_backend.py`
     - `tests/core/test_tool_profiles.py`
     - `tests/tasks/test_agent_execution.py`
   - Required error behavior:
     - missing executable: fail in runtime validation with a clear path/name
     - unsupported provider invocation surface: fail on first real call with
       provider name, executable, command class, and compact stderr/stdout
     - auth/config/runtime failures: surface on first real call, not startup
     - tool-profile and authority conflicts: still fail before execution,
       because those are Weft-owned static constraints
   - Required tests:
     - startup validation no longer launches provider subprocesses
     - isolated and full-suite delegated tests no longer fail on startup probe
       timeouts
     - first real execution/session-open surfaces provider errors clearly
     - project-local settings resolution precedence works
     - advisory cache writes do not affect correctness when stale or missing
   - Finish with the full suite, not only the delegated subset.

## Verification

Minimum verification for implementation:

- `source .envrc && uv run pytest tests/core/test_agent_validation.py -q`
- `source .envrc && uv run pytest tests/core/test_provider_cli_backend.py -q`
- `source .envrc && uv run pytest tests/core/test_provider_cli_session_backend.py -q`
- `source .envrc && uv run pytest tests/core/test_tool_profiles.py -q`
- `source .envrc && uv run pytest tests/tasks/test_agent_execution.py -q`
- `source .envrc && uv run pytest tests/cli/test_cli_validate.py -q`
- `source .envrc && uv run pytest`
- `source .envrc && uv run ruff check weft tests`
- `source .envrc && uv run mypy weft`

The full-suite run is mandatory here because the original bug was a
full-suite startup-boundary regression, not just a single targeted test miss.

## Review Loop

This plan should receive an independent review before implementation because it
changes validation semantics, `.weft/` settings shape, and delegated runtime
error behavior across multiple layers.

# Runner Extension Point Plan

Status: proposed
Source specs: see Source Documents below
Superseded by: none

This document is the implementation plan for adding a **well-designed runner
extension point** to Weft so TaskSpecs can declare **how** they execute
without changing **what** they execute.

The target reader is a strong Python engineer with almost no Weft or
SimpleBroker context and unreliable instincts around scope, abstractions, and
test design. Assume they will:

- overbuild a general plugin framework if the plan leaves room for it,
- branch inside the current `TaskRunner` instead of creating a real extension
  seam,
- conflate `spec.type` with runner choice,
- over-mock tests instead of exercising real broker-backed execution paths,
- and accidentally make validation machine-dependent in ways that break stored
  TaskSpec templates.

This plan does not leave room for those mistakes.

It is written as **bite-sized tasks**. Each task tells the implementer:

- what to read first,
- which files to touch,
- which tests to write first,
- which invariants must hold,
- and which gates must pass before moving on.

## 0. Scope Lock

Implement exactly these outcomes:

1. Weft gains a first-class runner selection field in `TaskSpec`:
   - `spec.type` remains the target kind (`function | command | agent`),
   - `spec.runner.name` selects the execution backend,
   - `spec.runner.options` holds runner-specific JSON-serializable options.
2. The current built-in local subprocess behavior becomes the built-in
   `host` runner implementation instead of remaining the only hard-coded path.
3. Weft gains a public runner plugin contract and an internal plugin resolver,
   following the same high-level pattern as SimpleBroker backend plugins.
4. Runner resolution, validation, and execution extend the existing
   `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log` spine.
   Do not create a second execution path.
5. Task control (`stop`, `kill`) and observability stop assuming that every
   running task is controlled purely by host PID. Core control paths use a
   runner-owned runtime handle.
6. Schema validation stays pure by default. Runtime availability checks are
   opt-in in validation flows and mandatory at execution time.
7. Weft can ship optional runner extensions through extras and separate
   packages under `extensions/`, mirroring the `simplebroker-pg` pattern.
8. The first non-host runners are:
   - `docker`
   - `macos-sandbox`
9. For the first implementation slice, non-host runners support only
   **one-shot command tasks**.
10. Existing `host` behavior for `function`, `command`, `agent`, interactive,
    persistent, streaming, limits, and task control keeps working.

Do **not** implement any of the following in this slice:

- changing `spec.type` to mean runner kind,
- a second execution/orchestration plane separate from `Consumer` and
  `TaskRunner`,
- a new task lifecycle model,
- a new durable state store outside queues,
- a generic "plugin manager" framework broader than runner plugins,
- generalized remote execution for Python functions or agent sessions,
- non-host interactive sessions,
- non-host persistent tasks,
- a Docker or macOS CI runtime provisioning project,
- or SimpleBroker changes in `../simplebroker`.

If implementation pressure starts pulling toward any of those, stop. That is
scope drift.

## 1. Step 0 Scope Challenge

This change touches a shared public contract, so it will touch more than eight
files. That is acceptable here, but only because the minimum correct cut is
cross-cutting:

- `TaskSpec` schema changes
- runner resolution changes
- execution/control changes
- validation CLI changes
- docs/spec updates
- extension packaging

The **minimum** acceptable cut is still:

1. add `spec.runner`,
2. create a runner plugin seam,
3. convert the current built-in behavior into `host`,
4. generalize runtime handles for stop/kill,
5. add schema/load/preflight validation layers,
6. add first-party `docker`,
7. add first-party `macos-sandbox`.

Do **not** "simplify" this by hard-coding:

- `if spec.runner.name == "docker"` branches in the current
  [`weft/core/tasks/runner.py`](../../weft/core/tasks/runner.py),
- PID-only stop/kill paths that special-case Docker later,
- or runtime checks inside ordinary schema validation.

That would be a false simplification and would need to be torn back out later.

## 2. What Already Exists

Reuse these existing paths instead of rebuilding them:

- [`weft/core/taskspec.py`](../../weft/core/taskspec.py)
  already owns TaskSpec schema validation and resolved-task default expansion
  through `resolve_taskspec_payload()`.
- [`weft/core/tasks/runner.py`](../../weft/core/tasks/runner.py)
  already owns the monitored execution boundary and the session entry points.
  Keep `TaskRunner` as the public façade; do not invent a sibling runner
  façade elsewhere.
- [`weft/core/tasks/consumer.py`](../../weft/core/tasks/consumer.py)
  and [`weft/core/tasks/interactive.py`](../../weft/core/tasks/interactive.py)
  already own task execution entry and session wiring. Extend those call sites;
  do not add a parallel execution path.
- [`weft/core/tasks/sessions.py`](../../weft/core/tasks/sessions.py)
  already defines the session surfaces used by interactive and agent flows.
- [`weft/core/resource_monitor.py`](../../weft/core/resource_monitor.py)
  already encapsulates host-process monitoring. Keep it as the `host` runner’s
  monitoring implementation.
- [`weft/commands/tasks.py`](../../weft/commands/tasks.py)
  already owns CLI stop/kill behavior. Change its control input, not its job.
- [`weft/context.py`](../../weft/context.py)
  already normalizes missing-plugin errors for backends. Reuse that style for
  missing runner plugins.
- [`../simplebroker/simplebroker/_backend_plugins.py`](../../../simplebroker/simplebroker/_backend_plugins.py)
  already demonstrates the entry-point loader shape and version checks that
  Weft should mirror at a smaller scale.

## 3. Source Documents

Read these before editing code:

1. [`AGENTS.md`](../../AGENTS.md)
2. [`docs/agent-context/README.md`](../agent-context/README.md)
3. [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
4. [`docs/agent-context/principles.md`](../agent-context/principles.md)
5. [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
6. [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
7. [`docs/agent-context/runbooks/testing-patterns.md`](../agent-context/runbooks/testing-patterns.md)
8. [`docs/agent-context/runbooks/runtime-and-context-patterns.md`](../agent-context/runbooks/runtime-and-context-patterns.md)
9. [`docs/specifications/00-Overview_and_Architecture.md`](../specifications/00-Overview_and_Architecture.md)
10. [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md)
11. [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
12. [`docs/specifications/06-Resource_Management.md`](../specifications/06-Resource_Management.md)
13. [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
14. [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
15. [`README.md`](../../README.md)
16. [`weft/core/taskspec.py`](../../weft/core/taskspec.py)
17. [`weft/core/tasks/runner.py`](../../weft/core/tasks/runner.py)
18. [`weft/core/tasks/consumer.py`](../../weft/core/tasks/consumer.py)
19. [`weft/core/tasks/interactive.py`](../../weft/core/tasks/interactive.py)
20. [`weft/core/tasks/sessions.py`](../../weft/core/tasks/sessions.py)
21. [`weft/core/tasks/base.py`](../../weft/core/tasks/base.py)
22. [`weft/core/manager.py`](../../weft/core/manager.py)
23. [`weft/commands/run.py`](../../weft/commands/run.py)
24. [`weft/commands/tasks.py`](../../weft/commands/tasks.py)
25. [`weft/commands/validate_taskspec.py`](../../weft/commands/validate_taskspec.py)
26. [`weft/context.py`](../../weft/context.py)
27. [`pyproject.toml`](../../pyproject.toml)
28. [`tests/taskspec/test_taskspec.py`](../../tests/taskspec/test_taskspec.py)
29. [`tests/tasks/test_runner.py`](../../tests/tasks/test_runner.py)
30. [`tests/tasks/test_task_interactive.py`](../../tests/tasks/test_task_interactive.py)
31. [`tests/core/test_manager.py`](../../tests/core/test_manager.py)
32. [`tests/commands/test_task_commands.py`](../../tests/commands/test_task_commands.py)
33. [`tests/cli/test_cli_run.py`](../../tests/cli/test_cli_run.py)
34. [`tests/cli/test_cli_validate.py`](../../tests/cli/test_cli_validate.py)
35. [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py)
36. [`tests/conftest.py`](../../tests/conftest.py)

Read these SimpleBroker files next so the plugin pattern is copied from the
real local source, not memory:

1. [`../simplebroker/simplebroker/_backend_plugins.py`](../../../simplebroker/simplebroker/_backend_plugins.py)
2. [`../simplebroker/simplebroker/ext.py`](../../../simplebroker/simplebroker/ext.py)
3. [`../simplebroker/simplebroker/project.py`](../../../simplebroker/simplebroker/project.py)
4. [`../simplebroker/extensions/simplebroker_pg/pyproject.toml`](../../../simplebroker/extensions/simplebroker_pg/pyproject.toml)

What you are learning from those files:

- public plugin contracts can stay small and still be usable,
- entry-point loading belongs behind one internal resolver,
- user-facing install hints should be centralized,
- and extras + separate extension packages can coexist cleanly.

## 4. Engineering Rules

These rules are mandatory.

### 4.1 DRY

- One runner resolution path.
- One runtime-handle serialization shape.
- One runner validation helper per phase.
- One built-in `host` implementation of current behavior.
- One missing-plugin error normalizer.

Do not duplicate slightly different versions of those rules in CLI, manager,
consumer, and tests.

### 4.2 YAGNI

- Do not create a general remote execution framework.
- Do not design for arbitrary capability negotiation beyond what current Weft
  needs.
- Do not add a second validation framework when a small helper layer over
  `validate_taskspec()` is enough.
- Do not add new session types unless a current code path needs them.
- Do not build a general config schema system for runner options. Runner
  plugins may validate their own `options` payloads.

### 4.3 Red-Green TDD

For every task below:

1. write the smallest failing test first,
2. make it pass with the smallest coherent implementation,
3. refactor only after the behavior is locked in.

Do not start with a large refactor and hope the existing tests cover it.

### 4.4 Test Design

- Prefer real `TaskSpec` objects, real broker queues, real `TaskRunner`
  execution, real `Consumer` flows, and `WeftTestHarness`.
- Do not mock `simplebroker.Queue`.
- Do not mock `TaskRunner` in tests meant to prove lifecycle behavior.
- Do not mock stop/kill behavior with fake PID lists.
- Do not fake plugin packaging by inventing an internal-only registry unless
  there is no cleaner way to exercise entry-point discovery.
- Use monkeypatch only at genuine external boundaries:
  - `importlib.metadata.entry_points()`
  - runtime availability probes (`docker version`, macOS sandbox tool probe)
  - import failures for missing plugin simulation

### 4.5 Minimal Diff

- Keep `TaskRunner` as the public façade to minimize blast radius.
- Add new modules only when they create a durable seam:
  - runner plugin contract,
  - runner resolver,
  - host plugin implementation,
  - extension packages.
- Do not rename unrelated files or refactor neighboring code for style.

## 5. Fixed Design Decisions

These decisions are not open during implementation.

### 5.1 `spec.type` Remains the Target Kind

Do **not** overload `spec.type` with `docker` or `macos-sandbox`.

The public shape becomes:

```jsonc
"spec": {
  "type": "command",
  "runner": {
    "name": "host",
    "options": {}
  }
}
```

`spec.type` answers: **what kind of work is this?**

`spec.runner.name` answers: **where/how should that work execute?**

This separation is not optional.

### 5.2 `TaskRunner` Stays, But Becomes a Façade

Do not replace every call site with a new class name.

Keep [`weft/core/tasks/runner.py`](../../weft/core/tasks/runner.py) exporting
`TaskRunner`, `RunnerOutcome`, and the current session types. Internally:

- `TaskRunner` resolves the selected runner plugin,
- builds the runner-specific implementation,
- and delegates to it.

The current built-in logic becomes the `host` implementation under a new
runner-focused module path. The façade stays at the old import path to minimize
blast radius.

### 5.3 Runtime Handle Replaces PID-Only Control

Core control and observability must move from:

- `pid`
- `managed_pids`

to a runner-owned handle shape, while still preserving host PID fields as
derived convenience fields for observability and transitional compatibility.

Use a small JSON-serializable handle model, for example:

```python
@dataclass(frozen=True, slots=True)
class RunnerHandle:
    runner_name: str
    runtime_id: str
    host_pids: tuple[int, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
```

Queue payloads should store the handle through an explicit `to_dict()` /
`from_dict()` shape so tuples become JSON lists predictably. Do not rely on
implicit dataclass serialization.

Use this shape in:

- runtime callbacks from `TaskRunner`,
- session objects,
- TID mapping payloads,
- and CLI stop/kill resolution.

### 5.4 Validation Is Layered

There are three distinct validation phases:

```text
schema validation  ->  load validation  ->  runtime preflight
pure / portable        plugin installed     runtime available now
no imports             option shape         daemon/tool/image/profile/etc.
```

Rules:

- `validate_taskspec()` stays schema-only by default.
- CLI validation gains opt-in deeper checks.
- execution-time preflight is still mandatory even if CLI preflight already ran.

Do **not** make ordinary schema validation import plugins or probe Docker.

### 5.5 Non-Host Runners Are Command-Only In This Slice

For `docker` and `macos-sandbox` in this plan:

- supported: one-shot `spec.type="command"`
- not supported: `function`
- not supported: `agent`
- not supported: `interactive`
- not supported: `persistent`

Reject unsupported combinations explicitly and early.

This is a deliberate capability gate, not a temporary hack.

### 5.6 `monitor_class` Remains Host-Oriented

Do not use `monitor_class` as the cross-runner abstraction.

In this slice:

- the `host` runner keeps current `monitor_class` semantics,
- external runners own their own limit/monitor behavior,
- external runners may reject unsupported monitor overrides explicitly.

This avoids binding the plugin contract to host-psutil assumptions.

### 5.7 The macOS Runtime Mechanism Must Be Explicit

`macos-sandbox` is the runner **slot**, not permission to guess a tool.

Before implementing the macOS runner package, confirm which concrete runtime
mechanism the project intends to support. If that choice is not explicit,
stop and get that decision. Do **not** cargo-cult `sandbox-exec` just because
it exists on some machines.

The extension seam is the main point of this plan; the macOS runtime adapter
must target an explicitly chosen tool.

## 6. Target End State

### 6.1 Execution Flow

```text
TaskSpec.spec.runner.name
            |
            v
      TaskRunner façade
            |
            v
   resolve runner plugin
            |
            v
  create runner instance for spec
            |
            +--> validate/load errors -> clear user-facing failure
            +--> execution-time preflight failure -> task fails cleanly
            |
            v
      execute or open session
            |
            v
   emit RunnerHandle to task runtime
            |
            v
  control/status paths use runner + handle
```

### 6.2 Validation Flow

```text
validate_taskspec(json)
    |
    +--> schema only (default)
    |
    +--> --load-runner
    |       plugin installed?
    |       runner options valid?
    |
    +--> --preflight
            runtime available on this machine?
            clear failure if not
```

### 6.3 Runtime Handle Flow

```text
runner instance starts runtime
        |
        v
  emits RunnerHandle
        |
        v
BaseTask records handle in weft.state.tid_mappings
        |
        v
CLI task stop/kill resolves latest mapping
        |
        v
loads runner plugin and asks plugin to stop/kill that handle
```

## 7. Invariants and Constraints

These must hold throughout the implementation:

- TID format and immutability do not change.
- `spec` and `io` remain immutable after resolved TaskSpec creation.
- state transitions remain forward-only.
- reserved queue policy behavior does not change.
- child processes still use `multiprocessing.get_context("spawn")`.
- `weft.state.*` queues remain runtime-only.
- append-only queue readers use generators/helpers when correctness matters.
- the canonical execution spine remains:
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log`
- existing `host` runner behavior for current task types stays intact.
- missing plugin/runtime failures must be explicit, not silent.
- runner `options` must stay JSON-serializable inside TaskSpec payloads and
  queue messages.

## 8. Tasks

Implement these tasks in order.

### Task 1. Lock the contract in specs and failing tests

Files to modify:

- `docs/specifications/01-Core_Components.md`
- `docs/specifications/02-TaskSpec.md`
- `docs/specifications/06-Resource_Management.md`
- `docs/specifications/10-CLI_Interface.md`
- `tests/taskspec/test_taskspec.py`
- `tests/cli/test_cli_validate.py`

Read first:

- `docs/specifications/01-Core_Components.md`
- `docs/specifications/02-TaskSpec.md`
- `docs/specifications/06-Resource_Management.md`
- `docs/specifications/10-CLI_Interface.md`
- `tests/taskspec/test_taskspec.py`
- `tests/cli/test_cli_validate.py`

Write these failing tests first:

1. A resolved TaskSpec without `spec.runner` defaults to `runner.name == "host"`
   and `runner.options == {}`.
2. A template TaskSpec without `spec.runner` also validates and defaults the
   same way once resolved.
3. `validate_taskspec()` still succeeds for a valid schema-only template
   without probing runtime availability.
4. CLI validation with `--load-runner` fails with a clear install hint when a
   runner plugin is missing.
5. CLI validation with `--preflight` fails with a clear runtime-unavailable
   message when the plugin is installed but the runtime probe fails.

Implementation notes:

- Update the specs first or in the same commit as the tests. This is a public
  contract change.
- Add `spec.runner` to the TaskSpec JSON schema examples.
- State clearly in the spec that `spec.type` and `spec.runner.name` are
  orthogonal.
- Document that validation is layered and that default validation is pure.
- Document that non-host runners in this slice are one-shot command-only.

Invariants to protect:

- existing TaskSpec templates remain portable,
- default schema validation remains machine-independent,
- and old TaskSpecs remain valid because `host` is the default runner.

Done when:

- the public docs name the new contract,
- and the new tests fail for the right reasons before implementation starts.

### Task 2. Add `RunnerSection` to `TaskSpec` and thread it through payload resolution

Files to modify:

- `weft/core/taskspec.py`
- `tests/taskspec/test_taskspec.py`
- `tests/specs/taskspec/test_agent_taskspec.py`
- `tests/fixtures/taskspecs.py`

Read first:

- `weft/core/taskspec.py`
- `tests/taskspec/test_taskspec.py`
- `tests/specs/taskspec/test_agent_taskspec.py`

Write these failing tests first:

1. `resolve_taskspec_payload()` injects `spec.runner = {"name": "host", "options": {}}`
   when omitted.
2. `RunnerSection` rejects empty runner names.
3. `RunnerSection.options` is deeply immutable after TaskSpec creation.
4. `RunnerSection.options` rejects non-JSON-serializable values so TaskSpecs
   remain queue-safe.
5. Existing fixtures and agent TaskSpecs still validate unchanged.

Implementation notes:

- Add a `RunnerSection` model under `SpecSection`.
- Keep it small:
  - `name: str = "host"`
  - `options: dict[str, Any] = Field(default_factory=dict)`
- Freeze `options` the same way other immutable spec containers are frozen.
- Update `resolve_taskspec_payload()` to set default runner values without
  mutating the input.
- Do not add a generic polymorphic runner-options schema in core.
- Do not move `runner` outside `spec`.

Invariants to protect:

- resolved TaskSpecs remain frozen on `spec` and `io`,
- templates remain partial,
- and `resolve_taskspec_payload()` stays the single resolved-task expansion path.

Verification gate for this task:

```bash
uv run pytest tests/taskspec/test_taskspec.py tests/specs/taskspec/test_agent_taskspec.py -q
```

Success looks like:

- TaskSpec defaults are stable,
- and no existing TaskSpec tests regress.

### Task 3. Add the public runner plugin contract and internal resolver

Files to modify:

- `weft/ext.py` (new)
- `weft/_runner_plugins.py` (new)
- `tests/core/test_runner_plugins.py` (new)
- `pyproject.toml`

Read first:

- `../simplebroker/simplebroker/_backend_plugins.py`
- `../simplebroker/simplebroker/ext.py`
- `weft/context.py`
- `pyproject.toml`

Write these failing tests first:

1. Built-in `host` resolves without entry points.
2. An external plugin registered under the `weft.runners` entry-point group
   resolves and must report the expected `name`.
3. A missing plugin surfaces a clear error:
   `Requested runner 'docker' is not available. Install weft[docker].`
4. A plugin whose exported object reports the wrong name fails loudly.

Implementation notes:

- Mirror SimpleBroker’s shape, but keep it smaller.
- Add a public protocol in `weft/ext.py`:
  - `RunnerPlugin`
  - `RunnerHandle`
  - `RunnerCapabilities`
  - `BaseTaskRunner` (or equivalently named execution protocol)
- Add one internal resolver in `weft/_runner_plugins.py`.
- Use entry-point group `weft.runners`.
- Support one built-in plugin name in core: `host`.
- Add root extras in `pyproject.toml`:
  - `docker = ["weft-docker>=0.1.0"]`
  - `macos-sandbox = ["weft-macos-sandbox>=0.1.0"]`
- Keep error normalization centralized and explicit.
- Do not add an internal-only registry just for tests unless the entry-point
  path is unworkable.

Invariants to protect:

- plugin loading is centralized,
- user-facing install hints are deterministic,
- and core can still run with no external runner packages installed.

Verification gate for this task:

```bash
uv run pytest tests/core/test_runner_plugins.py -q
uv run pytest tests/taskspec/test_taskspec.py -q
```

### Task 4. Convert the current local execution path into the built-in `host` runner

Files to modify:

- `weft/core/tasks/runner.py`
- `weft/core/tasks/sessions.py`
- `weft/core/resource_monitor.py`
- `tests/tasks/test_runner.py`
- `tests/tasks/test_task_interactive.py`

Files to add:

- `weft/core/runners/host.py`
- `weft/core/runners/__init__.py`

Read first:

- `weft/core/tasks/runner.py`
- `weft/core/tasks/sessions.py`
- `weft/core/resource_monitor.py`
- `tests/tasks/test_runner.py`
- `tests/tasks/test_task_interactive.py`

Write these failing tests first:

1. Existing `TaskRunner` unit tests still pass when `host` is selected via the
   new runner path.
2. `TaskRunner` defaults to `host` when no runner is passed explicitly.
3. Interactive command behavior still works through the same public
   `TaskRunner.start_session()` surface.
4. Persistent agent-session behavior still works through the same public
   `TaskRunner.start_agent_session()` surface.

Implementation notes:

- Move the current execution logic into a `host` runner implementation module.
- Keep `TaskRunner` as a façade that:
  - accepts new optional constructor args such as `runner_name` and
    `runner_options`,
  - resolves the plugin,
  - constructs the plugin-owned runner instance,
  - delegates `run_with_hooks()`, `start_session()`, and
    `start_agent_session()`.
- Do not duplicate the old host code into both façade and plugin.
- Keep `RunnerOutcome` at the old import path.
- Keep `CommandSession` and `AgentSession` at the old import path for now.
- The host plugin should continue to use `load_resource_monitor()` and the
  current psutil-based limit enforcement.

Invariants to protect:

- current subprocess behavior is preserved for `host`,
- timeout/limit/cancel behavior remains tree-aware,
- and interactive/agent session behavior does not regress.

Verification gate for this task:

```bash
uv run pytest tests/tasks/test_runner.py tests/tasks/test_task_interactive.py -q
```

### Task 5. Thread runner selection through task execution paths

Files to modify:

- `weft/core/tasks/consumer.py`
- `weft/core/tasks/interactive.py`
- `tests/tasks/test_runner.py`
- `tests/tasks/test_task_interactive.py`

Read first:

- `weft/core/tasks/consumer.py`
- `weft/core/tasks/interactive.py`
- `tests/tasks/test_runner.py`
- `tests/tasks/test_task_interactive.py`

Write these failing tests first:

1. `Consumer` passes `spec.runner.name` and `spec.runner.options` into the
   `TaskRunner` façade.
2. `InteractiveTask` rejects non-host runners explicitly before trying to open
   a session.
3. A non-host runner is rejected early for unsupported combinations:
   - `spec.type="function"`
   - `spec.type="agent"`
   - `spec.interactive=true`
   - `spec.persistent=true`

Implementation notes:

- Thread `spec.runner` through `Consumer` and `InteractiveTask`.
- Existing inline `weft run CMD ...` construction may continue to default to
  `host` in this slice; the core goal is TaskSpec-driven runner selection.
- Do not special-case Docker directly in `consumer.py` or `interactive.py`.
- All behavior must still flow through `TaskRunner`.
- For unsupported combinations, fail early and explicitly.

Invariants to protect:

- existing CLI behavior stays unchanged,
- resolved TaskSpecs still use one shared expansion path,
- and unsupported mode combinations fail before launch.

Verification gate for this task:

```bash
uv run pytest tests/tasks/test_runner.py tests/tasks/test_task_interactive.py -q
```

### Task 6. Introduce `RunnerHandle` into runtime bookkeeping and stop/kill control

Files to modify:

- `weft/core/tasks/base.py`
- `weft/core/tasks/runner.py`
- `weft/core/tasks/sessions.py`
- `weft/commands/tasks.py`
- `tests/commands/test_task_commands.py`
- `tests/core/test_manager.py`

Read first:

- `weft/core/tasks/base.py`
- `weft/commands/tasks.py`
- `tests/commands/test_task_commands.py`
- `tests/core/test_manager.py`

Write these failing tests first:

1. TID mapping entries include `runner` and `runtime_handle`.
2. `stop_tasks()` uses the runner plugin to stop a running task when a handle
   is present.
3. `kill_tasks()` uses the runner plugin to kill a running task when a handle
   is present.
4. Existing host task stop/kill tests still pass.
5. For older host-like mappings with only `pid`, command helpers still work for
   transitional compatibility.

Implementation notes:

- Add a `register_runtime_handle()` path in `BaseTask`.
- Keep `register_managed_pid()` as a host convenience that updates the current
  handle’s `host_pids`, rather than as the canonical control surface.
- Extend mapping payloads to include:
  - `runner`
  - `runtime_handle`
  - derived `pid` and `managed_pids` for host observability
- Update `TaskRunner` callbacks to emit `RunnerHandle`, not raw PID.
- Keep `RunnerOutcome.worker_pid` as a host-oriented compatibility field for
  now if existing call sites rely on it; add `runtime_handle` rather than
  replacing fields abruptly.
- Session objects should expose `handle` and keep `pid` as a convenience for
  host-backed sessions.
- Update `stop_tasks()` and `kill_tasks()` to:
  - read the latest mapping entry,
  - resolve the runner plugin,
  - ask the plugin to stop/kill the handle.
- Transitional fallback to raw PID is allowed only in the command layer for
  old live mapping entries. Do not spread dual-shape support everywhere.

Invariants to protect:

- control still works for host tasks,
- runtime bookkeeping remains queue-based,
- and append-only mapping reads remain generator-based where correctness matters.

Verification gate for this task:

```bash
uv run pytest tests/commands/test_task_commands.py tests/core/test_manager.py -q
```

### Task 7. Add load validation and runtime preflight helpers

Files to modify:

- `weft/core/taskspec.py`
- `weft/commands/validate_taskspec.py`
- `weft/cli.py`
- `tests/cli/test_cli_validate.py`
- `tests/cli/test_commands.py`

Files to add:

- `weft/core/runner_validation.py` (new)

Read first:

- `weft/core/taskspec.py`
- `weft/commands/validate_taskspec.py`
- `weft/cli.py`
- `tests/cli/test_cli_validate.py`

Write these failing tests first:

1. `validate_taskspec()` remains schema-only and unchanged for existing callers.
2. `weft spec validate --type task FILE --load-runner` fails with an install hint for
   a missing plugin.
3. `weft spec validate --type task FILE --preflight` fails with a runtime availability
   error when the plugin is installed but unavailable.
4. `weft spec validate --type task FILE --preflight` still succeeds for `host`.
5. Preflight errors are clear and user-facing; they do not dump tracebacks for
   expected runtime-unavailable cases.

Implementation notes:

- Keep `validate_taskspec()` stable for backward compatibility.
- Add a separate helper layer for deeper checks; do not overload the existing
  function with side effects.
- Add CLI flags:
  - `--load-runner`
  - `--preflight`
- `--preflight` should imply `--load-runner`.
- Normalize missing-plugin errors centrally in the same style as backend error
  normalization.
- Execution-time preflight remains mandatory in the runner execution path.

Invariants to protect:

- stored templates remain portable,
- CLI validation defaults remain fast and pure,
- and runtime availability failure messages are explicit.

Verification gate for this task:

```bash
uv run pytest tests/cli/test_cli_validate.py tests/cli/test_commands.py -q
```

### Task 8. Add the `docker` extension package

Files to add:

- `extensions/weft_docker/pyproject.toml`
- `extensions/weft_docker/README.md`
- `extensions/weft_docker/weft_docker/__init__.py`
- `extensions/weft_docker/weft_docker/plugin.py`
- `extensions/weft_docker/weft_docker/runner.py`
- `extensions/weft_docker/tests/test_plugin.py`
- `extensions/weft_docker/tests/test_runner.py`

Files to modify:

- `README.md`
- `pyproject.toml`
- `tests/core/test_runner_plugins.py`

Read first:

- `../simplebroker/extensions/simplebroker_pg/pyproject.toml`
- `weft/ext.py`
- `weft/_runner_plugins.py`
- `weft/core/tasks/runner.py`

Write these failing tests first:

1. Docker plugin package exposes the `weft.runners` entry point.
2. Plugin load validation rejects unsupported task shapes:
   - non-command
   - interactive
   - persistent
3. Preflight fails cleanly when Docker is unavailable.
4. One portable unit test proves command translation from TaskSpec to Docker
   invocation arguments without running Docker.
5. One integration test, skipped unless Docker is available, proves that a
   simple command TaskSpec actually executes through the Docker runner and
   returns stdout.

Implementation notes:

- Keep the Docker plugin narrow:
  - command-only
  - one-shot only
  - JSON-serializable runner options only
- Suggested runner options:
  - `image` (required)
  - `workdir` / `working_dir` inside the container (optional)
  - `env` passthrough additions (optional)
  - `mount_cwd` or explicit mount config (optional, choose one and document it)
- Docker preflight should check Docker availability, not force image presence.
  Missing images may be handled at execution time if the chosen Docker command
  naturally pulls or errors.
- Do not implement a general volume/network/security policy framework.
- The plugin must return a `RunnerHandle` that uses the container ID as the
  runtime ID.
- Stop/kill must operate through Docker, not host PID guesses.
- If Docker output streaming for one-shot commands can reuse the existing
  capture path cleanly, do so. Do not add an unrelated output subsystem.

Invariants to protect:

- no Docker dependency in the base install,
- Docker absence does not break host runner flows,
- and default test gates do not depend on Docker being installed.

Verification gate for this task:

```bash
uv pip install -e ./extensions/weft_docker
uv run pytest tests/core/test_runner_plugins.py -q
uv run pytest extensions/weft_docker/tests -q
```

If Docker is available locally, also run the Docker-available integration test
subset explicitly.

### Task 9. Add the `macos-sandbox` extension package

Files to add:

- `extensions/weft_macos_sandbox/pyproject.toml`
- `extensions/weft_macos_sandbox/README.md`
- `extensions/weft_macos_sandbox/weft_macos_sandbox/__init__.py`
- `extensions/weft_macos_sandbox/weft_macos_sandbox/plugin.py`
- `extensions/weft_macos_sandbox/weft_macos_sandbox/runner.py`
- `extensions/weft_macos_sandbox/tests/test_plugin.py`
- `extensions/weft_macos_sandbox/tests/test_runner.py`

Files to modify:

- `README.md`
- `pyproject.toml`

Read first:

- `weft/ext.py`
- `weft/_runner_plugins.py`
- `extensions/weft_docker/weft_docker/plugin.py`
- `extensions/weft_docker/weft_docker/runner.py`

Write these failing tests first:

1. The package exposes the `weft.runners` entry point.
2. Plugin load validation rejects unsupported task shapes exactly like Docker.
3. Preflight fails cleanly when the selected macOS sandbox runtime mechanism is
   unavailable.
4. One portable unit test proves the sandbox command construction without
   invoking the real runtime.
5. One integration test, skipped unless the selected runtime is available on
   macOS, proves a simple command TaskSpec executes and returns stdout.

Implementation notes:

- Do not begin this task until the concrete macOS runtime mechanism is
  confirmed.
- Keep capabilities identical to Docker in this slice:
  - command-only
  - one-shot only
- Keep the plugin narrow. Do not build a macOS policy DSL.
- Use the plugin’s own preflight and validation hooks to reject unsupported
  config or missing runtime support.
- The plugin must return a `RunnerHandle` meaningful to that runtime, even if
  it also includes host PID metadata.

Invariants to protect:

- non-macOS systems can install and use core Weft without this plugin,
- and host behavior remains unchanged.

Verification gate for this task:

```bash
uv pip install -e ./extensions/weft_macos_sandbox
uv run pytest extensions/weft_macos_sandbox/tests -q
```

Only run the runtime-backed integration test on macOS with the target runtime
available.

### Task 10. Finish docs, examples, and compatibility notes

Files to modify:

- `README.md`
- `docs/specifications/01-Core_Components.md`
- `docs/specifications/02-TaskSpec.md`
- `docs/specifications/06-Resource_Management.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/lessons.md` (only if implementation exposes a reusable lesson)

Read first:

- all docs touched in Tasks 1 and 8/9

Implementation notes:

- Add installation examples for:
  - `uv add 'weft[docker]'`
  - `uv add 'weft[macos-sandbox]'`
- Add TaskSpec examples showing `spec.runner`.
- Add CLI examples for:
  - `weft spec validate --type task --load-runner`
  - `weft spec validate --type task --preflight`
- Document that `host` remains the default runner.
- Document non-host capability limits in this slice.
- Document user-facing failure modes for:
  - missing plugin
  - runtime unavailable
  - unsupported task shape

Invariants to protect:

- docs match the implemented contract,
- and docs do not promise capabilities not present in this slice.

## 9. Test Plan

### 9.1 Test Diagram

```text
NEW / CHANGED SURFACES

1. TaskSpec schema
   - new field: spec.runner
   - default behavior: host
   - new nested immutability: runner.options

2. Runner resolution
   - built-in host resolution
   - entry-point plugin resolution
   - missing-plugin failure
   - mismatched-plugin-name failure

3. Execution path
   - TaskRunner façade delegates to host or external plugin
   - one-shot command execution via external runner
   - execution-time preflight failure

4. Control path
   - runtime handle emitted and persisted
   - CLI stop/kill routes through plugin by handle
   - transitional PID fallback for old live host entries

5. CLI validation
   - default schema validation
   - --load-runner
   - --preflight

6. CLI run
   - TaskSpec-driven runner execution continues to work
   - unsupported combination rejection before launch

7. Extension packaging
   - docker entry point
   - macos-sandbox entry point
   - runtime availability checks
```

### 9.2 Test Files To Update Or Add

Core:

- `tests/taskspec/test_taskspec.py`
- `tests/specs/taskspec/test_agent_taskspec.py`
- `tests/tasks/test_runner.py`
- `tests/tasks/test_task_interactive.py`
- `tests/core/test_manager.py`
- `tests/commands/test_task_commands.py`
- `tests/cli/test_cli_run.py` (regression coverage for adjacent `weft run` flows)
- `tests/cli/test_cli_validate.py`
- `tests/cli/test_commands.py`
- `tests/core/test_runner_plugins.py` (new)

Extensions:

- `extensions/weft_docker/tests/test_plugin.py`
- `extensions/weft_docker/tests/test_runner.py`
- `extensions/weft_macos_sandbox/tests/test_plugin.py`
- `extensions/weft_macos_sandbox/tests/test_runner.py`

### 9.3 Harness Selection

Use:

- plain unit tests for:
  - `RunnerSection`
  - runner plugin resolver
  - error normalization
  - command construction helpers
- `broker_env` or direct real `Queue` usage for:
  - runtime-handle mapping payloads
  - control queue behavior
- direct `TaskRunner` tests for:
  - host runner behavior
  - external runner execution surface
- `WeftTestHarness` or real CLI subprocess tests for:
  - `weft run`
  - `weft spec validate --type task`
  - end-to-end task control flows

### 9.4 Test Design Rules For This Plan

- Use real execution for lifecycle behavior.
- Do not mock queue semantics.
- Do not mock `TaskRunner` or `Consumer` in lifecycle tests.
- Do not use sleeps as assertions; use bounded polling or existing wait helpers.
- For plugin discovery, monkeypatching `importlib.metadata.entry_points()` is
  acceptable in focused resolver tests.
- For runtime availability tests, monkeypatch the runtime probe command/helper,
  not the entire plugin.
- For Docker and macOS runtime-backed integration tests, mark and skip when the
  runtime is unavailable. Do not make the default root suite depend on those
  runtimes.

### 9.5 Failure Modes To Cover

Each of these needs either a direct test or explicit error-handling assertion:

1. Runner plugin missing.
2. Runner plugin resolves to an object with the wrong name.
3. Runner plugin installed but runtime unavailable.
4. Unsupported task shape for external runner.
5. Execution-time preflight fails after a task was launched.
6. Runtime handle cannot be serialized or is missing required fields.
7. CLI stop/kill sees a new mapping entry and must route through the plugin.
8. CLI stop/kill sees an old mapping entry and must fall back to host PID.
9. Host runner behavior regresses while refactoring to the plugin seam.
10. Docker/macOS plugin claims success but returns no stdout or no terminal
    result.

If any new code path can fail silently, that is a critical gap.

## 10. Verification

Run focused commands while iterating, then the final gates **sequentially**.

Focused commands by stage:

```bash
uv run pytest tests/taskspec/test_taskspec.py tests/specs/taskspec/test_agent_taskspec.py -q
uv run pytest tests/core/test_runner_plugins.py -q
uv run pytest tests/tasks/test_runner.py tests/tasks/test_task_interactive.py -q
uv run pytest tests/core/test_manager.py tests/commands/test_task_commands.py -q
# Adjacent regression coverage: default/spec-driven run flows should not regress.
uv run pytest tests/cli/test_cli_run.py tests/cli/test_cli_validate.py tests/cli/test_commands.py -q
```

Extension-package focused commands:

```bash
uv pip install -e ./extensions/weft_docker
uv run pytest extensions/weft_docker/tests -q

uv pip install -e ./extensions/weft_macos_sandbox
uv run pytest extensions/weft_macos_sandbox/tests -q
```

Final repo gates after the implementation is complete:

```bash
uv run pytest -q
uv run pytest -m "" -q
uv run ruff check weft tests
uv run mypy weft
```

If root lint/type-check should cover extension packages too, add those paths in
the same slice and run:

```bash
uv run ruff check weft tests extensions/weft_docker extensions/weft_macos_sandbox
```

Success looks like:

- existing host-runner tests still pass,
- new runner schema/validation tests pass,
- stop/kill works through runtime handles,
- validation surfaces clear install/runtime errors,
- Docker package works when installed and available,
- macOS package works when installed and its runtime is available,
- and no current host behavior regresses.

## 11. NOT In Scope

- non-host support for `function` tasks
  - requires code distribution/import semantics that this plan intentionally
    avoids.
- non-host support for `agent` tasks
  - depends on session/runtime semantics beyond this slice.
- non-host interactive sessions
  - would require generalizing session contracts further than needed here.
- non-host persistent tasks
  - same reason as above.
- inline `weft run --runner ...` convenience flags
  - useful, but not required to prove the TaskSpec-driven extension point.
- Docker/macOS runtime CI provisioning
  - this plan adds the seam and local/runtime-gated tests, not CI infrastructure.
- a generic container/sandbox policy DSL
  - unnecessary abstraction for the first slice.
- removing host PID fields entirely from observability payloads
  - keep them as derived convenience fields while moving the canonical control
    surface to runtime handles.

## 12. Review Checklist For The Implementer

Before claiming the implementation is done, verify all of the following:

- `spec.type` still means target kind only.
- `spec.runner` exists and defaults to `host`.
- `TaskRunner` still exists at the old import path.
- current host execution and monitoring behavior still works.
- stop/kill use runner + runtime handle, not only PID.
- `validate_taskspec()` remains schema-only.
- CLI validation has opt-in load/preflight checks.
- missing plugin errors mention the correct extra to install.
- non-host runners reject unsupported shapes explicitly.
- docs/specs match the actual contract.

## 13. Final Warning

This plan is intentionally conservative in one place and intentionally
ambitious in another:

- conservative: external runners are command-only in this slice,
- ambitious: the extension seam itself is real and cross-cutting from day one.

Do not weaken the seam to make the first plugin easier. That would save a day
now and cost a redesign later.

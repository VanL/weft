# Agent Runtime Package Refactor Plan

Status: completed
Source specs: see Source Documents below
Superseded by: none

This plan performs the full agent-runtime layout refactor with no backward
compatibility shims. The current state splits one feature area across two
namespaces:

- backend-neutral agent-runtime modules in `weft/core/agent_*.py`
- concrete runtime code in `weft/core/agents/`

That split is real design debt. It makes the codebase harder to navigate,
encourages duplicate seams, and forces zero-context engineers to guess which
`agent` files are part of the same boundary and which are not.

This refactor is intentionally structural, not behavioral. The target outcome
is one agent-runtime package under `weft/core/agents/` with a clear internal
layout:

- package-root modules for backend-neutral agent-runtime concerns
- `backends/` for concrete runtime adapters
- `provider_cli/` for `provider_cli`-specific helpers

There are no shims in this plan. Old module paths must disappear from the
source tree. If any supported external contract depends on those exact module
paths, stop and escalate before implementation.

This is risky boundary work because it touches code, docs, tests, and one
first-party extension, all around the same execution surface:

`TaskSpec -> TaskRunner -> runner -> agent runtime -> provider_cli helpers -> docs/tests`

The likely failure modes are predictable:

- silently turning this into a behavior change instead of a path/layout change
- introducing alias modules, `compat.py`, import-forwarders, or re-export
  shims to make the move easier
- moving task-session glue into the wrong package because it has `agent` in
  the filename
- rewriting historical plans just to make grep output pretty
- or "cleaning up" adjacent logic in `provider_cli` while touching imports

Do not do those things.

## Scope Lock

Implement exactly this slice:

1. move all backend-neutral agent-runtime modules from `weft/core/agent_*.py`
   into `weft/core/agents/`
2. rename backend files so they read like package members, not leftover
   one-off filenames
3. move `provider_cli`-only helpers into a dedicated
   `weft/core/agents/provider_cli/` subpackage
4. update all in-repo imports, docstrings, specs, tests, and first-party
   extension imports to the new paths
5. keep behavior unchanged except for import-path and documentation updates
6. prove the refactor by keeping existing real tests green without replacing
   them with mocks

Do not implement any of the following in this slice:

- compatibility wrappers or alias modules at the old paths
- new public API promises around these internal module paths
- behavior changes in runtime registration, validation, execution, session
  semantics, queue behavior, or CLI output
- renaming task-session files in `weft/core/tasks/` just because they contain
  `agent`
- renaming builtins in `weft/builtins/`
- dist artifact regeneration under `extensions/weft_docker/dist/`
- broad historical-doc cleanup outside current specs and current active plans
- test rewrites that replace real broker/process/provider fixture coverage with
  mocks

If the implementation starts pulling toward any excluded item, stop and split a
follow-on plan instead of hiding that drift inside this refactor.

## Goal

Make the agent-runtime boundary legible. After this refactor, a zero-context
engineer should be able to look under `weft/core/agents/` and find the whole
agent-runtime surface in one place, with provider-specific code under a clear
subpackage, while task glue and builtins remain where they belong.

## Design Position

This refactor should lock in five structure rules:

- `weft/core/agents/` is the single home for agent-runtime code
- backend-neutral runtime helpers live at the package root
- concrete runtime adapters live in `weft/core/agents/backends/`
- `provider_cli`-specific helpers live in
  `weft/core/agents/provider_cli/`
- task-private glue remains in `weft/core/tasks/`

Keep the move conservative:

- preserve class names
- preserve function names
- preserve `__all__` surfaces when they already exist
- preserve registration behavior
- preserve module docstring spec references, but update file paths and wording
  to the new locations

The right mental model is "move and relink", not "move and redesign".

## Source Documents

Primary specs:

- [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
  [AR-5], [AR-6], [AR-7], [AR-9]
- [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md)
  [CC-3], [CC-3.1], [CC-3.2], [CC-3.3]
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [IMMUT.1], [IMMUT.2], [IMMUT.3], [STATE.1], [STATE.2], [QUEUE.1],
  [QUEUE.2], [QUEUE.3], [IMPL.5], [IMPL.6]
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
  [CLI-1.4.1]
- [`docs/specifications/10B-Builtin_TaskSpecs.md`](../specifications/10B-Builtin_TaskSpecs.md)

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

- [`docs/plans/2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md`](./2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md)
- [`docs/plans/2026-04-14-docker-agent-images-and-one-shot-provider-cli-plan.md`](./2026-04-14-docker-agent-images-and-one-shot-provider-cli-plan.md)
- [`docs/plans/2026-04-14-builtin-taskspecs-and-spec-resolution-plan.md`](./2026-04-14-builtin-taskspecs-and-spec-resolution-plan.md)

First-party extension context:

- [`extensions/weft_docker/weft_docker/agent_runner.py`](../../extensions/weft_docker/weft_docker/agent_runner.py)
- [`extensions/weft_docker/tests/test_agent_runner.py`](../../extensions/weft_docker/tests/test_agent_runner.py)

Source spec note:

- there is no behavioral spec change in this plan
- the spec work here is implementation-mapping and backlink maintenance

## Target Layout

The target code layout is this:

```text
weft/core/agents/
├── __init__.py
├── runtime.py
├── resolution.py
├── validation.py
├── tools.py
├── templates.py
├── backends/
│   ├── __init__.py
│   ├── llm.py
│   └── provider_cli.py
└── provider_cli/
    ├── __init__.py
    ├── execution.py
    ├── registry.py
    ├── settings.py
    └── probes.py
```

The explicit move map is:

```text
weft/core/agent_runtime.py
  -> weft/core/agents/runtime.py

weft/core/agent_resolution.py
  -> weft/core/agents/resolution.py

weft/core/agent_validation.py
  -> weft/core/agents/validation.py

weft/core/agent_tools.py
  -> weft/core/agents/tools.py

weft/core/agent_templates.py
  -> weft/core/agents/templates.py

weft/core/agents/backends/llm_backend.py
  -> weft/core/agents/backends/llm.py

weft/core/agents/backends/provider_cli_backend.py
  -> weft/core/agents/backends/provider_cli.py

weft/core/agents/provider_cli_execution.py
  -> weft/core/agents/provider_cli/execution.py

weft/core/agents/provider_registry.py
  -> weft/core/agents/provider_cli/registry.py

weft/core/agents/settings.py
  -> weft/core/agents/provider_cli/settings.py

weft/core/agents/probes.py
  -> weft/core/agents/provider_cli/probes.py
```

Create one new file:

- `weft/core/agents/provider_cli/__init__.py`

Do not create any of the following:

- `weft/core/agent_runtime.py` as a wrapper
- `weft/core/agent_resolution.py` as a wrapper
- `weft/core/agent_validation.py` as a wrapper
- `weft/core/agent_tools.py` as a wrapper
- `weft/core/agent_templates.py` as a wrapper
- `weft/core/agents/provider_registry.py` as a wrapper
- `weft/core/agents/provider_cli_execution.py` as a wrapper
- `weft/core/agents/settings.py` as a wrapper
- `weft/core/agents/probes.py` as a wrapper
- `legacy.py`, `compat.py`, `aliases.py`, or any `sys.modules` hack

## Context and Key Files

Current structure:

- `weft/core/agent_runtime.py` owns normalized work items, execution results,
  runtime registry, one-shot dispatch, and persistent-session startup
- `weft/core/agent_resolution.py`, `weft/core/agent_validation.py`,
  `weft/core/agent_tools.py`, and `weft/core/agent_templates.py` are
  backend-neutral agent-runtime helpers, but they live outside the package that
  holds the rest of the agent-runtime code
- `weft/core/agents/backends/` already contains concrete adapters
- `weft/core/agents/provider_cli_execution.py`,
  `weft/core/agents/provider_registry.py`, `weft/core/agents/settings.py`, and
  `weft/core/agents/probes.py` are all `provider_cli`-specific, but they are
  flat siblings instead of a provider-specific subpackage
- `weft/core/tasks/agent_session_protocol.py` and
  `weft/core/tasks/sessions.py` are task-runtime glue, not agent-runtime
  package members
- `weft/builtins/agent_probe.py` and `weft/builtins/agent_images.py` are
  builtins, not core package members
- `extensions/weft_docker/weft_docker/agent_runner.py` is a first-party
  extension that imports the current internal paths directly

Files to read before touching anything:

1. [`weft/core/agents/__init__.py`](../../weft/core/agents/__init__.py)
2. [`weft/core/agent_runtime.py`](../../weft/core/agent_runtime.py)
3. [`weft/core/agent_resolution.py`](../../weft/core/agent_resolution.py)
4. [`weft/core/agent_validation.py`](../../weft/core/agent_validation.py)
5. [`weft/core/agent_tools.py`](../../weft/core/agent_tools.py)
6. [`weft/core/agent_templates.py`](../../weft/core/agent_templates.py)
7. [`weft/core/agents/backends/__init__.py`](../../weft/core/agents/backends/__init__.py)
8. [`weft/core/agents/backends/llm_backend.py`](../../weft/core/agents/backends/llm_backend.py)
9. [`weft/core/agents/backends/provider_cli_backend.py`](../../weft/core/agents/backends/provider_cli_backend.py)
10. [`weft/core/agents/provider_cli_execution.py`](../../weft/core/agents/provider_cli_execution.py)
11. [`weft/core/agents/provider_registry.py`](../../weft/core/agents/provider_registry.py)
12. [`weft/core/agents/settings.py`](../../weft/core/agents/settings.py)
13. [`weft/core/agents/probes.py`](../../weft/core/agents/probes.py)
14. [`weft/core/tasks/runner.py`](../../weft/core/tasks/runner.py)
15. [`weft/core/runners/host.py`](../../weft/core/runners/host.py)
16. [`weft/builtins/agent_probe.py`](../../weft/builtins/agent_probe.py)
17. [`weft/builtins/agent_images.py`](../../weft/builtins/agent_images.py)
18. [`extensions/weft_docker/weft_docker/agent_runner.py`](../../extensions/weft_docker/weft_docker/agent_runner.py)

Style and guidance to follow while editing:

- keep `from __future__ import annotations` in every moved file
- keep module docstrings and spec references current
- use relative imports inside `weft/core/agents/` where that reduces noise
- keep absolute imports across package boundaries such as
  `weft.core.tasks -> weft.core.agents`
- use `git mv` for file moves so history remains readable
- use targeted edits only; do not reformat unrelated code
- use `Path`, modern type syntax, and the repo import grouping rules from
  `AGENTS.md`

Shared paths and helpers to reuse:

- runtime registration remains in `weft/core/agents/backends/__init__.py`
- `register_builtin_agent_runtimes()` remains the registration entry point
- `AgentRuntimeAdapter`, `AgentRuntimeSession`, and `AgentExecutionResult`
  remain in the runtime module
- task-private session protocol remains in
  `weft/core/tasks/agent_session_protocol.py`
- first-party Docker runner keeps using the shared provider-cli execution
  helpers rather than copying logic

Files that must not move:

- `weft/core/tasks/agent_session_protocol.py`
- `weft/core/tasks/sessions.py`
- `weft/core/tasks/runner.py`
- `weft/core/tasks/consumer.py`
- `weft/builtins/agent_probe.py`
- `weft/builtins/agent_images.py`
- `extensions/weft_docker/dist/*`

## Comprehension Questions

If the implementer cannot answer these before editing, they are not ready:

1. Which current files are backend-neutral agent-runtime helpers, and which are
   `provider_cli`-specific helpers?
2. Where does built-in runtime registration happen today, and which code path
   still depends on importing `weft.core.agents` for that side effect?
3. Which `agent`-named files belong to task/session glue and therefore should
   stay outside the new package layout?
4. Which first-party extension imports the old internal paths directly and must
   land in the same change because there are no shims?

## Invariants and Constraints

The following must stay true:

- no queue name changes
- no TID format or immutability changes
- no state-machine changes
- no public CLI shape changes
- no `TaskSpec` schema changes
- no runtime registration behavior changes
- no `provider_cli` execution, validation, or prompt-composition behavior
  changes
- no change to task-session private protocol messages
- no change to extension behavior beyond import path updates
- no compatibility wrappers at old paths
- no second execution path
- `spec` and `io` remain immutable after resolved `TaskSpec` creation
- spawned worker behavior and broker-connection rules remain unchanged

Review gates for this slice:

- no new dependency
- no drive-by refactor
- no behavior edits hidden inside import work
- no test weakening
- no "cleanup" of `provider_cli` validation or backend logic while touching the
  files
- no changes under `extensions/weft_docker/dist/`

One-way-door assumption:

- this plan assumes the old `weft.core.agent_*` module paths are internal and
  unsupported outside this repo plus the first-party `weft_docker` extension
- if you learn that a supported external consumer imports those paths, stop and
  escalate before proceeding, because the no-shims requirement turns that into
  a deliberate breaking change

## Refactor Discipline

This plan is intentionally strict:

- DRY: reuse the current modules and symbols; move them rather than
  re-implementing them
- YAGNI: do not introduce a broader plugin or provider structure just because
  the files are already open
- red-green TDD: use existing tests as the proof harness for each slice; the
  red state is broken imports/path references, and the green state is the
  existing test subset passing with no semantic diff beyond path/doc updates

Allowed code changes:

- `git mv`
- import-path edits
- relative-import conversion inside `weft/core/agents/`
- module docstring path/reference updates
- `__all__` and package-init updates required by the moves
- test import updates
- doc/spec path updates

Not allowed unless you stop and re-plan:

- changing conditional logic
- changing validation rules
- changing error strings for non-path reasons
- changing test behavior or assertions beyond path/module names
- adding or removing exported runtime features
- redesigning registration to avoid an import cycle

If a move exposes an import cycle, the permitted fixes are:

- switching an internal import from absolute to relative
- reordering imports
- using `TYPE_CHECKING` for a type-only dependency

The forbidden fixes are:

- reintroducing a legacy alias module
- moving unrelated code to "break the cycle"
- changing runtime registration semantics

## Historical Plan Policy

Be explicit here so the implementer does not churn the repo blindly.

Update these documentation surfaces:

- current specs
- this new plan's backlink in the governing spec
- current active `2026-04-14` plan files that still act as live guidance and
  directly name the moved files

Do not mass-edit older historical plans from `2026-04-13` and earlier just to
refresh file paths. Those documents describe work at a point in time. They may
keep the old paths as historical context.

Counterargument:

- updating every old plan would reduce grep noise

Why that is still the wrong default here:

- it bloats the refactor
- it obscures historical execution records
- it creates more risk of accidental content changes without improving current
  behavior

If review determines an older plan is still an active implementation document,
stop and split a follow-on documentation-normalization task rather than folding
that work into this refactor by reflex.

## Tasks

1. Prove the no-shims assumption and lock the move map before touching code.
   - Outcome: you know exactly which files move, which stay put, and whether
     any first-party consumer outside `weft/` depends on the old paths.
   - Files to read:
     - `weft/core/agent_runtime.py`
     - `weft/core/agents/__init__.py`
     - `weft/core/runners/host.py`
     - `extensions/weft_docker/weft_docker/agent_runner.py`
   - Commands to run:
     - `git status --short`
     - `rg -n 'from weft\\.core\\.agent_|import weft\\.core\\.agent_|weft\\.core\\.agents\\.' weft tests extensions/weft_docker`
     - `find weft/core -maxdepth 2 -type f | sort`
   - Required action:
     - confirm the first-party Docker extension is the only in-repo external
       consumer of the old internal paths
     - confirm that `weft/core/tasks/agent_session_protocol.py` and
       `weft/core/tasks/sessions.py` are task glue and must stay where they are
     - lock the exact move map from the `Target Layout` section above
   - Stop and re-evaluate if:
     - you find another supported package outside this repo that imports the
       old paths
     - you feel pressure to move task files or builtins just because of the
       filename
   - Done when:
     - you can name every file that moves and every file that must stay put

2. Move the backend-neutral agent-runtime modules into `weft/core/agents/`.
   - Outcome: the package root owns all backend-neutral runtime helpers.
   - Files to move:
     - `weft/core/agent_runtime.py`
       -> `weft/core/agents/runtime.py`
     - `weft/core/agent_resolution.py`
       -> `weft/core/agents/resolution.py`
     - `weft/core/agent_validation.py`
       -> `weft/core/agents/validation.py`
     - `weft/core/agent_tools.py`
       -> `weft/core/agents/tools.py`
     - `weft/core/agent_templates.py`
       -> `weft/core/agents/templates.py`
   - Files to touch for import updates:
     - `weft/ext.py`
     - `weft/commands/validate_taskspec.py`
     - `weft/core/tasks/consumer.py`
     - `weft/core/tasks/agent_session_protocol.py`
     - `weft/core/tasks/runner.py`
     - `weft/core/runners/host.py`
     - `weft/core/agents/backends/__init__.py`
     - `weft/core/agents/backends/llm_backend.py`
     - `weft/core/agents/backends/provider_cli_backend.py`
     - `weft/core/agents/provider_cli_execution.py`
     - `weft/core/agents/provider_registry.py`
     - `tests/fixtures/provider_cli_fixture.py`
     - `tests/core/test_agent_runtime.py`
     - `tests/core/test_agent_resolution.py`
     - `tests/core/test_agent_validation.py`
     - `tests/core/test_agent_tools.py`
     - `tests/core/test_llm_backend.py`
     - `tests/core/test_provider_cli_backend.py`
     - `tests/core/test_provider_cli_session_backend.py`
     - `tests/core/test_provider_cli_execution.py`
     - `tests/core/test_tool_profiles.py`
     - `extensions/weft_docker/weft_docker/agent_runner.py`
   - Read first:
     - moved files themselves
     - `weft/core/tasks/runner.py`
     - `weft/core/runners/host.py`
   - Required action:
     - use `git mv`
     - update package-internal imports to relative imports where appropriate
     - keep symbol names unchanged
     - keep runtime/session behavior untouched
   - Testing:
     - `. ./.envrc && ./.venv/bin/python -m pytest tests/core/test_agent_runtime.py tests/core/test_agent_tools.py tests/core/test_agent_resolution.py tests/core/test_agent_validation.py tests/core/test_llm_backend.py tests/core/test_provider_cli_backend.py tests/core/test_provider_cli_execution.py tests/core/test_provider_cli_session_backend.py tests/core/test_tool_profiles.py -q`
   - Stop and re-evaluate if:
     - you need a wrapper module at the old path
     - you need to change runtime logic rather than imports
     - a failure points to behavior drift rather than a missing path update
   - Done when:
     - the targeted tests are green
     - `rg -n 'from weft\\.core\\.agent_|import weft\\.core\\.agent_' weft tests extensions/weft_docker` no longer shows these five backend-neutral modules

3. Rename the backend adapter files so they read like package members.
   - Outcome: concrete runtime adapters live under concise backend filenames.
   - Files to move:
     - `weft/core/agents/backends/llm_backend.py`
       -> `weft/core/agents/backends/llm.py`
     - `weft/core/agents/backends/provider_cli_backend.py`
       -> `weft/core/agents/backends/provider_cli.py`
   - Files to touch:
     - `weft/core/agents/backends/__init__.py`
     - `weft/core/agents/__init__.py`
     - any direct code importer found by
       `rg -n 'llm_backend|provider_cli_backend' weft tests extensions/weft_docker`
   - Read first:
     - `weft/core/agents/backends/__init__.py`
     - `weft/core/agents/__init__.py`
   - Required action:
     - keep `LLMBackend`, `ProviderCLIBackend`, and
       `register_builtin_agent_runtimes()` names unchanged
     - keep import-side-effect registration behavior unchanged
     - do not turn `weft/core/agents/__init__.py` into a broad re-export barrel
   - Testing:
     - `. ./.envrc && ./.venv/bin/python -m pytest tests/core/test_llm_backend.py tests/core/test_provider_cli_backend.py tests/core/test_provider_cli_session_backend.py -q`
   - Stop and re-evaluate if:
     - you start changing how registration is triggered
     - you need to change backend class behavior to make imports work
   - Done when:
     - the targeted backend tests are green
     - direct code imports of `llm_backend` and `provider_cli_backend` are gone

4. Create the dedicated `provider_cli` subpackage and move the provider-only
   helpers under it.
   - Outcome: all `provider_cli`-specific helpers live in a single subpackage.
   - Files to add:
     - `weft/core/agents/provider_cli/__init__.py`
   - Files to move:
     - `weft/core/agents/provider_cli_execution.py`
       -> `weft/core/agents/provider_cli/execution.py`
     - `weft/core/agents/provider_registry.py`
       -> `weft/core/agents/provider_cli/registry.py`
     - `weft/core/agents/settings.py`
       -> `weft/core/agents/provider_cli/settings.py`
     - `weft/core/agents/probes.py`
       -> `weft/core/agents/provider_cli/probes.py`
   - Files to touch for import updates:
     - `weft/builtins/agent_images.py`
     - `weft/builtins/agent_probe.py`
     - `weft/core/agents/backends/provider_cli.py`
     - `extensions/weft_docker/weft_docker/agent_runner.py`
     - `tests/core/test_provider_cli_execution.py`
     - `tests/core/test_agent_validation.py`
     - any additional direct importer found by
       `rg -n 'provider_cli_execution|provider_registry|core\\.agents\\.settings|core\\.agents\\.probes' weft tests extensions/weft_docker`
   - Read first:
     - all four moved modules
     - `weft/builtins/agent_probe.py`
     - `extensions/weft_docker/weft_docker/agent_runner.py`
   - Required action:
     - keep provider helper function/class names unchanged
     - keep Docker runner using the shared provider-cli preparation helpers
     - make `weft/core/agents/provider_cli/__init__.py` a minimal package file;
       do not use it as a compatibility re-export surface
     - do not touch `extensions/weft_docker/dist/`
   - Testing:
     - `. ./.envrc && ./.venv/bin/python -m pytest tests/core/test_provider_cli_execution.py tests/core/test_provider_cli_backend.py tests/core/test_provider_cli_session_backend.py tests/core/test_builtin_agent_images.py extensions/weft_docker/tests/test_agent_runner.py -q`
   - Stop and re-evaluate if:
     - you are tempted to merge registry/settings/probes into one bigger file
     - you are tempted to redesign provider abstractions
     - extension tests require logic changes unrelated to path updates
   - Done when:
     - the targeted core and extension tests are green
     - direct code imports of the flat `provider_cli_*`, `settings`, and
       `probes` paths are gone

5. Sweep the remaining core, builtin, and extension imports to the new layout.
   - Outcome: no in-repo code depends on old module paths.
   - Files to touch:
     - `weft/ext.py`
     - `weft/commands/validate_taskspec.py`
     - `weft/core/tasks/consumer.py`
     - `weft/core/tasks/agent_session_protocol.py`
     - `weft/core/tasks/runner.py`
     - `weft/core/runners/host.py`
     - `weft/builtins/agent_probe.py`
     - `weft/builtins/agent_images.py`
     - `extensions/weft_docker/weft_docker/agent_runner.py`
     - all affected tests under `tests/core/`
   - Read first:
     - `weft/ext.py`
     - `weft/core/tasks/runner.py`
     - `weft/core/runners/host.py`
   - Required action:
     - prefer one import style per package boundary
     - keep cross-package imports explicit
     - do not rename test files unless a filename becomes actively misleading or
       collides; import updates inside existing tests are enough
   - Testing:
     - `. ./.envrc && ./.venv/bin/python -m pytest tests/tasks/test_agent_execution.py tests/tasks/test_runner.py tests/cli/test_cli_validate.py tests/cli/test_cli_run.py tests/core/test_builtin_agent_images.py -q`
   - Stop and re-evaluate if:
     - you start renaming unrelated tests or modules for aesthetic reasons
     - you need to change any queue or runtime semantics
   - Done when:
     - the targeted integration tests are green
     - `rg -n 'from weft\\.core\\.agent_|import weft\\.core\\.agent_|weft\\.core\\.agents\\.(provider_registry|provider_cli_execution|settings|probes)' weft tests extensions/weft_docker` returns no matches

6. Update module docstrings, current specs, and current active plans so the
   code-to-doc path stays honest.
   - Outcome: current documentation points to the new layout without rewriting
     historical records.
   - Files to touch:
     - moved modules under `weft/core/agents/`
     - `docs/specifications/13-Agent_Runtime.md`
     - `docs/specifications/10-CLI_Interface.md`
     - `docs/specifications/10B-Builtin_TaskSpecs.md`
     - `docs/plans/2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md`
     - `docs/plans/2026-04-14-docker-agent-images-and-one-shot-provider-cli-plan.md`
     - `docs/plans/2026-04-14-builtin-taskspecs-and-spec-resolution-plan.md`
   - Read first:
     - `docs/specifications/13-Agent_Runtime.md`
     - `docs/specifications/10B-Builtin_TaskSpecs.md`
   - Required action:
     - update implementation mappings and module-path references only
     - add this plan to the related-plans section of
       `docs/specifications/13-Agent_Runtime.md`
     - leave older `2026-04-13` and earlier plans untouched unless you have a
       separate approved reason
   - Grep proof:
     - no current implementation docs reference the old internal module paths.
   - Expected result:
     - zero matches
   - Stop and re-evaluate if:
     - you start editing older historical plans only to reduce grep noise
   - Done when:
     - current specs and current active plans point at the new paths

7. Run final grep and quality gates for an import-only refactor.
   - Outcome: the full repo is green with no behavior edits hidden in the move.
   - Commands to run in sequence:
     1. `. ./.envrc && rg -n 'weft\\.core\\.agent_|weft\\.core\\.agents\\.(provider_registry|provider_cli_execution|settings|probes)' weft tests extensions/weft_docker`
     2. `. ./.envrc && ./.venv/bin/python -m pytest extensions/weft_docker/tests -q`
     3. `. ./.envrc && ./.venv/bin/python -m pytest tests/core/test_agent_runtime.py tests/core/test_agent_tools.py tests/core/test_agent_resolution.py tests/core/test_agent_validation.py tests/core/test_llm_backend.py tests/core/test_provider_cli_backend.py tests/core/test_provider_cli_execution.py tests/core/test_provider_cli_session_backend.py tests/core/test_tool_profiles.py -q`
     4. `. ./.envrc && ./.venv/bin/python -m pytest tests/tasks/test_agent_execution.py tests/tasks/test_runner.py tests/cli/test_cli_validate.py tests/cli/test_cli_run.py tests/core/test_builtin_agent_images.py -q`
     5. `. ./.envrc && ./.venv/bin/python -m ruff check weft tests extensions/weft_docker`
     6. `. ./.envrc && ./.venv/bin/python -m mypy weft`
     7. `. ./.envrc && ./.venv/bin/python -m pytest`
     9. `. ./.envrc && ./.venv/bin/python -m pytest -m ""`
   - Interpretation:
     - grep commands must return zero matches
     - test commands must pass without logic edits beyond path/doc updates
   - Stop and re-evaluate if:
     - you feel pressure to "just fix" an unrelated failing test caused by
       pre-existing behavior
     - `ruff` or `mypy` failures tempt you into unrelated cleanup
   - Done when:
     - all commands are green or, if a pre-existing unrelated failure is
       proven, it is documented clearly with evidence

## Testing Plan

Keep these tests real. Do not replace them with mocks:

- `tests/core/test_agent_runtime.py`
- `tests/core/test_agent_validation.py`
- `tests/core/test_llm_backend.py`
- `tests/core/test_provider_cli_backend.py`
- `tests/core/test_provider_cli_session_backend.py`
- `tests/core/test_provider_cli_execution.py`
- `tests/tasks/test_agent_execution.py`
- `tests/tasks/test_runner.py`
- `tests/cli/test_cli_validate.py`
- `tests/cli/test_cli_run.py`
- `extensions/weft_docker/tests/test_agent_runner.py`

Why these matter:

- they cover the real runtime registry and dispatch path
- they cover backend resolution and provider-cli preparation
- they cover the first-party Docker extension import seam
- they cover the task and CLI surfaces that would break if a path update is
  missed

Because this is a layout refactor, new tests are generally not needed. The
main proof should be the existing suite staying green. Add a new test only if:

- the refactor exposes a previously untested import path that would otherwise
  remain unguarded

Even then, keep the new test tiny and structural. Do not add broad mock
fixtures just to assert imports.

## Rollout and Rollback

Rollout rule:

- land this refactor atomically with all in-repo importers updated, including
  the `weft_docker` extension

Why:

- there are no shims
- partial rollout leaves the repo in a broken-import state

Rollback rule:

- revert the full refactor together
- do not partially roll back only code or only docs
- do not revert only core without reverting the extension updates

Compatibility note:

- internal module paths are changing on purpose
- supported behavior should stay the same
- if you discover a supported external consumer outside this repo, the rollback
  discussion changes immediately because the refactor becomes a breaking
  external API change rather than an internal cleanup

## Review Note

Independent review is required before implementation if another agent family is
available.

Recommended review prompt:

> Read `docs/plans/2026-04-14-agent-runtime-package-refactor-plan.md` and the
> current agent-runtime code. Look for ambiguities, hidden behavior changes,
> and places where a zero-context engineer could accidentally introduce shims
> or logic edits. Could you implement this confidently and correctly?

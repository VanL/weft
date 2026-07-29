# Import Boundary Remediation Plan

Status: completed
Source specs: docs/specifications/00-Overview_and_Architecture.md §System Architecture/Runtime Layers; docs/specifications/01-Core_Components.md [CC-3.4]; docs/specifications/09-Implementation_Plan.md [IP-1], [IP-1.0]; docs/specifications/13-Agent_Runtime.md [AR-7]
Superseded by: none

Class: 4 — Architectural and public-surface preserving. This plan changes
package initialization, moves a runner result type, and locks import direction,
but does not change public names or runtime behavior.

## 1. Goal

Remove every verified avoidable import cycle and package-facade inversion in
the fixed Weft source graph while preserving the public imports callers use
today. Root, command, and core facades become lazy; internal modules import
leaf modules directly; `RunnerOutcome` moves to a neutral runner contract
module; architecture tests distinguish eager cycles from justified deferred
dispatch imports.

This is the second of four independent plans extracted from
[`2026-07-29-structural-review-remediation-plan.md`](./2026-07-29-structural-review-remediation-plan.md).
It can land before or after the validation-layering plan. The overlap in
`weft/commands/__init__.py` and the possible deletion of
`weft/commands/validate_taskspec.py` must be reconciled by rebasing, not by
restoring stale exports.

## 2. Source Documents

Governing specifications:

- `docs/specifications/00-Overview_and_Architecture.md` §System Architecture,
  especially §Runtime Layers, defines the adapter-to-capability-to-core
  dependency direction.
- `docs/specifications/01-Core_Components.md` [CC-3.4] assigns runner monitoring
  ownership.
- `docs/specifications/09-Implementation_Plan.md` [IP-1], [IP-1.0] define
  one-way ownership for `cli`, `client`, `commands`, and `core`.

Reference implementations:

- `../taut/taut/__init__.py` preserves a broad public facade with
  `TYPE_CHECKING`, `_LAZY_EXPORTS`, `__getattr__`, and `__dir__`.
- `../taut/tests/test_architecture_boundaries.py` demonstrates executable
  package-boundary checks.
- `../simplebroker/simplebroker/__init__.py` shows that a root facade need not
  load a CLI or command layer.

Required guidance:

- `CLAUDE.md` §4.2: imports at module top; function-local imports only for a
  real cycle or optional dependency and with a reason comment.
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`

## 3. Context and Key Files

### Verified current graph

Fresh-process measurements at baseline `a391a59b`:

- `import weft._constants` initializes 107 Weft modules, including the client,
  commands, core manager, and Rich.
- `weft.commands.__init__` eagerly imports manager, result, status, tidy,
  serve, init, and task-spec validation.
- `weft.core.__init__` eagerly imports Manager, launcher, resource monitors,
  targets, task classes, TaskRunner, and TaskSpec.

Verified avoidable cycles:

```text
commands facade cycle
  weft.commands
    -> result
    -> status -> system
    -> validate_taskspec
  result/system/validate_taskspec
    -> weft.commands package or package-owned siblings

core facade cycle
  weft.core
    -> manager
    -> monitor.task_monitor
    -> monitor.runtime
    -> weft.core facade

runner cycle
  runners.host
    -> runners.subprocess_runner
    -> runners.host::RunnerOutcome
       (TYPE_CHECKING plus function-local runtime import)
```

The root facade is not itself a business-logic SCC, because leaf modules do not
normally import `weft`. It is still an operational inversion: Python must
initialize `weft/__init__.py` before any `weft.*` leaf, so a low-level import
loads every upper adapter.

### Target graph

```text
weft facade
  eager: _constants only
  lazy: client and logging helper exports

weft.commands facade
  eager: no command implementations
  lazy: legacy cmd_* and manager exports
  internal command modules -> explicit sibling leaf modules

weft.core facade
  eager: no runtime implementations
  lazy: current public core exports
  internal core modules -> explicit core leaf modules

runners.outcome
  <- runners.host
  <- runners.subprocess_runner
  <- existing runner consumers

No fixed runtime edge points from a leaf implementation back through its
package facade merely to reach a sibling.
```

### Files to modify

Facade work:

- `weft/__init__.py`
- `weft/commands/__init__.py`
- `weft/core/__init__.py`

Internal command imports:

- `weft/commands/events.py`
- `weft/commands/result.py`
- `weft/commands/run.py`
- `weft/commands/submission.py`
- `weft/commands/system.py`
- `weft/commands/tasks.py`
- `weft/commands/validate_taskspec.py` if it still exists when this plan lands

Core back-edge:

- `weft/core/monitor/runtime.py`

Runner contract:

- new `weft/core/runners/outcome.py`
- `weft/core/runners/host.py`
- `weft/core/runners/subprocess_runner.py`
- `weft/core/runners/__init__.py`
- direct `RunnerOutcome` importers found by the pre-edit reference scan

Tests:

- `tests/architecture/test_import_boundaries.py`
- public-import tests that currently assume eager initialization, if any

Documentation:

- implementation mappings or module docstrings that name
  `RunnerOutcome` ownership
- `docs/plans/README.md`

### Read first

- all three package `__init__.py` files
- `weft/core/runners/host.py` and `subprocess_runner.py`
- `weft/core/monitor/task_monitor.py` import block and
  `weft/core/monitor/runtime.py`
- `tests/architecture/test_import_boundaries.py`, especially
  `_iter_import_edges`
- every result of:

  ```bash
  rg -n "from weft\.commands import|from \. import" weft/commands
  rg -n "from weft\.core import" weft/core
  rg -n "RunnerOutcome" weft tests integrations extensions
  ```

### Existing paths to reuse

- Copy Taut's small lazy-export pattern, adapted to Weft's names. Do not build a
  facade framework.
- For the module-valued `weft.commands.manager` export, add a separate literal
  `_LAZY_MODULES = {"manager": "weft.commands.manager"}` branch that returns
  and caches `import_module(module_name)`. Do not force module exports into
  Taut's `(module, attribute)` tuple shape.
- Preserve every current `__all__` name.
- Continue using Python's normal submodule imports for
  `from weft.commands import specs` and similar adapter-side imports.
- Keep the existing architecture-test AST parser, but extend its edge model
  only as far as the tests in Task 4 require.

### Comprehension questions

1. Why does `import weft._constants` execute `weft/__init__.py`, and which
   upper layers does that initialize today?
2. Which `RunnerOutcome` import executes at function runtime, and why is it
   needed only because the type is owned by `host.py`?
3. Why must a general cycle test distinguish eager module-level imports from
   deferred task-class dispatch imports inside Manager?
4. Which facade names are public compatibility obligations even though their
   implementations become lazy?

## 4. Invariants and Constraints

Must remain unchanged:

- All names in the current `weft.__all__`, `weft.commands.__all__`, and
  `weft.core.__all__` resolve to the same objects after first access.
- `from weft import WeftClient`, `from weft.core import Manager`, and current
  command facade imports remain valid.
- `RunnerOutcome` remains importable from every currently supported path:
  `weft.core.runners`, `weft.core.runners.host`, and
  `weft.core.tasks.runner`.
- No task execution, runner monitoring, process launch, result shape, exception
  type, or CLI behavior changes.
- Lazy lookup caches the resolved object in module globals, matching Taut and
  avoiding repeated import work.
- `__dir__` includes lazy public names; `from package import *` follows
  `__all__`.
- Type checkers see public exports under `TYPE_CHECKING`.
- Optional, platform, plugin, and user-supplied dynamic imports retain their
  current guards.

Not allowed:

- removing or renaming public facade exports
- adding an import framework or dependency
- using `try/except ImportError` to hide a fixed source-graph cycle
- converting justified Manager task-class dispatch imports merely to satisfy a
  naive graph test
- asserting an exact import duration or exact total module count
- broad module splitting unrelated to imports

Rollback:

- Facade changes are independently revertible because public names remain
  stable.
- The `RunnerOutcome` move is reverted together with its compatibility
  re-export and all import sites.
- There is no persisted state, rollout order, or one-way door.

## 4a. Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|
| Task 2 import isolation / [AR-7] | Lazy package facades prevent validation capability imports from loading Rich. | The package facades were made lazy, and built-in agent adapter registration was also moved from `weft.core.agents` import time to the explicit host/runtime registration boundary. | `commands.specs` imports agent validation. Python initializes `weft.core.agents` first, and its prior registration side effect loaded `llm`, which loaded Rich. Deferring adapter loading is required for the plan's fresh-process contract while preserving registration before execution. | Closed: [AR-7] now records explicit registration ownership; runtime behavior is unchanged. |

## 4b. Spec Baseline

- `a391a59b345a8e37dc6d5c362525f84be0f70343` —
  `docs/specifications/00-Overview_and_Architecture.md`,
  `docs/specifications/01-Core_Components.md`, and
  `docs/specifications/09-Implementation_Plan.md`
- Plan type: behavior-preserving architectural remediation; no proposed spec
  delta

## 5. Tasks

### Task 1 — Make the root facade lazy

- **Outcome:** importing any low-level `weft.*` leaf initializes only the root
  constants and the requested dependency path.
- **Files:** `weft/__init__.py`, architecture/public-import tests
- **Approach:**
  1. Record the current `__all__` inventory and identity of every export.
  2. Keep only `PROG_NAME` and `__version__` eager.
  3. Add `TYPE_CHECKING` imports for client and helper exports.
  4. Add a literal `_LAZY_EXPORTS` mapping and Taut-shaped `__getattr__` and
     `__dir__`; cache resolved objects in `globals()`.
  5. Preserve current `AttributeError` behavior for unknown names.
- **Tests:**
  - fresh subprocess: `import weft._constants` does not load `weft.client`,
    `weft.commands`, `weft.core`, or Rich
  - access each lazy name and compare object identity to its owning module
  - repeated access returns the cached object
  - `dir(weft)` contains every `__all__` name
- **Stop if:** preserving an export requires importing an upper package before
  that name is requested.
- **Done when:** the public inventory is unchanged and the fresh-process import
  isolation test passes.

### Task 2 — Make commands and core facades lazy

- **Outcome:** importing one commands/core leaf does not initialize unrelated
  commands or Manager/task runtime modules.
- **Files:** `weft/commands/__init__.py`, `weft/core/__init__.py`, the internal
  command import files listed in §3, `weft/core/monitor/runtime.py`
- **Approach:**
  1. Preserve both facades' current `__all__` inventories through
     `TYPE_CHECKING`, literal lazy maps, `__getattr__`, and `__dir__`.
     `weft.commands.manager` uses the `_LAZY_MODULES` branch described in §3;
     all command/function/class exports use `_LAZY_EXPORTS`.
  2. In command implementation modules, replace package-facade sibling access
     such as `from weft.commands import specs` and `from . import
     task_evidence` with explicit leaf-module imports. Do not mechanically
     rewrite CLI, client, integration, or test imports that legitimately enter
     through the package namespace.
  3. Change `weft/core/monitor/runtime.py` to import the `task_evidence` leaf
     directly instead of reaching through `weft.core`.
  4. If the validation-layering plan has already deleted
     `commands/validate_taskspec.py`, do not recreate it or its facade export.
     Preserve the rebased `__all__` inventory and remaining validation
     capability paths instead.
- **Tests:**
  - fresh subprocess: importing `weft.commands.specs` does not load result,
    status, system, validation rendering, or Rich
  - fresh subprocess: importing `weft.core.task_evidence` does not load Manager
    or TaskMonitor
  - every lazy facade export resolves to the owning object
  - `weft.commands.manager`, `from weft.commands import manager`, and command
    star import all resolve the same module object without recursion
- **Stop if:** a leaf import wants a function-local import solely because the
  facade remains partially initialized.
- **Done when:** no internal command/core implementation imports its own package
  facade to reach a sibling, except an explicitly justified dynamic path.

### Task 3 — Move `RunnerOutcome` to a neutral contract module

- **Outcome:** host and subprocess runner have one-way imports.
- **Files:** new `weft/core/runners/outcome.py`, host, subprocess runner,
  runners facade, and verified importers
- **Approach:**
  1. Move only the dataclass and its own direct type dependencies.
  2. Import it normally at module level from both runner implementations.
  3. Delete the `TYPE_CHECKING` and function-local imports from
     `subprocess_runner.py`.
  4. Re-export from `host.py`, `weft.core.runners`, and
     `weft.core.tasks.runner`; preserve the TYPE_CHECKING path used by
     `weft/ext.py`.
  5. Update [CC-3.4] implementation mapping or module docstrings only if they
     claim the old type ownership.
- **Tests:** existing runner behavior tests plus identity assertions showing
  compatibility re-exports are the same class object.
- **Stop if:** moving the dataclass starts pulling host execution behavior into
  the neutral module.
- **Done when:** no runtime or type-only edge points from subprocess runner to
  host, and all existing import paths pass.

### Task 4 — Add truthful import-graph guardrails

- **Outcome:** future eager cycles and leaf-to-facade back-edges fail tests
  without outlawing justified deferred dispatch.
- **File:** `tests/architecture/test_import_boundaries.py`
- **Approach:**
  1. Extend `ImportEdge` with enough context to distinguish module-level eager,
     `TYPE_CHECKING`, and function-local imports. Retain both the syntactic
     import base/form and the resolved runtime target.
  2. Emit one edge per imported alias. For both relative and absolute forms
     (`from . import sibling` and `from weft.commands import specs`), resolve an
     alias to `base.alias` when that child module or package exists in the
     scanned source tree. If the alias is an attribute rather than a child
     module, retain the edge to the base module. Keep the original base so the
     own-facade syntactic guard can still reject leaf modules that reach
     siblings through their package facade.
  3. Add an SCC assertion over unconditional module-level Weft imports. Exclude
     `TYPE_CHECKING`; do not silently exclude fixed top-level imports.
  4. Add a runners-package runtime SCC assertion that includes eager and
     function-local imports but excludes `TYPE_CHECKING` edges, permanently
     covering the former host/subprocess cycle.
  5. Add a guard forbidding command/core implementation leaves from importing
     their own package facade to reach fixed siblings.
  6. Keep fresh-process facade-isolation tests alongside these static checks.
- **Not allowed:** a blanket allowlist for current SCCs, ignoring every
  function-local import, or treating plugin/user dynamic imports as fixed
  source edges.
- **Mutation proof:** deliberately restore each former back-edge and show the
  corresponding guard fails.
- **Done when:** all verified cycles are absent, the tests detect deliberate
  reintroduction, and Manager's justified dispatch imports remain documented
  and accepted.

### Task 5 — Reconcile ownership documentation

- Update module docstrings for lazy facades and `runners/outcome.py`.
- Update nearby implementation mappings only where module ownership changed.
- Add reciprocal plan backlinks to the governing specs.
- Close all Deviation Log rows.
- Verify no documentation claims that importing a leaf initializes the whole
  public facade.

## 6. Testing Plan

```text
FRESH PROCESS
  import weft._constants
    -> no client/commands/core/Rich
  import weft.commands.specs
    -> no unrelated command modules
  import weft.core.task_evidence
    -> no Manager/TaskMonitor

PUBLIC COMPATIBILITY
  every __all__ name
    -> same owning object
    -> cached after first access

STATIC GRAPH
  eager module edges
    -> no SCC
  runner runtime edges
    -> no SCC including function-local imports
  internal leaves
    -> no own-facade sibling access

RUNTIME
  runner tests
    -> RunnerOutcome identity and behavior unchanged
```

Subprocess import tests must assert forbidden module presence, not wall-clock
timing or one exact module count. Import timing is diagnostic evidence only.

Do not mock Python imports. Use fresh repo-managed Python subprocesses so
`sys.modules` starts clean. Existing runner tests remain the behavioral proof;
the new identity tests cover only the moved type.

## 7. Verification and Gates

Per task:

```bash
./.venv/bin/python -m pytest tests/architecture/test_import_boundaries.py -q
./.venv/bin/python -m pytest tests/tasks/test_runner.py tests/tasks/test_task_execution.py -q
```

Final:

```bash
. ./.envrc
./.venv/bin/python -m pytest -m ""
./.venv/bin/mypy weft bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check weft
./bin/check-doc-paths
./bin/check-dom15-fixtures
```

Manual compatibility proof:

```bash
./.venv/bin/python -c "from weft import WeftClient; print(WeftClient)"
./.venv/bin/python -c "from weft.core import Manager; print(Manager)"
./.venv/bin/python -c "from weft.core.runners.host import RunnerOutcome; print(RunnerOutcome)"
```

## 8. Independent Review Loop

Review is required because public package imports are compatibility surfaces.
The reviewer must inspect the plan, all three facades, the host/subprocess
pair, monitor runtime, the AST test helper, and the Taut reference.

Review prompt:

> Answer PASS or BLOCKED. Could a zero-context engineer preserve every public
> import while removing all verified eager and deferred cycles? Does the test
> graph model real runtime edges without false-passing `from . import sibling`
> or false-failing justified Manager dispatch? Check overlap with the separate
> validation plan and reject any stale export restoration.

Every finding receives a disposition and accepted changes receive a scoped
round-2 verification.

## 9. Out of Scope

- Plugin- or user-supplied dynamic module cycles
- Import-time performance work inside `llm` or other third-party packages
- Removing public facade names
- Splitting Manager, TaskMonitor, helpers, or command modules for size
- Validation capability behavior, status reduction, converter deduplication,
  and test-integrity repairs covered by the other independent plans

## 10. Fresh-Eyes Review

Completed 2026-07-29 against the draft and current source:

- Replaced nonexistent architecture reference codes with the exact governing
  headings in `00-Overview_and_Architecture.md`.
- Pinned all supported `RunnerOutcome` compatibility paths rather than using
  the ambiguous phrase “existing imports.”
- Distinguished module-valued lazy exports from object-valued exports so
  `weft.commands.manager` cannot recurse through `__getattr__`.
- Made the AST edge model resolve both relative and absolute child-module
  imports while retaining the syntactic facade target for the boundary guard.
- Made the runner runtime SCC include eager and function-local imports but
  exclude `TYPE_CHECKING` edges.
- Replaced stale overlap wording with an explicit requirement to preserve the
  rebased `commands.__all__` and remaining capability paths.

## 11. Independent Review Result

Reviewer: `imports_layering` (independent agent), 2026-07-29.

Round 1: **BLOCKED**. The reviewer found four material ambiguities:

1. absolute `from package import child_module` aliases were not resolved as
   child-module runtime edges;
2. the Taut object-export tuple pattern did not cover the module-valued
   `weft.commands.manager` export;
3. the runner runtime SCC did not explicitly exclude `TYPE_CHECKING`; and
4. the validation-plan overlap instruction could restore stale exports.

All four were corrected in Tasks 1 and 4 and in the overlap contract.

Round 2: **PASS**. The same reviewer verified the four corrections and reported
no remaining or new material defects in the reviewed scope.

## 12. Implementation Review and Verification

Implemented and independently reviewed on 2026-07-29.

External reviewer: Grok CLI.

Round 1: **BLOCKED**.

1. `weft.core.agents.backends.__all__` still advertised `LLMBackend` and
   `ProviderCLIBackend` after their eager imports were removed, breaking direct
   and star imports.
2. The explicit registration boundary lacked fresh-process tests proving a
   schema-only package import does not register adapters and a host import does.
3. The architecture tests asserted the clean graph but did not contain
   synthetic mutation proofs for restored facade and runner-cycle back-edges.

Disposition:

- added lazy compatibility exports and identity/star-import tests for both
  backend classes;
- added fresh-process negative and positive registration tests; and
- extracted a source parser seam and added mutation proofs for relative and
  absolute facade back-edges, a function-local runner cycle, and a
  `TYPE_CHECKING` non-cycle.

Round 2: **PASS**. Grok verified all three dispositions, found no regression,
and independently ran the architecture suite (`32 passed`).

Final verification:

- full suite: `2418 passed, 14 skipped`
- full repository mypy: `Success: no issues found in 194 source files`
- Ruff: passed
- DOM-15 fixtures: passed
- documentation path check: no new dangling claims; the same eight pre-existing
  repository-wide claims remain
- whitespace/error check: passed

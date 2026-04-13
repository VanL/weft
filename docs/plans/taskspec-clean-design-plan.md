# TaskSpec Clean Design Plan

This document is the implementation plan for the **TaskSpec clean-design**
cleanup discussed in the review follow-up.

It is **not** the implementation plan for the agent `messages` role-boundary
bug in the `llm` backend. That bug is real, but it is a separate slice.

This plan is for an engineer who is strong in Python but has **almost no
context** for Weft, SimpleBroker, or the design history of this repository.
Assume they will:

- overbuild abstractions if the plan leaves room for it,
- mutate objects in place because it feels convenient,
- over-mock tests instead of running real queues and processes,
- and drift into a larger redesign if the boundaries are not explicit.

This document does not leave room for those mistakes.

Read this plan as a set of **bite-sized tasks**. Each task tells the
implementer:

- what to read first,
- which files to touch,
- which tests to write first,
- which invariants must hold,
- and which gates must pass before moving on.

## 0. Scope Lock

Implement exactly these outcomes:

1. Resolved `TaskSpec` objects enforce the documented boundary for real:
   - `tid` is immutable,
   - `spec` is deeply immutable,
   - `io` is deeply immutable,
   - `state` remains mutable,
   - `metadata` remains mutable.
2. All runtime expansion happens **before** a resolved `TaskSpec` is
   constructed:
   - TID assignment,
   - queue-name derivation,
   - queue-name TID rewrite,
   - `spec.weft_context` fill,
   - and any default expansion required for a resolved task.
3. No runtime code mutates `spec`, `io`, or `tid` after validation.
4. The public TaskSpec JSON schema stays recognizable to current callers:
   - no new database,
   - no new orchestration plane,
   - no new lifecycle model,
   - no required migration for stored task-spec JSON files beyond normal
     revalidation.
5. The Manager spawn path, CLI run path, and tests all use the same resolution
   rules instead of duplicating slightly different mutations.
6. Repository docs stop claiming a stronger invariant than the code enforces.

Do **not** implement any of the following in this slice:

- a brand-new `TaskDefinition` / `TaskRuntime` public object model,
- a new user-visible TaskSpec JSON schema,
- a new serialization format,
- a second runtime-state store,
- a new builder framework,
- generic copy-on-write infrastructure,
- speculative plugin hooks for alternative immutability backends,
- a broader agent-runtime redesign,
- or the `llm` structured-message fix.

If implementation pressure starts pulling toward any of those, stop. That is
scope drift.

## 1. What "Clean Design" Means Here

Do **not** misread "clean design" as "replace Weft with a different object
model."

The clean design for this slice is:

1. Keep the current **public** TaskSpec shape.
2. Make the phase boundaries explicit:
   - template payload,
   - resolved payload,
   - frozen resolved `TaskSpec`,
   - mutable runtime state updates.
3. Make the immutability guarantees true in practice, not just true for
   attribute reassignment.
4. Make runtime expansion copy-based and deterministic, not mutation-based.
5. Preserve current convenience for direct `TaskSpec(...)` construction of
   resolved tasks without preserving the old "mutate in `model_post_init`"
   implementation strategy.

This is a cleanup and hardening pass, not a reinvention pass.

## 2. Read This First

Read these files in this order before editing code:

1. [`AGENTS.md`](../../AGENTS.md)
2. [`docs/specifications/00-Overview_and_Architecture.md`](../specifications/00-Overview_and_Architecture.md)
3. [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
4. [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md)
5. [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
6. [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
7. [`docs/specifications/08-Testing_Strategy.md`](../specifications/08-Testing_Strategy.md)
8. [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
9. [`README.md`](../../README.md)
10. [`weft/core/taskspec.py`](../../weft/core/taskspec.py)
11. [`weft/core/manager.py`](../../weft/core/manager.py)
12. [`weft/commands/run.py`](../../weft/commands/run.py)
13. [`weft/core/tasks/base.py`](../../weft/core/tasks/base.py)
14. [`tests/taskspec/fixtures.py`](../../tests/taskspec/fixtures.py)
15. [`tests/taskspec/test_taskspec.py`](../../tests/taskspec/test_taskspec.py)
16. [`tests/core/test_manager.py`](../../tests/core/test_manager.py)
17. [`tests/cli/test_cli_run.py`](../../tests/cli/test_cli_run.py)
18. [`tests/specs/manager_architecture/test_tid_correlation.py`](../../tests/specs/manager_architecture/test_tid_correlation.py)
19. [`tests/specs/manager_architecture/test_agent_spawn.py`](../../tests/specs/manager_architecture/test_agent_spawn.py)
20. [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py)
21. [`tests/conftest.py`](../../tests/conftest.py)

Also inspect the installed SimpleBroker package before introducing helpers.
Do not hardcode a machine-specific path. Locate the installed module first, for
example:

```bash
uv run python - <<'PY'
import inspect
import simplebroker.helpers
import simplebroker.sbqueue
import simplebroker.watcher

print(inspect.getsourcefile(simplebroker.sbqueue))
print(inspect.getsourcefile(simplebroker.watcher))
print(inspect.getsourcefile(simplebroker.helpers))
PY
```

Then read the resolved files.

You are not copying SimpleBroker code. You are learning the local style:

- direct names,
- small helpers,
- copy-based transformations,
- real cleanup,
- and real integration tests against actual queues.

## 3. Engineering Rules

These rules are mandatory.

### 3.1 Style

- Use `from __future__ import annotations` in every new Python module.
- Keep imports at module top. No deferred imports.
- Use `collections.abc` abstract types.
- Use `Path`, not `os.path`.
- Add docstrings with spec references when behavior is non-trivial.
- Add comments rarely and only when the code would otherwise be hard to parse.
- Keep helpers single-purpose and boringly named.

### 3.2 DRY and YAGNI

- Be DRY where duplication already exists and is causing semantic drift.
- Be YAGNI where an abstraction only protects against hypothetical future work.
- Do not build a generic model-freezing framework for the entire repository.
- Do not introduce more top-level model types than this slice needs.
- Reuse existing TaskSpec sections (`spec`, `io`, `state`, `metadata`) instead
  of inventing a new public hierarchy.

### 3.3 TDD

For every task below, follow red-green-refactor:

1. Write the smallest failing test that proves the intended behavior.
2. Make it pass with the smallest coherent implementation.
3. Refactor only after the behavior is locked in.

Do not start with a giant refactor and hope the tests catch regressions later.

### 3.4 Test Design

- Prefer real `TaskSpec` objects, real `Queue` instances, real Manager/CLI
  paths, and `WeftTestHarness`.
- Do not mock `simplebroker.Queue`.
- Do not mock the Manager spawn path.
- Do not patch low-level mutation internals to "prove" immutability.
- When testing deep immutability, attempt real operations:
  - item assignment,
  - `.append(...)`,
  - `.update(...)`,
  - `.setdefault(...)`,
  - and field replacement.
- When testing resolution, assert on observable payloads and queues rather than
  private flags.
- A pure helper test is allowed for resolution logic, but every important rule
  must also be covered at a higher integration layer.

### 3.5 Verification Discipline

Run focused tests while iterating, then run the full gates **sequentially**:

```bash
uv run pytest tests/taskspec/test_taskspec.py -q
uv run pytest tests/core/test_manager.py tests/cli/test_cli_run.py tests/specs/manager_architecture/test_tid_correlation.py tests/specs/manager_architecture/test_agent_spawn.py -q
uv run pytest
uv run pytest -m ""
uv run ruff check weft tests
uv run mypy weft
```

Do not run multiple `uv run ...` commands in parallel. This repo shares a
virtualenv and parallel runs can create false failures.

## 4. Fixed Design Decisions

These decisions are not open during implementation.

### 4.1 Keep the Public TaskSpec Shape

The public TaskSpec JSON layout remains:

- top-level `tid`, `version`, `name`, `description`,
- `spec`,
- `io`,
- `state`,
- `metadata`.

Do **not** replace this with a different JSON schema in this slice.

The hard immutability guarantee in this slice is about **resolved** TaskSpecs.
Template payloads and template TaskSpecs may continue to be lighter-weight as
long as existing template behavior stays intact and runtime resolution remains
the only path to a launchable task.

### 4.2 Phase Separation Happens in Construction, Not After Construction

All of the following must happen before a resolved `TaskSpec` is returned:

- validate whether the payload is a template or resolved task,
- apply resolved-task defaults,
- rewrite queue names to the final TID,
- fill derived queue bindings,
- fill runtime-expanded `spec.weft_context` when applicable,
- normalize nested containers into immutable forms for frozen sections.

After that point, runtime code must treat `tid`, `spec`, and `io` as read-only.

### 4.3 Deep Immutability Must Be Real

Blocking only `taskspec.spec = ...` or `taskspec.io = ...` is not enough.

The following must also fail on resolved tasks:

- `taskspec.spec.args.append("x")`
- `taskspec.spec.keyword_args["k"] = "v"`
- `taskspec.spec.env.update({...})`
- `taskspec.io.outputs["outbox"] = "other"`
- `taskspec.io.inputs.setdefault("inbox", "...")`
- `taskspec.spec.agent.options["temperature"] = 0.1`
- `taskspec.spec.agent.runtime_config["plugin_modules"] = [...]`
- `taskspec.spec.agent.templates["name"] = ...`

If the docs say `spec` and `io` are immutable, those operations must fail.

### 4.4 `state` and `metadata` Stay Mutable

This slice must not make runtime state awkward to update.

These operations must continue to work:

- `mark_running()`
- `mark_completed()`
- `mark_failed()`
- `update_metrics(...)`
- `update_metadata(...)`
- `set_metadata(...)`

### 4.5 Prefer Copy-Based Resolution Over Mutation Guards

Do not solve the whole problem with "allow mutation for one more internal
escape hatch."

The preferred design is:

- transform raw payloads as plain dict/list data,
- then validate once,
- then freeze.

That is simpler, more honest, and easier to test than a maze of temporary
mutation overrides.

### 4.6 Do Not Split `TaskSpec` Into Five Public Classes in This Slice

The repo does not need a sweeping rename to `TaskDefinition`, `TaskBindings`,
`TaskSnapshot`, and similar types right now.

If you find yourself creating multiple new public top-level model modules, you
are over-rotating. Keep the cleanup local to `weft/core/taskspec.py` unless a
tiny helper module is clearly justified.

## 5. Current Problem Sites

These are the concrete problem sites this plan is fixing.

1. [`weft/core/taskspec.py`](../../weft/core/taskspec.py)
   - frozen sections are only shallowly protected,
   - `apply_defaults()` is mutation-oriented,
   - mutator helpers (`add_input_queue`, `add_output_queue`) teach the wrong
     pattern,
   - nested dict/list fields inside frozen sections stay writable.
2. [`weft/core/manager.py`](../../weft/core/manager.py)
   - `_build_child_spec()` still mutates `child_spec.io.inputs` after
     validation.
3. [`weft/commands/run.py`](../../weft/commands/run.py)
   - TID rewrite and resolved-spec building are duplicated,
   - CLI resolution rules can drift from Manager resolution rules,
   - several paths use shallow top-level copies (`dict(...)`) and then mutate
     nested structures, which is easy to get wrong when payloads contain nested
     dict/list state.
4. Tests
   - current immutability coverage proves only shallow assignment blocking,
   - there is little direct coverage of the "no post-validation mutation"
     invariant,
   - there is no focused regression test for deep nested writes inside
     `spec`, `io`, and `spec.agent`.
5. Docs
   - current docs describe a stronger invariant than the implementation
     enforces.

## 6. Target End State

At the end of this slice, the design should look like this.

### 6.1 Two Explicit Data Phases

There are only two meaningful TaskSpec data phases:

1. **Template payload / template TaskSpec**
   - may omit `tid`,
   - may omit runtime queue bindings,
   - not yet launchable,
   - stored on disk or built by CLI helpers.
2. **Resolved TaskSpec**
   - has final `tid`,
   - has final queue bindings,
   - has resolved `spec.weft_context` when applicable,
   - has resolved defaults,
   - is launchable,
   - has truly immutable `tid`, `spec`, and `io`.

Do not introduce a third half-resolved limbo state in runtime code.

### 6.2 One Resolution Path

Manager and CLI must use the same resolution logic for:

- TID insertion,
- TID rewrite in queue names,
- derived queue defaults,
- `weft_context` propagation,
- and resolved-task default expansion.

The exact helper name is up to the implementer, but it must live in one place
and be reused.

Recommended location:

- `weft/core/taskspec.py`

Recommended shape:

- a pure helper that accepts a raw mapping and resolution inputs,
- returns a new JSON-friendly dict,
- does not mutate the input payload,
- starts from a deep copy or equivalent fresh payload, not a shallow
  top-level copy,
- and does not require a partially constructed `TaskSpec` instance.

### 6.3 Deeply Frozen Config and Bindings

Resolved tasks must use immutable containers inside:

- `SpecSection`
- `AgentSection`
- `IOSection`

Examples:

- `args` should no longer be an appendable list on resolved tasks,
- `keyword_args` should no longer be a mutable dict on resolved tasks,
- `env` should no longer be mutable on resolved tasks,
- `io.inputs` / `io.outputs` / `io.control` should no longer be mutable maps.

The specific immutable container implementation is up to the implementer, but
it must satisfy all of these:

1. it preserves JSON-friendly `model_dump(...)` behavior,
2. it has predictable equality semantics for tests,
3. it raises cleanly on mutation attempts,
4. it does not require callers to know about Pydantic internals,
5. it does not add a new third-party dependency.

Recommended approach:

- tuples for ordered sequences,
- a small internal frozen mapping type for dict-like sections.

Do **not** add a dependency like `frozendict` for this slice.

### 6.4 No Post-Validation Mutation of Frozen Sections

After `TaskSpec.model_validate(..., context={"auto_expand": True})` returns a
resolved object, runtime code must not mutate:

- `tid`,
- `spec`,
- any nested field under `spec`,
- `io`,
- or any nested field under `io`.

If something needs to change, change the raw payload **before** validation.

### 6.5 State and Metadata APIs Still Feel Normal

This cleanup must not make ordinary runtime updates harder.

`mark_*`, metric updates, and metadata updates should remain direct and boring.

## 7. Implementation Tasks

Follow these tasks in order. Do not skip ahead.

This order is intentional:

1. lock the desired behavior,
2. centralize resolution,
3. remove runtime dependence on mutation,
4. then harden deep immutability.

Do **not** freeze nested containers first and then scramble to repair the
Manager/CLI paths afterward. That produces a noisier diff and makes root cause
analysis harder.

### Task 1: Lock in the Desired Behavior With Tests

#### Goal

Add failing tests that describe the clean-design boundary before changing
runtime code.

#### Read First

1. [`weft/core/taskspec.py`](../../weft/core/taskspec.py)
2. [`tests/taskspec/test_taskspec.py`](../../tests/taskspec/test_taskspec.py)
3. [`tests/taskspec/fixtures.py`](../../tests/taskspec/fixtures.py)
4. [`tests/core/test_manager.py`](../../tests/core/test_manager.py)
5. [`tests/cli/test_cli_run.py`](../../tests/cli/test_cli_run.py)

#### Files to Touch

- [`tests/taskspec/test_taskspec.py`](../../tests/taskspec/test_taskspec.py)
- [`tests/core/test_manager.py`](../../tests/core/test_manager.py)
- [`tests/cli/test_cli_run.py`](../../tests/cli/test_cli_run.py)
- optionally [`tests/taskspec/fixtures.py`](../../tests/taskspec/fixtures.py) if
  fixtures need additional nested data

#### Write These Tests First

1. A resolved TaskSpec rejects nested `spec` mutation:
   - `args.append(...)`
   - `keyword_args["k"] = "v"`
   - `env.update(...)`
2. A resolved TaskSpec rejects nested `io` mutation:
   - item assignment into `inputs` / `outputs` / `control`
   - `.setdefault(...)`
3. A resolved agent TaskSpec rejects nested agent-config mutation:
   - `options[...] = ...`
   - `runtime_config[...] = ...`
   - replacing template/tool mappings
4. `state` and `metadata` remain mutable.
5. Manager child-spec construction still produces an inbox queue without needing
   to mutate the resolved object afterward.
6. CLI `run --spec ...` and inline `run` still work end-to-end after the
   immutability hardening.

#### Test Design Notes

- Do not inspect private `_frozen` flags.
- Assert on actual operations failing via
  `pytest.raises((TypeError, AttributeError))`.
- Also assert the original value is unchanged after the failed write when that
  makes the test clearer.
- Prefer real object operations over helper stubs.
- For Manager/CLI, use the existing harness and real queues.

#### Invariants

- IMMUT.1, IMMUT.2, IMMUT.3, IMMUT.4
- MANAGER.4
- QUEUE.3

#### Gate

Run:

```bash
uv run pytest tests/taskspec/test_taskspec.py tests/core/test_manager.py tests/cli/test_cli_run.py -q
```

At minimum, the new deep-immutability tests in
`tests/taskspec/test_taskspec.py` should fail before implementation starts.
Some Manager/CLI coverage may already be green and should remain green as a
regression guard.

### Task 2: Replace Mutation-Oriented Resolution With a Pure Resolver

#### Goal

Centralize resolved-task construction rules in one copy-based helper.

#### Read First

1. [`weft/core/taskspec.py`](../../weft/core/taskspec.py)
2. [`weft/core/manager.py`](../../weft/core/manager.py)
3. [`weft/commands/run.py`](../../weft/commands/run.py)

#### Files to Touch

- [`weft/core/taskspec.py`](../../weft/core/taskspec.py)
- [`weft/core/manager.py`](../../weft/core/manager.py)
- [`weft/commands/run.py`](../../weft/commands/run.py)

#### Implementation Requirements

1. Introduce one pure resolution helper in `weft/core/taskspec.py`.
2. Move these behaviors into that helper:
   - fill final `tid`,
   - rewrite queue names from old TID to new TID,
   - derive missing required queue bindings,
   - fill `spec.weft_context` when omitted,
   - expand resolved-task defaults,
   - ensure the payload passed to validation is fully resolved.
3. Ensure the helper returns a **new** payload and does not mutate inputs.
4. Use this helper from both the Manager and CLI paths.
5. Stop relying on post-init mutation for resolved defaults. Preferred
   implementation: invoke the pure resolver before object construction, such as
   from a `model_validator(mode="before")` path or an equivalent
   pre-construction normalization hook.

#### Guidance

Keep the helper boring. A function is preferred over a new builder class unless
the implementer can justify a class with materially lower complexity.

Recommended helper shape:

- input: raw mapping + explicit resolution inputs,
- output: new dict,
- no side effects,
- no partially built model objects.

Important: `dict(payload)` is not enough here. The payload contains nested
sections (`spec`, `io`, `metadata`, `agent.runtime_config`, etc.). A shallow
copy still shares those nested structures and invites accidental mutation of
the caller's data. Use a real deep copy strategy or rebuild the nested payload
explicitly.

#### What to Do About `apply_defaults()`

`apply_defaults()` currently teaches the wrong pattern because it mutates an
instance in place.

Preferred outcome:

- remove it from normal runtime flow,
- update tests so they no longer depend on in-place defaulting,
- and either delete it or reduce it to a thin compatibility shim that does not
  mutate resolved objects.

If direct `TaskSpec(...)` / `TaskSpec.model_validate(...)` construction still
needs the old convenience of default expansion for resolved tasks, preserve the
convenience by resolving **before** final object construction. Do not preserve
the old post-construction mutation strategy just because current call sites are
used to it.

Do not keep a mutation-oriented API just because it already exists.

#### Invariants

- one resolution rule set across CLI and Manager,
- no runtime code mutates resolved `spec` / `io`,
- TID correlation still uses the spawn-request timestamp,
- queue defaults still follow the `T{tid}.` prefix rules.

#### Gate

Run:

```bash
uv run pytest tests/taskspec/test_taskspec.py tests/core/test_manager.py tests/cli/test_cli_run.py tests/specs/manager_architecture/test_tid_correlation.py tests/specs/manager_architecture/test_agent_spawn.py -q
```

### Task 3: Remove the Remaining Post-Validation Mutation Sites

#### Goal

Clean up the runtime call sites that currently rely on mutable resolved specs.

#### Read First

1. [`weft/core/manager.py`](../../weft/core/manager.py)
2. [`weft/commands/run.py`](../../weft/commands/run.py)
3. [`weft/core/tasks/base.py`](../../weft/core/tasks/base.py)

#### Files to Touch

- [`weft/core/manager.py`](../../weft/core/manager.py)
- [`weft/commands/run.py`](../../weft/commands/run.py)
- optionally [`weft/core/taskspec.py`](../../weft/core/taskspec.py) if a tiny
  helper is still needed

#### Required Cleanup

1. Remove `child_spec.io.inputs.setdefault(...)` from the Manager path by
   ensuring inbox defaults exist before validation.
2. Remove duplicated queue/TID resolution logic from `run.py` if it is now
   provided centrally.
3. Remove or rewrite any helper that encourages post-freeze mutation of
   `spec`/`io`.

#### Specific Question to Answer in Code Review

After this task, ask:

"Can any file under `weft/core/` or `weft/commands/` still mutate `tid`,
`spec`, or `io` on a resolved TaskSpec?"

If the answer is yes, this task is not done.

#### Invariants

- resolved TaskSpec is a read-only launch artifact,
- runtime code may inspect but not edit config/bindings,
- Manager and CLI still behave exactly as before from the user’s perspective.

#### Gate

Run:

```bash
uv run pytest tests/core/test_manager.py tests/cli/test_cli_run.py tests/specs/manager_architecture/test_tid_correlation.py tests/specs/manager_architecture/test_agent_spawn.py -q
```

### Task 4: Introduce Real Immutable Containers for Frozen Sections

#### Goal

Make nested config/binding containers actually immutable on resolved tasks.

#### Read First

1. [`weft/core/taskspec.py`](../../weft/core/taskspec.py)
2. [`tests/taskspec/test_taskspec.py`](../../tests/taskspec/test_taskspec.py)
3. [`tests/core/test_manager.py`](../../tests/core/test_manager.py)
4. [`tests/cli/test_cli_run.py`](../../tests/cli/test_cli_run.py)

#### Files to Touch

- [`weft/core/taskspec.py`](../../weft/core/taskspec.py)

#### Implementation Requirements

1. Add small internal helpers for freezing nested values in resolved sections.
2. Freeze nested containers inside:
   - `SpecSection`
   - `AgentSection`
   - `IOSection`
3. Preserve `model_dump(...)`, `model_dump_json(...)`, and equality behavior.
4. Keep state and metadata as ordinary mutable structures.

#### Preferred Direction

Use small, local primitives:

- tuples for sequences,
- an internal frozen mapping implementation for dict-like containers.

Avoid:

- a new dependency,
- reflection-heavy magic,
- or a generalized framework for the whole repository.

#### Important Constraint

Do not rely on temporary mutation escape hatches for normal runtime flow.
Freezing should happen as part of resolved-object construction.

#### Invariants

- nested writes into `spec` and `io` fail,
- reads and serialization still work,
- log snapshots still render ordinary JSON-friendly data,
- Manager and CLI remain green after the runtime mutation paths were removed in
  Task 3.

#### Gate

Run:

```bash
uv run pytest tests/taskspec/test_taskspec.py tests/core/test_manager.py tests/cli/test_cli_run.py tests/specs/manager_architecture/test_tid_correlation.py tests/specs/manager_architecture/test_agent_spawn.py -q
```

### Task 5: Remove or Reframe Bad Mutation APIs

#### Goal

Delete or constrain APIs that contradict the clean design.

#### Read First

1. [`weft/core/taskspec.py`](../../weft/core/taskspec.py)
2. [`tests/taskspec/test_taskspec.py`](../../tests/taskspec/test_taskspec.py)

#### Files to Touch

- [`weft/core/taskspec.py`](../../weft/core/taskspec.py)
- [`tests/taskspec/test_taskspec.py`](../../tests/taskspec/test_taskspec.py)

#### APIs to Revisit

- `add_input_queue(...)`
- `add_output_queue(...)`
- any other helper that mutates `io` or `spec` after construction

#### Preferred Outcome

If the helper is unused and only exists to mutate frozen sections, delete it.

If the helper is worth keeping, convert it into a copy-based helper that works
on raw payloads or returns a new TaskSpec rather than mutating a resolved one.

Do not leave mutation helpers in place with comments like "call this before
freezing." That is how the invariant drifts again.

#### Invariants

- public helpers no longer teach the wrong lifecycle,
- repository tests encode the new rule,
- future engineers have fewer footguns.

#### Gate

Run:

```bash
uv run pytest tests/taskspec/test_taskspec.py -q
```

### Task 6: Make the Docs Honest and Explicit

#### Goal

Update docs so they describe the design that the code now enforces.

#### Read First

1. [`docs/specifications/00-Overview_and_Architecture.md`](../specifications/00-Overview_and_Architecture.md)
2. [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
3. [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
4. this plan

#### Files to Touch

- [`docs/specifications/00-Overview_and_Architecture.md`](../specifications/00-Overview_and_Architecture.md)
- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
- optionally [`README.md`](../../README.md) if it makes a direct immutability
  claim that would now be stale

#### Required Doc Updates

1. Clarify that the public TaskSpec shape is unchanged.
2. Clarify the two phases:
   - template payload,
   - resolved frozen task.
3. Clarify that `spec` and `io` are **deeply** immutable on resolved tasks.
4. Clarify that `state` and `metadata` remain mutable runtime sections.
5. Clarify that runtime expansion happens before launch and not by mutating a
   live resolved object.

#### Invariants

- docs match code,
- docs do not promise more than is enforced,
- docs give future engineers the correct mental model.

#### Gate

No special command beyond the normal final gates, but review the diffs with a
skeptical eye:

- Did we describe current behavior or wishful behavior?
- Did we accidentally imply a broader redesign than was implemented?

## 8. Test Matrix

These behaviors must be covered by real tests before the work is done.

### 8.1 Deep Immutability

Must test all of the following against resolved objects:

- `spec.timeout = 5.0` fails
- `spec.args.append(...)` fails
- `spec.keyword_args["x"] = 1` fails
- `spec.env.update(...)` fails
- `io.outputs["outbox"] = ...` fails
- `io.inputs.setdefault(...)` fails
- `agent.instructions = ...` fails
- `agent.options["temperature"] = 0.1` fails
- `agent.runtime_config["plugin_modules"] = [...]` fails

Use `pytest.raises((TypeError, AttributeError))` unless the container choice
forces a more specific exception contract that is already documented in code.
Do not overfit tests to an incidental exception message.

### 8.2 Allowed Mutation

Must test all of the following still work:

- `mark_running(...)`
- `mark_completed(...)`
- `update_metrics(...)`
- `update_metadata(...)`
- `set_metadata(...)`

### 8.3 Resolution Behavior

Must test:

- template payload may omit `tid`
- resolved payload gets final `tid`
- queue names rewrite from old TID to new TID
- missing required resolved bindings are filled before validation
- custom queue names are preserved
- `weft_context` fills correctly when omitted

### 8.4 Integration Behavior

Must test:

- Manager spawn still works
- child TID still equals spawn-request timestamp
- CLI inline `run` still works
- CLI `run --spec` still works
- no-wait + result flow still works
- persistent spec behavior still works if already covered by the repo

### 8.5 Negative Behavior

Must test at least one failure proving that runtime code no longer depends on
post-validation mutation.

The cleanest way is:

- make the Manager/CLI path use a resolved object with deeply frozen `io`,
- prove the path still works without mutation,
- and separately prove the old mutation operation would now fail.

## 9. Final Gates

Before claiming the slice is done, all of the following must pass:

```bash
uv run pytest tests/taskspec/test_taskspec.py -q
uv run pytest tests/core/test_manager.py tests/cli/test_cli_run.py tests/specs/manager_architecture/test_tid_correlation.py tests/specs/manager_architecture/test_agent_spawn.py -q
uv run pytest
uv run pytest -m ""
uv run ruff check weft tests
uv run mypy weft
```

Also perform one manual grep as a sanity check:

```bash
rg -n "setdefault\\(|\\.append\\(|\\.update\\(|\\[[^]]+\\]\\s*=" weft/core weft/commands
```

Review every hit that touches:

- `taskspec.spec`
- `taskspec.io`
- `child_spec.spec`
- `child_spec.io`

The final diff should not contain a legitimate post-validation mutation of a
resolved TaskSpec frozen section.

## 10. Fresh-Eyes Review Checklist

Use this checklist after implementation and again before merge.

### 10.1 Scope Check

- Did we keep the public TaskSpec JSON shape?
- Did we avoid introducing a new orchestration model?
- Did we avoid mixing in the separate agent `messages` bug?
- Did we avoid adding a dependency?

### 10.2 Design Check

- Is resolution copy-based?
- Is there exactly one resolution rule set?
- Are `spec` and `io` deeply immutable for resolved tasks?
- Are `state` and `metadata` still easy to mutate?

### 10.3 Test Check

- Are there real integration tests for Manager and CLI paths?
- Are deep-immutability tests using real operations instead of internals?
- Did we avoid mocking queues and state transitions?
- Are the failure tests proving the right thing instead of just testing Python
  container mechanics in isolation?

### 10.4 Doc Check

- Do the docs describe what the code actually does now?
- Do they avoid promising a larger redesign than shipped?
- Would a new engineer know that runtime expansion must happen before
  construction?

## 11. Stop Conditions

Stop and ask for clarification if any of these become true:

1. Making `spec`/`io` deeply immutable would require a broad user-facing JSON
   schema break.
2. Pydantic serialization cannot be kept stable without adding a dependency.
3. A necessary runtime path truly requires mutating a resolved TaskSpec after
   validation and there is no small copy-based alternative.
4. The implementer finds themselves designing multiple new public model layers
   instead of hardening the existing boundary.

If none of those become true, this slice should be implemented exactly as
written.

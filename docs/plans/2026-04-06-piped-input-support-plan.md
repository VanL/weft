# Piped Input Support Plan

Status: proposed
Source specs: see Source Documents below
Superseded by: none

This document is the implementation plan for adding **consistent piped input
support** to Weft without breaking the queue-first execution model.

Assume the implementing engineer:

- is strong in Python,
- knows almost nothing about Weft internals,
- will over-abstract if the plan leaves room for it,
- will over-mock tests unless the real harness is named explicitly,
- and may drift into a bigger CLI/runtime redesign if boundaries are not
  stated up front.

This plan is written to prevent those failures.

## Goal

Add piped-input support in the places where it is currently missing or
inconsistent, while preserving Weft's existing execution spine:

`CLI -> spawn request -> Manager -> T{tid}.inbox -> Consumer`

Concretely, this slice should make omitted-message stdin work for
`weft queue write` and `weft queue broadcast`, and should let `weft run`
consume piped stdin not only for inline commands, but also for `--spec` and
`--pipeline` in a way that remains durable, queue-backed, and easy to reason
about.

## Source Documents

Primary specs and docs:

- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
- [`docs/specifications/08-Testing_Strategy.md`](../specifications/08-Testing_Strategy.md)
- [`docs/specifications/00-Overview_and_Architecture.md`](../specifications/00-Overview_and_Architecture.md)
- [`README.md`](../../README.md)

Repo context and runbooks:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/runtime-and-context-patterns.md`](../agent-context/runbooks/runtime-and-context-patterns.md)
- [`docs/agent-context/runbooks/testing-patterns.md`](../agent-context/runbooks/testing-patterns.md) if you need extra test guidance

Current implementation paths:

- [`weft/_constants.py`](../../weft/_constants.py)
- [`weft/context.py`](../../weft/context.py)
- [`weft/cli.py`](../../weft/cli.py)
- [`weft/commands/queue.py`](../../weft/commands/queue.py)
- [`weft/commands/run.py`](../../weft/commands/run.py)
- [`weft/core/manager.py`](../../weft/core/manager.py)
- [`weft/core/targets.py`](../../weft/core/targets.py)

Current tests and harnesses:

- [`tests/cli/test_cli_queue.py`](../../tests/cli/test_cli_queue.py)
- [`tests/cli/test_cli_run.py`](../../tests/cli/test_cli_run.py)
- [`tests/cli/test_cli_pipeline.py`](../../tests/cli/test_cli_pipeline.py)
- [`tests/conftest.py`](../../tests/conftest.py)
- [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py)

Reference implementation for CLI stdin UX:

- SimpleBroker's current stdin helper behavior is the **reference UX**:
  omitted message argument means "read from piped stdin"; omitted message on a
  TTY fails fast with a clear error.
- Do **not** import SimpleBroker private helpers into Weft.
- If you want to inspect the current installed source without assuming a local
  checkout path, locate it first:

```bash
uv run python - <<'PY'
import inspect
import simplebroker.commands

print(inspect.getsourcefile(simplebroker.commands))
PY
```

Read the resolved file, especially the stdin helper and its tests, as a
behavior reference only.

## Scope Lock

Implement exactly these outcomes:

1. `weft queue write <queue>` accepts piped stdin when the message argument is
   omitted.
2. `weft queue broadcast` accepts piped stdin when the message argument is
   omitted.
3. `weft run` accepts piped stdin for:
   - inline command targets,
   - inline function targets,
   - `--spec` task execution,
   - `--pipeline` execution when `--input` is not provided.
4. `weft run --pipeline ... --input ...` and piped stdin at the same time are
   rejected as an ambiguous double input source.
5. `weft run` reads piped stdin with **streaming size enforcement** instead of
   slurping all input into memory first.
6. Docs and examples match the implemented behavior.

Do **not** implement any of the following in this slice:

- a new `--stdin` or `--from-stdin` CLI flag,
- a live Unix pipe forwarded into child processes after task launch,
- a second execution path that bypasses the Manager or inbox queue,
- stdin chunking into multiple inbox messages,
- a new TaskSpec schema,
- a new queue type,
- a new persistence model for stdin,
- auto-JSON parsing of piped stdin for `weft run`,
- a generic stdin utility framework for the whole repo,
- or a broader pipeline redesign.

If implementation pressure starts pulling toward any of those, stop. That is
scope drift.

## Context and Key Files

This section is your orientation map. Read it before editing.

### Execution Spine

The canonical Weft run path is:

1. CLI builds or loads a TaskSpec template.
2. CLI enqueues a spawn request on `weft.spawn.requests`.
3. Manager expands the TaskSpec, assigns the spawn-request message ID as the
   task TID, and seeds `T{tid}.inbox`.
4. Consumer processes inbox messages via reserve semantics.

The relevant code lives in:

- [`weft/commands/run.py`](../../weft/commands/run.py)
- [`weft/core/manager.py`](../../weft/core/manager.py)
- [`weft/core/tasks/consumer.py`](../../weft/core/tasks/consumer.py)

Shared path that must be reused:

- [`weft/commands/run.py`](../../weft/commands/run.py) `_enqueue_taskspec`
  already carries an `inbox_message` through the spawn request.
- [`weft/core/manager.py`](../../weft/core/manager.py) already seeds the child
  inbox from `inbox_message`.

Do **not** invent a parallel path that writes directly into the child process
stdin from the CLI.

### Queue Passthrough Surface

`weft queue ...` is intentionally a thin wrapper around SimpleBroker commands.

Relevant files:

- [`weft/cli.py`](../../weft/cli.py)
- [`weft/commands/queue.py`](../../weft/commands/queue.py)

Shared path that must be reused:

- `weft/commands/queue.py` delegates to `simplebroker.commands`.

Do **not** reimplement SimpleBroker's stdin parsing rules in Weft queue
commands. Fix the wrapper so it forwards arguments correctly.

### Existing Run-Mode Split

`weft run` currently has three main entry paths:

- inline execution in `_run_inline`
- spec-file execution in `_run_spec_via_manager`
- pipeline execution in `_run_pipeline`

Relevant file:

- [`weft/commands/run.py`](../../weft/commands/run.py)

Current gap:

- stdin is read only in `_run_inline`
- `--spec` ignores piped stdin
- `--pipeline` only accepts `--input`, not piped stdin

Current non-goal:

- do not expand `--spec` name resolution in this slice; the current CLI path is
  file-based, and piped-input support should not be bundled with broader spec
  lookup changes

### Test Harnesses

Use the real test harnesses. Do not invent mocks for broker/process behavior.

Relevant files:

- [`tests/conftest.py`](../../tests/conftest.py)
- [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py)

Use:

- `run_cli(...)` for end-to-end CLI subprocess tests
- `WeftTestHarness` for manager and lifecycle behavior
- `broker_env` only when you are explicitly testing queue semantics outside the
  full CLI path

Do **not** mock:

- `simplebroker.Queue`
- the Manager spawn path
- the Consumer run path
- or subprocess behavior for core stdin routing

## Fixed Design Decisions

These decisions are not open during implementation.

### 1. Piped Input Is CLI Sugar for Queue-Seeding

Piped input is not a new transport. It is just one more way for the CLI to
produce the **initial inbox payload** that flows through the existing spawn
request and inbox queues.

That means:

- `weft run` still goes through the Manager.
- The initial payload still becomes `inbox_message` in the spawn request.
- The Manager still writes that payload into `T{tid}.inbox`.

Do **not** stream shell stdin directly into the child process outside the queue
model.

### 2. Keep `weft run` Piped Stdin as Text

Piped stdin should remain raw text.

- Do not call `_parse_cli_value(...)` on piped stdin.
- Do not infer JSON because the bytes happen to look like JSON.
- `--input` remains the explicit structured-input path for pipelines.

Reason:

- Unix pipes are text/byte streams, not typed JSON channels.
- Silent JSON parsing would create surprising behavior and a second input
  contract.

### 3. Pipelines Must Have One Initial Input Source

For pipelines:

- if `--input` is provided, use it and keep the current `_parse_cli_value(...)`
  behavior,
- if piped stdin is present and `--input` is absent, use piped stdin as the
  initial stage input,
- if both are present, fail fast with `typer.BadParameter`.

Do not silently prefer one source over the other.

### 4. Reuse One Work-Payload Builder

There should be **one** helper that maps:

- target type,
- interactive mode,
- and raw piped stdin text

into the initial inbox payload shape.

Current desired mapping:

- command, non-interactive: `{"stdin": text}`
- command, interactive: `{"stdin": text, "close": True}`
- function: raw string payload
- agent: raw string payload

Do not duplicate this logic separately across `_run_inline`,
`_run_spec_via_manager`, and `_run_pipeline`.

### 5. Preserve the Current Spawn-Request Contract in This Slice

Do not redesign the spawn-request payload format.

Current contract:

- spawn request contains `taskspec`
- spawn request may contain `inbox_message`

That is sufficient for this feature. No Manager refactor should be necessary.

### 6. Preserve Current Empty-stdin Behavior for `weft run`

Current `weft run` behavior treats empty non-TTY stdin as "no initial input."
Preserve that behavior in this slice unless a spec change explicitly says
otherwise.

This keeps the feature aligned with current behavior rather than smuggling in a
second semantic change.

## Invariants and Constraints

These are mandatory.

### Queue and Task Invariants

Protect these from [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md):

- **IMMUT.1 / IMMUT.2 / IMMUT.3**: do not mutate resolved `spec`, `io`, or
  `tid`
- **STATE.1 / STATE.2**: no backward or post-terminal state transitions
- **QUEUE.1 / QUEUE.2 / QUEUE.3**: required queue layout and `T{tid}.` naming
- **QUEUE.4 / QUEUE.5 / QUEUE.6**: preserve exactly-once move semantics and
  reserved queue policy
- **MANAGER.4**: spawn-request message ID remains the child task TID
- **IMPL.5 / IMPL.6**: keep spawn-based process boundaries and recreate broker
  connections in child processes

### Message-Flow Invariants

Protect these from [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md):

- task submission still goes through `weft.spawn.requests`
- Manager still seeds the child inbox
- work still flows `inbox -> reserved -> outbox`
- result availability still follows the current completion-event/outbox grace
  model

Do not change:

- `weft.log.tasks` semantics
- reserved queue cleanup policy
- or task completion timing assumptions

### Size Limit Constraint

SimpleBroker messages are size-limited. This matters because `weft run`
currently embeds the initial inbox payload in the spawn request, which is
itself a broker message.

Implementation rule:

- read piped stdin incrementally
- enforce the max-size limit **while reading**
- and fail before enqueue if the input exceeds the configured broker limit

Configuration note:

- Weft already maps `WEFT_MAX_MESSAGE_SIZE` to `BROKER_MAX_MESSAGE_SIZE` in
  [`weft/_constants.py`](../../weft/_constants.py).
- Read the effective limit from resolved Weft/SimpleBroker config, not from a
  hard-coded constant and not from ad hoc environment lookups sprinkled across
  multiple call sites.

Do not read unbounded stdin into memory and then check the size afterwards.

### DRY / YAGNI Constraints

- Do not add a new generic utility module unless a second real caller exists in
  this slice.
- Do not duplicate stdin parsing logic across run modes.
- Do not duplicate SimpleBroker queue-command logic in Weft.
- Do not build a future-proof stdin router for hypothetical later features.

## Tasks

Implement these in order. Each task is intentionally small.

### 1. Lock the queue passthrough behavior to SimpleBroker parity

Files to modify:

- [`weft/commands/queue.py`](../../weft/commands/queue.py)
- [`weft/cli.py`](../../weft/cli.py)
- [`tests/cli/test_cli_queue.py`](../../tests/cli/test_cli_queue.py)
- optional narrow adapter test file if needed, for example
  `tests/commands/test_queue_passthrough.py`

Read first:

- [`weft/commands/queue.py`](../../weft/commands/queue.py)
- [`weft/cli.py`](../../weft/cli.py)
- [`tests/cli/test_cli_queue.py`](../../tests/cli/test_cli_queue.py)
- current SimpleBroker `write` / `broadcast` CLI behavior

Shared path — do not duplicate:

- `weft/commands/queue.py` should keep delegating to `simplebroker.commands`

Approach:

1. Start with one narrow wrapper-boundary test that proves omitted `message`
   is forwarded to SimpleBroker as `None`, not rewritten to `"-"`.
2. Add an end-to-end CLI regression test that proves omitted-message piped
   stdin works for `weft queue write`.
3. Add an end-to-end CLI regression test that proves omitted-message piped
   stdin works for `weft queue broadcast` with a narrow `--pattern`.
4. Update the wrapper so omitted `message` is forwarded as `None`, not
   rewritten to `"-"`.
5. Update the help text to say "omit or use `-` for stdin" anywhere Weft
   exposes this behavior.

Important constraint:

- Do not add Weft-specific TTY detection here. The upstream SimpleBroker
  command owns that behavior already.

Testing notes:

- For `broadcast`, pre-create target queues and use `--pattern` so the test
  does not depend on unrelated runtime queues.
- Keep the real CLI subprocess tests. Do not replace them with mock-only
  tests.
- The wrapper-boundary test is required here because the real omitted-stdin
  CLI path may already succeed when Weft rewrites `None` to `"-"`, which would
  hide the actual parity bug.
- Keep that wrapper-boundary test tiny. It is testing Weft's delegation
  contract, not broker behavior.

### 2. Replace `weft run`'s eager stdin read with a chunked helper

Files to modify:

- [`weft/commands/run.py`](../../weft/commands/run.py)
- [`tests/cli/test_cli_run.py`](../../tests/cli/test_cli_run.py)

Read first:

- [`weft/commands/run.py`](../../weft/commands/run.py)
- current SimpleBroker stdin helper implementation
- [`tests/conftest.py`](../../tests/conftest.py)

Shared path — do not duplicate:

- `build_context(...)` already gives you broker config
- `run.py` should own its own local stdin helper for this slice

Approach:

1. Replace the current `_read_stdin()` helper with a chunked, size-enforcing
   version.
2. Keep the helper private to `weft/commands/run.py`.
3. The helper should:
   - return `None` when stdin is absent, closed, or a TTY,
   - read non-TTY stdin in chunks,
   - enforce the configured broker max-message size during the read,
   - return `None` for empty input,
   - and raise a CLI-facing error for oversized input.
4. Derive the limit from the active Weft/SimpleBroker config, not a duplicated
   magic number.
5. Do not read environment variables directly in multiple places; resolve the
   limit once from context/config and pass it into the helper.
6. Do not call the helper before you have a resolved `WeftContext`; the point
   is to honor the active broker configuration for the current project.
7. Do not hoist stdin reading to `cmd_run(...)` before mode selection. Read it
   inside the resolved execution path after the target mode and context are
   known.

Testing notes:

- Add a real CLI test that lowers the effective max message size via env and
  proves oversized piped stdin fails before task submission.
- Keep the test cheap: set the limit low, not the input huge.
- Do not over-mock `sys.stdin` for the primary behavior; the CLI subprocess
  path is the behavior that matters.

### 3. Reuse one initial-work-payload builder across inline and spec runs

Files to modify:

- [`weft/commands/run.py`](../../weft/commands/run.py)
- [`tests/cli/test_cli_run.py`](../../tests/cli/test_cli_run.py)

Read first:

- `_initial_work_payload` in [`weft/commands/run.py`](../../weft/commands/run.py)
- `_run_inline` in [`weft/commands/run.py`](../../weft/commands/run.py)
- `_run_spec_via_manager` in [`weft/commands/run.py`](../../weft/commands/run.py)
- [`weft/core/manager.py`](../../weft/core/manager.py)

Shared path — do not duplicate:

- `_enqueue_taskspec(...)` already forwards `inbox_message`
- Manager already seeds child inboxes from `inbox_message`
- [`weft/core/targets.py`](../../weft/core/targets.py) already accepts the
  current dict-vs-string work-item shapes; do not redesign target execution in
  this slice

Approach:

1. Refactor the initial payload logic into a single helper that can be used by
   both inline and `--spec` paths.
2. The helper should take:
   - target type,
   - interactive mode,
   - and raw stdin text
3. Keep the current payload mapping:
   - command: dict with `stdin`
   - interactive command: dict with `stdin` and `close=True`
   - non-command targets: raw string
4. Update `_run_inline` to use the shared helper.
5. Update `_run_spec_via_manager` to:
   - read piped stdin using the new chunked helper,
   - derive target type and interactive mode from the loaded TaskSpec,
   - pass the resulting payload into `_enqueue_taskspec(...)`
   - and otherwise leave the Manager path unchanged.

Important constraints:

- Do not mutate the loaded TaskSpec to "store" stdin.
- Do not touch `weft/core/manager.py` unless a test proves the existing
  `inbox_message` contract is insufficient.
- Do not touch `weft/core/targets.py` unless a failing test proves the existing
  string/dict work-item handling is insufficient.
- Do not add a new helper in `weft/helpers.py` just to make this look reusable.
  In this slice, private helper(s) in `run.py` are enough.

Required tests:

1. Spec path, function target:
   - create a minimal file-backed spec that calls
     `tests.tasks.sample_targets:echo_payload`
   - run `weft run --spec ...` with piped stdin
   - assert the piped text becomes the function payload
2. Spec path, command target:
   - create a small script that does `sys.stdin.read()` and prints a transformed
     value
   - mirror the existing inline stdin test pattern in
     [`tests/cli/test_cli_run.py`](../../tests/cli/test_cli_run.py) rather than
     inventing a new command shape
   - run it through `--spec` with piped stdin
   - assert stdin reached the subprocess
3. Persistent spec, `--no-wait`, initial inbox seed:
   - reuse the persistent agent-spec style from existing tests
   - run `weft run --spec <file> --no-wait` with piped stdin
   - then use `weft result TID` to prove the initial inbox work item was
     delivered without writing manually into the inbox queue afterwards

Why the persistent test matters:

- it proves the new stdin behavior works for the "seed initial inbox and return"
  path, not only for one-shot wait flows.

### 4. Extend pipeline execution to accept piped stdin safely

Files to modify:

- [`weft/commands/run.py`](../../weft/commands/run.py)
- [`tests/cli/test_cli_pipeline.py`](../../tests/cli/test_cli_pipeline.py)

Read first:

- `_run_pipeline` in [`weft/commands/run.py`](../../weft/commands/run.py)
- [`tests/cli/test_cli_pipeline.py`](../../tests/cli/test_cli_pipeline.py)

Shared path — do not duplicate:

- `_run_pipeline` already owns initial stage input resolution

Approach:

1. Read piped stdin in `_run_pipeline` using the same chunked helper from Task 2.
2. Preserve current `--input` behavior exactly:
   - it still flows through `_parse_cli_value(...)`
3. Add piped stdin as the fallback initial stage input only when `--input` is
   absent.
4. Reject simultaneous `--input` and piped stdin with `typer.BadParameter`.
5. Do not auto-parse piped stdin as JSON.

Required tests:

1. Existing sequential pipeline test should remain green.
2. Add a new pipeline test where stdin is piped and `--input` is absent.
3. Add a new pipeline test where both `--input` and piped stdin are supplied
   and assert a clear CLI error.

Important constraint:

- Do not redesign pipelines to support `--no-wait` or streaming stdin in this
  slice.

### 5. Update docs to match the actual behavior

Files to modify:

- [`README.md`](../../README.md)
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)

Read first:

- the current docs above
- final implementation and tests

Approach:

1. Fix the CLI examples and wording so they match reality.
2. Update the queue command help/docs to reflect "omit or use `-` for stdin."
3. Update the `weft run` docs to state:
   - inline runs accept piped stdin,
   - `--spec` accepts piped stdin as the initial payload,
   - `--pipeline` accepts piped stdin only when `--input` is absent,
   - piped stdin is text, not auto-JSON-parsed.
4. Fix the misleading CLI example:
   - `echo "data" | weft run process -`
   - should not be the canonical Weft example unless the child command itself
     requires `-` as an argv convention.
5. Broaden the TaskSpec `io.inputs.inbox` wording so it describes initial
   input/stdin payloads, not only long-running tasks.
6. If you touch message-flow docs, describe piped stdin as feeding the existing
   spawn-request `inbox_message`, not as a new runtime path.

Important constraint:

- docs must describe the implementation you shipped, not a bigger future design.

## Testing Plan

Use red-green TDD. Write the smallest failing test for each task first.

### Preferred test files

- queue passthrough:
  - [`tests/cli/test_cli_queue.py`](../../tests/cli/test_cli_queue.py)
- run/spec behavior:
  - [`tests/cli/test_cli_run.py`](../../tests/cli/test_cli_run.py)
- pipeline behavior:
  - [`tests/cli/test_cli_pipeline.py`](../../tests/cli/test_cli_pipeline.py)

### Harness and fixture rules

- Use `run_cli(...)` for CLI behavior.
- Use `weft_harness` for manager-backed run flows.
- Use real task spec files in temporary workdirs.
- Use the existing sample target functions and tiny temporary Python scripts
  rather than mocks.
- Remember that `run_cli(...)` strips leading/trailing whitespace from captured
  stdout/stderr. For exact-output assertions, use payloads without leading or
  trailing whitespace unless the test is explicitly about internal newlines.

### Required behavior assertions

Queue passthrough:

1. `weft queue write <queue>` with piped stdin and omitted message writes the
   piped text.
2. `weft queue broadcast --pattern ...` with piped stdin and omitted message
   delivers the piped text to matching queues.
3. The queue passthrough wrapper forwards omitted `message` as `None`, not
   `"-"`.

Run path:

1. Existing inline command stdin test stays green.
2. Oversized piped stdin fails fast under a lowered configured size limit.
3. `--spec` function target receives piped stdin as its payload.
4. `--spec` command target receives piped stdin on subprocess stdin.
5. persistent `--spec --no-wait` receives the initial inbox seed from piped
   stdin.

Pipeline path:

1. `--pipeline` accepts piped stdin when `--input` is absent.
2. `--pipeline` rejects both `--input` and piped stdin.
3. existing `--input` behavior remains unchanged.

### Test-design warnings

- Do not patch `simplebroker.Queue`.
- Do not patch Manager message handling just to force stdin through.
- Do not write tests that assert on private local variables inside `run.py`.
- Do not use fixed sleeps when `WeftTestHarness` and existing CLI/result flows
  already provide observable completion paths.
- If a test needs the task result, use the CLI result path or harness helper
  rather than probing private process state.

### One narrow wrapper exception

For the queue passthrough adapter only, one tiny wrapper-boundary test is
appropriate and required. The real CLI omitted-stdin path can still succeed
even when Weft rewrites `None` to `"-"`, so the adapter contract needs its own
small assertion.

Keep that exception narrow:

- it applies to the queue passthrough wrapper only,
- it should assert on forwarded arguments, not broker internals,
- and it must sit alongside the real end-to-end CLI tests rather than replace
  them.

## Verification

Run these commands in sequence before calling the work done:

```bash
uv run pytest tests/cli/test_cli_queue.py -q
uv run pytest tests/cli/test_cli_run.py tests/cli/test_cli_pipeline.py -q
uv run pytest
uv run pytest -m ""
uv run ruff check weft tests
uv run mypy weft
```

Success criteria:

- all new CLI tests pass,
- existing stdin-related tests still pass,
- no spec/run/queue regression appears in the full suite,
- lint is clean,
- mypy is clean,
- docs match the shipped behavior.

## Out of Scope

These are explicitly not part of this plan:

- live streaming shell stdin into already-running child processes outside the
  queue model
- a new `--stdin` CLI option
- stdin chunking into multiple task inbox messages
- structured parsing of piped stdin for `weft run`
- automatic pipeline stdin streaming across stages
- Manager protocol redesign
- TaskSpec schema changes
- queue persistence/export changes
- or a general cleanup/refactor unrelated to piped input

## Final Review Checklist for the Implementer

Before opening a PR or handing off:

1. Did you keep piped input inside the existing spawn-request/inbox path?
2. Did you avoid touching `weft/core/manager.py` unless absolutely necessary?
3. Is there exactly one stdin-to-work-payload mapping helper in `run.py`?
4. Did you keep piped stdin raw text instead of auto-JSON parsing it?
5. Does pipeline execution reject both `--input` and piped stdin together?
6. Did you use real CLI tests with `run_cli(...)` and `WeftTestHarness`?
7. Did you avoid queue/process mocks for core behavior?
8. Did you update the docs and examples that now would otherwise lie?

If any answer is "no", the slice is not done.

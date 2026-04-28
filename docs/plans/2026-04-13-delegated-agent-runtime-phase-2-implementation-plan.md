# Delegated Agent Runtime Phase 2 Implementation Plan

Status: completed
Source specs: see Source Documents below
Superseded by: none

This plan implements Phase 2 from
[`docs/specifications/13A-Agent_Runtime_Planned.md`](../specifications/13A-Agent_Runtime_Planned.md):
task-lifetime interactive delegated sessions for `provider_cli` while keeping
Weft as the system of record for task lifecycle, queues, control, and durable
audit state.

This is risky boundary work. It touches the durable spine:

`TaskSpec -> Manager -> Consumer -> TaskRunner -> runner -> agent runtime/session`

The wrong implementation path is easy to imagine:

- turning this into a second chat system,
- adding a new public queue or CLI protocol,
- tunneling agent work through `spec.interactive`,
- scraping provider TUIs instead of using real non-interactive continuation
  surfaces,
- relying on ambient provider session history in the user's home directory,
- or mocking away the real subprocess and queue path.

Do not do those things.

## Scope Lock

Implement exactly this slice:

1. keep the runtime literal as `spec.agent.runtime="provider_cli"`
2. allow `provider_cli` persistent sessions only for
   `spec.persistent=true` plus
   `spec.agent.conversation_scope="per_task"`
3. keep one-shot `provider_cli` behavior unchanged
4. reuse the existing persistent agent-session path:
   `Consumer._uses_agent_session()` ->
   `TaskRunner.start_agent_session()` ->
   `HostTaskRunner.start_agent_session()` ->
   `_agent_session_worker_entry()` ->
   `start_agent_runtime_session()`
5. implement `provider_cli.start_session()` using provider-native
   continuation surfaces, not a provider TUI or PTY automation layer
6. keep the public inbox and outbox contract unchanged
7. keep public session identity Weft-native: the persistent task TID is the
   session identifier for this phase
8. target the provider types already in Phase 1:
   `claude_code`, `codex`, `gemini`, `opencode`, and `qwen`
9. add fixture-backed and opt-in live local tests for persistent continuation
   through the real Weft session path

Target means:

- Phase 2 should aim to land deterministic persistent support for all five
  providers
- if one provider cannot meet the contract without a TUI or PTY hack, do not
  fake support; leave that provider one-shot-only with an explicit validation
  error and split the provider-specific work into a follow-on plan

Do not implement any of the following in this slice:

- Weft-owned durable threads or transcript replay
- restart-durable continuation after the task/session process dies
- MCP bridging
- shell/file tool surfacing beyond what a provider CLI already exposes on its
  own
- `spec.agent.tools` support for `provider_cli`
- build-backed containers or new runner environments
- an attach UI, a chat REPL, or a second public CLI family for agent sessions
- `spec.interactive=true` for agents
- a generic provider-plugin framework
- parsing or persisting raw provider transcripts as canonical truth

If implementation pressure starts pulling toward any item in the exclusion
list, stop and write a follow-on plan instead.

## Goal

Add persistent `provider_cli` sessions that let a single persistent Weft agent
task continue a delegated provider conversation across multiple inbox work
items for the life of that task, while preserving the existing queue model,
reserved-queue policy, timeout and cancellation semantics, public outbox
contract, and Weft-owned audit trail.

## Source Documents

Primary specs:

- [`docs/specifications/13A-Agent_Runtime_Planned.md`](../specifications/13A-Agent_Runtime_Planned.md)
  [AR-A1], [AR-A2], [AR-A3], [AR-A7]
- [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
  [AR-0.1], [AR-2.1], [AR-5], [AR-6], [AR-7], [AR-9]
- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
  [TS-1], [TS-1.3], [TS-1.4]
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-2], [MF-3], [MF-5], [MF-6]
- [`docs/specifications/06-Resource_Management.md`](../specifications/06-Resource_Management.md)
  [RM-5], [RM-5.1]
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
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)
- [`docs/plans/2026-04-13-delegated-agent-runtime-phase-1-implementation-plan.md`](./2026-04-13-delegated-agent-runtime-phase-1-implementation-plan.md)
- [`docs/plans/2026-04-06-persistent-agent-runtime-implementation-plan.md`](./2026-04-06-persistent-agent-runtime-implementation-plan.md)

Provider reference material:

- [`../agent-mcp/README.md`](../../../agent-mcp/README.md)
- [`../agent-mcp/src/server.ts`](../../../agent-mcp/src/server.ts)

The `agent-mcp` references are not the design authority for Weft. They are
only concrete references for real provider CLI flags and provider-local state
handling ideas. Do not copy `agent-mcp` job orchestration into Weft.

## Context and Key Files

Files that will likely change in this slice:

- `docs/specifications/13-Agent_Runtime.md`
- `docs/specifications/13A-Agent_Runtime_Planned.md`
- `docs/specifications/02-TaskSpec.md`
- `docs/specifications/10-CLI_Interface.md`
- `weft/core/taskspec.py`
- `weft/core/agent_validation.py`
- `weft/core/agents/provider_registry.py`
- `weft/core/agents/backends/provider_cli_backend.py`
- `weft/core/runners/host.py`
- `tests/taskspec/test_taskspec.py`
- `tests/core/test_agent_validation.py`
- `tests/core/test_provider_cli_backend.py`
- recommended new focused test:
  - `tests/core/test_provider_cli_session_backend.py`
- `tests/tasks/test_agent_execution.py`
- `tests/core/test_manager.py`
- `tests/cli/test_cli_validate.py`
- `tests/fixtures/provider_cli_fixture.py`

Files that should usually not change in this slice:

- `weft/core/tasks/consumer.py`
- `weft/core/tasks/runner.py`
- `weft/core/tasks/sessions.py`
- `weft/core/tasks/agent_session_protocol.py`
- `weft/ext.py`
- `weft/core/tasks/interactive.py`
- `weft/core/manager.py`

Touch those only if a red test proves a real gap. Most of the generic session
machinery already exists.

Read first:

- `weft/core/taskspec.py`
  - owns the current validation rule that blanket-rejects persistent
    `provider_cli`
- `weft/core/agent_validation.py`
  - owns runtime load/preflight validation for `weft spec validate` and
    `TaskRunner`
- `weft/core/agents/provider_registry.py`
  - owns provider names, CLI resolution, option validation, and one-shot
    invocation assembly today
- `weft/core/agents/backends/provider_cli_backend.py`
  - owns prompt composition, resolver/tool-profile loading, and provider
    command dispatch today
- `weft/core/runners/host.py`
  - owns the spawned worker subprocesses and the dedicated agent-session
    subprocess path
- `weft/core/tasks/consumer.py`
  - owns the split between one-shot and per-task agent sessions and writes
    `work_item_completed`
- `weft/core/tasks/sessions.py`
  - owns the parent-side `AgentSession` object and timeout/cancellation polling
- `tests/tasks/test_agent_execution.py`
  - already proves one-shot and `llm` per-task behavior and includes Phase 1
    live provider smokes
- `tests/fixtures/provider_cli_fixture.py`
  - already simulates the provider matrix through real executable wrappers

Important current structure:

- `Consumer._uses_agent_session()` already decides whether a task uses the
  dedicated persistent agent-session path.
- `HostTaskRunner.start_agent_session()` already starts a dedicated session
  subprocess and wraps it in `AgentSession`.
- `provider_cli` currently has no `start_session()` implementation, so the
  generic per-task session path exists but is unavailable to this runtime.
- `_agent_session_worker_entry()` in `weft/core/runners/host.py` currently does
  **not** apply `_worker_runtime_context(spec_data)`. That means persistent
  agent-session workers do not inherit task `env` and `working_dir` today.
  This does not matter much for current `llm` tests, but it matters a lot for
  `provider_cli` Phase 2 and must be fixed.
- Manager idle timeout already treats persistent child tasks as active work and
  does not kill them. There are existing tests proving this. Do not rewrite the
  manager unless Phase 2 exposes a real regression.

Shared helpers and paths to reuse:

- `register_builtin_agent_runtimes()` in
  `weft/core/agents/backends/__init__.py`
- `start_agent_runtime_session()` in `weft/core/agent_runtime.py`
- `AgentSession` in `weft/core/tasks/sessions.py`
- `BaseTask._report_state_change()` for durable internal metadata
- `validate_taskspec_agent_runtime(...)` in `weft/core/agent_validation.py`
- `write_provider_cli_wrapper(...)` and `PROVIDER_FIXTURE_NAMES` in
  `tests/fixtures/provider_cli_fixture.py`
- the existing live-test gating environment variables from Phase 1:
  `WEFT_RUN_LIVE_PROVIDER_CLI_TESTS` and `WEFT_LIVE_PROVIDER_CLI_TARGETS`

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
13. [`weft/core/agent_validation.py`](../../weft/core/agent_validation.py)
14. [`weft/core/agent_runtime.py`](../../weft/core/agent_runtime.py)
15. [`weft/core/agents/provider_registry.py`](../../weft/core/agents/provider_registry.py)
16. [`weft/core/agents/backends/provider_cli_backend.py`](../../weft/core/agents/backends/provider_cli_backend.py)
17. [`weft/core/tasks/consumer.py`](../../weft/core/tasks/consumer.py)
18. [`weft/core/tasks/runner.py`](../../weft/core/tasks/runner.py)
19. [`weft/core/tasks/sessions.py`](../../weft/core/tasks/sessions.py)
20. [`weft/core/tasks/agent_session_protocol.py`](../../weft/core/tasks/agent_session_protocol.py)
21. [`weft/core/runners/host.py`](../../weft/core/runners/host.py)
22. [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py)
23. [`tests/core/test_agent_runtime.py`](../../tests/core/test_agent_runtime.py)
24. [`tests/core/test_provider_cli_backend.py`](../../tests/core/test_provider_cli_backend.py)
25. [`tests/tasks/test_agent_execution.py`](../../tests/tasks/test_agent_execution.py)
26. [`tests/core/test_manager.py`](../../tests/core/test_manager.py)
27. [`tests/cli/test_cli_validate.py`](../../tests/cli/test_cli_validate.py)
28. [`tests/fixtures/provider_cli_fixture.py`](../../tests/fixtures/provider_cli_fixture.py)
29. [`../agent-mcp/README.md`](../../../agent-mcp/README.md)
30. [`../agent-mcp/src/server.ts`](../../../agent-mcp/src/server.ts)

## Comprehension Questions

If the implementer cannot answer these before editing, they are not ready.

1. Which current code path decides whether an agent work item uses a dedicated
   session subprocess or a fresh worker subprocess?
   Answer: `_uses_agent_session()` and `_run_task()` in
   `weft/core/tasks/consumer.py`.

2. Where is the private protocol between the task parent and the session
   subprocess defined, and is it public?
   Answer: `weft/core/tasks/agent_session_protocol.py`. It is private and must
   not leak to inbox/outbox.

3. Which current function already applies task `env` and `working_dir` inside a
   spawned one-shot worker, and where is the gap for persistent sessions?
   Answer: `_worker_runtime_context()` in `weft/core/runners/host.py`.
   `_agent_session_worker_entry()` currently does not use it.

4. Which queue-visible event marks one inbox message finishing while a
   persistent task remains alive?
   Answer: `work_item_completed`.

5. Why is `spec.interactive` the wrong implementation path for this slice?
   Answer: it is the command-session feature in
   `weft/core/tasks/interactive.py`, not the agent runtime session path.

6. Which existing tests already prove the manager should not idle-shutdown while
   a persistent child exists?
   Answer: `test_manager_idle_timeout_does_not_kill_persistent_child` and
   related manager tests in `tests/core/test_manager.py`.

## Engineering Rules

These are not optional.

### 1. Style

- Use `from __future__ import annotations` in every new Python file.
- Keep imports at module top.
- Use `collections.abc` types.
- Use `Path`, not `os.path`.
- Keep names direct and boring.
- Add docstrings with spec references when behavior is load-bearing.
- Add comments only when the code would otherwise be hard to parse.

### 2. Complexity

- Do not invent a second orchestration model.
- Do not create a public "agent session attach" feature here.
- Do not add a provider plugin framework.
- Do not add a transcript store.
- Do not add provider-specific hacks to unrelated modules when the logic can
  live in `provider_registry.py` or `provider_cli_backend.py`.

### 3. DRY and YAGNI

- Reuse the current persistent agent-session spine.
- Reuse the Phase 1 provider registry and fixture matrix.
- Prefer a few small provider capability flags or helper methods over a large
  generic abstraction tower.
- Do not split code into more modules unless a file becomes hard to read for a
  real reason.

### 4. Testing

- Use red-green-refactor.
- Do not mock `Queue`, `Consumer`, `TaskRunner`, `HostTaskRunner`,
  `AgentSession`, `subprocess.run`, or provider registry functions.
- Use real broker-backed queues, real spawned worker/session subprocesses, and
  real executable wrapper fixtures.
- Add opt-in live local smokes that hit the real installed CLIs through the
  full Weft path. Do not put those in CI.
- Prefer one manager-backed proof over a large pile of patched unit tests.

### 5. Verification Discipline

- Run `pytest`, `ruff`, and `mypy` sequentially through the repo venv.
- Reuse the existing live-test env vars if possible. Do not invent a second
  env-flag family unless the existing gates truly cannot express the new smokes.

## Fixed Design Decisions

These decisions are not open during implementation.

### 1. The runtime stays `provider_cli`

Do not add `provider_cli_session`, `delegated_session`, or any other new
runtime literal for this slice.

Rationale:

- Phase 2 extends the existing delegated runtime
- the outer Weft session model already distinguishes one-shot from persistent
  work
- a second runtime literal would make TaskSpec noisier without buying clearer
  semantics

### 2. Public session identity is the task TID

Do not add a new public `thread_id`, `session_id`, or `conversation_id` field
to TaskSpec in this slice.

Use:

- the persistent task TID as the Weft-native session identifier
- provider-native session IDs only as optional internal metadata when cheaply
  available

Rationale:

- Phase 4 owns durable thread semantics
- Weft already has a durable, public, queue-visible identifier: `tid`
- a second public identity would create contract drift before the durable
  thread design exists

### 3. Reuse the existing persistent agent-session path

Do not invent a second session daemon or parent-side session loop.

Use the current path:

- `Consumer._uses_agent_session()`
- `TaskRunner.start_agent_session()`
- `HostTaskRunner.start_agent_session()`
- `_agent_session_worker_entry()`
- `start_agent_runtime_session()`

Rationale:

- this is already the canonical per-task agent continuation path
- it already carries timeout, cancellation, and worker PID/runtime handle
  semantics
- it keeps the durable spine single

### 4. The delegated session implementation uses provider-native continuation
surfaces, not a TUI/PTY scraper

The session backend may spawn one provider CLI process per turn inside the
dedicated session subprocess. It does **not** need to keep a provider TUI alive
for the whole task lifetime.

This is the intended shape:

- session subprocess stays alive for the task lifetime
- each turn renders a grounded prompt envelope
- the backend invokes the provider's real continue or resume surface
- provider-native session state is task-lifetime only

Rationale:

- the local CLIs already expose real continuation surfaces:
  `claude --continue/--resume`, `codex exec resume`, `gemini --resume`,
  `opencode run --continue/--session`, `qwen --continue/--resume`
- this is easier to cancel and test than scraping a live TUI
- it keeps the provider-specific complexity inside the provider registry

### 5. Provider continuation strategy is provider-owned and explicit

Do not branch on provider names inside `ProviderCLIBackend` to decide how
continuation works.

The provider registry must own session capability and continuation strategy.
Keep the strategy surface small. The likely minimum is:

- explicit session ID on each call, when the provider CLI supports it
- resume-latest in an isolated provider-local session store, when explicit
  session IDs do not exist
- structured session metadata capture only when a provider truly needs it

Do not add more strategies unless a real provider requires them.

### 6. `provider_cli` persistent support is narrow in Phase 2

Enable only:

- `spec.persistent=true`
- `spec.agent.runtime="provider_cli"`
- `spec.agent.conversation_scope="per_task"`

Keep rejecting:

- `provider_cli` + `spec.persistent=true` + `conversation_scope="per_message"`
- `provider_cli` + `spec.agent.tools`
- non-text output modes

Rationale:

- Phase 2 is about delegated continuation, not general persistent
  one-shot-like workers
- widening `persistent + per_message` is extra surface with no clear need

### 7. Every turn still re-resolves grounded context

Do not trust provider memory as the only grounding.

For every work item:

- run the resolver again
- run the tool profile again
- render a fresh prompt envelope
- then send that envelope through the provider's continuation path

Rationale:

- Weft remains the visible source of grounding
- this keeps the top lane aligned with the pyramid model
- it avoids hidden memory becoming the only truth lane

### 8. Session state is task-lifetime and cleanup is mandatory

If a provider needs isolated state to make continuation deterministic, that
state must live in a task-session-owned temp directory and be removed on close.

Do not:

- write session state into the repo
- depend on the user's ambient session history without an explicit reason
- leave temp directories behind silently

### 9. Manager changes are opt-in, not assumed

Do not rewrite `weft/core/manager.py` just because Phase 2 involves persistent
tasks. Existing manager tests already cover the core idle-timeout and persistent
child rules.

Only touch manager code if a new red test proves a real problem.

## Invariants and Review Gates

Preserve these invariants:

- TID format and immutability
- forward-only state transitions
- reserved-queue policy
- `spec` and `io` immutability after TaskSpec creation
- the existing public inbox and outbox contract
- the private agent-session protocol staying private
- `provider_cli` public output staying text-only in this phase
- normal `work_item_completed` boundaries for persistent tasks
- runner-owned timeout, cancellation, and process-isolation behavior

Hidden couplings to keep explicit:

- `_agent_session_worker_entry()` currently skips `_worker_runtime_context()`
- provider continuation semantics may depend on provider-local state directories
  or session IDs
- session failure must not leave a parent task thinking the session is still
  live
- cleanup failure for provider temp/session state is best-effort only and must
  not rewrite the core task outcome

Review gates:

- no new public queue message types
- no new public CLI family
- no new dependency
- no drive-by refactor
- no widening to MCP/tool-profile work outside the current resolver/profile
  references
- no mock-heavy substitute for a real broker/process proof

Stop and re-evaluate if:

- a provider requires a PTY/TUI hack to continue a session
- you need a second parent/session orchestration path
- you are about to add durable transcript persistence
- you are about to change `spec.interactive` semantics
- you cannot describe rollback cleanly

## Rollout and Rollback

Rollout notes:

- This slice is additive for existing one-shot `provider_cli` tasks.
- Stored TaskSpecs that already use one-shot `provider_cli` must keep working
  exactly as before.
- New persistent `provider_cli` TaskSpecs only become valid once this code and
  the updated specs ship together.

Rollback notes:

- Roll back the code and docs together.
- Any active persistent `provider_cli` task started under the new code should
  be drained or stopped before rollback if you care about the live session,
  because Phase 2 does not provide restart-durable continuation.
- If a provider-specific continuation strategy proves unstable, roll back that
  provider's persistent support and keep the rest one-shot rather than
  half-supporting a broken session path.

One-way doors:

- TaskSpec validation contract changes for persistent `provider_cli`
- canonical spec text in `13-Agent_Runtime.md`

Hold those to a higher review bar.

## Tasks

### 1. Lock the Phase 2 contract with failing TaskSpec and validation tests.

- Outcome:
  - the tests make the allowed and rejected TaskSpec shapes unambiguous before
    runtime work starts
- Files to touch:
  - `tests/taskspec/test_taskspec.py`
  - `tests/core/test_agent_validation.py`
  - `tests/cli/test_cli_validate.py`
  - if needed for shared fixture creation:
    - `tests/taskspec/fixtures.py`
- Read first:
  - `docs/specifications/13A-Agent_Runtime_Planned.md` [AR-A1], [AR-A2]
  - `docs/specifications/13-Agent_Runtime.md` [AR-2.1], [AR-6], [AR-7]
  - `weft/core/taskspec.py`
  - `weft/core/agent_validation.py`
- Reuse:
  - existing `create_valid_provider_cli_agent_taskspec(...)`
  - existing provider matrix in `tests/fixtures/provider_cli_fixture.py`
- Important fixture rule:
  - keep the existing one-shot `create_valid_provider_cli_agent_taskspec(...)`
    default behavior unchanged; add explicit persistent overrides instead of
    silently changing the helper's default shape
- Add these red tests first:
  - valid: `provider_cli` + `persistent=true` + `conversation_scope="per_task"`
    for each provider fixture when session support is available
  - invalid: `provider_cli` + `persistent=true` + `conversation_scope="per_message"`
  - invalid: `provider_cli` persistent task with unsupported provider session
    capability, if you intentionally gate a provider off
  - CLI validate `--load-runner` and `--preflight` surfaces the same runtime
    support decisions and still separates runner failures from agent-runtime
    failures
- Constraints:
  - do not add new public TaskSpec fields
  - do not silently allow `persistent + per_message`
- Stop if:
  - you are about to add a top-level `session_id`, `thread_id`, or `provider`
    field to `AgentSection`
- Done when:
  - the red tests fail for the right reason before code changes
  - the expected contract is explicit in test names and assertions
- Verify with:
  - `./.venv/bin/python -m pytest tests/taskspec/test_taskspec.py tests/core/test_agent_validation.py tests/cli/test_cli_validate.py -q`

### 2. Extend the provider fixture matrix to simulate persistent continuation for every provider.

- Outcome:
  - the test fixtures can prove multi-turn continuation deterministically across
    all provider types without mocking subprocesses
- Files to touch:
  - `tests/fixtures/provider_cli_fixture.py`
  - recommended new focused tests:
    - `tests/core/test_provider_cli_session_backend.py`
- Read first:
  - `tests/fixtures/provider_cli_fixture.py`
  - `tests/core/test_provider_cli_backend.py`
  - local CLI help surfaces you already inspected:
    `claude --help`, `codex exec resume --help`, `gemini --help`,
    `opencode run --help`, `qwen --help`
- Fixture behavior to add:
  - first-turn and continue-turn command parsing for each provider's real flag
    family
  - deterministic session state per provider
  - a stable way to assert turn continuity, such as directives like:
    `remember:<token>`, `recall`, `sleep:<seconds>`, `fail:<message>`
  - session-scoped payload fields in fixture output, for example:
    `continued`, `turn_index`, `session_key`, `provider`, `cwd`, `env_value`
- Constraints:
  - keep using real executable wrapper scripts
  - do not create a second provider fixture module
  - do not replace the Phase 1 one-shot helpers; extend them carefully
- Stop if:
  - the fixtures start requiring monkeypatching of `subprocess.run` or the
    provider backend
- Done when:
  - every provider in `PROVIDER_FIXTURE_NAMES` can exercise a deterministic
    two-turn remember/recall flow through its own emulated continue surface
- Verify with:
  - `./.venv/bin/python -m pytest tests/core/test_provider_cli_session_backend.py -q`

### 3. Extend the provider registry with explicit persistent-session capability and deterministic continuation strategies.

- Outcome:
  - provider-specific session support lives in `provider_registry.py`, not in
    backend `if provider == ...` branches
- Files to touch:
  - `weft/core/agents/provider_registry.py`
  - maybe `weft/core/agent_validation.py`
  - tests:
    - `tests/core/test_agent_validation.py`
    - `tests/core/test_provider_cli_session_backend.py`
- Read first:
  - `weft/core/agents/provider_registry.py`
  - `weft/core/agents/backends/provider_cli_backend.py`
  - `../agent-mcp/src/server.ts`
- Approach:
  - add a small provider-owned session-support contract
  - keep the contract boring and local to `provider_registry.py`
  - share one-shot and session invocation assembly where flags overlap
  - add provider preflight checks for persistent-session support when
    `conversation_scope="per_task"`
  - keep preflight local and deterministic; use help/version/flag probes only,
    not real networked model calls
  - if a provider needs provider-local session storage to make "resume latest"
    deterministic, make that an internal provider-owned detail
  - if a provider can use an explicit session ID, prefer that over scraping
    human-readable output
  - if a provider requires session metadata capture, prefer stable JSON or
    machine-readable output flags already exposed by the CLI
- Strong preference:
  - do not regex over human prose if a structured or explicit surface exists
- Constraints:
  - do not widen `weft/ext.py`
  - do not build a public plugin system
  - do not add more continuation strategies than the real provider matrix needs
  - do not copy all of `agent-mcp` minimal-mode machinery; port only the
    minimum provider-local state shaping needed for deterministic continuation
- Stop if:
  - continuation strategy logic starts spreading into `Consumer`,
    `TaskRunner`, or `HostTaskRunner`
- Done when:
  - validation can answer "does this provider support `per_task`
    continuation?" without touching backend execution code
  - provider-specific command assembly for persistent turns is centralized in
    the registry layer
- Verify with:
  - `./.venv/bin/python -m pytest tests/core/test_agent_validation.py tests/core/test_provider_cli_session_backend.py -q`

### 4. Implement `ProviderCLIBackend.start_session()` and the task-lifetime session object.

- Outcome:
  - `provider_cli` can create an `AgentRuntimeSession` that survives across
    multiple work items for the lifetime of the session subprocess
- Files to touch:
  - `weft/core/agents/backends/provider_cli_backend.py`
  - possibly `weft/core/agent_runtime.py` only for docstrings or typing
  - tests:
    - `tests/core/test_provider_cli_session_backend.py`
    - maybe `tests/core/test_agent_runtime.py`
- Read first:
  - `weft/core/agents/backends/provider_cli_backend.py`
  - `weft/core/agent_runtime.py`
  - `weft/core/agents/backends/llm_backend.py`
- Implementation rules:
  - the session object owns task-lifetime provider session state
  - it may spawn one provider CLI process per turn
  - it must clean up any temp/session directory on `close()`
  - it must re-run resolver/tool-profile composition every turn
  - it must return `AgentExecutionResult` with text output and internal
    metadata only
  - add useful internal metadata such as:
    `provider`, `continuation_mode`, `turn_index`, and optional provider-native
    session info when cheaply available
- Constraints:
  - no public result envelope
  - no transcript persistence
  - no `spec.agent.tools`
- Stop if:
  - you are about to expose provider session IDs as the public session key
  - you are about to cache rendered prompt history in Weft as a pseudo
    transcript
- Done when:
  - a focused backend test can create a `provider_cli` session, send two work
    items, get a deterministic remembered answer on the second turn, and see
    temp/session cleanup on close
- Verify with:
  - `./.venv/bin/python -m pytest tests/core/test_provider_cli_session_backend.py tests/core/test_agent_runtime.py -q`

### 5. Fix session-worker runtime context inheritance and keep the generic session spine intact.

- Outcome:
  - persistent agent-session workers see the same task `env` and
    `working_dir` that one-shot worker subprocesses already see
  - `provider_cli` per-task sessions work through the existing generic session
    path with minimal core changes
- Files to touch:
  - `weft/core/runners/host.py`
  - only if a red test proves it:
    - `weft/core/tasks/sessions.py`
    - `weft/core/tasks/agent_session_protocol.py`
    - `weft/core/tasks/consumer.py`
- Read first:
  - `weft/core/runners/host.py`
  - `weft/core/tasks/sessions.py`
  - `weft/core/tasks/consumer.py`
- Approach:
  - wrap `_agent_session_worker_entry()` in `_worker_runtime_context(spec_data)`
    for the life of the session worker
  - do not create a second environment-setup helper
  - keep the existing ready/result/stop private protocol unless a real gap
    appears
  - prove first that `Consumer` and `TaskRunner` already do the right thing
    once `provider_cli.start_session()` exists
- Constraints:
  - do not route provider sessions through `InteractiveTaskMixin`
  - do not change `RunnerCapabilities`
- Stop if:
  - the fix starts turning into a broad host-runner refactor
- Done when:
  - persistent provider-cli session tests can observe task `env` and
    `working_dir` inside the session worker
  - existing `llm` persistent-session tests still pass unchanged
- Verify with:
  - `./.venv/bin/python -m pytest tests/tasks/test_agent_execution.py -q -k 'persistent_agent_per_task or provider_cli'`

### 6. Add end-to-end Consumer and Manager proofs for multi-turn provider sessions, control, and cleanup.

- Outcome:
  - the real Weft execution path proves persistent delegated continuation,
    cancellation, timeout, reserved-queue behavior, and manager compatibility
- Files to touch:
  - `tests/tasks/test_agent_execution.py`
  - `tests/core/test_manager.py`
  - if a harness-based proof is clearer:
    - `tests/helpers/weft_harness.py` only for a small reusable poll helper
- Read first:
  - `tests/tasks/test_agent_execution.py`
  - `tests/core/test_manager.py`
  - `tests/helpers/weft_harness.py`
- Add these contract tests:
  - `Consumer` + persistent `provider_cli` + two work items -> second answer
    proves continuation, task stays `running`, events are
    `work_item_completed`
  - timeout on a persistent `provider_cli` turn -> existing timeout terminal
    semantics still apply and session is torn down
  - failure on a persistent `provider_cli` turn -> reserved-queue policy still
    applies and the session is not reused silently
  - manager-backed proof: manager spawns a persistent `provider_cli` child,
    the child handles at least two inbox work items, manager does not idle-stop
    while the child exists, STOP drains correctly
  - cleanup proof: provider session temp/state directories are cleaned on
    session close or task cleanup
- Constraints:
  - prefer direct `Consumer` and real manager tests over patches and mocks
  - keep at least one proof through the manager path, not only direct Consumer
  - the manager-backed proof must go through the manager's actual spawn request
    path; do not instantiate the child `Consumer` manually in a manager test
- Stop if:
  - you are about to add sleeps as the correctness mechanism instead of polling
    queues or state
- Done when:
  - the end-to-end tests fail before the code and pass after the code
  - manager behavior proves no new orchestration path was introduced
- Verify with:
  - `./.venv/bin/python -m pytest tests/tasks/test_agent_execution.py tests/core/test_manager.py -q`

### 7. Add opt-in live local smokes that hit the real installed provider CLIs through the full persistent-session path.

- Outcome:
  - local developers can prove at least some real CLIs continue a provider
    session through the full Weft stack
- Files to touch:
  - `tests/tasks/test_agent_execution.py`
  - maybe a tiny helper in `tests/fixtures/provider_cli_fixture.py` for prompt
    generation only
- Read first:
  - existing live smoke in `tests/tasks/test_agent_execution.py`
- Rules:
  - keep these tests local-only and opt-in
  - reuse `WEFT_RUN_LIVE_PROVIDER_CLI_TESTS` and
    `WEFT_LIVE_PROVIDER_CLI_TARGETS` unless there is a real blocker
  - do not assert brittle full responses; use strong prompt constraints and
    broad success checks
- Suggested live proof:
  - first turn: ask the provider to remember a unique token and acknowledge it
  - second turn: ask for the token only
  - assert the token appears in the second response
- Constraints:
  - do not require all providers in CI
  - do not treat a missing local auth setup as a code failure
- Stop if:
  - live proof requires manual patching or hidden environment setup beyond the
    provider's normal local auth/config
- Done when:
  - at least one real installed provider can pass the full persistent-session
    smoke locally
  - the test infrastructure makes it easy to extend that proof to other local
    providers
- Verify with examples:
  - `WEFT_RUN_LIVE_PROVIDER_CLI_TESTS=1 WEFT_LIVE_PROVIDER_CLI_TARGETS=codex ./.venv/bin/python -m pytest tests/tasks/test_agent_execution.py -q -m slow -k live_provider_cli_session_smoke`
  - `WEFT_RUN_LIVE_PROVIDER_CLI_TESTS=1 WEFT_LIVE_PROVIDER_CLI_TARGETS=opencode ./.venv/bin/python -m pytest tests/tasks/test_agent_execution.py -q -m slow -k live_provider_cli_session_smoke`

### 8. Update the canonical specs and backlinks only after the code and tests prove the behavior.

- Outcome:
  - the current spec corpus matches what shipped, and the planned spec points to
    the right implementation plan
- Files to touch:
  - `docs/specifications/13-Agent_Runtime.md`
  - `docs/specifications/13A-Agent_Runtime_Planned.md`
  - `docs/specifications/02-TaskSpec.md`
  - `docs/specifications/10-CLI_Interface.md`
  - `docs/plans/2026-04-13-delegated-agent-runtime-phase-2-implementation-plan.md`
- Update these sections explicitly:
  - `13-Agent_Runtime.md`
    - Current Support [AR-0.1]
    - Field Rules [AR-2.1]
    - Persistent Session Boundary [AR-6]
    - `provider_cli` backend subsection [AR-7]
    - Implementation Mapping [AR-9]
    - Related Plans
  - `13A-Agent_Runtime_Planned.md`
    - Phase 2 subsection
    - Related Plans
    - backlink note if Phase 2 is now shipped
  - `02-TaskSpec.md`
    - implementation coverage notes for persistent `provider_cli`
  - `10-CLI_Interface.md`
    - `weft spec validate` notes if the runtime-validation copy mentions
      one-shot-only `provider_cli`
- Constraints:
  - do not document unsupported providers as supported
  - do not quietly keep outdated "one-shot only" language once code ships
- Stop if:
  - the code and docs disagree about whether a provider supports persistent
    continuation
- Done when:
  - a zero-context reader can find the current behavior in the canonical spec
    without reading the plan

## Independent Review Checkpoints

Because this slice changes a reusable runtime contract, do not wait until the
very end.

Run independent review:

1. after Task 3, when the provider continuation strategy and validation shape
   are fixed
2. after Task 6, when the full subprocess and manager-backed proofs are green
3. again after Task 8, if the implementation drifted materially from the
   original plan

If only same-family review is available, record that limitation in the review
notes.

## Verification Commands

Run these sequentially. Do not run `uv`-managed commands in parallel.

1. `./.venv/bin/python -m pytest tests/taskspec/test_taskspec.py tests/core/test_agent_validation.py tests/cli/test_cli_validate.py -q`
2. `./.venv/bin/python -m pytest tests/core/test_provider_cli_backend.py tests/core/test_provider_cli_session_backend.py tests/core/test_agent_runtime.py -q`
3. `./.venv/bin/python -m pytest tests/tasks/test_agent_execution.py tests/core/test_manager.py -q`
4. `./.venv/bin/python -m ruff check weft tests`
5. `./.venv/bin/python -m mypy weft`
6. `./.venv/bin/python -m pytest -q`
7. optional local live proof:
   `WEFT_RUN_LIVE_PROVIDER_CLI_TESTS=1 WEFT_LIVE_PROVIDER_CLI_TARGETS=<provider-list> ./.venv/bin/python -m pytest tests/tasks/test_agent_execution.py -q -m slow -k live_provider_cli_session_smoke`

## Observable Success

This phase is done when all of the following are true:

- a persistent `provider_cli` TaskSpec with `conversation_scope="per_task"` is
  valid
- the same persistent task can handle multiple inbox work items and preserve
  delegated conversation state across them
- the task remains `running` between work items and emits
  `work_item_completed`
- timeout, cancellation, and reserved-queue error policy still work through the
  normal session path
- task `env` and `working_dir` are visible inside the provider session worker
- provider session state is task-lifetime only and is cleaned on close
- existing one-shot `provider_cli` behavior still passes unchanged
- existing `llm` persistent-session tests still pass unchanged
- at least one real installed provider can pass the local persistent-session
  smoke through the full Weft path

## Final Warning

There is one tempting bad shortcut in this phase: using the provider's
interactive TUI as the session runtime because it feels "more interactive."
That is the wrong bar here. It is harder to test, harder to cancel cleanly,
and more likely to drift into a second hidden control plane.

The right narrow shape is simpler:

- Weft owns the persistent task and session subprocess
- the provider registry owns continuation strategy
- the backend owns prompt composition and result normalization
- the provider CLI owns one turn at a time

If the implementation starts moving away from that shape, stop and re-plan.

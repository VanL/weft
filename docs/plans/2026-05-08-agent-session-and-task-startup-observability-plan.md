# Agent Session And Task Startup Observability Plan

Status: draft
Source specs: see Source Documents below
Superseded by: none

## 1. Goal

Improve Weft's ability to explain runner and session startup failures without
changing task lifecycle truth. The first concrete failure is Windows agent
session startup: the parent waits for a private ready message, receives no
ready or startup-error payload, and raises a generic error. The broader target
is the same class of opaque failure for all task types: command startup,
function/one-shot multiprocessing workers, one-shot agent workers, interactive
command sessions, and persistent agent sessions.

The intended shape is a shared, bounded runner diagnostic contract that can be
attached to task-log events and runner outcomes. It must help operators answer
"did the child process start, did the worker entrypoint run, did runtime
initialization start, did it fail, did it hang, and what process/exit evidence
exists?" It must not create a second state machine, a second queue truth, or a
new policy layer above the normal `TaskSpec -> Manager -> Consumer ->
TaskRunner -> queues/state log` spine.

## 2. Source Documents

Source specs:

- `docs/specifications/01-Core_Components.md` [CC-2.4], [CC-2.5], [CC-3],
  [CC-3.1], [CC-3.2], [CC-3.4]
- `docs/specifications/05-Message_Flow_and_State.md` [MF-2], [MF-3], [MF-5]
- `docs/specifications/06-Resource_Management.md` [RM-5], [RM-5.1],
  [RM-5.2]
- `docs/specifications/13-Agent_Runtime.md` [AR-5], [AR-6], [AR-7], [AR-9]
- `docs/specifications/07-System_Invariants.md` [STATE.1]-[STATE.6],
  [QUEUE.1]-[QUEUE.6], [OBS.9], [IMPL.5]-[IMPL.7]

Existing context:

- Windows CI failures on May 8, 2026:
  `tests/tasks/test_runner.py::test_task_runner_agent_session_continues_conversation`
  and
  `tests/tasks/test_runner.py::test_task_runner_agent_session_startup_uses_dedicated_ready_timeout`
  both failed because `AgentSession.wait_ready()` raised the generic
  `RuntimeError("Agent session failed to signal readiness")`.
- `docs/plans/2026-04-06-persistent-agent-runtime-implementation-plan.md`
  describes the shipped persistent agent runtime shape. It is historical
  context, not the active source of truth.
- `docs/plans/2026-04-07-active-control-main-thread-plan.md` is relevant
  because this plan must not reintroduce background broker work that competes
  with the main task thread.
- `docs/agent-context/engineering-principles.md` requires extending the
  existing durable spine, keeping queues as canonical state, and using real
  broker/process tests for lifecycle behavior.
- `docs/agent-context/runbooks/writing-plans.md`,
  `docs/agent-context/runbooks/hardening-plans.md`, and
  `docs/agent-context/runbooks/testing-patterns.md` define the planning,
  hardening, and verification bar.
- `docs/lessons.md` contains recurring lessons about process teardown,
  Windows CI timing, queue-read reliability, and real process tests.

Spec updates required during implementation:

- Update `docs/specifications/13-Agent_Runtime.md` [AR-6] to describe the
  private agent-session startup handshake phases and the parent-side diagnostic
  expectations.
- Update `docs/specifications/01-Core_Components.md` [CC-3] and [CC-3.4] to
  point at the shared runner diagnostic payload and clarify that it is
  observability data, not lifecycle truth.
- Update `docs/specifications/05-Message_Flow_and_State.md` [MF-5] to state
  where runner diagnostics may appear in `weft.log.tasks` and that status
  reconstruction must not derive lifecycle state from diagnostic-only events.
- Update `docs/specifications/06-Resource_Management.md` [RM-5.2] to keep the
  distinction between work-execution timeout and private startup/readiness
  budgets.

## 3. Context And Key Files

Read these files before editing:

- `weft/core/tasks/runner.py`
  - Facade between `Consumer` and runner backends.
  - Currently passes lifecycle hooks for worker PID, runtime handle, and stream
    chunks. This is the correct place to add a thin optional diagnostic hook if
    needed.
- `weft/core/runners/host.py`
  - Built-in host runner.
  - Owns one-shot command execution, one-shot function/agent multiprocessing
    workers, interactive command sessions, and persistent agent session worker
    startup.
  - Current problem point: `_agent_session_worker_entry()` sends `ready` or
    `startup_error`, while `HostTaskRunner.start_agent_session()` waits through
    `AgentSession.wait_ready()`.
- `weft/core/tasks/sessions.py`
  - Owns `AgentSession.wait_ready()`, `AgentSession.execute()`, and
    `CommandSession`.
  - Current weak point: generic readiness failure with no child PID, exit code,
    last handshake phase, or late startup-error drain.
- `weft/core/tasks/agent_session_protocol.py`
  - Private parent/agent-session-worker protocol.
  - May be extended because it is explicitly not a public queue surface.
- `weft/core/tasks/consumer.py`
  - Owns task-level execution, public lifecycle events, terminal state, result
    emission, reserved-message policy, and failure publication.
  - This is the right place to attach runner diagnostics to `work_failed`,
    `work_timeout`, `work_limit_violation`, and possibly non-terminal
    diagnostic log events. Do not publish lifecycle state from runner code
    directly.
- `weft/core/tasks/base.py`
  - Owns `_report_state_change()` and `weft.log.tasks` writes.
  - Use the existing state-log path. Do not open a separate diagnostic queue.
- `weft/core/runners/subprocess_runner.py`
  - Shared command-subprocess loop used by the host command runner and external
    subprocess-backed runners.
  - Good place for command-process start/exit diagnostics, but not for task
    lifecycle decisions.
- `weft/ext.py`
  - Runner plugin protocol. Be careful here: a required new method or required
    hook argument can break external runner plugins.
- `weft/_constants.py`
  - Any production constants, event names, field names, and size limits belong
    here. `tests/system/test_constants.py` enforces this.

Existing tests to read before editing:

- `tests/tasks/test_runner.py`
  - Existing `AgentSession` and `TaskRunner` tests, including the two Windows
    CI failures.
- `tests/tasks/test_task_execution.py`
  - Consumer execution and terminal event behavior.
- `tests/tasks/test_task_observability.py`
  - Existing task-log observability expectations.
- `tests/tasks/test_agent_execution.py`
  - Agent runner and provider-related task behavior.
- `tests/specs/message_flow/test_agent_spawning_transition.py`
  - Spec-level expectations for agent spawning and terminal events.
- `tests/system/test_constants.py`
  - Enforces that production constants live in `weft/_constants.py`.
- `tests/specs/test_plan_metadata.py`
  - Enforces plan metadata and `docs/plans/README.md` rows.

Comprehension questions before editing:

- Which code path owns public task lifecycle events? Answer: `Consumer` through
  `BaseTask._report_state_change()`, not runner backends.
- Which messages are private to persistent agent sessions? Answer: the
  payloads in `weft/core/tasks/agent_session_protocol.py`, not public inbox,
  outbox, ctrl, or task-log queue contracts.
- Which timeout applies to persistent agent startup? Answer: the internal
  readiness budget, not `spec.timeout`; `spec.timeout` applies to later
  concrete work-item execution.

## 4. Architecture Direction

Use one shared diagnostic payload shape across task types, but roll it out in
small slices.

The payload should be a JSON-serializable mapping with a stable top-level name,
for example `runner_diagnostics`, attached to `RunnerOutcome` and selected
task-log events. The payload should be bounded and sanitized. It should answer
startup and execution-boundary questions without becoming status truth.

Recommended initial fields:

- `phase`: one of `process_spawn`, `worker_boot`, `runtime_startup`,
  `runtime_ready`, `execute`, `teardown`, `unknown`
- `runner`: runner name, usually `host`
- `target_type`: command, function, agent, or other TaskSpec target type
- `pid`: child or runtime PID when known
- `exitcode`: process exit code when known
- `alive`: whether the process was still alive at the observation point
- `duration_seconds`: elapsed time for the observed phase when known
- `timeout_seconds`: readiness or execution timeout when relevant
- `message`: short human-readable summary
- `exception_type`: exception class name when known
- `traceback_tail`: bounded traceback tail when needed for debugging startup
  errors
- `platform`: `sys.platform`
- `python`: `sys.executable` or version details only when useful and bounded
- `last_handshake`: for persistent agent sessions, the last private handshake
  type observed by the parent

Do not log full environment variables, full TaskSpec payloads, unbounded
stdout/stderr, full provider credentials, or arbitrary model/provider config.
Use existing TaskSpec redaction for TaskSpec data. Add a small bounded
traceback limit constant in `weft/_constants.py` if traceback tails are
included.

The shared payload can be implemented as a small helper or dataclass only if it
removes real duplication. Do not build a general event bus, schema registry, or
observability subsystem. This is diagnostic metadata on the existing execution
path.

## 5. Invariants And Constraints

- Do not change public task statuses or state transition rules.
- Do not make diagnostic-only events participate in status reconstruction,
  result materialization, reserved-message policy, or cleanup decisions.
- Do not expose private agent-session protocol messages on public task inbox,
  outbox, ctrl, or result queues.
- Do not add a separate diagnostic queue or database. `weft.log.tasks` is the
  existing operational lifecycle log.
- Do not turn diagnostics into audit records. They are operational evidence for
  debugging.
- Do not log secrets. Never include full environment mappings or unredacted
  TaskSpec dumps in new diagnostic payloads.
- Do not change `spec.timeout` semantics. It remains a work-execution timeout.
  Persistent agent readiness keeps its own internal budget.
- Do not require external runner plugins to implement a new method in the same
  release. Any new hook must be optional or backward compatible.
- Do not add a dependency. Use stdlib process and traceback data plus existing
  helpers.
- Do not make tests pass by increasing timeouts. If a timeout changes, the
  diagnostic path must explain why and still prove the failure mode.
- Do not mock process or queue behavior when a real worker process or real
  broker-backed task is practical.

Stop and re-plan if:

- implementation starts adding a second task state store
- runner code starts writing public task lifecycle events directly
- the diagnostic payload needs to become authoritative for status/result
- external plugin compatibility requires a breaking `weft.ext` change
- traceback or config logging cannot be bounded and redacted cleanly
- the fix for agent sessions turns into provider or model management policy

## 6. Bite-Sized Tasks

1. Update the specs for the diagnostic boundary.
   - Outcome: the intended contract is written before code changes land.
   - Files to touch:
     - `docs/specifications/13-Agent_Runtime.md`
     - `docs/specifications/01-Core_Components.md`
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/06-Resource_Management.md`
   - Read first:
     - [AR-6], [CC-3], [CC-3.4], [MF-5], [RM-5.2]
   - Required content:
     - persistent agent sessions have private startup handshake phases
     - runner diagnostics are operational metadata, not lifecycle truth
     - readiness timeout and work timeout remain separate
     - public status/result code must ignore diagnostic-only events for state
   - Tests:
     - `uv run pytest tests/specs/test_plan_metadata.py -q`
     - any spec-text tests that fail after the edit
   - Stop if:
     - the spec wording makes diagnostics sound authoritative for state
   - Done when:
     - spec backlinks to this plan exist
     - nearby implementation mapping remains accurate

2. Define the shared runner diagnostic payload helper.
   - Outcome: all task types can produce the same bounded JSON shape without
     copy-pasted dict construction.
   - Files to touch:
     - `weft/_constants.py`
     - new helper only if needed, preferably `weft/core/runner_diagnostics.py`
     - `tests/system/test_constants.py`
     - new focused tests, for example `tests/core/test_runner_diagnostics.py`
   - Read first:
     - `weft/helpers.py` redaction helpers
     - `weft/core/runners/host.py` `RunnerOutcome`
     - `docs/specifications/01-Core_Components.md` [CC-3.2]
   - Required approach:
     - put production constants such as event names, field names, and
       traceback tail limits in `weft/_constants.py`
     - normalize payloads to JSON-safe primitives
     - truncate long messages and traceback tails with named constants
     - keep helper dependency-light so core, runners, and tests can import it
   - Avoid:
     - Pydantic models for this internal payload unless validation complexity
       actually requires them
     - a plugin system or generalized telemetry framework
   - Tests:
     - helper returns JSON-serializable output
     - long traceback/message is bounded
     - `None` fields are either omitted consistently or preserved
       consistently, whichever the spec says
     - constants test remains green
   - Stop if:
     - the helper starts importing CLI, commands, or task classes
   - Done when:
     - one helper can be reused by agent-session, one-shot worker, and command
       diagnostics

3. Harden persistent agent-session startup diagnostics first.
   - Outcome: the Windows CI failure class can no longer produce only
     `Agent session failed to signal readiness`.
   - Files to touch:
     - `weft/core/tasks/agent_session_protocol.py`
     - `weft/core/tasks/sessions.py`
     - `weft/core/runners/host.py`
     - `tests/tasks/test_runner.py`
   - Read first:
     - `docs/specifications/13-Agent_Runtime.md` [AR-6]
     - current `_agent_session_worker_entry()`
     - current `AgentSession.wait_ready()`
   - Required behavior:
     - worker sends a private `booted` response as the first guarded action
       after entering the worker function, before task-scoped cwd/env setup and
       before model/runtime startup, so the parent can distinguish spawn
       bootstrap failure from worker-entry failure
     - worker sends `ready` only after `start_agent_runtime_session()` returns
     - worker sends `startup_error` with bounded traceback details when guarded
       startup fails
     - terminal startup-error writes are flushed before the worker exits on
       Windows; use queue close/join only on terminal child-exit paths, not
       after `ready` because the response queue must remain usable
     - parent tracks last handshake state, child PID, exit code, alive status,
       readiness timeout, and any late startup-error drained after child exit
     - parent raises a specific, informative `RuntimeError` message or custom
       exception that includes these facts
   - Compatibility:
     - existing `ready`, `startup_error`, `execute`, `result`, and `stop`
       payloads keep their semantics
     - if protocol version changes, update both helpers and tests together
   - Tests:
     - real multiprocessing test: worker sends `startup_error` and exits
       immediately; parent receives the startup error rather than generic
       readiness failure
     - real or minimal process test: worker exits before any handshake; parent
       error includes PID/exitcode/last_handshake
     - hanging worker after `booted`; parent error says alive=True and
       last_handshake=booted
     - existing slow-start success test still passes with `timeout=0.1`
       because readiness timeout is separate
   - Stop if:
     - the test only uses fake queues for the Windows queue-flush behavior
   - Done when:
     - the two CI-failing tests pass locally
     - new failure-mode tests prove the parent sees useful diagnostics

4. Carry diagnostics through `RunnerOutcome` and terminal task-log events.
   - Outcome: when any runner returns a non-ok outcome or the consumer catches
     a runner exception, the terminal event includes bounded diagnostics.
   - Files to touch:
     - `weft/core/runners/host.py`
     - `weft/core/tasks/consumer.py`
     - `weft/core/tasks/base.py` only if a small helper for diagnostic events
       belongs there
     - `tests/tasks/test_task_execution.py`
     - `tests/tasks/test_task_observability.py`
   - Read first:
     - `Consumer._execute_work_item()`
     - `Consumer._ensure_outcome_ok()`
     - `Consumer._finalize_terminal_outcome()`
     - `BaseTask._report_state_change()`
   - Required behavior:
     - add optional `diagnostics` or `runner_diagnostics` to `RunnerOutcome`
       with a default so existing construction remains source-compatible
     - pass diagnostics into `work_failed`, `work_timeout`, and
       `work_limit_violation` events
     - when `_run_task()` raises before a `RunnerOutcome` exists, synthesize a
       minimal diagnostic payload at the consumer boundary
     - do not change status/result reconstruction logic
   - Tests:
     - consumer terminal event for runner `status="error"` includes
       `runner_diagnostics`
     - terminal event for direct exception includes a bounded diagnostic
     - successful task events are not bloated with traceback payloads
   - Stop if:
     - diagnostics start changing task status, result contents, or reserved
       policy
   - Done when:
     - task-log evidence carries useful context for terminal runner failures
       across the normal `Consumer` path

5. Extend one-shot host worker diagnostics.
   - Outcome: function and one-shot agent workers explain abrupt child exit,
     no-result failures, startup exceptions, and normal worker boot.
   - Files to touch:
     - `weft/core/runners/host.py`
     - `tests/tasks/test_runner.py`
     - `tests/tasks/sample_targets.py` if a real abrupt-exit target is needed
   - Read first:
     - `_worker_entry()`
     - `HostTaskRunner.run_with_hooks()` non-command branch
   - Required behavior:
     - if worker entrypoint starts, record a `worker_boot` diagnostic
     - if the child exits without a result, report PID, exit code, alive=False,
       duration, and last known phase instead of only
       `Worker produced no result`
     - if startup fails inside `_worker_entry()`, preserve the traceback tail
       in runner diagnostics
     - keep result payload and `RunnerOutcome.value` semantics unchanged
   - Tests:
     - target that calls `os._exit(73)` produces a non-ok outcome with
       exitcode=73 in diagnostics
     - target that raises still produces the existing error behavior plus
       bounded diagnostics
   - Stop if:
     - implementation wants a second worker process wrapper just for logging
   - Done when:
     - no-result failures are actionable without reproducing under a debugger

6. Extend command and interactive command startup diagnostics.
   - Outcome: command startup failures and early process exits show command
     boundary evidence without leaking secrets.
   - Files to touch:
     - `weft/core/runners/host.py`
     - `weft/core/runners/subprocess_runner.py`
     - `weft/core/tasks/sessions.py`
     - `tests/tasks/test_runner.py`
     - `tests/tasks/test_task_interactive.py`
   - Read first:
     - `_run_command_with_hooks()`
     - `run_monitored_subprocess()`
     - `HostTaskRunner.start_session()`
     - `CommandSession`
   - Required behavior:
     - Popen failures include command path, cwd, errno or exception class, and
       bounded message
     - command process started diagnostics include PID and runtime handle, not
       full environment
     - early exit before meaningful output includes return code and stream tail
       only if already bounded by existing output-size rules
     - interactive session startup failures use the same diagnostic helper
   - Tests:
     - nonexistent command path has useful diagnostics in the terminal task log
     - interactive command process that exits immediately produces a useful
       session error or terminal event without hanging
   - Stop if:
     - command diagnostics include raw environment data
   - Done when:
     - command and interactive startup failures are explainable from task-log
       evidence

7. Add optional runtime diagnostic hook only if terminal diagnostics are not
   enough.
   - Outcome: long startup hangs can emit non-terminal breadcrumbs without
     changing status.
   - Files to touch only if needed:
     - `weft/core/tasks/runner.py`
     - `weft/core/runners/host.py`
     - `weft/ext.py`
     - `weft/core/tasks/consumer.py`
     - `tests/tasks/test_task_observability.py`
   - Read first:
     - current optional hook handling in `TaskRunner.run_with_hooks()`
     - `TaskRunner.supports_stream_callbacks()`
   - Required approach:
     - make any new hook optional and signature-detected for external runner
       compatibility
     - name it narrowly, for example `on_runtime_diagnostic`, not
       `on_event_bus`
     - have `Consumer` publish any non-terminal breadcrumb through
       `_report_state_change(event=<constant>, runner_diagnostics=...)`
     - ensure status/result code ignores the diagnostic-only event
   - Tests:
     - backend that accepts the hook emits one diagnostic event
     - backend without the hook still runs
     - status reconstruction ignores diagnostic-only events
   - Stop if:
     - this task starts changing public plugin requirements in a breaking way
   - Done when:
     - live breadcrumbs are available only where they add value and all
       existing runners remain compatible

8. Surface diagnostics where operators already look.
   - Outcome: the new data is visible without adding a new command family.
   - Files to touch:
     - `weft/commands/status.py`
     - `weft/commands/tasks.py`
     - `weft/commands/result.py` only if existing error output should include
       terminal diagnostic summaries
     - `tests/commands/test_status.py`
     - `tests/commands/test_task_commands.py`
     - `tests/commands/test_result.py`
   - Required behavior:
     - JSON task status may include `runner_diagnostics` from the latest
       relevant terminal event
     - human output may include a compact one-line diagnostic only for failed
       tasks, not for every task
     - `weft result` should keep result semantics unchanged; it may include
       richer failure text when a task failed before producing a result
   - Avoid:
     - new flags unless the existing surfaces become too noisy
     - using diagnostics to infer completion
   - Tests:
     - status JSON for failed task includes diagnostics
     - status for completed task is not noisy
     - result failure text includes the useful startup reason but still exits
       with the existing failure code
   - Stop if:
     - CLI output churn becomes larger than the debugging value
   - Done when:
     - operators do not need to manually scrape `weft.log.tasks` for the most
       common startup failures

9. Add a deploy verification runbook snippet.
   - Outcome: after release, ops can confirm diagnostics work with a small
     failure task instead of waiting for a rare CI-only failure.
   - Files to touch:
     - `docs/agent-context/runbooks/testing-patterns.md` if the guidance is
       generally reusable
     - `docs/lessons.md` if implementation exposes a repeated failure mode
   - Required content:
     - command to submit a deliberately failing function or command task
     - command to inspect `weft task status --json <tid>` or
       `weft queue peek weft.log.tasks`
     - expected diagnostic fields
     - reminder that diagnostics are operational evidence, not audit records
   - Done when:
     - post-deploy validation is clear enough to run on `ops` without reading
       this whole plan

## 7. Testing Plan

Use red-green TDD where practical. The first failing tests should target the
opaque Windows-ready failure class before implementation.

Do not over-mock:

- Use real `multiprocessing` workers for queue flush, child exit, and startup
  timeout behavior.
- Use real `TaskRunner` and `Consumer` paths for task-log diagnostics.
- Use `broker_env` or `WeftTestHarness` for queue-visible assertions.
- Mock only external agent providers or model backends, as current tests do
  with deterministic `llm` plugins.

Targeted tests:

- `tests/tasks/test_runner.py`
  - agent session startup-error survives immediate child exit
  - agent session child exits before handshake and reports PID/exitcode
  - agent session child hangs after boot and reports last_handshake=booted
  - existing slow-start readiness test still passes
  - one-shot worker abrupt exit includes exitcode diagnostics
- `tests/tasks/test_task_execution.py`
  - failed consumer terminal event includes `runner_diagnostics`
  - direct runner exception gets synthesized diagnostics
- `tests/tasks/test_task_observability.py`
  - diagnostic-only events do not change lifecycle assertions
  - successful tasks are not bloated with failure tracebacks
- `tests/tasks/test_task_interactive.py`
  - interactive command startup or early-exit diagnostics are visible and do
    not hang
- `tests/commands/test_status.py`
  - status JSON exposes diagnostics for failed tasks without deriving state
    from them
- `tests/commands/test_result.py`
  - result failure output remains compatible and gains useful diagnostic text
    only when no result can be read
- `tests/system/test_constants.py`
  - new constants live in `weft/_constants.py`
- `tests/specs/test_plan_metadata.py`
  - plan metadata remains normalized

Cross-platform expectations:

- Windows spawn behavior must be covered by CI. Local macOS passing is not
  enough for the agent-session queue-flush failure mode.
- Do not skip Windows tests unless a test asserts a POSIX-only mechanism. The
  current failure is Windows-specific but the contract should be portable.
- Avoid tests that assert exact traceback line numbers or OS-specific error
  wording. Assert stable fields: phase, pid or exitcode when known, alive
  state, and a bounded message.

## 8. Verification And Gates

Per-task gates:

```bash
uv run pytest tests/tasks/test_runner.py -q
uv run pytest tests/tasks/test_task_execution.py -q
uv run pytest tests/tasks/test_task_observability.py -q
uv run pytest tests/specs/test_plan_metadata.py -q
uv run pytest tests/system/test_constants.py -q
```

Expanded gates before claiming the slice is done:

```bash
uv run pytest
uv run bin/pytest-pg
uv run mypy weft extensions/weft_docker extensions/weft_macos_sandbox
uv run ruff format --check
uv run ruff check
```

Post-deploy observation:

- Submit or run a deliberate command-startup failure and confirm the task-log
  terminal event carries `runner_diagnostics`.
- Submit or run a deliberate abrupt function-worker exit and confirm the
  diagnostic includes child PID and exit code when available.
- If an agent runtime is available in the environment, run a persistent agent
  task with a deliberately bad plugin/module and confirm the failure explains
  whether the worker booted, runtime startup failed, or readiness timed out.

Rollback:

- Because diagnostics are additive, rollback should be able to remove the new
  fields and private `booted` handling without rewriting existing task logs.
- Keep readers tolerant of missing diagnostics. Old logs and older running
  tasks will not have the new fields.
- Do not introduce cleanup or migration work for historical rows.

## 9. Independent Review Loop

Before implementation, run an independent engineering review of this plan.
Recommended prompt:

> Read `docs/plans/2026-05-08-agent-session-and-task-startup-observability-plan.md`
> plus `weft/core/runners/host.py`, `weft/core/tasks/sessions.py`,
> `weft/core/tasks/consumer.py`, `weft/core/tasks/agent_session_protocol.py`,
> and the cited specs. Look for architecture mistakes, unclear ownership,
> over-broad scope, weak tests, and plugin compatibility hazards. Do not
> implement. Answer whether a skilled zero-context engineer could implement
> this correctly.

The author must incorporate review findings or record why a point is out of
scope before implementation begins.

## 10. Out Of Scope

- Queue cleanup, retention, TaskMonitor cleanup, and audit retention.
- Manager election, heartbeat supervision, and internal singleton service
  reconciliation.
- Provider-specific health management or model/provider readiness policy.
- A new telemetry service, metrics backend, log collector, or diagnostic
  database.
- A breaking public plugin API change.
- Changing task status semantics, result semantics, reserved-message policy,
  queue names, or TaskSpec schema.
- Making Weft queues into audit evidence. Diagnostics are operational
  breadcrumbs.

## 11. Fresh-Eyes Review

Review pass notes:

- The plan deliberately starts with agent-session readiness because that is the
  observed Windows failure and the highest-value opaque path.
- The plan extends to all task types through a shared payload and terminal
  events, not through a broad event bus.
- The plan keeps private protocol messages private.
- The plan names files, tests, stop gates, rollback, and plugin compatibility
  constraints.
- The largest ambiguity left for implementation is whether non-terminal
  `runtime_diagnostic` events are needed. Task 7 makes that conditional:
  implement terminal diagnostics first, then add the optional hook only if the
  terminal-only proof is insufficient for startup hangs.

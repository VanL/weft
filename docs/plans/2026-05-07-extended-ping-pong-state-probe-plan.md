# Extended PING/PONG State Probe Plan

Status: completed
Source specs: see Source Documents below
Superseded by: none

## 1. Goal

Extend the task control `PING`/`PONG` contract so a successful `PONG` is an
authoritative live task-local status snapshot at the time it is emitted, not
only a bare liveness response. Add request correlation for structured `PING`
messages, then use keyed PONGs as a last-resort active probe for known-task
status reconciliation when durable log/runtime evidence is stale or ambiguous,
especially for Docker and externally supervised runners where host PID
reconciliation may be weak.

This is a contract change on the control plane. Keep it narrow: no new task
states, no new queue names, no second control plane, no full TaskSpec dumps in
PONG, and no project-wide active probing by default. Docker-specific runtime
facts are in scope when the task is actually running under Docker and those
facts come from the existing runner handle/plugin path.

## 2. Source Documents

Source specs:

- [`docs/specifications/00-Quick_Reference.md`](../specifications/00-Quick_Reference.md)
  "Control Messages"
- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md)
  [MA-0], [MA-1.7]
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-3], [MF-5]
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [STATE.1], [STATE.2], [STATE.6], [QUEUE.2], [OBS.1], [OBS.2], [OBS.3],
  [OBS.10], [OBS.11], [MANAGER.7]
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
  [CLI-1.2], [CLI-1.2.1], [CLI-6]

Guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/runtime-and-context-patterns.md`](../agent-context/runbooks/runtime-and-context-patterns.md)
- [`docs/agent-context/runbooks/testing-patterns.md`](../agent-context/runbooks/testing-patterns.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)

Related plans:

- [`docs/plans/2026-05-06-status-coherence-and-stale-pid-liveness-plan.md`](./2026-05-06-status-coherence-and-stale-pid-liveness-plan.md)
  made terminal lifecycle proof win over stale runtime liveness and added
  reconciliation diagnostics.
- [`docs/plans/2026-05-06-task-evidence-reconciliation-model-plan.md`](./2026-05-06-task-evidence-reconciliation-model-plan.md)
  introduced the shared read-only task evidence model. This plan should extend
  that model with an active PONG probe instead of adding a parallel status
  reducer.
- [`docs/plans/2026-04-24-runtime-handle-authority-migration-plan.md`](./2026-04-24-runtime-handle-authority-migration-plan.md)
  defines why runtime handles, especially external-supervisor handles, cannot
  always be reduced to host PID truth.
- Docker runner implementation:
  `extensions/weft_docker/weft_docker/plugin.py` currently exposes
  `DockerRunnerPlugin.describe()` and `_describe_runtime()`, including
  container state, container ID/name, image, OOM flag, exit code, host PID,
  timestamps, network mode, CPU, memory, pids, network IO, and block IO.

Spec delta required before implementation is done:

- Update [MF-3] so `PING` accepts the legacy raw string and a structured JSON
  command envelope, and so `PONG` includes a task-local status snapshot.
- Update [MF-5] so a matched, fresh keyed PONG may be used as authoritative
  live evidence for known-task reconciliation. Reconciliation should compare
  authoritative observations by timestamp: a newer matched PONG can supersede
  older durable observations, while newer terminal log or terminal `ctrl_out`
  evidence still wins.
- Update `00-Quick_Reference.md` so `PING` no longer says only "responds
  PONG"; it should say "responds PONG with a live task-local status snapshot."
- Update [MA-1.7] because Managers inherit the same PING/PONG semantics.
- Update [CLI-1.2] / [CLI-1.2.1] only if the implementation exposes active
  PONG reconciliation in public JSON task snapshots.
- Add a backlink from each touched spec section to this plan.

## 3. Current Structure And Key Files

Files to modify:

- `weft/_constants.py`
  - Add `CONTROL_PING = "PING"` and `CONTROL_STATUS = "STATUS"` if doing so
    removes repeated string literals in touched code. Do not move policy into
    constants.
- `weft/core/tasks/base.py`
  - Main runtime owner for control message parsing, shared PING/STATUS
    response fields, and default task PONG behavior.
  - `BaseTask._handle_control_message()` currently uppercases the raw message
    string and passes only a command string to `_handle_control_command()`.
  - `_send_control_response()` already writes JSON responses to `ctrl_out`.
  - `_runtime_handle` and `_current_runner_name()` are the task-owned bridge to
    runner-specific observability. Reuse that bridge; do not reconstruct Docker
    identity from TaskSpec or environment variables.
- `weft/core/tasks/pipeline.py`
  - Overrides `_handle_control_command()` and currently special-cases `PING`
    and `STATUS`; keep pipeline-specific `STATUS` behavior, but reuse the
    shared PONG snapshot fields.
- `weft/core/manager.py`
  - Overrides `_handle_control_command()` for STOP/drain behavior. Update the
    signature if the base hook grows a control request object or `request_id`.
    Managers should inherit the base PING behavior.
- `weft/core/tasks/monitor.py`
  - Overrides `_handle_control_command()` for monitor-specific STOP behavior.
    Update the signature and preserve existing STOP semantics.
- `weft/core/tasks/consumer.py`
  - Contains additional task classes and STOP/KILL override paths. Update hook
    signatures without changing reserved-policy behavior.
- `weft/commands/task_evidence.py`
  - Extend the shared evidence model with active PONG evidence. This is the
    correct DRY home for reconciliation classification, not `tasks.py` or
    `status.py` ad hoc code.
- `extensions/weft_docker/weft_docker/plugin.py`
  - Read this file for available Docker runtime metadata. Touch it only if the
    existing `RunnerRuntimeDescription` lacks a needed safe field; do not add
    PING-specific Docker logic here unless the generic runner description
    contract needs it.
- `weft/commands/tasks.py`
  - Existing task command surface. `_send_control()` currently writes raw
    string commands. Add a keyed PING helper here only if it remains a thin
    command wrapper around shared evidence/probe logic.
- `weft/commands/interactive.py`
  - `InteractiveTaskClient.wait_for_control_response()` currently matches by
    command and status only. Add optional `request_id` matching if interactive
    clients need keyed probes.
- `weft/commands/types.py`
  - Touch only if public `TaskSnapshot.reconciliation` needs a new additive
    `classification` or metadata field.
- `tests/tasks/test_control_channel.py`
  - Main red-green tests for raw and structured PING/PONG behavior.
- `tests/tasks/test_task_interactive.py`
  - Update if interactive client control matching changes.
- `tests/commands/test_task_evidence.py`
  - Main tests for active PONG evidence classification and precedence.
- `tests/commands/test_task_commands.py`
  - Known-TID task status / terminal snapshot regressions when public command
    behavior changes.
- `tests/commands/test_status.py`
  - Touch only if project-wide or public status output changes. Do not make
    `weft status` actively ping many tasks by default in this plan.
- `docs/specifications/00-Quick_Reference.md`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/10-CLI_Interface.md` if public JSON output changes.
- `docs/lessons.md` only if implementation exposes a reusable failure mode not
  already captured.

Files to read before editing:

- `weft/core/tasks/base.py`
  - Read `_handle_control_message()`, `_handle_control_command()`,
    `_send_control_response()`, `_report_state_change()`,
    `_current_runner_name()`, `_runtime_handle`, activity handling, and
    terminal-envelope publication.
- `weft/ext.py`
  - Read `RunnerHandle` and `RunnerRuntimeDescription`. PONG should use these
    existing shapes for runner-specific facts rather than inventing a Docker
    payload format from scratch.
- `extensions/weft_docker/weft_docker/plugin.py`
  - Read `DockerRunnerPlugin.describe()` and `_describe_runtime()`. These are
    the existing source of Docker-specific observable facts.
- `weft/core/tasks/pipeline.py`
  - Read `_handle_control_command()` and `_publish_pipeline_snapshot()`. The
    pipeline task has extra status output; do not flatten that away.
- `weft/commands/task_evidence.py`
  - Read `TaskEvidenceSnapshot`, `known_tid_evidence()`,
    `runtime_evidence()`, `task_local_terminal_evidence()`, and the existing
    precedence order before adding PONG evidence.
- `weft/commands/tasks.py`
  - Read `_ctrl_in_for_tid()`, `_ctrl_out_for_tid()`, `_send_control()`,
    `task_status()`, `task_terminal_snapshot()`, and `watch_task_status()`.
- `weft/commands/interactive.py`
  - Read `wait_for_control_response()` and the code path that stores
    `_control_responses`.
- `tests/helpers/weft_harness.py`
  - Use the harness for end-to-end task/manager behavior. Do not replace it
    with mocks for broker and task lifecycle proof.
- `tests/tasks/test_control_channel.py`
  - Extend existing control-channel tests rather than creating a parallel
    mock-only test suite.

Comprehension checks before editing:

- What queue receives control commands? Answer: `T{tid}.ctrl_in` or the
  resolved control input queue in `TaskSpec.io.control`.
- What queue carries PONG? Answer: `T{tid}.ctrl_out` or the resolved control
  output queue in `TaskSpec.io.control`.
- What is canonical durable lifecycle history? Answer: `weft.log.tasks`.
- Why can PONG be stronger than PID reconciliation for Docker? Answer: a
  matched PONG proves the task-local control owner was alive and reported its
  current state at the PONG timestamp, while host PID evidence may point at a
  wrapper, stale PID, external supervisor, or unknown runtime state.
- Where should Docker-specific PONG facts come from? Answer: the task's
  current `RunnerHandle` plus the Docker runner plugin's existing
  `describe()`/`RunnerRuntimeDescription` path, not a new Docker inspection
  path inside command/status code.
- What must a PONG not do? Answer: it must not mutate task state, write a
  lifecycle event, publish a terminal envelope, or resurrect a task after newer
  terminal evidence.

## 4. Desired Contract

### 4.1 Control Input

Keep legacy raw control messages working:

```text
PING
STATUS
STOP
KILL
PAUSE
RESUME
```

Add structured JSON command envelopes for PING first:

```json
{
  "command": "PING",
  "request_id": "01JZEXAMPLE",
  "timestamp": 1770000000000000000
}
```

Rules:

- `command` is required for structured envelopes and is case-insensitive.
- `request_id` is optional but, when present, must be a non-empty string.
- The first implementation only needs structured-envelope support for PING and
  STATUS. It is acceptable if STOP/KILL/PAUSE/RESUME keep using raw strings,
  provided structured parsing does not break them.
- Do not call this field `idempotency_key`. It does not deduplicate a
  side-effecting operation; it correlates one request with one response.

### 4.2 PONG Output

Legacy raw `PING` still returns a JSON control response with `message="PONG"`.
A structured PING with `request_id` must echo that exact request ID:

```json
{
  "command": "PING",
  "status": "ok",
  "message": "PONG",
  "request_id": "01JZEXAMPLE",
  "tid": "1770000000000000000",
  "timestamp": 1770000001000000000,
  "task_status": "running",
  "paused": false,
  "should_stop": false,
  "runner": "docker",
  "activity": "executing",
  "waiting_on": null
}
```

Required PONG fields:

- `command`: `"PING"`
- `status`: `"ok"`
- `message`: `"PONG"`
- `tid`
- `timestamp`
- `task_status`
- `paused`
- `should_stop`
- `runner`

Optional PONG fields:

- `request_id`, only when present on the inbound command
- `activity`, only when known
- `waiting_on`, only when known
- `runtime`, as a small redacted summary for the active runner when available
  within the bounded PING handling budget

For Docker tasks, `runtime` should include Docker-specific facts when the
existing runner handle/plugin path can provide them safely:

```json
{
  "runner": "docker",
  "id": "weft-...",
  "state": "running",
  "metadata": {
    "container_id": "...",
    "container_name": "weft-...",
    "image": "example:tag",
    "status": "running",
    "oom_killed": false,
    "exit_code": 0,
    "host_pid": 12345,
    "started_at": "2026-05-07T00:00:00Z",
    "network_mode": "bridge",
    "cpu_percent": 1.23,
    "memory_usage_mb": 42.0,
    "memory_limit_mb": 512,
    "pids": 7,
    "network_io_bytes": {"rx": 1000, "tx": 2000},
    "block_io_bytes": {"read": 4096, "write": 8192}
  }
}
```

Do not require every Docker field to be present. Docker can omit fields by
platform, daemon version, container state, permission boundary, or stats
availability. Missing Docker details should not make PING fail if the task can
still return its core PONG snapshot.

Do not include:

- full TaskSpec payloads
- unredacted environment variables
- full runtime handles
- outbox contents
- queue dumps
- child process trees
- new public lifecycle states

### 4.3 STATUS Output

STATUS should reuse the same shared task-local snapshot fields where practical.
This keeps PING and STATUS from drifting:

- `task_status`
- `paused`
- `should_stop`
- `runner`
- optional `activity`
- optional `waiting_on`

Pipeline-specific STATUS may keep additional fields such as `status_queue` and
may continue to publish a pipeline snapshot as it does today.

### 4.4 Reconciliation Semantics

A matched, keyed PONG is authoritative live evidence for the responding task
at the PONG timestamp.

Precedence rule:

1. Compare authoritative task-owned observations by timestamp when timestamps
   are available: task-log lifecycle events, typed terminal `ctrl_out`
   envelopes, and matched keyed PONGs.
2. The newest authoritative observation wins for current known-task status.
3. If timestamps are equal or missing, terminal task-log or typed terminal
   `ctrl_out` evidence wins over PONG.
4. A matched keyed PONG wins over older or ambiguous non-terminal runtime/log
   evidence.
5. Runtime evidence and stale observer fallbacks remain below a matched keyed
   PONG.

Important nuance: terminal state remains forward-only inside the task state
machine. PONG does not mutate that state. Public reconciliation, however, is a
read model over observations. If a task responds after an older terminal
observation, the PONG is a newer authoritative live observation and may be used
for current known-task status. That conflict should be surfaced in
reconciliation metadata because it means durable history and the live control
owner disagree.

Recommended new evidence classification:

```json
{
  "classification": "live_pong",
  "reason": "matched_control_pong",
  "lifecycle_status": "running",
  "evidence_source": "ctrl_out",
  "request_id": "01JZEXAMPLE",
  "observed_at": 1770000001000000000
}
```

If a PONG is newer than visible terminal evidence, expose a diagnostic reason
such as `live_pong_after_terminal_evidence` rather than silently hiding the
conflict. If terminal evidence is newer than the PONG or cannot be ordered
against it, keep the terminal evidence.

### 4.5 Active Probe Boundary

Do not make `weft status` ping every task by default. Project-wide status must
stay a read-mostly status reducer; active probing every task would create
broker writes, PONG buildup, timeout costs, and surprising side effects.

Use active keyed PING only in known-task paths where the caller wants current
state beyond the read-only reducer:

- `weft task status TID` may use an active probe as a last-resort known-TID
  reconciliation step, if implementation keeps the timeout short and bounded.
  If the caller explicitly requests a current-state probe, the matched PONG is
  reconciled by timestamp even when read-only terminal evidence exists.
- command/client helper APIs may expose an explicit `probe_live` or equivalent
  parameter if automatic probing would surprise existing callers.
- `watch_task_status()` may use keyed PING only if it does not spam the control
  queue. A reasonable first slice is to avoid watch integration.

If the implementation starts needing a scheduler, background heartbeat, lease,
new queue, or periodic probe loop, stop. That is a materially different design.

## 5. Invariants And Constraints

- TID format and immutability do not change.
- Task state transitions stay forward-only. PONG is observation, not mutation.
- `spec` and `io` remain immutable after resolved TaskSpec creation.
- Existing raw string control messages remain backward-compatible.
- `ctrl_in` and `ctrl_out` queue names do not change.
- `weft.log.tasks` remains the durable audit trail. PONG does not replace the
  task log.
- Terminal `ctrl_out` envelopes remain typed with `type="terminal"`. Ordinary
  PONG responses must not look like terminal envelopes.
- Active STOP/KILL reserved-policy behavior does not change.
- Managers remain task-shaped and inherit PING/PONG behavior through
  `BaseTask` unless they have a specific override reason.
- Do not add a new dependency.
- Do not add a new abstraction layer such as a `ControlPlane` service. Use
  small helpers in existing owner modules.
- Do not add active probing to project-wide status by default.
- Runner-specific PONG facts must use the existing runner plugin/handle
  contracts. For Docker, that means `RunnerHandle` plus
  `DockerRunnerPlugin.describe()` / `RunnerRuntimeDescription`; no second
  Docker SDK inspection path in command/status code.
- Docker detail collection is best-effort. A Docker describe failure may add a
  small diagnostic in `runtime.metadata`, but it must not suppress the core
  PONG response if the task control loop is alive.
- Do not use fixed-limit queue history reads for correctness-critical evidence.
  Use generator-based reads where the code scans append-only queues.
- Do not mock SimpleBroker queues or task lifecycle when a real broker-backed
  test is practical.
- Control response write failures remain best-effort observability failures.
  They should not crash an otherwise healthy task loop.

Rollback compatibility:

- Old senders that write raw `"PING"` must continue to receive a PONG.
- Old readers that only check `command == "PING"` and `message == "PONG"` must
  continue to work.
- New readers must require matching `request_id` for active probes so old PONGs
  in `ctrl_out` cannot be mistaken for the new probe.
- If active PONG reconciliation must be reverted, the enriched PONG payload can
  remain because it is additive. Revert only the active-probe call path.

Out of scope:

- Periodic heartbeat services.
- Endpoint leases.
- Queue cleanup or PONG garbage collection.
- Full task introspection over PING.
- Public API redesign for all control messages.
- New Docker-specific status code outside the runner plugin. Docker-specific
  PONG details are in scope, but they must come through the existing runner
  plugin description contract.

## 6. Bite-Sized Tasks

### 1. Red test the enriched PONG contract

Outcome: existing raw PING still works, and structured keyed PING echoes the
request ID and task-local snapshot fields.

Files to touch:

- `tests/tasks/test_control_channel.py`

Read first:

- `weft/core/tasks/base.py` `_handle_control_message()`,
  `_handle_control_command()`, `_send_control_response()`
- Existing `test_ping_control_command_returns_pong`

Test requirements:

- Add or extend a test where `ctrl_in.write("PiNg")` still produces
  `command == "PING"`, `status == "ok"`, `message == "PONG"`.
- Add a red test for structured input:

  ```python
  ctrl_in.write(json.dumps({"command": "PING", "request_id": request_id}))
  ```

  Assert the PONG includes:

  - same `request_id`
  - `task_status == task.taskspec.state.status`
  - `paused is False`
  - `should_stop is False`
  - `runner` is present and non-empty

- Use the real broker-backed `broker_env` fixture. Do not mock queues.

Stop and re-evaluate if:

- The test wants to inspect private fields other than the existing task object
  state needed to assert the emitted public response.
- The implementation idea requires a new queue or a new watcher.

Per-task verification:

```bash
./.venv/bin/python -m pytest tests/tasks/test_control_channel.py -q
```

The new structured PING test should fail before implementation.

### 2. Parse structured control envelopes without breaking raw commands

Outcome: `BaseTask` accepts raw command strings and JSON object envelopes with
`command` and optional `request_id`.

Files to touch:

- `weft/core/tasks/base.py`
- `weft/_constants.py` if adding `CONTROL_PING` / `CONTROL_STATUS`
- `tests/tasks/test_control_channel.py`

Implementation guidance:

- Add a small frozen dataclass in `base.py`, for example
  `ControlRequest`, with:

  ```python
  command: str
  request_id: str | None = None
  raw: str | None = None
  ```

- Add a small parser helper near the control methods. Keep it local to
  `base.py` unless another module needs it immediately.
- For raw non-JSON messages, preserve current behavior:
  `message.strip().upper()`.
- For JSON objects, read `command`, uppercase it, and accept a non-empty string
  `request_id`.
- Keep unknown or malformed commands observable through the existing
  `control_unknown` path and ACK behavior.
- Avoid a generic schema framework. This is a tiny boundary parser, not a new
  control protocol layer.

Hook guidance:

- Prefer changing `_handle_control_command()` to receive the parsed request,
  not just a string, if that keeps request ID echoing DRY:

  ```python
  def _handle_control_command(
      self,
      request: ControlRequest,
      context: QueueMessageContext,
  ) -> bool:
  ```

- If that feels too invasive, an optional keyword-only `request_id` is
  acceptable. Do not use hidden mutable instance state to pass request IDs.
- Update all overrides in:
  - `weft/core/manager.py`
  - `weft/core/tasks/pipeline.py`
  - `weft/core/tasks/monitor.py`
  - `weft/core/tasks/consumer.py`

Tests to add/update:

- Existing STOP/KILL/Pause/Resume tests must still pass with raw strings.
- Add a malformed structured command test only if the parser behavior is not
  obvious from the happy-path tests. Keep it small.

Stop and re-evaluate if:

- You are tempted to accept multiple incompatible field names such as `id`,
  `nonce`, and `idempotency_key`. The field is `request_id`.
- The change starts modifying TaskSpec or queue names.

Per-task verification:

```bash
./.venv/bin/python -m pytest tests/tasks/test_control_channel.py -q
```

### 3. Add one shared task-local control snapshot helper

Outcome: PING and STATUS share the same task-local snapshot fields, and PONG
does not need custom field assembly scattered across task classes.

Files to touch:

- `weft/core/tasks/base.py`
- `weft/core/tasks/pipeline.py`
- `tests/tasks/test_control_channel.py`
- `tests/tasks/test_task_interactive.py` if existing interactive assertions
  expect the old PING shape

Implementation guidance:

- Add a helper on `BaseTask`, for example `_control_snapshot_fields()`, that
  returns a `dict[str, Any]` with:
  - `task_status`
  - `paused`
  - `should_stop`
  - `runner`
  - optional `activity`
  - optional `waiting_on`
  - optional `runtime`
- Reuse `_current_runner_name()` for `runner`.
- For `runtime`, add a small helper such as `_control_runtime_summary()` that:
  - returns `None` when no runtime handle is active
  - calls the existing runner plugin `describe()` only when a runtime handle is
    present and the plugin is available
  - returns `RunnerRuntimeDescription.to_dict()` or a redacted subset of that
    shape
  - catches runner describe failures defensively and returns a small diagnostic
    rather than failing PING
- For Docker, this helper should surface the Docker plugin's existing runtime
  metadata. Do not manually import the Docker SDK from `base.py`.
- Let `_send_control_response()` remain the single writer to `ctrl_out`.
- Update base PING to:

  ```python
  self._send_control_response(
      CONTROL_PING,
      "ok",
      message="PONG",
      request_id=request.request_id,  # only if not None
      **self._control_snapshot_fields(),
  )
  ```

  Build the extra dict so `request_id` is omitted when absent, not included as
  `null`, unless local style already prefers explicit nulls.

- Update base STATUS to reuse `_control_snapshot_fields()`.
- In `PipelineTask._handle_control_command()`, keep
  `_publish_pipeline_snapshot()` and `status_queue`, but use the shared
  snapshot fields for PING and STATUS. Do not create a second PONG builder.

Tests to add/update:

- Extend `test_status_command_reports_state` so STATUS includes `runner`.
- Add a unit-level or broker-backed test for `_control_snapshot_fields()` via
  public PONG output where a fake or simple registered runner plugin returns a
  `RunnerRuntimeDescription`. Assert `runtime.runner`, `runtime.id`, and
  `runtime.state` appear. Mocking the plugin boundary is acceptable here only
  to avoid depending on a real Docker daemon in the core test suite; do not mock
  the control queue.
- Add or update a pipeline PING test if there is existing coverage for
  pipeline control responses; otherwise add a focused test only if the override
  would otherwise remain untested.
- Keep assertions on public JSON response fields, not private helper internals.

Stop and re-evaluate if:

- Full TaskSpec payloads or runtime handles start entering PONG.
- `PipelineTask` needs duplicate code for fields already available from
  `BaseTask`.
- Docker support requires Docker SDK imports or container lookup logic outside
  the Docker runner plugin.

Per-task verification:

```bash
./.venv/bin/python -m pytest tests/tasks/test_control_channel.py tests/tasks/test_task_interactive.py -q
```

### 4. Add an active keyed PONG probe helper to shared task evidence

Outcome: known-task reconciliation has one shared way to send a keyed PING and
observe the matching PONG without consuming unrelated task-local evidence.

Files to touch:

- `weft/commands/task_evidence.py`
- `weft/commands/tasks.py` only for thin wrapper integration
- `weft/commands/types.py` only if public dataclasses need an additive field
- `tests/commands/test_task_evidence.py`

Implementation guidance:

- Add a helper in `task_evidence.py`, for example
  `probe_live_pong_evidence(ctx, *, tid, taskspec_payload, timeout)`.
- Generate a request ID with stdlib only, for example `uuid.uuid4().hex`.
  Do not add a dependency.
- Resolve `ctrl_in` and `ctrl_out` using existing queue-name helpers. Reuse
  `queue_names_for_tid()` for `ctrl_out`; add a parallel helper for `ctrl_in`
  if needed rather than duplicating queue-name parsing in multiple files.
- Write this payload to `ctrl_in`:

  ```json
  {"command": "PING", "request_id": "..."}
  ```

- Wait briefly for `ctrl_out` changes. Prefer existing queue monitoring
  primitives if already used in `tasks.py`; otherwise use a bounded poll with
  a small sleep constant already present in command code. Do not create a
  background thread.
- Match only JSON responses with:
  - `command == "PING"`
  - `status == "ok"`
  - `message == "PONG"`
  - exact `request_id`
  - matching `tid`
  - string `task_status`
- Do not consume arbitrary `ctrl_out` messages. If the code chooses to ACK the
  matched PONG, it must use the exact message timestamp and must never consume
  unmatched terminal envelopes or stream/error messages. The safer first slice
  is non-consuming peek.
- Return a `TaskEvidenceSnapshot` with:
  - `status` from `task_status`
  - `classification="live_pong"`
  - `source="ctrl_out"`
  - `terminal` based on whether `task_status` is terminal
  - `observed_at` from the PONG message timestamp or PONG payload timestamp,
    choosing one rule and documenting it in code
  - `activity`, `waiting_on`, and `runtime_handle` only if safely available
  - `reconciliation` containing `classification`, `reason`,
    `lifecycle_status`, `evidence_source`, `request_id`, and `observed_at`

Recommended timestamp rule:

- Use the broker message timestamp as `observed_at`, because it proves when the
  PONG became visible in `ctrl_out`.
- Preserve the payload's internal `timestamp` in `metadata["pong_timestamp"]`
  if needed.

Tests to add:

- A broker-backed live-task test, preferably with `WeftTestHarness`, where the
  helper sends a keyed PING to a real task control loop and observes the
  matching PONG. Assert the helper returns `live_pong`.
- A test with an old unmatched PONG already in `ctrl_out` before the probe
  starts. The real task should still answer the new keyed PING, and the helper
  must ignore the old unmatched response.
- A test with a terminal `ctrl_out` envelope already in the same queue before
  the probe starts. The helper must not consume or misclassify that terminal
  envelope while waiting for the matched PONG.
- If a full live task makes the helper tests too slow or brittle, split the
  implementation into a small public or module-local "match PONG payload"
  helper and a "send active PING" wrapper. Test matching with broker-backed
  queues and test the active wrapper once through a real task. Do not replace
  the active wrapper proof with mocks.

Do not mock:

- `WeftContext`
- SimpleBroker queues
- `ctrl_in` / `ctrl_out` queue behavior

Mocks are acceptable only for timeouts if the test would otherwise be slow, but
prefer short real timeouts over complicated time mocks.

Stop and re-evaluate if:

- The helper needs to scan `weft.log.tasks` itself. Evidence precedence should
  stay in `known_tid_evidence()` or a nearby shared reducer.
- The helper starts deleting queues or cleaning up old PONGs.

Per-task verification:

```bash
./.venv/bin/python -m pytest tests/commands/test_task_evidence.py -q
```

### 5. Integrate PONG evidence into known-TID reconciliation

Outcome: known-task status paths can use active keyed PING as a last-resort
current-state probe when durable evidence is stale or ambiguous.

Files to touch:

- `weft/commands/task_evidence.py`
- `weft/commands/tasks.py`
- `tests/commands/test_task_evidence.py`
- `tests/commands/test_task_commands.py`
- `tests/commands/test_status.py` only if public status behavior changes

Implementation guidance:

- Keep `known_tid_evidence()` read-only by default unless the function name and
  docs clearly say it may actively probe. A good shape is:

  ```python
  def known_tid_evidence(..., probe_live: bool = False, probe_timeout: float = ...):
      ...
  ```

- Preserve the current evidence order for read-only calls.
- When `probe_live=True`, the caller is explicitly asking for current
  task-local state. Send the keyed PING after collecting the normal read-only
  evidence, then reconcile the matched PONG against the best existing evidence
  by timestamp.
- If the matched PONG is newer than the best existing evidence, return PONG
  evidence. If the superseded evidence was terminal, include
  `reason="live_pong_after_terminal_evidence"` in reconciliation metadata.
- If the best existing terminal evidence is newer than the matched PONG, keep
  terminal evidence.
- If timestamps cannot be compared, keep terminal evidence over PONG and use
  PONG only over runtime/observer ambiguity.
- If no PONG is returned, preserve the current read-only evidence order.
- Do not active-probe inside project-wide `weft status` by default.
- If exposing public command behavior, prefer known-TID `weft task status TID`
  over broad `weft status`. If adding a CLI flag, name it plainly such as
  `--probe-live`; do not hide broad broker writes behind existing status
  commands without a spec update.

Tests to add/update:

- Known-TID status with stale/unknown runtime evidence and a matched PONG
  returns public status from PONG and reconciliation classification
  `live_pong`.
- Known-TID status with a matched Docker PONG preserves the PONG `runtime`
  summary in evidence metadata or reconciliation output, depending on the final
  public `TaskSnapshot` shape. The test should not require a real Docker daemon
  unless the Docker extension suite already has an isolated Docker test
  fixture.
- Terminal log evidence newer than PONG remains terminal.
- Matched PONG newer than visible terminal evidence returns PONG status with a
  conflict reconciliation reason.
- Matched PONG newer than stale non-terminal evidence wins.
- Project-wide status does not write PINGs by default. This can be tested by
  checking the task `ctrl_in` queue remains empty after `cmd_status()` or the
  existing status command helper runs.

Stop and re-evaluate if:

- Integrating PONG requires rewriting the entire status reducer.
- The design starts active-probing many tasks by default.
- The test suite becomes mock-heavy because the real queue path feels
  inconvenient. That is a sign the seam is wrong.

Per-task verification:

```bash
./.venv/bin/python -m pytest tests/commands/test_task_evidence.py tests/commands/test_task_commands.py tests/commands/test_status.py -q
```

### 6. Update interactive client control response matching if needed

Outcome: callers that send keyed PINGs through an interactive client can wait
for the matching PONG without accidentally accepting an older response.

Files to touch:

- `weft/commands/interactive.py`
- `tests/commands/test_interactive_client.py`
- `tests/tasks/test_task_interactive.py` if task-side PING behavior changes
  those assertions

Implementation guidance:

- Add optional `request_id: str | None = None` to
  `InteractiveTaskClient.wait_for_control_response()`.
- Preserve existing behavior when `request_id` is omitted.
- When `request_id` is provided, require exact equality on the response
  `request_id`.
- Do not make the interactive client generate PINGs in this task unless a
  caller already needs that wrapper. The evidence helper owns active probing.

Tests:

- Existing STOP wait tests continue to pass without request IDs.
- A new test with two stored PING responses proves `request_id` matching
  returns the intended one.

Stop and re-evaluate if:

- The client starts duplicating the active PING probe logic from
  `task_evidence.py`.

Per-task verification:

```bash
./.venv/bin/python -m pytest tests/commands/test_interactive_client.py tests/tasks/test_task_interactive.py -q
```

### 7. Update specs and backlinks

Outcome: specs become the authoritative behavior source again.

Files to touch:

- `docs/specifications/00-Quick_Reference.md`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/10-CLI_Interface.md` if public JSON output changes
- this plan, if implementation choices differ within the intended direction

Required edits:

- `00-Quick_Reference.md`: update the PING row.
- `03-Manager_Architecture.md`: update [MA-1.7] to mention inherited enriched
  PONG behavior.
- `05-Message_Flow_and_State.md`:
  - update [MF-3] with structured PING input and enriched PONG output
  - update [MF-5] with `live_pong` active evidence precedence
  - update implementation mapping if a new helper or function owns probing
  - add this plan under implementation plan backlinks
- `07-System_Invariants.md`: add or update an observability invariant only if
  implementation makes PONG precedence a durable invariant.
- `10-CLI_Interface.md`: document any additive JSON fields or flags.
- If Docker runtime details are exposed in public JSON snapshots, document that
  the `runtime` object is best-effort and runner-specific, with Docker metadata
  coming from `RunnerRuntimeDescription`.

Stop and re-evaluate if:

- The docs are being changed to justify behavior that moved beyond this plan,
  such as heartbeat services or project-wide active scans.

Per-task verification:

```bash
rg -n "PING|PONG|live_pong|request_id|2026-05-07-extended-ping-pong-state-probe-plan" docs/specifications docs/plans/2026-05-07-extended-ping-pong-state-probe-plan.md
```

### 8. Run the focused and release-gate checks

Outcome: behavior is proven at the smallest relevant level and then at the
normal handoff gate.

Focused checks:

```bash
./.venv/bin/python -m pytest tests/tasks/test_control_channel.py -q
./.venv/bin/python -m pytest tests/commands/test_task_evidence.py -q
./.venv/bin/python -m pytest tests/commands/test_task_commands.py -q
./.venv/bin/python -m pytest tests/commands/test_interactive_client.py -q
```

Expanded checks:

```bash
./.venv/bin/python -m pytest tests/tasks tests/commands -q
./.venv/bin/python -m mypy weft extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft
```

Full handoff gate if time allows:

```bash
./.venv/bin/python -m pytest
```

Do not assume `pytest`, `mypy`, or `ruff` are installed globally. Use the
in-repo virtualenv commands above after loading `.envrc` or otherwise ensuring
the repo environment is active.

## 7. Review Gates

Before implementation starts:

- Confirm the implementer can answer the comprehension checks in Section 3.
- Confirm the intended public behavior is either:
  - additive PONG fields only, or
  - additive PONG fields plus an explicit known-TID active-probe path.
- Confirm project-wide `weft status` will not active-probe every task by
  default.

After Task 3:

- Review the runtime control hook changes before adding reconciliation. The
  highest-risk bug is breaking STOP/KILL/Pause/Resume while changing the hook
  signature.

After Task 5:

- Review the evidence precedence carefully. The highest-risk bugs are allowing
  stale or unmatched PONGs to override newer terminal evidence, or refusing to
  trust a fresh matched PONG even though the caller explicitly requested a
  current-state probe.

Before completion:

- Run an independent review if available. Suggested prompt:

  > Read `docs/plans/2026-05-07-extended-ping-pong-state-probe-plan.md`, the
  > touched specs, `weft/core/tasks/base.py`, `weft/core/tasks/pipeline.py`,
  > and `weft/commands/task_evidence.py`. Look for contract drift, broken
  > control semantics, weak test design, and ambiguity. Could you implement or
  > sign off on this confidently?

If no different agent family is available, record that limitation in the
implementation notes and do a same-family review.

## 8. Self-Review Notes

First-pass ambiguities found and fixed while writing this plan:

- `idempotency_key` was the wrong term. The plan now uses `request_id` because
  PING is read-only and the field is for correlation.
- PONG should not be called "weak evidence." The plan now treats matched PONG
  as authoritative live task-local evidence at its timestamp.
- Project-wide active probing is too broad for this slice. The plan now limits
  active probes to known-task paths or explicit opt-in APIs.
- Full TaskSpec/runtime-handle dumps in PONG would expand the contract and
  risk leaking data. The plan now requires a small redacted snapshot only.
- The active probe could accidentally consume terminal `ctrl_out` messages.
  The plan now requires non-consuming matching or exact-message ACK only for
  the matched PONG.
- The first draft overcorrected by treating Docker-specific PONG data as out
  of scope. That was wrong for the product goal: PONG is most useful when PID
  reconciliation is weak, so Docker runtime facts should be included when they
  come from the existing runner plugin description path.

Second-pass review result:

- The plan still implements the discussed direction: enriched PONG, echoed
  request ID, and current-state reconciliation through a bounded keyed active
  probe.
- It does not require a materially different design such as heartbeat leases,
  periodic scans, or a second Docker-specific runtime management path. It does
  include Docker-specific observability through the existing runner plugin
  contract.
- The riskiest unresolved product choice is whether `weft task status TID`
  should active-probe automatically or require an explicit flag/API parameter.
  The implementation should decide this before changing CLI behavior and then
  update [CLI-1.2] / [CLI-1.2.1] accordingly.

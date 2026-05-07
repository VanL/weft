# Manager Selection PING/PONG Liveness Plan

Status: completed
Source specs: see Source Documents below
Superseded by: none

## 1. Goal

Improve manager selection and manager-startup convergence by using the
structured `PING`/`PONG` control contract as an optional positive liveness
probe for candidate manager records. A matched manager PONG should prove that a
specific manager task is alive and owns its task-local control surface at the
probe timestamp. Missing PONG must not, by itself, prove death or authorize
takeover.

This is a manager-election hardening slice. Keep it narrow: no new queue names,
no new manager lifecycle states, no second election algorithm, no locks, no
background health daemon, and no command-layer imports from core runtime code.

## 2. Source Documents

Source specs:

- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md)
  [MA-0], [MA-1.4], [MA-1.7], [MA-3]
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-3], [MF-5], [MF-6]
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [MANAGER.3], [MANAGER.6], [MANAGER.7], [MANAGER.8], [MANAGER.9],
  [MANAGER.10], [MANAGER.12], [MANAGER.13], [MANAGER.14]
- [`docs/specifications/00-Quick_Reference.md`](../specifications/00-Quick_Reference.md)
  "Control Messages" and "Queue Names"

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

- [`2026-05-07-extended-ping-pong-state-probe-plan.md`](./2026-05-07-extended-ping-pong-state-probe-plan.md)
  introduced the structured keyed `PING`/`PONG` task-local status probe. This
  plan reuses that contract for manager liveness, but moves the reusable wire
  helper into `weft/core/` so core manager code does not import command code.
- [`2026-05-07-result-evidence-and-superseded-manager-reconciliation-plan.md`](./2026-05-07-result-evidence-and-superseded-manager-reconciliation-plan.md)
  aligned task status with the selected active-manager registry view.
- [`2026-04-24-manager-status-container-pid-liveness-plan.md`](./2026-04-24-manager-status-container-pid-liveness-plan.md)
  hardened container PID liveness for manager status.
- [`2026-04-17-canonical-owner-fence-plan.md`](./2026-04-17-canonical-owner-fence-plan.md)
  defines the lowest-live-TID ownership fence that this plan must preserve.

Spec delta completed with this slice:

- Update [MA-1.4] and [MA-3] to say candidate manager liveness may use matched
  keyed PONG as positive live evidence, but missing PONG is only suspect
  evidence and cannot alone select a replacement manager.
- Update [MA-1.7] and [MF-3] to include manager-specific PONG fields when the
  responding task is a manager: `role`, `requests`, `ctrl_in`, `ctrl_out`,
  `outbox`, and `weft_context`.
- Update [MF-5] only if public status/reconciliation output gains additive
  diagnostics for manager PONG probes.
- Update [MANAGER.8] / [MANAGER.9] nearby text if the implementation clarifies
  that PONG affects liveness proof, not the lowest-TID leadership rule.
- Add backlinks from touched spec sections to this plan.

## 3. Current Context And Key Files

Current structure:

- Managers are normal `BaseTask` subclasses in `weft/core/manager.py`.
- Managers register active/stopped records in `weft.state.managers`.
- Manager startup and foreground serve share the helper code in
  `weft/core/manager_runtime.py`.
- Manager selection currently reduces the manager registry to the latest record
  per TID, filters canonical `role="manager"` records bound to
  `weft.spawn.requests`, prunes stale active records, and chooses the lowest
  live canonical TID.
- `BaseTask` already parses structured control requests through
  `ControlRequest` and emits keyed `PING` responses with `message="PONG"` to
  `ctrl_out`.
- `weft/commands/task_evidence.py` currently owns the command-layer active PONG
  probe helper. Core manager runtime must not import that module.

Files to modify:

- `weft/core/control_probe.py` (new)
  - Own the shared keyed PING/PONG wire helper.
  - This module belongs in `core` because both core manager runtime and command
    evidence readers need it.
  - Keep it queue/protocol focused. It must not know task status precedence,
    manager election policy, CLI output, or result materialization.

- `weft/core/tasks/base.py`
  - Read existing `ControlRequest`, `_parse_control_request()`,
    `_handle_control_command()`, `_control_snapshot_fields()`,
    `_send_control_response()`, and `_control_runtime_summary()`.
  - Likely no major behavior change is needed here because keyed PONG already
    exists.
  - If the new helper needs stricter PONG shape, update the base producer and
    its tests in the same slice.

- `weft/core/manager.py`
  - Add manager-specific fields to the PONG snapshot by overriding
    `_control_snapshot_fields()` or by adding a small manager-owned hook that
    calls `super()`.
  - Do not bypass `BaseTask._handle_control_command()`. Managers should still
    inherit the ordinary PING path.

- `weft/core/manager_runtime.py`
  - Use the new core PONG probe when evaluating candidate manager records.
  - Keep registry replay, stale pruning, active selection, foreground serve,
    and bootstrap on the existing shared path.
  - Do not import `weft.commands.task_evidence`.

- `weft/commands/task_evidence.py`
  - Replace its local PING/PONG wire implementation with the new core helper.
  - Keep command-layer evidence classification in this file. Do not move
    `TaskEvidenceSnapshot`, task evidence precedence, or result/status
    reconciliation into core.

- `weft/_constants.py`
  - Add narrowly scoped constants only if needed for probe timeout/interval or
    classification names.
  - Prefer reusing existing `CONTROL_SURFACE_WAIT_TIMEOUT` and
    `CONTROL_SURFACE_WAIT_INTERVAL` unless manager selection needs a shorter
    default. If adding a manager-specific timeout, document why it differs.

- `tests/core/test_control_probe.py` (new)
  - Unit tests for the core wire helper using real broker-backed queues.

- `tests/core/test_manager.py` or `tests/commands/test_manager_commands.py`
  - Add manager-selection tests near existing manager lifecycle tests. Use the
    existing file that already covers `_select_active_manager`,
    `_manager_record_is_stale`, and list/start behavior.

- `tests/commands/test_task_evidence.py`
  - Update active PONG evidence tests so they still prove command-layer
    behavior after the wire helper moves into core.

- `tests/tasks/test_control_channel.py`
  - Add or update tests for manager-specific PONG fields if no existing task
    control test covers them.

- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/00-Quick_Reference.md` if the quick reference should
  mention manager-specific PONG fields.
- `docs/lessons.md` only if implementation exposes a reusable failure mode not
  already captured.

Files to read before editing:

- `weft/core/tasks/base.py`
  - Understand `ControlRequest`, structured control parsing, PONG output, queue
    handle ownership, and defensive plugin-description behavior.
- `weft/core/manager.py`
  - Understand manager registration, heartbeat refresh, active-manager record
    shape, dispatch ownership, final dispatch fence, and yield behavior.
- `weft/core/manager_runtime.py`
  - Understand `_snapshot_registry()`, `_registry_view()`,
    `_select_active_manager_from_snapshot()`, `_manager_record_is_stale()`,
    `_ensure_manager()`, `_await_manager_start_settlement()`,
    `_start_manager()`, `_serve_manager_foreground()`, and public wrappers.
- `weft/commands/task_evidence.py`
  - Understand the current command-layer PONG helper before moving only the
    wire-level parts.
- `weft/core/queue_wait.py`
  - Reuse `QueueChangeMonitor` for bounded waits across `ctrl_out` when that
    is simpler and correct. Do not invent a new polling abstraction unless the
    existing one cannot express the probe.
- `weft/helpers.py`
  - Read `iter_queue_json_entries()`, `is_canonical_manager_record()`,
    `canonical_owner_tid()`, `pid_is_live()`, and scoped PID helpers.
- `tests/helpers/weft_harness.py`
  - Use it for end-to-end manager/task proofs. Do not replace broker/process
    behavior with broad mocks.

Comprehension checks before editing:

- What queue proves a manager answered a PING? Answer: the manager task-local
  `ctrl_out` queue, usually `T{manager_tid}.ctrl_out`, with a matched
  `request_id`.
- Which queue carries the current manager registry? Answer:
  `weft.state.managers`.
- What makes a manager canonical for default dispatch? Answer:
  `role="manager"` and `requests="weft.spawn.requests"`, then lowest live TID.
- What does PONG decide? Answer: liveness evidence for one TID at one
  timestamp. It does not decide leadership by itself.
- What should happen if a PING times out? Answer: the candidate is suspect or
  unproven by PONG. Existing stale-record rules still decide whether it can be
  pruned or ignored.
- Why must the wire helper live in `weft/core/`? Answer: core manager runtime
  needs it, and core modules must not import from `weft.commands.*`.

## 4. Invariants And Constraints

Preserve these invariants:

- TID format and immutability do not change.
- Manager selection remains lowest-live-TID among canonical managers for
  `weft.spawn.requests`.
- Only positive `self` ownership authorizes manager child launch.
- `other`, `none`, and `unknown` still prevent new child launch at the final
  dispatch fence.
- A missing PONG is not death proof by itself.
- A matched PONG is positive live evidence only for the responding task TID and
  only at the response timestamp.
- PONG must not mutate task state, publish lifecycle events, publish terminal
  envelopes, reserve spawn requests, or write manager registry records.
- A PING probe is still a queue write. Do not run it in unbounded loops or on
  broad status/list scans; otherwise a dead manager's `ctrl_in` queue becomes
  new stale state.
- Runtime-only queues stay runtime-only. Do not make `weft.state.managers` or
  PONG reports part of dump/load or archive truth.
- Queue history reads over `weft.state.managers`, `weft.log.tasks`, and
  `ctrl_out` histories must use generator-based reads or queue-native waits,
  not guessed fixed limits.
- Core code must not import `weft.commands.*`.
- Command-layer code may import the new core helper.
- No new dependency.
- No new public CLI option in the first implementation unless a test proves it
  is necessary for diagnosis.

Error-priority rules:

- Corrupt or malformed PONG is ignored as non-matching. It must not crash
  manager selection.
- Failure to write a PING or read `ctrl_out` is suspect evidence only. It must
  not authorize takeover alone.
- Failure to collect optional PONG runtime details must not suppress the core
  PONG response.
- If the PONG helper cannot confidently read queue state, manager ownership
  evaluation should return or preserve `unknown` rather than launch work.
- Stale registry pruning remains best effort. A failed stale-row delete must
  not block a correct active-manager view.

Out of scope:

- Changing SimpleBroker semantics.
- Adding lock tables, SQL shortcuts, advisory locks, or lease rows.
- Adding a manager health daemon.
- Running active PING probes across every task in `weft status`.
- Treating missing PONG as failure for ordinary task status.
- Changing task result materialization.
- Changing bridge smoke, Wazuh, host-policy, LLM schema, or other domain task
  behavior.
- Cleaning or pruning queues as part of manager election.

Stop and re-plan if:

- The implementation wants to import `weft.commands.task_evidence` from
  `weft/core/manager_runtime.py`.
- Missing PONG becomes sufficient to select a replacement manager.
- A second manager startup path appears instead of extending
  `weft/core/manager_runtime.py`.
- Tests require broad mocking of `simplebroker.Queue`, manager lifecycle, or
  control queues.
- The plan starts adding public CLI flags, background services, or deletion
  behavior to make the design work.

## 5. Architecture Decision

Add a small core PING/PONG probe helper and reuse it from both manager runtime
and command evidence.

Recommended shape:

```text
weft/core/control_probe.py
  MatchedPong
  ControlProbeResult
  coerce_pong_response(...)
  send_keyed_ping_probe(...)
```

The exact names can change if existing style suggests better names, but keep
the responsibilities narrow:

- build a unique request ID
- write `{"command": "PING", "request_id": ...}` to a caller-provided
  `ctrl_in` queue
- wait briefly for a matching PONG on a caller-provided `ctrl_out` queue
- parse only matched PONGs for the expected `tid` and `request_id`
- return a typed result that distinguishes:
  - `matched`: a valid PONG was observed
  - `timeout`: no matching PONG arrived before deadline
  - `probe_error`: the helper could not write/read safely
- close queues it opens
- avoid state mutation beyond writing the control request

The helper must not:

- know manager-election policy
- classify task evidence
- call status/result code
- inspect Docker or host process state directly
- update `weft.state.managers`
- delete old PONGs or control messages

Manager runtime then layers policy on top:

- If a manager record is otherwise canonical and a matched PONG proves it is
  alive, treat it as live for this registry view.
- If a manager record is stale by existing hard rules and no PONG proves it
  alive, keep the existing stale behavior.
- If a record is not stale by existing rules but PING times out, do not prune
  it only because of the timeout. At most expose suspect diagnostics in local
  variables or logs.
- If queue read/write failure makes the probe uncertain, do not launch work
  based on that uncertainty.
- Keep `_manager_record_is_stale()` a pure record/handle/timestamp predicate.
  Add probing as an explicit optional selection mode around registry-view
  construction rather than hiding queue writes inside the stale predicate.
- Do not enable active probing by default for broad public reads such as
  `weft manager list`, `weft status`, or task-list reconstruction. Start with
  the paths that are actively trying to start or settle a manager, such as
  `_ensure_manager()` and `_await_manager_start_settlement()`.
- In any loop that waits for manager settlement, probe at most once per
  candidate TID per settlement attempt, or use a small in-memory per-call
  throttle. Do not write a fresh PING on every registry poll.

This preserves the current safety bias: PONG can prevent false death; missing
PONG cannot create false life or authorize split brain.

Counterargument and response:

- Counterargument: This will not always shorten a deploy handoff where the old
  external-supervisor heartbeat is still inside its grace window.
- Response: Correct. Shortening handoff based on missing PONG alone would be a
  materially different and riskier election rule. This plan chooses stronger
  correctness and better live proof first. A later plan can tune heartbeat
  grace or add explicit supervisor-generation identity if ops needs faster
  takeover.

## 6. Bite-Sized Tasks

### 1. Write failing tests for the core PONG wire helper

Outcome: prove the reusable helper can send a keyed PING and match exactly the
right PONG without command-layer code.

Files to touch:

- `tests/core/test_control_probe.py` (new)

Read first:

- `weft/commands/task_evidence.py::probe_live_pong_evidence()`
- `weft/commands/task_evidence.py::coerce_pong_response()`
- `weft/core/queue_wait.py`
- `docs/agent-context/runbooks/testing-patterns.md`

Test requirements:

- Use real broker-backed queues through `build_context()` or existing broker
  fixtures. Do not mock `simplebroker.Queue`.
- Test that a matching PONG is accepted only when:
  - `command` is `PING`
  - `status` is `ok`
  - `message` is `PONG`
  - `tid` equals the expected TID
  - `request_id` equals the generated request ID
  - `task_status` is a non-empty string
- Test that stale PONGs with the wrong request ID are ignored.
- Test that PONGs for the wrong TID are ignored.
- Test timeout returns a non-matched result, not an exception.
- Test malformed JSON is ignored.

Implementation guidance:

- It is acceptable for the first red test to import a not-yet-created helper
  from `weft.core.control_probe`.
- Prefer small queue fixtures over full `WeftTestHarness` here because this
  helper is queue-protocol code, not process lifecycle code.

Done signal:

- The new tests fail because `weft.core.control_probe` does not exist or lacks
  the required behavior.

Stop-and-re-evaluate:

- If the test needs to start a real manager just to test PONG matching, split
  it. Wire matching belongs in this task; manager process behavior belongs in
  later tasks.

### 2. Implement `weft/core/control_probe.py`

Outcome: provide one core-owned keyed PING/PONG helper that manager runtime and
command evidence can both reuse.

Files to touch:

- `weft/core/control_probe.py` (new)
- `weft/_constants.py` only if a new timeout/interval constant is truly needed

Read first:

- `weft/core/queue_wait.py`
- `weft/context.py`
- `weft/core/tasks/base.py::ControlRequest`
- `weft/commands/task_evidence.py::probe_live_pong_evidence()`

Implementation requirements:

- Add `from __future__ import annotations`.
- Use dataclasses with `frozen=True, slots=True` for simple return values.
- Accept explicit `ctrl_in_name` and `ctrl_out_name` arguments. Do not infer
  manager queue names inside the helper.
- Accept `tid`, `timeout`, and optional `request_id`.
- Use `WeftContext.queue()` to open queues.
- Close every queue opened by the helper.
- Use JSON envelopes for PING:
  `{"command": CONTROL_PING, "request_id": request_id}`.
- Match PONG strictly. Do not accept legacy raw `PONG`.
- Use `peek_generator(with_timestamps=True)` or `QueueChangeMonitor`; do not
  use fixed-limit reads.
- Return structured non-match/error information instead of throwing for
  ordinary malformed queue content.
- Let truly invalid caller input fail early with `ValueError` only for local
  programming errors such as empty queue names or empty TID.

YAGNI boundaries:

- Do not add STOP/STATUS helpers.
- Do not add manager-specific policy to this helper.
- Do not add async support.
- Do not delete old PONGs from `ctrl_out`.
- Do not expose CLI output from this module.

Tests:

- Make `tests/core/test_control_probe.py` pass.

Done signal:

- `./.venv/bin/python -m pytest tests/core/test_control_probe.py -q` passes.

### 3. Reuse the core helper from command-layer task evidence

Outcome: remove duplicate PING/PONG wire logic from
`weft/commands/task_evidence.py` while preserving public task evidence
behavior.

Files to touch:

- `weft/commands/task_evidence.py`
- `tests/commands/test_task_evidence.py`

Read first:

- `weft/commands/task_evidence.py`
- `tests/commands/test_task_evidence.py`
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]

Implementation requirements:

- Import the new core helper from `weft.core.control_probe`.
- Keep `TaskEvidenceSnapshot`, `reconciliation_for_live_pong()`, and
  task-evidence precedence in `weft/commands/task_evidence.py`.
- Replace local `coerce_pong_response()` and raw probe loop only if doing so
  does not change command-layer semantics.
- If command-layer tests depend on `coerce_pong_response()` as a public-ish
  helper, keep a thin wrapper that delegates to core rather than duplicating
  parsing.
- Preserve the existing PONG evidence priority: terminal task-log and terminal
  `ctrl_out` evidence still beat equal or newer terminal evidence rules as
  specified in [MF-5].

Tests:

- Add/update tests that prove `probe_live_pong_evidence()` still returns
  `classification="live_pong"` with `request_id` metadata.
- Add/update tests that stale wrong-request PONGs are ignored.
- Do not rewrite existing task-evidence tests around mocks.

Done signal:

- `./.venv/bin/python -m pytest tests/commands/test_task_evidence.py -q`
  passes.

Stop-and-re-evaluate:

- If this step starts moving task evidence policy into `weft/core/`, stop.
  Core owns the wire helper only; commands own public evidence policy.

### 4. Add manager-specific fields to manager PONG

Outcome: a manager's matched PONG proves not only that the task answered, but
also that the answering task identifies as the default manager for the expected
request queue.

Files to touch:

- `weft/core/manager.py`
- `tests/tasks/test_control_channel.py` or a manager-specific test file already
  used for manager control behavior

Read first:

- `weft/core/manager.py::_register_manager()`
- `weft/core/manager.py::_queue_names`
- `weft/core/tasks/base.py::_control_snapshot_fields()`
- `docs/specifications/03-Manager_Architecture.md` [MA-1.7]

Implementation requirements:

- Add an override that calls `super()._control_snapshot_fields()` and then adds
  manager fields.
- Include these additive fields for manager PONG/STATUS snapshots:
  - `role`: `"manager"`
  - `requests`: manager request queue, normally `weft.spawn.requests`
  - `ctrl_in`: this manager's control input queue
  - `ctrl_out`: this manager's control output queue
  - `outbox`: manager outbox queue
  - `weft_context`: effective context path if already available from the
    TaskSpec
- Do not include a full TaskSpec dump.
- Do not change ordinary non-manager task PONG shape except for already
  specified fields.
- Do not make PONG write a manager registry heartbeat. Registry refresh remains
  registry-owned behavior.

Tests:

- Use a real manager instance if an existing lightweight manager construction
  helper exists. Otherwise unit-test the snapshot method directly with a
  manager TaskSpec built by `build_manager_spec()`.
- Assert additive fields exist in the manager PONG payload.
- Assert raw PING still works for managers.
- Assert structured PING echoes `request_id`.

Done signal:

- Targeted manager/control tests pass.

Stop-and-re-evaluate:

- If adding manager PONG fields requires changing TaskSpec immutability or
  hand-rolled queue defaults, stop. Use existing `_queue_names` and manager
  TaskSpec construction instead.

### 5. Add manager-record PONG probing to manager runtime selection

Outcome: active manager selection can use matched PONG as positive live
evidence for candidate manager records without changing lowest-TID leadership.

Files to touch:

- `weft/core/manager_runtime.py`
- `tests/commands/test_manager_commands.py` and/or `tests/core/test_manager.py`

Read first:

- `weft/core/manager_runtime.py::_snapshot_registry()`
- `weft/core/manager_runtime.py::_select_active_manager_from_snapshot()`
- `weft/core/manager_runtime.py::_manager_record_is_stale()`
- `weft/core/manager_runtime.py::_ensure_manager()`
- `weft/core/manager_runtime.py::_await_manager_start_settlement()`
- `weft/helpers.py::is_canonical_manager_record()`
- `docs/specifications/03-Manager_Architecture.md` [MA-1.4], [MA-3]
- `docs/specifications/07-System_Invariants.md` [MANAGER.8], [MANAGER.9]

Recommended implementation shape:

- Add a small helper in `manager_runtime.py`, for example
  `_probe_manager_record_liveness(context, record, timeout=...)`.
- The helper should:
  - reject non-canonical records before probing
  - read `tid`, `ctrl_in`, and `ctrl_out` from the registry record
  - fall back to `T{tid}.ctrl_in` / `T{tid}.ctrl_out` only if those fields are
    absent, matching existing manager TaskSpec defaults
  - call the new core `send_keyed_ping_probe()`
  - require matched PONG `tid`
  - require PONG fields `role="manager"` and
    `requests="weft.spawn.requests"` when those fields are present
  - treat missing manager-specific PONG fields from older managers as live
    only during the compatibility window if the matched PONG otherwise proves
    the expected TID; add a test for this if supporting rolling upgrades
  - return a narrow enum/string such as `live`, `unmatched`, `error`, not a
    broad dictionary policy blob
- Add an explicit optional probe path around registry-view construction, for
  example `_registry_view(..., probe_stale_managers=False, probe_cache=None)`.
  The exact shape can differ, but the default behavior for existing broad
  readers must stay non-probing.
- Use the optional probe path from startup/settlement code first:
  `_ensure_manager()` and `_await_manager_start_settlement()` are the primary
  owners because they are already trying to decide whether a manager must be
  started or adopted.
- If dispatch ownership later needs the same proof, reuse the same helper, but
  keep dispatch probing bounded and preserve `unknown` on uncertainty.
- Integrate probing where it helps stale/live decisions:
  - If a record would be considered stale by `_manager_record_is_stale()`, a
    matched PONG may rescue it as live for that registry view.
  - If a record is not stale by existing rules, failed PING must not make it
    stale by itself.
  - If several canonical records are live or PONG-proven live, continue to
    choose the lowest TID.

Important layering rule:

- Do not call `weft.commands.task_evidence.probe_live_pong_evidence()` from
  this file. That would invert the layer boundary.

Tests:

- Test stale external-supervisor record with matched manager PONG remains
  selectable/live.
- Test stale external-supervisor record with no PONG follows existing stale
  behavior.
- Test non-stale external-supervisor record with no PONG is not pruned only
  because the PING timed out.
- Test two canonical managers where both PONG: lower TID wins.
- Test two canonical managers where lower TID PONGs and higher TID PONGs:
  lower TID wins.
- Test higher TID PONG does not beat lower TID hard-live evidence.
- Test malformed PONG or wrong `request_id` does not count.
- Test non-manager PONG (`role` absent in new mode or `role!="manager"`) does
  not become manager-election proof once the compatibility window is removed.
- Test a settlement loop does not write repeated PINGs for the same candidate
  TID on every registry poll.

Done signal:

- Targeted manager runtime/command tests pass.

Stop-and-re-evaluate:

- If the implementation starts probing every registry record on every
  `manager list` or `weft status` call and makes those commands slow on large
  ops queues, stop. Active probing should be narrow and bounded.
- If the implementation writes more than one PING per candidate per bounded
  manager-start settlement attempt without a clear throttle, stop. The probe is
  becoming a stale-message generator.

### 6. Preserve dispatch ownership and startup semantics

Outcome: the new liveness proof strengthens existing manager behavior without
creating a second election path.

Files to touch:

- `weft/core/manager_runtime.py`
- `weft/core/manager.py` only if manager-side dispatch ownership needs to call
  a shared runtime helper
- Existing manager dispatch tests near:
  - `tests/commands/test_run.py`
  - `tests/core/test_manager.py`
  - `tests/cli/test_cli_serve.py`

Read first:

- `weft/core/manager.py::_evaluate_dispatch_ownership()`
- `weft/core/manager.py::_apply_final_dispatch_fence()`
- `weft/core/manager.py::_refresh_dispatch_suspension()`
- `weft/core/manager.py::_maybe_yield_leadership()`
- `docs/specifications/03-Manager_Architecture.md` current ownership-fence
  rules

Implementation guidance:

- Prefer limiting active PONG probing to `manager_runtime.py` selection first.
- If manager dispatch also needs PONG-aware liveness, factor only the smallest
  pure candidate-liveness helper that both startup and dispatch can call.
- Do not make a manager launch child work because another manager failed to
  PONG. The final dispatch fence still needs positive `self`.
- Preserve `unknown` and `none` behavior. If PONG probing fails due to broker
  uncertainty, the manager should suspend rather than launch.

Tests:

- Add an integration-style regression where a stale lower-TID manager record
  with a matched PONG prevents a higher-TID manager from becoming selected.
- Add a regression where an unanswered PING to a not-yet-stale lower-TID record
  does not allow a higher-TID manager to launch a child.
- Add a regression where an unanswered PING to an already-stale record does not
  block startup if existing stale criteria already prove it stale.
- Add a regression that manager settlement does not enqueue repeated PINGs to
  the same old manager control queue while waiting.

Done signal:

- Existing manager startup, serve, and dispatch-fence tests still pass.

Stop-and-re-evaluate:

- If a test requires sleeping for correctness instead of bounded queue waits or
  harness helpers, redesign the test.

### 7. Update specs and traceability

Outcome: specs describe the new steady-state contract, and the plan/code/spec
chain is bidirectional.

Files to touch:

- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/00-Quick_Reference.md` if needed
- Module docstrings in `weft/core/control_probe.py`,
  `weft/core/manager_runtime.py`, and any materially changed function
  docstrings

Required spec updates:

- Add this plan to the relevant `Related Plans` or implementation backlink
  sections.
- In [MA-1.4], document PONG as positive liveness evidence for manager records.
- In [MA-1.7] / [MF-3], document manager-specific PONG fields.
- In [MANAGER.8] / [MANAGER.9], clarify that PONG influences live proof only;
  it does not replace lowest-live-TID selection or positive `self` ownership.
- Keep the specs normative. Do not leave the plan as the only place describing
  the behavior after implementation lands.

Tests:

- Documentation-only changes do not need new tests, but spec examples must not
  contradict the implemented JSON payload shape.

Done signal:

- A zero-context reader can start at the spec, reach this plan, then reach the
  owning modules.

### 8. Verification gate

Outcome: the implementation is proven locally before release.

Required commands:

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/core/test_control_probe.py -q
./.venv/bin/python -m pytest tests/commands/test_task_evidence.py -q
./.venv/bin/python -m pytest tests/commands/test_manager_commands.py -q
./.venv/bin/python -m pytest tests/cli/test_cli_serve.py -q
./.venv/bin/python -m pytest tests/commands/test_run.py -q
./.venv/bin/ruff format --check weft tests
./.venv/bin/ruff check weft tests
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
```

If the touched tests live in different files after implementation, run the
nearest equivalent files and state the substitution in the handoff.

Additional confidence gate:

```bash
./.venv/bin/python -m pytest tests/specs/manager_architecture/ -q
```

Do not skip static checks because the functional change is small. This touches
core runtime typing and command-layer imports.

## 7. Test Design Notes

Prefer these test styles:

- Real broker-backed queues for PING/PONG matching.
- Real `WeftContext` and existing harness fixtures for manager startup and
  selection behavior.
- Small pure tests only for parsing helpers that do not need broker state.
- Bounded waits with `QueueChangeMonitor`, harness helpers, or explicit
  deadlines.

Avoid these test styles:

- Mocking `simplebroker.Queue` for queue ordering or visibility.
- Mocking the entire manager registry and then claiming election behavior is
  proven.
- Sleeping a fixed amount and assuming a manager had time to respond.
- Asserting implementation call order when queue-visible behavior is the real
  contract.

Specific invariants to assert:

- A matched PONG must include the expected `request_id`.
- A matched PONG must include the expected `tid`.
- Wrong-TID and wrong-request PONGs are ignored even if newer.
- Missing PONG does not change a non-stale manager record to stale.
- Matched PONG can keep an otherwise stale manager record live.
- Lowest live canonical TID still wins.
- Manager PONG fields are additive and do not break older task PONG readers.
- Command-layer task evidence still classifies live PONG evidence the same way
  as before the helper extraction.

## 8. Rollout And Rollback

Rollout:

- Ship the core helper and command-layer reuse first in the same release as the
  manager runtime integration. The helper extraction alone should not change
  behavior.
- Keep manager-specific PONG fields additive so old readers ignore them.
- Keep manager liveness probing compatible with older managers that can answer
  keyed PING but do not yet include manager-specific PONG fields, if rolling
  deploys can overlap versions.
- Prefer one Weft release for this slice. Deploy to ops, then observe manager
  restarts before moving to more aggressive handoff tuning.

Post-deploy observation on ops:

- `weft --version` shows the release containing this plan.
- `weft manager list --json` shows exactly one active manager for the
  governance context.
- `docker top governance-opsworker-serve-1` shows one manager process.
- During an opsworker restart, logs may show conservative refusal while an
  older manager is PONG-proven or otherwise live, but should not show two
  simultaneously selected active managers.
- If an older manager record is stale but still answers PONG, it should not be
  pruned or superseded only because its heartbeat row is old.

Rollback:

- Reverting this slice should leave existing registry and task queues readable
  because it adds no new required queue names and no destructive cleanup.
- Additive PONG fields can remain in old `ctrl_out` rows; old readers ignore
  ordinary control replies unless they match a keyed request.
- If manager probing causes unexpected latency, disable only the integration
  point in `manager_runtime.py` first. The core helper can remain because
  command evidence already uses the same control contract.
- Do not rollback by deleting `weft.state.managers` or task-local `ctrl_out`
  queues.

One-way doors:

- None intended. If implementation introduces destructive pruning, incompatible
  PONG payload requirements, or persisted election records, stop and rewrite
  the plan before continuing.

## 9. Review Checklist

Before implementation:

- Can a zero-context engineer identify every file to read and touch?
- Does the plan keep core free of command-layer imports?
- Is missing PONG clearly non-authoritative for death?
- Are manager-specific PONG fields additive?
- Does the plan preserve lowest-live-TID leadership?
- Are test seams real enough to catch queue/order bugs?

After implementation:

- Review the diff for accidental imports from `weft.commands` into
  `weft/core`.
- Review all PONG readers for duplicate parsing logic.
- Confirm manager runtime does not probe unboundedly on every status/list call.
- Confirm no public CLI shape changed unless specs and tests were updated.
- Confirm the specs have backlinks to this plan.

Independent review prompt:

> Read `docs/plans/2026-05-07-manager-selection-ping-pong-liveness-plan.md`,
> `docs/specifications/03-Manager_Architecture.md` [MA-1], [MA-3],
> `docs/specifications/05-Message_Flow_and_State.md` [MF-3], [MF-5], and the
> intended code owners in `weft/core/manager_runtime.py`,
> `weft/core/manager.py`, `weft/core/tasks/base.py`, and
> `weft/commands/task_evidence.py`. Look for errors, bad ideas, and latent
> ambiguities. Do not implement. Could you implement this confidently and
> correctly if asked?

## 10. Self-Review Notes

Fresh-eye review pass:

- Risk: The first draft could have implied that missing PONG shortens handoff.
  Correction: the plan now states repeatedly that missing PONG is only suspect
  evidence and cannot authorize takeover alone.
- Risk: The helper extraction could drift into moving task evidence policy into
  core. Correction: the plan gives `weft/core/control_probe.py` only the wire
  protocol and keeps `TaskEvidenceSnapshot` in commands.
- Risk: Manager-specific PONG fields could become a hard requirement and break
  rolling deploys. Correction: the manager runtime task explicitly calls out an
  optional compatibility window for older managers that already support keyed
  PING but do not emit the new manager fields.
- Risk: Active probing could make `weft status` slow on large ops queues.
  Correction: the plan scopes probing to manager selection/liveness checks and
  adds a stop gate for broad status/list probing.
- Risk: Tests could over-mock manager election. Correction: the test plan names
  real broker queues, `WeftContext`, and harness-backed manager tests as the
  preferred proof.

Second review pass:

- The plan remains aligned with the original direction: use extended PONG when
  testing possible manager liveness, preserve proper layering, and do not turn
  non-response into death proof.
- No material direction change was introduced during review.

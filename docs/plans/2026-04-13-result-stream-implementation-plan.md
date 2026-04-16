# Result Stream Implementation Plan

Status: proposed
Source specs: see Source Documents below
Superseded by: none

## Goal

Implement `weft result TID --stream` as a true single-task follow mode that
prints unread outbox stream chunks as they arrive while preserving the current
task-log boundary semantics, exit codes, and persistent-task batching rules.
The change must stay on the existing result waiter spine. It must not turn
`weft result` into a second interactive client, and it must not change
`weft run --wait` semantics unless the governing spec is updated separately.

## Source Documents

Source specs:

- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
  [TS-1], [TS-1.3]
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-5]
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [STATE.1], [STATE.2], [QUEUE.1], [QUEUE.2], [OBS.3], [IMPL.1], [IMPL.2],
  [IMPL.3]
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
  [CLI-1.2]
- [`docs/specifications/10A-CLI_Interface_Planned.md`](../specifications/10A-CLI_Interface_Planned.md)
  [10A-1]
- [`docs/specifications/11-CLI_Architecture_Crosswalk.md`](../specifications/11-CLI_Architecture_Crosswalk.md)
  [CLI-X2]
- [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
  [AR-4.1]

Repo guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)

Relevant current context:

- [`docs/lessons.md`](../lessons.md)
- [`docs/plans/2026-04-13-runner-monitor-result-waiter-and-liveness-fixes-plan.md`](./2026-04-13-runner-monitor-result-waiter-and-liveness-fixes-plan.md)

## Context and Key Files

Files to modify:

- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/specifications/10A-CLI_Interface_Planned.md`
- `docs/specifications/13-Agent_Runtime.md`
- `weft/commands/_result_wait.py`
- `weft/commands/_streaming.py`
- `weft/commands/result.py`
- `weft/commands/run.py`
- `tests/commands/test_result.py`
- `tests/cli/test_cli_result.py`
- `tests/cli/test_cli_result_all.py`
- `tests/commands/test_run.py` if the shared one-shot waiter signature changes

Read first:

- `weft/commands/result.py`
  - owns `cmd_result()`, task-id materialization, queue-name lookup, and the
    current persistent-task boundary logic
- `weft/commands/_result_wait.py`
  - owns the shared one-shot waiter used by both `weft run` and
    `weft result`
- `weft/commands/_streaming.py`
  - already decodes `type="stream"` envelopes and already has the switch that
    decides whether to print chunks immediately
- `weft/commands/run.py`
  - imports the shared one-shot waiter and must remain non-streaming after this
    slice
- `tests/commands/test_result.py`
  - current queue-backed tests for grace handling, persistent work-item
    boundaries, and custom outbox materialization
- `tests/cli/test_cli_result.py`
  - current real CLI surface coverage for `weft result`
- `tests/cli/test_cli_result_all.py`
  - current `--all` contract, including the rule that active streaming tasks do
    not appear there
- `weft/commands/interactive.py`
  - read only to confirm what is out of scope: this is the live stdin client,
    not the model for `weft result --stream`

Style and guidance:

- `AGENTS.md`
- `docs/agent-context/engineering-principles.md`

Shared paths and helpers to reuse:

- `weft.commands.result._await_result_materialization(...)`
- `weft.commands._result_wait.await_one_shot_result(...)`
- `weft.commands._result_wait.append_public_value(...)`
- `weft.commands._result_wait.terminal_status_from_event(...)`
- `weft.commands._result_wait.terminal_error_message(...)`
- `weft.commands._streaming.process_outbox_message(...)`
- `weft.commands._streaming.drain_available_outbox_values(...)`
- `weft.commands._streaming.handle_ctrl_stream(...)`
- `weft.commands._streaming.poll_log_events(...)`
- `weft.commands._streaming.aggregate_public_outputs(...)`

Current structure:

- `weft/cli.py` exposes `--stream` with help text that promises incremental
  output, but `cmd_result()` does not branch on `stream` for single-task mode.
- `weft result --all` already rejects `--stream`. Keep that behavior.
- Both current waiters already render `ctrl_out` messages live through
  `handle_ctrl_stream(...)`. The missing piece is live outbox emission, not a
  second control-channel watcher.
- `_streaming.process_outbox_message(...)` already has `emit_stream=True` logic,
  but the result waiters call it with `emit_stream=False`, so stream envelopes
  are decoded and aggregated only after completion.
- `_await_single_result(...)` in `weft/commands/result.py` already treats
  `work_item_completed` and `work_completed` as the boundary for one persistent
  result batch. That boundary must stay intact.
- `weft run --wait` imports `await_one_shot_result(...)` from
  `weft/commands/_result_wait.py`. Any new knobs on the shared helper must
  default to the current collect-then-print behavior so `run` does not change
  accidentally.
- `tests/cli/test_cli_result_all.py` currently references
  `tests.tasks.sample_targets:streaming_echo`, but that helper does not exist in
  `tests/tasks/sample_targets.py`. Do not build the new `--stream` coverage
  around that missing helper. Use queue-driven writer threads or add a small
  dedicated sample target explicitly in this slice.

Comprehension checks before editing:

1. Which helper already prints `ctrl_out` stream/control messages live, and why
   does that mean `--stream` should not add a second `ctrl_out` watcher?
2. Which helper already knows how to decode outbox `type="stream"` envelopes,
   and what single flag currently keeps `weft result` from using it for live
   output?
3. For persistent tasks, which existing log event marks one result boundary,
   and how does the current code keep one `weft result` call from draining past
   that boundary?

## Invariants and Constraints

- Keep one result waiter spine. Do not add a second queue watcher, thread, or
  interactive-style client just for `weft result --stream`.
- Preserve the current terminal-state and completion-grace semantics for both
  one-shot and persistent tasks. `work_completed` may still precede final
  outbox visibility by a short grace window.
- Preserve TID format and immutability.
- Preserve forward-only state transitions and terminal-state immutability.
- Preserve queue contracts. No new queue names, queue roles, or result-boundary
  events.
- Preserve `weft result --all` as a non-streaming aggregation command. Do not
  widen it into a follow mode.
- Preserve `weft run --wait` behavior. Shared helper refactors must leave it in
  collect-then-print mode.
- `--stream --json` must be rejected explicitly. Do not invent ad hoc NDJSON,
  multiplexed JSON, or mixed text-plus-JSON output in this slice.
- Preserve current `--error` semantics. This slice should not invent a new
  stderr-only live-stream mode. `ctrl_out` keeps routing to stderr as it does
  today, and `--error` continues to matter only for deferred structured payload
  selection unless the governing spec is expanded separately.
- Already-emitted live stream text must not be replayed at the end of the
  command. Non-stream final payloads must still print once.
- Pipeline TID lookup must keep working. `weft result --stream` should still
  resolve the pipeline outbox via the pipeline TaskSpec path rather than
  assuming `T{tid}.outbox`.
- Interactive stdin/session behavior is out of scope. `weft result --stream`
  reads result queues only; it does not become a REPL client.
- Large-output references are out of scope. Do not fold in auto-dereference
  work while implementing `--stream`.
- No new dependency, no drive-by refactor, and no change to `weft.state.*`
  persistence rules.
- Keep broker-backed queues real in tests. A narrow monkeypatch around the
  output sink is acceptable only when the assertion is specifically about the
  CLI emission order.

Out of scope:

- redesigning interactive sessions
- changing task runtime stream envelope formats
- changing `weft result --all`
- adding a machine-readable streaming mode
- adding large-output auto-dereference

## Rollout and Rollback

This slice is low-risk in storage terms because it does not change persisted
queue names, event names, or envelope formats. The public contract change is
purely in how `weft result` consumes existing queues.

Rollout order:

1. update the current/planned specs so the contract is explicit,
2. extend the shared streaming helpers,
3. wire one-shot and persistent result waiters to use the new mode,
4. add CLI validation and regression coverage,
5. verify docs and tests together.

Rollback rules:

- Roll back the `weft result --stream` helper changes and CLI wiring together.
  Reverting only one side risks restoring the current mismatch between flag help
  and behavior.
- The task runtime producer path does not need rollback in this slice because
  stream envelopes already exist today.

## Tasks

1. Promote `--stream` from placeholder to current CLI contract.
   - Outcome:
     - the current spec states exactly what `weft result TID --stream` does,
       and the planned-spec companion stops claiming this baseline behavior is
       still deferred.
   - Files to touch:
     - `docs/specifications/10-CLI_Interface.md`
     - `docs/specifications/10A-CLI_Interface_Planned.md`
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/13-Agent_Runtime.md`
   - Read first:
     - `docs/specifications/10-CLI_Interface.md` [CLI-1.2]
     - `docs/specifications/10A-CLI_Interface_Planned.md` [10A-1]
     - `docs/specifications/05-Message_Flow_and_State.md` [MF-5]
     - `docs/specifications/13-Agent_Runtime.md` [AR-4.1]
   - Required contract details:
     - `--stream` is single-task only
     - `--stream` is incompatible with `--all` and `--json`
      - live `ctrl_out` rendering stays as-is
      - outbox `type="stream"` chunks are printed as they become readable
      - one-shot tasks still end on terminal log state plus grace
      - persistent tasks still return one completed work-item batch per call
      - `--error` keeps its current deferred-payload meaning and does not become
        a new stdout suppression switch for live outbox chunks
      - already-emitted stream text is not replayed as a final payload
   - Stop if:
     - the contract appears to require new queue types, new boundary events, or
       a machine-readable stream format
   - Done when:
     - the current spec no longer says `--stream` is only a placeholder
     - the planned companion no longer owns this baseline feature
     - all touched specs include plan backlinks

2. Extend the shared stream-decoding helpers so live-emitted text can be
   suppressed from final replay.
   - Outcome:
     - the outbox decoder can tell the waiter whether a final value was already
       printed live or still needs to be returned for normal CLI rendering.
   - Files to touch:
     - `weft/commands/_streaming.py`
     - `tests/commands/test_result.py`
   - Read first:
     - `weft/commands/_streaming.py`
     - current stream-envelope tests in `tests/tasks/test_task_execution.py`
   - Implementation rules:
     - reuse the existing JSON/base64 decoder path
     - add a small typed record or equivalent metadata so callers can
       distinguish:
       - stream text already emitted live
       - deferred values that still need final echo
     - update both:
       - `drain_available_outbox_values(...)`
       - the timestamp-bounded drain used by persistent tasks
     - do not duplicate stream parsing logic in `result.py`
   - Tests:
     - stream envelopes with `emit_stream=True` are marked as already emitted
     - non-stream payloads still come back as deferred values
     - final newline behavior stays unchanged
   - Stop if:
     - a second outbox parser appears outside `_streaming.py`
   - Done when:
     - callers can enable live emission without re-echoing the same text later

3. Teach the shared one-shot waiter to use live outbox emission when requested.
   - Outcome:
     - one-shot `weft result --stream` follows unread outbox stream chunks in
       real time while preserving current timeout and terminal-state semantics.
   - Files to touch:
     - `weft/commands/_result_wait.py`
     - `weft/commands/run.py`
     - `tests/commands/test_result.py`
     - `tests/commands/test_run.py` if helper signatures change
   - Read first:
     - `weft/commands/_result_wait.py`
     - `weft/commands/run.py`
   - Implementation rules:
     - add an explicit mode flag such as `emit_stream` or equivalent to
       `await_one_shot_result(...)`
     - keep `ctrl_out` draining exactly as it works today
     - in stream mode, drain outbox with live emission enabled each loop while
       still honoring:
       - terminal-state classification
       - completion grace
       - timeout exit-code `124`
     - preserve the default non-stream path so `weft run --wait` continues to
       collect and print once at the end
   - Tests:
     - one-shot backlog of stream chunks prints once and exits `0`
     - late outbox write after `work_completed` still lands during the grace
       window
     - timeout and failed-task paths preserve current error shaping even if some
       stream text was already emitted
   - Stop if:
     - `weft run --wait` starts streaming by default
     - the helper grows a second terminal-state interpretation table
   - Done when:
     - one-shot callers can opt into live outbox emission without altering
       existing non-stream callers

4. Teach the persistent-task waiter to stream within one existing work-item
   boundary.
   - Outcome:
     - `weft result TID --stream` on persistent tasks prints unread stream
       chunks as they appear but still returns after exactly one completed
       `work_item_completed` batch.
   - Files to touch:
     - `weft/commands/result.py`
     - `tests/commands/test_result.py`
   - Read first:
     - `_await_single_result(...)` in `weft/commands/result.py`
     - `docs/specifications/13-Agent_Runtime.md` [AR-4.1]
   - Implementation rules:
     - keep the current boundary model:
       - `work_item_completed` ends one persistent batch
       - terminal task states still end the whole wait
     - continue using the existing `first_pending_timestamp` /
       `boundary_timestamp` correlation so one call does not drain past the
       active batch
     - enable live outbox emission during the wait, but only suppress final
       replay for values that were already emitted
     - keep pipeline outbox lookup and pipeline `ctrl_out` handling unchanged
   - Tests:
     - two sequential persistent work-item batches still require two
       `weft result` calls
     - stream mode on the first call does not consume the second batch
     - pipeline TID streaming still resolves the pipeline outbox correctly
   - Stop if:
     - the implementation wants a new public boundary event
     - the log reader drifts to `peek_many(limit=...)` instead of generator
       reads
   - Done when:
     - persistent `--stream` follows live output and still exits after one batch

5. Wire CLI validation and output shaping in `cmd_result()`.
   - Outcome:
     - the flag matrix is explicit and the CLI prints the right thing exactly
       once.
   - Files to touch:
     - `weft/commands/result.py`
     - `tests/cli/test_cli_result.py`
     - `tests/cli/test_cli_result_all.py`
   - Read first:
     - `cmd_result()` in `weft/commands/result.py`
     - current CLI tests for `result`
   - Required behavior:
     - reject `--stream --all` (existing)
     - reject `--stream --json` (new)
     - if all visible success output was already emitted live, return success
       with an empty final payload so the CLI does not duplicate it
     - if the task produced deferred non-stream values, print those once at the
       end
     - preserve exit codes:
       - `0` success
       - `124` timeout
       - `1` failed / killed / cancelled
       - `2` bad usage / not found
   - CLI integration tests:
     - `weft result TID --stream` prints streamed text once
     - completed plain-result tasks still print once under `--stream`
     - `--stream --error` preserves current stderr-oriented deferred-payload
       semantics without inventing a new live stderr-only mode
     - `--stream --json` returns usage error
     - `--all --stream` rejection remains
   - Stop if:
     - output formatting starts diverging between helper-level live emission and
       final CLI echo
   - Done when:
     - the CLI surface matches the spec and no longer advertises behavior it
       does not perform

6. Verify docs, tests, and review together before landing.
   - Outcome:
     - the shipped contract, helper behavior, and tests all line up.
   - Files to touch:
     - any touched docs/tests from earlier tasks
   - Verification:
     - targeted tests first, then the broader related slice
     - doc text checked against the final helper/caller behavior
   - Stop if:
     - docs still describe `--stream` as unimplemented after code is green
     - a review finding reveals the plan changed the boundary semantics
   - Done when:
     - docs, code, and tests all describe the same contract

## Testing Plan

Keep the broker-backed result path real. Do not replace queue behavior with
mock-only tests.

Targeted command/helper tests:

- `./.venv/bin/python -m pytest tests/commands/test_result.py -q`
- `./.venv/bin/python -m pytest tests/commands/test_run.py -q`
  if the shared one-shot waiter signature changes

CLI surface tests:

- `./.venv/bin/python -m pytest tests/cli/test_cli_result.py -q`
- `./.venv/bin/python -m pytest tests/cli/test_cli_result_all.py -q`

Broader slice before landing:

- `./.venv/bin/python -m pytest tests/commands/test_result.py tests/cli/test_cli_result.py tests/cli/test_cli_result_all.py -q`
- `./.venv/bin/python -m ruff check weft tests`
- `./.venv/bin/python -m mypy weft`

Testing rules:

- Keep queues and task-log behavior real.
- A narrow monkeypatch around `typer.echo` is acceptable only for proving
  emission order or duplicate-suppression. Do not mock the queue readers or the
  task-log boundary logic.
- For live-stream regressions, prefer queue-driven background writers in
  `tests/commands/test_result.py`. If real CLI coverage needs a runnable sample
  target, add a small explicit helper in `tests/tasks/sample_targets.py` rather
  than relying on `streaming_echo`, which does not currently exist.
- Prefer regression tests that assert observable CLI output, exit code, queue
  consumption, and work-item boundaries over tests that reach into helper
  internals only.

## Review Plan

Independent review is required because this changes a public CLI contract and
crosses CLI, queue, and task-log boundaries.

Recommended review timing:

1. review this plan before implementation starts,
2. review the helper and waiter slice after tasks 2 through 4,
3. review the final change before landing.

Reviewer preference:

- use a different agent family if available
- if only same-family review is available, record that limitation in the review
  notes

Suggested review prompt:

> Read the plan at `docs/plans/2026-04-13-result-stream-implementation-plan.md`.
> Carefully examine the plan and the associated `weft result` code paths. Look
> for errors, bad ideas, and latent ambiguities. Do not implement anything.
> Could you carry out this plan confidently and correctly as written?

# Task Evidence Reconciliation Model Plan

Status: draft
Source specs: see Source Documents below
Superseded by: none

Parent plan:
[`docs/plans/2026-05-06-lifecycle-reconciliation-architecture-plan.md`](./2026-05-06-lifecycle-reconciliation-architecture-plan.md)
Release slice: Release 2

## 1. Goal

Introduce one shared, read-only task evidence model for classifying task-log,
typed terminal `ctrl_out`, final one-shot outbox, runtime mapping, and stale
observer evidence. `weft status`, `weft task status`, known-TID terminal
snapshots, and result helpers should stop reimplementing their own evidence
priority rules. This release should make wrapper-loss and result-without-log
cases visible and coherent, without adding cleanup, deleting messages, or
adding new public task states.

This is an engineering-excellence slice. The correct path is one DRY evidence
reader with thin command wrappers. Do not patch symptoms separately in status,
task, and result code.

## 2. Source Documents

Source specs:

- [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md)
  [CC-2.2], [CC-2.3], [CC-3.2], [CC-3.4]
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-2], [MF-3], [MF-5], [MF-6], "Cleanup Boundary",
  "Queue Management Patterns"
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [STATE.1], [STATE.2], [STATE.4], [STATE.6], [OBS.1], [OBS.2],
  [OBS.3], [OBS.10], [OBS.11], [CTX.3]
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
  [CLI-1.2.1], [CLI-1.2], [CLI-6]
- [`docs/specifications/13C-Using_Weft_With_Django.md`](../specifications/13C-Using_Weft_With_Django.md)
  [DJ-2.2], [DJ-8.3]

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

- [`docs/plans/2026-05-06-lifecycle-reconciliation-architecture-plan.md`](./2026-05-06-lifecycle-reconciliation-architecture-plan.md)
  defines the release train and this Release 2 boundary.
- [`docs/plans/2026-05-06-status-coherence-and-stale-pid-liveness-plan.md`](./2026-05-06-status-coherence-and-stale-pid-liveness-plan.md)
  is Release 1. It made terminal task-log proof win over runtime liveness and
  added the first `reconciliation` diagnostics.
- [`docs/plans/2026-04-30-known-tid-terminal-snapshot-api-plan.md`](./2026-04-30-known-tid-terminal-snapshot-api-plan.md)
  added known-TID terminal snapshot helpers that already peek typed
  `ctrl_out`, final outbox, runtime mapping, and bounded log fallback. Reuse
  those semantics, but do not keep them isolated in `weft/commands/tasks.py`.
- [`docs/plans/2026-04-30-task-log-cursor-high-water-mark-plan.md`](./2026-04-30-task-log-cursor-high-water-mark-plan.md)
  tightened append-only log cursor behavior. Keep generator-based replay; do
  not introduce fixed-limit history reads.

Spec delta required before implementation is done:

- Update [MF-3] and [MF-5] to say task evidence classification is shared by
  status, task, result, and future monitor/reaper code.
- Update [CLI-1.2.1] and [CLI-1.2] to describe the new additive
  reconciliation classifications for `terminal_ctrl_out`,
  `wrapper_lost`, and `result_without_terminal`.
- Update [DJ-2.2] / [DJ-8.3] if the stable known-TID terminal snapshot metadata
  grows a classification field.
- Add backlinks from touched spec sections to this plan.

## 3. Live Evidence And Release 2 Target Cases

Ops check on 2026-05-06 after Release 1 deployment:

```bash
ssh ops 'cd governance && source .env && /opt/venv/bin/weft status --json'
```

Observed current shape:

- `weft status --json` no longer emits `status="running"` with `completed_at`
  set. Release 1 did its job.
- Many tasks still appear as `created` / `waiting` from `task_activity` even
  though their task-local `T{tid}.ctrl_out` queue has a typed manager terminal
  envelope:

```json
{
  "type": "terminal",
  "source": "manager",
  "tid": "1777893208720482304",
  "status": "failed",
  "error": "Task wrapper exited before publishing terminal state",
  "timestamp": 1777893445705676988,
  "return_code": 1
}
```

- The first sampled live case was
  `1777893208720482304`, name `reconcile-log-truncation`, public status
  `created`, activity `waiting`, event `task_activity`, with the terminal
  `ctrl_out` envelope above. Release 2 should classify this as terminal
  `failed` with `wrapper_lost`.
- The parent meta-plan also records a `wazuh-case-rollup` case where outbox
  result evidence exists but no terminal task-log event is visible. Release 2
  should classify this as `result_without_terminal` or an equivalent
  diagnostic without pretending all outbox traffic is terminal.

Important interpretation:

- A manager-authored terminal `ctrl_out` envelope is terminal proof for
  observation when task-log terminal proof is missing.
- It is not a normal task result and must not be written to or read from
  outbox.
- A one-shot final outbox message is result evidence. It can prove completion
  only under the explicit one-shot/non-persistent rules below.
- Domain failures are not Weft lifecycle bugs. Keep real `work_failed` task-log
  failures visible as failures, not stale cleanup candidates.

## 4. Current Structure And Key Files

Files to modify:

- `weft/commands/task_evidence.py`
  - New shared helper module for read-only evidence classification.
  - This is the main implementation file for Release 2.
  - Keep it under `weft/commands/`, not `weft/core/`, because it reads command
    observation surfaces and must not become runtime lifecycle authority.
- `weft/commands/system.py`
  - Status reducer and project-wide snapshots.
  - Replace private evidence fragments with calls into
    `weft.commands.task_evidence`.
  - Keep global status as one generator-based replay of `weft.log.tasks`.
- `weft/commands/tasks.py`
  - Known-TID terminal snapshot, `task_status()`, `task_snapshot()`, task list,
    and public snapshot conversion.
  - Move duplicated outbox/`ctrl_out`/runtime/log classification logic into
    the shared helper, then keep this file as a thin command wrapper.
- `weft/commands/_result_wait.py`
  - Shared one-shot result wait loop.
  - Reuse shared terminal event and typed terminal envelope interpretation
    where practical. Do not make the wait loop depend on project-wide status.
- `weft/commands/result.py`
  - Result materialization and queue-name discovery.
  - Reuse shared queue-name and evidence helpers where it reduces duplication
    without changing result consumption semantics.
- `weft/commands/types.py`
  - Add or refine public/internal dataclasses if the evidence helper needs a
    new `TaskEvidenceSnapshot` shape or `TaskTerminalSnapshot.metadata`
    contract.
  - Keep client-facing `TaskSnapshot` backward-compatible.
- `weft/_constants.py`
  - Add named classification/source constants only if they prevent scattered
    literals. Do not move complex policy into constants.
- `tests/commands/test_task_evidence.py`
  - New focused tests for the shared evidence helper.
- `tests/commands/test_status.py`
  - Project-wide status regressions.
- `tests/commands/test_task_commands.py`
  - Known-TID snapshot, `task status`, and non-consuming acknowledgement
    regressions.
- `tests/commands/test_result.py`
  - Result wait/materialization regressions if helper reuse touches result
    behavior.
- `tests/core/test_client.py`
  - Client-facing `Task.terminal_snapshot()` or related public dataclass
    regressions if metadata shape changes.
- `docs/specifications/05-Message_Flow_and_State.md`
  - Update [MF-3] and [MF-5] behavior and implementation mapping.
- `docs/specifications/07-System_Invariants.md`
  - Update observability/state invariants only if the classification contract
    needs a new invariant.
- `docs/specifications/10-CLI_Interface.md`
  - Update [CLI-1.2.1] and [CLI-1.2].
- `docs/specifications/13C-Using_Weft_With_Django.md`
  - Update [DJ-2.2] / [DJ-8.3] only if the stable known-TID terminal snapshot
    contract grows a documented metadata field.
- `docs/lessons.md`
  - Add a short lesson only if implementation exposes a reusable failure mode
    not already captured.

Files to read before editing:

- `weft/commands/system.py`
  - `_iter_log_events()` is the generator-based task-log replay helper.
  - `_collect_task_snapshot_records()` builds project-wide status snapshots.
  - `_reconcile_lifecycle_status()` and `_reconciliation_diagnostic()` are the
    Release 1 read-model rules to preserve or move.
  - `_effective_public_status()` must not reanimate terminal lifecycle state.
- `weft/commands/tasks.py`
  - `_peek_final_outbox_snapshot()` already implements conservative one-shot
    outbox inference.
  - `_peek_terminal_ctrl_out_snapshot()` already accepts only typed terminal
    envelopes.
  - `_runtime_snapshot_from_mapping()` and `_bounded_log_terminal_snapshot()`
    are known-TID-only evidence readers that should become shared logic.
  - `task_terminal_snapshot()` defines the compact public terminal/live API.
- `weft/commands/result.py`
  - `_await_result_materialization()` has result queue-name discovery, terminal
    log materialization, and pipeline considerations.
  - `_collect_all_results()` and single-result paths may consume outbox
    messages. Do not accidentally change peek/read semantics while refactoring.
- `weft/commands/_result_wait.py`
  - `terminal_status_from_event()` and `terminal_error_message()` duplicate
    task-log terminal interpretation. Reuse or delegate to the shared helper.
  - `await_one_shot_result()` consumes outbox messages for the result command.
    The evidence helper must remain non-consuming.
- `weft/commands/_streaming.py`
  - `process_outbox_message()` and `aggregate_public_outputs()` define public
    output decoding. Reuse them; do not parse outbox ad hoc.
- `weft/commands/_task_history.py`
  - `load_latest_taskspec_payload()` uses generator-based bounded replay.
    Reuse it for known-TID paths where appropriate.
- `weft/core/manager.py`
  - `_write_manager_terminal_envelope()` defines the manager-authored
    wrapper-loss terminal envelope.
  - `_child_terminal_proof_visible()` defines the "do not duplicate terminal
    proof" manager guard.
- `weft/ext.py`
  - `RunnerHandle.scoped_host_processes()` distinguishes weak host PID
    evidence from PID-plus-create-time evidence.
- `weft/helpers/__init__.py`
  - `iter_queue_json_entries()`, `handle_has_live_host_process()`, and PID
    helpers are the existing primitives.
- `tests/commands/test_task_commands.py`
  - Existing known-TID tests already prove `ctrl_out` and outbox reads are
    non-consuming.
- `tests/commands/test_status.py`
  - Existing Release 1 tests prove terminal task-log status wins.
- `tests/commands/test_result.py`
  - Existing result tests prove outbox/log grace behavior and ambiguous outbox
    handling.

Comprehension checks before editing:

- Which queue is lifecycle truth when it contains terminal proof?
  Answer: `weft.log.tasks`.
- Which task-local queue carries manager wrapper-loss proof?
  Answer: `T{tid}.ctrl_out` as a typed terminal envelope with
  `source="manager"`.
- Which task-local queue carries user result payloads?
  Answer: `T{tid}.outbox`.
- Is a manager terminal envelope a result?
  Answer: no. It is terminal observation and control-plane evidence.
- When may outbox evidence imply completion?
  Answer: only for one-shot, non-persistent tasks when the decoded outbox shape
  is final and unambiguous under the existing result decoding rules.
- May this release delete or consume queue messages during status/evidence
  reads?
  Answer: no. All evidence reads in this release are peek/generator reads.
- Which release owns cleanup/deletion?
  Answer: Releases 5 and 6, not this release.

## 5. Desired Contract

Public task lifecycle states stay unchanged:

- `created`
- `spawning`
- `running`
- `completed`
- `failed`
- `timeout`
- `cancelled`
- `killed`

Add or preserve internal evidence classifications. These are diagnostic
classifications, not public lifecycle states:

- `live`
- `terminal_log`
- `terminal_ctrl_out`
- `wrapper_lost`
- `result_without_terminal`
- `stale_created`
- `runtime_conflict`
- `domain_failure`
- `unknown`

The shared evidence snapshot should be able to report at least:

- `tid`
- chosen public `status`
- `classification`
- `source`
- `event`
- `activity`
- `waiting_on`
- `started_at`
- `completed_at`
- `return_code`
- `error`
- `value`, `stdout`, `stderr` only when outbox evidence is part of the
  classification and the caller explicitly needs it
- `taskspec_payload`
- `runtime_handle`
- `runtime`
- `metadata`
- `observed_at`
- optional non-consuming `ack_targets` for the existing known-TID API only

Public `reconciliation` diagnostic rules for this release:

- Omit `reconciliation` when there is no useful diagnostic.
- If task-log terminal proof exists, keep the Release 1 behavior:
  terminal log wins, runtime conflict stays diagnostic.
- If typed terminal `ctrl_out` is the best terminal proof:
  - public `status` should be the envelope status
  - `classification` should be `wrapper_lost` when `source="manager"` and the
    error matches wrapper-loss semantics
  - otherwise use `terminal_ctrl_out`
  - include `lifecycle_status`
  - include `terminal_source`, either `task` or `manager`
  - include `evidence_source="ctrl_out"`
  - include `observed_at` when known
- If one-shot final outbox evidence exists without terminal task-log proof:
  - public status should be `completed` only when the helper can classify the
    outbox as final and unambiguous
  - `classification` should be `result_without_terminal`
  - include `lifecycle_status="completed"`
  - include `evidence_source="outbox"`
  - include `observed_at` when known
- If stale observer logic marks a previously-live task as failed because no
  runtime or terminal surface remains visible:
  - preserve existing known-TID behavior, but classify it as `stale_created`
    only when the task is still `created`/`waiting`; otherwise use a clear
    observer classification from the helper
- Do not mark domain `work_failed` events as stale or wrapper-loss. A real
  task-log `work_failed` remains a failure from `terminal_log` or
  `domain_failure`, depending on whether the implementer chooses to surface
  that extra classification.

Evidence priority:

1. Terminal task-log events are canonical lifecycle proof.
2. Typed terminal `ctrl_out` envelopes are terminal proof when no terminal
   task-log event exists.
3. One-shot final outbox evidence may classify completion when no terminal
   task-log or terminal `ctrl_out` exists and the task is not persistent,
   interactive, streaming, or ambiguous.
4. Runtime liveness supports `running` only when stronger terminal evidence is
   absent.
5. Stale observer rules are fallback evidence for previously-live tasks after
   the configured grace window.
6. Unknown remains unknown. Do not guess.

## 6. Invariants And Constraints

Preserve:

- TID format and immutability.
- `TaskSpec.spec` and `TaskSpec.io` immutability after resolved creation.
- Forward-only public lifecycle semantics.
- Existing public task state set.
- Existing queue names and queue roles.
- Existing result consumption behavior for `weft result`.
- Existing non-consuming behavior for `task_terminal_snapshot()` unless the
  caller explicitly calls `ack_terminal_snapshot()`.
- Existing exact-message acknowledgement behavior for `ack_terminal_snapshot()`.
- Generator-based reads for append-only queues such as `weft.log.tasks`,
  `weft.state.tid_mappings`, and task-local queues when history matters.
- Runtime-only status of `weft.state.*` queues.
- Manager registry authority for active managers.
- Pipeline task status handling.

Do not:

- Add cleanup, deletion, pruning, archive, or monitor logic.
- Consume `outbox` or `ctrl_out` from status, task status, or evidence reads.
- Treat arbitrary `ctrl_out` payloads as terminal.
- Treat malformed JSON or ordinary control replies as terminal.
- Treat all outbox messages as terminal.
- Treat persistent, interactive, or streaming outbox traffic as terminal
  completion evidence.
- Add a second lifecycle database, cache, or hidden status table.
- Add new public task lifecycle states.
- Change queue names, TaskSpec schema, or result payload schema.
- Move command observation policy into `weft/core/manager.py` or
  `weft/core/tasks/*`.
- Rewrite result wait semantics while trying to DRY the evidence reader.
- Special-case governance task names, Wazuh, ops, or PID values.

Hidden couplings to respect:

- `weft status` needs a project-wide view and should not open every task-local
  queue for every historical task by default unless bounded by a current
  snapshot candidate set. On ops, `--all` currently reports roughly 19.5k task
  snapshots; opening two queues per task can become expensive.
- `weft task status TID` and `task_terminal_snapshot(TID)` are known-TID
  surfaces and may inspect task-local queues directly.
- `weft result` consumes outbox messages unless called in peek/all modes. The
  evidence helper must not consume; result code may consume only after it has
  selected the result surface.
- Completion events and outbox visibility are close but not simultaneous. Keep
  the existing grace-window behavior in `_result_wait.py`.
- Pipeline tasks can use `P{tid}` queues and pipeline status snapshots. Do not
  force every task into `T{tid}` queue assumptions when a TaskSpec declares
  custom IO.

Stop and re-plan if:

- the implementation wants to add cleanup or archive output
- project-wide status becomes unbounded per-task queue scanning over all
  historical tasks
- the helper requires callers to pass mocks instead of real `WeftContext` and
  queues
- result command behavior changes because of a refactor rather than a stated
  Release 2 requirement
- a public JSON shape becomes breaking instead of additive
- the code starts treating monitor checkpoints or memory as status truth
- the helper starts writing lifecycle events

## 7. Implementation Tasks

### 1. Add failing tests for typed terminal `ctrl_out` classification

Outcome: the current bug is captured before implementation.

Files to touch:

- `tests/commands/test_task_evidence.py` (new)
- `tests/commands/test_status.py`
- `tests/commands/test_task_commands.py`

Read first:

- `tests/commands/test_task_commands.py`
  `test_terminal_snapshot_reads_only_typed_terminal_ctrl_out`
- `tests/commands/test_status.py`
  Release 1 reconciliation tests
- `weft/core/manager.py` `_write_manager_terminal_envelope`

Test shape:

- Use `prepare_project_root(tmp_path)` and `build_context()`.
- Write a `weft.log.tasks` `task_activity` event for a new TID with:
  - `status="created"`
  - `activity="waiting"`
  - `waiting_on=f"T{tid}.inbox"`
  - a valid TaskSpec payload with default `io` queues
- Write a typed terminal envelope to `T{tid}.ctrl_out`:
  - `type="terminal"`
  - `source="manager"`
  - matching `tid`
  - `status="failed"`
  - `error="Task wrapper exited before publishing terminal state"`
  - `return_code=1`
- Assert the shared evidence helper, once added, classifies it as:
  - public status `failed`
  - classification `wrapper_lost`
  - evidence source `ctrl_out`
  - terminal source `manager`
  - non-consuming: the `ctrl_out` message remains visible after classification
- Assert `task_cmd.task_terminal_snapshot(tid, context=ctx)` still returns a
  terminal snapshot with `source="ctrl_out"` and does not consume the message.
- Assert `task_cmd.task_status(tid, context_path=root)` reports `failed` and
  includes reconciliation metadata.
- Assert `cmd_status(json_output=True, include_terminal=True, spec_context=root)`
  reports `failed` for that TID instead of `created`.
- Assert default `cmd_status(json_output=True, spec_context=root)` does not
  show that TID as `created` after classification has made it terminal. It may
  be absent because default status hides terminal tasks.

Anti-mocking rule:

- Do not mock `Queue`, `WeftContext`, or task-local queue reads. These tests
  should use real broker-backed queues.

Per-task verification:

```bash
./.venv/bin/python -m pytest tests/commands/test_task_evidence.py tests/commands/test_task_commands.py::test_terminal_snapshot_reads_only_typed_terminal_ctrl_out tests/commands/test_status.py -k 'ctrl_out or wrapper_lost' -q
```

Expected red phase:

- New status/evidence assertions should fail before the shared helper is
  implemented because status currently does not fold typed terminal `ctrl_out`
  into project-wide snapshots.

Stop and re-evaluate if:

- The test can only pass by mocking the queue reader.
- The test writes terminal task-log proof. That would test Release 1, not this
  release.

### 2. Add failing tests for one-shot outbox result evidence

Outcome: outbox-without-terminal behavior is specified without making all
outbox messages terminal.

Files to touch:

- `tests/commands/test_task_evidence.py`
- `tests/commands/test_status.py`
- `tests/commands/test_result.py` only if result helper behavior changes

Read first:

- `weft/commands/tasks.py` `_peek_final_outbox_snapshot`
- `tests/commands/test_task_commands.py`
  `test_terminal_snapshot_reads_outbox_without_consuming`
- `tests/commands/test_result.py`
  `test_await_one_shot_result_accepts_prewritten_outbox_when_log_event_is_missed`
  and
  `test_await_one_shot_result_does_not_infer_completion_from_ambiguous_outbox`

Test shape:

- Write a non-terminal `work_started` task-log event for a one-shot command or
  function task.
- Ensure the TaskSpec payload has `spec.persistent` false or absent and
  declares `io.outputs.outbox`.
- Write one final, decoded outbox payload to `T{tid}.outbox`.
- Do not write `work_completed`.
- Assert shared evidence classifies:
  - public status `completed`
  - classification `result_without_terminal`
  - evidence source `outbox`
  - value/stdout/stderr only where the evidence API promises those fields
  - non-consuming: outbox message remains visible after status/evidence read
- Assert `task_cmd.task_status(tid, context_path=root)` no longer reports plain
  `running`/`working`.
- Assert `cmd_status(json_output=True, include_terminal=True, spec_context=root)`
  shows the same classification. Default status may omit the task after it
  becomes terminal.
- Add negative tests:
  - persistent task with outbox payload does not become completed
  - stream chunk with `final=false` does not become completed
  - multiple primitive outbox messages without terminal/log proof do not become
    completed if existing result semantics consider that ambiguous

Per-task verification:

```bash
./.venv/bin/python -m pytest tests/commands/test_task_evidence.py tests/commands/test_status.py -k 'outbox or result_without_terminal or persistent' -q
./.venv/bin/python -m pytest tests/commands/test_result.py -k 'outbox_when_log_event_is_missed or ambiguous_outbox' -q
```

Stop and re-evaluate if:

- The implementation starts treating every outbox message as completion.
- The negative tests require changing current `weft result` behavior. Result
  command behavior is adjacent, but this release should not rewrite it unless
  the current behavior contradicts the specs.

### 3. Introduce `weft.commands.task_evidence`

Outcome: one shared module owns evidence reading and classification.

Files to touch:

- `weft/commands/task_evidence.py`
- `weft/commands/types.py`
- `weft/_constants.py` only for small shared string constants if needed

Read first:

- `weft/commands/tasks.py`
  `_queue_names_for_tid`, `_peek_final_outbox_snapshot`,
  `_coerce_terminal_envelope`, `_peek_terminal_ctrl_out_snapshot`,
  `_bounded_log_terminal_snapshot`, `_stale_observer_snapshot`
- `weft/commands/system.py`
  `_reconcile_lifecycle_status`, `_reconciliation_diagnostic`,
  `_runtime_evidence_details`
- `weft/commands/_streaming.py`
  `process_outbox_message`, `aggregate_public_outputs`

Required design:

- Add a dataclass such as `TaskEvidenceSnapshot` in `weft/commands/types.py` or
  inside `task_evidence.py` if it is intentionally internal.
- Keep the helper read-only. Evidence reads must use `peek_generator()`,
  `peek_one()`, or existing generator helpers, never `read_one()`.
- Expose two entry points:
  - one known-TID entry point that may inspect task-local queues directly
  - one project-wide entry point or reducer hook that accepts already replayed
    task-log records and only performs bounded task-local inspection for
    candidate non-terminal records
- Keep queue-name resolution shared:
  - prefer TaskSpec-declared `io.outputs.outbox`
  - prefer TaskSpec-declared `io.control.ctrl_out`
  - fall back to `T{tid}.outbox` / `T{tid}.ctrl_out`
- Keep terminal envelope validation strict:
  - JSON object only
  - `type == TERMINAL_ENVELOPE_TYPE`
  - matching `tid`
  - `source in TASK_TERMINAL_ENVELOPE_SOURCES`
  - `status in TERMINAL_TASK_STATUSES`
- Keep final outbox inference conservative:
  - return no completion for persistent tasks
  - return no completion for partial stream chunks
  - return no completion when existing result code treats the surface as
    ambiguous
  - use `process_outbox_message()` and `aggregate_public_outputs()`
- Keep task-log terminal interpretation in one helper:
  - move or delegate `_result_wait.terminal_status_from_event()`
  - preserve `TERMINAL_TASK_EVENTS` contradictory status handling from Release 1

Suggested API shape:

```python
@dataclass(frozen=True, slots=True)
class TaskEvidenceSnapshot:
    tid: str
    status: str
    classification: str
    source: str
    terminal: bool
    taskspec_payload: dict[str, Any] | None = None
    event: str | None = None
    activity: str | None = None
    waiting_on: str | None = None
    started_at: int | None = None
    completed_at: int | None = None
    observed_at: int | None = None
    error: str | None = None
    return_code: int | None = None
    value: Any | None = None
    stdout: str | None = None
    stderr: str | None = None
    runtime_handle: dict[str, Any] | None = None
    runtime: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    reconciliation: dict[str, Any] | None = None
    ack_targets: tuple[QueueAckTarget, ...] = ()
```

This exact shape is not mandatory, but any alternative must support the fields
needed by `TaskSnapshot`, `TaskTerminalSnapshot`, `weft result`, and future
monitor/reaper classification without adding a second helper later.

Per-task verification:

```bash
./.venv/bin/python -m pytest tests/commands/test_task_evidence.py -q
```

Stop and re-evaluate if:

- The helper becomes a generic class hierarchy or plugin system. YAGNI.
- The helper writes state or emits lifecycle events. This release is read-only.
- The helper cannot be used by both `tasks.py` and `system.py` without
  branching into two separate implementations.

### 4. Refactor known-TID task helpers onto the shared evidence model

Outcome: `task_terminal_snapshot()`, `task_status()`, and `task_snapshot()`
use the shared classification semantics.

Files to touch:

- `weft/commands/tasks.py`
- `tests/commands/test_task_commands.py`
- `tests/core/test_client.py` if client helpers expose changed metadata

Read first:

- `weft/commands/tasks.py` `task_terminal_snapshot()`,
  `_public_snapshot()`, `task_status()`, `task_snapshot()`
- `tests/core/test_client.py` terminal snapshot tests

Required changes:

- Replace `_peek_final_outbox_snapshot()`,
  `_peek_terminal_ctrl_out_snapshot()`, `_bounded_log_terminal_snapshot()`,
  `_runtime_snapshot_from_mapping()`, and `_stale_observer_snapshot()` with
  calls into `task_evidence.py`, or reduce them to thin wrappers around the
  shared helper.
- Preserve `TaskTerminalSnapshot` public behavior:
  - `source="outbox"` for final outbox evidence
  - `source="ctrl_out"` for terminal envelope evidence
  - `source="runtime"` for live runtime proof
  - `source="log_fallback"` or a compatible source for log terminal fallback
  - exact `ack_targets` where the existing API already returns them
  - non-consuming reads
- Add classification details into `TaskTerminalSnapshot.metadata`, not as a new
  required dataclass field, unless the spec is updated to make it stable.
- Make `task_status()` prefer shared evidence over plain task-log snapshot for
  known full TIDs when the evidence has stronger terminal proof from `ctrl_out`
  or outbox.

Per-task verification:

```bash
./.venv/bin/python -m pytest tests/commands/test_task_commands.py -q
./.venv/bin/python -m pytest tests/core/test_client.py -q
```

Stop and re-evaluate if:

- Known-TID helpers start replaying the global task log from the beginning for
  full TIDs.
- `ack_terminal_snapshot()` starts deleting more than the exact message targets
  returned by the terminal snapshot.

### 5. Refactor project-wide status onto the shared evidence model

Outcome: `weft status`, `weft status --all`, `weft task list`, and
`weft task status` report coherent classifications from task-log, typed
`ctrl_out`, and one-shot outbox evidence.

Files to touch:

- `weft/commands/system.py`
- `weft/commands/tasks.py`
- `tests/commands/test_status.py`
- `tests/commands/test_task_commands.py`

Read first:

- `weft/commands/system.py` `_collect_task_snapshot_records()`
- `weft/commands/tasks.py` `list_task_snapshots()` and `task_stats()`

Required changes:

- Keep `_collect_task_snapshot_records()` as the project-wide task-log replay
  owner.
- During replay, preserve enough per-TID evidence for the shared helper:
  - latest TaskSpec payload
  - latest task-log event payload
  - current lifecycle status and status reason
  - activity / waiting state
  - started/completed timestamps
  - latest mapping entry
- After replay, call the shared evidence classifier for each candidate record.
- For performance, project-wide status should inspect task-local `ctrl_out` and
  outbox only for records that are still non-terminal or contradictory after
  task-log replay. Terminal task-log records already have stronger proof.
- Preserve Release 1 behavior:
  - terminal task-log proof wins over runtime liveness
  - no `running` with `completed_at`
  - old terminal-looking rows remain terminal
  - active manager remains visible
- Ensure default `weft status` filtering still hides terminal tasks unless
  `include_terminal=True`, but does not hide non-terminal records that become
  terminal only after shared evidence classification until after classification
  has happened.
- Include additive `reconciliation` diagnostics for:
  - `wrapper_lost`
  - `terminal_ctrl_out`
  - `result_without_terminal`
  - Release 1 runtime conflicts

Per-task verification:

```bash
./.venv/bin/python -m pytest tests/commands/test_status.py tests/commands/test_task_commands.py -q
```

Stop and re-evaluate if:

- Status becomes slower because it opens task-local queues for every historical
  terminal task in `--all`.
- Status and known-TID task status disagree for the same synthetic broker
  evidence.

### 6. Consolidate result helper terminal interpretation without changing consumption

Outcome: result waits reuse shared terminal/log/envelope semantics where safe,
while preserving the command's read/consume behavior.

Files to touch:

- `weft/commands/_result_wait.py`
- `weft/commands/result.py`
- `tests/commands/test_result.py`

Read first:

- `weft/commands/_result_wait.py` `await_one_shot_result()`
- `weft/commands/result.py` `_await_result_materialization()`,
  `_await_single_result()`, `_collect_all_results()`
- `tests/commands/test_result.py` result grace and ambiguity tests

Required changes:

- Replace duplicated terminal-log helpers with shared functions from
  `task_evidence.py` if the shared functions exactly preserve behavior.
- Do not route `weft result` through project-wide status.
- Do not change `read_one()` behavior for result consumption.
- Keep `--peek` and `--all` semantics unchanged.
- Keep the completed-result grace window unchanged.
- If typed terminal `ctrl_out` should affect `weft result TID`, use only the
  same strict terminal-envelope validation. Ordinary stderr/control messages
  must still flow through `handle_ctrl_stream()` and must not become terminal
  proof.

Per-task verification:

```bash
./.venv/bin/python -m pytest tests/commands/test_result.py -q
```

Stop and re-evaluate if:

- Refactoring result code requires broad rewrites unrelated to shared evidence.
- Tests start relying on sleeps instead of existing result wait helpers or
  bounded polling.

### 7. Update specs, backlinks, and implementation mappings

Outcome: specs become the steady-state source of truth again.

Files to touch:

- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md` if needed
- `docs/specifications/10-CLI_Interface.md`
- `docs/specifications/13C-Using_Weft_With_Django.md` if needed
- `docs/plans/README.md`
- `docs/lessons.md` only if a reusable lesson is discovered

Required changes:

- In [MF-3], document that typed terminal `ctrl_out` envelopes are accepted by
  the shared evidence reader when task-log terminal proof is missing.
- In [MF-5], document evidence priority and classification ownership.
- In [CLI-1.2.1], document additive `reconciliation` classifications for
  `wrapper_lost`, `terminal_ctrl_out`, and `result_without_terminal`.
- In [CLI-1.2], document that `weft task status` and known-TID helpers use the
  shared evidence reader.
- In [DJ-8.3], document any stable metadata returned by
  `terminal_snapshot()` only if implementation exposes it as public API.
- Add this plan to each touched spec's related plans or implementation plan
  backlink section.
- Add this plan to `docs/plans/README.md` with status `draft` and update the
  count.

Per-task verification:

```bash
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
```

Stop and re-evaluate if:

- The spec edits describe cleanup, archive, or monitor behavior. That belongs
  to later releases.

### 8. Run local gates and independent review

Outcome: the slice is locally shippable and reviewable.

Required local gates:

```bash
./.venv/bin/python -m pytest tests/commands/test_task_evidence.py -q
./.venv/bin/python -m pytest tests/commands/test_status.py tests/commands/test_task_commands.py -q
./.venv/bin/python -m pytest tests/commands/test_result.py -q
./.venv/bin/python -m pytest tests/core/test_client.py -q
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft tests/commands/test_task_evidence.py tests/commands/test_status.py tests/commands/test_task_commands.py tests/commands/test_result.py tests/core/test_client.py tests/specs/test_plan_metadata.py
```

Run the full default suite before cutting a release:

```bash
./.venv/bin/python -m pytest
```

Independent review prompt:

> Read
> `docs/plans/2026-05-06-task-evidence-reconciliation-model-plan.md`, the
> touched implementation files, and the tests. Look for evidence-priority
> mistakes, hidden queue consumption, over-broad outbox inference, status/result
> disagreement, performance problems on project-wide status, missing spec
> updates, and drift into cleanup. Do not implement anything. Could you
> implement or approve this confidently?

Stop and re-evaluate if:

- Review finds that status, result, and known-TID snapshots still have separate
  priority rules.
- Review finds any queue-consuming read in an observation-only path.

## 8. Ops Validation After Release

Deploy the release to ops, then run read-only validation.

Always load the governance environment first:

```bash
ssh ops
cd governance
source .env
```

Confirm the deployed code version and helper presence:

```bash
/opt/venv/bin/python - <<'PY'
import weft.commands.task_evidence as task_evidence
print(task_evidence.__file__)
PY
```

Validate project status:

```bash
/opt/venv/bin/python - <<'PY'
import json
import subprocess

data = json.loads(subprocess.check_output([
    "/opt/venv/bin/weft",
    "status",
    "--json",
], text=True))

tasks = data.get("tasks", [])
bad_running = [
    task for task in tasks
    if task.get("status") == "running" and task.get("completed_at") is not None
]
wrapper_lost = [
    task for task in tasks
    if (task.get("reconciliation") or {}).get("classification") == "wrapper_lost"
]
result_without_terminal = [
    task for task in tasks
    if (task.get("reconciliation") or {}).get("classification") == "result_without_terminal"
]

print("tasks", len(tasks))
print("bad_running", len(bad_running))
print("wrapper_lost", len(wrapper_lost))
print("result_without_terminal", len(result_without_terminal))
print("managers", data.get("managers"))
PY
```

Expected:

- `bad_running` is `0`.
- Active manager remains visible in `managers`.
- Previously-created wrapper-loss tasks no longer appear as plain
  `created/waiting`.
- At least the known sampled wrapper-loss task
  `1777893208720482304` appears terminal `failed` or has
  `reconciliation.classification == "wrapper_lost"`.
- The `wazuh-case-rollup` outbox-without-terminal case is no longer plain
  `running/working`; it is classified as `result_without_terminal` or an
  equivalent additive diagnostic.

Validate the sampled wrapper-loss TID directly:

```bash
/opt/venv/bin/weft task status 1777893208720482304 --json
```

Expected:

- status is `failed`
- error mentions `Task wrapper exited before publishing terminal state`
- reconciliation classification is `wrapper_lost` or a clearly equivalent
  terminal `ctrl_out` classification
- `return_code` is `1` when surfaced by the selected JSON shape

Validate non-consumption:

```bash
/opt/venv/bin/weft queue peek T1777893208720482304.ctrl_out --timestamps
```

Expected:

- the terminal envelope is still present after `status` and `task status`
  reads
- no status command deletes or moves messages

Validate real domain failures remain visible:

```bash
/opt/venv/bin/weft status --json --all
```

Expected:

- historical `work_failed` tasks remain `failed`
- they are not converted to `wrapper_lost`, `stale_created`, or cleanup
  categories unless their best evidence actually says that

Performance note:

- `weft status --json --all` is expected to take noticeable time on the live
  broker. It should not become materially worse because this release should
  avoid opening task-local queues for every historical terminal record.

## 9. Rollout And Rollback

Rollout:

1. Implement this release slice only.
2. Run local targeted tests and static checks.
3. Run an independent review.
4. Update specs and backlinks.
5. Run the full default test suite.
6. Cut a Weft release.
7. Deploy to ops.
8. Run the ops validation gate above.
9. Do not begin Release 3 until live evidence is understood.

Rollback:

- This release is read-only and additive. It should not mutate broker state.
- If status/result behavior regresses, roll back the package version. Existing
  broker state remains valid because no payload shape or queue name changes.
- If `reconciliation` shape is too noisy or wrong, roll back the package and
  keep the broker untouched. Then fix the helper and tests locally before
  redeploying.
- If project-wide status becomes too slow on ops, roll back and redesign the
  bounded task-local inspection step. Do not start cleanup work as a workaround.

One-way doors:

- None intended in this release.

Treat these as accidental one-way doors and stop if they appear:

- deleting queue messages
- changing queue names
- changing TaskSpec schema
- changing result payload schema
- making public JSON output incompatible

## 10. Fresh-Eyes Review Notes

Self-review pass 1:

- Risk: a naive implementation might simply call known-TID
  `task_terminal_snapshot()` for every status row. That is too expensive on
  `--all` and duplicates bounded task-log replay. The plan now requires a
  project-wide reducer hook and task-local inspection only for non-terminal or
  contradictory candidates.
- Risk: outbox evidence could be over-generalized. The plan now requires
  negative tests for persistent tasks, partial stream chunks, and ambiguous
  multiple primitive messages.
- Risk: result command consumption could be broken by moving all outbox reads
  into a non-consuming helper. The plan separates non-consuming evidence reads
  from result command consumption and requires existing result tests.
- Risk: adding `classification` as a public state would drift from the parent
  plan. The plan explicitly keeps classifications diagnostic and preserves the
  existing public state set.

Self-review pass 2:

- Ambiguity: whether manager terminal envelopes should be `terminal_ctrl_out`
  or `wrapper_lost`. The plan resolves this by using `wrapper_lost` for
  manager-authored wrapper-exit envelopes and `terminal_ctrl_out` for other
  strict typed terminal envelopes.
- Ambiguity: whether typed `ctrl_out` should outrank task-log terminal proof.
  The plan keeps task-log terminal proof first because `weft.log.tasks` is the
  lifecycle truth when terminal proof exists.
- Ambiguity: whether docs should update Django specs. The plan scopes Django
  spec edits only to stable known-TID metadata changes.

Final scope check:

- This plan still matches Release 2 from the parent architecture plan. It does
  not add Release 3 write-path hardening, Release 4 monitor/archive behavior,
  or Release 5/6 cleanup. If implementation discovers that result-without-log
  cannot be classified correctly without changing terminal publication, stop
  and return with that finding instead of silently pulling Release 3 into this
  slice.

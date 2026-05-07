# Result Evidence And Superseded Manager Reconciliation Plan

Status: completed
Source specs: see Source Documents below
Superseded by: none

Parent plan:
[`docs/plans/2026-05-06-lifecycle-reconciliation-architecture-plan.md`](./2026-05-06-lifecycle-reconciliation-architecture-plan.md)

Release slice: corrective pre-phase-4 slice after Release 3
Implementation note: implemented locally; release and ops validation remain part
of the normal release/deploy flow.

## 1. Goal

Fix two Weft-owned reconciliation gaps observed on ops after `0.9.17`:

1. a historical one-shot task with an outbox result but no terminal task-log or
   terminal `ctrl_out` evidence can still be reported as an ordinary stale
   failure, and `weft result` can time out when the outbox row is claimed;
2. a superseded manager task can continue to appear as `running` in task-list
   views even when `manager list` has selected a newer active manager.

This slice is read-model and result-surface hardening. It must land before
destructive phase-4 cleanup because the monitor/reaper should not be asked to
delete or archive rows that the read model still misclassifies.

## 2. Source Documents

Source specs:

- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md)
  [MA-1], [MA-4] and the manager lifecycle implementation mapping.
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-2], [MF-3], [MF-5], "Cleanup Boundary", "Queue Management Patterns".
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [STATE.1], [STATE.2], [STATE.4], [STATE.6], [OBS.1], [OBS.2],
  [OBS.3], [OBS.11].
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
  [CLI-1.2], [CLI-1.2.1], [CLI-6].

Guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)
- [`docs/agent-context/runbooks/testing-patterns.md`](../agent-context/runbooks/testing-patterns.md)

Related plans:

- [`docs/plans/2026-05-06-status-coherence-and-stale-pid-liveness-plan.md`](./2026-05-06-status-coherence-and-stale-pid-liveness-plan.md)
  is Release 1. It fixed impossible `running` plus `completed_at` public
  snapshots and stale PID reanimation.
- [`docs/plans/2026-05-06-task-evidence-reconciliation-model-plan.md`](./2026-05-06-task-evidence-reconciliation-model-plan.md)
  is Release 2. It introduced shared known-TID evidence classification,
  including `result_without_terminal`.
- [`docs/plans/2026-05-06-terminal-publication-hardening-plan.md`](./2026-05-06-terminal-publication-hardening-plan.md)
  is Release 3. It made new tasks publish task-owned terminal `ctrl_out`
  envelopes.
- [`docs/plans/2026-05-07-lifecycle-monitor-archive-sink-plan.md`](./2026-05-07-lifecycle-monitor-archive-sink-plan.md)
  is phase 4 / Release 4. This corrective slice should land before that
  monitor/reaper work starts deleting or archiving.
- [`docs/plans/2026-04-24-manager-status-container-pid-liveness-plan.md`](./2026-04-24-manager-status-container-pid-liveness-plan.md)
  is relevant to the manager active-record boundary.

Spec delta required before implementation is done:

- Update [MF-5] to state that readable final one-shot outbox evidence must be
  considered before stale observer failure.
- Update [MF-5] to distinguish readable final outbox evidence from claimed
  result-surface residue. Claimed outbox residue is a recovery diagnostic, not
  proof that the result value is currently readable.
- Update [MF-5] or the manager section to say manager task snapshots must not
  show a non-active manager as `running` when the manager registry has selected
  a different active manager.
- Update [CLI-1.2] / [CLI-1.2.1] if `weft result --json` gains an additive
  result-unavailable diagnostic status or field.
- Add backlinks from touched spec sections to this plan.

## 3. Live Evidence

Ops check on 2026-05-07 after deploying `0.9.17`:

- `weft`, `weft-docker`, and `weft-django` were all deployed at `0.9.17`.
- `weft status --json` and `weft task list --all --json` no longer reported
  any `running` or `created` task with `completed_at` set. Release 1 worked.
- Known wrapper-loss task `1777893208720482304` now reports:
  - public status `failed`
  - `reconciliation.classification="wrapper_lost"`
  - `evidence_source="ctrl_out"`
- New task-owned terminal envelopes are being written:
  - `1778152816752726016` has task terminal `completed`
  - `1778153107264229376` has task terminal `failed`
- Full task-list reconciliation showed:
  - `327` `runtime_conflict`
  - `185` `wrapper_lost`
  - `1` `terminal_ctrl_out`

Remaining evidence for this plan:

- `1778084345905438720` (`wazuh-case-rollup`) has:
  - latest task-log event `work_started`
  - no terminal task-log event
  - no terminal `ctrl_out`
  - a task-local outbox row containing a real result payload
  - a task-local reserved marker
  - runtime handle for a missing host process
  - current `task status` output: `failed`, event `work_started`,
    `completed_at=null`, no reconciliation
  - current `weft result` behavior: timed out in a bounded probe
  - current DB observation after the probe: `T1778084345905438720.outbox`
    still exists but is `claimed=true`
- `1778130295163805696` is an old manager task:
  - `task list --all` can show it as `running`
  - `manager list` selects newer active manager `1778153033994522624`
  - Docker shows `1778153033994522624` is the active manager in
    `governance-opsworker-serve-1`
  - the older manager registry records point to an older container id and are
    no longer the selected active manager

Important interpretation:

- The `wazuh-case-rollup` workload output is real task-domain output. The
  lifecycle bug is that Weft cannot currently present the result/evidence
  coherently after terminal publication was lost and the outbox row became
  claimed.
- The old manager row is not an active manager. `manager list` is already
  closer to truth than task-list reconstruction for this case.

## 4. Non-Goals

Do not implement these in this slice:

- Any broker message deletion.
- Any broad phase-4 monitor/reaper cleanup.
- Any direct ops database repair in product code.
- Any new public task lifecycle state such as `superseded` or
  `claimed_result`.
- Any retry or fix for Wazuh, host-policy, LLM schema, or other domain tasks.
- Any manager process-supervision redesign.
- Any SimpleBroker backend-specific SQL shortcut in normal Weft command code.
- Any fixed-limit replay of `weft.log.tasks`, `weft.state.tid_mappings`, or
  per-task queues.
- Any mock-only test replacement for broker-visible result, claim, manager, or
  status behavior.

If implementation starts needing destructive cleanup, direct SQL, a new public
task state, or a manager-runtime rewrite, stop and revise this plan.

## 5. Architecture Decision

Keep the fix in the shared read model and result command layer.

For task result evidence:

- Preserve the evidence priority from Release 2, but make it explicit in code:
  terminal task-log evidence, then typed terminal `ctrl_out`, then readable
  final one-shot outbox, then runtime liveness, then stale observer fallback.
- A readable final one-shot outbox must beat stale observer failure.
- A claimed outbox row is not readable final outbox proof. It should produce
  a distinct reconciliation diagnostic such as
  `claimed_result_without_terminal` or `claimed_result_blocked`, and
  `weft result` should return promptly with a clear unavailable/recovery error
  instead of waiting until timeout.
- The claimed-row diagnostic should use SimpleBroker's public queue statistics
  if possible. Do not query `weft.messages` directly from Weft command code.

For manager snapshots:

- Treat `weft.state.managers` as the authority for which manager is currently
  active.
- A manager task-log `task_spawned` event is historical lifecycle evidence, not
  sufficient proof that the manager is still active.
- If a task has manager metadata and its TID is not the selected active manager
  while another active manager exists, do not publish it as `running`.
- Keep public task states unchanged. Prefer a terminal public status such as
  `failed` with `reconciliation.classification="superseded_manager_record"`.
  That makes the old manager disappear from default non-terminal views while
  keeping it inspectable with `--all`.

Why not fix this in phase 4 cleanup:

- Cleanup needs trustworthy classifications.
- If phase 4 sees `1778084345905438720` as an ordinary failed task, it may
  archive or prune the wrong thing.
- If phase 4 sees superseded managers as running, it may refuse safe cleanup
  or keep stale manager task rows indefinitely.

## 6. Current Structure And Key Files

Files to modify for result evidence:

- `weft/commands/task_evidence.py`
  - Owns `TaskEvidenceSnapshot`, `known_tid_evidence()`,
    `task_local_terminal_evidence()`, `peek_final_outbox_evidence()`, and
    stale observer fallback.
  - This is the main place to add the claimed-result diagnostic helper and to
    ensure final outbox evidence is evaluated before stale observer failure.
- `weft/commands/result.py`
  - Owns `await_task_result()` and materialization of result queue names.
  - Add prompt failure/unavailable behavior when a result surface is blocked by
    claimed output instead of letting `weft result` time out silently.
- `weft/commands/_result_wait.py`
  - Owns one-shot waiting and outbox draining.
  - Touch only if the bug is in the one-shot wait loop. Keep the shared result
    logic DRY; do not fork a second one-shot wait path in `result.py`.
- `weft/commands/tasks.py`
  - Owns `task_status()`, `task_terminal_snapshot()`, and public task command
    wrappers.
  - Touch only if known-TID task status does not reuse the corrected evidence.
- `weft/commands/types.py`
  - Touch only if adding an additive result diagnostic field or status.

Files to modify for superseded manager snapshots:

- `weft/commands/system.py`
  - Owns project-wide status reconstruction and `TaskSnapshot` shaping.
  - This is the main place to ensure old manager task rows cannot remain
    public `running` once a different active manager is selected.
- `weft/commands/manager.py`
  - Read before editing, but avoid changes unless manager-list behavior itself
    is wrong. Ops evidence showed `manager list` was correct.
- `weft/core/manager_runtime.py`
  - Read for manager registry semantics. Touch only if there is no command-layer
    way to identify active/superseded managers without duplicating logic.

Tests to add or update:

- `tests/commands/test_task_evidence.py`
  - Main tests for known-TID evidence priority and claimed-result diagnostic.
- `tests/commands/test_result.py`
  - Main tests for `weft result` returning promptly for blocked claimed output.
- `tests/commands/test_status.py`
  - Main tests for project-wide status and manager supersession.
- `tests/commands/test_task_commands.py`
  - Add coverage only if task command wrappers need direct regression beyond
    `test_task_evidence.py` and `test_status.py`.
- `tests/helpers/weft_harness.py`
  - Read for existing broker setup patterns. Do not add broad helper changes
    unless multiple tests need the same real broker setup.

Docs to update:

- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/10-CLI_Interface.md` if result JSON changes
- `docs/specifications/03-Manager_Architecture.md` if manager registry/task
  snapshot semantics are clarified
- `docs/lessons.md` only if implementation reveals a reusable engineering
  lesson not already captured
- `docs/plans/README.md` if the current plan index is being maintained in the
  active branch

Read first:

- `weft/commands/task_evidence.py`
  - `known_tid_evidence()`
  - `task_local_terminal_evidence()`
  - `peek_final_outbox_evidence()`
  - `stale_observer_evidence()`
- `weft/commands/result.py`
  - `_await_result_materialization()`
  - `_await_single_result()`
  - `await_task_result()`
- `weft/commands/_result_wait.py`
  - `await_one_shot_result()`
  - `drain_available_outbox_values()` usage
- `weft/commands/system.py`
  - `_collect_task_snapshot_records()`
  - `_effective_public_status()`
  - `_collect_manager_records()` usage
- `weft/core/manager_runtime.py`
  - `_list_manager_records()`
  - `_select_active_manager_from_snapshot()`
  - `_manager_record_is_stale()`
- Existing tests in:
  - `tests/commands/test_task_evidence.py`
  - `tests/commands/test_result.py`
  - `tests/commands/test_status.py`

Comprehension checks before editing:

- What queue proves normal terminal lifecycle? Answer: `weft.log.tasks`.
- What task-local evidence can repair observation when the task log is missing
  terminal proof? Answer: typed terminal `ctrl_out`, then conservative final
  one-shot outbox.
- Why is a claimed outbox row different from a readable outbox row? Answer:
  ordinary peek/read helpers cannot safely decode its payload; it may be a
  result claimed by a crashed reader, but that is a recovery condition until
  the payload is readable.
- Which surface decides the active manager? Answer: the canonical active record
  from `weft.state.managers`, exposed by manager runtime/list helpers.
- Why should the old manager not become a new public state? Answer: public task
  lifecycle states are fixed; supersession is reconciliation metadata.

## 7. Desired Public Contract

For one-shot task with readable final outbox and no terminal task-log or
terminal `ctrl_out`:

- `weft task status TID --json` returns public `status="completed"`.
- `reconciliation.classification="result_without_terminal"`.
- `reconciliation.evidence_source="outbox"`.
- `completed_at` is set from observed outbox evidence when no better timestamp
  exists.
- `weft result TID --json` returns the outbox value.
- Status/result reads remain non-consuming unless the command is explicitly a
  consuming result command.

For one-shot task with claimed outbox residue and no terminal task-log or
terminal `ctrl_out`:

- `weft task status TID --json` must not look like an ordinary stale failure.
- It may retain public `status="failed"` only with a reconciliation diagnostic
  such as:

```json
{
  "classification": "claimed_result_without_terminal",
  "reason": "claimed_outbox_blocks_result_classification",
  "lifecycle_status": "failed",
  "evidence_source": "outbox",
  "claimed_messages": 1
}
```

- If implementation can safely recover/requeue claimed output through a
  SimpleBroker public API, that recovery must be an explicit command or
  explicit result-path policy with tests. Do not hide it inside status.
- If implementation cannot safely recover/requeue, `weft result TID --json`
  should return promptly with a clear error such as "result output is claimed
  and requires recovery" rather than timing out.

For superseded manager tasks:

- `manager list --json` continues to show the selected active manager.
- Default `weft status --json` and default `weft task list --json` show at most
  one running manager: the selected active manager.
- `weft task list --all --json` may show old manager tasks, but old manager
  tasks must not be `running` if a different active manager exists.
- Old manager tasks should carry reconciliation metadata such as:

```json
{
  "classification": "superseded_manager_record",
  "reason": "different_active_manager_selected",
  "lifecycle_status": "failed",
  "active_manager_tid": "1778153033994522624"
}
```

## 8. Invariants And Constraints

Preserve:

- TID format and immutability.
- Existing public task lifecycle states.
- Forward-only state semantics in write paths.
- `spec` and `io` immutability after TaskSpec creation.
- Existing reserved-queue policy.
- Runtime-only status of `weft.state.*` queues.
- Manager registry authority for active manager selection.
- Generator-based reads for append-only history.
- Non-consuming behavior for status and terminal snapshot reads.
- Exact-message acknowledgement behavior for explicit cleanup helpers.
- Backend neutrality: normal Weft command code must use SimpleBroker public
  APIs and existing Weft context helpers, not direct backend SQL.

Do not:

- Add a new public task state.
- Delete or unclaim messages from status reads.
- Treat arbitrary outbox messages as final results for persistent,
  interactive, streaming, or ambiguous tasks.
- Treat claimed outbox counts as readable final payloads.
- Make `weft result` consume output before it can return a stable result or
  stable recovery diagnostic.
- Make manager task status independent from manager registry truth.
- Add broad abstractions or refactors around queues, managers, or results.

Stop and re-plan if:

- the fix needs direct SQL outside tests;
- tests require mocking `Queue`, `WeftContext`, or manager registry instead of
  writing real broker rows;
- the implementation wants to add cleanup/reaper behavior;
- result recovery requires SimpleBroker API changes;
- old and new readers cannot coexist safely during rollout.

## 9. Implementation Tasks

### 1. Add failing tests for readable final outbox beating stale observer

Outcome: reproduce the unclaimed form of the `wazuh-case-rollup` evidence and
prove the intended result-without-terminal behavior.

Files to touch:

- `tests/commands/test_task_evidence.py`
- `tests/commands/test_result.py`
- possibly `tests/commands/test_task_commands.py` if wrapper behavior needs a
  direct proof

Approach:

- Use real broker-backed queues and existing context/test helpers.
- Create a one-shot non-persistent TaskSpec payload with default
  `T{tid}.outbox` and `T{tid}.ctrl_out`.
- Write a `weft.log.tasks` `work_started` event for the TID.
- Write a latest `weft.state.tid_mappings` entry whose host runtime handle is
  no longer live.
- Write one final JSON outbox result to `T{tid}.outbox`.
- Do not write terminal task-log or terminal `ctrl_out`.
- Assert `task_evidence.known_tid_evidence()` returns:
  - `status="completed"`
  - `classification="result_without_terminal"`
  - `source="outbox"`
  - `terminal=True`
  - the public result value
- Assert `tasks.task_status()` returns `completed` with the same
  reconciliation.
- Assert `result.await_task_result()` or the command-level result helper
  returns the outbox value.

Do not mock queue reads. If the test feels slow because it scans
`weft.log.tasks`, make the fixture smaller rather than mocking the scan.

Done signal:

- The new tests fail before implementation because stale observer or result
  waiting wins over final outbox evidence.

### 2. Fix known-TID evidence priority for readable final outbox

Outcome: readable final outbox evidence is considered before stale observer
failure and is propagated consistently to task status/result surfaces.

Files to touch:

- `weft/commands/task_evidence.py`
- `weft/commands/tasks.py` only if wrapper propagation is missing
- `weft/commands/result.py` or `weft/commands/_result_wait.py` only if result
  waiting still cannot return the unclaimed result

Approach:

- Keep the shared helper as the owner. Do not duplicate result classification
  in `tasks.py` or `result.py`.
- Ensure `known_tid_evidence()` checks `task_local_terminal_evidence()` before
  `runtime_evidence()` and stale observer fallback. It already appears to do
  this; the implementation may need to fix why claimed/unreadable state causes
  plain stale failure.
- Ensure `task_local_terminal_evidence()` returns final one-shot outbox
  evidence only for non-persistent, non-interactive, non-streaming tasks.
- Preserve conservative behavior for ambiguous outbox surfaces:
  - multiple final values may aggregate only if existing result semantics
    allow it;
  - partial stream fragments must not become completion proof;
  - persistent/interactive tasks do not use outbox alone as terminal proof.

Stop and re-evaluate if implementation starts changing Consumer, Manager, or
TaskSpec write paths. This task is a read-model fix.

Verification:

```bash
uv run --extra dev pytest tests/commands/test_task_evidence.py -q
uv run --extra dev pytest tests/commands/test_result.py -q
```

### 3. Add claimed-outbox diagnostics without cleanup

Outcome: claimed result residue becomes visible as a recovery condition rather
than a generic stale failure or a `weft result` timeout.

Files to touch:

- `weft/commands/task_evidence.py`
- `weft/commands/result.py`
- `weft/commands/types.py` only if an additive result diagnostic is needed
- `tests/commands/test_task_evidence.py`
- `tests/commands/test_result.py`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/10-CLI_Interface.md` if result JSON changes

Approach:

- Add a small helper that inspects queue stats for one outbox queue through
  SimpleBroker's public broker/stat APIs. Reuse existing context patterns.
  Do not query backend tables directly.
- Add a `TaskEvidenceSnapshot` classification for claimed outbox residue, for
  example `claimed_result_without_terminal`.
- This classification must not pretend the claimed row is a decoded final
  result. It should carry metadata such as outbox queue name and claimed count.
- If the task has readable terminal log or terminal `ctrl_out`, claimed outbox
  residue is only cleanup noise and must not override terminal lifecycle.
- If the task has readable final outbox, readable final outbox wins over
  claimed residue.
- If only claimed outbox residue exists and stale observer would mark failed,
  attach the claimed-result reconciliation diagnostic to the public snapshot.
- Make `weft result TID --json` return promptly for this condition. Prefer an
  additive result status such as `unavailable` or an additive diagnostic field
  if the CLI spec accepts it. If that is too broad, return `status="failed"`
  with a precise error, but do not return `timeout` for a known claimed-result
  blockage.

Test shape:

- Write an outbox result, then use the real queue API to claim it without
  deleting/acknowledging it. In SimpleBroker today, `Queue.read_one()` is the
  likely public way to create that claimed state; verify this in the test.
- Assert the message remains counted in queue stats as claimed.
- Assert `known_tid_evidence()` reports the claimed-result diagnostic.
- Assert `weft result` returns before the timeout budget is exhausted and
  includes a clear recovery error.
- Assert no status or result path deletes, unclaims, or consumes the message
  in this diagnostic-only task.

Stop and re-evaluate if:

- SimpleBroker has no backend-neutral way to detect claimed count for one
  queue;
- recovering claimed rows requires direct SQL;
- the desired result status would break existing clients.

### 4. Add failing tests for superseded manager task display

Outcome: reproduce the old-manager task-list bug without Docker or a real
foreground manager process.

Files to touch:

- `tests/commands/test_status.py`
- possibly `tests/commands/test_task_commands.py`

Approach:

- Use a real isolated broker context.
- Write a `weft.log.tasks` `task_spawned` event for old manager TID A. Include
  a TaskSpec payload with manager metadata (`role="manager"`,
  `foreground_serve=True` or the existing manager metadata shape).
- Write an active manager registry record for newer manager TID B to
  `weft.state.managers`.
- Write a `task_spawned` event for manager TID B if needed to make it visible
  in project task status.
- Do not write a terminal event for manager TID A. This reproduces ops: the old
  manager was superseded before task-log terminal proof appeared.
- Assert `manager list` / manager runtime helper selects only B as active.
- Assert default `cmd_status(... include_terminal=False ...)` or
  task-list-equivalent output does not show A as running.
- Assert `include_terminal=True` shows A as terminal or diagnostic, not
  running, with `reconciliation.classification="superseded_manager_record"`.

Do not mock `_collect_manager_records()`. Write registry rows and task-log rows
so the test covers the actual reducer.

Done signal:

- The test fails before implementation because A remains `running` until the
  generic runtime-less stale timeout elapses.

### 5. Implement superseded manager reconciliation in the status reducer

Outcome: task status agrees with manager registry truth immediately when a
newer active manager supersedes an older manager.

Files to touch:

- `weft/commands/system.py`
- `weft/commands/tasks.py` only if task command wrappers bypass the corrected
  status reducer
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`

Approach:

- In `_collect_task_snapshot_records()`, collect enough manager registry
  context to know:
  - selected active manager TID(s), normally one;
  - whether there is any active manager other than the task's TID.
- Detect manager task rows through existing manager metadata (`role="manager"`)
  rather than name-only matching.
- If a manager task row is non-terminal, has manager metadata, is not the
  selected active manager, and a different active manager exists, force the
  public snapshot to a terminal public status such as `failed`.
- Attach reconciliation metadata:
  - `classification="superseded_manager_record"`
  - `reason="different_active_manager_selected"`
  - `lifecycle_status="failed"`
  - `active_manager_tid`
- Do not change `manager list` unless tests prove it is wrong.
- Do not write a new task-log event for the old manager in this slice. This is
  a read-model correction. A future write-path improvement can publish a
  manager stop/superseded event if needed.

Stop and re-evaluate if:

- old manager classification requires changing manager registry selection;
- more than one active manager can be selected in the test setup;
- the implementation starts depending on Docker container inspection.

Verification:

```bash
uv run --extra dev pytest tests/commands/test_status.py -q
uv run --extra dev pytest tests/commands/test_task_commands.py -q
```

### 6. Update specs and lessons

Outcome: specs match the corrected behavior and phase 4 can depend on these
classifications.

Files to touch:

- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/10-CLI_Interface.md` if result JSON changes
- `docs/lessons.md` only for a durable lesson

Required spec statements:

- Readable final one-shot outbox evidence is terminal observation evidence only
  under conservative one-shot rules.
- Claimed outbox residue is not decoded result evidence. It is a recovery
  diagnostic until requeued or otherwise made readable by an explicit recovery
  path.
- Stale observer fallback must not erase result-surface diagnostics.
- Manager task snapshots must not publish non-active historical manager rows
  as `running` when the manager registry has selected a different active
  manager.
- Phase 4 monitor/reaper should treat `claimed_result_without_terminal` and
  `superseded_manager_record` as explicit classifications, not infer them
  independently.

### 7. Full verification and review

Outcome: the corrective slice is safe to release before phase 4.

Run focused tests first:

```bash
uv run --extra dev pytest tests/commands/test_task_evidence.py tests/commands/test_result.py tests/commands/test_status.py -q
```

Then run static gates:

```bash
uv run --extra dev --extra docker --extra django --extra macos-sandbox ruff format --check weft tests integrations/weft_django extensions/weft_docker extensions/weft_macos_sandbox
uv run --extra dev --extra docker --extra django --extra macos-sandbox ruff check weft tests integrations/weft_django extensions/weft_docker extensions/weft_macos_sandbox
uv run --extra dev --extra docker --extra django --extra macos-sandbox mypy weft integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox --config-file pyproject.toml
```

Then run the broader relevant command suite:

```bash
uv run --extra dev pytest tests/commands -q
```

If the implementation changes result waiting or queue claim behavior, also run
the Postgres-backed focused tests:

```bash
uv run --extra dev bin/pytest-pg -- tests/commands/test_task_evidence.py tests/commands/test_result.py tests/commands/test_status.py
```

Independent review is required before implementation is called done. Use the
review prompt from
`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`, pointed at
this plan, the touched specs, and the touched command modules.

## 10. Ops Validation After Release

After release and deploy to ops, use read-only checks first.

Expected checks:

```bash
ssh ops 'cd ~/governance && . ./.envrc >/dev/null 2>&1 || . ./.env >/dev/null 2>&1 || true; /opt/venv/bin/weft task status 1778084345905438720 --json'
ssh ops 'cd ~/governance && . ./.envrc >/dev/null 2>&1 || . ./.env >/dev/null 2>&1 || true; timeout 15s /opt/venv/bin/weft result 1778084345905438720 --json; echo rc=$?'
ssh ops 'cd ~/governance && . ./.envrc >/dev/null 2>&1 || . ./.env >/dev/null 2>&1 || true; /opt/venv/bin/weft task status 1778130295163805696 --json'
ssh ops 'cd ~/governance && . ./.envrc >/dev/null 2>&1 || . ./.env >/dev/null 2>&1 || true; /opt/venv/bin/weft manager list --json'
```

Expected results:

- `1778084345905438720` no longer appears as a plain stale failure without a
  reconciliation diagnostic.
- If its outbox remains claimed, `weft result` returns promptly with a claimed
  result/recovery diagnostic instead of timeout.
- If a safe explicit recovery has made the outbox readable, task status reports
  `completed` with `result_without_terminal`, and `weft result` returns the
  result.
- `1778130295163805696` is not `running` when `manager list` selects
  `1778153033994522624` or another newer active manager.
- Default `weft status --json` shows at most one running manager.

Do not run direct SQL repair as part of validation unless a separate operator
runbook explicitly authorizes it.

## 11. Rollback

This slice should be rollback-safe because it changes read-model and result
command behavior, not queue payload formats or write-path semantics.

Rollback expectations:

- Existing broker rows remain readable by older Weft versions.
- New reconciliation classifications are additive diagnostics.
- No new queue names are introduced.
- No cleanup or destructive operation is introduced.
- If `weft result --json` gains an additive status or field, clients that only
  inspect `tid`, `status`, `result`, and `error` should still receive those
  fields.

If rollback would strand claimed result rows or require direct DB mutation,
the implementation has crossed out of this plan's scope.

## 12. Fresh-Eyes Review Notes

Self-review questions before implementation:

- Does the plan accidentally ask status to delete, unclaim, or consume output?
  It must not.
- Does the plan infer completion from claimed outbox counts alone? It must not.
- Does the plan let stale observer fallback hide result-surface diagnostics?
  It must not.
- Does the plan make Docker inspection part of normal manager status? It must
  not.
- Does the plan add a new public task state? It must not.
- Does phase 4 get cleaner inputs after this slice? It should: explicit
  `result_without_terminal`, claimed-result diagnostics, and
  `superseded_manager_record`.

If any answer changes during implementation, stop and revise this plan before
continuing.

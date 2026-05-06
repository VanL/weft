# Status Coherence And Stale PID Liveness Plan

Status: draft
Source specs: see Source Documents below
Superseded by: none

Parent plan:
[`docs/plans/2026-05-06-lifecycle-reconciliation-architecture-plan.md`](./2026-05-06-lifecycle-reconciliation-architecture-plan.md)
Release slice: Release 1

## 1. Goal

Fix the status read model so Weft never publishes impossible task snapshots
such as `status="running"` with `completed_at` set, and so stale or weak host
PID evidence cannot reanimate terminal task-log state. Runtime/lifecycle
disagreement should be visible as diagnostic metadata, not as a backward public
state rewrite.

This release is intentionally narrow. It is a read-model correctness release
for `weft status`, `weft task status`, and the shared command/client snapshot
reducers. It does not add lifecycle monitoring, queue cleanup, archive output,
new public task states, or write-path behavior.

## 2. Source Documents

Source specs:

- [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md)
  [CC-2.2], [CC-2.3], [CC-3.2], [CC-3.4]
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-2], [MF-3], [MF-5], [MF-6], "Cleanup Boundary",
  "Queue Management Patterns"
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [STATE.1], [STATE.2], [STATE.4], [OBS.1], [OBS.2], [OBS.3], [OBS.10],
  [CTX.3]
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
  [CLI-1.2.1], [CLI-1.2], [CLI-6]

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
  defines this release train and the Release 1 boundary.
- [`docs/plans/2026-04-24-manager-status-container-pid-liveness-plan.md`](./2026-04-24-manager-status-container-pid-liveness-plan.md)
  fixed one manager-specific stale PID case. This plan generalizes the public
  status rule beyond manager rows because live ops now shows ordinary task rows
  with the same stale host PID reanimation problem.
- [`docs/plans/2026-04-24-runtime-handle-authority-migration-plan.md`](./2026-04-24-runtime-handle-authority-migration-plan.md)
  introduced the current `runtime_handle` authority boundary.
- [`docs/plans/2026-04-30-known-tid-terminal-snapshot-api-plan.md`](./2026-04-30-known-tid-terminal-snapshot-api-plan.md)
  added known-TID terminal evidence helpers. Reuse its concepts where useful,
  but do not fold typed `ctrl_out` or final outbox evidence into project-wide
  status in this release. That belongs to Release 2.

Spec delta required:

- Update the status/read-model sections to say terminal lifecycle proof wins
  over runtime liveness for public `status`.
- Add the additive JSON diagnostic field defined in this plan.
- Add backlinks from touched spec sections to this plan.

## 3. Live Evidence

Ops check on 2026-05-06:

```bash
ssh ops 'cd governance && . .env && /opt/venv/bin/weft status --json'
```

Observed shape:

- Broker reported about 206k messages.
- One active manager was visible in `managers`:
  `1778090743675379712`, supervised by Docker container `fe4e08ab4e4d`.
- Many task rows had terminal-looking events such as `control_stop`,
  `task_signal_stop`, and `work_failed`, with `completed_at` set, but public
  `status` was still `running`.
- Those rows carried old host runtime handles with weak
  `observations.host_pids` such as `74`, `95`, `100`, `135`, and `307`. Those
  PIDs currently exist on the host, but they are not proof that the original
  Weft task wrapper is still alive.

The important failure is not only "old PID exists." The public status reducer
is letting runtime liveness evidence override terminal lifecycle evidence,
which violates the state invariants exposed to operators.

## 4. Current Structure And Key Files

Files to modify:

- `weft/commands/system.py`
  - Owns `cmd_status()`, `collect_known_tid_snapshot()`,
    `_collect_task_snapshot_records()`, and `_effective_public_status()`.
  - This is the main implementation file for Release 1.
- `weft/commands/types.py`
  - Owns the public command/client `TaskSnapshot` dataclass.
  - Touch only to add the additive `reconciliation` field if
    `system.TaskSnapshot` gains the same field.
- `weft/_constants.py`
  - Touch only if adding named event/status mapping constants. Do not leave
    policy literals scattered through `system.py`.
- `tests/commands/test_status.py`
  - Main regression suite. Use real broker-backed queues and command helpers.
- `tests/commands/test_task_commands.py`
  - Touch only if public `TaskSnapshot` conversion or `weft task status`
    behavior needs direct coverage beyond `test_status.py`.
- `docs/specifications/05-Message_Flow_and_State.md`
  - Update [MF-5] and nearby implementation mapping/backlinks.
- `docs/specifications/07-System_Invariants.md`
  - Update observability/state notes if needed to make the public-status rule
    explicit.
- `docs/specifications/10-CLI_Interface.md`
  - Update [CLI-1.2.1] and [CLI-1.2] with the additive diagnostic field.
- `docs/lessons.md`
  - Add a durable lesson only if implementation exposes a reusable failure
    mode not already covered. Do not add a duplicate lesson for the sake of it.

Files to read before editing:

- `weft/commands/system.py`
  - `_iter_log_events()` uses generator-based replay. Preserve this.
  - `_latest_tid_mapping_entries()` reduces `weft.state.tid_mappings`.
  - `_runtime_handle_from_mapping()` rejects invalid handle shapes.
  - `_task_process_alive()` currently treats live host PIDs as runtime proof.
  - `_effective_public_status()` currently reanimates terminal non-`completed`
    host tasks as `running`.
  - `_collect_task_snapshot_records()` is the only place that has enough
    context to attach diagnostics to a public snapshot.
- `weft/commands/tasks.py`
  - `task_status()`, `task_snapshot()`, `list_task_snapshots()`, and
    `_public_snapshot()` wrap the shared status reducer. Do not fork the
    liveness policy here.
- `weft/ext.py`
  - `RunnerHandle.scoped_host_processes()` returns `(pid, None)` for weak
    `observations.host_pids` and `(pid, create_time)` when identity proof is
    available through `observations.host_processes`.
- `weft/helpers/__init__.py`
  - `pid_is_live()`, `pid_matches_create_time()`,
    `live_host_processes_from_handle()`, and `handle_has_live_host_process()`
    define the existing PID liveness primitives.
- `tests/commands/test_status.py`
  - Several current tests encode the old behavior. Change them deliberately,
    not accidentally.
- `docs/lessons.md`
  - Read the 2026-04-24 Runtime Handle Authority and 2026-05-04 External
    Supervisor Manager Liveness lessons before touching runtime liveness.

Comprehension checks before editing:

- What queue is lifecycle truth for task state? Answer:
  `weft.log.tasks`, reduced by TID.
- What queue carries runtime hints for task control and liveness? Answer:
  `weft.state.tid_mappings`.
- What makes a host PID weak evidence? Answer: a runtime handle with only
  `observations.host_pids` and no `observations.host_processes.create_time`.
- Why is `running` plus `completed_at` invalid? Answer: public status would be
  moving a terminal lifecycle observation backward to a non-terminal state.
- Which release owns typed `ctrl_out` and final-outbox fallback? Answer:
  Release 2, not this release.

## 5. Desired Public Contract

Public task lifecycle states stay unchanged:

- `created`
- `spawning`
- `running`
- `completed`
- `failed`
- `timeout`
- `cancelled`
- `killed`

Add one optional diagnostic field to task snapshots:

```json
{
  "reconciliation": {
    "classification": "runtime_conflict",
    "reason": "terminal_lifecycle_with_live_runtime",
    "lifecycle_status": "failed",
    "runtime_status": "running",
    "runtime_evidence": "host-pid",
    "runtime_evidence_strength": "weak"
  }
}
```

Field rules:

- Omit `reconciliation` when there is no conflict worth surfacing.
- `classification` is required when the field is present.
- For Release 1, allowed `classification` values are only:
  - `runtime_conflict`
  - `stale_status_payload`
- For Release 1, allowed `reason` values are only:
  - `terminal_lifecycle_with_live_runtime`
  - `weak_host_pid_ignored_for_terminal_lifecycle`
  - `contradictory_terminal_event_status`
- `lifecycle_status` is the status chosen for public `status`.
- `runtime_status` is `"running"` only when runtime evidence appears live.
  Omit it otherwise.
- `runtime_evidence` is one of:
  - `host-pid`
  - `runner`
  - `external-supervisor`
  - `none`
- `runtime_evidence_strength` is one of:
  - `strong` when `observations.host_processes` has a matching create time or a
    runner-native description says the runtime is live
  - `weak` when liveness comes only from `observations.host_pids` without
    create-time identity
  - `unknown` when the surface cannot classify strength

Do not add new public task states such as `conflicted` or `stale`. These are
diagnostic classifications, not lifecycle states.

JSON output from `weft status --json`, `weft status --json --all`,
`weft task status TID --json`, and client `TaskSnapshot` objects should carry
the same optional field. Text output does not need a new column in this release;
avoid column churn unless tests prove text users need it.

## 6. Invariants And Constraints

Preserve:

- TID format and immutability.
- `TaskSpec.spec` and `TaskSpec.io` immutability after resolved creation.
- Forward-only lifecycle semantics in public output.
- Existing queue names and queue roles.
- Existing public task state set.
- Generator-based replay for append-only queues such as `weft.log.tasks`,
  `weft.state.tid_mappings`, and `weft.state.managers`.
- Manager registry authority for active managers.
- Runtime-only status of `weft.state.*` queues.
- The current durable spine:
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log`.

Do not:

- Add monitor, reaper, archive, or cleanup logic.
- Consume or delete task-local queues.
- Fold typed terminal `ctrl_out` into project-wide status yet.
- Treat one-shot outbox messages as terminal proof yet.
- Add a parallel status database or SQL status table.
- Special-case PID numbers such as `1`, `74`, `100`, or `307`.
- Special-case Docker, governance task names, Wazuh metadata, or ops-only
  workloads.
- Hide domain failures. A `work_failed` task should remain `failed`, not become
  invisible or generic stale cleanup material.
- Broaden the write path unless a failing test proves the read model cannot
  uphold the public contract.

Stop and re-plan if:

- The implementation starts reading Docker, podman, Kubernetes, or supervisor
  APIs.
- The implementation needs to mutate old queue messages.
- The fix depends on monitor checkpoints or cleanup state.
- The status reducer starts using `peek_many(limit=...)` for correctness.
- Tests need heavy mocks of `simplebroker.Queue` to pass.
- The diagnostic field starts becoming a full evidence model. That is Release
  2.

## 7. Implementation Tasks

Execute these tasks in order. Use red-green TDD where the task says to write a
failing test first.

### 1. Add failing coverage for terminal log state plus weak live host PID

Outcome: the core live ops bug is reproducible with a real broker-backed test.

Files to touch:

- `tests/commands/test_status.py`

Read first:

- `tests/commands/test_status.py` helpers `_runtime_handle()` and
  `_write_task_log_entry()`
- `weft/commands/system.py` `_effective_public_status()`

Test setup:

- Use `prepare_project_root(tmp_path)` and `build_context()` as existing tests
  do.
- Write one `weft.log.tasks` row for a normal host task:
  - `event="work_failed"`
  - `status="failed"`
  - `taskspec.state.status="failed"`
  - `started_at` set
  - `completed_at` set
  - `runner_name="host"`
- Write a latest `weft.state.tid_mappings` row for that TID with
  `runtime_handle.control.authority="host-pid"` and
  `observations.host_pids=[100]`, but no `observations.host_processes`.
- Monkeypatch `status_cmd.handle_has_live_host_process` to return `True` for
  the first red test. That targets the current reanimation branch directly
  without depending on whichever host PID happens to exist on the test machine.

Assertions:

- `task_cmd.task_status(tid, context_path=root)` returns `status == "failed"`.
- `completed_at` is still set.
- `reconciliation.classification == "runtime_conflict"`.
- `reconciliation.reason == "weak_host_pid_ignored_for_terminal_lifecycle"`.
- `cmd_status(json_output=True, include_terminal=True, spec_context=root)`
  returns the same task with the same public status and diagnostic field.
- `cmd_status(json_output=True, spec_context=root)` without `--all` does not
  show this task, because it is terminal.

Done signal:

- The new test fails before implementation because current code returns
  `running` for the task.

### 2. Add failing coverage for contradictory terminal-looking events

Outcome: status reconstruction handles old records whose event and timestamps
are terminal-looking even when the payload status stayed `running`.

Files to touch:

- `tests/commands/test_status.py`

Test cases:

- `event="control_stop"`, payload `status="running"`,
  `taskspec.state.status="running"`, `completed_at` set -> public
  `status == "cancelled"`.
- `event="task_signal_stop"`, same stale status shape -> public
  `status == "cancelled"`.
- `event="control_kill"` or `event="task_signal_kill"`, stale status shape ->
  public `status == "killed"`.
- `event="work_failed"`, stale status shape -> public `status == "failed"`.
- `event="work_timeout"`, stale status shape -> public `status == "timeout"`.
- `event="work_completed"`, stale status shape -> public
  `status == "completed"`.

Use `pytest.mark.parametrize` for these cases. Keep the test table local and
readable. Do not introduce a generic test fixture that hides the event/status
contract.

Assertions:

- Public `status` equals the expected terminal status.
- `event` remains the original event string. Do not rewrite history.
- `completed_at` remains the original timestamp.
- `activity` and `waiting_on` are `None`.
- When no runtime evidence appears live,
  `reconciliation.classification == "stale_status_payload"`.
- When runtime evidence also appears live,
  `reconciliation.classification == "runtime_conflict"`.
- In both cases, `reconciliation.reason` must be
  `"contradictory_terminal_event_status"` for this test family.

Done signal:

- At least one parameter fails before implementation because current code trusts
  payload/state `status` too much.

### 3. Add failing coverage for active manager preservation

Outcome: Release 1 fixes stale task rows without hiding or demoting the active
manager.

Files to touch:

- `tests/commands/test_status.py`

Test setup:

- Write one active manager registry record to `weft.state.managers` for manager
  TID B. Use `runtime_handle.control.authority="external-supervisor"` or a
  current-process `host-pid` handle, matching existing manager tests.
- Write one running task-log row for manager TID B with `metadata.role="manager"`.
- Write one old terminal manager task-log row for manager TID A with:
  - `event="task_signal_stop"`
  - `status="running"` or `status="cancelled"` depending on which edge you are
    proving
  - `completed_at` set
  - `metadata.role="manager"`
  - weak stale `host_pids` evidence that appears live via monkeypatch

Assertions:

- `cmd_status(json_output=True, include_terminal=True, spec_context=root)`
  returns manager B in `managers`.
- Manager B's task snapshot is still `running`.
- Manager A's task snapshot is terminal and not `running`.
- No task snapshot has both `status == "running"` and `completed_at is not None`.

Done signal:

- This test protects the regression fixed by the older manager-status plan
  while the new general rule is implemented.

### 4. Implement read-time terminal status reconciliation

Outcome: the reducer derives a coherent lifecycle status before runtime
liveness is applied.

Files to touch:

- `weft/commands/system.py`
- `weft/_constants.py` if adding a shared terminal event mapping constant

Required action:

- Add a small helper near `_collect_task_snapshot_records()`, for example
  `_reconcile_lifecycle_status(payload, taskspec_state)`.
- The helper must return:
  - the chosen lifecycle status
  - an optional reconciliation diagnostic reason
- Keep the helper pure. It should not open queues, call psutil, inspect
  runtime handles, or mutate payload dictionaries.

Required mapping:

- Explicit terminal `payload.status` wins.
- If `payload.status` is not terminal, explicit terminal
  `taskspec.state.status` wins.
- If neither status is terminal but `completed_at` is set:
  - `control_stop` -> `cancelled`
  - `task_signal_stop` -> `cancelled`
  - `control_kill` -> `killed`
  - `task_signal_kill` -> `killed`
  - `work_failed` -> `failed`
  - `work_timeout` -> `timeout`
  - `work_limit_violation` -> `failed`
  - `work_completed` -> `completed`
- Do not infer terminal state from `completed_at` alone for unknown events.

Engineering notes:

- If the event mapping is more than a tiny local literal, put it in
  `weft/_constants.py` with the other lifecycle constants.
- Do not rewrite `record["event"]`; event history should remain observable.
- Do not alter task-log replay order.

Stop gate:

- If the helper starts needing runtime liveness input, split the work. Status
  reconciliation and runtime conflict diagnostics are separate concerns.

### 5. Change `_effective_public_status()` so terminal lifecycle status wins

Outcome: runtime liveness can create diagnostics, but it cannot change a
terminal public status back to `running`.

Files to touch:

- `weft/commands/system.py`

Required action:

- Remove or bypass the terminal-state branch that currently returns `running`
  when `_task_process_alive(mapping_entry)` or `runtime_live` is true.
- Keep existing non-terminal behavior for `created`, `spawning`, and `running`:
  - dead host PID on a running/spawning host task can still demote to `failed`
  - runtime-less stale running/spawning host tasks can still demote to `failed`
  - live manager registry records still preserve active manager rows
- Keep external runner live descriptions useful for non-terminal tasks.
- Do not special-case `completed`; all terminal states should obey the same
  "terminal wins" public-status rule.

Suggested shape:

- Let `_effective_public_status()` return only the public status string.
- Add a separate pure helper such as `_runtime_conflict_diagnostic(...)` that
  decides whether to attach `reconciliation`.
- This separation keeps DRY without making `_effective_public_status()` return
  a tuple everywhere.

Stop gate:

- If implementation starts duplicating liveness rules in `tasks.py`, stop and
  move the shared logic back to `system.py`.

### 6. Add the diagnostic field through the shared snapshot path

Outcome: `weft status`, `weft task status`, and client/task command snapshots
surface the same conflict metadata.

Files to touch:

- `weft/commands/system.py`
- `weft/commands/types.py`
- `weft/commands/tasks.py` only for conversion plumbing if needed
- `tests/commands/test_status.py`
- `tests/commands/test_task_commands.py` only if conversion coverage is not
  already exercised

Required action:

- Add `reconciliation: dict[str, Any] | None = None` to
  `weft.commands.system.TaskSnapshot`.
- Include `reconciliation` in `TaskSnapshot.to_dict()` only when not `None`.
  Do not emit `"reconciliation": null`.
- Add the same optional field to `weft.commands.types.TaskSnapshot`.
- Update `_public_task_snapshot()` in `system.py` and `_public_snapshot()` in
  `tasks.py` so the field survives command/client conversion.
- Keep the diagnostic dictionary JSON-friendly and stable. No datetimes, no
  process objects, no exceptions.

Diagnostic helper requirements:

- For terminal lifecycle status plus apparently live weak host PID evidence,
  return:
  - `classification="runtime_conflict"`
  - `reason="weak_host_pid_ignored_for_terminal_lifecycle"`
  - `lifecycle_status=<public terminal status>`
  - `runtime_status="running"`
  - `runtime_evidence="host-pid"`
  - `runtime_evidence_strength="weak"`
- For terminal lifecycle status plus apparently live strong runtime evidence,
  return:
  - `classification="runtime_conflict"`
  - `reason="terminal_lifecycle_with_live_runtime"`
  - `runtime_evidence_strength="strong"`
- For contradictory terminal-looking event/status payload without live runtime
  evidence, return:
  - `classification="stale_status_payload"`
  - `reason="contradictory_terminal_event_status"`
  - `lifecycle_status=<public terminal status>`
  - `runtime_evidence="none"`
  - `runtime_evidence_strength="unknown"`

YAGNI boundary:

- Do not add a `ReconciliationSnapshot` class unless plain dictionaries become
  hard to keep correct. One optional dict is enough for this release.

### 7. Update existing old-behavior tests deliberately

Outcome: tests no longer encode terminal-to-running reanimation as desired
behavior.

Files to touch:

- `tests/commands/test_status.py`

Known tests to review:

- `test_task_status_keeps_terminal_log_state_running_while_task_pid_is_alive`
- `test_task_status_uses_log_runtime_metadata_when_mapping_is_missing`
- Any nearby test that asserts terminal `cancelled`, `failed`, `timeout`, or
  `killed` becomes public `running`.

Required action:

- Replace those expectations with terminal public status plus diagnostics.
- Keep tests that prove non-terminal live tasks stay `running`.
- Keep tests that prove dead non-terminal host tasks become `failed`.
- Keep tests that prove active manager registry records stay visible.

Do not delete a test just because it fails. Rename and update it so the new
contract is explicit.

### 8. Update specs and traceability

Outcome: docs, code, and plan agree about the public status rule.

Files to touch:

- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/lessons.md` only if a non-duplicate lesson is needed

Required action:

- In [MF-5], state that terminal task-log evidence wins for public
  `status`; live runtime evidence can produce `reconciliation`, not a backward
  lifecycle rewrite.
- In [MF-5], keep the implementation mapping pointed at
  `weft/commands/status.py` or the current re-export path, but mention
  `weft/commands/system.py` if that is the actual owner.
- In [OBS] or [STATE], state that public snapshots must not emit non-terminal
  status with terminal timestamps.
- In [CLI-1.2.1] and [CLI-1.2], document the additive `reconciliation` field
  for JSON task snapshots.
- Add this plan as a backlink in touched specs.
- If adding a lesson, make it short: weak host PID liveness can explain a
  conflict but must not override terminal lifecycle state.

Stop gate:

- If the docs start describing Release 2 evidence sources such as typed
  terminal `ctrl_out` or final outbox status folding, remove that text from
  this slice unless the implementation actually includes it.

### 9. Run local verification

Outcome: focused behavior is proven, and adjacent status/task surfaces did not
regress.

Before running commands:

```bash
. ./.envrc
uv sync --all-extras
```

Focused red/green commands:

```bash
./.venv/bin/python -m pytest tests/commands/test_status.py -q
./.venv/bin/python -m pytest tests/commands/test_task_commands.py -q
```

Nearest broader checks:

```bash
./.venv/bin/python -m pytest tests/commands/test_result.py -q
./.venv/bin/python -m pytest tests/core/test_client.py -q
```

Static checks:

```bash
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft tests/commands/test_status.py tests/commands/test_task_commands.py
```

Broader gate before release:

```bash
./.venv/bin/python -m pytest
```

If the broader gate is skipped, record why. Skipping because the focused tests
passed is not enough for this release; status surfaces are shared by CLI,
client, and ops workflows.

## 8. Ops Validation

Run these only after the release is built and deployed to ops.

Required setup:

```bash
ssh ops
cd governance
. .env
```

Default status check:

```bash
/opt/venv/bin/weft status --json > /tmp/weft-status.json
/opt/venv/bin/python - <<'PY'
import json
from pathlib import Path

data = json.loads(Path("/tmp/weft-status.json").read_text())
bad = [
    task for task in data["tasks"]
    if task.get("status") == "running" and task.get("completed_at") is not None
]
print(f"running_with_completed_at={len(bad)}")
print(f"managers={[(m.get('tid'), m.get('status')) for m in data['managers']]}")
raise SystemExit(1 if bad else 0)
PY
```

All-tasks diagnostic check:

```bash
/opt/venv/bin/weft status --json --all > /tmp/weft-status-all.json
/opt/venv/bin/python - <<'PY'
import json
from collections import Counter
from pathlib import Path

data = json.loads(Path("/tmp/weft-status-all.json").read_text())
bad = [
    task for task in data["tasks"]
    if task.get("status") == "running" and task.get("completed_at") is not None
]
reconciled = [
    task for task in data["tasks"]
    if isinstance(task.get("reconciliation"), dict)
]
classes = Counter(task["reconciliation"]["classification"] for task in reconciled)
print(f"running_with_completed_at={len(bad)}")
print(f"reconciliation_classes={dict(classes)}")
print(f"sample_reconciled={[task['tid'] for task in reconciled[:10]]}")
raise SystemExit(1 if bad else 0)
PY
```

Known sample checks:

- Re-run `weft task status <tid> --json` for a few old sample TIDs from the
  incident, such as rows that previously showed `event="work_failed"` or
  `event="control_stop"` with `completed_at` and status `running`.
- Confirm each sample is terminal (`failed`, `cancelled`, `timeout`, or
  `killed`) or has an explicit `reconciliation` field explaining the conflict.
- Confirm active manager `1778090743675379712` or its current replacement
  remains visible in `managers`.
- Confirm `weft.spawn.requests` is not falsely reported as backed up. This
  release should not affect spawn queue contents.

Do not run cleanup on ops as part of this release.

## 9. Rollout And Rollback

Rollout:

1. Land this read-model fix.
2. Run local verification.
3. Run independent review of the implementation.
4. Update specs and backlinks in the same change.
5. Cut a Weft release.
6. Deploy to ops.
7. Run the ops validation commands above.
8. Do not start Release 2 until the ops output is understood.

Rollback:

- This release is backward-compatible with broker data. It reads existing
  queues differently but does not mutate them.
- If a regression appears, roll back the package. No queue restoration is
  required.
- The only public contract change is additive JSON diagnostic metadata. If that
  breaks a downstream consumer, the safer rollback is package rollback, not
  broker cleanup or field deletion from stored messages.

One-way doors:

- None expected. If implementation introduces deletion, archive formats, queue
  renames, TaskSpec schema changes, or public state changes, stop. That is a
  materially different plan.

## 10. Independent Review Prompt

Before implementation starts, ask an independent reviewer:

> Read
> `docs/plans/2026-05-06-status-coherence-and-stale-pid-liveness-plan.md`, the
> parent lifecycle reconciliation meta-plan, and the named status code paths.
> Look for errors, unsafe assumptions, hidden coupling, bad test design, and
> places where a zero-context implementer would make the wrong choice. Do not
> implement anything. Could you implement this confidently and correctly if
> asked?

Review focus:

- Does terminal lifecycle state always win in public `status`?
- Is diagnostic metadata additive and stable enough?
- Are tests broker-backed rather than mock-heavy?
- Does the plan avoid Release 2 evidence sources?
- Does the plan preserve active manager visibility?
- Are rollback and ops validation concrete?

The implementation author must respond to every review finding before coding.

## 11. Fresh-Eyes Review Notes

Self-review after drafting:

- The first tempting wrong path was to preserve the old normal-host behavior
  where terminal task logs could still display `running` while a PID was live.
  Live ops shows that rule is too broad. The plan now chooses the more correct
  path: terminal lifecycle status wins for all task types, and runtime
  disagreement becomes diagnostic metadata.
- The plan does not pull typed terminal `ctrl_out` or final outbox evidence
  into `weft status`. That would drift into Release 2.
- The diagnostic contract is intentionally small. It is enough to explain the
  Release 1 conflict without building the full evidence/reconciliation model.
- The tests use real broker-backed queues and monkeypatch only PID-liveness
  boundaries. They do not mock SimpleBroker queues or manager/task lifecycle.
- The ops validation uses both default status and `--all`, because default
  status should omit terminal tasks after the fix.

If implementation reveals that terminal lifecycle status cannot safely win for
all task types, stop and return to the parent plan before coding around it.
That would mean the chosen rule is wrong or under-specified, not that the code
needs a clever exception.

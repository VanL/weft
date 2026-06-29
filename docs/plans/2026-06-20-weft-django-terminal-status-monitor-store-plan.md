# Weft Django Terminal Status Monitor Store Plan

Status: completed
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/09-Implementation_Plan.md [IP-1.1]; docs/specifications/10-CLI_Interface.md [CLI-1.2], [CLI-1.2.1]; docs/specifications/13C-Using_Weft_With_Django.md [DJ-2.1], [DJ-2.2], [DJ-8.3]
Superseded by: none

## 1. Goal

Fix the public client and `weft-django` known-TID status path so a task whose
raw task-log rows have retired still resolves terminal state from the Monitor
store. The concrete failure is that `Task.snapshot()` and `weft task status`
can see a terminal Monitor-store record, while `Task.terminal_snapshot()` and
therefore `weft_django.status(tid)` return `unknown`. The fix should make the
shared known-TID status reconstruction path authoritative for terminal
Monitor-store proof, then project that result into the compact terminal
snapshot API without making Django consume the heavier diagnostic
`TaskSnapshot` path.
Changing the specs is in scope for this slice; the intended spec change is to
extend terminal snapshot semantics, not to redefine Django `status(tid)` as a
full diagnostic snapshot.

## 2. Source Documents

Source specs:

- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]: public status and
  known-TID terminal snapshots reuse shared task-evidence interpretation.
- `docs/specifications/09-Implementation_Plan.md` [IP-1.1]: `weft.client`
  exposes stable task status and terminal snapshot surfaces for integrations.
- `docs/specifications/10-CLI_Interface.md` [CLI-1.2], [CLI-1.2.1]: task
  status may fall back to the Monitor store after raw task-log retirement.
- `docs/specifications/13C-Using_Weft_With_Django.md` [DJ-2.1], [DJ-2.2],
  [DJ-8.3]: Django depends on `weft.client`, and `weft_django.status(tid)` is
  a direct known-TID reconciliation helper using the non-consuming terminal
  snapshot API.

Guidance:

- `AGENTS.md`
- `docs/agent-context/principles.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/runtime-and-context-patterns.md`
- `docs/agent-context/runbooks/testing-patterns.md`
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`

Related plans:

- `docs/plans/2026-04-30-known-tid-terminal-snapshot-api-plan.md` introduced
  the compact non-consuming known-TID terminal snapshot API.
- `docs/plans/2026-04-21-weft-client-and-django-first-class-hardening-plan.md`
  made `weft-django` depend on the public client boundary.
- `docs/plans/2026-05-16-monitor-durable-collation-store-plan.md` introduced
  the durable Monitor store that survives raw task-log retirement.
- `docs/plans/2026-05-31-task-monitor-orphan-log-and-status-reconciliation-plan.md`
  made status fall back to Monitor-store data when raw lifecycle rows are no
  longer available.

## 3. Context And Key Files

Files to modify:

- `weft/commands/tasks.py`
- `weft/core/task_evidence.py` only if a pure converter or classification
  helper is needed; do not import Monitor-store storage into this module
  casually.
- `weft/client/_task.py` only if its docstring or type contract needs
  clarification. The preferred implementation should not need logic changes
  here because it delegates to `weft.commands.tasks.task_terminal_snapshot()`.
- `weft/client/_namespaces.py` only if namespace docstrings or exported
  behavior notes need clarification.
- `integrations/weft_django/weft_django/client.py` only for docstrings or
  wrapper cleanup. Do not switch `status()` to the heavier `snapshot()` path
  unless the spec is deliberately changed first.
- `integrations/weft_django/README.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/09-Implementation_Plan.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/specifications/13C-Using_Weft_With_Django.md`
- Tests listed in the testing section.

Read first:

- `weft/commands/tasks.py`, especially `task_terminal_snapshot()`,
  `task_snapshot()`, `task_status()`, `_monitor_store_task_snapshot()`, and
  `_task_snapshot_from_monitor_store_record()`.
- `weft/core/task_evidence.py`, especially `known_tid_evidence()`,
  `terminal_snapshot_from_evidence()`, and the documented evidence priority.
- `weft/core/monitor/store.py`, especially `MonitorTaskCollationRecord` and
  `MonitorTaskEventUpdate`, so the conversion reads the right terminal fields.
- `weft/client/_task.py` and `weft/client/_namespaces.py`, to confirm the
  public client is only a thin command-layer adapter.
- `integrations/weft_django/weft_django/client.py`, especially
  `WeftSubmission.status()`, `DjangoWeftClient.status()`, module-level
  `status()`, and module-level `terminal_snapshot()`.
- `integrations/weft_django/tests/test_weft_django.py`, especially
  `test_status_uses_terminal_snapshot_not_diagnostic_snapshot()`.
- `tests/commands/test_run.py`, especially the existing Monitor-store fallback
  tests for `task_status()`.

Current structure:

- `weft_django.status(tid)` and `WeftSubmission.status()` call
  `Task.terminal_snapshot()`, not `Task.snapshot()`.
- `Task.terminal_snapshot()` delegates to
  `weft.commands.tasks.task_terminal_snapshot()`.
- `task_terminal_snapshot()` currently normalizes a TID, asks
  `task_evidence.known_tid_evidence()` for task-log, task-local, runtime, and
  stale-observer evidence, and returns `status="unknown", source="observer"`
  when that bounded evidence is absent.
- `Task.snapshot()` delegates to `task_snapshot()`, which calls
  `task_status()`. For a full TID, `task_status()` first tries bounded task-log
  reconstruction and then falls back to `_monitor_store_task_snapshot()`.
- `_monitor_store_task_snapshot()` opens the Monitor store and converts a
  `MonitorTaskCollationRecord` into a diagnostic `TaskSnapshot`. The compact
  terminal snapshot path should reuse that status fallback and project terminal
  results, not open the Monitor store through a second reader.

Comprehension checkpoints:

- Which public Django helper currently calls `terminal_snapshot()` instead of
  `snapshot()`, and why does the spec want that?
- Where does `task_status()` already consult the Monitor store, and why does
  `task_terminal_snapshot()` miss that same evidence today?
- Which Monitor-store records are safe terminal proof, and which nonterminal
  records would be stale diagnostic history rather than live task proof?

## 4. Invariants And Constraints

Preserve these invariants:

- TID format and immutability do not change.
- State transitions remain forward-only; the fix must not write synthetic
  lifecycle events.
- `weft.log.tasks` and task-local queues remain the durable lifecycle and
  terminal observation surfaces. The Monitor store is a read-only fallback for
  status after raw task-log retirement.
- `Task.terminal_snapshot()` remains non-consuming. It must not call
  `ack_terminal_snapshot()`, delete queue messages, or consume result rows.
- `weft_django` must continue to depend only on `weft.client`; it must not
  import `weft.commands.*`, `weft.core.*`, or Monitor-store internals.
- `weft_django.status(tid)` must remain a compact known-TID status helper. It
  must not start returning the full diagnostic `TaskSnapshot` shape unless
  `docs/specifications/13C-Using_Weft_With_Django.md` is intentionally changed.
- Do not treat Monitor-store nonterminal rows as live proof. A retired
  `status="running"` Monitor-store row without task-local, runtime, PONG, or
  task-log evidence is diagnostic history, not evidence that a task is live
  now.
- Task-local terminal evidence still outranks Monitor-store fallback. If an
  outbox or typed terminal `ctrl_out` envelope is visible, keep the current
  source and acknowledgement targets.
- Monitor-store fallback must be read-only and bounded by known full TID. Do
  not add broad Monitor-store scans to `terminal_snapshot()`.
- Schema-missing Monitor stores should preserve the current default observer
  behavior. A context without Monitor-store tables should not turn ordinary
  unknown tasks into noisy failures.
- Non-schema Monitor-store read failures should not raise out of
  `terminal_snapshot()`; they should return an `unknown` snapshot with
  diagnostic metadata, analogous to `task_status()`'s
  `monitor_store_unavailable` reconciliation.

Hidden couplings:

- `weft_django.status()`, `WeftSubmission.status()`, and
  `DjangoWeftClient.status()` are three wrappers over the same underlying
  terminal snapshot contract. Updating only one wrapper leaves inconsistent
  integration behavior.
- `client.tasks.terminal_snapshot()` and `Task.terminal_snapshot()` share the
  same command helper. The fix should land there rather than duplicating logic
  in both client wrappers.
- `task_status()` already owns Monitor-store diagnostic snapshot conversion.
  Reuse nearby Monitor-store read/error handling in `weft/commands/tasks.py`;
  do not create a second Monitor-store access policy in the Django package.

Rejected approach:

- Do not fix this by changing `weft_django.status(tid)` to call
  `Task.snapshot()`. That would make ops reconciliation work for this case,
  but it contradicts [DJ-8.3], changes the shape/performance contract of
  `status(tid)`, and leaves `Task.terminal_snapshot()` broken for every other
  integration.

Stop and re-plan if:

- the implementation needs `weft_django` to import command/core modules;
- a nonterminal Monitor-store row is about to be returned as live/running
  evidence from `terminal_snapshot()`;
- a status read starts consuming outbox or `ctrl_out` messages;
- the fix requires changing queue names, task-log event shapes, or Monitor
  store schema;
- tests start mocking the command-layer terminal evidence path instead of
  proving it with a real broker-backed Monitor store record.

## 5. Tasks

1. Clarify the specs before code changes.
   - Outcome: specs explicitly say that the compact known-TID terminal
     snapshot API may use terminal Monitor-store collation rows after raw
     task-log retirement.
   - Files to touch:
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/09-Implementation_Plan.md`
     - `docs/specifications/10-CLI_Interface.md`
     - `docs/specifications/13C-Using_Weft_With_Django.md`
   - Required edits:
     - In [MF-5], add Monitor-store terminal fallback to the shared evidence
       priority after task-local/runtime bounded evidence is absent. State
       that only terminal Monitor-store rows count for terminal snapshots.
     - In [IP-1.1], clarify that `Task.terminal_snapshot()` observes terminal
       Monitor-store proof without consuming queue messages.
     - In [CLI-1.2] or [CLI-1.2.1], distinguish full diagnostic
       `TaskSnapshot` fallback from compact `TaskTerminalSnapshot` fallback.
     - In [DJ-8.3], document the new `terminal_monitor_store` or equivalent
       classification as additive metadata and keep `snapshot(tid)` as the
       diagnostic helper for full fields.
   - Stop if:
     - the spec update starts making the Monitor store a live status authority
       for nonterminal rows.
   - Done when:
     - the docs define the desired behavior clearly enough to write the
       failing tests without inference.

2. Add a failing command-layer regression for terminal Monitor-store fallback.
   - Outcome: today's `task_terminal_snapshot()` failure is captured directly.
   - Files to touch:
     - `tests/commands/test_run.py` or `tests/commands/test_task_commands.py`
   - Test shape:
     - Use `prepare_project_root(tmp_path)` and `build_context(spec_context=root)`.
     - Seed a real Monitor store with `open_monitor_store(context)`,
       `store.ensure_schema()`, and `store.upsert_task_event(MonitorTaskEventUpdate(...))`.
     - Use a terminal failed or completed row with `terminal_seen=True`,
       `terminal_event`, `terminal_status`, `return_code`, `state["error"]`,
       and a `taskspec_summary`.
     - Do not write raw `weft.log.tasks`, task-local outbox, `ctrl_out`, or
       runtime mapping evidence for this TID.
     - Assert `task_cmd.task_terminal_snapshot(tid, context=context)` returns
       the terminal status, `source="monitor_store"`, `terminal=True`, and
       classification metadata such as `terminal_monitor_store`.
     - Assert `ack_targets == ()` because Monitor-store fallback is read-only
       and has no exact queue message to acknowledge.
   - Also add:
     - a bare no-store/no-schema case that still returns
       `status="unknown", source="observer"`;
     - a non-schema store-read failure case, monkeypatching `open_monitor_store`
       to raise, that returns `status="unknown"` with diagnostic metadata
       rather than raising.
   - Stop if:
     - the test uses mocks for the success path instead of a real
       Monitor-store record.
   - Done when:
     - the success test fails before implementation because the current source
       is `observer` and status is `unknown`.

3. Project compact terminal snapshots from the shared status fallback.
   - Outcome: `task_terminal_snapshot()` returns terminal state from the
     Monitor store when no stronger local evidence exists, without adding a
     second Monitor-store read policy.
   - Files to touch:
     - `weft/commands/tasks.py`
   - Implementation approach:
     - Keep `_monitor_store_task_snapshot()` as the only Monitor-store status
       reader for this path.
     - Mark terminal Monitor-store diagnostic snapshots with additive
       reconciliation metadata such as
       `classification="terminal_monitor_store"`.
     - Add a small projection helper, for example
       `_terminal_snapshot_from_status_snapshot(...)`, that converts only
       terminal shared-status snapshots and `monitor_store_unavailable`
       diagnostics into `TaskTerminalSnapshot`.
     - In `task_terminal_snapshot()`, after `known_tid_evidence()` returns no
       stronger task-local/runtime/log evidence, call the shared
       `task_status()` path for the full TID and pass the result through the
       projection helper.
     - If `task_status()` returns a nonterminal Monitor-store snapshot, the
       projection returns `None` and `task_terminal_snapshot()` falls through
       to the existing observer behavior.
     - Store-read failures stay owned by `_monitor_store_task_snapshot()`; the
       projection only preserves the existing `monitor_store_unavailable`
       diagnostic in compact form.
   - Constraints:
     - Do not move Monitor-store imports into `weft.core.task_evidence.py`
       unless a pure shape conversion is split cleanly from storage access.
     - Do not add acknowledgement targets for Monitor-store fallback.
     - Do not add a second Monitor-store reader for terminal snapshots.
   - Stop if:
     - the code wants a new public status index, persistent cache, or fallback
       queue.
   - Done when:
     - the command-layer tests from task 2 pass.

4. Prove public client and Django adapter behavior.
   - Outcome: all public wrappers see the same fixed terminal evidence while
     Django remains a thin client adapter.
   - Files to touch:
     - `tests/core/test_client.py`
     - `integrations/weft_django/tests/test_weft_django.py`
     - `integrations/weft_django/weft_django/client.py` only if docstrings or
       small wrapper cleanup is needed.
   - Test shape:
     - Add a `Task.terminal_snapshot()` or `client.tasks.terminal_snapshot()`
       test that uses a real broker-backed Monitor-store terminal record and
       asserts the public client returns the monitor-backed terminal snapshot.
       This may reuse the command helper setup, but it must call the public
       client path.
     - Keep or update
       `test_status_uses_terminal_snapshot_not_diagnostic_snapshot()` so it
       still proves `weft_django.status()` does not call `snapshot()`.
     - Add a thin Django adapter test showing `weft_django.status(tid)` returns
       a failed/completed `TaskTerminalSnapshot` when the public client
       terminal snapshot source is `monitor_store`. It is acceptable for this
       adapter-only test to fake the client, because the command and client
       tests already prove the real Monitor-store behavior.
   - Stop if:
     - Django wrapper tests start importing Monitor-store helpers.
   - Done when:
     - Django still delegates to `terminal_snapshot()` and returns the
       monitor-backed terminal state.

5. Update user-facing docs and traceability.
   - Outcome: downstream integrations know which helper to call and what
     metadata to expect.
   - Files to touch:
     - `integrations/weft_django/README.md`
     - the spec files from task 1
     - module docstrings in touched code if ownership wording changes
   - Required edits:
     - In the Django README, clarify that `status()`/`terminal_snapshot()` use
       Weft's compact known-TID terminal status path, including Monitor-store
       terminal fallback after raw logs retire.
     - Preserve the distinction between `status()`/`terminal_snapshot()` and
       `snapshot()`: the former is compact terminal/live state; the latter is
       diagnostic task metadata.
     - Add or keep backlinks from touched specs to this plan.
   - Stop if:
     - docs imply Django owns durable task lifecycle state.
   - Done when:
     - docs match the implemented wrapper and command behavior.

6. Run the focused and release-relevant gates.
   - Outcome: the behavior is locally proved at the command, client, and
     Django adapter layers.
   - Commands:
     - `./.venv/bin/python -m pytest tests/commands/test_run.py -q -n 0`
     - `./.venv/bin/python -m pytest tests/commands/test_task_commands.py -q -n 0`
     - `./.venv/bin/python -m pytest tests/core/test_client.py -q -n 0`
     - `./.venv/bin/python -m pytest integrations/weft_django/tests/test_weft_django.py -q -n 0`
     - `./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q`
     - `./.venv/bin/python -m ruff check weft integrations/weft_django tests`
     - `./.venv/bin/python -m mypy weft integrations/weft_django/weft_django --config-file pyproject.toml`
   - Stop if:
     - a focused test requires broad sleeps, live managers, or process
       execution to prove this read-only fallback. The intended proof is a
       real broker-backed Monitor-store row, not a live runtime race.
   - Done when:
     - all focused gates pass and no doc/spec metadata check fails.

## 6. Testing Plan

Use red-green TDD for the command-layer regression. The failing test is clean:
seed a real Monitor-store terminal record with no raw task-log or task-local
terminal evidence, then call `task_terminal_snapshot()`. Today it returns
`unknown`; after the fix it should return the terminal Monitor-store snapshot.

Keep real:

- `prepare_project_root(tmp_path)`
- `build_context(spec_context=root)`
- real broker-backed queues
- real `open_monitor_store(context)` and `MonitorTaskEventUpdate` for the
  success path
- public `weft.client` wrappers for client-level proof

Mocks allowed:

- monkeypatch `open_monitor_store` only for the store-unavailable error path;
- fake the public client only in the thin Django adapter test that proves
  delegation shape, because command and client tests already prove the real
  Monitor-store behavior.

Do not mock:

- SimpleBroker queue reads;
- the command-layer `task_terminal_snapshot()` success path;
- `weft_django` into importing command or core helpers;
- Monitor-store record shape in the main regression.

Tempting edge case out of scope:

- Do not implement live-state fallback from nonterminal Monitor-store rows.
  That would be a separate status semantics change and risks reanimating stale
  running records after raw log retirement. This slice is terminal fallback
  only.

Post-deploy observation:

- In an ops context with retired raw task-log rows, a known failed TID should
  produce the same terminal status through `weft task status TID --json`,
  `python -c 'import weft_django; print(weft_django.status(TID))'`, and the
  host application's reconciliation command.
- Stale submitted rows in app databases should stop being skipped as
  `unknown` when Weft has terminal Monitor-store proof.

## 7. Verification And Gates

Per-task verification is listed in the task breakdown. Final gates before
claiming implementation complete:

```bash
./.venv/bin/python -m pytest tests/commands/test_run.py -q -n 0
./.venv/bin/python -m pytest tests/commands/test_task_commands.py -q -n 0
./.venv/bin/python -m pytest tests/core/test_client.py -q -n 0
./.venv/bin/python -m pytest integrations/weft_django/tests/test_weft_django.py -q -n 0
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
./.venv/bin/python -m ruff check weft integrations/weft_django tests
./.venv/bin/python -m mypy weft integrations/weft_django/weft_django --config-file pyproject.toml
```

If the implementation changes only docs plus `weft/commands/tasks.py`, the
four focused pytest files are enough before ruff/mypy. If implementation drifts
into Monitor-store schema, TaskMonitor collation, result consumption, or
queue-retention behavior, stop and expand the plan before running broader
gates.

## 8. Independent Review Loop

External review is required when available because this changes a public
adapter/status contract used by Django applications and other clients. Use a
different agent family if available.

Suggested review prompt:

> Read the plan at
> `docs/plans/2026-06-20-weft-django-terminal-status-monitor-store-plan.md`.
> Carefully examine the plan and the associated code. Look for errors, bad
> ideas, and latent ambiguities. Do not implement anything. Could you implement
> this confidently and correctly if asked?

Reviewer should read:

- this plan;
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5];
- `docs/specifications/09-Implementation_Plan.md` [IP-1.1];
- `docs/specifications/10-CLI_Interface.md` [CLI-1.2], [CLI-1.2.1];
- `docs/specifications/13C-Using_Weft_With_Django.md` [DJ-8.3];
- `weft/commands/tasks.py`;
- `weft/core/task_evidence.py`;
- `integrations/weft_django/weft_django/client.py`;
- existing Monitor-store fallback tests in `tests/commands/test_run.py`.

Feedback handling:

- Respond to every reviewer finding before implementation.
- Update this plan for accepted findings.
- If a finding is rejected, record why the current approach is still the best
  fit and which spec or invariant supports that choice.

Implementation review status:

- Gemini review could not run because the CLI rejected the untrusted worktree
  in headless mode.
- Qwen review could not run because the configured model was unavailable.
- Claude review could not run because the CLI lacked valid authentication.
- Codex review started but did not return findings within a useful window and
  was cancelled after it drifted into broad unrelated dirty-tree inspection.
- Self-review plus focused command/client/Django tests were completed.

## 9. Out Of Scope

- Fixing `mm-governance` resubmission, non-Grype filtering, prompt size, or
  application-record reconciliation logic.
- Changing Weft manager startup behavior or ManagerStartFailed retry policy.
- Making `weft_django` own task lifecycle rows or durable status truth.
- Adding a new Django model, result backend, scheduler, retry system, or
  admin dashboard.
- Changing Monitor-store schema, TaskMonitor collation, queue names, task-log
  event shapes, or task-local cleanup policy.
- Returning full diagnostic `TaskSnapshot` objects from
  `weft_django.status(tid)`.
- Treating nonterminal Monitor-store rows as live evidence.

## 10. Fresh-Eyes Review

Author self-review status: completed for this draft.

Findings:

- The first draft risked treating Monitor-store `running` rows as live status
  in `terminal_snapshot()`. The plan now limits Monitor-store fallback to
  terminal rows only and names nonterminal fallback as out of scope.
- The first draft could have implied a Django-only switch to `snapshot()`.
  The plan now explicitly rejects that workaround because it conflicts with
  [DJ-8.3] and leaves the public terminal snapshot API broken.
- Implementation feedback corrected the planned shape from a second
  terminal-only Monitor-store read to one shared status reconstruction path
  with compact and diagnostic projections.
- External review was attempted but unavailable in this environment. The
  implementation was completed with self-review and focused tests.

Residual risk:

- `terminal_monitor_store` is new additive metadata. Keep it stable and update
  specs, README, and tests together if the name ever changes.

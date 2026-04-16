# Release-Gate Failure Audit Findings

Status: findings
Source specs: None - audit artifact
Superseded by: none

Date: 2026-04-08
Plan: [release-gate-failure-audit-plan.md](./release-gate-failure-audit-plan.md)
Platform: Darwin

## Summary

- The `90fa5cc` release-gate failures are real and split into two families:
  prompt-mode interactive `:quit` hangs and ordinary host-task completion
  timeouts seen by `wait_for_completion()`.
- The interactive family is classifiable from current evidence: it is a prompt
  shutdown bug in the CLI/session-exit path, with manager idle handling as a
  contributing lifecycle layer.
- The list/task family is also real, but the audit cannot prove a tighter
  waiter-vs-publication-vs-live-runtime subtype on `90fa5cc` because timeout
  failures there do not capture the required queue and liveness state.
- `tests/helpers/weft_harness.py` at `90fa5cc` also had a real terminal-event
  recognition gap in `wait_for_completion()`, but the failing CI logs do not
  prove that this specific waiter bug is the sole cause of the timed-out
  `test_cli_list_task` cases.
- The list/task family is still bounded meaningfully: it is not a
  `weft/commands/status.py` projection bug and not a reverse-TID formatting
  bug. The narrowest supported owner is the manager/task-finalization boundary,
  with the harness waiter as the observing layer.
- Current local `HEAD` (`8b38a3f`) no longer reproduces the focused cases. The
  material post-`90fa5cc` changes are in `weft/commands/run.py`,
  `weft/core/manager.py`, `tests/helpers/weft_harness.py`, and the PTY helper
  setup, not in `weft/commands/status.py`.

## Findings

### [P1] Interactive `:quit` on `90fa5cc` is a CLI prompt-loop shutdown bug with manager lifecycle contribution

What failed:

- On commit `90fa5cc`, prompt-mode interactive sessions do not reliably exit
  after the user types `:quit`.
- This classifies as a CLI prompt-loop shutdown bug, not as a mere PTY-output
  artifact.

Why it matters:

- `:quit` is the user-visible success path for closing an interactive session.
- A session that acknowledges shutdown work but leaves the CLI alive is a
  release-blocking contract failure on the host interactive path.

What evidence proves it:

- GitHub `Test` run `24118464057`, job `70367306422`, on commit `90fa5cc`
  fails `tests/cli/test_cli_run_interactive_tty.py::test_interactive_python_repl_outputs`
  because the interactive session does not exit after `:quit`.
- GitHub `Release Gate` run `24118465039`, job `70367308790`, on the same
  commit shows the same failure with trailing PTY output that includes a `STOP`
  acknowledgement payload but still no process exit.
- In `weft/commands/run.py` at `90fa5cc`, prompt-mode `:quit` only closes
  input and breaks the prompt loop. It does not run the later
  close-input -> `STOP` -> `KILL` escalation that exists on current `HEAD`.
- `tests/cli/test_cli_run_interactive_tty.py` at `90fa5cc` sets
  `WEFT_MANAGER_LIFETIME_TIMEOUT=1.0` and `WEFT_MANAGER_REUSE_ENABLED=0`, while
  `weft/core/manager.py` at that commit still treats active non-persistent
  child tasks as idle-shutdown candidates.
- Current local `HEAD` focused repros pass after `1554d30` moved prompt-mode
  `:quit` onto explicit shutdown escalation and adjusted manager idle handling,
  with `8b38a3f` also widening the PTY helper lifetime.

Which file or layer owns it:

- Primary owner: `weft/commands/run.py` prompt-mode interactive shutdown path.
- Contributing owner: `weft/core/manager.py` idle-child termination behavior
  under the PTY helper's short-lived manager configuration.

What the smallest correct next fix slice should be:

- Keep the fix surface on prompt-mode shutdown semantics and active-child
  manager lifecycle handling.
- The narrow slice is explicit close-input -> `STOP` -> `KILL` escalation tied
  to session state, plus preventing active interactive children from being
  treated as idle-shutdown candidates.
- Do not treat a timeout increase by itself as the fix.

### [P1] The `test_cli_list_task` failures on `90fa5cc` are pre-projection completion-path failures, not `status.py` or reverse-TID bugs

What failed:

- On commit `90fa5cc`, both `test_list_and_task_status` and
  `test_task_tid_reverse` time out in `WeftTestHarness.wait_for_completion()`
  after `_submit_task()` already returned a TID.
- This is a completion-path failure before any `weft task list` or `weft task
  status` assertion executes.

Why it matters:

- The release gate is failing on ordinary host task completion, not on the
  user-facing projection or formatting code the test names suggest.
- A fix aimed at `weft/commands/status.py` or reverse ordering would miss the
  actual failure window.

What evidence proves it:

- GitHub `Test` run `24118464057`, job `70367306460`, on commit `90fa5cc`
  fails `tests/cli/test_cli_list_task.py::test_list_and_task_status` after task
  submission with `TimeoutError: Timed out waiting for task ...`.
- GitHub `Test` run `24118464057`, job `70367306364`, on the same commit fails
  `tests/cli/test_cli_list_task.py::test_task_tid_reverse` in the same waiter
  path before the reverse-TID assertion runs.
- `tests/helpers/weft_harness.py` at `90fa5cc` only treats `work_completed`,
  `work_failed`, and `work_timeout` as terminal events, while `1554d30`
  expands that set to include `work_limit_violation`, `control_stop`,
  `control_kill`, `task_signal_stop`, and `task_signal_kill`.
- Focused local repros on current `HEAD` (`8b38a3f`) pass, and the relevant
  post-`90fa5cc` fixes are concentrated in `weft/core/manager.py` and
  `tests/helpers/weft_harness.py`, not in `weft/commands/status.py`.
- The audit found no stable local reproduction on this machine for the
  `90fa5cc` list/task family under Python `3.13.12`, Python `3.12.13`,
  CI-shaped targeted runs, coverage-shaped targeted runs, or early-suite
  ordering.
- The failing CI logs still do not show which terminal event, if any, was
  emitted for the timed-out TIDs, so the harness waiter bug is a confirmed
  `90fa5cc` defect and a strong candidate contributor, not a fully proved sole
  root cause for those specific timeouts.

Which file or layer owns it:

- The narrowest supported owner is the manager/task-finalization boundary in
  `weft/core/manager.py`, with `tests/helpers/weft_harness.py` as the observing
  waiter layer.
- Separately, `tests/helpers/weft_harness.py` itself owns a confirmed
  `90fa5cc` waiter bug: incomplete terminal-event recognition in
  `wait_for_completion()`.
- The evidence does not support assigning ownership to
  `weft/commands/status.py`.

What the smallest correct next fix slice should be:

- Start from the post-`90fa5cc` manager drain, child-liveness, waiter-event,
  and cleanup fixes (`1554d30`, `b23f8c5`, `f9e47e5`) rather than changing
  status projection code.
- Any backport or regression plan should target terminal publication and
  manager child cleanup at the lifecycle boundary first.

### [P1] The audit cannot classify the `test_cli_list_task` family below the completion boundary because `90fa5cc` drops the required timeout evidence

What failed:

- The `90fa5cc` timeout path for `WeftTestHarness.wait_for_completion()` raises
  `TimeoutError` without recording the queue, mapping, outbox, or process state
  that the audit plan requires to distinguish waiter miss, terminal-publication
  miss, stale projection, or true live-runtime hang.

Why it matters:

- Without that snapshot, the audit can prove that the failure is earlier than
  `status.py`, but it cannot safely claim the exact subtype of completion bug.
- That is the main reason the list/task family remains bounded rather than
  fully root-caused from a single red local repro.

What evidence proves it:

- The failing CI logs for both list/task tests end at
  `tests/helpers/weft_harness.py` timeout output and do not include:
  - a `weft.log.tasks` tail
  - a registry tail
  - outbox presence or absence
  - a `tid_mappings` snapshot for the failing TID
  - live PID state
- Code inspection of `tests/helpers/weft_harness.py` at `90fa5cc` shows
  `wait_for_completion()` watching `weft.log.tasks`, falling back to an outbox
  peek, and then raising `TimeoutError` without dumping debug state.

Which file or layer owns it:

- `tests/helpers/weft_harness.py`, specifically the timeout behavior of
  `wait_for_completion()`.

What the smallest correct next fix slice should be:

- Add durable timeout-state capture to `wait_for_completion()` before reopening
  broader root-cause work on the list/task family.
- The minimum useful snapshot is: task-log tail, registry tail, outbox
  presence, latest `tid_mappings` entry, and live worker/task PID state for the
  failing TID.

## Commit Comparison

- `90fa5cc`:
  - GitHub failures are real in both audit families.
  - The interactive family is classifiable from current evidence.
  - The list/task family is bounded to the completion boundary but not fully
    subclassified because the timeout path lacks state capture.
  - `tests/helpers/weft_harness.py` also contains a confirmed waiter bug at
    this commit, but the audit does not prove that bug alone explains every
    timed-out CI task.
- `8b38a3f`:
  - Focused local repros for `test_list_and_task_status`,
    `test_task_tid_reverse`, and
    `test_interactive_python_repl_outputs` pass.
  - The relevant fix path between these commits is:
    `1554d30` -> `b23f8c5` -> `f9e47e5` -> `8b38a3f`.
  - Those fixes land in the interactive shutdown path, manager lifecycle code,
    harness cleanup/timeout behavior, and PTY helper configuration, which is
    consistent with the audited ownership above.

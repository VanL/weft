# Release-Gate Failure Audit Log

Date: 2026-04-08
Plan: [release-gate-failure-audit-plan.md](./release-gate-failure-audit-plan.md)

Use this file for:

- exact commands
- commit hashes
- environment notes
- timestamps
- raw observations
- disproved theories

Do not write fix ideas here unless they are directly tied to a proven root
cause.

## Commit And Environment Matrix

- Audit timestamp: `2026-04-08T15:00:31Z`
- Local audit host:
  - `Darwin 25.3.0 arm64`
  - `macOS 26.3.1 (25D771280a)`
- Current local repo head:
  - `8b38a3fc915ecc73894e9f28d459e5d4f15c52e7`
  - short: `8b38a3f`
- Audited worktree head:
  - `90fa5cc5b0b5f2f800c5cc21e43d6810259b9750`
  - short: `90fa5cc`
- Local Python / package matrix:
  - repo default shell Python: `3.12.13`
  - `uv`: `0.11.4`
  - audited worktree default `uv run`: Python `3.13.12`, `simplebroker 3.1.4`
  - audited worktree explicit `uv run --python 3.12`: Python `3.12.13`
  - current local `HEAD` default `uv run`: Python `3.13.12`, `simplebroker 3.1.5`

## Worktree Setup

Command:

```bash
git worktree add /Users/van/Developer/weft-audit-90fa5cc 90fa5cc
cd /Users/van/Developer/weft-audit-90fa5cc
uv sync --extra dev --extra docker --extra macos-sandbox
```

Observed result:

- The audit worktree already existed at `/Users/van/Developer/weft-audit-90fa5cc`.
- `uv sync` completed successfully.
- The audited environment resolved to `simplebroker 3.1.4`, matching the
  `90fa5cc` dependency set rather than current local `HEAD`.

## Local Repros On `90fa5cc`

### Serial focused repros, default local `uv run` Python (`3.13.12`)

Commands:

```bash
uv run --extra dev pytest -q -n 0 tests/cli/test_cli_list_task.py::test_list_and_task_status
uv run --extra dev pytest -q -n 0 tests/cli/test_cli_list_task.py::test_task_tid_reverse
uv run --extra dev pytest -q -n 0 tests/cli/test_cli_run_interactive_tty.py::test_interactive_python_repl_outputs
```

Observed result:

- `test_list_and_task_status`: passed
- `test_task_tid_reverse`: passed
- `test_interactive_python_repl_outputs`: passed

### CI-shape focused repro on `90fa5cc`

Command:

```bash
uv run --extra dev pytest -v --tb=short -m "not slow" \
  --override-ini="addopts=-ra -q --strict-markers" \
  tests/cli/test_cli_list_task.py tests/cli/test_cli_run_interactive_tty.py -n 0
```

Observed result:

- `3 passed in 5.41s`
- No local reproduction of either failure family.

### Coverage-shape repro on `90fa5cc`

Command:

```bash
uv run --extra dev pytest --cov=weft --cov-report=term-missing \
  -v --tb=short -m "not slow" \
  --override-ini="addopts=-ra -q --strict-markers" \
  tests/cli/test_cli_list_task.py -n 0
```

Observed result:

- `2 passed in 3.80s`
- The list/task family did not reproduce under local coverage shaping.

### Early-suite subset before `test_cli_list_task`

Command:

```bash
uv run --extra dev pytest -v --tb=short -m "not slow" \
  --override-ini="addopts=-ra -q --strict-markers" \
  tests/cli/test_cli.py \
  tests/cli/test_cli_init.py \
  tests/cli/test_cli_init_sqlite_only.py \
  tests/cli/test_cli_list_task.py -n 0
```

Observed result:

- `18 passed, 1 skipped in 5.57s`
- The failure did not reproduce from the early-suite ordering alone.

### Focused repros on `90fa5cc` under Python `3.12`

Commands:

```bash
uv run --python 3.12 --extra dev pytest -q -n 0 tests/cli/test_cli_list_task.py::test_list_and_task_status
uv run --python 3.12 --extra dev pytest -q -n 0 tests/cli/test_cli_list_task.py::test_task_tid_reverse
uv run --python 3.12 --extra dev pytest -q -n 0 tests/cli/test_cli_run_interactive_tty.py::test_interactive_python_repl_outputs
```

Observed result:

- all three focused repros passed

### Local repro conclusion on `90fa5cc`

- No stable local reproduction was found on this machine for either failure
  family.
- This remained true under:
  - isolated focused tests
  - CI-shape targeted runs
  - coverage-shaped targeted runs
  - a subset matching the tests immediately before `test_cli_list_task`
  - Python `3.13.12`
  - Python `3.12.13`

## GitHub Failed-Run Evidence On `90fa5cc`

### Interactive `:quit` failure, Linux test job

Command:

```bash
gh run view 24118464057 --job 70367306422 --log-failed
```

Observed result:

- commit under test: `90fa5cc5b0b5f2f800c5cc21e43d6810259b9750`
- platform: `ubuntu-24.04`, Python `3.12.13`
- failing test: `tests/cli/test_cli_run_interactive_tty.py::test_interactive_python_repl_outputs`
- failure message:
  - `interactive session did not exit after :quit`
- trailing PTY output showed the prompt re-rendered after `:quit` rather than a
  clean process exit.

Raw symptom excerpt:

```text
... Failed: interactive session did not exit after :quit; trailing='...weft> :quit...weft> :quit...'
```

### Interactive `:quit` failure, PG release-gate job

Command:

```bash
gh run view 24118465039 --job 70367308790 --log-failed
```

Observed result:

- commit under test: `90fa5cc5b0b5f2f800c5cc21e43d6810259b9750`
- platform: PG-backed Linux release-gate job, Python `3.13.12`
- same failing test:
  `tests/cli/test_cli_run_interactive_tty.py::test_interactive_python_repl_outputs`
- failure message:
  - `interactive session did not exit after :quit`
- trailing PTY output included a STOP acknowledgement payload, but the CLI
  process still did not exit.

Raw symptom excerpt:

```text
Failed: interactive session did not exit after :quit; trailing="...{'command': 'STOP', 'status': 'ack', 'tid': '1775624087128977408', ...}"
```

### List/task timeout failure, macOS test job

Command:

```bash
gh run view 24118464057 --job 70367306460 --log-failed
```

Observed result:

- commit under test: `90fa5cc5b0b5f2f800c5cc21e43d6810259b9750`
- platform: `macos-15-arm64`, Python `3.12.10`
- failing test: `tests/cli/test_cli_list_task.py::test_list_and_task_status`
- `_submit_task()` returned a TID and the failure occurred later in
  `WeftTestHarness.wait_for_completion()`
- waiter timeout message:
  - `TimeoutError: Timed out waiting for task 1775624081623314432`

### List/task timeout failure, Linux coverage job

Command:

```bash
gh run view 24118464057 --job 70367306364 --log-failed
```

Observed result:

- commit under test: `90fa5cc5b0b5f2f800c5cc21e43d6810259b9750`
- platform: `ubuntu-24.04`, Python `3.13.12`
- failing test: `tests/cli/test_cli_list_task.py::test_task_tid_reverse`
- the failure still occurred in `WeftTestHarness.wait_for_completion()` before
  the reverse-TID assertion ran
- waiter timeout message:
  - `TimeoutError: Timed out waiting for task 1775624083539648512`

### What the CI logs do and do not prove

Proven:

- The interactive `:quit` failure is real on `90fa5cc`.
- The list/task timeout family is real on `90fa5cc`.
- `test_task_tid_reverse` fails before any reverse-formatting assertion.

Not proven directly from the failing CI logs:

- whether the list/task timeout is a waiter miss, a terminal-publication miss,
  or a true live-runtime hang
- whether the interactive STOP ack in the PG job came from the interactive task
  itself or from manager-side teardown interacting with that task

Reason:

- the failing CI logs do not include a task-log tail, registry tail, outbox
  presence check, tid-mapping snapshot, or live PID dump for the failing TID

## Code Inspection On `90fa5cc`

### Prompt-mode `:quit` path

File inspected:

- `/Users/van/Developer/weft-audit-90fa5cc/weft/commands/run.py`

Observed behavior at `90fa5cc`:

- prompt-mode `:quit` in `_run_interactive_session()` only calls
  `client.close_input()` and then breaks
- there is no explicit STOP/KILL escalation path
- there is no state-based wait that distinguishes clean completion from a stuck
  child after EOF

This matches the CI symptom where the CLI stays alive after `:quit`.

### PTY test environment

File inspected:

- `/Users/van/Developer/weft-audit-90fa5cc/tests/cli/test_cli_run_interactive_tty.py`

Observed behavior at `90fa5cc`:

- `_spawn_with_pty()` sets `WEFT_MANAGER_LIFETIME_TIMEOUT=1.0`
- `_spawn_with_pty()` also sets `WEFT_MANAGER_REUSE_ENABLED=0`

This means the PTY test runs against a one-shot manager whose idle timeout is
far shorter than the default harness lifetime.

### Manager idle behavior on `90fa5cc`

File inspected:

- `/Users/van/Developer/weft-audit-90fa5cc/weft/core/manager.py`

Observed behavior at `90fa5cc`:

- `Manager.process_once()` treats active non-persistent child tasks as subject
  to idle shutdown
- when the idle threshold is reached and `_child_processes` is non-empty, the
  old code calls `_terminate_children()`

That is a direct match for the PTY helper’s `1.0s` manager lifetime and is a
plausible contributor to the PG trailing STOP-ack symptom.

### `wait_for_completion()` on `90fa5cc`

File inspected:

- `/Users/van/Developer/weft-audit-90fa5cc/tests/helpers/weft_harness.py`

Observed behavior at `90fa5cc`:

- `wait_for_completion()` watches `weft.log.tasks` for `work_completed`
- on timeout it falls back to a direct outbox `peek_one()`
- on failure it raises `TimeoutError` without emitting a registry/log/outbox
  debug snapshot

This explains why the CI timeout logs do not satisfy the plan’s full evidence
matrix for the list/task family.

## Commit Comparison Against Current Local `HEAD`

### Focused repros on current local `HEAD` (`8b38a3f`)

Commands:

```bash
uv run --extra dev pytest -q -n 0 tests/cli/test_cli_list_task.py::test_list_and_task_status
uv run --extra dev pytest -q -n 0 tests/cli/test_cli_list_task.py::test_task_tid_reverse
uv run --extra dev pytest -q -n 0 tests/cli/test_cli_run_interactive_tty.py::test_interactive_python_repl_outputs
uv run --extra dev pytest --cov=weft --cov-report=term-missing -v --tb=short -m "not slow" \
  --override-ini="addopts=-ra -q --strict-markers" tests/cli/test_cli_list_task.py -n 0
```

Observed result:

- all focused local repros passed on current `HEAD`

### Relevant post-`90fa5cc` fixes

- `1554d30` (`Fix manager idle child handling and interactive PTY sessions`)
  changed:
  - prompt-mode `:quit` from EOF-only shutdown to explicit close-input ->
    STOP -> KILL escalation in `weft/commands/run.py`
  - manager idle behavior so active child tasks are no longer treated as idle
    in `weft/core/manager.py`
  - interactive runs to request PTY transport on TTY stdin
- `b23f8c5` (`Fix preserve cleanup and manager drain races`) changed:
  - manager STOP handling to drain children instead of racing new child launch
  - control-first launch gating
  - child liveness cleanup logic
  - harness cleanup to distinguish worker and task cleanup paths
- `f9e47e5` (`Fix harness cleanup and lifecycle audit follow-ups`) further
  tightened:
  - manager role identity in tid mappings
  - harness cleanup around worker-vs-task TIDs and safe owner PIDs
- `8b38a3f` (`Fix release-gate test races`) changed:
  - PTY test helper manager lifetime from `1.0` to `5.0`
  - zombie-PID helper test wait behavior

## Disproved Or Bounded Theories

- Disproved for this machine:
  - `90fa5cc` does not fail locally as an isolated bug in the three focused
    tests, under either Python `3.13.12` or `3.12.13`
- Bounded by code comparison:
  - the list/task family is not best explained by `weft/commands/status.py` or
    reverse-TID formatting; the post-`90fa5cc` fix concentration is in
    manager/harness lifecycle code instead
- Bounded by dependency comparison:
  - the audited `90fa5cc` worktree used `simplebroker 3.1.4`, so the older
    `simplebroker 3.1.5` preserve-cleanup regression is not required to explain
    these CI failures

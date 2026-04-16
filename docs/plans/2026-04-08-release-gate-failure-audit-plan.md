# Release-Gate Failure Audit Plan

Status: proposed
Source specs: see Source Documents below
Superseded by: none

## Goal

Audit the still-live release-blocking test failures with a strict root-cause
discipline. The immediate target is not to fix code. The immediate target is to
produce a trustworthy findings document that explains, with evidence, why the
current failures happen and which code layer owns each failure.

This audit is specifically about the current release-gate failure family:

- interactive prompt sessions that do not exit cleanly after `:quit`
- ordinary host-runner CLI tasks that never become durably completed from the
  point of view of `list` / `task status` / harness waiters

Do not merge this audit with the earlier preserve-cleanup / SimpleBroker
corruption work unless fresh evidence proves the same root cause. That older
audit is useful historical context, but it is not the governing problem
statement for this plan.

## Source Documents

Source specs:

- [`docs/specifications/00-Overview_and_Architecture.md`](../specifications/00-Overview_and_Architecture.md)
- [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md) [CC-2], [CC-3], [CC-3.2], [CC-3.4]
- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md) [MA-1], [MA-3], [MA-4]
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md) [MF-2], [MF-3], [MF-5], [MF-7]
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md) [STATE.1], [STATE.2], [QUEUE.4], [QUEUE.6], [MANAGER.3], [MANAGER.7]
- [`docs/specifications/08-Testing_Strategy.md`](../specifications/08-Testing_Strategy.md)
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md) [CLI-1.1], [CLI-1.2], [CLI-1.3]

Current code and prior audit context:

- [`docs/plans/2026-04-08-task-lifecycle-stop-drain-audit-plan.md`](./2026-04-08-task-lifecycle-stop-drain-audit-plan.md)
- [`docs/plans/2026-04-08-task-lifecycle-stop-drain-findings.md`](./2026-04-08-task-lifecycle-stop-drain-findings.md)
- [`docs/plans/2026-04-08-task-lifecycle-stop-drain-audit-log.md`](./2026-04-08-task-lifecycle-stop-drain-audit-log.md)
- [`docs/plans/2026-04-06-release-helper-plan.md`](./2026-04-06-release-helper-plan.md)
- [`docs/plans/2026-04-06-release-helper-retag-plan.md`](./2026-04-06-release-helper-retag-plan.md)
- [`docs/lessons.md`](../lessons.md)

Repo guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)

Source spec note:

- There is no single spec section today that fully defines the user-visible
  semantics of prompt-mode `:quit` during interactive task execution. That is
  part of the problem. The audit must identify the de facto contract before any
  fix plan is written.

## Fixed Audit Outputs

Use fixed sibling files so the investigation does not sprawl across terminal
notes or stale thread memory:

- Audit log:
  [`docs/plans/2026-04-08-release-gate-failure-audit-log.md`](./2026-04-08-release-gate-failure-audit-log.md)
- Findings document:
  [`docs/plans/2026-04-08-release-gate-failure-findings.md`](./2026-04-08-release-gate-failure-findings.md)

Rules:

- Do not append new findings to the older `task-lifecycle-stop-drain-*` files.
- Use the audit log for commands, repros, timestamps, raw observations, and
  disproved theories.
- Use the findings doc for severity-ordered conclusions and recommended next
  fix slices.
- Record the exact commit for every repro. Do not mix evidence from different
  commits in one paragraph.

## Current Failure Inventory

This section is the current problem statement. The audit starts here.

### Commits and Run Boundaries

There are two evidence buckets right now:

1. GitHub failures on commit `90fa5cc`
   - tag: `v0.7.0`
   - visible in:
     - `Test` run `24118464057`
     - `Release Gate` run `24118465039`

2. Local unpublished commits after `90fa5cc`
   - current local `main` includes:
     - `f9e47e5`
     - `8b38a3f`
   - Do not assume those local commits fix or explain the GitHub failures until
     the same repro is exercised on the same code.

The first-pass audit must reproduce on `90fa5cc` or prove why local reproduction
must happen on a newer tree.

### Exact Failing Tests and Symptoms

#### A. Interactive prompt quit failure

Observed in:

- GitHub `Test` run `24118464057`
  - job `70367306422` (`ubuntu-latest, 3.12`)
- GitHub `Release Gate` run `24118465039`
  - job `70367308790` (`test-postgres`)

Failing test:

- [`tests/cli/test_cli_run_interactive_tty.py`](../../tests/cli/test_cli_run_interactive_tty.py)
  - `test_interactive_python_repl_outputs`

Observed symptom:

- the PTY session reaches the `weft>` prompt
- the child Python REPL prints `4`
- the test writes `:quit`
- the process does not exit before the timeout
- in the PG release-gate log, trailing PTY output includes a `STOP` ack:
  `{'command': 'STOP', 'status': 'ack', ...}`

Interpretation:

- the quit path is at least partially working, because control reaches the
  interactive task
- but either the CLI session process, the interactive task, or the stream
  client fails to complete the shutdown contract

#### B. Ordinary task completion timeout in list/task tests

Observed in:

- GitHub `Test` run `24118464057`
  - job `70367306460` (`macos-latest, 3.12`)
  - job `70367306364` (`coverage`)

Failing tests:

- [`tests/cli/test_cli_list_task.py`](../../tests/cli/test_cli_list_task.py)
  - `test_list_and_task_status`
  - `test_task_tid_reverse`

Observed symptom:

- `_submit_task()` succeeds and returns a TID
- [`WeftTestHarness.wait_for_completion()`](../../tests/helpers/weft_harness.py#L176)
  times out
- the test never reaches the list / task-status assertions

Interpretation:

- `test_task_tid_reverse` is not actually failing in reverse-TID formatting
  logic; it fails earlier waiting for the task to finish
- these two test failures are probably one lifecycle bug, not two unrelated test
  bugs

### What Is Not In Scope for This Audit Family

These may matter elsewhere, but they should not distract the first-pass audit:

- the earlier preserve-cleanup / SQLite corruption family, now tied to
  `simplebroker 3.1.5` and older sidecar-cleanup behavior
- Docker runner parity or SDK monitoring work
- macOS sandbox runner semantics
- the zombie helper test or `os._exit(0)` portability, except if fresh evidence
  proves current failures are caused by the same liveness helper

### CI runner-availability note

Record this once so nobody burns time on the wrong explanation:

- the regular `Test` matrix primarily installs `.[dev]`
- Docker runner coverage in normal CI relies on runtime self-skip for
  unavailable runners
- macOS sandbox coverage is meaningful only on macOS and also self-skips when
  unavailable
- the currently failing tests in this audit family are host-path CLI tests, not
  Docker-runner or macOS-sandbox-runner tests

## Context and Key Files

### Files to read first

- [`tests/cli/test_cli_list_task.py`](../../tests/cli/test_cli_list_task.py)
- [`tests/cli/test_cli_run_interactive_tty.py`](../../tests/cli/test_cli_run_interactive_tty.py)
- [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py)
- [`weft/commands/run.py`](../../weft/commands/run.py)
- [`weft/commands/status.py`](../../weft/commands/status.py)
- [`weft/commands/interactive.py`](../../weft/commands/interactive.py)
- [`weft/core/tasks/interactive.py`](../../weft/core/tasks/interactive.py)
- [`weft/core/tasks/consumer.py`](../../weft/core/tasks/consumer.py)
- [`weft/core/tasks/base.py`](../../weft/core/tasks/base.py)
- [`weft/core/manager.py`](../../weft/core/manager.py)
- [`bin/pytest-pg`](../../bin/pytest-pg)

### Files that may be touched during the audit

Normal audit outputs:

- `docs/plans/2026-04-08-release-gate-failure-audit-plan.md`
- `docs/plans/2026-04-08-release-gate-failure-audit-log.md`
- `docs/plans/2026-04-08-release-gate-failure-findings.md`

Temporary local-only instrumentation, if needed:

- `tests/cli/test_cli_list_task.py`
- `tests/cli/test_cli_run_interactive_tty.py`
- `tests/helpers/weft_harness.py`
- `weft/commands/run.py`
- `weft/commands/status.py`

Rules for temporary instrumentation:

- Prefer test-side logging first.
- Prefer existing helpers such as
  [`WeftTestHarness.dump_debug_state()`](../../tests/helpers/weft_harness.py#L115).
- Do not commit temporary instrumentation unless it becomes a durable regression
  test or a justified observability improvement.

### Current structure

- [`tests/cli/test_cli_list_task.py`](../../tests/cli/test_cli_list_task.py)
  submits a normal function task, then relies on
  [`WeftTestHarness.wait_for_completion()`](../../tests/helpers/weft_harness.py#L176)
  before asserting on `weft task list` / `weft task status`.
- [`WeftTestHarness.wait_for_completion()`](../../tests/helpers/weft_harness.py#L176)
  currently treats `weft.log.tasks` as the primary completion truth and only
  falls back to peeking `T{tid}.outbox`.
- Prompt-mode interactive execution is owned by
  [`_run_interactive_session()`](../../weft/commands/run.py#L731) in
  [`weft/commands/run.py`](../../weft/commands/run.py).
- Prompt-mode `:quit` currently routes through
  [`_request_interactive_exit()`](../../weft/commands/run.py#L780), which:
  - closes stdin
  - waits `1.0s`
  - sends `STOP`
  - waits `2.0s`
  - sends `KILL`
  - waits `2.0s`
- Interactive queue streaming and terminal-state observation are mediated by
  [`InteractiveStreamClient`](../../weft/commands/interactive.py).
- The interactive task’s control behavior and terminal event emission live in
  [`weft/core/tasks/interactive.py`](../../weft/core/tasks/interactive.py).
- `weft task list` / `weft task status` projections are built in
  [`weft/commands/status.py`](../../weft/commands/status.py), primarily from
  task-log history plus the latest `tid_mappings` payloads.

### Comprehension questions

The implementer is not ready to audit until they can answer these correctly:

1. Which queue/log is the durable completion truth for a task today?
2. Which function currently owns prompt-mode `:quit` shutdown?
3. Does `test_task_tid_reverse` fail in reverse formatting logic, or does it
   fail earlier?
4. Which module shapes `weft task list` / `weft task status` output?

## Invariants and Constraints

The audit must preserve these invariants:

- TID format and immutability remain unchanged.
- Task state transitions remain forward-only.
- `weft.log.tasks` remains the durable public truth for terminal task state.
- `weft.state.tid_mappings` remains runtime metadata, not durable completion
  truth.
- Reserved-queue policy is not to be changed as part of this audit.
- `spec` and `io` remain immutable after resolved `TaskSpec` creation.
- No new runner abstraction or CLI surface should be introduced while
  investigating these failures.

Review gates:

- no broad refactor
- no new dependency
- no new public CLI behavior during the audit
- no “fix” that is only a sleep increase without a root-cause explanation
- no mock-heavy substitute for the real broker / PTY / manager path when the
  real path is practical

## Working Assumptions

Unless disproved, assume:

1. The list/task timeout failures are real implementation bugs, not stale test
   expectations.
2. The interactive `:quit` failure is real implementation behavior, not just a
   noisy PTY test.
3. `WEFT_MANAGER_LIFETIME_TIMEOUT=1.0` in the PTY helper may amplify timing
   sensitivity, but it cannot explain the `test_cli_list_task` failures because
   those tests do not use the PTY helper.
4. `test_task_tid_reverse` shares its root cause with `test_list_and_task_status`
   until proven otherwise.
5. The current failures are host-path bugs. They are not primarily Docker or
   macOS-sandbox runner problems.
6. The older preserve-cleanup findings are historical context, not the default
   explanation for the current failures.

## Theories to Carry Into the Audit

This section exists to stop repeated dead ends.

### Strongly supported

1. `test_task_tid_reverse` is not a reverse-formatting bug.
   - It calls `_submit_task()` and then
     [`wait_for_completion()`](../../tests/helpers/weft_harness.py#L176)
     before the reverse-TID assertion.

2. The interactive `:quit` failure is at least partly below the test layer.
   - The CI logs show either repeated prompt output after `:quit` or an
     explicit `STOP` ack with no process exit.
   - That means the system is doing real shutdown work, but not finishing it.

3. Fixed sleeps are suspect.
   - They may be acceptable as bounded poll intervals.
   - They are not acceptable as the sole explanation for these failures.

### Open theories to evaluate

1. `wait_for_completion()` misses a valid completion signal.
   - Example outcome: `work_completed` exists but the waiter misses it due to
     history-read timing or fallback shape.

2. The task actually finishes work, but Weft fails to publish the terminal
   event durably.
   - Example outcome: outbox data exists, but `work_completed` never lands in
     `weft.log.tasks`.

3. The task truly remains live.
   - Example outcome: runtime handle or consumer is still alive at timeout.

4. `:quit` succeeds in sending `STOP`, but the prompt-mode CLI does not connect
   “stop acknowledged” to “session process exited successfully.”

5. Prompt-mode shutdown timing is too rigid.
   - `_request_interactive_exit()` uses a fixed `1 + 2 + 2` second sequence.
   - That may be too tight under CI load even if the underlying shutdown design
     is otherwise sound.

6. Prompt-mode exit status is wrong even when the user action succeeds.
   - `:quit` is a user-success path.
   - The CLI may be leaking internal stop/kill machinery into a user-visible
     exit code or hang.

7. `weft task list` / `weft task status` are reading stale projection state.
   - This is less likely than the waiter/finalization theories, but it must be
     tested explicitly.

### Disproved or superseded for this audit family

1. “This is the earlier preserve-cleanup / SimpleBroker corruption bug again.”
   - Not supported by the current failing test names or logs.

2. “This is a Docker or macOS sandbox parity issue.”
   - Current failures are in host-only CLI flows.

3. “This is just the zombie helper test.”
   - The current failing tests are unrelated to that helper.

4. “The only issue is the helper timeout value.”
   - The interactive helper timeout can influence PTY tests, but it cannot
     explain the host function-task completion failures in `test_cli_list_task`.

## Tasks

### 1. Normalize evidence by commit before debugging behavior

- Outcome: every repro and every finding is tagged to the exact commit under
  test.
- Files to touch:
  - `docs/plans/2026-04-08-release-gate-failure-audit-log.md`
- Read first:
  - this plan
  - `git log --oneline --decorate -8`
- Steps:
  - Create a clean worktree for the audited commit:

    ```bash
    cd /Users/van/Developer/weft
    git worktree add ../weft-audit-90fa5cc 90fa5cc
    cd ../weft-audit-90fa5cc
    uv sync --extra dev --extra docker --extra macos-sandbox
    ```

  - If a dedicated worktree is impossible, record the exact reason in the audit
    log and use a detached checkout instead.
  - Create a clean audit worktree for `90fa5cc` or otherwise reproduce on that
    exact commit first.
  - Record the exact local `HEAD` and the exact audited commit in the log.
  - Do not mix GitHub `90fa5cc` evidence with local `8b38a3f` evidence without
    labeling both.
- Stop if:
  - you find yourself drawing conclusions from a newer tree before reproducing
    the older failure.
- Done when:
  - the audit log starts with a commit matrix and environment summary.

### 2. Reproduce the list/task completion timeout on the audited commit

- Outcome: the `test_cli_list_task` failure is either reproduced locally or
  bounded tightly enough that the audit can proceed with CI logs plus local
  instrumentation.
- Files to touch:
  - `docs/plans/2026-04-08-release-gate-failure-audit-log.md`
  - optionally temporary local-only instrumentation in:
    - `tests/cli/test_cli_list_task.py`
    - `tests/helpers/weft_harness.py`
- Read first:
  - `tests/cli/test_cli_list_task.py`
  - `tests/helpers/weft_harness.py`
  - `weft/commands/status.py`
  - `weft/core/tasks/consumer.py`
  - `weft/core/tasks/base.py`
- Reuse:
  - `WeftTestHarness.dump_debug_state()`
  - `iter_queue_json_entries()`
- Required evidence if it fails:
  - task log tail
  - registry tail
  - outbox presence or absence
  - latest `tid_mappings` entry for the TID
  - whether any worker/consumer/runtime PID is still alive
- Do not:
  - increase the waiter timeout as the first move
  - rewrite the test to avoid waiting
- Done when:
  - the failure is classified as waiter bug, publication bug, projection bug,
    or real live-runtime hang

### 3. Reproduce the interactive `:quit` failure on the audited commit

- Outcome: the interactive quit failure is reproduced locally or narrowed to a
  concrete state mismatch.
- Files to touch:
  - `docs/plans/2026-04-08-release-gate-failure-audit-log.md`
  - optionally temporary local-only instrumentation in:
    - `tests/cli/test_cli_run_interactive_tty.py`
    - `weft/commands/run.py`
    - `weft/commands/interactive.py`
    - `weft/core/tasks/interactive.py`
- Read first:
  - `tests/cli/test_cli_run_interactive_tty.py`
  - `weft/commands/run.py`
  - `weft/commands/interactive.py`
  - `weft/core/tasks/interactive.py`
  - `weft/core/tasks/base.py`
- Required questions to answer:
  - Does `_request_interactive_exit()` return `False`, or does the prompt loop
    stall after it returns `True`?
  - After `:quit`, which of these arrive: `STOP` ack, `control_stop`,
    `work_completed`, session close, process exit?
  - If the process does not exit, which process is still alive?
- Do not:
  - “fix” the test by only increasing `EXIT_TIMEOUT`
  - rely on PTY output alone when queue/control state can be inspected
- Done when:
  - the interactive failure is classified as client wait bug, CLI prompt-loop
    bug, terminal-state publication bug, or real runtime hang

### 4. Compare `90fa5cc` against current local `HEAD`

- Outcome: determine whether local unpublished commits genuinely fix, mask, or
  merely avoid the audited failures.
- Files to touch:
  - `docs/plans/2026-04-08-release-gate-failure-audit-log.md`
  - `docs/plans/2026-04-08-release-gate-failure-findings.md`
- Read first:
  - `git log --oneline --decorate -8`
  - the log entries from Tasks 2 and 3
- Steps:
  - Run the same focused repros on current local `HEAD`.
  - Record whether the symptom disappears, changes shape, or remains identical.
  - If the symptom changes shape, note whether that indicates progress or a new
    masking layer.
- Stop if:
  - you start auditing only the newer tree without a clear statement of what
    changed between commits.
- Done when:
  - the findings document contains a commit-by-commit comparison.

### 5. Write a findings document before any fix plan

- Outcome: produce a severity-ordered findings doc that names:
  - the confirmed bug classes
  - the owning code layer
  - the disproved theories
  - the smallest correct next fix slice
- Files to touch:
  - `docs/plans/2026-04-08-release-gate-failure-findings.md`
- Read first:
  - the complete audit log
  - this plan
  - the current failing CI logs
- Constraints:
  - Findings must come before implementation.
  - If no stable repro exists for a suspected bug, say so explicitly.
- Done when:
  - another engineer could read the findings and write a narrow fix plan
    without reopening the whole investigation.

## Testing Plan

Run tests exactly like this unless the findings justify a different seam.

### Baseline setup

Repository root:

```bash
cd /Users/van/Developer/weft
```

Serial first, always:

```bash
uv run --extra dev pytest -q -n 0 tests/cli/test_cli_list_task.py::test_list_and_task_status
uv run --extra dev pytest -q -n 0 tests/cli/test_cli_list_task.py::test_task_tid_reverse
uv run --extra dev pytest -q -n 0 tests/cli/test_cli_run_interactive_tty.py::test_interactive_python_repl_outputs
```

### Stress loops for focused repro

```bash
for i in $(seq 1 50); do
  echo "ITER=$i"
  uv run --extra dev pytest -q -n 0 tests/cli/test_cli_list_task.py::test_list_and_task_status || break
done

for i in $(seq 1 50); do
  echo "ITER=$i"
  uv run --extra dev pytest -q -n 0 tests/cli/test_cli_run_interactive_tty.py::test_interactive_python_repl_outputs || break
done
```

### CI-shape local commands

Exact `Test`-job style command for the two relevant files:

```bash
uv run --extra dev pytest -v --tb=short -m "not slow" \
  --override-ini="addopts=-ra -q --strict-markers" \
  tests/cli/test_cli_list_task.py tests/cli/test_cli_run_interactive_tty.py -n 0
```

Coverage-shape repro for the list-task family:

```bash
uv run --extra dev pytest --cov=weft --cov-report=term-missing \
  -v --tb=short -m "not slow" \
  --override-ini="addopts=-ra -q --strict-markers" \
  tests/cli/test_cli_list_task.py -n 0
```

### Postgres-backed reproduction

Use this only after the SQLite/host repro is understood.

Full PG-compatible suite:

```bash
uv run --extra dev --extra docker --extra macos-sandbox bin/pytest-pg --all
```

If a focused PG repro is needed, start a Postgres container manually and run
the single test with the same env shape as `bin/pytest-pg`:

```bash
docker run --detach --rm \
  --name weft-pg-audit \
  --env POSTGRES_PASSWORD=postgres \
  --env POSTGRES_USER=postgres \
  --env POSTGRES_DB=simplebroker_test \
  --publish-all postgres:18 -c max_connections=300

PORT=$(docker port weft-pg-audit 5432/tcp | awk -F: '{print $2}')
export BROKER_TEST_BACKEND=postgres
export SIMPLEBROKER_PG_TEST_DSN="postgresql://postgres:postgres@127.0.0.1:${PORT}/simplebroker_test"

uv run --extra dev --extra docker --extra macos-sandbox \
  pytest -q -n 0 tests/cli/test_cli_run_interactive_tty.py::test_interactive_python_repl_outputs

docker rm -f weft-pg-audit
```

### GitHub log inspection

Use the exact failed runs first:

```bash
gh run view 24118464057 --job 70367306422 --log-failed
gh run view 24118464057 --job 70367306460 --log-failed
gh run view 24118464057 --job 70367306364 --log-failed
gh run view 24118465039 --job 70367308790 --log-failed
```

What each one proves:

- `70367306422`: Linux interactive prompt failure
- `70367306460`: macOS list/task timeout failure
- `70367306364`: coverage-job list/task timeout failure
- `70367308790`: PG release-gate interactive prompt failure

## Evidence Classification Matrix

### For list/task failures

If timeout happens, classify it like this:

- `work_completed` exists in `weft.log.tasks`
  - waiter or projection bug
- outbox exists, but no `work_completed`
  - finalization/publication bug
- no outbox, no terminal event, runtime dead
  - lost terminal publication / ownership bug
- no outbox, no terminal event, runtime alive
  - real runtime or consumer hang

### For interactive `:quit` failures

Classify based on the sequence after `:quit`:

- no control ack at all
  - control-send bug
- STOP ack arrives, but no terminal state and no process exit
  - session shutdown / client wait / prompt-loop bug
- terminal state lands, but CLI process stays alive
  - prompt-loop exit bug
- task exits, but CLI returns nonzero for user `:quit`
  - exit-status contract bug

## Stop-and-Re-Evaluate Gates

Stop and write a mini note in the audit log if any of these happen:

- the repro only exists on one commit and disappears on all others
- the bug starts looking like a generic manager-drain regression rather than a
  list/task or interactive-specific bug
- you are about to increase a timeout without proving the underlying contract
- you are about to add sleeps rather than state-based waiting
- you are about to rewrite the harness instead of first classifying the failing
  state

## Review Loop

Before any implementation plan is written from this audit:

1. Re-read the findings doc and ask: could a zero-context engineer implement a
   fix confidently from this?
2. If not, add the missing evidence or remove the unsupported theory.
3. Do not proceed to code from an ambiguous findings doc.

## Success Criteria

This audit is complete only when:

- the two live failure families are separated clearly
- each family has a commit-specific repro record
- each family has a classification from the evidence matrix
- the findings doc names the owning layer
- the next fix can be small, specific, and test-first

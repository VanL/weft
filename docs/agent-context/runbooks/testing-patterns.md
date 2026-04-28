# Testing Patterns and Debugging Test Failures

This runbook documents the preferred testing patterns for Weft and a few common
failure modes when working with real broker-backed tests.

## Quick Reference

All commands below assume you have first loaded the repo environment with
`direnv allow` or `. ./.envrc`, then run `uv sync --all-extras`, and that you
invoke the tools from the in-repo virtualenv. Do not assume `pytest`, `mypy`,
or `ruff` are installed globally.

### Test Command Cheat Sheet

```bash
# Fast/default suite
./.venv/bin/python -m pytest

# Include slow tests
./.venv/bin/python -m pytest -m ""

# Targeted directories
./.venv/bin/python -m pytest tests/cli/ -v
./.venv/bin/python -m pytest tests/core/ -v
./.venv/bin/python -m pytest tests/specs/manager_architecture/ -v

# Single file or test
./.venv/bin/python -m pytest tests/commands/test_run.py -q
./.venv/bin/python -m pytest tests/core/test_manager.py -q
./.venv/bin/python -m pytest tests/core/test_manager.py -k manager_reuse -q

# Static checks
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft
```

## Coverage Policy

Patch coverage is the main signal for new code. Keep it higher than the
legacy project-wide baseline so changed lines must carry meaningful tests.
Project coverage should only be raised after defensive exception arms and
backend-specific paths are classified; otherwise the gate measures noise.

Broad `except Exception` arms should be rare and explicit. If one remains
because it protects a process boundary, CLI boundary, plugin hook, or
best-effort cleanup path, mark it with `# pragma: no cover - <reason>`.
If no precise reason fits, narrow the catch or add a regression test instead.

## Harness Selection

Use the narrowest real harness that still proves the behavior:

| Scenario | Preferred fixture / approach | Why |
|---------|------------------------------|-----|
| CLI behavior | `WeftTestHarness` + `run_cli()` | Real subprocess invocation, isolated context, cleanup |
| Queue semantics | `broker_env` + real `Queue` objects | Exercises actual SQLite-backed broker behavior |
| Consumer/task execution | `task_factory` or direct `Consumer` construction | Covers real task wiring without inventing test doubles |
| Pure helpers or validation | Plain unit tests | Fastest option when no broker/process behavior is involved |

## Test Design Rules

1. Test observable behavior.
   Assert on outbox messages, state-log events, reserved queue outcomes, exit
   codes, CLI output, or persisted queue contents.

2. Prefer production paths.
   If a feature is exercised through `weft run`, use `run_cli()` or the command
   handler path rather than recreating the behavior with ad hoc helpers.

3. Minimize mocking.
   Do not mock `simplebroker.Queue`, manager lifecycle, or TaskSpec state
   transitions in tests that are supposed to prove durable behavior.

4. Avoid sleep-based correctness.
   Use bounded polling, existing harness helpers, and explicit timeouts. Sleeps
   can still appear in polling loops, but they should not be the assertion.

5. Write the failing test first when practical.
   For non-trivial regressions, prove the bug exists before implementing the
   fix.

6. Use the existing wait/result helpers for asynchronous completion.
   Completion events and final outbox writes are not always simultaneous, so
   tests should avoid assuming a terminal log event means the result is already
   readable.

## Common Failure Patterns

### Pattern 1: Broker-Heavy Tests Flake Under xdist

**Symptoms**

- Test passes alone, flakes in parallel runs.
- Failures cluster around worker teardown, manager cleanup, or queue state.

**Why it happens**

Broker-heavy tests share real SQLite-backed resources and live subprocess
cleanup. This repo groups tests using `weft_harness`, `broker_env`, `workdir`,
or `task_factory` onto one xdist worker to avoid teardown races.

**What to do**

- Use the existing fixtures instead of rolling your own isolated broker setup.
- If a new broker-heavy fixture is introduced, ensure collection groups it the
  same way `tests/conftest.py` does for the existing fixtures.

### Pattern 2: Mocked Queues Hide the Real Bug

**Symptoms**

- Unit test passes, integration test fails.
- Reserved-queue behavior or outbox semantics differ from expectations.

**Why it happens**

Mock queues do not reproduce timestamp ordering, reservation semantics, or
cleanup behavior.

**What to do**

- Replace the mock with `broker_env` or `WeftTestHarness`.
- Assert on the actual queue contents or state log, not on a mocked call list.

### Pattern 3: CLI Tests Time Out Without Enough Evidence

**Symptoms**

- `run_cli()` times out and the failure report is too thin to debug.

**What to do**

- Pass the active `WeftTestHarness` into `run_cli()` so timeout diagnostics
  include queue tails and tracked TIDs/PIDs.
- Prefer targeted test runs first so failures are easier to isolate.

### Pattern 4: Contract Change Updates Only One Side

**Symptoms**

- Validation tests pass but CLI or integration tests fail.
- New fields appear in results or logs, but consumers still read the old shape.

**What to do**

- Search for all producers and consumers of the changed contract.
- Update tests at each boundary: schema/validation, command layer, and runtime
  behavior where applicable.

### Pattern 5: Fixed-Size Queue Reads Miss History

**Symptoms**

- A targeted test passes, but a larger suite or real queue history fails.
- Code that looks at `weft.log.tasks` or registry queues misses the most recent
  or relevant record once the queue grows.

**Why it happens**

Append-only queues do not stay small forever. `peek_many(limit=N)` is a lossy
snapshot if `N` is guessed incorrectly.

**What to do**

- Use `iter_queue_entries()` / `iter_queue_json_entries()` or
  `peek_generator()` for history-sensitive reads.
- When you need a current snapshot, reduce by latest timestamp per key such as
  `tid`.
- If you intentionally use a fixed limit in a test helper, document why the
  queue is known to be small.

### Pattern 6: Completion Event Arrives Before Final Outbox Readability

**Symptoms**

- Test observes `work_completed` but outbox is briefly empty.
- Flakes around `weft result` or direct outbox assertions.

**Why it happens**

The terminal event and the public result are very close in time but not
guaranteed to be visible in the same instant.

**What to do**

- Use `WeftTestHarness.wait_for_completion()` or the existing result helpers.
- If you must assert directly, use bounded polling rather than a single
  immediate outbox read.

### Pattern 7: Client Follow Tests Hang Instead Of Failing

**Symptoms**

- CI prints the last completed module and then stays quiet for tens of minutes.
- The stuck test drains `Task.follow()`, `Task.realtime_events()`, or
  `client.tasks.watch()` with `list(...)`.

**Why it happens**

Follow-style client APIs are intentionally long-lived by default so UI streams
can stay attached until task completion. If a platform-specific race misses the
terminal observation, an unbounded test drain has no failure boundary.

**What to do**

- Pass an explicit timeout or cancellation boundary in tests.
- Add a regression at the shared command layer if the iterator skipped a
  terminal event that was already observed during result materialization.
- Keep the timeout caller-local. Do not publish task timeout state just because
  a test or UI stream stopped waiting.

## Verification Pattern

For changes that touch queue semantics, manager behavior, or task lifecycle:

1. Run the smallest targeted test that proves the change.
2. Run the nearest neighboring suite.
3. Run type check and lint if Python files changed.
4. Call out anything you did not verify.

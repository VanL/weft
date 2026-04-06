# Lessons Learned

This file is the canonical ledger for durable lessons learned while working on
Weft. Add short entries when a correction exposes a repeated failure mode or a
runbook needs to become stricter.

## How To Add A Lesson

- Use a dated heading or bullet.
- State the failure mode.
- State the durable rule that should prevent it next time.
- Link the related spec, plan, or test when useful.

## Starter Lessons

- Prefer extending the existing `Manager -> Consumer -> TaskRunner` path over
  inventing a second execution path for task lifecycle behavior.
- For queue, process, and lifecycle changes, use real broker-backed tests with
  `WeftTestHarness` or `broker_env` before trusting a fix.
- Treat `weft.state.*` queues as runtime-only unless a spec explicitly changes
  that rule.

## 2026-04-03 Audit Seeds

- Use `build_context()` and `WeftContext` for project resolution and broker
  wiring. Do not re-implement `.weft` discovery or infer Weft artifact paths
  from broker DB naming.
- For append-only queues such as `weft.log.tasks`, `weft.state.tid_mappings`,
  and `weft.state.workers`, use generator-based history reads or the helper
  iterators. Fixed `peek_many(limit=N)` reads are not reliable for
  correctness-critical history scans.
- Keep the TaskSpec boundary clean: templates stay partial, resolved TaskSpecs
  go through shared resolution before construction, and resolved `tid`, `spec`,
  and `io` are not mutated afterward.
- Reuse task/watch queue helpers on live runtime paths. Inside task code,
  opening fresh queue handles instead of using `_queue()` risks bypassing shared
  connection and stop-event behavior.
- Completion and final result visibility are close but not strictly
  simultaneous. Tests and command flows should use the existing wait/result
  helpers instead of assuming an immediate outbox read after a terminal log
  event.

## 2026-04-04 PG Parity

- When a task process exits, close every cached queue handle, not just
  "owned" auxiliary queues. Under SQLite this can look harmless; under
  Postgres it leaks connection-pool threads and prevents the process from
  exiting cleanly. See [weft/core/tasks/base.py](/Users/van/Developer/weft/weft/core/tasks/base.py)
  and [tests/commands/test_task_commands.py](/Users/van/Developer/weft/tests/commands/test_task_commands.py).
- In tests that launch background managers or workers from a CLI subprocess,
  do not start that CLI with a short-lived inner `uv run` environment.
  Background children inherit `sys.executable`; if that interpreter comes from
  an ephemeral env, later task spawns can fail after the parent CLI exits. Use
  the active long-lived environment for those subprocesses.
- Any Weft command path that opens a `persistent=True` broker queue must close
  it explicitly before returning. SQLite often masks this omission; Postgres
  surfaces it as CLI processes that print the expected output and then hang on
  pool-worker shutdown.
- In PG-backed tests, only wrap CLI subprocesses in `uv run --active` when the
  command may launch descendants that outlive the parent CLI process, such as
  `weft run` or `weft worker start`. Using nested `uv` for one-shot commands
  like `init`, `queue`, `status`, or `validate-taskspec` adds material test
  runtime without buying correctness.

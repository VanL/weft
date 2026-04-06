# Weft Backend Neutrality Plan

## 1. Goal

Finish the transition from "Weft primarily assumes SQLite, with Postgres made to
work" to "Weft runtime behavior is backend-neutral by default, with a small,
explicit set of file-backed SQLite exceptions."

This is not a plan to remove SQLite defaults or pretend backend-specific
behavior does not exist. It is a plan to make the normal Weft runtime path work
through SimpleBroker's opaque broker target APIs, isolate the few legitimate
SQLite-only branches, and make the test suite honest about what should pass on
both SQLite and Postgres.

Definition of done:

- Normal Weft runtime paths do not branch on backend name.
- Backend-specific logic is isolated to tiny helpers with explicit justification.
- `weft init` works correctly for both default SQLite projects and env- or
  project-configured Postgres projects.
- Every test module is explicitly classified as `shared` or `sqlite_only`, or
  is a pure non-broker test that is honestly marked `shared`.
- The temporary allowlist in [`tests/conftest.py`](../../tests/conftest.py) is
  gone or reduced to a consciously justified, documented remainder.
- SQLite and Postgres verification gates both pass.

This plan assumes the implementing engineer is strong in Python, weak on Weft
context, prone to over-abstracting, and likely to over-mock tests unless
stopped. Write the smallest real fix. Reuse the existing runtime boundary. Test
through real queues and real subprocesses whenever broker/process behavior is
involved.

## 2. Source Documents

Read these before changing code:

1. [`AGENTS.md`](../../AGENTS.md)
2. [`docs/agent-context/README.md`](../agent-context/README.md)
3. [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
4. [`docs/agent-context/principles.md`](../agent-context/principles.md)
5. [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
6. [`docs/agent-context/runbooks/testing-patterns.md`](../agent-context/runbooks/testing-patterns.md)
7. [`docs/agent-context/runbooks/runtime-and-context-patterns.md`](../agent-context/runbooks/runtime-and-context-patterns.md)
8. [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
9. [`docs/specifications/04-SimpleBroker_Integration.md`](../specifications/04-SimpleBroker_Integration.md)
10. [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
11. [`docs/specifications/08-Testing_Strategy.md`](../specifications/08-Testing_Strategy.md)
12. [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
13. [`README.md`](../../README.md)
14. [`docs/TODO-simplebroker-backend-followups.md`](../TODO-simplebroker-backend-followups.md)
15. [`docs/plans/postgres-backend-audit-and-shared-test-surface-plan.md`](./postgres-backend-audit-and-shared-test-surface-plan.md)

Source spec relationship:

- Main source specs:
  [`docs/specifications/04-SimpleBroker_Integration.md`](../specifications/04-SimpleBroker_Integration.md)
  and
  [`docs/specifications/08-Testing_Strategy.md`](../specifications/08-Testing_Strategy.md)
- This plan narrows and completes the earlier Postgres/shared-surface plan. It
  does not replace the architectural direction established there.

## 3. Context and Key Files

### 3.1 Mental Model

Weft is "SimpleBroker for processes." The broker is the persistence and queue
layer. Weft should not care whether the underlying broker target is a SQLite
file or a Postgres schema in ordinary runtime paths.

The correct runtime boundary is:

- `WeftContext.broker_target`
- `WeftContext.queue(...)`
- `WeftContext.broker(...)`
- `simplebroker.Queue(...)`
- `simplebroker.open_broker(...)`

The wrong runtime boundary is:

- raw `Path("...broker.db")`
- `str(context.database_path)` in shared behavior
- direct `BrokerDB(...)` construction in shared behavior
- tests that pass only because they accidentally created SQLite roots

### 3.2 Current State Summary

The good news:

- Most runtime code already works through `broker_target`.
- The PG-compatible non-`sqlite_only` suite can be made green.
- Manager/task/result/queue/status flows are already close to backend-neutral.

The remaining problems:

- Some runtime entry points are still SQLite-first in UX or validation.
- Some tests still create or assert SQLite file paths even when the behavior
  under test is supposed to be backend-neutral.
- Some CLI tests bypass the backend-aware test harness entirely.
- The marker taxonomy is incomplete, so "PG passed" is not yet the same as
  "every relevant test truly exercised PG."

Known exceptions that are still acceptable after this plan completes:

- [`weft/_constants.py`](../../weft/_constants.py) may still define
  `.weft/broker.db` as the default SQLite fallback name.
- [`weft/commands/load.py`](../../weft/commands/load.py) may still keep a small
  SQLite snapshot rollback helper for file-backed imports.
- [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py) may
  still clean up file-backed SQLite artifacts in test teardown.

### 3.3 Files That Matter Most

Read these code paths before implementation:

Runtime and config:

1. [`weft/_constants.py`](../../weft/_constants.py)
2. [`weft/context.py`](../../weft/context.py)
3. [`weft/commands/init.py`](../../weft/commands/init.py)
4. [`weft/commands/load.py`](../../weft/commands/load.py)
5. [`weft/commands/run.py`](../../weft/commands/run.py)
6. [`weft/commands/result.py`](../../weft/commands/result.py)
7. [`weft/commands/queue.py`](../../weft/commands/queue.py)
8. [`weft/commands/worker.py`](../../weft/commands/worker.py)
9. [`weft/helpers.py`](../../weft/helpers.py)
10. [`weft/manager_process.py`](../../weft/manager_process.py)

Test infrastructure:

1. [`tests/conftest.py`](../../tests/conftest.py)
2. [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py)
3. [`tests/helpers/test_backend.py`](../../tests/helpers/test_backend.py)
4. [`bin/pytest-pg`](../../bin/pytest-pg)

High-signal mixed test files:

1. [`tests/cli/test_cli.py`](../../tests/cli/test_cli.py)
2. [`tests/cli/test_cli_init.py`](../../tests/cli/test_cli_init.py)
3. [`tests/context/test_context.py`](../../tests/context/test_context.py)
4. [`tests/commands/test_dump_load.py`](../../tests/commands/test_dump_load.py)
5. [`tests/tasks/test_tasks_simple.py`](../../tests/tasks/test_tasks_simple.py)

### 3.4 Shared Paths and Helpers to Reuse

Do not duplicate these paths. Reuse them.

- [`weft/context.py`](../../weft/context.py)
  - `build_context(...)`
  - `WeftContext.queue(...)`
  - `WeftContext.broker(...)`
  - `WeftContext.broker_target`
  - `WeftContext.is_file_backed`

- [`tests/helpers/test_backend.py`](../../tests/helpers/test_backend.py)
  - `prepare_project_root(...)`
  - `prepare_cli_root(...)`
  - `active_test_backend(...)`

- [`tests/conftest.py`](../../tests/conftest.py)
  - `run_cli(...)`
  - `weft_harness`
  - collection-time backend marker enforcement

- [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py)
  - real subprocess cleanup and queue inspection

- [`weft/helpers.py`](../../weft/helpers.py)
  - `iter_queue_entries(...)`
  - `iter_queue_json_entries(...)`
  - use these for append-only queue history reads; do not go back to guessed
    `peek_many(limit=N)` snapshots for registry/log history.

## 4. Invariants and Constraints

### 4.1 Core Runtime Invariants

- Preserve 19-digit TID format and immutability.
- Preserve forward-only task state transitions.
- Preserve reserved queue policy behavior.
- Preserve `spec` / `io` immutability after `TaskSpec` creation.
- Preserve spawn-based process behavior for broker-connected children.
- Preserve runtime-only `weft.state.*` queues staying out of dump/load.
- Preserve `.weft/` as the Weft artifact directory regardless of broker
  backend.

### 4.2 Backend Neutrality Rules

- Do not add a new Weft-local broker abstraction. SimpleBroker already is the
  abstraction.
- In ordinary runtime code, prefer capability checks over backend-name checks.
  For example:
  - good: `context.is_file_backed`
  - bad: `if context.backend_name == "sqlite": ...` unless the behavior really
    is SQLite-specific
- Keep exact backend-name checks only in tiny helpers whose purpose is
  explicitly backend-specific.
- Keep SQLite as the default local backend. Backend-neutral does not mean
  "remove SQLite defaults."

### 4.3 Engineering Guardrails

- DRY: reuse `build_context`, `prepare_project_root`, `run_cli`, and the real
  harness. Do not invent a second setup path.
- YAGNI: do not introduce strategy classes, backend registries, adapters, or
  generic "storage capability" objects inside Weft unless blocked by a proven
  requirement.
- Red-green TDD:
  - write or expose a failing real test first when fixing non-trivial behavior
  - then implement the smallest fix
  - then run the nearest suite
- No patch-heavy tests for core broker/process behavior.
- No fake queue dictionaries when real queues are available.
- PG test isolation must remain one schema per Weft project root, not one
  schema per xdist worker. A single test can create multiple Weft roots.

### 4.4 Stop Conditions

If implementation reveals a missing SimpleBroker capability that cannot be
handled cleanly in Weft without a hack, stop and make the smallest upstream
SimpleBroker change first. Do not tunnel around the missing capability with a
Weft-only compatibility layer.

Examples that justify a small upstream fix:

- target resolution cannot represent a legitimate backend-neutral project state
- initialization semantics differ in a way Weft cannot observe cleanly
- the broker API is missing a capability needed for a backend-neutral runtime
  path

Examples that do not justify an upstream detour:

- a Weft test still hardcodes `.weft/broker.db`
- a Weft helper bypasses `prepare_project_root(...)`
- a Weft CLI test is using `CliRunner` when it should use `run_cli(...)`

## 5. Tasks

### 1. Lock the backend-neutrality contract in docs before changing behavior

Files to modify:

- [`docs/specifications/04-SimpleBroker_Integration.md`](../specifications/04-SimpleBroker_Integration.md)
- [`docs/specifications/08-Testing_Strategy.md`](../specifications/08-Testing_Strategy.md)
- [`README.md`](../../README.md) if behavior text is stale

Read first:

- [`weft/context.py`](../../weft/context.py)
- [`weft/commands/load.py`](../../weft/commands/load.py)
- [`tests/conftest.py`](../../tests/conftest.py)
- [`docs/TODO-simplebroker-backend-followups.md`](../TODO-simplebroker-backend-followups.md)

Approach:

- Add a short explicit statement to the specs:
  - ordinary runtime code is backend-neutral and uses `broker_target`
  - SQLite/file-backed behavior is an explicit exception, not the default shape
- Document the currently accepted exceptions:
  - SQLite default broker file
  - SQLite import snapshot rollback
  - file-backed cleanup in the test harness
- Do not write a broad architecture rewrite. This is a clarification pass.

Tests:

- No dedicated tests for doc-only edits.
- Verification is that the later implementation tasks match the documented
  contract exactly.

### 2. Make `weft init` target-resolution-first instead of SQLite-first

Files to modify:

- [`weft/commands/init.py`](../../weft/commands/init.py)
- [`tests/cli/test_cli_init.py`](../../tests/cli/test_cli_init.py)
- [`tests/cli/test_cli_init_sqlite_only.py`](../../tests/cli/test_cli_init_sqlite_only.py)
- possibly [`tests/cli/test_cli.py`](../../tests/cli/test_cli.py) if it still
  contains `init` coverage that belongs elsewhere

Read first:

- [`weft/context.py`](../../weft/context.py)
- [`weft/_constants.py`](../../weft/_constants.py)
- [`docs/specifications/04-SimpleBroker_Integration.md`](../specifications/04-SimpleBroker_Integration.md)

Shared path — do not duplicate:

- `target_for_directory(...)` from SimpleBroker
- `build_context(...)` for Weft artifact creation

Approach:

- Remove or narrow the preflight in `cmd_init()` that assumes
  `BROKER_DEFAULT_DB_NAME` must be set unless `.simplebroker.toml` exists.
- Let target resolution and SimpleBroker initialization determine whether the
  configured backend is valid.
- Preserve the friendly error path, but make the message describe target
  resolution or initialization failure generically, not "missing SQLite DB
  name," unless the failure really is that case.
- Keep `build_context(... create_database=False)` after successful SimpleBroker
  init so `.weft/outputs`, `.weft/logs`, metadata, and autostart directories
  still appear.

Required tests:

- Shared:
  - `weft init` on a fresh project root creates `.weft/`, outputs, logs, and a
    usable queue via the active backend.
  - Under PG with project configuration, `weft init` succeeds and a real queue
    roundtrip succeeds afterward.
  - Under PG with env-only configuration, `weft init` succeeds and a real queue
    roundtrip succeeds afterward.
- SQLite-only:
  - keep a negative test only if there is still a SQLite-specific invalid
    default-DB configuration case after the refactor.
  - if the current negative test becomes obsolete, delete it rather than
    preserving a fake constraint.

How to test:

- Use `run_cli(...)` with `weft_harness`; do not use `CliRunner` for backendful
  init tests.
- For env-only PG tests, use a unique schema per project root. Do not reuse one
  hard-coded schema across tests.
- Under PG, run the real test under `bin/pytest-pg` or the real PG env; do not
  fake the backend with mocks.

### 3. Isolate the legitimate file-backed exceptions and keep them small

Files to modify:

- [`weft/commands/load.py`](../../weft/commands/load.py)
- [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py)
- possibly [`weft/helpers.py`](../../weft/helpers.py) if a tiny shared helper is
  genuinely needed

Read first:

- [`docs/TODO-simplebroker-backend-followups.md`](../TODO-simplebroker-backend-followups.md)
- [`weft/context.py`](../../weft/context.py)

Approach:

- Keep import snapshot rollback explicitly SQLite/file-backed.
- Prefer `context.is_file_backed` when the distinction is "has on-disk broker
  artifacts" rather than "is SQLite by name."
- Keep exact SQLite artifact handling only where WAL/SHM files are actually
  relevant.
- If runtime and harness still need the same small SQLite-artifact helper after
  the refactor, extract one pure helper into an existing shared module.
- If the shared helper would be used in only one place, do not extract it.

Required tests:

- Shared:
  - `load --dry-run` remains backend-neutral and does not branch on file-backed
    assumptions.
- SQLite-only:
  - failed import restores pre-import broker state for file-backed SQLite.
- PG:
  - failed import reports non-atomic/partial-import risk honestly and does not
    pretend snapshot rollback exists if it does not.

How to test:

- Use real dump/load tests against real broker targets.
- Do not monkeypatch queue I/O to simulate failure if the failure can be
  triggered by a real max-message-size or write error.

### 4. Complete the runtime grep audit and remove accidental backend knowledge

Files to audit and possibly modify:

- [`weft/context.py`](../../weft/context.py)
- [`weft/_constants.py`](../../weft/_constants.py)
- [`weft/commands/init.py`](../../weft/commands/init.py)
- [`weft/commands/load.py`](../../weft/commands/load.py)
- [`weft/commands/run.py`](../../weft/commands/run.py)
- [`weft/commands/result.py`](../../weft/commands/result.py)
- [`weft/commands/queue.py`](../../weft/commands/queue.py)
- [`weft/commands/status.py`](../../weft/commands/status.py)
- [`weft/commands/worker.py`](../../weft/commands/worker.py)
- [`weft/manager_process.py`](../../weft/manager_process.py)

Read first:

- [`weft/context.py`](../../weft/context.py)
- [`docs/specifications/04-SimpleBroker_Integration.md`](../specifications/04-SimpleBroker_Integration.md)

Approach:

- Run a grep like:

```bash
rg -n "sqlite|database_path|is_file_backed|backend_name|BrokerDB|\\.db\\b|-wal|-shm" weft -g '*.py'
```

- For each hit in `weft/`, choose exactly one action:
  - remove it because it is accidental SQLite coupling
  - replace it with `broker_target`
  - replace it with `context.is_file_backed`
  - keep it in a tiny SQLite-only helper with a docstring explaining why
- Do not leave ambiguous hits behind.
- Do not change configuration defaults like `.weft/broker.db` unless the spec
  requires it. A SQLite default is acceptable. Hidden SQLite coupling is not.

Required tests:

- Re-run the nearest targeted suite for each touched command/module.
- For queue history or worker registry code, verify reads still use generator-
  based helpers where queue history length matters.

### 5. Normalize test setup so shared tests actually exercise the active backend

Files to modify:

- [`tests/helpers/test_backend.py`](../../tests/helpers/test_backend.py)
- [`tests/conftest.py`](../../tests/conftest.py)
- [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py)
- any shared test file that calls `build_context(spec_context=...)` directly on
  a fresh temp directory

Read first:

- [`tests/conftest.py`](../../tests/conftest.py)
- [`tests/helpers/test_backend.py`](../../tests/helpers/test_backend.py)
- [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py)

Shared path — do not duplicate:

- `prepare_project_root(...)`
- `prepare_cli_root(...)`
- `run_cli(...)`

Approach:

- Any test that creates a fresh project root and expects backendful behavior
  must use one of these patterns:
  - `weft_harness`
  - `prepare_project_root(...)` before `build_context(...)`
  - `run_cli(...)`, which must prepare the CLI root consistently
- Bare `build_context(spec_context=tmp_path)` is acceptable only when the test
  is intentionally SQLite-specific. In shared tests, treat a fresh unprepared
  temp path as wrong by default.
- Fix any spec-driven CLI flows that create a SQLite root in the test process
  and then a PG root in the subprocess. That is a false test, not a runtime
  bug.
- Do not hand-roll Postgres schema naming in individual tests if
  `prepare_project_root(...)` already gives per-root isolation.

Required tests:

- Worker lifecycle CLI tests on PG and SQLite.
- Status command tests on PG and SQLite.
- Any test touched because it previously mixed root preparation between process
  boundaries.

How to test:

- Reproduce failures with the real PG env and `-x`.
- Prefer fixing the shared setup helper once over patching many individual
  tests, but do not invent a broad new fixture layer if the existing helpers
  already solve it.

### 6. Split mixed test files and move broker-using CLI tests off `CliRunner`

Files to modify:

- [`tests/cli/test_cli.py`](../../tests/cli/test_cli.py)
- [`tests/cli/test_cli_init.py`](../../tests/cli/test_cli_init.py)
- [`tests/context/test_context.py`](../../tests/context/test_context.py)
- [`tests/commands/test_dump_load.py`](../../tests/commands/test_dump_load.py)
- possibly new `*_sqlite_only.py` siblings where the split keeps the tests
  simpler and more honest

Read first:

- [`tests/cli/test_cli_init.py`](../../tests/cli/test_cli_init.py)
- [`tests/context/test_context_sqlite_only.py`](../../tests/context/test_context_sqlite_only.py)
- [`tests/commands/test_dump_load_sqlite_only.py`](../../tests/commands/test_dump_load_sqlite_only.py)

Approach:

- Split by behavior, not by file-count aesthetics.
- Keep pure CLI parser/help/version tests in `tests/cli/test_cli.py`.
- Move backendful `init` coverage into `tests/cli/test_cli_init.py` and its
  SQLite-only companion.
- Move backendful tests off both `CliRunner` and ad hoc `subprocess.run(...)`
  calls unless the subprocess path itself is the behavior under test and the
  environment is prepared explicitly.
- If a test asserts `.weft/broker.db` exists, `database_path.exists()`, uses
  `BrokerDB`, or cares about WAL/SHM files, it is not shared.
- If a test asserts queue roundtrip, task lifecycle, CLI output, state events,
  or manager behavior independent of backend storage format, it should be
  shared.

Required tests:

- Shared slices pass under SQLite and PG.
- SQLite-only slices pass under SQLite.
- No broker-using PG-relevant tests remain in `CliRunner`-only files.

### 7. Burn down the collection allowlist until classification is honest

Files to modify:

- [`tests/conftest.py`](../../tests/conftest.py)
- the currently allowlisted test modules

Read first:

- [`tests/conftest.py`](../../tests/conftest.py)
- [`docs/specifications/08-Testing_Strategy.md`](../specifications/08-Testing_Strategy.md)

Current temporary debt:

- `_UNAUDITED_MODULE_ALLOWLIST` contains module-level exceptions.
- `_UNAUDITED_PATH_PREFIXES` exempts entire directories from explicit
  classification.

Approach:

Perform this as a burn-down, not a giant rewrite:

1. Pick 3 to 5 modules.
2. Audit each one line-by-line.
3. Mark it `shared`, `sqlite_only`, or split it.
4. Remove it from the allowlist.
5. Run the smallest targeted PG and SQLite checks.
6. Repeat.

Classification rules:

- `shared`:
  - help/version/argument parsing
  - queue/task/manager behavior that should not care about backend storage
  - pure validation and data-model tests with no file-backed assumptions
- `sqlite_only`:
  - `.weft/broker.db` existence
  - raw file-path broker initialization
  - `BrokerDB(...)`
  - SQLite artifact sidecars
  - SQLite-specific rollback behavior

Suggested first allowlist modules to burn down:

- [`tests/cli/test_cli.py`](../../tests/cli/test_cli.py)
- [`tests/cli/test_cli_worker.py`](../../tests/cli/test_cli_worker.py)
- [`tests/commands/test_status.py`](../../tests/commands/test_status.py)
- [`tests/commands/test_worker_commands.py`](../../tests/commands/test_worker_commands.py)
- [`tests/specs/worker_architecture/test_spawn_retry.py`](../../tests/specs/worker_architecture/test_spawn_retry.py)

Then audit the remaining allowlist modules:

- [`tests/cli/test_cli_pipeline.py`](../../tests/cli/test_cli_pipeline.py)
- [`tests/cli/test_cli_result_all.py`](../../tests/cli/test_cli_result_all.py)
- [`tests/cli/test_cli_run.py`](../../tests/cli/test_cli_run.py)
- [`tests/cli/test_cli_run_interactive_tty.py`](../../tests/cli/test_cli_run_interactive_tty.py)
- [`tests/cli/test_cli_spec.py`](../../tests/cli/test_cli_spec.py)
- [`tests/cli/test_cli_tidy.py`](../../tests/cli/test_cli_tidy.py)
- [`tests/cli/test_cli_validate.py`](../../tests/cli/test_cli_validate.py)
- [`tests/cli/test_commands.py`](../../tests/cli/test_commands.py)
- [`tests/cli/test_manager_proctitle.py`](../../tests/cli/test_manager_proctitle.py)
- [`tests/cli/test_rearrange_args.py`](../../tests/cli/test_rearrange_args.py)
- [`tests/commands/test_interactive_client.py`](../../tests/commands/test_interactive_client.py)
- [`tests/commands/test_queue.py`](../../tests/commands/test_queue.py)
- [`tests/specs/quick_reference/test_queue_names.py`](../../tests/specs/quick_reference/test_queue_names.py)
- [`tests/specs/worker_architecture/test_manager_state_events.py`](../../tests/specs/worker_architecture/test_manager_state_events.py)
- [`tests/system/test_helpers.py`](../../tests/system/test_helpers.py)
- [`tests/system/test_release_script.py`](../../tests/system/test_release_script.py)
- plus the directory-prefix exemptions under:
  - `tests/core/`
  - `tests/tasks/`
  - `tests/taskspec/`
  - `tests/specs/message_flow/`
  - `tests/specs/resource_management/`
  - `tests/specs/taskspec/`

Do not remove the allowlist entry or path-prefix exemption for a module until
that module is explicitly marked or split.

### 8. Make the PG verification path explicit and durable

Files to modify:

- [`bin/pytest-pg`](../../bin/pytest-pg)
- [`README.md`](../../README.md)
- [`docs/specifications/08-Testing_Strategy.md`](../specifications/08-Testing_Strategy.md)

Read first:

- [`bin/pytest-pg`](../../bin/pytest-pg)
- [`tests/conftest.py`](../../tests/conftest.py)

Approach:

- Keep `bin/pytest-pg` as a dev-only helper. Do not register it as a public
  package script.
- The helper should support both:
  - release gate: audited fast subset
  - audit gate: full PG-compatible suite (`not sqlite_only`)
- If the helper does not yet support both modes, extend it in this task.
- Docs must describe the published dependency flow correctly. Do not tell the
  reader to rely on sibling editable checkouts if the published dependencies are
  now the intended path.

Required tests:

- `bin/pytest-pg --fast`
- `bin/pytest-pg --all` if supported by this branch

### 9. Close with docs, lessons, and zero-context readability

Files to modify:

- [`README.md`](../../README.md)
- [`docs/specifications/04-SimpleBroker_Integration.md`](../specifications/04-SimpleBroker_Integration.md)
- [`docs/specifications/08-Testing_Strategy.md`](../specifications/08-Testing_Strategy.md)
- [`docs/lessons.md`](../lessons.md) if a repeated correction surfaced

Approach:

- Update docs to match reality after code and tests settle.
- Record any durable gotcha discovered during implementation, especially:
  - PG root preparation mismatches across subprocess boundaries
  - tests that silently use SQLite because they bypass the harness
  - where exact backend-name checks are still acceptable

Do not write a long changelog narrative. Update the docs that future engineers
will actually read.

## 6. Testing Plan

### 6.1 General Rules

- Use `WeftTestHarness` for CLI, manager, worker, and lifecycle behavior.
- Use `run_cli(...)` for broker-using CLI tests.
- Use real `Queue` or `context.broker()` for queue/broker semantics.
- Avoid monkeypatching core broker behavior when a real failure can be produced.
- Prefer a failing real PG test first for backend-neutral regressions.

### 6.2 Required Test Shapes

Shared test shapes:

- queue roundtrip using `context.queue(...)`
- CLI `init`, `status`, `run`, `result`, `queue`, `worker` behavior through
  `run_cli(...)`
- worker registry and status reads via real runtime queues
- dump/load preview and normal import behavior that should be backend-neutral

SQLite-only test shapes:

- `.weft/broker.db` existence
- `database_path.exists()`
- `BrokerDB(...)`
- WAL/SHM artifact behavior
- SQLite snapshot rollback

### 6.3 Test Authoring Rules

- Do not write new shared tests that pass raw SQLite paths into `Consumer`,
  `Queue`, or command helpers.
- Do not write new shared tests that assert backend name.
- If a shared test needs a project root, prepare it via `weft_harness` or
  `prepare_project_root(...)`.
- If the test reads append-only history queues, use generator-based helpers.

## 7. Verification

Run these incrementally while implementing, not only at the end.

Targeted checks for changed areas:

```bash
uv run --extra dev pytest tests/cli/test_cli_init.py -q
uv run --extra dev pytest tests/context/test_context.py -q
uv run --extra dev pytest tests/commands/test_dump_load.py -q
uv run --extra dev pytest tests/cli/test_cli_worker.py tests/commands/test_worker_commands.py tests/commands/test_status.py -q
```

SQLite-only checks:

```bash
uv run --extra dev pytest tests/context/test_context_sqlite_only.py -q
uv run --extra dev pytest tests/cli/test_cli_init_sqlite_only.py -q
uv run --extra dev pytest tests/commands/test_dump_load_sqlite_only.py -q
uv run --extra dev pytest tests/tasks/test_tasks_simple.py -q
```

PG checks:

```bash
uv run ./bin/pytest-pg --fast
uv run ./bin/pytest-pg --all
```

Fallback PG command if the helper is being edited and temporarily unavailable:

```bash
BROKER_TEST_BACKEND=postgres \
SIMPLEBROKER_PG_TEST_DSN='postgresql://postgres:postgres@127.0.0.1:54329/simplebroker_test' \
uv run --extra dev --with-editable . --with 'simplebroker-pg[dev]' \
pytest tests -m 'not sqlite_only' -n auto --dist loadgroup -q
```

Full default backend check:

```bash
uv run --extra dev pytest tests -q
```

If the implementation touched slow tests or reclassified any slow modules, also
run:

```bash
uv run --extra dev pytest tests -m "" -q
```

Static checks:

```bash
uv run --extra dev mypy weft
uv run --extra dev ruff check weft tests
```

Success criteria:

- SQLite default suite passes.
- PG fast gate passes.
- PG full compatible suite passes.
- Only intentionally SQLite-specific tests are excluded from PG.
- No broker-using shared test relies on accidental SQLite fallback.
- The temporary marker allowlist is empty or intentionally minimal with written
  justification in the code and docs.

## 8. Out of Scope

These are not part of this plan:

- Adding new broker backends beyond SQLite and Postgres.
- Replacing SimpleBroker with a Weft-local storage abstraction.
- Making import apply fully atomic on non-file-backed backends.
- Removing SQLite as the default local backend.
- Refactoring unrelated task/manager architecture.
- Rewriting every old pure unit test just for stylistic consistency.

## 9. Final Review Checklist

Before calling the implementation done, verify these statements are literally
true:

- I can explain every remaining `sqlite`, `.db`, `-wal`, `-shm`,
  `database_path`, and `BrokerDB` reference in the tree.
- Every remaining reference is either:
  - a default SQLite config value
  - an explicit SQLite-only helper
  - an explicit SQLite-only test
  - optional file-backed metadata that does not control ordinary runtime flow
- `tests/cli/test_cli.py` no longer provides false confidence about PG-backed
  behavior.
- A shared test failing under PG now indicates a real product problem, not a
  setup mismatch.
- I did not add a second broker abstraction or a speculative extensibility
  layer.

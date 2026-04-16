# Postgres Backend Audit and Shared Test Surface Plan

Status: proposed
Source specs: see Source Documents below
Superseded by: none

## 1. Goal

Make Postgres a real first-class Weft backend by auditing and removing hidden
SQLite assumptions, fixing the broker-config boundary, and running as much of
the Weft test surface as possible against both SQLite and Postgres.

The intended end state is:

- Weft runtime code is backend-agnostic by default.
- Weft only cares whether a broker target is file-backed when that distinction
  is truly required.
- Exact backend-name checks are isolated to tiny, explicit SQLite-only helpers.
- A large, audited `shared` pytest surface runs on both SQLite and Postgres.
- Intentionally file-backed or SQLite-specific tests remain, but they are named
  and marked honestly instead of leaking into the shared suite by accident.

This plan assumes the implementing engineer is strong in Python but has almost
no Weft or SimpleBroker context and will overbuild if the plan leaves room for
it. Do not leave room for it.

## 2. Source Documents

Read these before implementing anything:

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
14. [`docs/plans/2026-04-06-simplebroker-backend-generalization-plan.md`](./2026-04-06-simplebroker-backend-generalization-plan.md)
15. [`docs/TODO-simplebroker-backend-followups.md`](../TODO-simplebroker-backend-followups.md)

Read these Weft runtime and test files next:

1. [`weft/_constants.py`](../../weft/_constants.py)
2. [`weft/context.py`](../../weft/context.py)
3. [`weft/commands/init.py`](../../weft/commands/init.py)
4. [`weft/commands/load.py`](../../weft/commands/load.py)
5. [`weft/commands/queue.py`](../../weft/commands/queue.py)
6. [`weft/commands/result.py`](../../weft/commands/result.py)
7. [`weft/commands/run.py`](../../weft/commands/run.py)
8. [`weft/core/tasks/base.py`](../../weft/core/tasks/base.py)
9. [`weft/core/tasks/multiqueue_watcher.py`](../../weft/core/tasks/multiqueue_watcher.py)
10. [`tests/conftest.py`](../../tests/conftest.py)
11. [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py)
12. [`tests/context/test_context.py`](../../tests/context/test_context.py)
13. [`tests/cli/test_cli.py`](../../tests/cli/test_cli.py)
14. [`tests/cli/test_cli_init.py`](../../tests/cli/test_cli_init.py)
15. [`tests/cli/test_cli_result.py`](../../tests/cli/test_cli_result.py)
16. [`tests/cli/test_cli_system.py`](../../tests/cli/test_cli_system.py)
17. [`tests/cli/test_status.py`](../../tests/cli/test_status.py)
18. [`tests/commands/test_dump_load.py`](../../tests/commands/test_dump_load.py)
19. [`tests/commands/test_result.py`](../../tests/commands/test_result.py)
20. [`tests/commands/test_run.py`](../../tests/commands/test_run.py)
21. [`tests/commands/test_task_commands.py`](../../tests/commands/test_task_commands.py)
22. [`tests/tasks/test_tasks_simple.py`](../../tests/tasks/test_tasks_simple.py)

Read these sibling SimpleBroker files after that. They are part of the plan:

1. [`../simplebroker/simplebroker/__init__.py`](../../../simplebroker/simplebroker/__init__.py)
2. [`../simplebroker/simplebroker/_constants.py`](../../../simplebroker/simplebroker/_constants.py)
3. [`../simplebroker/simplebroker/project.py`](../../../simplebroker/simplebroker/project.py)
4. [`../simplebroker/simplebroker/sbqueue.py`](../../../simplebroker/simplebroker/sbqueue.py)
5. [`../simplebroker/simplebroker/db.py`](../../../simplebroker/simplebroker/db.py)
6. [`../simplebroker/simplebroker/watcher.py`](../../../simplebroker/simplebroker/watcher.py)
7. [`../simplebroker/tests/conftest.py`](../../../simplebroker/tests/conftest.py)
8. [`../simplebroker/tests/helper_scripts/broker_factory.py`](../../../simplebroker/tests/helper_scripts/broker_factory.py)
9. [`../simplebroker/bin/pytest-pg`](../../../simplebroker/bin/pytest-pg)
10. [`../simplebroker/extensions/simplebroker_pg/README.md`](../../../simplebroker/extensions/simplebroker_pg/README.md)

## 3. Context and Key Files

### 3.1 Current State

Weft already moved most runtime code onto `BrokerTarget`, `Queue`, and
`open_broker`. The broad direction is correct.

The remaining problem is not "Weft cannot ever talk to Postgres." The problem
is that the runtime and tests still carry enough SQLite-shaped assumptions that
Postgres support is brittle and largely unaudited.

### 3.2 Confirmed Gaps Found During Audit

These are real current issues, not hypotheticals:

1. `weft._constants.load_config()` returns only a small partial set of
   `BROKER_*` keys.
   - Example observed locally:
     `BROKER_AUTO_VACUUM_INTERVAL` is absent entirely under a PG backend
     environment.
   - File: [`weft/_constants.py`](../../weft/_constants.py)

2. Forcing missing broker keys through `WEFT_*` aliases still produces
   stringly-typed broker config values.
   - Example observed locally:
     with `WEFT_AUTO_VACUUM_INTERVAL=100`, a PG queue write reached
     `BrokerCore` and failed on an `int >= str` comparison.
   - Files:
     [`weft/_constants.py`](../../weft/_constants.py),
     [`../simplebroker/simplebroker/db.py`](../../../simplebroker/simplebroker/db.py)

3. `tests/conftest.py` and `WeftTestHarness` are still SQLite-shaped.
   - `broker_env` returns a string DB path.
   - the harness sets file-backed DB env vars directly.
   - cleanup unlinks `.db`, `-wal`, and `-shm` files.
   - Files:
     [`tests/conftest.py`](../../tests/conftest.py),
     [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py)

4. A non-trivial number of tests still use `str(context.database_path)` or
   `BrokerDB(...)` even when the behavior under test is backend-neutral.
   - Those tests should be migrated to `context.queue(...)`,
     `context.broker_target`, or `context.broker()`.

5. Weft tests create multiple project contexts inside a single test.
   - Example pattern: `source`, `target`, `roundtrip_test`.
   - That means a Postgres test backend cannot safely use one schema per xdist
     worker. It must use one schema per Weft project root.

### 3.3 Files That Are the Real Runtime Boundary

These are the shared paths to reuse. Do not duplicate them:

- [`weft/context.py`](../../weft/context.py)
  - `build_context(...)`
  - `WeftContext.queue(...)`
  - `WeftContext.broker(...)`
  - `WeftContext.broker_target`
  - `WeftContext.is_file_backed`

- [`weft/core/tasks/base.py`](../../weft/core/tasks/base.py)
  - `_queue(...)`
  - shared queue reuse and stop-event wiring

- [`weft/core/tasks/multiqueue_watcher.py`](../../weft/core/tasks/multiqueue_watcher.py)
  - shared watcher + queue target plumbing

- [`../simplebroker/tests/helper_scripts/broker_factory.py`](../../../simplebroker/tests/helper_scripts/broker_factory.py)
  - reference pattern for backend-neutral queue/broker fixtures

- [`../simplebroker/bin/pytest-pg`](../../../simplebroker/bin/pytest-pg)
  - reference pattern for "bring up Docker Postgres, run audited subset"

### 3.4 Test Modules to Audit First

These are the highest-signal modules for the shared-vs-SQLite split.

Likely `shared` after light refactoring:

- [`tests/commands/test_run.py`](../../tests/commands/test_run.py)
- [`tests/commands/test_result.py`](../../tests/commands/test_result.py)
- [`tests/commands/test_task_commands.py`](../../tests/commands/test_task_commands.py)
- [`tests/cli/test_status.py`](../../tests/cli/test_status.py)
- [`tests/cli/test_cli_system.py`](../../tests/cli/test_cli_system.py)
- [`tests/cli/test_cli_result.py`](../../tests/cli/test_cli_result.py)
- [`tests/specs/manager_architecture/test_agent_spawn.py`](../../tests/specs/manager_architecture/test_agent_spawn.py)
- [`tests/specs/manager_architecture/test_tid_correlation.py`](../../tests/specs/manager_architecture/test_tid_correlation.py)

Mixed modules that should be split into `*_shared.py` and
`*_sqlite_only.py` rather than left ambiguous:

- [`tests/context/test_context.py`](../../tests/context/test_context.py)
- [`tests/cli/test_cli.py`](../../tests/cli/test_cli.py)
- [`tests/cli/test_cli_init.py`](../../tests/cli/test_cli_init.py)
- [`tests/commands/test_dump_load.py`](../../tests/commands/test_dump_load.py)

Likely SQLite-only or legacy for this slice:

- [`tests/tasks/test_tasks_simple.py`](../../tests/tasks/test_tasks_simple.py)
  - keep honest or delete later; do not spend time forcing its `BrokerDB`
    compatibility path into the shared suite.

## 4. Invariants and Constraints

These are mandatory.

### 4.1 Core Runtime Invariants

- Preserve TID format and immutability.
- Preserve forward-only task state transitions.
- Preserve reserved-queue policy semantics.
- Preserve `spec` and `io` immutability after resolved `TaskSpec` creation.
- Preserve spawn-based multiprocessing for broker-connected child processes.
- Preserve runtime-only `weft.state.*` queues staying out of dump/load.
- Preserve `.weft/` as the fixed Weft artifact directory.

### 4.2 Backend-Agnostic Design Locks

- Weft should not grow a new broker abstraction.
- Weft should not branch on backend name in ordinary runtime code.
- The normal runtime surface should work through:
  - `WeftContext.broker_target`
  - `WeftContext.queue(...)`
  - `WeftContext.broker(...)`
  - `Queue(...)`
  - `open_broker(...)`

- Prefer `context.is_file_backed` when the distinction is really "file-backed
  vs non-file-backed."
- Only use exact backend-name checks in tiny, explicit SQLite-only helpers.
  Current example: the sqlite snapshot rollback helper in
  [`weft/commands/load.py`](../../weft/commands/load.py).

### 4.3 Test Infrastructure Locks

- Do not mock `simplebroker.Queue`.
- Do not mock `open_broker`.
- Do not fake queue state with dicts when a real queue or broker is available.
- Broker-heavy lifecycle tests must continue to use real queues and real child
  processes.
- Postgres test isolation must be per Weft project root, not per xdist worker.
  This is not optional.
- Mixed test modules must be split or explicitly marked; do not let PG runs
  silently execute unaudited SQLite assumptions.

### 4.4 DRY / YAGNI

- Do not duplicate SimpleBroker config defaults inside Weft.
- Do not vendor a new config parser in Weft.
- Do not add "temporary" backend branches across many call sites.
- Do not add a fake in-memory broker for tests.
- Do not add a generalized transaction or rollback layer in Weft.
- Reuse the sibling `simplebroker` test patterns, but do not blindly copy their
  "one schema per worker" approach because it is wrong for Weft.

### 4.5 Stop Conditions

Stop and re-evaluate if implementation starts drifting into any of these:

- a new `WeftBroker` or similar abstraction
- backend-specific branches spread across commands/tasks
- raw SQL or direct `psycopg` usage from Weft test helpers
- a PG test design that shares one schema across multiple Weft project roots
- a requirement to preserve export message timestamps on import

If a missing SimpleBroker public API is the blocker, make the smallest upstream
change in `../simplebroker` and come back. Do not tunnel through private
internals "just for now."

## 5. Tasks

Use red-green-refactor for every task. Do not skip the failing test first when
the failure can be expressed cleanly.

### Task 0: Lock the Failing Baseline

Goal:
Capture the current broken PG behavior before changing runtime or test
infrastructure.

Read first:

- [`weft/_constants.py`](../../weft/_constants.py)
- [`weft/context.py`](../../weft/context.py)
- [`tests/system/test_constants.py`](../../tests/system/test_constants.py)
- [`../simplebroker/simplebroker/_constants.py`](../../../simplebroker/simplebroker/_constants.py)
- [`../simplebroker/simplebroker/project.py`](../../../simplebroker/simplebroker/project.py)
- [`../simplebroker/simplebroker/db.py`](../../../simplebroker/simplebroker/db.py)

Files to modify:

- [`tests/system/test_constants.py`](../../tests/system/test_constants.py)
- optionally a new focused context/config test file under `tests/context/`

Red tests first:

1. Add a test proving that under a PG backend selection environment,
   `weft._constants.load_config()` exposes a full broker config, not a tiny
   partial override dict.
2. Assert typed expectations for at least:
   - `BROKER_AUTO_VACUUM_INTERVAL`
   - `BROKER_AUTO_VACUUM`
   - `BROKER_MAX_MESSAGE_SIZE`
3. Add or note a focused smoke repro that fails today:

```bash
WEFT_BACKEND=postgres \
WEFT_BACKEND_TARGET='postgresql://postgres:postgres@127.0.0.1:54329/simplebroker_test' \
WEFT_BACKEND_SCHEMA='weft_smoke' \
uv run --with-editable ../simplebroker \
  --with-editable ../simplebroker/extensions/simplebroker_pg \
  python - <<'PY'
from weft.context import build_context

ctx = build_context(spec_context='.', create_database=False)
print(ctx.broker_config)
PY
```

Implementation notes:

- Do not fix the issue in the test by hardcoding the missing broker defaults in
  the test itself.
- The test should describe the intended contract, not the current bug.

Done when:

- The failing baseline is captured in tests or a reproducible smoke command.
- You can point to a single concrete config-contract bug instead of vague
  "Postgres seems broken" language.

### Task 1: Fix the Broker Config Contract Upstream in SimpleBroker

Goal:
Make the public SimpleBroker config boundary safe for Weft. A caller passing
partial env-style overrides must still get a complete, typed config.

Why this task is upstream:

- Weft should not duplicate SimpleBroker defaults or type parsing.
- Weft already relies on SimpleBroker for backend selection and broker access.
- The current failure is a contract bug at the config boundary.

Read first:

- [`../simplebroker/simplebroker/_constants.py`](../../../simplebroker/simplebroker/_constants.py)
- [`../simplebroker/simplebroker/__init__.py`](../../../simplebroker/simplebroker/__init__.py)
- [`../simplebroker/simplebroker/project.py`](../../../simplebroker/simplebroker/project.py)
- [`../simplebroker/simplebroker/sbqueue.py`](../../../simplebroker/simplebroker/sbqueue.py)
- [`../simplebroker/simplebroker/db.py`](../../../simplebroker/simplebroker/db.py)
- [`../simplebroker/simplebroker/watcher.py`](../../../simplebroker/simplebroker/watcher.py)
- [`../simplebroker/tests/conftest.py`](../../../simplebroker/tests/conftest.py)

Files to modify in `../simplebroker`:

- [`../simplebroker/simplebroker/_constants.py`](../../../simplebroker/simplebroker/_constants.py)
- [`../simplebroker/simplebroker/__init__.py`](../../../simplebroker/simplebroker/__init__.py)
- [`../simplebroker/simplebroker/project.py`](../../../simplebroker/simplebroker/project.py)
- [`../simplebroker/simplebroker/sbqueue.py`](../../../simplebroker/simplebroker/sbqueue.py)
- [`../simplebroker/simplebroker/db.py`](../../../simplebroker/simplebroker/db.py)
- [`../simplebroker/simplebroker/watcher.py`](../../../simplebroker/simplebroker/watcher.py)
- matching tests in `../simplebroker/tests/`

Red tests first in `../simplebroker`:

1. A config-normalization test proving that partial override dicts produce a
   full typed config.
2. A public-surface test proving that these entry points accept partial config
   safely:
   - `Queue(...)`
   - `open_broker(...)`
   - `target_for_directory(...)`
   - `resolve_broker_target(...)`
   - queue watchers if they accept `config=`

Implementation notes:

- Prefer promoting or extending the existing internal config loader instead of
  inventing a second config system.
- Supported fix shape:
  - expose one tiny public SimpleBroker helper that returns a fully normalized
    config from env plus optional overrides, or
  - promote the existing loader in a way that keeps one shared normalization
    path.
- Unsupported fix shape:
  - importing `simplebroker._constants` from Weft and treating that private
    module as the long-term contract.
- The helper must normalize override values using the same parsing rules as env
  values. A raw string `"100"` must become an integer if the config key is
  integer-typed.
- Do not add a new abstraction layer. This is a contract repair, not a system
  redesign.

Done when:

- SimpleBroker public entry points accept partial config safely.
- Weft can rely on SimpleBroker, not a copy of its defaults.

### Task 2: Adopt the Normalized Broker Config in Weft

Goal:
Make `weft._constants.load_config()` and `WeftContext.broker_config` a full,
typed broker config rather than a partial override dictionary.

Read first:

- [`weft/_constants.py`](../../weft/_constants.py)
- [`weft/context.py`](../../weft/context.py)
- [`tests/system/test_constants.py`](../../tests/system/test_constants.py)
- the upstream SimpleBroker changes from Task 1

Files to modify:

- [`weft/_constants.py`](../../weft/_constants.py)
- [`weft/context.py`](../../weft/context.py)
- [`pyproject.toml`](../../pyproject.toml)
- [`tests/system/test_constants.py`](../../tests/system/test_constants.py)

Red tests first:

1. Update `tests/system/test_constants.py` to assert the new contract:
   - Weft-specific keys are present.
   - translated `BROKER_*` keys are present.
   - broker config values that should be typed are actually typed.
2. Add a focused context test showing `build_context(..., create_database=False)`
   produces a `broker_config` that can be handed to `Queue`/`open_broker`
   without extra patching.

Implementation notes:

- `load_config()` should still expose Weft-specific keys.
- Raise the minimum `simplebroker` version in
  [`pyproject.toml`](../../pyproject.toml) to a release line that actually
  contains the backend-neutral target APIs and the upstream config-contract fix.
  Do not leave the floor at `>=2.5.1`.
- Do not keep exact-set assertions for broker config keys. That will make the
  tests brittle every time SimpleBroker adds a new setting.
- Assertions should verify the important contract, not freeze every current key.
- Keep `context.database_path: Path | None` as an optional file-backed detail.
- Do not change `.weft/` directory behavior here.

Done when:

- `load_config()` returns a complete typed broker config.
- `context.broker_config` is safe to pass everywhere Weft currently passes it.

### Task 3: Build a Weft Test Backend Helper Layer for SQLite and Postgres

Goal:
Make the shared test fixtures backend-agnostic without teaching production code
about Postgres.

Read first:

- [`tests/conftest.py`](../../tests/conftest.py)
- [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py)
- [`../simplebroker/tests/conftest.py`](../../../simplebroker/tests/conftest.py)
- [`../simplebroker/tests/helper_scripts/broker_factory.py`](../../../simplebroker/tests/helper_scripts/broker_factory.py)

Files to modify:

- [`tests/conftest.py`](../../tests/conftest.py)
- add one or more new helper modules under `tests/helpers/`

Recommended helper modules:

- `tests/helpers/test_backend.py`
- `tests/helpers/test_project_root.py`

These names are suggestions. Use similarly boring names if you pick different
ones.

Red tests first:

1. Add a test that prepares two distinct project roots in one test and proves
   they do not share broker state under PG.
2. Add a fixture test that proves a prepared root works on both backends with:
   - `build_context(spec_context=root)`
   - `ctx.queue(...).write/read()`

Implementation notes:

- Reuse `BROKER_TEST_BACKEND` and `SIMPLEBROKER_PG_TEST_DSN` as the test-only
  backend selection env vars. Do not invent a second naming scheme unless there
  is a concrete collision.
- For PG, create one schema per Weft project root.
  - Derive the schema from the absolute project-root path via a stable hash.
  - Write a `.broker.toml` into that project root.
  - Use the plugin's public target initialization / cleanup hooks.
- Do not copy SimpleBroker's "one schema per xdist worker" design directly.
  That design is wrong for Weft because one Weft test may need multiple project
  contexts simultaneously.
- The helper should return or prepare:
  - a `ResolvedTarget` / `BrokerTarget`
  - a project root
  - a queue factory when needed
- Prefer adding `broker_target` and `queue_factory` fixtures aligned with
  SimpleBroker's names.
- `broker_env` may temporarily wrap the new fixtures during migration, but it
  should not remain the source of truth once conversion is done.
- Add one helper that prepares an arbitrary project root for the active test
  backend. Tests that create `source/`, `target/`, or other extra roots must
  call that helper instead of assuming raw `tmp_path` is ready for PG.

Done when:

- Test helpers can provision a real backend-neutral root.
- Two roots in one PG test are isolated from each other.

### Task 4: Convert `WeftTestHarness` and `run_cli()` to Use the New Backend Helper Layer

Goal:
Make the harness and CLI helper genuinely backend-neutral.

Read first:

- [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py)
- [`tests/conftest.py`](../../tests/conftest.py)
- [`../simplebroker/tests/conftest.py`](../../../simplebroker/tests/conftest.py)

Files to modify:

- [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py)
- [`tests/conftest.py`](../../tests/conftest.py)

Red tests first:

1. A harness test that:
   - starts from the harness root,
   - submits a task,
   - waits for completion,
   - and cleans up without backend-specific exceptions.
2. A CLI helper test that:
   - creates `source` and `target` roots,
   - runs `weft init` / `weft system dump` / `weft system load`,
   - and proves the target root uses a different broker context than source
     under PG.

Implementation notes:

- `WeftTestHarness` should prepare its root through the new backend helper
  rather than only setting file-backed DB env vars.
- PG cleanup should drop schemas discovered under the harness root.
- SQLite cleanup should keep the current unlink behavior for DB files and WAL
  sidecars.
- `run_cli()` should auto-prepare the relevant project root for PG before
  spawning the subprocess.
  - Mirror the spirit of SimpleBroker's `_config_root_from_args(...)`.
  - But prepare per-root schema config, not one worker schema.
  - Resolve the root from:
    - `--context` / `--dir` style explicit root flags
    - `weft init PATH`
    - otherwise the subprocess `cwd`
  - This preparation logic belongs only in test helpers, never in Weft runtime
    code.
- Keep the broker-heavy xdist grouping. Do not make broker/process tests run
  freely in parallel.

Done when:

- The same harness helpers can drive SQLite and PG shared tests.
- Cleanup is honest for both file-backed and non-file-backed backends.

### Task 5: Add Explicit Test Classification and Fail Closed

Goal:
Introduce `shared` and `sqlite_only` markers and make backend coverage an
explicit audited choice.

Read first:

- [`pyproject.toml`](../../pyproject.toml)
- [`tests/conftest.py`](../../tests/conftest.py)
- [`../simplebroker/tests/conftest.py`](../../../simplebroker/tests/conftest.py)

Files to modify:

- [`pyproject.toml`](../../pyproject.toml)
- [`tests/conftest.py`](../../tests/conftest.py)

Red tests first:

1. Add a collection-time check with an explicit temporary allowlist for
   unaudited modules.
   - A module must be either:
     - explicitly marked `shared`,
     - explicitly marked `sqlite_only`, or
     - listed in the shrinking temporary allowlist.
   - By the end of Task 7, that allowlist must be empty.
2. Add one or two representative marked modules to prove the new policy works.

Implementation notes:

- Add markers:
  - `shared`
  - `sqlite_only`
- Prefer module-level markers.
- If a file mixes both behaviors, split it into dedicated files rather than
  stacking many per-test markers in one module.
- Do not rely on heuristics as the final source of truth.
  - Heuristics are fine for temporary bootstrap reports.
  - They are not fine as the long-term safety boundary.
- Keep `slow` as-is.
- Keep the enforcement rule simple enough that a reviewer can tell why a module
  was accepted or rejected just by reading `tests/conftest.py`.

Done when:

- PG runs can execute `-m 'shared and not slow'` with confidence.
- No unaudited module is silently included in the PG suite.

### Task 6: Convert the Obvious Shared Test Modules First

Goal:
Migrate the low-risk backend-neutral tests onto `context.queue(...)`,
`context.broker()`, and `context.broker_target`.

Read first:

- [`tests/commands/test_run.py`](../../tests/commands/test_run.py)
- [`tests/commands/test_result.py`](../../tests/commands/test_result.py)
- [`tests/commands/test_task_commands.py`](../../tests/commands/test_task_commands.py)
- [`tests/cli/test_status.py`](../../tests/cli/test_status.py)
- [`tests/cli/test_cli_system.py`](../../tests/cli/test_cli_system.py)
- [`tests/cli/test_cli_result.py`](../../tests/cli/test_cli_result.py)
- [`tests/specs/manager_architecture/test_agent_spawn.py`](../../tests/specs/manager_architecture/test_agent_spawn.py)
- [`tests/specs/manager_architecture/test_tid_correlation.py`](../../tests/specs/manager_architecture/test_tid_correlation.py)

Files to modify:

- the files above

Red tests first:

- Mark these modules `shared`.
- Run them on SQLite and then on PG.
- Fix each failure by replacing file-path assumptions, not by weakening the
  test.

Implementation notes:

- Replace `Queue(... db_path=str(ctx.database_path) ...)` with either:
  - `ctx.queue(name, persistent=...)`, or
  - `Queue(... db_path=ctx.broker_target, ...)`
- Replace `launch_task_process(..., str(ctx.database_path), ...)` with
  `ctx.broker_target`.
- Replace direct `BrokerDB(...)` polling in
  [`tests/cli/test_cli_result.py`](../../tests/cli/test_cli_result.py) with
  `context.broker().list_queues()`, `ctx.queue(...).peek_one()`, or another
  backend-neutral real-broker read.
- Reuse the new fixtures. Do not create ad hoc PG setup logic inside each test.

Done when:

- These modules pass as `shared` on both backends.
- None of them depend on `context.database_path` unless the behavior under test
  is truly file-backed.

### Task 7: Split Mixed Modules and Keep SQLite-Only Assertions Honest

Goal:
Separate backend-neutral behavior from file-backed behavior in the test suite.

Read first:

- [`tests/context/test_context.py`](../../tests/context/test_context.py)
- [`tests/cli/test_cli.py`](../../tests/cli/test_cli.py)
- [`tests/cli/test_cli_init.py`](../../tests/cli/test_cli_init.py)
- [`tests/commands/test_dump_load.py`](../../tests/commands/test_dump_load.py)
- [`weft/commands/init.py`](../../weft/commands/init.py)
- [`weft/commands/load.py`](../../weft/commands/load.py)

Files to modify:

- split or replace the files above with explicit `*_shared.py` /
  `*_sqlite_only.py` modules
- [`weft/commands/init.py`](../../weft/commands/init.py) if needed

Recommended split:

- `tests/context/test_context_shared.py`
- `tests/context/test_context_sqlite_only.py`
- `tests/cli/test_cli_init_shared.py`
- `tests/cli/test_cli_init_sqlite_only.py`
- `tests/commands/test_dump_load_shared.py`
- `tests/commands/test_dump_load_sqlite_only.py`

Use those names unless there is a strong reason not to.

Red tests first:

Shared behavior to keep:

- `build_context(...)` creates `.weft/` directories and config metadata.
- `build_context(...)` can discover a prepared project root.
- `ctx.queue(...)` works.
- `weft init` creates the Weft artifact directories and a usable broker target.
- dump/load round-trips state across two distinct project contexts.

SQLite-only behavior to isolate:

- actual `.weft/broker.db` file existence
- WAL / SHM sidecars
- sqlite snapshot rollback helper behavior
- tests that intentionally validate default DB filename handling for the
  SQLite backend

Implementation notes:

- This task is where you fix any remaining SQLite-shaped runtime assumptions
  exposed by the shared tests.
- In particular, audit [`weft/commands/init.py`](../../weft/commands/init.py).
  Its `BROKER_DEFAULT_DB_NAME` guard should only apply when SQLite/default-file
  selection is actually required.
- For dump/load, keep the sqlite snapshot rollback helper small and explicitly
  SQLite-only. Do not fake backend neutrality there.
- Do not leave backend-neutral tests in `sqlite_only` just because the fixture
  migration is inconvenient.

Done when:

- The shared tests assert real backend-neutral behavior.
- SQLite-only tests are narrow and honest.

### Task 8: Decide What to Do With `tests/tasks/test_tasks_simple.py`

Goal:
Prevent this legacy file from distorting the shared backend story.

Read first:

- [`tests/tasks/test_tasks_simple.py`](../../tests/tasks/test_tasks_simple.py)
- [`weft/core/tasks/base.py`](../../weft/core/tasks/base.py)
- [`weft/core/tasks/multiqueue_watcher.py`](../../weft/core/tasks/multiqueue_watcher.py)

Files to modify:

- [`tests/tasks/test_tasks_simple.py`](../../tests/tasks/test_tasks_simple.py)
- optionally replace it with a clearer file name if you split it

Implementation notes:

- Do not spend this slice trying to make every legacy constructor-path test
  shared.
- The important question is whether Weft intentionally supports:
  - `BrokerTarget`
  - string path
  - `Path`
  - legacy `BrokerDB`
- If `BrokerDB` support is not part of the intended public/runtime contract,
  keep its test SQLite-only for now or delete it in a small follow-up.
- If path / `Path` constructor support is still intentional, keep those tests
  SQLite-only and clearly named as file-backed construction tests.

Done when:

- This file no longer muddies the meaning of the shared suite.

### Task 9: Add a One-Command Postgres Runner for Weft

Goal:
Provide a reproducible local and CI-friendly way to run the audited shared
suite on Postgres.

Read first:

- [`../simplebroker/bin/pytest-pg`](../../../simplebroker/bin/pytest-pg)
- [`pyproject.toml`](../../pyproject.toml)
- [`tests/conftest.py`](../../tests/conftest.py)

Files to modify:

- add `bin/pytest-pg` or similarly named script in this repo
- [`pyproject.toml`](../../pyproject.toml) if marker/docs wiring is needed
- optional CI workflow files only after the script is stable

Recommended behavior:

- Start a temporary Docker Postgres container.
- Export `SIMPLEBROKER_PG_TEST_DSN`.
- Set `BROKER_TEST_BACKEND=postgres`.
- Run only the audited shared suite:

```bash
pytest tests -m 'shared and not slow' -n auto --dist loadgroup
```

- Install local editable packages from the sibling repos:
  - `.` (Weft)
  - `../simplebroker`
  - `../simplebroker/extensions/simplebroker_pg`

Implementation notes:

- Keep the script boring and close to `simplebroker/bin/pytest-pg`.
- The script is allowed to depend on Docker and `uv`.
- Do not duplicate a second pile of workflow shell inside CI. If CI is added in
  this slice, it should call the script.
- CI wiring is allowed only after the local PG runner is green.
- Prefer stacked work:
  1. upstream SimpleBroker contract fix
  2. Weft runtime + fixture adoption
  3. Weft PG runner / CI hook
  If you cannot land cross-repo changes atomically, keep the dependency bump
  and runner wiring on the Weft side until the upstream change exists.

Done when:

- A reviewer can run one command and get a PG-backed shared-suite result.

### Task 10: Final Documentation and Audit Cleanup

Goal:
Leave the repository with an honest story about backend-neutral behavior and
shared test coverage.

Read first:

- [`README.md`](../../README.md)
- [`docs/specifications/04-SimpleBroker_Integration.md`](../specifications/04-SimpleBroker_Integration.md)
- [`docs/specifications/08-Testing_Strategy.md`](../specifications/08-Testing_Strategy.md)
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)

Files to modify:

- targeted docs only; do not do a broad docs sweep

Required doc updates:

1. Add plan backlinks in the relevant specs.
2. Update README / spec wording anywhere it still implies:
   - `.weft/broker.db` is universal for every backend
   - Weft tests are SQLite-only by default
3. Document the meaning of:
   - `shared`
   - `sqlite_only`
   - the PG runner

Audit commands before claiming done:

```bash
rg -n "BrokerDB\\(|sqlite3|database_path\\.exists\\(|backend_name == \"sqlite\"|\\.weft/broker\\.db" weft tests docs
rg -n "db_path=str\\(ctx\\.database_path\\)|db_path=str\\(context\\.database_path\\)" tests
```

Expected audit result:

- runtime hits are either gone or obviously intentional SQLite/file-backed
  helpers
- shared tests are not using `database_path` just as a queue handle
- remaining `.weft/broker.db` mentions are explicitly file-backed docs/tests

Done when:

- The repository tells the truth about shared vs SQLite-only behavior.

## 6. Testing Plan

### 6.1 Test Philosophy for This Slice

- Prefer real queue / broker / CLI behavior.
- Prefer failing tests that expose assumptions over patch-heavy tests that
  bypass them.
- Use the smallest real test that proves the bug, then expand.
- Keep PG failures actionable. If a failure belongs in `simplebroker`, prove
  that with a small upstream test instead of papering over it in Weft.

### 6.2 Tests to Add or Update

At minimum, cover these layers:

1. Config contract
   - `tests/system/test_constants.py`
   - upstream SimpleBroker config normalization tests

2. Context / queue smoke
   - shared context test using a prepared backend root
   - PG isolation test proving two roots do not share state

3. Harness / CLI
   - `weft_harness` round-trip task execution
   - CLI `init`, `status`, `result`, `system dump/load`

4. Shared module migration
   - the module list in Task 6

5. SQLite-only honest coverage
   - file existence assertions
   - sqlite snapshot rollback tests

### 6.3 Harness Guidance

- Use `WeftTestHarness` for CLI, manager, and task lifecycle coverage.
- Use `broker_target` / `queue_factory` / prepared project roots for lower-level
  broker behavior.
- Keep using real child processes for task-stop/kill tests.
- Do not replace broker-heavy tests with mocks because PG setup feels
  inconvenient.

### 6.4 Manual Smoke Tests

Run these during implementation, especially after Task 2 and Task 9:

1. PG context smoke:

```bash
export DSN='postgresql://postgres:postgres@127.0.0.1:54329/simplebroker_test'
export SCHEMA='weft_smoke_manual'

WEFT_BACKEND=postgres \
WEFT_BACKEND_TARGET="$DSN" \
WEFT_BACKEND_SCHEMA="$SCHEMA" \
uv run --with-editable ../simplebroker \
  --with-editable ../simplebroker/extensions/simplebroker_pg \
  python - <<'PY'
from weft.context import build_context

ctx = build_context(spec_context='.')
q = ctx.queue('smoke.queue', persistent=True)
q.write('hello')
print(q.read())
q.close()
PY
```

2. Multi-context PG isolation smoke:

- create `source/` and `target/` roots
- prepare each root with a different schema
- write to `source`
- assert `target` does not see the queue before load
- dump from `source`, load into `target`, assert data appears only after load

## 7. Verification

Run verification commands sequentially. Do not overlap `uv run ...` commands in
parallel.

Focused iteration commands:

```bash
uv run --extra dev pytest tests/system/test_constants.py -q
uv run --extra dev pytest tests/context/ -q
uv run --extra dev pytest tests/commands/test_run.py -q
uv run --extra dev pytest tests/commands/test_result.py -q
uv run --extra dev pytest tests/commands/test_task_commands.py -q
uv run --extra dev pytest tests/cli/test_status.py -q
uv run --extra dev pytest tests/cli/test_cli_system.py -q
uv run --extra dev pytest tests/cli/test_cli_result.py -q
uv run --extra dev pytest tests/commands/test_dump_load_shared.py -q
uv run --extra dev pytest tests/commands/test_dump_load_sqlite_only.py -q
```

When the PG runner exists:

```bash
bin/pytest-pg --fast
```

If the PG runner does not exist yet, use the equivalent direct command:

```bash
uv run --extra dev \
  --with-editable . \
  --with-editable ../simplebroker \
  --with-editable ../simplebroker/extensions/simplebroker_pg \
  pytest tests -m 'shared and not slow' -n auto --dist loadgroup
```

Full gates before claiming done:

```bash
uv run --extra dev pytest
uv run --extra dev pytest -m ""
uv run --extra dev ruff check weft tests
uv run --extra dev mypy weft
bin/pytest-pg --fast
```

Success looks like:

- SQLite suite stays green.
- PG shared suite is green.
- Shared tests no longer depend on file-backed broker paths.
- Remaining SQLite-only tests are explicit and justified.

## 8. Out of Scope

Do not do any of the following in this slice:

- add a new Weft broker abstraction
- rewrite Weft around backend-specific plugins
- make every file-backed test shared
- preserve import timestamps on load
- benchmark PG performance
- redesign SimpleBroker's extension model
- rewrite every historical doc that mentions `.weft/broker.db`
- remove every `BrokerDB` compatibility path in one shot if that turns into a
  separate API discussion
- add broad CI/CD changes before the local PG runner is stable

## 9. Final Warning

If implementation pressure starts pushing toward "teach Weft about Postgres in
many places," you are going in the wrong direction.

The right direction is:

- fix the broker config contract once,
- provision PG test roots honestly,
- move shared tests onto backend-neutral helpers,
- isolate the true SQLite-only behavior,
- and let the PG shared suite tell you what is left.

If you cannot keep the work on that path, stop and reassess instead of
continuing into a materially different design.

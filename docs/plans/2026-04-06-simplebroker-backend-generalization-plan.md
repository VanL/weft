# SimpleBroker Backend Generalization Plan

Status: completed
Source specs: see Source Documents below
Superseded by: none

This document is the implementation plan for the next backend-neutral cleanup
slice in Weft.

The target reader is a strong Python engineer with almost no Weft context and
unreliable instincts around scope, abstractions, and test design. Assume they
will overbuild if the plan leaves room for it. This document does not leave
room for it.

The job is to remove or isolate hidden SQLite assumptions in Weft runtime code
so the current SimpleBroker Postgres backend can work through the same Weft
surface area without Weft inventing a second broker abstraction.

This plan is intentionally small and opinionated.

It is written as bite-sized tasks. Each task tells the implementer:

- what to read first,
- which files to touch,
- which tests to write first,
- which invariants must hold,
- and which verification gates must pass before moving on.

## 0. Scope Lock

Implement exactly these outcomes:

1. Weft runtime code uses SimpleBroker's backend-neutral public surface by
   default: `BrokerTarget`, `Queue`, `open_broker`, `resolve_broker_target`,
   `target_for_directory`.
2. `weft system tidy` works against the active broker backend selected in the
   context instead of hard-failing when there is no SQLite file path.
3. `weft system load --dry-run` works against any backend using backend-neutral
   broker APIs.
4. `weft system load` applies imports through backend-neutral APIs.
5. Alias conflicts in `weft system load` are detected before any writes occur.
6. File-backed safety behavior is isolated to a tiny explicitly named
   SQLite/file helper instead of leaking through the main import path.
7. `.weft/` remains a fixed Weft artifact directory, not something inferred
   from `BROKER_DEFAULT_DB_NAME`.
8. Remaining SQLite-specific code is either deleted or clearly isolated as
   intentional file-backed behavior.
9. Tests distinguish backend-neutral behavior from intentionally SQLite-only
   behavior.

Do not implement any of the following in this slice:

- a new SimpleBroker API,
- a new Weft broker abstraction,
- timestamp-preserving import replay,
- a new Postgres CI matrix,
- a fake in-memory broker for tests,
- a broad documentation rewrite of every SQLite mention in the repository,
- compatibility shims for private `BrokerDB` internals,
- speculative abstractions for future backends beyond the currently supported
  SimpleBroker public surface.

If you discover a missing SimpleBroker public API that is truly required for a
correct implementation, stop and document the exact gap. Do not tunnel through
private SimpleBroker internals "just for now."

## 1. Read This First

Read these files in this order before editing code:

1. [`AGENTS.md`](../../AGENTS.md)
2. [`docs/specifications/00-Overview_and_Architecture.md`](../specifications/00-Overview_and_Architecture.md)
3. [`docs/specifications/04-SimpleBroker_Integration.md`](../specifications/04-SimpleBroker_Integration.md)
4. [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
5. [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
6. [`docs/specifications/08-Testing_Strategy.md`](../specifications/08-Testing_Strategy.md)
7. [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
8. [`README.md`](../../README.md)
9. [`docs/TODO-simplebroker-backend-followups.md`](../TODO-simplebroker-backend-followups.md)
10. [`weft/context.py`](../../weft/context.py)
11. [`weft/commands/tidy.py`](../../weft/commands/tidy.py)
12. [`weft/commands/load.py`](../../weft/commands/load.py)
13. [`weft/commands/dump.py`](../../weft/commands/dump.py)
14. [`weft/core/tasks/multiqueue_watcher.py`](../../weft/core/tasks/multiqueue_watcher.py)
15. [`weft/core/tasks/base.py`](../../weft/core/tasks/base.py)
16. [`weft/helpers.py`](../../weft/helpers.py)
17. [`tests/context/test_context.py`](../../tests/context/test_context.py)
18. [`tests/cli/test_cli_tidy.py`](../../tests/cli/test_cli_tidy.py)
19. [`tests/commands/test_dump_load.py`](../../tests/commands/test_dump_load.py)
20. [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py)
21. [`tests/conftest.py`](../../tests/conftest.py)

Read these SimpleBroker files next so you understand the intended public
surface and current backend behavior:

1. [`../simplebroker/simplebroker/__init__.py`](../../../simplebroker/simplebroker/__init__.py)
2. [`../simplebroker/simplebroker/db.py`](../../../simplebroker/simplebroker/db.py)
3. [`../simplebroker/simplebroker/_backend_plugins.py`](../../../simplebroker/simplebroker/_backend_plugins.py)
4. [`../simplebroker/extensions/simplebroker_pg/README.md`](../../../simplebroker/extensions/simplebroker_pg/README.md)
5. [`../simplebroker/extensions/simplebroker_pg/simplebroker_pg/plugin.py`](../../../simplebroker/extensions/simplebroker_pg/simplebroker_pg/plugin.py)

What you are learning from those files:

- `open_broker(...)` is the backend-neutral connection entry point.
- `Queue(...)` already accepts a `BrokerTarget`.
- `broker.vacuum(compact=True)` is already backend-native.
- `BrokerDB` is legacy SQLite-shaped machinery and should not be the default
  tool in new Weft runtime code.
- the Postgres plugin already knows how to validate targets and compact its own
  storage; Weft should not re-implement that.

## 2. Engineering Rules

These rules are mandatory.

### 2.1 Style

- Use `from __future__ import annotations` in every new Python module.
- Keep imports at module top.
- Use `collections.abc` abstract types.
- Use `Path`, not `os.path`.
- Add docstrings with spec references when behavior is non-trivial.
- Keep modules and helpers single-purpose.
- Use boring names. Prefer `is_file_backed` over clever wording.

### 2.2 DRY and YAGNI

- Reuse `WeftContext.broker()`, `WeftContext.queue()`, and `BrokerTarget`.
- Do not create a new wrapper type around SimpleBroker.
- Do not duplicate import parsing logic in separate preview and apply paths.
- Do not introduce a general transaction/snapshot framework in Weft.
- Keep any SQLite-only helper tiny, explicit, and file-backed by name.

### 2.3 Test Design

- Use red-green-refactor for every task below.
- Do not mock `simplebroker.Queue`.
- Do not mock `open_broker`.
- Do not fake queue state with hand-built dicts when a real queue will do.
- Prefer real broker operations through `WeftTestHarness`.
- Prefer polling a real queue or CLI result over `sleep()`-driven tests.
- Use mocks only to force hard-to-reproduce failure edges after you have at
  least one real integration test proving the happy path.
- When a test is specifically about file-backed rollback behavior, use a real
  SQLite database and a real runtime failure, not a fake broker.

### 2.4 Verification Discipline

Run these sequentially, not in parallel:

```bash
uv run --extra dev pytest
uv run --extra dev pytest -m ""
uv run --extra dev ruff check weft tests
uv run --extra dev mypy weft
```

This repository uses `uv`, and overlapping `uv run ...` commands can recreate
the shared virtualenv mid-run and cause false failures.

Use focused commands while iterating, then run the full gates at the end.

## 3. Fixed Design Decisions

These decisions are not open during implementation.

### 3.1 `WeftContext` Is the Runtime Boundary

The context object is the source of truth for broker access.

New or modified runtime code should prefer:

- `context.broker_target`,
- `context.broker()`,
- `context.queue(...)`,
- `context.backend_name`,
- `context.is_file_backed`.

New runtime code should not default to:

- `BrokerDB(str(path))`,
- `sqlite3.connect(...)`,
- stringly-typed `"broker.db"` fallbacks,
- or `target_path.parent` heuristics for core behavior.

### 3.2 `.weft/` Is Fixed

`.weft/` is a Weft artifact directory, not a derived broker path.

That means:

- `.weft/config.json`,
- `.weft/outputs/`,
- `.weft/logs/`,
- and default dump/load files

always live under `root / ".weft"`.

`BROKER_DEFAULT_DB_NAME` controls the broker target only. It does not relocate
Weft’s own artifact directory.

### 3.3 `load` Does Not Replay Original Message Timestamps

The current export format includes message timestamps, but current import does
not preserve them. Keep that behavior for this slice.

The `timestamp` field in `weft_export.jsonl` remains useful for:

- validation,
- reporting,
- diagnostics,
- and future tooling.

It is not an instruction to inject exact timestamps on import.

Do not reach into private SimpleBroker SQL or timestamp internals to replay
original IDs.

### 3.4 `load` Is Preflight-Then-Apply

This is the desired import contract:

1. Parse and validate input.
2. Discover current broker state.
3. Detect fatal conflicts before any write.
4. If there are fatal conflicts, return exit code `3` and perform no writes.
5. If there are no fatal conflicts, apply aliases and messages.

For file-backed contexts only, keep a tiny snapshot backup/restore safety net.

For non-file-backed contexts, do not pretend import is atomic if it is not.
If an apply-phase failure occurs after writes have started, return an error that
explicitly says a partial import may have occurred.

### 3.5 SQLite-Specific Code Must Be Honest

After this slice, SQLite-specific behavior is allowed only when all of the
following are true:

- it is isolated,
- it is explicitly named as file-backed or sqlite-specific,
- the generic path does not depend on it,
- and the repository can still run without it on a non-file backend.

This is allowed:

- a tiny SQLite snapshot helper for `load` rollback.

This is not allowed:

- `cmd_tidy()` refusing to run without a database file,
- import preview depending on `BrokerDB`,
- runtime helpers defaulting to `"broker.db"`,
- hidden path inference baked into unrelated features.

## 4. Current Problem Sites

These are the exact places this plan is fixing.

1. [`weft/commands/tidy.py`](../../weft/commands/tidy.py) still checks
   `context.database_path` and refuses non-file backends.
2. [`weft/helpers.py`](../../weft/helpers.py) still contains raw SQLite
   maintenance helpers used by `tidy`.
3. [`weft/commands/load.py`](../../weft/commands/load.py) still uses
   `BrokerDB(str(database_path))`, file backups, and file restore as the main
   import path.
4. [`weft/context.py`](../../weft/context.py) still derives `.weft/` from
   `BROKER_DEFAULT_DB_NAME`.
5. [`weft/core/tasks/multiqueue_watcher.py`](../../weft/core/tasks/multiqueue_watcher.py)
   still has a hidden `"broker.db"` fallback and a `BrokerDB`-shaped input
   helper.
6. [`weft/core/tasks/base.py`](../../weft/core/tasks/base.py) still has a
   `target_path.parent` fallback for process-title context inference.
7. Multiple tests still encode SQLite file assumptions where they should either
   be backend-neutral or explicitly marked as file-backed behavior.

## 5. Target End State

At the end of this plan:

- `weft system tidy` runs through `context.broker()` and backend-native vacuum.
- `weft system load --dry-run` uses backend-neutral broker state lookups.
- `weft system load` shares one parse/validation path with dry-run mode.
- alias conflicts are fatal before apply starts.
- `load` uses a tiny SQLite snapshot helper for file-backed rollback, and that
  helper is isolated from the generic apply logic.
- `.weft/` is always `.weft/`.
- runtime code no longer has hidden `"broker.db"` defaults.
- remaining runtime `BrokerDB` usage, if any, is explicit and justified.
- automated tests still run green on SQLite.
- there is a manual Postgres smoke test recipe that proves the changed paths on
  a real non-file backend.

## 6. Bite-Sized Tasks

### Task 0: Verify the Backend Env Alias Baseline

Goal:
Confirm the current branch already maps `WEFT_BACKEND*` to `BROKER_BACKEND*`.
If it does not, land that small config change first.

Read first:
[`weft/_constants.py`](../../weft/_constants.py),
[`tests/system/test_constants.py`](../../tests/system/test_constants.py),
[`../simplebroker/simplebroker/_constants.py`](../../../simplebroker/simplebroker/_constants.py),
[`../simplebroker/extensions/simplebroker_pg/README.md`](../../../simplebroker/extensions/simplebroker_pg/README.md).

Files to touch:
[`weft/_constants.py`](../../weft/_constants.py),
[`tests/system/test_constants.py`](../../tests/system/test_constants.py).

Red tests first:
Add or verify a config test that sets:

- `WEFT_BACKEND=postgres`
- `WEFT_BACKEND_HOST=...`
- `WEFT_BACKEND_PORT=...`
- `WEFT_BACKEND_USER=...`
- `WEFT_BACKEND_PASSWORD=...`
- `WEFT_BACKEND_DATABASE=...`
- `WEFT_BACKEND_SCHEMA=...`
- `WEFT_BACKEND_TARGET=...`

and asserts the corresponding `BROKER_BACKEND*` keys are present in
`load_config()`.

Implementation notes:
Do not add alias keys that SimpleBroker does not currently support.

Done when:
`load_config()` exposes the full backend alias set and the new test passes.

### Task 1: Lock the Context API Around Broker Targets, Not File Paths

Goal:
Make `WeftContext` explicit about backend shape and stop deriving `.weft/` from
broker filename config.

Read first:
[`weft/context.py`](../../weft/context.py),
[`tests/context/test_context.py`](../../tests/context/test_context.py),
[`docs/specifications/04-SimpleBroker_Integration.md`](../specifications/04-SimpleBroker_Integration.md).

Files to touch:
[`weft/context.py`](../../weft/context.py),
[`tests/context/test_context.py`](../../tests/context/test_context.py).

Red tests first:

1. Add a test that sets `WEFT_DEFAULT_DB_NAME` to a non-default value and
   asserts `ctx.weft_dir == root / ".weft"`.
2. Add a test that `ctx.backend_name == "sqlite"` for the normal harness path.
3. Add a test that `ctx.is_file_backed is True` for the normal harness path.

Implementation notes:

- Add `backend_name` and `is_file_backed` properties to `WeftContext`.
- Make `.weft/` fixed under the project root.
- Keep `database_path: Path | None` exactly as an optional file-backed detail.
- Do not change project root discovery semantics.
- Do not add new environment variables.

Invariants:

- `build_context()` must still create `.weft/`, `.weft/outputs/`,
  `.weft/logs/`, and `.weft/config.json`.
- If `database_path` is not `None`, parent directories still get created.
- `ctx.queue(...)` and `ctx.broker()` must still bind to the same
  `broker_target`.

Done when:
The new context tests pass and no code outside context discovery depends on
`.weft/` moving with broker filename config.

### Task 2: Generalize `weft system tidy`

Goal:
Make `tidy` use backend-native maintenance instead of SQLite file operations.

Read first:
[`weft/commands/tidy.py`](../../weft/commands/tidy.py),
[`weft/helpers.py`](../../weft/helpers.py),
[`../simplebroker/simplebroker/db.py`](../../../simplebroker/simplebroker/db.py),
[`../simplebroker/extensions/simplebroker_pg/simplebroker_pg/plugin.py`](../../../simplebroker/extensions/simplebroker_pg/simplebroker_pg/plugin.py).

Files to touch:
[`weft/commands/tidy.py`](../../weft/commands/tidy.py),
[`weft/helpers.py`](../../weft/helpers.py),
[`weft/cli.py`](../../weft/cli.py),
[`tests/cli/test_cli_tidy.py`](../../tests/cli/test_cli_tidy.py).

Red tests first:

1. Update the existing tidy CLI test so it proves idempotent success:
   run `weft system tidy` twice and expect exit code `0` both times.
2. Keep the test real. Use a real queue write to ensure there is broker state
   to compact.

Implementation notes:

- Replace the `database_path is None` branch with:

```python
with context.broker() as broker:
    broker.vacuum(compact=True)
```

- Return a user-facing success message using `context.broker_display_target`.
  That keeps file-backed output readable while still working for DSNs.
- Delete SQLite maintenance helpers from `weft/helpers.py` if they become
  unused. Do not leave dead compatibility wrappers behind.
- Remove raw `sqlite3` or `DBConnection` usage from the `tidy` path.
- Update the `weft system tidy` CLI help text in `weft/cli.py` so it no longer
  promises a WAL checkpoint implementation detail.

Invariants:

- `weft system tidy` must no longer require `context.database_path`.
- Exit code behavior stays simple: `0` on success, `1` on failure.
- The command must be safe to run repeatedly.

Done when:
`tidy` uses only backend-neutral broker access, the updated CLI test passes,
and `rg -n "wal_checkpoint|sqlite_wal|simplebroker_vacuum"` no longer finds
dead runtime references.

### Task 3: Refactor `load --dry-run` Into a Backend-Neutral Preflight

Goal:
Create one parse/validation path that dry-run mode and apply mode can share.

Read first:
[`weft/commands/load.py`](../../weft/commands/load.py),
[`weft/commands/dump.py`](../../weft/commands/dump.py),
[`tests/commands/test_dump_load.py`](../../tests/commands/test_dump_load.py),
[`../simplebroker/simplebroker/db.py`](../../../simplebroker/simplebroker/db.py).

Files to touch:
[`weft/commands/load.py`](../../weft/commands/load.py),
[`tests/commands/test_dump_load.py`](../../tests/commands/test_dump_load.py).

Red tests first:

1. Add a dry-run test that proves alias conflicts return exit code `3`.
2. In that same test, assert the destination broker state is unchanged after
   the dry-run.
3. Keep the existing preview formatting tests green.

Implementation notes:

- Introduce one internal representation for parsed import work. Call it
  something boring like `ParsedImport` or `ImportPlan`.
- That structure should hold:
  - parsed metadata,
  - validation warnings,
  - alias records,
  - message records,
  - per-queue counts,
  - total message count,
  - timestamp range,
  - and alias conflicts discovered against the current broker state.
- Discover current broker state with `with context.broker() as broker:`.
- Use `broker.list_aliases()`, `broker.list_queues()`, and `broker.get_meta()`
  instead of `BrokerDB(...)`.
- Keep skipping runtime queues with the `WEFT_STATE_QUEUE_PREFIX` rule.
- Keep validating timestamps for reporting, but do not use them to write
  messages back with exact original IDs.

Invariants:

- Dry-run must perform no writes.
- Alias conflicts must be known before apply starts.
- Preview formatting should stay stable enough that existing tests continue to
  pass with minimal updates.

Done when:
Dry-run builds its report without `BrokerDB`, the alias-conflict test passes,
and preview and apply modes can both reuse the same parsed import structure.

### Task 4: Refactor `load` Apply Into a Backend-Neutral Best-Effort Import

Goal:
Make apply mode use the shared preflight result, backend-neutral writes, and an
honest failure model.

Read first:
[`weft/commands/load.py`](../../weft/commands/load.py),
[`tests/commands/test_dump_load.py`](../../tests/commands/test_dump_load.py),
[`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py),
[`../simplebroker/simplebroker/db.py`](../../../simplebroker/simplebroker/db.py).

Files to touch:
[`weft/commands/load.py`](../../weft/commands/load.py),
[`tests/commands/test_dump_load.py`](../../tests/commands/test_dump_load.py).

Red tests first:

1. Add a test proving that alias conflicts in non-dry-run mode return exit code
   `3` and perform no writes.
   Capture destination state before the call with `context.broker()`:
   `list_aliases()`, `list_queues()`, and any queue contents you seed for the
   test. Assert the post-call state matches exactly.
2. Add a real SQLite rollback test for file-backed contexts:
   - create import input with one valid alias record first,
   - follow it with a message body large enough to exceed the broker max
     message size,
   - run `cmd_load(...)`,
   - assert the command fails,
   - and assert the alias added before the failure is not present afterward.
3. Keep the existing round-trip and successful import tests green.

Implementation notes:

- Apply mode must consume the already-built import plan. Do not re-parse the
  file in a second independent code path.
- If `plan.alias_conflicts` is non-empty, return exit code `3` before any
  snapshot creation or writes.
- Use `with context.broker() as broker:` for alias writes.
- Use `context.queue(queue_name, persistent=True)` for message writes.
- Add a tiny helper such as `_sqlite_snapshot_if_file_backed(context)`.
- That helper must be clearly named as file-backed or sqlite-specific.
- That helper must be small.
- That helper must not infect the generic apply logic.
- On non-file-backed backends, if apply fails after writes have started, return
  an error message that explicitly says a partial import may have occurred.

Invariants:

- No new runtime dependency on `BrokerDB`.
- No private `_runner` access.
- No exact timestamp replay.
- No silent partial success on alias conflict.

Done when:
Apply mode is driven by the shared plan, alias conflicts are preflight-fatal,
the rollback regression test passes on SQLite, and runtime `BrokerDB` usage is
gone from `load.py`.

### Task 5: Remove the Hidden `"broker.db"` and Path-Inference Fallbacks

Goal:
Eliminate the remaining runtime assumptions that a broker is always a local
SQLite file.

Read first:
[`weft/core/tasks/multiqueue_watcher.py`](../../weft/core/tasks/multiqueue_watcher.py),
[`weft/core/tasks/base.py`](../../weft/core/tasks/base.py),
[`tests/tasks/test_multiqueue_watcher.py`](../../tests/tasks/test_multiqueue_watcher.py),
relevant observability tests under `tests/tasks/`.

Files to touch:
[`weft/core/tasks/multiqueue_watcher.py`](../../weft/core/tasks/multiqueue_watcher.py),
[`weft/core/tasks/base.py`](../../weft/core/tasks/base.py),
[`tests/tasks/test_multiqueue_watcher.py`](../../tests/tasks/test_multiqueue_watcher.py),
and only add or update an observability test if a real regression risk remains.

Red tests first:

1. Keep existing multiqueue watcher tests green.
2. If you change process-title context inference materially, add one focused
   observability test that proves a valid title is still produced when no file
   path is available.

Implementation notes:

- In `multiqueue_watcher.py`, prefer explicit broker targets.
- Remove the string fallback `"broker.db"`.
- Remove `BrokerDB` from the runtime type path in this module. Current callers
  do not need it.
- If you still need a fallback target, resolve it through
  `target_for_directory(Path.cwd(), config=...)` so backend selection still
  works.
- In `base.py`, prefer `BrokerTarget.project_root` for process-title context.
- If there is no project root, use a neutral fallback like `"proj"` rather than
  walking `target_path.parent`.

Invariants:

- Queue watching still shares one broker target across queues.
- Process-title format remains
  `weft-{context_short}-{tid_short}:{name}:{status}[:details]`.
- No runtime code should silently assume a `.db` file when a broker target is
  absent.

Done when:
The watcher and process-title paths no longer depend on `"broker.db"` or
`target_path.parent`, and existing queue/task tests still pass.

### Task 6: Sweep Docs and Tests So the New Behavior Is Obvious

Goal:
Align the most relevant docs and test expectations with the actual backend
behavior after the code changes.

Read first:
[`README.md`](../../README.md),
[`docs/specifications/04-SimpleBroker_Integration.md`](../specifications/04-SimpleBroker_Integration.md),
[`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md),
[`docs/TODO-simplebroker-backend-followups.md`](../TODO-simplebroker-backend-followups.md),
the tests that still mention `.weft/broker.db`.

Files to touch:
[`README.md`](../../README.md),
[`docs/specifications/04-SimpleBroker_Integration.md`](../specifications/04-SimpleBroker_Integration.md),
[`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md),
[`docs/TODO-simplebroker-backend-followups.md`](../TODO-simplebroker-backend-followups.md),
and any tests whose assertions are no longer correct.

Implementation notes:

- Update only the docs directly affected by this slice.
- Document that `tidy` is backend-native through SimpleBroker maintenance.
- Document that `load` is preflight-then-apply and that non-file-backed imports
  may be best-effort if apply fails after writes begin.
- Remove or rewrite the backend follow-up TODO doc once the work is complete.
- Do not rewrite every historical SQLite mention in the repo. Stay scoped.

Test sweep guidance:

- Backend-neutral tests should assert broker behavior through
  `context.broker()` and `context.queue()`.
- SQLite/file-backed tests may still assert `context.database_path` or
  `.weft/broker.db` when the test is explicitly about file-backed behavior.
- If a test is inherently SQLite-shaped, make that explicit in the test name or
  comments. Do not pretend it is backend-neutral.

Done when:
The docs no longer claim `tidy` and `load` are SQLite-only, and the remaining
SQLite-specific tests are intentional rather than accidental.

### Task 7: Run the Final Audit and Gates

Goal:
Prove the slice is done and that you did not leave hidden SQLite assumptions in
runtime code.

Run these audits:

```bash
rg -n "BrokerDB\\(|sqlite3|sqlite-backed brokers only|currently supports sqlite|broker\\.db" weft tests docs
rg -n "target_path\\.parent|wal_checkpoint|simplebroker_vacuum|sqlite_wal" weft
```

Expected result:

- runtime hits should be either gone or obviously intentional file-backed code,
- test hits may remain where the test is explicitly SQLite-specific,
- doc hits should match the new behavior.

Run these focused commands while iterating:

```bash
uv run --extra dev pytest tests/context/test_context.py
uv run --extra dev pytest tests/cli/test_cli_tidy.py
uv run --extra dev pytest tests/commands/test_dump_load.py
uv run --extra dev pytest tests/tasks/test_multiqueue_watcher.py
```

Then run the full gates sequentially:

```bash
uv run --extra dev pytest
uv run --extra dev pytest -m ""
uv run --extra dev ruff check weft tests
uv run --extra dev mypy weft
```

Manual Postgres smoke test, if you have a local database and the plugin
installed:

```bash
uv pip install -e ../simplebroker/extensions/simplebroker_pg

export DSN='postgresql://postgres:postgres@127.0.0.1:54329/simplebroker_test'
export SRC_SCHEMA="weft_src_smoke_$$"
export DST_SCHEMA="weft_dst_smoke_$$"

SRC_DIR="$(mktemp -d)"
DST_DIR="$(mktemp -d)"

(
  cd "$SRC_DIR"
  export WEFT_BACKEND=postgres
  export WEFT_BACKEND_TARGET="$DSN"
  export WEFT_BACKEND_SCHEMA="$SRC_SCHEMA"
  uv run python -m weft.cli init
  uv run python -m weft.cli queue write smoke.queue hello
  uv run python -m weft.cli system dump
)

(
  cd "$DST_DIR"
  export WEFT_BACKEND=postgres
  export WEFT_BACKEND_TARGET="$DSN"
  export WEFT_BACKEND_SCHEMA="$DST_SCHEMA"
  uv run python -m weft.cli init
  uv run python -m weft.cli system load --dry-run --input "$SRC_DIR/.weft/weft_export.jsonl"
  uv run python -m weft.cli system load --input "$SRC_DIR/.weft/weft_export.jsonl"
  uv run python -m weft.cli queue read smoke.queue
  uv run python -m weft.cli system tidy
)
```

Expected manual smoke results:

- both `init` commands succeed,
- dry-run succeeds,
- apply succeeds,
- `queue read smoke.queue` prints `hello`,
- `system tidy` succeeds on the destination Postgres schema.

If you cannot run the Postgres smoke test, say so explicitly in handoff. Do not
imply it was covered.

## 8. Handoff Checklist

Do not claim the slice is done until all of the following are true:

- the code paths above no longer depend on SQLite by accident,
- dry-run and apply share one import-plan builder,
- alias conflicts are preflight-fatal,
- `tidy` is backend-native,
- `.weft/` is fixed,
- focused tests pass,
- full gates pass,
- and you can explain exactly which remaining SQLite-specific code is
  intentional and why.

If implementation pressure starts pulling toward private SimpleBroker runners,
timestamp replay, or a custom broker abstraction, stop. That is the wrong
direction.

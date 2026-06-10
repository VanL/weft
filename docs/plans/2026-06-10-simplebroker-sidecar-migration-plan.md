> **Execution note (2026-06-10):** Phases 1–2 are complete. SimpleBroker shipped the
> sidecar API as **4.5.0** (with simplebroker-pg 2.2.1 and simplebroker-redis 2.3.1),
> not 4.4.0 as drafted — read version references accordingly. Weft pins
> `simplebroker>=4.5.0`. Spec citation: no spec section governs the Monitor store's
> storage-access mechanism (verified: `grep -rn "_runner_from_broker\|broker\._runner\|sidecar" docs/specifications/` is empty);
> behavior specs ([OBS.13]) are unaffected by this migration. Line numbers cited for
> `weft/core/monitor/store.py` predate recent monitor work and have drifted; re-derive
> with the verification greps in each step.

# Sidecar Sessions & Public Watcher Surface — Implementation Plan

Status: completed
Source specs: none — storage-access mechanics are not spec-governed; behavior specs (docs/specifications/07-System_Invariants.md [OBS.13]) are unaffected
Superseded by: none

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give downstream embedders (weft today, more tomorrow) a formal, public API for
managing their own "sidecar" tables inside SimpleBroker's database, promote the watcher
subclassing contract into `simplebroker.ext`, and remove every private-module reach from
weft — phased around a publish-simplebroker-and-update-weft gate.

**Architecture:** A new `sidecar()` context manager on `BrokerCore` (and a delegating
convenience on `Queue`) yields a `SidecarSession` whose `run()` is the only way callers
execute SQL. The session inherits the broker's retry loop, its RLock, its
ephemeral-vs-persistent connection discipline, and its at-least-once generator guard —
the caller never sees the raw `SQLRunner`. The Redis backend (no SQL storage) raises a
new `SidecarUnavailableError`. Separately, `BaseWatcher`, `PollingStrategy`,
`default_error_handler`, and a newly public `StopWatching` exception (née `_StopLoop`)
are exported from `simplebroker.ext`. After simplebroker 4.4.0 and simplebroker-redis
2.3.0 are published (the gate), weft migrates: one import swap, the watcher import swap,
and the `MonitorStore` rewrite from `getattr(broker, "_runner")` to `broker.sidecar()`.

**Tech Stack:** Python ≥3.11, zero-dependency core, `uv` for everything, pytest (+xdist),
ruff, strict mypy. Repos: `/Users/van/Developer/simplebroker` and
`/Users/van/Developer/weft` (sibling checkouts; this is assumed throughout).

---

## Part I — Orientation (read this first)

### What these projects are

- **SimpleBroker** (`/Users/van/Developer/simplebroker`) is a message queue backed by
  SQLite (with Postgres and Redis backends shipped as extension packages from the same
  repo under `extensions/`). The core package has **zero runtime dependencies**. Public
  API surface for app developers is `simplebroker` (top-level); the systems-builder /
  extension surface is `simplebroker.ext`. Underscore-prefixed modules are private.
- **Weft** (`/Users/van/Developer/weft`) is a task-orchestration system that embeds
  SimpleBroker as its entire persistence substrate. It has **no database of its own**:
  weft state lives in broker queues (named `weft.*` and `T{tid}.*`) and in four
  "sidecar" tables that weft's `MonitorStore` creates *in the same database file* as
  SimpleBroker's queue tables. Today the store reaches a private attribute
  (`broker._runner`) to do that. This plan makes that pattern a supported API.

### Vocabulary you need

| Term | Meaning |
|---|---|
| **runner** (`SQLRunner`) | Protocol in `simplebroker/_runner.py` — `run(sql, params, *, fetch)`, `begin_immediate()`, `commit()`, `rollback()`, … Each backend implements it (SQLite, Postgres). Redis is a "direct backend" with **no** SQL runner (its `_runner` is a Redis-client wrapper, not a `SQLRunner`). |
| **`BrokerCore`** | `simplebroker/db.py:704`. The backend-agnostic core. Holds `self._runner` (db.py:749), `self._lock` (an `RLock`, db.py:741), and the retry wrapper `_run_with_retry` (db.py:788). |
| **`BrokerConnection`** | `typing.Protocol` in `simplebroker/_backend_plugins.py:212` describing what a broker handle can do. `BrokerCore` satisfies it; so must direct-backend cores like `RedisBrokerCore`. It is **not** `runtime_checkable`; it's a mypy-time contract. |
| **`Queue`** | `simplebroker/sbqueue.py:108`. User-facing handle. `Queue.get_connection()` (sbqueue.py:218) is a context manager that yields a `BrokerConnection`: persistent queues reuse a held connection, ephemeral queues (the default) open a fresh `DBConnection` per operation and tear it down ("get in, get out"). |
| **retry loop** | `_execute_with_retry` in `simplebroker/helpers.py` retries **only** lock/busy `OperationalError`s (see `_is_locked_operational_error`, helpers.py:148) with exponential backoff. `BrokerCore._run_with_retry` wraps it and honors the stop event. Precedent: transactions retry the `begin_immediate()` *acquisition*, never the statements inside (db.py:1292, db.py:1365). |
| **at-least-once generator batch** | `claim_generator`/`move_generator` with `delivery_guarantee="at_least_once"` hold a transaction open *while yielding* (db.py:1363–1400). A same-thread mutation during that window would corrupt the batch, so `BrokerCore._assert_no_reentrant_mutation_during_batch` (db.py:963) raises `RuntimeError`. The new sidecar API must call this same guard. |
| **sidecar table** | A caller-owned table living in the broker's database alongside SimpleBroker's own tables (`messages`, `meta`, and the alias table — `queue_aliases` on SQLite, `aliases` on Postgres). |

### Repository maps (files this plan touches)

**simplebroker** (Phase 1):

| Path | Role in this plan |
|---|---|
| `simplebroker/_exceptions.py` | Add `SidecarUnavailableError`. |
| `simplebroker/_sidecar.py` | **New file.** `SidecarSession` + `RESERVED_TABLE_NAMES`. |
| `simplebroker/db.py` | Add `BrokerCore.sidecar()` (one method, ~35 lines). |
| `simplebroker/sbqueue.py` | Add `Queue.sidecar()` delegate (~10 lines). |
| `simplebroker/_backend_plugins.py` | Add `sidecar` member to the `BrokerConnection` protocol. |
| `simplebroker/watcher.py` | Rename `_StopLoop` → `StopWatching` (keep `_StopLoop` alias); extend `__all__`. |
| `simplebroker/ext.py` | Export the new sidecar + watcher symbols. |
| `simplebroker/_constants.py` | Version bump `4.3.0` → `4.4.0` (line 38). |
| `pyproject.toml` | Version bump (line 7). |
| `extensions/simplebroker_redis/simplebroker_redis/core.py` | `RedisBrokerCore.sidecar()` raises `SidecarUnavailableError`. |
| `extensions/simplebroker_redis/pyproject.toml` | Version `2.2.0` → `2.3.0` (line 7); pin `simplebroker>=4.3.0` → `>=4.4.0` (line 17). |
| `tests/test_sidecar.py` | **New file.** Behavior tests (real databases, no mocks). |
| `tests/test_ext_imports.py` | Extend for new exports. |
| `extensions/simplebroker_pg/tests/test_pg_sidecar.py` | **New file.** One Postgres round-trip test. |
| `extensions/simplebroker_redis/tests/test_redis_sidecar.py` | **New file.** Unavailability test. |
| `README.md` | New "Sidecar tables" subsection (anchor at README.md:978–1013). |
| `CHANGELOG.md` | `[4.4.0]` entry. |

**weft** (Phase 3):

| Path | Role in this plan |
|---|---|
| `weft/commands/queue.py` | Line 25: import `TimestampGenerator` from `simplebroker.ext` instead of `simplebroker._timestamp`. |
| `weft/core/tasks/multiqueue_watcher.py` | Lines 28–33: import `BaseWatcher`, `PollingStrategy`, `StopWatching`, `default_error_handler` from `simplebroker.ext`; rename the one `_StopLoop` use (line 523). |
| `weft/core/monitor/store.py` | Replace `_runner_from_broker` / `_write_transaction` / local `_SQLRunner` protocol with a `_sidecar_session` helper built on `broker.sidecar()`. ~40 call sites. |
| `tests/core/test_monitor_store.py` | Add the "no private reach" firewall test (the red test that drives the migration). |
| `pyproject.toml` | Pin `simplebroker>=4.3.0` → `>=4.4.0` (in `dependencies`). |
| `CHANGELOG.md` | Entry. |
| `docs/plans/` | Copy of this plan (weft convention requires plans to live in weft's own `docs/plans/`). |

### Toolset crash course

Run everything through `uv` from the repo root. You never need to create or activate a
virtualenv yourself.

```bash
# simplebroker repo:
uv run pytest                       # default suite: -m 'not slow', parallel (~25s, expect "1206 passed, 11 skipped" at baseline)
uv run pytest tests/test_sidecar.py -v          # one file
uv run pytest tests/test_sidecar.py::test_name -v  # one test
uv run ruff check . && uv run ruff format --check .  # lint
uv run mypy simplebroker            # strict typing (disallow_untyped_defs etc.)
uv run bin/pytest-pg                # Postgres-backed suites (auto-starts Docker)
uv run bin/pytest-redis             # Redis-backed suites (auto-starts Docker)

# weft repo:
uv run pytest                       # weft suite (parallel)
uv run ruff check .
grep -n "tool.mypy" pyproject.toml && uv run mypy weft   # run mypy only if configured
```

Style rules (both repos): ruff line length 88, full type annotations on every function
(mypy is strict — `disallow_untyped_defs`), `from __future__ import annotations` at the
top of new modules, double quotes, imports sorted by isort (ruff enforces). Docstrings:
one-line summary, blank line, details; match neighboring code. Commit messages: plain
imperative subject lines matching `git log --oneline -10` in each repo (e.g. "Add
sidecar session API" — **no** `feat:`/`fix:` prefixes; neither repo uses conventional
commits).

### Test design rules for this plan (read carefully — this is a hard requirement)

The test suites in both repos test **real behavior against real databases**. SQLite is
an in-process library; there is nothing to mock. Specifically:

1. **Never mock, patch, or stub SimpleBroker internals** (`_runner`, `_run_with_retry`,
   connections, cursors) to make a test pass or to "isolate" a unit. If you are
   tempted to assert "method X was called with Y", stop: assert the *observable
   outcome* instead (a row exists, a row does not exist, an exception of type T was
   raised).
2. Every sidecar test gets a real database via pytest's `tmp_path` fixture and the
   public API (`Queue`, `q.get_connection()`, `q.sidecar()`).
3. Concurrency is tested with real threads and a real second `sqlite3` connection —
   the actual engine, not a simulation (see Task 8).
4. The **only** permitted test double in this entire plan is the *attribute firewall
   proxy* in weft's Task 22a. It wraps a **real** broker and forbids access to one
   private attribute. It exists to prove a negative ("the store no longer touches
   `broker._runner`") that cannot be observed any other way. It delegates everything
   else to the real object. Do not generalize this pattern.
5. New tests in `tests/` need **no backend markers**: simplebroker's
   `pytest_collection_modifyitems` hook (tests/conftest.py) auto-marks Python-API test
   modules as `sqlite_only`, which is correct here (Postgres and Redis get their own
   explicit tests in their extension test dirs).

### Design reference (locked — do not redesign during implementation)

The public surface added to simplebroker:

```python
from simplebroker import Queue
from simplebroker.ext import (
    RESERVED_TABLE_NAMES,      # frozenset of broker-owned table names
    SidecarSession,            # the session handle type
    SidecarUnavailableError,   # raised by non-SQL backends (Redis)
)

q = Queue("jobs", db_path="app.db")

# Autocommit mode: each run() is retry-wrapped and self-commits.
with q.sidecar() as session:
    rows = session.run("SELECT v FROM app_kv WHERE k = ?", ("a",), fetch=True)

# Transaction mode: BEGIN IMMEDIATE is acquired through the broker's retry
# loop; commit on clean exit; rollback if the block raises.
with q.sidecar(transaction=True) as session:
    session.run("CREATE TABLE IF NOT EXISTS app_kv (k TEXT PRIMARY KEY, v TEXT)")
    session.run("INSERT INTO app_kv (k, v) VALUES (?, ?)", ("a", "1"))
```

**Invariants (each maps to a test):**

| # | Invariant | Mechanism | Test |
|---|---|---|---|
| I1 | Sidecar SQL goes through the broker's retry loop. | Autocommit: each `run()` wrapped in `_run_with_retry`. Transaction: `begin_immediate` acquired via `_run_with_retry` (statements inside run bare — once the write lock is held they cannot go busy; this matches db.py:1292). | Task 8 |
| I2 | Connection discipline is inherited from the `Queue`. | `Queue.sidecar()` is implemented *on top of* `Queue.get_connection()`: ephemeral queues open/close per session, persistent queues reuse the lease. | Task 6 |
| I3 | Transaction mode commits on clean exit, rolls back on exception. | contextmanager control flow, mirroring `_execute_transactional_operation`. | Task 5 |
| I4 | A sidecar session cannot open while the same thread is yielding an at-least-once batch on the same core. | `sidecar()` calls `_assert_no_reentrant_mutation_during_batch("sidecar")` **in both modes** (an autocommit statement issued mid-batch would silently join the batch's open transaction — that's why reads are guarded too). | Task 7 |
| I5 | Sessions are scoped: using one after its `with` block raises. | `SidecarSession.close()` flips a flag; `run()` checks it. | Task 7 |
| I6 | Non-SQL backends fail loudly and typed. | `RedisBrokerCore.sidecar()` raises `SidecarUnavailableError`. | Task 12 |

**Deliberate non-goals (YAGNI — do NOT add these even if they feel helpful):**

- **No raw-runner accessor** (no `broker.runner` property, no escape hatch). Handing out
  the runner would bypass I1, I2, and I4. This was an explicit design decision.
- **No `supports_sidecar` boolean property.** Capability probing is "call it and catch
  `SidecarUnavailableError`" — one mechanism, not two.
- **No nested sidecar transactions.** A nested `begin_immediate` fails fast with
  `OperationalError` ("cannot start a transaction within a transaction" is not in the
  retryable list, helpers.py:28–32). Document, don't engineer around.
- **No SQL-dialect translation layer.** Caller SQL is the caller's portability problem.
  (Fact for the docs: the built-in SQLite backend uses `?` placeholders natively, and
  simplebroker-pg's runner textually translates `?` → `%s` —
  `extensions/simplebroker_pg/simplebroker_pg/runner.py:49–50` — so portable qmark SQL
  works on both. Note the translation is textual: a literal `?` inside a SQL string
  constant would also be rewritten.)
- **No phaselock integration for sidecar schema setup.** Idempotent
  `CREATE TABLE IF NOT EXISTS` inside a `transaction=True` session is already
  race-safe across processes (the IMMEDIATE lock serializes first-touch).
- **No new API for weft's other private reaches.** `weft/core/monitor/policies/task_log.py:374`
  and `weft/core/monitor/policies/dead_task.py:232` call `getattr(broker, "_retrieve", None)`.
  That is a *different* API gap (message retrieval, not sidecar tables), out of scope
  here. Leave those sites alone.
- **No change to `weft/core/queue_wait.py`'s `from simplebroker.watcher import
  QueueWatcher`** — `QueueWatcher` is already public (in `watcher.__all__` and the
  top-level `simplebroker` package).
- **simplebroker-pg needs no code change.** Postgres uses `BrokerCore` directly, so it
  inherits `sidecar()`. Do not release a new pg version.

**Threading/locking semantics to keep in mind (documented, not engineered):** the
session holds `BrokerCore._lock` (an RLock) for the duration of the `with` block, so a
sidecar session on a *shared persistent* handle serializes against queue operations on
that handle from other threads. Ephemeral queues open their own connection per session,
so contention is nil. Do not call queue operations (`q.write(...)` etc.) *inside* a
`sidecar(transaction=True)` block on the same persistent handle/thread: SQLite cannot
nest write transactions, and the inner `begin_immediate` will raise `OperationalError`.

---

## Phase 0 — Setup and baseline

### Task 0: Branch, baseline, and reading

**Files:** none modified.

- [ ] **Step 0.1: Read the context.** Skim, in this order:
  `simplebroker/ext.py` (all 49 lines), `simplebroker/db.py:704–800` (BrokerCore init +
  `_run_with_retry`), `db.py:950–1005` (the batch guard + `generate_timestamp`, your
  style template), `db.py:1271–1322` (`_execute_transactional_operation`, the
  transaction template), `simplebroker/sbqueue.py:156–272` (Queue init,
  `get_connection`, `refresh_last_ts`), `simplebroker/_runner.py` `SQLRunner` protocol
  (the `run()` signature you will mirror), `simplebroker/watcher.py:100–115` and
  `:238–242` (`__all__` and `_StopLoop`), `README.md:978–1015`.
- [ ] **Step 0.2: Create a branch in simplebroker.**

```bash
cd /Users/van/Developer/simplebroker
git checkout -b sidecar-api main
```

- [ ] **Step 0.3: Record the baseline.** All three must be clean before you start; if
  not, STOP and report — do not fix unrelated breakage inside this plan.

```bash
uv run pytest          # expect: ~"1206 passed, 11 skipped" (counts may drift slightly)
uv run ruff check .    # expect: "All checks passed!"
uv run mypy simplebroker   # expect: "Success: no issues found in 34 source files"
```

---

## Phase 1 — simplebroker: the sidecar API and the public watcher surface

Tasks 1–15. Each task is red → green → commit. Run the *full* suite only where a step
says to; targeted files otherwise (the full suite takes ~25s, so when in doubt, run it).

### Task 1: `SidecarUnavailableError`

**Files:**
- Modify: `simplebroker/_exceptions.py` (append before the trailing `# ~` line)
- Create: `tests/test_sidecar.py`

- [ ] **Step 1.1: Write the failing test.** Create `tests/test_sidecar.py`:

```python
"""Behavior tests for the public sidecar-table session API.

These tests run against real SQLite databases under tmp_path. Do not add
mocks: assert observable database state, not internal calls. This module is
auto-marked sqlite_only by conftest (Python-API tests with no run_cli usage);
Postgres and Redis coverage lives in the extension test directories.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from simplebroker import Queue
from simplebroker.ext import (
    RESERVED_TABLE_NAMES,
    SidecarSession,
    SidecarUnavailableError,
)


def test_sidecar_unavailable_error_is_broker_error() -> None:
    from simplebroker.ext import BrokerError

    assert issubclass(SidecarUnavailableError, BrokerError)
```

- [ ] **Step 1.2: Run it; verify it fails for the right reason.**

```bash
uv run pytest tests/test_sidecar.py -v
```

Expected: collection error — `ImportError: cannot import name 'RESERVED_TABLE_NAMES'
from 'simplebroker.ext'` (the module-level import fails before any test runs).

- [ ] **Step 1.3: Add the exception.** In `simplebroker/_exceptions.py`, after the
  `MessageError` class (line 81–87) and before the trailing `# ~`:

```python
class SidecarUnavailableError(BrokerError):
    """The active backend has no SQL storage for sidecar tables.

    Raised by ``sidecar()`` on backends (for example Redis/Valkey) that do
    not store queues in a SQL database. Catch this to detect the capability:
    there is deliberately no separate ``supports_sidecar`` flag.
    """

    pass
```

Do **not** wire the export into `ext.py` yet — that is Task 3, and the test stays red
until then. Commit nothing yet; Tasks 1–3 land as one commit in Step 3.5 because the
import test spans them. (Tasks 1–3 are separated so you implement one concept at a
time, not so each gets its own commit.)

### Task 2: `simplebroker/_sidecar.py` — the session type

**Files:**
- Create: `simplebroker/_sidecar.py`

- [ ] **Step 2.1: Create the module.** Full contents:

```python
"""Public sidecar-table session support.

Sidecar tables are caller-owned tables that live in the same database as
SimpleBroker's own tables. The sidecar API lets embedding applications (for
example weft's TaskMonitor store) manage those tables through the broker's
connection, retry, and locking discipline instead of reaching into private
attributes.

Sessions are created by ``BrokerCore.sidecar()`` / ``Queue.sidecar()`` and
are only valid inside their ``with`` block.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, Final

# Table names owned by SimpleBroker itself. Sidecar DDL must not touch these
# (or the broker's idx_* indexes). "queue_aliases" is the SQLite alias table;
# "aliases" is the Postgres one. Prefix your own tables (e.g. "myapp_...").
RESERVED_TABLE_NAMES: Final[frozenset[str]] = frozenset(
    {"messages", "meta", "queue_aliases", "aliases"}
)


class SidecarSession:
    """Executes caller-owned SQL through a broker-managed connection.

    Obtain one via ``Queue.sidecar()`` or ``BrokerCore.sidecar()``. The
    session is bound to its ``with`` block: using it afterwards raises
    ``RuntimeError``. ``run()`` mirrors the ``SQLRunner.run`` signature.
    """

    __slots__ = ("_closed", "_execute")

    def __init__(
        self, execute: Callable[..., Iterable[tuple[Any, ...]]]
    ) -> None:
        self._execute = execute
        self._closed = False

    def run(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
        *,
        fetch: bool = False,
    ) -> Iterable[tuple[Any, ...]]:
        """Execute one SQL statement with bound parameters.

        Args:
            sql: SQL statement using ``?`` (qmark) placeholders.
            params: Parameters for the statement.
            fetch: If True, return the result rows; otherwise an empty
                iterable.

        Raises:
            RuntimeError: If the session's ``with`` block has exited.
            OperationalError, IntegrityError, DataError: As raised by the
                backend for failing SQL.
        """
        if self._closed:
            raise RuntimeError(
                "sidecar session is closed; open a new one with sidecar()"
            )
        return self._execute(sql, params, fetch=fetch)

    def close(self) -> None:
        """Mark the session closed. Called by the owning context manager."""
        self._closed = True


# ~
```

- [ ] **Step 2.2: Sanity-check style gates on the new file.**

```bash
uv run ruff check simplebroker/_sidecar.py && uv run mypy simplebroker
```

Expected: both clean. (The import test is still red — `ext.py` comes next.)

### Task 3: Export the sidecar surface from `simplebroker.ext`

**Files:**
- Modify: `simplebroker/ext.py`
- Modify: `tests/test_ext_imports.py`

- [ ] **Step 3.1: Rewrite `simplebroker/ext.py`.** Replace the whole file with (this
  adds the `_sidecar` and `SidecarUnavailableError` imports; watcher exports come in
  Task 11 — do not add them yet):

```python
"""Public extension points for SimpleBroker.

This module provides the public API for extending SimpleBroker with custom
runners, backend plugins, and core components like timestamp generation,
plus the sidecar-table session surface for embedding applications.
"""

from ._backend_plugins import (
    ActivityWaiter,
    BackendAwareRunner,
    BackendPlugin,
    BrokerConnection,
    MultiQueueActivityWaiterHook,
    get_backend_plugin,
)
from ._exceptions import (
    BrokerError,
    DataError,
    IntegrityError,
    MessageError,
    OperationalError,
    QueueNameError,
    SidecarUnavailableError,
    TimestampError,
)
from ._runner import SetupPhase, SQLiteRunner, SQLRunner
from ._sidecar import RESERVED_TABLE_NAMES, SidecarSession
from ._timestamp import TimestampGenerator

__all__ = [
    # Protocols and implementations
    "SQLRunner",
    "SQLiteRunner",
    "SetupPhase",
    "BackendPlugin",
    "BrokerConnection",
    "ActivityWaiter",
    "BackendAwareRunner",
    "MultiQueueActivityWaiterHook",
    "get_backend_plugin",
    "TimestampGenerator",
    # Sidecar tables
    "RESERVED_TABLE_NAMES",
    "SidecarSession",
    # Exceptions
    "BrokerError",
    "OperationalError",
    "IntegrityError",
    "DataError",
    "TimestampError",
    "QueueNameError",
    "MessageError",
    "SidecarUnavailableError",
]

# ~
```

- [ ] **Step 3.2: Run the Task 1 test; verify green.**

```bash
uv run pytest tests/test_sidecar.py -v
```

Expected: `1 passed`.

- [ ] **Step 3.3: Extend `tests/test_ext_imports.py`.** In `test_ext_imports`, add to
  the import list and the assertions: `RESERVED_TABLE_NAMES`, `SidecarSession`,
  `SidecarUnavailableError`. In `test_ext_all_exports`, add the same three names to the
  `expected` list. Keep both lists alphabetized within their groups, matching the file's
  existing style.

- [ ] **Step 3.4: Run the ext-imports tests.**

```bash
uv run pytest tests/test_ext_imports.py -v
```

Expected: all pass.

- [ ] **Step 3.5: Commit Tasks 1–3.**

```bash
git add simplebroker/_exceptions.py simplebroker/_sidecar.py simplebroker/ext.py \
        tests/test_sidecar.py tests/test_ext_imports.py
git commit -m "Add SidecarSession type and sidecar ext exports"
```

### Task 4: `BrokerCore.sidecar()` — autocommit mode

**Files:**
- Modify: `simplebroker/db.py` (new method after `refresh_last_timestamp`, which ends at db.py:1004)
- Test: `tests/test_sidecar.py`

- [ ] **Step 4.1: Write the failing tests.** Append to `tests/test_sidecar.py`:

```python
def _db(tmp_path: Path) -> str:
    return str(tmp_path / "broker.db")


def test_autocommit_create_insert_select(tmp_path: Path) -> None:
    q = Queue("jobs", db_path=_db(tmp_path))
    with q.get_connection() as conn:
        with conn.sidecar() as session:
            assert isinstance(session, SidecarSession)
            session.run(
                "CREATE TABLE IF NOT EXISTS app_kv (k TEXT PRIMARY KEY, v TEXT)"
            )
            session.run("INSERT INTO app_kv (k, v) VALUES (?, ?)", ("a", "1"))
            rows = list(
                session.run("SELECT v FROM app_kv WHERE k = ?", ("a",), fetch=True)
            )
    assert rows == [("1",)]


def test_autocommit_rows_survive_across_connections(tmp_path: Path) -> None:
    db = _db(tmp_path)
    with Queue("jobs", db_path=db).get_connection() as conn:
        with conn.sidecar() as session:
            session.run(
                "CREATE TABLE IF NOT EXISTS app_kv (k TEXT PRIMARY KEY, v TEXT)"
            )
            session.run("INSERT INTO app_kv (k, v) VALUES (?, ?)", ("b", "2"))
    # A completely fresh handle sees the committed data: proof the sidecar
    # table lives in the same database file and autocommit is durable.
    with Queue("other", db_path=db).get_connection() as conn:
        with conn.sidecar() as session:
            rows = list(
                session.run("SELECT v FROM app_kv WHERE k = ?", ("b",), fetch=True)
            )
    assert rows == [("2",)]
```

- [ ] **Step 4.2: Run; verify failure mode.**

```bash
uv run pytest tests/test_sidecar.py -v
```

Expected: the two new tests FAIL with `AttributeError: 'BrokerCore' object has no
attribute 'sidecar'`.

- [ ] **Step 4.3: Implement autocommit mode.** In `simplebroker/db.py`:

  1. Everything needed is already imported: `contextmanager` (db.py:11) and
     `Callable, Iterable, Iterator, Mapping, Sequence` from `collections.abc`
     (db.py:10). Add exactly one new import: `from ._sidecar import SidecarSession`
     alongside the other relative imports.
  2. Insert this method in `BrokerCore`, immediately after `refresh_last_timestamp`
     (after db.py:1004), before `_decode_hybrid_timestamp`:

```python
    @contextmanager
    def sidecar(self, *, transaction: bool = False) -> Iterator[SidecarSession]:
        """Open a session for caller-owned sidecar tables in this database.

        Sidecar tables share the broker's database but are owned by the
        caller (see ``simplebroker.ext.RESERVED_TABLE_NAMES`` for the names
        you must not touch). Statements run through the broker's lock and
        retry discipline:

        - ``transaction=False`` (default): every ``run()`` is retried on
          lock contention and self-commits (runner autocommit).
        - ``transaction=True``: ``BEGIN IMMEDIATE`` is acquired through the
          retry loop; the transaction commits when the ``with`` block exits
          cleanly and rolls back if it raises. Statements inside the
          transaction are not individually retried (the write lock is
          already held). Do not nest, and do not call queue operations on
          this core inside the block: SQLite cannot nest write transactions.

        Both modes refuse to start while this thread is yielding an
        at-least-once generator batch from this core: in that window the
        connection has an open transaction, and sidecar statements would
        silently join it.

        Raises:
            SidecarUnavailableError: On backends without SQL storage.
            RuntimeError: If called during an open at-least-once batch.
        """
        self._assert_no_reentrant_mutation_during_batch("sidecar")
        with self._lock:
            if not transaction:

                def _run_autocommit(
                    sql: str,
                    params: tuple[Any, ...] = (),
                    *,
                    fetch: bool = False,
                ) -> Iterable[tuple[Any, ...]]:
                    return self._run_with_retry(
                        lambda: self._runner.run(sql, params, fetch=fetch)
                    )

                session = SidecarSession(_run_autocommit)
                try:
                    yield session
                finally:
                    session.close()
                return

            self._run_with_retry(self._runner.begin_immediate)
            session = SidecarSession(self._runner.run)
            try:
                yield session
            except BaseException:
                self._runner.rollback()
                raise
            finally:
                session.close()
            self._runner.commit()
```

  Notes for the implementer: the guard call and `with self._lock:` ordering
  deliberately mirrors `generate_timestamp` (db.py:979–989). The
  commit-after-`finally` placement is correct: on the exception path the `raise`
  exits the method before reaching `self._runner.commit()`.

- [ ] **Step 4.4: Run; verify green.**

```bash
uv run pytest tests/test_sidecar.py -v && uv run mypy simplebroker
```

Expected: all sidecar tests pass; mypy clean.

- [ ] **Step 4.5: Commit.**

```bash
git add simplebroker/db.py tests/test_sidecar.py
git commit -m "Add BrokerCore.sidecar autocommit sessions"
```

### Task 5: Transaction mode semantics

**Files:**
- Test: `tests/test_sidecar.py` (implementation already landed in Task 4)

- [ ] **Step 5.1: Write the tests.** Append:

```python
def test_transaction_commits_on_clean_exit(tmp_path: Path) -> None:
    db = _db(tmp_path)
    with Queue("jobs", db_path=db).get_connection() as conn:
        with conn.sidecar(transaction=True) as session:
            session.run(
                "CREATE TABLE IF NOT EXISTS app_kv (k TEXT PRIMARY KEY, v TEXT)"
            )
            session.run("INSERT INTO app_kv (k, v) VALUES (?, ?)", ("c", "3"))
    with Queue("jobs", db_path=db).get_connection() as conn:
        with conn.sidecar() as session:
            rows = list(session.run("SELECT v FROM app_kv", fetch=True))
    assert rows == [("3",)]


def test_transaction_rolls_back_on_exception(tmp_path: Path) -> None:
    db = _db(tmp_path)
    with Queue("jobs", db_path=db).get_connection() as conn:
        with conn.sidecar(transaction=True) as session:
            session.run(
                "CREATE TABLE IF NOT EXISTS app_kv (k TEXT PRIMARY KEY, v TEXT)"
            )

    class _Boom(Exception):
        pass

    with pytest.raises(_Boom):
        with Queue("jobs", db_path=db).get_connection() as conn:
            with conn.sidecar(transaction=True) as session:
                session.run(
                    "INSERT INTO app_kv (k, v) VALUES (?, ?)", ("d", "4")
                )
                raise _Boom()

    with Queue("jobs", db_path=db).get_connection() as conn:
        with conn.sidecar() as session:
            rows = list(session.run("SELECT v FROM app_kv", fetch=True))
    assert rows == []  # the insert was rolled back; the exception propagated
```

- [ ] **Step 5.2: Run; verify green** (the implementation exists, so these should pass
  immediately — if either fails, the Task 4 implementation has a control-flow bug; fix
  it there, do not adjust the tests).

```bash
uv run pytest tests/test_sidecar.py -v
```

- [ ] **Step 5.3: Commit.**

```bash
git add tests/test_sidecar.py
git commit -m "Cover sidecar transaction commit and rollback semantics"
```

### Task 6: `Queue.sidecar()` delegate (invariant I2)

**Files:**
- Modify: `simplebroker/sbqueue.py` (after `refresh_last_ts`, which ends at sbqueue.py:272)
- Test: `tests/test_sidecar.py`

- [ ] **Step 6.1: Write the failing tests.** Append:

```python
def test_queue_sidecar_ephemeral(tmp_path: Path) -> None:
    q = Queue("jobs", db_path=_db(tmp_path))
    with q.sidecar(transaction=True) as session:
        session.run(
            "CREATE TABLE IF NOT EXISTS app_kv (k TEXT PRIMARY KEY, v TEXT)"
        )
        session.run("INSERT INTO app_kv (k, v) VALUES (?, ?)", ("e", "5"))
    with q.sidecar() as session:
        rows = list(session.run("SELECT v FROM app_kv", fetch=True))
    assert rows == [("5",)]


def test_queue_sidecar_persistent(tmp_path: Path) -> None:
    with Queue("jobs", db_path=_db(tmp_path), persistent=True) as q:
        with q.sidecar(transaction=True) as session:
            session.run(
                "CREATE TABLE IF NOT EXISTS app_kv (k TEXT PRIMARY KEY, v TEXT)"
            )
            session.run("INSERT INTO app_kv (k, v) VALUES (?, ?)", ("f", "6"))
        q.write("interleaved")  # queue ops and sidecar ops share the handle
        with q.sidecar() as session:
            rows = list(session.run("SELECT v FROM app_kv", fetch=True))
        assert rows == [("6",)]
        assert q.read() == "interleaved"
```

- [ ] **Step 6.2: Run; verify failure.**

```bash
uv run pytest tests/test_sidecar.py -v
```

Expected: the two new tests FAIL with `AttributeError: 'Queue' object has no attribute
'sidecar'`.

- [ ] **Step 6.3: Implement.** In `simplebroker/sbqueue.py`: add
  `from ._sidecar import SidecarSession` to the relative imports, then insert after
  `refresh_last_ts` (sbqueue.py:272):

```python
    @contextmanager
    def sidecar(self, *, transaction: bool = False) -> Iterator[SidecarSession]:
        """Open a sidecar-table session against this queue's database.

        Connection lifetime follows this queue's mode: ephemeral queues open
        and close a connection for the session ("get in, get out");
        persistent queues reuse their held connection. See
        ``BrokerCore.sidecar`` for transaction semantics and
        ``simplebroker.ext.RESERVED_TABLE_NAMES`` for tables you must not
        touch.
        """
        with self.get_connection() as connection:
            with connection.sidecar(transaction=transaction) as session:
                yield session
```

  (`contextmanager` is already imported at sbqueue.py:11 and `Iterator` at
  sbqueue.py:10; the only new import is `SidecarSession`.)

- [ ] **Step 6.4: Run; verify green; run the full suite once.**

```bash
uv run pytest tests/test_sidecar.py -v && uv run pytest && uv run mypy simplebroker
```

- [ ] **Step 6.5: Commit.**

```bash
git add simplebroker/sbqueue.py tests/test_sidecar.py
git commit -m "Add Queue.sidecar delegate"
```

### Task 7: Misuse guards — batch guard (I4) and closed sessions (I5)

**Files:**
- Test: `tests/test_sidecar.py`

- [ ] **Step 7.1: Write the tests.** Append:

```python
def test_sidecar_blocked_during_at_least_once_batch(tmp_path: Path) -> None:
    with Queue("jobs", db_path=_db(tmp_path), persistent=True) as q:
        q.write("m1")
        q.write("m2")
        with q.get_connection() as conn:
            gen = conn.claim_generator(
                "jobs",
                with_timestamps=False,
                delivery_guarantee="at_least_once",
            )
            assert next(gen) == "m1"
            # The generator is suspended mid-batch: this thread holds an
            # open transaction. Both sidecar modes must refuse.
            with pytest.raises(RuntimeError, match="at_least_once"):
                with conn.sidecar():
                    pass
            with pytest.raises(RuntimeError, match="at_least_once"):
                with conn.sidecar(transaction=True):
                    pass
            gen.close()  # rolls the batch back; m1/m2 stay claimable


def test_session_unusable_after_block_exits(tmp_path: Path) -> None:
    q = Queue("jobs", db_path=_db(tmp_path))
    with q.sidecar() as session:
        session.run("CREATE TABLE IF NOT EXISTS app_kv (k TEXT, v TEXT)")
    with pytest.raises(RuntimeError, match="closed"):
        session.run("SELECT 1", fetch=True)
```

- [ ] **Step 7.2: Run; verify green.** Both behaviors were implemented in Tasks 2/4;
  these tests pin them. If `test_sidecar_blocked_during_at_least_once_batch` fails
  because no `RuntimeError` is raised, the most likely cause is that the guard call was
  omitted or placed after the lock acquisition incorrectly — re-check Step 4.3 against
  `db.py:979–989`; do not weaken the test.

```bash
uv run pytest tests/test_sidecar.py -v
```

- [ ] **Step 7.3: Commit.**

```bash
git add tests/test_sidecar.py
git commit -m "Pin sidecar batch-guard and closed-session behavior"
```

### Task 8: Retry discipline under real lock contention (I1)

**Files:**
- Test: `tests/test_sidecar.py`

This test uses a raw `sqlite3` connection as a *real* external lock holder — that is
the actual engine contending, not a mock. The sidecar writer must block/retry and then
succeed once the lock clears.

- [ ] **Step 8.1: Write the test.** Append:

```python
def test_sidecar_write_retries_until_external_lock_clears(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    q = Queue("jobs", db_path=db)
    q.write("seed")  # materialize the database file and schema first

    blocker = sqlite3.connect(db, timeout=1.0)
    try:
        blocker.execute("BEGIN IMMEDIATE")  # hold the write lock

        done = threading.Event()
        errors: list[Exception] = []

        def writer() -> None:
            try:
                with q.sidecar(transaction=True) as session:
                    session.run(
                        "CREATE TABLE IF NOT EXISTS app_kv "
                        "(k TEXT PRIMARY KEY, v TEXT)"
                    )
                    session.run(
                        "INSERT INTO app_kv (k, v) VALUES (?, ?)", ("g", "7")
                    )
            except Exception as exc:  # pragma: no cover - failure diagnostics
                errors.append(exc)
            finally:
                done.set()

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        # While the external lock is held the writer must not finish.
        assert not done.wait(0.3), "writer finished while the db was locked"
        blocker.rollback()
    finally:
        blocker.close()

    assert done.wait(15), "sidecar write never completed after lock release"
    thread.join(5)
    assert errors == []
    with q.sidecar() as session:
        rows = list(session.run("SELECT v FROM app_kv", fetch=True))
    assert rows == [("7",)]
```

- [ ] **Step 8.2: Run it several times to shake out timing flakiness.**

```bash
for i in 1 2 3; do
  uv run pytest tests/test_sidecar.py::test_sidecar_write_retries_until_external_lock_clears -q || break
done
```

Expected: PASS consistently. If the first assertion (`not done.wait(0.3)`) ever fails,
the external lock did not block the writer — check that `q.write("seed")` ran (the
file must exist before `blocker` connects) and that the blocker really issued
`BEGIN IMMEDIATE`.

- [ ] **Step 8.3: Commit.**

```bash
git add tests/test_sidecar.py
git commit -m "Prove sidecar writes retry through external lock contention"
```

### Task 9: Coexistence with queue operations and vacuum

**Files:**
- Test: `tests/test_sidecar.py`

- [ ] **Step 9.1: Write the test.** Append:

```python
def test_sidecar_tables_survive_queue_traffic_and_vacuum(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    q = Queue("jobs", db_path=db)
    for i in range(3):
        q.write(f"m{i}")
    assert q.read() == "m0"  # claim one message so vacuum has work to do

    with q.sidecar(transaction=True) as session:
        session.run(
            "CREATE TABLE IF NOT EXISTS app_state (k TEXT PRIMARY KEY, v TEXT)"
        )
        session.run("INSERT INTO app_state (k, v) VALUES (?, ?)", ("h", "8"))

    with q.get_connection() as conn:
        conn.vacuum()  # broker maintenance must not touch sidecar tables

    with q.sidecar() as session:
        rows = list(session.run("SELECT v FROM app_state", fetch=True))
    assert rows == [("8",)]
    assert q.read() == "m1"  # queue semantics undisturbed
    assert RESERVED_TABLE_NAMES >= {"messages", "meta"}
```

- [ ] **Step 9.2: Run; verify green; commit.**

```bash
uv run pytest tests/test_sidecar.py -v
git add tests/test_sidecar.py
git commit -m "Cover sidecar coexistence with queue traffic and vacuum"
```

### Task 10: Add `sidecar` to the `BrokerConnection` protocol

**Files:**
- Modify: `simplebroker/_backend_plugins.py` (protocol member; the protocol body runs
  `_backend_plugins.py:212–413`)

- [ ] **Step 10.1: Add the protocol member.** In the `BrokerConnection` protocol, after
  the `vacuum` member (`def vacuum(self, *, compact: bool = False) -> None: ...`,
  around line 391) insert:

```python
    def sidecar(
        self, *, transaction: bool = False
    ) -> AbstractContextManager[SidecarSession]: ...
```

  Imports: `_backend_plugins.py` has `from __future__ import annotations` (line 3) and
  an existing `if TYPE_CHECKING:` block (lines 13–15, where `SQLRunner` is imported the
  same way). Add both new imports inside that block, matching the file's pattern:

```python
if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from ._runner import SQLRunner
    from ._sidecar import SidecarSession
    from .metadata import QueueStats
```

  (Annotation-only imports; no runtime import, so no cycle is even possible.)

- [ ] **Step 10.2: Verify mypy accepts BrokerCore as a BrokerConnection.**

```bash
uv run mypy simplebroker && uv run pytest tests/test_sidecar.py -q
```

Expected: clean. (A `@contextmanager`-decorated method returns a
`_GeneratorContextManager`, which satisfies `AbstractContextManager` — if mypy
complains here, the protocol signature was typed differently from Step 10.1; fix the
protocol, not BrokerCore.)

- [ ] **Step 10.3: Commit.**

```bash
git add simplebroker/_backend_plugins.py
git commit -m "Add sidecar to the BrokerConnection protocol"
```

### Task 11: Public watcher surface — `StopWatching` and ext exports

**Files:**
- Modify: `simplebroker/watcher.py`
- Modify: `simplebroker/ext.py`
- Modify: `tests/test_ext_imports.py`
- Test: `tests/test_sidecar.py` is untouched; new assertions live in `tests/test_ext_imports.py`

Background: weft subclasses `BaseWatcher` and imports `PollingStrategy`,
`default_error_handler`, and the private `_StopLoop` from `simplebroker.watcher`
(weft `multiqueue_watcher.py:28–33`). We make that contract official:
`_StopLoop` becomes `StopWatching` (with a private alias kept so the old name keeps
working), and all four symbols are re-exported from `simplebroker.ext`.

- [ ] **Step 11.1: Write the failing test.** In `tests/test_ext_imports.py`, add:

```python
def test_watcher_contract_exports() -> None:
    """The watcher subclassing contract is part of the ext surface."""
    from simplebroker.ext import (
        BaseWatcher,
        PollingStrategy,
        StopWatching,
        default_error_handler,
    )
    from simplebroker.watcher import _StopLoop

    assert _StopLoop is StopWatching  # backwards-compatible private alias
    assert BaseWatcher is not None
    assert PollingStrategy is not None
    assert callable(default_error_handler)
```

- [ ] **Step 11.2: Run; verify it fails.**

```bash
uv run pytest tests/test_ext_imports.py -v
```

Expected: FAIL with `ImportError: cannot import name 'BaseWatcher' from
'simplebroker.ext'`.

- [ ] **Step 11.3: Rename `_StopLoop` in watcher.py.** There are 16 occurrences of
  `_StopLoop` in `simplebroker/watcher.py` (class at line 238, raises/catches at 404,
  414, 432, 433, 463, 485, 489, 492, 494, 764, 812, 823, 829, 1716, 1761). Rename them
  all, then re-add the alias. The alias is load-bearing, not cosmetic: weft imports
  `_StopLoop` until Phase 3, and simplebroker's own `tests/test_watcher_edge_cases.py`
  imports it in three places — both keep working through the alias, untouched.

```bash
sed -i '' 's/_StopLoop/StopWatching/g' simplebroker/watcher.py
```

  Then edit the class definition site (was line 238) to read:

```python
class StopWatching(Exception):
    """Sentinel raised inside watcher loops to stop watching gracefully.

    Subclasses of ``BaseWatcher`` (and error handlers) may raise this to
    end ``run_forever()`` cleanly. Public as of 4.4.0; ``_StopLoop`` remains
    as a private alias for older imports.
    """


# Backwards-compatible private alias (pre-4.4 name).
_StopLoop = StopWatching
```

- [ ] **Step 11.4: Extend `watcher.__all__`** (watcher.py:100–108) by adding
  `"BaseWatcher"`, `"PollingStrategy"`, and `"StopWatching"` to the list.

- [ ] **Step 11.5: Re-export from ext.** In `simplebroker/ext.py`, add:

```python
from .watcher import (
    BaseWatcher,
    PollingStrategy,
    StopWatching,
    default_error_handler,
)
```

  and append `"BaseWatcher"`, `"PollingStrategy"`, `"StopWatching"`,
  `"default_error_handler"` to `__all__` under a `# Watcher contract` comment, matching
  the file's grouping style. Also update `tests/test_ext_imports.py`'s two existing
  tests (`test_ext_imports`, `test_ext_all_exports`) to include the four new names.

  Note: `watcher.py` does not import `simplebroker.ext`, so this creates no import
  cycle. It does make `import simplebroker.ext` load the watcher module; that is
  acceptable for the systems-builder surface.

- [ ] **Step 11.6: Run the full suite** (the rename touched 16 call sites in the
  watcher hot paths; the existing watcher tests are the regression net):

```bash
uv run pytest && uv run ruff check . && uv run mypy simplebroker
```

Expected: same pass/skip counts as baseline, all green.

- [ ] **Step 11.7: Commit.**

```bash
git add simplebroker/watcher.py simplebroker/ext.py tests/test_ext_imports.py
git commit -m "Promote watcher subclass contract into ext as StopWatching et al"
```

### Task 12: Redis backend — `sidecar()` raises, version + pin bump

**Files:**
- Modify: `extensions/simplebroker_redis/simplebroker_redis/core.py`
- Modify: `extensions/simplebroker_redis/pyproject.toml`
- Create: `extensions/simplebroker_redis/tests/test_redis_sidecar.py`

Background: `RedisBrokerCore` (core.py:54) does **not** subclass `BrokerCore`, and its
`self._runner` (core.py:64) is a Redis-client wrapper, not a `SQLRunner` — which is
exactly why weft's current `getattr(broker, "_runner")` pattern would misbehave on
Redis. The formal API fails typed and loudly instead.

- [ ] **Step 12.1: Write the failing test.** Create
  `extensions/simplebroker_redis/tests/test_redis_sidecar.py`:

```python
"""Sidecar capability behavior on the Redis backend: it has none."""

from __future__ import annotations

import pytest
from simplebroker_redis import RedisRunner

from simplebroker import Queue
from simplebroker.ext import SidecarUnavailableError

pytestmark = [pytest.mark.redis_only]


def test_sidecar_raises_unavailable(redis_runner: RedisRunner) -> None:
    queue = Queue("jobs", runner=redis_runner, persistent=True)
    try:
        with pytest.raises(SidecarUnavailableError):
            with queue.sidecar():
                pass
        with pytest.raises(SidecarUnavailableError):
            with queue.sidecar(transaction=True):
                pass
    finally:
        queue.close()
```

  (`redis_runner` is an existing fixture in
  `extensions/simplebroker_redis/tests/conftest.py`; the `Queue(..., runner=...,
  persistent=True)` + `queue.close()` pattern copies
  `test_redis_integration.py::test_runner_queue_round_trip`.)

- [ ] **Step 12.2: Run; verify failure mode.**

```bash
uv run bin/pytest-redis
```

  (This runs the full Redis-backed suites with automatic Docker setup; find the new
  test in the output.) Expected: `test_sidecar_raises_unavailable` FAILS with
  `AttributeError: 'RedisBrokerCore' object has no attribute 'sidecar'`; everything
  else stays green.

- [ ] **Step 12.3: Implement.** In
  `extensions/simplebroker_redis/simplebroker_redis/core.py` (house style note: this
  first-party extension imports simplebroker's private modules directly — see core.py:22
  `from simplebroker._exceptions import IntegrityError, OperationalError, TimestampError`
  — so match that style rather than importing from `simplebroker.ext`):

  1. Add `from contextlib import AbstractContextManager` with the stdlib imports.
  2. Extend core.py:22 to
     `from simplebroker._exceptions import IntegrityError, OperationalError, SidecarUnavailableError, TimestampError`
     (ruff will re-wrap the line).
  3. Add `from simplebroker._sidecar import SidecarSession` next to the other
     `simplebroker.*` imports.

  Then add to `RedisBrokerCore`:

```python
    def sidecar(
        self, *, transaction: bool = False
    ) -> AbstractContextManager[SidecarSession]:
        """Sidecar tables require a SQL backend; Redis has no SQL storage."""
        raise SidecarUnavailableError(
            "the Redis backend does not support sidecar tables "
            "(no SQL storage); use the SQLite or Postgres backend"
        )
```

- [ ] **Step 12.4: Bump the extension's floor and version.** In
  `extensions/simplebroker_redis/pyproject.toml`: line 7 `version = "2.2.0"` →
  `version = "2.3.0"`; line 17 `"simplebroker>=4.3.0"` → `"simplebroker>=4.4.0"`
  (the new imports require 4.4.0). The unpublished floor is harmless locally: both the
  root project and the extension's own `[tool.uv.sources]` map `simplebroker` to the
  local editable checkout, and uv does not enforce version floors against path-sourced
  dependencies. There is no need to run `uv lock` anywhere in this step —
  `bin/release.py` refreshes lockfiles during the release.

- [ ] **Step 12.5: Run; verify green.**

```bash
uv run bin/pytest-redis
```

Expected: redis suite green including the new test. (Inside this monorepo the root
environment maps `simplebroker-redis` to the local editable checkout — root
`pyproject.toml` `[tool.uv.sources]` — so the unpublished pin is satisfied locally.)

- [ ] **Step 12.6: Commit.**

```bash
git add extensions/simplebroker_redis
git commit -m "Raise SidecarUnavailableError from the Redis backend"
```

### Task 13: Postgres coverage

**Files:**
- Create: `extensions/simplebroker_pg/tests/test_pg_sidecar.py`

- [ ] **Step 13.1: Write the test.** Postgres needs no implementation change
  (`BrokerCore` is shared), so this is a green-from-birth conformance test:

```python
"""Sidecar-session round trip on the Postgres backend."""

from __future__ import annotations

import pytest

from simplebroker.db import BrokerCore

pytestmark = [pytest.mark.pg_only]


def test_sidecar_round_trip_on_postgres(pg_core: BrokerCore) -> None:
    with pg_core.sidecar(transaction=True) as session:
        session.run(
            "CREATE TABLE IF NOT EXISTS app_sidecar_kv "
            "(k TEXT PRIMARY KEY, v TEXT)"
        )
        session.run(
            "INSERT INTO app_sidecar_kv (k, v) VALUES (?, ?)", ("a", "1")
        )
    with pg_core.sidecar() as session:
        rows = list(
            session.run(
                "SELECT v FROM app_sidecar_kv WHERE k = ?", ("a",), fetch=True
            )
        )
    assert rows == [("1",)]
```

  (`pg_core` is an existing fixture in `extensions/simplebroker_pg/tests/conftest.py`
  yielding a `BrokerCore` on a throwaway schema; it skips automatically when
  `SIMPLEBROKER_PG_TEST_DSN` is unset. The `?` placeholders are translated to `%s` by
  `PostgresRunner` — `simplebroker_pg/runner.py:49–50`.)

- [ ] **Step 13.2: Run; verify green; commit.**

```bash
uv run bin/pytest-pg
git add extensions/simplebroker_pg/tests/test_pg_sidecar.py
git commit -m "Cover sidecar sessions on the Postgres backend"
```

### Task 14: Versions, CHANGELOG, README

**Files:**
- Modify: `pyproject.toml` (line 7), `simplebroker/_constants.py` (line 38),
  `CHANGELOG.md`, `README.md`

- [ ] **Step 14.1: Bump the core version in BOTH places** (release tooling verifies
  they match): `pyproject.toml` line 7 `version = "4.3.0"` → `"4.4.0"`;
  `simplebroker/_constants.py` line 38 `__version__: Final[str] = "4.3.0"` → `"4.4.0"`.
  Then refresh the root lockfile so the committed lock matches the bumped version
  (uv resolves the editable extensions by path, so this is bookkeeping, not a fix):

```bash
uv lock && uv run pytest -q   # quick re-verification after the lock refresh
```

- [ ] **Step 14.2: Add the CHANGELOG entry.** At the top of `CHANGELOG.md`, after the
  header block and before `## [4.3.0]`:

```markdown
## [4.4.0] - 2026-06-10
### Added
- Added a public sidecar-table API for embedding applications: `Queue.sidecar()` /
  `BrokerCore.sidecar()` yield a `SidecarSession` for caller-owned tables in the
  broker's database, inheriting the broker's retry loop, locking, and
  ephemeral-vs-persistent connection discipline. Exported `SidecarSession`,
  `SidecarUnavailableError`, and `RESERVED_TABLE_NAMES` from `simplebroker.ext`.
- Promoted the watcher subclassing contract into `simplebroker.ext`: `BaseWatcher`,
  `PollingStrategy`, `default_error_handler`, and the new public `StopWatching`
  exception (the former private `_StopLoop`, which remains as an alias).

### simplebroker-redis 2.3.0
- `RedisBrokerCore.sidecar()` raises `SidecarUnavailableError` (the Redis backend has
  no SQL storage). Requires simplebroker>=4.4.0.
```

- [ ] **Step 14.3: Add the README section.** In `README.md`, insert a new subsection
  at the **end** of the `### Advanced: Custom Extensions` section — i.e. after its
  closing lines (`Backend authors should use the explicit extension contracts…` and
  `See [examples/]…`, README.md:1011–1013) and immediately before the
  `## Embedding SimpleBroker in Your Project` heading (README.md:1015). Inserting
  there, rather than mid-section, keeps the existing closing paragraphs inside their
  own section:

````markdown
### Sidecar tables (advanced)

Embedding applications sometimes need a few of their own tables living in the
broker's database — operational state that should share the broker's durability
and backups without a second storage system. The sidecar API supports exactly
that, through the broker's own locking and retry discipline:

```python
from simplebroker import Queue

q = Queue("jobs", db_path="app.db")

# Transactional writes: BEGIN IMMEDIATE through the broker's retry loop,
# commit on clean exit, rollback if the block raises.
with q.sidecar(transaction=True) as s:
    s.run("CREATE TABLE IF NOT EXISTS myapp_state (k TEXT PRIMARY KEY, v TEXT)")
    s.run("INSERT INTO myapp_state (k, v) VALUES (?, ?)", ("cursor", "42"))

# Autocommit reads/writes: each statement retried on lock contention.
with q.sidecar() as s:
    rows = list(s.run("SELECT v FROM myapp_state WHERE k = ?", ("cursor",), fetch=True))
```

Rules of the road:

- **Prefix your tables** (`myapp_...`) and never touch the broker's own tables —
  see `simplebroker.ext.RESERVED_TABLE_NAMES`.
- Connection lifetime follows the `Queue`: ephemeral queues get in and get out
  per session; `persistent=True` queues reuse their connection.
- Use `?` (qmark) placeholders. They work natively on SQLite and are translated
  by the Postgres backend. Other SQL dialect differences are yours to manage.
- The Redis backend has no SQL storage: `sidecar()` raises
  `SidecarUnavailableError` there. Catch it to probe the capability.
- Don't nest sidecar transactions and don't call queue operations inside a
  `sidecar(transaction=True)` block on the same persistent handle — SQLite
  cannot nest write transactions.
- Schema setup: idempotent `CREATE TABLE IF NOT EXISTS` (plus additive
  `ALTER TABLE`) inside a `transaction=True` session is race-safe across
  processes.
````

  (The block above is fenced with four backticks only so it can carry the inner
  ```python fence inside this plan document; in the README itself, paste the content
  between the four-backtick lines verbatim — the inner fence is an ordinary README
  code block, like its neighbors.)

- [ ] **Step 14.4: Commit.**

```bash
git add pyproject.toml simplebroker/_constants.py uv.lock CHANGELOG.md README.md
git commit -m "Bump to 4.4.0 and document sidecar sessions"
```

### Task 15: Full Phase-1 gate (all backends + cross-repo smoke)

**Files:** none modified.

- [ ] **Step 15.1: Full local gates.** Every one of these must be green:

```bash
uv run pytest                 # includes tests/test_weft_sqlite_stop_corruption_regression.py
                              # if ../weft + its .venv exist (it skips cleanly otherwise)
uv run ruff check . && uv run ruff format --check .
uv run mypy simplebroker bin/release.py \
  extensions/simplebroker_pg/simplebroker_pg \
  extensions/simplebroker_redis/simplebroker_redis \
  extensions/simplebroker_redis/tests
uv run bin/pytest-pg
uv run bin/pytest-redis
```

  (The redis **tests** directory is included because the release precheck type-checks
  it too — root mypy config excludes `^tests/` and the pg tests, but not the redis
  tests; matching the release gate here avoids a surprise at Task 16.)

- [ ] **Step 15.2: Cross-repo smoke — current weft against new simplebroker.** This
  proves 4.4.0 is non-breaking for weft *before* publishing (weft 0.9.75 still uses the
  private `_runner` reach; that attribute still exists, so it must keep working):

```bash
cd /Users/van/Developer/weft
uv run --with-editable /Users/van/Developer/simplebroker pytest \
  tests/core/test_monitor_store.py tests/tasks/test_task_monitor.py -q
cd /Users/van/Developer/simplebroker
```

Expected: all pass. If anything fails here, 4.4.0 has a regression — STOP and fix in
Phase 1; do not proceed to the gate.

- [ ] **Step 15.3: Push the branch and open a PR; merge to main once CI is green.**

```bash
git push -u origin sidecar-api
gh pr create --title "Add public sidecar session API and watcher ext surface" \
  --body "Sidecar sessions (Queue.sidecar/BrokerCore.sidecar, SidecarSession, SidecarUnavailableError, RESERVED_TABLE_NAMES), watcher contract promoted to ext (BaseWatcher, PollingStrategy, StopWatching, default_error_handler), Redis raises typed unavailability. Plan: docs/plans/2026-06-10-sidecar-sessions-and-public-watcher-surface-plan.md"
```

---

## Phase 2 — THE GATE: publish simplebroker, update weft's pin

Nothing in Phase 3 may start until every step here is verified.

Why this is a **batch** release: the two packages are codependent after Phase 1 —
simplebroker-redis 2.3.0 pins `simplebroker>=4.4.0`, and the core release flow rewrites
the root `simplebroker[redis]` extra to `>=2.3.0` and therefore requires the in-tree
redis version to already exist on PyPI (`require_published_redis_baseline`,
bin/release.py:438–446, reading the in-tree version per bin/release.py:483–491). Neither
single-target release can go first. `bin/release.py all` exists for exactly this: it
discovers all unpublished package versions, accepts in-batch baselines
(bin/release.py:1211–1227), makes one release commit, and pushes both tags
(`_run_batch_release`, bin/release.py:1386–1533). Do not run `core` or `redis`
individually here — `core` alone aborts with "Release … first".

### Task 16: Batch-release simplebroker 4.4.0 + simplebroker-redis 2.3.0

- [ ] **Step 16.1:** On an up-to-date `main` with Phase 1 merged:

```bash
cd /Users/van/Developer/simplebroker
git checkout main && git pull
uv run bin/release.py all
```

  The helper picks up the already-bumped, unpublished versions (4.4.0 and 2.3.0), runs
  the full precheck battery (pytest, pytest-pg, pytest-redis, ruff, mypy), syncs the
  root redis extra floor to `>=2.3.0`, refreshes lockfiles, creates one release commit,
  tags both packages, and pushes.

- [ ] **Step 16.2: Watch CI yourself.** The helper does **not** poll: it exits after
  printing "Next step: wait for <workflow> on <tag>" (bin/release.py:1711–1716). Watch
  the tag-triggered release workflows in GitHub Actions until green (they gate on the
  Test / Test Postgres Extension / Test Redis Extension workflows).

### Task 17: Verify both packages on PyPI — the hard gate condition

- [ ] **Step 17.1:**

```bash
curl -s https://pypi.org/pypi/simplebroker/4.4.0/json | head -c 120; echo
curl -s https://pypi.org/pypi/simplebroker-redis/2.3.0/json | head -c 120; echo
```

Expected: both print JSON starting `{"info": {"author"...` — not a 404 body. The
release helper never publishes to PyPI itself; if your tag workflow does not publish
automatically either, upload the built artifacts now (`uv build && uv publish` per
package, or your standard upload path), then re-run both curl checks.

### Task 18: Update weft's pin to the published release

- [ ] **Step 18.1: Branch in weft.**

```bash
cd /Users/van/Developer/weft
git checkout -b simplebroker-4.4 main
```

- [ ] **Step 18.2: Bump the pin.** In weft's `pyproject.toml` `dependencies` list,
  change `"simplebroker>=4.3.0"` → `"simplebroker>=4.4.0"`. Then:

```bash
uv lock
uv run pytest    # FULL weft suite against the published 4.4.0
```

- [ ] **Step 18.3: STOP-rule.** If the weft suite is not green here, **stop the plan**
  and report: the published 4.4.0 broke weft, and the fix belongs in simplebroker (a
  4.4.1), not in weft workarounds. Do not begin Phase 3 on a red baseline.

- [ ] **Step 18.4: Commit the pin bump** (Phase 3 continues on this branch):

```bash
git add pyproject.toml uv.lock
git commit -m "Require simplebroker 4.4.0"
```

---

## Phase 3 — weft: conforming changes

Weft has its own engineering conventions. Before touching code:

### Task 19: Weft orientation

- [ ] **Step 19.1: Read weft's agent context** (required by weft's `CLAUDE.md`):
  `docs/agent-context/README.md`, `docs/agent-context/decision-hierarchy.md`,
  `docs/agent-context/engineering-principles.md`, and
  `docs/agent-context/runbooks/testing-patterns.md`.
- [ ] **Step 19.2: Copy this plan into weft** (weft convention: plans live in weft's
  own `docs/plans/`, dated):

```bash
cp /Users/van/Developer/simplebroker/docs/plans/2026-06-10-sidecar-sessions-and-public-watcher-surface-plan.md \
   /Users/van/Developer/weft/docs/plans/2026-06-10-simplebroker-sidecar-migration-plan.md
git add docs/plans/2026-06-10-simplebroker-sidecar-migration-plan.md
git commit -m "Import sidecar migration plan"
```

  Spec citation note (weft requires plans to cite specs or say none exists): the
  Monitor store's behavior spec is referenced in code as `[OBS.13]`. This migration
  changes *how the store reaches storage*, not its behavior. Check whether any spec
  text documents the private-runner mechanism:

```bash
grep -rn "_runner_from_broker\|broker\._runner\|sidecar" docs/specifications/
```

  (A bare `_runner` grep is useless here — it matches unrelated task-runner text like
  `_runner_plugins.py` and `subprocess_runner.py`.) If a spec describes the
  storage-access mechanics, update that wording in Task 23; as of this writing the
  precise grep finds nothing, so expect to state in the weft plan copy: "No spec
  section governs the storage-access mechanism; behavior specs ([OBS.13]) are
  unaffected."

### Task 20: Import swap #1 — `TimestampGenerator` from ext

**Files:**
- Modify: `weft/commands/queue.py:25`

- [ ] **Step 20.1: Edit.** `weft/commands/queue.py` line 25–26 currently read:

```python
from simplebroker._timestamp import TimestampGenerator
from simplebroker.ext import TimestampError
```

  Replace both lines with one:

```python
from simplebroker.ext import TimestampError, TimestampGenerator
```

  (Usage sites — `TimestampGenerator.validate(...)` at queue.py:939, 942, 1152 — are
  unchanged; `simplebroker.ext` has exported `TimestampGenerator` since long before
  4.4.0, at ext.py:25/38.)

- [ ] **Step 20.2: Verify nothing else imports the private module, then test.**

```bash
grep -rn "simplebroker\._timestamp" weft/ && echo "FAIL: private import remains" || echo "clean"
uv run pytest tests/commands -q -k "queue"
uv run ruff check weft/commands/queue.py
```

Expected: "clean"; targeted tests pass. (If `tests/commands` selects nothing in your
checkout, run `uv run pytest -q -k "queue"`.)

- [ ] **Step 20.3: Commit.**

```bash
git add weft/commands/queue.py
git commit -m "Import TimestampGenerator from simplebroker.ext"
```

### Task 21: Import swap #2 — watcher contract from ext

**Files:**
- Modify: `weft/core/tasks/multiqueue_watcher.py` (imports at lines 28–33; one use of
  `_StopLoop` at line 523)

- [ ] **Step 21.1: Edit the import block.** Lines 26–33 currently read:

```python
from simplebroker import BrokerTarget, Queue, create_activity_waiter_for_queues
from simplebroker.ext import BrokerError
from simplebroker.watcher import (
    BaseWatcher,
    PollingStrategy,
    _StopLoop,
    default_error_handler,
)
```

  Replace with:

```python
from simplebroker import BrokerTarget, Queue, create_activity_waiter_for_queues
from simplebroker.ext import (
    BaseWatcher,
    BrokerError,
    PollingStrategy,
    StopWatching,
    default_error_handler,
)
```

  Then change line 523's `except _StopLoop:` to `except StopWatching:` (it is the only
  `_StopLoop` use in weft — verify: `grep -rn "_StopLoop" weft/` must return nothing
  after the edit).

- [ ] **Step 21.2: Run the watcher/task tests.**

```bash
uv run pytest tests/tasks -q
uv run ruff check weft/core/tasks/multiqueue_watcher.py
```

- [ ] **Step 21.3: Commit.**

```bash
git add weft/core/tasks/multiqueue_watcher.py
git commit -m "Use the public watcher contract from simplebroker.ext"
```

### Task 22: Migrate `MonitorStore` to sidecar sessions

This is the heart of Phase 3. `weft/core/monitor/store.py` (~2,700 lines) currently:

- defines a private structural protocol `_SQLRunner` (store.py:81–95),
- defines `_write_transaction(runner)` (store.py:97–107),
- extracts the runner via `_runner_from_broker(broker)` = `getattr(broker, "_runner")`
  (store.py:2650),
- and at ~40 call sites does `with self._context.broker() as broker:` + one of three
  patterns (read, write-transaction, or the two-transaction special at
  store.py:1659–1685).

Target state: one `_sidecar_session()` helper; `_MonitorTableAccess` holds a
`SidecarSession`; the three private constructs are deleted.

- [ ] **Step 22.1 (RED): Write the firewall test.** Append to
  `tests/core/test_monitor_store.py` (imports at the top of the test file:
  `from contextlib import contextmanager` and
  `from collections.abc import Iterator` if not already present, plus `from typing
  import Any`):

```python
class _NoPrivateRunnerBroker:
    """A real broker behind a firewall that forbids private runner access.

    This is the one sanctioned test double in the sidecar migration: it
    wraps the REAL broker object and delegates everything except the
    private ``_runner`` attribute, proving the store works through the
    public surface alone.
    """

    def __init__(self, inner: Any) -> None:
        object.__setattr__(self, "_inner", inner)

    def __getattr__(self, name: str) -> Any:
        if name == "_runner":
            raise AssertionError(
                "MonitorStore must not reach into broker._runner"
            )
        return getattr(object.__getattribute__(self, "_inner"), name)


class _FirewalledContext:
    """Delegating WeftContext wrapper that firewalls broker handles."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    @contextmanager
    def broker(self) -> Iterator[Any]:
        with self._inner.broker() as broker:
            yield _NoPrivateRunnerBroker(broker)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def test_store_uses_only_public_broker_surface(tmp_path) -> None:
    context = _FirewalledContext(_context(tmp_path))
    store = open_monitor_store(context)
    try:
        store.ensure_schema()
        assert store.get_checkpoint(WEFT_GLOBAL_LOG_QUEUE) is None
    finally:
        store.close()
```

  All names are verified against the current tree: `open_monitor_store(context)`
  (store.py:635, `config` is optional), `MonitorStore.ensure_schema` (store.py:1566),
  `MonitorStore.get_checkpoint(queue_name) -> int | None` (store.py:1601), and
  `MonitorStore.close` (store.py:1559). `_context`, `open_monitor_store`, and
  `WEFT_GLOBAL_LOG_QUEUE` are already imported at the top of this test file. The test
  intentionally exercises one write path (`ensure_schema`) and one read path
  (`get_checkpoint`) through the firewalled context.

- [ ] **Step 22.2: Run; verify RED for the right reason.**

```bash
uv run pytest tests/core/test_monitor_store.py::test_store_uses_only_public_broker_surface -v
```

Expected: FAIL with `AssertionError: MonitorStore must not reach into broker._runner`
(raised from `_runner_from_broker`'s `getattr`). Any other failure means the harness
wiring is wrong — fix the test setup before proceeding.

- [ ] **Step 22.3: Add the session helper and migrate `ensure_schema`.** In
  `weft/core/monitor/store.py`:

  1. Imports: add `from simplebroker.ext import SidecarSession,
     SidecarUnavailableError` after the existing third-party imports
     (the file already imports `contextmanager` at store.py:35).
  2. Add to `MonitorStore`, right after the `_access` method (store.py:1552–1557):

```python
    @contextmanager
    def _sidecar_session(
        self, *, transaction: bool = False
    ) -> Iterator[SidecarSession]:
        """Yield a broker sidecar session, mapping capability errors.

        SidecarUnavailableError only originates from ``broker.sidecar()``
        itself (non-SQL backends), so mapping it here cannot mask errors
        raised by Monitor-store SQL inside the block.
        """
        with self._context.broker() as broker:
            try:
                with broker.sidecar(transaction=transaction) as session:
                    yield session
            except SidecarUnavailableError as exc:
                raise MonitorStoreUnavailable(
                    f"Monitor store requires a SQL broker backend: {exc}"
                ) from exc
```

  3. Migrate `ensure_schema` (store.py:1566–1599) as the template. **Important:** the
     `_write_transaction` block in this method contains more than the
     `access.ensure_schema()` call — it also reads the stored schema version, writes
     `WEFT_MONITOR_SCHEMA_VERSION_KEY` when missing/older, and raises
     `MonitorStoreUnavailable` when newer (store.py:1583–1599). **Every line of that
     body stays, byte-identical.** The transformation rule for this and every Shape-B
     site: only the plumbing lines change —

  Before (plumbing):

```python
        with self._context.broker() as broker:
            runner = _runner_from_broker(broker)
            access = self._access(runner)
            with _write_transaction(runner):
                ... existing transaction body, possibly many lines ...
```

  After (plumbing):

```python
        with self._sidecar_session(transaction=True) as session:
            access = self._access(session)
            ... the same transaction body, unchanged, de-indented one level ...
```

  Verify with `git diff weft/core/monitor/store.py` after each method: inside the
  method, only the `with`/`runner`/`access` plumbing lines may appear in the diff.

- [ ] **Step 22.4: Retype `_MonitorTableAccess` and rename its handle.**

  1. Change the constructor (store.py:656–666) **by hand first**: parameter
     `runner: _SQLRunner` → `session: SidecarSession`, and the assignment
     `self._runner = runner` → `self._session = session`.
  2. Rename the remaining uses — every one is an attribute call of the form
     `self._runner.run(...)`, so a dot-anchored pattern is exact (do NOT use `\b`:
     macOS ships BSD sed, where `\b` is not a word boundary):

```bash
sed -i '' 's/self\._runner\./self._session./g' weft/core/monitor/store.py
grep -n "self\._runner" weft/core/monitor/store.py   # must print nothing
```

  3. Change `_access`'s signature (store.py:1552): `def _access(self, runner:
     _SQLRunner)` → `def _access(self, session: SidecarSession)` and pass `session`
     through to `_MonitorTableAccess`.

- [ ] **Step 22.5: Migrate the remaining call sites.** Every site matches one of three
  shapes. Work top-to-bottom through this list of `with self._context.broker()` lines
  (from the pre-migration file): 1604, 1615, 1659, 1713, 1726, 1741, 1764, 1785, 1805,
  1826, 1845, 1880, 1911, 1932, 1953, 2006, 2035, 2055, 2073, 2097, 2124, 2149, 2185,
  2235, 2273, 2294, 2309, 2319, 2332.

  **Shape A — read (no transaction).** Before / after:

```python
        with self._context.broker() as broker:
            return self._access(_runner_from_broker(broker)).get_checkpoint(...)
```

```python
        with self._sidecar_session() as session:
            return self._access(session).get_checkpoint(...)
```

  **Shape B — single write transaction.** Before / after:

```python
        with self._context.broker() as broker:
            runner = _runner_from_broker(broker)
            access = self._access(runner)
            with _write_transaction(runner):
                access.upsert_record(record)
```

```python
        with self._sidecar_session(transaction=True) as session:
            self._access(session).upsert_record(record)
```

  (Where the original body used `access` several times inside the transaction, keep a
  local: `access = self._access(session)`.)

  **Shape C — multiple transactions under one broker (store.py:1659–1689 only).** The
  original opens one broker and runs `_write_transaction(runner)` once **per chunk**
  inside a `for chunk in _chunks(...)` loop, plus a second, conditional
  `_write_transaction` for the checkpoint advance. Convert each `_write_transaction`
  block to its own `with self._sidecar_session(transaction=True) as session:` (the
  chunk loop therefore opens one session per chunk; derive `access =
  self._access(session)` inside each). This opens the broker once per transaction
  instead of once overall — acceptable and intended: the blocks were separate
  transactions already (no atomicity is lost), and per-session open/close is exactly
  the get-in/get-out discipline. Add no comment marking this; the code is
  self-evident.

- [ ] **Step 22.6: Delete the dead plumbing.** Remove `_SQLRunner` (store.py:81–95),
  `_write_transaction` (store.py:97–107), and `_runner_from_broker` (store.py:2650).
  If `Protocol` is now unused in the file's `typing` import, remove it (ruff will tell
  you). Verification:

```bash
grep -n "_runner_from_broker\|_write_transaction\|_SQLRunner" weft/core/monitor/store.py
```

Expected: no output.

- [ ] **Step 22.7: Run; verify GREEN, including the firewall test.**

```bash
uv run pytest tests/core/test_monitor_store.py tests/tasks/test_task_monitor.py -v
uv run ruff check weft/core/monitor/store.py
grep -n "tool.mypy" pyproject.toml && uv run mypy weft || true
```

Expected: all green; the firewall test now passes because the store only calls
`broker.sidecar(...)`.

- [ ] **Step 22.8: Commit.**

```bash
git add weft/core/monitor/store.py tests/core/test_monitor_store.py
git commit -m "Drive MonitorStore through the public sidecar session API"
```

### Task 23: Weft docs

**Files:**
- Modify: `CHANGELOG.md` (weft's), possibly `docs/specifications/*` (only if Task 19's
  grep found storage-access wording)

- [ ] **Step 23.1: CHANGELOG entry** (match weft's existing entry style at the top of
  the file):

```markdown
- Monitor store now uses SimpleBroker's public sidecar-session API
  (`broker.sidecar()`) instead of the private `_runner` attribute; requires
  simplebroker>=4.4.0. Watcher imports moved to `simplebroker.ext`
  (`StopWatching` replaces the private `_StopLoop`). No behavior change.
```

- [ ] **Step 23.2:** If Task 19's spec grep found `_runner` mechanics documented in
  `docs/specifications/`, update that text to describe the sidecar session instead.
  Otherwise note "no spec change needed" in the commit message.

- [ ] **Step 23.3: Commit.**

```bash
git add CHANGELOG.md docs/
git commit -m "Document sidecar migration"
```

### Task 24: Weft full gate and landing

- [ ] **Step 24.1: Full suite + lint, final sweep for private reaches.**

```bash
uv run pytest
uv run ruff check .
grep -rn "simplebroker\._" weft/ --include="*.py"
```

Expected: suite green; the grep returns **nothing** (the `_retrieve` getattr reaches in
`weft/core/monitor/policies/` use `getattr` strings, not imports, and are explicitly
out of scope — they will not appear in this grep).

- [ ] **Step 24.2: Push, PR, land per weft's normal flow.**

```bash
git push -u origin simplebroker-4.4
gh pr create --title "Adopt simplebroker 4.4.0 public surfaces" \
  --body "Pin simplebroker>=4.4.0; TimestampGenerator from ext; watcher contract from ext (StopWatching); MonitorStore on the public sidecar-session API. Plan: docs/plans/2026-06-10-simplebroker-sidecar-migration-plan.md"
```

- [ ] **Step 24.3 (optional, maintainer cadence): release weft** per its own
  `bin/release.py` when you would normally cut a release. Not a gate for this plan.

---

## Rollback notes

- **Phase 1 (pre-publish):** plain `git revert` territory; nothing external moved.
- **Phase 2:** published packages are immutable — roll *forward* (4.4.1) if a defect
  ships. The weft pin bump commit is independently revertable.
- **Phase 3:** each task is one commit; `MonitorStore` migration (Task 22) is a single
  commit and reverts cleanly. Weft on simplebroker 4.4.0 *without* Phase 3 is fully
  supported (4.4.0 keeps `_runner` and `_StopLoop` intact) — that is the designed
  intermediate state, so a partial Phase 3 landing is safe.

## Acceptance checklist (the whole plan is done when…)

- [ ] simplebroker 4.4.0 and simplebroker-redis 2.3.0 are on PyPI; CHANGELOG and README
  document the sidecar API; all CI workflows green.
- [ ] `tests/test_sidecar.py` covers invariants I1–I5 against real databases;
  pg and redis extension tests cover the backend matrix (I6).
- [ ] weft pins `simplebroker>=4.4.0`; `grep -rn "simplebroker\._" weft/ --include="*.py"`
  is empty; the firewall test pins the absence of `_runner` reaches; weft suite green.
- [ ] The only remaining known private-pattern reaches in weft are the two
  `getattr(broker, "_retrieve", None)` sites in `weft/core/monitor/policies/`
  (documented out of scope, future API discussion).

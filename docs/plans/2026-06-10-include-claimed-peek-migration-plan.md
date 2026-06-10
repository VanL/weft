# Include-Claimed Peek Surface — Implementation Plan

Status: completed
Source specs: none — SimpleBroker is README-governed; in weft, behavior specs are unaffected (cleanup-policy mechanics only) and storage-access mechanics are not spec-governed
Superseded by: none

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `include_claimed: bool = False` to SimpleBroker's public peek surface
(Python API **and** CLI), implement it natively on the Redis backend, publish, and then
delete weft's last private-API reach (`getattr(broker, "_retrieve", None)`) — with an
architecture test that keeps all private reaches gone for good.

**Architecture:** One boolean, threaded end-to-end. `BrokerCore.peek_one/peek_many/
peek_generator` pass `require_unclaimed=not include_claimed` into the existing private
`_retrieve` engine (which already supports it — weft has been using it for months
through the private reach). `Queue` mirrors the keyword on `peek`/`peek_one`/
`peek_many`/`peek_generator`; the `BrokerConnection` protocol gains it; the Redis
backend implements it by merging its per-queue `pending` and `claimed` ZSETs; Postgres
needs zero code (it shares `BrokerCore`, and its SQL layer already honors the flag).
The CLI mirrors the API: `broker peek --include-claimed`. After the publish gate
(simplebroker 4.6.0 + simplebroker-redis 2.4.0, batch-released), weft's two policy
helpers switch to public `peek_one`/`peek_many` calls.

**Tech Stack:** Python ≥3.11, `uv` for everything, pytest (+xdist), ruff, strict mypy.
Repos: `/Users/van/Developer/simplebroker` and `/Users/van/Developer/weft` (sibling
checkouts; assumed throughout). Work happens directly on `main` in both repos (repo
owner's standing instruction — no branches, no worktrees).

---

## Part I — Orientation (read this first)

### What you are touching and why

SimpleBroker is a message queue (SQLite core, Postgres/Redis extension backends in
`extensions/`). Consuming a message **claims** it (`claimed = 1` on SQLite/Postgres; a
move to the `claimed` ZSET on Redis); the row physically lingers until vacuum removes
it. The public peek API silently filters claimed rows out.

Weft (the downstream task orchestrator) runs garbage-collection policies over its
task-log queue that must *see* claimed rows — they are precisely what it collates and
deletes with exact-ID proofs. Today it reaches into the private retrieval engine:

- `weft/core/monitor/policies/task_log.py` — `peek_rows_including_claimed()` calls
  `getattr(broker, "_retrieve", None)` with `operation="peek", require_unclaimed=False`.
- `weft/core/monitor/policies/dead_task.py` —
  `_task_log_row_for_message_id_including_claimed()` does the same with
  `exact_timestamp=<message id>, limit=1`.

The asymmetry that makes this an API bug rather than a weft quirk: the public
`find_message_ids(...)` **already accepts `include_claimed=True`** (dead_task.py uses
it!), so callers can publicly *find* claimed rows but cannot publicly *read* them.
Both weft reaches exist to close that half-gap. Additionally, `RedisBrokerCore` has no
`_retrieve` at all, so both weft policies hard-fail on the Redis backend today even
though Redis's internals model the capability.

**The mirror rule (repo owner's standing policy):** the CLI and the client API surfaces
mirror each other unless there is a good reason to make something API-only. The API
gains `include_claimed` on the peek family, therefore the CLI gains
`broker peek --include-claimed`. Nothing else gains it (see non-goals).

### Vocabulary

| Term | Meaning |
|---|---|
| **`_retrieve`** | `BrokerCore`'s unified private retrieval engine (simplebroker/db.py:1462): one method behind every public read, parameterized by `operation` ("peek"/"claim"/"move"), `limit`, `offset`, `exact_timestamp`, `after_timestamp`, `before_timestamp`, `commit_before_yield`, and `require_unclaimed`. The public wrappers expose curated subsets. This plan exposes exactly one more knob — on the peek path only. |
| **claimed row** | A consumed-but-not-yet-vacuumed message. Reading one is *not* delivery state: vacuum may remove it at any moment. SQLite/Postgres: `claimed = 1` column. Redis: member of the per-queue `claimed` ZSET (`_qkey(queue, "claimed")`), body still in the shared `bodies` hash. |
| **`BrokerConnection`** | The typing `Protocol` in `simplebroker/_backend_plugins.py:215` that broker handles satisfy. `BrokerCore` (SQLite + Postgres) and `RedisBrokerCore` both implement it. Protocol changes are mypy-time contracts; Redis must be updated in the same release. |
| **`Queue`** | The user-facing handle (`simplebroker/sbqueue.py`). Peek family: `peek()` (high-level CLI-mirroring method, sbqueue.py:531), `peek_one` (:600), `peek_many` (:624), `peek_generator` (:657) — all thin delegates through `get_connection()`. |
| **conftest auto-markers** | simplebroker's `tests/conftest.py` auto-marks test modules: modules that drive the real CLI via `run_cli()` become `shared` (they run under the SQLite, Postgres, and Redis suite runs); Python-API-only modules become `sqlite_only`. Use this deliberately: the CLI test module in this plan is the free cross-backend conformance test. |

### Repository map (files this plan touches)

**simplebroker** (Phase 1):

| Path | Role |
|---|---|
| `simplebroker/db.py` | `include_claimed` on `BrokerCore.peek_one` (:1686), `peek_many` (:1721), `peek_generator` (:1765). |
| `simplebroker/sbqueue.py` | Same keyword on `Queue.peek` (:531), `peek_one` (:600), `peek_many` (:624), `peek_generator` (:657). |
| `simplebroker/_backend_plugins.py` | Keyword added to the protocol's `peek_one` (:269), `peek_many` (:277), `peek_generator` (:287). |
| `simplebroker/commands.py` | `cmd_peek` (:497) threads the flag via `functools.partial` into `_process_queue_fetch` (:342). |
| `simplebroker/cli.py` | `--include-claimed` on the peek subparser (peek parser built at cli.py:220 via shared `add_read_peek_args`, cli.py:87 — the new flag is added on `peek_parser` directly, NOT in the shared helper); dispatch at cli.py:1058. |
| `extensions/simplebroker_redis/simplebroker_redis/core.py` | Real implementation: `_peek_rows` (:347) learns to merge the `pending` + `claimed` ZSETs; `peek_one` (:688), the `peek_many` overload stack (:702–:751), `peek_generator` (:754) thread the keyword. |
| `extensions/simplebroker_redis/pyproject.toml` | version `2.3.1` → `2.4.0` (line 7); pin `simplebroker>=4.5.0` → `>=4.6.0` (line 17). |
| `pyproject.toml` / `simplebroker/_constants.py` | core version `4.5.0` → `4.6.0` (pyproject line 7; `_constants.py` line 38 — release tooling verifies they match). |
| `tests/test_peek_include_claimed.py` | **New.** Python-API behavior tests (auto-`sqlite_only`). |
| `tests/test_cli_peek_include_claimed.py` | **New.** CLI tests via `run_cli` (auto-`shared` → run on all three backends). |
| `extensions/simplebroker_pg/tests/test_pg_include_claimed.py` | **New.** Postgres conformance (no pg code change — `BrokerCore` is shared and `simplebroker_pg/_sql.py:221,227` already honors `require_unclaimed`). |
| `extensions/simplebroker_redis/tests/test_redis_include_claimed.py` | **New.** Redis merge-semantics tests. |
| `README.md`, `CHANGELOG.md` | Docs (CHANGELOG anchor: `## [4.5.0] - 2026-06-10` is at CHANGELOG.md:8). |

**weft** (Phase 3):

| Path | Role |
|---|---|
| `weft/core/monitor/policies/task_log.py` | `peek_rows_including_claimed()` body → public `broker.peek_many(..., include_claimed=True)`. **Keep the function name and signature**: it is re-exported by `weft/core/monitor/cleanup.py:35`, dependency-injected (`peek_rows_including_claimed_fn`, task_log.py:95/122/327–337), and monkeypatched by `tests/core/test_task_monitor_cleanup.py:443`. |
| `weft/core/monitor/policies/dead_task.py` | `_task_log_row_for_message_id_including_claimed()` body → public `broker.peek_one(..., exact_timestamp=..., include_claimed=True)`. |
| `tests/architecture/test_import_boundaries.py` | **Extend** with a repo-wide "no private simplebroker reach" guard (imports *and* getattr-string reaches). |
| `pyproject.toml` | Pin `simplebroker>=4.5.0` → `>=4.6.0` (line 36). |
| `CHANGELOG.md`, `docs/plans/` | Entry + plan copy. **Weft enforces plan hygiene via `tests/specs/test_plan_metadata.py`**: the plan copy must carry the 3-line `Status:` / `Source specs:` / `Superseded by:` metadata block immediately after its `# ` title and get a row (with matching status) in `docs/plans/README.md`'s Index table. Also bump the README count line ("There are currently 140 plan files" → 141) — accuracy, not test-enforced. This plan's header already carries the block — keep it intact when copying. |

### Toolset crash course

```bash
# simplebroker repo (run from repo root; uv manages everything):
uv run pytest                              # default suite, ~30s; baseline "1218 passed, 11 skipped"
uv run pytest tests/test_peek_include_claimed.py -v
uv run ruff check . && uv run ruff format --check .
uv run mypy simplebroker                   # strict
uv run bin/pytest-pg                       # Postgres suites (auto-Docker)
uv run bin/pytest-redis                    # Redis suites (auto-Docker)

# weft repo:
uv run pytest                              # ~2 min; baseline "1906 passed, 2 skipped"
uv run ruff check weft
uv run mypy weft
```

Style (both repos): ruff line length 88, full annotations (strict mypy), double quotes,
`from __future__ import annotations` in new modules, isort-clean imports (ruff
enforces). Commit messages: plain imperative subjects, no `feat:`/`fix:` prefixes.
Run `uv run ruff format <changed files>` before each commit — CI checks formatting.

**Execution lessons baked in from the sidecar plan run (do not relearn these):**

1. **mypy ordering:** a typed caller of a protocol method fails mypy until the protocol
   has the member. Land protocol-signature changes in the *same commit* as the first
   caller that needs them (here: Task 3 changes `db.py`, `sbqueue.py`, and
   `_backend_plugins.py` together).
2. **Pipe exit codes:** `cmd | tail -1` reports tail's status. Use `set -o pipefail` in
   every multi-command gate so failures aren't masked.
3. **macOS sed:** BSD sed has no `\b`. Prefer exact-string Python replacements with
   `assert src.count(old) == 1` over sed for code edits.
4. **Redis `peek_many` is an `@overload` stack** (three overloads + implementation).
   All four signatures must gain the keyword or mypy fails.
5. **Weft plan-metadata tests** will fail your whole suite if the plan copy is missing
   its metadata block or README index row (see repository map above).

### Test design rules (hard requirement — read twice)

1. **No mocks of broker internals, ever.** Every behavior test runs against a real
   database: `tmp_path` + the public API for SQLite; the `pg_core` fixture
   (extensions/simplebroker_pg/tests/conftest.py — yields a real `BrokerCore` on a
   throwaway schema, skips without a DSN; `bin/pytest-pg` provides one) for Postgres;
   the `redis_runner` fixture for Redis. Assert observable outcomes (rows returned,
   ordering, exit codes), never "method X was called".
2. **Creating a claimed row is trivial and real:** `q.write("m")` then `q.read()` —
   the read claims it; the row lingers until vacuum. Auto-vacuum triggers on a
   write-count interval, so tests doing a handful of writes will never race it. One
   test (Task 2) deliberately runs `vacuum()` to pin the disappearance semantics.
3. **Marker discipline:** do not add markers by hand in `tests/`. The new Python-API
   module auto-marks `sqlite_only`; the new CLI module uses `run_cli` and auto-marks
   `shared` — which is the point: it runs under `bin/pytest-pg` and `bin/pytest-redis`
   too, making it the cross-backend CLI conformance suite for free.
4. Red → green → commit for every behavior change. Run the failing test and confirm it
   fails *for the stated reason* before implementing.

### Design reference (locked — do not redesign during implementation)

The new surface, end to end:

```python
q = Queue("jobs", db_path="app.db")

q.peek_many(10)                            # unchanged: pending rows only
q.peek_many(10, include_claimed=True)      # pending + claimed, ordered by message ID
q.peek_one(exact_timestamp=mid, include_claimed=True)   # finds the row even if claimed
for row in q.peek_generator(include_claimed=True): ...
```

```bash
broker peek myqueue --all                    # unchanged
broker peek myqueue --all --include-claimed  # the same rows plus claimed ones
broker peek myqueue -m 1837...024 --include-claimed --json
```

**Naming:** `include_claimed`, defaulting to `False`. Positive-sense, matching the
existing public `find_message_ids(..., include_claimed=...)` parameter. The private
engine keeps its `require_unclaimed` spelling; the public wrappers translate
(`require_unclaimed=not include_claimed`). Do not export the double negative.

**Semantics (document everywhere the flag appears):** claimed rows are
deletion-pending. They may be removed by vacuum between any two calls; seeing one says
nothing about delivery state. `include_claimed=True` peeks return the union of pending
and claimed rows in message-ID order; peeking never changes claim state.

**Invariants (each maps to tests):**

| # | Invariant | Test home |
|---|---|---|
| I1 | Default behavior is byte-identical: `include_claimed=False` paths produce exactly today's results (the whole existing suite is the regression net, plus explicit default-vs-flag pairs). | Task 2, all suites |
| I2 | `include_claimed=True` returns a superset: every pending row plus every lingering claimed row, strictly ordered by message ID, with `limit`/`after`/`before` applied to the merged stream. | Tasks 2, 6, 7 |
| I3 | Exact-ID peek (`exact_timestamp=`/`-m`) finds a claimed row with the flag and returns `None`/exit 2 without it. | Tasks 2, 5 |
| I4 | Peeking with the flag mutates nothing: claim state, stats, and subsequent reads are unaffected. | Task 2 |
| I5 | All three backends conform (SQLite, Postgres, Redis) — same observable contract. | Tasks 5 (shared CLI), 6, 7 |
| I6 | CLI mirrors the API: `--include-claimed` composes with `--all`, `-m`, `--since`-style bounds, `--json`, `-t`; exit codes unchanged (0 rows → 2). | Task 5 |

**Deliberate non-goals (YAGNI — do NOT add these even if they feel symmetric):**

- **No flag on `read`/`claim_*`/`move_*` (API or CLI).** Claiming an already-claimed
  row is redelivery; that is a delivery-semantics feature with its own design burden,
  not a visibility flag. (The one internal exception already exists and stays
  internal: move-by-exact-ID passes `require_unclaimed=False` at commands.py:767.)
- **No `--claimed-only` filter.** Callers who want only-claimed can subtract; weft
  already does.
- **No exposure of `_retrieve`'s other private knobs** (`offset`,
  `commit_before_yield`) and no public `_retrieve`.
- **No `broker watch --include-claimed`.** Watch is a consumption loop; claimed
  visibility is an inspection concern.
- **No Postgres extension code or release.** `BrokerCore` is shared and pg's SQL
  already branches on `spec.require_unclaimed` (simplebroker_pg/_sql.py:221, 227).
  pg stays at 2.2.1; the batch release will skip it.
- **No new weft public surface.** The two weft helpers keep their names and
  signatures; only their bodies change.

---

## Phase 0 — Setup and baseline

### Task 0: Baseline on main

- [ ] **Step 0.1: Read the context** (30 minutes, in this order):
  `simplebroker/db.py:1462–1530` (`_retrieve` — the engine you are exposing one knob
  of), `db.py:1686–1830` (the three peek wrappers), `sbqueue.py:531–700` (the Queue
  peek family), `_backend_plugins.py:269–300` (protocol peeks),
  `commands.py:342–545` (`_process_queue_fetch` + `cmd_peek`), `cli.py:87–110` and
  `:220–222` (`add_read_peek_args` + peek parser), and on the Redis side
  `extensions/simplebroker_redis/simplebroker_redis/core.py:300–360` (`_zrange_pending`
  + `_peek_rows`) and `:688–782` (peek implementations).
- [ ] **Step 0.2: Baseline gates.** All must be clean; if not, STOP and report.

```bash
cd /Users/van/Developer/simplebroker
git pull --ff-only
set -o pipefail
uv run pytest 2>&1 | tail -1          # expect ~"1218 passed, 11 skipped"
uv run ruff check . && uv run ruff format --check . 2>&1 | tail -1
uv run mypy simplebroker 2>&1 | tail -1   # "Success: no issues found in 35 source files"
```

---

## Phase 1 — simplebroker: API, Redis, CLI, docs

### Task 1: Failing tests for the core API (RED)

**Files:**
- Create: `tests/test_peek_include_claimed.py`

- [ ] **Step 1.1: Create the test module** (full contents — note: no markers; the
  conftest auto-marks this `sqlite_only` because it never calls `run_cli`):

```python
"""Behavior tests for include_claimed on the public peek surface.

Claimed rows are consumed-but-not-vacuumed messages. These tests create them
the real way (write + read) against real SQLite databases under tmp_path.
No mocks: assert returned rows, ordering, and state — never internal calls.
"""

from __future__ import annotations

from pathlib import Path

from simplebroker import Queue


def _db(tmp_path: Path) -> str:
    return str(tmp_path / "broker.db")


def _seed(q: Queue, n: int) -> list[int]:
    """Write n messages m0..m{n-1}; return their message IDs in write order."""
    ids: list[int] = []
    for i in range(n):
        q.write(f"m{i}")
    rows = q.peek_many(n, with_timestamps=True)
    assert len(rows) == n
    ids = [ts for _body, ts in rows]
    return ids


def test_default_peek_excludes_claimed(tmp_path: Path) -> None:
    q = Queue("jobs", db_path=_db(tmp_path))
    _seed(q, 3)
    assert q.read() == "m0"  # claims m0; the row lingers until vacuum

    bodies = q.peek_many(10)
    assert bodies == ["m1", "m2"]  # byte-identical to pre-flag behavior


def test_include_claimed_returns_superset_in_id_order(tmp_path: Path) -> None:
    q = Queue("jobs", db_path=_db(tmp_path))
    ids = _seed(q, 4)
    assert q.read() == "m0"
    assert q.read() == "m1"  # two claimed, two pending

    rows = q.peek_many(10, with_timestamps=True, include_claimed=True)
    assert [body for body, _ in rows] == ["m0", "m1", "m2", "m3"]
    assert [ts for _, ts in rows] == ids  # strict message-ID order


def test_limit_and_bounds_apply_to_merged_stream(tmp_path: Path) -> None:
    q = Queue("jobs", db_path=_db(tmp_path))
    ids = _seed(q, 4)
    assert q.read() == "m0"

    # limit counts claimed rows too
    rows = q.peek_many(2, with_timestamps=True, include_claimed=True)
    assert [body for body, _ in rows] == ["m0", "m1"]

    # after_timestamp applies to the merged stream
    rows = q.peek_many(
        10, with_timestamps=True, include_claimed=True, after_timestamp=ids[0]
    )
    assert [body for body, _ in rows] == ["m1", "m2", "m3"]


def test_exact_id_peek_finds_claimed_row_only_with_flag(tmp_path: Path) -> None:
    q = Queue("jobs", db_path=_db(tmp_path))
    ids = _seed(q, 2)
    assert q.read() == "m0"

    assert q.peek_one(exact_timestamp=ids[0]) is None
    assert (
        q.peek_one(exact_timestamp=ids[0], include_claimed=True) == "m0"
    )


def test_generator_paginates_across_claimed_boundary(tmp_path: Path) -> None:
    q = Queue("jobs", db_path=_db(tmp_path))
    _seed(q, 5)
    assert q.read() == "m0"
    assert q.read() == "m1"

    # Queue.peek_generator has no batch_size knob (pre-existing asymmetry vs
    # BrokerCore — do NOT add one; out of scope). Drive the connection-level
    # generator where batch_size=1 forces offset pagination through the
    # merged stream, then confirm the Queue-level generator agrees.
    with q.get_connection() as conn:
        bodies = list(
            conn.peek_generator(
                "jobs", batch_size=1, with_timestamps=False, include_claimed=True
            )
        )
    assert bodies == ["m0", "m1", "m2", "m3", "m4"]
    assert list(q.peek_generator(include_claimed=True)) == bodies
    assert list(q.peek_generator()) == ["m2", "m3", "m4"]


def test_peeking_claimed_rows_mutates_nothing(tmp_path: Path) -> None:
    q = Queue("jobs", db_path=_db(tmp_path))
    _seed(q, 3)
    assert q.read() == "m0"

    before = q.stats()
    q.peek_many(10, include_claimed=True)
    q.peek_one(include_claimed=True)
    after = q.stats()
    assert (before.pending, before.claimed) == (after.pending, after.claimed) == (2, 1)
    assert q.read() == "m1"  # delivery order untouched


def test_vacuum_removes_claimed_rows_from_flagged_peeks(tmp_path: Path) -> None:
    """Pins the documented race: claimed rows are deletion-pending."""
    q = Queue("jobs", db_path=_db(tmp_path))
    _seed(q, 2)
    assert q.read() == "m0"

    with q.get_connection() as conn:
        conn.vacuum()

    assert q.peek_many(10, include_claimed=True) == ["m1"]


def test_queue_peek_high_level_mirrors_flag(tmp_path: Path) -> None:
    q = Queue("jobs", db_path=_db(tmp_path))
    _seed(q, 2)
    assert q.read() == "m0"

    assert q.peek() == "m1"
    assert q.peek(include_claimed=True) == "m0"
    all_rows = q.peek(all_messages=True, include_claimed=True)
    assert list(all_rows) == ["m0", "m1"]  # type: ignore[arg-type]
```

- [ ] **Step 1.2: Run; verify RED for the right reason.**

```bash
uv run pytest tests/test_peek_include_claimed.py -q 2>&1 | tail -3
```

Expected: every test except `test_default_peek_excludes_claimed` FAILS with
`TypeError: ... got an unexpected keyword argument 'include_claimed'`. The default
test PASSES (it exercises today's behavior — that is intentional: it is the I1 pin).

### Task 2: Implement the core API (GREEN)

**Files:**
- Modify: `simplebroker/db.py` (three methods), `simplebroker/sbqueue.py` (four
  methods), `simplebroker/_backend_plugins.py` (three protocol members)

All three files land in **one commit** (execution lesson #1: the typed `Queue`
delegates fail mypy until the protocol has the keyword).

- [ ] **Step 2.1: `BrokerCore` (db.py).** In each of the three methods, add the
  keyword and thread it. The full diffs:

  `peek_one` (db.py:1686) — signature gains `include_claimed: bool = False` after
  `exact_timestamp`; the `_retrieve` call becomes:

```python
        results = self._retrieve(
            queue,
            operation="peek",
            limit=1,
            exact_timestamp=exact_timestamp,
            require_unclaimed=not include_claimed,
        )
```

  `peek_many` (db.py:1721) — same keyword in the signature; the `_retrieve` call gains
  `require_unclaimed=not include_claimed,`.

  `peek_generator` (db.py:1765) — same keyword in the signature; the in-loop
  `_retrieve` call gains `require_unclaimed=not include_claimed,`.

  Extend each docstring's Args with:

```python
            include_claimed: If True, also return claimed (consumed but not
                yet vacuumed) messages, merged in message-ID order. Claimed
                rows are deletion-pending: vacuum may remove them at any
                time, and seeing one says nothing about delivery state.
                Peeking never changes claim state.
```

- [ ] **Step 2.2: `Queue` (sbqueue.py).** Add `include_claimed: bool = False` to
  `peek_one` (:600), `peek_many` (:624), `peek_generator` (:657) and pass
  `include_claimed=include_claimed` through their existing
  `connection.peek_*(...)` calls. Then `peek` (:531): add the keyword to its
  signature and thread it into all **four** internal delegation calls in its body
  (verified at sbqueue.py:570–596): `self.peek_one(exact_timestamp=message_id, ...)`
  in the message-id branch, `self.peek_generator(...)` in the `all_messages` branch,
  `self.peek_generator(...)` in the range-filtered single-read branch, and
  `self.peek_one(...)` in the plain branch. There are no other read paths in it and
  no `peek_many` call. Docstring addition: same text as Step 2.1.

- [ ] **Step 2.3: Protocol (`_backend_plugins.py`).** Add
  `include_claimed: bool = False,` as the final keyword in the protocol's `peek_one`
  (:269), `peek_many` (:277), and `peek_generator` (:287) members.

- [ ] **Step 2.4: Run; verify GREEN, then the full suite and gates.**

```bash
set -o pipefail
uv run pytest tests/test_peek_include_claimed.py -q 2>&1 | tail -1   # 8 passed
uv run pytest 2>&1 | tail -1                                          # no regressions
uv run ruff format simplebroker/db.py simplebroker/sbqueue.py simplebroker/_backend_plugins.py
uv run ruff check . && uv run mypy simplebroker
```

  Note: the redis extension's mypy stays green between Tasks 2 and 4 — nothing in the
  extension is statically annotated as `BrokerConnection`, so the protocol change is
  not type-checked against `RedisBrokerCore`. The signal that Redis lags the contract
  is Task 3's runtime `TypeError`. The one-commit rule in this task exists for the
  **core**: `Queue`'s typed `connection.peek_*` calls fail `uv run mypy simplebroker`
  unless the protocol member lands with them.

- [ ] **Step 2.5: Commit.**

```bash
git add simplebroker/db.py simplebroker/sbqueue.py simplebroker/_backend_plugins.py \
        tests/test_peek_include_claimed.py
git commit -m "Add include_claimed to the public peek surface"
```

### Task 3: Redis tests (RED)

**Files:**
- Create: `extensions/simplebroker_redis/tests/test_redis_include_claimed.py`

- [ ] **Step 3.1: Write the failing tests** (full contents; `redis_runner` is the
  existing conftest fixture; `Queue(..., runner=..., persistent=True)` +
  `queue.close()` copies `test_redis_integration.py`):

```python
"""include_claimed peek semantics on the Redis backend.

Redis keeps claimed rows in a per-queue "claimed" ZSET parallel to "pending"
(bodies shared). include_claimed=True must merge both, ordered by message ID.
"""

from __future__ import annotations

import pytest
from simplebroker_redis import RedisRunner

from simplebroker import Queue

pytestmark = [pytest.mark.redis_only]


def _seeded_queue(redis_runner: RedisRunner) -> tuple[Queue, list[int]]:
    queue = Queue("jobs", runner=redis_runner, persistent=True)
    for i in range(4):
        queue.write(f"m{i}")
    rows = queue.peek_many(4, with_timestamps=True)
    ids = [ts for _body, ts in rows]
    return queue, ids


def test_default_peek_excludes_claimed(redis_runner: RedisRunner) -> None:
    queue, _ids = _seeded_queue(redis_runner)
    try:
        assert queue.read() == "m0"
        assert queue.peek_many(10) == ["m1", "m2", "m3"]
    finally:
        queue.close()


def test_include_claimed_merges_zsets_in_id_order(
    redis_runner: RedisRunner,
) -> None:
    queue, ids = _seeded_queue(redis_runner)
    try:
        assert queue.read() == "m0"
        assert queue.read() == "m1"
        rows = queue.peek_many(10, with_timestamps=True, include_claimed=True)
        assert [body for body, _ in rows] == ["m0", "m1", "m2", "m3"]
        assert [ts for _, ts in rows] == ids
        # limit and bounds apply to the merged stream
        assert queue.peek_many(2, include_claimed=True) == ["m0", "m1"]
        rows = queue.peek_many(
            10, with_timestamps=True, include_claimed=True, after_timestamp=ids[1]
        )
        assert [body for body, _ in rows] == ["m2", "m3"]
    finally:
        queue.close()


def test_exact_id_and_generator(redis_runner: RedisRunner) -> None:
    queue, ids = _seeded_queue(redis_runner)
    try:
        assert queue.read() == "m0"
        assert queue.peek_one(exact_timestamp=ids[0]) is None
        assert queue.peek_one(exact_timestamp=ids[0], include_claimed=True) == "m0"
        bodies = list(queue.peek_generator(batch_size=1, include_claimed=True))
        assert bodies == ["m0", "m1", "m2", "m3"]
    finally:
        queue.close()
```

- [ ] **Step 3.2: Run; verify RED.**

```bash
uv run bin/pytest-redis
```

Expected: ALL THREE tests FAIL with `TypeError: ... unexpected keyword argument
'include_claimed'` — including the default-behavior test, because Task 2's `Queue`
delegates now pass the keyword unconditionally and `RedisBrokerCore.peek_*` does not
accept it yet. (This is why core and redis must ship in one release: between Tasks 2
and 4 the local redis backend is broken at the Queue level. Execution finding,
2026-06-10.) Tip: `bin/pytest-redis` accepts path passthrough,
so the fast cycle is:

```bash
uv run bin/pytest-redis extensions/simplebroker_redis/tests/test_redis_include_claimed.py
```

### Task 4: Redis implementation (GREEN) + version/pin bump

**Files:**
- Modify: `extensions/simplebroker_redis/simplebroker_redis/core.py`
- Modify: `extensions/simplebroker_redis/pyproject.toml`

- [ ] **Step 4.1: Read first** (engineering principle: read, then change):
  `_zrange_pending` (core.py, directly above `_peek_rows` at :347) — note how it
  builds ID bounds (`exact_bound`/`min_bound`/`max_bound` from `.keys`) and slices
  with `limit`/`offset` — and the key accessors (`self._qkey(queue, "pending")` /
  `"claimed"`; also `self._keys.claimed(queue)` style selectors near core.py:110).

- [ ] **Step 4.2: Implement the merge in `_peek_rows`.** Contract (the tests above are
  the acceptance criteria; the code shape below is the recommended implementation —
  adapt names to what you found in Step 4.1, but do NOT change the contract):

  1. `_peek_rows` gains `include_claimed: bool = False`.
  2. When `False`: behavior byte-identical to today (pending ZSET only).
  3. When `True`: fetch candidate IDs from **both** the pending and claimed ZSETs
     using the same bound logic, merge, de-duplicate, sort ascending by decoded
     message ID, then apply `offset`/`limit` to the **merged** ordered list, then
     fetch bodies via the existing `hmget` on the shared `bodies` hash.
  4. The simplest correct shape: factor the existing ZSET query so it can target
     either state (e.g. give `_zrange_pending` a `state: str = "pending"` parameter
     or add a `_zrange_state` helper it delegates to), call it twice **without**
     `limit`/`offset` applied per-ZSET when merging (bounds may be applied per-ZSET —
     they are monotone in ID — but limit/offset must apply post-merge; passing
     per-ZSET `limit=limit + offset` is a valid optimization, plain unlimited fetch
     bounded by the ID filters is the simplest correct version — pick simple).
  5. Rows whose body is missing from the `bodies` hash are skipped (the existing
     `if body is not None` guard) — keep that for both states.

- [ ] **Step 4.3: Thread the keyword through the public Redis methods.**
  `peek_one` (:688): add `include_claimed: bool = False`, pass to `_peek_rows`.
  `peek_many`: add the keyword to **all three `@overload` signatures and the
  implementation** (:702, :713, :724, :734) — execution lesson #4. `peek_generator`
  (:754): add and pass through its loop's `_peek_rows` call.

- [ ] **Step 4.4: Bump version and floor.** `extensions/simplebroker_redis/pyproject.toml`:
  line 7 `version = "2.3.1"` → `"2.4.0"`; line 17 `"simplebroker>=4.5.0"` →
  `"simplebroker>=4.6.0"` (the protocol it implements gained a member-keyword in
  4.6.0). Do not run `uv lock` in the extension dir — release tooling refreshes
  lockfiles; locally both repos resolve simplebroker by editable path.

- [ ] **Step 4.5: Run; verify GREEN.**

```bash
set -o pipefail
uv run bin/pytest-redis 2>&1 | tail -2
uv run ruff format extensions/simplebroker_redis/simplebroker_redis/core.py
uv run ruff check extensions/simplebroker_redis
uv run mypy extensions/simplebroker_redis/simplebroker_redis extensions/simplebroker_redis/tests
```

- [ ] **Step 4.6: Commit.**

```bash
git add extensions/simplebroker_redis
git commit -m "Implement include_claimed peeks on the Redis backend"
```

### Task 5: CLI surface — `broker peek --include-claimed`

**Files:**
- Modify: `simplebroker/cli.py`, `simplebroker/commands.py`
- Create: `tests/test_cli_peek_include_claimed.py`

- [ ] **Step 5.1: Write the failing CLI tests.** This module uses `run_cli`, so the
  conftest auto-marks it `shared` — it will run under the SQLite, Postgres, AND Redis
  suite runs, which is the intended cross-backend conformance. Harness facts
  (verified against tests/conftest.py:647 and :717, and tests/test_json_output.py):
  `run_cli(*args, cwd: Path, ...) -> (return_code, stdout, stderr)`; import it with
  `from .conftest import run_cli`; `workdir` is a fixture injected as a plain
  argument; JSON output rows carry `"message"` and `"timestamp"` fields. Full test
  content:

```python
"""CLI conformance for broker peek --include-claimed (runs on all backends)."""

from __future__ import annotations

import json
from pathlib import Path

from .conftest import run_cli


def _seed_and_claim_one(workdir: Path) -> str:
    """Write two messages, read (claim) the first, return its message ID."""
    assert run_cli("write", "jobs", "m0", cwd=workdir)[0] == 0
    assert run_cli("write", "jobs", "m1", cwd=workdir)[0] == 0
    code, out, _err = run_cli("peek", "jobs", "--json", cwd=workdir)
    assert code == 0
    first_id = str(json.loads(out.splitlines()[0])["timestamp"])
    code, out, _err = run_cli("read", "jobs", cwd=workdir)
    assert code == 0 and out.strip() == "m0"
    return first_id


def test_peek_all_with_and_without_flag(workdir: Path) -> None:
    _seed_and_claim_one(workdir)

    code, out, _err = run_cli("peek", "jobs", "--all", cwd=workdir)
    assert code == 0
    assert out.strip().splitlines() == ["m1"]

    code, out, _err = run_cli("peek", "jobs", "--all", "--include-claimed", cwd=workdir)
    assert code == 0
    assert out.strip().splitlines() == ["m0", "m1"]


def test_peek_exact_id_claimed_row(workdir: Path) -> None:
    first_id = _seed_and_claim_one(workdir)

    code, _out, _err = run_cli("peek", "jobs", "-m", first_id, cwd=workdir)
    assert code == 2  # claimed row invisible without the flag → queue-empty code

    code, out, _err = run_cli(
        "peek", "jobs", "-m", first_id, "--include-claimed", cwd=workdir
    )
    assert code == 0
    assert out.strip() == "m0"


def test_peek_only_claimed_queue_exit_codes(workdir: Path) -> None:
    assert run_cli("write", "jobs", "m0", cwd=workdir)[0] == 0
    assert run_cli("read", "jobs", cwd=workdir)[0] == 0  # queue now only-claimed

    assert run_cli("peek", "jobs", cwd=workdir)[0] == 2
    code, out, _err = run_cli("peek", "jobs", "--include-claimed", "--json", cwd=workdir)
    assert code == 0
    assert json.loads(out.splitlines()[0])["message"] == "m0"


def test_read_does_not_accept_the_flag(workdir: Path) -> None:
    """The mirror is peek-only by design: read must reject it."""
    assert run_cli("write", "jobs", "m0", cwd=workdir)[0] == 0
    code, _out, err = run_cli("read", "jobs", "--include-claimed", cwd=workdir)
    assert code != 0
    assert "include-claimed" in err or "unrecognized" in err
```

  (The harness calls above already match the house idiom — see
  `tests/test_json_output.py` for the model this was written against.)

- [ ] **Step 5.2: Run; verify RED** (argparse rejects the unknown flag):

```bash
uv run pytest tests/test_cli_peek_include_claimed.py -q 2>&1 | tail -3
```

Expected: the first three FAIL because `--include-claimed` is unrecognized (exit code
!= 0 where 0 expected); `test_read_does_not_accept_the_flag` already PASSES (argparse
rejects the flag on `read` today, which is exactly the behavior that must survive the
change — it is this task's I1-style pin).

- [ ] **Step 5.3: Implement the flag.**

  1. `cli.py` — after `add_read_peek_args(peek_parser)` (cli.py:220–221) add, on the
     peek parser only (NOT inside `add_read_peek_args`, which read shares):

```python
    peek_parser.add_argument(
        "--include-claimed",
        action="store_true",
        help=(
            "also show claimed (consumed but not yet vacuumed) messages; "
            "claimed rows may disappear to vacuum at any time"
        ),
    )
```

  2. `cli.py` dispatch (the `elif args.command == "peek":` branch at cli.py:1058):
     pass `include_claimed=args.include_claimed` into the `commands.cmd_peek(...)`
     call.
  3. `commands.py` — `cmd_peek` (:497) gains `include_claimed: bool = False` in its
     signature (document it in the docstring Args like the others), and binds it
     into the fetchers via `functools.partial` so the shared
     `_process_queue_fetch` helper stays untouched (DRY):

```python
        return _process_queue_fetch(
            fetch_one=partial(queue.peek_one, include_claimed=include_claimed),
            fetch_generator=partial(
                queue.peek_generator, include_claimed=include_claimed
            ),
            ...
        )
```

     Add `from functools import partial` to commands.py's imports (it is not imported
     today — verified).

- [ ] **Step 5.4: Run; verify GREEN; full suite; commit.**

```bash
set -o pipefail
uv run pytest tests/test_cli_peek_include_claimed.py -q 2>&1 | tail -1
uv run pytest 2>&1 | tail -1
uv run ruff format simplebroker/cli.py simplebroker/commands.py
uv run ruff check . && uv run mypy simplebroker
git add simplebroker/cli.py simplebroker/commands.py tests/test_cli_peek_include_claimed.py
git commit -m "Mirror include_claimed on the peek CLI"
```

### Task 6: Postgres conformance test

**Files:**
- Create: `extensions/simplebroker_pg/tests/test_pg_include_claimed.py`

No pg code changes — this is green-from-birth conformance (plus the shared CLI module
from Task 5 already exercises pg through `bin/pytest-pg`).

- [ ] **Step 6.1: Write the test** (`pg_core` fixture: real `BrokerCore` on a
  throwaway schema; skips without `SIMPLEBROKER_PG_TEST_DSN`, which `bin/pytest-pg`
  provides):

```python
"""include_claimed peek conformance on the Postgres backend."""

from __future__ import annotations

import pytest

from simplebroker.db import BrokerCore

pytestmark = [pytest.mark.pg_only]


def test_include_claimed_superset_and_exact_id(pg_core: BrokerCore) -> None:
    for i in range(3):
        pg_core.write("jobs", f"m{i}")
    rows = pg_core.peek_many("jobs", 3, with_timestamps=True)
    ids = [ts for _body, ts in rows]
    assert pg_core.claim_one("jobs", with_timestamps=False) == "m0"

    assert pg_core.peek_many("jobs", 10, with_timestamps=False) == ["m1", "m2"]
    merged = pg_core.peek_many(
        "jobs", 10, with_timestamps=True, include_claimed=True
    )
    assert [body for body, _ in merged] == ["m0", "m1", "m2"]
    assert [ts for _, ts in merged] == ids

    assert pg_core.peek_one("jobs", exact_timestamp=ids[0]) is None
    assert (
        pg_core.peek_one(
            "jobs", exact_timestamp=ids[0], with_timestamps=False, include_claimed=True
        )
        == "m0"
    )
```

- [ ] **Step 6.2: Run; commit.**

```bash
uv run bin/pytest-pg 2>&1 | tail -2
git add extensions/simplebroker_pg/tests/test_pg_include_claimed.py
git commit -m "Cover include_claimed peeks on the Postgres backend"
```

### Task 7: Versions, CHANGELOG, README

**Files:**
- Modify: `pyproject.toml` (line 7), `simplebroker/_constants.py` (line 38),
  `CHANGELOG.md`, `README.md`

- [ ] **Step 7.1: Bump core version in BOTH files** (release tooling verifies they
  match): `4.5.0` → `4.6.0`. Then `uv lock` at the repo root (bookkeeping; editable
  paths satisfy the redis floor locally).

- [ ] **Step 7.2: CHANGELOG.** Insert above `## [4.5.0] - 2026-06-10`
  (CHANGELOG.md:8):

```markdown
## [4.6.0] - <today's date>
### Added
- Added `include_claimed` to the public peek surface — `Queue.peek/peek_one/
  peek_many/peek_generator`, the `BrokerConnection` protocol, and the CLI
  (`broker peek --include-claimed`). When set, peeks return claimed (consumed
  but not yet vacuumed) messages merged with pending ones in message-ID order.
  Claimed rows are deletion-pending; peeking never changes claim state. This
  completes the public claimed-row round trip started by
  `find_message_ids(include_claimed=...)`.

### simplebroker-redis 2.4.0
- Implemented `include_claimed` peeks by merging the per-queue pending and
  claimed ZSETs. Requires simplebroker>=4.6.0.
```

- [ ] **Step 7.3: README.** Two small additions:
  1. In the command-reference/peek documentation (find the peek command section:
     `grep -n '"peek"\|broker peek' README.md | head` — anchor near the existing
     example at README.md:388 `broker peek tasks --all --after ...`), add one example
     line and one sentence:

```markdown
$ broker peek tasks --all --include-claimed   # also show consumed rows not yet vacuumed
```

     plus, in the surrounding prose: "Claimed rows are deletion-pending — vacuum may
     remove them at any time; `--include-claimed` is an inspection tool, not delivery
     state."
  2. The README has **no** granular-peek Python-API section today (verified: zero
     hits for `peek_one`/`peek_many`/`peek_generator` in README.md) — do not hunt for
     one. Instead, in the `## Python API` section, directly after the paragraph that
     mentions `read_generator`/`move_generator` (README.md:701), add a short
     standalone snippet:

````markdown
Peeks can also inspect claimed (consumed but not yet vacuumed) messages:

```python
q.peek_many(10, include_claimed=True)   # pending + claimed, in message-ID order
```

Claimed rows are deletion-pending — vacuum may remove them at any time — so
`include_claimed` is an inspection tool, not delivery state.
````

- [ ] **Step 7.4: Commit.**

```bash
git add pyproject.toml simplebroker/_constants.py uv.lock CHANGELOG.md README.md
git commit -m "Bump to 4.6.0 and document include_claimed peeks"
```

### Task 8: Full Phase-1 gate

- [ ] **Step 8.1: Everything, with pipefail:**

```bash
set -o pipefail
uv run pytest 2>&1 | tail -1
uv run ruff check . && uv run ruff format --check . 2>&1 | tail -1
uv run mypy simplebroker bin/release.py \
  extensions/simplebroker_pg/simplebroker_pg \
  extensions/simplebroker_redis/simplebroker_redis \
  extensions/simplebroker_redis/tests
uv run bin/pytest-pg 2>&1 | tail -2
uv run bin/pytest-redis 2>&1 | tail -2
```

- [ ] **Step 8.2: Cross-repo smoke — current weft against unreleased 4.6.0** (weft
  still uses the `_retrieve` reach; the private engine is untouched, so it must keep
  working — this proves 4.6.0 is non-breaking before publish).

  **Policy (repo owner):** this `--with-editable` overlay is the *only* sanctioned
  use of the on-disk simplebroker checkout from weft — a one-shot, pre-publish gate
  smoke. Weft's normal resolution of simplebroker is always the published package
  (its `uv.lock` pins the PyPI registry; there is no `[tool.uv.sources]` mapping for
  simplebroker, and the `.envrc` sibling-PYTHONPATH block is deliberately disabled).
  Do not add path mappings, editable installs, or PYTHONPATH injection to make weft
  tests "see" local simplebroker changes — publish first, then bump the pin.

```bash
cd /Users/van/Developer/weft
uv run --with-editable /Users/van/Developer/simplebroker pytest \
  tests/core/test_monitor_store.py tests/tasks/test_task_monitor.py \
  tests/core/test_task_monitor_cleanup.py -q 2>&1 | tail -1
cd /Users/van/Developer/simplebroker
```

Expected: all pass. Any failure = STOP, fix in Phase 1.

- [ ] **Step 8.3: Push main; wait for CI green.**

```bash
git push origin main   # if rejected: git fetch && git rebase origin/main, re-run
                       # the quick gates, push again (dependabot lands frequently)
```

---

## Phase 2 — THE GATE: publish, then update weft's pin

Same codependency as the sidecar release: redis 2.4.0 pins core ≥4.6.0, and the core
release flow requires the in-tree redis version to be published (or in-batch). Use the
batch target. A lone `core` run aborts on the unpublished redis baseline; a lone
`redis` run is worse — it has no core-floor check (the extension resolves simplebroker
by editable path) and would happily publish a 2.4.0 that pins an unpublished core. Run
`all`, nothing else.

### Task 9: Batch release + verification

- [ ] **Step 9.1:** On green main:

```bash
cd /Users/van/Developer/simplebroker
git checkout main && git pull
uv run bin/release.py all
```

  (Discovers the pre-bumped unpublished versions — 4.6.0 and 2.4.0; pg 2.2.1 is
  already published so it is skipped. Requires a clean worktree. Runs the full
  precheck battery, makes one release commit, tags both packages, pushes.)

- [ ] **Step 9.2: Watch CI yourself** — the helper exits after printing "Next step:
  wait for <workflow>"; it does not poll and does not publish to PyPI.

- [ ] **Step 9.3: Hard gate — verify on PyPI:**

```bash
curl -s https://pypi.org/pypi/simplebroker/4.6.0/json | head -c 120; echo
curl -s https://pypi.org/pypi/simplebroker-redis/2.4.0/json | head -c 120; echo
```

Both must return JSON, not 404. If the tag workflow doesn't publish in your setup,
upload manually (`uv build && uv publish` per package), then re-run the curls.

### Task 10: Weft pin bump + STOP-rule

- [ ] **Step 10.1:** In weft, on main:

```bash
cd /Users/van/Developer/weft
git pull --ff-only
```

  Edit `pyproject.toml` line 36: `"simplebroker>=4.5.0"` → `"simplebroker>=4.6.0"`.
  Then:

```bash
uv lock && set -o pipefail && uv run pytest 2>&1 | tail -1
```

- [ ] **Step 10.2: STOP-rule.** Suite not green against published 4.6.0 → stop the
  plan and report; the fix is a simplebroker 4.6.1, not weft workarounds.
- [ ] **Step 10.3: Commit** (`git add pyproject.toml uv.lock && git commit -m
  "Require simplebroker 4.6.0"`).

---

## Phase 3 — weft: delete the last private reach

Weft conventions reminder (from its CLAUDE.md / agent-context): read
`docs/agent-context/decision-hierarchy.md` and `engineering-principles.md` if you have
not; plans live in weft's `docs/plans/` and are test-enforced (see below); do not
touch unrelated uncommitted WIP in the tree; commit only files you changed.

### Task 11: Plan copy (metadata-conformant)

- [ ] **Step 11.1:** Copy this plan to
  `/Users/van/Developer/weft/docs/plans/2026-06-10-include-claimed-peek-migration-plan.md`.
  The metadata block under the title is already present — set `Status: draft` →
  leave as `draft` until Task 14 lands, then flip to `completed` in the final docs
  commit. Add the index row to `docs/plans/README.md` (format:
  `| [`<filename>`](./<filename>) | Include-Claimed Peek Surface — Implementation Plan | `draft` | none |`,
  rows are date-descending) and bump the count line ("There are currently 140 plan
  files" → 141). Verify:

```bash
uv run pytest tests/specs/test_plan_metadata.py -q 2>&1 | tail -1
git add docs/plans/ && git commit -m "Import include_claimed migration plan"
```

### Task 12: The architecture guard (RED first)

**Files:**
- Modify: `tests/architecture/test_import_boundaries.py`

This is the lasting deliverable: after this migration, *nothing* in the `weft/`
package touches private simplebroker surface — and a test keeps it that way. (The
scan is deliberately scoped to the `weft/` package via the file's existing
`PACKAGE_ROOT`; verified: the needles have zero hits in `tests/`, `extensions/`, or
`integrations/` today.)

- [ ] **Step 12.1: Read the existing file** and match its style. Facts (verified):
  it is AST-based import-edge analysis with module constants `REPO_ROOT`
  (test_import_boundaries.py:11) and `PACKAGE_ROOT = REPO_ROOT / "weft"` (:12), and a
  module-level `pytestmark = [pytest.mark.shared]` your new test inherits. The new
  test deliberately uses a plain text scan rather than AST — `getattr(broker, "_…")`
  reaches live inside string literals and attribute expressions that import-edge
  analysis cannot see. Append:

```python
def test_no_private_simplebroker_reaches() -> None:
    """weft must use only public simplebroker surface.

    Guards both private-module imports (simplebroker._x) and dynamic
    attribute reaches (getattr(obj, "_retrieve") / obj._runner style) that
    the sidecar and include_claimed migrations eliminated.
    """
    offenders: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for needle in (
            "simplebroker._",
            'getattr(broker, "_',
            "broker._runner",
            "broker._retrieve",
        ):
            if needle in text:
                offenders.append(f"{path}: {needle}")
    assert offenders == []
```

- [ ] **Step 12.2: Run; verify RED for the right reason:**

```bash
uv run pytest tests/architecture/test_import_boundaries.py -q 2>&1 | tail -4
```

Expected: FAILS listing exactly two offenders — the `getattr(broker, "_` reaches in
`weft/core/monitor/policies/task_log.py` and `.../dead_task.py`. Anything else listed
is a discovery: investigate before proceeding.

### Task 13: Migrate the two policy helpers (GREEN)

**Files:**
- Modify: `weft/core/monitor/policies/task_log.py`, `weft/core/monitor/policies/dead_task.py`

**Keep both function names and signatures** — `peek_rows_including_claimed` is
re-exported (`cleanup.py:35`), dependency-injected (task_log.py:95/122/327–337), and
monkeypatched in `tests/core/test_task_monitor_cleanup.py:443`. Only the bodies
change.

- [ ] **Step 13.1: task_log.py.** Replace the body of `peek_rows_including_claimed`:

  Before (current):

```python
    retrieve = getattr(broker, "_retrieve", None)
    if not callable(retrieve):
        raise RuntimeError("broker client cannot retrieve claimed rows")
    raw_rows = retrieve(
        queue_name,
        operation="peek",
        limit=max(1, limit),
        require_unclaimed=False,
    )
```

  After:

```python
    raw_rows = broker.peek_many(
        queue_name,
        max(1, limit),
        with_timestamps=True,
        include_claimed=True,
    )
```

  The row-shaping loop below it (`QueueWindowRow(...) for body, message_id in
  raw_rows`) is unchanged — `peek_many(with_timestamps=True)` returns the same
  `[(body, id), ...]` shape `_retrieve` did. Update the docstring's "using the broker
  backend hook" wording to "using the public peek surface".

- [ ] **Step 13.2: dead_task.py.** Replace the body of
  `_task_log_row_for_message_id_including_claimed`:

  Before (current):

```python
    retrieve = getattr(broker, "_retrieve", None)
    if not callable(retrieve):
        raise RuntimeError("broker client cannot retrieve claimed task-log rows")
    raw_rows = retrieve(
        WEFT_GLOBAL_LOG_QUEUE,
        operation="peek",
        limit=1,
        exact_timestamp=message_id,
        require_unclaimed=False,
    )
    if not raw_rows:
        return None
    body, timestamp = raw_rows[0]
```

  After:

```python
    row = broker.peek_one(
        WEFT_GLOBAL_LOG_QUEUE,
        exact_timestamp=message_id,
        with_timestamps=True,
        include_claimed=True,
    )
    if row is None:
        return None
    body, timestamp = row
```

  (Note the shape change: `peek_one` returns one tuple-or-None, not a list.)

- [ ] **Step 13.3: Run; verify GREEN** — the architecture guard plus the policy and
  monitor suites:

```bash
set -o pipefail
uv run pytest tests/architecture/test_import_boundaries.py -q 2>&1 | tail -1
uv run pytest tests/core -q 2>&1 | tail -1
uv run pytest tests/tasks -q 2>&1 | tail -1
uv run ruff check weft && uv run mypy weft 2>&1 | tail -1
```

- [ ] **Step 13.4: Commit.**

```bash
git add weft/core/monitor/policies/task_log.py weft/core/monitor/policies/dead_task.py \
        tests/architecture/test_import_boundaries.py
git commit -m "Read claimed task-log rows through the public peek surface"
```

### Task 14: Weft docs + full gate + land

- [ ] **Step 14.1: CHANGELOG** (weft's, top of `## Unreleased` → `### Changed` —
  check whether the section already exists and merge into it; do NOT create a
  duplicate heading):

```markdown
- Monitor cleanup policies now read claimed task-log rows through SimpleBroker's
  public `peek_one`/`peek_many` `include_claimed` surface instead of the private
  `_retrieve` hook; requires simplebroker>=4.6.0. An architecture test now pins
  "no private simplebroker reaches" repo-wide. No behavior change.
```

- [ ] **Step 14.2: Flip the plan copy's `Status: draft` → `Status: completed`** and
  update its README.md index row to match (the metadata test cross-checks them).
- [ ] **Step 14.3: Full gate + final sweep:**

```bash
set -o pipefail
uv run pytest 2>&1 | tail -1
uv run ruff check weft && uv run mypy weft 2>&1 | tail -1
grep -rn "simplebroker\._\|_retrieve" weft/ --include="*.py" | grep -v "test_" ; echo "sweep exit: $? (1 = clean)"
```

- [ ] **Step 14.4: Commit docs; push weft main** (if rejected, `git rebase
  --autostash origin/main`, re-run the suite, push):

```bash
git add CHANGELOG.md docs/plans/
git commit -m "Document include_claimed migration"
git push origin main
```

---

## Rollback notes

- **Phase 1 (pre-publish):** plain `git revert`; nothing external moved.
- **Phase 2:** published packages are immutable — roll forward (4.6.1).
- **Phase 3:** the migration commit reverts cleanly; weft on 4.6.0 *without* Phase 3
  is fully supported (the private `_retrieve` engine still exists and the old reach
  still works) — partial landing is a safe intermediate state, same as the sidecar
  migration's.

## Acceptance checklist

- [ ] simplebroker 4.6.0 + simplebroker-redis 2.4.0 on PyPI; pg untouched at 2.2.1.
- [ ] `include_claimed` on `Queue.peek/peek_one/peek_many/peek_generator`,
  `BrokerCore`, the `BrokerConnection` protocol, `RedisBrokerCore`, and
  `broker peek --include-claimed` — and nowhere else.
- [ ] Invariants I1–I6 pinned by tests on all three backends (the `shared` CLI module
  runs everywhere; pg and redis extension tests cover API conformance).
- [ ] Weft pins ≥4.6.0; both policy helpers use public peeks; the architecture test
  in `tests/architecture/test_import_boundaries.py` fails on ANY private simplebroker
  reach; weft suite green.
- [ ] Zero remaining `simplebroker._` / `getattr(broker, "_…")` reaches anywhere in
  `weft/` — the post-sidecar, post-include_claimed steady state.

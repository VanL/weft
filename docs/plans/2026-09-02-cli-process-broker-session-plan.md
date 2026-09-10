# CLI Process Broker Session Plan

Status: draft
Source specs: docs/specifications/04-SimpleBroker_Integration.md [SB-0.1], [SB-0.4]; docs/specifications/10-CLI_Interface.md [CLI-0.3]; docs/specifications/14-Python_API_Surfaces.md [PY-1], [PY-4]
Superseded by: none

Class: 5 — the change adds normative connection-lifetime text to
[SB-0.4] (a governing operational boundary that the spec's Postgres
sizing paragraph already speaks to). A risky trigger also fires: the
same core behavior (queue-handle lifetime) runs through CLI, client,
command, and `weft manager serve` contexts, and the change alters an
execution path shared by every command. `hardening: applies`. Plan
type: implementation with spec revision, promotion strategy **B —
atomic** (the delta is three short paragraphs). Independent review of
the plan and the delta was completed 2026-09-02 (§11); no code slice
starts before the go/no-go.

Review history: independent review on 2026-09-02; dispositions in §11.
The 2026-09-07 revision corrects the failure-path proof below. The §9
public-API decision remains open; authorizing these plan corrections does
not select an API or authorize implementation.

## 1. Goal

A Weft CLI process opens a fresh SimpleBroker connection for almost
every queue operation it performs, and several times per poll iteration
of every watcher thread it starts. Each open is a full SimpleBroker
bootstrap (PRAGMAs, schema proof, timestamp-generator read) followed by
a close. On Postgres the per-operation cost is larger still: each
ephemeral operation builds a runner, which opens a whole connection
pool with `wait=True` (`simplebroker/db.py:817-821`,
`simplebroker_pg/runner.py:689-709`). The same defect was reported
against `taut`, another SimpleBroker embedder, on 2026-09-02; the
mechanism is identical here.

Measured on 2026-09-02 at HEAD `bcea628e` against a fresh SQLite
project, counting `sqlite3.connect` calls made by the CLI process
itself (manager and task processes excluded):

| Command | Connections today | Statements | PRAGMAs | With one process session |
|---------|------------------:|-----------:|--------:|-------------------------:|
| `weft status` (empty project) | 15 | 121 | 75 | 2 |
| `weft task list` (empty project) | 8 | 64 | 40 | 1 |
| `weft run --no-wait echo hi` (manager up) | 4 | — | — | 1 |
| `weft run --no-wait echo hi` (manager autostart) | 286 | 2290 | 1558 | not measured |
| `weft run echo hi` (waited, manager up) | 610 | 5196 | 3468 | 10 |
| `weft queue write/read/list` | 1 | ~10 | 5 | 1 |

The "with one process session" column is the same harness with two
monkeypatches applied: every `WeftContext.queue()` returns a
`persistent=True` handle, and one persistent handle is held for the
life of the process. That is the shape this plan implements properly.
The remaining 2 for `status` is the `broker()` site (root cause 3),
which the monkeypatch did not cover.

Root cause, verified by stack attribution of every connect:

1. `WeftContext.queue()` (`weft/context.py:144`) defaults to
   `persistent=False`, and every call site in `weft/commands/` either
   passes `persistent=False` explicitly or goes through a local helper
   whose default is `False`. In SimpleBroker 8.0 an ephemeral `Queue`
   opens and closes a `DBConnection` inside `Queue.get_connection()`
   for **every** operation (`simplebroker/sbqueue.py:298-317`).
2. `QueueChangeMonitor` (`weft/core/queue_wait.py`) falls back to one
   `QueueWatcher` thread per queue on SQLite (the SQLite backend has no
   multi-queue activity waiter). `QueueWatcher` uses the `Queue` object
   it is handed as-is (`simplebroker/watcher.py:344-346`). When that
   handle is ephemeral, every `get_data_version()` check — one at the
   top of `wait_for_activity` and one between each backoff chunk
   (`watcher.py:1402-1409`) — and every `has_pending()` check
   (`watcher.py:1760`) opens a fresh connection. In the waited-run
   measurement 919 of 1184 connects came from three such watcher
   threads (the `ctrl_out` and `weft.log.tasks` handles passed to the
   monitor in `weft/commands/_result_wait.py:129-132`; the `outbox`
   handle is already persistent and its watcher thread opened none).
3. `WeftContext.broker()` (`weft/context.py:154`) wraps
   `simplebroker.open_broker`, which always builds a fresh
   `DBConnection` (`simplebroker/db.py:1177-1186`); 34 call sites, 13
   of them in `weft/core/`.
4. Even where handles are persistent, SimpleBroker's process-local
   session is reference counted (`simplebroker/_broker_session.py:317-355`):
   when the last persistent handle for a target closes, the physical
   connection closes. Commands such as `weft status` open and close
   handles sequentially, so nothing keeps the session alive between
   them. The experiment confirms this: flipping every handle to
   persistent without a held lease only halves `weft status` (15 → 9);
   holding one lease takes it to 2.

The fix has three parts, all inside Weft and all on public SimpleBroker
API: hold one process-local session lease for the life of the CLI
process; make command-layer queue handles persistent (session-sharing)
and explicitly closed; route `WeftContext.broker()` through the shared
session. Core-service *queue handle* sites keep their current
ephemeral handles and get a follow-up plan (§9); `broker()` changes for
all of its callers, core included (§4).

## 2. Source Documents

- 04 [SB-0.1]: Weft delegates queue mechanics to SimpleBroker; the
  implementation mapping names `weft/context.py` as the place that
  "injects the resolved broker target".
- 04 [SB-0.4]: `QueueChangeMonitor` and `MultiQueueWatcher` are the
  named wait paths; "Weft must not regress to one Postgres listener
  connection per watched queue"; operators size Postgres for
  "concurrent Weft processes, plus short-lived CLI/startup spikes". The
  connection-lifetime paragraph this plan adds lives here.
- 10 [CLI-0.3]: `weft/bootstrap.py` is the import-light CLI entry that
  applies `WEFT_ENV_FILE` and then dispatches the Typer app. It is the
  only place that brackets the whole CLI process.
- 14 [PY-1], [PY-4]: `weft.context` is part of the public Python
  surface; `cli -> commands -> core` layering is one-way. `WeftClient`
  builds a `WeftContext` (`weft/client/_client.py:41`) and has no
  direct `.queue(` calls of its own — every embedder handle is a
  command-layer handle closed on return.
- `docs/lessons.md` "2026-04-04 PG Parity": every `persistent=True`
  handle a command path opens must be closed explicitly before the
  command returns — Postgres surfaces the omission as a CLI that prints
  its output and then hangs on pool-worker shutdown. This plan makes
  that rule load-bearing for every command-layer handle. The lesson is
  the evidence for "release explicitly before return"; this plan does
  not restate a thread-shutdown mechanism (the installed
  `psycopg_pool` and `simplebroker_pg` listener threads are daemon
  threads, so the mechanism is not what the lesson describes).
- SimpleBroker 8.0.0 (`.venv/lib/python3.14/site-packages/simplebroker/`):
  `sbqueue.py` `Queue.__init__` docstring ("Persistent Queue handles
  for the same resolved backend target share process-local backend
  session state"), `Queue.get_connection`, `Queue.close`,
  `Queue.cleanup_connections`; `_broker_session.py`
  `_ProcessBrokerSessionRegistry` (refcount acquire/release,
  `close_all` with its 5 s active-operation wait, the `atexit` hook),
  `_ProcessBrokerSession.release_current_thread_connection` (per-thread
  cores stay cached until the session closes or the thread's watcher
  recycles them); `watcher.py:344-346` (watchers adopt the handle they
  are given) and `:734-749` (watcher stop recycles its thread's core).
- Guidance: `CLAUDE.md` §1.1, §4.2, §4.11, §8; `docs/agent-context/engineering-principles.md`;
  `docs/agent-context/runbooks/hardening-plans.md`.

## 3. Context and Key Files

Current structure (what exists today):

- `weft/context.py` — `WeftContext` (frozen dataclass) with
  `queue(name, *, persistent=False)` → `simplebroker.Queue(...)` and
  `broker()` → `simplebroker.open_broker(...)`. `build_context()` runs
  once per command in most commands, up to three times in
  `weft/commands/run.py` (`:1120`, `:1251`, `:1273`, `:1335`), and once
  per operation in `weft/commands/system.py:255-258` and
  `weft/core/spec_store.py:245`. Every context for one project
  resolves to the same `broker_target` and the same frozen
  `broker_config` (`freeze_broker_config` keeps only `BROKER_*` keys,
  so autostart overrides do not change the SimpleBroker session key).
  `service_context_key(context)` (`:179-195`) already computes a stable
  per-target identity string.
- `weft/bootstrap.py::main` — applies the env file, then inside the
  import-light boundary imports `weft.cli.app.app`, calls `app()`
  (Typer standalone mode: exits via `SystemExit`), and maps
  `InvalidConfigError` to exit 1. `weft/__main__.py` and the `weft`
  console script both enter here. `weft manager serve` also enters here
  and runs the Manager in the CLI process
  (`weft/core/manager_runtime.py:1787`). The detached manager enters via
  `python -m weft.manager_process` (`manager_runtime.py:1043-1048`) and
  task processes via `weft/core/launcher.py::_task_process_entry`;
  neither passes through bootstrap. `tests/cli/*` files that use
  `typer.testing.CliRunner` enter `app` directly and bypass bootstrap.
- `weft/core/queue_wait.py::QueueChangeMonitor` — takes a sequence of
  `Queue` objects; `__init__` first tries
  `create_activity_waiter_for_queues` (`:45`, a handle operation) and
  on SQLite falls back to one `QueueWatcher` thread per queue, reading
  `queue.last_ts` (`:54`, another handle operation). It does not
  inspect the handles' persistence. `Queue.conn` is a public,
  class-annotated attribute (`sbqueue.py:209`) that is `None` exactly
  for ephemeral built-in handles (`:253-261`); it says nothing about
  whether a handle has been closed.
- Command-layer ephemeral handle sites. Two kinds: literal
  `persistent=False` arguments, and local helpers whose *default* is
  ephemeral. The second kind is why a literal grep undercounts.

  Literal sites (close status verified 2026-09-02):

  | File:line | Handle | Passed to monitor? | Closed today? |
  |-----------|--------|--------------------|---------------|
  | `weft/commands/_result_wait.py:130, :132` | `ctrl_out`, `weft.log.tasks` | yes | yes (`:330-335`) |
  | `weft/commands/_spawn_submission.py:72, :89` | `weft.state.tid_mappings`, `weft.log.tasks` | no | yes |
  | `weft/commands/_task_history.py:23` | `weft.log.tasks` | no | yes |
  | `weft/commands/events.py:160` | `weft.log.tasks` | yes | yes (`:205-207`) |
  | `weft/commands/events.py:312-313` | `ctrl_out`, `weft.log.tasks` | yes | yes (`:533-537`) |
  | `weft/commands/result.py:169` | `ctrl_out` | no | yes |
  | `weft/commands/result.py:196` | `weft.log.tasks` | yes | yes (`:332-334`) |
  | `weft/commands/result.py:339` | `weft.state.streaming` | no | **no** — never closed |
  | `weft/commands/result.py:536-537` | `ctrl_out`, `weft.log.tasks` | yes | yes |
  | `weft/commands/run.py:647` | `weft.log.tasks` | no | yes (`:658`) |
  | `weft/commands/tasks.py:108` | `weft.state.tid_mappings` | no | yes |
  | `weft/commands/tasks.py:141` | `weft.log.tasks` | no | yes (`:154-155`) |
  | `weft/commands/tasks.py:901-902` | `weft.log.tasks`, `weft.state.tid_mappings` | yes | yes (`:931-934`) |
  | `weft/commands/tasks.py:1233-1235` | mappings, log, `ctrl_out` | yes | yes (`self.close()`) |

  Helper-default sites:

  | Helper | Default | Callers | Monitor sites among them |
  |--------|---------|---------|--------------------------|
  | `weft/commands/system.py:267-273` `_queue(ctx, name, *, persistent=False)` | ephemeral | `:330, :344, :853, :1088, :1154, :1317, :1582, :1764` | `:1582→:1585` (`weft status --watch`), `:1764→:1767` (project events) |
  | `weft/commands/_spawn_submission.py:52-58` `_queue_contains_exact_message(..., persistent=False)` | ephemeral | its callers in the same file | none |
  | `weft/commands/_spawn_submission.py:164-169` `_spawn_reconciliation_queue_specs` builds `(name, False)` pairs | ephemeral | `_open_spawn_reconciliation_monitor` (`:173-181`) | yes — spawn reconciliation monitor |

- Manager start/wait helpers used by the CLI autostart path:
  `weft/core/manager_runtime.py:1272`, `:1324`, `:1571` build a
  `QueueChangeMonitor` over `registry_queue` handles created with
  `persistent=False` (`:152`, `:354`). These are in `weft/core/` but are
  the 286-connect autostart path; they are in scope (Task 5).
- `WeftContext.broker()` callers (34): `weft/commands/queue.py` (10),
  `weft/commands/load.py` (3, including the rollback path at
  `:450-462`), `weft/commands/dump.py`, `weft/commands/result.py:125`,
  `weft/commands/system.py` (via `collect_broker_status`); in core:
  `weft/core/monitor/task_monitor.py` (6), `weft/core/monitor/store.py`
  (2), `weft/core/monitor/policies/*` (2), `weft/core/monitor/runtime.py:790`,
  `weft/core/manager.py:4387`, `weft/core/task_evidence.py:674`. Find
  them with `grep -rn "\.broker()" weft`.
- `weft/commands/load.py:158-170` `_SqliteSnapshot.restore()` copies
  the snapshot over the live SQLite file and unlinks `-wal`/`-shm`.
  Today the `with context.broker()` block has already closed its
  connection before the `except` at `:458` runs `restore()`.
- Test harness: `tests/helpers/weft_harness.py::cleanup` runs a
  `gc.get_objects()` scan that closes every `Queue` bound to the test
  database (`:1278-1289`); `_database_files_releasable` is a no-op off
  Windows (`:1345-1347`). `tests/conftest.py::broker_env` builds
  `persistent=True` handles (`:327-335`), so existing
  `QueueChangeMonitor` tests already use persistent handles.
  `tests/commands/test_queue.py:36`, `test_run.py:1275`,
  `test_status.py:183` define `_FakeQueueChangeMonitor` classes that
  are monkeypatched in place of the real class; they do not call the
  real `__init__`.

Files to modify:

- `weft/context.py`, `weft/_constants.py` (one queue-name constant)
- `weft/bootstrap.py`
- `weft/core/queue_wait.py`
- `weft/core/manager_runtime.py` (three monitor sites and their two
  `registry_queue` constructors only)
- the literal and helper-default sites listed above, plus
  `weft/commands/load.py` (rollback recycle)
- `tests/context/test_context.py`, `tests/core/test_queue_wait.py`,
  new `tests/cli/test_cli_process_session.py`, `tests/conftest.py`
  (one fixture)
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4];
  `docs/specifications/14-Python_API_Surfaces.md` [PY-1] only if the
  §9 decision makes the lease public
- `docs/lessons.md`, `docs/plans/README.md`

Read first:

- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.1], [SB-0.4]
- `weft/context.py` (whole file; it is short)
- `weft/core/queue_wait.py`
- `simplebroker/sbqueue.py` lines 163-380 and 2016-2064, and
  `simplebroker/_broker_session.py` lines 159-398 in the installed
  package
- `docs/lessons.md` "2026-04-04 PG Parity"
- `tests/helpers/weft_harness.py` `cleanup` and the gc scan at
  `:1278-1289`; `tests/cli/test_env_file_bootstrap.py:56-66` (the
  existing pattern for driving bootstrap in-process)

Style and guidance: `CLAUDE.md` §4.2 (imports at top; a function-level
import is allowed only to break a real cycle or keep an import-light
boundary, with a comment naming that reason), §4.5 (`Spec:` docstrings
on functions that own a spec boundary), §4.7 (defensive catches carry
a pragma), §4.11.

Shared paths — reuse, do not duplicate: `WeftContext.queue()` is the
only queue-handle constructor the command layer may use;
`WeftContext.broker()` is the only broker-connection constructor;
`QueueChangeMonitor` is the only wait helper; `service_context_key`
is the per-target identity. Do not add a connection pool, a
queue-handle cache, a context registry, a Weft-side refcount, or a
`QueueFactory`. SimpleBroker already owns the process-local session
and its refcounting; Weft holds exactly one extra reference per target.

Comprehension check before editing (answer in the handoff, not in the
plan): (a) why does flipping every handle to `persistent=True` reduce
`weft status` only from 15 to 9 connections, and what makes the
remaining 7 go away? (b) which three watcher threads produced 919
connects in the waited-run measurement, and why did the fourth produce
none? (c) which two helpers under `weft/commands/` hide ephemeral
defaults, and which two shipped commands would raise under the Task 6
guard if those helpers were left unconverted?

## 4. Invariants and Constraints

- Queue semantics do not change: same queue names, same message bodies,
  same ordering, same read/peek/move behavior. This plan changes only
  *how long a backend connection lives*, never *what is sent through it*.
- `spec` / `io` immutability, TID format, forward-only state, and
  reserved-queue policy are untouched.
- Spawn-based process behavior: the CLI launches the manager with
  `subprocess.Popen(close_fds=True, start_new_session=True)`
  (`weft/manager_detached_launcher.py:41-49`) and tasks with the
  `multiprocessing` spawn context (`weft/core/launcher.py:167`). A held
  SQLite or Postgres connection in the CLI process is therefore never
  inherited. No `fork` is used anywhere in `weft/` (verified by grep),
  and SimpleBroker's session key includes the PID
  (`_broker_session.py:29`). This must stay true.
- Every persistent handle the command layer opens is closed on every
  exit path (lesson "PG Parity"), and **no handle is used after
  `close()`**. The second rule is new: `DBConnection._ensure_shared_session`
  silently re-acquires the session on use-after-close
  (`db.py:911-918`), which for an ephemeral handle was harmless and for
  a persistent handle is a reference released only by the GC finalizer.
- Honest degradation without a lease: an unclosed persistent handle
  holds its thread's physical connection until the finalizer runs; an
  unclosed ephemeral handle held nothing. The harness gc-scan is the
  existing mitigation in tests; in production the rule above is the
  mitigation.
- `WeftContext.broker()` changes for **all 34 callers, core included**.
  Without a lease its behavior is equivalent to today (one connection,
  closed on exit) but it now runs through the session machinery,
  including `close_all`'s up-to-5 s wait for active operations
  (`_broker_session.py:20, 289-295`). Commands close their monitors
  before their handles, so that wait is normally zero.
- The lease is released by the same bracket that acquired it, in a
  `finally`, before `bootstrap.main` returns. SimpleBroker's `atexit`
  hook is a backstop only.
- Manager consequences: the detached manager and task processes are
  never armed (they do not enter bootstrap); `weft manager serve` is
  armed for the Manager's lifetime, which adds one session reference to
  a process that already holds persistent handles
  (`weft/core/manager.py:2587, 4011, 5249`) — no behavior change.
- Thread affinity: SimpleBroker gives each thread its own core inside a
  shared session. A watcher thread's core is recycled when the watcher
  stops (`watcher.py:734-749` → `Queue.cleanup_connections`); only the
  main thread's core lives to session close. Peak backends per CLI
  process = threads concurrently touching the broker, plus (Postgres)
  the pool's configured minimum.
- Library embedders are not surprised: `build_context()` and
  `WeftContext.queue()` acquire no lease unless the process is armed by
  bootstrap or the embedder brackets its work in the lease context
  manager (§9 decision). The CLI opts in; nothing else does implicitly.
- `weft.state.*` queues stay runtime-only; nothing here touches
  dump/load formats.
- No new dependency. No new SimpleBroker API is required
  (`close_process_broker_sessions` is not exported and is not needed).

Fatal versus best-effort: failing to acquire the lease is not fatal —
the lease helper logs at debug level and the command runs with today's
per-operation connections. Failing to *release* the lease is logged at
warning level and does not change the CLI exit code (the command's own
outcome already happened). Release may block up to 5 s if a handle
operation is still in flight on another thread; commands close their
monitors first, so this is a defect signal, not a normal path.

Review gates: no new execution path (the same commands run the same
queue operations); no public CLI shape change; no drive-by refactor of
`weft/core/` handle sites; real broker in every test (no mocked
`Queue`, `DBConnection`, or watcher); independent review of this plan
and the spec delta completed before implementation (§11).

Hidden couplings: (1) `run.py`, `system.py`, and `spec_store.py` build
several `WeftContext` objects per process — the lease is deduplicated
by `service_context_key`, not by context instance. Two contexts for the
same target with different `broker_config` get separate SimpleBroker
sessions (no sharing, no harm). (2) `tests/cli` `CliRunner` tests
bypass bootstrap and so never hold a lease; their persistent handles
are closed by the harness gc-scan. (3) The `QueueChangeMonitor` guard
(Task 6) must land *after* every command-layer and `manager_runtime`
site is converted, or `weft events`, `weft status --watch`, `weft run`
autostart, and spawn reconciliation raise in the interim.
(4) `load.py` rollback replaces the SQLite file under a connection that
a held lease keeps cached (Task 4).

One-way doors: none. Rollback is a plain revert; no persisted format,
queue name, or CLI flag changes.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Spec Baseline

- `bcea628e` — docs/specifications/04-SimpleBroker_Integration.md,
  docs/specifications/10-CLI_Interface.md,
  docs/specifications/14-Python_API_Surfaces.md at plan authoring time
  (2026-09-02). Plan type: implementation with spec revision.
  Promotion baseline identifier: to be recorded when the atomic slice
  lands.

## Proposed Spec Delta

Promotion strategy:

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| docs/specifications/04-SimpleBroker_Integration.md | B — atomic | [SB-0.4] "Current behavior" list and the Postgres operational paragraph; `_Implementation mapping_` block |
| docs/specifications/14-Python_API_Surfaces.md | B — atomic, **only if §9 decision = public** | [PY-1] |

### [SB-0.4] — append to the "Current behavior" bullet list, after the bullet ending "…before they fall back to discovery"

> - a Weft CLI process holds one process-local broker session per
>   resolved target for the life of the process. `weft/bootstrap.py`
>   arms the lease before dispatching the command and releases every
>   held lease before the process returns. Command-layer queue handles
>   (`weft/commands/`, `weft/cli/`) are persistent handles that share
>   that session, are closed explicitly on every return path, and are
>   never used after close; they do not open a backend connection per
>   queue operation
> - a handle passed to `QueueChangeMonitor` is always a persistent
>   handle. `QueueChangeMonitor` rejects an ephemeral handle before
>   performing any handle operation, because a watcher polling an
>   ephemeral handle opens a new backend connection on every poll
>   check

### [SB-0.4] — replace the sentence "Operators should still size Postgres for the number of concurrent Weft processes, plus short-lived CLI/startup spikes."

> Operators should still size Postgres for the number of concurrent
> Weft processes, plus short-lived CLI/startup spikes. Each CLI process
> holds one broker session for its lifetime instead of opening a
> connection per queue operation; its backend count is the number of
> threads concurrently touching the broker plus the pool's configured
> minimum, so a CLI spike is bounded by process and thread count, not
> by operation count.

### [SB-0.4] — `_Implementation mapping_` block, extend the `weft/context.py` entry

> `weft/context.py` (`build_context`, `_resolve_root_and_target`,
> `WeftContext`, the process broker lease), `weft/bootstrap.py` (lease
> bracket around CLI dispatch), …

### [PY-1] — only if the lease is public (§9): add one bullet to the `weft.context` surface

> - `process_broker_lease(context)`: a context manager that holds one
>   process-local broker session for `context`'s target while the block
>   runs. Embedders that perform many queue operations in one process
>   bracket them in it; `build_context()` and `WeftContext.queue()`
>   never acquire a lease on their own.

Rule-versus-code check: every bullet is *new* behavior; none describes
HEAD. The reviewer checked them against the tasks below (§11).

## 5. Tasks

Tasks 1-3 are sequential. Tasks 4 and 5 are independent of each other
and both depend on Task 3. Task 6 (the guard) depends on 4 and 5. Task
7 is the atomic promotion slice and lands with the code.

1. **Red tests (real broker, SQLite lane; PG lane skipped by marker).**
   - Outcome: failing tests that pin the contract.
   - Files: `tests/context/test_context.py`, `tests/core/test_queue_wait.py`,
     new `tests/cli/test_cli_process_session.py`, `tests/conftest.py`.
   - Read first: `tests/context/test_context.py:128-150`,
     `tests/core/test_queue_wait.py`, `tests/cli/test_env_file_bootstrap.py:56-66`.
   - Fixture: `count_sqlite_connects()` in `tests/conftest.py` — wraps
     `sqlite3.connect` with `monkeypatch` (a module-attribute patch
     works: `simplebroker/_runner.py:340` resolves it by attribute) and
     returns a counter with `reset()`. It observes the stdlib boundary
     and mocks nothing in SimpleBroker or Weft. Skip when
     `BROKER_TEST_BACKEND != "sqlite"`. Reset the counter *after*
     `build_context` so `_ensure_database` is not counted.
   - (a) `test_process_broker_lease_shares_one_connection`: build a
     context in `tmp_path`; inside the lease block open, write, close
     `ctx.queue("a")`, then open, peek, close `ctx.queue("b")`; assert
     exactly one connect. (Do not assert the no-lease count — that is a
     SimpleBroker property, not a Weft contract.)
   - (b) `test_queue_change_monitor_rejects_ephemeral_handle`:
     `QueueChangeMonitor([ctx.queue("x", persistent=False)])` raises
     `ValueError` and the connect counter did not move (proves the
     check precedes any handle operation).
     `test_queue_change_monitor_persistent_handle_does_not_reconnect_per_poll`:
     start a monitor over one persistent handle on queue `x`, reset
     the counter, write one message to `x` through a second persistent
     handle, `assert monitor.wait(2.0)` (at least one poll observed the
     write), close, and assert the counter grew by at most one (the
     watcher thread's own core). No sleeps.
   - (c) `test_cli_status_uses_one_connection_per_process`: initialise
     a project with `build_context`; change the test working directory to
     that project and set `WEFT_CONTEXT` to it (both commands must use the
     same isolated target); patch
     `sys.argv` to `["weft", "status"]`; call `weft.bootstrap.main()`
     inside `pytest.raises(SystemExit) as info` and assert
     `info.value.code in (None, 0)`; assert exactly one connect. Same
     for `["weft", "task", "list"]`. Add
     `test_cli_releases_lease_when_command_fails`: keep project config
     valid; drive `["weft", "queue", "alias", "remove",
     "missing-review-alias"]` against an empty alias registry. The real
     `cmd_queue_alias_remove` builds a context, reads the alias registry
     through `context.broker()`, closes that operation's handle, then
     raises `CommandExecutionError` because the alias is absent. Assert
     exit 1, the missing-alias diagnostic, no traceback, and exactly one
     observed connection before failure. After bootstrap unwinds, a fresh
     `ctx.queue("probe", persistent=True)` write must open exactly one
     additional connection; close this probe in `finally`. Do not use
     corrupt config as release evidence: it raises before the proposed
     `build_context` lease acquisition.
     This failure test alone also passes without a lease, so pair it with
     1(a) and the successful CLI sharing tests, and verify after Task 3
     that temporarily bypassing the armed bracket's entire release/reset
     cleanup makes this test fail. Retain its armed handle references for
     this mutation: dropping them can invoke Queue finalizers and mask a
     missing explicit close. With those references retained, the probe
     reuses the leaked core and opens zero connections. Restore the
     cleanup and rerun. The mutation is
     verification only, never a committed alternate implementation.
   - Stop if: a test needs to patch anything inside `simplebroker` or
     `weft.core` to become expressible — the contract is observable at
     the stdlib boundary and the assertion must stay there.
   - Done when: the sharing/guard tests fail for the stated reasons
     (`ImportError` for the lease, no `ValueError`, connect count > 1).
     The failure-release test is a new-lifecycle regression test: the
     current code has no CLI lease to leak, so its pre-change result does
     not demonstrate release. Its required negative proof is the omitted-
     cleanup mutation after Task 3, with armed references retained,
     followed by a passing restored run.

2. **Lease primitive and `broker()` through the session.**
   - Outcome: a lease context manager in `weft/context.py`, an arming
     slot consulted by `build_context`, and `WeftContext.broker()`
     sharing the session when one is held.
   - Files: `weft/context.py`, `weft/_constants.py`.
   - Lease: `process_broker_lease(context)` (name per §9 decision) is
     a `contextmanager` that constructs one
     `Queue(WEFT_PROCESS_SESSION_LEASE_QUEUE, db_path=context.broker_target, persistent=True, config=context.broker_config)`,
     yields, and `close()`s it in `finally`. No module state, no Weft
     refcount — SimpleBroker's registry already makes repeated and
     nested acquisition safe. Constructing a persistent `Queue`
     acquires the session without opening a physical connection,
     creating a queue row, reading a timestamp, or starting a thread
     (verified 2026-09-02: zero `sqlite3.connect` calls at
     construction). `WEFT_PROCESS_SESSION_LEASE_QUEUE = "weft.session.lease"`
     is a new constant in `weft/_constants.py` with a docstring saying
     the handle is never operated on and the queue never exists.
   - Arming slot: a module-level `_armed_leases: list[Queue] | None`
     in `weft/context.py`, `None` when not armed. `build_context()`,
     after constructing the context, appends one lease handle when the
     slot is a list and no held handle already covers
     `service_context_key(context)` (keep the keys in a parallel
     `set[str]` or store `(key, handle)` pairs — either is fine; no
     class). A second `contextmanager`, `_armed_process_broker_leases()`,
     sets the slot to `[]`, yields, and in `finally` closes every held
     handle and resets the slot to `None`. It is private; only
     bootstrap uses it. Set single-threaded, before dispatch.
   - `WeftContext.broker()`: keep the signature and the yielded
     `BrokerConnection` type. Inside, open
     `handle = self.queue(WEFT_PROCESS_SESSION_LEASE_QUEUE)` (persistent
     by Task 3's default; pass `persistent=True` explicitly until then)
     and `with handle.get_connection() as connection: yield connection`;
     `handle.close()` in `finally`. `Queue.get_connection()` yields the
     same `BrokerDB` type `open_broker` yields (verified 2026-09-02),
     and on Postgres the same core type over the shared runner with
     autocommit pooled connections, so `dump`/`load` transaction
     semantics are unchanged.
   - Constraints: no cache of handles handed to callers; no
     `WeftContext` field (it is frozen and built several times per
     process); imports at module top.
   - Tests: 1(a) goes green. Existing `test_context.py` round-trip
     stays green.
   - Stop if: SimpleBroker needs to change to make the lease work —
     stop and report; do not vendor or monkeypatch SimpleBroker.
   - Done when: `pytest tests/context -q` is green.

3. **Bootstrap bracket and default flip.**
   - Outcome: the CLI process holds the lease from the first
     `build_context` to just before `bootstrap.main` returns;
     `WeftContext.queue()` defaults to `persistent=True`.
   - Files: `weft/bootstrap.py`, `weft/context.py`.
   - Bootstrap: inside the existing import-light boundary (next to the
     deferred `from weft.cli.app import app`), add the deferred import
     of `_armed_process_broker_leases` with the §4.2 comment naming the
     import-light contract ([CLI-0.3]), and wrap `app()` in it. `app()`
     exits via `SystemExit`; the `finally` releases on that path, on
     `InvalidConfigError`, and on any other exception. Keep the
     `InvalidConfigError` mapping exactly as it is.
   - Default flip: change the `persistent` default in
     `WeftContext.queue()` to `True`. Keep the keyword so explicit
     ephemeral callers in `weft/core/` are untouched by this plan.
   - Tests: run 1(c)'s omitted-cleanup mutation and restored failure-release
     test. The successful-command sharing checks in 1(c) are expected to
     still fail after this task (command
     handles still pass `persistent=False`); record the connect count
     it reports — it should already be far below 15.
   - Stop if: any existing test starts holding a lease outside
     bootstrap (visible as a `Queue` the harness gc-scan closes with a
     `weft.session.lease` name) — that means `build_context()` is
     acquiring while unarmed.
   - Done when: `pytest tests/context tests/cli -q` has no new
     failures other than 1(c).

4. **Command-layer handle sites.**
   - Outcome: no ephemeral handle remains under `weft/commands/` or
     `weft/cli/`, whether by literal argument or helper default; every
     handle is closed on every path; `load` rollback recycles the
     cached connection.
   - Files: the literal and helper-default sites in §3;
     `weft/commands/load.py`.
   - Literal sites: delete the `persistent=False` argument. Add the
     missing `try/finally: queue.close()` at `result.py:339`
     (`_active_streaming_queues`). All other literal sites already
     close on every path (§3 table). For handles passed to
     `QueueChangeMonitor`, keep the existing order: `monitor.close()`
     first, then the handles.
   - Helper-default sites: change `system.py::_queue`'s default to
     `persistent: bool = True` (or drop the parameter — every caller
     uses the default), change
     `_spawn_submission.py::_queue_contains_exact_message`'s default
     likewise, and change `_spawn_reconciliation_queue_specs` to build
     `(name, True)` pairs (the static spec tuple it extends must be
     checked too). Confirm each of the eight `system.py` callers closes
     its handle in `finally`.
   - `load.py` rollback: immediately before `snapshot.restore()`
     (`:458-462`), recycle the current thread's cached SQLite core so
     the file swap does not happen under a live connection: open a
     persistent handle via `context.queue(WEFT_PROCESS_SESSION_LEASE_QUEUE)`,
     call its public `cleanup_connections()` (`sbqueue.py:2016-2023` →
     `DBConnection.cleanup` → `session.cleanup_current_thread`), close
     it, with a comment naming this plan. This path is SQLite-only
     (`_SqliteSnapshot`).
   - Do not touch other `weft/core/` sites in this task.
   - Tests: 1(c) goes green for `status` and `task list`. Run
     `pytest tests/commands tests/cli -q`. Add one `load` rollback
     test if none exercises the `snapshot.restore()` path with a live
     handle held (check `tests/commands/test_dump_load*.py` first).
   - Stop if: a handle cannot be closed on some path without
     restructuring a generator (`events.py` yields from inside the
     wait loop, but its `finally` already closes) — restructure
     minimally with `try/finally`; if that is not enough, record it in
     the Deviation Log and leave that one handle ephemeral with a
     comment naming this plan. Note that leaving one ephemeral handle
     that feeds a monitor is not an option once Task 6 lands.
   - Done when: `grep -rnE "persistent(: bool)? ?= ?False" weft/commands weft/cli`
     returns nothing and the suites are green.

5. **Manager start/wait path.** (independent of Task 4)
   - Outcome: the manager autostart wait no longer opens a connection
     per poll check.
   - Files: `weft/core/manager_runtime.py` — the two `registry_queue`
     constructors at `:152` and `:354` and the three monitor sites at
     `:1272`, `:1324`, `:1571`.
   - Change the two constructors to persistent handles and make every
     caller close them (`finally`). These functions are also called
     from the manager process itself; a persistent handle closed on
     return is correct in both contexts.
   - Tests: add `test_cli_run_autostart_uses_bounded_connections` to
     `tests/cli/test_cli_process_session.py`: in-process
     `bootstrap.main` with `["weft", "run", "--no-wait", "echo", "hi"]`
     against a project with no manager; assert connects ≤ 3, expected
     2 (main-thread core, registry watcher core; `_ensure_database`
     returns early for an existing file), and record the actual. The
     test launches a real detached manager; stop it in `finally` with
     `commands.cmd_manager_stop` and mark the test `slow`.
   - Stop if: the count exceeds 3 and the extra connects come from
     `weft/core` sites outside these five lines — record the site in
     the handoff for the follow-up plan rather than widening this task.
   - Done when: the test is green and `pytest tests/core/test_manager_runtime*.py -q`
     stays green.

6. **`QueueChangeMonitor` guard.** (after Tasks 4 and 5)
   - Outcome: `QueueChangeMonitor.__init__` raises
     `ValueError("QueueChangeMonitor requires persistent queue handles: <names>")`
     when any `queue.conn is None`, **before**
     `_start_multi_queue_waiter` and before reading `queue.last_ts`
     (both are handle operations). This is a contract gate on
     reachable input at a boundary (CLAUDE.md §4.11 "reject
     unsupported fields explicitly"), not a guard against a state the
     invariants preclude: SimpleBroker accepts ephemeral handles in
     watchers by design, and 17 sites reach this state today.
   - Files: `weft/core/queue_wait.py`.
   - Why not have the monitor build its own persistent handles: that
     fixes only the watcher half (callers still read through their own
     ephemeral handle) and needs SimpleBroker's private `_config`.
   - Tests: 1(b) goes green. Existing monitor tests use `broker_env`'s
     persistent handles and are unaffected; the `_FakeQueueChangeMonitor`
     classes in `tests/commands` never call the real `__init__`.
   - Done when: `pytest tests/core/test_queue_wait.py tests/commands tests/cli -q`
     is green.

7. **Atomic promotion slice: spec, lesson, index.**
   - Outcome: [SB-0.4] (and [PY-1] if public) carries the delta text
     verbatim; `docs/lessons.md` gains a dated entry;
     `docs/plans/README.md` row is already present; this plan records
     the promotion baseline identifier and its Status.
   - Files: `docs/specifications/04-SimpleBroker_Integration.md`,
     `docs/specifications/14-Python_API_Surfaces.md` (conditional),
     `docs/lessons.md`, this plan.
   - Lesson text (draft): "An ephemeral SimpleBroker handle costs one
     connection bootstrap per operation, and several per poll when a
     watcher is built over it. A CLI that opens handles sequentially
     needs a held session lease as well as persistent handles:
     persistent handles alone only halve the churn, because SimpleBroker
     closes the session when the last handle closes. Helper wrappers
     with `persistent=False` defaults hide sites from a literal grep.
     Count `sqlite3.connect` calls when a command feels slow; the
     number should be one per thread."
   - Also add the `Spec:` docstring reference `[SB-0.4]` on the lease
     helper, `WeftContext.broker`, and the `QueueChangeMonitor` guard.
   - Done when: the backstitch/traceability check the repo uses passes
     from current state and the spec text matches §"Proposed Spec Delta"
     character for character.

## 6. Testing Plan

- Harness: real SQLite broker in `tmp_path` via `build_context`; real
  watcher threads; in-process `bootstrap.main` for the CLI-level
  assertions (subprocess `run_cli` cannot count connects). The
  `count_sqlite_connects` fixture is the only patch and it wraps the
  stdlib, not Weft or SimpleBroker.
- Do not mock: `Queue`, `DBConnection`, `QueueWatcher`,
  `QueueChangeMonitor`, `build_context`, the manager launch.
- Contract each test protects:
  - 1(a): the lease keeps one physical connection across sequential
    handle open/close cycles ([SB-0.4] bullet 1).
  - 1(b): monitors never poll ephemeral handles ([SB-0.4] bullet 2);
    persistent-handle watchers do not reconnect per poll.
  - 1(c) and Task 5's test: end-to-end CLI processes hold one session
    (`status`, `task list`, `run --no-wait` with autostart), and the
    lease is released on a failing command.
  - Task 4's load test: rollback does not swap the file under a cached
    connection.
- Red first: sharing and guard tests fail today for the named reasons.
  Failure-release uses Task 1(c)'s omitted-cleanup mutation after the lease
  exists; the existing code has no such lifecycle to leak. Record the
  failing mutation and passing restored run as its correction proof.
- Sleep hygiene: no test sleeps. Poll observation is proven by
  `monitor.wait(...)` returning `True` after a real write.
- Edge case out of scope: Postgres pool-thread shutdown timing. The PG
  lane runs the existing CLI suites (`BROKER_TEST_BACKEND=postgres`),
  which already fail loudly when a CLI hangs on pool shutdown (lesson
  "PG Parity"); no new PG-only test is added.
- Runtime observation after landing: `weft status` wall time on a
  populated project, and, on Postgres, `pg_stat_activity` backend
  count during a `weft run` should show one backend per CLI thread plus
  the pool minimum, not a sawtooth.

## 7. Verification and Gates

Per task:

```bash
./.venv/bin/python -m pytest tests/context -q
./.venv/bin/python -m pytest tests/core/test_queue_wait.py -q
./.venv/bin/python -m pytest tests/cli/test_cli_process_session.py -q
./.venv/bin/python -m pytest tests/commands tests/cli -q
./.venv/bin/python -m pytest tests/core/test_manager_runtime*.py -q
```

Final gates (full suite: blast radius is every command):

```bash
./.venv/bin/python -m pytest -q
./.venv/bin/python -m pytest -m "" tests/cli/test_cli_process_session.py -q
./.venv/bin/mypy weft bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
grep -rnE "persistent(: bool)? ?= ?False" weft/commands weft/cli   # must be empty
```

Postgres lane when a DSN is available:

```bash
BROKER_TEST_BACKEND=postgres ./.venv/bin/python -m pytest tests/cli tests/commands -q
```

Rollout: one landing (strategy B). Rollback: revert the commit; no
data, queue, or flag migration.

## 8. Independent Review Loop

Completed 2026-09-02 by a general-purpose reviewing agent given the
plan, the delta, `weft/context.py`, `weft/core/queue_wait.py`,
`weft/bootstrap.py`, the command sites, `manager_runtime.py`, the
installed SimpleBroker session/watcher code, the "PG Parity" lesson,
[SB-0.4], and the harness cleanup. Every finding was verified against
the code by the author before disposition. Dispositions are in §11. A
second review pass is owed after implementation, before landing.

## 9. Out of Scope and Open Decision

- `weft/core/` queue-handle sites other than the five
  `manager_runtime.py` lines in Task 5 (27 `persistent=False` sites in
  `manager.py`, `heartbeat.py`, `task_monitor.py`, `endpoints.py`,
  `task_evidence.py`, `pruning/`, `control_probe.py`,
  `spawn_requests.py`, `monitor/`). Long-running services pay the same
  per-operation cost; they get a separate plan because their handle
  lifetimes interact with task shutdown (lesson "PG Parity" first
  bullet) and with the Manager's child bookkeeping. Note that
  `broker()` sites in core *do* change under this plan (§4).
- **Open decision (Van): is the lease public?** `weft.context` is a
  public surface ([PY-1]) and CLAUDE.md §8 says ask before public API
  changes. Recommended: public `process_broker_lease(context)` with the
  one-line [PY-1] delta above, because `taut`- and `engram`-style
  embedders have exactly this problem and `WeftClient` has no other
  place to hold a session. Alternative: keep it private
  (`_process_broker_lease`), drop the [PY-1] delta, and revisit with a
  `WeftClient.__enter__/__exit__` decision later.
- `WeftClient.__enter__/__exit__` or `close()`. Even if the lease is
  public, wrapping it into `WeftClient` is a separate public-API
  change.
- A first-class lease API in SimpleBroker. Worth proposing upstream
  (the handle-as-lease is a workaround, and `taut` needs the same
  thing), but Weft must not wait on it.
- The `scan_queue_window(..., persistent=False)` default in
  `weft/core/queue_window.py` (pruning/liveness callers).
- Any change to `weft queue` passthrough commands — they already use
  one connection.

## 10. Fresh-Eyes Review

Author self-review completed 2026-09-02 after the review pass: file
paths are exact; every task names its files, tests, and stop
condition; the lease is deduplicated by `service_context_key` because
several contexts are built per process; the guard is ordered after
site conversion; the `load` rollback hazard and the use-after-close
hazard are named; the two hidden helper defaults are in the site
inventory and in the Task 4 gate. External review is complete for the
plan; the open decision in §9 and the go/no-go are Van's.

## 11. Review Dispositions (2026-09-02)

| # | Finding (severity) | Verified? | Disposition |
|---|--------------------|-----------|-------------|
| A2 | Ephemeral watcher handles connect several times per wait iteration (between backoff chunks), not once | yes (`watcher.py:1402-1409`) | §1 root cause 2 corrected |
| A3 | Site inventory missed helper-default ephemeral sites in `system.py` and `_spawn_submission.py`, three of which feed monitors; Task 4 gate would pass with them unconverted (blocker) | yes (`system.py:267-273` + 8 callers; `_spawn_submission.py:52-58, :164-181`) | §3 helper-default table added; Task 4 converts them; gate regex now matches defaults |
| A4 | `broker()` has 34 callers, 13 in core; rewriting it contradicts "core unchanged" | yes (grep) | §1, §4, §9 now say plainly that `broker()` changes for all callers |
| B1 | Persistent-handle lease has no construction side effects | yes (0 connects at construction, verified) | recorded in Task 2 |
| B2 | Use-after-close silently re-leases the session (new hazard after the flip) | yes (`db.py:911-918`) | §4 invariant "never used after close" added; spec bullet 1 says so |
| B3 | "Never to a leak" overstated | yes | §4 rewritten to the honest degradation plus the gc-scan mitigation |
| B4 | `get_connection()` and `open_broker` yield the same type and semantics for dump/load | yes (`BrokerDB` both, verified) | recorded in Task 2 |
| B5 | `load` rollback restores the SQLite file under a cached connection when a lease is held | yes (`load.py:450-462, :158-170`) | Task 4 adds a `cleanup_connections()` recycle before `restore()` plus a test |
| B6 | Lease queue name should be inert, not `weft.log.tasks` | agree | `WEFT_PROCESS_SESSION_LEASE_QUEUE = "weft.session.lease"` |
| C1 | Weft-side keyed dict + refcount duplicates SimpleBroker's registry; use `service_context_key` + a list | agree | Task 2 rewritten; `release_process_broker_leases()` dropped |
| C2 | Armed bracket is right but its home and bootstrap's import-light rule were unspecified | yes (`bootstrap.py:1-10, 104-113`) | Task 2/3 name the slot in `weft/context.py` and the deferred import with comment |
| C3 | Manager consequences unstated | yes (`manager_runtime.py:1043-1048, :1787`) | §4 bullet added |
| C4 | `CliRunner` tests and embedders are fine | yes | §4 hidden coupling 2 |
| C5 | `process_broker_lease` is a public API addition without a [PY-1] delta or Van's approval | agree | §9 open decision; conditional [PY-1] delta added |
| C6 | In-process `bootstrap.main()` raises `SystemExit`; 1(c) as written cannot work | yes (`bootstrap.py:110-118`, `test_env_file_bootstrap.py:60-64`) | Task 1(c) rewritten |
| D1 | Guard is a contract gate, keep it, but land it after site conversion and before any handle operation | agree | guard moved to Task 6; ordering stated |
| D2/D3 | Existing monitor tests use persistent handles; `Queue.conn` is a public attribute | yes (`conftest.py:327-335`, `sbqueue.py:209`) | recorded in §3 and Task 6 |
| E1 | Watcher cores are recycled at watcher stop; spec sentence should say "concurrently" and mention the pool minimum | yes (`watcher.py:734-749`) | §4 and the Postgres delta paragraph updated |
| E2 | Today's PG cost is a pool open per operation | accepted from reviewer's citations | one sentence in §1 |
| E3 | Non-daemon-thread rationale is not evidenced; pool and listener threads are daemon | yes (`psycopg_pool/_acompat.py:113`, `simplebroker_pg/runner.py:251`) | mechanism claim dropped; lesson cited as the evidence |
| E4 | Release can block up to 5 s | yes (`_broker_session.py:20, 289-295`) | §4 fatal/best-effort updated |
| F1 | Class 5 with hardening is correct | agree | unchanged |
| F2 | Task 6 (harness/fixture hygiene) is performative | yes (gc-scan at `weft_harness.py:1278-1289`; releasable check no-op off Windows) | task dropped; renumbered |
| F4 | 1(a)'s second half asserts a SimpleBroker property | agree | dropped |
| F5 | 1(b) slept on the wrong constant and violated sleep hygiene | yes (`_constants.py:711`) | replaced with write-then-`wait(2.0)` |
| F6 | Counting `sqlite3.connect` is acceptable evidence; reset after `build_context` | agree | Task 1 fixture notes |
| F7 | Resolve the "check" rows | yes (all closed except `result.py:339`) | §3 table resolved |
| F8 | Task 5 expected count is 2 | yes (`context.py:460-461`) | Task 5 updated |

### 2026-09-07 review dispositions

The current-state review reproduced the SQLite connection counts with
both commands run inside an isolated project: `status` 15 → 2 and
`task list` 8 → 1 for the persistent-handle plus held-lease experiment.
These support the diagnosis, not an implementation-completion claim.

| Finding | Disposition and verification obligation |
|---------|----------------------------------------|
| Corrupt config fails before lease acquisition, so the proposed test cannot prove release | Task 1(c) now uses a missing alias after a real broker read. The command was probed at `bcea628e`: exit 1 after one connection. Require the omitted-cleanup mutation after Task 3 with armed references retained to expose a retained core, then a passing restored test. A real-session probe showed why references matter: omitting explicit close but dropping the held list opened one new probe connection (finalizers masked the omission); retaining the list opened zero. |
| A fresh-project benchmark can accidentally resolve another target | Both commands run with working directory and `WEFT_CONTEXT` set to the same test project. |
| Annotated `Status` fails the plan-metadata contract and mismatches the index | Restored `Status: draft`; moved review/API notes into prose. Before revision, `tests/specs/test_plan_metadata.py` failed its normalized-metadata and index-status tests on this file. |

This revision changes plan text only. The §9 public-API decision and
implementation gates remain in force. Independent review of these
corrections is recorded in the program ledger's 2026-09-07 revision review.

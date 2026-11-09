# Task Connection Footprint on PostgreSQL

Status: draft
Source specs: docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]; docs/specifications/14-Python_API_Surfaces.md [PY-1]; docs/specifications/05-Message_Flow_and_State.md [MF-5]
Superseded by: none

Class: 5 (strategy B, atomic). Normative text in [SB-0.4] and [PY-1] changes
with the code. Hardening applies: `build_context` is shared by the CLI, the
client and task processes, and the result reader sits on the completion path
of every nested parent. Spec baseline: `08c2f87b` (`perf/admission-base`).

## 1. Goal

On PostgreSQL the scarce resource is connection count ([WT-4.2] "Process
concurrency, short transactions"). The 2026-11-08 admission benchmark
measured about 2.4 sessions per running task (270 sessions at 114 running
tasks). This plan first measures which process holds which connection in
each task phase. It then removes the Weft-owned connection churn that the
measurement finds, and it writes up as SimpleBroker follow-ups the levers
that belong to SimpleBroker ([WT-4.2] "Push a concern down when it belongs
lower"; [WT-2a.8]).

The measurement falsifies one premise of the brief. A supervising task
wrapper cannot release its data connection from Weft: SimpleBroker uses that
connection every one to two seconds, and SimpleBroker returns pooled
PostgreSQL connections only when a process session ends (section 4). The
steady held count per running task therefore does not change in this plan.
The Weft changes cut a nested parent's physical connection opens by about
40 percent and its schema-inspection statements by about 70 percent.

## 2. Source documents and guidance read

- `docs/program-theory.md` [WT-0], [WT-2] items 4–6, [WT-2a.5], [WT-2a.6],
  [WT-2a.8], [WT-4.1] to [WT-4.4], [ALT-WT-001], [ALT-WT-002], [ALT-WT-010],
  [ALT-WT-011], [ALT-WT-018].
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4], including
  the bounded observation paragraph changed by
  `docs/plans/2026-11-07-nested-admission-load-plan.md` ("Release the result
  reader's data connection between waits"; still fully intended).
- `docs/specifications/14-Python_API_Surfaces.md` [PY-1] (`build_context`
  side effects).
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]: waiting observers
  register task-state and standard control interests before discovery reads.
- SimpleBroker `docs/specs/16-python-library-api.md` [SB-API-3] (persistent
  Queue close and reuse; checkout return; `recycle_thread`) and [SB-API-14]
  (owner labels), read from `../simplebroker` at 8.7.2, the installed version.
- `docs/agent-context/decision-hierarchy.md` [DOM-15];
  `docs/agent-context/runbooks/writing-plans.md`;
  `docs/agent-context/runbooks/hardening-plans.md`; `AGENTS.md` §4.

## 3. Measured inventory (baseline `08c2f87b`)

### Method

A measurement-only `sitecustomize.py` on `PYTHONPATH` wrapped
`psycopg.Connection.connect`, `psycopg.Connection.close`, `Cursor.execute`,
`psycopg_pool.ConnectionPool._connect` and
`simplebroker_pg.runner.PostgresRunner._create_pool` in every process and
logged each physical open and close with its thread, owner label and Weft
call chain. A probe workload with the same shape as
`tests/helpers/admission_workload.py::admission_work` logged phase markers
(context, submit, sleep, wait). The run used `./bin/pytest-pg` (PostgreSQL 18,
project-scoped target through `.weft/broker.toml`, simplebroker 8.7.2,
simplebroker-pg 4.7.2): one leaf, two one-child roots and one depth-3 chain,
which is 9 tasks with 5 waiting parents (one leaf, two two-task families, one four-task chain). Neither probe file is committed.
To reproduce, recreate both from this description and run the probe under
`./bin/pytest-pg`.

Pool connections are created on psycopg_pool worker threads, LISTEN on
SimpleBroker's `simplebroker-pg-listener-*` thread, and target validation
directly on the calling thread. Those thread names classify every row below.

### Task wrapper (Consumer process), every task

| Phase | Connection | Lifetime | Opened per task |
| --- | --- | --- | --- |
| Process start to exit | LISTEN (SimpleBroker process-local listener) | whole task | 1 |
| First broker operation (`BaseTask._register_tid_state`) to exit | Pool data connection (simplebroker-pg runner pool: min 0, max 3; psycopg_pool `max_idle` 600 s) | whole task; 74–82 statements | 1 |
| First use of each persistent Queue facade (`_register_tid_state`, `_report_state_change`, `MultiQueueWatcher._start_strategy`, two `_queue_has_pending` checks, `Consumer._emit_single_output`, `Consumer._purge_stream_markers`) | Dedicated validation connection (`simplebroker_pg.validation.inspect_schema`, 5 catalog statements) | about 5 ms each | 7.1 mean |

While the worker runs, the wrapper's data connection is not idle. Every one
to two seconds (uniform jitter) it runs four `SELECT EXISTS(... messages
WHERE queue = ...)` pending checks. This is SimpleBroker's
`PollingStrategy` native-idle fallback poll:
`_native_idle_poll_interval = max(1.0, 10 * BROKER_MAX_INTERVAL)`, jittered
over one to two times that value. The observed gaps were 1.0 to 2.0 s.

### Parent worker (host-runner function worker that submits, then waits)

| Phase | Connections opened | Held |
| --- | --- | --- |
| `weft.client.connect()` → `build_context` → `_ensure_database` → `open_broker` | 1 validation + 1 data | closed at once |
| `client.submit()` (session + spawn facade) | 1 validation + 1 data | about 40 ms |
| `time.sleep` | 0 | none |
| `child.result()` subscription | 1 LISTEN | whole wait |
| each observation pass (`_iter_consuming_result`) | 1 validation + 1 data (new facade, new process session, new pool) | 10–30 ms; 11–13 statements |

Leaf workers open no connections.

The number of passes per wait, P, is one initial pass plus one per coalesced
wake. Wakes come from the child's own `weft.state.tasks.<child>` snapshots
and from its outbox and `ctrl_out`. [MF-5] requires the task-state
subscription. In the probe, P was 2–3 when the parent began waiting after its
child had started. P was 8 when one parent began waiting during its child's
startup snapshot burst. If the benchmark's 11.0 per task under `_iter_consuming_result` counts
physical opens, it matches P ≈ 5.5 at two opens per pass.

### Held sessions per running task

A running leaf task holds 2 sessions: the wrapper's LISTEN and its data
connection. A waiting parent holds 3, because its worker adds a LISTEN.
Transient pass and validation connections add only milliseconds of
occupancy. About half of the stress workload's 1,250 tasks are parents (about 650), so
this predicts about 2.5 sessions per running task. The benchmark measured
2.4.

### After this plan (same probe)

| Process | Baseline physical opens | After |
| --- | --- | --- |
| Parent worker, `connect()` | 2 | 0 |
| Parent worker, `submit()` | 2 | 2 |
| Parent worker, wait | 1 + 2P | 2 + P |
| Parent worker, total | 5 + 2P (16 at P = 5.5) | 4 + P (9.5 at P = 5.5) |
| Wrapper | 2 held + 7.1 validation | unchanged (SimpleBroker follow-ups) |

`./bin/pytest-pg` evidence. Per parent, the probe runs measured 2.0 → 0 opens
for the context and 2.6 → 1.0 validation opens per wait. The firing test reports a
waiting thread that opened 8–9 validation connections, one per pass, before
the change, and 1 after it.

## 4. Lever analysis against the theory

| Lever | Owner | Disposition |
| --- | --- | --- |
| Do not reopen the broker for a context inherited from the task's runtime identity | Weft (`weft/context.py`) | Done (task 1). The task already holds the launch target ([WT-4.1] "Keep an identity you already have"). SimpleBroker validates or initializes on first use ([WT-2a.8]). The wrapper already builds its context with `create_database=False`. |
| Reuse one validated reader facade across observation passes, releasing its lease after each pass | Weft (`weft/commands/_result_wait.py`) | Done (task 2). [SB-API-3] documents that a closed persistent Queue can be reused, and its next operation starts a new process session. [SB-0.4] already prefers warm facade reuse to repeated first-use validation. |
| Release the wrapper's data connection while it only supervises | SimpleBroker | Not possible in Weft (section 3): the native-idle fallback poll uses the connection every 1–2 s. [SB-API-3] states that `recycle_thread()` and `cleanup_connections()` "are not required to return completed PostgreSQL operation checkouts". The pool closes only when every lease on the process session is released. Emulating that would mean tearing down every wrapper Queue lease, the watcher session and the drive scope for each quiet period. That reimplements pool idle policy in Weft and breaks the task session ownership in [SB-0.4]. Follow-up SB-F1. |
| Raise `BROKER_MAX_INTERVAL` for tasks so the fallback poll is rarer | SimpleBroker setting | Rejected. The strategy owns the pass length ([WT-4.3] "A quiet task costs nothing an operator can see"; [ALT-WT-002]). The same value also sets the SQLite polling cadence. |
| Validate a project target once per process session, not once per facade | SimpleBroker | Follow-up SB-F2. |
| Keep the reader's process session across passes and drop only the physical connection | SimpleBroker primitive, then Weft | Follow-up SB-F3. It depends on SB-F1(b). |
| Let a parent's result wait use the wrapper's LISTEN instead of the worker's own | Weft (new channel) | Rejected here. It needs a new worker-to-wrapper wait protocol, which is a new concept ([WT-4.4] "As few concepts as possible"). |
| Stop subscribing to the child's task-state queue to cut wakes | Weft ([MF-5]) | Rejected. [MF-5] requires the subscription because terminal projection can first appear there. Fewer child snapshots (the task-traffic track) cut parent passes instead. |
| Hold the reader's data connection across waits again | Weft | Rejected. It would add one held session per waiting parent. [ALT-WT-001] says to shorten resource lifetime, and the 2026-11-07 plan measured why. |
| Per-task connection charge or budget | — | Rejected ([ALT-WT-001]). |

## 5. Invariants and constraints

- [WT-2a.5]: no broker handle or connection crosses a process. Unchanged.
- One owner per resource ([WT-4.2]). The consuming loop in
  `_iter_consuming_result` owns the reader and closes it through its
  resource scope on every exit path. Each pass closes the reader's lease
  (`contextlib.closing`) before `monitor.wait`.
- [SB-0.4]: the release-before-wait property and caller-owned session
  preservation are unchanged. A caller session on the same key keeps the
  process session alive, as before.
- [MF-5]: subscription before the first evidence read is unchanged. The
  reader is not part of the subscription, and the subscription is still
  entered before the first pass.
- Cleanup order: watchers close before their queues. The reader is not
  watched. Its scope callback is registered before the subscription lease,
  so it closes after the subscription. That close is a no-op unless a pass
  raised.
- `build_context` keeps eager broker creation for every explicit root,
  fallback root or configuration, and in every non-task process.
- No queue, payload, CLI, client API, result-settlement or timing contract
  changes. No new dependency, constant, cache or abstraction.

### Hidden couplings

1. Reuse after `Queue.close()` is SimpleBroker's documented contract
   ([SB-API-3]: "exactly as a persistent Queue closed and reused does today.
   Its next operation starts a new process session if no other lease kept
   the old one alive").
2. One validation per wait depends on SimpleBroker keeping its per-facade
   setup marker across close. That is SimpleBroker behavior, not a Weft
   contract. If SimpleBroker changes it, correctness is unaffected, and the
   new test's `waiting_thread_connections == 1` assertion fails. That
   assertion measures the resource property this plan claims, as the existing
   physical-connection tests in `tests/commands/test_observation_connections.py`
   do. It pins no version ([ALT-WT-018]).
3. The runtime-identity inheritance branch in `build_context` fires only
   when the caller passes no root, fallback root or configuration
   (`weft/context.py::build_context`, `inherit_runtime`). Task wrappers
   (`weft/core/launcher.py`) and host workers (`weft/core/runners/host.py`)
   install that identity.

### Comprehension questions (answer before editing)

1. Why can't Weft release the wrapper's data connection while the task is
   supervising? Expected: SimpleBroker's native-idle fallback poll runs
   pending checks on it every 1–2 s, and SimpleBroker returns pooled
   PostgreSQL connections only at process-session end.
2. What closes the result reader's data connection between waits?
   Expected: `Queue.close()` on the retained reader releases its
   process-session lease. With no other lease, SimpleBroker closes the
   runner pool and its connection. The next operation reacquires one.

## 6. Tasks

1. Runtime-inherited contexts do not reopen the broker.
   - Files: `weft/context.py` (`build_context`).
   - Change: guard `_ensure_database` with `not inherit_runtime`. Document
     the rule in the `create_database` argument.
   - Do not add a flag, a cache or a second resolution path.
   - Stop if any non-task caller reaches the `inherit_runtime` branch.
2. One reader facade per wait.
   - Files: `weft/commands/_result_wait.py` (`_iter_consuming_result`).
   - Change: in release mode (`release_while_waiting`), create one persistent
     reader Queue after the subscription names are known, and register its
     close on the outer resource scope. Enter each pass through
     `contextlib.closing(reader)`. Retain mode keeps `nullcontext` over the
     subscription's own queue.
   - Do not touch settlement, route replacement or subscription order.
   - Stop if a pass needs the data connection after `monitor.wait` begins.
3. Test (red before tasks 1 and 2, then green).
   - File: `tests/commands/test_observation_connections.py`.
   - Function: `test_runtime_parent_opens_no_context_broker_and_validates_its_reader_once`,
     with worker target `observe_runtime_parent_connections`.
   - A real parent task runs through the Manager, its wrapper and its
     worker. It submits a child (`tests.tasks.sample_targets:wait_for_file`)
     that finishes only after the parent's first quiet wait. The worker
     counts its own physical opens per phase and its non-listener connections
     still open at every `QueueChangeMonitor.wait`. Mock nothing in the broker
     or process path. On both backends, assert the nested result. On
     PostgreSQL, also assert 0 context connections, 0 data connections held
     at every wait, and 1 validation open on the waiting thread.
   - Red evidence: before task 1, `context_connections` was 2. After task 1
     and before task 2, `waiting_thread_connections` was 9, then 8, in two runs (one per pass).
4. Spec promotion (atomic with the code): [SB-0.4] bounded observation
   paragraph and [PY-1] `build_context` paragraph, with Related Plans
   backlinks and the [PY-1] implementation mapping.

## 7. Proposed spec delta (promoted atomically, strategy B)

### [SB-0.4] — paragraph beginning "The bounded observation owners"

The span from "each observation pass. It releases that pass's lease" through
"reacquires a data connection for fresh evidence." becomes:

Replacement (`docs/specifications/04-SimpleBroker_Integration.md`):

```markdown
each observation pass. It keeps one reader Queue for the whole wait and
closes that Queue's session lease after each pass, before waiting, and
preserves any caller-owned session. The next wake reacquires a data
connection through the same reader for fresh evidence, so SimpleBroker's
first-use facade validation runs once per wait, not once per pass.
```

The paragraph's coverage sentence also links this plan.

### [PY-1] — `build_context` side-effects paragraph

Insert after "resolution can still create the project root.":

Replacement (`docs/specifications/14-Python_API_Surfaces.md`):

```markdown
Inside a Weft
task process, a context inherited from the runtime identity (no explicit
root, fallback root, or configuration) does not open the broker to create it:
the task's launch target already exists, and each SimpleBroker Queue still
validates the target on first use.
```

## 8. Observable difference register

| Change | Class | Theory basis |
| --- | --- | --- |
| A runtime-inherited `build_context()` no longer opens the broker on PostgreSQL. SQLite already skipped an existing file. | C | [WT-4.1] keep an identity you already have; [WT-2a.8] SimpleBroker validates on first use; [WT-4.2] connection count is scarce |
| In a task process, a broker that became unreachable after launch now fails at the first queue operation (for example `submit`) instead of at `connect()`. It is the same failure, reported to the same caller. | C | [WT-4.4] no over-armoring; [ALT-WT-011] no hidden preflight |
| The result reader keeps one facade per wait, so first-use facade validation runs once per wait | C | [SB-0.4] warm facade reuse; [WT-4.2] one owner per resource |
| Physical opens per subsequent observation pass drop from 2 to 1, and 5 catalog statements per subsequent pass are removed (the first pass still validates and opens data) | C (measured) | [WT-4.4] measure, do not bake in inefficiency |
| The data connection is released before every wait | N | 2026-11-07 nested admission load plan |
| Held sessions per running task: 2 for a leaf, 3 for a waiting parent | N (unchanged; see SB-F1) | [ALT-WT-001] investigate resource lifetime |
| Queues, payloads, CLI, client API, settlement rules and deadlines | N | — |

## 9. Testing and verification

Per-task and final commands:

```bash
./.venv/bin/python -m pytest -n 4 -q <targets>
./bin/pytest-pg <targets>
./.venv/bin/ruff check .
./.venv/bin/ruff format --check <touched files>
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
```

Targets: `tests/commands/test_observation_connections.py`,
`tests/test_harness_pg_connections.py`,
`tests/commands/test_result_observation_connections.py`,
`tests/commands/test_spawn_observation_connections.py`,
`tests/commands/test_burst_result_wait.py`,
`tests/commands/test_consuming_result_collection.py`,
`tests/commands/test_result.py`, `tests/commands/test_result_activity_waits.py`,
`tests/commands/test_local_result_completion.py`,
`tests/commands/test_submission.py`, `tests/core/test_client.py`,
`tests/core/test_client_submission_facade.py`,
`tests/core/test_public_client_contracts.py`, `tests/context/test_context.py`,
`tests/core/test_runtime_identity.py`,
`tests/core/test_task_runtime_connections.py`.

The throughput gate is
`tests/system/test_admission_stress.py::test_nested_admission_sustains_a_large_backlog`,
which the coordinator runs. After deployment, look for fewer `client backend`
session starts per task in `pg_stat_activity` and unchanged held sessions per
running task.

## 10. Expected benchmark effect

The effect is small. Each parent opens about 6.5 fewer physical connections
at P = 5.5 (16 → 9.5) and runs about 27 fewer schema-inspection statements
(37.5 → 10). The stress
workload's roughly 650 parents therefore make about 4,000 fewer
PostgreSQL backend starts per run. Held sessions per running task do not
change, so the concurrency ceiling near 110 tasks does not move. Wall-clock
change is likely within run-to-run noise. A small gain may appear where
transient sessions inflate measured usage at the startup-window edge. If the
admission benchmark shows no change, that is consistent with this plan. The
held-slot win requires SB-F1.

## 11. SimpleBroker follow-ups

These follow-ups are proposals for SimpleBroker's own planning. Weft does not
gate on them ([WT-2a.8]).

- **SB-F1. Native-idle fallback poll and idle checkout release (PostgreSQL).**
  (a) With a healthy LISTEN waiter, the fallback poll is a backstop against
  lost notifications. Its cadence is `max(1.0, 10 * MAX_INTERVAL)` s with
  1–2x jitter, so every quiet task runs pending checks every 1–2 s on a
  pooled connection. Tie the backstop to listener health or a much longer
  interval, independent of `MAX_INTERVAL`. (b) Give the runner pool an idle
  policy or a public API that returns idle connections to the server. Options
  are a configurable short idle close, or `BrokerSession.release_idle()` for
  an owner entering a long quiet wait. Expected effect: one fewer held session
  per running task (about 2.4 → 1.4). That raises the PostgreSQL-limited
  ceiling from about 110 to about 190 running tasks in 270 public slots. Weft
  needs no change for a policy, or one call at `BaseTask`'s quiet wait for an
  API.
- **SB-F2. Per-facade project-target validation.** The first use of every
  persistent Queue on a project-scoped PostgreSQL target opens a dedicated
  connection and runs 5 catalog statements, even when its process session
  already holds a validated runner. Validate once per process session, or
  over the session runner's checkout. Expected effect: about 7.1 fewer opens
  per wrapper and 1–2 per parent worker, which is about 8–9 per task and about
  10,000 per 1,250-task run. It also removes about 25–40 ms of validation
  connections before each wrapper's first task-state snapshot.
- **SB-F3. Session-retaining release.** Once SB-F1(b) exists, the result
  reader can keep one process session and runner across passes and return
  only the physical connection. That removes per-pass pool construction (a
  new psycopg_pool and its worker threads per pass).

## 12. Rollback and rollout

Revert the commit. No queue, payload or persisted format changes. Old and new
processes can coexist. There is no rollout order and no one-way door.

## 13. Out of scope

- Wrapper facade consolidation, the Manager's per-reap broker opens, and
  resource-monitor scans (other tracks).
- Child snapshot volume (the task-traffic track). Each snapshot wakes a
  waiting parent for one pass: one physical open and about 12 statements
  after this plan.
- CHANGELOG entry. The coordinator writes it at landing, to avoid
  cross-track conflicts.

## 14. Cross-track notes

- task-traffic: every `weft.state.tasks.<tid>` snapshot wakes each parent
  waiting on that task ([MF-5]), so fewer snapshots directly reduce parent
  passes and connection opens.
- manager-reaping: the Manager also opens a fresh broker per reap, which this
  plan does not measure.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Fresh-eyes review (author)

Re-read as a zero-context engineer. Every cited path, symbol and section
resolves on this branch. The two code changes are local, add no abstraction
or configuration, and keep the owners they had. The register's only `D`
candidate, held sessions per running task, is `N`: this plan does not change
held sessions and says so. Remaining risk: hidden coupling 2 is a
SimpleBroker behavior the new test observes. That is recorded above, not
armored against.

## Review and execution record

### Cross-family review

Tier 1, Codex (codex exec -s read-only, reasoning effort high), 2026-11-08,
scoped-change review of plan+implementation at 4ee15456 against 08c2f87b.
Verdict: no blocker. No production defect; the reviewer confirmed the reader
closes before `monitor.wait()`, child-state observation stays intact, explicit
context inputs keep broker initialization, and the test is falsifiable.

| ID | Severity | Finding | Disposition |
| --- | --- | --- | --- |
| F1 | nit | §3 probe topology totals 9 tasks, not 10. | Accepted, fixed: §3 states 9 tasks and lists the families. |
| F2 | nit | §8 "opens per pass drop from 2 to 1" omits the first pass, which still validates and opens data. | Accepted, fixed: the register row now says subsequent passes. |

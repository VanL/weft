# Bounded Registry Connection Reuse

Status: draft
Source specs: docs/specifications/03-Manager_Architecture.md [MA-3]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]; docs/specifications/08-Testing_Strategy.md [TS-0]
Superseded by: none

Class: 4. Release investigation found reconnect churn inside a production
manager lifecycle wait; resource lifetime and runtime state reads are risky.

## Goal and Baseline

Baseline: 3ad5fc4b. Preserve manager lifecycle behavior while reusing the
connection of the registry queue already owned by each bounded operation.
The public process-wide lease decision in the separate
2026-09-02-cli-process-broker-session-plan.md remains out of scope.
The user reaffirmed the runtime invariant: tasks default to persistent broker
connections; transient use requires a specific inability to reuse the owned
connection. Connection lifetime must not extend transaction lifetime.

Full 12-worker PostgreSQL release testing failed with EADDRNOTAVAIL near 93%.
The endpoint had 14,977 TIME_WAIT sockets; macOS ephemeral ports span
49152..65535. PostgreSQL was healthy and below max_connections=300. A narrower
12-worker run of tests/tasks/test_task_monitor.py and
tests/tasks/test_liveness_monitor.py reproduced the error in 56 seconds.
Temporary real-connect counters attributed repeated connections to manager
registry reads during teardown. The manager stop wait retains its queue object,
but _registry_queue returns persistent=False, reconnecting on every scan.
Separately, the snapshot-history test seeds 1,050 rows using an ephemeral writer;
its call opened 2,117 connections. That writer is setup, not the reader under test.

## Reading and Constraints

Read weft/core/manager_runtime.py (_registry_queue, _snapshot_registry,
_await_manager_start_settlement, _await_manager_stop_confirmation),
weft/core/queue_wait.py, tests/core/test_manager.py,
tests/commands/test_observation_connections.py, tests/core/test_task_state.py,
and the installed SimpleBroker Queue/session and PostgreSQL runner code.
Why does holding a Queue object not necessarily retain its connection?
Which scope closes the watcher before the queue on success and failure?

Preserve exact target/config custody, full history traversal, fresh queue reads,
stale/unknown identity distinctions, stop/exit proof, all timeouts, production
process titles, and 12-worker concurrency. Do not add cached registry data,
skip validation, introduce global sessions, alter [PY-1] public APIs, or change OS TCP
settings. Keep real broker behavior in tests; instrumentation counts real
connections and does not substitute results. Every acquired persistent handle
must close on success, failure, and partial construction. Watchers close first.

## Steps

1. Add a real PostgreSQL regression for repeated bounded registry observation
   and closure, using existing connection-count test patterns. Prove repeated
   scans currently reconnect, then assert no new connections after warmup and
   explicit closure on success/failure. Keep existing lifecycle proof tests.
2. Reuse the existing queue/session APIs in _registry_queue and audit every
   owner for close paths, including QueueChangeMonitor construction failures.
   Seed the snapshot-history test through a persistent writer and close it
   before reader assertions, so setup cannot give readers a borrowed lease.
3. Rerun targeted PostgreSQL and SQLite tests, the minimized Monitor slice,
   then full release gates, Ruff check/format, mypy, and metadata checks.
   Compare real connection counts, not a wall-time threshold. Stop and replan
   if evidence requires changes to task-control observation or public custody.
4. Audit TaskMonitor, MonitorStore sidecars, LivenessMonitor, and their helpers.
   Route task operations through the existing persistent queue connection.
   Give MonitorStore an optional borrowed queue for task-owned calls; standalone
   bounded callers keep their existing connection ownership. Store close must
   never close a borrowed task queue. A maintenance clone owns its own queue
   handles, acquired on its worker thread and closed by its existing cleanup.
   Worker cleanup first recycles that thread's cached core using the public
   Queue.cleanup_connections API; reactor leases may keep the session alive.
   Do not pass an entered connection or sidecar transaction across threads.
   Keep every existing sidecar transaction boundary unchanged. Verify reuse,
   commit visibility to an independent reader, rollback, and cleanup with real
   brokers. Audit dynamic-family handles for bounded memory as well as sockets.
   Update [SB-0.4] and [SB-0.4a] with this user-confirmed lifetime invariant.
5. User expanded the scope to all task-runtime calls. Audit indirect helpers,
   Manager, LivenessMonitor, pruning, scanner/evidence paths, and extension
   tasks. Parent owns Monitor/store; Jason owns Manager/Liveness/control probes;
   Newton owns Monitor/pruning helper changes; Averroes independently inventories
   remaining callers. Report every retained transient exception with its reason.
   No blanket change to WeftContext defaults: non-task callers keep explicit
   ownership. Independent review and real broker regressions precede release.
   Internal helpers accept optional borrowed broker arguments so repeated calls
   do not even create new facades: SimpleBroker project initialization performs
   a schema check once per new facade, which still opens a separate connection.
   Hot-path verification therefore requires zero new physical connections after
   warmup, not merely fewer connections or persistent=True flags.
6. Post-fix Monitor/Liveness PostgreSQL run passes all 214 tests, but diagnostics
   still record 17,051 connections, concentrated in public task-stop observation
   during harness teardown. The wait owns persistent queues but metadata,
   mapping, and status helpers reopen connections each turn. Extend the same
   explicit borrowed-connection repair through those bounded command helpers.
   Preserve dynamic metadata/route refresh and exact completion proof. This is
   not approval for the separate process-global lease proposal.
   Apply the same ownership to positive-timeout terminal snapshot polling,
   which independently repeats history, mapping, and terminal-evidence reads.
7. Final caller inventory found equivalent task-specific loops in status-watch,
   result materialization, realtime events, submission reconciliation, and
   interactive completion. Extend existing bounded queue ownership through
   nested history, queue-presence, terminal-evidence, and result reads.
   McClintock owns tasks.py/status-watch and interactive completion; Newton
   owns result.py; Averroes owns events.py; parent owns submission reconciliation.
   Each slice adds real connection-count, fresh external-write, and closure
   regressions. Do not change public APIs, yield/wait semantics, result
   consumption, deadlines, or global CLI ownership.

## Investigation Evidence

### Bounded Command Control Observation Slice

Owner: command-slice implementer; core API additions remain with their existing
owners. Class 4 inherited. Scope is `weft/commands/tasks.py`,
`weft/commands/_task_history.py`, `weft/commands/system.py`,
`tests/commands/test_control_observation_connections.py`, and narrow existing
task-command doubles. Governing baseline remains this plan's baseline plus the
in-flight [SB-0.4] lifetime clarification. Implements
`10-CLI_Interface.md` [CLI-1.2.1], [CLI-1.2.3], [CLI-1.3],
`05-Message_Flow_and_State.md` [MF-3], [MF-5], and
`04-SimpleBroker_Integration.md` [SB-0.4]; preserves
`14-Python_API_Surfaces.md` [PY-1], [PY-2]. Parent owns spec backlinks/mapping
reconciliation and independent integration review. No new behavioral spec delta.

The existing `_ControlSurfaceResources` owns persistent queues and lends its
log queue/broker to metadata, mapping, pipeline, status, and Monitor fallback
reads. Public `task_status` retains its signature; one internal implementation
accepts borrowed resources. Nested manager selection, service-owner migration,
and claimed-outbox probes must also borrow, using owner-provided core APIs.
No global lease, cached observations, new cleanup policy, deadline changes,
transaction across a wait, or core runtime edits belong to this slice.

1. Prove repeated control observation reconnects with real PG connect counters.
2. Thread explicit loans through every nested reader; preserve history fallback,
   malformed-row handling, late metadata/custom route updates, and exact terminal
   evidence. Stop the watcher before releasing queues on return, failure,
   replacement, and partial construction. New queue names may incur bounded
   setup; unchanged surfaces must open zero connections after warmup.
3. Run focused tests with all 12 workers on PG and SQLite, targeted Ruff/mypy,
   and fresh-eyes review. Prove external writes remain visible and test watcher
   and queue cleanup failures. Do not run the full suite before parent approval;
   do not delegate, commit, or push. Rollback is this scoped helper/loan change
   only, without touching persisted data or other agents' changes.

Parent-authorized extension: `tasks.py::task_terminal_snapshot(timeout>0)` is
also a bounded observation owner. Retain one persistent log queue for the whole
call, lend its broker to `_load_taskspec_payload_bounded`, mapping, known-TID
evidence and status fallback, and leave read scopes before every sleep. Preserve
zero-timeout single reads, normalization, running/pending expiry, exact ack
targets, and Monitor fallback. Averroes owns optional broker forwarding in
`core/task_evidence.py`; command-slice tests count repeated physical PG connects
and prove fresh external writes and closure on success/failure.

Parent-authorized final observer extension: `tasks.py::watch_task_status` owns
persistent log/state queues and closes its monitor before either queue through
ExitStack, including partial construction and generator close. An internal
`_task_snapshot` lends the same broker/queue through status and TaskSpec
projection without changing public `task_snapshot` or watch signatures. Per-turn
read scopes exit before both yield and wait. Preserve the `(timestamp, status)`
last-seen key, terminal return, timeout raising, and subscription membership.
Verify zero physical opens after warmup, independent committed metadata updates,
explicit iterator close, and constructor/read/wait/cleanup failures. Audit every
remaining `tasks.py` loop and explicit transient queue before final handoff.

Command-slice verification (uncommitted handoff): the initial control regression
measured 25 new PG connections per repeated empty-history observation; the
terminal-snapshot regression measured 24 per poll (`[24, 48, 72]` after warmup).
Both now assert zero, alongside task/pipeline/future-TID/initialized-Monitor
variants. Independent-thread publications prove fresh metadata, newest-valid
state behind malformed tails, custom control/pipeline routes, and non-consuming
terminal acknowledgement targets. Seven control-resource cases verify LIFO
watcher-before-queue cleanup, partial construction, monitor-close failure and
queue-close failure; three terminal-observer cases verify expiry/read/sleep exits.
Existing test doubles changed only in `test_task_commands.py` and one
`test_status.py` manager-selection failure hook so their original oracles fire.

After `. ./.envrc`, the focused command set (`test_control_observation_connections`,
`test_task_commands`, `test_status`, `test_task_evidence`, and
`test_system_public_contract`, all under `tests/commands/`) passed with
`./.venv/bin/python -m pytest -n12`: 188 passed, six PG-counter skips.
`tests/commands/test_run.py -k terminal_snapshot` separately passed all four
Monitor-fallback cases at `-n12`. The same combined set through
`PYTEST_XDIST_AUTO_NUM_WORKERS=12 ./.venv/bin/python bin/pytest-pg` passed
198 tests in 16.57 seconds. Logs: `/tmp/weft-sqlite-control-reuse.log`,
`/tmp/weft-sqlite-terminal-fallback.log`, `/tmp/weft-pg-control-reuse.log`, and
the red terminal proof `/tmp/weft-pg-terminal-reuse-red.log`.
Ruff check/format and mypy passed for the three command modules and three touched
test modules. Averroes' independent terminal-owner review found no actionable
issues; parent owns the combined integration/release gate. No timeout changes,
concurrency reductions, core writes, commits, or pushes were made by this slice.

The MonitorStore per-method connection design dates to 72bc99dc (2026-05-16).
The 7f8b8040 sidecar migration (2026-06-10) retained it. This is an old defect,
not introduced by the current patch bump. Making the collation store mandatory
and later service/reconciliation changes increased its possible use; the exact
change that crossed the source-port exhaustion threshold is not isolated.

Regression evidence: repeated real PostgreSQL store operations and independent
commit visibility failed connection-count assertions before task queue borrowing
and passed after it, including a genuine worker thread. Borrowed and standalone
sidecar rollback tests pass. A real SQLite worker-core weakref remained live
after queue.close while the reactor lease survived; cleanup_connections before
close releases it without recycling the reactor core. These are lifetime and
correctness assertions, not throughput or elapsed-time gates.

The all-call audit also found two BaseTask ownership defects. Configured watched
queues absent from _queue_cache (PipelineTask events) were not closed; cleanup
now includes runtime queues with identity deduplication. Raw SQLite task targets
rebuilt a default context target and Config rather than retaining the actual
queue target and exact configuration snapshot. The raw-path branch now preserves
both, as the BrokerTarget branch already did. Real regressions demonstrate the
events lease leak and wrong custom SQLite path before repair.

Independent review found the same core-retention defect in general BaseTask
cleanup: the late watcher cleanup ran after queue.close had released its
session reference. The deduplicated queue loop now recycles each current-thread
core before closing leases, after final endpoint/streaming/control writes.
The inherited late cleanup only repeated the primary queue cleanup and was
removed. Real Consumer and failure-order regressions pass: 99 targeted tests
on each backend with 12 workers.

### Open Release Blocker: Exited Manual Driver Core

Review confirmed a separate supported lifecycle sequence still retains a core:
keep an independent persistent queue open; construct a Consumer; run public
process_once on a thread and join it; call public stop and cleanup on the main
thread. The task is closed and the driver thread exited, but its weak-referenced
broker core and SQLite connection remain alive and usable until the independent
keeper lease closes. No private task method is needed to cause this behavior.
Core Components [CC-2.2.1] explicitly allows idle manual-owner finalization
without waiting for the application thread. SimpleBroker's shared session
retains cores in a strong set and cleanup_connections only recycles the calling
thread's core. The owner-thread ordering repair above cannot evict the departed
thread's core. Publication is held pending owner approval for an upstream
SimpleBroker lifetime repair; no cross-thread eviction is added in Weft.

## Confirmed Exception

MonitorStore schema migration invokes raw-row absence checks while inside a
sidecar transaction. SimpleBroker forbids queue operations on that same core
until the transaction exits. Retain the bounded independent broker scope for
that callback rather than weakening migration atomicity or broker guards.

Each existing single-flight Monitor service work item runs on a fresh worker
thread with a fresh isolated clone. Its connection persists for that work item;
the thread closes its core before exit. A later worker cannot reuse the departed
thread's connection. Changing this into a persistent thread pool is outside this
repair and would require a separate ownership design.

## Integration Verification

After command-control observation borrowing, the same 12-worker Monitor and
Liveness PostgreSQL slice passed 214 tests in 35.44 seconds. Real connection
instrumentation counted 7,844 opens, versus 17,051 before the command repair.
This comparison is diagnostic, not a timing or throughput gate. Per-owner
regressions require zero physical opens after warmup and verify independent
commit visibility, rollback, and release.

An independent 148-test PostgreSQL slice exposed a pool destructor warning.
The exact run-ID test patched `retention_pruning.os.getpid`, changing the shared
stdlib module process-wide. A core created under the fake PID later saw the real
PID at finalization and triggered a false fork recovery. The repair isolates the
run-ID producer's OS view without changing the real broker's process identity.

## Review, Rollout, and Rollback

Independent reviewer: Newton, already investigating the owning paths; request
plan review before implementation and scoped diff review after correction.
Self-review: retaining a connection must not retain stale data or let a watcher
outlive it. Constructor failure cleanup is part of the review, not an optional
follow-up. Existing alternate proof tests remain necessary beside count tests.
Rollout is one patch release; no format or configuration migration. Rollback
reverts the scoped handle changes and restores the old connection churn, without
changing queue data. Verify full GitHub release gates and package publishing.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |

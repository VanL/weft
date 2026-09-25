# SimpleBroker Integration

This document describes how Weft uses SimpleBroker today.

The important architectural rule is simple: SimpleBroker owns queues and broker
targets; Weft adds task semantics, manager lifecycle, and operator-facing
workflow on top of that.

_Implementation mapping_: `weft/context.py`, `weft/commands/queue.py`,
`weft/commands/init.py`, `weft/commands/dump.py`, `weft/commands/load.py`,
`weft/commands/interactive.py`, `weft/_constants.py`, `weft/bootstrap.py`,
`weft/core/manager.py`, `weft/core/pipelines.py`, `weft/core/queue_wait.py`,
`weft/core/spawn_requests.py`,
`weft/core/tasks/multiqueue_watcher.py`, `weft/core/tasks/base.py`,
`weft/core/endpoints.py`, `weft/core/agents/provider_cli/settings.py`.

See also:

- planned companion:
  [`04A-SimpleBroker_Integration_Planned.md`](04A-SimpleBroker_Integration_Planned.md)
- current CLI contract:
  [`10-CLI_Interface.md`](10-CLI_Interface.md)
- implementation plan:
  [`docs/plans/2026-04-16-runtime-endpoint-registry-boundary-plan.md`](../plans/2026-04-16-runtime-endpoint-registry-boundary-plan.md)
- SimpleBroker 7.5.1 compatibility plan:
  [`docs/plans/2026-08-26-simplebroker-7-5-1-compatibility-plan.md`](../plans/2026-08-26-simplebroker-7-5-1-compatibility-plan.md)
- SimpleBroker 8.2 configuration plan:
  [`docs/plans/2026-09-14-simplebroker-8-2-configuration-plan.md`](../plans/2026-09-14-simplebroker-8-2-configuration-plan.md)
- SimpleBroker 8.0 upgrade plan:
  [`docs/plans/2026-08-28-simplebroker-8-upgrade-plan.md`](../plans/2026-08-28-simplebroker-8-upgrade-plan.md)
- explicit broker session lifetime plan:
  [`docs/plans/2026-09-15-explicit-broker-session-lifetimes-plan.md`](../plans/2026-09-15-explicit-broker-session-lifetimes-plan.md)
- cleanup policy convergence plan:
  [`docs/plans/2026-05-23-monitor-cleanup-policy-convergence-plan.md`](../plans/2026-05-23-monitor-cleanup-policy-convergence-plan.md)
- monitor policy progress contract plan:
  [`docs/plans/2026-05-24-monitor-policy-progress-contract-plan.md`](../plans/2026-05-24-monitor-policy-progress-contract-plan.md)

## SimpleBroker Features Leveraged by Weft [SB-0]

Weft intentionally reuses SimpleBroker's native behavior wherever possible.
That keeps the runtime smaller and easier to reason about.

### Queue Operations [SB-0.1]

Weft queue commands delegate to SimpleBroker rather than reimplementing queue
semantics.

Weft requires SimpleBroker 8.3.0 or newer. Installations using the optional
PostgreSQL backend require `simplebroker-pg` 4.3.0 or newer. These coordinated
floors provide backend API v9, ascending public-message-ID default selection,
surrogate-free SQL schema v6, bounded dump watermarks, immutable
invocation/handle configuration snapshots, typed queue result overloads,
public closeable queue iterator types, ID-cursor live peek pagination,
synchronized watcher lifecycle and owned-versus-borrowed queue cleanup, and
the public `BrokerSession` connect/queue/connection/recycle_thread/close and
context-manager lifetime contract used by Weft.

Upgrading a SQLite or PostgreSQL target from the v7 package line to v8 is a
coordinated cold cutover. Stop all v7 clients and sidecar transactions, take a
whole-target backup, install the 8.0.0/4.0.0 package pair, migrate and verify
once, and then restart only v8 clients. V7 and v8 clients must not share a
schema-v6 target. Rollback requires restoring the complete pre-migration target
before reinstalling the prior package pair.

_Implementation mapping_: `weft/commands/queue.py` delegates to
`simplebroker.commands`; `weft/context.py` injects the resolved broker target;
`weft/core/tasks/base.py` and `weft/core/tasks/multiqueue_watcher.py` build
task-local queue handles from the same broker target.

Related plan: [Explicit broker session lifetimes](../plans/2026-09-15-explicit-broker-session-lifetimes-plan.md).

Current consequences:

- queue creation is implicit on first write
- queue naming is Weft-owned, but queue mechanics are broker-owned
- queue commands work against the resolved broker target for the active context,
  not just against a SQLite file path
- queue-name enumeration uses SimpleBroker's names-only `list_queues()` API
  when counts are not needed; callers that need pending/claimed/total counts
  must use `list_queue_stats()`
- monitor runtime cleanup uses SimpleBroker's public multi-queue delete API for
  whole standard task-local control/reserved queue cleanup rather than
  backend-specific queue SQL

### Message IDs and Timestamps [SB-0.2]

SimpleBroker message IDs are durable and ordered. Weft relies on that instead
of generating a second ID space.

_Implementation mapping_: exact input normalization in
`weft/helpers/message_ids.py`, `weft/commands/load.py`, and
`weft/commands/queue.py`; explicit external projections in the owning
command/CLI, Monitor, pruning, and serve-log modules; runtime ID use in
`weft/core/tasks/base.py` and `weft/core/manager.py`.

Current use:

- spawn-request message IDs become task TIDs
- queue history is reconstructed from append-only broker data
- operator tooling can correlate task lifecycle to queue operations without a
  side database

SimpleBroker reserves message ID `0` as its lower-bound/checkpoint origin.
Weft may select legacy zero-ID rows for recovery where SimpleBroker permits it,
but it must not create or import a message with ID `0`.

SimpleBroker message IDs remain integers in Python, broker/backend columns,
Monitor relational message-ID columns, and internal process, control, and work
protocols. At an external JSON boundary, or in a Weft-owned exact-ID field
embedded in JSON text stored in a Monitor table, every non-null exact
SimpleBroker message ID is a 19-character ASCII decimal string produced by the
public `simplebroker.format_message_id` helper; null remains null. Monitor
readers immediately normalize those owned stored strings back to integers.
Counts, PIDs, TIDs, Unix-clock measurements, and opaque or internal JSON retain
their owned types. Formatting is explicit by field and semantic source; Weft
does not install a generic encoder or traverse arbitrary mappings by key name.

Weft-owned exact-ID input boundaries accept either an integer or the canonical
19-character string. They validate with the supported formatter, require a
string input to equal the formatter's canonical result, and immediately
normalize the accepted value to `int`. Range cursors retain their existing
contracts.

### Safe Patterns [SB-0.3]

Weft relies on SimpleBroker's queue primitives to express current safety
patterns:

- reserve/move semantics for in-flight work
- peek for non-destructive inspection
- JSON-safe payload handling for structured state and control messages

_Implementation mapping_: reservation and recovery wiring in
`weft/core/tasks/base.py`; watcher scheduling in
`weft/core/tasks/multiqueue_watcher.py`; queue passthrough in
`weft/commands/queue.py`.

Structured JSON stored inside broker messages is internal data, not an
external JSON formatting boundary. Task/control/PONG bodies, TaskSpecs,
manager service-owner payloads, pipeline events, diagnostics, and extension
payloads are not recursively rewritten. Only the explicitly owned external
fields named by the CLI and state-observation specs, plus the exact Monitor
table JSON-at-rest fields named in [SB-0.4a], use [SB-0.2] formatting.

The reason this matters is failure visibility. Weft wants failed or interrupted
work to remain inspectable rather than being silently discarded.

### Context Resolution and Watchers [SB-0.4]

Weft builds on SimpleBroker's broker-target resolution rather than assuming a
file-backed SQLite path.

_Implementation mapping_: `weft/context.py` (`build_context`,
`_resolve_root_and_target`, `WeftContext`), `weft/commands/load.py`,
`weft/core/tasks/multiqueue_watcher.py` (exact activity signatures,
`_apply_topology_mutation_on_owner()`, and
`PollingStrategy.replace_activity_waiter(...)` ownership),
`weft/core/queue_wait.py`; configuration declarations and restoration in
`weft/_constants.py` (WEFT_CONFIG_FIELDS, load_config,
_resolve_weft_config, resolve_runtime_config); JSON process transport in
`weft/core/launcher.py` (launch_task_process, _task_process_entry),
`weft/core/manager_runtime.py` (_build_manager_process_command), and
`weft/manager_process.py` (main, run_manager_process); process helper logging
configuration in `weft/helpers/__init__.py` (reload_config). Configuration
acceptance coverage is in `tests/system/test_constants.py`,
`tests/system/test_config_transport.py`, `tests/system/test_manager_process.py`,
and `tests/context/test_context.py`; native waiter acceptance coverage is
`test_postgres_background_dynamic_membership_rebinds_native_waiter` in
`tests/tasks/test_multiqueue_watcher.py`.

An explicitly entered `WeftClient` may retain one SimpleBroker
`BrokerSession` lease only for its exact resolved `WeftContext` target and
immutable Config identity. Same-context submission may borrow that lease;
alternate effective runtime roots use independent bounded sessions and are not
cached by the base client. Every submission still owns and exits its connection
operation before manager recovery or waits. SimpleBroker continues to own the
process-shared backend core and PostgreSQL pool, per-operation checkout return,
broken-connection replacement, fork-safe inherited-handle close, and final
shutdown after the last lease closes.

The bounded observation owners in `weft/commands/_result_wait.py`, `result.py`,
`events.py`, `run.py`, and `tasks.py` retain persistent queue leases until their
existing close/finally boundary. Regression coverage lives in
`tests/commands/test_observation_connections.py`; the repair evidence is recorded
in [the dependency suite repair plan](../plans/2026-09-09-updated-dependency-suite-repairs-plan.md).

Task control observation in `weft/commands/tasks.py` also lends its owned log
queue connection through `_task_history.py`, `system.py`, and manager/evidence
helpers. Each turn rereads metadata, routing, and completion evidence; connection
reuse does not cache those observations. Watchers close before their queues,
including failed construction and cleanup paths. Coverage lives in
`tests/commands/test_control_observation_connections.py` and
`tests/core/test_manager_runtime_connections.py`; the task and helper audit is
recorded in [the connection reuse repair plan](../plans/2026-09-14-bounded-registry-connection-reuse-plan.md).
The same loans cover status-watch and terminal-snapshot loops, submission
reconciliation in `_spawn_submission.py`, result materialization in `result.py`,
realtime following in `events.py`, and the interactive Monitor-store fallback
in `run.py`. Transactions end before waits and generator yields; a bounded
observer may retain an operation lease without retaining a transaction. Regression
coverage also lives in `test_spawn_observation_connections.py`,
`test_result_observation_connections.py`, and `test_event_observation_connections.py`
under `tests/commands/`.

Current behavior:

_Implementation mapping_: shared task inventory and driver ownership live in
`weft/core/tasks/base.py` and `weft/core/tasks/multiqueue_watcher.py`; Monitor
worker/store ownership lives in `weft/core/monitor/task_monitor.py` and
`weft/core/monitor/store.py`; bounded command observers live in the command
modules listed above.
Standalone fallback scopes are owned by `weft/core/control_probe.py`,
`manager_runtime.py`, `pruning/apply.py`, `queue_window.py`, and
`spawn_requests.py`.

- Tasks use persistent SimpleBroker `BrokerSession` ownership by default.
  Shared task infrastructure owns fixed queue leases and the executing thread's
  session scope; subclasses and helpers borrow that ownership. A complete task
  drive scope exits on the thread that used the cached core, on both normal and
  exceptional exit. Connection reuse spans turns, not SQL
  transactions: transactions end before waits, yields, external I/O, and thread
  handoff. Cleanup releases waiters and iterators before final queue writes and
  session exit. This ordering includes caller-owned observers with active
  operations on the same thread and process-session key, not just the task's own
  handles. Close failures follow the owning boundary's recording or propagation
  contract; Weft does not depend on a private upstream refusal type or report a
  refused close as success. Dynamic queue discovery and watcher membership must
  not retain every historical queue handle. Independent bounded connections
  remain valid where no owner exists or a documented operation cannot use the
  owned session.
- context resolution returns a `WeftContext` with a resolved broker target
- queue and broker helpers are created from that broker target
- file-backed and non-file-backed backends share the same normal runtime path
- `MultiQueueWatcher` shares one resolved target across its queues instead of
  constructing per-operation SQLite-only handles
- `MultiQueueWatcher` and `QueueChangeMonitor` call
  `simplebroker.create_activity_waiter_for_queues(...)` for queue fan-in waits
  and treat `None` as the portable polling fallback
- `MultiQueueWatcher` passes its fan-in waiter through SimpleBroker's watcher
  lifecycle hook rather than cloning the base watcher retry loop; Weft still
  owns queue membership, priority, and dispatch policy
- a running standalone `MultiQueueWatcher` changes native waiter membership
  only on its drive owner. The owner creates a replacement with
  `simplebroker.create_activity_waiter_for_queues(...)`, installs it through
  the public owner-confined `PollingStrategy.replace_activity_waiter(...)`
  seam, and closes the returned displaced waiter. The replacement seam keeps
  strategy data-version callbacks and local wake hints intact and accepts
  `None` for polling fallback. Weft does not call backend-specific listener
  APIs and does not use `PollingStrategy.start()` as a live replacement
- `MultiQueueWatcher` drives its retained SimpleBroker `PollingStrategy` for
  every backend. It installs the optional multi-queue activity waiter through
  the strategy lifecycle seam and never waits on that native waiter directly.
  A service timer is passed as the optional strategy deadline; polling
  fallback keeps its configured one-pass cadence rather than adopting a
  second Weft polling interval.
- Native notifications, SQLite `data_version` changes and local notifications
  are readiness hints. The owner performs a live watched-queue pending check
  before queue dispatch; local-hint consumption performs the same check so a
  same-connection self-write can activate an inactive watched queue. Quiet,
  empty, unrelated and timer-deadline returns do not assert durable work or
  reset useful-activity state. Zero-timeout local timer boundaries return
  without queue probes.

`build_context()` owns root selection: an explicit `spec_context` argument takes
precedence over the resolved Config's `CONTEXT` (`WEFT_CONTEXT` input). In the absence
of either, project discovery starts at `fallback_root` when supplied, otherwise CWD. A
discovered project owns the root; if none is found, the discovery start directory is the
root. This keyword changes the discovery anchor, not the broker-target precedence.
Queue/status helpers retain their supported explicit environment override behavior.

Automatic Weft context discovery searches upward only for the configured
Weft-scoped broker configuration (by default `.weft/broker.toml`). If none is
found, Weft resolves the current explicit root. Weft does not search parent
directories for an old SQLite database filename.

A missing `.weft/config.json` may be created with current defaults. An
existing unreadable file, malformed JSON document, or non-object document is
an error. Weft reports the error and does not replace or modify the file.
Weft-owned metadata writes described as atomic use a same-directory temporary
file and atomic replacement. Failure to publish the replacement propagates and
leaves any prior target bytes unchanged. A caller may suppress that error only
when its own documented output is advisory.

This backend-neutral path is why the current CLI uses per-command context
selection rather than a root-level `--dir` / `--file` targeting model.

Postgres operational contract: a long-lived Weft watcher should share one
backend activity-wait/listen path across its watched queues within a process.
Weft must not regress to one Postgres listener connection per watched queue.
Operators should still size Postgres for the number of concurrent Weft
processes, plus short-lived CLI/startup spikes. Production deployments should
give Weft a separate Postgres connection budget from the application pool, with
200-300 allowed connections as a practical starting range for bursty
multi-process workloads. If an external pooler is used, it must preserve the
backend notification semantics required by `LISTEN`/`NOTIFY`; pooling modes
that discard listener state are outside Weft's broker contract.

For optional Manager admission control, Weft may call the package-root
`simplebroker_pg.get_connection_stats(queue)` helper with an existing
persistent Manager queue. The helper returns a dictionary with exactly
`numbackends`, `max_connections`, `superuser_reserved_connections`, and
`reserved_connections`; callers use those keys and do not depend on field
order. `reserved_connections` is zero on Postgres versions that do not expose
that setting. The helper owns its SQL and result validation inside
`simplebroker_pg`; Weft does not duplicate backend SQL or open a
measurement-only connection. `numbackends` is the raw server-wide backend
count, including the probing connection and unrelated clients. It is therefore
conservative evidence for a soft admission bound, not an exact count of Weft
workers and not a connection lease or permit. Admission reads `numbackends` by
key as its single Postgres usage value. The operator-configured
`WEFT_ADMISSION_MAX_CONNECTIONS`, not the helper's `max_connections`,
`superuser_reserved_connections`, or `reserved_connections` values, controls
the Weft lane limits. Weft adds no prospective per-launch connection charge.

Implementation note for [SB-0.4]:
`weft/commands/submission.py::_submit_prepared_outcome` owns or borrows the
exact effective-context session and owns one connection operation across
enqueue and initial availability. That connection operation ends before
`ensure_manager_after_submission` receives the same-call observation and enters
recovery. `weft/client/_client.py::WeftClient` owns any retained lease and
never retains a checkout or alternate-root session. Standalone
`weft/core/manager_runtime.py::observe_manager_availability` owns its own bounded
session/connection. Coverage lives in `tests/core/test_manager_runtime_connections.py`.
See the [submission manager check cost plan](../plans/2026-09-17-submission-manager-check-cost-plan.md).

### Weft-Owned Operational Tables [SB-0.4a]

Most Weft runtime state is queue-shaped, but Weft may keep narrow
non-queue operational tables beside SimpleBroker tables when the state is a
derived read model rather than queue data. The current example is the
TaskMonitor durable collation store:

Task-owned stores borrow the owner's `BrokerSession` and enter a short sidecar
context for each operation. `MonitorStore.close()` does not close that borrowed
owner. Standalone store operations retain a bounded independent fallback.
Migration raw-row checks retain their separate-connection exception inside a
sidecar transaction: SimpleBroker prohibits queue operations on the core
holding that transaction.
Implementation: `MonitorStore._sidecar_session`, `_raw_message_is_absent`, and
the TaskMonitor store-opening paths. Audit and repair evidence lives in the
[connection-reuse plan](../plans/2026-09-14-bounded-registry-connection-reuse-plan.md)
and the [explicit session lifetime plan](../plans/2026-09-15-explicit-broker-session-lifetimes-plan.md).

Monitor relational message-ID and checkpoint columns remain integers. Exact
broker IDs embedded in Monitor-owned JSON text are canonical strings at rest:
checkpoint metadata `value_json.message_id`, collation
`lifecycle_json.message_id`, nested pipeline
`lifecycle_json.checkpoint.message_id`, collation
`bookkeeping_json.last_message_id`, deferred
`body_json.subject.message_id`, `body_json.monitor.message_id`,
`body_json.monitor.first_message_id`, `body_json.monitor.last_message_id`,
nullable `body_json.monitor.terminal_message_id`,
`body_json.observations.message_id`, and every
`body_json.observations.message_ids[]`. Reads normalize those exact fields back
to integers before they enter Python domain objects. Other task, state,
resource, diagnostic, and extension mappings remain opaque and are not
traversed. External Monitor JSONL is projected at the final write or
durable-deferred handoff boundary, and deterministic lifetime-report identity
is computed before that projection.

Schema 6 requires the following named table columns and key structures. The
displayed column order is descriptive, not a physical-layout contract. Monitor
SQL names every selected and inserted column; physical column ordinal position
must not affect schema acceptance, reads, writes, or migration.

- `weft_monitor_meta`: `key`, `value_json`, `updated_at_ns`; primary key
  `key`.
- `weft_monitor_task_collations`: `context_key`, `tid`, `name`, `runner`,
  `parent_tid`, `role`, `status`, `terminal_seen`, `terminal_event`,
  `terminal_status`, `terminal_message_id`, `return_code`, `first_message_id`,
  `last_message_id`, `first_seen_at_ns`, `last_seen_at_ns`, `started_at_ns`,
  `completed_at_ns`, `taskspec_summary_json`, `state_json`, `lifecycle_json`,
  `resources_json`, `diagnostics_json`, `bookkeeping_json`,
  `reserved_probe_needed`, `summary_emitted_at_ns`, `raw_deleted_at_ns`,
  `suspect_reason`, `suspect_at_ns`, `disposition_reason`,
  `disposition_at_ns`, `task_control_deleted_at_ns`,
  `reserved_cleanup_checked_at_ns`, `orphan_raw_recovery_checked_at_ns`,
  `updated_at_ns`; primary key `context_key, tid`.
- `weft_monitor_task_messages`: `context_key`, `tid`, `queue_name`,
  `message_id`, `event`, `status`, `observed_at_ns`,
  `selected_for_delete_at_ns`, `deleted_at_ns`; primary key
  `context_key, tid, message_id`.
- `weft_monitor_deferred_writes`: `context_key`, `report_id`, `record_type`,
  `body_json`, `created_at_ns`, `updated_at_ns`, `first_external_error`,
  `last_external_error`, `attempt_count`, `last_attempt_at_ns`,
  `flushed_at_ns`; primary key `context_key, report_id`.

Startup validates each required column by name and by backend-compatible
storage family, capacity, and nullability. Equivalent backend type spellings
are accepted. Raw DDL text, physical column position, default-expression
spelling for proven literal defaults, and catalog row order are not schema
contracts.

The required semantic type groups are:

- Text: meta `key`, `value_json`; collation `context_key`, `tid`, `name`,
  `runner`, `parent_tid`, `role`, `status`, `terminal_event`,
  `terminal_status`, `taskspec_summary_json`, `state_json`, `lifecycle_json`,
  `resources_json`, `diagnostics_json`, `bookkeeping_json`, `suspect_reason`,
  `disposition_reason`; message `context_key`, `tid`, `queue_name`, `event`,
  `status`; deferred `context_key`, `report_id`, `record_type`, `body_json`,
  `first_external_error`, `last_external_error`.
- 64-bit integer: meta `updated_at_ns`; collation `terminal_message_id`,
  `first_message_id`, `last_message_id`, `first_seen_at_ns`, `last_seen_at_ns`,
  `started_at_ns`, `completed_at_ns`, `summary_emitted_at_ns`,
  `raw_deleted_at_ns`, `suspect_at_ns`, `disposition_at_ns`,
  `task_control_deleted_at_ns`, `reserved_cleanup_checked_at_ns`,
  `orphan_raw_recovery_checked_at_ns`, `updated_at_ns`; message `message_id`,
  `observed_at_ns`, `selected_for_delete_at_ns`, `deleted_at_ns`; deferred
  `created_at_ns`, `updated_at_ns`, `last_attempt_at_ns`, `flushed_at_ns`.
- Ordinary integer: collation `terminal_seen`, `return_code`,
  `reserved_probe_needed`; deferred `attempt_count`.

SQLite accepts TEXT affinity for the text group and INTEGER affinity for both
integer groups. PostgreSQL accepts `text` or unbounded `character varying` for
text, `bigint` for the 64-bit group, and `integer` or `bigint` for the ordinary
integer group.

The following columns must accept SQL `NULL` because current writers may pass
`None`: collation `name`, `runner`, `parent_tid`, `role`, `status`,
`terminal_event`, `terminal_status`, `terminal_message_id`, `return_code`,
`first_seen_at_ns`, `last_seen_at_ns`, `started_at_ns`, `completed_at_ns`,
`summary_emitted_at_ns`, `raw_deleted_at_ns`, `suspect_reason`, `suspect_at_ns`,
`disposition_reason`, `disposition_at_ns`, `task_control_deleted_at_ns`,
`reserved_cleanup_checked_at_ns`, and
`orphan_raw_recovery_checked_at_ns`; message `event`, `status`,
`observed_at_ns`, `selected_for_delete_at_ns`, and `deleted_at_ns`; deferred
`first_external_error`, `last_external_error`, and `flushed_at_ns`. Every other
required column must be `NOT NULL`. SQLite's canonical `key TEXT PRIMARY KEY`
reports nullable through its catalog; primary-key verification owns that one
backend-specific exception.

An additional column is accepted only when named Monitor inserts may safely
omit it because it is nullable, identity-backed, or has a catalog default that
is provably a non-NULL SQL literal. An unproved default expression, a
non-nullable generated column, and `NOT NULL DEFAULT NULL` are incompatible;
each can still make a named insert fail. Operator-added triggers and `CHECK`
constraints remain outside Weft's schema-repair responsibility.

Schema 6 deliberately retains the two version-5 delete-state columns in the
task-message table. `selected_for_delete_at_ns` has no current reader or writer;
retaining it avoids adding a backend-specific column drop or table rebuild to
the single supported migration edge. `deleted_at_ns` has no current writer, but
the version-5 migration reads it to identify old tombstones and strict
version-6 verification rejects any non-null value. Neither column authorizes a
normal-cycle cleanup or compatibility lane. Physical removal requires a future
explicit schema version; version-6 startup must not remove or repair them.

The required schema-6 secondary indexes are:

- `idx_weft_monitor_collations_reserved_cleanup` on
  `weft_monitor_task_collations` (`context_key`, `reserved_probe_needed`,
  `reserved_cleanup_checked_at_ns`, `last_message_id`)
- `idx_weft_monitor_collations_disposition_terminal` on
  `weft_monitor_task_collations` (`context_key`, `terminal_seen`,
  `disposition_at_ns`, `last_message_id`)
- `idx_weft_monitor_collations_control_cleanup` on
  `weft_monitor_task_collations` (`context_key`, `terminal_seen`,
  `summary_emitted_at_ns`, `task_control_deleted_at_ns`,
  `disposition_at_ns`, `last_message_id`)
- `idx_weft_monitor_collations_orphan_recovery` on
  `weft_monitor_task_collations` (`context_key`, `raw_deleted_at_ns`,
  `orphan_raw_recovery_checked_at_ns`, `last_message_id`)
- `idx_weft_monitor_collations_disposition_open` on
  `weft_monitor_task_collations` (`context_key`, `disposition_at_ns`,
  `last_message_id`)
- `idx_weft_monitor_deferred_pending` on `weft_monitor_deferred_writes`
  (`context_key`, `flushed_at_ns`, `created_at_ns`)

Each required name must resolve to the listed table, non-unique, non-partial
B-tree form, and ordered columns. PostgreSQL also requires the index to be
catalog-valid and ready. Additional non-unique indexes do not invalidate the
schema solely by existing. A unique secondary index is incompatible when its
key imposes a stronger constraint than the table primary key. A non-partial
B-tree copy of the exact ordered primary-key columns is redundant and accepted
only when its equality semantics also match the primary index: SQLite key
collations, or PostgreSQL operator classes and collations, must be identical.

Schema-6 creation also continues to create these four non-required legacy
indexes so a newly created v6 store remains acceptable to v0.9.95-v0.9.97
during rollback:

- `idx_weft_monitor_collations_terminal` on `weft_monitor_task_collations`
  (`context_key`, `terminal_seen`, `raw_deleted_at_ns`, `completed_at_ns`)
- `idx_weft_monitor_collations_last_seen` on
  `weft_monitor_task_collations` (`context_key`, `last_seen_at_ns`)
- `idx_weft_monitor_collations_reserved_probe` on
  `weft_monitor_task_collations` (`context_key`, `reserved_probe_needed`,
  `last_seen_at_ns`)
- `idx_weft_monitor_messages_tid` on `weft_monitor_task_messages`
  (`context_key`, `tid`)

Current validation does not require or reject those four legacy indexes.
Stopping their creation or removing an existing copy requires a future schema
version.

These tables are Monitor-owned and versioned. They are derived from
`weft.log.tasks`; they are not exposed through queue commands and do not
replace SimpleBroker queue semantics. The child message table is a temporary
pending-reference table, not a queue clone: once the corresponding raw broker
row is deleted or reconciled as already absent, the Monitor physically deletes
the child row. Reserved-queue cleanup proof for terminal non-completed families
is also stored on the collation row as
`reserved_cleanup_checked_at_ns`; it is set after the standard reserved queue is
deleted or proved already absent, and left unset on probe/delete errors. The
Monitor may create, verify, and migrate only these Monitor tables through the
supported version edge inside an already initialized Weft broker database. It
must use the resolved `WeftContext` and broker target; it must not parse DSNs,
rediscover a different database target, provision Postgres, or create the
broker database itself. Broker queue rows still go through public SimpleBroker
queue APIs; Monitor-table SQL is allowed only for the Monitor-owned tables
listed above.

Monitor schema version 6 has one migration edge. A newly created store writes
version 6. Existing Monitor tables with no version metadata may be initialized
as version 6 only when every Monitor-owned table is empty; non-empty
unversioned stores fail. Version 5 migrates transactionally to version 6.
Version 6 verifies the required tables, semantic required-column definitions
independent of physical order, ordered primary keys, and required named-index
shapes before reading owned data. It performs no schema DDL and does not
recreate a missing required object. Only the new/empty path and the version 5
migration may create or alter Monitor schema objects. Versions below 5 and
above 6 fail as unsupported. There is no generic "lower than current" version
advance.

The version 5 to 6 migration rewrites only the explicitly owned JSON
message-ID fields. It also upgrades pending version 5 deferred external
envelopes from external schema version 1 to 2 without traversing opaque
payloads. Before any DDL, version-5 preflight accepts fresh and release-evolved
physical column orders and read-only verifies the meta, collation, and child
message tables, their named migration inputs, and their primary keys. An
already-present deferred-write table is verified at the same point. Migration
preparation may then create an absent deferred-write table, create missing
schema-6 indexes, and drop the obsolete delete-state index. It must not create
a missing data-bearing base table or add a missing base column. Obsolete
child-message tombstones are physically removed after
their parent is verified and a public exact-ID queue probe, including claimed
rows, proves the corresponding raw row absent. A present raw row or probe
error fails and rolls back the migration; the migration does not delete raw
queue rows. The migration also drops the obsolete exact
`idx_weft_monitor_messages_deleted` index. A version 5 parent already marked
raw-deleted while live child refs survive has that marker reset solely by the
migration. The schema version advances only after every rewrite and
normalization succeeds in the same transaction. Checkpoint-prefixed metadata
must contain canonical `message_id`. Ordinary version 6 readers accept only
the canonical stored form. Startup fails on malformed owned data; there is no
tolerant current reader or normal-cycle old-release repair lane.

Monitor repair handles only states reachable from the current writer and
current cleanup transactions, including interrupted exact deletion,
pre-checkpoint gaps, pending deferred writes, and forced-process residue. Data
written by an older Monitor schema is handled by its schema migration, not by
a permanent normal-cycle compatibility lane.

_Implementation mapping_: `weft/core/monitor/store.py` uses the resolved
`WeftContext` and owns table access; `weft/core/monitor/sql.py` owns SQL
templates/builders and validates code-owned identifiers; `weft/core/tasks/base.py`
builds task contexts with `create_database=False`; `weft/core/monitor/task_monitor.py`
treats store unavailability as operational degradation instead of changing task
execution. Optional external task-log JSONL emission is file output owned by
`weft/core/monitor/external_log.py`; it is not queue data and does not change
SimpleBroker's queue semantics.

### Runtime Endpoint Registry State [SB-0.5]

Named endpoint discovery is stored as Weft-owned runtime state on ordinary
broker queues.

_Implementation mapping_: `weft/_constants.py`
`WEFT_ENDPOINTS_REGISTRY_QUEUE`; `weft/core/endpoints.py`;
`weft/core/tasks/base.py` `register_endpoint_name()` and
`unregister_endpoint_name()`.

Current contract:

- `weft.state.endpoints` stores task-owned JSON records keyed by ordinary task
  TIDs
- each record points at ordinary task-local queues rather than introducing a
  second transport
- endpoint state is runtime-only and is excluded from dump/load with the rest
  of the `weft.state.*` soft-state queues
- endpoint resolution and stale-owner pruning use ordinary broker APIs and
  queue-visible runtime state; there is no backend-specific SQL coupling
- names are project-local. Weft does not expose a cross-context or global
  service namespace

## Project Context and Directory Scoping

Weft uses SimpleBroker project discovery with Weft-specific scoping defaults.
The project root comes from an explicit context override or from upward search
for Weft's configured project-config path only. SQLite target filenames are not
discovery markers. The Weft metadata directory is materialized at that resolved
root for Weft-owned artifacts. Its default name is `.weft/`, and
`WEFT_DIRECTORY_NAME` may override that default. The default Weft broker config
is `.weft/broker.toml`.

_Implementation mapping_: `weft/context.py` (`build_context`,
`_resolve_root_and_target`, `WeftContext`), `weft/commands/init.py`
(`cmd_init`).

Root selection follows [SB-0.4]. Explicit roots are used directly. Automatic discovery
searches upward from the selected fallback/CWD anchor for the configured broker-config
path and name; SQLite filenames are not discovery markers. Weft-owned metadata is
materialized at the resulting root when requested.

At the selected root, delegate broker selection to
`simplebroker.target_for_directory()`: project broker config first, then applicable
configured backend selection, then directory-local SQLite fallback. Backend
configuration does not independently choose a Weft artifact root. `DEFAULT_DB_LOCATION`
and `PROJECT_SCOPE` retain the behavior of this explicit-directory API and do not choose
or relocate the Weft root.

Current boundary notes:

- `WEFT_*` inputs are resolved through `load_config()` once into unprefixed
  configuration keys and reused by Weft-owned context resolution
- `WEFT_DIRECTORY_NAME` sets the Weft-owned metadata directory name before
  discovery; `.weft/` remains the default when it is unset
- Weft maps the configured metadata-directory name onto SimpleBroker's
  project-config discovery keys. By default the Weft broker config path is
  `.weft/broker.toml`, not root `.broker.toml`
- An absolute `WEFT_PROJECT_CONFIG_PATH` selects the broker configuration and target only.
  Its parent does not become the Weft artifact root. Without an explicit root argument or
  resolved `CONTEXT`, the selected discovery anchor (`fallback_root` or CWD) remains the
  root for this case.
- the metadata directory's `config.json` file is project metadata, not a broker
  target source; it may carry the project-local autostart default used by
  `build_context()`
- the metadata directory's `agents.json` file is project-local agent settings, not a broker target
  source; current shipped entries are the `provider_cli.providers` executable
  defaults, and Weft may also write those defaults when it learns them
- the metadata directory's `agent-health.json` file is advisory
  agent-runtime health metadata, not a broker target source
- TaskSpec `metadata` is caller-owned runtime metadata, not a broker target
  source

Current project structure:

```text
project-root/
├── .weft/              # default; WEFT_DIRECTORY_NAME may override
│   ├── broker.toml        # optional Weft-scoped broker target config
│   ├── config.json        # project metadata, including optional autostart
│   ├── agents.json        # optional project-local agent settings
│   ├── agent-health.json  # advisory agent-runtime observations
│   ├── autostart/         # created when autostart is enabled
│   ├── outputs/
│   ├── logs/
│   ├── tasks/             # stored task specs, when present
│   └── pipelines/         # stored pipeline specs, when present
└── project files...
```

The reason for this shape is operator clarity. Even when the broker backend is
not file-backed, the configured Weft metadata directory remains the visible
project home for Weft-owned artifacts.

Builtin task helpers are different. They are shipped read-only with the Weft
package rather than copied into the metadata directory during project init.
Local stored task specs under the metadata directory's `tasks/` namespace may
shadow builtin task helpers with the same name.

Current agent-settings and delegated-runtime boundary:

- the metadata directory's `agents.json` file is project-local agent settings.
  In the current shipped
  implementation it stores `provider_cli.providers` executable defaults and
  other explicit provider-cli launch defaults when the TaskSpec does not pin
  them directly
- the metadata directory's `agent-health.json` file is observed metadata
  written by Weft after real
  delegated calls. It is advisory only and never treated as startup truth
- neither file changes the core queue/state model or broker resolution. They
  are project-scoped runtime artifacts alongside other metadata-directory
  contents

## Current Context API

`build_context()` is the canonical entry point for selecting a root,
materializing the configured Weft metadata directory, and resolving the broker
target.

Related plan:
- `docs/plans/2026-04-16-configurable-weft-directory-name-plan.md`

_Implementation mapping_: `weft/context.py` (`build_context`,
`WeftContext.queue`, `WeftContext.broker`); `weft/_constants.py`
(load_config, resolve_runtime_config, WEFT_CONFIG_FIELDS);
`weft/core/launcher.py` (launch_task_process, _task_process_entry);
`weft/core/manager_runtime.py` (_build_manager_process_command);
`weft/manager_process.py` (main, run_manager_process); `weft/commands/init.py`;
`weft/commands/interactive.py`;
`weft/core/manager.py`; `weft/core/pipelines.py`; `weft/core/queue_wait.py`;
`weft/core/spawn_requests.py`; `weft/core/tasks/base.py`;
`weft/core/tasks/multiqueue_watcher.py`; `weft/bootstrap.py` for
optional pre-import `WEFT_ENV_FILE` loading before CLI callers reach
`load_config()`.

Current contract:

- `build_context(...)` resolves the project root and broker target
- `build_context(..., config=...)` lets an embedding app reuse a preloaded
  Weft config instead of forcing a fresh environment read
- `load_config(overrides=...)` is the canonical way for an embedding app to
  compile explicit `WEFT_*` overrides into the same canonical
  config shape that CLI and env-driven Weft use
- Weft extends SimpleBroker's `DEFAULT_CONFIG` with `ConfigField` entries for its added or changed settings, each carrying a default, description, and validator. `load_config()` calls the public `resolve_config()` with prefix `WEFT`; broker defaults and validators that Weft does not change belong to SimpleBroker. New upstream fields require no mirrored Weft inventory or schema guard. Ambient `BROKER_*` values never tune Weft, and resolution does not mutate the environment or upstream declarations.
- In-process configuration is a read-only `Config` with uppercase unprefixed keys. Explicit overrides and environment inputs use `WEFT_*` names; the former `BROKER_*` override aliases and prefixed runtime keys are removed. Invalid earlier source values warn and may be replaced by valid later overrides; invalid final values raise `InvalidConfigError`. Weft retains its own cross-field project-path, external-log-mode, and PostgreSQL target-shape rules.
- Contexts and SimpleBroker handles share the resolved Config snapshot. Mutable runtime policy copies retain the same unprefixed keys. Process transport uses SimpleBroker's public `serialize_config()` JSON representation. Receiving processes use `deserialize_config(..., defaults=WEFT_CONFIG_DEFAULTS)` through `resolve_runtime_config()` to restore read-only values and namespace with locally imported declarations, without rereading environment or TOML. Validator callables and their import paths are not part of the JSON payload. Weft retains its PostgreSQL target-shape check after restoration. Omitted launch config is resolved in the parent before spawning. Receiving imports must not resolve ambient configuration; the task entry binds helper logging/debug policy from the restored snapshot before loading the task class. Standalone helpers may load their configuration lazily on first use. The private manager serve-active flag is not loaded from the environment.
- SimpleBroker owns serialized watcher startup/stop cleanup and treats an
  ordinary exception raised by an error handler as terminal after cleanup.
  Weft does not swallow or replace that terminal callback failure and does not
  duplicate the upstream watcher lifecycle.
- CLI entry points honor `WEFT_ENV_FILE` before importing the full CLI, so env
  values loaded from that file participate in the ordinary `load_config()` and
  `build_context()` path. The env file fills missing process env values only;
  it does not override explicit supervisor or shell environment values.
- `WeftContext.queue(name)` returns a queue bound to the resolved broker target
- `WeftContext.broker()` opens a broker handle for backend-native operations
- callers should work with broker targets, queue helpers, and context objects,
  not with guessed database file paths
- command and helper code that already has a `WeftContext` should construct
  queues through `WeftContext.queue()` rather than open-coding `Queue(...)`
- CLI wait surfaces that already know which queues they are waiting on should
  reuse SimpleBroker's queue-native waiting path (for example multi-queue
  activity waiters and `QueueWatcher` fallback) rather than layering Weft-owned
  sleep loops on top of queue peeks
- direct `Queue(...)` construction in command-layer code is reserved for
  explicit low-level edges that do not carry a `WeftContext`, such as the
  interactive queue client that owns its own task-local inbox lifecycle
- `weft queue` and `weft status` helpers also honor `WEFT_CONTEXT` as an
  explicit project-root override before falling back to discovery

`CONTEXT` is a Weft Config declaration with default `None` and external name
`WEFT_CONTEXT`. It accepts a string or `None`; an empty string means absent. Nonempty
path text is preserved during config loading; home expansion and relative-path
resolution happen when building the context. Supplied Config/mapping inputs do not read
ambient environment, including for `CONTEXT`. A supplied snapshot without `CONTEXT` has
no root override. JSON transport restores this field using the receiver's local
declarations, without serializing validators.

`build_context(..., fallback_root=...)` accepts an optional discovery anchor. Existing
creation flags retain their effects and do not promise side-effect-free resolution.
Configuration and metadata errors remain errors, not reasons to select a different
broker.

## CLI Integration and Initialization

Stateful CLI commands operate within an existing project via `--context`. The
project initializer is different: `weft init [DIRECTORY]` creates or selects the
project root itself.

_Implementation mapping_: `weft/commands/init.py`, `weft/context.py`,
`weft/commands/queue.py`, `weft/commands/system.py`, `weft/cli/run.py`.

Current rules:

- `weft init` defaults to the current directory
- `weft init /some/path` initializes a different root explicitly
- `weft init` does not accept `--context`
- commands that operate inside an existing project use `--context`

This is why `init` mirrors `git init` rather than mirroring every other
stateful command.

## Queue Command Delegation

Raw queue commands delegate to SimpleBroker with context injection. Endpoint
resolve and alias helpers stay Weft-owned but run against the same
context-bound queues.

Current implications:

- raw queue mechanics stay aligned with SimpleBroker
- Weft-specific value comes from project discovery, aliases, and task/runtime
  conventions
- endpoint resolution, aliasing, and broadcast/watch convenience remain
  Weft-owned layers over the same context-bound broker queues
- direct broker maintenance and import/export flows can still rely on
  backend-native behavior

## Operational Notes

- Queue payload size is bounded by the active broker's message limit.
- Weft's large-output handling is a task-runtime feature, not a generic queue
  passthrough feature.
- Weft dump/load uses SimpleBroker `simplebroker-dump` v1 NDJSON and preserves
  included broker message IDs through SimpleBroker's public import path. The
  header `last_ts` and message `id` fields are canonical 19-character strings
  when written. Those message IDs are task IDs for spawn requests, so an
  import path that cannot perform exact-ID import must fail before writes
  begin rather than silently allocate new message IDs.
- Load accepts a canonical string or an exact JSON integer token for header
  `last_ts` and message `id`, validates and immediately normalizes either form
  to an internal integer, and rebuilds canonical string records for
  SimpleBroker apply. Message ID `0` is rejected during validation before
  aliases or messages are written; header `last_ts=0` remains valid. Dump
  version 1 is unchanged and no compatibility-writer branch is added.
- SimpleBroker dump v1 header `last_ts` is an inclusive export bound and a
  destination allocation floor. Every message record must satisfy
  `0 < id <= last_ts`. Load advances the destination high-water to at least
  `last_ts` after replay, including for a header-only dump, so later generated
  IDs are greater than the source floor. Weft validates the bound before
  destination writes, preserves the original header while filtering
  runtime-only records, and delegates skew enforcement and durable floor
  advancement to public `load_lines()`.
- Spawn-request submission writes generated and caller-supplied TIDs through
  SimpleBroker's public `insert_messages()` API rather than rewriting the
  TaskSpec TID.
- Command layers must not hand-write backend SQL for queue rows.
- `weft system load` uses backend-aware apply behavior; file-backed sqlite
  contexts can use snapshot rollback, while non-file-backed backends report
  partial-apply risk if a failure happens after writes begin.

## Scope Boundary

Future context-management commands, cross-context bridges, and explicit
connection-pooling designs are tracked in the companion doc:

- [`04A-SimpleBroker_Integration_Planned.md`](04A-SimpleBroker_Integration_Planned.md)

## Related Plans

- [Client-owned submission session reuse](../plans/2026-09-25-client-owned-submission-session-plan.md) - reuses SimpleBroker's process-shared session machinery without retaining operation checkouts.

- [Watcher Reactor Restoration Plan](../plans/2026-09-17-watcher-reactor-restoration-plan.md) - makes the retained `PollingStrategy` the one backend-neutral wake arbiter and defines notifications as hints validated against live watched-queue state.

- [Django context resolution owned by Weft](../plans/2026-09-14-django-core-context-resolution-plan.md)

- [`docs/plans/2026-08-28-simplebroker-8-upgrade-plan.md`](../plans/2026-08-28-simplebroker-8-upgrade-plan.md)
- [`docs/plans/2026-08-25-manager-admission-control-plan.md`](../plans/2026-08-25-manager-admission-control-plan.md)
- [`docs/plans/2026-08-25-monitor-schema-semantic-validation-plan.md`](../plans/2026-08-25-monitor-schema-semantic-validation-plan.md)
- [`docs/plans/2026-08-24-simplebroker-7-4-1-compatibility-plan.md`](../plans/2026-08-24-simplebroker-7-4-1-compatibility-plan.md)
- [`docs/plans/2026-08-13-simplebroker-7-3-dump-watermark-plan.md`](../plans/2026-08-13-simplebroker-7-3-dump-watermark-plan.md)
- [`Canonical Contract And Dead Code Cleanup Plan`](../plans/2026-08-10-canonical-contract-and-dead-code-cleanup-plan.md)
- [`docs/plans/2026-08-10-simplebroker-7-json-message-id-boundary-plan.md`](../plans/2026-08-10-simplebroker-7-json-message-id-boundary-plan.md)
- [`docs/plans/2026-08-10-interactive-session-lifecycle-refactor-plan.md`](../plans/2026-08-10-interactive-session-lifecycle-refactor-plan.md)
- [`docs/plans/2026-08-10-result-observation-and-control-transition-refactor-plan.md`](../plans/2026-08-10-result-observation-and-control-transition-refactor-plan.md)
- [`docs/plans/2026-07-31-simplebroker-6-api-migration-plan.md`](../plans/2026-07-31-simplebroker-6-api-migration-plan.md)
- [`docs/plans/2026-07-10-postgresql-dynamic-native-waiter-rebind-plan.md`](../plans/2026-07-10-postgresql-dynamic-native-waiter-rebind-plan.md)
- [`docs/plans/2026-06-11-simplebroker-dump-load-adoption-plan.md`](../plans/2026-06-11-simplebroker-dump-load-adoption-plan.md)
- [`docs/plans/2026-06-01-critical-review-remediation-plan.md`](../plans/2026-06-01-critical-review-remediation-plan.md)
- [`docs/plans/2026-05-25-monitor-dead-task-catchup-convergence-plan.md`](../plans/2026-05-25-monitor-dead-task-catchup-convergence-plan.md)
- [`docs/plans/2026-05-20-monitor-collation-table-retirement-plan.md`](../plans/2026-05-20-monitor-collation-table-retirement-plan.md)
- [`docs/plans/2026-05-20-simplebroker-api-adoption-plan.md`](../plans/2026-05-20-simplebroker-api-adoption-plan.md)
- [`docs/plans/2026-05-20-monitor-reactor-worker-refactor-plan.md`](../plans/2026-05-20-monitor-reactor-worker-refactor-plan.md)
- [`docs/plans/2026-05-16-task-log-external-logging-and-retention-policy-plan.md`](../plans/2026-05-16-task-log-external-logging-and-retention-policy-plan.md)
- [`docs/plans/2026-05-16-monitor-store-hardening-and-layering-plan.md`](../plans/2026-05-16-monitor-store-hardening-and-layering-plan.md)
- [`docs/plans/2026-04-16-autostart-hardening-and-contract-alignment-plan.md`](../plans/2026-04-16-autostart-hardening-and-contract-alignment-plan.md)
- [`docs/plans/2026-04-14-config-precedence-and-parsing-alignment-plan.md`](../plans/2026-04-14-config-precedence-and-parsing-alignment-plan.md)
- [`docs/plans/2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md`](../plans/2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md)
- [`docs/plans/2026-04-14-builtin-taskspecs-and-spec-resolution-plan.md`](../plans/2026-04-14-builtin-taskspecs-and-spec-resolution-plan.md)
- [`docs/plans/2026-05-05-simplebroker-multiqueue-waiter-integration-plan.md`](../plans/2026-05-05-simplebroker-multiqueue-waiter-integration-plan.md)
- [`docs/plans/2026-05-13-early-env-file-bootstrap-plan.md`](../plans/2026-05-13-early-env-file-bootstrap-plan.md)
- [`docs/plans/2026-05-16-monitor-durable-collation-store-plan.md`](../plans/2026-05-16-monitor-durable-collation-store-plan.md)
- [`docs/plans/2026-05-18-reactive-task-loop-hot-probe-plan.md`](../plans/2026-05-18-reactive-task-loop-hot-probe-plan.md)

## Related Documents

- [`00-Overview_and_Architecture.md`](00-Overview_and_Architecture.md)
- [`03-Manager_Architecture.md`](03-Manager_Architecture.md)
- [`05-Message_Flow_and_State.md`](05-Message_Flow_and_State.md)
- [`10-CLI_Interface.md`](10-CLI_Interface.md)

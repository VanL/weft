# SimpleBroker Integration

This document describes how Weft uses SimpleBroker today.

The important architectural rule is simple: SimpleBroker owns queues and broker
targets; Weft adds task semantics, manager lifecycle, and operator-facing
workflow on top of that.

_Implementation mapping_: `weft/context.py`, `weft/commands/queue.py`,
`weft/commands/init.py`, `weft/commands/load.py`,
`weft/core/tasks/multiqueue_watcher.py`, `weft/core/tasks/base.py`,
`weft/core/endpoints.py`, `weft/core/agents/provider_cli/settings.py`.

See also:

- planned companion:
  [`04A-SimpleBroker_Integration_Planned.md`](04A-SimpleBroker_Integration_Planned.md)
- current CLI contract:
  [`10-CLI_Interface.md`](10-CLI_Interface.md)
- implementation plan:
  [`docs/plans/2026-04-16-runtime-endpoint-registry-boundary-plan.md`](../plans/2026-04-16-runtime-endpoint-registry-boundary-plan.md)
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

Weft requires SimpleBroker 7.0.0 or newer. Installations using the optional
PostgreSQL backend require `simplebroker-pg` 3.5.2 or newer. These paired
floors provide the supported public message-ID formatter and matching backend
contract.

_Implementation mapping_: `weft/commands/queue.py` delegates to
`simplebroker.commands`; `weft/context.py` injects the resolved broker target;
`weft/core/tasks/base.py` and `weft/core/tasks/multiqueue_watcher.py` build
task-local queue handles from the same broker target.

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
`weft/core/queue_wait.py`; native acceptance coverage is
`test_postgres_background_dynamic_membership_rebinds_native_waiter` in
`tests/tasks/test_multiqueue_watcher.py`.

Current behavior:

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
- when the backend returns a native activity waiter, Weft waits on that waiter
  and treats a `True` result as a queue-discovery hint; it must not perform a
  pre-wait SQL pending scan on every wait cycle
- when no native activity waiter is available, Weft may perform a bounded
  positive-timeout pending precheck as the fallback polling path. Zero-timeout
  local timer wakes still return immediately without queue probes
- queue and status command helpers also honor `WEFT_CONTEXT` as an explicit
  project-root override before they fall back to discovery

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

### Weft-Owned Operational Tables [SB-0.4a]

Most Weft runtime state is queue-shaped, but Weft may keep narrow
non-queue operational tables beside SimpleBroker tables when the state is a
derived read model rather than queue data. The current example is the
TaskMonitor durable collation store:

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

Schema 6 has this exact ordered table structure:

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

Schema 6 deliberately retains the two version-5 delete-state columns in the
task-message table. `selected_for_delete_at_ns` has no current reader or writer;
retaining it avoids adding a backend-specific column drop or table rebuild to
the single supported migration edge. `deleted_at_ns` has no current writer, but
the version-5 migration reads it to identify old tombstones and strict
version-6 verification rejects any non-null value. Neither column authorizes a
normal-cycle cleanup or compatibility lane. Physical removal requires a future
explicit schema version; version-6 startup must not remove or repair them.

The exact schema-6 secondary-index inventory whose names begin
`idx_weft_monitor_` is:

- `idx_weft_monitor_collations_terminal` on `weft_monitor_task_collations`
  (`context_key`, `terminal_seen`, `raw_deleted_at_ns`, `completed_at_ns`)
- `idx_weft_monitor_collations_last_seen` on
  `weft_monitor_task_collations` (`context_key`, `last_seen_at_ns`)
- `idx_weft_monitor_collations_reserved_probe` on
  `weft_monitor_task_collations` (`context_key`, `reserved_probe_needed`,
  `last_seen_at_ns`)
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
- `idx_weft_monitor_messages_tid` on `weft_monitor_task_messages`
  (`context_key`, `tid`)
- `idx_weft_monitor_deferred_pending` on `weft_monitor_deferred_writes`
  (`context_key`, `flushed_at_ns`, `created_at_ns`)

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
Version 6 verifies the exact required table, ordered-column, primary-key, and
secondary-index structure before reading owned data. It performs no schema
DDL and does not recreate a missing object. Only the new/empty path and the
version 5 migration may create or alter Monitor schema objects. Versions below
5 and above 6 fail as unsupported. There is no generic "lower than current"
version advance.

The version 5 to 6 migration rewrites only the explicitly owned JSON
message-ID fields. It also upgrades pending version 5 deferred external
envelopes from external schema version 1 to 2 without traversing opaque
payloads. Obsolete child-message tombstones are physically removed after
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

Current discovery rules:

1. start from the current working directory or explicit `--context`
2. discover the enclosing project root using SimpleBroker project scoping with
   Weft's configured project-config path/name
3. materialize Weft-owned directories under the configured Weft metadata
   directory when needed
4. resolve the active broker target for that project

Current broker target precedence:

1. choose the project root from explicit `--context` / `spec_context` or from
   SimpleBroker auto-discovery
2. for an explicit root, delegate to `simplebroker.target_for_directory()`:
   the configured Weft-scoped broker config first, then env-selected
   non-sqlite backend synthesis, then sqlite fallback rooted at that directory
3. for auto-discovery, search upward for the configured Weft-scoped broker
   config. If none exists, use current environment backend selection for
   explicit-root resolution at the current working directory
4. if auto-discovery finds nothing, Weft falls back to explicit-root resolution
   at the current working directory

Current boundary notes:

- `WEFT_*` broker aliases are translated through `load_config()` once and then
  reused by Weft-owned context resolution
- `WEFT_DIRECTORY_NAME` sets the Weft-owned metadata directory name before
  discovery; `.weft/` remains the default when it is unset
- Weft maps the configured metadata-directory name onto SimpleBroker's
  project-config discovery keys. By default the Weft broker config path is
  `.weft/broker.toml`, not root `.broker.toml`
- an absolute `BROKER_PROJECT_CONFIG_PATH` selects the broker configuration and
  target only. Without an explicit `spec_context`, the current working
  directory remains `WeftContext.root` and owns the Weft metadata directory;
  the absolute configuration file's parent does not become the Weft root
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
`WeftContext.queue`, `WeftContext.broker`); `weft/bootstrap.py` for
optional pre-import `WEFT_ENV_FILE` loading before CLI callers reach
`load_config()`.

Current contract:

- `build_context(...)` resolves the project root and broker target
- `build_context(..., config=...)` lets an embedding app reuse a preloaded
  Weft config instead of forcing a fresh environment read
- `load_config(overrides=...)` is the canonical way for an embedding app to
  compile explicit `WEFT_*` and `BROKER_*` overrides into the same canonical
  config shape that CLI and env-driven Weft use
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

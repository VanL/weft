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

Current use:

- spawn-request message IDs become task TIDs
- queue history is reconstructed from append-only broker data
- operator tooling can correlate task lifecycle to queue operations without a
  side database

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

The reason this matters is failure visibility. Weft wants failed or interrupted
work to remain inspectable rather than being silently discarded.

### Context Resolution and Watchers [SB-0.4]

Weft builds on SimpleBroker's broker-target resolution rather than assuming a
file-backed SQLite path.

_Implementation mapping_: `weft/context.py` (`build_context`,
`_resolve_root_and_target`, `WeftContext`), `weft/commands/load.py`,
`weft/core/tasks/multiqueue_watcher.py`, `weft/core/queue_wait.py`.

Current behavior:

- context resolution returns a `WeftContext` with a resolved broker target
- queue and broker helpers are created from that broker target
- file-backed and non-file-backed backends share the same normal runtime path
- `MultiQueueWatcher` shares one resolved target across its queues instead of
  constructing per-operation SQLite-only handles
- `MultiQueueWatcher` and `QueueChangeMonitor` call
  `simplebroker.create_activity_waiter_for_queues(...)` for queue fan-in waits
  and treat `None` as the portable polling fallback
- when the backend returns a native activity waiter, Weft waits on that waiter
  and treats a `True` result as a queue-discovery hint; it must not perform a
  pre-wait SQL pending scan on every wait cycle
- when no native activity waiter is available, Weft may perform a bounded
  positive-timeout pending precheck as the fallback polling path. Zero-timeout
  local timer wakes still return immediately without queue probes
- queue and status command helpers also honor `WEFT_CONTEXT` as an explicit
  project-root override before they fall back to discovery

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

- `weft_monitor_meta`
- `weft_monitor_task_collations`
- `weft_monitor_task_messages`

These tables are Monitor-owned and versioned. They are derived from
`weft.log.tasks`; they are not exposed through queue commands and do not
replace SimpleBroker queue semantics. The child message table is a temporary
pending-reference table, not a queue clone: once the corresponding raw broker
row is deleted or reconciled as already absent, the Monitor physically deletes
the child row. If parent collation state was already marked raw-deleted while
child refs still remain, a bounded repair pass uses the same public exact-delete
path to clear or reconcile those child refs. A bounded recovery pass may use
public SimpleBroker message search and exact-delete APIs to clear legacy raw
`weft.log.tasks` rows for terminal Monitor families whose child refs were
already removed by an older release. When that recovery pass proves no raw
broker rows remain, it records that completed probe in the Monitor collation row
so the same family does not stay on the recovery hot path. Reserved-queue cleanup
proof for terminal
non-completed families is also stored on the collation row as
`reserved_cleanup_checked_at_ns`; it is set after the standard reserved queue is
deleted or proved already absent, and left unset on probe/delete errors. The
Monitor may create, verify, and
additively migrate only these Monitor tables inside an already initialized
Weft broker database. It
must use the resolved `WeftContext` and broker target; it must not parse DSNs,
rediscover a different database target, provision Postgres, or create the
broker database itself. Broker queue rows still go through public SimpleBroker
queue APIs; Monitor-table SQL is allowed only for the Monitor-owned tables
listed above.

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
The project root comes from an explicit context override or from SimpleBroker's
upward project search using Weft's configured project-config and sqlite target
paths. The Weft metadata directory is materialized at that resolved root for
Weft-owned artifacts. Its default name is `.weft/`, and `WEFT_DIRECTORY_NAME`
may override that default. The default Weft broker config is
`.weft/broker.toml`.

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
3. for auto-discovery, delegate to `simplebroker.resolve_broker_target()`:
   upward Weft-scoped broker config first, then upward legacy sqlite discovery
   using the configured default DB name, then env-selected non-sqlite backend
   synthesis
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

_Implementation mapping_: `weft/context.py` (`build_context`, `get_context`,
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
- `get_context(...)` is a convenience wrapper
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
`weft/commands/queue.py`, `weft/commands/status.py`, `weft/cli/run.py`.

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
- Weft dump/load import preserves included broker message timestamps through
  SimpleBroker's public `import_messages()` API. Those timestamps are task IDs
  for spawn requests, so an import path that cannot perform exact-timestamp
  import must fail before writes begin rather than silently allocate new
  message IDs.
- Spawn-request submission writes generated TIDs through SimpleBroker's public
  `write_reserved_message()` API. If a caller supplies a valid external TID
  that was not generated by the destination broker, Weft uses
  `import_messages()` for that exact-ID spawn row rather than rewriting the
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

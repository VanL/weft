# SimpleBroker Integration

This document describes how Weft uses SimpleBroker today.

The important architectural rule is simple: SimpleBroker owns queues and broker
targets; Weft adds task semantics, manager lifecycle, and operator-facing
workflow on top of that.

_Implementation mapping_: `weft/context.py`, `weft/commands/queue.py`,
`weft/commands/init.py`, `weft/commands/load.py`,
`weft/core/tasks/multiqueue_watcher.py`, `weft/core/tasks/base.py`.

See also:

- planned companion:
  [`04A-SimpleBroker_Integration_Planned.md`](04A-SimpleBroker_Integration_Planned.md)
- current CLI contract:
  [`10-CLI_Interface.md`](10-CLI_Interface.md)
- implementation plan:
  [`docs/plans/2026-04-16-runtime-endpoint-registry-boundary-plan.md`](../plans/2026-04-16-runtime-endpoint-registry-boundary-plan.md)

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
`weft/core/tasks/multiqueue_watcher.py`.

Current behavior:

- context resolution returns a `WeftContext` with a resolved broker target
- queue and broker helpers are created from that broker target
- file-backed and non-file-backed backends share the same normal runtime path
- `MultiQueueWatcher` shares one resolved target across its queues instead of
  constructing per-operation SQLite-only handles

This backend-neutral path is why older CLI surfaces like global `--dir` and
`--file` are no longer the right mental model.

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

Weft uses broker-scoped project discovery. The project root comes from an
explicit context override or from SimpleBroker's upward project search.
`.weft/` is materialized at that resolved root for Weft-owned artifacts, but
it is not the broker-target source of truth.

_Implementation mapping_: `weft/context.py` (`build_context`,
`_resolve_root_and_target`, `WeftContext`), `weft/commands/init.py`
(`cmd_init`).

Current discovery rules:

1. start from the current working directory or explicit `--context`
2. discover the enclosing project root using SimpleBroker project scoping
3. materialize Weft-owned directories under `.weft/` when needed
4. resolve the active broker target for that project

Current broker target precedence:

1. choose the project root from explicit `--context` / `spec_context` or from
   SimpleBroker auto-discovery
2. for an explicit root, delegate to `simplebroker.target_for_directory()`:
   `root/.broker.toml` first, then env-selected non-sqlite backend synthesis,
   then sqlite fallback rooted at that directory
3. for auto-discovery, delegate to `simplebroker.resolve_broker_target()`:
   upward `.broker.toml` first, then upward legacy sqlite discovery using the
   configured default DB name, then env-selected non-sqlite backend synthesis
4. if auto-discovery finds nothing, Weft falls back to explicit-root resolution
   at the current working directory

Current boundary notes:

- `WEFT_*` broker aliases are translated through `load_config()` once and then
  reused by Weft-owned context resolution
- `.weft/config.json` is project metadata, not a broker target source; it may
  carry the project-local autostart default used by `build_context()`
- `.weft/agents.json` is delegated-agent launch config, not a broker target
  source
- TaskSpec `metadata` is caller-owned runtime metadata, not a broker target
  source

Current project structure:

```text
project-root/
├── .weft/
│   ├── agents.json        # optional explicit delegated-agent launch config
│   ├── agent-health.json  # optional advisory delegated-provider observations
│   ├── outputs/
│   ├── logs/
│   └── broker metadata and runtime artifacts
└── project files...
```

The reason for this shape is operator clarity. Even when the broker backend is
not file-backed, `.weft/` remains the visible project home for Weft-owned
artifacts.

Builtin task helpers are different. They are shipped read-only with the Weft
package rather than copied into `.weft/` during project init. Local stored task
specs under `.weft/tasks/` may shadow builtin task helpers with the same name.

Current delegated-agent boundary:

- `.weft/agents.json` is explicit user-authored project config. It may select
  delegated executable paths or other launch defaults when the TaskSpec does
  not pin them directly
- `.weft/agent-health.json` is observed metadata written by Weft after real
  delegated calls. It is advisory only and never treated as startup truth
- neither file changes the core queue/state model. They are project-scoped
  runtime artifacts alongside other `.weft/` metadata

## Current Context API

`build_context()` is the canonical entry point for selecting a root,
materializing `.weft/` directories, and resolving the broker target.

_Implementation mapping_: `weft/context.py` (`build_context`, `get_context`,
`WeftContext.queue`, `WeftContext.broker`).

Current contract:

- `build_context(...)` resolves the project root and broker target
- `get_context(...)` is a convenience wrapper
- `WeftContext.queue(name)` returns a queue bound to the resolved broker target
- `WeftContext.broker()` opens a broker handle for backend-native operations
- callers should work with broker targets, queue helpers, and context objects,
  not with guessed database file paths
- command and helper code that already has a `WeftContext` should construct
  queues through `WeftContext.queue()` rather than open-coding `Queue(...)`
- CLI wait surfaces that already know which queues they are waiting on should
  reuse SimpleBroker's queue-native waiting path (for example `QueueWatcher`
  and backend-native activity waiters when available) rather than layering
  Weft-owned sleep loops on top of queue peeks
- direct `Queue(...)` construction in command-layer code is reserved for
  explicit low-level edges that do not carry a `WeftContext`, such as the
  interactive queue client that owns its own task-local inbox lifecycle

## CLI Integration and Initialization

Stateful CLI commands operate within an existing project via `--context`. The
project initializer is different: `weft init [DIRECTORY]` creates or selects the
project root itself.

_Implementation mapping_: `weft/commands/init.py`, `weft/context.py`,
`weft/commands/queue.py`, `weft/commands/status.py`, `weft/commands/run.py`.

Current rules:

- `weft init` defaults to the current directory
- `weft init /some/path` initializes a different root explicitly
- `weft init` does not accept `--context`
- commands that operate inside an existing project use `--context`

This is why `init` mirrors `git init` rather than mirroring every other
stateful command.

## Queue Command Delegation

Weft queue commands are thin wrappers over SimpleBroker commands with context
injection.

Current implications:

- queue behavior stays aligned with SimpleBroker
- Weft-specific value comes from project discovery, aliases, and task/runtime
  conventions
- direct broker maintenance and import/export flows can still rely on
  backend-native behavior

## Operational Notes

- Queue payload size is bounded by the active broker's message limit.
- Weft's large-output handling is a task-runtime feature, not a generic queue
  passthrough feature.
- `weft system load` uses backend-aware apply behavior; file-backed sqlite
  contexts can use snapshot rollback, while non-file-backed backends report
  partial-apply risk if a failure happens after writes begin.

## Scope Boundary

Future context-management commands, cross-context bridges, and explicit
connection-pooling designs are tracked in the companion doc:

- [`04A-SimpleBroker_Integration_Planned.md`](04A-SimpleBroker_Integration_Planned.md)

## Related Plans

- [`docs/plans/2026-04-16-autostart-hardening-and-contract-alignment-plan.md`](../plans/2026-04-16-autostart-hardening-and-contract-alignment-plan.md)
- [`docs/plans/2026-04-14-config-precedence-and-parsing-alignment-plan.md`](../plans/2026-04-14-config-precedence-and-parsing-alignment-plan.md)
- [`docs/plans/2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md`](../plans/2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md)
- [`docs/plans/2026-04-14-builtin-taskspecs-and-spec-resolution-plan.md`](../plans/2026-04-14-builtin-taskspecs-and-spec-resolution-plan.md)

## Related Documents

- [`00-Overview_and_Architecture.md`](00-Overview_and_Architecture.md)
- [`03-Manager_Architecture.md`](03-Manager_Architecture.md)
- [`05-Message_Flow_and_State.md`](05-Message_Flow_and_State.md)
- [`10-CLI_Interface.md`](10-CLI_Interface.md)

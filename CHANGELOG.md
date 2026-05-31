# Changelog

## 0.9.73 - 2026-05-30

### Changed

- Changed TaskMonitor cleanup progress so FIFO age scans that select eligible
  rows and then stop at the first too-young row report base-for-now instead of
  leaving catch-up pending. Candidate selection and exact deletion behavior are
  unchanged.
- Changed realtime task events so a terminal state derived from a final
  snapshot still emits the public `state` event before the result/end events.
- Raised the SimpleBroker dependency floor to 4.0.1 so broker handle close and
  finalization no longer run process-wide garbage collection in monitor cleanup
  hot paths.

### Fixed

- Fixed queue-history scans to explicitly close SimpleBroker generator handles
  when callers stop early, preventing process-shared broker leases from
  lingering across manager probes, result waits, and cleanup scans.
- Fixed cleanup progress accounting when policy-level progress records are
  folded from queue-local cleanup phases, including selected/applied counts and
  blocked reasons for consolidated Monitor policies.
- Fixed Docker command-container profile validation so image/build conflicts
  identify the profile name and source file instead of returning a generic
  runner-options error.

## 0.9.72 - 2026-05-30

### Added

- Added TaskMonitor `jsonl_then_delete` logging for cleanup-selected subjects.
  The monitor now emits one `task_lifetime_report` JSONL record before each
  exact destructive cleanup effect, using a common policy-general shape with
  top-level `taskspec`, `lifetime`, `monitor`, and `observations` fields.
- Added a default external task-log path at `logs/weft.log` under the Weft
  project root, backed by Python's rotating logfile handler. External write
  failures are durably queued in the Monitor-owned deferred-writes table and
  retried on later monitor cycles.

### Changed

- Changed TaskMonitor status diagnostics so external-log health, permission
  regressions/recovery, and deferred-write backlog are cached and surfaced
  through status/PONG without opening the log path on the status read path.

## 0.9.70 - 2026-05-28

This is a cumulative feature entry since 0.9.10. The intervening releases were
small, iterative hardening releases; this entry describes the resulting product
state rather than the sequence of intermediate changes.

### Added

- Added the manager-supervised TaskMonitor as a durable operational cleanup and
  observability service. The Monitor now folds retained task-log rows into
  derived Monitor tables, emits compact operational summaries, tracks cleanup
  progress, and keeps task status available after raw task-log retirement while
  preserving task-owned queues and `weft.log.tasks` as the lifecycle source of
  truth.
- Added bounded Monitor cleanup policies for retained task logs, Monitor-store
  lifecycle rows, terminal task-local runtime queues, dead task-local queues,
  and runtime-state retention. Cleanup is policy-selected, retryable, and
  observable, with cached diagnostics surfaced through TaskMonitor liveness
  responses.
- Added shared `ServiceTask` infrastructure for long-running internal services.
  Manager, TaskMonitor, and Heartbeat now share common reactor, control,
  heartbeat, and service-worker mechanics without moving manager dispatch or
  service-convergence policy out of the Manager.
- Added manager-owned internal service supervision for Heartbeat, TaskMonitor,
  and autostart services. Managers reconcile these services through a reserved
  internal spawn path, service-owner records, keyed liveness probes, restart
  backoff, and explicit service status evidence.
- Added operator-facing diagnostics for manager and internal-service liveness,
  including registry evidence, host/PING proof, service-owner state, pending
  launches, and Monitor cleanup health.
- Added Docker-backed runner capabilities, including first-party Docker command
  execution, Docker-backed one-shot `provider_cli` agent execution, deterministic
  provider image recipes/cache warming, runtime-specific liveness probing, and
  command container profiles loaded from project-local TOML files.
- Added public Python client conveniences and parity guards, including
  `weft.client.connect()`, task PING helpers, active-manager stop parity, and a
  test-backed matrix for the stable client namespace surface.

### Changed

- Changed manager startup, selection, and replacement to use stronger bounded
  liveness evidence. Manager registry rows, service-owner rows, host runtime
  handles, and keyed PING/PONG probes now feed a shared convergence model
  instead of relying on stale-looking process records alone.
- Changed internal service launches to drain through a manager-owned priority
  path before ordinary public spawn work, so singleton services can converge
  deterministically without starving public tasks or orphaning reserved work.
- Changed task-log and runtime cleanup from ad hoc pruning into Monitor-owned
  cleanup slices. Expensive cleanup work runs in bounded service-worker lanes;
  the TaskMonitor reactor remains available for STOP, STATUS, PING, and cached
  diagnostics while cleanup is in flight.
- Changed Monitor diagnostics so PONG responses report cached cleanup state
  instead of scanning queues, opening external log sinks, querying Monitor
  tables, or recomputing cleanup candidates on the liveness path.
- Changed stale service-owner cleanup so old Manager, TaskMonitor, and Heartbeat
  service rows can be summarized, disposed, and cleaned only when same-service
  registry evidence proves the owner is no longer live or has been superseded.
  This cleanup authority does not turn Monitor state into lifecycle truth.
- Changed Docker integration so runner-specific Docker defaults can be
  materialized at the plugin boundary while Weft core continues to see ordinary
  TaskSpec runner options, environment, working directory, lifecycle, and queue
  semantics.
- Changed the specs and tests to make the current architecture easier to audit:
  Monitor invariants are split into explicit sub-contracts, dense runtime
  modules have section maps, plan metadata is normalized, and import-boundary
  tests guard against Monitor-derived state becoming public lifecycle authority.

### Fixed

- Fixed manager, result-wait, and shutdown races that showed up under release
  load, parallel CI, Windows cleanup, and Postgres-backed test runs.
- Fixed stale manager and service-owner records that could previously keep old
  managers, TaskMonitor instances, Heartbeat services, or their task-local
  control queues visible after restart or replacement paths.
- Fixed cleanup paths that could either do too much work on a hot path or leave
  retained task-log, terminal control, reserved, dead-task, and runtime-state
  cleanup work without durable retry/progress evidence.
- Fixed manager and managed-service probe residue by exact-deleting
  manager-owned PING/PONG probe rows after success, timeout, or probe failure.
- Fixed Docker runner validation and materialization edge cases for missing
  images/builds, network isolation, profile files, required environment,
  profile-sourced mounts/build contexts, and unsupported agent/profile
  combinations.

## 0.9.10 - 2026-04-30

### Changed
- Optimized status lookup for known tids

## 0.9.5 - 2026-04-20

### Added

- Added a package-based public `weft.client` surface with a `Task` handle,
  noun namespaces, and shared capability ownership below the CLI adapter.
- Added the first-party `weft-django` integration package on top of the public
  client surface, including the shipped embedding and inspection path.

### Changed

- Changed the repo architecture so `weft.commands` is the shared capability
  layer, `weft.cli` is the Typer adapter, `weft.client` is the Python adapter,
  and the temporary `core.ops` scaffolding is gone.
- Changed `weft run` and related manager, task, spec, and system surfaces to
  share the canonical command-layer owners and updated the README and specs to
  match the shipped package layout.

### Fixed

- Fixed divergent submission, wait, and manager-bootstrap paths between the
  CLI and Python client so the shared durable behavior now has one owner.
- Fixed Django request-id capture and submit-override parity so enqueue-time
  request metadata survives teardown and the integration applies the same
  supported override set as the shared submission surface.

## 0.9.2 - 2026-04-17

### Added

- Added a built-in runtime heartbeat service plus helper APIs for best-effort
  interval emission through ordinary Weft queues.

### Changed

- Changed internal runtime routing so reserved runtime task classes and reserved
  `_weft.*` endpoints are carried on a manager-owned spawn envelope instead of
  trusting public TaskSpec metadata.
- Changed the canonical-owner fence and manager suspension path so fenced spawn
  requests stay recoverable while ownership is unresolved and duplicate service
  claimants converge by loser exit.
- Changed the final specs to document heartbeat as a runtime-scoped interval
  emitter, not a scheduler; broader scheduler semantics remain deferred.

### Fixed

- Fixed manager yield, idle-shutdown, and autostart behavior so dispatch-
  suspended managers do not orphan fenced reserved work or self-enqueue
  duplicate autostart requests.
- Fixed public naming and endpoint-claim surfaces so ordinary tasks cannot
  claim reserved internal heartbeat endpoints.

## 0.9.1 - 2026-04-17

### Added

- Added `WEFT_DIRECTORY_NAME` so Weft metadata can live under a configured
  project directory name instead of always using `.weft`.
- Added `compile_config()` and `load_config(overrides=...)` for embedding
  callers that need to compile `WEFT_*` overrides into canonical Weft and
  SimpleBroker config without mutating process-global environment variables.

### Changed

- Changed context discovery, init, dump/load defaults, stored-spec resolution,
  provider CLI project settings, autostart paths, and task output paths to honor
  the configured Weft metadata directory consistently.
- Changed CLI help text and specs to describe the Weft metadata directory
  generically and to show import/export defaults relative to the active metadata
  directory.

### Fixed

- Fixed the default sqlite broker path so it follows the configured metadata
  directory unless an explicit database name override is supplied.
- Fixed in-process config compilation to apply the same ambiguous Postgres
  backend guardrails as the environment-driven config path.

## 0.9.0 - 2026-04-16

### Added

- Added the first agent runtime surfaces plus named-task and autorun workflow
  improvements for higher-level automation and embedded use cases.
- Added a first-task tutorial, expanded execution-mode help text, and backend
  decision guidance in the README.

### Changed

- Changed task startup, manager reuse, and streaming behavior to make detached
  bootstrap and live output handling more reliable.
- Changed the runtime and spec documentation to clarify reserved-queue timing,
  state-versus-metadata boundaries, large-output references, and task execution
  algorithms.

### Fixed

- Fixed startup and streaming regressions in the manager/runtime path.
- Fixed Windows provider CLI probing so release and CI paths no longer fail the
  executable assertion.

## 0.8.0 - 2026-04-09

### Added

- Added `weft serve`, a foreground manager command for `systemd`, `launchd`,
  `supervisord`, and similar service managers.
- Added a shared manager lifecycle helper so `weft run`, `weft worker`,
  `weft status`, and `weft serve` all use the same bootstrap and registry
  replay path.

### Changed

- Changed `weft worker start` to use the canonical manager bootstrap path
  instead of launching arbitrary manager TaskSpecs.
- Changed canonical manager selection and leadership handling to only consider
  managers bound to `weft.spawn.requests`.
- Changed manager external signal handling so `SIGTERM` and `SIGINT` drain like
  STOP while `SIGUSR1` keeps immediate kill semantics.

### Fixed

- Fixed divergent manager observability by sharing stale-record pruning and
  normalized registry reads across list, status, and stop surfaces.
- Fixed the descendant-timeout regression test to use an explicit child
  readiness handshake instead of a tight startup race under xdist load.

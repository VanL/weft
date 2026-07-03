# Changelog

## 0.9.86 - 2026-07-03

### Changed

- Raised the SimpleBroker floor to 5.0.0 and the Postgres backend floor to
  simplebroker-pg 3.0.0.
- Ignored stale `WEFT_VACUUM_LOCK_TIMEOUT` and
  `BROKER_VACUUM_LOCK_TIMEOUT` config overrides, matching the SimpleBroker
  5.0 removal of that tuning key.

## 0.9.85 - 2026-07-03

### Changed

- **Breaking:** TaskSpec validation now rejects agent tool descriptors with
  `approval_required: true`. The flag was previously accepted but silently
  unenforced; Weft does not implement tool approval policy ([AR-0.0]).
  Enforce approvals in the calling system or use a `provider_cli` runtime.
- **Breaking:** the macOS sandbox runner (weft-macos-sandbox 0.6.0) no
  longer inherits the full host environment. Sandboxed processes now receive
  a fixed baseline (PATH/HOME/TMPDIR/locale and similar), plus host
  variables opted in via `spec.runner.options.env_passthrough`, plus
  `spec.env`.
- Weft-owned data at rest is now owner-only: `.weft/` metadata directories
  are created 0700 (and tightened on upgrade), large-output spill files and
  `weft system dump` exports are written 0600. Dump content is unchanged and
  still contains TaskSpec env/args verbatim; treat dump files as secrets.
- Raised the SimpleBroker floor to 4.10.0 and the Postgres backend floor to
  simplebroker-pg 2.5.0 for the current retention and backend-correctness
  behavior.

### Fixed

- Hardened terminal-state publication and control handling: terminal-event
  writes are retried and surfaced with identifiers, deferred STOP/KILL is
  finalized on successful outcomes, and termination signal handling now runs
  outside the signal frame.
- Tightened TaskMonitor retention cleanup around TID mappings, runtime owners,
  reserved queues, and retention archive appends so cleanup preserves newest
  live evidence and avoids stale-open or concurrent-append loss.
- Verified CLI fallback stop/kill against PID create times before acting, so a
  reused PID cannot be treated as the original task process.

## 0.9.84 - 2026-06-29

### Added

- Added focused Hypothesis property tests for pure Weft invariants, including
  TaskSpec payload resolution, immutable resolved sections, lifecycle status
  sequences, queue-name classifiers, task-evidence queue fallbacks, and small
  configuration parsers. Property tests are now a documented dev-only test
  style with their own pytest marker.
- Added a TaskMonitor cleanup subphase that trims old manager-authored
  `task_spawned` rows after Monitor-store collation while preserving the newest
  launch hints for each manager TID.

### Changed

- Known-TID terminal snapshots now use the shared status reconstruction path as
  a fallback, so terminal Monitor-store records remain visible after raw
  task-log retirement. `weft_django.status(tid)` inherits the same compact
  non-consuming terminal status behavior.
- Documented property-test boundaries and the manager launch-event retention
  policy in the specs, plans, and agent testing runbook.

### Fixed

- Kept manager `task_spawned` compaction row-level only: it deletes exact old
  launch-event rows and their Monitor child refs without marking the live
  manager family raw-deleted or removing child-owned lifecycle evidence.

## 0.9.82 - 2026-06-17

### Added

- Added the optional `weft-microsandbox` first-party runner package and the
  root `weft[microsandbox]` extra. The runner executes disposable command tasks
  and one-shot `provider_cli` agent tasks inside fresh Microsandbox sandboxes,
  with explicit guest images/executables and narrow defaults for network,
  workspace access, environment forwarding, interactivity, and persistence.
- Documented the Microsandbox runner in the README and specs, including the
  TaskSpec runner option contract, resource-limit mapping, agent-runtime lane,
  implementation ownership, and release-gate coverage.
- Added root and package-specific CI/release gates for the Microsandbox
  extension so root Weft releases exercise the extension and
  `weft_microsandbox/v*` tags can publish the package independently.

### Fixed

- Fixed the Microsandbox release workflow wiring and paired the root
  `weft[microsandbox]` extra with `weft-microsandbox 0.5.1`.

## 0.9.81 - 2026-06-11

### Changed

- `weft system dump` now writes SimpleBroker `simplebroker-dump` v1 NDJSON
  directly through SimpleBroker's public dump primitive while preserving Weft's
  runtime-state exclusion and claimed-row omission summary.
- `weft system load` now accepts the SimpleBroker dump format, preserves broker
  message IDs through SimpleBroker's load path, skips runtime-only
  `weft.state.*` records, and treats same-target existing aliases as no-ops.
  Legacy Weft `meta`/`timestamp` dumps are intentionally rejected.
- Raised the SimpleBroker floor to 4.7.0 so dump/load can rely on the public
  versioned dump/load helpers.

### Fixed

- Added regression coverage for SQLite rollback when exact message-ID import
  fails, so duplicate IDs cannot be silently rewritten during restore.
- Added release-load coverage for TaskMonitor stale-owner disposition pruning,
  retirement pacing, and backlog-scale convergence without weakening the
  cleanup policy contracts.

## 0.9.80 - 2026-06-10

### Fixed

- Fixed a silent `jsonl_then_delete` lifecycle stall: terminal-family summary
  readiness and control cleanup compared broker hybrid message IDs against
  host wall-clock time, so a monitor whose clock lagged the durably monotonic
  broker ID domain stopped summarizing, exporting, and deleting with zero
  errors. Zero-retention terminal gates are now bound by the store's ingest
  checkpoint (ID domain against ID domain).
- Exact-row prune reconciliation now re-verifies each candidate per ID when a
  batch delete under-deletes, so a still-present row can never be reported
  deleted and Monitor families can no longer be marked raw-deleted vacuously.
- Stale/superseded service-owner dispositions now fire in all destructive
  monitor modes (previously `delete` only), unlocking the spec-intended
  dead-incarnation control-queue cleanup in `jsonl_then_delete` deployments.

### Changed

- Monitor cleanup policies now read claimed task-log rows through SimpleBroker's
  public `peek_one`/`peek_many` `include_claimed` surface instead of the private
  `_retrieve` hook; requires simplebroker>=4.6.0. An architecture test now pins
  "no private simplebroker reaches" across the `weft/` package. No behavior
  change.
- Monitor store now uses SimpleBroker's public sidecar-session API
  (`broker.sidecar()`) instead of the private `_runner` attribute; requires
  simplebroker>=4.5.0. Watcher imports moved to `simplebroker.ext`
  (`StopWatching` replaces the private `_StopLoop`). No behavior change.
- Keyed PING probes now retire their replies (single-reader contract): a
  matched PONG is deleted on match, and rows bearing the probe's request id
  are swept at timeout or abandonment, on the shared probe helper and on the
  manager's internal leadership/service probes alike. Live managers no longer
  accumulate unconsumed pongs in `T{tid}.ctrl_out`.
- `jsonl_then_delete` recovery paths (marked-with-refs repair and orphan raw
  recovery) are summary-gated: no raw task-log row is deleted before its
  family's JSONL lifetime export.
- New default-on TaskMonitor self-maintenance pass (hourly monotonic
  deadline): backend vacuum physically deletes claimed rows, and conservative
  runtime-state pruning covers the managers/services/streaming/endpoints/
  pipelines groups with foreground defaults. Reported through a non-policy
  `maintenance` STATUS block; opt out with `WEFT_TASK_MONITOR_MAINTENANCE=0`.
  Deployments no longer need external `weft system tidy`/`prune` scheduling
  for routine hygiene.

## 0.9.75 - 2026-06-01

### Changed

- Raised the SimpleBroker floor to 4.3.0 and the Postgres backend floor to
  simplebroker-pg 2.2.0 so Weft can rely on the exact-ID insert API and the
  current backend APIs across SQLite and Postgres.
- Changed `weft system dump`/`load` so runnable broker state preserves message
  timestamps instead of rewriting pending spawn request IDs on import. Backends
  that cannot preserve message IDs are rejected before mutation.
- Tightened provider CLI one-shot execution so tool profile resolution happens
  once per work item and Gemini read-only/general runs get the same isolated
  runtime home protections as bounded execution.
- Clarified the production retention guidance: `delete` remains the default
  TaskMonitor mode, while `jsonl_then_delete` is documented as the audit-retention
  preset.

### Fixed

- Fixed active structured STOP/KILL control envelopes so active consumers defer
  terminal publication until the runner unwinds instead of publishing terminal
  state early.
- Fixed pipeline stage handoff behavior by enforcing the single-output contract,
  preventing extra stage outputs from being stranded after the first downstream
  edge checkpoint.
- Fixed result and task evidence queue-name resolution by reusing the canonical
  helper for malformed or partial TaskSpec `io` payloads.
- Fixed manager cleanup by unregistering the registered `atexit` callback during
  normal cleanup.
- Removed stale monitor policy API scaffolding and unused constants/hooks that
  no longer represented live product behavior.

## 0.9.74 - 2026-05-31

### Fixed

- Reconciled orphan task-monitor service state so `weft status` classifies
  stale internal service log entries as superseded service records instead
  of reporting dead heartbeat/monitor services as live tasks.

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

# Changelog

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

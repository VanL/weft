# Changelog

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

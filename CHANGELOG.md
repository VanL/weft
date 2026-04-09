# Changelog

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

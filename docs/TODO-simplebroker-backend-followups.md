# SimpleBroker Backend Follow-Ups

Completed in the current backend-generalization slice:

- `weft tidy` now uses backend-native SimpleBroker maintenance via the active
  broker target instead of sqlite-only file operations.
- `weft load --dry-run` now builds its preview through backend-neutral broker
  APIs.
- `weft load` now does parse -> preflight -> apply, and alias conflicts return
  exit code `3` before any writes begin.

Remaining caveat:

- File-backed sqlite imports still use a small snapshot rollback helper during
  apply. Non-file-backed backends do not pretend imports are atomic; if an
  apply-phase write fails after writes begin, Weft reports that a partial
  import may have occurred.

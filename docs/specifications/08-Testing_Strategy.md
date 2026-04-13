# Testing Strategy

This document records the tests that exist now and why they are split the way
they are. Deferred test surfaces live in
[08A-Testing_Strategy_Planned.md](08A-Testing_Strategy_Planned.md).

## Why This Shape Exists [TS-0]

Weft tests through the repo-managed environment and a small set of shared
harnesses:

- `.envrc` and the in-repo `.venv` keep verification deterministic.
- `WeftTestHarness` isolates broker state, project roots, and cleanup.
- `shared` vs `sqlite_only` keeps backend-neutral coverage separate from
  SQLite-specific coverage.
- The shared Postgres suite (`bin/pytest-pg --all`) is the current check for
  backend-sensitive changes.

The point is not to maximize suite count. The point is to keep the current
contract exercised where it matters and to make backend-sensitive drift easy to
see.

## Current Coverage [TS-1]

- `tests/taskspec/` covers TaskSpec validation, immutability, defaults, and
  state transitions.
- `tests/tasks/` covers execution, reservation flow, control messages, process
  titles, and agent/task runtime behavior.
- `tests/commands/` and `tests/cli/` cover command wiring and end-to-end CLI
  behavior.
- `tests/context/` and `tests/core/` cover context discovery, manager behavior,
  pipeline runtime, agent runtime, and execution helpers.
- `tests/specs/` covers spec-level invariants and cross-surface validation.
- `tests/system/` holds the system-level checks that are already implemented.

## What Is Not Canonical [TS-2]

There is no dedicated `tests/integration/`, `tests/performance/`, or
`tests/property/` tree yet. Those deferred surfaces live in the companion doc
instead of being mixed into this canonical file.

## Related Documents

- [08A-Testing_Strategy_Planned.md](08A-Testing_Strategy_Planned.md)
- [07-System_Invariants.md](07-System_Invariants.md)
- [10-CLI_Interface.md](10-CLI_Interface.md)

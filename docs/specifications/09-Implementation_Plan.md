# Implementation Plan

This document records the current implementation boundary and why the code is
shaped this way. The deferred roadmap lives in
[09A-Implementation_Roadmap_Planned.md](09A-Implementation_Roadmap_Planned.md).

## Current Boundary [IP-0]

The current codebase is intentionally narrower than the old roadmap:

- CLI wiring lives in `weft/cli.py` and `weft/commands/*.py`.
- Context discovery is backend-neutral and no longer hangs off `--dir` or
  `--file` globals.
- Execution, process observation, and spec authoring live in the current
  `weft/core/` and `weft/commands/` modules rather than in separate helper
  package families.
- Task execution, persistence, and agent support are owned by the current
  `weft/core/` and `weft/commands/` modules.

The reason for that shape is simplicity. Weft keeps the visible command surface
small and routes everything through the current task, queue, and manager
machinery instead of splitting behavior across speculative helper packages.

## Current Ownership [IP-1]

- `weft/commands/run.py` owns task submission and pipeline execution.
- `weft/commands/status.py` and `weft/commands/result.py` own status and
  result reporting.
- `weft/commands/tasks.py` and `weft/commands/manager.py` own task lifecycle,
  TID handling, and manager control commands.
- `weft/commands/queue.py` owns direct SimpleBroker queue operations.
- `weft/commands/specs.py` owns spec management and validation.
- `weft/commands/tidy.py`, `weft/commands/dump.py`, and `weft/commands/load.py`
  own broker maintenance.

## Why This Shape Exists [IP-2]

- Backend-neutral project discovery is easier to reason about than directory-
  plus-database flags.
- Keeping the CLI thin reduces the chance that command behavior drifts away
  from the task and queue runtime.
- Folding current behavior into existing modules keeps traceability explicit
  and avoids inventing package boundaries that are not part of the shipped
  system.

## Related Documents

- [09A-Implementation_Roadmap_Planned.md](09A-Implementation_Roadmap_Planned.md)
- [10-CLI_Interface.md](10-CLI_Interface.md)
- [11-CLI_Architecture_Crosswalk.md](11-CLI_Architecture_Crosswalk.md)

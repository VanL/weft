# CLI Architecture Crosswalk Planned

This companion document is intentionally non-normative. It records only CLI
crosswalk work that is still planned and has not shipped into the canonical
crosswalk in [11-CLI_Architecture_Crosswalk.md](11-CLI_Architecture_Crosswalk.md).
If any item here ships, move it into the canonical sibling and remove it from
this planned companion rather than using this file as the ownership map.

Shipped ownership does not belong here. The helper boundaries for process
discovery, TID resolution, task monitoring, spec storage, and pipeline
compilation already exist in code and should be documented in the canonical
crosswalk if they need spec coverage.

## 1. Future Command Families [CLI-A1]

Use this section only for command surfaces that do not exist in the current CLI
and do not already have a place in the canonical crosswalk.

Possible future entries:

- additional operator-facing subcommands
- a new top-level command family beside the current `init`, `run`, `status`,
  `result`, `task`, `manager`, `queue`, `spec`, and `system` groups

## 2. Future Ownership Splits [CLI-A2]

Use this section only if a future CLI slice needs to document a new module or
boundary that is not shipped yet. Keep the entry narrow and explicit:

- owner: the module or helper that will own the boundary
- boundary: what it will own and what it will not own
- verification: the smallest behavior that proves the split is correct
- required action: the implementation step that makes the split real

## Backlink

Canonical CLI ownership lives in
[11-CLI_Architecture_Crosswalk.md](11-CLI_Architecture_Crosswalk.md).

# CLI Architecture Crosswalk Planned

This companion document tracks deferred CLI surfaces that correspond to
[11-CLI_Architecture_Crosswalk.md](11-CLI_Architecture_Crosswalk.md). It
contains only work that is still intended but not part of the current canonical
crosswalk.

## 1. Deferred Component Boundaries [CLI-A1]

The current code keeps these responsibilities folded into command modules and
runtime helpers. If those boundaries are ever split out, this is the place to
document the intended ownership:

- process-discovery helpers
- TID-resolution helpers
- task-monitor helpers
- spec-store helpers
- pipeline-builder helpers

## 2. Deferred CLI Surface Extensions [CLI-A2]

The current CLI already exposes the shipped command families. Deferred work
would live here only if the command surface itself grows in a way that needs its
own crosswalk:

- additional operator-facing subcommands
- new command families that sit beside the current `init`, `run`, `status`,
  `result`, `task`, `manager`, `queue`, `spec`, and `system` groups

## Backlink

Canonical CLI ownership lives in
[11-CLI_Architecture_Crosswalk.md](11-CLI_Architecture_Crosswalk.md).

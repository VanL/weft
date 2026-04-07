# Future Ideas (Deferred)

This document captures ideas that are intentionally **not** part of the current
spec, but may return once there is a concrete use case.

## Task Pause / Resume

Potentially expose `weft task pause` / `weft task resume` once we have a clear
runtime story (SIGSTOP vs. cooperative pause via `ctrl_in`). Deferred for now to
avoid over-specifying control semantics.

_Implementation status: Partially implemented._ The core runtime handles
PAUSE/RESUME control messages (`weft/core/tasks/base.py` dispatches them,
`weft/_constants.py` defines `CONTROL_PAUSE` / `CONTROL_RESUME`, and
`weft/core/tasks/consumer.py` requeues in-flight messages while paused). No CLI
commands (`weft task pause` / `weft task resume`) exist yet -- the feature is
core-only with no user-facing surface. (Audited 2026-04-07.)

## Task TUI (`weft task top`)

A curses-style live dashboard for task state, resource metrics, and tailing
logs. This would be a distinct UX from `weft list` and should be implemented as
an opt-in TUI, not a default code path.

_Implementation status: Not implemented._ `weft status --watch` and `weft task
status --watch` provide streaming text output but no curses/TUI interface exists.
(Audited 2026-04-07.)

## Reserved Queue UX (Retry / Recover)

The reserved-queue/DLQ concept is important, but the best operator UX is still
open. We do not yet have a clear mental model for first-class `retry` or
`recover` commands vs. composition and queue primitives. Ideas include:

- A guided `weft task recover` flow that surfaces pending reserved payloads and
  offers requeue/clear actions.
- Consolidating recovery into `weft queue` primitives plus higher-level helpers
  in docs/scripts.

The current spec keeps dedicated commands for clarity, but we may revisit once
usage patterns are established.

_Implementation status: Not implemented._ No `weft task recover` or `weft task
retry` commands exist. `weft queue move` and `weft queue peek` provide the
low-level primitives for manual recovery of reserved-queue messages.
(Audited 2026-04-07.)

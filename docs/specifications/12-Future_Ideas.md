# Future Ideas (Deferred)

This document captures ideas that are intentionally **not** part of the current
spec, but may return once there is a concrete use case.

If a behavior is already shipped or already covered by canonical specs, it does
not belong in this deferred ledger except as a note about the still-missing
surface area.

## Task Pause / Resume CLI

Potentially expose `weft task pause` / `weft task resume` once we have a clear
operator story for the CLI surface. The underlying `PAUSE` / `RESUME` control
messages already exist for task types that opt into live pausing, so the
deferred part here is the explicit user-facing command layer and its UX.

_Implementation status: Deferred at the CLI layer._ The runtime control path is
already shipped and documented in the canonical specs (`00-Quick_Reference.md`
and `01-Core_Components.md`). No `weft task pause` / `weft task resume`
commands exist yet. (Audited 2026-04-16.)

## Task TUI (`weft task top`)

A curses-style live dashboard for task state, resource metrics, and tailing
logs. This would be a distinct UX from `weft task list` and should be implemented as
an opt-in TUI, not a default code path.

_Implementation status: Not implemented._ `weft status --watch` and
`weft task status --watch` provide streaming text output, but no curses/TUI
interface exists. (Audited 2026-04-16.)

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
(Audited 2026-04-16.)

## Scheduler Surface Beyond Heartbeat

The built-in heartbeat service now exists and is documented in the canonical
specs. The still-deferred part is any broader scheduler surface above that
runtime primitive, such as:

- cron expressions
- wall-clock "run at 09:00" semantics
- time zones
- durable missed-run replay or catch-up
- exactly-once schedule guarantees

_Implementation status: Deferred above the heartbeat layer._ Weft ships a
runtime-scoped heartbeat interval emitter, but it does not ship a general
scheduler surface. (Audited 2026-04-17.)

# Planned Companion for 05: Message Flow and State

This document records follow-up surfaces that are not part of the current
message-flow and state contract.

Current shipped behavior, including task submission, reservation, control
handling, named endpoint discovery, pipeline flow, large-output spill
handling, state observation, and manager bootstrap, lives in
[`05-Message_Flow_and_State.md`](05-Message_Flow_and_State.md). Nothing here
overrides that canonical contract.
If any item here ships, move it into the canonical sibling and remove it from
this planned companion rather than using this file as the live source of truth.

## Planned Endpoint-Registry Extensions [05A-1]

Future work, if it proves useful, should stay thin and task-owned:

- explicit lease or heartbeat refresh instead of opportunistic stale pruning
- richer diagnostics for duplicate live endpoint claims
- small control-target helpers that still resolve to ordinary `ctrl_in` queue
  writes

Boundary:

- endpoint resolution remains explicit discovery
- sending to a named endpoint remains an ordinary queue write
- request and reply envelopes stay task-owned or builtin-owned contracts
- missing-name resolution must remain explicit and must not auto-spawn or
  auto-register a task

## Planned State-Inspection Helpers [05A-2]

Potential follow-up surfaces:

- dedicated helpers for replaying task-log history into operator-facing views
- richer task-state timelines that combine log events with runtime-only queue
  hints
- convenience helpers for comparing current state snapshots with historical
  snapshots

Boundary:

- the durable state model stays queue- and log-backed
- these helpers should not introduce a separate state database
- live hints remain hints, not new canonical state

## Planned Large-Output Reader Helpers [05A-3]

Potential follow-up surfaces:

- automatic dereferencing helpers for large-output reference envelopes
- chunked readers for spilled outputs
- integrity and retention tooling for output artifacts

Boundary:

- the current spill/reference format stays defined in the canonical 05 spec
- helpers may present or inspect spilled output, but they must not change the
  current outbox contract

## Planned Recovery and Queue-Lifecycle Helpers [05A-4]

Potential follow-up surfaces:

- guided reserved-queue recovery helpers
- more explicit retry and requeue surfaces
- queue-lifecycle dashboards or cleanup helpers

Boundary:

- recovery remains explicit and operator-directed
- queue moves stay ordinary queue operations
- there is no hidden retry orchestrator or separate queue-lifecycle service

## Related Plans

- [`docs/plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md`](../plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md)
- [`docs/plans/2026-04-13-pipeline-spec-expansion-plan.md`](../plans/2026-04-13-pipeline-spec-expansion-plan.md)
- [`docs/plans/2026-04-16-runtime-endpoint-registry-boundary-plan.md`](../plans/2026-04-16-runtime-endpoint-registry-boundary-plan.md)

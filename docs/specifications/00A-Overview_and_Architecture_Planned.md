# Planned Companion for 00: Overview and Architecture

This document tracks intended but unshipped architecture follow-ups adjacent to
[00-Overview_and_Architecture.md](00-Overview_and_Architecture.md).

Current task, pipeline, manager, and agent behavior live in the canonical
numbered specs. Nothing here overrides the current contract.
If any item here ships, move it into the canonical sibling and remove it from
this planned companion rather than citing this file as current behavior.

## Future Shape [00A-1]

If Weft grows beyond the current task, pipeline, manager, and agent surfaces,
the next layer should still feel task-shaped:

- simple verbs
- layered composition
- Unix-friendly input and output
- explicit observability through queues and task logs
- no separate workflow platform

That is a constraint for future work, not a description of current shipped
behavior.

## Future Constraints [00A-2]

The following are candidate performance targets for later slices, not current
guarantees:

- Task creation: 100 tasks/second
- Queue throughput: 1000 messages/second
- Concurrent tasks: 200 maximum
- Task startup: under 100ms
- Memory overhead: under 10MB per task
- CPU monitoring overhead: under 2%

These targets define a future tuning envelope only. They do not claim the
current implementation already meets them.

## Current References [00A-3]

- Current overview: [00-Overview_and_Architecture.md](00-Overview_and_Architecture.md)
- Current pipeline contract: [12-Pipeline_Composition_and_UX.md](12-Pipeline_Composition_and_UX.md)
- Current agent runtime contract: [13-Agent_Runtime.md](13-Agent_Runtime.md)
- Broker and CLI context:
  [04-SimpleBroker_Integration.md](04-SimpleBroker_Integration.md),
  [10-CLI_Interface.md](10-CLI_Interface.md)

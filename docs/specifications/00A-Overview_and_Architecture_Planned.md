# Overview and Architecture - Planned Surfaces

This companion document holds planned architecture surfaces that are intentionally not part of the current contract.

Canonical current behavior and rationale live in [00-Overview_and_Architecture.md](00-Overview_and_Architecture.md).

## Planned Product Shape [00A-1]

Planned pipeline surfaces remain task-shaped and should use the same operator verbs as ordinary tasks. The goal is not to create a separate workflow platform. The desired shape is:

- simple verbs
- layered composition
- Unix-friendly input and output
- explicit observability through queues, task logs, and normal task surfaces

The planned model is a task-shaped object whose body is composition. When the pipeline surface expands further, it should continue to use the same durable task lifecycle and observability model rather than inventing a second platform.

## Planned Constraints [00A-2]

The following are design targets, not current guarantees:

- Task creation: 100 tasks/second
- Queue throughput: 1000 messages/second
- Concurrent tasks: 200 maximum
- Task startup: under 100ms
- Memory overhead: under 10MB per task
- CPU monitoring overhead: under 2%

These targets describe the performance envelope the system is aiming for. They are not a statement that the current implementation already proves them.

## Backlinks [00A-3]

- Current contract: [00-Overview_and_Architecture.md](00-Overview_and_Architecture.md)
- Context and broker integration: [04-SimpleBroker_Integration.md](04-SimpleBroker_Integration.md)
- CLI surface: [10-CLI_Interface.md](10-CLI_Interface.md)

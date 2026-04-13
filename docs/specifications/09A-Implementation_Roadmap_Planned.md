# Implementation Roadmap Planned

This companion document tracks intended but unimplemented work that corresponds
to [09-Implementation_Plan.md](09-Implementation_Plan.md). It does not repeat
the current implementation boundary.

## 0. Manager and Runtime Enhancements [IP-A0]

Deferred work in this area would add or refine:

- manager hierarchy and specialization
- task routing to more specific workers
- load balancing across managers
- stronger manager recovery and coordination behavior

## 1. Process and Observability Enhancements [IP-A1]

Deferred work in this area would add or refine:

- richer process discovery helpers
- standalone TID resolution helpers
- live monitoring surfaces for operators
- deeper OS-level process integration

## 2. Spec Authoring Enhancements [IP-A2]

Deferred work in this area would add or refine:

- richer stored-spec workflows
- pipeline-building helpers
- template or registry helpers for more advanced task authoring

## 3. Additional Quality Gates [IP-A3]

Deferred work in this area would add or refine:

- explicit benchmark coverage
- dedicated integration-system split for future suites
- broader platform-specific validation when needed

## Backlink

Canonical implementation boundaries live in
[09-Implementation_Plan.md](09-Implementation_Plan.md).

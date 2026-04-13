# Testing Strategy Planned

This companion document tracks deferred test surfaces that correspond to
[08-Testing_Strategy.md](08-Testing_Strategy.md). It contains only work that is
still intended but not part of the current canonical test contract.

## 1. Unit Testing Additions [TS-A1]

The current suite covers the core behavior. Deferred unit-level additions would
focus on the last few assertions that are still useful but not required for the
current boundary:

- extra TaskSpec convenience assertions
- explicit limit-structure assertions where runner-level coverage already
  exists
- additional process-title edge cases that are currently covered indirectly

## 2. Integration Testing [TS-A2]

The current tree does not have a dedicated integration directory. Deferred
integration coverage would live in explicit suites for:

- queue integration
- context integration
- SimpleBroker integration
- monitoring integration

## 3. System Testing [TS-A3]

The current system-level coverage is distributed across CLI and command tests.
Deferred system suites would cover:

- end-to-end task workflows
- cross-platform process behavior
- load-style workflows
- task failure recovery at the whole-system level

## 4. Performance Testing [TS-A4]

Dedicated performance suites would cover:

- task creation throughput
- queue throughput
- monitoring overhead
- memory growth under load

## 5. Property-Based Testing [TS-A5]

Property-based checks are still useful for invariants that are already enforced
deterministically today. Deferred properties would focus on:

- forward-only state transitions
- delivery and reservation invariants
- resource-limit enforcement

## 6. Testing Hooks and CI [TS-A6]

The current suite uses local fixtures and helper modules. Deferred work in this
area would centralize:

- reusable fixtures
- queue helpers
- mock helpers
- utility helpers for waiting, asserting, and profiling
- dedicated CI jobs for the deferred suites above

## Backlink

Canonical coverage lives in
[08-Testing_Strategy.md](08-Testing_Strategy.md).

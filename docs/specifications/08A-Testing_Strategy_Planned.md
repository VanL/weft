# Testing Strategy Planned

This companion document tracks test surfaces that are still intentionally out
of the current canonical contract in [08-Testing_Strategy.md](08-Testing_Strategy.md).
It is not a current coverage map. Anything already shipped or already
represented in the canonical testing spec belongs in the sibling current doc
instead.
If any item here ships, move it into the canonical sibling and remove it from
this planned companion rather than treating this file as the live testing map.

## 1. Dedicated Integration Splits [TS-A1]

There is still no separate `tests/integration/` tree. If the suite ever needs a
dedicated integration layer, it would hold cross-surface flows that are already
covered elsewhere today:

- queue and broker integration beyond the current CLI, command, core, task, and
  spec suites
- context and project discovery flows
- monitoring and state-observation flows
- other SimpleBroker-backed seams that become hard to reason about inside the
  mixed current suites

Constraint:

- do not move already-current coverage out of the canonical spec just because a
  future split would make it easier to browse

## 2. Future Performance Coverage [TS-A2]

The benchmark scripts in `tests/long_session_surface_benchmark.py` and
`tests/multiqueue_polling_benchmark.py` are still dev-only measurement tools,
not part of the canonical pytest contract.

Planned performance work would stay limited to explicit regressions or soak
checks that we decide are worth enforcing:

- task-creation throughput
- queue-throughput sensitivity
- monitoring overhead
- memory growth under sustained load

Constraint:

- keep these as opt-in measurement or gated regression suites; do not make them
  the default pytest contract unless the canonical spec is updated

## 3. Property-Based Invariant Sweeps [TS-A3]

Property-style checks remain useful for invariants that are already covered
deterministically elsewhere. Future additions would focus on:

- forward-only state transitions
- delivery and reservation invariants
- resource-limit enforcement
- queue-history and reservation edge cases that are awkward to enumerate by hand

Constraint:

- treat these as supplemental confidence checks, not the primary proof of
  behavior

## 4. Test-Hook and CI Follow-Up [TS-A4]

The current suite already uses shared fixtures and helper modules. Any
additional work here would be limited to reusable utilities or dedicated job
wiring that reduces duplication without changing behavior coverage:

- reusable fixtures for newly added broker-heavy suites
- queue and assertion helpers
- wait and poll helpers for new asynchronous cases
- optional CI jobs for newly added deferred suites

Constraint:

- centralize only what is broadly reused; do not move current-suite helpers into
  a new abstraction unless duplication forces it

## Already Canonical

Current suite shape, harness selection, backend classification, and dev-only
benchmark status are owned by [08-Testing_Strategy.md](08-Testing_Strategy.md).
They are not repeated here.

## Backlink

Canonical coverage lives in
[08-Testing_Strategy.md](08-Testing_Strategy.md).

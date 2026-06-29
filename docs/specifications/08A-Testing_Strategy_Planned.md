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

## 3. Future Property-Based Invariant Sweeps [TS-A3]

The current canonical suite already includes focused Hypothesis checks for pure
TaskSpec, lifecycle, queue-identity, task-evidence, and configuration-parser
invariants. Future property-style additions should stay limited to new pure
contracts or demonstrably valuable edge cases that are awkward to enumerate by
hand.

Possible future additions:

- delivery and reservation invariants
- queue-history and reservation edge cases that are awkward to enumerate by hand
- additional monitor policy reducers if they stay pure and cheap

Constraint:

- treat these as supplemental confidence checks, not the primary proof of
  behavior
- do not put live Manager, Consumer, broker reservation loops, process
  execution, or wall-clock lifecycle races under Hypothesis in the default
  suite unless the canonical testing spec is updated with a concrete rationale

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

## Related Plans

- [`docs/plans/2026-06-18-hypothesis-property-testing-plan.md`](../plans/2026-06-18-hypothesis-property-testing-plan.md)

## Backlink

Canonical coverage lives in
[08-Testing_Strategy.md](08-Testing_Strategy.md).

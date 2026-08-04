# Testing Strategy

This document records the tests that exist now and why they are split the way
they are. Deferred test surfaces live in
[08A-Testing_Strategy_Planned.md](08A-Testing_Strategy_Planned.md).

## Why This Shape Exists [TS-0]

Weft tests through the repo-managed environment and a small set of shared
harnesses:

- `.envrc` and the in-repo `.venv` keep verification deterministic.
- `WeftTestHarness` in `tests/helpers/weft_harness.py` owns isolated project
  roots, live runtime tracking, and cleanup.
- `run_cli()` in `tests/conftest.py` drives the real subprocess CLI surface.
- `broker_env`, `queue_factory`, and `task_factory` in `tests/conftest.py`
  provide broker-backed fixtures for queue and task tests.
- `shared` vs `sqlite_only` keeps backend-neutral coverage separate from
  SQLite-specific coverage.
- Hypothesis is a dev-only dependency used for focused property-based
  invariant sweeps. Property tests use the `property` marker and remain in the
  normal domain modules they exercise.
- `tests/specs/test_test_audit_policy.py` enforces the classification tables,
  and `tests/test_harness_registration.py` guards harness-registration plumbing.
- The Postgres-backed check is `bin/pytest-pg --all` for backend-sensitive
  changes.
- The benchmark scripts in `tests/long_session_surface_benchmark.py` and
  `tests/multiqueue_polling_benchmark.py` are dev-only measurement tools, not
  part of the canonical test contract.

The point is not to maximize suite count. The point is to keep the current
contract exercised where it matters and to make backend-sensitive drift easy to
see.

Current classification rule:

- test modules should declare backend scope explicitly through `shared` or
  `sqlite_only`, either directly or through the central classification tables in
  `tests/conftest.py`
- property-based modules should also declare `property`; this marker is a test
  style marker, not a backend-scope marker
- broker-heavy tests run under normal xdist scheduling; parallel contention is
  part of the test signal, and isolation bugs should be fixed in the harness or
  implementation instead of hidden through broad serialization
- broad directory-level audit exemptions are temporary migration scaffolding
  and should disappear once a subtree has been reviewed
- any remaining unaudited debt should stay module-scoped, explicit, and
  reviewable rather than becoming the default home for new tests

Coverage policy:

- patch coverage is the active regression gate for new work and should stay
  materially higher than the legacy project baseline
- project coverage remains at the historical floor until defensive exception
  arms, generated paths, and backend-specific slow paths are classified well
  enough for the number to be meaningful
- after one release cycle with clean pragma/narrowing hygiene, raise project
  coverage to the observed baseline minus a small stability buffer
- broad defensive catches must either be tested, narrowed, or explicitly
  marked `# pragma: no cover - <reason>` so coverage does not confuse
  intentional process-boundary code with missing tests

The terminal handoff reducer requires exhaustive table-driven tests. Its test
table must equal the Cartesian product of all declared states and event kinds,
contain no duplicate cells, and assert the exact next state, action, and
transition ID for valid cells plus the exact rejection for invalid cells.
Every selected reason must be non-empty. Structural reachability and aggregate
coverage helpers do not replace this cell-by-cell proof.

The terminal handoff same-turn selector has one strict order per declared
adapter policy across all eight event kinds. Its table tests cover every
non-empty observation subset under both policies, 510 cases, and each host
adapter routes all 28 unordered event pairs through its declared policy.
Expected priorities are independent test data, not values derived from the
production priority table. Multi-turn cases prove already-reduced stop and
producer-exit level signals cannot starve outcome, seal, or drain expiry.

## Current Coverage [TS-1]

- `tests/cli/` covers subprocess CLI behavior and operator-visible output.
- `tests/commands/` covers command-layer helpers, including direct handler paths,
  queue/output boundaries, and command-control reducer tables such as
  `weft/commands/control_convergence.py`.
- `tests/context/` covers context discovery and backend-aware project setup.
- `tests/core/` covers manager behavior, pipelines, agent/runtime code,
  provider CLI adapters, target execution helpers, and related validation
  surfaces. Pure reducer helpers such as `weft/core/state_machines.py` are
  covered here with table tests that assert structural reachability,
  transition-ID coverage, state coverage, and action coverage. Focused
  property tests also cover pure queue-name classification and read-only task
  evidence queue fallback helpers.
  Terminal handoff coverage pairs the full pure state/event table with real
  spawn/IPC examples. It includes both `outcome -> exit` and
  `exit -> outcome`, channel seal without outcome, transport/serialization
  failure, timeout/result orderings, every non-empty same-turn observation
  subset, abrupt child exit, persistent session error-then-exit, and the public
  CLI fast-function and stored-spec regressions. Installed-workflow coverage
  invokes the environment's `weft` console script from a fresh initialized
  external directory with no test-added `PYTHONPATH`; it covers a
  standard-library function, a local module before and after manager reuse, a
  stored spec, and no-wait/result collection. Preloaded queues, target sleeps,
  retry-only assertions, and `python -m` test adapters are not substitutes for
  those paths.
- `tests/specs/` covers spec-level invariants and cross-surface contracts. This
  tree already includes focused subdirectories such as
  `manager_architecture/`, `message_flow/`, `quick_reference/`,
  `resource_management/`, and `taskspec/`, plus root-level guard tests like
  `test_command_queue_seam.py`, `test_plan_metadata.py`, and
  `test_test_audit_policy.py`.
- `tests/system/` holds repository-level checks for constants, helper behavior,
  backend test plumbing, and release-script invariants. It also contains pure
  property tests for finite configuration-parser boundaries and a live parity
  guard between default-backed environment-loader keys and explicit
  override-normalizer branches.
- `tests/tasks/` covers execution, reservation flow, control messages, process
  titles, observability, interactive behavior, pipeline runtime, and
  task-endpoint behavior. Real queue tests verify custom inbox, outbox, and
  control routing while the reserved lane remains TID-derived.
- `tests/taskspec/` covers TaskSpec validation, immutability, defaults, and
  state transitions. Property tests supplement the examples for generated
  TaskSpec payload resolution, immutable `spec`/`io` sections, resource-limit
  validation, metric peaks, and timestamp coherence. Generated transition
  sequences include one guaranteed-legal prefix so invariant assertions cannot
  pass without exercising a transition.
- `tests/helpers/` and `tests/fixtures/` provide shared harness, backend, and
  scenario setup for the above suites. They are support code, not their own
  test contract.
- `tests/test_harness_registration.py` is a root-level guard for harness
  cleanup and registration behavior.

## What Is Not Canonical [TS-2]

- There is no dedicated `tests/integration/` tree yet. Integration-style
  coverage already lives inside the existing CLI, command, core, task, and
  spec suites.
- There is no dedicated `tests/performance/` tree yet. Current performance work
  is in the dev-only benchmark modules under `tests/`, but those modules are not
  part of the canonical pytest contract.
- There is no dedicated `tests/property/` tree yet. Property-style checks remain
  embedded in normal pytest modules where they are needed.
- Property tests are not the proof mechanism for live Manager, Consumer,
  SimpleBroker reservation, process execution, or wall-clock lifecycle
  behavior. Those paths remain covered by deterministic table tests and real
  harness-backed examples.
- Deferred test surfaces stay in the companion planned doc instead of being
  mixed into this canonical file.

## Related Plans

- [`docs/plans/2026-08-01-terminal-handoff-reducer-plan.md`](../plans/2026-08-01-terminal-handoff-reducer-plan.md)
- [`docs/plans/2026-07-29-deduplication-and-test-integrity-plan.md`](../plans/2026-07-29-deduplication-and-test-integrity-plan.md)
- [`docs/plans/2026-06-18-hypothesis-property-testing-plan.md`](../plans/2026-06-18-hypothesis-property-testing-plan.md)
- [`docs/plans/2026-05-16-task-log-external-logging-and-retention-policy-plan.md`](../plans/2026-05-16-task-log-external-logging-and-retention-policy-plan.md)

## Related Documents

- [08A-Testing_Strategy_Planned.md](08A-Testing_Strategy_Planned.md)
- [07-System_Invariants.md](07-System_Invariants.md)
- [10-CLI_Interface.md](10-CLI_Interface.md)
- [Internal State Machine Helper Plan](../plans/2026-05-13-internal-state-machine-helper-plan.md)

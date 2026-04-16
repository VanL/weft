# Weft Road To Excellent Plan

Status: roadmap
Source specs: see Source Documents below
Superseded by: none

This plan turns the current high-level assessment into an implementation
roadmap. The system already has a strong core idea and a strong core spine.
What it lacks is not more scope. What it lacks is a tighter reliability floor,
firmer extension contracts, and more disciplined synchronization between code,
docs, help, and tests.

The goal is not to turn Weft into a broad agent-control framework. The goal is
to make Weft excellent at being Weft: a durable task substrate with a coherent
agent lane, explicit boundaries, and boring operation under stress.

## Scorecard Baseline

This roadmap uses the current evaluation checklist as its skeleton.

Engineering baseline:

- core architecture: strong
- code organization: strong, with some still-thin extension contracts
- reliability under load: not yet strong enough
- validation and trust boundaries: improved, but still maturing
- testing quality: broad, but too many newer guarantees still live mainly in
  integration tests
- docs and contract discipline: improved, but still too easy to drift

Conceptual baseline:

- everything is a task: strong
- queue-first model: strong
- substrate vs product boundary: mostly strong
- authority model for agents: much clearer now, but still worth hardening
- extension coherence: improved, but builtins are not yet as first-class as
  the older surfaces

## Scope Lock

This roadmap covers only the work required to make Weft excellent on its own
terms.

Implement in scope:

1. make manager bootstrap and parallel reuse converge reliably under load
2. formalize builtin TaskSpecs as a first-class current contract
3. harden the delegated-agent lane without turning Weft into a provider setup
   or approval-policy product
4. reduce contract drift between code, docs, help text, and tests
5. improve failure classification and observability for operator use

Do not implement as part of this roadmap:

- a new planner stack or second scheduler
- provider account setup, login flows, or token management
- an approval-policy engine for higher-level agents
- broad product-layer workflows that belong in systems built on Weft
- hidden bootstraps or sidecars that become required for correctness

If a proposed step starts pulling Weft into provider lifecycle ownership or a
new control-plane role, split it out or reject it.

## Goal

Reach a state where:

- core runtime behavior is boring under stress
- the builtin extension surface is as legible as the rest of the system
- the delegated agent lane is explicit, honest, and well-bounded
- the docs say exactly what the code does
- new feature slices have a harder time drifting from the contract

## Design Position

This roadmap assumes five anchor positions:

- one canonical durable spine stays the only execution path
- Weft owns lifecycle, identity, queues, persistence, and coarse authority
- external agent CLIs own their inner reasoning policy and setup state
- builtin helpers are ordinary Weft tasks, not privileged bootstrap logic
- bare command execution stays a command-execution surface, not a mixed
  command-or-spec guessing surface

## Source Documents

Primary specs:

- [`docs/specifications/00-Overview_and_Architecture.md`](../specifications/00-Overview_and_Architecture.md)
- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md)
  [MA-1], [MA-3]
- [`docs/specifications/04-SimpleBroker_Integration.md`](../specifications/04-SimpleBroker_Integration.md)
  [SB-0]
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
  [CLI-1.1.1], [CLI-1.4]
- [`docs/specifications/10B-Builtin_TaskSpecs.md`](../specifications/10B-Builtin_TaskSpecs.md)
- [`docs/specifications/11-CLI_Architecture_Crosswalk.md`](../specifications/11-CLI_Architecture_Crosswalk.md)
- [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
  [AR-7]
- [`docs/specifications/13A-Agent_Runtime_Planned.md`](../specifications/13A-Agent_Runtime_Planned.md)
  [AR-A3], [AR-A5]

Plans to align with rather than replace:

- [`docs/plans/2026-04-13-detached-manager-bootstrap-hardening-plan.md`](./2026-04-13-detached-manager-bootstrap-hardening-plan.md)
- [`docs/plans/2026-04-13-manager-bootstrap-readiness-and-cleanup-test-plan.md`](./2026-04-13-manager-bootstrap-readiness-and-cleanup-test-plan.md)
- [`docs/plans/2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md`](./2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md)
- [`docs/plans/2026-04-14-builtin-taskspecs-and-spec-resolution-plan.md`](./2026-04-14-builtin-taskspecs-and-spec-resolution-plan.md)

## Context and Key Files

Core reliability path:

- `weft/commands/_manager_bootstrap.py`
- `weft/commands/run.py`
- `weft/core/manager.py`
- `weft/manager_process.py`
- `tests/cli/test_cli_run.py`
- `tests/specs/manager_architecture/`

Builtin extension path:

- `weft/commands/specs.py`
- `weft/commands/run.py`
- `weft/cli.py`
- `weft/builtins/`
- `docs/specifications/10B-Builtin_TaskSpecs.md`

Delegated agent path:

- `weft/core/agents/backends/provider_cli.py`
- `weft/core/agents/provider_cli/registry.py`
- `weft/core/agents/provider_cli/settings.py`
- `weft/core/agents/provider_cli/probes.py`
- `docs/specifications/13-Agent_Runtime.md`

Contract-drift path:

- `weft/cli.py`
- `weft/commands/*.py`
- `docs/specifications/*.md`
- CLI black-box tests and spec-level tests

## Comprehension Questions

If the implementer cannot answer these before editing, they are not ready:

1. Which runtime path is still allowed to be contention-sensitive enough that
   parallel `weft run --no-wait` can intermittently fail?
2. What does Weft own in the delegated agent lane, and what does it explicitly
   not own?
3. Why is a builtin helper task acceptable while a hidden startup probe is not?
4. Which current extension surfaces are first-class in the spec corpus, and why
   are builtins still thinner than they should be?
5. What would have to become true for help text and implementation to drift
   less often than they do now?

## Invariants and Constraints

The following must stay true:

- TID generation and forward-only task state remain unchanged
- queue-first lifecycle remains the public system model
- no second scheduler or daemon is introduced
- manager availability still flows through one canonical bootstrap path
- `provider_cli` remains a delegated lane, not a full provider-control plane
- builtin helpers remain ordinary Weft tasks on the durable spine
- docs and help must stay synchronized with current runtime behavior

## Roadmap By Dimension

### 1. Core Architecture And One-Spine Discipline

Owner:

- the canonical execution spine and extension rules

Boundary:

- `TaskSpec -> Manager -> Consumer -> TaskRunner -> runtime/backend`
- `weft/core/manager.py`
- `weft/core/tasks/consumer.py`
- `weft/core/tasks/runner.py`
- `weft/commands/run.py`

Problem:

- the architecture is good, but the extension surfaces are growing faster than
  the architectural guardrails around them

Required action:

1. document and enforce the rule that new features must attach to the existing
   durable spine rather than invent parallel task-like helpers
2. keep builtin helpers and delegated diagnostics as ordinary tasks or explicit
   diagnostics flows, not bootstrap exceptions
3. codify these rules in the nearest current-contract docs so future slices
   have less room to drift

Verification:

- architecture review of new slices against the owning specs
- no new execution path outside the existing durable spine

Done when:

- new capability work extends one spine rather than introducing sidecar
  sub-systems

### 2. Reliability Under Load: Manager Bootstrap And Reuse

Owner:

- manager bootstrap and canonical lifecycle path

Boundary:

- `weft/commands/_manager_bootstrap.py`
- `weft/commands/run.py`
- manager registry and readiness proof

Problem:

- load-sensitive parallel manager reuse failures are still possible
- even intermittent submission failures under concurrent `weft run --no-wait`
  weaken the substrate promise

Required action:

1. turn the current load-sensitive parallel manager-reuse failure into a
   concrete failure model with traces
2. decide whether the fault is:
   - readiness proof still too weak
   - duplicate-launch handoff still racy
   - stale/canonical manager selection lag
   - submission path assuming manager stability too early
3. make parallel bootstrap attempts converge cleanly to one usable canonical
   manager without transient client-visible failure
4. make the convergence rule explicit in the current spec, not just in tests

Verification:

- repeated `tests/cli/test_cli_run.py::test_parallel_manager_reuse_converges_to_single_manager_under_repeated_bootstrap`
- targeted bootstrap/readiness tests from the existing manager-hardening plans
- full suite under xdist

Done when:

- manager bootstrap under contention is boring
- parallel `weft run --no-wait` races no longer surface spurious submission
  failures
- the spec says plainly what convergence behavior is required

### 3. Validation And Trust Boundaries

Owner:

- delegated `provider_cli` runtime contract

Boundary:

- `weft/core/agents/backends/provider_cli.py`
- `weft/core/agents/provider_cli/registry.py`
- `weft/core/agents/provider_cli/settings.py`
- `weft/core/agents/provider_cli/probes.py`

Problem:

- the delegated lane is conceptually cleaner now, but still needs more polish
  around explicit authority, diagnostics, and verification

Required action:

1. keep the startup-vs-runtime boundary strict:
   - static validation stays static
   - dynamic truth only on real execution or explicit diagnostics
2. improve provider failure classification so operators can tell apart:
   - executable not found
   - unsupported command surface
   - auth/login failure
   - bad provider output shape
   - task-level authority mismatch
3. make the operator-visible contract for `.weft/agents.json` and
   `.weft/agent-health.json` harder to misunderstand
4. ensure the builtin probe helper remains a client of this contract, not a
   second mutation path

Verification:

- targeted provider-cli tests
- explicit builtin probe-helper tests with seeded acceptable targets
- optional live-provider checks where available

Done when:

- delegated failures are sharper and easier to classify
- the delegated lane is explicit without becoming a control plane

### 4. Extension Coherence: First-Class Builtin TaskSpecs

Owner:

- builtin TaskSpec mechanism and builtin helper docs

Boundary:

- `weft/builtins/`
- `weft/commands/specs.py`
- `weft/commands/run.py`
- `docs/specifications/10B-Builtin_TaskSpecs.md`

Problem:

- builtins now exist, but the builtin model is still thinner than the rest of
  the public surfaces
- semantics like listing, shadowing, read-only status, and future evolution are
  only lightly specified

Required action:

1. promote builtin TaskSpecs from “feature that exists” to a clearly bounded
   current contract
2. define explicitly:
   - where builtin assets live
   - which commands may resolve them
   - shadowing order
   - listing/show/delete semantics
   - what makes a builtin safe enough to ship
3. require each builtin to have operator-facing documentation, not just source
4. add a lightweight acceptance bar for new builtins:
   - clear purpose
   - stable output shape
   - explicit non-goals
   - deterministic tests

Verification:

- CLI black-box tests for list/show/delete/shadowing
- documentation review against shipped builtins
- build/package validation so builtin JSON assets are always included

Done when:

- builtin helpers feel like a deliberate extension surface rather than a useful
  accident
- each builtin has a stable documented contract

### 5. Testing Quality And Invariant Coverage

Owner:

- stress, contract, and extension test coverage for the newest surfaces

Boundary:

- manager bootstrap/reuse tests
- builtin extension tests
- delegated runtime tests
- CLI contract tests

Problem:

- the suite is broad, but newer guarantees still rely too much on large
  integration passes and not enough on crisp small invariants

Required action:

1. add smaller, sharper invariants for:
   - manager convergence under contention
   - builtin shadowing, listing, and read-only behavior
   - delegated validation boundary and explicit diagnostics
2. keep at least one full-suite xdist gate for the contention-driven manager
   class of bugs
3. keep builtin probe-helper tests deterministic by seeding known acceptable
   targets before running
4. add focused help/contract tests where drift has happened before

Verification:

- targeted tests plus full-suite xdist
- no acceptance of isolated sequential passes as sufficient for contention bugs

Done when:

- the newest guarantees are locked by smaller invariants and still proven in
  the full suite under load

### 6. Docs And Contract Discipline

Owner:

- CLI/help/spec/test synchronization

Boundary:

- `weft/cli.py`
- command help text
- `docs/specifications/`
- black-box CLI tests

Problem:

- the system still allows help text, docs, and runtime behavior to diverge too
  easily when a feature slice lands

Required action:

1. identify the highest-drift surfaces:
   - `weft run --spec`
   - builtin task-spec semantics
   - delegated runtime validation behavior
   - manager bootstrap contract
2. add explicit regression tests for current help and user-facing command
   semantics where the system has drifted before
3. prefer shared resolution and shared rendering helpers over duplicated command
   descriptions or parallel lookup paths
4. make new spec-affecting slices update the nearest current-contract doc and
   relevant help text in the same change

Verification:

- black-box CLI help tests
- grep-style contract checks on critical phrases
- review for bidirectional traceability

Done when:

- contract drift becomes harder to introduce than it is today

### 7. Conceptual Coherence Guardrails

Owner:

- substrate boundary and Weft-owned responsibility lines

Boundary:

- current specs
- builtin surface
- delegated agent lane
- future feature plans

Problem:

- the core conceptual model is strong, but future slices could still blur Weft
  into a product-layer control system if the boundary is not restated often
  enough

Required action:

1. keep “everything is a task” explicit in newer surfaces, especially builtins
   and delegated helpers
2. keep queue-first lifecycle and Weft-owned session/task identity explicit
3. keep authority coarse and substrate-level:
   - Weft owns bounded vs general and explicit narrowing
   - Weft does not own provider approval workflows or auth lifecycle
4. reject features that only work by hidden ambient behavior or hidden startup
   work

Verification:

- review each new slice against these boundary rules
- keep current-contract docs explicit about what Weft does not own

Done when:

- the system stays conceptually coherent as new agent and helper surfaces land

### 8. Operator Legibility And Failure Reporting

Owner:

- user-facing diagnosis surfaces

Boundary:

- CLI output
- task failure messages
- delegated provider errors
- manager bootstrap errors

Problem:

- the system is internally coherent, but operator-facing failures are not yet as
  cleanly classified as they should be for an excellent substrate

Required action:

1. define a small failure taxonomy for current operator-visible classes:
   - spec/configuration error
   - runtime/plugin availability error
   - manager/bootstrap error
   - delegated provider error
   - operator environment/setup error
2. ensure the main CLI paths report those classes clearly and consistently
3. where useful, include compact actionable next-step hints without inventing a
   large diagnosis subsystem

Verification:

- black-box CLI tests for representative failures
- manual spot checks on the highest-friction flows

Done when:

- a user can usually tell which layer failed without reading source code

## Sequencing

The recommended order is:

1. reliability under load
2. validation and trust boundaries
3. extension coherence
4. testing quality and invariant coverage
5. docs and contract discipline
6. operator legibility and failure reporting

Reason:

- reliability is the floor
- trust-boundary honesty is next because delegated agents are the sharpest
  extension surface
- builtin-contract clarity follows because builtins are now a public surface

## Verification Plan

Run per workstream, then full suite:

- manager-focused targeted tests
- builtin CLI and packaging tests
- delegated runtime targeted tests
- `source .envrc && uv run ruff check weft tests`
- `source .envrc && uv run mypy weft`
- `source .envrc && uv run pytest`

For manager work specifically:

- do not accept isolated `-n 0` passes as sufficient
- verify under normal xdist pressure because the bug class is contention-driven

## Review Plan

- independent review after each workstream
- one final review across the full roadmap once the last slice lands

Review focus:

- manager bootstrap under contention
- substrate vs product boundary
- builtin extension safety and clarity
- contract drift across docs/help/runtime/tests

## Related Documents

- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md)
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
- [`docs/specifications/10B-Builtin_TaskSpecs.md`](../specifications/10B-Builtin_TaskSpecs.md)
- [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)

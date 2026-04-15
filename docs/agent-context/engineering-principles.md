# Weft Engineering Principles

These are the repo-specific engineering rules that come up repeatedly when
changing Weft. They are short on purpose: each principle should be actionable
during a single implementation session.

## 1. Extend the Existing Durable Spine

The canonical execution path is:

`TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log`

If a change touches execution, timeout handling, reservation semantics, result
delivery, or observability, start by extending that path. Do not introduce a
second execution path or side channel unless the relevant spec explicitly
requires it.

## 1.5 Keep Weft as the Substrate

Weft is the durable task-runner substrate, not the higher-level agent
management or orchestration layer.

When a feature starts trying to predict agent readiness, hide orchestration
policy in `weft run`, or add proactive health-management behavior before real
execution, stop and re-check the boundary. In Weft, the right default is to
make task submission and execution reliable, observable, and easy to validate
explicitly. Higher-level intelligence belongs above this layer.

Agent support and limited runners still belong here. An agent is a task. A
dangerous task in a restricted container is still a task. Runtime-specific
knowledge is acceptable when it directly serves execution, isolation,
observability, or clear failure on the normal durable spine.

## 2. Queues Are the Canonical State

Weft is evented through queues, not through an auxiliary database layer.
Observable task behavior should be proven through queue messages, task logs,
reserved-queue outcomes, and task outboxes.

When a feature needs durable state, first ask whether the existing queue model
already expresses it. Avoid ad hoc state stores that compete with queue truth.

## 3. Canonicalize at Boundaries, Then Stay Strict

Normalize CLI or TaskSpec input once, at the boundary, through one shared
validation path. After that, internal code should operate on the canonical
form.

When a contract changes, update forward. Do not leave permanent runtime
fallback readers that accept multiple incompatible shapes unless the spec
explicitly requires compatibility behavior.

For TaskSpec specifically, keep the template/resolved boundary explicit:
templates may stay partial, but resolved tasks must go through the shared
resolution path before construction and then remain frozen on `tid`, `spec`,
and `io`.

## 4. Real Broker and Process Tests Beat Mock-Heavy Tests

For core lifecycle behavior, use the real system:

- `WeftTestHarness` for isolated end-to-end and CLI flows
- `broker_env` for real SQLite-backed queues
- `task_factory` or real `Consumer` / `TaskRunner` code paths

Avoid mock-only tests for:

- queue reservations
- manager/task lifecycle
- timeout/resource behavior
- task state transitions
- final result delivery

Mock only boundaries that are genuinely external or nondeterministic.

## 5. Read Specs and Code Before Inference

Do not infer behavior from module names or mental models alone. Read:

1. the relevant spec,
2. the current implementation,
3. the closest existing test,
4. then decide what to change.

This is especially important for Weft because details such as reserved queue
policy, runtime-only queues, and TaskSpec immutability are easy to get almost
right while still breaking invariants.

## 6. Keep Traceability Bidirectional

Treat documentation traceability as part of the implementation, not as optional
cleanup.

- When writing a plan, cite the exact spec section(s) and reference code(s) the
  work implements.
- When implementing a plan, update the touched spec with a backlink to that
  plan and refresh any nearby implementation snapshot/status/mapping notes that
  describe current code ownership or behavior.
- When changing code at a spec-owned boundary, keep module and function
  docstrings pointing back to the governing spec sections so the code-to-spec
  path stays explicit in both directions.

## 7. Boundary-First Risky Plans

For risky or boundary-crossing work, name what must not change before breaking
the work into tasks.

For Weft this usually means spelling out:

- whether the change must stay on the existing
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log` spine
- which queue names, state transitions, or result payloads must remain stable
- whether the template/resolved `TaskSpec` boundary is changing
- whether runtime-only `weft.state.*` queues must stay out of persistence or
  history features
- what should stay real in tests instead of being mocked away
- what rollback, rollout order, or post-deploy observation is required

If a risky plan cannot say these clearly, it is not ready for implementation.

## Secondary Rules

- Use `build_context()` and `WeftContext` instead of re-implementing project
  discovery or broker-target resolution.
- Use generator-based queue history reads for append-only queues instead of
  correctness-critical `peek_many(limit=...)` calls.
- Reuse task/watcher queue handles on live runtime paths instead of opening new
  queue connections casually.
- Check blast radius before editing shared contracts such as queue names,
  TaskSpec schema, CLI output shape, or result payloads.
- Document plans for zero-context engineers: files to read, files to change,
  invariants to protect, and exact verification commands.
- Prefer explicit spec-section references such as `[MF-2]` or `[CLI-1.1.1]`
  over broad document-only references when tying a change to docs.
- Prefer explicit rejection over silently ignoring unsupported fields or modes.
- Keep runtime executability checks opt-in. Do not turn `weft run`,
  manager submission, or ordinary task startup into a speculative "can this
  binary run here" gate; explicit validation or diagnostic surfaces own that,
  and the normal execution path should attempt the real run and report the
  concrete startup failure. Hidden probes pull Weft upward into agent
  management, which is outside this layer's job.
- Keep future-proofing out unless the current spec requires it.

## 8. Fit Test For Agent And Runner Features

When adding agent or runner features, ask:

1. Is this about running, constraining, observing, controlling, or composing a
   task?
2. Does it stay on the canonical durable spine?
3. Is any runtime-specific knowledge required for execution, or is it really
   ecosystem management?
4. If it is convenience-only, can it remain explicit, optional, and
   non-authoritative?
5. Does it silently write policy, cache truth, or create a second control
   plane?

Good fits:

- agent runtimes that preserve queue and task semantics
- restricted runners for dangerous work
- explicit runtime preparation for a real slow path
- explicit discovery helpers that report what is available

Bad fits:

- hidden preflight in `weft run`
- background provider probing or image prep
- health caches treated as startup truth
- automatic mutation of project config without an explicit documented contract
- provider-specific management features that grow faster than the core task
  model

## Warning Signs

Sessions usually go sideways when one of these happens:

- a second code path appears instead of extending the canonical one
- a change relies on intuition rather than reading the relevant spec/test/code
- the failing test is skipped on a non-trivial bug
- a change is labeled "pre-existing" without proof
- a later stage starts re-deriving facts that an earlier stage already owns

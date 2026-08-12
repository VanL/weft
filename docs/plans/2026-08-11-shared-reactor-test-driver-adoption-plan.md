# Shared Reactor Test Driver Adoption Plan

Status: completed
Source specs: docs/specifications/08-Testing_Strategy.md [TS-0], [TS-1], [TS-2], [TS-3.1]; docs/specifications/07-System_Invariants.md [IMPL.9], [IMPL.10]
Superseded by: none

Class: 3+P (effective Class 5 treatment). The implementation creates a reusable
test workflow, adopts it across multiple task, manager, pipeline, and
spec-contract suites, and changes normative testing guidance. It does not alter
product runtime behavior, but a defective driver could hide a real reactor race
or create broad false failures, so the process modifier and independent review
bar apply.

Plan type: implementation with spec revision. Promotion strategy: B, atomic.
The [TS-0] testing rule, shared helper, direct helper tests, reciprocal code
reference, and first firing migration land in the same slice.

Hardening: required. The helper itself is test-only, but it coordinates manual
turn ownership, task activity waits, worker-result settlement, deadlines, and
failure diagnostics across several real broker-backed suites.

## 1. Goal

Add one small, directly tested `tests/helpers/reactor_driver.py::drive_until`
engine for Weft tests that manually own a task reactor. Centralize only the
mechanics that must be identical: absolute monotonic deadlines, reactor turns
followed by observation and deadline checks, clipped owner-legal waits, one
final ready-worker-result turn at the deadline, typed evidence return, and lazy
caller diagnostics. Then migrate the local wrappers that implement that same
protocol while preserving
their domain names, evidence conditions, timeout budgets, and failure detail.

The change must reduce the chance that a deadline race is fixed in one test file
but remains latent elsewhere. It must not force PID waits, process joins, queue
history cursors, multi-actor interleavings, or tests of timing behavior through
an abstraction that does not own those semantics.

## 2. Source Documents

Normative sources:

- [`docs/specifications/08-Testing_Strategy.md`](../specifications/08-Testing_Strategy.md)
  [TS-0] owns shared test harness and fixture policy, [TS-1] owns the current
  domain-suite coverage layout, and [TS-2] keeps test support code from becoming
  a product contract. [TS-3.1] governs the one local Ruff suppression needed to
  keep diagnostic callback failure secondary to the timeout assertion.
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [IMPL.9] owns the bounded worker-result channel and reactor drain, and
  [IMPL.10] requires `process_once()` and `wait_for_activity()` to remain on one
  drive-owning thread.

Required process and testing guidance:

- [`AGENTS.md`](../../AGENTS.md), especially the real-harness and test-design
  rules.
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
  [DOM-15] for the Class 3+P classification.
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
  for DRY, YAGNI, boundary validation, and firing-test expectations.
- [`docs/agent-context/runbooks/testing-patterns.md`](../agent-context/runbooks/testing-patterns.md)
  for bounded polling, real broker/process proof, completion gaps, and lifecycle
  closure under load.
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md),
  [`hardening-plans.md`](../agent-context/runbooks/hardening-plans.md), and
  [`review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)
  for implementation ordering and review gates.
- [`docs/lessons.md`](../lessons.md), especially "Test Sleep Hygiene," "Poll
  Floors And Grace Windows," and the target-task cursor rule. The April 27
  lesson remains authoritative about keeping domain-specific wait semantics
  local. This plan narrows its new shared layer to loop mechanics that August 11
  regressions proved must be identical.

Concrete evidence that motivated the change:

- `1edafaf27af451d6533ea0b7f65b856ff4474c39` added the final ready-result
  deadline drain and direct regression in
  `tests/tasks/test_task_observability.py`.
- `f5fe174b` replaced assumed pipeline turn counts with observation-driven
  reactor progress in `tests/tasks/test_pipeline_runtime.py`.
- `cb6bbd17` expanded TaskMonitor reactor timeout diagnostics in
  `tests/tasks/test_task_monitor.py`.

These commits are historical evidence, not normative behavior sources.

## 3. Spec Baseline

- `1edafaf27af451d6533ea0b7f65b856ff4474c39`:
  `docs/specifications/08-Testing_Strategy.md` [TS-0], [TS-1], [TS-2] and
  `docs/specifications/07-System_Invariants.md` [IMPL.9], [IMPL.10] at plan
  authoring time.
- Promotion baseline: record the implementation slice identifier after the
  atomic spec/helper/test migration lands. Before a commit exists, record the
  baseline SHA above plus the exact worktree diff for Spec 08 and the new
  helper/test files.
- Implementation evidence on 2026-08-11 remains an uncommitted worktree based
  on `1edafaf27af451d6533ea0b7f65b856ff4474c39`. No promotion commit identifier
  exists, so this plan and its index entry remain `draft` until the owner elects
  to commit the verified slice.

## 4. Proposed Spec And Process Delta

### 4.1 Promotion strategy

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| `docs/specifications/08-Testing_Strategy.md` | B, atomic | [TS-0] shared harness policy and `## Related Plans` |

The spec text, its implementation pointer, `tests/helpers/reactor_driver.py`,
the direct helper tests, the first migrated wrapper, and reciprocal `Spec:`
module references land together. Do not land a spec mapping that points to a
missing helper, and do not land a helper that claims the new [TS-0] rule before
the text exists.

### 4.2 Exact [TS-0] insertion

Insert the following after the existing [TS-0] shared harness and fixture list,
before "Current classification rule":

> Manual in-process tests with one task reactor and the exact repeated control
> frame `process_once -> observe -> bounded owner wait` use the shared
> `tests/helpers/reactor_driver.py` timing engine for condition observation, an
> absolute monotonic deadline, clipped owner-legal waits, one final pending-worker
> turn at the deadline, and lazy caller-owned diagnostics. Domain-named local
> wrappers remain appropriate when they define the evidence, add
> domain-specific stepping, or preserve a useful failure shape.
>
> PID, process-exit, file, database-release, queue-native, and
> `WeftTestHarness` completion waits do not route through the reactor driver;
> neither do nested or composed reactor drivers, multi-actor interleavings,
> domain-specific deadline-settlement protocols, or tests whose subject is wait
> ownership, timeout selection, poll cadence, or exact turn ordering. A
> liveness marker proves only that evidence appeared. Exact-count and absence
> assertions require a named producer-closure boundary, meaning proof that the
> relevant producer can no longer emit the event being counted, before ordered
> history is asserted.

Add this plan under Spec 08 `## Related Plans`. The helper module docstring and
its direct test module cite Spec 08 [TS-0]. Spec 07 needs no text change because
the helper follows [IMPL.9] and [IMPL.10] without changing them.

### 4.3 Exact testing-runbook insertion

Add this subsection under `## Test Design Rules` in
`docs/agent-context/runbooks/testing-patterns.md`, after the six existing rules:

```markdown
### Manual Reactor Test Drivers

Use `tests.helpers.reactor_driver.drive_until` when a test owns one in-process
task reactor and repeatedly calls `process_once()` plus
`wait_for_activity()` until caller-defined evidence matches. Keep a local,
domain-named wrapper when it explains what is being observed or supplies richer
diagnostics; delegate only the common deadline/turn/wait mechanics.

The driver owns liveness, not global safety. Before asserting that an event is
unique or absent, establish producer closure: for example, a real process has
exited, or reactor finalization has completed and the test has separately
proved that no relevant worker/producer remains live and no deliverable result
remains. The driver's final ready-result turn alone is never producer closure.
A terminal-looking task-log row alone is not producer closure either.

Do not use the reactor driver for process joins, PID/file/database release,
queue-history cursor management, native event/watcher waits, multi-actor
interleaving protocols, nested/composed reactor drivers, domain-specific
deadline settlement, or tests of timeout and wait behavior themselves. Use the
strongest synchronization primitive owned by that boundary.
```

Advance `docs/agent-context/context.index.yaml` to
`updated_at: 2026-08-11` in the same process-guidance slice. Do not change its
read order, roles, or document inventory.

## 5. Current Structure And Evidence

### 5.1 Existing strongest implementation

`tests/tasks/test_task_observability.py::drive_task_until` currently owns the
strongest common protocol:

1. call one real `process_once()` turn;
2. evaluate caller evidence;
3. clip `wait_for_activity()` to the remaining deadline;
4. at deadline, check `_has_pending_worker_results()`;
5. if a worker result is ready, run exactly one final turn and re-check;
6. fail with status, stop, turn, and worker diagnostics.

Its direct test proves the deadline race with deterministic monotonic values.
The pure race belongs in the shared helper's direct test module, while a
wrapper-level version remains in this file to prove that `drive_task_until`
passes pending-worker evidence into the shared engine.

### 5.2 Repeated mechanical loops

The authoring inventory found 19 deadline-bound loops that call
`process_once()` across 13 files. That number is evidence of repeated mechanics,
not a migration target: several loops own process, cursor, actor-order, or
failure semantics that must remain local.

The first required adoption group contains the eight wrappers whose control
frame is the same `step -> observe -> bounded owner wait` protocol:

| File | Local wrapper | Required disposition |
|------|---------------|----------------------|
| `tests/tasks/test_task_observability.py` | `drive_task_until` | Keep the domain wrapper; delegate the engine and preserve task diagnostics. |
| `tests/tasks/test_control_channel.py` | `_drive_task_until` | Keep or inline based on call count; use the shared engine. |
| `tests/tasks/test_agent_execution.py` | `_drive_consumer_until` | Keep the wrapper and provider-specific failure reporting. |
| `tests/tasks/test_command_runner_parity.py` | `_drive_consumer_until` | Keep its timeout-detail callback; adapt it to lazy shared diagnostics. |
| `tests/tasks/test_task_execution.py` | `_drive_consumer_until` | Keep worker-stack diagnostics local; delegate mechanics only. |
| `tests/specs/message_flow/test_agent_spawning_transition.py` | `_drive_task_until_complete` | Preserve immediate failure on a non-completed terminal state in the local observer. |
| `tests/specs/message_flow/test_spawning_transition.py` | `_drive_task_until_complete` | Delegate the common engine. |
| `tests/core/test_manager.py` | `drive_manager_until` | Delegate this one simple wrapper only; do not generalize other Manager loops. |

Two additional required wrappers in the pipeline module can reuse the timing
engine without losing their local meaning:

| File | Local wrapper | Required disposition |
|------|---------------|----------------------|
| `tests/tasks/test_pipeline_runtime.py` | `_drive_consumer_once_until_idle` | Preserve the mandatory first turn, then model the compound worker-idle state as the observation. Add a regression that rules out a skipped or doubled first turn. |
| `tests/tasks/test_pipeline_runtime.py` | `_drive_pipeline_until_snapshot` | Model queue draining as `observe`, return the matching snapshot, and preserve pipeline diagnostics. |

### 5.3 Explicit non-migrations

The following remain outside the helper in this plan:

- `tests/helpers/weft_harness.py::WeftTestHarness.wait_for_completion`, which
  owns target-scoped cursor overlap for PostgreSQL commit visibility, terminal
  interpretation, outbox fallback, diagnostics, and queue closure;
- `tests/core/test_manager.py::wait_for_log_event` and the deliberately tuned
  Manager multi-process interleaving loops documented in the April 27 lesson;
- `tests/tasks/test_service_task.py::drain_worker_results_until`, whose first
  `process_once()` is mandatory reactor-owner claiming and whose deadline
  settlement is another unconditional `process_once()`. The shared engine
  supports the first turn, but its final turn requires positive pending-work
  evidence, so this caller must remain local rather than pass an artificial
  always-true pending callback;
- both `drive_task_monitor_until_idle` helpers in
  `tests/core/test_task_monitoring.py` and
  `tests/tasks/test_task_monitor.py`. Their deadline settlement performs a
  direct `_drain_worker_results()` rather than another ordinary
  `process_once()`; the rich helper also rebuilds diagnostics and repeats its
  entire assertion frame after that drain;
- TaskMonitor's outer `drive_task_monitor_until`,
  `drive_task_monitor_until_observed`, and `_read_control_reply`, which compose
  due-time forcing, nested idle settlement, and request correlation;
- PID/process-exit, file readiness, database-release, event/barrier, native
  watcher, and subprocess `join`/`wait` loops;
- tests whose subject is `next_wait_timeout()`, ownership rejection, poll
  cadence, activity-wait behavior, or exact turn order;
- fixed-turn interactive `_spin()` helpers until each call site has a named
  evidence condition. This plan must not mechanically replace a fixed count
  with an opaque generic predicate;
- all generic task-log ordering or absence assertions. A separate plan may add
  domain-aware task-log records and terminal classification after repeated
  call sites justify it.

### 5.4 Post-migration audit, 2026-08-11

The implementation reran an AST inventory over test loops containing
`process_once()`. Every remaining loop falls into a Section 5.3 exclusion:

- task-evidence probe threads and Manager autostart/log/cleanup loops own
  multi-actor, multi-queue, child-process, or tuned interleaving semantics;
- ServiceTask and TaskMonitor loops own unconditional or drain-only deadline
  settlement that differs from the shared pending-gated turn;
- signal deferral, worker-lane delivery, activity-wait, cleanup, and exact-turn
  tests make the reactor order itself the subject under test;
- the foreground harness loop owns process service and lifecycle closure.

No second compatible repeated engine was found. No additional migration or
policy gate is justified by this audit.

## 6. Context And Key Files

### Files to add

- `tests/helpers/reactor_driver.py`: shared test-only engine.
- `tests/system/test_reactor_driver.py`: direct pure tests of engine ordering,
  deadlines, final settlement, diagnostics, and invalid configuration.

### Files to modify in the atomic helper slice

- `docs/specifications/08-Testing_Strategy.md`
- `docs/agent-context/runbooks/testing-patterns.md`
- `docs/agent-context/context.index.yaml`
- `docs/ruff-suppression-registry.md`
- `docs/plans/2026-08-11-shared-reactor-test-driver-adoption-plan.md`
- `docs/plans/README.md`
- `tests/tasks/test_task_observability.py`
- `tests/specs/test_ruff_policy.py`

### Files to modify during adoption

- the nine test modules listed as required migrations in Section 5.2;
- `docs/lessons.md`, after the migrations prove the final boundary;
- no production file under `weft/`, `bin/`, `integrations/`, or `extensions/`.

### Read before editing

- `weft/core/tasks/base.py`: `process_once()`, `wait_for_activity()`,
  `_has_pending_worker_results()`, `_worker_activity_snapshot()`, and drive
  ownership validation.
- the complete local wrapper and all its call sites before replacing its loop;
- `tests/helpers/weft_harness.py::wait_for_completion` as a deliberate example
  of a wait that must not be flattened into this driver;
- the April 27 "Test Sleep Hygiene" lesson before deciding that an additional
  local loop is a migration candidate.

### Comprehension questions before implementation

1. Why must `process_once()` and `wait_for_activity()` stay on the same owner
   thread, and which [IMPL.10] check enforces that?
2. Why is a worker result reported ready at the absolute deadline allowed one
   final turn, while the deadline itself is never reset?
3. Which migrated wrappers consume evidence as part of observation, and why
   must their local name and diagnostics remain?
4. Why does a terminal task-log marker not prove that a real task process can no
   longer publish another row?
5. Why must `WeftTestHarness.wait_for_completion` retain its target-scoped
   reorder window instead of using this helper?

An implementer who cannot answer all five should stop before editing.

## 7. Helper Contract

The implementation should expose this conceptual shape. Type details may vary
only if the same contract stays explicit and the direct tests prove it:

```python
def drive_until[T](
    observe: Callable[[], T],
    matches: Callable[[T], bool],
    *,
    step: Callable[[], None],
    wait: WaitForActivity,
    timeout: float,
    wait_slice: float = 0.02,
    pending_work: Sequence[Callable[[], bool]] = (),
    diagnostics: Callable[[], object] | None = None,
) -> T: ...
```

`WaitForActivity` is a small test-local `Protocol` whose call accepts the
keyword argument `timeout: float`. This permits direct use of
`task.wait_for_activity` and makes clipped-wait ownership visible in the API.

Required semantics, in order:

1. Reject non-positive `timeout` or `wait_slice` with `ValueError` before any
   callback runs.
2. Use `time.monotonic()` for the one absolute deadline. The deadline governs
   further waits and settlement; it cannot preempt a synchronous callback or
   cancel the one reactor turn paired with a completed owner wait.
3. Call `step()` once before the first observation. This required first turn
   preserves the existing wrappers' reactor-owner claim and `step -> observe`
   behavior. Increment the turn count, observe, and return the evidence if it
   matches. Evidence that was already true therefore still receives one turn,
   matching the local loops being replaced.
4. After every nonmatching observation, compute remaining time. If it is
   positive, call `wait(timeout=min(wait_slice, remaining))`, then begin the
   next turn with `step()`. The turn paired with that bounded owner wait is
   allowed to run even when the wait returns at or just after the deadline;
   synchronous reactor turns cannot be preempted, and this preserves the
   existing `drive_task_until` boundary behavior. There is no observation
   between that wait and its paired turn.
5. After the paired step, increment the turn count, observe, and return matching
   evidence. Recompute remaining time after a miss. Once it is non-positive,
   do not wait or start another ordinary turn. Enter only the deadline
   settlement path below. In particular, an observation that consumes the
   remaining budget must not be followed by an ordinary turn.
6. The most recent nonmatching observation is the boundary observation. If any
   `pending_work` callback then returns
   true, call exactly one final non-waiting `step()`, increment the turn count,
   observe once more, and return if it matches. Do not loop or extend the hard
   deadline.
7. If no pending callback is true, or the one settlement step still does not
   match, raise `AssertionError` containing the timeout, turn count, and
   latest observation. Evaluate `diagnostics()` lazily only on failure and add
   its representation to the message.
8. If diagnostics itself raises, keep the timeout as the primary failure and
   include the diagnostic exception type and message. Do not catch or rewrite
   exceptions from `observe`, `matches`, `step`, `wait`, or `pending_work`;
   those are test or production-path defects and must propagate.

The first version deliberately has no `step=None`, default sleep, progress
token, or stall deadline. Pure observation should use a real event, process
wait, queue-native wait, or an existing harness. No current migration offers a
safe scoped progress token, so a stall API would be unused future-proofing and
could make healthy quiet work fail early.

The helper is imported from `tests.helpers.reactor_driver`; do not re-export it
from `tests/helpers/__init__.py`, publish it from `weft`, or add a dependency.

## 8. Invariants And Constraints

- Product runtime behavior and all public Weft APIs remain byte-for-byte
  unchanged. No production module imports from `tests`.
- One caller thread performs every `observe`, `step`, and `wait` callback.
  The helper must not create threads, watchers, queues, or processes.
- The deadline is absolute and never reset. A wait's paired boundary turn and a
  final ready-result settlement turn receive no new time budget.
- Every wait is clipped to positive remaining time. There is no wait after the
  deadline and no busy loop.
- Local timeout defaults remain local. Existing budgets range from short
  in-process waits to 120-second live-provider waits; migration must not replace
  them with one global duration.
- Domain observers may consume queue evidence. The shared helper must not read
  a queue, inspect a TaskSpec, infer terminal state, or decide which event is
  authoritative.
- Domain diagnostics remain local and lazy. Do not reduce TaskMonitor worker
  stacks, Manager child state, Consumer worker snapshots, or pipeline status to
  one generic dump schema.
- Queue-history readers continue using generator-based helpers and their
  existing cursor rules. This helper does not read `weft.log.tasks`.
- A migrated test must prove the same externally visible state, queue payload,
  or process result as before. Helper turn counts are supporting diagnostics,
  not the domain assertion.
- Preserve `shared`/`sqlite_only` classification for every touched module.
- Add no new dependency, global production constant, broad serialization
  marker, test-only product hook, or alternate reactor path.

Stop and revise the plan if implementation requires any of these:

- an optional step or implicit sleep to support non-reactor waits;
- a shared event-order/absence DSL;
- a global task-log tail as progress evidence;
- a second reactor owner thread or worker-side broker access;
- changing `BaseTask` or Manager runtime behavior to make the helper easier;
- weakening or replacing domain diagnostics;
- a broad AST rule with an exception list to force unrelated waits through the
  helper.

## 9. Rollout, Rollback, And One-Way Doors

Rollout is test-only and staged:

1. land the atomic [TS-0] rule, helper, direct tests, runbook guidance, and
   `test_task_observability` firing migration;
2. migrate the remaining seven simple wrappers and run their nearest suites;
3. migrate the two pipeline wrappers while retaining first-turn, consuming
   observation, and diagnostics behavior;
4. run the inventory, backend, full-suite, lint, type, metadata, documentation,
   and traceability gates; then record the durable lesson and close the plan.

Each adoption slice must remain independently revertible to its former local
loop. The helper slice is useful after the first firing migration and does not
depend on completing every later migration in the same commit.

Rollback removes the delegations first, restoring each wrapper from its prior
version, then removes the helper/direct tests and reverts [TS-0], the runbook,
context-index timestamp, lesson, and spec backlink together. There is no data
migration, persisted format, public compatibility window, or one-way door.

Do not roll back by weakening timeouts or deleting the deadline regression. If
the shared helper exposes a caller-specific mismatch, restore that caller's
local protocol and record why it is outside the common engine.

## 10. Task Breakdown

### Task 1: Add the atomic helper contract and red-green proof

- Outcome: Spec 08 owns the narrow manual-reactor-driver rule, and a pure test
  suite proves every shared timing and settlement behavior before broad
  adoption.
- Files to add:
  - `tests/helpers/reactor_driver.py`
  - `tests/system/test_reactor_driver.py`
- Files to modify:
  - `docs/specifications/08-Testing_Strategy.md`
  - `docs/agent-context/runbooks/testing-patterns.md`
  - `docs/agent-context/context.index.yaml`
  - `tests/tasks/test_task_observability.py`
  - this plan and `docs/plans/README.md` only for traceability/status data
- Red first:
  - add direct tests importing the missing helper;
  - mark the new direct test module `pytest.mark.shared` so backend
    classification stays explicit;
  - prove initially matching evidence still receives exactly one required
    first step and no wait;
  - prove a normal step returns typed matching evidence;
  - prove every completed wait is paired with exactly one next step before the
    next observation;
  - prove every wait is clipped to the absolute deadline;
  - add a pure form of the `1edafaf` ready-at-deadline regression here and prove
    exactly one final non-waiting step;
  - retain or reshape the deterministic wrapper-level regression in
    `test_task_observability.py` so it proves that `drive_task_until` wires
    `_has_pending_worker_results` into the shared engine and performs exactly
    one final ready-result turn;
  - prove a post-step observation that advances the fake clock past the
    deadline is not followed by an ordinary step when no pending work exists;
  - prove a wait that reaches the deadline receives its one paired boundary
    step, but no later ordinary step when the following observation misses;
  - prove no pending work means no additional settlement step after the
    boundary observation;
  - prove pending final settlement that still misses fails with turn/latest
    evidence and lazy diagnostics;
  - prove a diagnostic exception is reported without replacing the timeout;
  - prove invalid timeout/slice values fail before callbacks;
  - prove observe/matches/step/wait/pending callback exceptions propagate
    unchanged.
- Green implementation:
  - implement only the contract in Section 7;
  - use test-local typed callables/Protocol and complete annotations;
  - keep the module independent of `weft` runtime classes and SimpleBroker;
  - delegate `test_task_observability.py::drive_task_until` to the helper while
    preserving its local diagnostics and call sites.
- Spec/process work:
  - apply Sections 4.2 and 4.3 exactly;
  - add the Spec 08 backlink and reciprocal helper/test docstrings;
  - record the promotion baseline identifier after this slice exists.
- Do not:
  - add stall/progress logic, a sleep default, task-specific field access, or a
    policy test;
  - mock `BaseTask` or SimpleBroker for the pure engine tests. Use small callback
    fakes because the engine callbacks themselves are the contract.
- Stop if:
  - the helper needs to know what a Worker, TaskSpec, queue, or terminal event
    means;
  - the direct tests cannot express the deadline race without real sleeping.
- Done when:
  - the helper tests and `test_task_observability.py` pass;
  - the spec, runbook, context index, and backlinks are synchronized;
  - the direct regression fails against a version without final settlement and
    passes with the helper.

### Task 2: Migrate the simple manual-reactor wrappers

- Outcome: the remaining seven wrappers in the first Section 5.2 table join the
  Task 1 wrapper on the tested engine while retaining their domain evidence and
  diagnostics.
- Files to modify:
  - `tests/tasks/test_control_channel.py`
  - `tests/tasks/test_agent_execution.py`
  - `tests/tasks/test_command_runner_parity.py`
  - `tests/tasks/test_task_execution.py`
  - `tests/specs/message_flow/test_agent_spawning_transition.py`
  - `tests/specs/message_flow/test_spawning_transition.py`
  - `tests/core/test_manager.py`
  - `tests/tasks/test_task_observability.py` only if Task 1 left any migration
    cleanup
- Approach:
  - inspect every wrapper call site before editing;
  - keep a local wrapper when it names the domain condition, sets a local
    timeout, detects an early terminal failure, or supplies diagnostics;
  - adapt predicates to `observe` plus `matches` without changing what is
    consumed or when domain assertions fire;
  - pass `_has_pending_worker_results` where one final reactor turn can apply a
    result that was ready at the deadline;
  - pass each task's real `wait_for_activity` method. Do not substitute
    `time.sleep()`;
  - preserve the 120-second live-provider budget and every other caller-local
    default.
- Tests:
  - use the existing broker-backed tests in each file as the firing proof;
  - add a local regression only if a wrapper has a domain behavior not already
    exercised by its callers, such as immediate failure on a terminal
    non-completed status.
- Do not:
  - change production objects, queue payloads, result assertions, or fixture
    scope;
  - migrate any other Manager loop while this file is open.
- Stop if:
  - a wrapper needs multiple actors, consumes a shared history cursor, or must
    perform more than one final settlement turn;
  - migration makes a local failure less diagnosable.
- Done when:
  - every named wrapper delegates the engine or has a written disposition in
    the plan's Deviation Log explaining why the authoring classification was
    wrong;
  - all named test modules pass with their existing backend markers.

### Task 3: Migrate the compatible pipeline drivers

- Outcome: the pipeline idle and snapshot helpers reuse the timing engine
  without flattening worker-quiescence or status-snapshot semantics.
- Files to modify:
  - `tests/tasks/test_pipeline_runtime.py`
- Pipeline requirements:
  - `_drive_consumer_once_until_idle` delegates directly, retains the mandatory
    first owner turn, observes its complete three-part idle condition, and does
    not pre-step before entering the engine. Pass
    `consumer._has_pending_worker_results` as `pending_work` so a ready result
    receives the shared final settlement turn;
  - `_drive_pipeline_until_snapshot` drains the status queue inside `observe`,
    retains the latest snapshot for diagnostics, applies the caller predicate
    through `matches`, and returns the matching snapshot;
  - retain active-queue, pending-precheck, task-status, and latest-snapshot
    diagnostics.
- Tests:
  - run the focused pipeline suite;
  - add one direct local regression proving the idle wrapper performs one, not
    zero or two, reactor turns before its first observation;
  - retain or add one regression showing a pipeline snapshot is returned, not
    hidden in closure state;
- Stop if:
  - generic helper parameters start naming TaskMonitor or pipeline concepts.
- Done when:
  - the pipeline suite retains its current failure evidence;
  - no TaskMonitor, additional Manager, or harness protocol was pulled into the
    helper.

### Task 4: Audit adoption, reconcile guidance, and close traceability

- Outcome: the repository records the narrow reusable boundary, not a false
  claim that all waits are equivalent.
- Files to modify:
  - `docs/lessons.md`
  - `docs/specifications/08-Testing_Strategy.md` only if implementation mapping
    or nearby wording needs reconciliation within the reviewed delta
  - this plan and `docs/plans/README.md`
- Audit:
  - rerun the manual-reactor loop inventory and classify remaining loops as
    `shared engine`, `domain wrapper over shared engine`, or an explicit Section
    5.3 exclusion;
  - do not use a raw helper-name count as a completion metric;
  - add no policy gate in this plan. A future structural gate is justified only
    after the migrated baseline has a stable, small exemption taxonomy.
- Durable lesson:
  - append a dated lesson explaining that the April 27 rule still protects
    domain-specific evidence, actor ordering, and failure shape;
  - record that the shared boundary is the tested monotonic
    step/observe/wait/final-settlement engine proved by the August 11 races;
  - state that liveness, producer closure, and safety are three separate test
    obligations.
- Traceability:
  - verify Spec 08's related-plan link, helper/test reciprocal references,
    runbook text, context-index timestamp, and final migration list;
  - update the Spec Baseline promotion identifier and close every Deviation Log
    row;
  - change plan/index status to `completed` only after all required tests,
    reviews, and gates pass and the implementation is actually committed.
- Stop if:
  - remaining loops reveal a second repeated engine with incompatible ordering;
  - the planned helper has acquired unused options or a growing exception API.
- Done when:
  - inventory and documentation describe the actual boundary;
  - no pending deviation remains;
  - the final independent review passes.

## 11. Testing Plan

### 11.1 Direct helper proof

`tests/system/test_reactor_driver.py` is pure and deterministic. Patch the
helper module's monotonic clock or use a deterministic local clock fake; do not
sleep in these tests. The callback order, remaining-time clipping, turn counts,
and final settlement are the observable contract of this test-only engine, so
direct callback assertions are appropriate here.

Every branch enumerated in Task 1 must fire. In particular, test both deadline
outcomes:

- result ready at the deadline, final step makes evidence match;
- result ready at the deadline, final step still does not match, diagnostic
  timeout follows without another wait or step.

### 11.2 Migrated real-path proof

Use the existing `broker_env`, real Queue objects, real Consumer/Manager task
wiring, and current fixtures. Do not replace broker-backed test bodies with
mock-only proofs merely because the timing engine itself is pure.

| Behavior | Existing proof surface |
|----------|------------------------|
| Consumer lifecycle and controls | `test_task_observability.py`, `test_control_channel.py`, `test_task_execution.py` |
| Agent/provider result and failure visibility | `test_agent_execution.py`, `test_command_runner_parity.py` |
| Spawning transition evidence | both `tests/specs/message_flow/*spawning_transition.py` modules |
| Manager manual turns | only existing callers of `drive_manager_until` |
| Pipeline observable snapshot | `test_pipeline_runtime.py` |

The domain assertions remain the proof. A test passing because the helper
returned is insufficient unless its existing state/queue/result assertion also
passes.

### 11.3 Boundaries not mocked or inferred

- Do not mock SimpleBroker, Queue history, TaskSpec transitions, or real worker
  lifecycle in migrated domain tests.
- Do not infer process closure from terminal task-log evidence.
- Do not add fixed sleeps to settle a migrated wrapper.
- Do not test absence by waiting an arbitrary duration. This plan adds no
  absence helper; existing exact-count assertions retain their real process or
  reactor-closure boundary.

## 12. Verification And Gates

Load the repository environment first and use in-repo tools:

```bash
. ./.envrc
```

### Per-task commands

Task 1:

```bash
./.venv/bin/python -m pytest tests/system/test_reactor_driver.py \
  tests/tasks/test_task_observability.py -q
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
```

Task 2:

```bash
./.venv/bin/python -m pytest \
  tests/tasks/test_control_channel.py \
  tests/tasks/test_agent_execution.py \
  tests/tasks/test_command_runner_parity.py \
  tests/tasks/test_task_execution.py \
  tests/specs/message_flow/test_agent_spawning_transition.py \
  tests/specs/message_flow/test_spawning_transition.py \
  tests/core/test_manager.py -q
```

Task 3:

```bash
./.venv/bin/python -m pytest \
  tests/tasks/test_pipeline_runtime.py -q
```

### Final repository gates

```bash
./.venv/bin/python -m pytest
./.venv/bin/python -m pytest -m ""
bin/pytest-pg --all
./.venv/bin/mypy weft bin integrations/weft_django/weft_django \
  extensions/weft_docker/weft_docker \
  extensions/weft_macos_sandbox/weft_macos_sandbox \
  extensions/weft_microsandbox/weft_microsandbox \
  --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/python bin/ruff_suppression_index.py --check
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py \
  tests/specs/test_test_audit_policy.py tests/specs/test_ruff_policy.py \
  tests/specs/test_ruff_suppression_index.py -q
bin/check-doc-paths
```

The SQLite/default and PostgreSQL runs must both pass because the migrated
wrappers exercise broker-backed tests under normal xdist load. A targeted pass
alone is not enough to close the plan.

### Traceability gate

Capture Backstitch reports before Task 1 and after Task 4 with the same roots:

```bash
../backstitch/.venv/bin/backstitch check --repo-root . --no-config \
  --spec-root docs/specifications --plan-root docs/plans \
  --code-root weft --code-root tests --code-root bin \
  --code-root integrations --code-root extensions --format json \
  --output /tmp/weft-reactor-driver-backstitch-before.json || test $? -eq 1

../backstitch/.venv/bin/backstitch check --repo-root . --no-config \
  --spec-root docs/specifications --plan-root docs/plans \
  --code-root weft --code-root tests --code-root bin \
  --code-root integrations --code-root extensions --format json \
  --output /tmp/weft-reactor-driver-backstitch-after.json || test $? -eq 1
```

Backstitch currently has unrelated repository debt, so aggregate exit 1 is
expected. Compare error/warning tuples for every touched file with this gate:

```bash
./.venv/bin/python - \
  /tmp/weft-reactor-driver-backstitch-before.json \
  /tmp/weft-reactor-driver-backstitch-after.json <<'PY'
from collections import Counter
import json
import sys

touched = {
    "docs/agent-context/context.index.yaml",
    "docs/agent-context/runbooks/testing-patterns.md",
    "docs/lessons.md",
    "docs/plans/2026-08-11-shared-reactor-test-driver-adoption-plan.md",
    "docs/plans/README.md",
    "docs/ruff-suppression-registry.md",
    "docs/specifications/08-Testing_Strategy.md",
    "tests/core/test_manager.py",
    "tests/helpers/reactor_driver.py",
    "tests/specs/message_flow/test_agent_spawning_transition.py",
    "tests/specs/message_flow/test_spawning_transition.py",
    "tests/specs/test_ruff_policy.py",
    "tests/system/test_reactor_driver.py",
    "tests/tasks/test_agent_execution.py",
    "tests/tasks/test_command_runner_parity.py",
    "tests/tasks/test_control_channel.py",
    "tests/tasks/test_pipeline_runtime.py",
    "tests/tasks/test_task_execution.py",
    "tests/tasks/test_task_observability.py",
}


def keyed_issues(path: str) -> Counter[tuple[object, ...]]:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    return Counter(
        (
            issue.get("severity"),
            issue.get("code"),
            issue.get("path"),
            issue.get("section_id"),
            issue.get("symbol"),
            issue.get("message"),
        )
        for issue in payload["issues"]
        if issue.get("severity") in {"error", "warning"}
        and issue.get("path") in touched
    )


added = keyed_issues(sys.argv[2]) - keyed_issues(sys.argv[1])
for issue, count in sorted(added.items(), key=repr):
    print(count, issue)
raise SystemExit(bool(added))
PY
```

Reject any added issue. Update the touched-file set if implementation changes
it. If the sibling Backstitch checkout is unavailable, record that as an
unpassed closeout blocker rather than substituting metadata tests.

### Implementation verification evidence, 2026-08-11

- All ten migrated wrappers passed together with the direct driver suite and
  their broker-backed modules.
- The default suite passed: 4,005 passed and 2 expected skips.
- The first all-marker run had one Docker runtime-description timeout outside
  `drive_until`; its exact test passed immediately. A clean full rerun passed:
  4,006 passed and 13 expected skips.
- `bin/pytest-pg --all` passed: 3,946 passed and 11 expected skips.
- Mypy passed for all configured production roots: 186 source files.
- Full Ruff, the suppression-index check, plan/test-audit/Ruff/spec-hygiene
  suites, and `bin/check-doc-paths` passed.
- Slice 1 and slices 2-3 independent implementation reviews returned PASS.
- The dated Section 5.4 remaining-loop audit classified every local loop and
  found no second compatible repeated engine.
- The final complete-diff review initially blocked on over-broad Spec 07
  citations and the missing written audit result. After both fixes, scoped
  round-two review returned PASS.
- The exact before/after Backstitch keyed comparison passed with no added error
  or warning on the touched surface.

## 13. Independent Review Loop

Plan review is required before implementation because this creates a reusable
workflow and revises [TS-0]. Prefer a reviewer that did not author the plan and
did not perform the initial wait-loop inventory.

Reviewer reading set:

- this complete plan, including Section 4's exact spec/process delta;
- Spec 08 [TS-0]-[TS-2] and Spec 07 [IMPL.9]-[IMPL.10];
- the April 27 Test Sleep Hygiene lesson;
- `test_task_observability.py::drive_task_until` and its direct deadline test;
- the required-migration tables and explicit exclusions in Section 5;
- `WeftTestHarness.wait_for_completion` as a non-migration control.

Review prompt:

> Review this plan and proposed [TS-0] delta without implementing it. Look for
> errors, bad ideas, latent ambiguity, false generalization, and performative
> process. Check whether the helper contract can preserve one-thread reactor
> ownership, the final ready-result deadline race, domain diagnostics, timeout
> budgets, and PostgreSQL cursor exclusions. Verify that every required
> migration is actually compatible and every non-migration has a sound reason.
> Answer PASS or BLOCKED. BLOCK only if a zero-context engineer could not
> implement the plan confidently and correctly, or if the implementation would
> impair test correctness or diagnostic quality.

After each meaningful implementation slice, run a scoped change review against
that slice's diff and accepted plan tasks. Before completion, run one final
review over the complete diff, spec/runbook mapping, remaining-loop audit,
Deviation Log, and verification evidence. Record every finding and disposition
in this plan. A BLOCKED result keeps the plan at `draft`.

## 14. Out Of Scope

- product runtime changes to BaseTask, Manager, Consumer, workers, queues, or
  waiters;
- a public testing package or dependency;
- stall-based deadlines or ledger-progress callbacks;
- a pure-observation `step=None` mode;
- a generic `assert_order`, event DSL, or cross-queue evidence merger;
- changing terminal event classification or status reconstruction;
- broad removal of sleeps, fixed-turn helpers, or all local wait wrappers;
- a mandatory policy/AST gate requiring every cross-process test to use the
  helper;
- rewriting `WeftTestHarness.wait_for_completion` or its PostgreSQL reorder
  window;
- tuning existing timeout budgets merely because migration exposes them;
- unrelated test-file refactors, renames, fixture moves, or formatting cleanup.

## 15. Fresh-Eyes Review Checklist

Before reporting this plan implementation-ready, perform a separate author
pass after the draft and external review:

- verify every migration symbol still exists at the baseline and its semantics
  match the table;
- verify every callback and failure behavior in Section 7 has a direct test;
- check that `producer closure` is defined and not confused with a first
  terminal marker;
- check that no task tells the implementer to migrate one of Section 5.3's
  exclusions;
- check that the atomic spec strategy, promotion identifier, backlink, and
  reciprocal module references are executable;
- check that rollback can restore each local wrapper without product changes;
- check all file paths and commands from a clean repository root.

Record findings below. A zero-finding pass must still state the residual risk.

## 16. Review Findings And Dispositions

Fresh-eyes self-review, 2026-08-11:

| ID | Finding | Disposition |
|----|---------|-------------|
| SELF-1 | The new direct helper test did not explicitly declare its backend classification. | Accepted. Task 1 now requires `pytest.mark.shared`. |
| SELF-2 | Completion criteria referred to Section 5.3 for a required migration, but Section 5.3 is the non-migration list. | Accepted. The criterion now names the pipeline snapshot migration in Section 5.2 and the exclusions in Section 5.3. |
| SELF-3 | The Backstitch gate referred to a keyed-comparison pattern in another plan, leaving a zero-context implementer to reconstruct the touched set. | Accepted. Section 12 now carries the exact comparison command and initial touched set. |
| SELF-4 | The first draft's observe-first engine did not preserve the `process_once -> predicate -> wait` order used by every required local wrapper or the proposal's strongest existing implementation. | Accepted. Section 7 now requires the first turn and the turn paired with each completed wait; the direct tests fire on skipped and doubled turns. |
| EXT-1 | The observe-first draft could begin an ordinary step after an observation consumed the deadline; its post-wait observation also departed from the existing helper. | Accepted with a contract correction. Section 7 now preserves the existing `step -> observe -> wait` frame: a nonmatching observation at the deadline forbids another ordinary turn, while a completed wait retains its one paired boundary turn. Direct tests distinguish the boundary turn from the optional pending-work settlement turn. |
| EXT-2 | ServiceTask's owner-claim turn and pipeline idle's mandatory first turn were incompatible with the observe-first engine without another mode. | Accepted. The engine now has one required first turn. Pipeline idle becomes a firing migration with a zero-or-double-turn regression; ServiceTask stays local because its unconditional deadline turn is still incompatible with pending-gated settlement. |
| EXT-3 | TaskMonitor's drain-only post-deadline settlement and repeated diagnostic assertion frame are not equivalent to a pending-gated `process_once()`. | Accepted. Both TaskMonitor idle helpers remain local in v1. |
| EXT-4 | The proposed [TS-0] rule was broader than its own nested/composed reactor exclusions. | Accepted. [TS-0] now applies only to a single-reactor exact control frame and names the exclusions. |
| EXT-5 | The runbook treated a final ready-result turn as producer closure even though a worker could remain live. | Accepted. The text now requires separate no-live-producer/no-deliverable-result proof and says the final turn alone is never closure. |
| EXT-6 | Moving the deadline regression entirely to pure helper tests could miss a wrapper that forgot to pass pending-worker evidence. | Accepted. Task 1 retains a deterministic `drive_task_until` wiring regression in addition to the pure engine test. |
| EXT-7 | Task 3 relied on the global contract to imply pipeline-idle pending-result settlement instead of naming the adapter. | Accepted. Task 3 now explicitly passes `consumer._has_pending_worker_results` as `pending_work`. |
| IMPL-1 | Eight baseline wrappers could perform zero turns if their positive timeout elapsed between deadline construction and the loop guard; pipeline idle could skip its post-turn observation if the first turn consumed the budget. | Accepted as an explicit standardization, not claimed as byte-identical edge behavior. [TS-0] owns one required first turn and observation. Direct engine tests and the pipeline wrapper regression prove no skipped or doubled first turn. |
| IMPL-2 | Folding an arbitrary caller diagnostic failure into the timeout requires one intentional `BLE001` catch. | Accepted as `RUFF-SUP-366`. The direct suite proves ordinary diagnostic failure remains secondary and fatal diagnostic failure propagates by identity; the Slice 1 independent review returned PASS. |
| IMPL-3 | The first all-marker gate timed out while Docker runtime inspection returned `state=unknown`; the failure path did not use `drive_until`. | Classified as shared-host/runtime contention after the exact Docker test passed immediately and a full all-marker rerun passed 4,006 tests. No timeout or product behavior was changed. |
| FINAL-1 | The helper's initial Spec 07 [IMPL.9]/[IMPL.10] citations created two new reciprocal-mapping warnings because those sections correctly map production owners. | Accepted. The helper now cites only its governing Spec 08 [TS-0]; the keyed Backstitch comparison is clean without widening Spec 07. |
| FINAL-2 | The implementation had run but not recorded the required remaining-loop audit. | Accepted. Section 5.4 records the dated AST audit, classifies every remaining loop under the explicit exclusions, and confirms that no second compatible engine or policy gate is justified. |

External review verdict was BLOCKED on EXT-1 through EXT-6. All six findings
were incorporated above. Round-two verification on 2026-08-11 returned PASS;
the reviewer found no new implementation-blocking contradiction. Its one
optional tightening is incorporated as EXT-7. Residual risk is limited to
whether one of the remaining ten wrappers has an undocumented ordering
dependency; each migration retains its local wrapper and has a stop gate that
restores the local engine rather than widening the shared API.

Slice 1 implementation review on 2026-08-11 returned PASS for the driver,
direct tests, `drive_task_until` firing migration, [TS-0] mapping, runbook, and
`RUFF-SUP-366`. No material finding remained.

Slices 2 and 3 implementation review on 2026-08-11 returned PASS for all ten
required wrappers. The reviewer verified consuming-observer placement, pending
settlement, local diagnostics and timeout budgets, pipeline newest-snapshot
semantics, the first-turn regression, and every explicit non-migration. No
material finding remained.

Final complete-diff review initially returned BLOCKED on FINAL-1 and FINAL-2.
Both findings were corrected without expanding scope. Scoped round-two review
on 2026-08-11 returned PASS, and the regenerated Backstitch keyed comparison
added no touched-file error or warning.

## 17. Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## 18. Completion Criteria

This plan may move to `completed` only when:

- the atomic [TS-0] delta, helper, direct tests, runbook, context index, and
  first firing migration have landed together;
- every required migration in Section 5.2 has landed or has a reviewed,
  non-pending Deviation Log disposition;
- both pipeline migrations in Section 5.2 preserve their local domain
  semantics, and the Section 5.3 exclusions remain outside the helper;
- all direct branches in Section 7 have firing tests;
- the remaining-loop audit, durable lesson, spec backlink, implementation
  mapping, and promotion baseline are current;
- default, all-marker, PostgreSQL, lint, type, metadata, documentation, and
  touched-surface traceability gates have passed;
- fresh-eyes, slice reviews, and final independent review have no unresolved
  blockers;
- the implementation is committed, verified by `git log`, and plan/index status
  are changed together. Do not commit on the user's behalf solely to satisfy
  this criterion.

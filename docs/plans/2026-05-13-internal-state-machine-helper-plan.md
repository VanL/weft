# Internal State Machine Helper Plan

Status: draft
Source specs: docs/specifications/01-Core_Components.md [CC-1], [CC-2.4], [CC-2.5]; docs/specifications/03-Manager_Architecture.md [MA-1.6a]; docs/specifications/05-Message_Flow_and_State.md [MF-2], [MF-3], [MF-5]; docs/specifications/07-System_Invariants.md [STATE.1]-[STATE.6], [MANAGER.15]; docs/specifications/08-Testing_Strategy.md [TS-0], [TS-1]
Superseded by: none

## 1. Goal

Introduce a small internal state-machine helper so Weft can express reducer-style
logic as `current state + evidence input -> decision` in one uniform, table-tested
way. Phase 1 builds the reusable helper and proves its coverage/reachability
features without changing runtime behavior. Phase 2 migrates existing lifecycle,
control, and manager-service reducers onto the helper in small compatibility
slices, with side effects still owned by the existing Manager, command, BaseTask,
and Consumer code.

## 2. Source Documents

Source specs:

- [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md)
  [CC-1], [CC-2.4], [CC-2.5]: TaskSpec mutable state, task control, and shared
  execution flow ownership.
- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md)
  [MA-1.6a]: manager-owned singleton services already reduce evidence through
  one pure transition table before side effects.
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-2], [MF-3], [MF-5]: reservation, control evidence, and task-log state
  reconstruction.
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [STATE.1]-[STATE.6], [MANAGER.15]: forward-only task lifecycle and
  deterministic manager-service convergence.
- [`docs/specifications/08-Testing_Strategy.md`](../specifications/08-Testing_Strategy.md)
  [TS-0], [TS-1]: deterministic repo-managed tests, harness selection, and
  coverage organization.

Relevant existing plans:

- [`2026-05-08-deterministic-manager-service-reconciler-plan.md`](./2026-05-08-deterministic-manager-service-reconciler-plan.md)
  is completed and established the pure reducer pattern now used in
  `weft/core/manager_services.py`.
- [`2026-05-10-control-and-service-convergence-state-machine-plan.md`](./2026-05-10-control-and-service-convergence-state-machine-plan.md)
  is completed and describes control and singleton convergence as explicit
  state machines. Preserve its truthfulness constraints.
- [`2026-05-06-task-evidence-reconciliation-model-plan.md`](./2026-05-06-task-evidence-reconciliation-model-plan.md)
  is a draft background plan for evidence classification. Use it only as
  context; do not widen this slice into a full status reconstruction rewrite.

Required guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)
- [`docs/agent-context/runbooks/testing-patterns.md`](../agent-context/runbooks/testing-patterns.md)

## 3. Context And Key Files

Files to create in Phase 1:

- `weft/core/state_machines.py`
- `tests/core/test_state_machines.py`

Files to modify in Phase 1:

- `docs/specifications/01-Core_Components.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/08-Testing_Strategy.md`
- `docs/plans/README.md`

Files to modify in Phase 2, in separate slices:

- `weft/core/taskspec/model.py`
- `weft/core/manager_services.py`
- `weft/commands/tasks.py`
- `weft/core/tasks/base.py` only if a control-policy reducer needs a small
  domain declaration nearby. Do not move side effects into the helper.
- `weft/core/tasks/consumer.py` only if active-control finalization needs to
  call a reducer; do not rewrite runner execution.
- corresponding tests under `tests/taskspec/`, `tests/specs/taskspec/`,
  `tests/core/test_manager_services.py`, `tests/commands/test_task_commands.py`,
  and existing CLI/process tests that prove behavior stayed stable.

Read first:

- `weft/core/manager_services.py`: current best example of pure evidence to
  decision reduction.
- `tests/core/test_manager_services.py`: table-style tests for manager-owned
  service decisions.
- `weft/core/taskspec/model.py`: `StateSection`, `TaskSpec.set_status()`, and
  `mark_*` helpers.
- `weft/commands/tasks.py`: `_await_control_surface()`, `stop_tasks()`, and
  `kill_tasks()`.
- `weft/core/tasks/base.py`: `TaskControlPolicy`, `_handle_control_command()`,
  `_handle_stop_request()`, and `_handle_kill_request()`.
- `weft/core/tasks/consumer.py`: `_defer_active_control()` and
  `_finalize_deferred_active_control()`.

Current structure:

- Task lifecycle validation is explicit but inline in
  `TaskSpec.set_status()`. It has a transition table, but the table is not a
  reusable object and cannot currently prove structural reachability or tested
  transition coverage.
- Manager-owned services already use a pure reducer in
  `reduce_managed_service_state()`. That reducer is table-tested but hand-coded;
  it is structural prior art for the helper, not proof that the helper can
  directly own every field in `ManagedServiceState`.
- CLI control convergence already separates evidence from side effects more
  than it used to, but the command-layer decision space is still encoded as
  imperative branching.
- Side effects are intentionally outside the reducer boundary: queue writes,
  process probes, process kills, task-log writes, control replies, and sleeps
  remain in their existing owners.

Comprehension checks before implementation:

1. Which current Weft code is allowed to mutate `TaskSpec.state`, and which
   parts of `TaskSpec` must remain immutable?
2. What evidence proves task-local terminal state in control flows, and why is
   a KILL acknowledgement not terminal proof?
3. Which function currently owns pure manager-service transition selection, and
   which Manager method applies the resulting side effects?
4. Which tests should remain pure table tests, and which must still use
   `WeftTestHarness`, `broker_env`, or real task processes?
5. What is the difference between proving a transition is structurally
   reachable and proving a concrete test exercised it?

## 4. Invariants And Constraints

- Reducers are pure: no queue reads, no process probes, no writes, no sleeps,
  and no logging as correctness.
- Reducer inputs are evidence snapshots or simple values, not live handles.
- Reducer outputs name allowed side effects but do not perform them.
- Action enums must be explicit enough for table-driven tests to cover every
  meaningful branch.
- Side-effect owners stay where they are today: Manager, command helpers,
  BaseTask, Consumer, and runner/plugin boundaries.
- Tests assert table behavior directly first; integration tests then prove the
  existing side-effect owners apply decisions correctly.
- The helper must be internal. Do not add `transitions`,
  `python-statemachine`, or another runtime dependency.
- Do not introduce a second execution path, second control plane, lease table,
  workflow engine, or hidden state store.
- Do not change public CLI output, queue names, TaskSpec payload shape, result
  payloads, or manager registry shape in Phase 1.
- Phase 2 migrations must be behavior-preserving unless a spec-backed bug fix
  is named in that slice.
- Existing forward-only task transitions stay intact:
  `created -> spawning|failed|cancelled`,
  `spawning -> running|completed|failed|timeout|cancelled|killed`, and
  `running -> completed|failed|timeout|cancelled|killed`.
- Terminal task states must not transition back to non-terminal states.
- Manager-service terminal proof still wins over live evidence for the same
  TID.
- Queue history reads touched by Phase 2 must stay generator-based when they
  scan append-only queues.
- Red-green TDD is preferred for each migration slice. If a slice is purely
  mechanical, the smallest acceptable proof is a table-test diff plus the
  nearest existing integration suite.

Failure priorities:

- Invalid transition tables, unreachable non-terminal states, ambiguous default
  transitions, or missing coverage proofs are fatal helper errors.
- Diagram generation, optional debug descriptions, or richer diagnostics are
  out of scope for Phase 1.
- A reducer returning an unknown action is fatal in the side-effect owner. It
  must not silently default to "wait" or "do nothing."

Rollback:

- Phase 1 rollback is deleting the unused helper module and tests. No runtime
  behavior should depend on it yet. Also revert the Phase 1 doc backlinks and
  helper-contract notes from `docs/specifications/01-Core_Components.md`,
  `docs/specifications/07-System_Invariants.md`,
  `docs/specifications/08-Testing_Strategy.md`, and `docs/plans/README.md`.
- Each Phase 2 migration must be revertible independently because it should
  preserve public contracts and use the existing side-effect owner.
- Do not land a broad one-shot migration that makes rollback require reverting
  task lifecycle, control convergence, and manager service supervision together.

## 5. Phase 1 Design Contract

The first implementation target is a small generic helper in
`weft/core/state_machines.py`.

Expected public-internal API shape:

```python
StateT = TypeVar("StateT", bound=str)
InputT = TypeVar("InputT")
ActionT = TypeVar("ActionT", bound=str)

@dataclass(frozen=True, slots=True)
class Transition(Generic[StateT, InputT, ActionT]):
    id: str
    source: StateT | frozenset[StateT]
    target: StateT
    action: ActionT
    predicate: Callable[[StateT, InputT], bool]
    reason: str

@dataclass(frozen=True, slots=True)
class StateDecision(Generic[StateT, ActionT]):
    source: StateT
    target: StateT
    action: ActionT
    transition_id: str
    reason: str

class StateMachine(Generic[StateT, InputT, ActionT]):
    def __init__(
        self,
        *,
        states: Iterable[StateT],
        actions: Iterable[ActionT],
        transitions: Iterable[Transition[StateT, InputT, ActionT]],
        terminal_states: Iterable[StateT] = (),
        sink_states: Iterable[StateT] = (),
        allow_terminal_outgoing: bool = False,
    ) -> None: ...

    def decide(self, current: StateT, input: InputT) -> StateDecision[StateT, ActionT]:
        ...
```

This exact spelling may change during implementation if type checking requires
it, but the semantics should not:

- `StateMachine.decide(current, input) -> StateDecision` evaluates matching
  transitions in declaration order and returns exactly one decision.
- If a transition declares `source` as a `frozenset`, `StateDecision.source`
  is the actual `current` state that matched, not the whole source set.
- Transition IDs are required, stable, and unique.
- States and actions are strings or string-backed literals/enums so assertions
  and JSON-friendly debug output stay simple.
- The machine validates at construction:
  - non-empty state set
  - non-empty action set
  - non-empty transition list
  - every transition source and static target is declared
  - every transition action is declared
  - transition IDs are unique
  - terminal states have no outgoing transitions unless explicitly allowed
  - every non-terminal, non-sink state appears as a transition source
- The machine exposes structural proof helpers:
  - `reachable_states(initial_states: Iterable[StateT]) -> frozenset[StateT]`
  - `unreachable_states(initial_states: Iterable[StateT]) -> frozenset[StateT]`
  - `assert_all_states_reachable(initial_states: Iterable[StateT]) -> None`
  - `assert_transition_ids_covered(covered_ids: Iterable[str]) -> None`
  - `assert_states_covered(covered_states: Iterable[StateT]) -> None`
  - `assert_actions_covered(covered_actions: Iterable[ActionT]) -> None`
- Structural reachability is graph reachability over declared static targets.
  Phase 1 must not support dynamic targets. Add them later only if a concrete
  migrated Weft reducer proves static targets are inadequate.
- Test coverage proof is separate from reachability. A state can be reachable
  but still untested; table tests must collect decision transition IDs, source
  and target states, and actions, then assert coverage.
- Tests collect coverage explicitly. Do not let `StateMachine` retain decision
  history internally.

Example coverage collection:

```python
seen_transitions: set[str] = set()
seen_states: set[str] = set()
seen_actions: set[str] = set()
for case in cases:
    decision = machine.decide(case.current, case.input)
    seen_transitions.add(decision.transition_id)
    seen_states.update({decision.source, decision.target})
    seen_actions.add(decision.action)
machine.assert_transition_ids_covered(seen_transitions)
machine.assert_states_covered(seen_states)
machine.assert_actions_covered(seen_actions)
```
- The helper may provide `explain()` or `as_dict()` for failure messages only
  if that stays small and deterministic.

Do not add:

- callback execution hooks
- enter/exit callbacks
- async handling
- background timers
- graphviz or mermaid output
- persistence
- queue/process helpers
- mutation of a domain model

## 6. Tasks

1. Build the generic helper with no runtime callers.
   - Outcome: `StateMachine`, `Transition`, and `StateDecision` exist as a
     pure internal reducer utility.
   - Files to touch:
     - `weft/core/state_machines.py`
     - `tests/core/test_state_machines.py`
   - Read first:
     - `weft/core/manager_services.py`
     - `tests/core/test_manager_services.py`
   - Implementation:
     - keep the module dependency-light: stdlib only
     - use dataclasses with `frozen=True` and `slots=True` where practical
     - use `collections.abc` types and modern Python type syntax
     - make construction validation strict and fail with actionable
       `ValueError` messages
     - make `decide()` deterministic by ordered transition evaluation
     - include the selected `transition_id` in every decision
   - Tests:
     - valid machine returns expected target/action/reason
     - duplicate transition IDs fail
     - unknown source/target fails
     - no matching transition fails explicitly
     - terminal state with outgoing transition fails unless allowed
     - structural reachability detects unreachable states
     - transition coverage assertion reports missing transition IDs
     - state coverage assertion reports missing states
     - action coverage assertion reports missing actions
   - Stop if:
     - the helper starts needing Weft queues, TaskSpec, Manager, or process
       concepts
     - the generic API needs callbacks or side effects to express Phase 1 tests
   - Done when:
     - `./.venv/bin/python -m pytest tests/core/test_state_machines.py -q`
       passes
     - `./.venv/bin/mypy weft` accepts the new module

2. Add a non-runtime example machine in tests that mirrors task lifecycle.
   - Outcome: the helper proves it can express Weft's forward-only lifecycle
     table without changing `TaskSpec` yet.
   - Files to touch:
     - `tests/core/test_state_machines.py`
   - Read first:
     - `docs/specifications/07-System_Invariants.md [STATE.1]-[STATE.6]`
     - `tests/specs/taskspec/test_state_transitions.py`
   - Implementation:
     - define a test-local lifecycle machine with the exact current allowed
       transitions
     - table-test every allowed transition
     - table-test representative forbidden transitions
     - assert all lifecycle states are structurally reachable from `created`
     - assert all declared transitions are covered by the table
     - assert all declared lifecycle actions are covered by the table
   - Stop if:
     - reproducing the lifecycle table requires changing production constants
       before a runtime caller exists
   - Done when:
     - the lifecycle example test fails if any allowed transition is removed
       or any lifecycle state becomes unreachable

3. Document the helper contract in specs.
   - Outcome: canonical docs describe the helper as an internal deterministic
     reducer pattern, not a new runtime truth source.
   - Files to touch:
     - `docs/specifications/01-Core_Components.md`
     - `docs/specifications/07-System_Invariants.md`
     - `docs/specifications/08-Testing_Strategy.md`
   - Required content:
     - `01-Core_Components.md`: note the helper as internal support for pure
       transition selection; side effects stay in existing owners
     - `07-System_Invariants.md`: add a helper implementation mapping for
       deterministic transition validation once code exists
     - `08-Testing_Strategy.md`: add table-test coverage expectations for
       state-machine reducers, including reachability and transition-ID
       coverage
   - Stop if:
     - the docs start describing future Phase 2 migrations as already shipped
   - Done when:
     - docs state only the Phase 1 reality after Phase 1 lands

4. Run the Phase 1 review gate.
   - Outcome: the generic helper is independently reviewed before runtime
     code starts depending on it.
   - Files/docs the reviewer should read:
     - this plan
     - `weft/core/state_machines.py`
     - `tests/core/test_state_machines.py`
     - `weft/core/manager_services.py`
     - `docs/specifications/07-System_Invariants.md`
   - Review stance:
     - look for over-generalization, missing determinism, weak coverage proof,
       and API shapes that would make Phase 2 migrations awkward
   - Done when:
     - review findings are either fixed or explicitly rejected with rationale

5. Phase 2a: migrate task lifecycle validation behind the helper.
   - Outcome: `TaskSpec.set_status()` uses a production lifecycle machine while
     preserving existing behavior and public errors as much as practical.
   - Files to touch:
     - `weft/core/taskspec/model.py`
     - `tests/taskspec/test_taskspec.py`
     - `tests/specs/taskspec/test_state_transitions.py`
   - Implementation:
     - define the lifecycle machine near the TaskSpec code or in a small
       domain module such as `weft/core/task_lifecycle.py`
     - keep timestamp and return-code mutation in `TaskSpec`; the helper only
       validates/selects the transition
     - preserve current same-status behavior explicitly, either as an early
       return/validation bypass before invoking the helper or as explicit
       self-edges with covered transition IDs
     - preserve `mark_running()` from `created` as the same two-step
       composition it uses today: `created -> spawning`, then
       `spawning -> running`
     - `set_status("running")` from `created` must still fail; only
       `mark_running()` owns the two-call promotion behavior
     - avoid importing command, manager, queue, or task runtime modules into
       TaskSpec
   - Tests:
     - preserve existing `mark_*` behavior
     - add exhaustive allowed-transition table tests
     - add forbidden-transition tests for terminal back-transitions and
       direct `created -> completed`
     - assert lifecycle states and transition IDs are covered
   - Stop if:
     - the migration changes TaskSpec construction or template/resolved
       behavior
   - Done when:
     - targeted TaskSpec tests pass
     - no runtime behavior outside state validation changed

6. Phase 2b: add manager-service coverage proof without forcing migration.
   - Outcome: `reduce_managed_service_state()` keeps its current rich
     `ManagedServiceState` reducer unless implementation proves a direct
     helper-backed rewrite is simpler. The required work is stronger table and
     branch coverage using the helper's coverage-proof pattern, not a forced
     generic-machine migration.
   - Files to touch:
     - `weft/core/manager_services.py`
     - `tests/core/test_manager_services.py`
   - Implementation:
     - preserve the existing public reducer function and dataclasses
     - preserve Manager as the only side-effect owner
     - keep terminal-proof-wins and pending-spawn-blocks-duplicate rules exact
     - treat `StateMachine` as a labeled-state helper; do not pretend the full
       9-field `ManagedServiceState` is just a string state label
     - prefer explicit manager-service tests over a compatibility wrapper if a
       direct generic-machine expression would make the current reducer harder
       to read
   - Tests:
     - existing reducer tests must stay green
     - add transition-ID or branch coverage checks for manager-service actions
     - keep order-invariance tests
   - Stop if:
     - forcing the generic helper makes the reducer less explicit or hides
       domain precedence rules
   - Done when:
     - manager-service behavior is unchanged and table/branch coverage is
       stronger, whether or not `reduce_managed_service_state()` delegates to
       `StateMachine`

7. Phase 2c: migrate command-layer control convergence.
   - Outcome: STOP/KILL command result classification is expressed as a pure
     evidence reducer, while control writes, waits, runner fallback, and host
     process termination remain in `weft/commands/tasks.py`.
   - Files to touch:
     - `weft/commands/tasks.py`
     - `tests/commands/test_task_commands.py`
   - Implementation:
     - define an evidence dataclass for command sent, ack seen, terminal
       status, host PID liveness, runner fallback result, and observation
       budget expiry
     - define the control-convergence state set before editing. Start with:
       `command_sent`, `accepted`, `terminal_observed`,
       `runtime_dead_after_control`, `escalating_runner`,
       `escalating_host`, and `unknown`
     - define actions such as `wait`, `accept_terminal`, `accept_dead_runtime`,
       `escalate_runner`, `escalate_host`, and `report_unknown`
     - do not let ack-only KILL count as success. Ack-only-with-no-terminal
       should reduce to `accepted` while within the observation budget and to
       `unknown`/`report_unknown` when the budget expires.
     - preserve the existing separation between `_await_control_surface()` and
       `kill_tasks()`: deadline-based loop exit can still happen in
       `_await_control_surface()`, while `kill_tasks()` remains responsible
       for counting success only from terminal `killed` proof or controlled
       runtime death proof.
     - map current local loop variables explicitly before implementation:
       `latest_entry` and `latest_snapshot` are observed evidence,
       `kill_ack_deadline` and `public_signal_deadline` are observation-budget
       inputs, and fallback runner/host kill results are later evidence
       snapshots passed back into the reducer.
   - Tests:
     - table-test each action and transition ID
     - preserve existing command tests around terminal `ctrl_out`, ack-only
       KILL, and live host PIDs
     - use limited mocks only for plugin result and PID liveness; do not mock
       queues where existing command tests can use real broker-backed queues
   - Stop if:
     - the reducer starts reading queues or killing processes directly
   - Done when:
     - targeted command tests pass and existing control truthfulness behavior
       is preserved or strengthened

8. Phase 2d: consider BaseTask/Consumer control policy declarations.
   - Outcome: decide whether `TaskControlPolicy` declarations should become
     helper-backed machines or remain descriptive dataclasses.
   - Files to inspect:
     - `weft/core/tasks/base.py`
     - `weft/core/tasks/consumer.py`
     - `tests/tasks/`
   - Rule:
     - migrate only if it reduces duplicated transition logic or creates a
       useful coverage proof. Do not migrate just for uniformity.
   - Done when:
     - either a small reducer-backed control policy lands with tests, or a
       follow-up note says no migration is justified yet
   - Follow-up note, 2026-05-13:
     - no BaseTask/Consumer migration is justified for this slice. The current
       `TaskControlPolicy` declarations already make subclass STOP/KILL
       behavior visible, while the concrete handlers own state mutation,
       reserved-queue policy, control replies, terminal envelopes, and stop
       events. A generic reducer would either duplicate those declarations or
       hide side-effect order inside a misleading state label.

## 7. Testing Plan

Phase 1 tests are pure unit tests:

- `tests/core/test_state_machines.py` for helper construction, decision
  selection, reachability, and test-coverage proof helpers.
- Do not add a spec-level test in Phase 1. Revisit that only after at least one
  Phase 2 runtime migration proves the helper API is worth treating as a
  stable internal contract.

Phase 2 tests are split:

- pure reducer table tests for each migrated domain machine
- existing integration tests for queue/process/control behavior
- `WeftTestHarness` for CLI or manager lifecycle behavior
- `broker_env` or real queues for queue semantics
- limited mocks only for external, slow, or nondeterministic boundaries such
  as runner plugins and PID liveness

Do not mock:

- SimpleBroker queue behavior in lifecycle or command integration tests
- manager/task lifecycle in tests meant to prove runtime behavior
- reserved-queue semantics
- terminal task-log or terminal `ctrl_out` evidence when a real queue is
  practical

Edge cases that must be covered:

- unreachable state detection
- missing transition-ID test coverage
- missing action coverage
- terminal states rejecting outgoing transitions
- no matching transition
- duplicate transition IDs
- state reachable but not exercised by tests
- KILL ack-only is not terminal success
- terminal proof wins over live proof for the same service TID

## 8. Verification And Gates

Per-task verification:

```bash
./.venv/bin/python -m pytest tests/core/test_state_machines.py -q
./.venv/bin/python -m pytest tests/specs/taskspec/test_state_transitions.py -q
./.venv/bin/python -m pytest tests/core/test_manager_services.py -q
./.venv/bin/python -m pytest tests/commands/test_task_commands.py -q
```

Phase 1 final gates:

```bash
./.venv/bin/python -m pytest tests/core/test_state_machines.py -q
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft
```

Phase 2 final gates after all migrations:

```bash
./.venv/bin/python -m pytest tests/taskspec tests/specs/taskspec tests/core/test_manager_services.py tests/commands/test_task_commands.py -q
./.venv/bin/python -m pytest tests/cli/test_cli_serve.py -q
./.venv/bin/python -m pytest
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft
```

Backend-sensitive follow-up:

```bash
bin/pytest-pg --all
```

Run the Postgres gate when Phase 2 touches manager-service convergence,
control convergence, or any queue/process lifecycle path. It is not required
for a Phase 1 helper-only change unless the implementation unexpectedly touches
runtime code.

## 9. Rollout, Review, And Fresh-Eyes Loop

Rollout sequence:

1. Land Phase 1 helper with no runtime callers.
2. Run independent review before Phase 2 starts.
3. Migrate one domain machine per slice.
4. After each Phase 2 slice, run targeted tests and an independent review if
   the slice changes control, manager service convergence, or TaskSpec
   lifecycle behavior.
5. Treat Phase 2d as decision-only unless the decision finds a small,
   clearly useful control-policy reducer. It may produce a follow-up plan
   instead of a code diff.
6. Run the full final gates only after the last migration slice.

Independent review:

- Preferred reviewer: Claude, because it is a different agent family than the
  authoring agent in this session.
- Expected runtime: allow 8-10 minutes for the plan review.
- Review prompt:

```text
Read docs/plans/2026-05-13-internal-state-machine-helper-plan.md.
Carefully examine the plan and the associated code paths it cites. Look for
errors, bad ideas, and latent ambiguities. Do not implement anything. Answer
whether a zero-context engineer could implement this confidently and correctly.
Prioritize findings with file/section references and concrete fixes.
```

The author must respond to each review finding by updating the plan, recording
why the current path is still better, or marking the point out of scope.

Initial review response:

- Claude review completed during plan authoring.
- Accepted findings: specify the constructor, remove dynamic targets from
  Phase 1, clarify explicit coverage collection, call out TaskSpec
  self-transition and `mark_running()` composition, reclassify manager-service
  work as coverage-first rather than forced migration, name the command-control
  state set, and include doc rollback.
- Rejected findings: none. The review's concern that Phase 2 is not yet a
  single implementation-ready slice is correct; this plan now treats Phase 2
  as separate migration slices with their own review gates.

Fresh-eyes checklist before implementation:

- A zero-context engineer can find every file to read and modify.
- The helper boundary is clear: pure reducer only, no side effects.
- Reachability and test coverage proof are both specified.
- Phase 1 cannot change runtime behavior.
- Phase 2 migrations are independently revertible.
- No task asks the implementer to "update the flow" without naming the owner,
  boundary, test, and stop gate.

## 10. Out Of Scope

- External state-machine dependencies.
- Runtime behavior changes in Phase 1.
- Public CLI shape changes.
- Queue name, TaskSpec schema, result payload, or manager registry changes.
- Graph rendering, diagrams, or documentation generation.
- New workflow engine behavior.
- Broad status reconstruction rewrite.
- Broad cleanup of unrelated branching logic.
- Moving side effects into reducer modules.
- Replacing `WeftTestHarness`, `broker_env`, or existing integration tests
  with mock-heavy substitutes.

# Hypothesis Property-Based Testing Plan
Status: completed
Source specs: docs/specifications/08-Testing_Strategy.md [TS-0, TS-1, TS-2]; docs/specifications/08A-Testing_Strategy_Planned.md [TS-A3, TS-A4]; docs/specifications/07-System_Invariants.md [IMMUT.1-IMMUT.4, STATE.1-STATE.6, REDUCER.1-REDUCER.4, QUEUE.1-QUEUE.6, RES.1-RES.6, EXEC.1-EXEC.4, OBS.1-OBS.17, IMPL.1-IMPL.9, MANAGER.1-MANAGER.17, CTX.1-CTX.4]; docs/specifications/02-TaskSpec.md [TS-1, TS-1.1, TS-1.4, TS-1.5]; docs/specifications/05-Message_Flow_and_State.md [MF-2, MF-3, MF-5]
Superseded by: none

## Goal

Add a small, deliberate Hypothesis property-based test layer for Weft
invariants that are cheap, pure, and stable enough to generate many examples
without turning the default suite into noise. Use property tests to supplement
the existing example and table tests, not to replace real broker/process
coverage. Update the testing specs and developer docs so the next engineer
knows exactly when property-based testing is appropriate.

This is a test and documentation plan. It should not change runtime behavior.
If a property test exposes a product bug, stop the current slice at the failing
test and create a separate behavior-fix task or plan for the runtime change.

## Source Documents

Read these first, in this order:

1. `AGENTS.md`: repo philosophy, house style, test commands, and the warning
   against generic refactors.
2. `docs/agent-context/README.md`: canonical agent context entry point.
3. `docs/agent-context/decision-hierarchy.md`: specs outrank plans.
4. `docs/agent-context/principles.md`: real behavior evidence beats mock-heavy
   proofs.
5. `docs/agent-context/engineering-principles.md`: boundary validation,
   DRY/YAGNI, and test design.
6. `docs/agent-context/runbooks/testing-patterns.md`: harness selection and
   backend classification.
7. `docs/agent-context/runbooks/writing-plans.md`: plan expectations.
8. `docs/agent-context/runbooks/hardening-plans.md`: this change touches the
   test toolchain and multiple specs, so use the hardening checklist.
9. `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`: review
   expectations for cross-cutting plans.
10. `docs/agent-context/lessons.md` and `docs/lessons.md`: durable repo
    lessons.
11. `docs/specifications/08-Testing_Strategy.md`: current canonical suite
    shape. Important constraint: no dedicated `tests/property/` tree.
12. `docs/specifications/08A-Testing_Strategy_Planned.md`: planned property
    sweep and test-hook follow-up.
13. `docs/specifications/07-System_Invariants.md`: invariant IDs and
    implementation mappings.
14. `docs/specifications/02-TaskSpec.md`: TaskSpec schema, immutability,
    reserved policies, and lifecycle fields.
15. `docs/specifications/05-Message_Flow_and_State.md`: queue and state-flow
    semantics.

Also read these code files before writing tests:

- `pyproject.toml`: dev dependencies, pytest markers, and tool config.
- `.gitignore`: add Hypothesis's local example database before running the
  suite.
- `tests/conftest.py`: backend classification audit, fixtures, and CLI helper.
- `tests/helpers/weft_harness.py`: real harness for live runtime tests. Do not
  use it inside generated examples unless there is a deliberate reason.
- `tests/taskspec/test_taskspec.py`: current TaskSpec examples.
- `tests/specs/taskspec/test_state_transitions.py`: current lifecycle table
  checks.
- `tests/core/test_state_machines.py`: current reducer helper coverage.
- `weft/core/taskspec/model.py`: TaskSpec implementation.
- `weft/core/task_lifecycle.py`: lifecycle transition table.
- `weft/core/state_machines.py`: pure reducer helper.
- `weft/core/task_evidence.py`: queue-name fallback helpers.
- `weft/core/monitor/policies/dead_task.py`: dead-task queue classifier.
- `weft/core/monitor/policies/runtime_control.py`: runtime control queue
  classifier.

External docs to check during implementation:

- Hypothesis quick start:
  `https://hypothesis.readthedocs.io/en/latest/quickstart.html`
- Hypothesis pytest integration:
  `https://hypothesis.readthedocs.io/en/latest/details.html#use-with-pytest`
- Hypothesis settings:
  `https://hypothesis.readthedocs.io/en/latest/settings.html`

Important external-doc fact: pytest function-scoped fixtures are set up once
for the whole Hypothesis test, not once per generated example. This is why the
first slice must avoid generated live broker/process tests. A generated example
must not leak task state into the next generated example.

## Recommendation

Use Hypothesis only where the input space is large, the contract is pure or
nearly pure, and failures shrink to a useful counterexample:

- TaskSpec validation, defaults, immutability, queue-name resolution, resource
  limit validation, and metric update invariants.
- Pure lifecycle status-operation sequences. Exhaustive lifecycle
  transition-table checks should stay deterministic.
- Queue-name parsing and monitor policy selectors that work on plain values.
- Small config parser boundaries where generated input finds edge cases better
  than hand examples.

Do not put live Manager, Consumer, SimpleBroker reservation loops, process
execution, wall-clock timeout behavior, or lifecycle event races under
Hypothesis in the default suite. Those paths are valuable, but property-based
fuzzing will be noisy because the generated input is not the hard part. The
hard part is process scheduling, queue persistence, cleanup, and time. Keep
those as real harness/example tests with deterministic scenarios.

The right mental model:

- Property tests are a broad input sweep for pure contracts.
- Table tests are the source of truth for finite state machines.
- Real harness tests prove the durable path through queues and processes.
- Mocks are for hostile external dependencies only. Do not mock SimpleBroker,
  Manager, Consumer, or TaskSpec to prove Weft invariants.

## Invariant Audit

`docs/specifications/07-System_Invariants.md` currently defines 93 invariant
IDs. This plan classifies the primary reasonable test style for each family.
The numbers below are a starting audit, not permission to skip reading the
spec. Task A0 requires the implementer to verify this map against current tests
before adding new property tests.

| Invariant family | Total | Good Hypothesis candidates | Better as deterministic/table tests | Better as real harness tests | Better as static/spec review |
| --- | ---: | ---: | ---: | ---: | ---: |
| IMMUT | 4 | 4 | 0 | 0 | 0 |
| STATE | 6 | 4 | 2 | 0 | 0 |
| REDUCER | 4 | 1 | 2 | 0 | 1 |
| QUEUE | 6 | 3 | 0 | 3 | 0 |
| RES | 6 | 5 | 0 | 1 | 0 |
| EXEC | 4 | 0 | 0 | 4 | 0 |
| OBS | 31 | 7 | 7 | 14 | 3 |
| IMPL | 9 | 1 | 2 | 3 | 3 |
| MANAGER | 19 | 2 | 5 | 11 | 1 |
| CTX | 4 | 0 | 0 | 4 | 0 |
| **Total** | **93** | **27** | **18** | **40** | **8** |

Interpretation:

- About 27 invariants are good default-suite Hypothesis targets.
- About 18 should be covered by deterministic enumerations, table tests, or
  spec-audit tests. Hypothesis adds little when the complete state space is
  small.
- About 40 require real queue/process/context evidence. These should stay in
  harness-backed tests, not property fuzzers.
- About 8 are architecture or ownership constraints where tests can guard some
  symptoms, but the primary enforcement is spec mapping, code review, import
  boundaries, or static audit.

Do not try to make the numbers larger by forcing live lifecycle events into
Hypothesis. It will look more thorough while making failures harder to trust.
The goal is high-signal tests, not maximum generated examples.

## Files To Touch

Toolchain and test config:

- `pyproject.toml`: add Hypothesis as a dev dependency and register a
  `property` pytest marker.
- `uv.lock`: update through `uv lock` or `uv sync --all-extras`.
- `.gitignore`: add `.hypothesis/`.
- `tests/conftest.py`: touch only if the backend classification audit requires
  a central entry. Prefer module-level `pytestmark = [pytest.mark.shared,
  pytest.mark.property]` in new pure test modules.

New or changed tests:

- `tests/taskspec/test_taskspec_properties.py`: TaskSpec, immutability,
  resource limits, metrics, and queue resolution properties.
- `tests/specs/taskspec/test_state_transitions.py`: add exhaustive table
  coverage and a small pure sequence property if it is clearer than more hand
  examples.
- `tests/core/monitor/policies/test_dead_task_properties.py`: dead-task queue
  classifier and candidate-selection properties.
- `tests/core/monitor/policies/test_runtime_control_properties.py`: runtime
  reserved-control queue parser and selector properties.
- `tests/core/test_task_evidence_properties.py`: queue fallback and override
  properties for task evidence helpers.
- `tests/system/test_constants_properties.py`: optional second slice for config
  parser properties. Do this only after the first property slice is stable.
- `tests/helpers/hypothesis_strategies.py`: create only after duplication is
  visible across at least two property modules. Keep this helper small.

Docs and specs:

- `docs/specifications/08-Testing_Strategy.md`: move shipped property testing
  behavior into the canonical testing spec.
- `docs/specifications/08A-Testing_Strategy_Planned.md`: narrow or remove
  [TS-A3] once pure invariant sweeps ship.
- `docs/specifications/07-System_Invariants.md`: update implementation notes
  or related-plan links only where tests create a durable invariant mapping.
- `docs/agent-context/runbooks/testing-patterns.md`: add property-test guidance
  after the implementation proves the pattern.
- `docs/plans/README.md`: index this plan and keep the count current.

Do not touch:

- Runtime code under `weft/` unless a property test exposes a real bug and the
  user approves a behavior-fix slice.
- CI config. Add a CI job only if the first implementation shows the default
  suite cannot absorb the property tests.
- `tests/property/`. The current spec says property-style checks remain
  embedded in normal pytest modules.

## Code Style

Follow the repo style from `AGENTS.md`:

- `from __future__ import annotations` in every new Python file.
- Complete type annotations for helper functions.
- Imports grouped stdlib, third-party, local.
- Use `collections.abc` abstract types.
- Use `Path` rather than `os.path`.
- Use succinct comments only where the generated strategy or invariant is not
  obvious.
- Prefer local strategies inside a test module until two modules need the same
  strategy. DRY means avoid real duplication; it does not mean prebuilding a
  large strategy library.
- Keep generated values small. A 10-line failing example is useful. A 300 KB
  nested JSON blob is not.

Hypothesis style:

- Use `@given(...)` with focused strategies. Avoid broad `st.data()` tests that
  become mini-random-program generators.
- Avoid heavy `.filter(...)`. Generate valid shapes directly where possible.
- Avoid blanket `HealthCheck` suppression. If Hypothesis warns, fix the test or
  explain a narrow suppression in a code comment.
- Set explicit `@settings(max_examples=...)` only when needed. Start with the
  default. Lower it for slow but valuable pure tests. Do not raise it in the
  default suite without measured runtime.
- Use `derandomize=True` or a profile only if repeatability becomes an actual
  problem. Do not add global Hypothesis profiles in the first slice unless the
  default behavior is noisy.
- Keep generated tests `shared` unless they truly require SQLite-only behavior.

## Task A0: Verify The Invariant Map

Purpose: prevent a property-test plan from pinning the wrong behavior or
duplicating coverage blindly.

Steps:

1. Read `docs/specifications/07-System_Invariants.md`.
2. Create a short working checklist in the implementation PR description or
   task notes. Do not create a permanent audit doc unless the implementer finds
   a repeated maintenance need.
3. For each invariant family, map existing test coverage by file. Use `rg` for
   invariant IDs and implementation function names.
4. Mark each invariant as one of:
   `property-now`, `deterministic-now`, `harness-now`, `static-review`, or
   `already-covered`.
5. Compare the result to this plan's audit table. If more than five invariants
   move between categories, pause and update this plan or ask for review before
   implementation. That size of drift means the investigation was wrong enough
   to revisit.

Verification:

- The implementer can explain why lifecycle events are not property-fuzzed
  through the live Manager/Consumer path.
- The first code slice targets only pure or nearly pure invariants.
- No test task begins with "mock the queue" or "mock the manager."

## Task A1: Add Hypothesis To The Dev Toolchain

Red-green target: before this task, `python -c "import hypothesis"` fails in
the repo virtualenv. After this task, it succeeds and pytest recognizes the
`property` marker.

Files:

- `pyproject.toml`
- `uv.lock`
- `.gitignore`

Steps:

1. Add `hypothesis>=6` to the existing dev dependency group in
   `pyproject.toml`. Do not add it as a runtime dependency.
2. Add this pytest marker in `pyproject.toml`:
   `property: property-based invariant tests using Hypothesis`.
3. Add `.hypothesis/` to `.gitignore`.
4. Regenerate the lockfile with the repo toolchain:
   `uv lock` or `uv sync --all-extras`. Use the command already expected by the
   repo; do not edit `uv.lock` by hand.

Verification:

```bash
. ./.envrc
uv sync --all-extras
./.venv/bin/python - <<'PY'
import hypothesis
print(hypothesis.__version__)
PY
./.venv/bin/python -m pytest --markers | rg property
git status --short
```

Gate:

- Hypothesis appears only in dev tooling.
- `.hypothesis/` is ignored.
- No runtime package imports Hypothesis.

## Task A2: Add Minimal Shared Strategies Only If Needed

Purpose: keep DRY honest without building a premature strategy framework.

Default choice: keep strategies local in the test module. Create
`tests/helpers/hypothesis_strategies.py` only when two or more modules need the
same strategy and the duplicate code is non-trivial.

If the helper file is created, include only these small functions:

- `taskspec_tid_strings()`: valid 19-digit SimpleBroker-style TaskSpec TID
  strings within the current validator bounds.
- `digit_tid_strings()`: non-empty digit strings for queue-name classifiers
  that own only task-local queue identity, not full TaskSpec TID validation.
- `invalid_taskspec_tid_strings()`: strings that are close to TaskSpec TIDs but
  invalid, such as empty strings, signed numbers, non-digits, decimal strings,
  and wrong-length digit strings.
- `json_scalars()`: `None`, bools, bounded ints, bounded floats without NaN or
  infinity, and short strings.
- `json_values(max_leaves: int = 8)`: small JSON-like values built from the
  scalar strategy.
- `queue_suffixes()`: the canonical task-local suffixes used by Weft.

Do not reuse `invalid_taskspec_tid_strings()` to assert queue-name classifiers
reject wrong-length digit strings. Current cleanup classifiers parse standard
task-local queue identity; they do not own the full TaskSpec TID validator
unless the code and spec are changed first.

Do not add:

- A strategy for live `Queue` objects.
- A strategy that starts Manager, Consumer, or subprocesses.
- A global TaskSpec strategy that tries to generate every schema field. That is
  too broad for the first slice.

Verification:

```bash
./.venv/bin/ruff check tests/helpers tests/taskspec tests/core tests/specs tests/system
```

Gate:

- The helper exists only if it removes real duplication.
- Strategies generate small, readable failing examples.
- No generated example creates filesystem, process, or broker state.

## Task A3: Add TaskSpec Property Tests

Primary invariants: [IMMUT.1-IMMUT.4], [QUEUE.1-QUEUE.3], [RES.1-RES.5],
[STATE.3-STATE.5].

Files:

- `tests/taskspec/test_taskspec_properties.py`
- `weft/core/taskspec/model.py` only if a test exposes a real bug

Module header:

```python
from __future__ import annotations

import pytest
from hypothesis import given

pytestmark = [pytest.mark.shared, pytest.mark.property]
```

Tests to write:

1. `test_resolve_taskspec_payload_does_not_mutate_input`
   - Generate small command argv lists, env mappings, tags, metadata, and TIDs.
   - Deep-copy the input payload before calling `resolve_taskspec_payload()`.
   - Assert the original payload is unchanged.
   - Assert generated default queues are `T{tid}.inbox`,
     `T{tid}.reserved`, `T{tid}.outbox`, `T{tid}.ctrl_in`, and
     `T{tid}.ctrl_out` when no overrides are provided.

2. `test_resolved_taskspec_freezes_spec_and_io`
   - Generate valid minimal TaskSpec payloads with small nested `spec` and `io`
     sections.
   - Construct `TaskSpec` through the production constructor or
     `resolve_taskspec_payload()`.
   - Try to mutate top-level `spec`, top-level `io`, and one nested mutable
     child in each section.
   - Assert mutation raises the existing exception type used by current
     immutability tests.
   - Assert `metadata`, `state`, or runtime metrics remain mutable through the
     supported methods.

3. `test_limit_validation_accepts_only_positive_resource_bounds`
   - Generate memory, fd, timeout, and CPU values around boundaries.
   - Assert valid positive values construct successfully.
   - Assert zero, negative, NaN, infinity, and CPU values outside the allowed
     percent range fail with `ValidationError`.
   - Keep this as model validation. Do not start a task to prove validation.

4. `test_metric_updates_preserve_current_and_peak_relationship`
   - Generate a sequence of metric update dictionaries.
   - Apply them through `TaskSpec.update_metrics()` or the current public
     method.
   - Assert current metrics never exceed recorded peaks after each update.
   - Assert unrelated immutable sections remain unchanged.

5. `test_running_and_terminal_timestamps_are_coherent`
   - Generate valid status-operation sequences using public status methods
     (`mark_running`, `mark_completed`, `mark_failed`, or `set_status` as
     appropriate).
   - Assert running has `started_at`.
   - Assert terminal states after running have `completed_at`.
   - Assert `completed_at >= started_at` when both exist.

Red-green TDD:

- Add one property at a time.
- First run the new test and confirm either it fails for the missing dependency
  or passes after A1. If it exposes an actual bug, preserve the smallest
  failing example and stop before changing runtime behavior.
- Do not write all properties first and then debug a large failure pile.

Verification:

```bash
./.venv/bin/python -m pytest tests/taskspec/test_taskspec_properties.py -q
./.venv/bin/python -m pytest tests/taskspec/test_taskspec.py -q
```

Gate:

- Tests use real TaskSpec construction and methods.
- No mocks.
- No broker, filesystem, process, or wall-clock sleeps.

## Task A4: Add Lifecycle And Reducer Coverage Carefully

Primary invariants: [STATE.1-STATE.6], [REDUCER.2-REDUCER.4].

Files:

- `tests/specs/taskspec/test_state_transitions.py`
- `tests/core/test_state_machines.py` only if the reducer helper needs one
  small generated validation test

Important judgment:

- The lifecycle transition table is small. Exhaustive deterministic tests are
  better than Hypothesis for the complete pair matrix.
- Hypothesis is useful only for generated sequences through public TaskSpec
  status methods, where it can find invalid terminal back-transitions or
  timestamp combinations.
- Do not property-test live lifecycle events through Manager or Consumer in the
  default suite. That would test scheduling and cleanup noise more than
  lifecycle semantics.

Tests to write:

1. Complete transition pair matrix
   - Enumerate all known statuses from `weft/core/task_lifecycle.py`.
   - For each `(from_status, to_status)` pair, assert acceptance exactly
     matches the transition table.
   - Assert every documented edge in [STATE.1] has a test assertion.
   - Assert terminal states have no outgoing non-terminal transition.

2. Generated public status operation sequence
   - Generate short sequences of public operations such as run, complete,
     fail, timeout, cancel, and kill.
   - Apply each to a fresh TaskSpec.
   - Allowed invalid operations should raise the existing lifecycle exception,
     not corrupt state.
   - Once terminal, later generated operations must either leave the state
     terminal or raise. They must never return to `created`, `spawning`, or
     `running`.

3. Optional reducer construction validation
   - Generate small invalid state-machine definitions only if current
     deterministic tests do not already cover the construction failure space.
   - Assert construction rejects missing states, duplicate transition IDs, and
     terminal outgoing edges.
   - Do not test reducer purity with Hypothesis. Purity is enforced by code
     review, import boundaries, and the fact that reducer tests pass plain
     values.

Verification:

```bash
./.venv/bin/python -m pytest tests/specs/taskspec/test_state_transitions.py -q
./.venv/bin/python -m pytest tests/core/test_state_machines.py -q
```

Gate:

- Every lifecycle edge is covered deterministically.
- The generated sequence test starts from a fresh TaskSpec for each example.
- No Manager, Consumer, Queue, sleep, or process execution appears in these
  property tests.

## Task A5: Add Queue Classifier And Evidence Properties

Primary invariants: [QUEUE.1-QUEUE.3], selected [OBS.13] cleanup identity
rules, and monitor policy identity constraints.

Files:

- `tests/core/monitor/policies/test_dead_task_properties.py`
- `tests/core/monitor/policies/test_runtime_control_properties.py`
- `tests/core/test_task_evidence_properties.py`
- `weft/core/monitor/policies/dead_task.py` only if a test exposes a real bug
- `weft/core/monitor/policies/runtime_control.py` only if a test exposes a real
  bug
- `weft/core/task_evidence.py` only if a test exposes a real bug

Tests to write:

1. Dead-task queue classifier accepts exact task queues
   - Generate valid TIDs and canonical suffixes.
   - Assert `T{tid}.{suffix}` parses to the expected task identity.
   - Generate near misses: lowercase `t`, missing suffix, extra segment,
     non-digit TID, empty TID, empty suffix, unknown suffix, and runtime queue
     names.
   - Assert near misses are rejected.
   - Do not assert wrong-length digit TIDs are rejected unless the production
     helper is changed to own full TaskSpec TID validation. Today that belongs
     to `weft/core/taskspec/model.py`.

2. Runtime-control reserved queue classifier accepts exact reserved queues
   - Generate valid TIDs and assert `T{tid}.reserved` is accepted.
   - Generate all other task-local suffixes and assert they are rejected by the
     reserved-only parser.
   - Generate malformed names and assert rejection.

3. Candidate selectors are stable and do not duplicate TIDs
   - Generate small lists of queue names containing valid names, duplicates,
     near misses, and runtime queues.
   - Assert selected task IDs are unique.
   - Assert selected task IDs are sorted by the production rule if the current
     code promises ordering. If ordering is not promised, assert as a set only
     and do not create a new contract by accident.
   - Assert live or too-young entries are excluded when the current selector
     accepts age/liveness evidence.

4. Task evidence queue fallback respects overrides
   - Generate TaskSpec queue override combinations.
   - Assert blank or absent values fall back to `T{tid}.*`.
   - Assert non-empty override values are preserved exactly.
   - Assert control queue helpers return exactly one `ctrl_in` and one
     `ctrl_out`.

Verification:

```bash
./.venv/bin/python -m pytest tests/core/monitor/policies/test_dead_task_properties.py -q
./.venv/bin/python -m pytest tests/core/monitor/policies/test_runtime_control_properties.py -q
./.venv/bin/python -m pytest tests/core/test_task_evidence_properties.py -q
```

Gate:

- Tests call production parsers/selectors.
- No regex reimplementation in the tests. Assert behavior through public or
  module-private helpers that already own the policy.
- No SimpleBroker mock. These are classifier tests over plain queue names.

## Task A6: Add Config Parser Properties As A Second Slice

Primary invariants: selected [CTX] and [IMPL] configuration boundaries.

This task is optional for the first implementation pass. Do it only after A3,
A4, and A5 are stable and the property suite is still fast.

Files:

- `tests/system/test_constants_properties.py`
- `weft/_constants.py` only if a test exposes a real bug

Tests to write:

1. Boolean parser
   - Generate strings with random casing and whitespace around known truthy and
     falsey tokens.
   - Assert only documented true values produce `True`.
   - Assert only documented false values produce `False` where the parser has a
     false branch.

2. Positive int and float parsers
   - Generate boundary values as strings.
   - Assert positive values parse.
   - Assert zero, negative, empty, decimal-for-int, NaN, and infinity values
     reject or fall back exactly as the current function specifies.

3. Weft directory name parser
   - Generate names with path separators, empty values, dot entries, spaces,
     and safe names.
   - Assert unsafe values are rejected or normalized exactly as documented by
     current tests and code.

Warning:

- These helpers are private. Testing private helpers is acceptable here only
  because `_constants.py` is the central environment boundary and the parsers
  are the contract. If the public `compile_config()` path can express the same
  property without heavy env mutation, prefer the public path.

Verification:

```bash
./.venv/bin/python -m pytest tests/system/test_constants_properties.py -q
```

Gate:

- No global environment leaks. Use `monkeypatch` for env mutation.
- No new config semantics unless a separate spec update approves them.

## Task A7: Update Specs And Documentation

This is not optional. The user explicitly asked that associated specs and
documentation be updated.

Files:

- `docs/specifications/08-Testing_Strategy.md`
- `docs/specifications/08A-Testing_Strategy_Planned.md`
- `docs/specifications/07-System_Invariants.md` where needed
- `docs/agent-context/runbooks/testing-patterns.md`
- `docs/plans/README.md`

Required updates after implementation:

1. `docs/specifications/08-Testing_Strategy.md`
   - Add Hypothesis to the current suite description.
   - State that property tests live beside the domain they test, not in
     `tests/property/`.
   - Name the `property` marker.
   - State the fixture caveat: generated examples should not share live
     `WeftTestHarness`, broker, process, or filesystem state.
   - Add the property modules that landed.
   - Add this plan under Related Plans.

2. `docs/specifications/08A-Testing_Strategy_Planned.md`
   - If A3-A5 ship, remove or narrow the broad [TS-A3] planned language so it
     does not describe shipped behavior as future work.
   - Leave future live broker/lifecycle fuzzing as planned only if there is a
     concrete reason to pursue it later. Otherwise say it is intentionally not
     canonical.
   - Add this plan under Related Plans.

3. `docs/specifications/07-System_Invariants.md`
   - Update implementation mapping notes only if tests reveal stale mappings.
   - Add a related-plan backlink if the implementation creates a durable
     invariant-to-test mapping.
   - Do not rewrite invariant text merely to match test names.

4. `docs/agent-context/runbooks/testing-patterns.md`
   - Add a concise property-testing section:
     - use Hypothesis for pure invariant sweeps
     - avoid live lifecycle fuzzing in the default suite
     - do not over-mock queues/processes
     - keep generated examples small
     - avoid blanket health-check suppression
     - module-level `pytestmark` must include `shared` or `sqlite_only`

5. `docs/plans/README.md`
   - Keep this plan indexed while it is active.
   - If implementation completes and the plan remains accurate, change the
     plan status and README row to `completed`.

Verification:

```bash
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
./.venv/bin/python -m pytest tests/specs/test_test_audit_policy.py -q
```

Gate:

- Specs describe current behavior after the code lands.
- Planned docs contain only genuinely future work.
- Plan backlinks are bidirectional enough for a zero-context engineer to find
  the governing spec and the implementation plan.

## Task A8: Run The Verification Ladder

Run the smallest relevant tests after each task, then expand. Do not wait until
the end.

Minimum targeted gates:

```bash
. ./.envrc
uv sync --all-extras
./.venv/bin/python -m pytest tests/taskspec/test_taskspec_properties.py -q
./.venv/bin/python -m pytest tests/specs/taskspec/test_state_transitions.py -q
./.venv/bin/python -m pytest tests/core/monitor/policies/test_dead_task_properties.py -q
./.venv/bin/python -m pytest tests/core/monitor/policies/test_runtime_control_properties.py -q
./.venv/bin/python -m pytest tests/core/test_task_evidence_properties.py -q
./.venv/bin/python -m pytest -m property -q
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_test_audit_policy.py -q
```

Full gates before handoff:

```bash
./.venv/bin/python -m pytest
./.venv/bin/mypy weft bin extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft tests
```

Backend gate:

```bash
bin/pytest-pg --all
```

Run the backend gate if any test touches broker behavior, queue persistence, or
classification boundaries that differ by backend. If the final slice remains
pure and shared, document why the backend gate was not run.

Performance gate:

- `./.venv/bin/python -m pytest -m property -q` should stay comfortably under
  one minute on a normal developer machine.
- If it does not, lower example counts for the slow property, split the test,
  or move the slow scenario out of the default suite. Do not hide slow tests
  behind broad marks without updating the testing spec.

## Task A9: Review And Handoff

Fresh-eyes review checklist:

1. Read the property tests as if the code under test is wrong.
2. Confirm each property states a real invariant, not a restatement of the
   implementation.
3. Confirm generated values are small enough that a failure is actionable.
4. Confirm no property test uses a mock where a production helper can be
   called directly.
5. Confirm live lifecycle events remain in real harness tests with
   deterministic scenarios.
6. Confirm specs and docs match the landed state.
7. Confirm no runtime behavior changed without a separate behavior-fix review.

External review:

- Request an independent review if implementation touches runtime code, CI, or
  more than the listed files.
- A review is recommended even for the pure test-only slice because the change
  affects the toolchain and testing doctrine.

Handoff notes must include:

- Which invariant families gained property coverage.
- Which invariants were intentionally left to deterministic or harness tests.
- Exact verification commands run.
- Any Hypothesis health checks, flakes, or shrinking issues encountered.
- Any discovered runtime bugs that were deferred to a separate task.

## Out Of Scope

- No live Manager/Consumer lifecycle fuzzing in the default suite.
- No new `tests/property/` tree.
- No new runtime dependency.
- No new public API, CLI behavior, queue format, TaskSpec field, or persisted
  format.
- No CI job unless the implementation proves the default suite is not the
  right home.
- No Hypothesis ghostwriter output checked in without hand review and rewrite.
- No broad test abstraction layer unless duplication forces it.

## Rollback

Rollback is simple because this plan is test/tooling/docs only:

1. Remove property tests added by A3-A6.
2. Remove shared Hypothesis strategies if created.
3. Remove Hypothesis from `pyproject.toml` and regenerate `uv.lock`.
4. Remove the `property` marker if no tests use it.
5. Remove `.hypothesis/` from `.gitignore` only if Hypothesis is fully removed.
6. Revert testing-spec and runbook updates that describe shipped property
   tests.
7. Keep any separately approved runtime bug fixes. Do not roll those back as
   part of removing Hypothesis unless the bug-fix task says so.

## Plan Author Fresh-Eyes Review

Review pass 1 found four issues in the draft direction:

- It was tempting to treat lifecycle events as a Hypothesis target because
  lifecycle has many invariants. That is the wrong boundary. The plan now
  separates pure lifecycle transition checks from live Manager/Consumer
  behavior and keeps live behavior in harness tests.
- The initial instinct to add a shared strategy module up front was premature.
  The plan now requires local strategies first and creates a helper only after
  duplication is visible.
- The invariant audit needed counts, not just examples. The plan now includes
  a 93-invariant classification table and an A0 verification task so an
  implementer must validate the map before coding.
- The initial queue-classifier instructions confused full TaskSpec TID
  validation with standard task-local queue identity. The plan now separates
  TaskSpec TID strategies from digit-only queue identity strategies.

Review pass 2 looked for ambiguity a zero-context engineer would exploit:

- The plan now names exact files to touch and exact files not to touch.
- The plan now states that Hypothesis is a dev dependency only.
- The plan now gives concrete gates for property tests, docs, backend
  classification, and full verification.
- The plan now makes docs/spec updates a required implementation task instead
  of a final optional cleanup.

Review pass 3 checked the written plan against the current queue-classifier
code paths and lifecycle-test boundary:

- Queue classifiers remain digit-identity tests unless the implementation
  changes them to call full TaskSpec TID validation.
- Lifecycle transition-table coverage remains deterministic; only public
  status-operation sequences are proposed as property tests.
- No remaining instruction asks the implementer to fuzz live Manager/Consumer
  lifecycle events in the default suite.

This plan remains materially in the same direction as the investigation:
target high-signal pure invariant sweeps, do not fuzz live lifecycle events by
default, and keep real durable behavior covered through real harness tests.

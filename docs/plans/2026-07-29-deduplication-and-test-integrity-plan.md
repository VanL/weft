# Deduplication and Test Integrity Plan

Status: completed
Source specs: docs/specifications/02-TaskSpec.md [TS-1]; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/08-Testing_Strategy.md [TS-0], [TS-1]; docs/specifications/09-Implementation_Plan.md [IP-1], [IP-1.0]
Superseded by: none

Class: 3 — behavior-preserving maintenance across command/core ownership and
test contracts. No public behavior is intended to change, but the work removes
parallel implementations and strengthens tests at runtime boundaries.

## 1. Goal

Delete four verified duplicate converter/timing paths, remove four dead
compatibility helpers, replace queue tests that only restate their fixture,
make two generated transition tests non-vacuous, and add a live configuration
key-parity guard. Each behavior will have one implementation and at least one
test that fails when that implementation or assertion path is broken.

This is the fourth of four independent plans extracted from
[`2026-07-29-structural-review-remediation-plan.md`](./2026-07-29-structural-review-remediation-plan.md).
It deliberately bundles small mechanical deletions and test repairs. It does
not include the architectural validation, import, or MF-5 reducer work in the
other three plans.

## 2. Source Documents

- `docs/specifications/02-TaskSpec.md` [TS-1] defines resolved TaskSpec queue
  names and state timestamps.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5] defines shared
  runtime/result evidence interpretation.
- `docs/specifications/08-Testing_Strategy.md` [TS-0], [TS-1] requires tests
  across the real queue-first system and records the current property suite.
- `docs/specifications/09-Implementation_Plan.md` [IP-1], [IP-1.0] defines the
  `commands -> core` direction and shared client/CLI capabilities.
- No normative spec enumerates `_load_weft_env_vars` versus in-process
  override keys. The parity check is a maintenance invariant over
  `weft/_constants.py`, not a new configuration contract.

Required guidance:

- `CLAUDE.md` §4.1, §4.8, and §5.2
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`

The umbrella plan is exploratory context only. This plan corrects one flaw in
its property-test proposal: an arbitrary generated operation list can legally
contain only transitions invalid from `created`, so asserting success without
changing the generator would reject correct code.

## 3. Context and Key Files

### Verified duplicate paths

Re-verify bodies and references immediately before editing:

| Behavior | Duplicate definitions | Canonical owner |
|---|---|---|
| manager record conversion | `weft/commands/manager.py::_manager_snapshot`; `weft/commands/system.py::_manager_snapshot` | `commands/manager.py` |
| runner runtime description | `weft/core/task_evidence.py::_runtime_description`; `weft/commands/system.py::_describe_runtime_handle` | `core/task_evidence.py::describe_runtime` |
| stdout/stderr extraction | `weft/core/task_evidence.py::split_stdio`; `weft/commands/result.py::_split_stdio` | `core/task_evidence.py::split_stdio` |
| timeout/deadline math | `weft/commands/tasks.py::_deadline_from_timeout` and `_remaining_timeout`; copies plus `_timed_out` in `commands/events.py`; inline expiry in `tasks.py` | `commands/tasks.py`, adding `_deadline_expired` |

The runtime-description bodies are currently identical, including the
defensive runner-plugin catch. Promote the core function to the short
declarative name `describe_runtime`; re-export it through
`weft/commands/task_evidence.py`. Update `commands/system.py`,
`commands/tasks.py`, and all reference-scan results. Do not keep a second
compatibility wrapper for private names.

`commands/events.py` already imports the `tasks` sibling. Delete its three
deadline definitions and call the canonical task helpers through that module.
Replace the inline `time.monotonic() >= deadline` in `commands/tasks.py` with
`_deadline_expired`. Do not create a generic time utility module for three
command-local functions.

### Verified dead helpers

The following `weft/commands/tasks.py` functions had no references in `weft`,
`tests`, `integrations`, or `extensions` at review time:

- `_mapping_has_prior_live_proof`
- `_runtime_snapshot_from_mapping`
- `_bounded_log_terminal_snapshot`
- `_stale_observer_snapshot`

Run an exact-symbol `rg` immediately before deletion. If any caller has
appeared, remove that helper from this plan and review the caller; do not
silently delete it or redirect behavior.

### Weak tests

- `tests/tasks/test_tasks_simple.py::test_required_queues` and
  `test_custom_taskspec_queues` assert fields on the same TaskSpec object passed
  to `Consumer`. `test_task_has_basic_attributes` proves object identity, so
  those assertions do not prove queue wiring.
- Two Hypothesis tests swallow all `ValueError` transitions. If every
  operation rejects, their post-operation assertions can remain vacuous.

### Configuration parity

`_load_weft_env_vars()` and `_normalize_weft_override_value()` repeat the
supported-key inventory in two syntactic forms. The known set differences are
five names in three intentional categories:

1. loader-only `WEFT_MANAGER_RUNTIME_HANDLE_JSON`, whose override identity
   fall-through is equivalent;
2. normalizer-only removed-key rejection branches for
   `WEFT_TASK_MONITOR_TASK_LOG_CUTOFF_SECONDS`,
   `WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED`, and
   `WEFT_TASK_MONITOR_CLEANUP_WORKERS`; and
3. normalizer-only internal `MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY`.

Do not describe these as “four asymmetries”; that confuses four categories in
the umbrella wording with five concrete keys.

### Files to modify

- `weft/commands/manager.py`
- `weft/commands/system.py`
- `weft/commands/result.py`
- `weft/commands/tasks.py`
- `weft/commands/events.py`
- `weft/commands/task_evidence.py`
- `weft/core/task_evidence.py`
- `tests/core/test_client.py`
- `tests/commands/test_manager_commands.py`
- focused command/core evidence tests found by the reference scan
- `tests/tasks/test_tasks_simple.py`
- `tests/tasks/test_task_execution.py`
- `tests/tasks/test_control_channel.py`
- `tests/taskspec/test_taskspec_properties.py`
- `tests/specs/taskspec/test_state_transitions.py`
- `tests/system/test_constants.py`
- touched module/spec implementation mappings
- `docs/plans/README.md`

### Read first

- both definitions in every row of the duplicate table
- all exact-symbol reference-scan results
- `BaseTask._resolve_queue_names`, `_reactor_queue_roles`, and initialization
- the complete two generated transition tests and the transition matrix in
  `tests/specs/taskspec/test_state_transitions.py`
- both configuration functions and current constants AST-test helpers

## 4. Invariants and Constraints

Preserve:

- every converter output field, defensive fallback, timestamp conversion, and
  result shape
- deadline use of `time.monotonic`, `None` as no deadline, clamping remaining
  time to zero, and equality (`now >= deadline`) as expired
- current private/public command and client results; deleted names are verified
  private and unreferenced
- default and custom runtime queue roles:
  `inbox`, `reserved`, `outbox`, `ctrl_in`, and `ctrl_out`
- additional TaskSpec input entries remain schema data; this plan does not
  make BaseTask watch arbitrary input queues
- legal TaskSpec transitions and timestamp behavior
- loader/override key differences in the explicit five-name allowlist

Not allowed:

- adding a shared converter or deadline module
- keeping forwarding wrappers solely to avoid editing private call sites
- widening BaseTask to watch `data`, `config`, or arbitrary `io.inputs`
- building a second lifecycle transition model in tests
- deriving config parity from a third handwritten complete key list
- changing parser behavior, defaults, client fields, or public JSON
- folding the MF-5 reducer or import-boundary work into this plan

Each sub-slice is independently revertible. Revert the canonical call-site
changes with the deleted duplicate definition. Test-strengthening changes can
remain after any production-code rollback. No persistent-state migration or
rollout ordering exists.

## 4a. Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|
| [TS-1] / Task 4 | Add a keyed-PING custom-control routing test. | Strengthened the existing real custom-control reconstruction test with default-queue traps before and after reconstruction. | The existing test already drove keyed PING through custom ctrl-in/out; adding a second test would duplicate the path. The traps add the missing proof that default control queues remain untouched. | Closed; no behavior change. |

## 4b. Spec Baseline

- `05070d79193f6098e18a3b23d6aa935ca98242d2` —
  `docs/specifications/02-TaskSpec.md`,
  `docs/specifications/05-Message_Flow_and_State.md`,
  `docs/specifications/08-Testing_Strategy.md`, and
  `docs/specifications/09-Implementation_Plan.md`
- Plan type: behavior-preserving deduplication/test repair; no proposed spec
  delta

## 5. Implementation Tasks

### Task 1 — Prove and consolidate manager conversion

- Strengthen
  `test_system_and_manager_namespaces_expose_shared_runtime_state` so the
  manager selected from `client.system.status().managers` equals
  `client.managers.status(tid)` field by field. Compare the complete public
  dataclass, not only TID.
- Add an independent converter contract in
  `tests/commands/test_manager_commands.py`. Feed `_manager_snapshot` a crafted
  record containing every optional field and assert the complete expected
  dataclass. Add malformed-optional-field and numeric versus nonnumeric
  timestamp cases. Expected values must be written from the raw record
  contract, not produced by the system namespace.
- Mutation proof: alter each field/coercion in the single canonical converter
  and show the crafted-record contract fails. Namespace equality is an adapter
  drift guard, not the converter oracle.
- Import `_manager_snapshot` from `commands.manager` into `system.py`; delete
  the local body. Keep one call path and one definition.

### Task 2 — Consolidate evidence converters

- Rename core `_runtime_description` to `describe_runtime`, export it from
  core's `__all__` if present, and re-export it from the commands compatibility
  shim.
- Replace the system and tasks private calls with
  `task_evidence.describe_runtime`; delete `_describe_runtime_handle`.
- Replace result/tasks `_split_stdio` call sites with
  `task_evidence.split_stdio`; delete both command wrappers.
- Add focused value tests for external-supervisor, plugin `None`, plugin
  exception, structured stdout/stderr, wrong types, and non-dict values. Reuse
  existing plugin fixtures; patch only runner-plugin lookup for its three
  return/failure branches.
- Mutation proof: change one returned field/type guard and show a focused test
  fails.

### Task 3 — Consolidate deadline math and remove dead helpers

- Add `_deadline_expired(deadline)` beside the two canonical helpers in
  `commands/tasks.py`.
- Delete the three copies in `events.py`; use the imported tasks module at
  every call site. Replace the inline tasks expiry comparison.
- With `time.monotonic` fixed at an exact value, table-test no deadline,
  negative timeout clamping, before, equal, and after deadline. Equality must
  be expired.
- Re-run the four-symbol dead-helper scan, then delete only still-unreferenced
  functions and imports made unused by them.
- Run Ruff after deletion so stale imports cannot survive.

### Task 4 — Test resolved queue topology

- Rewrite `test_required_queues` to assert
  `task._reactor_queue_roles()` contains the five expected default concrete
  names and that the eagerly opened ctrl-out queue has the resolved name.
- Configure `inbox`, `outbox`, `ctrl_in`, and `ctrl_out` explicitly in
  `test_custom_taskspec_queues`; assert the same runtime role map and ctrl-out
  handle use those names. Assert `reserved` remains TID-derived.
- Keep one additional `data` input and assert it is absent from the reactor
  role map. This distinguishes schema preservation from runtime wiring without
  pretending the input is consumed.
- The protected role map is acceptable as a supporting structural assertion,
  but it is not the behavioral proof.
- Add real routing tests using the existing `broker_env`, consumer-driving, and
  queue helpers in `tests/tasks/test_task_execution.py` and
  `tests/tasks/test_control_channel.py`:
  1. write a work item to a custom inbox and assert its result appears on the
     custom outbox, not the default names;
  2. write keyed PING to custom ctrl-in, drive one control cycle, and assert
     the matching PONG is read from custom ctrl-out; and
  3. place a message in the TID-derived reserved queue under REQUEUE-on-STOP,
     send STOP through custom ctrl-in, and assert the exact message returns to
     the custom inbox and leaves reserved.
- Mutation proof: make `_resolve_queue_names` ignore one configured canonical
  role and show its corresponding behavioral route test fails.

### Task 5 — Make generated transition tests non-vacuous

- Change each Hypothesis input to generate a pair:
  1. an initial operation sampled from the transitions currently legal from
     `created` (`started`, `running`, `failed`, `cancelled`); and
  2. a possibly empty tail from the full operation set.
- Execute the guaranteed-valid prefix through the same loop as the tail.
  Count successful operations; retain `ValueError` handling for invalid tail
  transitions; assert `successful_operations >= 1`.
- Keep all current timestamp and terminal-stickiness assertions.
- Do not merely add a counter to the existing arbitrary list. A list containing
  only `completed`, `timeout`, or `killed` can correctly have zero successful
  transitions from `created`.
- Mutation proof: temporarily force every operation callable to raise and show
  both properties fail at the success assertion.

### Task 6 — Add live configuration key parity

- In `tests/system/test_constants.py`, derive loader keys by calling
  `_load_weft_env_vars()` under a clean `WEFT_*` environment and reading the
  returned dictionary keys. Do not copy its dict literal.
- Derive explicitly normalized keys by parsing
  `_normalize_weft_override_value` with `ast`: collect string literals and
  module string constants used in `name == ...` and `name in {...}` tests.
  Resolve `ast.Name` nodes only through the imported constants module and
  require their values to be strings; fail loudly on an unsupported comparison
  shape so new code cannot disappear from the analysis.
- Compare the two derived sets after applying the exact five-name,
  direction-specific allowlist in §3.
- Add an assertion that every allowlisted key is actually present on its
  stated side and absent from the other, so stale allowlist entries fail.
- Mutation proof: add an unpaired loader or normalizer branch and show parity
  fails.

### Task 7 — Reconcile traceability

- Update [MF-5]/[IP-1] implementation mappings only for renamed ownership.
- Update touched module docstrings and add reciprocal plan backlinks.
- Close any implementation-time Deviation Log rows and record final evidence.

## 6. Testing Plan

```text
ONE IMPLEMENTATION
  manager/runtime/stdio/deadline call sites
    -> canonical owner only

REAL ADAPTER + TASK BOUNDARIES
  manager namespaces -> equal complete snapshots
  constructed Consumer -> resolved role map
  real work/control/STOP -> custom inbox/outbox/ctrl routes + TID reserved

GENERATED LIFECYCLE
  guaranteed legal prefix + arbitrary tail
    -> at least one fired transition + invariants after every attempt

CONFIG INVENTORY
  live loader result set <-> AST-derived explicit normalizer set
    -> exact directional exceptions only
```

Mocks are acceptable only at the runner-plugin lookup boundary for converter
fallback branches. They are not acceptable for manager/client equality, queue
topology, transition operations, or configuration inventory.

## 7. Verification and Gates

Focused:

```bash
./.venv/bin/python -m pytest tests/core/test_client.py -q
./.venv/bin/python -m pytest tests/commands/test_manager_commands.py -q
./.venv/bin/python -m pytest tests/tasks/test_tasks_simple.py tests/tasks/test_task_execution.py tests/tasks/test_control_channel.py -q
./.venv/bin/python -m pytest tests/taskspec/test_taskspec_properties.py tests/specs/taskspec/test_state_transitions.py -q
./.venv/bin/python -m pytest tests/system/test_constants.py -q
./.venv/bin/python -m pytest tests/commands/test_task_evidence.py tests/core/test_task_evidence_properties.py -q
./.venv/bin/ruff check weft tests
```

Definition/reference proof:

```bash
rg -n "def (_manager_snapshot|_describe_runtime_handle|_runtime_description|_split_stdio|_deadline_from_timeout|_remaining_timeout|_timed_out)" weft
rg -n "_mapping_has_prior_live_proof|_runtime_snapshot_from_mapping|_bounded_log_terminal_snapshot|_stale_observer_snapshot" weft tests integrations extensions
```

The first scan must be interpreted against the canonical names, not asserted
empty: one manager converter, one stdio converter, and the canonical deadline
definitions remain. The second scan must be empty.

Final:

```bash
. ./.envrc
./.venv/bin/python -m pytest -m ""
./.venv/bin/mypy weft bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check weft
./bin/check-doc-paths
./bin/check-dom15-fixtures
```

## 8. Independent Review Loop

Review is required because several “mechanical” deletions cross command/core
ownership and because weak tests can look stronger without gaining mutation
sensitivity. The reviewer must inspect every duplicate body, all call sites,
BaseTask queue resolution, both property generators, and both configuration
functions.

Review prompt:

> Answer PASS or BLOCKED. Are the selected canonical owners layer-correct, are
> all duplicate/dead paths truly removable, and does every replacement test
> fail under a realistic mutation? Check that arbitrary TaskSpec inputs are not
> accidentally promoted to runtime lanes, legal property inputs cannot fail
> the new success guard, and config parity derives both inventories without a
> third handwritten list.

Accepted changes receive a scoped round-2 verification by the same reviewer.

## 9. Out of Scope

- Declarative configuration registries or rewriting `_constants.py`
- Reducing unrelated C901 functions
- Renaming public APIs or general naming cleanup
- Supporting arbitrary TaskSpec input lanes in BaseTask
- A second lifecycle reference model
- Import-cycle, validation-layer, or MF-5 reducer changes
- Interactive session or manager complexity work

## 10. Fresh-Eyes Review

Completed 2026-07-29 against the draft and current source:

- Reverified the duplicate definitions and all direct reference sites.
- Corrected the property-test proposal: arbitrary lists can contain only
  transitions invalid from `created`, so each generator now supplies one
  known-legal prefix before its arbitrary tail.
- Counted the config differences as five concrete names in three categories
  and made the allowlist directional and self-expiring.
- Replaced the nonexistent `tests/core/test_task_evidence.py` gate with the two
  real task-evidence test modules.
- Added an independent crafted-record manager converter oracle; comparing two
  consumers of one converter is only an adapter drift test.
- Replaced queue-map-only proof with real custom work, control, and reserved
  requeue routes.
- Removed the proposed normative TaskSpec clarification, keeping the plan
  behavior-preserving and correctly classified as Class 3.

## 11. Independent Review Result

Reviewer: `tests_quality` (independent agent), 2026-07-29.

Round 1: **BLOCKED** on three issues:

1. post-consolidation namespace equality could not detect a shared manager
   converter defect;
2. a role map plus one ctrl-out handle did not prove inbox, outbox, ctrl-in, or
   reserved routing; and
3. the proposed normative [TS-1] clarification would require Class 5 rather
   than Class 3.

The plan now includes an independent raw-record converter contract, real
queue-routing tests for all five roles, and no normative spec delta.

Round 2: **PASS**. The same reviewer verified all three corrections and
reported no remaining material defect.

## 12. Implementation Result

Completed 2026-07-29.

- Consolidated manager-record conversion in
  `weft/commands/manager.py::_manager_snapshot`.
- Consolidated runtime description and structured stdout/stderr extraction in
  `weft/core/task_evidence.py`, with the command compatibility shim
  re-exporting both helpers.
- Consolidated optional-deadline math in `weft/commands/tasks.py`; event
  iteration now calls that canonical helper set.
- Deleted the four reverified unreferenced task helpers and all duplicate
  converter/deadline definitions.
- Replaced fixture-restating queue tests with exact reactor topology checks and
  real custom work, PING, STOP, and reserved-message routes.
- Guaranteed one legal transition in both generated lifecycle properties and
  asserted that at least one operation succeeds.
- Added a live environment-loader versus AST-derived explicit-normalizer key
  parity guard with exact directional exceptions.

Mutation proof caught deliberate changes to manager field conversion, runtime
description, stdout type guarding, deadline equality, custom outbox
resolution, all-transition rejection, and an unpaired normalizer key. The
complete manager dataclass oracle and malformed/timestamp tables make every
converter field and coercion branch observable.

Implementation review:

- Grok round 1: **PASS**, with non-blocking requests for explicit default
  control-queue isolation and a direct null runtime-handle case.
- Both observations were addressed.
- Grok round 2: **PASS**. The reviewer confirmed the added assertions are
  observable, non-vacuous, and not coupled to private queue maps.

Verification:

- all focused command, client, task, TaskSpec, evidence, and configuration
  suites passed
- full suite: 2471 passed, 14 skipped
- mypy: 195 source files, no issues
- Ruff: passed
- definition/reference scans: one canonical definition per behavior; all four
  dead-helper symbols absent
- DOM-15 fixture contract: passed
- documentation path check: unchanged repository baseline of eight dangling
  claims, none introduced by this plan

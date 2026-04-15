# Maintainability And Boundary Remediation

## 1. Goal

Turn the validated architecture, testing, and documentation concerns into a
sequenced remediation program that lowers cognitive load first, then tightens
the broker and lifecycle seams, then reduces monolithic ownership in the
largest runtime files without destabilizing the durable spine.

This plan is intentionally narrower than the original review in two places:
the agent runtime already has a canonical spec, and process-title metacharacter
injection is already sanitized. The remediation work there is traceability and
regression protection, not first-time feature creation.

## 2. Source Documents

Source specs:
- `docs/specifications/01-Core_Components.md` [CC-1], [CC-2.2], [CC-2.4],
  [CC-2.5], [CC-3]
- `docs/specifications/02-TaskSpec.md` [TS-1], [TS-1.1], [TS-1.2], [TS-1.3],
  [TS-1.4]
- `docs/specifications/03-Manager_Architecture.md` [MA-1.4], [MA-1.5],
  [MA-1.6], [MA-2], [MA-3], [MA-4]
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0], [SB-0.1],
  [SB-0.3], [SB-0.4]
- `docs/specifications/07-System_Invariants.md` `QUEUE.1`-`QUEUE.6`,
  `OBS.1`-`OBS.8`, `IMPL.5`-`IMPL.6`, `MANAGER.1`-`MANAGER.7`
- `docs/specifications/08-Testing_Strategy.md` [TS-0], [TS-1], [TS-2]
- `docs/specifications/10-CLI_Interface.md` [CLI-1.1.1], [CLI-1.2],
  [CLI-1.3], [CLI-1.4], [CLI-6]
- `docs/specifications/13-Agent_Runtime.md` [AR-0.1], [AR-5], [AR-7], [AR-9]

Related current plans:
- `docs/plans/2026-04-14-weft-road-to-excellent-plan.md`
- `docs/plans/2026-04-15-docs-audit-and-alignment-plan.md`
- `docs/plans/2026-04-14-spawn-request-reconciliation-plan.md`
- `docs/plans/2026-04-13-detached-manager-bootstrap-hardening-plan.md`
- `docs/plans/2026-04-13-manager-bootstrap-readiness-and-cleanup-test-plan.md`

Local guidance:
- `AGENTS.md`
- `docs/agent-context/decision-hierarchy.md`
- `docs/agent-context/principles.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`

No current spec exists yet for:
- plan metadata and archive/status conventions under `docs/plans/`
- the `_UNAUDITED_PATH_PREFIXES` review policy in `tests/conftest.py`
- test-typechecking scope policy in `pyproject.toml`

Those repo-governance surfaces should be codified during the documentation and
testing slices below. Do not pretend they are already covered by a canonical
spec.

## 3. Context and Key Files

### Validated Current Findings

The following findings were validated against the current repo before writing
this plan:

- five core files still concentrate a large amount of ownership:
  `weft/core/taskspec.py`, `weft/core/tasks/base.py`, `weft/commands/run.py`,
  `weft/cli.py`, and `weft/core/manager.py`
- production code still contains 40 direct `Queue(...)` constructions
- production code still contains 19 `time.sleep(...)` calls across 16 files
- `_UNAUDITED_PATH_PREFIXES` currently excludes 44 of 90 test files by path
- `pyproject.toml` still excludes all `tests/` from the default mypy run
- plan sprawl is real: 55 plans, no shared index, no per-file status field,
  and almost no supersession markers

The following review claims were materially overstated:

- the agent runtime is not unspec'd; `docs/specifications/13-Agent_Runtime.md`
  already exists and many modules cite it
- process-title shell metacharacters are already stripped in
  `BaseTask._format_process_title`; the risk is drift or regression, not an
  active injection bug

### Current Structure To Reuse

Shared broker seam that already exists:
- `weft/context.py` :: `WeftContext.queue()` is the current context-backed
  queue constructor
- `weft/core/tasks/base.py` :: `BaseTask._queue()` is the current task-owned
  queue cache and stop-event integration point

Large files with mixed ownership:
- `weft/core/manager.py` currently owns registry heartbeat, leadership,
  autostart scanning, child launch, idle timeout, and drain
- `weft/core/tasks/base.py` currently owns queue wiring, control handling,
  process titles, state reporting, and large-output spillover helpers
- `weft/commands/run.py` currently mixes CLI parsing, TaskSpec shaping,
  submission, waiting, and interactive handling
- `weft/cli.py` currently carries a large amount of command registration and
  help-surface wiring
- `weft/core/taskspec.py` currently mixes schema models, frozen container
  helpers, and TaskSpec shaping utilities

Files to read before implementing any slice:
- `weft/context.py`
- `weft/core/tasks/base.py`
- `weft/core/manager.py`
- `weft/commands/run.py`
- `tests/conftest.py`
- `pyproject.toml`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/04-SimpleBroker_Integration.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/08-Testing_Strategy.md`

Comprehension questions for implementers:
1. Which queue-construction paths already exist today, and why would adding a
   second unrelated factory likely make the backend seam worse rather than
   better?
2. Which manager-registry behaviors are current contract, and which cleanup
   paths are explicitly best-effort?
3. Which `time.sleep(...)` loops are waiting on an external process or broker
   condition, and which ones can be replaced by existing queue/control events?
4. Which large files are load-bearing public or runtime boundaries, and which
   ones can be split by extracting leaf helpers without changing the contract?

## 4. Invariants and Constraints

The remediation program must preserve these invariants:

- TID generation, format, and immutability remain unchanged (`IMMUT.3`,
  `MANAGER.2`, [MA-2])
- state transitions remain forward-only and terminal states do not roll back
  (`STATE.1`-`STATE.5`)
- queue names and reserve/claim semantics remain unchanged (`QUEUE.1`-`QUEUE.6`,
  [TS-1.1], [SB-0.3])
- the durable spine remains the only execution model:
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log`
- process-title sanitization remains intact (`OBS.4`-`OBS.8`)
- child broker-connected code must continue to recreate state under spawn-style
  process boundaries (`IMPL.5`, `IMPL.6`)
- no public CLI shape change is allowed unless the same slice updates the
  owning spec and CLI tests
- no new dependency is allowed unless a slice states why the current stdlib and
  repo helpers are insufficient

Review gates:

- no big-bang rewrite of `Manager`, `BaseTask`, `run.py`, `cli.py`, or
  `taskspec.py`
- no second queue-construction path when an existing seam can be reused
- no backend swap as part of this program
- no new scheduler, daemon, or control plane
- no mock-heavy substitute for broker-backed lifecycle proofs when
  `WeftTestHarness` or the existing CLI tests can exercise the real path

Out of scope for this plan:

- replacing Typer or redesigning the CLI surface for aesthetic reasons
- rewriting the TaskSpec schema layout or persisted payload format
- forcing every polling loop to zero sleeps even when the external boundary is
  inherently poll-based
- adding provider account management or product-layer agent features

## 5. Review And Rollout Strategy

This work is boundary-crossing. Independent review is required:

1. after this plan is written
2. after the plan/doc-governance slice
3. after the queue-seam slice
4. after the lifecycle/timing slice
5. again before the final residual-hygiene slice is closed

Prefer a different agent family than the author when one is available. If only
the same family is available, record that limitation in the review notes.

Rollout order matters:

1. land documentation and plan-governance rules first
2. establish and enforce the queue seam second
3. extract `Manager` and `BaseTask` leaf ownership after the seam exists
4. then reduce `run.py` / `cli.py` / `taskspec.py` mixed ownership
5. only after those seams stabilize, tighten polling, typing, catches, and
   constant placement

Rollback principle:

- each slice must remain independently revertible
- do not mix mechanical code motion with behavior changes unless the behavior
  change is the point of the slice
- do not combine queue-seam refactors with queue-name or payload changes

## 6. Tasks

1. Add plan-corpus governance and indexing.
   - Outcome:
     - every plan becomes discoverable and classifiable without opening dozens
       of files
     - status and supersession become explicit
   - Files to touch:
     - `docs/plans/README.md` (new)
     - `docs/specifications/09-Implementation_Plan.md`
     - `docs/specifications/README.md`
     - plan files under `docs/plans/`
   - Read first:
     - `docs/plans/2026-04-15-docs-audit-and-alignment-plan.md`
     - `docs/specifications/README.md`
     - `docs/agent-context/runbooks/writing-plans.md`
   - Required action:
     - create a single plan index with status taxonomy, ownership rules, and
       supersession conventions
     - add a small standardized metadata block near the top of each plan with
       at least `Status`, `Source specs`, and `Superseded by` when relevant
     - do not add YAML frontmatter unless a real parser or automation needs it;
       solve discoverability first, not machine-readability for its own sake
     - add a lightweight validation check so new plans cannot land without the
       required metadata
   - Shared path to reuse:
     - existing plan naming convention in `docs/plans/`
   - Tests to add or update:
     - a small docs/spec test that validates required plan metadata and index
       presence
   - Stop-and-re-evaluate gate:
     - if bulk-editing old plans requires semantic decisions that cannot be
       made safely in one pass, land the index plus validator first and track
       the remaining historical migrations explicitly
   - Done when:
     - a zero-context engineer can answer which plans are current, landed,
       superseded, or abandoned without grep-driven archaeology

2. Make queue construction a real enforced seam.
   - Outcome:
     - context-backed code and task-owned code both use named queue-construction
       seams instead of ad hoc `Queue(...)` usage
   - Files to touch:
     - `weft/context.py`
     - `weft/core/tasks/base.py`
     - `weft/commands/_manager_bootstrap.py`
     - `weft/commands/run.py`
     - `weft/commands/result.py`
     - `weft/commands/tasks.py`
     - `weft/commands/status.py`
     - `weft/core/manager.py`
     - `weft/core/spawn_requests.py`
     - `weft/core/resource_monitor.py`
     - `weft/core/tasks/multiqueue_watcher.py`
     - related tests in `tests/commands/`, `tests/core/`, and `tests/context/`
   - Read first:
     - `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.1], [SB-0.4]
     - `weft/context.py`
     - `weft/core/tasks/base.py`
     - `weft/commands/_manager_bootstrap.py`
   - Required action:
     - treat `WeftContext.queue()` and `BaseTask._queue()` as the preferred
       current seams
     - remove direct `Queue(...)` construction from higher-level production
       modules that already have access to a context or task wrapper
     - explicitly document the allowed low-level exceptions where direct queue
       construction remains the boundary implementation itself
     - add a guard test or repository check that blocks new production
       callsites outside the allowlist
   - Shared path to reuse:
     - `WeftContext.queue()`
     - `BaseTask._queue()`
   - Tests to add or update:
     - command-level tests for `run`, `result`, `status`, and task control
     - context and manager tests that prove queue creation still honors the
       resolved broker target
   - Stop-and-re-evaluate gate:
     - if a callsite lacks the right object to reach the existing seam, do not
       thread context through unrelated public APIs in the same slice; add the
       smallest wrapper helper first and continue from there
   - Done when:
     - backend-sensitive queue ownership is obvious and new backend work does
       not require grep-driven edits across command modules

3. Extract `Manager` leaf ownership before touching larger runtime behavior.
   - Outcome:
     - registry, leadership, and autostart logic stop competing for the same
       class body
   - Files to touch:
     - `weft/core/manager.py`
     - new helper modules such as `weft/core/manager_registry.py` and
       `weft/core/manager_autostart.py`
     - `docs/specifications/03-Manager_Architecture.md`
     - `tests/core/test_manager.py`
     - `tests/commands/test_run.py`
   - Read first:
     - `docs/specifications/03-Manager_Architecture.md` [MA-1.4], [MA-1.5],
       [MA-1.6], [MA-3], [MA-4]
     - `docs/specifications/07-System_Invariants.md` `MANAGER.1`-`MANAGER.7`
     - `weft/core/manager.py`
   - Required action:
     - extract registry record shaping, publish/prune, and canonical-manager
       selection into a dedicated helper module with behavior-preserving tests
     - extract autostart manifest loading, state tracking, and payload shaping
       into a dedicated helper module
     - keep `Manager` as the public runtime owner; do not replace it with a
       new abstraction tree
     - update nearby spec implementation mappings in the same slice
   - Shared path to reuse:
     - current manager bootstrap and registry semantics in
       `weft/commands/_manager_bootstrap.py`
   - Tests to add or update:
     - real registry and bootstrap tests, including stale/zombie manager record
       behavior
   - Stop-and-re-evaluate gate:
     - if the extracted helper needs most of `Manager` passed into every call,
       the seam is too broad; narrow the extraction to pure payload or state
       helpers first
   - Done when:
     - registry/leadership and autostart can be reasoned about without paging
       through child-launch and drain logic

4. Extract `BaseTask` leaf ownership, then split `run.py` and `cli.py` only
   where ownership is already clear.
   - Outcome:
     - large-file reduction happens by explicit ownership boundaries rather than
       by arbitrary file chopping
   - Files to touch:
     - `weft/core/tasks/base.py`
     - new helper modules such as `weft/core/tasks/observability.py` and
       `weft/core/tasks/output_spill.py`
     - `weft/commands/run.py`
     - `weft/commands/_spawn_submission.py`
     - `weft/commands/_result_wait.py`
     - `weft/cli.py`
     - optionally `weft/core/taskspec.py` only for leaf-helper extraction
     - related tests in `tests/tasks/`, `tests/cli/`, and `tests/taskspec/`
   - Read first:
     - `docs/specifications/01-Core_Components.md` [CC-2.2], [CC-2.4],
       [CC-2.5]
     - `docs/specifications/10-CLI_Interface.md` [CLI-1.1.1], [CLI-1.4]
     - `weft/core/tasks/base.py`
     - `weft/commands/run.py`
     - `weft/cli.py`
   - Required action:
     - extract process-title formatting, state-log payload shaping, and large
       output spill/reference helpers from `BaseTask` into sibling modules with
       stable tests
     - move more submission and wait logic from `run.py` into the already
       existing helper modules before creating new namespaces
     - reduce `cli.py` registration sprawl only through shared tables or
       helper registration, not by changing command behavior
     - treat `taskspec.py` as last in this slice; only extract frozen container
       helpers or shaping helpers if that can be done without schema changes
   - Shared path to reuse:
     - `weft/commands/_spawn_submission.py`
     - `weft/commands/_result_wait.py`
     - current TaskSpec tests as the schema truth surface
   - Tests to add or update:
     - task observability tests
     - CLI help and command wiring tests
     - TaskSpec immutability and shaping tests
   - Stop-and-re-evaluate gate:
     - if the `taskspec.py` split starts changing schema ownership or serialized
       payloads, stop and defer that part; line count alone is not justification
   - Done when:
     - file size goes down through obvious ownership seams, and the public
       CLI/runtime contract does not move

5. Replace unjustified polling with owned wait semantics and document timeout
   composition.
   - Outcome:
     - each production `time.sleep(...)` is either replaced by a stronger event
       path or explicitly documented as the right boundary wait
     - the current termination timeouts have one documented strategy rather than
       scattered magic
   - Files to touch:
     - `weft/_constants.py`
     - `weft/helpers.py`
     - `weft/commands/_manager_bootstrap.py`
     - `weft/commands/_result_wait.py`
     - `weft/commands/result.py`
     - `weft/commands/tasks.py`
     - `weft/commands/queue.py`
     - `weft/core/manager.py`
     - `weft/core/launcher.py`
     - `weft/core/tasks/base.py`
     - `weft/core/tasks/interactive.py`
     - `weft/core/runners/subprocess_runner.py`
     - `extensions/weft_docker/weft_docker/agent_runner.py`
     - `extensions/weft_docker/weft_docker/plugin.py`
     - relevant lifecycle tests
   - Read first:
     - `docs/specifications/03-Manager_Architecture.md` [MA-1.4], [MA-1.5],
       [MA-3]
     - `docs/specifications/07-System_Invariants.md` `MANAGER.3`-`MANAGER.7`,
       `OBS.1`-`OBS.3`
     - current timeout constants in `weft/_constants.py`
   - Required action:
     - inventory each sleep by owner and reason: external-process polling,
       broker backoff, user-facing refresh, or missing event
     - replace only the loops that already have a durable signal available
     - centralize the remaining poll intervals and timeout-composition comments
       in `_constants.py`
     - add an end-to-end test for abrupt manager death or stale-registry
       cleanup so lifecycle recovery is proven beyond best-effort `atexit`
   - Shared path to reuse:
     - existing registry and PID-liveness helpers
   - Tests to add or update:
     - manager bootstrap/recovery tests
     - task stop/kill wait tests
     - long-session cleanup and result-wait tests
   - Stop-and-re-evaluate gate:
     - if a loop is genuinely waiting on an external process or broker API with
       no event surface, keep it and document why; do not build a fake event
       mechanism just to remove `sleep`
   - Done when:
     - the remaining polling points are few, named, and justified, and timeout
       math is understandable from one file

6. Tighten test-governance and test typing in stages.
   - Outcome:
     - audit exclusions shrink, and typed-test coverage grows without a single
       giant cleanup branch
   - Files to touch:
     - `tests/conftest.py`
     - `pyproject.toml`
     - `docs/specifications/08-Testing_Strategy.md`
     - `tests/helpers/weft_harness.py`
     - the highest-value currently excluded test packages
   - Read first:
     - `docs/specifications/08-Testing_Strategy.md` [TS-0], [TS-1], [TS-2]
     - `tests/conftest.py`
     - `pyproject.toml`
   - Required action:
     - replace the broad `_UNAUDITED_PATH_PREFIXES` set with a smaller explicit
       allowlist that includes a reason and exit criteria for each remaining
       exclusion
     - remove the blanket `(^tests/)` mypy exclusion in stages, starting with
       `tests/helpers/` and the highest-churn test packages
     - add or update local commands so typed-test verification is practical
       during normal development
     - update the testing spec to describe the new audit and typing policy
   - Shared path to reuse:
     - `WeftTestHarness` for real lifecycle proofs
   - Tests to add or update:
     - meta-tests or config checks for audit exclusions
     - mypy verification for the newly included test targets
   - Stop-and-re-evaluate gate:
     - if full-test mypy creates unbounded churn, land a staged include list
       with explicit remaining debt; do not keep the blanket exclusion once the
       first staged targets are viable
   - Done when:
     - test-review coverage and typed-test coverage both move from hidden global
       exemptions to explicit tracked debt

7. Finish with residual hygiene only after the seams above have landed.
   - Outcome:
     - broad catches, stray constants, and traceability drift are cleaned up
       once the larger ownership problems are no longer moving
   - Files to touch:
     - `weft/core/manager.py`
     - `weft/core/tasks/base.py`
     - `weft/core/tasks/consumer.py`
     - `weft/_constants.py`
     - `weft/core/agents/`
     - owning specs touched by extracted modules
   - Read first:
     - `docs/specifications/07-System_Invariants.md`
     - `docs/specifications/13-Agent_Runtime.md` [AR-5], [AR-7], [AR-9]
   - Required action:
     - review each broad `except Exception` and classify it as required
       best-effort handling, boundary guard, or bug-masking catch
     - narrow catches where the failure should be visible
     - move lingering constants out of large modules and into `_constants.py`
     - audit module docstrings and implementation mappings so extracted modules
       point back to the governing spec
     - keep a regression test for process-title sanitization rather than adding
       a redundant second escaping layer
   - Tests to add or update:
     - focused tests for process-title sanitization and any narrowed error paths
   - Stop-and-re-evaluate gate:
     - do not start this slice while the queue, manager, or task ownership
       moves are still active; otherwise catches and constants will move twice
   - Done when:
     - the remaining hygiene debt is explicit, small, and tied to stable module
       ownership

## 7. Verification

Use per-slice verification, then rerun the shared regression set:

- `./.venv/bin/ruff check weft extensions tests`
- `bin/mypy-check`
- staged mypy runs for newly included test targets
- `./.venv/bin/python -m pytest -n0 tests/commands/test_run.py`
- `./.venv/bin/python -m pytest -n0 tests/commands/test_status.py`
- `./.venv/bin/python -m pytest -n0 tests/core/test_manager.py`
- `./.venv/bin/python -m pytest -n0 tests/tasks/test_task_observability.py`
- `./.venv/bin/python -m pytest -n0 tests/test_harness_registration.py`
- `./.venv/bin/python -m pytest -n0 -m slow tests/cli/test_cli_long_session.py::test_cli_long_session_produces_identical_transcript_across_backends`
- `bin/pytest-pg --all` after queue-seam and lifecycle slices

Documentation slices should also include:

- validation that every plan has the required metadata block
- validation that the plan index is synchronized

## 8. Rollback

Rollback by slice, not by a single all-or-nothing revert:

- the plan-governance slice can be reverted independently because it is docs and
  validation only
- queue-seam and ownership-extraction slices must remain behavior-preserving so
  they can be reverted without touching queue names, TIDs, or payload shapes
- lifecycle/timing changes must keep old and new wait behavior observably
  compatible until the stronger proof is in place
- if a later slice destabilizes the runtime, revert that slice and keep the
  earlier plan-governance and seam-documentation work

If rollback requires undoing a queue contract, public CLI shape, or persisted
payload change, the slice was too broad and should be split before landing.

# Autostart Hardening And Contract Alignment Plan

Status: completed
Source specs: see Source Documents below
Superseded by: none

## 1. Goal

Tighten Weft's autostart behavior so operator intent is durable at the project
level, manager-side autostart bookkeeping reflects real queue outcomes, and the
documented manifest policy matches the implemented contract. This slice must
not change the deliberate bootstrap rule that an already-running canonical
manager is adopted as-is; `weft run --no-autostart` must not try to reconfigure
that live manager.

## 2. Source Documents

Source specs:

- [`docs/specifications/00-Overview_and_Architecture.md`](../specifications/00-Overview_and_Architecture.md)
- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md) [TS-1.2]
- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md) [MA-1.6], [MA-3]
- [`docs/specifications/04-SimpleBroker_Integration.md`](../specifications/04-SimpleBroker_Integration.md) [SB-0], [SB-0.4]
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md) [MF-6], [MF-7]
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md) [MANAGER.5], [MANAGER.6]
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md) [CLI-1.1], [CLI-1.1.1]

Repo guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/runtime-and-context-patterns.md`](../agent-context/runbooks/runtime-and-context-patterns.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)

Related plans:

- [`docs/plans/2026-04-14-config-precedence-and-parsing-alignment-plan.md`](./2026-04-14-config-precedence-and-parsing-alignment-plan.md)
- [`docs/plans/2026-04-14-spawn-request-reconciliation-plan.md`](./2026-04-14-spawn-request-reconciliation-plan.md)

## 3. Context and Key Files

Files to modify:

- `weft/context.py`
- `weft/commands/init.py`
- `weft/core/manager.py`
- `tests/context/test_context.py`
- `tests/cli/test_cli_init.py`
- `tests/core/test_manager.py`
- `docs/specifications/02-TaskSpec.md`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/04-SimpleBroker_Integration.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/10-CLI_Interface.md`

Read first:

- `weft/context.py`
- `weft/commands/init.py`
- `weft/core/manager.py`
- `tests/context/test_context.py`
- `tests/cli/test_cli_init.py`
- `tests/core/test_manager.py`

Shared paths and helpers to reuse:

- `build_context()` and `WeftContext` in `weft/context.py`
- `_load_project_config()` in `weft/context.py`
- `Manager._tick_autostart()`, `Manager._active_autostart_sources()`,
  `Manager._enqueue_autostart_request()`, and `Manager._cleanup_children()`
- the existing manager bootstrap path in
  `weft/commands/_manager_bootstrap.py`

Current structure:

- `build_context()` decides `autostart_enabled` before it loads
  `.weft/config.json`, so project metadata cannot currently affect the
  autostart decision.
- `cmd_init(..., autostart=False)` only passes a transient override into
  `build_context()`. It does not persist a project-level autostart setting.
- the manager scans `.weft/autostart/*.json` on startup and then once per scan
  interval, building an autostart payload from stored task specs under
  `.weft/tasks/`.
- `_enqueue_autostart_request()` logs write failures but does not tell the
  caller whether the enqueue actually succeeded.
- `_tick_autostart()` currently marks a manifest as launched and advances
  ensure-state even when the enqueue helper may have failed.
- `ensure` policy behavior is only partially locked down by tests today. The
  docs say `max_restarts` and `backoff_seconds` are accepted but not enforced,
  while the code already applies both.

Comprehension checks:

1. Which function currently decides `autostart_enabled`, and at what point in
   that function is `.weft/config.json` loaded?
2. Which existing manager behavior is intentionally out of scope for this
   change?
3. Which helper currently records autostart state as launched, and what proof
   does it have today that a spawn request really reached the queue?

## 4. Invariants and Constraints

- Keep one durable execution spine:
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log`.
- Autostarted work must continue to enter through the ordinary manager spawn
  queue path. Do not create a side channel, direct child launch shortcut, or a
  second manager-owned scheduler path.
- Preserve TID generation, TID immutability, forward-only task-state
  transitions, and canonical manager election semantics.
- `weft run --autostart/--no-autostart` remains a per-invocation context
  override. It must not become a hidden project mutation.
- Adopting an already-live canonical manager is correct by design. Do not add
  logic that tries to reconfigure that manager from a later `weft run` flag.
- `.weft/config.json` may become the source for one project-local autostart
  default, but it must not become a new broker-target source or a generic
  project-settings system in this slice.
- Do not add pipeline autostart in this plan.
- Do not add a new CLI command or a new long-lived background service.
- Keep tests broker-backed and manager-real where practical. Do not mock away
  queue writes, manager lifecycle, or autostart child cleanup unless the test
  is explicitly about a narrow internal helper seam.

Hidden couplings:

- `cmd_init()`, `build_context()`, `weft manager start`, and `weft manager serve`
  all depend on the same context-construction path.
- manager-side autostart state is currently split across the manifest scan
  loop, `_autostart_launched`, `_autostart_state`, child-process bookkeeping,
  and task-log-derived active-source reconstruction.
- `ensure` policy semantics are both user-facing and timing-sensitive: launch
  count, restart count, and backoff all interact.
- abrupt child death without a clean terminal event may leave task-log-derived
  "active" state behind. That risk is plausible from the code, but it is not
  yet proven by a regression test.

Out of scope:

- reconfiguring a live canonical manager from `weft run --autostart` or
  `--no-autostart`
- pipeline-target autostart
- adding `--autostart` flags to `weft manager start` or `weft manager serve`
  in this slice
- a broader `.weft/config.json` redesign
- manager orphan adoption or cross-manager child supervision redesign

## 5. Review And Rollout Strategy

This work crosses context resolution, CLI initialization, manager runtime, and
spec documentation. Independent review is required after the implementation
slice and again after the spec text is updated.

Rollout order matters:

1. lock the intended behavior with focused tests
2. implement project-level autostart persistence and precedence
3. fix manager-side enqueue bookkeeping and policy counters
4. prove or falsify the abrupt-child-death gap
5. update spec text to match the landed behavior

Rollback principle:

- the project-config slice must remain independently revertible from the
  manager-runtime slice
- documentation updates must land with the behavior they describe
- if abrupt-child-death hardening grows into orphan adoption or cross-manager
  supervision, stop and write a separate plan

## 6. Tasks

1. Lock the intended contract with focused tests first.
   - Outcome:
     - the plan's target behavior is expressed in small failing tests before
       implementation starts
   - Files to touch:
     - `tests/context/test_context.py`
     - `tests/cli/test_cli_init.py`
     - `tests/core/test_manager.py`
   - Read first:
     - `weft/context.py`
     - `weft/commands/init.py`
     - `weft/core/manager.py`
   - Required action:
     - add a context/init test that proves `cmd_init(..., autostart=False)`
       persists a project default that a later plain `build_context()` reads
     - add a precedence test that explicit `build_context(..., autostart=...)`
       still overrides the project default
     - add manager tests showing a failed autostart enqueue does not mark the
       source as launched and does not advance ensure counters
     - add policy tests that pin the intended `max_restarts` and non-zero
       `backoff_seconds` semantics
   - Shared path to reuse:
     - existing context tests in `tests/context/test_context.py`
     - existing `cmd_init` tests in `tests/cli/test_cli_init.py`
     - existing autostart manager tests in `tests/core/test_manager.py`
   - Stop-and-re-evaluate gate:
     - if the tests force a generic config framework or a second autostart
       runtime path, stop and narrow the test surface
   - Done when:
     - the desired persistence, enqueue-state, and policy semantics are all
       pinned by targeted tests

2. Make project-level autostart intent durable without broadening config scope.
   - Outcome:
     - a project can opt in or out of autostart durably through `.weft/config.json`
       and all ordinary context-build paths see that same default
   - Files to touch:
     - `weft/context.py`
     - `weft/commands/init.py`
     - `tests/context/test_context.py`
     - `tests/cli/test_cli_init.py`
   - Read first:
     - `docs/specifications/04-SimpleBroker_Integration.md` [SB-0], [SB-0.4]
     - `weft/context.py`
     - `weft/commands/init.py`
   - Required action:
     - load project metadata early enough for `build_context()` to derive the
       autostart default before directory creation
     - keep precedence explicit and small:
       explicit `autostart=` argument > project-local autostart setting >
       env/global default
     - persist the `weft init --autostart/--no-autostart` choice into
       `.weft/config.json`
     - keep `.weft/config.json` limited to Weft-owned project metadata plus
       this one autostart setting; do not turn it into a generic configuration
       merge layer
   - Shared path to reuse:
     - `_load_project_config()` for config file creation and parsing
     - `build_context()` as the only context-resolution entry point
   - Stop-and-re-evaluate gate:
     - if the implementation starts threading project config into broker-target
       resolution or into unrelated `WEFT_*` settings, stop
   - Done when:
     - `weft init`, `weft manager start`, `weft manager serve`, and plain
       `build_context()` all agree on the same durable project default unless
       the caller passes an explicit override

3. Fix manager-side autostart bookkeeping so state changes follow real queue outcomes.
   - Outcome:
     - `once` and `ensure` manifests only advance after a spawn request is
       actually written
   - Files to touch:
     - `weft/core/manager.py`
     - `tests/core/test_manager.py`
   - Read first:
     - `docs/specifications/03-Manager_Architecture.md` [MA-1.6]
     - `docs/specifications/05-Message_Flow_and_State.md` [MF-6]
     - `weft/core/manager.py`
   - Required action:
     - make `_enqueue_autostart_request()` return a success signal or raise
       cleanly instead of only logging
     - move `_autostart_launched` updates and ensure counter/backoff updates so
       they happen only after a confirmed queue write
     - preserve the ordinary spawn-request queue path and payload shape
   - Shared path to reuse:
     - the existing spawn payload structure in `_enqueue_autostart_request()`
   - Stop-and-re-evaluate gate:
     - if the fix starts bypassing the queue or directly launching children,
       stop immediately
   - Done when:
     - a failed enqueue leaves the manifest eligible for a later retry and does
       not consume `once` or `ensure` state

4. Define and lock the manifest policy semantics instead of leaving them half-implemented.
   - Outcome:
     - `ensure` behavior has one explicit contract for launch counting and
       backoff timing
   - Files to touch:
     - `weft/core/manager.py`
     - `tests/core/test_manager.py`
     - `docs/specifications/02-TaskSpec.md`
     - `docs/specifications/03-Manager_Architecture.md`
   - Read first:
     - `docs/specifications/02-TaskSpec.md` [TS-1.2]
     - existing autostart policy code in `weft/core/manager.py`
   - Required action:
     - choose and document one clear meaning for `max_restarts`
       (recommended: count restarts after the initial launch, not total launches)
     - apply non-zero `backoff_seconds` only to relaunches, not the initial
       launch
     - update the implementation if it does not match the chosen contract
     - add direct tests for non-zero backoff and exact restart-limit behavior
   - Shared path to reuse:
     - `_autostart_state` and `_autostart_last_scan_ns`
   - Stop-and-re-evaluate gate:
     - if this starts requiring a general supervisor framework, per-manifest
       persistent state, or cross-manager adoption, stop and split the work
   - Done when:
     - docs and tests both describe the same `ensure` semantics and the code
       follows that contract

5. Prove or falsify the abrupt-child-death false-active gap before changing the tracking model.
   - Outcome:
     - the repo either gains a real regression test and fix for abrupt child
       death, or it gains a documented residual-risk note with no speculative
       redesign
   - Files to touch:
     - `tests/core/test_manager.py`
     - `weft/core/manager.py` only if the gap is reproduced
     - `docs/specifications/02-TaskSpec.md` and
       `docs/specifications/05-Message_Flow_and_State.md` only if behavior
       changes
   - Read first:
     - `weft/core/manager.py`
     - `tests/core/test_manager.py`
     - `docs/agent-context/runbooks/testing-patterns.md`
   - Required action:
     - add a real broker/process test that kills or otherwise abruptly removes
       an autostart child without relying on a clean terminal event
     - if the test proves that `_active_autostart_sources()` leaves a dead
       source falsely active, tighten the logic using real liveness evidence
       available on the current runtime path
     - if the test does not reproduce a correctness problem on the real path,
       do not redesign active-source tracking preemptively
   - Shared path to reuse:
     - `_cleanup_children()` and the existing child-process bookkeeping
   - Stop-and-re-evaluate gate:
     - if the only apparent fix is manager orphan adoption or cross-manager
       reconciliation, stop and write a separate supervision plan
   - Done when:
     - this ambiguity is resolved with real evidence instead of code-reading
       inference

6. Update the specs and CLI text to match the landed behavior.
   - Outcome:
     - the docs describe the real contract and carry backlinks to this plan
   - Files to touch:
     - `docs/specifications/02-TaskSpec.md`
     - `docs/specifications/03-Manager_Architecture.md`
     - `docs/specifications/04-SimpleBroker_Integration.md`
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/10-CLI_Interface.md`
   - Read first:
     - the implemented code and tests from Tasks 2-5
   - Required action:
     - document project-level autostart precedence and the limited role of
       `.weft/config.json`
     - state plainly that `weft run --autostart/--no-autostart` is a
       submission-time/context override and does not reconfigure an already-live
       manager
     - replace the current stale `max_restarts` / `backoff_seconds` wording
       with the actual enforced contract
     - add this plan to the touched spec backlink lists
   - Stop-and-re-evaluate gate:
     - if the final doc wording no longer matches the tests or the
       implementation, stop and reconcile before merging
   - Done when:
     - a zero-context engineer can infer the intended autostart behavior from
       the specs without reverse-engineering the code

## 7. Testing Plan

- `./.venv/bin/python -m pytest tests/context/test_context.py -k autostart -q -n 0`
- `./.venv/bin/python -m pytest tests/cli/test_cli_init.py -k autostart -q -n 0`
- `./.venv/bin/python -m pytest tests/core/test_manager.py -k autostart -q -n 0`
- `./.venv/bin/python -m pytest tests/context/test_context.py tests/cli/test_cli_init.py tests/core/test_manager.py -q -n 0`

## 8. Rollback

If the project-config persistence slice proves problematic, revert the
`build_context()` and `cmd_init()` changes together and keep the manager-runtime
fixes separate. If the abrupt-child-death probe shows the issue is broader than
current manager-owned state, revert any partial tracking rewrite and keep only
the added regression test or residual-risk documentation.

# Manager Stop Timeout Hardening Plan

Status: completed
Source specs: docs/specifications/03-Manager_Architecture.md [MA-3]
Superseded by: none

## 1. Goal

Make manager stop waits robust under slower broker backends and xdist load by
separating the manager's internal child-drain budget from the caller's external
stop-confirmation budget. Test teardown should use forceful cleanup semantics,
while normal graceful-stop behavior should get enough time to observe the
manager's stopped registry record after the drain window expires.

## 2. Source Documents

- `docs/specifications/03-Manager_Architecture.md` [MA-3] defines manager
  STOP/SIGTERM drain behavior, bounded child reaping, and shared lifecycle
  ownership in `weft/core/manager_runtime.py`.
- `docs/agent-context/runbooks/testing-patterns.md` requires real
  broker/process proof for lifecycle behavior and warns against xdist cleanup
  flakes.
- `docs/agent-context/runbooks/runtime-and-context-patterns.md` documents that
  manager registry state is append-only runtime state and must be interpreted
  through the shared lifecycle helpers.
- `docs/lessons.md` records repeated PG parity and lifecycle timing failures.

## 3. Context and Key Files

Files to modify:

- `weft/_constants.py`: add the external stop-confirmation timeout constant.
- `weft/core/manager_runtime.py`: use the new timeout as the shared lifecycle
  default.
- `weft/commands/manager.py`: use the shared default in command-layer wrappers.
- `weft/cli/app.py`: expose the same default through `weft manager stop`.
- `weft/client/_namespaces.py`: expose the same default through the public
  Python client manager namespace.
- `tests/commands/test_manager_commands.py`: lock timeout delegation and force
  cleanup behavior.
- `tests/commands/test_run.py`: keep active-manager teardown forceful with an
  explicit cleanup timeout.
- `docs/specifications/03-Manager_Architecture.md`: backlink this plan and
  document the timeout separation.
- `docs/plans/README.md`: add the plan row.
- `docs/lessons.md`: record the lifecycle timeout rule if the implementation
  confirms the repeated CI failure mode.

Current structure:

- `Manager._continue_shutdown_drain()` waits for tracked children, then
  forcefully terminates them only after
  `MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS`.
- `manager_runtime._stop_manager()` sends STOP, waits for the registry to show
  stopped or PID absence, then optionally escalates with `force=True`.
- Existing CLI and command defaults use a 5-second stop timeout, equal to the
  manager's internal drain budget. That leaves no observation margin for
  backend latency or scheduler delay.

Comprehension checks before editing:

- Confirm that task completion and manager child reaping are separate clocks.
- Confirm that `force=True` is appropriate for test cleanup but not for tests
  whose purpose is graceful STOP semantics.

## 4. Invariants and Constraints

- Do not change queue names, task TID semantics, TaskSpec immutability, or
  manager registry payload shape.
- Do not introduce a second manager stop path. All CLI and command surfaces
  must keep delegating to `weft/core/manager_runtime.py`.
- Do not shorten the manager's internal drain timeout to make tests pass.
- Do not mock broker queues or process lifecycle for the end-to-end cleanup
  proof. Unit mocks are acceptable only for timeout delegation arithmetic and
  command wrapper behavior.
- `weft.state.managers` remains runtime-only append-only state.

## 5. Tasks

1. Add `MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS`.
   - Files: `weft/_constants.py`
   - Set it strictly greater than `MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS`.
   - Document that it is the caller-side observation budget, not the manager's
     internal child-drain budget.
   - Stop if the change starts making drain behavior configurable in this
     slice.

2. Wire shared defaults through manager stop surfaces.
   - Files: `weft/core/manager_runtime.py`, `weft/commands/manager.py`,
     `weft/client/_namespaces.py`, `weft/cli/app.py`
   - Reuse the shared lifecycle helper.
   - Keep explicit caller-provided timeouts authoritative.
   - Stop if a second stop implementation appears in CLI or command code.

3. Keep teardown forceful and explicit.
   - Files: `tests/commands/test_run.py`,
     `tests/commands/test_manager_commands.py`
   - Preserve forceful cleanup helpers for tests that create background
     managers but are not asserting graceful manager stop.
   - Add or update tests proving the default command-layer timeout is larger
     than the manager drain budget and that explicit timeout values still pass
     through.

4. Update traceability docs.
   - Files: `docs/specifications/03-Manager_Architecture.md`,
     `docs/plans/README.md`, `docs/lessons.md`
   - Add a plan backlink in the spec and a short implementation note about
     internal drain versus external observation timeout.
   - Mark this plan completed after implementation and verification.

## 6. Testing Plan

- Use `pytest` command tests for wrapper/default behavior:
  `uv run pytest tests/commands/test_manager_commands.py -q`.
- Use the previously flaky targeted run test:
  `uv run pytest tests/commands/test_run.py::test_run_inline_no_wait_succeeds_when_post_proof_acknowledgement_fails -q`.
- Use Postgres proof for the reported failure shape:
  `uv run --extra dev --extra docker --extra macos-sandbox bin/pytest-pg tests/commands/test_run.py::test_run_inline_no_wait_succeeds_when_post_proof_acknowledgement_fails`.
- Run static checks for touched Python:
  `uv run ruff check weft tests/commands/test_manager_commands.py tests/commands/test_run.py`
  and `uv run mypy weft extensions/weft_docker extensions/weft_macos_sandbox`
  if time permits.

## 7. Verification and Gates

Done means:

- the shared default manager stop timeout is strictly greater than the internal
  manager drain timeout;
- CLI and command wrappers use that shared default;
- explicit timeout overrides still work;
- teardown helpers use forceful cleanup where their purpose is isolation;
- spec, plan index, and lessons are synchronized.

## 8. Independent Review Loop

This is boundary-crossing lifecycle work, so independent review would normally
be required. In this Codex turn no external reviewer was requested by the user;
perform a final self-review against this plan and the touched diff, and record
any residual risk in the final handoff.

## 9. Out of Scope

- Changing manager registry payload shape or adding child-count diagnostics.
- Reworking force escalation into a separate graceful-timeout API.
- Changing SimpleBroker backend behavior.
- Shortening manager child-drain semantics.

## 10. Fresh-Eyes Review

Before closing the slice, re-read the diff as a new engineer and confirm the
names make the two clocks obvious: manager internal drain versus caller
confirmation wait.

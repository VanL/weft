# Manager Bootstrap Readiness And Cleanup Test Plan

## Goal

Replace the fixed detached-bootstrap startup stability delay with a cheaper
readiness proof that still stays on the canonical manager bootstrap path, then
split the current SQLite preserve-cleanup stress test so startup-race coverage
and preserve-cleanup integrity coverage are measured independently.

## Source Documents

Source specs:

- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md) [MA-3]
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md) [MF-7]
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md) [MANAGER.6]
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md) [CLI-1.1.1], [CLI-1.1.2]

Repo guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/testing-patterns.md`](../agent-context/runbooks/testing-patterns.md)

Related plans:

- [`docs/plans/detached-manager-bootstrap-hardening-plan.md`](./detached-manager-bootstrap-hardening-plan.md)
- [`docs/plans/task-lifecycle-stop-drain-findings.md`](./task-lifecycle-stop-drain-findings.md)

## Context and Key Files

Files to modify:

- `weft/commands/_manager_bootstrap.py`
- `tests/cli/test_cli_run.py`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/10-CLI_Interface.md`

Read first:

- `weft/commands/_manager_bootstrap.py`
- `weft/core/manager.py`
- `tests/cli/test_cli_run.py`
- `tests/helpers/weft_harness.py`

Shared paths and helpers to reuse:

- `_launch_detached_manager()`, `_select_active_manager()`, `_manager_record()`,
  `_acknowledge_manager_launch_success()`, `_communicate_launcher()`
- `Manager._register_manager()` in `weft/core/manager.py`
- `WeftTestHarness.cleanup(preserve_database=True)`

Current structure:

- `_start_manager()` launches a detached wrapper, then waits for the launched
  PID plus canonical registry record to remain stable through a fixed
  `_MANAGER_STARTUP_STABILITY_WINDOW`.
- The current CLI test loops that path 20 times and also asserts SQLite
  integrity before and after preserve cleanup, so one test currently covers two
  different contracts.

Comprehension checks:

1. Which helper currently decides detached bootstrap success for both
   `weft run` and `weft manager start`?
2. Which record proves canonical manager ownership today?
3. Which part of the current stress test is startup-cost sensitive, and which
   part is preserve-cleanup specific?

## Invariants and Constraints

- Keep one durable bootstrap path:
  `_start_manager()` -> `weft.manager_detached_launcher` ->
  `weft.manager_process` -> `run_manager_process()` -> `Manager`.
- Preserve canonical manager election semantics and registry shape.
- Preserve TID format, pid liveness checks, and forward-only state transitions.
- Do not add a second readiness channel or a new long-lived bootstrap service.
- Do not change public CLI flags or output shape.
- Keep preserve-cleanup assertions on real broker-backed behavior.
- Do not replace the real CLI/bootstrap path with mocks.

Hidden couplings:

- `weft run` and `weft manager start` both depend on `_ensure_manager()`.
- Manager readiness is inferred from launcher state, pid liveness, and the
  canonical registry record.
- `test_weft_harness_cleanup_preserves_sqlite_integrity_for_parallel_manager_reuse`
  currently conflates manager bootstrap cost with preserve-cleanup integrity.

Out of scope:

- manager registry schema redesign
- leadership election redesign
- permanent manager stderr logging
- preserve-cleanup behavior redesign

## Tasks

1. Update the documented bootstrap contract.
   - Files to touch:
     - `docs/specifications/03-Manager_Architecture.md`
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/10-CLI_Interface.md`
   - Replace the fixed "bounded startup-stability window" language with a
     cheaper but still strong readiness rule:
     launched pid is live, the canonical registry record for the requested
     manager TID exists with that same pid, and the detached launcher itself
     has not reported early child exit before the caller acknowledges success.
   - Add this plan as a backlink in the touched spec section lists.
   - Stop if the wording starts implying a new queue, new CLI flag, or a new
     persistent readiness protocol.

2. Remove the fixed startup delay from `_start_manager()`.
   - Files to touch:
     - `weft/commands/_manager_bootstrap.py`
   - Reuse the existing detached-launch wrapper and pid-plus-registry checks.
   - Return success as soon as:
     - the launched pid is live,
     - the canonical registry record for `manager_tid` is active and matches
       that pid,
     - the detached launcher is still healthy and can accept the success
       acknowledgement.
   - Preserve the race-adoption behavior when another canonical manager wins.
   - Stop if the implementation wants a second bootstrap path or background
     polling channel.

3. Split the stress test by contract.
   - Files to touch:
     - `tests/cli/test_cli_run.py`
   - Keep one focused test for parallel manager reuse and single-manager
     convergence.
   - Keep a separate preserve-cleanup integrity test with fewer iterations and
     no repeated bootstrap-stress loop, so it measures SQLite integrity instead
     of bootstrap latency.
   - Reuse `WeftTestHarness` and real `run_cli()` calls.
   - Stop if the preserve-cleanup test stops exercising
     `cleanup(preserve_database=True)` on a real database.

## Testing Plan

- `./.venv/bin/python -m pytest tests/cli/test_cli_run.py::test_parallel_manager_reuse_converges_to_single_manager_under_repeated_bootstrap -q -n 0`
- `./.venv/bin/python -m pytest tests/cli/test_cli_run.py::test_weft_harness_cleanup_preserves_sqlite_integrity_for_parallel_manager_reuse -q -n 0`
- `./.venv/bin/python -m pytest tests/cli/test_cli_run.py -k 'parallel_no_wait_adopts_active_manager or repeated_bootstrap or cleanup_preserves_sqlite' -q -n 0`

## Rollback

If the cheaper readiness proof proves unsafe, revert the `_start_manager()`
change together with the spec wording and keep the test split. The test split
is independently valuable even if bootstrap readiness has to go back to a
slower proof.

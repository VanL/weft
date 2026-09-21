# Watcher SIGINT Lock Safety

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.1]; docs/specifications/07-System_Invariants.md [QUEUE.8]
Superseded by: none

Class: 4. Signal-time reentrancy in standalone topology shutdown crosses an
execution boundary; hardening and independent review apply.

## Goal

Remove lock-taking operations from deferred standalone SIGINT handling, verify
consistent topology and cleanup, and supply the repaired revision for the
user's downstream S1 exact-copy pin once its location is identified.

## Source Documents and Spec Baseline

Baseline: `ff95a4d1c530b7932f25d6efb9a6f88defbfb2a3`.
The governing contracts are [CC-2.1](../specifications/01-Core_Components.md)
and [QUEUE.8](../specifications/07-System_Invariants.md). No intended behavior
or normative spec change: retain deferred delivery after consistent publication
or rollback. Update spec backlinks and implementation mapping only.
Follow `docs/agent-context/engineering-principles.md` and the writing-plans,
hardening-plans, testing-patterns, and review-loops-and-agent-bootstrap runbooks.

## Context and Files

`weft/core/tasks/multiqueue_watcher.py::_sigint_handler` currently sets a plain
flag, then calls Event.set and strategy.notify_activity inside the signal frame.
Event.set reacquires its condition lock. The installed default strategy notify
only assigns a flag, but custom strategies may lock; neither effect is needed
until the existing finish boundary.
`_finish_topology_sigint_critical` already consumes that flag and calls
`stop(join=False)` outside the critical section before delivering the interrupt.
`BaseTask._sigint_handler` overrides this path and remains unchanged.

Modify that handler, `tests/helpers/multiqueue_sigint_probe.py`,
`tests/tasks/test_multiqueue_watcher.py`, this plan and the plan index, plus
non-normative mappings/backlinks in the two governing specs.
Read the existing probe and fatal-error precedence test before edits.
Comprehension checks: which boundary settles the request before interrupt
propagation? Which method already owns stop and wake effects? Answers:
`_apply_pending_topology_mutations` finally block and
`_finish_topology_sigint_critical`, respectively.

## Invariants and Hardening

Keep normal noncritical SIGINT delegation, exact fatal exception precedence,
synchronous mutator completion, owner-thread waiter cleanup, published membership,
and BaseTask behavior. No queue names, task states, reservations, payloads,
persistence, dependencies, public API, or configuration changes. No new signal
mechanism or watcher lifecycle. The plain deferred flag is the existing bridge
between the signal frame and normal owner execution. Stop/wake effects remain
owned by the finish method. Repeated critical SIGINT remains idempotent.

Keep real broker queues, real main-thread signal delivery and a subprocess
watcher drive; use only the existing controllable waiter/strategy test seams.
Hold the real stop-event condition or a custom strategy notification lock while raising
SIGINT during replacement. A bounded subprocess timeout catches deadlock without
hanging pytest. Retain existing replacement, displaced-close, and fatal rollback
coverage. Observable success: synchronous caller released, consistent queues,
KeyboardInterrupt after settlement, each waiter closed exactly once by owner.

Rollout: fix and verify Weft before updating any downstream exact-copy revision.
Locate S1 rather than inventing a pin destination. A pin requires a real commit;
follow user commit authorization and report outstanding landing work explicitly.
Rollback: revert the small source/test/doc delta and downstream pin together;
no persisted migration or one-way door exists, but rollback restores the bug.

## Steps and Verification

1. Review this plan independently before production edits. Add two cases to the
   existing real-signal subprocess probe (stop-event condition held; custom strategy
   notification lock held). Run those cases against unchanged production code
   and observe timeout. Stop if either case does not enter the target handler.
2. Remove both lock-taking calls from the critical handler; document why only
   plain state belongs there and cite [QUEUE.8]. Keep finish-time stop intact.
   Run the new cases and all existing watcher/SIGINT tests. Stop and re-plan if
   any caller needs an early stop event or wake to settle the transaction.
3. Add spec backlinks/mapping, run plan metadata and spec hygiene tests, full
   repository Ruff and mypy, and targeted BaseTask signal tests. Run independent
   completed-work review and record findings/dispositions. Use the repo .envrc
   and .venv tools. Run `git diff --check`. Only then prepare the S1 pin using
   the verified Weft commit after its destination/landing are resolved.

Commands: `. ./.envrc` then `.venv/bin/python -m pytest -n 0
 tests/tasks/test_multiqueue_watcher.py tests/tasks/test_signal_deferral.py
 tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py`;
`.venv/bin/ruff check .`; `.venv/bin/ruff format --check` on touched Python files;
`.venv/bin/mypy weft tests bin integrations/weft_django/weft_django
 extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox
 extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml`.
Commands with shared mutation run sequentially; independent read-only gates may
run concurrently. The main agent owns formatting and final integration.

## Review and Execution Log

- Author fresh-eyes review: no ambiguity in source fix. Residual scope question:
  downstream S1 destination and commit authorization. Requested location from user.
- Independent plan review: PASS (native same-family reviewer). Claude CLI
  discovered but its availability has not been validated. Recommendation to
  deliver repeated critical signals accepted: each held-lock probe raises twice.
- RED: targeted stop_event and notification subprocess cases both failed with
  TimeoutExpired at 10 seconds against unchanged production code.
- Downstream destination identified: Taut reactor restoration plan, S1; its
  baseline and source digest must advance together after the Weft fix lands.

- GREEN final targeted run: 85 passed, 1 PostgreSQL-only skipped in 11.79s.
  Both held-lock probes complete and preserve coherent membership and owner-only
  exactly-once cleanup. This signal-only change did not run the full suite or
  the live PostgreSQL lane.
- Full-repository Ruff passed; touched Python formatting passed; full mypy
  passed (435 source files); `git diff --check` passed.
- Independent completed-work review: no blocker, no actionable findings.
  Reviewed source, tests and non-normative traceability against the baseline.
  Author final review agrees; no intended behavior or public boundary changed.

- Source fix committed as `8bcf294be08d71c8bb70864ec7b19eb34f771b13`.
  Taut reactor restoration plan S1 now pins that exact revision and whole-file
  SHA-256 `72274ae115a7021477afa23fcb3190a92bb9a6d5b350a922d578237d21e3aebb`.
  Its provenance explicitly requires the standalone held-lock regression and
  distinguishes the source pin from executing the downstream vendoring slice.
  Parent verified the pin against `git show`; Taut document-path and explicit
  pin/digest/checkbox/whitespace checks passed. No Taut runtime copy was made.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |

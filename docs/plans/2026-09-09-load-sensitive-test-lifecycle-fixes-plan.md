# Load-Sensitive Test Lifecycle Fixes
Status: completed
Source specs: docs/specifications/03-Manager_Architecture.md [MA-3]; docs/specifications/07-System_Invariants.md [IMPL.10]; docs/specifications/08-Testing_Strategy.md [TS-0]
Superseded by: none

Class: 4 — launcher bootstrap, real-process test ownership, and cleanup lifecycles cross execution boundaries. No normative spec change is intended.

## Goal

Repair the reproduced nested-context process leak and inline-driver ownership loss, retain useful startup/result/crash diagnostics, replace timing-dependent lifecycle assertions with explicit actor synchronization, and exercise the affected surface under bounded higher-than-normal parallelism. Do not claim the historical startup stalls or worker crash explained without new evidence.

## Source Documents and Spec Baseline

Baseline: `c7628b6d`, governing specs listed above. Follow `docs/agent-context/engineering-principles.md`, `docs/agent-context/runbooks/testing-patterns.md`, `docs/agent-context/runbooks/hardening-plans.md`, and `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`.

Existing [MA-3] owns matching live PID plus canonical registry startup proof and diagnostic failure reporting. [IMPL.10] forbids closure while a reactor is active. [TS-0] makes the harness own isolated roots and runtime cleanup. Update only implementation notes/backlinks in touched specs; no new product contract, queue, persistence, dependency, or CLI flag.

## Context and Key Files

- `tests/helpers/weft_harness.py`: owns one context, PID discovery, daemon inline manager drivers, queue and directory teardown. Nested contexts currently escape ownership, and a timed-out inline driver is forgotten.
- `tests/conftest.py`: `run_cli` runs real subprocesses but callers can omit harness diagnostics. Pytest defaults to up to 12 workers.
- `tests/cli/test_cli_manager.py`, `tests/cli/test_manager_proctitle.py`: explicitly selected context must be owned before startup; failure output must include stderr; process title must be observed without hiding process disappearance.
- `weft/core/manager_runtime.py`, `weft/manager_detached_launcher.py`: detached launcher first-line read currently precedes the startup timeout. Keep the existing protocol and runtime-launch owner; bound first-event observation and clean/reap on timeout or interruption.
- `tests/core/test_client.py`: inline manager + real result waits; capture timeout state before context cleanup, separate snapshot invariant from completion latency.
- `tests/core/test_manager.py`, `tests/tasks/sample_targets.py`: SIGTERM test uses a five-second sleeping target and consumes task history. Replace this test's observation with non-consuming readiness and a target-owned gate.
- `tests/test_harness_registration.py`, `tests/system/test_manager_detached_launcher.py`, existing manager-bootstrap command tests: regression owners.

Comprehension checks: Which context does the parent harness actually own? Does a returned `cleanup()` call prove the inline thread has exited? Which evidence proves child target entry versus only process creation?

## Invariants and Constraints

Keep TIDs, queues, lifecycle transitions, immutable TaskSpec fields, reservations, and launch acknowledgement truth unchanged. Use real queues/processes for ownership and ordering regressions. Fault injection gates scheduling or launcher protocol only; do not mock broker semantics. Never terminate unrelated processes or trust PID alone without existing identity checks. Tests remain parallel; no blanket retries, sleeps as correctness, or raised shared timeouts. An outer watchdog bounds infrastructure hangs, not the semantic assertion.

## Implementation Slices

1. **Ownership:** Make the affected CLI tests use their already-owned harness context (explicit `--context` coverage remains). Register the harness on each CLI call and retain stdout/stderr on failure. Add a real-process regression that raises after startup and proves fixture cleanup reaps its manager. For inline managers request stop and join; if any driver remains, retain its ownership and context artifacts, fail cleanup visibly, and permit retry after it exits. Do not close queues or remove roots while any driver remains. Demonstrate with an event-gated real manager turn.
2. **Launcher:** Bound the initial detached-launcher protocol event using a cross-platform primitive, retaining existing startup limits. Keep a live launcher's pipes open until its abort/EOF cleanup can terminate its child. Prove no first event, EOF/malformed event, valid event, and cancellation with real helper processes and isolated gates. Avoid a persistent orphan reader thread. Capture startup phase, child/launcher identity, and stderr before teardown destroys them. Preserve successful readiness despite post-proof diagnostic failure.
3. **Tests and diagnostics:** Synchronize the SIGTERM target's own entry with a readiness artifact and release/stop boundary; use non-destructive event observation for this test. Reuse reactor driver where its boundary fits, preserving producer closure. Improve proctitle failure output. On client-result timeout attach queue/PID/thread state before cleanup; preserve a direct payload snapshot assertion plus end-to-end result coverage. Add opt-in test-run diagnostic directory support for worker faulthandler output and controller worker-crash details, kept outside harness tempdirs. No diagnostics become durable application truth.
4. **Validation and review:** Run new regressions red then green, neighboring suites, independent review after meaningful slices and final integration, repo-managed lint/type checks and spec metadata/traceability gates. Run a limited cohort with `-n 24 --maxprocesses 24`, twice the configured cap, enough tests/repetitions to occupy workers. Save logs and worker diagnostics in a unique temp directory. Bound the run with an outer watchdog; compare owned-process identities before/after, investigate any failure, and clean only run-owned processes. Do not run the full suite just to manufacture pressure.

Stop and reassess if a public contract change, new execution path, PID-ownership ambiguity, or suppression of a real failure is required.

## Rollout and Rollback

Test ownership and launcher patches are independently revertible; no stored format or migration. No one-way door. Keep all unrelated dirty files unchanged. Do not commit without user authorization; report changed files and verification explicitly at handoff. If cleanup fails, retain artifacts and tracking rather than report success or delete live storage.

## Verification

Commands use `. ./.envrc` and `.venv/bin/python -m pytest`. Run focused regressions, then CLI manager/proctitle, client, harness registration and launcher neighbors. Run `.venv/bin/ruff check .`, repository mypy command, and spec metadata checks. The user requested a limited stress run, so scope full validation to changed surfaces plus static gates. Record exact selected count, workers, durations, failures, and post-run process check. Failures need state snapshots before any teardown; a fresh passing rerun never erases a failed run.

## Review and Execution Log

- Separate plan self-review and independent same-family review PASS before implementation. Clarifications: pre-event abort retains launcher child ownership, failed inline cleanup remains retryable, diagnostic failures preserve the original error.
- Both active-turn cleanup variants failed against baseline (no error despite live driver), then passed after retention/retry. Pre-cleanup evidence persistence tests likewise demonstrated red/green. Independent review found a combined-case bug: cleanup replaced a prior timeout; a real gated regression reproduced it, and the corrected `__exit__` preserves the primary exception. Round-2 review PASS.
- Launcher first-event stall hit the old outer watchdog; malformed first event left a real detached child alive. Both corrected paths pass. Review corrected two test-fixture faults: false EOF from `sys.stdout.close()` and readiness published before the protocol action. Real descriptor EOF and atomic post-action readiness now exercise their intended branches. Sixteen launcher tests pass.
- Registry readiness failure records prior row/proof/PID observations with no additional probe. Two observation tests pass; all 98 command-run neighbors pass. Independent diagnostic delta review found no production blocker.
- Per-worker diagnostic regression deliberately aborts a real xdist worker and verifies retained fatal stacks, last node, and controller exit report. Twenty diagnostic/launcher tests pass in independent final review. SIGKILL cannot yield a Python stack; controller and last-node evidence remain.
- Bounded stress: `.venv/bin/python -m pytest -o addopts= --strict-markers -m 'not slow' -n 24 --maxprocesses 24 --dist load --tb=short -ra` over CLI manager/proctitle, client, three shutdown tests, harness registration, launcher, and diagnostic modules. Two rounds each passed 124 tests (21.67s and 25.45s pytest durations). Diagnostic events confirm 24 distinct worker IDs and 24 simultaneously active tests, twice the normal 12-worker cap. Supervisor observed 106/107 descendants; neither round left an observed live descendant or detached manager in its run roots. Artifacts: `/tmp/weft-lifecycle-stress-67i27ll1/`, including exact argv, XML, logs, diagnostics, and process checks.
- Validation: repository Ruff passes, repository mypy passes (186 sources), plan/spec/suppression tests pass (47 tests), suppression index check passes, diff whitespace check passes. Extra mypy of the harness initially found an existing `_terminal_status_from_task_log` variable-type error, reproduced unchanged at baseline line 597 in an isolated copy. At user-requested commit closeout, separate `event_status` from the raw payload `status` variable to preserve identical behavior and clear that check.
- Final independent integration review: no blocker across harness ownership, launcher cleanup, failure diagnostics, and readiness synchronization. Native Windows and PostgreSQL runs were not performed; stress used macOS/SQLite. Historical worker-crash and startup-stall causation remains unproven; this change preserves the evidence needed on recurrence.

- Commit closeout: user authorized a targeted commit and clean Ruff/mypy checks. The only additional code adjustment is the local variable-name repair above; no runtime semantics changed. Stage only this task’s plan-index row/count and leave pre-existing edits outside the commit.

## Changed Files

- Runtime: `weft/core/manager_runtime.py`.
- Harness and diagnostics: `tests/helpers/weft_harness.py`, `tests/helpers/run_diagnostics.py`, `tests/conftest.py`, `tests/test_harness_registration.py`, `tests/system/test_run_diagnostics.py`.
- Lifecycle/client tests: `tests/cli/test_cli_manager.py`, `tests/cli/test_manager_proctitle.py`, `tests/core/test_manager.py`, `tests/core/test_client.py`, `tests/tasks/sample_targets.py`, `tests/system/test_manager_detached_launcher.py`, `tests/commands/test_run.py`.
- Documentation: this plan and index, `docs/ruff-suppression-registry.md`, `docs/specifications/03-Manager_Architecture.md`, `docs/specifications/08-Testing_Strategy.md`, `docs/lessons.md`.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |

No normative deviation. First-event observation has its own existing-constant 10-second bound; the subsequent registry observation budget remains unchanged. No blanket retries or increased wait defaults.

# Manager Late-Launch Cleanup Hardening Plan

Status: completed
Source specs: docs/specifications/07-System_Invariants.md [IMPL.10], [EXEC.3], [OBS.6]
Superseded by: none

Class: 4 - risky. This changes Manager cleanup behavior at the child-process
boundary and closes a path that can leave a descendant alive after the shared
cleanup deadline.

## Goal

Ensure a child launch that completes after Manager cleanup has exhausted its
deadline still receives an immediate process-tree kill, while making the CI
tests observe the contracts they claim to exercise instead of unrelated wall
time.

## Evidence and Root Cause

- GitHub Test run `31821241249`, attempt 1, Windows Python 3.13 core/commands
  job `94834708202`, timed out before
  `test_manager_cleanup_terminates_worker_descendants` observed the launched
  wrapper. Fixture cleanup then failed for 30 seconds with `WinError 32` while
  removing the harness directory.
- `Manager._run_child_launch_service_worker()` skips
  `terminate_process_tree()` when the shared cleanup deadline has expired and
  falls back to `process.kill()`. That kills the wrapper only; a descendant can
  survive and retain a Windows handle to the harness directory.
- The same job's
  `test_manager_child_termination_uses_one_deadline_for_multiple_children`
  exceeded a 250 ms wall-clock assertion even though its fake `join()` records
  timeouts without sleeping. The deterministic `sum(join_timeouts)` assertion
  already fires the one-deadline contract; host preemption is not product
  elapsed time.
- Windows Python 3.14 remaining job `94834708132` failed
  `test_tid_mapping_records_runtime_identity_from_start_hooks` because the
  test waited five seconds for full task completion. The worker was still
  active. Runtime-handle publication occurs at process start and is the direct
  evidence required by [EXEC.3] and [OBS.6].
- The affected jobs showed broad unrelated setup, test, and teardown delays,
  while the same commit and dependency versions passed the prior run. Runner
  starvation explains why the latent assumptions surfaced now, but it does
  not remove the descendant-cleanup defect.

## Source Documents and Baseline

- `docs/specifications/07-System_Invariants.md` [IMPL.10] owns the shared
  absolute cleanup deadline and process-tree escalation contract.
- The same specification's [EXEC.3] and [OBS.6] own runtime-handle and TID
  mapping publication.
- Implementation baseline: commit
  `5bf65444b98963664512cd9c35c61df69944f080`.

## Invariants and Boundaries

- The shared cleanup deadline remains absolute. Late escalation must not open
  a fresh grace period.
- A launch discovered before deadline retains graceful process-tree
  termination within the remaining budget.
- A launch that completes after deadline receives an immediate zero-wait tree
  kill before wrapper-only fallback, so descendants cannot escape cleanup.
- Runtime identity tests observe a published runtime mapping, not task
  completion.
- Synthetic deadline tests assert allocated budgets and resulting state, not
  scheduler-dependent wall time.
- No timeout increase alone, SimpleBroker change, public API change, or CI
  configuration change belongs in this fix.

## Implementation

1. In the late-launch cleanup branch, call `kill_process_tree(pid,
   timeout=0.0)` when no cleanup budget remains, then retain the wrapper kill
   fallback.
2. Extend the existing blocked-launch regression with a real wrapper and
   descendant to prove the expired-deadline path calls zero-wait tree kill,
   both processes exit, and their working directory becomes removable.
3. Make the affected child-cleanup readiness checks use the reactor driver and
   the existing Windows process-start budget instead of fixed five-second
   polling loops.
4. Replace the synthetic wall-clock ceiling with a fake monotonic clock and
   fake waits while retaining the deterministic aggregate timeout-budget
   assertion.
5. Make the runtime-identity test drive until a mapping with a runtime handle
   exists, using the process-start budget and preserving direct diagnostics.
6. Update [IMPL.10] and its implementation mapping, run focused repetitions,
   then run the full release verification matrix.

## Verification

- Focused Manager late-launch, descendant-cleanup, and shared-deadline tests.
- Focused runtime-identity observability test, including repeated runs.
- Ruff check and format check for touched files.
- Full local default and PostgreSQL suites plus extension, type, and live
  provider release gates through the release helper.
- Independent review of the cleanup deadline semantics and regression tests.
- Retag `v0.9.96`, monitor all GitHub Test and Release Gate jobs, and verify
  PyPI publication.

## Review Outcome

Independent review found and drove four corrections before completion:

- The runtime-mapping predicate now excludes the constructor's fallback task
  process handle and requires runner-owned start-hook evidence.
- The late-launch regression uses a real wrapper and descendant, has an
  untimed release gate, recovers the process handle on every failure path, and
  proves the working directory is removable after cleanup.
- The one-deadline regression uses module-bound fake-time proxies instead of
  mutating the process-wide `time` module.
- The late-launch regression retains `C901` under existing group
  `RUFF-SUP-009`. Review concluded that this is one ordered concurrency proof;
  fragmenting its happens-before sequence merely to lower the score would make
  the boundary harder to audit. The suppression registry records that choice.

Focused regressions, the complete Manager and task-observability modules in
both serial diagnostic mode and normal xdist mode, Ruff, format, mypy, plan
metadata/spec hygiene, and Ruff suppression-policy tests passed locally. The
release helper remains the authoritative full default/PostgreSQL/extension/live
provider gate.

## Rollback

Revert the implementation, tests, spec delta, and this plan together. The
rollback restores the wrapper-only late-launch behavior and therefore reopens
the descendant leak; it is not a supported steady state.

# Idempotent Wrapper-Lost Settlement

Status: completed
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.6a]; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md MANAGER.11
Superseded by: none

Class: 4, risky execution/custody repair under existing terminal-evidence rules.
No normative behavior delta: preserve the already committed supervisor outcome
when live cleanup and adopted-resident retirement settle the same wrapper.
Baseline: `2f3abf66`. Theory: [WT-2a.7], [WT-2a.13], [WT-4.3], and the
[WT-7] aligned-by-reading/false-by-running falsifier. The independent review
supplied by the owner establishes the causal premise and recommends this fix.

## Goal and boundary

Live `_cleanup_children` commits wrapper_lost with the OS exit code, then
`_record_reaped_admission` reaches `_retire_scheduler_residents` before the
reservation is deleted. Retirement calls the same writer with exitcode=None.
The writer recognizes only task-owned proof, so writes a second envelope that
can erase the exit code from the public observation. Make that writer recognize
an existing valid Manager-source wrapper_lost as committed. Preserve task-owned
precedence, task-only proof-grace semantics, exact reservation acknowledgement,
configured routes, and read-failure behavior. No queue/schema/dependency change.

## Tasks

1. Add a real spawned managed-wrapper death regression under the live Manager.
   Seed actual reservation/checkpoint state, wait for an explicit child-ready
   witness, kill and reap the process, and run the actual cleanup owner. RED
   must show duplicate envelopes. Assert one envelope and the real OS exit code
   (-9 on POSIX; nonzero platform-reported code on Windows). Clean up on failure.
2. Share terminal-envelope scanning between task-only proof and the idempotent
   writer. The task-only proof adapter retains its semantics. A valid matching
   manager wrapper_lost (type/source/TID/status/error) prevents another write.
   Adopted-resident retirement skips locally tracked children: local cleanup
   owns their real Process exit code and invokes admission retirement after
   removing its child entry. Otherwise a periodic refresh can write the first
   envelope with exitcode=None before live cleanup. Add both real ordering
   cases and malformed/unrelated-manager-row cases; preserve task precedence.
3. Bound the skipped-row warning to a count and one example. Fix Related Plans
   ordering and blank separation. Repeated validation warnings on malformed
   retained requests were explicitly accepted in the parent plan; leave them
   as an observation rather than adding a cache or parser bypass.
4. Run real SQLite/PostgreSQL owner regressions, nearby live cleanup and recovery
   suites, canonical type/lint/doc gates, and independent completed-work review.
   Record every finding and disposition before marking this plan completed.

## Hardening and rollback

Keep real broker, reservation, checkpoint, OS process and cleanup ownership.
The regression must fail on the baseline; no fake process or mocked settlement.
Rollback is a code/test revert, with no persisted-format or migration work.
Stop if recognition needs timestamp/source precedence changes, alters task-only
proof grace, or releases custody after a failed terminal publication.

## Verification

Load `. ./.envrc`; use normal configured concurrency.

- `python -m pytest tests/core/test_admission_recovery.py tests/core/test_manager.py tests/core/test_manager_family_maintenance.py -q`
- `bin/pytest-pg tests/core/test_admission_recovery.py tests/core/test_manager.py tests/core/test_manager_family_maintenance.py`
- Canonical repository mypy command, Ruff check/format, `tests/specs`,
  `bin/check-doc-paths`, and `git diff --check`.

## Review and execution log

- Initial owner-supplied independent review reproduces the regression on
  `2f3abf66`, traces both writer calls, and recommends recognizing existing
  Manager-source wrapper_lost while keeping task-only grace proof unchanged.
- Fresh-eyes design check: idempotent recognition belongs to the shared commit
  owner, not only one caller. Do not suppress a second call with local memory.
- Implementation and RED/GREEN evidence completed; review and final gates below.

- RED: the actual live cleanup plus real spawned/killed managed process wrote
  two envelopes (len 2 versus expected 1). Idempotent recognition fixed that.
- Additional RED after that first fix: running adopted-resident retirement
  before local cleanup produced one envelope but return_code=None instead of
  the actual -9. Local-child settlement ownership now keeps that operation in
  `_cleanup_children`; both orderings are covered by the same real process test.
- Broad SQLite owner suites passed; PostgreSQL owner suites passed 351 cases,
  12 backend skips in 99.95 seconds before the second ownership correction.
  Final results are recorded below.

- Final focused admission-recovery suite passed all 23 cases on SQLite and
  PostgreSQL after the local-child ownership correction. Updated broad SQLite
  owner roots passed with 3 expected PG-only skips. The final combined D1 owner
  gates passed 721/3 (SQLite) and 715/12 (PG), and full PG passed 6470/40.
- Canonical mypy491, Ruff check/format668, doc paths and specs passed. Full fast
  SQLite ran 6617 pass/49 skip with one unrelated-in-kind plan/index metadata
  mismatch from D1 promotion; the narrow fix and final 203-case specs/owner
  regression passed. No runtime source changed after that full run.
- Cross-family completed review Unit A passed; focused round 2 inspected the
  added local-child ownership skip and both actual kill/cleanup orderings and
  passed. The combined D1 source review preserved this constraint. Google
  source-native reads were permission denied on a later combined attempt;
  source-inline review and its counterevidence follow-up supplied the actual
  final PASS. No native-read claim is made for that follow-up.
- Related Plans repair was limited to the specific theory/repair bullets;
  broader unrelated historical reordering was rejected. Repeated malformed
  retained-request warnings remain the already acknowledged tradeoff.
- No new commit requested; completed repair remains reviewable in the worktree.

- Final completed status/index and synchronized spec closeout passed all 165
  specification tests in 3.97 seconds; doc paths and diff checks passed.

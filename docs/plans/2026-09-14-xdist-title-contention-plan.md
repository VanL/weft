# Xdist Title Contention Investigation

Status: draft
Source specs: docs/specifications/08-Testing_Strategy.md [TS-0]; docs/specifications/01-Core_Components.md [CC-2.4]
Superseded by: none

Class: 5+P. Test bootstrap changes preserve production behavior while making the owner-approved test boundary explicit. Hardening: worker bootstrap isolation must not alter spawned production processes.

## Outcome and Baseline

Baseline: df9757d6. Retain GUI titles and the accepted synchronous native calls
in [CC-2.4]. User requires everything under test to behave as production;
xdist and harness infrastructure may avoid GUI title registration.

- [x] Remove xdist-owned native title traffic on macOS before its optional import.
- [x] Restore default titles on the Consumer under STOP/KILL tests.
- [x] Prove real task title activation remains available in a worker and child.
- [x] Correct harness liveness to follow owned manager threads, not pytest PID.
- [ ] Reproduce the concurrent suite with native evidence and complete release gates.

## Evidence and Limits

Full 12-worker runs repeatedly timed out in persistent-agent conversation and
long-session result tests; CLI-only run passed 292 tests. Samples in
/tmp/weft-concurrency-driver-samples-1789427890 captured PIDs 89492, 89493,
89502 and three managers spending all samples in stock setproctitle initial
_LSApplicationCheckIn and synchronous XPC. Other pytest workers generated
repeated GUI title updates. This proves the immediate wait site, not permanent
deadlock or the source of LaunchServices backlog. Retained queue evidence showed
pending persistent input and an undrained worker result. No broker data loss was
observed. Compare after reducing infrastructure traffic; do not infer success.

## Proposed Spec Delta

Promotion A: add to [TS-0]: "Test infrastructure must preserve production
behavior for the subject under test, including process-title activation.
Harness-only inline managers may disable their own titles. On macOS, xdist's
optional worker-title updates are suppressed before its native title import;
the worker retains its Unix executable name until code under test changes it.
This changes no production module, child environment, or native-library module
binding."

## Implementation and Verification

Use the installed xdist remote source through its public
pytest_xdist_getremotemodule hook. Locate only the optional setproctitle import
with Python's AST, remove that try block in favor of xdist's own missing-library
fallback, and preserve all other source. An incompatible upstream shape must
fail explicitly instead of silently restoring GUI traffic. Do not replace
sys.modules, mutate site-packages, or set inherited disabling environment.
Wire in tests/conftest.py. Restore _make_taskspec title defaults in the
STOP/KILL tests. Inline managers created solely by WeftTestHarness retain
their explicit title opt-out. Direct Manager/Consumer tests remain real.

First verify that the actual remote source imports stock setproctitle before
the fix, then verify the adapted bootstrap does not. Real process tests must
exercise stock GUI activation in the worker and a spawned child using the
existing title probe, plus full-suite runs at 12 workers and native samples.
Run Ruff, mypy, metadata/spec hygiene, then release helper. No timeout expansion,
parallelism reduction, workload deletion, or production title-policy change.

Rollback: revert the test bootstrap adapter and plugin wiring. No persistence
or production behavior migration. Reviewer must check native writer ownership,
source adaptation scope, non-Darwin path, and subprocess environment isolation.

## Reviews and Deviations

Self-review: using processtitle directly for xdist would duplicate preparation
and could alternate native writers after an in-process task's stock handoff.
Prefer the existing xdist no-title fallback. Independent review: Boyle PASS,
with explicit confirmation after owner approval of full xdist suppression.
Promotion A applied to [TS-0] before implementation.

Implementation review found two adapter gaps: validate the entire known
optional-import/fallback AST (decorators and default expressions can execute
code), and verify native handoff inside a fresh real xdist worker. The pure
bootstrap test must not include a native import within its short timeout.

The review also identified an existing candidate harness defect: pytest PID
liveness was incorrectly treated as exact inline-manager liveness. Correct the
test-only provider and active-record selection to consult the owned Thread by
full manager TID. Known finished threads are stale; missing ownership or a
not-yet-started thread is unknown. A live thread is lifetime evidence, not proof
of responsiveness. Keep production liveness unchanged.

## Verification

Initial focused run: 162 passed. Initial full SQLite run with xdist title
suppression: 4,908 passed, 25 skipped in 320.75 seconds. The persistent-agent
conversation passed and the long-session call passed in 217.44 seconds
(12.30 seconds teardown). Both commands specified 12 workers but the existing
pyproject maxprocesses=8 cap made them eight-worker runs. These are not the
required 12-worker release evidence. The next focused run and release helper
use the uncapped addopts override; their startup must confirm 12/12 workers.

A different-family review was attempted with Claude, but the CLI failed before
review because its OAuth session was expired and could not refresh. No verdict
is attributed to that attempt. Boyle's independent review and parent review
remain the available review evidence.

Final focused verification confirmed 12/12 workers: 179 passed in 15.92 seconds,
including the native handoff in a fresh xdist worker and in a child, STOP/KILL,
thread lifetime/missing ownership/two managers sharing one PID, stale active-row
replacement, and metadata tests. Ruff check and format --check passed (860
files); mypy passed (424 source files). Parent reviewed the weak ownership
registry and active-record guard. No production files, timeouts, or suite
parallelism policy changed in this slice. Full release gates remain pending.

Final independent scoped review: Boyle PASS. Both title-adapter findings are
resolved; parent separately reviewed Boyle's harness liveness implementation.
The isolation correction is established, not the underlying source of macOS
LaunchServices backlog. Keep observing the uncapped release run.

Staging exposed repo-root weft-tests.db and its lock (empty SimpleBroker schema,
no messages); neither is included in the release. A temporary sqlite3.connect
audit hook outside the repo identified real context/TID preflight in
tests/commands/test_task_commands.py, initially the mixed-scope and invalid-TID
tests and then a terminal-task rejection test. Mocking the control operation
did not mock context construction. Bind this command module to the existing
WeftTestHarness fixture so unqualified command contexts cannot touch the caller's
working database; three TID/scope tests also pass their context explicitly.
This Class 2 test-isolation repair changes no production behavior. Recheck with
the temporary trace before the release; do not add the trace or DBs to git.

Isolation recheck: 179 passed at confirmed 12 workers in 15.75 seconds; the
audit recorded no new repo-root connections and no database artifacts appeared.
Ruff check/format and mypy (424 files) remained clean.

CI slice inspection before tagging found that the new root-level test module
would not be collected by the enumerated remaining slice. Move it to
tests/system/test_xdist_titles.py, already covered on every CI platform. The
first release run was deliberately interrupted at 24% with no test failures to
include this correction; its KeyboardInterrupt was operator-issued, not a
runtime failure. Restart the full gates from the final commit.

While monitoring the full 12-worker SQLite gate, comparison with v0.9.99 found
that commit 8a221e35b had lowered pyproject's default maxprocesses from 12 to 8.
Restore 12 under the owner's standing prohibition on reducing concurrency.
SQLite already runs with its uncapped release override; the subsequent PG
wrapper now also runs with 12 instead of silently capping its requested 12 at 8.
This restores the prior release policy, not a new throughput requirement.

# Updated Dependency Suite Repairs
Status: draft
Source specs: docs/specifications/08-Testing_Strategy.md [TS-0]; docs/specifications/07-System_Invariants.md [IMPL.10]
Superseded by: none

Class: 4. Test process ownership and any runtime repairs require independent review. Baseline: `601185f7`, with the user's updated dependency lockfile retained.

## Goal and Boundaries

Reproduce and repair failures from `uv run pytest` and `uv run pytest-pg` against the updated dependencies. A load-sensitive failure remains a defect requiring evidence. Do not reduce worker counts, downgrade dependencies, add blanket retries, or enlarge semantic timeouts to hide failures. Preserve unrelated edits. Queue contracts, immutable TaskSpec fields, process identity checks, and the existing execution spine remain unchanged.

## Evidence and Owners

The initial SQLite baseline is recorded in `/tmp/weft-current-pytest.log`. Ruff lint, formatting, and mypy pass against the updated environment. PostgreSQL provisioning is available through `bin/pytest-pg`; use that wrapper for real backend evidence. The existing lastfailed cache includes removed tests and is not current failure evidence.

`integrations/weft_django/tests/test_weft_django.py` allocates a module root with `mkdtemp`, submits tasks that auto-start managers, and never tears those managers down. Eight surviving managers each retain four children and broker files in separate Django test roots. This is a confirmed test ownership defect independent of the remaining suite failures.

## Slices and Verification

1. Repair Django integration ownership using the existing `WeftTestHarness`. Construct an inactive harness at import only to establish the settings root; enter it in a module autouse fixture after Django setup. Move broker bootstrap and migrations inside the fixture. Close Django connections before harness exit. The harness must stop/reap the owned manager and children before deleting its root, including when a test fails. Do not kill processes by title or traverse arbitrary override roots. Prove success and assertion-failure teardown using real subprocess pytest and real broker/runtime identities; demonstrate the regression before and after the fix. Override roots that only support read/config tests remain inert; future runtime overrides require their own owned harness.
2. Complete both requested baseline commands and classify every fresh failure by actual trace. Extend this plan with concrete owners, spec references, red tests, and minimal repairs before changing additional runtime behavior. Distinguish dependency incompatibility, application defects, and test synchronization defects. Stop speculative fixes when evidence contradicts a hypothesis.
3. Run targeted regressions, then both complete commands at normal parallelism. Exercise repaired load-sensitive paths at higher parallelism in a bounded run. Verify no owned live descendants survive, retain failure diagnostics, and rerun Ruff lint/format and repository mypy. A passing rerun does not erase unexplained earlier failures.

## Review, Rollback, and Traceability

Read `tests/helpers/weft_harness.py`, the Django integration module and settings fixture, Spec 08 [TS-0], and Spec 07 [IMPL.10] before implementation. Key questions: when does Django capture its root, who owns detached runtime children, and which connection must close before root removal? Keep real queues and processes in lifecycle regressions; inject only the deliberate assertion failure.

Separate self-review and independent same-family review are required before each runtime/ownership slice and at integration closeout. Record findings and verification here. No new normative behavior is intended; add the plan backlink to touched testing guidance. Rollback is reverting the relevant code/test slice together; no persisted schema migration or deployment ordering is introduced. Operational proof is both suite results and absence of surviving owned process identities.

## Review and Execution Record

Self-review: scope separates the proven Django leak from as-yet-unclassified suite failures. Import-time harness construction must remain inactive; module setup and teardown must retain one concrete broker owner. Independent review pending.

Independent same-family review: PASS for slice 1. Incorporate two checks: fixture-owned Django environment paths must override inherited fixture paths so settings and harness cannot select different roots; preserve the module `_bootstrap_context` used by the export purity test when deferring its initialization. Regression must assert the captured settings match the owned root.

The first SQLite run completed with 36 failures, 4663 passes, and 5 skips. Its plan-metadata mismatch concerned the Django plan's draft/completed index row, subsequently synchronized by the concurrent Django closeout. A sampled recheck was interrupted by its diagnostic controller's psutil exception and is not a valid suite result. Its native stacks are valid causal evidence: 26 sampled owned children were blocked in `setproctitle` import, through `spt_getproctitle -> spt_setup -> init_ps_display -> darwin_set_process_title -> _LSApplicationCheckIn`, waiting synchronously on macOS LaunchServices before task initialization. This is an optional observability operation on the critical startup path, not a broker timeout diagnosis.

Upstream setproctitle 1.3.7 has no LaunchServices opt-out. An isolated eight-line C patch at upstream commit `389ed6f4e3ccfebb34a38893553992ea10cf5dc8` adds `SPT_NO_LAUNCHSERVICES` before any GUI resources are acquired, retaining the caller's argv update and native thread name. Independent review PASS. A built wheel passed 24 concurrent imports/title updates with all titles verified through `ps`; maximum startup 0.27 seconds. Dependency delivery preference is being resolved separately; temporary wheel overlays are validation only, not a distributable project fix. Runtime adoption must set the option before eager imports, including xdist child bootstrap, and remain reproducible in a clean install. Resolved 2026-09-10: no patch or fork ships; see the dependency disposition at the end of this plan.

PostgreSQL's initial wrapper run completed with 2 failures, 1568 passes, 11 skips before its fail-fast setting stopped the remaining shared tests. Failures were persistent-stream timeout coupling and installed-console final cleanup, described below. Ruff lint/format and mypy (186 sources) pass with the user's dependency updates. Django ownership regressions demonstrated both success/failure leaks and inherited root mismatch before repair; the full 64-test Django cohort and 46 harness tests pass after repair. Independent reviews additionally required a watchdog around the callback test, creation-time-safe regression cleanup, and parent ownership before the nested Django pytest controller starts. These review corrections are part of the repair.

## Additional Proven Repairs

- PostgreSQL iterator test: `test_persistent_stream_output_is_not_realtime_completion` starts a 1.5-second follow deadline, suspends the iterator, then writes two real PG rows. The deadline can expire while the test itself holds the consumer suspended, yielding no stream frames. Replace elapsed-time absence with causal evidence: consume the final-marked frame, write another frame, and prove the same iterator remains live to deliver it. Close the iterator explicitly; retain an outer test watchdog, not a semantic deadline.
- Installed-console fixture: the final unasserted `manager stop --force --timeout 5` has three possible five-second lifecycle waits plus interpreter/registry work, while its outer subprocess cap is only fifteen seconds. It is also the sole owner cleanup, so timeout can leak the detached manager. Use the existing harness for runtime ownership after installed-console fresh init. Keep PYTHONPATH absent for installed acceptance and prove console commands use the harness's exact broker target (default harness DB and initialized project DB must not diverge). The test still checks all installed command/result/reuse assertions; manager-stop behavior has its own tests.

- Harness allocation inspection: the full Django module exposed five teardown errors because `isinstance(obj, Queue)` invokes unrelated lazy `__class__` properties during GC traversal. Inspect concrete allocation types with `issubclass(type(obj), Queue)` instead. The regression fails before the change on proxy evaluation and also verifies actual Queue subclasses close. Independent same-family review PASS: initialized proxies' underlying queues remain independently discoverable; uninitialized proxies must never be realized by cleanup.

- Tool dispatch (class 2): `bin/uv` strips repository `bin` from the PATH exported to real uv, making `uv run pytest-pg` fail before tests start. Resolve real uv with the filtered lookup path, but execute its absolute path with the original child PATH. Prove discovery and no recursion with a fake real executable and the actual wrapper help command. No dependency downgrade or manifest change.
- Test lifetime boundaries: `tests/commands/test_task_commands.py::_launch_running_task` asserts readiness before callers enter their cleanup blocks. A failed precondition leaks the Consumer and open queue handles. Own setup failure inside the helper, close queue handles in finally, and retain deterministic worker liveness with target-owned readiness/release instead of a five-second sleep. Use real process failure injection to prove cleanup; assert stop/kill against a still-live worker. In `tests/tasks/test_runner.py`, the leaked-sender test incorrectly times from process spawn though its contract bounds drain after producer exit. Establish exit before timing drain. Callback-error isolation should not impose an unrelated five-second execution timeout. Root reviewed these bounded proposals against the helpers and full failing traces; implementation remains test-only. Governing runtime contract: `docs/specifications/07-System_Invariants.md` [EXEC.7]; existing [TS-0] owns fixture cleanup. Keep unresolved process bootstrap stalls open rather than claiming these test repairs explain them.


Latest validation: an isolated clean virtualenv with the patched setproctitle source and opt-out enabled completed exact `uv run pytest`: 4707 passed, 5 skipped in 188.01 seconds at the configured 12 workers. This is dependency-patch validation, not evidence that the main checkout has a distributable dependency fix. The preceding wheel-overlay run had only two Ruff suppression inventory failures; the new intentional Django E402 entry is now reflected in the registry.

The PostgreSQL repeat stopped at its fail-fast gate with 2 failures, 1565 passes, and 11 skips. The one-shot final-stream observation test had the same suspended-iterator deadline coupling as its persistent sibling; it now proves delivery of a subsequent frame, with independent review PASS. The other failure was `test_result_returns_payload_for_completed_task`, whose `work_failed` payload was discarded by the harness. A targeted PG rerun passed; its cause remains open. Preserve terminal event details and pre-cleanup evidence before repeating the complete PG selection.

Installed-console review corrections passed SQLite (2 tests) and PostgreSQL (3 tests including CLI-result reproduction). Explicit reuse and a nonempty manager list retain meaningful reuse coverage. An injected pre-enter failure proves cleanup does not materialize a context from inherited foreign defaults. Independent review PASS.


Completion-wait diagnostics now include the exact terminal event JSON, then collect retained task/broker evidence after unwinding the active queue. This closes the evidence gap for yield-fixture callers without adding pytest lifecycle hooks. Two real-queue regression variants failed before repair and passed afterward; focused SQLite 7 passed, live PG 3 passed, independent review PASS. Diagnostic collection failure preserves the original task error.

Bounded stress: the eight originally reported cases passed in 4.19 seconds with 24 pytest workers while the normal-parallel PostgreSQL suite was also running. No application timeout or worker-count reduction was used. This does not resolve the earlier unexplained work_failed event by itself.


Final aggregate review identified inherited bare-PID cleanup in six successful callers of `_launch_running_task`. They now retain the worker's `psutil.Process` identity through kill/wait and use the original multiprocessing handle for Consumer reaping. Setup failure uses the same owner cleanup and does not reconstruct authority from an already-reaped PID. The full 87-test module passed at normal 12 workers; parent review PASS. No runtime cleanup policy changed.

Dependency delivery boundary remains open: ordinary `pip install weft` ignores uv source overrides. A private vendored extension would require native wheel packaging and would not fix xdist's separate top-level setproctitle import. A release/fork under the existing dependency import is the smallest complete delivery. The validated patch was `/tmp/weft-setproctitle-launchservices.patch`; no fork was published and no project runtime dependency metadata was changed to imply delivery. Resolved 2026-09-10: see the dependency disposition below.


The final diagnostic-enabled SQLite run passed 4710 tests with 5 skips in 240.09 seconds, concurrently with the PG suite. Five additional live-PG CLI-result module runs each passed all 11 tests. The final identity-cleanup module passed all 87 tests on live PG in 11.77 seconds. The unexplained earlier task error did not recur in these runs; no causal claim is inferred from that absence.

### PostgreSQL bounded-wait connection lifetime

A real PostgreSQL 18 probe against installed simplebroker 8.1.1/simplebroker-pg
4.1.1 measured 100 repeated scans of one config-backed ephemeral Queue opening
100 validation connections and 100 runner connections. A persistent Queue opened
zero additional connections after its initial operation. The prototype initially
used a missing config file (which additionally initialized the target on every
operation); the corrected probe touches that file and measures the steady state.
The harness completion wait, terminal-state wait, and registry drain retain their
queue handles across repeated operations but explicitly request ephemeral sessions.
Change these three handles to persistent sessions and retain their existing finally
closure. No Queue default, pool policy, timeout, or worker count changes. Parent
independent review approved this narrow lifecycle slice before implementation.
Real PG regressions inject terminal events after a fixed number of real scans,
count physical connection opens, and verify queue closure on success and failure.


The diagnostic-enabled PostgreSQL suite stopped after 4284 passes and 23 skips with one setup error: `test_system_load_alias_conflict_uses_exit_3` could not open a TCP connection (`Can't assign requested address`). The failing call was fresh schema inspection in harness entry, so that test is the allocation victim, not proof that its alias behavior is defective. Its log is `/tmp/weft-pg-isolated-diagnostics.log`. The server had allocated roughly 97,000 backend process IDs during the run; this is supporting churn evidence, not a precise connection count. Measured connection creation in the bounded probe above identifies excessive per-operation sessions. Runtime result-observation loops have the same retained-handle/ephemeral-session mismatch and are being repaired separately from fixture loops. The unchanged backend API already supports process-shared persistent sessions; no backend pool redesign is needed.

The corresponding application slice retains the same ephemeral handles in result
materialization, one-shot and persistent result waits, raw/realtime event followers,
interactive run log observation, and control-surface observation. Use persistent
handles only within these existing lifetimes. Same-target handles share the broker's
process-local session; they do not each require a new physical connection. Register
partial acquisition cleanup immediately and release the monitor before queue leases.
The controlling existing public SimpleBroker Queue contract recommends persistent
sessions where connection overhead matters; this repairs use of that contract and
adds no new product semantics. Spec 04 [SB-0.4] remains unchanged. Parent review
approved this bounded application slice before implementation. Real PostgreSQL
regressions must count actual connection opens across repeated observation and prove
owned handles close, including constructor/monitor failure and generator closure.

The liveness pagination regression also seeded 1,200 rows through one retained ephemeral queue. Its explicitly finally-closed fixture now uses a persistent session; the pagination size and all assertions remain unchanged. This removes per-row connection creation from setup rather than reducing the workload.

Bounded-wait slice verification: six real-PG regression cases failed before the
three persistent-session edits (four extra physical connections during two
repeated operations), then all six passed in 2.59 seconds. SQLite harness suite:
48 passed, six PG-specific skips in 15.81 seconds at default worker count. Scoped
Ruff lint and format passed. Parent independent review passed the real-I/O scan
injection, alias counting, shared-session use, and success/failure closure. The
owned probe container `weft-pg-churn-probe` was removed after verification. No
application wait paths or dependency pooling code were changed by this slice.

Application regression evidence: real PG event following opened 13 connections
versus an initial 5 across five successive events before repair; after repair it
opened none beyond its initial session. Eight new live-PG regressions passed,
including one-shot/persistent result wait reuse, final physical closure, partial
queue/monitor acquisition, and interactive constructor cleanup. The combined PG
result/realtime gate passed 82 tests. Six existing mocked lifecycle cases asserted
old ephemeral-handle flags; update only those flags, preserving their behavioral
and close-count assertions. Parent independent runtime review passed. Scoped Ruff
lint/format and mypy passed without new suppressions.

Integration review: parent reviewed all five application modules and the new regression boundary cases. Seven retained observation lifetimes now use broker-managed sessions; no polling intervals, task deadlines, result precedence, or worker counts changed. Six mock-based expectations that pinned ephemeral observation handles were updated to the repaired lifetime policy. Final root static checks pass: Ruff lint, 904-file formatting check, and mypy 186 sources. Full concurrent SQLite/PG verification remains in flight.


Final concurrent suite validation in the isolated checkout with the patched macOS
setproctitle dependency: `uv run pytest` passed 4715 tests, skipped 14, in 216.73
seconds; `uv run pytest-pg` passed 4560 tests, skipped 23, in 370.08 seconds. Both
used configured normal parallelism. The PG run reached its end with no socket
allocation error, although sampled late-suite TCP TIME_WAIT peaked at 15,449:
the targeted ownership repair is proven, not a claim that all suite connection
churn is eliminated. No OS settings, retries, or timeouts were changed.

The reproducible dependency diff was retained for validation at
`patches/setproctitle-1.3.7-headless-macos.patch`, based on upstream tag
`version-1.3.7`, commit `389ed6f4e3ccfebb34a38893553992ea10cf5dc8`. It added the
macOS LaunchServices opt-out and its upstream README documentation. It was never
installed into the main project or referenced by published dependency metadata,
and the file and directory have since been removed (see the dependency
disposition below). Current changes are
uncommitted. The earlier isolated work_failed event never preserved its cause;
five repeated PG module runs and the final suites did not reproduce it, so its
specific cause remains unproven despite the now-persistent failure diagnostics.

Final bounded stress: 13 selected startup, client, drain, and acquisition-failure cases passed with 24 workers in 4.22 seconds. Final Ruff lint/format and mypy checks passed again.

## Dependency Disposition (2026-09-10, Van)

Weft will not patch or fork setproctitle. The LaunchServices startup stall is
owned by the [deferred macOS process-title plan](2026-09-10-deferred-macos-process-title-plan.md),
which keeps unmodified stock setproctitle on every platform, adds unmodified
`processtitle` on macOS only, and defers GUI registration to a task's drive
turn after a one-to-three-second deadline so ephemeral tasks never register.
The `SPT_NO_LAUNCHSERVICES` opt-out described above was validation evidence
only; it never shipped and the retained patch file and `patches/` directory
were removed. The native samples and concurrency measurements in this record
remain valid evidence for the stall; the patch approach they validated is
superseded. No dependency metadata from this plan's patch investigation
remains in `pyproject.toml` or `uv.lock`.

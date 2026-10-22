# Interrupt-Safe Observation Wake

Status: completed
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/10-CLI_Interface.md [CLI-1.2]
Superseded by: none

Class: 4. Runtime observation cleanup crosses CLI and watcher threads.
Escalated from the Class 2 CPU workload correction after release CI exposed a
SIGINT deadlock. Plan type: implementation; no intended behavior revision.

## Goal

Preserve clean task-watch interruption without deadlocking activity wake cleanup.
Complete the user-authorized release and verify exact-commit CI and publication.

## Source Documents

Consulted `docs/program-theory.md` (WT-2, WT-7, ALT-WT-017),
`docs/agent-context/README.md`, `docs/agent-context/decision-hierarchy.md`,
`docs/agent-context/principles.md`, `docs/agent-context/engineering-principles.md`,
`docs/agent-context/lessons.md`, `docs/lessons.md`, and testing-patterns,
writing-plans, hardening-plans, review-loops-and-agent-bootstrap under
`docs/agent-context/runbooks/`. Apply `skills/test-audit/SKILL.md` and
`skills/call-agent/SKILL.md`.

Behavior owners: `docs/specifications/05-Message_Flow_and_State.md` [MF-5]
(queue observation, wake and close), `docs/specifications/10-CLI_Interface.md`
[CLI-1.2] (task status watch), and `docs/specifications/07-System_Invariants.md`
IMPL.8–IMPL.11 (event-driven waiting).

## Spec Baseline

Baseline: `7aea60c9` (CPU test correction). Preserve normative spec text;
add only this plan backlink and update nearby implementation rationale.

## Evidence and Causal Model

GitHub Test run 39705827919, job 119585723729 (macOS, Python 3.14, CLI),
failed `test_task_status_watch_sigint_exits_cleanly` after the child emitted a
heartbeat and received SIGINT. Parent communicate exceeded its unchanged ten
second limit; stderr was empty.

Local diagnostic: 300 copies of that unchanged test at 12 workers, with
child-only faulthandler enabled, produced 1 failure and 299 passes in 23.24s.
The failed child's sole thread was blocked at threading.Condition.__enter__
inside Event.set, called by QueueChangeMonitor.close -> wake. A separate
child showed a bounded broker-session drain, not the deadlock being repaired.

CPython Event uses `with self._cond`; Condition.__enter__ is a Python wrapper
around native Lock.__enter__. SIGINT after native acquisition but before the
wrapper returns leaves no registered caller cleanup and strands the lock.
The later close->wake tries to acquire that same nonrecursive lock forever.
A deterministic trace injection at that wrapper's return reproduces the
stranded lock. No increase to timeout or reduction in contention fixes it.

## Context and Key Files

Modify `weft/core/queue_wait.py` and `tests/core/test_queue_wait.py`.
Update the cleanup hook in `integrations/weft_django/tests/test_weft_django.py`
to public `wake()` and the activity description in `weft/commands/_result_wait.py`.
Read `weft/cli/app.py::_task_watch_interrupt_scope`,
`weft/commands/_result_wait.py::_iter_consuming_result`,
`weft/commands/_resources.py`, and `tests/cli/test_status.py`.
Update this plan, `docs/plans/README.md`, and the [MF-5] implementation note/backlink.

QueueChangeMonitor owns native/fallback watcher custody, failure latching,
coalesced wake state, wait and close. Preserve that owner and public methods.
Do not modify SimpleBroker, Python stdlib, CLI signal handling, or session
cleanup as part of this repair.

Comprehension answers before implementation:
1. Why not replace Event's private condition lock with an RLock? That can hide
   same-thread deadlock while retaining a recursion level and blocking watchers.
2. Use a binary wake token implemented by one native Lock, initially acquired.
   Wake releases it (already released means a pending wake); wait acquires it.
   No mutual-exclusion lock is held across Python activity-wait code. Wake never
   blocks, even if interruption occurs immediately after token consumption.
3. Each production construction site has one observing loop, with multiple wake
   producers. The token preserves that usage, not Event's incidental broadcast
   to multiple simultaneous observers. [MF-5] defines no multi-consumer broadcast
   contract. Keep the single observing caller explicit in the class docstring.
   Close/failure remains durable and each subsequent wait rechecks it.

Related precedent: the retired `2026-09-21-watcher-sigint-lock-safety-plan.md`
(index retirement ledger, commit `396c3b85`) removed lock-taking calls from a
signal handler. This repair removes a lock-taking cleanup wake.

## Invariants and Hardening

- Queue subscriptions, durable evidence, registration-before-read, and the
  TaskSpec/Manager/Consumer/runner spine do not change.
- Wake remains coalesced and thread-safe; a wake preceding wait is retained;
  timeout and close return semantics stay intact; observer failures still raise.
- Close stays idempotent and wakes a waiting observer. No periodic polling,
  new thread, resource type, signal handler, dependency, or public flag.
- TIDs, forward-only states, reserved policies, immutable spec/io, spawn usage,
  runtime-state dump exclusions and broker target ownership are untouched.
- The activity token is not a mutual-exclusion lock. State locking, broker I/O
  and thread joins remain separate; no new lock-order dependency.
- Rollback is a normal code/test revert; no stored format or rollout ordering.
  It restores the known interruption hazard. Release only after regression,
  full local checks and exact-commit CI; watch publish workflow results.
- External inputs and outputs are unchanged. No one-way door before the
  user-authorized package publication. No provider/cache/queue redesign.

## Implementation Steps

1. Independent plan review before runtime edits. Verify the causal premise and
   deterministic injection. Add a subprocess-bounded regression that delivers
   KeyboardInterrupt at Python Condition entry while QueueChangeMonitor.wait
   executes (or at a safe wait boundary if no wrapper is entered). After
   interruption, another daemon thread must finish wake within a bounded join,
   wait must observe the wake and close must complete. The join assertion
   detects the old stranded lock; a parent subprocess timeout contains any hang.
   Keep existing real CLI SIGINT test unchanged as end-to-end proof.
2. Replace the activity Event with a native Lock, acquired at construction.
   Wake calls release and ignores RuntimeError only for an already pending token.
   Wait acquires with -1 for no deadline and max(0, timeout) otherwise, preserving
   Event's negative-timeout nonblocking behavior. Keep failure/stop checks.
   Adapt the idle-close test's entry witness using a test-only token proxy;
   preserve its wake, join and resource-close assertions. Run wake/close/failure
   tests, the regression, and repeated real SIGINT tests at normal concurrency.

3. Independent completed-work review; resolve accepted findings. Synchronize
   the implementation note and plan backlink, close plan/index together, commit
   explicit files. Rerun `bin/release.py all` without skipping checks. Monitor
   Test and release gates through gh until all selected packages publish.

## Verification

Load `. ./.envrc` for repository-managed tooling.

- Regression must fail on the original monitor with the explicit cross-thread
  wake-blocked assertion after Condition entry injection, not setup failure;
  then pass with a consumed wake and completed close after interruption.
- `./.venv/bin/python -m pytest tests/core/test_queue_wait.py tests/cli/test_status.py tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py`
- Repeat the actual SIGINT test with `--keep-duplicates` and standard 12-worker
  settings; no assertion, timeout or concurrency relaxation.
- `./.venv/bin/ruff check .`; changed-file format check and repository mypy via
  the release helper; `git diff --check`.
- `bin/release.py all`: full core, PostgreSQL, integration/extension, provider,
  static and build checks. Failures are diagnosed before corrections.
- `gh run view` on the exact commit's Test and package release gates; confirm
  publish-to-PyPI and GitHub Release jobs and release assets.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |

## Review Log

Independent Tier-1 review: Claude, read-only tool set Read/Grep/Glob, plan
permission mode; baseline 7aea60c9. Review completed with one P1 and six P2s.

| Finding | Disposition | Operative change |
| --- | --- | --- |
| P1-1 native context-entry safety unproven on 3.14 | accept | Replace Condition design with native binary token; no acquired mutual-exclusion lock crosses Python boundaries. |
| P2-2 binary token | accept | Step 2 and comprehension answers now specify token and single-observer scope after inspecting all construction sites. |
| P2-3 Condition.wait interrupt gaps | accept | Remove Condition design entirely. |
| P2-4 RLock rationale | accept | Remove RLock design. |
| P2-5 regression precision | accept | Match Condition.__enter__ code identity; assert interrupt, cross-thread wake, consumption and bounded close; POSIX-only child. |
| P2-6 idle-close witness | accept | Test-only proxy around native token, unchanged completion assertions. |
| P2-7 verification | accept | Update wake docstring and MF-5 backlink; run on available Python 3.14. |

Publication remains explicitly user-authorized, so the review's optional request
for another release confirmation is not adopted. The preexisting broader async
exception risks outside activity wait remain out of scope.

Round 2 Tier-1 review: PASS. Accepted P2-A (Django private Event cleanup becomes
public wake), P2-B (seed a wake before injection to cover interrupted token
consumption), P2-C (synchronize result-wait docstring). Repeated RED with seeded
wake: `injected:enter`, `cross-thread wake blocked`, one failure in 2.94s.

## Execution Record

Regression RED on Python 3.14.4, standard 12 workers: one failure in 2.92s.
The child printed `injected:enter` and failed `cross-thread wake blocked`.
It did not time out or fail during import/setup.

Additional Class 2 CI test corrections (same failed Test run, Windows 3.12
core-commands job 119585723832):

- `tests/core/test_manager.py::test_process_once_launches_service_spawn_after_shared_self_write_wake`:
  [CC-2.1] in `01-Core_Components.md` and [MA-1] item 1 in
  `03-Manager_Architecture.md` require self-write wake to discover inactive queues.
  They permit same-turn fallback discovery. Expiring the real inactive deadline
  immediately after service publication reproduced the exact first-turn failure.
  Set the test's periodic deadline to infinity so fallback cannot substitute for
  its intended local-wake path. Preserve all launch assertions.
- `tests/core/test_control_probe.py::test_send_keyed_ping_probe_routes_pong_to_ephemeral_ctrl_in`:
  [MF-3] in `05-Message_Flow_and_State.md` and [MANAGER.8] in
  `07-System_Invariants.md` own keyed requester routing/retirement. A real PONG
  published 1.261s after PING reproduces the one-second timeout; historical CI
  causation remains unproven because publication timing was not recorded.
  Make the routing test publish its real PING and consume/respond through real
  queues before the production deadline begins. Keep malformed-row filtering,
  exact wire identity, matching and queue-retirement assertions. Existing timeout
  and late-PONG tests keep deadline coverage. No runtime or timeout change.

These test corrections were independently diagnosed by a native exploration
subagent; completed-work cross-family review will cover the actual edits.

Verification before release commit:

- Focused monitor, CLI status, probe, Manager and spec hygiene: 381 passed,
  4 backend-specific skips, 17.03s.
- Wake/negative-timeout/coalescing and Django disconnect cases: 16 passed, 1.63s.
- 300 unchanged real CLI SIGINT tests on Python 3.14.4 at standard 12 workers:
  300 passed, 21.73s.
- Repository lint and formatting clean; mypy: 478 source files, no issues.
- First full release invocation (CPU correction baseline) passed all local
  suites/builds, then correctly stopped on Test run 39705827919 failure without
  publishing. Full release invocation will run again on this repair commit.

Completed-work Tier-1 review: PASS, no P1, five nonblocking P2s.

| Finding | Disposition | Evidence/change |
| --- | --- | --- |
| P2-1 index count should be 47 | reject | Filesystem count excluding README is 48; git baseline has 47 plans plus README. The old declared 46 was stale. Keep measured 48. |
| P2-2 sibling cleanup probe test latency | accept | Share the real write/respond helper in both routing and cleanup-result tests; remove the threaded helper and its unused scheduling machinery. |
| P2-3 coverage scope wording | accept | These two tests cover replies already present before wait; end-to-end PING tests retain asynchronous arrivals. No wake-latency oracle was removed. |
| P2-4 concurrent wait limit | accept | Class docstring explicitly states one observer and durable close/failure. |
| P2-5 close plan/index on commit | accept | Close both together when landing this slice. |

The native token also atomically consumes a wake, eliminating the old separate
wait/clear race. This strengthens coalesced notification without changing the
normative durable-state recheck contract. Broader repeated-SIGINT hazards during
other cleanup code are preexisting and not claimed fixed by this change.

Completed-work round 2: PASS, no blockers. The shared responder keeps the actual
write, matching loop and cleanup owner intact. Two preexisting observations
(no-op retirement in the cleanup fault test; self-referential reply_to equality
supplemented by route/retirement checks) are unchanged and outside this repair.
Final focused probe/monitor/spec run: 88 passed, 1 backend-specific skip, 1.31s.
Implementation and review are complete for this landing commit. Full release
checks, exact-commit CI and publication remain post-commit gates; no success is
claimed for them in this pre-release record.

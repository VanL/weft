# Stress Admission with Nested Work

Status: completed
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.8]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]; docs/specifications/05-Message_Flow_and_State.md [MF-1], [MF-5]; docs/specifications/08-Testing_Strategy.md [TS-0], [TS-1]; docs/specifications/07-System_Invariants.md MANAGER.18
Superseded by: none

Class: 5. Escalated from 3 for the workload to 4 for the admission hot-path
change, then to 5 for result-wait resource ownership. Baseline b6d850c1 plus
the pre-existing uncommitted scheduler and registry repairs. The spec baseline
is b6d850c1. The first runtime optimization changes no admission policy.

## Goal and source documents

Exercise a backlog of 600 ordinary roots. The original 500 include 250
single-child families. Add 100 chains with three, four, or five parent-child
edges, for 1,250 tasks in total. Work durations cover 0.5 to 5 seconds at every
level. Parent wall time can also include waiting for its child. Use the same
deterministic schedule on both backends.

Consulted the program theory, particularly [WT-2] items 4–6, [WT-4.2],
[WT-4.4], [WT-7], [ALT-WT-001], [ALT-WT-017]; the agent-context README,
decision hierarchy, principles, engineering principles, testing and review
runbooks, and lessons; and the test-audit skill. [MA-1.8] measures PostgreSQL
usage against native capacity and a reserve, and makes SQLite admission
opt-in. This workload observes those contracts and prices the tradeoff between
throughput, server headroom and local process pressure. It is a benchmark
of one workload, not a universal progress or peak-rate guarantee.

## Test plan

1. Add one shared slow test in `tests/system/test_admission_stress.py` and a
   spawn-importable workload in `tests/helpers/admission_workload.py`.
   Use the canonical exact-ID spawn writer to preload all roots on one broker
   lease, then launch a real Manager. Each parent calls `client.submit`
   under its actual runtime identity before doing timed work, then calls
   `child.result` through the normal result observation path. Close the
   one-shot submission client before work or wait.
   Leaves do timed work only. Use a deterministic mixed order and fixed
   duration range, with no test concurrency cap or launch waves.
2. Collect the 600 root outputs from a test-owned output queue. The outputs
   identify the descendant chain. Require the exact root indexes and depths,
   distinct descendant TIDs, the expected submitter for each launch, and one
   launch, work start and task-owned successful completion for each of the
   1,250 tasks. Preserve task lifecycle evidence for diagnostics. Use the
   Manager's drained public source, reservations and ordinary residents as
   the producer boundary for exact counts. Wait for outputs and drain
   within one absolute deadline.
   Timed work is the workload, not a readiness wait. Own all test processes
   and leases through teardown.
3. Request the existing `isolated_postgres_server` fixture dynamically
   only for the PostgreSQL backend. Expose its container name. Configure
   native max_connections=300 with automatic Weft capacity. Use the default
   reserve for the baseline, with the existing environment override available
   for policy comparisons. Sample global client sessions with one independent
   autocommit SQL connection. After harness cleanup and before the fixture removes the
   container, inspect its logs for slot-rejection signatures. Fail even
   when retries later complete the workload. Sampled peak is a lower bound,
   while the server enforces the hard limit. Measure elapsed time from
   Manager launch to receipt of all root outputs, excluding preload and
   teardown. Report task throughput without a machine-dependent speed
   floor.
4. Run the same workload on SQLite with the default disabled gate. Use the
   existing optional limit for a comparison if resource evidence warrants
   it. Price process concurrency and elapsed time before recommending a
   default limit. Do not transpose PostgreSQL's connection count to SQLite.
5. Run a second PostgreSQL leg with 250 independent idle client sessions
   and a 5% reserve by default for this case. Keep pressure until at least
   100 root outputs and one depth-five chain complete. Require a sampled
   peak of at least 280 sessions while pressure is still held. Release
   while the backlog is live. This proves a full nested chain can drain
   under real near-ceiling pressure, then tests progress as demand leaves.
   A 90 s interval without a new root result fails the held-pressure phase.
   The workload, durations, and native limit stay fixed in each comparison.
6. Diagnose any connection failure or stall under the original full load
   before changing production. Report peak running work, peak unsettled
   startup observations and checkpointed refusal reasons. If utilization
   is low, use this and targeted timing to distinguish the readiness
   window from resource exhaustion. Compare repeatable evidence with the
   same workload and normal pytest concurrency. Do not tune the reserve or
   startup window on the basis of a single raw peak.

Existing PostgreSQL tests hold controlled pressure and cover gate decisions
with small, resource-calibrated topologies. They do not cover sustained
process and session churn through a large mixed backlog. This test should
fail on lost/duplicated work, stalled progress, or a rejected connection
even when a driver retries it. It uses the real admission controller,
launcher, result API and broker. No fixture replaces their decisions.

## Admission hot path

The owner permits changing admission policy, including startup overlap and
reserve. Evaluate throughput and correctness independently. Never relax the
physical server limit, the full workload, or the native slot-rejection oracle.
The workload cannot prove future connection demand from other clients.

The PostgreSQL Manager profile attributed 13,235 calls and 90.65 s of
inclusive instrumented time to `Manager._admission_backend_name`. The live
source uses `Queue.backend_name`, which resolves the activity identity and
discovers the installed PostgreSQL plugin on each access. Backend identity is
already bound by the Manager's initial Queue target
(`MultiQueueWatcher.__init__`) and retained in the immutable runtime
`WeftContext` (`BaseTask._task_context`). Read the existing bound backend
name through `Manager._manager_context().backend_name`. Add no cache or
fallback. Preserve all fresh capacity and usage observations and their
failure handling. The change does not affect broker selection, exact-ID
reservation, task handoff, scheduling order, or restart evidence.

Use the full workload as the throughput and correctness gate. Measure the
same pressure schedule with a smaller reserve in an isolated trial before
choosing any new default. Evaluate startup overlap only if the current
window is a measured limit after removing repeated discovery. A single
successful configuration is evidence for this workload only. Stop and
re-evaluate if capacity failures appear, measured use no longer gates
launches, or an experiment requires new coordination or persistence.
Rollback restores the single backend-name lookup. There is no data or
rollout sequencing change.

## Release the result reader's data connection between waits

The 1,250-task PostgreSQL run with 250 background connections and a 5%
reserve stopped at 67 root outputs and 153 completed tasks. Eight waiting
ancestors from two chains each retained two LISTEN sessions and two
idle data sessions across the wrapper and worker. The Manager published
measured exhaustion at 287 against a public limit of 285. Both pending
child tips were leaves. A 2.5% trial hit native slot rejections. That makes
shortening the lifetime of a real connection preferable to expanding
admission against the same idle footprint.

The fork-join analogy is Cilk's work-first principle. Remove overhead from
the repeated path, and let a ready descendant complete its live dependency
path. The distinction here is that an OS process waiting on a child can
retain a scarce database session. The queueing consequence follows Little's
law: longer resource residence raises occupancy at a given completion rate.
[Cilk-5](https://pages.cs.wisc.edu/~markhill/restricted/pldi98_cilk.pdf),
[Little's law](https://doi.org/10.1287/opre.9.3.383).

A TCP-style in-flight window limits work that has started but has yet to be
observed. PostgreSQL slot rejections are a failed run, so loss feedback
cannot serve as ordinary control. Dijkstra's Banker requires a declared
maximum resource demand. Weft's arbitrary task processes do not supply
one. A hard no-rejection guarantee across unrelated clients would require
coordination at physical connection acquisition. PgBouncer supplies that
kind of queue for its clients, with different support for session-local
features such as LISTEN across pooling modes.
[TCP congestion control](https://datatracker.ietf.org/doc/rfc5681/),
[Banker's algorithm](https://www.cs.utexas.edu/~EWD/transcriptions/EWD06xx/EWD623.html),
[PgBouncer connection limits](https://www.pgbouncer.org/config#max_db_connections),
[PgBouncer pooling modes](https://www.pgbouncer.org/features.html).

Reuse `weft/commands/_result_wait.py::_iter_consuming_result` and its
`result_observation` subscription owner. For a runtime task consuming a
configured PostgreSQL result without live output or lifecycle following,
subscribe through ephemeral Queue facades. Use one short-lived persistent
Queue across each reading pass. End that scope before the monitor waits.
The listener remains subscribed and can accumulate a wake while the data
connection is closed. Allow any caller-owned session to retain its own
resources. Route changes, output claims, terminal settlement, the original
deadline, and cleanup order keep their existing owners. A configuration
known at entry stays known during acquisition. The unconfigured wait path
continues to use the current bounded observation owner.

Use the measured 5% reserve trial and the same fixed external pressure as
the regression comparison. Require progress through the deepest chain and
completion of the entire workload, with native zero rejection evidence.
Run existing connection and settlement tests on both backends. Stop and
re-evaluate if the listener needs a pinned data lease, the release introduces
rejections, or the larger workload still stalls at held pressure.
Rollback restores the original observation scope and corresponding spec
paragraph. No queue, persisted format, client option, or rollout ordering
changes.

The retained reader subscription owns the existing session lease. Route
replacement acquires its new subscription before closing the old one.
Remove the enclosing duplicate session lease from the consuming loop.

## Proposed Spec Delta

Promotion strategy B (atomic). Revise the bounded observation paragraph
under `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4].
Replace the paragraph beginning “The bounded observation owners”.
Promotion baseline: b6d850c1 plus the worktree [SB-0.4] delta and
`weft/commands/_result_wait.py` change reviewed below.

### [SB-0.4] in `docs/specifications/04-SimpleBroker_Integration.md`

Replacement:

```markdown
The bounded observation owners in `weft/commands/_result_wait.py`, `result.py`,
`events.py`, `run.py`, and `tasks.py` normally retain persistent queue leases
until their existing close/finally boundary. A runtime task collecting
only a final PostgreSQL result with routes known at entry retains its queue
activity subscription while waiting and owns a data connection only for
each observation pass. It releases that pass's lease
before waiting and preserves any caller-owned session. The next wake
reacquires a data connection for fresh evidence. Regression coverage lives in
`tests/commands/test_observation_connections.py` and
`tests/system/test_admission_stress.py`. See the
[nested admission load plan](../plans/2026-11-07-nested-admission-load-plan.md)
and [the dependency suite repair plan](../plans/2026-09-09-updated-dependency-suite-repairs-plan.md).
```

## Large-workload fixture cleanup

OS samples of the post-workload pytest worker found repeated psutil ancestry
probes in the existing historical mapping cleanup. Keep all mapping
history and the exact identity recheck before any signal. During each
`WeftTestHarness._collect_pid_mappings` pass, fold the existing historical
process pairs by PID using the last encountered evidence, matching the
existing final value in `_registered_pid_create_times`. Register each PID
once per pass. Skip testing a mapping's owner for TID registration if the
TID is already registered. These changes remove repeated guard work while
preserving discovery of historical worker PIDs absent from newer snapshots.
Run the existing harness registration tests and the full workload. Rollback
restores the helper loop without changing test-owned data or runtime cleanup.

## Verification

```bash
. ./.envrc
WEFT_ADMISSION_MAX_CONNECTIONS=0 PYTEST_ADDOPTS=-rP .venv/bin/python -m pytest -m slow tests/system/test_admission_stress.py
env -u WEFT_ADMISSION_RESERVE_FRACTION PYTEST_ADDOPTS=-rP ./bin/pytest-pg tests/system/test_admission_stress.py
.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_test_audit_policy.py
.venv/bin/python -m pytest tests/core/test_admission.py tests/core/test_manager_family_maintenance.py tests/core/test_admission_recovery.py
bin/check-doc-paths
git diff --check
```

Check lint, formatting and types on touched files. Review plan and completed
work independently. Record metrics and any fault evidence below. Test rollback
removes the test files, the fixture return-value change and this plan with
its index entry. Existing scheduler/registry edits are outside this plan's
scope.

## Deviation log

The first PostgreSQL run reached 111 sampled sessions. It completed all
750 tasks in 126.13 s (5.95/s) with no rejected connections. This exercised
startup overlap but did not put the public capacity gate under pressure.
Add a second case of the same test with independent background pressure.
A pilot that held 250 sessions for the full run repeatedly observed the
capacity barrier and made sustained progress (214 completions before
intentional interruption), with no slot rejection. Holding that budget for
the entire run would chiefly measure the smaller fixed workload allowance.
Release the background sessions after the first 100 root results so the
same run also verifies recovery from external pressure.

The owner asked for harder nested pressure. Retain the 500 roots and add
100 depth-three-to-five chains, including 33 at depth five, to the same
backlog. Start the first depth-five chain at the head and mix the remaining
families in deterministic order. Release pressure only after evidence of
a deepest completion and of at least 280 simultaneous client sessions.
The larger workload has a 600 s absolute work-and-drain deadline.

## Review and execution record

Plan review, Gemini 4 Argon Medium, 2026-11-07. Accepted the blocking
conditional-fixture finding and the refinements on autocommit, server-log
ordering, lineage and completion proof, clock boundaries and rollback.
`bin/pytest-pg` passes `-m shared` explicitly, so its targeted command
selects this shared slow test. Manager snapshots expose refusal reasons as
observations rather than one row per deferred dispatch. The test reports
checkpoint counts with that scope.

The initial SQLite run at the disabled default finished in 116.75 s
(6.42/s) with 33 peak running tasks and one unfinished startup. Run a
positive ceiling as an explicit comparison before recommending any limit.

Completed-test review, Gemini 4 Argon Medium, 2026-11-07. No blocker.
Documented that SQLite intentionally allows its existing environment
override for ceiling comparisons. The baseline command fixes the ceiling
at zero. The first drain-proof runs revealed a fixture error after all
root outputs arrived. The broker-level `peek_one` defaults to including
timestamps. Read the snapshot body with `with_timestamps=False` and rerun.
The explicit shared-test inventory also lacked the prior scheduling model
test. Registered the existing module and reran the metadata and test audit
checks.

Runtime plan review, Gemini 4 Argon Medium, 2026-11-07. No blocker.
Verified that the initial Queue already binds the backend and the existing
task context carries that exact target. Ran the focused admission,
Manager maintenance and recovery tests to verify the lookup change at
normal call sites. 138 tests passed including the metadata and test audit
checks. The isolated backend-name comparison over 500 reads took 1.185 s
through Queue discovery and 8 microseconds through the bound target.
The native rejection oracle caught the deliberate 301st connection attempt
on a separate disposable server.

The 750-task pilot reached 276, 290 and 296 sampled sessions under pressure
with 10%, 5% and 2.5% reserves respectively, with no native rejection in any
of those runs. The first 100 root results took 159.78 s, 68.19 s and 67.60 s.
The 2.5% case used six more slots for a similar pressured completion time.
The width-12 trial peaked at five unsettled startups. Host load varied, so
these trials do not establish a global throughput ranking. Use 5% in the
pressured test for the 300-slot server. The production defaults are unchanged.
SQLite at 24 live/unknown TIDs completed the 750-task pilot in 175.10 s
with 19 peak running tasks. The uncapped run took 170.60 s and peaked at 48.
Keep the SQLite cap opt-in for workloads with a specific host resource limit.

The scoped cleanup review and the combined completed-work review returned
no blocker for the pilot. The harness registration and metadata run passed
73 tests. The focused PostgreSQL admission run passed 133 tests.

Result lease plan review, Gemini 4 Argon Medium. Initial dispatch produced no
response because a file-read request was denied in plan mode. The inline-only
retry raised a blocker on the omitted middle of the observation loop and two
lease/scope questions. The complete-loop round two passed. The read and claim
operations use the same pass owner, watcher replacement preserves the mode,
the shared session registry retains independent owners, and the spec now
scopes the behavior to routes known at entry. The original depth workload
failed after 67/600 roots at 293 sampled sessions. The scratch 5% run
completed the held-pressure phase after releasing idle result data leases.
The 2.5% trial on the larger workload hit 300 sampled sessions, logged
163 native connection-slot rejections and timed out at 596/600 roots.
The earlier 750-task result did not expose that failure.

The full SQLite workload with admission disabled verified all 1,250 tasks
in 277.00 s (4.51 tasks/s), with 55 peak running tasks and one unsettled
startup. This run preceded the PostgreSQL-specific result lease change.

Completed-work review, Gemini 4 Argon Medium, 2026-11-07. No blocker.
Verified the result lease scope, subscription lifetime, exact nested shape,
the real server pressure and native rejection oracle, and the drained
producer boundary before lifecycle counts.

Both final PostgreSQL cases passed on isolated 300-slot PostgreSQL 18.4
servers, with exactly 1,250 launches, starts and successful completions
per case and zero native slot rejections. The default baseline used a
10% reserve, finished in 382.47 s (3.27 tasks/s) and peaked at 149 sampled
sessions and 56 running tasks. The pressured case used a 5% reserve,
held 250 background sessions for 219.32 s, finished in 495.15 s
(2.52 tasks/s) and peaked at 291 sampled sessions and 57 running tasks.
Both cases peaked at five unsettled starts. The pressured Manager carried
the measured capacity refusal in 247 checkpoints. Elapsed work timings
exclude preload, final evidence checks and teardown. Both cases ran with
the regular concurrent test configuration.

The focused result suites initially had Consumer startup/work fixture
timeouts while the stress runs were active. The SQLite rerun after the
stress workers drained passed 321 tests with three expected PostgreSQL-only
skips. The focused PostgreSQL rerun passed 251 tests. Ruff, mypy,
`bin/check-doc-paths` and `git diff --check` passed.

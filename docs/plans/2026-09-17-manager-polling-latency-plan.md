# Manager Polling Latency

Status: draft
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.6a]; docs/specifications/01-Core_Components.md [CC-2.1]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]; docs/specifications/05-Message_Flow_and_State.md [MF-3]
Superseded by: [Watcher Reactor Restoration](./2026-09-17-watcher-reactor-restoration-plan.md)

Class: 5. Explicit manager fallback scheduling contract; hardening applies to
reactor execution on the durable spine.

## Goal and Outcomes

- Bound each manager fallback sleep to 50 ms, including native-waiter failure.
- Preserve shorter deadlines, native notification waits, immediate work, and
  non-manager scheduling.
- Measure real idle cost and PING latency; preserve earlier submission changes.

## Spec Baseline

Committed baseline: `3faeff21f527bbd32c8ccfb53d662a68a0ccb024`.
Spec 03/04 already have submission-cost implementation notes from the preceding
slice; retain those additions. This change revises spec 03 [MA-1.6a].
Promotion strategy A: promote the reviewed contract before code; existing
ownership mapping remains valid and receives a nearby implementation note.

## Current Structure and Required Reading

Read the cited specs, 07-System_Invariants [IMPL.10], runtime-and-context-patterns,
testing-patterns, writing-plans, hardening-plans, and review-loops-and-agent-bootstrap.
`BaseTask.run_until_stopped` passes Manager.next_wait_timeout to the shared wait.
The manager's timers often request one second. MultiQueueWatcher has native
notification waits, but its fallback only checks pending once before Event.wait.
SQLite lacks a native waiter. Manager turns drain control before spawn; the
manager-specific pending check suppresses blocked spawn and stalled control lanes.
Keep those checks intact. Why must the cap apply after native-waiter selection?
Why must shorter/zero deadlines and blocked-lane suppression survive?

## Proposed Spec Delta

Append to spec 03 [MA-1.6a], after the sentence about prompt queue activity wake:

> When the manager has no usable native queue activity waiter, each fallback
> sleep is capped at 50 ms, or the requested timeout if shorter. This
> also applies after native-waiter failure. The cap bounds intentional idle
> sleep, not end-to-end dispatch or PING latency: scheduler delay, broker work,
> reactor work, and caller response polling can add time. Native notification
> waits retain the caller deadline. Housekeeping keeps its own due timers;
> the polling cap does not accelerate leadership or service audit intervals.

Append to spec 05 [MF-3] keyed PONG rules:

> Caller-side keyed PING probes poll for replies with sleeps capped at 25 ms
> or the remaining probe timeout if shorter. This interval is separate from
> terminal-state and spawn-reconciliation polling. It bounds response-polling
> sleep, not end-to-end probe latency.

Use a dedicated CONTROL_PING_POLL_INTERVAL_SECONDS constant; keep the existing
CONTROL_SURFACE_WAIT_INTERVAL unchanged for task terminal/reconciliation flows.
Test real broker reply delivery with a module-local fake clock/sleep boundary,
including shorter remaining timeout and immediate PONG (no sleep).

No generic task latency guarantee or public configuration/API change.

## Invariants, Risks, and Scope

Keep one reactor, existing queue custody and drain order, deadline calculations,
stop-event interruption, zero/None no-wait semantics, pending precheck behavior,
blocked-admission suppression, and native waiter reset/error handling. No broker
internals, data-version cache, busy loop, new thread, TTL or schema change.
Extra manager turns can increase idle CPU and broker reads. Measure this rather
than asserting that 25 ms leaves a particular CPU idle fraction. Stop/reassess if
idle cost is unexpectedly large. The cap is no hard real-time execution SLA.

## Implementation and Verification Slices

1. Independent plan and exact delta review, then spec promotion. Add plan
   backlink and index row. Record promotion baseline. No production edits before
   review. Self-review: apply cap solely at the fallback sleep, not to manager
   next_wait_timeout (which would truncate native waits).
2. Add deterministic manager wait tests, red first: missing and failing native
   waiter use min(timeout, .05); shorter waits unchanged; zero/None return
   without probing; pending work avoids sleeping; native wait gets full timeout.
   Keep existing real-broker blocked-lane tests. Implement a private watcher
   class attribute `_fallback_poll_interval: float | None = None`; Manager sets
   it to a new `_constants.py` MANAGER_FALLBACK_POLL_INTERVAL_SECONDS = .05.
   The shared fallback applies min only immediately before stop_event.wait.
   Default None preserves ordinary tasks/watchers. Tests use fake wait events
   solely to record scheduling, never as evidence of broker delivery.
3. Run existing manager/watcher/base tests plus a real isolated manager PING
   trace (real SQLite and PostgreSQL). Measure idle process CPU/turns for a
   fixed quiet interval before and after; temporary scripts must clean up
   managers and brokers. Rerun full lint/type and docs gates. Independent
   completed-diff review, reconcile traceability and record results.

Files: weft/_constants.py, weft/core/manager.py,
weft/core/tasks/multiqueue_watcher.py, tests/core/test_manager.py,
weft/core/control_probe.py, tests/core/test_control_probe.py,
existing watcher tests as needed, specs 03 and 05, this plan and docs/plans/README.md.
Preserve all other dirty files from the preceding submission-cost task.

Commands (source .envrc first):
- ./.venv/bin/python -m pytest tests/core/test_manager.py tests/tasks/test_multiqueue_watcher.py tests/tasks/test_task_execution.py tests/core/test_control_probe.py
  (resolve existing watcher/base filenames before running)
- bin/pytest-pg --fast tests/core/test_manager.py tests/tasks/test_multiqueue_watcher.py
- ./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py
- ./.venv/bin/ruff check .
- ./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
- git diff --check; scoped ruff format check; repository backstitch check if configured.

## Rollout and Rollback

Restart managers to adopt scheduling. No rollout ordering, migration, new
resources or one-way door. Roll back only this slice's constant/attribute/cap
and tests/spec delta; preserve submission optimizations. Runtime success means
short queued-PING-to-handler delay while idle and acceptable measured idle cost.
Leave commits and push to explicit user instruction.

## Review and Deviation Log

Independent agent receives this plan, exact delta, source files and specs; must
review deadline preservation, native failure, default task behavior, idle cost,
and unnecessary abstraction. Author disposes of every finding before edits.
Final independent review covers the same surface after verification.

No deviations yet.

## Review Disposition

- Preimplementation reviewer: use requested timeout wording for manager cap,
  because existing native-failure fallback does not subtract time spent inside
  a failed native wait. Accepted; no new deadline-accounting behavior.
- User explicitly requested CPU comparisons on both SQLite and PostgreSQL and
  shorter PONG polling. Add the dedicated PING constant and spec05 delta above.
  Benchmark baseline, manager-only cap, and both changes, sequentially on both
  backends. Capture manager CPU and caller probe CPU; PostgreSQL server CPU
  should be measured separately when possible, not folded into client CPU.

- Independent revised plan/delta review: PASS. Accepted all path/test refinements.
- Promotion baseline: HEAD `3faeff21f527bbd32c8ccfb53d662a68a0ccb024` plus
  spec03 [MA-1.6a] and spec05 [MF-3] paragraphs below, applied in this slice.

## Revision: Measured Idle CPU Budget

User set a stricter goal: choose the shortest manager fallback interval with
measured idle cost below 2.5% of one core on SQLite and PostgreSQL. The 25 ms
manager delta is withdrawn pending measurements; its short-lived spec03
promotion has been removed. PONG 25 ms remains in scope. Sweep untraced
25/40/50/60/75/100 ms intervals with isolated real managers, then repeat the
selected candidate with margin. Record server/child CPU separately from manager
CPU. A measured CPU target is environment-specific, not a portable guarantee.
Re-review the resulting interval/spec delta before code implementation.

- Revised interval review: PASS for manager50 ms/PONG25 ms, with repeat
  measurements required before final choice. Untraced SQLite25sec sweep:
  baseline0.19%,25ms3.80%,40ms2.33%,50ms1.80%,60ms1.78% of one core.
  PostgreSQL native manager0.40-0.48%, server0.29-0.45%; native scheduling
  unchanged. PONG25ms lowers PG median from67.1 to37.8ms (12 probes/config).

- Promotion baseline revision: HEAD `3faeff21f527bbd32c8ccfb53d662a68a0ccb024`
  plus spec03 manager50ms paragraph/implementation note and spec05 PONG25ms
  paragraph. Independent delta review PASS before promotion and code.
- Red test run: manager fallback cap failed four long-timeout cases, PONG
  interval failed one long-timeout case; short-timeout cases passed. Evidence:
  `/tmp/weft-wake-red.log`, pytest manager/control_probe with caps selectors.

## Final Measurement and Selection

Local environment: Apple M4 Max, macOS, Python 3.14.4; SQLite local and
PostgreSQL 18 in an isolated Docker container. Sequential idle runs use isolated
WeftTestHarness managers, disabled idle shutdown, three-second startup settling,
and process user+system CPU deltas over 25-30 seconds. No per-wakeup trace I/O
in the SQLite selection sweep. No managed child process CPU was observed.
Percentages mean percent of ONE core, not percent of all machine cores. These
are measured local averages, not a portable utilization or scheduling guarantee.

| SQLite fallback | Initial idle CPU | Repeat idle CPU |
| --- | --- | --- |
| Existing due-timer fallback | 0.19% | not repeated |
| 25 ms | 3.80% | not repeated |
| 40 ms | 2.33% | 2.13%, 2.23% |
| 50 ms | 1.80% | 2.06%, 2.13% |
| 60 ms | 1.78% | not repeated |
| 75 ms | 1.25% | not repeated |
| 100 ms | 0.93% | not repeated |

Choose 50 ms for margin below the user's 2.5% budget. 40 ms also met the ceiling
in all three samples but left less headroom. The repeats used alternating order
40/50/50/40 and the actual production cap with a temporary shorter event-wait
interception for the 40 ms comparison. Startup and shutdown CPU are excluded.

PostgreSQL's native waiter retains its existing deadlines. Before/final manager
idle CPU measured 0.40%/0.53% of one core; database-container CPU measured
0.29%/0.31% separately (cgroup usage counter, includes server background work).
Both used 20-second quiet windows, with sparse trace I/O at about two waits/sec.
These small differences are not evidence of a causal native-wait overhead.
The earlier experimental 25 ms cap runs measured manager0.46-0.48% and
server0.39-0.45%; native scheduling stayed unchanged.

Final real keyed PING tests use no forced waiter or handler, real broker writes,
real manager subprocess, and ordinary caller reply polling. Before12/final24
probes deliberately sample differing idle phases; not production percentiles:

| Backend | Before median round trip | Final median round trip | Final median committed-PING to handler |
| --- | --- | --- | --- |
| SQLite | 539 ms | 31.6 ms | 15.1 ms |
| PostgreSQL | 67.1 ms | 40.3 ms | 1.57 ms |

Temporary experiment scripts/results (supporting evidence, not runtime code):
`/tmp/weft_idle_sweep.py`, `/tmp/weft-idle-sweep-results.jsonl`,
`/tmp/weft-idle-repeat-results.jsonl`, `/tmp/weft_wake_trace.py`,
`/tmp/weft_wake_cpu_pg.py`, `/tmp/weft-wake-cpu-before.json`,
`/tmp/weft-wake-cpu-pg-results.json`, `/tmp/weft-wake-final-sqlite.json`,
`/tmp/weft-wake-final-pg.json`. PostgreSQL runs used `bin/pytest-pg --fast`
with temporary pytest wrappers; all managers/containers were cleaned up.

## Verification and Final Review

- Independent static completed-diff review: no blockers. Preserved native waits,
  pending checks, blocked-source suppression, stop interruption, ordinary-task
  defaults and dedicated keyed-PING scope. Reviewer ran no tests during timings.
- Focused wait/PING regression selection: 20 passed after the recorded red run.
- Full repository Ruff passes; seven edited Python files pass format check;
  mypy passes all 434 source files. Final broad runtime/docs results follow.
- No standalone backstitch configuration exists in this checkout; configured
  traceability gates are tests/specs/test_spec_hygiene.py and plan metadata.

- Architectural reconsideration supersedes this direction. Preserve measurement
  evidence; the central reactor plan owns removal of the fixed-interval
  experiment while retaining the earlier submission resource optimizations.
- The experiment regression run passed 592 SQLite tests (4 PG-only skips),
  579 PostgreSQL tests (11 SQLite-only skips), and 6 final document gates.
  These passes do not establish shared event-source alignment.

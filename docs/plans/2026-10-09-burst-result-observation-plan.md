# Burst Result Observation and Session Setup

Status: draft
Source specs: docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/14-Python_API_Surfaces.md [PY-1], [PY-2]; docs/specifications/02-TaskSpec.md [TS-1]; docs/specifications/10-CLI_Interface.md [CLI-1.2.2]
Superseded by: [Result observation simplification](./2026-10-10-result-observation-simplification-plan.md)

Class: 5. This changes the result-observation execution path and normative
broker/client ownership descriptions. Hardening applies to result-observation and retained cleanup ownership. No public API, new queue, dependency, or broker changes.

## Goal and Scope

Historical approach: the successor replaces this plan's result-repair direction
and deliberately retains its session-owned spawn-facade behavior under [SB-0.4]
and [PY-1], including verification. The scope and review records below describe
the earlier narrowed approach, not current implementation instructions.

Stop parents replaying already-read shared task history while waiting for a
queued child. Keep the existing result API, generic route discovery, TaskSpec
interpretation, and result collectors. Reuse the existing session-owned queue
facade for retained submission. Prefer one local change at each existing owner
rather than a parallel submitted-handle result protocol.

- [x] Trace replay/setup owners and audit polling elsewhere.
- [x] Review this narrowed plan and exact spec delta with a fresh reviewer focused
  on simplicity, conceptual unity, and expert Python judgment.
- [x] Narrow the candidate to incremental generic discovery and retained
  submission-facade reuse, preserving current watcher and timer behavior.
- [ ] Verify correctness, resource ownership, both backends, and final simplicity
  review. Leave uncommitted unless the user requests a commit.

Only this repository changes. SimpleBroker findings are reported to the user for
separate action. Other polling sites are audit findings, not this implementation.
Task-specific discovery is a separate undecided
[placeholder](./2026-10-09-task-specific-result-discovery-placeholder.md).

## Revision Log

Reviewed prior plan revision: SHA-256
`4a310492eba460b7b7a451f89dde361daf8cdfc1a5bffe7a509a42f3837146d5`
of this plan before narrowing on October 9. Its round-2 plan review accepted a
larger bundle. The user challenged complexity, then requested a simplicity-first
fix and a separate discovery placeholder. The first narrowed revision removed submitted-handle
route custody, new launch-rejection semantics, and realtime observer fixes from
the active slice. It re-enters independent review before further code edits.
Fresh Claude review returned BLOCKED (F1 shared-monitor error blast radius;
F2 SQLite six-watcher multiplier; F3 metadata/result visibility race). After
source inspection, a second narrowing accepts the reviewer's recommendation:
keep existing watchers/subscriptions and timer bounds in this slice, fixing
replay and retained facade setup only. No new watcher subclass or observer
framework. Correct removal of application timers requires a separate
fan-in/failure-custody slice; the polling audit below remains its input.
First narrowed revision digest before this change: `e47d9bb3936c4ff3d53074b4468b7c519b09fb7a09a4770c41378b06b9158987`.
This second revision re-enters review scoped to accepted F1-F3 dispositions.
Historical review/deviation rows below remain a record of the earlier candidate,
not authority for the narrowed work.

## Spec Baseline and Promotion

Clean investigation baseline: `8085962af48c2ae38b8487fc449a4b11e0471170`.
Evidence at revision start: broader candidate edits were uncommitted. Initial promoted spec diff against
that SHA covered `04-SimpleBroker_Integration.md`,
`05-Message_Flow_and_State.md`, and `14-Python_API_Surfaces.md`; SHA-256 was
`8fc55e8b2db46934fcf8d1df22fd5f0697cd85f8b03745aa5852bcecc8f44139`.
A later reviewed cursor clarification changed it. This is historical evidence,
not the current promotion baseline.

Promotion strategy B: independently review the exact replacement below, then
replace only this task's uncommitted spec additions and corresponding code in
one owning slice. Retain unrelated work. Record the new diff-base and digest.
After promotion the specs are canonical; this delta is historical review text.

## Proposed Spec Delta (Exact Narrowed Replacement)

[SB-0.4], after the existing session lease paragraph:

> An entered client may retain one lazy, session-owned spawn-queue facade for
> its exact target/Config. Submissions borrow bounded Queue.get_connection()
> operations, ending before manager recovery or readiness waits. Session cleanup
> owns the facade; close failure and fork reset preserve existing custody rules.
> Warm reuse avoids repeating facade plugin discovery and schema setup, without
> extending transaction lifetime or bypassing SimpleBroker's first-use
> facade/schema validation.

[MF-5], after the existing result-materialization bullet:

> Generic result materialization reads shared lifecycle history once at startup,
> then uses one advancing message-ID cursor for TaskSpec and terminal evidence.
> Cursor progress includes malformed, non-object, and unrelated rows. Already
> scanned rows are not decoded on subsequent turns by that observation owner.
> After result activity appears without TaskSpec metadata, materialization
> advances that cursor once more before choosing collection mode, so metadata
> committed before the observed output is incorporated. It passes its cursor
> and observed completion evidence to the existing result collector. Realtime
> events retain their own startup lifecycle replay; their cursor is independent
> of result materialization. Separate claimed-result recovery remains bounded
> to actual claimed rows and is not repeated on empty-result turns.
>
> Shared lifecycle-log cursor completeness covers generated live runtime writes
> at the supported SimpleBroker dependency floors. Administrative exact-ID
> imports or ID-preserving moves into that log may backfill behind the cursor;
> start a fresh observation afterward. This does not restrict ordinary pipeline
> result moves. Generic discovery still decodes newly appended unrelated log
> rows.

[PY-1], replace only the retention sentence:

> Retention spans the session lease and its single lazy spawn-queue facade. It
> never spans a queue operation, backend checkout, SQL transaction, manager
> recovery, or readiness wait. Existing queue() and broker() ownership and
> defaults are unchanged.

Remove this candidate's submitted Task route/rejection additions from [PY-2]
and [MF-5]. Preserve baseline public behavior. Implementation mappings point to
result._await_result_materialization,
_streaming.poll_log_events,
WeftClient, and submission._submit_with_session. Add reciprocal plan backlinks.

## Invariants and Necessary Complexity

Keep the existing task and queue spine, TID immutability, queue-first submission,
TaskSpec spec/io immutability, reserved policy, process isolation, state/result
precedence, and caller-local timeout behavior. No new state cache, Task field,
route descriptor, persistence format, or public/private alternate result API.

The wait owns one cursor. The session owns one queue facade. These are the same
lifetimes that already own observation and submission. Do not retain operations
across manager recovery, waits, or external I/O. Never cross owner-thread or
process boundaries. Alternate runtime roots stay bounded and uncached.

Keep the existing watcher topology, subscriptions, and result timer/grace bounds.
Removing the application timer is deferred: it requires complete wake coverage,
shared-monitor failure policy compatible with accepted submissions, and proof
that portable observation does not multiply threads/connections per waiter.
The current slice reduces log replay, not the number of empty queue probes.

A positive result probe can race initialization: read log, child commits TaskSpec
then output, probe sees output. Before returning without metadata, perform one
additional advancing-cursor read and use any newly observed TaskSpec/terminal
proof. This closes the race without restarting history. Do not cache a synthetic
TaskSpec or create a submitted-handle path.

## Implementation Slices

1. Narrow the previously broader candidate and repair generic observation.
   Remove only our route descriptor, Task wiring, synthetic TaskSpec path,
   manager rejection reducer, realtime changes/tests, QueueChangeMonitor changes
   and result timer removals. Restore their
   baseline behavior, preserving facade work. Delete only this task's new
   route/rejection-only test file after its cases are out of scope; the user's
   narrowing authorizes removing this task's own uncommitted additions.
   In materialization use the existing poll_log_events stream for both metadata
   and terminal reduction, starting once at None and advancing thereafter.
   Keep the existing ResultMaterialization handoff; do not create another reducer
   type. Remove _TaskResultRoute, Task._from_submission, the alternate collector
   split, synthetic child_tid events, and rejection fields/parameters/source
   checks. Make raw cursor progress independent of decoding/filtering.
   Remove both uncommitted observer-failure/timer-removal paragraphs from
   [SB-0.4] and [MF-5], plus their _ActivityQueueWatcher mapping. In result.py
   restore its log-only materialization subscription and both interval caps.
   Keep baseline QueueChangeMonitor and subscription topology. After a positive
   default/pipeline result probe without metadata, reduce one fresh log suffix
   before returning. Cover that interleaving with real broker publication.
   Restore baseline core/test queue-wait files, constants, _result_wait.py,
   and the earlier realtime high-water changes/tests. In events.py retain only
   compatibility: realtime starts its own log cursor at None, rather than using
   materialization's result-collection cursor. Its existing terminal_state_emitted
   guard suppresses synthetic terminal output after a replayed terminal row.
   Add a real-broker test with initialization/running/completed rows present
   before observation, asserting all lifecycle rows and one terminal state. Remove only our new
   test_submitted_result_routing.py and logless-no-timer regression cases;
   retain cursor and facade regressions plus existing result semantics tests.
2. Retain one lazy spawn-queue facade inside the entered WeftClient session.
   Loan it through existing private submission helpers; close/re-entry/fork and
   rollback/core replacement remain owned by the existing lifecycle. No upstream
   edit or dependency upgrade. Independently review this ownership slice.
3. Verify and review the final narrowed diff.
   Use real broker queues and existing harnesses. Prove once-per-row history work,
   late default/custom/pipeline publication, actual deadline/grace behavior,
   terminal/error precedence, historical TIDs, persistent/streamed results,
   facade reuse and cleanup. Observer failure and timer removal are deferred. Run focused SQLite and canonical
   PG checks, full fast suite, type/lint/doc gates. Review complexity against the
   reduced owner model, not against the amount of code removed.

## Verification and Review

Required reading completed after the recorded initial process miss: the canonical
agent-context chain, runtime/testing/writing/hardening/review/adversarial runbooks,
both lessons files, Overview/Invariants, and owning spec sections. Continue using
those rules. Root owns edits/formatter/test runs; reviewers are read-only. Do not
edit while tests run. Bug regressions need an intended pre-fix failure and a pass
from the restored current candidate; existing red evidence is reusable only for
unchanged cases. New tests must pass the test-audit value bar.

Fresh reviewer brief: review this revision and proposed spec delta before code;
then review each owning slice and the full candidate. Ask whether an expert
Python developer could implement/sign off confidently, whether responsibility
and state remain conceptually unified, and whether each new construct is needed.
Prefer deletion/simplification over additional framework code. Disposition all
findings and re-review accepted fixes; do not trade correctness for line count.

Self-review: the narrowed model avoids synthetic TaskSpec semantics, a second
Task result path, shared-observer exception changes, and extra watcher threads.
Remaining risks are metadata/output ordering, cursor compatibility, and facade
cleanup. Real queue
coverage is necessary. Claude reviewed the narrowed plan twice. Its G1 consumer-compatibility amendment
received a scoped independent PASS before implementation. Final source review and
verification remain pending.

Load .envrc and use the repo-managed tools:

```bash
./.venv/bin/python -m pytest tests/commands/test_burst_result_wait.py tests/commands/test_result.py tests/commands/test_result_observation_connections.py tests/core/test_queue_wait.py tests/core/test_client_submission_facade.py tests/core/test_client.py tests/core/test_public_client_contracts.py tests/commands/test_submission.py
./.venv/bin/python bin/pytest-pg <the same focused files>
./.venv/bin/python -m pytest
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py
git diff --check
```

Use configured traceability tooling if available; no invented checker/config.
Record PG availability and measured results honestly. Count actual reader rows and
warm facade setup separately; do not promise historical 0.82s/12-13s timings are
explained. An isolated fanout probe must not become a new benchmark framework.

## Rollout, Rollback, and Follow-Ups

No migration or one-way door. Existing queues and readers coexist. Revert this
code/spec slice together if needed. Restart parents to pick up new code. Monitor
history decoding, wait latency, listener failures, and connection high water.

Out of scope: task-specific discovery (named placeholder), application timer
removal/observer redesign, launch-rejection API semantics, other polling repairs,
startup-wide setup changes, and admission
reservations. Report SimpleBroker facade-local setup and repeated schema/plugin
work to the user for separate action. Existing pool checkout, facade, listener,
and process lifetimes are distinct; do not label all connection demand a leak.

## Review and Execution Log

Independent plan review: required before implementation and spec promotion.
Same-family fresh-context review is available through collaboration agents.
Authenticated Claude CLI provided two independent read-only plan reviews.
A collaboration reviewer completed the scoped G1 consumer amendment review.
Reviewer must read proposed delta, baseline specs, ownership paths and tests;
answer PASS/BLOCKED under the canonical planning-review prompt. Disposition every
finding and re-review material changes. Review each meaningful implementation
slice and the final complete diff.

Author fresh-eyes review: checked queued-child, custom route, pipeline, imported
TID, listener death, cancellation, persistent boundaries, owner cleanup, and
validation lifetime. Added explicit generic full-pass limit, raw-row cursor rule,
typed observer failure, and no prompt/state retention to avoid false shortcuts.
Residual risk: submitted route parity and actual PG burst improvement require the
named real-backend tests; source inspection alone cannot establish them.

| Date | Work / evidence | Disposition |
| --- | --- | --- |
| 2026-10-09 | Clean baseline 8085962a; result/submission paths traced; independent polling and session audits completed | Investigation complete; independent plan review precedes implementation. User authorizes spec correction toward reactor design. |
| 2026-10-09 | Independent plan review: P1 missing rejection contract; P2 claimed-result one-off evidence replay | Accepted P1: exact child_tid matching, precedence and failed TaskResult rule added. P2: streaming-cursor guarantee explicitly excludes one-off claimed-row recovery; no repeated empty-turn recovery. Re-review requested. |
| 2026-10-09 | Independent plan re-review, round 2: PASS after P1/P2 dispositions | Plan and proposed delta accepted before implementation; same-family reviewer limitation retained. |
| 2026-10-09 | Initial spec promotion and implementation slices applied in the worktree | Result routes, advancing history, observer failure propagation, and retained submission facade implemented; uncommitted, verification incomplete. |
| 2026-10-09 | Initial regressions demonstrated nine baseline failures; focused candidate checks passed for burst replay, subscriptions, facade reuse, observer failure, realtime progress, and duplicate-TID rollback | Preliminary evidence only. New route/rejection tests and current-state broad checks still required. |
| 2026-10-09 | Independent facade review: no blocker; F1 missing loaned-facade rollback coverage, F2 missing constructor failure/core replacement/re-entry coverage | Accepted; agent added firing cases. Current-state verification and final whole-change review remain pending. |
| 2026-10-09 | User challenged required reading and execution process | Root had treated truncated reads as sufficient. Edits paused; full required chain, relevant runbooks, both lessons files and governing sections read before resumption. This remediation does not retroactively satisfy the pre-edit prerequisite. |
| 2026-10-09 | Independent reviewer accepted exact cursor clarification before promotion | Scope explicitly applies to the shared lifecycle log, not ordinary pipeline result moves. Promoted into [MF-5]; fresh historical replay remains required. |
| 2026-10-09 | User authorized continuation under repo rules and scoped plan to Weft | Reconcile ledger before further implementation; independent final source review resumed. SimpleBroker issues are reports for separate user action. |

## Deviation Log

The initial entry was: "None. No implementation or normative spec promotion has
occurred." It was accurate when drafted and left stale after edits began.

| Date | Deviation | Required action / disposition |
| --- | --- | --- |
| 2026-10-09 | Required context reads were incomplete before implementation, despite a dated plan and independent plan review. The execution/promotion record was not kept current. | Acknowledge to user; stop edits; finish required reading; reconcile this ledger and rerun review/verification against current state. Completed reading cannot retroactively repair sequencing. User explicitly authorized continuation following repo rules. |
| 2026-10-09 | Raw-cursor wording is broader than SimpleBroker's guarantee for administrative backfill. Installed supported broker versions serialize generated runtime writes, but exact-ID imports/moves may land behind a cursor. | Submit exact clarification to independent reviewer before promotion. Limit completeness to runtime publication and report upstream limits separately; no broker edits or finite-overlap workaround. |


## Revised Review Dispositions

- F1/F2 (Claude first review): accepted. Restore shared QueueChangeMonitor,
  watcher topology and result interval bounds; defer timer removal instead of
  adding an exception flag, subclass, or a new fallback implementation.
- F3: accepted. Collection mode must include metadata committed before observed
  output. Keep the explicit extra advancing read after a positive surface probe;
  the probe-first suggestion is optional and not needed to correct the race.
- F4/G2: accepted. Explicit removal sites/spec paragraphs are listed in slice 1.
- F5: accepted. Supported generated writes serialize allocation plus insertion
  through commit (SQLite BEGIN IMMEDIATE; PG singleton meta row lock). Exact-ID
  imports/moves are outside the cursor guarantee. Qualify the May 10 lesson.
- F6/F7: accepted. Repair the split execution table, label revision-start evidence,
  and name SimpleBroker's facade/schema validation.
- F8: deferred with QueueChangeMonitor changes/tests; no new private watcher seam
  is retained.
- G1 (Claude second review): preserve startup lifecycle replay. Realtime owns a
  separate cursor starting at None; its existing terminal_state_emitted guard
  prevents a duplicate synthetic terminal event. Result collection alone adopts
  the materialization cursor. Scoped independent review returned PASS before edits. Late/unknown metadata
  can now replay historical states baseline sometimes skipped; this strengthens
  consistency rather than promising event-for-event identity on those edges.
- G3: keep a direct conditional second read for now. Delete the repeated loader
  only when routes are already provided, preserving direct private collector
  tests/callers and their discovery contract. Add persistent/pipeline/default
  race cases if the mechanism needs them; no new mode framework.


## Current Narrowed Execution Evidence

- October 9: scoped G1 review PASS. Independent realtime startup cursor and
  existing terminal-state suppression need no new type or routing protocol.
- New race regressions failed with missing TaskSpec metadata for both persistent
  and one-shot tasks. Startup-replay regression failed with only the synthetic
  terminal row instead of the three original timestamps. These are intended
  pre-fix failures, recorded in `/tmp/weft-new-red.txt`.
- Removed the earlier route descriptor, synthetic TaskSpec, rejected-launch
  protocol, watcher subclass, shared monitor exception changes, and timer removal.
  The narrowed candidate retains the original watcher and timer topology.

## Polling Audit (Read-Only Inventory; Deferred Except Materialization Replay)

The October 9 audit distinguishes shared-history replay from clocks, process
probes, retry bounds, and backend-owned activity. These are findings, not an
implementation backlog authorized by this plan. Owners and verification must be
reviewed before another slice changes them.

| Owner / baseline location | Finding and required follow-up |
| --- | --- |
| result.py:211; _task_history.py:18 | Primary repeated TaskSpec lookup and replay. This slice advances history once; result probes still have their existing timer. |
| events.py:560–567 | Realtime cursor can retain discarded rows. Deferred separately; do not broaden the active result-materialization fix. |
| queue_wait.py:89–109 | Native producer death silently removes a wake source. Timer removal needs explicit shared failure custody and portable fan-in proof. |
| tasks.py:397–449; 995–1021; 1067; 1494–1569 | Snapshot/status/control loops repeatedly rebuild metadata and evidence at 50–100ms. Separate PID liveness and ACK/grace obligations before removal. |
| _spawn_submission.py:81–124; 267–296 | Reconciliation repeats post-TID suffix at 50ms. Must preserve queue-first acceptance and recovery outcomes. |
| _result_wait.py:176–345; result.py:521–736 | Known-route collection retains 100ms bounds and persistent pending history. Deferred. |
| events.py:263–301; 680–735; 779 | Plain events use a cursor but retain cancellation responsiveness bound. Removal requires a cancellation publisher. |
| task_monitor.py:610–623 | Summary reconstructs TaskMonitor at each configured interval. Investigate retained scan ownership separately. |
| heartbeat.py:219–229; manager_runtime.py:1658–1672; 1711–1766; 2021–2146 | Readiness/registry probes at 100ms also cover external process exit. Need publishers before changing waits. |
| run.py:723–735; 750–764 | Interactive completion uses 50ms and may lack a log/monitor-only wake. |
| system.py:1421–1439; queue.py:560–620; bin/launch_manager:80–105 | Public status/watch intervals and bounded diagnostic scans. Different contracts from result observation. |
| subprocess_runner.py:152–317; host.py:574–701; task_sessions.py:435–478; 734 | OS pipes, exit, cancellation use portable responsiveness bounds. Need platform-specific wake evidence before removal. |
| task_sessions.py:534–535; 830 | Resource limits checked per handoff at 20Hz rather than configured cadence. Separate resource policy work. |
| Docker agent_runner.py:260–279; _sdk.py:61–80; plugin.py:1209–1216; Microsandbox _runtime.py:445–465 | Remote startup and callback cancellation polling. Separate extension ownership. |

BaseTask/MultiQueueWatcher's retained broker strategy, true deadlines, exact
PING/PONG replies, resource clocks, bounded retries/drains/joins, external CI,
and OS process-loss probes have distinct reasons. Their existence does not
justify repeated shared-log replay. SimpleBroker's watcher has PEEK dispatch
semantics and backend core-replacement handling; reusing it for cursor-based
fan-in requires a separate design rather than a cosmetic subclass.

Narrowed spec promotion against `8085962af48c2ae38b8487fc449a4b11e0471170`:
SHA-256 `5633cee5a9ced33ec56d82777af7730c95858907d63a8662915279d21ff7fe50`.

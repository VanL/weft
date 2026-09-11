# Audit Regression Fixes

Status: draft
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13.4]; docs/specifications/02-TaskSpec.md [TS-1.1]
Superseded by: none

Class: 5 — clarification of normative observation and retirement boundaries;
hardening required for queue subscription and destructive cleanup lifecycles.
Plan type: implementation with spec clarification.

## Goal

Fix false realtime completion when a persistent task's metadata arrives late,
and restore retirement of already eligible Monitor families independently of
unrelated log backlog. Record the owner's shared-inbox requeue decision without
changing supported requeue behavior.

## Spec Baseline

`9b858304753f1c185ea489a33b4b58975c39a519` is the initial code/spec baseline.
The audit reproduced both differences against release `v0.9.99`.
Promotion baseline: initial SHA plus the three source-spec working-tree diffs
applied on 2026-09-11 after independent review; `git diff 9b858304 --
docs/specifications/02-TaskSpec.md docs/specifications/05-Message_Flow_and_State.md
docs/specifications/07-System_Invariants.md` identifies the promoted text.

## Source Documents and Context

Read [MF-5](../specifications/05-Message_Flow_and_State.md),
[OBS.13.4](../specifications/07-System_Invariants.md),
[TS-1.1](../specifications/02-TaskSpec.md), and the repository
[engineering principles](../agent-context/engineering-principles.md).
The prior [Monitor correctness plan](2026-08-31-monitor-and-task-correctness-fixes-plan.md)
is historical; its claim that the surviving retirement call is ungated is
incorrect.

Files to modify:
- `weft/commands/events.py` and `tests/commands/test_realtime_events.py`:
  realtime observation retains only initial metadata today. The shared evidence
  classifier treats unknown task type as eligible for final outbox evidence.
  Queue handles, per-queue cursors, and QueueChangeMonitor must change together
  when late metadata identifies custom routes.
- `weft/core/monitor/task_monitor.py` and `tests/tasks/test_task_monitor.py`:
  retirement currently sits beneath completed_fifo_high_water. The store's
  retire_completed_collation_families already enforces per-family prerequisites.
- The three source specs, this plan and its index, `docs/lessons.md`, and
  `CHANGELOG.md`: record exact boundaries and verification.

Read `weft/core/task_evidence.py`, `weft/core/queue_wait.py`,
`weft/core/monitor/store.py`, and the closest tests before editing.
Comprehension checks: Why is a persistent work-item result not task completion?
Why can an already disposed family retire without creating a new summary?

## Invariants and Constraints

Observation remains non-consuming; no lifecycle writes, queue acknowledgements,
public API changes, new dependencies or task execution paths. Explicit terminal
log/control evidence remains usable without TaskSpec metadata. Ordinary outbox
fallback in realtime observation requires known one-shot metadata. Persistent
and interactive tasks never complete from a work-item value. Streaming frames
remain observation only. Cancellation, deadlines, terminal grace and wrapper-lost
precedence remain intact. Rebind opens the new handles/monitor with cleanup
registered, closes the old monitor before its handles, resets cursors only for
changed queues, resets outbox_stream_frames_seen when the outbox changes, and
preserves log progress. Unknown metadata must not leak outbox-inferred terminal
snapshots at startup or later snapshot emission, nor adopt them as completion. Failed acquisition releases both
newly acquired resources and the existing subscription when iteration unwinds.

Monitor retains one retirement owner, destructive/collated mode gates, store
availability requirements, per-family SQL safety proofs, retention age and batch
limits. Summary creation, disposition and orphan recovery stay high-water gated.
An ingestion result reporting failure or backlog cannot suppress retirement of
previously eligible families. No retirement through an unavailable/broken store.
No TaskSpec mutation, TID changes, reserved-policy changes, schema migrations,
shared-inbox support, or endpoint policy changes.

## Proposed Spec Delta

Strategy A: promote the following text before implementation; attach existing
section backlinks and synchronize implementation notes in the same work.

### MF-5 observation paragraph: append

> Realtime observation refreshes task metadata before classifying late output.
> When discovered metadata identifies different outbox or control routes, it
> switches observation to those routes without consuming their contents.
> Until one-shot task metadata is known, ordinary outbox values alone cannot
> end realtime observation; explicit terminal log or control evidence remains
> sufficient. Persistent and interactive work-item output is not task completion.

### OBS.13.4: append

> Retirement of already eligible families does not require global task-log
> ingestion catchup. Backlog or a reported ingestion failure does not block
> the existing per-family retirement checks in an available store. This does
> not relax the high-water requirement for summary creation or disposition.

### TS-1.1: append

> Task-level requeue remains unsupported under the current task contract.
> The manager spawn queue is the only explicitly supported shared inbox.
> Reexamine task-level requeue if shared task inboxes become explicitly
> supported; configurable queue names alone do not establish that contract.

## Rollout and Rollback

Ship both fixes in the normal next release; no data format or mixed-version
migration. Realtime code can be reverted independently (restoring its bug).
Monitor rollback restores delayed cleanup; already retired eligible records are
not reconstructed. Existing SQL safety predicates protect this destructive edge.
Observe persistent event subscriptions remaining open through work results and
eligible Monitor rows retiring during bounded backlog passes.

## Tasks and Verification

1. Independent plan/delta review, explicit disposition, then spec promotion.
2. Write failing real-broker realtime tests: attach before metadata, then ordinary
   persistent output remains open; late one-shot output completes; custom routes
   are followed; unknown metadata plus ordinary output remains open; typed
   terminal evidence still ends it. Include ordinary output already present before
   subscription with unknown metadata: no terminal snapshot/result/end may escape.
   Assert unchanged-route cursor preservation, changed-route replay, and failed
   rebind cleanup. Use deterministic generator/event boundaries
   rather than sleeps for ordering. Implement within existing iterator; keep
   queue leases and monitor cleanup paired. Run realtime/result/connection tests.
3. Write failing real-store retirement tests with unrelated backlog and reported
   ingestion failure; eligible family retires and a neighboring missing-proof
   family stays. Preserve report-only/raw-owner gates. Move the bounded existing
   retirement operation, do not add another owner or scheduler. Run monitor tests.
4. Independent completed-work review, address findings, record the owner note and
   lessons, then run repo-managed checks after `. ./.envrc`:
   `./.venv/bin/python -m pytest`, full CI-target mypy, `./.venv/bin/ruff check .`,
   `./.venv/bin/ruff format --check` on touched Python files, and spec/plan hygiene
   tests. Run the repository traceability command if present. Use `bin/pytest-pg`
   for the changed real-broker tests if provisionable. Record actual outcomes.

Stop and re-evaluate if repair requires a new evidence priority, changes task
lifecycle, bypasses retirement proofs, or needs a generic subscription framework.
Brokers and store queries stay real; controlled observation/ingestion boundaries
may schedule events or inject errors without substituting storage semantics.

## Independent Review Loop

Same-family independent reviewers are available in this session. Review this
plan, exact delta, baseline code and focused tests before implementation and
review both finished slices. Findings require explicit disposition. Author's
fresh-eyes pass checks metadata/route/cursor custody and the distinction between
summary eligibility and parent retirement. No claim of different-family review.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |

## Execution Log

- Author fresh-eyes: plan names late queue-route rebinding and failed acquisition
  cleanup, and preserves the separate summary/disposition high-water gate.
- Independent plan review (schema_audit): blocked on snapshot-path guard and
  queue-local stream flag/cursor tests. Accepted: added both requirements above;
  no scope or architecture expansion. Amendment verification pending.

- Amendment review (schema_audit): PASS; snapshot and route-custody requirements
  verified. Exact spec delta promoted before runtime edits.

- Monitor plan review (runtime_audit): PASS; retain available-store and
  destructive/collated gates; do not retire from an unconditional finally.
- Realtime red proof: 3 failed, 4 passed in the added boundary matrix before
  implementation. Failures showed persistent false completion, custom-route
  result loss and unknown-type terminal snapshot. After repair, all 22 realtime
  tests passed, including route-cursor and rebind-failure cleanup cases.
- Monitor red proof: 2 failed, 4 passed in the new real-store matrix. The two
  failures were collated/delete backlog and reported ingestion error; mode and
  owner protection cases already passed. After repair, 8 targeted cases passed.
- Implementation choice: retirement follows existing high-water-gated orphan
  recovery, so recovery may refresh proofs before retirement SQL runs. This
  preserves one owner without a new branch or helper solely for lint. No SQL
  predicate or summary/disposition gate changed; independent review covers it.
- Completed-code independent reviews: schema_audit and runtime_audit both PASS
  with no blocking findings. The latter explicitly verified orphan ordering,
  available-store failure boundaries, mode gates and SQL proof preservation.
- SQLite result/observation/realtime verification: 96 passed, 3 PostgreSQL-only
  skips. PostgreSQL via `bin/pytest-pg --all` on realtime, observation connections
  and the six new Monitor cases: 36 passed. The first PostgreSQL attempt failed
  the existing five-second staggered-wrapper test; the exact rerun passed.
  Independent review found deadline sensitivity plausible, not conclusively
  established; no timing behavior was changed to hide it.
- Full default suite: 4,755 passed, 14 skipped, 8 failed. Two failures were old
  private snapshot test doubles missing the new keyword parameter. Only those
  two signatures were updated; their assertions remain unchanged. The complete
  shared-ops and realtime files then passed all 34 tests.
- The remaining six full-suite failures predate this change: five exact
  dependency-floor assertions expect older versions than HEAD's pyproject, and
  the September 8 persistent-result plan links a nonexistent September 10 plan.
  Neither source was changed by this repair. Spec/plan checks independently
  returned 7 passed, 1 failed for that same missing link; the new plan metadata
  was checked separately and passed. No dedicated traceability command was found.
- Full CI-target mypy passed (188 source files); repository Ruff check and
  formatting checks for all five touched Python files passed. `git diff --check`
  passed. The owner subsequently authorized a targeted commit of these fixes,
  their tests and related documentation.

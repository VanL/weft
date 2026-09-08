# Monitor and Task Correctness Fixes Plan

Status: completed
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-3], [MF-5]; docs/specifications/07-System_Invariants.md [OBS.1], [OBS.13.12]; docs/specifications/01-Core_Components.md [CC-2.1], [CC-2.5]
Superseded by: none

Class: 4 — multi-surface fixes touch task terminal delivery and the
Monitor's queue-cleanup worker lifecycle; hardening applies. These fixes
conform to existing spec text and require no normative edit. Escalated
from Class 3 on 2026-09-07 after reviewing those execution boundaries.
Plan type: implementation. Program ledger:
[2026-08-31-guard-and-custody-simplification-plan.md](./2026-08-31-guard-and-custody-simplification-plan.md).

## 1. Goal

Seven independent, small fixes, each verified against code on
2026-08-31, each landable as its own commit. None changes a spec; several
bring code into line with spec text it currently contradicts.

## 2. Source Documents

- 05 [MF-3] (lines ~190–230): typed terminal `ctrl_out` envelopes; "public
  acknowledgement remains the post-unwind `ctrl_out` reply"; STOP/KILL
  envelopes with `request_id` echo it "in the eventual post-unwind
  acknowledgement".
- 05 [MF-5] (line ~723): "Successful completed lifecycles do not require
  reserved-queue probes."
- 07 [OBS.13.12] (lines ~965–977): bounded policy runs; "must not spin when
  only future-eligible or blocked work remains".
- `_write_state_queue_message` docstring in `weft/core/tasks/base.py`
  (the bounded terminal-write retry — implementation elaboration under
  [OBS.1], not spec text).

## 3. Context and Key Files (per item)

**A. Interactive terminal envelope.** `weft/core/tasks/interactive.py`:
`_interactive_ensure_session` except-block (:232–254) marks `failed` and
re-raises without any ctrl_out envelope; `_interactive_finalize_session`
returns at :393–394 when no session exists and otherwise writes a
hand-built envelope with a bare `ctrl_out.write` at :482
(`_interactive_terminal_envelope` :363–391, adds an `event` field). Canonical
writer: `base.py::_send_terminal_envelope` (:1906–1931), status-gated,
bounded retry. No in-repo ctrl_out reader keys on `event`
(`weft/commands/tasks.py:1320` keys on `type=="terminal"` + `status`).

**B. Interactive STOP/KILL ack ordering.** `_interactive_handle_control`
(:529–567) calls `_send_control_response(..., "ack", request_id=…)` at :549
and :564 *before* `_interactive_shutdown` at :550/:565. Non-interactive
path acks post-unwind (`consumer.py::_finalize_deferred_active_control`
:706–760, docstring at :677–679 explains why). Test
`tests/tasks/test_task_interactive.py:203–240` asserts presence of ack and
envelope but not order. Command-side consumer
`commands/run.py::_InteractiveRunLifecycle.request_exit` (:699–730) waits
1 s for the ack and then falls through to `wait_for_completion`;
`commands/interactive.py::_handle_ctrl_message` (:257–275) handles ack and
terminal independently.

**C. Collation retirement call site.** `task_monitor.py`: store-cycle call
at :2513 (`_run_monitor_store_cycle`); reserved-slice call at :4189–4201
behind `if not errors`; comment at :3962–3964 claims "The reserved slice is
the sole collation-retirement owner." Retirement is predicate-gated
(`sql.py:1145–1148`: `reserved_probe_needed = 0 OR
reserved_cleanup_checked_at_ns IS NOT NULL`), so call order cannot strand
anything. Test `tests/tasks/test_task_monitor.py:4189–4201` asserts
`cleanup.families_retired == 0` and `reserved_cleanup.families_retired == 1`.

**D. Queue-discovery cadence.** `task_monitor.py:2450–2451` resets
`_runtime_cleanup_queue_discovery_pending=False` and
`_next_runtime_cleanup_queue_discovery_due_monotonic=0.0` at the top of
every store cycle; the post-slice write at :5666–5673 is therefore wiped
before `policies/runtime_control.py::runtime_cleanup_queue_discovery_due`
(:178–191) reads it. Init at :718–721; diagnostics carry at :1093–1097,
:1192–1196. Removing these resets alone is unsafe: the not-due return in
`_run_terminal_control_cleanup_slice` (:3856–3872) skips the remaining
discovery slices, but `_handle_control_cleanup_worker_result`
(:5666–5673) still pushes the discovery deadline to now + interval.
Frequent catch-up cycles or wakeups can postpone discovery indefinitely.
The reactor serializes built-in and runtime-cleanup workers
(:1583–1597); this is a result-ownership defect, not a concurrent merge.
Slice-level throttle test: `tests/tasks/test_task_monitor.py:6620–6656`
does not cover the result handler.
Cost: `_queue_name_snapshot` (:3615–3627) issues ten `list_queues`
calls per slice chain, each an index range scan over all `T*` rows.

**E. Waiter close ledger.** `weft/core/tasks/multiqueue_watcher.py`:
`_closed_activity_waiter_ids` (:290) append-only set of `id()`;
`_close_activity_waiter_once` (:507–518). Reset path via
`commands/interactive.py:99` + watcher error at :911. ID reuse reproduced.

**F. SimpleBroker return-shape guards.** `heartbeat.py:177–182, 201–206`
(after `peek_one`/`move_one` — the message is already moved to reserved
before the check), `consumer.py:628–632` (after `peek_many`), read-only
variants `heartbeat.py:449–451`, `pipeline.py:778–779`.

**G. Negative-sentinel branches.** `manager.py::_registry_entry_is_expired`
(:2043–2047, negative ⇒ everything expired), `_manager_record_liveness`
(:2490–2494, negative ⇒ stale), `manager_runtime.py::_record_is_recent_enough_for_uncertain_selection`
(:329–332, negative ⇒ never stale), :375–376 (block startup forever),
:489–492 (never expire). Constants `MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS`
(:747 = 300.0) and `MANAGER_NAMESPACE_AMBIGUOUS_BACKLOG_GRACE_SECONDS`
(:826 = 2.0) are positive `Final`s with no env override. Tests that
monkeypatch `-1.0`: `tests/core/test_manager.py:6670`
(`test_manager_liveness_rejects_stale_external_supervisor_record`),
`tests/commands/test_manager_commands.py:1752`,
`tests/commands/test_run.py:3468, 3496, 3523, 3550, 3582, 3615, 3733`.

## 4. Invariants and Constraints

- Exactly one terminal ctrl_out envelope per task, schema-identical
  across task kinds; observable ctrl_out ordering for interactive tasks is
  preserved: stderr chunks and final stream markers precede the terminal
  envelope.
- Acks echo `request_id` and follow unwind.
- Retirement never precedes reserved proof for probe-needed families
  (already enforced by SQL; must remain).
- Discovery stays bounded and eventually runs while the Monitor keeps
  cycling. A skipped discovery pass must not postpone its existing due
  time. Terminal-ready records and pending/error cleanup still bypass
  the idle deadline; slice chaining and error/backoff behavior stay intact.
- No new abstraction anywhere in this plan.

Read before D: the terminal-control not-due return, all three slice
results, and their reactor result handler. Comprehension check: which
successful result proves the name-discovery chain reached its end, and
which successful result means discovery never started? The existing
`work.slice_kind`, `cleanup.pending`, and `cleanup.success` distinguish
them; no new timer owner or worker lane is needed.

Rollout: land D's reset removal and deadline-owner correction together.
The cadence state is process-local; no wire shape, queue name, or store
schema changes. Reverting D restores the earlier overscanning behavior;
it does not restore rows legitimately deleted by cleanup. A/B keep the
canonical durable spine and terminal schema, and retain the bounded
writer's existing failure policy. Independent review is required before
implementation and after each completed slice.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Spec Baseline

- `bcea628e` — 05, 07, 01 at plan authoring time (2026-08-31). No delta.

## 5. Tasks (each its own commit; any order)

1. **A + B together (interactive).**
   - In the `_interactive_ensure_session` except-block, call
     `self._send_terminal_envelope()` after `_report_state_change`.
   - In `_interactive_finalize_session`, replace the bare write at :482
     with `self._send_terminal_envelope()`; delete
     `_interactive_terminal_envelope` and the `event` field. Do **not**
     introduce suppression/defer state machinery — the mixin already
     bypasses the base handlers, so there is no double emission to
     suppress; the change is only *which writer* is used.
   - Move the two `_send_control_response` calls after
     `_interactive_shutdown(...)`.
   - Tests (`tests/tasks/test_task_interactive.py`): session-start failure
     → exactly one `type=="terminal"` envelope on ctrl_out; STOP → the ack
     appears *after* the terminal envelope and the final stream markers in
     ctrl_out order; envelope keys identical to a Consumer's. Red today
     for all three.
2. **C (retirement).** Delete the reserved-slice call (:4189–4201) and the
   comment (:3962–3964); keep :2513. Update the test at :4189–4201 to
   assert retirement is attributed to the store cycle. Verify a
   persistently erroring reserved queue no longer blocks retirement (it
   can't, once the only call is the ungated one — add the assertion).
3. **D (cadence), one atomic code/test slice.**
   - Remove the two store-cycle resets at :2450–2451. Initialization and
     worker diagnostic transport retain the state; the reactor's
     `_handle_control_cleanup_worker_result` remains its only
     post-initialization scheduling owner.
   - In that handler, keep assigning
     `_runtime_cleanup_queue_discovery_pending = cleanup.pending or not
     cleanup.success`. If that value is true, retain today's catch-up
     deadline update. Otherwise advance the idle discovery deadline to
     now + `interval_seconds` only for a completed `dead_tid` slice
     (`work.slice_kind == "dead_tid"`), the end of the existing discovery
     chain. A successful non-pending `terminal_control` result that
     skipped discovery leaves the deadline unchanged. Do not infer that
     discovery ran from zero selected/deleted counts. Keep slice chaining,
     the terminal-records OR-term, and the main cycle's error/backoff
     scheduling unchanged. No new result field or abstraction is needed.
   - Characterize the full scheduling path with `broker_env`, real
     store/cleanup slices, real worker results, and the reactor result
     handler; patch only clock readings and external boundaries. During
     frequent 2 s catch-up cycles with no terminal-ready records or
     pending cleanup, run past two idle intervals: each skipped pass
     preserves the due time; the first eligible cycle at/after that time
     reaches reserved and dead-TID discovery; a completed chain sets the
     next idle deadline. Count discovery chains (the number of individual
     `list_queues` calls varies by slice), asserting both no early repeat
     and eventual discovery. Seed an eligible orphan runtime queue after
     the first pass and assert its removal on a later due pass, even while
     frequent wakeups continue. An upper-bound-only test is insufficient.
   - Pin pending/error results to their existing retry deadline and
     terminal-ready records to immediate discovery despite a future idle
     deadline. Keep the existing slice-level throttle test. Fix the
     `_maybe_run_maintenance_pass` docstring that cites the cadence as a
     model.
4. **E (waiter).** Replace the `id()` set with per-waiter closed state
   (a flag on the owned resource wrapper or idempotent `close()`); test:
   replace the waiter twice and assert both old waiters are closed (red
   today under id reuse — reproduce with the verifier's approach: close,
   drop, allocate, compare ids).
5. **F (shape guards).** Delete all five; no replacement. Tests: suite
   green; grep gate for `isinstance(.*tuple)` in the two files' queue-read
   paths.
6. **G (sentinels).** First rewrite the listed tests to express intent
   with positive values (e.g., a tiny `stale_after` plus a record older
   than it, instead of `-1.0`); then delete the five negative branches.
   mypy/ruff green; the constants stay `Final` positives.

Stop if: item A wants the base handlers to route interactive STOP/KILL
(that is a larger consolidation; report rather than start it), item C
finds retirement genuinely depending on slice order, or D cannot
distinguish a skipped pass from the completed discovery chain using the
existing work/result fields. Do not introduce new scheduling state to
work around a failed characterization.

## 6. Testing Plan

`broker_env` + real `Consumer` for A/B/E/F; `tests/tasks/test_task_monitor.py`
fixtures for C/D; unit tests for G. Ordering assertions read the real
ctrl_out queue in message-id order. Keep broker reads, lifecycle work,
cleanup selection, and result handling real. Inject a transient broker
write failure for the retry check in A (queue-object seam); D may control
clock readings and external heartbeat boundaries without replacing the
slice or result-handler logic.

## 7. Verification and Gates

Per item: the named test module. Final for the plan: full suite + mypy +
ruff. Rollback: each commit independently revertable.

## 8. Independent Review Loop

Different agent family preferred; required before implementation and on
completed slices for Class 4 (per `review-loops-and-agent-bootstrap.md`).
Stance for A/B: could the
post-unwind ack delay (`INTERACTIVE_STOP_GRACE_SECONDS`) break any
command-side waiter? For D: does every successful skipped pass preserve
the discovery deadline, does a completed discovery chain advance it, and
can an eligible orphan queue still be cleaned under frequent catch-up
cycles with no terminal records? Check both bounded work and progress.

## 9. Out of Scope

Reserved disposition (plan 2); registry custody (plan 4); the window-scan
engine (plan 6); consolidating interactive STOP/KILL into the base
handlers.

## 10. Fresh-Eyes Review

Author pass 2026-08-31: removed the earlier "suppress base emission"
design after verifying the mixin never reaches the base handlers; reversed
the retirement call-site choice to the store-cycle call after verifying
the SQL predicate; reversed "delete the throttle" to "fix the reset" after
costing the discovery snapshot.

## Review Record (append-only)

**2026-09-07 — review against code/spec baseline `bcea628e`; plan correction.**

| Finding | Disposition |
|---------|-------------|
| F1 — reset removal alone activates deadline starvation; the proposed at-most-once test permits never scanning | Accepted: D now keeps the deadline on skipped passes and advances it after the existing discovery chain completes; the test covers real result handling, bounded scans, and eventual orphan-queue cleanup. |
| Classification — terminal delivery and queue-cleanup worker changes trigger hardening | Accepted: Class 4, pre-implementation review, and explicit rollout/rollback constraints added. |

Probe evidence from this review: a real `TaskMonitor` and SQLite-backed
Monitor store ran `_run_terminal_control_cleanup_slice` followed by
`_handle_control_cleanup_worker_result`, with monotonic time controlled
and the existing due time retained between calls (modeling reset
removal). No terminal-ready rows or pending work existed. The observed
`(now, prior_due, next_slice_kind, new_due)` values were
`(2, 60, None, 62)`, `(4, 62, None, 64)`, `(62, 64, None, 122)`, and
`(64, 122, None, 124)`: even after the original deadline, discovery had
not started. The current slice-only test
`test_task_monitor_runtime_cleanup_skips_queue_snapshot_when_not_due`
passed when rerun, confirming it does not detect this interaction.
This is pre-change defect evidence; the revised code and progress tests
remain implementation work. No implementation or final-review pass is
claimed by this plan revision.

## Implementation Record (2026-09-07)

Class 4, hardened; no normative delta. Baseline is plan 2's reviewed worktree
(the reserved-disposition changes atop caa1513b), pending its full gate and
commit. Plan 3 work is isolated so it cannot alter that gate; plan 2 commits
first. The user's one-commit-per-plan instruction supersedes internal
per-item commit suggestions. Independent same-family pre-implementation
review passed: preserve SQL reserved proof, retain store-cycle retirement,
advance idle discovery only after completed dead_tid slices, and preserve
existing pending/error retries. No new scheduling state.

For E, installed SimpleBroker ActivityWaiter documents terminal, idempotent
close even after the first close raises. Rely on that resource contract,
remove the object-id ledger, and use a controlled id collision regression
rather than allocator luck. For A/B, include no-session STOP/KILL: the
existing exactly-one-terminal invariant applies before first input too.
Use existing terminal status to avoid re-emission after a startup failure;
no suppression state or consolidation into base handlers.

E regression used the real watcher topology path with a controlled object-id
collision: before removal only the first waiter closed (`[1, 0, 0]`); after
removal all three closed (`[1, 1, 1]`). The fake resource now honors the
installed ActivityWaiter idempotent-close contract. F removes the tuple and
scalar return-shape guards at all five named broker-return sites; parsing of
message content is unchanged.

C/D characterization: both baseline probes failed (reserved slice retired a
family; a skipped second cycle moved due time from 60 to 62). Nine focused
cases passed after the fix: real discovery chains at 0/60/120 under two-second
wakeups, an orphan removed at 60, terminal-ready bypass at 122, six
pending/error retry cases, and retirement despite a reserved-delete error.
G pre-implementation correction: negative TTL previously both forced stale
classification and disabled the service reducer's expiry, an impossible
production combination. Positive-TTL characterization replaces those cases:
an expired candidate remains expired even after keyed PONG, and a fresh live
candidate wins. No selection fix is added here; plan 4 owns reader custody and
its promoted age-window rules.

Integration type checking found the waiter's removed id ledger also named
in TaskMonitor worker-clone initialization and two snapshot field ledgers.
Removed those references as part of E; worker snapshot parity is verified by
the existing TaskMonitor tests. Independent completed-work review passed
A-D and E-G. A real-client command test confirms terminal proof is accepted
without a delayed STOP ack and no KILL is sent.

Integration baseline: `0b20d4d1`, plan 2 committed after its full gate. The
isolated changes were copied onto that baseline; only plan 3 files moved.
Full Ruff and mypy passed in isolation. Final root-worktree gates follow.

Final root gate: `pytest -m '' -n 2` passed 4,439 tests with 16 skips
(11 opt-in live-provider and five PostgreSQL-only cases) in 843.93 seconds.
Full Ruff, mypy (192 source files), and suppression inventory checks passed.
Independent same-family reviews passed; a different family was not used.

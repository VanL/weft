# Manager Drain and Service Bookkeeping: One Record, One Clock, One Enablement Rule

Status: draft
Source specs: docs/specifications/03-Manager_Architecture.md [MA-3], [MA-1] item 7; docs/specifications/07-System_Invariants.md [MANAGER.12]
Superseded by: none

Class: 4 (risky). No normative spec text changes: the behavior of the
drain and of service enablement is unchanged, and no mapped function is
renamed. The edits sit on the Manager's system-STOP drain and its service
reconciliation, which are on the durable spine, so the hardening-plans
checklist applies in full and review happens before implementation. Not
+P. Plan type: implementation. One-way doors: none.

This plan carries three of the five suggestions an outside reviewer made
on 2026-11-04 after grading the series' code for theory fit, excellence
and elegance; the series lead evaluated all five against the code on the
same day, the owner folded suggestion 1 into plan 15
(`docs/plans/2026-11-04-system-stop-snapshot-targets-plan.md`), and the
owner directed that the others the lead agrees with become a plan. These
are suggestions 2 (one progress record per drained task), 3 (shutdown
durations on a monotonic clock) and 4 (internal-service enablement
defined once). Suggestion 5 (narrowing the TID structural test) is an
owner decision recorded in `## 10`, not a task.

Sequencing: this plan lands after plan 15. Task 1 edits the same
`_signal_children_to_stop` loop plan 15 changes, and plan 15's broadcast
test asserts on the attributes Task 1 replaces; that test is updated here.

## 1. Goal

Three craft changes to `weft/core/manager.py`, each behavior-preserving,
each removing a way the code can disagree with itself:

1. The drain's per-task progress is kept in three parallel collections
   (`_drain_signaled_children`, `_drain_escalated_children`,
   `_drain_signal_started_ns`) that six reset sites must clear together
   and whose readers assume agree; the escalation loop's
   `.get(tid, time.time_ns())` fallback would silently restart a rung's
   escalation delay if a timestamp were ever missing. One record per task
   makes "signalled without a timestamp" unrepresentable.
2. The drain's windows (the shutdown window and each rung's escalation
   delay) are measured on the wall clock while the forced-cleanup budgets
   already use the monotonic clock. A wall-clock step would shorten or
   stretch a window; a monotonic clock cannot. Durations move to the
   monotonic clock; published timestamps stay on the wall clock.
3. Which internal services the Manager wants (heartbeat always; TaskMonitor
   and LivenessMonitor by flag; only for the canonical Manager) is
   restated at three sites. One pure function returns the desired keys and
   all three read it, so a policy edit cannot change launches while leaving
   probe ownership or wake reasons behind.

## 2. Source Documents

- `docs/specifications/03-Manager_Architecture.md` [MA-3]: the phase order,
  the per-rung escalation delay `MANAGER_CHILD_STOP_ESCALATION_SECONDS`
  (2.0 s, `weft/_constants.py` 858) and the shutdown window
  `MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS` (15.0 s, line 861). Unchanged.
- `docs/specifications/03-Manager_Architecture.md` [MA-1] item 7: the
  heartbeat is a standing internal service of the canonical Manager; the
  Manager neither reads nor deletes `weft.state.heartbeats`. Unchanged.
- `docs/program-theory.md` [WT-4] rows "Shared function, shared code"
  (line 462), "The expert Python developer test", "One owner for each
  resource" where it applies to local protocol state; [WT-7]
  "Event-driven against absence" (timers exist for clock work and absence
  detection; a window measures elapsed time); [ALT-WT-023] (standing
  internal services).
- Plan 15 (`docs/plans/2026-11-04-system-stop-snapshot-targets-plan.md`):
  the broadcast rung and its test, which Task 1 updates.
- The outside review of 2026-11-04 (held outside the repository; its
  items 2, 3, 4 are restated here in the lead's words).

## 3. Context and Key Files

### Current structure (at `e13bb7b5`; re-verify every line before editing)

Drain progress. `Manager.__init__` holds `_drain_signaled_children:
set[str]`, `_drain_escalated_children: set[str]`,
`_drain_signal_started_ns: dict[str, int]` (lines 710-712) and
`_drain_started_ns: int | None` (713). Readers: the signalling and
escalation loop in `_signal_children_to_stop` (4885-4907: membership,
`.get(tid, time.time_ns())`, the escalated set, the add and the stamp) and
the drain wake-timer block (8688-8712: the shutdown deadline from
`_drain_started_ns`, then one escalation deadline per member from
`_drain_signal_started_ns.get(tid)`, skipping escalated members). Reset
sites that clear the three collections together: `_begin_shutdown_drain`
(5246-5249), the two drains that follow it at 5286-5289 and 5314-5317, the
reset at 5345-5348, and `_resume_leadership_after_drain_loss` (9080-9083).
`_drain_started_ns` is set from `time.time_ns()` at 5250, 5290, 5318 and
9203 (phase 2 start) and read by the wake block and by
`_shutdown_drain_timed_out` (9207-9211).

Clocks. The wake block converts wall-clock deadlines with
`_timeout_until_ns(due_ns, now_ns=now_ns)` from
`weft/core/tasks/service.py` 662, where `now_ns` is `time.time_ns()`. The
forced-cleanup budgets at 9157 and 9184 use `time.monotonic()`. The file
has 71 `time.time_ns()` calls and 17 `time.monotonic()` calls; most of the
former stamp published events or compare against broker timestamps and are
out of scope. Tests drive Manager deadlines by patching
`manager_mod.time.time_ns` (`tests/core/test_manager.py` 3674, 3955, 4047,
4064, 4514, 4551; `tests/core/test_manager_family_maintenance.py` 997) and
once `manager_mod.time.monotonic` (`test_manager_family_maintenance.py`
167); Task 0 records which of these touch drain windows.

Service enablement. Three sites restate the same policy:
`_reconcile_managed_services` (7586-7592: if the inbox is the public spawn
queue, append the heartbeat spec, then TaskMonitor and LivenessMonitor by
their flags), `_pending_service_probes_for_scope` (8186-8192: the same
test on the inbox, then `internal_keys.add(...)` by the same flags) and
`_managed_service_convergence_active_reasons` (8226-8232: a literal set of
the three keys). The flags are `_task_monitor_enabled` and
`_liveness_monitor_enabled`; the canonical test is
`self._queue_names["inbox"] == WEFT_SPAWN_REQUESTS_QUEUE`. The reducer in
`weft/core/manager_services.py` (line 240) consumes evidence and returns
decisions; it is not touched.

Tests that reference the drain attributes: `tests/core/test_manager.py`
lines 3921-3926, 4568-4603, 8746, 9106, plus plan 15's
`test_first_stop_rung_is_one_broadcast_over_pending_targets`.

### Files to modify

- `weft/core/manager.py`
- `tests/core/test_manager.py`
- `tests/core/test_manager_family_maintenance.py` (only if Task 0 finds a
  drain-window test patching `time_ns` there)
- `CHANGELOG.md` (one bullet under `### Changed`; the clock change is
  observable under a wall-clock step)
- `docs/plans/README.md` (this plan's row)

### Read first

- `weft/core/manager.py` 700-716, 4861-4924, 5234-5350, 7576-7600,
  8180-8240, 8680-8750, 9078-9090, 9150-9215
- `weft/core/tasks/service.py` 655-675 (`_timeout_until_ns`)
- `weft/core/manager_services.py` 230-260 (the reducer boundary; read, do
  not edit)
- `tests/core/test_manager.py` 3900-3930, 4560-4610, 8740-8750, 9100-9110,
  and plan 15's broadcast test
- `docs/program-theory.md` [WT-4] rows named in `## 2`

### Comprehension questions (answer in the Execution log before the first edit)

1. What state can the three drain collections express that one record
   cannot, and what does the loop do in that state today?
   Expected: a TID in `_drain_signaled_children` with no entry in
   `_drain_signal_started_ns`; the loop's `.get(tid, time.time_ns())`
   treats it as signalled just now, restarting its escalation delay. No
   production path writes that state; the record removes it.
2. Which deadlines in the Manager are windows (elapsed time) and which are
   timestamps (published facts or broker comparisons)?
   Expected: the shutdown window and each rung's escalation delay are
   windows; `_drain_started_ns` is compared only against now; the stamps
   published in state events and the admission retry deadlines compared
   against broker timestamps stay on the wall clock. Only the windows move.
3. Where is the policy "which internal services this Manager wants"
   decided today, and what happens if the three copies diverge?
   Expected: at the three sites in `## 3`; a divergence can launch a
   service whose probe is not owned by convergence, or keep a wake reason
   for a service that is never launched.

## 4. Invariants and Constraints

Must not change:

- The phase order and both durations ([MA-3]); escalation eligibility
  (`escalate`, identity re-verification, SIGTERM only, hard kill at drain
  timeout); settlement.
- Which services are desired for which Manager: heartbeat always for the
  canonical Manager, TaskMonitor and LivenessMonitor by flag, none for a
  scoped (non-canonical) Manager.
- The reducer in `manager_services.py` and its evidence-to-decision
  boundary.
- Published event timestamps (`timestamp` fields, `_report_state_change`)
  stay `time.time_ns()`.
- Plan 15's broadcast rung semantics (one broadcast per drain turn over the
  rungs not yet signalled; failure marks the rungs anyway).

Hidden couplings:

- The record replaces three attributes that tests read by name; those
  tests are updated in the same commit, asserting the same facts through
  the record.
- The wake block feeds `next_wait_timeout`; a monotonic deadline must be
  converted with the monotonic now, never passed to `_timeout_until_ns`
  with a wall-clock now. Mixing the domains in one calculation is the
  failure Task 2's test must catch.
- Phase 2 restarts `_drain_started_ns` (9203); after Task 2 it restarts the
  monotonic stamp. `_shutdown_drain_timed_out` reads the same stamp.
- `_pending_service_probes_for_scope` and the convergence reasons are
  called on every reactor turn; the keys function must stay pure and
  cheap (no spec objects, no broker).

Error-path priorities: nothing changes; these are bookkeeping edits.

Theory check: one record per resource and one clock domain per window
are [WT-4] "The expert Python developer test" applied; the keys function is
"Shared function, shared code"; nothing adds a timer, a cache, a mode, or
a second control path ([ALT-WT-010], [WT-7]).

Review gates: no behavior change outside the wall-clock-step case; no
drive-by edits; no new dependency; cross-family review of the plan before
implementation and of the landed work before closure.

### Rollout, rollback, one-way doors

One landing commit per task is allowed (three commits, in task order) or
one for all three; rollback is `git revert` per commit. No persisted shape
changes. No one-way doors.

### Observable-difference register

| # | Behavior at `e13bb7b5` | After this plan | Class |
|---|---|---|---|
| 1 | Three drain collections, six reset sites. | One `dict[str, _StopProgress]`, the same six reset sites clearing one mapping. | N |
| 2 | A signalled rung with no timestamp would restart its delay. | Unrepresentable. | N (no production path produced it) |
| 3 | Shutdown window and escalation delays on the wall clock. | On the monotonic clock; same durations. | C: a wall-clock step during a drain no longer shortens or stretches a window |
| 4 | Service enablement decided at three sites. | Decided once; three readers. | N |
| 5 | Published event timestamps. | Unchanged, wall clock. | N |

No D row.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Spec Baseline

- `e13bb7b5` — `docs/specifications/03-Manager_Architecture.md` at plan
  authoring time. Plan type: implementation; no spec delta (`hardening`
  applies by the risky trigger; the mapping names no function this plan
  renames).

## 5. Tasks

### Task 0. Read, answer, verify

- Answer the three questions in the Execution log before any edit;
  re-verify every line cited in `## 3`.
- List, in the Execution log, which of the seven `time_ns` patches in the
  two test files drive a drain window (shutdown timeout or escalation) and
  which drive other deadlines; only the former change in Task 2.
- Confirm plan 15 has landed (its tests present) and record its landing
  commit.

### Task 1. One progress record per drained task

- In `weft/core/manager.py`, next to `_ManagerDrainStopTarget`, add
  `@dataclass(slots=True) class _StopProgress: attempted_at: int;
  term_attempted: bool = False` with a docstring: "Progress of one
  STOP-drain rung. Presence means a STOP write was attempted (not received
  or completed); `attempted_at` is the monotonic nanosecond stamp Task 2
  defines (wall-clock nanoseconds until Task 2 lands); `term_attempted`
  records the SIGTERM rung." Replace the three attributes with
  `_drain_progress: dict[str, _StopProgress]`.
- `_signal_children_to_stop`: membership is `tid in self._drain_progress`;
  the escalation branch reads `progress.attempted_at` (no fallback) and
  sets `progress.term_attempted = True`; the first rung (plan 15's
  broadcast) inserts `_StopProgress(attempted_at=stamp)` for each rung it
  covers. The wake block reads the same record. Each of the six reset
  sites clears the one mapping.
- Tests: update the references at `tests/core/test_manager.py` 3921-3926,
  4568-4603, 8746, 9106 and plan 15's broadcast test to read
  `manager._drain_progress[tid].attempted_at` and `.term_attempted`,
  asserting the same facts. No new behavior test: this is a representation
  change; the existing escalation and wake-timer tests are the proof, and
  mypy proves no reader of the old attributes remains.
- Stop if: a reader needs a state the record cannot express, or a reset
  site clears some collections and not others today (then that site's
  behavior is a finding, not something to preserve silently).
- Done when: the escalation and wake-timer tests pass unchanged in
  meaning; `grep -n "_drain_signaled_children\|_drain_escalated_children\|_drain_signal_started_ns" weft tests` prints nothing.

### Task 2. Drain windows on the monotonic clock

- RED first, `tests/core/test_manager.py`:
  `test_wall_clock_step_does_not_move_drain_windows`. Using the in-process
  Manager fixture the stop-order tests use, begin a drain with one child
  that ignores STOP (the existing long-work child the escalation tests
  use); patch `manager_mod.time.time_ns` to return a value one hour ahead
  of real time while leaving `time.monotonic` real; assert
  `manager._shutdown_drain_timed_out()` is `False` and that the rung is
  not escalated (no SIGTERM, `term_attempted` false) on the next
  `_signal_children_to_stop` call; then patch `time_ns` one hour behind
  and assert the wake timeout for the shutdown window is at most
  `MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS`. RED at `e13bb7b5`: the forward
  step exhausts the window immediately (timed out `True`, rung escalated)
  and the backward step stretches it.
- GREEN: `_drain_started_ns` becomes `_drain_started_monotonic_ns` set
  from `time.monotonic_ns()` at the four sites; `_StopProgress.attempted_at`
  is `time.monotonic_ns()`; `_shutdown_drain_timed_out` and the wake block
  compute `max(0.0, (due - time.monotonic_ns()) / 1e9)` directly instead of
  going through `_timeout_until_ns(..., now_ns=wall)`. Name the fields with
  their domain. Published timestamps untouched.
- Update the drain-window tests Task 0 listed to patch `monotonic_ns`
  (or advance it) instead of `time_ns`.
- Stop if: a drain deadline is compared against a broker message
  timestamp anywhere (then that comparison needs the wall clock and must
  stay); or the wake aggregation cannot accept a monotonic timeout without
  changing `_timeout_until_ns` for every caller.
- Done when: the RED test is green; the escalation and shutdown-timeout
  tests pass; `grep -n "_drain_started_ns" weft tests` prints nothing.

### Task 3. Internal-service enablement defined once

- Add `Manager._desired_internal_service_keys(self) -> frozenset[str]`,
  pure: empty unless the inbox is `WEFT_SPAWN_REQUESTS_QUEUE`; otherwise
  the heartbeat key, plus the TaskMonitor key if `_task_monitor_enabled`,
  plus the LivenessMonitor key if `_liveness_monitor_enabled`. Docstring:
  `Spec: [MA-1] item 7`.
- `_reconcile_managed_services`: build `internal_services` from the keys
  (a small mapping from key to spec-builder method, built once in the
  function; specs are constructed only here). `_pending_service_probes_for_scope`
  and `_managed_service_convergence_active_reasons`: replace their inline
  policy with the keys function (the latter currently uses a literal set
  of all three keys; Task 0 confirms whether that site must mean "desired"
  or "any internal key" and the Execution log records which; if it means
  "any", it keeps the literal and the plan's claim of three sites becomes
  two, recorded as a deviation-free correction).
- Tests: `tests/core/test_manager.py`,
  `test_desired_internal_service_keys` parametrized over canonical versus
  scoped inbox and the two flags (eight cases), asserting the exact set;
  and the existing enqueue and convergence tests, including
  `test_manager_never_reads_or_deletes_heartbeat_registrations` (5483),
  unchanged.
- Done when: `grep -n "INTERNAL_SERVICE_KEY_TASK_MONITOR" weft/core/manager.py`
  shows the policy in one place plus the key-to-builder mapping.

### Task 4. CHANGELOG, gates, review, closure

- CHANGELOG `### Changed`: "The Manager's shutdown window and per-task
  escalation delays are measured on the monotonic clock, so a wall-clock
  step during a drain no longer shortens or stretches them; published
  timestamps are unchanged ([MA-3])."
- Gates in `## 7`; cross-family review of the landed work; the plan's
  index row to `completed` with the [WT-7] items (audit refresh of rows
  I-13 and 4.4-b by reference; a keyed possession probe from the owner's
  bank).

## 6. Testing Plan

- Harness: the in-process Manager fixtures of `tests/core/test_manager.py`.
  Do not mock the Manager, the queues, or the clock beyond the single
  `monkeypatch.setattr(manager_mod.time, ...)` seam the file already uses.
- Red-green: Task 2's test is red at `e13bb7b5` and green after. Tasks 1
  and 3 are representation changes with no new observable behavior; their
  proof is the unchanged behavior tests plus mypy (Task 1) and the
  parametrized keys test (Task 3).
- Guards: escalation tests (4568-4603), wake-timer test (8746), stop-order
  tests, the heartbeat-registrations test, plan 15's four tests.

## 7. Verification

```bash
./.venv/bin/python -m pytest tests/core/test_manager.py tests/cli/test_cli_manager.py -q -m ""
./.venv/bin/python -m pytest -q
./bin/pytest-pg --fast
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check . && ./.venv/bin/ruff format --check .
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
bin/check-doc-paths
```

## 8. Rollback

`git revert` per landing commit.

## 9. Out of Scope

- The reducer in `manager_services.py`; any service framework.
- The remaining wall-clock deadlines in the Manager (admission retries,
  family repair, registration refresh): they compare against broker
  timestamps or publish, and are not windows.
- `_timeout_until_ns` in `weft/core/tasks/service.py` and its other
  callers.
- Suggestion 5 (`## 10`).

## 10. Owner Decisions

1. **Suggestion 5, the TID structural test (owner's).** The outside review
   proposes cutting `tests/system/test_tid_encoding.py::test_tid_arguments_share_one_canonicalizer_and_one_decoder`
   (line 318) to one check that the decoder is called only from
   `normalize_tid`, removing `_mentions`, `_boundary_failures` and
   `_prune_selector_boundary`, and relying on the fixed codec vectors and
   the behavioral boundary tests. The test exists under the owner's ruling
   of 2026-11-03 ("Sure, widen. Make sure they all use the same
   canonicalization function and the same decoder function") and was
   already reduced to bounded checks in plan 12's round. The lead's
   recommendation: accept the narrowing only after confirming that a
   behavioral boundary test exists for each surface the ruling listed
   (CLI task arguments, the client, the prune selector), since those are
   what prove the observable property; the retained structural check is
   the +P control. The reviewer's own caveat stands: an independently
   implemented decoder under another name escapes both the current and the
   narrowed test. Not a task of this plan until the owner rules.

## Review Log

- 2026-11-04: plan drafted by the series lead (Fable 5.1) from the outside
  review's suggestions 2, 3 and 4 after evaluating all five against the
  code. Self-driven fresh-eyes review: pending. Cross-family round 1:
  pending.

## Revision Log

| Date | Baseline | Section | Change and reason | Re-review required? |
|------|----------|---------|-------------------|---------------------|

## Execution log

(empty until implementation)

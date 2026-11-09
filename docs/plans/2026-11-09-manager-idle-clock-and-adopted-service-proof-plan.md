# Manager Idle Clock Ignores Service Supervision; Adopted Services Keep Live Proof

Status: draft
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1] items 5 and 7, [MA-1.5], [MA-1.6a]; docs/specifications/05-Message_Flow_and_State.md [MF-3.2] (audited, unchanged)
Superseded by: none

Class: 5 (spec-changing) with risky triggers. Normative text in
`03-Manager_Architecture.md` [MA-1] items 5 and 7 changes, so the class is
5. Both slices edit the Manager's idle verdict and its service-evidence path,
which sit on the durable spine (`Manager -> queues/state log`), so the
hardening-plans checklist applies. Not +P: no runbook, skill or gate changes
(the separate lesson commit is documentation under owner decision B). Plan
type: implementation with spec revision. Promotion strategy B (atomic with
each slice's code). One-way doors: none.

Both behaviour changes are owner decisions of 2026-11-09, relayed to this
session by the coordinating agent:

- **C1.** Amend [MA-1.5] so that exits, relaunches and convergence of
  supervision-only internal services do not reset the Manager's idle clock.
- **C2.** PONG-probe aged service-owner registry rows through the existing
  `_service_pong_candidate`, instead of losing live proof. The 300-second
  retention rule stays. No new mechanism.

## 1. Goal

A Manager idle-exits after `idle_timeout` without work, whatever its
internal services do. Today it cannot when they cycle:

1. **C1.** The heartbeat and LivenessMonitor end themselves after
   `SERVICE_IDLE_EXIT_SECONDS` (300 s) when they see no live Manager, and
   the Manager's ensure relaunches them. Each exit, relaunch request,
   launch, commit and convergence state resets `Manager._last_activity_ns`.
   The cycle (about 315 to 376 s) is shorter than the 600-second default
   idle timeout, so the Manager never exits. Reproduced 2026-11-08: when a
   context's database file was unlinked under a live Manager, its services
   (launched against the path, which no longer named the Manager's file)
   saw no Manager and cycled; the Manager stayed alive past 25 minutes,
   while the control run idle-exited at exactly 600 s.
2. **C2.** A successor Manager that adopts its predecessor's live services
   has one piece of live evidence for each: the predecessor's service-owner
   registry row with a host-pid runtime handle. `reduce_service_ownership`
   drops any row older than the 300-second retention window. About 300 s
   after the services launched, the successor has no candidate; the reducer
   increments `uncertain_attempts` ("active service lacks live proof") on
   every pass and, under [WT-2a.13], rightly never relaunches. Reproduced
   2026-11-08 in a clean handoff: Manager 1 idle-exited after 20 s, Manager 2
   adopted, `uncertain_attempts` from t ≈ 282 s, alive past 600 s.

After this plan: C1 makes supervision-only internal-service activity
invisible to the idle clock while every kind of real work still resets it;
C2 re-proves an aged owner row with the existing keyed PING, so the adopter
keeps proof and stops counting attempts.

## 2. Source Documents

Governing specs (baseline in `## Spec Baseline`):

- `docs/specifications/03-Manager_Architecture.md` [MA-1] item 5 (idle
  timeout), item 7 (managed services, live service ownership, service PING
  fallback), the `_Implementation mapping_` lines [MA-1.5] and [MA-1.6a]
- `docs/specifications/05-Message_Flow_and_State.md` [MF-3.2]: the heartbeat
  ends itself after `SERVICE_IDLE_EXIT_SECONDS` once no live non-superseded
  Manager remains. Audited; it says nothing about the Manager's own idle
  clock and stays unchanged.

Theory (`docs/program-theory.md`): [WT-2a.13] (independent lifetimes; death
needs proof; a failed probe is `unknown`), [WT-2a.12] (a protocol is a
message), [ALT-WT-023] (internal services are always on; a Manager only
ensures them), the [WT-4.3] rows "Ask who owns this lifetime" and
"Availability and death are different questions", the [WT-4.4] rows "As few
concepts as possible" and "No over-armoring", [ALT-WT-003] (a PONG is
pushed to the requester's `ctrl_in`, never polled).

Earlier plans, as context (none is a veto):

- `docs/plans/2026-11-04-context-level-service-singletons-plan.md`
  (completed). §1.1: a launched service "does not hold its launcher alive".
  Owner ruling 10.1(b): a live non-superseded Manager keeps an idle service
  alive, so under a visible live Manager services do not cycle. C1 extends
  §1.1 to the cycle itself: a service whose exit or relaunch the Manager
  observes does not hold the Manager alive either. Neither ruling is
  reversed.
- `docs/plans/2026-11-05-manager-wake-idle-clock-and-settlement-repairs-plan.md`
  (completed) introduced `Manager._idle_clock_running`; this plan keeps it.

Diagnosis evidence (read-only hooks, clean worktrees):
`<scratchpad>/leak-diag/` `repro.py`, `repro_handoff.py`,
`run600-leak/hook/` (C1: `since_activity_s` held at 0 through each
`spawn_pending`/`missing_active_tid` window, including the 60-second
LivenessMonitor restart backoff), `runhandoff/hook/` (C2:
`uncertain_attempts` 3, 6, 9 ... from t ≈ 282 s with reason "active service
lacks live proof").

Local guidance: `AGENTS.md` §1.1, §4; `docs/agent-context/engineering-principles.md`;
`docs/agent-context/runbooks/testing-patterns.md`.

## 3. Context and Key Files

### Current structure (at `08c2f87b`; re-verify every line before editing)

`weft/core/manager.py`, `Manager.process_once` ends with the idle verdict:
return unless `_idle_clock_running()` (idle timeout enabled and no user-work
child); refresh broker activity; reset `_last_activity_ns` and return when
`_manager_owned_work_pending()`, `_managed_service_convergence_active(include_autostart=True)`
or `_autostart_ensure_obligation_pending()` holds; otherwise exit once
`now - _last_activity_ns >= idle_timeout`.

Every place a service cycle resets the clock today:

| Site | Function | What resets it during a heartbeat exit and relaunch |
|------|----------|------------------------------------------------------|
| A | `_cleanup_children` | any reaped child, services included |
| B | `_handle_work_message` | any reserved spawn row, the service request included |
| C | `_run_managed_service_convergence` | `if drained:` after the internal drain |
| D | `_idle_activity_queue_names` (read by `_update_idle_activity_from_broker`) | the newest row of the internal inbox or internal reserved queue: the Manager's own service request |
| E | `_manager_owned_work_pending` | an active child launch (the service's) or a pending internal-inbox row (a second service request waiting behind the first, since drains launch one child at a time) |
| F | `_managed_service_convergence_active` | `spawn_pending`, `missing_active_tid` (held through the 60-second TaskMonitor/LivenessMonitor restart backoff), `duplicate_scan_pending`, `uncertain_attempts`, `pong_probe_pending`, `internal_spawn_enqueued`, `internal_spawn_pending` |
| G | `_commit_child_launch_success` | any committed launch |

The one existing classifier is `Manager._child_is_supervision_only(child)`:
`child.internal_role` in the three built-in runtime classes. It already
decides `_user_work_children` (and so `_idle_clock_running`) and
`_detach_launched_services`. `_ManagerChildLaunchRequest` carries the same
`internal_role` field. For a spawn row, `Manager._spawn_request_service_key`
already recognises a trusted built-in service request (metadata `internal`
plus the matching runtime class), and `INTERNAL_SERVICE_KEYS` names the
three service keys.

C2: `Manager._observed_service_candidates_by_key` reads
`weft.state.services`, and for each desired key calls
`reduce_service_ownership(..., ttl_ns=self._manager_registry_retention_ns())`
(`MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS`, 300 s). Only
`decision.records` (latest row per owner, not expired) become candidates,
through `_service_candidate_from_service_owner_record`: a live host handle
is `live`, a dead host-pid handle is `terminal`, otherwise a keyed PING via
`_service_pong_candidate`. A Manager writes one `active` row per service it
launches and a `terminal` row when it reaps it; nobody refreshes the
`active` row. An adopter has no tracked child, so after 300 s it has no
candidate. `reduce_managed_service_state` then returns `wait_uncertain`,
then `degraded_wait`, forever.

### Files to modify

- `weft/core/manager.py` (C1: sites A to G; C2: `_observed_service_candidates_by_key`,
  one new method `_service_candidate_from_aged_owner_record`, one extracted
  helper shared with `_service_candidate_from_service_owner_record`)
- `weft/core/service_convergence.py` (C2: `ServiceConvergenceDecision.aged_live`)
- `tests/core/test_manager.py` (new tests; one existing test inverted)
- `tests/core/test_service_convergence.py` (C2: reducer field)
- `docs/specifications/03-Manager_Architecture.md` (delta below)
- `docs/plans/README.md` (index row, file count)

### Read first

- `docs/specifications/03-Manager_Architecture.md` [MA-1] items 5 and 7
- `weft/core/manager.py`: `process_once` (idle verdict), the seven sites
  above, `_child_is_supervision_only`, `_spawn_request_service_key`,
  `_observed_service_candidates_by_key`, `_service_pong_candidate`,
  `_advance_service_pong_probe`
- `weft/core/manager_services.py::reduce_managed_service_state`
- `tests/core/test_manager.py`: `test_manager_control_replies_do_not_reset_idle_clock`,
  `test_managed_service_pong_probe_is_nonblocking`, `drive_manager_until`,
  `FakeLaunchProcess`

### Comprehension questions (answer before the first edit)

1. Why is `_idle_clock_running()` not enough to keep service cycles off the
   idle clock? *Expected: it only stops the clock while a user-work child
   is live; it says nothing about resets. Services are already excluded
   from it, yet sites A to G reset `_last_activity_ns` to now.*
2. Why must an aged row whose host process is proven dead not be PINGed?
   *Expected: nobody reads a dead task's `ctrl_in`; a PING per pass would
   accumulate unconsumed rows (consume-to-disappear, [WT-2]) and an
   unanswered probe would keep the key uncertain for nothing.*

## 4. Invariants and Constraints

- **Real work still resets the clock.** A public spawn request (arrival,
  reservation, pending row), a pipeline-owned internal request (reservation,
  pending row, launch), a launch, commit or exit of a user-work or autostart
  child, autostart convergence and an `ensure` autostart obligation, and a
  canonical control request. Each keeps its current site.
- **One classifier.** A child or launch request is supervision exactly when
  `_child_is_supervision_only` says so; a spawn row exactly when
  `_spawn_request_service_key(..., desired_keys=INTERNAL_SERVICE_KEYS)` names
  a built-in key; convergence state exactly when its key is in
  `INTERNAL_SERVICE_KEYS`. No second list.
- **No new timer, no new state.** The idle clock keeps its one deadline.
  C2 adds no cache of PONG results and no registry refresh.
- **[WT-2a.13].** An unanswered or pending PING on an aged row is unknown
  evidence: it never yields `terminal` and never authorizes a relaunch.
  The 300-second retention rule stays: an aged row is never `live` by its
  handle alone.
- **[ALT-WT-003].** The re-proof uses the existing keyed PING with
  `reply_to` set to the Manager's `ctrl_in`; nothing polls the service's
  `ctrl_out`.
- Service supervision itself does not change: ensure, relaunch, restart
  backoff, duplicate convergence and detach on exit behave as before.
- Not touched: `_idle_clock_running`, `next_wait_timeout`, leadership,
  admission, the public dispatch path.

### 4a. Observable-difference register

| # | Change | Class | Theory basis |
|---|--------|-------|--------------|
| 1 | A Manager whose only events are internal-service exits, relaunch requests, launches, commits or unsettled convergence idle-exits `idle_timeout` after its last real work. Before: each such event restarted the window. | A (owner, C1) | [ALT-WT-023] (a Manager ensures services, it does not own their lifetime), [WT-2a.13] (independent lifetimes), 2026-11-04 plan §1.1 |
| 2 | A Manager may idle-exit while a built-in service is missing, in restart backoff, being relaunched, or unproved. The service request already written stays in `weft.spawn.internal`; a launch in flight is committed and detached by the existing cleanup ([IMPL.10]); the next Manager's ensure finds or relaunches the service. Before: the Manager waited for convergence to settle. | A (owner, C1) | as row 1; [MA-1] item 7 (services are context-level singletons, not Manager custody) |
| 2a | A due autostart scan (every second while the autostart directory exists) no longer holds an idle Manager at the verdict. The scan itself still runs in that turn's convergence pass; a manifest it finds still holds the Manager as an `ensure` obligation or a pending public spawn. | N (a check, not work; a manifest dropped in the last second before an idle exit waits for the next Manager, like any submission racing an exit) | [MA-1] item 7 "checking for work is not work"; [WT-4.3] "A quiet task costs nothing an operator can see" |
| 3 | A pipeline-owned internal spawn request no longer resets the clock through the newest-row broker probe; it still does when pending (owned work), when reserved, and when its child launches, commits or exits. | N | covered by the remaining sites; same outcome |
| 4 | An adopter Manager sends one keyed PING to each adopted service whose owner row is older than 300 s, at the ordinary active-service cadence, and keeps live proof from the PONG. Before: no PING; proof lost; `uncertain_attempts` grew and `degraded_wait` warnings repeated. Cost: about one PING/PONG pair per adopted service per stable audit (5 s), only on an adopter, only for live services. | A (owner, C2) | [WT-2a.12] (a protocol is a message), [WT-2a.13] (death needs proof), [ALT-WT-003] |
| 5 | A fresh Manager that finds a live service's owner row already aged waits for one PING round before deciding; before, it saw no candidate and enqueued a duplicate launch. | C (no loss; a duplicate is avoided) | [MA-1] item 7 singleton rule |
| 6 | An aged row whose scoped host process is proven dead contributes no candidate and gets no PING, exactly as before. | N | [WT-2a.13] |

### 4b. Rollout, rollback, one-way doors

No persisted format, queue name or public surface changes. Each slice is
one commit and reverts independently; C2's idle-exit assertion relies on C1,
so revert C2 before C1 if both go. No one-way door.

## Spec Baseline

- `08c2f87b81ec4e5ed164c0a5cfaedcf09ac3fb83` —
  `docs/specifications/03-Manager_Architecture.md`,
  `docs/specifications/05-Message_Flow_and_State.md` at plan authoring time.
  This plan revises 03.
- Promotion baseline identifier: the C1 commit (slice 1) and the C2 commit
  (slice 2) on branch `perf/fix-manager-idle-services`; strategy B lands
  each delta with its code.

## Proposed Spec Delta

Promotion strategy:

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| docs/specifications/03-Manager_Architecture.md | B — atomic with each slice's code | [MA-1] item 5; [MA-1] item 7; `_Implementation mapping_` [MA-1.5], [MA-1.6a]; `## Related Plans` |

### Slice 1 (C1) — [MA-1] item 5: append after "recurring liveness probes cannot hold an idle manager alive."

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```text
   recurring liveness probes cannot hold an idle manager alive. Only work
   resets the idle clock: a public spawn request, a pipeline-owned internal
   spawn request, the launch, commit or exit of a user-work or autostart
   child, unsettled autostart convergence or an `ensure` autostart
   obligation, and a canonical control request. The built-in internal
   services (heartbeat, `TaskMonitor`, `LivenessMonitor`) are supervision,
   not work: their exits, relaunch requests, launches and commits, and their
   convergence state (a pending spawn, a missing, backing-off or unproved
   owner, a duplicate scan, a pending `PING`), neither reset the idle clock
   nor hold an idle manager, so a manager whose services cycle still
   idle-exits on its own clock (item 7, [ALT-WT-023]). A due autostart scan
   is a check for work, not work: the turn's convergence pass runs it, and
   only the obligation or pending spawn it finds holds the manager.
```

### Slice 1 (C1) — `_Implementation mapping_` [MA-1.5]: replace the whole bullet

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```text
- [MA-1.5] Idle timeout — `Manager._idle_clock_running` couples idle deadline publication and evaluation; `Manager.process_once` (idle-timeout check), `Manager._note_control_request_activity` (only canonical control requests reset idle activity), `Manager._read_broker_timestamp`, `Manager._update_idle_activity_from_broker` and `Manager._idle_activity_queue_names` (public spawn queues only), `Manager._managed_service_convergence_active` (only autostart convergence resets idle activity; a due autostart scan does not), `Manager._manager_owned_work_pending`, `Manager._autostart_ensure_obligation_pending`. The one supervision classifier is `Manager._child_is_supervision_only` for children and launch requests, `Manager._is_service_supervision_row` for internal spawn rows, and `INTERNAL_SERVICE_KEYS` for convergence state; `Manager._cleanup_children`, `Manager._commit_child_launch_success` and `Manager._handle_work_message` reset the clock only for work, through `Manager._note_idle_activity`.
```

### Slice 2 (C2) — [MA-1] item 7: insert after "caller-owned TaskSpec metadata alone is not service authority."

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```text
   caller-owned TaskSpec metadata alone is not service authority. A
   service-owner registry row older than the 300-second registry retention
   window is not live proof by itself, whatever its runtime handle says:
   unless its scoped host process is proven dead, the manager re-proves that
   owner with the same non-blocking keyed `PING`, and an unanswered or
   pending probe is unknown evidence that never counts as terminal. This is
   how a manager that adopted a predecessor's services keeps their proof.
```

### Slice 2 (C2) — `_Implementation mapping_` [MA-1.6a]: insert after "`Manager._service_pong_candidate`,"

```text
`Manager._service_candidate_from_aged_owner_record` (aged rows from `weft/core/service_convergence.py::ServiceConvergenceDecision.aged_live`),
```

### `## Related Plans` — insert as the first bullet

```text
- [Manager Idle Clock Ignores Service Supervision; Adopted Services Keep Live Proof](../plans/2026-11-09-manager-idle-clock-and-adopted-service-proof-plan.md) keeps internal-service exits, relaunches and convergence off the idle clock and re-proves aged service-owner rows by keyed `PING` ([MA-1] items 5 and 7, [MA-1.5], [MA-1.6a]).
```

### Spans audited and unchanged

- [MA-1] item 1 "Pending internal spawn work is manager-owned convergence
  work": scheduling of the internal drain, not the idle clock. Unchanged.
- [MA-1] item 7 "Each service defines its own work and ends itself after
  `SERVICE_IDLE_EXIT_SECONDS`": the services' clock. Unchanged.
- [MA-1.4] "the existing 300-second unknown-record age cutoff": manager rows
  and recovery policy. Unchanged; slice 2 applies the same window to
  service rows and keeps it.
- [MF-3.2] heartbeat lifetime: unchanged.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|
| [MA-1] item 5, [MA-1.5] | The idle verdict counts "unsettled autostart convergence", including a due autostart scan. | It ignores `autostart_scan_due`. | A real handoff probe (Manager 2 adopting live services, after slice 2) stayed alive past 720 s: an instrumented rerun showed every idle-clock reset at the convergence-active check with reasons `["autostart_scan_due"]` and 1.000 to 1.001 s since the last scan. The one-second scan fell due between the turn's convergence pass and its verdict; aged-row PING traffic gave the Manager the unaligned wakes that hit that window. A due scan is a check for work, not work (the rule this plan states). | Slice 1 delta, last sentence of the item 5 insertion; applied in the slice 1 commit. |

## 5. Tasks

### Task 0 — baseline

Run the owning test files (§7) on the base commit and record pass counts.

### Task 1 (C1) — supervision never resets the idle clock

- RED first: add `test_service_cycle_does_not_reset_manager_idle_clock` in
  `tests/core/test_manager.py` (see §6) and confirm it fails on the base for
  the intended reason (the clock moved during the cycle).
- `_child_is_supervision_only`: widen the parameter type to
  `ManagedChild | _ManagerChildLaunchRequest` (both carry `internal_role`).
- Add `_note_idle_activity(*, supervision: bool)`: resets the clock unless
  `supervision`. Sites A, B and G call it instead of assigning the clock
  (this also keeps those functions under the repository's complexity limit
  without new suppressions).
- Site A, `_cleanup_children`: call it per reaped child with
  `supervision=self._child_is_supervision_only(child)`, in place of the one
  reset after the loop. Every other effect of `child_exited` stays.
- Site G, `_commit_child_launch_success`: call it with
  `supervision=self._child_is_supervision_only(request)`.
- Add `_is_service_supervision_row(queue_name, body) -> bool`: true only
  for a row of the internal inbox whose JSON payload
  `_spawn_request_service_key(payload, desired_keys=set(INTERNAL_SERVICE_KEYS))`
  names. Public rows are never parsed.
- Site B, `_handle_work_message`: call it with
  `supervision=self._is_service_supervision_row(source_queue, message)`.
- Site C: delete the `if drained:` reset; every drained row already passed
  through `_handle_work_message` (site B), and its launch counts at G or E.
- Site D, `_idle_activity_queue_names`: public inbox and public reserved
  only. Internal arrivals are seen by site E (unthrottled, every verdict)
  and site B.
- Site E, `_manager_owned_work_pending`: an active launch counts only when
  not supervision; the internal inbox counts only when it holds a row that
  `_is_service_supervision_row` rejects. The public inbox check is
  unchanged.
- Site F: `_managed_service_convergence_active_reasons` gains
  `include_internal: bool = True`; when false it leaves out probes, the
  duplicate scan and per-service state for `INTERNAL_SERVICE_KEYS` and the
  two internal-lane reasons. `_pending_service_probes_for_scope` gains the
  same keyword. `_managed_service_convergence_active` (idle verdict only)
  passes `include_internal=False` and ignores `autostart_scan_due`: a due
  scan is a check for work, run by the turn's convergence pass before the
  verdict; a manifest it finds holds the Manager through
  `_autostart_ensure_obligation_pending` or a pending spawn. Other callers
  are unchanged. (Added after the runtime probe; see the Deviation Log.)
- Invert `test_manager_idle_shutdown_waits_for_missing_internal_service`
  (owner decision C1): a missing internal service no longer holds the
  Manager.
- Apply the slice 1 spec delta.
- Stop if: a site needs a second classifier, a new field, or a timer.

### Task 2 (C2) — aged owner rows are re-proved by PING

- RED first: add
  `test_adopted_service_keeps_live_proof_past_registry_retention` (§6);
  confirm it fails on the C1 commit because no probe is sent.
- `ServiceConvergenceDecision.aged_live: tuple[ServiceOwnerRecord, ...] = ()`:
  the latest row per owner whose status is live and whose timestamp is
  past `ttl_ns`. Set in `reduce_service_ownership`; `records` and every
  other field are unchanged.
- Extract `_service_owner_record_evidence(record)` returning the candidate
  metadata and parsed runtime handle that
  `_service_candidate_from_service_owner_record` builds today, and use it
  there (no behaviour change).
- Add `_service_candidate_from_aged_owner_record(record, *, resolve_unanswered)`:
  `None` when the handle has host-pid authority and its process is not
  live, or when the row names no control queues; otherwise the result of
  `_service_pong_candidate(..., source="service-registry-pong")`. It never
  returns `terminal`.
- In `_observed_service_candidates_by_key`, for each `decision.aged_live`
  row whose owner is not locally terminal: add its probe key to
  `observed_probe_sources` and append the candidate when not `None`.
- Add a reducer test in `tests/core/test_service_convergence.py` for
  `aged_live`.
- Apply the slice 2 spec delta.
- Stop if: the aged path wants to return `terminal`, cache a PONG, or write
  the registry.

## 6. Testing Plan

Real Manager, real broker queues, real reducer; the only doubles are the
existing process seams.

- **C1** `test_service_cycle_does_not_reset_manager_idle_clock`: canonical
  Manager (TaskMonitor and LivenessMonitor off, heartbeat on),
  `idle_timeout` 60 s, `manager_mod.launch_task_process` patched to return
  `FakeLaunchProcess(pid=None)` (the established launch seam). Drive until
  the heartbeat is committed. Set the clock to `T0 = now - 30 s` (the
  existing time seam). Kill the fake heartbeat; drive until a heartbeat with
  a new TID is committed (reap, internal request, drain, launch, commit all
  run). Assert `_last_activity_ns == T0`, the Manager has not stopped, and
  the launch seam ran twice. Then move the clock past the window and assert
  one `process_once` reaches idle shutdown (`completed`). Named mutation:
  any of sites A to G counting service supervision as activity.
- **C1** inverted `test_manager_idle_shutdown_waits_for_missing_internal_service`
  (renamed `..._does_not_wait_...`): a missing heartbeat no longer holds an
  idle Manager.
- **C1** `test_manager_owned_work_excludes_only_service_requests_on_internal_lane`
  (site E): the heartbeat request a canonical Manager enqueues at
  construction is not owned work; a pipeline-shaped internal row is. Guards
  the "real work still resets the clock" invariant.
- **C1** `test_due_autostart_scan_alone_does_not_hold_an_idle_manager`:
  with an empty autostart directory, the scan due and this turn's
  convergence pass held (the ordering the probe hit), an expired idle clock
  reaches idle shutdown.
- **C2** `test_adopted_service_keeps_live_proof_past_registry_retention`:
  write a predecessor's heartbeat owner row (host-pid handle of a live
  process), construct a canonical Manager (it adopts the owner), then, with
  the existing instance seam `_manager_registry_retention_ns` shortened so
  the row is aged, run `_run_managed_service_convergence(force=True)`.
  Assert exactly one keyed PING on `T{owner}.ctrl_in` with `reply_to` the
  Manager's `ctrl_in`; deliver the PONG (`_write_service_pong`); run
  convergence again; assert the owner is still `active_tid`,
  `uncertain_attempts == 0`, and no internal spawn request exists. Restore
  the window, move the clock past `idle_timeout`, and assert idle shutdown.
- **C2** reducer: `reduce_service_ownership` reports an expired latest live
  row in `aged_live`, not in `records`, and never reports a superseded
  older row of the same owner or a terminal row.
- Sensitivity, per slice: revert only that slice's code, rerun its test,
  record the failure, restore exactly (`git diff` empty).

Not mocked: queues, registry rows, reducer, PING/PONG control path, child
table, idle verdict.

## 7. Verification and Gates

```bash
./.venv/bin/python -m pytest tests/core/test_manager.py tests/core/test_manager_services.py tests/core/test_service_convergence.py tests/tasks/test_service_task.py tests/tasks/test_heartbeat.py -n 4 -q
./bin/pytest-pg tests/core/test_manager.py tests/core/test_manager_services.py tests/core/test_service_convergence.py tests/tasks/test_service_task.py tests/tasks/test_heartbeat.py
./.venv/bin/ruff check .
./.venv/bin/ruff format --check <touched files>
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
```

Runtime observation after landing: rerun `repro_handoff.py` (Manager 2
idle-exits near 600 s, no `uncertain_attempts`) and the unlinked-database
`repro.py` (Manager exits near 600 s).

## 8. Review and execution record

Author family: Anthropic (Claude). Cross-family review: owned by the
coordinating agent; not yet run.

Comprehension answers (before the first edit): as recorded under §3.

Slice 1 (C1), 2026-11-09:

- RED on `08c2f87b`: `test_service_cycle_does_not_reset_manager_idle_clock`
  failed with "service supervision moved the idle clock by 30.004s".
- Sensitivity: with only the slice-1 `weft/core/manager.py` change reverted,
  all three slice-1 tests failed (clock moved 30.004 s; missing service
  held the Manager; pending heartbeat request counted as owned work);
  restored byte-for-byte (`git diff` identical to the saved patch). A
  site-D-only mutation (internal lane back in the broker probe) also failed
  the cycle test, at its idle-shutdown step. The scan-due amendment (see
  the Deviation Log): with only that line reverted,
  `test_due_autostart_scan_alone_does_not_hold_an_idle_manager` failed
  (`should_stop` stayed `False`); restored byte-for-byte.
- Gates: owning files on SQLite `-n 4`: 445 passed, 3 skipped; on
  PostgreSQL (`./bin/pytest-pg`): 432 passed, 15 skipped; `tests/specs`:
  165 passed; ruff, ruff format, repo mypy (499 files), `bin/check-doc-paths`:
  clean.

## 9. Out of Scope

- TaskMonitor's runtime prune deletes service-owner rows older than
  `RUNTIME_PRUNE_DEFAULT_MIN_AGE_SECONDS` (one hour), live owners included
  (`plan_service_owner_history_prune`). After that an adopter has no row to
  re-prove. Pre-existing; owner question in §10.
- An adopted service that dies after its row ages is not replaced by the
  adopter (no candidate, as before). Pre-existing; owner question in §10.
- The unlinked-database leak itself (deleting a live context directory) is
  out of contract ([CTX.5], [WT-2a.11]); recorded as a lesson in a separate
  documentation commit.

## 10. Open owner questions

1. Should TaskMonitor's runtime prune keep the latest `active` row of a
   service whose host process is live? Today an adopter loses its only row
   after one hour.
2. Should an aged row whose scoped host process is proven dead count as
   `terminal` (as an in-window row does), so an adopter relaunches a dead
   adopted service? This plan keeps today's "no candidate".

## 11. Fresh-Eyes Review

Done after drafting. Findings applied: site E needed the internal-inbox
row check because drains launch one child at a time, so a second service
request waits pending; site C is redundant with B and is deleted rather
than conditioned; the aged path must gate on host-pid death to avoid
PINGs piling up on a dead task's `ctrl_in`.

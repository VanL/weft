# Manager Idle Clock Ignores Service Supervision; Adopted Services Keep Live Proof

Status: draft
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1] items 5 and 7, [MA-1.5], [MA-1.6a]; docs/specifications/01-Core_Components.md [CC-2] (BaseTask, ServiceTask and HeartbeatTask deadlines); docs/specifications/05-Message_Flow_and_State.md [MF-3.2], [MF-5] (service deadlines)
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
  retention rule stays. No new mechanism. *Superseded by the revision
  below; kept as the record of what slice 2 first built.*
- **C2, revised (owner decision B, 2026-11-09).** The owner: "the existing
  service should refresh the row, although I don't know at what frequency.
  If we start a new service, it should be taken care of by the existing
  coordination/supersession process." Then, choosing option B: every task
  launched with a managed service key, internal service or autostart,
  refreshes its own registry owner row on its own clock through
  `BaseTask.next_wait_timeout`, and the C2 aged-row PING path is removed.
  One mechanism.

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
C2 (revised) has every task launched with a managed service key re-stamp
its own owner row on its own clock, so the row a reader sees is never older
than a third of the retention window while its owner lives: an adopter, or a
successor after a launcher crash, keeps live proof from the registry with no
PING and launches no duplicate.

## 2. Source Documents

Governing specs (baseline in `## Spec Baseline`):

- `docs/specifications/03-Manager_Architecture.md` [MA-1] item 5 (idle
  timeout), item 7 (managed services, live service ownership, service PING
  fallback), the `_Implementation mapping_` lines [MA-1.5] and [MA-1.6a]
- `docs/specifications/05-Message_Flow_and_State.md` [MF-3.2]: the heartbeat
  ends itself after `SERVICE_IDLE_EXIT_SECONDS` once no live non-superseded
  Manager remains. Nothing here about the Manager's idle clock changes; the
  revised C2 adds the owner-row refresh to the heartbeat's and TaskMonitor's
  stated deadlines.
- `docs/specifications/01-Core_Components.md` [CC-2]: BaseTask owns the
  refresh; ServiceTask keeps its deadline; the HeartbeatTask deadline list.

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
then `degraded_wait`, forever. An autostart child carries the same
Manager-written row: its launch's `service_key` is the autostart source
(`Manager._launch_child_task`), and a Consumer never refreshes it, so after a
launcher crash a successor that starts more than 300 s after the launch sees
no live owner and, with empty state, enqueues a duplicate (base behaviour).
The Manager already refreshes its own row this way
(`Manager._refresh_manager_registration`, every 30 s).

### Files to modify

- `weft/core/manager.py` (C1: sites A to G; C2 as revised: the
  extracted `_service_owner_record_evidence` stays; the aged-row PING path
  of the first C2 is removed)
- `weft/core/tasks/base.py` (revised C2: `managed_service_owner_key`,
  `BaseTask._service_owner_refresh_timeout`,
  `BaseTask._refresh_service_owner_row`, the refresh deadline in
  `BaseTask.next_wait_timeout`, the call in `BaseTask.process_once`, the
  `services_registry` support route)
- `weft/core/tasks/service.py` (revised C2: `ServiceTask.next_wait_timeout`
  keeps the refresh deadline)
- `weft/_constants.py` (revised C2: `SERVICE_OWNER_REFRESH_INTERVAL_SECONDS`,
  derived as a third of `MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS`)
- `tests/core/test_manager.py` (new tests; one existing test inverted)
- `tests/tasks/test_service_task.py` (revised C2: refresh tests)
- `docs/specifications/01-Core_Components.md`,
  `docs/specifications/05-Message_Flow_and_State.md` (revised C2: deadlines;
  plan backlinks)
- `docs/specifications/07-System_Invariants.md` (review F3: [QUEUE.7] names
  the `services_registry` support route; plan backlink)
- `CHANGELOG.md`, `README.md` (review F1: the change and the upgrade step)
- `docs/ruff-suppression-registry.md` (C2: one `C901` suppression fewer)
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
2. (Revised C2.) Why does the refresh re-write the launcher's row instead
   of building a new one from the task's own fields? *Expected: the
   launcher's row already carries the authoritative runtime handle and its
   provenance (`manager_tid`, shown by `weft status`); re-stamping keeps the
   row a reader sees identical except for its broker timestamp, and the
   refresh needs no second payload builder. A task with no live row of its
   own has nothing to refresh.*

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
- **No new state for C1.** The idle clock keeps its one deadline.
- **One proof mechanism for adopted services (revised C2).** The owner
  re-stamps its own row; no Manager PINGs an aged row and no reader caches
  proof. The 300-second retention rule stays: an aged row is never `live`.
- **One refresh clock per managed task, on its own clock.** The deadline is
  published through `next_wait_timeout` and serviced in `process_once`;
  nothing renews per turn. Interval `SERVICE_OWNER_REFRESH_INTERVAL_SECONDS`,
  derived from the retention constant, with no knob. The [WT-7] falsifier
  "Event-driven against absence" asks which event cannot arrive: nothing
  announces that a row is nearing the retention window, because readers
  judge absence by age, so the owner re-asserts on a clock.
- **A refresh is not work.** It touches neither `_has_work` (the task's idle
  exit) nor any Manager idle-activity queue (C1). An ordinary task without a
  managed service key publishes no refresh deadline and writes nothing.
- **A stopping or terminal task does not refresh**, so a dead or wedged
  owner's row ages out as before ([WT-2a.13]: absence stays `unknown`).
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
| 4 | Revised C2: every task a Manager launched with a managed service key (heartbeat, TaskMonitor, LivenessMonitor, autostart services) re-writes its latest live owner row unchanged every 100 s (a third of the 300 s retention) and deletes the rows that one replaces; an adopter keeps live proof from the registry past 300 s, with no PING. Before: proof lost after 300 s; `uncertain_attempts` grew and `degraded_wait` warnings repeated. Cost: one registry write and one delete per managed task per 100 s, one more wake per 100 s for each managed task, and the wakes those writes give tasks with a registry interest. | A (owner, decision B) | [WT-2a.13] (death needs proof; a live owner keeps proving itself), [WT-4.2] "One owner for each resource" (the owner keeps its own row), [ALT-WT-023] |
| 5 | After a launcher crash, a successor that starts more than 300 s after an `ensure` autostart service launched sees it live from its refreshed row and launches no duplicate. Before (base): no live candidate, `start_now`, two copies, and duplicate convergence could not see the original. | C (no loss; a duplicate is avoided) | [MA-1] item 7 singleton rule; owner decision B |
| 6 | A live owner's latest row is never older than about 100 s, so neither the 300 s retention nor TaskMonitor's runtime prune (expired rows, after 3,600 s) can remove it; superseded own rows are deleted by the refresh itself. Closes owner question 1. | C | consume-to-disappear ([WT-2]) |
| 7 | A stopping, terminal, dead or wedged owner stops refreshing; its row ages out as before, with no PING. | N | [WT-2a.13] |
| 8 | The first C2 (aged-row PING, `aged_live`, review F1's probe-key rule) is removed; no Manager writes PINGs to service owners for proof any more. | N (replaced by row 4) | [WT-4.4] "As few concepts as possible" |
| 9 | An ordinary task without a managed service key publishes no refresh deadline and writes no registry row. | N | [WT-4.3] "A quiet task costs nothing an operator can see" |
| 10 | Mixed versions only (review F1): services and autostart tasks still running from pre-refresh code never re-stamp their rows; 300 s after their last row a new Manager sees no live owner (a fresh successor starts a duplicate; an existing adopter waits). Removed by the documented upgrade step, `weft system stop` before or while upgrading (§4b). | D (during mixed-version operation only) | [WT-4.4] "No compatibility code: contract changes update all producers and consumers in one coordinated release"; owner rule: no backward-compatibility shims, break cleanly, note it in the CHANGELOG |
| 11 | A task launched with a managed service key declares `weft.state.services` as a support route, so it can no longer name that queue as its outbox or another queue role; the topology check rejects it at construction. Ordinary tasks keep the baseline topology (review F3). | D (an unreasonable configuration, rejected loudly) | [WT-4.2] "One owner for each resource" (the registry route belongs to the task's owner-row duty); [WT-4.3] closed reactor inputs; [QUEUE.7] collision guard |

### 4b. Rollout, rollback, one-way doors

No persisted format, queue name or public surface changes. Each slice is
one commit and reverts independently; C2's idle-exit assertion relies on C1,
so revert C2 before C1 if both go. No one-way door.

Upgrade (revised C2, review F1). A clean break with an operator-controlled
transition, per the owner's standing rule (no backward-compatibility code;
break cleanly and note it in the CHANGELOG). Services and autostart tasks
already running from code that predates the refresh never re-stamp their
rows, so 300 s after their last launcher-written row a Manager on the new
code sees no live owner: a fresh successor starts a duplicate, and an
existing adopter waits as `wait_uncertain`. Step: run `weft system stop` in
each context before or while upgrading. It stops the active Manager, whose
STOP drain first stops every live user-work task of the context, autostart
tasks included, found through their own snapshots ([MA-3] phase 1); it then
stops the context's internal services by registry record. The next Manager
launches services and autostart tasks under the new code. A context with no
active Manager has no drain, so an autostart task still running there is
stopped with `weft task stop`. Recorded in the CHANGELOG, README (internal
services) and spec 03 [MA-1] item 7. No second proof mechanism is kept for
the transition.

A mixed-version check is not added as a separate test: an old service that
never refreshes is exactly the "task that stops refreshing" path, already
covered by phase 1 of `test_adopted_service_keeps_live_proof_by_refreshing_its_owner_row`
(an existing adopter: no proof, no PING) and by the autostart test's
sensitivity run (a fresh successor: duplicate launch); the stop step's
mechanics are covered by the existing `weft system stop` tests.

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

### Slice 2 (C2, revised by owner decision B) — [MA-1] item 7: insert after "caller-owned TaskSpec metadata alone is not service authority." (replaces the first slice-2 sentence about re-proving aged rows by `PING`)

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```text
   caller-owned TaskSpec metadata alone is not service authority. Every task
   a manager launched with a managed service key, a built-in internal service
   or an autostart service, keeps its own service-owner row young: on its own
   clock, every `SERVICE_OWNER_REFRESH_INTERVAL_SECONDS` (a third of the
   300-second registry retention window), it re-writes its latest live owner
   row unchanged and deletes the rows that one replaces. A refresh is neither
   work for the task's idle exit nor manager idle activity, and an ordinary
   task without a service key does no refresh. So a live owner's latest row
   never ages out of the retention window or reaches the runtime prune, and a
   manager that adopted a predecessor's services, or a successor after a
   launcher crash, sees them live from the registry and launches no
   duplicate; a duplicate that does start converges as above. A task that
   stops refreshing is dead or wedged, and its row ages out as before.
```

### Slice 2 (revised) — `_Implementation mapping_` [MA-1.6a]: insert after "`Manager._service_pong_candidate`," (replaces the aged-row mapping)

```text
`weft/core/tasks/base.py::BaseTask._refresh_service_owner_row` with `BaseTask._service_owner_refresh_timeout` and `managed_service_owner_key` (owner-row refresh, published through `BaseTask.next_wait_timeout` and `ServiceTask.next_wait_timeout`),
```

### Slice 2 (revised) — `01-Core_Components.md` [CC-2] and `05-Message_Flow_and_State.md` [MF-3.2], [MF-5]

- BaseTask responsibilities: insert after "own reserved-queue policy
  application": "for a task a manager launched with a managed service key,
  re-stamp its own service-owner row in `weft.state.services` on its own
  clock and publish that refresh deadline through `next_wait_timeout()`
  ([MA-1] item 7); a task without a service key does no refresh".
- ServiceTask: after "suppresses the inherited `BaseTask` poll-report
  deadline" add "; it keeps the `BaseTask` owner-row refresh deadline
  ([MA-1] item 7)".
- HeartbeatTask (01) and the heartbeat bullets of [MF-3.2] (05): the
  empty-table deadlines are the idle clock and the owner-row refresh; with
  rows, the refresh joins the due emission and lease expiry.
- TaskMonitor ([MF-5]): "The monitor's only clock of its own is its cycle
  deadline …; the shared service idle clock and owner-row refresh ([MA-1]
  item 7) come from its base classes."

### `## Related Plans` — insert as the first bullet

```text
- [Manager Idle Clock Ignores Service Supervision; Adopted Services Keep Live Proof](../plans/2026-11-09-manager-idle-clock-and-adopted-service-proof-plan.md) keeps internal-service exits, relaunches and convergence off the idle clock, and has every managed service re-stamp its own service-owner row on its own clock ([MA-1] items 5 and 7, [MA-1.5], [MA-1.6a]).
```

### Spans audited and unchanged

- [MA-1] item 1 "Pending internal spawn work is manager-owned convergence
  work": scheduling of the internal drain, not the idle clock. Unchanged.
- [MA-1] item 7 "Each service defines its own work and ends itself after
  `SERVICE_IDLE_EXIT_SECONDS`": the services' clock. Unchanged.
- [MA-1.4] "the existing 300-second unknown-record age cutoff": manager rows
  and recovery policy. Unchanged; the revised slice 2 keeps the same window
  for service rows and keeps live rows inside it.
- [MA-1] item 4 "Readers do not delete registry rows": unchanged; a
  refreshing task deletes only rows it owns, as the row's writer.
- [MF-3.2] heartbeat lifetime: unchanged; only its deadline list gains the
  refresh.
- Theory [ALT-WT-004] "TaskMonitor's only clock is its cycle deadline":
  not edited (a theory revision is out of scope); the entry refuses a
  heartbeat as TaskMonitor's clock, and the refresh is a base-class
  registry obligation, not a TaskMonitor work clock. Flagged for the owner.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|
| [MA-1] item 7, [MA-1.6a] | Slice 2 re-proves an aged owner row by a keyed `PING` (`aged_live`, `_service_candidate_from_aged_owner_record`, review F1's probe-key rule). | Every task launched with a managed service key re-stamps its own owner row every `SERVICE_OWNER_REFRESH_INTERVAL_SECONDS`; the PING path, `aged_live` and their tests are removed. | Owner decision B, 2026-11-09 ("the existing service should refresh the row"). One mechanism; it also covers adopted autostart services after a launcher crash, which the PING path covered and internal-only refresh would not. | Revised slice 2 delta (03 item 7 and [MA-1.6a]; 01 [CC-2]; 05 [MF-3.2], [MF-5]); applied in the revision commit. |
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

### Task 2 (C2, revised by owner decision B) — managed services refresh their own owner rows

- RED first, on `34faeb6a`:
  `test_adopted_service_keeps_live_proof_by_refreshing_its_owner_row`,
  `test_adopted_autostart_service_after_launcher_crash_is_live_not_duplicated`
  and `test_managed_service_task_restamps_its_owner_row_on_its_own_clock`
  (§6).
- `weft/_constants.py`: `SERVICE_OWNER_REFRESH_INTERVAL_SECONDS =
  MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS / 3`; not configurable.
- `weft/core/tasks/base.py`:
  - `managed_service_owner_key(taskspec)`: the service key, only when the
    metadata carries the Manager service authority stamp (public submission
    strips reserved service metadata, so ordinary tasks have none).
  - `__init__`: `_service_owner_key`, `_service_owner_refreshed_ns`
    (monotonic, starts at construction; the launcher wrote a fresh row).
  - `_service_owner_refresh_timeout()`: `None` without a key or while
    stopping or terminal; else the time to the next refresh.
  - `next_wait_timeout()`: the minimum of that and the poll-report timing.
  - `_refresh_service_owner_row()`: when due, read the registry once
    (`iter_queue_json_entries` + `collect_service_owner_records` for the key),
    take the task's own rows, and if the latest is live re-write its payload
    unchanged and delete every own row it replaces. A missing or non-live
    row is left alone; a broker error is logged and retried next interval.
  - `process_once()`: call it after `_process_reactor_turn()` on the
    non-stopping branch.
  - `_reactor_support_routes()`: declare `services_registry` for a keyed
    task ([QUEUE.7]).
- `weft/core/tasks/service.py`: `ServiceTask.next_wait_timeout()` returns the
  minimum of the idle remainder and the refresh deadline (it does not call
  the BaseTask poll-report part, which services suppress).
- Remove the first C2: `Manager._service_candidate_from_aged_owner_record`,
  the aged-row block (and review F1's probe-key rule) in
  `_observed_service_candidates_by_key`, `ServiceConvergenceDecision.aged_live`,
  `test_adopted_service_keeps_live_proof_past_registry_retention`,
  `test_aged_owner_row_of_dead_host_process_is_not_pinged` and
  `test_aged_live_owner_is_reported_apart_from_usable_records`. Keep the
  extracted `_service_owner_record_evidence` (it still serves
  `_service_candidate_from_service_owner_record` and keeps that method below
  the complexity limit).
- Apply the revised slice 2 spec delta.
- Duplicates: no new code. With every live owner's row fresh, the existing
  duplicate convergence sees both owners and sends KILL to the non-canonical
  one (`test_task_monitor_duplicate_live_candidates_get_kill_signal`,
  `test_liveness_monitor_duplicate_live_candidates_converge`).
- Stop if: the refresh wants a per-turn write, a new knob, a Manager-side
  PING, or a second payload builder.

### Task 2 (first C2, superseded; kept as record) — aged owner rows are re-proved by PING

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
  `None` when the owner is locally terminal, when the handle has host-pid
  authority and its process is not live, or when the row names no control
  queues; otherwise the result of
  `_service_pong_candidate(..., source="service-registry-pong")`. It never
  returns `terminal`.
- In `_observed_service_candidates_by_key`, for every `decision.aged_live`
  row whose helper result is not `None`: add its probe key to
  `observed_probe_sources` (so the end-of-scan sweep keeps its pending
  probe) and extend the key's candidates with that result. A row the helper
  excludes keeps no key, so the sweep retires any probe still pending for
  that owner, as before (review F1). Written as comprehensions, which keeps
  the method under the complexity limit.
- The extraction removes the need for the `C901` suppression on
  `_service_candidate_from_service_owner_record`: drop it and lower
  `RUFF-SUP-012` and the global inventory in
  `docs/ruff-suppression-registry.md` (regenerate the index with
  `bin/ruff_suppression_index.py --write`).
- Add a reducer test in `tests/core/test_service_convergence.py` for
  `aged_live`, and `test_aged_owner_row_of_dead_host_process_is_not_pinged`
  (no PING, no candidate for an aged row of a dead host process).
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
- **C2 revised** `test_adopted_service_keeps_live_proof_by_refreshing_its_owner_row`:
  a predecessor's heartbeat row, a canonical successor that adopts it, and
  an in-process task with the heartbeat's service key and TID. With only the
  original row treated as past retention (a seam on
  `service_convergence.timestamp_is_expired` for that one row), convergence
  gives no live proof and writes no PING (the row ages out as before). The
  task's refresh comes due (seam on its refresh clock) and one turn
  re-stamps the row; convergence then sees the owner live with no PING and
  no relaunch, the Manager's idle clock has not moved, and an expired idle
  clock reaches idle shutdown.
- **C2 revised** `test_adopted_autostart_service_after_launcher_crash_is_live_not_duplicated`:
  an `ensure` autostart manifest, the crashed launcher's row for its
  service, the in-process service task refreshing it, then a successor
  started with the original row past retention: no PING, no launch request,
  `active_tid` is the original, and its only candidate is `live`.
- **C2 revised** (`tests/tasks/test_service_task.py`)
  `test_managed_service_task_restamps_its_owner_row_on_its_own_clock`
  (nothing before the interval; the deadline is published; one re-stamped
  row, same payload, newer stamp, the replaced row gone; nothing on the next
  turn), `test_ending_service_task_stops_refreshing_its_owner_row`
  (parametrized: a stopping task, and a terminal task that is not stopping,
  so each guard is exercised on its own; named
  `test_stopping_service_task_stops_refreshing_its_owner_row` before review
  F3's round) and `test_task_without_service_key_writes_no_registry_rows`.
- **Review F3** `test_managed_task_cannot_use_the_service_registry_as_a_queue_role`:
  a Consumer whose outbox is `weft.state.services` is rejected at
  construction (`ValueError` naming `services_registry`) when it carries a
  managed service key, and built as before when it does not (register
  row 11).
- **First C2 (superseded, tests removed)** `test_adopted_service_keeps_live_proof_past_registry_retention`:
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
  older row of the same owner or a non-live latest row.
- **C2** `test_aged_owner_row_of_dead_host_process_is_not_pinged`: an aged
  row whose host-pid handle names a dead process yields no candidate and no
  row on its `ctrl_in`, and a probe already pending for that owner is
  retired by the scan (review F1).
- Sensitivity, per slice: revert only that slice's code, rerun its test,
  record the failure, restore exactly (`git diff` empty).

Not mocked: queues, registry rows, reducer, PING/PONG control path, child
table, idle verdict.

## 7. Verification and Gates

```bash
./.venv/bin/python -m pytest tests/core/test_manager.py tests/core/test_manager_services.py tests/core/test_service_convergence.py tests/tasks/test_service_task.py tests/tasks/test_heartbeat.py tests/tasks/test_task_monitor.py tests/tasks/test_liveness_monitor.py tests/tasks/test_task_execution.py tests/specs -n 4
./bin/pytest-pg tests/core/test_manager.py tests/core/test_manager_services.py tests/core/test_service_convergence.py tests/tasks/test_service_task.py tests/tasks/test_heartbeat.py tests/tasks/test_task_monitor.py tests/tasks/test_liveness_monitor.py tests/tasks/test_task_execution.py
./.venv/bin/ruff check .
./.venv/bin/ruff format --check <touched files>
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
```

Runtime observation after landing: rerun `repro_handoff.py` (Manager 2
idle-exits near 600 s, no `uncertain_attempts`) and the unlinked-database
`repro.py` (Manager exits near 600 s).

## 8. Review and execution record

Author family: Anthropic (Claude). Cross-family review: owned by the
coordinating agent; not yet run. The other-family two-factor fit account and
the theory-versus-code audit refresh ([WT-7]) are due with that review and
at closure.

Author's two-factor fit account ([WT-7] structure):

- Verdict: fits.
- Rests on: [WT-2a.13] (a Manager, its tasks and the context-level services
  have independent lifetimes; absence is `unknown`), [ALT-WT-023] (a
  Manager ensures services and does not own their lifetime), [WT-2a.12]
  and [ALT-WT-003] (proof by a keyed PING pushed to the requester's
  `ctrl_in`), the [WT-4.4] row "As few concepts as possible" (one existing
  classifier, one existing probe; no timer, cache or registry refresh).
- Refuses: refreshing service-owner rows or extending the retention window
  (a new mechanism; [ALT-WT-010]); treating an unanswered PING as death
  ([WT-2a.13]); a second idle clock for services (the [WT-7] falsifier
  "Event-driven against absence").
- Predicts, falsifiably: a Manager with no work idle-exits within
  `idle_timeout` of its last real work however its services behave; an
  adopter shows no `uncertain_attempts` past 300 s while its services live;
  no PING is ever written to a dead service's `ctrl_in`. A Manager held
  alive by service churn, or a growing `T{dead}.ctrl_in`, falsifies it.
- Fits worst: the [WT-4.3] row "A quiet task costs nothing an operator can
  see": an adopter now sends one PING per adopted service on each
  reconciliation pass that collects registry evidence (up to about once a
  second while the autostart directory exists) for as long as it lives
  (register row 4).

Revised account for the C2 revision (owner decision B):

- Verdict: fits.
- Rests on: [WT-2a.13] (a live owner keeps proving itself; absence stays
  `unknown`), the [WT-4.2] row "One owner for each resource" (the owner
  writes, refreshes and replaces its own row), [ALT-WT-023], the [WT-4.4]
  row "As few concepts as possible" (one proof mechanism instead of two).
- Refuses: a Manager-side PING for proof (removed); a per-turn renewal (the
  2026-11-02 rule that rows carry their own lease with no per-tick renewal);
  a configurable interval; extending the retention window.
- Predicts, falsifiably: a live managed task's latest owner row is never
  older than about 100 s; no successor duplicates a live service; a task
  without a service key never writes the registry. A live owner's row
  pruned, or a registry write from an ordinary task, falsifies it.
- Fits worst: the [WT-4.3] row "A quiet task costs nothing an operator can
  see" and the [WT-7] falsifier "Event-driven against absence": every
  managed service gains one clock (one write and one delete per 100 s). The
  answer recorded under §4 is that nothing announces a row nearing the
  window, so the owner re-asserts on a clock.

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

Slice 2 (C2), 2026-11-09:

- RED on the slice-1 commit: `test_adopted_service_keeps_live_proof_past_registry_retention`
  failed with "No pending service probe for <owner tid>" (the aged row was
  dropped and nothing re-proved it).
- Sensitivity: with only the slice-2 code (`weft/core/manager.py`,
  `weft/core/service_convergence.py`) reverted, the adoption test failed
  the same way and the reducer test failed (`ServiceConvergenceDecision`
  has no `aged_live`); restored byte-for-byte. A mutation that removes only
  the dead-host gate made `test_aged_owner_row_of_dead_host_process_is_not_pinged`
  fail (a `ping_pending` candidate appeared for the dead owner).
- Deviation from the drafted file list, within scope: the extraction made
  the `C901` suppression on `_service_candidate_from_service_owner_record`
  unused, so it was removed, `RUFF-SUP-012` lowered to 2 directives, the
  global inventory to `C901=110`, and the ratchet mirror in
  `tests/specs/test_ruff_policy.py` to 320 directives / 110 `C901` (the
  2026-11-04 precedent `95a8893a`).
- Gates: owning files plus `tests/specs` on SQLite `-n 4`: all passed after
  the ratchet update (165 spec tests); owning files on PostgreSQL
  (`./bin/pytest-pg`): 435 passed, 15 skipped; ruff, ruff format, repo mypy
  (499 files), `bin/ruff_suppression_index.py --check`,
  `bin/check-doc-paths`: clean.

Runtime probes and final gates, 2026-11-09 (clean handoff: Manager 1 with a
20 s idle timeout launches the services and exits; Manager 2 adopts them;
every probe stops its context in `finally`; diagnostic hooks were read-only
except one that shortened the service-row retention to 30 s):

- Slices 1 and 2 as first committed, natural retention, Manager 2 idle
  timeout 600 s: Manager 2 kept proof (per-service `uncertain_attempts`
  alternated 0 and 1 as each PING was answered, never accumulating) but was
  still alive at 721 s. A hook on every write of the idle clock showed each
  reset at the convergence-active verdict with reasons
  `["autostart_scan_due"]`, 1.000 to 1.001 s after the last scan (15 resets
  between 290 s and 544 s in a 420 s-timeout rerun). Recorded in the
  Deviation Log and fixed in slice 1.
- After the fix, 30 s retention, Manager 2 idle timeout 150 s: Manager 2
  exited at 150.2 s with no idle-clock write after construction.
- After the fix, natural retention, Manager 2 idle timeout 600 s:
  Manager 2 exited at 600.8 s (5 s polling) with no idle-clock write after
  construction, through the aged-row PING phase from about 280 s; it never
  relaunched a service: the context stop found exactly the three services
  Manager 1 had launched, by their original TIDs.
- Final gates on the rebuilt branch: owning files plus `tests/specs` on
  SQLite `-n 4`: 610 passed, 3 skipped; the three `slow` tests of
  `tests/core/test_manager.py`: passed; owning files on PostgreSQL
  (`./bin/pytest-pg`): 436 passed, 15 skipped; ruff, ruff format, repo mypy
  (499 files), `bin/ruff_suppression_index.py --check`,
  `bin/check-doc-paths`: clean.

Cross-family review, 2026-11-09: Tier 1, Codex (reviewer family OpenAI;
author family Anthropic), run by the coordinating agent over the plan, the
spec delta and the diff of the three commits. Verdict: no blocker. The
reviewer found the scan-due exclusion consistent with "supervision-only
activity does not reset idle", found no `D` register rows, judged that no
further algorithm or mechanism is warranted, and confirmed open owner
question 1 (§10).

| ID | Severity | Location | Finding | Disposition |
|----|----------|----------|---------|-------------|
| F1 | P3 | `weft/core/manager.py`, aged-row block of `_observed_service_candidates_by_key` | Every aged live-status row kept its probe key even when death evidence excluded it, so a probe already pending for that owner was neither advanced nor retired; after a replacement started it still held convergence active and forced registry scans until the row disappeared. Baseline retired it. | Accepted, fixed: only an aged row routed to the PING keeps its probe key; an excluded row's pending probe is retired by the existing end-of-scan sweep (no new mechanism). `test_aged_owner_row_of_dead_host_process_is_not_pinged` now starts with a pending probe: RED on the previous tip (the probe survived the scan), green after; with only the fix reverted it failed again, restored byte-for-byte. |
| F2 | P3 | Register row 4 | Understated the PING cadence (a due autostart scan also triggers reconciliation, so up to one PING per second after the previous PONG) and the scope (forced and duplicate-scan passes of locally tracked services). | Accepted, fixed: row 4 and the fit account's "fits worst" line now state the shared reconciliation cadence and scope. The cadence itself is unchanged. |

Gates after the review fixes: owning files plus `tests/specs` on SQLite
`-n 4`: 610 passed, 3 skipped; owning files on PostgreSQL
(`./bin/pytest-pg`): 436 passed, 15 skipped; ruff, ruff format, repo mypy
(499 files), `bin/ruff_suppression_index.py --check`,
`bin/check-doc-paths`: clean.

C2 revision (owner decision B), 2026-11-09:

- Before building, the author reported one case an internal-only refresh
  could not cover: an `ensure` autostart service adopted after a launcher
  crash (its row is Manager-written and a Consumer never refreshed it). The
  owner chose option B: every task launched with a managed service key
  refreshes, and the PING path goes.
- Design note: the refresh re-writes the task's latest live owner row
  unchanged (fresh broker stamp) instead of building a new payload with
  `build_service_owner_payload`; that row was built by the launcher through
  that builder and carries the authoritative runtime handle and `manager_tid`
  provenance that `weft status` shows, which a task-built row would lose.
- RED on `34faeb6a`: the internal adoption test failed because the Manager
  wrote a keyed PING to the aged owner; the autostart test failed with
  `active_tid` `None` (state `ping_pending`); the refresh test failed with no
  refresh deadline. The two guard tests (stopping task, task without a key)
  were green on `34faeb6a`, which never refreshes.
- Sensitivity: with only the refresh code (`base.py`, `service.py`,
  `_constants.py`) reverted, all three behaviour tests failed, the autostart
  one by enqueuing a duplicate launch (the base behaviour); restored
  byte-for-byte. Mutations: a key for every task failed
  `test_task_without_service_key_writes_no_registry_rows` (a 99.99 s refresh
  deadline appeared); refreshing a stopping task failed
  `test_stopping_service_task_stops_refreshing_its_owner_row` (the row was
  re-stamped); restored byte-for-byte.
- Duplicates: no new code; the existing
  `test_task_monitor_duplicate_live_candidates_get_kill_signal` and
  `test_liveness_monitor_duplicate_live_candidates_converge` pass: with both
  owners' rows fresh, the non-canonical one gets KILL.
- Gates: manager, manager-services, convergence, service-task, heartbeat,
  task-monitor, liveness-monitor and task-execution files plus `tests/specs`
  on SQLite `-n 4`: 1,154 passed, 5 skipped; the same task and manager files
  on PostgreSQL (`./bin/pytest-pg`): 982 passed, 15 skipped; ruff, ruff
  format, repo mypy (499 files), `bin/ruff_suppression_index.py --check`,
  `bin/check-doc-paths`: clean.

Runtime probes on `9a408b78` (refresh design), 2026-11-09. Each probe stops
its context in `finally`; a read-only hook logged every owner-row refresh,
every Manager service PONG probe and service spawn request, every idle-clock
write, and the Manager's service state every 5 s:

- Clean handoff, retention 30 s and refresh interval 10 s (the hook shortened
  both, keeping their ratio), Manager 2 idle timeout 150 s: each service
  re-stamped its row every 10.0 s; Manager 2 sent no PONG probe, enqueued
  no service request, never wrote its idle clock after construction, kept
  `uncertain_attempts` at 0 and one owner TID per service (Manager 1's), had
  no children, and exited at 150.1 s. The context stop found exactly the
  three services Manager 1 launched.
- Clean handoff, natural retention (300 s, refresh 100 s), Manager 2 idle
  timeout 600 s: refreshes every 100.0 s; the same zero counts and
  `uncertain_attempts` 0 through 596 s; Manager 2 exited at 600.4 s; the
  stop found Manager 1's three services.
- Autostart crash adoption, natural retention: Manager 1 launched an
  `ensure` autostart task (`/bin/sleep 900`) and was killed with SIGKILL
  0.5 s later; the task re-stamped its row at about 100, 200 and 300 s.
  Manager 2 started 320 s after the kill (321 s after the launch) and saw a
  20.1 s-old row; 40 s later the only owner was still the original, one
  task process ran, no spawn request was queued, and Manager 2 sent no
  PONG probe. It enqueued only a heartbeat: Manager 1's heartbeat, with no
  work and no live Manager, had idle-exited at about 300 s, as designed.
- A first autostart run used an invalid command (`process_target` with an
  argument inside it); every launch failed at once and the `ensure` policy
  relaunched it about every 0.4 s, so it tested nothing and is discarded.

Cross-family review of `9a408b78`, 2026-11-09: Tier 1, run by the
coordinating agent over the theory, agent-context guidance, specs, plan and
the 12-file delta (read-only inspection and in-memory probes). Blockers F1
and F2; F3 and one test-coverage note.

| ID | Severity | Location | Finding | Disposition |
|----|----------|----------|---------|-------------|
| F1 | P2 | §4b; `service_convergence.py` retention; the removed aged-row PING path | Services already running baseline code never refresh; 300 s after their last row a new Manager discards it: a fresh successor gets `start_now` (a duplicate) and an existing adopter `wait_uncertain`. The rollout section and register omitted the loss. | Accepted, fixed as a documented clean break with an operator-controlled transition (owner's standing rule: no compatibility code, break cleanly, note it in the CHANGELOG). Upgrade step `weft system stop` per context in §4b, the CHANGELOG, the README and spec 03 [MA-1] item 7; register row 10 (`D` during mixed-version operation only, [WT-4.4]). No second proof mechanism. No separate mixed-version test (§4b says why). |
| F2 | P3 | Register row 8; the service-candidate PING fallback in `manager.py` | A fresh owner row with no host-checkable proof still reaches `_service_pong_candidate`, so the Manager still writes service-owner proof PINGs; row 8's claim was false. | Decided by the owner on 2026-11-09: the self-refreshed row is the proof and the fallback PING is removed. Handled in the commit after this one. |
| F3 | P3 | `BaseTask._reactor_support_routes`; §4a | The `services_registry` support route rejects a managed Consumer whose outbox is `weft.state.services`, which baseline built; unlisted. | Accepted: the collision guard stays ([QUEUE.7] requires every fixed support route to be declared); register row 11 (`D`, [WT-4.2], [WT-4.3]); regression test `test_managed_task_cannot_use_the_service_registry_as_a_queue_role` (managed rejected, ordinary built). Spec 07 [QUEUE.7] names the route. |
| note | — | `tests/tasks/test_service_task.py` | The stopping test did not exercise the terminal-status guard on its own. | Accepted: the test is parametrized with a terminal, not-stopping case. Mutations: removing the terminal-status guard fails only the terminal case; dropping the `services_registry` route fails the managed F3 case; each restored byte-for-byte. |

Gates after the F1 and F3 fixes: the eight owning files plus `tests/specs`
on SQLite `-n 4`: 1,157 passed, 5 skipped; the eight owning files on
PostgreSQL (`./bin/pytest-pg`): 985 passed, 15 skipped; ruff, ruff format,
repo mypy (499 files), `bin/ruff_suppression_index.py --check`,
`bin/check-doc-paths`: clean.

## 9. Out of Scope

- An adopted service that dies, and so stops refreshing, is not replaced
  by its adopter once its row ages out (no candidate, as before).
  Pre-existing; owner question 2 in §10.
- The unlinked-database leak itself (deleting a live context directory) is
  out of contract ([CTX.5], [WT-2a.11]); recorded as a lesson in a separate
  documentation commit.

## 10. Open owner questions

1. **Closed by the C2 revision (register row 6).** With refresh, a live
   owner's latest row is never older than about 100 s, so
   `plan_service_owner_history_prune` (which protects a non-expired latest
   live row) can never delete it, and the refresh itself deletes the owner's
   superseded rows. The original question: should TaskMonitor's runtime prune
   keep the latest `active` row of a service whose host process is live? Today an adopter loses its only row
   after one hour. Confirmed by the 2026-11-09 review, which reproduced both
   reducer outcomes: once `plan_service_owner_history_prune` removes an aged
   owner row (expired rows lose protection whatever their status; runtime
   pruning adds the 3,600 s minimum age), a surviving adopter keeps
   `active_tid` and stays in `wait_uncertain`, then `degraded_wait`,
   indefinitely, and a fresh Manager with empty state can `start_now` a
   duplicate. Both predate this plan; with slice 1 an otherwise idle adopter
   still exits, so the uncertainty no longer leaks a Manager.
2. Should an aged row whose scoped host process is proven dead count as
   `terminal` (as an in-window row does), so an adopter relaunches a dead
   adopted service? With refresh, a row ages only after its owner stopped
   refreshing; this plan keeps today's "no candidate".
3. Theory wording: [ALT-WT-004] says "TaskMonitor's only clock is its cycle
   deadline". The owner-row refresh adds a base-class clock to every managed
   service, TaskMonitor included. Should the theory say "only work clock"?

## 11. Fresh-Eyes Review

Done after drafting. Findings applied: site E needed the internal-inbox
row check because drains launch one child at a time, so a second service
request waits pending; site C is redundant with B and is deleted rather
than conditioned; the aged path must gate on host-pid death to avoid
PINGs piling up on a dead task's `ctrl_in`.

### Round 2 verification (coordinator)

Tier 1, Codex (`codex exec -s read-only`, reasoning effort high), 2026-11-09,
scoped to F1/F2 over `9f7e0207..1fc4cabc`. Verdict: PASS, no new defect. The
aged-row block keeps a probe key only when the helper returns a candidate, so
the existing sweep retires a dead owner's pending probe; an in-memory run of
the repository methods kept the probe on `9f7e0207` and removed it on
`1fc4cabc`. Register row 4 matches the live reconciliation paths. Full pytest
was not rerun by the reviewer. Review loop closed.

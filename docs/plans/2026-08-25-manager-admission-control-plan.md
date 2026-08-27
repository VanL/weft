# Manager Admission Control Plan

Status: draft
Source specs: docs/specifications/01-Core_Components.md [CC-2.4]; docs/specifications/03-Manager_Architecture.md [MA-1], [MA-1.1], [MA-1.4], [MA-1.6a], [MA-1.8]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]; docs/specifications/05-Message_Flow_and_State.md [MF-5], [MF-6]; docs/specifications/07-System_Invariants.md [OBS.13.7], [MANAGER.9]-[MANAGER.18]
Superseded by: none

Class: 5. This changes the Manager execution path, scheduling behavior, config,
backend observation, and failure handling. Spec promotion, real-backend
acceptance, rollback instructions, and independent review are required.

Plan type: implementation with spec revision.

Review state: implementation review passed on 2026-08-26, but the plan remains
draft because the corrected slice is uncommitted and the owner did not request
a commit. Reviews covered correctness, unnecessary process, non-causal tests,
and overengineering. Three implementation/test findings and two plan-evidence
findings were corrected; a final wording-only evidence finding was also
corrected, and the last pass returned PASS. No code, spec, or test issue remains
open.

## Goal

Delay child launch before reservation when the active backend's observed usage
cannot admit the source lane. Denied work stays in its existing spawn queue and
advances when usage falls below the lane limit.

Admission uses one soft, backend-specific usage observation per decision:

- SQLite counts the union of latest mappings that the shared payload-only probe
  reports live or undecidable, this Manager's in-flight child launches, and
  this Manager's committed child processes, each full TID once. A task-owned
  terminal liveness bit on the latest mapping makes completed external runners
  probe dead without consulting retained task-log history. Local launch/process
  evidence remains counted until the Manager clears it. This is a best-effort
  live-TID proxy, not a count of SQLite handles or an atomic task permit.
- PostgreSQL uses the raw server-wide `numbackends` value returned by
  `simplebroker_pg.get_connection_stats()` through the Manager's existing
  persistent Queue.

There is no second local child-count guard and no per-launch connection charge;
the Manager's active-launch and committed-child TIDs enter the single SQLite
usage set as evidence, not as a separate gate. One reserve derived from the
configured maximum pauses the public lane before the internal lane. The reserve
has a three-slot floor that models room in the internal lane for Manager,
TaskMonitor, and Heartbeat. It is not three dedicated permits and does not
attribute a slot to a service.

## Scope Challenge

### Smallest design that solves the stated problem

The Manager already owns both spawn lanes, strict internal priority,
single-flight launches, reactor deadlines, PING/STATUS, and rate-limited logs.
Admission belongs at its last pre-reservation seam. Reusing those structures
requires only:

1. two config values;
2. one named three-service reserve-floor constant;
3. one backend-selected usage observer;
4. pure reserve and lane-limit arithmetic;
5. blocked-lane state plus one retry deadline;
6. one additive task-owned terminal liveness hint on the mapping row SQLite
   already scans.

The plan does not add a semaphore queue, permit row, recovery protocol,
capacity-provider abstraction, pipeline weight, live config reload, direct SQL
in Weft, or a second Manager-local task count.

### Why the bound remains soft

Neither observer acquires capacity. SQLite reduces retained runtime evidence
that may lag process changes or omit evidence. PostgreSQL returns a server-wide
snapshot that includes the probing connection and unrelated clients. Another
task or connection can appear after either observation. The reserve provides
headroom; it does not turn observation into a lease.

The three-slot floor is deliberately conservative. A Manager is commonly
already present in the observed usage value, but the floor still models room in
the internal lane for the three known service roles rather than trying to infer
which ones are represented in a racing snapshot. It creates no per-service
permit or ownership right.

### Leadership and pipelines stay separate

Registry leadership remains advisory until a Manager can prove another live,
lower-TID Manager is primary. Once it has that proof, the non-primary Manager
must not reserve new public or internal work. It may finish work it already
owns: a reserved spawn row, an in-flight launch, or a locally tracked child.
An unreserved row in the global internal inbox is not owned work, even if this
Manager enqueued it. This distinction lets duplicate Managers converge and
also frees the non-primary Manager's own backend usage slot.

A pipeline remains ordinary work. Its top-level task, stages, and edges enter
the existing public or internal spawn lanes. Admission adds no topology
inspection, eager-child estimate, temporary reserve, weight, exemption, or
production branch. Natural task turnover permits progress unless persistent
work saturates the internal limit.

## Source Documents and Released Prerequisite

- `docs/specifications/03-Manager_Architecture.md` [MA-1], [MA-1.1],
  [MA-1.4], [MA-1.6a], and [MA-1.8]: dispatch, leadership, internal priority,
  control, and the shared launch path.
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4]: backend
  selection, persistent Queue reuse, and PostgreSQL connection observation.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-6]: public/internal
  spawn flow and pre-reservation ownership.
- `docs/specifications/07-System_Invariants.md` [MANAGER.9]-[MANAGER.18]:
  reservation authority, drain fences, leadership liveness, internal priority,
  and queue-activity ownership.
- `docs/specifications/01-Core_Components.md` [CC-2.4],
  `docs/specifications/05-Message_Flow_and_State.md` [MF-5], and
  `docs/specifications/07-System_Invariants.md` [OBS.13.7]: task-owned mapping
  publication and the shared payload-only mapping-liveness policy.
- `docs/agent-context/runbooks/adversarial-acceptance-probes.md` and
  `hardening-plans.md`: config, execution-path, failure, and rollback gates.
- `docs/plans/2026-08-26-simplebroker-7-5-1-compatibility-plan.md`: completed
  adoption of `simplebroker` 7.5.1 and `simplebroker-pg` 3.10.0.
- SimpleBroker `docs/specs/16-python-library-api.md` [SB-API-13]: exact
  `get_connection_stats()` result and lifecycle contract.

The released PostgreSQL helper has this public shape:

```python
from simplebroker_pg import get_connection_stats

stats: dict[str, int] = get_connection_stats(queue)
```

It always returns exactly these named keys:

- `numbackends`;
- `max_connections`;
- `superuser_reserved_connections`;
- `reserved_connections`.

Weft reads `numbackends` by key. It does not depend on field order and does not
derive its configured limit from the other server settings. `numbackends` is
the unfiltered server-wide sum. It includes the connection executing the probe
and may include database-attached workers that do not consume an ordinary
client slot. Weft uses it without subtraction or attribution.

The helper runs through the supplied Queue. Pass the Manager's existing
target-resolved persistent Queue and do not open a measurement-only
connection. SQL stays in `simplebroker_pg._sql` with the extension's other SQL.

## Resolved SQLite Live Usage Count

The SQLite path keeps one durable liveness hint on the mapping payload rather
than joining two independently retained histories:

1. `BaseTask._build_tid_mapping_payload()` includes a boolean task-owner
   `terminal` hint derived from `TERMINAL_TASK_STATUSES`;
2. `BaseTask._report_state_change()` forces one best-effort mapping publication
   when the task first reports a terminal transition, even when activity was
   already empty. `_tid_mapping_equivalent()` includes `terminal`, so the
   terminal row is not suppressed as equivalent to the prior live row;
3. `mapping_row_is_live()` first preserves a row with positive scoped host-
   process liveness. If no scoped host process is live, it returns false for a
   valid latest mapping with `terminal: true`; otherwise it applies the existing
   conservative runtime-handle policy. The hint is a tie-breaker for dead or
   unprobeable evidence, never an override of a live `(pid, create_time)`. This
   remains one payload-only liveness rule and lets existing mapping cleanup
   retire the newest terminal row after its age gate and wrapper exit;
4. `latest_tid_mapping_entries_for_endpoint_resolution(ctx, strict=True)`
   reduces `weft.state.tid_mappings` to the latest payload per full TID;
5. each latest mapping is kept when the shared
   `weft/core/monitor/policies/tid_mapping.py::mapping_row_is_live` probe
   reports live or undecidable;
6. the surviving mapped TIDs are unioned with the keys of
   `_active_child_launches` and `_child_processes`, then counted once.

The formula is:

```text
used_tids = live_or_undecidable_latest_mapping_tids
          | active_child_launch_tids
          | committed_child_process_tids
used = len(used_tids)
```

The terminal hint survives raw task-log collation/deletion and Manager restart
because it lives on the latest mapping row that admission already scans. It is
not public lifecycle truth and is never used to reconstruct task status or
result. If its best-effort publication fails, the previous undecidable mapping
remains counted; this is the accepted SQLite fail-conservative behavior.

Manager-local launch/process evidence re-adds local work until the existing
launch-failure or child-reap path clears it. This closes the post-launch/pre-
mapping gap without adding host PIDs to a runner-controlled handle or changing
runner control authority. A just-exited local child may remain counted until
the next ordinary reap; it cannot create an overshoot.

Probe verdicts are memoized per TID in Manager-local state
(`_admission_probe_memo`): the fingerprint includes both `runtime_handle` and
normalized `terminal`, because either changes liveness. A dead verdict is
permanent while that fingerprint is unchanged, because a probed
`(pid, create_time)` identity never becomes live again; a live verdict expires after
`MANAGER_ADMISSION_RECHECK_SECONDS` so death is observed promptly. Memo
entries for TIDs no longer present in the reduction are pruned each
observation, bounding memory to current mapping rows.

The accepted v1 cost remains one full TID-mapping scan per admission decision
(`O(mapping rows)` time and `O(unique TIDs)` memory), plus one shared-probe call
per expired or new memo entry. Admission is opt-in. Do not add a task-log scan,
Monitor-store lookup, durable index, or another cleanup lifecycle.

Admission needs strict failure signaling even though endpoint resolution is
best effort. Add one keyword-only `strict: bool = False` option through the
existing `iter_queue_entries()` -> `iter_queue_json_entries()` -> endpoint
reducer path. Existing callers retain best-effort empty-on-generator-open
behavior. Admission passes `strict=True` to the mapping reduction, and wraps
the scan, filtering, and memo updates in the existing SQLite ordinary-exception
boundary. A generator-open failure, iteration failure, or liveness/filtering
failure therefore returns `None` and fails closed.

## What Already Exists and Must Be Reused

- `Manager._process_queue_message()` is the last Manager-owned point before
  `BaseTask` reserves a source row.
- `_drain_internal_spawn_requests()` and `_drain_public_spawn_requests()`
  preserve strict internal priority.
- `next_wait_timeout()`, `_active_queues`,
  `_queue_counts_as_wait_activity()`, and `MultiQueueWatcher` own bounded wakeup
  and native/fallback wait behavior.
- Manager child reap and child-launch worker completion already provide earlier
  wakeups after local progress.
- `_emit_serve_log_rate_limited()` owns non-normative Manager operational logs.
- `BaseTask._get_connected_queue()` returns the Manager's cached persistent
  Queue for the PostgreSQL helper.
- `weft/core/endpoints.py` owns the latest-TID-mapping reduction for SQLite
  admission.
- `TERMINAL_TASK_STATUSES` is the existing forward-only lifecycle boundary;
  do not create a second terminal-status set.
- `BaseTask._build_tid_mapping_payload()`, `_tid_mapping_equivalent()`, and
  `_report_state_change()` own task mapping shape, change detection, and
  terminal publication; extend that path instead of creating a Manager-owned
  terminal store.
- `_active_child_launches` and `_child_processes` already delimit the local
  launch handoff and reap lifecycle; use their keys as observation evidence.

Before editing, the implementer must be able to answer:

1. Which payload field makes a completed external runner's latest mapping probe
   dead after raw task-log retirement, and which local evidence keeps a
   launched child counted before or after mapping publication?
2. Why is an unreserved row in `weft.spawn.internal` not owned by the Manager
   that happened to enqueue it?
3. Why must Manager fallback pending-work detection suppress only a blocked
   spawn source, not its reserved recovery queue?

## Proposed Behavior

### One pre-reservation gate

```text
weft.spawn.internal -+
                     +-> observe backend usage -> lane limit -> reserve/launch
weft.spawn.requests -+
```

There is one usage value and one configured maximum. Backend selection changes
only how `used` is observed:

| Backend | `used` |
|---|---|
| SQLite | Count of live-or-undecidable latest mappings (terminal payload hints probe dead), unioned with this Manager's active launches and committed children, per full TID |
| PostgreSQL | Server-wide `get_connection_stats(existing_queue)["numbackends"]` |

The Manager keeps four internal decision states for gating and transition logs:

- `open`: both lanes may reserve;
- `public_paused`: public stays in source; internal may reserve;
- `all_paused`: both lanes stay in source;
- `unavailable`: the backend usage observation failed; both lanes stay in
  source until retry.

### Config surface

Add only these settings:

| Key | Meaning |
|---|---|
| `WEFT_ADMISSION_MAX_CONNECTIONS` | Admission maximum. Unset or `0` disables admission. An enabled value is a positive integer. |
| `WEFT_ADMISSION_RESERVE_FRACTION` | Fraction withheld from the public lane, subject to the three-service floor. It must be finite and satisfy `0 <= value < 1`; default `0.1`. It has no effect while admission is disabled. |

The maximum is operator-supplied on both backends. PostgreSQL does not replace
it with the server's `max_connections`; SQLite compares it with the best-effort
live-TID union above. Configuration is snapshotted when the Manager starts;
changes require restart.

### Arithmetic

Use one named product and implementation constant:

```text
ADMISSION_SERVICE_RESERVE_SLOTS = 3
```

The three slots correspond to Manager, TaskMonitor, and Heartbeat. For
configured maximum `max_connections` and reserve fraction `f`:

```text
reserve = max(
    ceil(max_connections * f),
    ADMISSION_SERVICE_RESERVE_SLOTS,
)
public_limit = max(0, max_connections - reserve)
internal_limit = max_connections

public admits iff used < public_limit
internal admits iff used < internal_limit
```

This is the complete arithmetic. There is no prospective launch-cost term, no
second fixed continuation or launch constant, no subtraction of PostgreSQL
server-reserved settings, and no conjunction with a Manager-local child count.

`f = 0` still preserves the three-service floor. When
`max_connections <= reserve`, `public_limit` is clamped to `0`; public admission
is disabled because observed usage cannot be negative, while internal work
remains usable whenever `used < internal_limit`. This is valid boundary
behavior, not a config error.

### Backend observation

SQLite calls the existing latest-mapping reducer in strict mode, keeps rows the
shared `mapping_row_is_live` probe reports live or undecidable (positive scoped
host-process liveness wins; otherwise a terminal payload hint reports dead;
other dead verdicts are permanent per unchanged liveness fingerprint; live
expires on the recheck interval), unions this Manager's in-flight launches and
committed children, and returns the size of that TID set. The
result is context-scoped and best effort: it is an observed live-TID count, not
an exact SQLite connection count, durable permit set, or ownership claim.

PostgreSQL calls
`get_connection_stats(self._get_connected_queue())["numbackends"]` only on the
effective PostgreSQL path and on the Manager reactor thread. Import from the
`simplebroker_pg` package root. Do not import private SQL, parse raw rows, add
psycopg, call `open_broker()`, or open an ephemeral Queue.

Each successful observation is valid for one launch decision. A later decision
re-observes. Observation and launch are non-atomic on both backends.

The exception boundary is exact. PostgreSQL observation fails closed only on
`DatabaseError` or `ValueError`. SQLite observation fails closed only on
`BrokerError`, `OSError`, or `RuntimeError`, across generator open, history
iteration/reduction, terminal filtering, runtime-handle probing, and memo
maintenance. These are ordinary `Exception` subclasses. Do not catch
`BaseException`, match exception strings, or suppress shutdown/control flow.
Failure leaves the source row unreserved and schedules the same one-second
retry as a capacity denial.

### Blocked wait behavior

Denial or observation failure must not reserve the row and must not spin on a
known non-empty source:

1. record the lane decision and latest observation evidence in Manager-local
   state;
2. leave the message in its existing source queue;
3. keep the source in `_active_queues`, but exclude it from ordinary wait
   activity while that lane remains blocked;
4. set one `MANAGER_ADMISSION_RECHECK_SECONDS = 1.0` deadline through
   `next_wait_timeout()` for a denied or unavailable observation;
5. retry the retained source when the deadline expires;
6. clear the block earlier when existing child-reap or child-launch worker
   progress can have changed observed usage, while retaining the universal
   deadline as recovery if that progress path fails or produces no queue wake.

An admitted request whose child launch fails must not leave its restored or
retained source suppressed indefinitely. The universal deadline must clear
stale blocked state and re-observe without requiring unrelated queue activity.

The existing active-queue update may remove the source if another Manager
consumed its row. A due local timer must not probe a truly inactive queue.
Control, cleanup, leadership convergence, service reconciliation, and shutdown
remain live while launch lanes are paused.

Both native and fallback waiting must apply the same lane suppression. Keep
reserved queues eligible in `Manager._has_pending_messages()` because they own
recovery work; skip only public/internal source configs whose admission lane is
currently blocked. Do not change generic `MultiQueueWatcher` behavior.

### Duplicate-manager convergence

A proven non-primary Manager must yield instead of treating shared, unreserved
internal backlog as work it owns. In `_has_actionable_leadership_work()`, keep
only local ownership evidence: persistent children, active child launches,
reserved spawn rows, and actionable Manager control. Remove
`_managed_internal_spawn_enqueued` and `_internal_spawn_pending()` from this
yield veto. Those states prove shared backlog, not ownership. The primary
Manager can reserve that row after the duplicate exits and frees its observed
backend slot.

Do not add a new leadership flag or global lock. Do not make the admission gate
elect a primary; it only relies on the existing canonical lower-TID proof.

### Minimal operational evidence

Admission adds no PING/STATUS field, public status schema, CLI rendering, or
control-path observation. Existing Manager control responses remain unchanged.

Emit only existing rate-limited structured logs when the internal admission
state changes or observation fails. These logs are non-normative operational
evidence, not lifecycle truth or a stable API. They may include backend, lane,
`used` when known, reserve, and the two limits. Never log SQL, credentials,
TaskSpecs, raw exception text, or a full traceback. Keep the retry deadline and
cached decision state private.

## Invariants, Correctness Boundaries, and Failure Behavior

- Admission runs before reservation. A denied source row remains unowned and
  the Manager reserved queue is unchanged.
- Already-reserved rows keep current inspection, acknowledgement, policy,
  leadership-requeue, cleanup, and drain behavior.
- Internal priority is unchanged. Admission changes eligibility, not order.
- SQLite and PostgreSQL use one selected observation, never a conjunction.
- SQLite counts latest mappings when the shared payload-only probe reports them
  live or undecidable, unioned with this Manager's in-flight launches and
  committed children. Positive scoped host-process liveness always keeps the
  mapping counted. Otherwise a task-owned `terminal: true` mapping or dead
  host-process evidence releases it; undecidable non-terminal rows remain
  counted.
- SQLite admission owns no liveness policy beyond the shared
  `mapping_row_is_live` probe; the per-TID verdict memo is Manager-local,
  pruned to current rows, and adds no durable/shared cache, index, cleanup
  lifecycle, or freshness authority.
- PostgreSQL uses raw `numbackends`; do not subtract the probe connection or
  filter by role, database, backend type, context, or application name.
- The three-slot floor always applies, even when the fraction would reserve
  fewer slots or the Manager is already represented in `used`.
- Observation overcount can pause early. Missing or racing evidence can still
  overshoot. The guard is deliberately best effort and non-atomic.
- Observation failure fails closed only when admission is enabled. Disabled
  admission preserves current dispatch.
- A proven non-primary Manager does not reserve shared spawn backlog. Existing
  reservations, active launches, and locally tracked children remain under
  their current ownership and drain rules.
- Persistent internal work may block the lane forever. Admission logs
  transitions but does not evict, preempt, or raise a pipeline-specific error.
- No task state, TID, queue name, reserved policy, result delivery, process
  title, or TaskSpec shape changes.
- Existing non-admission history callers retain best-effort generator-open
  behavior; strict failure signaling is opt-in at the admission call site.
- The change has no one-way door: `terminal` is an additive runtime mapping
  field that older readers ignore. It adds no queue, table, migration, or
  incompatible payload. Disabled admission and rollback preserve the current
  durable spine.

## Files Expected to Change

The released prerequisite is already complete in SimpleBroker and in Weft's
dependency adoption slice. This plan does not modify `../simplebroker`.

Expected Weft changes:

- `weft/_constants.py`;
- `weft/helpers/__init__.py`;
- `weft/core/endpoints.py`;
- `weft/core/tasks/base.py`;
- `weft/core/monitor/policies/tid_mapping.py`;
- `weft/core/monitor/task_monitor.py` (documentation only);
- `weft/core/manager.py`;
- `tests/system/test_constants.py`;
- `tests/system/test_helpers.py`;
- `tests/tasks/test_task_observability.py`;
- `tests/core/monitor/policies/test_tid_mapping.py`;
- `tests/core/test_manager.py`;
- the source specs listed above plus
  `docs/specifications/00-Quick_Reference.md`;
- `README.md`, `CHANGELOG.md`, this plan, and `docs/plans/README.md`.

No new dependency, queue, table, CLI command, backend protocol, provider
registry, or pipeline production branch is expected.

## Spec Baseline and Proposed Delta

- Original plan baseline: repository commit
  `0f19367a5781ead326f632853b3c49bc07e9623d` plus the completed dependency
  adoption recorded in
  `docs/plans/2026-08-26-simplebroker-7-5-1-compatibility-plan.md`.
- Reviewed implementation baseline: `a659a481`. This is evidence of the
  historical incomplete baseline implementation, not a promotion or completion
  marker.
- Promotion strategy A: update the current contract sections below and retain
  reciprocal plan links before implementation resumes.
- Corrected spec-promotion baseline: working-tree blobs relative to
  `a659a481` are
  `00=d8eb7b1a1e25dda3d41d4bab48a9d34dce3f384e`,
  `01=4019f8b8e7feb34e3622f2a787b4d29b35b3582c`,
  `03=facbe66ffaf2255cbc8340df457d98e4650bf392`,
  `04=3d79dc95fe8dde2033d3b11fe828b525107284a4`,
  `05=d03c600707a77b9fe2d1cc728a7a61ea7d4eccfc`, and
  `07=becb734cd983136fdcca3e93220202dae125941e`. Runtime implementation
  proceeds against these promoted current-contract texts; [SB-0.4] already
  matched the corrected delta and therefore retains its baseline blob.

### `docs/specifications/01-Core_Components.md` [CC-2.4]

Specify:

> Every task-owned TID mapping carries a boolean `terminal` liveness hint.
> Non-terminal publication writes `false`; the first task-owned terminal state
> report forces a best-effort mapping publication with `true`, and mapping
> equivalence includes the field. The hint is operational runtime evidence for
> payload-only liveness and cleanup. It is not public lifecycle truth, result
> authority, or a replacement for the terminal task-log/control writes.

### `docs/specifications/03-Manager_Architecture.md` [MA-1.8]

Specify:

> Before reserving a public or internal spawn row, an admission-enabled Manager
> observes one backend-specific usage value. SQLite reduces TID-mapping history
> to the latest row per full TID, retains each row whose shared payload-only
> liveness probe reports live or undecidable, and unions the result with the
> Manager's in-flight child launches and committed child processes. A mapping
> with the task-owned terminal hint and no positive scoped host-process proof
> probes dead. Each full TID counts once.
> Local launch/process evidence remains counted until existing launch-failure
> or child-reap cleanup clears it. PostgreSQL reads raw
> `numbackends` through `simplebroker_pg.get_connection_stats()` on the
> Manager's existing persistent Queue.
>
> For configured maximum `N` and reserve fraction `f`, reserve is
> `max(ceil(N * f), 3)`, the public limit is
> `max(0, N - reserve)`, and the internal limit is `N`. Public admits exactly when `used < public_limit`; internal
> admits exactly when `used < internal_limit`. The three-slot floor reserves
> modeled internal-lane room for Manager, TaskMonitor, and Heartbeat; it is not
> a set of dedicated service permits. A denied or unavailable observation
> leaves the source row unreserved until bounded retry or an earlier progress
> wake.
>
> The observation is best effort and non-atomic. Pipelines receive no special
> accounting or branch. An enabled admission observer fails closed on its
> listed ordinary history, broker, and runtime-probe errors. A proven
> non-primary Manager does not reserve new work from either shared spawn inbox;
> shared unreserved internal backlog is not work that the non-primary owns.

Map implementation to `Manager._process_queue_message()`, its backend usage
observer, blocked-lane state, `next_wait_timeout()`, fallback pending-work
precheck, and duplicate-manager yield ownership check. SQLite reduction uses
`latest_tid_mapping_entries_for_endpoint_resolution()` in
`weft/core/endpoints.py`. Admission adds no `_control_snapshot_fields()` data.

### `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4]

Specify:

> A PostgreSQL-backed Manager calls the public package-root
> `simplebroker_pg.get_connection_stats(persistent_queue)` helper and reads raw
> `numbackends` by key. Weft passes its existing target-resolved persistent
> Queue, opens no measurement-only connection, and does not duplicate backend
> SQL. The server snapshot is conservative evidence, not a permit. The
> operator-configured admission maximum, not the returned server maximum,
> controls Weft's lane limits.

### `docs/specifications/05-Message_Flow_and_State.md` [MF-5], [MF-6]

Specify:

> On the task-owned terminal reporting path, terminal TID-mapping publication
> is attempted independently of terminal task-log/control publication. Raw
> task-log collation or deletion cannot resurrect a terminal mapping as live;
> the liveness hint remains on the latest mapping until normal runtime-state
> retention removes it. A mapping write failure is best effort and leaves the
> prior row conservatively live or undecidable.
>
> Admission precedes spawn-row reservation. A denied public or internal row
> remains in its source queue. The Manager suppresses the known-blocked source
> as ordinary wait activity in both native and fallback wait paths,
> re-evaluates it on a one-second deadline, and may wake earlier after child or
> launch-worker progress. Reserved rows remain eligible recovery work. No task
> lifecycle state or reserved-row policy is added.

### `docs/specifications/07-System_Invariants.md` [OBS.13.7], [MANAGER.18]

Specify:

> The payload-only TID-mapping liveness policy first preserves a row whose
> scoped `(pid, create_time)` evidence proves a live host process. With no
> positive scoped host-process proof, a valid latest row with `terminal: true`
> returns dead; missing or malformed terminal hints preserve the existing
> conservative runtime-handle behavior. The hint is subordinate to positive
> host liveness, changes no runner control authority, and introduces no task-
> log or Monitor-store lookup into cleanup.
>
> Admission is a lane-specific pre-reservation soft guard over one
> backend-selected usage observation. Public work cannot consume the greater
> of the fractional reserve and the three-service floor; internal work can use
> the configured maximum. SQLite latest-mapping reduction and PostgreSQL
> `numbackends` observations are best effort and non-atomic.
> Manager-local probe memoization is allowed, but admission owns no durable or
> shared cache, index, permit, or cleanup lifecycle. Pipelines receive no
> special weight, exemption, or branch. A blocked lane cannot block Manager
> control, cleanup, child reap, duplicate-manager convergence, service
> reconciliation, or shutdown.

### `docs/specifications/00-Quick_Reference.md`

Add the two config rows exactly as defined in **Config surface**. Replace the
SQLite summary with the set formula from **Resolved SQLite Live Usage Count**,
including the terminal mapping hint and both manager-local launch states. Link
to [CC-2.4], [MA-1.8], [OBS.13.7], and [MANAGER.18]. Add `terminal` to the
runtime TID-mapping payload summary as an additive task-owned liveness hint.

## Implementation Tasks

1. **Promote the corrected contract before changing code.**
   - Files: the five source specs listed above, this plan, and spec backlinks.
   - Replace the old SQLite mapping-only wording, native-wait-only wording,
     and ambiguous no-cache wording with the exact delta above.
   - Record a new promotion baseline identifier. Do not cite this plan as the
     governing runtime contract after promotion.
   - Stop if the spec edit starts defining a new runner-handle authority,
     lifecycle status, queue, or leadership mechanism.
   - Done when plan metadata/spec hygiene and traceability checks pass.

2. **Publish terminal mapping liveness and close SQLite observation gaps.**
   - Files: `weft/core/tasks/base.py`,
     `weft/core/monitor/policies/tid_mapping.py`,
     `weft/helpers/__init__.py`, `weft/core/endpoints.py`,
     `weft/core/manager.py`, and their existing focused tests.
   - Add `terminal: bool` to the mapping payload, include it in mapping
     equivalence, and force one mapping registration from the existing
     task-owned terminal report path. Keep the write best effort and separate
     from terminal lifecycle/control publication.
   - In `mapping_row_is_live()`, keep positive scoped host-process liveness as
     the first authority. If no scoped process is live, return false for
     `terminal is True`; missing, false, or malformed hints retain the current
     conservative payload-only behavior. Do not look up task logs or Monitor
     tables from the cleanup policy.
   - Update the existing liveness-policy module text in
     `weft/core/monitor/policies/tid_mapping.py` and the destruction-protection
     explanation in `weft/core/monitor/task_monitor.py` so both state the same
     ordering: positive scoped host-process liveness wins; otherwise a valid
     terminal hint makes the row dead; non-terminal unprobeable rows remain
     undecidable/protected. This is documentation synchronization, not another
     runtime branch or test requirement.
   - Add keyword-only `strict: bool = False` through the existing queue-history
     iterator and latest-mapping reducer. `strict=False` preserves current
     endpoint/pruning best-effort behavior; `strict=True` re-raises the existing
     generator-open exception types.
   - In `_observe_admission_usage()`, request the mapping reduction with
     `strict=True`; compute the exact set formula in **Resolved SQLite Live
     Usage Count**; extend the current per-TID probe memo fingerprint with
     normalized `terminal`; wrap the full SQLite reduction/filter/memo path in
     the existing exact ordinary-exception catch.
   - Reuse `TERMINAL_TASK_STATUSES`, `mapping_row_is_live`,
     `_active_child_launches`, and `_child_processes`. Do not import
     `weft.commands`, duplicate the reducer, add a task-log join, or add a
     cache/index/provider class.
   - Red first on: forced terminal mapping publication; terminal payload
     liveness after raw-log absence and Manager reconstruction; terminal plus a
     live scoped host process at `min_age=0`; same-Manager false-to-true memo
     invalidation; committed child before mapping publication; nested history-
     open failure; strict-default compatibility; and filtering failure.
   - Stop if correctness starts depending on field order, a runner handle is
     reinterpreted as a host PID, or a third liveness policy appears.

3. **Close duplicate-manager and fallback-wait liveness gaps.**
   - Files: `weft/core/manager.py` and `tests/core/test_manager.py`.
   - In `_has_actionable_leadership_work()`, remove shared unreserved internal
     backlog (`_managed_internal_spawn_enqueued` and
     `_internal_spawn_pending()`) as a veto after another primary is proven.
     Preserve existing owned-work checks and drain behavior.
   - In Manager's `_has_pending_messages()` fallback precheck, skip only spawn
     source configs whose admission lane is blocked. Keep reserved queues and
     Manager control actionable. Reuse `_admission_lane_for_queue()` and the
     existing blocked-lane set; no generic watcher change is needed.
   - Preserve the universal one-second retry and failed-launch retry repair
     already present in baseline `a659a481`.
   - Red first on the cap-filled duplicate-manager case and the native-waiter-
     unavailable fallback case.
   - Stop if the change adds a global lock, a second election path, or suppresses
     reserved recovery work.

4. **Reconcile tests, docs, and completed-work evidence.**
   - Files: `tests/core/test_manager.py`, existing helper/endpoint tests,
     `README.md`, `CHANGELOG.md`, this plan, and `docs/plans/README.md` only
     when status changes.
   - Keep arithmetic, lane, deadline, launch-failure, and real-PostgreSQL tests
     that already prove outcomes. Add only the regressions named below.
   - Remove `test_linear_pipeline_larger_than_admission_cap_completes()` as
     admission evidence. It proves only ordinary pipeline completion, already
     owned by the pipeline lifecycle suite. Do not replace it with a production
     hook or timing-sensitive assertion.
   - Run focused, full, type, lint, spec, and real-PostgreSQL gates. Then run an
     independent completed-work review focused on correctness, unnecessary
     process, tests that do not correlate with outcomes, and overengineering.
   - Do not mark this plan completed until that review passes and all new
     implementation/deviation evidence is recorded.

## Outcome-Based Test Plan

```text
SQLite observation
  task terminal report -> latest mapping terminal=true
  latest TID mappings (strict) --failure--> unavailable / source retained
  latest mapping -> shared payload-only liveness probe
      scoped host process live ----------------------------> counted
      terminal=true + no live scoped host process --------> released
      dead ------------------------------------------------> released
      live/undecidable ------------------------------------> counted
  active launch -------------------------------------------> counted once
  committed local child ----------------------------------> counted once

Scheduler
  capacity available -> source reserved -> existing launch path
  lane full          -> source retained -> bounded retry
  native wait absent -> blocked source suppressed -> deadline wait
  launch fails       -> source restored -> bounded retry
  non-primary + shared internal row -> yield -> primary may reserve later
```

Required tests:

- Config and arithmetic: disabled maximum, minimum enabled maximum, invalid
  type/range, default and invalid reserve fraction, zero fraction with the
  three-slot floor, fractional ceiling above the floor, equality denial, and
  one-below admission for each lane. Cover `max_connections <= reserve` as a
  valid public-disabled/internal-usable boundary.
- SQLite observer: preserve existing latest-per-TID deduplication, dead-row
  release, undecidable-row conservatism, launch union, and memo tests. Add:
  - **CRITICAL regression:** a terminal transition forces a latest mapping with
    `terminal: true` even when activity was already empty;
  - **CRITICAL regression:** a terminal external-runner mapping with an
    undecidable runtime handle no longer consumes capacity after raw task-log
    rows are absent and a new Manager instance reconstructs observation;
  - **CRITICAL cleanup regression:** `terminal: true` plus a live scoped
    `(pid, create_time)` remains protected even at `min_age_seconds=0`;
  - **CRITICAL memo regression:** on the same Manager, a latest row changing
    only `terminal: false -> true` invalidates the cached live verdict and is
    re-probed immediately;
  - **CRITICAL regression:** a committed `_child_processes` TID counts before
    its mapping is published and is deduplicated after publication;
  - **CRITICAL regression:** an exception opening the nested mapping-history
    generator returns unavailable and leaves the source row unreserved;
  - compatibility pair: generator-open `BrokerError` yields an empty reduction
    by default and re-raises when `strict=True`;
  - one representative liveness/filter `RuntimeError` returns unavailable.
  Do not add an exception cross-product or duplicate upstream runtime-handle
  probe matrices.
- Observable Manager behavior: denied rows remain in source and reserved stays
  empty; public pauses at its limit; internal pauses at the maximum; a progress
  wake and the one-second deadline each resume retained work; blocked queues do
  not spin. The universal one-second deadline must re-observe and advance work
  without unrelated queue activity. A failed child launch must not leave a
  restored or retained source suppressed indefinitely. Existing control,
  cleanup, and leadership suites own unchanged behavior; add no admission
  PING/STATUS test.
- Duplicate Manager: with a lower-TID live primary, a higher-TID Manager, one
  shared unreserved internal row, and observed usage at the internal limit, the
  non-primary yields instead of treating the row as owned. After its usage slot
  is removed, the primary can reserve the retained row. Use real broker-backed
  queues and registry payloads; patch only the backend usage snapshot if needed
  to make the boundary deterministic.
- Fallback wait: disable/fail the native activity waiter, leave a blocked spawn
  source non-empty, and prove the fallback path reaches the admission deadline
  instead of returning immediately. Prefer a fake stop-event/deadline call
  assertion over a sub-millisecond wall-clock threshold. Also prove a pending
  reserved row still wakes recovery.
- Default and backend boundary: disabled admission preserves dispatch; SQLite
  never imports/calls the PostgreSQL helper; observer failure fails closed only
  when admission is enabled; the exact ordinary-exception catches fail closed,
  while a representative `BaseException` propagates.
- Service floor: the derived reserve is never below three, even when the
  fraction is zero or observed usage already contains a Manager. Prove it is
  lane arithmetic, not three service-specific permits.
- Pipeline: remove the admission-specific over-cap completion test. Existing
  generic pipeline lifecycle coverage proves the unchanged path; lane
  retention/retry and terminal-mapping regressions prove admission. Do not
  estimate topology, add a production hook, or add a timing assertion.
- Real PostgreSQL: one focused Manager integration test through
  `bin/pytest-pg` uses the actual package-root helper and proves a deliberately
  tight configured limit leaves work in source. Upstream tests own SQL,
  permissions, result validation, and connection lifecycle.

Prefer source/reserved queue contents and completed work over private-state
assertions. Transition logs may corroborate a test but are not contract
authority. Private state is acceptable only where it is the lifecycle seam
under test (`_child_processes`, active launch handoff, leadership ownership).
Do not repeat the full SimpleBroker extension or runtime-liveness suite in
Weft Manager tests.

## Failure Modes and Observable Outcomes

| Path | Realistic failure | Handling | Required proof | Operator/user signal |
|---|---|---|---|---|
| Terminal mapping publication | terminal state is reported while activity is already empty | terminal report path forces a changed mapping with `terminal: true` | task-observability publication regression | no direct user signal; admission later releases the TID |
| SQLite mapping reduction | generator fails while opening or iterating | exact ordinary exception returns unavailable; row stays in source; bounded retry | nested history-open regression | rate-limited observation-failure log; no task failure |
| SQLite external runner | undecidable runner handle outlives raw terminal logs | terminal hint on latest mapping probes dead across Manager restart when no scoped host process is live | terminal external-runner reconstruction regression | later queued work resumes |
| Mapping cleanup | terminal report precedes wrapper exit | positive scoped host-process proof overrides the terminal hint | zero-age live-process cleanup regression | no premature loss of runtime/control mapping |
| Admission memo | latest mapping changes only `terminal` | terminal participates in the liveness fingerprint | same-Manager false-to-true regression | capacity releases without waiting for TTL |
| Local child launch handoff | process exists before mapping publication | `_child_processes` keeps TID in union | committed-child/pre-mapping regression | no extra launch admitted at equality |
| Runtime-handle filtering | shared liveness probe raises `RuntimeError` | exact SQLite boundary returns unavailable | representative filtering-failure test | rate-limited observation-failure log |
| Duplicate Manager | shared internal row keeps non-primary alive at cap | non-primary yields unless it owns work | two-manager registry/broker regression | primary later reserves retained row |
| Fallback wait | blocked non-empty source makes `_has_pending_messages()` return immediately | suppress only blocked source; deadline remains | native-waiter-disabled regression | flat blocked CPU; bounded recheck |
| Reserved recovery | broad suppression hides a reserved row | reserved queues remain actionable | paired fallback regression | recovery advances without waiting for admission capacity |

No path above should fail a task merely because admission cannot observe
capacity. Fail-closed means retain and retry the spawn row, not emit a terminal
task state.

## Verification

```bash
. ./.envrc
uv lock --check
uv sync --frozen --all-extras
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py -q
./.venv/bin/python -m pytest tests/system/test_helpers.py tests/system/test_constants.py tests/tasks/test_task_observability.py tests/core/monitor/policies/test_tid_mapping.py tests/core/test_manager.py -k 'admission or strict or terminal_mapping or mapping_row_is_live' -q
PYTEST_ADDOPTS='-k admission -q' ./.venv/bin/python bin/pytest-pg tests/core/test_manager.py
./.venv/bin/python -m pytest
./.venv/bin/mypy weft bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
git diff --check
../backstitch/.venv/bin/backstitch check --repo-root . --no-config --spec-root docs/specifications --plan-root docs/plans --code-root weft --code-root tests --code-root bin --code-root integrations --code-root extensions --format json --output /tmp/weft-admission-backstitch-after.json
```

`bin/pytest-pg` provisions PostgreSQL and injects its DSN and backend settings
into the pytest child environment. It does not require or leave behind a
caller-supplied `WEFT_PG_TEST_DSN`.

## Rollout and Rollback

Admission is opt-in. Recommended first canary:

```text
WEFT_ADMISSION_MAX_CONNECTIONS=100
WEFT_ADMISSION_RESERVE_FRACTION=0.1
```

Choose the maximum from deployment evidence. On PostgreSQL it is an explicit
Weft budget and need not equal the server setting. On SQLite it bounds the
best-effort live-TID union rather than physical connections.

During canary, verify public pause, internal continuation, bounded retry,
control responsiveness, flat CPU in both native and fallback wait modes,
duplicate-manager convergence, and resume after terminal external-runner or
local child progress. Measure the one SQLite mapping-history scan under retained
history. Do not propose default-on behavior without measured canary evidence.

Rollback requires no data cleanup: stop or drain the Manager, unset
`WEFT_ADMISSION_MAX_CONNECTIONS` (or set it to `0`), and restart. The feature
adds no new store; additive terminal mapping hints are harmless to older code
and remain under existing runtime-state retention.

Mixed-version rollout is safe. Older mapping readers ignore the additive
`terminal` field; the revised payload-only probe treats rows without a valid
hint exactly as before. The publisher and probe may therefore roll forward or
back independently, although they should ship in one release so external-
runner release is not partially effective. Disabling admission remains the
immediate rollback and does not require deleting terminal mapping rows.

Stop rollout if the Manager hot-loops, duplicate Managers fail to converge,
terminal external runners remain charged, local launches overshoot through the
pre-mapping gap, control latency regresses, measured SQLite admission latency
or CPU cost is unacceptable, ordinary-role PostgreSQL queries fail, or the
configured reserve is routinely consumed under the canary.

## Work Order and Parallelization

Sequential implementation, no parallelization opportunity. The SQLite
observer, duplicate-manager yield decision, and fallback wait precheck all
touch `weft/core/manager.py` and share the same scheduler invariants. Promote
the spec first, then implement Tasks 2 and 3 in order, then reconcile tests and
docs. Use a read-only independent reviewer after this plan revision and after
the finished slice; parallel Manager edits would add merge and reasoning risk
without reducing the critical path.

## Out of Scope

- durable or distributed permits;
- task adoption after Manager restart;
- a new leadership fence;
- a second independent Manager-local gate, prospective launch charge, or
  service-specific permit; local launch/process TIDs are evidence in the one
  SQLite set only;
- pipeline-specific admission, weights, exemptions, topology inspection, or
  temporary reserve changes;
- prospective per-launch connection estimates or launch-cost constants;
- deriving limits from PostgreSQL `max_connections` or reserved settings;
- preemption, fairness classes, or persistent-task eviction;
- role/database/PgBouncer attribution;
- counting SQLite connection objects;
- raw PostgreSQL SQL or a generic backend-capacity protocol in Weft;
- new CLI commands, queues, tables, dependencies, or migrations;
- changing endpoint/pruning callers from their existing best-effort history
  behavior; only admission opts into strict failure signaling.

## Independent Review Gate

The reviewer must challenge the design, not merely check implementability.
Return PASS or BLOCKED and report P0-P2 findings. In particular:

1. Is there exactly one selected usage value, with no surviving conjunctive
   child count or prospective launch charge?
2. Does each task force an additive `terminal: true` latest mapping from its
   existing terminal report path; does positive scoped host-process liveness
   remain stronger than the hint; does the hint release otherwise dead or
   unprobeable rows across raw-log retirement/Manager restart; and does SQLite
   union active launches and committed children once per TID without changing
   runner-handle authority or adding a new store?
3. Are the three-slot floor, fractional rounding, and strict `<` comparisons
   correct at zero, equality, and small budgets?
4. Does `max_connections <= reserve` disable public work without incorrectly
   disabling internal work below the maximum?
5. Is the gate at the last safe pre-reservation seam for both watcher modes?
6. Can blocked state spin in either native or fallback waiting, starve reserved
   recovery/control, or miss external, child, or failed-launch recovery? Does
   the universal deadline work without queue activity?
7. Does PostgreSQL use only the package-root helper, existing persistent Queue,
   exact `numbackends` key, and configured maximum?
8. Are exception catches limited to the listed ordinary exception types so
   shutdown and control `BaseException` subclasses propagate?
9. Has every admission-specific PING/STATUS schema and test been removed?
10. Once a lower-TID primary is proven, does the non-primary avoid dispatching
    shared unreserved internal backlog while still finishing owned work?
11. Do nested history-open and filtering failures fail closed, with a firing
    compatibility test that default non-admission callers remain best effort?
12. Is the non-causal admission-specific pipeline test removed without a
    production hook, timing assertion, or topology-aware replacement?
13. Does the Manager memo fingerprint include every mapping field that affects
    liveness, including normalized `terminal`?
14. Does each added test prove a correctness outcome from this review, and can
    any config, state field, test, abstraction, or process gate be removed
    without weakening one of those outcomes?

Every accepted finding must be incorporated and rechecked before
implementation resumes.

## Review Report

### Earlier plan and implementation reviews

The former reviewed design used a Manager-local task cap conjoined with a
PostgreSQL server-headroom gate. That model is superseded. Its fixed one-task
reserve, fixed two-connection launch charge, dual capacity objects, and two old
environment settings are no longer planned behavior.

The independent review returned BLOCKED. All five findings are accepted and
incorporated:

1. The first review requested positive current runtime proof. The owner
   initially rejected that extra admission policy in favor of unconditional
   counting. A subsequent owner-directed re-review (2026-08-26) reversed that
   disposition: unconditional counting made `used` track recent task history
   (mapping rows persist at least the age-gated cleanup window after task
   exit), rate-limiting dispatch and stalling over-cap pipelines. The observer
   now filters the reduction through the shared `mapping_row_is_live` probe —
   the same payload-only policy cleanup and endpoint resolution apply, so no
   second liveness rule was added — unions in-flight launches, and memoizes
   verdicts. Dead rows release immediately; undecidable rows stay counted.
2. PostgreSQL catches only `DatabaseError`/`ValueError`; SQLite catches only
   `BrokerError`/`OSError`/`RuntimeError`; `BaseException` propagates.
3. The planned admission PING/STATUS object and its tests are deleted. Only
   rate-limited, non-normative transition/failure logs remain.
4. Acceptance now proves universal deadline recovery without queue activity
   and failed-launch restoration/retry. The dedicated over-cap pipeline test,
   removed while the observer counted unconditionally (it could not pass
   inside the cleanup window), is restored under the live-filtered observer as
   the churn-release acceptance proof.
5. SQLite is described as context-scoped and PostgreSQL as server-wide. The
   three-slot floor is modeled internal-lane room, not dedicated permits.

The full TID-mapping history scan remains accepted for opt-in v1:
`O(rows)` time and `O(unique TIDs)` memory per decision, with no cache or new
index. Independent plan re-review passed. Independent completed-work review
then found and verified fixes for the failed-launch deadline and vacuous live
PostgreSQL threshold; its final result is PASS.

That cost and PASS statement are historical. The current revision keeps one
mapping scan, adds the terminal mapping hint, and the later review below
supersedes the earlier completion verdict.

### 2026-08-26 completed-work review of `a659a481`

Verdict: BLOCKED. These findings are accepted and define the remaining work:

1. **Terminal external runners never release SQLite capacity.** The shared
   liveness probe correctly treats handles without host-process evidence as
   undecidable, but that policy alone cannot distinguish a completed Docker or
   other external runner. Publish a task-owned terminal hint on the same latest
   mapping row and let the shared payload-only probe treat it as dead. Do not
   change runner-handle authority or introduce a second lifecycle store.
2. **A proven duplicate Manager can deadlock admission.** Shared unreserved
   internal backlog currently vetoes leadership yield. Remove that false
   ownership evidence so the non-primary exits and frees its slot; preserve
   already-reserved, launching, and locally tracked work.
3. **Committed local children disappear before mapping publication.** The
   launch result moves a TID from `_active_child_launches` to `_child_processes`,
   but admission observes only the first set. Union both sets once per TID.
4. **Nested SQLite history-open failures fail open.** The general history
   iterator intentionally converts generator-open failures to an empty stream.
   Add opt-in strict propagation through the existing helper/reducer seam, and
   catch the full admission reduction/filtering path so enabled admission fails
   closed.
5. **Fallback waiting hot-loops on a blocked source.** Manager's fallback
   `_has_pending_messages()` does not apply admission lane suppression. Skip
   only blocked spawn sources there; reserved recovery remains actionable.
6. **Docs and test claims overstate the implementation.** Align Quick Reference
   and [MANAGER.18] with the exact SQLite formula and local-only memo. Reclassify
   then remove the non-causal admission-specific pipeline completion case. Add
   only regressions tied to the five correctness failures above.

The correction deliberately adds no provider abstraction, state queue,
permit, new status schema, process-role accounting, or pipeline branch.

### 2026-08-26 fresh-eyes review of this revision

Verdict: BLOCKED, then revised for re-review. All three findings are accepted:

1. Raw terminal task-log rows can be collated and deleted while an undecidable
   external-runner mapping remains. The task-log join is removed. The existing
   mapping publication path now carries the terminal liveness hint, so release
   survives raw-log retirement and Manager reconstruction without a new store.
2. The strict history option now has a paired compatibility proof: default
   generator-open failure still yields an empty reduction, while
   `strict=True` re-raises for admission to fail closed.
3. The admission-specific pipeline test is removed outright. Its completion
   assertion was non-causal and duplicated generic pipeline lifecycle coverage.

Final plan re-review of these dispositions: PASS.

The first re-review remained BLOCKED on two accepted details:

1. `terminal: true` cannot override positive live `(pid, create_time)` evidence,
   because the shared probe also protects the newest mapping from destructive
   cleanup. The hint is now explicitly subordinate to positive host liveness,
   with a zero-age cleanup regression.
2. `_admission_probe_memo` previously fingerprinted only `runtime_handle`.
   Normalized `terminal` now participates in the fingerprint, with a same-
   Manager false-to-true invalidation regression.

The final re-review found no remaining P0-P2 issue, unnecessary process,
non-causal test, or abstraction that could be removed without weakening a
stated correctness outcome.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|---|---|---|---|---|
| [MA-1.8], [MANAGER.18] | Dedicated pipeline admission engagement/completion test if observable without production hooks | No dedicated test; ordinary lane admission tests plus the existing pipeline lifecycle suite cover the unchanged path | A deterministic transient-retention assertion required a production hook or timing dependency and did not improve the production contract | None; specs already require no pipeline special case |
| [MA-1.8], [MANAGER.18] | Count every latest-per-TID mapping, including stale rows | Filter the reduction through the shared `mapping_row_is_live` probe and union active launches plus committed children; dead rows release immediately, undecidable rows stay counted | Owner-directed 2026-08-26 correction: unconditional counting rate-limited dispatch to the cleanup window; the shared probe adds liveness without a second policy | Promoted into all admission spec sections |
| [MA-1.8], [MANAGER.18] | Restored over-cap pipeline completion test presented as churn-release acceptance proof | Remove the admission-specific test; lane retention/retry and terminal-release regressions prove admission behavior | Completion alone does not prove the gate engaged, and making that causal would require a production hook or timing dependence | No behavior change; test-evidence correction implemented |
| [MA-1.8], [MANAGER.18] | Shared liveness probe alone determines whether every mapped TID is counted | A task-owned terminal mapping hint releases mapping-only external runners; active launches and committed local children remain counted until Manager cleanup/reap | Completed external runners can have valid but host-unprobeable control handles; the retained mapping is the history admission always reads | Promoted into [CC-2.4], [MA-1.8], [MF-5], [MF-6], [OBS.13.7], and [MANAGER.18] |
| [MF-6], [MANAGER.18] | Existing leadership and wait paths need no further admission-specific correction | Proven non-primary Managers ignore shared unreserved backlog for yield; fallback pending-work detection suppresses blocked spawn sources but not reserved recovery | Review reproduced a duplicate-manager capacity deadlock and fallback busy spin | Promoted into [MA-1.8], [MF-6], and [MANAGER.18] |
| [CC-2.4], [OBS.13.7], [MA-1.8] | Raw terminal task-log reduction releases external-runner mappings | Task-owned `terminal: true` latest mapping makes the shared payload-only probe return dead across task-log retirement and Manager restart | The raw log is intentionally deletable; the latest mapping is the only retained evidence admission always reads | Promoted into [CC-2.4], [MA-1.8], [MF-5], [MF-6], [OBS.13.7], and [MANAGER.18] |
| [OBS.13.7], [MA-1.8] | `terminal: true` returns dead before host-process probing; memo fingerprints only `runtime_handle` | Positive scoped host-process liveness wins; otherwise the terminal hint may return dead; memo fingerprints both handle and normalized terminal hint | Prevent deletion of a still-live wrapper mapping and prevent stale same-Manager live memo reuse | Promoted into [MA-1.8], [MF-6], [OBS.13.7], and [MANAGER.18] |

## Current Corrected Implementation Evidence (Uncommitted)

Baseline `a659a481` remains the historical reviewed implementation that exposed
the six corrective findings above. The corrected implementation is the current
uncommitted worktree relative to that baseline. It has passed the required
implementation review and gates, but it is not a completion marker because the
owner did not request a commit.

- SQLite now uses strict latest-mapping reduction, shared payload-only
  liveness, a runtime-handle-plus-terminal memo fingerprint, and the set union
  of live-or-undecidable mappings, active launches, and committed children.
  Terminal mapping publication is best effort across queue acquisition,
  history scan, close, and append failures, so it cannot block terminal task
  logs or control evidence. Heartbeat terminal paths enter `completed` before
  reporting.
- Proven non-primary Managers no longer treat shared unreserved internal
  backlog as owned work. Fallback waits suppress only blocked spawn sources;
  reserved recovery remains actionable. The non-causal over-cap pipeline test
  was removed without adding a pipeline production branch or test hook.
- Corrected spec-promotion blobs relative to `a659a481` are
  `00=d8eb7b1a1e25dda3d41d4bab48a9d34dce3f384e`,
  `01=4019f8b8e7feb34e3622f2a787b4d29b35b3582c`,
  `03=facbe66ffaf2255cbc8340df457d98e4650bf392`,
  `04=3d79dc95fe8dde2033d3b11fe828b525107284a4`,
  `05=d03c600707a77b9fe2d1cc728a7a61ea7d4eccfc`, and
  `07=becb734cd983136fdcca3e93220202dae125941e`.
- Verification: spec metadata/hygiene 8 passed; focused SQLite/touched suites
  passed with three expected PostgreSQL-only skips; real PostgreSQL admission
  14 passed / 11 SQLite-only skipped; final default suite 4,304 passed / 5
  skipped; mypy reported no issues across 187 source files; Ruff and
  `git diff --check` passed. Backstitch completed with the repository's 27
  existing errors and 997 warnings, with no admission-specific error after the
  implementation-mapping symbol was corrected.
- Independent completed-work review first found three issues: mapping-scan
  failures could block terminal evidence, Heartbeat reported before entering a
  terminal state, and two external-runner fixtures used an invalid handle kind.
  All three were fixed with firing regressions or corrected fixtures. The next
  pass found only this plan's stale completion evidence, which was corrected.
- Rollback remains configuration-only: unset or set
  `WEFT_ADMISSION_MAX_CONNECTIONS=0` and restart the Manager. The disabled-path
  regression proves that no observer runs.
- Remaining limitations are unchanged: SQLite scans retained mapping history
  per decision; undecidable non-terminal rows remain counted; both backend
  observations are non-atomic. Canary and deployment evidence are outside this
  implementation request.

## Execution Log

- 2026-08-26: the initial failure-first implementation targeted the now-
  superseded `WEFT_ADMISSION_MAX_TASKS` and
  `WEFT_ADMISSION_POSTGRES_HEADROOM_PERCENT` contract. Those results are not
  acceptance evidence for this revision. New failure-first evidence must start
  with the confirmed config names and single-observer behavior.
- 2026-08-26: final-config failure-first collection failed on the missing
  `ADMISSION_SERVICE_RESERVE_SLOTS` symbol (exit 1), then the implementation
  replaced the obsolete dual-capacity path. Independent completed-work review
  found that failed launch cleared rather than scheduled its backstop and that
  the live PostgreSQL threshold was vacuous. The implementation now schedules
  a lane retry after failure restoration, verifies source restoration and
  retry without unrelated activity, and uses `N=4`, `f=0` so real
  `numbackends >= 1` is the cause of denial.
- 2026-08-26 live-filter correction: the SQLite observer now filters the
  latest-per-TID reduction through the shared `mapping_row_is_live` probe with
  a Manager-local memo (dead permanent per unchanged handle, live expiring on
  the recheck interval) and unions `_active_child_launches` once per TID. New
  observer tests cover dead-row release, undecidable conservatism, launch
  union, and memoization; the over-cap pipeline acceptance test is restored
  and passes (`WEFT_ADMISSION_MAX_CONNECTIONS=6`, three stages, completes in
  the harness). Gates: 476 passed/3 PG-only skips across the touched files on
  SQLite; `bin/pytest-pg` admission run green; mypy 187 files clean; ruff
  clean.
- 2026-08-26 completed-work review of `a659a481`: BLOCKED. Reproduced terminal
  external-runner retention, committed-child/pre-mapping undercount,
  nested-history fail-open, shared-internal-backlog duplicate-manager deadlock,
  and fallback-wait busy spin. Documentation and the pipeline test claim also
  drifted from the actual correctness evidence. This plan revision accepts all
  findings and narrows the repair to existing lifecycle, history, leadership,
  and wait seams.
- 2026-08-26 corrected implementation: promoted the terminal mapping and
  admission contracts, added strict history propagation only at the admission
  call site, unioned committed children, fixed memo invalidation,
  duplicate-manager yield, and fallback wait behavior, and removed the
  non-causal pipeline test. Failure-first regressions covered each reproduced
  liveness/correctness gap.
- 2026-08-26 final review loop: the first corrected-tree pass found best-effort
  mapping scan containment, Heartbeat terminal ordering, and invalid external-
  handle fixture gaps. After repair, the second pass found only stale plan
  completion evidence. A later pass caught the repository rule that an
  uncommitted slice cannot be marked completed; status and evidence were
  restored to an explicitly reviewed-but-uncommitted state. The last stale
  historical/current-baseline phrase was then corrected, and the final pass
  returned PASS.
- 2026-08-27 independent completed-work review (Claude, different family from
  the implementing Codex session): PASS. Verified the corrected tree delivers
  all six accepted findings with firing regressions — terminal-hint release
  subordinate to positive host liveness, duplicate-manager yield on shared
  backlog, committed-children union, strict fail-closed observation, blocked-
  lane wait suppression, and docs/test-evidence realignment (including the
  Heartbeat mark-before-report ordering). Gates rerun by the reviewer:
  focused admission/terminal suite 39 passed (3 PG-only skips); real
  PostgreSQL via `bin/pytest-pg` 14 passed (11 SQLite-only skips); mypy clean
  across 187 source files; Ruff clean; plan metadata and spec hygiene green;
  full default suite 4,304 passed / 5 skipped / 0 failed. The owner authorized
  the completion commit; the commit introducing this entry is the completion
  marker.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope and strategy | 0 | Not run | Not required for this corrective backend slice |
| Codex Review | `/codex review` | Independent implementation review | 7 | Clear | Baseline and corrected-tree findings resolved; final pass returned PASS with the worktree explicitly uncommitted |
| Eng Review | `/plan-eng-review` | Architecture and tests (required) | 1 | Clear | Seven plan issues found across review rounds; zero unresolved and zero critical gaps after revision |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | Not run | No UI scope |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | Not run | No developer-interface change beyond existing configuration |

**CODEX:** The corrected implementation and its causal tests passed independent
review for correctness, unnecessary process, non-causal evidence, and
overengineering. The 2026-08-27 independent completed-work review (different
agent family) confirmed the corrected tree and reran every gate green.

**VERDICT:** CODEX + ENG + INDEPENDENT COMPLETED-WORK REVIEW CLEARED — the
owner authorized the completion commit and the commit introducing this report
revision is the completion marker.

NO UNRESOLVED DECISIONS

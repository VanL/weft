# Manager Admission Control Plan

Status: draft
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1], [MA-1.1], [MA-1.4], [MA-1.6a], [MA-1.8]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]; docs/specifications/05-Message_Flow_and_State.md [MF-6]; docs/specifications/07-System_Invariants.md [MANAGER.9]-[MANAGER.18]
Superseded by: none

Class: 5. This changes the Manager execution path, scheduling behavior, config,
backend observation, and failure handling. Spec promotion, real-backend
acceptance, rollback instructions, and independent review are required.

Review state: revised on 2026-08-26 after independent review and owner
correction. The dispositions are recorded under **Review Report**. The previous
conjunctive model, admission-specific positive-liveness policy, and PING/STATUS
schema are superseded within this draft. A final independent re-review is
required after the owner correction.

## Goal

Delay child launch before reservation when the active backend's observed usage
cannot admit the source lane. Denied work stays in its existing spawn queue and
advances when usage falls below the lane limit.

Admission uses one soft, backend-specific usage observation per decision:

- SQLite counts the union of live-probed latest mappings (one per full TID in
  the current Weft context, kept when the shared payload-only probe reports
  live or undecidable) and this Manager's in-flight child launches, each TID
  once. A latest mapping whose handle probes dead leaves the count on the next
  observation; undecidable rows remain counted. This is a best-effort proxy,
  not a count of SQLite handles or an atomic task permit.
- PostgreSQL uses the raw server-wide `numbackends` value returned by
  `simplebroker_pg.get_connection_stats()` through the Manager's existing
  persistent Queue.

There is no second local child-count guard and no per-launch connection
charge; the Manager's in-flight launches enter the single SQLite usage value
as evidence, not as a separate gate. One reserve derived from the configured
maximum pauses the public lane before the internal lane. The reserve has a three-slot floor that
models room in the internal lane for Manager, TaskMonitor, and Heartbeat. It is
not three dedicated permits and does not attribute a slot to a service.

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
5. blocked-lane state plus one retry deadline.

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

Existing leadership behavior remains unchanged. Registry leadership is
advisory for public work-stealing dispatch; atomic spawn-row reservation is the
current dispatch authority. A Manager that has entered leadership drain does
not dispatch, but merely observing a lower-TID primary is not a new global
pre-reservation fence while actionable work exists. Every Manager that reaches
the admission seam applies the same configured guard.

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

The 2026-08-26 owner correction replaced unconditional mapping counting with a
live-filtered union. The observer:

1. calls
   `latest_tid_mapping_entries_for_endpoint_resolution(self._task_context())`
   from `weft/core/endpoints.py` to reduce the TID-mapping queue to the latest
   payload for each full TID;
2. keeps each reduced row whose payload the shared
   `weft/core/monitor/policies/tid_mapping.py::mapping_row_is_live` probe
   reports live or undecidable — the exact payload-only policy TaskMonitor
   cleanup and endpoint resolution already apply. Admission introduces no
   second liveness rule; it reads a mapping row the same way the rest of the
   system does;
3. unions the surviving TIDs with this Manager's `_active_child_launches`,
   whose admitted launches have no durable evidence yet, and counts each TID
   once.

A latest row whose handle probes dead therefore leaves the count on the next
observation instead of waiting for age-gated cleanup retirement, so capacity
recycles with task churn. Undecidable rows (no handle, no probeable host
processes) remain counted, preserving the overcount-over-undercount bias for
live tasks whose runtime proof is missing or inconclusive.

Probe verdicts are memoized per TID in Manager-local state
(`_admission_probe_memo`): a dead verdict is permanent while the row's runtime
handle payload is unchanged, because a probed `(pid, create_time)` identity
never becomes live again; a live verdict expires after
`MANAGER_ADMISSION_RECHECK_SECONDS` so death is observed promptly. Memo
entries for TIDs no longer present in the reduction are pruned each
observation, bounding memory to current mapping rows.

The accepted v1 cost is a full TID-mapping history scan per admission decision
(`O(rows)` time, `O(unique TIDs)` memory) plus one shared-probe call per
expired or new memo entry. Admission is opt-in, and the first release favors
the shared correctness policy over a cache with a second invalidation
lifecycle. Canary evidence must include observer cost. A later optimization
requires its own measured design; it must not silently add a divergent
live-TID index.

## Existing Structures to Reuse

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
- `weft/core/endpoints.py::latest_tid_mapping_entries_for_endpoint_resolution`
  owns latest-per-TID reduction for SQLite admission.

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
| SQLite | Count of one latest TID mapping per full TID in this Weft context |
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
latest-TID-mapping proxy. Configuration is snapshotted when the Manager starts; changes
require restart.

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

SQLite calls the latest-per-TID reducer, keeps the rows the shared
`mapping_row_is_live` probe reports live or undecidable (memoized: dead is
permanent per unchanged handle, live expires on the recheck interval), unions
this Manager's in-flight child launches, and returns the size of that TID set.
The result is context-scoped and best effort: it is an observed live-TID
count, not an exact SQLite connection count, durable permit set, or ownership
claim.

PostgreSQL calls
`get_connection_stats(self._get_connected_queue())["numbackends"]` only on the
effective PostgreSQL path and on the Manager reactor thread. Import from the
`simplebroker_pg` package root. Do not import private SQL, parse raw rows, add
psycopg, call `open_broker()`, or open an ephemeral Queue.

Each successful observation is valid for one launch decision. A later decision
re-observes. Observation and launch are non-atomic on both backends.

The exception boundary is exact. PostgreSQL observation fails closed only on
`DatabaseError` or `ValueError`. SQLite observation fails closed only on
`BrokerError`, `OSError`, or `RuntimeError`. These are ordinary `Exception`
subclasses. Do not catch `BaseException`, match exception strings, or suppress
shutdown/control flow. Failure leaves the source row unreserved and schedules
the same one-second retry as a capacity denial.

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

### Minimal operational evidence

Admission adds no PING/STATUS field, public status schema, CLI rendering, or
control-path observation. Existing Manager control responses remain unchanged.

Emit only existing rate-limited structured logs when the internal admission
state changes or observation fails. These logs are non-normative operational
evidence, not lifecycle truth or a stable API. They may include backend, lane,
`used` when known, reserve, and the two limits. Never log SQL, credentials,
TaskSpecs, raw exception text, or a full traceback. Keep the retry deadline and
cached decision state private.

## Correctness Boundaries and Failure Behavior

- Admission runs before reservation. A denied source row remains unowned and
  the Manager reserved queue is unchanged.
- Already-reserved rows keep current inspection, acknowledgement, policy,
  leadership-requeue, cleanup, and drain behavior.
- Internal priority is unchanged. Admission changes eligibility, not order.
- SQLite and PostgreSQL use one selected observation, never a conjunction.
- SQLite counts one latest mapping entry per full TID when the shared probe
  reports it live or undecidable, unioned with this Manager's in-flight
  launches. A row whose latest handle probes dead is released immediately;
  undecidable rows remain counted.
- SQLite admission owns no liveness policy beyond the shared
  `mapping_row_is_live` probe; the per-TID verdict memo is Manager-local,
  pruned to current rows, and adds no cleanup, index, or freshness rule.
- PostgreSQL uses raw `numbackends`; do not subtract the probe connection or
  filter by role, database, backend type, context, or application name.
- The three-slot floor always applies, even when the fraction would reserve
  fewer slots or the Manager is already represented in `used`.
- Observation overcount can pause early. Missing or racing evidence can still
  overshoot. The guard is deliberately best effort and non-atomic.
- Observation failure fails closed only when admission is enabled. Disabled
  admission preserves current dispatch.
- Persistent internal work may block the lane forever. Admission logs
  transitions but does not evict, preempt, or raise a pipeline-specific error.
- No task state, TID, queue name, reserved policy, result delivery, process
  title, or TaskSpec shape changes.

## Files Expected to Change

The released prerequisite is already complete in SimpleBroker and in Weft's
dependency adoption slice. This plan does not modify `../simplebroker`.

Expected Weft changes:

- `weft/_constants.py`;
- `weft/core/manager.py`;
- `tests/system/test_constants.py`;
- `tests/core/test_manager.py`;
- the source specs listed above plus
  `docs/specifications/00-Quick_Reference.md`;
- `README.md`, `CHANGELOG.md`, this plan, and `docs/plans/README.md`.

No new dependency, queue, table, CLI command, backend protocol, provider
registry, or pipeline production branch is expected.

## Spec Baseline and Proposed Delta

- Baseline: repository commit `0f19367a5781ead326f632853b3c49bc07e9623d`
  plus the completed dependency adoption recorded in
  `docs/plans/2026-08-26-simplebroker-7-5-1-compatibility-plan.md`.
- Promotion strategy A: update the current contract sections below and retain
  reciprocal plan links before implementation resumes.
- The prior working-tree promotion identifiers described the superseded
  two-source design and must be recomputed after this revision.

### `docs/specifications/03-Manager_Architecture.md` [MA-1.8]

Specify:

> Before reserving a public or internal spawn row, an admission-enabled Manager
> observes one backend-specific usage value. SQLite counts latest mapping
> entries per full TID that the shared payload-only liveness probe reports
> live or undecidable, unioned with the Manager's in-flight child launches;
> a latest row whose handle probes dead is released immediately, and
> undecidable rows remain counted. PostgreSQL reads raw
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
> accounting or branch.

Map implementation to `Manager._process_queue_message()`, its backend usage
observer, blocked-lane state, and `next_wait_timeout()`; SQLite latest reduction
uses `latest_tid_mapping_entries_for_endpoint_resolution()` in
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

### `docs/specifications/05-Message_Flow_and_State.md` [MF-6]

Specify:

> Admission precedes spawn-row reservation. A denied public or internal row
> remains in its source queue. The Manager suppresses the known-blocked source
> as ordinary wait activity, re-evaluates it on a one-second deadline, and may
> wake earlier after child or launch-worker progress. No task lifecycle state
> or reserved-row policy is added.

### `docs/specifications/07-System_Invariants.md` [MANAGER.18]

Specify:

> Admission is a lane-specific pre-reservation soft guard over one
> backend-selected usage observation. Public work cannot consume the greater
> of the fractional reserve and the three-service floor; internal work can use
> the configured maximum. SQLite latest-TID-mapping and PostgreSQL `numbackends`
> observations are best effort and non-atomic. Pipelines receive no special
> weight, exemption, or branch. A blocked lane cannot block Manager control,
> cleanup, child reap, leadership convergence, service reconciliation, or
> shutdown.

### `docs/specifications/00-Quick_Reference.md`

Add the two config rows exactly as defined in **Config surface**, plus links to
[MA-1.8] and [MANAGER.18].

## Implementation Tasks

1. **Promote the revised contract and reuse SQLite TID reduction.**
   - Apply the exact spec delta and recompute the promoted spec patch identity.
   - Reuse the latest-per-TID reducer, filter through the shared
     `mapping_row_is_live` probe with the Manager-local verdict memo, and
     union in-flight launches.
   - Do not import `weft.commands` or add a liveness rule beyond the shared
     probe, and no cleanup lifecycle or persistent index.
   - Record the accepted `O(rows)`/`O(unique TIDs)` v1 cost in canary notes
     without turning a performance observation into a brittle timing test.

2. **Replace config and pure arithmetic.**
   - Remove the superseded task-cap and PostgreSQL-headroom settings and their
     fixed one-slot/two-connection constants.
   - Add the two confirmed settings, named three-service reserve floor, and
     one-second retry constant.
   - Add one pure reserve/limit calculation; do not add a policy registry or
     provider interface.

3. **Select one observer and gate both lanes.**
   - Gate at the existing pre-reservation seam.
   - SQLite calls the shared latest-per-TID reduction; PostgreSQL calls the
     released package-root helper through `_get_connected_queue()`.
   - Reuse active-queue and wait-deadline machinery.
   - Preserve reserved-row, control, cleanup, leadership, and internal-priority
     behavior.
   - Catch only `DatabaseError`/`ValueError` on PostgreSQL and
     `BrokerError`/`OSError`/`RuntimeError` on SQLite. Let `BaseException`
     subclasses propagate.

4. **Add bounded operational logs and harden the result.**
   - Add only rate-limited transition/failure logs; do not change PING/STATUS,
     CLI rendering, or any public response schema.
   - Document opt-in examples, restart semantics, best-effort observation,
     unsupported role/database/pooler attribution, and persistent saturation.
   - Run focused, full, type, lint, and real-PostgreSQL gates.
   - Run independent completed-work review before calling the feature ready.

## Outcome-Based Test Plan

```text
capacity available -> source row reserved -> existing launch path
public limit full   -> public row remains -> internal may launch
internal limit full -> both rows remain   -> control/reap still run
usage falls         -> retry/progress wake -> retained row advances
observation fails   -> both rows remain   -> retry, no hot loop
```

Required tests:

- Config and arithmetic: disabled maximum, minimum enabled maximum, invalid
  type/range, default and invalid reserve fraction, zero fraction with the
  three-slot floor, fractional ceiling above the floor, equality denial, and
  one-below admission for each lane. Cover `max_connections <= reserve` as a
  valid public-disabled/internal-usable boundary.
- SQLite observer: integration outcomes use schema-valid mapping rows to
  prove latest-per-TID deduplication, dead-row release, undecidable-row
  conservatism, once-per-TID launch union, and probe memoization (dead
  permanent, live within TTL). Do not duplicate the upstream probe or endpoint
  matrices beyond those admission-observable outcomes.
- Observable Manager behavior: denied rows remain in source and reserved stays
  empty; public pauses at its limit; internal pauses at the maximum; a progress
  wake and the one-second deadline each resume retained work; blocked queues do
  not spin. The universal one-second deadline must re-observe and advance work
  without unrelated queue activity. A failed child launch must not leave a
  restored or retained source suppressed indefinitely. Existing control,
  cleanup, and leadership suites own unchanged behavior; add no admission
  PING/STATUS test.
- Default and backend boundary: disabled admission preserves dispatch; SQLite
  never imports/calls the PostgreSQL helper; observer failure fails closed only
  when admission is enabled; the exact ordinary-exception catches fail closed,
  while a representative `BaseException` propagates.
- Service floor: the derived reserve is never below three, even when the
  fraction is zero or observed usage already contains a Manager. Prove it is
  lane arithmetic, not three service-specific permits.
- Pipeline: one end-to-end linear pipeline larger than the free slots under
  its configured maximum completes through ordinary stage churn, proving that
  finished tasks leave the observed usage count via the shared probe. This is
  the acceptance evidence that capacity recycles with task churn. Do not
  estimate topology or add a production special case.
- Real PostgreSQL: one focused Manager integration test through
  `bin/pytest-pg` uses the actual package-root helper and proves a deliberately
  tight configured limit leaves work in source. Upstream tests own SQL,
  permissions, result validation, and connection lifecycle.

Prefer source/reserved queue contents and completed work over private-state
assertions. Transition logs may corroborate a test but are not contract
authority. Do not repeat the full SimpleBroker extension or runtime-liveness
suite in Weft Manager tests.

## Verification

```bash
. ./.envrc
uv lock --check
uv sync --frozen --all-extras
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py -q
./.venv/bin/python -m pytest tests/system/test_constants.py tests/core/test_manager.py tests/tasks/test_pipeline_runtime.py -k 'admission or pipeline_continuation' -q
./.venv/bin/python bin/pytest-pg tests/core/test_manager.py
./.venv/bin/python -m pytest
./.venv/bin/mypy weft bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
git diff --check
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
best-effort latest-TID-mapping proxy rather than physical connections.

During canary, verify public pause, internal continuation, bounded retry,
control responsiveness, flat CPU while blocked, and resume after usage falls.
Do not propose default-on behavior without measured canary evidence.

Rollback requires no data cleanup: stop or drain the Manager, unset
`WEFT_ADMISSION_MAX_CONNECTIONS` (or set it to `0`), and restart. The feature
adds no persisted state.

Stop rollout if the Manager hot-loops, control latency regresses, measured
SQLite admission latency or CPU cost is unacceptable, ordinary-role PostgreSQL
queries fail, or the configured reserve is routinely consumed under the
canary.

## Work Order and Parallelization

The released dependency prerequisite and SQLite TID reduction are complete.
Config arithmetic, Manager gating, and wait behavior touch the same execution
path and should be implemented sequentially. Documentation can follow
observable behavior. Use a read-only independent reviewer after this plan
revision and after the finished slice; parallel edits to the Manager path are
not useful.

## Out of Scope

- durable or distributed permits;
- task adoption after Manager restart;
- a new leadership fence;
- a second Manager-local child or active-launch capacity source;
- pipeline-specific admission, weights, exemptions, topology inspection, or
  temporary reserve changes;
- prospective per-launch connection estimates or launch-cost constants;
- deriving limits from PostgreSQL `max_connections` or reserved settings;
- preemption, fairness classes, or persistent-task eviction;
- role/database/PgBouncer attribution;
- counting SQLite connection objects;
- raw PostgreSQL SQL or a generic backend-capacity protocol in Weft;
- new CLI commands, queues, tables, dependencies, or migrations.

## Independent Review Gate

The reviewer must challenge the design, not merely check implementability.
Return PASS or BLOCKED and report P0-P2 findings. In particular:

1. Is there exactly one selected usage value, with no surviving conjunctive
   child count or prospective launch charge?
2. Does SQLite filter the latest-per-TID reduction through only the shared
   `mapping_row_is_live` probe (undecidable rows counted, dead rows released,
   launches unioned once per TID), without importing `weft.commands` or adding
   a liveness, timeout, cleanup, or persistent-index policy beyond the
   Manager-local verdict memo?
3. Are the three-slot floor, fractional rounding, and strict `<` comparisons
   correct at zero, equality, and small budgets?
4. Does `max_connections <= reserve` disable public work without incorrectly
   disabling internal work below the maximum?
5. Is the gate at the last safe pre-reservation seam for both watcher modes?
6. Can blocked state spin, starve control, or miss external, child, or failed-
   launch recovery? Does the universal deadline work without queue activity?
7. Does PostgreSQL use only the package-root helper, existing persistent Queue,
   exact `numbackends` key, and configured maximum?
8. Are exception catches limited to the listed ordinary exception types so
   shutdown and control `BaseException` subclasses propagate?
9. Has every admission-specific PING/STATUS schema and test been removed?
10. Does pipeline coverage remain on ordinary lane and lifecycle outcomes,
    without a production hook, timing assertion, or topology-aware admission?
11. Can any config, state field, test, or process gate be removed without
    weakening a stated correctness outcome?

Every accepted finding must be incorporated and rechecked before
implementation resumes.

## Review Report

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

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|---|---|---|---|---|
| [MA-1.8], [MANAGER.18] | Dedicated pipeline admission engagement/completion test if observable without production hooks | No dedicated test; ordinary lane admission tests plus the existing pipeline lifecycle suite cover the unchanged path | A deterministic transient-retention assertion required a production hook or timing dependency and did not improve the production contract | None; specs already require no pipeline special case |
| [MA-1.8], [MANAGER.18] | Count every latest-per-TID mapping, including stale rows | Filter the reduction through the shared `mapping_row_is_live` probe and union in-flight launches; dead rows release immediately, undecidable rows stay counted | Owner-directed 2026-08-26 correction: unconditional counting rate-limited dispatch to the cleanup window and stalled over-cap pipelines; the shared probe adds liveness without a second policy | Promoted into all admission spec sections |

## Completion Summary

- SQLite usage observation: resolved to the live-filtered union — latest
  mapping per full TID reduced by
  `latest_tid_mapping_entries_for_endpoint_resolution(self._task_context())`,
  filtered through the shared `mapping_row_is_live` probe with a Manager-local
  memo (dead permanent per unchanged handle, live expiring on the recheck
  interval), unioned with `_active_child_launches`, each TID once. Accepted
  `O(rows)` scan plus memoized probes per decision.
- spec promotion identifier: worktree blobs at commit time
  `00=9ec212e8096556cf977f31bc7f133dbb36cb9fff`,
  `03=aebd6bdbd79d52b21ef1d4742cbc04d48ff867b4`,
  `04=3d79dc95fe8dde2033d3b11fe828b525107284a4`,
  `05=ad716795d436be5502ddfc0423f27bd829fcc6b3`, and
  `07=6fc4608b1184d1aae8a01cd63f8460f4709872fe`.
- implementation commit: the commit introducing this summary (see `git log`
  for this file).
- files changed: `_constants.py`, `core/manager.py`, constants/Manager tests,
  the restored over-cap pipeline acceptance test, five governing specs,
  README, CHANGELOG, plan index, and lessons. The commit also carries the
  completed SimpleBroker 7.5.1 dependency-adoption slice recorded in
  `docs/plans/2026-08-26-simplebroker-7-5-1-compatibility-plan.md`.
- focused SQLite result: all admission tests passed (PostgreSQL-only skips);
  touched files in full: 476 passed / 3 skipped.
- focused PostgreSQL result: 14 admission tests passed through `bin/pytest-pg`
  against provisioned PostgreSQL, including the real helper and exact failure
  types (SQLite-only skips).
- full pytest/mypy/ruff results: default suite 4,294 passed / 5 skipped;
  mypy clean across 187 source files; Ruff clean; `git diff --check` clean.
- canary result: not run; deployment is outside this implementation request.
- independent review and dispositions: prior final PASS covered exact
  exceptions, status removal, failed-launch retry, and causal PostgreSQL test
  corrections. The 2026-08-26 owner-directed live-filter correction is
  recorded in the Review Report and Deviation Log; final independent
  re-review of the corrected slice is pending.
- deviations promoted to spec: the live-filtered SQLite observation (see
  Deviation Log) is promoted into [MA-1.8], [MF-6], and [MANAGER.18].
- rollback verification: admission defaults disabled; unsetting or setting
  `WEFT_ADMISSION_MAX_CONNECTIONS=0` preserves dispatch without invoking an
  observer, covered by a firing test.
- remaining limitations: SQLite scans retained mapping history per decision;
  undecidable mapping rows remain counted; both backend observations are
  non-atomic and can under- or over-estimate instantaneous launch pressure.

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

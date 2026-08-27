# Bounded TID Mapping Publication and Retention Plan

Status: draft
Source specs: docs/specifications/01-Core_Components.md [CC-2.2], [CC-2.5]; docs/specifications/05-Message_Flow_and_State.md [MF-5], Cleanup Boundary; docs/specifications/07-System_Invariants.md [OBS.6], [OBS.13.7]
Superseded by: [Manager Admission Control Plan](./2026-08-25-manager-admission-control-plan.md)

This historical draft is not approved implementation work. The admission
design bounds SQLite dispatch from observed live TIDs without changing TID
mapping publication or retention. Its separate `R5_future`, writer, cleanup,
and benchmark proposal is outside the approved feature and must not be treated
as an admission-control gate.

Class: 5 - spec-changing and risky. This changes the normative runtime-state
publication contract on the durable task spine and corrects the cleanup
exclusion policy needed to retire the resulting superseded rows. The Class 4
hardening checklist and independent review before implementation are mandatory.

Review state: author fresh-eyes and independent draft-v3 plan review PASS after
three review rounds. Spec promotion and implementation remain blocked until the
live `R5_future` measurement and PostgreSQL capacity gates in Task 1 pass and
are recorded. The 2026-08-26 measurement amendment replaces generic connection
counting and ambient-DSN assumptions with the released
`simplebroker_pg.get_connection_stats()` API and `bin/pytest-pg`; it does not
change the proposed publication or retention contract. Independent re-review
passed after replacing an arbitrary repeated-cycle protocol with the three
cleanup outcomes that own distinct iterator lifecycles.

## Goal

Remove the unbounded read-before-write replay of
`weft.state.tid_mappings` from task initialization, activity, runtime-handle,
PID, and terminal transitions. TID mapping producers will append complete
runtime-observability snapshots without first determining whether the newest
row is semantically equivalent. Equivalent rows are valid. Existing consumers
continue selecting the newest valid row per full TID. TaskMonitor cleanup will
apply the existing age-only policy to every superseded row, including older
rows for the cleanup monitor's own excluded TID; the exclusion protects only
that TID's newest row. Protected newest rows at the FIFO head will not hide
eligible superseded rows later in the aged queue prefix.

The result must be a full forward fix for the transition-path defect: mapping
publication cost and failure behavior cannot depend on mapping-history depth,
and a mapping-specific broker failure cannot abort the remaining task lifecycle
publication path.

## Requested Outcomes

- [ ] `BaseTask` performs no mapping-history read as part of TID mapping
  publication.
- [ ] Semantically equivalent complete mapping snapshots may be appended.
- [ ] Actual activity changes publish current `activity` and `waiting_on`
  fields instead of being suppressed by a comparator that ignores them.
- [ ] A mapping iterator failure is no longer reachable from task startup or
  terminal publication.
- [ ] A mapping append failure remains best effort and does not replace or
  abort task-owned lifecycle publication.
- [ ] Latest-row status, endpoint, runtime-pruning, and cleanup consumers remain
  correct with equivalent consecutive rows.
- [ ] Superseded-row cleanup remains safe and is proven against equivalent
  snapshots; the newest row per full TID keeps the current liveness gate, and
  a TaskMonitor self-exclusion cannot exempt its superseded history.
- [ ] Cleanup skips protected head rows, selects at most one configured batch
  of eligible exact IDs across the aged queue prefix, and converges under
  repeated cycles without unbounded candidate memory.
- [ ] Live transition-rate evidence and a PostgreSQL capacity benchmark prove
  a four-times cleanup-rate margin and acceptable global-reader latency at the
  doubled production age-window depth before the new contract is promoted.
- [ ] The change ships without a new queue, payload version, persisted table,
  dependency, SimpleBroker API, compatibility reader, or data migration.
- [ ] SQLite, PostgreSQL, full-suite, lint, type, spec-hygiene, traceability,
  release, and post-deploy operational gates are rerun from the final state.

## Source Documents

- `docs/specifications/01-Core_Components.md` [CC-2.2] owns `BaseTask` queue,
  state-reporting, TID-mapping, and activity responsibilities. [CC-2.5] owns
  the shared startup-through-terminal execution flow that mapping publication
  must not abort.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5] owns lifecycle and
  runtime observation flow. Its Cleanup Boundary defines TID-mapping retention
  and newest-row selection.
- `docs/specifications/07-System_Invariants.md` [OBS.1], [OBS.6], [OBS.13.6],
  and [OBS.13.7] distinguish lifecycle authority from runtime observability and
  define exact cleanup safety.
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.3] is a consulted
  boundary: Weft continues using ordinary queue writes and generator-based
  history readers. [SB-0.4] supplies the released PostgreSQL operational
  snapshot used only as benchmark evidence. This plan does not change
  SimpleBroker or add a production connection guard.
- Published `simplebroker` 7.5.1 and `simplebroker-pg` 3.10.0 provide public
  `Queue.backend_name` and package-root
  `get_connection_stats(queue: Queue) -> dict[str, int]`. The exact result keys
  are `numbackends`, `max_connections`,
  `superuser_reserved_connections`, and `reserved_connections`.
- `docs/plans/2026-08-26-simplebroker-7-5-1-compatibility-plan.md` records the
  completed Weft dependency/API adoption slice. This plan consumes that locked
  baseline rather than modifying the sibling repository.
- `docs/agent-context/runbooks/writing-plans.md`, `hardening-plans.md`,
  `review-loops-and-agent-bootstrap.md`, and `testing-patterns.md` govern the
  plan, review, real-broker proof, and final gates.
- `docs/lessons.md`, especially the append-only history, exception-boundary,
  cleanup-progress, and forward-only rules, constrains the implementation.

## Context and Key Files

### Current structure

- `weft/core/tasks/base.py::BaseTask._register_tid_mapping()` builds one
  complete payload, calls `_latest_tid_mapping()`, compares selected fields
  with `_tid_mapping_equivalent()`, and writes only if the comparison differs.
- `_latest_tid_mapping()` calls the lazy `Queue.peek_generator()` and decodes
  every row in the shared queue to find the newest row for one full TID. Its
  `try` covers generator creation, not iteration, so broker errors raised while
  paging escape the intended best-effort boundary.
- SimpleBroker pages `peek_generator()` in batches of 1,000. A queue containing
  75,441 rows requires 76 `LIMIT/OFFSET` retrievals for one publication
  decision, regardless of how many rows belong to the current TID.
- `BaseTask._set_activity()` emits `task_activity` and then calls
  `_register_tid_mapping()`. Consumer startup does this after
  `mark_started()` and before `work_started`; terminal paths do it after the
  in-memory terminal transition and before the recognized terminal event.
- `_tid_mapping_equivalent()` excludes `activity`, `waiting_on`, and the
  per-publication `started` value. It therefore pays the global scan while
  suppressing many activity-only snapshots that the mapping payload is able to
  carry.
- `weft/commands/system.py`, `weft/core/endpoints.py`, TaskMonitor liveness,
  pruning, and test harness readers already reduce valid mapping history by the
  greatest message ID per full TID. Their explicitly global observation work
  is not on the per-task transition path.
- `weft/core/monitor/policies/tid_mapping.py::tid_mapping_candidates()` intends
  to classify every non-newest row for a full TID as superseded and apply the
  age-only rule. However, it passes `exclude_tids` into
  `older_than_candidates()` before newest/superseded classification.
  `TaskMonitor._run_task_monitor_cleanup_cycle()` supplies its own TID, so all
  mapping rows for the live monitor are currently excluded, not only its newest
  row. With append-on-change publication, the monitor's own superseded mapping
  history would never retire until a different monitor process takes over.
- `tests/core/monitor/policies/test_tid_mapping.py` already proves that, without
  an exclusion, two semantically equivalent rows for one live TID select only
  the older row. It does not cover an excluded TID or the real cleanup entry
  point that supplies the TaskMonitor self-exclusion.
- `weft/core/monitor/cleanup.py::run_task_monitor_cleanup()` reads only the
  first `batch_size` rows for candidate selection. When that head window is
  full of protected newest rows, its full-queue pass computes newest IDs but
  does not expose later rows to the policy. Repeated cycles therefore select
  nothing and can permanently hide eligible superseded rows in the tail. A
  four-row real-broker reproduction with two protected head keys and an
  eligible duplicate pair behind them produced zero deletions for three cycles.
- `tests/helpers/weft_harness.py::_load_tid_mapping_payloads()` reads only the
  first 2,048 rows and discards broker message IDs. Its teardown and live-PID
  discovery can miss the current mapping after the queue grows beyond that
  prefix, leak processes, or diagnose the wrong owner.
- `simplebroker_pg.get_connection_stats()` now provides the PostgreSQL
  capacity benchmark's server-wide snapshot through an existing Queue. It
  returns a fresh named dictionary and keeps its SQL in
  `simplebroker_pg._sql`. Pass the existing persistent benchmark Queue so no
  measurement-only connection path is added. `numbackends` is conservative: it
  includes the probe's own backend and may include database-attached workers
  that do not consume an ordinary client slot.

### Files to modify

- `docs/specifications/01-Core_Components.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `weft/core/tasks/base.py`
- `weft/core/monitor/cleanup.py`
- `weft/core/monitor/policies/tid_mapping.py`
- `tests/tasks/test_task_observability.py`
- `tests/tasks/test_task_execution.py` or the nearest existing Consumer
  lifecycle test module selected after the red fixture is written
- `tests/core/monitor/policies/test_tid_mapping.py`
- `tests/core/test_task_monitor_cleanup.py`
- `tests/helpers/weft_harness.py`
- `tests/test_harness_registration.py`
- `CHANGELOG.md`
- `docs/lessons.md`
- this plan and `docs/plans/README.md`

### Read before editing

- `weft/core/tasks/base.py`: constructor publication, `_set_activity()`,
  `_register_tid_mapping()`, mapping payload construction, terminal STOP/KILL
  paths, and queue-handle reuse.
- `weft/core/tasks/consumer.py`: `_begin_work_item()`, `_finalize_message()`,
  failure/timeout finalization, persistent work-item transitions, and direct
  `run_work_item()`.
- `weft/core/monitor/cleanup.py` and
  `weft/core/monitor/policies/tid_mapping.py`: newest-row and superseded-row
  cleanup semantics, bounded-head selection, full newest-ID evidence, exact
  deletion, and progress reporting.
- `weft/commands/system.py`, `weft/core/endpoints.py`, and
  `weft/core/pruning/runtime.py`: current latest-row consumers.
- `tests/tasks/test_task_observability.py` and
  `tests/core/monitor/policies/test_tid_mapping.py`: existing mapping writer
  and retention proofs.
- `tests/helpers/weft_harness.py` and `tests/test_harness_registration.py`:
  teardown-time mapping reduction and its current fixed-prefix assumption.

Comprehension checks before implementation:

1. Which event proves a Consumer is durably running, and which event closes a
   completed Monitor family? Answer: `work_started` and a recognized terminal
   event such as `work_completed`; a terminal-looking `task_activity` row is
   not a Monitor terminal event.
2. Why is a semantically equivalent mapping row safe? Answer: all current
   consumers select the greatest valid message ID per full TID, while cleanup
   protects that newest row and treats older siblings as superseded. For the
   TaskMonitor's own excluded TID, that classification must happen before the
   exclusion is applied.
3. Why does the fix belong in Weft rather than SimpleBroker? Answer: the
   transition writer does not need a lookup at all. Adding a generic keyed
   broker query or a second state store would solve an invented requirement.
4. Why is changing only `exclude_tids` insufficient? Answer: candidate
   selection currently sees only the FIFO head. Protected newest rows in that
   bounded window recur forever, so cleanup must scan past them while keeping
   selected exact IDs and memory bounded.

## Invariants and Constraints

- Keep the existing `TaskSpec -> Manager -> Consumer -> TaskRunner ->
  queues/state log` execution spine. Add no second publication or lifecycle
  path.
- `weft.log.tasks` and task-local terminal envelopes remain lifecycle
  authority. `weft.state.tid_mappings` remains best-effort runtime observation
  and liveness evidence.
- TID format, TID immutability, forward-only lifecycle state, reserved-queue
  policy, result ordering, TaskSpec `spec`/`io` immutability, queue names, and
  payload shape do not change.
- `weft.state.tid_mappings` remains runtime-only and excluded from dump/load
  persistence.
- Every emitted mapping remains a complete snapshot. Do not introduce patches,
  deltas, tombstones, sequence fields, or a second current-state representation.
- Mapping publication performs one ordinary write attempt and no queue-history
  read. The number of mapping rows must not change the publication algorithm or
  query count.
- Existing in-memory guards remain valid: `_set_activity()` suppresses an
  unchanged `(activity, waiting_on)` pair; runtime-handle registration suppresses
  an equal handle; managed-PID registration suppresses an already-known PID.
  Do not add a new mapping cache unless failing tests demonstrate a separate
  need.
- A direct `_register_tid_mapping()` call may append a semantically equivalent
  snapshot. Duplicate avoidance is not a writer correctness requirement.
- Catch only `BrokerError`, `OSError`, and `RuntimeError` around the mapping
  append. Payload construction and JSON serialization defects remain internal
  errors and must surface, consistent with the repository exception-boundary
  lesson.
- Do not reorder lifecycle events, terminal result delivery, reserved handling,
  or process-title transitions unless a failing contract test proves that the
  no-read append still leaves a correctness defect. Any such discovery is a
  stop-and-re-plan gate because it expands the reviewed behavior change.
- Preserve queue-handle reuse through `BaseTask._queue()`; do not open a fresh
  queue per snapshot.
- Preserve latest-valid-message-ID reduction in every consumer. Do not add a
  first-row, first-match, fixed-limit, or payload-equality shortcut.
- Preserve [OBS.13.7] liveness safety: equivalent older rows may age out, but
  the newest row cannot be deleted unless its payload supplies dead-owner
  proof under the existing policy.
- `exclude_tids` remains a current-row safety fence for TID mappings, not a
  retention exemption for a full mapping key. For an excluded TID, protect the
  greatest valid message-ID row; apply the same age-only rule to every valid
  superseded row. Malformed-row handling remains unchanged.
- `batch_size` bounds selected candidates and exact deletes, not how many rows
  cleanup may inspect to find those candidates. A protected newest row is a
  skip, never a FIFO stop or proof that the policy reached base. The cleanup
  scan may traverse the aged prefix or full queue under the existing global
  newest-ID evidence model, but candidate storage stays at most `batch_size`
  plus the existing one-newest-ID-per-key map.
- Preserve cleanup progress semantics: candidate-cap exhaustion is a catch-up
  waypoint; reaching queue tail or the valid-row age boundary without hidden
  eligible work is base. A full protected prefix with unscanned rows is neither.
- No new dependency, database table, queue type, queue name, backend SQL,
  SimpleBroker private API, or public CLI shape.
- PostgreSQL benchmark code may import only the released package-root
  `simplebroker_pg.get_connection_stats` helper. It must pass an existing
  target-resolved persistent Queue, read the result by key, and must not add
  psycopg, raw SQL, `pg_stat_activity`, private-extension imports, or
  field-order assumptions.
- Keep the diff local. Correct exclusion ordering in the TID-mapping policy and
  candidate reachability in the TID-mapping cleanup orchestrator; do not change
  generic `older_than_candidates()` semantics or consolidate production
  readers. Correct the test harness's fixed-prefix reducer because this change
  directly makes its hidden limit unsafe. Other paths retain their ownership.

## Spec Baseline

- `0f19367a5781ead326f632853b3c49bc07e9623d` - repository and governing
  specs at plan authoring time. The worktree was clean at baseline.
- Plan type: implementation with spec revision.
- Promotion strategy: A - edit the existing active spec sections after plan
  review and before code. Add the plan backlinks during promotion, but do not
  add new code-to-section claims until the implementation and reciprocal
  `Spec:` references land together.
- Promotion baseline identifier: pending. Record the last pre-promotion commit
  plus the promoted spec blobs or exact working-tree diff before code changes.

## Proposed Spec Delta

### `docs/specifications/07-System_Invariants.md` [OBS.6], [OBS.6a]

Replace [OBS.6] with:

> **OBS.6**: Each TID mapping row is a complete runtime-observability snapshot
> written to `weft.state.tid_mappings`. A consumer that requires current state
> selects the valid row with the greatest broker message ID for each full TID.
> Multiple rows for one full TID, including rows with equivalent observable
> fields, are valid ordered history; uniqueness per TID is not a queue
> invariant.

Insert immediately after [OBS.6]:

> **OBS.6a**: A TID mapping producer appends its complete current snapshot
> without reading or reducing `weft.state.tid_mappings` to decide whether to
> publish. Publication work is therefore independent of mapping-history depth.
> Existing owner-local state guards may suppress a registration request before
> publication, but queue-wide read-before-write deduplication is forbidden. A
> broker failure from the mapping append is a best-effort observability failure:
> it must not mutate lifecycle state, replace task-owned lifecycle evidence, or
> abort the remaining lifecycle publication path. Payload construction and
> serialization defects are not broker failures and remain visible internal
> errors.

### `docs/specifications/01-Core_Components.md` [CC-2.2]

Replace the current BaseTask responsibility bullet “maintain TID mappings and
process titles” with:

> - append complete best-effort TID mapping snapshots without replaying shared
>   mapping history, and maintain process titles

After the “state publication should come from one shared path” rationale, add:

> TID mapping registration is the shared [OBS.6]/[OBS.6a] publication path.
> Call sites decide whether their owner-local activity, runtime handle, PID, or
> diagnostic state changed; registration itself performs one append attempt and
> does not inspect global mapping history.

### `docs/specifications/01-Core_Components.md` [CC-2.5]

Insert after the six-step high-level flow:

> TID mapping publication follows [OBS.6] and [OBS.6a]. A mapping broker append
> failure is an auxiliary observability failure and cannot abort startup,
> work-started, terminal-state, result, or recognized terminal-event
> publication. Payload construction and serialization defects remain visible
> internal failures under [OBS.6a].

### `docs/specifications/05-Message_Flow_and_State.md` [MF-5]

Insert after the rule describing status reconstruction from task logs plus
latest TID mappings:

> TID mapping publication follows [OBS.6] and [OBS.6a]. Producers append a
> complete snapshot without first replaying the shared mapping queue, and
> equivalent consecutive snapshots are valid. Current-state consumers reduce
> valid rows by greatest broker message ID per full TID. This runtime-state
> publication remains best-effort and cannot become task lifecycle authority.

### `docs/specifications/05-Message_Flow_and_State.md` Cleanup Boundary and
`docs/specifications/07-System_Invariants.md` [OBS.13.7]

Insert in both locations immediately after the keep-newest-per-key rule, using
the exact same normative text:

> Semantically equivalent mapping rows remain distinct ordered snapshots. All
> but the greatest valid message-ID row for a full TID are superseded and
> follow the existing age-only rule; equivalence does not grant retention or
> weaken the newest row's payload-liveness gate. A cleanup-cycle exclusion for
> a TID protects only that TID's greatest valid message-ID mapping row; it does
> not exempt superseded mapping rows from age-only retention.

Immediately after that text in both locations, insert:

> Candidate and exact-deletion counts remain bounded by the configured cleanup
> batch size, but a protected newest row is a skip rather than a FIFO stop. When
> a bounded head window does not reach queue tail, TID-mapping cleanup must
> continue through the aged queue prefix until it selects one candidate batch,
> reaches the valid-row age boundary while still scanning for policy-deletable
> malformed rows, or reaches queue tail. A head window containing only
> protected newest rows cannot hide eligible superseded rows or be reported as
> cleanup base. Full-queue newest-ID evidence remains required before any valid
> row is classified as newest or superseded.

Add this plan to the three touched specs' `## Related Plans` sections. During
the implementation/linking slice, update nearby implementation mappings and
add reciprocal `Spec: [CC-2.2], [CC-2.5], [MF-5], [OBS.6], [OBS.6a],
[OBS.13.7]`
references at the mapping publication boundary. After promotion,
`docs/specifications/` is the only governing contract; this section remains
historical review material.

## Rollout and Rollback

- There is no stored-format or queue-name migration. Old and new processes may
  coexist: existing readers already accept multiple rows and choose the newest.
- New writers may create more superseded rows between cleanup cycles. Deploy
  only when the TaskMonitor's current cycle is healthy and
  `runtime_state.retention` is advancing. The canary must prove that the active
  TaskMonitor's own old mapping rows retire while its newest row survives. A
  broken monitor is a rollout blocker, not a reason to restore read-before-write
  deduplication.
- Treat the first production append from the new release as the operational
  one-way threshold for the old writer. After that point, do not deploy a Weft
  version that restores queue-wide read-before-write mapping replay. The rows
  are wire-compatible, but the old algorithm can reintroduce history-sized
  latency and lifecycle-aborting lazy-iterator failures at any depth.
- Release the Weft change first. Then update the downstream mm-governance Weft
  pin through its normal dependency and release gates. Do not add a downstream
  compatibility branch.
- Rollback is forward-only. If the added activity write volume is unsafe,
  release a patch that preserves the one-attempt/no-read writer and removes
  mapping refresh from `_set_activity()` while leaving `task_activity`
  lifecycle diagnostics intact. Constructor, runtime-handle, managed-PID, and
  explicit TaskMonitor health refreshes continue to publish mappings. This
  fallback sacrifices live activity fields in the mapping read model; it does
  not restore the incident path, change stored payload shape, or erase rows.
- Before the first production append, an ordinary deployment abort may keep the
  old release because no new history has been created. After it, the fallback
  above is the only approved release rollback. A defect in cleanup or readers
  is repaired forward while the direct writer remains in place.
- No destructive manual cleanup, queue rewrite, or schema change is authorized
  by this plan. There is no one-way storage-format door, but restoring the old
  writer after new publication begins is explicitly an operational one-way
  door.
- Stop rollout if transition latency still grows with mapping depth, if mapping
  depth grows monotonically across healthy cleanup cycles, if a current-state
  consumer selects anything other than the newest valid row, or if terminal
  event/outbox evidence regresses.

## Tasks

1. **Close the capacity and review gates before promoting the contract.**
   - From a read-only live mapping scan, record total rows, distinct full TIDs,
     rows older/younger than the 2,400-second minimum age, current output rates,
     and the busiest task roles. Do not use the current mapping append rate as
     the new-load predictor: baseline queue-wide dedup suppresses activity,
     `waiting_on`, timestamp-only, and other equivalent registration requests
     that the new writer will append.
   - Measure the future append input at `_register_tid_mapping()` entry, after
     owner-local call-site guards but before `_latest_tid_mapping()` or its
     comparator. Use a temporary benchmark-only wrapper or subclass, not a
     production `weft/` edit, and replay the production work mix at its observed
     peak submission rate. Include constructor, actual activity/waiting changes,
     runtime-handle changes, new managed PIDs, and explicit TaskMonitor health
     refreshes. Run through at least one representative scheduled incident
     burst and one persistent-Consumer burst. Call the maximum five-minute
     registration-request rate `R5_future` rows/second. Cross-check activity
     counts against durable `task_activity` rows and task construction against
     `task_initialized`; if any registration class or representative peak cannot
     be measured, promotion is blocked.
   - Set the PostgreSQL benchmark depth to
     `D = max(current_live_depth, current_distinct_tids + ceil(2 * R5_future * 2400))`.
     The factor of two is the reviewed sustained-rate margin, not a guessed row
     count. Seed equivalent and distinct rows so protected-head, superseded-tail,
     malformed, excluded-self, live-owner, dead-owner, and undecidable-owner
     classes are represented.
   - Benchmark the planned two-pass cleanup shape at `D` using the configured
     batch size and public generator APIs. Promotion requires all of these:
     sustained selected-and-deleted service rate at least `4 * R5_future`; a full
     candidate batch on every catch-up cycle while at least that many eligible
     rows remain; convergence to zero eligible over-age superseded rows in at
     most `ceil(initial_eligible / batch_size) + 1` cycles; newest-row safety in
     every class; and a dedicated cleanup-path PostgreSQL check whose final
     stable `numbackends` returns to its stable warmed baseline.
   - Measure that PostgreSQL condition only through the released package-root
     `simplebroker_pg.get_connection_stats()` helper. Pass the same
     target-resolved persistent Queue used by the benchmark context. Capture
     the complete four-key dictionary after Queue warm-up and after final
     quiescence. Read `numbackends` by key; do not depend on dictionary order,
     issue raw catalog SQL, open a sidecar psycopg connection, or subtract the
     probe's own backend.
   - Treat `numbackends` as conservative server evidence, not an exact count of
     Weft-owned connections. Run one dedicated test item on the isolated server
     with `PYTEST_XDIST_AUTO_NUM_WORKERS=1` so the pytest-pg wrapper creates only
     one worker. The fixture must execute the three cleanup outcomes that own
     distinct iterator lifecycles: candidate-cap early exit, tail/base
     completion, and a converged no-op cycle. Before the first path and after
     the third, sample once per second until three consecutive `numbackends`
     values are identical, with a 15-second deadline for each checkpoint.
     Promotion requires the final stable value to equal the warmed stable
     baseline. Failure to stabilize by either deadline or a different stable
     final value blocks promotion and investigation; do not add a tolerance.
     Capture intermediate dictionaries only when diagnosing a failed run. The
     probe is present at both checkpoints, so no subtraction is needed.
   - Benchmark 20 runs each of system-status mapping reduction, endpoint
     resolution, runtime-prune dry-run, and harness teardown reduction at `D`.
     Promotion requires p95 at or below 2 seconds and every run below 5 seconds.
     Also require the task publication path to issue zero mapping reads and one
     append attempt per registration. Record statements, wall time, connection
     baseline/final dictionaries, hardware, PostgreSQL version, pytest-pg
     provisioning command, and seed construction in the Execution Log so the
     evidence is repeatable.
   - If `R5_future` cannot be measured, cleanup has less than four-times margin,
     any reader misses the latency threshold, or eligible rows fail to converge,
     stop. Do not promote this delta. Revise and independently re-review a plan
     that adds owner-local coalescing/rate limiting or a bounded current-state
     read model; do not hide the failure by tuning retention or batch size.
   - Run the independent re-review below against this revised plan, the exact
     proposed delta, baseline `0f19367a`, reproduced head-starvation evidence,
     fixed-prefix harness evidence, and recorded capacity results.
   - Resolve every finding explicitly. A BLOCKED verdict prevents promotion.
   - Apply the exact Strategy-A spec text, add plan backlinks, and record the
     promotion baseline identifier.
   - Run the focused spec metadata/hygiene and backstitch comparison before
     code. Between promotion and implementation, accept only the expected
     unmapped-info debt from the new [OBS.6a] text; no missing-section or
     reciprocal-link warning may be introduced.
   - Stop if review concludes that mapping uniqueness is relied upon anywhere,
     that lifecycle event ordering must change, or that a SimpleBroker change
     is required. Revise and re-review the plan rather than expanding silently.

2. **Write the failure-first publication and lifecycle regressions.**
   - In `tests/tasks/test_task_observability.py`, replace
     `test_tid_mapping_deduplicates_identical_payloads` with a real-broker test
     proving that a second direct registration appends another complete row
     without invoking the task's mapping queue `peek_generator()`.
   - Make any attempted history read fail loudly at the test seam. Assert the
     append still occurs and both rows carry the canonical full/short TID,
     runner, runtime handle, role/name, hostname, and timestamp fields.
   - Add an activity test that performs `working`, repeated `working`, then
     `waiting`. Assert the repeated owner-local no-op creates nothing, while
     the two actual transitions append snapshots carrying the correct
     `activity` and `waiting_on` values.
   - Add a Consumer regression through `run_work_item()` or the nearest real
     production path. Make mapping-history iteration fail if called after task
     construction, execute one successful item, and assert result visibility,
     `work_started`, `work_completed`, the terminal envelope, and terminal
     TaskSpec state. Current baseline must fail before the recognized lifecycle
     sequence completes.
   - Add a separate mapping-write-failure case using the cached real queue
     handle with only `write()` faulted. Assert the task still attempts and
     publishes its lifecycle sequence. Do not mock the global log, TaskSpec
     state machine, result path, or Consumer lifecycle.
   - Record the exact red command and failure in this plan's Execution Log
     before implementation.

3. **Remove queue-wide deduplication from the shared writer.**
   - In `BaseTask._register_tid_mapping()`, retain payload construction, JSON
     serialization, cached queue acquisition, the single write attempt, and
     the narrow best-effort broker catch.
   - Delete the call to `_latest_tid_mapping()` and remove
     `_latest_tid_mapping()` plus `_tid_mapping_equivalent()` when repository
     search confirms they have no other caller.
   - Remove the then-unused `closing_queue_iterator` import. Replace the
     writer's stale `[MA-2]` docstring citation with the promoted [CC-2.2],
     [CC-2.5], [MF-5], [OBS.6], and [OBS.6a] references; do not leave a false
     implementation backlink.
   - Do not add local cache state. Existing call-site guards already suppress
     unchanged activity, runtime handles, and managed PIDs; direct registration
     is explicitly allowed to append an equivalent snapshot.
   - Add the reciprocal spec references to `_register_tid_mapping()` and the
     activity boundary. Keep the mapping queue on `BaseTask._queue()`.
   - Run the focused red tests green, then run the neighboring task lifecycle
     suite.
   - Stop if the implementation needs event reordering, a background writer,
     a keyed store, direct SQL, a new SimpleBroker API, or a second mapping
     representation.

4. **Make superseded cleanup match the promoted retention contract.**
   - Keep production latest-row reducers unchanged unless a firing regression
     proves a real incompatibility. The test harness is an explicit exception:
     its fixed 2,048-row prefix has already been proved incompatible with the
     planned depth and must be corrected in this slice.
   - First add a policy regression with two equivalent old rows for one TID and
     `exclude_tids` containing that TID. Assert the older row is selected as
     `superseded_tid_mapping`, the greatest message-ID row is protected, and
     progress does not falsely report a blocked/no-progress window.
   - Add a real-broker `run_task_monitor_cleanup()` integration regression that
     writes equivalent rows, supplies the same exclusion used by
     `TaskMonitor._run_task_monitor_cleanup_cycle()`, applies cleanup, and proves
     exact deletion of the old row plus survival of the newest row. If the
     nearest existing TaskMonitor integration fixture can cheaply exercise the
     actual self-TID call site, add that assertion there as well; the direct
     cleanup entry-point test is mandatory.
   - Add the independently reproduced head-starvation regression: use a batch
     of two, put two old protected distinct keys at the FIFO head, put an old
     equivalent duplicate pair behind them, run repeated real cleanup cycles,
     and prove the superseded tail row is reached and exactly deleted while all
     newest rows survive. Assert catch-up/base flags for the intermediate and
     converged cycles; the baseline's three zero-deletion cycles are the red
     evidence.
   - In `tid_mapping_candidates()`, run the FIFO age scan across every valid
     unclaimed mapping row without applying `exclude_tids` first. After merging
     full-queue newest-ID evidence, retain an excluded TID only when the age
     candidate is its greatest valid message-ID row. Superseded rows remain
     age-only. Keep newest non-excluded rows on the existing payload-liveness
     gate and leave malformed-row policy unchanged.
   - Keep this semantic local to the TID-mapping policy. Do not change generic
     `older_than_candidates()` or the task-log/reserved policy meaning of
     `exclude_tids`.
   - In `weft/core/monitor/cleanup.py`, replace the TID-mapping bounded-head
     candidate path with a memory-bounded two-pass public-generator scan. Pass
     one computes the greatest valid message ID per full TID across the full
     queue. Pass two scans FIFO, retains at most `batch_size` exact candidates,
     skips protected newest rows, applies age-only selection to superseded rows,
     and preserves malformed-row selection even beyond the first too-young
     valid row. Stop pass two at candidate capacity or queue tail; track the
     valid-row age boundary without allowing it to hide later malformed rows.
     Do not materialize the full row set. Reuse one queue handle when the public
     API permits and close both lazy iterators on every success/failure path.
   - Report a catch-up waypoint when candidate capacity is reached. Report base
     only after the scan proves there is no hidden eligible row through queue
     tail (or equivalent complete evidence under the existing age ordering).
     Never interpret a full protected prefix as base or forward progress.
   - Strengthen the existing TID-mapping policy test names/assertions so they
     state explicitly that byte- or semantic-equivalent older snapshots are
     superseded and the newest live snapshot remains protected.
   - Add a TaskMonitor diagnostic-refresh case if existing coverage does not
     already prove that health changes append the newest passive-status
     snapshot while the self-exclusion still permits its older snapshot to be
     retired.
   - In `tests/helpers/weft_harness.py`, remove `peek_many(limit=2048)` from
     mapping discovery. Reduce the full lazy generator by greatest broker
     message ID per full TID, retain IDs until the comparison is complete, and
     close iteration and queue handles on every path. Preserve the defensive
     best-effort harness boundary. In `tests/test_harness_registration.py`, put
     the live mapping after at least 2,048 decoys and prove teardown discovers
     the current owner without leaking or targeting the stale mapping.
   - Run focused status, endpoint, pruning, TaskMonitor cleanup, and test-harness
     coverage to prove no one-row-per-TID assumption exists.
   - Add no timing assertion to the normal suite. The structural assertion is
     stronger: publication invokes zero history reads for any queue depth.
   - For an operational performance probe, seed more than two SimpleBroker
     peek batches, execute the same registration/Consumer path, and capture
     broker statement counts or an equivalent recording seam. Expected: zero
     mapping `SELECT ... LIMIT/OFFSET` statements and one append attempt per
     actual registration. Keep this probe targeted or benchmark-marked so the
     default suite does not carry a large-row timing test.

5. **Close documentation, review, release, and deployment evidence.**
   - Add a changelog entry naming the eliminated transition-path replay, valid
     duplicate snapshots, unchanged wire shape, self-exclusion correction,
     protected-head reachability fix, and full harness reduction.
   - Add a durable lesson: never replay a shared append-only history on a
     per-task transition merely to avoid a harmless runtime-state append;
     owner-local guards plus latest-row reduction and retention are the proper
     boundaries.
   - Reconcile spec backlinks, plan links, implementation notes, and reciprocal
     `Spec:` references. Close every deviation row and run backstitch from the
     final state.
   - Run an independent completed-work review after the implementation slice
     and again before release if findings change the design.
   - Run all final gates below, commit in meaningful slices, wait for CI on the
     exact release commit, then use the normal Weft release helper. A release
     is invalid if any normal test gate was skipped.
   - After downstream mm-governance deployment, run the post-deploy checks
     below before declaring the incident path healthy.

## Testing Plan

Use `broker_env` and real `Queue` objects for mapping semantics. Use a real
`Consumer`/`task_factory` or `WeftTestHarness` path for lifecycle evidence. The
only mocked behavior should be the deliberately injected mapping iterator or
mapping append failure. Do not mock task logs, TaskSpec transitions, terminal
envelopes, result queues, the cleanup reducer, or newest-row selection.

Task 1's capacity work is pre-contract exploration, not production
implementation. Build the two-pass selector as a throwaway read-only benchmark
driver against an isolated PostgreSQL context or restored production-shaped
fixture; do not patch `weft/` before the evidence chooses the contract. Keep the
recorded benchmark command or promote it to a benchmark-marked test with the
implementation so the acceptance result is reproducible. Use
`get_connection_stats()` only for the PostgreSQL connection-stability evidence;
it does not become part of mapping publication or cleanup production code.

Failure-first coverage must straddle both sides of the boundary:

- history read attempted versus no history read;
- equivalent direct registration versus unchanged owner-local activity no-op;
- mapping iterator/append failure versus successful lifecycle evidence;
- superseded equivalent row versus newest live equivalent row, with and without
  the TaskMonitor self-TID exclusion;
- protected newest rows at the FIFO head versus eligible superseded rows beyond
  the candidate batch;
- current harness mapping after 2,048 decoys versus the removed fixed prefix;
- one-shot `work_started`/`work_completed` plus terminal result visibility.

Coverage map:

```text
registration request
  -> no history read -> one append attempt -> latest-row consumers
  -> append failure  -> lifecycle evidence still completes

aged mapping queue
  -> protected head -> eligible tail found -> bounded exact deletion
  -> excluded self  -> newest kept, superseded rows retire

isolated PostgreSQL benchmark
  -> persistent Queue -> get_connection_stats() named dict
  -> stable warmed baseline -> three cleanup outcomes -> stable final restored
```

The PostgreSQL run is required because the incident manifested through paged
PostgreSQL reads and connection pressure. `bin/pytest-pg` provisions the test
server and injects its DSN and backend settings into the pytest child
environment, so a developer-supplied `WEFT_PG_TEST_DSN` is not a prerequisite.
Failure of that wrapper or its PostgreSQL fixture remains a release blocker; it
is not grounds to skip the shared focused regression.

## Verification and Gates

Load the Weft environment before every command:

```bash
set -a; . ./.envrc; set +a
```

Per-slice gates:

```bash
./.venv/bin/python -m pytest tests/tasks/test_task_observability.py -k 'tid_mapping or activity' -q
./.venv/bin/python -m pytest tests/tasks/test_task_execution.py tests/tasks/test_consumer_terminal_events.py -k 'mapping or work_started or work_completed or terminal' -q
./.venv/bin/python -m pytest tests/core/monitor/policies/test_tid_mapping.py tests/core/test_task_monitor_cleanup.py -q
./.venv/bin/python -m pytest tests/test_harness_registration.py -k 'tid_mapping or mapping' -q
./.venv/bin/python -m pytest tests/commands/test_status.py tests/tasks/test_task_endpoints.py -q
./.venv/bin/python bin/pytest-pg tests/tasks/test_task_observability.py tests/core/monitor/policies/test_tid_mapping.py tests/core/test_task_monitor_cleanup.py tests/test_harness_registration.py
PYTEST_XDIST_AUTO_NUM_WORKERS=1 ./.venv/bin/python bin/pytest-pg tests/core/test_task_monitor_cleanup.py::test_tid_mapping_cleanup_connection_count_returns_to_baseline
```

Final gates, sequential after the diff is stable:

```bash
./.venv/bin/ruff format --check .
./.venv/bin/ruff check .
./.venv/bin/mypy weft bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py tests/architecture/test_import_boundaries.py -q
./.venv/bin/python -m pytest
./.venv/bin/python -m pytest -m ""
./.venv/bin/python bin/pytest-pg --all
bin/check-dom15-fixtures
bin/check-doc-paths
git diff --check
../backstitch/.venv/bin/backstitch check --repo-root . --no-config --spec-root docs/specifications --plan-root docs/plans --code-root weft --code-root tests --code-root bin --code-root integrations --code-root extensions --format json --output /tmp/weft-bounded-tid-mapping-backstitch-after.json
```

Capture the same backstitch command before promotion to
`/tmp/weft-bounded-tid-mapping-backstitch-before.json`. Repository-wide
historical findings may remain; acceptance is no new error or warning keyed to
the touched specs, plan, code, or tests. `bin/check-doc-paths` is advisory under
current guidance, so report its output rather than rewriting unrelated paths.

Release and post-deploy evidence:

- CI must pass on the exact release commit before publication.
- From a clean Weft worktree on the exact green commit, run
  `uv run python bin/release.py` (or `--version X.Y.Z` only if the current
  version is already published). Do not use `--retag` unless the maintainer has
  explicitly chosen to move an unpublished remote tag, and do not use a
  shortcut that omits tests.
- Wait for the tag-triggered release gate and verify the GitHub Release and PyPI
  artifact resolve to the tested commit before changing the downstream pin.
- In mm-governance, update the Weft dependency forward to that published
  version, run its required Ruff, mypy, and test gates, commit in meaningful
  slices, run `inv release`, wait for CI on the exact release commit, then run
  `fab deploy`. Do not add a backwards-compatible Weft branch or repin the old
  version as the normal rollback path.
- Confirm the deployed Weft version in mm-governance before testing behavior.
- Inspect TaskMonitor PING/cached diagnostics and require a successful current
  cleanup cycle with `runtime_state.retention` progress.
- Record mapping queue total, distinct full TIDs, superseded rows, and oldest
  row before the canary and after at least two cleanup cycles.
- For the active TaskMonitor's own full TID, require its newest mapping row to
  remain and every older row past minimum age to become eligible and retire;
  this is the direct regression for the exclusion-order defect.
- Submit representative one-shot and persistent work. Measure the durable
  gaps from spawning activity to `work_started`, and from completed activity
  to `work_completed`; require recognized terminal events and readable results
  for every canary.
- During the canary, inspect PostgreSQL statements or activity and verify that
  task mapping publication does not issue repeated paged
  `LIMIT/OFFSET` reads of `weft.state.tid_mappings`.
- Observe server connection pressure separately by taking full
  `get_connection_stats()` snapshots through one existing persistent Queue
  before the canary and after final quiescence. Compare raw `numbackends`
  without subtracting the probe. This is diagnostic evidence, not an exact
  task-owned count. The change does not claim to repair the independent
  unthrottled-enrichment connection fan-out.

## Independent Review Loop

Use a different model family from the author when available. If only the same
family is available, record that limitation. Give the reviewer this plan, the
three governing specs at baseline `0f19367a`, `weft/core/tasks/base.py`,
`weft/core/tasks/consumer.py`, the current mapping cleanup policy, the existing
writer/cleanup tests, and the SimpleBroker generator implementation.

Review prompt:

> Read `docs/plans/2026-08-25-bounded-tid-mapping-publication-plan.md`, its
> exact Proposed Spec Delta and Strategy-A promotion order, baseline
> `0f19367a`, and the cited code/tests. Do not implement. First try to disprove
> the premise that read-before-write deduplication is unnecessary. Identify any
> producer, reader, liveness check, or cleanup path that requires one row per
> TID or requires the writer to know the previous payload. Then look for bad
> ideas, latent ambiguity, missing failure boundaries, and performative work.
> In particular, challenge duplicate growth for persistent tasks, TaskMonitor
> health-change publication, protected-head/tail-candidate reachability,
> self-TID exclusion, cleanup catch-up and progress flags, fixed-depth readers,
> the measured `R5_future` capacity envelope, lifecycle-event ordering, the
> forward-only fallback under deep history, and whether any SimpleBroker change
> is actually required. Answer PASS or BLOCKED based on whether a zero-context
> engineer can implement the plan confidently and whether the result would
> avoid degrading correctness or robustness.

Every finding receives an explicit accepted/rejected/out-of-scope disposition.
If a finding changes invariants, ownership, or blast radius, revise the plan and
rerun review against the revision delta before spec promotion.

The reviewer must also challenge necessity. Identify any process gate,
benchmark, test, compatibility step, or abstraction that can be removed without
weakening a stated correctness or operational outcome. Flag tests that assert
private mechanics, duplicate SimpleBroker coverage, or produce evidence that
does not correlate with publication latency, cleanup convergence, newest-row
safety, lifecycle completion, or connection stability.

## Review Log

| Date | Reviewer | Scope | Verdict | Findings and disposition |
|---|---|---|---|---|
| 2026-08-25 | implementation-shape audit, independently reproduced by author | producer, consumer, and cleanup blast radius | BLOCKED on draft v1; resolved in v2 | Accepted P1: `exclude_tids` was applied before newest/superseded classification, so append-on-change would leak every old row for the live TaskMonitor's own TID. The plan now changes the policy ordering, adds exact [MF-5]/[OBS.13.7] wording, and requires policy plus real-cleanup regressions. |
| 2026-08-25 | spec-contract audit, independently checked by author | governing sections and writer boundary | advisory findings resolved | Accepted: add [CC-2.5], remove the stale `[MA-2]` writer citation and unused iterator helper import, and test TaskMonitor diagnostic snapshots. Rejected one implementation suggestion to serialize inside the broker catch because `docs/lessons.md` requires construction/serialization defects to remain visible; only broker append failures are best effort. |
| 2026-08-25 | author | fresh-eyes over draft v2 | PASS; external review still required | The first draft's cleanup assumption was unsafe and is now corrected. No remaining ambiguity was found in spec-promotion order, failure priority, test realism, or local implementation shape. Residual risk is increased append volume against still-global readers and a full-queue cleanup reduction; rollout therefore gates on cleanup convergence and statement/depth evidence rather than claiming those separate global reads are removed. |
| 2026-08-25 | Claude Opus, read-only different-family attempt | full plan and cited sources | no verdict | The process remained alive but returned no output in ten minutes and was stopped. No review claim is derived from this attempt. |
| 2026-08-25 | independent reviewer (same family after different-family timeout) | draft v2, proposed spec delta, code/tests, and installed SimpleBroker | BLOCKED | Accepted and reproduced P1: protected head rows starve eligible tail duplicates. Accepted P1: the plan lacked a measured append/cleanup/global-reader capacity envelope. Accepted P1: baseline-writer restoration was not a safe rollback. Accepted P2: `WeftTestHarness` reads only the first 2,048 mappings. Draft v3 adds two-pass reachability, explicit pre-promotion capacity thresholds, a forward-only fallback, and full harness reduction. |
| 2026-08-25 | same independent reviewer | round-2 accepted-findings verification | FAIL on F2 only | F1 cleanup reachability, F3 forward fallback, and F4 harness reduction passed. Accepted new F2 defect: current deduplicated append rate can be near zero while the new writer appends two activity snapshots per persistent item. Task 1 now measures `R5_future` at registration entry after local guards and covers every registration class plus representative bursts. |
| 2026-08-25 | same independent reviewer | round-3 F2 verification over draft v3 | PASS | Verified that `R5_future` is measured at registration entry after owner-local guards and before global dedup, covers every registration class plus representative scheduled and persistent bursts, drives depth and cleanup thresholds, and blocks promotion if incomplete. No new defect found. Plan review is complete; Task 1 runtime evidence remains a promotion gate. |
| 2026-08-26 | independent API/simplification reviewer | released helper, pytest-pg lifecycle, connection-stability gate, and process necessity | BLOCKED, then PASS after accepted P2 reductions | Corrected the mandatory four-key result and pytest-child DSN wording; replaced vague sampling with bounded stable baseline/final sampling; then removed the arbitrary 20-cycle/per-cycle protocol in favor of candidate-cap early exit, tail/base completion, and converged no-op. Confirmed no production admission-control coupling and no remaining P0-P2 finding. |

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Execution Log

Record only durable completed evidence here: failure-first command/result,
promotion identifier, implementation slices, focused and full gate results,
review dispositions, commit SHAs, CI result, release version, and post-deploy
observations. Do not record transient staging or worktree claims.

- 2026-08-25 planning evidence: the four-row SQLite reproduction ran cleanup
  three times with `batch_size=2`; every cycle selected/deleted zero and left
  the eligible superseded tail row in place. Draft-v3 plan metadata/spec hygiene
  passed 8 tests; `check-dom15-fixtures`, `check-doc-paths`, and
  `git diff --check` passed. Backstitch reported the repository's existing 27
  errors, 988 warnings, and 572 infos with no issue keyed to this plan. The
  independent review sequence was BLOCKED (four accepted findings), FAIL on
  the first capacity correction, then PASS after `R5_future` replaced the
  current deduplicated output rate.
- 2026-08-26 API amendment evidence: installed SimpleBroker 7.5.1 and
  `simplebroker-pg` 3.10.0 expose the package-root four-key dictionary helper;
  `bin/pytest-pg` owns PostgreSQL provisioning and child-environment injection.
  Plan metadata/spec hygiene, DOM-15 fixtures, document paths, backstitch, and
  `git diff --check` passed. Independent necessity review passed after the
  three-outcome connection-stability fixture replaced arbitrary cycle counts.
- 2026-08-26 read-only production gate: the live TID-mapping queue contained
  78,244 rows for 35,213 distinct full TIDs. Of those rows, 78,186 were older
  than 2,400 seconds and 58 were younger; observed output was 95 rows in the
  last hour, 2,863 in 24 hours, 21,553 in seven days, and a peak deduplicated
  five-minute bucket of 121 rows (0.403 rows/second). The task-log queue depth
  was 185,196. The bounded task-log aggregate was stopped before it produced a
  usable result. This evidence cannot establish `R5_future`: passive history
  cannot observe registration attempts suppressed by the current global
  comparison, and `ServiceTask` mapping changes do not all have a matching
  `task_activity` row. Production instrumentation/restart or an authorized
  production-shaped replay is required. Per Task 1, spec promotion and
  implementation remain blocked; no bounded-TID production code or spec was
  changed.

## Out of Scope

- Replacing global status, endpoint, pruning, or cleanup reductions with a
  keyed materialized view. Those operations intentionally build global current
  state and need separate evidence before their consistency model changes.
- Removing the full-queue newest-ID pass from TaskMonitor cleanup. [OBS.13.7]
  currently requires that evidence when the bounded head window is truncated;
  changing it safely is a separate retention-policy design.
- Changing generic `older_than_candidates()` exclusion semantics. Only the
  TID-mapping policy applies exclusion after newest/superseded classification;
  task-log and reserved cleanup keep their current whole-TID exclusion rules.
- Changing the TID mapping payload shape, renaming `started`, adding a schema
  version, or migrating existing rows.
- Adding a new SimpleBroker query, reverse iterator, JSON-field index, keyed
  queue primitive, or backend SQL in Weft.
- Changing task-log terminal-event classification, Manager terminal proof,
  monitor collation, or lifecycle event ordering.
- Treating this change as the fix for PostgreSQL connection fan-out. Connection
  concurrency remains a separate incident. Manager-side mitigation belongs to
  `docs/plans/2026-08-25-manager-admission-control-plan.md`; this plan uses the
  released helper only for benchmark evidence.
- Manual production deletion or emergency compaction of existing mapping rows.

## Fresh-Eyes Review

Author verdict: PASS on draft v3. Independent plan verdict: PASS. Spec promotion
and implementation remain blocked on Task 1's live-rate and PostgreSQL capacity
evidence.

Findings, ordered by severity:

1. P1, accepted and fixed: the first draft assumed existing cleanup would
   retire every superseded row. In fact, `tid_mapping_candidates()` applied the
   active TaskMonitor's self-TID exclusion before classifying newest versus
   superseded rows, so every self mapping would survive. The plan now includes
   the production policy correction, exact governing wording, and red policy
   plus integration coverage.
2. P2, accepted and fixed: the first draft did not cite [CC-2.5], did not name
   the stale `[MA-2]` implementation mapping, and did not explicitly remove the
   iterator-closing import left dead after deleting `_latest_tid_mapping()`.
3. P2, rejected after checking the durable exception-boundary lesson: one audit
   suggested placing JSON serialization inside the best-effort catch. The plan
   keeps payload construction and serialization outside it so programming and
   schema defects surface; only the broker append is nonfatal.
4. P1, accepted and independently reproduced: a bounded head containing only
   protected newest rows selected zero candidates for three consecutive cycles
   while an eligible duplicate pair remained behind it. Draft v3 adds the
   two-pass, candidate-bounded cleanup path and a firing convergence regression.
5. P1, accepted: acknowledging capacity as a residual risk was insufficient.
   Draft v3 measures future registration-input `R5_future`, then makes a
   production-shaped PostgreSQL benchmark, four-times cleanup-rate margin, and
   explicit two-/five-second reader thresholds hard gates before spec
   promotion.
6. P1, accepted: restoring the baseline writer is not rollback because it
   restores the incident. Draft v3 declares publication start an operational
   one-way threshold and defines a forward fallback that keeps the no-read
   writer while suppressing activity-triggered mapping refresh.
7. P2, accepted: `WeftTestHarness` silently limits mapping discovery to 2,048
   rows. Draft v3 adds full greatest-message-ID reduction and a beyond-limit
   teardown regression.

The remaining risk is capacity, not contract ambiguity: direct appends increase
the retained age-window floor and global current-state/cleanup readers still
reduce shared history. The plan makes that limitation explicit, gates rollout
on cleanup convergence, and keeps a separate cleanup/read-model redesign out of
this change. The v2 reviewer showed that the original gate was not specific
enough and also found protected-head starvation plus a fixed-prefix harness
reader. Draft v3 now requires measured `R5_future` at the pre-dedup registration
boundary, a doubled age-window target, four-times cleanup margin, explicit
reader latency limits, a memory-bounded
two-pass cleanup scan, and full harness reduction before promotion. The only
available different-family review attempt timed out without a verdict, so the
recorded external findings came from a same-family independent context and that
limitation remains explicit.

The 2026-08-26 API amendment also passes independent review. It uses the
released helper only for a single-worker baseline/final connection-stability
check across candidate-cap early exit, tail/base completion, and converged
no-op. It adds no production connection guard, raw SQL, sidecar connection, or
field-order dependency to this plan.

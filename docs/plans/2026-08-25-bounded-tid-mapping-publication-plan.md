# Bounded TID Mapping Publication and Retention Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.2], [CC-2.5]; docs/specifications/05-Message_Flow_and_State.md [MF-5], Cleanup Boundary; docs/specifications/07-System_Invariants.md [OBS.6], [OBS.13.7]
Superseded by: none

Relationship to Manager admission control: the implemented admission feature
(`docs/plans/2026-08-25-manager-admission-control-plan.md`, [MA-1.8]) consumes
the latest-per-TID mapping reduction plus the shared `mapping_row_is_live`
probe on every enabled SQLite admission decision. It did not change mapping
publication or retention, and this plan is not an admission-control gate — but
this plan's cleanup corrections bound that observer's scan depth for every
runtime with a registered liveness probe (genuinely undecidable newest rows
stay protected and retained), so the dependency runs from admission's
operational cost to this plan's fixes, not the other way. The admission-side
counting correction for non-host runtimes is owned by the admission plan.

Hard dependency gate: SATISFIED on 2026-08-27. The admission correction
slice is committed as `0bf1ddd` ("Correct admission liveness, convergence,
and fail-closed observation") with its forced terminal-publication tests
green (full suite 4,304 passed / 5 skipped; PG admission suite green). The
`terminal` contract this plan's comparator and cleanup work build on is now
in the baseline. Both plans touch the payload builder, comparator, shared
probe, specs, and tests — this plan's work proceeds on top of `0bf1ddd`.

Class: 5 - spec-changing and risky. This changes the normative runtime-state
publication contract on the durable task spine and corrects the cleanup
exclusion policy needed to retire the resulting superseded rows. The Class 4
hardening checklist and independent review before implementation are mandatory.

Review state: implementation review is active. Earlier capacity formulas,
fixed throughput thresholds, repeated-sampling protocols, and work-item-rate
estimates were planning experiments and are not release requirements or
product contracts. They remain only in the dated review history below. Durable
gates cover correctness: publication does not read history, newest rows remain
protected, eligible superseded rows remain reachable, destructive cleanup
fails closed when evidence is unavailable, and queue iterators are closed.
Performance measurements may diagnose a concrete failure but do not establish
a permanent threshold unless a governing specification first defines one.

## Goal

Remove the unbounded read-before-write replay of
`weft.state.tid_mappings` from task initialization, activity, runtime-handle,
PID, and terminal transitions, restoring the original edge-triggered
publication model: every write is a new fact — a transition or a report — so
the writer performs one append with no queue read and no payload comparison.
Edge detection lives at the call sites (unchanged activity, equal handles,
and known PIDs are suppressed where the change is observed), and the
terminal transition publishes exactly once per task, latched only on a
successful write. Equivalent rows remain valid whenever they appear. Existing consumers
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

- [x] `BaseTask` performs no mapping-history read as part of TID mapping
  publication.
- [x] Semantically equivalent complete mapping snapshots may be appended.
- [x] Actual activity changes publish current `activity` and `waiting_on`
  fields instead of being suppressed by a comparator that ignores them.
- [x] A mapping iterator failure is no longer reachable from task startup or
  terminal publication.
- [x] A mapping append failure remains best effort and does not replace or
  abort task-owned lifecycle publication.
- [x] Latest-row status, endpoint, runtime-pruning, Manager admission, and
  cleanup consumers remain correct with equivalent consecutive rows.
- [x] Superseded-row cleanup remains safe and is proven against equivalent
  snapshots; the newest row per full TID keeps the current liveness gate, and
  a TaskMonitor self-exclusion cannot exempt its superseded history.
- [x] Cleanup skips protected head rows, selects at most one configured batch
  of eligible exact IDs across the aged queue prefix, and converges under
  repeated cycles without unbounded candidate memory.
- [x] Cleanup correctness is covered on SQLite and PostgreSQL without
  hard-coded throughput, timing, queue-depth, or repeated-sampling gates.
- [x] The change ships without a new queue, payload version, persisted table,
  dependency, SimpleBroker API, compatibility reader, or data migration.
- [x] SQLite and PostgreSQL correctness suites, lint, format, type, and spec
  hygiene gates are rerun from the final implementation state.

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
  boundary: Weft continues using ordinary queue writes and strict
  generator-based history readers for destructive cleanup. This plan does not
  change SimpleBroker or add a production connection guard.
- `docs/specifications/03-Manager_Architecture.md` [MA-1.8] and
  `docs/specifications/07-System_Invariants.md` [MANAGER.18] govern the
  implemented Manager admission observer, a consulted read-only consumer of
  the mapping reduction and shared liveness probe. This plan must not change
  its counting semantics; it changes only how fast the queue that observer
  scans converges.
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
  every row in the shared queue to find the newest row for one full TID. The
  admission correction slice wrapped generator iteration and the write inside
  the best-effort boundary, so paging failures no longer abort lifecycle
  publication — but the unbounded read-before-write replay and its
  history-depth cost remain, and are this plan's target. The correction also
  added the `terminal` field to the payload, the comparator, and the shared
  probe.
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
- `weft/core/manager.py::Manager._observe_admission_usage()` (opt-in, since
  commit `a659a48`) reduces the same queue per enabled SQLite admission
  decision, filters latest rows through the shared
  `weft/core/monitor/policies/tid_mapping.py::mapping_row_is_live` probe with
  a memoized verdict per TID, and unions in-flight launches plus committed
  children (per the admission plan's in-flight correction, which also adds the
  task-owned `terminal: true` mapping bit that releases completed external
  runners under the same payload-only probe). Its scan cost is proportional to
  total retained rows, and its reduction size to distinct TIDs whose newest
  rows have not retired — so the head-starvation and self-exclusion defects
  below directly inflate admission-decision cost, and the append-on-change
  writer will add rows between cleanup cycles. The probe now has three
  consumers (cleanup policy, endpoint resolution, admission); its semantics
  must stay shared, and this plan's comparator work must keep the `terminal`
  field in equivalence. The admission correction's slice lands first; this
  plan builds on that state.
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
- `weft/commands/system.py`, `weft/core/endpoints.py`,
  `weft/core/pruning/runtime.py`, and
  `weft/core/manager.py::_observe_admission_usage()` /
  `_admission_mapping_is_live()`: current latest-row consumers.
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
- The writer holds no payload memory and performs no equivalence
  comparison: writer-side oracles are forbidden by [OBS.6a] because a
  curated comparator field list silently suppresses newly added payload
  fields (the recorded drift history proves it twice) and because a report
  of identical values is still a new observation. The only writer-adjacent
  state is the one-bit terminal-published flag, latched only on a successful
  terminal append so the task's final liveness evidence retries across
  terminal reports until written.
- A direct `_register_tid_mapping()` call always appends a complete valid
  snapshot, equivalent or not. Duplicate avoidance is exclusively the call
  sites' edge detection; no consumer may rely on uniqueness.
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

- Authoring baseline `0f19367a5781ead326f632853b3c49bc07e9623d`; advanced on
  2026-08-26 to `a659a481794e460c1430d3038ebe23e43f61964f` (Manager admission
  control plus SimpleBroker 7.5.1 adoption; clean worktree). The governing
  specs now carry the [MA-1.8]/[MANAGER.18]/[MF-6] admission text, which this
  plan reads but does not modify. Review Log rows citing `0f19367a` are
  historical.
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
> Owner-local state guards — including the process-local last-written-payload
> guard, which compares `activity`, `waiting_on`, and `terminal` and updates
> only after a successful write — may suppress a registration request before
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
> weaken the newest row's liveness gate. A cleanup-cycle exclusion for
> a TID protects only that TID's greatest valid message-ID mapping row; it does
> not exempt superseded mapping rows from age-only retention.
>
> The newest-row liveness gate remains the shared payload-only
> `mapping_row_is_live` policy as amended by the admission plan's correction:
> positive scoped host-process liveness protects the row; absent that, a valid
> latest row with `terminal: true` probes dead and becomes age-eligible; other
> undecidable evidence stays protected. This plan adds no second gate and no
> registered-probe destruction rule. Crashed external runners that never
> published a terminal marker therefore remain protected and retained; their
> accumulation is bounded only by crash rate and is watched by the
> distinct-TID-growth canary stop condition.

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
- The immediate operator action for an overloaded Manager needs no release:
  set `WEFT_ADMISSION_MAX_CONNECTIONS=0` (or unset it) and restart the
  Manager, which disables the admission observer and its mapping scans
  entirely; dispatch reverts to pre-admission behavior. This is authorized
  interim mitigation, already covered by a firing disabled-admission test.
- Release rollback is forward-only. If the added activity write volume is
  unsafe, release a pre-built, pre-validated patch that preserves the
  one-attempt/no-read writer and removes mapping refresh from
  `_set_activity()` while leaving `task_activity` lifecycle diagnostics
  intact. Build and test that fallback branch during Task 5, before the
  canary, so selecting it is a deploy, not a development effort. Constructor, runtime-handle, managed-PID, and
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
- The canary must span at least one full 2,400-second minimum-age window plus
  cleanup convergence, so steady-state retention is actually observed; a
  two-cycle snapshot cannot see the retention floor.
- Stop rollout if transition latency still grows with mapping depth, if mapping
  depth grows monotonically across healthy cleanup cycles, if the crash-residue
  cohort grows past its threshold (see below), if a current-state
  consumer selects anything other than the newest valid row, if
  admission-enabled Manager decision latency degrades beyond its recorded
  canary envelope, or if terminal event/outbox evidence regresses.
- The crash-residue cohort is the specific unbounded class: newest rows older
  than the 2,400-second minimum age whose payload is non-terminal and
  liveness-undecidable (no live scoped host process, no `terminal: true`, no
  probeable evidence). Measure it once per 2,400-second window during the
  canary. Threshold: any net cohort growth across two consecutive windows, or
  an absolute cohort above 1% of distinct retained TIDs, stops rollout.
  Total-distinct-TID growth alone is not a signal — normal workload grows it
  while cleanup is healthy, and unrelated retirement can mask residue.

## Tasks

1. **Close the correctness and review gates before promoting the contract.**
   - Prove with firing tests that publication performs no history read and a
     mapping append failure cannot abort task lifecycle publication.
   - Prove with real broker rows that newest valid mappings survive cleanup,
     protected head rows do not hide eligible tail rows, exclusions protect
     only the newest row, and missing newest-row evidence fails closed before
     any destructive apply.
   - Exercise the same correctness cases on PostgreSQL through the normal
     `bin/pytest-pg` release run. Iterator ownership must be verified by normal
     completion, candidate-cap exit, and scan failure paths, without timing or
     connection-count thresholds.
   - Treat production-depth scans and local load measurements as diagnostic
     observations only. Do not derive a rate, fixed depth, timeout, iteration
     count, or permanent release gate from them.
   - Run the independent re-review below against this revised plan, the exact
     proposed delta, baseline `a659a481794e460c1430d3038ebe23e43f61964f`,
     reproduced head-starvation evidence, and fixed-prefix harness evidence.
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
     `test_tid_mapping_deduplicates_identical_payloads` with real-broker tests
     proving that registration never invokes the task's mapping queue
     `peek_generator()`: a direct re-registration appends a valid equivalent
     row (no writer-side oracle); call-site edge detection publishes actual
     activity transitions with their `activity`/`waiting_on` values while
     no-op repeats publish nothing; the terminal transition publishes exactly
     once across repeated terminal reports; and a faulted terminal append
     does not latch the once-published flag, so the next terminal report
     retries.
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
   - Delete the call to `_latest_tid_mapping()` and remove both
     `_latest_tid_mapping()` and `_tid_mapping_equivalent()` when repository
     search confirms they have no other caller: the edge-triggered writer
     keeps no payload memory and performs no comparison. Replace the
     admission correction's level-triggered terminal force with the
     edge-triggered equivalent: publish on the terminal transition exactly
     once, latching the once-published flag only on a successful write so the
     external-runner release row — the task's final liveness evidence —
     retries across terminal reports until written. Rerun the admission
     plan's forced terminal-publication regression and keep it green.
   - Remove the then-unused `closing_queue_iterator` import. Replace the
     writer's stale `[MA-2]` docstring citation with the promoted [CC-2.2],
     [CC-2.5], [MF-5], [OBS.6], and [OBS.6a] references; do not leave a false
     implementation backlink.
   - Add only the one-bit terminal-published flag described in the
     invariants; latch it solely on a successful terminal append. No payload
     cache, comparator, index, or shared state.
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
     candidate path with a candidate-bounded two-pass public-generator scan
     (candidate memory bounded by `batch_size`; pass one retains one message ID
     per distinct TID, so working memory is `O(distinct TIDs)` — record peak
     RSS at depth `D` in the benchmark evidence). Pass
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
   - The large-depth statement-count probe is owned by Task 1's benchmark
     evidence; do not duplicate it here. The normal suite's structural
     assertion (zero history reads at any depth) is the durable regression.

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
- guard-suppressed equivalent registration versus the appends the guard must
  never suppress (empty cache, post-failure retry, terminal-only change);
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

isolated SQLite admission benchmark
  -> seeded depth D -> spawn burst -> repeated enabled decisions
  -> cold memo then warmed memo -> per-decision latency within thresholds
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
  artifact resolve to the tested commit.
- Post-publish smoke checks cover only observable correctness: representative
  one-shot and persistent work reaches terminal state with readable results,
  and cleanup preserves the newest live mapping while retiring eligible
  superseded rows. Tests, not this temporary release checklist, own those
  contracts long term.

## Independent Review Loop

Use a different model family from the author when available. If only the same
family is available, record that limitation. Give the reviewer this plan, the
governing specs at baseline `a659a48`, `weft/core/tasks/base.py`,
`weft/core/tasks/consumer.py`, the current mapping cleanup policy, the existing
writer/cleanup tests, and the SimpleBroker generator implementation.

Review prompt:

> Read `docs/plans/2026-08-25-bounded-tid-mapping-publication-plan.md`, its
> exact Proposed Spec Delta and Strategy-A promotion order, baseline
> `a659a48` plus the committed admission terminal-bit correction it depends
> on, and the cited code/tests. Do not implement. First try to disprove
> the premise that read-before-write deduplication is unnecessary. Identify any
> producer, reader, liveness check, or cleanup path that requires one row per
> TID or requires the writer to know the previous payload. Then look for bad
> ideas, latent ambiguity, missing failure boundaries, and performative work.
> In particular, challenge duplicate growth for persistent tasks, TaskMonitor
> health-change publication, protected-head/tail-candidate reachability,
> self-TID exclusion, cleanup catch-up and progress flags, fixed-depth readers,
> fail-closed behavior when either cleanup scan cannot produce evidence,
> whether any timing or capacity check is being reified without a product
> contract, the terminal once-published flag's
> retry semantics, lifecycle-event ordering, the
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

Entries below are a historical record. Recommendations later discarded by the
owner are not active gates and must not be promoted into tests or process.

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
| 2026-08-26 | owner | capacity-gate simplification | accepted direction; re-review pending | `R5_future` discarded as overly complex: it required production instrumentation or an authorized replay that the read-only gate proved unavailable. Replaced by fixed margins over recorded production evidence (benchmark depth `>= 2x` recorded rows, cleanup rate `>= 10x` recorded peak output) with live-rate risk owned by canary cleanup-progress stop conditions and the forward-only fallback. Rounds 2-3 above remain the historical record of the superseded measurement design. |
| 2026-08-26 | Codex (different-family independent reviewer) | reactivated draft, full plan + cited code at `a659a48` | BLOCKED | Accepted P1: 10x-over-deduplicated-rate margin bounds nothing — replaced with an analytical append bound enabled by the adopted owner-local last-written guard, and the canary must span one minimum-age window. Accepted P1: the admission observation sat in the PostgreSQL benchmark where the observer reads `numbackends` — moved to an isolated SQLite benchmark at depth `D`. Accepted P1 (owner escalated to starvation): cleanup retains undecidable newest rows forever, so non-host runtimes ratchet admission usage — resolved by aligning with the admission plan's task-owned `terminal: true` mapping bit (one payload-only rule releasing completed external runners for both counting and age-gated retirement); a registered-probe destruction gate was considered and rejected as a second liveness policy; crash residue stays protected and is watched by the new distinct-TID-growth stop condition; the header claim is qualified. Accepted P1: named `WEFT_ADMISSION_MAX_CONNECTIONS=0` + restart as the authorized no-release interim action and required the fallback branch pre-built. Accepted P1: stale `0f19367a` re-review references advanced to `a659a48`. Accepted P2s: `O(distinct TIDs)` wording with peak-RSS evidence; owner-local last-written guard adopted; redundant default-pytest and duplicate statement probe removed. |
| 2026-08-26 | Codex (same session, resumed) | re-review of the disposition revision | BLOCKED; residuals accepted | Verified FIXED: SQLite admission benchmark, fallback authorization, memory wording, last-written guard, redundant-gate removal. Accepted residual P1s: the analytical bound now enumerates every producer class with per-class maxima and feeds a retention-window floor into `D`; the canary stop condition now measures the aged non-terminal undecidable cohort with a numeric threshold and window instead of total distinct TIDs; the embedded review prompt now cites `a659a48` plus the admission correction and the current capacity design. Accepted new P1s: `terminal` is explicitly required in the repurposed comparator with a terminal-only-change red test and a rerun of the admission plan's forced terminal-publication regression; a hard dependency gate blocks promotion/implementation until the admission correction slice's commit SHA is recorded with green terminal tests. Accepted new P2: coverage map gains the SQLite burst benchmark. |
| 2026-08-26 | Codex (same session, third pass) | verification of residual fixes and consistency sweep | BLOCKED; three items accepted | Verified FIXED: crash-residue cohort stop condition, corrected review prompt, terminal comparator seam with regressions, hard cross-plan dependency gate, SQLite benchmark coverage. Accepted P1: producer maxima must be derived from the actual `_register_tid_mapping()` call sites (managed-PID registrations can recur per work item; TaskMonitor publishes more than once per interval) — the plan now requires a reviewed call-site-derived table before `analytical_rate` or `D` is computed, replacing asserted constants. Accepted P2s: the Goal states the guard is the only *additional* registration-level suppression alongside the retained call-site guards, and the guard invariant now names `terminal` with the other compared fields. |
| 2026-08-27 | owner (first-principles review during implementation) | writer publication model | accepted; design reverted to original | The owner-local last-written guard adopted from codex P2-7 was identified as the same oracle pattern as the removed queue-wide dedup, one layer cheaper: a curated comparator field list that must track the payload by hand (the recorded drift history shows it eating `activity`/`waiting_on` for months and nearly eating `terminal`), and semantically wrong for reports, where identical values are still new observations. Reverted to the original edge-triggered design: writes happen because something happened; call sites own edge detection; the writer is a bare append; the terminal transition publishes once, latched only on successful write (stronger delivery for the final liveness row than the guard's incidental retry). Append volume is unchanged because call sites fire on changes either way. Archaeology: the original writer (`4adcf72`, 2025-10-22) was a pure append; the dedup oracle entered in `8a0bb44` (2025-11-06, "Phase 1 basically complete") without spec backing; `_set_activity` and the activity payload fields arrived five months later in `f1baa76` (2026-04-13) and were never added to the comparator — the predicted failure mode, shipped. |

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
- 2026-08-26 reactivation: supersession by the admission plan removed. The
  implemented admission observer (commit `a659a48`) is recorded as a third
  consumer of the mapping reduction and shared liveness probe, added to the
  reader benchmark set and rollout stop conditions; the baseline advanced to
  `a659a481794e460c1430d3038ebe23e43f61964f`. At the recorded production depth
  (78,244 rows / 35,213 distinct TIDs), an enabled admission decision scans
  the full retained history, which strengthens this plan's motivation: cleanup
  reachability and self-exclusion fixes now bound a live Manager decision
  path, not only global status readers. Independent re-review of this
  revision delta is pending.
- 2026-08-28 owner correction: the work-item-rate model, fixed benchmark depth,
  throughput minimum, reader-latency thresholds, and repeated connection
  sampling are discarded planning experiments. They are not product policy or
  release gates. The production scan remains incident evidence only; durable
  gates cover observable correctness.
- 2026-08-26 codex review: independent different-family review (Codex CLI,
  session `01a04127`, 1.5M tokens) returned BLOCKED with five P1 and three P2
  findings; every finding dispositioned in the Review Log and incorporated in
  this revision. The owner escalated the undecidable-row finding to a
  concrete starvation defect for mixed docker+host Manager deployments; the
  fix is the admission plan's task-owned `terminal: true` mapping bit, which
  this plan aligns with rather than adding a registered-probe destruction
  gate; crash residue is watched by the distinct-TID-growth stop condition. Independent re-review
  of this revision remains required before promotion.
- 2026-08-26 codex re-review (resumed session `01a04127`, 2.8M tokens):
  5 of 8 dispositions verified FIXED; 3 PARTIAL residuals and 3 new findings
  (2 P1, 1 P2) all accepted and incorporated in this revision — enumerated
  producer-class bound with retention-floor benchmark depth, crash-residue
  cohort stop condition, corrected executable review prompt, load-bearing
  `terminal` comparator requirement with regressions, hard cross-plan
  dependency gate, and the SQLite benchmark in the coverage map. Verdict on
  the pre-fix revision: BLOCKED; a further independent pass on this state is
  still required before promotion.
- 2026-08-26 codex third pass (resumed session `01a04127`, 4.0M tokens):
  five of six residuals verified FIXED; the producer-maxima P1 and two
  consistency P2s accepted and incorporated — per-class maxima are now a
  reviewed call-site-derived Task 1 artifact gating `analytical_rate` and `D`
  rather than asserted constants. Remaining pre-promotion work is the
  admission correction dependency, the producer table, and the capacity
  benchmarks themselves; the next independent pass should run fresh against
  that evidence rather than re-verifying wording.
- 2026-08-27 implementation slice (Tasks 2-4) on top of `0bf1ddd`: spec delta
  promoted to 01/05/07 with backlinks; failure-first writer tests recorded
  red against the reader-writer and green after the change; cleanup exclusion
  ordering fixed in `tid_mapping_candidates`; streaming two-pass
  reachability scan (`tid_mapping_streaming_candidates`) replaced the bounded
  head window in `run_task_monitor_cleanup` with red head-starvation and
  excluded-TID regressions now green; harness mapping discovery reduced over
  the full generator with a beyond-2,048-decoys regression. Two legacy
  window-semantics tests updated to the reachability contract (an all-young
  queue completes to tail with no stop reason; a malformed sibling beyond the
  old window is now correctly retired while the valid live row survives).
- 2026-08-27 edge-triggered correction (owner first-principles review): the
  last-written guard and repurposed comparator were removed before commit;
  the writer is a bare append returning success, call sites own edge
  detection, and the terminal transition latches a once-published flag only
  on successful write. [OBS.6a] rewritten to the edge-triggered contract
  (writer-side equivalence oracles forbidden). Producer-class capacity
  analysis discarded: call sites fire on changes under either model, and no
  work-item-rate estimate or derived throughput threshold is a product gate.
- 2026-08-28 implementation review found and fixed two destructive-scan
  correctness defects. A missing pass-one generator previously produced empty
  newest-row evidence and could authorize deletion of the newest live mapping;
  both passes now use strict reads and cleanup fails without applying
  candidates. Candidate-cap exit previously left the pass-two broker iterator
  open; the caller now closes it explicitly. Both defects have deterministic
  firing regressions with no sleeps or timing thresholds.
- 2026-08-28 local verification from the corrected worktree: focused mapping,
  cleanup, publication, harness, and plan-metadata tests passed; repository-wide
  Ruff format/check, mypy, DOM-15, and spec metadata/hygiene passed; the full
  SQLite suite passed with only the expected PostgreSQL/platform skips.
- 2026-08-28 PostgreSQL verification: `bin/pytest-pg` ran the affected mapping
  policy, cleanup, publication, and harness suites through the real PostgreSQL
  backend; 82 tests passed. The emitted duration report was retained as
  diagnostic output only and did not create timing gates.

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
  concurrency is mitigated by the implemented Manager admission control
  (`docs/plans/2026-08-25-manager-admission-control-plan.md`); this plan must not
  alter admission counting semantics or add the deferred admission observer
  cache.
- Manual production deletion or emergency compaction of existing mapping rows.
- A registered-runtime-probe destruction gate for newest mapping rows.
  Destruction stays payload-only ([OBS.13.7] plus the admission plan's
  `terminal: true` amendment); probing external runtimes before deleting their
  evidence is a separate measured design if crash residue ever matters.

## Fresh-Eyes Review

Current verdict: PASS after corrections. Review focused on observable product
correctness and rejected the earlier capacity formulas, fixed throughput
minimums, timing thresholds, and repeated sampling protocols as temporary
planning experiments.

Accepted and fixed findings:

1. Exclusion used to protect every row for the TaskMonitor's own TID. Cleanup
   now classifies newest versus superseded first, so only the newest excluded
   row is protected.
2. Protected rows in a bounded FIFO head could hide eligible superseded tail
   rows forever. Candidate-bounded pass two now streams to the first full
   candidate batch or queue tail.
3. The harness read only the first 2,048 mapping rows. It now reduces the full
   generator by greatest message ID.
4. A best-effort pass-one read could silently provide no newest-row evidence,
   allowing destructive cleanup to delete the actual newest row. Both
   destructive passes now read strictly and abort that queue on scan failure.
5. Candidate-cap early exit did not explicitly close the pass-two broker
   iterator. The caller now owns and closes it on every exit path.

JSON construction and serialization remain outside the best-effort broker
append catch so programming and schema defects stay visible. No SimpleBroker
change was required.
